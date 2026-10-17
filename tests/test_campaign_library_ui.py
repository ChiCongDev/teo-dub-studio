import json
from pathlib import Path
import tempfile
import tkinter as tk
from tkinter import ttk
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from teodub.campaign_library_ui import CampaignLibraryTab


class CampaignLibraryUITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data_dir = Path(self.temp.name)
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack()
        self.studio = SimpleNamespace(data_dir=self.data_dir, closed=False, process=None,
                                      campaign_busy=False, login_pending=False)
        self.campaign_tab = ttk.Frame(self.notebook)
        self.campaign_tab.scanning = False
        self.campaign_tab.directory = None
        self.campaign_tab.refresh_saved = Mock()
        self.campaign_tab.display = Mock()
        self.campaign_tab.refresh_buttons = Mock()
        self.notebook.add(self.campaign_tab, text='Chiến dịch hàng loạt')
        self.controller = Mock()
        self.controller.busy = False
        self.controller.snapshot.return_value = None
        self.one = self.make_campaign('one', 'Chiến dịch câu cá', '2026-09-30T12:00:00', ['pending', 'pending'])
        self.two = self.make_campaign('two', 'Chiến dịch đèn', '2026-09-29T09:00:00', ['completed', 'failed'])
        self.running = patch('teodub.campaign_library_ui.is_running', return_value=False)
        self.running.start()
        self.addCleanup(self.running.stop)
        self.tab = CampaignLibraryTab(self.notebook, self.studio, self.campaign_tab, self.controller)
        self.notebook.add(self.tab, text='Danh sách chiến dịch')
        self.notebook.select(self.tab)
        self.root.update_idletasks()

    def make_campaign(self, ident, name, created, states):
        directory = self.data_dir/'campaigns'/ident
        directory.mkdir(parents=True, exist_ok=True)
        manifest = dict(id=ident, name=name, created_at=created, status='ready',
                        original_dir=str(self.data_dir/'originals'/ident),
                        output_dir=str(self.data_dir/'outputs'/ident),
                        settings=dict(voice='Thanh Niên Tự Tin', asr_model='small',
                                      translation_model='gpt-5.6-luna', tts_model='local'),
                        items=[dict(number=i, status=state, url=f'https://www.douyin.com/video/{i}', title='Thử')
                               for i, state in enumerate(states, 1)], discovery=None)
        (directory/'campaign.json').write_text(json.dumps(manifest, ensure_ascii=False), encoding='utf-8')
        return directory.resolve()

    def change(self, directory, **fields):
        path = directory/'campaign.json'
        manifest = json.loads(path.read_text(encoding='utf-8'))
        manifest.update(fields)
        path.write_text(json.dumps(manifest, ensure_ascii=False), encoding='utf-8')

    def choose(self, *directories):
        self.tab.tree.selection_set([str(path) for path in directories])
        self.tab.show_selection()

    def test_saved_library_loading_never_starts_or_resumes_work(self):
        self.assertEqual(len(self.tab.tree.get_children()), 2)
        self.controller.start.assert_not_called()
        self.controller.resume.assert_not_called()
        self.assertEqual(self.tab.tree.item(str(self.two))['values'][3:5], ['1/2', 1])

    def test_refresh_preserves_multi_selection_and_updates_video_progress(self):
        self.choose(self.one, self.two)
        self.change(self.one, items=[dict(status='completed', url='https://www.douyin.com/video/1', title='Thử')])
        self.tab.refresh()
        self.assertEqual(set(self.tab.tree.selection()), {str(self.one), str(self.two)})
        self.assertEqual(self.tab.tree.item(str(self.one))['values'][3], '1/1')
        self.assertIn('đã hoàn tất 2', self.tab.details.get())

    def test_search_matches_name_or_channel_and_select_all_only_visible_rows(self):
        self.change(self.two, discovery=dict(channel='https://www.douyin.com/user/LIGHTS', complete=True))
        self.tab.search.set('lights')
        self.tab.select_all()
        self.assertEqual(self.tab.selected_directories(), [self.two])
        self.tab.search.set('CÂU CÁ')
        self.tab.select_all()
        self.assertEqual(self.tab.selected_directories(), [self.one])

    def test_corrupt_saved_campaign_is_reported_without_hiding_valid_campaigns(self):
        broken = self.data_dir/'campaigns'/'broken'
        broken.mkdir()
        (broken/'campaign.json').write_text('{bad', encoding='utf-8')
        malformed = self.data_dir/'campaigns'/'malformed'
        malformed.mkdir()
        (malformed/'campaign.json').write_text(json.dumps(dict(name='Bad', created_at='x', items=[None])), encoding='utf-8')
        self.tab.refresh()
        self.assertEqual(len(self.tab.tree.get_children()), 2)
        self.assertIn('Bỏ qua 2 tệp', self.tab.library_summary.get())
        self.assertTrue((broken/'campaign.json').exists())

    def test_incomplete_channel_is_visible_in_row_and_details(self):
        self.change(self.one, discovery=dict(channel='https://www.douyin.com/user/FISH', complete=False,
                                            message='Chỉ lấy được 18 video công khai.'))
        self.tab.refresh()
        self.choose(self.one)
        self.assertIn('chưa đủ kênh', self.tab.tree.item(str(self.one))['values'][5])
        self.assertIn('18 video', self.tab.details.get())
        self.assertIn('gpt-5.6-luna', self.tab.details.get())

    def test_open_saved_campaign_while_queue_busy_is_read_only_and_routes_to_detail_tab(self):
        self.controller.busy = True
        self.studio.campaign_busy = True
        self.choose(self.one)
        self.tab.open_selected()
        self.assertEqual(self.campaign_tab.directory, self.one)
        self.campaign_tab.refresh_saved.assert_called_once()
        self.campaign_tab.display.assert_called_once()
        self.assertEqual(self.notebook.select(), str(self.campaign_tab))
        self.assertEqual(len(self.campaign_tab.records), 2)
        self.controller.start.assert_not_called()
        self.controller.stop.assert_not_called()

    def test_open_requires_one_selected_campaign(self):
        self.choose(self.one, self.two)
        self.tab.open_selected()
        self.campaign_tab.display.assert_not_called()
        self.assertIn('Chọn một', self.tab.status.get())

    def test_run_selected_passes_visible_row_order_once_to_controller(self):
        self.choose(self.two, self.one)
        self.tab.run_selected()
        self.controller.start.assert_called_once_with([self.one, self.two])
        self.controller.resume.assert_not_called()

    def test_active_single_video_or_scan_or_queue_prevents_new_queue(self):
        self.choose(self.one)
        for owner, field, value in ((self.studio, 'process', Mock()), (self.studio, 'campaign_busy', True),
                                    (self.studio, 'login_pending', True), (self.campaign_tab, 'scanning', True),
                                    (self.controller, 'busy', True)):
            with self.subTest(field=field):
                old = getattr(owner, field)
                setattr(owner, field, value)
                self.tab.run_selected()
                self.tab.resume()
                setattr(owner, field, old)
        self.controller.start.assert_not_called()
        self.controller.resume.assert_not_called()

    def test_paused_queue_can_only_resume_on_explicit_click(self):
        self.controller.snapshot.return_value = dict(status='paused', message='Hết hạn mức',
            campaigns=[dict(directory=str(self.one), status='pending')], active_directory=None)
        self.tab.refresh()
        self.assertEqual(str(self.tab.resume_button['state']), 'normal')
        self.assertIn('Hết hạn mức', self.tab.queue_status.get())
        self.controller.resume.assert_not_called()
        self.tab.resume()
        self.controller.resume.assert_called_once()
        self.controller.start.assert_not_called()

    def test_active_queue_progress_and_pending_status(self):
        self.controller.busy = True
        self.controller.snapshot.return_value = dict(status='running', message='Đang xử lý', active_directory=str(self.two),
            campaigns=[dict(directory=str(self.two), status='completed'), dict(directory=str(self.one), status='pending')])
        self.tab.refresh()
        self.assertIn('Hoàn tất 1/2', self.tab.queue_status.get())
        self.assertIn('Chiến dịch đèn', self.tab.queue_status.get())
        self.assertEqual(self.tab.tree.item(str(self.one))['values'][2], 'Chờ trong hàng đợi')
        self.assertEqual(str(self.tab.stop_button['state']), 'normal')
        self.tab.stop()
        self.controller.stop.assert_called_once()

    def test_finished_queue_with_errors_does_not_offer_automatic_retry(self):
        self.controller.snapshot.return_value = dict(status='needs_attention', message='Một chiến dịch có video lỗi.',
            campaigns=[dict(directory=str(self.one), status='completed'),
                       dict(directory=str(self.two), status='needs_attention')], active_directory=None)
        self.tab.refresh()
        self.assertIn('Hoàn tất 1/2', self.tab.queue_status.get())
        self.assertIn('Cần kiểm tra 1', self.tab.queue_status.get())
        self.assertIn('Thử lại video lỗi', self.tab.queue_status.get())
        self.assertEqual(str(self.tab.resume_button['state']), 'disabled')
        self.controller.resume.assert_not_called()

    def test_bad_channel_metadata_is_reported_without_breaking_library(self):
        self.change(self.one, discovery=dict(channel=['not a URL'], message='Chưa đủ kênh'))
        self.change(self.two, discovery=dict(channel='https://www.douyin.com/user/LIGHTS', message=dict(error='bad')))
        good = self.make_campaign('three', 'Chiến dịch còn tốt', '2026-09-28T12:00:00', ['pending'])
        self.tab.refresh()
        self.assertEqual(self.tab.tree.get_children(), (str(good),))
        self.assertIn('Bỏ qua 2 tệp', self.tab.library_summary.get())
        self.assertTrue((self.one/'campaign.json').is_file())

    def test_single_video_discovery_with_no_channel_stays_in_library(self):
        self.change(self.one, discovery=dict(channel=None, complete=True, reason='single_video', message='Một video.'))
        self.tab.refresh()
        self.assertIn(str(self.one), self.tab.tree.get_children())
        self.assertEqual(self.tab.tree.item(str(self.one))['values'][5], 'Danh sách link video')

    def test_malformed_queue_directory_does_not_break_saved_campaign_refresh(self):
        self.controller.snapshot.return_value = dict(status='running', active_directory=['bad'],
            campaigns=[dict(directory=['bad'], status='running'), dict(directory=123, status='pending'),
                       dict(directory=str(self.one), status='pending')])
        self.tab.refresh()
        self.assertEqual(len(self.tab.tree.get_children()), 2)
        self.assertEqual(self.tab.tree.item(str(self.one))['values'][2], 'Chờ trong hàng đợi')
        self.assertIn('Hoàn tất 0/1', self.tab.queue_status.get())

    def test_stale_running_manifest_does_not_claim_active_process(self):
        self.change(self.one, status='running')
        self.tab.refresh()
        self.assertIn('Bị gián đoạn', self.tab.tree.item(str(self.one))['values'][2])
        with patch('teodub.campaign_library_ui.is_running', side_effect=lambda directory: Path(directory) == self.one):
            self.tab.refresh()
        self.assertEqual(self.tab.tree.item(str(self.one))['values'][2], 'Đang chạy')

    def test_open_folders_uses_selected_campaign_saved_paths(self):
        original = self.data_dir/'originals'/'one'
        original.mkdir(parents=True)
        self.choose(self.one)
        with patch('teodub.campaign_library_ui.os.startfile') as open_folder:
            self.tab.open_folder('original_dir')
            open_folder.assert_called_once_with(original)
            self.tab.open_folder('output_dir')
            self.assertEqual(open_folder.call_count, 1)
        self.assertIn('chưa có video', self.tab.status.get())

    def test_controller_error_is_displayed_and_saved_campaigns_remain(self):
        self.choose(self.one)
        self.controller.start.side_effect = ValueError('Video vẫn đang chạy.')
        self.tab.run_selected()
        self.tab.refresh()
        self.assertIn('Video vẫn đang chạy', self.tab.status.get())
        self.assertTrue((self.one/'campaign.json').is_file())

    def test_destroy_cancels_periodic_refresh_without_stopping_workers(self):
        self.assertIsNotNone(self.tab.timer)
        self.tab.destroy()
        self.assertIsNone(self.tab.timer)
        self.assertTrue(self.tab.destroyed)
        self.controller.stop.assert_not_called()


    def test_selected_campaign_details_show_pause_cause(self):
        self.change(self.one, status='paused', message='Lỗi đăng nhập dịch vụ giọng')
        self.tab.refresh()
        self.choose(self.one)
        self.assertIn('Tạm dừng: Lỗi đăng nhập dịch vụ giọng', self.tab.details.get())
        self.assertIn('Kiểm tra đăng nhập', self.tab.details.get())


if __name__ == '__main__':
    unittest.main()
