"""Campaign posting display tests: hidden Tk and mocked passive snapshots only."""
import json
from pathlib import Path
import tempfile
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import patch

from teodub.app import Studio
from teodub.campaign_ui import CampaignTab
from teodub.common import atomic_json


class CampaignPostingUITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        posting = patch('teodub.campaign_ui.campaign_posting_statuses')
        self.posting = posting.start()
        self.posting.side_effect = lambda data_dir, directory, manifest: self.snapshot(manifest)
        self.addCleanup(posting.stop)
        worker = patch('teodub.campaign_ui.subprocess.Popen')
        self.worker = worker.start()
        self.addCleanup(worker.stop)
        self.addCleanup(self.worker.assert_not_called)
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
        self.tab.source.insert('1.0', '\n'.join(
            f'https://www.douyin.com/video/{number}' for number in range(111, 115)))

    def cleanup(self):
        self.tab.after_cancel(self.tab.timer)
        if self.tab.auto_timer is not None:
            self.tab.after_cancel(self.tab.auto_timer)
        self.studio.process = None
        self.studio.finish_close()

    def campaign(self):
        self.tab.create()
        manifest = json.loads((self.tab.directory/'campaign.json').read_text(encoding='utf-8'))
        manifest['status'] = 'completed'
        for item in manifest['items']:
            item.update(status='completed', completed_at='2026-10-01T02:03:04+00:00',
                        processing_seconds=65, message=f'Đã dựng video {item["number"]}')
        atomic_json(self.tab.directory/'campaign.json', manifest)
        self.posting.side_effect = None
        self.posting.return_value = self.snapshot(manifest)
        self.posting.reset_mock()
        return manifest

    @staticmethod
    def status(label, details='', *, published=0, targets=1):
        return dict(label=label, details=details, published=published, targets=targets)

    def snapshot(self, manifest, statuses=None):
        result = {item['number']: self.status('Chưa đăng', targets=0)
                  for item in manifest['items']}
        result.update(statuses or {})
        return result

    def test_posting_column_is_between_completion_and_message(self):
        columns = tuple(self.tab.tree['columns'])
        self.assertEqual(columns[columns.index('percent'):],
                         ('percent', 'completed_at', 'posting', 'drive', 'message'))
        self.assertEqual(self.tab.tree.heading('posting', 'text'), 'Trạng thái đăng bài')

    def test_display_maps_each_posting_label_by_video_number_without_changing_render_status(self):
        manifest = self.campaign()
        labels = {1: 'Chưa đăng', 2: 'Đang lên lịch', 3: 'Đang đăng', 4: 'Đăng thành công'}
        self.posting.return_value = {
            number: self.status(label, published=int(number == 4))
            for number, label in labels.items()}
        # Snapshot order is irrelevant: a posting result belongs to its source number.
        manifest['items'].reverse()
        with patch('teodub.campaign_ui.is_running', return_value=False), \
             patch('teodub.campaign_ui.subprocess.Popen') as spawn:
            self.tab.display(manifest)
        self.posting.assert_called_once_with(self.tab.data_dir, self.tab.directory, manifest)
        spawn.assert_not_called()
        for number, label in labels.items():
            with self.subTest(number=number):
                self.assertEqual(self.tab.tree.set(str(number), 'posting'), label)
                self.assertEqual(self.tab.tree.set(str(number), 'state'), 'Hoàn tất')
                self.assertEqual(self.tab.tree.set(str(number), 'percent'), '100%')
                self.assertEqual(self.tab.tree.set(str(number), 'completed_at'), '01/10/2026 09:03:04')
                self.assertIn(f'Đã dựng video {number}', self.tab.tree.set(str(number), 'message'))

    def test_existing_poll_refreshes_posting_and_keeps_one_second_interval(self):
        manifest = self.campaign()
        self.posting.side_effect = [
            self.snapshot(manifest, {1: self.status('Đang lên lịch')}),
            self.snapshot(manifest, {1: self.status('Đang đăng')}),
            self.snapshot(manifest, {1: self.status('Đăng thành công', published=1)}),
        ]
        with patch('teodub.campaign_ui.is_running', return_value=False), \
             patch('teodub.campaign_ui.subprocess.Popen') as spawn, \
             patch.object(self.tab, 'after', wraps=self.tab.after) as schedule_refresh:
            for label in ('Đang lên lịch', 'Đang đăng', 'Đăng thành công'):
                self.tab.after_cancel(self.tab.timer)
                self.tab.poll()
                self.assertEqual(self.tab.tree.set('1', 'posting'), label)
                self.assertEqual(schedule_refresh.call_args.args, (1000, self.tab.poll))
            spawn.assert_not_called()
        self.assertEqual(self.posting.call_count, 3)
        for call in self.posting.call_args_list:
            self.assertEqual(call.args[:2], (self.tab.data_dir, self.tab.directory))

    def test_detail_dialog_uses_source_number_after_reordering_and_includes_publication_uncertainty(self):
        manifest = self.campaign()
        selected = manifest['items'][0]
        different = manifest['items'][-1]
        manifest['items'].reverse()
        atomic_json(self.tab.directory/'campaign.json', manifest)
        detail = 'Đã xác nhận 1/2 Page; 1 Page cần kiểm tra. Chưa xác nhận kết quả; không tự gửi lại.'
        self.posting.return_value = self.snapshot(
            manifest, {1: self.status('Chưa đăng', detail, published=1, targets=2)})
        with patch('teodub.campaign_ui.is_running', return_value=False):
            self.tab.display(manifest)
            self.tab.tree.selection_set('1')
            with patch('tkinter.messagebox.showinfo') as popup, \
                 patch('teodub.campaign_ui.os.startfile') as open_file:
                self.tab.show_item(detail_only=True)
        popup.assert_called_once()
        text = popup.call_args.args[1]
        self.assertIn('Chưa đăng', text)
        self.assertIn(detail, text)
        self.assertIn('Đã dựng video 1', text)
        self.assertIn('01/10/2026 09:03:04', text)
        self.assertIn('Tổng thời gian xử lý: 00:01:05', text)
        self.assertTrue(text.startswith(selected['url']))
        self.assertIn(str(Path(selected['job'])/'worker.log'), text)
        self.assertNotIn(different['url'], text)
        self.assertNotIn(different['message'], text)
        self.assertNotIn(str(Path(different['job'])/'worker.log'), text)
        open_file.assert_not_called()

    def test_read_failure_clears_old_success_and_explains_uncertainty_in_details(self):
        manifest = self.campaign()
        self.posting.return_value = self.snapshot(
            manifest, {1: self.status('Đăng thành công', published=1)})
        with patch('teodub.campaign_ui.is_running', return_value=False), \
             patch('tkinter.messagebox.showinfo') as popup:
            self.tab.display(manifest)
            self.assertEqual(self.tab.tree.set('1', 'posting'), 'Đăng thành công')
            self.posting.side_effect = OSError('fixture: snapshot unavailable')
            self.tab.display(manifest)
            popup.assert_not_called()
            for item in manifest['items']:
                self.assertEqual(self.tab.tree.set(str(item['number']), 'posting'), 'Cần kiểm tra')
                self.assertEqual(self.tab.tree.set(str(item['number']), 'state'), 'Hoàn tất')
            self.tab.tree.selection_set('1')
            self.tab.show_item(detail_only=True)
        popup.assert_called_once()
        text = popup.call_args.args[1]
        self.assertIn('Cần kiểm tra', text)
        self.assertIn('Chưa đọc được trạng thái đăng bài', text)
        self.assertIn('fixture: snapshot unavailable', text)
        self.assertNotIn('Đăng thành công', text)

    def test_missing_video_summary_replaces_previous_success_with_visible_attention(self):
        manifest = self.campaign()
        self.posting.return_value = self.snapshot(
            manifest, {1: self.status('Đăng thành công', published=1)})
        with patch('teodub.campaign_ui.is_running', return_value=False), \
             patch('tkinter.messagebox.showinfo') as popup:
            self.tab.display(manifest)
            self.assertEqual(self.tab.tree.set('1', 'posting'), 'Đăng thành công')
            missing = self.snapshot(manifest, {2: self.status('Đang lên lịch')})
            del missing[1]
            self.posting.return_value = missing
            self.tab.display(manifest)
            popup.assert_not_called()
        self.assertEqual(self.tab.tree.set('1', 'posting'), 'Cần kiểm tra')
        self.assertEqual(self.tab.tree.set('2', 'posting'), 'Đang lên lịch')
        self.assertEqual(self.tab.tree.set('1', 'state'), 'Hoàn tất')

    def test_preview_resets_previous_posting_results_without_reading_saved_status(self):
        manifest = self.campaign()
        self.posting.return_value = self.snapshot(
            manifest, {1: self.status('Đăng thành công', published=1)})
        with patch('teodub.campaign_ui.is_running', return_value=False):
            self.tab.display(manifest)
        self.assertEqual(self.tab.tree.set('1', 'posting'), 'Đăng thành công')
        self.posting.reset_mock()
        self.tab.preview_records([dict(url='https://www.douyin.com/video/999', title='Nguồn mới')])
        self.posting.assert_not_called()
        self.assertEqual(self.tab.tree.get_children(), ('1',))
        self.assertEqual(self.tab.tree.set('1', 'posting'), 'Chưa đăng')
        self.assertEqual(self.tab.tree.set('1', 'completed_at'), '—')
        self.assertEqual(self.tab.tree.set('1', 'url'), 'https://www.douyin.com/video/999')
        self.assertIsNone(self.tab.directory)

    def test_switching_saved_campaign_drops_old_posting_results(self):
        manifest = self.campaign()
        first = self.tab.directory
        second = first.parent/'other-fixture'
        second.mkdir()
        other = json.loads(json.dumps(manifest))
        other['name'] = 'Chiến dịch fixture khác'
        other['items'][0]['url'] = 'https://www.douyin.com/video/999'
        atomic_json(second/'campaign.json', other)
        self.tab.saved_paths = {'Đầu tiên': first, 'Tiếp theo': second}
        self.posting.side_effect = lambda data_dir, directory, snapshot: self.snapshot(
            snapshot, {1: self.status('Đăng thành công', published=1)} if directory == first else {})
        with patch('teodub.campaign_ui.is_running', return_value=False):
            self.tab.saved.set('Đầu tiên')
            self.tab.select_saved()
            self.assertEqual(self.tab.tree.set('1', 'posting'), 'Đăng thành công')
            self.tab.saved.set('Tiếp theo')
            self.tab.select_saved()
        self.assertEqual(self.tab.directory, second)
        self.assertEqual(self.tab.tree.set('1', 'posting'), 'Chưa đăng')
        self.assertEqual(self.tab.tree.set('1', 'url'), 'https://www.douyin.com/video/999')
        self.assertEqual(self.posting.call_args.args[:2], (self.tab.data_dir, second))


if __name__ == '__main__':
    unittest.main()
