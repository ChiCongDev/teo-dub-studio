"""Simple setup for an existing Drive for desktop synchronized folder."""
import os
from pathlib import Path
import subprocess
import tkinter as tk
from tkinter import filedialog, ttk
import webbrowser

from teodub.drive_folder import normalize_folder_config

DOWNLOAD_URL = 'https://www.google.com/drive/download/'


def installed_drive():
    root = Path(os.environ.get('PROGRAMFILES', 'C:/Program Files'))/'Google'/'Drive File Stream'
    candidates = sorted(root.glob('*/GoogleDriveFS.exe'), key=lambda path: path.stat().st_mtime, reverse=True)
    direct = root/'GoogleDriveFS.exe'
    if direct.is_file():
        candidates.insert(0, direct)
    return candidates[0] if candidates else None


def configure_drive_folder(parent, initial, on_save, *, advanced=None):
    return DriveFolderDialog(parent, initial, on_save, advanced=advanced)


class DriveFolderDialog(tk.Toplevel):
    def __init__(self, parent, initial, on_save, *, advanced=None):
        super().__init__(parent)
        self.on_save = on_save
        self.advanced = advanced
        same_mode = initial.get('mode') == 'sync_folder'
        self.enabled = tk.BooleanVar(self, initial.get('enabled', False) if same_mode else True)
        suggested = Path.home()/'Videos'/'Teo Drive'
        self.folder = tk.StringVar(self, initial.get('folder_path', '') if same_mode
                                  else (str(suggested) if suggested.is_dir() else ''))
        self.ready = tk.BooleanVar(self, bool(same_mode and initial.get('enabled')))
        self.status = tk.StringVar(self)
        self.closed = False
        self.title('Google Drive — Chọn thư mục đồng bộ')
        self.geometry('780x560')
        self.minsize(640, 400)
        self.transient(parent)
        self.protocol('WM_DELETE_WINDOW', self.close)
        footer = ttk.Frame(self, padding=(18, 10))
        footer.pack(side='bottom', fill='x')
        viewport = ttk.Frame(self)
        viewport.pack(fill='both', expand=True)
        self.canvas = tk.Canvas(viewport, highlightthickness=0)
        scroll = ttk.Scrollbar(viewport, orient='vertical', command=self.canvas.yview)
        scroll.pack(side='right', fill='y')
        self.canvas.configure(yscrollcommand=scroll.set)
        self.canvas.pack(side='left', fill='both', expand=True)
        shell = ttk.Frame(self.canvas, padding=18)
        self.panel = self.canvas.create_window((0, 0), window=shell, anchor='nw')
        shell.bind('<Configure>', lambda _event: self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<Configure>', lambda event: self.canvas.itemconfigure(self.panel, width=event.width))
        self.bind('<MouseWheel>', lambda event: self.canvas.yview_scroll(-int(event.delta/120), 'units'))
        ttk.Label(shell, text='Lưu video qua thư mục Google Drive', font=('Segoe UI', 17, 'bold')).pack(anchor='w')
        ttk.Label(shell, text='Dùng Google Drive cho máy tính và dung lượng Drive đang có. '
                  'Không cần tệp JSON hoặc đăng ký dùng thử Google Cloud.', wraplength=680).pack(anchor='w', pady=(8, 12))
        guide = ('1. Cài / mở Google Drive cho máy tính và đăng nhập tài khoản có 5 TB.\n'
                 '2. Trong Drive, thêm thư mục thành phẩm để đồng bộ, hoặc chọn một thư mục trong Drive của tôi.\n'
                 '3. Chọn đúng thư mục đó ở dưới, xác nhận đã bật đồng bộ rồi lưu lựa chọn.')
        ttk.Label(shell, text=guide, wraplength=680, justify='left').pack(anchor='w')
        row = ttk.Frame(shell)
        row.pack(fill='x', pady=(10, 12))
        ttk.Button(row, text='Tải Google Drive chính thức', command=lambda: webbrowser.open(DOWNLOAD_URL)).pack(side='left')
        ttk.Button(row, text='Mở Google Drive', command=self.open_drive).pack(side='left', padx=8)
        ttk.Label(shell, text='Thư mục nhận MP4 và SRT').pack(anchor='w')
        row = ttk.Frame(shell)
        row.pack(fill='x', pady=(4, 8))
        self.folder_entry = ttk.Entry(row, textvariable=self.folder)
        self.folder_entry.pack(side='left', fill='x', expand=True)
        self.choose_button = ttk.Button(row, text='Chọn thư mục…', command=self.choose_folder)
        self.choose_button.pack(side='left', padx=(8, 0))
        self.folder.trace_add('write', lambda *_: self.ready.set(False))
        ttk.Checkbutton(shell, text='Tôi đã bật đồng bộ thư mục này trong Google Drive', variable=self.ready).pack(anchor='w')
        ttk.Checkbutton(shell, text='Tự chép thành phẩm vào thư mục này sau khi xuất xong', variable=self.enabled).pack(anchor='w', pady=(7, 0))
        ttk.Label(shell, text='Tèo giữ bản xuất trên máy và chép MP4/SRT theo từng phiên. '
                  'Google Drive quản lý tải lên; xem biểu tượng Drive để kiểm tra tiến độ đồng bộ.',
                  wraplength=680).pack(anchor='w', pady=(10, 0))
        ttk.Label(shell, textvariable=self.status, wraplength=680).pack(anchor='w', pady=(8, 0))
        actions = footer
        self.save_button = ttk.Button(actions, text='Lưu lựa chọn', command=self.save)
        self.save_button.pack(side='left')
        if advanced:
            ttk.Button(actions, text='Cách kết nối khác…', command=self.open_advanced).pack(side='left', padx=8)
        ttk.Button(actions, text='Đóng', command=self.close).pack(side='right')
        self.status.set('Google Drive đã cài: đăng nhập và chọn thư mục đồng bộ.' if installed_drive()
                        else 'Máy chưa có Google Drive cho máy tính. Bấm Tải Google Drive chính thức để cài.')

    def choose_folder(self):
        folder = filedialog.askdirectory(parent=self, title='Chọn thư mục đã bật đồng bộ Google Drive',
                                         initialdir=self.folder.get() or str(Path.home()), mustexist=True)
        if folder:
            self.folder.set(folder)

    def open_drive(self):
        executable = installed_drive()
        if executable is None:
            self.status.set('Chưa tìm thấy ứng dụng Drive. Bấm Tải Google Drive chính thức rồi cài đặt.')
            return
        try:
            subprocess.Popen([str(executable)], creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            self.status.set('Đã mở ứng dụng Drive. Đăng nhập và kiểm tra thư mục đồng bộ tại biểu tượng Drive.')
        except OSError:
            self.status.set('Chưa mở được Drive. Mở Google Drive từ menu Start của Windows.')

    def save(self):
        try:
            options = normalize_folder_config(dict(enabled=self.enabled.get(), folder_path=self.folder.get().strip()))
            if options['enabled'] and not Path(options['folder_path']).is_dir():
                raise ValueError('Thư mục chưa sẵn sàng. Mở Google Drive rồi chọn thư mục đang có trên máy.')
            if options['enabled'] and not self.ready.get():
                raise ValueError('Bật đồng bộ trong Google Drive trước, rồi đánh dấu xác nhận thư mục này.')
            self.on_save(options)
        except (ValueError, OSError) as exc:
            self.status.set(str(exc))
            return
        self.close()

    def open_advanced(self):
        self.close()
        if self.advanced:
            self.advanced()

    def close(self):
        if not self.closed:
            self.closed = True
            self.destroy()
