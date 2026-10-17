"""Reconcile an already submitted browser video from a read-only DOM observation.

This module never uploads, clicks Publish, creates a draft, or arms a schedule.
The complete old attempt is captured before observation and rechecked under both
worker locks and a cross-database transaction before recording its result.
"""
from contextlib import ExitStack, contextmanager
from copy import deepcopy
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import time

from teodub.common import hash_file
from teodub.page_activity import now
from teodub.pipeline import _job_lock


_STOPPED = {'paused', 'needs_attention', 'published', 'removed'}
_JSON_FIELDS = ('media', 'remote_assets', 'metrics', 'browser_review', 'browser_receipt', 'browser_tracking')
_MESSAGE = 'Đã xác minh bài video thực tế trên Facebook; không đăng lại.'


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _time(value):
    try:
        result = datetime.fromisoformat(value)
        if result.tzinfo is None or result.utcoffset() is None:
            raise ValueError
        return result.timestamp()
    except (ValueError, TypeError, OverflowError):
        raise ValueError('Thiếu thời gian có múi giờ của lượt đã gửi; chưa xác minh kết quả.') from None


def _positive(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def _post(saved):
    result = dict(saved)
    for key in _JSON_FIELDS:
        result[key] = json.loads(result[key])
    return result


@contextmanager
def _reading(store):
    conn = sqlite3.connect(store.path.resolve().as_uri()+'?mode=ro', uri=True, timeout=3)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute('ATTACH DATABASE ? AS activity', (store.activity.path.resolve().as_uri()+'?mode=ro',))
        conn.execute('BEGIN')
        yield conn
    finally:
        conn.close()


def _capture(store, conn, account_id, page_key, ident):
    saved = conn.execute('SELECT * FROM schedules WHERE id=? AND account_id=? AND page_key=?',
                         (ident, account_id, page_key)).fetchone()
    if saved is None:
        raise ValueError('Lịch không thuộc tài khoản/Page đang kiểm tra.')
    row = store.decode(saved)
    saved_post = conn.execute('SELECT * FROM activity.posts WHERE id=? AND account_id=? AND page_key=?',
                              (row['post_id'], account_id, page_key)).fetchone()
    if saved_post is None:
        raise ValueError('Không còn lượt bài viết của lịch đã chọn.')
    post = _post(saved_post)
    events = [dict(event) for event in conn.execute(
        'SELECT id,at,state,message FROM activity.events WHERE post_id=? ORDER BY id', (post['id'],))]
    return row, post, events


def _eligible(row, post, events):
    from teodub.facebook_web_post import _same_body
    review = post['browser_review']
    if (row['state'] not in _STOPPED or row['phase'] != 'publish' or not row['attempted_at']
            or post['state'] not in {'unknown', 'published'} or post['archived']
            or post['delivery_mode'] != 'browser' or post['browser_finish_mode'] != 'tool'
            or post['kind'] != 'video' or len(post['media']) != 1 or not post['body'].strip()
            or row['source'].get('kind') == 'text' or not isinstance(review, dict)):
        raise ValueError('Chỉ kiểm tra lượt video đã thực sự gửi bằng Chrome và đã dừng; không gán bài cũ cho bản nháp mới.')
    if (row['post_id'] != post['id'] or row['account_id'] != post['account_id']
            or row['page_key'] != post['page_key'] or row['body'] != post['body']
            or any(post.get(key) != value for key, value in row['snapshot'].items())):
        raise ValueError('Bản nháp hoặc nội dung lịch không còn khớp lượt đã gửi.')
    for key in ('account_id', 'page_key', 'post_id', 'target_id', 'target_url', 'target_name', 'browser_finish_mode'):
        expected = post['id'] if key == 'post_id' else post[key]
        if review.get(key) != expected:
            raise ValueError('Bản duyệt Chrome không khớp tài khoản/Page hoặc lượt bài viết.')
    if not _same_body(review.get('body', ''), post['body']):
        raise ValueError('Caption đầy đủ không khớp bản đã duyệt trong Chrome.')
    frozen = review.get('media')
    digest = row['source'].get('sha256', '')
    media = post['media'][0]
    if (not re.fullmatch(r'[0-9a-f]{64}', digest) or not isinstance(frozen, list) or len(frozen) != 1
            or frozen[0] != dict(media, sha256=digest)
            or media != {key: row['source'].get(key) for key in ('path', 'size', 'mtime_ns')}):
        raise ValueError('Thiếu bản duyệt gắn mã kiểm tra đúng tệp video đã gửi.')
    composer = review.get('composer', {})
    videos = composer.get('video_preview', []) if isinstance(composer, dict) else []
    if (not isinstance(videos, list) or len(videos) != 1 or not isinstance(videos[0], dict)
            or not all(_positive(videos[0].get(key)) for key in ('duration', 'width', 'height'))):
        raise ValueError('Bản duyệt cũ thiếu thời lượng/kích thước video; chưa đủ bằng chứng đối soát.')
    submitted = [event for event in events if event['state'] == 'submitting']
    if not submitted or not any(event['state'] == 'processing' for event in events):
        raise ValueError('Lượt này chưa có bằng chứng đã bấm gửi; không nhận bài Facebook vào bản nháp mới.')
    _time(row['attempted_at'])
    prepared_at = _time(review.get('prepared_at'))
    for event in submitted:
        if _time(event['at']) < prepared_at:
            raise ValueError('Thời gian gửi không khớp bản duyệt của lượt đã chọn.')
    latest = submitted[-1]
    if not any(event['state'] == 'processing' and event['id'] > latest['id']
               and _time(event['at']) >= _time(latest['at']) for event in events):
        raise ValueError('Chưa có bằng chứng xử lý sau lần gửi cuối; chưa xác minh kết quả.')
    return latest, {key: videos[0][key] for key in ('duration', 'width', 'height')}


def can_inspect(store, row):
    """Cheap UI eligibility check: no hashing, probing, or all-history scan."""
    try:
        with _reading(store) as conn:
            fresh, post, events = _capture(store, conn, row['account_id'], row['page_key'], row['id'])
            _eligible(fresh, post, events)
            from teodub.page_schedule_recovery import _target
            _target(store, fresh, post)
        return True
    except (OSError, sqlite3.Error, ValueError, KeyError, TypeError, AttributeError):
        return False


def _attempts(conn, post, digest):
    from teodub.facebook_web_post import _same_body
    candidates = []
    for saved in conn.execute("SELECT * FROM activity.posts WHERE target_id=? AND kind='video' AND delivery_mode='browser'",
                              (post['target_id'],)):
        other = _post(saved)
        if not _same_body(other['body'], post['body']):
            continue
        media = other['browser_review'].get('media', [])
        known = media[0].get('sha256') if len(media) == 1 and isinstance(media[0], dict) else None
        # Incomplete old evidence cannot establish that a same-caption attempt
        # was a different video. Include it conservatively in ambiguity checks.
        if known and known != digest:
            continue
        for event in conn.execute("SELECT id,at FROM activity.events WHERE post_id=? AND state='submitting' ORDER BY id",
                                  (other['id'],)):
            _time(event['at'])
            candidates.append(dict(post_id=other['id'], submitted_at=event['at'], event_id=event['id']))
    return sorted(candidates, key=lambda value: (_time(value['submitted_at']), value['event_id'], value['post_id']))


def _preview(store, conn, account_id, page_key, ident, *, check_cancel=None, verify_source=True):
    from teodub.page_schedule_recovery import _target
    row, post, events = _capture(store, conn, account_id, page_key, ident)
    submitted, expected = _eligible(row, post, events)
    target = _target(store, row, post)
    digest = row['source']['sha256']
    if verify_source:
        store.activity.verify_media(post)
        digest = hash_file(post['media'][0]['path'])
    if check_cancel:
        check_cancel()
    if digest != row['source']['sha256']:
        raise ValueError('Tệp video đã thay đổi sau lượt gửi; chưa cập nhật kết quả.')
    if verify_source:
        store.activity.verify_media(post)
    candidates = _attempts(conn, post, digest)
    selected = dict(post_id=post['id'], submitted_at=submitted['at'], event_id=submitted['id'])
    if selected not in candidates:
        raise ValueError('Không xác định được đúng lượt gửi của video.')
    subsequent = [_time(value['submitted_at']) for value in candidates if _time(value['submitted_at']) > _time(submitted['at'])]
    next_at = min(subsequent) if subsequent else None
    result = deepcopy(dict(schedule=row, post=post, events=events, target=target,
        submitted_at=submitted['at'], media_sha256=digest, expected_duration=expected['duration'],
        expected_video=expected, attempt_candidates=candidates,
        association=dict(submit_at=submitted['at'], not_before=datetime.fromtimestamp(
            _time(submitted['at'])-2, datetime.fromisoformat(submitted['at']).tzinfo).isoformat(),
            next_submit_at=next((value['submitted_at'] for value in candidates
                                if next_at is not None and _time(value['submitted_at']) == next_at), ''),
            matching_attempt_ids=[value['post_id'] for value in candidates])))
    result['preview_key'] = hashlib.sha256(_json(result).encode('utf-8')).hexdigest()
    return result


def preview(store, account_id, page_key, schedule_id):
    """Freeze the complete old attempt before opening its public result URL."""
    try:
        with _reading(store) as conn:
            return _preview(store, conn, account_id, page_key, schedule_id)
    except sqlite3.Error as exc:
        raise ValueError('Chưa đọc được bằng chứng lượt đã gửi; dữ liệu được giữ. '+str(exc)) from None
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError('Bằng chứng cũ chưa đầy đủ để xác minh đúng lượt đã gửi.') from exc


def validate_current(store, expected_preview):
    """Cheap preflight on the UI thread; the commit still hashes the actual file."""
    try:
        row = expected_preview['schedule']
        with _reading(store) as conn:
            current = _preview(store, conn, row['account_id'], row['page_key'], row['id'], verify_source=False)
        if current != expected_preview:
            raise ValueError('Lượt hoặc Page đã thay đổi sau màn hình xác nhận; mở lại để kiểm tra.')
        return True
    except (sqlite3.Error, KeyError, TypeError, AttributeError) as exc:
        raise ValueError('Chưa kiểm tra được bản xem trước lượt đã gửi.') from exc


def association_matches(capture, published_at):
    """Require visible publication time to identify exactly the selected attempt."""
    try:
        observed = _time(published_at)
        selected = capture['post']['id'], capture['submitted_at']
        candidates = capture['attempt_candidates']
        matches = []
        for candidate in candidates:
            start = _time(candidate['submitted_at'])-2
            later = [_time(other['submitted_at'])-2 for other in candidates
                     if _time(other['submitted_at']) > _time(candidate['submitted_at'])]
            if observed >= start and (not later or observed < min(later)):
                matches.append((candidate['post_id'], candidate['submitted_at']))
        return matches == [selected]
    except (ValueError, KeyError, TypeError):
        return False


def _receipt(capture, evidence):
    from teodub.facebook_web_post import _canonical_inspection_url, _page_id, _same_body, _target_url
    if not isinstance(evidence, dict):
        raise ValueError('Chưa có bằng chứng quan sát bài video thực tế.')
    if (evidence.get('source') != 'visible_dom' or evidence.get('preview_key') != capture['preview_key']
            or evidence.get('target_id') != capture['target']['page_id']
            or any(evidence.get(key) is not True for key in ('visible_video', 'video_verified', 'attempt_confirmed'))
            or not _same_body(evidence.get('body', ''), capture['post']['body'])):
        raise ValueError('Bằng chứng chưa khớp đúng Page, toàn bộ caption, video và lượt đã gửi.')
    url = _canonical_inspection_url(evidence.get('url', ''))
    if not url or _page_id(_target_url(evidence.get('owner_url', ''))) != capture['target']['page_id']:
        raise ValueError('Cần permalink bài video thực tế và chủ bài là đúng Page đã lưu.')
    video = capture['expected_video']
    if (not all(_positive(evidence.get(key)) for key in ('observed_duration', 'observed_width', 'observed_height'))
            or abs(evidence['observed_duration']-video['duration']) > 2
            or abs((evidence['observed_width']/evidence['observed_height'])/(video['width']/video['height'])-1) > .03):
        raise ValueError('Thời lượng hoặc tỷ lệ video thực tế không khớp bản đã duyệt.')
    checked = _time(evidence.get('checked_at'))
    if checked < _time(capture['submitted_at']) or checked > time.time()+5 or time.time()-checked > 120:
        raise ValueError('Quan sát kết quả đã cũ hoặc thời gian không khớp lượt gửi; kiểm tra lại.')
    published = evidence.get('published_at', '')
    if published:
        if (evidence.get('timestamp_source') != 'visible_dom'
                or evidence.get('association_mode') != 'visible_time'
                or _time(published) > checked+5 or not association_matches(capture, published)):
            raise ValueError('Thời gian đăng thực tế không xác định duy nhất đúng lượt đã chọn.')
    elif (evidence.get('association_mode') != 'unique_attempt_user_link'
            or len(capture['attempt_candidates']) != 1 or evidence.get('confirmed_attempt') is not True
            or _canonical_inspection_url(evidence.get('explicit_result_url', '')) != url):
        raise ValueError('Thiếu thời gian đăng: chỉ đối soát lượt gửi duy nhất sau xác nhận rõ link của lượt này.')
    result = deepcopy(evidence)
    result['url'] = url
    return result


def _duplicate_url(conn, post_id, url):
    from teodub.facebook_web_post import _canonical_inspection_url
    for saved in conn.execute('SELECT id,external_url,manual_url,browser_receipt FROM activity.posts WHERE id<>?', (post_id,)):
        receipt = json.loads(saved['browser_receipt'])
        if any(_canonical_inspection_url(value) == url for value in
               (saved['external_url'], saved['manual_url'], receipt.get('url', ''))):
            raise ValueError('Link Facebook này đã gắn với lượt khác; không nhận thành hai bài đã đăng.')


def commit_verified(store, expected_preview, evidence, *, check_cancel=None):
    """Record one verified old result atomically, without changing scheduling intent."""
    if not isinstance(expected_preview, dict):
        raise ValueError('Cần xem trước đúng lượt đã gửi trước khi cập nhật kết quả.')
    try:
        row = expected_preview['schedule']
        ident, post_id, account_id, page_key = row['id'], expected_preview['post']['id'], row['account_id'], row['page_key']
    except (KeyError, TypeError):
        raise ValueError('Bản xem trước lượt đã gửi không hợp lệ.') from None
    if not all(re.fullmatch(r'[0-9a-f]{32}', str(value)) for value in (ident, post_id, account_id, page_key)):
        raise ValueError('Mã lịch/bài/tài khoản/Page không hợp lệ.')
    try:
        frozen = {key: value for key, value in expected_preview.items() if key != 'preview_key'}
        if hashlib.sha256(_json(frozen).encode('utf-8')).hexdigest() != expected_preview.get('preview_key'):
            raise ValueError('Bản xem trước đã thay đổi; mở lại đúng lượt đã gửi.')
        receipt = _receipt(expected_preview, evidence)
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError('Bản xem trước hoặc bằng chứng quan sát chưa đầy đủ.') from exc
    with ExitStack() as locks:
        for path in (store.path.parent/'schedule-locks'/ident, store.path.parent/'post-locks'/post_id):
            path.mkdir(parents=True, exist_ok=True)
            locks.enter_context(_job_lock(path))
        with store.database() as conn:
            conn.execute('ATTACH DATABASE ? AS activity', (str(store.activity.path),))
            conn.execute('BEGIN IMMEDIATE')
            if any(conn.execute(f'PRAGMA {name}.journal_mode').fetchone()[0].lower() != 'delete'
                   for name in ('main', 'activity')):
                raise ValueError('Chế độ kho dữ liệu chưa bảo đảm giao dịch hai tệp; chưa cập nhật kết quả.')
            journal = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='browser_reconciliations'").fetchone()
            recorded = conn.execute('SELECT * FROM browser_reconciliations WHERE schedule_id=?', (ident,)).fetchone() if journal else None
            if recorded:
                current = _preview(store, conn, account_id, page_key, ident, check_cancel=check_cancel)
                current_row, current_post = current['schedule'], current['post']
                if (json.loads(recorded['previous']) != expected_preview
                        or recorded['preview_key'] != expected_preview['preview_key'] or json.loads(recorded['receipt']) != receipt
                        or current_post['id'] != post_id or current_post['state'] != 'published'
                        or current_post['browser_receipt'] != receipt or current_post['external_url'] != receipt['url']
                        or current_row['state'] != ('removed' if row['state'] == 'removed' else 'published')
                        or current_row['message'] != _MESSAGE
                        or current_row['finished_at'] != (row['finished_at'] or recorded['at'])
                        or current_post['message'] != _MESSAGE or current_post['updated_at'] != recorded['at']
                        or current_post['published_at'] != receipt.get('published_at', '')
                        or current['target'] != expected_preview['target']
                        or current['attempt_candidates'] != expected_preview['attempt_candidates']
                        or current['events'][:-1] != expected_preview['events']
                        or current['events'][-1]['state'] != 'published'
                        or current['events'][-1]['at'] != recorded['at']
                        or current['events'][-1]['message'] != _MESSAGE+' '+receipt['url']
                        or any(current_post[key] != value for key, value in expected_preview['post'].items()
                               if key not in {'state', 'message', 'browser_receipt', 'external_url', 'published_at', 'updated_at'})
                        or any(current_row[key] != value for key, value in expected_preview['schedule'].items()
                               if key not in {'state', 'message', 'finished_at'})):
                    raise ValueError('Lượt này đã có kết quả đối soát khác; không thay bằng chứng đã lưu.')
                _duplicate_url(conn, post_id, receipt['url'])
                if check_cancel:
                    check_cancel()
                return dict(current_post, schedule_id=ident)
            fresh = _preview(store, conn, account_id, page_key, ident, check_cancel=check_cancel)
            if fresh != expected_preview:
                raise ValueError('Lịch, bài viết, Page hoặc lịch sử gửi đã thay đổi; xem trước lại trước khi đối soát.')
            receipt = _receipt(fresh, evidence)
            _duplicate_url(conn, post_id, receipt['url'])
            post = fresh['post']
            from teodub.facebook_web_post import _canonical_inspection_url
            if (post['state'] == 'published' and post['browser_receipt'].get('source') == 'visible_dom'
                    and _canonical_inspection_url(post['external_url']) == receipt['url']
                    and _canonical_inspection_url(post['browser_receipt'].get('url', '')) == receipt['url']):
                # Reconfirmation preserves the original durable receipt and its
                # publication timestamp rather than replacing them with a scan.
                from teodub.page_schedule_recovery import _target
                if _target(store, fresh['schedule'], post) != fresh['target']:
                    raise ValueError('Page đã thay đổi trong lúc đối soát; chưa xác nhận kết quả.')
                if check_cancel:
                    check_cancel()
                return dict(post, schedule_id=ident)
            if ((post['external_url'] and post['external_url'] != receipt['url'])
                    or post['browser_receipt'] and post['browser_receipt'] != receipt):
                raise ValueError('Lượt đã có permalink hoặc bằng chứng khác; chưa thay kết quả cũ.')
            timestamp = now()
            # Verification time belongs in checked_at/events. It is not the
            # actual publication time when Facebook does not expose that date.
            published_at = receipt.get('published_at', '')
            conn.execute('''CREATE TABLE IF NOT EXISTS browser_reconciliations (
                schedule_id TEXT PRIMARY KEY,post_id TEXT NOT NULL,at TEXT NOT NULL,
                preview_key TEXT NOT NULL,previous TEXT NOT NULL,receipt TEXT NOT NULL)''')
            conn.execute('''INSERT INTO browser_reconciliations VALUES (?,?,?,?,?,?)''',
                         (ident, post_id, timestamp, fresh['preview_key'], _json(fresh), _json(receipt)))
            conn.execute('''UPDATE activity.posts SET state='published',message=?,browser_receipt=?,
                external_url=?,published_at=?,updated_at=? WHERE id=?''',
                (_MESSAGE, _json(receipt), receipt['url'], published_at, timestamp, post_id))
            conn.execute('INSERT INTO activity.events(post_id,at,state,message) VALUES (?,?,?,?)',
                         (post_id, timestamp, 'published', _MESSAGE+' '+receipt['url']))
            conn.execute("""UPDATE schedules SET state=CASE WHEN state='removed' THEN 'removed' ELSE 'published' END,
                message=?,finished_at=CASE WHEN finished_at='' THEN ? ELSE finished_at END WHERE id=?""",
                (_MESSAGE, timestamp, ident))
            from teodub.page_schedule_recovery import _target
            if _target(store, fresh['schedule'], fresh['post']) != fresh['target']:
                raise ValueError('Page đã thay đổi trong lúc đối soát; chưa lưu kết quả.')
            if check_cancel:
                check_cancel()
            store.activity.verify_media(fresh['post'])
            saved = conn.execute('SELECT * FROM activity.posts WHERE id=?', (post_id,)).fetchone()
            result = dict(_post(saved), schedule_id=ident)
    return result
