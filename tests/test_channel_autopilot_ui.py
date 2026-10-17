"""Hidden Tk checks for the channel source tab; synthetic scans and campaigns only."""
from pathlib import Path
import tempfile
import tkinter as tk
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from tests.test_channel_autopilot import CHANNEL, SETTINGS, FakeBatch, video
from teodub.channel_autopilot import ChannelAutopilot
from teodub.channel_autopilot_ui import ChannelAutopilotTab
from teodub.common import atomic_json
from teodub.facebook_accounts import FacebookAccounts


class ChannelAutopilotTabTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f'Tk unavailable: {exc}')
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)
        accounts = FacebookAccounts(self.data)
        account = accounts.add_account('Synthetic FB')['id']
        accounts.save_page(account, 'Page A', 'facebook.com/profile.php?id=100', '100')
        accounts.save_page(account, 'Page B', 'facebook.com/profile.php?id=200', '200')
        self.studio = SimpleNamespace(data_dir=self.data, process=None, campaign_busy=False, login_pending=False,
            voice_learning_busy=False, closing=False, closed=False, restart_pending=False, restart_draining=False)
        self.campaign_tab = SimpleNamespace(scanning=False, settings=SETTINGS, settings_error='', capture_settings=Mock(),
            original_dir=tk.StringVar(self.root, str(self.data/'goc')), output_dir=tk.StringVar(self.root, str(self.data/'out')),
            selected_browser=lambda: 'chrome')
        self.batch = FakeBatch(self.data)
        pilot = ChannelAutopilot(self.studio, self.campaign_tab, self.batch, scan=self.scan, create=self.create,
                                 spawn=lambda work: work())
        self.tab = ChannelAutopilotTab(self.root, self.studio, self.campaign_tab, self.batch, pilot=pilot)

    def scan(self, text, profile_dir, **options):
        return dict(channel=text, items=[video(1), video(2)], complete=False, reason='no_new_posts', message='')

    def create(self, data_dir, name, records, settings, original_dir, output_dir, discovery=None, **options):
        ident = (records[0]['url'][-8:]+name.encode().hex())[:32].ljust(32, '0')
        ident = ''.join(c if c in '0123456789abcdef' else '0' for c in ident)
        directory = Path(data_dir)/'campaigns'/ident
        atomic_json(directory/'campaign.json', dict(id=ident, name=name, status='ready',
            items=[dict(number=1, url=records[0]['url'], title='', status='pending', outputs={})]))
        return directory

    def add_source(self):
        self.tab.link.set(CHANNEL)
        self.tab.page_list.selection_set(0, 1)
        self.tab.save()

    def test_pages_from_every_account_are_listed_and_selection_is_required(self):
        self.assertEqual(self.tab.page_list.size(), 2)
        self.assertIn('Page A (100)', self.tab.page_list.get(0))
        self.tab.link.set(CHANNEL)
        self.tab.save()
        self.assertIn('Chọn ít nhất một Page', self.tab.status.get())
        self.assertEqual(self.tab.tree.get_children(), ())

    def test_saving_creates_a_running_source_with_frozen_video_settings(self):
        self.add_source()
        self.campaign_tab.capture_settings.assert_called_once_with()
        rows = self.tab.tree.get_children()
        self.assertEqual(len(rows), 1)
        values = self.tab.tree.item(rows[0], 'values')
        self.assertEqual((values[1], values[2]), ('Page A, Page B', 'Đang chạy'))
        self.assertEqual(self.tab.link.get(), '')
        self.assertEqual(self.tab.pilot.store.get(rows[0])['settings'], SETTINGS)

    def test_invalid_single_video_settings_block_saving(self):
        self.campaign_tab.settings_error = 'Chọn giọng đọc.'
        self.add_source()
        self.assertIn('Chọn giọng đọc.', self.tab.status.get())
        self.assertEqual(self.tab.tree.get_children(), ())

    def test_timer_scans_and_shows_the_result(self):
        self.add_source()
        self.tab.poll()
        self.tab.poll()
        row = self.tab.tree.get_children()[0]
        self.assertIn('Đã lấy 2 video mới', self.tab.tree.item(row, 'values')[5])

    def test_blocked_processing_banner_appears_and_resume_clears_it(self):
        self.tab.pilot.store.set_meta('processing_blocked', 'Hàng đợi xử lý của Nguồn kênh đã dừng: hết hạn mức.')
        self.tab.refresh()
        self.assertEqual(self.tab.banner.winfo_manager(), 'grid')
        self.tab.resume_processing()
        self.assertEqual(self.tab.banner.winfo_manager(), '')
        self.assertEqual(self.tab.pilot.processing_blocked(), '')

    def test_removing_requires_confirmation_and_keeps_history(self):
        self.add_source()
        row = self.tab.tree.get_children()[0]
        self.tab.tree.selection_set(row)
        with patch('teodub.channel_autopilot_ui.messagebox.askyesno', return_value=False):
            self.tab.remove()
        self.assertEqual(self.tab.tree.get_children(), (row,))
        with patch('teodub.channel_autopilot_ui.messagebox.askyesno', return_value=True):
            self.tab.remove()
        self.assertEqual(self.tab.tree.get_children(), ())
        self.assertEqual(self.tab.pilot.store.get(row)['state'], 'removed')


if __name__ == '__main__':
    unittest.main()
