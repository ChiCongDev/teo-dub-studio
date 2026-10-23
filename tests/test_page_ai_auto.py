"""Auto mode: a finished campaign video gets an AI caption, is approved and scheduled on the owner's grid.

Real local stores and a real schedule controller; the AI is a synthetic function that reads the prompt. No
inference, browser or Facebook action.
"""
from collections import Counter
from datetime import datetime, timedelta
import json
import re
import time
import unittest
import uuid
from unittest.mock import Mock

from tests import test_page_ai as ai_fixtures
from tests import test_page_ai_slots as slot_fixtures
from teodub import page_ai
from teodub.page_ai import auto_slots, validate_config
from teodub.page_schedule import LOCAL_TIME, PageScheduleStore


def at(day, hour, minute=0):
    return datetime(2026, 10, day, hour, minute, tzinfo=LOCAL_TIME).timestamp()


def auto_config(**values):
    config = dict(campaigns=['a'*32], model='gpt-5.6-luna', effort='low', days=7, videos_per_day=2, texts_per_week=0,
                  interval_days=7, goal='', auto_publish=True, auto_campaign=True, slot_start='12:00', slot_gap=450)
    config.update(values)
    return config


def row(due, kind='video', state='armed'):
    return dict(due_at=due, source=dict(kind=kind), state=state)


