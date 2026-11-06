"""Áp dụng cho cả video đã xong chưa đăng (owner 06/11/2026): the Sub Việt choice on a link's tab, also for the videos it has already
finished but that are not posted yet. They are made again in new revision jobs (no download, no AI call, the voice already made is
reused), the ones whose Page schedule is soonest first. A video already posted or sent to a Page is never touched; a schedule not sent
yet keeps its time, Page and caption and moves to the new file when it is ready, and posts the old file at its time if the new one
is not ready (campaign_redo, page_schedule_redo)."""
from datetime import datetime, timedelta, timezone
import time
import tkinter as tk
from tkinter import ttk

from teodub import campaign_redo
from teodub.campaign_settings import made_with, proposed_subtitle_settings, save_next_subtitle_settings
from teodub.subtitle_style import SUBTITLE_BOXES

LOCAL_TIME = timezone(timedelta(hours=7))
SHOWN = 12  # Videos listed with their time; the rest are counted.


def sub_text(burn, box):
    if not burn:
        return 'không ghi Sub Việt vào hình (chỉ có Sub bật/tắt)'
    return 'ghi Sub Việt vào hình, '+(SUBTITLE_BOXES[box]['label'].lower() if box != 'none' else 'không khung che')


def describe(plan, order, name_of=lambda number: f'Video {number}'):
    """The text of the check: how many videos will be made again, in which order, and why the others are left."""
    todo = [e for e in plan['entries'] if e['kind'] == 'redo']
    skipped = [e for e in plan['entries'] if e['kind'] == 'skip']
    lines = [f'Sẽ làm lại: {len(todo)} video đã hoàn thành, chưa đăng.'
             +(f' Trong đó {plan["scheduled"]} video có lịch đăng; lịch giữ giờ, Page và caption, chuyển sang file mới khi nó xong.'
               if plan['scheduled'] else '')]
    if order:
        lines.append('Làm trước, theo giờ đăng gần nhất:')
        lines += [f'  {index}. {name_of(number)} — đăng {datetime.fromtimestamp(due, LOCAL_TIME).strftime("%d/%m %H:%M")}'
                  for index, (number, due) in enumerate(order[:SHOWN], 1)]
        if len(order) > SHOWN:
            lines.append(f'  … và {len(order)-SHOWN} video có lịch khác.')
        rest = len(todo)-len(order)
        if rest > 0:
            lines.append(f'Sau đó: {rest} video chưa có lịch hoặc lịch đã lỡ giờ, theo số video.')
    lines.append(f'Bỏ qua: {len(skipped)} video.')
    reasons = {}
    for entry in skipped:
        reasons.setdefault(entry['reason'], []).append(entry['number'])
    for reason, numbers in reasons.items():
        shown = ', '.join(str(number) for number in numbers[:15])+(' …' if len(numbers) > 15 else '')
        lines.append(f'  · {len(numbers)} video — {reason} ({shown})')
    return '\n'.join(lines)


