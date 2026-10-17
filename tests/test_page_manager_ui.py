"""Hidden Tk, temporary Page data, no Facebook or personal browser interactions."""
from pathlib import Path
import tempfile
import threading
import tkinter as tk
from tkinter import ttk
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from teodub.facebook_accounts import META_APPS, META_EXPLORER
from teodub.facebook_ui import FacebookTab, BrowserSession
from teodub.meta_page_api import PageApiService, MetaError
from teodub.page_manager_ui import DELIVERY_LABELS, FINISH_LABELS
from tests.test_page_activity_api import FakeVault


class PageManagerUITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.studio = SimpleNamespace(data_dir=Path(self.temp.name),closing=False,closed=False)
        self.enterContext(patch('teodub.facebook_ui.create_page_bridge', return_value=Mock(enabled=True)))
        self.owner = FacebookTab(ttk.Notebook(self.root),self.studio)
        self.a = self.owner.store.add_account('Synthetic A')['id']
        self.p = self.owner.store.save_page(self.a,'Synthetic Page','facebook.com/synthetic','100')['id']
        self.b = self.owner.store.add_account('Synthetic B')['id']
        self.pb = self.owner.store.save_page(self.b,'Other Page','facebook.com/other','200')['id']
        self.remote = Mock()
        self.remote.verify_page.return_value = dict(id='100',name='Verified Page',category='Test')
        self.remote.page_info.return_value = dict(name='Verified Page',followers=0,likes=4)
        self.remote.recent_posts.return_value = []
        self.remote.request.return_value = dict(id='100_77')
        self.remote.post_info.return_value = dict(id='100_77',is_published=True,
            permalink_url='https://www.facebook.com/synthetic/posts/77')
        self.service = PageApiService(self.studio.data_dir,client_factory=Mock(return_value=self.remote),credentials=FakeVault())
        self.enterContext(patch('teodub.page_manager_ui.PageApiService',return_value=self.service))
        self.thread = self.enterContext(patch('teodub.page_manager_ui.threading.Thread',return_value=Mock()))
        self.browser = self.enterContext(patch('teodub.facebook_browser.open_facebook_browser'))
        self.enterContext(patch.object(tk.Toplevel,'grab_set',return_value=None))
        self.owner.reload()
        self.owner.activate_account(self.a)
        self.owner.tree.selection_set(self.p)
        self.window = self.owner.manage_page()
        self.window.withdraw()

    def drain(self):
        self.window.after_cancel(self.window.timer)
        self.window.poll()

    def run_worker(self):
        self.thread.call_args.kwargs['target']()
        self.drain()

    def connect(self):
        self.window.secret.set('synthetic-key')
        self.window.connect_api()
        self.run_worker()

    def draft(self, mode='api'):
        self.window.delivery_mode.set(DELIVERY_LABELS[mode])
        self.window.body.insert('1.0','Bài thử ngoại tuyến')
        self.window.save_draft()
        return self.window.post_id

    def browser_draft(self, finish='manual'):
        self.window.delivery_mode.set(DELIVERY_LABELS['browser'])
        self.window.browser_finish_mode.set(FINISH_LABELS[finish])
        self.window.delivery_changed()
        return self.draft('browser')

    @staticmethod
    def descendants(widget):
        result = []
        for child in widget.winfo_children():
            result.append(child)
            result.extend(PageManagerUITests.descendants(child))
        return result

    def modal_button(self, label):
        return next(widget for widget in self.descendants(self.window.preview_window)
                    if isinstance(widget,ttk.Button) and widget['text'] == label)

    def post_session(self):
        session = BrowserSession(self.a,'Synthetic A',opened=True)
        session.post_actions = {}
        self.owner.sessions[self.a] = session
        return session

    def prepared_snapshot(self, post_id, finish='manual'):
        return dict(version=1,account_id=self.a,account_name='Synthetic A',page_key=self.p,post_id=post_id,
            prepared_at='2026-10-03T12:00:00+07:00',target_name='Synthetic Page',
            target_url='https://www.facebook.com/synthetic',target_id='100',body='Bài thử ngoại tuyến',
            kind='text',media=[],observed_files=[],browser_finish_mode=finish,
            identity=dict(page_id='100',name='Synthetic Page',url='https://www.facebook.com/synthetic'),
            composer=dict(url='https://www.facebook.com/synthetic',body='Bài thử ngoại tuyến',attachment_count=0,preview_count=0))

    def test_manager_mount_is_passive_reuses_window_and_new_account_scopes(self):
        self.window.refresh_page_run_status()
        self.assertIn('Chưa bật',self.window.page_run_status.get())
        self.remote.verify_page.assert_not_called()
        self.remote.request.assert_not_called()
        self.thread.assert_not_called()
        self.browser.assert_not_called()
        self.assertEqual([self.window.tabs.tab(t,'text') for t in self.window.tabs.tabs()],
            ['Thiết lập','Kế hoạch','Lịch đăng','Kết quả','Lịch sử AI'])
        self.assertIs(self.owner.manage_page(),self.window)
        self.window.withdraw()
        self.owner.activate_account(self.b)
        self.owner.tree.selection_set(self.pb)
        other = self.owner.manage_page()
        other.withdraw()
        self.assertEqual(other.account_id,self.b)
        self.assertEqual(self.window.account_id,self.a)
        self.assertIsNot(other,self.window)

    def test_removed_schedule_post_cannot_be_prepared_again_from_manager(self):
        post_id=self.browser_draft('tool')
        scheduler=Mock()
        scheduler.store.list.return_value=[dict(post_id=post_id,state='removed')]
        with patch.object(self.owner,'get_page_scheduler',return_value=scheduler), \
                patch.object(self.owner,'submit_page_browser') as submit:
            self.window.preview()
            self.assertIn('đã có lịch',self.window.status.get())
            self.assertIsNone(self.window.preview_window)
            for operation in ('prepare','publish'):
                self.window.submit_post_browser(post_id,operation)
                self.assertIn('đã có lịch',self.window.browser_notice.get())
            submit.assert_not_called()

    def test_notes_and_drafts_persist_without_api_or_login(self):
        self.window.topic.set('Câu cá')
        self.window.notes.insert('1.0','Ghi chú')
        self.window.save_notes()
        post_id = self.draft()
        self.window.destroy()
        self.owner.tree.selection_set(self.p)
        reopened = self.owner.manage_page()
        reopened.withdraw()
        self.assertEqual(reopened.topic.get(),'Câu cá')
        self.assertEqual(reopened.notes.get('1.0','end').strip(),'Ghi chú')
        self.assertEqual(reopened.post_tree.get_children(),(post_id,))
        self.remote.request.assert_not_called()
        self.assertEqual(str(reopened.publish_button['state']),'disabled')

    def test_hidden_advanced_keeps_draft_and_ai_values_without_starting_work(self):
        self.browser_draft('tool')
        self.window.body.insert('end',' unsaved words')
        self.window.notes.insert('1.0','unsaved note')
        original_body=self.window.body.get('1.0','end')
        original_model=self.window.ai_panel.model.get()
        self.assertFalse(self.window.advanced_visible)
        self.assertNotIn(str(self.window.advanced_frame),self.window.tabs.tabs())
        self.window.open_advanced(self.window.connection_frame)
        self.assertEqual(self.window.advanced_tabs.select(),str(self.window.connection_frame))
        self.window.toggle_advanced()
        self.assertEqual(self.window.tabs.select(),str(self.window.setup_frame))
        self.assertEqual(self.window.body.get('1.0','end'),original_body)
        self.assertEqual(self.window.notes.get('1.0','end').strip(),'unsaved note')
        self.assertEqual(self.window.ai_panel.model.get(),original_model)
        self.assertIsNone(self.window.ai_panel.store.policy(self.a,self.p))
        self.remote.request.assert_not_called()
        self.thread.assert_not_called()
        self.browser.assert_not_called()

    def test_results_observe_original_page_without_replacing_unsaved_manual_draft(self):
        draft_id=self.browser_draft('tool')
        self.window.body.insert('end',' unsaved words')
        draft_body=self.window.body.get('1.0','end')
        published=self.service.store.save_draft(self.a,self.p,'Published A',delivery_mode='browser',browser_finish_mode='tool')
        self.service.store.transition(self.a,self.p,published['id'],'published','Synthetic completed receipt',expected={'draft'},
            external_url='https://www.facebook.com/synthetic/posts/77',external_id='100_77')
        foreign=self.service.store.save_draft(self.b,self.pb,'Other Page published',delivery_mode='browser',browser_finish_mode='tool')
        self.service.store.transition(self.b,self.pb,foreign['id'],'published','Other receipt',expected={'draft'},
            external_url='https://www.facebook.com/other/posts/88',external_id='200_88')
        self.window.results_filter.set('Đã đăng')
        self.window.refresh_page_run_status()
        self.assertEqual(self.window.results_tree.get_children(),(published['id'],))
        self.assertEqual(self.window.post_id,draft_id)
        self.assertEqual(self.window.body.get('1.0','end'),draft_body)
        self.assertIn('Người theo dõi: —',self.window.results_metrics.get())
        self.window.results_tree.selection_set(published['id'])
        self.window.select_result()
        with patch.object(self.window,'open_url') as open_url:
            self.window.open_result()
            open_url.assert_called_once_with('https://www.facebook.com/synthetic/posts/77')
        self.assertEqual(self.window.post_id,draft_id)
        self.remote.page_info.assert_not_called()
        self.remote.request.assert_not_called()
        self.thread.assert_not_called()

    def test_results_distinguish_verification_from_published_and_attention(self):
        rows = {}
        for state in ('processing','unknown','published'):
            row = self.service.store.save_draft(self.a,self.p,'Synthetic '+state,
                delivery_mode='browser',browser_finish_mode='tool')
            self.service.store.transition(self.a,self.p,row['id'],state,'Synthetic '+state,
                external_url='https://www.facebook.com/synthetic/posts/77' if state=='published' else '')
            rows[state] = row
        self.window.reload_data()
        self.assertIn('Đã xác nhận đăng: 1',self.window.results_summary.get())
        self.assertIn('Đã gửi, đang xác minh: 1',self.window.results_summary.get())
        self.assertIn('Cần kiểm tra: 1',self.window.results_summary.get())
        self.window.results_filter.set('Đang xác minh')
        self.window.refresh_results()
        self.assertEqual(self.window.results_tree.get_children(),(rows['processing']['id'],))
        self.assertEqual(self.window.results_tree.item(rows['processing']['id'])['values'][2],
                         'Đã gửi — đang xác minh')
        self.window.results_filter.set('Cần kiểm tra')
        self.window.refresh_results()
        self.assertEqual(self.window.results_tree.get_children(),(rows['unknown']['id'],))
        self.window.results_filter.set('Đã đăng')
        self.window.refresh_results()
        self.assertEqual(self.window.results_tree.get_children(),(rows['published']['id'],))
        self.remote.request.assert_not_called()
        self.thread.assert_not_called()

    def test_strategy_navigation_expands_only_local_connection_controls(self):
        self.window.open_strategy_settings()
        self.assertEqual(self.window.tabs.select(),str(self.window.content_panel))
        self.assertTrue(self.window.content_panel.strategy_open)
        self.thread.assert_not_called()
        self.remote.request.assert_not_called()

    def test_masked_key_captured_cleared_and_connection_updates_main_list(self):
        self.assertEqual(self.window.connection_entries[2]['show'],'•')
        self.window.secret.set('synthetic-key')
        self.window.connect_api()
        self.assertEqual(self.window.secret.get(),'')
        self.assertTrue(self.window.busy)
        self.assertFalse(self.thread.call_args.kwargs['daemon'])
        self.remote.verify_page.assert_not_called()
        self.run_worker()
        self.assertFalse(self.window.busy)
        self.assertIn('Page ID 100',self.window.connection_status.get())
        self.assertEqual(self.owner.tree.item(self.p)['values'][-1],'Chrome chưa mở · Đã kết nối đọc API')
        self.assertNotIn('synthetic-key',self.window.status.get())

    def test_preview_is_reviewable_and_does_not_post_until_final_button(self):
        self.connect()
        post_id = self.draft()
        self.remote.reset_mock()
        self.thread.reset_mock()
        self.window.preview()
        preview = next(c for c in self.window.winfo_children() if isinstance(c,tk.Toplevel))
        preview.withdraw()
        shell = preview.winfo_children()[0]
        label = next(c for c in shell.winfo_children() if isinstance(c,ttk.Label))
        self.assertIn('Verified Page',label['text'])
        self.assertIn('100',label['text'])
        self.remote.request.assert_not_called()
        self.thread.assert_not_called()
        next(c for c in shell.winfo_children() if isinstance(c,ttk.Button)).invoke()
        self.assertTrue(self.window.busy)
        self.run_worker()
        self.assertEqual(self.service.store.post(self.a,self.p,post_id)['state'],'published')
        self.remote.request.assert_called_once()
        self.assertEqual(str(self.window.publish_button['state']),'disabled')
        self.assertEqual(str(self.window.open_post_button['state']),'normal')

    def test_unsaved_changes_block_preview_and_refresh_keeps_edits(self):
        self.connect()
        self.draft()
        self.window.body.insert('end',' lời mới chưa lưu')
        self.window.notes.insert('end','Ghi chú chưa lưu')
        before = self.window.body.get('1.0','end')
        self.window.reload_data()
        self.assertEqual(self.window.body.get('1.0','end'),before)
        self.assertIn('Ghi chú chưa lưu',self.window.notes.get('1.0','end'))
        self.window.preview()
        self.assertIn('chưa lưu',self.window.status.get())
        self.assertFalse(any(isinstance(c,tk.Toplevel) for c in self.window.winfo_children()))
        self.remote.request.assert_not_called()

    def test_meta_setup_from_manager_queues_original_account_not_default_browser(self):
        a = BrowserSession(self.a,'Synthetic A',opened=True)
        b = BrowserSession(self.b,'Synthetic B',opened=True)
        self.owner.sessions = {self.a:a,self.b:b}
        self.owner.activate_account(self.b)
        for url in (META_APPS,META_EXPLORER):
            self.window.open_url(url)
            command = a.commands.get_nowait()
            self.assertEqual(command['url'],url)
            self.assertEqual(command['request_id'],self.window.browser_request)
            self.assertEqual(command['action'],'open_url')
            self.assertTrue(b.commands.empty())
            a.messages.put(('navigation',dict(request_id=command['request_id'],state='opened',message='Đã mở đúng tab')))
            self.owner.after_cancel(self.owner.timer)
            self.owner.poll()
            self.drain()
            self.assertIn('Đã mở đúng tab',self.window.browser_notice.get())
        self.assertEqual(self.owner.account_id,self.a)
        self.thread.assert_not_called()
        self.browser.assert_not_called()

    def test_meta_button_on_main_reuses_existing_teo_browser(self):
        a = BrowserSession(self.a,'Synthetic A',opened=True)
        self.owner.sessions[self.a] = a
        self.owner.refresh_buttons()
        self.owner.developers_button.invoke()
        self.assertEqual(a.commands.get_nowait(),META_APPS)
        self.thread.assert_not_called()
        self.browser.assert_not_called()

    def test_thread_failure_unlocks_and_no_automatic_retry(self):
        self.window.secret.set('synthetic-key')
        self.thread.side_effect = RuntimeError('fixture error')
        self.window.connect_api()
        self.assertFalse(self.window.busy)
        self.assertIn('fixture error',self.window.status.get())
        self.assertEqual(self.window.secret.get(),'')
        self.remote.verify_page.assert_not_called()

    def test_auto_refresh_is_opt_in_and_stops_on_expired_key(self):
        self.connect()
        self.assertFalse(self.window.auto_refresh.get())
        self.assertIsNone(self.window.auto_timer)
        self.window.auto_refresh.set(True)
        self.window.toggle_auto()
        self.assertIsNotNone(self.window.auto_timer)
        self.remote.recent_posts.side_effect = MetaError('expired',code=190)
        self.run_worker()
        self.assertFalse(self.window.auto_refresh.get())
        self.assertIsNone(self.window.auto_timer)
        self.assertIn('Cần kết nối lại',self.window.connection_status.get())
        self.assertEqual(self.remote.page_info.call_count,1)
        self.remote.request.assert_not_called()

    def test_close_cancels_worker_and_timers_without_deleting_data(self):
        self.connect()
        post_id = self.draft()
        self.window.refresh_meta()
        self.window.destroy()
        self.assertTrue(self.window.cancel.is_set())
        self.assertIsNone(self.window.timer)
        self.thread.call_args.kwargs['target']()
        self.assertEqual(self.service.store.post(self.a,self.p,post_id)['state'],'draft')
        self.assertTrue(self.service.store.connection(self.a,self.p))

    def test_removed_page_reports_failure_does_not_retarget(self):
        self.connect()
        self.owner.store.remove_page(self.a,self.p)
        self.window.refresh_meta()
        self.run_worker()
        self.assertFalse(self.window.busy)
        self.assertIn('Page không còn',self.window.status.get())
        self.remote.page_info.assert_not_called()

    def test_opening_browser_keeps_request_and_reports_it_in_page_window(self):
        session = BrowserSession(self.a,'Synthetic A',opened=False)
        self.owner.sessions[self.a] = session
        self.window.meta_button.invoke()
        request = session.commands.get_nowait()
        self.assertEqual(request['url'],META_APPS)
        self.assertIn('đã giữ yêu cầu',self.window.browser_notice.get())
        self.assertEqual(str(self.window.meta_button['state']),'disabled')
        self.assertEqual(str(self.window.explorer_button['state']),'disabled')
        self.window.open_url(META_EXPLORER)
        self.assertTrue(session.commands.empty())
        self.assertIn('không cần bấm thêm',self.window.browser_notice.get())

    def test_load_page_keeps_navigation_until_scan_finishes(self):
        session = BrowserSession(self.a,'Synthetic A',opened=True,loading_pages=True)
        session.status = 'Đang Load Page'
        self.owner.sessions[self.a] = session
        self.window.explorer_button.invoke()
        self.assertIn('Đang Load Page',self.window.browser_notice.get())
        self.assertEqual(session.status,'Đang Load Page')
        self.assertEqual(session.commands.get_nowait()['url'],META_EXPLORER)

    def test_closing_browser_rejects_visibly_instead_of_silent_drop(self):
        session = BrowserSession(self.a,'Synthetic A',opened=True)
        session.finished.set()
        self.owner.sessions[self.a] = session
        self.window.explorer_button.invoke()
        self.assertTrue(session.commands.empty())
        self.assertIsNone(self.window.browser_request)
        self.assertIn('đang đóng',self.window.browser_notice.get())
        self.assertEqual(str(self.window.meta_button['state']),'normal')

    def test_browser_launch_error_visible_in_page_not_only_hidden_main_window(self):
        self.window.meta_button.invoke()
        session = self.owner.sessions[self.a]
        session.messages.put(('error','Hồ sơ đang mở ở cửa sổ Tèo khác (fixture)'))
        self.owner.after_cancel(self.owner.timer)
        self.owner.poll()
        self.drain()
        self.assertIn('Hồ sơ đang mở',self.window.browser_notice.get())
        self.assertIsNone(self.window.browser_request)
        self.assertEqual(str(self.window.explorer_button['state']),'normal')

    def test_separate_page_windows_keep_correct_navigation_result(self):
        page = self.owner.store.save_page(self.a,'Page Two','facebook.com/pagetwo','101')['id']
        self.owner.reload()
        self.owner.tree.selection_set(page)
        other = self.owner.manage_page()
        other.withdraw()
        session = BrowserSession(self.a,'Synthetic A',opened=True)
        self.owner.sessions[self.a] = session
        self.window.open_url(META_APPS)
        other.open_url(META_EXPLORER)
        one,two = session.commands.get_nowait(),session.commands.get_nowait()
        for command,message in ((one,'Meta Apps đã mở'),(two,'Explorer bị lỗi mạng')):
            session.messages.put(('navigation',dict(request_id=command['request_id'],state='error' if command==two else 'opened',message=message)))
        self.owner.after_cancel(self.owner.timer)
        self.owner.poll()
        self.window.poll_browser()
        other.poll_browser()
        self.assertEqual(self.window.browser_notice.get(),'Meta Apps đã mở')
        self.assertEqual(other.browser_notice.get(),'Explorer bị lỗi mạng')

    def test_empty_key_has_immediate_instruction_no_thread_or_network(self):
        self.window.connect_api()
        self.assertIn('Chưa có khóa truy cập Page',self.window.status.get())
        self.assertIn('Mở Meta Developers',self.window.status.get())
        self.assertFalse(self.window.busy)
        self.thread.assert_not_called()
        self.remote.verify_page.assert_not_called()

    def test_api_error_message_not_overwritten_by_browser_navigation_feedback(self):
        session = BrowserSession(self.a,'Synthetic A',opened=True)
        self.owner.sessions[self.a] = session
        self.window.open_url(META_APPS)
        request = session.commands.get_nowait()
        self.window.status.set('Chưa có quyền API (fixture)')
        session.navigation[request['request_id']].update(state='opened',message='Đã mở Meta')
        self.window.poll_browser()
        self.assertEqual(self.window.status.get(),'Chưa có quyền API (fixture)')
        self.assertEqual(self.window.browser_notice.get(),'Đã mở Meta')

    def test_worker_with_old_command_protocol_times_out_visibly_without_resend(self):
        session = BrowserSession(self.a,'Synthetic A',opened=True)
        self.owner.sessions[self.a] = session
        self.window.open_url(META_APPS)
        session.commands.get_nowait()  # Simulate an older worker ignoring the command.
        with patch('teodub.page_manager_ui.time.monotonic',return_value=100):
            self.window.poll_browser()
        with patch('teodub.page_manager_ui.time.monotonic',return_value=121):
            self.window.poll_browser()
        self.assertIn('Chưa nhận được phản hồi',self.window.browser_notice.get())
        self.assertIsNone(self.window.browser_request)
        self.assertTrue(session.commands.empty())
        self.assertEqual(str(self.window.meta_button['state']),'normal')

    def test_old_main_window_module_has_actionable_restart_message(self):
        with patch.object(self.owner,'open_browser',side_effect=TypeError('unsupported keyword')):
            self.window.open_url(META_APPS)
        self.assertIn('phần điều khiển Chrome cũ',self.window.browser_notice.get())
        self.assertIsNone(self.window.browser_request)
        self.thread.assert_not_called()

    def test_browser_delivery_and_finish_persist_without_api_and_notes_are_shared(self):
        self.window.notes.insert('1.0','Ghi chú chung')
        self.window.save_notes()
        post_id = self.browser_draft('tool')
        self.assertEqual(self.service.store.post(self.a,self.p,post_id)['delivery_mode'],'browser')
        self.assertEqual(str(self.window.publish_button['state']),'normal')
        self.window.destroy()
        reopened = self.owner.manage_page()
        reopened.withdraw()
        reopened.post_tree.selection_set(post_id)
        reopened.select_post()
        self.assertEqual(reopened.delivery_mode.get(),DELIVERY_LABELS['browser'])
        self.assertEqual(reopened.browser_finish_mode.get(),FINISH_LABELS['tool'])
        self.assertEqual(reopened.notes.get('1.0','end').strip(),'Ghi chú chung')
        self.remote.verify_page.assert_not_called()
        self.remote.request.assert_not_called()
        self.browser.assert_not_called()

    def test_existing_api_draft_mode_and_unsaved_mode_switch_block_preview(self):
        post_id = self.draft()
        self.assertEqual(self.service.store.post(self.a,self.p,post_id).get('delivery_mode','api'),'api')
        self.window.delivery_mode.set(DELIVERY_LABELS['browser'])
        self.window.delivery_changed()
        self.window.preview()
        self.assertIn('Cách đăng đang sửa chưa lưu',self.window.status.get())
        self.assertIsNone(self.window.preview_window)
        self.remote.request.assert_not_called()

    def test_browser_initial_preview_is_frozen_and_queues_only_after_prepare_button(self):
        post_id = self.browser_draft('tool')
        session = self.post_session()
        def submit(account,page,post,operation):
            session.post_actions['request'] = dict(state='queued',message='Đang chờ Chrome')
            return 'request'
        self.owner.submit_page_browser = Mock(side_effect=submit)
        self.owner.activate_account(self.b)
        self.window.preview()
        modal = self.window.preview_window
        modal.withdraw()
        labels = '\n'.join(str(widget['text']) for widget in self.descendants(modal) if isinstance(widget,ttk.Label))
        self.assertIn('Synthetic A',labels)
        self.assertIn('Synthetic Page',labels)
        self.assertIn('100',labels)
        self.assertIn('facebook.com/synthetic',labels)
        self.assertEqual(str(self.window.delivery_picker['state']),'disabled')
        self.assertEqual(str(self.window.finish_picker['state']),'disabled')
        self.owner.submit_page_browser.assert_not_called()
        self.remote.request.assert_not_called()
        self.thread.assert_not_called()
        self.modal_button('Chuẩn bị trong Chrome').invoke()
        self.owner.submit_page_browser.assert_called_once_with(self.a,self.p,post_id,'prepare')
        self.assertTrue(self.window.busy)
        self.assertIsNone(self.window.preview_window)
        self.window.preview()
        self.assertIsNone(self.window.preview_window)
        self.window.submit_post_browser(post_id,'prepare')
        self.assertEqual(self.owner.submit_page_browser.call_count,1)

    def test_browser_prepare_completion_freezes_review_and_never_publishes_on_poll(self):
        post_id = self.browser_draft('tool')
        session = self.post_session()
        session.post_actions['one'] = dict(state='queued',message='Đang chờ')
        self.owner.submit_page_browser = Mock(return_value='one')
        self.window.submit_post_browser(post_id,'prepare')
        row = self.service.store.transition(self.a,self.p,post_id,'review','Đã chuẩn bị; hãy duyệt.',expected={'draft'})
        session.post_actions['one'].update(state='done',message='Đã chuẩn bị',result=row)
        self.window.poll_post_browser()
        self.assertFalse(self.window.busy)
        self.assertEqual(str(self.window.delivery_picker['state']),'disabled')
        self.assertEqual(str(self.window.finish_picker['state']),'disabled')
        self.assertEqual(str(self.window.choose_files_button['state']),'disabled')
        self.assertEqual(str(self.window.publish_button['state']),'normal')
        self.assertEqual(self.owner.submit_page_browser.call_count,1)
        self.remote.request.assert_not_called()

    def test_browser_actual_review_uses_durable_snapshot_and_separate_final_button(self):
        post_id = self.browser_draft('tool')
        row = self.service.store.transition(self.a,self.p,post_id,'review','Đã chuẩn bị',expected={'draft'})
        self.window.reload_data()
        publisher = Mock()
        publisher.review.return_value = self.prepared_snapshot(post_id,'tool')
        self.window.browser_publisher = Mock(return_value=publisher)
        session = self.post_session()
        session.post_actions['final'] = dict(state='queued',message='Đang chờ')
        self.owner.submit_page_browser = Mock(return_value='final')
        self.window.preview()
        self.window.preview_window.withdraw()
        publisher.review.assert_called_once_with(self.a,self.p,post_id)
        self.owner.submit_page_browser.assert_not_called()
        self.remote.request.assert_not_called()
        self.modal_button('Đăng bài đã duyệt').invoke()
        self.owner.submit_page_browser.assert_called_once_with(self.a,self.p,post_id,'publish')
        session.post_actions['final'].update(state='done',message='Đã bấm; chưa xác minh',
            result=dict(row,state='unknown',message='Đã bấm; chưa xác minh'))
        self.window.poll_post_browser()
        self.assertIn('Chưa rõ kết quả',self.window.status.get())
        self.assertEqual(self.service.store.post(self.a,self.p,post_id)['state'],'review')
        self.remote.request.assert_not_called()

    def test_manual_review_has_no_tool_publish_and_records_unverified_link_only(self):
        post_id = self.browser_draft()
        self.service.store.transition(self.a,self.p,post_id,'review','Đã chuẩn bị',expected={'draft'})
        self.window.reload_data()
        publisher = Mock()
        publisher.review.return_value = self.prepared_snapshot(post_id)
        self.window.browser_publisher = Mock(return_value=publisher)
        self.owner.submit_page_browser = Mock()
        self.window.preview()
        self.window.preview_window.withdraw()
        widgets = self.descendants(self.window.preview_window)
        buttons = [widget['text'] for widget in widgets if isinstance(widget,ttk.Button)]
        self.assertNotIn('Đăng bài đã duyệt',buttons)
        entry = next(widget for widget in widgets if isinstance(widget,ttk.Entry))
        entry.insert(0,'https://www.facebook.com/synthetic/posts/77')
        self.modal_button('Ghi link bài').invoke()
        publisher.record_manual_result.assert_called_once_with(self.a,self.p,post_id,'https://www.facebook.com/synthetic/posts/77')
        self.assertEqual(self.service.store.post(self.a,self.p,post_id)['state'],'review')
        self.owner.submit_page_browser.assert_not_called()
        self.remote.request.assert_not_called()

    def test_manual_open_focuses_original_prepared_form_using_review_operation(self):
        post_id = self.browser_draft()
        self.service.store.transition(self.a,self.p,post_id,'review','Đã chuẩn bị',expected={'draft'})
        self.window.reload_data()
        publisher = Mock()
        publisher.review.return_value = self.prepared_snapshot(post_id)
        self.window.browser_publisher = Mock(return_value=publisher)
        self.owner.submit_page_browser = Mock(return_value='focus')
        self.window.preview()
        self.window.preview_window.withdraw()
        self.modal_button('Xem trong Chrome').invoke()
        self.owner.submit_page_browser.assert_called_once_with(self.a,self.p,post_id,'review')
        self.browser.assert_not_called()

    def test_browser_error_login_or_identity_message_unlocks_without_retry(self):
        post_id = self.browser_draft()
        session = self.post_session()
        session.post_actions['error'] = dict(state='queued',message='Đang chờ')
        self.owner.submit_page_browser = Mock(return_value='error')
        self.window.submit_post_browser(post_id,'prepare')
        session.post_actions['error'].update(state='error',message='Bạn cần đăng nhập và đổi sang Page Synthetic Page trong Chrome.')
        self.window.poll_post_browser()
        self.assertFalse(self.window.busy)
        self.assertIn('đăng nhập',self.window.browser_notice.get())
        self.assertEqual(str(self.window.delivery_picker['state']),'readonly')
        self.window.poll_post_browser()
        self.assertEqual(self.owner.submit_page_browser.call_count,1)
        self.remote.request.assert_not_called()

    def test_stopped_browser_releases_pending_controls_without_resend(self):
        post_id = self.browser_draft()
        session = self.post_session()
        request_cancel = threading.Event()
        session.post_actions['stop'] = dict(state='running',message='Đang chuẩn bị',cancel=request_cancel)
        self.owner.submit_page_browser = Mock(return_value='stop')
        self.window.submit_post_browser(post_id,'prepare')
        session.finished.set()
        self.window.poll_post_browser()
        self.assertFalse(self.window.busy)
        self.assertIn('không tự gửi lại',self.window.browser_notice.get())
        self.assertTrue(request_cancel.is_set())
        self.assertEqual(self.owner.submit_page_browser.call_count,1)
        self.remote.request.assert_not_called()

    def test_browser_action_results_from_other_account_cannot_complete_this_window(self):
        post_id = self.browser_draft()
        session = self.post_session()
        session.post_actions['same'] = dict(state='queued',message='Tài khoản A đang chờ')
        other = BrowserSession(self.b,'Synthetic B',opened=True)
        other.post_actions = {'same':dict(state='done',message='Kết quả tài khoản B',result={})}
        self.owner.sessions[self.b] = other
        self.owner.submit_page_browser = Mock(return_value='same')
        self.window.submit_post_browser(post_id,'prepare')
        self.window.poll_post_browser()
        self.assertTrue(self.window.busy)
        self.assertEqual(self.window.browser_notice.get(),'Tài khoản A đang chờ')
        self.assertEqual(self.owner.submit_page_browser.call_count,1)

    def test_browser_submit_rejects_api_row_even_with_api_connected(self):
        self.connect()
        post_id = self.draft()
        self.owner.submit_page_browser = Mock()
        self.window.submit_post_browser(post_id,'publish')
        self.owner.submit_page_browser.assert_not_called()
        self.assertIn('dùng API Meta',self.window.browser_notice.get())
        self.remote.request.assert_not_called()

    def test_browser_preview_integrates_real_controller_queue_for_original_account(self):
        post_id = self.browser_draft()
        session = self.post_session()
        other = BrowserSession(self.b,'Synthetic B',opened=True)
        self.owner.sessions[self.b] = other
        self.owner.activate_account(self.b)
        self.window.preview()
        self.window.preview_window.withdraw()
        self.modal_button('Chuẩn bị trong Chrome').invoke()
        command = session.commands.get_nowait()
        self.assertEqual(command['action'],'page_post')
        self.assertEqual(command['operation'],'prepare')
        self.assertEqual(command['account_id'],self.a)
        self.assertEqual(command['page_key'],self.p)
        self.assertEqual(command['post_id'],post_id)
        self.assertEqual(command['request_id'],self.window.post_browser_request)
        self.assertTrue(other.commands.empty())
        self.assertEqual(self.owner.account_id,self.b)
        self.thread.assert_not_called()
        self.browser.assert_not_called()
        self.remote.request.assert_not_called()

    def test_browser_controller_refusal_is_visible_and_does_not_lock_window(self):
        post_id = self.browser_draft()
        session = self.post_session()
        session.finished.set()
        self.window.submit_post_browser(post_id,'prepare')
        self.assertIn('Chrome đang đóng',self.window.browser_notice.get())
        self.assertFalse(self.window.busy)
        self.assertIsNone(self.window.post_browser_request)
        self.assertTrue(session.commands.empty())
        self.remote.request.assert_not_called()

    def test_browser_unknown_result_offers_manual_recording_without_final_publish(self):
        post_id = self.browser_draft('tool')
        self.service.store.transition(self.a,self.p,post_id,'preparing','Chuẩn bị',expected={'draft'})
        self.service.store.save_browser_review(self.a,self.p,post_id,self.prepared_snapshot(post_id,'tool'))
        self.service.store.transition(self.a,self.p,post_id,'unknown','Chưa rõ kết quả',expected={'review'})
        self.window.reload_data()
        self.owner.submit_page_browser = Mock()
        self.window.check_result()
        self.window.preview_window.withdraw()
        widgets = self.descendants(self.window.preview_window)
        self.assertNotIn('Đăng bài đã duyệt',[widget['text'] for widget in widgets if isinstance(widget,ttk.Button)])
        entry = next(widget for widget in widgets if isinstance(widget,ttk.Entry))
        entry.insert(0,'https://www.facebook.com/synthetic/posts/77')
        self.modal_button('Ghi link bài').invoke()
        row = self.service.store.post(self.a,self.p,post_id)
        self.assertEqual(row['state'],'unknown')
        self.assertEqual(row['manual_url'],'https://www.facebook.com/synthetic/posts/77')
        self.assertEqual(row['external_url'],'')
        self.owner.submit_page_browser.assert_not_called()
        self.remote.request.assert_not_called()


if __name__ == '__main__':
    unittest.main()
