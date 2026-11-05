"""Finished videos made again with a new voice (owner 05/11/2026, choices 1A 2A 3A): the old output is kept and the new one is
written beside it, a failure leaves the old video as it was, videos already sent to a Page are left alone, and a Page schedule
that was never sent follows the new file with the same time and caption. Local fixtures only; no worker process, Chrome or AI."""
from pathlib import Path
import time
from types import SimpleNamespace
from unittest.mock import Mock

from teodub import campaign, campaign_redo
from teodub.campaign_settings import save_next_voice_settings
from teodub.common import atomic_json, hash_file
from teodub.facebook_accounts import FacebookAccounts
from teodub.page_schedule import PageScheduleController, campaign_video_source
from teodub.page_schedule_redo import follow_new_files
from teodub.pipeline import _job_lock
from teodub.campaign_tracking import tracking_row
from tests.test_campaign_exclude import Base, link

NEW_VOICE = dict(voice_provider='local', voice='Trúc Ly', reference_audio='', reference_sha256='',
                 reading_style='legacy', voice_speed=1.0, tts_model='v3turbo')


class Redo(Base):
    def setUp(self):
        super().setUp()
        self.again = []   # (voice, video number) of every video made again

    def new_voice(self, directory):
        return save_next_voice_settings(self.root, directory, NEW_VOICE, allow_finished=True)

    def remake(self, config_path, stop):
        """Like the pipeline: a new revision job never overwrites an output that exists, it writes `_lan-2` beside it."""
        config = campaign.read_json(config_path)
        number = int(config_path.parent.parent.parent.name)
        self.again.append((config['voice'], number))
        outcome = self.outcomes.get(('redo', number))
        if outcome:
            return outcome
        output = Path(config['output_dir'])/(config['campaign_basename']+'_lan-2.mp4')
        output.write_bytes(b'again '+config['voice'].encode()+str(number).encode())
        outputs = dict(video=str(output))
        atomic_json(config_path.parent/'state.json', dict(rendered=dict(outputs=outputs, hashes=dict(video=hash_file(output)))))
        return dict(status='completed', message='Xong lại', outputs=outputs)

    def finished(self, count=3):
        a = self.make_link(count)
        campaign._run_campaign_locked(a, runner=self.video)
        return a

    def item(self, directory, number):
        return next(i for i in campaign.read_json(directory/'campaign.json')['items'] if i['number'] == number)


class MarkTests(Redo):
    def test_only_a_finished_video_with_output_is_marked_and_a_completed_link_reopens(self):
        a = self.make_link(3)
        campaign._run_campaign_locked(a, runner=self.video, max_videos=2)
        manifest = campaign.read_json(a/'campaign.json')
        manifest['items'][1]['no_post'] = 'x'
        self.assertEqual(campaign.mark_redo(manifest, [1, 2, 3, 99]), [1])  # 2 = Không đăng, 3 = not finished
        self.assertIn('previous', manifest['items'][0]['redo'])
        self.assertEqual(campaign.mark_redo(manifest, [1]), [])  # Already asked for.
        done = self.finished(2)
        everything = campaign.read_json(done/'campaign.json')
        self.assertEqual(everything['status'], 'completed')
        campaign.mark_redo(everything, [1, 2])
        self.assertEqual(everything['status'], 'paused')

    def test_the_desktop_marks_now_or_leaves_a_request_for_a_running_worker(self):
        a = self.finished(2)
        with _job_lock(a):
            self.assertEqual(campaign_redo.apply(a, [1]), dict(mode='request', changed=[]))
        self.assertEqual(campaign_redo.pending_requests(a), 1)
        self.assertNotIn('redo', self.item(a, 1))
        self.new_voice(a)
        campaign._run_campaign_locked(a, runner=self.remake)  # The request is taken before the first video.
        self.assertEqual(self.again, [('Trúc Ly', 1)])
        self.assertEqual(campaign_redo.pending_requests(a), 0)
        self.assertEqual(campaign_redo.apply(a, [2]), dict(mode='now', changed=[2]))
        with self.assertRaises(ValueError):
            campaign_redo.apply(a, [])


