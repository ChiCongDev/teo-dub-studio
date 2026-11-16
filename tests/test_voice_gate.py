"""16/11/2026 (owner): a video whose new voice is not ready is never posted with the old one.
* a remake that fails is tried 3 times in all, at once; failed every time, the video is dropped for good and the link goes on;
* at the due time of a video without its new voice the Page's nearest later ready video takes the time (and the other takes its time);
  nothing ready later: the schedule is paused and nothing is posted; not even "Dang ngay" posts the old voice;
* a dropped video's Page schedule is removed and the nearest later ready video takes its time.
Local fixtures only: no worker process, Chrome, Facebook or AI."""
import time

from teodub import campaign, campaign_daily, campaign_redo
from teodub.campaign_tracking import tracking_row
from teodub.common import atomic_json
from teodub.page_schedule_voice import blocked_reason
from tests import test_campaign_redo as base

DROPPED = dict(at='2026-11-16T10:00:00+07:00', tries=3, message='Codex dịch thiếu hoặc thừa câu')


class Flaky(base.Redo):
    def runner(self, fails, only=None):
        """Every remake of `only` (or of any video) fails `fails` times in a row, then really makes the new file."""
        calls = {}

        def run(config_path, stop):
            number = int(config_path.parent.parent.parent.name)
            if only is not None and number != only:
                return self.remake(config_path, stop)
            calls[number] = calls.get(number, 0)+1
            if calls[number] <= fails:
                return dict(status='failed', message='Codex dịch thiếu hoặc thừa câu')
            return self.remake(config_path, stop)
        return run, calls


class RemakeTriesTests(Flaky):
    def test_a_failing_remake_is_tried_three_times_in_all_and_can_succeed_on_the_last(self):
        a = self.finished(2)
        self.new_voice(a)
        campaign_redo.apply(a, [1])
        run, calls = self.runner(2)
        result = campaign._run_campaign_locked(a, runner=run)
        item = self.item(a, 1)
        self.assertEqual(calls, {1: 3})
        self.assertNotIn('redo', item)
        self.assertNotIn('redo_dropped', item)
        self.assertTrue(item['outputs']['video'].endswith('_lan-2.mp4'))
        self.assertEqual(result['status'], 'completed')

    def test_failing_three_times_drops_the_video_for_good_and_the_link_goes_on(self):
        a = self.finished(3)
        old = self.item(a, 1)['outputs']
        self.new_voice(a)
        campaign_redo.apply(a, [1, 2])
        run, calls = self.runner(99, only=1)
        result = campaign._run_campaign_locked(a, runner=run)
        first, second = self.item(a, 1), self.item(a, 2)
        self.assertEqual(calls, {1: 3})  # Three tries, no more.
        self.assertNotIn('redo', first)
        self.assertEqual(first['redo_dropped']['tries'], 3)
        self.assertIn('Codex dịch thiếu', first['redo_dropped']['message'])
        self.assertEqual((first['status'], first['outputs']), ('completed', old))  # The old files stay on disk.
        self.assertTrue(second['outputs']['video'].endswith('_lan-2.mp4'))  # The next video was made.
        self.assertEqual(result['status'], 'completed')  # The link does not turn into a failure.
        again = []
        campaign._run_campaign_locked(a, runner=lambda c, s: again.append(c) or self.remake(c, s))
        self.assertEqual(again, [])  # Never made again.
        self.assertEqual(campaign.mark_redo(campaign.read_json(a/'campaign.json'), [1]), [])

    def test_the_plan_and_the_row_say_the_video_was_dropped(self):
        a = self.finished(2)
        self.new_voice(a)
        campaign_redo.apply(a, [1])
        run, _ = self.runner(99)
        campaign._run_campaign_locked(a, runner=run)
        plan = campaign_redo.build_plan(a)
        entry = next(e for e in plan['entries'] if e['number'] == 1)
        self.assertEqual(entry['kind'], 'skip')
        self.assertIn('lỗi 3 lần', entry['reason'])
        row = tracking_row(self.item(a, 1))
        self.assertIn('đã bỏ', row[3])
        self.assertIn('Đã bỏ, không đăng', row[6])

    def test_a_stop_or_a_quota_is_not_a_failure_and_keeps_the_video_in_the_queue(self):
        a = self.finished(2)
        self.new_voice(a)
        campaign_redo.apply(a, [1])
        calls = []

        def stopped(config_path, stop):
            calls.append(1)
            return dict(status='failed', message='Hết hạn mức AI')
        campaign._run_campaign_locked(a, runner=stopped)
        self.assertEqual(len(calls), 1)  # No second try for a pause.
        self.assertIn('redo', self.item(a, 1))
        self.assertNotIn('redo_dropped', self.item(a, 1))

    def test_the_tries_of_one_video_count_once_in_the_number_of_videos_a_day(self):
        a = self.finished(2)
        self.new_voice(a)
        campaign_redo.apply(a, [1, 2])
        run, _ = self.runner(99, only=1)
        campaign._run_campaign_locked(a, runner=run)
        new, again = campaign_daily.counts_today(campaign.read_json(a/'campaign.json'))
        self.assertEqual(again, 2)  # Video 1 dropped (once) and video 2 made again (once).


