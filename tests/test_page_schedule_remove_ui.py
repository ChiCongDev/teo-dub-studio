"""Explicit removal in disposable hidden Tk; no live accounts or browsers."""
from copy import deepcopy
from pathlib import Path
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from tests import test_page_schedule_ui as fixtures
from teodub.page_schedule import PageScheduleController


class ScheduleRemoveUITests(unittest.TestCase):
    panel = fixtures.PageScheduleUITests.panel
    choose = fixtures.PageScheduleUITests.choose
    start_schedule = fixtures.PageScheduleUITests.start_schedule
    pause_schedule = fixtures.PageScheduleUITests.pause_schedule
    cancel_schedule = fixtures.PageScheduleUITests.cancel_schedule

    def setUp(self):
        fixtures.PageScheduleUITests.setUp(self)
        self.owner.sessions = {}
        self.owner.finish_browser = Mock()
        self.controller = PageScheduleController(self.owner)
        self.addCleanup(self.controller.close)
        self.owner.get_page_scheduler.return_value = self.controller
        self.controller.remove_many = Mock(wraps=self.controller.remove_many)
        self.controller.start = Mock(wraps=self.controller.start)
        self.controller.start_now = Mock(wraps=self.controller.start_now)
        self.controller.tick = Mock()
        self.controller.start_many = Mock(wraps=self.controller.start_many)
        self.store = self.controller.store

    def videos(self, count=1):
        rows = []
        for number in range(1, count+1):
            source = dict(self.source, number=number, key='c'*32+':'+str(number),
                          label='Synthetic campaign · Video '+str(number))
            self.sources[number] = source
            rows.append(self.store.create(self.a, self.p, source, 'Caption '+str(number),
                                           '#Việt', time.time()+7200+number*60))
        return rows

    def capture(self, rows):
        return dict(rows={row['id']: self.store.get(row['id']) for row in rows},
            posts=self.store.activity.posts(self.a, self.p, include_archived=True),
            events={row['post_id']: self.store.activity.events(self.a, self.p, row['post_id']) for row in rows},
            histories={row['id']: self.store.removal_history(row['id']) for row in rows},
            registry=deepcopy(self.registry.load()))

    def select(self, panel, rows):
        panel.tree.selection_set([row['id'] for row in rows])
        panel.select_row()

    def assert_no_publishing(self):
        self.controller.start.assert_not_called()
        self.controller.start_now.assert_not_called()
        self.controller.tick.assert_not_called()
        self.owner.submit_page_browser.assert_not_called()
        self.owner.finish_browser.assert_not_called()

    def test_button_visible_without_details_or_selection_and_does_not_mutate(self):
        rows = self.videos()
        before = self.capture(rows)
        panel = self.panel()
        self.assertEqual(panel.remove_button['text'], 'Xóa video')
        self.assertTrue(panel.remove_button.winfo_manager())
        self.assertFalse(panel.details_visible)
        self.assertEqual(str(panel.remove_button['state']), 'disabled')
        self.assertFalse(panel.show_removed.get())
        self.assertEqual(panel.show_removed_check['text'], 'Hiện video đã xóa')
        panel.remove_selected()
        self.confirm.assert_not_called()
        self.controller.remove_many.assert_not_called()
        self.assertEqual(self.capture(rows), before)
        self.assert_no_publishing()

    def test_unknown_publish_video_can_be_removed_when_recovery_is_disabled(self):
        rows = self.videos()
        row = rows[0]
        scope = (self.a, self.p, row['post_id'])
        self.store.activity.transition(*scope, 'preparing', 'Synthetic prepare', expected={'draft'})
        self.store.activity.transition(*scope, 'submitting', 'Clicked publish', expected={'preparing'})
        self.store.activity.transition(*scope, 'unknown', 'Receipt timeout', expected={'submitting'})
        rows[0] = self.store.update(row['id'], state='paused', phase='publish',
            attempted_at='2026-10-14T14:35:00+07:00', request_id='old-finished-request')
        before = self.capture(rows)
        original_bytes = Path(self.source['path']).read_bytes()
        panel = self.panel()
        self.select(panel, rows)
        self.assertEqual(str(panel.recovery_button['state']), 'disabled')
        self.assertEqual(str(panel.recreate_button['state']), 'disabled')
        self.assertEqual(str(panel.time_button['state']), 'disabled')
        self.assertEqual(str(panel.remove_button['state']), 'normal')
        self.confirm.return_value = True
        panel.remove_button.invoke()
        saved = self.store.get(row['id'])
        self.assertEqual(saved['state'], 'removed')
        self.assertEqual(saved['source_key'], before['rows'][row['id']]['source_key'])
        self.assertEqual(saved['phase'], 'publish')
        self.assertEqual(saved['attempted_at'], before['rows'][row['id']]['attempted_at'])
        self.assertEqual(self.capture(rows)['posts'], before['posts'])
        self.assertEqual(self.capture(rows)['events'], before['events'])
        self.assertEqual(Path(self.source['path']).read_bytes(), original_bytes)
        self.assertNotIn(row['id'], panel.rows)
        self.assertEqual(len(self.store.removal_history(row['id'])), 1)
        with self.assertRaisesRegex(ValueError, 'đã có lịch'):
            self.store.create(self.a, self.p, self.source, 'Attempted repeat', '', time.time()+7200)
        self.assert_no_publishing()

    def test_declining_confirmation_keeps_video_and_all_history(self):
        rows = self.videos(2)
        before = self.capture(rows)
        panel = self.panel()
        self.select(panel, rows)
        panel.remove_button.invoke()
        self.confirm.assert_called_once()
        self.controller.remove_many.assert_not_called()
        self.assertEqual(self.capture(rows), before)
        self.assertEqual(set(panel.rows), {row['id'] for row in rows})
        self.assert_no_publishing()

    def test_confirmation_names_exact_scope_count_labels_and_preserved_data(self):
        rows = self.videos(2)
        panel = self.panel()
        self.select(panel, rows)
        panel.remove_button.invoke()
        title, prompt = self.confirm.call_args.args
        self.assertEqual(title, 'Xóa video khỏi danh sách Tèo')
        for value in ('2 video', 'Synthetic Page', 'https://www.facebook.com/synthetic', 'Page ID: 100',
                      rows[0]['source']['label'], rows[1]['source']['label'], 'Tệp video trên máy',
                      'caption', 'lịch sử', 'bài đã đăng trên Facebook', 'Hiện video đã xóa',
                      'không tự đăng lại'):
            self.assertIn(value, prompt)
        self.controller.remove_many.assert_not_called()

    def test_confirmed_multi_removal_hides_rows_and_does_not_delete_files_or_posts(self):
        rows = self.videos(3)
        before = self.capture(rows)
        original_bytes = Path(self.source['path']).read_bytes()
        panel = self.panel()
        self.select(panel, rows)
        self.confirm.return_value = True
        panel.remove_button.invoke()
        self.assertFalse(panel.rows)
        self.assertFalse(panel.tree.get_children())
        self.assertEqual(len(self.store.list(self.a, self.p)), 3)
        self.assertTrue(all(self.store.get(row['id'])['state'] == 'removed' for row in rows))
        self.assertEqual(self.capture(rows)['posts'], before['posts'])
        self.assertEqual(self.capture(rows)['events'], before['events'])
        self.assertEqual(Path(self.source['path']).read_bytes(), original_bytes)
        self.assertIn('Đã xóa 3 video', panel.status.get())
        self.controller.remove_many.assert_called_once()
        self.assert_no_publishing()

    def test_fresh_source_can_be_added_again_after_explicit_removal(self):
        rows = self.videos()
        panel = self.panel()
        self.select(panel, rows)
        self.confirm.return_value = True
        panel.remove_button.invoke()
        replacement = self.store.create(self.a, self.p, self.source, 'New caption', '#New', time.time()+7200)
        panel.refresh()
        self.assertEqual(list(panel.rows), [replacement['id']])
        self.assertEqual(self.store.get(rows[0]['id'])['state'], 'removed')
        self.assertEqual(replacement['state'], 'scheduled')
        self.assert_no_publishing()

    def test_removed_history_is_optional_readonly_and_excluded_from_start_all(self):
        rows = self.videos(2)
        panel = self.panel()
        self.select(panel, rows[:1])
        self.confirm.return_value = True
        panel.remove_button.invoke()
        panel.show_removed_check.invoke()
        self.assertEqual(set(panel.rows), {row['id'] for row in rows})
        self.select(panel, rows[:1])
        for name in ('remove_button', 'detail_remove_button', 'now_button', 'start_button', 'pause_button',
                     'time_button', 'cancel_button', 'recovery_button', 'recreate_button'):
            self.assertEqual(str(getattr(panel, name)['state']), 'disabled', name)
        self.assertIn('lịch sử chỉ đọc', panel.detail.get())
        self.assertIn('Trạng thái lịch trước khi xóa', panel.detail.get())
        panel.start_all_button.invoke()
        self.controller.start_many.assert_called_once_with(self.a, self.p, [rows[1]['id']])
        self.assertNotIn(rows[0]['id'], self.controller.active)
        self.assertEqual(self.store.get(rows[0]['id'])['state'], 'removed')
        panel.show_removed_check.invoke()
        self.assertNotIn(rows[0]['id'], panel.rows)
        self.owner.submit_page_browser.assert_not_called()

    def test_all_removed_history_cannot_enable_start_all(self):
        rows = self.videos()
        panel = self.panel()
        self.select(panel, rows)
        self.confirm.return_value = True
        panel.remove_button.invoke()
        panel.show_removed_check.invoke()
        self.assertTrue(panel.rows)
        self.assertEqual(str(panel.start_all_button['state']), 'disabled')
        self.assert_no_publishing()

    def test_cancelled_video_has_visible_delete_and_disappears_after_confirmation(self):
        rows = self.videos()
        self.controller.cancel(rows[0]['id'])
        panel = self.panel()
        self.select(panel, rows)
        self.assertEqual(str(panel.remove_button['state']), 'normal')
        self.confirm.return_value = True
        panel.remove_button.invoke()
        self.assertEqual(self.store.get(rows[0]['id'])['state'], 'removed')
        self.assertFalse(panel.rows)
        self.assert_no_publishing()

    def test_published_video_removal_preserves_receipt_and_actual_post_link(self):
        rows = self.videos()
        row = rows[0]
        self.store.activity.transition(self.a, self.p, row['post_id'], 'published', 'Published receipt',
            external_url='https://www.facebook.com/synthetic/posts/987654321', external_id='100_987654321')
        self.store.update(row['id'], state='published', phase='publish', attempted_at='2026-10-14T10:00:00+07:00',
                          finished_at='2026-10-14T10:01:00+07:00')
        before = self.capture(rows)
        panel = self.panel()
        self.select(panel, rows)
        self.assertEqual(str(panel.remove_button['state']), 'normal')
        self.confirm.return_value = True
        panel.detail_remove_button.invoke()
        self.assertEqual(self.store.get(row['id'])['state'], 'removed')
        self.assertEqual(self.capture(rows)['posts'], before['posts'])
        self.assertEqual(self.capture(rows)['events'], before['events'])
        self.assert_no_publishing()

    def test_scope_change_during_confirmation_aborts_every_removal(self):
        rows = self.videos()
        before = self.capture(rows)
        panel = self.panel()
        self.select(panel, rows)
        def change_scope(*_args, **_kwargs):
            panel.manager.page_key = self.p2
            return True
        self.confirm.side_effect = change_scope
        panel.remove_button.invoke()
        self.controller.remove_many.assert_not_called()
        self.assertIn('đã thay đổi', panel.status.get())
        self.assertEqual(self.capture(rows), before)
        self.assert_no_publishing()

    def test_mixed_selection_deletes_only_video_and_keeps_text_appointment(self):
        rows = self.videos()
        post = self.store.activity.save_draft(self.a, self.p, 'Text appointment',
            delivery_mode='browser', browser_finish_mode='tool')
        text_row = self.store.create_post(self.a, self.p, post['id'], time.time()+8000)
        panel = self.panel()
        self.select(panel, [*rows, text_row])
        self.confirm.return_value = True
        panel.remove_button.invoke()
        self.assertEqual(self.store.get(rows[0]['id'])['state'], 'removed')
        self.assertEqual(self.store.get(text_row['id']), text_row)
        self.assertEqual([item['id'] for item in self.controller.remove_many.call_args.args[0]], [rows[0]['id']])
        self.assertIn('1 video', self.confirm.call_args.args[1])
        self.assertNotIn('Text appointment', self.confirm.call_args.args[1])
        self.assert_no_publishing()

    def test_post_event_change_during_confirmation_preserves_updated_post(self):
        rows = self.videos()
        panel = self.panel()
        self.select(panel, rows)
        def update_event(*_args, **_kwargs):
            self.store.activity.transition(self.a, self.p, rows[0]['post_id'], 'failed', 'New failure arrived')
            return True
        self.confirm.side_effect = update_event
        panel.remove_button.invoke()
        self.assertEqual(self.store.get(rows[0]['id'])['state'], 'scheduled')
        self.assertEqual(self.store.activity.post(self.a, self.p, rows[0]['post_id'])['message'], 'New failure arrived')
        self.assertFalse(self.store.removal_history(rows[0]['id']))
        self.assertIn('Không xóa 1', panel.status.get())
        self.assertIn('thay đổi', panel.status.get())
        self.assert_no_publishing()

    def test_selection_change_during_confirmation_does_not_redirect_deletion(self):
        rows = self.videos(2)
        panel = self.panel()
        self.select(panel, rows[:1])
        def change_selection(*_args, **_kwargs):
            self.select(panel, rows[1:])
            return True
        self.confirm.side_effect = change_selection
        panel.remove_button.invoke()
        previews = self.controller.remove_many.call_args.args[0]
        self.assertEqual([item['id'] for item in previews], [rows[0]['id']])
        self.assertEqual(self.store.get(rows[0]['id'])['state'], 'removed')
        self.assertEqual(self.store.get(rows[1]['id'])['state'], 'scheduled')
        self.assert_no_publishing()

    def test_row_change_during_confirmation_is_reported_without_deleting_new_state(self):
        rows = self.videos()
        panel = self.panel()
        self.select(panel, rows)
        def change_row(*_args, **_kwargs):
            self.store.update(rows[0]['id'], state='preparing', request_id='new-request')
            return True
        self.confirm.side_effect = change_row
        panel.remove_button.invoke()
        self.assertEqual(self.store.get(rows[0]['id'])['state'], 'preparing')
        self.assertFalse(self.store.removal_history(rows[0]['id']))
        self.assertIn('Không xóa 1', panel.status.get())
        self.assertIn('thay đổi', panel.status.get())
        self.assert_no_publishing()

    def test_partial_removal_reports_busy_video_and_keeps_its_live_request(self):
        rows = self.videos(2)
        self.store.update(rows[1]['id'], state='preparing', request_id='live-request')
        request = {'state': 'running', 'post_id': rows[1]['post_id']}
        self.owner.sessions[self.a] = SimpleNamespace(post_actions={'live-request': request})
        panel = self.panel()
        self.select(panel, rows)
        self.confirm.return_value = True
        panel.remove_button.invoke()
        self.assertEqual(self.store.get(rows[0]['id'])['state'], 'removed')
        self.assertEqual(self.store.get(rows[1]['id'])['state'], 'preparing')
        self.assertIn(rows[1]['id'], panel.rows)
        self.assertIn('Đã xóa 1', panel.status.get())
        self.assertIn('Không xóa 1', panel.status.get())
        self.assertEqual(request, {'state': 'running', 'post_id': rows[1]['post_id']})
        self.assert_no_publishing()

    def test_busy_only_selection_disables_delete_button(self):
        rows = self.videos()
        self.store.update(rows[0]['id'], state='submitting', request_id='active')
        panel = self.panel()
        self.select(panel, rows)
        self.assertEqual(str(panel.remove_button['state']), 'disabled')
        panel.remove_button.invoke()
        self.confirm.assert_not_called()
        self.controller.remove_many.assert_not_called()
        self.assert_no_publishing()

    def test_reentrant_delete_while_confirmation_open_cannot_submit_twice(self):
        rows = self.videos()
        panel = self.panel()
        self.select(panel, rows)
        def reenter(*_args, **_kwargs):
            panel.remove_selected()
            return True
        self.confirm.side_effect = reenter
        panel.remove_button.invoke()
        self.confirm.assert_called_once()
        self.controller.remove_many.assert_called_once()
        self.assertEqual(len(self.store.removal_history(rows[0]['id'])), 1)
        self.assert_no_publishing()

    def test_no_file_required_to_remove_record_with_missing_local_video(self):
        rows = self.videos()
        Path(self.source['path']).unlink()
        panel = self.panel()
        self.select(panel, rows)
        self.confirm.return_value = True
        panel.remove_button.invoke()
        self.assertEqual(self.store.get(rows[0]['id'])['state'], 'removed')
        self.assert_no_publishing()

    def test_mount_and_history_checkbox_do_not_remove_recover_or_post(self):
        rows = self.videos()
        before = self.capture(rows)
        panel = self.panel()
        panel.show_removed_check.invoke()
        panel.show_removed_check.invoke()
        panel.refresh()
        self.controller.remove_many.assert_not_called()
        self.confirm.assert_not_called()
        self.assertEqual(self.capture(rows), before)
        self.assert_no_publishing()


if __name__ == '__main__':
    unittest.main()
