"""User-supplied ADAM references: local immutable versions, no service calls."""
import json
from pathlib import Path
import queue
import threading
import uuid
import tkinter as tk
from tkinter import filedialog, ttk

from teodub.common import atomic_json, check_cancel
from teodub.voice_profiles import prepare_reference, validate_reference

LABEL = 'ADAM (theo mẫu)'


def library_root(data_dir):
    return (Path(data_dir)/'voice-library'/'adam').resolve()


def checked_reference(path, data_dir):
    path = Path(path).resolve()
    root = library_root(data_dir)
    if path.name != 'reference.wav' or path.parent.parent != root:
        raise ValueError('Mẫu ADAM không thuộc thư viện giọng của Tèo.')
    metadata = json.loads(path.with_suffix('.json').read_text(encoding='utf-8'))
    if not isinstance(metadata, dict) or not isinstance(metadata.get('path'), str) or Path(metadata['path']).resolve() != path:
        raise ValueError('Hồ sơ mẫu ADAM không khớp tệp âm thanh.')
    validate_reference(metadata)
    return path


def saved_reference(data_dir):
    index = library_root(data_dir)/'selected.json'
    if not index.exists():
        return None
    record = json.loads(index.read_text(encoding='utf-8'))
    return checked_reference(record['path'], data_dir)


def import_reference(source, data_dir, *, cancel=None):
    root = library_root(data_dir)
    folder = root/uuid.uuid4().hex
    metadata = prepare_reference(source, folder, cancel=cancel)
    check_cancel(cancel)
    target = checked_reference(metadata['path'], data_dir)
    check_cancel(cancel)
    # Updating the selection never replaces audio used by older jobs.
    atomic_json(root/'selected.json', {'path': str(target)})
    return target


class AdamImportDialog(tk.Toplevel):
    def __init__(self, parent, data_dir, on_save):
        super().__init__(parent)
        self.data_dir, self.on_save = data_dir, on_save
        self.cancel = threading.Event()
        self.results = queue.Queue()
        self.busy = False
        self.timer = None
        self.title('Nhập mẫu ADAM — chạy trên máy')
        self.geometry('660x270')
        self.transient(parent)
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.bind('<Destroy>', self.destroyed)
        frame = ttk.Frame(self, padding=15)
        frame.pack(fill='both', expand=True)
        ttk.Label(frame, text='Chọn đoạn ADAM nói rõ, ít nhạc nền, dài 3–8 giây. Có thể dùng tệp âm thanh hoặc video. '
                  'Tệp dài hơn chỉ lấy 8 giây đầu. Tèo lưu bản mẫu riêng trên máy.', wraplength=620).pack(anchor='w')
        ttk.Label(frame, text='Giọng tạo bằng VieNeu chỉ mô phỏng theo mẫu, không phải Adam gốc qua ElevenLabs. '
                  'Chưa có mẫu thì chưa thể tạo giọng ADAM.', wraplength=620).pack(anchor='w', pady=9)
        self.path = tk.StringVar(self)
        self.entry = ttk.Entry(frame, textvariable=self.path)
        self.entry.pack(fill='x')
        buttons = ttk.Frame(frame)
        buttons.pack(fill='x', pady=10)
        self.choose_button = ttk.Button(buttons, text='Chọn tệp mẫu…', command=self.choose)
        self.choose_button.pack(side='left')
        self.save_button = ttk.Button(buttons, text='Lưu mẫu ADAM', command=self.start)
        self.save_button.pack(side='left', padx=8)
        ttk.Button(buttons, text='Đóng', command=self.close).pack(side='right')
        self.status = tk.StringVar(self, value='Sau khi lưu, chọn Nghe thử giọng trong Video đơn.')
        ttk.Label(frame, textvariable=self.status, wraplength=620).pack(anchor='w')
        self.grab_set()

    def choose(self):
        value = filedialog.askopenfilename(parent=self, title='Chọn mẫu ADAM 3–8 giây',
            filetypes=[('Âm thanh / video', '*.wav *.mp3 *.m4a *.flac *.ogg *.mp4 *.webm *.mov'), ('Tất cả tệp', '*.*')])
        if value:
            self.path.set(value)

    def start(self):
        if self.busy:
            return
        source = self.path.get().strip()
        if not source or not Path(source).is_file():
            self.status.set('Chọn tệp mẫu ADAM đang có trên máy trước.')
            return
        self.busy = True
        for control in (self.entry, self.choose_button, self.save_button):
            control.configure(state='disabled')
        self.status.set('Đang kiểm tra và lưu mẫu trên máy…')
        def work():
            try:
                result = import_reference(source, self.data_dir, cancel=self.cancel)
                self.results.put((result, None))
            except Exception as exc:
                self.results.put((None, str(exc)))
        threading.Thread(target=work, daemon=True).start()
        self.timer = self.after(100, self.poll)

    def poll(self):
        self.timer = None
        try:
            path, error = self.results.get_nowait()
        except queue.Empty:
            self.timer = self.after(100, self.poll)
            return
        self.busy = False
        for control in (self.entry, self.choose_button, self.save_button):
            control.configure(state='normal')
        if error:
            self.status.set('Chưa lưu: '+error)
            return
        try:
            self.on_save(path)
        except (ValueError, OSError) as exc:
            self.status.set('Mẫu đã lưu; chưa chọn được giọng: '+str(exc))
            return
        self.close()

    def destroyed(self, event):
        if event.widget == self:
            self.cancel.set()
            if self.timer is not None:
                self.after_cancel(self.timer)
                self.timer = None

    def close(self):
        self.cancel.set()
        self.destroy()
