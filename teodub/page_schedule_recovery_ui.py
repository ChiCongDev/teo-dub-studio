"""Explicit preview/confirmation for recovering selected video appointments."""
from copy import deepcopy
import time
import tkinter as tk
from tkinter import messagebox, ttk

from teodub.page_schedule import display_time
from teodub.page_schedule_ui import future_time


class ScheduleRecoveryDialog(tk.Toplevel):
    def __init__(self, panel, identifiers, *, recreate=False, new_attempt=False):
        preview_method = panel.controller.new_attempt_preview if new_attempt else panel.controller.recovery_preview
        previews = {ident: preview_method(panel.manager.account_id,
                    panel.manager.page_key, ident) for ident in dict.fromkeys(identifiers)}
        if not previews:
            raise ValueError('Chọn lịch video đã lỗi hoặc tạm dừng sau lượt chuẩn bị.')
        super().__init__(panel)
        self.panel, self.controller = panel, panel.controller
        self.new_attempt = new_attempt
        self.recreate = recreate = recreate or new_attempt
        self.account_id = panel.manager.account_id
        self.page_key = panel.manager.page_key
        self.browser_timer = None
        self.previews = deepcopy(previews)
        self.restored = set()
        self.created_ids = {}
        self.saving = False
        self.title('Tạo lượt đăng mới' if new_attempt else
                   'Bỏ lượt cũ và tạo lịch mới' if recreate else 'Khôi phục lịch đã chọn')
        self.geometry('940x790' if recreate else '940x700')
        self.minsize(820, 720 if recreate else 640)
        self.transient(panel.winfo_toplevel())
        self.start_time = tk.StringVar(self, display_time(time.time()+600))
        self.interval_minutes = tk.IntVar(self, 30)
        self.checked_unpublished = tk.BooleanVar(self, False)
        self.closed_composer = tk.BooleanVar(self, False)
        self.status = tk.StringVar(self)
        shell = ttk.Frame(self, padding=16)
        shell.pack(fill='both', expand=True)
        ttk.Label(shell, text=('Tạo lượt mới cho ' if new_attempt else 'Tạo lại ' if recreate else 'Khôi phục ')+str(len(previews))+' lịch video',
                  font=('Segoe UI', 17, 'bold')).pack(anchor='w')
        ttk.Label(shell, text='Giữ video, caption và lịch sử lượt cũ. Tạo bản nháp mới cho cùng Page ID; chưa tự bật đăng.',
                  wraplength=870).pack(anchor='w', pady=(4, 8))
        if new_attempt:
            ttk.Label(shell, text='Lượt cũ đã xóa khỏi danh sách, nhưng có thể đã gửi tới Facebook. '
                'Chỉ tạo lượt mới sau khi kiểm tra video chưa đăng hoặc hẹn giờ. '
                'Caption của lượt cũ được giữ nguyên; nội dung đang nhập trong Thêm bài đăng không được dùng.',
                wraplength=870).pack(anchor='w', pady=(0, 8))
        instructions = ('Kiểm tra Facebook chưa có bản đã đăng/hẹn giờ. Lưu hoặc bỏ phần soạn cũ, '
                        'rồi bấm Đóng phiên Chrome cũ bên dưới.' if recreate else
                        'Trước khi lưu: kiểm tra Facebook chưa có bản đã đăng/hẹn giờ, đóng hộp soạn cũ, '
                        'rồi Đóng / Lưu phiên tài khoản này trong Tèo.')
        ttk.Label(shell, text=instructions, wraplength=870).pack(anchor='w', pady=(0, 8))
        content_frame = ttk.Frame(shell)
        content_frame.pack(fill='both', expand=True)
        self.content = tk.Text(content_frame, wrap='word', height=15, font=('Segoe UI', 10))
        self.content.pack(side='left', fill='both', expand=True)
        scroll = ttk.Scrollbar(content_frame, orient='vertical', command=self.content.yview)
        scroll.pack(side='right', fill='y')
        self.content.configure(yscrollcommand=scroll.set)
        for preview in previews.values():
            row, post, target = preview['schedule'], preview['post'], preview['target']
            outcome = 'Lỗi ở bước chuẩn bị' if post['state'] == 'failed' else 'Chưa rõ kết quả; cần kiểm tra Facebook'
            if new_attempt:
                outcome = {'draft': 'Bản nháp cũ', 'review': 'Đã chuẩn bị bài; cần kiểm tra Facebook',
                           'failed': 'Lượt cũ có lỗi; cần kiểm tra Facebook',
                           'unknown': 'Chưa rõ kết quả; cần kiểm tra Facebook'}[post['state']]
            self.content.insert('end', row['source']['label']+'\n'
                +'Page đã lưu: '+post['target_name']+' → Page hiện tại: '+target['name']+'\n'
                +'Page ID: '+target['page_id']+' · '+target['url']+'\n'
                +'Giờ cũ: '+display_time(row['due_at'])+' · Kết quả cũ: '+outcome+'\n'
                +post['message']+'\nTệp: '+row['source']['path']+'\n'
                +'Mã kiểm tra: '+row['source']['sha256']+'\n'
                +'Caption / hashtag sẽ giữ nguyên:\n'+(row['body'] or '(Chỉ video)')+'\n\n')
            if new_attempt:
                self.content.insert('end', 'Bằng chứng lượt cũ:\nTrạng thái bài: '+post['state']+
                    '\nBước xử lý: '+(row['phase'] or '(Chưa có)')+
                    '\nLượt thao tác: '+(row['attempted_at'] or '(Chưa có)')+'\n')
                for event in preview['events']:
                    self.content.insert('end', event['at']+' · '+event['state']+' · '+event['message']+'\n')
                self.content.insert('end', '\n')
        self.content.configure(state='disabled')
        times = ttk.Frame(shell)
        times.pack(fill='x', pady=10)
        ttk.Label(times, text='Bài đầu (UTC+7) — YYYY-MM-DD HH:MM').grid(row=0, column=0, sticky='w')
        ttk.Entry(times, textvariable=self.start_time, width=23).grid(row=1, column=0, sticky='w')
        ttk.Label(times, text='Cách nhau (phút)').grid(row=0, column=1, sticky='w', padx=16)
        ttk.Spinbox(times, from_=1, to=1440, textvariable=self.interval_minutes, width=8).grid(row=1, column=1, sticky='w', padx=16)
        ttk.Checkbutton(shell, text='Tôi đã kiểm tra Facebook: các video đã chọn chưa được đăng hoặc hẹn giờ.',
                        variable=self.checked_unpublished).pack(anchor='w', pady=3)
        closed_text = ('Tôi đã lưu hoặc đồng ý bỏ phần soạn cũ trên Facebook để tạo lại lịch.' if recreate else
                       'Tôi đã đóng hộp soạn bài cũ và Đóng / Lưu phiên tài khoản này trong Tèo.')
        ttk.Checkbutton(shell, text=closed_text,
                        variable=self.closed_composer).pack(anchor='w', pady=3)
        if recreate:
            browser_actions = ttk.Frame(shell)
            browser_actions.pack(fill='x', pady=(6, 0))
            self.close_browser_button = ttk.Button(browser_actions, text='Đóng phiên Chrome cũ', command=self.close_browser)
            self.close_browser_button.pack(side='left')
            self.browser_status = tk.StringVar(self)
            ttk.Label(shell, textvariable=self.browser_status, wraplength=870).pack(anchor='w', pady=(3, 0))
        ttk.Label(shell, textvariable=self.status, wraplength=870).pack(anchor='w', pady=6)
        actions = ttk.Frame(shell)
        actions.pack(fill='x')
        self.save_button = ttk.Button(actions, text='Tạo lượt mới (chưa bật)' if new_attempt else
            'Tạo lịch mới (chưa bật)' if recreate else 'Khôi phục lịch (chưa bật)', command=self.save)
        self.save_button.pack(side='left')
        ttk.Button(actions, text='Đóng', command=self.destroy).pack(side='right')
        for variable in (self.checked_unpublished, self.closed_composer):
            variable.trace_add('write', lambda *_: self.refresh_buttons())
        self.refresh_buttons()
        self.bind('<Destroy>', self.on_destroy, add='+')
        self.grab_set()
        if recreate:
            self.poll_browser()

    def browser_ready(self):
        if not self.recreate:
            return True
        if self.session_busy(self.panel.manager.owner.sessions.get(self.account_id)):
            return False
        try:
            self.controller._recovery_idle(self.account_id)
            return True
        except (OSError, ValueError, RuntimeError):
            return False

    def session_busy(self, session):
        return bool(session and (getattr(session, 'loading_pages', False) or any(
            item.get('state') in {'queued', 'running', 'opening', 'loading_pages', 'verifying'}
            for key in ('post_actions', 'navigation', 'page_load_requests')
            for item in getattr(session, key, {}).values())))

    def poll_browser(self):
        if self.browser_timer is not None:
            self.after_cancel(self.browser_timer)
        self.browser_timer = None
        session = self.panel.manager.owner.sessions.get(self.account_id)
        finished = getattr(session, 'finished', None)
        closing = bool(finished and finished.is_set() and getattr(session, 'pending', False))
        ready = self.browser_ready()
        busy = self.session_busy(session)
        if ready:
            notice = 'Phiên Chrome cũ đã đóng. Có thể tạo lịch mới sau khi xác nhận và chọn giờ.'
        elif closing:
            notice = 'Đang đóng Chrome và lưu hồ sơ tài khoản. Đợi hoàn tất rồi tạo lịch mới.'
        elif busy:
            notice = 'Tài khoản đang xử lý bài hoặc lấy danh sách Page. Đợi xong trước khi đóng phiên.'
        else:
            notice = 'Phiên Chrome cũ còn mở hoặc Tèo chưa sẵn sàng. Bấm Đóng phiên Chrome cũ trước khi tạo lịch mới.'
        self.browser_status.set(notice)
        self.close_browser_button.configure(state='normal' if not self.saving and not ready and not closing
            and not busy and session and getattr(session, 'pending', False) else 'disabled')
        self.refresh_buttons()
        self.browser_timer = self.after(400, self.poll_browser)

    def close_browser(self):
        if self.saving or self.browser_ready():
            return
        owner = self.panel.manager.owner
        session = owner.sessions.get(self.account_id)
        if not session or not getattr(session, 'pending', False):
            self.status.set('Phiên tài khoản chưa đóng hoàn tất. Kiểm tra trạng thái Chrome trong Tèo.')
            return
        if self.session_busy(session):
            self.status.set('Tài khoản đang xử lý bài hoặc lấy danh sách Page. Đợi xong trước khi đóng phiên.')
            return
        if session.finished.is_set():
            return
        name = session.account_name
        if not messagebox.askyesno('Đóng phiên Chrome cũ',
                'Đóng Chrome của tài khoản '+name+' để tạo lại lịch?\n\n'
                'Phần soạn trên Facebook chưa lưu có thể bị mất. Hãy lưu bản nháp cần giữ trước khi đồng ý.\n'
                'Video, caption và lịch sử trong Tèo được giữ nguyên. Thao tác này chưa tạo lịch hoặc đăng bài.',
                parent=self):
            return
        # Confirmation can be open while background requests change. Recheck
        # the bound account/session before closing, never the account picker.
        if (owner.sessions.get(self.account_id) is not session or self.session_busy(session)
                or not session.pending or session.finished.is_set()
                or any(getattr(owner.studio, flag, False) for flag in ('closing', 'closed', 'restart_draining'))):
            self.status.set('Phiên tài khoản vừa thay đổi hoặc có lượt xử lý mới; chưa đóng Chrome.')
            return
        try:
            if owner.finish_browser(account_id=self.account_id):
                self.status.set('Đã yêu cầu đóng phiên Chrome cũ; đợi lưu hồ sơ xong rồi bấm Tạo lịch mới.')
            else:
                self.status.set('Chưa đóng được phiên Chrome cũ; kiểm tra trạng thái tài khoản trong Tèo.')
        except (OSError, ValueError, RuntimeError, tk.TclError) as exc:
            self.status.set('Chưa đóng được phiên Chrome cũ: '+str(exc))
        self.refresh_buttons()

    def refresh_buttons(self):
        self.save_button.configure(state='normal' if not self.saving
            and len(self.restored) < len(self.previews) and self.checked_unpublished.get()
            and self.closed_composer.get() and self.browser_ready() else 'disabled')

    def save(self):
        if self.saving or len(self.restored) == len(self.previews):
            return
        self.saving = True
        self.refresh_buttons()
        try:
            if not self.checked_unpublished.get() or not self.closed_composer.get():
                raise ValueError('Cần xác nhận đã kiểm tra Facebook và đóng hộp soạn/phiên Chrome cũ.')
            if self.recreate and not self.browser_ready():
                raise ValueError('Đợi phiên Chrome cũ đóng hoàn tất trước khi tạo lịch mới.')
            if self.new_attempt and (self.account_id, self.page_key) != (self.panel.manager.account_id, self.panel.manager.page_key):
                raise ValueError('Tài khoản/Page đang mở đã thay đổi. Đóng và mở lại để duyệt đúng lượt đăng mới.')
            first = future_time(self.start_time.get())
            interval = self.interval_minutes.get()
            if not 1 <= interval <= 1440:
                raise ValueError('Khoảng cách bài đăng từ 1 đến 1440 phút.')
            last = first+(len(self.previews)-1)*interval*60
            if last >= time.time()+366*86400:
                raise ValueError('Tất cả giờ đăng mới phải nằm trong một năm tới.')
            errors = []
            for index, (ident, preview) in enumerate(self.previews.items()):
                if ident in self.restored:
                    continue
                try:
                    save_method = self.controller.create_new_attempt if self.new_attempt else self.controller.recover_schedule
                    saved = save_method(self.account_id,
                        self.page_key, ident, first+index*interval*60,
                        expected_preview=deepcopy(preview), checked_unpublished=True, closed_composer=True)
                    self.restored.add(ident)
                    if self.new_attempt:
                        self.created_ids[ident] = saved['id']
                except (OSError, ValueError, RuntimeError) as exc:
                    errors.append(preview['schedule']['source']['label']+': '+str(exc))
            result = ('Đã tạo lượt mới cho ' if self.new_attempt else 'Đã tạo lại ' if self.recreate else 'Đã khôi phục ')+str(len(self.restored))+'/'+str(len(self.previews))+' lịch. Chưa bật tự động đăng.'
            if errors:
                result += '\n'+'\n'.join(errors)
            else:
                result += ' Đóng cửa sổ rồi bấm Bật phần đã chọn khi sẵn sàng.'
            self.status.set(result)
            self.panel.status.set(result)
            self.panel.refresh()
            if self.new_attempt:
                selected = [ident for ident in self.created_ids.values() if ident in self.panel.rows]
                self.panel.tree.selection_set(selected)
                if selected:
                    self.panel.tree.see(selected[-1])
                self.panel.select_row()
        except (OSError, ValueError, RuntimeError, tk.TclError) as exc:
            self.status.set(str(exc))
        finally:
            self.saving = False
            self.refresh_buttons()

    def on_destroy(self, event):
        if event.widget is self:
            if self.browser_timer is not None:
                try:
                    self.after_cancel(self.browser_timer)
                except tk.TclError:
                    pass
                self.browser_timer = None
            if self.panel.recovery_window is self:
                self.panel.recovery_window = None
