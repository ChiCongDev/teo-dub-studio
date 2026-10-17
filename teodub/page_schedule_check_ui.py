"""Explicit, scoped inspection of an old browser publishing attempt."""
from copy import deepcopy
import tkinter as tk
from tkinter import ttk

from teodub.page_schedule import display_time


class ScheduleResultCheckDialog(tk.Toplevel):
    def __init__(self, panel, ident):
        account_id, page_key = panel.manager.account_id, panel.manager.page_key
        preview = panel.controller.reconcile_preview(account_id, page_key, ident)
        super().__init__(panel)
        self.panel, self.controller, self.store = panel, panel.controller, panel.store
        self.account_id, self.page_key, self.ident = account_id, page_key, ident
        self.preview = deepcopy(preview)
        self.post_id = self.preview['post']['id']
        self.request_id = ''
        self.session = None
        self.timer = None
        self.sending = self.finished = self.closed = False
        self.result_url = tk.StringVar(self)
        self.confirmed_attempt = tk.BooleanVar(self, False)
        self.status = tk.StringVar(self)
        self.title('Kiểm tra lại trạng thái — '+preview['target']['name'])
        self.geometry('900x740')
        self.minsize(760, 620)
        self.transient(panel.winfo_toplevel())
        self.build()
        self.bind('<Destroy>', self.on_destroy, add='+')
        self.confirmed_attempt.trace_add('write', lambda *_: self.refresh_button())
        self.refresh_button()
        self.grab_set()

    def build(self):
        shell = ttk.Frame(self, padding=16)
        shell.pack(fill='both', expand=True)
        ttk.Label(shell, text='Đối chiếu đúng lượt đã gửi lên Facebook', font=('Segoe UI', 16, 'bold')).pack(anchor='w')
        ttk.Label(shell, text='Kiểm tra chỉ đọc bài trên Facebook; không đăng lại, bật lịch, xóa video hoặc đóng Chrome. '
            'Chỉ ghi Đã đăng khi đúng bài, đúng Page và đúng lượt cũ được xác minh.',
            wraplength=850).pack(anchor='w', pady=(6, 8))
        self.content = tk.Text(shell, height=16, wrap='word')
        self.content.pack(fill='both', expand=True)
        preview = self.preview
        row, post, target = preview['schedule'], preview['post'], preview['target']
        self.content.insert('1.0', 'Page: '+target['name']+'\nLink Page: '+target['url']+
            '\nPage ID: '+target['page_id']+'\n\nVideo: '+row['source']['label']+
            '\nMã lịch của lượt cũ: '+row['id']+'\nMã bài trong Tèo: '+post['id']+
            '\nGiờ hẹn: '+display_time(row['due_at'])+' (UTC+7)'+
            '\nLượt thao tác: '+row['attempted_at']+'\nBước xử lý: '+row['phase']+
            '\nTệp đã dùng: '+row['source'].get('path', '(Không có tệp)')+
            '\nMã nguồn: '+row['source'].get('key', '')+
            '\nMã kiểm tra video: '+row['source'].get('sha256', '')+
            '\n\nCaption / hashtag đầy đủ:\n'+(row['body'] or '(Chỉ video)')+
            '\n\nKết quả đã lưu:\n'+row['message']+'\n'+post['message']+'\n')
        for event in preview['events']:
            if event['state'] in {'submitting', 'processing', 'unknown', 'published'}:
                self.content.insert('end', '\n'+event['at']+' · '+event['state']+' · '+event['message'])
        self.content.configure(state='disabled')
        ttk.Label(shell, text='Link trực tiếp của bài Facebook hoặc Reel cần đối chiếu').pack(anchor='w', pady=(10, 4))
        self.url_entry = ttk.Entry(shell, textvariable=self.result_url)
        self.url_entry.pack(fill='x')
        ttk.Label(shell, text='Dán link bài/Reel thực tế, không dùng link Page. Có thể để trống khi đúng Reel đang mở '
            'trong Chrome của Tèo; Tèo vẫn phải xác minh bài và tác giả. '
            'Bản nháp mới chưa gửi không thể được xác nhận bằng một bài Facebook của lượt cũ.',
            wraplength=850).pack(anchor='w', pady=(4, 8))
        self.confirm_check = ttk.Checkbutton(shell,
            text='Đây là bài Facebook của đúng lượt đã gửi ở trên', variable=self.confirmed_attempt)
        self.confirm_check.pack(anchor='w')
        ttk.Label(shell, textvariable=self.status, wraplength=850).pack(anchor='w', pady=8)
        actions = ttk.Frame(shell)
        actions.pack(fill='x')
        self.check_button = ttk.Button(actions, text='Kiểm tra và cập nhật nếu đúng', command=self.inspect)
        self.check_button.pack(side='left')
        ttk.Button(actions, text='Đóng', command=self.destroy).pack(side='right')

    def scope_current(self):
        return (not self.panel.closed and not self.closed and
                (self.account_id, self.page_key) == (self.panel.manager.account_id, self.panel.manager.page_key))

    def refresh_button(self):
        if not self.closed:
            self.check_button.configure(state='normal' if self.confirmed_attempt.get() and not self.sending
                and not self.request_id and not self.finished and self.scope_current() else 'disabled')

    def inspect(self):
        if self.closed or self.sending or self.request_id or self.finished:
            return
        self.sending = True
        self.refresh_button()
        try:
            if not self.scope_current():
                raise ValueError('Cửa sổ Page đã thay đổi hoặc đóng. Mở lại để chọn đúng lượt cần kiểm tra.')
            if not self.confirmed_attempt.get():
                raise ValueError('Xác nhận bài Facebook thuộc đúng lượt đã gửi ở trên trước khi kiểm tra.')
            request = self.controller.inspect_result(self.account_id, self.page_key, self.ident,
                result_url=self.result_url.get().strip(), expected_reconcile=deepcopy(self.preview), confirmed_attempt=True)
            if not isinstance(request, str) or not request:
                raise ValueError('Chưa nhận được yêu cầu kiểm tra từ Chrome. Trạng thái cũ được giữ.')
            self.request_id = request
            self.session = self.panel.manager.owner.sessions.get(self.account_id)
            self.url_entry.configure(state='disabled')
            self.confirm_check.configure(state='disabled')
            self.status.set('Đã nhận kiểm tra lượt cũ; đang đọc bài/Reel trong Chrome. Không gửi lại video.')
            self.poll()
        except (OSError, ValueError, RuntimeError) as exc:
            self.status.set(str(exc))
        finally:
            self.sending = False
            self.refresh_button()

    def finish(self, message):
        self.finished = True
        self.status.set(message)
        if self.scope_current():
            self.panel.status.set(message)
            self.panel.refresh()
        self.refresh_button()

    def poll(self):
        if self.timer is not None:
            self.after_cancel(self.timer)
        self.timer = None
        if self.closed or self.finished or not self.request_id:
            return
        if not self.scope_current():
            self.finish('Cửa sổ Page đã thay đổi hoặc đóng. Mở lại đúng Page để xem kết quả; không tự kiểm tra lại.')
            return
        session = self.panel.manager.owner.sessions.get(self.account_id)
        reply = getattr(session, 'post_actions', {}).get(self.request_id) if session else None
        if session is not self.session or not reply:
            self.finish('Không còn phản hồi của phiên Chrome đã kiểm tra. Trạng thái cũ được giữ; không tự gửi lại yêu cầu.')
            return
        if any(reply.get(key) != value for key, value in
               (('account_id', self.account_id), ('page_key', self.page_key), ('post_id', self.post_id),
                ('operation', 'inspect_result'))):
            self.finish('Phản hồi không thuộc đúng tài khoản, Page hoặc lượt bài đã chọn. Chưa xác nhận đã đăng.')
            return
        state = reply.get('state')
        if state in {'queued', 'running', 'verifying'}:
            self.status.set(reply.get('message') or 'Đang đọc bài trên Facebook để đối chiếu lượt cũ.')
            self.timer = self.after(400, self.poll)
            return
        if state == 'error':
            self.finish('Chưa xác minh được bài: '+str(reply.get('message') or 'Chrome đã dừng kiểm tra.')+
                        ' Trạng thái cũ được giữ; không tự đăng lại.')
            return
        if state != 'done':
            self.finish('Chưa có phản hồi kiểm tra hợp lệ. Trạng thái cũ được giữ; không tự đăng lại.')
            return
        result = reply.get('result') or {}
        if not isinstance(result, dict):
            self.finish('Phản hồi kiểm tra chưa hợp lệ. Trạng thái cũ được giữ; không tự đăng lại.')
            return
        if result.get('schedule_id') != self.ident:
            self.finish('Kết quả không thuộc đúng lịch của lượt cũ. Chưa xác nhận đã đăng.')
            return
        if result.get('state') != 'published':
            self.finish('Chưa xác minh: '+str(result.get('message') or 'Bài/Reel chưa khớp lượt đã chọn.')+
                        ' Trạng thái cũ được giữ; không tự đăng lại.')
            return
        try:
            post = self.store.activity.post(self.account_id, self.page_key, self.post_id)
            row = self.store.get(self.ident)
            receipt = post.get('browser_receipt') or {}
            if (post['state'] != 'published' or not post['external_url'] or receipt.get('url') != post['external_url']
                    or row['post_id'] != self.post_id or row['state'] not in {'published', 'removed'}):
                raise ValueError('Chưa có kết quả xác minh được lưu cho đúng lượt cũ.')
            self.finish('Đã xác minh bài trên Facebook cho đúng lượt cũ. '+post['external_url']+
                (' Lịch vẫn được giữ trong lịch sử video đã xóa.' if row['state'] == 'removed' else ''))
        except (OSError, ValueError, RuntimeError) as exc:
            self.finish(str(exc)+' Chưa xác nhận đã đăng; không tự đăng lại.')

    def on_destroy(self, event):
        if event.widget is not self:
            return
        self.closed = True
        if self.timer is not None:
            try:
                self.after_cancel(self.timer)
            except tk.TclError:
                pass
            self.timer = None
        if self.panel.check_window is self:
            self.panel.check_window = None
