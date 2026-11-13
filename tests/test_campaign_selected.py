"""Chạy video đã chọn (owner 13/11/2026): pick any videos of a link and run them FIRST with the link's current config; with none picked
the old logic is unchanged. The daily limit does not hold a chosen video back; a finished video already sent to a Page is never made
again. Local fixtures only; no worker process, network, Chrome or AI."""
from pathlib import Path
from unittest.mock import patch

from teodub import campaign, campaign_daily, campaign_selected
from teodub.common import atomic_json
from teodub.campaign_selected import KIND_TEXT, describe, plan
from teodub.pipeline import _job_lock
from tests import test_campaign_redo as redo_fixtures


class Selected(redo_fixtures.Redo):
    def mixed(self, config_path, stop):
        """The runner of a link whose finished videos are being made again: a video asked for again is remade, any other is made."""
        directory = next(path for path in config_path.parents if (path/'campaign.json').is_file())
        item = next(x for x in campaign.read_json(directory/'campaign.json')['items'] if Path(x['job']).resolve() in config_path.parents)
        return (self.remake if item.get('redo') else self.video)(config_path, stop)

    def order(self):
        return [number for _, number in self.videos]

    def manifest(self, directory):
        return campaign.read_json(directory/'campaign.json')


class MarkTests(Selected):
    def test_each_state_is_prepared_the_way_it_needs_and_the_chosen_go_first_newest_request_first(self):
        a = self.make_link(6)
        self.outcomes[('A', 2)] = dict(status='failed', message='lỗi thử', outputs={})
        campaign._run_campaign_locked(a, runner=self.video, max_videos=3)  # 1 and 3 finished, 2 failed
        manifest = self.manifest(a)
        manifest['items'][3]['status'] = campaign.EXCLUDED       # 4 excluded
        manifest['items'][5]['status'] = 'cancelled'             # 6 stopped
        chosen = campaign.mark_selected(manifest, [3, 2, 4, 5, 6, 1, 99])
        self.assertEqual(chosen, [3, 2, 4, 5, 6, 1])
        by = {x['number']: x for x in manifest['items']}
        self.assertIn('redo', by[3])                              # Finished: made again, the old output stays.
        self.assertIn('redo', by[1])
        self.assertTrue(by[2]['retry_once'])                      # Failed: one more try.
        self.assertEqual(by[4]['status'], 'pending')              # Excluded: brought back.
        self.assertEqual(by[5]['status'], 'pending')
        self.assertEqual(by[6]['status'], 'cancelled')
        self.assertEqual(manifest['priority'], [3, 2, 4, 5, 6, 1])
        again = campaign.mark_selected(manifest, [5])             # The newest request goes to the front.
        self.assertEqual((again, manifest['priority']), ([5], [5, 3, 2, 4, 6, 1]))

    def test_no_post_a_review_and_a_missing_number_are_not_marked(self):
        a = self.finished(3)
        manifest = self.manifest(a)
        manifest['items'][0]['no_post'] = 'x'
        manifest['items'][1]['status'] = 'review_required'
        self.assertEqual(campaign.mark_selected(manifest, [1, 2, 77]), [])
        self.assertNotIn('priority', manifest)
        self.assertNotIn('redo', manifest['items'][0])

    def test_a_finished_or_attention_link_reopens_so_it_can_run(self):
        a = self.finished(2)
        manifest = self.manifest(a)
        self.assertEqual(manifest['status'], 'completed')
        campaign.mark_selected(manifest, [2])
        self.assertEqual(manifest['status'], 'paused')
        self.assertIn('chạy trước', manifest['message'])

    def test_the_numbers_still_listed_are_those_that_need_work(self):
        manifest = dict(items=[dict(number=1, status='pending'), dict(number=2, status='completed'), dict(number=3, status='failed')],
                        priority=[2, 1, 3, 'x', 9])
        self.assertEqual([x['number'] for x in campaign.chosen_items(manifest)], [1])
        self.assertEqual(manifest['priority'], [1])  # Finished, failed (not asked again) and unknown ones are dropped.


