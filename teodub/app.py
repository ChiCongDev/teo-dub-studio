"""Standalone Vietnamese desktop UI; media work always runs in a child process."""
import argparse
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import uuid
import wave
import winsound

from teodub.common import atomic_json, hash_file
from teodub.speech_plan import STYLES, validate_style
from teodub.speech_audio import VOICE_SPEEDS, validate_voice_speed
from teodub.ai_models import CHOICES, LEGACY_MODELS, NEW_MODELS, validate_models, local_voices as model_voices
from teodub.subtitle_style import SUBTITLE_COLORS, SUBTITLE_WEIGHTS, validate_subtitle_style
from teodub.wording import format_rule_text, parse_rule_text, rewrite_text, validate_wording
from teodub.adam_voice import LABEL as ADAM_VOICE
from teodub.audio_mix import validate_audio_mix, validate_percent


VOICES = ('Mai Anh', 'Trúc Ly', 'Thùy Dung', 'Ngọc Huyền')
VOICE_PROVIDERS = {'local': 'Trên máy · miễn phí', 'capcut': 'CapCut trực tuyến (thử nghiệm)'}
CAPCUT_VOICES = ('Cô Gái Hoạt Ngôn', 'Thanh Niên Tự Tin')
REFERENCE_VOICE = 'Giọng từ mẫu'
REFERENCE_DIRECTORY = Path(__file__).resolve().parents[1]/'assets'/'voice-references'
REFERENCE_ALIASES = {
    'Cô Gái Hoạt Ngôn (theo mẫu)': REFERENCE_DIRECTORY/'co-gai-hoat-ngon.wav',
    'Thanh Niên Tự Tin (theo mẫu)': REFERENCE_DIRECTORY/'thanh-nien-tu-tin.wav',
}
SPEEDS = {'0,9× — hình chậm hơn': .9, '1,0× — tốc độ gốc': 1.0}
VOICE_SPEED_LABELS = {
    f'{speed:.2f}'.replace('.', ',')+'× — '+{
        1.0: 'giữ nhịp đã chọn', 1.05: 'nhanh nhẹ', 1.1: 'nhanh hơn', 1.2: 'nhanh nhiều'
    }.get(speed, 'chậm hơn' if speed < 1 else 'nhanh hơn'): speed
    for speed in VOICE_SPEEDS
}
FINISHED = {'completed', 'review_required'}
MAX_LOG_LINES = 160
READING_STYLES = ('conversation', 'story', 'advertising', 'legacy')


def open_capcut_login():
    from teodub.capcut_tts import open_capcut_login as open_login
    return open_login()


def default_data_dir():
    return Path(os.environ.get('LOCALAPPDATA', Path.home()/'.local'/'share'))/'TeoDubStudio'


def worker_command(config_path):
    if getattr(sys, 'frozen', False):
        prefix = [sys.executable]
    else:
        project = Path(__file__).resolve().parents[1]
        python = project/'.venv'/'Scripts'/'python.exe'
        if os.name != 'nt':
            python = project/'.venv'/'bin'/'python'
        if not python.is_file():
            raise ValueError('Chưa có môi trường chạy .venv. Cài đặt Tèo Dub Studio trên máy này trước khi chạy.')
        prefix = [str(python), '-X', 'utf8', '-B', str(project/'launch.py')]
    return prefix + ['--worker', '--config', str(Path(config_path).resolve())]