class Gate(base.PageFixture):
    def mark(self, directory, number, **fields):
        manifest = campaign.read_json(directory/'campaign.json')
        item = next(x for x in manifest['items'] if x['number'] == number)
        if fields.pop('redo', False):
            campaign.mark_redo(manifest, [number])
        item.update(fields)
        atomic_json(directory/'campaign.json', manifest)

    def due_now(self, ident):
        self.store.update(ident, due_at=time.time()-5)

    def tick(self):
        self.controller.last_tick = 0
        self.controller.tick()

    def armed(self, directory, count, hours):
        ids = [self.schedule(directory, n, h) for n, h in zip(range(1, count+1), hours)]
        for ident in ids:
            self.controller.start(ident)
        return ids


class BlockedTests(Gate):
    def test_only_a_video_waiting_for_its_new_voice_or_dropped_is_blocked(self):
        a = self.finished(4)
        ids = [self.schedule(a, n, n+4) for n in (1, 2, 3, 4)]
        self.mark(a, 1, redo=True)
        self.mark(a, 2, redo_dropped=DROPPED)
        rows = [self.store.get(i) for i in ids]
        self.assertIn('chưa có bản giọng mới', blocked_reason(rows[0]))
        self.assertIn('bị bỏ', blocked_reason(rows[1]))
        self.mark(a, 3, redo_failed=dict(at='2026-11-15T10:00:00+07:00', message='Codex dịch thiếu hoặc thừa câu'))
        self.assertIn('lần làm lại gần nhất bị lỗi', blocked_reason(self.store.get(ids[2])))
        self.assertEqual(blocked_reason(rows[3]), '')
        gone = dict(rows[0], source=dict(rows[0]['source'], campaign=str(a/'missing')))
        self.assertEqual(blocked_reason(gone), '')  # Unreadable campaign: as before.

    def test_even_post_now_refuses_a_video_without_its_new_voice(self):
        a = self.finished(2)
        first, _ = (self.schedule(a, n, n+4) for n in (1, 2))
        self.mark(a, 1, redo=True)
        row = self.store.get(first)
        with self.assertRaisesRegex(ValueError, 'không đăng bản giọng cũ'):
            self.controller.start_now(self.account, self.page, first, expected_schedule=row)
        self.assertEqual(self.store.get(first), row)  # Nothing changed.


