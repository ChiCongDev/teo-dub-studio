"""14/11/2026: Facebook opened the Page in Tèo's Chrome at web.facebook.com (the Page was fine) and Tèo stopped every post after 2 seconds with
"Facebook yêu cầu đăng nhập/xác minh hoặc đã chuyển khỏi Page". web.facebook.com is Facebook's own address and counts like www.facebook.com; the
checks of the Page (ID, name, username), the login/checkpoint pages and every other host stay as they were. Local fixtures only."""
import unittest

from teodub.facebook_accounts import facebook_url, username_of
from teodub.facebook_page_discovery import _first_segment
from teodub.facebook_page_identity import HOSTS, _matching_url, _safe
from teodub.facebook_web_post import BrowserPagePublisher, _page_id, _receipt_url_parts

NUMBER = 'https://web.facebook.com/profile.php?id=61594903914921&_rdc=1&_rdr#'
TARGET = dict(url='https://www.facebook.com/profile.php?id=61594903914921', page_id='61594903914921', name='Kỹ Năng Câu Cá')
WITH_NAME = dict(TARGET, username='kynangcauca')


class FakeTab:
    def __init__(self, url):
        self.url = url

    def evaluate(self, script):
        return {}

    def locator(self, selector):
        return self

    def count(self):
        return 0


class WebHostTests(unittest.TestCase):
    def test_the_tab_check_accepts_the_page_at_web_facebook_com(self):
        BrowserPagePublisher._safe_page(object(), FakeTab(NUMBER), TARGET)
        BrowserPagePublisher._safe_page(object(), FakeTab('https://web.facebook.com/kynangcauca/'), WITH_NAME)

    def test_the_login_and_check_pages_are_still_refused_on_every_host(self):
        for host in ('web.facebook.com', 'www.facebook.com'):
            for path in ('/login/?next=x', '/checkpoint/1', '/recover/initiate', '/two_step_verification/authentication'):
                with self.subTest(host=host, path=path), self.assertRaisesRegex(ValueError, 'đăng nhập/xác minh'):
                    BrowserPagePublisher._safe_page(object(), FakeTab(f'https://{host}{path}'), TARGET)

    def test_other_hosts_are_still_refused(self):
        for url in ('https://evil.test/profile.php?id=61594903914921', 'https://web.facebook.com.evil.test/x', 'https://webfacebook.com/x',
                    'https://business.facebook.com/x', 'https://l.facebook.com/l.php?u=x'):
            with self.subTest(url=url), self.assertRaisesRegex(ValueError, 'đăng nhập/xác minh'):
                BrowserPagePublisher._safe_page(object(), FakeTab(url), TARGET)

    def test_web_facebook_com_never_makes_another_page_this_one(self):
        with self.assertRaisesRegex(ValueError, 'Page khác'):
            BrowserPagePublisher._safe_page(object(), FakeTab('https://web.facebook.com/profile.php?id=999'), TARGET)
        with self.assertRaisesRegex(ValueError, 'Page khác'):
            BrowserPagePublisher._safe_page(object(), FakeTab('https://web.facebook.com/someoneelse'), WITH_NAME)

    def test_page_ids_and_usernames_read_the_same_at_web_facebook_com(self):
        self.assertEqual(_page_id(NUMBER), '61594903914921')
        self.assertEqual(_page_id('https://web.facebook.com/kynangcauca', WITH_NAME), '61594903914921')
        self.assertEqual(_page_id('https://web.facebook.com/kynangcauca'), '')  # The username is still proved by Load Page only.
        self.assertEqual(_page_id('https://web.facebook.com/another', WITH_NAME), '')
        self.assertEqual(username_of('https://web.facebook.com/KyNangCauCa/'), 'kynangcauca')
        self.assertEqual(username_of('https://web.facebook.com/profile.php?id=61594903914921'), '')

    def test_a_saved_or_found_link_is_written_back_at_www(self):
        self.assertEqual(facebook_url(NUMBER, page=True), 'https://www.facebook.com/profile.php?id=61594903914921')
        self.assertEqual(facebook_url('web.facebook.com/kynangcauca', page=True), 'https://www.facebook.com/kynangcauca')

    def test_the_page_switch_row_and_the_receipt_addresses_accept_the_host(self):
        _matching_url(NUMBER, TARGET)
        _matching_url('https://web.facebook.com/kynangcauca', WITH_NAME)
        with self.assertRaises(ValueError):
            _matching_url('https://web.facebook.com/profile.php?id=999', TARGET)
        self.assertIsNotNone(_receipt_url_parts('https://web.facebook.com/reel/123'))
        self.assertIsNone(_receipt_url_parts('https://web.facebook.com.evil.test/reel/123'))
        self.assertIn('web.facebook.com', HOSTS)

    def test_load_page_reads_the_first_segment_at_web_facebook_com(self):
        self.assertEqual(_first_segment('https://web.facebook.com/kynangcauca/'), 'kynangcauca')
        self.assertEqual(_first_segment('https://evil.test/kynangcauca'), '')

    def test_the_account_menu_check_accepts_the_host_and_refuses_the_login_page(self):
        _safe(FakeTab(NUMBER))
        with self.assertRaisesRegex(ValueError, 'đăng nhập/xác minh'):
            _safe(FakeTab('https://web.facebook.com/login/'))


if __name__ == '__main__':
    unittest.main()