class Studio(ttk.Frame):
    def __init__(self, master, data_dir, *, parent=None):
        super().__init__(parent if parent is not None else master, padding=16)
        self.window = master
        self.data_dir = Path(data_dir).resolve()
        self.voice_learning_busy = False
        self.learned_profiles = {}
        self.refresh_learned_voices(update=False)
        self.adam_reference = None
        try:
            from teodub.adam_voice import saved_reference
            self.adam_reference = saved_reference(self.data_dir)
        except (OSError, ValueError, KeyError, TypeError):
            pass  # A missing/broken sample stays unavailable; never substitute a voice.
        self.jobs_dir = self.data_dir/'runs'
        self.process = None
        self.campaign_busy = False
        self.current = None
        self.result = None
        self.drive_options = dict(version=1, enabled=False, account_id='', folder_id='', folder_name='')
        self.clean_options = dict(enabled=False)
        self.clean_enabled = tk.BooleanVar(master, value=False)
        self.clean_summary = tk.StringVar(master, value='Tải video và dịch Sub, rồi lưu vùng xóa cho video này.')
        self._clean_input = None
        self.closing = False
        self.closed = False
        self.restart_pending = self.restart_draining = False
        self.close_started = None
        self.stop_started = None
        self.terminated_at = None
        self.autoplay_preview = False
        self.preview_path = None
        self.preview_config = None
        self.playing = False
        self.playback_timer = None
        self.setting_profile = False
        self.login_pending = False
        self.login_results = queue.Queue()
        self.event_offset = 0
        self.event_pending = b''
        self.logged_stage = None
        self.last_event_message = None
        self.fields = {
            'original_dir': tk.StringVar(master, value=str(Path.home()/'Downloads'/'Teo Dub Studio')),
            'output_dir': tk.StringVar(master, value=str(Path.home()/'Videos'/'Teo Dub Studio')),
            'subtitles': tk.StringVar(master),
            'voice_provider': tk.StringVar(master, value='local'),
            'voice': tk.StringVar(master, value=VOICES[0]),
            'reference_audio': tk.StringVar(master),
            'reading_style': tk.StringVar(master, value='conversation'),
            'voice_speed': tk.StringVar(master, value=next(label for label, speed in VOICE_SPEED_LABELS.items() if speed == 1.0)),
            'speed': tk.StringVar(master, value=next(iter(SPEEDS))),
            'original_volume': tk.StringVar(master, value='10'),
            'voice_volume': tk.StringVar(master, value='100'),
            'burn_subtitles': tk.BooleanVar(master, value=False),
            'subtitle_color': tk.StringVar(master, value='white'),
            'subtitle_weight': tk.StringVar(master, value='bold'),
            'subtitle_size': tk.StringVar(master, value='13'),
            'wording_enabled': tk.BooleanVar(master, value=False),
            'wording_rules': tk.StringVar(master, value='bán => báng'),
            'drive_enabled': tk.BooleanVar(master, value=False),
            **{key: tk.StringVar(master, value=value) for key, value in NEW_MODELS.items()},
        }
        self.provider_label = tk.StringVar(master, value=VOICE_PROVIDERS['local'])
        self.style_label = tk.StringVar(master, value=STYLES['conversation']['label'])
        self.subtitle_color_label = tk.StringVar(master, value=SUBTITLE_COLORS['white']['label'])
        self.subtitle_weight_label = tk.StringVar(master, value=SUBTITLE_WEIGHTS['bold']['label'])
        self.voice_help = tk.StringVar(master)
        self.model_summary = tk.StringVar(master)
        self.wording_summary = tk.StringVar(master)
        self.drive_summary = tk.StringVar(master, value='Drive: chưa bật tự lưu')
        self.status = tk.StringVar(master, value='Chọn video hoặc dán link rồi bấm Bắt đầu.')
        self.progress = tk.DoubleVar(master, value=0)
        self.inputs = []
        self.build()
        for key in ('voice_provider', 'voice', 'reference_audio', 'reading_style', 'voice_speed', *CHOICES):
            self.fields[key].trace_add('write', self.profile_changed)
        self.fields['burn_subtitles'].trace_add('write', self.subtitle_changed)
        for key in ('wording_enabled', 'wording_rules'):
            self.fields[key].trace_add('write', self.update_wording_summary)
        self.update_wording_summary()
        self.fields['drive_enabled'].trace_add('write', self.update_drive_summary)
        self.update_profile_controls()
        self.restore()
        self.refresh_buttons()
        self.timer = master.after(300, self.poll)
        master.protocol('WM_DELETE_WINDOW', self.close)

    def build(self):
        self.pack(fill='both', expand=True)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(6, weight=1)
        ttk.Label(self, text='Tèo Dub Studio', font=('Segoe UI', 24, 'bold')).grid(row=0, column=0, sticky='w')
        ttk.Label(self, wraplength=1000, text='Tải video và lồng tiếng Việt chạy nền. Chọn giọng miễn phí trên máy hoặc CapCut trực tuyến. '
                  'Nhận dạng chạy trên máy; dịch dùng Codex và hạn mức tài khoản.').grid(
                      row=1, column=0, sticky='ew', pady=(5, 8))
        source = ttk.LabelFrame(self, text='1. Video cần lồng tiếng', padding=10)
        source.grid(row=2, column=0, sticky='ew')
        source.columnconfigure(0, weight=1)
        ttk.Label(source, text='Dán link / đoạn chia sẻ Douyin, TikTok hoặc chọn video trên máy:').grid(row=0, column=0, sticky='w')
        self.input = tk.Text(source, height=2, wrap='word', font=('Segoe UI', 11), relief='solid', borderwidth=1)
        self.input.grid(row=1, column=0, sticky='ew', pady=(6, 0))
        self.inputs.append(self.input)
        choose = ttk.Button(source, text='Chọn video…', command=self.choose_input)
        choose.grid(row=1, column=1, padx=(10, 0))
        self.inputs.append(choose)
        steps = ttk.Frame(source)
        steps.grid(row=2, column=0, columnspan=2, sticky='ew', pady=(8, 0))
        self.download_button = ttk.Button(steps, text='1. Tải video', command=lambda: self.start('download'))
        self.download_button.pack(side='left', padx=(0, 6))
        self.captions_button = ttk.Button(steps, text='2. Tải video và dịch Sub', command=lambda: self.start('captions'))
        self.captions_button.pack(side='left', padx=(0, 6))
        self.dialogue_button = ttk.Button(steps, text='3. Xem / sửa lời thoại', command=self.edit_dialogue)
        self.dialogue_button.pack(side='left', padx=(0, 6))
        self.dub_button = ttk.Button(steps, text='4. Lồng tiếng và xuất video', command=self.dub_prepared)
        self.dub_button.pack(side='left')

        options = ttk.LabelFrame(self, text='2. Nơi lưu và giọng đọc', padding=10)
        options.grid(row=3, column=0, sticky='ew', pady=8)
        options.columnconfigure(1, weight=1)
        for row, (key, label) in enumerate((('original_dir', 'Lưu video gốc'),
                                           ('output_dir', 'Lưu video lồng tiếng'),
                                           ('subtitles', 'SRT Việt có sẵn (tùy chọn)'))):
            ttk.Label(options, text=label).grid(row=row, column=0, sticky='w', padx=(0, 14), pady=4)
            entry = ttk.Entry(options, textvariable=self.fields[key])
            entry.grid(row=row, column=1, sticky='ew', pady=4)
            button = ttk.Button(options, text='Chọn…', command=lambda item=key: self.choose(item))
            button.grid(row=row, column=2, padx=(10, 0), pady=4)
            self.inputs.extend((entry, button))
        voice_row = ttk.Frame(options)
        voice_row.grid(row=3, column=0, columnspan=3, sticky='ew', pady=(8, 0))
        ttk.Label(voice_row, text='Bộ giọng').pack(side='left', padx=(0, 10))
        self.provider_picker = ttk.Combobox(voice_row, textvariable=self.provider_label,
                                          values=list(VOICE_PROVIDERS.values()), state='readonly', width=30)
        self.provider_picker.pack(side='left')
        self.provider_picker.bind('<<ComboboxSelected>>', self.choose_provider)
        ttk.Label(voice_row, text='Giọng đọc').pack(side='left', padx=(16, 10))
        self.voice_picker = ttk.Combobox(voice_row, textvariable=self.fields['voice'], values=VOICES,
                                       state='readonly', width=32)
        self.voice_picker.pack(side='left')
        playback = ttk.Frame(options)
        playback.grid(row=4, column=0, columnspan=3, sticky='ew', pady=(7, 0))
        self.preview_button = ttk.Button(playback, text='Nghe thử giọng', command=lambda: self.start('preview'))
        self.preview_button.pack(side='left', padx=(0, 6))
        self.replay_button = ttk.Button(playback, text='Nghe lại', command=self.play_preview)
        self.replay_button.pack(side='left', padx=(0, 6))
        self.stop_listen_button = ttk.Button(playback, text='Dừng nghe', command=self.stop_playback)
        self.stop_listen_button.pack(side='left')
        self.adam_button = ttk.Button(playback, text='Nhập mẫu ADAM…', command=self.choose_adam)
        self.adam_button.pack(side='right')
        pacing = ttk.Frame(options)
        pacing.grid(row=5, column=0, columnspan=3, sticky='ew', pady=(7, 0))
        ttk.Label(pacing, text='Tốc độ giọng').pack(side='left', padx=(0, 10))
        self.voice_speed_picker = ttk.Combobox(pacing, textvariable=self.fields['voice_speed'],
                                              values=list(VOICE_SPEED_LABELS), state='readonly', width=24)
        self.voice_speed_picker.pack(side='left')
        ttk.Label(pacing, text='Kiểu đọc').pack(side='left', padx=(16, 8))
        self.style_picker = ttk.Combobox(pacing, textvariable=self.style_label,
                                        values=[STYLES[key]['label'] for key in READING_STYLES],
                                        state='readonly', width=23)
        self.style_picker.pack(side='left')
        self.style_picker.bind('<<ComboboxSelected>>', self.choose_style)
        ttk.Label(pacing, text='Tốc độ hình').pack(side='left', padx=(16, 8))
        self.speed_picker = ttk.Combobox(pacing, textvariable=self.fields['speed'], values=list(SPEEDS),
                                       state='readonly', width=25)
        self.speed_picker.pack(side='left')
        self.sample_row = ttk.Frame(options)
        self.sample_row.grid(row=6, column=0, columnspan=3, sticky='ew', pady=(6, 0))
        self.sample_row.columnconfigure(1, weight=1)
        ttk.Label(self.sample_row, text='Mẫu giọng 3–8 giây').grid(row=0, column=0, padx=(0, 12))
        sample_entry = ttk.Entry(self.sample_row, textvariable=self.fields['reference_audio'])
        sample_entry.grid(row=0, column=1, sticky='ew')
        sample_button = ttk.Button(self.sample_row, text='Chọn mẫu…', command=self.choose_reference)
        sample_button.grid(row=0, column=2, padx=(10, 0))
        self.inputs.extend((sample_entry, sample_button))
        self.login_row = ttk.Frame(options)
        self.login_row.grid(row=6, column=0, columnspan=3, sticky='ew', pady=(6, 0))
        self.login_button = ttk.Button(self.login_row, text='Mở CapCut / đăng nhập', command=self.login_capcut)
        self.login_button.pack(side='left')
        ttk.Label(self.login_row, text='Đăng nhập thủ công, rồi đóng cửa sổ CapCut này trước khi nghe thử.').pack(
            side='left', padx=(12, 0))
        ttk.Label(options, wraplength=990, textvariable=self.voice_help).grid(
            row=7, column=0, columnspan=3, sticky='w', pady=(6, 0))
        from teodub.audio_mix_ui import AudioMixControls
        self.audio_mix_controls = AudioMixControls(options, self.fields)
        self.audio_mix_controls.grid(row=8, column=0, columnspan=3, sticky='ew', pady=(7, 0))
        self.inputs.extend(self.audio_mix_controls.controls)
        clean_row = ttk.Frame(options)
        clean_row.grid(row=9, column=0, columnspan=3, sticky='ew', pady=(8, 0))
        self.clean_button = ttk.Button(clean_row, text='Lưu / sửa vùng xóa Sub gốc…', command=self.edit_clean)
        self.clean_button.pack(side='left')
        self.clean_check = ttk.Checkbutton(clean_row, text='Xóa Sub gốc trước khi chèn Sub Việt (GPU)',
                                           variable=self.clean_enabled, command=self.toggle_clean)
        self.clean_check.pack(side='left', padx=10)
        ttk.Label(options, textvariable=self.clean_summary, wraplength=990).grid(
            row=10, column=0, columnspan=3, sticky='w', pady=(3, 0))
        subtitle_row = ttk.Frame(options)
        subtitle_row.grid(row=11, column=0, columnspan=3, sticky='ew', pady=(8, 0))
        self.subtitle_check = ttk.Checkbutton(subtitle_row, text='Ghi Sub Việt trực tiếp vào hình',
                                             variable=self.fields['burn_subtitles'])
        self.subtitle_check.pack(side='left', padx=(0, 16))
        ttk.Label(subtitle_row, text='Màu chữ').pack(side='left', padx=(0, 7))
        self.subtitle_color_picker = ttk.Combobox(
            subtitle_row, textvariable=self.subtitle_color_label,
            values=[item['label'] for item in SUBTITLE_COLORS.values()], state='readonly', width=12)
        self.subtitle_color_picker.pack(side='left')
        self.subtitle_color_picker.bind('<<ComboboxSelected>>', self.choose_subtitle_color)
        ttk.Label(subtitle_row, text='Độ đậm').pack(side='left', padx=(16, 7))
        self.subtitle_weight_picker = ttk.Combobox(
            subtitle_row, textvariable=self.subtitle_weight_label,
            values=[item['label'] for item in SUBTITLE_WEIGHTS.values()], state='readonly', width=14)
        self.subtitle_weight_picker.pack(side='left')
        self.subtitle_weight_picker.bind('<<ComboboxSelected>>', self.choose_subtitle_weight)
        ttk.Label(subtitle_row, text='Cỡ chữ').pack(side='left', padx=(12, 6))
        self.subtitle_size_picker = ttk.Spinbox(subtitle_row, from_=8, to=36, increment=1, width=5,
                                               textvariable=self.fields['subtitle_size'])
        self.subtitle_size_picker.pack(side='left')
        ttk.Label(subtitle_row, text='(mặc định 13)').pack(side='left', padx=(5, 0))
        ttk.Label(subtitle_row, textvariable=self.wording_summary).pack(side='right', padx=(12, 0))
        ttk.Label(options, wraplength=990, text='Âm lượng trên áp dụng khi xuất video; Nghe thử giọng giữ mức mẫu 100%. Lời đọc nối tiếp, không đè nhau. '
                  'Tốc độ giọng nhân thêm vào nhịp của Kiểu đọc và giữ nguyên cao độ. '
                  'Lồng tiếng và xuất video dùng SRT đã chọn; Tự động toàn bộ dịch lời mới. Từ ngữ chỉ thay lời khi bạn bật. '
                  'Có thể ghi Sub Việt vào hình với màu và độ đậm đã chọn; luôn xuất thêm một SRT Việt. '
                  'Lưu vùng xóa Sub gốc cho từng video, rồi tick áp dụng trước khi lồng tiếng.').grid(
                      row=12, column=0, columnspan=3, sticky='w', pady=(6, 0))

        drive_row = ttk.Frame(options)
        drive_row.grid(row=13, column=0, columnspan=3, sticky='ew', pady=(7, 0))
        self.drive_check = ttk.Checkbutton(drive_row, text='Tự lưu video thành phẩm lên Google Drive',
                                           variable=self.fields['drive_enabled'])
        self.drive_check.pack(side='left')
        self.drive_button = ttk.Button(drive_row, text='Chọn thư mục Drive…', command=self.choose_drive)
        self.drive_button.pack(side='left', padx=(10, 8))
        ttk.Label(drive_row, textvariable=self.drive_summary, wraplength=420).pack(side='left')

        actions = ttk.Frame(self)
        actions.grid(row=4, column=0, sticky='ew', pady=(0, 10))
        self.start_button = ttk.Button(actions, text='Tự động toàn bộ', command=self.start)
        self.start_button.pack(side='left', padx=(0, 8))
        self.stop_button = ttk.Button(actions, text='Dừng', command=self.stop)
        self.stop_button.pack(side='left', padx=(0, 8))
        self.continue_button = ttk.Button(actions, text='Tiếp tục phiên trước', command=self.resume)
        self.continue_button.pack(side='left', padx=(0, 8))
        self.open_button = ttk.Button(actions, text='Mở kết quả', command=self.open_result)
        self.open_button.pack(side='left', padx=(0, 8))
        self.models_button = ttk.Button(actions, text='Mô hình AI…', command=self.choose_models)
        self.models_button.pack(side='left', padx=(0, 8))
        self.wording_button = ttk.Button(actions, text='Từ ngữ…', command=self.choose_wording)
        self.wording_button.pack(side='left', padx=(0, 8))
        ttk.Button(actions, text='Hướng dẫn', command=self.help).pack(side='right')
        progress = ttk.Frame(self)
        progress.grid(row=5, column=0, sticky='ew')
        ttk.Label(progress, textvariable=self.status, wraplength=1000).pack(fill='x', pady=(0, 6))
        ttk.Progressbar(progress, variable=self.progress, maximum=100).pack(fill='x')
        self.log = tk.Text(self, height=4, wrap='word', state='disabled', font=('Segoe UI', 10),
                           relief='solid', borderwidth=1)
        self.log.grid(row=6, column=0, sticky='nsew', pady=(8, 6))
        ttk.Label(self, wraplength=1000, textvariable=self.model_summary).grid(row=7, column=0, sticky='w')

    def clean_source(self):
        text = self.input.get('1.0', 'end').strip()
        if text and Path(text).is_file():
            return Path(text).resolve()
        if self.current and text == self.current.get('input'):
            try:
                state = json.loads((Path(self.current['directory'])/'state.json').read_text(encoding='utf-8'))
                source = Path(state['source']['path'])
                if source.is_file():
                    return source.resolve()
            except (OSError, ValueError, KeyError):
                pass
        return None

    def sync_clean_source(self, *, force=False):
        text = self.input.get('1.0', 'end').strip()
        if not force and text == self._clean_input:
            return
        self._clean_input = text
        from teodub.subtitle_clean_profiles import load_profile
        self.clean_options = dict(enabled=False)
        try:
            source = self.clean_source()
            if source:
                self.clean_options = load_profile(self.data_dir, source)
            name = source.name if source else 'video này'
            if self.clean_options.get('region'):
                state = 'Đã bật GPU' if self.clean_options['enabled'] else 'Đã lưu vùng; tick ô bên trên để áp dụng'
                self.clean_summary.set(f'{state} — {name}')
            else:
                self.clean_summary.set(f'Chưa lưu vùng xóa cho {name}. Tải + dịch Sub → lưu vùng → tick → lồng tiếng.')
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.clean_summary.set('Chưa dùng được vùng đã lưu: '+str(exc))
        self.clean_enabled.set(self.clean_options.get('enabled', False))

    def toggle_clean(self):
        from teodub.subtitle_clean_profiles import set_enabled
        try:
            source = self.clean_source()
            if not source:
                raise ValueError('Tải video và dịch Sub trước, rồi lưu vùng xóa cho video đó.')
            self.clean_options = set_enabled(self.data_dir, source, self.clean_enabled.get())
            self.sync_clean_source(force=True)
            self.save_preferences()
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.clean_enabled.set(False)
            self.clean_options['enabled'] = False
            self.clean_summary.set(str(exc))
            self.status.set('Chưa bật xóa Sub: '+str(exc))

    def clean_selection(self, *, require_bound=False):
        from teodub.subtitle_clean import validate_clean
        if require_bound:
            self.sync_clean_source()
            if self.clean_options.get('enabled'):
                from teodub.subtitle_clean_profiles import load_profile
                source = self.clean_source()
                if not source or not self.clean_options.get('source_sha256'):
                    raise ValueError('Hãy lưu vùng xóa riêng cho video này, rồi tick áp dụng.')
                saved = load_profile(self.data_dir, source, verify_hash=True)
                if saved != self.clean_options:
                    raise ValueError('Vùng xóa đã được thay đổi ở cửa sổ khác. Mở lại vùng xóa để kiểm tra trước khi chạy.')
                self.clean_options = saved
        return validate_clean({'subtitle_clean': self.clean_options})

    def edit_clean(self):
        if self.process is not None or self.closing or self.login_pending or self.campaign_busy or self.voice_learning_busy:
            return
        self.sync_clean_source(force=True)
        if not self.clean_source():
            self.status.set('Bấm Tải video và dịch Sub trước, rồi lưu vùng xóa Sub cho video đã tải.')
            return
        from teodub.subtitle_clean_ui import SubtitleCleanDialog
        SubtitleCleanDialog(self)

    def audio_mix_selection(self):
        return dict(original_gain=validate_percent(self.fields['original_volume'].get(), 'Tiếng video gốc'),
                    voice_gain=validate_percent(self.fields['voice_volume'].get(), 'Giọng lồng tiếng'))

    def fill_audio_mix(self, config):
        levels = validate_audio_mix(config)
        self.fields['original_volume'].set(f"{levels['original_gain']*100:g}")
        self.fields['voice_volume'].set(f"{levels['voice_gain']*100:g}")

    def drive_selection(self):
        from teodub.google_drive import normalize_drive_config
        options = dict(self.drive_options, enabled=self.fields['drive_enabled'].get())
        return normalize_drive_config(options)

    def fill_drive(self, config):
        from teodub.google_drive import normalize_drive_config
        self.drive_options = normalize_drive_config(config)
        self.fields['drive_enabled'].set(self.drive_options['enabled'])
        self.update_drive_summary()

    def update_drive_summary(self, *_args):
        enabled = self.fields['drive_enabled'].get()
        if not enabled:
            message = 'Drive: chưa bật tự lưu'
        elif self.drive_options.get('mode') == 'sync_folder' and self.drive_options.get('folder_path'):
            message = 'Qua thư mục: '+self.drive_options['folder_path']+' · Drive tự đồng bộ'
        elif self.drive_options.get('account_id') and self.drive_options.get('folder_id'):
            message = 'Drive: '+(self.drive_options.get('folder_name') or 'Thư mục đã chọn')
        else:
            message = 'Chọn thư mục Drive để thiết lập tự lưu trước.'
        self.drive_summary.set(message)

    def choose_drive(self):
        from teodub.drive_folder_ui import configure_drive_folder
        initial = dict(self.drive_options, enabled=self.fields['drive_enabled'].get())

        def save(options):
            if self.closed or self.closing:
                return
            self.fill_drive(options)
            self.save_preferences()
        return configure_drive_folder(self, initial, save, advanced=self.choose_drive_api)

    def choose_drive_api(self):
        from teodub.google_drive_ui import configure_google_drive
        initial = dict(self.drive_options, enabled=self.fields['drive_enabled'].get())
        if initial.get('mode') == 'sync_folder':
            initial = dict(version=1, enabled=False, account_id='', folder_id='', folder_name='')
        if not initial.get('account_id') or not initial.get('folder_id'):
            initial['enabled'] = False

        def save(options):
            if self.closed or self.closing:
                return
            self.fill_drive(options)
            self.save_preferences()
        return configure_google_drive(self, self.data_dir, initial, save)

    @staticmethod
    def wording_from_text(enabled, text):
        return validate_wording({'wording': dict(version='vi-wording-v1', enabled=enabled,
                                                 rules=parse_rule_text(text))})

    def wording_selection(self):
        return self.wording_from_text(self.fields['wording_enabled'].get(), self.fields['wording_rules'].get())

    def fill_wording(self, config):
        wording = validate_wording(config)
        self.fields['wording_enabled'].set(wording['enabled'])
        self.fields['wording_rules'].set(format_rule_text(wording['rules']))

    def update_wording_summary(self, *_args):
        enabled = self.fields['wording_enabled'].get()
        count = sum(bool(line.strip()) for line in self.fields['wording_rules'].get().splitlines())
        self.wording_summary.set(f'Từ ngữ: Bật ({count} quy tắc)' if enabled else 'Từ ngữ: Tắt')

    def apply_wording_selection(self, enabled, text):
        if self.process is not None or self.closing or self.login_pending or self.campaign_busy or self.voice_learning_busy:
            return None
        wording = self.wording_from_text(enabled, text)
        self.fill_wording({'wording': wording})
        self.save_preferences()
        self.status.set('Đã lưu Từ ngữ cho video và chiến dịch mới. Tiếp tục phiên trước giữ cấu hình đã lưu.')
        return wording

    def choose_wording(self):
        if self.process is not None or self.closing or self.login_pending or self.campaign_busy or self.voice_learning_busy:
            return
        window = tk.Toplevel(self.window)
        window.title('Từ ngữ cho Sub Việt và lời đọc')
        window.transient(self.window)
        window.resizable(False, False)
        frame = ttk.Frame(window, padding=18)
        frame.pack(fill='both', expand=True)
        ttk.Label(frame, text='Từ ngữ cho phiên mới', font=('Segoe UI', 15, 'bold')).pack(anchor='w')
        enabled = tk.BooleanVar(window, value=self.fields['wording_enabled'].get())
        ttk.Checkbutton(frame, text='Bật bảng thay từ cho Sub Việt và lời đọc', variable=enabled).pack(
            anchor='w', pady=(10, 6))
        ttk.Label(frame, wraplength=660, text='Áp dụng cả bản dịch AI và SRT Việt có sẵn. '
                  'Bản gốc được giữ để đối chiếu. Chỉ thay từ/cụm khớp trọn vẹn, không thay bên trong từ khác. '
                  'Cụm như bán kính, bán dẫn, giá trị được giữ khi thay từ đơn; muốn thay hãy nhập cả cụm.').pack(anchor='w')
        ttk.Label(frame, text='Mỗi dòng: từ/cụm nguồn => từ/cụm thay thế. Ví dụ: bán => báng. Tối đa 50 dòng.',
                  wraplength=660).pack(anchor='w', pady=(10, 4))
        table = tk.Text(frame, height=6, width=79, wrap='word', font=('Segoe UI', 10), relief='solid', borderwidth=1)
        table.insert('1.0', self.fields['wording_rules'].get())
        table.pack(fill='x')
        ttk.Label(frame, wraplength=660, text='Đổi chữ có thể làm giọng đọc phát âm khác. '
                  'Tính năng này không bảo đảm tương tác hoặc bản quyền; hãy nghe và kiểm tra trước khi đăng.').pack(
            anchor='w', pady=(8, 8))
        ttk.Label(frame, text='Câu để xem thử (bạn có thể sửa, không gọi AI):').pack(anchor='w')
        sample = tk.Text(frame, height=3, width=79, wrap='word', font=('Segoe UI', 10), relief='solid', borderwidth=1)
        sample.insert('1.0', 'Cửa hàng bán chiếc đèn này với giá 199 nghìn đồng.\n'
                            'Bạn có thể mua hai chiếc và hỏi giá trước.\n'
                            'Bán hàng cần nói rõ số lượng.')
        sample.pack(fill='x', pady=(4, 5))
        preview = tk.Text(frame, height=3, width=79, wrap='word', font=('Segoe UI', 10),
                          relief='solid', borderwidth=1, state='disabled')
        preview.pack(fill='x')
        note = tk.StringVar(window)
        ttk.Label(frame, textvariable=note, wraplength=660).pack(anchor='w', pady=(5, 4))
        buttons = ttk.Frame(frame)
        buttons.pack(fill='x', pady=(4, 0))

        def show_preview():
            try:
                wording = self.wording_from_text(enabled.get(), table.get('1.0', 'end').strip())
                original = sample.get('1.0', 'end').strip()
                rewritten, changes = rewrite_text(original, wording['rules']) if wording['enabled'] else (original, [])
                preview.configure(state='normal')
                preview.delete('1.0', 'end')
                preview.insert('1.0', rewritten)
                preview.configure(state='disabled')
                note.set(f'Đã thay {len(changes)} vị trí trong câu thử.' if wording['enabled'] else
                         'Đang tắt: Sub Việt và lời đọc giữ nguyên từ ngữ.')
            except (ValueError, TypeError) as exc:
                note.set(str(exc))

        def save():
            try:
                if self.apply_wording_selection(enabled.get(), table.get('1.0', 'end').strip()) is not None:
                    window.destroy()
                else:
                    note.set('Ứng dụng đang xử lý. Đợi xong rồi lưu Từ ngữ cho phiên mới.')
            except (ValueError, TypeError, OSError) as exc:
                note.set(str(exc))

        ttk.Button(buttons, text='Xem thử', command=show_preview).pack(side='left')
        ttk.Button(buttons, text='Lưu lựa chọn', command=save).pack(side='right')
        ttk.Button(buttons, text='Hủy', command=window.destroy).pack(side='right', padx=(0, 8))
        show_preview()
        window.grab_set()

    def choose_models(self):
        if self.process is not None or self.closing or self.login_pending:
            return
        window = tk.Toplevel(self.window)
        window.title('Chọn mô hình AI cho từng bước')
        window.transient(self.window)
        window.resizable(False, False)
        frame = ttk.Frame(window, padding=18)
        frame.pack(fill='both', expand=True)
        ttk.Label(frame, text='Mô hình cho phiên mới', font=('Segoe UI', 15, 'bold')).grid(row=0, column=0, columnspan=2, sticky='w')
        descriptions = {
            'asr_model': ('1. Nhận dạng lời nói', 'Chạy miễn phí trên máy. Tự động chọn Small/Medium/Large v3 theo bộ nhớ GPU và chế độ được hỗ trợ; dùng Medium trên CPU khi cần. Phiên cũ giữ mô hình đã chốt. Lần đầu có thể tải mô hình.'),
            'translation_model': ('2. Dịch Trung → Việt', 'Dùng tài khoản Codex. Gợi ý sát nghĩa, văn nói tự nhiên: GPT-6.1 Sol + suy luận Vừa. Luna ưu tiên hạn mức; Astra cho nội dung khó. Vẫn có Sol/Terra/Luna 5.6. Khả năng dùng phụ thuộc tài khoản; không tự đổi model khi lỗi. Nguồn nhận dạng sai vẫn cần duyệt.'),
            'translation_effort': ('Mức suy luận khi dịch', 'Thấp giảm phần suy luận; không làm giảm số từ nguồn gửi dịch. Hạn mức thực tế phụ thuộc model và nội dung.'),
            'tts_model': ('3. Tạo giọng trên máy', 'Turbo: 48 kHz, bộ giọng hiện tại. Nano: 24 kHz, nhẹ hơn, bộ giọng riêng; vẫn hỗ trợ hai giọng theo mẫu. Lần đầu tải mô hình. Lựa chọn này áp dụng bộ giọng Trên máy.'),
        }
        variables = {}
        for index, (key, choices) in enumerate(CHOICES.items()):
            row = 1 + index*2
            label, description = descriptions[key]
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky='w', padx=(0, 14), pady=(12, 3))
            variables[key] = tk.StringVar(window, value=choices[self.fields[key].get()])
            picker = ttk.Combobox(frame, textvariable=variables[key], values=list(choices.values()), state='readonly', width=43)
            picker.grid(row=row, column=1, sticky='ew', pady=(12, 3))
            if key == 'tts_model' and self.fields['voice_provider'].get() != 'local':
                picker.configure(state='disabled')
            ttk.Label(frame, text=description, wraplength=635).grid(row=row+1, column=0, columnspan=2, sticky='w')
        ttk.Label(frame, text='Có SRT Việt: bỏ qua nhận dạng và dịch. Tiếp tục phiên trước luôn giữ model đã lưu.', wraplength=635).grid(row=9, column=0, columnspan=2, sticky='w', pady=(16, 8))
        def apply():
            selected = {key: next(value for value, label in CHOICES[key].items() if label == variable.get())
                        for key, variable in variables.items()}
            self.apply_model_selection(selected)
            window.destroy()
        ttk.Button(frame, text='Lưu lựa chọn', command=apply).grid(row=10, column=1, sticky='e')
        window.grab_set()

    def apply_model_selection(self, config):
        if self.process is not None or self.closing or self.login_pending:
            return
        values = validate_models(config)
        previous_voice = self.fields['voice'].get()
        self.stop_playback(refresh=False)
        self.setting_profile = True
        try:
            for key, value in values.items():
                self.fields[key].set(value)
        finally:
            self.setting_profile = False
        self.update_profile_controls()
        changed_voice = self.fields['voice'].get() != previous_voice
        self.refresh_buttons()
        self.save_preferences()
        self.status.set(('Giọng trước không có ở model này; đang chọn '+self.fields['voice'].get()+'. Hãy nghe thử hoặc chọn giọng khác.'
                         if changed_voice else 'Đã lưu mô hình AI cho phiên mới. Tiếp tục phiên trước giữ nguyên cấu hình cũ.'))

    def help(self):
        messagebox.showinfo('Hướng dẫn Tèo Dub Studio',
            '• Làm từng bước: Tải video → Tải video và dịch Sub → Xem / sửa lời thoại → Lồng tiếng và xuất video.\n'
            '• Có thể bấm Tải video và dịch Sub ngay sau khi dán link. Video đã tải được dùng lại.\n'
            '• Tự động toàn bộ dịch lời mới cho video đang chọn, bỏ qua ô SRT có sẵn. Nút xem lời không tự gọi AI.\n'
            '• Có Internet để tải mô hình lần đầu và dịch bằng Codex.\n'
            '• Cài Codex CLI trên máy và đăng nhập bằng tài khoản của bạn.\n'
            '• Dịch dùng hạn mức Codex; không cần API key.\n'
            '• Nhận dạng và giọng đọc chạy trên máy. Lần đầu có thể cần chờ tải mô hình.\n'
            '• Bấm Mô hình AI để chọn riêng Whisper, model dịch Codex, mức suy luận và VieNeu Turbo/Nano. '
            'Nhận dạng và giọng chạy trên máy không tốn token Codex. Nano dùng bộ giọng riêng và hỗ trợ giọng theo mẫu.\n'
            '• Giọng theo mẫu miễn phí trên máy, chỉ mô phỏng gần giống. Chọn mẫu rõ tiếng dài 3–8 giây.\n'
            '• Kiểu đọc: Trò chuyện tự nhiên, Kể chuyện chậm rãi hoặc Quảng cáo năng động. '
            'Các kiểu thay đổi nhịp đọc, ngắt nghỉ và nối các đoạn câu; dùng được với mọi bộ giọng. '
            'Cảm xúc còn tùy giọng và mẫu, không tự thêm tiếng cười. Theo bản cũ giữ cách xử lý trước đây.\n'
            '• Tốc độ giọng: 1,00×, 1,05×, 1,10× hoặc 1,20×; nhân thêm vào nhịp của Kiểu đọc. '
            'Tool rút ngắn âm thanh nhưng giữ cao độ. Tốc độ hình là lựa chọn riêng.\n'
            '• CapCut trực tuyến đang thử nghiệm: mở đăng nhập thủ công, rồi đóng cửa sổ đó trước khi thử giọng. '
            'Khả năng dùng giọng và điều kiện miễn phí phụ thuộc tài khoản/website; chưa xác nhận mọi giọng hoạt động.\n'
            '• Nếu có SRT Việt, chọn tệp rồi bấm Lồng tiếng và xuất video để dùng nguyên lời, bỏ qua bước dịch.\n'
            '• Ghi Sub vào hình: chọn Trắng/Đen/Vàng và Chữ đậm/Chữ nhạt. Khi tắt, phụ đề trong MP4 có thể bật/tắt trên trình phát hỗ trợ. Luôn có một SRT Việt riêng; chữ dính sẵn trong hình gốc vẫn còn.\n'
            '• Tiếp tục dùng lại cấu hình và các phần hoàn tất của phiên trước.', parent=self.window)

    def local_voices(self, model=None):
        return (*model_voices(model or self.fields['tts_model'].get()),
                *(label for label, path in REFERENCE_ALIASES.items() if path.is_file()),
                *self.learned_profiles, ADAM_VOICE, REFERENCE_VOICE)

    def reference_aliases(self):
        return {**REFERENCE_ALIASES, **({ADAM_VOICE: self.adam_reference} if self.adam_reference else {}),
                **{label: Path(p['reference_audio']) for label, p in self.learned_profiles.items()}}

    def refresh_learned_voices(self, *, update=True):
        from teodub.voice_learning import list_profiles, profile_label
        profiles, _errors = list_profiles(self.data_dir)
        self.learned_profiles = {profile_label(p): p for p in profiles}
        if update:
            # Refresh choices without silently replacing the current selection.
            values = CAPCUT_VOICES if self.fields['voice_provider'].get() == 'capcut' else self.local_voices()
            self.voice_picker.configure(values=values)

    def apply_learned_voice(self, profile_id, tts_model='v3turbo'):
        # Campaign jobs already carry frozen configurations. Updating the form
        # is safe during a campaign; committing future campaign settings is a
        # separate explicit choice in the voice lab.
        if self.process is not None or self.closed or self.closing or self.restart_pending or self.login_pending or self.voice_learning_busy:
            raise ValueError('Đợi video hiện tại xử lý xong rồi chọn giọng đã học.')
        from teodub.voice_learning import profile_selection
        selection = profile_selection(self.data_dir, profile_id, tts_model)
        self.refresh_learned_voices(update=False)
        self.fill_profile({**{key: value.get() for key, value in self.fields.items() if key in CHOICES}, **selection})
        self.refresh_buttons()
        self.save_preferences()
        self.status.set('Đã chọn giọng học theo mẫu. Phong cách lấy từ mẫu, tốc độ 1,00×; có thể chỉnh tiếp. '
            'Chiến dịch cần bấm Lấy cấu hình từ tab Video đơn để áp dụng từ video tiếp theo.')
        return selection

    def choose_adam(self):
        if self.process is not None or self.closing or self.login_pending or self.campaign_busy or self.voice_learning_busy:
            return
        from teodub.adam_voice import AdamImportDialog
        self.adam_dialog = AdamImportDialog(self.window, self.data_dir, self.apply_adam)

    def apply_adam(self, path):
        if self.process is not None or self.closing or self.login_pending or self.campaign_busy or self.voice_learning_busy:
            raise ValueError('Đợi công việc hiện tại xong rồi chọn ADAM.')
        from teodub.adam_voice import checked_reference
        self.adam_reference = checked_reference(path, self.data_dir)
        self.stop_playback(refresh=False)
        self.setting_profile = True
        try:
            self.fields['voice_provider'].set('local')
            self.fields['voice'].set(ADAM_VOICE)
            self.fields['reference_audio'].set(str(self.adam_reference))
        finally:
            self.setting_profile = False
        self.update_profile_controls()
        self.refresh_buttons()
        self.save_preferences()
        self.status.set('Đã lưu và chọn ADAM theo mẫu. Bấm Nghe thử giọng để kiểm tra trước khi lồng tiếng.')

    def choose_provider(self, _event=None):
        provider = next((key for key, label in VOICE_PROVIDERS.items() if label == self.provider_label.get()), None)
        if provider:
            self.fields['voice_provider'].set(provider)

    def choose_style(self, _event=None):
        style = next((key for key in READING_STYLES if STYLES[key]['label'] == self.style_label.get()), None)
        if style:
            self.fields['reading_style'].set(style)

    def choose_subtitle_color(self, _event=None):
        value = next((key for key, item in SUBTITLE_COLORS.items()
                      if item['label'] == self.subtitle_color_label.get()), None)
        if value:
            self.fields['subtitle_color'].set(value)

    def choose_subtitle_weight(self, _event=None):
        value = next((key for key, item in SUBTITLE_WEIGHTS.items()
                      if item['label'] == self.subtitle_weight_label.get()), None)
        if value:
            self.fields['subtitle_weight'].set(value)

    def subtitle_changed(self, *_args):
        self.update_subtitle_controls()
        self.refresh_buttons()

    def profile_changed(self, *_args):
        if self.setting_profile:
            return
        self.stop_playback(refresh=False)
        self.update_profile_controls()
        self.refresh_buttons()

    def update_profile_controls(self):
        values = {key: self.fields[key].get() for key in CHOICES}
        self.model_summary.set('AI: '+ ' | '.join(CHOICES[key].get(values[key], values[key]) for key in CHOICES))
        reading_style = self.fields['reading_style'].get()
        self.style_label.set(STYLES.get(reading_style, {}).get('label', reading_style))
        provider = self.fields['voice_provider'].get()
        self.provider_label.set(VOICE_PROVIDERS.get(provider, provider))
        voices = CAPCUT_VOICES if provider == 'capcut' else self.local_voices()
        self.voice_picker.configure(values=voices)
        if self.fields['voice'].get() not in voices:
            self.setting_profile = True
            self.fields['voice'].set(voices[0])
            self.setting_profile = False
        custom = provider == 'local' and self.fields['voice'].get() == REFERENCE_VOICE
        if custom:
            self.sample_row.grid()
        else:
            self.sample_row.grid_remove()
        if provider == 'capcut':
            self.login_row.grid()
            self.voice_help.set('CapCut trực tuyến đang thử nghiệm; cần đăng nhập. Giọng và điều kiện miễn phí phụ thuộc tài khoản/website.')
        else:
            self.login_row.grid_remove()
            if self.fields['voice'].get() == ADAM_VOICE:
                self.voice_help.set('ADAM theo mẫu: chạy miễn phí trên máy, chỉ mô phỏng gần giống. '+
                    ('Đã có mẫu; bấm Nghe thử giọng để kiểm tra.' if self.adam_reference and self.adam_reference.is_file()
                     else 'Chưa có mẫu. Bấm Nhập mẫu ADAM để chọn đoạn nói rõ 3–8 giây.'))
            elif self.fields['voice'].get() in ('Adam', 'Adam bựa'):
                self.voice_help.set('Giọng có sẵn của VieNeu · miễn phí trên máy, không cần nhập mẫu. '
                    'Adam: giọng Nam; Adam bựa: giọng Bắc (Turbo). Chọn Turbo để ưu tiên chất lượng. '
                    'Bấm Nghe thử để so sánh; chưa xác nhận giống Adam của ElevenLabs/CapCut.')
            elif self.fields['voice'].get() in self.learned_profiles:
                self.voice_help.set('Giọng đã học theo mẫu trên máy. Phong cách chịu ảnh hưởng đoạn mẫu; '
                    'chưa huấn luyện lại mô hình. Nghe thử trước khi lồng tiếng; Chiến dịch dùng đúng mẫu khi lấy cấu hình Video đơn.')
            elif custom or self.fields['voice'].get() in REFERENCE_ALIASES:
                self.voice_help.set('Giọng theo mẫu chạy miễn phí trên máy, chỉ gần giống mẫu. Mẫu rõ tiếng 3–8 giây; lần đầu tải mô hình.')
            else:
                self.voice_help.set('Giọng đọc miễn phí trên máy. Lần đầu cần tải mô hình; Nghe lại dùng bản thử đã tạo, không tạo giọng lần nữa.')
        self.update_subtitle_controls()

    def update_subtitle_controls(self):
        color = self.fields['subtitle_color'].get()
        weight = self.fields['subtitle_weight'].get()
        self.subtitle_color_label.set(SUBTITLE_COLORS.get(color, {}).get('label', color))
        self.subtitle_weight_label.set(SUBTITLE_WEIGHTS.get(weight, {}).get('label', weight))
        busy = self.process is not None or self.closing or self.login_pending
        state = 'readonly' if self.fields['burn_subtitles'].get() and not busy else 'disabled'
        self.subtitle_color_picker.configure(state=state)
        self.subtitle_weight_picker.configure(state=state)
        self.subtitle_size_picker.configure(state='normal' if state == 'readonly' else 'disabled')

    def choose_reference(self):
        value = filedialog.askopenfilename(parent=self.window, title='Chọn mẫu giọng rõ tiếng 3–8 giây',
            filetypes=[('Âm thanh', '*.wav *.mp3 *.flac *.m4a *.ogg'), ('Tất cả tệp', '*.*')])
        if value:
            self.fields['reference_audio'].set(value)

    def voice_selection(self, *, include_hash=True):
        provider = self.fields['voice_provider'].get()
        voice = self.fields['voice'].get()
        if provider not in VOICE_PROVIDERS:
            raise ValueError('Chọn một bộ giọng có trong danh sách.')
        choices = CAPCUT_VOICES if provider == 'capcut' else self.local_voices()
        if voice not in choices:
            raise ValueError('Chọn một giọng đọc trong danh sách.')
        reading_style = self.fields['reading_style'].get()
        validate_style(reading_style)
        voice_speed = VOICE_SPEED_LABELS.get(self.fields['voice_speed'].get())
        if voice_speed is None:
            raise ValueError('Chọn tốc độ giọng 1,00×, 1,05×, 1,10× hoặc 1,20×.')
        selection = dict(voice_provider=provider, voice=voice, reading_style=reading_style,
                         voice_speed=voice_speed, tts_model=self.fields['tts_model'].get())
        aliases = self.reference_aliases()
        if provider == 'local' and voice == ADAM_VOICE and not self.adam_reference:
            raise ValueError('Chưa có mẫu ADAM. Bấm Nhập mẫu ADAM để chọn đoạn nói rõ 3–8 giây.')
        if provider == 'local' and voice == ADAM_VOICE and include_hash:
            from teodub.adam_voice import checked_reference
            checked_reference(self.adam_reference, self.data_dir)
        if provider == 'local' and voice in self.learned_profiles and include_hash:
            from teodub.voice_learning import checked_profile
            profile = self.learned_profiles[voice]
            verified = checked_profile(self.data_dir, profile['id'])
            if (verified['reference_audio'] != profile['reference_audio'] or
                    verified['reference_sha256'] != profile['reference_sha256']):
                raise ValueError('Hồ sơ giọng học đã thay đổi; chưa chạy với mẫu khác.')
        if provider == 'local' and (voice == REFERENCE_VOICE or voice in aliases):
            raw = str(aliases[voice]) if voice in aliases else self.fields['reference_audio'].get().strip()
            if not raw or not Path(raw).is_absolute():
                raise ValueError('Chọn tệp mẫu giọng dài 3–8 giây trước.')
            path = Path(raw).resolve()
            selection.update(voice=REFERENCE_VOICE, reference_audio=str(path))
            if include_hash:
                if not path.is_file():
                    raise ValueError('Tệp mẫu giọng không còn tồn tại. Hãy chọn lại mẫu.')
                before = path.stat()
                selection['reference_sha256'] = hash_file(path)
                after = path.stat()
                if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                    raise ValueError('Tệp mẫu giọng đang thay đổi. Đợi tệp lưu xong rồi thử lại.')
        return selection

    def login_capcut(self):
        if self.process is not None or self.closing or self.login_pending:
            return
        self.stop_playback(refresh=False)
        self.login_pending = True
        self.status.set('Đang mở cửa sổ CapCut để bạn đăng nhập…')
        self.refresh_buttons()

        def launch_login():
            try:
                open_capcut_login()
                self.login_results.put(None)
            except Exception as exc:
                self.login_results.put(str(exc))
        threading.Thread(target=launch_login, daemon=True).start()

    def choose_input(self):
        value = filedialog.askopenfilename(parent=self.window, title='Chọn video trên máy',
            filetypes=[('Video', '*.mp4 *.mov *.mkv *.webm *.m4v *.avi'), ('Tất cả tệp', '*.*')])
        if value:
            self.input.delete('1.0', 'end')
            self.input.insert('1.0', value)

    def choose(self, key):
        if key == 'subtitles':
            value = filedialog.askopenfilename(parent=self.window, title='Chọn SRT tiếng Việt',
                                               filetypes=[('Phụ đề SRT', '*.srt')])
        else:
            value = filedialog.askdirectory(parent=self.window, title='Chọn thư mục lưu',
                                            initialdir=self.fields[key].get())
        if value:
            self.fields[key].set(value)

    def config_from_form(self, mode, *, use_subtitles=True):
        text = self.input.get('1.0', 'end').strip()
        if mode in ('full', 'captions', 'download') and not text:
            raise ValueError('Dán link hoặc chọn video trên máy trước.')
        if mode in ('captions', 'download'):
            original = self.fields['original_dir'].get().strip()
            if not original or not Path(original).is_absolute():
                raise ValueError('Chọn thư mục lưu video gốc trước.')
            directory = (self.jobs_dir/uuid.uuid4().hex).resolve()
            return dict(mode=mode, directory=str(directory), input=text, subtitles=None,
                        original_dir=str(Path(original).resolve()), output_dir=str(self.data_dir),
                        **(validate_models({key: self.fields[key].get() for key in CHOICES}) if mode == 'captions' else {}),
                        stop_file=str(directory/'stop.request'), execution_lock=str(self.data_dir/'execution'))
        selection = self.voice_selection()
        models = validate_models({key: self.fields[key].get() for key in CHOICES})
        selection.update(models)
        speed = .9 if mode == 'preview' else SPEEDS.get(self.fields['speed'].get())
        if speed is None:
            raise ValueError('Chọn tốc độ hình 0,9× hoặc 1,0×.')
        folders = {}
        for key in ('original_dir', 'output_dir'):
            value = self.fields[key].get().strip()
            if mode == 'preview':
                folders[key] = str(self.data_dir)
                continue
            if not value or not Path(value).is_absolute():
                raise ValueError('Chọn đầy đủ thư mục video gốc và video lồng tiếng.')
            folders[key] = str(Path(value).resolve())
        # The automatic flow generates dialogue for this source. Only the
        # explicit dubbing step may import a selected/edited SRT and its binding.
        subtitles = self.fields['subtitles'].get().strip() if use_subtitles else ''
        if mode == 'full' and subtitles and (not Path(subtitles).is_file() or Path(subtitles).suffix.lower() != '.srt'):
            raise ValueError('Tệp SRT Việt không tồn tại hoặc không đúng định dạng.')
        subtitle_options = validate_subtitle_style({key: self.fields[key].get() for key in
                                                    ('burn_subtitles', 'subtitle_color', 'subtitle_weight', 'subtitle_size')})
        directory = (self.jobs_dir/uuid.uuid4().hex).resolve()
        audio_options = self.audio_mix_selection() if mode == 'full' else validate_audio_mix({})
        clean_options = {'subtitle_clean': self.clean_selection(require_bound=True)} if mode == 'full' else {}
        wording_options = {'wording': self.wording_selection()} if mode == 'full' else {}
        drive_options = ({'drive_upload': self.drive_selection(), 'drive_data_dir': str(self.data_dir)}
                         if mode == 'full' else {})
        dialogue_options = {}
        if mode == 'full' and subtitles:
            from teodub.dialogue_editor import draft_binding
            dialogue_options = dict(subtitles_sha256=hash_file(subtitles),
                                    **draft_binding(subtitles, self.data_dir))
        return dict(mode=mode, directory=str(directory), input=text, **folders, **selection,
                    subtitles=str(Path(subtitles).resolve()) if subtitles and mode == 'full' else None,
                    speed=speed, **audio_options, **subtitle_options, **wording_options, **drive_options, **dialogue_options, **clean_options,
                    stop_file=str(directory/'stop.request'), execution_lock=str(self.data_dir/'execution'))

    def edit_dialogue(self):
        if self.process is not None or self.closing or self.login_pending or self.campaign_busy or self.voice_learning_busy:
            return
        try:
            from teodub.dialogue_editor import DialogueEditor
            selected = self.fields['subtitles'].get().strip()
            source = None
            if not selected and self.current and self.current.get('mode') in ('full', 'captions'):
                directory = Path(self.current['directory'])
                state_path = directory/'state.json'
                state = json.loads(state_path.read_text(encoding='utf-8')) if state_path.exists() else {}
                original = state.get('source') or {}
                text = self.input.get('1.0', 'end').strip()
                if text in (self.current.get('input'), original.get('path')) and state.get('captions'):
                    record = state['captions']
                    if hash_file(record['path']) != record['sha256']:
                        raise ValueError('Bản lời của phiên đã thay đổi. Chọn SRT Việt để mở một bản riêng.')
                    selected, source = record['path'], original
            if not selected:
                self.status.set('Chưa có lời Việt. Bấm Tải video và dịch Sub, hoặc chọn SRT Việt có sẵn.')
                return

            def saved(path):
                if self.process is not None or self.campaign_busy or self.closing:
                    raise ValueError('Tèo đang chạy. Đợi công việc xong rồi lưu lời thoại.')
                self.fields['subtitles'].set(str(path))
                if source:
                    self.input.delete('1.0', 'end')
                    self.input.insert('1.0', source['path'])
                self.save_preferences()
                self.status.set('Đã chọn bản lời vừa sửa. Bấm Lồng tiếng và xuất video; không dịch lại bản này.')

            self.dialogue_editor = DialogueEditor(self.window, selected, self.data_dir, saved,
                                                 source_sha256=(source or {}).get('sha256'))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.status.set('Chưa mở được lời thoại: '+str(exc))

    def use_prepared_result(self):
        """Select checked local artifacts; never start the next worker automatically."""
        if not self.current or not (self.current.get('mode') in ('download', 'captions') or
                                   self.current.get('mode') == 'full' and not self.current.get('subtitles')):
            return
        state = json.loads((Path(self.current['directory'])/'state.json').read_text(encoding='utf-8'))
        source = state['source']
        if not Path(source['path']).is_file():
            raise ValueError('Video đã chuẩn bị không còn ở nơi lưu. Chọn lại video trước khi tiếp tục.')
        subtitles = ''
        if self.current['mode'] in ('captions', 'full'):
            record = state['captions']
            if hash_file(record['path']) != record['sha256']:
                raise ValueError('Bản lời đã chuẩn bị bị thay đổi. Chưa tự chọn bản này.')
            subtitles = record['path']
        self.input.delete('1.0', 'end')
        self.input.insert('1.0', source['path'])
        self.fields['subtitles'].set(subtitles)
        self.sync_clean_source(force=True)
        if self.current['mode'] == 'captions':
            self.status.set('Đã tải video và dịch Sub. Tiếp theo: Lưu vùng xóa Sub gốc → tick áp dụng → Lồng tiếng và xuất video.')

    def dub_prepared(self):
        self.start('full', require_subtitles=True)

    def confirm_clean_choice(self):
        """Do not silently skip a saved removal region when starting a fresh export."""
        self.sync_clean_source()
        if self.clean_options.get('region') and not self.clean_options.get('enabled'):
            answer = messagebox.askyesnocancel(
                'Vùng xóa Sub đã lưu nhưng đang TẮT',
                'Video này đã có vùng xóa Sub gốc, nhưng xử lý GPU đang tắt.\n\n'
                'Có: bật GPU xóa Sub gốc rồi mới chèn Sub Việt.\n'
                'Không: giữ chữ gốc trong video lần này.\n'
                'Hủy: quay lại kiểm tra vùng đã khoanh.', parent=self.window, default='yes')
            if answer is None:
                self.status.set('Chưa xuất video. Hãy kiểm tra vùng xóa Sub gốc.')
                return False
            if answer:
                self.clean_enabled.set(True)
                self.toggle_clean()
                if not self.clean_options.get('enabled'):
                    raise ValueError(self.clean_summary.get())
        return True

    def start(self, mode='full', *, require_subtitles=False):
        if self.process is not None or self.closing or self.login_pending or self.campaign_busy or self.voice_learning_busy:
            return
        try:
            if require_subtitles and not self.fields['subtitles'].get().strip():
                raise ValueError('Bấm Tải video và dịch Sub hoặc chọn SRT Việt trước, rồi mới lồng tiếng.')
            if mode == 'full' and not self.confirm_clean_choice():
                return
            automatic = mode == 'full' and not require_subtitles
            config = self.config_from_form(mode, use_subtitles=not automatic)
            if require_subtitles:
                config['require_subtitles'] = True
            self.launch(config, fresh=True)
            if automatic:
                self.fields['subtitles'].set('')
                self.save_preferences()
        except (ValueError, OSError, KeyError, TypeError) as exc:
            self.status.set('Chưa chạy: ' + str(exc))

    def owned_config(self, directory):
        directory = Path(directory).resolve()
        if not directory.is_relative_to(self.jobs_dir.resolve()) or directory == self.jobs_dir.resolve():
            raise ValueError('Phiên xử lý không nằm trong thư mục dữ liệu của ứng dụng.')
        config = json.loads((directory/'config.json').read_text(encoding='utf-8'))
        if (not isinstance(config, dict) or Path(config.get('directory', '')).resolve() != directory
                or Path(config.get('stop_file', '')).resolve() != directory/'stop.request'):
            raise ValueError('Cấu hình phiên trước không khớp thư mục xử lý.')
        return config

    def resume(self):
        if self.process is not None or self.closing or self.login_pending or not self.current or self.campaign_busy or self.voice_learning_busy:
            return
        try:
            config = self.owned_config(self.current['directory'])
            if config != self.current:
                raise ValueError('Cấu hình phiên trước đã thay đổi. Chưa chạy lại.')
            self.fill_form(config)
            self.launch(config, fresh=False)
        except (ValueError, OSError, KeyError, TypeError) as exc:
            self.status.set('Chưa thể tiếp tục: ' + str(exc))

    def launch(self, config, *, fresh):
        self.stop_playback(refresh=False)
        self.save_preferences()
        directory = Path(config['directory'])
        command = worker_command(directory/'config.json')
        if fresh:
            directory.mkdir(parents=True, exist_ok=False)
            atomic_json(directory/'config.json', config)
        else:
            # Only remove the checked stop marker belonging to this job.
            Path(config['stop_file']).unlink(missing_ok=True)
        previous = self.current
        events = directory/'events.jsonl'
        event_offset = events.stat().st_size if not fresh and events.exists() else 0
        try:
            atomic_json(self.data_dir/'last-job.json', dict(directory=str(directory)))
            with (directory/'worker.log').open('ab') as output:
                worker = subprocess.Popen(command, cwd=str(directory), stdout=output, stderr=output,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except OSError:
            if previous:
                atomic_json(self.data_dir/'last-job.json', dict(directory=previous['directory']))
            raise
        self.current = dict(config)
        self.process = worker
        self.result = None
        self.stop_started = None
        self.terminated_at = None
        self.autoplay_preview = config.get('mode') == 'preview'
        if self.autoplay_preview:
            self.preview_path = None
            self.preview_config = None
        self.event_pending = b''
        self.logged_stage = None
        self.last_event_message = None
        self.event_offset = event_offset
        self.progress.set(0)
        self.log.configure(state='normal')
        self.log.delete('1.0', 'end')
        self.log.configure(state='disabled')
        self.status.set('Đang chuẩn bị giọng thử…' if config.get('mode') == 'preview' else
                        'Đang tải / kiểm tra video gốc; chưa dịch hoặc tạo giọng.' if config.get('mode') == 'download' else
                        'Đang chuẩn bị lời Việt để bạn chỉnh sửa; chưa tạo giọng.' if config.get('mode') == 'captions' else
                        'Đang xử lý nền. Bạn có thể tiếp tục dùng máy.')
        self.append('Bắt đầu phiên mới.' if fresh else 'Tiếp tục cấu hình cũ; kiểm tra và dùng lại phần đã hoàn tất.')
        self.refresh_buttons()

    @staticmethod
    def profile_key(config):
        reference = config.get('reference_audio')
        return (config.get('voice_provider', 'local'), config.get('voice'),
                str(Path(reference).resolve()) if reference else None, config.get('reading_style', 'legacy'),
                config.get('voice_speed', 1.0), config.get('tts_model', 'v3turbo'))

    def matching_preview(self):
        if not self.preview_path or not self.preview_config or not self.preview_path.is_file():
            return False
        try:
            return self.profile_key(self.voice_selection(include_hash=False)) == self.profile_key(self.preview_config)
        except (ValueError, OSError):
            return False

    def remember_preview(self):
        if not self.current or self.current.get('mode') != 'preview' or (self.result or {}).get('status') not in FINISHED:
            return
        audio = ((self.result or {}).get('outputs') or {}).get('audio')
        expected = Path(self.current['directory'])/'preview.wav'
        if audio and Path(audio).resolve() == expected.resolve() and expected.is_file():
            self.preview_path = expected.resolve()
            self.preview_config = dict(self.current)

    def play_preview(self):
        if self.process is not None or self.closing or self.login_pending or not self.matching_preview():
            return
        try:
            # Replaying must not silently audition a different version of a sample.
            selection = self.voice_selection()
            if selection.get('reference_sha256') != self.preview_config.get('reference_sha256'):
                raise ValueError('Tệp mẫu đã thay đổi. Bấm Nghe thử giọng để tạo lại.')
            self.stop_playback(refresh=False)
            winsound.PlaySound(str(self.preview_path), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
            self.playing = True
            try:
                with wave.open(str(self.preview_path), 'rb') as audio:
                    milliseconds = max(1, round(1000*audio.getnframes()/audio.getframerate()))
            except (OSError, ValueError, EOFError, wave.Error):
                milliseconds = 10000
            self.playback_timer = self.window.after(milliseconds+100, self.playback_finished)
        except (OSError, ValueError, RuntimeError) as exc:
            self.status.set('Chưa nghe được giọng thử: ' + str(exc))
        self.refresh_buttons()

    def playback_finished(self):
        self.playback_timer = None
        self.playing = False
        self.refresh_buttons()

    def stop_playback(self, *, refresh=True):
        if self.playback_timer is not None:
            self.window.after_cancel(self.playback_timer)
            self.playback_timer = None
        if self.playing:
            try:
                winsound.PlaySound(None, 0)
            except RuntimeError:
                pass
            self.playing = False
        if refresh:
            self.refresh_buttons()

    def stop(self):
        self.stop_playback(refresh=False)
        if self.process is None or not self.current:
            return
        try:
            Path(self.current['stop_file']).touch()
            if self.stop_started is None:
                self.stop_started = time.monotonic()
            self.autoplay_preview = False
            self.status.set('Đang dừng; giữ các phần đã hoàn tất để tiếp tục.')
            self.stop_button.configure(state='disabled')
        except OSError as exc:
            self.status.set('Chưa gửi được yêu cầu dừng: ' + str(exc))

    def append(self, message):
        text = str(message).replace('\r', '').strip()
        if not text:
            return
        self.log.configure(state='normal')
        self.log.insert('end', text[:3000]+'\n')
        lines = int(self.log.index('end-1c').split('.')[0])
        if lines > MAX_LOG_LINES:
            self.log.delete('1.0', f'{lines-MAX_LOG_LINES}.0')
        self.log.see('end')
        self.log.configure(state='disabled')

    def read_events(self):
        if not self.current:
            return
        path = Path(self.current['directory'])/'events.jsonl'
        try:
            with path.open('rb') as stream:
                if path.stat().st_size < self.event_offset:
                    self.event_offset = 0
                    self.event_pending = b''
                stream.seek(self.event_offset)
                raw = stream.read(65536)
                self.event_offset = stream.tell()
        except OSError:
            return
        lines = (self.event_pending+raw).split(b'\n')
        self.event_pending = lines.pop()
        if len(self.event_pending) > 65536:
            self.event_pending = b''
        for line in lines:
            try:
                event = json.loads(line.decode('utf-8'))
                if not isinstance(event, dict):
                    continue
                message = event.get('message')
                percent = event.get('percent')
                numeric_progress = isinstance(percent, (int, float)) and not isinstance(percent, bool)
                if message:
                    stage = event.get('stage') or ('progress' if numeric_progress else None)
                    terminal = (event.get('status') in FINISHED | {'failed', 'cancelled', 'error'} or
                                event.get('event') in {'error', 'failed', 'cancelled', 'completed'} or
                                event.get('level') == 'error')
                    # Percentage ticks replace the status line. The bounded log
                    # retains stage changes/errors, not every download chunk.
                    if (terminal or stage != self.logged_stage or stage is None) and message != self.last_event_message:
                        self.append(message)
                        self.last_event_message = message
                    self.logged_stage = stage
                    if not self.closing and self.stop_started is None:
                        self.status.set(str(message)[:500])
                if numeric_progress:
                    self.progress.set(max(0, min(100, percent)))
            except (ValueError, UnicodeError, TypeError):
                continue

    def read_result(self):
        try:
            value = json.loads((Path(self.current['directory'])/'result.json').read_text(encoding='utf-8'))
            return value if isinstance(value, dict) else None
        except (OSError, ValueError, TypeError):
            return None

    def complete(self):
        self.read_events()
        self.result = self.read_result()
        self.process = None
        result = self.result or {}
        status = result.get('status')
        message = result.get('message') or ('Đã hoàn tất. Hãy nghe duyệt bản dịch và giọng đọc.' if status in FINISHED
                  else 'Phiên đã dừng. Chưa có báo cáo hoàn tất; xem nhật ký hoặc bấm Tiếp tục.')
        delivery = result.get('google_drive') or {}
        if delivery.get('message'):
            message += '\nGoogle Drive: '+delivery['message']
        self.status.set(str(message))
        self.append(message)
        if status in FINISHED:
            self.progress.set(100)
        self.remember_preview()
        if self.autoplay_preview and not self.closing and status in FINISHED:
            self.play_preview()
        self.autoplay_preview = False
        self.refresh_buttons()
        if status == 'completed' and not self.closing and (
                self.current.get('mode') in ('download', 'captions') or
                self.current.get('mode') == 'full' and not self.current.get('subtitles')):
            try:
                self.use_prepared_result()
                self.save_preferences()
            except (OSError, ValueError, KeyError, TypeError) as exc:
                self.status.set('Đã xử lý xong nhưng chưa chọn được video / lời thoại mới: '+str(exc))

    def poll(self):
        if self.closed:
            return
        if self.process is None and not self.closing:
            self.sync_clean_source()
        try:
            login_error = self.login_results.get_nowait()
        except queue.Empty:
            pass
        else:
            self.login_pending = False
            message = ('Chưa mở được CapCut: ' + login_error if login_error else
                       'Đã mở CapCut. Đăng nhập thủ công rồi đóng cửa sổ đó trước khi nghe thử; chưa xác nhận giọng dùng được.')
            self.status.set(message)
            self.append(message)
            self.refresh_buttons()
        if self.process is not None:
            self.read_events()
            if self.process.poll() is not None:
                self.complete()
            elif self.closing or self.stop_started is not None:
                now = time.monotonic()
                deadline = self.close_started + 5 if self.closing else self.stop_started + 8
                if self.stop_started is not None:
                    deadline = min(deadline, self.stop_started + 8)
                if now >= deadline and self.terminated_at is None:
                    try:
                        self.process.terminate()
                    except OSError:
                        pass  # The worker may finish between poll and terminate.
                    self.terminated_at = now
                elif self.terminated_at is not None and now-self.terminated_at >= 2:
                    try:
                        self.process.kill()
                    except OSError:
                        pass
        if self.closing and self.process is None:
            self.finish_close()
            return
        self.timer = self.window.after(300, self.poll)

    def fill_profile(self, config):
        models = validate_models(config)
        provider = config.get('voice_provider', 'local')
        voice = config.get('voice', VOICES[0])
        reference = config.get('reference_audio') or ''
        reading_style = config.get('reading_style', 'legacy')
        validate_style(reading_style)
        voice_speed = validate_voice_speed(config.get('voice_speed', 1.0))
        voice_speed_label = next(label for label, value in VOICE_SPEED_LABELS.items() if value == voice_speed)
        if provider not in VOICE_PROVIDERS:
            raise ValueError('Bộ giọng trong phiên trước chưa được hỗ trợ.')
        if provider == 'local' and voice == REFERENCE_VOICE and reference:
            from teodub.adam_voice import library_root
            if Path(reference).resolve().is_relative_to(library_root(self.data_dir)):
                # Preserve the requested identity even when a sample is broken.
                # New work validates it; resume keeps the original frozen hash.
                self.adam_reference = Path(reference).resolve()
                voice = ADAM_VOICE
            else:
                voice = next((label for label, path in self.reference_aliases().items()
                              if path.is_file() and path.resolve() == Path(reference).resolve()), voice)
        valid = CAPCUT_VOICES if provider == 'capcut' else self.local_voices(models['tts_model'])
        if voice not in valid:
            raise ValueError('Giọng của phiên trước không còn trong danh sách; chưa tự đổi sang giọng khác.')
        self.stop_playback(refresh=False)
        self.setting_profile = True
        try:
            for key, value in models.items():
                self.fields[key].set(value)
            self.fields['voice_provider'].set(provider)
            self.fields['voice'].set(voice)
            self.fields['reference_audio'].set(reference if provider == 'local' else '')
            self.fields['reading_style'].set(reading_style)
            self.fields['voice_speed'].set(voice_speed_label)
        finally:
            self.setting_profile = False
        self.update_profile_controls()

    def fill_form(self, config):
        if config.get('mode') in ('captions', 'download'):
            self.input.delete('1.0', 'end')
            self.input.insert('1.0', str(config.get('input', '')))
            self.fields['subtitles'].set('')
            self.fields['original_dir'].set(config['original_dir'])
            return
        self.fill_profile(config)
        subtitle_options = validate_subtitle_style(config)
        for key, value in subtitle_options.items():
            self.fields[key].set(value)
        self.update_subtitle_controls()
        if config.get('mode') == 'preview':
            return
        self.fill_audio_mix(config)
        from teodub.subtitle_clean import validate_clean
        self.clean_options = validate_clean(config)
        self.fill_wording(config)
        self.fill_drive(config)
        self.input.delete('1.0', 'end')
        self.input.insert('1.0', str(config.get('input', '')))
        for key in ('original_dir', 'output_dir', 'subtitles'):
            if config.get(key) is not None:
                self.fields[key].set(config[key])
            elif key == 'subtitles':
                self.fields[key].set('')
        speed = next((label for label, value in SPEEDS.items() if value == config.get('speed')), None)
        if speed:
            self.fields['speed'].set(speed)

    def restore(self):
        preferred_dialogue = None
        preferred_dialogue_job = None
        preferred_profile = None
        preferred_subtitles = None
        preferred_wording = None
        preferred_drive = None
        preferred_audio_mix = None
        preferred_clean = None
        try:
            preferences = json.loads((self.data_dir/'preferences.json').read_text(encoding='utf-8'))
            for key, variable in self.fields.items():
                if key in ('voice_provider', 'voice', 'reference_audio', 'reading_style',
                           'wording_enabled', 'wording_rules', 'drive_enabled', 'original_volume', 'voice_volume'):
                    continue
                value = preferences.get(key)
                if isinstance(variable, tk.BooleanVar) and type(value) is bool:
                    variable.set(value)
                elif isinstance(value, str):
                    variable.set(value)
            self.input.insert('1.0', str(preferences.get('input', '')))
            draft = Path(preferences.get('subtitles') or '.')
            if (draft.resolve().is_relative_to((self.data_dir/'dialogue-drafts').resolve()) or
                    draft.resolve().is_relative_to(self.jobs_dir.resolve())):
                from teodub.dialogue_editor import draft_binding
                draft_binding(draft, self.data_dir)
                preferred_dialogue = (str(draft), str(preferences.get('input', '')))
                preferred_dialogue_job = preferences.get('selection_job')
            self.fill_profile({**NEW_MODELS, **preferences, 'reading_style': preferences.get('reading_style', 'conversation')})
            preferred_audio_mix = validate_audio_mix(preferences)
            from teodub.subtitle_clean import validate_clean
            preferred_clean = validate_clean(preferences)
            self.clean_options = preferred_clean
            self.fill_audio_mix(preferred_audio_mix)
            preferred_subtitles = validate_subtitle_style(preferences)
            if 'wording' in preferences:
                preferred_wording = validate_wording(preferences)
            elif 'wording_enabled' in preferences or 'wording_rules' in preferences:
                preferred_wording = self.wording_from_text(preferences.get('wording_enabled', False),
                                                          preferences.get('wording_rules', ''))
            if preferred_wording is not None:
                self.fill_wording({'wording': preferred_wording})
            if 'drive_upload' in preferences:
                preferred_drive = preferences['drive_upload']
                self.fill_drive({'drive_upload': preferred_drive})
            if all(key in preferences for key in NEW_MODELS):
                preferred_profile = {key: preferences[key] for key in (
                    *NEW_MODELS, 'voice_provider', 'voice', 'reference_audio', 'voice_speed')
                                     if key in preferences}
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            pass
        try:
            saved = json.loads((self.data_dir/'last-job.json').read_text(encoding='utf-8'))
            self.current = self.owned_config(saved['directory'])
            self.fill_form(self.current)
            if preferred_profile is not None:
                # The form is for new work; resume() reloads the immutable job.
                self.fill_profile({**self.current, **preferred_profile})
            if preferred_subtitles is not None:
                for key, value in preferred_subtitles.items():
                    self.fields[key].set(value)
                self.update_subtitle_controls()
            if preferred_audio_mix is not None:
                self.fill_audio_mix(preferred_audio_mix)
            if preferred_clean is not None:
                self.clean_options = preferred_clean
            if preferred_wording is not None:
                self.fill_wording({'wording': preferred_wording})
            if preferred_drive is not None:
                self.fill_drive({'drive_upload': preferred_drive})
            self.result = self.read_result()
            if (self.result or {}).get('status') == 'completed' and (
                    self.current.get('mode') in ('download', 'captions') or
                    self.current.get('mode') == 'full' and not self.current.get('subtitles')):
                self.use_prepared_result()
            message = (self.result or {}).get('message') or 'Phiên trước chưa rõ kết quả. Bấm Tiếp tục để kiểm tra và xử lý phần còn lại.'
            self.status.set('Phiên trước: ' + str(message))
            self.append(self.status.get())
            if (self.result or {}).get('status') in FINISHED:
                self.progress.set(100)
            self.remember_preview()
        except FileNotFoundError:
            pass
        except (OSError, ValueError, KeyError, TypeError):
            self.current = None
            self.status.set('Chưa đọc được phiên trước. Chọn video để tạo phiên mới.')
        if preferred_dialogue and (not self.current or self.current.get('mode') == 'preview' or
                preferred_dialogue_job == self.current.get('directory') or
                preferred_dialogue[1] == self.input.get('1.0', 'end').strip()):
            self.fields['subtitles'].set(preferred_dialogue[0])
            self.input.delete('1.0', 'end')
            self.input.insert('1.0', preferred_dialogue[1])

    def refresh_buttons(self):
        busy = self.process is not None or self.closing or self.login_pending or self.campaign_busy or self.voice_learning_busy
        self.clean_button.configure(state='disabled' if busy else 'normal')
        self.clean_check.configure(state='disabled' if busy else 'normal')
        for widget in self.inputs:
            widget.configure(state='disabled' if busy else 'normal')
        for widget in (self.provider_picker, self.voice_picker, self.voice_speed_picker,
                       self.speed_picker, self.style_picker):
            widget.configure(state='disabled' if busy else 'readonly')
        for widget in (self.start_button, self.preview_button):
            widget.configure(state='disabled' if busy else 'normal')
        self.stop_button.configure(state='normal' if self.process is not None and not self.closing
                                   and self.stop_started is None else 'disabled')
        drive_pending = (self.current is not None and
            (self.current.get('drive_upload') or {}).get('enabled') and
            (self.result or {}).get('status') == 'completed' and
            ((self.result or {}).get('google_drive') or {}).get('state') not in ('uploaded', 'staged'))
        resume = self.current is not None and ((self.result or {}).get('status') not in FINISHED or drive_pending)
        self.continue_button.configure(state='normal' if resume and not busy else 'disabled')
        self.open_button.configure(state='normal' if self.current and not self.closing else 'disabled')
        self.replay_button.configure(state='normal' if not busy and self.matching_preview() else 'disabled')
        self.stop_listen_button.configure(state='normal' if self.playing and not self.closing else 'disabled')
        self.login_button.configure(state='disabled' if busy else 'normal')
        self.models_button.configure(state='disabled' if busy else 'normal')
        self.wording_button.configure(state='disabled' if busy else 'normal')
        self.drive_button.configure(state='disabled' if busy else 'normal')
        self.drive_check.configure(state='disabled' if busy else 'normal')
        self.dialogue_button.configure(state='disabled' if busy else 'normal')
        self.adam_button.configure(state='disabled' if busy else 'normal')
        for button in (self.download_button, self.captions_button, self.dub_button):
            button.configure(state='disabled' if busy else 'normal')
        self.subtitle_check.configure(state='disabled' if busy else 'normal')
        self.update_subtitle_controls()

    def open_result(self):
        if not self.current:
            return
        outputs = (self.result or {}).get('outputs') or {}
        media = outputs.get('video') or outputs.get('audio') or outputs.get('source') or outputs.get('subtitles')
        path = Path(media).parent if media and Path(media).is_file() else Path(self.current['directory'])
        try:
            os.startfile(path)
        except OSError as exc:
            self.status.set('Chưa mở được thư mục: ' + str(exc))

    def close(self):
        if self.closing or self.closed:
            return
        if getattr(self,'restart_controller',None):
            self.restart_controller.cancel()
        self.closing = True
        self.stop_playback(refresh=False)
        self.close_started = time.monotonic()
        if self.process is not None:
            self.stop()
            self.refresh_buttons()
        else:
            self.finish_close()

    def finish_close(self):
        self.stop_playback(refresh=False)
        if getattr(self, 'voice_learning_tab', None):
            self.voice_learning_tab.shutdown()
        try:
            self.save_preferences()
        except OSError:
            pass
        self.closed = True
        self.window.after_cancel(self.timer)
        self.window.destroy()

    def save_preferences(self):
        values = {key: variable.get() for key, variable in self.fields.items()}
        # Preferences need no hash; each new job freezes and hashes the selected sample.
        try:
            selection = self.voice_selection(include_hash=False)
            values.update(selection)
            values['reference_audio'] = selection.get('reference_audio', '')
        except (ValueError, OSError):
            pass
        try:
            values.update(self.audio_mix_selection())
        except (ValueError, TypeError):
            pass
        try:
            values['wording'] = self.wording_selection()
        except (ValueError, TypeError):
            pass
        try:
            values['drive_upload'] = self.drive_selection()
        except (ValueError, TypeError):
            pass
        values['input'] = self.input.get('1.0', 'end').strip()
        values['subtitle_clean'] = self.clean_selection()
        values['selection_job'] = (self.current or {}).get('directory')
        atomic_json(self.data_dir/'preferences.json', values)


def single_video_body(parent):
    """Keep all video controls reachable on shorter screens and high Windows DPI."""
    canvas = tk.Canvas(parent, highlightthickness=0)
    scrollbar = ttk.Scrollbar(parent, orient='vertical', command=canvas.yview)
    scrollbar.pack(side='right', fill='y')
    canvas.pack(side='left', fill='both', expand=True)
    canvas.configure(yscrollcommand=scrollbar.set)
    body = ttk.Frame(canvas)
    window = canvas.create_window((0, 0), window=body, anchor='nw')
    body.bind('<Configure>', lambda _event: canvas.configure(scrollregion=canvas.bbox('all')))
    canvas.bind('<Configure>', lambda event: canvas.itemconfigure(window, width=event.width))
    return body


def main(args=None):
    parser = argparse.ArgumentParser(description='Tèo Dub Studio')
    parser.add_argument('--data-dir', default=str(default_data_dir()))
    parser.add_argument('--web-data-dir', default=None, help='Thư mục dữ liệu web để theo dõi')
    parser.add_argument('--restart-token', default=None, help=argparse.SUPPRESS)
    options = parser.parse_args(args)
    transfer = None
    root = tk.Tk()
    if options.restart_token:
        root.withdraw()  # Build every tab before ACK; do not dispatch timers yet.
    root.title('Tèo Dub Studio')
    root.geometry('1120x860')
    root.minsize(1050, 850)
    style = ttk.Style(root)
    style.theme_use('clam')
    style.configure('.', font=('Segoe UI', 10))
    style.configure('TButton', padding=(12, 7))
    restart_bar = ttk.Frame(root,padding=(8,4))
    restart_bar.pack(fill='x')
    restart_status = tk.StringVar(root,'Khởi động lại để nạp bản cập nhật, giữ tiến trình video và lịch đã bật.')
    notebook = ttk.Notebook(root)
    notebook.pack(fill='both', expand=True)
    single_tab = ttk.Frame(notebook)
    notebook.add(single_tab, text='Video đơn')
    studio = Studio(root, options.data_dir, parent=single_video_body(single_tab))
    from teodub.campaign_ui import CampaignTab
    campaign_tab = CampaignTab(notebook, studio)
    notebook.add(campaign_tab, text='Chiến dịch hàng loạt')
    from teodub.campaign_batch_controller import CampaignBatchController
    from teodub.campaign_library_ui import CampaignLibraryTab
    studio.batch_controller = CampaignBatchController(studio, campaign_tab)
    library_tab = CampaignLibraryTab(notebook, studio, campaign_tab, studio.batch_controller)
    notebook.add(library_tab, text='Danh sách chiến dịch')
    from teodub.facebook_ui import FacebookTab
    facebook_tab = FacebookTab(notebook, studio)
    studio.facebook_tab = facebook_tab
    notebook.add(facebook_tab, text='Quản lý Page')
    from teodub.web_tracking_ui import WebTrackingTab
    studio.web_tracking_tab = WebTrackingTab(notebook, options.web_data_dir)
    notebook.add(studio.web_tracking_tab, text='Công việc web & thống kê')
    from teodub.voice_learning_ui import VoiceLearningTab
    studio.voice_learning_tab = VoiceLearningTab(notebook, studio, campaign_tab=campaign_tab)
    notebook.add(studio.voice_learning_tab, text='Học giọng & phong cách')
    # Last tab: restart restores the selected tab by index, so earlier indices must not shift.
    from teodub.channel_autopilot_ui import ChannelAutopilotTab
    studio.channel_autopilot_tab = ChannelAutopilotTab(notebook, studio, campaign_tab, studio.batch_controller)
    notebook.add(studio.channel_autopilot_tab, text='Nguồn kênh tự động')
    from teodub.plan_review_ui import PlanReviewTab
    studio.plan_review_tab = PlanReviewTab(notebook, studio, facebook_tab.get_page_scheduler)
    notebook.add(studio.plan_review_tab, text='Duyệt caption')
    from teodub.restart import RestartController,restore_transfer,transfer_directory
    studio.restart_controller = RestartController(studio,campaign_tab,facebook_tab,notebook,restart_status,
        web_data_dir=options.web_data_dir)
    ttk.Button(restart_bar,text='Khởi động lại, giữ công việc',command=studio.restart_controller.request).pack(side='left')
    ttk.Button(restart_bar,text='Hủy khởi động lại',command=studio.restart_controller.cancel).pack(side='left',padx=6)
    ttk.Button(restart_bar,text='Chi tiết',command=studio.restart_controller.show_report).pack(side='left')
    ttk.Label(restart_bar,textvariable=restart_status,wraplength=450).pack(side='left',padx=4)
    if options.restart_token:
        from teodub.restart import accept_transfer
        transfer = accept_transfer(options.data_dir,options.restart_token)
        studio.restore()  # Reload preferences saved at the old UI's final checkpoint.
    if transfer:
        messages = restore_transfer(studio,campaign_tab,facebook_tab,notebook,transfer)
        transfer.update(state='restored',restored_at=time.time(),report=messages)
        atomic_json(transfer_directory(studio.data_dir,options.restart_token)/'transfer.json',transfer)
        studio.restart_controller.folder = transfer_directory(studio.data_dir,options.restart_token)
        attention = any(m.startswith(('Cần kiểm tra','Chưa bật lại')) for m in messages)
        restart_status.set('Đã khởi động lại. '+('Có việc cần kiểm tra; bấm Chi tiết.' if attention else 'Đã nối lại công việc; bấm Chi tiết để xem.'))
        for message in messages:
            studio.append(message)
        root.deiconify()
    root.mainloop()
    return 0
