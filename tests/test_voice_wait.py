"""22/11/2026 (owner): every Page schedule keeps ITS video and is never posted with the old voice.
* made again before its time: posted at its old time;
* still being made at its time: the schedule waits (armed) and is posted as soon as the new voice is ready, also after closing Tèo;
* a waiting schedule does not keep Chrome open; its message says when the link is stopped;
* no 30-minute rule; a video being made when "làm lại" was saved is made again when it finishes;
* a video made again for a post already due comes first and runs even when "Số video mỗi ngày" is reached.
Local fixtures only: no worker process, Chrome, Facebook or AI."""
import time
from types import SimpleNamespace

from teodub import campaign, campaign_daily, campaign_redo
from teodub.facebook_ui import FacebookTab
from teodub.page_schedule import PageScheduleController
from teodub.page_schedule_voice import read_holds
from tests.test_voice_gate import Gate


class WaitTests(Gate):
    def waiting(self, count=2):
        a = self.finished(count)
        ids = self.armed(a, count, [5+n for n in range(count)])
        self.new_voice(a)
        campaign_redo.apply(a, [1])
        self.due_now(ids[0])
        self.tick()
        return a, ids

    def test_posted_as_soon_as_the_new_voice_is_ready(self):
        a, (first, second) = self.waiting()
        old_due = self.store.get(first)['due_at']
        self.assertIn(first, read_holds(self.store))
        self.tick()
        self.assertEqual(self.store.get(first)['state'], 'armed')  # Still waiting: nothing posted, nothing moved.
        self.controller.owner.submit_page_browser.assert_not_called()
        campaign._run_campaign_locked(a, runner=self.remake)  # The new voice is ready now.
        self.tick()
        row = self.store.get(first)
        self.assertGreater(row['due_at'], old_due)  # Given the time now …
        self.assertLessEqual(row['due_at'], time.time())
        self.assertIn('đã xong: Tèo đăng ngay', row['message'])
        self.assertNotIn(first, read_holds(self.store))
        self.tick()
        row = self.store.get(first)
        self.assertEqual(row['state'], 'preparing')  # … and posted,
        self.assertTrue(row['source']['path'].endswith('_lan-2.mp4'))  # with the new voice.
        self.assertEqual(self.store.get(second)['state'], 'armed')

    def test_made_again_before_its_time_is_posted_at_its_old_time(self):
        a = self.finished(1)
        (first,) = self.armed(a, 1, (5,))
        due = self.store.get(first)['due_at']
        self.new_voice(a)
        campaign_redo.apply(a, [1])
        campaign._run_campaign_locked(a, runner=self.remake)
        self.tick()
        row = self.store.get(first)
        self.assertEqual((row['due_at'], row['state']), (due, 'armed'))  # The old time is kept …
        self.assertTrue(row['source']['path'].endswith('_lan-2.mp4'))  # … for the new file.
        self.due_now(first)
        self.tick()
        self.assertEqual(self.store.get(first)['state'], 'preparing')
        self.assertEqual(read_holds(self.store), {})

    def test_the_message_says_the_link_is_stopped(self):
        a, (first, _) = self.waiting()
        self.assertIn('đang dừng: bấm Chạy', self.store.get(first)['message'])
        self.assertIn('Tèo không đăng bản giọng cũ', self.store.get(first)['message'])

    def test_the_wait_goes_on_after_closing_and_opening_teo(self):
        a, (first, _) = self.waiting()
        due = self.store.get(first)['due_at']
        self.controller.close()
        again = PageScheduleController(self.controller.owner)
        self.addCleanup(again.close)
        row = again.store.get(first)
        self.assertEqual((row['state'], row['due_at']), ('armed', due))  # Re-armed at its old time, not moved to another day.
        self.assertIn('tiếp tục chờ bản giọng mới', row['message'])
        campaign._run_campaign_locked(a, runner=self.remake)
        for _ in range(2):
            again.last_tick = 0
            again.tick()
        self.assertEqual(again.store.get(first)['state'], 'preparing')

    def test_a_waiting_schedule_does_not_keep_chrome_open_but_a_coming_one_does(self):
        a, (first, second) = self.waiting()
        tab = SimpleNamespace(get_page_scheduler=lambda: self.controller)
        self.assertFalse(FacebookTab.schedule_busy_within(tab, self.account))
        self.store.update(second, due_at=time.time()+120)
        self.assertTrue(FacebookTab.schedule_busy_within(tab, self.account))

    def test_a_schedule_given_another_time_by_hand_forgets_the_wait(self):
        a, (first, _) = self.waiting()
        self.store.update(first, state='paused')
        self.store.reschedule(first, time.time()+3600)
        self.controller.holds_tidied = 0
        self.tick()
        self.assertEqual(read_holds(self.store), {})


