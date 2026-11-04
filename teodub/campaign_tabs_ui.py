"""Chiến dịch hàng loạt as one sub-tab per link (owner 23/10/2026).

The first sub-tab, '＋ Thêm link', is the campaign workspace as before (read a list, create, run, stop all).
Every other sub-tab shows one campaign: one link, one name, its own Chạy / Dừng link này. The machine still makes
one video at a time, so running links take turns (one video each). Closing a sub-tab only hides it.
"""
import os
from pathlib import Path
import queue
import re
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

from teodub.campaign import read_json, requested_retries, totals
from teodub.campaign_tracking import campaign_notice, posting_label, row_tag, tracking_row
from teodub.common import atomic_json
from teodub.subtitle_style import SUBTITLE_BOXES

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
CHANNEL_WINDOW_S = 12*3600  # A channel window may stay open all day; the login helper's own 30 minutes is for signing in.
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
        # Mở kênh (owner 31/10/2026): at most one channel window of Tèo's own Chrome at a time.
        self.channel_tab = None
        self.channel_thread = None
        self.channel_cancel = threading.Event()
        self.channel_messages = queue.Queue()
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
        self.drain_channel()
        visible = self.visible_tab()
        for tab in list(self.links.values()):
            try:
                tab.refresh(full=tab is visible)
            except tk.TclError:
                continue
        self.timer = self.after(REFRESH_MS, self.poll)

    @property
    def channel_open(self):
        return self.channel_tab is not None

    def open_channel(self, tab, channel):
        """Open one link's channel page in Tèo's own Chrome (the profile "Đăng nhập Chrome" signs in). Look only: it reads
        nothing and changes no campaign. Runs on its own thread until the owner closes the Chrome window."""
        if self.channel_open:
            raise ValueError('Đang có một cửa sổ kênh mở trong Chrome của Tèo. Đóng cửa sổ Chrome đó trước rồi bấm lại.')
        workspace = self.campaign_tab
        if (getattr(self.studio, 'login_pending', False) or getattr(workspace, 'chrome_login_pending', False)
                or getattr(workspace, 'scanning', False)):
            raise ValueError('Đang đăng nhập hoặc đọc kênh bằng Chrome của Tèo. Đợi xong rồi bấm Mở kênh.')
        from teodub.channel_browser import profile_directory
        profile = profile_directory(self.data_dir/'douyin-channel-browser', 'chrome')
        self.channel_cancel.clear()
        self.channel_tab = tab
        shown = []

        def progress(_message):
            # The login helper reports twice: browser starting, then the page loaded. Its second text talks about signing in.
            shown.append(1)
            self.channel_messages.put(('opened' if len(shown) > 1 else 'starting', ''))

        def work():
            try:
                from teodub.channel_login import open_chrome_login
                reason = open_chrome_login(profile, text=channel, cancel=self.channel_cancel, progress=progress,
                                           timeout=CHANNEL_WINDOW_S)
                self.channel_messages.put(('done', reason))
            except Exception as exc:
                self.channel_messages.put(('error', str(exc) or type(exc).__name__))
        try:
            # Not a daemon: closing Tèo cancels it and it closes the browser and flushes the profile first.
            self.channel_thread = threading.Thread(target=work, daemon=False)
            self.channel_thread.start()
        except Exception:
            self.channel_tab = None
            raise

    def drain_channel(self):
        while True:
            try:
                kind, value = self.channel_messages.get_nowait()
            except queue.Empty:
                return
            tab = self.channel_tab
            if kind == 'starting':
                text = 'Đang mở kênh trong Chrome của Tèo…'
            elif kind == 'opened':
                text = 'Đã mở kênh trong Chrome của Tèo (phiên đăng nhập đã lưu). Xem xong thì đóng cửa sổ Chrome đó.'
            else:
                self.channel_tab = None
                if kind == 'error':
                    text = 'Chưa mở được kênh: '+value
                elif value == 'timeout':
                    text = 'Cửa sổ kênh đã mở quá lâu nên Tèo đóng lại; hồ sơ đăng nhập vẫn được giữ.'
                else:
                    text = 'Đã đóng cửa sổ kênh. Hồ sơ đăng nhập của Tèo được giữ nguyên.'
            if tab is not None:
                try:
                    tab.message.set(text)
                except tk.TclError:
                    pass

    def on_destroy(self, event):
        if event.widget is self:
            self.closed = True
            self.channel_cancel.set()
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
        self.more_window = None
        self.likes_window = None
        self.exclude_window = None
        self.header = tk.StringVar(self)
        self.source = tk.StringVar(self)
        self.status = tk.StringVar(self)
        self.notice = tk.StringVar(self)
        self.message = tk.StringVar(self)
        self.subtitle_note = tk.StringVar(self)
        self.subtitle_dirty = False
        self.subtitle_seen = None
        self.progress = tk.DoubleVar(self, value=0)
        self.build()

    def build(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(6, weight=1)
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
        self.more_button = ttk.Button(buttons, text='Lấy thêm video mới…', command=self.open_more, state='disabled')
        self.more_button.pack(side='left', padx=6)
        self.likes_button = ttk.Button(buttons, text='Xếp theo lượt thích…', command=self.open_likes, state='disabled')
        self.likes_button.pack(side='left')
        self.exclude_button = ttk.Button(buttons, text='Loại trừ video…', command=self.open_exclude)
        self.exclude_button.pack(side='left', padx=6)
        ttk.Button(buttons, text='Mở video gốc', command=lambda: self.open_folder('original_dir')).pack(side='left', padx=6)
        ttk.Button(buttons, text='Mở thành phẩm', command=lambda: self.open_folder('output_dir')).pack(side='left')
        ttk.Button(buttons, text='Đóng tab', command=self.close).pack(side='left', padx=(18, 0))
        self.channel_button = ttk.Button(buttons, text='Mở kênh', command=self.open_channel, state='disabled')
        self.channel_button.pack(side='left', padx=(6, 0))
        # Owner 04/11/2026: Sub Việt in the picture, per link. It reaches only videos not finished yet.
        subtitles = ttk.Frame(self)
        subtitles.grid(row=5, column=0, sticky='ew', pady=(0, 6))
        self.burn = tk.BooleanVar(self, value=False)
        self.box_label = tk.StringVar(self, value=SUBTITLE_BOXES['none']['label'])
        self.burn_check = ttk.Checkbutton(subtitles, text='Ghi Sub Việt vào hình', variable=self.burn,
                                          command=self.subtitle_edited)
        self.burn_check.pack(side='left')
        ttk.Label(subtitles, text='Khung che Sub gốc').pack(side='left', padx=(16, 6))
        self.box_picker = ttk.Combobox(subtitles, textvariable=self.box_label, state='readonly', width=22,
                                       values=[item['label'] for item in SUBTITLE_BOXES.values()])
        self.box_picker.pack(side='left')
        self.box_picker.bind('<<ComboboxSelected>>', self.subtitle_edited)
        self.subtitle_button = ttk.Button(subtitles, text='Áp dụng cho video chưa xong', command=self.apply_subtitles,
                                          state='disabled')
        self.subtitle_button.pack(side='left', padx=(12, 0))
        ttk.Label(subtitles, textvariable=self.subtitle_note, foreground='#164b78').pack(side='left', padx=(10, 0))
        table = ttk.Frame(self)
        table.grid(row=6, column=0, sticky='nsew')
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
        self.tree.tag_configure('excluded', foreground='#5f5f5f', background='#dedede')  # Loại trừ / Không đăng: the whole row greyed.
        ttk.Progressbar(self, variable=self.progress, maximum=100).grid(row=7, column=0, sticky='ew', pady=6)
        ttk.Label(self, textvariable=self.message, wraplength=1000).grid(row=8, column=0, sticky='w')
        ttk.Label(self, wraplength=1000, text=(
            'Mỗi tab là một link. "Dừng link này" chỉ dừng link này, các link khác vẫn chạy. Máy làm một video '
            'một lúc nên các link đang chạy làm xen kẽ, mỗi link một video. "Chạy" bỏ qua video lỗi; "Thử lại video '
            'lỗi" làm lại mỗi video lỗi một lần trong các lượt của link này, kể cả khi link khác đang chạy. '
            '"Lấy thêm video mới…" đọc lại kênh của link và chỉ thêm vào cuối link những video chưa có trong chiến dịch nào. '
            '"Mở kênh" mở link kênh trong Chrome của Tèo (đã đăng nhập Douyin) để bạn xem; đóng cửa sổ Chrome đó khi xong. '
            'Đóng tab không dừng link. Nhấp đôi một video để mở thành phẩm hoặc xem lỗi.')).grid(
                row=9, column=0, sticky='w', pady=(4, 0))

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
                +(f' (chờ thử lại {len(retrying)})' if retrying else '')+f' · Chờ {counts["waiting"]}'
                +(f' · Loại trừ {counts["excluded"]}' if counts['excluded'] else '')
                +(f' · Không đăng {counts["no_post"]}' if counts['no_post'] else ''))
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
        # Owner 30/10/2026: only a link made from a channel has one to read again.
        self.more_button.configure(state='normal' if channel and not busy else 'disabled')
        self.likes_button.configure(state='normal' if channel and not busy else 'disabled')
        # Owner 31/10/2026: opening the channel to look needs only a channel link; it never touches a running video.
        self.channel_button.configure(state='normal' if channel else 'disabled')
        self.read_subtitles(manifest)
        if time.monotonic()-self.slow_at >= SLOW_S or not self.posting:
            self.slow_at = time.monotonic()
            self.read_slow_columns(manifest)
        expected = {str(item['number']) for item in manifest['items']}
        for key in set(self.tree.get_children())-expected:
            self.tree.delete(key)
        for item in manifest['items']:
            queued = item['number'] in retrying
            row = tracking_row(item, active=active, retrying=queued)
            posting = posting_label(item, self.posting)
            drive = self.drive.get(item['number'], {}).get('label', 'Cần kiểm tra')
            values = row[:-1]+(posting, drive, row[-1])
            key, tags = str(item['number']), (row_tag(item, queued),)
            if self.tree.exists(key):
                self.tree.item(key, values=values, tags=tags)
            else:
                self.tree.insert('', 'end', iid=key, values=values, tags=tags)

    def read_subtitles(self, manifest):
        """Show what the next video will use; never overwrite a choice the owner is still making."""
        from teodub.campaign_settings import next_subtitle_settings
        try:
            current = next_subtitle_settings(self.directory, manifest)
        except (OSError, ValueError, TypeError, KeyError):
            current = None
        self.subtitle_seen = current
        if current is None:
            self.burn_check.configure(state='disabled')
            self.box_picker.configure(state='disabled')
            self.subtitle_button.configure(state='disabled')
            self.subtitle_note.set('Không còn video chưa xong.')
            return
        self.burn_check.configure(state='normal')
        self.box_picker.configure(state='readonly' if self.burn.get() else 'disabled')
        if not self.subtitle_dirty:
            self.burn.set(current['burn_subtitles'])
            self.box_label.set(SUBTITLE_BOXES[current['subtitle_box']]['label'])
            self.box_picker.configure(state='readonly' if current['burn_subtitles'] else 'disabled')
            self.subtitle_note.set('')
        self.subtitle_button.configure(state='normal' if self.subtitle_dirty else 'disabled')

    def subtitle_edited(self, _event=None):
        self.subtitle_dirty = True
        self.box_picker.configure(state='readonly' if self.burn.get() else 'disabled')
        self.subtitle_button.configure(state='normal')
        self.subtitle_note.set('Chưa áp dụng.')

    def apply_subtitles(self):
        from teodub.campaign_settings import save_next_subtitle_settings
        box = next((key for key, item in SUBTITLE_BOXES.items() if item['label'] == self.box_label.get()), 'none')
        burn = bool(self.burn.get())
        question = ('Ghi Sub Việt vào hình' + (' với '+SUBTITLE_BOXES[box]['label'].lower() if box != 'none' else ', không khung')
                    if burn else 'Không ghi Sub Việt vào hình (chỉ có Sub bật/tắt)')
        if not messagebox.askyesno('Sub Việt', question+'\n\nÁp dụng cho các video chưa xong của link này '
                                   '(kể cả video lỗi khi thử lại). Video đang làm và video đã xong giữ nguyên. Áp dụng?',
                                   parent=self):
            return
        try:
            save_next_subtitle_settings(self.studio.data_dir, self.directory, burn, box)
        except (OSError, ValueError) as exc:
            self.message.set('Chưa áp dụng Sub: '+str(exc))
            return
        self.subtitle_dirty = False
        self.message.set('Đã lưu. Sub mới áp dụng cho các video chưa xong; video đang làm làm xong theo cấu hình cũ.')
        self.refresh(full=True)

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

    def open_more(self):
        """Lấy thêm video mới (owner 30/10/2026): read this link's channel again, add only the videos no campaign has."""
        if self.more_window is not None and self.more_window.winfo_exists():
            self.more_window.lift()
            return
        workspace = getattr(self.area, 'campaign_tab', None)
        if self.studio.process is not None or getattr(self.studio, 'login_pending', False) or getattr(workspace, 'scanning', False):
            self.message.set('Đang có video đơn, cửa sổ đăng nhập hoặc một lượt đọc kênh khác. Đợi xong rồi bấm Lấy thêm video mới.')
            return
        try:
            from teodub.campaign_append_ui import AppendVideosDialog
            self.more_window = AppendVideosDialog(self)
        except (OSError, ValueError, RuntimeError) as exc:
            self.message.set(str(exc))

    def open_likes(self):
        """Xếp theo lượt thích (owner 03/11/2026): read this link's channel again, set the picking order by Douyin's likes."""
        if self.likes_window is not None and self.likes_window.winfo_exists():
            self.likes_window.lift()
            return
        workspace = getattr(self.area, 'campaign_tab', None)
        if self.studio.process is not None or getattr(self.studio, 'login_pending', False) or getattr(workspace, 'scanning', False):
            self.message.set('Đang có video đơn, cửa sổ đăng nhập hoặc một lượt đọc kênh khác. Đợi xong rồi bấm Xếp theo lượt thích.')
            return
        try:
            from teodub.campaign_likes_ui import LikesOrderDialog
            self.likes_window = LikesOrderDialog(self)
        except (OSError, ValueError, RuntimeError) as exc:
            self.message.set(str(exc))

    def open_exclude(self):
        """Loại trừ video (owner 04/11/2026): paste links, mark those videos of this link so they are never dubbed."""
        if self.exclude_window is not None and self.exclude_window.winfo_exists():
            self.exclude_window.lift()
            return
        try:
            from teodub.campaign_exclude_ui import ExcludeVideosDialog
            self.exclude_window = ExcludeVideosDialog(self)
        except (OSError, ValueError, RuntimeError) as exc:
            self.message.set(str(exc))

    def open_channel(self):
        """Mở kênh (owner 31/10/2026): open this link's channel in Tèo's Chrome, signed in as "Đăng nhập Chrome" left it."""
        try:
            discovery = (self.manifest or read_json(self.directory/'campaign.json')).get('discovery') or {}
            channel = discovery.get('channel') if isinstance(discovery, dict) else None
            if not channel:
                raise ValueError('Link này không có link kênh (tạo từ danh sách video dán vào).')
            self.area.open_channel(self, channel)
        except (OSError, ValueError, RuntimeError) as exc:
            self.message.set(str(exc))
            return
        self.message.set('Đang mở kênh trong Chrome của Tèo…')

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
        if self.exclude_window is not None and self.exclude_window.winfo_exists():
            self.exclude_window.close()
        if self.likes_window is not None and self.likes_window.winfo_exists():
            self.likes_window.close()
        if self.more_window is not None and self.more_window.winfo_exists():
            self.more_window.close()
        self.area.close_link(self)
