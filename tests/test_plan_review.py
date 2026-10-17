"""Cross-Page caption review over real plan/schedule stores; synthetic files, no AI or Facebook."""
from datetime import datetime, timedelta, timezone
import json
import time
import unittest
from unittest.mock import patch
import uuid

from tests import test_page_ai as ai_fixtures
from tests import test_page_content_plan as plan_fixtures
from teodub.page_schedule import PageScheduleStore

LOCAL = timezone(timedelta(hours=7))


class PlanReviewTests(unittest.TestCase):
    setUp = plan_fixtures.ContentPlanTests.setUp
    document = plan_fixtures.ContentPlanTests.document
    snapshot = plan_fixtures.ContentPlanTests.snapshot
    imported = plan_fixtures.ContentPlanTests.imported
    video = plan_fixtures.ContentPlanTests.video

    def item(self, ident, *, kind='video', video_key='', hours=24, body=None):
        date = (datetime.now(LOCAL)+timedelta(hours=hours)).replace(microsecond=0)
        return dict(id=ident, kind=kind, due_at=date.isoformat(), video_key=video_key, goal='Giải trí',
                    body=body or f'Caption AI cho {ident} #cauca')

    def three_item_plan(self):
        key1, _ = self.video(1, 'a'*32)
        key2, self.video2 = self.video(1, 'b'*32)
        self.keys = key1, key2
        return self.imported(self.document([self.item('v1', video_key=key1, hours=24), self.item('v2', video_key=key2, hours=25),
                                            self.item('t1', kind='text', hours=26)]))

    def queue(self, **options):
        return {row['item_id']: row for row in self.store.review_queue(**options)}

    def expected(self, *ids):
        rows = self.queue()
        return {ident: dict(body=rows[ident]['body'], due_at=rows[ident]['due_at']) for ident in ids}

    def approve(self, plan, *ids):
        revision = self.store.stage_items(plan['record_id'], self.expected(*ids))
        return self.store.arm_items(revision, self.controller)

    def schedules(self):
        return PageScheduleStore(self.root).list(self.account, self.page)

    def test_queue_lists_unreviewed_items_of_every_page(self):
        plan = self.three_item_plan()
        other = self.registry.save_page(self.account, 'Page câu đêm', 'facebook.com/profile.php?id=200', '200')['id']
        other_thread = str(uuid.uuid4())
        self.store.save_connection(self.account, other, other_thread, 'Chat 2')
        page, thread = self.page, self.thread
        self.page, self.thread = other, other_thread
        document = self.document([self.item('x1', kind='text')])
        document['page_id'] = '200'
        self.imported(document)
        self.page, self.thread = page, thread
        rows = self.store.review_queue()
        self.assertEqual(sorted((r['page_name'], r['item_id']) for r in rows),
                         [('Page câu đêm', 'x1'), ('Synthetic Page', 't1'), ('Synthetic Page', 'v1'), ('Synthetic Page', 'v2')])
        row = self.queue()['v1']
        self.assertEqual((row['record_id'], row['kind'], row['edited'], row['expired'], row['decision']),
                         (plan['record_id'], 'video', False, False, ''))
        self.assertTrue(row['video_path'].endswith('Video 1.mp4'))
        self.assertIn('Synthetic campaign', row['video_label'])
        only = self.store.review_queue(pages=[(self.account, other)])
        self.assertEqual([r['item_id'] for r in only], ['x1'])

    def test_edit_keeps_ai_original_and_saving_original_text_restores_it(self):
        plan = self.three_item_plan()
        due = self.queue()['v1']['due_at']+3600
        self.store.edit_item(plan['record_id'], 'v1', body='  Hụt cá vẫn cười 😅  ', due_at=due)
        row = self.queue()['v1']
        self.assertEqual((row['body'], row['due_at'], row['edited']), ('Hụt cá vẫn cười 😅', due, True))
        self.assertEqual(row['original_body'], 'Caption AI cho v1 #cauca')
        saved = self.store.get_plan(plan['plan_id'], self.account, self.page)
        self.assertEqual((saved['digest'], saved['items'][0]['body']), (plan['digest'], 'Caption AI cho v1 #cauca'))
        self.store.edit_item(plan['record_id'], 'v1', body=row['original_body'], due_at=row['original_due_at'])
        self.assertFalse(self.queue()['v1']['edited'])

    def test_edit_rejects_empty_text_past_time_and_unknown_item(self):
        plan = self.three_item_plan()
        due = self.queue()['v1']['due_at']
        for body, when in (('   ', due), ('Hợp lệ', time.time()-60), ('x'*6001, due)):
            with self.subTest(body=body[:10], when=when):
                with self.assertRaises(ValueError):
                    self.store.edit_item(plan['record_id'], 'v1', body=body, due_at=when)
        with self.assertRaises(ValueError):
            self.store.edit_item(plan['record_id'], 'nope', body='Hợp lệ', due_at=due)
        self.assertFalse(self.queue()['v1']['edited'])

    def test_approving_selected_items_arms_only_them_with_edited_caption(self):
        plan = self.three_item_plan()
        row = self.queue()['v1']
        self.store.edit_item(plan['record_id'], 'v1', body='Câu ngắn đã sửa', due_at=row['due_at'])
        approved = self.approve(plan, 'v1', 't1')
        self.assertTrue(approved['approved_at'])
        self.assertEqual([i['id'] for i in approved['items']], ['v1', 't1'])
        self.assertEqual(approved['document']['review_of'], plan['record_id'])
        rows = self.schedules()
        self.assertEqual(sorted(r['state'] for r in rows), ['armed', 'armed'])
        video_row = next(r for r in rows if r['source_key'] == self.keys[0])
        self.assertEqual(video_row['body'], 'Câu ngắn đã sửa')
        self.assertFalse(any(r['source_key'] == self.keys[1] for r in rows))
        self.assertEqual(list(self.queue()), ['v2'])
        original = self.store.get_plan(plan['plan_id'], self.account, self.page)
        self.assertEqual((original['state'], original['digest']), ('proposed', plan['digest']))

    def test_rejected_video_returns_to_ai_inventory_and_approved_stays_reserved(self):
        plan = self.three_item_plan()
        self.assertEqual(self.store.reserved_video_keys(self.account, self.page), set(self.keys))
        self.store.reject_items(plan['record_id'], ['v2'])
        self.assertEqual(self.store.reserved_video_keys(self.account, self.page), {self.keys[0]})
        self.approve(plan, 'v1')
        self.assertEqual(self.store.reserved_video_keys(self.account, self.page), {self.keys[0]})
        self.assertNotIn('v2', self.queue())

    def test_fully_decided_plan_is_reviewed_and_old_whole_plan_approval_is_blocked(self):
        plan = self.three_item_plan()
        self.store.edit_item(plan['record_id'], 'v2', body='Đã sửa', due_at=self.queue()['v2']['due_at'])
        for action in (lambda: self.store.stage(plan['plan_id'], self.account, self.page, plan['digest']),
                       lambda: self.store.approve(plan['plan_id'], self.account, self.page, self.controller, plan['digest'])):
            with self.assertRaisesRegex(ValueError, 'Duyệt caption'):
                action()
        self.approve(plan, 'v1')
        self.store.reject_items(plan['record_id'], ['v2', 't1'])
        original = self.store.get_plan(plan['plan_id'], self.account, self.page)
        self.assertEqual(original['state'], 'reviewed')
        self.assertIn('1', original['message'])
        self.assertEqual(self.queue(), {})
        self.assertEqual(len(self.schedules()), 1)

    def test_changed_view_expired_time_or_spacing_refuses_without_writes(self):
        plan = self.three_item_plan()
        stale = self.expected('v1')
        self.store.edit_item(plan['record_id'], 'v1', body='Sửa ở cửa sổ khác', due_at=stale['v1']['due_at'])
        with self.assertRaisesRegex(ValueError, 'làm mới'):
            self.store.stage_items(plan['record_id'], stale)
        due = self.queue()['v2']['due_at']
        self.store.edit_item(plan['record_id'], 't1', body='Bài chữ', due_at=due+600)
        with self.assertRaisesRegex(ValueError, '30 phút'):
            self.store.stage_items(plan['record_id'], self.expected('v2', 't1'))
        future = time.time()+3*86400
        with patch('teodub.page_content_plan.time.time', return_value=future):
            self.assertTrue(self.queue()['v1']['expired'])
            with self.assertRaisesRegex(ValueError, 'quá giờ'):
                self.store.stage_items(plan['record_id'], self.expected('v1'))
        self.assertEqual(self.schedules(), [])
        self.assertEqual(len(self.store.plans(self.account, self.page)), 1)

    def test_spacing_also_counts_schedules_of_earlier_approvals(self):
        plan = self.three_item_plan()
        self.approve(plan, 'v1')
        due = self.queue()['v2']['due_at']
        self.store.edit_item(plan['record_id'], 'v2', body='Gần giờ bài trước', due_at=self.schedules()[0]['due_at']+900)
        with self.assertRaisesRegex(ValueError, '30 phút'):
            self.store.stage_items(plan['record_id'], self.expected('v2'))
        self.store.edit_item(plan['record_id'], 'v2', body='Gần giờ bài trước', due_at=due)
        self.approve(plan, 'v2')
        self.assertEqual(len(self.schedules()), 2)

    def test_failed_staging_needs_checking_and_cannot_be_approved_twice(self):
        plan = self.three_item_plan()
        self.video2.write_bytes(b'changed after the AI proposal')
        with self.assertRaises(ValueError):
            self.store.stage_items(plan['record_id'], self.expected('v2'))
        row = self.queue()['v2']
        self.assertEqual(row['decision'], 'failed')
        self.assertTrue(row['message'])
        with self.assertRaises(ValueError):
            self.store.stage_items(plan['record_id'], {'v2': dict(body=row['body'], due_at=row['due_at'])})
        self.assertFalse(any(r['state'] == 'armed' for r in self.schedules()))
        self.store.reject_items(plan['record_id'], ['v2'])
        self.assertNotIn('v2', self.queue())
        self.assertIn(self.keys[1], self.store.reserved_video_keys(self.account, self.page))
        self.assertFalse(any(r['state'] == 'armed' for r in self.schedules()))

    def test_unexpected_staging_error_is_reported_not_silently_approved(self):
        plan = self.three_item_plan()
        with patch.object(type(self.store), 'stage', side_effect=OSError('Ổ đĩa không đọc được')):
            with self.assertRaisesRegex(ValueError, 'Ổ đĩa'):
                self.store.stage_items(plan['record_id'], self.expected('v1'))
        row = self.queue()['v1']
        self.assertEqual(row['decision'], 'failed')
        self.assertIn('Ổ đĩa', row['message'])

    def test_unreviewed_items_never_get_a_schedule(self):
        plan = self.three_item_plan()
        self.store.edit_item(plan['record_id'], 'v2', body='Chỉ sửa, chưa duyệt', due_at=self.queue()['v2']['due_at'])
        self.store.reject_items(plan['record_id'], ['t1'])
        self.assertEqual(self.schedules(), [])
        self.approve(plan, 'v1')
        self.assertEqual([r['source_key'] for r in self.schedules()], [self.keys[0]])

    def test_generated_plan_on_page_without_strategy_chat_is_reviewable(self):
        self.store.disconnect(self.account, self.page)
        key, _ = self.video(1, 'c'*32)
        document = self.document([self.item('v1', video_key=key)])
        result = self.store.import_generated(self.account, self.page, document, run_id=uuid.uuid4().hex, thread_id='')
        self.assertFalse(result['error'], result['status'])
        self.assertEqual(result['plan']['state'], 'proposed')
        self.approve(result['plan'], 'v1')
        self.assertEqual([r['state'] for r in self.schedules()], ['armed'])
        refused = self.store.import_messages(self.account, self.page, dict(id='', messages=[]))
        self.assertTrue(refused['error'])


class ChatlessAITurnTests(unittest.TestCase):
    setUp = ai_fixtures.PageAITests.setUp
    saved = ai_fixtures.PageAITests.saved
    task = ai_fixtures.PageAITests.task
    answer = ai_fixtures.PageAITests.answer
    finish = ai_fixtures.PageAITests.finish

    def test_ai_turn_on_page_without_chat_saves_a_reviewable_proposal(self):
        self.store.plans.disconnect(self.account, self.page)
        run = self.finish(self.task())
        self.assertEqual(run['state'], 'done', run['message'])
        self.reader.assert_not_called()
        self.assertEqual([row['video_key'] for row in self.store.plans.review_queue()], [self.campaign+':1'])
        self.assertEqual(PageScheduleStore(self.root).list(), [])


if __name__ == '__main__':
    unittest.main()
