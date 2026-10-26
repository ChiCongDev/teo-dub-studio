"""Saved campaigns and an explicitly started campaign queue: one campaign after another, or in turns."""
import os
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox

from teodub.campaign import is_running, read_json, totals
from teodub.campaign_tracking import campaign_notice


STATE_LABELS = {
    'ready': 'Sẵn sàng', 'completed': 'Hoàn tất', 'needs_attention': 'Còn video lỗi',
    'paused': 'Đã dừng', 'running': 'Bị gián đoạn · có thể tiếp tục',
    'pending': 'Chờ trong hàng đợi', 'queued': 'Chờ trong hàng đợi',
    'failed': 'Cần kiểm tra', 'cancelled': 'Đã dừng', 'waiting': 'Chờ trong hàng đợi',
    'review_required': 'Hoàn tất · cần nghe duyệt', 'skipped': 'Đã bỏ qua',
}
# How "Chạy các chiến dịch đã chọn" runs them; the second is the rotation (owner 23/10/2026).
ORDERS = ('Lần lượt từng chiến dịch', 'Xen kẽ từng video')


class CampaignLibraryTab(ttk.Frame):
    """Browsing is passive; management and execution use explicit buttons."""

    def __init__(self, parent, studio, campaign_tab, batch_controller):
        super().__init__(parent, padding=16)
        self.studio = studio
        self.campaign_tab = campaign_tab
        self.batch_controller = batch_controller
        self.records = {}
        self.timer = None
        self.destroyed = False
        self.search = tk.StringVar(self)
        self.show_deleted = tk.BooleanVar(self, value=False)
        self.status = tk.StringVar(self, value='Chiến dịch được lưu tự động khi bạn bấm Tạo chiến dịch mới.')
        self.library_summary = tk.StringVar(self)
        self.queue_status = tk.StringVar(self)
        self.details = tk.StringVar(self, value='Chọn một chiến dịch để xem thư mục và cấu hình đã lưu.')
        self.selection_summary = tk.StringVar(self)
        self.order = tk.StringVar(self, value=ORDERS[0])
        self.build()
        self.search.trace_add('write', lambda *_: self.refresh())
        self.bind('<Destroy>', self.on_destroy, add='+')
        self.refresh()
        self.timer = self.after(2000, self.poll)

    def build(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)
        ttk.Label(self, text='Danh sách chiến dịch', font=('Segoe UI', 21, 'bold')).grid(
            row=0, column=0, sticky='w')
        ttk.Label(self, text='Lưu một lần, mở lại bất cứ lúc nào. Chọn nhiều chiến dịch để chạy lần lượt từng chiến dịch '
                  'hoặc xen kẽ từng video; giữ kết quả khi dừng và tiếp tục.', wraplength=1000).grid(
                      row=1, column=0, sticky='w', pady=(4, 10))
        search_row = ttk.Frame(self)
        search_row.grid(row=2, column=0, sticky='ew', pady=(0, 8))
        ttk.Label(search_row, text='Tìm chiến dịch').pack(side='left', padx=(0, 8))
        ttk.Entry(search_row, textvariable=self.search, width=35).pack(side='left', fill='x', expand=True)
        ttk.Button(search_row, text='Làm mới', command=self.refresh).pack(side='left', padx=6)
        ttk.Button(search_row, text='Chọn tất cả', command=self.select_all).pack(side='left')
        ttk.Checkbutton(search_row, text='Hiện đã xóa', variable=self.show_deleted,
                        command=self.refresh).pack(side='left', padx=8)
        table = ttk.Frame(self)
        table.grid(row=3, column=0, sticky='nsew')
        table.columnconfigure(0, weight=1)
        table.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(table, columns=('name', 'created', 'state', 'progress', 'errors', 'channel'),
                                 show='headings', selectmode='extended', height=11)
        for key, label, width in (('name', 'Chiến dịch', 220), ('created', 'Ngày tạo', 140),
                                  ('state', 'Trạng thái', 210), ('progress', 'Video hoàn tất', 105),
                                  ('errors', 'Video lỗi', 75), ('channel', 'Nguồn / kênh', 250)):
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, minwidth=60, stretch=key in ('name', 'state', 'channel'))
        self.tree.grid(row=0, column=0, sticky='nsew')
        vertical = ttk.Scrollbar(table, orient='vertical', command=self.tree.yview)
        vertical.grid(row=0, column=1, sticky='ns')
        horizontal = ttk.Scrollbar(table, orient='horizontal', command=self.tree.xview)
        horizontal.grid(row=1, column=0, sticky='ew')
        self.tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.tree.bind('<<TreeviewSelect>>', lambda _event: self.show_selection())
        self.tree.bind('<Double-1>', self.open_selected)
        self.tree.tag_configure('running', foreground='#065ca8')
        self.tree.tag_configure('completed', foreground='#237637')
        self.tree.tag_configure('needs_attention', foreground='#b12727')
        ttk.Label(self, textvariable=self.selection_summary).grid(row=4, column=0, sticky='w', pady=5)
        actions = ttk.Frame(self)
        actions.grid(row=5, column=0, sticky='ew')
        self.open_button = ttk.Button(actions, text='Mở chiến dịch', command=self.open_selected)
        self.open_button.pack(side='left')
        self.run_button = ttk.Button(actions, text='Chạy các chiến dịch đã chọn', command=self.run_selected)
        self.run_button.pack(side='left', padx=6)
        ttk.Combobox(actions, textvariable=self.order, values=ORDERS, state='readonly', width=24).pack(side='left', padx=(0, 6))
        self.resume_button = ttk.Button(actions, text='Tiếp tục hàng đợi', command=self.resume)
        self.resume_button.pack(side='left')
        self.stop_button = ttk.Button(actions, text='Dừng hàng đợi', command=self.stop)
        self.stop_button.pack(side='left', padx=6)
        folders = ttk.Frame(self)
        folders.grid(row=6, column=0, sticky='ew', pady=7)
        self.originals_button = ttk.Button(folders, text='Mở video gốc', command=lambda: self.open_folder('original_dir'))
        self.originals_button.pack(side='left')
        self.outputs_button = ttk.Button(folders, text='Mở thành phẩm', command=lambda: self.open_folder('output_dir'))
        self.outputs_button.pack(side='left', padx=6)
        self.edit_button = ttk.Button(folders, text='Sửa chiến dịch', command=self.edit_selected)
        self.edit_button.pack(side='left', padx=6)
        self.delete_button = ttk.Button(folders, text='Xóa chiến dịch', command=self.delete_selected)
        self.delete_button.pack(side='left')
        self.restore_button = ttk.Button(folders, text='Khôi phục', command=self.restore_selected)
        self.restore_button.pack(side='left', padx=6)
        ttk.Label(self, textvariable=self.details, wraplength=1000, justify='left').grid(row=7, column=0, sticky='w', pady=4)
        ttk.Label(self, textvariable=self.queue_status, wraplength=1000, foreground='#065ca8').grid(
            row=8, column=0, sticky='w', pady=4)
        ttk.Label(self, textvariable=self.library_summary, wraplength=1000).grid(row=9, column=0, sticky='w', pady=4)
        ttk.Label(self, textvariable=self.status, wraplength=1000).grid(row=10, column=0, sticky='w', pady=4)
        ttk.Label(self, text='Chọn nhiều dòng bằng Ctrl / Shift. Tab này tự cập nhật tiến độ mỗi 2 giây. '
                  'Mở ứng dụng hoặc xem danh sách không tự chạy hàng đợi.', wraplength=1000).grid(row=11, column=0, sticky='w')

    def snapshot(self):
        try:
            value = self.batch_controller.snapshot()
            if (not isinstance(value, dict) or not isinstance(value.get('campaigns', []), list)
                    or not isinstance(value.get('status'), str)):
                return None
            campaigns = [item for item in value.get('campaigns', [])
                         if isinstance(item, dict) and isinstance(item.get('directory'), str)
                         and item['directory'] and isinstance(item.get('status'), str)]
            active = value.get('active_directory')
            return dict(value, campaigns=campaigns, active_directory=active if isinstance(active, str) else None)
        except (OSError, ValueError):
            return None

    def refresh(self):
        if self.destroyed or self.studio.closed:
            return
        selected = set(self.tree.selection())
        focus = self.tree.focus()
        records = {}
        skipped = 0
        for path in (Path(self.studio.data_dir)/'campaigns').glob('*/campaign.json'):
            try:
                manifest = read_json(path)
                # A damaged manifest must not hide the rest of the library.
                if (not isinstance(manifest, dict) or not isinstance(manifest.get('name'), str)
                        or not isinstance(manifest.get('created_at'), str)
                        or not isinstance(manifest.get('status', 'ready'), str)
                        or not isinstance(manifest.get('settings') or {}, dict)
                        or not isinstance(manifest.get('discovery') or {}, dict)
                        or not isinstance(manifest.get('items'), list)
                        or not all(isinstance(item, dict) and isinstance(item.get('status'), str)
                                   for item in manifest['items'])):
                    raise ValueError('Chiến dịch không hợp lệ')
                discovery = manifest.get('discovery') or {}
                if ((discovery.get('channel') is not None and not isinstance(discovery['channel'], str))
                        or ('message' in discovery and not isinstance(discovery['message'], str))):
                    raise ValueError('Thông tin kênh không hợp lệ')
                totals(manifest)
                records[str(path.parent.resolve())] = manifest
            except (OSError, ValueError, KeyError, TypeError):
                skipped += 1
        self.records = records
        needle = self.search.get().strip().casefold()
        snapshot = self.snapshot()
        queued = {str(Path(item['directory']).resolve()): item for item in (snapshot or {}).get('campaigns', [])
                  if isinstance(item, dict) and item.get('directory')}
        visible = []
        for directory, manifest in sorted(records.items(), key=lambda pair: pair[1]['created_at'], reverse=True):
            if manifest.get('archived_at') and not self.show_deleted.get():
                continue
            discovery = manifest.get('discovery') or {}
            channel = str(discovery.get('channel') or '') if isinstance(discovery, dict) else ''
            channel = channel or 'Danh sách link video'
            if needle and needle not in (manifest['name']+' '+manifest['created_at']+' '+channel).casefold():
                continue
            counts = totals(manifest)
            active = is_running(directory)
            state = manifest.get('status', 'ready')
            label = 'Đang chạy' if active else STATE_LABELS.get(state, state)
            if manifest.get('archived_at'):
                label = 'Đã xóa · có thể khôi phục'
            queue_item = queued.get(directory)
            if not active and snapshot and snapshot.get('status') == 'running' and queue_item:
                queue_state = queue_item.get('status', '')
                if queue_state in ('pending', 'queued', 'waiting'):
                    label = 'Chạy xen kẽ · chờ tới lượt' if snapshot.get('order') == 'rotate' else 'Chờ trong hàng đợi'
                elif queue_state == 'running':
                    label = 'Đang khởi động'
            if isinstance(discovery, dict) and discovery and not discovery.get('complete', False):
                channel += ' · chưa đủ kênh'
            values = (manifest['name'], manifest['created_at'][:16].replace('T', ' '), label,
                      f'{counts["completed"]}/{counts["total"]}', counts['failed'], channel)
            tag = 'running' if active else state
            if self.tree.exists(directory):
                self.tree.item(directory, values=values, tags=(tag,))
            else:
                self.tree.insert('', 'end', iid=directory, values=values, tags=(tag,))
            visible.append(directory)
        for key in set(self.tree.get_children())-set(visible):
            self.tree.delete(key)
        for index, key in enumerate(visible):
            self.tree.move(key, '', index)
        remaining = [key for key in visible if key in selected]
        if remaining:
            self.tree.selection_set(remaining)
        if focus in visible:
            self.tree.focus(focus)
        self.library_summary.set(f'Đã lưu {len(records)} chiến dịch · đang hiển thị {len(visible)}.'
                                 +(f' Bỏ qua {skipped} tệp chiến dịch hỏng; giữ nguyên các tệp đó.' if skipped else ''))
        self.show_queue(snapshot)
        self.show_selection()

    def show_queue(self, snapshot):
        if not snapshot:
            self.queue_status.set('Chưa có hàng đợi. Chọn chiến dịch rồi bấm Chạy các chiến dịch đã chọn.')
            return
        campaigns = snapshot.get('campaigns', [])
        finished = sum(item.get('status') == 'completed' for item in campaigns)
        attention = sum(item.get('status') == 'needs_attention' for item in campaigns)
        label = {'running': 'Đang chạy', 'completed': 'Hoàn tất', 'paused': 'Đã dừng',
                 'failed': 'Cần kiểm tra', 'ready': 'Sẵn sàng',
                 'needs_attention': 'Còn chiến dịch lỗi'}.get(snapshot.get('status'), str(snapshot.get('status', '')))
        kind = 'Vòng xen kẽ' if snapshot.get('order') == 'rotate' else 'Hàng đợi'
        text = f'{kind}: {label} · Hoàn tất {finished}/{len(campaigns)} chiến dịch · Cần kiểm tra {attention}.'
        active = snapshot.get('active_directory')
        if active:
            manifest = self.records.get(str(Path(active).resolve()))
            if manifest:
                text += ' Hiện tại: '+manifest['name']+'.'
        if snapshot.get('message'):
            text += ' '+str(snapshot['message'])
        if snapshot.get('status') == 'needs_attention':
            text += ' Mở chiến dịch có lỗi để xem chi tiết và bấm Thử lại video lỗi khi bạn muốn.'
        self.queue_status.set(text)

    def selected_directories(self):
        selected = set(self.tree.selection())
        return [Path(key) for key in self.tree.get_children() if key in selected and key in self.records]

    def show_selection(self):
        directories = self.selected_directories()
        self.selection_summary.set(f'Đã chọn {len(directories)} chiến dịch.'+( ' Chạy theo thứ tự các dòng đã chọn.' if directories else ''))
        if len(directories) == 1:
            manifest = self.records[str(directories[0])]
            settings = manifest.get('settings') or {}
            from teodub.voice_profiles import describe_voice_settings
            text = (f'Video gốc: {manifest.get("original_dir", "Chưa chọn")}\n'
                    f'Thành phẩm: {manifest.get("output_dir", "Chưa chọn")}\n'
                    f'Giọng đã lưu: {describe_voice_settings(settings)} · ASR: {settings.get("asr_model", "")} · '
                    f'Dịch: {settings.get("translation_model", "")} · TTS: {settings.get("tts_model", "")}')
            from teodub.campaign_preview import next_video_config, describe_pipeline_settings
            try:
                preview = next_video_config(directories[0], manifest, active=is_running(directories[0]))
                if preview:
                    config = preview.get('future_config') or preview['config']
                    text += '\nCấu hình lượt tiếp theo: '+describe_pipeline_settings(config)
            except (OSError, ValueError, TypeError, KeyError):
                text += '\nChưa xác minh được cấu hình lượt tiếp theo; mở chiến dịch để xem lỗi.'
            discovery = manifest.get('discovery') or {}
            if isinstance(discovery, dict) and discovery and not discovery.get('complete', False):
                text += '\nDanh sách chưa đủ kênh: '+str(discovery.get('message', 'Chỉ gồm video đã lấy được.'))
            if manifest.get('note'):
                text += '\nGhi chú: '+str(manifest['note'])
            reason = campaign_notice(manifest, active=is_running(directories[0]))
            if reason:
                text += '\nThông báo: '+reason
            self.details.set(text)
        elif directories:
            total = sum(totals(self.records[str(path)])['total'] for path in directories)
            done = sum(totals(self.records[str(path)])['completed'] for path in directories)
            self.details.set(f'{len(directories)} chiến dịch · {total} video · đã hoàn tất {done}. '
                             'Mỗi chiến dịch giữ thư mục và cấu hình riêng đã lưu.')
        else:
            self.details.set('Chọn một chiến dịch để xem thư mục và cấu hình đã lưu.')
        self.refresh_buttons()

    def refresh_buttons(self):
        selected = len(self.selected_directories())
        busy = bool(self.batch_controller.busy)
        blocked = busy or self.studio.process is not None or self.studio.campaign_busy or self.studio.login_pending or self.campaign_tab.scanning or getattr(self.campaign_tab, 'creating', False)
        one = 'normal' if selected == 1 else 'disabled'
        for button in (self.open_button, self.originals_button, self.outputs_button):
            button.configure(state=one)
        deleted = [bool(self.records[str(path)].get('archived_at')) for path in self.selected_directories()]
        self.edit_button.configure(state='normal' if selected == 1 and not blocked and not any(deleted) else 'disabled')
        self.delete_button.configure(state='normal' if selected and not blocked and not any(deleted) else 'disabled')
        self.restore_button.configure(state='normal' if selected and not blocked and all(deleted) else 'disabled')
        self.run_button.configure(state='normal' if selected and not blocked and not any(deleted) else 'disabled')
        snapshot = self.snapshot()
        resumable = snapshot and snapshot.get('status') in ('paused', 'failed', 'ready', 'running')
        self.resume_button.configure(state='normal' if resumable and not blocked else 'disabled')
        self.stop_button.configure(state='normal' if busy else 'disabled')

    def select_all(self):
        self.tree.selection_set(self.tree.get_children())
        self.show_selection()

    def management_blocked(self):
        return (self.batch_controller.busy or self.studio.process is not None or self.studio.campaign_busy
                or self.studio.login_pending or self.campaign_tab.scanning or getattr(self.campaign_tab, 'creating', False))

    def sync_campaign_tab(self):
        directory = self.campaign_tab.directory
        if directory:
            manifest = read_json(directory/'campaign.json')
            if manifest.get('archived_at'):
                self.campaign_tab.directory = None
                self.campaign_tab.saved.set('')
                self.campaign_tab.records = []
                self.campaign_tab.tree.delete(*self.campaign_tab.tree.get_children())
                self.campaign_tab.summary.set('Chiến dịch đã xóa khỏi danh sách; chọn chiến dịch khác để tiếp tục.')
                if hasattr(self.campaign_tab, 'saved_voice'):
                    self.campaign_tab.saved_voice.set('')
            else:
                self.campaign_tab.display(manifest)
        self.campaign_tab.refresh_saved()
        self.campaign_tab.refresh_buttons()
        self.refresh()

    def edit_selected(self):
        selected = self.selected_directories()
        if len(selected) != 1 or self.management_blocked():
            return
        directory = selected[0]
        manifest = self.records[str(directory)]
        if manifest.get('archived_at'):
            return
        dialog = tk.Toplevel(self)
        dialog.title('Sửa chiến dịch')
        dialog.transient(self.winfo_toplevel())
        dialog.resizable(False, False)
        panel = ttk.Frame(dialog, padding=18)
        panel.pack(fill='both', expand=True)
        ttk.Label(panel, text='Tên chiến dịch').pack(anchor='w')
        name = tk.StringVar(dialog, value=manifest['name'])
        entry = ttk.Entry(panel, textvariable=name, width=65)
        entry.pack(fill='x', pady=(4, 12))
        ttk.Label(panel, text='Ghi chú').pack(anchor='w')
        note = tk.Text(panel, height=4, width=65, wrap='word')
        note.insert('1.0', manifest.get('note', ''))
        note.pack(fill='x', pady=(4, 12))
        ttk.Label(panel, text='Đổi tên hiển thị giữ nguyên thư mục, thứ tự video và cấu hình lồng tiếng.',
                  wraplength=480).pack(anchor='w')
        error = tk.StringVar(dialog)
        ttk.Label(panel, textvariable=error, foreground='#b12727', wraplength=480).pack(anchor='w', pady=6)
        def save():
            try:
                if self.management_blocked():
                    raise ValueError('Đợi công việc hiện tại hoàn tất hoặc dừng trước khi sửa.')
                from teodub.campaign_management import update_campaign
                update_campaign(self.studio.data_dir, directory, name=name.get(), note=note.get('1.0', 'end-1c'))
                self.status.set('Đã lưu tên và ghi chú chiến dịch.')
                self.sync_campaign_tab()
                dialog.destroy()
            except (OSError, ValueError, KeyError, TypeError) as exc:
                error.set(str(exc))
        buttons = ttk.Frame(panel)
        buttons.pack(fill='x', pady=8)
        ttk.Button(buttons, text='Lưu thay đổi', command=save).pack(side='left')
        ttk.Button(buttons, text='Hủy', command=dialog.destroy).pack(side='left', padx=6)
        dialog.grab_set()
        entry.focus_set()

    def delete_selected(self):
        selected = self.selected_directories()
        if not selected or self.management_blocked() or any(self.records[str(p)].get('archived_at') for p in selected):
            return
        if not messagebox.askyesno('Xóa chiến dịch',
                f'Xóa {len(selected)} chiến dịch đã chọn khỏi danh sách?\n'
                'Video gốc và thành phẩm được giữ nguyên. Có thể bật Hiện đã xóa để khôi phục.\n'
                'Hàng đợi cũ chứa chiến dịch này cần khôi phục hoặc tạo lại danh sách chạy.', parent=self):
            return
        self.set_archived(selected, True)

    def restore_selected(self):
        selected = self.selected_directories()
        if selected and not self.management_blocked():
            self.set_archived(selected, False)

    def set_archived(self, selected, archived):
        from teodub.campaign_management import update_campaign
        count = 0
        try:
            for directory in selected:
                if self.management_blocked():
                    raise ValueError('Đợi công việc đang chạy hoàn tất hoặc dừng trước.')
                update_campaign(self.studio.data_dir, directory, archived=archived)
                count += 1
            self.status.set(f'Đã {"xóa khỏi danh sách" if archived else "khôi phục"} {count} chiến dịch. Video được giữ nguyên.')
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.status.set(f'Đã xử lý {count}/{len(selected)} chiến dịch. '+str(exc))
        finally:
            self.sync_campaign_tab()

    def open_selected(self, _event=None):
        selected = self.selected_directories()
        if len(selected) != 1:
            self.status.set('Chọn một chiến dịch để mở chi tiết.')
            return
        if self.campaign_tab.scanning:
            self.status.set('Đợi lấy danh sách kênh xong rồi mở chiến dịch đã lưu.')
            return
        try:
            directory = selected[0]
            manifest = read_json(directory/'campaign.json')
            if manifest.get('archived_at'):
                raise ValueError('Khôi phục chiến dịch trước khi mở chi tiết.')
            self.campaign_tab.directory = directory
            self.campaign_tab.refresh_saved()
            self.campaign_tab.records = [dict(url=item['url'], title=item.get('title', '')) for item in manifest['items']]
            self.campaign_tab.display(manifest)
            self.campaign_tab.refresh_buttons()
            area = getattr(self.studio, 'campaign_area', None)
            if area is not None:
                area.open_link(directory)  # Its own sub-tab (owner 23/10/2026).
            else:
                self.master.select(self.campaign_tab)
        except (OSError, ValueError, KeyError, TypeError, tk.TclError) as exc:
            self.status.set('Chưa mở được chiến dịch: '+str(exc))

    def open_folder(self, key):
        selected = self.selected_directories()
        if len(selected) != 1:
            self.status.set('Chọn một chiến dịch để mở thư mục.')
            return
        try:
            manifest = read_json(selected[0]/'campaign.json')
            path = Path(manifest[key])
            if not path.is_dir():
                self.status.set('Thư mục chưa có video. Bấm Chạy các chiến dịch đã chọn để xử lý.')
                return
            os.startfile(path)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.status.set('Chưa mở được thư mục: '+str(exc))

    def run_selected(self):
        selected = self.selected_directories()
        if not selected:
            self.status.set('Chọn ít nhất một chiến dịch trong danh sách.')
            return
        if any(self.records[str(path)].get('archived_at') for path in selected):
            self.status.set('Khôi phục chiến dịch đã xóa trước khi chạy.')
            return
        if self.batch_controller.busy or self.studio.process is not None or self.studio.campaign_busy or self.studio.login_pending or self.campaign_tab.scanning:
            self.status.set('Đợi công việc hiện tại hoàn tất hoặc dừng trước khi tạo hàng đợi mới.')
            return
        try:
            if self.order.get() == ORDERS[1]:
                self.batch_controller.start(selected, order='rotate')
            else:
                self.batch_controller.start(selected)
            self.refresh()
        except (OSError, ValueError) as exc:
            self.status.set('Chưa chạy hàng đợi: '+str(exc))

    def resume(self):
        if self.batch_controller.busy or self.studio.process is not None or self.studio.campaign_busy or self.studio.login_pending or self.campaign_tab.scanning:
            return
        try:
            self.batch_controller.resume()
            self.refresh()
        except (OSError, ValueError) as exc:
            self.status.set('Chưa tiếp tục được hàng đợi: '+str(exc))

    def stop(self):
        try:
            self.batch_controller.stop()
            self.status.set('Đang dừng hàng đợi và giữ các phần đã hoàn tất…')
            self.refresh_buttons()
        except (OSError, ValueError) as exc:
            self.status.set('Chưa dừng được hàng đợi: '+str(exc))

    def poll(self):
        self.timer = None
        if self.destroyed or self.studio.closed:
            return
        self.refresh()
        self.timer = self.after(2000, self.poll)

    def on_destroy(self, event):
        if event.widget is self:
            self.destroyed = True
            if self.timer is not None:
                try:
                    self.after_cancel(self.timer)
                except tk.TclError:
                    pass
                self.timer = None
