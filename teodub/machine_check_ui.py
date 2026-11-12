"""The "Kiểm tra máy" window: one row per check, a fix button where there is one. Opened by the button in the top
bar and, once, on the first start of a PC where something is missing."""
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import ttk

from teodub import machine_check

PROJECT = Path(__file__).resolve().parents[1]
TITLES = ('Google Chrome', 'Dịch bằng ChatGPT (Codex)', 'Mô hình trên máy', 'Thư mục cài đặt', 'Thư viện Microsoft Visual C++',
          'Dịch thử một câu')


class MachineCheckWindow(tk.Toplevel):
    def __init__(self, master, project=PROJECT):
        super().__init__(master)
        self.title('Kiểm tra máy')
        self.resizable(False, False)
        self.project = project
        self.results = queue.Queue()
        self.rows = {}
        self.busy = set()
        body = ttk.Frame(self, padding=14)
        body.pack(fill='both', expand=True)
        ttk.Label(body, text='Kiểm tra máy này đã sẵn sàng chạy Tèo chưa', font=('Segoe UI', 12, 'bold')).grid(
            row=0, column=0, columnspan=3, sticky='w')
        for index, title in enumerate(TITLES, 1):
            mark = ttk.Label(body, text='…', width=2, font=('Segoe UI', 12, 'bold'))
            mark.grid(row=index, column=0, sticky='nw', pady=(10, 0))
            text = ttk.Label(body, text=title, wraplength=560, justify='left')
            text.grid(row=index, column=1, sticky='w', padx=8, pady=(10, 0))
            button = ttk.Button(body, text='', width=20)
            button.grid(row=index, column=2, sticky='ne', pady=(8, 0))
            button.grid_remove()
            self.rows[title] = dict(mark=mark, text=text, button=button)
        self.rows['Dịch thử một câu']['text'].configure(text='Dịch thử một câu\nChưa chạy. Bấm “Dịch thử” để thử một câu thật qua tài khoản ChatGPT.')
        self.set_button('Dịch thử một câu', 'Dịch thử', self.run_translation_test)
        footer = ttk.Frame(body)
        footer.grid(row=len(TITLES)+1, column=0, columnspan=3, sticky='e', pady=(16, 0))
        ttk.Button(footer, text='Kiểm tra lại', command=self.refresh).pack(side='left')
        ttk.Button(footer, text='Đóng', command=self.destroy).pack(side='left', padx=(8, 0))
        self.after(150, self.drain)
        self.refresh()

    def set_button(self, title, label, command):
        button = self.rows[title]['button']
        button.configure(text=label, command=command)
        button.grid()

    def hide_button(self, title):
        self.rows[title]['button'].grid_remove()

    def show(self, check):
        row = self.rows[check.title]
        row['mark'].configure(text='✓' if check.ok else '✗', foreground='#1a7f37' if check.ok else '#b42318')
        row['text'].configure(text=check.title+'\n'+check.message)
        if check.action == 'login':
            self.set_button(check.title, 'Đăng nhập ChatGPT', self.run_login)
        elif check.title != 'Dịch thử một câu':
            self.hide_button(check.title)

    def refresh(self):
        if 'quick' in self.busy:
            return
        self.busy.add('quick')
        for title in TITLES[:-1]:
            self.rows[title]['mark'].configure(text='…', foreground='')
        threading.Thread(target=lambda: self.results.put(('quick', machine_check.quick_checks(self.project))), daemon=True).start()

    def run_translation_test(self):
        if 'translate' in self.busy:
            return
        self.busy.add('translate')
        row = self.rows['Dịch thử một câu']
        row['mark'].configure(text='…', foreground='')
        row['text'].configure(text='Dịch thử một câu\nĐang dịch thử, chờ vài giây…')
        row['button'].configure(state='disabled')
        threading.Thread(target=lambda: self.results.put(('translate', machine_check.test_translation())), daemon=True).start()

    def run_login(self):
        if 'login' in self.busy:
            return
        title = 'Dịch bằng ChatGPT (Codex)'
        try:
            process = machine_check.start_codex_login()
        except (OSError, ValueError) as exc:
            self.rows[title]['text'].configure(text=title+'\n'+str(exc))
            return
        self.busy.add('login')
        self.rows[title]['text'].configure(text=title+'\nĐang chờ bạn đăng nhập trên trình duyệt vừa mở (tối đa 10 phút)…')
        self.rows[title]['button'].configure(state='disabled')

        def wait():
            try:
                process.wait(timeout=600)
            except Exception:
                process.kill()
            self.results.put(('login', None))
        threading.Thread(target=wait, daemon=True).start()

    def drain(self):
        try:
            while True:
                kind, value = self.results.get_nowait()
                self.busy.discard(kind)
                if kind == 'quick':
                    for check in value:
                        self.show(check)
                elif kind == 'translate':
                    row = self.rows['Dịch thử một câu']
                    row['button'].configure(state='normal')
                    self.show(value)
                elif kind == 'login':
                    self.rows['Dịch bằng ChatGPT (Codex)']['button'].configure(state='normal')
                    self.refresh()
        except queue.Empty:
            pass
        if self.winfo_exists():
            self.after(150, self.drain)


def open_window(master, project=PROJECT):
    window = MachineCheckWindow(master, project)
    window.transient(master)
    return window


def offer_on_first_run(root, data_dir, project=PROJECT):
    """Once per data folder: if something is missing, open the window by itself. Never blocks or raises."""
    found = []

    def work():
        try:
            if machine_check.first_run_pending(data_dir):
                machine_check.mark_first_run_shown(data_dir)
                found.append(machine_check.needs_attention(machine_check.quick_checks(project)))
            else:
                found.append(False)
        except Exception:
            found.append(False)

    def poll():
        if not found:
            root.after(500, poll)
        elif found[0]:
            open_window(root, project)

    def start():
        # Only once the window is up: building the UI must not start any process (tests rely on it too).
        threading.Thread(target=work, daemon=True).start()
        root.after(500, poll)

    root.after(2500, start)
