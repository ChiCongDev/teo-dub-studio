"""Chỉnh sửa cấu hình on a link's tab (owner 06/11/2026; choices 1A, 2B, 3A): a window like the Video đơn page, filled with the
settings THIS link will use for its next videos. "Lưu chỉnh sửa" saves them for the videos not made yet (the same pending settings
the voice and Sub buttons use); the Video đơn page and the other links are not touched. The finished videos are made again only
when the owner ticks it, and then only for what still reaches a finished video (voice, levels, Sub, picture speed, wording): no
download and no AI call (campaign_redo). The form borrows the Video đơn page's own methods, so every box is filled, read and
checked exactly as there."""
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk
from pathlib import Path
import types

from teodub import campaign_redo, config_presets
from teodub.common import atomic_json
from teodub.campaign import read_json
from teodub.campaign_settings import (VOICE_KEYS, current_voice_config, made_with_everything, same_language_models,
                                      save_next_settings, validate_settings, next_settings)
from teodub.campaign_subtitle_redo_ui import describe

# Taken unchanged from the Video đơn page (teodub.app.Studio); they only use the fields, labels and rows built below.
BORROWED = ('voice_selection', 'local_voices', 'reference_aliases', 'wording_selection', 'audio_mix_selection', 'drive_selection',
            'fill_drive', 'update_drive_summary', 'choose_drive', 'choose_drive_api', 'fill_profile', 'fill_audio_mix', 'voice_removal_selection',
            'fill_wording', 'update_profile_controls', 'update_subtitle_controls', 'choose_provider', 'choose_style',
            'choose_subtitle_color', 'choose_subtitle_weight', 'choose_subtitle_box', 'choose_reference')
CHANGES = (('giọng và cách đọc', set(VOICE_KEYS)), ('tốc độ hình', {'speed'}), ('âm lượng', {'original_gain', 'voice_gain'}), ('bỏ giọng nói gốc', {'remove_original_voice'}),
           ('Sub Việt', {'burn_subtitles', 'subtitle_color', 'subtitle_weight', 'subtitle_size', 'subtitle_box'}),
           ('từ ngữ', {'wording'}), ('mô hình AI', {'asr_model', 'translation_model', 'translation_effort'}),
           ('Google Drive', {'drive_upload'}))
REDO_FILE = 'config-redo-choice.json'
REDO_NOTE = 'Đang tick: bấm Lưu chỉnh sửa sẽ làm lại cả video đã xong chưa đăng (chỉ video chưa làm đúng cấu hình này).'
# Owner 12/11/2026: a finished video made with another recognition/translation model is made again too; it is translated again
# with AI (its Vietnamese lines may differ a little). This is only said in the plan, never a reason to skip it.
RETRANSLATE = 'sẽ dịch lại bằng AI (làm bằng mô hình nhận dạng/dịch khác; tốn AI, lời Việt có thể khác bản cũ một chút)'


def read_redo_choice(directory):
    """The tick the owner saved last time for this link. A missing or damaged file means not ticked, never a surprise redo."""
    try:
        return read_json(Path(directory)/REDO_FILE).get('redo') is True
    except (OSError, ValueError, AttributeError):
        return False


def write_redo_choice(directory, value):
    atomic_json(Path(directory)/REDO_FILE, dict(version=1, redo=bool(value)))


def changed_parts(before, after):
    """The names of the groups of settings that differ between two validated settings."""
    def value(settings, key):
        return '' if settings.get(key) is None else settings[key]  # A missing reference sample and an empty one are the same.
    return [name for name, keys in CHANGES if any(value(before, key) != value(after, key) for key in keys)]


