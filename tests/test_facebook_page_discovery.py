import threading
import unittest
from unittest.mock import Mock, patch

from playwright._impl._errors import TargetClosedError
from teodub.common import Cancelled
from teodub.facebook_accounts import FACEBOOK_PAGES
from teodub.facebook_page_discovery import SNAPSHOT_SCRIPT, SCROLL_SCRIPT, normalize_items, read_managed_pages


class FacebookDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.page = Mock(url=FACEBOOK_PAGES)
        self.context = Mock()
        self.context.new_page.return_value = self.page
        self.clock = 0
        self.snapshot = dict(managed=True, auth=False, bottom=True, busy=False, items=[
            dict(name='Kỹ Năng Câu Cá', url='https://www.facebook.com/profile.php?id=123&ref=share')])
        self.page.evaluate.side_effect = lambda script: self.snapshot if script == SNAPSHOT_SCRIPT else None
        self.page.wait_for_timeout.side_effect = self.advance
        self.enterContext(patch('teodub.facebook_page_discovery.time.monotonic', side_effect=lambda: self.clock))
        self.messages = []
        self.cancel = threading.Event()
        self.finished = threading.Event()

    def advance(self, ms):
        self.clock += ms/1000

    def scan(self, **kwargs):
        return read_managed_pages(self.context, cancel=self.cancel, finished=self.finished,
                                  progress=self.messages.append, **kwargs)

    def test_normalize_card_urls_ids_and_deduplicate_without_trusting_supplied_id(self):
        rows = normalize_items([
            dict(name='Page A', url='https://m.facebook.com/profile.php?id=123&ref=bookmark', page_id='999'),
            dict(name='Page A', url='https://facebook.com/profile.php?id=123'),
            dict(name='Page B', url='https://facebook.com/pages/Page-B/456/?ref=share'),
            dict(name='Page C', url='https://www.facebook.com/789'),
            dict(name='Page vanity', url='https://www.facebook.com/vanity')])
        self.assertEqual([row['page_id'] for row in rows], ['123', '456', '789', ''])
        self.assertEqual(rows[0]['url'], 'https://www.facebook.com/profile.php?id=123')

    def test_navigation_or_external_links_are_not_treated_as_page_cards(self):
        self.assertEqual(normalize_items([
            dict(name='Friends', url='https://facebook.com/friends'),
            dict(name='Group', url='https://facebook.com/groups/123'),
            dict(name='Your Pages', url=FACEBOOK_PAGES),
            dict(name='Evil', url='https://facebook.com.evil.test/page'),
            dict(name='', url='https://facebook.com/valid')]), [])

    def test_read_known_list_in_new_tab_and_return_observed_not_all_api_pages(self):
        result = self.scan()
        self.context.new_page.assert_called_once()
        self.page.goto.assert_called_once_with(FACEBOOK_PAGES, wait_until='domcontentloaded', timeout=45000)
        self.assertEqual(result['status'], 'observed')
        self.assertEqual(len(result['items']), 1)
        self.assertIn('Chưa xác minh tổng Page', result['message'])
        self.assertGreaterEqual(self.clock, 6)
        self.assertEqual(self.messages[0], self.messages[-1])
        self.assertEqual(len(self.messages), 1)
        self.page.click.assert_not_called()
        self.page.fill.assert_not_called()
        self.context.cookies.assert_not_called()
        self.page.close.assert_not_called()

    def test_wait_for_manual_login_then_read_without_entering_credentials(self):
        self.page.url = 'https://www.facebook.com/login/?next=pages'
        def advance(ms):
            self.advance(ms)
            if self.clock >= 1:
                self.page.url = FACEBOOK_PAGES
        self.page.wait_for_timeout.side_effect = advance
        result = self.scan()
        self.assertEqual(len(result['items']), 1)
        self.assertIn('tự đăng nhập', self.messages[0])
        self.page.fill.assert_not_called()
        self.page.click.assert_not_called()

    def test_login_timeout_preserves_old_list_and_does_not_return_empty_success(self):
        self.page.url = 'https://www.facebook.com/login.php'
        with self.assertRaisesRegex(ValueError, 'chưa đọc được Page'):
            self.scan(timeout=2)
        self.page.evaluate.assert_not_called()

    def test_unknown_layout_and_empty_cards_report_explanation(self):
        for snapshot in (dict(managed=False, auth=False, items=[]),
                         dict(managed=True, auth=False, items=[], bottom=True, busy=False)):
            self.clock = 0
            self.snapshot = snapshot
            with self.assertRaises(ValueError):
                self.scan(timeout=8)

    def test_late_cards_reset_settle_timer_and_are_accumulated_across_scroll(self):
        def snapshot(script):
            if script != SNAPSHOT_SCRIPT:
                return None
            items = self.snapshot['items'] if self.clock < 5 else [dict(name='Nhất Tâm Review', url='https://facebook.com/nhattam')]
            return dict(managed=True, auth=False, busy=False, bottom=True, items=items)
        self.page.evaluate.side_effect = snapshot
        result = self.scan()
        self.assertEqual([row['name'] for row in result['items']], ['Kỹ Năng Câu Cá', 'Nhất Tâm Review'])
        self.assertGreaterEqual(self.clock, 11)

    def test_busy_or_unfinished_scroll_yields_partial_on_timeout(self):
        self.snapshot.update(busy=True, bottom=False)
        result = self.scan(timeout=2)
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(len(result['items']), 1)
        self.assertIn('Có thể chưa đủ', result['message'])

    def test_limit_is_partial_and_does_not_claim_exhausted_account(self):
        self.snapshot['items'].append(dict(name='Page B', url='https://facebook.com/b'))
        result = self.scan(limit=1)
        self.assertEqual(len(result['items']), 1)
        self.assertEqual(result['status'], 'partial')

    def test_user_closure_external_redirect_and_cancellation_are_not_success(self):
        self.page.goto.side_effect = TargetClosedError()
        with self.assertRaisesRegex(ValueError, 'đóng trước'):
            self.scan()
        self.page.goto.side_effect = None
        self.page.url = 'https://evil.test/pages/?category=your_pages'
        with self.assertRaisesRegex(ValueError, 'khác Facebook'):
            self.scan()
        self.page.url = FACEBOOK_PAGES
        self.finished.set()
        with self.assertRaises(Cancelled):
            self.scan()
        self.finished.clear()
        self.cancel.set()
        with self.assertRaises(Cancelled):
            self.scan()

    def test_browser_closed_before_dedicated_tab_opens_reports_preserved_list(self):
        self.context.new_page.side_effect = TargetClosedError()
        with self.assertRaisesRegex(ValueError, 'đóng trước'):
            self.scan()


