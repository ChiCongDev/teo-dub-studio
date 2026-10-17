"""Explicit recovery of a known pre-input locator failure, in temporary stores."""
import time
import unittest
from unittest.mock import Mock
from tests import test_page_schedule as fixtures
from tests import test_page_schedule_ui as ui_fixtures
from teodub.page_schedule import display_time

FAILURE = 'Không xác định duy nhất vùng Page (2 mục). Chrome giữ nguyên để kiểm tra; chưa bấm Đăng.'


class PreparationRetryTests(unittest.TestCase):
    setUp = fixtures.ScheduleTests.setUp
    video = fixtures.ScheduleTests.video
    schedule = fixtures.ScheduleTests.schedule
    submit = fixtures.ScheduleTests.submit

    def failure(self):
        row = self.schedule()
        activity = self.store.activity
        activity.transition(self.account, self.page, row['post_id'], 'preparing', 'Preparing', expected={'draft'})
        activity.transition(self.account, self.page, row['post_id'], 'failed', FAILURE, expected={'preparing'})
        return self.store.update(row['id'], state='needs_attention', phase='prepare',
            request_id='test request', attempted_at='2026-10-10T11:45:01+07:00', message=FAILURE)

    def test_explicit_recovery_preserves_previous_attempt_caption_page_and_video(self):
        old = self.failure()
        self.assertTrue(self.store.can_retry_preparation(old))
        due = time.time()+3600
        row = self.controller.retry_preparation(self.account, self.page, old['id'], due)
        self.assertEqual(row['state'], 'scheduled')
        self.assertFalse(row['attempted_at'])
        self.assertFalse(row['request_id'])
        for key in ('id','post_id','account_id','page_key','body','snapshot','source','source_key','created_at'):
            self.assertEqual(row[key], old[key], key)
        history = self.store.preparation_retry_history(old['id'])
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]['previous'], old)
        self.assertEqual(history[0]['new_due_at'], due)
        self.assertFalse(self.calls)
        self.assertNotIn(row['id'], self.controller.active)
        with self.assertRaises(ValueError):
            self.controller.retry_preparation(self.account, self.page, row['id'], time.time()+7200)
        self.assertEqual(len(self.store.preparation_retry_history(old['id'])), 1)
        self.controller.start(row['id'])
        self.assertEqual(self.store.get(row['id'])['state'], 'armed')
        self.assertFalse(self.calls)

    def test_unknown_result_or_composer_review_never_recovered(self):
        row = self.failure()
        self.store.activity.transition(self.account, self.page, row['post_id'], 'unknown', FAILURE, expected={'failed'})
        self.assertFalse(self.store.can_retry_preparation(row))
        with self.assertRaises(ValueError):
            self.controller.retry_preparation(self.account, self.page, row['id'], time.time()+3600)
        self.assertEqual(self.store.get(row['id']), row)
        self.assertFalse(self.store.preparation_retry_history(row['id']))

    def test_non_locator_failed_message_is_not_a_retry_permission(self):
        row = self.failure()
        self.store.activity.transition(self.account, self.page, row['post_id'], 'failed', 'Upload failed')
        row = self.store.update(row['id'], message='Upload failed')
        self.assertFalse(self.store.can_retry_preparation(row))
        with self.assertRaises(ValueError):
            self.store.reschedule_failed_preparation(row['id'], time.time()+3600)

    def test_nested_dialog_fault_recovers_only_when_pre_input_failed(self):
        row = self.failure()
        message = 'Không xác định duy nhất hộp Tạo bài viết (2 mục). Chrome giữ nguyên để kiểm tra; chưa bấm Đăng.'
        self.store.activity.transition(self.account, self.page, row['post_id'], 'failed', message)
        row = self.store.update(row['id'], message=message)
        self.assertTrue(self.store.can_retry_preparation(row))
        result = self.controller.retry_preparation(self.account, self.page, row['id'], time.time()+3600)
        self.assertEqual(result['state'], 'scheduled')
        self.assertEqual(self.store.preparation_retry_history(row['id'])[0]['previous'], row)
        self.assertFalse(self.calls)

    def test_wrong_page_or_locked_schedule_cannot_reset_attempt(self):
        row = self.failure()
        with self.assertRaisesRegex(ValueError, 'không thuộc'):
            self.controller.retry_preparation(self.other, self.other_page, row['id'], time.time()+3600)
        with self.controller.guard(row['id']):
            with self.assertRaises(ValueError):
                self.controller.retry_preparation(self.account, self.page, row['id'], time.time()+3600)
        self.assertEqual(self.store.get(row['id']), row)

    def test_changed_draft_or_source_blocks_recovery(self):
        row = self.failure()
        with self.store.activity.database() as conn:
            conn.execute('UPDATE posts SET body=? WHERE id=?', ('Changed caption', row['post_id']))
        self.assertFalse(self.store.can_retry_preparation(row))
        with self.assertRaises(ValueError):
            self.controller.retry_preparation(self.account, self.page, row['id'], time.time()+3600)
        self.assertEqual(self.store.get(row['id']), row)

    def test_expired_time_keeps_original_failure_and_no_history(self):
        row = self.failure()
        with self.assertRaisesRegex(ValueError, 'tương lai'):
            self.controller.retry_preparation(self.account, self.page, row['id'], time.time()-60)
        self.assertEqual(self.store.get(row['id']), row)
        self.assertFalse(self.store.preparation_retry_history(row['id']))

    def test_any_previous_review_blocks_recovery_even_after_failed_reset(self):
        row = self.failure()
        with self.store.activity.database() as conn:
            self.store.activity._event(conn, row['post_id'], 'review', 'Previously prepared')
        self.assertFalse(self.store.can_retry_preparation(row))
        with self.assertRaises(ValueError):
            self.controller.retry_preparation(self.account, self.page, row['id'], time.time()+3600)


