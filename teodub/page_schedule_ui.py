"""Explicit video schedule approval and a passive, per-Page schedule view."""
from copy import deepcopy
import time
import tkinter as tk
from tkinter import messagebox, ttk

from teodub.page_schedule import STATE_LABELS, display_time, parse_time
from teodub.page_publish_settings import result_checks_enabled


SCHEDULE_NOTE = ('Giữ Tèo mở và máy không ngủ đến giờ đăng; Chrome cần có phiên đăng nhập đúng tài khoản/Page. '
    'Đóng cửa sổ quản lý Page vẫn giữ lịch, đóng Tèo sẽ dừng lịch; mở lại cần bật tiếp. '
    'Facebook có thể yêu cầu xác minh hoặc hạn chế tài khoản; Tèo không bảo đảm tránh khóa/checkpoint. '
    'Nếu lỗi hoặc chưa rõ kết quả, lịch dừng để bạn kiểm tra, không tự đăng lại.')


def future_time(value):
    due_at = parse_time(value)
    current = time.time()
    if not current + 5 < due_at < current + 366 * 86400:
        raise ValueError('Chọn giờ đăng trong tương lai, tối đa một năm tới (UTC+7).')
    return due_at


def preview_text(account_name, page, source, body, due_at):
    return ('Tài khoản: ' + account_name + '\n'
        'Page: ' + page['name'] + '\n'
        'Link Page: ' + page['url'] + '\n'
        'Page ID: ' + (page.get('page_id', '') or 'Chưa lưu ID; cần kiểm tra đúng Page trong Chrome') + '\n\n'
        'Video thành phẩm: ' + source['label'] + '\n'
        'Tệp: ' + source['path'] + '\n'
        'Dung lượng: ' + str(source['size']) + ' byte\n'
        'Mã kiểm tra video: ' + source['sha256'] + '\n\n'
        'Giờ đăng: ' + display_time(due_at) + ' (UTC+7)\n\n'
        'Nội dung sẽ đăng:\n' + (body or '(Chỉ video, không có caption/hashtag)'))


