from pathlib import Path
import sys
import tempfile
import threading
from types import ModuleType
import unittest
from unittest.mock import Mock, patch

from teodub.channel_discovery import scan_channel
from teodub.channel_scan_guard import channel_scan_guard
from teodub.common import Cancelled


class ChannelScanLifecycleTests(unittest.TestCase):
    def test_profile_is_locked_during_open_and_close_with_progress_before_open(self):
        with tempfile.TemporaryDirectory() as root:
            profile = Path(root)/'synthetic-profile'
            page = Mock()
            page.url = 'https://www.douyin.com/video/123'
            context = Mock(pages=[page])
            runtime = Mock()
            manager = Mock()
            manager.__enter__ = Mock(return_value=runtime)
            manager.__exit__ = Mock(return_value=False)
            module = ModuleType('playwright.sync_api')
            module.sync_playwright = Mock(return_value=manager)
            progress = []
            checked = []
            def locked(stage):
                with self.assertRaises(ValueError):
                    with channel_scan_guard(profile):
                        pass
                checked.append(stage)
            def launch(*args, **kwargs):
                self.assertTrue(progress)
                self.assertIn('Đang mở', progress[-1])
                locked('open')
                return context, 'coccoc'
            context.close.side_effect = lambda: locked('close')
            with patch.dict(sys.modules, {'playwright.sync_api': module}), \
                 patch('teodub.channel_discovery.launch_channel_browser', side_effect=launch):
                result = scan_channel('https://www.douyin.com/user/owner', profile,
                                      browser='coccoc', visible=True, allow_video=True, progress=progress.append)
            self.assertEqual(result['reason'], 'single_video')
            self.assertEqual(checked, ['open', 'close'])
            with channel_scan_guard(profile):
                pass

    def test_cancelled_before_open_never_creates_profile_or_launches(self):
        with tempfile.TemporaryDirectory() as root:
            profile = Path(root)/'synthetic-profile'
            cancel = threading.Event()
            cancel.set()
            with patch('teodub.channel_discovery.launch_channel_browser') as launch:
                with self.assertRaises(Cancelled):
                    scan_channel('https://www.douyin.com/user/owner', profile, browser='coccoc', cancel=cancel)
                launch.assert_not_called()
            self.assertFalse(profile.exists())

    def test_cancel_during_open_closes_context_before_navigation_and_releases_lock(self):
        with tempfile.TemporaryDirectory() as root:
            profile = Path(root)/'synthetic-profile'
            cancel = threading.Event()
            page = Mock()
            context = Mock(pages=[page])
            manager = Mock()
            manager.__enter__ = Mock(return_value=Mock())
            manager.__exit__ = Mock(return_value=False)
            module = ModuleType('playwright.sync_api')
            module.sync_playwright = Mock(return_value=manager)
            def opening(*args, **kwargs):
                cancel.set()
                return context, 'coccoc'
            with patch.dict(sys.modules, {'playwright.sync_api': module}), \
                 patch('teodub.channel_discovery.launch_channel_browser', side_effect=opening):
                with self.assertRaises(Cancelled):
                    scan_channel('https://www.douyin.com/user/owner', profile, browser='coccoc', cancel=cancel)
            page.goto.assert_not_called()
            context.close.assert_called_once()
            with channel_scan_guard(profile):
                pass


if __name__ == '__main__':
    unittest.main()
