"""Request dispatch, bounded cancellation and scope guards in disposable stores."""
from copy import deepcopy
import time
import unittest

from tests import test_page_browser_worker as worker_fixture
from tests import test_browser_schedule_integration as desktop_fixture
from teodub.facebook_post_timing import PUBLISH_RESULT_SECONDS, post_action_seconds
from teodub.page_status import summarize


class InspectionWorkerTests(unittest.TestCase):
    setUp = worker_fixture.PagePostWorkerTests.setUp
    close_under_lock = worker_fixture.PagePostWorkerTests.close_under_lock
    browse = worker_fixture.PagePostWorkerTests.browse
    command = worker_fixture.PagePostWorkerTests.command
    run_commands = worker_fixture.PagePostWorkerTests.run_commands
    post_events = worker_fixture.PagePostWorkerTests.post_events

    def inspect_command(self, **extra):
        return self.command(operation='inspect_result', result_url='https://www.facebook.com/reel/123',
                            expected_reconcile={'schedule': {'id': 'e'*32}}, confirmed_attempt=True, **extra)

    def test_inspection_forwards_only_to_readonly_method_with_scoped_cancellation(self):
        command = self.inspect_command()
        self.publisher.inspect_result.return_value = dict(state='unverified', message='No proof')
        self.run_commands(command)
        call = self.publisher.inspect_result.call_args
        self.assertIs(call.args[0], self.context)
        self.assertEqual(call.args[1:], (self.account['id'], 'b'*32, 'c'*32))
        self.assertEqual(call.kwargs['expected_reconcile'], command['expected_reconcile'])
        self.assertEqual(call.kwargs['result_url'], command['result_url'])
        self.assertIs(call.kwargs['confirmed_attempt'], True)
        self.assertIs(call.kwargs['finished'], self.finished)
        self.assertFalse(call.kwargs['cancel'].is_set())
        self.publisher.prepare.assert_not_called()
        self.publisher.publish.assert_not_called()
        self.publisher.focus_review.assert_not_called()
        events = self.post_events('post_result')
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['request_id'], command['request_id'])
        self.assertEqual(events[0]['operation'], 'inspect_result')

    def test_malformed_or_unconfirmed_inspections_do_not_touch_publisher(self):
        for extra in ({'confirmed_attempt': False}, {'confirmed_attempt': 1},
                      {'expected_reconcile': None}, {'result_url': ['https://www.facebook.com/reel/123']}):
            with self.subTest(extra=extra):
                command = self.inspect_command()
                command.update(extra)
                self.finished.clear()
                self.run_commands(command)
        self.publisher.inspect_result.assert_not_called()
        self.publisher.prepare.assert_not_called()
        self.publisher.publish.assert_not_called()
        self.assertEqual(len(self.post_events('post_error')), 4)
        self.assertFalse(self.post_events('post_result'))

    def test_expired_inspection_never_invokes_observer_or_posting(self):
        self.run_commands(self.inspect_command(expires_at=time.monotonic()-1))
        self.publisher.inspect_result.assert_not_called()
        self.publisher.publish.assert_not_called()
        self.assertEqual(len(self.post_events('post_error')), 1)


