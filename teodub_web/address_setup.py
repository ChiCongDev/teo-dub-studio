"""Local setup window. Tokens never travel through the portal or a chat."""
import argparse
from pathlib import Path
import tkinter as tk
from tkinter import ttk
import webbrowser

from teodub.web_address import fixed_url, load_address, save_address
from teodub.web_tracking import web_data_root
from teodub_web.address_credentials import AddressCredentials

PROVIDERS = {'Tên miền riêng · Cloudflare': 'cloudflare', 'Địa chỉ miễn phí · ngrok': 'ngrok'}
PROJECT = Path(__file__).resolve().parents[1]


class AddressSetup(ttk.Frame):
    def __init__(self, parent, root):
        super().__init__(parent, padding=18)
        self.root = Path(root)
        self.saved = None
        self.mode = tk.StringVar(self, value='fixed')
        self.provider = tk.StringVar(self, value=next(iter(PROVIDERS)))
        self.url = tk.StringVar(self)
        self.token = tk.StringVar(self)
        self.status = tk.StringVar(self, value='Nhập địa chỉ thật đã được cấp cho tài khoản của anh.')
        self.pack(fill='both', expand=True)
        self.columnconfigure(0, weight=1)
        ttk.Label(self, text='Địa chỉ web cố định', font=('Segoe UI', 21, 'bold')).grid(row=0, sticky='w')
        ttk.Label(self, text='Nhân viên dùng lại cùng một link sau mỗi lần mở máy. '
                  'Máy này vẫn xử lý video và lưu thành phẩm.', wraplength=690).grid(row=1, sticky='ew', pady=(6, 12))
        modes = ttk.Frame(self)
        modes.grid(row=2, sticky='ew')
        ttk.Radiobutton(modes, text='Dùng địa chỉ cố định', variable=self.mode, value='fixed').pack(side='left')
        ttk.Radiobutton(modes, text='Quay về link thử tạm thời', variable=self.mode, value='quick').pack(side='left', padx=16)
        ttk.Label(self, text='Cách kết nối').grid(row=3, sticky='w', pady=(12, 3))
        ttk.Combobox(self, values=tuple(PROVIDERS), textvariable=self.provider,
                     state='readonly', width=38).grid(row=4, sticky='ew')
        ttk.Label(self, text='Địa chỉ web đã được cấp (HTTPS)').grid(row=5, sticky='w', pady=(12, 3))
        ttk.Entry(self, textvariable=self.url).grid(row=6, sticky='ew')
        ttk.Label(self, text='Mã kết nối của dịch vụ · nhập trên máy này').grid(row=7, sticky='w', pady=(12, 3))
        ttk.Entry(self, textvariable=self.token, show='●').grid(row=8, sticky='ew')
        ttk.Label(self, text='Cloudflare: dán riêng Tunnel token. Ngrok: dán Authtoken. '
                  'Kết nối cũ có thể để trống ô mã để giữ lại. Windows mã hóa mã đã lưu; không gửi mã vào chat.',
                  wraplength=690).grid(row=9, sticky='ew', pady=6)
        ttk.Label(self, text='Tên miền riêng: tạo tunnel trong Cloudflare, thêm tuyến tên miền về '
                  'http://127.0.0.1:8765. Ngrok miễn phí dùng tên được cấp tự động, '
                  'giới hạn 1 GB và 20.000 lượt gọi/tháng; chỉ phù hợp thử nhỏ.', wraplength=690
                  ).grid(row=10, sticky='ew', pady=(4, 10))
        links = ttk.Frame(self)
        links.grid(row=11, sticky='ew')
        ttk.Button(links, text='Mở trang dịch vụ', command=self.open_provider).pack(side='left')
        ttk.Button(links, text='Xem hướng dẫn', command=lambda: webbrowser.open(
            'https://developers.cloudflare.com/tunnel/get-started/' if self.provider.get()==next(iter(PROVIDERS))
            else 'https://ngrok.com/docs/getting-started/')).pack(side='left', padx=6)
        actions = ttk.Frame(self)
        actions.grid(row=12, sticky='ew', pady=(14, 6))
        ttk.Button(actions, text='Lưu cấu hình', command=self.save).pack(side='left')
        ttk.Button(actions, text='Áp dụng khi web nghỉ', command=self.apply).pack(side='left', padx=6)
        ttk.Button(actions, text='Đóng', command=parent.destroy).pack(side='right')
        ttk.Label(self, textvariable=self.status, wraplength=690).grid(row=13, sticky='ew', pady=(5, 0))
        try:
            self.saved = load_address(self.root)
            if self.saved['mode'] == 'fixed':
                self.url.set(self.saved['url'])
                self.provider.set(next(label for label, value in PROVIDERS.items() if value == self.saved['provider']))
                self.status.set('Đã có cấu hình cố định. Để trống mã nếu muốn giữ kết nối đã lưu.')
        except ValueError as exc:
            self.status.set(str(exc))

    def open_provider(self):
        webbrowser.open('https://dash.cloudflare.com/' if PROVIDERS[self.provider.get()] == 'cloudflare'
                        else 'https://dashboard.ngrok.com/')

    def save(self):
        reference = None
        try:
            if self.mode.get() == 'quick':
                value = dict(version=1, mode='quick')
            else:
                provider = PROVIDERS[self.provider.get()]
                url = fixed_url(self.url.get())
                if self.token.get().strip():
                    reference = AddressCredentials(self.root).save(provider, self.token.get())
                elif self.saved and self.saved.get('provider') == provider:
                    reference = self.saved['credential']
                else:
                    raise ValueError('Nhập mã kết nối được cấp trong tài khoản dịch vụ.')
                value = dict(version=1, mode='fixed', provider=provider, url=url, credential=reference)
            self.saved = save_address(self.root, value)
            self.token.set('')
            self.status.set('Đã lưu. Chọn Áp dụng khi web nghỉ để nạp cấu hình; '
                            'việc đang chạy và link hiện tại chưa bị thay đổi.')
            return True
        except (OSError, ValueError, KeyError) as exc:
            # A newly sealed unused token can be discarded; a prior reference is retained.
            if reference and (not self.saved or reference != self.saved.get('credential')):
                AddressCredentials(self.root).remove(reference)
            self.status.set(str(exc))
            return False

    def apply(self):
        if not self.save():
            return
        try:
            from teodub_web.address_restart import restart_when_idle
            restart_when_idle(self.root)
            self.status.set('Đã mở lại web với cấu hình đã lưu. Kết nối cần vài giây; '
                            'xem trạng thái địa chỉ ở trang quản trị.')
        except (OSError, ValueError) as exc:
            self.status.set(str(exc))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root')
    options = parser.parse_args()
    window = tk.Tk()
    window.title('Tèo · Địa chỉ web cố định')
    window.geometry('760x695')
    window.minsize(730, 675)
    style = ttk.Style(window)
    style.theme_use('clam')
    style.configure('.', font=('Segoe UI', 10))
    style.configure('TButton', padding=(10, 7))
    AddressSetup(window, web_data_root(options.root))
    window.mainloop()


if __name__ == '__main__':
    main()