class RunTests(Selected):
    def test_without_a_choice_nothing_changes(self):
        a = self.make_link(3)
        campaign._run_campaign_locked(a, runner=self.video)
        self.assertEqual(self.order(), [1, 2, 3])
        self.assertNotIn('priority', self.manifest(a))

    def test_the_chosen_videos_are_made_first_in_the_order_chosen_then_the_rest_as_usual(self):
        a = self.make_link(5)
        campaign_selected.apply(a, [4, 2])
        self.assertEqual(self.manifest(a)['priority'], [4, 2])
        campaign._run_campaign_locked(a, runner=self.video)
        self.assertEqual(self.order(), [4, 2, 1, 3, 5])
        self.assertNotIn('priority', self.manifest(a))  # Each one left the front of the line when it was done.

    def test_a_failed_one_is_tried_once_and_the_other_failed_ones_stay_as_they_were(self):
        a = self.make_link(3)
        self.outcomes[('A', 1)] = dict(status='failed', message='lỗi', outputs={})
        self.outcomes[('A', 2)] = dict(status='failed', message='lỗi', outputs={})
        campaign._run_campaign_locked(a, runner=self.video)
        self.assertEqual(self.order(), [1, 2, 3])
        self.videos.clear()
        campaign_selected.apply(a, [2])
        campaign._run_campaign_locked(a, runner=self.video)
        self.assertEqual(self.order(), [2])                       # Only the chosen one, once.
        self.assertEqual(self.numbers(a), {1: 'failed', 2: 'failed', 3: 'completed'})
        self.assertNotIn('priority', self.manifest(a))
        self.videos.clear()
        campaign._run_campaign_locked(a, runner=self.video)
        self.assertEqual(self.order(), [])                        # Not tried again by itself.

    def test_a_finished_video_is_made_again_with_the_old_one_kept_and_the_others_untouched(self):
        a = self.finished(3)
        old = self.item(a, 2)['outputs']['video']
        old_bytes = open(old, 'rb').read()
        self.new_voice(a)
        campaign_selected.apply(a, [2])
        self.assertEqual(self.item(a, 2)['status'], 'completed')
        result = campaign._run_campaign_locked(a, runner=self.mixed)
        self.assertEqual([number for _, number in self.again], [2])  # Only the video chosen was made again.
        self.assertTrue(self.item(a, 2)['outputs']['video'].endswith('_lan-2.mp4'))
        self.assertEqual(open(old, 'rb').read(), old_bytes)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(self.item(a, 1)['outputs']['video'].count('_lan-'), 0)

    def test_a_stop_keeps_the_chosen_video_at_the_front_for_the_next_run(self):
        a = self.make_link(3)
        campaign_selected.apply(a, [3])
        self.outcomes[('A', 3)] = dict(status='cancelled', message='Đã nhận yêu cầu Dừng.', outputs={})
        campaign._run_campaign_locked(a, runner=self.video)
        self.assertEqual(self.manifest(a)['priority'], [3])
        del self.outcomes[('A', 3)]
        self.videos.clear()
        campaign._run_campaign_locked(a, runner=self.video)
        self.assertEqual(self.order(), [3, 1, 2])

    def test_a_request_left_while_the_link_runs_is_taken_before_its_next_video(self):
        a = self.make_link(4)
        self.during[('A', 1)] = lambda: campaign_selected.apply(a, [4])
        with _job_lock(a):
            pass
        campaign._run_campaign_locked(a, runner=self.video)
        self.assertEqual(self.order(), [1, 4, 2, 3])
        self.assertEqual(campaign_selected.pending_requests(a), 0)

    def test_the_desktop_marks_now_or_leaves_a_request_for_a_running_worker(self):
        a = self.make_link(3)
        with _job_lock(a):
            self.assertEqual(campaign_selected.apply(a, [2]), dict(mode='request', changed=[]))
        self.assertEqual(campaign_selected.pending_requests(a), 1)
        self.assertNotIn('priority', self.manifest(a))
        self.assertEqual(campaign_selected.apply(a, [2, 3]), dict(mode='now', changed=[2, 3]))
        with self.assertRaises(ValueError):
            campaign_selected.apply(a, [])


class DailyLimitTests(Selected):
    def test_a_chosen_video_runs_even_when_the_days_number_is_reached_and_the_normal_ones_still_rest(self):
        a = self.make_link(4)
        campaign._run_campaign_locked(a, runner=self.video, max_videos=1)   # Video 1 finished today.
        campaign_daily.write_limit(a, '1')
        self.videos.clear()
        result = campaign._run_campaign_locked(a, runner=self.video)
        self.assertEqual(self.order(), [])                                  # Without a choice: rest, as before.
        self.assertTrue(result['message'].startswith(campaign_daily.REST))
        campaign_selected.apply(a, [3])
        result = campaign._run_campaign_locked(a, runner=self.video)
        self.assertEqual(self.order(), [3])                                 # The chosen one ran; video 2 did not.
        self.assertEqual(self.numbers(a)[2], 'pending')
        self.assertTrue(result['message'].startswith(campaign_daily.REST))
        self.assertIsNotNone(result.get('resume_after'))


