"""A connector token is entered locally and sealed for the current Windows user."""
from pathlib import Path
import re
import uuid


class AddressCredentials:
    def __init__(self, root):
        self.root = Path(root)/'address-credentials'

    def path(self, reference):
        if not isinstance(reference, str) or not re.fullmatch('[a-f0-9]{32}', reference):
            raise ValueError('Mã kết nối web chưa hợp lệ.')
        return self.root/(reference+'.bin')

    @staticmethod
    def validate(token):
        if not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9_+/=.-]{20,4096}', token.strip()):
            raise ValueError('Chỉ dán mã kết nối được dịch vụ cấp, không dán cả lệnh cài đặt.')
        return token.strip()

    @staticmethod
    def entropy(provider):
        if provider not in ('cloudflare', 'ngrok'):
            raise ValueError('Dịch vụ kết nối chưa hợp lệ.')
        return ('TeoDubWeb.Address.'+provider).encode('ascii')

    def save(self, provider, token):
        import win32crypt
        import pywintypes
        token = self.validate(token)
        try:
            blob = win32crypt.CryptProtectData(token.encode('utf-8'), 'Teo Web Address',
                                              self.entropy(provider), None, None, 0x01)
        except pywintypes.error:
            raise ValueError('Windows chưa lưu được kết nối. Cấu hình cũ được giữ.') from None
        reference = uuid.uuid4().hex
        self.root.mkdir(parents=True, exist_ok=True)
        self.path(reference).write_bytes(blob)
        return reference

    def load(self, provider, reference):
        import win32crypt
        import pywintypes
        try:
            _, value = win32crypt.CryptUnprotectData(self.path(reference).read_bytes(),
                                                     self.entropy(provider), None, None, 0x01)
            return self.validate(value.decode('utf-8'))
        except (OSError, ValueError, UnicodeError, pywintypes.error):
            raise ValueError('Chưa mở được kết nối web trên tài khoản Windows này. '
                             'Nhập lại mã kết nối trong cửa sổ cấu hình địa chỉ.') from None

    def remove(self, reference):
        self.path(reference).unlink(missing_ok=True)
