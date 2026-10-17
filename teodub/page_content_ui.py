"""Read a linked Codex conversation; plans stay passive until exact user review."""
from copy import deepcopy
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk

from teodub import codex_chat
from teodub.page_content_plan import ContentPlanStore
from teodub.page_schedule import STATE_LABELS, display_time


PLAN_STATES = {**STATE_LABELS, 'proposed': 'Chưa duyệt', 'draft': 'Chưa duyệt', 'ready': 'Chưa duyệt', 'pending': 'Chưa duyệt',
               'approved': 'Đã bật lịch', 'staged': 'Đã kiểm tra, chưa bật', 'invalid': 'Cần sửa',
               'failed': 'Có lỗi', 'published': 'Đã đăng', 'cancelled': 'Đã bỏ',
               'reviewed': 'Đã xử lý ở Duyệt caption'}
CONTENT_NOTE = ('Liên kết cuộc trò chuyện chứa chiến lược của Page để nhận kế hoạch đã hoàn tất. '
    'Sao chép yêu cầu bên dưới rồi dán vào cuộc trò chuyện khi cần lập kế hoạch mới. '
    'Yêu cầu AI trong cuộc trò chuyện sử dụng hạn mức Codex. Mỗi kế hoạch cần được duyệt trước khi bật lịch.')
EXECUTION_NOTE = ('Giữ Tèo mở và máy không ngủ; Facebook cần đăng nhập đúng tài khoản. '
    'Facebook có thể yêu cầu xác minh. Luồng video/Reels nhiều màn hình chưa hỗ trợ đầy đủ. '
    'Lỗi hoặc chưa rõ kết quả sẽ dừng để kiểm tra, không tự đăng lại.')


def item_body(item):
    return str(item.get('body', item.get('caption', '')))


def plan_key(plan):
    return plan.get('record_id') or plan['plan_id']


def item_time(item):
    value = item.get('due_at', item.get('publish_at', item.get('time', '')))
    if isinstance(value, (int, float)):
        return display_time(value)
    return str(value)


def item_video(item):
    source = item.get('source') or {}
    if isinstance(source, dict):
        return str(source.get('label', source.get('key', item.get('video_key', ''))))
    return str(item.get('video_key', ''))


def item_video_label(item):
    """Use a familiar title in the workspace; exact file details stay in review."""
    if item.get('kind') != 'video':
        return ''
    source = item.get('source') or {}
    if isinstance(source, dict):
        label = source.get('label') or source.get('path')
        if label:
            return str(label).replace('\\', '/').rsplit('/', 1)[-1]
    return 'Video thành phẩm'


def item_preview(item, plan, target):
    frozen = plan.get('target') or plan.get('snapshot') or target
    lines = [('Video' if item.get('kind') == 'video' else 'Bài chữ') + ' · ' + item_time(item),
             'Page: ' + str(frozen.get('name', frozen.get('target_name', target['name'])))]
    if item.get('kind') == 'video':
        lines.append('Video: ' + item_video_label(item))
    if item.get('goal'):
        lines.append('Mục tiêu: ' + str(item['goal']))
    return '\n'.join(lines + ['', item_body(item)])


def plan_preview(plan, target, account_id):
    """All scheduled content is shown verbatim, not a truncated table cell."""
    frozen = plan.get('target') or plan.get('snapshot') or target
    text = ['Tài khoản đã chọn: ' + account_id,
            'Page: ' + str(frozen.get('name', frozen.get('target_name', target['name']))),
            'Link Page: ' + str(frozen.get('url', frozen.get('target_url', target['url']))),
            'Page ID: ' + str(frozen.get('page_id', frozen.get('target_id', target.get('page_id', '')))),
            'Kế hoạch: ' + str(plan.get('title', plan.get('plan_id', ''))),
            'Nguồn: ' + ('AI phân tích độc lập theo chiến lược chat · lượt '+str(plan.get('source_run_id',''))
                        if plan.get('source_kind')=='scheduled_ai' else 'Câu trả lời trong chat liên kết'),
            'Mục tiêu: ' + str(plan.get('goal', '')),
            'Giờ theo Việt Nam (UTC+7).']
    for index, item in enumerate(plan.get('items', []), 1):
        source = item.get('source') or {}
        text.extend(['', 'Bài ' + str(index) + ' · ' + item_time(item),
                     'Loại: ' + ('Video' if item.get('kind') == 'video' else 'Bài chữ')])
        if item.get('kind') == 'video':
            text.append('Video: ' + item_video(item))
            if isinstance(source, dict):
                text.extend(['Tệp: ' + str(source.get('path', '')),
                             'Mã kiểm tra video: ' + str(source.get('sha256', ''))])
        text.extend(['Mục tiêu bài: ' + str(item.get('goal', '')), 'Nội dung sẽ đăng:', item_body(item)])
    return '\n'.join(text)


