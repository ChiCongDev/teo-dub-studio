"""Lấy thêm video mới on a link's tab (owner 30/10/2026): the button, the dialog and what it adds. Real Tk windows, but the
channel reading is replaced by a canned answer: no Chrome, no Douyin, no AI."""
from pathlib import Path
import tempfile
import threading
import time
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import Mock, patch

from teodub import campaign, campaign_append
from teodub.campaign_batch_controller import CampaignBatchController
from tests.test_campaign_rotation import SETTINGS

CHANNEL = 'https://www.douyin.com/user/MS4wLjABAAAAexample'


def video(number):
    return dict(url=f'https://www.douyin.com/video/{number}', title=f'Tiêu đề {number}')


class AppendDialogTests(unittest.TestCase):
    def setUp(self):
        from teodub.app import Studio
        from teodub.campaign_tabs_ui import CampaignArea
        from teodub.campaign_ui import CampaignTab
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name).resolve()
        self.root = tk.Tk()
        self.root.withdraw()
        self.notebook = ttk.Notebook(self.root)
        single = ttk.Frame(self.notebook)
        self.notebook.add(single, text='Video đơn')
        self.studio = Studio(self.root, str(self.data), parent=single)
        self.area = CampaignArea(self.notebook, self.studio)
        self.studio.campaign_area = self.area
        self.tab = CampaignTab(self.area.tabs, self.studio)
        self.notebook.add(self.area, text='Chiến dịch hàng loạt')
        self.studio.batch_controller = CampaignBatchController(self.studio, self.tab)
        self.area.attach(self.tab)
        self.addCleanup(self.cleanup)
        self.windows = []

    def cleanup(self):
        for window in self.windows:
            if window.winfo_exists():
                window.close()
        for owner in (self.tab, self.area):
            for name in ('timer', 'auto_timer'):
                if getattr(owner, name, None) is not None:
                    owner.after_cancel(getattr(owner, name))
        self.studio.process = None
        self.studio.finish_close()

    def channel_link(self, name='Kênh A', numbers=(10, 11, 12)):
        records = [video(number) for number in numbers]
        directory = campaign.create_campaign(self.data, name, records, SETTINGS, str(self.data/'originals'), str(self.data/'outputs'),
                                             dict(channel=CHANNEL, items=records, complete=False, reason='limit', message='x'))
        return self.area.open_link(directory)

    def dialog(self, link):
        link.open_more()
        window = link.more_window
        self.assertIsNotNone(window, link.message.get())
        self.windows.append(window)
        return window

    def read(self, window, items, message='Trang kênh báo đã hết danh sách video công khai.'):
        result = dict(channel=CHANNEL, items=items, complete=True, reason='end_of_public_posts', message=message,
                      skipped_image_posts=0, requested_browser='chrome', browser='chrome')
        with patch('teodub.campaign_append_ui.scan_channel', return_value=result) as scan:
            window.scan()
            self.wait(window)
        return scan

    def wait(self, window):
        deadline = time.monotonic()+10
        while window.scanning and time.monotonic() < deadline:
            self.root.update()
            time.sleep(0.02)
        self.root.update()
        self.assertFalse(window.scanning, 'the reading did not finish')

    def test_only_a_link_made_from_a_channel_can_read_it_again(self):
        plain = campaign.create_campaign(self.data, 'Danh sách', [video(1)], SETTINGS, str(self.data/'originals'), str(self.data/'outputs'))
        link = self.area.open_link(plain)
        self.assertEqual(str(link.more_button['state']), 'disabled')
        link.open_more()
        self.assertIsNone(link.more_window)
        self.assertIn('không tạo từ link kênh', link.message.get())
        channel = self.channel_link()
        self.assertEqual(str(channel.more_button['state']), 'normal')

    def test_the_button_waits_while_a_single_video_or_a_login_window_is_busy(self):
        link = self.channel_link()
        self.studio.process = Mock()
        link.refresh(full=True)
        self.assertEqual(str(link.more_button['state']), 'disabled')
        link.open_more()
        self.assertIsNone(link.more_window)
        self.assertIn('Đợi xong', link.message.get())
        self.studio.process = None
        self.studio.login_pending = True
        link.open_more()
        self.assertIsNone(link.more_window)

    def test_the_dialog_shows_the_channel_and_asks_for_more_than_the_link_has(self):
        link = self.channel_link()
        window = self.dialog(link)
        self.assertEqual(window.channel, CHANNEL)
        self.assertEqual(int(window.limit.get()), 1000)
        self.assertEqual(str(window.add_button['state']), 'disabled')  # Nothing is read yet, nothing can be added.
        from teodub.campaign_append_ui import default_limit
        self.assertEqual((default_limit(100), default_limit(1928), default_limit(9000)), (1000, 3856, 10000))
        link.open_more()  # Pressing again brings the same window forward, never a second one.
        self.assertIs(link.more_window, window)
        self.assertEqual(len([child for child in link.winfo_children() if isinstance(child, tk.Toplevel)]), 1)

    def test_the_reading_goes_through_the_saved_browser_profile_with_the_chosen_limit(self):
        window = self.dialog(self.channel_link())
        window.limit.set('250')
        scan = self.read(window, [video(10)])
        args, kwargs = scan.call_args
        self.assertEqual(args[0], CHANNEL)
        self.assertEqual(Path(args[1]), (self.data/'douyin-channel-browser'/'chrome').resolve())
        self.assertEqual((kwargs['limit'], kwargs['visible'], kwargs['browser']), (250, True, 'chrome'))

    def test_only_videos_no_campaign_has_are_listed_and_counted(self):
        link = self.channel_link(numbers=(10, 11, 12))
        campaign.create_campaign(self.data, 'Link khác', [video(20)], SETTINGS, str(self.data/'originals'), str(self.data/'outputs'))
        window = self.dialog(link)
        self.read(window, [video(n) for n in (30, 20, 12, 31, 11, 32, 10)])
        self.assertEqual(window.summary.get(), 'Đọc được 7 video · 4 đã có trong Tèo · 3 video mới.')
        self.assertEqual([row['url'] for row in window.fresh], [video(n)['url'] for n in (30, 31, 32)])
        shown = [window.tree.item(key, 'values')[0] for key in window.tree.get_children()]
        self.assertEqual(shown, ['Video 4', 'Video 5', 'Video 6'])
        self.assertEqual(str(window.add_button['state']), 'normal')
        self.assertIn('Thêm 3 video mới', window.add_button['text'])
        self.assertEqual(len(campaign.read_json(link.directory/'campaign.json')['items']), 3)  # Reading adds nothing.

    def test_nothing_new_means_nothing_to_add(self):
        window = self.dialog(self.channel_link(numbers=(10, 11)))
        self.read(window, [video(10), video(11)])
        self.assertIn('0 video mới', window.summary.get())
        self.assertIn('Không có gì để thêm', window.summary.get())
        self.assertEqual(str(window.add_button['state']), 'disabled')
        window.add()
        self.assertEqual(len(campaign.read_json(window.directory/'campaign.json')['items']), 2)

    def test_adding_puts_the_new_videos_after_the_old_ones_and_waits_for_chay_when_not_ticked(self):
        link = self.channel_link(numbers=(10, 11, 12))
        before = campaign.read_json(link.directory/'campaign.json')['items']
        window = self.dialog(link)
        self.read(window, [video(n) for n in (40, 41, 10)])
        window.run_after.set(False)
        with patch('teodub.campaign_batch_controller.subprocess.Popen') as spawn:
            window.add()
            spawn.assert_not_called()
        self.assertFalse(window.winfo_exists())
        items = campaign.read_json(link.directory/'campaign.json')['items']
        self.assertEqual(items[:3], before)
        self.assertEqual([(item['number'], item['url']) for item in items[3:]], [(4, video(40)['url']), (5, video(41)['url'])])
        self.assertIn('Đã thêm 2 video mới', link.message.get())
        self.assertIn('Bấm Chạy', link.message.get())
        self.assertEqual(len(link.tree.get_children()), 5)

    def test_adding_with_the_box_ticked_runs_the_link_like_its_chay(self):
        link = self.channel_link()
        window = self.dialog(link)
        self.read(window, [video(40)])
        child = Mock()
        child.poll.return_value = None
        with patch('teodub.campaign_batch_controller.subprocess.Popen', return_value=child) as spawn:
            window.add()
            self.assertIn('--campaign-batch-worker', spawn.call_args.args[0])
        self.assertIn('Đã bắt đầu chạy link này', link.message.get())
        self.assertEqual(len(campaign.read_json(link.directory/'campaign.json')['items']), 4)

    def test_a_refusal_keeps_the_dialog_open_and_says_why(self):
        link = self.channel_link()
        window = self.dialog(link)
        self.read(window, [video(40)])
        worker = Mock()
        worker.poll.return_value = None
        self.tab.process, self.tab.worker_directory = worker, link.directory  # The link runs on its own.
        window.add()
        self.assertTrue(window.winfo_exists())
        self.assertIn('Chưa thêm được', window.status.get())
        self.assertIn('chạy riêng', window.status.get())
        self.assertEqual(len(campaign.read_json(link.directory/'campaign.json')['items']), 3)

    def test_a_reading_that_fails_or_is_stopped_is_told_and_changes_nothing(self):
        link = self.channel_link()
        window = self.dialog(link)
        with patch('teodub.campaign_append_ui.scan_channel', side_effect=ValueError('Chưa đọc được video của kênh.')):
            window.scan()
            self.wait(window)
        self.assertEqual(window.status.get(), 'Chưa lấy được danh sách: Chưa đọc được video của kênh.')
        self.assertEqual(str(window.add_button['state']), 'disabled')
        self.assertEqual(str(window.scan_button['state']), 'normal')
        started, release = threading.Event(), threading.Event()

        def slow(*args, cancel=None, **kwargs):
            started.set()
            release.wait(5)
            from teodub.common import check_cancel
            check_cancel(cancel)
        with patch('teodub.campaign_append_ui.scan_channel', side_effect=slow):
            window.scan()
            self.assertTrue(started.wait(5))
            self.assertEqual(str(window.stop_button['state']), 'normal')
            window.stop()
            release.set()
            self.wait(window)
        self.assertEqual(window.status.get(), 'Đã dừng đọc kênh.')
        self.assertEqual(len(campaign.read_json(link.directory/'campaign.json')['items']), 3)

    def test_the_limit_box_and_a_busy_machine_are_checked_before_reading(self):
        window = self.dialog(self.channel_link())
        with patch('teodub.campaign_append_ui.scan_channel') as scan:
            for text, said in (('abc', 'phải là một số'), ('0', 'Chọn từ 1 đến 10.000'), ('10001', 'Chọn từ 1 đến 10.000')):
                window.limit.set(text)
                window.scan()
                self.assertIn(said, window.status.get())
            window.limit.set('100')
            self.tab.scanning = True  # The other tab is reading a channel.
            window.scan()
            self.assertIn('Đợi xong', window.status.get())
            scan.assert_not_called()

    def test_closing_the_dialog_stops_the_reading_and_closing_the_tab_closes_the_dialog(self):
        link = self.channel_link()
        window = self.dialog(link)
        window.close()
        self.assertTrue(window.cancel.is_set())
        self.assertFalse(window.winfo_exists())
        again = self.dialog(link)
        link.close()
        self.assertFalse(again.winfo_exists())


if __name__ == '__main__':
    unittest.main()
