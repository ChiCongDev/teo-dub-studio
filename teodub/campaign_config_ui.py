"""Chỉnh sửa cấu hình on a link's tab (owner 06/11/2026; choices 1A, 2B, 3A): a window like the Video đơn page, filled with the
settings THIS link will use for its next videos. "Lưu chỉnh sửa" saves them for the videos not made yet (the same pending settings
the voice and Sub buttons use); the Video đơn page and the other links are not touched. The finished videos are made again only
when the owner ticks it, and then only for what still reaches a finished video (voice, levels, Sub, picture speed, wording): no
download and no AI call (campaign_redo). The form borrows the Video đơn page's own methods, so every box is filled, read and
checked exactly as there."""
import tkinter as tk
from tkinter import ttk
import types

from teodub import campaign_redo
from teodub.campaign_settings import (VOICE_KEYS, current_voice_config, made_with_everything, same_language_models,
                                      save_next_settings, validate_settings)
from teodub.campaign_subtitle_redo_ui import describe

# Taken unchanged from the Video đơn page (teodub.app.Studio); they only use the fields, labels and rows built below.
BORROWED = ('voice_selection', 'local_voices', 'reference_aliases', 'wording_selection', 'audio_mix_selection', 'drive_selection',
            'fill_drive', 'update_drive_summary', 'choose_drive', 'choose_drive_api', 'fill_profile', 'fill_audio_mix',
            'fill_wording', 'update_profile_controls', 'update_subtitle_controls', 'choose_provider', 'choose_style',
            'choose_subtitle_color', 'choose_subtitle_weight', 'choose_subtitle_box', 'choose_reference')
CHANGES = (('giọng và cách đọc', set(VOICE_KEYS)), ('tốc độ hình', {'speed'}), ('âm lượng', {'original_gain', 'voice_gain'}),
           ('Sub Việt', {'burn_subtitles', 'subtitle_color', 'subtitle_weight', 'subtitle_size', 'subtitle_box'}),
           ('từ ngữ', {'wording'}), ('mô hình AI', {'asr_model', 'translation_model', 'translation_effort'}),
           ('Google Drive', {'drive_upload'}))
SKIP_LANGUAGE = 'Làm bằng mô hình nhận dạng/dịch khác; làm lại sẽ phải dịch lại (tốn AI), nên để nguyên.'


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
        self.redo = tk.BooleanVar(self, False)
        ttk.Checkbutton(redo, variable=self.redo, text='Làm lại cả video ĐÃ HOÀN THÀNH chưa đăng với chỉnh sửa này').pack(anchor='w')
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
        self.save_button.configure(state='normal')
        self.check_button.configure(state='normal')

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
                        wording=self.wording_selection(), speed=speed, **self.audio_mix_selection())
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
        return campaign_redo.build_plan(
            self.directory, settings_id=settings_id, unchanged=lambda config: made_with_everything(config, settings),
            blocked=lambda config: '' if same_language_models(config, settings) else SKIP_LANGUAGE,
            same_reason='Đã có đúng cấu hình này.')

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
            self.status.set('Chưa đổi gì: sửa một ô rồi bấm Lưu chỉnh sửa.')
            return
        try:
            record = save_next_settings(self.data_dir, self.directory, merged, allow_finished=self.redo.get())
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
        self.tab.subtitle_dirty = False
        self.tab.message.set(message)
        self.tab.refresh(full=True)
        self.close()

    def close(self):
        self.closed = True
        self.destroy()
