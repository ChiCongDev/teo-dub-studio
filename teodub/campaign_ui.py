"""Separate campaign tab; ordinary single-video controls remain in Studio."""
import copy
from datetime import datetime
import os
from pathlib import Path
import queue
import subprocess
import threading
import time
import tkinter as tk
from tkinter import filedialog, ttk

from teodub.campaign import (ROTATION_REQUEST, ROTATION_WAIT, auto_resume_due, create_campaign, is_running,
                            read_json, totals)
from teodub.campaign_tracking import STATUS, tracking_row, campaign_notice, processing_time
from teodub.campaign_posting import campaign_posting_statuses
from teodub.drive_delivery import drive_upload_summary
from teodub.channel_browser import BROWSER_LABELS, profile_directory
from teodub.channel_discovery import MAX_SCAN_VIDEOS
from teodub.common import atomic_json


# A campaign with more videos than this reads its table rows a few at a time (owner 29/10/2026, up to 10.000 videos):
# opening every video's files each second would freeze the window. Up to this size nothing changes.
BIG_TABLE_ITEMS = 1000
BIG_TABLE_SECONDS = 0.15  # The most one refresh spends reading rows' files.
BIG_TABLE_REFRESH_S = 30  # A row that nothing reported about is read again after this long.


class CampaignTab(ttk.Frame):
    def __init__(self, parent, studio):
        super().__init__(parent, padding=16)
        self.studio = studio
        self.data_dir = studio.data_dir
        self.directory = None
        self.process = None
        self.worker_directory = None
        self.scanning = False
        self.creating = False
        self.scan_cancel = threading.Event()
        self.scan_messages = queue.Queue()
        self.chrome_login_messages = queue.Queue()
        self.chrome_login_pending = False
        self.chrome_login_cancel = threading.Event()
        self.chrome_login_finished = threading.Event()
        self.discovery = None
        self.records = []
        self.posting_statuses = {}
        self.drive_statuses = {}
        self.big_rows = {}  # Big campaigns: number -> (item as read, table row, Drive status, when read).
        self.big_written = {}  # Big campaigns: table key -> (values, status) last written.
        self.big_cursor = 0
        self.big_for = None
        self.saved_paths = {}
        self.auto_timer = None
        self.auto_key = None
        self.scan_key = None
        self.name = tk.StringVar(self, value='Chiến dịch '+datetime.now().strftime('%d-%m-%Y'))
        self.source_kind = tk.StringVar(self, value='Tự nhận dạng link')
        self.auto_fetch = tk.BooleanVar(self, value=True)
        self.limit = tk.StringVar(self, value='100')
        self.visible = tk.BooleanVar(self, value=True)
        self.browser_label = tk.StringVar(self, value=BROWSER_LABELS['chrome'])
        self.reverse = tk.BooleanVar(self, value=False)
        self.original_dir = tk.StringVar(self, value=studio.fields['original_dir'].get())
        self.output_dir = tk.StringVar(self, value=studio.fields['output_dir'].get())
        self.status = tk.StringVar(self, value='Dán link kênh: tự lấy link từng video. Dán nhiều link: tự lập danh sách.')
        self.notice = tk.StringVar(self)
        self.launch_notice = None
        self.discover_notice = ''  # A Lấy danh sách refusal stays until the next try (28/10/2026).
        self.summary = tk.StringVar(self)
        self.creation_summary = tk.StringVar(self)
        self.retry_summary = tk.StringVar(self)
        self.preview_retry_failed = False
        self.auto_resume_tried = 0.0
        self.saved_voice = tk.StringVar(self)
        self.saved = tk.StringVar(self)
        self.progress = tk.DoubleVar(self, value=0)
        self.settings = {}
        self.settings_error = ''
        self.loading_preferences = True
        self.build()
        self.source.bind('<<Modified>>', self.source_modified)
        self.bind('<Destroy>', self.on_destroy, add='+')
        for variable in (self.source_kind, self.limit):
            variable.trace_add('write', lambda *_: self.schedule_discovery())
        self.auto_fetch.trace_add('write', self.auto_fetch_changed)
        self.visible.trace_add('write', self.visibility_changed)
        self.browser_label.trace_add('write', self.browser_changed)
        self.capture_settings()
        try:
            prefs = read_json(self.data_dir/'campaign-preferences.json')
            self.original_dir.set(prefs['original_dir'])
            self.output_dir.set(prefs['output_dir'])
            browser = prefs.get('browser', 'chrome')
            self.browser_label.set(BROWSER_LABELS.get(browser, BROWSER_LABELS['chrome']))
            visible = prefs.get('visible', browser in ('coccoc', 'chrome'))
            if type(visible) is bool:
                self.visible.set(visible)
            auto_fetch = prefs.get('auto_fetch', True)
            if type(auto_fetch) is bool:
                self.auto_fetch.set(auto_fetch)
        except (OSError, ValueError, KeyError):
            pass
        self.loading_preferences = False
        self.refresh_saved()
        self.timer = self.after(700, self.poll)
        self.refresh_buttons()

    def build(self):
        self.columnconfigure(1, weight=1)
        self.rowconfigure(10, weight=1)
        ttk.Label(self, text='Chiến dịch tải và lồng tiếng', font=('Segoe UI', 21, 'bold')).grid(
            row=0, column=0, columnspan=3, sticky='w')
        ttk.Label(self, text='Tạo danh sách → lưu chiến dịch → chạy lần lượt. Giữ kết quả, tên và thứ tự khi tiếp tục.',
                  wraplength=1000).grid(row=1, column=0, columnspan=3, sticky='w', pady=(4, 10))
        ttk.Label(self, text='Tên chiến dịch').grid(row=2, column=0, sticky='w')
        ttk.Entry(self, textvariable=self.name).grid(row=2, column=1, columnspan=2, sticky='ew', pady=3)
        source_area = ttk.Frame(self)
        source_area.grid(row=3, column=0, columnspan=3, sticky='ew', pady=5)
        source_row = ttk.Frame(source_area)
        source_row.pack(fill='x')
        ttk.Combobox(source_row, textvariable=self.source_kind,
                     values=('Tự nhận dạng link', 'Danh sách link video', 'Link kênh Douyin'), state='readonly', width=21).pack(side='left')
        ttk.Checkbutton(source_row, text='Tự lấy khi dán', variable=self.auto_fetch).pack(side='left', padx=6)
        ttk.Label(source_row, text='Tối đa').pack(side='left', padx=(12, 5))
        ttk.Spinbox(source_row, from_=1, to=MAX_SCAN_VIDEOS, textvariable=self.limit, width=6).pack(side='left')
        ttk.Label(source_row, text='video/kênh').pack(side='left', padx=5)
        ttk.Checkbutton(source_row, text='Hiện trình duyệt để đăng nhập/xác minh', variable=self.visible).pack(side='left', padx=8)
        self.scan_button = ttk.Button(source_row, text='Lấy danh sách', command=self.discover)
        self.scan_button.pack(side='right')
        browser_row = ttk.Frame(source_area)
        browser_row.pack(fill='x', pady=(6, 0))
        ttk.Label(browser_row, text='Trình duyệt đọc kênh').pack(side='left', padx=(0, 8))
        self.browser_picker = ttk.Combobox(browser_row, textvariable=self.browser_label,
                                           values=tuple(BROWSER_LABELS.values()), state='readonly', width=26)
        self.browser_picker.pack(side='left')
        ttk.Button(browser_row, text='Xem lỗi mở…', command=self.show_scan_report).pack(side='right')
        ttk.Label(browser_row, text='Chrome dùng hồ sơ cố định, giữ phiên đăng nhập cho các lần lấy danh sách sau.',
                  wraplength=420).pack(side='left', padx=10)
        desktop_row = ttk.Frame(source_area)
        desktop_row.pack(fill='x', pady=(6, 0))
        self.chrome_login_button = ttk.Button(desktop_row, text='Đăng nhập Chrome', command=self.start_chrome_login)
        self.chrome_login_button.pack(side='left')
        self.chrome_login_done_button = ttk.Button(desktop_row, text='Đã xong / Lưu phiên', command=self.finish_chrome_login)
        self.chrome_login_done_button.pack(side='left', padx=6)
        self.desktop_browser_button = ttk.Button(desktop_row, text='Mở Cốc Cốc trên máy', command=self.open_desktop_browser)
        self.desktop_browser_button.pack(side='right')
        self.chrome_login_status = tk.StringVar(self, value='Đăng nhập Douyin lần đầu trong Chrome của Tèo; phiên hết hạn có thể cần đăng nhập lại.')
        ttk.Label(source_area, textvariable=self.chrome_login_status, wraplength=1000).pack(fill='x', pady=(4, 0))
        self.source = tk.Text(self, height=3, wrap='word', font=('Segoe UI', 10))
        self.source.grid(row=4, column=0, columnspan=3, sticky='ew')
        folders = ttk.Frame(self)
        folders.grid(row=5, column=0, columnspan=3, sticky='ew', pady=6)
        folders.columnconfigure(1, weight=1)
        for row, (label, var) in enumerate((('Lưu video gốc', self.original_dir), ('Lưu video lồng tiếng', self.output_dir))):
            ttk.Label(folders, text=label).grid(row=row, column=0, sticky='w', padx=(0, 10))
            ttk.Entry(folders, textvariable=var).grid(row=row, column=1, sticky='ew', pady=3)
            ttk.Button(folders, text='Chọn…', command=lambda v=var: self.choose_folder(v)).grid(row=row, column=2, padx=(8, 0))
        settings = ttk.Frame(self)
        settings.grid(row=6, column=0, columnspan=3, sticky='ew')
        summary_row = ttk.Frame(settings)
        summary_row.pack(fill='x')
        summary_row.columnconfigure(0, weight=1)
        summary_label = ttk.Label(summary_row, textvariable=self.summary, wraplength=850, foreground='#164b78',
                                  justify='left')
        summary_label.grid(row=0, column=0, sticky='ew', padx=(0, 8))
        self.apply_settings_button = ttk.Button(summary_row, text='Lấy cấu hình từ tab Video đơn',
                                               command=self.apply_single_settings)
        self.apply_settings_button.grid(row=0, column=1, sticky='e')
        summary_row.bind('<Configure>', lambda event: summary_label.configure(
            wraplength=max(200, event.width-self.apply_settings_button.winfo_reqwidth()-12)))
        ttk.Label(settings, textvariable=self.retry_summary, wraplength=1000,
                  justify='left').pack(fill='x')
        self.creation_settings_frame = ttk.LabelFrame(settings, text='Chỉ áp dụng khi Tạo chiến dịch mới', padding=6)
        ttk.Label(self.creation_settings_frame, textvariable=self.creation_summary, wraplength=1000,
                  justify='left').pack(fill='x')
        drive_row = ttk.Frame(self.creation_settings_frame)
        drive_row.pack(fill='x', pady=(3, 0))
        drive_var = self.studio.fields.get('drive_enabled') or tk.BooleanVar(self, value=False)
        self.drive_check = ttk.Checkbutton(drive_row, text='Tự lưu video thành phẩm lên Google Drive (chiến dịch mới)',
                                           variable=drive_var, command=self.capture_settings)
        self.drive_check.pack(side='left')
        self.drive_button = ttk.Button(drive_row, text='Chọn thư mục Drive…', command=self.choose_drive)
        self.drive_button.pack(side='left', padx=(10, 0))
        ttk.Label(settings, text='Lấy cấu hình Video đơn: áp dụng cho các video chưa xong từ lượt tiếp theo. '
                  'Video đang xử lý giữ cấu hình của lượt đó.', wraplength=1000).pack(fill='x')
        actions = ttk.Frame(self)
        actions.grid(row=7, column=0, columnspan=3, sticky='ew', pady=8)
        self.create_button = ttk.Button(actions, text='Tạo chiến dịch mới', command=self.create)
        self.create_button.pack(side='left', padx=(0, 6))
        ttk.Checkbutton(actions, text='Đảo thứ tự danh sách trước khi tạo', variable=self.reverse).pack(side='left')
        self.start_button = ttk.Button(actions, text='Chạy / Tiếp tục', command=self.start)
        self.start_button.pack(side='left', padx=6)
        self.retry_button = ttk.Button(actions, text='Thử lại video lỗi', command=lambda: self.start(retry=True))
        self.retry_button.pack(side='left')
        self.stop_button = ttk.Button(actions, text='Dừng', command=self.stop)
        self.stop_button.pack(side='left', padx=6)
        self.creation_settings_button = ttk.Button(actions, text='Cấu hình tạo mới…', command=self.toggle_creation_settings)
        self.creation_settings_button.pack(side='right')
        history = ttk.Frame(self)
        history.grid(row=8, column=0, columnspan=3, sticky='ew')
        ttk.Label(history, text='Chiến dịch đã lưu').pack(side='left', padx=(0, 8))
        self.saved_picker = ttk.Combobox(history, textvariable=self.saved, state='readonly', width=65)
        self.saved_picker.pack(side='left', fill='x', expand=True)
        self.saved_picker.bind('<<ComboboxSelected>>', self.select_saved)
        ttk.Button(history, text='Mở video gốc', command=lambda: self.open_folder('original_dir')).pack(side='left', padx=6)
        ttk.Button(history, text='Mở thành phẩm', command=lambda: self.open_folder('output_dir')).pack(side='left')
        state_panel = ttk.Frame(self)
        state_panel.grid(row=9, column=0, columnspan=3, sticky='ew', pady=6)
        ttk.Label(state_panel, textvariable=self.status, wraplength=1000).pack(fill='x')
        ttk.Label(state_panel, textvariable=self.notice, foreground='#b12727', wraplength=1000,
                  justify='left').pack(fill='x', pady=(3, 0))
        table = ttk.Frame(self)
        table.grid(row=10, column=0, columnspan=3, sticky='nsew')
        table.columnconfigure(0, weight=1)
        table.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(table, columns=('name', 'title', 'url', 'state', 'percent', 'completed_at', 'posting', 'drive', 'message'), show='headings', height=9)
        for key, label, width in (('name', 'Video', 70), ('title', 'Tiêu đề', 190), ('url', 'Link video', 240),
                                  ('state', 'Trạng thái', 140), ('percent', 'Tiến độ', 70),
                                  ('completed_at', 'Thời gian hoàn thành', 168),
                                  ('posting', 'Trạng thái đăng bài', 185), ('drive', 'Google Drive', 170),
                                  ('message', 'Chi tiết / lỗi', 280)):
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, minwidth=60, stretch=key != 'name')
        self.tree.grid(row=0, column=0, sticky='nsew')
        scroll = ttk.Scrollbar(table, orient='vertical', command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky='ns')
        self.tree.configure(yscrollcommand=scroll.set)
        horizontal = ttk.Scrollbar(table, orient='horizontal', command=self.tree.xview)
        horizontal.grid(row=1, column=0, sticky='ew')
        self.tree.configure(xscrollcommand=horizontal.set)
        self.tree.bind('<Double-1>', self.show_item)
        self.tree.bind('<Button-3>', self.video_context_menu)
        self.video_menu = tk.Menu(self, tearoff=False)
        self.video_menu.add_command(label='Đăng video…', command=self.schedule_selected_video)
        self.video_menu.add_command(label='Mở video', command=self.open_context_video)
        self.schedule_context = None
        links = ttk.Frame(table)
        links.grid(row=2, column=0, columnspan=2, sticky='ew', pady=3)
        ttk.Button(links, text='Sao chép link đã chọn', command=self.copy_selected).pack(side='left')
        ttk.Button(links, text='Sao chép tất cả link', command=self.copy_all).pack(side='left', padx=6)
        ttk.Button(links, text='Nhập HTML kênh…', command=self.import_channel_page).pack(side='left', padx=3)
        ttk.Button(links, text='Xem chi tiết / nguyên nhân', command=lambda: self.show_item(detail_only=True)).pack(side='left', padx=3)
        ttk.Label(links, text='Nhấp đôi để xem chi tiết / mở thành phẩm.').pack(side='left', padx=5)
        for tag, color in (('completed', '#237637'), ('review_required', '#8a6000'),
                           ('failed', '#b12727'), ('running', '#065ca8'), ('cancelled', '#777777')):
            self.tree.tag_configure(tag, foreground=color)
        ttk.Progressbar(self, variable=self.progress, maximum=100).grid(row=11, column=0, columnspan=3, sticky='ew', pady=6)
        ttk.Label(self, wraplength=1000, text='Video 1, Video 2… được đánh số theo danh sách đã chốt, kèm ngày tạo chiến dịch. '
                  'Quét kênh chỉ đọc video công khai mà trang tải được; có thể cần đăng nhập. '
                  'Nhấp đôi vào một dòng để xem lỗi hoặc mở video. Đóng app không dừng chiến dịch; bấm Dừng để tạm dừng.').grid(
                      row=12, column=0, columnspan=3, sticky='w')

    def choose_folder(self, variable):
        value = filedialog.askdirectory(parent=self, initialdir=variable.get())
        if value:
            variable.set(value)

    def open_desktop_browser(self):
        if self.scanning or self.chrome_login_pending:
            self.status.set('Đang đọc kênh. Bấm Dừng hoặc đợi đọc xong rồi mở Cốc Cốc trên máy.')
            return
        from teodub.desktop_browser import open_desktop_coccoc
        try:
            # Cancel any pending automatic isolated scan before opening the normal browser.
            self.auto_fetch.set(False)
            from teodub.channel_discovery import channel_url
            try:
                text = channel_url(self.source.get('1.0', 'end').strip())
            except ValueError:
                text = ''
            open_desktop_coccoc(text)
            self.status.set('Đã gửi lệnh mở Cốc Cốc trên máy bằng phiên thường. '
                            'Chưa lấy link tự động; có thể lưu trang rồi bấm Nhập HTML kênh…')
        except (OSError, ValueError) as exc:
            self.status.set(str(exc))

    def start_chrome_login(self):
        if self.scanning or self.studio.process is not None or self.studio.campaign_busy or self.studio.login_pending:
            return
        from teodub.channel_discovery import channel_url
        try:
            text = channel_url(self.source.get('1.0', 'end').strip())
        except ValueError:
            text = ''
        self.auto_fetch.set(False)
        self.browser_label.set(BROWSER_LABELS['chrome'])
        self.visible.set(True)
        try:
            self.save_campaign_preferences()
        except (OSError, ValueError) as exc:
            self.chrome_login_status.set('Chưa lưu được lựa chọn Chrome: '+str(exc))
            return
        self.chrome_login_cancel.clear()
        self.chrome_login_finished.clear()
        self.chrome_login_pending = self.studio.login_pending = True
        profile = profile_directory(self.data_dir/'douyin-channel-browser', 'chrome')
        self.chrome_login_status.set('Đang mở Chrome với hồ sơ đăng nhập đã lưu…')
        self.refresh_buttons()
        self.studio.refresh_buttons()
        def login():
            try:
                from teodub.channel_login import open_chrome_login
                reason = open_chrome_login(profile, text=text, cancel=self.chrome_login_cancel,
                    finished=self.chrome_login_finished,
                    progress=lambda message: self.chrome_login_messages.put(('progress', message)))
                self.chrome_login_messages.put(('done', reason))
            except Exception as exc:
                self.chrome_login_messages.put(('error', str(exc)))
        try:
            # Let the login worker close its context and flush the profile if the GUI exits.
            threading.Thread(target=login, daemon=False).start()
        except Exception as exc:
            self.chrome_login_pending = self.studio.login_pending = False
            self.chrome_login_status.set('Chưa mở được Chrome: '+str(exc))
            self.refresh_buttons()
            self.studio.refresh_buttons()

    def finish_chrome_login(self):
        if self.chrome_login_pending:
            self.chrome_login_finished.set()
            self.chrome_login_status.set('Đang đóng Chrome và lưu hồ sơ. Chờ xong rồi bấm Lấy danh sách.')
            self.chrome_login_done_button.configure(state='disabled')

    def show_scan_report(self):
        from tkinter import messagebox
        try:
            report = read_json(self.data_dir/'channel-scan-last.json')
            lines = [str(report.get('time', '')), str(report.get('last_message', ''))]
            if report.get('status') == 'running':
                lines.append('Lượt đọc chưa có kết quả kết thúc. Nếu vừa mở lại Tèo, đây có thể là báo cáo lượt trước.')
            if report.get('message'):
                lines.append(str(report['message']))
            for cause in report.get('causes', []):
                lines.append(str(cause.get('message', '')))
            messagebox.showinfo('Báo cáo mở trình duyệt', '\n\n'.join(lines), parent=self)
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            self.status.set('Chưa có báo cáo đọc được. Chọn trình duyệt và bấm Lấy danh sách để thử. '+str(exc))

    def import_channel_page(self):
        if self.scanning or self.studio.campaign_busy or self.studio.process is not None or self.studio.login_pending:
            self.status.set('Đợi bước đang chạy hoàn tất rồi nhập danh sách kênh.')
            return
        from tkinter import messagebox
        messagebox.showinfo('Nhập từ Cốc Cốc đang dùng',
            'Trong Cốc Cốc của bạn: mở trang kênh, chọn tab Tác phẩm, cuộn để tải thêm video. '
            'Bấm Ctrl+S để lưu trang dạng HTML, rồi chọn tệp đó ở bước tiếp theo. '
            'Tèo chỉ lấy link video trong trang đã lưu; số lượng tùy phần trang đã tải.', parent=self)
        path = filedialog.askopenfilename(parent=self, title='Chọn trang kênh Douyin đã lưu',
                                         filetypes=[('Trang HTML', '*.html *.htm')])
        if not path:
            return
        try:
            from teodub.saved_channel import import_saved_channel
            from teodub.channel_discovery import channel_url
            text = self.source.get('1.0', 'end').strip()
            try:
                expected = channel_url(text) if text else None
            except ValueError:
                expected = None
            result = import_saved_channel(path, limit=int(self.limit.get()), expected_channel=expected)
            self.auto_fetch.set(False)
            self.source_kind.set('Link kênh Douyin')
            self.source.delete('1.0', 'end')
            self.source.insert('1.0', result['channel'])
            self.auto_key = self.input_key()
            self.discovery = dict(input=result['channel'], result=result)
            self.preview_records(result['items'])
            self.status.set(result['message']+' Bấm Tạo chiến dịch mới để lưu.')
            self.refresh_buttons()
        except (OSError, ValueError) as exc:
            self.status.set('Chưa nhập được trang: '+str(exc))

    def capture_settings(self):
        try:
            from teodub.ai_models import validate_models
            from teodub.app import SPEEDS
            from teodub.subtitle_style import validate_subtitle_style
            fields = self.studio.fields
            speed = SPEEDS.get(fields['speed'].get())
            if speed is None:
                raise ValueError('Chọn tốc độ hình ở tab Video đơn.')
            selected = dict(self.studio.voice_selection(),
                **validate_models({key: value.get() for key, value in fields.items()}),
                **validate_subtitle_style({key: value.get() for key, value in fields.items()}),
                wording=self.studio.wording_selection(), speed=speed, **self.studio.audio_mix_selection())
            selected['drive_upload'] = (self.studio.drive_selection() if hasattr(self.studio, 'drive_selection')
                                            else dict(version=1, enabled=False, account_id='', folder_id='', folder_name=''))
            selected['drive_data_dir'] = str(self.data_dir)
            selected['subtitle_clean'] = (self.studio.clean_selection() if hasattr(self.studio, 'clean_selection')
                                          else dict(enabled=False))
            if selected['subtitle_clean'].get('source_sha256'):
                # A single-video region must never be applied to an entire different channel.
                selected['subtitle_clean'] = dict(enabled=False)
            self.settings = selected
            self.settings_error = ''
            from teodub.campaign_preview import describe_pipeline_settings
            self.creation_summary.set('Cấu hình tạo chiến dịch mới: '+describe_pipeline_settings(selected))
            if self.directory is None:
                self.summary.set(self.creation_summary.get())
            return True
        except (OSError, ValueError) as exc:
            self.settings_error = str(exc)
            self.creation_summary.set('Cấu hình tạo mới chưa hợp lệ: '+str(exc))
            if self.directory is None:
                self.summary.set(self.creation_summary.get())
            self.status.set(str(exc))
            return False

    def toggle_creation_settings(self):
        if self.creation_settings_frame.winfo_manager():
            self.creation_settings_frame.pack_forget()
        else:
            self.capture_settings()
            self.creation_settings_frame.pack(fill='x', pady=4)

    def apply_single_settings(self):
        if self.studio.closed or self.studio.closing or not self.capture_settings():
            return
        if self.directory is None:
            self.status.set('Đã lấy cấu hình Video đơn. Bấm Tạo chiến dịch mới để lưu.')
            return
        try:
            from teodub.campaign_settings import save_next_settings
            record = save_next_settings(self.data_dir, self.directory, self.settings)
            self.display(read_json(self.directory/'campaign.json'))
            self.launch_notice = (self.directory, 'Đã lưu cấu hình '+record['id'][:8]+' cho các video chưa hoàn tất; '
                            'áp dụng từ video chạy tiếp theo. Giữ nguyên video đang chạy và thành phẩm đã xong.')
            self.notice.set(self.launch_notice[1])
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.launch_notice = (self.directory, 'Chưa áp dụng cấu hình: '+str(exc))
            self.notice.set(self.launch_notice[1])

    def update_execution_summary(self, manifest, *, active=False):
        from teodub.campaign_preview import next_video_config, describe_pipeline_settings
        self.retry_summary.set('')
        try:
            preview = next_video_config(self.directory, manifest, retry_failed=self.preview_retry_failed, active=active)
            if preview:
                prefix = ('Đang xử lý' if active and preview['status'] == 'running' else 'Video chạy tiếp theo')
                self.summary.set(f'{prefix}: Video {preview["number"]} | '+describe_pipeline_settings(preview['config']))
                if preview.get('future_config'):
                    self.summary.set('Cấu hình video chạy tiếp theo: '+describe_pipeline_settings(preview['future_config']))
                    self.retry_summary.set(f'Video {preview["number"]} đang xử lý vẫn giữ: '+describe_pipeline_settings(preview['config']))
            else:
                self.summary.set('Không còn video chờ chạy với Chạy / Tiếp tục.')
            if not self.retry_summary.get() and not self.preview_retry_failed:
                retry = next_video_config(self.directory, manifest, retry_failed=True)
                if retry and (not preview or retry['number'] != preview['number']):
                    self.retry_summary.set(f'Thử lại video lỗi bắt đầu ở Video {retry["number"]}: '+describe_pipeline_settings(retry['config']))
            self.saved_voice.set(self.summary.get())
            return True
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.summary.set('Chưa xác minh được cấu hình video tiếp theo: '+str(exc))
            self.saved_voice.set(self.summary.get())
            return False

    def apply_learned_voice_settings(self, selection):
        if self.studio.closed or self.studio.closing or self.directory is None:
            raise ValueError('Chọn chiến dịch trước khi áp dụng giọng đã học.')
        from teodub.campaign_settings import save_next_voice_settings
        record = save_next_voice_settings(self.data_dir, self.directory, selection)
        manifest = read_json(self.directory/'campaign.json')
        self.display(manifest)
        message = ('Đã áp dụng giọng đã học cho '+manifest['name']+' từ video chạy tiếp theo. '
                   'Giữ nguyên video đang chạy, thành phẩm và các cấu hình khác của chiến dịch.')
        self.launch_notice = (self.directory, message)
        self.notice.set(message)
        return record

    def choose_drive(self):
        if not hasattr(self.studio, 'choose_drive'):
            return
        dialog = self.studio.choose_drive()
        if dialog is not None:
            dialog.bind('<Destroy>', lambda event: self.capture_settings() if event.widget == dialog
                        and not self.studio.closed and not self.studio.closing else None, add='+')

    def preview_records(self, records):
        self.records = records
        self.posting_statuses = {}
        self.drive_statuses = {}
        self.big_for = None
        self.directory = None
        self.saved_voice.set('')
        self.retry_summary.set('')
        self.preview_retry_failed = False
        self.summary.set(self.creation_summary.get())
        self.saved.set('')
        self.notice.set('')
        expected = {str(i) for i in range(1, len(records)+1)}
        for key in set(self.tree.get_children())-expected:
            self.tree.delete(key)
        for number, row in enumerate(records, 1):
            values = (f'Video {number}', row.get('title') or '(Chưa có tiêu đề)', row['url'], 'Chờ tạo chiến dịch', '0%', '—', 'Chưa đăng', '—', '')
            if self.tree.exists(str(number)):
                self.tree.item(str(number), values=values, tags=())
            else:
                self.tree.insert('', 'end', iid=str(number), values=values)
        self.progress.set(0)

    def input_key(self):
        return (self.source.get('1.0', 'end').strip(), self.source_kind.get(), self.limit.get(), self.visible.get(), self.browser_label.get())

    def selected_browser(self):
        browser = next((key for key, label in BROWSER_LABELS.items() if label == self.browser_label.get()), None)
        if browser is None:
            raise ValueError('Chọn trình duyệt đọc kênh trong danh sách.')
        return browser

    def save_campaign_preferences(self):
        atomic_json(self.data_dir/'campaign-preferences.json',
                    dict(original_dir=self.original_dir.get(), output_dir=self.output_dir.get(),
                         browser=self.selected_browser(), visible=self.visible.get(), auto_fetch=self.auto_fetch.get()))

    def auto_fetch_changed(self, *_args):
        if self.loading_preferences:
            return
        self.schedule_discovery()
        self.remember_browser_settings()

    def remember_browser_settings(self):
        try:
            self.save_campaign_preferences()
        except (OSError, ValueError) as exc:
            self.status.set('Chưa lưu được lựa chọn trình duyệt: '+str(exc))

    def visibility_changed(self, *_args):
        if self.loading_preferences:
            return
        self.schedule_discovery()
        self.remember_browser_settings()

    def browser_changed(self, *_args):
        if self.loading_preferences:
            return
        # An explicit Cốc Cốc choice should initially show the window. A user can
        # still turn this off, and the saved choice is respected when reopening.
        if self.browser_label.get() in (BROWSER_LABELS['coccoc'], BROWSER_LABELS['chrome']) and not self.visible.get():
            self.visible.set(True)
        else:
            self.schedule_discovery()
            self.remember_browser_settings()

    def source_modified(self, _event=None):
        if self.source.edit_modified():
            self.source.edit_modified(False)
            if self.scanning and self.scan_key != self.input_key():
                self.scan_cancel.set()
            self.schedule_discovery()

    def on_destroy(self, event):
        if event.widget is self:
            self.scan_cancel.set()
            self.chrome_login_cancel.set()
            if self.auto_timer is not None:
                self.after_cancel(self.auto_timer)
                self.auto_timer = None

    def schedule_discovery(self):
        if self.scanning and self.scan_key != self.input_key():
            self.scan_cancel.set()
        if self.auto_timer is not None:
            self.after_cancel(self.auto_timer)
            self.auto_timer = None
        if self.auto_fetch.get() and not self.studio.closed:
            self.auto_timer = self.after(900, self.auto_discover)

    def auto_discover(self):
        self.auto_timer = None
        if self.studio.closed or not self.auto_fetch.get():
            return
        key = self.input_key()
        if not key[0] or key == self.auto_key:
            return
        # Running campaigns are no reason to wait: a new list joins them in turns (owner 23/10/2026).
        if self.scanning or self.studio.process is not None or self.studio.login_pending:
            self.auto_timer = self.after(1000, self.auto_discover)
            return
        # Incomplete typing is not a request; never loop a failed browser scan.
        from teodub.channel_discovery import classify_input
        try:
            classify_input(key[0], key[1])
            if not 1 <= int(key[2]) <= MAX_SCAN_VIDEOS:
                return
        except ValueError:
            return
        self.discover()

    def discover(self):
        if self.scanning or self.studio.process is not None or self.studio.login_pending:
            return
        text = self.source.get('1.0', 'end').strip()
        self.discovery = None
        self.discover_notice = ''
        self.notice.set('')
        try:
            from teodub.channel_discovery import classify_input, scan_channel
            browser = self.selected_browser()
            kind, value = classify_input(text, self.source_kind.get())
            self.auto_key = self.input_key()
            if kind == 'videos':
                self.preview_records(value)
                self.status.set(f'{len(self.records)} link không trùng. Bấm Tạo chiến dịch mới để lưu.')
                self.refresh_buttons()
                return
            try:
                limit = int(self.limit.get())
            except ValueError:
                raise ValueError('Ô Tối đa phải là một số.') from None
            if not 1 <= limit <= MAX_SCAN_VIDEOS:
                raise ValueError('Chọn từ 1 đến 10.000 video.')
            self.save_campaign_preferences()
        except (ValueError, OSError) as exc:
            self.discover_notice = 'Chưa lấy danh sách: '+str(exc)+' Sửa rồi bấm Lấy danh sách lại.'
            self.status.set(str(exc))
            self.notice.set(self.discover_notice)
            return
        self.scanning = True
        self.scan_cancel.clear()
        self.scan_key = self.input_key()
        key = self.scan_key
        allow_video = self.source_kind.get() == 'Tự nhận dạng link'
        self.preview_records([])
        visible = self.visible.get()
        def scan():
            last_message = {'value': 'Đang mở trình duyệt'}
            def progress(message):
                last_message['value'] = str(message)
                report(dict(status='running'))
                self.scan_messages.put((key, 'progress', message))
            def report(data):
                try:
                    atomic_json(self.data_dir/'channel-scan-last.json', dict(
                        time=datetime.now().isoformat(timespec='seconds'), input=text,
                        requested_browser=browser, visible=visible, last_message=last_message['value'], **data))
                except OSError:
                    pass
            try:
                progress('Đang chuẩn bị mở '+BROWSER_LABELS[browser]+(' · hiện cửa sổ.' if visible else ' · chạy ẩn.'))
                result = scan_channel(text, profile_directory(self.data_dir/'douyin-channel-browser', browser), limit=limit,
                                      visible=visible, cancel=self.scan_cancel, browser=browser,
                                      allow_video=allow_video,
                                      on_items=lambda items: self.scan_messages.put((key, 'items', items)),
                                      progress=progress)
                report(dict(status='completed', browser=result.get('browser'), count=len(result['items']),
                            complete=result['complete'], reason=result['reason'], message=result['message']))
                self.scan_messages.put((key, 'done', (text, result)))
            except Exception as exc:
                causes = []
                cause = exc
                while cause is not None and len(causes) < 4:
                    causes.append(dict(type=type(cause).__name__, message=str(cause).splitlines()[0][:1500] if str(cause) else ''))
                    cause = cause.__cause__
                report(dict(status='failed', message=str(exc), causes=causes))
                self.scan_messages.put((key, 'error', str(exc)))
        threading.Thread(target=scan, daemon=True).start()
        self.status.set('Đang mở '+BROWSER_LABELS[browser]+(' · hiện cửa sổ.' if visible else ' · chạy ẩn.'))
        self.refresh_buttons()

    def create(self):
        if self.creating:
            return
        self.creating = True
        self.create_button.configure(text='Đang lưu…', state='disabled')
        self.update_idletasks()
        try:
            self._create()
        finally:
            self.creating = False
            self.create_button.configure(text='Tạo chiến dịch mới')
            self.refresh_buttons()

    def _create(self):
        if self.scanning or self.studio.process is not None or self.studio.login_pending:
            return
        # Campaign work may run meanwhile: the new campaign then joins it in turns (owner 23/10/2026).
        running = bool(self.studio.campaign_busy)
        if running:
            from tkinter import messagebox
            batch = getattr(self.studio, 'batch_controller', None)
            serial = bool(batch and batch.busy and batch.rotation() is None)
            question = ('Một danh sách đang chạy lần lượt. Chiến dịch mới sẽ được lưu nhưng chưa tự chạy; '
                        'bấm Chạy / Tiếp tục khi danh sách xong. Tạo chiến dịch?' if serial else
                        'Đang có chiến dịch chạy. Tạo chiến dịch mới và chạy xen kẽ: mỗi chiến dịch một video, lần lượt? '
                        'Video đang làm sẽ làm xong rồi mới chuyển.')
            if not messagebox.askyesno('Tạo chiến dịch mới', question, parent=self):
                return
        try:
            # The single-video form may have changed since this tab was opened.
            # Freeze its complete current profile only at NEW campaign creation;
            # selecting/resuming a saved campaign must retain its original jobs.
            self.capture_settings()
            if self.settings_error:
                raise ValueError(self.settings_error+' Sửa lựa chọn ở tab Video đơn rồi tạo lại.')
            if hasattr(self.studio, 'drive_selection'):
                # The checkbox is shared across tabs; never silently ignore a
                # newly enabled/changed Drive target while using cached voices.
                self.settings = dict(self.settings, drive_upload=self.studio.drive_selection(),
                                     drive_data_dir=str(self.data_dir))
            text = self.source.get('1.0', 'end').strip()
            from teodub.channel_discovery import classify_input
            kind, value = classify_input(text, self.source_kind.get())
            if kind == 'videos':
                records = value
                discovery = None
            else:
                if not self.discovery or self.discovery['input'] != text:
                    raise ValueError('Bấm Lấy danh sách kênh và kiểm tra trước khi tạo chiến dịch.')
                discovery = self.discovery['result']
                records = discovery['items']
            if self.reverse.get():
                records = list(reversed(records))
            def saving(done, total):  # Saving thousands of videos takes minutes: show it is working.
                self.status.set(f'Đang lưu chiến dịch… {done}/{total} video. Cửa sổ có thể đứng im một lúc; đừng đóng ứng dụng.')
                self.update_idletasks()
            directory = create_campaign(self.data_dir, self.name.get(), records, self.settings,
                                        self.original_dir.get(), self.output_dir.get(), discovery, prevent_duplicates=True,
                                        progress=saving)
            self.save_campaign_preferences()
            self.directory = directory
            self.records = list(records)
            self.refresh_saved()
            outcome, problem = None, ''
            if running:
                try:
                    outcome = self.join_running(directory)
                except (ValueError, OSError, KeyError, TypeError) as exc:
                    problem = str(exc)
            self.display(read_json(directory/'campaign.json'))
            saved = f'Đã lưu chiến dịch {len(records)} video.'
            area = getattr(self.studio, 'campaign_area', None)
            self.status.set(saved+({
                'handoff': ' Chiến dịch đang chạy làm xong video hiện tại thì Tèo chạy xen kẽ: mỗi chiến dịch một video, lần lượt.',
                'joined': ' Đã thêm vào vòng chạy xen kẽ; tới lượt là chạy.',
                'serial': ' Danh sách đang chạy lần lượt nên chiến dịch này chưa tự chạy; bấm Chạy / Tiếp tục khi danh sách xong.',
            }.get(outcome) or (' Chưa thêm được vào vòng chạy xen kẽ: '+problem if problem else
                               ' Bấm Chạy / Tiếp tục để bắt đầu.')))
            self.refresh_buttons()
            if area is not None:  # Each link gets its own sub-tab, named like the campaign (owner 23/10/2026).
                area.open_link(directory, message=self.status.get().replace('Chạy / Tiếp tục', 'Chạy'))
        except (ValueError, OSError, KeyError) as exc:
            self.status.set('Chưa tạo: '+str(exc))

    def join_running(self, directory):
        """Join a campaign created while campaign work runs; see CampaignBatchController.join."""
        batch = getattr(self.studio, 'batch_controller', None)
        if batch is None:
            return None
        running = self.worker_directory if self.process is not None and self.process.poll() is None else None
        if running is None and not batch.busy:
            # A campaign worker this window did not start (Tèo was reopened while it ran).
            running = next((path for path in self.saved_paths.values()
                            if Path(path).resolve() != Path(directory).resolve() and is_running(path)), None)
        return batch.join(directory, running)

    def rotation_running(self):
        # Reading the queue first: probing a worker's lock only when it is a rotation.
        batch = getattr(self.studio, 'batch_controller', None)
        return bool(batch is not None and batch.rotation() is not None and batch.busy)

    def resume_rotation(self):
        """Chạy / Tiếp tục on a campaign of a stopped rotation continues the rotation, not this campaign alone."""
        batch = getattr(self.studio, 'batch_controller', None)
        queue = batch.rotation() if batch is not None and self.directory else None
        if not queue or queue.get('status') not in ('ready', 'paused') or not batch.includes(self.directory, queue):
            return False
        target = str(Path(self.directory).resolve())
        if any(row.get('directory') == target and row.get('status') in ('completed', 'needs_attention')
               for row in queue['campaigns']):
            return False
        try:
            if any(row.get('directory') == target and row.get('status') == 'stopped' for row in queue['campaigns']):
                from teodub.campaign_batch import add_to_rotation
                add_to_rotation(batch.directory, self.directory)  # Stopped on its own tab: back into the turns.
            batch.resume()
            self.status.set('Đang chạy tiếp vòng xen kẽ: mỗi chiến dịch một video, lần lượt.')
        except (OSError, ValueError) as exc:
            self.status.set('Chưa chạy tiếp vòng xen kẽ: '+str(exc))
        self.refresh_buttons()
        return True

    def continue_rotation(self, busy):
        """Start the rotation once the running campaign handed over; restart it when a download source is back."""
        batch = getattr(self.studio, 'batch_controller', None)
        if batch is None or self.studio.closed or self.studio.closing:
            return
        try:
            if batch.complete_handoff() == 'started':
                self.status.set('Bắt đầu chạy xen kẽ: mỗi chiến dịch một video, lần lượt.')
                return
        except (OSError, ValueError):
            pass  # Something else runs now (a list being read, a single video); try again on a later poll.
        queue = batch.rotation()
        now = time.time()
        if busy or not queue or queue.get('status') != 'paused' or now-self.auto_resume_tried < 60 or batch.busy:
            return
        paused = next((row for row in queue.get('campaigns', []) if row.get('status') == 'paused'), None)
        try:
            due = bool(paused) and auto_resume_due(read_json(Path(paused['directory'])/'campaign.json'), now)
        except (OSError, ValueError, KeyError, TypeError):
            due = False
        if due:
            self.auto_resume_tried = now
            try:
                batch.resume()
                self.status.set('Đã đến giờ thử lại nguồn tải; Tèo tự chạy tiếp vòng xen kẽ.')
            except (OSError, ValueError):
                pass

    def refresh_saved(self):
        self.saved_paths = {}
        if self.directory is None:
            self.saved_voice.set('')
        root = self.data_dir/'campaigns'
        for path in sorted(root.glob('*/campaign.json'), reverse=True):
            try:
                item = read_json(path)
                if item.get('archived_at'):
                    continue
                label = f'{item["created_at"][:16]} · {item["name"]} · {item["id"][:8]}'
                self.saved_paths[label] = path.parent
                if self.directory == path.parent:
                    self.saved.set(label)
            except (OSError, ValueError, KeyError):
                continue
        self.saved_picker.configure(values=list(self.saved_paths))
        if self.directory is None and self.saved_paths:
            active = next((p for p in self.saved_paths.values() if is_running(p)), None)
            self.directory = active or max(self.saved_paths.values(), key=lambda p: (p/'campaign.json').stat().st_mtime)
            self.saved.set(next(k for k, v in self.saved_paths.items() if v == self.directory))
            self.display(read_json(self.directory/'campaign.json'))

    def select_saved(self, _event=None):
        self.preview_retry_failed = False
        self.directory = self.saved_paths.get(self.saved.get())
        if self.directory:
            manifest = read_json(self.directory/'campaign.json')
            self.records = [dict(url=x['url'], title=x['title']) for x in manifest['items']]
            self.display(manifest)
        self.refresh_buttons()

    def start(self, retry=False):
        if (not self.directory or self.scanning or self.studio.process is not None or self.studio.campaign_busy
                or self.studio.login_pending or getattr(self.studio, 'voice_learning_busy', False) is True):
            return
        if not retry and self.resume_rotation():
            return
        try:
            self.launch_notice = None
            self.preview_retry_failed = bool(retry)
            if not self.update_execution_summary(read_json(self.directory/'campaign.json')):
                raise ValueError(self.summary.get())
            # Run on its own: a hand-over asked of an earlier run must not end this one after its first video.
            (self.directory/ROTATION_REQUEST).unlink(missing_ok=True)
            from teodub.app import worker_command
            # Same interpreter/frozen entry point as single-video workers.
            command = worker_command(self.directory/'unused.json')
            command = command[:command.index('--worker')]+['--campaign-worker', '--directory', str(self.directory)]
            if retry:
                command.append('--retry-failed')
            with (self.directory/'campaign-worker.log').open('ab') as log:
                self.process = subprocess.Popen(command, cwd=str(self.directory), stdout=log, stderr=log,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            self.worker_directory = Path(self.directory)
            self.studio.campaign_busy = True
            self.studio.refresh_buttons()
            self.status.set('Đang khởi động chiến dịch…')
            self.refresh_buttons()
        except (OSError, ValueError) as exc:
            self.status.set('Chưa chạy: '+str(exc))
            self.launch_notice = (self.directory, 'Chưa khởi động chiến dịch: '+str(exc))
            self.notice.set(self.launch_notice[1])

    def stop(self):
        reading = self.chrome_login_pending or self.scanning
        if self.chrome_login_pending:
            self.chrome_login_cancel.set()
        if self.scanning:
            self.scan_cancel.set()
        if reading and self.studio.campaign_busy:
            # Reading a new list while campaigns run: Dừng ends the reading only; the campaigns go on.
            self.status.set('Đã dừng lấy danh sách; chiến dịch đang chạy vẫn tiếp tục.')
            return
        active_directory = self.worker_directory if self.process and self.process.poll() is None else self.directory
        if active_directory and is_running(active_directory):
            (active_directory/'stop.request').touch()
        if self.rotation_running():
            self.studio.batch_controller.stop()  # Dừng stops the whole rotation, whichever campaign's turn it is.
        self.status.set('Đang dừng và giữ các phần đã hoàn tất…')
        self.notice.set('Đã yêu cầu Dừng; đang chờ bước hiện tại dừng và lưu tiến độ.')

    def display(self, manifest):
        counts = totals(manifest)
        active = is_running(self.directory) or (self.process is not None and self.process.poll() is None
            and (self.worker_directory or self.directory) == self.directory)
        self.update_execution_summary(manifest, active=active)
        label = 'Đang chạy' if active else {
            'ready': 'Sẵn sàng', 'completed': 'Hoàn tất', 'needs_attention': 'Còn video lỗi',
            'paused': 'Đã dừng', 'running': 'Bị gián đoạn · bấm Tiếp tục'}.get(manifest['status'], manifest['status'])
        # Campaigns in turns (owner 23/10/2026): resting between turns is not a stop.
        batch = getattr(self.studio, 'batch_controller', None)
        rotation = batch.rotation() if batch is not None else None
        target = str(Path(self.directory).resolve())
        # A link stopped on its own tab is out of the turns.
        in_rotation = bool(rotation) and any(row.get('directory') == target and row.get('status') != 'stopped'
                                             for row in rotation.get('campaigns', []))
        handoff = bool(rotation) and rotation.get('status') == 'ready' and rotation.get('handoff_from')
        rotation_busy = in_rotation and batch.busy
        if in_rotation and not active and manifest['status'] not in ('completed', 'needs_attention'):
            if rotation_busy:
                label = 'Chạy xen kẽ · chờ tới lượt'
            elif handoff:
                label = 'Chờ chạy xen kẽ (sau video đang làm)'
        self.status.set(f'{manifest["name"]}: {label} · Xong {counts["completed"]}/{counts["total"]} · '
                        f'Lỗi {counts["failed"]} · Chờ {counts["waiting"]}. '+manifest.get('message', ''))
        if active and handoff and in_rotation:
            self.status.set(self.status.get()+' Làm xong video đang chạy, Tèo chạy xen kẽ: mỗi chiến dịch một video, lần lượt.')
        discovery = manifest.get('discovery') or {}
        if discovery and not discovery.get('complete', False):
            self.status.set(self.status.get()+' · Danh sách chưa đủ kênh: '+discovery.get('message', ''))
        notice = campaign_notice(manifest, active=active)
        if in_rotation and not active and (rotation_busy or handoff) and manifest.get('status') == 'paused':
            notice = ''  # Waiting for its turn, not stopped.
        elif manifest.get('message') == ROTATION_WAIT and manifest.get('status') == 'paused' and not active:
            notice = 'Vòng chạy xen kẽ đang dừng. Bấm Chạy / Tiếp tục để chạy tiếp.'
        if self.discover_notice:
            notice = self.discover_notice
        if self.launch_notice and self.launch_notice[0] == self.directory:
            notice = self.launch_notice[1]
        elif active and (self.directory/'stop.request').exists():
            notice = 'Đã yêu cầu Dừng; đang chờ bước hiện tại dừng và lưu tiến độ.'
        self.notice.set(notice)
        self.progress.set(counts['completed']*100/max(1, counts['total']))
        expected = {str(x['number']) for x in manifest['items']}
        try:
            self.posting_statuses = campaign_posting_statuses(self.data_dir, self.directory, manifest)
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            self.posting_statuses = {item['number']: dict(label='Cần kiểm tra',
                details='Chưa đọc được trạng thái đăng bài: '+str(exc)) for item in manifest['items']}
        for item in manifest['items']:
            self.posting_statuses.setdefault(item['number'], dict(label='Cần kiểm tra',
                details='Chưa đọc được trạng thái đăng bài của video này.'))
        if len(manifest['items']) > BIG_TABLE_ITEMS:
            self.display_big_table(manifest, active, expected)
            return
        self.big_for = None
        self.drive_statuses = {}
        from teodub.campaign_settings import effective_job
        for item in manifest['items']:
            try:
                job = effective_job(self.directory, item)
                config = read_json(job/'config.json')
                self.drive_statuses[item['number']] = drive_upload_summary(config, job)
            except (OSError, ValueError, TypeError, KeyError):
                self.drive_statuses[item['number']] = dict(label='Cần kiểm tra', details='Chưa đọc được cấu hình Drive của video.')
        for key in set(self.tree.get_children())-expected:
            self.tree.delete(key)
        for item in manifest['items']:
            row = tracking_row(item, active=active)
            posting = self.posting_statuses.get(item['number'], {}).get('label', 'Cần kiểm tra')
            values = row[:-1]+(posting, self.drive_statuses[item['number']]['label'], row[-1])
            key = str(item['number'])
            if self.tree.exists(key):
                self.tree.item(key, values=values, tags=(item['status'],))
            else:
                self.tree.insert('', 'end', iid=key, values=values, tags=(item['status'],))

    def display_big_table(self, manifest, active, expected):
        """The table of a campaign with thousands of videos: rows are read a few at a time, never all at once.

        What changed since the last read and the video being made are read at once; the other rows are read in turns,
        at most BIG_TABLE_SECONDS per refresh, and again after BIG_TABLE_REFRESH_S. A row not read yet shows what the
        list already knows. The table is only written where a row differs from what it already shows.
        """
        from teodub.campaign_settings import effective_job
        if self.big_for != self.directory:
            self.big_rows, self.big_written, self.big_cursor, self.big_for = {}, {}, 0, self.directory
        items, rows = manifest['items'], self.big_rows
        started = time.monotonic()

        def read(item):
            row = tracking_row(item, active=active)
            try:
                job = effective_job(self.directory, item)
                drive = drive_upload_summary(read_json(job/'config.json'), job)
            except (OSError, ValueError, TypeError, KeyError):
                drive = dict(label='Cần kiểm tra', details='Chưa đọc được cấu hình Drive của video.')
            rows[item['number']] = (copy.deepcopy(item), row, drive, time.monotonic())

        for item in items:
            known = rows.get(item['number'])
            if item['status'] == 'running' or (known is not None and known[0] != item):
                read(item)
        count = len(items)
        first = self.big_cursor % count
        for step in range(count):
            if time.monotonic()-started > BIG_TABLE_SECONDS:
                break
            position = (first+step) % count
            item = items[position]
            known = rows.get(item['number'])
            if known is None or started-known[3] >= BIG_TABLE_REFRESH_S:
                read(item)
            self.big_cursor = position+1
        self.drive_statuses = {number: entry[2] for number, entry in rows.items()}
        for key in set(self.tree.get_children())-expected:
            self.tree.delete(key)
            self.big_written.pop(key, None)
        written = self.big_written
        for item in items:
            known = rows.get(item['number'])
            posting = self.posting_statuses.get(item['number'], {}).get('label', 'Cần kiểm tra')
            if known is None:
                values = (f'Video {item["number"]}', item.get('title') or '(Chưa có tiêu đề)', item['url'],
                          STATUS.get(item['status'], item['status']), '…', '—', posting, 'Đang đọc…', '')
            else:
                values = known[1][:-1]+(posting, known[2]['label'], known[1][-1])
            key = str(item['number'])
            stamp = (values, item['status'])
            if written.get(key) == stamp:
                continue
            if self.tree.exists(key):
                self.tree.item(key, values=values, tags=(item['status'],))
            else:
                self.tree.insert('', 'end', iid=key, values=values, tags=(item['status'],))
            written[key] = stamp

    def refresh_buttons(self):
        busy = (self.studio.process is not None or self.studio.campaign_busy or self.scanning or self.creating
            or self.studio.login_pending or getattr(self.studio, 'voice_learning_busy', False) is True)
        # Campaigns running are no reason to wait for a new list: it joins them in turns (owner 23/10/2026).
        new_blocked = (self.studio.process is not None or self.scanning or self.creating or self.studio.login_pending
            or getattr(self.studio, 'voice_learning_busy', False) is True)
        for button in (self.create_button, self.scan_button):
            button.configure(state='disabled' if new_blocked else 'normal')
        self.chrome_login_button.configure(state='disabled' if busy else 'normal')
        self.drive_button.configure(state='disabled' if busy else 'normal')
        self.drive_check.configure(state='disabled' if busy else 'normal')
        self.apply_settings_button.configure(state='disabled' if self.creating or self.scanning or self.studio.closed
                                             or self.studio.closing or self.studio.login_pending else 'normal')
        self.chrome_login_done_button.configure(state='normal' if self.chrome_login_pending and not self.chrome_login_finished.is_set() else 'disabled')
        self.desktop_browser_button.configure(state='disabled' if self.scanning or self.chrome_login_pending else 'normal')
        self.browser_picker.configure(state='disabled' if self.chrome_login_pending else 'readonly')
        for button in (self.start_button, self.retry_button):
            button.configure(state='normal' if self.directory and not busy else 'disabled')
        active_directory = self.worker_directory if self.process and self.process.poll() is None else self.directory
        self.stop_button.configure(state='normal' if self.scanning or self.chrome_login_pending or (active_directory and is_running(active_directory))
                                   or self.rotation_running() else 'disabled')

    def poll(self):
        if self.studio.closed:
            self.scan_cancel.set()
            self.chrome_login_cancel.set()
            if self.auto_timer is not None:
                self.after_cancel(self.auto_timer)
                self.auto_timer = None
            return
        while True:
            try:
                kind, value = self.chrome_login_messages.get_nowait()
            except queue.Empty:
                break
            if kind == 'progress':
                self.chrome_login_status.set(value)
            else:
                self.chrome_login_pending = self.studio.login_pending = False
                if kind == 'error':
                    self.chrome_login_status.set('Phiên Chrome đã dừng: '+value)
                else:
                    prefix = 'Hết 30 phút chờ. ' if value == 'timeout' else ''
                    self.chrome_login_status.set(prefix+'Đã đóng Chrome và giữ hồ sơ. Nếu đã đăng nhập, bấm Lấy danh sách để dùng phiên này. '
                                                 'Tèo chưa xác nhận trạng thái đăng nhập của Douyin.')
                self.studio.refresh_buttons()
        while True:
            try:
                key, kind, value = self.scan_messages.get_nowait()
            except queue.Empty:
                break
            if kind in ('done', 'error'):
                self.scanning = False
            if key != self.input_key():
                continue
            if kind == 'items':
                self.preview_records(value)
            elif kind == 'progress':
                self.status.set(value)
            elif kind == 'done':
                self.scanning = False
                text, result = value
                self.discovery = dict(input=text, result=result)
                self.preview_records(result['items'])
                self.status.set(f'Tìm thấy {len(result["items"])} video. {result["message"]} Bấm Tạo chiến dịch mới khi danh sách phù hợp.')
            else:
                self.scanning = False
                self.discover_notice = 'Chưa lấy được danh sách: '+value
                self.status.set(self.discover_notice)
                self.notice.set(self.discover_notice)
        child_alive = self.process is not None and self.process.poll() is None
        batch = getattr(self.studio, 'batch_controller', None)
        busy = child_alive or bool(batch and batch.busy) or (self.studio.process is None
            and getattr(self.studio, 'voice_learning_busy', False) is not True and is_running(self.data_dir/'execution'))
        if self.studio.campaign_busy != busy:
            self.studio.campaign_busy = busy
            self.studio.refresh_buttons()
        self.continue_rotation(busy)
        if self.directory and not self.scanning:
            try:
                manifest = read_json(self.directory/'campaign.json')
                self.display(manifest)
                self.resume_when_source_returns(manifest, busy)
            except (OSError, ValueError) as exc:
                self.status.set('Chưa đọc được tiến độ: '+str(exc))
        self.refresh_buttons()
        self.timer = self.after(1000, self.poll)

    def resume_when_source_returns(self, manifest, busy):
        """Only a campaign that paused itself because no download service worked, at most once a minute."""
        now = time.time()
        if busy or not auto_resume_due(manifest, now) or now-self.auto_resume_tried < 60:
            return
        self.auto_resume_tried = now
        self.start(retry=manifest.get('resume_retry_failed') is True)
        if self.process is not None:
            self.status.set('Đã đến giờ thử lại nguồn tải; Tèo tự chạy tiếp chiến dịch.')

    def displayed_records(self):
        if self.directory:
            return read_json(self.directory/'campaign.json')['items']
        return self.records

    def video_context_menu(self, event):
        iid = self.tree.identify_row(event.y)
        if not iid:
            self.schedule_context = None
            return
        self.tree.selection_set(iid)
        self.tree.focus(iid)
        # Freeze the row and campaign: a later refresh must not target another video.
        self.schedule_context = (self.directory, int(iid)) if self.directory else None
        usable = False
        if self.schedule_context:
            try:
                from teodub.page_schedule import campaign_video_source
                campaign_video_source(self.data_dir,*self.schedule_context)
                usable = True
            except (OSError, ValueError, KeyError, TypeError) as exc:
                self.notice.set('Chưa xếp lịch được video này: '+str(exc))
        self.video_menu.entryconfigure(0,state='normal' if usable else 'disabled')
        try:
            self.context_video_file()
            openable = True
        except (OSError,ValueError,KeyError,TypeError):
            openable = False
        self.video_menu.entryconfigure(1,state='normal' if openable else 'disabled')
        try:
            self.video_menu.tk_popup(event.x_root,event.y_root)
        finally:
            self.video_menu.grab_release()

    def context_video_file(self):
        if not self.schedule_context:
            raise ValueError('Chọn video thành phẩm trong chiến dịch đã lưu.')
        directory, number = self.schedule_context
        manifest = read_json(directory/'campaign.json')
        item = next((row for row in manifest['items'] if row['number']==number),None)
        if not item or item.get('status') not in {'completed','review_required'}:
            raise ValueError('Video chưa có thành phẩm để mở.')
        video = item.get('outputs',{}).get('video')
        if not video or Path(video).suffix.lower() not in {'.mp4','.mov'} or not Path(video).is_file():
            raise ValueError('Không tìm thấy video thành phẩm; tệp có thể đã được di chuyển.')
        return str(Path(video).resolve())

    def open_context_video(self):
        try:
            video = self.context_video_file()
            os.startfile(video)
            self.notice.set('Đã gửi lệnh mở video thành phẩm bằng trình xem video trên máy.')
        except (OSError,ValueError,KeyError,TypeError) as exc:
            self.notice.set('Chưa mở được video: '+str(exc))

    def schedule_selected_video(self):
        if not self.schedule_context:
            self.notice.set('Chọn một video thành phẩm đã hoàn tất trong chiến dịch.')
            return
        try:
            from teodub.page_schedule import campaign_video_source
            from teodub.page_schedule_ui import ScheduleVideoDialog
            source = campaign_video_source(self.data_dir,*self.schedule_context)
            owner = getattr(self.studio,'facebook_tab',None)
            if owner is None or owner.destroyed or self.studio.closed or self.studio.closing:
                raise ValueError('Mở lại Tèo khi công việc nghỉ để nạp phần quản lý Page và lịch đăng mới.')
            ScheduleVideoDialog(self,owner,source)
        except (OSError,ValueError,KeyError,TypeError) as exc:
            self.notice.set('Chưa mở được lịch đăng: '+str(exc))

    def copy_links(self, rows):
        if not rows:
            self.status.set('Chưa có link để sao chép.')
            return
        self.clipboard_clear()
        self.clipboard_append('\n'.join(row['url'] for row in rows))
        self.status.set(f'Đã sao chép {len(rows)} link video.')

    def copy_all(self):
        self.copy_links(self.displayed_records())

    def copy_selected(self):
        selected = set(self.tree.selection())
        self.copy_links([row for number, row in enumerate(self.displayed_records(), 1) if str(number) in selected])

    def open_folder(self, key):
        try:
            if self.directory:
                path = Path(read_json(self.directory/'campaign.json')[key])
                if path.is_dir():
                    os.startfile(path)
                else:
                    self.status.set('Chưa có video ở thư mục này; bấm Chạy / Tiếp tục.')
        except (OSError, ValueError) as exc:
            self.status.set(str(exc))

    def show_item(self, _event=None, *, detail_only=False):
        if not self.tree.selection():
            return
        from tkinter import messagebox
        number = int(self.tree.selection()[0])
        records = self.displayed_records()
        item = (next((item for item in records if item.get('number') == number), None)
                if self.directory else records[number-1])
        if item is None:
            return
        video = item.get('outputs', {}).get('video')
        if not detail_only and video and Path(video).is_file():
            os.startfile(video)
        else:
            row = tracking_row(dict(item, number=number, status=item.get('status', 'pending')),
                               active=bool(self.directory and is_running(self.directory)))
            detail = str(item.get('message') or row[-1]) if item.get('status') != 'running' else row[-1]
            detail = detail or 'Chưa ghi nhận thông tin bước xử lý.'
            text = (item['url']+f'\n\nTrạng thái: {row[3]} · Tiến độ: {row[4]}'
                    +f'\nThời gian hoàn thành: {row[5]}\nTổng thời gian xử lý: {processing_time(item)}'
                    +'\n\n'+detail)
            posting = self.posting_statuses.get(number)
            if posting:
                text += '\n\nTrạng thái đăng bài: '+posting['label']+'\n'+posting['details']
            drive = self.drive_statuses.get(number)
            if drive:
                text += '\n\nGoogle Drive: '+drive['label']+'\n'+drive['details']
            if item.get('job'):
                text += '\n\nNhật ký video: '+str(Path(item.get('execution_job') or item['job'])/'worker.log')
            messagebox.showinfo(f'Video {number}', text, parent=self)
