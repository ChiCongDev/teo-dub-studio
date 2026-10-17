"""Hidden Tk fixtures: no real chat, browser, Facebook, or account data."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import tkinter as tk
from tkinter import ttk
from types import SimpleNamespace
import unittest
import uuid
from unittest.mock import Mock, patch

from teodub.page_content_ui import PageContentPanel, plan_preview
from teodub.facebook_accounts import FacebookAccounts
from teodub.page_content_plan import ContentPlanStore
from teodub.page_schedule import PageScheduleController, PageScheduleStore


class FakePlans:
    def __init__(self):
        self.link = dict(thread_id='', title='', auto_sync=False)
        self.rows = []
        self.import_calls = []
        self.approve_calls = []
        self.stage_calls = []

    def get_connection(self, account, page):
        return deepcopy(self.link)

    def save_connection(self, account, page, ident, title, auto_sync=False):
        self.link = dict(thread_id=ident, title=title, auto_sync=auto_sync)

    def disconnect(self, account, page):
        self.link = dict(thread_id='', title='', auto_sync=False)

    def request_context(self, account, page):
        return 'Exact request for ' + account + '/' + page

    def plans(self, account, page):
        return deepcopy(self.rows)

    def get_plan(self, ident, account, page):
        return deepcopy(next(row for row in self.rows if row['plan_id'] == ident))

    def import_messages(self, account, page, snapshot):
        self.import_calls.append((account, page, snapshot))
        self.rows = [sample_plan()]
        return dict(plan=self.rows[0], status='Đã nhập kế hoạch; cần duyệt để bật lịch.', imported=True, error=False)

    def stage(self, ident, account, page, expected_digest):
        self.stage_calls.append((ident, account, page, expected_digest))
        row = self.get_plan(ident, account, page)
        if row['digest'] != expected_digest:
            raise ValueError('Nội dung thay đổi')
        return row

    def approve(self, ident, account, page, controller, expected_digest):
        row = next(row for row in self.rows if row['plan_id'] == ident)
        if row['digest'] != expected_digest:
            raise ValueError('Nội dung thay đổi từ lúc xem trước')
        self.approve_calls.append((ident, account, page, controller, expected_digest))
        row['state'] = 'approved'
        return deepcopy(row)


def sample_plan(ident='plan-one'):
    return dict(plan_id=ident, title='Kế hoạch câu cá', state='proposed', digest='frozen-digest',
        target=dict(name='Fixture Page', url='https://www.facebook.com/fixture', page_id='100'),
        items=[dict(id='one', kind='text', due_at=1791545400, body='Nội dung đầy đủ\n#cauca',
                    video_key='', goal='Chia sẻ mẹo dùng mồi', state='proposed')])


class PageContentUITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.controller = Mock()
        self.owner = SimpleNamespace(studio=SimpleNamespace(data_dir=Path(self.temp.name)),
            page_bridge=Mock(), get_page_scheduler=Mock(return_value=self.controller))
        self.manager = SimpleNamespace(owner=self.owner, account_id='account-a', page_key='page-a',
            target=dict(name='Fixture Page', url='https://www.facebook.com/fixture', page_id='100'),
            schedule_panel=SimpleNamespace(refresh=Mock()))
        self.store, self.reader = FakePlans(), Mock()
        self.reader.list_threads.return_value = [dict(id='thread-one', title='Phát triển page câu cá')]
        self.reader.read_thread.return_value = dict(id='thread-one', messages=[])
        self.thread = self.enterContext(patch('teodub.page_content_ui.threading.Thread'))
        self.enterContext(patch.object(tk.Toplevel, 'grab_set', return_value=None))
        self.panel = PageContentPanel(ttk.Notebook(self.root), self.manager, store=self.store, reader=self.reader)

    def run_worker(self):
        self.thread.call_args.kwargs['target']()

    def drain(self):
        if self.panel.timer is not None:
            self.panel.after_cancel(self.panel.timer)
        self.panel.poll()

    def linked(self):
        self.store.save_connection('account-a', 'page-a', 'thread-one', 'Phát triển page câu cá')
        self.panel.reload_connection()

    def received(self):
        self.linked()
        self.store.rows = [sample_plan()]
        self.panel.refresh_plans()

    def preview(self):
        self.received()
        self.panel.show_preview()
        self.run_worker()
        self.drain()

    def test_mount_is_passive_and_has_no_implicit_link(self):
        self.thread.assert_not_called()
        self.reader.list_threads.assert_not_called()
        self.reader.read_thread.assert_not_called()
        self.assertEqual(self.store.link['thread_id'], '')
        self.assertFalse(self.store.approve_calls)
        self.assertEqual(str(self.panel.approve_button['state']), 'disabled')

    def test_unlinked_page_shows_one_source_entry_with_advanced_controls_collapsed(self):
        self.assertIn('Chưa có nguồn chiến lược', self.panel.source_summary.get())
        self.assertIn('Liên kết chat', str(self.panel.source_toggle['text']))
        self.assertFalse(self.panel.strategy_open)
        self.assertEqual(self.panel.strategy_frame.winfo_manager(), '')
        self.assertFalse(self.panel.help_open)
        self.assertEqual(self.panel.help_frame.winfo_manager(), '')
        self.assertTrue(self.panel.source_toggle.winfo_manager())
        self.reader.list_threads.assert_not_called()
        self.reader.read_thread.assert_not_called()

    def test_linked_mount_keeps_source_details_and_execution_help_collapsed(self):
        self.panel.destroy()
        self.store.save_connection('account-a', 'page-a', 'thread-one', 'Phát triển page câu cá')
        self.panel = PageContentPanel(ttk.Notebook(self.root), self.manager, store=self.store, reader=self.reader)
        self.assertIn('Nguồn chiến lược: Phát triển page câu cá', self.panel.source_summary.get())
        self.assertEqual(self.panel.strategy_frame.winfo_manager(), '')
        self.assertEqual(self.panel.help_frame.winfo_manager(), '')
        self.assertEqual(self.panel.thread_picker.master.master, self.panel.strategy_frame)
        self.assertEqual(self.panel.auto_button.master.master, self.panel.strategy_frame)
        self.thread.assert_not_called()

    def test_public_source_settings_opens_without_searching_or_changing_target(self):
        target = deepcopy(self.manager.target)
        self.panel.open_strategy_settings()
        self.assertTrue(self.panel.strategy_open)
        self.assertEqual(self.panel.strategy_frame.winfo_manager(), 'grid')
        self.panel.reload_connection()
        self.assertEqual(self.panel.strategy_frame.winfo_manager(), 'grid')
        self.assertEqual(self.manager.target, target)
        self.assertEqual((self.panel.account_id, self.panel.page_key), ('account-a', 'page-a'))
        self.thread.assert_not_called()
        self.panel.toggle_strategy_settings()
        self.assertFalse(self.panel.strategy_open)
        self.assertEqual(self.panel.strategy_frame.winfo_manager(), '')

    def test_collapsed_help_preserves_video_and_reels_limit(self):
        self.panel.toggle_help()
        self.assertTrue(self.panel.help_open)
        self.assertEqual(self.panel.help_frame.winfo_manager(), 'grid')
        note = self.panel.help_frame.winfo_children()[0]
        self.assertIn('video/Reels', str(note['text']))
        self.assertIn('Giữ Tèo mở', str(note['text']))
        self.panel.toggle_help()
        self.assertEqual(self.panel.help_frame.winfo_manager(), '')
        self.assertFalse(self.store.approve_calls)

    def test_plan_selects_first_caption_and_keeps_exact_file_details_for_review(self):
        self.received()
        self.store.rows[0]['items'][0].update(kind='video', video_key='private-opaque-video-key',
            source=dict(path='C:/fixture/private/video.mp4', sha256='exact-hash'))
        self.panel.refresh_plans()
        self.assertEqual(self.panel.tree.selection(), ('0',))
        content = self.panel.detail.get('1.0', 'end')
        self.assertIn('Nội dung đầy đủ\n#cauca', content)
        self.assertIn('Video: video.mp4', content)
        self.assertNotIn('C:/fixture/private', content)
        self.assertNotIn('exact-hash', content)
        self.assertNotIn('private-opaque-video-key', content)
        self.assertNotIn('Tài khoản đã chọn', content)
        review = plan_preview(self.store.rows[0], self.manager.target, self.panel.account_id)
        self.assertIn('C:/fixture/private/video.mp4', review)
        self.assertIn('exact-hash', review)

    def test_expanding_source_before_approval_keeps_original_page_and_frozen_plan(self):
        self.preview()
        self.panel.open_strategy_settings()
        self.panel.toggle_help()
        self.panel.toggle_strategy_settings()
        self.panel.approve()
        self.assertEqual(self.store.approve_calls,
            [('plan-one', 'account-a', 'page-a', self.controller, 'frozen-digest')])
        self.assertEqual(self.manager.target['page_id'], '100')
        self.reader.list_threads.assert_not_called()
        self.reader.read_thread.assert_not_called()

    def test_workspace_and_expanded_source_fit_880_pixel_fixture(self):
        self.received()
        notebook = self.panel.master
        notebook.add(self.panel, text='Kế hoạch')
        notebook.pack(fill='both', expand=True)
        self.root.geometry('880x880')
        self.root.update_idletasks()
        self.assertLessEqual(self.panel.winfo_reqwidth(), 880)
        self.panel.open_strategy_settings()
        self.panel.toggle_help()
        self.root.update_idletasks()
        self.assertLessEqual(self.panel.winfo_reqwidth(), 880)
        self.assertGreaterEqual(self.panel.tree.winfo_height(), 100)
        self.assertGreaterEqual(self.panel.detail.winfo_height(), 70)

    def test_search_and_save_bind_the_original_page_only(self):
        self.panel.find_threads()
        self.run_worker()
        self.assertTrue(self.panel.busy)
        self.assertEqual(self.panel.threads, [])
        self.drain()
        self.panel.save_link()
        self.assertEqual(self.store.link['thread_id'], 'thread-one')
        self.reader.list_threads.assert_called_once_with(search_term='Phát triển page câu cá', limit=30)
        self.assertFalse(self.store.approve_calls)

    def test_async_reader_only_delivers_data_until_tk_drains_it(self):
        self.linked()
        self.panel.sync()
        self.run_worker()
        self.assertEqual(self.store.import_calls, [])
        self.drain()
        self.assertEqual(len(self.store.import_calls), 1)
        self.assertEqual(self.store.import_calls[0][:2], ('account-a', 'page-a'))
        self.assertFalse(self.store.approve_calls)
        self.assertFalse(self.controller.start.called)
        self.assertIn('cần duyệt', self.panel.status.get())

    def test_read_failure_keeps_link_and_existing_plans(self):
        self.received()
        self.reader.read_thread.side_effect = ValueError('Fixture unavailable')
        self.panel.sync()
        self.run_worker()
        self.drain()
        self.assertIn('Fixture unavailable', self.panel.status.get())
        self.assertEqual(self.store.link['thread_id'], 'thread-one')
        self.assertEqual(self.panel.selected_plan_id, 'plan-one')
        self.assertFalse(self.store.approve_calls)

    def test_duplicate_sync_click_starts_one_reader(self):
        self.linked()
        self.panel.sync()
        self.panel.sync()
        self.assertEqual(self.thread.call_count, 1)

    def test_worker_start_failure_restores_controls_without_losing_link(self):
        self.linked()
        self.thread.return_value.start.side_effect = RuntimeError('Fixture no worker')
        self.panel.sync()
        self.assertFalse(self.panel.busy)
        self.assertIn('Fixture no worker', self.panel.status.get())
        self.assertEqual(self.store.link['thread_id'], 'thread-one')
        self.assertFalse(self.store.approve_calls)

    def test_link_changed_while_reading_discards_old_reply(self):
        self.linked()
        self.panel.sync()
        self.store.save_connection('account-a', 'page-a', 'thread-other', 'Other')
        self.run_worker()
        self.drain()
        self.assertFalse(self.store.import_calls)
        self.assertFalse(self.store.approve_calls)

    def test_closed_panel_discards_late_reply_and_does_not_close_chat(self):
        self.linked()
        self.panel.sync()
        self.panel.destroy()
        self.run_worker()
        self.panel.poll()
        self.assertTrue(self.panel.closed)
        self.assertFalse(self.store.import_calls)
        self.reader.close.assert_not_called()
        self.assertIsNone(self.panel.auto_timer)

    def test_auto_sync_persists_without_arming_and_uses_singleton_receiver(self):
        self.linked()
        self.panel.auto_sync.set(True)
        self.panel.toggle_auto()
        self.assertTrue(self.store.link['auto_sync'])
        self.assertIsNone(self.panel.auto_timer)
        self.thread.assert_not_called()
        self.assertFalse(self.store.approve_calls)
        self.assertIn('vẫn cần', self.panel.status.get())

    def test_standalone_auto_tick_receives_without_approval(self):
        self.linked()
        self.owner.page_bridge = None
        self.panel.auto_sync.set(True)
        self.panel.toggle_auto()
        self.assertIsNotNone(self.panel.auto_timer)
        self.panel.after_cancel(self.panel.auto_timer)
        self.panel.auto_tick()
        self.run_worker()
        self.drain()
        self.assertEqual(len(self.store.import_calls), 1)
        self.assertFalse(self.store.approve_calls)

    def test_copy_request_worker_updates_clipboard_on_tk_thread(self):
        self.linked()
        self.panel.copy_request()
        self.assertTrue(self.panel.busy)
        self.run_worker()
        self.drain()
        self.assertEqual(self.panel.clipboard_get(), 'Exact request for account-a/page-a')
        self.assertFalse(self.store.approve_calls)

    def test_approve_requires_preview_then_one_confirm(self):
        self.received()
        self.panel.approve()
        self.assertFalse(self.store.approve_calls)
        self.panel.show_preview()
        self.assertIsNone(self.panel.preview_window)
        self.run_worker()
        self.drain()
        self.assertIsNotNone(self.panel.preview_window)
        self.assertFalse(self.store.approve_calls)
        self.panel.approve()
        self.panel.approve()
        self.assertEqual(self.store.approve_calls,
            [('plan-one', 'account-a', 'page-a', self.controller, 'frozen-digest')])
        self.assertEqual(str(self.panel.approve_button['state']), 'disabled')
        self.assertIsNone(self.panel.preview_window)

    def test_preview_approval_uses_frozen_plan_despite_later_selection(self):
        self.preview()
        self.store.rows.append(sample_plan('plan-two'))
        self.panel.refresh_plans()
        self.panel.plan_picker.current(1)
        self.panel.select_plan()
        self.panel.approve()
        self.assertEqual(self.store.approve_calls[0][0], 'plan-one')
        self.assertEqual(self.store.rows[1]['state'], 'proposed')

    def test_plan_change_after_preview_fails_without_approval(self):
        self.preview()
        self.store.rows[0]['digest'] = 'changed-digest'
        self.panel.approve()
        self.assertFalse(self.store.approve_calls)
        self.assertIn('thay đổi', self.panel.preview_status.get())
        self.assertIsNotNone(self.panel.preview_window)

    def test_preview_contains_whole_body_video_hash_and_target(self):
        plan = sample_plan()
        plan['items'][0].update(kind='video', video_key='video-key',
            source=dict(label='Video 1', path='C:/fixture/video.mp4', sha256='exact-hash'))
        content = plan_preview(plan, self.manager.target, 'account-a')
        self.assertIn('Nội dung đầy đủ\n#cauca', content)
        self.assertIn('C:/fixture/video.mp4', content)
        self.assertIn('exact-hash', content)
        self.assertIn('Page ID: 100', content)

    def test_disconnect_keeps_existing_plan_and_never_cancels_schedule(self):
        self.received()
        self.panel.disconnect()
        self.assertEqual(self.store.link['thread_id'], '')
        self.assertEqual(len(self.store.rows), 1)
        self.controller.cancel.assert_not_called()
        self.assertFalse(self.store.approve_calls)

    def test_same_plan_uuid_from_another_thread_is_hidden(self):
        self.linked()
        current, other = sample_plan(), sample_plan()
        current.update(record_id='record-current', thread_id='thread-one')
        other.update(record_id='record-other', thread_id='thread-other')
        self.store.rows = [other, current]
        self.panel.refresh_plans()
        self.assertEqual(self.panel.plan_order, ['record-current'])
        self.assertEqual(self.panel.selected_plan_id, 'record-current')

    def test_partially_approved_plan_cannot_be_rearmed_by_new_preview(self):
        self.received()
        self.store.rows[0].update(state='needs_attention', approved_at='2026-10-09T18:00:00+07:00')
        self.panel.refresh_plans()
        self.assertEqual(str(self.panel.approve_button['state']), 'disabled')
        self.panel.show_preview()
        self.assertIsNone(self.panel.preview_window)
        self.assertFalse(self.store.stage_calls)

    def test_real_backend_and_scheduler_are_passive_until_preview_confirmation(self):
        self.panel.destroy()
        directory = Path(self.temp.name)
        registry = FacebookAccounts(directory)
        account = registry.add_account('Synthetic account')['id']
        page = registry.save_page(account, 'Fixture Page', 'facebook.com/profile.php?id=100', '100')['id']
        linked = str(uuid.uuid4())
        self.store = ContentPlanStore(directory)
        self.store.save_connection(account, page, linked, 'Phát triển page câu cá')
        due = datetime.now(timezone(timedelta(hours=7))) + timedelta(days=1)
        document = dict(format='teo-page-plan', version=1, plan_id=str(uuid.uuid4()),
            title='Fixture plan', page_id='100', time_zone='Asia/Bangkok', items=[dict(id='one',
            kind='text', due_at=due.isoformat(timespec='seconds'), body='Mẹo câu cá ngoại tuyến\n#cauca',
            video_key='', goal='Chia sẻ kiến thức')])
        self.reader.read_thread.return_value = dict(id=linked, messages=[dict(turn_id='done',
            phase='final_answer', turn_status='completed',
            text='```teo-page-plan\n' + json.dumps(document, ensure_ascii=False) + '\n```')])
        self.owner.sessions = {}
        self.owner.status = Mock()
        self.owner.submit_page_browser = Mock(return_value=None)
        self.controller = PageScheduleController(self.owner)
        self.addCleanup(self.controller.close)
        self.owner.get_page_scheduler.return_value = self.controller
        self.manager.account_id, self.manager.page_key = account, page
        self.manager.target = registry.load()['pages'][0]
        self.panel = PageContentPanel(ttk.Notebook(self.root), self.manager, store=self.store, reader=self.reader)
        self.panel.sync()
        self.run_worker()
        self.drain()
        self.assertEqual(len(self.store.plans(account, page)), 1)
        schedules = PageScheduleStore(directory)
        self.assertEqual(schedules.list(account, page), [])
        self.assertFalse(self.controller.active)
        self.panel.show_preview()
        self.run_worker()
        self.drain()
        self.assertEqual(len(schedules.list(account, page)), 1)
        self.assertFalse(self.controller.active)
        self.assertIsNotNone(self.panel.preview_window)
        self.panel.approve()
        self.assertEqual(len(self.controller.active), 1)
        self.panel.approve()
        self.assertEqual(len(self.controller.active), 1)
        self.owner.submit_page_browser.assert_not_called()


if __name__ == '__main__':
    unittest.main()