class InspectionDesktopTests(unittest.TestCase):
    setUp = desktop_fixture.BrowserScheduleIntegrationTests.setUp
    session = desktop_fixture.BrowserScheduleIntegrationTests.session
    poll_owner = desktop_fixture.BrowserScheduleIntegrationTests.poll_owner

    def old_post(self):
        post = self.activity.save_draft(self.a, self.pa, 'Submitted old caption',
                                        delivery_mode='browser', browser_finish_mode='tool')
        for state in ('preparing', 'review', 'submitting', 'processing', 'unknown'):
            post = self.activity.transition(self.a, self.pa, post['id'], state, 'Synthetic '+state)
        return post

    def enqueue(self, post, **options):
        preview = dict(schedule=dict(id='e'*32, account_id=self.a, page_key=self.pa, post_id=post['id']))
        values = dict(result_url='https://www.facebook.com/reel/123', expected_reconcile=preview,
                      confirmed_attempt=True)
        values.update(options)
        return self.owner.submit_page_browser(self.a, self.pa, post['id'], 'inspect_result', **values)

    def reply(self, request_id, result):
        session = self.owner.sessions[self.a]
        session.messages.put(('post_result', dict(request_id=request_id, operation='inspect_result',
            account_id=self.a, page_key=self.pa, post_id=result['id'], result=result, message='Observed only')))
        self.poll_owner()
        return session.post_actions[request_id]

    def test_inspection_budget_is_separate_from_existing_posting_budgets(self):
        self.assertEqual(post_action_seconds('inspect_result', 'video'), 90)
        self.assertEqual(post_action_seconds('inspect_result', 'text'), 90)
        self.assertEqual(post_action_seconds('prepare', 'video'), 1200)
        self.assertEqual(post_action_seconds('publish', 'video'), PUBLISH_RESULT_SECONDS+60)
        self.assertEqual(post_action_seconds('verify', 'video'), PUBLISH_RESULT_SECONDS+60)
        self.assertEqual(post_action_seconds('prepare', 'text'), 360)
        self.assertEqual(post_action_seconds('review', 'video'), 360)

    def test_inspection_captures_immutable_preview_and_does_not_change_saved_attempt(self):
        post = self.old_post()
        session = self.session()
        before_events = self.activity.events(self.a, self.pa, post['id'])
        original = dict(schedule=dict(id='e'*32, account_id=self.a, page_key=self.pa, post_id=post['id']),
                        association=dict(matching_attempt_ids=[post['id']]))
        request_id = self.enqueue(post, expected_reconcile=original)
        self.assertTrue(request_id, self.owner.status.get())
        command = session.commands.get_nowait()
        original['association']['matching_attempt_ids'].append('tampered')
        self.assertEqual(command['expected_reconcile']['association']['matching_attempt_ids'], [post['id']])
        self.assertEqual(command['operation'], 'inspect_result')
        self.assertEqual(session.post_actions[request_id]['schedule_id'], 'e'*32)
        self.assertEqual(session.post_actions[request_id]['timeout_seconds'], 90)
        self.assertEqual(self.activity.post(self.a, self.pa, post['id']), post)
        self.assertEqual(self.activity.events(self.a, self.pa, post['id']), before_events)
        self.browser.assert_not_called()
        self.thread.assert_not_called()

    def test_no_browser_session_does_not_launch_browser_or_queue_a_post(self):
        post = self.old_post()
        self.assertIsNone(self.enqueue(post))
        self.browser.assert_not_called()
        self.thread.assert_not_called()
        self.assertFalse(self.owner.sessions)
        self.assertEqual(self.activity.post(self.a, self.pa, post['id']), post)

    def test_unopened_loading_or_closing_session_cannot_queue_inspection(self):
        post = self.old_post()
        for kind in ('unopened', 'loading', 'cancelled', 'finished'):
            with self.subTest(kind=kind):
                session = self.session(opened=kind!='unopened', loading=kind=='loading')
                if kind == 'cancelled':
                    session.cancel.set()
                if kind == 'finished':
                    session.finished.set()
                self.assertIsNone(self.enqueue(post))
                self.assertTrue(session.commands.empty())
                self.assertFalse(session.post_actions)
        self.thread.assert_not_called()

    def test_busy_same_account_inspection_or_posting_is_not_overwritten(self):
        post = self.old_post()
        session = self.session()
        request_id = self.enqueue(post)
        first = deepcopy({key: value for key, value in session.post_actions[request_id].items() if key!='cancel'})
        self.assertIsNone(self.enqueue(post))
        self.assertEqual(len(session.post_actions), 1)
        self.assertEqual({key: value for key, value in session.post_actions[request_id].items() if key!='cancel'}, first)

    def test_fresh_unsubmitted_draft_or_wrong_page_preview_cannot_queue_inspection(self):
        draft = self.activity.save_draft(self.a, self.pa, 'New draft', delivery_mode='browser', browser_finish_mode='tool')
        session = self.session()
        self.assertIsNone(self.enqueue(draft))
        post = self.old_post()
        wrong = dict(schedule=dict(id='e'*32, account_id=self.b, page_key=self.pb, post_id=post['id']))
        self.assertIsNone(self.enqueue(post, expected_reconcile=wrong))
        self.assertTrue(session.commands.empty())
        self.assertFalse(session.post_actions)

    def test_unverified_result_completes_request_without_transitioning_attempt(self):
        post = self.old_post()
        self.session()
        request_id = self.enqueue(post)
        result = dict(id=post['id'], account_id=self.a, page_key=self.pa, schedule_id='e'*32,
                      state='unverified', message='Insufficient evidence')
        request = self.reply(request_id, result)
        self.assertEqual(request['state'], 'done')
        self.assertEqual(request['result'], result)
        self.assertEqual(self.activity.post(self.a, self.pa, post['id']), post)

    def test_malformed_preview_cannot_leave_orphaned_queued_request(self):
        post = self.old_post()
        session = self.session()
        base = dict(account_id=self.a, page_key=self.pa, post_id=post['id'])
        for saved in ('not a record', [], None, base, dict(base, id=123), dict(base, id='bad')):
            with self.subTest(saved=saved):
                self.assertIsNone(self.enqueue(post, expected_reconcile={'schedule': saved}))
                self.assertFalse(session.post_actions)
                self.assertTrue(session.commands.empty())
        for request_id in (123, [], object()):
            with self.subTest(request_id=request_id):
                self.assertIsNone(self.enqueue(post, request_id=request_id))
                self.assertFalse(session.post_actions)
                self.assertTrue(session.commands.empty())
        self.assertEqual(self.activity.post(self.a, self.pa, post['id']), post)

    def test_wrong_schedule_reply_is_rejected_even_if_account_page_post_match(self):
        post = self.old_post()
        self.session()
        request_id = self.enqueue(post)
        result = dict(id=post['id'], account_id=self.a, page_key=self.pa, schedule_id='f'*32,
                      state='published', message='Wrong scope')
        request = self.reply(request_id, result)
        self.assertEqual(request['state'], 'error')
        self.assertEqual(self.activity.post(self.a, self.pa, post['id']), post)

    def test_timed_out_inspection_cancels_itself_and_ignores_late_success(self):
        post = self.old_post()
        session = self.session()
        request_id = self.enqueue(post)
        session.post_actions[request_id]['expires_at'] = time.monotonic()-1
        self.poll_owner()
        request = session.post_actions[request_id]
        self.assertEqual(request['state'], 'error')
        self.assertTrue(request['cancel'].is_set())
        self.reply(request_id, dict(id=post['id'], account_id=self.a, page_key=self.pa, schedule_id='e'*32,
                                  state='published', message='Late result'))
        self.assertEqual(request['state'], 'error')
        self.assertEqual(self.activity.post(self.a, self.pa, post['id']), post)

    def test_malformed_request_envelopes_do_not_stop_desktop_polling(self):
        post = self.old_post()
        session = self.session()
        request_id = self.enqueue(post)
        for ident in ([], {}, 123, None):
            session.messages.put(('post_error', dict(request_id=ident, operation='inspect_result', message='Malformed')))
        self.poll_owner()
        self.assertEqual(session.post_actions[request_id]['state'], 'queued')
        self.assertTrue(self.owner.timer)
        self.assertIsNone(self.owner.submit_page_browser(self.a, self.pa, post['id'], []))
        self.assertEqual(self.activity.post(self.a, self.pa, post['id']), post)

    def test_foreign_scope_progress_cancels_only_selected_inspection(self):
        post = self.old_post()
        session = self.session()
        other = self.session(self.b)
        request_id = self.enqueue(post)
        session.messages.put(('post_progress', dict(request_id=request_id, operation='inspect_result',
            account_id=self.b, page_key=self.pa, post_id=post['id'], message='Wrong account')))
        self.poll_owner()
        self.assertEqual(session.post_actions[request_id]['state'], 'error')
        self.assertTrue(session.post_actions[request_id]['cancel'].is_set())
        self.assertFalse(other.cancel.is_set())
        self.assertEqual(self.activity.post(self.a, self.pa, post['id']), post)


class VerifiedStatusCopyTests(unittest.TestCase):
    def test_unverified_attempt_and_new_drafts_have_distinct_display_counts(self):
        rows = [dict(id='old', state='needs_attention', phase='publish', due_at=1, message='No receipt'),
                dict(id='new', state='scheduled', due_at=2), dict(id='verified', state='published', due_at=0)]
        before = deepcopy(rows)
        result = summarize(None, [], rows)
        self.assertEqual(result['state'], 'needs_attention')
        self.assertEqual(result['published_posts'], 1)
        self.assertEqual(result['waiting_posts'], 0)
        self.assertIn('Đã xác nhận đăng: 1', result['detail'])
        self.assertIn('Đã lưu, chưa bật: 1', result['detail'])
        self.assertEqual(rows, before)

    def test_preparation_failure_remains_a_failure(self):
        result = summarize(None, [], [dict(id='error', state='needs_attention', phase='prepare', due_at=1,
                                          message='Wrong Page')])
        self.assertIn('lỗi', result['label'])
        self.assertIn('Wrong Page', result['detail'])


if __name__ == '__main__':
    unittest.main()
