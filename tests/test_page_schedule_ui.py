"""Hidden Tk schedule UI tests with synthetic records and no publishing workers."""
import hashlib
from pathlib import Path
import tempfile
import time
import tkinter as tk
from tkinter import ttk
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from teodub.facebook_accounts import FacebookAccounts
from teodub.page_activity import PageActivity
from teodub.page_schedule import PageScheduleStore, PageScheduleController, display_time
from teodub.page_schedule_ui import PageSchedulePanel, ScheduleVideoDialog


class PageScheduleUITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.enterContext(patch.object(tk.Toplevel, 'grab_set', return_value=None))
        self.confirm = self.enterContext(patch('teodub.page_schedule_ui.messagebox.askyesno', return_value=False))
        self.registry = FacebookAccounts(self.directory)
        self.a = self.registry.add_account('Synthetic A')['id']
        self.p = self.registry.save_page(self.a, 'Synthetic Page', 'facebook.com/synthetic', '100')['id']
        self.p2 = self.registry.save_page(self.a, 'Synthetic Page', 'facebook.com/synthetic-two', '101')['id']
        self.b = self.registry.add_account('Synthetic B')['id']
        self.pb = self.registry.save_page(self.b, 'Other Page', 'facebook.com/other', '200')['id']
        video = self.directory / 'rendered.mp4'
        video.write_bytes(b'synthetic rendered video, not a real media upload')
        stat = video.stat()
        self.source = dict(key='c'*32 + ':1', campaign=str(self.directory / 'campaigns' / ('c'*32)), number=1,
            label='Synthetic campaign · Video 1', path=str(video), sha256=hashlib.sha256(video.read_bytes()).hexdigest(),
            size=stat.st_size, mtime_ns=stat.st_mtime_ns)
        self.sources = {1: dict(self.source)}
        self.enterContext(patch('teodub.page_schedule.campaign_video_source', side_effect=lambda _data, _directory, number: self.sources[number]))
        self.store = PageScheduleStore(self.directory)
        self.create = self.enterContext(patch.object(self.store, 'create', wraps=self.store.create))
        self.controller = SimpleNamespace(store=self.store, active=set(), tick=Mock(), close=Mock())
        self.controller.start = Mock(side_effect=self.start_schedule)
        self.controller.start_many = lambda account, page, ids: PageScheduleController.start_many(self.controller,account,page,ids)
        self.controller.pause = Mock(side_effect=self.pause_schedule)
        self.controller.cancel = Mock(side_effect=self.cancel_schedule)
        self.owner = SimpleNamespace(store=self.registry, studio=SimpleNamespace(data_dir=self.directory),
            get_page_scheduler=Mock(return_value=self.controller), status=tk.StringVar(self.root), submit_page_browser=Mock())
        self.store.save_template(self.a, self.p, 'Caption A nguyên văn\nDòng hai', '#A #PageA')
        self.store.save_template(self.a, self.p2, 'Caption Page thứ hai', '#Second')
        self.store.save_template(self.b, self.pb, 'Caption B nguyên văn', '#B')
        self.dialog = ScheduleVideoDialog(self.root, self.owner, self.source, initial_account=self.a, initial_page=self.p)
        self.dialog.withdraw()

    def start_schedule(self, ident):
        row = self.store.update(ident, expected={'scheduled', 'paused', 'needs_attention'},
                                state='armed', message='Synthetic enabled schedule')
        self.controller.active.add(ident)
        return row

    def pause_schedule(self, ident):
        row = self.store.update(ident, state='paused', message='Synthetic paused schedule')
        self.controller.active.discard(ident)
        return row

    def cancel_schedule(self, ident):
        row = self.store.update(ident, state='cancelled', message='Synthetic cancelled before sending')
        self.controller.active.discard(ident)
        return row

    def schedule(self, account=None, page=None):
        return self.store.create(account or self.a, page or self.p, self.source, 'Saved caption', '#Saved', time.time()+7200)

    def panel(self):
        activity = PageActivity(self.directory)
        self.manager = SimpleNamespace(owner=self.owner, account_id=self.a, page_key=self.p, store=activity,
            target=activity.page(self.a, self.p), open_url=Mock())
        panel = PageSchedulePanel(ttk.Frame(self.root), self.manager)
        return panel

    def choose(self, panel, row):
        panel.tree.selection_set(row['id'])
        panel.select_row()

    def test_mount_loads_initial_page_template_without_creating_or_starting(self):
        self.assertEqual((self.dialog.account_id, self.dialog.page_key), (self.a, self.p))
        self.assertEqual(self.dialog.caption.get('1.0', 'end-1c'), 'Caption A nguyên văn\nDòng hai')
        self.assertEqual(self.dialog.hashtags.get('1.0', 'end-1c'), '#A #PageA')
        self.assertNotEqual(self.registry.load()['selected_account_id'], self.dialog.account_id)
        self.create.assert_not_called()
        self.controller.start.assert_not_called()
        self.controller.tick.assert_not_called()
        self.owner.submit_page_browser.assert_not_called()

    def test_account_and_duplicate_page_name_selection_are_scoped_to_saved_ids(self):
        self.dialog.page_picker.current(1)
        self.dialog.select_page()
        self.assertEqual(self.dialog.page_key, self.p2)
        self.assertEqual(self.dialog.caption.get('1.0', 'end-1c'), 'Caption Page thứ hai')
        self.dialog.account_picker.current(1)
        self.dialog.select_account()
        self.assertEqual((self.dialog.account_id, self.dialog.page_key), (self.b, self.pb))
        self.assertEqual(len(self.dialog.page_picker['values']), 1)
        self.assertEqual(self.dialog.caption.get('1.0', 'end-1c'), 'Caption B nguyên văn')
        self.dialog.account_picker.current(0)
        self.dialog.select_account(initial_page=self.p)
        self.assertEqual(self.dialog.hashtags.get('1.0', 'end-1c'), '#A #PageA')
        self.controller.start.assert_not_called()

    def test_template_save_preserves_literal_text_for_only_selected_page(self):
        self.dialog.caption.delete('1.0', 'end')
        self.dialog.caption.insert('1.0', 'Literal {caption}\nNo replacement\n')
        self.dialog.hashtags.delete('1.0', 'end')
        self.dialog.hashtags.insert('1.0', '#One  #Two')
        self.dialog.save_template()
        self.assertEqual(self.store.template(self.a, self.p),
                         {'caption': 'Literal {caption}\nNo replacement\n', 'hashtags': '#One  #Two'})
        self.assertEqual(self.store.template(self.a, self.p2)['caption'], 'Caption Page thứ hai')
        self.assertEqual(self.store.template(self.b, self.pb)['hashtags'], '#B')
        self.create.assert_not_called()
        self.controller.start.assert_not_called()

    def test_concrete_preview_precedes_approval_and_captured_fields_are_frozen(self):
        expected_time = display_time(time.time()+7200)
        self.dialog.due_time.set(expected_time)
        self.dialog.show_preview()
        self.dialog.preview_window.withdraw()
        content = self.dialog.preview_content.get('1.0', 'end-1c')
        for value in ('Synthetic A', 'Synthetic Page', 'https://www.facebook.com/synthetic', 'Page ID: 100',
                      self.source['label'], self.source['path'], self.source['sha256'], expected_time, 'UTC+7',
                      'Caption A nguyên văn\nDòng hai\n\n#A #PageA'):
            self.assertIn(value, content)
        self.assertEqual(self.dialog.approve_button['text'], 'Lưu lịch / Cho phép tự đăng')
        self.assertEqual(str(self.dialog.preview_content['state']), 'disabled')
        self.create.assert_not_called()
        self.controller.start.assert_not_called()
        self.dialog.caption.delete('1.0', 'end')
        self.dialog.caption.insert('1.0', 'Changed after preview')
        self.source['label'] = 'Caller changed source'
        self.dialog.approve()
        args = self.create.call_args.args
        self.assertEqual(args[:2], (self.a, self.p))
        self.assertEqual(args[2]['label'], 'Synthetic campaign · Video 1')
        self.assertEqual(args[3:5], ('Caption A nguyên văn\nDòng hai', '#A #PageA'))
        self.assertEqual(self.create.call_args.kwargs['expected_target'],
            {'name': 'Synthetic Page', 'url': 'https://www.facebook.com/synthetic', 'page_id': '100'})
        self.assertEqual(self.controller.start.call_args.args, (self.dialog.saved_id,))
        self.assertTrue(self.dialog.done)
        self.assertIsNone(self.dialog.preview_window)
        self.owner.submit_page_browser.assert_not_called()

    def test_invalid_or_past_time_preserves_fields_without_preview_or_start(self):
        for value in ('not a date', '2020-01-01 12:00', display_time(time.time()+370*86400)):
            with self.subTest(value=value):
                self.dialog.due_time.set(value)
                self.dialog.show_preview()
                self.assertIsNone(self.dialog.preview_window)
                self.assertEqual(self.dialog.due_time.get(), value)
                self.assertEqual(self.dialog.caption.get('1.0', 'end-1c'), 'Caption A nguyên văn\nDòng hai')
                self.assertTrue(self.dialog.status.get())
        self.create.assert_not_called()
        self.controller.start.assert_not_called()

    def test_duplicate_approval_invokes_create_and_start_only_once(self):
        self.dialog.show_preview()
        self.dialog.preview_window.withdraw()
        self.dialog.approve()
        self.dialog.approve()
        self.dialog.show_preview()
        self.create.assert_called_once()
        self.controller.start.assert_called_once()
        self.assertEqual(len(self.store.list(self.a, self.p)), 1)
        self.assertEqual(str(self.dialog.preview_button['state']), 'disabled')

    def test_duplicate_video_error_preserves_input_and_does_not_arm_existing_schedule(self):
        saved = self.schedule()
        self.create.reset_mock()
        self.dialog.show_preview()
        self.dialog.preview_window.withdraw()
        self.dialog.approve()
        self.assertFalse(self.dialog.done)
        self.assertEqual(self.dialog.saved_id, '')
        self.assertIn('đã có lịch', self.dialog.preview_status.get())
        self.assertEqual(self.dialog.caption.get('1.0', 'end-1c'), 'Caption A nguyên văn\nDòng hai')
        self.assertEqual(self.store.get(saved['id'])['state'], 'scheduled')
        self.controller.start.assert_not_called()

    def test_start_error_retries_existing_saved_schedule_without_duplicate_create(self):
        self.controller.start.side_effect = ValueError('Synthetic browser owner closing')
        self.dialog.show_preview()
        self.dialog.preview_window.withdraw()
        self.dialog.approve()
        saved_id = self.dialog.saved_id
        self.assertTrue(saved_id)
        self.assertIn('đã lưu nhưng chưa bật', self.dialog.preview_status.get())
        self.assertEqual(self.store.get(saved_id)['state'], 'scheduled')
        self.assertEqual(self.dialog.approve_button['text'], 'Bật lịch đã lưu')
        self.assertEqual(str(self.dialog.caption['state']), 'disabled')
        self.controller.start.side_effect = self.start_schedule
        self.dialog.approve()
        self.create.assert_called_once()
        self.assertEqual(self.controller.start.call_count, 2)
        self.assertEqual(self.dialog.saved_id, saved_id)
        self.assertTrue(self.dialog.done)

    def test_page_changed_after_preview_requires_a_new_review_without_saving(self):
        self.dialog.show_preview()
        self.dialog.preview_window.withdraw()
        self.registry.save_page(self.a, 'Renamed Page', 'facebook.com/replaced', '999', record_id=self.p)
        self.dialog.approve()
        self.assertIn('đã đổi từ lúc xem trước', self.dialog.preview_status.get())
        self.assertFalse(self.dialog.done)
        self.create.assert_not_called()
        self.controller.start.assert_not_called()

    def test_template_read_error_preserves_typed_text_and_blocks_unreviewed_page(self):
        self.dialog.caption.insert('end', '\nUser edit')
        before = self.dialog.caption.get('1.0', 'end-1c')
        self.dialog.page_picker.current(1)
        with patch.object(self.store, 'template', side_effect=ValueError('Synthetic template read error')):
            self.dialog.select_page()
        self.assertEqual(self.dialog.caption.get('1.0', 'end-1c'), before)
        self.assertEqual(self.dialog.page_key, '')
        self.assertEqual(str(self.dialog.preview_button['state']), 'disabled')
        self.assertIn('Synthetic template read error', self.dialog.status.get())
        self.dialog.show_preview()
        self.assertIsNone(self.dialog.preview_window)
        self.controller.start.assert_not_called()

    def test_panel_mount_is_passive_and_lists_only_its_account_and_page(self):
        own = self.schedule()
        other = self.schedule(self.b, self.pb)
        same_account_other_page = self.schedule(self.a, self.p2)
        panel = self.panel()
        self.assertEqual(list(panel.rows), [own['id']])
        self.assertNotIn(other['id'], panel.rows)
        self.assertNotIn(same_account_other_page['id'], panel.rows)
        self.assertIsNotNone(panel.timer)
        self.assertEqual(str(panel.start_button['state']), 'disabled')
        self.controller.start.assert_not_called()
        self.controller.tick.assert_not_called()
        self.owner.submit_page_browser.assert_not_called()

    def test_panel_details_are_collapsed_and_selection_has_readable_caption_and_result(self):
        row = self.schedule()
        panel = self.panel()
        self.choose(panel, row)
        summary = panel.summary.get()
        self.assertIn('Saved caption #Saved', summary)
        self.assertIn(display_time(row['due_at']), summary)
        for technical_value in (row['id'], self.source['path'], self.source['sha256'],
                                row['snapshot']['target_url']):
            self.assertNotIn(technical_value, summary)
            self.assertIn(technical_value, panel.detail_text.get('1.0', 'end-1c'))
        self.assertEqual(panel.details_frame.winfo_manager(), '')
        self.assertFalse(panel.details_visible)
        self.assertEqual(panel.details_window.state(), 'withdrawn')
        self.create.reset_mock()
        panel.details_button.invoke()
        self.assertEqual(panel.details_frame.winfo_manager(), 'pack')
        self.assertTrue(panel.details_visible)
        self.assertIn(row['body'], panel.detail_text.get('1.0', 'end-1c'))
        panel.details_button.invoke()
        self.assertEqual(panel.details_frame.winfo_manager(), '')
        self.assertEqual(panel.details_window.state(), 'withdrawn')
        self.assertEqual(panel.tree.selection(), (row['id'],))
        self.create.assert_not_called()
        self.controller.start.assert_not_called()
        self.controller.pause.assert_not_called()
        self.controller.cancel.assert_not_called()

    def test_select_all_and_details_toggle_keep_bulk_actions_within_page(self):
        own = [self.extra_schedule(n) for n in (2, 3)]
        other = self.extra_schedule(4, self.p2)
        panel = self.panel()
        panel.select_all_button.invoke()
        expected = {row['id'] for row in own}
        self.assertEqual(set(panel.tree.selection()), expected)
        self.assertIn('Đã chọn 2', panel.summary.get())
        panel.details_button.invoke()
        panel.details_button.invoke()
        panel.refresh_button.invoke()
        self.assertEqual(set(panel.tree.selection()), expected)
        panel.start_button.invoke()
        self.assertEqual([self.store.get(row['id'])['state'] for row in own], ['armed', 'armed'])
        self.assertEqual(self.store.get(other['id'])['state'], 'scheduled')
        self.assertIn('Đã bật 2', panel.status.get())
        self.assertEqual(panel.details_frame.winfo_manager(), '')
        panel.pause_button.invoke()
        self.assertEqual([self.store.get(row['id'])['state'] for row in own], ['paused', 'paused'])
        self.assertEqual(self.store.get(other['id'])['state'], 'scheduled')
        self.assertEqual(set(panel.tree.selection()), expected)

    def test_panel_explicit_start_pause_and_close_does_not_stop_global_schedule(self):
        row = self.schedule()
        panel = self.panel()
        self.choose(panel, row)
        self.assertEqual(str(panel.start_button['state']), 'normal')
        panel.start_button.invoke()
        self.assertEqual(self.store.get(row['id'])['state'], 'armed')
        self.assertEqual(str(panel.time_button['state']), 'disabled')
        panel.pause_button.invoke()
        self.assertEqual(self.store.get(row['id'])['state'], 'paused')
        panel.start_button.invoke()
        panel.destroy()
        self.assertEqual(self.store.get(row['id'])['state'], 'armed')
        self.assertIn(row['id'], self.controller.active)
        self.assertTrue(panel.closed)
        self.assertIsNone(panel.timer)
        self.assertEqual(self.controller.pause.call_count, 1)
        self.controller.close.assert_not_called()

    def test_panel_change_time_requires_future_and_never_arms_or_changes_body(self):
        row = self.schedule()
        panel = self.panel()
        self.choose(panel, row)
        panel.change_time()
        panel.time_window.withdraw()
        panel.new_time.set('2020-01-01 12:00')
        panel.save_time(row['id'])
        self.assertIsNotNone(panel.time_window)
        self.assertIn('tương lai', panel.time_status.get())
        panel.new_time.set(display_time(time.time()+10800))
        panel.save_time(row['id'])
        changed = self.store.get(row['id'])
        self.assertEqual(changed['state'], 'scheduled')
        self.assertGreater(changed['due_at'], row['due_at'])
        self.assertEqual(changed['body'], row['body'])
        self.assertEqual(changed['snapshot'], row['snapshot'])
        self.assertIsNone(panel.time_window)
        self.controller.start.assert_not_called()

    def test_cancel_shows_concrete_page_video_time_and_keeps_history_and_video(self):
        row = self.schedule()
        panel = self.panel()
        self.choose(panel, row)
        self.assertEqual(str(panel.cancel_button['state']), 'normal')
        panel.cancel_selected()
        self.controller.cancel.assert_not_called()
        confirmation = self.confirm.call_args.args[1]
        for value in (row['source']['label'], 'Synthetic Page', 'https://www.facebook.com/synthetic', display_time(row['due_at']), 'UTC+7'):
            self.assertIn(value, confirmation)
        self.confirm.return_value = True
        panel.cancel_selected()
        self.controller.cancel.assert_called_once_with(row['id'])
        self.assertEqual(self.store.get(row['id'])['state'], 'cancelled')
        self.assertTrue(Path(row['source']['path']).is_file())
        self.assertEqual(self.store.activity.post(self.a, self.p, row['post_id'])['state'], 'draft')
        self.assertIn(row['id'], panel.rows)
        self.assertEqual(str(panel.start_button['state']), 'disabled')
        self.assertEqual(str(panel.cancel_button['state']), 'disabled')
        self.controller.start.assert_not_called()

    def test_cancel_rejects_attempted_schedule_before_confirmation_or_controller(self):
        row = self.schedule()
        self.store.update(row['id'], state='needs_attention', attempted_at='Synthetic Chrome command time')
        panel = self.panel()
        self.choose(panel, row)
        self.assertEqual(str(panel.cancel_button['state']), 'disabled')
        panel.cancel_selected()
        self.confirm.assert_not_called()
        self.controller.cancel.assert_not_called()
        self.assertIn('chưa có thao tác gửi', panel.status.get())

    def test_attempted_or_uncertain_schedule_cannot_resume_or_change_time(self):
        row = self.schedule()
        self.store.update(row['id'], state='needs_attention', attempted_at='Synthetic attempted time', message='Check Page first')
        panel = self.panel()
        self.choose(panel, row)
        self.assertEqual(str(panel.start_button['state']), 'disabled')
        self.assertEqual(str(panel.time_button['state']), 'disabled')
        panel.change_time()
        self.assertIsNone(panel.time_window)
        self.assertIn('trước lượt chuẩn bị/gửi', panel.status.get())
        self.controller.start.assert_not_called()

    def test_submitted_schedule_has_distinct_verifying_status_and_cannot_be_resent(self):
        row = self.schedule()
        self.store.update(row['id'], state='verifying', phase='verify', attempted_at='Synthetic submit time',
                          message='Waiting for Facebook receipt')
        self.controller.active.add(row['id'])
        panel = self.panel()
        self.choose(panel, row)
        self.assertEqual(panel.tree.item(row['id'])['values'][2], 'Đã gửi — đang xác minh')
        self.assertIn('Trạng thái: Đã gửi — đang xác minh', panel.summary.get())
        self.assertIn('chưa xác nhận bài đã xuất hiện', panel.summary.get())
        self.assertIn('Các bài khác đến giờ vẫn được xử lý', panel.summary.get())
        for button in (panel.now_button, panel.start_button, panel.time_button, panel.cancel_button, panel.remove_button):
            self.assertEqual(str(button['state']), 'disabled')
        self.controller.start.assert_not_called()
        self.owner.submit_page_browser.assert_not_called()

    def test_unowned_verification_and_unknown_are_distinct(self):
        row = self.schedule()
        self.store.update(row['id'], state='verifying', phase='verify', attempted_at='Synthetic submit time')
        panel = self.panel()
        self.choose(panel, row)
        self.assertEqual(panel.tree.item(row['id'])['values'][2], 'Đã gửi — chờ kiểm tra lại')
        self.store.activity.transition(self.a, self.p, row['post_id'], 'unknown', 'Synthetic expired verification')
        self.store.update(row['id'], state='needs_attention')
        panel.refresh()
        self.assertEqual(panel.tree.item(row['id'])['values'][2], 'Chưa xác minh đã đăng')

    def test_result_recheck_resumes_read_only_following_without_url_dialog_or_resend(self):
        row = self.schedule()
        row = self.store.update(row['id'], state='needs_attention', phase='verify',
            attempted_at='Synthetic prior submit time', message='Synthetic expired result check')
        self.store.activity.transition(self.a, self.p, row['post_id'], 'unknown', 'Synthetic pending result')
        self.controller.can_follow_result = Mock(return_value=True)
        self.controller.can_inspect_result = Mock(return_value=False)
        def follow(account, page, ident):
            self.controller.active.add(ident)
            return self.store.update(ident, state='verifying', phase='verify', request_id='')
        self.controller.follow_result = Mock(side_effect=follow)
        panel = self.panel()
        self.choose(panel, row)
        self.assertEqual(str(panel.check_button['state']), 'normal')
        with patch('teodub.page_schedule_check_ui.ScheduleResultCheckDialog') as dialog:
            panel.check_button.invoke()
        self.controller.follow_result.assert_called_once_with(self.a, self.p, row['id'])
        dialog.assert_not_called()
        self.assertIsNone(panel.check_window)
        self.assertEqual(panel.tree.item(row['id'])['values'][2], 'Đã gửi — đang xác minh')
        self.assertIn('không tải hoặc đăng lại', panel.status.get())
        self.assertEqual(self.store.activity.post(self.a, self.p, row['post_id'])['state'], 'unknown')
        self.controller.start.assert_not_called()
        self.owner.submit_page_browser.assert_not_called()

    def test_overdue_armed_schedule_is_waiting_for_send_turn(self):
        row = self.schedule()
        self.store.update(row['id'], state='armed', due_at=time.time()-60)
        self.controller.active.add(row['id'])
        panel = self.panel()
        self.assertEqual(panel.tree.item(row['id'])['values'][2], 'Đã đến giờ — chờ lượt gửi')

    def test_open_destination_uses_bound_page_and_verified_post_without_retargeting(self):
        row = self.schedule()
        panel = self.panel()
        self.choose(panel, row)
        self.registry.select_account(self.b)
        panel.open_selected()
        self.manager.open_url.assert_called_once_with('https://www.facebook.com/synthetic')
        self.store.activity.transition(self.a, self.p, row['post_id'], 'published', 'Synthetic verified result',
                                       external_url='https://www.facebook.com/synthetic/posts/77')
        panel.open_selected()
        self.assertEqual(self.manager.open_url.call_args.args, ('https://www.facebook.com/synthetic/posts/77',))
        self.owner.submit_page_browser.assert_not_called()

    def test_panel_store_error_preserves_list_and_shows_reason_without_starting(self):
        row = self.schedule()
        panel = self.panel()
        self.choose(panel, row)
        with patch.object(self.store, 'list', side_effect=ValueError('Synthetic local read error')):
            panel.refresh()
        self.assertIn('Synthetic local read error', panel.status.get())
        self.assertIn(row['id'], panel.rows)
        self.controller.start.assert_not_called()

    def extra_schedule(self, number, page=None):
        source = dict(self.source,number=number,key='c'*32+':'+str(number),label='Video '+str(number))
        self.sources[number] = source
        return self.store.create(self.a,page or self.p,source,'Caption','#Saved',time.time()+7200)

    def test_multiselect_survives_poll_and_starts_only_selection(self):
        rows = [self.extra_schedule(n) for n in (2,3,4)]
        panel = self.panel()
        panel.tree.selection_set([row['id'] for row in rows[:2]])
        panel.refresh()
        self.assertEqual(set(panel.tree.selection()),{row['id'] for row in rows[:2]})
        self.assertEqual(str(panel.tree['selectmode']),'extended')
        panel.start()
        self.assertEqual([self.store.get(row['id'])['state'] for row in rows],['armed','armed','scheduled'])
        self.assertIn('Đã bật 2',panel.status.get())
        panel.refresh()
        self.assertEqual(set(panel.tree.selection()),{row['id'] for row in rows[:2]})
        self.assertEqual(str(panel.time_button['state']),'disabled')
        self.assertEqual(str(panel.cancel_button['state']),'disabled')
        panel.change_time()
        self.assertIsNone(panel.time_window)
        panel.pause()
        self.assertEqual([self.store.get(row['id'])['state'] for row in rows],['paused','paused','scheduled'])

    def test_start_all_skips_past_and_attempted_and_other_page_without_changing_times(self):
        rows = [self.extra_schedule(n) for n in (2,3,4)]
        other = self.extra_schedule(5,self.p2)
        self.store.update(rows[1]['id'],due_at=time.time()-10)
        self.store.update(rows[2]['id'],state='needs_attention',attempted_at='uncertain')
        old = self.store.get(rows[1]['id'])
        panel = self.panel()
        panel.start_all()
        self.assertEqual([self.store.get(row['id'])['state'] for row in rows],['armed','scheduled','needs_attention'])
        self.assertEqual(self.store.get(other['id'])['state'],'scheduled')
        self.assertEqual(self.store.get(old['id'])['due_at'],old['due_at'])
        self.assertIn('Bỏ qua 2',panel.status.get())
        self.assertIn('Giờ đăng đã qua',panel.bulk_report)
        self.assertIn('Đã có thao tác gửi',panel.bulk_report)
        panel.start_all()
        self.assertEqual(self.controller.start.call_count,1)
        self.assertIn('Đã chạy 1',panel.status.get())


if __name__ == '__main__':
    unittest.main()
