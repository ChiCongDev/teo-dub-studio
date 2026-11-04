"""Xếp theo lượt thích on a link's tab (owner 03/11/2026): read the link's channel again (the reading of "Lấy danh sách", same
browser profile), show the next videos in the new order, and only when the owner presses the button save it. No AI call."""
import queue
import threading
import tkinter as tk
from tkinter import ttk

from teodub.campaign_append_ui import default_limit
from teodub.campaign_likes import apply_likes, preview
from teodub.channel_browser import BROWSER_LABELS, profile_directory
from teodub.channel_discovery import MAX_SCAN_VIDEOS, scan_channel

POLL_MS = 200


class LikesOrderDialog(tk.Toplevel):
    def __init__(self, tab):
        manifest = tab.manifest or {}
        discovery = manifest.get('discovery') or {}
        channel = discovery.get('channel') if isinstance(discovery, dict) else None
        if not channel:
            raise ValueError('Link này không tạo từ link kênh Douyin nên chưa có kênh để đọc lượt thích.')
        super().__init__(tab)
        self.tab, self.studio, self.directory, self.channel = tab, tab.studio, tab.directory, channel
        self.total = len(manifest.get('items', []))
        self.name = str(manifest.get('name') or self.directory.name[:8])
        self.messages = queue.Queue()
        self.cancel = threading.Event()
        self.scanning = False
        self.records = []
        self.closed = False
        self.timer = None
        self.title('Xếp theo lượt thích')
        self.geometry('900x680')
        self.minsize(760, 600)
        self.transient(tab.winfo_toplevel())
        self.limit = tk.StringVar(self, str(default_limit(self.total)))
        self.visible = tk.BooleanVar(self, True)
        self.status = tk.StringVar(self, 'Bấm Lấy số lượt thích. Tèo chỉ đọc kênh; chưa đổi thứ tự nào.')
        self.summary = tk.StringVar(self)
        shell = ttk.Frame(self, padding=16)
        shell.pack(fill='both', expand=True)
        ttk.Label(shell, text=f'Xếp theo lượt thích cho "{self.name}"', font=('Segoe UI', 16, 'bold')).pack(anchor='w')
        ttk.Label(shell, text='Link kênh: '+channel, wraplength=840).pack(anchor='w', pady=(4, 2))
        ttk.Label(shell, text='Tèo đọc lại kênh để lấy số lượt thích Douyin báo cho từng video, rồi làm các video CHƯA XONG theo thứ tự nhiều thích '
                  'trước. Số Video, thành phẩm và lịch đăng giữ nguyên (không đánh số lại); video đã xong không đổi; video chưa đọc được '
                  'số lượt thích làm sau, theo thứ tự cũ. Douyin ẩn lượt xem nên dùng lượt thích.', wraplength=840).pack(anchor='w', pady=(0, 8))
        fields = ttk.Frame(shell)
        fields.pack(fill='x')
        ttk.Label(fields, text='Tối đa').pack(side='left')
        ttk.Spinbox(fields, from_=1, to=MAX_SCAN_VIDEOS, textvariable=self.limit, width=8).pack(side='left', padx=(6, 4))
        ttk.Label(fields, text='video đọc từ kênh (đặt cao hơn số video của kênh để lấy hết)').pack(side='left')
        ttk.Checkbutton(shell, variable=self.visible,
                        text='Hiện trình duyệt để đăng nhập/xác minh (nên bật: Douyin chỉ cho khách xem một phần kênh)').pack(anchor='w', pady=(8, 0))
        ttk.Label(shell, text='Trình duyệt: '+BROWSER_LABELS[self.browser()]+' (đổi ở tab "＋ Thêm link").', wraplength=840).pack(anchor='w', pady=(2, 8))
        buttons = ttk.Frame(shell)
        buttons.pack(fill='x')
        self.scan_button = ttk.Button(buttons, text='Lấy số lượt thích', command=self.scan)
        self.scan_button.pack(side='left')
        self.stop_button = ttk.Button(buttons, text='Dừng đọc', command=self.stop, state='disabled')
        self.stop_button.pack(side='left', padx=6)
        ttk.Label(shell, textvariable=self.status, wraplength=840, foreground='#164b78').pack(anchor='w', pady=(10, 2))
        ttk.Label(shell, textvariable=self.summary, wraplength=840, font=('Segoe UI', 11, 'bold')).pack(anchor='w', pady=(2, 6))
        # The button row is packed before the list so a short window shrinks the list, never the buttons.
        actions = ttk.Frame(shell)
        actions.pack(side='bottom', fill='x', pady=(8, 0))
        self.apply_button = ttk.Button(actions, text='Áp dụng thứ tự này', command=self.apply, state='disabled')
        self.apply_button.pack(side='left')
        ttk.Button(actions, text='Đóng', command=self.close).pack(side='right')
        body = ttk.Frame(shell)
        body.pack(fill='both', expand=True)
        self.tree = ttk.Treeview(body, columns=('number', 'likes', 'title'), show='headings', height=6)
        for key, label, width in (('number', 'Video', 80), ('likes', 'Lượt thích', 100), ('title', 'Tiêu đề (các video sẽ làm tiếp theo)', 600)):
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, minwidth=60)
        self.tree.pack(side='left', fill='both', expand=True)
        scroll = ttk.Scrollbar(body, orient='vertical', command=self.tree.yview)
        scroll.pack(side='right', fill='y')
        self.tree.configure(yscrollcommand=scroll.set)
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.timer = self.after(POLL_MS, self.poll)

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
            self.status.set('Đang có video đơn, cửa sổ đăng nhập hoặc một lượt đọc kênh khác. Đợi xong rồi bấm Lấy số lượt thích.')
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
        self.records = []
        self.summary.set('')
        self.tree.delete(*self.tree.get_children())
        self.scan_button.configure(state='disabled')
        self.stop_button.configure(state='normal')
        self.apply_button.configure(state='disabled')
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
            self.status.set('Đã dừng đọc kênh.' if self.cancel.is_set() else 'Chưa đọc được kênh: '+value)
            return
        self.records = value['items']
        try:
            plan = preview(self.directory, self.records)
        except (OSError, ValueError, KeyError) as exc:
            self.status.set('Chưa xem trước được: '+str(exc))
            return
        self.status.set(value['message'])
        self.summary.set(f'Đọc được số lượt thích của {plan["matched"]}/{plan["total"]} video của link · còn {plan["pending"]} video chưa xong'
                         f' ({plan["unknown"]} chưa có số lượt thích, làm sau).'
                         +('' if plan['changed'] else ' Thứ tự làm tiếp theo không đổi.'))
        for row in plan['first']:
            self.tree.insert('', 'end', values=(f'Video {row["number"]}', '—' if row['likes'] is None else f'{row["likes"]:,}'.replace(',', '.'),
                                                row['title'] or '(Chưa có tiêu đề)'))
        if plan['matched']:
            self.apply_button.configure(state='normal')

    def apply(self):
        if self.scanning or not self.records:
            return
        try:
            result = apply_likes(self.directory, self.records)
        except (OSError, ValueError) as exc:
            self.status.set('Chưa đổi được: '+str(exc))
            return
        self.tab.message.set(f'Đã xếp theo lượt thích ({result["matched"]}/{result["total"]} video có số). Từ lượt tiếp theo, link làm video '
                             'nhiều lượt thích trước; số Video và các video đã xong giữ nguyên.')
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
