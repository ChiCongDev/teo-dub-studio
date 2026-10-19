"""Owner-chosen posting times: AI writes captions only. Real local stores, synthetic AI; no inference or Facebook."""
from datetime import datetime, timedelta
import time
import unittest
import unittest.mock

from tests import test_page_ai as ai_fixtures
from teodub.common import atomic_json, hash_file
from teodub.page_ai import fixed_slots, make_prompt, validate_config
from teodub.page_schedule import LOCAL_TIME, PageScheduleStore


def at(day, hour, minute=0):
    return datetime(2026, 10, day, hour, minute, tzinfo=LOCAL_TIME).timestamp()


def slot_config(**values):
    config = dict(campaigns=['a'*32], model='gpt-5.6-luna', effort='low', days=2, videos_per_day=2, texts_per_week=0,
                  interval_days=2, goal='', auto_publish=False, slot_start='12:00', slot_gap=450)
    config.update(values)
    return config


class SlotCalculationTests(unittest.TestCase):
    def test_daily_slots_start_at_first_time_and_follow_the_gap(self):
        slots = fixed_slots(slot_config(), at(20, 10), [])
        self.assertEqual(slots, [at(20, 12), at(20, 19, 30), at(21, 12), at(21, 19, 30)])

    def test_slots_before_thirty_minutes_from_now_are_dropped(self):
        self.assertEqual(fixed_slots(slot_config(), at(20, 11, 45), [])[0], at(20, 19, 30))
        self.assertEqual(fixed_slots(slot_config(), at(20, 11, 29), [])[0], at(20, 12))

    def test_days_define_how_many_dates_are_planned(self):
        self.assertEqual(len(fixed_slots(slot_config(days=1), at(20, 10), [])), 2)
        self.assertEqual(len(fixed_slots(slot_config(days=3, interval_days=3), at(20, 10), [])), 6)
        self.assertEqual(fixed_slots(slot_config(days=1, interval_days=1), at(20, 22), []), [])

    def test_slots_within_thirty_minutes_of_existing_schedules_are_skipped(self):
        slots = fixed_slots(slot_config(), at(20, 10), [at(20, 19, 10), at(21, 12, 40)])
        self.assertEqual(slots, [at(20, 12), at(21, 12), at(21, 19, 30)])

    def test_without_slot_settings_the_ai_keeps_choosing_times(self):
        config = slot_config()
        del config['slot_start'], config['slot_gap']
        self.assertIsNone(fixed_slots(config, at(20, 10), []))
        self.assertEqual(validate_config(config), config)


class SlotValidationTests(unittest.TestCase):
    def rejected(self, message, **values):
        with self.subTest(values=values):
            with self.assertRaisesRegex(ValueError, message):
                validate_config(slot_config(**values))

    def test_valid_configuration_is_accepted(self):
        self.assertEqual(validate_config(slot_config())['slot_gap'], 450)

    def test_invalid_slot_settings_are_rejected(self):
        self.rejected('HH:MM', slot_start='9h30')
        self.rejected('HH:MM', slot_start='24:00')
        self.rejected('(?i)khoảng cách', slot_gap=29)
        self.rejected('(?i)khoảng cách', slot_gap=1441)
        self.rejected('(?i)khoảng cách', slot_gap='450')
        self.rejected('(?i)khoảng cách', slot_gap=True)
        self.rejected('bài chữ', texts_per_week=2)
        self.rejected('23:59', slot_start='20:00', slot_gap=300)

    def test_one_of_the_two_settings_alone_is_rejected(self):
        config = slot_config()
        del config['slot_gap']
        with self.assertRaisesRegex(ValueError, 'cả giờ'):
            validate_config(config)


