"""Số video mỗi ngày (owner 07/11/2026, counting changed 10/11/2026): a link makes only N videos a day, new ones and ones made again
together, and rests until 0h; empty = all of them. Local fixtures only; no worker process, network or AI."""
from datetime import datetime, timedelta
import time
import unittest

from teodub import campaign, campaign_batch, campaign_daily, campaign_redo
from teodub.campaign_daily import REST, finished_day, midnight_after, parse_limit, read_limit, write_limit
from teodub.common import atomic_json
from test_campaign_rotation import Fixture
from test_campaign_voice_ui import LinkFixture


def stamp(days_ago=0, hour=10):
    moment = datetime.now().astimezone().replace(hour=hour, minute=0, second=0, microsecond=0)-timedelta(days=days_ago)
    return moment.isoformat(timespec='seconds')


class LimitFileTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)

    def test_a_number_is_saved_and_empty_removes_it(self):
        self.assertIsNone(read_limit(self.directory))
        self.assertEqual(write_limit(self.directory, ' 2 '), 2)
        self.assertEqual(read_limit(self.directory), 2)
        self.assertIsNone(write_limit(self.directory, ''))
        self.assertIsNone(read_limit(self.directory))
        self.assertFalse((self.directory/campaign_daily.DAILY_FILE).exists())

    def test_wrong_input_is_refused_and_the_saved_number_stays(self):
        write_limit(self.directory, '3')
        for bad in ('0', '-1', '1000', 'abc', '2.5', '٢'):
            with self.assertRaises(ValueError, msg=bad):
                write_limit(self.directory, bad)
        self.assertEqual(read_limit(self.directory), 3)
        self.assertEqual(parse_limit('999'), 999)

    def test_a_damaged_file_means_no_limit_never_a_stopped_link(self):
        (self.directory/campaign_daily.DAILY_FILE).write_text('{not json', encoding='utf-8')
        self.assertIsNone(read_limit(self.directory))
        (self.directory/campaign_daily.DAILY_FILE).write_text('{"per_day": "2"}', encoding='utf-8')
        self.assertIsNone(read_limit(self.directory))

    def test_new_videos_finished_today_and_videos_made_again_today_both_count(self):
        items = [dict(status='completed', completed_at=stamp(0)),
                 dict(status='completed', completed_at=stamp(1), first_completed_at=stamp(1)),
                 dict(status='failed', completed_at=stamp(0)),
                 dict(status='pending'),
                 # Made again today, first made yesterday: counts for yesterday, not today.
                 dict(status='completed', completed_at=stamp(0), first_completed_at=stamp(1), redone=dict(at=stamp(0))),
                 # Made again before the day was stored: not counted at all.
                 dict(status='completed', completed_at=stamp(0), redone=dict(at=stamp(0))),
                 dict(status='completed', completed_at=stamp(0), first_completed_at=stamp(0))]
        self.assertEqual([finished_day(item) is not None for item in items], [True, True, False, False, True, False, True])
        # New today: the 2nd and the last (the first has no first_completed_at day stored but was completed today).
        self.assertEqual(campaign_daily.counts_today(dict(items=items)), (2, 2))  # Made again today: the two redone items.
        self.assertEqual(campaign_daily.done_today(dict(items=items)), 4)
        self.assertEqual(campaign_daily.counts_today(dict(items=[dict(status='completed', redone=dict(at=stamp(1)))])), (0, 0))

    def test_the_day_ends_at_the_next_midnight(self):
        now = datetime.now().astimezone().replace(hour=23, minute=59, second=0, microsecond=0).timestamp()
        until = datetime.fromtimestamp(midnight_after(now))
        self.assertEqual((until.hour, until.minute), (0, 0))
        self.assertEqual(until.date(), datetime.fromtimestamp(now).date()+timedelta(days=1))