class ScheduleVideoDialog(tk.Toplevel):
    def __init__(self, parent, owner, source, initial_account='', initial_page=''):
        super().__init__(parent)
        self.owner = owner
        self.source = deepcopy(source)
        self.controller = owner.get_page_scheduler()
        self.store = self.controller.store
        self.registry = owner.store
        self.account_id = self.page_key = self.saved_id = ''
        self.preview_window = None
        self.preview_data = None
        self.saving = self.done = False
        self.schedule = None
        self.account = tk.StringVar(self)
        self.page = tk.StringVar(self)
        self.due_time = tk.StringVar(self, display_time(time.time() + 3600))
        self.status = tk.StringVar(self)
        self.edit_controls = []
        self.title('Đăng video — Lưu lịch cho Page')
        self.geometry('860x760')
        self.minsize(730, 640)
        self.protocol('WM_DELETE_WINDOW', self.destroy)
        self.build()
        try:
            self.registry_data = self.registry.load()
            self.accounts = self.registry_data['accounts']
            self.account_picker.configure(values=[row['name'] for row in self.accounts])
            ident = initial_account or self.registry_data.get('selected_account_id', '')
            index = next((index for index, row in enumerate(self.accounts) if row['id'] == ident), 0)
            if self.accounts:
                self.account_picker.current(index)
                self.select_account(initial_page=initial_page)
            else:
                self.status.set('Chưa có tài khoản Facebook đã lưu. Thêm tài khoản/Page trong Quản lý Page trước.')
                self.preview_button.configure(state='disabled')
        except (OSError, ValueError) as exc:
            self.status.set(str(exc))
            self.preview_button.configure(state='disabled')

    def build(self):
        shell = ttk.Frame(self, padding=16)
        shell.pack(fill='both', expand=True)
        ttk.Label(shell, text='Lên lịch video thành phẩm', font=('Segoe UI', 17, 'bold')).pack(anchor='w')
        ttk.Label(shell, text=self.source['label'] + '\n' + self.source['path'], wraplength=790).pack(anchor='w', pady=(6, 12))
        ttk.Label(shell, text='Tài khoản Facebook đã lưu').pack(anchor='w')
        self.account_picker = ttk.Combobox(shell, textvariable=self.account, state='readonly')
        self.account_picker.pack(fill='x', pady=(3, 10))
        self.account_picker.bind('<<ComboboxSelected>>', self.select_account)
        ttk.Label(shell, text='Page của tài khoản này').pack(anchor='w')
        self.page_picker = ttk.Combobox(shell, textvariable=self.page, state='readonly')
        self.page_picker.pack(fill='x', pady=(3, 10))
        self.page_picker.bind('<<ComboboxSelected>>', self.select_page)
        ttk.Label(shell, text='Caption cố định cho Page (dùng nguyên nội dung bạn nhập)').pack(anchor='w')
        self.caption = tk.Text(shell, height=6, wrap='word')
        self.caption.pack(fill='both', expand=True, pady=(3, 8))
        ttk.Label(shell, text='Hashtag cố định').pack(anchor='w')
        self.hashtags = tk.Text(shell, height=3, wrap='word')
        self.hashtags.pack(fill='x', pady=(3, 6))
        self.template_button = ttk.Button(shell, text='Lưu mẫu caption / hashtag cho Page', command=self.save_template)
        self.template_button.pack(anchor='w', pady=(0, 10))
        ttk.Label(shell, text='Giờ đăng tại Việt Nam (UTC+7) — YYYY-MM-DD HH:MM').pack(anchor='w')
        self.time_entry = ttk.Entry(shell, textvariable=self.due_time)
        self.time_entry.pack(fill='x', pady=(3, 8))
        ttk.Label(shell, text=SCHEDULE_NOTE, wraplength=790, justify='left').pack(anchor='w', pady=6)
        ttk.Label(shell, textvariable=self.status, wraplength=790, justify='left').pack(anchor='w', pady=6)
        actions = ttk.Frame(shell)
        actions.pack(fill='x', pady=(6, 0))
        self.preview_button = ttk.Button(actions, text='Xem trước lịch đăng', command=self.show_preview)
        self.preview_button.pack(side='left')
        ttk.Button(actions, text='Đóng', command=self.destroy).pack(side='right')
        self.edit_controls = [self.account_picker, self.page_picker, self.caption, self.hashtags,
                              self.time_entry, self.template_button]

    def select_account(self, event=None, *, initial_page=''):
        if self.saved_id or self.saving or self.done:
            return
        index = self.account_picker.current()
        if not 0 <= index < len(self.accounts):
            self.status.set('Chọn tài khoản đã lưu trong danh sách.')
            return
        self.account_id = self.accounts[index]['id']
        self.pages = [row for row in self.registry_data['pages'] if row['account_id'] == self.account_id]
        self.page_picker.configure(values=[row['name'] + ' · ' + (row.get('page_id', '') or 'Chưa có ID') + ' · ' + row['url'] for row in self.pages])
        self.page_key = ''
        self.page.set('')
        self.caption.delete('1.0', 'end')
        self.hashtags.delete('1.0', 'end')
        if self.pages:
            selected = next((index for index, row in enumerate(self.pages) if row['id'] == initial_page), 0)
            self.page_picker.current(selected)
            self.select_page()
        else:
            self.status.set('Tài khoản này chưa có Page đã lưu. Thêm/Load Page trước khi xếp lịch.')
            self.preview_button.configure(state='disabled')

    def select_page(self, event=None):
        if self.saved_id or self.saving or self.done:
            return
        index = self.page_picker.current()
        if not 0 <= index < len(self.pages):
            self.status.set('Chọn Page đã lưu trong tài khoản này.')
            return
        candidate = self.pages[index]['id']
        try:
            template = self.store.template(self.account_id, candidate)
            self.page_key = candidate
            self.caption.delete('1.0', 'end')
            self.caption.insert('1.0', template['caption'])
            self.hashtags.delete('1.0', 'end')
            self.hashtags.insert('1.0', template['hashtags'])
            self.preview_button.configure(state='normal')
            self.status.set('Đã nạp mẫu của Page. Xem trước để duyệt tài khoản, Page, video, nội dung và giờ đăng.')
        except (OSError, ValueError) as exc:
            self.page_key = ''
            self.preview_button.configure(state='disabled')
            self.status.set(str(exc))

    def values(self):
        if not self.account_id or not self.page_key:
            raise ValueError('Chọn tài khoản và Page đã lưu trước khi xếp lịch.')
        data = self.registry.load()
        account = next((row for row in data['accounts'] if row['id'] == self.account_id), None)
        page = next((row for row in data['pages'] if row['id'] == self.page_key and row['account_id'] == self.account_id), None)
        if not account or not page:
            raise ValueError('Tài khoản/Page đã thay đổi hoặc bị bỏ. Đóng và mở lại lịch đăng để chọn đúng Page.')
        caption, hashtags = self.caption.get('1.0', 'end-1c'), self.hashtags.get('1.0', 'end-1c')
        if len(caption) + len(hashtags) > 60000:
            raise ValueError('Caption và hashtag tối đa tổng 60.000 ký tự.')
        return deepcopy(dict(account=account, page=page, source=self.source, caption=caption, hashtags=hashtags,
            body='\n\n'.join(value.strip() for value in (caption, hashtags) if value.strip()), due_at=future_time(self.due_time.get())))

    def save_template(self):
        if self.saved_id or self.saving or self.done:
            return
        try:
            if not self.account_id or not self.page_key:
                raise ValueError('Chọn tài khoản và Page trước khi lưu mẫu.')
            self.store.save_template(self.account_id, self.page_key, self.caption.get('1.0', 'end-1c'), self.hashtags.get('1.0', 'end-1c'))
            self.status.set('Đã lưu mẫu nguyên văn cho Page này; chưa tạo hoặc bật lịch đăng.')
        except (OSError, ValueError) as exc:
            self.status.set(str(exc))

    def show_preview(self):
        if self.saving or self.done:
            return
        if self.preview_window is not None:
            self.preview_window.lift()
            return
        try:
            if not self.saved_id:
                self.preview_data = self.values()
        except (OSError, ValueError) as exc:
            self.status.set(str(exc))
            return
        data = self.preview_data
        modal = self.preview_window = tk.Toplevel(self)
        modal.title('Duyệt lịch đăng video')
        modal.geometry('850x700')
        modal.transient(self)
        shell = ttk.Frame(modal, padding=14)
        shell.pack(fill='both', expand=True)
        self.preview_content = tk.Text(shell, wrap='word', height=20)
        self.preview_content.pack(fill='both', expand=True)
        self.preview_content.insert('1.0', preview_text(data['account']['name'], data['page'], data['source'], data['body'], data['due_at']))
        self.preview_content.configure(state='disabled')
        ttk.Label(shell, text=SCHEDULE_NOTE, wraplength=790).pack(anchor='w', pady=8)
        ttk.Label(shell, text='Bấm nút dưới để cho phép Tèo tự chuẩn bị và bấm Đăng trong Chrome vào giờ đã duyệt.', wraplength=790).pack(anchor='w')
        self.preview_status = tk.StringVar(modal)
        ttk.Label(shell, textvariable=self.preview_status, wraplength=790).pack(anchor='w', pady=6)
        actions = ttk.Frame(shell)
        actions.pack(fill='x')
        self.approve_button = ttk.Button(actions, text='Bật lịch đã lưu' if self.saved_id else 'Lưu lịch / Cho phép tự đăng', command=self.approve)
        self.approve_button.pack(side='left')
        ttk.Button(actions, text='Quay lại', command=self.close_preview).pack(side='right')
        modal.protocol('WM_DELETE_WINDOW', self.close_preview)
        modal.grab_set()

    def close_preview(self):
        if self.saving:
            return
        if self.preview_window is not None:
            self.preview_window.destroy()
            self.preview_window = None

    def freeze_fields(self):
        for widget in self.edit_controls:
            widget.configure(state='disabled')

    def approve(self):
        if self.saving or self.done or self.preview_window is None or self.preview_data is None:
            return
        self.saving = True
        self.approve_button.configure(state='disabled')
        try:
            data = self.preview_data
            current = self.registry.load()
            account = next((row for row in current['accounts'] if row['id'] == data['account']['id']), None)
            page = next((row for row in current['pages'] if row['id'] == data['page']['id'] and row['account_id'] == data['account']['id']), None)
            if (not account or account['name'] != data['account']['name'] or not page or
                    any(page.get(key, '') != data['page'].get(key, '') for key in ('name', 'url', 'page_id'))):
                raise ValueError('Tài khoản/Page đã đổi từ lúc xem trước. Quay lại và mở lại lịch để duyệt đúng đích đăng.')
            if not self.saved_id:
                # The exact frozen preview is the approved content, even if another UI changes selection.
                row = self.store.create(data['account']['id'], data['page']['id'], data['source'],
                                        data['caption'], data['hashtags'], data['due_at'],
                                        expected_target={key: data['page'].get(key, '') for key in ('name', 'url', 'page_id')})
                self.saved_id = row['id']
                self.freeze_fields()
            self.schedule = self.controller.start(self.saved_id)
            self.done = True
            self.status.set('Đã lưu và bật lịch: ' + display_time(data['due_at']) + ' (UTC+7). Giữ Tèo mở đến giờ đăng.')
            if hasattr(self.owner, 'status'):
                self.owner.status.set('Đã bật lịch video cho ' + data['page']['name'] + ' lúc ' + display_time(data['due_at']) + ' (UTC+7).')
            self.preview_button.configure(state='disabled')
        except (OSError, ValueError, RuntimeError) as exc:
            message = ('Lịch đã lưu nhưng chưa bật. ' if self.saved_id else 'Chưa lưu lịch. ') + str(exc)
            self.status.set(message)
            self.preview_status.set(message)
            self.approve_button.configure(state='normal', text='Bật lịch đã lưu' if self.saved_id else 'Lưu lịch / Cho phép tự đăng')
        finally:
            self.saving = False
        if self.done:
            self.close_preview()

    def destroy(self):
        if self.preview_window is not None:
            self.preview_window.destroy()
            self.preview_window = None
        super().destroy()


