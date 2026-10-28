"""Hidden Tk checks: the "Đặt giờ riêng cho từng video" box next to Video mỗi ngày (28/10/2026)."""
import tkinter as tk
from tkinter import ttk
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from tests import test_page_ai as ai_fixtures
from tests.test_page_ai_hidden import texts
from teodub.page_ai_ui import PageAIPanel

LABEL = 'Đặt giờ riêng cho từng video'


class CustomTimesPanelTests(unittest.TestCase):
    def setUp(self):
        ai_fixtures.PageAITests.setUp(self)
        try:
            self.window = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f'Tk unavailable: {exc}')
        self.window.withdraw()
        self.addCleanup(self.window.destroy)
        self.manager = SimpleNamespace(owner=self.owner, account_id=self.account, page_key=self.page,
                                       open_strategy_settings=Mock())
        self.panel = PageAIPanel(ttk.Notebook(self.window), self.manager, history_parent=ttk.Frame(self.window))
        self.panel.set_sources([self.campaign])

    def saved_config(self):
        return self.store.policy(self.account, self.page)['config']

    def test_the_box_is_visible_and_off_by_default_and_the_old_rhythm_fields_stay_enabled(self):
        self.assertIn(LABEL, texts(self.panel))
        self.assertFalse(self.panel.custom_times_on.get())
        self.assertEqual(str(self.panel.slot_start_entry['state']), 'normal')
        self.assertEqual(str(self.panel.custom_times_entry['state']), 'disabled')

    def test_unticked_behaves_exactly_like_before_the_feature(self):
        self.panel.videos.set(3)
        self.panel.slot_start.set('11:20')
        self.panel.slot_gap.set('45')
        self.panel.auto_campaign.set(True)
        self.panel.save()
        config = self.saved_config()
        self.assertNotIn('slot_times', config)
        self.assertEqual((config['slot_start'], config['slot_gap']), ('11:20', 45))

    def test_ticking_disables_the_rhythm_fields_and_enables_the_times_box(self):
        self.panel.custom_times_on.set(True)
        self.panel.toggle_custom_times()
        self.assertEqual(str(self.panel.slot_start_entry['state']), 'disabled')
        self.assertEqual(str(self.panel.slot_gap_entry['state']), 'disabled')
        self.assertEqual(str(self.panel.custom_times_entry['state']), 'normal')
        self.panel.custom_times_on.set(False)
        self.panel.toggle_custom_times()
        self.assertEqual(str(self.panel.slot_start_entry['state']), 'normal')
        self.assertEqual(str(self.panel.custom_times_entry['state']), 'disabled')

    def test_saving_with_custom_times_stores_and_sorts_them_and_reloads(self):
        self.panel.videos.set(3)
        self.panel.slot_times.set('20:15, 8:00, 12:30')
        self.panel.custom_times_on.set(True)
        self.assertIn('08:00, 12:30, 20:15', self.panel.slots_summary.get())
        self.panel.save()
        config = self.saved_config()
        self.assertEqual(config['slot_times'], ['08:00', '12:30', '20:15'])
        self.assertNotIn('slot_start', config)
        self.assertEqual(self.panel.slot_times.get(), '08:00, 12:30, 20:15')
        self.panel.destroy()
        self.panel = PageAIPanel(ttk.Notebook(self.window), self.manager, history_parent=ttk.Frame(self.window))
        self.assertTrue(self.panel.custom_times_on.get())
        self.assertEqual(self.panel.slot_times.get(), '08:00, 12:30, 20:15')
        self.assertEqual(str(self.panel.slot_start_entry['state']), 'disabled')

    def test_auto_mode_description_mentions_the_fifteen_minute_catch_up(self):
        self.panel.videos.set(2)
        self.panel.slot_times.set('08:00, 20:00')
        self.panel.custom_times_on.set(True)
        self.panel.auto_campaign.set(True)
        summary = self.panel.slots_summary.get()
        self.assertIn('15 phút', summary)
        self.assertIn('08:00, 20:00', summary)

    def test_a_count_mismatch_is_reported_and_nothing_is_saved(self):
        self.panel.videos.set(3)
        self.panel.slot_times.set('08:00, 12:30')
        self.panel.custom_times_on.set(True)
        self.assertIn('đúng số Video', self.panel.slots_summary.get())
        self.panel.save()
        self.assertIsNone(self.store.policy(self.account, self.page))

    def test_duplicate_times_are_reported_and_nothing_is_saved(self):
        self.panel.videos.set(2)
        self.panel.slot_times.set('08:00, 08:00')
        self.panel.custom_times_on.set(True)
        self.assertIn('không được trùng', self.panel.slots_summary.get())
        self.panel.save()
        self.assertIsNone(self.store.policy(self.account, self.page))

    def test_a_malformed_time_is_reported_and_nothing_is_saved(self):
        self.panel.videos.set(1)
        self.panel.slot_times.set('8h00')
        self.panel.custom_times_on.set(True)
        self.assertIn('HH:MM', self.panel.slots_summary.get())
        self.panel.save()
        self.assertIsNone(self.store.policy(self.account, self.page))

    def test_unticking_and_saving_restores_the_rhythm_and_drops_the_custom_times(self):
        self.panel.videos.set(2)
        self.panel.slot_times.set('08:00, 20:00')
        self.panel.custom_times_on.set(True)
        self.panel.save()
        self.panel.custom_times_on.set(False)
        self.panel.slot_start.set('11:20')
        self.panel.slot_gap.set('45')
        self.panel.auto_campaign.set(True)
        self.panel.save()
        config = self.saved_config()
        self.assertNotIn('slot_times', config)
        self.assertEqual((config['slot_start'], config['slot_gap']), ('11:20', 45))


if __name__ == '__main__':
    unittest.main()