class WorkerTests(Gate):
    def test_a_video_made_again_for_a_post_already_due_comes_first_even_past_the_daily_number(self):
        a = self.finished(3)
        campaign_daily.write_limit(a, '1')  # Three made today already: the day's number is reached.
        first, second = (self.schedule(a, n, h) for n, h in ((1, 5), (2, 1)))
        for ident in (first, second):
            self.controller.start(ident)
        self.store.update(first, due_at=time.time()-60)  # Due: its post waits for it.
        self.new_voice(a)
        campaign_redo.apply(a, [1, 2])
        self.assertEqual(sorted(campaign_redo.next_due(self.root, campaign.read_json(a/'campaign.json')['id'])), [1, 2])
        campaign._run_campaign_locked(a, runner=self.remake)
        self.assertEqual([number for _voice, number in self.again], [1])  # Video 2 (not due yet) waits for tomorrow.

    def test_a_video_being_made_when_remake_was_saved_is_made_again_when_it_finishes(self):
        a = self.make_link(3)

        def save_now():
            record = self.new_voice(a)
            campaign_redo.remember_follow(a, record['id'])
        self.during[('A', 1)] = save_now

        def run(config_path, stop):
            return (self.remake(config_path, stop) if config_path.parent.parent.name == 'revisions'
                    else self.video(config_path, stop))
        campaign._run_campaign_locked(a, runner=run)
        first, second = self.item(a, 1), self.item(a, 2)
        self.assertIn(1, [number for _voice, number in self.again])  # Video 1 was made again, with the new voice …
        self.assertIn('redone', first)
        self.assertNotIn('redo', first)
        self.assertNotIn('redone', second)  # … video 2 was made with it from the start.
        self.assertNotIn('redo', second)

    def test_without_the_tick_a_finished_video_stays_as_it_is(self):
        a = self.make_link(2)
        self.during[('A', 1)] = lambda: (self.new_voice(a), campaign_redo.remember_follow(a, None))

        def run(config_path, stop):
            return (self.remake(config_path, stop) if config_path.parent.parent.name == 'revisions'
                    else self.video(config_path, stop))
        campaign._run_campaign_locked(a, runner=run)
        self.assertNotIn(1, [number for _voice, number in self.again])
        self.assertNotIn('redo', self.item(a, 1))

    def test_follow_after_finish_never_raises_and_follows_only_the_newest_settings(self):
        a = self.finished(1)
        record = self.new_voice(a)
        campaign_redo.remember_follow(a, 'f'*32)  # Ticked for older settings than the newest ones.
        manifest = campaign.read_json(a/'campaign.json')
        self.assertFalse(campaign_redo.follow_after_finish(a, manifest, manifest['items'][0], a/'jobs'/'0001'))
        self.assertFalse(campaign_redo.follow_after_finish(a, manifest, manifest['items'][0], a/'missing'))
        campaign_redo.remember_follow(a, record['id'])
        self.assertEqual(campaign_redo.follow_settings(a), record['id'])
        campaign_redo.remember_follow(a, None)
        self.assertIsNone(campaign_redo.follow_settings(a))


if __name__ == '__main__':
    import unittest
    unittest.main()
