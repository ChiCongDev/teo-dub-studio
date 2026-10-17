"""Explicit desktop OAuth and resumable Drive delivery; no work on startup.

Only ``drive.file`` is requested. Tokens and resumable-session capabilities are
encrypted by Windows DPAPI; the public registry and journals never contain them.
Official protocols: developers.google.com/identity/protocols/oauth2/native-app
and developers.google.com/workspace/drive/api/guides/manage-uploads.
"""
import base64
from contextlib import contextmanager
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import time
from urllib.parse import parse_qs, quote, urlencode, urlsplit
import uuid
import webbrowser

import requests

from teodub.common import Cancelled, atomic_json, check_cancel


SCOPE = 'https://www.googleapis.com/auth/drive.file'
AUTH_URL = 'https://accounts.google.com/o/oauth2/v2/auth'
TOKEN_URL = 'https://oauth2.googleapis.com/token'
API_URL = 'https://www.googleapis.com/drive/v3'
UPLOAD_URL = 'https://www.googleapis.com/upload/drive/v3/files'
FOLDER_MIME = 'application/vnd.google-apps.folder'
CHUNK_SIZE = 8 * 1024 * 1024  # A multiple of Drive's 256 KiB chunk unit.
_UUID = re.compile(r'[0-9a-f]{32}')
_DIGEST = re.compile(r'[0-9a-f]{64}')
_DRIVE_ID = re.compile(r'[A-Za-z0-9_-]{1,200}')
_REMOTE_FIELDS = 'id,name,mimeType,size,md5Checksum,sha256Checksum,appProperties,parents,trashed,webViewLink'


class DriveError(ValueError):
    def __init__(self, message, *, uncertain=False):
        super().__init__(message)
        self.uncertain = uncertain


class DriveAuthError(DriveError):
    """Requires an explicit reconnection; never changes account automatically."""


def _uuid(value):
    if not isinstance(value, str) or not _UUID.fullmatch(value):
        raise DriveError('Mã tài khoản Google Drive không hợp lệ. Chọn lại tài khoản.')
    return value


def _drive_id(value):
    if not isinstance(value, str) or not _DRIVE_ID.fullmatch(value):
        raise DriveError('Mã thư mục hoặc tệp Google Drive không hợp lệ.')
    return value


def _text(value, maximum=150):
    if (not isinstance(value, str) or not value.strip() or len(value) > maximum
            or any(ord(char) < 32 for char in value)):
        raise DriveError('Tên thư mục Google Drive cần nằm trên một dòng, từ 1 đến 150 ký tự.')
    return value.strip()


def normalize_drive_config(value=None):
    """Accept the nested payload, or a full worker config. Defaults to disabled."""
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise DriveError('Cấu hình Google Drive không hợp lệ; chọn và lưu lại.')
    if 'drive_upload' in value:
        value = value['drive_upload']
        if value is None:
            value = {}
    elif value.get('mode') == 'sync_folder':
        pass
    elif 'enabled' not in value and any(key not in {'version', 'account_id', 'folder_id', 'folder_name'} for key in value):
        value = {}
    if (not isinstance(value, dict) or type(value.get('version', 1)) is not int
            or value.get('version', 1) != 1 or type(value.get('enabled', False)) is not bool):
        raise DriveError('Cấu hình Google Drive không hợp lệ; chọn và lưu lại.')
    if value.get('mode') == 'sync_folder':
        from teodub.drive_folder import normalize_folder_config
        return normalize_folder_config(value)
    if value.get('mode') not in (None, 'oauth'):
        raise DriveError('Cách lưu Google Drive không được hỗ trợ. Chọn lại nơi lưu.')
    account = value.get('account_id', '')
    folder = value.get('folder_id', '')
    name = value.get('folder_name', '')
    if not isinstance(account, str) or not isinstance(folder, str) or not isinstance(name, str):
        raise DriveError('Cấu hình Google Drive không hợp lệ; chọn và lưu lại.')
    if account:
        _uuid(account)
    if folder:
        _drive_id(folder)
    if len(name) > 200 or any(ord(char) < 32 for char in name):
        raise DriveError('Tên thư mục Google Drive không hợp lệ.')
    enabled = value.get('enabled', False)
    if enabled and (not account or not folder):
        raise DriveError('Chọn tài khoản và thư mục Google Drive trước khi bật tự lưu.')
    return dict(version=1, enabled=enabled, account_id=account, folder_id=folder, folder_name=name)


def _read_json(path, maximum=2 * 1024 * 1024):
    try:
        if Path(path).is_symlink() or Path(path).stat().st_size > maximum:
            raise ValueError()
        value = json.loads(Path(path).read_text(encoding='utf-8'))
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (OSError, ValueError, TypeError):
        raise DriveError('Chưa đọc được hồ sơ Google Drive. Dữ liệu cũ được giữ để kiểm tra.') from None


