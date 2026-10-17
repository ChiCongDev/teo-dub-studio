"""A user-confirmed new attempt after removing an uncertain old video job.

The old publication evidence stays intact. A source slot is transferred directly
to one fresh, unarmed appointment, never freed for automatic retry.
"""
from contextlib import ExitStack
from copy import deepcopy
import json
import math
import re
import time
import uuid

from teodub.page_activity import now
from teodub.page_schedule_recovery import _target, _source
from teodub.page_schedule_removal import _snapshot
from teodub.pipeline import _job_lock


def _removal(conn, ident):
    saved = conn.execute('SELECT previous FROM schedule_removals WHERE schedule_id=?', (ident,)).fetchone()
    return json.loads(saved[0]) if saved else {}


def _unverified_send(row, removal):
    """The finish recorded for one Publish click sent while result checks were paused."""
    previous = removal.get('schedule') if isinstance(removal, dict) else None
    return (isinstance(previous, dict) and previous.get('state') == 'submitted'
            and previous.get('finished_at') == row['finished_at'])


def _preview(store, row, post, events, removal):
    if (row['state'] != 'removed' or (row['finished_at'] and not _unverified_send(row, removal))
            or row['source'].get('kind') == 'text' or row['source_key'] != row['source'].get('key')
            or post['state'] not in {'draft', 'failed', 'unknown', 'review'} or post['archived']
            or post['kind'] != 'video' or len(post['media']) != 1
            or post['delivery_mode'] != 'browser' or post['browser_finish_mode'] != 'tool'
            or any(post.get(key) for key in ('browser_receipt', 'remote_assets', 'external_id',
                                             'external_url', 'manual_url', 'published_at'))
            or any(event['state'] == 'published' for event in events)):
        raise ValueError('Chỉ tạo lượt mới cho video đã xóa khỏi danh sách và chưa có bằng chứng đã đăng. '
                         'Lượt đã xác nhận đăng hoặc đang xử lý phải kiểm tra riêng.')
    if (row['post_id'] != post['id'] or row['body'] != post['body']
            or any(post.get(key) != value for key, value in row['snapshot'].items())):
        raise ValueError('Nội dung/video của lượt cũ đã thay đổi; chưa tạo lượt đăng mới.')
    target = _target(store, row, post)
    _source(store, row, post)
    return deepcopy(dict(schedule=row, post=post, events=events, target=target,
                         classification='removed_'+post['state']))


def preview(store, account_id, page_key, ident):
    row = store.get(ident)
    if (row['account_id'], row['page_key']) != (account_id, page_key):
        raise ValueError('Video không thuộc tài khoản/Page đang mở.')
    post = store.activity.post(account_id, page_key, row['post_id'])
    events = store.activity.events(account_id, page_key, row['post_id'])
    with store.database() as conn:
        removal = _removal(conn, ident)
    return _preview(store, row, post, events, removal)


