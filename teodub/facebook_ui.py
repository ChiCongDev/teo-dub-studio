"""Facebook browser sessions, saved Pages and explicit Page management windows."""
from dataclasses import dataclass, field
from copy import deepcopy
from collections import Counter
from pathlib import Path
import queue
import threading
import time
import tkinter as tk
import uuid
from tkinter import messagebox, simpledialog, ttk

from teodub.facebook_accounts import BUSINESS_SUITE, FACEBOOK_HOME, FACEBOOK_PAGES, META_APPS, FacebookAccounts, facebook_browser_url
from teodub.page_publish_settings import result_checks_enabled, set_result_checks_enabled


def bridge_mailbox_root():
    """Use the existing pinned portal store, never create another web data root."""
    pin = Path(__file__).resolve().parents[1]/'tools'/'web-data-root.txt'
    try:
        root = Path(pin.read_text(encoding='utf-8').strip())
        if root.is_absolute() and root.is_dir():
            return root.resolve()/'page-control'
    except (OSError, ValueError):
        pass
    return None


def create_page_bridge(owner):
    from teodub.page_command_bridge import PageCommandBridge
    return PageCommandBridge(owner, mailbox_root=bridge_mailbox_root())


@dataclass
class BrowserSession:
    account_id: str
    account_name: str
    pending: bool = True
    opened: bool = False
    failed: bool = False
    loading_pages: bool = False
    page_load_status: str = ''
    page_load_active: str = ''
    status: str = ''
    navigation: dict = field(default_factory=dict)
    page_load_requests: dict = field(default_factory=dict)
    post_actions: dict = field(default_factory=dict)
    messages: queue.Queue = field(default_factory=queue.Queue)
    commands: queue.Queue = field(default_factory=queue.Queue)
    cancel: threading.Event = field(default_factory=threading.Event)
    finished: threading.Event = field(default_factory=threading.Event)
    worker: object = None
    restart_error: str = ''