class MakeAgainTests(Redo):
    def test_the_new_version_is_written_beside_the_old_one_which_is_kept(self):
        a = self.finished(3)
        old = self.item(a, 2)['outputs']['video']
        old_bytes = Path(old).read_bytes()
        self.new_voice(a)
        campaign_redo.apply(a, [2])
        self.assertEqual(self.item(a, 2)['status'], 'completed')  # Still finished, with its old file, until the new one is ready.
        result = campaign._run_campaign_locked(a, runner=self.remake)
        self.assertEqual(self.again, [('Trúc Ly', 2)])  # Only the video asked for.
        item = self.item(a, 2)
        self.assertEqual((item['status'], result['status']), ('completed', 'completed'))
        self.assertTrue(item['outputs']['video'].endswith('_lan-2.mp4'))
        self.assertEqual(Path(old).read_bytes(), old_bytes)  # The old one is untouched.
        self.assertEqual(item['redone']['previous_outputs']['video'], old)
        self.assertNotIn('redo', item)
        self.assertEqual(self.item(a, 1)['outputs']['video'].count('_lan-'), 0)  # The others stay as they were.
        # The finished video is checked again on the next run and still matches its new state.
        campaign._run_campaign_locked(a, runner=self.remake)
        self.assertEqual(self.again, [('Trúc Ly', 2)])

    def test_a_failed_new_version_puts_the_old_finished_video_back_exactly(self):
        a = self.finished(2)
        before = {key: self.item(a, 1).get(key) for key in campaign.REDO_KEEP}
        self.new_voice(a)
        campaign_redo.apply(a, [1])
        self.outcomes[('redo', 1)] = dict(status='failed', message='Codex hết lượt')
        result = campaign._run_campaign_locked(a, runner=self.remake)
        item = self.item(a, 1)
        self.assertEqual(item['status'], 'completed')
        self.assertEqual({key: item.get(key) for key in campaign.REDO_KEEP}, before)
        self.assertEqual(item['redo_failed']['message'], 'Codex hết lượt')
        self.assertNotIn('redo', item)
        self.assertEqual(result['status'], 'completed')  # A failed remake does not turn the link into a failure.
        self.assertIn('chưa xong', tracking_row(item)[6])

    def test_stopping_during_a_remake_keeps_the_old_video_and_pauses_the_link(self):
        a = self.finished(2)
        self.new_voice(a)
        campaign_redo.apply(a, [1, 2])
        self.outcomes[('redo', 1)] = dict(status='cancelled', message='')
        result = campaign._run_campaign_locked(a, runner=self.remake)
        self.assertEqual(result['status'], 'paused')
        self.assertEqual([item['status'] for item in campaign.read_json(a/'campaign.json')['items']], ['completed', 'completed'])
        self.assertEqual(self.again, [('Trúc Ly', 1)])  # The second was not started.
        self.assertIn('redo', self.item(a, 2))  # Still asked for: the next run makes it.

    def test_a_link_with_nothing_left_accepts_a_new_voice_only_for_remaking(self):
        a = self.finished(2)
        with self.assertRaises(ValueError):
            save_next_voice_settings(self.root, a, NEW_VOICE)
        record = self.new_voice(a)
        self.assertEqual(record['settings']['voice'], 'Trúc Ly')
        self.assertEqual(record['settings']['tts_model'], 'v3turbo')

    def test_the_row_says_the_video_is_being_made_again(self):
        a = self.finished(1)
        self.new_voice(a)
        campaign_redo.apply(a, [1])
        row = tracking_row(self.item(a, 1))
        self.assertEqual((row[3], row[4]), ('Hoàn tất · làm lại giọng', '100%'))