@contextmanager
def _lock(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    with (directory/'worker.lock').open('a+b') as stream:
        stream.seek(0, 2)
        if not stream.tell():
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise DriveError('Một lượt Google Drive đang xử lý nguồn này. Chờ lượt đó hoàn tất.') from None
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


class DriveVault:
    def __init__(self, directory):
        self.directory = Path(directory)/'credentials'

    def path(self, reference):
        _uuid(reference)
        return self.directory/(reference+'.bin')

    @staticmethod
    def entropy(subject):
        return ('TeoDubStudio.GoogleDrive.'+subject).encode('ascii')

    def save(self, subject, value):
        if os.name != 'nt':
            raise DriveAuthError('Lưu kết nối Google Drive hiện cần Windows. Không lưu khóa dạng văn bản.')
        import win32crypt
        try:
            blob = win32crypt.CryptProtectData(json.dumps(value).encode('utf-8'),
                'Teo Google Drive', self.entropy(subject), None, None, 0x01)
            reference = uuid.uuid4().hex
            self.directory.mkdir(parents=True, exist_ok=True)
            with self.path(reference).open('xb') as handle:
                handle.write(blob)
                handle.flush()
                os.fsync(handle.fileno())
            return reference
        except Exception:
            raise DriveAuthError('Windows chưa mã hóa được kết nối Google Drive. Không lưu khóa dạng văn bản.') from None

    def load(self, subject, reference):
        try:
            import win32crypt
            _, plain = win32crypt.CryptUnprotectData(self.path(reference).read_bytes(),
                self.entropy(subject), None, None, 0x01)
            value = json.loads(plain.decode('utf-8'))
            if not isinstance(value, dict):
                raise ValueError()
            return value
        except Exception:
            raise DriveAuthError('Không mở được kết nối trên máy hoặc tài khoản Windows này. Kết nối Google Drive lại.') from None

    def remove(self, reference):
        self.path(reference).unlink(missing_ok=True)


def _progress(callback, state, message, transferred=0, total=0):
    if callback:
        try:
            callback(dict(state=state, message=message, transferred=transferred, total=total,
                          percent=int(100 * transferred / total) if total else 0))
        except Exception:
            # A UI closing must not destroy a successfully received upload result.
            pass


def _body(response):
    try:
        body = response.json()
        return body if isinstance(body, dict) else {}
    except (ValueError, TypeError):
        return {}


def _checked_token(body, *, require_refresh=False):
    """Validate wire values without incorporating a server body in an error."""
    if not isinstance(body, dict):
        raise DriveAuthError('Google chưa cấp kết nối hợp lệ. Kết nối lại trước khi tiếp tục.')
    for name in ('access_token', 'refresh_token'):
        token = body.get(name)
        required = name == 'access_token' or require_refresh
        if ((required and not token) or (token is not None and
                (not isinstance(token, str) or len(token) > 32768 or any(ord(c) < 32 for c in token)))):
            raise DriveAuthError('Google chưa cấp kết nối hợp lệ. Kết nối lại và cấp quyền Drive.')
    scope = body.get('scope', SCOPE)
    if not isinstance(scope, str) or SCOPE not in scope.split():
        raise DriveAuthError('Google chưa cấp quyền lưu tệp Drive. Kết nối lại và cấp quyền.')
    token_type = body.get('token_type', 'Bearer')
    if not isinstance(token_type, str) or token_type.lower() != 'bearer':
        raise DriveAuthError('Loại kết nối Google chưa được Tèo hỗ trợ. Kết nối lại bằng ứng dụng Desktop.')
    try:
        expires = float(body.get('expires_in', 3600))
        if not 0 < expires <= 365*24*3600:
            raise ValueError()
    except (ValueError, TypeError, OverflowError):
        raise DriveAuthError('Google trả thời hạn kết nối không hợp lệ. Kết nối chưa được lưu.') from None
    return expires


def _response_error(response, *, uncertain=False):
    body = _body(response)
    error = body.get('error', {})
    entries = error.get('errors', []) if isinstance(error, dict) else []
    reasons = {row.get('reason') for row in entries if isinstance(row, dict) and isinstance(row.get('reason'), str)} if isinstance(entries, list) else set()
    if response.status_code == 401 or error == 'invalid_grant':
        raise DriveAuthError('Kết nối Google Drive đã hết hạn hoặc bị thu hồi. Kết nối lại trước khi tiếp tục.')
    if 'storageQuotaExceeded' in reasons:
        raise DriveError('Google Drive đã hết dung lượng. Video thành phẩm trên máy vẫn được giữ.')
    if 'accessNotConfigured' in reasons or 'SERVICE_DISABLED' in reasons:
        raise DriveError('Chưa bật Google Drive API cho ứng dụng OAuth. Bật API trong Google Cloud rồi thử lại.')
    if response.status_code == 403:
        raise DriveError('Google Drive chưa cho phép thao tác này. Kiểm tra quyền thư mục, hạn mức hoặc kết nối lại.')
    if response.status_code == 429:
        raise DriveError('Google Drive đang giới hạn lượt yêu cầu. Chờ rồi tiếp tục lượt tải đã lưu.', uncertain=uncertain)
    if response.status_code >= 500 or response.status_code in (408, 409):
        raise DriveError('Google Drive chưa xác nhận kết quả. Phiên tải được giữ để đối chiếu khi tiếp tục.', uncertain=uncertain)
    raise DriveError('Google Drive chưa chấp nhận yêu cầu. Kiểm tra kết nối và thư mục; video trên máy được giữ.', uncertain=uncertain)


class DriveClient:
    def __init__(self, data_dir, *, session=None, vault=None):
        self.directory = Path(data_dir).resolve()/'google-drive'
        self.registry_path = self.directory/'accounts.json'
        self.session = session or requests.Session()
        self.vault = vault or DriveVault(self.directory)

    def _registry(self):
        if not self.registry_path.exists():
            return dict(version=1, accounts=[])
        registry = _read_json(self.registry_path)
        try:
            if registry['version'] != 1 or not isinstance(registry['accounts'], list):
                raise ValueError()
            seen = set()
            for row in registry['accounts']:
                key = _uuid(row['id'])
                if key in seen or type(row['connected']) is not bool:
                    raise ValueError()
                seen.add(key)
                _uuid(row['generation'])
                if row.get('credential_ref'):
                    _uuid(row['credential_ref'])
                if row['connected'] and not row.get('credential_ref'):
                    raise ValueError()
                for field in ('email', 'name'):
                    if not isinstance(row[field], str) or len(row[field]) > 250 or any(ord(c) < 32 for c in row[field]):
                        raise ValueError()
            return registry
        except (KeyError, TypeError, ValueError):
            raise DriveError('Danh sách Google Drive bị lỗi. Không ghi đè hồ sơ đã lưu.') from None

    def accounts(self):
        """Public status only; neither creates files nor decrypts stored tokens."""
        return [{key: row[key] for key in ('id', 'name', 'email', 'connected')}
                for row in self._registry()['accounts']]

    def _account(self, account_id, generation=None):
        _uuid(account_id)
        row = next((row for row in self._registry()['accounts'] if row['id'] == account_id), None)
        if not row or not row['connected'] or (generation and generation != row['generation']):
            raise DriveAuthError('Tài khoản Google Drive đã ngắt hoặc thay kết nối. Kết nối lại trước khi tiếp tục.')
        return row

    def disconnect(self, account_id):
        _uuid(account_id)
        with _lock(self.directory/'locks'/'registry'):
            registry = self._registry()
            row = next((row for row in registry['accounts'] if row['id'] == account_id), None)
            if not row:
                raise DriveAuthError('Không tìm thấy tài khoản Google Drive đã chọn.')
            reference = row.get('credential_ref')
            row.update(connected=False, generation=uuid.uuid4().hex, credential_ref='')
            atomic_json(self.registry_path, registry)
            if reference:
                self.vault.remove(reference)
        # Upload session references are retained for reconciliation, but no bearer
        # access token remains and every later network request checks this epoch.

    def _raw(self, method, url, *, cancel=None, **kwargs):
        check_cancel(cancel)
        try:
            response = self.session.request(method, url, timeout=(10, 60), allow_redirects=False, **kwargs)
        except requests.RequestException:
            raise DriveError('Chưa kết nối được Google Drive. Phiên được giữ; không tự tạo bản tải mới.',
                             uncertain=method in ('POST', 'PUT')) from None
        check_cancel(cancel)
        return response

    @staticmethod
    def _client_json(path):
        value = _read_json(path, maximum=100_000)
        client = value.get('installed')
        if not isinstance(client, dict) or 'web' in value:
            raise DriveError('Chọn tệp OAuth Client JSON loại Desktop app từ Google Cloud, không dùng Web app.')
        client_id = client.get('client_id', '')
        secret = client.get('client_secret', '')
        if (not isinstance(client_id, str) or not re.fullmatch(r'[A-Za-z0-9._-]+\.apps\.googleusercontent\.com', client_id)
                or not isinstance(secret, str) or len(secret) > 1000 or any(ord(c) < 32 for c in secret)
                or client.get('auth_uri', AUTH_URL) not in (AUTH_URL, 'https://accounts.google.com/o/oauth2/auth')
                or client.get('token_uri', TOKEN_URL) not in (TOKEN_URL, 'https://accounts.google.com/o/oauth2/token')):
            raise DriveError('Tệp OAuth Desktop chưa đúng định dạng Google. Không dùng tệp chứa địa chỉ máy chủ khác.')
        return dict(client_id=client_id, client_secret=secret)

    def connect(self, client_json_path, *, open_browser=webbrowser.open, cancel=None, progress=None, timeout=300):
        """Blocking desktop OAuth: run on a UI thread worker, never on startup."""
        client = self._client_json(client_json_path)
        check_cancel(cancel)
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode('ascii')).digest()).decode('ascii').rstrip('=')
        state = secrets.token_urlsafe(32)
        result = {}

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass  # Never log OAuth codes or query strings.

            def setup(self):
                super().setup()
                self.connection.settimeout(2)

            def do_GET(self):
                try:
                    query = parse_qs(urlsplit(self.path).query, max_num_fields=10)
                except ValueError:
                    self.send_response(400)
                    self.end_headers()
                    return
                valid = (urlsplit(self.path).path == '/oauth2callback'
                         and self.headers.get('Host') == '127.0.0.1:'+str(self.server.server_port)
                         and query.get('state') == [state])
                if not valid or result:
                    self.send_response(400)
                    self.end_headers()
                    return
                if query.get('error'):
                    result['error'] = True
                elif len(query.get('code', [])) == 1 and 0 < len(query['code'][0]) < 4096:
                    result['code'] = query['code'][0]
                else:
                    self.send_response(400)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header('Content-Type', 'text/plain; charset=utf-8')
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                self.wfile.write('Đã nhận phản hồi Google. Quay lại Tèo Dub Studio.'.encode('utf-8'))

        try:
            server = HTTPServer(('127.0.0.1', 0), Handler)
        except OSError:
            raise DriveError('Chưa mở được cổng xác nhận Google trên máy. Kiểm tra tường lửa rồi kết nối lại.') from None
        redirect = 'http://127.0.0.1:'+str(server.server_port)+'/oauth2callback'
        url = AUTH_URL+'?'+urlencode(dict(client_id=client['client_id'], redirect_uri=redirect,
            response_type='code', scope=SCOPE, state=state, code_challenge=challenge,
            code_challenge_method='S256', access_type='offline', prompt='consent select_account'))
        server.timeout = 0.2
        try:
            _progress(progress, 'connecting', 'Đăng nhập Google và cho phép Tèo lưu tệp trong trình duyệt.')
            try:
                opened = open_browser(url)
            except Exception:
                opened = False
            if opened is False:
                raise DriveError('Chưa mở được trình duyệt đăng nhập Google. Kiểm tra trình duyệt mặc định rồi thử lại.')
            deadline = time.monotonic()+max(1, min(float(timeout), 600))
            while not result:
                check_cancel(cancel)
                if time.monotonic() >= deadline:
                    raise DriveError('Chưa nhận xác nhận Google trong thời gian chờ. Bấm Kết nối lại khi sẵn sàng.')
                server.handle_request()
        finally:
            server.server_close()
        if result.get('error'):
            raise DriveAuthError('Bạn chưa cấp quyền Google Drive. Kết nối chưa được lưu.')
        response = self._raw('POST', TOKEN_URL, cancel=cancel, data=dict(client,
            code=result['code'], code_verifier=verifier, redirect_uri=redirect, grant_type='authorization_code'))
        if response.status_code != 200:
            _response_error(response)
        token = _body(response)
        expires = _checked_token(token, require_refresh=True)
        response = self._raw('GET', API_URL+'/about', cancel=cancel,
            headers={'Authorization': 'Bearer '+token['access_token']}, params={'fields': 'user(displayName,emailAddress,permissionId)'})
        if response.status_code != 200:
            _response_error(response)
        user = _body(response).get('user', {})
        if not isinstance(user, dict) or not user.get('permissionId'):
            raise DriveAuthError('Chưa xác nhận được tài khoản Google Drive. Không lưu kết nối chưa rõ tài khoản.')
        account_id = hashlib.sha256((client['client_id']+'|'+str(user['permissionId'])).encode('utf-8')).hexdigest()[:32]
        credential = dict(client, access_token=token['access_token'], refresh_token=token['refresh_token'],
                          expires_at=time.time()+expires, scope=SCOPE)
        row = dict(id=account_id, name=str(user.get('displayName', 'Google Drive'))[:250],
                   email=str(user.get('emailAddress', ''))[:250], connected=True,
                   generation=uuid.uuid4().hex, credential_ref='')
        if any(ord(c) < 32 for field in ('name', 'email') for c in row[field]):
            raise DriveAuthError('Google trả về thông tin tài khoản không hợp lệ. Kết nối chưa được lưu.')
        check_cancel(cancel)
        with _lock(self.directory/'locks'/'registry'):
            check_cancel(cancel)
            registry = self._registry()
            old = next((r for r in registry['accounts'] if r['id'] == account_id), None)
            reference = self.vault.save(account_id, credential)
            row['credential_ref'] = reference
            registry['accounts'] = [r for r in registry['accounts'] if r['id'] != account_id]+[row]
            try:
                atomic_json(self.registry_path, registry)
            except Exception:
                self.vault.remove(reference)
                raise DriveError('Chưa lưu được kết nối Google Drive. Kết nối cũ được giữ.') from None
            if old and old.get('credential_ref'):
                self.vault.remove(old['credential_ref'])
        _progress(progress, 'connected', 'Đã lưu kết nối Google Drive.')
        return {key: row[key] for key in ('id', 'name', 'email', 'connected')}

    def _token(self, account_id, generation, cancel=None):
        row = self._account(account_id, generation)
        token = self.vault.load(account_id, row['credential_ref'])
        if (token.get('scope') != SCOPE or not isinstance(token.get('access_token'), str)
                or not token.get('access_token') or not isinstance(token.get('refresh_token'), str)
                or not token.get('refresh_token')):
            raise DriveAuthError('Kết nối Google Drive không hợp lệ. Kết nối lại trước khi tiếp tục.')
        try:
            expiry = float(token.get('expires_at', 0))
        except (ValueError, TypeError, OverflowError):
            raise DriveAuthError('Hồ sơ kết nối Drive bị lỗi. Kết nối lại trước khi tiếp tục.') from None
        if expiry > time.time()+60:
            return token['access_token']
        # Lock only the refresh publication. Disconnect must be able to invalidate
        # the generation during a request that is already in flight.
        response = self._raw('POST', TOKEN_URL, cancel=cancel, data=dict(
            client_id=token.get('client_id', ''), client_secret=token.get('client_secret', ''),
            refresh_token=token['refresh_token'], grant_type='refresh_token'))
        self._account(account_id, generation)
        if response.status_code != 200:
            _response_error(response)
        fresh = _body(response)
        expires = _checked_token(fresh)
        token.update(access_token=fresh['access_token'], expires_at=time.time()+expires)
        if fresh.get('refresh_token'):
            token['refresh_token'] = fresh['refresh_token']
        with _lock(self.directory/'locks'/'registry'):
            row = self._account(account_id, generation)
            registry = self._registry()
            old_reference = row['credential_ref']
            new_reference = self.vault.save(account_id, token)
            target = next(r for r in registry['accounts'] if r['id'] == account_id)
            target['credential_ref'] = new_reference
            try:
                atomic_json(self.registry_path, registry)
            except Exception:
                self.vault.remove(new_reference)
                raise DriveError('Chưa lưu được kết nối Google Drive đã làm mới.') from None
            self.vault.remove(old_reference)
        return token['access_token']

    def _api(self, account_id, generation, method, url, *, cancel=None, **kwargs):
        token = self._token(account_id, generation, cancel)
        self._account(account_id, generation)
        headers = dict(kwargs.pop('headers', {}), Authorization='Bearer '+token)
        response = self._raw(method, url, cancel=cancel, headers=headers, **kwargs)
        self._account(account_id, generation)
        return response

    def folders(self, account_id):
        row = self._account(account_id)
        generation = row['generation']
        rows = [dict(id='root', name='My Drive (thư mục gốc)')]
        page = ''
        seen = set()
        while True:
            response = self._api(account_id, generation, 'GET', API_URL+'/files', params=dict(
                q="mimeType = 'application/vnd.google-apps.folder' and trashed = false",
                fields='nextPageToken,files(id,name)', pageSize=100, pageToken=page, spaces='drive'))
            if response.status_code != 200:
                _response_error(response)
            body = _body(response)
            for folder in body.get('files', []):
                if isinstance(folder, dict) and _DRIVE_ID.fullmatch(str(folder.get('id', ''))):
                    rows.append(dict(id=folder['id'], name=str(folder.get('name', 'Thư mục'))[:200]))
            page = body.get('nextPageToken', '')
            if not page:
                break
            if not isinstance(page, str) or page in seen or len(seen) >= 100:
                raise DriveError('Danh sách thư mục Drive chưa đầy đủ. Chọn thư mục gốc hoặc thử Làm mới.')
            seen.add(page)
        return rows

    def create_folder(self, account_id, name):
        name = _text(name)
        row = self._account(account_id)
        response = self._api(account_id, row['generation'], 'POST', API_URL+'/files',
            params={'fields': 'id,name'}, json=dict(name=name, mimeType=FOLDER_MIME))
        if response.status_code not in (200, 201):
            _response_error(response, uncertain=True)
        result = _body(response)
        _drive_id(result.get('id'))
        return dict(id=result['id'], name=str(result.get('name', name))[:200])

    def _journal_key(self, config, source_id, sha256):
        if (not isinstance(source_id, str) or not source_id or len(source_id) > 2048
                or any(ord(c) < 32 for c in source_id) or not isinstance(sha256, str) or not _DIGEST.fullmatch(sha256)):
            raise DriveError('Nguồn hoặc dấu kiểm tra video không hợp lệ. Tạo lại hồ sơ thành phẩm trước khi tải.')
        return hashlib.sha256(json.dumps([source_id, sha256, config['account_id'], config['folder_id']],
                                        ensure_ascii=False, separators=(',', ':')).encode('utf-8')).hexdigest()

    def _journal(self, key):
        path = self.directory/'uploads'/(key+'.json')
        if not path.exists():
            return None
        value = _read_json(path)
        if value.get('version') != 1 or value.get('key') != key:
            raise DriveError('Hồ sơ tải Google Drive không hợp lệ. Không tạo bản tải khác để tránh trùng.')
        return value

    def upload_status(self, config, source_id, sha256):
        """Read only public journal fields; never checks Drive or opens a token."""
        config = normalize_drive_config(config)
        if not config['account_id'] or not config['folder_id']:
            return None
        journal = self._journal(self._journal_key(config, source_id, sha256))
        if not journal:
            return None
        self._validate_journal(journal, config, source_id, sha256, journal.get('size'), journal.get('md5'))
        if journal.get('state') == 'uploaded':
            remote = journal.get('confirmed_remote')
            if (not isinstance(remote, dict) or not isinstance(journal.get('receipt'), dict)
                    or journal['receipt'] != self._receipt(remote, journal)):
                raise DriveError('Bằng chứng Google Drive đã lưu bị thiếu hoặc không khớp. Chưa xác nhận thành công.')
        return {key: journal.get(key) for key in ('state', 'message', 'percent', 'transferred', 'size', 'file_id', 'receipt')}

    @staticmethod
    def _session_url(value):
        try:
            parts = urlsplit(value)
            valid = (parts.scheme == 'https' and parts.hostname == 'www.googleapis.com'
                     and parts.port in (None, 443) and not parts.username and not parts.password
                     and parts.path == '/upload/drive/v3/files' and bool(parts.query) and not parts.fragment)
        except (ValueError, TypeError):
            valid = False
        if not valid:
            raise DriveError('Google trả về địa chỉ phiên tải không hợp lệ. Không gửi video hoặc khóa tới địa chỉ đó.')
        return value

    def _save_journal(self, journal):
        atomic_json(self.directory/'uploads'/(journal['key']+'.json'), journal)

    def _remote(self, account_id, generation, file_id, cancel):
        response = self._api(account_id, generation, 'GET', API_URL+'/files/'+quote(_drive_id(file_id), safe=''),
                             cancel=cancel, params={'fields': _REMOTE_FIELDS})
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            _response_error(response)
        return _body(response)

    def _receipt(self, remote, journal):
        properties = remote.get('appProperties') or {}
        sha = remote.get('sha256Checksum')
        checksum_ok = sha == journal['sha256'] if sha else remote.get('md5Checksum') == journal['md5']
        if (remote.get('id') != journal['file_id'] or remote.get('trashed') is not False
                or str(remote.get('size')) != str(journal['size']) or not checksum_ok
                or not isinstance(properties, dict) or properties.get('teodubUpload') != journal['key']
                or properties.get('teodubSHA') != journal['sha256']
                or remote.get('mimeType') != journal['mime_type']
                or journal['parent_id'] not in (remote.get('parents') or [])):
            raise DriveError('Tệp Drive chưa khớp thành phẩm và hồ sơ tải. Không đánh dấu đã lưu hoặc tạo bản trùng.', uncertain=True)
        file_id = _drive_id(remote['id'])
        journal['confirmed_remote'] = {key: remote.get(key) for key in
            ('id', 'mimeType', 'size', 'md5Checksum', 'sha256Checksum', 'appProperties', 'parents', 'trashed')}
        return dict(state='uploaded', file_id=file_id, url='https://drive.google.com/file/d/'+file_id+'/view',
                    name=journal['name'], size=journal['size'], sha256=journal['sha256'],
                    account_id=journal['account_id'], folder_id=journal['folder_id'])

    def upload_video(self, config, *, source_id, video_path, sha256, cancel=None, progress=None):
        config = normalize_drive_config(config)
        if not config['enabled']:
            raise DriveError('Tự lưu Google Drive chưa được bật cho video này.')
        key = self._journal_key(config, source_id, sha256)
        account = self._account(config['account_id'])
        generation = account['generation']
        # Validate the actual bytes before OAuth refresh or any other network call.
        try:
            path = Path(video_path).resolve(strict=True)
            if not path.is_file() or path.suffix.lower() not in ('.mp4', '.mov') or path.stat().st_size <= 0:
                raise ValueError()
            before = path.stat()
            digest, md5 = hashlib.sha256(), hashlib.md5()
            with path.open('rb') as handle:
                for chunk in iter(lambda: handle.read(1024*1024), b''):
                    check_cancel(cancel)
                    digest.update(chunk)
                    md5.update(chunk)
            after = path.stat()
            if (digest.hexdigest() != sha256 or before.st_size != after.st_size
                    or before.st_mtime_ns != after.st_mtime_ns):
                raise DriveError('Video thành phẩm đã thay đổi. Không tải tệp khác với hồ sơ đã lưu.')
        except (OSError, ValueError) as exc:
            if isinstance(exc, DriveError):
                raise
            raise DriveError('Không đọc được video thành phẩm trên máy. Tệp cần là MP4 hoặc MOV còn nguyên vẹn.') from None
        self._account(config['account_id'], generation)
        with _lock(self.directory/'locks'/key):
            journal = self._journal(key)
            if journal:
                self._validate_journal(journal, config, source_id, sha256, after.st_size, md5.hexdigest())
                if journal.get('state') == 'uploaded':
                    receipt = journal.get('receipt')
                    remote = journal.get('confirmed_remote')
                    if (not isinstance(receipt, dict) or not isinstance(remote, dict)
                            or receipt != self._receipt(remote, journal)):
                        raise DriveError('Bằng chứng Google Drive đã lưu không hợp lệ. Không đánh dấu thành công.')
                    _progress(progress, 'uploaded', 'Đã lưu Google Drive.', journal['size'], journal['size'])
                    return dict(receipt)
            else:
                journal = dict(version=1, key=key, source_id=source_id, sha256=sha256, md5=md5.hexdigest(),
                    account_id=config['account_id'], folder_id=config['folder_id'], name=path.name,
                    size=after.st_size, mime_type='video/mp4' if path.suffix.lower() == '.mp4' else 'video/quicktime',
                    state='pending', message='Chờ tải Google Drive.', percent=0, transferred=0,
                    file_id='', parent_id='', session_ref='', receipt=None)
                self._save_journal(journal)
            try:
                receipt = self._upload(journal, path, after, config, generation, cancel, progress)
                journal.update(state='uploaded', message='Đã lưu Google Drive.', transferred=journal['size'], percent=100, receipt=receipt)
                self._save_journal(journal)
                _progress(progress, 'uploaded', journal['message'], journal['size'], journal['size'])
                return receipt
            except Cancelled:
                journal.update(state='paused', message='Đã dừng tải Drive; thành phẩm trên máy được giữ.')
                self._save_journal(journal)
                raise
            except DriveError as exc:
                journal.update(state='needs_reconnect' if isinstance(exc, DriveAuthError) else 'needs_check' if exc.uncertain else 'failed',
                               message=str(exc))
                self._save_journal(journal)
                raise

    @staticmethod
    def _validate_journal(journal, config, source_id, sha256, size, md5):
        fields = dict(source_id=source_id, sha256=sha256, size=size, md5=md5,
                      account_id=config['account_id'], folder_id=config['folder_id'])
        if any(journal.get(k) != value for k, value in fields.items()):
            raise DriveError('Hồ sơ tải không khớp video hoặc tài khoản. Không tạo bản tải trùng.')
        if (type(size) is not int or size <= 0 or not isinstance(md5, str)
                or not re.fullmatch(r'[0-9a-f]{32}', md5)):
            raise DriveError('Hồ sơ kích thước hoặc dấu kiểm tra Drive không hợp lệ.')
        if journal.get('file_id'):
            _drive_id(journal['file_id'])
        if journal.get('parent_id'):
            _drive_id(journal['parent_id'])
            if config['folder_id'] != 'root' and journal['parent_id'] != config['folder_id']:
                raise DriveError('Hồ sơ tải trỏ tới thư mục khác với lựa chọn. Không gửi video.')
        if journal.get('session_ref'):
            _uuid(journal['session_ref'])
        if (type(journal.get('transferred')) is not int or not 0 <= journal['transferred'] <= size
                or not isinstance(journal.get('name'), str) or Path(journal['name']).name != journal['name']
                or journal.get('mime_type') not in ('video/mp4', 'video/quicktime')):
            raise DriveError('Hồ sơ tải Google Drive bị lỗi. Không gửi hoặc tạo bản tải mới.')

    def _upload(self, journal, path, stat, config, generation, cancel, progress):
        account_id = config['account_id']
        _progress(progress, 'uploading', 'Đang đối chiếu và lưu Google Drive.', journal['transferred'], journal['size'])
        if journal['file_id']:
            remote = self._remote(account_id, generation, journal['file_id'], cancel)
            if remote is not None:
                return self._receipt(remote, journal)
        if not journal['parent_id']:
            response = self._api(account_id, generation, 'GET', API_URL+'/files/'+quote(config['folder_id'], safe=''),
                cancel=cancel, params={'fields': 'id,mimeType,trashed,capabilities(canAddChildren)'})
            if response.status_code != 200:
                _response_error(response)
            folder = _body(response)
            if (folder.get('mimeType') != FOLDER_MIME or folder.get('trashed') is not False
                    or not isinstance(folder.get('capabilities'), dict)
                    or folder.get('capabilities', {}).get('canAddChildren') is not True
                    or (config['folder_id'] != 'root' and folder.get('id') != config['folder_id'])):
                raise DriveError('Thư mục Google Drive chưa cho phép thêm tệp. Chọn hoặc tạo thư mục khác trong Tèo.')
            journal['parent_id'] = _drive_id(folder.get('id'))
            self._save_journal(journal)
        if not journal['file_id']:
            response = self._api(account_id, generation, 'GET', API_URL+'/files/generateIds',
                cancel=cancel, params={'count': 1, 'space': 'drive', 'type': 'files'})
            if response.status_code != 200:
                _response_error(response)
            ids = _body(response).get('ids', [])
            if not isinstance(ids, list) or len(ids) != 1:
                raise DriveError('Google Drive chưa cấp mã tệp để chống tải trùng. Chưa gửi video.')
            journal['file_id'] = _drive_id(ids[0])
            self._save_journal(journal)  # Durable ID BEFORE any file-creation request.
        session_url = ''
        if journal['session_ref']:
            value = self.vault.load('upload-'+journal['key'], journal['session_ref'])
            session_url = self._session_url(value.get('url'))
            response = self._api(account_id, generation, 'PUT', session_url, cancel=cancel,
                headers={'Content-Length': '0', 'Content-Range': 'bytes */'+str(journal['size'])}, data=b'')
            if response.status_code in (200, 201):
                remote = self._remote(account_id, generation, journal['file_id'], cancel)
                if remote is None:
                    raise DriveError('Drive báo tải xong nhưng chưa đọc được tệp. Giữ phiên để kiểm tra lại.', uncertain=True)
                return self._receipt(remote, journal)
            if response.status_code == 308:
                journal['transferred'] = self._offset(response, journal['size'])
                self._save_journal(journal)
            elif response.status_code in (404, 410):
                # Expired sessions are restarted with the SAME pre-generated ID.
                self.vault.remove(journal['session_ref'])
                journal.update(session_ref='', transferred=0, percent=0)
                self._save_journal(journal)
                session_url = ''
            else:
                _response_error(response, uncertain=True)
        if not session_url:
            metadata = dict(id=journal['file_id'], name=journal['name'], parents=[journal['parent_id']],
                mimeType=journal['mime_type'], appProperties={'teodubUpload': journal['key'], 'teodubSHA': journal['sha256']})
            response = self._api(account_id, generation, 'POST', UPLOAD_URL, cancel=cancel,
                params={'uploadType': 'resumable', 'fields': _REMOTE_FIELDS}, json=metadata,
                headers={'X-Upload-Content-Type': journal['mime_type'], 'X-Upload-Content-Length': str(journal['size'])})
            if response.status_code not in (200, 201):
                _response_error(response, uncertain=True)
            session_url = self._session_url(response.headers.get('Location'))
            reference = self.vault.save('upload-'+journal['key'], {'url': session_url})
            journal.update(session_ref=reference, transferred=0, percent=0)
            self._save_journal(journal)
        with path.open('rb') as handle:
            while journal['transferred'] < journal['size']:
                check_cancel(cancel)
                current = path.stat()
                if current.st_size != stat.st_size or current.st_mtime_ns != stat.st_mtime_ns:
                    raise DriveError('Video thay đổi trong khi tải. Phiên được giữ và chưa đánh dấu đã lưu.')
                offset = journal['transferred']
                handle.seek(offset)
                chunk = handle.read(min(CHUNK_SIZE, journal['size']-offset))
                if not chunk:
                    raise DriveError('Video bị thiếu dữ liệu trong lúc tải. Chưa đánh dấu đã lưu.')
                response = self._api(account_id, generation, 'PUT', session_url, cancel=cancel,
                    headers={'Content-Type': journal['mime_type'], 'Content-Length': str(len(chunk)),
                             'Content-Range': f'bytes {offset}-{offset+len(chunk)-1}/{journal["size"]}'}, data=chunk)
                if response.status_code in (200, 201):
                    remote = self._remote(account_id, generation, journal['file_id'], cancel)
                    if remote is None:
                        raise DriveError('Drive chưa xác nhận tệp sau khi nhận video. Giữ phiên để đối chiếu.', uncertain=True)
                    return self._receipt(remote, journal)
                if response.status_code != 308:
                    _response_error(response, uncertain=True)
                transferred = self._offset(response, journal['size'])
                if not offset < transferred <= offset+len(chunk):
                    raise DriveError('Tiến độ Drive trả về không khớp khối đã gửi. Giữ phiên để kiểm tra.', uncertain=True)
                journal.update(transferred=transferred, percent=int(100*transferred/journal['size']),
                               state='uploading', message='Đang lưu Google Drive.')
                self._save_journal(journal)
                _progress(progress, 'uploading', journal['message'], transferred, journal['size'])
        # A 308 acknowledging every byte is not proof that the file was committed.
        remote = self._remote(account_id, generation, journal['file_id'], cancel)
        if remote is None:
            raise DriveError('Đã gửi đủ dữ liệu nhưng Drive chưa xác nhận tệp. Tiếp tục để đối chiếu.', uncertain=True)
        return self._receipt(remote, journal)

    @staticmethod
    def _offset(response, size):
        value = response.headers.get('Range', '')
        if not value:
            return 0
        match = re.fullmatch(r'bytes=0-([0-9]+)', value)
        if not match or not 0 <= int(match[1]) < size:
            raise DriveError('Google Drive trả về mốc tải không hợp lệ. Giữ phiên để kiểm tra.', uncertain=True)
        return int(match[1])+1
