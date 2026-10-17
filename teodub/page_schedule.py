"""Explicitly approved, local video schedules. No catch-up bursts or startup publishing."""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import queue
import re
import sqlite3
import threading
import time
import uuid

from teodub.campaign import read_json
from teodub.common import hash_file
from teodub.page_activity import PageActivity, now
from teodub.page_publish_settings import result_checks_enabled
from teodub.pipeline import _job_lock

LOCAL_TIME = timezone(timedelta(hours=7))
STATE_LABELS = {'scheduled':'Đã lưu, chưa bật', 'armed':'Đang chờ giờ đăng', 'paused':'Đã tạm dừng',
    'preparing':'Đang chuẩn bị trong Chrome', 'submitting':'Đang gửi tới Facebook',
    'verifying':'Đã gửi — đang xác minh', 'published':'Đã xác nhận đăng',
    'submitted':'Đã gửi — chưa xác minh',
    'needs_attention':'Cần kiểm tra', 'cancelled':'Đã bỏ lịch chưa gửi', 'removed':'Đã xóa khỏi danh sách'}


def parse_time(value):
    try:
        result = datetime.strptime(value.strip(), '%Y-%m-%d %H:%M').replace(tzinfo=LOCAL_TIME)
    except (ValueError, AttributeError):
        raise ValueError('Nhập giờ đăng theo dạng YYYY-MM-DD HH:MM, giờ Việt Nam (UTC+7).') from None
    return result.timestamp()


def display_time(value):
    return datetime.fromtimestamp(float(value), LOCAL_TIME).strftime('%Y-%m-%d %H:%M')


def campaign_video_source(data_dir, directory, number):
    """Read only a completed rendered output, never an original download."""
    directory = Path(directory).resolve()
    root = Path(data_dir).resolve()/'campaigns'
    if directory.parent != root or not re.fullmatch(r'[0-9a-f]{32}', directory.name):
        raise ValueError('Video không thuộc chiến dịch đã lưu của Tèo.')
    manifest = read_json(directory/'campaign.json')
    if manifest.get('id') != directory.name or manifest.get('archived_at'):
        raise ValueError('Chiến dịch đã bỏ khỏi danh sách hoặc không còn khớp dữ liệu.')
    item = next((item for item in manifest['items'] if item['number'] == int(number)), None)
    if not item or item.get('status') != 'completed':
        raise ValueError('Chỉ xếp lịch video đã hoàn tất. Video còn cần duyệt bản dịch hoặc đang xử lý phải kiểm tra trước.')
    job = directory/'jobs'/f'{int(number):04}'
    if Path(item['job']).resolve() != job or job.is_symlink():
        raise ValueError('Phiên video không khớp chiến dịch.')
    from teodub.campaign_settings import effective_job
    job = effective_job(directory, item)
    if item.get('execution_job') and hash_file(job/'config.json') != item.get('execution_config_sha256'):
        raise ValueError('Cấu hình video hoàn tất đã thay đổi.')
    rendered = read_json(job/'state.json').get('rendered', {})
    output = item.get('outputs', {}).get('video')
    digest = rendered.get('hashes', {}).get('video', '')
    if (not output or rendered.get('outputs') != item['outputs'] or
            not re.fullmatch(r'[0-9a-f]{64}', digest)):
        raise ValueError('Thành phẩm chưa có dữ liệu kiểm tra khớp với phiên Tèo.')
    video = Path(output).resolve()
    destination = Path(manifest['output_dir']).resolve()
    if not video.is_relative_to(destination) or video.suffix.lower() not in {'.mp4', '.mov'} or not video.is_file():
        raise ValueError('Không tìm thấy video thành phẩm trong thư mục xuất của chiến dịch.')
    stat = video.stat()
    if stat.st_size <= 0:
        raise ValueError('Video thành phẩm đang rỗng.')
    return dict(key=manifest['id']+':'+str(number), campaign=str(directory), number=int(number),
        label=f'{manifest["name"]} · Video {number}', path=str(video), sha256=digest,
        size=stat.st_size, mtime_ns=stat.st_mtime_ns)


