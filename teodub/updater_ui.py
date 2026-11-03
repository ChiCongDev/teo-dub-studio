"""The "Cập nhật phiên bản" window, the "Có bản mới" notice in the top bar, and the one-time result message."""
from pathlib import Path
import queue
import tempfile
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from teodub import updater

PROJECT = updater.PROJECT


class UpdateWindow(tk.Toplevel):
    def __init__(self, master, studio, busy_check, info=None, project=PROJECT):
        super().__init__(master)
        self.title('Cập nhật phiên bản')
        self.resizable(False, False)
        self.studio, self.busy_check, self.project = studio, busy_check, Path(project)
        self.info = info if info and info.get('status') == 'newer' else None
        self.events = queue.Queue()
        self.working = False
        allowed, why = updater.can_update_here(self.project)
        self.allowed = allowed
        body = ttk.Frame(self, padding=14)
        body.pack(fill='both', expand=True)
        self.current = updater.current_version()
        ttk.Label(body, text=f'Phiên bản đang dùng: {self.current}', font=('Segoe UI', 12, 'bold')).grid(row=0, column=0, columnspan=3, sticky='w')
        self.status = tk.StringVar(value=why or 'Bấm “Kiểm tra bản mới” để xem có bản cập nhật không.')
        ttk.Label(body, textvariable=self.status, wraplength=560, justify='left').grid(row=1, column=0, columnspan=3, sticky='w', pady=(8, 6))
        self.notes = tk.Text(body, width=72, height=10, wrap='word', state='disabled', relief='solid', borderwidth=1,
                             font=('Segoe UI', 10))
        self.notes.grid(row=2, column=0, columnspan=3, sticky='ew')
        buttons = ttk.Frame(body)
        buttons.grid(row=3, column=0, columnspan=3, sticky='ew', pady=(10, 0))
        self.check_button = ttk.Button(buttons, text='Kiểm tra bản mới', command=self.run_check)
        self.check_button.pack(side='left')
        self.update_button = ttk.Button(buttons, text='Cập nhật', command=self.run_update, state='disabled')
        self.update_button.pack(side='left', padx=8)
        ttk.Button(buttons, text='Để sau', command=self.destroy).pack(side='right')
        ttk.Separator(body).grid(row=4, column=0, columnspan=3, sticky='ew', pady=12)
        ttk.Label(body, text='Quay lại bản trước (nếu bản mới gặp lỗi):').grid(row=5, column=0, sticky='w')
        self.choice = tk.StringVar()
        self.picker = ttk.Combobox(body, textvariable=self.choice, state='readonly', width=24)
        self.picker.grid(row=5, column=1, sticky='w', padx=8)
        self.rollback_button = ttk.Button(body, text='Quay lại bản này', command=self.run_rollback)
        self.rollback_button.grid(row=5, column=2, sticky='e')
        self.saved = updater.previous_versions(self.project) if allowed else []
        self.picker.configure(values=[version if path.name == version else f'{version} ({path.name})' for version, path in self.saved])
        if self.saved:
            self.picker.current(0)
        if not self.saved or not allowed:
            self.rollback_button.configure(state='disabled')
        if not allowed:
            self.check_button.configure(state='disabled')
        self.after(150, self.drain)
        if self.info:
            self.show_info(self.info)
        elif allowed:
            self.run_check()

    def show_notes(self, text):
        self.notes.configure(state='normal')
        self.notes.delete('1.0', 'end')
        self.notes.insert('1.0', text)
        self.notes.configure(state='disabled')

    def show_info(self, info):
        self.info = info
        self.status.set(f'Có bản mới {info["version"]} (bạn đang dùng {self.current}). Ghi chú thay đổi:')
        self.show_notes(info.get('notes') or '(Chủ dự án chưa ghi chú thay đổi.)')
        self.update_button.configure(state='normal' if self.allowed and not self.working else 'disabled')

    def run_check(self):
        if self.working:
            return
        self.status.set('Đang kiểm tra…')
        self.check_button.configure(state='disabled')
        updater.check_in_background(lambda result: self.events.put(('check', result)), timeout=8)

    def refuse_if_busy(self, what):
        self.withdraw()  # this window itself would count as an open editing window
        reasons = self.busy_check()
        if reasons:
            self.deiconify()
            messagebox.showinfo('Chưa '+what+' được lúc này', 'Tèo đang bận:\n\n- '+'\n- '.join(reasons[:6]) +
                                '\n\nXong việc hoặc dừng việc đó rồi bấm lại.', parent=self)
            return True
        self.deiconify()
        return False

    def run_update(self):
        if self.working or not self.info or not self.allowed:
            return
        if self.refuse_if_busy('cập nhật'):
            return
        if not messagebox.askyesno('Cập nhật Tèo', f'Cập nhật lên bản {self.info["version"]}?\n\nTèo sẽ tự đóng, thay mã mới, '
                'tự kiểm tra rồi mở lại (khoảng 1 phút). Dữ liệu, mô hình và đăng nhập được giữ nguyên. '
                'Lịch đăng đã hẹn sẽ nối lại sau khi Tèo mở lại.', parent=self):
            return
        self.working = True
        self.update_button.configure(state='disabled')
        self.check_button.configure(state='disabled')
        self.rollback_button.configure(state='disabled')
        info = dict(self.info)

        def work():
            try:
                folder = Path(tempfile.mkdtemp(prefix='teo-download-'))
                path, sha = updater.download(info, folder, progress=lambda done, total: self.events.put(('progress', (done, total))))
                self.events.put(('downloaded', (info, path, sha)))
            except ValueError as exc:
                self.events.put(('error', str(exc)))
        threading.Thread(target=work, daemon=True).start()

    def run_rollback(self):
        if self.working or not self.saved or not self.allowed:
            return
        version, path = self.saved[self.picker.current()]
        if self.refuse_if_busy('quay lại bản cũ'):
            return
        if not messagebox.askyesno('Quay lại bản trước', f'Quay lại bản {version}?\n\nTèo sẽ tự đóng, đổi về bản đó rồi mở lại. '
                                   'Dữ liệu và đăng nhập được giữ nguyên.', parent=self):
            return
        self.begin_apply(source_dir=path, new_version=version)

    def begin_apply(self, **kwargs):
        try:
            updater.start_apply(self.project, data_dir=self.studio.data_dir, **kwargs)
        except (OSError, ValueError) as exc:
            self.working = False
            self.status.set(str(exc))
            return
        self.status.set('Đang đóng Tèo để thay mã mới…')
        self.studio.close()

    def drain(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == 'check':
                    self.check_button.configure(state='normal' if self.allowed else 'disabled')
                    if value['status'] == 'newer':
                        self.show_info(value)
                    else:
                        self.status.set(value.get('message', ''))
                        self.update_button.configure(state='disabled')
                elif kind == 'progress':
                    done, total = value
                    self.status.set('Đang tải bản mới… '+(f'{done*100//total}%' if total else f'{done//1024//1024} MB'))
                elif kind == 'error':
                    self.working = False
                    self.status.set(value)
                    self.check_button.configure(state='normal')
                    self.update_button.configure(state='normal' if self.info else 'disabled')
                    self.rollback_button.configure(state='normal' if self.saved else 'disabled')
                elif kind == 'downloaded':
                    info, path, sha = value
                    self.begin_apply(zip_path=path, sha256=sha, new_version=info['version'])
        except queue.Empty:
            pass
        if self.winfo_exists():
            self.after(150, self.drain)


def install(root, bar, studio, busy_check, project=PROJECT):
    """Add the button and the 'Có bản mới' notice to the top bar; check quietly on start; show the last update's result."""
    holder = {}
    notice = ttk.Button(bar, text='')

    def open_window(info=None):
        existing = holder.get('window')
        if existing is not None and existing.winfo_exists():
            existing.lift()
            return
        holder['window'] = UpdateWindow(root, studio, busy_check, info, project)

    ttk.Button(bar, text='Cập nhật phiên bản', command=open_window).pack(side='right', padx=(0, 6))
    events = queue.Queue()

    def show_news(result):
        notice.configure(text=f'Có bản mới {result["version"]} – Xem', command=lambda: open_window(result))
        notice.pack(side='right', padx=(0, 6))

    def poll():
        try:
            show_news(events.get_nowait())
        except queue.Empty:
            if holder.get('waiting'):
                root.after(500, poll)

    def start_check():
        allowed, _ = updater.can_update_here(project)
        if not allowed:
            return
        holder['waiting'] = True

        def done(result):
            if result['status'] == 'newer':
                events.put(result)
            holder['waiting'] = False
        updater.check_in_background(done, timeout=5)
        root.after(500, poll)

    def announce_last():
        result = updater.pop_last_result(project)
        if result:
            (messagebox.showinfo if result.get('ok') else messagebox.showwarning)(
                'Cập nhật phiên bản', result.get('message', ''), parent=root)

    root.after(1500, announce_last)
    root.after(3000, start_check)
    return open_window