class PlanTests(Selected):
    def test_the_plan_says_what_each_chosen_video_does_and_changes_nothing(self):
        a = self.make_link(6)
        self.outcomes[('A', 2)] = dict(status='failed', message='lỗi', outputs={})
        campaign._run_campaign_locked(a, runner=self.video, max_videos=3)
        manifest = self.manifest(a)
        manifest['items'][3]['status'] = campaign.EXCLUDED
        campaign.mark_redo(manifest, [3])
        manifest['items'][4]['status'] = 'cancelled'
        atomic_json(a/'campaign.json', manifest)
        self.new_voice(a)
        before = (a/'campaign.json').read_bytes()
        found = plan(a, [1, 2, 3, 4, 5, 6, 77])
        kinds = {entry['number']: entry['kind'] for entry in found['entries']}
        self.assertEqual(kinds, {1: 'redo', 2: 'retry', 3: 'queued', 4: 'restore', 5: 'run', 6: 'run'})
        self.assertEqual(found['numbers'], [1, 2, 3, 4, 5, 6])
        self.assertEqual((a/'campaign.json').read_bytes(), before)

    def test_a_finished_video_already_sent_to_a_page_a_near_one_or_a_no_post_is_skipped_with_its_reason(self):
        a = self.finished(3)
        self.new_voice(a)
        fake = dict(entries=[dict(number=1, kind='skip', reason='Đã đăng hoặc đã gửi lên Page; bản trên Facebook không đổi được.', scheduled=False),
                             dict(number=2, kind='skip', reason='Sắp đến giờ đăng (dưới 30 phút); để Tèo đăng bản hiện có.', scheduled=False),
                             dict(number=3, kind='redo', reason='', scheduled=True)])
        with patch.object(campaign_selected, 'build_plan', return_value=fake):
            found = plan(a, [1, 2, 3])
        self.assertEqual([(e['number'], e['kind']) for e in found['entries']], [(1, 'skip'), (2, 'skip'), (3, 'redo')])
        self.assertEqual(found['numbers'], [3])
        text = describe(found)
        self.assertIn('Video 1: bỏ qua. Đã đăng hoặc đã gửi lên Page', text)
        self.assertIn('Video 2: bỏ qua. Sắp đến giờ đăng', text)
        self.assertIn(KIND_TEXT['redo'], text)
        self.assertIn('lịch đăng chưa gửi', text)

    def test_when_the_page_schedules_cannot_be_read_no_finished_video_is_touched_blindly(self):
        a = self.finished(2)
        self.new_voice(a)
        with patch.object(campaign_selected, 'build_plan', side_effect=OSError('busy')):
            found = plan(a, [1])
        self.assertEqual((found['entries'][0]['kind'], found['numbers']), ('skip', []))
        self.assertIn('Chưa đọc được lịch đăng', found['entries'][0]['reason'])

    def test_a_finished_video_is_not_made_again_when_the_link_has_no_newer_config_or_it_is_already_made_with_it(self):
        a = self.finished(3)
        found = plan(a, [1, 2])
        self.assertEqual([e['kind'] for e in found['entries']], ['skip', 'skip'])  # The link has no newer config: same result.
        self.assertIn('Link chưa có cấu hình mới', found['entries'][0]['reason'])
        self.assertEqual(found['numbers'], [])
        self.new_voice(a)
        self.assertEqual([e['kind'] for e in plan(a, [1, 2])['entries']], ['redo', 'redo'])
        campaign_selected.apply(a, [2])
        campaign._run_campaign_locked(a, runner=self.mixed)      # Video 2 is now made with the link's current config.
        found = plan(a, [1, 2])
        self.assertEqual([(e['number'], e['kind']) for e in found['entries']], [(1, 'redo'), (2, 'skip')])
        self.assertIn('Đã làm bằng đúng cấu hình hiện tại của link', found['entries'][1]['reason'])

    def test_a_video_waiting_for_approval_is_skipped(self):
        a = self.finished(2)
        manifest = self.manifest(a)
        manifest['items'][0]['status'] = 'review_required'
        atomic_json(a/'campaign.json', manifest)
        found = plan(a, [1, 2])
        self.assertEqual(found['entries'][0]['kind'], 'skip')
        self.assertIn('duyệt', found['entries'][0]['reason'])


if __name__ == '__main__':
    import unittest
    unittest.main()
