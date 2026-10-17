"""Human dialogue edits are new SRT snapshots, never mutations of a worker cache."""
import json
from pathlib import Path
import re
import uuid
import tkinter as tk
from tkinter import messagebox, ttk

from teodub.common import atomic_json, hash_file
from teodub.srt import stamp, write_srt
from teodub.subtitles import normalized_text, read_vietnamese_srt


def draft_binding(path, data_dir):
    path = Path(path).resolve()
    drafts = (Path(data_dir)/'dialogue-drafts').resolve()
    runs = (Path(data_dir)/'runs').resolve()
    if path.is_relative_to(runs):
        # Prepared captions remain immutable in their original worker directory.
        if path.name != 'vietnamese.srt' or path.parent.name != 'captions' or path.parent.parent.parent != runs:
            raise ValueError('Bản lời không thuộc một phiên chuẩn bị hợp lệ.')
        state = json.loads((path.parent.parent/'state.json').read_text(encoding='utf-8'))
        record = state['captions']
        if Path(record['path']).resolve() != path or record['sha256'] != hash_file(path):
            raise ValueError('Bản lời đã chuẩn bị bị thay đổi. Chọn lại đúng SRT.')
        return {'dialogue_source_sha256': state['source']['sha256']}
    if not path.is_relative_to(drafts):
        return {}
    record = json.loads(path.with_suffix('.json').read_text(encoding='utf-8'))
    if record.get('sha256') != hash_file(path):
        raise ValueError('Bản lời đã lưu bị thay đổi. Mở và lưu lại một bản lời mới.')
    return {key: record[key] for key in ('dialogue_source_sha256',) if record.get(key)}


def save_dialogue(path, texts, data_dir, *, expected_sha256, source_sha256=None):
    """Preserve cue order/timings and validate all text before creating a new draft."""
    if hash_file(path) != expected_sha256:
        raise ValueError('Bản lời gốc đã thay đổi trong lúc sửa. Đóng và mở lại để tránh ghi nhầm.')
    cues = read_vietnamese_srt(path)
    if len(texts) != len(cues):
        raise ValueError('Số câu không khớp bản lời gốc.')
    edited = []
    for index, (cue, text) in enumerate(zip(cues, texts), 1):
        text = normalized_text(text)
        if not text or re.search(r'[\u3400-\u9fff\U00020000-\U0002fa1f]', text):
            raise ValueError(f'Câu {index}: nhập lời Việt, không để trống hoặc còn chữ Trung.')
        edited.append(dict(cue, text=text))
    if hash_file(path) != expected_sha256:
        raise ValueError('Bản lời gốc đang thay đổi; chưa lưu.')
    folder = Path(data_dir)/'dialogue-drafts'/uuid.uuid4().hex
    folder.mkdir(parents=True, exist_ok=False)
    target = folder/'loi-thoai.srt'
    write_srt(target, edited)
    read_vietnamese_srt(target)
    atomic_json(target.with_suffix('.json'), dict(sha256=hash_file(target),
        original_sha256=expected_sha256, dialogue_source_sha256=source_sha256,
        cue_count=len(edited)))
    return target


