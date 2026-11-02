"""30/10/2026: "Xếp lại theo giờ của Page" — many past/paused/stopped video appointments go onto the Page's daily times and are
armed. Disposable stores and files only; no browser, no publishing worker, no AI, no Facebook."""
from copy import deepcopy
from datetime import datetime
import time
import tkinter as tk
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from tests import test_page_schedule as fixtures
from tests import test_page_schedule_recovery as recovery
from tests import test_page_schedule_recovery_ui as recovery_ui
from teodub import page_schedule_replan as replan
from teodub.page_schedule import LOCAL_TIME, display_time

TIMES = ['11:30', '19:00']


def at(text):
    return datetime.strptime(text, '%Y-%m-%d %H:%M').replace(tzinfo=LOCAL_TIME).timestamp()


def row(due, *, state='scheduled', kind='video'):
    return dict(due_at=at(due), state=state, source=dict(kind=kind))


class TimesTests(unittest.TestCase):
    def test_times_are_padded_sorted_and_checked(self):
        self.assertEqual(replan.parse_times('19:00, 8:5 ;11:30'), ['08:05', '11:30', '19:00'])
        self.assertEqual(replan.parse_times(' 11:30 19:00 '), ['11:30', '19:00'])
        for bad in ('', '  ', 'abc', '24:00', '12:60', '12', '11:30, 11:30', '11:30, 7pm'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                replan.parse_times(bad)

    def test_no_saved_ai_times_means_an_empty_list(self):
        with patch('teodub.page_ai.PageAIStore.policy', return_value=None):
            self.assertEqual(replan.page_times('.', 'a', 'b'), [])

    def test_saved_ai_times_are_read_from_own_times_or_the_rhythm(self):
        own = dict(slot_times=['19:00', '11:30'], videos_per_day=2)
        rhythm = dict(slot_start='06:00', slot_gap=450, videos_per_day=3)
        for config, expected in ((own, ['11:30', '19:00']), (rhythm, ['06:00', '13:30', '21:00'])):
            with self.subTest(config=config), patch('teodub.page_ai.PageAIStore.policy', return_value=dict(config=config)):
                self.assertEqual(replan.page_times('.', 'a', 'b'), expected)


class SlotTests(unittest.TestCase):
    clock = at('2026-11-01 10:00')

    def slots(self, count, rows=(), start=None, times=TIMES):
        return [display_time(s) for s in replan.free_slots(times, count, list(rows), self.clock, start or self.clock+600)]

    def test_slots_follow_the_daily_times_from_now_on_and_skip_times_already_passed(self):
        self.assertEqual(self.slots(5), ['2026-11-01 11:30', '2026-11-01 19:00', '2026-11-02 11:30',
                                         '2026-11-02 19:00', '2026-11-03 11:30'])
        self.assertEqual(self.slots(2, start=at('2026-11-01 12:00')), ['2026-11-01 19:00', '2026-11-02 11:30'])

    def test_the_start_moves_the_first_slot_but_never_into_the_past(self):
        self.assertEqual(self.slots(1, start=at('2026-11-05 08:00')), ['2026-11-05 11:30'])
        self.assertEqual(self.slots(1, start=at('2026-10-01 08:00')), ['2026-11-01 11:30'])  # Never before now+lead.

    def test_a_day_that_is_full_goes_to_the_next_day_counting_what_was_already_sent(self):
        sent_today = [row('2026-11-01 08:00', state='submitted'), row('2026-11-01 09:00', state='published')]
        self.assertEqual(self.slots(1, sent_today), ['2026-11-02 11:30'])

    def test_a_taken_time_is_skipped_and_the_day_cap_counts_it(self):
        self.assertEqual(self.slots(2, [row('2026-11-01 11:30', state='armed')]),
                         ['2026-11-01 19:00', '2026-11-02 11:30'])

    def test_removed_cancelled_and_text_posts_do_not_take_a_place(self):
        rows = [row('2026-11-01 11:30', state='removed'), row('2026-11-01 19:00', state='cancelled'),
                row('2026-11-01 12:00', kind='text')]
        self.assertEqual(self.slots(2, rows), ['2026-11-01 11:30', '2026-11-01 19:00'])

    def test_a_text_post_in_the_same_minute_still_blocks_that_time(self):
        self.assertEqual(self.slots(1, [row('2026-11-01 11:30', kind='text')]), ['2026-11-01 19:00'])

    def test_too_many_videos_for_a_year_is_refused_with_a_message(self):
        with self.assertRaisesRegex(ValueError, 'Không đủ giờ trống'):
            replan.free_slots(TIMES, 800, [], self.clock, self.clock+600)


class ReplanTests(unittest.TestCase):
    setUp = fixtures.ScheduleTests.setUp
    video = fixtures.ScheduleTests.video
    schedule = fixtures.ScheduleTests.schedule
    submit = fixtures.ScheduleTests.submit
    attempted = recovery.ScheduleRecoveryTests.attempted

    def past(self, campaign_id, **values):
        row = self.schedule(campaign_id)
        return self.store.update(row['id'], due_at=time.time()-3600, **values)

    def ids(self, *rows):
        return [r['id'] for r in rows]

    def plan(self, rows, **kwargs):
        return replan.build_plan(self.controller, self.account, self.page, self.ids(*rows), kwargs.pop('times', TIMES),
                                 kwargs.pop('start', time.time()+600), **kwargs)

    def test_never_sent_rows_get_new_future_times_in_the_order_of_their_old_times_and_are_armed(self):
        first, second, third = (self.past(c) for c in ('a'*32, 'b'*32, 'c'*32))
        self.store.update(first['id'], due_at=time.time()-5000)
        self.store.update(third['id'], due_at=time.time()-9000, state='paused')
        plan = self.plan([first, second, third])
        self.assertEqual([e['kind'] for e in plan['entries']], ['fresh']*3)
        self.assertEqual(plan['moved'], 3)
        order = sorted(plan['entries'], key=lambda e: e['due_at'])
        self.assertEqual([e['id'] for e in order], [third['id'], first['id'], second['id']])  # Oldest time first.
        results = replan.apply_plan(self.controller, self.account, self.page, plan)
        self.assertEqual([r['outcome'] for r in results], ['armed']*3)
        for entry in plan['entries']:
            saved = self.store.get(entry['id'])
            self.assertEqual((saved['state'], saved['due_at']), ('armed', entry['due_at']))
            self.assertGreater(saved['due_at'], time.time())
            self.assertEqual(saved['attempted_at'], '')
        self.assertEqual(self.controller.active, set(self.ids(first, second, third)))
        self.assertEqual(self.calls, [])  # Nothing was sent to Chrome.

    def test_captions_media_and_posts_are_kept(self):
        row = self.past('a'*32)
        before = (row['body'], row['source'], row['post_id'], self.store.activity.post(self.account, self.page, row['post_id']))
        replan.apply_plan(self.controller, self.account, self.page, self.plan([row]))
        saved = self.store.get(row['id'])
        self.assertEqual((saved['body'], saved['source'], saved['post_id']), before[:3])
        self.assertEqual(self.store.activity.post(self.account, self.page, row['post_id']), before[3])

    def test_a_row_tèo_may_have_sent_is_never_moved(self):
        sent = self.past('a'*32, state='submitted', phase='publish', attempted_at='2026-10-29T09:00:00+07:00')
        unknown = self.past('b'*32, state='needs_attention', phase='verify', attempted_at='2026-10-29T09:00:00+07:00')
        good = self.past('c'*32)
        plan = self.plan([sent, unknown, good])
        self.assertEqual([e['kind'] for e in plan['entries']], ['skip', 'skip', 'fresh'])
        self.assertIn('Tèo đã bấm Đăng', plan['entries'][0]['reason'])
        replan.apply_plan(self.controller, self.account, self.page, plan)
        for original in (sent, unknown):
            self.assertEqual(self.store.get(original['id']), original)
        self.assertEqual(self.controller.active, {good['id']})

    def test_removed_text_active_and_other_state_rows_are_skipped_with_a_reason(self):
        removed = self.past('a'*32, state='removed')
        published = self.past('b'*32, state='published')
        armed = self.schedule('c'*32)
        self.controller.start(armed['id'])
        self.store.update(armed['id'], due_at=time.time()+3600)
        plan = self.plan([removed, published, self.store.get(armed['id'])])
        self.assertEqual([e['kind'] for e in plan['entries']], ['skip']*3)
        self.assertEqual(plan['moved'], 0)
        for entry in plan['entries']:
            self.assertTrue(entry['reason'])
        self.assertIsNone(plan['first'])
        text = dict(id='t', state='scheduled', due_at=1, attempted_at='', phase='', source=dict(kind='text', label='Bài chữ'))
        entry = replan.classify(self.controller, [text])[0]
        self.assertEqual(entry['kind'], 'skip')
        self.assertIn('Bài chữ', entry['reason'])

    def test_a_stopped_preparation_needs_both_confirmations_then_is_recreated_and_armed(self):
        old = self.attempted(campaign_id='a'*32)
        self.store.update(old['id'], due_at=time.time()-3600)
        plan = self.plan([self.store.get(old['id'])])
        self.assertEqual(plan['entries'][0]['kind'], 'recover')
        self.assertEqual(plan['recover'], 1)
        for flags in (dict(), dict(checked_unpublished=True), dict(closed_composer=True)):
            with self.subTest(flags=flags), self.assertRaisesRegex(ValueError, 'xác nhận'):
                replan.apply_plan(self.controller, self.account, self.page, plan, **flags)
            self.assertEqual(self.store.get(old['id'])['post_id'], old['post_id'])
        results = replan.apply_plan(self.controller, self.account, self.page, plan, checked_unpublished=True, closed_composer=True)
        self.assertEqual(results[0]['outcome'], 'armed')
        saved = self.store.get(old['id'])
        self.assertEqual((saved['state'], saved['due_at']), ('armed', plan['entries'][0]['due_at']))
        self.assertNotEqual(saved['post_id'], old['post_id'])
        self.assertEqual(self.store.activity.post(self.account, self.page, saved['post_id'])['state'], 'draft')
        self.assertEqual(self.store.activity.post(self.account, self.page, old['post_id'])['archived'], 1)  # Old evidence kept.
        self.assertEqual(len(self.store.recovery_history(old['id'])), 1)
        self.assertEqual(self.calls, [])

    def test_a_mix_of_both_kinds_shares_one_sequence_of_slots(self):
        fresh = self.past('a'*32)
        stopped = self.attempted(campaign_id='b'*32)
        self.store.update(stopped['id'], due_at=time.time()-100)
        plan = self.plan([fresh, self.store.get(stopped['id'])])
        self.assertEqual({e['kind'] for e in plan['entries']}, {'fresh', 'recover'})
        stamps = sorted(e['due_at'] for e in plan['entries'])
        self.assertEqual(len(set(stamps)), 2)
        results = replan.apply_plan(self.controller, self.account, self.page, plan, checked_unpublished=True, closed_composer=True)
        self.assertEqual([r['outcome'] for r in results], ['armed', 'armed'])

    def test_the_rows_that_are_already_on_the_page_take_their_places_first(self):
        keep = self.schedule('d'*32)
        self.store.update(keep['id'], due_at=replan.free_slots(TIMES, 1, [], time.time(), time.time()+600)[0])
        old = self.past('a'*32)
        plan = self.plan([old])
        self.assertNotEqual(plan['entries'][0]['due_at'], self.store.get(keep['id'])['due_at'])
        self.assertGreater(plan['entries'][0]['due_at'], self.store.get(keep['id'])['due_at'])

    def test_when_the_free_places_change_after_the_preview_nothing_is_changed(self):
        old = self.past('a'*32)
        plan = self.plan([old])
        taker = self.schedule('b'*32)
        self.store.update(taker['id'], due_at=plan['entries'][0]['due_at'])
        with self.assertRaisesRegex(ValueError, 'đã thay đổi'):
            replan.apply_plan(self.controller, self.account, self.page, plan)
        self.assertEqual(self.store.get(old['id']), old)
        self.assertFalse(self.controller.active)

    def test_a_row_of_another_page_is_refused(self):
        other_source = fixtures.campaign_video_source(self.directory, *self.video(campaign_id='e'*32))
        other = self.store.create(self.other, self.other_page, other_source, 'X', '#X', time.time()+3600)
        with self.assertRaisesRegex(ValueError, 'không thuộc'):
            self.plan([other])
        with self.assertRaises(ValueError):
            self.plan([])

    def test_a_failure_on_one_row_does_not_stop_the_others(self):
        good, bad = self.past('a'*32), self.past('b'*32)
        plan = self.plan([good, bad])
        real = self.controller.store.reschedule
        def flaky(ident, due):
            if ident == bad['id']:
                raise ValueError('Synthetic failure')
            return real(ident, due)
        with patch.object(self.controller.store, 'reschedule', side_effect=flaky):
            results = replan.apply_plan(self.controller, self.account, self.page, plan)
        self.assertEqual([r['outcome'] for r in results], ['armed', 'failed'])
        self.assertEqual(self.store.get(bad['id'])['state'], 'scheduled')
        self.assertEqual(self.controller.active, {good['id']})

    def test_a_row_that_cannot_be_armed_keeps_its_new_time_and_says_so(self):
        old = self.past('a'*32)
        plan = self.plan([old])
        with patch.object(self.controller, 'start_many', return_value=[dict(id=old['id'], label='x', outcome='skipped', message='Synthetic')]):
            results = replan.apply_plan(self.controller, self.account, self.page, plan)
        self.assertEqual(results[0]['outcome'], 'moved')
        self.assertIn('Chưa bật được: Synthetic', results[0]['message'])
        self.assertEqual(self.store.get(old['id'])['state'], 'scheduled')


class ReplanDialogTests(unittest.TestCase):
    video = recovery_ui.ScheduleRecoveryUITests.video
    panel = recovery_ui.ScheduleRecoveryUITests.panel
    failed_rows = recovery_ui.ScheduleRecoveryUITests.failed_rows
    rendered_text = staticmethod(recovery_ui.ScheduleRecoveryUITests.rendered_text)
    choose = recovery_ui.ScheduleRecoveryUITests.choose
    start_schedule = recovery_ui.ScheduleRecoveryUITests.start_schedule
    pause_schedule = recovery_ui.ScheduleRecoveryUITests.pause_schedule
    cancel_schedule = recovery_ui.ScheduleRecoveryUITests.cancel_schedule
    setUp = recovery_ui.ScheduleRecoveryUITests.setUp

    def never_sent(self, count):
        rows = []
        for number in range(1, count+1):
            source = dict(self.source, number=number, key='c'*32+':'+str(number), label='Synthetic campaign · Video '+str(number))
            self.sources[number] = source
            row = self.store.create(self.a, self.p, source, 'Caption '+str(number), '#Saved', time.time()+7200)
            rows.append(self.store.update(row['id'], due_at=time.time()-3600*number, state='paused'))
        return rows

    def open_dialog(self, rows):
        panel = self.panel()
        panel.tree.selection_set([row['id'] for row in rows])
        panel.select_row()
        self.assertEqual(str(panel.replan_button['state']), 'normal')
        with patch('teodub.page_schedule_replan_ui.page_times', return_value=TIMES):
            panel.open_replan()
        dialog = panel.replan_window
        self.assertIsNotNone(dialog)
        dialog.withdraw()
        return panel, dialog

    def test_the_button_follows_the_selection(self):
        rows = self.never_sent(1)
        panel = self.panel()
        panel.select_row()
        self.assertEqual(str(panel.replan_button['state']), 'disabled')
        panel.tree.selection_set(rows[0]['id'])
        panel.select_row()
        self.assertEqual(str(panel.replan_button['state']), 'normal')

    def test_opening_shows_the_new_times_and_changes_nothing(self):
        rows = self.never_sent(3)
        _, dialog = self.open_dialog(rows)
        text = self.rendered_text(dialog)
        for expected in ('3 video sẽ được đăng', 'GIỜ MỚI', 'Synthetic campaign · Video 3', '11:30, 19:00'):
            self.assertIn(expected, text)
        self.assertEqual(str(dialog.save_button['state']), 'normal')
        for original in rows:
            self.assertEqual(self.store.get(original['id']), original)
        self.assertFalse(self.controller.active)
        self.owner.submit_page_browser.assert_not_called()

    def test_saving_moves_and_arms_every_selected_video_and_reports(self):
        rows = self.never_sent(3)
        panel, dialog = self.open_dialog(rows)
        dialog.save()
        for original in rows:
            saved = self.store.get(original['id'])
            self.assertEqual(saved['state'], 'armed')
            self.assertGreater(saved['due_at'], time.time())
        self.assertEqual(self.controller.active, {row['id'] for row in rows})
        self.assertIn('Đã bật lịch 3 video', panel.status.get())
        self.assertIn('Synthetic campaign · Video 1', panel.bulk_report)
        self.assertEqual(str(dialog.save_button['state']), 'disabled')  # One click per plan.
        dialog.save()
        self.owner.submit_page_browser.assert_not_called()

    def test_changing_the_times_requires_a_new_preview(self):
        rows = self.never_sent(2)
        _, dialog = self.open_dialog(rows)
        dialog.times.set('08:00, 20:00')
        self.assertEqual(str(dialog.save_button['state']), 'disabled')
        self.assertIn('Xem lịch mới', dialog.status.get())
        dialog.show_plan()
        self.assertEqual(str(dialog.save_button['state']), 'normal')
        self.assertIn('08:00', self.rendered_text(dialog))

    def test_bad_times_show_a_message_and_block_saving(self):
        rows = self.never_sent(1)
        _, dialog = self.open_dialog(rows)
        dialog.times.set('25:00')
        dialog.show_plan()
        self.assertIn('chưa hợp lệ', dialog.status.get())
        self.assertEqual(str(dialog.save_button['state']), 'disabled')

    def test_a_stopped_preparation_needs_the_two_checks_in_the_dialog(self):
        rows = self.failed_rows(2)
        for row in rows:
            self.store.update(row['id'], due_at=time.time()-3600)
        _, dialog = self.open_dialog(rows)
        self.assertEqual(dialog.plan['recover'], 2)
        self.assertEqual(str(dialog.save_button['state']), 'disabled')
        dialog.checked_unpublished.set(True)
        self.assertEqual(str(dialog.save_button['state']), 'disabled')
        dialog.closed_composer.set(True)
        with patch.object(type(dialog), 'browser_ready', return_value=True):
            dialog.refresh_buttons()
            self.assertEqual(str(dialog.save_button['state']), 'normal')
            dialog.save()
        for row in rows:
            self.assertEqual(self.store.get(row['id'])['state'], 'armed')
        self.owner.submit_page_browser.assert_not_called()

    def test_no_saved_page_times_asks_for_them(self):
        rows = self.never_sent(1)
        panel = self.panel()
        panel.tree.selection_set(rows[0]['id'])
        panel.select_row()
        with patch('teodub.page_schedule_replan_ui.page_times', return_value=[]):
            panel.open_replan()
        dialog = panel.replan_window
        dialog.withdraw()
        self.assertEqual(str(dialog.save_button['state']), 'disabled')
        self.assertIn('chưa có giờ riêng', self.rendered_text(dialog))


if __name__ == '__main__':
    unittest.main()
