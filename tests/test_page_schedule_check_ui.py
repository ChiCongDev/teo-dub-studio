"""Scoped old-attempt inspection UI in disposable stores; never a live browser."""
from copy import deepcopy
import json
from threading import Event
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from tests import test_page_schedule_recovery_ui as recovery_fixtures
from tests import test_page_schedule_ui as fixtures


class ScheduleResultCheckUITests(unittest.TestCase):
    panel = fixtures.PageScheduleUITests.panel
    choose = fixtures.PageScheduleUITests.choose
    start_schedule = fixtures.PageScheduleUITests.start_schedule
    pause_schedule = fixtures.PageScheduleUITests.pause_schedule
    cancel_schedule = fixtures.PageScheduleUITests.cancel_schedule

    def setUp(self):
        recovery_fixtures.ScheduleRecoveryUITests.setUp(self)
        self.controller.reconcile_preview = Mock(wraps=self.controller.reconcile_preview)
        self.controller.inspect_result = Mock(wraps=self.controller.inspect_result)
        self.owner.finish_browser = Mock()
        self.owner.submit_page_browser.side_effect = self.submit_inspection
        self.session = self.owner.sessions[self.a] = SimpleNamespace(
            opened=True, pending=True, loading_pages=False, finished=Event(), cancel=Event(),
            post_actions={}, navigation={}, page_load_requests={})

    def submit_inspection(self, account, page, post, operation, **options):
        self.assertEqual(operation, 'inspect_result')
        ident = options.get('request_id') or 'f'*32
        session = self.owner.sessions[account]
        session.post_actions[ident] = dict(state='queued', message='Synthetic read-only inspection queued',
            account_id=account, page_key=page, post_id=post, operation=operation, result=None, cancel=Event())
        return ident

    def rows(self, count=1, *, submitted=True):
        rows = []
        for number in range(1, count+1):
            source = dict(self.source, number=number, key='c'*32+':'+str(number),
                          label='Synthetic campaign · Video '+str(number))
            self.sources[number] = source
            row = self.store.create(self.a, self.p, source,
                'Full caption '+str(number)+'\nExact second line\n#Việt', '', time.time()+7200)
            if submitted:
                scope = (self.a, self.p, row['post_id'])
                self.store.activity.transition(*scope, 'preparing', 'Synthetic prepare')
                post = self.store.activity.post(*scope)
                self.store.activity.save_browser_review(*scope, dict(version=1, account_id=self.a, account_name='Synthetic A', page_key=self.p,
                    post_id=row['post_id'], target_name=post['target_name'], target_url=post['target_url'],
                    target_id=post['target_id'], body=row['body'], kind='video', prepared_at=post['created_at'], browser_finish_mode='tool',
                    media=[dict(post['media'][0], sha256=source['sha256'])],
                    composer=dict(layout='reel_settings', url=post['target_url'], body=row['body'], observed_body=row['body'],
                                  attachment_count=1, preview_count=1,
                                  video_preview=[dict(duration=30.0, width=1280, height=720)])))
                self.store.activity.transition(*scope, 'submitting', 'Clicked publish exactly once')
                self.store.activity.transition(*scope, 'processing', 'Publish was clicked; waiting for receipt')
                self.store.activity.transition(*scope, 'unknown', 'Receipt timeout; check Facebook')
                row = self.store.update(row['id'], state='paused', phase='publish',
                    attempted_at='2026-10-14T14:35:00+07:00', request_id='old-finished-'+str(number),
                    message='Old unverified attempt remains stopped')
            rows.append(row)
        return rows

    def capture(self):
        rows = self.store.list()
        return dict(rows=rows, posts=self.store.activity.posts(self.a, self.p, include_archived=True),
                    events={row['post_id']: self.store.activity.events(row['account_id'], row['page_key'], row['post_id'])
                            for row in rows})

    def open(self, panel, row):
        self.choose(panel, row)
        panel.check_button.invoke()
        child = panel.check_window
        self.assertIsNotNone(child)
        child.withdraw()
        return child

    def inspect(self, child, url='https://www.facebook.com/reel/987654321'):
        child.result_url.set(url)
        child.confirmed_attempt.set(True)
        child.check_button.invoke()
        self.assertTrue(child.request_id, child.status.get())
        return self.owner.sessions[child.account_id].post_actions[child.request_id]

    def assert_no_posting(self):
        self.controller.start.assert_not_called()
        self.owner.finish_browser.assert_not_called()
        self.assertFalse(self.controller.active)
        for call in self.owner.submit_page_browser.call_args_list:
            self.assertEqual(call.args[3], 'inspect_result')

    def verify_saved_receipt(self, row, *, removed=False):
        """Simulate a worker's completed, persisted observation in temporary DBs."""
        url = 'https://www.facebook.com/reel/987654321'
        self.store.activity.transition(self.a, self.p, row['post_id'], 'published', 'Verified actual Reel',
            external_url=url, external_id='987654321')
        with self.store.activity.database() as conn:
            conn.execute('UPDATE posts SET browser_receipt=? WHERE id=?',
                (json.dumps(dict(source='visible_dom', url=url, schedule_id=row['id'])), row['post_id']))
        if not removed:
            self.store.update(row['id'], state='published', finished_at='2026-10-14T14:37:00+07:00',
                message='Verified actual Reel')
        return dict(self.store.activity.post(self.a, self.p, row['post_id']), schedule_id=row['id'])

    def test_button_visible_and_fresh_draft_not_mappable_to_old_facebook_post(self):
        row = self.rows(submitted=False)[0]
        before = self.capture()
        panel = self.panel()
        self.assertEqual(panel.check_button['text'], 'Kiểm tra lại trạng thái…')
        self.assertTrue(panel.check_button.winfo_manager())
        self.choose(panel, row)
        self.assertEqual(str(panel.check_button['state']), 'disabled')
        self.assertIn('Bản nháp này chưa có lượt gửi', panel.summary.get())
        panel.open_result_check()
        self.assertIsNone(panel.check_window)
        self.controller.reconcile_preview.assert_not_called()
        self.controller.inspect_result.assert_not_called()
        self.owner.submit_page_browser.assert_not_called()
        self.assertEqual(self.capture(), before)

    def test_unknown_submitted_attempt_opens_readonly_preview_with_exact_old_evidence(self):
        row = self.rows()[0]
        before = self.capture()
        panel = self.panel()
        self.choose(panel, row)
        self.assertEqual(str(panel.check_button['state']), 'normal')
        self.assertEqual(panel.tree.item(row['id'], 'values')[2], 'Chưa xác minh đã đăng')
        self.assertEqual(self.store.get(row['id'])['state'], 'paused')
        child = self.open(panel, row)
        content = child.content.get('1.0', 'end-1c')
        for value in (row['id'], row['post_id'], row['source']['label'], row['source']['path'], row['body'],
                      row['attempted_at'], row['message'], 'Synthetic Page', 'Page ID: 100',
                      'Clicked publish exactly once', 'Receipt timeout'):
            self.assertIn(value, content)
        self.assertEqual(str(child.content['state']), 'disabled')
        self.assertFalse(child.confirmed_attempt.get())
        self.assertEqual(child.result_url.get(), '')
        self.assertEqual(str(child.check_button['state']), 'disabled')
        self.assertEqual(self.capture(), before)
        self.controller.inspect_result.assert_not_called()
        self.owner.submit_page_browser.assert_not_called()
        self.assert_no_posting()

    def test_removed_old_attempt_can_be_checked_from_visible_history(self):
        row = self.rows()[0]
        self.controller.remove_many(self.store.removal_preview([row['id']], self.a, self.p), self.a, self.p)
        panel = self.panel()
        self.assertNotIn(row['id'], panel.rows)
        panel.show_removed_check.invoke()
        child = self.open(panel, row)
        self.assertEqual(child.preview['schedule']['state'], 'removed')
        self.assert_no_posting()

    def test_confirmation_required_even_when_direct_reel_url_entered(self):
        row = self.rows()[0]
        before = self.capture()
        child = self.open(self.panel(), row)
        child.result_url.set('https://www.facebook.com/reel/987654321')
        child.inspect()
        self.assertIn('Xác nhận', child.status.get())
        self.controller.inspect_result.assert_not_called()
        self.owner.submit_page_browser.assert_not_called()
        self.assertEqual(self.capture(), before)

    def test_one_explicit_scoped_read_request_and_repeated_click_does_not_duplicate(self):
        row = self.rows()[0]
        before = self.capture()
        child = self.open(self.panel(), row)
        reply = self.inspect(child)
        child.inspect()
        child.check_button.invoke()
        self.controller.inspect_result.assert_called_once()
        args, kwargs = self.controller.inspect_result.call_args
        self.assertEqual(args, (self.a, self.p, row['id']))
        self.assertEqual(kwargs['result_url'], 'https://www.facebook.com/reel/987654321')
        self.assertIs(kwargs['confirmed_attempt'], True)
        self.assertEqual(kwargs['expected_reconcile']['schedule']['id'], row['id'])
        self.assertEqual(str(child.url_entry['state']), 'disabled')
        self.assertEqual(str(child.confirm_check['state']), 'disabled')
        self.assertEqual(reply['state'], 'queued')
        self.assertEqual(self.capture(), before)
        self.assert_no_posting()

    def test_blank_url_is_sent_only_after_explicit_same_attempt_confirmation(self):
        row = self.rows()[0]
        child = self.open(self.panel(), row)
        self.inspect(child, url='')
        self.assertEqual(self.controller.inspect_result.call_args.kwargs['result_url'], '')
        self.assertTrue(self.controller.inspect_result.call_args.kwargs['confirmed_attempt'])
        self.assert_no_posting()

    def test_selection_change_does_not_redirect_captured_attempt(self):
        rows = self.rows(2)
        panel = self.panel()
        child = self.open(panel, rows[0])
        self.choose(panel, rows[1])
        reply = self.inspect(child)
        self.assertEqual(reply['post_id'], rows[0]['post_id'])
        self.assertEqual(self.controller.inspect_result.call_args.args[2], rows[0]['id'])
        self.assert_no_posting()

    def test_page_scope_change_before_submit_creates_no_request(self):
        row = self.rows()[0]
        before = self.capture()
        panel = self.panel()
        child = self.open(panel, row)
        panel.manager.page_key = self.p2
        child.confirmed_attempt.set(True)
        child.inspect()
        self.controller.inspect_result.assert_not_called()
        self.owner.submit_page_browser.assert_not_called()
        self.assertIn('đã thay đổi', child.status.get())
        self.assertEqual(self.capture(), before)

    def test_stale_post_events_after_preview_cannot_be_sent_for_inspection(self):
        row = self.rows()[0]
        child = self.open(self.panel(), row)
        self.store.activity.transition(self.a, self.p, row['post_id'], 'unknown', 'New observation after preview')
        before = self.capture()
        child.confirmed_attempt.set(True)
        child.result_url.set('https://www.facebook.com/reel/987654321')
        child.inspect()
        self.assertFalse(child.request_id)
        self.assertIn('thay đổi', child.status.get())
        self.owner.submit_page_browser.assert_not_called()
        self.assertEqual(self.capture(), before)

    def test_unverified_reply_keeps_old_unknown_state_and_does_not_retry(self):
        row = self.rows()[0]
        before = self.capture()
        child = self.open(self.panel(), row)
        reply = self.inspect(child)
        reply.update(state='done', result=dict(state='unverified', schedule_id=row['id'], message='Author does not match'))
        child.poll()
        self.assertTrue(child.finished)
        self.assertIsNone(child.timer)
        self.assertIn('Chưa xác minh', child.status.get())
        self.assertIn('Author does not match', child.status.get())
        self.assertEqual(self.capture(), before)
        child.poll()
        child.inspect()
        self.controller.inspect_result.assert_called_once()
        self.assert_no_posting()

    def test_background_verifying_reply_keeps_read_only_check_pending(self):
        row = self.rows()[0]
        before = self.capture()
        child = self.open(self.panel(), row)
        reply = self.inspect(child)
        reply.update(state='verifying', message='Synthetic background receipt check')
        child.poll()
        self.assertFalse(child.finished)
        self.assertIsNotNone(child.timer)
        self.assertIn('background receipt check', child.status.get())
        self.assertEqual(self.capture(), before)
        self.assert_no_posting()

    def test_published_reply_alone_does_not_claim_success_without_stored_receipt(self):
        row = self.rows()[0]
        before = self.capture()
        child = self.open(self.panel(), row)
        reply = self.inspect(child)
        reply.update(state='done', result=dict(state='published', schedule_id=row['id']))
        child.poll()
        self.assertIn('Chưa xác nhận đã đăng', child.status.get())
        self.assertNotIn('Đã xác minh bài trên Facebook', child.status.get())
        self.assertEqual(self.capture(), before)
        self.assert_no_posting()

    def test_verified_persisted_receipt_refreshes_old_row_and_reports_actual_link(self):
        row = self.rows()[0]
        panel = self.panel()
        child = self.open(panel, row)
        reply = self.inspect(child)
        reply.update(state='done', result=self.verify_saved_receipt(row))
        child.poll()
        self.assertTrue(child.finished)
        self.assertIn('Đã xác minh bài trên Facebook cho đúng lượt cũ', child.status.get())
        self.assertIn('https://www.facebook.com/reel/987654321', child.status.get())
        self.assertEqual(panel.rows[row['id']]['state'], 'published')
        self.assertEqual(self.store.get(row['id'])['post_id'], row['post_id'])
        self.assert_no_posting()

    def test_verified_removed_old_attempt_keeps_removed_row_hidden(self):
        row = self.rows()[0]
        self.controller.remove_many(self.store.removal_preview([row['id']], self.a, self.p), self.a, self.p)
        panel = self.panel()
        panel.show_removed_check.invoke()
        child = self.open(panel, row)
        reply = self.inspect(child)
        reply.update(state='done', result=self.verify_saved_receipt(row, removed=True))
        child.poll()
        self.assertIn('lịch sử video đã xóa', child.status.get())
        self.assertEqual(self.store.get(row['id'])['state'], 'removed')
        panel.show_removed_check.invoke()
        self.assertNotIn(row['id'], panel.rows)
        self.assert_no_posting()

    def test_wrong_reply_scope_or_operation_cannot_refresh_as_verified(self):
        row = self.rows()[0]
        before = self.capture()
        for key, value in (('account_id', self.b), ('page_key', self.p2), ('post_id', '0'*32), ('operation', 'publish')):
            with self.subTest(key=key):
                panel = self.panel()
                child = self.open(panel, row)
                reply = self.inspect(child)
                reply.update(state='done', result=dict(state='published', schedule_id=row['id']))
                reply[key] = value
                child.poll()
                self.assertIn('Phản hồi không thuộc', child.status.get())
                self.assertEqual(self.capture(), before)
                child.destroy()
                self.session.post_actions.clear()
        self.assert_no_posting()

    def test_wrong_schedule_result_cannot_update_old_row(self):
        row = self.rows()[0]
        before = self.capture()
        child = self.open(self.panel(), row)
        reply = self.inspect(child)
        reply.update(state='done', result=dict(state='published', schedule_id='0'*32))
        child.poll()
        self.assertIn('không thuộc đúng lịch', child.status.get())
        self.assertEqual(self.capture(), before)
        self.assert_no_posting()

    def test_malformed_completed_reply_stops_without_claiming_publication(self):
        row = self.rows()[0]
        before = self.capture()
        child = self.open(self.panel(), row)
        reply = self.inspect(child)
        reply.update(state='done', result=['unexpected', 'response'])
        child.poll()
        self.assertTrue(child.finished)
        self.assertIn('Phản hồi kiểm tra chưa hợp lệ', child.status.get())
        self.assertEqual(self.capture(), before)
        self.assert_no_posting()

    def test_session_replacement_cannot_supply_another_sessions_reply(self):
        row = self.rows()[0]
        before = self.capture()
        child = self.open(self.panel(), row)
        reply = dict(self.inspect(child))
        self.owner.sessions[self.a] = SimpleNamespace(post_actions={child.request_id: reply})
        child.poll()
        self.assertIn('Không còn phản hồi', child.status.get())
        self.assertEqual(self.capture(), before)
        self.assert_no_posting()

    def test_error_reply_stops_poll_and_keeps_unknown_result(self):
        row = self.rows()[0]
        before = self.capture()
        child = self.open(self.panel(), row)
        reply = self.inspect(child)
        reply.update(state='error', message='Synthetic browser timeout')
        child.poll()
        self.assertTrue(child.finished)
        self.assertIsNone(child.timer)
        self.assertIn('Synthetic browser timeout', child.status.get())
        self.assertIn('Trạng thái cũ được giữ', child.status.get())
        self.assertEqual(self.capture(), before)
        self.assert_no_posting()

    def test_page_scope_change_during_poll_cannot_refresh_another_page(self):
        row = self.rows()[0]
        before = self.capture()
        panel = self.panel()
        child = self.open(panel, row)
        self.inspect(child)
        panel.manager.page_key = self.p2
        panel.status.set('Other Page status')
        child.poll()
        self.assertTrue(child.finished)
        self.assertEqual(panel.status.get(), 'Other Page status')
        self.assertEqual(self.capture(), before)
        self.assert_no_posting()

    def test_closing_dialog_cancels_only_its_poll_without_closing_browser_or_request(self):
        row = self.rows()[0]
        before = self.capture()
        panel = self.panel()
        child = self.open(panel, row)
        reply = self.inspect(child)
        self.assertIsNotNone(child.timer)
        child.destroy()
        self.assertIsNone(child.timer)
        self.assertIsNone(panel.check_window)
        self.assertFalse(reply['cancel'].is_set())
        self.assertTrue(self.session.opened)
        self.assertEqual(self.capture(), before)
        self.assert_no_posting()

    def test_one_preview_window_and_panel_cleanup_are_passive(self):
        row = self.rows()[0]
        before = self.capture()
        panel = self.panel()
        child = self.open(panel, row)
        with unittest.mock.patch.object(child, 'lift') as lift:
            panel.open_result_check()
        lift.assert_called_once()
        self.controller.reconcile_preview.assert_called_once()
        panel.destroy()
        self.assertFalse(child.winfo_exists())
        self.assertIsNone(child.timer)
        self.controller.inspect_result.assert_not_called()
        self.owner.submit_page_browser.assert_not_called()
        self.assertEqual(self.capture(), before)
        self.assert_no_posting()


if __name__ == '__main__':
    unittest.main()
