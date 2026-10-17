"""Create a text or completed campaign-video appointment inside a Page's schedule."""
from copy import deepcopy
from pathlib import Path
import re
import time
import tkinter as tk
from tkinter import ttk

from teodub.campaign import read_json
from teodub.page_schedule import campaign_video_source, display_time
from teodub.page_schedule_ui import future_time


def campaign_choices(data_dir):
    root = Path(data_dir) / 'campaigns'
    result = []
    if not root.is_dir():
        return result
    for directory in root.iterdir():
        if not directory.is_dir() or directory.is_symlink() or not re.fullmatch(r'[0-9a-f]{32}', directory.name):
            continue
        try:
            manifest = read_json(directory / 'campaign.json')
            if manifest.get('id') != directory.name or manifest.get('archived_at'):
                continue
            items = [dict(number=item['number'], title=str(item.get('title', ''))[:160])
                     for item in manifest.get('items', []) if isinstance(item, dict) and item.get('status') == 'completed']
            if items:
                result.append(dict(directory=str(directory), name=manifest['name'], items=items))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return sorted(result, key=lambda row: (row['name'].casefold(), row['directory']))


class AddScheduledPostDialog(tk.Toplevel):
    def __init__(self, panel):
        self.panel = panel
        self.controller, self.store = panel.controller, panel.store
        self.account_id, self.page_key = panel.manager.account_id, panel.manager.page_key
        page = self.store.activity.page(self.account_id, self.page_key)
        self.target = {key: page.get(key, '') for key in ('name', 'url', 'page_id')}
        account = next(row for row in self.store.activity.accounts.load()['accounts'] if row['id'] == self.account_id)
        super().__init__(panel)
        self.title('Thêm bài đăng — ' + self.target['name'])
        self.geometry('820x700')
        self.minsize(690, 600)
        self.transient(panel.winfo_toplevel())
        self.saved_id = self.draft_id = ''
        self.saving = self.done = False
        self.source = None
        self.reserved_removed_id = ''
        self.new_attempt_window = None
        self.campaigns = None
        self.kind = tk.StringVar(self, 'Bài chữ')
        self.due_time = tk.StringVar(self, display_time(time.time() + 3600))
        self.status = tk.StringVar(self)
        self.video_status = tk.StringVar(self, 'Chọn chiến dịch rồi chọn video đã hoàn tất.')
        self.build(account['name'])
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.grab_set()

    def build(self, account_name):
        shell = ttk.Frame(self, padding=16)
        shell.pack(fill='both', expand=True)
        ttk.Label(shell, text='Thêm bài vào lịch đăng', font=('Segoe UI', 17, 'bold')).pack(anchor='w')
        ttk.Label(shell, text='Page: ' + self.target['name'] + ' · Tài khoản: ' + account_name,
                  wraplength=760).pack(anchor='w', pady=(6, 2))
        ttk.Label(shell, text=self.target['url'], wraplength=760).pack(anchor='w', pady=(0, 10))
        self.kind_picker = ttk.Combobox(shell, textvariable=self.kind, state='readonly',
                                       values=('Bài chữ', 'Video từ chiến dịch'))
        self.kind_picker.pack(fill='x')
        self.kind_picker.bind('<<ComboboxSelected>>', self.change_kind)
        self.video_frame = ttk.LabelFrame(shell, text='Video thành phẩm', padding=8)
        self.campaign_picker = ttk.Combobox(self.video_frame, state='readonly')
        self.campaign_picker.pack(fill='x', pady=(0, 5))
        self.campaign_picker.bind('<<ComboboxSelected>>', self.change_campaign)
        self.video_picker = ttk.Combobox(self.video_frame, state='readonly')
        self.video_picker.pack(fill='x')
        self.video_picker.bind('<<ComboboxSelected>>', self.change_video)
        ttk.Label(self.video_frame, textvariable=self.video_status, wraplength=730).pack(anchor='w', pady=(5, 0))
        self.new_attempt_button = ttk.Button(self.video_frame, text='Tạo lượt đăng mới…',
                                              command=self.open_new_attempt, state='disabled')
        self.new_attempt_button.pack(anchor='w', pady=(5, 0))
        self.body_label = ttk.Label(shell, text='Nội dung bài / caption và hashtag (đăng nguyên nội dung bạn nhập)')
        self.body_label.pack(anchor='w', pady=(12, 4))
        self.body = tk.Text(shell, height=9, wrap='word', undo=True)
        self.body.pack(fill='both', expand=True)
        ttk.Label(shell, text='Giờ đăng tại Việt Nam (UTC+7) — YYYY-MM-DD HH:MM').pack(anchor='w', pady=(12, 4))
        self.time_entry = ttk.Entry(shell, textvariable=self.due_time)
        self.time_entry.pack(fill='x')
        ttk.Label(shell, text='Lưu lịch: thêm vào danh sách, bật sau. Lưu và bật lịch: Tèo tự đăng vào giờ đã chọn. '
                  'Giữ Tèo mở và máy không ngủ đến giờ đăng.', wraplength=760).pack(anchor='w', pady=(10, 4))
        ttk.Label(shell, textvariable=self.status, wraplength=760).pack(anchor='w', pady=(2, 8))
        actions = ttk.Frame(shell)
        actions.pack(fill='x')
        self.save_button = ttk.Button(actions, text='Lưu lịch', command=lambda: self.save(False))
        self.save_button.pack(side='left')
        self.enable_button = ttk.Button(actions, text='Lưu và bật lịch', command=lambda: self.save(True))
        self.enable_button.pack(side='left', padx=6)
        ttk.Button(actions, text='Đóng', command=self.close).pack(side='right')

    def change_kind(self, event=None):
        if self.saved_id or self.saving:
            return
        if self.kind.get() == 'Video từ chiến dịch':
            self.video_frame.pack(fill='x', before=self.body_label, pady=(8, 0))
            if self.campaigns is None:
                try:
                    self.campaigns = campaign_choices(self.store.data_dir)
                    self.campaign_picker.configure(values=[row['name'] + ' · ' + Path(row['directory']).name[:8]
                                                          for row in self.campaigns])
                    if self.campaigns:
                        self.campaign_picker.current(0)
                        self.change_campaign()
                    else:
                        self.video_status.set('Chưa có video hoàn tất. Hoàn thành video trong chiến dịch rồi mở lại mục này.')
                except (OSError, ValueError) as exc:
                    self.status.set(str(exc))
        else:
            self.video_frame.pack_forget()
            self.reserved_removed_id = ''
            self.new_attempt_button.configure(state='disabled')

    def change_campaign(self, event=None):
        if self.saved_id or self.saving:
            return
        self.source = None
        self.reserved_removed_id = ''
        self.new_attempt_button.configure(state='disabled')
        self.video_picker.set('')
        index = self.campaign_picker.current()
        if not self.campaigns or not 0 <= index < len(self.campaigns):
            return
        self.items = self.campaigns[index]['items']
        used = {row['source_key']: row for row in self.store.list(self.account_id, self.page_key)}
        campaign_id = Path(self.campaigns[index]['directory']).name
        values = []
        for item in self.items:
            existing = used.get(f"{campaign_id}:{item['number']}")
            notice = (' · Lượt cũ đã xóa — cần xác nhận đăng lại' if existing and existing['state'] == 'removed' else
                      ' · Đã có lịch trên Page này' if existing else '')
            values.append(f"Video {item['number']} · {item['title']}"+notice)
        self.video_picker.configure(values=values)
        self.video_status.set('Chọn một video thành phẩm; video đã có lịch không được thêm trùng.')

    def change_video(self, event=None):
        if self.saved_id or self.saving:
            return
        self.source = None
        self.reserved_removed_id = ''
        self.new_attempt_button.configure(state='disabled')
        try:
            ci, vi = self.campaign_picker.current(), self.video_picker.current()
            if ci < 0 or vi < 0:
                return
            source = campaign_video_source(self.store.data_dir, self.campaigns[ci]['directory'], self.items[vi]['number'])
            existing = next((row for row in self.store.list(self.account_id, self.page_key)
                             if row['source_key'] == source['key']), None)
            if existing and existing['state'] == 'removed':
                self.controller.new_attempt_preview(self.account_id, self.page_key, existing['id'])
                self.reserved_removed_id = existing['id']
                self.new_attempt_button.configure(state='normal')
                self.video_status.set('Lượt cũ đã xóa — cần xác nhận đăng lại. Bấm Tạo lượt đăng mới… '
                    'để kiểm tra lượt cũ và chọn giờ; caption của lượt cũ được giữ nguyên.')
                return
            if existing:
                raise ValueError('Video này đã có lịch trên Page. Chọn video khác hoặc dùng lịch đã lưu.')
            self.source = source
            self.video_status.set(source['label'] + ' · ' + Path(source['path']).name)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.video_status.set(str(exc))

    def open_new_attempt(self):
        if self.saved_id or self.saving or self.done or not self.reserved_removed_id:
            return
        if self.new_attempt_window is not None and self.new_attempt_window.winfo_exists():
            self.new_attempt_window.lift()
            return
        if self.panel.recovery_window is not None and self.panel.recovery_window.winfo_exists():
            self.status.set('Đóng cửa sổ khôi phục/lượt đăng đang mở trước khi tạo lượt mới.')
            self.panel.recovery_window.lift()
            return
        try:
            if (self.account_id, self.page_key) != (self.panel.manager.account_id, self.panel.manager.page_key):
                raise ValueError('Tài khoản/Page đã thay đổi. Đóng và mở lại Thêm bài đăng để chọn đúng Page.')
            from teodub.page_schedule_recovery_ui import ScheduleRecoveryDialog
            child = ScheduleRecoveryDialog(self.panel, [self.reserved_removed_id], recreate=True, new_attempt=True)
            self.new_attempt_window = self.panel.recovery_window = child
            child.transient(self)
            child.bind('<Destroy>', self.on_new_attempt_closed, add='+')
        except (OSError, ValueError, RuntimeError) as exc:
            self.status.set(str(exc))

    def on_new_attempt_closed(self, event):
        child = self.new_attempt_window
        if event.widget is not child:
            return
        self.new_attempt_window = None
        if not self.winfo_exists():
            return
        if child.created_ids:
            self.status.set(child.status.get())
        if not self.saved_id and not self.saving and self.kind.get() == 'Video từ chiến dịch':
            index = self.video_picker.current()
            self.change_campaign()
            if 0 <= index < len(getattr(self, 'items', [])):
                self.video_picker.current(index)
                self.change_video()
        self.grab_set()

    def freeze(self):
        for widget in (self.kind_picker, self.campaign_picker, self.video_picker, self.body, self.time_entry):
            widget.configure(state='disabled')

    def save(self, enable):
        if self.saving or self.done:
            return
        self.saving = True
        self.save_button.configure(state='disabled')
        self.enable_button.configure(state='disabled')
        try:
            if not self.saved_id:
                due = future_time(self.due_time.get())
                page = self.store.activity.page(self.account_id, self.page_key)
                if self.target != {key: page.get(key, '') for key in self.target}:
                    raise ValueError('Page đã thay đổi. Đóng và mở lại Thêm bài đăng để chọn đúng Page.')
                if not re.fullmatch(r'[0-9]+', self.target['page_id']):
                    raise ValueError('Page chưa có ID bằng số. Load Page hoặc Sửa Page trước khi lưu lịch.')
                body = self.body.get('1.0', 'end-1c').strip()
                if len(body) > 60000:
                    raise ValueError('Nội dung tối đa 60.000 ký tự.')
                if self.kind.get() == 'Video từ chiến dịch':
                    if self.source is None:
                        if self.reserved_removed_id:
                            raise ValueError('Lượt cũ đã xóa nhưng cần xác nhận đăng lại. Bấm Tạo lượt đăng mới…; '
                                             'lưu lịch thông thường không gửi lại lượt này.')
                        raise ValueError('Chọn video thành phẩm chưa có lịch trên Page này.')
                    row = self.store.create(self.account_id, self.page_key, deepcopy(self.source), body, '', due,
                                            expected_target=self.target, persist_template=False)
                else:
                    if not body:
                        raise ValueError('Nhập nội dung bài viết trước khi lưu lịch.')
                    post = self.store.activity.save_draft(self.account_id, self.page_key, body, post_id=self.draft_id or None,
                                                         delivery_mode='browser', browser_finish_mode='tool')
                    self.draft_id = post['id']
                    from teodub.page_command_bridge import public_post
                    row = self.store.create_post(self.account_id, self.page_key, post['id'], due,
                        expected_target=self.target, expected_post_key=public_post(post)['post_key'])
                self.saved_id = row['id']
                self.freeze()
            if enable:
                self.controller.start(self.saved_id)
            row = self.store.get(self.saved_id)
            self.done = True
            self.panel.status.set(('Đã thêm và bật lịch' if enable else 'Đã thêm bài vào lịch, chưa bật tự đăng') +
                                  ': ' + display_time(row['due_at']) + ' (UTC+7).')
        except (OSError, ValueError, RuntimeError) as exc:
            self.status.set(('Lịch đã lưu nhưng chưa bật. ' if self.saved_id else 'Chưa lưu lịch. ') + str(exc))
            self.enable_button.configure(text='Bật lịch đã lưu' if self.saved_id else 'Lưu và bật lịch')
        finally:
            self.saving = False
            self.save_button.configure(state='normal')
            self.enable_button.configure(state='normal')
            self.panel.refresh()
            if self.saved_id in self.panel.rows:
                self.panel.tree.selection_set(self.saved_id)
                self.panel.tree.see(self.saved_id)
                self.panel.select_row()
        if self.done:
            self.destroy()

    def close(self):
        if not self.saving:
            self.destroy()

    def destroy(self):
        if self.new_attempt_window is not None:
            self.new_attempt_window.destroy()
            self.new_attempt_window = None
        super().destroy()