class FixedSlotAITests(unittest.TestCase):
    setUp = ai_fixtures.PageAITests.setUp
    task = ai_fixtures.PageAITests.task
    finish = ai_fixtures.PageAITests.finish

    def add_video(self, number):
        job = self.root/'campaigns'/self.campaign/'jobs'/f'{number:04}'
        video = self.root/'outputs'/f'Video {number}.mp4'
        video.write_bytes(b'synthetic finished video '+bytes([number]))
        subs = self.root/'outputs'/f'Video {number}.srt'
        subs.write_text(f'1\n00:00:00,000 --> 00:00:03,000\nCảnh số {number} trong video.\n', encoding='utf-8')
        outputs = dict(video=str(video), subtitles=str(subs))
        atomic_json(job/'state.json', dict(rendered=dict(outputs=outputs, hashes={k: hash_file(v) for k, v in outputs.items()})))
        path = self.root/'campaigns'/self.campaign/'campaign.json'
        from teodub.campaign import read_json
        data = read_json(path)
        data['items'].append(dict(number=number, status='completed', job=str(job), outputs=outputs, title=f'Video {number}'))
        atomic_json(path, data)
        return f'{self.campaign}:{number}'

    def slot_task(self, **values):
        later = datetime.fromtimestamp(time.time()+3*3600, LOCAL_TIME)
        values = dict(dict(slot_start=later.strftime('%H:%M'), slot_gap=60, days=2, interval_days=2, videos_per_day=2,
                           texts_per_week=0), **values)
        return self.task(**values)

    def plan(self, task, slots, keys, bodies=None):
        items = [dict(id=f'v{i}', kind='video', due_at=datetime.fromtimestamp(stamp, LOCAL_TIME).isoformat(timespec='seconds'),
                      body=(bodies or {}).get(i, f'Caption AI cho video {i}. #cauca'), video_key=key, goal='Giải trí')
                 for i, (stamp, key) in enumerate(zip(slots, keys), 1)]
        return dict(analysis='Caption theo từng video.', plan=dict(format='teo-page-plan', version=1,
            plan_id=task['plan_uuid'], title='Kế hoạch AI', page_id='100', time_zone='Asia/Bangkok', items=items))

    def required(self, task):
        return fixed_slots(task['config'], task['clock'], [])

    def test_prompt_lists_the_exact_times_the_owner_chose(self):
        task = self.slot_task()
        context = self.store.context(task)
        self.assertEqual(len(context['required_slots']), len(self.required(task)))
        prompt = make_prompt(task, context)
        self.assertIn('required_slots', prompt)
        self.assertIn('đúng các giờ', prompt)
        self.assertIn(context['required_slots'][0], prompt)

    def test_ai_may_only_write_captions_for_the_owners_times(self):
        key2 = self.add_video(2)
        task = self.slot_task()
        slots = self.required(task)
        answer = self.plan(task, slots[:2], [self.campaign+':1', key2])
        run = self.finish(task, answer)
        self.assertEqual(run['state'], 'done', run['message'])
        plan = self.store.plans.get_plan(task['plan_uuid'], self.account, self.page)
        self.assertEqual([item['due_at'] for item in plan['items']], slots[:2])
        self.assertEqual(plan['items'][0]['body'], 'Caption AI cho video 1. #cauca')

    def test_ai_choosing_any_other_time_is_refused_without_a_plan(self):
        task = self.slot_task()
        wrong = self.required(task)[0]+900
        run = self.finish(task, self.plan(task, [wrong], [self.campaign+':1']))
        self.assertEqual(run['state'], 'needs_attention')
        self.assertIn('đã đặt', run['message'])
        self.assertEqual(self.store.plans.plans(self.account, self.page), [])
        self.assertEqual(PageScheduleStore(self.root).list(), [])

    def test_two_videos_on_one_time_are_refused(self):
        key2 = self.add_video(2)
        task = self.slot_task()
        slot = self.required(task)[0]
        run = self.finish(task, self.plan(task, [slot, slot], [self.campaign+':1', key2]))
        self.assertEqual(run['state'], 'needs_attention')
        self.assertEqual(self.store.plans.plans(self.account, self.page), [])

    def test_text_posts_are_refused_in_fixed_time_mode(self):
        task = self.slot_task()
        answer = self.plan(task, self.required(task)[:1], [''])
        answer['plan']['items'][0]['kind'] = 'text'
        run = self.finish(task, answer)
        self.assertEqual(run['state'], 'needs_attention')

    def test_auto_publish_arms_exactly_the_chosen_times(self):
        key2 = self.add_video(2)
        task = self.slot_task(auto_publish=True)
        slots = self.required(task)
        run = self.finish(task, self.plan(task, slots[:2], [self.campaign+':1', key2]))
        self.assertEqual(run['state'], 'armed', run['message'])
        rows = sorted(PageScheduleStore(self.root).list(self.account, self.page), key=lambda r: r['due_at'])
        self.assertEqual([r['due_at'] for r in rows], slots[:2])
        self.assertEqual([r['state'] for r in rows], ['armed', 'armed'])

    def test_no_free_time_skips_the_ai_call_and_saves_quota(self):
        task = self.slot_task(slot_start='00:10', slot_gap=30, days=1, interval_days=1, videos_per_day=1)
        with unittest.mock.patch('teodub.page_ai.fixed_slots', return_value=[]):
            run = self.finish(task, self.plan(task, [], []))
        self.assertEqual(self.invoke.call_count, 0)
        self.assertEqual(run['state'], 'done')
        self.assertIn('giờ trống', run['message'])

    def test_saving_keeps_the_settings_and_adding_sources_does_not_lose_them(self):
        saved = self.saved(**slot_config(campaigns=[self.campaign]))
        self.assertEqual((saved['config']['slot_start'], saved['config']['slot_gap']), ('12:00', 450))
        updated = self.store.add_campaigns(self.account, self.page, [self.campaign])
        self.assertEqual((updated['config']['slot_start'], updated['config']['slot_gap']), ('12:00', 450))
        snapshot = self.store.public_snapshot([dict(account_id=self.account, id=self.page)])
        self.assertEqual(snapshot['ai_policies'][0]['config']['slot_gap'], 450)

    saved = ai_fixtures.PageAITests.saved