class AutoSlotTests(unittest.TestCase):
    def test_slots_follow_the_owner_grid_day_after_day(self):
        slots = auto_slots(auto_config(), at(20, 10), [], 5)
        self.assertEqual(slots, [at(20, 12), at(20, 19, 30), at(21, 12), at(21, 19, 30), at(22, 12)])

    def test_only_a_three_minute_technical_lead_is_kept_before_the_first_slot(self):
        self.assertEqual(auto_slots(auto_config(), at(20, 11, 45), [], 2), [at(20, 12), at(20, 19, 30)])
        self.assertEqual(auto_slots(auto_config(), at(20, 11, 56), [], 1), [at(20, 12)])
        self.assertEqual(auto_slots(auto_config(), at(20, 11, 58), [], 2), [at(20, 19, 30), at(21, 12)])
        self.assertEqual(page_ai.AUTO_LEAD_SECONDS, 180)

    def test_existing_schedules_block_only_the_same_minute_and_use_up_the_days_quota(self):
        rows = [row(at(20, 19, 30)), row(at(21, 12))]
        self.assertEqual(auto_slots(auto_config(), at(20, 10), rows, 3), [at(20, 12), at(21, 19, 30), at(22, 12)])
        close = [row(at(20, 19, 10)), row(at(21, 12, 20))]
        self.assertEqual(auto_slots(auto_config(), at(20, 10), close, 3), [at(20, 12), at(21, 12), at(22, 12)])
        manual = [row(at(20, 9, 0)), row(at(20, 9, 0)+7200)]
        self.assertEqual(auto_slots(auto_config(), at(20, 8), manual, 2), [at(21, 12), at(21, 19, 30)])

    def test_removed_and_cancelled_posts_neither_use_the_days_quota_nor_block_times(self):
        config = auto_config(videos_per_day=2)
        gone = [row(at(20, 12), state='removed'), row(at(20, 19, 30), state='cancelled'), row(at(20, 13), state='removed')]
        self.assertEqual(auto_slots(config, at(20, 10), gone, 2), [at(20, 12), at(20, 19, 30)])

    def test_posts_that_still_count_use_the_quota_and_block_their_minute(self):
        config = auto_config(videos_per_day=2)
        for state in ('armed', 'paused', 'submitted', 'published', 'needs_attention', 'scheduled'):
            with self.subTest(state=state):
                rows = [row(at(20, 12), state=state), row(at(20, 19, 30), state=state)]
                self.assertEqual(auto_slots(config, at(20, 10), rows, 2), [at(21, 12), at(21, 19, 30)])

    def test_text_posts_do_not_use_the_video_quota(self):
        rows = [row(at(20, 15), 'text')]
        self.assertEqual(auto_slots(auto_config(videos_per_day=1), at(20, 10), rows, 1), [at(20, 12)])

    def test_a_video_ready_after_the_days_first_times_still_goes_out_the_same_day_on_the_rhythm(self):
        # The owner's 21/10 settings: 10 a day from 09:30, 5 minutes apart. Two posted, one armed at 11:30.
        config = auto_config(videos_per_day=10, slot_start='09:30', slot_gap=5)
        today = [row(at(21, 9, 45), state='submitted'), row(at(21, 10, 10), state='submitted'), row(at(21, 11, 30))]
        self.assertEqual(auto_slots(config, at(21, 12, 33), today, 1), [at(21, 12, 40)])  # Not tomorrow 09:30.
        self.assertEqual(auto_slots(config, at(21, 11, 24), today[:2], 3), [at(21, 11, 30), at(21, 11, 35), at(21, 11, 40)])

    def test_the_daily_number_still_holds_and_the_next_day_starts_at_the_first_time(self):
        config = auto_config(videos_per_day=10, slot_start='09:30', slot_gap=5)
        nine = [row(at(21, 9, 30)+300*index, state='submitted') for index in range(9)]
        self.assertEqual(auto_slots(config, at(21, 12, 33), nine, 3), [at(21, 12, 40), at(22, 9, 30), at(22, 9, 35)])

    def test_never_before_the_first_time_and_the_rhythm_ends_with_the_day(self):
        config = auto_config(videos_per_day=10, slot_start='09:30', slot_gap=5)
        self.assertEqual(auto_slots(config, at(21, 6), [], 2), [at(21, 9, 30), at(21, 9, 35)])
        self.assertEqual(auto_slots(config, at(21, 23, 50), [], 2), [at(21, 23, 55), at(22, 9, 30)])
        self.assertEqual(auto_slots(config, at(21, 23, 56), [], 1), [at(22, 9, 30)])

    def test_a_wide_gap_keeps_its_rhythm_for_late_videos_too(self):
        config = auto_config(videos_per_day=3, slot_start='09:30', slot_gap=60)
        # 09:30, 10:30 and 11:30 passed with nothing ready; a video ready at 15:10 goes out at 15:30.
        self.assertEqual(auto_slots(config, at(21, 15, 10), [], 2), [at(21, 15, 30), at(21, 16, 30)])

    def test_the_gap_is_entirely_the_owners_choice_even_a_few_minutes(self):
        config = auto_config(videos_per_day=4, slot_start='10:00', slot_gap=5)
        slots = auto_slots(config, at(20, 8), [], 6)
        self.assertEqual(slots[:4], [at(20, 10), at(20, 10, 5), at(20, 10, 10), at(20, 10, 15)])
        self.assertEqual(slots[4:], [at(21, 10), at(21, 10, 5)])
        self.assertEqual(auto_slots(auto_config(videos_per_day=2, slot_gap=1), at(20, 8), [], 2), [at(20, 12), at(20, 12, 1)])

    def test_no_daily_limit_and_a_busy_day_may_run_past_midnight(self):
        config = auto_config(videos_per_day=30, slot_start='08:00', slot_gap=60)
        slots = auto_slots(config, at(20, 6), [], 40)
        self.assertEqual(len(slots), 40)
        self.assertEqual(slots[:3], [at(20, 8), at(20, 9), at(20, 10)])
        self.assertEqual(slots, sorted(set(slots)))
        self.assertTrue(all(b-a >= 60 for a, b in zip(slots, slots[1:])))
        self.assertEqual(Counter(datetime.fromtimestamp(s, LOCAL_TIME).day for s in slots)[20] <= 30, True)

    def test_only_the_requested_count_is_returned(self):
        self.assertEqual(auto_slots(auto_config(), at(20, 10), [], 0), [])
        self.assertEqual(len(auto_slots(auto_config(), at(20, 10), [], 1)), 1)


