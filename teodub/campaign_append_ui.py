"""Lấy thêm video mới on a link's tab (owner 30/10/2026): read the link's channel again and add to that same link only the
videos no campaign has yet. The reading is the one of "Lấy danh sách" (same browser profile, so a Douyin login made with
"Đăng nhập Chrome" is used); nothing is added until the owner presses the add button. No AI call."""
import queue
import threading
import tkinter as tk
from tkinter import ttk

from teodub.campaign import likes_first
from teodub.campaign_append import known_urls, split_known
from teodub.channel_browser import BROWSER_LABELS, profile_directory
from teodub.channel_discovery import MAX_SCAN_VIDEOS, scan_channel

POLL_MS = 200
RUN_OUTCOMES = {'already': 'Link này đang chạy.',
                'joined': 'Link vào lượt; tới lượt là làm (mỗi link một video).',
                'handoff': 'Link đang chạy làm xong video hiện tại thì tới lượt link này.',
                'started': 'Đã bắt đầu chạy link này.'}


def default_limit(total):
    """A channel is read newest first and the videos already in the link come first: ask for at least twice as many."""
    return min(MAX_SCAN_VIDEOS, max(1000, total*2))


class AppendVideosDialog(tk.Toplevel):
    def __init__(self, tab):
        manifest = tab.manifest or {}
        discovery = manifest.get('discovery') or {}
        channel = discovery.get('channel') if isinstance(discovery, dict) else None
        if not channel:
            raise ValueError('Link này không tạo từ link kênh Douyin nên chưa có kênh để lấy thêm video.')
        super().__init__(tab)
        self.tab, self.studio, self.directory, self.channel = tab, tab.studio, tab.directory, channel
        self.total = len(manifest.get('items', []))
        self.name = str(manifest.get('name') or self.directory.name[:8])
        self.messages = queue.Queue()
        self.cancel = threading.Event()
        self.scanning = False
        self.fresh = []
        self.closed = False
        self.timer = None
        self.title('Lấy thêm video mới')
        self.geometry('900x560')
        self.minsize(760, 480)
        self.transient(tab.winfo_toplevel())
        self.limit = tk.StringVar(self, str(default_limit(self.total)))
        self.visible = tk.BooleanVar(self, True)
        self.run_after = tk.BooleanVar(self, True)
        self.status = tk.StringVar(self, 'Bấm Lấy danh sách. Tèo chỉ đọc kênh; chưa thêm gì vào link.')
        self.summary = tk.StringVar(self)
        shell = ttk.Frame(self, padding=16)
        shell.pack(fill='both', expand=True)
        ttk.Label(shell, text=f'Lấy thêm video mới cho "{self.name}"', font=('Segoe UI', 16, 'bold')).pack(anchor='w')
        ttk.Label(shell, text='Link kênh: '+channel, wraplength=840).pack(anchor='w', pady=(4, 2))
        ttk.Label(shell, text=f'Link đang có {self.total} video. Tèo đọc lại kênh rồi chỉ giữ những video chưa có trong BẤT KỲ chiến dịch nào '
                  'của Tèo; video mới được thêm vào cuối link này (Video '+str(self.total+1)+' trở đi), video cũ giữ nguyên.',
                  wraplength=840).pack(anchor='w', pady=(0, 8))
        fields = ttk.Frame(shell)
        fields.pack(fill='x')
        ttk.Label(fields, text='Tối đa').pack(side='left')
        self.limit_box = ttk.Spinbox(fields, from_=1, to=MAX_SCAN_VIDEOS, textvariable=self.limit, width=8)
        self.limit_box.pack(side='left', padx=(6, 4))
        ttk.Label(fields, text='video đọc từ kênh (mới nhất trước; đặt cao hơn số video của kênh để lấy hết)').pack(side='left')
        ttk.Checkbutton(shell, variable=self.visible,
                        text='Hiện trình duyệt để đăng nhập/xác minh (nên bật: Douyin chỉ cho khách xem một phần kênh)').pack(anchor='w', pady=(8, 0))
        ttk.Label(shell, text='Trình duyệt: '+self.browser_label()+' (đổi ở tab "＋ Thêm link"). Nếu chưa đăng nhập Douyin, '
                  'bấm "Đăng nhập Chrome" ở tab đó trước.', wraplength=840).pack(anchor='w', pady=(2, 8))
        buttons = ttk.Frame(shell)
        buttons.pack(fill='x')
        self.scan_button = ttk.Button(buttons, text='Lấy danh sách', command=self.scan)
        self.scan_button.pack(side='left')
        self.stop_button = ttk.Button(buttons, text='Dừng đọc', command=self.stop, state='disabled')
        self.stop_button.pack(side='left', padx=6)
        ttk.Label(shell, textvariable=self.status, wraplength=840, foreground='#164b78').pack(anchor='w', pady=(10, 2))
        ttk.Label(shell, textvariable=self.summary, wraplength=840, font=('Segoe UI', 11, 'bold')).pack(anchor='w', pady=(2, 6))
        body = ttk.Frame(shell)
        body.pack(fill='both', expand=True)
        self.tree = ttk.Treeview(body, columns=('number', 'title', 'url'), show='headings', height=8)
        for key, label, width in (('number', 'Sẽ là', 80), ('title', 'Tiêu đề', 340), ('url', 'Link video', 360)):
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, minwidth=60)
        self.tree.pack(side='left', fill='both', expand=True)
        scroll = ttk.Scrollbar(body, orient='vertical', command=self.tree.yview)
        scroll.pack(side='right', fill='y')
        self.tree.configure(yscrollcommand=scroll.set)
        actions = ttk.Frame(shell)
        actions.pack(side='bottom', fill='x', pady=(8, 0))
        self.add_button = ttk.Button(actions, text='Thêm video mới vào link', command=self.add, state='disabled')
        self.add_button.pack(side='left')
        ttk.Checkbutton(actions, variable=self.run_after, text='Chạy link này ngay sau khi thêm').pack(side='left', padx=12)
        ttk.Button(actions, text='Đóng', command=self.close).pack(side='right')
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.timer = self.after(POLL_MS, self.poll)

    def browser_label(self):
        return BROWSER_LABELS[self.browser()]

    def browser(self):
        try:
            return self.tab.area.campaign_tab.selected_browser()
        except (AttributeError, ValueError):
            return 'chrome'

    def scan(self):
        if self.scanning:
            return
        studio, workspace = self.studio, getattr(self.tab.area, 'campaign_tab', None)
        if studio.process is not None or getattr(studio, 'login_pending', False) or getattr(workspace, 'scanning', False):
            self.status.set('Đang có video đơn, cửa sổ đăng nhập hoặc một lượt đọc kênh khác. Đợi xong rồi bấm Lấy danh sách.')
            return
        try:
            limit = int(self.limit.get())
        except ValueError:
            self.status.set('Ô Tối đa phải là một số.')
            return
        if not 1 <= limit <= MAX_SCAN_VIDEOS:
            self.status.set('Chọn từ 1 đến 10.000 video.')
            return
        browser, visible = self.browser(), self.visible.get()
        profile = profile_directory(self.studio.data_dir/'douyin-channel-browser', browser)
        self.scanning = True
        self.cancel.clear()
        self.fresh = []
        self.summary.set('')
        self.tree.delete(*self.tree.get_children())
        self.scan_button.configure(state='disabled')
        self.stop_button.configure(state='normal')
        self.add_button.configure(state='disabled')
        self.status.set('Đang mở '+BROWSER_LABELS[browser]+(' · hiện cửa sổ.' if visible else ' · chạy ẩn.'))

        def work():
            try:
                result = scan_channel(self.channel, profile, limit=limit, visible=visible, cancel=self.cancel, browser=browser,
                                      progress=lambda message: self.messages.put(('progress', str(message))))
                self.messages.put(('done', result))
            except Exception as exc:  # The reading's own refusals and a stop (Dừng đọc) are shown, never raised here.
                self.messages.put(('error', str(exc)))
        threading.Thread(target=work, daemon=True).start()

    def stop(self):
        self.cancel.set()
        self.status.set('Đang dừng đọc kênh…')

    def poll(self):
        self.timer = None
        if self.closed:
            return
        while True:
            try:
                kind, value = self.messages.get_nowait()
            except queue.Empty:
                break
            if kind == 'progress':
                self.status.set(value)
            else:
                self.finish(kind, value)
        self.timer = self.after(POLL_MS, self.poll)

    def finish(self, kind, value):
        self.scanning = False
        self.scan_button.configure(state='normal')
        self.stop_button.configure(state='disabled')
        if kind == 'error':
            self.status.set('Đã dừng đọc kênh.' if self.cancel.is_set() else 'Chưa lấy được danh sách: '+value)
            return
        known = known_urls(self.studio.data_dir)
        self.fresh, already = split_known(value['items'], known)
        self.fresh = likes_first(self.fresh)  # The same order `append_videos` numbers them in.
        self.status.set(value['message'])
        self.summary.set(f'Đọc được {len(value["items"])} video · {already} đã có trong Tèo · {len(self.fresh)} video mới.'
                         +('' if self.fresh else ' Không có gì để thêm.'))
        for offset, row in enumerate(self.fresh):
            self.tree.insert('', 'end', values=(f'Video {self.total+1+offset}', row['title'] or '(Chưa có tiêu đề)', row['url']))
        if self.fresh:
            self.add_button.configure(state='normal', text=f'Thêm {len(self.fresh)} video mới vào link')

    def add(self):
        if self.scanning or not self.fresh:
            return
        batch = getattr(self.studio, 'batch_controller', None)
        if batch is None:
            return
        try:
            count = batch.append_videos(self.directory, self.fresh)
        except (OSError, ValueError) as exc:
            self.status.set('Chưa thêm được: '+str(exc))
            return
        message = (f'Đã thêm {count} video mới cho link này (nếu link đang có video đang làm, chúng hiện trong bảng ngay sau video đó). '
                   'Các video cũ giữ nguyên.')
        if self.run_after.get():
            try:
                message += ' '+RUN_OUTCOMES.get(batch.run_link(self.directory), '')
            except (OSError, ValueError) as exc:
                message += ' Chưa chạy được: '+str(exc)+' Bấm Chạy khi sẵn sàng.'
        else:
            message += ' Bấm Chạy khi muốn làm các video mới.'
        self.tab.message.set(message)
        self.studio.refresh_buttons()
        self.tab.refresh(full=True)
        self.close()

    def close(self):
        self.closed = True
        self.cancel.set()
        if self.timer is not None:
            try:
                self.after_cancel(self.timer)
            except tk.TclError:
                pass
            self.timer = None
        self.destroy()
