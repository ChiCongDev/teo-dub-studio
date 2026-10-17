"""Remove videos from the local schedule view, retaining publication evidence.

This module never deletes media or changes a Facebook post. Only an untouched
draft may release its source slot. Every other removed row still reserves it.
"""
from contextlib import ExitStack
from copy import deepcopy
import json
import re

from teodub.page_activity import now
from teodub.pipeline import _job_lock


_IN_FLIGHT = {'preparing', 'submitting', 'processing', 'verifying'}
_EVIDENCE = ('browser_review', 'browser_receipt', 'browser_tracking', 'remote_assets',
             'external_id', 'external_url', 'manual_url', 'published_at')


def _snapshot(store, conn, row):
    saved = conn.execute('SELECT * FROM activity.posts WHERE id=? AND account_id=? AND page_key=?',
        (row['post_id'], row['account_id'], row['page_key'])).fetchone()
    if saved is None:
        raise ValueError('Không còn hồ sơ bài đăng của video; chưa xóa.')
    post = dict(saved)
    for key in ('media', 'remote_assets', 'metrics', 'browser_review', 'browser_receipt', 'browser_tracking'):
        post[key] = json.loads(post[key])
    events = [dict(event) for event in conn.execute(
        'SELECT at,state,message FROM activity.events WHERE post_id=? ORDER BY id', (post['id'],))]
    return deepcopy(dict(id=row['id'], schedule=row, post=post, events=events))


def _scope(row, account_id, page_key):
    if (row['account_id'], row['page_key']) != (account_id, page_key):
        raise ValueError('Video không thuộc tài khoản/Page đang mở.')
    if row['source'].get('kind') == 'text':
        raise ValueError('Nút Xóa video chỉ áp dụng cho lịch video.')


def preview(store, identifiers, account_id, page_key):
    results = []
    # Initialize legacy activity schemas before opening the read transaction.
    with store.activity.database():
        pass
    with store.database() as conn:
        conn.execute('ATTACH DATABASE ? AS activity', (str(store.activity.path),))
        conn.execute('BEGIN')
        for ident in dict.fromkeys(identifiers):
            saved = conn.execute('SELECT * FROM schedules WHERE id=?', (ident,)).fetchone()
            if saved is None:
                raise ValueError('Không còn lịch video đã chọn; làm mới danh sách.')
            row = store.decode(saved)
            _scope(row, account_id, page_key)
            results.append(_snapshot(store, conn, row))
    return results


def _untouched(value):
    row, post, events = value['schedule'], value['post'], value['events']
    return bool(not any(row[key] for key in ('attempted_at', 'request_id', 'phase', 'finished_at'))
        and row['state'] in {'scheduled', 'armed', 'paused', 'needs_attention'}
        and post['state'] in {'draft', 'failed'} and post['attempt'] == 0
        and not any(post.get(key) for key in _EVIDENCE)
        and not any(event['state'] not in {'draft', 'failed'} for event in events)
        and row['source_key'] == row['source'].get('key'))


def remove_one(store, expected, account_id, page_key, *, guard_owned=False):
    if not isinstance(expected, dict):
        raise ValueError('Chọn và xác nhận đúng video trước khi xóa.')
    ident = expected.get('id', '')
    old_post = expected.get('schedule', {}).get('post_id', '')
    if not all(re.fullmatch(r'[0-9a-f]{32}', str(value)) for value in (ident, old_post)):
        raise ValueError('Mã lịch/video không hợp lệ; chưa xóa.')
    with ExitStack() as locks:
        paths = ([] if guard_owned else [store.path.parent/'schedule-locks'/ident])
        paths.append(store.path.parent/'post-locks'/old_post)
        for path in paths:
            path.mkdir(parents=True, exist_ok=True)
            locks.enter_context(_job_lock(path))
        with store.database() as conn:
            conn.execute('ATTACH DATABASE ? AS activity', (str(store.activity.path),))
            # The attached activity database is read-only here, but locked in
            # the same transaction so a concurrent draft cannot change evidence.
            conn.execute('BEGIN IMMEDIATE')
            saved = conn.execute('SELECT * FROM schedules WHERE id=?', (ident,)).fetchone()
            if saved is None:
                raise ValueError('Không còn lịch video đã chọn.')
            row = store.decode(saved)
            _scope(row, account_id, page_key)
            if row['state'] == 'removed':
                raise ValueError('Video đã được xóa khỏi danh sách.')
            value = _snapshot(store, conn, row)
            if value != expected:
                raise ValueError('Video hoặc lượt đăng đã thay đổi sau khi xác nhận; làm mới rồi chọn lại.')
            if row['state'] in _IN_FLIGHT or value['post']['state'] in _IN_FLIGHT:
                raise ValueError('Video đang được xử lý; chờ lượt thao tác kết thúc trước khi xóa khỏi danh sách.')
            released = _untouched(value)
            conn.execute('INSERT INTO schedule_removals(schedule_id,at,previous,source_released) VALUES (?,?,?,?)',
                (ident, now(), json.dumps(value, ensure_ascii=False), int(released)))
            conn.execute("UPDATE schedules SET state='removed',source_key=?,message=? WHERE id=?",
                ('removed:'+ident if released else row['source_key'],
                 'Đã xóa video khỏi danh sách Tèo; giữ tệp, caption và lịch sử. Bài trên Facebook được giữ.', ident))
    return ident


def remove_many(store, previews, account_id, page_key):
    result = dict(removed=[], skipped={})
    seen = set()
    for expected in previews:
        ident = expected.get('id', '') if isinstance(expected, dict) else ''
        if ident in seen:
            continue
        seen.add(ident)
        try:
            result['removed'].append(remove_one(store, expected, account_id, page_key))
        except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
            result['skipped'][ident] = str(exc)
    return result


def history(store, ident):
    with store.database() as conn:
        return [dict(row, previous=json.loads(row['previous']), source_released=bool(row['source_released']))
                for row in map(dict, conn.execute('SELECT * FROM schedule_removals WHERE schedule_id=?', (ident,)))]