class AutoConfigTests(unittest.TestCase):
    def rejected(self, message, **values):
        with self.subTest(values=values):
            with self.assertRaisesRegex(ValueError, message):
                validate_config(auto_config(**values))

    def test_valid_auto_configuration_and_large_daily_numbers_are_accepted(self):
        self.assertTrue(validate_config(auto_config())['auto_campaign'])
        self.assertEqual(validate_config(auto_config(videos_per_day=1000, slot_gap=30))['videos_per_day'], 1000)
        self.assertEqual(validate_config(auto_config(videos_per_day=12, slot_start='06:00', slot_gap=120))['slot_gap'], 120)

    def test_incomplete_or_contradictory_auto_settings_are_rejected(self):
        config = auto_config()
        del config['slot_start'], config['slot_gap']
        with self.assertRaisesRegex(ValueError, '(?i)giờ'):
            validate_config(config)
        self.rejected('(?i)tự đăng', auto_publish=False)
        self.rejected('bài chữ', texts_per_week=2)
        self.rejected('(?i)không hợp lệ', videos_per_day=1001)
        self.rejected('(?i)bật hoặc tắt', auto_campaign='yes')
        self.rejected('(?i)khoảng cách', slot_gap=0)
        self.rejected('(?i)khoảng cách', slot_gap=1441)
        self.rejected('(?i)khoảng cách', slot_gap=True)

    def test_short_gaps_are_valid_in_auto_mode_but_not_in_the_plain_fixed_time_mode(self):
        self.assertEqual(validate_config(auto_config(slot_gap=1))['slot_gap'], 1)
        self.assertEqual(validate_config(auto_config(slot_gap=5, videos_per_day=20))['slot_gap'], 5)
        plain = auto_config(slot_gap=10, videos_per_day=2, auto_publish=False)
        del plain['auto_campaign']
        with self.assertRaisesRegex(ValueError, '(?i)khoảng cách'):
            validate_config(plain)

    def test_plain_scheduled_ai_is_unchanged(self):
        config = auto_config()
        del config['auto_campaign'], config['slot_start'], config['slot_gap']
        config['auto_publish'] = False
        self.assertEqual(validate_config(config), config)


def fake_ai(prompt, schema, **options):
    """Reads the real prompt: one caption per verified video, on the offered times in order."""
    plan_id = re.search(r'plan_id=([0-9a-f-]{36})', prompt).group(1)
    context = json.loads(prompt.rsplit('\n', 1)[-1])
    videos = [v for v in context['videos'] if v['content_verified']]
    items = [dict(id=f'v{i}', kind='video', due_at=slot, body=f'Caption cho {video["title"]} #cauca',
                  video_key=video['key'], goal='Giải trí')
             for i, (video, slot) in enumerate(zip(videos, context['required_slots']), 1)]
    return (dict(analysis='Caption theo từng video.', plan=dict(format='teo-page-plan', version=1, plan_id=plan_id,
        title='Tự động từ chiến dịch', page_id='100', time_zone='Asia/Bangkok', items=items)),
        dict(requested_model='gpt-5.6-luna', observed_model=None, usage={}))


