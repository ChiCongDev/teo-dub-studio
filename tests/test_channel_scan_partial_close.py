from pathlib import Path
import sys
import tempfile
import threading
import shutil
import subprocess
from types import ModuleType
import unittest
from unittest.mock import Mock, patch

from teodub.channel_discovery import scan_channel
from teodub.channel_scan_guard import channel_scan_guard
from teodub.common import Cancelled

try:
    from playwright._impl._errors import TargetClosedError
except ModuleNotFoundError:
    # Use the same identity for the raised fixture error and the scanner's import.
    # The bundled interpreter instead exercises the real Playwright error class.
    class TargetClosedError(Exception):
        pass


class ChannelScanPartialCloseTests(unittest.TestCase):
    channel = 'https://www.douyin.com/user/ownerABC'
    endpoint = 'https://www.douyin.com/aweme/v1/web/aweme/post/?sec_user_id=ownerABC'

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='teo partial close fixtures ')
        self.addCleanup(self.temp.cleanup)
        self.profile = Path(self.temp.name)/'synthetic-app-profile'
        self.cancel = threading.Event()
        self.progress = []
        self.item_updates = []
        self.posts = [self.video(str(ident)) for ident in range(1018, 1000, -1)]
        self.payload = dict(aweme_list=self.posts, has_more=1)
        self.callbacks = {}
        self.page = Mock(name='synthetic page')
        self.page.url = self.channel
        self.page.is_closed.return_value = False
        self.page.on.side_effect = lambda event, handler: self.callbacks.setdefault(event, handler)
        self.page.goto.side_effect = self.emit_posts
        self.links = Mock(name='synthetic post anchors')
        self.links.evaluate_all.return_value = []
        self.body = Mock(name='synthetic body')
        self.body.inner_text.return_value = ''
        self.listing = Mock(name='synthetic post list')
        self.listing.first.count.return_value = 0
        self.page.locator.side_effect = {
            '[data-e2e="user-post-list"] a[href*="/video/"]': self.links,
            '[data-e2e="user-post-list"]': self.listing,
            'body': self.body,
        }.__getitem__
        self.context = Mock(name='synthetic context', pages=[self.page])
        self.closed_under_lock = False
        self.context.close.side_effect = self.check_lock_during_close
        manager = Mock(name='synthetic Playwright manager')
        manager.__enter__ = Mock(return_value=Mock(name='synthetic runtime'))
        manager.__exit__ = Mock(return_value=False)

        playwright = ModuleType('playwright')
        playwright.__path__ = []
        sync_api = ModuleType('playwright.sync_api')
        sync_api.sync_playwright = Mock(return_value=manager)
        impl = ModuleType('playwright._impl')
        impl.__path__ = []
        errors = ModuleType('playwright._impl._errors')
        errors.TargetClosedError = TargetClosedError
        playwright.sync_api = sync_api
        playwright._impl = impl
        impl._errors = errors
        self.fake_modules = {
            'playwright': playwright,
            'playwright.sync_api': sync_api,
            'playwright._impl': impl,
            'playwright._impl._errors': errors,
        }

    def video(self, ident):
        return dict(aweme_id=ident, author={'sec_uid': 'ownerABC'},
                    video={'duration': 10}, desc='Video '+ident)

    def emit_posts(self, *args, **kwargs):
        response = Mock(name='synthetic response')
        response.url = self.endpoint
        response.json.return_value = self.payload
        self.callbacks['response'](response)

    def check_lock_during_close(self):
        try:
            with channel_scan_guard(self.profile):
                self.closed_under_lock = False
        except ValueError:
            self.closed_under_lock = True

    def run_scan(self):
        with patch.dict(sys.modules, self.fake_modules), \
                patch('teodub.channel_discovery.launch_channel_browser',
                      return_value=(self.context, 'coccoc')) as launch:
            try:
                return scan_channel(self.channel, self.profile, browser='coccoc',
                    visible=True, timeout=5, cancel=self.cancel,
                    progress=self.progress.append, on_items=self.item_updates.append)
            finally:
                launch.assert_called_once()
                self.context.close.assert_called_once()
                self.assertTrue(self.closed_under_lock, 'Profile must remain locked until context.close finishes')
                with channel_scan_guard(self.profile):
                    pass

    def assert_partial_result(self, result):
        self.assertEqual(result['channel'], self.channel)
        self.assertEqual(result['items'], [
            {'url': 'https://www.douyin.com/video/'+post['aweme_id'], 'title': post['desc']}
            for post in self.posts
        ])
        self.assertIs(result['complete'], False)
        self.assertEqual(result['reason'], 'browser_closed')
        self.assertEqual(result['browser'], 'coccoc')
        self.assertEqual(result['requested_browser'], 'coccoc')
        self.assertIn('Trình duyệt đã đóng', result['message'])

    def test_close_during_login_wait_preserves_all_18_ordered_links(self):
        self.body.inner_text.return_value = '登录后查看更多作品'
        self.page.wait_for_timeout.side_effect = TargetClosedError('Fixture closed during login wait')
        result = self.run_scan()
        self.assert_partial_result(result)
        self.assertEqual(len(result['items']), 18)
        self.page.wait_for_timeout.assert_called_once_with(1200)
        self.assertEqual(self.item_updates[-1], result['items'])

    def test_close_during_dom_read_preserves_api_items_and_clears_complete(self):
        self.payload['has_more'] = 0
        self.links.evaluate_all.side_effect = TargetClosedError('Fixture closed during DOM read')
        result = self.run_scan()
        self.assert_partial_result(result)
        self.body.inner_text.assert_not_called()

    def test_is_closed_branch_clears_previous_terminal_complete_flag(self):
        self.payload['has_more'] = 0
        self.page.is_closed.return_value = True
        result = self.run_scan()
        self.assert_partial_result(result)
        self.links.evaluate_all.assert_not_called()

    def test_close_before_any_posts_are_found_returns_friendly_no_list_error(self):
        self.payload = dict(aweme_list=[], has_more=1)
        self.links.evaluate_all.side_effect = TargetClosedError('Fixture closed with empty posts')
        with self.assertRaises(ValueError) as error:
            self.run_scan()
        self.assertIn('Trình duyệt đã đóng trước khi đọc được video', str(error.exception))
        self.assertNotIn('Fixture', str(error.exception))

    def test_close_before_channel_navigation_returns_friendly_no_list_error(self):
        self.page.goto.side_effect = TargetClosedError('Fixture closed during navigation')
        with self.assertRaises(ValueError) as error:
            self.run_scan()
        self.assertIn('Trình duyệt đã đóng trước khi đọc được danh sách', str(error.exception))
        self.assertNotIn('Fixture', str(error.exception))

    def test_unrelated_dom_error_propagates_even_if_message_mentions_closed_browser(self):
        failure = RuntimeError('Target page, context or browser has been closed: unrelated fixture')
        self.links.evaluate_all.side_effect = failure
        with self.assertRaises(RuntimeError) as error:
            self.run_scan()
        self.assertIs(error.exception, failure)

    def test_cancel_during_login_wait_propagates(self):
        self.body.inner_text.return_value = '登录后查看更多作品'
        self.page.wait_for_timeout.side_effect = lambda *args: self.cancel.set()
        with self.assertRaises(Cancelled):
            self.run_scan()
        self.assertTrue(self.item_updates)

    def test_cancel_wins_when_browser_closes_during_login_wait(self):
        self.body.inner_text.return_value = '登录后查看更多作品'
        def cancel_and_close(*args):
            self.cancel.set()
            raise TargetClosedError('Fixture simultaneous cancel and close')
        self.page.wait_for_timeout.side_effect = cancel_and_close
        with self.assertRaises(Cancelled):
            self.run_scan()
        self.assertTrue(self.item_updates)

    def test_login_hint_does_not_stop_scrolling_while_new_posts_arrive(self):
        self.body.inner_text.return_value = '登录后查看更多作品'
        tick = [0]
        def next_page(*args):
            tick[0] += 1
            self.payload = dict(aweme_list=[self.video(str(2000+tick[0]))], has_more=1)
            self.emit_posts()
        self.page.wait_for_timeout.side_effect = next_page
        with patch('teodub.channel_discovery.time.monotonic', side_effect=lambda: tick[0]):
            result = self.run_scan()
        self.assertEqual(result['reason'], 'timeout')
        self.assertFalse(result['complete'])
        self.assertGreater(len(result['items']), 18)
        self.assertEqual(self.page.mouse.wheel.call_count, 5)
        self.assertFalse(any('yêu cầu đăng nhập' in msg for msg in self.progress))

    def test_stalled_login_hint_still_stops_hidden_scan_without_retry(self):
        self.body.inner_text.return_value = '登录后查看更多作品'
        tick = [0]
        self.page.wait_for_timeout.side_effect = lambda *_: tick.__setitem__(0, tick[0]+2)
        with patch.dict(sys.modules, self.fake_modules), \
             patch('teodub.channel_discovery.launch_channel_browser', return_value=(self.context, 'chrome')) as launch, \
             patch('teodub.channel_discovery.time.monotonic', side_effect=lambda: tick[0]):
            result = scan_channel(self.channel, self.profile, browser='chrome', visible=False, timeout=30)
        self.assertEqual(result['reason'], 'login_required')
        self.assertFalse(result['complete'])
        self.assertEqual(self.page.mouse.wheel.call_count, 4)
        launch.assert_called_once()
        self.context.close.assert_called_once()

    def test_plain_login_button_does_not_mean_channel_is_blocked(self):
        self.body.inner_text.return_value = '登录 作品 258'
        self.payload['has_more'] = 0
        result = self.run_scan()
        self.assertTrue(result['complete'])
        self.assertEqual(result['reason'], 'end_of_public_posts')

    @unittest.skipUnless(shutil.which('node'), 'Node needed for synthetic scroll DOM test')
    def test_scroll_targets_list_itself_or_nearest_scrollable_parent(self):
        self.listing.first.count.return_value = 1
        self.page.wait_for_timeout.side_effect = TargetClosedError('Fixture ends after first scroll')
        self.run_scan()
        script = self.listing.first.evaluate.call_args.args[0]
        fixture = '''
const assert = require('node:assert/strict');
global.getComputedStyle = p => ({overflowY:p.overflowY});
const scroll = (SCRIPT);
const parent = {overflowY:'auto',scrollHeight:5000,clientHeight:800,scrollTop:0,parentElement:null};
const list = {overflowY:'auto',scrollHeight:4000,clientHeight:700,scrollTop:0,parentElement:parent};
scroll(list);
assert.equal(list.scrollTop,1100);
assert.equal(parent.scrollTop,0);
list.overflowY='visible';
scroll(list);
assert.equal(parent.scrollTop,1100);
'''.replace('SCRIPT', script)
        result = subprocess.run([shutil.which('node'), '-e', fixture], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