if __name__ == '__main__':
    unittest.main()


import tkinter as tk
from tkinter import ttk
from types import SimpleNamespace
from unittest.mock import Mock

from teodub.page_ai_ui import PageAIPanel


class SlotPanelTests(unittest.TestCase):
    def setUp(self):
        ai_fixtures.PageAITests.setUp(self)
        try:
            self.window = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f'Tk unavailable: {exc}')
        self.window.withdraw()
        self.addCleanup(self.window.destroy)
        self.manager = SimpleNamespace(owner=self.owner, account_id=self.account, page_key=self.page,
                                       open_strategy_settings=Mock())
        self.panel = PageAIPanel(ttk.Notebook(self.window), self.manager)
        self.panel.set_sources([self.campaign])
        self.panel.enabled.set(True)
        self.panel.days.set(1)
        self.panel.interval.set(1)
        self.panel.texts.set(0)

    def test_fixed_times_are_off_by_default_and_not_saved(self):
        self.assertFalse(self.panel.slots_on.get())
        self.assertIn('AI tự chọn', self.panel.slots_summary.get())
        self.panel.save()
        config = self.store.policy(self.account, self.page)['config']
        self.assertNotIn('slot_start', config)

    def test_saving_with_owner_times_stores_start_and_gap_and_reloads_them(self):
        self.panel.videos.set(2)
        self.panel.slots_on.set(True)
        self.panel.slot_start.set('12:00')
        self.panel.slot_gap.set('450')
        self.assertIn('12:00, 19:30', self.panel.slots_summary.get())
        self.panel.save()
        config = self.store.policy(self.account, self.page)['config']
        self.assertEqual((config['slot_start'], config['slot_gap'], config['auto_publish']), ('12:00', 450, False))
        self.panel.destroy()
        self.panel = PageAIPanel(ttk.Notebook(self.window), self.manager)
        self.assertTrue(self.panel.slots_on.get())
        self.assertEqual((self.panel.slot_start.get(), self.panel.slot_gap.get()), ('12:00', '450'))

    def test_invalid_times_are_reported_and_nothing_is_saved(self):
        self.panel.videos.set(3)
        self.panel.slots_on.set(True)
        for start, gap in (('9h', '60'), ('12:00', 'abc'), ('12:00', '10'), ('20:00', '300')):
            with self.subTest(start=start, gap=gap):
                self.panel.slot_start.set(start)
                self.panel.slot_gap.set(gap)
                self.assertIn('chưa hợp lệ', self.panel.slots_summary.get())
                self.panel.save()
                self.assertIsNone(self.store.policy(self.account, self.page))
                self.assertTrue(self.panel.status.get())