def create(store, account_id, page_key, ident, due_at, *, expected_preview,
           checked_unpublished, closed_composer):
    if checked_unpublished is not True or closed_composer is not True:
        raise ValueError('Xác nhận đã kiểm tra Facebook chưa có bài/hẹn giờ và đã đóng phiên soạn cũ trước khi tạo lượt mới.')
    if isinstance(due_at, bool):
        raise ValueError('Chọn giờ đăng mới trong tương lai.')
    try:
        due_at = float(due_at)
    except (TypeError, ValueError):
        raise ValueError('Chọn giờ đăng mới trong tương lai.') from None
    if not math.isfinite(due_at) or not time.time()+5 < due_at < time.time()+366*86400:
        raise ValueError('Chọn giờ đăng mới trong tương lai, tối đa một năm tới.')
    if not isinstance(expected_preview, dict) or not isinstance(expected_preview.get('post'), dict):
        raise ValueError('Cần xem trước đúng video và lượt cũ trước khi tạo lượt mới.')
    old_post_id = expected_preview.get('post', {}).get('id', '')
    if not all(re.fullmatch(r'[0-9a-f]{32}', str(key)) for key in (ident, old_post_id)):
        raise ValueError('Mã lịch/bài không hợp lệ; chưa tạo lượt mới.')
    with ExitStack() as locks:
        for path in (store.path.parent/'schedule-locks'/ident,
                     store.path.parent/'post-locks'/old_post_id,
                     store.path.parent/'schedule-creation'):
            path.mkdir(parents=True, exist_ok=True)
            locks.enter_context(_job_lock(path))
        with store.database() as conn:
            conn.execute('ATTACH DATABASE ? AS activity', (str(store.activity.path),))
            conn.execute('BEGIN IMMEDIATE')
            if any(conn.execute(f'PRAGMA {name}.journal_mode').fetchone()[0].lower() != 'delete'
                   for name in ('main', 'activity')):
                raise ValueError('Kho dữ liệu đang dùng chế độ khác; chưa tạo lượt mới để giữ nguyên dữ liệu.')
            saved = conn.execute('SELECT * FROM schedules WHERE id=?', (ident,)).fetchone()
            if saved is None:
                raise ValueError('Không còn lượt video đã chọn.')
            row = store.decode(saved)
            if (row['account_id'], row['page_key']) != (account_id, page_key):
                raise ValueError('Video không thuộc tài khoản/Page đang mở.')
            captured = _snapshot(store, conn, row)
            approved = _preview(store, row, captured['post'], captured['events'], _removal(conn, ident))
            if approved != expected_preview or row['post_id'] != old_post_id:
                raise ValueError('Video, lượt cũ hoặc Page đã thay đổi sau khi xem trước; mở lại màn hình tạo lượt mới.')
            if not time.time()+5 < due_at < time.time()+366*86400:
                raise ValueError('Giờ đã chọn không còn trong tương lai sau khi kiểm tra video; chọn giờ mới.')
            post, target = approved['post'], approved['target']
            timestamp, new_post, new_schedule = now(), uuid.uuid4().hex, uuid.uuid4().hex
            conn.execute('''CREATE TABLE IF NOT EXISTS schedule_new_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,schedule_id TEXT NOT NULL UNIQUE,at TEXT NOT NULL,
                previous TEXT NOT NULL,new_schedule_id TEXT NOT NULL,new_post_id TEXT NOT NULL,
                new_due_at REAL NOT NULL,checked_unpublished INTEGER NOT NULL,closed_composer INTEGER NOT NULL)''')
            conn.execute('''INSERT INTO activity.posts
                (id,account_id,page_key,target_name,target_url,target_id,body,kind,media,
                 created_at,updated_at,state,message,delivery_mode,browser_finish_mode)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (new_post, account_id, page_key, target['name'], target['url'], target['page_id'],
                 row['body'], 'video', json.dumps(post['media'], ensure_ascii=False), timestamp, timestamp,
                 'draft', 'Lượt mới sau khi người dùng kiểm tra Facebook; chưa gửi.', 'browser', 'tool'))
            conn.execute('INSERT INTO activity.events(post_id,at,state,message) VALUES (?,?,?,?)',
                (new_post, timestamp, 'draft', 'Lượt mới từ lịch đã xóa '+ident+'; giữ lịch sử lượt cũ.'))
            snapshot = deepcopy(row['snapshot'])
            snapshot.update(target_name=target['name'], target_url=target['url'], target_id=target['page_id'])
            # Transfer the unique source directly within the same transaction.
            # Do not rewrite any old attempted state, post, receipt or event.
            conn.execute('UPDATE schedules SET source_key=? WHERE id=?', ('removed:'+ident, ident))
            conn.execute('''INSERT INTO schedules
                (id,account_id,page_key,post_id,source_key,source,body,snapshot,due_at,created_at,state,message)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
                (new_schedule, account_id, page_key, new_post, row['source']['key'],
                 json.dumps(row['source'], ensure_ascii=False), row['body'],
                 json.dumps(snapshot, ensure_ascii=False), due_at, timestamp, 'scheduled',
                 'Đã tạo lượt đăng mới sau khi xác nhận kiểm tra Facebook. Chưa bật tự đăng.'))
            conn.execute('''INSERT INTO schedule_new_attempts
                (schedule_id,at,previous,new_schedule_id,new_post_id,new_due_at,checked_unpublished,closed_composer)
                VALUES (?,?,?,?,?,?,1,1)''',
                (ident, timestamp, json.dumps(approved, ensure_ascii=False), new_schedule, new_post, due_at))
            if _target(store, row, post) != target:
                raise ValueError('Page đã thay đổi trong lúc tạo lượt mới; chưa lưu.')
            if not time.time()+5 < due_at < time.time()+366*86400:
                raise ValueError('Giờ đã chọn không còn trong tương lai; chưa lưu lượt mới.')
    return store.get(new_schedule)


def history(store, ident):
    with store.database() as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='schedule_new_attempts'").fetchone():
            return []
        return [dict(row, previous=json.loads(row['previous']),
                     checked_unpublished=bool(row['checked_unpublished']), closed_composer=bool(row['closed_composer']))
                for row in map(dict, conn.execute('SELECT * FROM schedule_new_attempts WHERE schedule_id=? ORDER BY id', (ident,)))]