class PageScheduleStore:
    def __init__(self, data_dir):
        self.activity = PageActivity(data_dir)
        self.data_dir = Path(data_dir).resolve()
        self.path = self.activity.accounts.directory/'schedules.sqlite3'

    @contextmanager
    def database(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=3)
        conn.row_factory = sqlite3.Row
        try:
            conn.executescript('''
              CREATE TABLE IF NOT EXISTS templates (
                account_id TEXT NOT NULL,page_key TEXT NOT NULL,caption TEXT NOT NULL,hashtags TEXT NOT NULL,
                PRIMARY KEY(account_id,page_key));
              CREATE TABLE IF NOT EXISTS schedules (
                id TEXT PRIMARY KEY,account_id TEXT NOT NULL,page_key TEXT NOT NULL,post_id TEXT NOT NULL,
                source_key TEXT NOT NULL,source TEXT NOT NULL,body TEXT NOT NULL,snapshot TEXT NOT NULL,
                due_at REAL NOT NULL,created_at TEXT NOT NULL,state TEXT NOT NULL,message TEXT NOT NULL,
                request_id TEXT NOT NULL DEFAULT '',phase TEXT NOT NULL DEFAULT '',attempted_at TEXT NOT NULL DEFAULT '',
                finished_at TEXT NOT NULL DEFAULT '',
                UNIQUE(account_id,page_key,source_key));
              CREATE TABLE IF NOT EXISTS preparation_retries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,schedule_id TEXT NOT NULL,
                at TEXT NOT NULL,previous TEXT NOT NULL,new_due_at REAL NOT NULL);
              CREATE TABLE IF NOT EXISTS immediate_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,schedule_id TEXT NOT NULL,
                at TEXT NOT NULL,previous TEXT NOT NULL,new_due_at REAL NOT NULL);
              CREATE TABLE IF NOT EXISTS schedule_removals (
                schedule_id TEXT PRIMARY KEY,at TEXT NOT NULL,previous TEXT NOT NULL,
                source_released INTEGER NOT NULL);
            ''')
            conn.commit()
            with conn:
                yield conn
        except sqlite3.Error as exc:
            raise ValueError('Chưa lưu/đọc được lịch đăng. Lịch cũ được giữ. '+str(exc)) from None
        finally:
            conn.close()

    @staticmethod
    def decode(row):
        result = dict(row)
        for key in ('source','snapshot'):
            result[key] = json.loads(result[key])
        return result

    def get(self, ident):
        with self.database() as conn:
            row = conn.execute('SELECT * FROM schedules WHERE id=?', (ident,)).fetchone()
        if not row:
            raise ValueError('Không còn lịch đăng này.')
        return self.decode(row)

    def list(self, account_id=None, page_key=None):
        if not self.path.exists():
            return []
        with self.database() as conn:
            rows = conn.execute('SELECT * FROM schedules ORDER BY due_at,created_at').fetchall()
        return [self.decode(row) for row in rows if (not account_id or row['account_id']==account_id)
                and (not page_key or row['page_key']==page_key)]

    def template(self, account_id, page_key):
        if not self.path.exists():
            return dict(caption='',hashtags='')
        with self.database() as conn:
            row = conn.execute('SELECT caption,hashtags FROM templates WHERE account_id=? AND page_key=?',
                               (account_id,page_key)).fetchone()
        return dict(row) if row else dict(caption='',hashtags='')

    def save_template(self, account_id, page_key, caption, hashtags):
        self.activity.page(account_id,page_key)
        if len(caption)+len(hashtags)>60000:
            raise ValueError('Caption và hashtag quá dài; tối đa tổng 60.000 ký tự.')
        with self.database() as conn:
            conn.execute('INSERT INTO templates VALUES (?,?,?,?) ON CONFLICT(account_id,page_key) DO UPDATE '
                'SET caption=excluded.caption,hashtags=excluded.hashtags', (account_id,page_key,caption,hashtags))

    def create(self, account_id, page_key, source, caption, hashtags, due_at, *, expected_target=None,
               persist_template=True):
        due_at = float(due_at)
        if not time.time()+5 < due_at < time.time()+366*86400:
            raise ValueError('Chọn giờ đăng trong tương lai, tối đa một năm tới.')
        if len(caption)+len(hashtags)>60000:
            raise ValueError('Caption và hashtag quá dài; tối đa tổng 60.000 ký tự.')
        page = self.activity.page(account_id,page_key)
        target = {key:page.get(key,'') for key in ('name','url','page_id')}
        if not re.fullmatch(r'[0-9]+',target['page_id']):
            raise ValueError('Page chưa có ID bằng số. Load Page hoặc Sửa Page để lưu đúng ID và tên trước khi xếp lịch.')
        if expected_target is not None and expected_target != target:
            raise ValueError('Page đã đổi sau màn hình duyệt; xem trước lại đúng Page trước khi lưu lịch.')
        actual = campaign_video_source(self.data_dir,source['campaign'],source['number'])
        if actual != source:
            raise ValueError('Thành phẩm đã thay đổi; chọn lại video trước khi lưu lịch.')
        body = '\n\n'.join(value.strip() for value in (caption,hashtags) if value.strip())
        lock = self.path.parent/'schedule-creation'
        lock.mkdir(parents=True,exist_ok=True)
        with _job_lock(lock):
            with self.database() as conn:
                if conn.execute('SELECT id FROM schedules WHERE account_id=? AND page_key=? AND source_key=?',
                                (account_id,page_key,source['key'])).fetchone():
                    raise ValueError('Video này đã có lịch trên Page. Xem lịch đã lưu để tránh đăng trùng.')
            post = self.activity.save_draft(account_id,page_key,body,[source['path']],
                                            delivery_mode='browser',browser_finish_mode='tool')
            if (post['target_name'],post['target_url'],post['target_id']) != (target['name'],target['url'],target['page_id']):
                raise ValueError('Page đã đổi trong lúc lưu lịch; chưa bật đăng. Xem trước lại đúng Page.')
            snapshot = {key:post[key] for key in ('target_name','target_url','target_id','body','media','delivery_mode','browser_finish_mode')}
            ident = uuid.uuid4().hex
            with self.database() as conn:
                conn.execute('INSERT INTO schedules (id,account_id,page_key,post_id,source_key,source,body,snapshot,'
                    'due_at,created_at,state,message) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                    (ident,account_id,page_key,post['id'],source['key'],json.dumps(source,ensure_ascii=False),body,
                     json.dumps(snapshot,ensure_ascii=False),due_at,now(),'scheduled','Đã lưu lịch; chưa bật tự động.'))
            if persist_template:
                self.save_template(account_id,page_key,caption,hashtags)
        return self.get(ident)

    def create_post(self, account_id, page_key, post_id, due_at, *, expected_target=None,
                    expected_post_key=None):
        """Schedule an existing text draft; creation never submits to Facebook."""
        due_at = float(due_at)
        if not time.time()+5 < due_at < time.time()+366*86400:
            raise ValueError('Chọn giờ đăng trong tương lai, tối đa một năm tới.')
        page = self.activity.page(account_id, page_key)
        target = {key: page.get(key, '') for key in ('name', 'url', 'page_id')}
        if not re.fullmatch(r'[0-9]+', target['page_id']):
            raise ValueError('Page chưa có ID bằng số. Load Page hoặc Sửa Page trước khi hẹn giờ.')
        if expected_target is not None and expected_target != target:
            raise ValueError('Page đã đổi sau màn hình duyệt; xem trước lại đúng Page.')
        lock = self.path.parent/'schedule-creation'
        lock.mkdir(parents=True, exist_ok=True)
        with _job_lock(lock):
            with self.activity.database() as posts:
                posts.execute('BEGIN IMMEDIATE')
                post = self.activity._post(posts, account_id, page_key, post_id)
                if (post['archived'] or post['state'] not in {'draft', 'failed'}
                        or post['external_id'] or post['external_url'] or post['manual_url']
                        or post['browser_review'] or post['browser_receipt'] or post.get('browser_tracking') or post['remote_assets']):
                    raise ValueError('Bài đã có lượt chuẩn bị/gửi hoặc đã bỏ khỏi danh sách; kiểm tra Facebook trước.')
                if post['delivery_mode'] != 'browser' or post['browser_finish_mode'] != 'tool':
                    raise ValueError('Lưu bài với cách đăng Trình duyệt web và Tèo bấm Đăng sau khi duyệt trước khi hẹn giờ.')
                if post['kind'] != 'text' or post['media'] or not post['body'].strip():
                    raise ValueError('Hẹn giờ bài chữ chỉ nhận nội dung không có ảnh/video. Video chiến dịch dùng Đăng video.')
                if (post['target_name'], post['target_url'], post['target_id']) != (target['name'], target['url'], target['page_id']):
                    raise ValueError('Page của bản nháp đã đổi; lưu lại bài và xem trước đúng Page.')
                if expected_post_key is not None:
                    from teodub.page_command_bridge import public_post
                    if public_post(post)['post_key'] != expected_post_key:
                        raise ValueError('Nội dung bài đã đổi sau khi xem trước; chưa lưu lịch.')
                source = dict(kind='text', key='post:'+post['id'],
                    label='Bài chữ: '+' '.join(post['body'].split())[:80])
                snapshot = {key: post[key] for key in ('target_name', 'target_url', 'target_id', 'body',
                    'kind', 'media', 'delivery_mode', 'browser_finish_mode')}
                ident = uuid.uuid4().hex
                with self.database() as conn:
                    conn.execute('BEGIN IMMEDIATE')
                    if conn.execute('SELECT id FROM schedules WHERE account_id=? AND page_key=? AND source_key=?',
                            (account_id, page_key, source['key'])).fetchone():
                        raise ValueError('Bài này đã có lịch trên Page. Xem lịch đã lưu để tránh đăng trùng.')
                    conn.execute('INSERT INTO schedules (id,account_id,page_key,post_id,source_key,source,body,snapshot,'
                        'due_at,created_at,state,message) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                        (ident, account_id, page_key, post['id'], source['key'], json.dumps(source, ensure_ascii=False),
                         post['body'], json.dumps(snapshot, ensure_ascii=False), due_at, now(), 'scheduled',
                         'Đã lưu lịch bài chữ; chưa bật tự động.'))
        return self.get(ident)

    def update(self, ident, *, expected=None, **values):
        if set(values)-{'due_at','state','message','request_id','phase','attempted_at','finished_at'}:
            raise ValueError('Không thay đổi nội dung/Page/video đã duyệt của lịch đăng.')
        with self.database() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('SELECT * FROM schedules WHERE id=?',(ident,)).fetchone()
            if not row or row['state'] == 'removed' or expected is not None and row['state'] not in expected:
                raise ValueError('Lịch đang được xử lý hoặc đã thay đổi; làm mới để xem trạng thái.')
            conn.execute('UPDATE schedules SET '+','.join(key+'=?' for key in values)+' WHERE id=?',
                         (*values.values(),ident))
        return self.get(ident)

    def reschedule(self, ident, due_at):
        row = self.get(ident)
        if row['attempted_at']:
            raise ValueError('Lịch đã có lượt chuẩn bị/gửi. Kiểm tra bài trên Facebook trước; không tự đăng lại.')
        if not time.time()+5 < float(due_at) < time.time()+366*86400:
            raise ValueError('Chọn giờ đăng mới trong tương lai, tối đa một năm tới.')
        return self.update(ident, expected={'scheduled','paused','needs_attention'}, due_at=float(due_at),
                           state='scheduled',message='Đã đổi giờ; cần bấm Bật / Tiếp tục lịch.')

    def arm_now(self, account_id, page_key, ident, expected_schedule):
        """Move the SAME reviewed, untouched appointment to now under its controller lock."""
        row = self.get(ident)
        if (row['account_id'], row['page_key']) != (account_id, page_key):
            raise ValueError('Lịch không thuộc tài khoản/Page này.')
        lock = self.activity.accounts.directory/'post-locks'/row['post_id']
        lock.mkdir(parents=True, exist_ok=True)
        with _job_lock(lock), self.activity.database() as posts, self.database() as conn:
            posts.execute('BEGIN IMMEDIATE')
            conn.execute('BEGIN IMMEDIATE')
            saved = conn.execute('SELECT * FROM schedules WHERE id=?', (ident,)).fetchone()
            if not saved:
                raise ValueError('Không còn lịch đăng này.')
            row = self.decode(saved)
            if row != expected_schedule:
                raise ValueError('Lịch đã thay đổi từ lúc xem trước. Đóng và bấm Đăng ngay lại để xem đúng bài.')
            if (row['attempted_at'] or row['request_id'] or row['finished_at'] or
                    row['state'] not in {'scheduled', 'armed', 'paused', 'needs_attention'}):
                raise ValueError('Bài đã có lượt xử lý hoặc đã hoàn tất. Kiểm tra Facebook trước; không tự đăng lại.')
            post = self.activity._post(posts, account_id, page_key, row['post_id'])
            if (post['archived'] or post['state'] not in {'draft', 'failed'} or
                    any(post.get(key) for key in ('browser_review', 'browser_receipt', 'browser_tracking', 'remote_assets',
                                                  'external_id', 'external_url', 'manual_url')) or
                    any(post.get(key) != value for key, value in row['snapshot'].items()) or
                    post['delivery_mode'] != 'browser' or post['browser_finish_mode'] != 'tool' or
                    posts.execute("SELECT 1 FROM events WHERE post_id=? AND state IN "
                        "('review','submitting','processing','published','unknown') LIMIT 1",
                        (post['id'],)).fetchone()):
                raise ValueError('Bản nháp đã thay đổi hoặc có lượt xử lý. Kiểm tra Facebook trước; chưa đăng lại.')
            page = self.activity.page(account_id, page_key)
            if any(page.get(key, '') != post[field] for key, field in
                   (('name', 'target_name'), ('url', 'target_url'), ('page_id', 'target_id'))):
                raise ValueError('Page đã đổi sau khi duyệt lịch; chưa đăng.')
            self.activity.verify_media(post)
            due_at = time.time()
            conn.execute('INSERT INTO immediate_requests (schedule_id,at,previous,new_due_at) VALUES (?,?,?,?)',
                (ident, now(), json.dumps(row, ensure_ascii=False), due_at))
            conn.execute("UPDATE schedules SET due_at=?,state='armed',phase='',message=? WHERE id=?",
                (due_at, 'Đăng ngay: đã nhận bài đã duyệt; đang chờ kiểm tra video và Chrome của tài khoản.', ident))
        return self.get(ident)

    def immediate_history(self, ident):
        with self.database() as conn:
            return [dict(event, previous=json.loads(event['previous'])) for event in
                conn.execute('SELECT * FROM immediate_requests WHERE schedule_id=? ORDER BY id', (ident,))]

    @staticmethod
    def _retryable_preparation(row, post, events):
        # Existing publisher persists failed only before changing the composer
        # or uploading anything. Restrict recovery to this known locator fault.
        failure = re.compile(r'^Không xác định duy nhất (vùng Page(?: sau khi chuyển)?|'
            r'tên Page trong nội dung chính|vùng nội dung mang tên Page|nút Tạo bài viết của Page|hộp Tạo bài viết) \(\d+ mục\)\.')
        return bool(row['state'] == 'needs_attention' and row['phase'] == 'prepare' and row['attempted_at']
            and post['state'] == 'failed' and failure.match(row['message'])
            and post['message'] == row['message'] and not post['archived']
            and post['delivery_mode'] == 'browser' and post['browser_finish_mode'] == 'tool'
            and not any(post.get(key) for key in ('browser_review','browser_receipt','browser_tracking','remote_assets',
                                                'external_id','external_url','manual_url'))
            and not any(event['state'] in {'review','submitting','published','unknown','processing'} for event in events)
            and all(post.get(key) == value for key, value in row['snapshot'].items()))

    def can_retry_preparation(self, row):
        try:
            post = self.activity.post(row['account_id'], row['page_key'], row['post_id'])
            events = self.activity.events(row['account_id'], row['page_key'], row['post_id'])
            return self._retryable_preparation(row, post, events)
        except (OSError, ValueError, KeyError):
            return False

    def preparation_retry_history(self, ident):
        with self.database() as conn:
            return [dict(event, previous=json.loads(event['previous'])) for event in
                conn.execute('SELECT * FROM preparation_retries WHERE schedule_id=? ORDER BY id', (ident,))]

    def reschedule_failed_preparation(self, ident, due_at):
        due_at = float(due_at)
        if not time.time()+5 < due_at < time.time()+366*86400:
            raise ValueError('Chọn giờ đăng mới trong tương lai, tối đa một năm tới.')
        row = self.get(ident)
        lock = self.activity.accounts.directory/'post-locks'/row['post_id']
        lock.mkdir(parents=True, exist_ok=True)
        with _job_lock(lock), self.activity.database() as posts:
            posts.execute('BEGIN IMMEDIATE')
            post = self.activity._post(posts, row['account_id'], row['page_key'], row['post_id'])
            events = [dict(event) for event in posts.execute('SELECT state FROM events WHERE post_id=?', (row['post_id'],))]
            if not self._retryable_preparation(row, post, events):
                raise ValueError('Chỉ xếp lại lỗi nhận diện Page trước khi nhập/tải tệp. Bài đã chuẩn bị hoặc chưa rõ kết quả phải kiểm tra Facebook.')
            page = self.activity.page(row['account_id'], row['page_key'])
            if any(page.get(key, '') != post[field] for key, field in
                   (('name','target_name'), ('url','target_url'), ('page_id','target_id'))):
                raise ValueError('Page đã đổi sau khi lưu lịch; chưa xếp lại.')
            self.activity.verify_media(post)
            if row['source'].get('kind') != 'text' and campaign_video_source(self.data_dir,
                    row['source']['campaign'], row['source']['number']) != row['source']:
                raise ValueError('Video thành phẩm đã thay đổi; chưa xếp lại lịch.')
            with self.database() as conn:
                conn.execute('BEGIN IMMEDIATE')
                current = self.decode(conn.execute('SELECT * FROM schedules WHERE id=?', (ident,)).fetchone())
                if current != row:
                    raise ValueError('Lịch đã thay đổi; làm mới trước khi xếp lại.')
                conn.execute('INSERT INTO preparation_retries(schedule_id,at,previous,new_due_at) VALUES (?,?,?,?)',
                    (ident, now(), json.dumps(row, ensure_ascii=False), due_at))
                conn.execute("UPDATE schedules SET due_at=?,state='scheduled',message=?,request_id='',phase='',attempted_at='',finished_at='' WHERE id=?",
                    (due_at, 'Đã xếp lại lỗi nhận diện Page trước khi nhập/tải tệp. Giữ lịch sử lượt lỗi; bấm Bật phần đã chọn khi sẵn sàng.', ident))
        return self.get(ident)

    def cancel_unattempted(self, ident):
        with self.database() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('SELECT * FROM schedules WHERE id=?',(ident,)).fetchone()
            if not row or row['attempted_at'] or row['state'] not in {'scheduled','armed','paused','needs_attention'}:
                raise ValueError('Chỉ bỏ lịch chưa gửi vào Chrome. Kiểm tra Page nếu đã có lượt thao tác.')
            # The same draft may be open in a Page manager. Do not free the
            # duplicate slot after any independently prepared browser action.
            with self.activity.database() as posts:
                posts.execute('BEGIN IMMEDIATE')
                post = self.activity._post(posts,row['account_id'],row['page_key'],row['post_id'])
                if (post['state'] not in {'draft','failed'} or post['external_id'] or post['external_url'] or
                        post['browser_review'] or post['browser_receipt'] or post.get('browser_tracking') or post['manual_url'] or post['remote_assets']):
                    raise ValueError('Bản nháp đã có thao tác từ cửa sổ Page. Kiểm tra Facebook trước; chưa bỏ lịch để tránh đăng trùng.')
                posts.execute('UPDATE posts SET archived=1,updated_at=? WHERE id=?',(now(),post['id']))
                self.activity._event(posts,post['id'],post['state'],'Lịch chưa gửi đã được bỏ; ẩn bản nháp cũ, giữ lịch sử và video.')
            conn.execute('UPDATE schedules SET state=?,source_key=?,message=? WHERE id=?',
                ('cancelled','cancelled:'+ident,'Đã bỏ lịch chưa gửi; giữ lịch sử và tệp video.',ident))
        return self.get(ident)

    def can_recover_schedule(self, row):
        from teodub.page_schedule_recovery import can_recover
        return can_recover(self, row)

    def recovery_preview(self, account_id, page_key, ident):
        from teodub.page_schedule_recovery import preview_recovery
        return preview_recovery(self, account_id, page_key, ident)

    def recover_schedule(self, account_id, page_key, ident, due_at, **approval):
        from teodub.page_schedule_recovery import recover
        return recover(self, account_id, page_key, ident, due_at, **approval)

    def recovery_history(self, ident):
        from teodub.page_schedule_recovery import recovery_history
        return recovery_history(self, ident)

    def removal_preview(self, identifiers, account_id, page_key):
        from teodub.page_schedule_removal import preview
        return preview(self, identifiers, account_id, page_key)

    def remove_many(self, previews, account_id, page_key):
        from teodub.page_schedule_removal import remove_many
        return remove_many(self, previews, account_id, page_key)

    def removal_history(self, ident):
        from teodub.page_schedule_removal import history
        return history(self, ident)

    def new_attempt_preview(self, account_id, page_key, ident):
        from teodub.page_schedule_new_attempt import preview
        return preview(self, account_id, page_key, ident)

    def create_new_attempt(self, account_id, page_key, ident, due_at, **approval):
        from teodub.page_schedule_new_attempt import create
        return create(self, account_id, page_key, ident, due_at, **approval)

    def new_attempt_history(self, ident):
        from teodub.page_schedule_new_attempt import history
        return history(self, ident)


