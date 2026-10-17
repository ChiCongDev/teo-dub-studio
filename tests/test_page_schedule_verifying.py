"""Independent scheduled inputs and read-only recovery of submitted results."""
from copy import deepcopy
import unittest

from tests import test_page_schedule_queue_wait as queue_fixtures
from teodub.page_schedule import PageScheduleController


class ScheduleVerificationTests(unittest.TestCase):
    setUp = queue_fixtures.QueueWaitTests.setUp
    video = queue_fixtures.QueueWaitTests.video
    schedule = queue_fixtures.QueueWaitTests.schedule
    submit = queue_fixtures.QueueWaitTests.submit
    reply = queue_fixtures.QueueWaitTests.reply
    tick = queue_fixtures.QueueWaitTests.tick
    advance = queue_fixtures.QueueWaitTests.advance
    live_wait = queue_fixtures.QueueWaitTests.live_wait
    arm = queue_fixtures.QueueWaitTests.arm
    first_publishing = queue_fixtures.QueueWaitTests.first_publishing
    finish_validation = queue_fixtures.QueueWaitTests.finish_validation
    assert_unattempted = queue_fixtures.QueueWaitTests.assert_unattempted
    waiting_pair = queue_fixtures.QueueWaitTests.waiting_pair

    def verifying(self, row):
        scope = (row['account_id'], row['page_key'], row['post_id'])
        self.store.activity.transition(*scope, 'submitting', 'Exactly one publish click', expected={'review'})
        post = self.store.activity.transition(*scope, 'processing', 'Facebook result pending', expected={'submitting'})
        self.owner.sessions[self.account].post_actions[row['request_id']].update(
            state='verifying', result=None, message='Sent; receipt observation continues')
        self.tick()
        return post

    def confirmed(self, row):
        scope = (row['account_id'], row['page_key'], row['post_id'])
        url = 'https://www.facebook.com/reel/'+str(int(row['post_id'], 16))
        self.store.activity.record_browser_receipt(*scope, dict(source='visible_dom', url=url))
        post = self.store.activity.transition(*scope, 'published', 'Actual matching result observed',
                                               expected={'processing'}, external_url=url)
        self.owner.sessions[self.account].post_actions[row['request_id']].update(state='done', result=post)
        return post

    def tracking(self, row):
        post = self.store.activity.post(self.account, self.page, row['post_id'])
        return dict(version=1, account_id=self.account, page_key=self.page, post_id=row['post_id'],
                    submitted_at=self.wall, deadline_at=self.wall+1800,
                    review=deepcopy(post['browser_review']), baseline=dict(statuses=[], articles=[]),
                    references=[], notifications={}, tab_url='https://www.facebook.com/profile.php?id=100')

    def reopen(self):
        self.controller.close()
        self.owner.sessions = {}
        self.controller = PageScheduleController(self.owner)
        self.addCleanup(self.controller.close)
        return self.controller

    def test_due_second_video_starts_validation_before_first_receipt(self):
        first, following = self.waiting_pair()
        self.verifying(first)
        self.assertEqual(self.store.get(first['id'])['state'], 'verifying')
        second = self.store.get(following['id'])
        self.assertEqual((second['state'], second['phase']), ('preparing', 'validate'))
        self.assertEqual([call[-1] for call in self.calls], ['prepare', 'publish'])
        self.assertEqual(self.thread.call_count, 2)
        self.assertEqual(self.store.activity.post(self.account, self.page, first['post_id'])['external_url'], '')

    def test_multiple_receipts_remain_pending_while_third_input_starts(self):
        first, second = self.waiting_pair()
        self.verifying(first)
        self.finish_validation(second)
        self.reply(self.store.get(second['id']), 'done', 'review')
        self.tick()
        second = self.store.get(second['id'])
        self.verifying(second)
        third = self.arm(self.schedule('c'*32), self.wall+1)
        self.advance(1)
        self.tick()
        self.assertEqual(self.store.get(first['id'])['state'], 'verifying')
        self.assertEqual(self.store.get(second['id'])['state'], 'verifying')
        self.assertEqual(self.store.get(third['id'])['phase'], 'validate')
        self.assertEqual(self.thread.call_count, 3)
        self.assertEqual([call[-1] for call in self.calls], ['prepare', 'publish', 'prepare', 'publish'])

    def test_verifying_message_requires_durable_processing_state(self):
        first, following = self.waiting_pair()
        self.owner.sessions[self.account].post_actions[first['request_id']].update(
            state='verifying', message='Synthetic uncommitted submission')
        self.tick()
        self.assertEqual(self.store.get(first['id'])['state'], 'needs_attention')
        self.assertEqual(self.store.get(following['id'])['phase'], 'validate')
        self.assertFalse(self.store.activity.post(self.account, self.page, first['post_id'])['external_url'])

    def test_receipt_for_first_does_not_change_following_video_status(self):
        first, following = self.waiting_pair()
        self.verifying(first)
        before_second = self.store.get(following['id'])
        self.confirmed(first)
        self.tick()
        self.assertEqual(self.store.get(first['id'])['state'], 'published')
        self.assertEqual(self.store.get(following['id']), before_second)
        self.assertFalse(self.store.activity.post(self.account, self.page, following['post_id'])['external_url'])

    def test_restart_resumes_only_result_read_and_keeps_unsubmitted_wait_paused(self):
        first, following = self.waiting_pair()
        self.verifying(first)
        tracking = self.tracking(first)
        self.store.activity.save_browser_tracking(self.account, self.page, first['post_id'], tracking)
        self.reopen()
        restored = self.store.get(first['id'])
        self.assertEqual((restored['state'], restored['phase'], restored['request_id']), ('verifying', 'verify', ''))
        self.assert_unattempted(following, 'paused')
        self.tick()
        self.assertEqual([call[-1] for call in self.calls], ['prepare', 'publish', 'verify'])
        self.assertEqual(self.store.get(first['id'])['phase'], 'verify')
        self.assertEqual(self.store.activity.post(self.account, self.page, first['post_id'])['browser_tracking'], tracking)
        self.assertEqual(self.thread.call_count, 2)

    def test_recent_legacy_processing_event_can_resume_read_without_descriptor(self):
        first = self.first_publishing()
        self.verifying(first)
        self.reopen()
        self.assertEqual((self.store.get(first['id'])['state'], self.store.get(first['id'])['phase']),
                         ('verifying', 'verify'))
        self.tick()
        self.assertEqual([call[-1] for call in self.calls], ['prepare', 'publish', 'verify'])

    def test_restart_does_not_resume_review_that_never_reached_publish_click(self):
        first = self.first_publishing()
        self.reopen()
        self.assertEqual(self.store.get(first['id'])['state'], 'needs_attention')
        self.assertNotIn(first['id'], self.controller.guards)
        self.tick()
        self.assertEqual([call[-1] for call in self.calls], ['prepare', 'publish'])

    def test_restart_repairs_schedule_when_receipt_committed_before_last_scheduler_tick(self):
        first = self.first_publishing()
        self.verifying(first)
        confirmed = self.confirmed(first)
        # Simulate process exit after the durable publication commit, before
        # the desktop receives its done event and updates the schedule row.
        self.assertEqual(self.store.get(first['id'])['state'], 'verifying')
        self.reopen()
        restored = self.store.get(first['id'])
        self.assertEqual(restored['state'], 'published')
        self.assertTrue(restored['finished_at'])
        self.assertNotIn(first['id'], self.controller.active)
        self.assertEqual(self.store.activity.post(self.account, self.page, first['post_id']), confirmed)
        self.tick()
        self.assertEqual([call[-1] for call in self.calls], ['prepare', 'publish'])

    def test_committed_publication_wins_over_request_error_during_live_tick(self):
        first = self.first_publishing()
        self.verifying(first)
        confirmed = self.confirmed(first)
        self.owner.sessions[self.account].post_actions[first['request_id']].update(
            state='error', result=None, message='Synthetic timeout raced with receipt commit')
        self.tick()
        self.assertEqual(self.store.get(first['id'])['state'], 'published')
        self.assertNotIn(first['id'], self.controller.active)
        self.assertEqual(self.store.activity.post(self.account, self.page, first['post_id']), confirmed)
        self.assertEqual([call[-1] for call in self.calls], ['prepare', 'publish'])

    def test_committed_publication_wins_when_worker_reply_was_lost(self):
        first = self.first_publishing()
        self.verifying(first)
        confirmed = self.confirmed(first)
        del self.owner.sessions[self.account].post_actions[first['request_id']]
        self.tick()
        self.assertEqual(self.store.get(first['id'])['state'], 'published')
        self.assertNotIn(first['id'], self.controller.guards)
        self.assertEqual(self.store.activity.post(self.account, self.page, first['post_id']), confirmed)
        self.assertEqual([call[-1] for call in self.calls], ['prepare', 'publish'])

    def stopped_submission(self):
        row = self.first_publishing()
        self.verifying(row)
        scope = (self.account, self.page, row['post_id'])
        self.store.activity.save_browser_tracking(*scope, self.tracking(row))
        post = self.store.activity.transition(*scope, 'unknown', 'Observation timed out', expected={'processing'})
        self.owner.sessions[self.account].post_actions[row['request_id']].update(
            state='done', result=post, message='Observation timed out')
        self.tick()
        return self.store.get(row['id'])

    def test_follow_stopped_submission_queues_only_verify_and_preserves_original_attempt(self):
        row = self.stopped_submission()
        before_post = self.store.activity.post(self.account, self.page, row['post_id'])
        before_calls = list(self.calls)
        self.assertEqual(row['state'], 'needs_attention')
        self.assertTrue(self.controller.can_follow_result(row))
        request = self.controller.follow_result(self.account, self.page, row['id'])
        current = self.store.get(row['id'])
        self.assertEqual((current['state'], current['phase'], current['request_id']), ('verifying', 'verify', request))
        self.assertNotEqual(request, row['request_id'])
        self.assertEqual(self.calls, before_calls+[(self.account, self.page, row['post_id'], 'verify')])
        for key in ('attempted_at', 'due_at', 'body', 'source', 'source_key', 'snapshot', 'post_id'):
            self.assertEqual(current[key], row[key], key)
        self.assertEqual(self.store.activity.post(self.account, self.page, row['post_id']), before_post)
        self.assertEqual(self.owner.sessions[self.account].post_actions[request]['operation'], 'verify')
        self.assertFalse(self.controller.can_follow_result(current))
        self.assertEqual(self.thread.call_count, 1)

    def test_follow_result_rejects_wrong_scope_without_touching_stopped_attempt(self):
        row = self.stopped_submission()
        before_calls = list(self.calls)
        for account, page in ((self.other, self.other_page), (self.account, self.other_page),
                              (self.other, self.page)):
            with self.subTest(account=account, page=page):
                with self.assertRaises(ValueError):
                    self.controller.follow_result(account, page, row['id'])
                self.assertEqual(self.store.get(row['id']), row)
        self.assertFalse(self.controller.can_follow_result(dict(row, account_id=self.other)))
        self.assertEqual(self.calls, before_calls)
        self.assertNotIn(row['id'], self.controller.guards)

    def test_follow_result_rejects_prepared_video_without_processing_event(self):
        row = self.first_publishing()
        self.controller.fail(row['id'], 'Stopped before actual publish click')
        self.store.activity.transition(self.account, self.page, row['post_id'], 'unknown',
                                       'Uncertain preparation only', expected={'review'})
        row = self.store.get(row['id'])
        before_calls = list(self.calls)
        self.assertFalse(self.controller.can_follow_result(row))
        with self.assertRaises(ValueError):
            self.controller.follow_result(self.account, self.page, row['id'])
        self.assertEqual(self.store.get(row['id']), row)
        self.assertEqual(self.calls, before_calls)
        self.assertNotIn(row['id'], self.controller.guards)


if __name__ == '__main__':
    unittest.main()
