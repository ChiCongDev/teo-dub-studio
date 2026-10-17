from pathlib import Path
import queue
import sys
import tempfile
import threading
from types import ModuleType
import unittest
from unittest.mock import Mock, patch

from playwright._impl._errors import Error, TargetClosedError
from teodub.common import Cancelled
from teodub.facebook_accounts import BUSINESS_SUITE, FACEBOOK_HOME, META_APPS, META_EXPLORER
from teodub.facebook_browser import facebook_profile_guard, open_facebook_browser


class FacebookBrowserTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.profile = Path(self.temp.name)/'page-publishing'/'browser-profiles'/('a'*32)/'chrome'
        self.cancel = threading.Event()
        self.finished = threading.Event()
        self.messages = []
        self.commands = queue.Queue()
        self.page = Mock(url='about:blank')
        self.page.is_closed.return_value = False
        self.page.wait_for_timeout.side_effect = lambda _: self.finished.set()
        self.context = Mock(pages=[self.page])
        self.context.close.side_effect = self.close_under_lock
        self.runtime = Mock()
        self.launch = self.runtime.chromium.launch_persistent_context
        self.launch.return_value = self.context
        manager = Mock()
        manager.__enter__ = Mock(return_value=self.runtime)
        manager.__exit__ = Mock(return_value=False)
        module = ModuleType('playwright.sync_api')
        module.sync_playwright = Mock(return_value=manager)
        self.enterContext(patch.dict(sys.modules, {'playwright.sync_api': module}))

    def close_under_lock(self):
        with self.assertRaises(ValueError):
            with facebook_profile_guard(self.profile):
                pass

    def browse(self, **kwargs):
        try:
            return open_facebook_browser(self.profile, cancel=self.cancel, finished=self.finished,
                commands=self.commands, progress=lambda kind, text: self.messages.append((kind, text)), **kwargs)
        finally:
            if self.profile.exists():
                with facebook_profile_guard(self.profile):
                    pass

    def test_visible_chrome_reopen_uses_same_profile_and_keeps_data(self):
        self.profile.mkdir(parents=True)
        marker = self.profile/'synthetic-marker'
        marker.write_text('fixture, no credentials')
        for _ in range(2):
            self.finished.clear()
            self.assertEqual(self.browse(), 'finished')
            self.assertEqual(self.launch.call_args.args, (str(self.profile.resolve()),))
            self.assertEqual(self.launch.call_args.kwargs, dict(channel='chrome', headless=False,
                no_viewport=True, ignore_default_args=['--mute-audio'], timeout=30000))
            self.assertEqual(marker.read_text(), 'fixture, no credentials')
        self.context.cookies.assert_not_called()
        self.context.storage_state.assert_not_called()
        self.page.fill.assert_not_called()
        self.page.click.assert_not_called()
        self.assertEqual(self.context.close.call_count, 2)
        self.assertIn('tự đăng nhập', next(text for kind, text in self.messages if kind == 'opened'))

    def test_business_and_page_open_in_same_context_without_relaunch(self):
        second = Mock(url=BUSINESS_SUITE)
        self.context.new_page.return_value = second
        self.commands.put(BUSINESS_SUITE)
        self.commands.put('https://www.facebook.com/fanpage')
        waits = 0
        def step(_):
            nonlocal waits
            waits += 1
            if waits == 2:
                self.finished.set()
        self.page.wait_for_timeout.side_effect = step
        self.assertEqual(self.browse(), 'finished')
        self.launch.assert_called_once()
        self.assertEqual(second.goto.call_count, 2)
        self.assertEqual(second.goto.call_args.args[0], 'https://www.facebook.com/fanpage')

    def test_initial_invalid_url_never_launches_or_creates_profile(self):
        with self.assertRaises(ValueError):
            self.browse(url='https://evil.example/')
        self.launch.assert_not_called()
        self.assertFalse(self.profile.exists())

    def test_meta_developers_initial_open_uses_account_profile_no_auth_actions(self):
        self.assertEqual(self.browse(url=META_APPS), 'finished')
        self.page.goto.assert_called_once_with(META_APPS, wait_until='domcontentloaded', timeout=45000)
        self.assertEqual(self.launch.call_args.args[0], str(self.profile.resolve()))
        self.context.cookies.assert_not_called()
        self.context.storage_state.assert_not_called()
        self.page.fill.assert_not_called()
        self.page.click.assert_not_called()

    def test_meta_explorer_queued_in_same_context_no_new_browser(self):
        second = Mock(url='about:blank')
        self.context.new_page.return_value = second
        self.commands.put(META_EXPLORER)
        self.assertEqual(self.browse(), 'finished')
        self.launch.assert_called_once()
        second.goto.assert_called_once_with(META_EXPLORER, wait_until='domcontentloaded', timeout=45000)
        self.assertEqual(self.page.goto.call_args.args[0], FACEBOOK_HOME)

    def test_tracked_initial_navigation_reports_opening_and_opened(self):
        self.assertEqual(self.browse(url=META_APPS,request_id='a'*32),'finished')
        events = [value for kind,value in self.messages if kind=='navigation']
        self.assertEqual([event['state'] for event in events],['opening','opened'])
        self.assertTrue(all(event['request_id']=='a'*32 for event in events))
        self.assertIn(META_APPS,events[-1]['message'])

    def test_tracked_queued_navigation_reports_timeout_without_relaunch(self):
        second = Mock(url='about:blank')
        second.goto.side_effect = Error('fixture timeout')
        self.context.new_page.return_value = second
        self.commands.put(dict(action='open_url',url=META_EXPLORER,request_id='b'*32))
        self.assertEqual(self.browse(),'finished')
        events = [value for kind,value in self.messages if kind=='navigation']
        self.assertEqual([event['state'] for event in events],['opening','error'])
        self.assertIn('trang chưa tải xong',events[-1]['message'])
        self.launch.assert_called_once()
        second.goto.assert_called_once()

    def test_existing_facebook_tab_is_raised_without_reloading_user_form(self):
        self.page.url = FACEBOOK_HOME
        self.commands.put(FACEBOOK_HOME)
        self.assertEqual(self.browse(), 'finished')
        self.page.goto.assert_not_called()
        self.context.new_page.assert_not_called()
        self.assertEqual(self.page.bring_to_front.call_count, 2)

    def test_invalid_queued_url_is_not_navigated_and_browser_stays_open(self):
        self.commands.put('https://evil.example/')
        self.assertEqual(self.browse(), 'finished')
        self.page.goto.assert_called_once()
        self.assertTrue(any('Chỉ nhận link' in text for _, text in self.messages))

    def test_cancel_before_launch_no_profile(self):
        self.cancel.set()
        with self.assertRaises(Cancelled):
            self.browse()
        self.launch.assert_not_called()
        self.assertFalse(self.profile.exists())

    def test_cancel_during_launch_closes_under_lock_before_navigation(self):
        def launch(*args, **kwargs):
            self.cancel.set()
            return self.context
        self.launch.side_effect = launch
        with self.assertRaises(Cancelled):
            self.browse()
        self.page.goto.assert_not_called()
        self.context.close.assert_called_once()

    def test_finish_while_launching_closes_without_waiting_on_navigation(self):
        def launch(*args, **kwargs):
            self.finished.set()
            return self.context
        self.launch.side_effect = launch
        self.assertEqual(self.browse(), 'finished')
        self.page.goto.assert_not_called()
        self.context.close.assert_called_once()

    def test_navigation_error_keeps_visible_window_for_manual_retry(self):
        self.page.goto.side_effect = Error('Fixture network timeout')
        self.assertEqual(self.browse(), 'finished')
        self.assertIn('chưa tải xong', next(text for kind, text in self.messages if kind == 'opened'))
        self.context.close.assert_called_once()

    def test_user_close_is_not_login_success(self):
        self.page.goto.side_effect = TargetClosedError()
        self.assertEqual(self.browse(), 'browser_closed')
        self.context.close.assert_called_once()
        self.assertFalse(any('đã đăng nhập' in text.lower() for _, text in self.messages))

    def test_launch_failure_release_lock_and_retain_profile(self):
        self.launch.side_effect = Error('Fixture executable missing')
        with self.assertRaisesRegex(ValueError, 'Google Chrome đã cài'):
            self.browse()
        self.assertTrue(self.profile.is_dir())

    def test_same_profile_cannot_open_twice(self):
        with facebook_profile_guard(self.profile):
            with self.assertRaisesRegex(ValueError, 'cửa sổ Tèo khác'):
                open_facebook_browser(self.profile)
        self.launch.assert_not_called()

    def test_load_pages_uses_current_context_and_returns_structured_metadata(self):
        self.commands.put(dict(action='load_pages'))
        result = dict(items=[dict(name='Kỹ Năng Câu Cá', url='https://facebook.com/Page')],
                      status='observed', message='Chỉ danh sách đã đọc')
        def scan(context, **kwargs):
            self.assertIs(context, self.context)
            self.assertIs(kwargs['cancel'], self.cancel)
            self.assertIs(kwargs['finished'], self.finished)
            kwargs['progress']('Đã đọc 1 Page')
            return result
        with patch('teodub.facebook_page_discovery.read_managed_pages', side_effect=scan) as reader:
            self.assertEqual(self.browse(), 'finished')
        reader.assert_called_once()
        self.launch.assert_called_once()
        self.assertIn(('pages_progress', 'Đã đọc 1 Page'), self.messages)
        self.assertIn(('pages_loaded', result), self.messages)
        self.context.cookies.assert_not_called()
        self.context.storage_state.assert_not_called()

    def test_load_page_error_keeps_browser_available_without_automatic_retry(self):
        self.commands.put(dict(action='load_pages'))
        self.commands.put(BUSINESS_SUITE)
        waits = 0
        def advance(_):
            nonlocal waits
            waits += 1
            if waits == 2:
                self.finished.set()
        self.page.wait_for_timeout.side_effect = advance
        second = Mock(url=BUSINESS_SUITE)
        self.context.new_page.return_value = second
        with patch('teodub.facebook_page_discovery.read_managed_pages', side_effect=ValueError('Fixture layout changed')) as reader:
            self.assertEqual(self.browse(), 'finished')
        reader.assert_called_once()
        self.assertIn(('pages_error', 'Fixture layout changed'), self.messages)
        self.assertFalse(any(kind == 'pages_loaded' for kind, _ in self.messages))
        second.goto.assert_called_once_with(BUSINESS_SUITE, wait_until='domcontentloaded', timeout=45000)

    def test_restart_flushes_only_current_profile_after_editor_check(self):
        self.commands.put(dict(action='restart_close',token='f'*32))
        with patch('teodub.restart.browser_restart_check') as check:
            self.assertEqual(self.browse(),'restart_saved')
        check.assert_called_once_with(self.context)
        self.context.close.assert_called_once()
        self.assertFalse(self.cancel.is_set())
        self.context.cookies.assert_not_called()

    def test_restart_blocked_editor_keeps_context_until_user_finishes(self):
        self.commands.put(dict(action='restart_close',token='f'*32))
        def keep_until_user_finishes(_):
            self.context.close.assert_not_called()
            self.finished.set()
        self.page.wait_for_timeout.side_effect = keep_until_user_finishes
        with patch('teodub.restart.browser_restart_check',side_effect=ValueError('Fixture unsaved editor')):
            self.assertEqual(self.browse(),'finished')
        self.assertIn(('restart_blocked','Fixture unsaved editor'),self.messages)
        self.context.close.assert_called_once()

    def test_finish_during_scan_releases_profile_and_never_emits_partial_success(self):
        self.commands.put(dict(action='load_pages'))
        def stop(*args, **kwargs):
            self.finished.set()
            raise Cancelled('Đã dừng Load Page')
        with patch('teodub.facebook_page_discovery.read_managed_pages', side_effect=stop):
            self.assertEqual(self.browse(), 'finished')
        self.assertIn(('pages_error', 'Đã dừng Load Page'), self.messages)
        self.assertFalse(any(kind == 'pages_loaded' for kind, _ in self.messages))
        self.context.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
