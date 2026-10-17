"""Per-Page workspace. Preparing and publishing are separate explicit actions."""
from pathlib import Path
import queue
import threading
import time
import tkinter as tk
import uuid
from tkinter import filedialog, ttk

from teodub.meta_page_api import DEFAULT_API_VERSION, PageApiService
from teodub.page_publish_settings import result_checks_enabled


STATE_TEXT = {'draft':'Bản nháp', 'preparing':'Đang chuẩn bị', 'review':'Đang xem trước',
              'submitting':'Đang gửi', 'processing':'Đã gửi — đang xác minh', 'published':'Đã xác nhận đăng',
              'failed':'Có lỗi', 'unknown':'Chưa rõ kết quả', 'manual_recorded':'Link do bạn ghi',
              'browser_submitted':'Đã bấm Đăng; cần kiểm tra'}

DELIVERY_LABELS = {'api':'API Meta', 'browser':'Trình duyệt web'}
FINISH_LABELS = {'manual':'Tôi bấm Đăng trên Chrome', 'tool':'Tèo bấm Đăng sau khi duyệt'}


class PageManager(tk.Toplevel):
    def __init__(self, owner, account_id, page_key, *, service=None):
        service = service or PageApiService(owner.studio.data_dir)
        target = service.store.page(account_id, page_key)
        super().__init__(owner)
        self.owner = owner
        self.account_id, self.page_key = account_id, page_key
        self.service = service
        self.store = self.service.store
        self.target = target
        self.title('Quản lý Page — '+self.target['name'])
        self.geometry('1160x880')
        self.minsize(880, 680)
        self.busy = self.destroyed = False
        self.cancel = threading.Event()
        self.messages = queue.Queue()
        self.timer = self.auto_timer = None
        self.post_id = ''
        self.rows = {}
        self.remote_rows = {}
        self.paths = []
        self.status = tk.StringVar(self)
        self.connection_status = tk.StringVar(self)
        self.browser_summary = tk.StringVar(self)
        self.results_summary = tk.StringVar(self)
        self.results_metrics = tk.StringVar(self)
        self.results_filter = tk.StringVar(self, 'Tất cả')
        self.results_detail = tk.StringVar(self, 'Chọn một bài để xem kết quả.')
        self.advanced_visible = False
        self.page_run_status = tk.StringVar(self, 'Đang đọc trạng thái lịch Page…')
        self.page_run_detail = tk.StringVar(self)
        self.last_run_status_refresh = 0
        self.topic = tk.StringVar(self)
        self.auto_refresh = tk.BooleanVar(self, False)
        self.info = tk.StringVar(self)
        self.media_text = tk.StringVar(self, 'Chưa chọn ảnh/video.')
        self.post_detail = tk.StringVar(self)
        self.page_id = tk.StringVar(self, self.target.get('page_id',''))
        self.version = tk.StringVar(self, DEFAULT_API_VERSION)
        self.secret = tk.StringVar(self)
        self.action_buttons = []
        self.browser_request = None
        self.browser_started_at = None
        self.browser_ready_at = None
        self.browser_notice = tk.StringVar(self)
        self.account_load_request = None
        self.delivery_mode = tk.StringVar(self, DELIVERY_LABELS['browser'])
        self.browser_finish_mode = tk.StringVar(self, FINISH_LABELS['tool'])
        self.delivery_notice = tk.StringVar(self)
        self.post_browser_request = None
        self.post_browser_operation = ''
        self.post_browser_id = ''
        self.post_browser_started_at = self.post_browser_ready_at = None
        self.preview_window = None
        self.text_schedule_window = None
        self.bind('<Destroy>', self.on_destroy, add='+')
        try:
            self.build()
            self.load_local()
        except Exception:
            self.destroy()
            raise
        self.protocol('WM_DELETE_WINDOW', self.destroy)
        self.timer = self.after(250, self.poll)

    def button(self, parent, text, command, **kwargs):
        button = ttk.Button(parent, text=text, command=command, **kwargs)
        self.action_buttons.append(button)
        return button

    def build(self):
        shell = ttk.Frame(self, padding=14)
        shell.pack(fill='both', expand=True)
        ttk.Label(shell, text=self.target['name'], font=('Segoe UI', 18, 'bold')).pack(anchor='w')
        account = next(row for row in self.store.accounts.load()['accounts'] if row['id'] == self.account_id)
        ttk.Label(shell, text='Tài khoản: '+account['name'], wraplength=920).pack(anchor='w', pady=4)
        ttk.Label(shell, textvariable=self.browser_summary, wraplength=920).pack(anchor='w')
        run_card = ttk.LabelFrame(shell, text='Trạng thái lịch Page', padding=(8, 5))
        run_card.pack(fill='x', pady=(6, 2))
        self.run_status_label = ttk.Label(run_card, textvariable=self.page_run_status, font=('Segoe UI', 11, 'bold'))
        self.run_status_label.pack(anchor='w')
        ttk.Label(run_card, textvariable=self.page_run_detail, wraplength=1080).pack(anchor='w')
        browser_controls = ttk.Frame(shell)
        browser_controls.pack(fill='x', pady=(6, 0))
        self.browser_open_button = self.button(browser_controls, 'Đăng nhập / Mở Facebook',
            lambda: self.open_url('https://www.facebook.com/'))
        self.browser_open_button.pack(side='left')
        self.browser_page_button = self.button(browser_controls, 'Mở đúng Page', lambda: self.open_url(self.target['url']))
        self.browser_page_button.pack(side='left', padx=6)
        self.advanced_button = ttk.Button(browser_controls, text='Nâng cao', command=self.toggle_advanced)
        self.advanced_button.pack(side='right')
        self.status_label = ttk.Label(shell, textvariable=self.status, wraplength=1080)
        self.browser_notice_label = ttk.Label(shell, textvariable=self.browser_notice, wraplength=1080)
        self.status.trace_add('write', self.render_notices)
        self.browser_notice.trace_add('write', self.render_notices)
        style = ttk.Style(self)
        style.configure('PageWorkflow.TNotebook.Tab', padding=(12, 7), font=('Segoe UI', 10))
        self.tabs = ttk.Notebook(shell, style='PageWorkflow.TNotebook')
        self.tabs.pack(fill='both', expand=True)
        self.setup_frame = ttk.Frame(self.tabs, padding=10)
        self.tabs.add(self.setup_frame, text='Thiết lập')
        topic_row = ttk.Frame(self.setup_frame)
        topic_row.pack(fill='x', pady=(0, 6))
        ttk.Label(topic_row, text='Chủ đề Page').pack(side='left', padx=(0, 8))
        ttk.Entry(topic_row, textvariable=self.topic).pack(side='left', fill='x', expand=True)
        self.button(topic_row, 'Lưu chủ đề', self.save_notes).pack(side='left', padx=(8, 0))
        self.advanced_frame = ttk.Frame(self.tabs, padding=8)
        self.advanced_tabs = ttk.Notebook(self.advanced_frame)
        self.advanced_tabs.pack(fill='both', expand=True)
        overview, compose, remote, connection = [ttk.Frame(self.advanced_tabs, padding=12) for _ in range(4)]
        self.overview_frame, self.compose_frame, self.remote_frame, self.connection_frame = overview, compose, remote, connection
        for frame, name in ((compose,'Soạn thủ công'),(overview,'Thông tin & ghi chú'),(remote,'Số liệu Meta'),(connection,'Kết nối API')):
            self.advanced_tabs.add(frame, text=name)

        ttk.Label(overview, textvariable=self.info, wraplength=900, justify='left').pack(anchor='w', pady=(0,10))
        controls = ttk.Frame(overview)
        controls.pack(fill='x', pady=(0,12))
        self.button(controls, 'Cập nhật từ Meta', self.refresh_meta).pack(side='left')
        self.button(controls, 'Mở Page', lambda: self.open_url(self.target['url'])).pack(side='left', padx=6)
        self.button(controls, 'Lịch sử thông tin', self.show_history).pack(side='left', padx=6)
        self.auto_button = ttk.Checkbutton(overview, text='Tự cập nhật mỗi 5 phút khi cửa sổ này mở',
                                         variable=self.auto_refresh, command=self.toggle_auto)
        self.auto_button.pack(anchor='w', pady=(6,0))
        ttk.Label(overview, text='Link Page: '+self.target['url'], wraplength=900).pack(anchor='w', pady=4)
        ttk.Label(overview, textvariable=self.connection_status, wraplength=900).pack(anchor='w', pady=4)
        self.browser_load_button = self.button(overview, 'Lấy lại danh sách Page của tài khoản', self.load_account_pages)
        self.browser_load_button.pack(anchor='w', pady=4)
        ttk.Label(overview, text='Ghi chú quản lý Page (lưu trong Tèo)').pack(anchor='w')
        self.notes = tk.Text(overview, height=5, wrap='word')
        self.notes.pack(fill='both', expand=True, pady=4)
        self.button(overview, 'Lưu ghi chú', self.save_notes).pack(anchor='w', pady=6)

        compose.columnconfigure(0, weight=1)
        compose.rowconfigure(5, weight=1)
        mode_row = ttk.Frame(compose)
        mode_row.grid(row=0, column=0, sticky='ew', pady=(0,4))
        ttk.Label(mode_row, text='Cách đăng').pack(side='left')
        self.delivery_picker = ttk.Combobox(mode_row, textvariable=self.delivery_mode,
            values=list(DELIVERY_LABELS.values()), state='readonly', width=19)
        self.delivery_picker.pack(side='left', padx=(6,16))
        self.delivery_picker.bind('<<ComboboxSelected>>', self.delivery_changed)
        ttk.Label(mode_row, text='Cách hoàn tất').pack(side='left')
        self.finish_picker = ttk.Combobox(mode_row, textvariable=self.browser_finish_mode,
            values=list(FINISH_LABELS.values()), state='readonly', width=30)
        self.finish_picker.pack(side='left', padx=6)
        ttk.Label(compose, textvariable=self.delivery_notice, wraplength=880).grid(
            row=1, column=0, sticky='ew', pady=(0,6))
        self.body = tk.Text(compose, height=6, wrap='word')
        self.body.grid(row=2, column=0, sticky='ew')
        file_row = ttk.Frame(compose)
        file_row.grid(row=3, column=0, sticky='ew', pady=6)
        self.choose_files_button = self.button(file_row, 'Chọn ảnh/video…', self.choose_files)
        self.choose_files_button.pack(side='left')
        self.clear_files_button = self.button(file_row, 'Bỏ tệp', self.clear_files)
        self.clear_files_button.pack(side='left', padx=5)
        ttk.Label(file_row, textvariable=self.media_text, wraplength=630).pack(side='left')
        actions = ttk.Frame(compose)
        actions.grid(row=4, column=0, sticky='ew', pady=(0,8))
        self.button(actions, 'Bài mới', self.new_post).grid(row=0, column=0, sticky='w')
        self.save_button = self.button(actions, 'Lưu bản nháp', self.save_draft)
        self.save_button.grid(row=0, column=1, sticky='w', padx=5)
        self.publish_button = self.button(actions, 'Xem trước / Đăng ngay', self.preview)
        self.publish_button.grid(row=0, column=2, sticky='w')
        self.check_button = self.button(actions, 'Kiểm tra kết quả', self.check_result)
        self.check_button.grid(row=0, column=3, sticky='w', padx=5)
        secondary = ttk.Frame(actions)
        secondary.grid(row=1, column=0, columnspan=4, sticky='w', pady=(6,0))
        self.button(secondary, 'Chi tiết', self.show_details).pack(side='left')
        self.button(secondary, 'Bỏ khỏi danh sách', self.archive_post).pack(side='left', padx=5)
        self.schedule_text_button = self.button(secondary, 'Hẹn giờ bài chữ', self.schedule_text_post)
        self.schedule_text_button.pack(side='left', padx=5)
        self.post_tree = self.table(compose, [('body','Nội dung',230), ('kind','Loại',70), ('state','Trạng thái',145),
            ('created','Ngày tạo',140), ('published','Hoàn tất đăng',140)], row=5)
        self.post_tree.bind('<<TreeviewSelect>>', self.select_post)
        ttk.Label(compose, textvariable=self.post_detail, wraplength=900).grid(row=6, column=0, sticky='ew', pady=5)
        self.open_post_button = self.button(compose, 'Mở bài trên Facebook', self.open_post)
        self.open_post_button.grid(row=7, column=0, sticky='w')

        remote.columnconfigure(0, weight=1)
        remote.rowconfigure(1, weight=1)
        ttk.Label(remote, text='Bài lấy từ API Meta; mỗi lượt tối đa 25 bài gần nhất. Dấu — nghĩa là chưa có số liệu.',
                  wraplength=900).grid(row=0, column=0, sticky='w', pady=(0,8))
        self.remote_tree = self.table(remote, [('body','Nội dung',280), ('created','Ngày đăng',150),
            ('likes','Thích',75), ('comments','Bình luận',80), ('shares','Chia sẻ',75)], row=1)
        self.remote_tree.bind('<Double-1>', lambda _: self.open_remote())
        self.button(remote, 'Cập nhật bài / số liệu', self.refresh_meta).grid(row=2, column=0, sticky='w', pady=8)
        self.button(remote, 'Mở bài đã chọn', self.open_remote).grid(row=3, column=0, sticky='w')

        ttk.Label(connection, text='Kết nối riêng cho Page này. Chrome đã đăng nhập không tự cấp quyền API.',
                  wraplength=870).pack(anchor='w', pady=(0,12))
        form = ttk.Frame(connection)
        form.pack(fill='x')
        form.columnconfigure(1, weight=1)
        self.connection_entries = []
        for i, (label, var, masked) in enumerate([('Page ID',self.page_id,False),
                ('Phiên bản API',self.version,False), ('Khóa truy cập Page',self.secret,True)]):
            ttk.Label(form, text=label).grid(row=i, column=0, sticky='w', padx=(0,10), pady=6)
            entry = ttk.Entry(form, textvariable=var, show='•' if masked else '')
            entry.grid(row=i, column=1, sticky='ew', pady=6)
            self.connection_entries.append(entry)
        self.button(connection, 'Kiểm tra / Lưu kết nối', self.connect_api).pack(anchor='w', pady=8)
        self.button(connection, 'Ngắt kết nối API', self.disconnect_api).pack(anchor='w')
        ttk.Label(connection, text='Thiết lập trên Meta: tạo/chọn App, cấp quyền đọc và đăng Page, rồi lấy Page access token '
            'của đúng Page. Nhập khóa tại đây; không gửi vào chat. Tèo kiểm tra token thuộc đúng Page ID rồi mã hóa bằng Windows. '
            'Chuyển máy hoặc hết hạn có thể cần kết nối lại. Quyền đăng được Meta kiểm khi gửi bài.\n\n'
            'Quyền nền thường dùng: pages_show_list, pages_read_engagement, pages_manage_posts. '
            'Điều kiện cấp quyền/xét duyệt phụ thuộc App và tài khoản của bạn. '
            'Bản này chưa có đăng nhập OAuth một nút, lịch đăng hoặc Reels riêng.', wraplength=870).pack(anchor='w', pady=16)
        self.meta_button = self.button(connection, 'Mở Meta Developers trong Tèo', lambda: self.open_url('https://developers.facebook.com/apps/'))
        self.meta_button.pack(anchor='w')
        self.explorer_button = self.button(connection, 'Mở công cụ lấy quyền API trong Tèo', lambda: self.open_url('https://developers.facebook.com/tools/explorer/'))
        self.explorer_button.pack(anchor='w', pady=6)
        from teodub.page_schedule_ui import PageSchedulePanel
        from teodub.page_content_ui import PageContentPanel
        self.content_panel = PageContentPanel(self.tabs, self)
        self.tabs.add(self.content_panel, text='Kế hoạch')
        self.schedule_panel = PageSchedulePanel(self.tabs,self)
        self.tabs.add(self.schedule_panel,text='Lịch đăng')
        self.build_results()
        self.history_frame = ttk.Frame(self.tabs, padding=8)
        self.tabs.add(self.history_frame, text='Lịch sử AI')
        from teodub.page_ai_ui import PageAIPanel
        self.ai_panel = PageAIPanel(self.setup_frame, self, history_parent=self.history_frame)
        self.ai_panel.pack(fill='both', expand=True)

    def render_notices(self, *_):
        for label, variable in ((self.status_label,self.status),(self.browser_notice_label,self.browser_notice)):
            if variable.get().strip():
                label.pack(anchor='w', fill='x', pady=(4, 0), before=self.tabs)
            else:
                label.pack_forget()

    def toggle_advanced(self):
        if self.advanced_visible:
            if self.tabs.select() == str(self.advanced_frame):
                self.tabs.select(self.setup_frame)
            self.tabs.forget(self.advanced_frame)
            self.advanced_visible = False
            self.advanced_button.configure(text='Nâng cao')
        else:
            self.open_advanced()

    def open_advanced(self, panel=None):
        if not self.advanced_visible:
            self.tabs.add(self.advanced_frame, text='Nâng cao')
            self.advanced_visible = True
            self.advanced_button.configure(text='Ẩn nâng cao')
        self.tabs.select(self.advanced_frame)
        if panel is not None:
            self.advanced_tabs.select(panel)

    def open_strategy_settings(self):
        self.tabs.select(self.content_panel)
        self.content_panel.open_strategy_settings()

    def open_video_sources(self):
        notebook = self.owner.master
        campaign = next((child for child in notebook.winfo_children() if type(child).__name__ == 'CampaignTab'), None)
        if campaign is None:
            self.status.set('Mở tab Chiến dịch hàng loạt của Tèo để thêm link Douyin, rồi quay lại đây chọn nguồn.')
            return
        notebook.select(campaign)
        self.status.set('Đã mở Chiến dịch hàng loạt. Thêm link và tạo video, rồi mở lại Page này để chọn nguồn.')
        self.withdraw()
        notebook.winfo_toplevel().lift()

    def build_results(self):
        frame = self.results_frame = ttk.Frame(self.tabs, padding=12)
        self.tabs.add(frame, text='Kết quả')
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(3, weight=1)
        ttk.Label(frame, textvariable=self.results_summary, font=('Segoe UI', 11, 'bold'), wraplength=1020).grid(row=0, column=0, sticky='w')
        ttk.Label(frame, textvariable=self.results_metrics, wraplength=1020).grid(row=1, column=0, sticky='w', pady=(4, 8))
        line = ttk.Frame(frame)
        line.grid(row=2, column=0, sticky='ew', pady=(0, 8))
        ttk.Label(line, text='Hiển thị').pack(side='left')
        picker = ttk.Combobox(line, textvariable=self.results_filter, values=('Tất cả','Đã đăng','Đã gửi, chưa xác minh','Đang xác minh','Cần kiểm tra'), state='readonly', width=23)
        picker.pack(side='left', padx=6)
        picker.bind('<<ComboboxSelected>>', lambda _: self.refresh_results())
        ttk.Button(line, text='Làm mới', command=self.reload_data).pack(side='right')
        self.results_tree = self.table(frame, [('body','Nội dung',400), ('kind','Loại',70), ('state','Kết quả',150), ('published','Thời gian đăng',165)], row=3)
        self.results_tree.bind('<<TreeviewSelect>>', self.select_result)
        ttk.Label(frame, textvariable=self.results_detail, wraplength=1020, justify='left').grid(row=4, column=0, sticky='ew', pady=8)
        actions = ttk.Frame(frame)
        actions.grid(row=5, column=0, sticky='ew')
        self.result_open_button = ttk.Button(actions, text='Mở bài trên Facebook', command=self.open_result, state='disabled')
        self.result_open_button.pack(side='left')
        self.result_detail_button = ttk.Button(actions, text='Xem nội dung / kết quả', command=self.show_result, state='disabled')
        self.result_detail_button.pack(side='left', padx=6)
        ttk.Button(actions, text='Xem lịch đăng', command=lambda: self.tabs.select(self.schedule_panel)).pack(side='right')
        ttk.Label(frame, text='Kết quả là những lượt Tèo đã ghi nhận. Số liệu chưa thu được hiển thị —.', wraplength=1020).grid(row=6, column=0, sticky='w', pady=(8, 0))

    def refresh_results(self):
        self.refresh_submission_labels()
        selected = self.results_tree.selection()
        scroll = self.results_tree.yview()
        wanted = self.results_filter.get()
        needs = {'failed','unknown','browser_submitted'}
        rows = [row for row in self.rows.values() if wanted == 'Tất cả' or
                wanted == 'Đã đăng' and row['state'] == 'published' or
                wanted == 'Đã gửi, chưa xác minh' and row['id'] in self.unverified_submission_ids or
                wanted == 'Đang xác minh' and row['state'] == 'processing' and row['id'] not in self.unverified_submission_ids or
                wanted == 'Cần kiểm tra' and row['state'] in needs and row['id'] not in self.unverified_submission_ids]
        current = {row['id'] for row in rows}
        for ident in self.results_tree.get_children():
            if ident not in current:
                self.results_tree.delete(ident)
        for row in rows:
            values = (' '.join(row['body'].split())[:160] or '(Bài có tệp)', {'text':'Chữ','image':'Ảnh','video':'Video'}[row['kind']],
                      self.post_state_label(row), row['published_at'] or '—')
            if self.results_tree.exists(row['id']):
                self.results_tree.item(row['id'], values=values)
            else:
                self.results_tree.insert('', 'end', iid=row['id'], values=values)
        self.results_tree.selection_set([ident for ident in selected if ident in current])
        if scroll:
            self.results_tree.yview_moveto(scroll[0])
        self.results_summary.set('Đã xác nhận đăng: '+str(sum(row['state']=='published' for row in self.rows.values()))+
                                 ' · Đã gửi, chưa xác minh: '+str(len(self.unverified_submission_ids))+
                                 ' · Đã gửi, đang xác minh: '+str(sum(row['state']=='processing' and row['id'] not in self.unverified_submission_ids for row in self.rows.values()))+
                                 ' · Cần kiểm tra: '+str(sum(row['state'] in needs and row['id'] not in self.unverified_submission_ids for row in self.rows.values())))
        observations = self.store.observations(self.account_id, self.page_key)
        latest = observations[0] if observations else {}
        followers = latest.get('followers')
        self.results_metrics.set('Người theo dõi: '+str(followers if followers is not None else '—')+
                                 ' · Cập nhật: '+str(latest.get('at') or '—'))
        self.select_result()

    def refresh_submission_labels(self):
        self.unverified_submission_ids = set()
        if result_checks_enabled(self.owner.studio.data_dir):
            return
        for row in self.rows.values():
            if row.get('delivery_mode') != 'browser' or row['state'] not in {'unknown','processing'}:
                continue
            events = self.store.events(self.account_id,self.page_key,row['id'])
            if any(event['state'] == 'processing' for event in events):
                self.unverified_submission_ids.add(row['id'])

    def post_state_label(self, row):
        if row.get('id') in getattr(self,'unverified_submission_ids',set()):
            return 'Đã gửi — chưa xác minh'
        return STATE_TEXT.get(row.get('state',''),row.get('state','Hoàn tất'))

    def selected_result(self):
        selection = self.results_tree.selection()
        return self.rows.get(selection[0]) if selection else None

    def select_result(self, _=None):
        row = self.selected_result()
        self.result_open_button.configure(state='normal' if row and (row['external_url'] or row.get('manual_url')) else 'disabled')
        self.result_detail_button.configure(state='normal' if row else 'disabled')
        self.results_detail.set((self.post_state_label(row)+': '+row['message']) if row else 'Chọn một bài để xem kết quả.')

    def open_result(self):
        row = self.selected_result()
        if row and (row['external_url'] or row.get('manual_url')):
            self.open_url(row['external_url'] or row['manual_url'])

    def show_result(self):
        row = self.selected_result()
        if not row:
            return
        window = tk.Toplevel(self)
        window.title('Kết quả bài — '+self.target['name'])
        window.geometry('780x580')
        body = tk.Text(window, wrap='word', padx=12, pady=12)
        body.pack(fill='both', expand=True)
        metrics = row.get('metrics') or {}
        text = [self.target['name'], self.post_state_label(row)+': '+row['message'],
                'Thời gian đăng: '+(row['published_at'] or '—'), '', row['body'], '',
                ' · '.join(label+': '+str(metrics.get(key,'—')) for key,label in
                           [('likes','Thích'),('comments','Bình luận'),('shares','Chia sẻ'),('views','Lượt xem')])]
        if row['external_url']:
            text.extend(['', 'Link bài: '+row['external_url']])
        elif row.get('manual_url'):
            text.extend(['', 'Link bạn ghi (chưa xác minh): '+row['manual_url']])
        body.insert('1.0','\n'.join(text))
        body.configure(state='disabled')
        ttk.Button(window, text='Đóng', command=window.destroy).pack(anchor='e', padx=12, pady=8)

    def table(self, parent, columns, row):
        frame = ttk.Frame(parent)
        frame.grid(row=row, column=0, sticky='nsew')
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        tree = ttk.Treeview(frame, columns=[c[0] for c in columns], show='headings', selectmode='browse', height=7)
        for key, text, width in columns:
            tree.heading(key, text=text)
            tree.column(key, width=width, minwidth=55)
        tree.grid(row=0, column=0, sticky='nsew')
        scroll = ttk.Scrollbar(frame, command=tree.yview)
        scroll.grid(row=0, column=1, sticky='ns')
        horizontal = ttk.Scrollbar(frame, orient='horizontal', command=tree.xview)
        horizontal.grid(row=1, column=0, sticky='ew')
        tree.configure(yscrollcommand=scroll.set, xscrollcommand=horizontal.set)
        return tree

    def load_local(self):
        details = self.store.details(self.account_id, self.page_key)
        self.topic.set(details['topic'])
        self.notes.insert('1.0', details['notes'])
        self.reload_data()
        self.delivery_changed()
        self.tabs.select(self.setup_frame)

    def load_account_pages(self):
        if self.destroyed or self.busy or self.owner.studio.closing:
            return
        request_id = uuid.uuid4().hex
        if self.owner.load_pages(self.account_id, request_id=request_id):
            self.account_load_request = request_id
            self.browser_notice.set('Đã nhận Load Page cho tài khoản của cửa sổ này. Kết quả sẽ lưu vào danh sách Page.')
        else:
            self.browser_notice.set(self.owner.status.get() or 'Chưa nhận được Load Page. Kiểm tra Chrome của tài khoản.')

    def reload_data(self, *, select=''):
        connection = self.store.connection(self.account_id, self.page_key)
        browser = self.owner.browser_state(self.account_id)
        self.browser_summary.set(browser)
        self.connection_status.set(browser+' · API: chưa kết nối (không cần khi đăng bằng Chrome).' if not connection else
            browser+' · API: '+connection['name']+' · Page ID '+connection['page_id']+' · '+
            ('Cần kết nối lại' if connection['status'] == 'reconnect' else 'Đã kiểm tra kết nối đọc')+' · '+connection['checked_at'])
        if connection:
            self.page_id.set(connection['page_id'])
            self.version.set(connection['api_version'])
            if connection['status'] == 'reconnect':
                self.auto_refresh.set(False)
                if self.auto_timer is not None:
                    self.after_cancel(self.auto_timer)
                    self.auto_timer = None
        observations = self.store.observations(self.account_id, self.page_key)
        latest = observations[0] if observations else {}
        self.rows = {row['id']:row for row in self.store.posts(self.account_id, self.page_key)}
        self.refresh_submission_labels()
        summary = ('Bản nháp: '+str(sum(r['state']=='draft' for r in self.rows.values()))+' · Đã xác nhận đăng: '+
                   str(sum(r['state']=='published' for r in self.rows.values()))+' · Đã gửi, chưa xác minh: '+
                   str(len(self.unverified_submission_ids))+' · Đã gửi, đang xác minh: '+
                   str(sum(r['state']=='processing' and r['id'] not in self.unverified_submission_ids for r in self.rows.values()))+' · Cần kiểm tra: '+
                   str(sum(r['state'] in {'failed','unknown','browser_submitted'} and r['id'] not in self.unverified_submission_ids for r in self.rows.values())))
        self.info.set('Tên API: '+str(latest.get('name','—'))+'\nNgười theo dõi: '+str(latest.get('followers','—'))+
            ' · Lượt thích Page: '+str(latest.get('likes','—'))+'\nDanh mục: '+str(latest.get('category','—'))+
            '\nGiới thiệu: '+str(latest.get('description','—'))+'\nCập nhật: '+str(latest.get('at','—'))+'\n'+summary)
        for iid in self.post_tree.get_children():
            if iid not in self.rows:
                self.post_tree.delete(iid)
        for row in self.rows.values():
            values = (' '.join(row['body'].split())[:100] or '(Bài có tệp)', {'text':'Chữ','image':'Ảnh','video':'Video'}[row['kind']],
                self.post_state_label(row), row['created_at'], row['published_at'] or '—')
            if self.post_tree.exists(row['id']):
                self.post_tree.item(row['id'], values=values)
            else:
                self.post_tree.insert('', 'end', iid=row['id'], values=values)
        if select and self.post_tree.exists(select):
            self.post_tree.selection_set(select)
            self.select_post()
        elif self.post_id in self.rows:
            self.update_post_detail(self.rows[self.post_id])
        self.remote_rows = {row['id']:row for row in self.store.remote_posts(self.account_id, self.page_key)}
        for iid in self.remote_tree.get_children():
            if iid not in self.remote_rows:
                self.remote_tree.delete(iid)
        for row in self.remote_rows.values():
            values = (' '.join(row['body'].split())[:120] or '(Bài có ảnh/video)', row['created_time'],
                      *[row.get(k) if row.get(k) is not None else '—' for k in ('likes','comments','shares')])
            if self.remote_tree.exists(row['id']):
                self.remote_tree.item(row['id'], values=values)
            else:
                self.remote_tree.insert('', 'end', iid=row['id'], values=values)
        self.refresh_results()
        self.refresh_controls()

    def save_notes(self):
        try:
            self.store.save_details(self.account_id, self.page_key, self.topic.get(), self.notes.get('1.0','end'))
            self.status.set('Đã lưu chủ đề và ghi chú của Page.')
        except (OSError, ValueError) as exc:
            self.status.set(str(exc))

    def delivery_key(self):
        return next((key for key, label in DELIVERY_LABELS.items() if label == self.delivery_mode.get()), 'api')

    def finish_key(self):
        return next((key for key, label in FINISH_LABELS.items() if label == self.browser_finish_mode.get()), 'manual')

    def delivery_changed(self, _=None):
        if self.delivery_key() == 'browser':
            self.delivery_notice.set('Đăng bằng Chrome riêng của tài khoản, không cần khóa API. Lưu bản nháp → Xem trước / Chuẩn bị '
                '→ duyệt đúng Page và nội dung → Đăng bài đã duyệt. Tèo tự mở Page và đổi danh tính Page khi xác nhận được; '
                'nếu Facebook yêu cầu đăng nhập/xác minh, xử lý trực tiếp trong Chrome rồi tiếp tục. '
                'Tham khảo: facebook.com/legal/terms.')
        else:
            self.delivery_notice.set('API Meta dùng kết nối của Page trong tab Kết nối API. Bản nháp và ghi chú dùng chung.')
        self.refresh_controls()

    def new_post(self):
        if self.busy or self.preview_window:
            return
        self.post_id = ''
        self.post_tree.selection_remove(*self.post_tree.selection())
        self.body.configure(state='normal')
        self.body.delete('1.0','end')
        self.clear_files()
        self.delivery_mode.set(DELIVERY_LABELS['browser'])
        self.browser_finish_mode.set(FINISH_LABELS['tool'])
        self.delivery_changed()
        self.post_detail.set('Bài mới, chưa lưu.')
        self.refresh_controls()

    def choose_files(self):
        row = self.rows.get(self.post_id)
        if self.busy or self.preview_window or row and row['state'] not in {'draft','failed'}:
            return
        selected = filedialog.askopenfilenames(parent=self, title='Chọn ảnh hoặc một video',
            filetypes=[('Ảnh / video','*.jpg *.jpeg *.png *.webp *.mp4 *.mov')])
        if selected:
            try:
                self.store.media_files(selected)
                self.paths = list(selected)
                self.media_text.set(' · '.join(Path(p).name for p in self.paths))
            except (ValueError, OSError) as exc:
                self.status.set(str(exc))

    def clear_files(self):
        row = self.rows.get(self.post_id)
        if self.busy or self.preview_window or row and row['state'] not in {'draft','failed'}:
            return
        self.paths = []
        self.media_text.set('Chưa chọn ảnh/video.')

    def save_draft(self):
        if self.busy or self.preview_window:
            return
        try:
            saved = self.store.save_draft(self.account_id, self.page_key, self.body.get('1.0','end'), self.paths,
                post_id=self.post_id or None, delivery_mode=self.delivery_key(), browser_finish_mode=self.finish_key())
            self.post_id = saved['id']
            self.status.set('Đã lưu bản nháp. Xem trước nội dung và đúng Page rồi đăng khi sẵn sàng.')
            self.reload_data(select=saved['id'])
        except (ValueError, OSError) as exc:
            self.status.set(str(exc))

    def schedule_text_post(self):
        if self.busy or self.preview_window or self.destroyed:
            return
        if self.text_schedule_window is not None and self.text_schedule_window.winfo_exists():
            self.text_schedule_window.lift()
            return
        try:
            if not self.post_id:
                raise ValueError('Lưu bản nháp bài chữ trước khi hẹn giờ.')
            post = self.store.post(self.account_id, self.page_key, self.post_id)
            if (self.body.get('1.0', 'end').strip() != post['body'] or self.paths != [p['path'] for p in post['media']]
                    or self.delivery_key() != post['delivery_mode'] or self.finish_key() != post['browser_finish_mode']):
                raise ValueError('Nội dung hoặc cách đăng đang sửa chưa lưu. Lưu bản nháp trước khi hẹn giờ.')
            from teodub.page_schedule_ui import SchedulePostDialog
            self.text_schedule_window = SchedulePostDialog(self)
        except (OSError, ValueError, RuntimeError) as exc:
            self.status.set(str(exc))

    def select_post(self, _=None):
        chosen = self.post_tree.selection()
        if not chosen or chosen[0] not in self.rows or self.busy or self.preview_window:
            return
        row = self.rows[chosen[0]]
        self.post_id = row['id']
        self.body.configure(state='normal')
        self.body.delete('1.0','end')
        self.body.insert('1.0', row['body'])
        self.paths = [p['path'] for p in row['media']]
        self.delivery_mode.set(DELIVERY_LABELS.get(row.get('delivery_mode','api'), DELIVERY_LABELS['api']))
        self.browser_finish_mode.set(FINISH_LABELS.get(row.get('browser_finish_mode','manual'), FINISH_LABELS['manual']))
        self.media_text.set(' · '.join(Path(p).name for p in self.paths) or 'Chưa chọn ảnh/video.')
        self.update_post_detail(row)
        self.delivery_changed()

    def update_post_detail(self, row):
        metrics = row['metrics']
        self.post_detail.set(DELIVERY_LABELS.get(row.get('delivery_mode','api'),'API Meta')+' · '+
            self.post_state_label(row)+': '+row['message']+
            ('\nLink do bạn ghi (chưa xác minh): '+row['manual_url'] if row.get('manual_url') else '')+'\n'+
            ' · '.join(label+': '+str(metrics.get(key,'—')) for key,label in
                       [('likes','Thích'),('comments','Bình luận'),('shares','Chia sẻ'),('views','Lượt xem')]))

    def ensure_not_scheduled(self, post_id):
        schedules = self.owner.get_page_scheduler().store.list(self.account_id, self.page_key)
        if any(row['post_id'] == post_id and row['state'] in
               {'scheduled', 'armed', 'paused', 'preparing', 'submitting', 'needs_attention', 'removed'} for row in schedules):
            raise ValueError('Bài này đã có lịch đăng. Xem tab Lịch đăng bài / video để xử lý lịch trước; '
                             'không chuẩn bị hoặc đăng thêm từ cửa sổ bài viết để tránh trùng.')

    def preview(self):
        if self.busy or self.preview_window or not self.post_id:
            return
        try:
            post = self.store.post(self.account_id, self.page_key, self.post_id)
            self.ensure_not_scheduled(post['id'])
            mode = post.get('delivery_mode','api')
            if self.delivery_key() != mode:
                raise ValueError('Cách đăng đang sửa chưa lưu. Bấm Lưu bản nháp trước khi xem bản gửi.')
            if mode == 'browser' and post['state'] == 'review':
                self.show_browser_review(post['id'])
                return
            if post['state'] not in {'draft','failed'}:
                raise ValueError('Bài đã gửi hoặc đang xử lý; dùng Kiểm tra kết quả thay vì đăng lại.')
            if (self.body.get('1.0','end').strip() != post['body'] or self.paths != [p['path'] for p in post['media']]
                    or mode == 'browser' and self.finish_key() != post.get('browser_finish_mode','manual')):
                raise ValueError('Nội dung/tệp hoặc cách hoàn tất đang sửa chưa lưu. Bấm Lưu bản nháp trước khi xem bản gửi.')
            self.store.verify_media(post)
            if mode == 'browser':
                self.show_browser_draft(post)
                return
            connection = self.store.connection(self.account_id, self.page_key)
            if not connection:
                self.status.set('Kết nối API Page trước khi đăng.')
                self.open_advanced(self.connection_frame)
                return
        except (ValueError, OSError) as exc:
            self.status.set(str(exc))
            return
        window, shell = self.make_preview('Xem bài trước khi đăng')
        ttk.Label(shell, text='Đăng lên: '+connection['name']+' · Page ID '+connection['page_id'],
                  font=('Segoe UI',12,'bold'), wraplength=640).pack(anchor='w')
        body = tk.Text(shell, height=12, wrap='word')
        body.pack(fill='both', expand=True, pady=12)
        body.insert('1.0', post['body'])
        body.configure(state='disabled')
        ttk.Label(shell, text='Tệp: '+(' · '.join(Path(p['path']).name for p in post['media']) or 'Không có'), wraplength=640).pack(anchor='w')
        clicked = False
        def publish():
            nonlocal clicked
            if clicked or self.destroyed:
                return
            clicked = True
            window.destroy()
            self.start('publish', lambda: self.service.publish(self.account_id, self.page_key, post['id'],
                cancel=self.cancel, progress=lambda msg: self.messages.put(('progress',msg))))
        ttk.Button(shell, text='Đăng bài này', command=publish).pack(anchor='e', pady=12)
        window.grab_set()

    def make_preview(self, title, *, geometry='740x580'):
        window = tk.Toplevel(self)
        self.preview_window = window
        window.title(title)
        window.geometry(geometry)
        window.transient(self)
        shell = ttk.Frame(window, padding=16)
        shell.pack(fill='both', expand=True)
        def closed(event):
            if event.widget is window and self.preview_window is window:
                self.preview_window = None
                try:
                    self.refresh_controls()
                except tk.TclError:
                    pass  # Parent teardown may have removed its controls first.
        window.bind('<Destroy>', closed, add='+')
        self.refresh_controls()
        return window, shell

    @staticmethod
    def preview_body(shell, body):
        text = tk.Text(shell, height=9, wrap='word')
        text.pack(fill='both', expand=True, pady=10)
        text.insert('1.0',body)
        text.configure(state='disabled')

    def show_browser_draft(self, post):
        account = next(row for row in self.store.accounts.load()['accounts'] if row['id'] == self.account_id)
        window, shell = self.make_preview('Xem bản nháp trước khi chuẩn bị trong Chrome')
        ttk.Label(shell, text='Tài khoản: '+account['name']+'\nPage: '+post['target_name']+
            ' · ID '+(post['target_id'] or 'Chưa có')+'\n'+post['target_url'],
            font=('Segoe UI',11,'bold'), wraplength=690).pack(anchor='w')
        self.preview_body(shell,post['body'])
        ttk.Label(shell, text='Tệp: '+(' · '.join(p['path'] for p in post['media']) or 'Không có'),
            wraplength=690).pack(anchor='w')
        ttk.Label(shell, text='Cách hoàn tất: '+FINISH_LABELS[post.get('browser_finish_mode','manual')]+
            '\nBước này mở form, điền nội dung và đính kèm tệp. Khi xong bạn xem lại bài đã chuẩn bị.',
            wraplength=690).pack(anchor='w',pady=8)
        clicked = False
        def prepare():
            nonlocal clicked
            if clicked or self.destroyed:
                return
            clicked = True
            window.destroy()
            self.submit_post_browser(post['id'],'prepare')
        ttk.Button(shell,text='Chuẩn bị trong Chrome',command=prepare).pack(anchor='e',pady=10)
        window.grab_set()

    def browser_publisher(self):
        from teodub.facebook_web_post import BrowserPagePublisher
        return BrowserPagePublisher(self.owner.studio.data_dir)

    def show_browser_review(self, post_id):
        if self.busy or self.preview_window or self.destroyed:
            return
        try:
            snapshot = self.browser_publisher().review(self.account_id,self.page_key,post_id)
            row = self.store.post(self.account_id,self.page_key,post_id)
        except (ValueError,OSError) as exc:
            self.status.set(str(exc))
            return
        reviewing = row['state'] == 'review'
        window, shell = self.make_preview('Duyệt bài đã chuẩn bị trong Chrome' if reviewing else
            'Đối chiếu bản đã chuẩn bị trong Chrome',geometry='780x700')
        identity, composer = snapshot.get('identity',{}), snapshot.get('composer',{})
        ttk.Label(shell,text='Tài khoản: '+snapshot.get('account_name',snapshot['account_id'])+
            '\nPage đã chuẩn bị: '+snapshot['target_name']+' · ID '+(snapshot['target_id'] or 'Chưa có')+
            '\n'+snapshot['target_url'],font=('Segoe UI',11,'bold'),wraplength=730).pack(anchor='w')
        ttk.Label(shell,text='Danh tính trên form: '+identity.get('name','—')+' · ID '+identity.get('page_id','—')+
            '\nForm: '+composer.get('url','—')+'\nChuẩn bị lúc: '+snapshot.get('prepared_at','—'),
            wraplength=730).pack(anchor='w',pady=(8,0))
        self.preview_body(shell,composer.get('body',snapshot['body']))
        files = snapshot.get('observed_files',[])
        ttk.Label(shell,text='Tệp trên form: '+(' · '.join(f['name']+' ('+str(f.get('size','—'))+' byte)' for f in files) or 'Không có')+
            '\nTệp đã chọn: '+(' · '.join(p['path'] for p in snapshot.get('media',[])) or 'Không có')+
            '\nSố tệp: '+str(composer.get('attachment_count','—'))+' · Khung xem trước: '+str(composer.get('preview_count','—')),
            wraplength=730).pack(anchor='w')
        finish = snapshot.get('browser_finish_mode','manual')
        actions = ttk.Frame(shell)
        actions.pack(fill='x',pady=10)
        def focus():
            window.destroy()
            if reviewing:
                self.submit_post_browser(post_id,'review')
            else:
                self.open_url(snapshot['target_url'])
        ttk.Button(actions,text='Xem trong Chrome' if reviewing else 'Mở Page để kiểm tra',command=focus).pack(side='left')
        final_button = None
        if reviewing and finish == 'tool' and not row.get('manual_url'):
            clicked = False
            def publish():
                nonlocal clicked
                if clicked or self.destroyed:
                    return
                clicked = True
                window.destroy()
                self.submit_post_browser(post_id,'publish')
            final_button = ttk.Button(actions,text='Đăng bài đã duyệt',command=publish)
            final_button.pack(side='right')
        elif reviewing:
            ttk.Label(shell,text='Bạn bấm Đăng trên Chrome khi đã kiểm tra bài. Sau đó có thể ghi link bài vào Tèo.',
                wraplength=730).pack(anchor='w')
        else:
            ttk.Label(shell,text='Đây là bản đã chuẩn bị để đối chiếu. Kiểm tra kết quả trên Page rồi ghi link; không tự gửi lại bài.',
                wraplength=730).pack(anchor='w')
        manual_url = tk.StringVar(window,row.get('manual_url',''))
        ttk.Label(shell,text='Link bài do bạn ghi (chưa xác minh)').pack(anchor='w',pady=(6,2))
        ttk.Entry(shell,textvariable=manual_url).pack(fill='x')
        note = tk.StringVar(window)
        ttk.Label(shell,textvariable=note,wraplength=730).pack(anchor='w',pady=4)
        def record():
            try:
                self.browser_publisher().record_manual_result(self.account_id,self.page_key,post_id,manual_url.get())
                if final_button:
                    final_button.configure(state='disabled')
                note.set('Đã ghi link do bạn cung cấp; chưa xác minh bài đã đăng.')
                self.reload_data()
            except (ValueError,OSError) as exc:
                note.set(str(exc))
        ttk.Button(shell,text='Ghi link bài',command=record).pack(anchor='w')
        window.grab_set()

    def submit_post_browser(self, post_id, operation):
        if self.busy or self.destroyed or self.owner.studio.closing:
            return
        try:
            post = self.store.post(self.account_id,self.page_key,post_id)
            if operation in {'prepare', 'publish'}:
                self.ensure_not_scheduled(post_id)
            if post.get('delivery_mode','api') != 'browser':
                raise ValueError('Bài này dùng API Meta; lưu cách đăng Trình duyệt web trước khi chuẩn bị.')
            request_id = self.owner.submit_page_browser(self.account_id,self.page_key,post_id,operation)
        except Exception as exc:
            self.browser_notice.set('Chưa nhận được yêu cầu Chrome: '+str(exc))
            self.refresh_controls()
            return
        if not request_id:
            self.browser_notice.set(self.owner.status.get() or 'Chrome chưa nhận yêu cầu. Kiểm tra phiên tài khoản trong Tèo.')
            self.refresh_controls()
            return
        self.post_browser_request = request_id
        self.post_browser_operation = operation
        self.post_browser_id = post_id
        self.post_browser_started_at = time.monotonic()
        self.post_browser_ready_at = None
        self.busy = True
        self.browser_notice.set('Đã nhận yêu cầu '+{'prepare':'chuẩn bị','review':'xem bài đã chuẩn bị',
            'publish':'đăng bài đã duyệt'}.get(operation,operation)+' trong Chrome.')
        self.refresh_controls()

    def check_result(self):
        post_id = self.post_id
        if post_id:
            if self.rows.get(post_id,{}).get('delivery_mode','api') == 'browser':
                if not result_checks_enabled(self.owner.studio.data_dir):
                    self.status.set('Đã tạm dừng kiểm tra trạng thái. Bật lại trong Nâng cao khi cần.')
                    return
                if self.rows[post_id]['state'] == 'review' or self.rows[post_id].get('browser_review'):
                    self.show_browser_review(post_id)
                else:
                    self.show_details()
                    self.status.set('Kiểm tra bài trong Chrome và ghi link khi có; Tèo không tự gửi lại bài chưa rõ kết quả.')
                return
            self.start('check', lambda: self.service.check_post(self.account_id, self.page_key, post_id))

    def archive_post(self):
        if self.busy or not self.post_id:
            return
        try:
            self.store.archive(self.account_id, self.page_key, self.post_id)
            self.new_post()
            self.reload_data()
            self.status.set('Đã bỏ bài khỏi danh sách trong Tèo; bài trên Facebook và tệp gốc được giữ.')
        except ValueError as exc:
            self.status.set(str(exc))

    def show_details(self):
        if not self.post_id:
            return
        try:
            row = self.store.post(self.account_id, self.page_key, self.post_id)
            events = self.store.events(self.account_id, self.page_key, self.post_id)
        except (ValueError, OSError) as exc:
            self.status.set(str(exc))
            return
        window = tk.Toplevel(self)
        window.title('Chi tiết bài viết')
        content = tk.Text(window, wrap='word', width=90, height=22)
        content.pack(fill='both', expand=True)
        content.insert('1.0', row['body']+'\n\nMã Meta: '+(row['external_id'] or '—')+'\nLink: '+(row['external_url'] or '—')+
            ('\nLink do bạn ghi (chưa xác minh): '+row['manual_url'] if row.get('manual_url') else '')+
            '\n\n'+'\n'.join(e['at']+' · '+STATE_TEXT.get(e['state'],e['state'])+' · '+e['message'] for e in events))
        content.configure(state='disabled')

    def show_history(self):
        try:
            rows = self.store.observations(self.account_id, self.page_key)
        except (ValueError, OSError) as exc:
            self.status.set(str(exc))
            return
        window = tk.Toplevel(self)
        window.title('Lịch sử thông tin — '+self.target['name'])
        window.geometry('720x400')
        shell = ttk.Frame(window, padding=12)
        shell.pack(fill='both', expand=True)
        shell.columnconfigure(0, weight=1)
        shell.rowconfigure(1, weight=1)
        ttk.Label(shell, text='100 lần cập nhật gần nhất qua API. Dấu — là chưa có số liệu.').grid(row=0, column=0, sticky='w')
        tree = self.table(shell, [('at','Lúc cập nhật',210),('followers','Người theo dõi',130),
                                  ('likes','Thích Page',120),('name','Tên Page',200)], row=1)
        for row in rows:
            tree.insert('', 'end', values=tuple(row.get(k,'—') for k in ('at','followers','likes','name')))

    def connect_api(self):
        if self.busy or self.destroyed:
            return
        token, version, page_id = self.secret.get().strip(), self.version.get().strip(), self.page_id.get().strip()
        if not token:
            self.status.set('Chưa có khóa truy cập Page. Mở Meta Developers để tạo/chọn App và cấp quyền, '
                            'rồi lấy Page access token của đúng Page, dán vào ô Khóa truy cập Page. Đăng nhập Chrome chưa đủ để kết nối API.')
            return
        self.secret.set('')
        self.start('connect', lambda: self.service.connect(self.account_id, self.page_key, page_id, token, version))

    def disconnect_api(self):
        if not self.busy:
            self.start('disconnect', lambda: self.service.disconnect(self.account_id, self.page_key))

    def refresh_meta(self):
        self.start('refresh', lambda: self.service.refresh_page(self.account_id, self.page_key))

    def toggle_auto(self):
        if self.auto_timer is not None:
            self.after_cancel(self.auto_timer)
            self.auto_timer = None
        if self.auto_refresh.get():
            if not self.store.connection(self.account_id, self.page_key):
                self.auto_refresh.set(False)
                self.status.set('Kết nối API Page trước khi bật tự cập nhật.')
                return
            self.refresh_meta()
            self.auto_timer = self.after(300000, self.auto_tick)

    def auto_tick(self):
        self.auto_timer = None
        if not self.destroyed and self.auto_refresh.get():
            if not self.busy:
                self.refresh_meta()
            self.auto_timer = self.after(300000, self.auto_tick)

    def start(self, operation, function):
        if self.busy or self.preview_window or self.destroyed or self.owner.studio.closing:
            return
        self.busy = True
        self.cancel.clear()
        self.status.set('Đang xử lý '+{'connect':'kết nối API','disconnect':'ngắt kết nối','refresh':'thông tin Page',
                                     'publish':'đăng bài','check':'kết quả bài'}[operation]+'…')
        self.refresh_controls()
        def worker():
            try:
                result = function()
                self.messages.put(('done',(operation,result)))
            except Exception as exc:
                self.messages.put(('error',str(exc)))
        try:
            self.worker = threading.Thread(target=worker, daemon=False)
            self.worker.start()
        except Exception as exc:
            self.busy = False
            self.status.set('Chưa mở được bước xử lý: '+str(exc))
            self.refresh_controls()

    def refresh_controls(self):
        if self.destroyed:
            return
        locked = self.busy or bool(self.preview_window)
        for button in self.action_buttons:
            if button.winfo_exists():
                button.configure(state='disabled' if locked else 'normal')
        for entry in self.connection_entries:
            entry.configure(state='disabled' if locked else 'normal')
        try:
            connection = self.store.connection(self.account_id, self.page_key)
        except (ValueError, OSError) as exc:
            connection = None
            self.status.set(str(exc))
        row = self.rows.get(self.post_id)
        editable = not row or row['state'] in {'draft','failed'}
        browser_mode = row.get('delivery_mode','api') == 'browser' if row else self.delivery_key() == 'browser'
        self.body.configure(state='normal' if not locked and editable else 'disabled')
        self.delivery_picker.configure(state='readonly' if not locked and editable else 'disabled')
        self.finish_picker.configure(state='readonly' if not locked and editable and self.delivery_key() == 'browser' else 'disabled')
        for button in (self.save_button,self.choose_files_button,self.clear_files_button):
            button.configure(state='normal' if not locked and editable else 'disabled')
        if browser_mode:
            self.publish_button.configure(text='Duyệt bài đã chuẩn bị' if row and row['state'] == 'review' else 'Xem trước / Chuẩn bị',
                state='normal' if not locked and row and row['state'] in {'draft','failed','review'} and not row['external_id'] else 'disabled')
            self.check_button.configure(text='Chi tiết / Kiểm tra thủ công',state='normal' if result_checks_enabled(self.owner.studio.data_dir) and not locked and row and
                row['state'] not in {'draft','failed','preparing','submitting'} else 'disabled')
        else:
            self.publish_button.configure(text='Xem trước / Đăng ngay',state='normal' if not locked and connection and row
                and row['state'] in {'draft','failed'} and not row['external_id'] and connection['status'] != 'reconnect' else 'disabled')
            self.check_button.configure(text='Kiểm tra kết quả',state='normal' if not locked and connection and row
                and row['state'] in {'preparing','submitting','processing','published','unknown'} else 'disabled')
        self.open_post_button.configure(state='normal' if not locked and row and (row['external_url'] or row.get('manual_url')) else 'disabled')
        self.schedule_text_button.configure(state='normal' if not locked and row and row['kind'] == 'text'
            and row['state'] in {'draft', 'failed'} and row.get('delivery_mode') == 'browser'
            and row.get('browser_finish_mode') == 'tool' else 'disabled')
        self.auto_button.configure(state='disabled' if locked else 'normal')
        for button in (self.meta_button, self.explorer_button):
            button.configure(state='disabled' if locked or self.browser_request else 'normal')

    def poll(self):
        if self.destroyed:
            return
        if time.monotonic()-self.last_run_status_refresh>=2:
            self.refresh_page_run_status()
        changed = False
        while True:
            try:
                kind, value = self.messages.get_nowait()
            except queue.Empty:
                break
            if kind == 'progress':
                self.status.set(value)
            elif kind == 'error':
                self.busy = False
                self.status.set(value)
                self.auto_refresh.set(False)
                if self.auto_timer is not None:
                    self.after_cancel(self.auto_timer)
                    self.auto_timer = None
                changed = True
            else:
                self.busy = False
                operation, result = value
                message = ({'connect':'Đã kiểm tra và lưu kết nối API của đúng Page.',
                            'disconnect':'Đã ngắt kết nối API. Bản nháp và lịch sử được giữ.'}.get(operation))
                if operation == 'refresh':
                    message = result['message']
                elif operation in {'publish','check'}:
                    message = STATE_TEXT.get(result['state'],result['state'])+': '+result['message']
                self.status.set(message or 'Đã hoàn tất bước xử lý.')
                changed = True
        if changed:
            try:
                self.reload_data()
                if not self.owner.destroyed:
                    self.owner.reload()
            except (OSError, ValueError) as exc:
                self.status.set(str(exc))
        self.poll_browser()
        self.poll_post_browser()
        session = self.owner.sessions.get(self.account_id)
        if self.account_load_request:
            request = getattr(session, 'page_load_requests', {}).get(self.account_load_request) if session else None
            if request:
                self.browser_notice.set(request.get('message', 'Đang Load Page…'))
                if request.get('state') in {'done', 'error'}:
                    self.account_load_request = None
            elif not session or not session.pending:
                self.browser_notice.set('Phiên Chrome đã dừng trước khi Load Page xong; danh sách đã lưu được giữ.')
                self.account_load_request = None
        current = self.connection_status.get().partition(' · API:')[2]
        self.connection_status.set(self.owner.browser_state(self.account_id)+' · API:'+current)
        self.browser_summary.set(self.owner.browser_state(self.account_id))
        self.timer = self.after(250, self.poll)

    def refresh_page_run_status(self):
        self.last_run_status_refresh=time.monotonic()
        try:
            from teodub.page_status import read_page_status
            result=read_page_status(self.owner.studio.data_dir,self.account_id,self.page_key,self.owner)
            self.page_run_status.set(result['label'])
            self.page_run_detail.set(result['detail'].replace('\n',' · '))
            color='#15803d' if result['state'] in {'waiting','analyzing','posting','preparing','verifying'} else '#b45309' if result['state'] in {'needs_attention','paused','waiting_executor'} else '#475569'
            self.run_status_label.configure(foreground=color)
            self.rows = {row['id']:row for row in self.store.posts(self.account_id, self.page_key)}
            self.refresh_results()
        except (ValueError,OSError) as exc:
            self.page_run_status.set('Chưa đọc được trạng thái Page')
            self.page_run_detail.set(str(exc))

    def poll_post_browser(self):
        if not self.post_browser_request:
            return
        session = self.owner.sessions.get(self.account_id)
        actions = getattr(session,'post_actions',{}) if session else {}
        request = actions.get(self.post_browser_request)
        stopped = not session or not session.pending or session.finished.is_set() or session.failed
        if request:
            self.browser_notice.set(request.get('message',''))
        terminal = request and request.get('state') in {'done','error'}
        if not terminal and not stopped:
            return
        if not terminal:
            if request and request.get('cancel') is not None:
                request['cancel'].set()
            self.browser_notice.set('Phiên Chrome đã dừng hoặc không còn giữ thao tác. Kiểm tra bài trên Chrome trước khi làm tiếp; không tự gửi lại.')
        elif request['state'] == 'done':
            result = request.get('result') or {}
            self.refresh_submission_labels()
            self.status.set(self.post_state_label(result) +': '+
                result.get('message',request.get('message','Đã hoàn tất bước Chrome.')))
        self.post_browser_request = None
        self.post_browser_operation = self.post_browser_id = ''
        self.post_browser_started_at = self.post_browser_ready_at = None
        self.busy = False
        try:
            self.reload_data()
            if not self.owner.destroyed:
                self.owner.reload()
        except (ValueError,OSError) as exc:
            self.status.set(str(exc))
        self.refresh_controls()

    def poll_browser(self):
        if not self.browser_request:
            return
        session = self.owner.sessions.get(self.account_id)
        request = session.navigation.get(self.browser_request) if session else None
        if session and request and request['state'] in {'queued','opening'}:
            current_time = time.monotonic()
            if session.opened and not session.loading_pages and request['state'] == 'queued':
                if self.browser_ready_at is None:
                    self.browser_ready_at = current_time
                overdue = current_time-self.browser_ready_at > 20
            else:
                self.browser_ready_at = None
                overdue = self.browser_started_at is not None and current_time-self.browser_started_at > 360
            if overdue:
                request.update(state='error',message='Chưa nhận được phản hồi mở trang từ Chrome. '
                    'Không tự gửi lại yêu cầu. Khi công việc nghỉ, đóng/mở lại Tèo để nạp phần điều khiển Chrome mới, '
                    'rồi thử nút mở trang; hồ sơ đăng nhập đã lưu được giữ.')
        if request:
            self.browser_notice.set(request['message'])
        if not session or request is None or request['state'] in {'opened','error'} or not session.pending:
            if not request:
                self.browser_notice.set('Phiên Chrome không còn giữ yêu cầu mở trang. Bấm mở lại trong Tèo.')
            self.browser_request = None
            self.browser_started_at = self.browser_ready_at = None
            self.refresh_controls()

    def open_url(self, url):
        if self.destroyed:
            return
        if not url or self.busy:
            self.browser_notice.set('Đang xử lý API; đợi xong rồi mở trang trong Chrome.')
            return
        self.poll_browser()
        if self.browser_request:
            self.browser_notice.set('Tèo đã nhận yêu cầu mở trang và đang xử lý; không cần bấm thêm.')
            return
        if not self.owner.activate_account(self.account_id):
            self.browser_notice.set('Chưa chọn được đúng tài khoản để mở Chrome: '+self.owner.status.get())
            return
        request_id = uuid.uuid4().hex
        try:
            accepted = self.owner.open_browser(url,request_id=request_id)
        except TypeError:
            self.browser_notice.set('Phiên Tèo này đang dùng phần điều khiển Chrome cũ. Khi công việc nghỉ, '
                'đóng/mở lại Tèo để nạp bản mới rồi thử nút mở trang; hồ sơ đăng nhập đã lưu được giữ.')
            return
        except Exception as exc:
            self.browser_notice.set('Chưa mở được Chrome của Tèo: '+str(exc))
            return
        self.browser_notice.set(self.owner.status.get())
        if accepted:
            self.browser_request = request_id
            self.browser_started_at = time.monotonic()
            self.browser_ready_at = None
            self.refresh_controls()

    def open_post(self):
        row = self.rows.get(self.post_id)
        if row and (row['external_url'] or row.get('manual_url')):
            self.open_url(row['external_url'] or row['manual_url'])

    def open_remote(self):
        selection = self.remote_tree.selection()
        row = self.remote_rows.get(selection[0]) if selection else None
        if row:
            self.open_url(row.get('url',''))

    def on_destroy(self, event):
        if event.widget is not self:
            return
        self.destroyed = True
        self.cancel.set()
        self.secret.set('')
        for timer in (self.timer, self.auto_timer):
            if timer is not None:
                try:
                    self.after_cancel(timer)
                except tk.TclError:
                    pass
        self.timer = self.auto_timer = None
