from pathlib import Path
import sys
import tempfile
import threading
from types import ModuleType
import unittest
from unittest.mock import Mock, patch

from playwright._impl._errors import TargetClosedError
from teodub.channel_login import open_chrome_login
from teodub.channel_scan_guard import channel_scan_guard
from teodub.common import Cancelled


class ChromeLoginTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.profile = Path(self.temp.name)/'douyin-channel-browser'/'chrome'
        self.cancel = threading.Event()
        self.finished = threading.Event()
        self.page = Mock()
        self.page.is_closed.return_value = False
        self.page.wait_for_timeout.side_effect = lambda _: self.finished.set()
        self.context = Mock(pages=[self.page])
        self.context.close.side_effect = self.close_under_lock
        manager = Mock()
        manager.__enter__ = Mock(return_value=Mock())
        manager.__exit__ = Mock(return_value=False)
        module = ModuleType('playwright.sync_api')
        module.sync_playwright = Mock(return_value=manager)
        self.enterContext(patch.dict(sys.modules, {'playwright.sync_api': module}))
        self.launch = self.enterContext(patch('teodub.channel_login.launch_channel_browser', return_value=(self.context, 'chrome')))

    def close_under_lock(self):
        with self.assertRaises(ValueError):
            with channel_scan_guard(self.profile):
                pass

    def run_login(self, **kwargs):
        try:
            return open_chrome_login(self.profile, cancel=self.cancel, finished=self.finished, **kwargs)
        finally:
            if self.profile.exists():
                with channel_scan_guard(self.profile):
                    pass

    def test_finish_and_reopen_use_identical_chrome_profile_without_deleting_data(self):
        self.profile.mkdir(parents=True)
        marker = self.profile/'synthetic-session-marker'
        marker.write_text('fixture, not credentials')
        for _ in range(2):
            self.finished.clear()
            self.assertEqual(self.run_login(), 'finished')
            self.assertEqual(self.launch.call_args.args[1], self.profile.resolve())
            self.assertEqual(self.launch.call_args.kwargs, dict(browser='chrome', visible=True))
            self.assertEqual(marker.read_text(), 'fixture, not credentials')
        self.assertEqual(self.context.close.call_count, 2)
        self.context.cookies.assert_not_called()
        self.context.storage_state.assert_not_called()

    def test_channel_is_normalized_and_user_gets_manual_login_guidance(self):
        messages = []
        self.run_login(text='https://www.douyin.com/user/owner?from=share', progress=messages.append)
        self.page.goto.assert_called_once_with('https://www.douyin.com/user/owner', wait_until='domcontentloaded', timeout=45000)
        self.assertIn('Bạn tự đăng nhập', messages[-1])
        self.assertIn('30 phút', messages[-1])
        self.page.fill.assert_not_called()
        self.page.click.assert_not_called()

    def test_cancel_before_open_creates_no_profile(self):
        self.cancel.set()
        with self.assertRaises(Cancelled):
            self.run_login()
        self.launch.assert_not_called()
        self.assertFalse(self.profile.exists())

    def test_cancel_while_launching_closes_before_navigation(self):
        def launching(*args, **kwargs):
            self.cancel.set()
            return self.context, 'chrome'
        self.launch.side_effect = launching
        with self.assertRaises(Cancelled):
            self.run_login()
        self.page.goto.assert_not_called()
        self.context.close.assert_called_once()

    def test_user_closing_browser_is_not_a_login_success_claim(self):
        self.page.goto.side_effect = TargetClosedError()
        self.assertEqual(self.run_login(), 'browser_closed')
        self.context.close.assert_called_once()

    def test_other_navigation_errors_propagate_and_release_profile(self):
        self.page.goto.side_effect = RuntimeError('Fixture navigation failed')
        with self.assertRaisesRegex(RuntimeError, 'Fixture navigation failed'):
            self.run_login()
        self.context.close.assert_called_once()

    def test_timeout_flushes_context_without_clearing_profile(self):
        with patch('teodub.channel_login.time.monotonic', side_effect=[0, 1801]):
            self.assertEqual(self.run_login(), 'timeout')
        self.context.close.assert_called_once()
        self.assertTrue(self.profile.is_dir())

    def test_login_cannot_open_profile_while_reader_holds_it(self):
        with channel_scan_guard(self.profile):
            with self.assertRaisesRegex(ValueError, 'cùng hồ sơ'):
                open_chrome_login(self.profile)
        self.launch.assert_not_called()


if __name__ == '__main__':
    unittest.main()