class DialogueEditor(tk.Toplevel):
    def __init__(self, parent, path, data_dir, on_save, *, source_sha256=None):
        self.path = Path(path)
        self.digest = hash_file(self.path)
        self.cues = read_vietnamese_srt(self.path)
        if hash_file(self.path) != self.digest:
            raise ValueError('Bản lời đang thay đổi. Thử lại sau khi xử lý xong.')
        self.binding = draft_binding(path, data_dir)
        self.source_sha256 = self.binding.get('dialogue_source_sha256') or source_sha256
        super().__init__(parent)
        self.data_dir, self.on_save = data_dir, on_save
        self.texts = [cue['text'] for cue in self.cues]
        self.original = list(self.texts)
        self.index = None
        self.title('Xem và sửa lời thoại Việt')
        self.geometry('930x620')
        self.minsize(720, 470)
        self.transient(parent)
        self.protocol('WM_DELETE_WINDOW', self.close)
        frame = ttk.Frame(self, padding=12)
        frame.pack(fill='both', expand=True)
        ttk.Label(frame, text='Chọn một câu rồi sửa lời Việt bên dưới. Mốc thời gian được giữ nguyên.',
                  wraplength=850).pack(anchor='w')
        table = ttk.Frame(frame)
        table.pack(fill='both', expand=True, pady=8)
        self.tree = ttk.Treeview(table, columns=('number', 'time', 'text'), show='headings', selectmode='browse')
        for name, label, width in [('number', 'Câu', 50), ('time', 'Thời gian gốc', 200), ('text', 'Lời thoại Việt', 590)]:
            self.tree.heading(name, text=label)
            self.tree.column(name, width=width, stretch=name == 'text')
        scroll = ttk.Scrollbar(table, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        self.tree.pack(fill='both', expand=True)
        for i, cue in enumerate(self.cues):
            self.tree.insert('', 'end', iid=str(i), values=(i+1,
                stamp(cue['start_ms'])+' – '+stamp(cue['end_ms']), cue['text']))
        self.label = tk.StringVar(self)
        ttk.Label(frame, textvariable=self.label).pack(anchor='w')
        self.edit = tk.Text(frame, height=5, wrap='word', undo=True, font=('Segoe UI', 12))
        self.edit.pack(fill='x', pady=6)
        self.status = tk.StringVar(self, value=f'{len(self.cues)} câu. Lưu tạo một SRT riêng; bản dịch cũ được giữ.')
        ttk.Label(frame, textvariable=self.status, wraplength=850).pack(anchor='w')
        ttk.Label(frame, text='Sau khi lưu, bấm Lồng tiếng và xuất video. Nếu bật Từ ngữ, bảng thay từ vẫn áp dụng.',
                  wraplength=850).pack(anchor='w', pady=5)
        buttons = ttk.Frame(frame)
        buttons.pack(fill='x', pady=5)
        ttk.Button(buttons, text='Câu trước', command=lambda: self.move(-1)).pack(side='left')
        ttk.Button(buttons, text='Câu tiếp', command=lambda: self.move(1)).pack(side='left', padx=6)
        ttk.Button(buttons, text='Lưu lời thoại để lồng tiếng', command=self.save).pack(side='right')
        ttk.Button(buttons, text='Đóng', command=self.close).pack(side='right', padx=6)
        self.tree.bind('<<TreeviewSelect>>', self.select)
        self.tree.selection_set('0')
        self.select()
        self.grab_set()

    def commit(self):
        if self.index is not None:
            self.texts[self.index] = self.edit.get('1.0', 'end-1c')
            self.tree.set(str(self.index), 'text', normalized_text(self.texts[self.index]))

    def select(self, _event=None):
        selected = self.tree.selection()
        if not selected or int(selected[0]) == self.index:
            return
        self.commit()
        self.index = int(selected[0])
        self.label.set(f'Sửa câu {self.index+1} / {len(self.cues)}')
        self.edit.delete('1.0', 'end')
        self.edit.insert('1.0', self.texts[self.index])
        self.edit.edit_reset()

    def move(self, amount):
        target = max(0, min(len(self.cues)-1, (self.index or 0)+amount))
        self.tree.selection_set(str(target))
        self.tree.see(str(target))
        self.select()

    def save(self):
        self.commit()
        try:
            target = save_dialogue(self.path, self.texts, self.data_dir,
                                  expected_sha256=self.digest, source_sha256=self.source_sha256)
            self.on_save(target)
        except (OSError, ValueError, TypeError) as exc:
            self.status.set('Chưa lưu: '+str(exc))
            return
        self.destroy()

    def close(self):
        self.commit()
        if self.texts != self.original and not messagebox.askyesno(
                'Lời thoại chưa lưu', 'Đóng và bỏ các thay đổi chưa lưu?', parent=self):
            return
        self.destroy()
