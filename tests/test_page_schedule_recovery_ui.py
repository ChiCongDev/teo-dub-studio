"""Hidden Tk recovery approval tests: temporary stores, never a browser/upload."""
from collections import Counter
from copy import deepcopy
from pathlib import Path
import time
import tkinter as tk
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from tests import test_page_schedule_ui as fixtures
from teodub.page_schedule import PageScheduleController, display_time, parse_time


FAILURE = ('Không xác định duy nhất tên Page trong nội dung chính (0 mục). '
           'Chrome giữ nguyên để kiểm tra; chưa bấm Đăng.')


class ScheduleRecoveryUITests(unittest.TestCase):
    video = fixtures.PageScheduleUITests.schedule
    panel = fixtures.PageScheduleUITests.panel
    choose = fixtures.PageScheduleUITests.choose
    start_schedule = fixtures.PageScheduleUITests.start_schedule
    pause_schedule = fixtures.PageScheduleUITests.pause_schedule
    cancel_schedule = fixtures.PageScheduleUITests.cancel_schedule

    def setUp(self):
        fixtures.PageScheduleUITests.setUp(self)
        self.owner.sessions = {}
        self.controller = PageScheduleController(self.owner)
        self.addCleanup(self.controller.close)
        self.owner.get_page_scheduler.return_value = self.controller
        self.controller.recovery_preview = Mock(wraps=self.controller.recovery_preview)
        self.real_recover = self.controller.recover_schedule
        self.controller.recover_schedule = Mock(wraps=self.real_recover)
        self.controller.start = Mock(wraps=self.controller.start)
        self.source['path'] = str(Path(self.source['path']).resolve())
        self.source['campaign'] = str(Path(self.source['campaign']).resolve())
        self.sources[1] = deepcopy(self.source)

    def failed_rows(self, count=3, *, unknown_last=False):
        rows = []
        for number in range(1, count + 1):
            source = dict(self.source, number=number,
                key='c' * 32 + ':' + str(number),
                label='Synthetic campaign · Video ' + str(number))
            self.sources[number] = source
            row = self.store.create(self.a, self.p, source,
                'Caption nguyên văn ' + str(number), '#Saved #Việt', time.time() + 7200)
            scope = (self.a, self.p, row['post_id'])
            self.store.activity.transition(*scope, 'preparing', 'Synthetic preparation', expected={'draft'})
            is_unknown = unknown_last and number == count
            self.store.activity.transition(*scope, 'unknown' if is_unknown else 'failed',
                'Upload result not yet verified' if is_unknown else FAILURE, expected={'preparing'})
            row = self.store.update(row['id'], state='paused', phase='prepare',
                attempted_at='2026-10-14T09:41:00+07:00', request_id='synthetic-' + str(number),
                message='Đã tạm dừng; không tự chạy khi mở lại ứng dụng.')
            rows.append(row)
        self.registry.save_page(self.a, 'Minh châu sneaker', 'facebook.com/synthetic',
                                '100', record_id=self.p)
        return rows

    def open_dialog(self, rows):
        panel = self.panel()
        panel.tree.selection_set([row['id'] for row in rows])
        panel.select_row()
        panel.open_recovery()
        dialog = panel.recovery_window
        self.assertIsNotNone(dialog)
        dialog.withdraw()
        return panel, dialog

    @staticmethod
    def rendered_text(widget):
        values = []
        if isinstance(widget, tk.Text):
            values.append(widget.get('1.0', 'end-1c'))
        elif 'text' in widget.keys():
            values.append(str(widget.cget('text')))
        for child in widget.winfo_children():
            values.append(ScheduleRecoveryUITests.rendered_text(child))
        return '\n'.join(values)

    def approve(self, dialog):
        dialog.checked_unpublished.set(True)
        dialog.closed_composer.set(True)
        dialog.start_time.set(display_time(time.time() + 3600))
        dialog.interval_minutes.set(30)

    def assert_no_start(self):
        self.controller.start.assert_not_called()
        self.owner.submit_page_browser.assert_not_called()
        self.assertFalse(self.controller.active)

    def test_open_and_cancel_preview_are_read_only_and_expose_full_approval(self):
        rows = self.failed_rows(3, unknown_last=True)
        before_posts = {row['id']: self.store.activity.post(self.a, self.p, row['post_id']) for row in rows}
        panel, dialog = self.open_dialog(rows)
        content = self.rendered_text(dialog)
        for expected in ('Synthetic Page', 'Minh châu sneaker', '100', self.source['path'],
                         self.source['sha256'], 'Caption nguyên văn 1', '#Saved #Việt',
                         'Synthetic campaign · Video 3'):
            self.assertIn(expected, content)
        self.assertFalse(dialog.checked_unpublished.get())
        self.assertFalse(dialog.closed_composer.get())
        self.assertEqual(str(dialog.save_button['state']), 'disabled')
        self.assertEqual(dialog.interval_minutes.get(), 30)
        self.assertEqual(str(dialog.save_button['text']), 'Khôi phục lịch (chưa bật)')
        self.assertEqual(set(dialog.previews), {row['id'] for row in rows})
        dialog.destroy()
        for row in rows:
            self.assertEqual(self.store.get(row['id']), row)
            self.assertEqual(self.store.activity.post(self.a, self.p, row['post_id']), before_posts[row['id']])
        self.controller.recover_schedule.assert_not_called()
        self.assert_no_start()

    def test_both_manual_checks_are_required_even_for_known_failed_preparation(self):
        rows = self.failed_rows(1)
        _, dialog = self.open_dialog(rows)
        for checked, closed in ((False, False), (True, False), (False, True)):
            with self.subTest(checked_unpublished=checked, closed_composer=closed):
                dialog.checked_unpublished.set(checked)
                dialog.closed_composer.set(closed)
                self.assertEqual(str(dialog.save_button['state']), 'disabled')
                dialog.save()
                self.controller.recover_schedule.assert_not_called()
                self.assertEqual(self.store.get(rows[0]['id']), rows[0])
                self.assertTrue(dialog.status.get())
        self.assert_no_start()

    def test_open_chrome_save_error_stays_visible_without_changing_schedules(self):
        rows = self.failed_rows(3, unknown_last=True)
        self.owner.sessions[self.a] = SimpleNamespace(pending=True, opened=True,
            loading_pages=False, post_actions={}, navigation={}, page_load_requests={})
        _, dialog = self.open_dialog(rows)
        self.approve(dialog)
        dialog.save()
        self.assertFalse(dialog.restored)
        self.assertIn('Đóng / Lưu phiên', dialog.status.get())
        for row in rows:
            self.assertEqual(self.store.get(row['id']), row)
        self.assert_no_start()

    def test_mixed_attempted_and_fresh_selection_does_not_open_recovery_or_mutate(self):
        rows = self.failed_rows(2)
        source = dict(self.source, number=3, key='c' * 32 + ':3',
                      label='Synthetic campaign · Video 3')
        self.sources[3] = source
        fresh = self.store.create(self.a, self.p, source, 'Fresh caption', '#Fresh', time.time() + 7200)
        panel = self.panel()
        panel.tree.selection_set([row['id'] for row in rows] + [fresh['id']])
        panel.select_row()
        self.assertEqual(str(panel.recovery_button['state']), 'disabled')
        panel.open_recovery()
        self.assertIsNone(panel.recovery_window)
        self.assertTrue(panel.status.get())
        for row in rows + [fresh]:
            self.assertEqual(self.store.get(row['id']), row)
        self.controller.recover_schedule.assert_not_called()
        self.assert_no_start()

    def test_three_recovered_with_30_minute_spacing_and_separate_start(self):
        rows = self.failed_rows(3, unknown_last=True)
        panel, dialog = self.open_dialog(rows)
        captured = deepcopy(dialog.previews)
        self.approve(dialog)
        expected_first = parse_time(dialog.start_time.get())
        dialog.save()
        self.assertEqual(self.controller.recover_schedule.call_count, 3)
        due_values = []
        for call in self.controller.recover_schedule.call_args_list:
            account, page, ident, due_at = call.args
            self.assertEqual((account, page), (self.a, self.p))
            self.assertEqual(call.kwargs['expected_preview'], captured[ident])
            self.assertIs(call.kwargs['checked_unpublished'], True)
            self.assertIs(call.kwargs['closed_composer'], True)
            due_values.append(due_at)
        self.assertEqual(due_values, [expected_first + 1800 * index for index in range(3)])
        self.assertEqual(dialog.restored, {row['id'] for row in rows})
        for original in rows:
            saved = self.store.get(original['id'])
            self.assertEqual(saved['state'], 'scheduled')
            self.assertEqual(saved['body'], original['body'])
            self.assertEqual(saved['source'], original['source'])
            self.assertEqual(saved['source_key'], original['source_key'])
            self.assertEqual(saved['snapshot']['target_name'], 'Minh châu sneaker')
            self.assertFalse(saved['attempted_at'])
        self.assert_no_start()
        # A separate explicit action is still required after recovery approval.
        panel.tree.selection_set([row['id'] for row in rows])
        panel.start()
        self.assertEqual(self.controller.start.call_count, 3)
        self.assertEqual(self.controller.active, {row['id'] for row in rows})
        self.owner.submit_page_browser.assert_not_called()

    def test_partial_failure_reentry_never_replays_successful_recoveries(self):
        rows = self.failed_rows(3)
        _, dialog = self.open_dialog(rows)
        self.approve(dialog)
        counts = Counter()
        failed = rows[1]['id']

        def recover(account, page, ident, due_at, **kwargs):
            counts[ident] += 1
            if ident == failed and counts[ident] == 1:
                raise ValueError('Synthetic Chrome still open: close and retry only this row')
            return self.real_recover(account, page, ident, due_at, **kwargs)

        self.controller.recover_schedule.side_effect = recover
        dialog.save()
        self.assertEqual(dialog.restored, {rows[0]['id'], rows[2]['id']})
        self.assertEqual(self.store.get(failed), rows[1])
        self.assertIn('Synthetic Chrome still open', dialog.status.get())
        self.assert_no_start()
        dialog.save()
        self.assertEqual(dialog.restored, {row['id'] for row in rows})
        self.assertEqual(counts, Counter({rows[0]['id']: 1, failed: 2, rows[2]['id']: 1}))
        self.assert_no_start()

    def test_double_save_inside_running_recovery_cannot_reenter(self):
        rows = self.failed_rows(3)
        _, dialog = self.open_dialog(rows)
        self.approve(dialog)

        def recover(account, page, ident, due_at, **kwargs):
            dialog.save()
            return self.real_recover(account, page, ident, due_at, **kwargs)

        self.controller.recover_schedule.side_effect = recover
        dialog.save()
        self.assertEqual(self.controller.recover_schedule.call_count, 3)
        self.assertEqual(dialog.restored, {row['id'] for row in rows})
        self.assert_no_start()

    def test_invalid_time_or_interval_cannot_call_recovery(self):
        rows = self.failed_rows(1)
        _, dialog = self.open_dialog(rows)
        self.approve(dialog)
        future = dialog.start_time.get()
        for start, interval in (('not a date', 30), (display_time(time.time() - 3600), 30),
                                (future, 0), (future, -2)):
            with self.subTest(start=start, interval=interval):
                dialog.start_time.set(start)
                dialog.interval_minutes.set(interval)
                dialog.save()
                self.controller.recover_schedule.assert_not_called()
                self.assertEqual(self.store.get(rows[0]['id']), rows[0])
        self.assert_no_start()

    def test_preview_failure_is_visible_without_accepting_or_creating_a_draft(self):
        rows = self.failed_rows(1)
        self.controller.recovery_preview.side_effect = ValueError('Synthetic target identity changed')
        panel = self.panel()
        panel.tree.selection_set(rows[0]['id'])
        panel.select_row()
        panel.open_recovery()
        window = panel.recovery_window
        message = panel.status.get() + (window.status.get() if window is not None else '')
        self.assertIn('Synthetic target identity changed', message)
        self.assertEqual(self.store.get(rows[0]['id']), rows[0])
        self.controller.recover_schedule.assert_not_called()
        self.assert_no_start()

    def test_single_preview_window_and_panel_cleanup_close_recovery_without_mutation(self):
        rows = self.failed_rows(1)
        panel, dialog = self.open_dialog(rows)
        self.controller.recovery_preview.reset_mock()
        panel.open_recovery()
        self.assertIs(panel.recovery_window, dialog)
        self.controller.recovery_preview.assert_not_called()
        panel.destroy()
        self.assertFalse(dialog.winfo_exists())
        self.assertEqual(self.store.get(rows[0]['id']), rows[0])
        self.controller.recover_schedule.assert_not_called()
        self.assert_no_start()


if __name__ == '__main__':
    unittest.main()
