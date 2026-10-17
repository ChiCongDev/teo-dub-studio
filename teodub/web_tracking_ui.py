"""Tk monitoring tab; the web server remains the only queue owner."""
import os
import queue
import subprocess
import threading
import time
import tkinter as tk
from tkinter import ttk
import webbrowser

from teodub.web_tracking import (LOCAL_URL, PERIODS, STATES, date_text, job_directory,
                                 output_folder, snapshot, web_data_root, PROJECT)


class WebTrackingTab(ttk.Frame):
    def __init__(self, parent, root=None, *, autostart=True):
        super().__init__(parent, padding=12)
        self.data_root = web_data_root(root)
        self.records = {}
        self.users = {}
        self.results = queue.SimpleQueue()
        self.pending = False
        self.destroyed = False
        self.timer = None
        self.generation = 0
        self.due = 0
        self.last_update = None
        self.period = tk.StringVar(self, value=PERIODS[0])
        self.owner = tk.StringVar(self, value='Tất cả nhân viên')
        self.state = tk.StringVar(self, value='Tất cả trạng thái')
        self.status = tk.StringVar(self, value='Đang đọc hàng đợi web…')
        self.summary = tk.StringVar(self)
        self.metrics = {key: tk.StringVar(self, value='—') for key in
                        ('total', 'queued', 'working', 'completed', 'review', 'issues')}
        self.build()
        self.bind('<Destroy>', self.on_destroy, add='+')
        if autostart:
            self.refresh()
            self.timer = self.after(150, self.poll)

    def build(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(5, weight=1)
        top = ttk.Frame(self)
        top.grid(row=0, column=0, sticky='ew')
        ttk.Label(top, text='Theo dõi công việc web', font=('Segoe UI', 20, 'bold')).pack(side='left')
        ttk.Button(top, text='Mở web trên máy', command=lambda: webbrowser.open(LOCAL_URL)).pack(side='right')
        ttk.Button(top, text='Sao chép link web', command=self.copy_link).pack(side='right', padx=6)
        ttk.Button(top, text='Địa chỉ cố định…', command=self.configure_address).pack(side='right')
        ttk.Label(self, textvariable=self.status, wraplength=1000).grid(
            row=1, column=0, sticky='ew', pady=(5, 8))
        filters = ttk.Frame(self)
        filters.grid(row=2, column=0, sticky='ew')
        for label, variable, values, width in (
                ('Ngày gửi', self.period, PERIODS, 19),
                ('Nhân viên', self.owner, ('Tất cả nhân viên',), 27),
                ('Danh sách', self.state, ('Tất cả trạng thái', *STATES.values()), 19)):
            ttk.Label(filters, text=label).pack(side='left', padx=(0, 5))
            picker = ttk.Combobox(filters, textvariable=variable, values=values, state='readonly', width=width)
            picker.pack(side='left', padx=(0, 12))
            picker.bind('<<ComboboxSelected>>', self.filters_changed)
            if variable is self.owner:
                self.owner_picker = picker
        ttk.Button(filters, text='Làm mới', command=self.refresh).pack(side='right')
        cards = ttk.Frame(self)
        cards.grid(row=3, column=0, sticky='ew', pady=(10, 6))
        for index, (key, label) in enumerate((('total', 'Tổng công việc'), ('queued', 'Chờ xử lý'),
                ('working', 'Đang chạy / dừng'), ('completed', 'Hoàn tất'),
                ('review', 'Cần nghe duyệt'), ('issues', 'Lỗi / gián đoạn'))):
            cards.columnconfigure(index, weight=1, uniform='metric')
            card = ttk.LabelFrame(cards, text=label, padding=(8, 5))
            card.grid(row=0, column=index, sticky='nsew', padx=(0, 6 if index < 5 else 0))
            ttk.Label(card, textvariable=self.metrics[key], font=('Segoe UI', 20, 'bold')).pack()
        ttk.Label(self, text='Thống kê theo ngày gửi và nhân viên. Bộ lọc trạng thái chỉ áp dụng cho danh sách.'
                  ).grid(row=4, column=0, sticky='w', pady=(0, 6))
        panes = ttk.PanedWindow(self, orient='vertical')
        panes.grid(row=5, column=0, sticky='nsew')
        table = ttk.Frame(panes)
        panes.add(table, weight=3)
        self.tree = self.table(table, (
            ('created', 'Gửi lúc', 145), ('owner', 'Nhân viên', 130), ('title', 'Công việc', 215),
            ('state', 'Trạng thái', 130), ('progress', 'Tiến độ', 65), ('drive', 'Google Drive', 215)), height=10)
        self.tree.bind('<<TreeviewSelect>>', lambda _event: self.show_selection())
        for state, color in (('running', '#065ca8'), ('completed', '#237637'),
                              ('failed', '#b12727'), ('interrupted', '#b12727'), ('review_required', '#946300')):
            self.tree.tag_configure(state, foreground=color)
        ttk.Label(table, textvariable=self.summary).grid(row=2, column=0, sticky='w', pady=4)
        bottom = ttk.PanedWindow(panes, orient='horizontal')
        panes.add(bottom, weight=2)
        detail = ttk.LabelFrame(bottom, text='Chi tiết công việc đã chọn', padding=8)
        bottom.add(detail, weight=3)
        detail.columnconfigure(0, weight=1)
        detail.rowconfigure(0, weight=1)
        self.details = tk.Text(detail, height=10, width=48, wrap='word', state='disabled',
                               font=('Segoe UI', 10), relief='flat')
        self.details.grid(row=0, column=0, sticky='nsew')
        scrollbar = ttk.Scrollbar(detail, command=self.details.yview)
        scrollbar.grid(row=0, column=1, sticky='ns')
        self.details.configure(yscrollcommand=scrollbar.set)
        actions = ttk.Frame(detail)
        actions.grid(row=1, column=0, sticky='ew', pady=(6, 0))
        self.output_button = ttk.Button(actions, text='Mở thư mục thành phẩm', command=self.open_output, state='disabled')
        self.output_button.pack(side='left')
        self.log_button = ttk.Button(actions, text='Mở hồ sơ công việc', command=self.open_job, state='disabled')
        self.log_button.pack(side='left', padx=5)
        people = ttk.LabelFrame(bottom, text='Thống kê theo nhân viên', padding=8)
        bottom.add(people, weight=2)
        self.people_tree = self.table(people, (
            ('name', 'Nhân viên', 150), ('total', 'Tổng', 50), ('waiting', 'Chờ', 50),
            ('running', 'Chạy', 50), ('done', 'Xong', 50), ('review', 'Duyệt', 50),
            ('issues', 'Lỗi', 50), ('cancelled', 'Dừng', 50)), height=8)
        self.set_details('Chọn một công việc trong danh sách để xem tiến độ, cấu hình và kết quả.')
        ttk.Label(self, text='Tự cập nhật mỗi 3 giây · Chỉ theo dõi công việc gửi qua web · '
                  'Đã chép vào thư mục đồng bộ chưa xác nhận đã tải lên Drive online.', wraplength=1000
                  ).grid(row=6, column=0, sticky='ew', pady=(8, 0))

    @staticmethod
    def table(parent, columns, height):
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)
        tree = ttk.Treeview(parent, columns=tuple(key for key, _, _ in columns),
                            show='headings', selectmode='browse', height=height)
        for key, label, width in columns:
            tree.heading(key, text=label)
            tree.column(key, width=width, minwidth=width, stretch=key in ('title', 'name'))
        tree.grid(row=0, column=0, sticky='nsew')
        vertical = ttk.Scrollbar(parent, command=tree.yview)
        vertical.grid(row=0, column=1, sticky='ns')
        horizontal = ttk.Scrollbar(parent, orient='horizontal', command=tree.xview)
        horizontal.grid(row=1, column=0, sticky='ew')
        tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        return tree

    def filters_changed(self, _event=None):
        self.generation += 1
        self.refresh()

    def refresh(self):
        if self.destroyed:
            return
        self.due = 0
        if self.pending:
            return
        self.pending = True
        owner = self.users.get(self.owner.get())
        state = next((key for key, label in STATES.items() if label == self.state.get()), None)
        period, generation = self.period.get(), self.generation
        root, results = self.data_root, self.results

        def load():
            try:
                result = snapshot(root, owner=owner, period=period, state=state)
                results.put((generation, result, None))
            except Exception as exc:
                results.put((generation, None, str(exc)))

        # No Tk calls or queue mutations in the reading thread.
        threading.Thread(target=load, daemon=True, name='teo-web-monitor').start()

    def poll(self):
        self.timer = None
        if self.destroyed:
            return
        try:
            generation, result, error = self.results.get_nowait()
        except queue.Empty:
            pass
        else:
            self.pending = False
            if generation == self.generation:
                self.due = time.monotonic()+3
                if error:
                    previous = ' · Giữ số liệu lần '+date_text(self.last_update) if self.last_update else ''
                    self.status.set('Chưa cập nhật được hàng đợi'+previous+'. '+error[:200])
                else:
                    self.apply_snapshot(result)
        if not self.pending and time.monotonic() >= self.due:
            self.refresh()
        self.timer = self.after(150, self.poll)

    def apply_snapshot(self, data):
        self.last_update = data['refreshed']
        prefix = ('Web trên máy đang hoạt động' if data['online'] else
                  'Web trên máy chưa phản hồi · đang xem trạng thái đã lưu')
        if not data['exists']:
            prefix = 'Chưa có dữ liệu web. Mở “Tèo Web” để khởi động bản web đã cài.'
        self.status.set(prefix+' · Cập nhật '+date_text(self.last_update))
        self.users = {f'{user["name"]} ({user["username"]})': user['id'] for user in data['users']}
        values = ('Tất cả nhân viên', *self.users)
        self.owner_picker.configure(values=values)
        if self.owner.get() not in values:
            self.owner.set(values[0])
            self.generation += 1
            self.due = 0
        counts = data['counts']
        totals = dict(total=sum(counts.values()), queued=counts.get('queued', 0),
                      working=counts.get('running', 0)+counts.get('stopping', 0),
                      completed=counts.get('completed', 0), review=counts.get('review_required', 0),
                      issues=counts.get('failed', 0)+counts.get('interrupted', 0))
        for key, value in totals.items():
            self.metrics[key].set(str(value))
        selection = self.tree.selection()
        self.records = {job['id']: job for job in data['jobs']}
        for ident in self.tree.get_children():
            if ident not in self.records:
                self.tree.delete(ident)
        for index, job in enumerate(data['jobs']):
            values = (date_text(job['created']), job['owner_name'], job['title'],
                      job['label'], job['progress'], job['drive'])
            if self.tree.exists(job['id']):
                self.tree.item(job['id'], values=values, tags=(job['state'],))
                self.tree.move(job['id'], '', index)
            else:
                self.tree.insert('', index, iid=job['id'], values=values, tags=(job['state'],))
        if selection and selection[0] in self.records:
            self.tree.selection_set(selection[0])
        self.show_selection()
        shown, total = len(data['jobs']), data['visible_total']
        self.summary.set(f'Hiển thị {shown}/{total} công việc · Ưu tiên đang chạy, tiếp đến hàng đợi theo lượt gửi.'
                         +(' Lọc ngày hoặc nhân viên để xem lịch sử cũ hơn.' if shown < total else '')
                         +f' · Đã dừng trong thống kê: {counts.get("cancelled", 0)}')
        # Keep the employee table selection/scroll intact on unchanged refreshes.
        person_ids = {person['id'] for person in data['people']}
        for ident in self.people_tree.get_children():
            if ident not in person_ids:
                self.people_tree.delete(ident)
        for index, person in enumerate(data['people']):
            count = person['counts']
            values = (person['name'], sum(count.values()), count.get('queued', 0),
                      count.get('running', 0)+count.get('stopping', 0), count.get('completed', 0),
                      count.get('review_required', 0), count.get('failed', 0)+count.get('interrupted', 0),
                      count.get('cancelled', 0))
            if self.people_tree.exists(person['id']):
                self.people_tree.item(person['id'], values=values)
                self.people_tree.move(person['id'], '', index)
            else:
                self.people_tree.insert('', index, iid=person['id'], values=values)

    def selected(self):
        selection = self.tree.selection()
        return self.records.get(selection[0]) if selection else None

    def set_details(self, text):
        if self.details.get('1.0', 'end-1c') == text:
            return
        position = self.details.yview()[0]
        self.details.configure(state='normal')
        self.details.delete('1.0', 'end')
        self.details.insert('1.0', text)
        self.details.configure(state='disabled')
        self.details.yview_moveto(position)

    def show_selection(self):
        job = self.selected()
        self.output_button.configure(state='normal' if job and output_folder(self.data_root, job) else 'disabled')
        directory = job_directory(self.data_root, job['id']) if job else None
        self.log_button.configure(state='normal' if directory and directory.is_dir() else 'disabled')
        if not job:
            self.set_details('Chọn một công việc trong danh sách để xem tiến độ, cấu hình và kết quả.')
            return
        spec = job['spec']
        text = (f'{job["title"]}\nNhân viên: {job["owner_name"]} ({job["username"]})\n'
                f'Trạng thái: {job["label"]} · Tiến độ: {job["progress"]}\n'
                f'Gửi: {date_text(job["created"])} · Thay đổi: {date_text(job["updated"])}\n'
                f'Link nguồn: {job["input"]}\nGoogle Drive: {job["drive"]}\n'
                f'Thông báo: {job["message"] or "—"}\n')
        if job['last_event']:
            text += f'Bước ghi nhận cuối ({job["event_time"]}): {job["last_event"]}\n'
        style = {'conversation': 'Tự nhiên', 'story': 'Kể chuyện', 'advertising': 'Quảng cáo',
                 'legacy': 'Theo bản cũ'}.get(spec.get('reading_style'), '—')
        text += (f'Giọng: {spec.get("voice", "—")} · Cách đọc: {style}\n'
                 f'Tốc độ giọng: {spec.get("voice_speed", "—")}× · Hình: {spec.get("speed", "—")}×\n'
                 f'Dịch: {spec.get("translation_model", "—")} · Phụ đề trên video: '
                 f'{"Bật" if spec.get("burn_subtitles") else "Tắt"}\nMã công việc: {job["id"]}')
        self.set_details(text)

    def open_output(self):
        job = self.selected()
        folder = output_folder(self.data_root, job) if job else None
        self.open_folder(folder)

    def open_job(self):
        job = self.selected()
        self.open_folder(job_directory(self.data_root, job['id']) if job else None)

    def open_folder(self, folder):
        try:
            if folder is None or not folder.is_dir():
                raise OSError('Thư mục chưa có hoặc đã được di chuyển.')
            os.startfile(folder)
        except OSError as exc:
            self.status.set('Chưa mở được thư mục: '+str(exc))

    def copy_link(self):
        try:
            link = (self.data_root/'LINK_WEB.txt').read_text(encoding='utf-8').strip()
            if not link.startswith('https://') or any(char.isspace() for char in link):
                raise ValueError('Link online chưa được ghi nhận.')
            self.clipboard_clear()
            self.clipboard_append(link)
            self.status.set('Đã sao chép link web đã lưu. Trạng thái trên máy được cập nhật ở lượt làm mới tiếp theo.')
        except (OSError, ValueError) as exc:
            self.status.set('Chưa sao chép được link: '+str(exc))

    def configure_address(self):
        python = PROJECT/'.venv-web'/'Scripts'/'pythonw.exe'
        try:
            subprocess.Popen([str(python), '-X', 'utf8', '-B', '-m', 'teodub_web.address_setup',
                              '--root', str(self.data_root)], cwd=PROJECT,
                              creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except OSError:
            self.status.set('Chưa mở được cấu hình. Mở “Cai dia chi Teo Web.cmd” trong thư mục Tèo.')

    def on_destroy(self, event):
        if event.widget is self:
            self.destroyed = True
            if self.timer is not None:
                self.after_cancel(self.timer)
                self.timer = None
