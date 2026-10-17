"""Explicit recovery of attempted video preparations; never arms or publishes.

A fresh draft replaces the schedule's active binding. The original draft and
events remain as archived evidence, and the unique source slot stays occupied.
"""
from contextlib import ExitStack
from copy import deepcopy
import json
import math
from pathlib import Path
import re
import time
import uuid

from teodub.common import hash_file
from teodub.page_activity import now
from teodub.pipeline import _job_lock


_EVIDENCE = ('browser_review', 'browser_receipt', 'browser_tracking', 'remote_assets',
             'external_id', 'external_url', 'manual_url', 'published_at')
_SUBMITTED = {'review', 'submitting', 'processing', 'published'}


def _target(store, row, post):
    from teodub.facebook_web_post import _page_id, _target_url
    page = store.activity.page(row['account_id'], row['page_key'])
    target = {key: page.get(key, '') for key in ('name', 'url', 'page_id')}
    if (not target['name'] or not re.fullmatch(r'[0-9]+', target['page_id'])
            or target['page_id'] != post['target_id']):
        raise ValueError('Chỉ khôi phục cho cùng Page ID. Không chuyển lịch cũ sang Page khác.')
    for url in (target['url'], post['target_url']):
        linked_id = _page_id(_target_url(url))
        if linked_id and linked_id != target['page_id']:
            raise ValueError('Link và Page ID không khớp; chưa khôi phục lịch.')
    return target


def _eligible(row, post, events):
    if (row['state'] not in {'paused', 'needs_attention'} or not row['attempted_at'] or row['finished_at']
            or row['phase'] != 'prepare' or row['source'].get('kind') == 'text'
            or post['state'] not in {'failed', 'unknown'} or post['archived']
            or post['kind'] != 'video' or len(post['media']) != 1
            or post['delivery_mode'] != 'browser' or post['browser_finish_mode'] != 'tool'
            or any(post.get(key) for key in _EVIDENCE)
            or any(event['state'] in _SUBMITTED for event in events)):
        raise ValueError('Chỉ khôi phục lượt chuẩn bị video đã dừng. Bài đã duyệt/gửi hoặc có kết quả đăng phải kiểm tra riêng.')
    if (row['post_id'] != post['id'] or row['body'] != post['body']
            or row['source_key'] != row['source'].get('key')
            or any(post.get(key) != value for key, value in row['snapshot'].items())):
        raise ValueError('Bản nháp hoặc nội dung lịch đã thay đổi; chưa khôi phục.')


def _source(store, row, post):
    from teodub.page_schedule import campaign_video_source
    source = row['source']
    if campaign_video_source(store.data_dir, source['campaign'], source['number']) != source:
        raise ValueError('Video thành phẩm đã thay đổi; chưa khôi phục lịch.')
    store.activity.verify_media(post)
    media = post['media'][0]
    if (media != {key: source[key] for key in ('path', 'size', 'mtime_ns')}
            or hash_file(source['path']) != source['sha256']):
        raise ValueError('Tệp video không còn khớp mã kiểm tra của lịch cũ; chưa khôi phục.')


def _preview(store, row, post, events, *, verify_source):
    _eligible(row, post, events)
    target = _target(store, row, post)
    if verify_source:
        _source(store, row, post)
    return deepcopy(dict(schedule=row, post=post, events=events, target=target,
                         classification=post['state'] + '_preparation'))


def can_recover(store, row):
    try:
        post = store.activity.post(row['account_id'], row['page_key'], row['post_id'])
        events = store.activity.events(row['account_id'], row['page_key'], row['post_id'])
        _preview(store, row, post, events, verify_source=False)
        return True
    except (OSError, ValueError, KeyError, TypeError):
        return False


def preview_recovery(store, account_id, page_key, ident):
    row = store.get(ident)
    if (row['account_id'], row['page_key']) != (account_id, page_key):
        raise ValueError('Lịch không thuộc tài khoản/Page đang mở.')
    post = store.activity.post(account_id, page_key, row['post_id'])
    events = store.activity.events(account_id, page_key, row['post_id'])
    return _preview(store, row, post, events, verify_source=True)


def recovery_history(store, ident):
    with store.database() as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='schedule_recoveries'").fetchone():
            return []
        return [dict(row, previous=json.loads(row['previous']), target=json.loads(row['target']),
                     checked_unpublished=bool(row['checked_unpublished']), closed_composer=bool(row['closed_composer']))
                for row in map(dict, conn.execute('SELECT * FROM schedule_recoveries WHERE schedule_id=? ORDER BY id', (ident,)))]