class SwapTests(Gate):
    def test_a_due_video_without_its_voice_swaps_places_with_the_nearest_later_ready_video(self):
        a = self.finished(4)
        first, second, third, fourth = self.armed(a, 4, (5, 6, 7, 8))
        times = {i: self.store.get(i)['due_at'] for i in (first, second, third, fourth)}
        self.mark(a, 1, redo=True)
        self.mark(a, 2, redo=True)  # The next one is not ready either: the third is the nearest ready one.
        self.due_now(first)
        self.tick()
        was, took = self.store.get(first), self.store.get(third)
        self.assertEqual(was['due_at'], times[third])  # Video 1 moved to the time of video 3.
        self.assertLessEqual(took['due_at'], time.time())  # Video 3 takes the time that is due now.
        self.assertEqual(self.store.get(second)['due_at'], times[second])
        self.assertEqual(self.store.get(fourth)['due_at'], times[fourth])
        self.assertEqual((was['state'], took['state']), ('armed', 'armed'))
        self.assertIn('Video 3', was['message'])
        self.assertIn('Video 1', took['message'])
        self.assertEqual((took['source']['number'], took['body'] != was['body']), (3, True))  # Each keeps its own video and caption.
        self.controller.owner.submit_page_browser.assert_not_called()

    def test_nothing_ready_later_pauses_the_schedule_and_posts_nothing(self):
        a = self.finished(3)
        first, second, third = self.armed(a, 3, (5, 6, 7))
        for n in (1, 2, 3):
            self.mark(a, n, redo=True)
        self.due_now(first)
        self.tick()
        row = self.store.get(first)
        self.assertEqual(row['state'], 'paused')
        self.assertIn('không đăng bản giọng cũ', row['message'])
        self.assertNotIn(first, self.controller.guards)
        self.assertEqual(self.store.get(second)['state'], 'armed')  # The others wait for their own time.
        self.controller.owner.submit_page_browser.assert_not_called()

    def test_only_an_armed_untouched_later_appointment_of_the_same_page_is_a_partner(self):
        a = self.finished(4)
        first, second, third = self.armed(a, 3, (5, 6, 7))
        fourth = self.schedule(a, 4, 8)  # Saved, never switched on: not armed.
        self.store.update(second, state='paused')  # Paused: not a partner either.
        self.mark(a, 1, redo=True)
        self.due_now(first)
        self.tick()
        self.assertEqual(self.store.get(first)['state'], 'armed')
        self.assertLessEqual(self.store.get(third)['due_at'], time.time())
        self.assertEqual(self.store.get(fourth)['state'], 'scheduled')

    def test_a_ready_video_is_posted_as_before(self):
        a = self.finished(2)
        first, second = self.armed(a, 2, (5, 6))
        self.due_now(first)
        self.tick()
        self.assertEqual(self.store.get(first)['state'], 'preparing')  # Not touched by the gate.
        self.assertEqual(self.store.get(second)['state'], 'armed')

    def test_the_exchange_is_all_or_nothing(self):
        a = self.finished(2)
        first, second = self.armed(a, 2, (5, 6))
        before = {i: self.store.get(i) for i in (first, second)}
        self.store.update(second, state='paused')
        with self.assertRaises(ValueError):
            self.store.retime(self.account, self.page, {first: before[second]['due_at'], second: before[first]['due_at']},
                              {first: before[first]['due_at'], second: before[second]['due_at']},
                              {first: 'a', second: 'b'})
        self.assertEqual(self.store.get(first)['due_at'], before[first]['due_at'])


class DroppedTests(Gate):
    def test_a_dropped_videos_schedule_is_removed_and_the_next_ready_video_takes_its_time(self):
        a = self.finished(3)
        first, second, third = self.armed(a, 3, (5, 6, 7))
        times = {i: self.store.get(i)['due_at'] for i in (first, second, third)}
        self.mark(a, 1, redo_dropped=DROPPED)
        self.controller.dropped_checked = 0
        self.controller._apply_dropped_videos(time.monotonic()+100)
        self.assertEqual(self.store.get(first)['state'], 'removed')
        self.assertNotIn(first, self.controller.guards)
        self.assertEqual(self.store.get(second)['due_at'], times[first])  # Video 2 comes up to the time of video 1.
        self.assertIn('Video 1', self.store.get(second)['message'])
        self.assertEqual(self.store.get(third)['due_at'], times[third])
        self.assertEqual(self.store.get(second)['state'], 'armed')

    def test_the_controllers_own_tick_removes_the_schedule_of_a_dropped_video(self):
        a = self.finished(2)
        first, second = self.armed(a, 2, (5, 6))
        self.mark(a, 1, redo_dropped=DROPPED)
        self.tick()
        self.assertEqual(self.store.get(first)['state'], 'removed')
        self.assertEqual(self.store.get(second)['state'], 'armed')

    def test_a_time_that_is_almost_now_is_not_taken_over_and_the_others_keep_their_time(self):
        a = self.finished(2)
        first, second = self.armed(a, 2, (.05, 6))  # 3 minutes ahead.
        time_second = self.store.get(second)['due_at']
        self.mark(a, 1, redo_dropped=DROPPED)
        self.controller.dropped_checked = 0
        self.controller._apply_dropped_videos(time.monotonic()+100)
        self.assertEqual(self.store.get(first)['state'], 'removed')
        self.assertEqual(self.store.get(second)['due_at'], time_second)

    def test_a_dropped_video_with_a_sent_schedule_is_left_alone(self):
        a = self.finished(2)
        first, second = (self.schedule(a, n, n+4) for n in (1, 2))
        self.store.update(first, state='needs_attention', attempted_at='synthetic attempted')
        self.mark(a, 1, redo_dropped=DROPPED)
        self.controller.dropped_checked = 0
        self.controller._apply_dropped_videos(time.monotonic()+100)
        self.assertEqual(self.store.get(first)['state'], 'needs_attention')


if __name__ == '__main__':
    import unittest
    unittest.main()
