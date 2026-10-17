"""Content analysis panel sharing the voice lab's isolated cancellable worker."""
import json
import os
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog

from teodub.common import hash_file
from teodub.voice_learning import library_root
from teodub.video_content import settings, load_report, report_text, bind_script_to_video


class VideoContentPanel(ttk.Frame):
    def __init__(self, parent, lab):
        super().__init__(parent, padding=10)
        self.lab = lab
        self.report = None
        self.history = {}
        self.frames = {}
        self.photo = None
        self.duration = tk.StringVar(self, '60')
        self.limit = tk.StringVar(self, '300')
        self.model = tk.StringVar(self, lab.studio.fields['translation_model'].get())
        self.history_var = tk.StringVar(self)
        self.source_notice = tk.StringVar(self)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(6, weight=1)
        ttk.Label(self, textvariable=self.source_notice, wraplength=990).grid(row=0, sticky='ew')
        ttk.Label(self, text='Video mới muốn làm: chủ đề, người xem, sản phẩm/sự kiện đã biết, tư liệu bạn có.',
                  wraplength=990).grid(row=1, sticky='ew', pady=(7, 3))
        self.topic = tk.Text(self, height=3, wrap='word', font=('Segoe UI', 10))
        self.topic.insert('1.0', 'Làm video mới cùng chủ đề, học cách mở đầu và kể chuyện; lời văn và tình huống mới cho người Việt.')
        self.topic.grid(row=2, sticky='ew')
        row = ttk.Frame(self)
        row.grid(row=3, sticky='ew', pady=5)
        ttk.Label(row, text='Video mới (giây)').pack(side='left')
        duration = ttk.Spinbox(row, from_=15, to=300, textvariable=self.duration, width=6)
        duration.pack(side='left', padx=(5, 15))
        ttk.Label(row, text='Đọc tối đa (giây/video)').pack(side='left')
        limit = ttk.Spinbox(row, from_=3, to=1800, textvariable=self.limit, width=7)
        limit.pack(side='left', padx=5)
        ttk.Label(row, text='AI').pack(side='left', padx=(10, 4))
        from teodub.ai_models import TRANSLATION_MODELS
        self.model_picker = ttk.Combobox(row, textvariable=self.model, values=tuple(TRANSLATION_MODELS), state='readonly', width=17)
        self.model_picker.pack(side='left')
        self.run_button = ttk.Button(row, text='Phân tích & viết kịch bản', command=self.analyze)
        self.run_button.pack(side='left', padx=8)
        self.inputs = [duration, limit, self.run_button]
        ttk.Label(self, wraplength=990, text='Dùng 1–3 nguồn ở Video mẫu; lấy ảnh và lời trong khoảng đã chọn. '
            'Whisper dùng GPU khi sẵn sàng. Bấm phân tích sẽ gửi tối đa 36 ảnh + lời thoại qua Codex, dùng hạn mức tài khoản. '
            'Kết quả gồm phân tích, kịch bản, cảnh quay và lời đọc; chưa tự dựng hình thành MP4.').grid(row=4, sticky='ew', pady=3)
        row = ttk.Frame(self)
        row.grid(row=5, sticky='ew', pady=5)
        ttk.Label(row, text='Đã lưu').pack(side='left')
        self.history_picker = ttk.Combobox(row, textvariable=self.history_var, state='readonly')
        self.history_picker.pack(side='left', fill='x', expand=True, padx=5)
        self.history_picker.bind('<<ComboboxSelected>>', self.open_history)
        ttk.Button(row, text='Làm mới', command=self.refresh_history).pack(side='left')
        self.output = ttk.Notebook(self)
        self.output.grid(row=6, sticky='nsew')
        text_tab = ttk.Frame(self.output)
        text_tab.columnconfigure(0, weight=1)
        text_tab.rowconfigure(0, weight=1)
        self.output.add(text_tab, text='Phân tích & kịch bản mới')
        self.text = tk.Text(text_tab, wrap='word', state='disabled', font=('Segoe UI', 10), height=8)
        self.text.grid(row=0, column=0, sticky='nsew')
        scroll = ttk.Scrollbar(text_tab, command=self.text.yview)
        scroll.grid(row=0, column=1, sticky='ns')
        self.text.configure(yscrollcommand=scroll.set)
        images = ttk.Frame(self.output, padding=5)
        self.output.add(images, text='Ảnh đã phân tích')
        images.columnconfigure(1, weight=1)
        images.rowconfigure(0, weight=1)
        self.frame_tree = lab.table(images, ('time',), ('Mốc ảnh trong video gốc',), (220,), 7)
        self.frame_tree.master.grid(row=0, column=0, sticky='ns')
        self.frame_tree.bind('<<TreeviewSelect>>', self.show_frame)
        self.picture = ttk.Label(images, anchor='center', text='Chọn một mốc để xem ảnh thực tế đã gửi AI.')
        self.picture.grid(row=0, column=1, sticky='nsew')
        row = ttk.Frame(self)
        row.grid(row=7, sticky='ew', pady=(6, 0))
        self.copy_button = ttk.Button(row, text='Sao chép kết quả', command=self.copy)
        self.copy_button.pack(side='left')
        self.folder_button = ttk.Button(row, text='Mở thư mục kết quả', command=self.open_folder)
        self.folder_button.pack(side='left', padx=6)
        self.use_button = ttk.Button(row, text='Chọn video hình & dùng lời đọc…', command=self.apply)
        self.use_button.pack(side='left')
        ttk.Label(self, text='Chọn video bạn đã dựng theo danh sách cảnh để chuyển lời đọc sang Video đơn, '
            'sửa lời nếu cần rồi lồng tiếng. Mốc SRT là thời gian dự kiến.', wraplength=990).grid(row=8, sticky='ew', pady=4)
        self.refresh_history()

    def analyze(self):
        if self.lab.blocked():
            return
        try:
            if self.lab.link.get().strip():
                self.lab.collect_links()
            cfg = settings(dict(sources=list(self.lab.files), topic=self.topic.get('1.0', 'end').strip(),
                target_seconds=int(self.duration.get()), start_s=float(self.lab.start_s.get()),
                limit_s=float(self.limit.get()), asr_model=self.lab.asr_model.get(),
                model=self.model.get(), effort='medium'))
            self.lab.launch('content', **cfg)
        except (ValueError, OSError) as exc:
            self.lab.status.set('Chưa phân tích nội dung: '+str(exc))

    def refresh_history(self):
        self.history = {}
        for file in (library_root(self.lab.data_dir)/'content').glob('*/content.json'):
            try:
                r = json.loads(file.read_text(encoding='utf-8'))
                label = f'{r["created_at"]} · {r["answer"]["new_video"]["title"][:65]} · {r["id"][:6]}'
                self.history[label] = r['id']
            except (OSError, ValueError, KeyError, TypeError):
                continue
        self.history = dict(sorted(self.history.items(), reverse=True))
        self.history_picker.configure(values=tuple(self.history))

    def open_history(self, _event=None):
        if self.lab.busy:
            return
        try:
            self.show(load_report(self.lab.data_dir, self.history[self.history_var.get()]))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.lab.status.set('Chưa mở được nội dung đã lưu: '+str(exc))

    def show(self, report):
        self.report = report
        self.text.configure(state='normal')
        self.text.delete('1.0', 'end')
        self.text.insert('1.0', report_text(report))
        self.text.configure(state='disabled')
        self.text.yview_moveto(0)
        self.frames = {f['id']: f for v in report['evidence'] for f in v['frames']}
        self.frame_tree.delete(*self.frame_tree.get_children())
        for ident, f in self.frames.items():
            self.frame_tree.insert('', 'end', iid=ident, values=(f'{ident} · {f["time_s"]:.2f} giây',))
        self.photo = None
        self.picture.configure(image='', text='Chọn mốc để xem ảnh thực tế đã gửi AI.')
        self.refresh_history()
        for label, ident in self.history.items():
            if ident == report['id']:
                self.history_var.set(label)
                break
        self.lab.notebook.select(self)
        self.lab.status.set('Đã lưu phân tích nội dung và kịch bản mới. Xem từng cảnh, rồi chuẩn bị tư liệu để tạo video.')
        self.refresh()

    def show_frame(self, _event=None):
        try:
            selected = self.frame_tree.selection()
            if not selected:
                return
            f = self.frames[selected[0]]
            root = library_root(self.lab.data_dir)/'content'/self.report['id']
            path = Path(f['path']).resolve()
            if not path.is_relative_to(root.resolve()) or hash_file(path) != f['sha256']:
                raise ValueError('Ảnh đã bị đổi hoặc không thuộc lượt phân tích.')
            from PIL import Image, ImageTk, ImageOps
            with Image.open(path) as img:
                preview = ImageOps.contain(img, (520, max(100, min(400, self.picture.winfo_height()-10))))
                self.photo = ImageTk.PhotoImage(preview, master=self)
            self.picture.configure(image=self.photo, text='')
        except (OSError, ValueError, KeyError) as exc:
            self.lab.status.set('Chưa mở ảnh: '+str(exc))

    def copy(self):
        if self.report:
            self.clipboard_clear()
            self.clipboard_append(report_text(self.report))
            self.lab.status.set('Đã sao chép phân tích và kịch bản mới.')

    def open_folder(self):
        if self.report:
            os.startfile(str(library_root(self.lab.data_dir)/'content'/self.report['id']))

    def apply(self):
        if self.lab.blocked() or not self.report:
            return
        video = filedialog.askopenfilename(parent=self, title='Chọn video hình đã dựng cho kịch bản mới',
            filetypes=[('Video', '*.mp4 *.mov *.mkv *.webm'), ('Tất cả', '*.*')])
        if not video:
            return
        try:
            if self.lab.blocked():
                raise ValueError('Đợi công việc hiện tại xong rồi áp dụng.')
            result = bind_script_to_video(self.lab.data_dir, self.report['id'], video)
            studio = self.lab.studio
            studio.input.delete('1.0', 'end')
            studio.input.insert('1.0', result['video'])
            studio.fields['subtitles'].set(result['subtitles'])
            studio.save_preferences()
            self.lab.master.select(self.lab.master.tabs()[0])
            studio.status.set('Đã chọn video hình và lời đọc mới. Bấm Xem / sửa lời thoại hoặc Lồng tiếng và xuất video.')
            self.lab.status.set('Đã đưa lời đọc sang Video đơn, gắn đúng video hình bạn chọn.')
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.lab.status.set('Chưa áp dụng lời đọc: '+str(exc))

    def refresh(self):
        blocked = self.lab.blocked()
        count = len(self.lab.files)
        self.source_notice.set(f'Nguồn: {count} video ở mục Video mẫu & nghe thử. '
            f'Bắt đầu từ {self.lab.start_s.get()} giây; muốn đổi nguồn/mốc hãy quay lại mục đó.')
        for widget in self.inputs:
            widget.configure(state='disabled' if blocked else 'normal')
        self.model_picker.configure(state='disabled' if blocked else 'readonly')
        self.history_picker.configure(state='disabled' if self.lab.busy else 'readonly')
        self.topic.configure(state='disabled' if blocked else 'normal')
        for widget in (self.copy_button, self.folder_button):
            widget.configure(state='normal' if self.report else 'disabled')
        self.use_button.configure(state='normal' if self.report and not blocked else 'disabled')
