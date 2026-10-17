"""Immediate posting uses disposable records and the real scheduler, never Facebook."""
from copy import deepcopy
import time
import unittest
from unittest.mock import Mock, patch

from tests import test_page_schedule as core_fixture
from tests import test_page_schedule_ui as ui_fixture
from teodub.page_schedule import PageScheduleController


class PostNowTests(unittest.TestCase):
    setUp = core_fixture.ScheduleTests.setUp
    video = core_fixture.ScheduleTests.video
    schedule = core_fixture.ScheduleTests.schedule
    submit = core_fixture.ScheduleTests.submit
    tick = core_fixture.ScheduleTests.tick
    reply = core_fixture.ScheduleTests.reply

    def launch(self, row):
        with patch('teodub.page_schedule.threading.Thread') as thread:
            thread.return_value.start.side_effect = lambda: self.controller.validate_video(self.store.get(row['id']))
            self.controller.start_now(self.account, self.page, row['id'], expected_schedule=row)
        self.tick()
        return self.store.get(row['id'])

    def test_existing_armed_video_runs_once_with_same_post_and_no_old_time_replay(self):
        row = self.schedule()
        other = self.schedule('b'*32)
        self.controller.start(row['id'])
        self.controller.start(other['id'])
        expected = self.store.get(row['id'])
        old_other = self.store.get(other['id'])
        running = self.launch(expected)
        self.assertEqual(running['id'], row['id'])
        self.assertEqual(running['post_id'], row['post_id'])
        self.assertEqual(running['source'], row['source'])
        self.assertEqual(running['body'], row['body'])
        self.assertEqual(running['snapshot'], row['snapshot'])
        self.assertLess(running['due_at'], row['due_at'])
        self.assertEqual(len(self.store.list()), 2)
        history = self.store.immediate_history(row['id'])
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]['previous'], expected)
        self.assertEqual([call[-1] for call in self.calls], ['prepare'])
        self.reply(running, 'done', 'review')
        self.tick()
        self.assertEqual([call[-1] for call in self.calls], ['prepare', 'publish'])
        self.reply(self.store.get(row['id']), 'done', 'published')
        self.tick()
        saved = self.store.get(row['id'])
        self.assertEqual(saved['state'], 'published')
        with self.assertRaises(ValueError):
            self.controller.start_now(self.account, self.page, row['id'], expected_schedule=saved)
        with patch('teodub.page_schedule.time.time', return_value=row['due_at']+1):
            self.tick()
        self.assertEqual(sum(call[2] == row['post_id'] for call in self.calls), 2)
        self.assertEqual(self.store.get(other['id'])['due_at'], old_other['due_at'])
        self.assertEqual(len(self.store.immediate_history(row['id'])), 1)

    def test_past_paused_unattempted_post_can_be_explicitly_sent_now(self):
        row = self.schedule()
        row = self.store.update(row['id'], state='paused', due_at=time.time()-7200)
        running = self.launch(row)
        self.assertEqual(running['phase'], 'prepare')
        self.assertEqual(len(self.calls), 1)

    def test_foreign_page_stale_preview_and_changed_target_leave_time_and_state_untouched(self):
        row = self.schedule()
        with self.assertRaises(ValueError):
            self.controller.start_now(self.other, self.other_page, row['id'], expected_schedule=row)
        self.assertEqual(self.store.get(row['id']), row)
        stale = deepcopy(row)
        stale['body'] = 'Another caption'
        with self.assertRaisesRegex(ValueError, 'thay đổi'):
            self.controller.start_now(self.account, self.page, row['id'], expected_schedule=stale)
        self.accounts.save_page(self.account, 'Changed', 'facebook.com/profile.php?id=100', '100', record_id=self.page)
        with self.assertRaisesRegex(ValueError, 'Page đã đổi'):
            self.controller.start_now(self.account, self.page, row['id'], expected_schedule=row)
        self.assertEqual(self.store.get(row['id']), row)
        self.assertEqual(self.controller.guards, {})
        self.assertEqual(self.store.immediate_history(row['id']), [])
        self.assertEqual(self.calls, [])

    def test_attempted_unknown_finished_and_independently_prepared_posts_never_replay(self):
        for index, state in enumerate(('needs_attention', 'published', 'cancelled', 'submitting', 'preparing')):
            with self.subTest(state=state):
                row = self.schedule(str(index+1)*32)
                row = self.store.update(row['id'], state=state, attempted_at='Prior attempt')
                with self.assertRaises(ValueError):
                    self.controller.start_now(self.account, self.page, row['id'], expected_schedule=row)
                self.assertEqual(self.store.get(row['id']), row)
        row = self.schedule('f'*32)
        self.store.activity.transition(self.account, self.page, row['post_id'], 'unknown', 'Uncertain upload')
        # Even an incorrectly reset draft cannot conceal its prior uncertain event.
        self.store.activity.transition(self.account, self.page, row['post_id'], 'failed', 'Changed externally')
        with self.assertRaises(ValueError):
            self.controller.start_now(self.account, self.page, row['id'], expected_schedule=row)
        self.assertEqual(self.store.get(row['id']), row)
        self.assertEqual(self.calls, [])

    def test_other_controller_cannot_move_owned_appointment_or_queue_another_post(self):
        row = self.schedule()
        self.controller.start(row['id'])
        expected = self.store.get(row['id'])
        second = PageScheduleController(self.owner)
        self.addCleanup(second.close)
        with self.assertRaises((ValueError, OSError)):
            second.start_now(self.account, self.page, row['id'], expected_schedule=expected)
        self.assertEqual(self.store.get(row['id']), expected)
        self.assertEqual(second.guards, {})
        self.assertEqual(self.calls, [])

    def test_tampered_video_never_uploads_even_when_metadata_is_unchanged(self):
        import os
        from pathlib import Path
        row = self.schedule()
        video = Path(row['source']['path'])
        data = video.read_bytes()
        video.write_bytes(b'X'+data[1:])
        os.utime(video, ns=(row['source']['mtime_ns'], row['source']['mtime_ns']))
        result = self.launch(row)
        self.assertEqual(result['state'], 'needs_attention')
        self.assertEqual(result['attempted_at'], '')
        self.assertEqual(self.calls, [])

    def test_progress_is_visible_and_error_never_becomes_success_or_auto_retry(self):
        row = self.launch(self.schedule())
        request = self.owner.sessions[self.account].post_actions[row['request_id']]
        request.update(state='running', message='Đã chọn tệp; đang chờ Facebook tải video.')
        self.tick()
        self.assertIn('đang chờ Facebook', self.store.get(row['id'])['message'])
        request.update(state='error', message='Chưa xác nhận kết quả tải; kiểm tra Page.')
        self.tick(); self.tick()
        saved = self.store.get(row['id'])
        self.assertEqual(saved['state'], 'needs_attention')
        self.assertEqual(saved['finished_at'], '')
        self.assertEqual(len(self.calls), 1)
        with self.assertRaises(ValueError):
            self.controller.start_now(self.account, self.page, row['id'], expected_schedule=saved)

    def test_text_post_uses_same_validation_and_no_extra_draft(self):
        post = self.store.activity.save_draft(self.account, self.page, 'Exact text', delivery_mode='browser', browser_finish_mode='tool')
        row = self.store.create_post(self.account, self.page, post['id'], time.time()+3600)
        result = self.launch(row)
        self.assertEqual(result['post_id'], post['id'])
        self.assertEqual(result['phase'], 'prepare')
        self.assertEqual(self.calls, [(self.account, self.page, post['id'], 'prepare')])

    def test_late_progress_does_not_replace_pause_notice_or_resume_post(self):
        row = self.launch(self.schedule())
        self.controller.pause(row['id'])
        paused = self.store.get(row['id'])
        request = self.owner.sessions[self.account].post_actions[row['request_id']]
        request.update(state='running', message='Late upload progress')
        self.tick()
        self.assertEqual(self.store.get(row['id'])['message'], paused['message'])
        self.assertEqual(self.store.get(row['id'])['state'], 'paused')
        self.assertEqual(len(self.calls), 1)

    def test_restart_blocks_immediate_before_any_schedule_change(self):
        row = self.schedule()
        self.owner.studio.restart_draining = True
        with self.assertRaises(ValueError):
            self.controller.start_now(self.account, self.page, row['id'], expected_schedule=row)
        self.assertEqual(self.store.get(row['id']), row)
        self.assertEqual(self.controller.guards, {})


