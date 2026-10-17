"""Page notes, post drafts and observed results. Never publishes on startup."""
from contextlib import contextmanager
from datetime import datetime
import json
import math
from pathlib import Path
import re
import sqlite3
from urllib.parse import parse_qs, urlsplit
import uuid

from teodub.facebook_accounts import FacebookAccounts, facebook_post_url, facebook_result_url, facebook_url


POST_STATES = {'draft', 'preparing', 'review', 'submitting', 'processing', 'published', 'failed', 'unknown'}
BUSY_STATES = {'preparing', 'review', 'submitting', 'processing', 'unknown'}
DELIVERY_MODES = {'api', 'browser'}


def now():
    return datetime.now().astimezone().isoformat(timespec='seconds')


class PageActivity:
    def __init__(self, data_dir):
        self.accounts = FacebookAccounts(data_dir)
        self.path = self.accounts.directory/'activity.sqlite3'

    @contextmanager
    def database(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=3)
        conn.row_factory = sqlite3.Row
        try:
            conn.executescript('''
                CREATE TABLE IF NOT EXISTS details (
                  page_key TEXT PRIMARY KEY, account_id TEXT NOT NULL,
                  topic TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS posts (
                  id TEXT PRIMARY KEY, account_id TEXT NOT NULL, page_key TEXT NOT NULL,
                  target_name TEXT NOT NULL, target_url TEXT NOT NULL, target_id TEXT NOT NULL,
                  body TEXT NOT NULL, kind TEXT NOT NULL, media TEXT NOT NULL,
                  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, state TEXT NOT NULL,
                  message TEXT NOT NULL DEFAULT '', external_url TEXT NOT NULL DEFAULT '',
                  external_id TEXT NOT NULL DEFAULT '', published_at TEXT NOT NULL DEFAULT '',
                  attempt INTEGER NOT NULL DEFAULT 0, archived INTEGER NOT NULL DEFAULT 0);
                CREATE INDEX IF NOT EXISTS posts_page ON posts(page_key, created_at);
                CREATE TABLE IF NOT EXISTS events (
                  id INTEGER PRIMARY KEY, post_id TEXT NOT NULL, at TEXT NOT NULL,
                  state TEXT NOT NULL, message TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS observations (
                  id INTEGER PRIMARY KEY, page_key TEXT NOT NULL, account_id TEXT NOT NULL,
                  at TEXT NOT NULL, source TEXT NOT NULL, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS remote_posts (
                  page_key TEXT NOT NULL, account_id TEXT NOT NULL, external_id TEXT NOT NULL,
                  checked_at TEXT NOT NULL, data TEXT NOT NULL, PRIMARY KEY(page_key,external_id));
            ''')
            columns = {row['name'] for row in conn.execute('PRAGMA table_info(posts)')}
            for name, default in [('remote_assets', '[]'), ('metrics', '{}')]:
                if name not in columns:
                    conn.execute(f"ALTER TABLE posts ADD COLUMN {name} TEXT NOT NULL DEFAULT '{default}'")
            # Old drafts used Meta API; never silently route them to browser posting.
            for name, default in [('delivery_mode', 'api'), ('browser_finish_mode', 'manual'), ('browser_review', '{}'),
                                  ('browser_receipt', '{}'), ('browser_tracking', '{}'), ('manual_url', '')]:
                if name not in columns:
                    conn.execute(f"ALTER TABLE posts ADD COLUMN {name} TEXT NOT NULL DEFAULT '{default}'")
            conn.execute('CREATE TABLE IF NOT EXISTS connections '
                '(page_key TEXT PRIMARY KEY, account_id TEXT NOT NULL, page_id TEXT NOT NULL, '
                'api_version TEXT NOT NULL, credential_ref TEXT NOT NULL, name TEXT NOT NULL, '
                'checked_at TEXT NOT NULL, status TEXT NOT NULL, message TEXT NOT NULL)')
            conn.commit()
            with conn:
                yield conn
        except sqlite3.Error as exc:
            raise ValueError('Chưa lưu/đọc được hoạt động Page. Dữ liệu cũ được giữ. '+str(exc)) from None
        finally:
            conn.close()

    def page(self, account_id, page_key):
        row = next((p for p in self.accounts.load()['pages']
                    if p['id'] == page_key and p['account_id'] == account_id), None)
        if row is None:
            raise ValueError('Page không còn trong tài khoản này. Bấm Làm mới danh sách Page.')
        return row

    def details(self, account_id, page_key):
        self.page(account_id, page_key)
        with self.database() as conn:
            row = conn.execute('SELECT * FROM details WHERE page_key=? AND account_id=?',
                               (page_key, account_id)).fetchone()
        return dict(row) if row else dict(topic='', notes='')

    def save_details(self, account_id, page_key, topic, notes):
        self.page(account_id, page_key)
        if len(topic) > 300 or len(notes) > 10000:
            raise ValueError('Chủ đề tối đa 300 ký tự; ghi chú tối đa 10.000 ký tự.')
        with self.database() as conn:
            conn.execute('INSERT INTO details VALUES (?,?,?,?,?) ON CONFLICT(page_key) DO UPDATE SET '
                         'topic=excluded.topic,notes=excluded.notes,updated_at=excluded.updated_at',
                         (page_key, account_id, topic.strip(), notes.strip(), now()))

    @staticmethod
    def media_files(paths):
        paths = list(paths)
        if len(paths) > 10:
            raise ValueError('Một bài tối đa 10 ảnh hoặc một video.')
        files, kinds = [], set()
        for value in paths:
            path = Path(value).expanduser().resolve()
            suffix = path.suffix.lower()
            kind = ('image' if suffix in {'.jpg', '.jpeg', '.png', '.webp'} else
                    'video' if suffix in {'.mp4', '.mov'} else '')
            if not kind or not path.is_file():
                raise ValueError('Chọn ảnh JPG/PNG/WebP hoặc video MP4/MOV đang có trên máy.')
            stat = path.stat()
            if stat.st_size <= 0:
                raise ValueError('Tệp đính kèm đang rỗng: '+path.name)
            kinds.add(kind)
            files.append(dict(path=str(path), size=stat.st_size, mtime_ns=stat.st_mtime_ns))
        if len(kinds) > 1 or 'video' in kinds and len(files) != 1:
            raise ValueError('Không trộn ảnh với video; mỗi bài video chỉ chọn một tệp.')
        return next(iter(kinds), 'text'), files

    @staticmethod
    def verify_media(post):
        for saved in post['media']:
            path = Path(saved['path'])
            if not path.is_file():
                raise ValueError('Tệp đính kèm không còn: '+str(path))
            stat = path.stat()
            if stat.st_size != saved['size'] or stat.st_mtime_ns != saved['mtime_ns']:
                raise ValueError('Tệp đính kèm đã thay đổi sau khi lưu bài. Sửa và lưu lại bản nháp trước khi đăng.')

    def save_draft(self, account_id, page_key, body, paths=(), *, post_id=None, delivery_mode=None,
                   browser_finish_mode=None):
        target = self.page(account_id, page_key)
        if delivery_mode is not None and delivery_mode not in DELIVERY_MODES:
            raise ValueError('Chọn cách đăng qua API hoặc trình duyệt.')
        if browser_finish_mode is not None and browser_finish_mode not in {'manual', 'tool'}:
            raise ValueError('Chọn tự bấm Đăng trong Chrome hoặc Tèo bấm sau khi duyệt.')
        body = body.strip()
        if len(body) > 60000:
            raise ValueError('Nội dung quá dài; tối đa 60.000 ký tự.')
        kind, files = self.media_files(paths)
        if not body and not files:
            raise ValueError('Nhập nội dung hoặc chọn ảnh/video trước khi lưu bài.')
        timestamp = now()
        with self.database() as conn:
            conn.execute('BEGIN IMMEDIATE')
            if post_id:
                old = self._post(conn, account_id, page_key, post_id)
                if old['state'] not in {'draft', 'failed'} or old['archived'] or old.get('browser_tracking'):
                    raise ValueError('Chỉ sửa bản nháp hoặc bài lỗi trước khi gửi. Bài đã gửi được giữ để đối chiếu.')
                mode = delivery_mode or old['delivery_mode']
                finish_mode = browser_finish_mode or old['browser_finish_mode']
                if old['remote_assets'] and mode != old['delivery_mode']:
                    raise ValueError('Bài đã tải tệp qua API; tạo bản nháp mới để đổi cách đăng.')
                assets = old['remote_assets'] if files == old['media'] else []
                conn.execute("UPDATE posts SET body=?,kind=?,media=?,updated_at=?,state=?,message=?,remote_assets=?,"
                             "delivery_mode=?,browser_finish_mode=?,browser_review='{}',browser_receipt='{}',browser_tracking='{}',manual_url='' WHERE id=?",
                             (body, kind, json.dumps(files, ensure_ascii=False), timestamp, 'draft', '', json.dumps(assets), mode, finish_mode, post_id))
            else:
                post_id = uuid.uuid4().hex
                conn.execute('INSERT INTO posts '
                    '(id,account_id,page_key,target_name,target_url,target_id,body,kind,media,created_at,updated_at,state,delivery_mode,browser_finish_mode) '
                    'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (post_id, account_id, page_key, target['name'], target['url'], target.get('page_id',''),
                     body, kind, json.dumps(files, ensure_ascii=False), timestamp, timestamp, 'draft', delivery_mode or 'api', browser_finish_mode or 'manual'))
            self._event(conn, post_id, 'draft', 'Đã lưu bản nháp; chưa gửi lên Facebook.')
        return self.post(account_id, page_key, post_id)

    @staticmethod
    def _post(conn, account_id, page_key, post_id):
        row = conn.execute('SELECT * FROM posts WHERE id=? AND account_id=? AND page_key=?',
                           (post_id, account_id, page_key)).fetchone()
        if row is None:
            raise ValueError('Không còn bài viết này trong Page đã chọn.')
        result = dict(row)
        result['media'] = json.loads(result['media'])
        result['remote_assets'] = json.loads(result['remote_assets'])
        result['metrics'] = json.loads(result['metrics'])
        result['browser_review'] = json.loads(result['browser_review'])
        result['browser_receipt'] = json.loads(result['browser_receipt'])
        result['browser_tracking'] = json.loads(result['browser_tracking'])
        return result

    def save_browser_review(self, account_id, page_key, post_id, snapshot):
        """Commit exactly one immutable, observed composer preview before publication."""
        self.page(account_id, page_key)
        with self.database() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = self._post(conn, account_id, page_key, post_id)
            if row['delivery_mode'] != 'browser' or row['state'] != 'preparing' or row['archived'] or row['browser_review']:
                raise ValueError('Bản nháp không còn ở bước chuẩn bị trình duyệt; không thay bản xem trước.')
            message = 'Đã chuẩn bị trong Chrome. Xem trước nội dung thực tế rồi bấm Đăng bài đã duyệt nếu đồng ý.'
            conn.execute('UPDATE posts SET browser_review=?,state=?,message=?,updated_at=? WHERE id=?',
                         (json.dumps(snapshot, ensure_ascii=False), 'review', message, now(), post_id))
            self._event(conn, post_id, 'review', message)
        return self.post(account_id, page_key, post_id)

    def record_browser_receipt(self, account_id, page_key, post_id, receipt):
        with self.database() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = self._post(conn, account_id, page_key, post_id)
            if row['delivery_mode'] != 'browser' or row['state'] not in {'submitting', 'processing', 'unknown', 'published'}:
                raise ValueError('Bài chưa ở bước ghi nhận kết quả trình duyệt.')
            url = receipt.get('url') if isinstance(receipt, dict) else None
            if url:
                url = facebook_result_url(url)
                receipt = dict(receipt, url=url)
                for other in conn.execute('SELECT id,external_url,browser_receipt FROM posts WHERE id!=?', (post_id,)):
                    claimed = json.loads(other['browser_receipt'])
                    def clean(value):
                        try:
                            return facebook_result_url(value) if value else ''
                        except ValueError:
                            return ''
                    if clean(other['external_url']) == url or clean(claimed.get('url')) == url:
                        raise ValueError('Link kết quả này đã thuộc lượt đăng khác; chưa xác nhận nhầm hai video.')
                if row['browser_receipt'].get('url') not in (None, '', url):
                    raise ValueError('Lượt này đã có bằng chứng khác; giữ kết quả đã lưu để kiểm tra.')
            conn.execute('UPDATE posts SET browser_receipt=?,updated_at=? WHERE id=?',
                         (json.dumps(receipt, ensure_ascii=False), now(), post_id))

    def save_browser_tracking(self, account_id, page_key, post_id, tracking):
        """Persist observation-only recovery data, separately from a receipt."""
        finite = lambda value: isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0
        if (not isinstance(tracking, dict) or type(tracking.get('version')) is not int or tracking.get('version') != 1
                or not finite(tracking.get('submitted_at')) or not finite(tracking.get('deadline_at'))
                or tracking['deadline_at'] < tracking['submitted_at']
                or not isinstance(tracking.get('baseline'), dict)
                or not isinstance(tracking.get('review'), dict) or not tracking['review']
                or not isinstance(tracking.get('references', []), list)):
            raise ValueError('Dữ liệu theo dõi bài đã gửi không hợp lệ.')
        with self.database() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = self._post(conn, account_id, page_key, post_id)
            if (row['delivery_mode'] != 'browser' or row['archived']
                    or row['state'] not in {'submitting', 'processing', 'unknown'}
                    or tracking.get('post_id') != post_id
                    or tracking.get('account_id') != account_id or tracking.get('page_key') != page_key
                    or tracking.get('review') != row['browser_review']):
                raise ValueError('Lượt gửi đã thay đổi; không thay dữ liệu theo dõi kết quả.')
            previous = row.get('browser_tracking') or {}
            if previous and any(previous.get(key) != tracking.get(key) for key in
                    ('account_id', 'page_key', 'post_id', 'submitted_at', 'review', 'baseline', 'deadline_at')):
                raise ValueError('Không thay thế danh tính hoặc thời điểm của lượt đã gửi.')
            conn.execute('UPDATE posts SET browser_tracking=?,updated_at=? WHERE id=?',
                         (json.dumps(tracking, ensure_ascii=False), now(), post_id))

    def confirm_browser_publication(self, account_id, page_key, post_id, receipt, *, expected_review):
        """Claim one exact result and publish its status in the same transaction."""
        receipt = dict(receipt, url=facebook_result_url(receipt['url']))
        if receipt.get('source') != 'visible_dom':
            raise ValueError('Chỉ xác nhận khi đã đối chiếu bằng chứng thực tế trên Facebook.')
        target = self.page(account_id, page_key)
        with self.database() as conn:
            conn.execute('BEGIN IMMEDIATE')
            post = self._post(conn, account_id, page_key, post_id)
            if (post['state'] != 'processing' or post['archived'] or post['delivery_mode'] != 'browser'
                    or post['browser_review'] != expected_review
                    or post['target_id'] != target.get('page_id')
                    or post['target_url'] != target['url'] or post['target_name'] != target['name']
                    or post['body'].strip() != expected_review.get('body', '').strip()):
                raise ValueError('Lượt gửi hoặc Page đã thay đổi; chưa xác nhận kết quả.')
            for other in conn.execute('SELECT id,external_url,browser_receipt FROM posts WHERE id!=?', (post_id,)):
                claimed = json.loads(other['browser_receipt'])
                for value in (other['external_url'], claimed.get('url')):
                    try:
                        same = bool(value) and facebook_result_url(value) == receipt['url']
                    except ValueError:
                        same = False
                    if same:
                        raise ValueError('Kết quả đã thuộc lượt đăng khác; không gắn chung hai video.')
            if post['browser_receipt'].get('url') not in (None, '', receipt['url']):
                raise ValueError('Lượt này đã có bằng chứng khác; giữ nguyên để đối chiếu.')
            timestamp = now()
            message = 'Đã xác nhận bài thực tế khớp Page, caption và video trên Facebook.'
            conn.execute("UPDATE posts SET state='published',message=?,browser_receipt=?,external_url=?,"
                         'published_at=?,updated_at=? WHERE id=?',
                         (message, json.dumps(receipt, ensure_ascii=False), receipt['url'],
                          receipt.get('published_at', ''), timestamp, post_id))
            self._event(conn, post_id, 'published', message+' '+receipt['url'])
        return self.post(account_id, page_key, post_id)

    def record_manual_result(self, account_id, page_key, post_id, url):
        """A user report is evidence to inspect, never a verified publication."""
        self.page(account_id, page_key)
        clean = facebook_post_url(url)
        parsed = urlsplit(clean)
        bits = parsed.path.strip('/').split('/')
        query = parse_qs(parsed.query)
        permalink = ((len(bits) == 3 and bits[1] in {'posts', 'videos'} and bool(bits[2]))
                     or len(bits) == 2 and bits[0] in {'reel', 'reels'} and bool(bits[1])
                     or parsed.path in {'/permalink.php', '/story.php'} and bool(query.get('story_fbid')) and bool(query.get('id'))
                     or parsed.path == '/photo.php' and bool(query.get('fbid'))
                     or parsed.path.rstrip('/') in {'/watch', '/video.php'} and bool(query.get('v'))
                     or bool(re.fullmatch(r'/[0-9]+_[0-9]+', parsed.path)))
        if not permalink:
            raise ValueError('Cần link của bài/video thực tế; link Page hoặc nhóm không phải kết quả đăng.')
        with self.database() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = self._post(conn, account_id, page_key, post_id)
            if row['delivery_mode'] != 'browser' or row['state'] not in {'review', 'processing', 'unknown', 'published'}:
                raise ValueError('Chỉ ghi link người dùng kiểm tra cho bài đã chuẩn bị/gửi bằng trình duyệt.')
            message = 'Người dùng báo link bài: '+clean+'; Tèo chưa xác minh nội dung/quyền sở hữu hoặc kết quả đăng.'
            conn.execute('UPDATE posts SET manual_url=?,updated_at=? WHERE id=?', (clean, now(), post_id))
            self._event(conn, post_id, row['state'], message)
        return self.post(account_id, page_key, post_id)

    @staticmethod
    def _event(conn, post_id, state, message):
        conn.execute('INSERT INTO events(post_id,at,state,message) VALUES (?,?,?,?)',
                     (post_id, now(), state, message))

    def post(self, account_id, page_key, post_id):
        with self.database() as conn:
            return self._post(conn, account_id, page_key, post_id)

    def posts(self, account_id, page_key, *, include_archived=False):
        self.page(account_id, page_key)
        with self.database() as conn:
            rows = conn.execute('SELECT id FROM posts WHERE account_id=? AND page_key=? '
                +('' if include_archived else 'AND archived=0 ')+ 'ORDER BY created_at DESC,id DESC',
                (account_id, page_key)).fetchall()
            return [self._post(conn, account_id, page_key, row['id']) for row in rows]

    def transition(self, account_id, page_key, post_id, state, message, *, expected=None,
                   external_url='', external_id=''):
        if state not in POST_STATES:
            raise ValueError('Trạng thái bài viết không hợp lệ.')
        self.page(account_id, page_key)
        if external_url:
            external_url = facebook_result_url(external_url)
        with self.database() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = self._post(conn, account_id, page_key, post_id)
            if expected is not None and row['state'] not in expected:
                raise ValueError('Bài đang có thao tác khác hoặc đã gửi; không tự đăng lại.')
            if row['archived']:
                raise ValueError('Bài đã bỏ khỏi danh sách hoạt động.')
            if state == 'published' and not external_url:
                raise ValueError('Cần link bài thật để ghi đã đăng; không suy đoán từ nút bấm.')
            attempt = row['attempt'] + (state == 'preparing')
            conn.execute('UPDATE posts SET state=?,message=?,updated_at=?,attempt=?,external_url=?,external_id=?,published_at=? '
                'WHERE id=?', (state, str(message)[:10000], now(), attempt, external_url or row['external_url'],
                              external_id or row['external_id'], (row['published_at'] or now()) if state == 'published' else row['published_at'], post_id))
            self._event(conn, post_id, state, str(message)[:10000])
        return self.post(account_id, page_key, post_id)

    def archive(self, account_id, page_key, post_id):
        with self.database() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = self._post(conn, account_id, page_key, post_id)
            if row['state'] in BUSY_STATES:
                raise ValueError('Bài đang xử lý hoặc chưa rõ kết quả. Kiểm tra trên Facebook trước khi bỏ khỏi danh sách.')
            conn.execute('UPDATE posts SET archived=1,updated_at=? WHERE id=?', (now(), post_id))
            self._event(conn, post_id, row['state'], 'Đã bỏ khỏi danh sách trong Tèo; không xóa bài trên Facebook.')

    def events(self, account_id, page_key, post_id):
        with self.database() as conn:
            self._post(conn, account_id, page_key, post_id)
            return [dict(row) for row in conn.execute('SELECT at,state,message FROM events WHERE post_id=? ORDER BY id', (post_id,))]

    def observe(self, account_id, page_key, data, *, source='meta_api'):
        self.page(account_id, page_key)
        clean = {key: str(data[key])[:2000] for key in ('name','followers','likes','description','url','category')
                 if data.get(key) is not None and data.get(key) != ''}
        for key in ('followers', 'likes'):
            if key in clean:
                if isinstance(data[key], int) and not isinstance(data[key], bool) and data[key] >= 0:
                    clean[key] = data[key]
                else:
                    clean.pop(key)
        if clean.get('url'):
            clean['url'] = facebook_url(clean['url'], page=True)
        if not clean:
            raise ValueError('Chưa có thông tin Page để lưu; giữ lần ghi trước.')
        with self.database() as conn:
            conn.execute('INSERT INTO observations(page_key,account_id,at,source,data) VALUES (?,?,?,?,?)',
                         (page_key, account_id, now(), source, json.dumps(clean, ensure_ascii=False)))

    def observations(self, account_id, page_key):
        self.page(account_id, page_key)
        with self.database() as conn:
            rows = conn.execute('SELECT at,source,data FROM observations WHERE account_id=? AND page_key=? '
                                'ORDER BY id DESC LIMIT 100', (account_id, page_key)).fetchall()
        return [dict(at=row['at'], source=row['source'], **json.loads(row['data'])) for row in rows]

    def connection(self, account_id, page_key):
        self.page(account_id, page_key)
        with self.database() as conn:
            row = conn.execute('SELECT * FROM connections WHERE page_key=? AND account_id=?', (page_key, account_id)).fetchone()
        return dict(row) if row else None

    def save_connection(self, account_id, page_key, *, page_id, api_version, credential_ref, name):
        self.page(account_id, page_key)
        with self.database() as conn:
            conn.execute('INSERT INTO connections VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(page_key) DO UPDATE SET '
                'page_id=excluded.page_id,api_version=excluded.api_version,credential_ref=excluded.credential_ref,'
                'name=excluded.name,checked_at=excluded.checked_at,status=excluded.status,message=excluded.message',
                (page_key, account_id, page_id, api_version, credential_ref, name, now(), 'connected',
                 'Đã kiểm tra token thuộc đúng Page. Quyền đăng sẽ được Meta kiểm khi gửi bài.'))

    def connection_notice(self, account_id, page_key, status, message):
        with self.database() as conn:
            conn.execute('UPDATE connections SET checked_at=?,status=?,message=? WHERE account_id=? AND page_key=?',
                         (now(), status, str(message)[:2000], account_id, page_key))

    def disconnect(self, account_id, page_key):
        self.page(account_id, page_key)
        with self.database() as conn:
            conn.execute('DELETE FROM connections WHERE account_id=? AND page_key=?', (account_id, page_key))

    def record_assets(self, account_id, page_key, post_id, assets):
        with self.database() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = self._post(conn, account_id, page_key, post_id)
            if row['state'] not in {'preparing', 'submitting'}:
                raise ValueError('Không lưu lại tệp cho bài đã kết thúc lượt gửi.')
            conn.execute('UPDATE posts SET remote_assets=?,updated_at=? WHERE id=?', (json.dumps(assets), now(), post_id))

    def bind_target(self, account_id, page_key, post_id, page_id):
        if not str(page_id).isdigit():
            raise ValueError('Mã Page từ API chưa hợp lệ.')
        with self.database() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = self._post(conn, account_id, page_key, post_id)
            if row['state'] != 'preparing' or row['target_id'] and row['target_id'] != str(page_id):
                raise ValueError('Bài đã gắn với Page khác hoặc không còn ở bước chuẩn bị.')
            conn.execute('UPDATE posts SET target_id=? WHERE id=?', (str(page_id), post_id))

    def record_metrics(self, account_id, page_key, post_id, values):
        clean = {key: value for key, value in values.items() if key in {'likes','comments','shares','views','created_time'}
                 and (value is None or isinstance(value, (int, str)))}
        with self.database() as conn:
            row = self._post(conn, account_id, page_key, post_id)
            merged = dict(row['metrics'], **clean, checked_at=now())
            conn.execute('UPDATE posts SET metrics=? WHERE id=?', (json.dumps(merged), post_id))

    def save_remote_posts(self, account_id, page_key, posts):
        self.page(account_id, page_key)
        with self.database() as conn:
            for post in posts:
                post = dict(post)
                if post.get('url'):
                    post['url'] = facebook_post_url(post['url'])
                conn.execute('INSERT INTO remote_posts VALUES (?,?,?,?,?) ON CONFLICT(page_key,external_id) '
                    'DO UPDATE SET checked_at=excluded.checked_at,data=excluded.data',
                    (page_key, account_id, post['id'], now(), json.dumps(post, ensure_ascii=False)))

    def remote_posts(self, account_id, page_key):
        self.page(account_id, page_key)
        with self.database() as conn:
            rows = conn.execute('SELECT checked_at,data FROM remote_posts WHERE account_id=? AND page_key=? '
                                'ORDER BY checked_at DESC,external_id DESC LIMIT 100', (account_id, page_key)).fetchall()
        return [dict(checked_at=row['checked_at'], **json.loads(row['data'])) for row in rows]