class PageTests(Redo):
    def setUp(self):
        super().setUp()
        self.accounts = FacebookAccounts(self.root)
        self.account = self.accounts.add_account('Fixture A')['id']
        self.page = self.accounts.save_page(self.account, 'Fixture Page', 'facebook.com/profile.php?id=100', '100')['id']
        owner = SimpleNamespace(studio=SimpleNamespace(data_dir=self.root), sessions={}, status=Mock())
        owner.submit_page_browser = Mock(return_value='request')
        self.controller = PageScheduleController(owner)
        self.addCleanup(self.controller.close)
        self.store = self.controller.store

    def schedule(self, directory, number, hours):
        source = campaign_video_source(self.root, directory, number)
        return self.store.create(self.account, self.page, source, f'Caption {number}', '#tag', time.time()+hours*3600)['id']

    def test_the_plan_leaves_sent_and_soon_videos_alone_and_counts_unsent_schedules(self):
        a = self.finished(6)
        armed, saved, sent, soon, _ = (self.schedule(a, n, h) for n, h in ((1, 5), (2, 6), (3, 7), (4, .2), (5, 8)))
        self.controller.start(armed)
        self.controller.start(soon)
        self.store.update(sent, state='needs_attention', attempted_at='synthetic attempted')
        manifest = campaign.read_json(a/'campaign.json')
        manifest['items'][5]['no_post'] = 'x'
        atomic_json(a/'campaign.json', manifest)
        plan = campaign_redo.build_plan(a)
        kinds = {e['number']: (e['kind'], e['scheduled']) for e in plan['entries']}
        self.assertEqual(kinds, {1: ('redo', True), 2: ('redo', True), 3: ('skip', False), 4: ('skip', False),
                                 5: ('redo', True), 6: ('skip', False)})
        reasons = {e['number']: e['reason'] for e in plan['entries']}
        self.assertIn('Đã đăng hoặc đã gửi', reasons[3])
        self.assertIn('Sắp đến giờ đăng', reasons[4])
        self.assertIn('Không đăng', reasons[6])
        self.assertEqual((plan['numbers'], plan['scheduled']), ([1, 2, 5], 3))

    def test_a_schedule_never_sent_follows_the_new_file_with_the_same_time_and_caption(self):
        a = self.finished(3)
        armed, saved, other = (self.schedule(a, n, n+4) for n in (1, 2, 3))
        self.controller.start(armed)
        before = {i: self.store.get(i) for i in (armed, saved, other)}
        self.new_voice(a)
        campaign_redo.apply(a, [1, 2])
        self.assertEqual(follow_new_files(self.store), ([], []))  # Nothing is moved before the new file exists.
        campaign._run_campaign_locked(a, runner=self.remake)
        moved, errors = follow_new_files(self.store)
        self.assertEqual((sorted(moved), errors), (sorted([armed, saved]), []))
        for ident in (armed, saved):
            row, was = self.store.get(ident), before[ident]
            self.assertEqual((row['due_at'], row['state'], row['body'], row['page_key']), (was['due_at'], was['state'], was['body'], was['page_key']))
            self.assertTrue(row['source']['path'].endswith('_lan-2.mp4'))
            self.assertNotEqual((row['post_id'], row['source']['sha256']), (was['post_id'], was['source']['sha256']))
            self.assertEqual(row['snapshot']['media'][0]['path'], row['source']['path'])
            self.assertEqual(self.store.video_replacements(ident)[0]['previous_source'], was['source'])
        self.assertEqual(self.store.get(other), before[other])  # Video 3 was not made again.
        self.assertIn(armed, self.controller.active)
        self.assertEqual(follow_new_files(self.store), ([], []))  # Done once; nothing more to move.
        self.controller.owner.submit_page_browser.assert_not_called()
        self.assertEqual(campaign_video_source(self.root, a, 1)['path'], self.store.get(armed)['source']['path'])

    def test_a_schedule_that_was_touched_is_never_moved(self):
        a = self.finished(2)
        sent, saved = self.schedule(a, 1, 5), self.schedule(a, 2, 6)
        self.store.update(sent, state='needs_attention', attempted_at='synthetic attempted')
        self.new_voice(a)
        campaign_redo.apply(a, [1, 2])  # Forced: the plan would have skipped video 1.
        campaign._run_campaign_locked(a, runner=self.remake)
        before = self.store.get(sent)
        moved, errors = follow_new_files(self.store)
        self.assertEqual((moved, errors), ([saved], []))
        self.assertEqual(self.store.get(sent), before)
        with self.assertRaises(ValueError):
            self.store.replace_video(sent, before, campaign_video_source(self.root, a, 1))

    def test_a_row_changed_after_it_was_read_is_not_moved(self):
        a = self.finished(1)
        ident = self.schedule(a, 1, 5)
        stale = self.store.get(ident)
        self.new_voice(a)
        campaign_redo.apply(a, [1])
        campaign._run_campaign_locked(a, runner=self.remake)
        self.store.update(ident, message='changed meanwhile')
        with self.assertRaises(ValueError):
            self.store.replace_video(ident, stale, campaign_video_source(self.root, a, 1))
        self.assertFalse(self.store.get(ident)['source']['path'].endswith('_lan-2.mp4'))  # Still the old file.
        self.assertEqual(self.store.video_replacements(ident), [])

    def test_a_schedule_due_now_moves_to_the_new_file_before_it_is_checked_for_posting(self):
        a = self.finished(1)
        ident = self.schedule(a, 1, 5)
        self.controller.start(ident)
        self.new_voice(a)
        campaign_redo.apply(a, [1])
        campaign._run_campaign_locked(a, runner=self.remake)
        fresh = self.controller._follow_redone_videos(time.monotonic(), only=ident)
        self.assertTrue(fresh['source']['path'].endswith('_lan-2.mp4'))
        self.assertIsNone(self.controller._follow_redone_videos(time.monotonic(), only=ident))
