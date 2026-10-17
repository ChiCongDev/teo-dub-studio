"""Local account/Page registry. Browser sessions stay in Chrome's own profile."""
from contextlib import contextmanager
from datetime import datetime
import json
from pathlib import Path
import re
from urllib.parse import parse_qs, unquote, urlencode, urlsplit, urlunsplit
import uuid

from teodub.common import atomic_json
from teodub.pipeline import _job_lock


FACEBOOK_HOME = 'https://www.facebook.com/'
FACEBOOK_PAGES = 'https://www.facebook.com/pages/?category=your_pages'
BUSINESS_SUITE = 'https://business.facebook.com/latest/home'
META_APPS = 'https://developers.facebook.com/apps/'
META_EXPLORER = 'https://developers.facebook.com/tools/explorer/'
_HOSTS = {'facebook.com', 'www.facebook.com', 'm.facebook.com', 'business.facebook.com'}


def facebook_url(value, *, page=False):
    """Accept Facebook links only; remove tracking and never persist credentials."""
    value = str(value).strip()
    if not value or any(ord(char) < 32 for char in value) or '\\' in value:
        raise ValueError('Nhập link Facebook hợp lệ, ví dụ https://www.facebook.com/tenpage.')
    if '://' not in value:
        value = 'https://' + value
    try:
        parts = urlsplit(value)
        valid = (parts.scheme in ('https', 'http') and parts.hostname in _HOSTS
                 and parts.username is None and parts.password is None and parts.port in (None, 443))
    except ValueError:
        valid = False
    if not valid:
        raise ValueError('Chỉ nhận link trên facebook.com; không dùng link có thông tin đăng nhập.')
    path = parts.path or '/'
    if page and (parts.hostname == 'business.facebook.com' or path == '/'
                 or path.rstrip('/').lower() in ('/login', '/login.php', '/recover', '/checkpoint')):
        raise ValueError('Nhập link của Page, không dùng trang đăng nhập hoặc Business Suite.')
    query = ''
    if path.rstrip('/').lower() == '/pages' and parse_qs(parts.query).get('category') == ['your_pages']:
        query = 'category=your_pages'
        path = '/pages/'
    if path.rstrip('/').lower() == '/profile.php':
        page_id = parse_qs(parts.query).get('id', [''])[0]
        if not re.fullmatch(r'[0-9]+', page_id):
            raise ValueError('Link profile.php cần có id bằng số.')
        query = urlencode({'id': page_id})
    post_keys = ({'story_fbid','id'} if path in {'/permalink.php','/story.php'} else
                 {'fbid'} if path == '/photo.php' else {'v'} if path.rstrip('/') in {'/watch','/video.php'} else set())
    if post_keys:
        query = urlencode({key: values[0] for key, values in parse_qs(parts.query).items()
                           if key in post_keys and re.fullmatch(r'[0-9]+', values[0])})
    host = 'business.facebook.com' if parts.hostname == 'business.facebook.com' else 'www.facebook.com'
    clean_path = '/pages/' if query == 'category=your_pages' else path.rstrip('/') or '/'
    return urlunsplit(('https', host, clean_path, query, ''))


def facebook_browser_url(value):
    """Allow the two setup destinations without treating them as saved Page URLs."""
    if str(value).rstrip('/') in {META_APPS.rstrip('/'), META_EXPLORER.rstrip('/')}:
        return str(value).rstrip('/')+'/'
    if urlsplit(str(value)).path.rstrip('/') == '/content/insights':
        return facebook_result_url(str(value))
    return facebook_url(value)


def facebook_post_url(value):
    """Keep essential numeric IDs in a post permalink, while dropping tracking."""
    clean = facebook_url(value)
    parsed = urlsplit(clean)
    if parsed.hostname != 'www.facebook.com' or parsed.path == '/':
        raise ValueError('Cần link bài/video trên Facebook, không dùng trang chủ hoặc Business Suite.')
    source = urlsplit(value if '://' in value else 'https://'+value)
    accepted = {'story_fbid', 'id'} if parsed.path in {'/permalink.php','/story.php'} else \
               {'fbid'} if parsed.path == '/photo.php' else {'v'} if parsed.path.rstrip('/') in {'/watch','/video.php'} else set()
    query = {key: values[0] for key, values in parse_qs(source.query).items()
             if key in accepted and re.fullmatch(r'[0-9]+', values[0])}
    if accepted and not query:
        raise ValueError('Link bài/video thiếu mã số cần thiết; sao chép đầy đủ địa chỉ trên Facebook.')
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query) if accepted else parsed.query, ''))