class SchedulePostDialog(tk.Toplevel):
    """A concrete, fixed text draft plus the selected time authorizes this schedule."""
    def __init__(self, manager):
        from teodub.page_command_bridge import public_post
        self.manager = manager
        self.owner = manager.owner
        self.account_id, self.page_key, self.post_id = manager.account_id, manager.page_key, manager.post_id
        self.controller = self.owner.get_page_scheduler()
        self.store = self.controller.store
        post = self.store.activity.post(self.account_id, self.page_key, self.post_id)
        if (post['kind'] != 'text' or post['media'] or post['delivery_mode'] != 'browser'
                or post['browser_finish_mode'] != 'tool' or post['state'] not in {'draft', 'failed'}):
            raise ValueError('Lưu bản nháp bài chữ với cách đăng Trình duyệt web và Tèo bấm Đăng trước khi hẹn giờ.')
        self.target = {key: self.store.activity.page(self.account_id, self.page_key).get(key, '')
                       for key in ('name', 'url', 'page_id')}
        self.post_key = public_post(post)['post_key']
        self.saved_id = ''
        self.saving = self.done = False
        super().__init__(manager)
        self.title('Hẹn giờ bài chữ — '+self.target['name'])
        self.geometry('780x670')
        self.transient(manager)
        self.due_time = tk.StringVar(self, display_time(time.time()+3600))
        self.status = tk.StringVar(self)
        shell = ttk.Frame(self, padding=16)
        shell.pack(fill='both', expand=True)
        account = next(row for row in self.owner.store.load()['accounts'] if row['id'] == self.account_id)
        ttk.Label(shell, text='Duyệt lịch đăng bài chữ', font=('Segoe UI', 17, 'bold')).pack(anchor='w')
        ttk.Label(shell, text='Tài khoản: '+account['name']+'\nPage: '+self.target['name']+
            ' · ID '+(self.target['page_id'] or 'Chưa có')+'\n'+self.target['url'], wraplength=730).pack(anchor='w', pady=10)
        self.content = tk.Text(shell, wrap='word', height=13)
        self.content.pack(fill='both', expand=True)
        self.content.insert('1.0', post['body'])
        self.content.configure(state='disabled')
        ttk.Label(shell, text='Giờ đăng tại Việt Nam (UTC+7) — YYYY-MM-DD HH:MM').pack(anchor='w', pady=(10, 4))
        self.time_entry = ttk.Entry(shell, textvariable=self.due_time)
        self.time_entry.pack(fill='x')
        ttk.Label(shell, text=SCHEDULE_NOTE, wraplength=730).pack(anchor='w', pady=10)
        ttk.Label(shell, text='Bấm nút dưới để cho phép Tèo tự mở Page, điền đúng bài chữ phía trên và bấm Đăng vào giờ bạn chọn.',
                  wraplength=730).pack(anchor='w')
        ttk.Label(shell, textvariable=self.status, wraplength=730).pack(anchor='w', pady=8)
        actions = ttk.Frame(shell)
        actions.pack(fill='x')
        self.approve_button = ttk.Button(actions, text='Lưu lịch / Cho phép tự đăng', command=self.approve)
        self.approve_button.pack(side='left')
        ttk.Button(actions, text='Đóng', command=self.destroy).pack(side='right')
        self.grab_set()

    def approve(self):
        if self.saving or self.done:
            return
        self.saving = True
        self.approve_button.configure(state='disabled')
        try:
            if not self.saved_id:
                row = self.store.create_post(self.account_id, self.page_key, self.post_id,
                    future_time(self.due_time.get()), expected_target=self.target, expected_post_key=self.post_key)
                self.saved_id = row['id']
                self.time_entry.configure(state='disabled')
            self.controller.start(self.saved_id)
            row = self.store.get(self.saved_id)
            self.done = True
            self.manager.status.set('Đã bật lịch bài chữ cho '+self.target['name']+' lúc '+display_time(row['due_at'])+' (UTC+7).')
            self.manager.schedule_panel.refresh()
        except (OSError, ValueError, RuntimeError) as exc:
            self.status.set(('Lịch đã lưu nhưng chưa bật. ' if self.saved_id else 'Chưa lưu lịch. ')+str(exc))
            self.approve_button.configure(state='normal', text='Bật lịch đã lưu' if self.saved_id else 'Lưu lịch / Cho phép tự đăng')
        finally:
            self.saving = False
        if self.done:
            self.destroy()


