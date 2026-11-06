"""Áp dụng cho cả video đã xong chưa đăng (owner 06/11/2026, choices 1B 2A 3A): finished videos that are not posted yet are made
again in the order their Page schedules come (soonest first, looked at again before every video), one with a schedule already
sent is left alone, and a video that would come out the same is not made again. Local fixtures only; no worker process, Chrome or AI."""
import time

from teodub import campaign, campaign_redo
from teodub.campaign_settings import made_with
from teodub.campaign_subtitle_redo_ui import describe
from teodub.common import atomic_json
from tests.test_campaign_redo import PageFixture


class OrderTests(PageFixture):
    def run_remakes(self, a, hook=None):
        def runner(config_path, stop):
            outcome = self.remake(config_path, stop)
            if hook:
                hook(self.again[-1][1])
            return outcome
        return campaign._run_campaign_locked(a, runner=runner)

    def test_the_soonest_schedule_is_made_first_then_the_others_in_number_order(self):
        a = self.finished(6)
        ids = {n: self.schedule(a, n, h) for n, h in ((1, 40), (2, 20), (3, 30), (4, 10), (6, 50))}
        for ident in ids.values():
            self.controller.start(ident)
        self.new_voice(a)
        campaign_redo.apply(a, [1, 2, 3, 4, 5, 6])
        self.run_remakes(a)
        self.assertEqual([number for _voice, number in self.again], [4, 2, 3, 1, 6, 5])  # 5 has no schedule: after the scheduled ones.

    def test_a_schedule_that_was_not_enabled_still_counts_and_a_cancelled_or_late_one_does_not(self):
        a = self.finished(4)
        ids = {n: self.schedule(a, n, h) for n, h in ((1, 10), (2, 5), (3, 6), (4, 7))}
        self.store.update(ids[2], state='cancelled')
        self.store.update(ids[3], due_at=time.time()-100)
        manifest = campaign.read_json(a/'campaign.json')
        due = campaign_redo.next_due(self.root, manifest['id'])
        self.assertEqual(sorted(due), [1, 4])
        self.assertLess(due[4], due[1])
        self.assertEqual(campaign_redo.next_due(self.root, 'f'*32), {})

    def test_the_order_is_looked_at_again_before_every_video(self):
        a = self.finished(4)
        ids = {n: self.schedule(a, n, h) for n, h in ((1, 40), (2, 20), (3, 30), (4, 10))}
        self.new_voice(a)
        campaign_redo.apply(a, [1, 2, 3, 4])

        def moved(number):
            if number == 4:  # Video 1 is given an earlier time while the link is running.
                self.store.update(ids[1], due_at=time.time()+15*3600)
        self.run_remakes(a, moved)
        self.assertEqual([number for _voice, number in self.again], [4, 1, 2, 3])

    def test_a_video_asked_for_while_the_link_runs_joins_the_order(self):
        a = self.finished(3)
        ids = {n: self.schedule(a, n, h) for n, h in ((1, 30), (2, 20), (3, 10))}
        self.new_voice(a)
        campaign_redo.apply(a, [1])

        def more(number):
            if number == 1:
                campaign_redo.apply(a, [2, 3])
        self.run_remakes(a, more)
        self.assertEqual([number for _voice, number in self.again], [1, 3, 2])

    def test_an_unreadable_schedule_file_leaves_the_usual_order(self):
        a = self.finished(3)
        (self.root/'page-publishing'/'schedules.sqlite3').write_bytes(b'not a database')
        self.new_voice(a)
        campaign_redo.apply(a, [3, 1, 2])
        self.run_remakes(a)
        self.assertEqual([number for _voice, number in self.again], [1, 2, 3])


class MadeWithTests(PageFixture):
    BASE = dict(burn_subtitles=False, voice='Mai Anh', voice_provider='local', original_gain=.1, voice_gain=1.0)

    def test_the_same_picture_voice_and_levels_come_out_the_same(self):
        config = dict(self.BASE)
        self.assertTrue(made_with(config, dict(self.BASE, subtitle_box='white')))  # No Sub in the picture: the box does not matter.
        self.assertTrue(made_with(config, dict(self.BASE, voice_gain=1.0, tts_model='v3turbo')))
        for change in (dict(burn_subtitles=True), dict(voice='Trúc Ly'), dict(original_gain=.3), dict(voice_gain=1.4)):
            with self.subTest(change=change):
                self.assertFalse(made_with(config, dict(self.BASE, **change)))
        burned = dict(self.BASE, burn_subtitles=True, subtitle_box='white')
        self.assertTrue(made_with(burned, dict(burned)))
        self.assertFalse(made_with(burned, dict(burned, subtitle_box='black')))
        self.assertFalse(made_with(burned, dict(burned, subtitle_size=20)))

    def test_an_unreadable_config_counts_as_different(self):
        self.assertFalse(made_with(dict(self.BASE, burn_subtitles='yes'), self.BASE))
        self.assertFalse(made_with(self.BASE, dict(self.BASE, voice_gain=9)))

    def test_the_plan_skips_videos_that_would_come_out_the_same(self):
        a = self.finished(3)
        same = lambda config: made_with(config, dict(config))  # Every video is already made with exactly this.
        plan = campaign_redo.build_plan(a, unchanged=same)
        self.assertEqual(plan['numbers'], [])
        self.assertIn('Đã có đúng Sub', plan['entries'][0]['reason'])
        different = lambda config: made_with(config, dict(config, burn_subtitles=True))
        self.assertEqual(campaign_redo.build_plan(a, unchanged=different)['numbers'], [1, 2, 3])

    def test_the_check_text_lists_the_order_and_the_reasons(self):
        a = self.finished(4)
        for n, h in ((2, 20), (3, 10)):
            self.controller.start(self.schedule(a, n, h))
        manifest = campaign.read_json(a/'campaign.json')
        manifest['items'][3]['no_post'] = 'x'
        atomic_json(a/'campaign.json', manifest)
        plan = campaign_redo.build_plan(a)
        due = campaign_redo.next_due(self.root, manifest['id'])
        order = sorted(((n, due[n]) for n in plan['numbers'] if n in due), key=lambda row: row[1])
        text = describe(plan, order)
        self.assertIn('Sẽ làm lại:', text)
        self.assertLess(text.index('1. Video 3'), text.index('2. Video 2'))
        self.assertIn('Bỏ qua:', text)
