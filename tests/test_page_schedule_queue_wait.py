"""Continuous account waits over temporary schedules; no real browser or posts."""
import time
import unittest
from unittest.mock import patch

from tests import test_page_schedule as fixtures
from teodub.page_schedule import PageScheduleController, campaign_video_source


class QueueWaitTests(unittest.TestCase):
    video = fixtures.ScheduleTests.video
    schedule = fixtures.ScheduleTests.schedule
    submit = fixtures.ScheduleTests.submit
    reply = fixtures.ScheduleTests.reply

    def setUp(self):
        fixtures.ScheduleTests.setUp(self)
        self.wall = time.time()
        self.clock = 1000.0
        self.enterContext(patch('teodub.page_schedule.time.time', side_effect=lambda: self.wall))
        self.enterContext(patch('teodub.page_schedule.time.monotonic', side_effect=lambda: self.clock))
        self.thread = self.enterContext(patch('teodub.page_schedule.threading.Thread'))

    def tick(self):
        # Legacy fixtures do this; queue continuity must use its own heartbeat.
        self.controller.last_tick = 0
        self.controller.tick()

    def advance(self, seconds, *, wall_seconds=None):
        self.clock += seconds
        self.wall += seconds if wall_seconds is None else wall_seconds

    def live_wait(self, seconds):
        while seconds:
            interval = min(60, seconds)
            self.advance(interval)
            self.tick()
            seconds -= interval

    def arm(self, row, due):
        self.store.update(row['id'], due_at=due)
        return self.controller.start(row['id'])

    def first_publishing(self):
        row = self.arm(self.schedule(), self.wall+1)
        self.advance(1)
        self.tick()
        self.finish_validation(row)
        self.reply(self.store.get(row['id']), 'done', 'review')
        self.tick()
        return self.store.get(row['id'])

    def finish_validation(self, row):
        self.controller.validate_video(self.store.get(row['id']))
        self.tick()

    def publish(self, row):
        current = self.store.get(row['id'])
        if current['phase'] == 'validate':
            self.finish_validation(current)
            current = self.store.get(row['id'])
        if current['phase'] == 'prepare':
            self.reply(current, 'done', 'review')
            self.tick()
            current = self.store.get(row['id'])
        self.reply(current, 'done', 'published')
        self.tick()

    def assert_unattempted(self, row, state):
        current = self.store.get(row['id'])
        self.assertEqual(current['state'], state)
        self.assertFalse(current['attempted_at'])
        self.assertFalse(current['request_id'])
        self.assertEqual(self.store.activity.post(current['account_id'], current['page_key'], current['post_id'])['state'], 'draft')
        return current

    def waiting_pair(self):
        first = self.first_publishing()
        following = self.arm(self.schedule('b'*32), self.wall+2)
        self.advance(2)
        self.tick()
        self.assert_unattempted(following, 'armed')
        return first, following

    def test_live_wait_longer_than_two_minutes_then_verified_success_starts_next_once(self):
        first, following = self.waiting_pair()
        self.live_wait(180)
        waiting = self.assert_unattempted(following, 'armed')
        self.assertEqual(waiting['due_at'], following['due_at'])
        self.assertIn('đã chờ 3 phút', waiting['message'])
        count = self.thread.call_count
        self.publish(first)
        self.assertEqual(self.store.get(first['id'])['state'], 'published')
        self.assertEqual(self.store.get(following['id'])['phase'], 'validate')
        self.assertEqual(self.thread.call_count, count+1)
        self.tick(); self.tick()
        self.assertEqual(self.thread.call_count, count+1)
        self.assertEqual(self.store.get(following['id'])['due_at'], following['due_at'])
        self.assertEqual([call[-1] for call in self.calls], ['prepare', 'publish'])

    def test_unknown_predecessor_releases_input_slot_without_replaying_previous_video(self):
        first, following = self.waiting_pair()
        self.live_wait(180)
        self.reply(first, 'done', 'unknown')
        self.tick(); self.tick()
        waiting = self.store.get(following['id'])
        self.assertEqual((waiting['state'], waiting['phase']), ('preparing', 'validate'))
        self.assertFalse(waiting['attempted_at'])
        self.assertFalse(waiting['request_id'])
        self.assertEqual(self.store.get(first['id'])['state'], 'needs_attention')
        self.assertEqual(self.store.activity.post(self.account, self.page, first['post_id'])['state'], 'unknown')
        self.assertEqual([call[-1] for call in self.calls], ['prepare', 'publish'])

    def test_receipt_error_does_not_stop_independent_following_video(self):
        first, following = self.waiting_pair()
        self.owner.sessions[self.account].post_actions[first['request_id']].update(
            state='error', message='Synthetic receipt failure')
        self.tick()
        waiting = self.store.get(following['id'])
        self.assertEqual((waiting['state'], waiting['phase']), ('preparing', 'validate'))
        self.assertFalse(waiting['attempted_at'])
        self.assertFalse(waiting['request_id'])
        self.assertEqual([call[-1] for call in self.calls], ['prepare', 'publish'])

    def test_late_first_observation_still_fails_even_while_account_is_busy(self):
        self.first_publishing()
        following = self.arm(self.schedule('b'*32), self.wall+1)
        self.advance(120, wall_seconds=121.01)
        self.tick()
        waiting = self.assert_unattempted(following, 'needs_attention')
        self.assertIn('lỡ giờ', waiting['message'])
        self.assertEqual(self.thread.call_count, 1)

    def test_exact_two_minute_lateness_can_observe_wait_without_changing_existing_boundary(self):
        self.first_publishing()
        following = self.arm(self.schedule('b'*32), self.wall+1)
        self.advance(120, wall_seconds=121)
        self.tick()
        self.assert_unattempted(following, 'armed')
        self.live_wait(60)
        self.assert_unattempted(following, 'armed')
        self.assertEqual(self.thread.call_count, 1)

    def test_sleep_over_two_minutes_invalidates_wait_and_never_catches_up(self):
        first, following = self.waiting_pair()
        self.advance(121)
        self.tick()
        self.assert_unattempted(following, 'needs_attention')
        self.publish(first)
        self.tick()
        self.assertEqual(self.thread.call_count, 1)

    def test_monotonic_gap_does_not_regrant_old_wait_after_wall_clock_moves_back(self):
        first, following = self.waiting_pair()
        self.advance(121, wall_seconds=60)
        self.tick()
        self.assert_unattempted(following, 'armed')
        self.live_wait(61)
        self.assert_unattempted(following, 'needs_attention')
        self.publish(first)
        self.assertEqual(self.thread.call_count, 1)

    def test_pause_then_reschedule_requires_new_future_approval_and_fresh_wait(self):
        first, following = self.waiting_pair()
        self.live_wait(180)
        self.controller.pause(following['id'])
        self.publish(first)
        self.assert_unattempted(following, 'paused')
        with self.assertRaises(ValueError):
            self.controller.start(following['id'])
        self.store.reschedule(following['id'], self.wall+60)
        self.tick()
        self.assert_unattempted(following, 'scheduled')
        self.controller.start(following['id'])
        self.advance(60)
        self.tick()
        self.assertEqual(self.store.get(following['id'])['phase'], 'validate')

    def test_removal_and_restart_never_preserve_a_wait_privilege(self):
        first, following = self.waiting_pair()
        preview = self.store.removal_preview([following['id']], self.account, self.page)
        self.assertEqual(self.controller.remove_many(preview, self.account, self.page)['removed'], [following['id']])
        self.live_wait(180)
        self.publish(first)
        self.assertEqual(self.store.get(following['id'])['state'], 'removed')
        self.assertEqual(self.thread.call_count, 1)
        self.controller.close()
        reopened = PageScheduleController(self.owner)
        self.addCleanup(reopened.close)
        reopened.tick()
        self.assertEqual(reopened.active, set())
        self.assertEqual(reopened.guards, {})
        self.assertEqual(reopened.queue_waits, {})

    def test_restart_pauses_unattempted_wait_instead_of_resuming_it(self):
        _, following = self.waiting_pair()
        self.controller.close()
        reopened = PageScheduleController(self.owner)
        self.addCleanup(reopened.close)
        reopened.tick()
        self.assert_unattempted(following, 'paused')
        self.assertEqual(reopened.queue_waits, {})
        self.assertEqual(self.thread.call_count, 1)

    def test_changed_candidate_binding_cannot_inherit_old_wait(self):
        _, following = self.waiting_pair()
        self.live_wait(180)
        with self.store.database() as conn:
            conn.execute('UPDATE schedules SET body=? WHERE id=?', ('Externally changed approved caption', following['id']))
        self.tick()
        self.assert_unattempted(following, 'needs_attention')
        self.assertEqual(self.thread.call_count, 1)

    def test_retimed_blocker_cannot_grant_its_old_wait_or_pacing(self):
        first, following = self.waiting_pair()
        self.store.update(first['id'], due_at=first['due_at']+1)
        self.tick()
        waiting = self.assert_unattempted(following, 'needs_attention')
        self.assertIn('đã thay đổi', waiting['message'])
        self.assertEqual(self.thread.call_count, 1)

    def test_unrelated_manual_browser_work_does_not_create_a_queue_exception(self):
        following = self.arm(self.schedule(), self.wall+1)
        self.owner.sessions[self.account] = __import__('types').SimpleNamespace(
            loading_pages=True, post_actions={})
        self.advance(1); self.tick()
        self.live_wait(121)
        waiting = self.assert_unattempted(following, 'needs_attention')
        self.assertIn('lỡ giờ', waiting['message'])
        self.assertEqual(self.thread.call_count, 0)

    def test_another_account_continues_while_first_account_waits(self):
        _, following = self.waiting_pair()
        source = campaign_video_source(self.directory, *self.video(campaign_id='c'*32))
        other = self.store.create(self.other, self.other_page, source, 'Other approved caption', '', self.wall+10)
        self.controller.start(other['id'])
        self.advance(10); self.tick()
        self.assertEqual(self.store.get(other['id'])['phase'], 'validate')
        self.assert_unattempted(following, 'armed')
        self.assertEqual(self.thread.call_count, 2)

    def test_verified_release_in_same_tick_preserves_original_gap_between_waiting_jobs(self):
        first = self.first_publishing()
        following = self.arm(self.schedule('b'*32), self.wall+10)
        third = self.arm(self.schedule('c'*32), self.wall+20)
        self.advance(20); self.tick()
        self.live_wait(180)
        self.publish(first)
        self.assertEqual(self.store.get(following['id'])['phase'], 'validate')
        self.assert_unattempted(third, 'armed')
        self.publish(following)
        waiting = self.assert_unattempted(third, 'armed')
        self.assertIn('giữ khoảng cách', waiting['message'])
        self.assertIn('Chờ đến', waiting['message'])
        count = self.thread.call_count
        self.advance(9); self.tick()
        self.assert_unattempted(third, 'armed')
        self.advance(1); self.tick()
        self.assertEqual(self.store.get(third['id'])['phase'], 'validate')
        self.assertEqual(self.thread.call_count, count+1)
        self.assertEqual(self.store.get(third['id'])['due_at'], third['due_at'])

    def test_later_due_row_waits_behind_armed_pacing_row_without_overtaking_or_expiring(self):
        first = self.first_publishing()
        following = self.arm(self.schedule('b'*32), self.wall+10)
        third = self.arm(self.schedule('c'*32), self.wall+100)
        fourth = self.arm(self.schedule('d'*32), self.wall+215)
        self.live_wait(200)
        self.publish(first)
        self.publish(following)
        self.assert_unattempted(third, 'armed')
        self.advance(15); self.tick()
        self.assert_unattempted(fourth, 'armed')
        self.assertIn('lượt tải/gửi trước', self.store.get(fourth['id'])['message'])
        self.live_wait(75)
        self.assertEqual(self.store.get(third['id'])['phase'], 'validate')
        self.publish(third)
        self.assert_unattempted(fourth, 'armed')
        self.live_wait(114)
        self.assert_unattempted(fourth, 'armed')
        self.advance(1); self.tick()
        self.assertEqual(self.store.get(fourth['id'])['phase'], 'validate')
        self.assertEqual(self.store.get(fourth['id'])['due_at'], fourth['due_at'])

    def test_wall_clock_jump_does_not_shorten_pacing_measured_with_monotonic_time(self):
        first = self.first_publishing()
        following = self.arm(self.schedule('b'*32), self.wall+10)
        third = self.arm(self.schedule('c'*32), self.wall+20)
        self.advance(20); self.tick()
        self.live_wait(180)
        self.publish(first); self.publish(following)
        self.advance(0, wall_seconds=3600); self.tick()
        self.assert_unattempted(third, 'armed')
        self.advance(10); self.tick()
        self.assertEqual(self.store.get(third['id'])['phase'], 'validate')


if __name__ == '__main__':
    unittest.main()