class WorkerTests(Fixture):
    def limit(self, directory, number):
        write_limit(directory, str(number))

    def test_a_link_makes_only_its_videos_for_today_then_rests_until_midnight(self):
        a = self.make('A', 4)
        self.limit(a, 2)
        manifest = campaign._run_campaign_locked(a, runner=self.video)
        self.assertEqual(self.videos, [('A', 1), ('A', 2)])
        self.assertEqual([item['status'] for item in manifest['items']], ['completed', 'completed', 'pending', 'pending'])
        self.assertTrue(all(item.get('first_completed_at') for item in manifest['items'][:2]))
        self.assertEqual(manifest['status'], 'paused')
        self.assertTrue(manifest['message'].startswith(REST))
        self.assertIn('2/2', manifest['message'])
        self.assertEqual(manifest['resume_after'], midnight_after())
        self.assertFalse(campaign.auto_resume_due(manifest, time.time()))
        self.assertTrue(campaign.auto_resume_due(manifest, midnight_after()+1))  # The desktop starts it again from this.
        self.assertFalse(manifest['resume_retry_failed'])

    def test_no_number_means_all_of_them_as_before(self):
        a = self.make('A', 4)
        manifest = campaign._run_campaign_locked(a, runner=self.video)
        self.assertEqual(self.videos, [('A', 1), ('A', 2), ('A', 3), ('A', 4)])
        self.assertEqual(manifest['status'], 'completed')

    def test_pressing_chay_again_the_same_day_makes_nothing_more(self):
        a = self.make('A', 4)
        self.limit(a, 2)
        campaign._run_campaign_locked(a, runner=self.video)
        manifest = campaign._run_campaign_locked(a, runner=self.video)
        self.assertEqual(self.videos, [('A', 1), ('A', 2)])
        self.assertTrue(manifest['message'].startswith(REST))

    def test_raising_the_number_lets_the_link_make_more_at_once(self):
        a = self.make('A', 4)
        self.limit(a, 1)
        campaign._run_campaign_locked(a, runner=self.video)
        self.limit(a, 3)
        campaign._run_campaign_locked(a, runner=self.video)
        self.assertEqual(self.videos, [('A', 1), ('A', 2), ('A', 3)])

    def test_the_next_day_the_link_makes_its_next_videos(self):
        a = self.make('A', 4)
        self.limit(a, 2)
        campaign._run_campaign_locked(a, runner=self.video)
        manifest = campaign.read_json(a/'campaign.json')
        for item in manifest['items'][:2]:  # The day passes.
            item['completed_at'] = item['first_completed_at'] = stamp(1)
        atomic_json(a/'campaign.json', manifest)
        manifest = campaign._run_campaign_locked(a, runner=self.video)
        self.assertEqual(self.videos, [('A', 1), ('A', 2), ('A', 3), ('A', 4)])
        self.assertEqual(manifest['status'], 'completed')

    def test_videos_made_again_count_towards_the_day_in_the_order_they_come(self):
        a = self.make('A', 5)
        campaign._run_campaign_locked(a, runner=self.video, max_videos=3)  # Videos 1-3 finished earlier.
        manifest = campaign.read_json(a/'campaign.json')
        for item in manifest['items'][:3]:
            item['completed_at'] = item['first_completed_at'] = stamp(1)
        atomic_json(a/'campaign.json', manifest)
        self.videos.clear()
        campaign_redo.apply(a, [1, 2, 3])
        self.limit(a, 2)
        manifest = campaign._run_campaign_locked(a, runner=self.video)
        # The two a day are videos made again (they come first); the new videos wait for tomorrow.
        self.assertEqual(self.videos, [('A', 1), ('A', 2)])
        self.assertEqual(campaign_daily.counts_today(manifest), (0, 2))
        self.assertEqual([bool(item.get('redo')) for item in manifest['items'][:3]], [False, False, True])
        self.assertEqual(manifest['status'], 'paused')
        self.assertTrue(manifest['message'].startswith(REST))
        self.assertIn('2/2', manifest['message'])
        self.assertEqual(manifest['resume_after'], midnight_after())

    def test_new_and_redone_videos_share_the_same_number_when_the_day_has_both(self):
        a = self.make('A', 5)
        campaign._run_campaign_locked(a, runner=self.video, max_videos=2)
        manifest = campaign.read_json(a/'campaign.json')
        for item in manifest['items'][:2]:
            item['completed_at'] = item['first_completed_at'] = stamp(1)
        atomic_json(a/'campaign.json', manifest)
        self.videos.clear()
        campaign_redo.apply(a, [1])
        self.limit(a, 3)
        manifest = campaign._run_campaign_locked(a, runner=self.video)
        self.assertEqual(self.videos, [('A', 1), ('A', 3), ('A', 4)])  # One made again, then two new ones: three in all.
        self.assertEqual(campaign_daily.counts_today(manifest), (2, 1))
        self.assertIn('3/3', manifest['message'])

    def test_no_number_still_makes_every_video_made_again_first(self):
        a = self.make('A', 3)
        campaign._run_campaign_locked(a, runner=self.video, max_videos=2)
        self.videos.clear()
        campaign_redo.apply(a, [1, 2])
        manifest = campaign._run_campaign_locked(a, runner=self.video)
        self.assertEqual(self.videos, [('A', 1), ('A', 2), ('A', 3)])
        self.assertEqual(manifest['status'], 'completed')

    def test_a_failed_video_does_not_count_for_the_day(self):
        a = self.make('A', 5)
        self.limit(a, 2)
        self.outcomes[('A', 1)] = dict(status='failed', message='Lỗi thử.')
        manifest = campaign._run_campaign_locked(a, runner=self.video)
        self.assertEqual(self.videos, [('A', 1), ('A', 2), ('A', 3)])
        self.assertEqual([item['status'] for item in manifest['items']], ['failed', 'completed', 'completed', 'pending', 'pending'])
        self.assertTrue(manifest['message'].startswith(REST))

    def test_the_number_changed_while_a_video_is_made_applies_before_the_next_one(self):
        a = self.make('A', 4)
        self.limit(a, 3)
        self.during[('A', 1)] = lambda: self.limit(a, 1)
        manifest = campaign._run_campaign_locked(a, runner=self.video)
        self.assertEqual(self.videos, [('A', 1)])  # Video 1 was finished, never cut off.
        self.assertEqual(manifest['items'][0]['status'], 'completed')
        self.assertTrue(manifest['message'].startswith(REST))

    def test_a_stop_is_still_a_stop(self):
        a = self.make('A', 4)
        self.limit(a, 1)
        stop = self.root/'queue-stop.request'
        self.during[('A', 1)] = stop.touch
        manifest = campaign._run_campaign_locked(a, external_stop=stop, runner=self.video)
        self.assertEqual(manifest['status'], 'paused')
        self.assertNotIn('resume_after', manifest)
        self.assertFalse(manifest['message'].startswith(REST))

    def test_the_last_video_of_a_link_completes_it_instead_of_resting(self):
        a = self.make('A', 2)
        self.limit(a, 2)
        manifest = campaign._run_campaign_locked(a, runner=self.video)
        self.assertEqual(manifest['status'], 'completed')


