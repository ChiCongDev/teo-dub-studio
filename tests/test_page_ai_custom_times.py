"""Owner's own time per video (28/10/2026, `slot_times`), instead of a first-time+gap rhythm.

Real local stores; no inference or Facebook. See tests/test_page_ai_slots.py (fixed rhythm) and
tests/test_page_ai_auto.py (auto rhythm) for the unchanged behaviour this sits beside.
"""
from datetime import datetime
import time
import unittest

from tests import test_page_ai as ai_fixtures
from tests import test_page_ai_auto as auto_fixtures
from teodub import page_ai
from teodub.page_ai import auto_slots, fixed_slots, validate_config
from teodub.page_schedule import LOCAL_TIME, PageScheduleStore


def at(day, hour, minute=0):
    return datetime(2026, 10, day, hour, minute, tzinfo=LOCAL_TIME).timestamp()


def times_config(**values):
    config = dict(campaigns=['a'*32], model='gpt-5.6-luna', effort='low', days=2, videos_per_day=3, texts_per_week=0,
                  interval_days=2, goal='', auto_publish=False, slot_times=['08:00', '12:30', '20:15'])
    config.update(values)
    return config


def auto_times_config(**values):
    config = dict(campaigns=['a'*32], model='gpt-5.6-luna', effort='low', days=7, videos_per_day=3, texts_per_week=0,
                  interval_days=7, goal='', auto_publish=True, auto_campaign=True, slot_times=['08:00', '12:30', '20:15'])
    config.update(values)
    return config


def row(due, kind='video', state='armed'):
    return dict(due_at=due, source=dict(kind=kind), state=state)


class ConfigValidationTests(unittest.TestCase):
    def rejected(self, message, **values):
        with self.subTest(values=values):
            with self.assertRaisesRegex(ValueError, message):
                validate_config(times_config(**values))

    def test_a_valid_list_is_kept_as_is(self):
        self.assertEqual(validate_config(times_config())['slot_times'], ['08:00', '12:30', '20:15'])

    def test_count_must_match_videos_per_day(self):
        self.rejected('đúng số Video', videos_per_day=2)
        self.rejected('đúng số Video', videos_per_day=4)

    def test_times_must_be_ascending_and_distinct(self):
        self.rejected('tăng dần', slot_times=['12:30', '08:00', '20:15'])
        self.rejected('không trùng nhau', slot_times=['08:00', '08:00', '20:15'])

    def test_times_must_be_zero_padded_hh_mm(self):
        self.rejected('HH:MM', slot_times=['8:00', '12:30', '20:15'])
        self.rejected('HH:MM', slot_times=['08:00', '24:00', '20:15'])
        self.rejected('HH:MM', slot_times=['08:00', 12.5, '20:15'])

    def test_text_posts_are_refused(self):
        self.rejected('bài chữ', texts_per_week=1)

    def test_rhythm_and_custom_times_together_are_refused(self):
        config = times_config(slot_start='12:00', slot_gap=60)
        with self.assertRaisesRegex(ValueError, 'Chỉ dùng một cách'):
            validate_config(config)

    def test_auto_mode_with_custom_times_still_needs_auto_publish(self):
        config = auto_times_config(auto_publish=False)
        with self.assertRaisesRegex(ValueError, 'tự đăng'):
            validate_config(config)

    def test_auto_mode_with_custom_times_is_accepted(self):
        self.assertTrue(validate_config(auto_times_config())['auto_campaign'])

    def test_plain_rhythm_configuration_is_unaffected(self):
        config = dict(times_config(), slot_start='12:00', slot_gap=60)
        del config['slot_times']
        self.assertEqual(validate_config(config)['slot_gap'], 60)


class FixedSlotsCustomTimesTests(unittest.TestCase):
    def test_each_day_uses_exactly_the_owners_times(self):
        slots = fixed_slots(times_config(), at(20, 6), [])
        self.assertEqual(slots, [at(20, 8), at(20, 12, 30), at(20, 20, 15), at(21, 8), at(21, 12, 30), at(21, 20, 15)])

    def test_a_time_within_thirty_minutes_of_now_is_dropped(self):
        self.assertEqual(fixed_slots(times_config(), at(20, 7, 45), [])[0], at(20, 12, 30))
        self.assertEqual(fixed_slots(times_config(), at(20, 7, 29), [])[0], at(20, 8))

    def test_slots_near_existing_schedules_are_skipped(self):
        slots = fixed_slots(times_config(), at(20, 6), [at(20, 12, 20)])
        self.assertNotIn(at(20, 12, 30), slots)
        self.assertIn(at(20, 8), slots)