class AutoControllerTests(unittest.TestCase):
    setUp = ai_fixtures.PageAITests.setUp
    saved = ai_fixtures.PageAITests.saved
    add_video = slot_fixtures.FixedSlotAITests.add_video

    def start(self, **values):
        later = datetime.fromtimestamp(time.time()+3*3600, LOCAL_TIME)
        config = auto_config(**dict(dict(slot_start=later.strftime('%H:%M'), slot_gap=60), **values))
        self.config.update(config)
        self.store.save(self.account, self.page, self.config, time.time()+86400, False)
        self.invoke.side_effect = fake_ai

    def cycle(self):
        self.controller.last_poll = 0
        self.controller.auto_checked.clear()
        self.controller.tick()
        for _ in range(200):
            self.controller.observe()
            if self.controller.pending is None:
                return
            time.sleep(0.05)
        self.fail('The AI turn never finished')

    def schedules(self):
        return sorted(PageScheduleStore(self.root).list(self.account, self.page), key=lambda r: r['due_at'])

    def runs(self):
        return self.store.runs(self.account, self.page)

    def test_finished_video_gets_a_caption_is_approved_and_armed_on_the_owner_grid(self):
        self.start()
        self.cycle()
        rows = self.schedules()
        self.assertEqual([r['state'] for r in rows], ['armed'])
        expected = auto_slots(self.store.policy(self.account, self.page)['config'], self.runs()[0]['started_at'], [], 1)
        self.assertEqual(rows[0]['body'], 'Caption cho Mẹo câu cá #cauca')
        self.assertEqual(self.runs()[0]['state'], 'armed')
        self.assertEqual(self.invoke.call_count, 1)
        plan = self.store.plans.plans(self.account, self.page)[0]
        self.assertTrue(plan['approved_at'])
        self.assertEqual(plan['source_kind'], 'scheduled_ai')
        self.assertEqual(rows[0]['due_at'], plan['items'][0]['due_at'])
        self.assertEqual(len(expected), 1)

    def test_nothing_new_means_no_second_ai_call(self):
        self.start()
        self.cycle()
        self.cycle()
        self.cycle()
        self.assertEqual(self.invoke.call_count, 1)
        self.assertEqual(len(self.runs()), 1)

    def test_a_newly_finished_video_triggers_one_more_batch_the_same_day(self):
        self.start()
        self.cycle()
        key2, key3 = self.add_video(2), self.add_video(3)
        self.cycle()
        self.assertEqual(self.invoke.call_count, 2)
        rows = self.schedules()
        self.assertEqual(len(rows), 3)
        self.assertEqual([r['state'] for r in rows], ['armed']*3)
        self.assertEqual({r['source_key'] for r in rows}, {self.campaign+':1', key2, key3})
        self.assertTrue(all(b['due_at']-a['due_at'] >= 1800 for a, b in zip(rows, rows[1:])))

    def test_daily_limit_is_whatever_the_owner_set_and_no_other_limit_applies(self):
        self.start(videos_per_day=1)
        keys = [self.add_video(n) for n in (2, 3, 4)]
        self.cycle()
        rows = self.schedules()
        self.assertEqual(len(rows), 4)
        days = Counter(datetime.fromtimestamp(r['due_at'], LOCAL_TIME).date() for r in rows)
        self.assertEqual(max(days.values()), 1)
        self.assertEqual(len(keys), 3)

    def test_posts_a_few_minutes_apart_are_scheduled_when_the_owner_chooses_so(self):
        self.start(videos_per_day=5, slot_gap=5)
        keys = [self.add_video(n) for n in (2, 3, 4)]
        self.cycle()
        rows = self.schedules()
        self.assertEqual(len(rows), 4)
        self.assertEqual([r['state'] for r in rows], ['armed']*4)
        self.assertEqual([round(b['due_at']-a['due_at']) for a, b in zip(rows, rows[1:])], [300, 300, 300])
        self.assertEqual({r['source_key'] for r in rows}, {self.campaign+':1', *keys})

    def test_a_slot_that_passes_while_the_ai_writes_is_dropped_the_rest_is_armed_and_the_video_waits(self):
        now = datetime.fromtimestamp(time.time(), LOCAL_TIME)
        if now.hour == 23 and now.minute > 50:
            self.skipTest('too close to midnight for a same-day grid')
        soon = datetime.fromtimestamp(time.time()+300, LOCAL_TIME)
        self.start(videos_per_day=2, slot_start=soon.strftime('%H:%M'), slot_gap=60)
        key2 = self.add_video(2)
        from unittest.mock import patch
        with patch.object(page_ai, 'AUTO_STALE_SECONDS', 400):
            self.cycle()
        rows = self.schedules()
        self.assertEqual([r['source_key'] for r in rows], [key2])
        self.assertEqual(self.runs()[0]['state'], 'armed')
        self.assertEqual(self.invoke.call_count, 1)
        self.cycle()
        self.assertEqual(self.invoke.call_count, 2)
        self.assertEqual(sorted(r['source_key'] for r in self.schedules()), sorted([self.campaign+':1', key2]))
        self.assertEqual({r['state'] for r in self.schedules()}, {'armed'})

    def test_all_slots_passing_arms_nothing_keeps_auto_mode_on_and_retries_later(self):
        now = datetime.fromtimestamp(time.time(), LOCAL_TIME)
        if now.hour == 23 and now.minute > 50:
            self.skipTest('too close to midnight for a same-day grid')
        soon = datetime.fromtimestamp(time.time()+300, LOCAL_TIME)
        self.start(videos_per_day=1, slot_start=soon.strftime('%H:%M'), slot_gap=60)
        from unittest.mock import patch
        with patch.object(page_ai, 'AUTO_STALE_SECONDS', 400):
            self.cycle()
        self.assertEqual(self.schedules(), [])
        self.assertIn('còn trong hàng chờ', self.runs()[0]['message'])
        self.assertIs(self.store.policy(self.account, self.page)['config']['auto_campaign'], True)
        self.cycle()
        self.assertEqual(len(self.schedules()), 1)

    def remove(self, ident):
        store = PageScheduleStore(self.root)
        preview = store.removal_preview([ident], account_id=self.account, page_key=self.page)
        return self.scheduler.remove_many(preview, account_id=self.account, page_key=self.page)

    def test_an_untouched_post_removed_from_the_list_returns_its_video_to_the_next_batch(self):
        self.start()
        self.cycle()
        first = self.schedules()[0]
        self.assertEqual(self.remove(first['id']), dict(removed=[first['id']], skipped={}))
        keys = self.store.plans.reserved_video_keys(self.account, self.page)
        self.assertNotIn(self.campaign+':1', keys)
        self.assertEqual(self.store.auto_ready(self.account, self.page, self.store.policy(self.account, self.page)['config']),
                         [self.campaign+':1'])
        self.cycle()
        self.assertEqual(self.invoke.call_count, 2)
        live = [r for r in self.schedules() if r['state'] != 'removed']
        self.assertEqual([r['state'] for r in live], ['armed'])
        self.assertEqual(live[0]['source_key'], self.campaign+':1')
        self.assertEqual(len([r for r in self.schedules() if r['state'] == 'removed']), 1)

    def test_posts_removed_earlier_the_same_day_do_not_push_new_ones_to_tomorrow(self):
        self.start(videos_per_day=1)
        self.cycle()
        first = self.schedules()[0]
        self.remove(first['id'])
        self.cycle()
        live = [r for r in self.schedules() if r['state'] != 'removed']
        self.assertEqual(len(live), 1)
        self.assertEqual(live[0]['due_at'], first['due_at'])
        self.assertEqual(self.invoke.call_count, 2)
        self.remove(live[0]['id'])
        self.cycle()
        again = [r for r in self.schedules() if r['state'] != 'removed']
        self.assertEqual([r['due_at'] for r in again], [first['due_at']])

    def test_a_removed_post_that_may_have_been_sent_keeps_its_video_reserved(self):
        self.start()
        self.cycle()
        first = self.schedules()[0]
        PageScheduleStore(self.root).update(first['id'], state='removed')
        self.assertIn(self.campaign+':1', self.store.plans.reserved_video_keys(self.account, self.page))
        self.cycle()
        self.assertEqual(self.invoke.call_count, 1)

    def test_a_video_in_a_proposal_that_was_never_scheduled_stays_reserved(self):
        self.start(videos_per_day=2)
        later = self.config
        self.store.plans.import_generated(self.account, self.page, dict(format='teo-page-plan', version=1,
            plan_id=str(uuid.uuid4()), title='Đề xuất', page_id='100', time_zone='Asia/Bangkok',
            items=[dict(id='p1', kind='video', due_at=(datetime.fromtimestamp(time.time()+86400, LOCAL_TIME)).isoformat(timespec='seconds'),
                        body='Chờ duyệt', video_key=self.campaign+':1', goal='')]), run_id=uuid.uuid4().hex, thread_id=self.thread)
        self.assertIn(self.campaign+':1', self.store.plans.reserved_video_keys(self.account, self.page))
        self.assertEqual(later, self.config)

    def test_wrong_ai_times_stop_auto_mode_without_any_schedule(self):
        self.start()
        def wrong(prompt, schema, **options):
            answer, usage = fake_ai(prompt, schema, **options)
            answer['plan']['items'][0]['due_at'] = '2031-01-01T10:00:00+07:00'
            return answer, usage
        self.invoke.side_effect = wrong
        self.cycle()
        self.assertEqual(self.schedules(), [])
        self.assertEqual(self.runs()[0]['state'], 'needs_attention')
        config = self.store.policy(self.account, self.page)['config']
        self.assertIs(config['auto_campaign'], False)
        self.cycle()
        self.assertEqual(self.invoke.call_count, 1)

    def test_ai_failure_stops_auto_mode_and_never_retries(self):
        self.start()
        self.invoke.side_effect = RuntimeError('hết hạn mức Codex')
        self.cycle()
        self.assertEqual(self.runs()[0]['state'], 'needs_attention')
        self.assertIn('hạn mức', self.runs()[0]['message'])
        self.assertIs(self.store.policy(self.account, self.page)['config']['auto_campaign'], False)
        self.cycle()
        self.assertEqual(self.invoke.call_count, 1)

    def test_turned_off_pages_and_other_pages_are_never_touched(self):
        self.start()
        config = dict(self.store.policy(self.account, self.page)['config'])
        for key in ('target', 'thread_id', 'thread_title'):
            config.pop(key)
        config['auto_campaign'] = False
        self.store.save(self.account, self.page, config, time.time()+86400, False)
        self.cycle()
        self.assertEqual(self.invoke.call_count, 0)
        self.assertEqual(self.runs(), [])

    def test_does_not_start_while_the_page_command_is_busy(self):
        self.start()
        self.bridge.active = object()
        self.cycle_blocked()
        self.bridge.active = None

    def cycle_blocked(self):
        self.controller.last_poll = 0
        self.controller.auto_checked.clear()
        try:
            self.controller.tick()
        finally:
            pass
        self.assertEqual(self.invoke.call_count, 0)

    def test_interrupted_run_is_reported_and_auto_mode_stays_off_after_restart(self):
        self.start()
        row_ = self.store.policy(self.account, self.page)
        task = self.store.claim_auto(row_, time.time())
        self.assertIsNotNone(task)
        self.store.recover()
        self.assertEqual(self.runs()[0]['state'], 'needs_attention')
        self.assertIs(self.store.policy(self.account, self.page)['config']['auto_campaign'], False)

    def test_only_one_run_per_page_at_a_time(self):
        self.start()
        row_ = self.store.policy(self.account, self.page)
        self.assertIsNotNone(self.store.claim_auto(row_, time.time()))
        self.assertIsNone(self.store.claim_auto(row_, time.time()+1))

    def test_adding_campaign_sources_is_allowed_for_auto_pages_but_not_for_plain_auto_publish(self):
        self.start()
        updated = self.store.add_campaigns(self.account, self.page, [self.campaign])
        self.assertTrue(updated['config']['auto_campaign'])
        plain = dict(self.store.policy(self.account, self.page)['config'])
        for key in ('target', 'thread_id', 'thread_title', 'auto_campaign', 'slot_start', 'slot_gap'):
            plain.pop(key)
        plain.update(auto_publish=True, days=7, interval_days=7)
        self.store.save(self.account, self.page, plain, time.time()+86400, False)
        with self.assertRaises(ValueError):
            self.store.add_campaigns(self.account, self.page, [self.campaign])

    def test_scheduled_ai_path_is_not_changed_by_auto_mode(self):
        plain = dict(self.config, auto_publish=False)
        self.store.save(self.account, self.page, plain, time.time()+30, True)
        self.assertEqual([p['config'].get('auto_campaign') for p in self.store.policies()], [None])
        self.assertEqual(self.store.auto_policies(), [])

    def test_approved_ai_items_listing_shows_caption_time_and_state(self):
        self.start()
        self.cycle()
        items = self.store.plans.approved_ai_items()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['body'], 'Caption cho Mẹo câu cá #cauca')
        self.assertEqual(items[0]['state'], 'armed')
        self.assertEqual(items[0]['schedule_id'], self.schedules()[0]['id'])
        self.assertEqual(items[0]['page_name'], 'Synthetic Page')


if __name__ == '__main__':
    unittest.main()
