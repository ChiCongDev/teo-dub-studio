"""Chiến dịch hàng loạt as one sub-tab per link (owner 23/10/2026).

The first sub-tab, '＋ Thêm link', is the campaign workspace as before (read a list, create, run, stop all).
Every other sub-tab shows one campaign: one link, one name, its own Chạy / Dừng link này. The machine still makes
one video at a time, so running links take turns (one video each). Closing a sub-tab only hides it.
"""
import os
from pathlib import Path
import re
import time
import tkinter as tk
from tkinter import messagebox, ttk

from teodub.campaign import read_json, requested_retries, totals
from teodub.campaign_tracking import campaign_notice, tracking_row
from teodub.common import atomic_json

TABS_FILE = 'campaign-tabs.json'
REFRESH_MS = 2000
SLOW_S = 15  # Posting and Google Drive columns of the tab on screen.
# State -> (word in the tab title, status line).
STATES = {
    'running': ('đang làm', 'Đang làm'),
    'waiting': ('chờ lượt', 'Chạy xen kẽ · chờ tới lượt'),
    'handoff': ('chờ chuyển', 'Chờ chạy xen kẽ (sau video link khác đang làm)'),
    'stopped': ('đã dừng', 'Đã dừng link này'),
    'paused': ('tạm dừng', 'Đang tạm dừng · bấm Chạy để chạy tiếp'),
    'done': ('xong', 'Hoàn tất'),
    'attention': ('còn lỗi', 'Còn video lỗi · bấm Thử lại video lỗi khi đã xử lý'),
    'retrying': ('chờ thử lại', 'Chờ thử lại video lỗi · tới lượt link này là làm'),
    'ready': ('sẵn sàng', 'Sẵn sàng · bấm Chạy'),
    'interrupted': ('gián đoạn', 'Bị gián đoạn · bấm Chạy để chạy tiếp'),
}
RUNNABLE = {'ready', 'paused', 'stopped', 'interrupted'}
STOPPABLE = {'running', 'waiting', 'handoff'}


class CampaignArea(ttk.Frame):
    """The 'Chiến dịch hàng loạt' tab: the campaign workspace plus one sub-tab per opened link."""

    def __init__(self, parent, studio):
        super().__init__(parent)
        self.studio = studio
        self.data_dir = Path(studio.data_dir)
        self.tabs = ttk.Notebook(self)
        self.tabs.pack(fill='both', expand=True)
        self.links = {}
        self.campaign_tab = None
        self.timer = None
        self.closed = False
        self.bind('<Destroy>', self.on_destroy, add='+')

    def attach(self, campaign_tab):
        """Put the existing workspace first, then reopen the links that were open last time (never runs them)."""
        self.campaign_tab = campaign_tab
        self.tabs.add(campaign_tab, text='＋ Thêm link')
        for ident in self.saved_ids():
            directory = self.data_dir/'campaigns'/ident
            try:
                if not read_json(directory/'campaign.json').get('archived_at'):
                    self.open_link(directory, select=False, save=False)
            except (OSError, ValueError, tk.TclError):
                continue
        self.timer = self.after(REFRESH_MS, self.poll)

    def saved_ids(self):
        try:
            data = read_json(self.data_dir/TABS_FILE)
        except (OSError, ValueError):
            return []
        ids = data.get('campaigns', []) if isinstance(data, dict) else []
        return [ident for ident in ids if isinstance(ident, str) and re.fullmatch(r'[0-9a-f]{32}', ident)]

    def save(self):
        try:
            atomic_json(self.data_dir/TABS_FILE, dict(version=1, campaigns=[Path(key).name for key in self.links]))
        except OSError:
            pass  # Only which tabs were open is lost.

    def open_link(self, directory, *, select=True, save=True, message=''):
        key = str(Path(directory).resolve())
        tab = self.links.get(key)
        if tab is None:
            tab = CampaignLinkTab(self.tabs, self, Path(key))
            self.links[key] = tab
            self.tabs.add(tab, text=tab.title())
            if save:
                self.save()
        if message:
            tab.message.set(message)
        if select:
            self.tabs.select(tab)
            self.show()
        tab.refresh(full=True)
        return tab

    def close_link(self, tab):
        self.links.pop(str(tab.directory), None)
        try:
            self.tabs.forget(tab)
        except tk.TclError:
            pass
        tab.destroy()
        self.save()

    def show(self):
        try:
            self.master.select(self)
        except (tk.TclError, AttributeError):
            pass

    def show_workspace(self):
        if self.campaign_tab is not None:
            self.tabs.select(self.campaign_tab)
        self.show()

    def visible_tab(self):
        try:
            if self.master.select() != str(self):
                return None
        except (tk.TclError, AttributeError):
            pass
        selected = self.tabs.select()
        return next((tab for tab in self.links.values() if str(tab) == selected), None)

    def poll(self):
        self.timer = None
        if self.closed or getattr(self.studio, 'closed', False):
            return
        visible = self.visible_tab()
        for tab in list(self.links.values()):
            try:
                tab.refresh(full=tab is visible)
            except tk.TclError:
                continue
        self.timer = self.after(REFRESH_MS, self.poll)

    def on_destroy(self, event):
        if event.widget is self:
            self.closed = True
            if self.timer is not None:
                try:
                    self.after_cancel(self.timer)
                except tk.TclError:
                    pass
                self.timer = None


