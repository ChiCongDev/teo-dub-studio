"""Giọng & âm lượng on a link's tab (owner 05/11/2026): the voice of THIS link (one of Tèo's own voices or a learned one) and the two
export levels (the original sound, the dubbed voice; 0–200%), with a 20-second listening test, and optionally the videos the link has
already finished made again with the change. Only what is changed is saved; every other setting of the link stays. Videos not made
yet use the change from the next one. A finished video is made again only when asked (campaign_redo)."""
import os
import queue
import threading
import tkinter as tk
from tkinter import ttk

from teodub import campaign_redo
from teodub.audio_mix import validate_percent
from teodub.campaign_settings import current_voice_config, save_next_link_settings
from teodub.voice_learning import list_profiles, profile_label, profile_selection

KEEP = '(Giữ giọng hiện tại)'


def voice_choices(data_dir, config):
    """[(label, selection)] for the link: Tèo's own voices first, then the learned ones. `selection` is what
    save_next_voice_settings takes; a preset keeps the link's reading style, speed and voice engine."""
    from teodub.ai_models import local_voices
    tts_model = config.get('tts_model', 'v3turbo')
    choices = [(f'{name} (giọng có sẵn)', dict(voice_provider='local', voice=name, reference_audio='', reference_sha256='',
                                               reading_style=config.get('reading_style', 'legacy'),
                                               voice_speed=config.get('voice_speed', 1.0), tts_model=tts_model))
               for name in local_voices(tts_model)]
    profiles, _errors = list_profiles(data_dir)
    for profile in profiles:
        choices.append((profile_label(profile), profile_selection(data_dir, profile['id'], tts_model)))
    return choices


def describe_current(config):
    if config.get('voice_provider') == 'capcut':
        return f'{config.get("voice", "?")} (CapCut trực tuyến)'
    if config.get('reference_audio'):
        return 'giọng học theo mẫu'
    return str(config.get('voice', '?'))


def percent_text(gain):
    value = round(float(gain)*100, 1)
    return str(int(value)) if value == int(value) else str(value)


