"""A local command mailbox; only the desktop's account browser may execute it.

The web process uses ``PageCommandStore(None, mailbox_root=...)``. It reads an
explicit, sanitized snapshot and queues immutable intents, never a Chrome
profile or Facebook credential. ``PageCommandBridge`` lives in FacebookTab and
uses its existing request-scoped queues on the Tk thread.
"""
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path
import queue
import re
import sqlite3
import threading
import time
import uuid

from teodub.common import atomic_json, hash_file
from teodub.facebook_accounts import FACEBOOK_HOME, FacebookAccounts, facebook_url
from teodub.page_activity import PageActivity
from teodub.pipeline import _job_lock


ACTIONS = {'open_account', 'load_pages', 'open_page', 'prepare_post', 'publish_post',
           'review_post', 'add_account', 'save_page', 'save_draft', 'schedule_post',
           'sync_content_plan', 'approve_content_plan', 'save_ai_policy', 'pause_ai_policy'}
STATES = {'queued', 'running', 'done', 'failed', 'unknown'}
MAX_AGE = 600
HEARTBEAT_AGE = 15


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _digest(value):
    return hashlib.sha256(_json(value).encode('utf-8')).hexdigest()


def _id(value, label='Mã'):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{32}', value):
        raise ValueError(label+' không hợp lệ.')
    return value