class RotationTests(Fixture):
    def rows(self, directory):
        return {row['name']: row for row in campaign_batch.read_batch(directory)['campaigns']}

    def test_a_link_that_has_enough_rests_alone_and_the_others_keep_their_turns(self):
        a, b = self.make('A', 3), self.make('B', 3, start=200)
        write_limit(a, '1')
        directory = campaign_batch.create_batch(self.root, [a, b], order='rotate')
        queue = campaign_batch.run_batch(directory, runner=self.turn)
        self.assertEqual(self.videos, [('A', 1), ('B', 1), ('B', 2), ('B', 3)])
        rows = {row['name']: row for row in queue['campaigns']}
        self.assertEqual(rows['B']['status'], 'completed')
        # Only the resting link is left: the rotation pauses on it, and the desktop starts it at 0h.
        self.assertEqual((queue['status'], rows['A']['status']), ('paused', 'paused'))
        self.assertTrue(rows['A']['message'].startswith(REST))
        self.assertGreaterEqual(rows['A']['wait_until'], midnight_after()-1)
        self.assertTrue(campaign.auto_resume_due(campaign.read_json(a/'campaign.json'), midnight_after()+1))

    def test_after_midnight_the_link_comes_back_in_its_turn(self):
        a, b = self.make('A', 3), self.make('B', 1, start=200)
        write_limit(a, '1')
        directory = campaign_batch.create_batch(self.root, [a, b], order='rotate')
        campaign_batch.run_batch(directory, runner=self.turn)
        manifest = campaign.read_json(a/'campaign.json')
        manifest['items'][0]['completed_at'] = manifest['items'][0]['first_completed_at'] = stamp(1)  # The day passes.
        atomic_json(a/'campaign.json', manifest)
        queue = campaign_batch.read_batch(directory)
        for row in queue['campaigns']:
            row['wait_until'] = time.time()-1
        atomic_json(directory/'queue.json', queue)
        campaign_batch.run_batch(directory, runner=self.turn)
        self.assertEqual(self.videos, [('A', 1), ('B', 1), ('A', 2)])


