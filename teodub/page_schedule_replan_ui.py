"""30/10/2026: "Xếp lại theo giờ của Page…" — move many selected video appointments onto the Page's own daily times and arm them.
A preview first; nothing changes until the owner presses the save button. No AI call."""
import time
import tkinter as tk
from tkinter import ttk

from teodub.page_schedule import display_time
from teodub.page_schedule_recovery_ui import ScheduleRecoveryDialog
from teodub.page_schedule_replan import apply_plan, build_plan, page_times, parse_times
from teodub.page_schedule_ui import future_time

KIND_TEXT = {'fresh': 'chưa từng gửi', 'recover': 'dừng trước khi bấm Đăng'}


class ScheduleReplanDialog(tk.Toplevel):
    # The Chrome session of the account must be closed before a stopped preparation is recreated: the same checks and the same
    # button as in "Bỏ lượt cũ và tạo lịch mới…".
    session_busy = ScheduleRecoveryDialog.session_busy
    browser_ready = ScheduleRecoveryDialog.browser_ready
    poll_browser = ScheduleRecoveryDialog.poll_browser
    close_browser = ScheduleRecoveryDialog.close_browser

    def __init__(self, panel, identifiers):
        identifiers = list(dict.fromkeys(identifiers))
        if not identifiers:
            raise ValueError('Chọn một hoặc nhiều lịch video cần xếp lại.')
        super().__init__(panel)
        self.panel, self.controller = panel, panel.controller
        self.account_id, self.page_key = panel.manager.account_id, panel.manager.page_key
        self.identifiers = identifiers
        self.recreate = True  # Used by the borrowed Chrome-session checks.
        self.saving = False
        self.browser_timer = None
        self.plan = None
        self.title('Xếp lại theo giờ của Page')
        self.geometry('960x800')
        self.minsize(840, 700)
        self.transient(panel.winfo_toplevel())
        saved = page_times(self.controller.store.data_dir, self.account_id, self.page_key)
        self.times = tk.StringVar(self, ', '.join(saved))
        self.start = tk.StringVar(self, display_time(time.time()+600))
        self.checked_unpublished = tk.BooleanVar(self, False)
        self.closed_composer = tk.BooleanVar(self, False)
        self.status = tk.StringVar(self)
        self.browser_status = tk.StringVar(self)
        shell = ttk.Frame(self, padding=16)
        shell.pack(fill='both', expand=True)
        ttk.Label(shell, text='Xếp lại '+str(len(identifiers))+' lịch video theo giờ của Page',
                  font=('Segoe UI', 17, 'bold')).pack(anchor='w')
        ttk.Label(shell, wraplength=900, text='Tèo xếp các video đã chọn vào những giờ còn trống trong tương lai, theo thứ tự giờ cũ, '
                  'giữ video và caption đã duyệt, rồi bật lịch. Không gọi AI. Video Tèo đã bấm Đăng hoặc chưa rõ kết quả '
                  '(Đã gửi — chưa xác minh) được bỏ qua, không đăng lại.').pack(anchor='w', pady=(4, 8))
        fields = ttk.Frame(shell)
        fields.pack(fill='x')
        fields.columnconfigure(0, weight=1)
        ttk.Label(fields, text='Giờ đăng mỗi ngày (HH:MM, cách nhau dấu phẩy)').grid(row=0, column=0, sticky='w')
        ttk.Entry(fields, textvariable=self.times).grid(row=1, column=0, sticky='ew', padx=(0, 12))
        ttk.Label(fields, text='Bắt đầu từ (UTC+7) — YYYY-MM-DD HH:MM').grid(row=0, column=1, sticky='w')
        ttk.Entry(fields, textvariable=self.start, width=22).grid(row=1, column=1, sticky='w')
        self.preview_button = ttk.Button(fields, text='Xem lịch mới', command=self.show_plan)
        self.preview_button.grid(row=1, column=2, padx=(12, 0))
        ttk.Label(shell, wraplength=900, text='Các giờ được lấy từ thiết lập AI của Page nếu Page đã có giờ riêng; bạn có thể sửa. '
                  'Số giờ cũng là số video tối đa mỗi ngày. Giờ đã qua hôm nay không được dồn bài; ngày nào Page đã đủ video '
                  'thì sang ngày sau.').pack(anchor='w', pady=(4, 6))
        # The buttons and the notices are packed first, at the bottom, so a long list never pushes them out of the window.
        actions = ttk.Frame(shell)
        actions.pack(side='bottom', fill='x')
        self.status_label = ttk.Label(shell, textvariable=self.status, wraplength=900)
        self.status_label.pack(side='bottom', anchor='w', pady=6)
        self.confirm_frame = ttk.Frame(shell)
        body = ttk.Frame(shell)
        body.pack(fill='both', expand=True)
        self.content = tk.Text(body, wrap='word', height=10, font=('Segoe UI', 10), state='disabled')
        self.content.pack(side='left', fill='both', expand=True)
        scroll = ttk.Scrollbar(body, orient='vertical', command=self.content.yview)
        scroll.pack(side='right', fill='y')
        self.content.configure(yscrollcommand=scroll.set)
        self.check_one = ttk.Checkbutton(self.confirm_frame, variable=self.checked_unpublished,
            text='Tôi đã kiểm tra Facebook: các video "dừng trước khi bấm Đăng" chưa được đăng hoặc hẹn giờ.')
        self.check_one.pack(anchor='w', pady=2)
        self.check_two = ttk.Checkbutton(self.confirm_frame, variable=self.closed_composer,
            text='Tôi đã lưu hoặc đồng ý bỏ phần soạn cũ trên Facebook để tạo lại lịch.')
        self.check_two.pack(anchor='w', pady=2)
        browser = ttk.Frame(self.confirm_frame)
        browser.pack(fill='x', pady=(4, 0))
        self.close_browser_button = ttk.Button(browser, text='Đóng phiên Chrome cũ', command=self.close_browser)
        self.close_browser_button.pack(side='left')
        ttk.Label(browser, textvariable=self.browser_status, wraplength=700).pack(side='left', padx=8)
        self.save_button = ttk.Button(actions, text='Lưu giờ mới và bật lịch', command=self.save, state='disabled')
        self.save_button.pack(side='left')
        ttk.Button(actions, text='Đóng', command=self.destroy).pack(side='right')
        for variable in (self.times, self.start):
            variable.trace_add('write', lambda *_: self.invalidate())
        for variable in (self.checked_unpublished, self.closed_composer):
            variable.trace_add('write', lambda *_: self.refresh_buttons())
        self.bind('<Destroy>', self.on_destroy, add='+')
        self.grab_set()
        self.poll_browser()
        if saved:
            self.show_plan()
        else:
            self.say('Page chưa có giờ riêng trong thiết lập AI. Gõ các giờ đăng mỗi ngày rồi bấm Xem lịch mới.')

    def say(self, text):
        self.content.configure(state='normal')
        self.content.delete('1.0', 'end')
        self.content.insert('1.0', text)
        self.content.configure(state='disabled')

    def invalidate(self):
        if self.plan is not None:
            self.plan = None
            self.status.set('Đã đổi giờ hoặc ngày bắt đầu; bấm Xem lịch mới trước khi lưu.')
        self.refresh_buttons()

    def show_plan(self):
        try:
            times = parse_times(self.times.get())
            plan = build_plan(self.controller, self.account_id, self.page_key, self.identifiers, times, future_time(self.start.get()))
        except (OSError, ValueError, RuntimeError) as exc:
            self.plan = None
            self.status.set(str(exc))
            self.refresh_buttons()
            return
        self.plan = plan
        lines = []
        if plan['moved']:
            lines.append(f"{plan['moved']} video sẽ được đăng từ {display_time(plan['first'])} đến {display_time(plan['last'])} (UTC+7), "
                         f"mỗi ngày tối đa {len(plan['times'])} video, lúc {', '.join(plan['times'])}.")
        else:
            lines.append('Không có video nào xếp lại được trong phần đã chọn.')
        if plan['skipped']:
            lines.append(f"{plan['skipped']} video bị bỏ qua (lý do ở cuối mỗi dòng).")
        lines.append('')
        for entry in plan['entries']:
            if entry['kind'] == 'skip':
                lines.append('✕ '+entry['label']+' — giờ cũ '+display_time(entry['old_due_at'])+' — BỎ QUA: '+entry['reason'])
            else:
                lines.append('✓ '+entry['label']+' — giờ cũ '+display_time(entry['old_due_at'])+' → GIỜ MỚI '+display_time(entry['due_at'])
                             +' ('+KIND_TEXT[entry['kind']]+')')
        self.say('\n'.join(lines))
        if plan['recover']:
            self.confirm_frame.pack(side='bottom', fill='x', before=self.status_label)
        else:
            self.confirm_frame.pack_forget()
        self.status.set('Bấm Lưu giờ mới và bật lịch để áp dụng. Tèo sẽ đăng đúng các giờ trên; giữ Tèo mở và Chrome có phiên đăng nhập.')
        self.refresh_buttons()

    def refresh_buttons(self):
        plan = self.plan
        ready = bool(plan and plan['moved'] and not self.saving)
        if ready and plan['recover']:
            ready = self.checked_unpublished.get() and self.closed_composer.get() and self.browser_ready()
        self.save_button.configure(state='normal' if ready else 'disabled')

    def save(self):
        if self.saving or self.plan is None:
            return
        self.saving = True
        self.refresh_buttons()
        try:
            results = apply_plan(self.controller, self.account_id, self.page_key, self.plan,
                checked_unpublished=self.checked_unpublished.get(), closed_composer=self.closed_composer.get())
            counts = {key: sum(row['outcome'] == key for row in results) for key in ('armed', 'moved', 'failed', 'skipped')}
            report = '\n'.join(row['label']+': '+row['message'] for row in results)
            text = (f"Đã bật lịch {counts['armed']} video"+(f" · đã đổi giờ nhưng chưa bật {counts['moved']}" if counts['moved'] else '')
                    +(f" · lỗi {counts['failed']}" if counts['failed'] else '')+f" · bỏ qua {counts['skipped']}.")
            self.panel.bulk_report = report
            self.panel.status.set(text+' Xem từng lịch trong bảng hoặc Chi tiết / Tùy chọn.')
            self.status.set(text+'\n'+report)
            self.plan = None
            self.panel.refresh()
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
            if getattr(self.panel, 'replan_window', None) is self:
                self.panel.replan_window = None
