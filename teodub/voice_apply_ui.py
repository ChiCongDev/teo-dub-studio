"""Owner 05/11/2026: choose where a saved voice is used. Nothing is ticked by default, so nothing changes by accident."""
from pathlib import Path
import tkinter as tk
from tkinter import ttk

from teodub.campaign import DONE, is_running, read_json
from teodub.voice_learning import checked_profile, profile_label


def campaigns_for_voice(data_dir):
    """Saved, not archived campaigns that still have videos to make: [(directory, label)], newest first."""
    rows = []
    for path in Path(data_dir).glob('campaigns/*/campaign.json'):
        try:
            manifest = read_json(path)
            if manifest.get('archived_at') or manifest.get('id') != path.parent.name:
                continue
            left = sum(1 for item in manifest['items'] if item['status'] not in DONE and item['status'] != 'excluded')
            if not left:
                continue
            state = ' · đang chạy' if is_running(path.parent) else ''
            rows.append((path.stat().st_mtime, path.parent,
                         f'{manifest["name"]} · {path.parent.name[:8]} · còn {left} video chưa làm{state}'))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return [(directory, label) for _, directory, label in sorted(rows, key=lambda row: row[0], reverse=True)]


class ApplyVoiceDialog(tk.Toplevel):
    def __init__(self, tab, profile_id):
        super().__init__(tab)
        self.tab, self.profile_id = tab, profile_id
        profile = checked_profile(tab.data_dir, profile_id)
        self.title('Áp dụng giọng')
        self.geometry('860x600')
        self.transient(tab.winfo_toplevel())
        self.single = tk.BooleanVar(self, False)
        self.status = tk.StringVar(self, 'Chưa tích nơi nào thì không có gì thay đổi.')
        frame = ttk.Frame(self, padding=14)
        frame.pack(fill='both', expand=True)
        # The action row is packed first so a short window can never clip its buttons.
        actions = ttk.Frame(frame)
        actions.pack(side='bottom', fill='x', pady=(10, 0))
        self.apply_button = ttk.Button(actions, text='Áp dụng giọng', command=self.apply)
        self.apply_button.pack(side='left')
        ttk.Button(actions, text='Đóng', command=self.destroy).pack(side='right')
        ttk.Label(frame, textvariable=self.status, wraplength=800).pack(side='bottom', anchor='w', pady=(8, 0))
        ttk.Label(frame, text='Giọng: '+profile_label(profile), font=('Segoe UI', 11, 'bold')).pack(anchor='w')
        ttk.Label(frame, wraplength=800, text='Tích nơi muốn dùng giọng này. Chiến dịch chỉ đổi giọng cho các video chưa làm '
                  '(từ video tiếp theo); video đã xong và mọi cấu hình khác của chiến dịch được giữ nguyên.').pack(anchor='w', pady=(4, 8))
        ttk.Checkbutton(frame, variable=self.single,
                        text='Video đơn và các chiến dịch tạo mới sau này').pack(anchor='w')
        ttk.Label(frame, text='Chiến dịch đã lưu:').pack(anchor='w', pady=(10, 2))
        box = ttk.Frame(frame, relief='sunken', borderwidth=1)
        box.pack(fill='both', expand=True)
        canvas = tk.Canvas(box, highlightthickness=0, background='white')
        scroll = ttk.Scrollbar(box, orient='vertical', command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        canvas.pack(side='left', fill='both', expand=True)
        inner = tk.Frame(canvas, background='white')
        canvas.create_window((0, 0), window=inner, anchor='nw')
        inner.bind('<Configure>', lambda _e: canvas.configure(scrollregion=canvas.bbox('all')))
        self.choices = []
        for directory, label in campaigns_for_voice(tab.data_dir):
            variable = tk.BooleanVar(self, False)
            tk.Checkbutton(inner, text=label, variable=variable, background='white', anchor='w',
                           activebackground='white').pack(fill='x', anchor='w')
            self.choices.append((directory, variable))
        if not self.choices:
            tk.Label(inner, text='Chưa có chiến dịch nào còn video chưa làm.', background='white').pack(anchor='w', padx=6, pady=6)

    def apply(self):
        directories = [directory for directory, variable in self.choices if variable.get()]
        if not self.single.get() and not directories:
            self.status.set('Chưa tích nơi nào để áp dụng.')
            return
        try:
            message = self.tab.apply_profile(self.profile_id, single=self.single.get(), directories=directories)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.status.set('Chưa áp dụng xong: '+str(exc))
            self.tab.status.set(str(exc))
            return
        self.tab.status.set(message)
        self.destroy()