class LinkTabTests(LinkFixture):
    def test_the_box_is_on_the_tab_empty_and_saves_a_number(self):
        self.assertEqual(self.link.daily.get(), '')
        self.assertIn('Để trống', self.link.daily_note.get())
        self.assertEqual(str(self.link.daily_button['state']), 'disabled')
        self.link.daily.set('2')
        self.link.daily_edited()
        self.assertEqual(str(self.link.daily_button['state']), 'normal')
        self.link.save_daily()
        self.assertEqual(read_limit(self.directory), 2)
        self.assertEqual(self.link.daily.get(), '2')
        self.assertIn('Hôm nay đã xong 0/2', self.link.daily_note.get())
        self.assertIn('2 video', self.link.message.get())

    def test_a_wrong_number_is_refused_and_empty_removes_the_limit(self):
        write_limit(self.directory, '2')
        self.link.refresh(full=True)
        self.assertEqual(self.link.daily.get(), '2')
        self.link.daily.set('abc')
        self.link.save_daily()
        self.assertIn('Chưa lưu', self.link.message.get())
        self.assertEqual(read_limit(self.directory), 2)
        self.link.daily.set('')
        self.link.save_daily()
        self.assertIsNone(read_limit(self.directory))
        self.assertIn('Đã bỏ giới hạn', self.link.message.get())

    def test_a_number_being_typed_is_not_overwritten_by_the_refresh(self):
        write_limit(self.directory, '2')
        self.link.refresh(full=True)
        self.link.daily.set('5')
        self.link.daily_edited()
        self.link.refresh(full=True)
        self.assertEqual(self.link.daily.get(), '5')
        self.assertEqual(self.link.daily_note.get(), 'Chưa lưu.')

    def test_the_tab_says_when_the_link_has_made_enough_today(self):
        write_limit(self.directory, '1')
        manifest = campaign.read_json(self.directory/'campaign.json')
        manifest['items'][0].update(status='completed', outputs=dict(video='x.mp4'), completed_at=stamp(0), first_completed_at=stamp(0))
        atomic_json(self.directory/'campaign.json', manifest)
        self.link.refresh(full=True)
        self.assertIn('Hôm nay đã xong 1/1', self.link.daily_note.get())
        self.assertIn('nghỉ tới 0h', self.link.daily_note.get())

    def test_the_note_says_how_many_were_made_again(self):
        write_limit(self.directory, '3')
        manifest = campaign.read_json(self.directory/'campaign.json')
        manifest['items'][0].update(status='completed', outputs=dict(video='x.mp4'), completed_at=stamp(0), first_completed_at=stamp(0))
        manifest['items'][1].update(status='completed', outputs=dict(video='y.mp4'), completed_at=stamp(0), first_completed_at=stamp(1),
                                    redone=dict(at=stamp(0)))
        atomic_json(self.directory/'campaign.json', manifest)
        self.link.refresh(full=True)
        self.assertIn('Hôm nay đã xong 2/3 video (1 làm lại, 1 mới).', self.link.daily_note.get())

    def test_a_link_resting_until_midnight_is_not_called_stopped(self):
        write_limit(self.directory, '1')
        manifest = campaign.read_json(self.directory/'campaign.json')
        manifest['items'][0].update(status='completed', outputs=dict(video='x.mp4'), completed_at=stamp(0), first_completed_at=stamp(0))
        until = midnight_after()
        manifest.update(status='paused', message=campaign_daily.rest_message(campaign_daily.summary(self.directory, manifest)),
                        resume_after=until)
        atomic_json(self.directory/'campaign.json', manifest)
        self.link.refresh(full=True)
        self.assertIn('Đang nghỉ: đã đủ 1/1 video hôm nay', self.link.status.get())
        self.assertIn(datetime.fromtimestamp(until).strftime('%d/%m'), self.link.status.get())
        self.assertNotIn('tạm dừng', self.link.status.get())
        self.assertIn('nghỉ tới 0h', self.link.area.tabs.tab(self.link, 'text'))
        manifest.update(message='Đã nhận yêu cầu Dừng.')  # A real stop keeps its old words.
        manifest.pop('resume_after')
        atomic_json(self.directory/'campaign.json', manifest)
        self.link.refresh(full=True)
        self.assertIn('tạm dừng', self.link.status.get())
        self.assertIn('tạm dừng', self.link.area.tabs.tab(self.link, 'text'))


if __name__ == '__main__':
    unittest.main()