class CampaignLinkTab(ttk.Frame):
    """One link (one campaign, one name): its progress, its videos and its own buttons."""

    def __init__(self, parent, area, directory):
        super().__init__(parent, padding=12)
        self.area, self.directory, self.studio = area, Path(directory), area.studio
        self.manifest = None
        self.state = 'ready'
        self.slow_at = 0.
        self.posting, self.drive = {}, {}
        self.header = tk.StringVar(self)
        self.source = tk.StringVar(self)
        self.status = tk.StringVar(self)
        self.notice = tk.StringVar(self)
        self.message = tk.StringVar(self)
        self.progress = tk.DoubleVar(self, value=0)
        self.build()

    def build(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(5, weight=1)
        ttk.Label(self, textvariable=self.header, font=('Segoe UI', 16, 'bold')).grid(row=0, column=0, sticky='w')
        ttk.Label(self, textvariable=self.source, wraplength=1000).grid(row=1, column=0, sticky='w', pady=(2, 4))
        ttk.Label(self, textvariable=self.status, wraplength=1000, foreground='#164b78').grid(row=2, column=0, sticky='w')
        ttk.Label(self, textvariable=self.notice, wraplength=1000, foreground='#b12727',
                  justify='left').grid(row=3, column=0, sticky='w', pady=(2, 4))
        buttons = ttk.Frame(self)
        buttons.grid(row=4, column=0, sticky='ew', pady=6)
        self.run_button = ttk.Button(buttons, text='Chạy', command=self.run)
        self.run_button.pack(side='left')
        self.stop_button = ttk.Button(buttons, text='Dừng link này', command=self.stop)
        self.stop_button.pack(side='left', padx=6)
        self.retry_button = ttk.Button(buttons, text='Thử lại video lỗi', command=self.retry)
        self.retry_button.pack(side='left')
        ttk.Button(buttons, text='Mở video gốc', command=lambda: self.open_folder('original_dir')).pack(side='left', padx=6)
        ttk.Button(buttons, text='Mở thành phẩm', command=lambda: self.open_folder('output_dir')).pack(side='left')
        ttk.Button(buttons, text='Đóng tab', command=self.close).pack(side='left', padx=(18, 0))
        table = ttk.Frame(self)
        table.grid(row=5, column=0, sticky='nsew')
        table.columnconfigure(0, weight=1)
        table.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(table, columns=('name', 'title', 'url', 'state', 'percent', 'completed_at', 'posting',
                                                 'drive', 'message'), show='headings', height=12)
        for key, label, width in (('name', 'Video', 70), ('title', 'Tiêu đề', 190), ('url', 'Link video', 240),
                                  ('state', 'Trạng thái', 140), ('percent', 'Tiến độ', 70),
                                  ('completed_at', 'Thời gian hoàn thành', 168),
                                  ('posting', 'Trạng thái đăng bài', 185), ('drive', 'Google Drive', 170),
                                  ('message', 'Chi tiết / lỗi', 280)):
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, minwidth=60, stretch=key != 'name')
        self.tree.grid(row=0, column=0, sticky='nsew')
        vertical = ttk.Scrollbar(table, orient='vertical', command=self.tree.yview)
        vertical.grid(row=0, column=1, sticky='ns')
        horizontal = ttk.Scrollbar(table, orient='horizontal', command=self.tree.xview)
        horizontal.grid(row=1, column=0, sticky='ew')
        self.tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.tree.bind('<Double-1>', self.open_item)
        for tag, color in (('completed', '#237637'), ('review_required', '#8a6000'), ('retrying', '#8a6000'),
                           ('failed', '#b12727'), ('running', '#065ca8'), ('cancelled', '#777777')):
            self.tree.tag_configure(tag, foreground=color)
        ttk.Progressbar(self, variable=self.progress, maximum=100).grid(row=6, column=0, sticky='ew', pady=6)
        ttk.Label(self, textvariable=self.message, wraplength=1000).grid(row=7, column=0, sticky='w')
        ttk.Label(self, wraplength=1000, text=(
            'Mỗi tab là một link. "Dừng link này" chỉ dừng link này, các link khác vẫn chạy. Máy làm một video '
            'một lúc nên các link đang chạy làm xen kẽ, mỗi link một video. "Chạy" bỏ qua video lỗi; "Thử lại video '
            'lỗi" làm lại mỗi video lỗi một lần trong các lượt của link này, kể cả khi link khác đang chạy. '
            'Đóng tab không dừng link. Nhấp đôi một video để mở thành phẩm hoặc xem lỗi.')).grid(
                row=8, column=0, sticky='w', pady=(4, 0))

    def title(self, manifest=None, counts=None, word=''):
        manifest = manifest or self.manifest
        name = str((manifest or {}).get('name') or self.directory.name[:8])
        name = name if len(name) <= 28 else name[:27]+'…'
        if not counts:
            return name
        return f'{name} · {counts["completed"]}/{counts["total"]}'+(f' · {word}' if word else '')

    def refresh(self, full=False):
        try:
            manifest = read_json(self.directory/'campaign.json')
        except (OSError, ValueError) as exc:
            self.status.set('Chưa đọc được chiến dịch: '+str(exc))
            return
        if manifest.get('archived_at'):
            self.area.close_link(self)  # Deleted in Danh sách chiến dịch: nothing left to show.
            return
        self.manifest = manifest
        batch = getattr(self.studio, 'batch_controller', None)
        try:
            self.state = batch.link_state(self.directory, manifest) if batch is not None else 'ready'
        except (OSError, ValueError, TypeError, KeyError):
            self.state = 'ready'
        counts = totals(manifest)
        failed = {item['number'] for item in manifest['items'] if item['status'] == 'failed'}
        # Asked for one more try: marked by the worker, or still a request it has not taken.
        retrying = failed & ({item['number'] for item in manifest['items'] if item.get('retry_once') is True}
                             | requested_retries(self.directory))
        if self.state == 'attention' and failed and retrying == failed:
            self.state = 'retrying'  # Thử lại video lỗi was pressed; its turn has not come yet.
        word, line = STATES.get(self.state, STATES['ready'])
        try:
            self.area.tabs.tab(self, text=self.title(manifest, counts, word))
        except tk.TclError:
            pass
        if not full:
            return
        active = self.state == 'running'
        self.header.set(manifest['name'])
        discovery = manifest.get('discovery') or {}
        channel = discovery.get('channel') if isinstance(discovery, dict) else None
        self.source.set(('Link kênh: '+channel) if channel else 'Danh sách link video')
        current = next((item for item in manifest['items'] if item.get('status') == 'running'), None)
        text = (f'{line} · Xong {counts["completed"]}/{counts["total"]} · Lỗi {counts["failed"]}'
                +(f' (chờ thử lại {len(retrying)})' if retrying else '')+f' · Chờ {counts["waiting"]}')
        if current is not None and active:
            row = tracking_row(current, active=True)
            text += f' · {row[0]}: {row[3]}'+(f' {row[4]}' if row[4] != '—' else '')
        self.status.set(text+'.')
        notice = campaign_notice(manifest, active=active)
        if failed and retrying == failed and manifest.get('status') != 'paused':
            notice = ''  # Every failed video is asked for again: the status line says so.
        if self.state in ('waiting', 'handoff', 'stopped'):
            notice = ''  # Waiting for its turn or stopped on purpose: not a problem to report.
        self.notice.set(notice)
        self.progress.set(counts['completed']*100/max(1, counts['total']))
        busy = self.studio.process is not None or getattr(self.studio, 'login_pending', False)
        self.run_button.configure(state='normal' if self.state in RUNNABLE and not busy else 'disabled')
        self.stop_button.configure(state='normal' if self.state in STOPPABLE else 'disabled')
        self.retry_button.configure(state='normal' if failed-retrying and not busy else 'disabled')
        if time.monotonic()-self.slow_at >= SLOW_S or not self.posting:
            self.slow_at = time.monotonic()
            self.read_slow_columns(manifest)
        expected = {str(item['number']) for item in manifest['items']}
        for key in set(self.tree.get_children())-expected:
            self.tree.delete(key)
        for item in manifest['items']:
            queued = item['number'] in retrying
            row = tracking_row(item, active=active, retrying=queued)
            posting = self.posting.get(item['number'], {}).get('label', 'Cần kiểm tra')
            drive = self.drive.get(item['number'], {}).get('label', 'Cần kiểm tra')
            values = row[:-1]+(posting, drive, row[-1])
            key, tags = str(item['number']), ('retrying',) if queued else (item['status'],)
            if self.tree.exists(key):
                self.tree.item(key, values=values, tags=tags)
            else:
                self.tree.insert('', 'end', iid=key, values=values, tags=tags)

    def read_slow_columns(self, manifest):
        from teodub.campaign_posting import campaign_posting_statuses
        from teodub.campaign_settings import effective_job
        from teodub.drive_delivery import drive_upload_summary
        try:
            self.posting = campaign_posting_statuses(self.studio.data_dir, self.directory, manifest)
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            self.posting = {item['number']: dict(label='Cần kiểm tra', details=str(exc)) for item in manifest['items']}
        self.drive = {}
        for item in manifest['items']:
            try:
                job = effective_job(self.directory, item)
                self.drive[item['number']] = drive_upload_summary(read_json(job/'config.json'), job)
            except (OSError, ValueError, TypeError, KeyError):
                self.drive[item['number']] = dict(label='Cần kiểm tra')

    def run(self):
        batch = getattr(self.studio, 'batch_controller', None)
        if batch is None:
            return
        try:
            outcome = batch.run_link(self.directory)
        except (OSError, ValueError) as exc:
            self.message.set('Chưa chạy: '+str(exc))
            return
        self.message.set({
            'already': 'Link này đang chạy.',
            'joined': 'Đã cho link này vào lượt; tới lượt là làm (mỗi link một video).',
            'handoff': 'Link đang chạy làm xong video hiện tại thì tới lượt link này.',
            'started': 'Đã bắt đầu chạy link này.'}.get(outcome, ''))
        self.studio.refresh_buttons()
        self.refresh(full=True)

    def stop(self):
        batch = getattr(self.studio, 'batch_controller', None)
        if batch is None:
            return
        try:
            outcome = batch.stop_link(self.directory)
        except (OSError, ValueError) as exc:
            self.message.set('Chưa dừng được: '+str(exc))
            return
        self.message.set('Đang dừng link này: video đang làm dừng lại và giữ các bước đã xong; các link khác vẫn chạy.'
                         if outcome else 'Link này đang không chạy.')
        self.refresh(full=True)

    def retry(self):
        batch = getattr(self.studio, 'batch_controller', None)
        if batch is None:
            return
        try:
            count, outcome = batch.retry_link(self.directory)
        except (OSError, ValueError) as exc:
            self.message.set('Chưa chạy: '+str(exc))
            self.refresh(full=True)
            return
        if not count:
            self.message.set('Link này không còn video lỗi.')
        else:
            self.message.set(f'Đã xếp {count} video lỗi để thử lại, mỗi video một lần, trong các lượt của link này. '+{
                'already': 'Link đang chạy một mình nên Tèo làm lại chúng ở lần chạy sau của link này.',
                'joined': 'Link chạy xen kẽ với các link khác; tới lượt là làm.',
                'handoff': 'Link đang chạy làm xong video hiện tại thì tới lượt link này.',
                'started': 'Đã bắt đầu chạy link này.'}.get(outcome, ''))
        self.studio.refresh_buttons()
        self.refresh(full=True)

    def open_folder(self, key):
        try:
            path = Path((self.manifest or read_json(self.directory/'campaign.json'))[key])
            if not path.is_dir():
                raise ValueError('Thư mục chưa có video.')
            os.startfile(path)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.message.set('Chưa mở được thư mục: '+str(exc))

    def open_item(self, _event=None):
        selected = self.tree.selection()
        if not selected or not self.manifest:
            return
        item = next((item for item in self.manifest['items'] if str(item['number']) == selected[0]), None)
        if item is None:
            return
        video = (item.get('outputs') or {}).get('video')
        if item.get('status') in ('completed', 'review_required') and video and Path(video).is_file():
            try:
                os.startfile(video)
                return
            except OSError as exc:
                self.message.set('Chưa mở được video: '+str(exc))
                return
        messagebox.showinfo(f'Video {item["number"]}', str(item.get('message') or 'Chưa có thông tin.'), parent=self)

    def close(self):
        self.area.close_link(self)
