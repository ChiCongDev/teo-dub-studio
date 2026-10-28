"""Hidden Tk checks: the single auto-mode checkbox on the Page panel and the auto-approved list in Duyệt caption."""
import time
import tkinter as tk
from tkinter import ttk
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from tests import test_page_ai as ai_fixtures
from tests import test_page_ai_auto as auto_fixtures
from tests.test_page_ai_hidden import texts
from teodub.page_ai_ui import PageAIPanel
from teodub.page_schedule import PageScheduleStore
from teodub.plan_review_ui import PlanReviewTab

CHECKBOX = 'Tự động từ chiến dịch: video lồng lồng tiếng xong → AI viết caption → tự duyệt → tự đăng'.replace('lồng lồng', 'lồng')


class AutoPanelTests(unittest.TestCase):
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

    def test_the_checkbox_is_visible_and_off_by_default(self):
        self.assertIn(CHECKBOX, texts(self.panel))
        self.assertFalse(self.panel.auto_campaign.get())
        self.panel.save()
        self.assertNotIn('auto_campaign', self.saved_config())
        self.assertFalse(self.store.policy(self.account, self.page)['enabled'])

    def test_ticking_it_saves_the_whole_auto_setup_from_the_visible_fields(self):
        self.panel.videos.set(3)
        self.panel.slot_start.set('11:20')
        self.panel.slot_gap.set('45')
        self.panel.auto_campaign.set(True)
        self.assertIn('11:20, 12:05, 12:50', self.panel.slots_summary.get())
        self.panel.save()
        config = self.saved_config()
        self.assertIs(config['auto_campaign'], True)
        self.assertIs(config['auto_publish'], True)
        self.assertEqual((config['slot_start'], config['slot_gap'], config['videos_per_day'], config['texts_per_week']),
                         ('11:20', 45, 3, 0))
        self.assertFalse(self.store.policy(self.account, self.page)['enabled'])
        self.assertEqual([p['config']['auto_campaign'] for p in self.store.auto_policies()], [True])

    def test_the_daily_number_has_no_ceiling_in_the_screen(self):
        self.panel.videos.set(40)
        self.panel.slot_start.set('06:00')
        self.panel.slot_gap.set('30')
        self.panel.auto_campaign.set(True)
        self.panel.save()
        self.assertEqual(self.saved_config()['videos_per_day'], 40)
        self.assertIn('mỗi ngày tối đa 40 bài', self.panel.slots_summary.get())

    def test_a_short_gap_is_accepted_in_auto_mode(self):
        self.panel.videos.set(4)
        self.panel.slot_start.set('10:00')
        self.panel.slot_gap.set('5')
        self.panel.auto_campaign.set(True)
        self.assertIn('10:00, 10:05, 10:10, 10:15', self.panel.slots_summary.get())
        self.assertIn('đăng bù theo nhịp 5 phút tới hết ngày', self.panel.slots_summary.get())
        self.panel.save()
        self.assertEqual(self.saved_config()['slot_gap'], 5)

    def test_invalid_times_are_reported_and_nothing_is_saved(self):
        self.panel.auto_campaign.set(True)
        for start, gap in (('9h', '60'), ('12:00', 'abc'), ('12:00', '0'), ('12:00', '2000')):
            with self.subTest(start=start, gap=gap):
                self.panel.slot_start.set(start)
                self.panel.slot_gap.set(gap)
                self.assertIn('chưa hợp lệ', self.panel.slots_summary.get())
                self.panel.save()
                self.assertIsNone(self.store.policy(self.account, self.page))

    def test_unticking_and_saving_turns_auto_mode_and_auto_publish_off_but_keeps_the_rest(self):
        self.panel.slot_start.set('11:20')
        self.panel.slot_gap.set('45')
        self.panel.auto_campaign.set(True)
        self.panel.save()
        self.panel.destroy()
        self.panel = PageAIPanel(ttk.Notebook(self.window), self.manager, history_parent=ttk.Frame(self.window))
        self.assertTrue(self.panel.auto_campaign.get())
        self.panel.auto_campaign.set(False)
        self.panel.save()
        config = self.saved_config()
        self.assertFalse(config.get('auto_campaign', False))
        self.assertFalse(config['auto_publish'])
        self.assertEqual(config['campaigns'], [self.campaign])
        self.assertEqual(self.store.auto_policies(), [])


