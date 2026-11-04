"""03/11/2026: "Xếp theo lượt thích…" on a Page's schedule tab — the most liked videos take the Page's earliest posting times.
A preview first (each time: which video is there now, which one will be); nothing changes until the owner presses the save
button. The times themselves stay the same. No AI call."""
import tkinter as tk
from tkinter import ttk

from teodub.page_schedule import display_time
from teodub.page_schedule_likes import SAFE_SECONDS, apply_plan, build_plan

SKIPPED_TEXT = {'text': 'bài chữ', 'busy': 'đang xử lý hoặc đã có lượt gửi', 'soon': f'sắp đến giờ (trong {SAFE_SECONDS//60} phút)'}


def likes_text(value):
    return '—' if value is None else f'{value:,}'.replace(',', '.')


class ScheduleLikesDialog(tk.Toplevel):
    def __init__(self, panel):
        super().__init__(panel)
        self.panel, self.controller = panel, panel.controller
        self.account_id, self.page_key = panel.manager.account_id, panel.manager.page_key
        self.plan = None
        self.saving = False
        self.title('Xếp lịch đăng theo lượt thích')
        self.geometry('980x720')
        self.minsize(860, 600)
        self.transient(panel.winfo_toplevel())
        self.summary = tk.StringVar(self)
        self.status = tk.StringVar(self)
        shell = ttk.Frame(self, padding=16)
        shell.pack(fill='both', expand=True)
        ttk.Label(shell, text='Xếp lịch đăng theo lượt thích', font=('Segoe UI', 17, 'bold')).pack(anchor='w')
        ttk.Label(shell, wraplength=920, text='Tèo giữ NGUYÊN các giờ đăng của Page, chỉ đổi video nào đăng vào giờ nào: video nhiều lượt thích '
                  'nhận giờ sớm nhất. Mỗi lịch giữ video và caption của nó. Chỉ xếp các lịch đang chờ giờ đăng mà Tèo chưa gửi lần nào; '
                  'bài chữ, lịch đã gửi, đang xử lý, tạm dừng hoặc sắp đến giờ giữ nguyên. Video chưa có số lượt thích xếp sau cùng '
                  '(muốn có số: mở tab chiến dịch của link, bấm "Xếp theo lượt thích…"). Không gọi AI.').pack(anchor='w', pady=(4, 8))
        # The buttons and the notice are packed first, at the bottom, so a long list never pushes them out of the window.
        actions = ttk.Frame(shell)
        actions.pack(side='bottom', fill='x')
        ttk.Label(shell, textvariable=self.status, wraplength=920).pack(side='bottom', anchor='w', pady=6)
        self.save_button = ttk.Button(actions, text='Lưu thứ tự mới', command=self.save, state='disabled')
        self.save_button.pack(side='left')
        ttk.Button(actions, text='Xem lại', command=self.show_plan).pack(side='left', padx=8)
        ttk.Button(actions, text='Đóng', command=self.destroy).pack(side='right')
        ttk.Label(shell, textvariable=self.summary, wraplength=920, font=('Segoe UI', 11, 'bold')).pack(anchor='w', pady=(0, 6))
        body = ttk.Frame(shell)
        body.pack(fill='both', expand=True)
        self.tree = ttk.Treeview(body, columns=('time', 'now', 'new'), show='headings', height=14)
        for key, label, width in (('time', 'Giờ đăng (UTC+7)', 150), ('now', 'Đang xếp ở giờ này', 360), ('new', 'Sẽ đăng ở giờ này', 400)):
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, minwidth=100)
        self.tree.tag_configure('moved', background='#fff4d6')
        self.tree.pack(side='left', fill='both', expand=True)
        scroll = ttk.Scrollbar(body, orient='vertical', command=self.tree.yview)
        scroll.pack(side='right', fill='y')
        self.tree.configure(yscrollcommand=scroll.set)
        self.show_plan()

    def show_plan(self):
        self.tree.delete(*self.tree.get_children())
        try:
            plan = build_plan(self.controller, self.account_id, self.page_key)
        except (OSError, ValueError, RuntimeError) as exc:
            self.plan = None
            self.summary.set('')
            self.status.set(str(exc))
            self.save_button.configure(state='disabled')
            return
        self.plan = plan
        for entry in plan['entries']:
            was = entry['was']
            moved = entry['due_at'] != entry['old_due_at']
            self.tree.insert('', 'end', tags=('moved',) if moved else (), values=(
                display_time(entry['due_at']),
                f"{was['label']} · {likes_text(was['likes'])} thích" if was else '',
                f"{entry['label']} · {likes_text(entry['likes'])} thích"+('  ← đổi' if moved else '')))
        total, unknown = len(plan['entries']), len(plan['entries'])-plan['known']
        skipped = ', '.join(f'{count} {SKIPPED_TEXT[key]}' for key, count in plan['skipped'].items())
        if not total:
            self.summary.set('Page này chưa có lịch video nào đủ điều kiện để xếp lại.')
        else:
            self.summary.set(f"{total} lịch video chưa gửi · {plan['changed']} lịch sẽ đổi giờ · {unknown} video chưa có số lượt thích (xếp sau cùng)."
                             +('' if plan['changed'] else ' Thứ tự hiện tại đã đúng.'))
        self.status.set(('Giữ nguyên: '+skipped+'. ' if skipped else '')+
                        ('Bấm Lưu thứ tự mới để áp dụng. Giờ đăng không đổi; Tèo vẫn đăng đúng các giờ đó.' if plan['changed'] else ''))
        self.save_button.configure(state='normal' if plan['changed'] and not self.saving else 'disabled')

    def save(self):
        if self.saving or self.plan is None:
            return
        self.saving = True
        self.save_button.configure(state='disabled')
        try:
            moved = apply_plan(self.controller, self.account_id, self.page_key, self.plan)
            self.panel.status.set(f'Đã xếp lại theo lượt thích: {moved} lịch đổi giờ đăng; video nhiều thích đăng sớm hơn. Các giờ đăng giữ nguyên.')
            self.panel.refresh()
            self.destroy()
        except (OSError, ValueError, RuntimeError, tk.TclError) as exc:
            self.saving = False
            self.status.set(str(exc))
            self.save_button.configure(state='normal')