def facebook_result_url(value):
    """Keep a verified result reference, including an opaque Insights ID.

    This validates an address only. Callers still need independent, observed
    owner/content/video evidence before recording a publication as confirmed.
    Manual post URLs retain the separate ``facebook_post_url`` policy.
    """
    if not isinstance(value, str) or not value or '\\' in value or any(ord(char) < 32 for char in value):
        raise ValueError('Link kết quả Facebook không hợp lệ.')
    value = value.strip()
    try:
        parsed = urlsplit(value)
        insights = parsed.path.rstrip('/') == '/content/insights'
        if insights:
            identifiers = parse_qs(parsed.query, keep_blank_values=True).get('content_id', [])
            if (parsed.scheme != 'https' or parsed.hostname not in {'facebook.com', 'www.facebook.com', 'm.facebook.com'}
                    or parsed.username is not None or parsed.password is not None or parsed.port not in (None, 443)
                    or len(identifiers) != 1 or not identifiers[0] or len(identifiers[0]) > 2048
                    or any(ord(char) <= 32 or ord(char) == 127 for char in identifiers[0])):
                raise ValueError('Link Insights cần đúng một content_id hợp lệ trên Facebook.')
            return urlunsplit(('https', 'www.facebook.com', '/content/insights/',
                              urlencode({'content_id': identifiers[0]}), ''))
        if parsed.path.startswith('/content/insights'):
            raise ValueError('Đường dẫn Insights không hợp lệ.')
    except ValueError:
        raise ValueError('Link kết quả Facebook không hợp lệ hoặc thiếu mã nội dung.') from None
    return facebook_post_url(value)


def _name(value, label):
    value = str(value).strip()
    if not value or len(value) > 150 or any(ord(char) < 32 for char in value):
        raise ValueError(f'{label} cần từ 1 đến 150 ký tự, trên một dòng.')
    return value


def _id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{32}', value):
        raise ValueError('Mã hồ sơ Facebook không hợp lệ; dữ liệu được giữ để kiểm tra.')
    return value


def _page_values(name, url, page_id):
    name, url = _name(name, 'Tên Page'), facebook_url(url, page=True)
    page_id = str(page_id).strip()
    if page_id and not re.fullmatch(r'[0-9]+', page_id):
        raise ValueError('Page ID chỉ gồm chữ số; có thể để trống nếu chưa biết.')
    return dict(name=name, url=url, page_id=page_id)


def _now():
    return datetime.now().astimezone().isoformat(timespec='seconds')


