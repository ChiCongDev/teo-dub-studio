"""Explicit Page API calls, encrypted credentials, and no blind POST retries."""
from contextlib import ExitStack, contextmanager
import io
import json
import mimetypes
import os
from pathlib import Path
import re
import uuid

from teodub.common import Cancelled, check_cancel
from teodub.page_activity import PageActivity
from teodub.pipeline import _job_lock


DEFAULT_API_VERSION = 'v26.0'  # Meta's official Python SDK apiconfig, checked 2026-10-03.


class MetaError(ValueError):
    def __init__(self, message, *, uncertain=False, code=None):
        super().__init__(message)
        self.uncertain = uncertain
        self.code = code


class PageCredentials:
    def __init__(self, directory):
        self.directory = Path(directory)/'credentials'

    def path(self, reference):
        if not re.fullmatch(r'[0-9a-f]{32}', str(reference)):
            raise ValueError('Mã kết nối Page không hợp lệ; cần kết nối lại.')
        return self.directory/(reference+'.bin')

    @staticmethod
    def entropy(account_id, page_key):
        return ('TeoDubStudio.PageAPI.'+account_id+'.'+page_key).encode('ascii')

    def save(self, account_id, page_key, token):
        if os.name != 'nt':
            raise ValueError('Lưu khóa API hiện hỗ trợ mã hóa Windows. Không lưu khóa dạng văn bản.')
        import win32crypt
        import pywintypes
        try:
            sealed = win32crypt.CryptProtectData(token.encode('utf-8'), 'Teo Page API',
                self.entropy(account_id, page_key), None, None, 0x01)
        except pywintypes.error:
            raise ValueError('Windows chưa mã hóa được khóa. Kết nối chưa được lưu; không lưu khóa dạng văn bản.') from None
        reference = uuid.uuid4().hex
        self.directory.mkdir(parents=True, exist_ok=True)
        with self.path(reference).open('xb') as handle:
            handle.write(sealed)
        return reference

    def load(self, account_id, page_key, reference):
        import win32crypt
        import pywintypes
        try:
            blob = self.path(reference).read_bytes()
            _, plain = win32crypt.CryptUnprotectData(blob, self.entropy(account_id, page_key), None, None, 0x01)
            return plain.decode('utf-8')
        except (OSError, ValueError, pywintypes.error):
            raise ValueError('Không mở được khóa API trên máy/tài khoản Windows này. Kết nối Page lại trong Tèo.') from None

    def remove(self, reference):
        self.path(reference).unlink(missing_ok=True)


