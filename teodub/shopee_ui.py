"""Owner 25/11/2026 (Tk): the "Mở Shopee" button of Quản lý Page and the question shown when a post falls due while that Chrome is open.

Question (approved 25/11): "Tới giờ đăng bài. Chrome Shopee sẽ tự đóng sau 15 phút." with a countdown.
  Đồng ý      -> that Chrome is closed now (gently), the post goes as soon as it is closed.
  Không       -> nothing is closed; the post waits; asked again after 15 minutes.
  no answer   -> after 15 minutes that Chrome is closed (gently), then the post goes.
Only a Chrome the button opened can be closed by Tèo; one opened another way gets a notice only ("Đã hiểu")."""
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

from teodub import shopee_browser
from teodub.page_schedule_shopee import outside_chrome

ASK_SECONDS = 15*60  # No answer for this long: the Shopee Chrome is closed.
SNOOZE_SECONDS = 15*60  # "Không" / "Đã hiểu": asked again after this long while the post still waits.


def account_name(tab, account_id):
    try:
        return next(row['name'] for row in tab.store.load()['accounts'] if row['id'] == account_id)
    except Exception:
        return account_id


def next_due_text(tab, account_id):
    """The time (UTC+7) of the account's next armed post, or ''."""
    try:
        from teodub.page_schedule import display_time
        due = [row['due_at'] for row in tab.get_page_scheduler().store.list(account_id) if row['state'] == 'armed']
        return display_time(min(due)) if due else ''
    except Exception:
        return ''


def open_shopee(tab):
    """The button. Refuses (with a box saying why) when Tèo's own Chrome of the account is open or a post of it is being worked on or
    due within SOON_MINUTES. True when the plain Chrome was opened."""
    account_id = tab.account_id
    if tab.destroyed or tab.studio.closing or tab.studio.closed:
        return False
    if not account_id:
        tab.status.set('Chọn tài khoản Facebook trước khi bấm Mở Shopee.')
        return False
    name = account_name(tab, account_id)
    session = tab.sessions.get(account_id)
    if session and session.pending:
        messagebox.showinfo('Mở Shopee', f'Chrome của Tèo cho "{name}" đang mở.\nTắt Chrome đó (bấm X) rồi bấm Mở Shopee lại.',
                            parent=tab)
        return False
    if tab.posting_now(account_id) or tab.schedule_busy_within(account_id, minutes=shopee_browser.SOON_MINUTES):
        when = next_due_text(tab, account_id)
        messagebox.showinfo('Mở Shopee', f'"{name}" đang đăng bài hoặc sắp tới giờ đăng (trong {shopee_browser.SOON_MINUTES} phút'
                            + (f', lúc {when}' if when else '')+').\nMở Shopee sau khi bài đăng xong.', parent=tab)
        return False
    try:
        chrome_dir = tab.store.profile_directory(account_id)
        shopee_browser.open_shopee(tab.studio.data_dir, account_id, chrome_dir)
    except (OSError, ValueError) as exc:
        messagebox.showinfo('Mở Shopee', str(exc), parent=tab)
        return False
    tab.chrome_log(account_id, 'Mở Shopee: Chrome thường của tài khoản (Tèo không điều khiển). Tới giờ đăng, Tèo hỏi trước khi đóng.')
    tab.status.set(f'Đã mở Shopee cho "{name}". Dùng xong thì tắt Chrome đó. Tới giờ đăng, Tèo hỏi trước rồi mới tự đóng.')
    return True


