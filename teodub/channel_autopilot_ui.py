"""Channel sources: paste a Douyin channel, pick Pages; captions are always reviewed before posting."""
import tkinter as tk
from tkinter import ttk, messagebox

from teodub.channel_autopilot import DAILY_LIMIT, PER_SCAN, ChannelAutopilot
from teodub.facebook_accounts import FacebookAccounts

STATE_LABELS = {'active': 'Đang chạy', 'paused': 'Tạm dừng', 'needs_attention': 'Cần kiểm tra'}


class ChannelAutopilotTab(ttk.Frame):
    """Setup and status only; the desktop timer drives scans, campaigns and Page AI sources."""

    def __init__(self, parent, studio, campaign_tab, batch_controller, *, pilot=None):
        super().__init__(parent, padding=16)
        self.studio, self.campaign_tab = studio, campaign_tab
        self.pilot = pilot or ChannelAutopilot(studio, campaign_tab, batch_controller)
        self.link = tk.StringVar(self)
        self.shared = tk.BooleanVar(self, value=False)
        self.per_scan = tk.StringVar(self, value=str(PER_SCAN))
        self.status = tk.StringVar(self)
        self.blocked = tk.StringVar(self)
        self.folders = tk.StringVar(self)
        self.pages = []
        self.timer = None
        self.destroyed = False
        self.build()
        self.load_pages()
        self.refresh()
        self.bind('<Destroy>', self.on_destroy, add='+')
        self.timer = self.after(2000, self.poll)

    def build(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(4, weight=1)
        ttk.Label(self, text='Nguồn kênh tự động', font=('Segoe UI', 21, 'bold')).grid(row=0, column=0, sticky='w')
        ttk.Label(self, wraplength=1000, text=(
            'Dán link kênh Douyin và chọn Page. Tèo tự lấy video mới, lồng tiếng khi máy rảnh rồi để AI soạn caption. '
            'Tèo không tự đăng: mỗi sáng AI soạn bài, bạn vào tab Duyệt caption để đọc, sửa caption, '
            'duyệt rồi mới bật lịch.')).grid(row=1, column=0, sticky='w', pady=(4, 10))
        form = ttk.LabelFrame(self, text='Thêm nguồn kênh', padding=10)
        form.grid(row=2, column=0, sticky='ew')
        form.columnconfigure(1, weight=1)
        ttk.Label(form, text='Link kênh Douyin').grid(row=0, column=0, sticky='w', padx=(0, 8))
        ttk.Entry(form, textvariable=self.link).grid(row=0, column=1, columnspan=2, sticky='ew')
        ttk.Label(form, text='Page đích').grid(row=1, column=0, sticky='nw', padx=(0, 8), pady=(8, 0))
        self.page_list = tk.Listbox(form, selectmode='extended', height=5, exportselection=False)
        self.page_list.grid(row=1, column=1, sticky='ew', pady=(8, 0))
        ttk.Button(form, text='Làm mới Page', command=self.load_pages).grid(row=1, column=2, sticky='nw', padx=(8, 0), pady=(8, 0))
        ttk.Label(form, text='Giữ Ctrl để chọn nhiều Page. Mỗi video mới chỉ đi về một Page, chia lần lượt giữa các Page.',
                  wraplength=700).grid(row=2, column=1, columnspan=2, sticky='w')
        ttk.Checkbutton(form, variable=self.shared, text='Cho phép đăng cùng một video lên mọi Page đã chọn '
                        '(Meta dễ coi là nội dung trùng lặp)').grid(row=3, column=1, columnspan=2, sticky='w', pady=(6, 0))
        line = ttk.Frame(form)
        line.grid(row=4, column=1, columnspan=2, sticky='w', pady=(6, 0))
        ttk.Label(line, text='Video mới mỗi lượt quét').pack(side='left')
        ttk.Spinbox(line, from_=1, to=20, textvariable=self.per_scan, width=5).pack(side='left', padx=6)
        ttk.Label(line, text=f'Quét lại mỗi 24 giờ; tối đa {DAILY_LIMIT} video mới/ngày cho cả máy.').pack(side='left')
        ttk.Label(form, textvariable=self.folders, wraplength=850).grid(row=5, column=1, columnspan=2, sticky='w', pady=(6, 0))
        ttk.Button(form, text='Lưu nguồn và quét ngay', command=self.save).grid(row=6, column=1, sticky='w', pady=(8, 0))
        self.banner = ttk.Frame(self)
        self.banner.grid(row=3, column=0, sticky='ew', pady=(10, 0))
        ttk.Label(self.banner, textvariable=self.blocked, foreground='#a33', wraplength=780).pack(side='left')
        ttk.Button(self.banner, text='Tiếp tục xử lý tự động', command=self.resume_processing).pack(side='left', padx=8)
        table = ttk.Frame(self)
        table.grid(row=4, column=0, sticky='nsew', pady=(10, 0))
        table.columnconfigure(0, weight=1)
        table.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(table, columns=('channel', 'pages', 'state', 'taken', 'next', 'message'),
                                 show='headings', selectmode='browse', height=7)
        for key, label, width in (('channel', 'Kênh', 170), ('pages', 'Page đích', 170), ('state', 'Trạng thái', 100),
                                  ('taken', 'Đã lấy', 60), ('next', 'Quét tới', 95), ('message', 'Thông báo', 420)):
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, minwidth=50, stretch=key in ('pages', 'message'))
        self.tree.grid(row=0, column=0, sticky='nsew')
        scroll = ttk.Scrollbar(table, orient='vertical', command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky='ns')
        self.tree.configure(yscrollcommand=scroll.set)
        buttons = ttk.Frame(self)
        buttons.grid(row=5, column=0, sticky='w', pady=(8, 0))
        for text, command in (('Quét ngay', self.scan_now), ('Tạm dừng', self.pause), ('Tiếp tục', self.resume),
                              ('Bỏ nguồn…', self.remove), ('Lịch sử', self.details)):
            ttk.Button(buttons, text=text, command=command).pack(side='left', padx=(0, 6))
        ttk.Label(self, textvariable=self.status, wraplength=1000).grid(row=6, column=0, sticky='w', pady=(8, 0))

    def load_pages(self):
        try:
            saved = FacebookAccounts(self.studio.data_dir).load()
        except ValueError as exc:
            self.status.set(str(exc))
            return
        names = {account['id']: account['name'] for account in saved['accounts']}
        self.pages = [(dict(account_id=page['account_id'], page_key=page['id']),
                       f'{names.get(page["account_id"], "Tài khoản")} · {page["name"]} '
                       f'({page.get("page_id") or "chưa có Page ID"})') for page in saved['pages']]
        self.page_list.delete(0, 'end')
        for _, label in self.pages:
            self.page_list.insert('end', label)
        if not self.pages:
            self.status.set('Chưa có Page nào. Thêm tài khoản và Page trong tab Quản lý Page trước.')

    def save(self):
        try:
            picks = [self.pages[index][0] for index in self.page_list.curselection()]
            if not picks:
                raise ValueError('Chọn ít nhất một Page đích.')
            try:
                per_scan = int(self.per_scan.get())
            except ValueError:
                raise ValueError('Số video mới mỗi lượt quét từ 1 đến 20.') from None
            self.campaign_tab.capture_settings()
            if self.campaign_tab.settings_error:
                raise ValueError(self.campaign_tab.settings_error+' Sửa lựa chọn ở tab Video đơn rồi lưu lại.')
            self.pilot.add_source(self.link.get().strip(), picks, shared=self.shared.get(), per_scan=per_scan,
                                  settings=dict(self.campaign_tab.settings),
                                  original_dir=self.campaign_tab.original_dir.get(),
                                  output_dir=self.campaign_tab.output_dir.get(),
                                  browser=self.campaign_tab.selected_browser())
        except (ValueError, OSError) as exc:
            self.status.set('Chưa lưu: '+str(exc))
            return
        self.link.set('')
        self.status.set('Đã lưu nguồn kênh. Tèo quét kênh ngay khi rảnh; video mới được lồng tiếng khi không có việc khác.')
        self.refresh()

    def selected(self):
        selection = self.tree.selection()
        if not selection:
            self.status.set('Chọn một nguồn kênh trong bảng trước.')
            return None
        return selection[0]

    def act(self, action, done):
        ident = self.selected()
        if not ident:
            return
        try:
            action(ident)
            self.status.set(done)
        except ValueError as exc:
            self.status.set(str(exc))
        self.refresh()

    def scan_now(self):
        self.act(self.pilot.scan_now, 'Sẽ quét lại kênh ngay khi rảnh.')

    def pause(self):
        self.act(self.pilot.pause, 'Đã tạm dừng nguồn kênh.')

    def resume(self):
        self.act(self.pilot.resume, 'Đã tiếp tục nguồn kênh; quét lại ngay khi rảnh.')

    def remove(self):
        ident = self.selected()
        if ident and messagebox.askyesno('Bỏ nguồn kênh', 'Ngừng lấy video từ kênh này? Chiến dịch, video đã lồng tiếng '
                                         'và kế hoạch AI đã có được giữ nguyên.', parent=self):
            self.act(self.pilot.remove, 'Đã bỏ nguồn kênh; dữ liệu cũ được giữ.')

    def details(self):
        ident = self.selected()
        if not ident:
            return
        lines = [f'{row["at"][:16]} · {row["action"]} · {row["detail"][:300]}' for row in self.pilot.store.history(ident)[:20]]
        messagebox.showinfo('Lịch sử nguồn kênh', '\n'.join(lines) or 'Chưa có hoạt động.', parent=self)

    def resume_processing(self):
        self.pilot.resume_processing()
        self.status.set('Đã cho phép xử lý tự động trở lại; Tèo chạy tiếp khi rảnh.')
        self.refresh()

    def refresh(self):
        try:
            rows = self.pilot.rows()
            blocked = self.pilot.processing_blocked()
        except ValueError as exc:
            self.status.set(str(exc))
            return
        self.folders.set('Lưu video vào thư mục của tab Chiến dịch hàng loạt: '
                         f'{self.campaign_tab.original_dir.get() or "(chưa chọn)"} · '
                         f'{self.campaign_tab.output_dir.get() or "(chưa chọn)"}. '
                         'Giọng, Sub và model chốt theo tab Video đơn lúc bấm Lưu.')
        self.blocked.set(blocked)
        if blocked:
            self.banner.grid()
        else:
            self.banner.grid_remove()
        live = set()
        for row in rows:
            values = (row['label'], row['pages'], STATE_LABELS.get(row['state'], row['state']), row['taken'],
                      row['next_text'], row['message'])
            live.add(row['id'])
            if self.tree.exists(row['id']):
                self.tree.item(row['id'], values=values)
            else:
                self.tree.insert('', 'end', iid=row['id'], values=values)
        for ident in self.tree.get_children():
            if ident not in live:
                self.tree.delete(ident)
        if not rows and not self.status.get():
            self.status.set('Chưa có nguồn kênh nào. Dán link kênh Douyin ở trên để bắt đầu.')

    def poll(self):
        if self.destroyed:
            return
        try:
            self.pilot.tick()
        except Exception as exc:  # Keep the desktop timer alive and show the cause.
            self.status.set('Nguồn kênh gặp lỗi: '+str(exc))
        self.refresh()
        self.timer = self.after(2000, self.poll)

    def on_destroy(self, event):
        if event.widget is not self:
            return
        self.destroyed = True
        if self.timer:
            self.after_cancel(self.timer)
        self.pilot.close()