class LinkConfigDialog(tk.Toplevel):
    def __init__(self, tab):
        super().__init__(tab)
        from teodub import app
        from teodub.ai_models import CHOICES, NEW_MODELS
        from teodub.speech_plan import STYLES
        from teodub.subtitle_style import SUBTITLE_BOXES, SUBTITLE_COLORS, SUBTITLE_WEIGHTS
        self.tab, self.studio, self.directory = tab, tab.studio, tab.directory
        self.data_dir = self.studio.data_dir
        self.window = self
        self.name = str((tab.manifest or {}).get('name') or self.directory.name[:8])
        self.closed = self.closing = self.login_pending = self.setting_profile = False
        self.process = None
        self.base = None
        self.adam_reference = None
        self.learned_profiles = {}
        self.drive_options = dict(version=1, enabled=False, account_id='', folder_id='', folder_name='')
        for name in BORROWED:
            setattr(self, name, types.MethodType(getattr(app.Studio, name), self))
        self.wording_from_text = app.Studio.wording_from_text
        self.choices = CHOICES
        self.refresh_learned_voices()
        try:
            from teodub.adam_voice import saved_reference
            self.adam_reference = saved_reference(self.data_dir)
        except (OSError, ValueError, KeyError, TypeError):
            pass  # A missing or broken sample stays unavailable; never substitute a voice.
        self.fields = {
            'voice_provider': tk.StringVar(self, value='local'), 'voice': tk.StringVar(self, value=app.VOICES[0]),
            'reference_audio': tk.StringVar(self), 'reading_style': tk.StringVar(self, value='conversation'),
            'voice_speed': tk.StringVar(self, value=next(label for label, speed in app.VOICE_SPEED_LABELS.items() if speed == 1.0)),
            'speed': tk.StringVar(self, value=next(iter(app.SPEEDS))),
            'original_volume': tk.StringVar(self, value='10'), 'voice_volume': tk.StringVar(self, value='100'),
            'remove_original_voice': tk.BooleanVar(self, value=False),
            'burn_subtitles': tk.BooleanVar(self, value=False), 'subtitle_color': tk.StringVar(self, value='white'),
            'subtitle_weight': tk.StringVar(self, value='bold'), 'subtitle_size': tk.StringVar(self, value='13'),
            'subtitle_box': tk.StringVar(self, value='none'), 'wording_enabled': tk.BooleanVar(self, value=False),
            'wording_rules': tk.StringVar(self, value=''), 'drive_enabled': tk.BooleanVar(self, value=False),
            **{key: tk.StringVar(self, value=value) for key, value in NEW_MODELS.items()},
        }
        self.provider_label = tk.StringVar(self, value=app.VOICE_PROVIDERS['local'])
        self.style_label = tk.StringVar(self, value=STYLES['conversation']['label'])
        self.subtitle_color_label = tk.StringVar(self, value=SUBTITLE_COLORS['white']['label'])
        self.subtitle_weight_label = tk.StringVar(self, value=SUBTITLE_WEIGHTS['bold']['label'])
        self.subtitle_box_label = tk.StringVar(self, value=SUBTITLE_BOXES['none']['label'])
        self.model_labels = {key: tk.StringVar(self) for key in CHOICES}
        self.voice_help, self.model_summary = tk.StringVar(self), tk.StringVar(self)
        self.drive_summary = tk.StringVar(self, value='Drive: chưa bật tự lưu')
        self.status = tk.StringVar(self, '')
        self.preset_choice, self.preset_name = tk.StringVar(self), tk.StringVar(self)
        self.preset_state = tk.StringVar(self)  # Owner 13/11/2026: which saved config the link uses now (display only).
        self.saved_boxes = None  # The boxes as the link's own settings fill them.
        self.preset_offer = None  # The settings the name box is about to keep, while it is shown.
        self.close_after_name = False
        self.title('Chỉnh sửa cấu hình')
        self.geometry('1000x860')
        self.minsize(820, 600)
        self.transient(tab.winfo_toplevel())
        self.build(app, CHOICES, STYLES, SUBTITLE_BOXES, SUBTITLE_COLORS, SUBTITLE_WEIGHTS)
        for key in ('voice_provider', 'voice', 'reference_audio', 'reading_style', 'voice_speed', *CHOICES):
            self.fields[key].trace_add('write', self.profile_changed)
        for key in ('burn_subtitles', 'subtitle_box'):
            self.fields[key].trace_add('write', lambda *_args: self.update_subtitle_controls())
        self.fields['drive_enabled'].trace_add('write', self.update_drive_summary)
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.load()
        self.watch_state()

    # The Video đơn page keeps these; the form has nothing to play, save or stop.
    def stop_playback(self, refresh=True):
        pass

    def save_preferences(self):
        pass

    def refresh_learned_voices(self):
        from teodub.voice_learning import list_profiles, profile_label
        profiles, _errors = list_profiles(self.data_dir)
        self.learned_profiles = {profile_label(profile): profile for profile in profiles}

    def profile_changed(self, *_args):
        if not self.setting_profile:
            self.update_profile_controls()
            self.sync_models()

    def sync_models(self):
        """The model lists show the chosen values; the voice engine only applies to the on-machine voices."""
        for key, options in self.choices.items():
            self.model_labels[key].set(options.get(self.fields[key].get(), self.fields[key].get()))
        self.tts_picker.configure(state='readonly' if self.fields['voice_provider'].get() == 'local' else 'disabled')

    def picker(self, parent, **options):
        """A read-only list. The mouse wheel scrolls the form over it instead of silently changing its choice."""
        box = ttk.Combobox(parent, state='readonly', **options)
        box.bind('<MouseWheel>', lambda event: (self.scroll(event), 'break')[1])
        return box

    def scroll(self, event):
        self.canvas.yview_scroll(int(-event.delta/120), 'units')

    def build(self, app, choices, styles, boxes, colors, weights):
        shell = ttk.Frame(self, padding=(16, 12))
        shell.pack(fill='both', expand=True)
        ttk.Label(shell, text=f'Chỉnh sửa cấu hình của "{self.name}"', font=('Segoe UI', 16, 'bold')).pack(anchor='w')
        ttk.Label(shell, wraplength=940, text='Giống trang Video đơn, nhưng chỉ cho link này: điền sẵn cấu hình link đang dùng. Bấm '
                  '"Lưu chỉnh sửa" thì các video CHƯA LÀM của link làm theo cấu hình mới, từ video kế tiếp. Video đang làm dở giữ cấu hình '
                  'cũ. Trang Video đơn và các link khác không đổi.').pack(anchor='w', pady=(4, 8))
        self.build_presets(shell)
        # Buttons, tick and notice are packed first, at the bottom, so a short window shrinks the form, never them.
        actions = ttk.Frame(shell)
        actions.pack(side='bottom', fill='x')
        self.save_button = ttk.Button(actions, text='Lưu chỉnh sửa', command=self.apply, state='disabled')
        self.save_button.pack(side='left')
        self.check_button = ttk.Button(actions, text='Kiểm tra video làm lại được', command=self.check, state='disabled')
        self.check_button.pack(side='left', padx=8)
        ttk.Button(actions, text='Đóng', command=self.close).pack(side='right')
        ttk.Label(shell, textvariable=self.status, wraplength=940, foreground='#164b78').pack(side='bottom', anchor='w', pady=4)
        redo = ttk.LabelFrame(shell, text='Video đã hoàn thành, chưa đăng', padding=8)
        redo.pack(side='bottom', fill='x', pady=(6, 0))
        self.redo = tk.BooleanVar(self, read_redo_choice(self.directory))
        self.redo_note = tk.StringVar(self)
        ttk.Checkbutton(redo, variable=self.redo, text='Làm lại cả video ĐÃ HOÀN THÀNH chưa đăng với chỉnh sửa này').pack(anchor='w')
        ttk.Label(redo, textvariable=self.redo_note, wraplength=920, foreground='#b3261e').pack(anchor='w')
        self.redo.trace_add('write', lambda *_args: self.redo_note.set(REDO_NOTE if self.redo.get() else ''))
        self.redo_note.set(REDO_NOTE if self.redo.get() else '')
        self.result = tk.Text(redo, wrap='word', height=5, font=('Segoe UI', 10), state='disabled')
        ttk.Label(redo, wraplength=920, foreground='#444444', text=(
            'Mặc định không tick: chỉ video chưa làm theo cấu hình mới. Khi tick, chỉ giọng, âm lượng, Sub, tốc độ hình và từ ngữ được '
            'làm lại: không tải lại, không gọi AI, dùng lại lời Việt đã dịch; video có lịch đăng gần nhất làm trước. Bản cũ được giữ; '
            'bản mới có đuôi "_lan-2". Video đã đăng hoặc đã gửi lên Page không bị làm lại; lịch chưa gửi giữ giờ, Page, caption và chuyển '
            'sang file mới khi nó xong. Mô hình AI chỉ áp dụng cho video chưa làm; video đã xong dùng mô hình khác sẽ được bỏ qua.')).pack(
                anchor='w', pady=(2, 4))
        self.result.pack(fill='x')
        body = ttk.Frame(shell)
        body.pack(fill='both', expand=True)
        self.canvas = tk.Canvas(body, highlightthickness=0)
        bar = ttk.Scrollbar(body, orient='vertical', command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=bar.set)
        bar.pack(side='right', fill='y')
        self.canvas.pack(side='left', fill='both', expand=True)
        form = ttk.Frame(self.canvas, padding=(0, 0, 10, 0))
        inside = self.canvas.create_window((0, 0), window=form, anchor='nw')
        form.bind('<Configure>', lambda event: self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<Configure>', lambda event: self.canvas.itemconfigure(inside, width=event.width))
        self.bind('<MouseWheel>', self.scroll)
        fields = self.fields

        voice = ttk.LabelFrame(form, text='Giọng đọc', padding=10)
        voice.pack(fill='x', pady=(0, 8))
        row = ttk.Frame(voice)
        row.pack(fill='x')
        ttk.Label(row, text='Bộ giọng').pack(side='left', padx=(0, 10))
        self.provider_picker = self.picker(row, textvariable=self.provider_label, values=list(app.VOICE_PROVIDERS.values()), width=30)
        self.provider_picker.pack(side='left')
        self.provider_picker.bind('<<ComboboxSelected>>', self.choose_provider)
        ttk.Label(row, text='Giọng đọc').pack(side='left', padx=(16, 10))
        self.voice_picker = self.picker(row, textvariable=fields['voice'], values=app.VOICES, width=32)
        self.voice_picker.pack(side='left')
        pacing = ttk.Frame(voice)
        pacing.pack(fill='x', pady=(8, 0))
        ttk.Label(pacing, text='Tốc độ giọng').pack(side='left', padx=(0, 10))
        self.picker(pacing, textvariable=fields['voice_speed'], values=list(app.VOICE_SPEED_LABELS), width=24).pack(side='left')
        ttk.Label(pacing, text='Kiểu đọc').pack(side='left', padx=(16, 8))
        style = self.picker(pacing, textvariable=self.style_label, values=[styles[key]['label'] for key in app.READING_STYLES], width=23)
        style.pack(side='left')
        style.bind('<<ComboboxSelected>>', self.choose_style)
        ttk.Label(pacing, text='Tốc độ hình').pack(side='left', padx=(16, 8))
        self.picker(pacing, textvariable=fields['speed'], values=list(app.SPEEDS), width=25).pack(side='left')
        extras = ttk.Frame(voice)
        extras.pack(fill='x')
        extras.columnconfigure(0, weight=1)
        self.sample_row = ttk.Frame(extras)
        self.sample_row.grid(row=0, column=0, sticky='ew', pady=(6, 0))
        self.sample_row.columnconfigure(1, weight=1)
        ttk.Label(self.sample_row, text='Mẫu giọng 3–8 giây').grid(row=0, column=0, padx=(0, 12))
        ttk.Entry(self.sample_row, textvariable=fields['reference_audio']).grid(row=0, column=1, sticky='ew')
        ttk.Button(self.sample_row, text='Chọn mẫu…', command=self.choose_reference).grid(row=0, column=2, padx=(10, 0))
        self.login_row = ttk.Frame(extras)
        self.login_row.grid(row=1, column=0, sticky='ew', pady=(6, 0))
        ttk.Label(self.login_row, text='CapCut trực tuyến cần đăng nhập: bấm "Mở CapCut / đăng nhập" ở trang Video đơn.').pack(anchor='w')
        ttk.Label(voice, wraplength=900, textvariable=self.voice_help).pack(anchor='w', pady=(6, 0))
        ttk.Label(voice, wraplength=900, foreground='#444444', text='Nghe thử giọng và nhập mẫu ADAM làm ở trang Video đơn hoặc nút '
                  '"Giọng & âm lượng…" của link; giọng ADAM đã nhập sẵn chọn được ở đây.').pack(anchor='w', pady=(2, 0))

        from teodub.audio_mix_ui import AudioMixControls
        AudioMixControls(form, fields).pack(fill='x', pady=(0, 8))

        sub = ttk.LabelFrame(form, text='Sub Việt ghi vào hình', padding=10)
        sub.pack(fill='x', pady=(0, 8))
        row = ttk.Frame(sub)
        row.pack(fill='x')
        ttk.Checkbutton(row, text='Ghi Sub Việt trực tiếp vào hình', variable=fields['burn_subtitles']).pack(side='left', padx=(0, 16))
        ttk.Label(row, text='Màu chữ').pack(side='left', padx=(0, 7))
        self.subtitle_color_picker = self.picker(row, textvariable=self.subtitle_color_label,
                                                 values=[item['label'] for item in colors.values()], width=12)
        self.subtitle_color_picker.pack(side='left')
        self.subtitle_color_picker.bind('<<ComboboxSelected>>', self.choose_subtitle_color)
        ttk.Label(row, text='Độ đậm').pack(side='left', padx=(16, 7))
        self.subtitle_weight_picker = self.picker(row, textvariable=self.subtitle_weight_label,
                                                  values=[item['label'] for item in weights.values()], width=14)
        self.subtitle_weight_picker.pack(side='left')
        self.subtitle_weight_picker.bind('<<ComboboxSelected>>', self.choose_subtitle_weight)
        ttk.Label(row, text='Cỡ chữ').pack(side='left', padx=(12, 6))
        self.subtitle_size_picker = ttk.Spinbox(row, from_=8, to=36, increment=1, width=5, textvariable=fields['subtitle_size'])
        self.subtitle_size_picker.pack(side='left')
        ttk.Label(row, text='(mặc định 13)').pack(side='left', padx=(5, 0))
        row = ttk.Frame(sub)
        row.pack(fill='x', pady=(6, 0))
        ttk.Label(row, text='Khung che Sub gốc').pack(side='left', padx=(0, 7))
        self.subtitle_box_picker = self.picker(row, textvariable=self.subtitle_box_label,
                                               values=[item['label'] for item in boxes.values()], width=22)
        self.subtitle_box_picker.pack(side='left')
        self.subtitle_box_picker.bind('<<ComboboxSelected>>', self.choose_subtitle_box)
        ttk.Label(sub, wraplength=900, foreground='#444444', text='Vùng xóa Sub gốc bằng GPU được lưu riêng cho từng video (mỗi video có '
                  'Sub gốc ở chỗ khác nhau) nên không nằm trong cửa sổ này; link giữ nguyên cài đặt xóa Sub hiện có.').pack(anchor='w', pady=(6, 0))

        wording = ttk.LabelFrame(form, text='Từ ngữ cho Sub Việt và lời đọc', padding=10)
        wording.pack(fill='x', pady=(0, 8))
        ttk.Checkbutton(wording, text='Bật bảng thay từ cho Sub Việt và lời đọc', variable=fields['wording_enabled']).pack(anchor='w')
        ttk.Label(wording, text='Mỗi dòng: từ/cụm nguồn => từ/cụm thay thế. Ví dụ: bán => báng. Tối đa 50 dòng.').pack(anchor='w', pady=(6, 3))
        self.rules = tk.Text(wording, height=4, wrap='word', font=('Segoe UI', 10), relief='solid', borderwidth=1)
        self.rules.pack(fill='x')
        self.rules.bind('<MouseWheel>', lambda event: (self.scroll(event), 'break')[1])

        models = ttk.LabelFrame(form, text='Mô hình AI cho các video làm tiếp', padding=10)
        models.pack(fill='x', pady=(0, 8))
        models.columnconfigure(1, weight=1)
        names = dict(asr_model='1. Nhận dạng lời nói', translation_model='2. Dịch Trung → Việt',
                     translation_effort='Mức suy luận khi dịch', tts_model='3. Tạo giọng trên máy')
        for index, (key, options) in enumerate(choices.items()):
            ttk.Label(models, text=names[key]).grid(row=index, column=0, sticky='w', padx=(0, 14), pady=3)
            box = self.picker(models, textvariable=self.model_labels[key], values=list(options.values()), width=43)
            box.grid(row=index, column=1, sticky='w', pady=3)
            box.bind('<<ComboboxSelected>>', lambda _event, item=key: self.choose_model(item))
            if key == 'tts_model':
                self.tts_picker = box
        ttk.Label(models, wraplength=900, textvariable=self.model_summary, foreground='#444444').grid(
            row=len(choices), column=0, columnspan=2, sticky='w', pady=(6, 0))

        drive = ttk.LabelFrame(form, text='Google Drive', padding=10)
        drive.pack(fill='x', pady=(0, 8))
        ttk.Checkbutton(drive, text='Tự lưu video thành phẩm lên Google Drive', variable=fields['drive_enabled']).pack(side='left')
        ttk.Button(drive, text='Chọn thư mục Drive…', command=self.choose_drive).pack(side='left', padx=(10, 8))
        ttk.Label(drive, textvariable=self.drive_summary, wraplength=500).pack(side='left')

    # Cấu hình đã lưu (owner 10/11/2026): named sets of these boxes, the same for every link; they only fill the boxes.
    def build_presets(self, shell):
        frame = ttk.LabelFrame(shell, text='Cấu hình đã lưu (dùng chung cho mọi link)', padding=(8, 4))
        frame.pack(fill='x', pady=(0, 6))
        row = ttk.Frame(frame)
        row.pack(fill='x')
        self.preset_picker = ttk.Combobox(row, textvariable=self.preset_choice, state='readonly', width=38)
        self.preset_picker.pack(side='left')
        self.preset_picker.bind('<<ComboboxSelected>>', lambda _event: self.update_preset_buttons())
        self.preset_apply = ttk.Button(row, text='Áp dụng', command=self.apply_preset)
        self.preset_apply.pack(side='left', padx=(8, 0))
        self.preset_rename = ttk.Button(row, text='Đổi tên…', command=self.rename_preset)
        self.preset_rename.pack(side='left', padx=(6, 0))
        self.preset_delete = ttk.Button(row, text='Xóa', command=self.delete_preset)
        self.preset_delete.pack(side='left', padx=(6, 0))
        ttk.Button(row, text='Lưu thành mẫu…', command=self.offer_current).pack(side='right')
        ttk.Label(frame, textvariable=self.preset_state, foreground='#1a4f8a').pack(anchor='w', pady=(4, 0))
        self.name_row = ttk.Frame(frame)  # Shown only while a name is asked for.
        ttk.Label(self.name_row, text='Lưu cấu hình này để dùng lại. Tên:').pack(side='left')
        self.name_entry = ttk.Entry(self.name_row, textvariable=self.preset_name, width=34)
        self.name_entry.pack(side='left', padx=6)
        self.name_entry.bind('<Return>', lambda _event: self.save_preset())
        ttk.Button(self.name_row, text='Lưu mẫu', command=self.save_preset).pack(side='left')
        ttk.Button(self.name_row, text='Không cần', command=self.skip_preset).pack(side='left', padx=6)
        self.refresh_presets()

    def refresh_presets(self, keep=''):
        self.presets = config_presets.read_presets(self.data_dir)
        names = [row['name'] for row in self.presets]
        self.preset_picker.configure(values=names)
        self.preset_choice.set(keep if keep in names else (self.preset_choice.get() if self.preset_choice.get() in names else ''))
        self.update_preset_buttons()

    def chosen_preset(self):
        return config_presets.find_by_name(self.presets, self.preset_choice.get()) if self.preset_choice.get() else None

    def update_preset_buttons(self):
        state = 'normal' if self.chosen_preset() is not None else 'disabled'
        for button in (self.preset_apply, self.preset_rename, self.preset_delete):
            button.configure(state=state)

    def apply_preset(self):
        """Fill every box from the chosen preset. Nothing is saved: the owner looks at the boxes, then presses Lưu chỉnh sửa."""
        row = self.chosen_preset()
        if row is None:
            self.status.set('Chọn một cấu hình đã lưu trước.')
            return
        if self.base is None:
            self.status.set('Chưa đọc được cấu hình hiện tại của link.')
            return
        try:
            config = dict(self.base, **row['settings'])
            config['drive_upload'] = self.drive_selection()  # The link's Google Drive choice stays as it is in the boxes now.
            self.fill(config)
        except (OSError, ValueError, KeyError, TypeError, StopIteration) as exc:
            self.load()  # A box may have been filled halfway: show the link's own settings again, never a mix.
            self.status.set(f'Chưa áp dụng được cấu hình "{row["name"]}" (các ô trở về cấu hình hiện tại của link): {exc}')
            return
        self.status.set(f'Đã điền các ô theo cấu hình "{row["name"]}". Chưa lưu cho link: xem lại rồi bấm Lưu chỉnh sửa.')

    def offer_current(self):
        """Lưu thành mẫu…: keep the boxes as they are now, without saving them for the link."""
        try:
            self.offer_name(config_presets.preset_settings(self.capture()), close_after=False)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.status.set('Chưa lưu được thành mẫu: '+str(exc))

    def offer_name(self, settings, *, close_after):
        same = config_presets.find_same(config_presets.read_presets(self.data_dir), settings)
        if same is not None:
            self.status.set(f'Cấu hình này đã có sẵn với tên "{same["name"]}".')
            return False
        self.preset_offer, self.close_after_name = settings, close_after
        self.preset_name.set('')
        self.name_row.pack(fill='x', pady=(6, 0))
        self.name_entry.focus_set()
        return True

    def hide_name(self):
        self.preset_offer = None
        self.name_row.pack_forget()
        if self.close_after_name:
            self.close()

    def save_preset(self):
        if self.preset_offer is None:
            return
        try:
            name = config_presets.clean_name(self.preset_name.get())
            same = config_presets.find_by_name(config_presets.read_presets(self.data_dir), name)
            if same is not None and not messagebox.askyesno(
                    'Cấu hình đã lưu', f'Đã có cấu hình tên "{same["name"]}". Ghi đè bằng các ô hiện tại?', parent=self):
                return
            record = config_presets.save_preset(self.data_dir, name, self.preset_offer, replace=same is not None)
        except ValueError as exc:
            self.status.set(str(exc))
            return
        self.refresh_presets(record['name'])
        self.status.set(f'Đã lưu cấu hình "{record["name"]}". Dùng lại bất cứ lúc nào bằng nút Áp dụng.')
        self.hide_name()

    def skip_preset(self):
        self.hide_name()

    def rename_preset(self):
        row = self.chosen_preset()
        if row is None:
            return
        name = simpledialog.askstring('Đổi tên cấu hình', 'Tên mới:', initialvalue=row['name'], parent=self)
        if name is None:
            return
        try:
            record = config_presets.rename_preset(self.data_dir, row['id'], name)
        except ValueError as exc:
            self.status.set(str(exc))
            return
        self.refresh_presets(record['name'])
        self.status.set(f'Đã đổi tên thành "{record["name"]}". Các link đang dùng cấu hình này không bị đổi.')

    def delete_preset(self):
        row = self.chosen_preset()
        if row is None or not messagebox.askyesno(
                'Xóa cấu hình', f'Xóa cấu hình "{row["name"]}"? Các link đang dùng nó không bị đổi.', parent=self):
            return
        try:
            config_presets.delete_preset(self.data_dir, row['id'])
        except ValueError as exc:
            self.status.set(str(exc))
            return
        self.preset_choice.set('')
        self.refresh_presets()
        self.status.set(f'Đã xóa cấu hình "{row["name"]}".')

    def choose_model(self, key):
        label = self.model_labels[key].get()
        value = next((item for item, text in self.choices[key].items() if text == label), None)
        if value is not None:
            self.fields[key].set(value)

    def load(self):
        try:
            self.base = current_voice_config(self.data_dir, self.directory)
            self.fill(self.base)
        except (OSError, ValueError, KeyError, TypeError, StopIteration) as exc:
            self.base = None
            self.status.set('Chưa đọc được cấu hình hiện tại của link: '+str(exc))
            return
        self.status.set('Đang hiện cấu hình link dùng cho video chưa làm. Chưa lưu gì.')
        self.remember_saved_boxes()
        self.save_button.configure(state='normal')
        self.check_button.configure(state='normal')

    def boxes(self):
        """The boxes as a saved config keeps them, or None when they cannot be read right now."""
        try:
            return config_presets.preset_settings(self.capture())
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def remember_saved_boxes(self):
        """After the link's own settings fill the boxes: remember them, and show the name of the saved config they equal in the box."""
        self.saved_boxes = self.boxes()
        name = config_presets.matching_name(self.presets, self.saved_boxes)
        self.preset_choice.set(name if name else '')
        self.update_preset_buttons()
        self.show_state()

    def show_state(self):
        if self.saved_boxes is None:
            self.preset_state.set('')
            return
        self.preset_state.set(config_presets.state_text(self.presets, self.saved_boxes, self.boxes()))

    def watch_state(self):
        """The label follows the boxes (a light look once a second); it stops with the window."""
        if getattr(self, 'closed', False):
            return
        try:
            self.show_state()
            self.after(1000, self.watch_state)
        except tk.TclError:
            pass  # The window is already gone.

    def fill(self, config):
        from teodub import app
        from teodub.subtitle_style import validate_subtitle_style
        self.fill_profile(config)
        style = validate_subtitle_style(config)
        for key, value in style.items():
            self.fields[key].set(value)
        self.fields['subtitle_box'].set(style.get('subtitle_box', 'none'))  # Absent in jobs without a box.
        self.fill_audio_mix(config)
        self.fill_wording(config)
        self.fill_drive(config)
        self.rules.delete('1.0', 'end')
        self.rules.insert('1.0', self.fields['wording_rules'].get())
        speed = next((label for label, value in app.SPEEDS.items() if value == config.get('speed')), None)
        if speed:
            self.fields['speed'].set(speed)
        self.update_profile_controls()
        self.sync_models()

    def capture(self):
        """What the boxes say now, checked as the Video đơn page checks it. Raises ValueError for a choice that is not valid."""
        from teodub import app
        from teodub.ai_models import NEW_MODELS, validate_models
        from teodub.subtitle_style import validate_subtitle_style
        fields = self.fields
        speed = app.SPEEDS.get(fields['speed'].get())
        if speed is None:
            raise ValueError('Chọn tốc độ hình.')
        fields['wording_rules'].set(self.rules.get('1.0', 'end').strip())
        selected = dict(self.voice_selection(), **validate_models({key: fields[key].get() for key in NEW_MODELS}),
                        **validate_subtitle_style({key: value.get() for key, value in fields.items()}),
                        wording=self.wording_selection(), speed=speed, **self.audio_mix_selection(), **self.voice_removal_selection())
        selected['drive_upload'] = self.drive_selection()
        selected['drive_data_dir'] = str(self.data_dir)
        return selected

    def merged_settings(self):
        """The link's settings with every box applied on top. The page leaves the reference sample out for an ordinary voice, so the
        link's old one is dropped first and cannot linger."""
        if self.base is None:
            raise ValueError('Chưa đọc được cấu hình hiện tại của link.')
        return dict({key: value for key, value in self.base.items() if key not in ('reference_audio', 'reference_sha256')},
                    **self.capture())

    def proposal(self):
        """(settings to save, validated settings, names of what changed). Nothing is saved here."""
        merged = self.merged_settings()
        settings = validate_settings(merged)
        return merged, settings, changed_parts(validate_settings(self.base), settings)

    def plan(self, settings, settings_id=None):
        plan = campaign_redo.build_plan(
            self.directory, settings_id=settings_id,
            unchanged=lambda config: made_with_everything(config, settings) and same_language_models(config, settings),
            same_reason='Đã có đúng cấu hình này.')
        plan['retranslate'] = [number for number in plan['numbers']
                               if not campaign_redo.job_config_matches(self.directory, number,
                                                                       lambda config: same_language_models(config, settings))]
        return plan

    def say(self, text):
        self.result.configure(state='normal')
        self.result.delete('1.0', 'end')
        self.result.insert('1.0', text)
        self.result.configure(state='disabled')

    def check(self):
        """Read only: which finished videos would be made again with the boxes as they are now."""
        try:
            _merged, settings, changed = self.proposal()
            plan = self.plan(settings)
            due = campaign_redo.next_due(self.data_dir, (self.tab.manifest or {}).get('id', ''))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.say('')
            self.status.set('Chưa kiểm tra được: '+str(exc))
            return
        order = sorted(((number, due[number]) for number in plan['numbers'] if number in due), key=lambda row: (row[1], row[0]))
        self.say(describe(plan, order))
        self.status.set(('Đã đổi: '+', '.join(changed)+'. ' if changed else 'Chưa đổi gì so với cấu hình hiện tại. ')
                        +'Chỉ đọc; chưa thay đổi gì.')

    def apply(self):
        try:
            merged, settings, changed = self.proposal()
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.status.set('Chưa lưu: '+str(exc))
            return
        if not changed:
            self.redo_only(settings)
            return
        try:
            keep = config_presets.preset_settings(self.capture())
            record = save_next_settings(self.data_dir, self.directory, merged, allow_finished=self.redo.get())
            try:
                write_redo_choice(self.directory, self.redo.get())  # Remembered for the next time the window opens.
            except OSError:
                pass  # Only the memory of the tick is lost; the saved settings and the redo below are unaffected.
            message = f'Đã lưu chỉnh sửa của "{self.name}" ({", ".join(changed)}) cho video chưa làm, từ video chạy tiếp theo.'
            if self.redo.get():
                plan = self.plan(record['settings'], record['id'])
                if plan['numbers']:
                    outcome = campaign_redo.apply(self.directory, plan['numbers'])
                    message += (f' Đã xếp làm lại {len(plan["numbers"])} video đã xong, video có lịch đăng gần nhất làm trước; '
                                +('link đang chạy sẽ làm chúng trước video kế tiếp.' if outcome['mode'] == 'request'
                                  else 'bấm Chạy để làm.'))
                else:
                    message += ' Không có video đã xong nào cần làm lại.'
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.status.set('Chưa lưu: '+str(exc))
            return
        self.saved_boxes = self.boxes()  # What is saved for the link now is what the boxes say.
        self.tab.message.set(message)
        self.tab.refresh(full=True)
        # Owner 10/11/2026: after saving, a name can be given to keep these settings and use them again; "Không cần" closes.
        if self.offer_name(keep, close_after=True):
            self.status.set('Đã lưu cho link. Muốn dùng lại cấu hình này sau? Đặt tên rồi bấm Lưu mẫu, hoặc bấm Không cần.')
        else:
            self.close()

    def redo_only(self, settings):
        """Owner 12/11/2026: the settings are already the link's, but the tick asks for the finished, unposted videos to follow them:
        Lưu chỉnh sửa queues those videos only (the saved settings are not written again)."""
        if not self.redo.get():
            self.status.set('Chưa đổi gì: sửa một ô rồi bấm Lưu chỉnh sửa.')
            return
        try:
            update = next_settings(self.directory)
            if not update:
                self.status.set('Chưa đổi gì: link chưa có cấu hình đã lưu; sửa một ô rồi bấm Lưu chỉnh sửa.')
                return
            plan = self.plan(settings, update['id'])
            if not plan['numbers']:
                self.status.set('Chưa đổi gì và không có video đã xong nào cần làm lại.')
                return
            outcome = campaign_redo.apply(self.directory, plan['numbers'])
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.status.set('Chưa xếp làm lại: '+str(exc))
            return
        message = (f'Cấu hình của "{self.name}" giữ nguyên. Đã xếp làm lại {len(plan["numbers"])} video đã xong chưa đăng theo cấu hình '
                   'này, video có lịch đăng gần nhất làm trước; '
                   +('link đang chạy sẽ làm chúng trước video kế tiếp.' if outcome['mode'] == 'request' else 'bấm Chạy để làm.'))
        if plan.get('retranslate'):
            message += f' {len(plan["retranslate"])} video sẽ dịch lại bằng AI.'
        self.tab.message.set(message)
        self.tab.refresh(full=True)
        self.close()

    def close(self):
        self.closed = True
        self.destroy()