class FacebookDOMFixtureTests(unittest.TestCase):
    """Real JS/DOM verification on offline fixtures; never opens a live Facebook account."""
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls.runtime = sync_playwright().start()
        try:
            cls.browser = cls.runtime.chromium.launch(channel='chrome', headless=True)
        except Exception:
            cls.runtime.stop()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.runtime.stop()

    def setUp(self):
        self.page = self.browser.new_page()
        # All URLs remain local fixture documents; outbound requests are blocked.
        self.page.route('**/*', lambda route: route.abort())
        self.addCleanup(self.page.close)

    def card(self, name, href):
        return f'<article><a href="{href}"><img alt=""/></a><h2><a href="{href}">{name}</a></h2>' \
               '<a href="https://facebook.com/messages">Tin nhắn</a><button>Tạo bài viết</button><button>Quảng cáo</button></article>'

    def test_screenshot_layout_reads_only_managed_page_cards(self):
        self.page.set_content('<aside><a href="https://facebook.com/PersonalNick">Trang cá nhân</a></aside>'
            '<main><h1>Trang bạn quản lý</h1>'
            + self.card('Kỹ Năng Câu Cá', 'https://facebook.com/profile.php?id=123&amp;ref=bookmarks')
            + self.card('Nhất Tâm Review', 'https://facebook.com/nhattam')
            + '<section><h2>Followed Pages</h2><a href="https://facebook.com/Followed">Page đang theo dõi</a></section>'
            + '</main><input type="hidden" value="fixture-unrelated-private-data"/>')
        result = self.page.evaluate(SNAPSHOT_SCRIPT)
        self.assertTrue(result['managed'])
        self.assertEqual([row['name'] for row in result['items']], ['Kỹ Năng Câu Cá', 'Nhất Tâm Review'])
        self.assertEqual(result['items'][0]['page_id'], '123')
        self.assertNotIn('fixture-unrelated-private-data', str(result))

    def test_unknown_screen_and_login_are_never_imported_as_page_cards(self):
        self.page.set_content('<main><h1>Trang</h1>'+self.card('Bạn bè', 'https://facebook.com/personal')+'</main>')
        self.assertFalse(self.page.evaluate(SNAPSHOT_SCRIPT)['managed'])
        self.page.set_content('<input type="password" value="fixture-only"/>')
        result = self.page.evaluate(SNAPSHOT_SCRIPT)
        self.assertTrue(result['auth'])
        self.assertEqual(result['items'], [])
        self.assertNotIn('fixture-only', str(result))

    def test_english_heading_actions_and_inner_scroller(self):
        self.page.set_content('<main style="height:140px;overflow-y:auto"><h1>Pages you manage</h1>'
            + self.card('Page A', 'https://facebook.com/PageA').replace('Tạo bài viết', 'Create post').replace('Quảng cáo', 'Advertise')
            + '<div style="height:600px"></div></main>')
        result = self.page.evaluate(SNAPSHOT_SCRIPT)
        self.assertEqual(len(result['items']), 1)
        self.assertFalse(result['bottom'])
        self.page.evaluate(SCROLL_SCRIPT)
        self.assertGreater(self.page.locator('main').evaluate('(node) => node.scrollTop'), 0)

    def test_no_actions_no_profile_links_and_hidden_cards_are_ignored(self):
        self.page.set_content('<main><h1>Trang bạn quản lý</h1>'
            '<article><a href="https://facebook.com/Random">Trang gợi ý</a></article>'
            '<article><a href="https://facebook.com/groups/123">Nhóm</a><button>Tạo bài viết</button><button>Quảng cáo</button></article>'
            '<div hidden>'+self.card('Ẩn', 'https://facebook.com/Hidden')+'</div></main>')
        self.assertEqual(self.page.evaluate(SNAPSHOT_SCRIPT)['items'], [])

    def test_actions_in_another_card_cannot_validate_suggestion_link(self):
        self.page.set_content('<main><h1>Trang bạn quản lý</h1><section>'
            '<article><div><h2><a href="https://facebook.com/suggestion">Gợi ý</a></h2></div></article>'
            '<article><a href="https://facebook.com/groups/123">Nhóm</a><button>Tạo bài viết</button>'
            '<button>Quảng cáo</button></article></section>'
            '<div><div><h2><a href="https://facebook.com/actual">Page thật</a></h2></div>'
            '<div><button>Tạo bài viết</button><button>Quảng cáo</button></div></div></main>')
        self.assertEqual([row['name'] for row in self.page.evaluate(SNAPSHOT_SCRIPT)['items']], ['Page thật'])

    def test_div_only_cards_do_not_borrow_actions_and_action_links_do_not_replace_names(self):
        self.page.set_content('<main><h1>Trang bạn quản lý</h1><section>'
            '<div><h2><a href="https://facebook.com/suggestion">Gợi ý</a></h2></div>'
            '<div><a href="https://facebook.com/groups/123">Nhóm</a><button>Tạo bài viết</button>'
            '<button>Quảng cáo</button></div></section>'
            '<div><h2><a href="https://facebook.com/actual">Page thật</a></h2>'
            '<a href="https://facebook.com/actual?view=notification">2 thông báo</a>'
            '<a href="https://facebook.com/actual?create=post">Create a post</a>'
            '<button>Quảng cáo</button></div></main>')
        self.assertEqual(self.page.evaluate(SNAPSHOT_SCRIPT)['items'], [
            dict(name='Page thật', url='https://www.facebook.com/actual', page_id='')])


if __name__ == '__main__':
    unittest.main()