def recover(store, account_id, page_key, ident, due_at, *, expected_preview,
            checked_unpublished, closed_composer):
    if checked_unpublished is not True or closed_composer is not True:
        raise ValueError('Xác nhận đã kiểm tra Facebook chưa có bài và đã đóng hộp soạn cũ trước khi khôi phục.')
    if isinstance(due_at, bool):
        raise ValueError('Chọn giờ đăng mới trong tương lai.')
    due_at = float(due_at)
    if not math.isfinite(due_at) or not time.time()+5 < due_at < time.time()+366*86400:
        raise ValueError('Chọn giờ đăng mới trong tương lai, tối đa một năm tới.')
    if not isinstance(expected_preview, dict):
        raise ValueError('Cần xem trước đúng lịch và Page trước khi khôi phục.')
    old_id = expected_preview.get('post', {}).get('id', '')
    if not re.fullmatch(r'[0-9a-f]{32}', str(ident)) or not re.fullmatch(r'[0-9a-f]{32}', str(old_id)):
        raise ValueError('Mã lịch/bản nháp không hợp lệ; chưa khôi phục.')
    directory = store.path.parent
    with ExitStack() as locks:
        for path in (directory/'schedule-locks'/ident,
                     directory/'post-locks'/old_id, directory/'schedule-creation'):
            path.mkdir(parents=True, exist_ok=True)
            locks.enter_context(_job_lock(path))
        # Both databases use rollback journals by default. Fail closed for WAL
        # rather than claim cross-file crash atomicity that SQLite cannot give.
        with store.database() as conn:
            conn.execute('ATTACH DATABASE ? AS activity', (str(store.activity.path),))
            conn.execute('BEGIN IMMEDIATE')
            if any(conn.execute(f'PRAGMA {name}.journal_mode').fetchone()[0].lower() != 'delete'
                   for name in ('main', 'activity')):
                raise ValueError('Kho dữ liệu đang dùng chế độ khác; chưa khôi phục lịch để giữ an toàn dữ liệu.')
            saved = conn.execute('SELECT * FROM schedules WHERE id=?', (ident,)).fetchone()
            if saved is None:
                raise ValueError('Không còn lịch đăng này.')
            row = store.decode(saved)
            if (row['account_id'], row['page_key']) != (account_id, page_key):
                raise ValueError('Lịch không thuộc tài khoản/Page đang mở.')
            saved_post = conn.execute('SELECT * FROM activity.posts WHERE id=? AND account_id=? AND page_key=?',
                                      (row['post_id'], account_id, page_key)).fetchone()
            if saved_post is None:
                raise ValueError('Không còn bản nháp của lịch cũ.')
            post = dict(saved_post)
            for key in ('media', 'remote_assets', 'metrics', 'browser_review', 'browser_receipt', 'browser_tracking'):
                post[key] = json.loads(post[key])
            events = [dict(event) for event in conn.execute(
                'SELECT at,state,message FROM activity.events WHERE post_id=? ORDER BY id', (post['id'],))]
            preview = _preview(store, row, post, events, verify_source=True)
            if preview != expected_preview:
                raise ValueError('Lịch, bản nháp hoặc Page đã thay đổi sau khi xem trước; mở lại màn hình khôi phục.')
            if row['post_id'] != old_id:
                raise ValueError('Lịch đã đổi bản nháp; chưa khôi phục.')
            target, timestamp, new_id = preview['target'], now(), uuid.uuid4().hex
            conn.execute('''CREATE TABLE IF NOT EXISTS schedule_recoveries (
                id INTEGER PRIMARY KEY AUTOINCREMENT, schedule_id TEXT NOT NULL, at TEXT NOT NULL,
                previous TEXT NOT NULL, new_post_id TEXT NOT NULL, target TEXT NOT NULL,
                new_due_at REAL NOT NULL, checked_unpublished INTEGER NOT NULL, closed_composer INTEGER NOT NULL)''')
            conn.execute('''INSERT INTO activity.posts
                (id,account_id,page_key,target_name,target_url,target_id,body,kind,media,
                 created_at,updated_at,state,message,delivery_mode,browser_finish_mode)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (new_id, account_id, page_key, target['name'], target['url'], target['page_id'],
                 post['body'], post['kind'], json.dumps(post['media'], ensure_ascii=False),
                 timestamp, timestamp, 'draft', 'Bản nháp mới từ lịch đã khôi phục; chưa gửi lên Facebook.', 'browser', 'tool'))
            conn.execute('INSERT INTO activity.events(post_id,at,state,message) VALUES (?,?,?,?)',
                         (new_id, timestamp, 'draft', 'Khôi phục từ bản nháp '+old_id+'; chưa gửi lên Facebook.'))
            conn.execute('UPDATE activity.posts SET archived=1,updated_at=? WHERE id=?', (timestamp, old_id))
            snapshot = deepcopy(row['snapshot'])
            snapshot.update(target_name=target['name'], target_url=target['url'], target_id=target['page_id'])
            conn.execute('''UPDATE schedules SET post_id=?,snapshot=?,due_at=?,state='scheduled',
                message=?,request_id='',phase='',attempted_at='',finished_at='' WHERE id=?''',
                (new_id, json.dumps(snapshot, ensure_ascii=False), due_at,
                 'Đã khôi phục lịch với bản nháp mới; giữ lịch sử lượt cũ. Chưa bật tự động đăng.', ident))
            conn.execute('''INSERT INTO schedule_recoveries
                (schedule_id,at,previous,new_post_id,target,new_due_at,checked_unpublished,closed_composer)
                VALUES (?,?,?,?,?,?,1,1)''',
                (ident, timestamp, json.dumps(preview, ensure_ascii=False), new_id,
                 json.dumps(target, ensure_ascii=False), due_at))
            # Registry writes use their own file lock; recheck target before
            # committing so an edit during source hashing cannot silently retarget.
            if _target(store, row, post) != target:
                raise ValueError('Page đã thay đổi trong lúc khôi phục; chưa lưu lịch mới.')
    return store.get(ident)