class ChangeVoiceDialog(tk.Toplevel):
    def __init__(self, tab):
        super().__init__(tab)
        self.tab, self.studio, self.directory = tab, tab.studio, tab.directory
        manifest = tab.manifest or {}
        self.name = str(manifest.get('name') or self.directory.name[:8])
        self.closed = False
        self.previewing = False
        self.cancel = threading.Event()
        self.results = queue.Queue()
        self.current_levels = (.1, 1.0)
        self.title('Giọng & âm lượng')
        self.geometry('880x760')
        self.minsize(740, 640)
        self.transient(tab.winfo_toplevel())
        shell = ttk.Frame(self, padding=16)
        shell.pack(fill='both', expand=True)
        ttk.Label(shell, text=f'Giọng & âm lượng của "{self.name}"', font=('Segoe UI', 16, 'bold')).pack(anchor='w')
        ttk.Label(shell, wraplength=820, text='Chỉ phần bạn đổi được lưu; Sub và mọi cấu hình khác của link giữ nguyên, các link khác không '
                  'bị ảnh hưởng. Video chưa làm dùng thay đổi từ video kế tiếp. Video đã xong giữ nguyên trừ khi bạn tích làm lại bên dưới.').pack(
                  anchor='w', pady=(4, 8))
        # The buttons and the notice are packed first, at the bottom, so a short window shrinks the list, never the buttons.
        actions = ttk.Frame(shell)
        actions.pack(side='bottom', fill='x')
        self.status = tk.StringVar(self, '')
        ttk.Label(shell, textvariable=self.status, wraplength=820, foreground='#164b78').pack(side='bottom', anchor='w', pady=6)
        self.apply_button = ttk.Button(actions, text='Áp dụng', command=self.apply, state='disabled')
        self.apply_button.pack(side='left')
        self.check_button = ttk.Button(actions, text='Kiểm tra video làm lại được', command=self.check)
        self.check_button.pack(side='left', padx=8)
        ttk.Button(actions, text='Đóng', command=self.close).pack(side='right')
        self.current = tk.StringVar(self)
        ttk.Label(shell, textvariable=self.current).pack(anchor='w')
        ttk.Label(shell, text='Giọng:').pack(anchor='w', pady=(8, 2))
        self.choice = tk.StringVar(self, KEEP)
        self.picker = ttk.Combobox(shell, textvariable=self.choice, state='readonly', width=80, values=[KEEP])
        self.picker.pack(anchor='w')
        levels = ttk.LabelFrame(shell, text='Âm lượng khi xuất video (0% tắt hẳn · 100% mức gốc · 200% gấp đôi)', padding=10)
        levels.pack(fill='x', pady=(12, 0))
        self.original = tk.StringVar(self)
        self.voice_level = tk.StringVar(self)
        row = ttk.Frame(levels)
        row.pack(fill='x')
        ttk.Label(row, text='Tiếng video gốc').pack(side='left')
        ttk.Spinbox(row, textvariable=self.original, from_=0, to=200, increment=5, width=7).pack(side='left', padx=(6, 2))
        ttk.Label(row, text='%').pack(side='left')
        ttk.Label(row, text='Giọng lồng tiếng').pack(side='left', padx=(28, 0))
        ttk.Spinbox(row, textvariable=self.voice_level, from_=0, to=200, increment=5, width=7).pack(side='left', padx=(6, 2))
        ttk.Label(row, text='%').pack(side='left')
        self.preview_button = ttk.Button(row, text='Nghe thử 20 giây', command=self.preview, state='disabled')
        self.preview_button.pack(side='right')
        ttk.Label(levels, wraplength=800, foreground='#444444', text='Nghe thử xuất 20 giây đầu của một video đã xong của link với hai mức này '
                  '(tiếng gốc vẫn tự hạ xuống khi có lời đọc), dùng giọng hiện có của video đó. Không đổi gì và không tốn lượt AI.').pack(
                  anchor='w', pady=(6, 0))
        self.redo = tk.BooleanVar(self, False)
        ttk.Checkbutton(shell, variable=self.redo, text='Làm lại cả video ĐÃ HOÀN THÀNH với thay đổi này').pack(anchor='w', pady=(14, 0))
        ttk.Label(shell, wraplength=820, foreground='#444444', text='Không tải lại video và không gọi AI: Tèo dùng lại video gốc và lời Việt đã '
                  'dịch. Chỉ đổi âm lượng: dùng lại giọng đã tạo, chỉ trộn và xuất lại (nhanh, không tốn GPU tạo giọng). Đổi giọng: tạo giọng '
                  'lại, vài phút mỗi video. Bản cũ được giữ nguyên; bản mới nằm cạnh, có đuôi "_lan-2". Video đã đăng hoặc đã gửi lên Page '
                  'không bị làm lại. Video có lịch đăng chưa gửi: lịch giữ giờ, Page và caption, chỉ chuyển sang video mới khi nó xong.').pack(
                  anchor='w', pady=(2, 8))
        ttk.Label(shell, text='Kết quả kiểm tra:').pack(anchor='w')
        body = ttk.Frame(shell)
        body.pack(fill='both', expand=True)
        self.result = tk.Text(body, wrap='word', height=6, font=('Segoe UI', 10), state='disabled')
        self.result.pack(side='left', fill='both', expand=True)
        scroll = ttk.Scrollbar(body, orient='vertical', command=self.result.yview)
        scroll.pack(side='right', fill='y')
        self.result.configure(yscrollcommand=scroll.set)
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.choices = {}
        self.load()
        self.timer = self.after(300, self.poll)

    def load(self):
        try:
            config = current_voice_config(self.studio.data_dir, self.directory)
            self.choices = dict(voice_choices(self.studio.data_dir, config))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.status.set('Chưa đọc được cấu hình hiện tại của link: '+str(exc))
            return
        self.current_levels = (float(config.get('original_gain', .1)), float(config.get('voice_gain', 1.0)))
        self.original.set(percent_text(self.current_levels[0]))
        self.voice_level.set(percent_text(self.current_levels[1]))
        self.current.set('Hiện tại, cho video chưa làm: giọng '+describe_current(config)
                         +f' · tiếng gốc {self.original.get()}% · giọng lồng {self.voice_level.get()}%')
        self.picker.configure(values=[KEEP, *self.choices])
        self.apply_button.configure(state='normal')
        self.preview_button.configure(state='normal')

    def say(self, text):
        self.result.configure(state='normal')
        self.result.delete('1.0', 'end')
        self.result.insert('1.0', text)
        self.result.configure(state='disabled')

    def levels(self):
        """(original_gain, voice_gain) typed in the two boxes, refused outside 0–200%."""
        return (validate_percent(self.original.get(), 'Âm lượng tiếng gốc'),
                validate_percent(self.voice_level.get(), 'Âm lượng giọng lồng'))

    def check(self):
        try:
            plan = campaign_redo.build_plan(self.directory)
        except (OSError, ValueError, KeyError) as exc:
            self.say('')
            self.status.set('Chưa kiểm tra được: '+str(exc))
            return
        todo = [e for e in plan['entries'] if e['kind'] == 'redo']
        skipped = [e for e in plan['entries'] if e['kind'] == 'skip']
        lines = [f'Làm lại được: {len(todo)} video đã hoàn thành'+(f' (trong đó {plan["scheduled"]} video có lịch đăng chưa gửi, '
                 'lịch sẽ chuyển sang video mới)' if plan['scheduled'] else '')+'.',
                 f'Bỏ qua: {len(skipped)} video.']
        lines += [f'  · Video {e["number"]}: {e["reason"]}' for e in skipped[:40]]
        if len(skipped) > 40:
            lines.append(f'  … và {len(skipped)-40} video khác.')
        self.say('\n'.join(lines))
        self.status.set('Chỉ đọc; chưa thay đổi gì.')

    def preview(self):
        if self.previewing:
            return
        try:
            original, voice = self.levels()
        except ValueError as exc:
            self.status.set(str(exc))
            return
        self.previewing = True
        self.preview_button.configure(state='disabled')
        self.status.set('Đang xuất 20 giây nghe thử… (chờ vài chục giây)')
        data_dir, directory, cancel = self.studio.data_dir, self.directory, self.cancel

        def work():
            from teodub.audio_preview import make_preview
            try:
                self.results.put((make_preview(data_dir, directory, original, voice, cancel=cancel), None))
            except Exception as exc:
                self.results.put((None, str(exc) or type(exc).__name__))
        threading.Thread(target=work, daemon=True).start()

    def poll(self):
        self.timer = None
        if self.closed:
            return
        try:
            video, error = self.results.get_nowait()
        except queue.Empty:
            self.timer = self.after(300, self.poll)
            return
        self.previewing = False
        self.preview_button.configure(state='normal')
        if error:
            self.status.set('Chưa nghe thử được: '+error)
        else:
            try:
                os.startfile(video)
                self.status.set('Đã mở bản nghe thử. Đây chỉ là bản thử, chưa đổi gì của link.')
            except OSError as exc:
                self.status.set(f'Đã xuất bản nghe thử nhưng chưa mở được ({exc}): {video}')
        self.timer = self.after(300, self.poll)

    def apply(self):
        selection = None if self.choice.get() == KEEP else self.choices.get(self.choice.get())
        if selection is None and self.choice.get() != KEEP:
            self.status.set('Chọn giọng trong danh sách.')
            return
        try:
            original, voice = self.levels()
        except ValueError as exc:
            self.status.set(str(exc))
            return
        original = None if abs(original-self.current_levels[0]) < 1e-9 else original
        voice = None if abs(voice-self.current_levels[1]) < 1e-9 else voice
        if selection is None and original is None and voice is None:
            self.status.set('Chưa đổi gì: chọn giọng khác hoặc đổi âm lượng.')
            return
        try:
            record = save_next_link_settings(self.studio.data_dir, self.directory, selection=selection, original_gain=original,
                                             voice_gain=voice, allow_finished=self.redo.get(), what='thay đổi mới')
            changed = ' và '.join(part for part, used in (('giọng', selection is not None),
                                                          ('âm lượng', original is not None or voice is not None)) if used)
            message = f'Đã đổi {changed} của "{self.name}" cho video chưa làm, từ video chạy tiếp theo.'
            if self.redo.get():
                plan = campaign_redo.build_plan(self.directory, settings_id=record['id'])
                if plan['numbers']:
                    outcome = campaign_redo.apply(self.directory, plan['numbers'])
                    message += (f' Đã xếp làm lại {len(plan["numbers"])} video đã xong; '
                                +('link đang chạy sẽ làm chúng trước video kế tiếp.' if outcome['mode'] == 'request'
                                  else 'bấm Chạy để làm.'))
                else:
                    message += ' Không có video đã xong nào cần làm lại.'
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.status.set('Chưa áp dụng: '+str(exc))
            return
        self.tab.message.set(message)
        self.tab.refresh(full=True)
        self.close()

    def close(self):
        self.closed = True
        self.cancel.set()
        if self.timer is not None:
            self.after_cancel(self.timer)
            self.timer = None
        self.destroy()
