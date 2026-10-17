"""Recreate appointments in hidden Tk, with disposable stores and no browser.

These tests exercise the explicit browser-close boundary and the existing
transactional recovery engine. They never connect to a live Facebook account.
"""
from collections import Counter
from copy import deepcopy
import time
from threading import Event
import tkinter as tk
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from tests import test_page_schedule_recovery_ui as recovery_fixtures
from tests import test_page_schedule_ui as schedule_fixtures
from teodub.page_schedule import display_time, parse_time


class ScheduleRecreateUITests(unittest.TestCase):
    setUp = recovery_fixtures.ScheduleRecoveryUITests.setUp
    failed_rows = recovery_fixtures.ScheduleRecoveryUITests.failed_rows
    approve = recovery_fixtures.ScheduleRecoveryUITests.approve
    assert_no_start = recovery_fixtures.ScheduleRecoveryUITests.assert_no_start
    panel = schedule_fixtures.PageScheduleUITests.panel
    start_schedule = schedule_fixtures.PageScheduleUITests.start_schedule
    pause_schedule = schedule_fixtures.PageScheduleUITests.pause_schedule
    cancel_schedule = schedule_fixtures.PageScheduleUITests.cancel_schedule

    @staticmethod
    def session(**values):
        closing = values.pop('closing', False)
        finished = Event()
        if closing:
            finished.set()
        state = dict(pending=values.get('opened', False) or closing, opened=False,
                     loading_pages=False, finished=finished, account_name='Synthetic A',
                     post_actions={}, navigation={}, page_load_requests={})
        state.update(values)
        return SimpleNamespace(**state)

    def open_recreate(self, rows):
        self.owner.finish_browser = Mock(return_value=True)
        panel = self.panel()
        panel.tree.selection_set([row['id'] for row in rows])
        panel.select_row()
        self.assertEqual(str(panel.recreate_button['state']), 'normal')
        self.assertEqual(panel.recreate_button['text'], 'Bỏ lượt cũ và tạo lịch mới…')
        panel.recreate_button.invoke()
        dialog = panel.recovery_window
        self.assertIsNotNone(dialog)
        dialog.withdraw()
        return panel, dialog

    def capture(self, rows):
        return dict(
            schedules={row['id']: self.store.get(row['id']) for row in rows},
            posts=self.store.activity.posts(self.a, self.p, include_archived=True),
            events={row['post_id']: self.store.activity.events(self.a, self.p, row['post_id'])
                    for row in rows},
            histories={row['id']: self.store.recovery_history(row['id']) for row in rows},
            registry=deepcopy(self.registry.load()))

    def assert_unchanged(self, rows, before):
        self.assertEqual(self.capture(rows), before)
        self.assert_no_start()

    def test_open_preview_and_checks_cannot_close_browser_or_create_draft(self):
        rows = self.failed_rows(2, unknown_last=True)
        self.owner.sessions[self.a] = self.session(opened=True)
        before = self.capture(rows)
        _, dialog = self.open_recreate(rows)
        self.assertEqual(dialog.title(), 'Bỏ lượt cũ và tạo lịch mới')
        content = recovery_fixtures.ScheduleRecoveryUITests.rendered_text(dialog)
        for value in (self.source['path'], self.source['sha256'], '100',
                      rows[0]['body'], rows[1]['body']):
            self.assertIn(value, content)
        self.assertFalse(dialog.checked_unpublished.get())
        self.assertFalse(dialog.closed_composer.get())
        self.assertEqual(str(dialog.save_button['state']), 'disabled')
        self.approve(dialog)
        dialog.poll_browser()
        self.assertEqual(str(dialog.save_button['state']), 'disabled')
        self.assertTrue(dialog.browser_status.get())
        self.owner.finish_browser.assert_not_called()
        self.controller.recover_schedule.assert_not_called()
        self.assert_unchanged(rows, before)

    def test_declining_close_confirmation_preserves_open_session_and_all_data(self):
        rows = self.failed_rows(1)
        session = self.owner.sessions[self.a] = self.session(opened=True)
        before = self.capture(rows)
        _, dialog = self.open_recreate(rows)
        self.approve(dialog)
        with patch('teodub.page_schedule_recovery_ui.messagebox.askyesno', return_value=False) as confirmation:
            dialog.close_browser_button.invoke()
        confirmation.assert_called_once()
        self.assertTrue(session.opened)
        self.owner.finish_browser.assert_not_called()
        self.controller.recover_schedule.assert_not_called()
        self.assert_unchanged(rows, before)

    def test_close_confirmation_names_captured_account_and_warns_about_unsaved_draft(self):
        rows = self.failed_rows(1)
        self.owner.sessions[self.a] = self.session(opened=True)
        _, dialog = self.open_recreate(rows)
        self.approve(dialog)
        with patch('teodub.page_schedule_recovery_ui.messagebox.askyesno', return_value=False) as confirmation:
            dialog.close_browser_button.invoke()
        args, kwargs = confirmation.call_args
        prompt = ' '.join(map(str, args)) + ' ' + ' '.join(map(str, kwargs.values()))
        self.assertIn('Synthetic A', prompt)
        self.assertIn('nháp', prompt.lower())

    def test_close_targets_manager_account_despite_different_outer_selected_account(self):
        rows = self.failed_rows(1)
        self.owner.sessions[self.a] = self.session(opened=True)
        other = self.owner.sessions[self.b] = self.session(opened=True)
        self.registry.select_account(self.b)
        self.owner.account_id = self.b
        self.owner.selected_account_id = self.b
        _, dialog = self.open_recreate(rows)
        self.approve(dialog)
        with patch('teodub.page_schedule_recovery_ui.messagebox.askyesno', return_value=True):
            dialog.close_browser_button.invoke()
        self.owner.finish_browser.assert_called_once_with(account_id=self.a)
        self.assertTrue(other.opened)
        self.assertEqual(self.registry.load()['selected_account_id'], self.b)
        self.assert_no_start()

    def test_new_work_arriving_during_close_confirmation_preserves_browser_and_data(self):
        rows = self.failed_rows(1)
        session = self.owner.sessions[self.a] = self.session(opened=True)
        before = self.capture(rows)
        _, dialog = self.open_recreate(rows)
        self.approve(dialog)

        def work_arrives(*_args, **_kwargs):
            session.navigation['new-request'] = {'state': 'queued'}
            return True

        with patch('teodub.page_schedule_recovery_ui.messagebox.askyesno', side_effect=work_arrives):
            dialog.close_browser_button.invoke()
        self.owner.finish_browser.assert_not_called()
        self.assertFalse(session.finished.is_set())
        self.assertTrue(dialog.status.get())
        self.controller.recover_schedule.assert_not_called()
        self.assert_unchanged(rows, before)

    def test_replaced_session_during_confirmation_cannot_be_closed_by_stale_approval(self):
        rows = self.failed_rows(1)
        original = self.owner.sessions[self.a] = self.session(opened=True)
        replacement = self.session(opened=True)
        before = self.capture(rows)
        _, dialog = self.open_recreate(rows)
        self.approve(dialog)

        def replace_session(*_args, **_kwargs):
            self.owner.sessions[self.a] = replacement
            return True

        with patch('teodub.page_schedule_recovery_ui.messagebox.askyesno', side_effect=replace_session):
            dialog.close_browser_button.invoke()
        self.owner.finish_browser.assert_not_called()
        self.assertFalse(original.finished.is_set())
        self.assertFalse(replacement.finished.is_set())
        self.controller.recover_schedule.assert_not_called()
        self.assert_unchanged(rows, before)

    def test_closing_is_asynchronous_and_repeated_click_does_not_queue_another_close(self):
        rows = self.failed_rows(1)
        session = self.owner.sessions[self.a] = self.session(opened=True)
        _, dialog = self.open_recreate(rows)
        self.approve(dialog)

        def begin_close(*, account_id):
            self.assertEqual(account_id, self.a)
            session.finished.set()
            return True

        self.owner.finish_browser.side_effect = begin_close
        with patch('teodub.page_schedule_recovery_ui.messagebox.askyesno', return_value=True) as confirmation:
            dialog.close_browser_button.invoke()
            dialog.poll_browser()
            self.assertEqual(str(dialog.save_button['state']), 'disabled')
            self.assertEqual(str(dialog.close_browser_button['state']), 'disabled')
            dialog.close_browser_button.invoke()
        confirmation.assert_called_once()
        self.owner.finish_browser.assert_called_once_with(account_id=self.a)
        self.controller.recover_schedule.assert_not_called()
        session.pending = session.opened = False
        dialog.poll_browser()
        self.assertEqual(str(dialog.save_button['state']), 'normal')
        self.assert_no_start()

    def test_closing_or_loading_blocks_close_and_save_without_confirmation(self):
        rows = self.failed_rows(1)
        before = self.capture(rows)
        _, dialog = self.open_recreate(rows)
        self.approve(dialog)
        for flags in (dict(closing=True), dict(loading_pages=True),
                      dict(opened=True, closing=True), dict(opened=True, loading_pages=True)):
            with self.subTest(flags=flags):
                self.owner.sessions[self.a] = self.session(**flags)
                dialog.poll_browser()
                self.assertEqual(str(dialog.save_button['state']), 'disabled')
                self.assertEqual(str(dialog.close_browser_button['state']), 'disabled')
                with patch('teodub.page_schedule_recovery_ui.messagebox.askyesno') as confirmation:
                    dialog.close_browser_button.invoke()
                confirmation.assert_not_called()
                self.owner.finish_browser.assert_not_called()
        self.assert_unchanged(rows, before)

    def test_active_post_navigation_and_page_loading_work_cannot_be_closed(self):
        rows = self.failed_rows(1)
        before = self.capture(rows)
        _, dialog = self.open_recreate(rows)
        self.approve(dialog)
        for table in ('post_actions', 'navigation', 'page_load_requests'):
            for state in ('queued', 'running', 'opening', 'loading_pages', 'verifying'):
                with self.subTest(table=table, state=state):
                    self.owner.sessions[self.a] = self.session(opened=True,
                        **{table: {'synthetic-active-request': {'state': state}}})
                    dialog.poll_browser()
                    self.assertEqual(str(dialog.save_button['state']), 'disabled')
                    self.assertEqual(str(dialog.close_browser_button['state']), 'disabled')
                    with patch('teodub.page_schedule_recovery_ui.messagebox.askyesno') as confirmation:
                        dialog.close_browser_button.invoke()
                    confirmation.assert_not_called()
                    self.owner.finish_browser.assert_not_called()
        self.assert_unchanged(rows, before)

    def test_unrelated_account_active_work_does_not_block_closed_target_account(self):
        rows = self.failed_rows(1)
        self.owner.sessions[self.b] = self.session(opened=True, pending=True,
            post_actions={'other-request': {'state': 'running'}})
        _, dialog = self.open_recreate(rows)
        self.approve(dialog)
        dialog.poll_browser()
        self.assertEqual(str(dialog.save_button['state']), 'normal')
        dialog.save()
        self.assertEqual(dialog.restored, {rows[0]['id']})
        self.assertTrue(self.owner.sessions[self.b].pending)
        self.owner.finish_browser.assert_not_called()
        self.assert_no_start()

    def test_actual_closed_session_requires_both_checks_before_saving(self):
        rows = self.failed_rows(1)
        self.owner.sessions[self.a] = self.session()
        _, dialog = self.open_recreate(rows)
        for checked, closed in ((False, False), (True, False), (False, True), (True, True)):
            with self.subTest(checked=checked, closed=closed):
                dialog.checked_unpublished.set(checked)
                dialog.closed_composer.set(closed)
                dialog.poll_browser()
                self.assertEqual(str(dialog.save_button['state']), 'normal' if checked and closed else 'disabled')
        self.controller.recover_schedule.assert_not_called()
        self.owner.finish_browser.assert_not_called()
        self.assert_no_start()

    def test_completed_old_requests_do_not_block_closed_session(self):
        rows = self.failed_rows(1)
        self.owner.sessions[self.a] = self.session(
            post_actions={'done': {'state': 'done'}, 'failed': {'state': 'error'}},
            navigation={'done': {'state': 'done'}}, page_load_requests={'done': {'state': 'done'}})
        _, dialog = self.open_recreate(rows)
        self.approve(dialog)
        dialog.poll_browser()
        self.assertEqual(str(dialog.save_button['state']), 'normal')
        self.owner.finish_browser.assert_not_called()
        self.assert_no_start()

    def test_recreated_rows_keep_same_schedule_and_source_but_have_new_unarmed_drafts(self):
        rows = self.failed_rows(3, unknown_last=True)
        before = self.capture(rows)
        _, dialog = self.open_recreate(rows)
        self.approve(dialog)
        first = parse_time(dialog.start_time.get())
        dialog.save()
        self.assertEqual(dialog.restored, {row['id'] for row in rows})
        self.assertIn('Đã tạo lại 3/3', dialog.status.get())
        self.assertIn('chưa bật', dialog.status.get().lower())
        for index, old in enumerate(rows):
            saved = self.store.get(old['id'])
            for field in ('id', 'account_id', 'page_key', 'source_key', 'source', 'body', 'created_at'):
                self.assertEqual(saved[field], old[field], field)
            self.assertEqual(saved['state'], 'scheduled')
            self.assertEqual(saved['due_at'], first + index * 1800)
            self.assertNotEqual(saved['post_id'], old['post_id'])
            old_post = self.store.activity.post(self.a, self.p, old['post_id'])
            original = next(post for post in before['posts'] if post['id'] == old['post_id'])
            self.assertEqual(old_post['archived'], 1)
            for key, value in original.items():
                if key not in {'archived', 'updated_at'}:
                    self.assertEqual(old_post[key], value, key)
            self.assertEqual(self.store.activity.events(self.a, self.p, old['post_id']),
                             before['events'][old['post_id']])
            new_post = self.store.activity.post(self.a, self.p, saved['post_id'])
            self.assertEqual(new_post['state'], 'draft')
            self.assertEqual(new_post['attempt'], 0)
            self.assertEqual(new_post['body'], original['body'])
            self.assertEqual(new_post['media'], original['media'])
            history = self.store.recovery_history(old['id'])
            self.assertEqual(len(history), 1)
            self.assertEqual(history[0]['new_post_id'], saved['post_id'])
            self.assertEqual(history[0]['previous']['post']['id'], old['post_id'])
        self.assertEqual(len(self.store.list(self.a, self.p)), 3)
        self.assertEqual(len(self.store.activity.posts(self.a, self.p, include_archived=True)), 6)
        self.assert_no_start()
        self.owner.finish_browser.assert_not_called()

    def test_reopened_browser_between_enable_and_save_refuses_all_writes(self):
        rows = self.failed_rows(1)
        before = self.capture(rows)
        _, dialog = self.open_recreate(rows)
        self.approve(dialog)
        self.assertEqual(str(dialog.save_button['state']), 'normal')
        self.owner.sessions[self.a] = self.session(opened=True)
        dialog.save()
        self.assertFalse(dialog.restored)
        self.assertEqual(str(dialog.save_button['state']), 'disabled')
        self.assert_unchanged(rows, before)

    def test_backend_rechecks_browser_if_it_reopens_during_save(self):
        rows = self.failed_rows(1)
        before = self.capture(rows)
        _, dialog = self.open_recreate(rows)
        self.approve(dialog)

        def reopen_then_recover(*args, **kwargs):
            self.owner.sessions[self.a] = self.session(opened=True)
            return self.real_recover(*args, **kwargs)

        self.controller.recover_schedule.side_effect = reopen_then_recover
        dialog.save()
        self.controller.recover_schedule.assert_called_once()
        self.assertFalse(dialog.restored)
        self.assertTrue(dialog.status.get())
        self.assert_unchanged(rows, before)

    def test_invalid_future_time_or_spacing_never_creates_a_new_draft(self):
        rows = self.failed_rows(1)
        before = self.capture(rows)
        _, dialog = self.open_recreate(rows)
        self.approve(dialog)
        future = dialog.start_time.get()
        for due, interval in (('not a date', 30), (display_time(time.time()-3600), 30),
                              (future, 0), (future, 1441)):
            with self.subTest(due=due, interval=interval):
                dialog.start_time.set(due)
                dialog.interval_minutes.set(interval)
                dialog.save()
                self.controller.recover_schedule.assert_not_called()
                self.assertFalse(dialog.restored)
                self.assert_unchanged(rows, before)

    def test_registry_target_change_after_preview_cannot_silently_retarget_new_draft(self):
        rows = self.failed_rows(1)
        _, dialog = self.open_recreate(rows)
        self.approve(dialog)
        self.registry.save_page(self.a, 'Another target', 'facebook.com/replaced',
                                '999', record_id=self.p)
        before = self.capture(rows)
        dialog.save()
        self.assertFalse(dialog.restored)
        self.assertTrue(dialog.status.get())
        self.assert_unchanged(rows, before)

    def test_partial_failure_retry_never_replays_rows_already_recreated(self):
        rows = self.failed_rows(3)
        _, dialog = self.open_recreate(rows)
        self.approve(dialog)
        counts = Counter()
        failed_id = rows[1]['id']

        def recover(account, page, ident, due_at, **kwargs):
            counts[ident] += 1
            if ident == failed_id and counts[ident] == 1:
                raise ValueError('Synthetic source lock: retry this row only')
            return self.real_recover(account, page, ident, due_at, **kwargs)

        self.controller.recover_schedule.side_effect = recover
        dialog.save()
        self.assertEqual(dialog.restored, {rows[0]['id'], rows[2]['id']})
        self.assertIn('Synthetic source lock', dialog.status.get())
        successful_bindings = {ident: self.store.get(ident)['post_id'] for ident in dialog.restored}
        dialog.save()
        dialog.save()
        self.assertEqual(dialog.restored, {row['id'] for row in rows})
        self.assertEqual(counts, Counter({rows[0]['id']: 1, failed_id: 2, rows[2]['id']: 1}))
        for ident, post_id in successful_bindings.items():
            self.assertEqual(self.store.get(ident)['post_id'], post_id)
        self.assertEqual([len(self.store.recovery_history(row['id'])) for row in rows], [1, 1, 1])
        self.assert_no_start()

    def test_reentrant_save_cannot_create_another_draft(self):
        rows = self.failed_rows(2)
        _, dialog = self.open_recreate(rows)
        self.approve(dialog)

        def recover(*args, **kwargs):
            dialog.save()
            return self.real_recover(*args, **kwargs)

        self.controller.recover_schedule.side_effect = recover
        dialog.save()
        self.assertEqual(self.controller.recover_schedule.call_count, 2)
        self.assertEqual([len(self.store.recovery_history(row['id'])) for row in rows], [1, 1])
        self.assert_no_start()

    def test_destroying_dialog_cancels_poll_and_is_passive(self):
        rows = self.failed_rows(1)
        self.owner.sessions[self.a] = self.session(opened=True)
        before = self.capture(rows)
        panel, dialog = self.open_recreate(rows)
        timer = dialog.browser_timer
        self.assertTrue(timer)
        self.assertIn(timer, self.root.tk.call('after', 'info'))
        dialog.destroy()
        self.assertIsNone(panel.recovery_window)
        self.assertNotIn(timer, self.root.tk.call('after', 'info'))
        self.owner.finish_browser.assert_not_called()
        self.controller.recover_schedule.assert_not_called()
        self.assert_unchanged(rows, before)

    def test_panel_destroy_cancels_child_poll_without_closing_account_or_recreating(self):
        rows = self.failed_rows(1)
        self.owner.sessions[self.a] = self.session(opened=True)
        before = self.capture(rows)
        panel, dialog = self.open_recreate(rows)
        timer = dialog.browser_timer
        panel.destroy()
        self.assertFalse(dialog.winfo_exists())
        self.assertNotIn(timer, self.root.tk.call('after', 'info'))
        self.owner.finish_browser.assert_not_called()
        self.controller.recover_schedule.assert_not_called()
        self.assert_unchanged(rows, before)

    def test_one_shared_modal_prevents_duplicate_previews_and_close_requests(self):
        rows = self.failed_rows(1)
        panel, dialog = self.open_recreate(rows)
        self.controller.recovery_preview.reset_mock()
        panel.open_recovery(recreate=True)
        panel.open_recovery()
        self.assertIs(panel.recovery_window, dialog)
        self.controller.recovery_preview.assert_not_called()
        self.owner.finish_browser.assert_not_called()
        self.assert_no_start()

    def test_mixed_fresh_and_attempted_selection_disables_recreate_without_writes(self):
        rows = self.failed_rows(1)
        source = dict(self.source, number=2, key='c' * 32 + ':2', label='Fresh video')
        self.sources[2] = source
        fresh = self.store.create(self.a, self.p, source, 'Fresh caption', '#Fresh', time.time()+7200)
        rows.append(fresh)
        before = self.capture(rows)
        panel = self.panel()
        panel.tree.selection_set([row['id'] for row in rows])
        panel.select_row()
        self.assertEqual(str(panel.recreate_button['state']), 'disabled')
        panel.open_recovery(recreate=True)
        self.assertIsNone(panel.recovery_window)
        self.controller.recover_schedule.assert_not_called()
        self.assert_unchanged(rows, before)

    def test_published_review_or_submission_evidence_cannot_be_recreated(self):
        rows = self.failed_rows(1)
        old = rows[0]
        fields = {'browser_review': '{"confirmed": true}',
                  'browser_receipt': '{"submitted": true}',
                  'remote_assets': '[{"id": "synthetic-asset"}]',
                  'external_id': 'synthetic-post', 'external_url': 'https://facebook.com/synthetic-post',
                  'published_at': '2026-10-14T10:00:00+07:00'}
        for field, value in fields.items():
            with self.subTest(field=field):
                with self.store.activity.database() as conn:
                    conn.execute('UPDATE posts SET '+field+'=? WHERE id=?', (value, old['post_id']))
                before = self.capture(rows)
                panel = self.panel()
                panel.tree.selection_set(old['id'])
                panel.select_row()
                self.assertEqual(str(panel.recreate_button['state']), 'disabled')
                panel.open_recovery(recreate=True)
                self.assertIsNone(panel.recovery_window)
                self.controller.recover_schedule.assert_not_called()
                self.assert_unchanged(rows, before)
                panel.destroy()
                with self.store.activity.database() as conn:
                    conn.execute('UPDATE posts SET '+field+'=? WHERE id=?',
                        ('{}' if field in {'browser_review', 'browser_receipt'} else '[]'
                         if field == 'remote_assets' else '', old['post_id']))


if __name__ == '__main__':
    unittest.main()