class MultipartStream(io.RawIOBase):
    """Bounded-memory multipart upload; never reads an entire video into RAM."""
    def __init__(self, fields, handle, filename, *, cancel=None):
        self.cancel = cancel
        boundary = 'teo'+uuid.uuid4().hex
        self.content_type = 'multipart/form-data; boundary='+boundary
        prefix = b''
        for key, value in fields.items():
            prefix += (f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n').encode('utf-8')
        mime = mimetypes.guess_type(filename)[0] or 'application/octet-stream'
        # Generic upload name avoids sending local paths and unescaped user filenames.
        generic = 'upload'+Path(filename).suffix.lower()
        prefix += (f'--{boundary}\r\nContent-Disposition: form-data; name="source"; filename="{generic}"\r\n'
                   f'Content-Type: {mime}\r\n\r\n').encode('utf-8')
        suffix = f'\r\n--{boundary}--\r\n'.encode('ascii')
        self.parts = [io.BytesIO(prefix), handle, io.BytesIO(suffix)]
        self.index = 0
        self.position = 0
        self.length = len(prefix)+os.fstat(handle.fileno()).st_size+len(suffix)

    def __len__(self):
        return self.length

    def tell(self):
        return self.position

    def read(self, size=-1):
        check_cancel(self.cancel)
        size = 65536 if size is None or size < 0 else size
        chunks, remaining = [], size
        while remaining > 0 and self.index < len(self.parts):
            block = self.parts[self.index].read(remaining)
            if block:
                chunks.append(block)
                remaining -= len(block)
            else:
                self.index += 1
        result = b''.join(chunks)
        self.position += len(result)
        return result


class MetaPageClient:
    def __init__(self, token, version=DEFAULT_API_VERSION, *, session=None):
        token = str(token).strip()
        if not token or len(token) > 16000 or any(ord(c) < 33 for c in token):
            raise ValueError('Nhập khóa truy cập Page hợp lệ trong cửa sổ kết nối của Tèo.')
        if not re.fullmatch(r'v[0-9]{1,3}\.[0-9]', version):
            raise ValueError('Phiên bản API cần dạng v26.0, theo phiên bản App Meta của bạn.')
        self.token, self.version = token, version
        if session is None:
            import requests
            session = requests.Session()
        self.session = session

    def close(self):
        self.session.close()

    def request(self, method, node, *, edge='', data=None, fields=None, upload=None, cancel=None):
        import requests
        if method not in {'GET', 'POST'} or not re.fullmatch(r'(?:me|[0-9]+(?:_[0-9]+)?)', str(node)) or edge not in {'','feed','photos','videos','published_posts'}:
            raise ValueError('Địa chỉ thao tác Page không hợp lệ.')
        check_cancel(cancel)
        url = 'https://graph.facebook.com/'+self.version+'/'+str(node)+('/'+edge if edge else '')
        headers = {'Authorization': 'Bearer '+self.token}
        kwargs = dict(headers=headers, timeout=(15, 120 if upload else 45), allow_redirects=False)
        with ExitStack() as resources:
            if method == 'GET':
                kwargs['params'] = dict(data or {})
                if fields:
                    kwargs['params']['fields'] = fields
            elif upload:
                handle = resources.enter_context(Path(upload).open('rb'))
                body = MultipartStream(data or {}, handle, str(upload), cancel=cancel)
                headers.update({'Content-Type': body.content_type, 'Content-Length': str(len(body))})
                kwargs['data'] = body
            else:
                kwargs['data'] = data or {}
            try:
                response = self.session.request(method, url, **kwargs)
            except (requests.RequestException, Cancelled, OSError):
                raise MetaError('Kết nối bị ngắt hoặc đã dừng. '+
                    ('Chưa rõ Meta đã nhận bài hay chưa; kiểm tra trước khi gửi lại.' if method == 'POST'
                     else 'Chưa cập nhật được dữ liệu; thông tin đã lưu được giữ.'), uncertain=method == 'POST') from None
        try:
            payload = response.json()
        except (ValueError, TypeError):
            raise MetaError('Meta chưa trả kết quả hợp lệ. Không tự gửi lại bài.', uncertain=method == 'POST') from None
        finally:
            response.close()
        if not isinstance(payload, dict) or 300 <= response.status_code < 400:
            raise MetaError('Meta trả kết quả hoặc điều hướng không hợp lệ; không chuyển khóa sang địa chỉ khác.',
                            uncertain=method == 'POST')
        error = payload.get('error')
        if error or response.status_code >= 400:
            error = error if isinstance(error, dict) else {}
            code = error.get('code')
            message = str(error.get('message', 'Meta chưa xử lý thành công yêu cầu này.')).replace(self.token, '[đã ẩn khóa]')
            hint = (' Kết nối Page lại vì khóa/phiên đã hết hạn.' if code == 190 else
                    ' Kiểm tra quyền của App và quyền trên Page.' if code in {10, 200} else
                    ' Hạn mức API đang bị giới hạn; thử lại sau, Tèo không tự gửi lại.' if code in {4, 17, 32, 613} else '')
            raise MetaError('Meta'+(f' ({code})' if code is not None else '')+': '+message[:1500]+hint,
                            uncertain=method == 'POST' and (response.status_code >= 500 or not error), code=code)
        return payload

    def verify_page(self, page_id):
        if not re.fullmatch(r'[0-9]+', str(page_id)):
            raise ValueError('Nhập Page ID bằng số; đây là mã của Page, không phải mã tài khoản cá nhân.')
        data = self.request('GET', 'me', fields='id,name,category')
        if str(data.get('id')) != str(page_id) or 'category' not in data or not data.get('name'):
            raise ValueError('Khóa không thuộc đúng Page ID này hoặc là khóa tài khoản cá nhân. Chọn khóa Page rồi kết nối lại.')
        return data

    def page_info(self, page_id):
        data = self.request('GET', page_id, fields='id,name,link,about,category,fan_count,followers_count')
        if str(data.get('id')) != str(page_id):
            raise ValueError('API trả thông tin của Page khác; chưa lưu dữ liệu.')
        return dict(name=data.get('name'), url=data.get('link'), description=data.get('about'),
                    category=data.get('category'), likes=data.get('fan_count'), followers=data.get('followers_count'))

    def post_info(self, external_id, kind):
        fields = ('id,published,permalink_url,status,views' if kind == 'video' else
                  'id,is_published,permalink_url,created_time,likes.limit(0).summary(true),comments.limit(0).summary(true),shares')
        return self.request('GET', external_id, fields=fields)

    def recent_posts(self, page_id):
        result = self.request('GET', page_id, edge='published_posts', data={'limit':25},
            fields='id,message,created_time,permalink_url,likes.limit(0).summary(true),comments.limit(0).summary(true),shares')
        clean = []
        for row in result.get('data', []):
            external_id = str(row.get('id',''))
            if not re.fullmatch(re.escape(page_id)+r'_[0-9]+', external_id):
                continue
            clean.append(dict(id=external_id, body=str(row.get('message',''))[:60000], created_time=row.get('created_time',''),
                url=row.get('permalink_url',''), likes=row.get('likes',{}).get('summary',{}).get('total_count'),
                comments=row.get('comments',{}).get('summary',{}).get('total_count'), shares=row.get('shares',{}).get('count')))
        return clean


class PageApiService:
    def __init__(self, data_dir, *, client_factory=MetaPageClient, credentials=None):
        self.store = PageActivity(data_dir)
        self.vault = credentials or PageCredentials(self.store.accounts.directory)
        self.client_factory = client_factory

    def connect(self, account_id, page_key, page_id, token, version):
        target = self.store.page(account_id, page_key)
        if target.get('page_id') and target['page_id'] != page_id:
            raise ValueError('Page ID khác với Page đã lưu. Sửa đúng Page trong danh sách trước khi kết nối.')
        client = self.client_factory(token, version)
        try:
            verified = client.verify_page(page_id)
        finally:
            client.close()
        old = self.store.connection(account_id, page_key)
        reference = self.vault.save(account_id, page_key, token)
        try:
            if not target.get('page_id'):
                self.store.accounts.save_page(account_id, target['name'], target['url'], page_id, record_id=page_key)
            self.store.save_connection(account_id, page_key, page_id=page_id, api_version=version,
                                       credential_ref=reference, name=verified['name'])
        except Exception:
            self.vault.remove(reference)
            raise
        if old:
            try:
                self.vault.remove(old['credential_ref'])
            except OSError:
                pass  # The verified replacement is committed; an unused sealed file can remain.
        return verified

    def disconnect(self, account_id, page_key):
        connection = self.store.connection(account_id, page_key)
        self.store.disconnect(account_id, page_key)
        if connection:
            self.vault.remove(connection['credential_ref'])

    @contextmanager
    def client(self, account_id, page_key):
        connection = self.store.connection(account_id, page_key)
        if not connection:
            raise ValueError('Page chưa kết nối API. Bấm Kết nối API trong phần quản lý Page.')
        target = self.store.page(account_id, page_key)
        if target.get('page_id') != connection['page_id']:
            raise ValueError('Page ID đã thay đổi; kết nối lại trước khi thao tác.')
        token = self.vault.load(account_id, page_key, connection['credential_ref'])
        client = self.client_factory(token, connection['api_version'])
        try:
            yield client, connection
        except MetaError as exc:
            if exc.code == 190:
                self.store.connection_notice(account_id, page_key, 'reconnect', str(exc))
            raise
        finally:
            client.close()

    def refresh_page(self, account_id, page_key):
        with self.client(account_id, page_key) as (client, connection):
            result = client.page_info(connection['page_id'])
            self.store.observe(account_id, page_key, result)
            self.store.connection_notice(account_id, page_key, 'connected', 'Đã cập nhật thông tin qua API Meta.')
            message = 'Đã cập nhật thông tin Page.'
            try:
                posts = client.recent_posts(connection['page_id'])
                self.store.save_remote_posts(account_id, page_key, posts)
                message += f' Đã đọc {len(posts)} bài trong lượt gần nhất (tối đa 25).'
            except MetaError as exc:
                if exc.code == 190:
                    self.store.connection_notice(account_id, page_key, 'reconnect', str(exc))
                message += ' Chưa đọc được bài trên Facebook: '+str(exc)
            return dict(info=result, message=message)

    def publish(self, account_id, page_key, post_id, *, cancel=None, progress=None):
        self.store.post(account_id, page_key, post_id)
        if not re.fullmatch(r'[0-9a-f]{32}', post_id):
            raise ValueError('Mã bản nháp không hợp lệ.')
        lock = self.store.accounts.directory/'post-locks'/post_id
        lock.mkdir(parents=True, exist_ok=True)
        with _job_lock(lock):
            return self._publish(account_id, page_key, post_id, cancel=cancel, progress=progress)

    def _publish(self, account_id, page_key, post_id, *, cancel=None, progress=None):
        post = self.store.post(account_id, page_key, post_id)
        if post.get('delivery_mode', 'api') != 'api':
            raise ValueError('Bài này dùng Trình duyệt web; không gửi qua API Meta.')
        if post['external_id']:
            raise ValueError('Bài đã có mã Meta; dùng Kiểm tra kết quả, không gửi lại.')
        self.store.verify_media(post)
        post = self.store.transition(account_id, page_key, post_id, 'preparing', 'Đang kiểm tra Page và tệp trước khi gửi.',
                                     expected={'draft','failed'})
        submitted = False
        try:
            with self.client(account_id, page_key) as (client, connection):
                check_cancel(cancel)
                if post['target_id'] and post['target_id'] != connection['page_id']:
                    raise ValueError('Kết nối đã đổi sang Page khác với bản nháp; chưa gửi bài.')
                client.verify_page(connection['page_id'])
                self.store.bind_target(account_id, page_key, post_id, connection['page_id'])
                assets = list(post['remote_assets'])
                if post['kind'] == 'image':
                    for saved in post['media'][len(assets):]:
                        check_cancel(cancel)
                        submitted = True
                        if progress:
                            progress(f'Đang tải ảnh {len(assets)+1}/{len(post["media"])}…')
                        result = client.request('POST', connection['page_id'], edge='photos',
                            data={'published':'false'}, upload=saved['path'], cancel=cancel)
                        photo_id = str(result.get('id',''))
                        if not re.fullmatch(r'[0-9]+', photo_id):
                            raise MetaError('Ảnh đã gửi nhưng chưa nhận được mã; không tự tải lại.', uncertain=True)
                        assets.append(photo_id)
                        self.store.record_assets(account_id, page_key, post_id, assets)
                        submitted = False
                check_cancel(cancel)
                self.store.transition(account_id, page_key, post_id, 'submitting', 'Đang gửi bài lên Meta; không gửi lặp.',
                                      expected={'preparing'})
                if progress:
                    progress('Đang gửi bài…' if post['kind'] != 'video' else 'Đang tải video lên Page…')
                submitted = True
                if post['kind'] == 'video':
                    result = client.request('POST', connection['page_id'], edge='videos',
                        data={'description':post['body'], 'published':'true'}, upload=post['media'][0]['path'], cancel=cancel)
                else:
                    fields = {'message':post['body'], 'published':'true'}
                    if assets:
                        fields['attached_media'] = json.dumps([{'media_fbid':value} for value in assets])
                    result = client.request('POST', connection['page_id'], edge='feed', data=fields, cancel=cancel)
                external_id = str(result.get('id',''))
                expected_id = r'[0-9]+' if post['kind'] == 'video' else re.escape(connection['page_id'])+r'_[0-9]+'
                if not re.fullmatch(expected_id, external_id):
                    raise MetaError('Meta chưa trả mã bài đã gửi; kiểm tra trên Facebook trước khi gửi lại.', uncertain=True)
                self.store.transition(account_id, page_key, post_id, 'processing',
                    'Meta đã nhận mã bài/video; đang chờ kiểm tra kết quả.', expected={'submitting'}, external_id=external_id)
            return self.check_post(account_id, page_key, post_id)
        except Exception as exc:
            current = self.store.post(account_id, page_key, post_id)
            if current['state'] == 'processing':
                self.store.transition(account_id, page_key, post_id, 'processing',
                    'Meta đã nhận bài, chưa kiểm tra được kết quả: '+str(exc), expected={'processing'})
                return self.store.post(account_id, page_key, post_id)
            uncertain = isinstance(exc, MetaError) and exc.uncertain or submitted and not isinstance(exc, MetaError)
            self.store.transition(account_id, page_key, post_id, 'unknown' if uncertain else 'failed',
                                  str(exc), expected={'preparing','submitting'})
            raise

    def check_post(self, account_id, page_key, post_id):
        post = self.store.post(account_id, page_key, post_id)
        if post.get('delivery_mode', 'api') != 'api':
            raise ValueError('Bài này dùng Trình duyệt web; xem kết quả trong Chrome và lịch đăng của Tèo.')
        if not post['external_id']:
            if post['state'] in {'preparing','submitting'}:
                lock = self.store.accounts.directory/'post-locks'/post_id
                lock.mkdir(parents=True, exist_ok=True)
                with _job_lock(lock):
                    self.store.transition(account_id, page_key, post_id, 'unknown',
                        'Phiên gửi trước không còn giữ khóa và chưa lưu được mã bài. Kiểm tra Page trước; không tự gửi lại.',
                        expected={'preparing','submitting'})
            raise ValueError('Chưa có mã bài từ Meta. Mở Page kiểm tra trước; Tèo không tự gửi lại bài chưa rõ kết quả.')
        with self.client(account_id, page_key) as (client, connection):
            if post['target_id'] and post['target_id'] != connection['page_id']:
                raise ValueError('Kết nối đã đổi Page so với bài đã gửi. Giữ lịch sử và kết nối lại đúng Page để kiểm tra.')
            result = client.post_info(post['external_id'], post['kind'])
        if str(result.get('id')) != post['external_id']:
            raise ValueError('Meta trả mã bài khác; giữ trạng thái để đối chiếu.')
        metrics = {}
        for key in ('likes','comments'):
            value = result.get(key,{}).get('summary',{}).get('total_count')
            if value is not None:
                metrics[key] = value
        if result.get('shares',{}).get('count') is not None:
            metrics['shares'] = result['shares']['count']
        for key in ('views','created_time'):
            if result.get(key) is not None:
                metrics[key] = result[key]
        self.store.record_metrics(account_id, page_key, post_id, metrics)
        published = result.get('published' if post['kind'] == 'video' else 'is_published') is True
        if post['kind'] == 'video':
            status = result.get('status') or {}
            if status.get('video_status') == 'error':
                self.store.transition(account_id, page_key, post_id, 'unknown',
                    'Meta báo lỗi xử lý video. Mã video được giữ; kiểm tra video trên Page trước, không tự tải lại.',
                    expected={'processing','unknown','published'})
                return self.store.post(account_id, page_key, post_id)
            phase = status.get('publishing_phase') or {}
            if phase.get('status'):
                published = phase['status'] == 'complete'
            elif status.get('video_status'):
                published = published and status['video_status'] == 'ready'
        url = result.get('permalink_url')
        if published and url:
            self.store.transition(account_id, page_key, post_id, 'published', 'Meta đã xác nhận bài/video đã đăng.',
                expected={'processing','published','unknown'}, external_url=url, external_id=post['external_id'])
        elif post['state'] != 'published':
            self.store.transition(account_id, page_key, post_id, 'processing',
                'Meta chưa xác nhận đăng xong. Video có thể đang xử lý; dùng Kiểm tra kết quả, không gửi lại.',
                expected={'processing','unknown'})
        return self.store.post(account_id, page_key, post_id)