class AutoSlotsCustomTimesTests(unittest.TestCase):
    def test_times_still_ahead_keep_their_own_appointment(self):
        self.assertEqual(auto_slots(auto_times_config(), at(20, 6), [], 3), [at(20, 8), at(20, 12, 30), at(20, 20, 15)])

    def test_a_time_already_passed_today_is_filled_as_soon_as_a_video_is_ready(self):
        # 08:00 and 12:30 passed with nothing posted; a video is ready at 14:00.
        self.assertEqual(auto_slots(auto_times_config(), at(20, 14), [], 1), [at(20, 14) + page_ai.AUTO_LEAD_SECONDS])

    def test_several_owed_times_are_spaced_at_least_fifteen_minutes_apart(self):
        slots = auto_slots(auto_times_config(), at(20, 14), [], 2)
        first = at(20, 14) + page_ai.AUTO_LEAD_SECONDS
        self.assertEqual(slots, [first, first + page_ai.AUTO_DEBT_GAP_SECONDS])
        self.assertEqual(page_ai.AUTO_DEBT_GAP_SECONDS, 900)

    def test_a_future_time_is_not_delayed_by_an_earlier_owed_catch_up(self):
        # 08:00 is owed at 09:00; 12:30 is still ahead and keeps its own time, not 09:00+15min.
        slots = auto_slots(auto_times_config(), at(20, 9), [], 2)
        self.assertEqual(slots, [at(20, 9) + page_ai.AUTO_LEAD_SECONDS, at(20, 12, 30)])

    def test_never_before_the_days_first_time(self):
        self.assertEqual(auto_slots(auto_times_config(), at(20, 5), [], 1), [at(20, 8)])

    def test_the_daily_quota_still_holds_and_the_next_day_starts_at_the_first_time(self):
        rows = [row(at(20, 8)), row(at(20, 12, 30)), row(at(20, 20, 15))]
        self.assertEqual(auto_slots(auto_times_config(), at(20, 21), rows, 1), [at(21, 8)])

    def test_existing_schedules_block_only_the_same_minute(self):
        rows = [row(at(20, 12, 30))]
        self.assertEqual(auto_slots(auto_times_config(), at(20, 6), rows, 2), [at(20, 8), at(20, 20, 15)])

    def test_removed_and_cancelled_posts_do_not_block_times_or_use_the_quota(self):
        rows = [row(at(20, 8), state='removed'), row(at(20, 12, 30), state='cancelled')]
        self.assertEqual(auto_slots(auto_times_config(), at(20, 6), rows, 3), [at(20, 8), at(20, 12, 30), at(20, 20, 15)])

    def test_only_the_requested_count_is_returned(self):
        self.assertEqual(auto_slots(auto_times_config(), at(20, 6), [], 0), [])
        self.assertEqual(len(auto_slots(auto_times_config(), at(20, 6), [], 2)), 2)


class ContextAndSnapshotTests(unittest.TestCase):
    setUp = ai_fixtures.PageAITests.setUp
    saved = ai_fixtures.PageAITests.saved
    task = ai_fixtures.PageAITests.task

    def test_saving_keeps_slot_times_and_the_public_snapshot_shows_them(self):
        saved = self.saved(**times_config(campaigns=[self.campaign]))
        self.assertEqual(saved['config']['slot_times'], ['08:00', '12:30', '20:15'])
        snapshot = self.store.public_snapshot([dict(account_id=self.account, id=self.page)])
        self.assertEqual(snapshot['ai_policies'][0]['config']['slot_times'], ['08:00', '12:30', '20:15'])

    def test_required_slots_in_the_ai_context_follow_the_owners_times(self):
        task = self.task(**times_config(campaigns=[self.campaign], auto_publish=False))
        context = self.store.context(task)
        self.assertEqual(len(context['required_slots']), len(fixed_slots(task['config'], task['clock'], [])))


class AutoControllerCustomTimesTests(unittest.TestCase):
    """End to end through PageAIController.tick/auto_tick, like test_page_ai_auto.AutoControllerTests."""
    setUp = ai_fixtures.PageAITests.setUp
    saved = ai_fixtures.PageAITests.saved
    add_video = auto_fixtures.AutoControllerTests.add_video
    cycle = auto_fixtures.AutoControllerTests.cycle
    schedules = auto_fixtures.AutoControllerTests.schedules
    runs = auto_fixtures.AutoControllerTests.runs

    def start(self, **values):
        config = auto_times_config(**values)
        self.config.update(config)
        self.store.save(self.account, self.page, self.config, time.time()+86400, False)
        self.invoke.side_effect = auto_fixtures.fake_ai

    def test_a_video_ready_before_its_time_posts_exactly_on_the_owners_time(self):
        later = datetime.fromtimestamp(time.time()+3*3600, LOCAL_TIME)
        self.start(videos_per_day=1, slot_times=[later.strftime('%H:%M')])
        self.cycle()
        rows = self.schedules()
        self.assertEqual([r['state'] for r in rows], ['armed'])
        self.assertEqual(rows[0]['due_at'], page_ai.auto_slots(
            self.store.policy(self.account, self.page)['config'], self.runs()[0]['started_at'], [], 1)[0])

    def test_a_video_ready_after_its_time_posts_about_fifteen_minutes_later_not_tomorrow(self):
        past = datetime.fromtimestamp(time.time()-3600, LOCAL_TIME)  # Already passed today: owed.
        self.start(videos_per_day=1, slot_times=[past.strftime('%H:%M')])
        before = time.time()
        self.cycle()
        rows = self.schedules()
        self.assertEqual([r['state'] for r in rows], ['armed'])
        self.assertTrue(before+page_ai.AUTO_LEAD_SECONDS-5 <= rows[0]['due_at'] <= time.time()+page_ai.AUTO_LEAD_SECONDS+30)

    def test_two_owed_videos_do_not_land_on_the_same_minute(self):
        past = datetime.fromtimestamp(time.time()-7200, LOCAL_TIME)
        soon = datetime.fromtimestamp(time.time()-3600, LOCAL_TIME)
        self.start(videos_per_day=2, slot_times=sorted([past.strftime('%H:%M'), soon.strftime('%H:%M')]))
        key2 = self.add_video(2)
        self.cycle()
        rows = self.schedules()
        self.assertEqual(len(rows), 2)
        self.assertGreaterEqual(rows[1]['due_at']-rows[0]['due_at'], page_ai.AUTO_DEBT_GAP_SECONDS)
        self.assertEqual({r['source_key'] for r in rows}, {self.campaign+':1', key2})


if __name__ == '__main__':
    unittest.main()
