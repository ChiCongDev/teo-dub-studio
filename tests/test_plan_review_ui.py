"""Hidden Tk checks for the cross-Page caption review tab over real plan/schedule stores."""
import time
import tkinter as tk
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tests import test_plan_review as review_fixtures
from teodub.page_schedule import PageScheduleStore
from teodub.plan_review_ui import PlanReviewTab, local_text


class PlanReviewTabTests(unittest.TestCase):
    item = review_fixtures.PlanReviewTests.item
    three_item_plan = review_fixtures.PlanReviewTests.three_item_plan
    document = review_fixtures.plan_fixtures.ContentPlanTests.document
    snapshot = review_fixtures.plan_fixtures.ContentPlanTests.snapshot
    imported = review_fixtures.plan_fixtures.ContentPlanTests.imported
    video = review_fixtures.plan_fixtures.ContentPlanTests.video

    def setUp(self):
        review_fixtures.plan_fixtures.ContentPlanTests.setUp(self)
        try:
            self.window = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f'Tk unavailable: {exc}')
        self.window.withdraw()
        self.addCleanup(self.window.destroy)
        self.plan = self.three_item_plan()
        self.tab = PlanReviewTab(self.window, SimpleNamespace(data_dir=self.root_dir()), lambda: self.controller,
                                 store=self.store, spawn=lambda work: work())

    def root_dir(self):
        return self.store.data_dir

    def ident(self, item_id):
        return self.plan['record_id']+':'+item_id

    def pick(self, *item_ids):
        self.tab.tree.selection_set([self.ident(i) for i in item_ids])
        self.tab.on_select()

    def schedules(self):
        return PageScheduleStore(self.store.data_dir).list(self.account, self.page)

    def test_rows_show_every_waiting_item_and_editor_loads_selection(self):
        self.assertEqual(len(self.tab.tree.get_children()), 3)
        self.assertEqual(self.tab.counts.get(), '3 bài chờ duyệt')
        self.pick('v1')
        self.assertEqual(self.tab.caption.get('1.0', 'end-1c'), 'Caption AI cho v1 #cauca')
        self.assertIn('Synthetic campaign', self.tab.video.get())
        self.assertEqual(self.tab.when.get(), local_text(self.tab.rows[self.ident('v1')]['due_at']))

    def test_saving_an_edit_keeps_it_waiting_and_shows_the_ai_original(self):
        self.pick('v1')
        self.tab.caption.delete('1.0', 'end')
        self.tab.caption.insert('1.0', 'Hụt cá mà vẫn cười 😅')
        self.assertTrue(self.tab.save_edit())
        row = self.tab.rows[self.ident('v1')]
        self.assertEqual((row['body'], row['edited']), ('Hụt cá mà vẫn cười 😅', True))
        self.assertIn('Bản AI gốc', self.tab.original.get())
        self.assertEqual(self.tab.tree.item(self.ident('v1'), 'values')[4], 'Đã sửa · chờ duyệt')
        self.assertEqual(self.schedules(), [])

    def test_switching_items_saves_a_pending_edit_or_stays_on_invalid_text(self):
        self.pick('v1')
        self.tab.caption.delete('1.0', 'end')
        self.tab.caption.insert('1.0', 'Sửa rồi chuyển bài')
        self.pick('v2')
        self.assertEqual(self.tab.current, self.ident('v2'))
        self.assertEqual(self.tab.rows[self.ident('v1')]['body'], 'Sửa rồi chuyển bài')
        self.tab.caption.delete('1.0', 'end')
        self.pick('t1')
        self.assertEqual(self.tab.current, self.ident('v2'))
        self.assertIn('Chưa lưu sửa', self.tab.status.get())

    def test_approval_requires_confirmation_then_arms_only_selected(self):
        self.pick('v1', 't1')
        with patch('teodub.plan_review_ui.messagebox.askyesno', return_value=False):
            self.tab.approve()
        self.assertEqual(self.schedules(), [])
        with patch('teodub.plan_review_ui.messagebox.askyesno', return_value=True) as ask:
            self.tab.approve()
        self.assertIn('Bật lịch 2 bài', ask.call_args.args[1])
        self.tab.poll()
        self.assertEqual(sorted(r['state'] for r in self.schedules()), ['armed', 'armed'])
        self.assertIn('Đã bật lịch 2 bài', self.tab.status.get())
        self.assertEqual(self.tab.tree.get_children(), (self.ident('v2'),))

    def test_rejecting_removes_items_without_scheduling(self):
        self.pick('v2', 't1')
        with patch('teodub.plan_review_ui.messagebox.askyesno', return_value=True):
            self.tab.reject()
        self.assertEqual(self.tab.tree.get_children(), (self.ident('v1'),))
        self.assertEqual(self.schedules(), [])
        self.assertEqual(self.store.reserved_video_keys(self.account, self.page), {self.keys[0]})

    def test_expired_item_cannot_be_approved(self):
        later = time.time()+3*86400
        with patch('teodub.page_content_plan.time.time', return_value=later):
            self.tab.refresh()
        self.assertEqual(self.tab.tree.item(self.ident('v1'), 'values')[4], 'Quá giờ')
        self.pick('v1')
        with patch('teodub.plan_review_ui.messagebox.askyesno', return_value=True) as ask, \
             patch('teodub.page_content_plan.time.time', return_value=later):
            self.tab.approve()
        ask.assert_not_called()
        self.assertIn('quá giờ', self.tab.status.get())
        self.assertEqual(self.schedules(), [])


if __name__ == '__main__':
    unittest.main()
