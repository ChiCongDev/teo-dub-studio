"""02/11/2026: scheduled posts sometimes stopped because Tèo gave up reading a slow Facebook page too early, and a post
that stopped at the last checks looked "sent". Only waiting/reading and wording changed; no Publish click is added.
Disposable stores and fake page objects only; no browser, no Facebook."""
import inspect
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from playwright.sync_api import Error as PlaywrightError

from tests import test_page_schedule as fixtures
from tests import test_page_schedule_recovery as recovery
from teodub import facebook_page_identity as identity
from teodub import facebook_web_post as web
from teodub import page_schedule_replan as replan
from teodub.facebook_web_post import BrowserPagePublisher
from teodub.page_schedule_recovery import stopped_before_publish
from teodub.page_schedule_ui import PageSchedulePanel

LABEL_LATE = 'Dừng trước khi bấm Đăng — Facebook chưa nhận bài'


class Locator:
    def __init__(self, visible):
        self.visible = visible

    def count(self):
        return len(self.visible)

    def nth(self, index):
        return SimpleNamespace(is_visible=lambda: self.visible[index])


class Clock:
    def __init__(self, step):
        self.now, self.step = 0.0, step

    def __call__(self):
        self.now += self.step
        return self.now


class WaitOpenersTests(unittest.TestCase):
    def setUp(self):
        self.publisher = BrowserPagePublisher.__new__(BrowserPagePublisher)
        self.tab = SimpleNamespace(wait_for_timeout=Mock())
        self.main = SimpleNamespace(get_by_role=Mock(return_value=Locator([])))
        self.target = dict(name='Page', page_id='1')

    def wait(self, answers, *, step=1.0, timeout=None):
        calls = iter(answers)
        def openers(*_):
            answer = next(calls)
            if isinstance(answer, Exception):
                raise answer
            return answer
        with patch.object(web, '_page_openers', side_effect=openers) as read, \
                patch.object(web.time, 'monotonic', Clock(step)):
            result = self.publisher._wait_openers(self.tab, self.target, self.main, timeout=timeout)
        return result, read

    def test_the_button_that_shows_up_later_is_found_instead_of_giving_up_at_once(self):
        result, read = self.wait([[], [], [], ['button']])
        self.assertEqual(result, ['button'])
        self.assertEqual(read.call_count, 4)
        self.assertEqual(self.tab.wait_for_timeout.call_count, 3)

    def test_it_stops_after_the_wait_when_the_button_never_shows(self):
        result, read = self.wait([[]]*100, step=1.0, timeout=5)
        self.assertEqual(result, [])
        self.assertLessEqual(read.call_count, 6)

    def test_the_default_wait_is_twenty_seconds(self):
        self.assertEqual(web.OPENER_WAIT_SECONDS, 20)
        result, read = self.wait([[]]*100, step=1.0)
        self.assertEqual(result, [])
        self.assertTrue(18 <= read.call_count <= 22, read.call_count)

    def test_one_switch_button_ends_the_wait_at_once(self):
        self.main.get_by_role.return_value = Locator([True])
        result, read = self.wait([[]]*100)
        self.assertEqual(result, [])
        self.assertEqual(read.call_count, 1)
        self.tab.wait_for_timeout.assert_not_called()

    def test_a_reload_in_the_middle_is_waited_out_and_read_again(self):
        reload_error = PlaywrightError('Execution context was destroyed, most likely because of a navigation')
        with patch('teodub.facebook_page_identity._reloading') as reloading:
            result, _ = self.wait([reload_error, ['button']])
        self.assertEqual(result, ['button'])
        reloading.assert_called_once()

    def test_another_error_is_not_hidden(self):
        with self.assertRaisesRegex(PlaywrightError, 'closed'):
            self.wait([PlaywrightError('Target page, context or browser has been closed')])

    def test_it_never_clicks_anything(self):
        clicker = Mock()
        self.main.click = clicker
        self.wait([[], ['button']])
        clicker.assert_not_called()


class WaitValuesTests(unittest.TestCase):
    def test_slow_pages_get_longer_waits(self):
        self.assertEqual(inspect.signature(BrowserPagePublisher._page_ready).parameters['timeout'].default, 30)
        self.assertEqual(inspect.signature(identity._selector).parameters['timeout'].default, 30)
        self.assertEqual((web.PAGE_READY_SECONDS, identity.SELECTOR_SECONDS, web.REEL_WAIT_SECONDS), (30, 30, 30))
        self.assertEqual(web.REEL_NEXT_CLICK_SECONDS, 30)

    def test_each_reel_next_click_is_one_click_that_may_wait_for_an_overlay_to_go(self):
        clicks = []
        def element():
            return SimpleNamespace(is_visible=lambda: True, click=lambda **kw: clicks.append(kw))
        class Scope:
            def get_by_role(self, *_a, **_k):
                return SimpleNamespace(count=lambda: 1, nth=lambda i: element())
        publisher = BrowserPagePublisher.__new__(BrowserPagePublisher)
        tab = SimpleNamespace(_teodub_page_actor=dict(page_id='1'))
        initial = dict(videos=[dict(src='blob:x')], upload_complete=True, identity='i', files=[], preview_count=1)
        with patch.object(BrowserPagePublisher, '_wait_reel', side_effect=[(Scope(), dict(videos=[])), (Scope(), dict())]):
            publisher._advance_reel(tab, dict(page_id='1'), Scope(), initial)
        self.assertEqual(clicks, [dict(timeout=30000), dict(timeout=30000)])  # Exactly two clicks, never repeated.