class PostNowUITests(unittest.TestCase):
    setUp = ui_fixture.PageScheduleUITests.setUp
    schedule = ui_fixture.PageScheduleUITests.schedule
    panel = ui_fixture.PageScheduleUITests.panel
    choose = ui_fixture.PageScheduleUITests.choose
    start_schedule = ui_fixture.PageScheduleUITests.start_schedule
    pause_schedule = ui_fixture.PageScheduleUITests.pause_schedule
    cancel_schedule = ui_fixture.PageScheduleUITests.cancel_schedule
    extra_schedule = ui_fixture.PageScheduleUITests.extra_schedule

    def test_preview_captures_exact_target_caption_and_media_without_posting_then_sends_once(self):
        row = self.schedule()
        other = self.extra_schedule(2)
        panel = self.panel()
        self.controller.start_now = Mock(return_value=row)
        self.choose(panel, row)
        panel.preview_now()
        panel.now_window.withdraw()
        contents = panel.now_content.get('1.0', 'end-1c')
        for value in ('Synthetic A', 'Synthetic Page', 'Page ID: 100', self.source['path'], 'Saved caption', '#Saved'):
            self.assertIn(value, contents)
        self.controller.start_now.assert_not_called()
        self.choose(panel, other)
        panel.submit_now(); panel.submit_now()
        self.controller.start_now.assert_called_once_with(self.a, self.p, row['id'], expected_schedule=row)
        self.assertIsNone(panel.now_window)
        self.assertEqual(panel.tree.selection(), (row['id'],))
        self.owner.submit_page_browser.assert_not_called()

    def test_cancel_preview_or_multiselect_never_changes_appointments(self):
        row = self.schedule()
        other = self.extra_schedule(2)
        panel = self.panel()
        self.choose(panel, row)
        panel.preview_now(); panel.close_now(); panel.submit_now()
        self.assertEqual(self.store.get(row['id']), row)
        panel.tree.selection_set([row['id'], other['id']]); panel.select_row()
        self.assertEqual(str(panel.now_button['state']), 'disabled')
        panel.preview_now()
        self.assertIsNone(panel.now_window)
        self.assertIn('đúng một lịch', panel.status.get())

    def test_attempted_button_disabled_and_controller_error_visible_without_success(self):
        row = self.schedule()
        panel = self.panel()
        self.choose(panel, row)
        self.controller.start_now = Mock(side_effect=ValueError('Chrome còn đang tải bài khác'))
        panel.preview_now(); panel.now_window.withdraw(); panel.submit_now()
        self.assertIn('Chrome còn đang tải', panel.now_status.get())
        self.assertEqual(self.store.get(row['id']), row)
        self.assertEqual(str(panel.now_approve_button['state']), 'normal')
        panel.close_now()
        self.store.update(row['id'], attempted_at='Uncertain previous attempt', state='needs_attention')
        panel.refresh()
        self.assertEqual(str(panel.now_button['state']), 'disabled')
        panel.preview_now()
        self.assertIsNone(panel.now_window)


if __name__ == '__main__':
    unittest.main()