class PageScheduleController:
    def __init__(self, owner):
        self.owner = owner
        self.store = PageScheduleStore(owner.studio.data_dir)
        self.active = set()
        self.guards = {}
        self.validation = queue.Queue()
        self.last_tick = 0
        # Only a continuously observed, locally owned wait can extend the
        # lateness allowance. These witnesses are deliberately never persisted.
        self.queue_waits = {}
        self.queue_starts = {}
        self.queue_interrupted = {}
        self.queue_last_observed = None
        self.inspections = {}
        self.closed = False
        # A saved schedule is never authorization to restart a lost browser job.
        for row in self.store.list():
            if row['state'] in {'armed','preparing','submitting','verifying'}:
                try:
                    with self.guard(row['id']):
                        self.store.update(row['id'],expected={row['state']},
                            state='needs_attention' if row['attempted_at'] else 'paused',
                            message='Phiên Tèo trước đã dừng. Kiểm tra kết quả nếu đã có lượt gửi; chưa tự chạy lại.')
                except (OSError,ValueError):
                    pass  # Another process still owns the schedule.
        self._restore_result_tracking()

    def _restore_result_tracking(self):
        """Resume only reads of recent submitted results, never a lost upload."""
        wall = time.time()
        for row in self.store.list():
            if row['state'] != 'needs_attention' or row['phase'] not in {'publish', 'verify'}:
                continue
            try:
                post = self.store.activity.post(row['account_id'], row['page_key'], row['post_id'])
                tracking = post.get('browser_tracking') or {}
                receipt = post.get('browser_receipt') or {}
                if (post['state'] == 'published' and receipt.get('source') == 'visible_dom'
                        and post.get('external_url') and receipt.get('url') == post['external_url']
                        and not any(post.get(key) != value for key, value in row['snapshot'].items())):
                    with self.guard(row['id']):
                        if self.store.get(row['id']) == row:
                            self.store.update(row['id'], expected={'needs_attention'}, state='published',
                                finished_at=row['finished_at'] or now(), message=post['message'])
                    continue
                if post['state'] not in {'processing', 'unknown'} or not post.get('browser_review'):
                    continue
                if not result_checks_enabled(self.store.data_dir):
                    if self._is_submitted_attempt(row, post):
                        with self.guard(row['id']):
                            if self.store.get(row['id']) == row:
                                self._finish_without_verification(row)
                    continue
                if tracking:
                    eligible = tracking.get('deadline_at', 0) > wall
                else:
                    events = self.store.activity.events(row['account_id'], row['page_key'], row['post_id'])
                    sent = [event for event in events if event['state'] == 'processing']
                    eligible = bool(sent and 0 <= wall-datetime.fromisoformat(sent[-1]['at']).timestamp() <= 7200)
                if not eligible:
                    continue
                guard = self.guard(row['id'])
                guard.__enter__()
                try:
                    current = self.store.get(row['id'])
                    if current != row:
                        raise ValueError('Lượt đã thay đổi trong lúc nối lại theo dõi.')
                    self.store.update(row['id'], expected={'needs_attention'}, state='verifying', phase='verify',
                        request_id='', message='Đã gửi — chờ nối lại kiểm tra kết quả; không tải hoặc đăng lại.')
                except BaseException:
                    guard.__exit__(None, None, None)
                    raise
                self.guards[row['id']] = guard
                self.active.add(row['id'])
            except (OSError, ValueError, TypeError, KeyError):
                continue

    def _is_submitted_attempt(self, row, post):
        """Local one-click evidence only; never infer publication or read Facebook."""
        review = post.get('browser_review') or {}
        tracking = post.get('browser_tracking') or {}
        if (not row.get('attempted_at') or row.get('phase') not in {'publish', 'verify'}
                or post.get('state') not in {'processing', 'unknown'} or post.get('archived')
                or post.get('delivery_mode') != 'browser' or post.get('browser_finish_mode') != 'tool'
                or not isinstance(review, dict) or not review or not isinstance(tracking, dict)
                or type(tracking.get('version')) is not int or tracking.get('version') != 1
                or tracking.get('review') != review
                or any(post.get(key) != value for key, value in row['snapshot'].items())
                or any(tracking.get(key) != value for key, value in
                    (('account_id', row['account_id']), ('page_key', row['page_key']), ('post_id', row['post_id'])))
                or any(post.get(key) != row[field] for key, field in
                    (('account_id', 'account_id'), ('page_key', 'page_key'), ('id', 'post_id')))):
            return False
        submitted_at = tracking.get('submitted_at')
        if (not isinstance(submitted_at, (int, float)) or isinstance(submitted_at, bool)
                or not math.isfinite(submitted_at) or submitted_at <= 0):
            return False
        for key in ('account_id', 'page_key', 'target_name', 'target_url', 'target_id', 'browser_finish_mode'):
            if review.get(key) != post.get(key):
                return False
        if (review.get('post_id') != post['id'] or review.get('body') != row['body']
                or post.get('body') != row['body']):
            return False
        media = ([] if row['source'].get('kind') == 'text' else
                 [dict(saved, sha256=row['source']['sha256']) for saved in row['snapshot']['media']])
        if review.get('media') != media:
            return False
        events = self.store.activity.events(row['account_id'], row['page_key'], row['post_id'])
        sent = False
        for event in events:
            if event['state'] == 'submitting':
                sent = True
            elif event['state'] == 'processing' and sent:
                return True
        return False

    def _finish_without_verification(self, row):
        self.store.update(row['id'], state='submitted', finished_at=row['finished_at'] or now(),
            message='Đã gửi một lần tới Facebook. Kiểm tra trạng thái đang tạm tắt; chưa xác minh đã đăng.')
        self.release(row['id'])

    def guard(self, ident):
        if not re.fullmatch(r'[0-9a-f]{32}',ident):
            raise ValueError('Mã lịch đăng không hợp lệ.')
        path = self.store.path.parent/'schedule-locks'/ident
        path.mkdir(parents=True,exist_ok=True)
        return _job_lock(path)

    def release(self, ident):
        self.active.discard(ident)
        self.queue_waits.pop(ident, None)
        self.queue_interrupted.pop(ident, None)
        guard = self.guards.pop(ident,None)
        if guard:
            guard.__exit__(None,None,None)

    def remove_many(self, previews, account_id, page_key):
        """Explicit local removal; never cancels or closes a browser action."""
        from teodub.page_schedule_removal import remove_one
        results = dict(removed=[], skipped={})
        seen = set()
        for expected in previews:
            ident = expected.get('id', '') if isinstance(expected, dict) else ''
            if ident in seen:
                continue
            seen.add(ident)
            try:
                studio = self.owner.studio
                if self.closed or any(getattr(studio, flag, False) is True
                        for flag in ('closing', 'closed', 'restart_draining')):
                    raise ValueError('Tèo đang đóng hoặc khởi động lại; chờ xong trước khi xóa video.')
                row = self.store.get(ident)
                if (row['account_id'], row['page_key']) != (account_id, page_key):
                    raise ValueError('Video không thuộc tài khoản/Page đang mở.')
                session = self.owner.sessions.get(account_id)
                if session and any(request.get('state') in {'queued', 'running', 'opening', 'verifying'}
                        and (key == row['request_id'] or request.get('post_id') == row['post_id'])
                        for key, request in getattr(session, 'post_actions', {}).items()):
                    raise ValueError('Chrome đang xử lý video này; chờ lượt thao tác kết thúc trước khi xóa.')
                owned = ident in self.guards
                if owned and any(row[key] for key in ('phase', 'request_id', 'attempted_at')):
                    raise ValueError('Lịch còn lượt xử lý đang chạy; chờ xong trước khi xóa video.')
                remove_one(self.store, expected, account_id, page_key, guard_owned=owned)
                self.release(ident)
                results['removed'].append(ident)
            except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
                results['skipped'][ident] = str(exc)
        return results

    def start(self, ident):
        if self.closed:
            raise ValueError('Tèo đang đóng; mở lại để tiếp tục lịch chưa gửi.')
        row = self.store.get(ident)
        if ident in self.active:
            return row
        if row['attempted_at']:
            raise ValueError('Lịch đã có lượt thao tác; kiểm tra Facebook trước, không tự gửi lại.')
        if row['due_at']<=time.time():
            raise ValueError('Giờ đăng đã qua; đổi giờ mới để tránh dồn bài.')
        guard = self.guard(ident)
        guard.__enter__()
        try:
            row = self.store.update(ident,expected={'scheduled','paused','needs_attention'},state='armed',
                message='Đã bật lịch. Giữ Tèo chạy và Chrome có phiên đăng nhập; đến giờ mới thao tác.')
        except Exception:
            guard.__exit__(None,None,None)
            raise
        self.guards[ident] = guard
        self.active.add(ident)
        return row

    def retry_preparation(self, account_id, page_key, ident, due_at):
        if self.closed:
            raise ValueError('Tèo đang đóng; mở lại trước khi xếp lại lịch lỗi.')
        row = self.store.get(ident)
        if (row['account_id'],row['page_key']) != (account_id,page_key):
            raise ValueError('Lịch không thuộc tài khoản/Page này.')
        with self.guard(ident):
            return self.store.reschedule_failed_preparation(ident, due_at)

    def _recovery_idle(self, account_id):
        studio = self.owner.studio
        if self.closed or any(getattr(studio, flag, False) is True for flag in ('closing', 'closed', 'restart_draining')):
            raise ValueError('Tèo đang đóng hoặc khởi động lại; chưa khôi phục lịch.')
        session = self.owner.sessions.get(account_id)
        if session and (getattr(session, 'pending', False) or getattr(session, 'opened', False)
                        or getattr(session, 'loading_pages', False)
                        or any(item.get('state') in {'queued', 'running', 'opening'}
                               for key in ('post_actions', 'navigation', 'page_load_requests')
                               for item in getattr(session, key, {}).values())):
            raise ValueError('Đóng / Lưu phiên tài khoản này trước khi khôi phục; Chrome hoặc yêu cầu cũ còn mở.')

    def recovery_preview(self, account_id, page_key, ident):
        return self.store.recovery_preview(account_id, page_key, ident)

    def recover_schedule(self, account_id, page_key, ident, due_at, **approval):
        self._recovery_idle(account_id)
        if ident in self.active or ident in self.guards:
            raise ValueError('Lịch đang có lượt xử lý; tạm dừng và chờ xong trước khi khôi phục.')
        return self.store.recover_schedule(account_id, page_key, ident, due_at, **approval)

    def new_attempt_preview(self, account_id, page_key, ident):
        return self.store.new_attempt_preview(account_id, page_key, ident)

    def can_follow_result(self, row):
        """Check an actual prior submission without asking for a public link."""
        try:
            if (not result_checks_enabled(self.store.data_dir) or self.closed
                    or not isinstance(row, dict) or row.get('id') in self.guards
                    or row.get('state') != 'needs_attention' or row.get('phase') not in {'publish', 'verify'}):
                return False
            post = self.store.activity.post(row['account_id'], row['page_key'], row['post_id'])
            if (post['state'] not in {'processing', 'unknown'} or not post.get('browser_review')
                    or post.get('delivery_mode') != 'browser' or post.get('archived')):
                return False
            return any(event['state'] == 'processing' for event in self.store.activity.events(
                row['account_id'], row['page_key'], row['post_id']))
        except (OSError, ValueError, KeyError, TypeError):
            return False

    def follow_result(self, account_id, page_key, ident):
        """An explicit status check; the worker's verify path cannot submit."""
        if not result_checks_enabled(self.store.data_dir):
            raise ValueError('Kiểm tra trạng thái đang tạm tắt; lịch đăng tiếp tục hoạt động.')
        row = self.store.get(ident)
        if (row['account_id'], row['page_key']) != (account_id, page_key) or not self.can_follow_result(row):
            raise ValueError('Chỉ kiểm tra lại đúng lượt đã gửi và đã dừng; không tạo lượt đăng mới.')
        if any(getattr(self.owner.studio, flag, False) is True for flag in ('closing', 'closed', 'restart_draining')):
            raise ValueError('Tèo đang đóng hoặc khởi động lại; đợi xong rồi kiểm tra kết quả.')
        guard = self.guard(ident)
        guard.__enter__()
        try:
            if self.store.get(ident) != row:
                raise ValueError('Lượt đã thay đổi; chọn lại trước khi kiểm tra.')
            self.verify_snapshot(row)
            self.guards[ident] = guard
            self.active.add(ident)
            self.submit(row, 'verify')
            return self.store.get(ident)['request_id']
        except BaseException:
            if ident in self.guards:
                self.fail(ident, 'Chưa nối lại được kiểm tra; lịch và lượt gửi cũ được giữ.')
            else:
                guard.__exit__(None, None, None)
            raise

    def can_inspect_result(self, row):
        """A passive eligibility check; it never owns or starts a publishing job."""
        try:
            if (not result_checks_enabled(self.store.data_dir) or self.closed
                    or not isinstance(row, dict) or row.get('id') in self.guards):
                return False
            studio = self.owner.studio
            if any(getattr(studio, name, False) is True for name in ('closing', 'closed', 'restart_draining')):
                return False
            session = self.owner.sessions.get(row.get('account_id'))
            if session and (getattr(session, 'loading_pages', False) or any(
                    item.get('state') in {'queued', 'running', 'opening'}
                    for item in getattr(session, 'post_actions', {}).values())):
                return False
            from teodub.page_browser_reconcile import can_inspect
            return can_inspect(self.store, row)
        except (OSError, ValueError, KeyError, TypeError, ImportError):
            return False

    def reconcile_preview(self, account_id, page_key, ident):
        if not result_checks_enabled(self.store.data_dir):
            raise ValueError('Kiểm tra trạng thái đang tạm tắt; lịch đăng tiếp tục hoạt động.')
        from teodub.page_browser_reconcile import preview
        if self.closed or ident in self.guards:
            raise ValueError('Lượt này còn đang xử lý; đợi xong trước khi kiểm tra lại kết quả.')
        return preview(self.store, account_id, page_key, ident)

    def inspect_result(self, account_id, page_key, ident, *, result_url='', expected_reconcile,
                       confirmed_attempt=False):
        """Queue an explicit read-only inspection, preserving the original attempt."""
        if not result_checks_enabled(self.store.data_dir):
            raise ValueError('Kiểm tra trạng thái đang tạm tắt; lịch đăng tiếp tục hoạt động.')
        studio = self.owner.studio
        if self.closed or any(getattr(studio, name, False) is True for name in ('closing', 'closed', 'restart_draining')):
            raise ValueError('Tèo đang đóng hoặc khởi động lại; chờ xong trước khi kiểm tra kết quả.')
        if confirmed_attempt is not True or not isinstance(result_url, str) or not isinstance(expected_reconcile, dict):
            raise ValueError('Xác nhận bài Facebook thuộc đúng lượt đã gửi trước khi kiểm tra.')
        if ident in self.guards:
            raise ValueError('Lượt này còn đang xử lý; không kiểm tra hoặc gửi lại song song.')
        with self.guard(ident):
            row = self.store.get(ident)
            if (row['account_id'], row['page_key']) != (account_id, page_key):
                raise ValueError('Lượt đã gửi không thuộc tài khoản/Page đang mở.')
            if row != expected_reconcile.get('schedule'):
                raise ValueError('Lượt đã thay đổi sau màn hình đối chiếu; đóng và mở lại để xem đúng lượt.')
            if not self.can_inspect_result(row):
                raise ValueError('Lượt chưa gửi hoặc tài khoản đang bận; chưa kiểm tra lại được kết quả.')
            from teodub.page_browser_reconcile import validate_current
            validate_current(self.store, expected_reconcile)
            session = self.owner.sessions.get(account_id)
            if (not session or not getattr(session, 'pending', False) or not getattr(session, 'opened', False)
                    or getattr(session, 'loading_pages', False)
                    or any(getattr(session, name, None) is not None and getattr(session, name).is_set()
                           for name in ('cancel', 'finished'))):
                raise ValueError('Mở Facebook của tài khoản này rồi kiểm tra lại; không cần tạo hoặc gửi lại bài.')
            request_id = uuid.uuid4().hex
            request = self.owner.submit_page_browser(account_id, page_key, row['post_id'], 'inspect_result',
                request_id=request_id, result_url=result_url.strip(), expected_reconcile=deepcopy(expected_reconcile),
                confirmed_attempt=True)
            if not request:
                raise ValueError(self.owner.status.get() or 'Chưa gửi được yêu cầu kiểm tra vào Chrome.')
            if request != request_id:
                raise ValueError('Mã phản hồi kiểm tra không khớp; xem kết quả trước, không tự gửi lại.')
            for old_id, scope in list(self.inspections.items()):
                old_session = self.owner.sessions.get(scope['account_id'])
                old_request = getattr(old_session, 'post_actions', {}).get(old_id) if old_session else None
                if not old_request or old_request.get('state') in {'done', 'error'}:
                    self.inspections.pop(old_id, None)
            self.inspections[request_id] = dict(account_id=account_id, page_key=page_key, post_id=row['post_id'])
            return request_id

    def create_new_attempt(self, account_id, page_key, ident, due_at, **approval):
        self._recovery_idle(account_id)
        if ident in self.active or ident in self.guards:
            raise ValueError('Lượt cũ còn đang xử lý; chờ kết thúc trước khi tạo lượt mới.')
        return self.store.create_new_attempt(account_id, page_key, ident, due_at, **approval)

    def start_now(self, account_id, page_key, ident, *, expected_schedule):
        """Explicit immediate posting uses the existing validation/prepare/publish pipeline."""
        studio = self.owner.studio
        if (self.closed or getattr(studio, 'closing', False) or getattr(studio, 'closed', False)
                or getattr(studio, 'restart_draining', False)):
            raise ValueError('Tèo đang đóng hoặc khởi động lại; đợi xong rồi bấm Đăng ngay.')
        row = self.store.get(ident)
        if (row['account_id'], row['page_key']) != (account_id, page_key):
            raise ValueError('Lịch không thuộc tài khoản/Page này.')
        session = self.owner.sessions.get(account_id)
        if (session and (session.loading_pages or any(request['state'] in {'queued', 'running'}
                for request in session.post_actions.values())) or any(
                self.store.get(key)['account_id'] == account_id and self.store.get(key)['state'] in {'preparing', 'submitting'}
                for key in self.guards)):
            raise ValueError('Tài khoản này đang xử lý một bài hoặc Load Page. Đợi xong rồi bấm Đăng ngay.')
        owned = ident in self.guards
        guard = self.guards.get(ident) if owned else self.guard(ident)
        if not owned:
            guard.__enter__()
        try:
            self.store.arm_now(account_id, page_key, ident, expected_schedule)
        except Exception:
            if not owned:
                guard.__exit__(None, None, None)
            raise
        self.guards[ident] = guard
        self.active.add(ident)
        self.last_tick = 0
        self.tick()
        return self.store.get(ident)

    def start_many(self, account_id, page_key, identifiers):
        """Explicitly arm this Page's selected future jobs; never replay attempted jobs."""
        results = []
        for ident in dict.fromkeys(identifiers):
            result = dict(id=ident, label=ident, outcome='skipped', message='')
            try:
                row = self.store.get(ident)
                if (row['account_id'], row['page_key']) != (account_id, page_key):
                    raise ValueError('Lịch không thuộc tài khoản/Page đang mở.')
                result['label'] = row['source']['label']
                if ident in self.active:
                    result.update(outcome='active', message='Lịch đã chạy; giữ nguyên.')
                else:
                    if row['attempted_at']:
                        raise ValueError('Đã có thao tác gửi; cần kiểm tra Facebook, không gửi lại.')
                    if row['state'] not in {'scheduled', 'paused', 'needs_attention'}:
                        raise ValueError('Không bật lại lịch đã hoàn tất, đã bỏ hoặc đang xử lý.')
                    if row['due_at'] <= time.time():
                        raise ValueError('Giờ đăng đã qua; cần Đổi giờ sang tương lai.')
                    self.start(ident)
                    result.update(outcome='started', message='Đã bật lịch lúc '+display_time(row['due_at'])+' (UTC+7).')
            except (OSError, ValueError, RuntimeError) as exc:
                result['message'] = str(exc)
            results.append(result)
        return results

    def pause(self, ident):
        self.queue_waits.pop(ident, None)
        self.queue_interrupted.pop(ident, None)
        row = self.store.get(ident)
        if ident not in self.guards:
            with self.guard(ident):
                return self.store.update(ident,expected={'scheduled','paused','armed','needs_attention'},
                    state='paused',message='Đã tạm dừng; không tự chạy khi mở lại ứng dụng.')
        self.active.discard(ident)
        self.cancel_request(row)
        self.store.update(ident,state='paused',message='Đã dừng lịch. Thao tác đã gửi tới Chrome có thể đang hoàn tất; kiểm tra trước khi thử lại.')
        if row['state'] not in {'preparing','submitting','verifying'}:
            self.release(ident)

    def fail(self, ident, message):
        try:
            self.cancel_request(self.store.get(ident))
            self.store.update(ident,state='needs_attention',message=str(message)[:10000])
        finally:
            self.release(ident)

    def cancel(self, ident):
        if ident in self.guards:
            row = self.store.cancel_unattempted(ident)
            self.release(ident)
            return row
        with self.guard(ident):
            return self.store.cancel_unattempted(ident)

    def cancel_request(self, row):
        session = self.owner.sessions.get(row['account_id'])
        request = session.post_actions.get(row['request_id']) if session and row['request_id'] else None
        if request and request.get('cancel') is not None:
            request['cancel'].set()

    def verify_snapshot(self, row):
        post = self.store.activity.post(row['account_id'],row['page_key'],row['post_id'])
        page = self.store.activity.page(row['account_id'],row['page_key'])
        if post['archived'] or any(post.get(key)!=value for key,value in row['snapshot'].items()):
            raise ValueError('Nội dung hoặc tệp của bài đã khác lịch được duyệt; chưa đăng.')
        if row['phase']=='validate' and post['state'] not in {'draft','failed'}:
            raise ValueError('Bản nháp đã được xử lý từ cửa sổ Page; kiểm tra Facebook trước, lịch không tự gửi lại.')
        if (page['url']!=post['target_url'] or page['name']!=post['target_name'] or
                page.get('page_id','')!=post['target_id']):
            raise ValueError('Page đã đổi sau khi duyệt lịch; chưa đăng.')
        self.store.activity.verify_media(post)
        return post

    def verify_review(self, row, post):
        review = post.get('browser_review') or {}
        fields = ('account_id','page_key','post_id','target_name','target_url','target_id','browser_finish_mode')
        expected = dict(account_id=row['account_id'],page_key=row['page_key'],post_id=row['post_id'],**row['snapshot'])
        body = row['body'].replace('\r\n','\n').replace('\r','\n').strip()
        files = ([] if row['source'].get('kind') == 'text' else
                 [dict(saved,sha256=row['source']['sha256']) for saved in row['snapshot']['media']])
        if (post['state']!='review' or post.get('manual_url') or
                any(review.get(key)!=expected[key] for key in fields) or
                review.get('body')!=body or review.get('media')!=files):
            raise ValueError('Bản xem trước thực tế không khớp Page, nội dung hoặc tệp đã duyệt; lịch dừng, chưa bấm Đăng.')

    def validate_video(self, row):
        try:
            source = row['source']
            if source.get('kind') == 'text':
                post = self.verify_snapshot(row)
                if post['kind'] != 'text' or post['media'] or source.get('key') != 'post:'+post['id']:
                    raise ValueError('Bài chữ không còn khớp lịch đã duyệt; chưa đăng.')
            else:
                if campaign_video_source(self.store.data_dir,source['campaign'],source['number'])!=source:
                    raise ValueError('Thành phẩm đã thay đổi sau khi duyệt lịch.')
                if hash_file(source['path'])!=source['sha256']:
                    raise ValueError('Video không còn khớp thành phẩm Tèo đã duyệt; chưa tải lên Facebook.')
            self.validation.put((row['id'],None))
        except Exception as exc:
            self.validation.put((row['id'],str(exc)))

    def submit(self, row, operation):
        if operation == 'verify' and not result_checks_enabled(self.store.data_dir):
            raise ValueError('Kiểm tra trạng thái đang tạm tắt; chưa xếp lượt xác minh.')
        # Reserve the durable request before the worker can perform any action.
        request_id = uuid.uuid4().hex
        self.store.update(row['id'],state={'prepare':'preparing','publish':'submitting','verify':'verifying'}[operation],
            phase=operation,request_id=request_id,attempted_at=row['attempted_at'] or now(),
            message={'prepare':'Đang chuẩn bị bài trong Chrome.', 'publish':'Đang gửi bài theo lịch đã duyệt.',
                     'verify':'Đã gửi — đang nối lại xác minh kết quả; không tải hoặc đăng lại.'}[operation])
        request = self.owner.submit_page_browser(row['account_id'],row['page_key'],row['post_id'],operation,
                                               request_id=request_id)
        if not request:
            if operation=='prepare':
                self.store.update(row['id'],attempted_at='',request_id='')
            raise ValueError(self.owner.status.get() or 'Chưa gửi được yêu cầu vào Chrome; lịch đã dừng.')
        if request != request_id:
            raise ValueError('Mã phản hồi Chrome không khớp lượt đã lưu; kiểm tra Page, không tự gửi lại.')

    def _observe_queue(self, clock):
        if (self.queue_last_observed is not None
                and clock-self.queue_last_observed > 120):
            self.queue_waits.clear()
            self.queue_starts.clear()
            wall = time.time()
            for ident in self.guards:
                row = self.store.get(ident)
                if ident in self.active and row['state'] == 'armed' and row['due_at'] <= wall:
                    self.queue_interrupted[ident] = self._queue_binding(row)
        self.queue_last_observed = clock
        for ident, binding in list(self.queue_interrupted.items()):
            row = self.store.get(ident) if ident in self.guards and ident in self.active else None
            if not row or row['state'] != 'armed' or self._queue_binding(row) != binding:
                self.queue_interrupted.pop(ident, None)
        for ident, witness in list(self.queue_waits.items()):
            row = self.store.get(ident) if ident in self.guards and ident in self.active else None
            if (not row or row['state'] != 'armed' or row['account_id'] != witness['account_id']
                    or row['due_at'] != witness['due_at'] or row['attempted_at'] or row['request_id']
                    or self._queue_binding(row) != witness['binding']):
                self.queue_waits.pop(ident, None)

    @staticmethod
    def _queue_binding(row):
        return {key: row[key] for key in
            ('account_id', 'page_key', 'post_id', 'source_key', 'source', 'body', 'snapshot', 'due_at')}

    def _queue_blockers(self, row):
        """Re-read ownership after replies release guards in this same tick."""
        blockers = []
        order = (row['due_at'], row['created_at'], row['id'])
        for ident in list(self.guards):
            if ident == row['id']:
                continue
            other = self.store.get(ident)
            if other['account_id'] != row['account_id']:
                continue
            if other['state'] in {'preparing', 'submitting'} or (
                    ident in self.active and other['state'] == 'armed' and ident in self.queue_waits
                    and (other['due_at'], other['created_at'], ident) < order):
                blockers.append(other)
        return blockers

    def _queue_predecessors_done(self, row, witness):
        for ident, binding in witness['blockers'].items():
            previous = self.store.get(ident)
            if self._queue_binding(previous) != binding:
                raise ValueError('Lượt trước hoặc video đã thay đổi trong khi chờ. '
                    'Lịch này chưa tải/gửi; kiểm tra rồi đổi giờ và bật lại.')
            if (ident in self.guards and ident in self.active
                    and previous['account_id'] == row['account_id']
                    and (previous['state'] in {'preparing', 'submitting'}
                         or previous['state'] == 'armed' and ident in self.queue_waits)):
                continue
            # Receipt collection is independent from the next approved input.
            if previous['account_id'] != row['account_id']:
                raise ValueError('Tài khoản lượt trước đã đổi; kiểm tra lại lịch đã duyệt.')

    def _wait_for_account(self, row, wall, clock):
        blockers = self._queue_blockers(row)
        witness = self.queue_waits.get(row['id'])
        if (witness is None and wall-row['due_at'] <= 120 and blockers
                and row['id'] not in self.queue_interrupted
                and not any(row[key] for key in ('attempted_at', 'request_id', 'phase', 'finished_at'))):
            witness = dict(account_id=row['account_id'], due_at=row['due_at'], since=clock,
                binding=self._queue_binding(row), blockers={})
            self.queue_waits[row['id']] = witness
        if witness is not None:
            self._queue_predecessors_done(row, witness)
            for item in blockers:
                witness['blockers'].setdefault(item['id'], self._queue_binding(item))
            self._queue_predecessors_done(row, witness)
        if wall-row['due_at'] > 120 and witness is None:
            raise ValueError('Đã lỡ giờ đăng hơn 2 phút. Đổi giờ mới; không tự đăng dồn bài.')
        if blockers:
            if witness is not None:
                waited = max(0, int(clock-witness['since']))
                message = ('Đang chờ lượt tải/gửi trước cùng tài khoản kết thúc '
                    f'(đã chờ {waited//60} phút {waited%60} giây). Chưa tải/gửi video này; giữ giờ đã chọn.')
                if row['message'] != message:
                    self.store.update(row['id'], expected={'armed'}, message=message)
            return True
        if witness is not None:
            previous = self.queue_starts.get(row['account_id'])
            if previous and previous['id'] != row['id']:
                ready_at = previous['at']+max(0, row['due_at']-previous['due_at'])
                remaining = max(0, math.ceil(ready_at-clock))
                if remaining:
                    message = ('Luồng tải/gửi đã rảnh; đang giữ khoảng cách giữa các lịch. '
                        'Chờ đến '+display_time(wall+remaining)+f' (UTC+7; còn {remaining//60} phút '
                        f'{remaining%60} giây). Chưa tải/gửi video này; giữ giờ đã chọn.')
                    if row['message'] != message:
                        self.store.update(row['id'], expected={'armed'}, message=message)
                    return True
        return False

    def tick(self):
        clock = time.monotonic()
        if self.closed or clock-self.last_tick<1:
            return
        self.last_tick = clock
        try:
            self._observe_queue(clock)
            while True:
                try:
                    ident,error = self.validation.get_nowait()
                except queue.Empty:
                    break
                if ident not in self.guards:
                    continue
                row = self.store.get(ident)
                if ident not in self.active:
                    self.release(ident)
                    continue
                try:
                    if error:
                        raise ValueError(error)
                    self.verify_snapshot(row)
                    self.submit(row,'prepare')
                except (OSError,ValueError) as exc:
                    self.fail(ident,exc)
            owned_rows = sorted((self.store.get(ident) for ident in list(self.guards)),
                                key=lambda row:(row['due_at'],row['created_at'],row['id']))
            for row in owned_rows:
                ident = row['id']
                if row['phase'] in {'publish', 'verify'}:
                    # The durable receipt wins even if the UI missed the last
                    # worker event or a timeout raced with its atomic commit.
                    post = self.store.activity.post(row['account_id'], row['page_key'], row['post_id'])
                    receipt = post.get('browser_receipt') or {}
                    if (post['state'] == 'published' and post.get('external_url')
                            and receipt.get('source') == 'visible_dom' and receipt.get('url') == post['external_url']
                            and not any(post.get(key) != value for key, value in row['snapshot'].items())):
                        self.store.update(ident, state='published', finished_at=row['finished_at'] or now(), message=post['message'])
                        self.release(ident)
                        continue
                if row['request_id']:
                    session = self.owner.sessions.get(row['account_id'])
                    reply = session.post_actions.get(row['request_id']) if session else None
                    if (not result_checks_enabled(self.store.data_dir)
                            and row['phase'] in {'publish', 'verify'}):
                        post = self.store.activity.post(row['account_id'], row['page_key'], row['post_id'])
                        if self._is_submitted_attempt(row, post):
                            if reply and (reply.get('state') == 'verifying'
                                    or reply.get('operation') in {'verify', 'inspect_result'}):
                                self.cancel_request(row)
                            self._finish_without_verification(row)
                            continue
                    if not reply:
                        self.fail(ident,'Không còn phản hồi của phiên Chrome; kiểm tra Page trước, không tự gửi lại.')
                        continue
                    if reply['state']=='error':
                        self.fail(ident,reply['message'])
                    elif reply['state'] == 'verifying' and row['phase'] in {'publish', 'verify'}:
                        post = self.store.activity.post(row['account_id'], row['page_key'], row['post_id'])
                        if post['state'] != 'processing':
                            self.fail(ident, 'Chưa lưu được trạng thái đã gửi; kiểm tra Chrome trước khi thao tác lại.')
                        else:
                            self.store.update(ident, state='verifying',
                                message=str(reply.get('message') or 'Đã gửi — đang xác minh kết quả.')[:10000])
                    elif reply['state'] in {'queued', 'running'} and row['state'] in {'preparing', 'submitting', 'verifying'}:
                        message = str(reply.get('message') or row['message'])[:10000]
                        if message != row['message']:
                            self.store.update(ident, message=message)
                    elif reply['state']=='done':
                        result = reply.get('result') or {}
                        if row['phase']=='prepare' and result.get('state')=='review' and ident in self.active:
                            try:
                                post = self.verify_snapshot(row)
                                self.verify_review(row,post)
                                self.submit(row,'publish')
                            except (OSError,ValueError) as exc:
                                self.fail(ident,exc)
                        elif (row['phase'] in {'publish', 'verify'} and result.get('verification_paused') is True
                                and not result_checks_enabled(self.store.data_dir)):
                            post = self.store.activity.post(row['account_id'], row['page_key'], row['post_id'])
                            if self._is_submitted_attempt(row, post):
                                self._finish_without_verification(row)
                            else:
                                self.fail(ident, 'Chưa có bằng chứng lượt đã bấm gửi; giữ trạng thái để kiểm tra.')
                        elif row['phase'] in {'publish', 'verify'} and result.get('state')=='published':
                            post = self.store.activity.post(row['account_id'],row['page_key'],row['post_id'])
                            receipt = post.get('browser_receipt') or {}
                            if (post['state']!='published' or not post['external_url'] or
                                    receipt.get('source')!='visible_dom' or receipt.get('url')!=post['external_url']):
                                self.fail(ident,'Chưa có bằng chứng/link bài được lưu từ Chrome; kiểm tra Page, không tự gửi lại.')
                            else:
                                self.store.update(ident,state='published',finished_at=now(),message=post['message'])
                                self.release(ident)
                        else:
                            self.fail(ident,result.get('message') or 'Thao tác đã dừng hoặc kết quả chưa rõ. Kiểm tra trên Page; không tự gửi lại.')
                    continue
                if row['state'] == 'verifying' and row['phase'] == 'verify':
                    if not result_checks_enabled(self.store.data_dir):
                        post = self.store.activity.post(row['account_id'], row['page_key'], row['post_id'])
                        if self._is_submitted_attempt(row, post):
                            self._finish_without_verification(row)
                        else:
                            self.fail(ident, 'Đã tạm tắt kiểm tra trạng thái; chưa đủ bằng chứng lượt đã gửi.')
                        continue
                    session = self.owner.sessions.get(row['account_id'])
                    if session and (session.loading_pages or any(
                            item['state'] in {'queued', 'running'} for item in session.post_actions.values())):
                        continue
                    try:
                        self.verify_snapshot(row)
                        self.submit(row, 'verify')
                    except (OSError, ValueError) as exc:
                        self.fail(ident, exc)
                    continue
                if row['state']=='armed' and row['due_at']<=time.time():
                    if getattr(self.owner.studio,'restart_draining',False):
                        continue
                    try:
                        if self._wait_for_account(row, time.time(), clock):
                            continue
                    except (OSError, ValueError) as exc:
                        self.fail(ident, exc)
                        continue
                    session = self.owner.sessions.get(row['account_id'])
                    if session and (session.loading_pages or any(x['state'] in {'queued','running'} for x in session.post_actions.values())):
                        continue
                    self.store.update(ident,expected={'armed'},state='preparing',phase='validate',message='Đang kiểm tra nội dung đã duyệt trước khi đăng.')
                    try:
                        started_at = time.monotonic()
                        threading.Thread(target=self.validate_video,args=(row,),daemon=True).start()
                        self.queue_starts[row['account_id']] = dict(id=ident, due_at=row['due_at'], at=started_at)
                        self.queue_waits.pop(ident, None)
                        self.queue_interrupted.pop(ident, None)
                    except Exception as exc:
                        self.fail(ident,'Chưa mở được bước kiểm tra video: '+str(exc))
        except (OSError,ValueError) as exc:
            self.owner.status.set('Lịch đăng cần kiểm tra: '+str(exc))

    def close(self):
        self.closed = True
        for request_id, scope in list(self.inspections.items()):
            session = self.owner.sessions.get(scope['account_id'])
            request = getattr(session, 'post_actions', {}).get(request_id) if session else None
            if (request and request.get('operation') == 'inspect_result'
                    and request.get('post_id') == scope['post_id']
                    and request.get('page_key') == scope['page_key']
                    and request.get('state') in {'queued', 'running'}
                    and request.get('cancel') is not None):
                request['cancel'].set()
        self.inspections.clear()
        self.queue_waits.clear()
        self.queue_starts.clear()
        self.queue_interrupted.clear()
        self.queue_last_observed = None
        for ident in list(self.guards):
            try:
                row = self.store.get(ident)
                self.cancel_request(row)
                self.store.update(ident,state='needs_attention' if row['attempted_at'] else 'paused',
                    message='Tèo đã đóng; chưa tự chạy lại. Kiểm tra Page nếu đã có lượt thao tác.')
            except (OSError,ValueError):
                pass
            self.release(ident)
