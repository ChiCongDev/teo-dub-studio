import contextlib
import hashlib
import json
from pathlib import Path
import re
import secrets
import sqlite3
import time
import uuid

from werkzeug.security import check_password_hash, generate_password_hash
from teodub.ai_models import NEW_MODELS
from teodub_web.form_settings import FORM_FIELDS, model_policy, preferences

ACTIVE = ('queued', 'running', 'stopping')
RETRYABLE = ('failed', 'cancelled', 'interrupted')


class Store:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root/'portal.sqlite3'
        with self.db() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS users (
                  id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL,
                  name TEXT NOT NULL, password TEXT NOT NULL,
                  role TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1);
                CREATE TABLE IF NOT EXISTS sessions (
                  token TEXT PRIMARY KEY, user_id TEXT NOT NULL, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS attempts (key TEXT NOT NULL, time REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS attempts_key ON attempts(key,time);
                CREATE TABLE IF NOT EXISTS jobs (
                  id TEXT PRIMARY KEY, owner TEXT NOT NULL, title TEXT NOT NULL,
                  input TEXT NOT NULL, spec TEXT NOT NULL, state TEXT NOT NULL,
                  created REAL NOT NULL, updated REAL NOT NULL,
                  message TEXT NOT NULL DEFAULT '', submit_key TEXT NOT NULL,
                  UNIQUE(owner,submit_key));
                CREATE TABLE IF NOT EXISTS audit (
                  time REAL NOT NULL, user_id TEXT, action TEXT NOT NULL, target TEXT);
                CREATE TABLE IF NOT EXISTS portal_options (
                  key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS web_preferences (
                  user_id TEXT PRIMARY KEY, value TEXT NOT NULL, updated REAL NOT NULL);
            ''')

    @contextlib.contextmanager
    def db(self, *, transaction=False):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            if transaction:
                db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def add_user(self, username, name, password, role='staff', actor=None):
        if (not isinstance(username, str) or not re.fullmatch(r'[a-z0-9][a-z0-9_.-]{2,31}', username)
                or not isinstance(name, str) or not 1 <= len(name.strip()) <= 80
                or not isinstance(password, str) or not 12 <= len(password) <= 128
                or role not in ('admin', 'staff')):
            raise ValueError('Tên đăng nhập cần 3–32 ký tự thường; mật khẩu cần ít nhất 12 ký tự.')
        ident = uuid.uuid4().hex
        digest = generate_password_hash(password)
        with self.db(transaction=True) as db:
            try:
                db.execute('INSERT INTO users(id,username,name,password,role) VALUES(?,?,?,?,?)',
                           (ident, username, name.strip(), digest, role))
            except sqlite3.IntegrityError:
                raise ValueError('Tên đăng nhập đã được dùng.') from None
            db.execute('INSERT INTO audit VALUES(?,?,?,?)', (time.time(), actor, 'create_user', ident))
        return ident

    def authenticate(self, username, password, address):
        # Both address and username gates matter when a tunnel forwards requests.
        keys = ['ip:'+str(address), 'user:'+str(username).lower()]
        now = time.time()
        with self.db(transaction=True) as db:
            db.execute('DELETE FROM attempts WHERE time<?', (now-600,))
            counts = [db.execute('SELECT count(*) FROM attempts WHERE key=?', (key,)).fetchone()[0] for key in keys]
            if counts[0] >= 25 or counts[1] >= 5:
                raise ValueError('Đăng nhập sai nhiều lần. Thử lại sau 10 phút.')
            # Reserve attempts before checking the expensive hash; no parallel bypass.
            db.executemany('INSERT INTO attempts VALUES(?,?)', [(key, now) for key in keys])
            user = db.execute('SELECT * FROM users WHERE username=?', (username,)).fetchone()
        digest = user['password'] if user else generate_password_hash('invalid-password-placeholder')
        if not check_password_hash(digest, password) or not user or not user['enabled']:
            raise ValueError('Tên đăng nhập hoặc mật khẩu chưa đúng.')
        token = secrets.token_urlsafe(32)
        with self.db(transaction=True) as db:
            db.execute('DELETE FROM attempts WHERE key=?', (keys[1],))
            db.execute('DELETE FROM sessions WHERE expires<?', (now,))
            db.execute('INSERT INTO sessions VALUES(?,?,?)', (self.digest(token), user['id'], now+8*3600))
        return token

    @staticmethod
    def digest(token):
        return hashlib.sha256(token.encode()).hexdigest()

    def user_for_token(self, token):
        if not isinstance(token, str):
            return None
        with self.db() as db:
            row = db.execute('''SELECT u.id,u.username,u.name,u.role FROM users u JOIN sessions s
                ON u.id=s.user_id WHERE s.token=? AND s.expires>? AND u.enabled=1''',
                (self.digest(token), time.time())).fetchone()
        return dict(row) if row else None

    def logout(self, token):
        with self.db() as db:
            db.execute('DELETE FROM sessions WHERE token=?', (self.digest(token),))

    def change_password(self, user, password, *, actor):
        if not isinstance(password, str) or not 12 <= len(password) <= 128:
            raise ValueError('Mật khẩu cần 12–128 ký tự.')
        with self.db(transaction=True) as db:
            if not db.execute('SELECT 1 FROM users WHERE id=?', (user,)).fetchone():
                raise ValueError('Không tìm thấy tài khoản.')
            db.execute('UPDATE users SET password=? WHERE id=?', (generate_password_hash(password), user))
            db.execute('DELETE FROM sessions WHERE user_id=?', (user,))
            db.execute('INSERT INTO audit VALUES(?,?,?,?)', (time.time(), actor, 'reset_password', user))

    def users(self):
        with self.db() as db:
            return [dict(row) for row in db.execute('SELECT id,username,name,role,enabled FROM users ORDER BY username')]

    def disable_user(self, ident, actor):
        with self.db(transaction=True) as db:
            # The bootstrap admin cannot be disabled through the staff form.
            db.execute("UPDATE users SET enabled=0 WHERE id=? AND role='staff'", (ident,))
            db.execute('DELETE FROM sessions WHERE user_id=?', (ident,))
            db.execute('INSERT INTO audit VALUES(?,?,?,?)', (time.time(), actor, 'disable_user', ident))

    def submit(self, owner, title, url, spec, key):
        if not isinstance(key, str) or not re.fullmatch('[a-f0-9]{32}', key):
            raise ValueError('Phiếu gửi đã hết hạn. Tải lại trang rồi gửi.')
        ident = uuid.uuid4().hex
        now = time.time()
        with self.db(transaction=True) as db:
            existing = db.execute('SELECT id FROM jobs WHERE owner=? AND submit_key=?', (owner, key)).fetchone()
            if existing:
                return existing['id']
            # Freeze the model policy with the job in the same transaction.
            models = self._model_policy(db)
            if any(key in spec and spec[key] != value for key, value in models.items()):
                raise ValueError('Model AI đã được quản trị viên cập nhật. Làm mới trang rồi gửi lại.')
            personal = preferences({key: spec[key] for key in FORM_FIELDS if key in spec}, models)
            spec = dict(spec, **models, **personal)
            if db.execute("SELECT count(*) FROM jobs WHERE state IN ('queued','running','stopping')").fetchone()[0] >= 100:
                raise ValueError('Hàng đợi đã đầy. Chờ các video đang chạy hoàn tất.')
            if db.execute("SELECT count(*) FROM jobs WHERE owner=? AND state IN ('queued','running','stopping')", (owner,)).fetchone()[0] >= 10:
                raise ValueError('Mỗi người có tối đa 10 video đang chờ hoặc đang chạy.')
            db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?)',
                       (ident, owner, title, url, json.dumps(spec, ensure_ascii=False), 'queued', now, now, 'Đang chờ máy xử lý.', key))
            db.execute('INSERT INTO audit VALUES(?,?,?,?)', (now, owner, 'submit', ident))
            self._write_preferences(db, owner, personal, now)
        return ident

    @staticmethod
    def _model_policy(db):
        row = db.execute("SELECT value FROM portal_options WHERE key='models'").fetchone()
        try:
            return model_policy(json.loads(row['value']) if row else dict(NEW_MODELS))
        except (ValueError, TypeError):
            raise ValueError('Cấu hình model AI cần được quản trị viên kiểm tra và lưu lại.') from None

    def model_policy(self):
        with self.db() as db:
            return self._model_policy(db)

    def save_model_policy(self, values, actor):
        models = model_policy(values)
        with self.db(transaction=True) as db:
            if not db.execute("SELECT 1 FROM users WHERE id=? AND role='admin' AND enabled=1", (actor,)).fetchone():
                raise PermissionError('Chỉ quản trị viên được lưu model AI.')
            db.execute("INSERT INTO portal_options VALUES('models',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                       (json.dumps(models),))
            db.execute('INSERT INTO audit VALUES(?,?,?,?)', (time.time(), actor, 'save_model_policy', 'models'))
        return models

    @staticmethod
    def _preferences(db, user_id, models):
        row = db.execute('SELECT value FROM web_preferences WHERE user_id=?', (user_id,)).fetchone()
        if not row:
            # Start existing accounts from their latest submitted choices.
            row = db.execute('SELECT spec AS value FROM jobs WHERE owner=? ORDER BY created DESC,id DESC LIMIT 1',
                             (user_id,)).fetchone()
        try:
            values = json.loads(row['value']) if row else {}
            return preferences({key: values[key] for key in FORM_FIELDS if key in values}, models, fallback_voice=True)
        except (ValueError, TypeError):
            return preferences({}, models)

    def form_preferences(self, user_id, models=None):
        with self.db() as db:
            return self._preferences(db, user_id, models or self._model_policy(db))

    @staticmethod
    def _write_preferences(db, user_id, values, updated):
        db.execute('INSERT INTO web_preferences VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET value=excluded.value,updated=excluded.updated',
                   (user_id, json.dumps(values), updated))

    def save_preferences(self, user_id, values):
        if not isinstance(values, dict) or set(values)-FORM_FIELDS:
            raise ValueError('Lựa chọn cá nhân có trường chưa được hỗ trợ.')
        with self.db(transaction=True) as db:
            if not db.execute('SELECT 1 FROM users WHERE id=? AND enabled=1', (user_id,)).fetchone():
                raise PermissionError('Phiên đăng nhập đã hết hạn.')
            models = self._model_policy(db)
            personal = preferences(dict(self._preferences(db, user_id, models), **values), models)
            self._write_preferences(db, user_id, personal, time.time())
        return personal

    def job(self, ident, user=None):
        with self.db() as db:
            row = db.execute('SELECT j.*,u.name AS owner_name FROM jobs j JOIN users u ON u.id=j.owner WHERE j.id=?', (ident,)).fetchone()
        if not row or (user and user['role'] != 'admin' and row['owner'] != user['id']):
            return None
        return dict(row)

    def jobs(self, user=None):
        clause = 'WHERE j.owner=?' if user and user['role'] != 'admin' else ''
        args = (user['id'],) if clause else ()
        with self.db() as db:
            return [dict(row) for row in db.execute(f'''SELECT j.*,u.name AS owner_name FROM jobs j JOIN users u
                ON u.id=j.owner {clause} ORDER BY created DESC LIMIT 200''', args)]

    def set_state(self, ident, state, message=''):
        with self.db() as db:
            db.execute('UPDATE jobs SET state=?,message=?,updated=? WHERE id=?', (state, message, time.time(), ident))

    def claim(self):
        with self.db(transaction=True) as db:
            if db.execute("SELECT 1 FROM jobs WHERE state IN ('running','stopping')").fetchone():
                return None
            row = db.execute("SELECT id FROM jobs WHERE state='queued' ORDER BY created LIMIT 1").fetchone()
            if row:
                db.execute("UPDATE jobs SET state='running',message='Đang chuẩn bị xử lý.',updated=? WHERE id=?", (time.time(), row['id']))
        return self.job(row['id']) if row else None

    def action(self, ident, action, user):
        with self.db(transaction=True) as db:
            row = db.execute('SELECT * FROM jobs WHERE id=?', (ident,)).fetchone()
            if not row or (user['role'] != 'admin' and row['owner'] != user['id']):
                raise LookupError()
            if action == 'stop' and row['state'] in ACTIVE:
                state = 'cancelled' if row['state'] == 'queued' else 'stopping'
                if state == 'stopping':
                    directory = self.root/'jobs'/ident
                    directory.mkdir(parents=True, exist_ok=True)
                    (directory/'stop.request').touch()
                message = 'Đã dừng hàng đợi.' if state == 'cancelled' else 'Đang dừng; giữ các bước đã hoàn tất.'
            elif action == 'retry' and row['state'] in RETRYABLE:
                if db.execute("SELECT count(*) FROM jobs WHERE state IN ('queued','running','stopping')").fetchone()[0] >= 100:
                    raise ValueError('Hàng đợi đã đầy.')
                (self.root/'jobs'/ident/'stop.request').unlink(missing_ok=True)
                state, message = 'queued', 'Đang chờ tiếp tục với cấu hình đã lưu.'
            else:
                raise ValueError('Trạng thái video đã thay đổi. Tải lại danh sách.')
            db.execute('UPDATE jobs SET state=?,message=?,updated=? WHERE id=?', (state, message, time.time(), ident))
            db.execute('INSERT INTO audit VALUES(?,?,?,?)', (time.time(), user['id'], action, ident))