class PreparationRetryUITests(unittest.TestCase):
    setUp = ui_fixtures.PageScheduleUITests.setUp
    start_schedule = ui_fixtures.PageScheduleUITests.start_schedule
    pause_schedule = ui_fixtures.PageScheduleUITests.pause_schedule
    cancel_schedule = ui_fixtures.PageScheduleUITests.cancel_schedule
    schedule = ui_fixtures.PageScheduleUITests.schedule
    panel = ui_fixtures.PageScheduleUITests.panel
    choose = ui_fixtures.PageScheduleUITests.choose

    def test_retry_button_only_for_verified_pre_input_failure_and_saves_without_arming(self):
        row = self.schedule()
        self.store.activity.transition(self.a, self.p, row['post_id'], 'failed', FAILURE)
        row = self.store.update(row['id'], state='needs_attention', phase='prepare',
            attempted_at='2026-10-10T11:45:01+07:00', message=FAILURE)
        self.controller.retry_preparation = Mock(side_effect=lambda _a,_p,ident,due:
            self.store.reschedule_failed_preparation(ident, due))
        panel = self.panel()
        self.choose(panel, row)
        self.assertEqual(panel.retry_schedule_button.winfo_manager(), 'pack')
        self.assertEqual(str(panel.start_button['state']), 'disabled')
        panel.retry_schedule_button.invoke()
        self.assertIsNotNone(panel.time_window)
        panel.new_time.set(display_time(time.time()+3600))
        panel.save_time(row['id'], retry=True)
        self.controller.retry_preparation.assert_called_once()
        self.assertEqual(self.store.get(row['id'])['state'], 'scheduled')
        self.controller.start.assert_not_called()
        self.owner.submit_page_browser.assert_not_called()
        panel.refresh()
        self.assertFalse(panel.retry_schedule_button.winfo_manager())
        self.assertIn('Lượt lỗi đã lưu', panel.detail.get())

    def test_unknown_result_never_shows_retry_control(self):
        row = self.schedule()
        row = self.store.update(row['id'], state='needs_attention', phase='publish',
            attempted_at='unknown attempt', message=FAILURE)
        panel = self.panel()
        self.choose(panel, row)
        self.assertFalse(panel.retry_schedule_button.winfo_manager())
        panel.change_time(retry=True)
        self.assertIsNone(panel.time_window)
        self.controller.start.assert_not_called()