class FacebookAccounts:
    def __init__(self, data_dir):
        self.directory = Path(data_dir).resolve()/'page-publishing'
        self.path = self.directory/'accounts.json'

    def load(self):
        if not self.path.exists():
            return dict(version=1, selected_account_id='', accounts=[], pages=[])
        try:
            value = json.loads(self.path.read_text(encoding='utf-8'))
            if (value.get('version') != 1 or not isinstance(value.get('accounts'), list)
                    or not isinstance(value.get('pages'), list)):
                raise ValueError('Định dạng không được hỗ trợ.')
            accounts, pages = set(), set()
            for row in value['accounts']:
                key = _id(row['id'])
                if key in accounts:
                    raise ValueError('Trùng mã tài khoản.')
                _name(row['name'], 'Tên tài khoản')
                accounts.add(key)
            for row in value['pages']:
                key = _id(row['id'])
                if key in pages or row['account_id'] not in accounts:
                    raise ValueError('Page không có tài khoản hoặc trùng mã.')
                _page_values(row['name'], row['url'], row.get('page_id', ''))
                pages.add(key)
            if value.get('selected_account_id', '') not in accounts | {''}:
                raise ValueError('Không tìm thấy tài khoản đã chọn.')
            return value
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise ValueError('Chưa đọc được danh sách Facebook; không ghi đè dữ liệu. '+str(exc)) from None

    @contextmanager
    def _edit(self):
        lock = self.directory/'registry-lock'
        lock.mkdir(parents=True, exist_ok=True)
        with _job_lock(lock):
            value = self.load()
            yield value
            atomic_json(self.path, value)

    def add_account(self, name):
        name = _name(name, 'Tên tài khoản')
        with self._edit() as value:
            if any(row['name'].casefold() == name.casefold() for row in value['accounts']):
                raise ValueError('Tên tài khoản đã có. Chọn tài khoản đó để mở lại phiên đã lưu.')
            row = dict(id=uuid.uuid4().hex, name=name, created_at=_now())
            value['accounts'].append(row)
            value['selected_account_id'] = row['id']
        return row

    def rename_account(self, account_id, name):
        name = _name(name, 'Tên tài khoản')
        with self._edit() as value:
            row = self._account(value, account_id)
            if any(other['id'] != account_id and other['name'].casefold() == name.casefold()
                   for other in value['accounts']):
                raise ValueError('Tên tài khoản đã được sử dụng.')
            row['name'] = name
        return row

    @staticmethod
    def _account(value, account_id):
        _id(account_id)
        row = next((row for row in value['accounts'] if row['id'] == account_id), None)
        if row is None:
            raise ValueError('Không còn tài khoản này trong danh sách. Bấm Làm mới.')
        return row

    def select_account(self, account_id):
        with self._edit() as value:
            self._account(value, account_id)
            value['selected_account_id'] = account_id

    def profile_directory(self, account_id):
        self._account(self.load(), account_id)
        # Names can change; the immutable account ID always keeps the same profile.
        return self.directory/'browser-profiles'/_id(account_id)/'chrome'

    def save_page(self, account_id, name, url, page_id='', *, record_id=None):
        fields = _page_values(name, url, page_id)
        with self._edit() as value:
            self._account(value, account_id)
            row = None
            if record_id is not None:
                row = next((item for item in value['pages'] if item['id'] == record_id
                            and item['account_id'] == account_id), None)
                if row is None:
                    raise ValueError('Không còn Page này trong tài khoản. Bấm Làm mới.')
            for other in value['pages']:
                if other['account_id'] != account_id or other['id'] == record_id:
                    continue
                if (other['url'].casefold() == fields['url'].casefold()
                        or fields['page_id'] and fields['page_id'] == other.get('page_id')):
                    raise ValueError('Page này đã có trong tài khoản; chọn Sửa Page để cập nhật.')
            if row is None:
                row = dict(id=uuid.uuid4().hex, account_id=account_id, created_at=_now())
                value['pages'].append(row)
            row.update(fields, source='manual', updated_at=_now())
        return row

    def save_page_link(self, account_id, url, name=''):
        """Save a user-supplied Page address locally; no browser/login lookup."""
        url = facebook_url(url, page=True)
        parts = urlsplit(url)
        bits = parts.path.strip('/').split('/')
        excluded = {'pages', 'login', 'login.php', 'checkpoint', 'recover', 'friends', 'me', 'settings',
                    'messages', 'notifications', 'groups', 'watch', 'marketplace', 'gaming', 'reel',
                    'reels', 'ads', 'ad_center', 'business', 'help', 'privacy', 'policies', 'bookmarks',
                    'home.php', 'photo', 'photo.php', 'stories', 'profile_switcher', 'share'}
        page_id = ''
        if parts.path == '/profile.php':
            page_id = parse_qs(parts.query)['id'][0]
            fallback = 'Page '+page_id
        elif len(bits) == 1 and bits[0].lower() not in excluded and not bits[0].lower().endswith('.php'):
            page_id = bits[0] if re.fullmatch(r'[0-9]+', bits[0]) else ''
            fallback = 'Page '+page_id if page_id else unquote(bits[0]).replace('-', ' ').replace('_', ' ')
        elif len(bits) == 3 and bits[0] == 'pages' and re.fullmatch(r'[0-9]+', bits[2]):
            page_id = bits[2]
            fallback = unquote(bits[1]).replace('-', ' ').replace('_', ' ')
        else:
            raise ValueError('Mở đúng Page và sao chép địa chỉ trên thanh trình duyệt; '
                             'không dùng link bài viết, nhóm hoặc trang danh sách Page.')
        label = str(name).strip() or fallback[:150]
        return self.save_page(account_id, label, url, page_id)

    def remove_page(self, account_id, record_id):
        with self._edit() as value:
            self._account(value, account_id)
            row = next((row for row in value['pages'] if row['id'] == record_id
                        and row['account_id'] == account_id), None)
            if row is None:
                raise ValueError('Không còn Page này trong tài khoản. Bấm Làm mới.')
            value['pages'].remove(row)

    def merge_discovered_pages(self, account_id, items, *, message='', status='observed'):
        """Merge one browser scan atomically. Partial scans never delete saved Pages."""
        if (not isinstance(items, list) or len(items) > 1000
                or any(not isinstance(row, dict) for row in items)):
            raise ValueError('Danh sách Page không hợp lệ hoặc vượt quá 1.000 Page/lượt.')
        if status not in {'observed', 'partial'}:
            raise ValueError('Trạng thái Load Page không hợp lệ; chưa lưu danh sách.')
        fields = [_page_values(row['name'], row['url'], row.get('page_id', '')) for row in items]
        added = updated = seen = 0
        timestamp = _now()
        with self._edit() as value:
            account = self._account(value, account_id)
            visited = set()
            for found in fields:
                matches = [row for row in value['pages'] if row['account_id'] == account_id
                    and (row['url'].casefold() == found['url'].casefold()
                         or found['page_id'] and found['page_id'] == row.get('page_id'))]
                if len(matches) > 1:
                    raise ValueError('Hai Page đã lưu có cùng ID/link của Page vừa đọc; dùng Sửa Page để kiểm tra trước.')
                row = matches[0] if matches else None
                if row and row.get('page_id') and found['page_id'] and row['page_id'] != found['page_id']:
                    raise ValueError('Page ID đã lưu khác với Page vừa đọc; dữ liệu cũ được giữ để kiểm tra.')
                if row is None:
                    row = dict(id=uuid.uuid4().hex, account_id=account_id, created_at=timestamp,
                               source='browser_managed_pages', **found)
                    value['pages'].append(row)
                    added += 1
                elif row['id'] not in visited:
                    # Respect manually chosen names/links; refresh names only for auto-added records.
                    if row.get('source') == 'browser_managed_pages':
                        row.update(found)
                    elif not row.get('page_id') and found['page_id']:
                        row['page_id'] = found['page_id']
                    updated += 1
                row.update(last_seen_at=timestamp, discovered_name=found['name'])
                if row['id'] not in visited:
                    seen += 1
                    visited.add(row['id'])
            account['page_load'] = dict(at=timestamp, status=status, read_count=seen,
                                        message=str(message)[:2000])
        return dict(added=added, updated=updated, read_count=seen)
