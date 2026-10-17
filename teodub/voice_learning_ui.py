"""A separate, local voice/style lab; opening the tab never starts inference."""
import json
import os
from pathlib import Path
import subprocess
import time
import tkinter as tk
from tkinter import filedialog, ttk
import uuid
import winsound

from teodub.common import atomic_json, hash_file
from teodub.voice_learning import (MAX_FILES, STYLE_LABELS, checked_candidate, checked_profile,
    analysis_text, library_root, list_profiles, profile_label, save_profile, video_links)


class VoiceLearningTab(ttk.Frame):
    def __init__(self, parent, studio, *, campaign_tab=None):
        super().__init__(parent, padding=14)
        self.studio = studio
        self.campaign_tab = campaign_tab
        self.campaign_target = None
        self.campaign_signature = None
        self.campaign_available = False
        self.data_dir = studio.data_dir
        self.files = []
        self.link = tk.StringVar(self)
        self.process = None
        self.folder = None
        self.operation = None
        self.analysis = None
        self.candidates = {}
        self.profiles = {}
        self.preview = None
        self.preview_binding = None
        self.stop_started = None
        self.log_stream = None
        self.timer = None
        self.dead = False
        self.status = tk.StringVar(self, 'Dán link hoặc thêm tệp, rồi bấm Phân tích video mẫu.')
        self.progress = tk.DoubleVar(self, 0)
        self.name = tk.StringVar(self)
        self.style = tk.StringVar(self, STYLE_LABELS[0])
        self.asr_model = tk.StringVar(self, 'small')
        self.tts_model = tk.StringVar(self, 'v3turbo')
        self.start_s = tk.StringVar(self, '0')
        self.limit_s = tk.StringVar(self, '120')
        self.history_var = tk.StringVar(self)
        self.use_campaign = tk.BooleanVar(self, False)
        self.campaign_caption = tk.StringVar(self)
        self.history = {}
        self.build()
        self.bind('<Destroy>', self.destroyed)
        self.refresh_library()
        self.refresh_history()
        self.timer = self.after(300, self.poll)

    def build(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        ttk.Label(self, text='Phân tích video & học giọng', font=('Segoe UI', 21, 'bold')).grid(row=0, sticky='w')
        ttk.Label(self, wraplength=1020, text='Dán link hoặc nhập tệp để học giọng, hoặc mở Nội dung & kịch bản để học cách làm video. '
            'Học giọng chạy trên máy; phân tích hình ảnh và viết kịch bản dùng hạn mức Codex.').grid(
                row=1, sticky='ew', pady=(5, 10))
        self.notebook = ttk.Notebook(self)
        self.notebook.grid(row=2, sticky='nsew')
        self.samples_tab = ttk.Frame(self.notebook, padding=10)
        self.library_tab = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(self.samples_tab, text='Video mẫu & nghe thử')
        self.notebook.add(self.library_tab, text='Giọng đã lưu')
        self.report_tab = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(self.report_tab, text='Báo cáo video')
        self.report_tab.columnconfigure(0, weight=1)
        self.report_tab.rowconfigure(1, weight=1)
        ttk.Label(self.report_tab, text='Báo cáo được lưu trên máy; gồm đoạn lời thoại, nhịp đọc và thông số video.',
            wraplength=990).grid(row=0, sticky='ew', pady=(0, 6))
        self.report_text = tk.Text(self.report_tab, wrap='word', state='disabled', font=('Segoe UI', 10))
        self.report_text.grid(row=1, column=0, sticky='nsew')
        report_scroll = ttk.Scrollbar(self.report_tab, command=self.report_text.yview)
        report_scroll.grid(row=1, column=1, sticky='ns')
        self.report_text.configure(yscrollcommand=report_scroll.set)
        ttk.Button(self.report_tab, text='Sao chép báo cáo', command=self.copy_report).grid(row=2, sticky='w', pady=6)
        self.notebook.bind('<<NotebookTabChanged>>', lambda _e: self.selection_changed())
        self.controls = []
        from teodub.app import single_video_body
        body = single_video_body(self.samples_tab)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(5, weight=1)
        row = ttk.Frame(body)
        row.grid(row=0, sticky='ew')
        for text, command in [('Thêm video / âm thanh…', self.choose_files), ('Bỏ tệp đã chọn', self.remove_file)]:
            control = ttk.Button(row, text=text, command=command)
            control.pack(side='left', padx=(0, 6))
            self.controls.append(control)
        ttk.Label(row, text='Tối đa 10 nguồn/lượt; mỗi hồ sơ dùng mẫu của một người nói.').pack(side='left', padx=5)
        row = ttk.LabelFrame(body, text='Học video bằng link Facebook / YouTube / Douyin / TikTok', padding=7)
        row.grid(row=1, sticky='ew', pady=5)
        row.columnconfigure(0, weight=1)
        link_entry = ttk.Entry(row, textvariable=self.link)
        link_entry.grid(row=0, column=0, sticky='ew')
        link_entry.bind('<Return>', lambda _e: self.add_links())
        link_button = ttk.Button(row, text='Thêm link', command=self.add_links)
        link_button.grid(row=0, column=1, padx=(7, 0))
        ttk.Label(row, text='Dán link video/Reel/Shorts công khai. Bấm Phân tích để Tèo tải và học video.',
            wraplength=990).grid(row=1, columnspan=2, sticky='w', pady=(4, 0))
        self.controls.extend((link_entry, link_button))
        self.sources_tree = self.table(body, ('file',), ('Video / âm thanh mẫu',), (900,), 2)
        self.sources_tree.master.grid(row=2, sticky='ew', pady=5)
        row = ttk.Frame(body)
        row.grid(row=3, sticky='ew', pady=4)
        ttk.Label(row, text='AI nhận dạng').pack(side='left')
        self.asr_picker = ttk.Combobox(row, textvariable=self.asr_model,
            values=('small', 'medium', 'large-v3'), state='readonly', width=12)
        self.asr_picker.pack(side='left', padx=5)
        self.pickers = [self.asr_picker]
        ttk.Label(row, text='Bắt đầu (giây)').pack(side='left', padx=(12, 5))
        start = ttk.Entry(row, textvariable=self.start_s, width=7)
        start.pack(side='left')
        ttk.Label(row, text='Đọc tối đa (giây/tệp)').pack(side='left', padx=(12, 5))
        limit = ttk.Spinbox(row, from_=3, to=300, textvariable=self.limit_s, width=7)
        limit.pack(side='left')
        self.controls.extend((start, limit))
        self.analyze_button = ttk.Button(row, text='Phân tích video mẫu', command=self.analyze)
        self.analyze_button.pack(side='left', padx=10)
        self.controls.append(self.analyze_button)
        ttk.Label(body, text='Small: nhẹ · Medium: cân bằng · Large v3: nặng hơn. Whisper dùng GPU nếu sẵn sàng; '
            'lỗi GPU sẽ được báo. Nhạc nền/người nói chưa được tự tách.', wraplength=990).grid(row=4, sticky='ew', pady=3)
        self.candidate_tree = self.table(body,
            ('source', 'time', 'seconds', 'language', 'rate'),
            ('Video mẫu', 'Đoạn trong video', 'Dài (giây)', 'Ngôn ngữ', 'Âm tiết/giây (ước tính)'),
            (330, 220, 110, 105, 195), 5)
        self.candidate_tree.master.grid(row=5, sticky='nsew', pady=4)
        self.candidate_tree.bind('<<TreeviewSelect>>', lambda _e: self.selection_changed())
        row = ttk.Frame(body)
        row.grid(row=6, sticky='ew')
        ttk.Label(row, text='Lượt phân tích đã lưu').pack(side='left')
        self.history_picker = ttk.Combobox(row, textvariable=self.history_var, state='readonly', width=68)
        self.history_picker.pack(side='left', padx=6, fill='x', expand=True)
        self.history_picker.bind('<<ComboboxSelected>>', self.load_history)
        self.details = tk.Text(body, height=4, wrap='word', state='disabled', font=('Segoe UI', 10))
        self.details.grid(row=7, sticky='ew', pady=5)
        row = ttk.Frame(body)
        row.grid(row=8, sticky='ew')
        ttk.Label(row, text='Tên giọng').pack(side='left')
        name = ttk.Entry(row, textvariable=self.name, width=29)
        name.pack(side='left', padx=6)
        ttk.Label(row, text='Phong cách của mẫu').pack(side='left', padx=(10, 5))
        self.style_picker = ttk.Combobox(row, textvariable=self.style, values=STYLE_LABELS, width=19)
        self.style_picker.pack(side='left')
        self.profile_controls = [name, self.style_picker]
        self.save_button = ttk.Button(row, text='Lưu giọng & phong cách', command=self.save)
        self.save_button.pack(side='left', padx=10)
        body = self.library_tab
        body.columnconfigure(0, weight=1)
        body.rowconfigure(1, weight=1)
        ttk.Label(body, text='Mỗi dòng là một giọng + phong cách từ mẫu đã duyệt. '
            'Có thể lưu nhiều phong cách cho cùng một giọng.', wraplength=980).grid(row=0, sticky='ew', pady=5)
        self.library_tree = self.table(body, ('name', 'style', 'date', 'source'),
            ('Tên giọng', 'Phong cách', 'Ngày lưu', 'Video mẫu'), (230, 160, 200, 350), 12)
        self.library_tree.master.grid(row=1, sticky='nsew')
        self.library_tree.bind('<<TreeviewSelect>>', lambda _e: self.selection_changed())
        self.library_tree.bind('<Double-1>', lambda _e: self.apply())
        self.library_notice = tk.StringVar(self)
        ttk.Label(body, textvariable=self.library_notice, wraplength=990).grid(row=2, sticky='ew', pady=8)
        ttk.Button(body, text='Làm mới thư viện', command=self.refresh_library).grid(row=3, sticky='w')
        preview = ttk.LabelFrame(self, text='Nghe thử lời mới bằng giọng mẫu', padding=8)
        self.preview_frame = preview
        preview.grid(row=3, sticky='ew', pady=(8, 4))
        self.preview_text = tk.Text(preview, height=2, wrap='word', font=('Segoe UI', 10))
        self.preview_text.pack(fill='x')
        self.preview_text.insert('1.0', 'Bạn biết không? Chỉ cần thay đổi cách ngắt nghỉ, câu chuyện sẽ dễ nghe hơn nhiều. Cùng nghe thử nhé!')
        row = ttk.Frame(preview)
        row.pack(fill='x', pady=(6, 0))
        self.raw_button = ttk.Button(row, text='Nghe mẫu gốc', command=self.listen_sample)
        self.raw_button.pack(side='left')
        ttk.Label(row, text='Bộ giọng').pack(side='left', padx=(10, 5))
        self.tts_picker = ttk.Combobox(row, textvariable=self.tts_model, values=('v3turbo', 'v3nano'),
            state='readonly', width=11)
        self.tts_picker.pack(side='left')
        self.pickers.append(self.tts_picker)
        self.generate_button = ttk.Button(row, text='Tạo & nghe lời mới', command=self.generate)
        self.generate_button.pack(side='left', padx=6)
        self.replay_button = ttk.Button(row, text='Nghe lại', command=self.replay)
        self.replay_button.pack(side='left')
        ttk.Button(row, text='Dừng nghe', command=self.stop_audio).pack(side='left', padx=6)
        self.apply_button = ttk.Button(row, text='Dùng để tạo video', command=self.apply)
        self.apply_button.pack(side='right')
        self.campaign_check = ttk.Checkbutton(preview, variable=self.use_campaign,
            text='Áp dụng cả cho chiến dịch đang chọn (từ video tiếp theo)')
        self.campaign_check.pack(anchor='w', pady=(6, 0))
        ttk.Label(preview, textvariable=self.campaign_caption, wraplength=990).pack(anchor='w')
        row = ttk.Frame(self)
        row.grid(row=4, sticky='ew', pady=4)
        self.stop_button = ttk.Button(row, text='Dừng xử lý mẫu', command=self.stop, state='disabled')
        self.stop_button.pack(side='right')
        ttk.Label(row, textvariable=self.status, wraplength=840).pack(side='left', fill='x', expand=True)
        ttk.Progressbar(self, variable=self.progress, maximum=100).grid(row=5, sticky='ew')
        self.voice_help = ttk.Label(self, text='Lưu hồ sơ sẽ tự chọn giọng và phong cách ở Video đơn, đồng thời dùng cho chiến dịch tạo mới. '
            'Tick mục chiến dịch để đổi riêng giọng cho các video chạy tiếp theo của chiến dịch đang chọn. '
            'Phong cách theo mẫu có thể chưa giống hoàn toàn; nghe duyệt trước khi sản xuất.', wraplength=1020)
        self.voice_help.grid(row=6, sticky='ew', pady=5)
        from teodub.video_content_ui import VideoContentPanel
        self.content_panel = VideoContentPanel(self.notebook, self)
        self.notebook.add(self.content_panel, text='Nội dung & kịch bản')
        # Wheel scrolling is scoped to this sample form; table/text controls
        # keep their own scrolling, and no global bindings affect other tabs.
        self.scroll_tag = 'VoiceLabScroll'+uuid.uuid4().hex
        # single_video_body creates scrollbar before canvas; find by type.
        canvas = next(w for w in self.samples_tab.winfo_children() if isinstance(w, tk.Canvas))
        def wheel(event):
            canvas.yview_scroll(-1 if event.delta > 0 else 1, 'units')
            return 'break'
        self.bind_class(self.scroll_tag, '<MouseWheel>', wheel)
        def attach(widget):
            if widget.winfo_class() not in ('Text', 'Treeview', 'TSpinbox', 'TCombobox', 'TScrollbar'):
                widget.bindtags((self.scroll_tag, *widget.bindtags()))
            for child in widget.winfo_children():
                attach(child)
        attach(self.samples_tab)

    def table(self, parent, columns, headings, widths, height):
        frame = ttk.Frame(parent)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        tree = ttk.Treeview(frame, columns=columns, show='headings', selectmode='browse', height=height)
        for key, title, width in zip(columns, headings, widths):
            tree.heading(key, text=title)
            tree.column(key, width=width, minwidth=70)
        tree.grid(row=0, column=0, sticky='nsew')
        y = ttk.Scrollbar(frame, orient='vertical', command=tree.yview)
        y.grid(row=0, column=1, sticky='ns')
        x = ttk.Scrollbar(frame, orient='horizontal', command=tree.xview)
        x.grid(row=1, column=0, sticky='ew')
        tree.configure(yscrollcommand=y.set, xscrollcommand=x.set)
        return tree

    @property
    def busy(self):
        return self.process is not None

    def blocked(self):
        return (self.busy or self.studio.process is not None or self.studio.campaign_busy or
            self.studio.closing or self.studio.restart_pending or self.studio.login_pending)

    def apply_blocked(self):
        # Saving a reviewed sample/choosing a library voice is light work;
        # inference remains blocked while a campaign consumes the machine.
        return (self.busy or self.studio.voice_learning_busy or self.studio.process is not None or
            self.studio.closed or self.studio.closing or self.studio.restart_pending or self.studio.login_pending)

    def refresh_campaign_target(self):
        directory = self.campaign_tab.directory if self.campaign_tab is not None else None
        if directory != self.campaign_target:
            self.use_campaign.set(False)
            self.campaign_target = directory
            self.campaign_signature = None
        if directory is None:
            self.campaign_caption.set('Chưa chọn chiến dịch; giọng vẫn tự áp dụng cho Video đơn và chiến dịch tạo mới.')
            self.campaign_available = False
            return False
        try:
            path = directory/'campaign.json'
            signature = (directory, path.stat().st_mtime_ns)
            if signature == self.campaign_signature:
                return self.campaign_available
            manifest = json.loads(path.read_text(encoding='utf-8'))
            self.campaign_caption.set('Chiến dịch: '+manifest['name']+'. Các cấu hình khác của chiến dịch được giữ nguyên.')
            self.campaign_signature = signature
            self.campaign_available = True
            return True
        except (OSError, ValueError, KeyError, TypeError):
            self.campaign_caption.set('Chưa đọc được chiến dịch đang chọn; kiểm tra tại tab Chiến dịch hàng loạt.')
            self.campaign_available = False
            return False

    def choose_files(self):
        if self.blocked():
            return
        paths = filedialog.askopenfilenames(parent=self, title='Chọn video / âm thanh để học giọng',
            filetypes=[('Video / âm thanh', '*.mp4 *.mov *.mkv *.webm *.wav *.mp3 *.m4a *.flac *.ogg'), ('Tất cả', '*.*')])
        for raw in paths:
            path = str(Path(raw).resolve())
            if path not in self.files and len(self.files) < MAX_FILES:
                self.files.append(path)
        self.refresh_sources()
        if len(self.files) == MAX_FILES:
            self.status.set('Đã chọn tối đa 10 tệp cho lượt này.')

    def refresh_sources(self):
        self.sources_tree.delete(*self.sources_tree.get_children())
        for index, path in enumerate(self.files):
            self.sources_tree.insert('', 'end', iid=str(index), values=(path,))

    def collect_links(self):
        links = video_links(self.link.get())
        combined = list(dict.fromkeys([*self.files, *links]))
        if len(combined) > MAX_FILES:
            raise ValueError('Tối đa 10 nguồn/lượt. Bỏ bớt nguồn trước khi thêm link.')
        self.files = combined
        self.link.set('')
        self.refresh_sources()

    def add_links(self):
        if self.blocked():
            return
        try:
            self.collect_links()
            self.status.set(f'Đã chọn {len(self.files)} nguồn. Bấm Phân tích video mẫu để tải và học.')
        except ValueError as exc:
            self.status.set('Chưa thêm link: '+str(exc))

    def copy_report(self):
        if not self.analysis:
            self.status.set('Phân tích một video trước để có báo cáo.')
            return
        self.clipboard_clear()
        self.clipboard_append(analysis_text(self.analysis))
        self.status.set('Đã sao chép báo cáo video.')

    def remove_file(self):
        if not self.blocked() and self.sources_tree.selection():
            del self.files[int(self.sources_tree.selection()[0])]
            self.refresh_sources()

    def active_target(self):
        if self.notebook.select() in (str(self.report_tab), str(getattr(self, 'content_panel', ''))):
            return None
        if self.notebook.index(self.notebook.select()) == 1:
            selection = self.library_tree.selection()
            return ('profile', selection[0]) if selection else None
        selection = self.candidate_tree.selection()
        return ('candidate', selection[0]) if selection else None

    def selection_changed(self):
        self.stop_audio()
        content = self.notebook.select() == str(getattr(self, 'content_panel', ''))
        for widget in (getattr(self, 'preview_frame', None), getattr(self, 'voice_help', None)):
            if widget:
                widget.grid_remove() if content else widget.grid()
        target = self.active_target()
        if target and target[0] == 'candidate':
            sample = self.candidates[target[1]]
            m = sample['metrics']
            pitch = f'{m["median_pitch_hz"]} Hz' if m['median_pitch_hz'] else 'chưa đo được'
            rate = m['estimated_syllables_per_s'] or '—'
            lines = (f'Lời AI nhận dạng: {sample["text"]}\n'
                f'Nhịp đọc ước tính: {rate} âm tiết/giây · Cao độ: {pitch} · '
                f'Khoảng nghỉ trung vị: {m["pause_median_s"] or "—"} giây.\n'
                f'Gợi ý: {sample.get("style_hint", "Theo mẫu")}. {sample.get("style_reason", "")}\n'
                'Số đo chỉ tham khảo; nhạc nền có thể ảnh hưởng. '+ ' '.join(m['warnings']))
            self.details.configure(state='normal')
            self.details.delete('1.0', 'end')
            self.details.insert('1.0', lines)
            self.details.configure(state='disabled')
        self.refresh_buttons()

    def show_analysis(self, report):
        self.analysis = report
        self.report_text.configure(state='normal')
        self.report_text.delete('1.0', 'end')
        self.report_text.insert('1.0', analysis_text(report))
        self.report_text.configure(state='disabled')
        self.candidates = {c['id']: c for c in report['candidates']}
        self.candidate_tree.delete(*self.candidate_tree.get_children())
        for c in report['candidates']:
            self.candidate_tree.insert('', 'end', iid=c['id'], values=(c['source_name'],
                f'{c["start_s"]:.2f} → {c["end_s"]:.2f}', c['metrics']['duration_s'],
                c['language'], c['metrics']['estimated_syllables_per_s'] or '—'))
        if self.candidates:
            first = next(iter(self.candidates))
            self.candidate_tree.selection_set(first)
            self.candidate_tree.focus(first)
        self.notebook.select(self.samples_tab)
        failures = '; '.join(Path(e['source']).name+': '+e['message'] for e in report.get('errors', []))
        self.status.set(f'Đã có {len(self.candidates)} mẫu · Whisper {report["model"]} · {report["device"]}. '
            + ('Lỗi một số tệp: '+failures if failures else 'Nghe mẫu gốc rồi thử lời mới trước khi lưu.'))
        self.selection_changed()

    def refresh_history(self):
        self.history = {}
        for file in sorted((library_root(self.data_dir)/'analyses').glob('*/analysis.json'), reverse=True):
            try:
                record = json.loads(file.read_text(encoding='utf-8'))
                label = f'{record["created_at"]} · {len(record["candidates"])} mẫu · {record["id"][:6]}'
                self.history[label] = file
            except (OSError, ValueError, KeyError, TypeError):
                continue
        self.history_picker.configure(values=tuple(self.history))

    def load_history(self, _event=None):
        if self.busy:
            return
        try:
            self.show_analysis(json.loads(self.history[self.history_var.get()].read_text(encoding='utf-8')))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.status.set('Chưa mở được lượt mẫu: '+str(exc))

    def refresh_library(self):
        selected = self.library_tree.selection()
        profiles, errors = list_profiles(self.data_dir)
        self.profiles = {p['id']: p for p in profiles}
        self.library_tree.delete(*self.library_tree.get_children())
        for p in profiles:
            self.library_tree.insert('', 'end', iid=p['id'], values=(p['name'], p['style'], p['created_at'],
                p['candidate']['source_name']))
        if selected and selected[0] in self.profiles:
            self.library_tree.selection_set(selected[0])
        self.library_notice.set(f'Đã lưu {len(profiles)} hồ sơ trên máy. '+('Hồ sơ lỗi: '+ '; '.join(errors) if errors else
            'Mẫu đã lưu là bản riêng; giữ nguyên giọng cho các phiên đang dùng.'))
        self.studio.refresh_learned_voices()
        self.refresh_buttons()

    def checked_target(self):
        target = self.active_target()
        if not target:
            raise ValueError('Chọn một đoạn mẫu hoặc giọng đã lưu trước.')
        if target[0] == 'profile':
            record = checked_profile(self.data_dir, target[1])
            return target, record['reference_audio'], record['reference_sha256']
        if not self.analysis:
            raise ValueError('Chưa có lượt phân tích mẫu.')
        record = checked_candidate(self.data_dir, self.analysis['id'], target[1])
        return target, record['path'], record['sha256']

    def stop_audio(self):
        self.studio.stop_playback(refresh=False)
        winsound.PlaySound(None, 0)

    def play(self, path):
        self.stop_audio()
        winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)

    def listen_sample(self):
        if self.blocked():
            return
        try:
            _, path, _ = self.checked_target()
            self.play(path)
        except (OSError, ValueError, RuntimeError) as exc:
            self.status.set('Chưa nghe được mẫu: '+str(exc))

    def analyze(self):
        try:
            if self.blocked():
                return
            if self.link.get().strip():
                self.collect_links()
            if not self.files:
                raise ValueError('Dán link hoặc thêm video/âm thanh mẫu trước.')
            start, limit = float(self.start_s.get()), float(self.limit_s.get())
            import math
            if not math.isfinite(start+limit) or start < 0 or not 3 <= limit <= 300:
                raise ValueError('Bắt đầu từ 0; đọc tối đa 3–300 giây/tệp.')
            self.launch('analyze', sources=list(self.files), asr_model=self.asr_model.get(), start_s=start, limit_s=limit)
        except (OSError, ValueError) as exc:
            self.status.set('Chưa phân tích: '+str(exc))

    def generate(self):
        try:
            target, _, digest = self.checked_target()
            text = self.preview_text.get('1.0', 'end').strip()
            if not 10 <= len(text) <= 300:
                raise ValueError('Nhập 10–300 ký tự cho đoạn nghe thử.')
            options = dict(profile_id=target[1]) if target[0] == 'profile' else dict(
                analysis_id=self.analysis['id'], candidate_id=target[1])
            self.preview_binding = (target, digest, text, self.tts_model.get())
            self.preview = None
            self.launch('preview', text=text, tts_model=self.tts_model.get(), **options)
        except (OSError, ValueError) as exc:
            self.status.set('Chưa tạo lời thử: '+str(exc))

    def launch(self, operation, **options):
        if self.blocked():
            raise ValueError('Đợi xử lý video hiện tại hoặc khởi động lại xong rồi thử.')
        from teodub.app import worker_command
        self.stop_audio()
        ident = uuid.uuid4().hex
        family = 'content' if operation == 'content' else 'analyses' if operation == 'analyze' else 'previews'
        folder = library_root(self.data_dir)/family/ident
        folder.mkdir(parents=True, exist_ok=False)
        config = dict(id=ident, data_dir=str(self.data_dir), operation=operation, **options)
        atomic_json(folder/'config.json', config)
        # Reuse the installed interpreter without touching main video job/state.
        python = worker_command(folder/'config.json')[0]
        command = [python, '-X', 'utf8', '-B', '-m', 'teodub.voice_learning', '--config', str(folder/'config.json')]
        log = (folder/'worker.log').open('wb')
        try:
            self.process = subprocess.Popen(command, cwd=str(Path(__file__).resolve().parents[1]),
                stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except Exception:
            log.close()
            raise
        self.log_stream = log
        self.folder, self.operation = folder, operation
        self.stop_started = None
        self.studio.voice_learning_busy = True
        self.studio.refresh_buttons()
        self.progress.set(0)
        self.status.set('Đang chuẩn bị ảnh và lời thoại để phân tích nội dung…' if operation == 'content' else
            'Đang tải / phân tích video mẫu trên máy…' if operation == 'analyze' else 'Đang tạo lời thử bằng bộ giọng của Tèo…')
        self.refresh_buttons()

    def replay(self):
        if self.blocked() or not self.preview:
            return
        try:
            target, _, digest = self.checked_target()
            binding = (target, digest, self.preview_text.get('1.0', 'end').strip(), self.tts_model.get())
            if binding != self.preview_binding or hash_file(self.preview['path']) != self.preview['sha256']:
                raise ValueError('Mẫu/lời/model đã đổi; tạo lại lời nghe thử.')
            self.play(self.preview['path'])
        except (OSError, ValueError, RuntimeError) as exc:
            self.status.set('Chưa nghe lại: '+str(exc))

    def save(self):
        if self.apply_blocked():
            return
        try:
            target = self.active_target()
            if not target or target[0] != 'candidate' or not self.analysis:
                raise ValueError('Chọn đoạn trong bảng Video mẫu để lưu.')
            p = save_profile(self.data_dir, self.analysis['id'], target[1], self.name.get(), self.style.get())
            self.refresh_library()
            self.library_tree.selection_set(p['id'])
            self.notebook.select(self.library_tab)
        except (OSError, ValueError) as exc:
            self.status.set('Chưa lưu hồ sơ: '+str(exc))
            return
        # A failed form/campaign application must not misreport a durable saved
        # profile as lost, or create a second profile automatically on retry.
        try:
            message = self.apply_profile(p['id'])
            self.status.set('Đã lưu '+profile_label(p)+'. '+message)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.status.set('Đã lưu '+profile_label(p)+'; chưa áp dụng xong: '+str(exc)+
                ' Chọn hồ sơ rồi bấm Dùng để tạo video để thử lại.')

    def apply_profile(self, profile_id):
        if self.use_campaign.get() and (self.campaign_tab is None or self.campaign_target is None or
                self.campaign_tab.directory != self.campaign_target):
            self.refresh_campaign_target()
            raise ValueError('Chiến dịch đang chọn đã đổi; tick lại đúng chiến dịch rồi áp dụng.')
        selection = self.studio.apply_learned_voice(profile_id, self.tts_model.get())
        if self.campaign_tab is not None:
            self.campaign_tab.capture_settings()  # New-campaign preview only.
        message = 'Đã tự chọn giọng và phong cách tại Video đơn và cho chiến dịch tạo mới.'
        if self.use_campaign.get():
            try:
                self.campaign_tab.apply_learned_voice_settings(selection)
            except (OSError, ValueError, KeyError, TypeError) as exc:
                self.studio.status.set(message+' Chưa đổi giọng chiến dịch: '+str(exc))
                raise ValueError('Video đơn đã chọn giọng; chưa đổi giọng chiến dịch: '+str(exc)) from exc
            message += ' Chiến dịch đang chọn sẽ dùng giọng này từ video tiếp theo.'
        self.studio.status.set(message+' Tốc độ ban đầu 1,00×, giữ nhịp mẫu; có thể chỉnh tiếp.')
        parent = self.master
        parent.select(parent.tabs()[0])
        return message

    def apply(self):
        if self.apply_blocked():
            return
        try:
            target = self.active_target()
            if not target or target[0] != 'profile':
                raise ValueError('Lưu hồ sơ trước, rồi chọn trong Giọng đã lưu.')
            self.status.set(self.apply_profile(target[1]))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.status.set('Chưa áp dụng giọng: '+str(exc))

    def stop(self):
        if self.busy and self.stop_started is None:
            (self.folder/'stop.request').touch()
            self.stop_started = time.monotonic()
            self.status.set('Đang dừng phân tích/nghe thử; hồ sơ đã lưu được giữ nguyên.')
            self.refresh_buttons()

    def refresh_buttons(self):
        if self.dead:
            return
        blocked = self.blocked()
        for control in self.controls:
            control.configure(state='disabled' if blocked else 'normal')
        for control in self.pickers:
            control.configure(state='disabled' if blocked else 'readonly')
        apply_blocked = self.apply_blocked()
        for control in self.profile_controls:
            control.configure(state='disabled' if apply_blocked else 'normal')
        self.tts_picker.configure(state='disabled' if apply_blocked else 'readonly')
        campaign_available = self.refresh_campaign_target()
        self.campaign_check.configure(state='normal' if campaign_available and not apply_blocked else 'disabled')
        self.history_picker.configure(state='disabled' if blocked else 'readonly')
        self.preview_text.configure(state='disabled' if blocked else 'normal')
        target = self.active_target()
        for control in (self.raw_button, self.generate_button):
            control.configure(state='normal' if target and not blocked else 'disabled')
        self.save_button.configure(state='normal' if target and target[0] == 'candidate' and not apply_blocked else 'disabled')
        self.apply_button.configure(state='normal' if target and target[0] == 'profile' and not apply_blocked else 'disabled')
        self.replay_button.configure(state='normal' if self.preview and not blocked else 'disabled')
        self.stop_button.configure(state='normal' if self.busy and self.stop_started is None else 'disabled')
        if hasattr(self, 'content_panel'):
            self.content_panel.refresh()

    def poll(self):
        self.timer = None
        if self.dead:
            return
        if self.busy:
            try:
                state = json.loads((self.folder/'state.json').read_text(encoding='utf-8'))
                if self.stop_started is None:
                    self.status.set(state['message'])
                    if isinstance(state.get('percent'), (int, float)):
                        self.progress.set(state['percent'])
            except (OSError, ValueError, KeyError, TypeError):
                pass
            if self.stop_started and time.monotonic()-self.stop_started > 5 and self.process.poll() is None:
                self.process.terminate()
            if self.process.poll() is not None:
                self.process = None
                if self.log_stream:
                    self.log_stream.close()
                    self.log_stream = None
                self.studio.voice_learning_busy = False
                self.studio.refresh_buttons()
                try:
                    outcome = json.loads((self.folder/'result.json').read_text(encoding='utf-8'))
                    if outcome['status'] != 'completed':
                        self.status.set('Đã dừng: '+outcome['message'] if outcome['status'] == 'cancelled'
                            else 'Lỗi xử lý mẫu: '+outcome['message'])
                    elif self.operation == 'analyze':
                        self.show_analysis(outcome['result'])
                        self.refresh_history()
                        self.progress.set(100)
                    elif self.operation == 'content':
                        self.content_panel.show(outcome['result'])
                        self.progress.set(100)
                    else:
                        self.preview = outcome['result']
                        self.progress.set(100)
                        self.status.set('Đã tạo lời thử bằng '+self.preview['tts_model']+'. Nghe so sánh rồi lưu hồ sơ.')
                        self.replay()
                except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
                    self.status.set('Đã dừng theo yêu cầu.' if self.stop_started else 'Chưa đọc được kết quả mẫu: '+str(exc))
                self.stop_started = None
        self.refresh_buttons()
        self.timer = self.after(300, self.poll)

    def shutdown(self):
        if self.dead:
            return
        self.dead = True
        self.unbind_class(self.scroll_tag, '<MouseWheel>')
        self.stop_audio()
        if self.timer:
            self.after_cancel(self.timer)
            self.timer = None
        if self.busy:
            (self.folder/'stop.request').touch()
            self.process.terminate()
            self.process = None
        if self.log_stream:
            self.log_stream.close()
            self.log_stream = None
        self.studio.voice_learning_busy = False

    def destroyed(self, event):
        if event.widget == self:
            self.shutdown()
