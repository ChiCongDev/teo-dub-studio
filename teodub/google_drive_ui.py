"""Google Drive connection and destination selection; uploads run in the pipeline."""
from copy import deepcopy
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from teodub.google_drive import DriveClient, DriveError, normalize_drive_config


def drive_selection_summary(value):
    """A local preference summary, not a claim that a video has been uploaded."""
    selected = normalize_drive_config(value)
    if not selected['enabled']:
        return 'Google Drive: Tắt'
    if selected.get('mode') == 'sync_folder':
        return 'Drive qua thư mục: '+selected['folder_path']
    if not selected['account_id'] or not selected['folder_id']:
        return 'Google Drive: Chưa chọn tài khoản / thư mục'
    return 'Google Drive: ' + (selected['folder_name'] or 'Thư mục đã chọn')


def configure_google_drive(parent, data_dir, initial, on_save, *, client=None):
    return GoogleDriveDialog(parent, data_dir, initial, on_save, client=client)


class GoogleDriveDialog(tk.Toplevel):
    """Keep all Tk work on the UI thread; never initiate an upload here."""
    def __init__(self, parent, data_dir, initial, on_save, *, client=None):
        initial = normalize_drive_config(deepcopy(initial))
        super().__init__(parent)
        self.client = client if client is not None else DriveClient(data_dir)
        self.on_save = on_save
        self.initial = initial
        self.account_id = self.initial['account_id']
        self.folder_id = self.initial['folder_id']
        self.accounts = []
        self.folders = []
        self.busy = self.closed = False
        self.cancel = threading.Event()
        self.messages = queue.Queue()
        self.task_id = 0
        self.timer = None
        self.enabled = tk.BooleanVar(self, self.initial['enabled'])
        self.account = tk.StringVar(self)
        self.folder = tk.StringVar(self)
        self.client_path = tk.StringVar(self)
        self.status = tk.StringVar(self)
        self.title('Google Drive — Lưu video thành phẩm')
        self.geometry('780x600')
        self.minsize(670, 520)
        self.transient(parent)
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.bind('<Destroy>', self.on_destroy, add='+')
        self.build()
        self.reload_accounts()
        self.timer = self.after(100, self.poll)

    def build(self):
        shell = ttk.Frame(self, padding=16)
        shell.pack(fill='both', expand=True)
        ttk.Label(shell, text='Lưu video lên Google Drive', font=('Segoe UI', 17, 'bold')).pack(anchor='w')
        self.enable_button = ttk.Checkbutton(shell, text='Tự lưu video thành phẩm lên Drive khi lồng tiếng xong',
                                             variable=self.enabled)
        self.enable_button.pack(anchor='w', pady=(12, 8))
        ttk.Label(shell, text='Kết nối Google một lần, chọn thư mục rồi lưu lựa chọn cho video / chiến dịch mới.',
                  wraplength=720).pack(anchor='w')
        ttk.Label(shell, text='Tài khoản Google đã kết nối').pack(anchor='w', pady=(12, 3))
        self.account_picker = ttk.Combobox(shell, textvariable=self.account, state='readonly')
        self.account_picker.pack(fill='x')
        self.account_picker.bind('<<ComboboxSelected>>', self.select_account)
        row = ttk.Frame(shell)
        row.pack(fill='x', pady=(6, 10))
        self.disconnect_button = ttk.Button(row, text='Ngắt tài khoản đã chọn', command=self.disconnect)
        self.disconnect_button.pack(side='left')
        self.refresh_accounts_button = ttk.Button(row, text='Làm mới tài khoản', command=self.reload_accounts)
        self.refresh_accounts_button.pack(side='left', padx=6)
        ttk.Label(shell, text='Tệp kết nối Google (JSON của ứng dụng máy tính)').pack(anchor='w')
        row = ttk.Frame(shell)
        row.pack(fill='x', pady=3)
        self.client_entry = ttk.Entry(row, textvariable=self.client_path)
        self.client_entry.pack(side='left', fill='x', expand=True)
        self.choose_client_button = ttk.Button(row, text='Chọn tệp…', command=self.choose_client)
        self.choose_client_button.pack(side='left', padx=(6, 0))
        self.connect_button = ttk.Button(shell, text='Kết nối tài khoản Google…', command=self.connect)
        self.connect_button.pack(anchor='w', pady=(5, 10))
        ttk.Label(shell, text='Lần đầu cần tệp OAuth của Google Cloud, loại Desktop app, bật Google Drive API. '
            'Khi bấm Kết nối, bạn tự đăng nhập và cấp quyền trong trang Google mở ra. '
            'Không nhập mật khẩu hoặc khóa vào Tèo / chat.', wraplength=720, justify='left').pack(anchor='w')
        ttk.Label(shell, text='Thư mục nhận video trên Drive').pack(anchor='w', pady=(12, 3))
        self.folder_picker = ttk.Combobox(shell, textvariable=self.folder, state='readonly')
        self.folder_picker.pack(fill='x')
        self.folder_picker.bind('<<ComboboxSelected>>', self.select_folder)
        row = ttk.Frame(shell)
        row.pack(fill='x', pady=(6, 8))
        self.load_folders_button = ttk.Button(row, text='Tải danh sách thư mục', command=self.load_folders)
        self.load_folders_button.pack(side='left')
        self.create_folder_button = ttk.Button(row, text='Tạo thư mục mới…', command=self.create_folder)
        self.create_folder_button.pack(side='left', padx=6)
        ttk.Label(shell, text='Tèo chỉ xem các thư mục được cấp quyền cho ứng dụng; có thể tạo một thư mục mới tại đây. '
            'Video vẫn được lưu trên máy. Nếu tải lên lỗi, giữ thành phẩm và báo nguyên nhân.',
            wraplength=720, justify='left').pack(anchor='w')
        ttk.Label(shell, textvariable=self.status, wraplength=720, justify='left').pack(anchor='w', pady=10)
        actions = ttk.Frame(shell)
        actions.pack(side='bottom', fill='x', pady=(4, 0))
        self.save_button = ttk.Button(actions, text='Lưu lựa chọn', command=self.save)
        self.save_button.pack(side='left')
        ttk.Button(actions, text='Đóng', command=self.close).pack(side='right')
        self.edit_controls = [self.enable_button, self.account_picker, self.client_entry,
                              self.choose_client_button, self.connect_button, self.refresh_accounts_button,
                              self.disconnect_button, self.folder_picker, self.load_folders_button,
                              self.create_folder_button, self.save_button]

    def safe_error(self, exc):
        return str(exc) if isinstance(exc, DriveError) else 'Không hoàn tất được thao tác Google Drive. Thử lại sau khi kiểm tra kết nối mạng.'

    def refresh_controls(self):
        for widget in self.edit_controls:
            state = 'disabled' if self.busy else ('readonly' if widget in (self.account_picker, self.folder_picker) else 'normal')
            widget.configure(state=state)
        if not self.busy:
            self.disconnect_button.configure(state='normal' if self.account_id else 'disabled')
            for widget in (self.load_folders_button, self.create_folder_button):
                widget.configure(state='normal' if self.account_id else 'disabled')

    def reload_accounts(self):
        if self.busy or self.closed:
            return
        try:
            self.accounts = [deepcopy(row) for row in self.client.accounts() if row.get('connected')]
            self.account_picker.configure(values=[row.get('email') or row.get('name') or row['id'] for row in self.accounts])
            index = next((index for index, row in enumerate(self.accounts) if row['id'] == self.account_id), None)
            if index is not None:
                self.account_picker.current(index)
                self.restore_folder()
                self.status.set('Tài khoản đã lưu. Tải danh sách hoặc tạo thư mục trước khi bật tự lưu.')
            else:
                self.account_id = ''
                self.account.set('')
                self.clear_folder()
                self.status.set('Chưa có tài khoản được chọn. Kết nối Google hoặc chọn tài khoản đã lưu.')
        except Exception as exc:
            self.accounts = []
            self.account_id = ''
            self.account.set('')
            self.account_picker.configure(values=[])
            self.clear_folder()
            self.status.set(self.safe_error(exc))
        self.refresh_controls()

    def clear_folder(self):
        self.folder_id = ''
        self.folders = []
        self.folder.set('')
        self.folder_picker.configure(values=[])

    def restore_folder(self):
        current = next((row for row in self.folders if row['id'] == self.folder_id), None)
        if current:
            self.folder_picker.configure(values=[row['name'] + ' · ' + row['id'] for row in self.folders])
            self.folder_picker.current(self.folders.index(current))
        elif self.account_id == self.initial['account_id'] and self.initial['folder_id']:
            self.folders = [dict(id=self.initial['folder_id'], name=self.initial['folder_name'] or 'Thư mục đã lưu')]
            self.folder_picker.configure(values=[self.folders[0]['name']])
            self.folder_picker.current(0)
            self.folder_id = self.folders[0]['id']
        else:
            self.clear_folder()

    def select_account(self, event=None):
        if self.busy or self.closed:
            return
        index = self.account_picker.current()
        if not 0 <= index < len(self.accounts):
            return
        self.account_id = self.accounts[index]['id']
        self.clear_folder()
        self.status.set('Đã chọn tài khoản. Tải danh sách hoặc tạo thư mục cho tài khoản này.')
        self.refresh_controls()

    def select_folder(self, event=None):
        if self.busy or self.closed:
            return
        index = self.folder_picker.current()
        self.folder_id = self.folders[index]['id'] if 0 <= index < len(self.folders) else ''

    def choose_client(self):
        if self.busy or self.closed:
            return
        path = filedialog.askopenfilename(parent=self, title='Chọn tệp OAuth Google Desktop app',
                                         filetypes=[('Google OAuth JSON', '*.json')])
        if path:
            self.client_path.set(path)

    def run_task(self, operation, work, success):
        if self.busy or self.closed:
            return False
        self.busy = True
        self.cancel = threading.Event()
        self.task_id += 1
        ident, cancel = self.task_id, self.cancel
        self.status.set(operation)
        self.refresh_controls()
        def report(value):
            self.messages.put((ident, 'progress', value))
        def worker():
            try:
                result = work(cancel, report)
                self.messages.put((ident, 'done', (success, result)))
            except Exception as exc:
                self.messages.put((ident, 'error', self.safe_error(exc)))
        try:
            threading.Thread(target=worker, name='Teo-Drive-Setup', daemon=True).start()
        except Exception:
            self.busy = False
            self.status.set('Chưa bắt đầu được thao tác Google Drive. Thử lại sau.')
            self.refresh_controls()
            return False
        return True

    def connect(self):
        if self.busy or self.closed:
            return
        path = self.client_path.get().strip()
        if not path or not Path(path).is_file() or Path(path).suffix.lower() != '.json':
            self.status.set('Chọn tệp OAuth JSON của Google, loại Desktop app, trước khi kết nối.')
            return
        self.run_task('Đang mở trang Google; đăng nhập và cấp quyền trong trình duyệt…',
            lambda cancel, report: self.client.connect(path, cancel=cancel, progress=report), self.connected)

    def connected(self, row):
        self.account_id = row['id']
        self.initial = normalize_drive_config(dict(version=1, enabled=False, account_id=row['id'],
                                                   folder_id='', folder_name=''))
        self.reload_accounts()
        self.status.set('Đã kết nối Google. Tải danh sách hoặc tạo thư mục nhận video.')

    def load_folders(self):
        if self.busy or self.closed:
            return
        if not self.account_id:
            self.status.set('Chọn tài khoản Google đã kết nối trước.')
            return
        account_id = self.account_id
        selected_id = self.folder_id
        def loaded(rows):
            self.folders = [deepcopy(row) for row in rows]
            self.folder_picker.configure(values=[row['name'] + ' · ' + row['id'] for row in self.folders])
            index = next((index for index, row in enumerate(self.folders) if row['id'] == selected_id), None)
            self.folder_id = ''
            self.folder.set('')
            if index is not None:
                self.folder_picker.current(index)
                self.folder_id = self.folders[index]['id']
            self.status.set('Đã tải thư mục. Chọn thư mục rồi Lưu lựa chọn.' if self.folders else
                            'Chưa có thư mục được cấp quyền. Bấm Tạo thư mục mới để lưu video.')
        self.run_task('Đang tải thư mục Google Drive…', lambda _cancel, _report: self.client.folders(account_id), loaded)

    def create_folder(self):
        if self.busy or self.closed:
            return
        if not self.account_id:
            self.status.set('Chọn tài khoản Google đã kết nối trước.')
            return
        name = simpledialog.askstring('Tạo thư mục Google Drive', 'Tên thư mục nhận video:',
                                      initialvalue='Tèo Dub Studio', parent=self)
        if name is None:
            return
        if not name.strip():
            self.status.set('Tên thư mục không được để trống.')
            return
        account_id = self.account_id
        def created(row):
            self.folders = [deepcopy(row)] + [folder for folder in self.folders if folder['id'] != row['id']]
            self.folder_picker.configure(values=[folder['name'] + ' · ' + folder['id'] for folder in self.folders])
            self.folder_picker.current(0)
            self.folder_id = row['id']
            self.status.set('Đã tạo thư mục. Lưu lựa chọn để dùng cho video / chiến dịch mới.')
        self.run_task('Đang tạo thư mục Google Drive…',
            lambda _cancel, _report: self.client.create_folder(account_id, name.strip()), created)

    def disconnect(self):
        if self.busy or self.closed or not self.account_id:
            return
        account_id = self.account_id
        if not messagebox.askyesno('Ngắt Google Drive',
            'Ngắt tài khoản đã chọn khỏi Tèo? Video trên máy và trên Drive được giữ. '
            'Các phiên đã chọn tài khoản này sẽ cần kết nối lại trước khi tải lên.', parent=self):
            return
        def disconnected(_result):
            self.enabled.set(False)
            self.account_id = ''
            self.initial = normalize_drive_config({})
            self.reload_accounts()
            self.status.set('Đã ngắt tài khoản khỏi Tèo; chưa xóa video. Lưu lựa chọn để tắt tự lưu cho phiên mới.')
        self.run_task('Đang ngắt kết nối Google Drive…',
                      lambda _cancel, _report: self.client.disconnect(account_id), disconnected)

    def save(self):
        if self.busy or self.closed:
            return
        try:
            if self.enabled.get() and (not self.account_id or not self.folder_id):
                raise ValueError('Kết nối Google, chọn tài khoản và thư mục trước khi bật tự lưu.')
            if self.enabled.get() and not any(row['id'] == self.account_id and row.get('connected')
                for row in self.client.accounts()):
                raise ValueError('Tài khoản đã ngắt hoặc thay đổi. Kết nối lại trước khi lưu lựa chọn.')
            folder = next((row for row in self.folders if row['id'] == self.folder_id), None)
            value = normalize_drive_config(dict(version=1, enabled=self.enabled.get(), account_id=self.account_id,
                                                folder_id=self.folder_id, folder_name=folder['name'] if folder else ''))
            self.on_save(deepcopy(value))
        except ValueError as exc:
            self.status.set(str(exc))
            return
        except Exception:
            self.status.set('Chưa lưu được lựa chọn Google Drive. Lựa chọn hiện tại vẫn giữ để bạn thử lại.')
            return
        self.close()

    def poll(self):
        if self.closed:
            return
        if self.timer is not None:
            self.after_cancel(self.timer)
            self.timer = None
        try:
            while True:
                ident, kind, value = self.messages.get_nowait()
                if ident != self.task_id:
                    continue
                if kind == 'progress':
                    self.status.set(value.get('message', '') if isinstance(value, dict) else 'Đang kết nối Google Drive…')
                elif kind == 'error':
                    self.busy = False
                    self.status.set(value)
                    self.refresh_controls()
                elif kind == 'done':
                    self.busy = False
                    callback, result = value
                    try:
                        callback(result)
                    except Exception as exc:
                        self.status.set(self.safe_error(exc))
                    self.refresh_controls()
        except queue.Empty:
            pass
        self.timer = self.after(100, self.poll)

    def on_destroy(self, event):
        if event.widget is self and not self.closed:
            self.closed = True
            self.cancel.set()
            self.task_id += 1
            if self.timer is not None:
                self.after_cancel(self.timer)
                self.timer = None

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.cancel.set()
        self.task_id += 1
        if self.timer is not None:
            self.after_cancel(self.timer)
            self.timer = None
        self.destroy()