class PageSchedulePanel(ttk.Frame):
    def __init__(self, parent, manager):
        super().__init__(parent, padding=12)
        self.manager = manager
        self.controller = manager.owner.get_page_scheduler()
        self.store = self.controller.store
        self.rows = {}
        self.timer = None
        self.closed = False
        self.time_window = None
        self.add_window = None
        self.recovery_window = None
        self.check_window = None
        self.now_window = None
        self.now_preview = None
        self.now_busy = False
        self.bulk_busy = False
        self.remove_busy = False
        self.bulk_report = ''
        self.status = tk.StringVar(self)
        self.summary = tk.StringVar(self)
        self.detail = tk.StringVar(self)
        self.show_removed = tk.BooleanVar(self, False)
        self.details_visible = False
        self.bind('<Destroy>', self.on_destroy, add='+')
        self.build()
        self.refresh()
        self.timer = self.after(1000, self.poll)

    def build(self):
        heading = ttk.Frame(self)
        heading.pack(fill='x')
        ttk.Label(heading, text='Lịch đăng của Page', font=('Segoe UI', 14, 'bold')).pack(side='left')
        self.add_button = ttk.Button(heading, text='Thêm bài đăng…', command=self.add_post)
        self.add_button.pack(side='right')
        self.remove_button = ttk.Button(heading, text='Xóa video', command=self.remove_selected)
        self.remove_button.pack(side='right', padx=(0, 8))
        self.check_button = ttk.Button(heading, text='Kiểm tra lại trạng thái…', command=self.open_result_check)
        self.check_button.pack(side='right', padx=(0, 8))
        ttk.Label(self, text='Thêm bài chữ hoặc video chiến dịch vào lịch. Chọn bài để Đăng ngay hoặc bật lịch theo giờ.',
                  wraplength=760).pack(anchor='w', pady=(4, 8))
        table = ttk.Frame(self)
        table.pack(fill='both', expand=True, pady=(0, 8))
        self.tree = ttk.Treeview(table, columns=('video', 'due', 'state', 'reason'), show='headings', selectmode='extended', height=4)
        for column, label, width in (('video', 'Bài viết / Video', 210), ('due', 'Giờ đăng (UTC+7)', 145),
                                     ('state', 'Trạng thái', 145), ('reason', 'Kết quả / cần xử lý', 250)):
            self.tree.heading(column, text=label)
            self.tree.column(column, width=width, minwidth=80, stretch=True)
        self.tree.pack(side='left', fill='both', expand=True)
        scrollbar = ttk.Scrollbar(table, orient='vertical', command=self.tree.yview)
        scrollbar.pack(side='right', fill='y')
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.bind('<<TreeviewSelect>>', self.select_row)
        actions = ttk.Frame(self)
        actions.pack(fill='x', pady=(0, 6))
        self.now_button = ttk.Button(actions, text='Đăng ngay', command=self.post_immediately)
        self.now_button.pack(side='left', padx=(0, 4))
        self.start_button = ttk.Button(actions, text='Bật phần đã chọn', command=self.start)
        self.start_button.pack(side='left')
        self.start_all_button = ttk.Button(actions, text='Bật tất cả', command=self.start_all)
        self.start_all_button.pack(side='left', padx=4)
        self.pause_button = ttk.Button(actions, text='Tạm dừng phần đã chọn', command=self.pause)
        self.pause_button.pack(side='left', padx=4)
        self.refresh_button = ttk.Button(actions, text='Làm mới', command=self.refresh)
        self.refresh_button.pack(side='left', padx=4)
        self.open_button = ttk.Button(actions, text='Mở bài / Page', command=self.open_selected)
        self.open_button.pack(side='left', padx=4)
        selection_actions = ttk.Frame(self)
        selection_actions.pack(fill='x', pady=(0, 8))
        self.select_all_button = ttk.Button(selection_actions, text='Chọn tất cả', command=self.select_all)
        self.select_all_button.pack(side='left')
        ttk.Label(selection_actions, text='Ctrl: chọn nhiều · Shift: chọn một dải').pack(side='left', padx=8)
        self.retry_schedule_button = ttk.Button(selection_actions, text='Xếp lại lịch lỗi…',
                                                command=lambda: self.change_time(retry=True))
        self.recovery_button = ttk.Button(selection_actions, text='Khôi phục lịch đã chọn…', command=self.open_recovery)
        self.recovery_button.pack(side='left', padx=4)
        self.details_button = ttk.Button(selection_actions, text='Chi tiết / Tùy chọn…', command=self.toggle_details)
        self.details_button.pack(side='right')
        recreate_actions = ttk.Frame(self)
        recreate_actions.pack(fill='x', pady=(0, 8))
        self.recreate_button = ttk.Button(recreate_actions, text='Bỏ lượt cũ và tạo lịch mới…',
                                         command=lambda: self.open_recovery(recreate=True))
        self.recreate_button.pack(side='left')
        ttk.Label(recreate_actions, text='Giữ video và caption; chọn giờ đăng mới.').pack(side='left', padx=8)
        self.show_removed_check = ttk.Checkbutton(recreate_actions, text='Hiện video đã xóa',
                                                 variable=self.show_removed, command=self.refresh)
        self.show_removed_check.pack(side='right')
        summary = ttk.LabelFrame(self, text='Lịch đã chọn', padding=8)
        summary.pack(fill='x')
        ttk.Label(summary, textvariable=self.summary, wraplength=740, justify='left').pack(anchor='w')
        ttk.Label(self, textvariable=self.status, wraplength=760, justify='left').pack(anchor='w', pady=6)
        self.details_window = tk.Toplevel(self)
        self.details_window.withdraw()
        self.details_window.title('Chi tiết lịch — '+self.manager.target['name'])
        self.details_window.geometry('820x620')
        self.details_window.minsize(700, 480)
        self.details_window.transient(self.winfo_toplevel())
        self.details_window.protocol('WM_DELETE_WINDOW', self.toggle_details)
        self.details_frame = ttk.LabelFrame(self.details_window, text='Chi tiết / Tùy chọn', padding=12)
        secondary = ttk.Frame(self.details_frame)
        secondary.pack(fill='x')
        self.time_button = ttk.Button(secondary, text='Đổi giờ', command=self.change_time)
        self.time_button.pack(side='left')
        self.cancel_button = ttk.Button(secondary, text='Bỏ lịch chưa gửi', command=self.cancel_selected)
        self.cancel_button.pack(side='left', padx=5)
        self.bulk_result_button = ttk.Button(secondary, text='Kết quả bật lịch', command=self.show_bulk_result)
        self.bulk_result_button.pack(side='left', padx=5)
        self.detail_remove_button = ttk.Button(secondary, text='Xóa video', command=self.remove_selected)
        self.detail_remove_button.pack(side='left', padx=5)
        ttk.Button(secondary, text='Đóng', command=self.toggle_details).pack(side='right')
        self.details_status = ttk.Label(self.details_frame, textvariable=self.status, wraplength=760)
        self.details_status.pack(anchor='w', pady=(5, 0))
        self.detail_text = tk.Text(self.details_frame, height=5, wrap='word', state='disabled')
        self.detail_text.pack(fill='both', expand=True, pady=6)
        self.details_help = ttk.Label(self.details_frame, text='Bấm Thêm bài đăng để nhập bài chữ hoặc chọn video thành phẩm từ chiến dịch. '
                  'Bật tất cả chỉ áp dụng Page này; lịch quá giờ cần Đổi giờ.',
                  wraplength=760, justify='left')
        self.details_help.pack(anchor='w', pady=(0, 4))
        self.details_note = ttk.Label(self.details_frame, text=SCHEDULE_NOTE, wraplength=760, justify='left')
        self.details_note.pack(anchor='w')
        self.details_frame.bind('<Configure>', self.wrap_details)
        self.select_row()

    def add_post(self):
        if self.add_window is not None and self.add_window.winfo_exists():
            self.add_window.lift()
            return
        try:
            from teodub.page_schedule_add_ui import AddScheduledPostDialog
            self.add_window = AddScheduledPostDialog(self)
        except (OSError, ValueError) as exc:
            self.status.set(str(exc))

    def open_recovery(self, *, recreate=False):
        if self.recovery_window is not None and self.recovery_window.winfo_exists():
            self.recovery_window.lift()
            return
        try:
            selected = list(self.tree.selection())
            if not selected:
                raise ValueError('Chọn một hoặc nhiều lịch video cần khôi phục.')
            from teodub.page_schedule_recovery_ui import ScheduleRecoveryDialog
            self.recovery_window = ScheduleRecoveryDialog(self, selected, recreate=recreate)
        except (OSError, ValueError, RuntimeError) as exc:
            self.status.set(str(exc))

    def open_result_check(self):
        if not result_checks_enabled(self.store.data_dir):
            self.status.set('Đã tạm dừng kiểm tra trạng thái. Bật lại trong Nâng cao khi cần.')
            return
        if self.check_window is not None and self.check_window.winfo_exists():
            self.check_window.lift()
            return
        try:
            row = self.selected()
            can_follow = getattr(self.controller, 'can_follow_result', None)
            if can_follow and can_follow(row):
                self.controller.follow_result(self.manager.account_id, self.manager.page_key, row['id'])
                self.status.set('Đã nhận kiểm tra lại kết quả; Tèo chỉ xác minh bài đã gửi và không tải hoặc đăng lại video.')
                self.refresh()
                return
            if not self.controller.can_inspect_result(row):
                raise ValueError('Chỉ kiểm tra lượt đã gửi tới Facebook. Bản nháp mới chưa có lượt gửi để đối chiếu.')
            from teodub.page_schedule_check_ui import ScheduleResultCheckDialog
            self.check_window = ScheduleResultCheckDialog(self, row['id'])
        except (OSError, ValueError, RuntimeError) as exc:
            self.status.set(str(exc))

    def wrap_details(self, event):
        width = max(160, event.width - 28)
        for label in (self.details_status, self.details_help, self.details_note):
            if int(label.cget('wraplength')) != width:
                label.configure(wraplength=width)

    def toggle_details(self):
        self.details_visible = not self.details_visible
        if self.details_visible:
            self.details_frame.pack(fill='both', expand=True, padx=12, pady=12)
            self.details_window.deiconify()
            self.details_window.lift()
        else:
            self.details_frame.pack_forget()
            self.details_window.withdraw()

    def select_all(self):
        self.tree.selection_set(self.tree.get_children())
        self.select_row()

    def refresh(self):
        if self.closed:
            return
        try:
            rows = self.store.list(self.manager.account_id, self.manager.page_key)
            if not self.show_removed.get():
                rows = [row for row in rows if row['state'] != 'removed']
            selected = self.tree.selection()
            scroll = self.tree.yview()[0]
            focused = self.tree.focus()
            self.rows = {row['id']: row for row in rows}
            self.tree.delete(*self.tree.get_children())
            for row in rows:
                self.tree.insert('', 'end', iid=row['id'], values=(row['source']['label'], display_time(row['due_at']),
                    self.row_state_label(row), row['message']))
            self.tree.selection_set([ident for ident in selected if ident in self.rows])
            if focused in self.rows:
                self.tree.focus(focused)
            self.tree.yview_moveto(scroll)
            self.select_row()
        except (OSError, ValueError) as exc:
            self.status.set(str(exc))

    def selected(self):
        selected = self.tree.selection()
        if len(selected) != 1 or selected[0] not in self.rows:
            raise ValueError('Chọn đúng một lịch để đăng ngay, đổi giờ, mở bài hoặc bỏ lịch.')
        row = self.store.get(selected[0])
        if (row['account_id'], row['page_key']) != (self.manager.account_id, self.manager.page_key):
            raise ValueError('Lịch không thuộc tài khoản/Page này; làm mới để kiểm tra.')
        return row

    def row_state_label(self, row):
        label = STATE_LABELS.get(row['state'], row['state'])
        if row['state'] == 'submitted':
            return 'Đã gửi — chưa xác minh'
        if row['state'] == 'armed' and row['due_at'] <= time.time():
            return 'Đã đến giờ — chờ lượt gửi'
        if row['state'] == 'verifying':
            if not result_checks_enabled(self.store.data_dir):
                return 'Đã gửi — chưa xác minh'
            if row['id'] not in self.controller.active:
                return 'Đã gửi — chờ kiểm tra lại'
            return 'Đã gửi — đang xác minh'
        if row['state'] in {'paused', 'needs_attention'} and row['attempted_at'] and row['phase'] in {'publish', 'verify'}:
            try:
                post = self.store.activity.post(row['account_id'], row['page_key'], row['post_id'])
                if post['state'] == 'unknown':
                    return 'Chưa xác minh đã đăng'
            except (OSError, ValueError):
                pass
        return label

    def select_row(self, event=None):
        selected = self.tree.selection()
        row = self.rows.get(selected[0]) if selected else None
        single = len(selected) == 1
        inspect_method = getattr(self.controller, 'can_inspect_result', None)
        follow_method = getattr(self.controller, 'can_follow_result', None)
        checkable = bool(result_checks_enabled(self.store.data_dir) and single and row and ((follow_method and follow_method(row))
                                            or (inspect_method and inspect_method(row))))
        self.check_button.configure(state='normal' if checkable else 'disabled')
        retryable = bool(single and row and self.store.can_retry_preparation(row))
        if retryable:
            self.retry_schedule_button.pack(side='left', padx=5)
        else:
            self.retry_schedule_button.pack_forget()
        available = any(not self.rows[ident]['attempted_at'] and self.rows[ident]['state'] in {'scheduled', 'paused', 'needs_attention'} for ident in selected if ident in self.rows)
        recoverable = bool(selected) and all(ident in self.rows and self.store.can_recover_schedule(self.rows[ident])
                                              for ident in selected)
        self.recreate_button.configure(state='normal' if recoverable and not self.bulk_busy else 'disabled')
        self.recovery_button.configure(state='normal' if recoverable else 'disabled')
        self.now_button.configure(state='normal' if single and row and not row['attempted_at']
            and not row['request_id'] and row['state'] in {'scheduled', 'armed', 'paused', 'needs_attention'}
            and not self.now_busy else 'disabled')
        self.start_button.configure(state='normal' if available else 'disabled')
        self.start_all_button.configure(state='normal' if any(item['state'] != 'removed' for item in self.rows.values())
            and not self.bulk_busy else 'disabled')
        self.time_button.configure(state='normal' if single and available else 'disabled')
        self.pause_button.configure(state='normal' if any(self.rows[ident]['state'] in {'scheduled','paused','armed','needs_attention','preparing','submitting'} for ident in selected if ident in self.rows) else 'disabled')
        self.open_button.configure(state='normal' if single and row else 'disabled')
        self.cancel_button.configure(state='normal' if single and row and not row['attempted_at'] and row['state'] not in {'published', 'cancelled', 'removed'} else 'disabled')
        removable = any(self.is_video(self.rows[ident]) and self.rows[ident]['state'] not in {'removed', 'preparing', 'submitting', 'verifying'}
                        for ident in selected if ident in self.rows)
        for button in (self.remove_button, self.detail_remove_button):
            button.configure(state='normal' if removable and not self.remove_busy else 'disabled')
        if row and single:
            snapshot = row['snapshot']
            caption = ' '.join(row['body'].split())
            if len(caption) > 240:
                caption = caption[:237] + '…'
            result = ' '.join((row['message'] or STATE_LABELS.get(row['state'], row['state'])).split())
            if len(result) > 180:
                result = result[:177] + '…'
            self.summary.set(row['source']['label'] + ' · ' + display_time(row['due_at']) + ' (UTC+7)\n'
                'Caption: ' + (caption or '(Chỉ video, không có caption)') + '\n'
                'Trạng thái: ' + self.row_state_label(row) + '\n'
                'Kết quả: ' + result)
            if row['state'] == 'verifying':
                self.summary.set(self.summary.get()+'\nĐã thực hiện thao tác Đăng; chưa xác nhận bài đã xuất hiện. Các bài khác đến giờ vẫn được xử lý.')
            if row['state'] == 'submitted':
                self.summary.set(self.summary.get()+'\nĐã tạm dừng kiểm tra trạng thái sau khi gửi. Các bài tiếp theo vẫn chạy; lượt này không tự gửi lại.')
            if self.row_state_label(row) == 'Chưa xác minh đã đăng':
                self.summary.set(self.summary.get()+'\nLượt cũ đã gửi nhưng chưa xác minh bài trên Facebook; không tự đăng lại.')
            if not row['attempted_at'] and row['state'] in {'scheduled', 'armed', 'paused'}:
                self.summary.set(self.summary.get()+'\nBản nháp này chưa có lượt gửi; không dùng bài Facebook cũ để xác nhận đã đăng.')
            self.detail.set('Page: ' + snapshot['target_name'] + ' · ' + snapshot['target_url'] +
                ' · ID: ' + (snapshot['target_id'] or 'Chưa lưu') + '\n'
                'Mã lịch: ' + row['id'] + ' · Mã bài: ' + row['post_id'] + '\n'
                'Trạng thái: ' + self.row_state_label(row) + '\n'
                'Nội dung: ' + row['source']['label'] + '\nTệp: ' + row['source'].get('path', '(Bài chữ, không có tệp)') + '\n'
                'Mã nguồn: ' + row['source'].get('key', '') + '\n'
                'Mã kiểm tra: ' + row['source'].get('sha256', 'Bản nháp được khóa theo nội dung đã duyệt') + '\nGiờ đăng: ' + display_time(row['due_at']) + ' (UTC+7)\n'
                'Caption / hashtag đã duyệt:\n' + row['body'] + '\n' + row['message'])
            for event in self.store.preparation_retry_history(row['id']):
                previous = event['previous']
                self.detail.set(self.detail.get()+'\n\nLượt lỗi đã lưu: '+previous['attempted_at']+' · '+previous['message']+
                    '\nXếp lại lúc '+event['at']+' → giờ mới '+display_time(event['new_due_at'])+' (UTC+7).')
            for event in self.store.recovery_history(row['id']):
                previous = event['previous']
                self.detail.set(self.detail.get()+'\n\nKhôi phục lịch lúc '+event['at']+
                    '\nLượt cũ: '+previous['post']['target_name']+' · '+previous['post']['state']+
                    ' · '+previous['post']['message']+'\nGiờ mới: '+display_time(event['new_due_at'])+' (UTC+7).')
            for event in self.store.immediate_history(row['id']):
                self.detail.set(self.detail.get()+'\n\nĐăng ngay lúc '+event['at']+
                    ' · Giờ cũ: '+display_time(event['previous']['due_at'])+' (UTC+7).')
            if row['state'] == 'removed':
                self.detail.set(self.detail.get()+'\n\nVideo đã xóa khỏi danh sách chạy. Đây là lịch sử chỉ đọc; '
                    'tệp video và bài Facebook được giữ.')
                for event in self.store.removal_history(row['id']):
                    previous = event['previous']['schedule']
                    self.detail.set(self.detail.get()+'\nXóa khỏi danh sách lúc '+event['at']+
                        '\nTrạng thái lịch trước khi xóa: '+STATE_LABELS.get(previous['state'], previous['state'])+
                        '\nKết quả trước khi xóa: '+previous['message'])
        elif selected:
            self.summary.set('Đã chọn '+str(len(selected))+' lịch của Page này. Bật hoặc tạm dừng phần đã chọn theo giờ đã lưu.')
            self.detail.set('Chọn một lịch để xem nội dung đã duyệt và thông tin nguồn.')
        else:
            self.summary.set('Chọn một lịch để xem caption và kết quả. Bật tất cả áp dụng cho '+str(len(self.rows))+' lịch của Page này.')
            self.detail.set('Chưa chọn lịch. Lịch đã lưu chỉ chạy sau khi bạn bật; mở tab này không tự đăng.')
        if self.bulk_report:
            self.detail.set(self.detail.get()+'\n\nKết quả bật lịch gần nhất:\n'+self.bulk_report)
        self.detail_text.configure(state='normal')
        self.detail_text.delete('1.0', 'end')
        self.detail_text.insert('1.0', self.detail.get())
        self.detail_text.configure(state='disabled')

    def start(self):
        self.start_rows(self.tree.selection())

    @staticmethod
    def is_video(row):
        return row['source'].get('kind') != 'text' and bool(row['source'].get('path'))

    def remove_selected(self):
        """Remove approved local rows without deleting media or Facebook posts."""
        if self.remove_busy:
            return
        self.remove_busy = True
        self.select_row()
        try:
            account_id, page_key = self.manager.account_id, self.manager.page_key
            identifiers = [ident for ident in self.tree.selection() if ident in self.rows
                and self.is_video(self.rows[ident]) and self.rows[ident]['state'] != 'removed']
            if not identifiers:
                raise ValueError('Chọn một hoặc nhiều video muốn xóa khỏi danh sách.')
            previews = self.store.removal_preview(identifiers, account_id=account_id, page_key=page_key)
            target = deepcopy(self.manager.target)
            labels = [item['schedule']['source']['label']+' · '+display_time(item['schedule']['due_at'])+' (UTC+7)'
                      for item in previews]
            description = ('Xóa '+str(len(previews))+' video khỏi danh sách Tèo?\n\n'
                'Page: '+target['name']+'\nLink Page: '+target['url']+'\n'
                'Page ID: '+(target.get('page_id', '') or 'Chưa lưu')+'\n\n'+
                '\n'.join(labels[:12])+('\n… và '+str(len(labels)-12)+' video khác.' if len(labels) > 12 else '')+
                '\n\nVideo đã xóa sẽ không chạy theo lịch này và được ẩn khỏi bảng. '
                'Tệp video trên máy, caption, lịch sử và bài đã đăng trên Facebook vẫn được giữ. '
                'Bạn có thể xem lại bằng “Hiện video đã xóa”. '
                'Xóa khỏi danh sách không tự đăng lại video.')
            if not messagebox.askyesno('Xóa video khỏi danh sách Tèo', description, parent=self.winfo_toplevel()):
                return
            if self.closed or (account_id, page_key) != (self.manager.account_id, self.manager.page_key):
                raise ValueError('Tài khoản/Page đã thay đổi từ lúc xác nhận. Làm mới và chọn lại video muốn xóa.')
            result = self.controller.remove_many(previews, account_id=account_id, page_key=page_key)
            skipped = result.get('skipped', {})
            labels_by_id = {item['id']: item['schedule']['source']['label'] for item in previews}
            self.status.set('Đã xóa '+str(len(result['removed']))+' video khỏi danh sách. '
                'Tệp video và bài Facebook được giữ.'+
                ('\nKhông xóa '+str(len(skipped))+': '+ '\n'.join(labels_by_id.get(ident, ident)+': '+message
                    for ident, message in skipped.items()) if skipped else ''))
            self.refresh()
        except (OSError, ValueError, RuntimeError) as exc:
            self.status.set(str(exc))
        finally:
            self.remove_busy = False
            if not self.closed:
                self.select_row()

    def post_immediately(self):
        """One explicit click runs the approved appointment; no second review dialog."""
        if self.now_busy:
            return
        self.now_busy = True
        self.now_button.configure(state='disabled')
        try:
            selected = self.selected()
            if (selected['attempted_at'] or selected['request_id'] or selected['finished_at'] or
                    selected['state'] not in {'scheduled', 'armed', 'paused', 'needs_attention'}):
                raise ValueError('Bài đã có lượt xử lý hoặc đã hoàn tất; Tèo không gửi lại để tránh đăng trùng.')
            row = self.controller.start_now(self.manager.account_id, self.manager.page_key, selected['id'],
                expected_schedule=deepcopy(selected))
            self.status.set('Tèo đang tự đăng '+row['source']['label']+'. Không cần thao tác trong Chrome; theo dõi kết quả ở bảng.')
            self.refresh()
        except (OSError, ValueError, RuntimeError) as exc:
            self.status.set(str(exc))
        finally:
            self.now_busy = False
            self.select_row()

    def preview_now(self):
        if self.now_busy:
            return
        if self.now_window is not None:
            self.now_window.lift()
            return
        try:
            row = self.selected()
            if (row['attempted_at'] or row['request_id'] or row['finished_at'] or
                    row['state'] not in {'scheduled', 'armed', 'paused', 'needs_attention'}):
                raise ValueError('Bài đã có lượt xử lý hoặc đã hoàn tất. Kiểm tra Facebook trước; không tự đăng lại.')
            post = self.store.activity.post(row['account_id'], row['page_key'], row['post_id'])
            if post['state'] not in {'draft', 'failed'} or post['archived']:
                raise ValueError('Bản nháp đã có lượt xử lý; kiểm tra Facebook trước khi đăng lại.')
            accounts = self.manager.owner.store.load()['accounts']
            account = next(item for item in accounts if item['id'] == row['account_id'])
            self.now_preview = deepcopy(row)
            modal = self.now_window = tk.Toplevel(self)
            modal.title('Đăng ngay — '+row['snapshot']['target_name'])
            modal.geometry('780x640')
            modal.minsize(620, 480)
            modal.transient(self.winfo_toplevel())
            shell = ttk.Frame(modal, padding=14)
            shell.pack(fill='both', expand=True)
            ttk.Label(shell, text='Đăng ngay bài đã chọn', font=('Segoe UI', 16, 'bold')).pack(anchor='w')
            self.now_content = tk.Text(shell, wrap='word', height=16)
            self.now_content.pack(fill='both', expand=True, pady=8)
            self.now_content.insert('1.0', 'Tài khoản: '+account['name']+'\nPage: '+row['snapshot']['target_name']+
                '\nLink Page: '+row['snapshot']['target_url']+'\nPage ID: '+row['snapshot']['target_id']+
                '\n\nBài / video: '+row['source']['label']+'\nTệp: '+row['source'].get('path', '(Bài chữ, không có tệp)')+
                '\nGiờ cũ: '+display_time(row['due_at'])+' (UTC+7)\n\nCaption / hashtag sẽ đăng:\n'+
                (row['body'] or '(Chỉ video, không có caption)'))
            self.now_content.configure(state='disabled')
            ttk.Label(shell, text='Tèo sẽ mở Chrome đúng tài khoản, tải video và bấm Đăng. '
                'Bài này chuyển sang đăng ngay và không đăng lại vào giờ cũ. '
                'Theo dõi tiến độ ở bảng Lịch đăng; chỉ báo thành công khi xác nhận được bài trên Facebook.',
                wraplength=710, justify='left').pack(anchor='w', pady=6)
            self.now_status = tk.StringVar(modal)
            ttk.Label(shell, textvariable=self.now_status, wraplength=710).pack(anchor='w', pady=6)
            actions = ttk.Frame(shell)
            actions.pack(fill='x')
            self.now_approve_button = ttk.Button(actions, text='Đăng ngay bài này', command=self.submit_now)
            self.now_approve_button.pack(side='left')
            ttk.Button(actions, text='Quay lại', command=self.close_now).pack(side='right')
            modal.protocol('WM_DELETE_WINDOW', self.close_now)
        except (OSError, ValueError, StopIteration) as exc:
            self.status.set(str(exc) or 'Không còn tài khoản của Page này; làm mới để kiểm tra.')

    def submit_now(self):
        if self.now_busy or self.now_window is None or self.now_preview is None:
            return
        self.now_busy = True
        self.now_approve_button.configure(state='disabled')
        started = False
        try:
            preview = self.now_preview
            row = self.controller.start_now(self.manager.account_id, self.manager.page_key, preview['id'],
                expected_schedule=deepcopy(preview))
            self.status.set('Đã nhận Đăng ngay: '+row['source']['label']+'. Theo dõi trạng thái và kết quả trong bảng.')
            self.tree.selection_set(row['id'])
            self.refresh()
            started = True
        except (OSError, ValueError, RuntimeError) as exc:
            self.status.set(str(exc))
            self.now_status.set(str(exc))
            self.now_approve_button.configure(state='normal')
        finally:
            self.now_busy = False
        if started:
            self.close_now()

    def close_now(self):
        if self.now_busy:
            return
        if self.now_window is not None:
            self.now_window.destroy()
            self.now_window = None
        self.now_preview = None
        if not self.closed:
            self.select_row()

    def show_bulk_result(self):
        if not self.bulk_report:
            self.status.set('Chưa có lượt bật lịch hàng loạt trong cửa sổ này.')
            return
        modal = tk.Toplevel(self)
        modal.title('Kết quả bật lịch — '+self.manager.target['name'])
        modal.geometry('820x480')
        content = tk.Text(modal,wrap='word')
        content.pack(fill='both',expand=True,padx=12,pady=12)
        content.insert('1.0',self.bulk_report)
        content.configure(state='disabled')

    def start_all(self):
        # Reload the Page scope before taking the explicit "all" snapshot.
        try:
            identifiers = [row['id'] for row in self.store.list(self.manager.account_id, self.manager.page_key)
                           if row['state'] != 'removed']
            self.start_rows(identifiers)
        except (OSError, ValueError) as exc:
            self.status.set(str(exc))

    def start_rows(self, identifiers):
        if self.bulk_busy:
            return
        if not identifiers:
            self.status.set('Chọn các lịch muốn bật trước.')
            return
        self.bulk_busy = True
        try:
            results = self.controller.start_many(self.manager.account_id, self.manager.page_key, identifiers)
            counts = {key: sum(row['outcome'] == key for row in results) for key in ('started','active','skipped')}
            self.bulk_report = '\n'.join(row['label']+': '+row['message'] for row in results)
            self.status.set(f"Đã bật {counts['started']} lịch · Đã chạy {counts['active']} · Bỏ qua {counts['skipped']}. "
                'Xem từng lịch trong bảng hoặc Chi tiết / Tùy chọn.')
            self.refresh()
        except (OSError, ValueError, RuntimeError) as exc:
            self.status.set(str(exc))
        finally:
            self.bulk_busy = False
            self.start_all_button.configure(state='normal' if any(row['state'] != 'removed' for row in self.rows.values()) else 'disabled')

    def pause(self):
        try:
            identifiers = self.tree.selection()
            if not identifiers:
                raise ValueError('Chọn các lịch muốn tạm dừng.')
            paused, errors = 0, []
            for ident in identifiers:
                try:
                    row = self.store.get(ident)
                    if (row['account_id'],row['page_key']) != (self.manager.account_id,self.manager.page_key):
                        raise ValueError('Lịch không thuộc Page này.')
                    self.controller.pause(ident)
                    paused += 1
                except (OSError,ValueError,RuntimeError) as exc:
                    errors.append(str(exc))
            self.status.set('Đã tạm dừng '+str(paused)+' lịch. Nếu Chrome đã nhận thao tác, kiểm tra Page trước khi thử lại. '+ ' · '.join(errors))
            self.refresh()
        except (OSError, ValueError, RuntimeError) as exc:
            self.status.set(str(exc))

    def cancel_selected(self):
        try:
            row = self.selected()
            if row['attempted_at'] or row['state'] in {'published', 'cancelled'}:
                raise ValueError('Chỉ bỏ lịch chưa có thao tác gửi tới Chrome. Kiểm tra bài trên Facebook nếu đã có lượt thao tác.')
            description = ('Bỏ lịch chưa gửi này?\n\nNội dung: ' + row['source']['label'] + '\n'
                'Page: ' + row['snapshot']['target_name'] + '\n'
                'Link Page: ' + row['snapshot']['target_url'] + '\n'
                'Giờ đăng: ' + display_time(row['due_at']) + ' (UTC+7)\n\n'
                'Tèo giữ lịch sử và tệp video. Sau khi bỏ lịch, bạn có thể chọn lại thành phẩm và tạo lịch đúng.')
            if not messagebox.askyesno('Bỏ lịch chưa gửi', description, parent=self.winfo_toplevel()):
                return
            self.controller.cancel(row['id'])
            self.status.set('Đã bỏ lịch chưa gửi; lịch sử và video được giữ. Có thể chọn lại thành phẩm để tạo lịch mới.')
            self.refresh()
        except (OSError, ValueError, RuntimeError) as exc:
            self.status.set(str(exc))

    def change_time(self, *, retry=False):
        try:
            row = self.selected()
            if retry and not self.store.can_retry_preparation(row):
                raise ValueError('Lỗi này không được xếp lại tự động; kiểm tra kết quả bài trên Facebook.')
            if not retry and (row['attempted_at'] or row['state'] not in {'scheduled', 'paused', 'needs_attention'}):
                raise ValueError('Chỉ đổi giờ trước lượt chuẩn bị/gửi; tạm dừng lịch đang chờ trước khi đổi giờ.')
            if self.time_window is not None:
                self.time_window.lift()
                return
            modal = self.time_window = tk.Toplevel(self)
            modal.title(('Xếp lại lịch lỗi — ' if retry else 'Đổi giờ đăng — ') + row['source']['label'])
            modal.transient(self.winfo_toplevel())
            shell = ttk.Frame(modal, padding=16)
            shell.pack(fill='both', expand=True)
            ttk.Label(shell, text='Giữ nguyên Page, video và caption đã duyệt.\nGiờ Việt Nam (UTC+7) — YYYY-MM-DD HH:MM', wraplength=550).pack(anchor='w')
            if retry:
                ttk.Label(shell, text='Lượt trước dừng khi nhận diện Page, chưa nhập nội dung hoặc tải video. '
                    'Lưu giờ mới giữ lịch sử lượt lỗi; chưa tự bật đăng.', wraplength=550).pack(anchor='w', pady=6)
            self.new_time = tk.StringVar(modal, display_time(time.time()+600 if retry else row['due_at']))
            ttk.Entry(shell, textvariable=self.new_time, width=40).pack(fill='x', pady=8)
            self.time_status = tk.StringVar(modal)
            ttk.Label(shell, textvariable=self.time_status, wraplength=550).pack(anchor='w', pady=6)
            ttk.Button(shell, text='Lưu giờ mới (chưa bật)', command=lambda: self.save_time(row['id'], retry=retry)).pack(anchor='w')
            modal.protocol('WM_DELETE_WINDOW', self.close_time)
            modal.grab_set()
        except (OSError, ValueError) as exc:
            self.status.set(str(exc))

    def save_time(self, ident, *, retry=False):
        try:
            row = self.store.get(ident)
            if (row['account_id'], row['page_key']) != (self.manager.account_id, self.manager.page_key):
                raise ValueError('Lịch không thuộc Page này.')
            due_at = future_time(self.new_time.get())
            if retry:
                self.controller.retry_preparation(self.manager.account_id, self.manager.page_key, ident, due_at)
            else:
                self.store.reschedule(ident, due_at)
            self.status.set('Đã đổi giờ; bấm Bật phần đã chọn khi đồng ý. Chưa tự bật lịch.')
            self.close_time()
            self.refresh()
        except (OSError, ValueError) as exc:
            self.time_status.set(str(exc))

    def close_time(self):
        if self.time_window is not None:
            self.time_window.destroy()
            self.time_window = None

    def open_selected(self):
        try:
            row = self.selected()
            post = self.store.activity.post(row['account_id'], row['page_key'], row['post_id'])
            self.manager.open_url(post['external_url'] or post.get('manual_url', '') or row['snapshot']['target_url'])
        except (OSError, ValueError) as exc:
            self.status.set(str(exc))

    def poll(self):
        if self.closed:
            return
        self.refresh()
        self.timer = self.after(1000, self.poll)

    def on_destroy(self, event):
        if event.widget != self:
            return
        self.closed = True
        if self.timer is not None:
            try:
                self.after_cancel(self.timer)
            except tk.TclError:
                pass
            self.timer = None
        self.close_time()
        self.close_now()
        if self.recovery_window is not None:
            self.recovery_window.destroy()
        if self.check_window is not None:
            self.check_window.destroy()
