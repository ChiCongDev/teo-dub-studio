"""Disposable Tk/store tests; no production data or Facebook is touched."""
import json
from pathlib import Path
import time
import unittest
from unittest.mock import patch

from tests import test_page_schedule_ui as existing_ui
from teodub.page_schedule_add_ui import AddScheduledPostDialog, campaign_choices
from teodub.page_schedule import display_time


class AddScheduleTests(unittest.TestCase):
    setUp = existing_ui.PageScheduleUITests.setUp
    start_schedule = existing_ui.PageScheduleUITests.start_schedule
    pause_schedule = existing_ui.PageScheduleUITests.pause_schedule
    cancel_schedule = existing_ui.PageScheduleUITests.cancel_schedule
    panel = existing_ui.PageScheduleUITests.panel

    def form(self):
        panel = self.panel()
        panel.add_button.invoke()
        form = panel.add_window
        form.withdraw()
        return panel, form

    def body(self, form, value='Bài mới\n\nNội dung nguyên văn.\n#CauCa'):
        form.body.delete('1.0', 'end')
        form.body.insert('1.0', value)

    def test_open_single_dialog_and_close_are_passive(self):
        panel, form = self.form()
        panel.add_post()
        self.assertIs(panel.add_window, form)
        self.assertEqual(form.target['page_id'], '100')
        self.assertEqual(self.store.list(), [])
        form.close()
        self.assertEqual(self.store.activity.posts(self.a, self.p), [])
        self.controller.start.assert_not_called()
        self.owner.submit_page_browser.assert_not_called()

    def test_save_text_selects_new_row_without_arming_or_duplicate_click(self):
        panel, form = self.form()
        self.body(form)
        form.save(False)
        form.save(False)
        rows = self.store.list()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual((row['account_id'], row['page_key']), (self.a, self.p))
        self.assertEqual(row['state'], 'scheduled')
        self.assertEqual(row['body'], 'Bài mới\n\nNội dung nguyên văn.\n#CauCa')
        self.assertEqual(panel.tree.selection(), (row['id'],))
        self.assertIn('chưa bật', panel.status.get())
        self.controller.start.assert_not_called()

    def test_save_and_enable_uses_existing_controller_once(self):
        panel, form = self.form()
        self.body(form)
        form.save(True)
        form.save(True)
        self.assertEqual(self.store.get(form.saved_id)['state'], 'armed')
        self.controller.start.assert_called_once_with(form.saved_id)
        self.owner.submit_page_browser.assert_not_called()

    def test_start_failure_retains_one_saved_row_and_retries_only_start(self):
        panel, form = self.form()
        self.body(form)
        self.controller.start.side_effect = ValueError('Synthetic temporary failure')
        form.save(True)
        ident = form.saved_id
        self.assertEqual(len(self.store.list()), 1)
        self.assertIn('Lịch đã lưu nhưng chưa bật', form.status.get())
        self.assertEqual(str(form.body['state']), 'disabled')
        self.controller.start.side_effect = self.start_schedule
        form.save(True)
        self.assertEqual(form.saved_id, ident)
        self.assertEqual(len(self.store.list()), 1)
        self.assertEqual(self.store.get(ident)['state'], 'armed')

    def test_failed_schedule_save_reuses_same_draft(self):
        panel, form = self.form()
        self.body(form)
        with patch.object(self.store, 'create_post', side_effect=ValueError('Synthetic storage error')):
            form.save(False)
        original = form.draft_id
        self.assertTrue(original)
        self.body(form, 'Đã chỉnh lại')
        form.save(False)
        self.assertEqual(self.store.get(form.saved_id)['post_id'], original)
        self.assertEqual(len(self.store.activity.posts(self.a, self.p)), 1)
        self.assertEqual(self.store.get(form.saved_id)['body'], 'Đã chỉnh lại')

    def test_empty_or_past_input_creates_nothing(self):
        panel, form = self.form()
        form.save(False)
        self.assertIn('Nhập nội dung', form.status.get())
        self.body(form)
        form.due_time.set('2020-01-01 12:00')
        form.save(True)
        self.assertIn('tương lai', form.status.get())
        self.assertEqual(self.store.list(), [])
        self.assertEqual(self.store.activity.posts(self.a, self.p), [])
        self.controller.start.assert_not_called()

    def test_changed_page_blocks_save_before_creating_draft(self):
        panel, form = self.form()
        self.body(form)
        with patch.object(self.store.activity, 'page', return_value=dict(form.target, name='Changed Page')):
            form.save(True)
        self.assertIn('Page đã thay đổi', form.status.get())
        self.assertEqual(self.store.list(), [])
        self.assertEqual(self.store.activity.posts(self.a, self.p), [])

    def video_form(self):
        panel, form = self.form()
        choices = [dict(name='Campaign', directory=self.source['campaign'], items=[dict(number=1,title='Video caption')])]
        form.kind.set('Video từ chiến dịch')
        with patch('teodub.page_schedule_add_ui.campaign_choices', return_value=choices):
            form.change_kind()
        form.video_picker.current(0)
        with patch('teodub.page_schedule_add_ui.campaign_video_source', return_value=self.source):
            form.change_video()
        return panel, form

    def test_video_uses_completed_source_without_changing_page_template(self):
        original = self.store.template(self.a, self.p)
        panel, form = self.video_form()
        self.body(form, 'Caption video riêng\n#Video')
        form.save(False)
        row = self.store.get(form.saved_id)
        self.assertEqual(row['source'], self.source)
        self.assertEqual(row['body'], 'Caption video riêng\n#Video')
        self.assertEqual(self.store.template(self.a, self.p), original)
        self.controller.start.assert_not_called()

    def test_already_scheduled_video_rejected_on_selection(self):
        self.store.create(self.a, self.p, self.source, 'old', '', time.time()+7200)
        panel, form = self.video_form()
        self.assertIsNone(form.source)
        self.assertIn('đã có lịch', form.video_status.get())
        form.save(True)
        self.assertEqual(len(self.store.list()), 1)
        self.controller.start.assert_not_called()

    def test_removed_untouched_video_with_released_source_is_selectable_again(self):
        saved = self.store.create(self.a, self.p, self.source, 'old caption', '', time.time()+7200)
        preview = self.store.removal_preview([saved['id']], self.a, self.p)
        self.store.remove_many(preview, self.a, self.p)
        panel, form = self.video_form()
        self.assertEqual(form.source, self.source)
        self.assertNotIn('Đã có lịch', form.video_picker['values'][0])
        self.assertNotIn('Lượt cũ đã xóa', form.video_picker['values'][0])
        self.assertEqual(str(form.new_attempt_button['state']), 'disabled')
        self.body(form, 'Replacement caption')
        form.save(False)
        self.assertNotEqual(form.saved_id, saved['id'])
        self.assertEqual(self.store.get(saved['id'])['state'], 'removed')
        self.assertEqual(self.store.get(form.saved_id)['state'], 'scheduled')
        self.controller.start.assert_not_called()

    def test_cancelled_with_released_source_is_selectable_without_duplicate_warning(self):
        saved = self.store.create(self.a, self.p, self.source, 'old caption', '', time.time()+7200)
        self.store.cancel_unattempted(saved['id'])
        panel, form = self.video_form()
        self.assertEqual(form.source, self.source)
        self.assertNotIn('Đã có lịch', form.video_picker['values'][0])
        self.controller.start.assert_not_called()

    def test_inventory_only_completed_nonarchived_campaigns(self):
        root = self.directory / 'campaigns'
        for index, archived in enumerate((False, True)):
            directory = root / (str(index)*32)
            directory.mkdir(parents=True)
            manifest = dict(id=directory.name, name='Same name', items=[
                dict(number=1, status='pending'), dict(number=2, status='completed', title='Finished')])
            if archived:
                manifest['archived_at'] = '2026-10-12'
            (directory/'campaign.json').write_text(json.dumps(manifest), encoding='utf-8')
        found = campaign_choices(self.directory)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]['items'], [dict(number=2, title='Finished')])


if __name__ == '__main__':
    unittest.main()