class AutoApprovedListTests(unittest.TestCase):
    setUp = auto_fixtures.AutoControllerTests.setUp
    saved = auto_fixtures.AutoControllerTests.saved
    add_video = auto_fixtures.AutoControllerTests.add_video
    start = auto_fixtures.AutoControllerTests.start
    cycle = auto_fixtures.AutoControllerTests.cycle
    schedules = auto_fixtures.AutoControllerTests.schedules
    runs = auto_fixtures.AutoControllerTests.runs

    def open_tab(self):
        try:
            self.window = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f'Tk unavailable: {exc}')
        self.window.withdraw()
        self.addCleanup(self.window.destroy)
        self.tab = PlanReviewTab(self.window, SimpleNamespace(data_dir=self.root), lambda: self.scheduler,
                                 store=self.store.plans, spawn=lambda work: work())

    def test_auto_approved_posts_are_listed_with_caption_time_and_state(self):
        self.start()
        self.cycle()
        self.open_tab()
        ids = self.tab.auto_tree.get_children()
        self.assertEqual(len(ids), 1)
        values = self.tab.auto_tree.item(ids[0], 'values')
        self.assertEqual(values[0], 'Synthetic Page')
        self.assertIn('Caption cho Mẹo câu cá', values[2])
        self.assertEqual(values[3], 'Đang chờ giờ đăng')
        self.assertEqual(self.tab.tree.get_children(), ())

    def test_pausing_a_selected_auto_post_stops_it_and_keeps_the_history(self):
        self.start()
        self.cycle()
        self.open_tab()
        ident = self.tab.auto_tree.get_children()[0]
        self.tab.auto_tree.selection_set(ident)
        self.tab.pause_auto()
        self.assertEqual([r['state'] for r in self.schedules()], ['paused'])
        self.assertEqual(len(self.tab.auto_tree.get_children()), 1)
        self.assertEqual(self.tab.auto_tree.item(ident, 'values')[3], 'Đã tạm dừng')

    def test_pausing_without_a_selection_does_nothing(self):
        self.start()
        self.cycle()
        self.open_tab()
        self.tab.pause_auto()
        self.assertEqual([r['state'] for r in self.schedules()], ['armed'])
        self.assertIn('Chọn', self.tab.status.get())


class SaveNoticeTests(unittest.TestCase):
    """28/10/2026: the owner typed 9:30, pressed Lưu, saw no refusal (the 2-second refresh wiped it) and the old
    setup stayed in force."""
    def setUp(self):
        AutoPanelTests.setUp(self)
        self.panel.videos.set(10)
        self.panel.slot_gap.set('10')
        self.panel.auto_campaign.set(True)

    saved_config = AutoPanelTests.saved_config

    def test_short_hour_is_saved_as_two_digits_and_the_box_follows(self):
        for typed, saved in (('9:30', '09:30'), ('9:5', '09:05'), ('09:30', '09:30'), (' 6:00 ', '06:00')):
            with self.subTest(typed=typed):
                self.panel.slot_start.set(typed)
                self.panel.save()
                self.assertEqual(self.saved_config()['slot_start'], saved)
                self.assertEqual(self.panel.slot_start.get(), saved)
                self.assertTrue(self.panel.status.get().startswith('Đã lưu thiết lập lúc '))

    def test_a_wrong_hour_is_still_refused_and_the_old_setup_stays(self):
        self.panel.slot_start.set('10:00')
        self.panel.save()
        for typed in ('25:00', '9:75', 'abc', '', '930'):
            with self.subTest(typed=typed):
                self.panel.slot_start.set(typed)
                self.panel.save()
                self.assertEqual(self.saved_config()['slot_start'], '10:00')
                self.assertEqual(self.panel.slot_start.get(), typed)
                self.assertTrue(self.panel.status.get().startswith('Chưa lưu: '))
                self.assertIn('HH:MM', self.panel.status.get())

    def test_the_notice_survives_the_two_second_refresh_and_the_next_save_replaces_it(self):
        self.panel.slot_start.set('25:00')
        self.panel.save()
        refused = self.panel.status.get()
        self.assertTrue(refused.startswith('Chưa lưu: '))
        for _ in range(3):
            self.panel.refresh()  # What poll() does every 2 seconds.
            self.assertTrue(self.panel.status.get().startswith(refused.split('\n')[0]))
        self.panel.slot_start.set('9:30')
        self.panel.save()
        self.assertTrue(self.panel.status.get().startswith('Đã lưu thiết lập lúc '))
        self.panel.refresh()
        self.assertNotIn('Chưa lưu', self.panel.status.get())


if __name__ == '__main__':
    unittest.main()
