"""A new, explicit attempt in disposable Tk stores; no live browser or posting."""
from copy import deepcopy
from pathlib import Path
from threading import Event
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from tests import test_page_schedule_add_ui as add_fixtures
from tests import test_page_schedule_recovery_ui as recovery_fixtures
from tests import test_page_schedule_ui as schedule_fixtures
from teodub.page_schedule import display_time
from teodub.page_schedule_recovery_ui import ScheduleRecoveryDialog


class NewAttemptUITests(unittest.TestCase):
    panel = schedule_fixtures.PageScheduleUITests.panel
    start_schedule = schedule_fixtures.PageScheduleUITests.start_schedule
    pause_schedule = schedule_fixtures.PageScheduleUITests.pause_schedule
    cancel_schedule = schedule_fixtures.PageScheduleUITests.cancel_schedule
    form = add_fixtures.AddScheduleTests.form
    body = add_fixtures.AddScheduleTests.body
    video_form = add_fixtures.AddScheduleTests.video_form

    def setUp(self):
        recovery_fixtures.ScheduleRecoveryUITests.setUp(self)
        self.controller.new_attempt_preview = Mock(wraps=self.controller.new_attempt_preview)
        self.controller.create_new_attempt = Mock(wraps=self.controller.create_new_attempt)
        self.owner.finish_browser = Mock(return_value=True)

    @staticmethod
    def session(**values):
        finished = Event()
        if values.pop('closing', False):
            finished.set()
        state = dict(opened=False, pending=False, loading_pages=False, finished=finished,
                     account_name='Synthetic A', post_actions={}, navigation={}, page_load_requests={})
        state.update(values)
        return SimpleNamespace(**state)

    def removed_rows(self, count=1, *, published=False):
        rows = []
        for number in range(1, count+1):
            source = dict(self.source, number=number, key='c'*32+':'+str(number),
                          label='Synthetic campaign · Video '+str(number))
            self.sources[number] = source
            row = self.store.create(self.a, self.p, source, 'Old caption '+str(number),
                                    '#Original #Việt', time.time()+7200)
            scope = (self.a, self.p, row['post_id'])
            self.store.activity.transition(*scope, 'preparing', 'Prepared approved media')
            self.store.activity.transition(*scope, 'submitting', 'Clicked publish once')
            self.store.activity.transition(*scope, 'published' if published else 'unknown',
                'Real receipt' if published else 'Receipt timeout; inspect Facebook',
                **({'external_url': 'https://www.facebook.com/synthetic/posts/987654321'} if published else {}))
            self.store.update(row['id'], state='published' if published else 'paused', phase='publish',
                attempted_at='2026-10-14T14:35:00+07:00', request_id='old-finished-'+str(number))
            result = self.controller.remove_many(self.store.removal_preview([row['id']], self.a, self.p), self.a, self.p)
            self.assertEqual(result['removed'], [row['id']])
            rows.append(self.store.get(row['id']))
        return rows

    def capture(self):
        rows = self.store.list()
        return dict(rows=rows, posts=self.store.activity.posts(self.a, self.p, include_archived=True),
                    events={row['post_id']: self.store.activity.events(row['account_id'], row['page_key'], row['post_id'])
                            for row in rows},
                    histories={row['id']: self.store.new_attempt_history(row['id']) for row in rows})

    def assert_no_automatic_action(self):
        self.controller.start.assert_not_called()
        self.owner.submit_page_browser.assert_not_called()
        self.owner.finish_browser.assert_not_called()
        self.assertFalse(self.controller.active)

    def approve(self, dialog):
        dialog.checked_unpublished.set(True)
        dialog.closed_composer.set(True)
        dialog.start_time.set(display_time(time.time()+3600))
        dialog.interval_minutes.set(10)

    def new_dialog(self, rows):
        panel = self.panel()
        dialog = ScheduleRecoveryDialog(panel, [row['id'] for row in rows], recreate=True, new_attempt=True)
        panel.recovery_window = dialog
        dialog.withdraw()
        return panel, dialog

    def test_reserved_removed_selection_exposes_new_attempt_without_normal_save_bypass(self):
        rows = self.removed_rows()
        before = self.capture()
        panel, form = self.video_form()
        self.assertIsNone(form.source)
        self.assertEqual(form.reserved_removed_id, rows[0]['id'])
        self.assertIn('Lượt cũ đã xóa — cần xác nhận đăng lại', form.video_picker['values'][0])
        self.assertIn('caption của lượt cũ', form.video_status.get())
        self.assertEqual(str(form.new_attempt_button['state']), 'normal')
        self.assertEqual(form.new_attempt_button['text'], 'Tạo lượt đăng mới…')
        self.body(form, 'Unsaved alternative caption')
        form.save(True)
        self.assertIn('Bấm Tạo lượt đăng mới', form.status.get())
        self.assertEqual(self.capture(), before)
        self.controller.create_new_attempt.assert_not_called()
        self.assert_no_automatic_action()

    def test_confirmed_published_removed_video_does_not_offer_new_attempt(self):
        rows = self.removed_rows(published=True)
        before = self.capture()
        panel, form = self.video_form()
        self.assertIsNone(form.source)
        self.assertFalse(form.reserved_removed_id)
        self.assertEqual(str(form.new_attempt_button['state']), 'disabled')
        self.assertIn('bằng chứng đã đăng', form.video_status.get())
        form.open_new_attempt()
        self.assertIsNone(form.new_attempt_window)
        self.assertEqual(self.capture(), before)
        self.controller.create_new_attempt.assert_not_called()
        self.assert_no_automatic_action()

    def test_add_child_preview_retains_typed_caption_and_displays_old_publish_evidence(self):
        rows = self.removed_rows()
        self.owner.sessions[self.a] = self.session(opened=True, pending=True)
        before = self.capture()
        panel, form = self.video_form()
        self.body(form, 'Caption still being edited')
        form.new_attempt_button.invoke()
        child = form.new_attempt_window
        self.assertIsNotNone(child)
        child.withdraw()
        self.assertEqual(child.title(), 'Tạo lượt đăng mới')
        self.assertEqual(child.save_button['text'], 'Tạo lượt mới (chưa bật)')
        content = child.content.get('1.0', 'end-1c')
        for value in (rows[0]['body'], 'publish', 'unknown', 'Clicked publish once', 'Receipt timeout',
                      self.source['sha256'], self.source['path']):
            self.assertIn(value, content)
        self.assertNotIn('Caption still being edited', content)
        self.assertFalse(child.checked_unpublished.get())
        self.assertFalse(child.closed_composer.get())
        self.assertEqual(str(child.save_button['state']), 'disabled')
        self.approve(child)
        child.poll_browser()
        self.assertEqual(str(child.save_button['state']), 'disabled')
        child.save()
        self.assertEqual(self.capture(), before)
        self.controller.create_new_attempt.assert_not_called()
        self.assertEqual(form.body.get('1.0', 'end-1c'), 'Caption still being edited')
        self.assert_no_automatic_action()

    def test_new_attempt_requires_both_explicit_checks_with_actual_closed_session(self):
        rows = self.removed_rows()
        before = self.capture()
        panel, child = self.new_dialog(rows)
        for checked, closed in ((False, False), (True, False), (False, True)):
            with self.subTest(checked=checked, closed=closed):
                child.checked_unpublished.set(checked)
                child.closed_composer.set(closed)
                self.assertEqual(str(child.save_button['state']), 'disabled')
                child.save()
                self.assertEqual(self.capture(), before)
        self.controller.create_new_attempt.assert_not_called()
        self.assert_no_automatic_action()

    def test_creation_uses_old_caption_new_ids_and_remains_unarmed(self):
        rows = self.removed_rows()
        original_post = self.store.activity.post(self.a, self.p, rows[0]['post_id'])
        original_events = self.store.activity.events(self.a, self.p, rows[0]['post_id'])
        panel, form = self.video_form()
        self.body(form, 'Keep my unsaved caption')
        form.new_attempt_button.invoke()
        child = form.new_attempt_window
        child.withdraw()
        self.approve(child)
        child.save_button.invoke()
        created_id = child.created_ids[rows[0]['id']]
        created = self.store.get(created_id)
        self.assertNotEqual(created_id, rows[0]['id'])
        self.assertNotEqual(created['post_id'], rows[0]['post_id'])
        self.assertEqual(created['state'], 'scheduled')
        self.assertFalse(created['attempted_at'])
        self.assertEqual(created['body'], rows[0]['body'])
        self.assertEqual(created['source'], rows[0]['source'])
        self.assertEqual(self.store.activity.post(self.a, self.p, rows[0]['post_id']), original_post)
        self.assertEqual(self.store.activity.events(self.a, self.p, rows[0]['post_id']), original_events)
        self.assertEqual(self.store.get(rows[0]['id'])['state'], 'removed')
        self.assertEqual(panel.tree.selection(), (created_id,))
        self.assertIn('Chưa bật tự động đăng', child.status.get())
        self.assertEqual(form.body.get('1.0', 'end-1c'), 'Keep my unsaved caption')
        self.assert_no_automatic_action()

    def test_repeated_save_creates_only_one_fresh_attempt_and_one_journal(self):
        rows = self.removed_rows()
        panel, child = self.new_dialog(rows)
        self.approve(child)
        child.save()
        child.save()
        child.save_button.invoke()
        self.controller.create_new_attempt.assert_called_once()
        self.assertEqual(len(self.store.list()), 2)
        self.assertEqual(len(self.store.new_attempt_history(rows[0]['id'])), 1)
        self.assertEqual(str(child.save_button['state']), 'disabled')
        self.assert_no_automatic_action()

    def test_declining_explicit_browser_close_keeps_every_record_and_session(self):
        rows = self.removed_rows()
        self.owner.sessions[self.a] = self.session(opened=True, pending=True)
        before = self.capture()
        panel, child = self.new_dialog(rows)
        self.approve(child)
        with patch('teodub.page_schedule_recovery_ui.messagebox.askyesno', return_value=False):
            child.close_browser_button.invoke()
        self.assertTrue(self.owner.sessions[self.a].opened)
        self.assertEqual(self.capture(), before)
        self.controller.create_new_attempt.assert_not_called()
        self.assert_no_automatic_action()

    def test_explicit_close_waits_until_the_captured_account_is_closed(self):
        rows = self.removed_rows()
        session = self.owner.sessions[self.a] = self.session(opened=True, pending=True)
        other = self.owner.sessions[self.b] = self.session(opened=True, pending=True)
        panel, child = self.new_dialog(rows)
        self.approve(child)
        def close_account(*, account_id):
            self.assertEqual(account_id, self.a)
            session.finished.set()
            return True
        self.owner.finish_browser.side_effect = close_account
        with patch('teodub.page_schedule_recovery_ui.messagebox.askyesno', return_value=True):
            child.close_browser_button.invoke()
        self.owner.finish_browser.assert_called_once_with(account_id=self.a)
        child.poll_browser()
        self.assertEqual(str(child.save_button['state']), 'disabled')
        self.assertTrue(other.opened)
        self.controller.create_new_attempt.assert_not_called()
        session.opened = session.pending = False
        child.poll_browser()
        self.assertEqual(str(child.save_button['state']), 'normal')
        self.controller.start.assert_not_called()
        self.owner.submit_page_browser.assert_not_called()

    def test_partial_failure_retries_only_the_failed_old_video(self):
        rows = self.removed_rows(2)
        panel, child = self.new_dialog(rows)
        self.approve(child)
        actual = self.controller.create_new_attempt._mock_wraps
        fail_once = {rows[1]['id']}
        def create(account, page, ident, due, **approval):
            if ident in fail_once:
                fail_once.remove(ident)
                raise ValueError('Synthetic transient storage failure')
            return actual(account, page, ident, due, **approval)
        self.controller.create_new_attempt.side_effect = create
        child.save()
        first = child.created_ids[rows[0]['id']]
        self.assertEqual(child.restored, {rows[0]['id']})
        self.assertIn('1/2', child.status.get())
        child.save()
        self.assertEqual(child.restored, {row['id'] for row in rows})
        self.assertEqual(child.created_ids[rows[0]['id']], first)
        self.assertEqual(self.controller.create_new_attempt.call_count, 3)
        self.assertEqual(len(self.store.list()), 4)
        self.assert_no_automatic_action()

    def test_stale_post_event_after_preview_blocks_new_draft(self):
        rows = self.removed_rows()
        panel, child = self.new_dialog(rows)
        self.approve(child)
        self.store.activity.transition(self.a, self.p, rows[0]['post_id'], 'unknown', 'New observation after preview')
        before = self.capture()
        child.save()
        self.assertIn('đã thay đổi', child.status.get())
        self.assertEqual(self.capture(), before)
        self.assertFalse(child.created_ids)
        self.assert_no_automatic_action()

    def test_changed_page_scope_during_preview_blocks_new_attempt(self):
        rows = self.removed_rows()
        panel, child = self.new_dialog(rows)
        self.approve(child)
        panel.manager.page_key = self.p2
        before = self.capture()
        child.save()
        self.assertIn('đã thay đổi', child.status.get())
        self.controller.create_new_attempt.assert_not_called()
        self.assertEqual(self.capture(), before)
        self.assert_no_automatic_action()

    def test_changed_media_after_preview_does_not_create_an_attempt(self):
        rows = self.removed_rows()
        panel, child = self.new_dialog(rows)
        self.approve(child)
        Path(self.source['path']).write_bytes(b'Changed video content')
        before = self.capture()
        child.save()
        self.assertFalse(child.created_ids)
        self.assertIn('thay đổi', child.status.get())
        self.assertEqual(self.capture(), before)
        self.assert_no_automatic_action()

    def test_closing_child_preserves_unsaved_parent_caption_and_restores_parent_grab(self):
        self.removed_rows()
        panel, form = self.video_form()
        self.body(form, 'Unsaved parent caption')
        form.new_attempt_button.invoke()
        child = form.new_attempt_window
        child.withdraw()
        before = self.capture()
        with patch.object(form, 'grab_set') as grab:
            child.destroy()
        grab.assert_called_once()
        self.assertIsNone(form.new_attempt_window)
        self.assertIsNone(panel.recovery_window)
        self.assertEqual(form.body.get('1.0', 'end-1c'), 'Unsaved parent caption')
        self.assertIsNone(child.browser_timer)
        self.assertEqual(self.capture(), before)
        self.assert_no_automatic_action()

    def test_parent_close_destroys_child_and_cancels_its_poll(self):
        self.removed_rows()
        panel, form = self.video_form()
        form.new_attempt_button.invoke()
        child = form.new_attempt_window
        child.withdraw()
        self.assertIsNotNone(child.browser_timer)
        before = self.capture()
        form.close()
        self.assertFalse(child.winfo_exists())
        self.assertIsNone(child.browser_timer)
        self.assertIsNone(panel.recovery_window)
        self.assertEqual(self.capture(), before)
        self.assert_no_automatic_action()

    def test_repeated_open_lifts_one_child_without_creating_an_attempt(self):
        self.removed_rows()
        panel, form = self.video_form()
        form.new_attempt_button.invoke()
        child = form.new_attempt_window
        child.withdraw()
        with patch.object(child, 'lift') as lift:
            form.open_new_attempt()
        lift.assert_called_once()
        self.assertIs(form.new_attempt_window, child)
        self.controller.create_new_attempt.assert_not_called()
        self.assert_no_automatic_action()

    def test_new_attempt_creation_updates_parent_warning_and_keeps_unsaved_body(self):
        rows = self.removed_rows()
        panel, form = self.video_form()
        self.body(form, 'Retained editing text')
        form.new_attempt_button.invoke()
        child = form.new_attempt_window
        child.withdraw()
        self.approve(child)
        child.save()
        new_id = child.created_ids[rows[0]['id']]
        child.destroy()
        self.assertFalse(form.reserved_removed_id)
        self.assertEqual(str(form.new_attempt_button['state']), 'disabled')
        self.assertIsNone(form.source)
        self.assertIn('Đã có lịch', form.video_picker['values'][0])
        self.assertIn('Chưa bật tự động đăng', form.status.get())
        self.assertEqual(form.body.get('1.0', 'end-1c'), 'Retained editing text')
        self.assertEqual(panel.tree.selection(), (new_id,))
        self.assert_no_automatic_action()


if __name__ == '__main__':
    unittest.main()
