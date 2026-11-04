"""Loại trừ video on a link's tab (owner 04/11/2026): paste video links, check them, then mark them "Đã loại trừ" so Tèo never dubs or
subtitles them. "Kiểm tra" only reads; nothing changes until "Loại trừ" or "Bỏ loại trừ" is pressed. No AI call."""
import tkinter as tk
from tkinter import ttk

from teodub.campaign_exclude import apply, build_plan, cancel_unsent
from teodub.campaign_tracking import STATUS
from teodub.page_schedule import STATE_LABELS, display_time


class ExcludeVideosDialog(tk.Toplevel):
    def __init__(self, tab):
        super().__init__(tab)
        self.tab, self.studio, self.directory = tab, tab.studio, tab.directory
        manifest = tab.manifest or {}
        self.name = str(manifest.get('name') or self.directory.name[:8])
        self.closed = False
        self.title('Loại trừ video')
        self.geometry('900x700')
        self.minsize(760, 600)
        self.transient(tab.winfo_toplevel())
        shell = ttk.Frame(self, padding=16)
        shell.pack(fill='both', expand=True)
        ttk.Label(shell, text=f'Loại trừ video khỏi "{self.name}"', font=('Segoe UI', 16, 'bold')).pack(anchor='w')
        ttk.Label(shell, wraplength=840, text='Dán link video Douyin (mỗi dòng một link, hoặc nhiều link cách nhau bằng dấu cách) rồi bấm Kiểm tra. '
                  'Video chưa xong bị loại trừ sẽ KHÔNG bao giờ được lồng tiếng hay Vietsub, không giữ link lại khi hoàn tất, và "Lấy thêm '
                  'video mới…" không thêm lại. Video ĐÃ XONG không bị làm lại: nó được đặt KHÔNG ĐĂNG (Tèo không bao giờ xếp lịch đăng nó, '
                  'lịch chưa gửi của nó bị bỏ; lịch đã gửi lên Facebook giữ nguyên, chỉ báo). Link đang chạy vẫn nhận: áp dụng từ video tiếp '
                  'theo, video đang làm dở vẫn làm xong. Không xóa gì; muốn đổi ý thì dán link và bấm Bỏ loại trừ.').pack(anchor='w', pady=(4, 8))
        # The buttons and the notice are packed first, at the bottom, so a short window shrinks the lists, never the buttons.
        actions = ttk.Frame(shell)
        actions.pack(side='bottom', fill='x')
        self.status = tk.StringVar(self, 'Dán link video rồi bấm Kiểm tra.')
        ttk.Label(shell, textvariable=self.status, wraplength=840, foreground='#164b78').pack(side='bottom', anchor='w', pady=6)
        ttk.Button(actions, text='Kiểm tra', command=lambda: self.check(False)).pack(side='left')
        self.exclude_button = ttk.Button(actions, text='Loại trừ các video này', command=lambda: self.run(False), state='disabled')
        self.exclude_button.pack(side='left', padx=8)
        self.restore_button = ttk.Button(actions, text='Bỏ loại trừ', command=lambda: self.run(True))
        self.restore_button.pack(side='left')
        ttk.Button(actions, text='Đóng', command=self.close).pack(side='right')
        ttk.Label(shell, text='Link video cần loại trừ:').pack(anchor='w')
        self.links = tk.Text(shell, height=7, wrap='word', font=('Segoe UI', 10))
        self.links.pack(fill='x', pady=(2, 8))
        ttk.Label(shell, text='Kết quả kiểm tra:').pack(anchor='w')
        body = ttk.Frame(shell)
        body.pack(fill='both', expand=True)
        self.result = tk.Text(body, wrap='word', height=10, font=('Segoe UI', 10), state='disabled')
        self.result.pack(side='left', fill='both', expand=True)
        scroll = ttk.Scrollbar(body, orient='vertical', command=self.result.yview)
        scroll.pack(side='right', fill='y')
        self.result.configure(yscrollcommand=scroll.set)
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.links.bind('<<Modified>>', self.changed)
        self.plan = None

    def changed(self, _event=None):
        if self.links.edit_modified():
            self.links.edit_modified(False)
            self.plan = None
            self.exclude_button.configure(state='disabled')

    def say(self, text):
        self.result.configure(state='normal')
        self.result.delete('1.0', 'end')
        self.result.insert('1.0', text)
        self.result.configure(state='disabled')

    def check(self, restore):
        try:
            plan = build_plan(self.directory, self.links.get('1.0', 'end'), restore=restore)
        except (OSError, ValueError, KeyError) as exc:
            self.plan = None
            self.say('')
            self.status.set('Chưa kiểm tra được: '+str(exc))
            return None
        self.plan = plan
        verb = 'bỏ loại trừ' if restore else 'loại trừ'
        lines = []
        for entry in plan['entries']:
            if entry['kind'] == 'mark' and entry['nopost']:
                state = STATUS.get(entry['status'], entry['status'])
                lines.append(f"✓ Video {entry['number']} ({state}) — " + ('sẽ bỏ dấu KHÔNG ĐĂNG (lịch đã bỏ không tự quay lại; thêm lại trong Quản lý Page nếu cần).'
                             if restore else 'đã xong: sẽ đặt KHÔNG ĐĂNG (giữ video, không lồng tiếng lại).'))
                for schedule in entry['schedules']:
                    when = display_time(schedule['due_at'])
                    lines.append(f"      ↳ lịch đăng {when}: " + (f"chưa gửi → sẽ BỎ lịch này." if schedule['unsent'] else
                                 f"{STATE_LABELS.get(schedule['state'], schedule['state'])} → giữ nguyên, Tèo chỉ báo; kiểm tra trên Facebook."))
            elif entry['kind'] == 'mark':
                lines.append(f"✓ Video {entry['number']} ({STATUS.get(entry['status'], entry['status'])}) — sẽ {verb}.")
            elif entry['number'] is None:
                lines.append(f"✕ …{entry['id'][-8:]} — {entry['reason']}")
            else:
                lines.append(f"• Video {entry['number']} ({STATUS.get(entry['status'], entry['status'])}) — {entry['reason']}")
        for piece in plan['unreadable']:
            lines.append(f'? "{piece}" — không đọc được số video. Dán link đầy đủ https://www.douyin.com/video/… (link rút gọn v.douyin.com chưa dùng được).')
        self.say('\n'.join(lines) if lines else 'Chưa thấy link video nào trong ô.')
        self.exclude_button.configure(state='normal' if plan['marked'] and not restore else 'disabled')
        self.status.set(f"{plan['marked']} video sẽ {verb}. " + ('Bấm "Loại trừ các video này" để áp dụng.' if plan['marked'] and not restore
                        else ('Bấm Bỏ loại trừ để áp dụng.' if plan['marked'] else 'Không có video nào để đổi.')))
        return plan

    def run(self, restore):
        plan = self.check(restore)
        if not plan or not plan['marked']:
            return
        numbers = [entry['number'] for entry in plan['entries'] if entry['kind'] == 'mark']
        try:
            outcome = apply(self.directory, numbers, restore=restore)
        except (OSError, ValueError) as exc:
            self.status.set('Chưa đổi được: '+str(exc))
            return
        verb = 'bỏ loại trừ' if restore else 'loại trừ'
        if outcome['mode'] == 'request':
            message = (f'Link đang chạy: đã nhận yêu cầu {verb} {len(numbers)} video; áp dụng trước video tiếp theo '
                       '(video đang làm dở vẫn làm xong).')
        else:
            message = f"Đã {verb} {len(outcome['changed'])} video."
        if not restore:
            message += self.cancel_schedules([entry['number'] for entry in plan['entries'] if entry['kind'] == 'mark' and entry['nopost']])
        self.tab.message.set(message)
        self.status.set(message)
        self.exclude_button.configure(state='disabled')
        self.studio.refresh_buttons()
        self.tab.refresh(full=True)

    def cancel_schedules(self, numbers):
        """Choice A of 04/11/2026: the Page schedules of the videos just set to Không đăng that were never sent are cancelled."""
        if not numbers:
            return ''
        get = getattr(getattr(self.studio, 'facebook_tab', None), 'get_page_scheduler', None)
        if get is None:
            return ' Chưa bỏ được lịch đăng của các video đã xong: mở tab Quản lý Page rồi dán lại link và bấm Loại trừ.'
        try:
            result = cancel_unsent(get(), self.directory.name, numbers)
        except (OSError, ValueError, RuntimeError) as exc:
            return ' Chưa bỏ được lịch đăng: '+str(exc)
        text = f" Đã bỏ {len(result['cancelled'])} lịch đăng chưa gửi." if result['cancelled'] else ''
        if result['kept']:
            text += f" {len(result['kept'])} lịch đã gửi hoặc đang xử lý được giữ nguyên; kiểm tra trên Facebook."
        if result['failed']:
            text += f" {len(result['failed'])} lịch chưa bỏ được: {result['failed'][0][1]}"
        return text

    def close(self):
        self.closed = True
        self.destroy()