def _key(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 128 or any(ord(c) < 32 for c in value):
        raise ValueError('Mã yêu cầu phải có 1–128 ký tự, không có ký tự điều khiển.')
    return value


def _text(value, label, maximum, *, empty=False):
    if not isinstance(value, str) or len(value) > maximum or '\x00' in value or not empty and not value.strip():
        raise ValueError(label+' không hợp lệ.')
    return value.strip()


def _target(row):
    return {key: row.get(key, '') for key in ('name', 'url', 'page_id')}


def _post_binding(post):
    return {key: post[key] for key in ('account_id', 'page_key', 'id', 'target_name', 'target_url',
        'target_id', 'body', 'kind', 'media', 'delivery_mode', 'browser_finish_mode')}


def _public_media(rows):
    return [dict(name=Path(row['path']).name, size=row['size'],
                 **({'sha256': row['sha256']} if row.get('sha256') else {})) for row in rows]


def public_post(post):
    """Expose the concrete post/review without local filenames' parent paths."""
    result = {key: post.get(key, '') for key in ('id', 'account_id', 'page_key', 'body', 'kind', 'state',
        'message', 'target_name', 'target_url', 'target_id', 'delivery_mode', 'browser_finish_mode',
        'external_url', 'created_at', 'published_at')}
    result.update(media=_public_media(post['media']), post_key=_digest(_post_binding(post)), review_key='', review=None)
    review = post.get('browser_review') or {}
    if review:
        result['review_key'] = _digest(review)
        result['review'] = {key: review.get(key, '') for key in ('body', 'kind', 'target_name',
            'target_url', 'target_id', 'browser_finish_mode', 'prepared_at')}
        result['review']['media'] = _public_media(review.get('media', []))
    return result


def _payload(action, value):
    if action not in ACTIONS or not isinstance(value, dict):
        raise ValueError('Thao tác Page không được hỗ trợ.')
    allowed = {'add_account': {'name'}, 'save_page': {'account_id', 'name', 'url', 'page_id'},
        'save_draft': {'account_id', 'page_key', 'target', 'body', 'asset'},
        'open_account': {'account_id'}, 'load_pages': {'account_id'},
        'open_page': {'account_id', 'page_key', 'target'},
        'prepare_post': {'account_id', 'page_key', 'post_id', 'target', 'post_key'},
        'review_post': {'account_id', 'page_key', 'post_id', 'target', 'post_key'},
        'sync_content_plan': {'account_id', 'page_key', 'target', 'thread_id'},
        'approve_content_plan': {'account_id', 'page_key', 'target', 'plan_id', 'plan_digest'},
        'save_ai_policy': {'account_id','page_key','target','config','due_at','enabled','thread_id'},
        'pause_ai_policy': {'account_id','page_key','target'},
        'schedule_post': {'account_id', 'page_key', 'post_id', 'target', 'post_key', 'due_at'},
        'publish_post': {'account_id', 'page_key', 'post_id', 'target', 'post_key', 'review_key'}}[action]
    if set(value) != allowed:
        raise ValueError('Yêu cầu Page thiếu trường cần thiết hoặc có trường ngoài phạm vi.')
    row = deepcopy(value)
    if action=='save_ai_policy':
        from teodub.page_ai import validate_config
        validate_config(row['config'])
        if type(row['enabled']) is not bool:
            raise ValueError('Chọn bật/tắt lịch AI.')
    for key in ('account_id', 'page_key', 'post_id'):
        if key in row:
            _id(row[key], key)
    if 'name' in row:
        row['name'] = _text(row['name'], 'Tên', 150)
        if any(ord(c) < 32 for c in row['name']):
            raise ValueError('Tên phải trên một dòng.')
    if action == 'save_page':
        row['url'] = facebook_url(row['url'], page=True)
        if not isinstance(row['page_id'], str) or row['page_id'] and not re.fullmatch(r'[0-9]+', row['page_id']):
            raise ValueError('Page ID chỉ gồm chữ số.')
    if 'target' in row:
        target = row['target']
        if not isinstance(target, dict) or set(target) != {'name', 'url', 'page_id'}:
            raise ValueError('Thiếu bản chốt Page.')
        target['name'] = _text(target['name'], 'Tên Page', 150)
        target['url'] = facebook_url(target['url'], page=True)
        if not isinstance(target['page_id'], str) or target['page_id'] and not re.fullmatch(r'[0-9]+', target['page_id']):
            raise ValueError('Page ID không hợp lệ.')
    for key in ('post_key', 'review_key'):
        if key in row and (not isinstance(row[key], str) or not re.fullmatch(r'[0-9a-f]{64}', row[key])):
            raise ValueError('Bản chốt nội dung/xem trước không hợp lệ; làm mới trước khi tiếp tục.')
    if 'thread_id' in row:
        if not isinstance(row['thread_id'], str) or not re.fullmatch(
                r'[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}', row['thread_id']):
            raise ValueError('Mã cuộc trò chuyện Codex không hợp lệ.')
    if 'plan_id' in row:
        if not isinstance(row['plan_id'], str) or not re.fullmatch(
                r'[0-9a-f]{32}|[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}', row['plan_id']):
            raise ValueError('Mã kế hoạch không hợp lệ.')
        row['plan_id'] = str(uuid.UUID(row['plan_id']))
        if not isinstance(row['plan_digest'], str) or not re.fullmatch(r'[0-9a-f]{64}', row['plan_digest']):
            raise ValueError('Bản chốt kế hoạch không hợp lệ; làm mới và duyệt lại.')
    # Shape checking is stable across retries. The execution factory validates
    # future time; an exact idempotency retry after that time still returns its
    # existing command, instead of becoming a new attempt.
    if action in {'schedule_post','save_ai_policy'} and (type(row['due_at']) not in (float,int)
            or not math.isfinite(row['due_at']) or row['due_at'] <= 0):
        raise ValueError('Giờ đăng không hợp lệ.')
    if action == 'save_draft':
        row['body'] = _text(row['body'], 'Nội dung', 60000, empty=True)
        asset = row['asset']
        if asset is not None:
            if not isinstance(asset, dict) or set(asset) != {'path', 'sha256', 'size', 'mtime_ns', 'job_id'}:
                raise ValueError('Thành phẩm web không hợp lệ.')
            _id(asset['job_id'], 'Mã video web')
            if (not isinstance(asset['path'], str) or not Path(asset['path']).is_absolute()
                    or not re.fullmatch(r'[0-9a-f]{64}', str(asset['sha256']))
                    or type(asset['size']) is not int or asset['size'] <= 0
                    or type(asset['mtime_ns']) is not int or asset['mtime_ns'] <= 0):
                raise ValueError('Thiếu dữ liệu kiểm tra thành phẩm web.')
        if not row['body'] and asset is None:
            raise ValueError('Nhập nội dung hoặc chọn video thành phẩm.')
    if len(_json(row).encode('utf-8')) > 512000:
        raise ValueError('Yêu cầu Page quá lớn.')
    return row


class PageCommandStore:
    def __init__(self, data_dir=None, *, mailbox_root=None):
        if mailbox_root is None and data_dir is None:
            raise ValueError('Chưa chọn thư mục hộp lệnh Page.')
        self.data_dir = Path(data_dir).resolve() if data_dir is not None else None
        self.directory = (Path(mailbox_root).resolve() if mailbox_root is not None
                          else FacebookAccounts(data_dir).directory)
        self.path = self.directory/'page-commands.sqlite3'

    @contextmanager
    def database(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=3)
        conn.row_factory = sqlite3.Row
        try:
            conn.executescript('''CREATE TABLE IF NOT EXISTS commands (
                id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, request_key TEXT NOT NULL,
                action TEXT NOT NULL, payload TEXT NOT NULL, created REAL NOT NULL, expires REAL NOT NULL,
                state TEXT NOT NULL, message TEXT NOT NULL DEFAULT '', executor TEXT NOT NULL DEFAULT '',
                request_id TEXT NOT NULL DEFAULT '', result TEXT NOT NULL DEFAULT '{}',
                started REAL NOT NULL DEFAULT 0, finished REAL NOT NULL DEFAULT 0,
                UNIQUE(owner_id,request_key));
                CREATE INDEX IF NOT EXISTS commands_state ON commands(state,created);''')
            conn.commit()
            with conn:
                yield conn
        except sqlite3.Error as exc:
            raise ValueError('Chưa đọc/lưu được yêu cầu Page; giữ dữ liệu cũ. '+str(exc)) from None
        finally:
            conn.close()

    @staticmethod
    def decode(row):
        result = dict(row)
        result['payload'], result['result'] = json.loads(result['payload']), json.loads(result['result'])
        result['created_at'], result['finished_at'] = result['created'], result['finished']
        return result

    def enqueue_remote(self, action, payload, *, owner_id, request_key, ttl=MAX_AGE):
        _id(owner_id, 'Mã người gửi')
        _key(request_key)
        if type(ttl) not in (int, float) or not 5 <= ttl <= MAX_AGE:
            raise ValueError('Yêu cầu chỉ chờ tối đa 10 phút, tối thiểu 5 giây.')
        payload = _payload(action, payload)
        encoded = _json(payload)
        timestamp = time.time()
        with self.database() as conn:
            conn.execute('BEGIN IMMEDIATE')
            old = conn.execute('SELECT * FROM commands WHERE owner_id=? AND request_key=?', (owner_id, request_key)).fetchone()
            if old:
                if old['action'] != action or old['payload'] != encoded:
                    raise ValueError('Mã yêu cầu này đã dùng cho nội dung khác; chưa gửi thêm.')
                return self.decode(old)
            if 'post_id' in payload and action in {'prepare_post', 'publish_post', 'schedule_post'}:
                for prior in conn.execute("SELECT action,payload,state FROM commands WHERE state IN ('queued','running','unknown')"):
                    data = json.loads(prior['payload'])
                    if (data.get('account_id'), data.get('page_key'), data.get('post_id')) == (
                            payload['account_id'], payload['page_key'], payload['post_id']):
                        raise ValueError('Bài này đang có yêu cầu hoặc kết quả chưa rõ; kiểm tra trước, không gửi lại.')
            if action in {'sync_content_plan', 'approve_content_plan'}:
                for prior in conn.execute("SELECT action,payload FROM commands WHERE state IN ('queued','running','unknown')"):
                    data = json.loads(prior['payload'])
                    if (prior['action'] == action
                            and (data.get('account_id'), data.get('page_key')) == (payload['account_id'], payload['page_key'])
                            and (action == 'sync_content_plan' or data.get('plan_id') == payload['plan_id'])):
                        raise ValueError('Kế hoạch này đang có yêu cầu hoặc cần kiểm tra kết quả; chưa gửi trùng.')
            ident = uuid.uuid4().hex
            conn.execute('INSERT INTO commands (id,owner_id,request_key,action,payload,created,expires,state,message) '
                'VALUES (?,?,?,?,?,?,?,?,?)', (ident, owner_id, request_key, action, encoded, timestamp, timestamp+ttl,
                                             'queued', 'Đang chờ Tèo desktop nhận yêu cầu.'))
        return self.get(ident)

    def enqueue(self, operation, account_id, *, page_key='', post_id='', expected_review=None, idempotency_key, ttl=MAX_AGE):
        if self.data_dir is None:
            raise ValueError('Web chỉ dùng enqueue_remote; không đọc hồ sơ Facebook desktop.')
        registry = FacebookAccounts(self.data_dir)
        registry._account(registry.load(), account_id)
        payload = dict(account_id=account_id)
        if page_key:
            activity = PageActivity(self.data_dir)
            payload.update(page_key=page_key, target=_target(activity.page(account_id, page_key)))
            if post_id:
                post = activity.post(account_id, page_key, post_id)
                payload.update(post_id=post_id, post_key=_digest(_post_binding(post)))
                if operation == 'publish_post':
                    if not expected_review or expected_review != post.get('browser_review'):
                        raise ValueError('Cần duyệt đúng bản xem trước đã lưu trước khi đăng.')
                    self.validate_publish(post, _digest(expected_review))
                    payload['review_key'] = _digest(expected_review)
        return self.enqueue_remote(operation, payload, owner_id='0'*32, request_key=idempotency_key, ttl=ttl)

    @staticmethod
    def validate_publish(post, review_key):
        review = post.get('browser_review') or {}
        if (post['state'] != 'review' or post['delivery_mode'] != 'browser'
                or post['browser_finish_mode'] != 'tool' or post.get('manual_url')
                or not review or _digest(review) != review_key
                or review.get('browser_finish_mode') != 'tool'):
            raise ValueError('Bài chưa có bản xem trước phù hợp hoặc chưa chọn Tèo bấm Đăng; chưa đăng.')
        for key, expected in [('account_id', post['account_id']), ('page_key', post['page_key']),
                ('post_id', post['id']), ('target_name', post['target_name']), ('target_url', post['target_url']),
                ('target_id', post['target_id']), ('body', post['body'].replace('\r\n','\n').replace('\r','\n').rstrip('\n'))]:
            if review.get(key) != expected:
                raise ValueError('Bản xem trước khác Page hoặc nội dung đã chốt; chưa đăng.')
        if ([{k:v for k,v in row.items() if k != 'sha256'} for row in review.get('media', [])] != post['media']
                or any(not re.fullmatch(r'[0-9a-f]{64}', str(row.get('sha256',''))) for row in review.get('media', []))):
            raise ValueError('Tệp trong bản xem trước khác bản nháp; chưa đăng.')

    def get(self, ident):
        _id(ident, 'Mã yêu cầu')
        with self.database() as conn:
            row = conn.execute('SELECT * FROM commands WHERE id=?', (ident,)).fetchone()
        if not row:
            raise ValueError('Không tìm thấy yêu cầu Page.')
        return self.decode(row)

    def list(self, account_id=None, *, limit=100, owner_id=None):
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError('Giới hạn danh sách yêu cầu không hợp lệ.')
        with self.database() as conn:
            rows = conn.execute('SELECT * FROM commands '+('WHERE owner_id=? ' if owner_id else '')+
                'ORDER BY created DESC,id DESC LIMIT ?', (owner_id,limit) if owner_id else (limit,)).fetchall()
        values = [self.decode(row) for row in rows]
        return [row for row in values if not account_id or row['payload'].get('account_id') == account_id]

    def update(self, ident, state, message, *, expected, executor=None, result=None, request_id=None):
        if state not in STATES:
            raise ValueError('Trạng thái yêu cầu Page không hợp lệ.')
        with self.database() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('SELECT * FROM commands WHERE id=?', (_id(ident),)).fetchone()
            if not row or row['state'] not in expected or executor is not None and row['executor'] != executor:
                raise ValueError('Yêu cầu Page đã đổi hoặc do phiên khác xử lý.')
            conn.execute('UPDATE commands SET state=?,message=?,result=?,request_id=?,finished=? WHERE id=?',
                (state, str(message)[:10000], _json(result if result is not None else json.loads(row['result'])),
                 request_id if request_id is not None else row['request_id'],
                 time.time() if state in {'done','failed','unknown'} else row['finished'], ident))
        return self.get(ident)

    def claim(self, ident, executor):
        with self.database() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('SELECT * FROM commands WHERE id=?', (_id(ident),)).fetchone()
            if not row or row['state'] != 'queued' or row['expires'] <= time.time() or time.time()-row['created'] > MAX_AGE:
                return None
            conn.execute("UPDATE commands SET state='running',executor=?,request_id=id,started=?,message=? WHERE id=?",
                (executor, time.time(), 'Tèo desktop đã nhận yêu cầu.', ident))
        return self.get(ident)

    def expire_queued(self):
        with self.database() as conn:
            conn.execute("UPDATE commands SET state='failed',finished=?,message=? WHERE state='queued' AND (expires<=? OR created<?)",
                (time.time(), 'Yêu cầu đã quá 10 phút/hết hạn; chưa thực hiện. Gửi yêu cầu mới nếu vẫn cần.', time.time(),time.time()-MAX_AGE))

    def recover_running(self):
        # Called ONLY while holding the singleton executor OS lock. A stale
        # heartbeat alone cannot authorize recovery or re-execution.
        with self.database() as conn:
            conn.execute("UPDATE commands SET state='unknown',finished=?,message=? WHERE state='running'",
                (time.time(), 'Phiên desktop trước bị gián đoạn. Kiểm tra Facebook trước; Tèo không tự thực hiện lại.'))

    def cancel_queued(self, ident):
        return self.update(ident, 'failed', 'Đã bỏ yêu cầu chưa gửi vào Chrome.', expected={'queued'})

    def _read(self, name, default):
        try:
            value = json.loads((self.directory/name).read_text(encoding='utf-8'))
            return value if isinstance(value, dict) else default
        except (OSError, ValueError):
            return default

    def public_snapshot(self):
        return self._read('bridge-snapshot.json', dict(updated_at=0,accounts=[],pages=[],posts=[],schedules=[]))

    def status(self):
        result = self._read('bridge.json', dict(updated_at=0, enabled=False, message='Chưa có Tèo desktop kết nối.'))
        result['enabled'] = bool(result.get('enabled') and self.setting_enabled())
        result['online'] = bool(result.get('enabled') and 0 <= time.time()-result.get('updated_at',0) < HEARTBEAT_AGE)
        return result

    def setting_enabled(self):
        path = self.directory/'bridge-settings.json'
        # A new mailbox defaults ON as requested. An unreadable/malformed
        # persisted preference must never silently enable browser control.
        return not path.exists() or self._read('bridge-settings.json', {}).get('enabled') is True


class PageCommandBridge:
    def __init__(self, owner, *, mailbox_root=None):
        self.owner = owner
        self.store = PageCommandStore(owner.studio.data_dir, mailbox_root=mailbox_root)
        self.activity = PageActivity(owner.studio.data_dir)
        self.executor = uuid.uuid4().hex
        self.guard = None
        self.active = None
        self.closed = False
        self.last_tick = self.last_snapshot = 0
        self.asset_checks = queue.Queue()
        self.asset_verified = None
        self.enabled = self.store.setting_enabled()
        from teodub.page_content_bridge import ContentPlanReceiver
        self.content_receiver = ContentPlanReceiver(self)
        from teodub.page_ai import PageAIController
        self.ai_controller = PageAIController(self)

    def set_enabled(self, enabled):
        if type(enabled) is not bool:
            raise ValueError('Chọn bật/tắt điều khiển Page từ web.')
        self.enabled = enabled
        atomic_json(self.store.directory/'bridge-settings.json', dict(enabled=enabled))
        # Already dispatched browser work is tracked to completion. Do not
        # discard its ACK or allow another executor to claim it after disabling.
        if not enabled and self.active:
            if self.active['action'] == 'save_draft':
                self.complete(self.active,'failed','Đã tắt nhận lệnh từ web; chưa lưu bản nháp, chưa đăng.')
            else:
                self.cancel_request(self.active)
        self.heartbeat()

    def capable(self):
        return not (self.closed or getattr(self.owner,'destroyed',False)
                    or getattr(self.owner.studio,'closing',False) or getattr(self.owner.studio,'closed',False))

    def acquire(self):
        if self.guard:
            return True
        path = self.store.directory/'command-executor-lock'
        path.mkdir(parents=True, exist_ok=True)
        guard = _job_lock(path)
        try:
            guard.__enter__()
        except ValueError:
            return False
        self.guard = guard
        self.store.recover_running()
        return True

    def heartbeat(self):
        if not self.guard:
            return
        counts = {state:0 for state in STATES}
        with self.store.database() as conn:
            for row in conn.execute('SELECT state,count(*) AS n FROM commands GROUP BY state'):
                counts[row['state']] = row['n']
        atomic_json(self.store.directory/'bridge.json', dict(updated_at=time.time(), enabled=self.enabled and self.capable(),
            executor=self.executor, pid=os.getpid(), counts=counts, busy=bool(self.active),
            message='Đang xử lý yêu cầu Page.' if self.active else 'Tèo desktop sẵn sàng.' if self.enabled and self.capable()
                    else 'Điều khiển Page từ web đang tắt.'))

    def snapshot(self):
        registry = self.activity.accounts.load()
        accounts = []
        for row in registry['accounts']:
            session = self.owner.sessions.get(row['id'])
            browser = ('opening' if session and session.pending and not session.opened else
                       'open' if session and session.pending else 'closed')
            accounts.append(dict(id=row['id'],name=row['name'],browser=browser))
        pages = [dict(id=row['id'],account_id=row['account_id'],**_target(row)) for row in registry['pages']]
        posts = []
        for row in pages:
            posts.extend(public_post(post) for post in self.activity.posts(row['account_id'],row['id'])[:100])
            if len(posts) >= 1000:
                posts = posts[:1000]
                break
        from teodub.page_schedule import PageScheduleStore
        schedules = [{key:row.get(key,'') for key in ('id','account_id','page_key','post_id','state','due_at','body','message')}
                     for row in PageScheduleStore(self.owner.studio.data_dir).list()[:1000]]
        content = self.content_receiver.public_snapshot(pages)
        ai = self.ai_controller.store.public_snapshot(pages)
        from teodub.page_status import read_page_status
        page_statuses=[dict(account_id=p['account_id'],page_key=p['id'],
            **read_page_status(self.owner.studio.data_dir,p['account_id'],p['id'],self.owner)) for p in pages]
        atomic_json(self.store.directory/'bridge-snapshot.json', dict(updated_at=time.time(),accounts=accounts,
            pages=pages,posts=posts,schedules=schedules,page_statuses=page_statuses,**content,**ai))

    def validate(self, row):
        payload, action = _payload(row['action'], row['payload']), row['action']
        if action == 'add_account':
            return payload
        registry = self.activity.accounts
        registry._account(registry.load(), payload['account_id'])
        if 'page_key' in payload:
            page = self.activity.page(payload['account_id'], payload['page_key'])
            if _target(page) != payload['target']:
                raise ValueError('Page đã thay đổi sau khi gửi yêu cầu; làm mới và duyệt lại.')
        if 'post_id' in payload:
            post = self.activity.post(payload['account_id'],payload['page_key'],payload['post_id'])
            if (post['archived'] or post['delivery_mode'] != 'browser'
                    or _digest(_post_binding(post)) != payload['post_key']):
                raise ValueError('Bài/Page/tệp đã thay đổi sau khi gửi yêu cầu; chưa thao tác.')
            if _target(page) != dict(name=post['target_name'],url=post['target_url'],page_id=post['target_id']):
                raise ValueError('Page của bản nháp khác danh sách hiện tại; chưa thao tác.')
            if action in {'prepare_post','publish_post'}:
                from teodub.page_schedule import PageScheduleStore
                schedules = PageScheduleStore(self.owner.studio.data_dir).list(payload['account_id'],payload['page_key'])
                if any(saved['post_id'] == post['id'] and saved['state'] not in {'published','cancelled'} for saved in schedules):
                    raise ValueError('Bài này đã có lịch đăng được lưu/đang giữ. Xử lý lịch đó hoặc tạo bài mới; chưa thao tác đăng ngay.')
            if action == 'prepare_post' and post['state'] not in {'draft','failed'}:
                raise ValueError('Bài đã có lượt chuẩn bị/gửi; kiểm tra Facebook, không tự làm lại.')
            if action == 'publish_post':
                self.store.validate_publish(post, payload['review_key'])
            if action == 'review_post' and post['state'] != 'review':
                raise ValueError('Bài không còn ở bước xem trước.')
            if action == 'schedule_post' and (post['state'] not in {'draft','failed'} or post['kind'] != 'text' or post['media']):
                raise ValueError('Lịch bài chữ chỉ nhận bản nháp chữ chưa gửi; video dùng lịch video của chiến dịch.')
        return payload

    def asset(self, value):
        if value is None:
            return []
        path = Path(value['path'])
        root = self.store.directory.parent/'outputs'/value['job_id']
        if (not path.is_file() or path.is_symlink() or path.suffix.lower() != '.mp4'
                or not path.resolve().is_relative_to(root.resolve())
                or any(parent.is_symlink() for parent in [root, *path.parents] if parent == root or parent.is_relative_to(root))):
            raise ValueError('Video không thuộc thành phẩm web đã được duyệt.')
        stat = path.stat()
        if stat.st_size != value['size'] or stat.st_mtime_ns != value['mtime_ns'] or hash_file(path) != value['sha256']:
            raise ValueError('Video thành phẩm đã đổi sau khi chọn; chưa tạo bản nháp.')
        return [str(path.resolve())]

    def dispatch(self, row):
        payload = self.validate(row)
        if row['action'] in {'save_ai_policy','pause_ai_policy'}:
            a,p=payload['account_id'],payload['page_key']
            if row['action']=='save_ai_policy':
                if self.ai_controller.store.plans.get_connection(a,p)['thread_id'] != payload['thread_id']:
                    raise ValueError('Chat chiến lược đã đổi sau khi gửi cấu hình; làm mới và lưu lại.')
                self.ai_controller.store.save(a,p,payload['config'],payload['due_at'],payload['enabled'])
            else:
                self.ai_controller.store.pause(a,p)
            return dict(account_id=a,page_key=p)
        action, ident = row['action'], row['id']
        if action in {'sync_content_plan', 'approve_content_plan'}:
            return self.content_receiver.dispatch(row)
        if action == 'add_account':
            account = self.activity.accounts.add_account(payload['name'])
            self.owner.reload()
            return dict(id=account['id'],account_id=account['id'],name=account['name'])
        if action == 'save_page':
            page = self.activity.accounts.save_page(payload['account_id'],payload['name'],payload['url'],payload['page_id'])
            self.owner.reload()
            return dict(id=page['id'],page_key=page['id'],account_id=page['account_id'],**_target(page))
        if action == 'save_draft':
            files = []
            if payload['asset'] is not None:
                if self.asset_verified != (ident, _json(payload['asset'])):
                    raise ValueError('Video chưa được kiểm tra xong; chưa lưu bản nháp.')
                path = Path(payload['asset']['path'])
                stat = path.stat()
                if stat.st_size != payload['asset']['size'] or stat.st_mtime_ns != payload['asset']['mtime_ns']:
                    raise ValueError('Video đã đổi sau kiểm tra; chưa lưu bản nháp.')
                files = [str(path.resolve())]
            post = self.activity.save_draft(payload['account_id'],payload['page_key'],payload['body'],
                files,delivery_mode='browser',browser_finish_mode='tool')
            return dict(public_post(post),post_id=post['id'])
        if action == 'schedule_post':
            from teodub.page_schedule import PageScheduleStore
            schedule = PageScheduleStore(self.owner.studio.data_dir).create_post(payload['account_id'],payload['page_key'],
                payload['post_id'],payload['due_at'],expected_target=payload['target'],expected_post_key=payload['post_key'])
            self.owner.get_page_scheduler().start(schedule['id'])
            return dict(schedule_id=schedule['id'],post_id=payload['post_id'],account_id=payload['account_id'],page_key=payload['page_key'])
        if action in {'open_account','open_page'}:
            accepted = self.owner.open_browser(FACEBOOK_HOME if action == 'open_account' else payload['target']['url'],
                                               account_id=payload['account_id'],request_id=ident)
        elif action == 'load_pages':
            accepted = self.owner.load_pages(payload['account_id'],request_id=ident)
        else:
            accepted = self.owner.submit_page_browser(payload['account_id'],payload['page_key'],payload['post_id'],
                {'prepare_post':'prepare','publish_post':'publish','review_post':'review'}[action],request_id=ident)
            if accepted and accepted != ident:
                raise ValueError('Mã phản hồi Chrome khác yêu cầu; kiểm tra trước, không gửi lại.')
        if not accepted:
            raise ValueError(self.owner.status.get() or 'Desktop chưa nhận được thao tác; chưa gửi vào Chrome.')
        return None

    def cancel_request(self, row):
        if row['action'] in {'sync_content_plan', 'approve_content_plan'}:
            self.content_receiver.cancel(row['id'])
            return
        session = self.owner.sessions.get(row['payload'].get('account_id'))
        if not session:
            return
        request = next((getattr(session,table,{}).get(row['request_id']) for table in ('post_actions','navigation','page_load_requests')
                        if getattr(session,table,{}).get(row['request_id'])),None)
        if request and request.get('cancel') is not None:
            request['cancel'].set()

    def complete(self, row, state, message, result=None):
        self.store.update(row['id'],state,message,expected={'running'},executor=self.executor,result=result)
        self.active = None
        self.asset_verified = None

    def check_asset(self, row):
        # File hashing can be slow. The background task only reads one
        # server-verified file and never touches Tk or a browser.
        try:
            self.asset(row['payload']['asset'])
            self.asset_checks.put((row['id'], None))
        except Exception as exc:
            self.asset_checks.put((row['id'], str(exc) or type(exc).__name__))

    def observe_asset(self):
        row = self.active
        try:
            ident, error = self.asset_checks.get_nowait()
        except queue.Empty:
            if time.time() >= row['expires']:
                self.complete(row,'failed','Kiểm tra video quá thời gian; chưa lưu bản nháp, chưa đăng.')
            return
        if ident != row['id']:
            return
        if error:
            self.complete(row,'failed',error)
            return
        self.asset_verified = (ident, _json(row['payload']['asset']))
        try:
            result = self.dispatch(row)
        except Exception as exc:
            self.complete(row,'unknown',str(exc) or type(exc).__name__)
        else:
            self.complete(row,'done','Đã lưu bản nháp trong Tèo; chưa đăng công khai.',result)

    def observe(self):
        row = self.active
        session = self.owner.sessions.get(row['payload'].get('account_id'))
        action = row['action']
        table = ('navigation' if action in {'open_account','open_page'} else
                 'page_load_requests' if action == 'load_pages' else 'post_actions')
        reply = getattr(session,table,{}).get(row['request_id']) if session else None
        if reply and reply['state'] in {'opened','done'}:
            result = reply.get('result') or {}
            if action in {'prepare_post','publish_post','review_post'}:
                post = self.activity.post(row['payload']['account_id'],row['payload']['page_key'],row['payload']['post_id'])
                if (result.get('id'),result.get('account_id'),result.get('page_key')) != (post['id'],post['account_id'],post['page_key']):
                    self.complete(row,'unknown','Phản hồi bài không khớp tài khoản/Page; kiểm tra trước, không gửi lại.')
                    return
                if action == 'publish_post':
                    receipt = post.get('browser_receipt') or {}
                    if post['state'] != 'published' or not post['external_url'] or receipt.get('source') != 'visible_dom' or receipt.get('url') != post['external_url']:
                        self.complete(row,'unknown',post['message'] or 'Chưa có bằng chứng bài đã đăng; không tự gửi lại.',public_post(post))
                        return
                elif post['state'] != 'review':
                    self.complete(row,'unknown','Bài không còn ở bước xem trước; kiểm tra trên Facebook.',public_post(post))
                    return
                result = public_post(post)
            self.complete(row,'done',reply.get('message') or 'Đã hoàn tất thao tác trong Chrome.', result)
        elif reply and reply['state'] == 'error':
            state = 'failed'
            if action in {'prepare_post','publish_post','review_post'}:
                state = 'unknown'
                try:
                    post = self.activity.post(row['payload']['account_id'],row['payload']['page_key'],row['payload']['post_id'])
                    # The publisher records unknown after any editor/upload
                    # mutation. Only a clean draft or its pre-mutation failure
                    # is eligible for a new explicit user-requested attempt.
                    if (post['state'] in {'draft','failed'} and not post['browser_review'] and not post['browser_receipt']
                            and not post.get('browser_tracking')
                            and not post['remote_assets'] and not post['external_id'] and not post['external_url'] and not post['manual_url']):
                        state = 'failed'
                except (OSError,ValueError,KeyError,TypeError):
                    pass
            self.complete(row,state,reply.get('message') or 'Chrome báo lỗi; kiểm tra trước khi tiếp tục.')
        elif time.time() >= row['expires'] or session is None or not session.pending:
            self.cancel_request(row)
            self.complete(row,'unknown','Phiên Chrome đóng hoặc hết thời gian; kiểm tra Facebook, không tự gửi lại.')

    def tick(self):
        if not self.capable() or time.monotonic()-self.last_tick < 1:
            return
        self.last_tick = time.monotonic()
        try:
            setting = self.store.setting_enabled()
            if setting != self.enabled:
                self.enabled = setting
                if not setting and self.active:
                    if self.active['action'] == 'save_draft':
                        self.complete(self.active,'failed','Đã tắt nhận lệnh từ web; chưa lưu bản nháp, chưa đăng.')
                    else:
                        self.cancel_request(self.active)
            if not self.guard and not self.acquire():
                return
            self.content_receiver.tick()
            self.ai_controller.tick()
            self.store.expire_queued()
            if self.active:
                if self.active['action'] in {'sync_content_plan', 'approve_content_plan'}:
                    if time.time() >= self.active['expires']:
                        self.cancel_request(self.active)
                        self.complete(self.active,'failed','Xử lý kế hoạch quá thời gian; lịch đã lưu chưa được bật tự động.')
                elif self.active['action'] == 'save_draft' and self.active['payload']['asset'] is not None:
                    self.observe_asset()
                else:
                    self.observe()
            if self.enabled and self.active is None and not getattr(self.owner.studio,'restart_pending',False):
                queued = sorted((row for row in self.store.list(limit=1000) if row['state']=='queued'),key=lambda x:(x['created'],x['id']))
                for candidate in queued:
                    pending = self.content_receiver.pending
                    if pending and candidate['action'] in {'sync_content_plan', 'approve_content_plan'}:
                        payload = candidate['payload']
                        can_join = (candidate['action'] == 'sync_content_plan' and pending.get('kind') == 'read'
                            and not pending.get('command_id') and not pending['cancel'].is_set()
                            and (pending['account_id'], pending['page_key'], pending['thread_id']) ==
                                (payload['account_id'], payload['page_key'], payload['thread_id']))
                        if not can_join:
                            continue
                    session = self.owner.sessions.get(candidate['payload'].get('account_id'))
                    if session and (session.loading_pages or any(x['state'] in {'queued','running'} for x in session.post_actions.values())):
                        continue
                    row = self.store.claim(candidate['id'],self.executor)
                    if not row:
                        continue
                    self.active = row
                    try:
                        self.validate(row)
                    except Exception as exc:
                        self.complete(row,'failed',str(exc) or type(exc).__name__)
                        break
                    if row['action'] == 'save_draft' and row['payload']['asset'] is not None:
                        try:
                            threading.Thread(target=self.check_asset,args=(row,),daemon=True).start()
                        except Exception as exc:
                            self.complete(row,'failed','Chưa mở được bước kiểm tra video: '+str(exc))
                        break
                    try:
                        result = self.dispatch(row)
                    except Exception as exc:
                        # The durable running marker precedes every action. Any
                        # unexpected exception is ambiguous; never requeue it.
                        self.complete(row,'unknown',str(exc) or type(exc).__name__)
                    else:
                        if result is not None:
                            self.complete(row,'done','Đã lưu trong Tèo; chưa đăng công khai.',result)
                    break
            if time.monotonic()-self.last_snapshot >= 2:
                self.snapshot()
                self.last_snapshot = time.monotonic()
            self.heartbeat()
        except (OSError,ValueError,KeyError,TypeError) as exc:
            self.owner.status.set('Điều khiển Page từ web cần kiểm tra: '+str(exc))

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.content_receiver.close()
        self.ai_controller.close()
        try:
            if self.active:
                self.cancel_request(self.active)
                self.complete(self.active,'unknown','Tèo desktop đã đóng; kiểm tra Facebook, không tự gửi lại.')
            self.heartbeat()
        finally:
            if self.guard:
                self.guard.__exit__(None,None,None)
                self.guard = None
