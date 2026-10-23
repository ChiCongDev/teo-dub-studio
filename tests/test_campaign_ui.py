import json
from pathlib import Path
import tempfile
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import Mock, patch

from teodub.app import Studio
from teodub.campaign_ui import CampaignTab


class CampaignUITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = tk.Tk()
        self.root.withdraw()
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill='both', expand=True)
        single = ttk.Frame(self.notebook)
        self.notebook.add(single, text='Video đơn')
        self.studio = Studio(self.root, self.temp.name, parent=single)
        self.tab = CampaignTab(self.notebook, self.studio)
        self.notebook.add(self.tab, text='Chiến dịch hàng loạt')
        self.root.update_idletasks()
        self.addCleanup(self.cleanup)
        self.tab.original_dir.set(str(Path(self.temp.name)/'originals'))
        self.tab.output_dir.set(str(Path(self.temp.name)/'outputs'))
        self.tab.source.insert('1.0', 'https://www.douyin.com/video/111\nhttps://www.douyin.com/video/222')

    def cleanup(self):
        self.tab.after_cancel(self.tab.timer)
        if self.tab.auto_timer is not None:
            self.tab.after_cancel(self.tab.auto_timer)
        self.studio.process = None
        self.studio.finish_close()

    def test_separate_tab_preview_and_creation_never_launch_worker(self):
        with patch('teodub.campaign_ui.subprocess.Popen') as spawn:
            self.tab.discover()
            self.assertEqual(len(self.tab.tree.get_children()), 2)
            self.assertIsNone(self.studio.current)
            self.tab.create()
            self.assertTrue((self.tab.directory/'campaign.json').is_file())
            spawn.assert_not_called()
            self.assertEqual(len(self.notebook.tabs()), 2)

    def test_double_create_keeps_one_manifest_and_releases_creation_button(self):
        self.tab.create()
        one = self.tab.directory
        self.tab.create()
        self.assertEqual(self.tab.directory, one)
        self.assertEqual(len(list((self.tab.data_dir/'campaigns').glob('*/campaign.json'))), 1)
        self.assertFalse(self.tab.creating)
        self.assertEqual(str(self.tab.create_button['state']), 'normal')

    def test_library_delete_restore_and_rename_keep_saved_job(self):
        from teodub.campaign_library_ui import CampaignLibraryTab
        from teodub.campaign import read_json
        from teodub.common import hash_file
        self.tab.create()
        directory = self.tab.directory
        original_name = read_json(directory/'campaign.json')['name']
        job = directory/'jobs/0001/config.json'
        before = hash_file(job)
        controller = Mock(busy=False)
        controller.snapshot.return_value = None
        library = CampaignLibraryTab(self.notebook, self.studio, self.tab, controller)
        self.notebook.add(library, text='Danh sách chiến dịch')
        try:
            library.tree.selection_set(str(directory))
            library.show_selection()
            library.edit_selected()
            dialog = next(child for child in library.winfo_children() if isinstance(child, tk.Toplevel))
            panel = dialog.winfo_children()[0]
            entry = next(child for child in panel.winfo_children() if isinstance(child, ttk.Entry))
            entry.delete(0, 'end')
            entry.insert(0, 'Tên đã sửa')
            buttons = next(child for child in panel.winfo_children() if isinstance(child, ttk.Frame))
            next(child for child in buttons.winfo_children() if child['text'] == 'Lưu thay đổi').invoke()
            self.assertEqual(read_json(directory/'campaign.json')['name'], 'Tên đã sửa')
            self.assertEqual(hash_file(job), before)
            self.assertIn('Tên đã sửa', self.tab.saved.get())
            library.tree.selection_set(str(directory))
            with patch('teodub.campaign_library_ui.messagebox.askyesno', return_value=False):
                library.delete_selected()
            self.assertNotIn('archived_at', read_json(directory/'campaign.json'))
            with patch('teodub.campaign_library_ui.messagebox.askyesno', return_value=True):
                library.delete_selected()
            self.assertFalse(library.tree.exists(str(directory)))
            self.assertIsNone(self.tab.directory)
            self.assertFalse(self.tab.saved_paths)
            self.assertFalse(self.tab.tree.get_children())
            library.show_deleted.set(True)
            library.refresh()
            library.tree.selection_set(str(directory))
            library.show_selection()
            self.assertEqual(str(library.run_button['state']), 'disabled')
            library.run_selected()
            controller.start.assert_not_called()
            library.restore_selected()
            self.assertNotIn('archived_at', read_json(directory/'campaign.json'))
            self.assertEqual(hash_file(job), before)
            self.assertEqual(self.tab.directory, directory)
            self.assertNotEqual(read_json(directory/'campaign.json')['name'], original_name)
        finally:
            library.destroy()

    def test_campaign_settings_frozen_without_reusing_single_srt(self):
        self.studio.fields['subtitles'].set('wrong-video.srt')
        self.studio.fields['burn_subtitles'].set(True)
        self.studio.fields['subtitle_color'].set('yellow')
        self.tab.capture_settings()
        self.tab.reverse.set(True)
        self.tab.create()
        config = json.loads((self.tab.directory/'jobs/0001/config.json').read_text(encoding='utf-8'))
        self.assertEqual(config['input'], 'https://www.douyin.com/video/222')
        self.assertIsNone(config['subtitles'])
        self.assertTrue(config['burn_subtitles'])
        self.assertEqual(config['subtitle_color'], 'yellow')
        self.assertIsNone(self.studio.current)

    def test_campaign_creation_refreshes_form_then_keeps_saved_snapshot(self):
        chosen = self.studio.apply_wording_selection(True, 'bán => báng\ngiá => giá tham khảo')
        self.tab.capture_settings()
        self.assertIn('Từ ngữ: Bật', self.tab.summary.get())
        chosen = self.studio.apply_wording_selection(False, 'mua => lấy')
        self.tab.create()
        manifest = json.loads((self.tab.directory/'campaign.json').read_text(encoding='utf-8'))
        self.assertEqual(manifest['settings']['wording'], chosen)
        for number in ('0001', '0002'):
            config = json.loads((self.tab.directory/f'jobs/{number}/config.json').read_text(encoding='utf-8'))
            self.assertEqual(config['wording'], chosen)
        self.tab.capture_settings()
        self.assertIn('Từ ngữ: Tắt', self.tab.summary.get())
        self.assertFalse(self.tab.settings['wording']['enabled'])
        self.assertEqual(json.loads((self.tab.directory/'campaign.json').read_text(encoding='utf-8'))['settings']['wording'], chosen)

    def test_campaign_start_locks_single_video_and_does_not_double_spawn(self):
        self.tab.create()
        worker = Mock()
        worker.poll.return_value = None
        with patch('teodub.campaign_ui.subprocess.Popen', return_value=worker) as spawn:
            self.tab.start()
            self.tab.start()
            self.studio.start('preview')
            spawn.assert_called_once()
            self.assertIn('--campaign-worker', spawn.call_args.args[0])
            self.assertTrue(self.studio.campaign_busy)
            self.assertEqual(str(self.studio.start_button['state']), 'disabled')

    def waiting_for_source(self, resume_after, status='paused', **extra):
        self.tab.create()
        path = self.tab.directory/'campaign.json'
        manifest = json.loads(path.read_text(encoding='utf-8'))
        manifest.update(status=status, resume_after=resume_after, message='Chờ nguồn tải', **extra)
        path.write_text(json.dumps(manifest, ensure_ascii=False), encoding='utf-8')

    def test_a_wait_during_a_retry_pass_restarts_as_a_retry_pass(self):
        self.waiting_for_source(1.0, resume_retry_failed=True)
        worker = Mock()
        worker.poll.return_value = None
        with patch('teodub.campaign_ui.subprocess.Popen', return_value=worker) as spawn:
            self.tab.poll()
            spawn.assert_called_once()
            self.assertIn('--retry-failed', spawn.call_args.args[0])

    def test_a_campaign_waiting_for_a_download_source_restarts_itself_when_the_time_comes(self):
        self.waiting_for_source(1.0)
        worker = Mock()
        worker.poll.return_value = None
        with patch('teodub.campaign_ui.subprocess.Popen', return_value=worker) as spawn:
            self.tab.poll()
            self.tab.poll()
            spawn.assert_called_once()
            self.assertIn('--campaign-worker', spawn.call_args.args[0])
            self.assertNotIn('--retry-failed', spawn.call_args.args[0])

    def test_it_does_not_restart_before_the_time_nor_for_other_pauses_nor_while_busy(self):
        import time
        self.waiting_for_source(time.time()+3600)
        with patch('teodub.campaign_ui.subprocess.Popen') as spawn:
            self.tab.poll()
            spawn.assert_not_called()
        self.waiting_for_source(1.0, status='running')
        with patch('teodub.campaign_ui.subprocess.Popen') as spawn:
            self.tab.poll()
            spawn.assert_not_called()
        self.waiting_for_source(1.0)
        self.studio.process = Mock()
        with patch('teodub.campaign_ui.subprocess.Popen') as spawn:
            self.tab.poll()
            spawn.assert_not_called()

    def test_single_job_blocks_campaign(self):
        self.tab.create()
        self.studio.process = Mock()
        with patch('teodub.campaign_ui.subprocess.Popen') as spawn:
            self.tab.start()
            spawn.assert_not_called()

    def test_changed_channel_requires_rescan(self):
        self.tab.source_kind.set('Link kênh Douyin')
        self.tab.source.delete('1.0', 'end')
        self.tab.source.insert('1.0', 'https://www.douyin.com/user/NEW')
        self.tab.discovery = dict(input='old channel', result=dict(items=[]))
        self.tab.create()
        self.assertIsNone(self.tab.directory)
        self.assertIn('Lấy danh sách', self.tab.status.get())

    def channel_source(self):
        self.tab.source.delete('1.0', 'end')
        self.tab.source.insert('1.0', 'https://v.douyin.com/ABC/')
        return self.tab.input_key()

    def test_pasted_channel_auto_fetches_and_shows_each_url_without_launching(self):
        self.channel_source()
        rows = [dict(url='https://www.douyin.com/video/111', title='Đèn'),
                dict(url='https://www.douyin.com/video/222', title='Thử đèn')]
        def scan(*args, **kwargs):
            kwargs['on_items'](rows[:1])
            kwargs['on_items'](rows)
            return dict(items=rows, channel='https://www.douyin.com/user/owner',
                        complete=False, reason='login_required', message='Cần đăng nhập để lấy thêm.')
        with patch('teodub.channel_discovery.scan_channel', side_effect=scan) as scanner, \
             patch('teodub.campaign_ui.threading.Thread', side_effect=lambda **kw: Mock(start=kw['target'])), \
             patch('teodub.campaign_ui.subprocess.Popen') as worker:
            self.tab.auto_discover()
            self.tab.after_cancel(self.tab.timer)
            self.tab.poll()
            self.tab.auto_discover()
            scanner.assert_called_once()
            worker.assert_not_called()
        self.assertEqual(len(self.tab.tree.get_children()), 2)
        self.assertEqual(self.tab.tree.set('1', 'url'), rows[0]['url'])
        self.assertEqual(self.tab.tree.set('2', 'title'), 'Thử đèn')
        self.assertIn('đăng nhập', self.tab.status.get())
        self.assertIsNone(self.tab.directory)
        self.tab.create()
        self.assertEqual((self.tab.directory/'links.txt').read_text(encoding='utf-8').splitlines(), [x['url'] for x in rows])

    def test_streaming_rows_do_not_wait_for_scan_to_finish(self):
        key = self.channel_source()
        self.tab.scanning = True
        self.tab.scan_messages.put((key, 'items', [dict(url='https://www.douyin.com/video/111', title='Đèn')]))
        self.tab.after_cancel(self.tab.timer)
        self.tab.poll()
        self.assertTrue(self.tab.scanning)
        self.assertEqual(self.tab.tree.set('1', 'url'), 'https://www.douyin.com/video/111')

    def test_changed_input_discards_old_browser_results(self):
        key = self.channel_source()
        self.tab.scanning = True
        self.tab.preview_records([])
        self.tab.source.delete('1.0', 'end')
        self.tab.source.insert('1.0', 'https://v.douyin.com/OTHER/')
        self.tab.scan_messages.put((key, 'items', [dict(url='https://www.douyin.com/video/111')]))
        self.tab.scan_messages.put((key, 'done', (key[0], dict(items=[], message='Old'))))
        self.tab.after_cancel(self.tab.timer)
        self.tab.poll()
        self.assertEqual(self.tab.records, [])
        self.assertIsNone(self.tab.discovery)
        self.assertFalse(self.tab.scanning)

    def test_manual_mode_can_disable_auto_and_copy_all_links(self):
        self.tab.auto_fetch.set(False)
        with patch.object(self.tab, 'discover') as discover:
            self.tab.auto_discover()
            discover.assert_not_called()
        self.tab.discover()
        self.tab.copy_all()
        self.assertEqual(self.root.clipboard_get().splitlines(), ['https://www.douyin.com/video/111', 'https://www.douyin.com/video/222'])
        self.tab.tree.selection_set('2')
        self.tab.copy_selected()
        self.assertEqual(self.root.clipboard_get(), 'https://www.douyin.com/video/222')

    def test_running_row_reads_real_stage_percent_and_keeps_url(self):
        self.tab.create()
        manifest = json.loads((self.tab.directory/'campaign.json').read_text(encoding='utf-8'))
        row = manifest['items'][0]
        row['status'] = 'running'
        (Path(row['job'])/'events.jsonl').write_text(json.dumps(dict(stage='voice', percent=61, message='Đang đọc câu 3/5'))+'\n', encoding='utf-8')
        with patch('teodub.campaign_ui.is_running', return_value=True):
            self.tab.display(manifest)
        self.assertEqual(self.tab.tree.set('1', 'state'), 'Tạo giọng')
        self.assertEqual(self.tab.tree.set('1', 'percent'), '61%')
        self.assertEqual(self.tab.tree.set('1', 'url'), row['url'])

    def test_source_modified_debounces_and_stop_does_not_restart_same_scan(self):
        self.channel_source()
        self.tab.source.edit_modified(True)
        self.tab.source_modified()
        first = self.tab.auto_timer
        self.tab.source.edit_modified(True)
        self.tab.source_modified()
        self.assertNotEqual(first, self.tab.auto_timer)
        self.assertFalse(self.tab.source.edit_modified())
        self.tab.auto_key = self.tab.input_key()
        self.tab.stop()
        with patch.object(self.tab, 'discover') as discover:
            self.tab.auto_discover()
            discover.assert_not_called()

    def test_selected_coccoc_is_passed_to_reader_with_separate_profile(self):
        self.channel_source()
        self.tab.browser_label.set('Cốc Cốc (thử nghiệm)')
        result = dict(items=[dict(url='https://www.douyin.com/video/111', title='Đèn')],
                      message='Danh sách thử', complete=False, reason='no_new_posts',
                      browser='coccoc', requested_browser='coccoc')
        with patch('teodub.channel_discovery.scan_channel', return_value=result) as scanner, \
             patch('teodub.campaign_ui.threading.Thread', side_effect=lambda **kw: Mock(start=kw['target'])), \
             patch('teodub.campaign_ui.subprocess.Popen') as worker:
            self.tab.discover()
            self.tab.after_cancel(self.tab.timer)
            self.tab.poll()
            worker.assert_not_called()
        self.assertEqual(scanner.call_args.kwargs['browser'], 'coccoc')
        self.assertEqual(scanner.call_args.args[1], self.tab.data_dir/'douyin-channel-browser'/'coccoc')
        self.assertEqual(self.tab.discovery['result']['browser'], 'coccoc')
        prefs = json.loads((self.tab.data_dir/'campaign-preferences.json').read_text(encoding='utf-8'))
        self.assertEqual(prefs['browser'], 'coccoc')

    def test_browser_change_cancels_old_scan_and_ignores_its_rows(self):
        key = self.channel_source()
        self.tab.scanning = True
        self.tab.scan_key = key
        self.tab.browser_label.set('Cốc Cốc (thử nghiệm)')
        self.assertTrue(self.tab.scan_cancel.is_set())
        self.tab.scan_messages.put((key, 'done', (key[0], dict(items=[], message='old'))))
        self.tab.after_cancel(self.tab.timer)
        self.tab.poll()
        self.assertIsNone(self.tab.discovery)
        self.assertFalse(self.tab.scanning)

    def test_browser_preference_restores_without_starting_a_reader(self):
        self.tab.browser_label.set('Cốc Cốc (thử nghiệm)')
        self.tab.save_campaign_preferences()
        with patch('teodub.channel_discovery.scan_channel') as scanner:
            new_tab = CampaignTab(self.notebook, self.studio)
            try:
                self.assertEqual(new_tab.selected_browser(), 'coccoc')
                scanner.assert_not_called()
            finally:
                new_tab.after_cancel(new_tab.timer)
                if new_tab.auto_timer is not None:
                    new_tab.after_cancel(new_tab.auto_timer)
                new_tab.destroy()

    def test_create_preserves_browser_preference_and_discovery_metadata(self):
        self.tab.browser_label.set('Cốc Cốc (thử nghiệm)')
        self.tab.create()
        prefs = json.loads((self.tab.data_dir/'campaign-preferences.json').read_text(encoding='utf-8'))
        self.assertEqual(prefs['browser'], 'coccoc')

    def test_choose_coccoc_shows_window_and_remembers_explicit_hidden_preference(self):
        self.tab.browser_label.set('Cốc Cốc (thử nghiệm)')
        self.assertTrue(self.tab.visible.get())
        self.tab.visible.set(False)
        prefs = json.loads((self.tab.data_dir/'campaign-preferences.json').read_text(encoding='utf-8'))
        self.assertIs(prefs['visible'], False)
        with patch('teodub.channel_discovery.scan_channel') as scanner:
            restored = CampaignTab(self.notebook, self.studio)
            try:
                self.assertEqual(restored.selected_browser(), 'coccoc')
                self.assertFalse(restored.visible.get())
                scanner.assert_not_called()
            finally:
                restored.after_cancel(restored.timer)
                if restored.auto_timer is not None:
                    restored.after_cancel(restored.auto_timer)
                restored.destroy()

    def test_legacy_coccoc_preference_defaults_to_visible_without_auto_scan(self):
        from teodub.common import atomic_json
        atomic_json(self.tab.data_dir/'campaign-preferences.json', dict(browser='coccoc',
                    original_dir=self.tab.original_dir.get(), output_dir=self.tab.output_dir.get()))
        with patch('teodub.channel_discovery.scan_channel') as scanner:
            restored = CampaignTab(self.notebook, self.studio)
            try:
                self.assertTrue(restored.visible.get())
                scanner.assert_not_called()
            finally:
                restored.after_cancel(restored.timer)
                if restored.auto_timer is not None:
                    restored.after_cancel(restored.auto_timer)
                restored.destroy()

    def test_scan_report_exists_before_launcher_and_updates_while_still_running(self):
        self.channel_source()
        self.tab.browser_label.set('Cốc Cốc (thử nghiệm)')
        report_path = self.tab.data_dir/'channel-scan-last.json'
        def scan(*args, **kwargs):
            initial = json.loads(report_path.read_text(encoding='utf-8'))
            self.assertEqual(initial['status'], 'running')
            self.assertTrue(initial['visible'])
            self.assertIn('chuẩn bị mở', initial['last_message'])
            kwargs['progress']('Đang mở Cốc Cốc')
            during = json.loads(report_path.read_text(encoding='utf-8'))
            self.assertEqual(during['last_message'], 'Đang mở Cốc Cốc')
            return dict(items=[dict(url='https://www.douyin.com/video/111')],
                        browser='coccoc', complete=False, reason='no_new_posts', message='Giữ danh sách vừa đọc.')
        with patch('teodub.channel_discovery.scan_channel', side_effect=scan), \
             patch('teodub.campaign_ui.threading.Thread', side_effect=lambda **kw: Mock(start=kw['target'])):
            self.tab.discover()
        finished = json.loads(report_path.read_text(encoding='utf-8'))
        self.assertEqual(finished['status'], 'completed')
        self.assertEqual(finished['count'], 1)

    def test_scan_report_dialog_shows_cause_without_launch_or_resubmit(self):
        from teodub.common import atomic_json
        atomic_json(self.tab.data_dir/'channel-scan-last.json', dict(status='failed',
                    time='2026-09-30', last_message='Đang mở', message='Không mở được',
                    causes=[dict(message='Browser launch failed')]))
        with patch('tkinter.messagebox.showinfo') as dialog, \
             patch('teodub.channel_discovery.scan_channel') as scanner:
            self.tab.show_scan_report()
            scanner.assert_not_called()
        self.assertIn('Browser launch failed', dialog.call_args.args[1])

    def test_desktop_button_opens_normal_browser_and_disables_pending_auto_scan(self):
        self.channel_source()
        self.tab.schedule_discovery()
        with patch('teodub.desktop_browser.open_desktop_coccoc') as opener, \
             patch('teodub.channel_discovery.scan_channel') as scanner:
            self.tab.desktop_browser_button.invoke()
            opener.assert_called_once_with('https://v.douyin.com/ABC/')
            scanner.assert_not_called()
        self.assertFalse(self.tab.auto_fetch.get())
        self.assertIsNone(self.tab.auto_timer)
        self.assertIsNone(self.tab.discovery)
        self.assertIn('Chưa lấy link tự động', self.tab.status.get())
        prefs = json.loads((self.tab.data_dir/'campaign-preferences.json').read_text(encoding='utf-8'))
        self.assertIs(prefs['auto_fetch'], False)
        restored = CampaignTab(self.notebook, self.studio)
        try:
            self.assertFalse(restored.auto_fetch.get())
            self.assertIsNone(restored.auto_timer)
        finally:
            restored.after_cancel(restored.timer)
            restored.destroy()

    def test_desktop_button_without_channel_still_opens_browser_and_keeps_source(self):
        before = self.tab.source.get('1.0', 'end')
        with patch('teodub.desktop_browser.open_desktop_coccoc') as opener:
            self.tab.desktop_browser_button.invoke()
        opener.assert_called_once_with('')
        self.assertEqual(self.tab.source.get('1.0', 'end'), before)

    def test_desktop_button_reports_launch_error_without_claiming_links_read(self):
        with patch('teodub.desktop_browser.open_desktop_coccoc', side_effect=ValueError('Chưa tìm thấy Cốc Cốc')):
            self.tab.desktop_browser_button.invoke()
        self.assertEqual(self.tab.status.get(), 'Chưa tìm thấy Cốc Cốc')
        self.assertIsNone(self.tab.discovery)

    def test_desktop_launch_does_not_interfere_with_an_active_scan(self):
        self.tab.scanning = True
        self.tab.refresh_buttons()
        self.assertEqual(str(self.tab.desktop_browser_button['state']), 'disabled')
        with patch('teodub.desktop_browser.open_desktop_coccoc') as opener:
            self.tab.open_desktop_browser()
            opener.assert_not_called()
        self.assertTrue(self.tab.auto_fetch.get())

    def test_chrome_login_and_following_scan_share_the_saved_profile(self):
        self.channel_source()
        launched = []
        with patch('teodub.campaign_ui.threading.Thread', side_effect=lambda **kw: Mock(start=lambda: launched.append(kw))), \
             patch('teodub.channel_discovery.scan_channel') as scanner:
            self.tab.chrome_login_button.invoke()
            self.tab.start_chrome_login()
            self.tab.discover()
            scanner.assert_not_called()
        self.assertEqual(len(launched), 1)
        self.assertFalse(launched[0]['daemon'])
        self.assertTrue(self.studio.login_pending)
        self.assertEqual(str(self.tab.scan_button['state']), 'disabled')
        self.assertEqual(str(self.tab.browser_picker['state']), 'disabled')
        self.assertFalse(self.tab.auto_fetch.get())
        self.assertTrue(self.tab.visible.get())
        self.assertEqual(self.tab.selected_browser(), 'chrome')
        self.tab.chrome_login_done_button.invoke()
        with patch('teodub.channel_login.open_chrome_login', return_value='finished') as login:
            launched[0]['target']()
        profile = login.call_args.args[0]
        self.assertEqual(profile, self.tab.data_dir/'douyin-channel-browser'/'chrome')
        self.assertTrue(login.call_args.kwargs['finished'].is_set())
        self.tab.after_cancel(self.tab.timer)
        self.tab.poll()
        self.assertFalse(self.studio.login_pending)
        self.assertFalse(self.tab.chrome_login_pending)
        self.assertIn('chưa xác nhận', self.tab.chrome_login_status.get())
        result = dict(items=[dict(url='https://www.douyin.com/video/123', title='Fixture')], complete=False,
                      browser='chrome', reason='limit', message='Fixture partial')
        with patch('teodub.channel_discovery.scan_channel', return_value=result) as scanner, \
             patch('teodub.campaign_ui.threading.Thread', side_effect=lambda **kw: Mock(start=kw['target'])):
            self.tab.discover()
        self.assertEqual(scanner.call_args.args[1], profile)
        self.assertEqual(scanner.call_args.kwargs['browser'], 'chrome')
        prefs = json.loads((self.tab.data_dir/'campaign-preferences.json').read_text(encoding='utf-8'))
        self.assertEqual(prefs['browser'], 'chrome')
        self.assertTrue(prefs['visible'])
        restored = CampaignTab(self.notebook, self.studio)
        try:
            self.assertEqual(restored.selected_browser(), 'chrome')
            self.assertTrue(restored.visible.get())
            self.assertIsNone(restored.auto_timer)
        finally:
            restored.after_cancel(restored.timer)
            restored.destroy()

    def test_failed_chrome_login_releases_gui_without_automatic_retry(self):
        with patch('teodub.channel_login.open_chrome_login', side_effect=ValueError('Chrome fixture failed')) as login, \
             patch('teodub.campaign_ui.threading.Thread', side_effect=lambda **kw: Mock(start=kw['target'])):
            self.tab.start_chrome_login()
        self.tab.after_cancel(self.tab.timer)
        self.tab.poll()
        login.assert_called_once()
        self.assertFalse(self.studio.login_pending)
        self.assertFalse(self.tab.chrome_login_pending)
        self.assertIn('Chrome fixture failed', self.tab.chrome_login_status.get())
        self.assertIsNone(self.tab.auto_timer)

    def test_chrome_login_stop_and_window_close_signal_graceful_cleanup(self):
        with patch('teodub.campaign_ui.threading.Thread'):
            self.tab.start_chrome_login()
        self.tab.stop()
        self.assertTrue(self.tab.chrome_login_cancel.is_set())
        self.tab.chrome_login_cancel.clear()
        self.tab.on_destroy(Mock(widget=self.tab))
        self.assertTrue(self.tab.chrome_login_cancel.is_set())


    def test_completion_column_and_persistent_notice_with_detail_dialog(self):
        from teodub.common import atomic_json
        from teodub.campaign import read_json
        self.tab.create()
        manifest = read_json(self.tab.directory/'campaign.json')
        manifest.update(status='paused', message='Đã hết hạn mức Codex')
        manifest['items'][0].update(status='completed', completed_at='2026-10-01T02:03:04+00:00',
                                    processing_seconds=65, message='Đã hoàn tất')
        manifest['items'][1].update(status='failed', message='Đã hết hạn mức Codex')
        atomic_json(self.tab.directory/'campaign.json', manifest)
        with patch('tkinter.messagebox.showinfo') as popup:
            self.tab.display(manifest)
            self.tab.display(manifest)
            popup.assert_not_called()
        columns = self.tab.tree['columns']
        self.assertEqual(columns[columns.index('percent')+1], 'completed_at')
        self.assertEqual(self.tab.tree.set('1', 'completed_at'), '01/10/2026 09:03:04')
        self.assertEqual(self.tab.tree.set('2', 'completed_at'), '—')
        self.assertIn('Tạm dừng: Đã hết hạn mức Codex', self.tab.notice.get())
        self.assertIn('Kiểm tra hạn mức', self.tab.notice.get())
        self.tab.tree.selection_set('1')
        with patch('tkinter.messagebox.showinfo') as popup, patch('teodub.campaign_ui.os.startfile') as open_file:
            self.tab.show_item(detail_only=True)
            self.assertIn('Tổng thời gian xử lý: 00:01:05', popup.call_args.args[1])
            self.assertIn('01/10/2026 09:03:04', popup.call_args.args[1])
            open_file.assert_not_called()
        self.tab.preview_records([dict(url='https://www.douyin.com/video/111')])
        self.assertEqual(self.tab.tree.set('1', 'completed_at'), '—')
        self.assertEqual(self.tab.notice.get(), '')

    def test_campaign_launch_error_notice_is_not_overwritten_by_progress_refresh(self):
        from teodub.campaign import read_json
        self.tab.create()
        with patch('teodub.campaign_ui.subprocess.Popen', side_effect=OSError('Không đủ bộ nhớ')):
            self.tab.start()
        self.tab.display(read_json(self.tab.directory/'campaign.json'))
        self.assertIn('Chưa khởi động chiến dịch', self.tab.notice.get())
        self.assertIn('Không đủ bộ nhớ', self.tab.notice.get())


if __name__ == '__main__':
    unittest.main()
