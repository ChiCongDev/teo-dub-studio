"""Browser-first controls, isolated temporary account data and hidden Tk only."""
from pathlib import Path
import tempfile
import tkinter as tk
from tkinter import ttk
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from teodub.facebook_ui import BrowserSession, FacebookTab
from teodub.page_activity import PageActivity
from teodub.page_manager_ui import DELIVERY_LABELS, FINISH_LABELS


class FakeBridge:
    def __init__(self):
        self.enabled = True
        self.tick = Mock()
        self.close = Mock()
        self.changed = []

    def set_enabled(self, enabled):
        self.enabled = bool(enabled)
        self.changed.append(self.enabled)


class PageBrowserPrimaryUITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.bridge = FakeBridge()
        self.enterContext(patch('teodub.facebook_ui.create_page_bridge', return_value=self.bridge))
        self.thread = self.enterContext(patch('teodub.facebook_ui.threading.Thread', return_value=Mock()))
        self.enterContext(patch.object(tk.Toplevel, 'grab_set', return_value=None))
        self.studio = SimpleNamespace(data_dir=Path(self.temp.name), closing=False, closed=False)
        self.owner = FacebookTab(ttk.Notebook(self.root), self.studio)
        self.a = self.owner.store.add_account('Synthetic A')['id']
        self.p = self.owner.store.save_page(self.a, 'Page A', 'facebook.com/a', '100')['id']
        self.b = self.owner.store.add_account('Synthetic B')['id']
        self.pb = self.owner.store.save_page(self.b, 'Page B', 'facebook.com/b', '200')['id']
        self.owner.reload()
        self.owner.activate_account(self.a)
        self.owner.tree.selection_set(self.p)

    def drain(self):
        self.owner.after_cancel(self.owner.timer)
        self.owner.poll()

    def manager(self):
        window = self.owner.manage_page()
        window.withdraw()
        return window

    def test_new_draft_uses_browser_and_tool_finish_without_api_or_browser_launch(self):
        window = self.manager()
        self.assertEqual(window.delivery_key(), 'browser')
        self.assertEqual(window.finish_key(), 'tool')
        self.assertEqual(window.tabs.index(window.tabs.select()), 0)
        self.assertIn('không cần', window.connection_status.get())
        window.body.insert('1.0', 'Nội dung thử được duyệt khi đăng')
        window.save_draft()
        post = window.store.post(self.a, self.p, window.post_id)
        self.assertEqual((post['delivery_mode'], post['browser_finish_mode']), ('browser', 'tool'))
        self.assertEqual(post['state'], 'draft')
        self.assertEqual(str(window.publish_button['state']), 'normal')
        self.assertEqual(self.owner.sessions, {})
        self.thread.assert_not_called()

    def test_api_draft_remains_api_then_new_post_returns_to_browser(self):
        activity = PageActivity(self.studio.data_dir)
        api = activity.save_draft(self.a, self.p, 'API legacy', delivery_mode='api')
        window = self.manager()
        window.post_tree.selection_set(api['id'])
        window.select_post()
        self.assertEqual(window.delivery_key(), 'api')
        window.new_post()
        self.assertEqual((window.delivery_key(), window.finish_key()), ('browser', 'tool'))
        self.assertEqual(activity.post(self.a, self.p, api['id'])['delivery_mode'], 'api')
        self.thread.assert_not_called()

    def test_visible_load_button_and_browser_status_do_not_claim_authenticated(self):
        session = BrowserSession(self.a, 'Synthetic A', opened=True)
        self.owner.sessions[self.a] = session
        self.owner.refresh_buttons()
        self.assertEqual(str(self.owner.load_pages_button['state']), 'normal')
        state = self.owner.tree.item(self.p, 'values')[-1]
        self.assertIn('Chrome đã mở', state)
        self.assertIn('API chưa kết nối', state)
        self.assertNotIn('đã đăng nhập', state)
        self.owner.load_pages_button.invoke()
        self.assertEqual(session.commands.get_nowait(), {'action': 'load_pages'})
        self.assertEqual(str(self.owner.load_pages_button['state']), 'disabled')

    def test_tracked_load_uses_original_account_keeps_selection_and_scope(self):
        session = BrowserSession(self.b, 'Synthetic B', opened=True)
        self.owner.sessions[self.b] = session
        request_id = 'a' * 32
        self.assertTrue(self.owner.load_pages(self.b, request_id=request_id))
        self.assertEqual(self.owner.account_id, self.a)
        self.assertEqual(self.owner.store.load()['selected_account_id'], self.a)
        command = session.commands.get_nowait()
        self.assertEqual((command['action'], command['request_id']), ('load_pages', request_id))
        self.assertIs(command['cancel_request'], session.page_load_requests[request_id]['cancel'])
        session.messages.put(('pages_progress', dict(request_id=request_id, message='Đang đọc Synthetic B')))
        self.drain()
        self.assertEqual(session.page_load_requests[request_id]['state'], 'running')
        session.messages.put(('pages_loaded', dict(request_id=request_id,
            items=[dict(name='New B', url='facebook.com/newb', page_id='201')],
            status='observed', message='Synthetic visible scan')))
        self.drain()
        result = session.page_load_requests[request_id]
        self.assertEqual((result['state'], result['result']['added']), ('done', 1))
        self.assertEqual(self.owner.account_id, self.a)
        self.assertEqual(len(self.owner.tree.get_children()), 1)
        self.assertTrue(any(row['page_id'] == '201' and row['account_id'] == self.b
                            for row in self.owner.store.load()['pages']))

    def test_load_missing_account_duplicate_foreign_reply_and_closed_browser_fail(self):
        self.assertFalse(self.owner.load_pages('f' * 32, request_id='a' * 32))
        self.assertEqual(self.owner.sessions, {})
        session = BrowserSession(self.b, 'Synthetic B', opened=True)
        self.owner.sessions[self.b] = session
        self.assertTrue(self.owner.load_pages(self.b, request_id='a' * 32))
        self.assertFalse(self.owner.load_pages(self.b, request_id='a' * 32))
        session.messages.put(('pages_loaded', dict(request_id='c' * 32,
            items=[dict(name='Foreign', url='facebook.com/foreign', page_id='999')], status='observed')))
        self.drain()
        self.assertTrue(session.loading_pages)
        self.assertEqual(session.page_load_requests['a' * 32]['state'], 'queued')
        session.messages.put(('done', 'closed'))
        self.drain()
        self.assertEqual(session.page_load_requests['a' * 32]['state'], 'error')
        self.assertFalse(any(row['page_id'] == '999' for row in self.owner.store.load()['pages']))

    def test_bridge_toggle_tick_close_has_no_browser_effect(self):
        self.assertTrue(self.owner.bridge_enabled.get())
        self.owner.bridge_button.invoke()
        self.assertEqual(self.bridge.changed, [False])
        self.assertIn('Đã tắt', self.owner.bridge_notice.get())
        self.drain()
        self.bridge.tick.assert_called_once()
        self.owner.destroy()
        self.bridge.close.assert_called_once()
        self.thread.assert_not_called()

    def test_bridge_preference_changed_in_another_window_updates_checkbox_without_writing(self):
        self.bridge.enabled = False
        self.drain()
        self.assertFalse(self.owner.bridge_enabled.get())
        self.assertIn('Đã tắt', self.owner.bridge_notice.get())
        self.assertEqual(self.bridge.changed, [])
        self.thread.assert_not_called()

    def test_manager_load_control_targets_bound_account_without_selection_change(self):
        window = self.manager()
        self.owner.activate_account(self.b)
        session = BrowserSession(self.a, 'Synthetic A', opened=True)
        self.owner.sessions[self.a] = session
        window.browser_load_button.invoke()
        self.assertEqual(self.owner.account_id, self.b)
        request = session.commands.get_nowait()
        self.assertEqual(request['action'], 'load_pages')
        self.assertEqual(request['request_id'], window.account_load_request)
        session.messages.put(('pages_error', dict(request_id=window.account_load_request,
                                                  message='Bạn cần đăng nhập Synthetic A')))
        self.drain()
        window.after_cancel(window.timer)
        window.poll()
        self.assertIn('đăng nhập Synthetic A', window.browser_notice.get())
        self.assertIsNone(window.account_load_request)

    def test_navigation_and_load_timeout_cancel_and_ignore_late_success(self):
        session = BrowserSession(self.a, 'Synthetic A', opened=True)
        self.owner.sessions[self.a] = session
        self.assertTrue(self.owner.open_browser('https://www.facebook.com/a', account_id=self.a, request_id='b'*32))
        nav = session.navigation['b'*32]
        nav['expires_at'] = 0
        self.assertTrue(self.owner.load_pages(self.a, request_id='c'*32))
        load = session.page_load_requests['c'*32]
        load['expires_at'] = 0
        self.drain()
        self.assertEqual((nav['state'], load['state']), ('error', 'error'))
        self.assertTrue(nav['cancel'].is_set())
        self.assertTrue(load['cancel'].is_set())
        self.assertFalse(session.loading_pages)
        session.messages.put(('navigation', dict(request_id='b'*32, state='opened', message='Late')))
        session.messages.put(('pages_loaded', dict(request_id='c'*32,
            items=[dict(name='Late', url='facebook.com/late', page_id='999')], status='observed')))
        self.drain()
        self.assertEqual((nav['state'], load['state']), ('error', 'error'))
        self.assertFalse(any(row['page_id'] == '999' for row in self.owner.store.load()['pages']))

    def test_text_schedule_dialog_is_passive_then_approval_uses_fixed_account_post(self):
        window = self.manager()
        window.body.insert('1.0', 'Bài chữ nguyên văn\n#ca')
        window.save_draft()
        post_id = window.post_id
        window.schedule_text_button.invoke()
        dialog = window.text_schedule_window
        dialog.withdraw()
        self.assertEqual(dialog.content.get('1.0', 'end').strip(), 'Bài chữ nguyên văn\n#ca')
        self.assertEqual(dialog.store.list(), [])
        self.owner.activate_account(self.b)
        dialog.approve()
        rows = dialog.store.list()
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]['account_id'], rows[0]['page_key'], rows[0]['post_id']), (self.a, self.p, post_id))
        self.assertEqual(rows[0]['state'], 'armed')
        dialog.approve()
        self.assertEqual(len(dialog.store.list()), 1)
        self.assertEqual(self.owner.sessions, {})
        self.thread.assert_not_called()

    def test_text_schedule_checks_unsaved_and_changed_text_without_creating_schedule(self):
        window = self.manager()
        window.body.insert('1.0', 'Bài được duyệt')
        window.save_draft()
        window.body.insert('end', ' nhưng chưa lưu')
        window.schedule_text_post()
        self.assertIsNone(window.text_schedule_window)
        self.assertIn('chưa lưu', window.status.get())
        window.body.delete('1.0', 'end')
        window.body.insert('1.0', 'Bài được duyệt')
        window.schedule_text_post()
        dialog = window.text_schedule_window
        dialog.withdraw()
        window.store.save_draft(self.a, self.p, 'Nội dung khác', post_id=window.post_id)
        dialog.approve()
        self.assertEqual(dialog.store.list(), [])
        self.assertIn('đã đổi', dialog.status.get())
        self.thread.assert_not_called()

    def test_text_schedule_retry_after_start_failure_reuses_saved_schedule(self):
        window = self.manager()
        window.body.insert('1.0', 'Bài chữ')
        window.save_draft()
        window.schedule_text_post()
        dialog = window.text_schedule_window
        dialog.withdraw()
        with patch.object(dialog.controller, 'start', side_effect=ValueError('Synthetic executor busy')):
            dialog.approve()
        saved_id = dialog.saved_id
        self.assertTrue(saved_id)
        self.assertEqual(len(dialog.store.list()), 1)
        self.assertEqual(str(dialog.time_entry['state']), 'disabled')
        self.assertEqual(dialog.approve_button['text'], 'Bật lịch đã lưu')
        dialog.approve()
        self.assertEqual(len(dialog.store.list()), 1)
        self.assertEqual(dialog.store.get(saved_id)['state'], 'armed')
        self.thread.assert_not_called()

    def test_manual_prepare_and_publish_cannot_compete_with_existing_schedule(self):
        window = self.manager()
        window.body.insert('1.0', 'Bài có lịch')
        window.save_draft()
        store = self.owner.get_page_scheduler().store
        row = store.create_post(self.a, self.p, window.post_id, __import__('time').time()+3600)
        with patch.object(self.owner, 'submit_page_browser') as submit:
            for state in ('scheduled', 'armed', 'paused', 'preparing', 'submitting', 'needs_attention'):
                store.update(row['id'], state=state)
                window.preview()
                self.assertIn('đã có lịch', window.status.get())
                self.assertIsNone(window.preview_window)
                for operation in ('prepare', 'publish'):
                    window.submit_post_browser(window.post_id, operation)
                    self.assertIn('đã có lịch', window.browser_notice.get())
            submit.assert_not_called()
        self.assertEqual(self.owner.sessions, {})


if __name__ == '__main__':
    unittest.main()