class PageContentPanel(ttk.Frame):
    """Tk owns all widgets; background readers return plain data via a queue."""
    def __init__(self, parent, manager, *, store=None, reader=None):
        super().__init__(parent, padding=12)
        self.manager = manager
        self.account_id, self.page_key = manager.account_id, manager.page_key
        self.store = store or ContentPlanStore(manager.owner.studio.data_dir)
        self.reader = reader or codex_chat
        self.closed = self.busy = self.approving = False
        self.generation = 0
        self.messages = queue.Queue()
        self.timer = self.auto_timer = None
        self.threads = []
        self.plans_by_id = {}
        self.plan_order = []
        self.plan_signature = None
        self.last_refresh = time.monotonic()
        self.selected_plan_id = ''
        self.preview_window = self.preview_data = None
        self.search = tk.StringVar(self, 'Phát triển page câu cá')
        self.thread_choice = tk.StringVar(self)
        self.plan_choice = tk.StringVar(self)
        self.auto_sync = tk.BooleanVar(self, False)
        self.connection_text = tk.StringVar(self)
        self.source_summary = tk.StringVar(self)
        self.strategy_open = False
        self.help_open = False
        self.status = tk.StringVar(self, 'Chọn kế hoạch để xem nội dung và duyệt bật lịch.')
        self.controls = []
        self.bind('<Destroy>', self.on_destroy, add='+')
        self.build()
        self.reload_connection()
        self.refresh_plans()
        self.timer = self.after(200, self.poll)
        self.arm_auto()

    def button(self, parent, text, command):
        widget = ttk.Button(parent, text=text, command=command)
        self.controls.append(widget)
        return widget

    def build(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(5, weight=3)
        self.rowconfigure(6, weight=2)
        self.wrap_labels = []
        ttk.Label(self, text='Xem kế hoạch, kiểm tra từng bài và duyệt để bật lịch.',
                  justify='left').grid(row=0, column=0, sticky='w')
        source_row = ttk.Frame(self)
        source_row.grid(row=1, column=0, sticky='ew', pady=(8, 6))
        source_row.columnconfigure(0, weight=1)
        source_label = ttk.Label(source_row, textvariable=self.source_summary, wraplength=560,
                                 justify='left')
        source_label.grid(row=0, column=0, sticky='ew', padx=(0, 8))
        self.source_toggle = ttk.Button(source_row, text='Nguồn chiến lược / Liên kết chat',
                                       command=self.toggle_strategy_settings)
        self.source_toggle.grid(row=0, column=1, sticky='e')
        self.strategy_frame = ttk.LabelFrame(self, text='Nguồn chiến lược / Liên kết chat', padding=10)
        self.strategy_frame.grid(row=2, column=0, sticky='ew', pady=(0, 8))
        self.strategy_frame.columnconfigure(0, weight=1)
        strategy_note = ttk.Label(self.strategy_frame, text=CONTENT_NOTE, wraplength=760, justify='left')
        strategy_note.grid(row=0, column=0, sticky='ew', pady=(0, 8))
        self.wrap_labels.append(strategy_note)
        search_row = ttk.Frame(self.strategy_frame)
        search_row.grid(row=1, column=0, sticky='ew', pady=(0, 5))
        search_row.columnconfigure(1, weight=1)
        ttk.Label(search_row, text='Tìm cuộc trò chuyện').grid(row=0, column=0, sticky='w')
        ttk.Entry(search_row, textvariable=self.search).grid(row=0, column=1, sticky='ew', padx=6)
        self.button(search_row, 'Tìm trong Codex', self.find_threads).grid(row=0, column=2)
        link_row = ttk.Frame(self.strategy_frame)
        link_row.grid(row=2, column=0, sticky='ew')
        link_row.columnconfigure(0, weight=1)
        self.thread_picker = ttk.Combobox(link_row, textvariable=self.thread_choice, state='readonly')
        self.thread_picker.grid(row=0, column=0, sticky='ew')
        self.button(link_row, 'Liên kết cuộc trò chuyện', self.save_link).grid(row=0, column=1, padx=(6, 0))
        self.button(link_row, 'Bỏ liên kết', self.disconnect).grid(row=0, column=2, padx=(6, 0))
        connection_label = ttk.Label(self.strategy_frame, textvariable=self.connection_text,
                                      wraplength=760, justify='left')
        connection_label.grid(row=3, column=0, sticky='ew', pady=5)
        self.wrap_labels.append(connection_label)
        controls = ttk.Frame(self.strategy_frame)
        controls.grid(row=4, column=0, sticky='ew')
        self.button(controls, 'Sao chép yêu cầu lập kế hoạch', self.copy_request).pack(side='left')
        self.auto_button = ttk.Checkbutton(controls, variable=self.auto_sync,
            text='Tự nhận kế hoạch mới (mỗi phút)', command=self.toggle_auto)
        self.auto_button.pack(side='left', padx=8)
        self.strategy_frame.grid_remove()
        plan_row = ttk.Frame(self)
        plan_row.grid(row=3, column=0, sticky='ew')
        plan_row.columnconfigure(1, weight=1)
        ttk.Label(plan_row, text='Kế hoạch').grid(row=0, column=0, sticky='w')
        self.plan_picker = ttk.Combobox(plan_row, textvariable=self.plan_choice, state='readonly')
        self.plan_picker.grid(row=0, column=1, sticky='ew', padx=6)
        self.plan_picker.bind('<<ComboboxSelected>>', self.select_plan)
        self.button(plan_row, 'Nhận kế hoạch mới', self.sync).grid(row=0, column=2, padx=(0, 6))
        self.approve_button = self.button(plan_row, 'Duyệt & bật lịch', self.show_preview)
        self.approve_button.grid(row=0, column=3)
        status_label = ttk.Label(self, textvariable=self.status, wraplength=800, justify='left')
        status_label.grid(row=4, column=0, sticky='ew', pady=8)
        self.wrap_labels.append(status_label)
        table_frame = ttk.Frame(self)
        table_frame.grid(row=5, column=0, sticky='nsew', pady=(0, 8))
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)
        columns = [('time', 'Giờ đăng (UTC+7)', 140), ('kind', 'Loại', 60),
                   ('video', 'Video thành phẩm', 150), ('body', 'Caption / nội dung', 270),
                   ('state', 'Trạng thái', 110)]
        self.tree = ttk.Treeview(table_frame, columns=[col[0] for col in columns], show='headings', height=6, selectmode='browse')
        for key, label, width in columns:
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, minwidth=50)
        self.tree.grid(row=0, column=0, sticky='nsew')
        scrollbar = ttk.Scrollbar(table_frame, command=self.tree.yview)
        scrollbar.grid(row=0, column=1, sticky='ns')
        horizontal = ttk.Scrollbar(table_frame, orient='horizontal', command=self.tree.xview)
        horizontal.grid(row=1, column=0, sticky='ew')
        self.tree.configure(yscrollcommand=scrollbar.set, xscrollcommand=horizontal.set)
        self.tree.bind('<<TreeviewSelect>>', self.show_item)
        detail_frame = ttk.LabelFrame(self, text='Nội dung bài đã chọn', padding=8)
        detail_frame.grid(row=6, column=0, sticky='nsew')
        detail_frame.columnconfigure(0, weight=1)
        detail_frame.rowconfigure(0, weight=1)
        self.detail = tk.Text(detail_frame, height=6, wrap='word', state='disabled')
        self.detail.grid(row=0, column=0, sticky='nsew')
        detail_scroll = ttk.Scrollbar(detail_frame, command=self.detail.yview)
        detail_scroll.grid(row=0, column=1, sticky='ns')
        self.detail.configure(yscrollcommand=detail_scroll.set)
        self.help_toggle = ttk.Button(self, text='Lưu ý khi bật lịch ▸', command=self.toggle_help)
        self.help_toggle.grid(row=7, column=0, sticky='w', pady=(6, 0))
        self.help_frame = ttk.Frame(self)
        self.help_frame.grid(row=8, column=0, sticky='ew', pady=(5, 0))
        self.help_frame.columnconfigure(0, weight=1)
        help_label = ttk.Label(self.help_frame, text=EXECUTION_NOTE, wraplength=800, justify='left')
        help_label.grid(row=0, column=0, sticky='ew')
        self.wrap_labels.append(help_label)
        self.help_frame.grid_remove()
        self.bind('<Configure>', self.resize_notes, add='+')

    def resize_notes(self, event):
        if event.widget is self:
            for label in self.wrap_labels:
                label.configure(wraplength=max(360, event.width - 60))

    def open_strategy_settings(self):
        self.strategy_open = True
        self.strategy_frame.grid()
        self.source_toggle.configure(text='Ẩn nguồn chiến lược ▾')

    def toggle_strategy_settings(self):
        if self.strategy_open:
            self.strategy_open = False
            self.strategy_frame.grid_remove()
            self.reload_source_summary()
        else:
            self.open_strategy_settings()

    def toggle_help(self):
        self.help_open = not self.help_open
        if self.help_open:
            self.help_frame.grid()
        else:
            self.help_frame.grid_remove()
        self.help_toggle.configure(text='Lưu ý khi bật lịch ' + ('▾' if self.help_open else '▸'))

    def reload_source_summary(self):
        connection = self.connection()
        title = connection.get('title') or 'Cuộc trò chuyện đã chọn'
        if connection.get('thread_id'):
            self.source_summary.set('Nguồn chiến lược: ' + title)
            collapsed_text = 'Nguồn chiến lược / Liên kết chat ▸'
        else:
            self.source_summary.set('Chưa có nguồn chiến lược. Liên kết chat để nhận kế hoạch cho Page này.')
            collapsed_text = 'Liên kết chat chiến lược ▸'
        if not self.strategy_open:
            self.source_toggle.configure(text=collapsed_text)

    def connection(self):
        return self.store.get_connection(self.account_id, self.page_key) or {}

    def reload_connection(self):
        connection = self.connection()
        self.auto_sync.set(bool(connection.get('auto_sync', False)))
        title = connection.get('title') or connection.get('thread_id', '')
        self.connection_text.set('Đã liên kết: ' + title if connection.get('thread_id') else
            'Page này chưa liên kết cuộc trò chuyện. Liên kết không áp dụng sang Page khác.')
        if connection.get('last_error'):
            self.connection_text.set(self.connection_text.get() + '\nLần nhận gần nhất: ' + connection['last_error'])
        self.reload_source_summary()

    def set_busy(self, value):
        self.busy = value
        for widget in self.controls:
            widget.configure(state='disabled' if value else 'normal')
        self.auto_button.configure(state='disabled' if value else 'normal')
        self.thread_picker.configure(state='disabled' if value else 'readonly')
        self.plan_picker.configure(state='disabled' if value else 'readonly')
        if not value:
            self.update_approval_state()

    def start_read(self, operation, callback, context=None):
        if self.closed or self.busy or self.approving:
            return False
        self.generation += 1
        generation = self.generation
        self.set_busy(True)
        def work():
            try:
                result, error = callback(), ''
            except Exception as exc:
                result, error = None, str(exc)
            self.messages.put((generation, operation, context, result, error))
        try:
            threading.Thread(target=work, name='TeoPageCodexReader', daemon=True).start()
        except (OSError, RuntimeError) as exc:
            self.set_busy(False)
            self.status.set('Chưa khởi động được bước xử lý: ' + str(exc))
            return False
        return True

    def find_threads(self):
        term = self.search.get().strip()
        if self.start_read('threads', lambda: self.reader.list_threads(search_term=term, limit=30)):
            self.status.set('Đang tìm cuộc trò chuyện đã lưu; không gửi yêu cầu AI.')

    def save_link(self):
        if self.closed or self.busy or self.approving:
            return
        index = self.thread_picker.current()
        if not 0 <= index < len(self.threads):
            self.status.set('Bấm Tìm trong Codex, rồi chọn đúng cuộc trò chuyện trong danh sách.')
            return
        thread = self.threads[index]
        try:
            self.store.save_connection(self.account_id, self.page_key, thread['id'], thread['title'], auto_sync=self.auto_sync.get())
            self.reload_connection()
            self.plan_signature = None
            self.refresh_plans()
            self.arm_auto()
            self.status.set('Đã lưu liên kết cho đúng Page này. Bấm Nhận kế hoạch mới để đọc câu trả lời đã hoàn tất.')
        except (OSError, ValueError, RuntimeError) as exc:
            self.status.set(str(exc))

    def disconnect(self):
        if self.closed or self.busy or self.approving:
            return
        try:
            self.store.disconnect(self.account_id, self.page_key)
            self.generation += 1
            self.reload_connection()
            self.update_approval_state()
            self.arm_auto()
            self.status.set('Đã bỏ liên kết; kế hoạch và lịch đã duyệt được giữ. Không đóng cuộc trò chuyện hoặc Chrome.')
        except (OSError, ValueError, RuntimeError) as exc:
            self.status.set(str(exc))

    def toggle_auto(self):
        if self.closed or self.busy or self.approving:
            return
        try:
            connection = self.connection()
            if not connection.get('thread_id'):
                raise ValueError('Liên kết cuộc trò chuyện trước khi bật tự nhận kế hoạch.')
            self.store.save_connection(self.account_id, self.page_key, connection['thread_id'], connection['title'], auto_sync=self.auto_sync.get())
            self.status.set('Tự nhận kế hoạch chỉ lưu để xem; mỗi kế hoạch mới vẫn cần bạn duyệt bật lịch.' if self.auto_sync.get() else
                'Đã tắt tự nhận kế hoạch; giữ lịch đã duyệt.')
        except (OSError, ValueError, RuntimeError) as exc:
            self.auto_sync.set(False)
            self.status.set(str(exc))
        self.arm_auto()

    def arm_auto(self):
        if self.auto_timer is not None:
            self.after_cancel(self.auto_timer)
            self.auto_timer = None
        # The singleton desktop receiver keeps watching when this panel is closed.
        # A standalone panel uses its own timer only when no bridge was created.
        shared = getattr(self.manager.owner, 'page_bridge', None)
        if not self.closed and self.auto_sync.get() and shared is None:
            self.auto_timer = self.after(60000, self.auto_tick)

    def auto_tick(self):
        self.auto_timer = None
        if self.closed:
            return
        if self.auto_sync.get() and not self.busy and not self.approving:
            self.sync()
        self.arm_auto()

    def sync(self):
        if self.closed or self.busy or self.approving:
            return
        try:
            connection = self.connection()
            ident = connection.get('thread_id', '')
            if not ident:
                raise ValueError('Liên kết một cuộc trò chuyện Codex trước khi nhận kế hoạch.')
            if self.start_read('sync', lambda: self.reader.read_thread(ident), context=ident):
                self.status.set('Đang đọc câu trả lời đã hoàn tất; chưa bật hoặc đăng lịch nào.')
        except (OSError, ValueError, RuntimeError) as exc:
            self.status.set(str(exc))

    def copy_request(self):
        if self.closed or self.busy or self.approving:
            return
        try:
            if self.start_read('copy', lambda: self.store.request_context(self.account_id, self.page_key)):
                self.status.set('Đang chuẩn bị danh sách thành phẩm để đưa vào yêu cầu lập kế hoạch.')
        except (OSError, ValueError, RuntimeError) as exc:
            self.status.set(str(exc))

    def refresh_plans(self):
        plans = self.store.plans(self.account_id, self.page_key)
        linked = self.connection().get('thread_id')
        if linked:
            plans = [plan for plan in plans if not plan.get('thread_id') or plan['thread_id'] == linked]
        signature = repr(plans)
        if signature == self.plan_signature:
            return
        self.plan_signature = signature
        self.plans_by_id = {plan_key(plan): plan for plan in plans}
        self.plan_order = [plan_key(plan) for plan in plans]
        self.plan_picker.configure(values=[str(plan.get('title') or plan['plan_id']) + ' · ' +
            PLAN_STATES.get(plan.get('state', ''), plan.get('state', 'Chưa duyệt')) for plan in plans])
        if self.selected_plan_id not in self.plans_by_id:
            self.selected_plan_id = self.plan_order[0] if self.plan_order else ''
        if self.selected_plan_id:
            self.plan_picker.current(self.plan_order.index(self.selected_plan_id))
        else:
            self.plan_choice.set('')
        self.render_plan()

    def select_plan(self, event=None):
        index = self.plan_picker.current()
        self.selected_plan_id = self.plan_order[index] if 0 <= index < len(self.plan_order) else ''
        self.render_plan()

    def render_plan(self):
        self.tree.delete(*self.tree.get_children())
        plan = self.plans_by_id.get(self.selected_plan_id)
        if plan:
            for index, item in enumerate(plan.get('items', [])):
                state = item.get('state', plan.get('state', 'proposed'))
                self.tree.insert('', 'end', iid=str(index), values=(item_time(item),
                    'Video' if item.get('kind') == 'video' else 'Bài chữ', item_video_label(item),
                    item_body(item), PLAN_STATES.get(state, state)))
            children = self.tree.get_children()
            if children:
                self.tree.selection_set(children[0])
                self.tree.focus(children[0])
                self.show_item()
            else:
                self.set_detail('Kế hoạch này chưa có bài đăng.')
        else:
            self.set_detail('Chưa có kế hoạch. Lập kế hoạch ở bước trước, hoặc mở Nguồn chiến lược / Liên kết chat để nhận từ cuộc trò chuyện.')
        self.update_approval_state()

    def set_detail(self, value):
        self.detail.configure(state='normal')
        self.detail.delete('1.0', 'end')
        self.detail.insert('1.0', value)
        self.detail.configure(state='disabled')

    def show_item(self, event=None):
        plan = self.plans_by_id.get(self.selected_plan_id)
        selection = self.tree.selection()
        if plan and selection:
            index = int(selection[0])
            self.set_detail(item_preview(plan['items'][index], plan, self.manager.target))

    def update_approval_state(self):
        plan = self.plans_by_id.get(self.selected_plan_id)
        enabled = bool(plan and self.connection().get('thread_id') and plan.get('items') and not plan.get('approved_at') and plan.get('state', 'proposed') not in {'approved', 'cancelled', 'invalid', 'reviewed'}
                       and not self.busy and not self.approving)
        self.approve_button.configure(state='normal' if enabled else 'disabled')

    def show_preview(self):
        if self.closed or self.busy or self.approving:
            return
        if self.preview_window is not None:
            self.preview_window.lift()
            return
        try:
            selected = self.plans_by_id.get(self.selected_plan_id)
            if not selected:
                raise ValueError('Chọn kế hoạch có bài đăng để duyệt.')
            plan = self.store.get_plan(selected['plan_id'], self.account_id, self.page_key)
            if plan_key(plan) != plan_key(selected):
                raise ValueError('Cuộc trò chuyện đã đổi. Nhận lại kế hoạch của đúng cuộc trò chuyện trước khi duyệt.')
            if not plan or not plan.get('items'):
                raise ValueError('Chọn kế hoạch có bài đăng để duyệt.')
            if plan.get('approved_at') or plan.get('state') in {'approved', 'cancelled', 'invalid', 'reviewed'}:
                raise ValueError('Kế hoạch này đã bật hoặc không thể duyệt. Kiểm tra trong tab Lịch đăng.')
            if self.start_read('stage', lambda: self.store.stage(plan['plan_id'], self.account_id,
                    self.page_key, expected_digest=plan['digest']), context=plan['plan_id']):
                self.status.set('Đang kiểm tra đúng video và chuẩn bị bản duyệt; chưa bật lịch hoặc đăng bài.')
        except (OSError, ValueError, RuntimeError) as exc:
            self.status.set(str(exc))

    def open_preview(self, plan):
        if self.closed:
            return
        if plan.get('approved_at'):
            self.refresh_plans()
            self.status.set('Kế hoạch đã được duyệt ở cửa sổ khác. Xem trạng thái trong Lịch đăng.')
            return
        try:
            self.preview_data = deepcopy(plan)
            modal = self.preview_window = tk.Toplevel(self)
            modal.title('Duyệt kế hoạch từ Codex — ' + self.manager.target['name'])
            modal.geometry('860x740')
            modal.transient(self.winfo_toplevel())
            shell = ttk.Frame(modal, padding=14)
            shell.pack(fill='both', expand=True)
            text = tk.Text(shell, wrap='word', height=20)
            text.pack(fill='both', expand=True)
            text.insert('1.0', plan_preview(plan, self.manager.target, self.account_id))
            text.configure(state='disabled')
            ttk.Label(shell, text=EXECUTION_NOTE, wraplength=800).pack(anchor='w', pady=8)
            ttk.Label(shell, text='Bấm nút dưới cho phép Tèo tự đăng các bài chính xác ở trên vào giờ đã ghi. '
                'Kế hoạch sau này vẫn cần duyệt riêng.', wraplength=800).pack(anchor='w')
            self.preview_status = tk.StringVar(modal)
            ttk.Label(shell, textvariable=self.preview_status, wraplength=800).pack(anchor='w', pady=6)
            actions = ttk.Frame(shell)
            actions.pack(fill='x')
            self.confirm_button = ttk.Button(actions, text='Đồng ý nội dung / Bật lịch tự đăng', command=self.approve)
            self.confirm_button.pack(side='left')
            ttk.Button(actions, text='Quay lại', command=self.close_preview).pack(side='right')
            modal.protocol('WM_DELETE_WINDOW', self.close_preview)
            modal.grab_set()
        except (OSError, ValueError, RuntimeError) as exc:
            self.status.set(str(exc))

    def approve(self):
        if self.closed or self.approving or self.preview_window is None or self.preview_data is None:
            return
        self.approving = True
        self.confirm_button.configure(state='disabled')
        complete = False
        try:
            plan = self.preview_data
            self.store.approve(plan['plan_id'], self.account_id, self.page_key,
                self.manager.owner.get_page_scheduler(), expected_digest=plan['digest'])
            self.status.set('Đã duyệt và bật lịch kế hoạch. Xem trạng thái từng bài trong tab Lịch đăng.')
            complete = True
            self.refresh_plans()
            if hasattr(self.manager, 'schedule_panel'):
                self.manager.schedule_panel.refresh()
        except (OSError, ValueError, RuntimeError) as exc:
            self.preview_status.set(str(exc))
            self.status.set(str(exc))
            self.confirm_button.configure(state='normal')
        finally:
            self.approving = False
            self.update_approval_state()
        if complete:
            self.close_preview()

    def close_preview(self):
        if self.approving:
            return
        if self.preview_window is not None:
            self.preview_window.destroy()
        self.preview_window = self.preview_data = None

    def poll(self):
        if self.closed:
            return
        while True:
            try:
                generation, operation, context, result, error = self.messages.get_nowait()
            except queue.Empty:
                break
            if generation != self.generation:
                continue
            self.set_busy(False)
            if error:
                prefix = {'stage': 'Chưa chuẩn bị được bản duyệt: ', 'copy': 'Chưa tạo được yêu cầu: '}.get(
                    operation, 'Chưa đọc được Codex: ')
                self.status.set(prefix + error)
                continue
            try:
                if operation == 'threads':
                    self.threads = result
                    self.thread_picker.configure(values=[row['title'] + ' · ' + row['id'][:8] for row in result])
                    if result:
                        linked = self.connection().get('thread_id', '')
                        index = next((index for index, row in enumerate(result) if row['id'] == linked), 0)
                        self.thread_picker.current(index)
                    else:
                        self.thread_choice.set('')
                    self.status.set('Tìm thấy ' + str(len(result)) + ' cuộc trò chuyện. Chọn đúng cuộc trò chuyện rồi bấm Liên kết.' if result else
                        'Chưa tìm thấy cuộc trò chuyện. Kiểm tra tên hoặc tìm bằng một phần tên.')
                elif operation == 'sync':
                    if self.connection().get('thread_id') != context:
                        continue
                    imported = self.store.import_messages(self.account_id, self.page_key, result)
                    self.refresh_plans()
                    self.status.set(str(imported.get('status') or
                        ('Đã nhận ' + str(imported.get('imported', 0)) + ' kế hoạch mới; chưa bật lịch.')))
                elif operation == 'copy':
                    self.clipboard_clear()
                    self.clipboard_append(result)
                    self.status.set('Đã sao chép. Dán vào cuộc trò chuyện đã liên kết và yêu cầu lập kế hoạch; Tèo sẽ nhận kế hoạch để bạn duyệt.')
                elif operation == 'stage':
                    if result.get('plan_id') != context:
                        raise ValueError('Bản duyệt khác kế hoạch đã chọn; chưa bật lịch.')
                    self.open_preview(result)
            except (OSError, ValueError, RuntimeError) as exc:
                self.status.set(str(exc))
        if not self.busy and not self.approving and time.monotonic() - self.last_refresh > 2:
            self.last_refresh = time.monotonic()
            try:
                self.refresh_plans()
                self.reload_connection()
            except (OSError, ValueError, RuntimeError) as exc:
                self.status.set(str(exc))
        self.timer = self.after(200, self.poll)

    def on_destroy(self, event):
        if event.widget is not self:
            return
        self.closed = True
        self.generation += 1
        for timer in (self.timer, self.auto_timer):
            if timer is not None:
                try:
                    self.after_cancel(timer)
                except tk.TclError:
                    pass
        self.timer = self.auto_timer = None