class ShopeeAsk:
    """The question for one account (one at a time). Its 1-second tick ends it as soon as that Chrome is closed by anyone."""

    def __init__(self, tab, account_id, closable):
        self.tab = tab
        self.account_id = account_id
        self.closable = closable
        self.deadline = time.monotonic()+ASK_SECONDS
        self.closing = None  # the thread closing the Chrome
        self.closed = None  # its answer: True closed, False still open
        self.failed = False
        name = account_name(tab, account_id)
        self.window = window = tk.Toplevel(tab)
        window.title('Tới giờ đăng bài')
        window.resizable(False, False)
        window.attributes('-topmost', True)
        window.protocol('WM_DELETE_WINDOW', self.no)  # The box's own X = Không.
        ttk.Label(window, text=f'Tới giờ đăng bài ({name})', font=('Segoe UI', 11, 'bold')).pack(padx=24, pady=(16, 4))
        self.text = tk.StringVar()
        ttk.Label(window, textvariable=self.text, wraplength=400, justify='center').pack(padx=24)
        self.clock = tk.StringVar()
        ttk.Label(window, textvariable=self.clock, font=('Segoe UI', 10, 'bold')).pack(pady=(6, 10))
        self.buttons = ttk.Frame(window)
        self.buttons.pack(pady=(0, 16))
        if closable:
            self.text.set('Chrome Shopee sẽ tự đóng sau 15 phút.')
            self.yes_button = ttk.Button(self.buttons, text='Đồng ý', command=self.yes)
            self.yes_button.pack(side='left', padx=8)
            self.no_button = ttk.Button(self.buttons, text='Không', command=self.no)
            self.no_button.pack(side='left', padx=8)
        else:
            self.text.set('Chrome của tài khoản này đang mở (không phải do nút Mở Shopee mở), nên Tèo không tự đóng được.\n'
                          'Tắt Chrome đó (bấm X) để Tèo đăng bài.')
            self.yes_button = None
            self.no_button = ttk.Button(self.buttons, text='Đã hiểu', command=self.no)
            self.no_button.pack(side='left', padx=8)
        window.after(1000, self.tick)
        self.show_clock()

    def alive(self):
        try:
            return bool(self.window.winfo_exists())
        except tk.TclError:
            return False

    def show_clock(self):
        if self.closable and not self.closing and not self.failed:
            left = max(0, int(self.deadline-time.monotonic()))
            self.clock.set(f'Còn lại: {left//60:02d}:{left%60:02d}')
        elif not self.closing:
            self.clock.set('')

    def end(self, *, snooze):
        self.tab.shopee_asks.pop(self.account_id, None)
        if snooze:
            self.tab.shopee_snooze[self.account_id] = time.monotonic()+SNOOZE_SECONDS
        else:
            self.tab.shopee_snooze.pop(self.account_id, None)
        try:
            self.window.destroy()
        except tk.TclError:
            pass

    def tick(self):
        if not self.alive():
            return
        if self.tab.destroyed:
            self.end(snooze=False)
            return
        if self.closing is not None:
            if self.closing.is_alive():
                self.window.after(500, self.tick)
                return
            self.closing = None
            if self.closed:
                self.tab.chrome_log(self.account_id, 'Đã đóng Chrome Shopee: bài đăng ngay.')
                self.end(snooze=False)
                return
            self.failed = True
            self.tab.chrome_log(self.account_id, 'Chrome Shopee không tự đóng được; chờ anh tắt (bấm X).')
            self.text.set('Chrome Shopee không tự đóng được (có thể đang hỏi điều gì đó).\nAnh tắt giúp Chrome đó (bấm X); '
                          'tắt xong là Tèo đăng bài.')
            for child in self.buttons.winfo_children():
                child.destroy()
            ttk.Button(self.buttons, text='Đã hiểu', command=self.no).pack(side='left', padx=8)
            self.clock.set('')
        elif not outside_chrome(self.tab, self.account_id):
            self.end(snooze=False)  # Closed by the owner: the waiting post goes on by itself.
            return
        elif self.closable and not self.failed and time.monotonic() >= self.deadline:
            self.start_close('Hết 15 phút không ai trả lời: Tèo tự đóng Chrome Shopee.')
        self.show_clock()
        self.window.after(1000, self.tick)

    def yes(self):
        if self.closing is None and self.closable and not self.failed:
            self.start_close('Anh bấm Đồng ý: Tèo đóng Chrome Shopee.')

    def no(self):
        if self.closing is not None:
            return  # Closing already: wait for its answer.
        if self.closable and not self.failed:
            self.tab.chrome_log(self.account_id, 'Anh bấm Không: chưa đóng Chrome Shopee, bài chờ; hỏi lại sau 15 phút.')
        self.end(snooze=True)

    def start_close(self, reason):
        self.tab.chrome_log(self.account_id, reason)
        for button in (self.yes_button, self.no_button):
            if button is not None:
                button.configure(state='disabled')
        self.text.set('Đang đóng Chrome Shopee…')
        self.clock.set('')
        data_dir, account_id = self.tab.studio.data_dir, self.account_id
        try:
            chrome_dir = self.tab.store.profile_directory(account_id)
        except (OSError, ValueError):
            self.closed = False
            self.closing = threading.Thread(target=lambda: None, daemon=True)
            self.closing.start()
            return

        def close():
            try:
                self.closed = shopee_browser.close_account_chrome(data_dir, account_id, chrome_dir)
            except Exception:
                self.closed = False

        self.closing = threading.Thread(target=close, daemon=True)
        self.closing.start()


def waiting(tab, account_id):
    """From the schedule controller, every second while a post of the account waits for its Shopee Chrome: shows the question (once;
    again 15 minutes after "Không"). Never raises."""
    try:
        if tab.destroyed or tab.studio.closing or tab.studio.closed:
            return
        ask = tab.shopee_asks.get(account_id)
        if ask is not None and ask.alive():
            return
        if tab.shopee_snooze.get(account_id, 0) > time.monotonic():
            return
        try:
            closable = bool(shopee_browser.opened_by_button(tab.studio.data_dir, account_id,
                                                            tab.store.profile_directory(account_id)))
        except Exception:
            closable = False
        tab.shopee_asks[account_id] = ShopeeAsk(tab, account_id, closable)
    except Exception:
        pass