class LateStopTests(unittest.TestCase):
    setUp = fixtures.ScheduleTests.setUp
    video = fixtures.ScheduleTests.video
    schedule = fixtures.ScheduleTests.schedule
    submit = fixtures.ScheduleTests.submit

    def stopped(self, campaign_id='a'*32, *, up_to='review', phase='publish', message='Chưa đọc được danh tính đang chọn'):
        row = self.schedule(campaign_id)
        activity, scope = self.store.activity, (self.account, self.page, row['post_id'])
        activity.transition(*scope, 'preparing', 'Synthetic preparation', expected={'draft'})
        if up_to != 'preparing':
            activity.save_browser_review(*scope, dict(browser_finish_mode='tool', media=[], composer={}))
        if up_to in {'submitting', 'processing'}:
            activity.transition(*scope, 'submitting', 'Synthetic submitting', expected={'review'})
        if up_to == 'processing':
            activity.transition(*scope, 'processing', 'Synthetic processing', expected={'submitting'})
        activity.transition(*scope, 'unknown', message, expected={'review', 'submitting', 'processing', 'preparing'})
        return self.store.update(row['id'], state='needs_attention', phase=phase, request_id='r-'+campaign_id[:1],
                                 attempted_at='2026-11-02T18:36:56+07:00', due_at=time.time()-3600)

    def events(self, row):
        return self.store.activity.events(self.account, self.page, row['post_id'])

    def post(self, row):
        return self.store.activity.post(self.account, self.page, row['post_id'])

    def test_a_post_that_stopped_at_the_last_checks_never_clicked_publish(self):
        row = self.stopped()
        self.assertTrue(stopped_before_publish(row, self.post(row), self.events(row)))
        self.assertTrue(self.store.can_recover_schedule(row))

    def test_anything_that_reached_submitting_or_processing_is_not_recoverable(self):
        for up_to in ('submitting', 'processing'):
            with self.subTest(up_to=up_to):
                row = self.stopped(('b' if up_to == 'submitting' else 'c')*32, up_to=up_to)
                self.assertFalse(stopped_before_publish(row, self.post(row), self.events(row)))
                self.assertFalse(self.store.can_recover_schedule(row))

    def test_no_preview_or_another_phase_is_not_this_case(self):
        no_review = self.stopped('d'*32, up_to='preparing')
        self.assertFalse(stopped_before_publish(no_review, self.post(no_review), self.events(no_review)))
        verify = self.stopped('e'*32, phase='verify')
        self.assertFalse(self.store.can_recover_schedule(verify))

    def test_a_manual_result_link_or_receipt_keeps_it_out(self):
        row = self.stopped()
        with self.store.activity.database() as conn:
            conn.execute("UPDATE posts SET manual_url='https://www.facebook.com/x/posts/1' WHERE id=?", (row['post_id'],))
        self.assertFalse(self.store.can_recover_schedule(row))

    def test_recovering_it_makes_a_new_draft_and_keeps_the_old_evidence(self):
        row = self.stopped()
        preview = self.controller.recovery_preview(self.account, self.page, row['id'])
        result = self.controller.recover_schedule(self.account, self.page, row['id'], time.time()+7200,
            expected_preview=preview, checked_unpublished=True, closed_composer=True)
        self.assertEqual((result['state'], result['phase'], result['attempted_at']), ('scheduled', '', ''))
        self.assertNotEqual(result['post_id'], row['post_id'])
        self.assertEqual(self.store.activity.post(self.account, self.page, result['post_id'])['state'], 'draft')
        old = self.post(row)
        self.assertEqual((old['archived'], old['state']), (1, 'unknown'))
        self.assertTrue(old['browser_review'])
        self.assertEqual(self.calls, [])

    def test_it_still_needs_both_confirmations(self):
        row = self.stopped()
        preview = self.controller.recovery_preview(self.account, self.page, row['id'])
        for flags in (dict(checked_unpublished=False, closed_composer=True), dict(checked_unpublished=True, closed_composer=False)):
            with self.subTest(flags=flags), self.assertRaisesRegex(ValueError, 'Xác nhận'):
                self.controller.recover_schedule(self.account, self.page, row['id'], time.time()+7200,
                                                 expected_preview=preview, **flags)
        self.assertEqual(self.store.get(row['id'])['post_id'], row['post_id'])

    def test_the_bulk_re_plan_moves_it_but_not_a_post_that_clicked_publish(self):
        late = self.stopped('a'*32)
        clicked = self.stopped('b'*32, up_to='processing')
        entries = replan.classify(self.controller, [late, clicked])
        self.assertEqual([e['kind'] for e in entries], ['recover', 'skip'])
        self.assertIn('Tèo đã bấm Đăng', entries[1]['reason'])

    def test_the_row_says_publish_was_not_clicked_only_for_the_late_stop(self):
        late = self.stopped('a'*32)
        clicked = self.stopped('b'*32, up_to='processing')
        panel = SimpleNamespace(store=self.controller.store)
        self.assertEqual(PageSchedulePanel.row_state_label(panel, late), LABEL_LATE)
        self.assertEqual(PageSchedulePanel.row_state_label(panel, clicked), 'Chưa xác minh đã đăng')
        submitted = self.store.update(self.schedule('c'*32)['id'], state='submitted')
        self.assertEqual(PageSchedulePanel.row_state_label(panel, submitted), 'Đã gửi — chưa xác minh')


if __name__ == '__main__':
    unittest.main()
