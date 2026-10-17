"""Local public-address preferences; no account or queue data lives here."""
import ipaddress
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

from teodub.common import atomic_json

ADDRESS_FILE = 'web-address.json'


def fixed_url(value):
    if not isinstance(value, str):
        raise ValueError('Nhập địa chỉ web cố định, ví dụ https://teo.congty.com.')
    value = value.strip()
    if '://' not in value:
        value = 'https://'+value
    if any(char.isspace() or ord(char) < 32 for char in value) or '\\' in value:
        raise ValueError('Địa chỉ web không được có khoảng trắng hoặc dấu gạch ngược.')
    try:
        parts = urlsplit(value)
        host = (parts.hostname or '').encode('idna').decode('ascii').lower()
        valid = (parts.scheme == 'https' and parts.username is None and parts.password is None
                 and parts.port in (None, 443) and parts.path in ('', '/')
                 and not parts.query and not parts.fragment and len(host) <= 253
                 and len(host.split('.')) >= 2 and not host.endswith('.')
                 and all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label)
                         for label in host.split('.')))
    except (ValueError, UnicodeError):
        valid, host = False, ''
    try:
        ipaddress.ip_address(host)
        valid = False
    except ValueError:
        pass
    if (not valid or host.rsplit('.', 1)[-1] in ('local', 'localhost', 'internal', 'invalid', 'test')
            or host == 'trycloudflare.com' or host.endswith('.trycloudflare.com')):
        raise ValueError('Dùng tên miền HTTPS cố định; không dùng IP, link tạm, đường dẫn hoặc link đăng nhập.')
    return 'https://'+host


def normalize_address(value):
    if not isinstance(value, dict) or value.get('version') != 1:
        raise ValueError('Cấu hình địa chỉ web chưa hợp lệ. Mở lại mục Địa chỉ web cố định.')
    mode = value.get('mode')
    if mode == 'quick' and set(value) == {'version', 'mode'}:
        return dict(version=1, mode='quick')
    if mode != 'fixed' or set(value) != {'version', 'mode', 'provider', 'url', 'credential'}:
        raise ValueError('Cấu hình địa chỉ web chưa hợp lệ.')
    provider = value['provider']
    if provider not in ('cloudflare', 'ngrok'):
        raise ValueError('Dịch vụ địa chỉ web chưa được hỗ trợ.')
    url = fixed_url(value['url'])
    if provider == 'ngrok' and not urlsplit(url).hostname.endswith(('.ngrok-free.app', '.ngrok-free.dev', '.ngrok.app')):
        raise ValueError('Nhập đúng địa chỉ cố định được ngrok cấp trong tài khoản của anh.')
    reference = value['credential']
    if not isinstance(reference, str) or not re.fullmatch('[a-f0-9]{32}', reference):
        raise ValueError('Chưa có kết nối của địa chỉ cố định. Nhập mã kết nối trong cửa sổ trên máy.')
    return dict(version=1, mode='fixed', provider=provider, url=url, credential=reference)


def load_address(root):
    path = Path(root)/ADDRESS_FILE
    if not path.exists():
        return dict(version=1, mode='quick')
    try:
        return normalize_address(json.loads(path.read_text(encoding='utf-8')))
    except (OSError, UnicodeError, ValueError):
        raise ValueError('Chưa đọc được cấu hình địa chỉ web. Mở mục Địa chỉ web cố định để kiểm tra; '
                         'tài khoản và công việc vẫn được giữ.') from None


def save_address(root, value):
    address = normalize_address(value)
    atomic_json(Path(root)/ADDRESS_FILE, address)
    return address