class FacebookTab(ttk.Frame):
    def __init__(self, parent, studio):
        super().__init__(parent, padding=16)
        self.studio = studio
        self.store = FacebookAccounts(studio.data_dir)
        self.data = dict(accounts=[], pages=[])
        self.account_ids = []
        self.account_id = ''
        self.context_account_id = ''
        self.link_account_id = ''
        self.page_link_drafts = {}
        self.sessions = {}
        self.manager_windows = {}
        self.page_scheduler = None
        self.page_bridge = None
        self.destroyed = False
        self.timer = None
        self.advanced_visible = False
        self.page_link_visible = False
        self.account_label = tk.StringVar(self)
        self.status = tk.StringVar(self, 'Thêm tài khoản, rồi mở Facebook để tự đăng nhập. '
                                   'Tèo sẽ dùng lại hồ sơ đó khi bạn mở lần sau.')
        self.detail = tk.StringVar(self)
        self.browser_summary = tk.StringVar(self, 'Chưa mở trình duyệt Facebook.')
        self.account_count = tk.StringVar(self)
        self.page_hint = tk.StringVar(self)
        self.page_link_name = tk.StringVar(self)
        self.page_link_url = tk.StringVar(self)
        self.page_link_destination = tk.StringVar(self)
        self.page_link_notice = tk.StringVar(self)
        self.bridge_enabled = tk.BooleanVar(self, True)
        self.bridge_notice = tk.StringVar(self)
        self.result_checks_enabled = tk.BooleanVar(self, result_checks_enabled(studio.data_dir))
        self.result_checks_notice = tk.StringVar(self)
        self.build()
        self.update_result_checks_notice()
        self.reload()
        try:
            self.page_bridge = create_page_bridge(self)
            self.bridge_enabled.set(self.page_bridge.enabled)
            self.update_bridge_notice()
        except (OSError, ValueError, RuntimeError) as exc:
            self.bridge_enabled.set(False)
            self.bridge_notice.set('Chưa kết nối được quản lý Page trên web: '+str(exc)+'. Quản lý Page trong Tèo vẫn dùng được.')
        self.refresh_buttons()
        self.bind('<Destroy>', self.on_destroy, add='+')
        try:
            self.get_page_scheduler()  # Restore result reads even before opening a Page dialog.
        except (OSError, ValueError, RuntimeError) as exc:
            self.status.set('Chưa nối lại được theo dõi kết quả Page: '+str(exc))
        self.timer = self.after(250, self.poll)

    @property
    def browser_pending(self):
        session = self.sessions.get(self.account_id)
        return bool(session and session.pending)

    def selected_status(self):
        session = self.sessions.get(self.account_id)
        if session and session.status:
            self.status.set(session.status)
        elif self.account_id:
            message = 'Đã chọn '+self.account_label.get()+'. Mở Facebook để kiểm tra phiên đã lưu.'
            account = next((row for row in self.data['accounts'] if row['id'] == self.account_id), {})
            saved = account.get('page_load', {})
            if isinstance(saved, dict) and saved.get('message'):
                message += ' Lượt Load Page đã lưu: '+str(saved['message'])
            self.status.set(message)

    def build(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(5, weight=1)
        ttk.Label(self, text='Quản lý Facebook và Page', font=('Segoe UI', 20, 'bold')).grid(
            row=0, column=0, sticky='w', pady=(0, 8))
        ttk.Label(self, text='Chọn tài khoản bên trái, chọn Page rồi mở quản lý kế hoạch và lịch đăng.',
                  wraplength=1020).grid(row=1, column=0, sticky='w')
        accounts = ttk.Frame(self)
        accounts.grid(row=2, column=0, sticky='ew', pady=(14, 8))
        accounts.columnconfigure(1, weight=1)
        ttk.Label(accounts, text='Tài khoản Facebook').grid(row=0, column=0, padx=(0, 10))
        self.account_picker = ttk.Combobox(accounts, textvariable=self.account_label, state='readonly')
        self.account_picker.grid(row=0, column=1, sticky='ew')
        self.account_picker.bind('<<ComboboxSelected>>', self.select_account)
        self.selected_account_label = ttk.Label(accounts, textvariable=self.account_label)
        self.selected_account_label.grid(row=0, column=1, sticky='w')
        self.add_account_button = ttk.Button(accounts, text='Thêm tài khoản…', command=self.add_account)
        self.add_account_button.grid(row=0, column=2, padx=6)
        self.rename_account_button = ttk.Button(accounts, text='Đổi tên…', command=self.rename_account)
        self.rename_account_button.grid(row=0, column=3)
        self.reload_button = ttk.Button(accounts, text='Làm mới', command=self.reload)
        self.reload_button.grid(row=0, column=4, padx=(6, 0))
        browser = ttk.Frame(self)
        browser.grid(row=3, column=0, sticky='ew', pady=(0, 8))
        self.facebook_button = ttk.Button(browser, text='Đăng nhập / Mở Facebook', command=self.open_facebook)
        self.facebook_button.pack(side='left', padx=(0, 6))
        self.load_pages_button = ttk.Button(browser, text='Load Page của tài khoản', command=self.load_pages)
        self.load_pages_button.pack(side='left', padx=(0, 6))
        self.business_button = ttk.Button(browser, text='Mở Business Suite', command=self.open_business)
        self.business_button.pack(side='left', padx=(0, 6))
        self.developers_button = ttk.Button(browser, text='Mở Meta Developers', command=self.open_developers)
        self.developers_button.pack(side='left', padx=(0, 6))
        self.close_button = ttk.Button(browser, text='Đóng / Lưu phiên tài khoản này', command=self.finish_browser)
        self.close_button.pack(side='left')
        self.advanced_button = ttk.Button(browser, text='Nâng cao', command=self.toggle_advanced)
        self.advanced_button.pack(side='right',before=self.facebook_button)
        notices = ttk.Frame(self)
        notices.grid(row=4, column=0, sticky='ew', pady=(0, 12))
        ttk.Label(notices, textvariable=self.status, wraplength=1020).pack(anchor='w')
        self.browser_summary_label = ttk.Label(notices, textvariable=self.browser_summary, wraplength=1020)
        self.browser_summary_label.pack(anchor='w', pady=(4, 0))
        self.bridge_button = ttk.Checkbutton(notices, text='Nhận lệnh quản lý Page từ web',
            variable=self.bridge_enabled, command=self.toggle_bridge)
        self.bridge_button.pack(anchor='w', pady=(4, 0))
        self.bridge_notice_label = ttk.Label(notices, textvariable=self.bridge_notice, wraplength=1020)
        self.bridge_notice_label.pack(anchor='w')
        self.result_checks_button = ttk.Checkbutton(notices, text='Tự kiểm tra trạng thái sau khi gửi',
            variable=self.result_checks_enabled, command=self.toggle_result_checks)
        self.result_checks_notice_label = ttk.Label(notices, textvariable=self.result_checks_notice, wraplength=1020)
        self.lists = ttk.Panedwindow(self, orient='horizontal')
        self.lists.grid(row=5, column=0, sticky='nsew')
        account_table = ttk.LabelFrame(self.lists, text='Danh sách tài khoản Facebook', padding=8)
        account_table.columnconfigure(0, weight=1)
        account_table.rowconfigure(0, weight=1)
        self.account_tree = ttk.Treeview(account_table, columns=('name', 'pages', 'browser'),
                                        show='headings', selectmode='browse', height=10)
        for key, text, width in [('name', 'Tài khoản', 145), ('pages', 'Page đã lưu', 85),
                                 ('browser', 'Trình duyệt', 125)]:
            self.account_tree.heading(key, text=text)
            self.account_tree.column(key, width=width, minwidth=45, stretch=key == 'name')
        self.account_tree.grid(row=0, column=0, sticky='nsew')
        account_scroll = ttk.Scrollbar(account_table, orient='vertical', command=self.account_tree.yview)
        account_scroll.grid(row=0, column=1, sticky='ns')
        account_horizontal = ttk.Scrollbar(account_table, orient='horizontal', command=self.account_tree.xview)
        account_horizontal.grid(row=1, column=0, sticky='ew')
        self.account_tree.configure(yscrollcommand=account_scroll.set, xscrollcommand=account_horizontal.set)
        self.account_tree.bind('<<TreeviewSelect>>', self.select_account_from_list)
        self.account_tree.bind('<Double-1>', self.open_selected_account)
        self.account_tree.bind('<Return>', lambda _: self.open_selected_account())
        self.account_tree.bind('<Button-3>', self.show_account_menu)
        self.account_menu = tk.Menu(self, tearoff=False)
        self.account_menu.add_command(label='Load Page', command=lambda: self.load_pages(self.context_account_id))
        ttk.Label(account_table, textvariable=self.account_count, wraplength=310).grid(
            row=2, column=0, sticky='w', pady=(6, 0))
        self.lists.add(account_table, weight=1)
        table = ttk.LabelFrame(self.lists, text='Fanpage của tài khoản đang chọn', padding=8)
        self.lists.add(table, weight=2)
        table.columnconfigure(0, weight=1)
        table.rowconfigure(2, weight=1)
        self.page_link_form = ttk.LabelFrame(table, text='Lưu Page bằng link', padding=8)
        self.page_link_form.grid(row=0, column=0, columnspan=2, sticky='ew', pady=(0, 8))
        self.page_link_form.grid_remove()
        self.page_link_form.columnconfigure(1, weight=1)
        ttk.Label(self.page_link_form, textvariable=self.page_link_destination, wraplength=550).grid(
            row=0, column=0, columnspan=3, sticky='w', pady=(0, 6))
        ttk.Label(self.page_link_form, text='Tên Page (tùy chọn)').grid(row=1, column=0, sticky='w', padx=(0, 8))
        self.page_name_entry = ttk.Entry(self.page_link_form, textvariable=self.page_link_name)
        self.page_name_entry.grid(row=1, column=1, columnspan=2, sticky='ew', pady=(0, 6))
        ttk.Label(self.page_link_form, text='Link Page').grid(row=2, column=0, sticky='w', padx=(0, 8))
        self.page_url_entry = ttk.Entry(self.page_link_form, textvariable=self.page_link_url)
        self.page_url_entry.grid(row=2, column=1, sticky='ew')
        self.save_page_link_button = ttk.Button(self.page_link_form, text='Lưu link Page', command=self.save_page_link)
        self.save_page_link_button.grid(row=2, column=2, padx=(6, 0))
        for entry in (self.page_name_entry, self.page_url_entry):
            entry.bind('<Return>', self.save_page_link)
        ttk.Label(self.page_link_form, textvariable=self.page_link_notice, wraplength=550).grid(
            row=3, column=0, columnspan=3, sticky='w', pady=(6, 0))
        self.page_hint_label = ttk.Label(table, textvariable=self.page_hint, wraplength=570)
        self.page_hint_label.grid(row=1, column=0, sticky='w', pady=(0, 8))
        columns = ('name', 'page_id', 'url', 'state')
        self.tree = ttk.Treeview(table, columns=columns, show='headings', selectmode='browse', height=10)
        for key, text, width in [('name', 'Tên Page', 150), ('page_id', 'Page ID', 105),
                                 ('url', 'Link Page', 220), ('state', 'Trình duyệt / API', 220)]:
            self.tree.heading(key, text=text)
            self.tree.column(key, width=width, minwidth=90)
        self.tree.grid(row=2, column=0, sticky='nsew')
        vertical = ttk.Scrollbar(table, orient='vertical', command=self.tree.yview)
        vertical.grid(row=2, column=1, sticky='ns')
        horizontal = ttk.Scrollbar(table, orient='horizontal', command=self.tree.xview)
        horizontal.grid(row=3, column=0, sticky='ew')
        self.tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.tree.bind('<<TreeviewSelect>>', lambda _: self.refresh_buttons())
        self.tree.bind('<Double-1>', lambda _: self.open_page())
        actions = ttk.Frame(self)
        actions.grid(row=6, column=0, sticky='w', pady=10)
        self.page_link_button = ttk.Button(actions, text='Thêm Page bằng link…', command=self.toggle_page_link)
        self.page_link_button.pack(side='left', padx=(0, 6))
        self.add_page_button = ttk.Button(actions, text='Thêm Page…', command=self.add_page)
        self.edit_page_button = ttk.Button(actions, text='Sửa Page…', command=self.edit_page)
        self.remove_page_button = ttk.Button(actions, text='Bỏ khỏi danh sách', command=self.remove_page)
        self.open_page_button = ttk.Button(actions, text='Mở Page đã chọn', command=self.open_page)
        self.manage_page_button = ttk.Button(actions, text='Quản lý Page / Bài viết', command=self.manage_page)
        for button in (self.add_page_button, self.edit_page_button, self.remove_page_button, self.open_page_button, self.manage_page_button):
            button.pack(side='left', padx=(0, 6))
        self.detail_label = ttk.Label(self, textvariable=self.detail, wraplength=1020)
        self.detail_label.grid(row=7, column=0, sticky='ew')
        self.guidance_label = ttk.Label(self, text='Bạn tự đăng nhập và xử lý xác minh trong Chrome; không cần gửi mật khẩu vào chat. '
                  'Tên tài khoản là nhãn bạn đặt, Tèo chưa tự xác minh danh tính Facebook.\n'
                  'Chuột phải tài khoản → Load Page để đọc và lưu Trang bạn quản lý. Có thể thêm Page bằng tay. '
                  'Đăng nhập trình duyệt và lưu Page chưa cấp quyền '
                  'API. Chọn Page → Quản lý Page / Bài viết để soạn chữ, chọn video và đăng bằng Chrome của tài khoản. '
                  'API Meta là lựa chọn riêng nếu bạn đã kết nối. '
                  'Mở Meta Developers dùng đúng trình duyệt của tài khoản trong Tèo.',
                  wraplength=1020)
        self.guidance_label.grid(row=8, column=0, sticky='ew', pady=(12, 0))
        self.set_advanced_visible(False)

    def toggle_advanced(self):
        self.set_advanced_visible(not self.advanced_visible)

    def set_advanced_visible(self, visible):
        self.advanced_visible = bool(visible)
        self.advanced_button.configure(text='Ẩn nâng cao' if visible else 'Nâng cao')
        if visible:
            self.account_picker.grid()
            self.selected_account_label.grid_remove()
            self.rename_account_button.grid()
            for button in (self.business_button,self.developers_button,self.close_button):
                button.pack(side='left',padx=(0,6))
            self.browser_summary_label.pack(anchor='w',pady=(4,0))
            self.bridge_button.pack(anchor='w',pady=(4,0))
            self.bridge_notice_label.pack(anchor='w')
            self.result_checks_button.pack(anchor='w',pady=(4,0))
            self.result_checks_notice_label.pack(anchor='w')
            for button in (self.add_page_button,self.edit_page_button,self.remove_page_button):
                button.pack(side='left',padx=(0,6),before=self.open_page_button)
            self.detail_label.grid()
            self.guidance_label.grid()
        else:
            self.account_picker.grid_remove()
            self.selected_account_label.grid()
            self.rename_account_button.grid_remove()
            for widget in (self.business_button,self.developers_button,self.close_button,
                           self.browser_summary_label,self.bridge_button,self.bridge_notice_label,
                           self.result_checks_button,self.result_checks_notice_label,
                           self.add_page_button,self.edit_page_button,self.remove_page_button):
                widget.pack_forget()
            self.detail_label.grid_remove()
            self.guidance_label.grid_remove()
        self.tree.configure(displaycolumns=('name','page_id','url','state') if visible else ('name',))
        if visible:
            for key, width in [('name',150),('page_id',105),('url',220),('state',220)]:
                self.tree.column(key,width=width)
        self.lists.pane(self.account_tree.master, weight=1 if visible else 0)
        self.lists.pane(self.tree.master, weight=2 if visible else 1)

    def toggle_page_link(self):
        self.page_link_visible = not self.page_link_visible
        if self.page_link_visible:
            self.page_link_form.grid()
        else:
            self.page_link_form.grid_remove()
        self.page_link_button.configure(text='Ẩn thêm Page' if self.page_link_visible else 'Thêm Page bằng link…')

    def reload(self):
        try:
            self.data = self.store.load()
            self.account_ids = [row['id'] for row in self.data['accounts']]
            self.account_picker.configure(values=[row['name'] for row in self.data['accounts']])
            chosen = self.account_id if self.account_id in self.account_ids else self.data.get('selected_account_id')
            self.account_id = chosen if chosen in self.account_ids else next(iter(self.account_ids), '')
            if self.account_id:
                self.account_picker.current(self.account_ids.index(self.account_id))
            else:
                self.account_label.set('')
            self.sync_page_link_form()
            for row in self.data['accounts']:
                if row['id'] in self.sessions:
                    self.sessions[row['id']].account_name = row['name']
            self.display_pages()
            self.sync_account_list(select_current=True)
            self.selected_status()
        except (OSError, ValueError) as exc:
            self.status.set(str(exc))
        self.refresh_buttons()

    def sync_page_link_form(self):
        if self.link_account_id != self.account_id:
            if self.link_account_id:
                self.page_link_drafts[self.link_account_id] = (
                    self.page_link_name.get(), self.page_link_url.get(), self.page_link_notice.get())
            self.link_account_id = self.account_id
            name, url, notice = self.page_link_drafts.get(self.account_id, ('', '',
                'Dán link rồi bấm Lưu link Page. Để trống tên: dùng tên trong link hoặc Page ID.'))
            self.page_link_name.set(name)
            self.page_link_url.set(url)
            self.page_link_notice.set(notice)
        self.page_link_destination.set('Lưu vào tài khoản: '+self.account_label.get() if self.account_id
                                       else 'Chọn hoặc thêm tài khoản Facebook để lưu link Page.')

    def save_page_link(self, _=None):
        if self.destroyed or self.studio.closing or self.studio.closed:
            return
        account_id = self.account_id
        if not account_id or account_id != self.link_account_id:
            self.page_link_notice.set('Chọn tài khoản Facebook trước khi lưu link Page.')
            return
        try:
            saved = self.store.save_page_link(account_id, self.page_link_url.get(), self.page_link_name.get())
            self.page_link_name.set('')
            self.page_link_url.set('')
            self.page_link_notice.set('Đã lưu “'+saved['name']+'” vào '+self.account_label.get()+'. '
                                      'Chọn Page trong bảng để sửa tên/link hoặc mở kiểm tra.')
            self.reload()
            self.display_pages(saved['id'])
            self.refresh_buttons()
        except (OSError, ValueError) as exc:
            self.page_link_notice.set(str(exc))

    def select_account(self, _=None):
        index = self.account_picker.current()
        if index < 0:
            return
        self.activate_account(self.account_ids[index])

    def activate_account(self, account_id):
        if self.destroyed or self.studio.closing or account_id not in self.account_ids:
            return False
        if account_id == self.account_id:
            return True
        try:
            self.store.select_account(account_id)
            self.account_id = account_id
            self.reload()
            return True
        except (OSError, ValueError) as exc:
            self.status.set(str(exc))
            if self.account_id in self.account_ids:
                self.account_picker.current(self.account_ids.index(self.account_id))
            self.sync_account_list(select_current=True)
            return False

    def select_account_from_list(self, _=None):
        if self.destroyed:
            return
        selection = self.account_tree.selection()
        if selection:
            self.activate_account(selection[0])

    def open_selected_account(self, event=None):
        if self.destroyed:
            return
        row = self.account_tree.identify_row(event.y) if event is not None else ''
        selection = self.account_tree.selection()
        account_id = row or (selection[0] if selection else '')
        if account_id and self.activate_account(account_id):
            self.open_facebook()

    def show_account_menu(self, event):
        if self.destroyed or self.studio.closing:
            return
        row = self.account_tree.identify_row(event.y)
        if not row or not self.activate_account(row):
            return
        self.account_tree.selection_set(row)
        self.context_account_id = row
        session = self.sessions.get(row)
        blocked = bool(session and session.pending and (session.loading_pages or session.finished.is_set()))
        self.account_menu.entryconfigure('Load Page', state='disabled' if blocked else 'normal')
        try:
            self.account_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.account_menu.grab_release()

    def load_pages(self, account_id=None, *, request_id=None):
        account_id = account_id or self.account_id
        if self.destroyed or self.studio.closing or self.studio.closed:
            self.status.set('Tèo đang đóng; chưa nhận yêu cầu Load Page.')
            return False
        if request_id:
            try:
                if uuid.UUID(request_id).hex != request_id:
                    raise ValueError('Mã yêu cầu Load Page không hợp lệ.')
                self.store._account(self.store.load(), account_id)
            except (ValueError, OSError, AttributeError) as exc:
                self.status.set(str(exc))
                return False
        elif not account_id or not self.activate_account(account_id):
            return False
        session = self.sessions.get(account_id)
        command = dict(action='load_pages')
        request_cancel = threading.Event() if request_id else None
        expires_at = time.monotonic()+360 if request_id else None
        if request_id:
            command.update(request_id=request_id, cancel_request=request_cancel, expires_at=expires_at)
        if session and session.pending:
            if (session.loading_pages or session.finished.is_set() or session.cancel.is_set()
                    or any(item['state'] in {'queued', 'running'} for item in session.post_actions.values())):
                self.status.set('Chrome của tài khoản đang có thao tác khác hoặc đang đóng; đợi xong rồi Load Page.')
                return False
            if request_id in session.page_load_requests:
                self.status.set('Yêu cầu Load Page đã được nhận; không gửi trùng.')
                return False
            session.loading_pages = True
            session.page_load_status = ''
            session.status = 'Đang Load Page của '+session.account_name+'…'
            session.commands.put(command)
            if account_id == self.account_id:
                self.status.set(session.status)
            self.refresh_buttons()
        else:
            if not self.open_browser(FACEBOOK_PAGES, initial_command=command, account_id=account_id):
                return False
            session = self.sessions[account_id]
        session.page_load_active = request_id or ''
        if request_id:
            session.page_load_requests[request_id] = dict(state='queued', account_id=account_id,
                message='Đã nhận Load Page trong Chrome của '+session.account_name+'.',
                cancel=request_cancel, expires_at=expires_at)
            while len(session.page_load_requests) > 64:
                old = next((key for key, row in session.page_load_requests.items()
                            if row['state'] in {'done', 'error'}), None)
                if old is None:
                    break
                del session.page_load_requests[old]
        return True

    def browser_state(self, account_id):
        """Browser lifecycle is separate from login or Page permissions."""
        session = self.sessions.get(account_id)
        if session and session.pending:
            if session.finished.is_set() or session.cancel.is_set():
                return 'Chrome đang đóng'
            if session.loading_pages:
                return 'Chrome đang Load Page'
            return 'Chrome đã mở; kiểm tra đúng Page' if session.opened else 'Chrome đang mở'
        return 'Chrome có lỗi' if session and session.failed else 'Chrome chưa mở'

    def toggle_bridge(self):
        if self.page_bridge is None or self.destroyed:
            return
        try:
            self.page_bridge.set_enabled(self.bridge_enabled.get())
        except (OSError, ValueError, RuntimeError) as exc:
            self.bridge_enabled.set(self.page_bridge.enabled)
            self.bridge_notice.set('Chưa đổi được kết nối web: '+str(exc))
            return
        self.update_bridge_notice()

    def update_result_checks_notice(self):
        self.result_checks_notice.set('Đã bật kiểm tra kết quả sau khi gửi.' if self.result_checks_enabled.get() else
            'Đã tạm dừng kiểm tra trạng thái. Bài gửi xong hiển thị Đã gửi — chưa xác minh; các bài tiếp theo vẫn chạy.')

    def toggle_result_checks(self):
        try:
            enabled = self.result_checks_enabled.get()
            set_result_checks_enabled(self.studio.data_dir, enabled)
        except (OSError, ValueError) as exc:
            self.result_checks_enabled.set(result_checks_enabled(self.studio.data_dir))
            self.result_checks_notice.set('Chưa đổi được kiểm tra trạng thái: '+str(exc))
            return
        if not enabled:
            for session in self.sessions.values():
                for request in session.post_actions.values():
                    if (request.get('state') == 'verifying' or
                            request.get('operation') in {'verify','inspect_result'}
                            and request.get('state') in {'queued','running'}):
                        if request.get('cancel') is not None:
                            request['cancel'].set()
        self.update_result_checks_notice()
        for manager in self.manager_windows.values():
            if not manager.destroyed:
                manager.refresh_controls()
                manager.schedule_panel.refresh()

    def update_bridge_notice(self):
        if self.page_bridge is None:
            return
        self.bridge_notice.set('Đã bật: web quản trị có thể gửi lệnh mở Chrome, Load Page và đăng nội dung đã duyệt. '
            'Giữ Tèo mở trên máy này; chỉ một cửa sổ Tèo nhận lệnh. Đăng nhập/xác minh trực tiếp trong Chrome.' if self.page_bridge.enabled else
            'Đã tắt nhận lệnh web. Tính năng quản lý Page trong Tèo vẫn dùng bình thường.')

    def sync_account_list(self, *, select_current=False):
        counts = Counter(row['account_id'] for row in self.data['pages'])
        for iid in self.account_tree.get_children():
            if iid not in self.account_ids:
                self.account_tree.delete(iid)
        for row in self.data['accounts']:
            session = self.sessions.get(row['id'])
            if session and session.pending:
                state = ('Đang đóng' if session.finished.is_set() else 'Đang Load Page' if session.loading_pages
                         else 'Đã mở' if session.opened else 'Đang mở…')
            elif session and session.failed:
                state = 'Có lỗi'
            else:
                state = 'Đã đóng' if session else 'Chưa mở'
            values = (row['name'], counts[row['id']], state)
            if not self.account_tree.exists(row['id']):
                self.account_tree.insert('', 'end', iid=row['id'], values=values)
            elif tuple(self.account_tree.item(row['id'], 'values')) != tuple(str(value) for value in values):
                self.account_tree.item(row['id'], values=values)
        if (select_current and self.account_id and self.account_tree.exists(self.account_id)
                and self.account_tree.selection() != (self.account_id,)):
            self.account_tree.selection_set(self.account_id)
            self.account_tree.see(self.account_id)
        count = len(self.account_ids)
        self.account_count.set(f'Đã lưu {count} tài khoản. Nhấp đôi để mở Facebook; chuột phải → Load Page.'
                               if count else 'Chưa có tài khoản. Bấm Thêm tài khoản… để lưu tài khoản đầu tiên.')

    def add_account(self):
        if self.destroyed or self.studio.closing:
            return False
        name = simpledialog.askstring('Thêm tài khoản Facebook',
            'Đặt tên để phân biệt tài khoản (không nhập mật khẩu):', parent=self)
        if name is None:
            return False
        try:
            self.account_id = self.store.add_account(name)['id']
            self.reload()
            self.status.set('Đã lưu tài khoản. Bấm Đăng nhập / Mở Facebook và tự đăng nhập trong Chrome.')
            return True
        except (OSError, ValueError) as exc:
            self.status.set(str(exc))
            return False

    def rename_account(self):
        if not self.account_id or self.browser_pending:
            return
        name = simpledialog.askstring('Đổi tên tài khoản', 'Tên hiển thị trong Tèo:',
                                      initialvalue=self.account_label.get(), parent=self)
        if name is None:
            return
        try:
            self.store.rename_account(self.account_id, name)
            self.reload()
        except (OSError, ValueError) as exc:
            self.status.set(str(exc))

    def display_pages(self, select=''):
        selection = self.tree.selection()
        select = select or (selection[0] if selection else '')
        self.tree.delete(*self.tree.get_children())
        rows = [row for row in self.data['pages'] if row['account_id'] == self.account_id]
        for row in rows:
            state = self.browser_state(row['account_id'])+' · API chưa kết nối'
            from teodub.page_activity import PageActivity
            activity = PageActivity(self.studio.data_dir)
            if activity.path.exists():
                connection = activity.connection(self.account_id, row['id'])
                if connection:
                    state = self.browser_state(row['account_id'])+' · '+('API cần kết nối lại'
                             if connection['status'] == 'reconnect' or connection['page_id'] != row.get('page_id')
                             else 'Đã kết nối đọc API')
            self.tree.insert('', 'end', iid=row['id'], values=(row['name'], row.get('page_id') or '—',
                             row['url'], state))
        if select and self.tree.exists(select):
            self.tree.selection_set(select)
        if not rows:
            self.page_hint.set('Chưa có Fanpage được lưu. Bấm Thêm Page bằng link để lưu trực tiếp, '
                               'hoặc chuột phải tài khoản → Load Page để đọc tự động.' if self.account_id else
                               'Chọn hoặc thêm tài khoản Facebook trước, rồi bấm Thêm Page bằng link…')
            self.page_hint_label.grid()
        else:
            self.page_hint_label.grid_remove()
        self.detail.set(f'Đã lưu {len(rows)} Page cho tài khoản này. Các Page dùng chung phiên trình duyệt '
                        'của tài khoản; thêm/sửa Page không thay đổi phiên đăng nhập.')

    def selected_page(self):
        selection = self.tree.selection()
        return next((row for row in self.data['pages'] if selection and row['id'] == selection[0]
                     and row['account_id'] == self.account_id), None)

    def page_dialog(self, row=None):
        window = tk.Toplevel(self)
        window.title('Sửa Page' if row else 'Thêm Page')
        window.transient(self.winfo_toplevel())
        window.resizable(False, False)
        form = ttk.Frame(window, padding=16)
        form.pack(fill='both', expand=True)
        fields = {}
        for number, (key, label) in enumerate([('name', 'Tên Page'), ('url', 'Link Facebook của Page'),
                                               ('page_id', 'Page ID (có thể để trống)')]):
            ttk.Label(form, text=label).grid(row=number*2, column=0, sticky='w', pady=(6, 3))
            fields[key] = tk.StringVar(window, (row or {}).get(key, ''))
            ttk.Entry(form, textvariable=fields[key], width=65).grid(row=number*2+1, column=0, sticky='ew')
        message = tk.StringVar(window, 'Page sẽ thuộc tài khoản: '+self.account_label.get())
        ttk.Label(form, textvariable=message, wraplength=480).grid(row=6, column=0, sticky='w', pady=10)
        def save():
            try:
                saved = self.store.save_page(self.account_id, **{key: var.get() for key, var in fields.items()},
                                             record_id=row['id'] if row else None)
                self.reload()
                self.display_pages(saved['id'])
                self.refresh_buttons()
                self.status.set('Đã lưu Page. Bạn có thể mở Page trong hồ sơ của tài khoản này để kiểm tra.')
                window.destroy()
            except (OSError, ValueError) as exc:
                message.set(str(exc))
        buttons = ttk.Frame(form)
        buttons.grid(row=7, column=0, sticky='e')
        ttk.Button(buttons, text='Lưu Page', command=save).pack(side='left', padx=6)
        ttk.Button(buttons, text='Hủy', command=window.destroy).pack(side='left')
        window.bind('<Escape>', lambda _: window.destroy())
        window.grab_set()
        window.wait_window()

    def add_page(self):
        if self.account_id and not self.destroyed:
            self.page_dialog()

    def edit_page(self):
        row = self.selected_page()
        if row and not self.destroyed:
            self.page_dialog(row)

    def remove_page(self):
        row = self.selected_page()
        if row and messagebox.askyesno('Bỏ Page khỏi danh sách',
            f'Bỏ “{row["name"]}” khỏi Tèo?\nĐây chỉ là danh sách trong Tèo, không xóa Page trên Facebook.', parent=self):
            try:
                self.store.remove_page(self.account_id, row['id'])
                self.reload()
                self.refresh_buttons()
            except (OSError, ValueError) as exc:
                self.status.set(str(exc))

    def open_facebook(self):
        self.open_browser(FACEBOOK_HOME)

    def open_business(self):
        self.open_browser(BUSINESS_SUITE)

    def open_developers(self):
        self.open_browser(META_APPS)

    def manage_page(self):
        if self.destroyed or self.studio.closing or self.studio.closed:
            return
        row = self.selected_page()
        if not row:
            return
        window = self.manager_windows.get(row['id'])
        if window is not None and window.winfo_exists():
            window.deiconify()
            window.lift()
            return window
        try:
            from teodub.page_manager_ui import PageManager
            window = PageManager(self, self.account_id, row['id'])
            self.manager_windows[row['id']] = window
            return window
        except (OSError, ValueError) as exc:
            self.status.set(str(exc))

    def open_page(self):
        row = self.selected_page()
        if row:
            self.open_browser(row['url'])

    def open_browser(self, url, *, initial_command=None, request_id=None, account_id=None):
        if getattr(self.studio,'restart_draining',False):
            self.status.set('Đang bàn giao phiên Chrome; đợi Tèo mở lại rồi thao tác.')
            return False
        if self.destroyed or self.studio.closing or self.studio.closed:
            self.status.set('Tèo đang đóng; chưa mở trình duyệt. Mở lại ứng dụng để tiếp tục.')
            return False
        try:
            url = facebook_browser_url(url)
        except ValueError as exc:
            self.status.set(str(exc))
            return False

        navigation_cancel = threading.Event() if request_id else None
        navigation_expires_at = time.monotonic()+360 if request_id else None

        def track(session, message):
            if request_id:
                session.navigation[request_id] = dict(state='queued', message=message,
                    cancel=navigation_cancel, expires_at=navigation_expires_at)
                while len(session.navigation) > 64:
                    old = next((key for key,value in session.navigation.items()
                                if value['state'] in {'opened','error'}), None)
                    if old is None:
                        break
                    del session.navigation[old]

        current_account = account_id or self.account_id
        if account_id and not any(row['id'] == account_id for row in self.store.load()['accounts']):
            self.status.set('Không còn tài khoản Facebook này trong Tèo.')
            return False
        page_navigation = None
        page_matches = [row for row in self.store.load()['pages'] if row['account_id'] == current_account and row['url'] == url]
        if len(page_matches) == 1:
            page_navigation = dict(action='open_page',url=url,data_dir=str(self.studio.data_dir),
                account_id=current_account,page_key=page_matches[0]['id'],request_id=request_id,
                cancel_request=navigation_cancel,expires_at=navigation_expires_at)
        session = self.sessions.get(current_account)
        if session and session.pending:
            if session.finished.is_set() or session.cancel.is_set():
                self.status.set('Chrome của '+session.account_name+' đang đóng. Đợi đóng xong rồi bấm mở lại.')
                return False
            if not request_id and (not session.opened or session.loading_pages):
                self.status.set('Chrome đang khởi động; đợi trình duyệt sẵn sàng rồi mở trang.' if not session.opened else
                                'Đang Load Page; đợi đọc xong rồi mở trang để giữ lượt đang đọc.')
                return False
            command = dict(action='open_url',url=url,request_id=request_id,
                cancel_request=navigation_cancel, expires_at=navigation_expires_at) if request_id else url
            if page_navigation:
                command = page_navigation
            session.commands.put(command)
            phase = ('Chrome đang khởi động; đã giữ yêu cầu và sẽ mở trang khi sẵn sàng.' if not session.opened else
                     'Đang Load Page; đã giữ yêu cầu mở trang và sẽ thực hiện khi đọc xong.' if session.loading_pages else
                     'Đang mở trang trong phiên Chrome của '+session.account_name+'…')
            track(session, phase)
            if not session.loading_pages:
                session.status = phase
            self.status.set(phase)
            return True
        if not current_account and not self.add_account():
            self.status.set('Chưa chọn tài khoản Facebook để mở trình duyệt.')
            return False
        current_account = current_account or self.account_id
        try:
            profile = self.store.profile_directory(current_account)
        except (OSError, ValueError) as exc:
            self.status.set(str(exc))
            return False
        # Capture the account/session, never the mutable GUI selection, in the worker.
        account_name = next((row['name'] for row in self.store.load()['accounts'] if row['id'] == current_account), self.account_label.get())
        session = BrowserSession(current_account, account_name)
        self.sessions[session.account_id] = session
        if initial_command is not None:
            session.commands.put(initial_command)
            session.loading_pages = True
        session.status = ('Đang mở Chrome để Load Page của ' if initial_command else 'Đang mở Chrome cho ')+session.account_name+'…'
        track(session, session.status)
        self.status.set(session.status)
        self.refresh_buttons()
        def browse():
            try:
                from teodub.facebook_browser import open_facebook_browser
                options = dict(url=url, cancel=session.cancel, finished=session.finished,
                    commands=session.commands, progress=lambda kind, message: session.messages.put((kind, message)))
                if page_navigation:
                    options['page_navigation'] = page_navigation
                if request_id:
                    options['request_id'] = request_id
                    options['navigation_cancel'] = navigation_cancel
                    options['navigation_expires_at'] = navigation_expires_at
                reason = open_facebook_browser(profile, **options)
                session.messages.put(('done', reason))
            except Exception as exc:
                session.messages.put(('error', str(exc)))
        try:
            # Non-daemon: closing the GUI requests a graceful flush of this profile.
            session.worker = threading.Thread(target=browse, daemon=False)
            session.worker.start()
        except Exception as exc:
            session.pending = session.opened = False
            session.loading_pages = False
            session.failed = True
            session.status = 'Chưa mở được Chrome: '+str(exc)
            self.status.set(session.status)
            if request_id:
                session.navigation[request_id] = dict(state='error',message=session.status)
            self.refresh_buttons()
            return False
        return True

    def submit_page_browser(self, account_id, page_key, post_id, operation, *, request_id=None,
                            result_url='', expected_reconcile=None, confirmed_attempt=False):
        """Queue one scoped action; no credential access or posting in the Tk thread."""
        if self.destroyed or self.studio.closing or self.studio.closed or getattr(self.studio,'restart_draining',False):
            self.status.set('Tèo đang đóng; lịch và bài đã lưu được giữ.')
            return None
        if not isinstance(operation, str) or operation not in {'prepare', 'publish', 'review', 'inspect_result', 'verify'}:
            self.status.set('Thao tác bài viết qua Chrome không hợp lệ.')
            return None
        if operation in {'verify','inspect_result'} and not result_checks_enabled(self.studio.data_dir):
            self.status.set('Đã tạm dừng kiểm tra trạng thái. Bật lại trong Nâng cao khi cần.')
            return None
        try:
            from teodub.page_activity import PageActivity
            activity = PageActivity(self.studio.data_dir)
            page = activity.page(account_id, page_key)
            post = activity.post(account_id, page_key, post_id)
            if post.get('delivery_mode', 'api') != 'browser':
                raise ValueError('Bài này đang dùng API Meta; lưu cách đăng Trình duyệt web trước.')
            inspection = {}
            if operation == 'inspect_result':
                if (not isinstance(result_url, str) or not isinstance(expected_reconcile, dict)
                        or confirmed_attempt is not True):
                    raise ValueError('Xem lại đúng lượt đã gửi và xác nhận link bài trước khi kiểm tra kết quả.')
                saved = expected_reconcile.get('schedule')
                if (not isinstance(saved, dict) or not isinstance(saved.get('id'), str)
                        or uuid.UUID(saved['id']).hex != saved['id']):
                    raise ValueError('Bản đối chiếu thiếu mã lịch đã gửi hợp lệ.')
                if (saved.get('account_id'), saved.get('page_key'), saved.get('post_id')) != (account_id, page_key, post_id):
                    raise ValueError('Bản đối chiếu không thuộc đúng tài khoản/Page/lượt gửi.')
                if post.get('state') not in {'unknown', 'published'}:
                    raise ValueError('Chỉ kiểm tra lại lượt đã gửi và đang chờ xác minh hoặc đã xác nhận.')
                inspection = dict(result_url=result_url.strip(), expected_reconcile=deepcopy(expected_reconcile),
                                  confirmed_attempt=True)
            session = self.sessions.get(account_id)
            if session and any(item['state'] in {'queued', 'running'} and operation != 'verify'
                    or item['state'] in {'queued', 'running', 'verifying'} and item.get('post_id') == post_id
                    for item in session.post_actions.values()):
                raise ValueError('Chrome của tài khoản này đang xử lý một bài; đợi xong rồi tiếp tục.')
            if session and session.pending and (session.cancel.is_set() or session.finished.is_set()):
                raise ValueError('Chrome đang đóng; đợi đóng xong rồi tiếp tục.')
            if operation == 'inspect_result' and (not session or not session.pending or not session.opened
                    or session.loading_pages):
                raise ValueError('Mở Facebook của tài khoản này và đợi Load Page xong trước khi kiểm tra lại kết quả.')
            if not session or not session.pending:
                if operation not in {'prepare', 'verify'}:
                    raise ValueError('Phiên soạn bài đã đóng. Mở Page để đối chiếu; không tự tạo lại hoặc gửi lại bài.')
                if not self.open_browser(page['url'], account_id=account_id):
                    return None
                session = self.sessions[account_id]
            if request_id is not None and not isinstance(request_id, str):
                raise ValueError('Mã yêu cầu không hợp lệ; chưa gửi vào Chrome.')
            request_id = request_id or uuid.uuid4().hex
            if uuid.UUID(request_id).hex != request_id or request_id in session.post_actions:
                raise ValueError('Mã yêu cầu đã được sử dụng hoặc không hợp lệ; không gửi lại.')
            from teodub.facebook_post_timing import post_action_seconds
            timeout_seconds = post_action_seconds(operation, post['kind'])
            deadline = time.monotonic()+timeout_seconds
            request_cancel = threading.Event()
            message = ('Đã nhận yêu cầu; chờ Chrome khởi động.' if not session.opened else
                       'Đã nhận yêu cầu; chờ Load Page xong.' if session.loading_pages else
                       'Đã nhận yêu cầu '+{'prepare':'chuẩn bị bài', 'publish':'đăng bài đã duyệt',
                                          'review':'xem bài trong Chrome', 'inspect_result':'đối chiếu bài đã gửi',
                                          'verify':'theo dõi lại bài đã gửi'}[operation]+'.')
            session.post_actions[request_id] = dict(state='queued', message=message, operation=operation,
                account_id=account_id, page_key=page_key, post_id=post_id, result=None,
                cancel=request_cancel, expires_at=deadline, timeout_seconds=timeout_seconds)
            if inspection:
                session.post_actions[request_id]['schedule_id'] = inspection['expected_reconcile']['schedule']['id']
            while len(session.post_actions) > 64:
                old = next((key for key,value in session.post_actions.items() if value['state'] in {'done','error'}), None)
                if old is None:
                    break
                del session.post_actions[old]
            session.commands.put(dict(action='page_post',operation=operation,request_id=request_id,
                data_dir=str(self.studio.data_dir),account_id=account_id,page_key=page_key,post_id=post_id,
                cancel_request=request_cancel,expires_at=deadline, **inspection))
            self.status.set(message)
            return request_id
        except (OSError, ValueError) as exc:
            self.status.set(str(exc))
            return None

    def get_page_scheduler(self):
        if self.page_scheduler is None:
            from teodub.page_schedule import PageScheduleController
            self.page_scheduler = PageScheduleController(self)
        return self.page_scheduler

    def finish_browser(self, account_id=None):
        session = self.sessions.get(account_id or self.account_id)
        if session and session.pending:
            session.finished.set()
            session.status = 'Đang đóng Chrome và lưu hồ sơ của '+session.account_name+'. Vui lòng đợi…'
            self.status.set(session.status)
            self.refresh_buttons()
            return True
        return False

    def refresh_buttons(self):
        ready = not self.destroyed and not self.studio.closing
        idle = ready and not self.browser_pending
        session = self.sessions.get(self.account_id)
        usable = ready and (not self.browser_pending or session.opened and not session.finished.is_set()
                            and not session.loading_pages)
        self.account_picker.configure(state='readonly' if ready else 'disabled')
        manual = ready and not self.studio.closed and bool(self.account_id)
        for widget in (self.page_name_entry, self.page_url_entry, self.save_page_link_button):
            widget.configure(state='normal' if manual else 'disabled')
        for button in (self.add_account_button, self.reload_button):
            button.configure(state='normal' if ready else 'disabled')
        for button in (self.advanced_button, self.page_link_button):
            button.configure(state='normal' if ready and not self.studio.closed else 'disabled')
        self.rename_account_button.configure(state='normal' if idle and self.account_id else 'disabled')
        self.facebook_button.configure(state='normal' if usable and not self.studio.closing else 'disabled')
        load_usable = (manual and not (session and session.pending and
            (session.loading_pages or session.finished.is_set() or session.cancel.is_set()
             or any(item['state'] in {'queued', 'running'} for item in session.post_actions.values()))))
        self.load_pages_button.configure(state='normal' if load_usable else 'disabled')
        self.bridge_button.configure(state='normal' if ready and not self.studio.closed and self.page_bridge is not None else 'disabled')
        self.business_button.configure(state='normal' if usable and self.account_id and not self.studio.closing else 'disabled')
        self.developers_button.configure(state='normal' if usable and self.account_id else 'disabled')
        self.close_button.configure(state='normal' if ready and self.browser_pending and not session.finished.is_set() else 'disabled')
        self.add_page_button.configure(state='normal' if idle and self.account_id else 'disabled')
        selected = self.selected_page() is not None
        for button in (self.edit_page_button, self.remove_page_button):
            button.configure(state='normal' if idle and selected else 'disabled')
        self.open_page_button.configure(state='normal' if usable and selected and not self.studio.closing else 'disabled')
        self.manage_page_button.configure(state='normal' if ready and selected and not self.studio.closed else 'disabled')
        active = []
        failed = []
        for value in self.sessions.values():
            if value.pending:
                phase = ('đang đóng' if value.finished.is_set() else 'đang Load Page' if value.loading_pages
                         else 'đã mở' if value.opened else 'đang mở')
                active.append(value.account_name+' ('+phase+')')
            elif value.failed:
                failed.append(value.account_name)
        text = 'Chrome: '+' · '.join(active) if active else 'Chưa mở trình duyệt Facebook.'
        if failed:
            text += ' Chưa mở/giữ được phiên: '+', '.join(failed)+'. Chọn tài khoản để xem lỗi.'
        self.browser_summary.set(text)
        self.sync_account_list()
        for key in self.tree.get_children():
            values = list(self.tree.item(key, 'values'))
            if len(values) == 4:
                api = values[-1].partition(' · ')[2] or 'API chưa kết nối'
                current = self.browser_state(self.account_id)+' · '+api
                if values[-1] != current:
                    values[-1] = current
                    self.tree.item(key, values=values)

    def poll(self):
        if self.destroyed:
            return
        for account_id, session in self.sessions.items():
            while True:
                try:
                    kind, value = session.messages.get_nowait()
                except queue.Empty:
                    break
                if kind == 'restart_blocked':
                    session.restart_error = str(value)
                elif kind in ('done', 'error'):
                    if session.loading_pages:
                        session.page_load_status = 'Load Page bị gián đoạn trước khi lưu danh sách. Chọn Load Page lại; Page cũ được giữ.'
                    session.loading_pages = False
                    session.page_load_active = ''
                    session.pending = session.opened = False
                    session.failed = kind == 'error'
                    if kind == 'error':
                        session.status = 'Chưa mở/giữ được phiên Facebook của '+session.account_name+': '+value
                    else:
                        session.status = 'Đã đóng Chrome của '+session.account_name+' và giữ hồ sơ. '
                        session.status += 'Mở Facebook lần sau để kiểm tra phiên. '
                        session.status += 'Tèo chưa tự xác nhận tài khoản đã đăng nhập hay quyền trên Page.'
                    if session.page_load_status:
                        session.status += ' '+session.page_load_status
                    for navigation in session.navigation.values():
                        if navigation['state'] not in {'opened','error'}:
                            if navigation.get('cancel') is not None:
                                navigation['cancel'].set()
                            navigation.update(state='error', message=session.status)
                    for request in session.page_load_requests.values():
                        if request['state'] in {'queued', 'running'}:
                            if request.get('cancel') is not None:
                                request['cancel'].set()
                            request.update(state='error', message=session.status)
                    for request in session.post_actions.values():
                        if request['state'] in {'queued','running','verifying'}:
                            if request.get('cancel') is not None:
                                request['cancel'].set()
                            request.update(state='error',message='Phiên Chrome đã đóng hoặc gián đoạn. Kiểm tra Page trước; không tự gửi lại.')
                elif kind in {'post_progress','post_verifying','post_result','post_error'}:
                    if (not isinstance(value, dict) or not isinstance(value.get('request_id'), str)
                            or value['request_id'] not in session.post_actions):
                        continue
                    request = session.post_actions[value['request_id']]
                    if request['state'] in {'done','error'} or value.get('operation') != request['operation']:
                        continue
                    if request['operation'] == 'inspect_result' and any(value.get(key) != expected for key, expected in
                            (('account_id', account_id), ('page_key', request['page_key']), ('post_id', request['post_id']))):
                        request['cancel'].set()
                        request.update(state='error', message='Phản hồi kiểm tra không đúng tài khoản/Page/lượt gửi; đã dừng yêu cầu.')
                        session.status = request['message']
                        continue
                    result = value.get('result')
                    if kind == 'post_result' and (not isinstance(result, dict) or result.get('id') != request['post_id']
                            or result.get('account_id') != account_id or result.get('page_key') != request['page_key']
                            or request['operation'] == 'inspect_result'
                                and result.get('schedule_id') != request.get('schedule_id')):
                        request.update(state='error',message='Phản hồi bài viết không đúng tài khoản/Page; cần kiểm tra trong Chrome.')
                    else:
                        next_state = {'post_progress':'running','post_verifying':'verifying',
                                      'post_result':'done','post_error':'error'}[kind]
                        if kind == 'post_progress' and request['state'] == 'verifying':
                            next_state = 'verifying'
                        request.update(state=next_state,
                            message=str(value.get('message') or 'Đã nhận phản hồi từ Chrome.'),result=result)
                    session.status = request['message']
                elif kind == 'navigation':
                    if isinstance(value, dict) and value.get('request_id') in session.navigation:
                        request = session.navigation[value['request_id']]
                        if request['state'] not in {'opened', 'error'}:
                            request.update(state=value['state'], message=value['message'])
                            session.status = value['message']
                elif kind == 'pages_loaded':
                    if not session.loading_pages:
                        continue
                    request_id = value.get('request_id') if isinstance(value, dict) else None
                    request = session.page_load_requests.get(request_id)
                    if request_id and (not request or request['state'] not in {'queued', 'running'}):
                        continue
                    session.loading_pages = False
                    session.page_load_active = ''
                    try:
                        summary = self.store.merge_discovered_pages(account_id, value['items'],
                            message=value.get('message', ''), status=value.get('status', 'observed'))
                        session.page_load_status = (f'Load Page: thêm {summary["added"]}, đã có/cập nhật {summary["updated"]}. '
                                                    +value.get('message', ''))
                        session.status = session.page_load_status
                        if request:
                            request.update(state='done', message=session.page_load_status, result=summary)
                        self.reload()
                    except (OSError, ValueError, KeyError, TypeError) as exc:
                        session.page_load_status = 'Chưa lưu được danh sách Load Page: '+str(exc)
                        session.status = session.page_load_status
                        if request:
                            request.update(state='error', message=session.page_load_status)
                elif kind == 'pages_error':
                    request_id = value.get('request_id') if isinstance(value, dict) else None
                    request = session.page_load_requests.get(request_id)
                    if request_id and (not request or request['state'] not in {'queued', 'running'}):
                        continue
                    session.loading_pages = False
                    session.page_load_active = ''
                    session.page_load_status = 'Load Page chưa xong: '+str(value.get('message', '') if isinstance(value, dict) else value)
                    session.status = session.page_load_status
                    if request:
                        request.update(state='error', message=session.page_load_status)
                elif kind == 'pages_progress' and isinstance(value, dict):
                    request = session.page_load_requests.get(value.get('request_id'))
                    if request and request['state'] in {'queued', 'running'}:
                        session.status = str(value.get('message', 'Đang Load Page…'))
                        request.update(state='running', message=session.status)
                else:
                    if kind == 'opened':
                        session.opened = True
                    if kind != 'opened' or not session.loading_pages:
                        session.status = value
                if account_id == self.account_id:
                    self.status.set(session.status)
            for request in session.post_actions.values():
                if request['state'] in {'queued','running','verifying'} and time.monotonic() >= request.get('expires_at',float('inf')):
                    if request.get('cancel') is not None:
                        request['cancel'].set()
                    from teodub.facebook_post_timing import post_timeout_message
                    message = post_timeout_message(request)
                    request.update(state='error', message=message)
                    session.status = request['message']
                    if account_id == self.account_id:
                        self.status.set(session.status)
            for table in (session.navigation, session.page_load_requests):
                for key, request in table.items():
                    if (request['state'] in {'queued', 'running', 'opening'}
                            and time.monotonic() >= request.get('expires_at', float('inf'))):
                        if request.get('cancel') is not None:
                            request['cancel'].set()
                        request.update(state='error', message='Quá thời gian thao tác Chrome (6 phút); yêu cầu đã dừng, không tự gửi lại.')
                        if table is session.page_load_requests and key == session.page_load_active:
                            session.loading_pages = False
                            session.page_load_active = ''
        self.refresh_buttons()
        if self.page_scheduler is not None:
            self.page_scheduler.tick()
        if self.page_bridge is not None:
            try:
                self.page_bridge.tick()
                if self.bridge_enabled.get() != self.page_bridge.enabled:
                    self.bridge_enabled.set(self.page_bridge.enabled)
                    self.update_bridge_notice()
            except (OSError, ValueError, RuntimeError) as exc:
                self.bridge_notice.set('Kết nối web chưa sẵn sàng: '+str(exc))
        self.timer = self.after(250, self.poll)

    def on_destroy(self, event):
        if event.widget is not self:
            return
        self.destroyed = True
        if self.page_bridge is not None:
            try:
                self.page_bridge.close()
            except (OSError, ValueError, RuntimeError):
                pass  # Closing the bridge must not prevent profile flushing.
        if self.page_scheduler is not None:
            self.page_scheduler.close()
        for session in self.sessions.values():
            session.cancel.set()
            session.finished.set()
        if self.timer is not None:
            try:
                self.after_cancel(self.timer)
            except tk.TclError:
                pass
            self.timer = None
