"""Pausing result checks preserves one-click history and independent posting."""
import threading
import unittest
from unittest.mock import patch

from tests import test_page_schedule_verifying as fixtures
from teodub.page_schedule import PageScheduleController


class PausedResultCheckScheduleTests(unittest.TestCase):
    setUp = fixtures.ScheduleVerificationTests.setUp
    video = fixtures.ScheduleVerificationTests.video
    schedule = fixtures.ScheduleVerificationTests.schedule
    submit = fixtures.ScheduleVerificationTests.submit
    reply = fixtures.ScheduleVerificationTests.reply
    tick = fixtures.ScheduleVerificationTests.tick
    advance = fixtures.ScheduleVerificationTests.advance
    live_wait = fixtures.ScheduleVerificationTests.live_wait
    arm = fixtures.ScheduleVerificationTests.arm
    first_publishing = fixtures.ScheduleVerificationTests.first_publishing
    finish_validation = fixtures.ScheduleVerificationTests.finish_validation
    assert_unattempted = fixtures.ScheduleVerificationTests.assert_unattempted
    waiting_pair = fixtures.ScheduleVerificationTests.waiting_pair
    verifying = fixtures.ScheduleVerificationTests.verifying
    confirmed = fixtures.ScheduleVerificationTests.confirmed
    tracking = fixtures.ScheduleVerificationTests.tracking
    reopen = fixtures.ScheduleVerificationTests.reopen

    def pause_checks(self):
        self.enterContext(patch('teodub.page_schedule.result_checks_enabled', return_value=False))

    def save_sent_attempt(self, row):
        self.verifying(row)
        self.store.activity.save_browser_tracking(self.account, self.page, row['post_id'], self.tracking(row))
        return self.store.activity.post(self.account, self.page, row['post_id'])

    def stopped_attempt(self):
        row = self.first_publishing()
        self.save_sent_attempt(row)
        post = self.store.activity.transition(self.account, self.page, row['post_id'], 'unknown',
                                               'Result observation stopped', expected={'processing'})
        self.owner.sessions[self.account].post_actions[row['request_id']].update(state='done', result=post)
        self.tick()
        return self.store.get(row['id'])

    def test_closed_submitted_attempt_becomes_terminal_without_startup_browser_checks(self):
        row = self.first_publishing()
        post_before = self.save_sent_attempt(row)
        self.pause_checks()
        calls = list(self.calls)
        self.reopen()
        current = self.store.get(row['id'])
        self.assertEqual(current['state'], 'submitted')
        self.assertTrue(current['finished_at'])
        self.assertEqual(self.controller.guards, {})
        self.assertEqual(self.controller.active, set())
        self.tick()
        self.assertEqual(self.calls, calls)
        self.assertEqual(self.owner.sessions, {})
        self.assertEqual(self.store.activity.post(self.account, self.page, row['post_id']), post_before)

    def test_stopped_unknown_attempt_is_preserved_as_sent_without_success_receipt(self):
        row = self.stopped_attempt()
        before = self.store.activity.post(self.account, self.page, row['post_id'])
        self.pause_checks()
        self.reopen()
        current = self.store.get(row['id'])
        self.assertEqual(current['state'], 'submitted')
        for key in ('attempted_at', 'due_at', 'body', 'snapshot', 'source', 'post_id'):
            self.assertEqual(current[key], row[key], key)
        self.assertEqual(self.store.activity.post(self.account, self.page, row['post_id']), before)
        self.assertEqual((before['state'], before['browser_receipt'], before['external_url']), ('unknown', {}, ''))

    def test_disabled_result_paths_never_queue_follow_or_explicit_inspection(self):
        row = self.stopped_attempt()
        calls = list(self.calls)
        self.pause_checks()
        self.assertFalse(self.controller.can_follow_result(row))
        self.assertFalse(self.controller.can_inspect_result(row))
        with self.assertRaises(ValueError):
            self.controller.follow_result(self.account, self.page, row['id'])
        with self.assertRaises(ValueError):
            self.controller.reconcile_preview(self.account, self.page, row['id'])
        with self.assertRaises(ValueError):
            self.controller.inspect_result(self.account, self.page, row['id'], result_url='https://www.facebook.com/reel/123',
                expected_reconcile={}, confirmed_attempt=True)
        self.assertEqual(self.calls, calls)
        self.assertEqual(self.store.get(row['id']), row)

    def test_active_observer_off_releases_following_input_without_changing_post_evidence(self):
        first, following = self.waiting_pair()
        post = self.save_sent_attempt(first)
        request = self.owner.sessions[self.account].post_actions[first['request_id']]
        request['cancel'] = threading.Event()
        self.pause_checks()
        self.tick()
        self.assertEqual(self.store.get(first['id'])['state'], 'submitted')
        self.assertTrue(request['cancel'].is_set())
        self.assertNotIn(first['id'], self.controller.guards)
        self.assertEqual(self.store.get(following['id'])['phase'], 'validate')
        self.assertEqual(self.store.activity.post(self.account, self.page, first['post_id']), post)
        self.assertEqual(self.thread.call_count, 2)

    def test_publish_result_paused_marker_finishes_only_with_real_durable_send(self):
        first = self.first_publishing()
        self.save_sent_attempt(first)
        scope = (self.account, self.page, first['post_id'])
        post = self.store.activity.transition(*scope, 'unknown', 'Checks temporarily disabled', expected={'processing'})
        result = dict(post, verification_paused=True)
        self.owner.sessions[self.account].post_actions[first['request_id']].update(state='done', result=result)
        self.pause_checks()
        self.tick()
        self.assertEqual(self.store.get(first['id'])['state'], 'submitted')
        self.assertEqual([call[-1] for call in self.calls], ['prepare', 'publish'])
        self.assertFalse(self.store.activity.post(*scope)['external_url'])

    def test_paused_marker_without_processing_event_cannot_claim_sent(self):
        first = self.first_publishing()
        post = self.store.activity.post(self.account, self.page, first['post_id'])
        self.owner.sessions[self.account].post_actions[first['request_id']].update(
            state='done', result=dict(post, verification_paused=True))
        self.pause_checks()
        self.tick()
        self.assertEqual(self.store.get(first['id'])['state'], 'needs_attention')
        self.assertNotIn(first['id'], self.controller.guards)
        self.assertEqual(self.store.activity.post(self.account, self.page, first['post_id']), post)

    def test_future_fresh_post_still_prepares_and_publishes_with_checks_disabled(self):
        self.pause_checks()
        first = self.first_publishing()
        self.assertEqual(first['phase'], 'publish')
        self.assertEqual([call[-1] for call in self.calls], ['prepare', 'publish'])
        self.assertEqual(self.thread.call_count, 1)
        self.assertEqual(self.store.activity.post(self.account, self.page, first['post_id'])['state'], 'review')

    def test_removed_cancelled_unattempted_and_never_submitted_rows_stay_unchanged(self):
        removed = self.schedule('b'*32)
        removed = self.store.update(removed['id'], state='removed')
        cancelled = self.schedule('c'*32)
        cancelled = self.store.update(cancelled['id'], state='cancelled')
        fresh = self.schedule('d'*32)
        never_sent = self.first_publishing()
        self.controller.fail(never_sent['id'], 'Stopped before click')
        never_sent = self.store.get(never_sent['id'])
        self.pause_checks()
        calls = list(self.calls)
        self.reopen()
        for row in (removed, cancelled, fresh, never_sent):
            self.assertEqual(self.store.get(row['id']), row)
        self.tick()
        self.assertEqual(self.calls, calls)

    def test_preexisting_confirmed_receipt_still_wins_with_checks_disabled(self):
        first = self.first_publishing()
        self.save_sent_attempt(first)
        confirmed = self.confirmed(first)
        self.pause_checks()
        self.tick()
        self.assertEqual(self.store.get(first['id'])['state'], 'published')
        self.assertEqual(self.store.activity.post(self.account, self.page, first['post_id']), confirmed)

    def test_corrupt_or_mismatched_tracking_does_not_reclassify_uncertain_attempt(self):
        first = self.stopped_attempt()
        with self.store.activity.database() as conn:
            conn.execute("UPDATE posts SET browser_tracking='{}' WHERE id=?", (first['post_id'],))
        self.pause_checks()
        self.reopen()
        self.assertEqual(self.store.get(first['id']), first)
        self.assertEqual(self.controller.guards, {})


if __name__ == '__main__':
    unittest.main()
