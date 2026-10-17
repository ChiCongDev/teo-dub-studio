from pathlib import Path
import tempfile
import tkinter as tk
from tkinter import ttk
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from teodub.facebook_accounts import BUSINESS_SUITE, FACEBOOK_HOME, FACEBOOK_PAGES
from teodub.facebook_ui import FacebookTab


class FacebookUITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = tk.Tk()
        self.root.withdraw()
        self.studio = SimpleNamespace(data_dir=Path(self.temp.name), closing=False, closed=False,
            login_pending=False, campaign_busy=True, process=object())
        self.bridge = Mock(enabled=True)
        self.enterContext(patch('teodub.facebook_ui.create_page_bridge', return_value=self.bridge))
        notebook = ttk.Notebook(self.root)
        self.tab = FacebookTab(notebook, self.studio)
        notebook.add(self.tab, text='Quản lý Page')
        self.addCleanup(self.root.destroy)
        self.browser = self.enterContext(patch('teodub.facebook_browser.open_facebook_browser', return_value='finished'))
        self.thread = self.enterContext(patch('teodub.facebook_ui.threading.Thread', return_value=Mock()))

    def account(self, name='Tài khoản chính'):
        with patch('teodub.facebook_ui.simpledialog.askstring', return_value=name):
            self.assertTrue(self.tab.add_account())
        return self.tab.account_id

    def drain(self):
        self.tab.after_cancel(self.tab.timer)
        self.tab.poll()

    def test_mount_is_passive_and_empty_browser_button_creates_named_account(self):
        self.assertFalse(self.tab.store.path.exists())
        self.assertEqual(self.tab.tree.get_children(), ())
        self.assertEqual(self.tab.account_tree.get_children(), ())
        with patch('teodub.facebook_ui.simpledialog.askstring', return_value='Nick chính'):
            self.tab.facebook_button.invoke()
        self.assertEqual(len(self.tab.store.load()['accounts']), 1)
        self.assertEqual(len(self.tab.account_tree.get_children()), 1)
        self.thread.assert_called_once()
        self.assertFalse(self.thread.call_args.kwargs['daemon'])
        self.browser.assert_not_called()
        self.assertFalse(self.studio.login_pending)

    def test_outer_page_screen_hides_advanced_controls_without_changing_services(self):
        self.assertFalse(self.tab.advanced_visible)
        self.assertEqual(tuple(self.tab.tree['displaycolumns']),('name',))
        self.assertEqual(self.tab.page_link_form.winfo_manager(),'')
        self.assertEqual(self.tab.account_picker.winfo_manager(),'')
        for widget in (self.tab.business_button,self.tab.developers_button,self.tab.close_button,
                       self.tab.bridge_button,self.tab.edit_page_button,self.tab.remove_page_button):
            self.assertEqual(widget.winfo_manager(),'')
        self.assertEqual(self.tab.manage_page_button.winfo_manager(),'pack')
        self.assertEqual(self.tab.account_tree.winfo_manager(),'grid')
        self.assertTrue(self.tab.bridge_enabled.get())
        self.assertFalse(self.tab.store.path.exists())
        self.bridge.set_enabled.assert_not_called()
        self.bridge.close.assert_not_called()
        self.thread.assert_not_called()

    def test_advanced_and_link_visibility_preserve_drafts_selection_and_browser_session(self):
        from teodub.facebook_ui import BrowserSession
        account=self.account()
        page=self.tab.store.save_page(account,'Page nguyên vẹn','facebook.com/page','100')
        self.tab.reload()
        self.tab.tree.selection_set(page['id'])
        self.tab.page_link_name.set('Tên chưa lưu')
        self.tab.page_link_url.set('https://www.facebook.com/new-page')
        session=BrowserSession(account,'Tài khoản chính',opened=True)
        self.tab.sessions[account]=session
        before=self.tab.store.load()
        self.tab.advanced_button.invoke()
        self.assertEqual(tuple(self.tab.tree['displaycolumns']),('name','page_id','url','state'))
        self.assertEqual(self.tab.bridge_button.winfo_manager(),'pack')
        self.assertEqual(self.tab.account_picker.winfo_manager(),'grid')
        self.tab.page_link_button.invoke()
        self.assertEqual(self.tab.page_link_form.winfo_manager(),'grid')
        self.tab.advanced_button.invoke()
        self.tab.page_link_button.invoke()
        self.assertEqual(self.tab.page_link_form.winfo_manager(),'')
        self.assertEqual(self.tab.tree.selection(),(page['id'],))
        self.assertEqual(self.tab.page_link_name.get(),'Tên chưa lưu')
        self.assertEqual(self.tab.page_link_url.get(),'https://www.facebook.com/new-page')
        self.assertEqual(self.tab.store.load(),before)
        self.assertIs(self.tab.sessions[account],session)
        self.assertFalse(session.finished.is_set())
        self.assertFalse(session.cancel.is_set())
        self.assertTrue(self.tab.bridge_enabled.get())
        self.bridge.set_enabled.assert_not_called()
        self.bridge.close.assert_not_called()
        self.thread.assert_not_called()

    def test_browser_can_open_during_video_work_and_same_session_accepts_pages(self):
        account_id = self.account()
        page = self.tab.store.save_page(account_id, 'Page một', 'facebook.com/mot')
        self.tab.reload()
        self.tab.tree.selection_set(page['id'])
        self.tab.open_facebook()
        self.assertEqual(str(self.tab.account_picker['state']), 'readonly')
        self.assertEqual(str(self.tab.facebook_button['state']), 'disabled')
        self.assertEqual(str(self.tab.close_button['state']), 'normal')
        session = self.tab.sessions[account_id]
        session.messages.put(('opened', 'Chrome đang mở'))
        self.drain()
        self.tab.open_page()
        self.tab.open_business()
        command = session.commands.get_nowait()
        self.assertEqual((command['action'],command['url'],command['account_id'],command['page_key']),
                         ('open_page',page['url'],account_id,page['id']))
        self.assertEqual(session.commands.get_nowait(), BUSINESS_SUITE)
        self.thread.assert_called_once()
        self.thread.call_args.kwargs['target']()
        self.assertEqual(self.browser.call_args.args[0], self.tab.store.profile_directory(account_id))
        self.assertEqual(self.browser.call_args.kwargs['url'], FACEBOOK_HOME)
        self.assertFalse(self.studio.login_pending)
        self.assertTrue(self.studio.campaign_busy)
        self.assertIsNotNone(self.studio.process)

    def test_finish_waits_for_worker_close_then_reopen_uses_same_profile(self):
        account = self.account()
        self.tab.open_facebook()
        session = self.tab.sessions[account]
        self.tab.finish_browser()
        self.assertTrue(self.tab.browser_pending)
        self.assertTrue(session.finished.is_set())
        self.assertEqual(str(self.tab.close_button['state']), 'disabled')
        self.assertEqual(str(self.tab.account_picker['state']), 'readonly')
        session.messages.put(('done', 'finished'))
        self.drain()
        self.assertFalse(self.tab.browser_pending)
        self.assertEqual(str(self.tab.account_picker['state']), 'readonly')
        self.assertIn('chưa tự xác nhận', self.tab.status.get())
        profile = self.tab.store.profile_directory(account)
        self.tab.open_business()
        self.thread.call_args.kwargs['target']()
        self.assertEqual(self.browser.call_args.args[0], profile)
        self.assertEqual(self.browser.call_args.kwargs['url'], BUSINESS_SUITE)

    def test_background_error_is_displayed_and_controls_unlock_without_retry(self):
        self.account()
        self.tab.open_facebook()
        self.browser.side_effect = ValueError('Không có Chrome (fixture)')
        self.thread.call_args.kwargs['target']()
        self.drain()
        self.assertIn('Không có Chrome', self.tab.status.get())
        self.assertFalse(self.tab.browser_pending)
        self.assertEqual(str(self.tab.facebook_button['state']), 'normal')
        self.thread.assert_called_once()
        self.browser.assert_called_once()

    def test_destroy_requests_browser_cleanup_and_cancels_timer(self):
        account = self.account()
        self.tab.open_facebook()
        session = self.tab.sessions[account]
        self.tab.destroy()
        self.assertTrue(session.cancel.is_set())
        self.assertTrue(session.finished.is_set())
        self.assertIsNone(self.tab.timer)
        self.assertTrue(self.tab.destroyed)

    def test_selected_account_filters_many_pages_and_restores_after_reopen(self):
        first = self.account('Nick một')
        self.tab.store.save_page(first, 'Page 1', 'facebook.com/one')
        self.tab.store.save_page(first, 'Page 2', 'facebook.com/two')
        second = self.account('Nick hai')
        self.tab.store.save_page(second, 'Page 3', 'facebook.com/three')
        self.tab.reload()
        self.assertEqual(len(self.tab.tree.get_children()), 1)
        self.assertEqual(int(self.tab.account_tree.item(first)['values'][1]), 2)
        self.assertEqual(int(self.tab.account_tree.item(second)['values'][1]), 1)
        self.tab.account_picker.current(0)
        self.tab.select_account()
        self.assertEqual(len(self.tab.tree.get_children()), 2)
        self.assertEqual(self.tab.store.load()['selected_account_id'], first)
        other = FacebookTab(self.tab.master, self.studio)
        self.assertEqual(other.account_id, first)
        self.assertEqual(len(other.tree.get_children()), 2)
        self.assertEqual(len(other.account_tree.get_children()), 2)
        self.assertEqual(other.account_tree.selection(), (first,))
        for row in other.tree.get_children():
            self.assertEqual(other.tree.item(row)['values'][-1], 'Chrome chưa mở · API chưa kết nối')
        self.browser.assert_not_called()
        self.thread.assert_not_called()

    def test_page_dialog_saves_then_edits_local_page_without_auth_claim(self):
        account = self.account()
        def fill_dialog(name, url):
            window = next(child for child in self.tab.winfo_children() if isinstance(child, tk.Toplevel))
            form = window.winfo_children()[0]
            entries = [child for child in form.winfo_children() if isinstance(child, ttk.Entry)]
            entries[0].delete(0, 'end')
            entries[0].insert(0, name)
            entries[1].delete(0, 'end')
            entries[1].insert(0, url)
            buttons = next(child for child in form.winfo_children() if isinstance(child, ttk.Frame))
            next(button for button in buttons.winfo_children() if button['text'] == 'Lưu Page').invoke()
        self.root.after(0, lambda: fill_dialog('Câu cá', 'facebook.com/cauca'))
        self.tab.add_page()
        page = self.tab.selected_page()
        self.assertEqual(page['name'], 'Câu cá')
        self.assertEqual(page['account_id'], account)
        self.root.after(0, lambda: fill_dialog('Đổi tên Page', 'facebook.com/cauca'))
        self.tab.edit_page()
        self.assertEqual(self.tab.selected_page()['name'], 'Đổi tên Page')
        self.assertEqual(len(self.tab.store.load()['pages']), 1)
        self.browser.assert_not_called()

    def test_remove_page_only_removes_local_record_and_cancel_preserves_it(self):
        account = self.account()
        record = self.tab.store.save_page(account, 'Page', 'facebook.com/page')
        profile = self.tab.store.profile_directory(account)
        profile.mkdir(parents=True)
        marker = profile/'fixture'
        marker.write_text('keep')
        self.tab.reload()
        self.tab.tree.selection_set(record['id'])
        with patch('teodub.facebook_ui.messagebox.askyesno', return_value=False):
            self.tab.remove_page()
        self.assertEqual(len(self.tab.store.load()['pages']), 1)
        with patch('teodub.facebook_ui.messagebox.askyesno', return_value=True):
            self.tab.remove_page()
        self.assertEqual(self.tab.store.load()['pages'], [])
        self.assertEqual(marker.read_text(), 'keep')
        self.browser.assert_not_called()

    def test_two_accounts_open_independently_and_selection_cannot_cross_wire_workers(self):
        first = self.account('Nick A')
        self.tab.open_facebook()
        first_target = self.thread.call_args.kwargs['target']
        first_session = self.tab.sessions[first]
        # Add/change selection while A is launching, before A's worker has run.
        second = self.account('Nick B')
        self.tab.open_business()
        second_target = self.thread.call_args.kwargs['target']
        second_session = self.tab.sessions[second]
        self.assertEqual(self.thread.call_count, 2)
        self.assertIsNot(first_session.cancel, second_session.cancel)
        self.assertIsNot(first_session.finished, second_session.finished)
        self.assertIsNot(first_session.commands, second_session.commands)
        self.assertIsNot(first_session.messages, second_session.messages)
        first_target()
        first_call = self.browser.call_args
        self.assertEqual(first_call.args[0], self.tab.store.profile_directory(first))
        self.assertIs(first_call.kwargs['cancel'], first_session.cancel)
        self.assertIs(first_call.kwargs['commands'], first_session.commands)
        self.assertTrue(second_session.messages.empty())
        second_target()
        second_call = self.browser.call_args
        self.assertEqual(second_call.args[0], self.tab.store.profile_directory(second))
        self.assertIs(second_call.kwargs['cancel'], second_session.cancel)
        self.assertEqual(second_call.kwargs['url'], BUSINESS_SUITE)

    def test_closing_selected_browser_only_signals_that_account_and_other_can_stay_open(self):
        first = self.account('Nick A')
        self.tab.open_facebook()
        second = self.account('Nick B')
        self.tab.open_facebook()
        one, two = self.tab.sessions[first], self.tab.sessions[second]
        one.messages.put(('opened', 'A đang mở'))
        two.messages.put(('opened', 'B đang mở'))
        self.drain()
        self.assertIn('Nick A (đã mở)', self.tab.browser_summary.get())
        self.assertIn('Nick B (đã mở)', self.tab.browser_summary.get())
        self.assertEqual(self.tab.account_tree.item(first)['values'][2], 'Đã mở')
        self.assertEqual(self.tab.account_tree.item(second)['values'][2], 'Đã mở')
        self.tab.finish_browser()
        self.assertTrue(two.finished.is_set())
        self.assertEqual(self.tab.account_tree.item(second)['values'][2], 'Đang đóng')
        self.assertFalse(one.finished.is_set())
        self.assertFalse(one.cancel.is_set())
        # A can be selected and used even while B's context is flushing.
        self.tab.account_picker.current(0)
        self.tab.select_account()
        self.assertEqual(self.tab.status.get(), 'A đang mở')
        self.tab.open_business()
        self.assertEqual(one.commands.get_nowait(), BUSINESS_SUITE)
        self.assertTrue(two.commands.empty())
        two.messages.put(('done', 'finished'))
        self.drain()
        self.assertTrue(one.pending)
        self.assertFalse(two.pending)
        self.assertEqual(self.tab.account_tree.item(second)['values'][2], 'Đã đóng')
        self.assertEqual(self.tab.status.get(), 'Đang mở trang trong phiên Chrome của Nick A…')
        self.tab.destroy()
        self.assertTrue(one.cancel.is_set())
        self.assertTrue(two.cancel.is_set())

    def test_inactive_account_error_is_retained_without_replacing_current_account_status(self):
        first = self.account('Nick A')
        self.tab.open_facebook()
        second = self.account('Nick B')
        self.tab.open_facebook()
        self.tab.sessions[first].messages.put(('error', 'Fixture hồ sơ A đang khóa'))
        self.tab.sessions[second].messages.put(('opened', 'B vẫn đang mở'))
        self.drain()
        self.assertEqual(self.tab.status.get(), 'B vẫn đang mở')
        self.assertTrue(self.tab.sessions[second].pending)
        self.assertIn('Nick A', self.tab.browser_summary.get())
        self.assertIn('Chọn tài khoản để xem lỗi', self.tab.browser_summary.get())
        self.assertEqual(self.tab.account_tree.item(first)['values'][2], 'Có lỗi')
        self.assertEqual(self.tab.account_tree.item(second)['values'][2], 'Đã mở')
        self.tab.account_picker.current(0)
        self.tab.select_account()
        self.assertIn('hồ sơ A đang khóa', self.tab.status.get())
        self.assertEqual(str(self.tab.facebook_button['state']), 'normal')
        self.assertEqual(str(self.tab.close_button['state']), 'disabled')

    def test_repeated_open_of_same_account_keeps_one_session_and_reopen_creates_fresh_signals(self):
        account = self.account()
        self.tab.open_facebook()
        original = self.tab.sessions[account]
        self.tab.open_facebook()
        self.thread.assert_called_once()
        original.messages.put(('opened', 'Chrome đang mở'))
        self.drain()
        self.tab.open_business()
        self.thread.assert_called_once()
        self.assertEqual(original.commands.get_nowait(), BUSINESS_SUITE)
        self.tab.finish_browser()
        original.messages.put(('done', 'finished'))
        self.drain()
        self.tab.open_facebook()
        renewed = self.tab.sessions[account]
        self.assertIsNot(renewed, original)
        self.assertTrue(original.finished.is_set())
        self.assertFalse(renewed.finished.is_set())
        self.assertEqual(self.thread.call_count, 2)

    def test_refresh_while_browsers_are_open_keeps_profile_and_live_session_bindings(self):
        account = self.account()
        self.tab.open_facebook()
        session = self.tab.sessions[account]
        profile = self.tab.store.profile_directory(account)
        self.tab.store.save_page(account, 'Page ngoài', 'facebook.com/ngoai')
        self.tab.reload_button.invoke()
        self.assertEqual(len(self.tab.tree.get_children()), 1)
        self.assertIs(self.tab.sessions[account], session)
        self.assertEqual(self.tab.store.profile_directory(account), profile)
        self.assertTrue(self.tab.browser_pending)

    def test_account_list_selection_filters_pages_and_persists_without_opening_browser(self):
        first = self.account('Nick A')
        first_page = self.tab.store.save_page(first, 'Page A', 'facebook.com/pageA')
        second = self.account('Nick B')
        self.tab.store.save_page(second, 'Page B', 'facebook.com/pageB')
        self.tab.reload()
        self.tab.account_tree.selection_set(first)
        with patch.object(self.tab.store, 'select_account', wraps=self.tab.store.select_account) as select:
            self.tab.select_account_from_list()
            self.root.update()
            select.assert_called_once_with(first)
        self.assertEqual(self.tab.account_id, first)
        self.assertEqual(self.tab.account_picker.current(), 0)
        self.assertEqual(self.tab.account_label.get(), 'Nick A')
        self.assertEqual(self.tab.account_tree.selection(), (first,))
        self.assertEqual(self.tab.tree.get_children(), (first_page['id'],))
        self.assertEqual(self.tab.store.load()['selected_account_id'], first)
        self.thread.assert_not_called()
        self.browser.assert_not_called()

    def test_dropdown_and_list_stay_synced_and_rename_keeps_profile_and_row_id(self):
        first = self.account('Nick A')
        self.account('Nick B')
        self.tab.account_picker.current(0)
        self.tab.select_account()
        self.root.update()
        self.assertEqual(self.tab.account_tree.selection(), (first,))
        profile = self.tab.store.profile_directory(first)
        with patch('teodub.facebook_ui.simpledialog.askstring', return_value='Tên mới'):
            self.tab.rename_account()
        self.root.update()
        self.assertEqual(self.tab.account_tree.item(first)['values'][0], 'Tên mới')
        self.assertEqual(self.tab.account_label.get(), 'Tên mới')
        self.assertEqual(self.tab.store.profile_directory(first), profile)
        self.assertEqual(len(self.tab.account_tree.get_children()), 2)
        self.thread.assert_not_called()

    def test_double_click_account_row_opens_that_profile_after_changing_selection(self):
        first = self.account('Nick A')
        second = self.account('Nick B')
        self.assertEqual(self.tab.account_id, second)
        with patch.object(self.tab.account_tree, 'identify_row', return_value=first):
            self.tab.open_selected_account(SimpleNamespace(y=20))
        self.assertEqual(self.tab.account_id, first)
        self.assertEqual(self.tab.account_tree.selection(), (first,))
        self.thread.call_args.kwargs['target']()
        self.assertEqual(self.browser.call_args.args[0], self.tab.store.profile_directory(first))
        self.assertNotIn(second, self.tab.sessions)

    def test_closed_browser_does_not_claim_login_or_discover_pages_and_empty_hint_explains_next_step(self):
        account = self.account()
        self.tab.open_facebook()
        self.assertEqual(self.tab.account_tree.item(account)['values'][2], 'Đang mở…')
        session = self.tab.sessions[account]
        session.messages.put(('opened', 'Chrome đang mở'))
        self.drain()
        session.messages.put(('done', 'finished'))
        self.drain()
        self.assertEqual(self.tab.account_tree.item(account)['values'][2], 'Đã đóng')
        self.assertEqual(int(self.tab.account_tree.item(account)['values'][1]), 0)
        self.assertEqual(self.tab.tree.get_children(), ())
        self.assertIn('Bấm Thêm Page bằng link', self.tab.page_hint.get())
        self.assertIn('Load Page', self.tab.page_hint.get())
        self.assertEqual(self.tab.page_hint_label.winfo_manager(), 'grid')
        self.tab.store.save_page(account, 'Page nhập tay', 'facebook.com/page')
        self.tab.reload()
        self.assertEqual(int(self.tab.account_tree.item(account)['values'][1]), 1)
        self.assertEqual(self.tab.page_hint_label.winfo_manager(), '')

    def test_failed_account_selection_restores_current_row_and_never_opens_wrong_browser(self):
        first = self.account('Nick A')
        second = self.account('Nick B')
        self.tab.account_picker.current(0)
        self.tab.select_account()
        self.tab.account_tree.selection_set(second)
        with patch.object(self.tab.store, 'select_account', side_effect=ValueError('Fixture danh sách đang khóa')):
            self.tab.open_selected_account()
            self.root.update()
        self.assertEqual(self.tab.account_id, first)
        self.assertEqual(self.tab.account_tree.selection(), (first,))
        self.assertIn('danh sách đang khóa', self.tab.status.get())
        with patch.object(self.tab.store, 'select_account', side_effect=ValueError('Fixture danh sách đang khóa')):
            self.tab.account_picker.current(1)
            self.tab.select_account()
            self.root.update()
        self.assertEqual(self.tab.account_id, first)
        self.assertEqual(self.tab.account_label.get(), 'Nick A')
        self.assertEqual(self.tab.account_picker.current(), 0)
        self.thread.assert_not_called()

    def test_periodic_refresh_preserves_user_selection_before_its_event_is_handled(self):
        first = self.account('Nick A')
        second = self.account('Nick B')
        self.tab.account_picker.current(0)
        self.tab.select_account()
        self.tab.account_tree.selection_set(second)
        self.tab.refresh_buttons()
        self.assertEqual(self.tab.account_tree.selection(), (second,))
        self.assertEqual(self.tab.account_id, first)
        self.tab.select_account_from_list()
        self.root.update()
        self.assertEqual(self.tab.account_id, second)
        self.thread.assert_not_called()

    def test_right_click_targets_clicked_account_and_launches_one_saved_profile(self):
        first = self.account('Nick A')
        second = self.account('Nick B')
        with patch.object(self.tab.account_tree, 'identify_row', return_value=first), \
                patch.object(self.tab.account_menu, 'tk_popup'), patch.object(self.tab.account_menu, 'grab_release'):
            self.tab.show_account_menu(SimpleNamespace(y=20, x_root=100, y_root=200))
        self.assertEqual(self.tab.account_id, first)
        self.assertEqual(self.tab.context_account_id, first)
        self.tab.activate_account(second)
        self.tab.account_menu.invoke('Load Page')
        self.tab.account_menu.invoke('Load Page')
        self.assertEqual(self.tab.account_id, first)
        self.assertNotIn(second, self.tab.sessions)
        session = self.tab.sessions[first]
        self.assertTrue(session.loading_pages)
        self.assertEqual(self.tab.account_tree.item(first)['values'][2], 'Đang Load Page')
        self.assertEqual(session.commands.get_nowait(), dict(action='load_pages'))
        self.assertTrue(session.commands.empty())
        self.thread.assert_called_once()
        self.thread.call_args.kwargs['target']()
        self.assertEqual(self.browser.call_args.args[0], self.tab.store.profile_directory(first))
        self.assertEqual(self.browser.call_args.kwargs['url'], FACEBOOK_PAGES)

    def test_load_page_queues_into_opening_or_open_session_and_blocks_duplicates(self):
        for opened in (False, True):
            account = self.account('Nick '+str(opened))
            self.tab.open_facebook()
            session = self.tab.sessions[account]
            if opened:
                session.messages.put(('opened', 'Chrome đang mở'))
                self.drain()
            threads = self.thread.call_count
            self.tab.load_pages()
            self.tab.load_pages()
            self.tab.open_business()  # No competing navigation while scanning.
            self.assertEqual(self.thread.call_count, threads)
            self.assertEqual(session.commands.get_nowait(), dict(action='load_pages'))
            self.assertTrue(session.commands.empty())
            self.assertEqual(str(self.tab.business_button['state']), 'disabled')
            self.assertEqual(str(self.tab.close_button['state']), 'normal')
            with patch.object(self.tab.account_tree, 'identify_row', return_value=account), \
                    patch.object(self.tab.account_menu, 'tk_popup'), patch.object(self.tab.account_menu, 'grab_release'):
                self.tab.show_account_menu(SimpleNamespace(y=20, x_root=100, y_root=200))
            self.assertEqual(self.tab.account_menu.entrycget('Load Page', 'state'), 'disabled')

    def test_load_result_stays_with_its_account_when_user_switches_and_reopens_from_disk(self):
        first = self.account('Nick A')
        old = self.tab.store.save_page(first, 'Page đã lưu', 'facebook.com/old')
        self.tab.load_pages()
        session = self.tab.sessions[first]
        second = self.account('Nick B')
        self.tab.store.save_page(second, 'Page B', 'facebook.com/b')
        self.tab.reload()
        result = dict(items=[dict(name='Kỹ Năng Câu Cá', url='facebook.com/fishing'),
                             dict(name='Nhất Tâm Review', url='facebook.com/review')],
                      message='Đã đọc 2 Page; có thể chưa đủ.', status='partial')
        session.messages.put(('opened', 'Chrome đang mở'))
        session.messages.put(('pages_loaded', result))
        self.drain()
        self.assertEqual(self.tab.account_id, second)
        self.assertEqual(self.tab.store.load()['selected_account_id'], second)
        self.assertEqual(len(self.tab.tree.get_children()), 1)
        self.assertEqual(int(self.tab.account_tree.item(first)['values'][1]), 3)
        self.assertTrue(session.pending)
        self.assertFalse(session.loading_pages)
        self.tab.activate_account(first)
        self.assertIn('thêm 2', self.tab.status.get())
        self.assertIn('chưa đủ', self.tab.status.get())
        self.assertIn(old['id'], self.tab.tree.get_children())
        other = FacebookTab(self.tab.master, self.studio)
        self.assertEqual(other.account_id, first)
        self.assertEqual(len(other.tree.get_children()), 3)
        self.assertIn('Lượt Load Page đã lưu', other.status.get())
        for row in other.tree.get_children():
            self.assertEqual(other.tree.item(row)['values'][-1], 'Chrome chưa mở · API chưa kết nối')
        self.thread.assert_called_once()

    def test_load_error_and_save_error_preserve_pages_and_unlock_retry_without_relaunch(self):
        account = self.account()
        old = self.tab.store.save_page(account, 'Page cũ', 'facebook.com/old')
        self.tab.load_pages()
        session = self.tab.sessions[account]
        session.messages.put(('opened', 'Chrome đang mở'))
        session.messages.put(('pages_error', 'Giao diện khác ảnh mẫu; danh sách cũ được giữ.'))
        self.drain()
        self.assertFalse(session.loading_pages)
        self.assertTrue(session.pending)
        self.assertIn('Giao diện khác', self.tab.status.get())
        self.assertEqual(self.tab.store.load()['pages'], [old])
        self.tab.load_pages()
        session.messages.put(('pages_loaded', dict(items=[dict(name='Mới', url='facebook.com/new')],
                                                   message='Đã đọc 1', status='observed')))
        with patch.object(self.tab.store, 'merge_discovered_pages', side_effect=ValueError('Fixture danh sách đang khóa')):
            self.drain()
        self.assertIn('Chưa lưu được', self.tab.status.get())
        self.assertEqual(self.tab.store.load()['pages'], [old])
        self.assertTrue(session.pending)
        self.assertFalse(session.loading_pages)
        self.assertEqual(str(self.tab.business_button['state']), 'normal')
        self.thread.assert_called_once()

    def test_close_during_load_preserves_old_data_and_requires_explicit_retry(self):
        account = self.account()
        old = self.tab.store.save_page(account, 'Page cũ', 'facebook.com/old')
        self.tab.load_pages()
        session = self.tab.sessions[account]
        self.tab.finish_browser()
        self.tab.load_pages()
        self.thread.assert_called_once()
        self.assertEqual(session.commands.qsize(), 1)
        session.messages.put(('done', 'finished'))
        self.drain()
        self.assertFalse(session.loading_pages)
        self.assertFalse(session.pending)
        self.assertEqual(self.tab.store.load()['pages'], [old])
        self.assertIn('Load Page bị gián đoạn', self.tab.status.get())
        self.tab.load_pages()
        self.assertEqual(self.thread.call_count, 2)

    def test_inline_link_save_works_without_browser_and_persists_on_reopen(self):
        self.assertEqual(str(self.tab.save_page_link_button['state']), 'disabled')
        account = self.account()
        self.tab.page_link_name.set('Kỹ Năng Câu Cá')
        self.tab.page_link_url.set('https://facebook.com/profile.php?id=123&ref=bookmarks')
        self.tab.save_page_link_button.invoke()
        row = self.tab.selected_page()
        self.assertEqual((row['name'], row['account_id'], row['page_id']), ('Kỹ Năng Câu Cá', account, '123'))
        self.assertEqual(self.tab.page_link_url.get(), '')
        self.assertEqual(int(self.tab.account_tree.item(account)['values'][1]), 1)
        self.assertIn('Đã lưu', self.tab.page_link_notice.get())
        self.assertEqual(str(self.tab.open_page_button['state']), 'normal')
        other = FacebookTab(self.tab.master, self.studio)
        self.assertEqual(other.tree.get_children(), (row['id'],))
        self.thread.assert_not_called()
        self.browser.assert_not_called()

    def test_inline_link_save_then_open_uses_same_logged_in_browser_session(self):
        account = self.account()
        self.tab.open_facebook()
        session = self.tab.sessions[account]
        session.messages.put(('opened', 'Chrome đang mở'))
        self.drain()
        self.tab.page_link_url.set('facebook.com/KyNangCauCa')
        self.tab.save_page_link_button.invoke()
        self.assertTrue(session.pending)
        self.assertIs(self.tab.sessions[account], session)
        self.assertEqual(self.tab.selected_page()['name'], 'KyNangCauCa')
        self.tab.open_page_button.invoke()
        command = session.commands.get_nowait()
        self.assertEqual((command['action'],command['url'],command['account_id'],command['page_key']),
                         ('open_page','https://www.facebook.com/KyNangCauCa',account,self.tab.selected_page()['id']))
        self.thread.assert_called_once()
        self.assertFalse(session.finished.is_set())
        self.assertFalse(session.cancel.is_set())

    def test_inline_link_drafts_are_separate_for_accounts_and_survive_poll_refresh(self):
        one = self.account('Một')
        self.tab.page_link_name.set('Tên A')
        self.tab.page_link_url.set('facebook.com/PageA')
        two = self.account('Hai')
        self.assertEqual(self.tab.page_link_url.get(), '')
        self.tab.page_link_url.set('facebook.com/PageB')
        self.tab.reload()
        self.drain()
        self.assertEqual(self.tab.page_link_url.get(), 'facebook.com/PageB')
        self.tab.activate_account(one)
        self.assertEqual(self.tab.page_link_name.get(), 'Tên A')
        self.assertEqual(self.tab.page_link_url.get(), 'facebook.com/PageA')
        self.assertIn('Một', self.tab.page_link_destination.get())
        self.tab.save_page_link_button.invoke()
        self.assertEqual(self.tab.store.load()['pages'][0]['account_id'], one)
        self.tab.activate_account(two)
        self.assertEqual(self.tab.page_link_url.get(), 'facebook.com/PageB')
        self.assertEqual(self.tab.page_link_name.get(), '')
        self.tab.save_page_link_button.invoke()
        self.assertEqual([row['account_id'] for row in self.tab.store.load()['pages']], [one, two])

    def test_inline_link_validation_duplicate_and_save_error_retain_input_and_old_data(self):
        self.account()
        self.tab.page_link_url.set('facebook.com/friends')
        self.tab.save_page_link_button.invoke()
        self.assertEqual(self.tab.store.load()['pages'], [])
        self.assertEqual(self.tab.page_link_url.get(), 'facebook.com/friends')
        self.assertIn('Mở đúng Page', self.tab.page_link_notice.get())
        self.tab.page_link_url.set('facebook.com/PageA')
        self.tab.save_page_link_button.invoke()
        before = self.tab.store.path.read_bytes()
        self.tab.page_link_name.set('Tên khác')
        self.tab.page_link_url.set('facebook.com/PageA?ref=share')
        self.tab.save_page_link_button.invoke()
        self.assertIn('đã có', self.tab.page_link_notice.get())
        self.assertEqual(self.tab.store.path.read_bytes(), before)
        self.assertEqual(self.tab.page_link_name.get(), 'Tên khác')
        with patch.object(self.tab.store, 'save_page_link', side_effect=OSError('Fixture không ghi được')):
            self.tab.save_page_link_button.invoke()
        self.assertIn('không ghi được', self.tab.page_link_notice.get())
        self.assertEqual(self.tab.store.path.read_bytes(), before)
        self.assertEqual(self.tab.page_link_url.get(), 'facebook.com/PageA?ref=share')

    def test_inline_link_save_during_discovery_survives_merge_and_closing_disables_form(self):
        account = self.account()
        self.tab.load_pages()
        session = self.tab.sessions[account]
        self.tab.page_link_name.set('Tên tôi đặt')
        self.tab.page_link_url.set('facebook.com/profile.php?id=123')
        self.tab.save_page_link_button.invoke()
        self.assertTrue(session.loading_pages)
        session.messages.put(('opened', 'Chrome đang mở'))
        session.messages.put(('pages_loaded', dict(items=[dict(name='Tên từ FB', url='facebook.com/Other', page_id='123')],
                                                 message='Đã đọc 1', status='observed')))
        self.drain()
        self.assertEqual(len(self.tab.store.load()['pages']), 1)
        self.assertEqual(self.tab.store.load()['pages'][0]['name'], 'Tên tôi đặt')
        before = self.tab.store.path.read_bytes()
        self.studio.closing = True
        self.tab.refresh_buttons()
        self.assertEqual(str(self.tab.save_page_link_button['state']), 'disabled')
        self.tab.page_link_url.set('facebook.com/new')
        self.tab.save_page_link()
        self.assertEqual(self.tab.store.path.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
