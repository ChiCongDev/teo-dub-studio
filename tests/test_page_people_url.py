"""30/10/2026: Facebook opens a new Page at facebook.com/people/<name>/<id>/ ; Tèo refused it as "not a Page address".
The Page ID in that address must still equal the saved one. Local fixtures only (no browser, network or Facebook)."""
from pathlib import Path
import tempfile
import unittest

from teodub.facebook_accounts import FacebookAccounts
from teodub.facebook_page_discovery import normalize_items
from teodub.facebook_page_identity import _matching_url
from teodub.facebook_web_post import BrowserPagePublisher, _page_id, _target_url, _where

SAVED = 'https://www.facebook.com/profile.php?id=61595943214900'
PEOPLE = 'https://www.facebook.com/people/Review-s%E1%BA%A3n-ph%E1%BA%A9m/61595943214900/'
PEOPLE_UTF8 = 'https://www.facebook.com/people/Review-sản-phẩm/61595943214900/'
OTHER = 'https://www.facebook.com/people/Review-sản-phẩm/61595000000000/'
TARGET = dict(url=SAVED, page_id='61595943214900', name='Review sản phẩm')


class FakeTab:
    def __init__(self, url):
        self.url = url

    def evaluate(self, script):
        return {}


class PeopleAddressTests(unittest.TestCase):
    def test_the_page_id_is_read_from_the_people_address(self):
        for url in (PEOPLE, PEOPLE_UTF8):
            self.assertEqual(_page_id(_target_url(url)), '61595943214900')
        self.assertEqual(_page_id(SAVED), '61595943214900')

    def test_only_a_people_address_with_a_numeric_id_is_a_page(self):
        for url in ('https://www.facebook.com/people/Ten/abc/', 'https://www.facebook.com/people/',
                    'https://www.facebook.com/people', 'https://www.facebook.com/people/Ten/123/extra/'):
            with self.assertRaises(ValueError, msg=url):
                _target_url(url)

    def test_the_old_address_shapes_still_work(self):
        for url in (SAVED, 'https://www.facebook.com/tenpage', 'https://www.facebook.com/pages/Ten/123'):
            self.assertEqual(_target_url(url), url)

    def test_the_tab_check_accepts_the_same_page_in_the_people_shape(self):
        BrowserPagePublisher._safe_page(object(), FakeTab(PEOPLE), TARGET)

    def test_the_tab_check_still_refuses_another_page_id(self):
        with self.assertRaisesRegex(ValueError, 'Page khác'):
            BrowserPagePublisher._safe_page(object(), FakeTab(OTHER), TARGET)

    def test_a_tab_that_left_the_page_says_so_and_names_the_path_only(self):
        with self.assertRaises(ValueError) as caught:
            BrowserPagePublisher._safe_page(object(), FakeTab('https://www.facebook.com/watch/?v=999&token=bí-mật'), TARGET)
        message = str(caught.exception)
        self.assertIn('chuyển tab sang một trang không phải Page', message)
        self.assertIn('/watch', message)
        self.assertNotIn('bí-mật', message)
        self.assertNotIn('999', message)
        self.assertNotIn('Link đã lưu', message)

    def test_where_keeps_the_path_short_and_without_a_query(self):
        self.assertEqual(_where('https://www.facebook.com/a/b?x=1#y'), '/a/b')
        self.assertEqual(len(_where('https://www.facebook.com/' + 'a' * 500)), 80)

    def test_the_page_switch_row_check_uses_the_page_id_of_a_people_link(self):
        _matching_url(PEOPLE, TARGET)
        _matching_url(PEOPLE_UTF8, TARGET)
        with self.assertRaisesRegex(ValueError, 'khác cấu hình'):
            _matching_url(OTHER, TARGET)

    def test_a_people_link_can_be_saved_as_a_page(self):
        with tempfile.TemporaryDirectory() as temp:
            store = FacebookAccounts(Path(temp) / 'dữ liệu')
            account = store.add_account('Một')
            saved = store.save_page_link(account['id'], PEOPLE_UTF8)
            self.assertEqual((saved['name'], saved['page_id']), ('Review sản phẩm', '61595943214900'))
            with self.assertRaises(ValueError):
                store.save_page_link(account['id'], 'https://www.facebook.com/people/Ten/abc/')

    def test_discovery_keeps_a_people_page_and_its_id(self):
        rows = normalize_items([dict(name='Review sản phẩm', url=PEOPLE_UTF8),
                                dict(name='Không phải Page', url='https://www.facebook.com/people/')])
        self.assertEqual([(row['name'], row['page_id']) for row in rows], [('Review sản phẩm', '61595943214900')])


if __name__ == '__main__':
    unittest.main()
