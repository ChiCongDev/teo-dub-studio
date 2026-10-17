"""Review AI captions of every Page in one place; only approved items are scheduled."""
from datetime import datetime
import os
import queue
import threading
import tkinter as tk
from tkinter import ttk, messagebox

from teodub.page_content_plan import LOCAL, ContentPlanStore

TIME_FORMAT = '%Y-%m-%d %H:%M'
ALL_PAGES = 'Tất cả Page'


def local_text(stamp):
    return datetime.fromtimestamp(stamp, LOCAL).strftime(TIME_FORMAT)


def parse_local(text):
    try:
        return datetime.strptime(text.strip(), TIME_FORMAT).replace(tzinfo=LOCAL).timestamp()
    except ValueError:
        raise ValueError('Giờ đăng theo dạng YYYY-MM-DD HH:MM, giờ Việt Nam.') from None


def one_line(text, width=70):
    text = ' '.join(text.split())
    return text if len(text) <= width else text[:width-1]+'…'


class PlanReviewTab(ttk.Frame):
    """Edits and rejections are passive; approval asks once, then stages in a worker and arms on the Tk thread."""

    def __init__(self, parent, studio, get_scheduler, *, store=None, spawn=None):
        super().__init__(parent, padding=16)
        self.studio, self.get_scheduler = studio, get_scheduler
        self.store = store or ContentPlanStore(studio.data_dir)
        self.spawn = spawn or (lambda work: threading.Thread(target=work, daemon=True, name='teo-review-stage').start())
        self.results = queue.Queue()
        self.rows, self.pages = {}, {}
        self.current = None
        self.busy = False
        self.ticks = 0
        self.page_filter = tk.StringVar(self, value=ALL_PAGES)
        self.when = tk.StringVar(self)
        self.video = tk.StringVar(self)
        self.original = tk.StringVar(self)
        self.counts = tk.StringVar(self)
        self.status = tk.StringVar(self, value='Bài AI đề xuất cho mọi Page hiện ở đây. Bài chưa duyệt không bao giờ được đăng.')
        self.timer = None
        self.destroyed = False
        self.build()
        self.refresh()
        self.bind('<Destroy>', self.on_destroy, add='+')
        self.timer = self.after(1000, self.poll)

    def build(self):
        self.columnconfigure(0, weight=3)
        self.columnconfigure(1, weight=2)
        self.rowconfigure(3, weight=1)
        ttk.Label(self, text='Duyệt caption', font=('Segoe UI', 21, 'bold')).grid(row=0, column=0, columnspan=2, sticky='w')
        ttk.Label(self, wraplength=1000, text=(
            'Đọc và sửa caption AI soạn cho từng Page, chọn bài rồi bấm Duyệt & bật lịch. Tèo chỉ đăng bài đã duyệt; '
            'bài bị bỏ trả video lại kho để AI chọn ở lượt sau.')).grid(row=1, column=0, columnspan=2, sticky='w', pady=(4, 8))
        bar = ttk.Frame(self)
        bar.grid(row=2, column=0, columnspan=2, sticky='ew', pady=(0, 8))
        ttk.Label(bar, text='Page').pack(side='left')
        self.page_picker = ttk.Combobox(bar, textvariable=self.page_filter, state='readonly', width=40)
        self.page_picker.pack(side='left', padx=6)
        self.page_picker.bind('<<ComboboxSelected>>', lambda _event: self.refresh())
        ttk.Button(bar, text='Làm mới', command=self.refresh).pack(side='left')
        ttk.Label(bar, textvariable=self.counts).pack(side='left', padx=12)
        table = ttk.Frame(self)
        table.grid(row=3, column=0, sticky='nsew', padx=(0, 10))
        table.columnconfigure(0, weight=1)
        table.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(table, columns=('page', 'time', 'kind', 'caption', 'state'), show='headings',
                                 selectmode='extended', height=14)
        for key, label, width in (('page', 'Page', 130), ('time', 'Giờ đăng', 115), ('kind', 'Loại', 55),
                                  ('caption', 'Caption', 300), ('state', 'Trạng thái', 110)):
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, minwidth=50, stretch=key == 'caption')
        self.tree.grid(row=0, column=0, sticky='nsew')
        scroll = ttk.Scrollbar(table, orient='vertical', command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky='ns')
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.bind('<<TreeviewSelect>>', self.on_select)
        editor = ttk.LabelFrame(self, text='Bài đang xem', padding=10)
        editor.grid(row=3, column=1, sticky='nsew')
        editor.columnconfigure(0, weight=1)
        ttk.Label(editor, textvariable=self.video, wraplength=380).grid(row=0, column=0, sticky='w')
        ttk.Button(editor, text='Mở video', command=self.open_video).grid(row=0, column=1, sticky='e')
        ttk.Label(editor, text='Caption (đăng đúng nội dung này)').grid(row=1, column=0, columnspan=2, sticky='w', pady=(8, 2))
        self.caption = tk.Text(editor, height=8, wrap='word', width=44)
        self.caption.grid(row=2, column=0, columnspan=2, sticky='nsew')
        editor.rowconfigure(2, weight=1)
        ttk.Label(editor, text='Giờ đăng (UTC+7, YYYY-MM-DD HH:MM)').grid(row=3, column=0, columnspan=2, sticky='w', pady=(8, 2))
        ttk.Entry(editor, textvariable=self.when, width=20).grid(row=4, column=0, sticky='w')
        ttk.Label(editor, textvariable=self.original, wraplength=400, foreground='#555').grid(
            row=5, column=0, columnspan=2, sticky='w', pady=(8, 0))
        line = ttk.Frame(editor)
        line.grid(row=6, column=0, columnspan=2, sticky='w', pady=(8, 0))
        ttk.Button(line, text='Lưu sửa', command=self.save_edit).pack(side='left')
        ttk.Button(line, text='Khôi phục bản AI', command=self.restore).pack(side='left', padx=6)
        actions = ttk.Frame(self)
        actions.grid(row=4, column=0, columnspan=2, sticky='w', pady=(10, 0))
        self.approve_button = ttk.Button(actions, text='Duyệt & bật lịch bài đã chọn', command=self.approve)
        self.approve_button.pack(side='left')
        ttk.Button(actions, text='Bỏ bài đã chọn…', command=self.reject).pack(side='left', padx=6)
        ttk.Label(self, textvariable=self.status, wraplength=1000).grid(row=5, column=0, columnspan=2, sticky='w', pady=(8, 0))

    # Data ---------------------------------------------------------------------------
    def load_pages(self):
        try:
            saved = self.store.activity.accounts.load()
        except ValueError as exc:
            self.status.set(str(exc))
            return
        names = {account['id']: account['name'] for account in saved['accounts']}
        self.pages = {f'{names.get(p["account_id"], "Tài khoản")} · {p["name"]}': (p['account_id'], p['id'])
                      for p in saved['pages']}
        self.page_picker.configure(values=[ALL_PAGES, *self.pages])
        if self.page_filter.get() not in self.pages:
            self.page_filter.set(ALL_PAGES)

    def refresh(self):
        self.load_pages()
        chosen = self.pages.get(self.page_filter.get())
        try:
            rows = self.store.review_queue(pages=[chosen] if chosen else None)
        except ValueError as exc:
            self.status.set(str(exc))
            return
        self.rows = {row['record_id']+':'+row['item_id']: row for row in rows}
        for ident in self.tree.get_children():
            if ident not in self.rows:
                self.tree.delete(ident)
        for index, (ident, row) in enumerate(self.rows.items()):
            state = ('Cần kiểm tra' if row['decision'] == 'failed' else 'Quá giờ' if row['expired']
                     else 'Đã sửa · chờ duyệt' if row['edited'] else 'Chờ duyệt')
            values = (row['page_name'], local_text(row['due_at']), 'Video' if row['kind'] == 'video' else 'Bài chữ',
                      one_line(row['body']), state)
            if self.tree.exists(ident):
                self.tree.item(ident, values=values)
                self.tree.move(ident, '', index)
            else:
                self.tree.insert('', index, iid=ident, values=values)
        waiting = sum(1 for row in rows if not row['decision'] and not row['expired'])
        self.counts.set(f'{waiting} bài chờ duyệt' + (f' · {len(rows)-waiting} cần xử lý' if len(rows) > waiting else ''))
        if self.current and self.current not in self.rows:
            self.show(None)

    def selected_rows(self):
        return [self.rows[ident] for ident in self.tree.selection() if ident in self.rows]

    # Editor -------------------------------------------------------------------------
    def show(self, ident):
        self.current = ident
        row = self.rows.get(ident)
        self.caption.delete('1.0', 'end')
        if not row:
            self.video.set('Chọn một bài trong danh sách để đọc và sửa.')
            self.when.set('')
            self.original.set('')
            return
        self.caption.insert('1.0', row['body'])
        self.when.set(local_text(row['due_at']))
        self.video.set(f'{row["page_name"]} · {row["video_label"] or "Bài chữ"}')
        notes = []
        if row['edited']:
            notes.append('Bản AI gốc: '+one_line(row['original_body'], 160)+' · '+local_text(row['original_due_at']))
        if row['message']:
            notes.append(row['message'])
        if row['goal']:
            notes.append('Mục tiêu AI: '+row['goal'])
        self.original.set('\n'.join(notes))

    def editor_values(self, row):
        text = self.when.get().strip()
        due = row['due_at'] if text == local_text(row['due_at']) else parse_local(text)
        return self.caption.get('1.0', 'end-1c'), due

    def dirty(self):
        row = self.rows.get(self.current)
        if not row:
            return False
        return (self.caption.get('1.0', 'end-1c').strip() != row['body']
                or self.when.get().strip() != local_text(row['due_at']))

    def on_select(self, _event=None):
        selection = self.tree.selection()
        if len(selection) != 1 or selection[0] == self.current:
            return
        target = selection[0]
        if self.dirty():
            if not self.save_edit(refresh=False):
                self.tree.selection_set(self.current)
                return
            self.refresh()
        self.show(target)

    def save_edit(self, refresh=True):
        row = self.rows.get(self.current)
        if not row:
            self.status.set('Chọn một bài để sửa.')
            return False
        try:
            body, due = self.editor_values(row)
            self.store.edit_item(row['record_id'], row['item_id'], body=body, due_at=due)
        except ValueError as exc:
            self.status.set('Chưa lưu sửa: '+str(exc))
            return False
        self.status.set('Đã lưu sửa; bài vẫn chờ bạn duyệt.')
        if refresh:
            current = self.current
            self.refresh()
            self.show(current)
        return True

    def restore(self):
        row = self.rows.get(self.current)
        if not row:
            return
        try:
            self.store.edit_item(row['record_id'], row['item_id'], body=row['original_body'], due_at=row['original_due_at'])
        except ValueError as exc:
            self.status.set('Chưa khôi phục: '+str(exc))
            return
        current = self.current
        self.refresh()
        self.show(current)
        self.status.set('Đã khôi phục caption và giờ AI đề xuất.')

    def open_video(self):
        row = self.rows.get(self.current)
        if not row or not row['video_path']:
            self.status.set('Bài này không có video.')
            return
        try:
            os.startfile(row['video_path'])
        except (OSError, AttributeError) as exc:
            self.status.set('Chưa mở được video: '+str(exc))

    # Decisions ----------------------------------------------------------------------
    def approve(self):
        if self.busy:
            return
        if self.dirty() and not self.save_edit():
            return
        self.refresh()
        picked = self.selected_rows()
        if not picked:
            self.status.set('Chọn ít nhất một bài để duyệt (giữ Ctrl để chọn nhiều).')
            return
        blocked = [row for row in picked if row['decision'] or row['expired']]
        if blocked:
            self.status.set('Bỏ chọn bài quá giờ hoặc cần kiểm tra trước khi duyệt: '
                            + ', '.join(f'{row["page_name"]} {local_text(row["due_at"])}' for row in blocked[:5]))
            return
        groups = {}
        for row in picked:
            groups.setdefault(row['record_id'], {})[row['item_id']] = dict(body=row['body'], due_at=row['due_at'])
        lines = [f'{row["page_name"]} · {local_text(row["due_at"])} · {one_line(row["body"], 60)}' for row in picked[:8]]
        more = f'\n… và {len(picked)-8} bài khác' if len(picked) > 8 else ''
        if not messagebox.askyesno('Duyệt & bật lịch', f'Bật lịch {len(picked)} bài:\n\n'+'\n'.join(lines)+more
                                   +'\n\nTèo sẽ tự đăng đúng giờ với caption này. Tiếp tục?', parent=self):
            self.status.set('Chưa duyệt; không bài nào được bật lịch.')
            return
        self.busy = True
        self.approve_button.configure(state='disabled')
        self.status.set('Đang kiểm tra video và lưu lịch…')

        def work():
            outcome = []
            for record, expected in groups.items():
                try:
                    outcome.append((self.store.stage_items(record, expected), ''))
                except Exception as exc:
                    outcome.append((None, str(exc) or type(exc).__name__))
            self.results.put(outcome)
        self.spawn(work)

    def finish_approval(self, outcome):
        armed, errors = 0, [error for _, error in outcome if error]
        for revision, _ in outcome:
            if revision is None:
                continue
            try:
                armed += len(self.store.arm_items(revision, self.get_scheduler())['items'])
            except ValueError as exc:
                errors.append(str(exc))
        self.busy = False
        self.approve_button.configure(state='normal')
        message = f'Đã bật lịch {armed} bài; Tèo đăng đúng giờ.' if armed else 'Chưa bật lịch bài nào.'
        if errors:
            message += ' Cần kiểm tra: '+' | '.join(errors[:3])
        self.status.set(message)
        self.refresh()

    def reject(self):
        picked = self.selected_rows()
        if not picked:
            self.status.set('Chọn bài cần bỏ.')
            return
        if not messagebox.askyesno('Bỏ bài', f'Bỏ {len(picked)} bài? Bài không được đăng; video trở lại kho để AI '
                                   'chọn ở lượt sau.', parent=self):
            return
        groups = {}
        for row in picked:
            groups.setdefault(row['record_id'], []).append(row['item_id'])
        errors = []
        for record, items in groups.items():
            try:
                self.store.reject_items(record, items)
            except ValueError as exc:
                errors.append(str(exc))
        self.status.set('Đã bỏ bài đã chọn.' if not errors else 'Chưa bỏ được: '+' | '.join(errors[:3]))
        self.refresh()

    # Timer --------------------------------------------------------------------------
    def poll(self):
        if self.destroyed:
            return
        try:
            self.finish_approval(self.results.get_nowait())
        except queue.Empty:
            self.ticks += 1
            if self.ticks % 10 == 0 and not self.busy and not self.dirty():
                self.refresh()
        self.timer = self.after(1000, self.poll)

    def on_destroy(self, event):
        if event.widget is not self:
            return
        self.destroyed = True
        if self.timer:
            self.after_cancel(self.timer)