class SubtitleRedoDialog(tk.Toplevel):
    def __init__(self, tab):
        super().__init__(tab)
        self.tab, self.studio, self.directory = tab, tab.studio, tab.directory
        self.name = str((tab.manifest or {}).get('name') or self.directory.name[:8])
        self.burn = bool(tab.burn.get())
        self.box = next((key for key, item in SUBTITLE_BOXES.items() if item['label'] == tab.box_label.get()), 'none')
        if not self.burn:
            self.box = 'none'
        self.todo = 0
        self.title('Áp dụng Sub cho video đã xong')
        self.geometry('860x640')
        self.minsize(720, 520)
        self.transient(tab.winfo_toplevel())
        shell = ttk.Frame(self, padding=16)
        shell.pack(fill='both', expand=True)
        ttk.Label(shell, text=f'Làm lại video đã xong của "{self.name}"', font=('Segoe UI', 16, 'bold')).pack(anchor='w')
        ttk.Label(shell, wraplength=800, text='Sub dùng cho cả video chưa xong và video đã xong nhưng chưa đăng: '
                  + sub_text(self.burn, self.box)+'.', font=('Segoe UI', 11, 'bold')).pack(anchor='w', pady=(6, 4))
        ttk.Label(shell, wraplength=800, foreground='#444444', text=(
            'Không tải lại video và không gọi AI: Tèo dùng lại video gốc, lời Việt và giọng đã tạo, chỉ dựng lại hình có Sub. Bản cũ '
            'được giữ nguyên; bản mới nằm cạnh, có đuôi "_lan-2". Video đã đăng hoặc đã gửi lên Page không bị làm lại. Tới giờ đăng mà '
            'bản mới chưa xong thì Tèo đăng bản cũ đúng giờ. Video có lịch đăng gần nhất được làm trước.')).pack(anchor='w', pady=(0, 8))
        actions = ttk.Frame(shell)
        actions.pack(side='bottom', fill='x')
        self.status = tk.StringVar(self, 'Đang kiểm tra các video… (chỉ đọc)')
        ttk.Label(shell, textvariable=self.status, wraplength=800, foreground='#164b78').pack(side='bottom', anchor='w', pady=6)
        self.apply_button = ttk.Button(actions, text='Áp dụng và làm lại', command=self.apply, state='disabled')
        self.apply_button.pack(side='left')
        ttk.Button(actions, text='Đóng', command=self.close).pack(side='right')
        body = ttk.Frame(shell)
        body.pack(fill='both', expand=True)
        self.result = tk.Text(body, wrap='word', font=('Segoe UI', 10), state='disabled')
        self.result.pack(side='left', fill='both', expand=True)
        scroll = ttk.Scrollbar(body, orient='vertical', command=self.result.yview)
        scroll.pack(side='right', fill='y')
        self.result.configure(yscrollcommand=scroll.set)
        self.closed = False
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.timer = self.after(50, self.check)

    def say(self, text):
        self.result.configure(state='normal')
        self.result.delete('1.0', 'end')
        self.result.insert('1.0', text)
        self.result.configure(state='disabled')

    def check(self):
        """Read only: nothing is saved or marked until the owner presses Áp dụng và làm lại."""
        if self.timer is not None:
            self.after_cancel(self.timer)
        self.timer = None
        try:
            settings = proposed_subtitle_settings(self.studio.data_dir, self.directory, self.burn, self.box, allow_finished=True)
            plan = campaign_redo.build_plan(self.directory, unchanged=lambda config: made_with(config, settings))
            manifest = self.tab.manifest or {}
            due = campaign_redo.next_due(self.studio.data_dir, manifest.get('id', ''))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.say('')
            self.status.set('Chưa kiểm tra được: '+str(exc))
            return
        urgent = sorted(((number, due[number]) for number in plan['numbers'] if number in due), key=lambda row: (row[1], row[0]))
        self.todo = len(plan['numbers'])
        self.say(describe(plan, urgent))
        self.apply_button.configure(state='normal' if self.todo else 'disabled')
        self.status.set('Chỉ đọc; chưa thay đổi gì.' if self.todo else
                        'Không có video đã xong nào cần làm lại. Muốn chỉ đổi Sub cho video chưa xong, đóng cửa sổ này và bấm '
                        '"Áp dụng cho video chưa xong".')

    def apply(self):
        try:
            record = save_next_subtitle_settings(self.studio.data_dir, self.directory, self.burn, self.box, allow_finished=True)
            plan = campaign_redo.build_plan(self.directory, settings_id=record['id'],
                                            unchanged=lambda config: made_with(config, record['settings']))
            message = f'Đã lưu Sub cho video chưa xong của "{self.name}": {sub_text(self.burn, self.box)}.'
            if plan['numbers']:
                outcome = campaign_redo.apply(self.directory, plan['numbers'])
                message += (f' Đã xếp làm lại {len(plan["numbers"])} video đã xong, video có lịch đăng gần nhất làm trước; '
                            +('link đang chạy sẽ làm chúng trước video kế tiếp.' if outcome['mode'] == 'request' else 'bấm Chạy để làm.'))
            else:
                message += ' Không có video đã xong nào cần làm lại.'
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.status.set('Chưa áp dụng: '+str(exc))
            return
        self.tab.subtitle_dirty = False
        self.tab.message.set(message)
        self.tab.refresh(full=True)
        self.close()

    def close(self):
        self.closed = True
        if self.timer is not None:
            self.after_cancel(self.timer)
            self.timer = None
        self.destroy()
