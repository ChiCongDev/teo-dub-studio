"""11/11/2026: a Page that has a username (Sài Thật Nói Thật = facebook.com/reviewsaithatnoithat) could not be posted to: Facebook opens
the saved number link at the username, and Tèo answered "Tab đã chuyển sang Page khác; chưa bấm Đăng" (and could not confirm a post that was
published). Load Page proves the username of a Page by following its number link and keeps it in the Page record; posting accepts that
username address as that very Page's own, and nothing else. Local fixtures only (no Facebook, no network)."""
import json
from pathlib import Path
import tempfile
import unittest

from teodub.facebook_accounts import FacebookAccounts, username_of
from teodub.facebook_page_identity import _matching_url
from teodub.facebook_web_post import BrowserPagePublisher, _page_id
from tests import test_page_browser_reconcile_dom as reconcile

NUMBER = 'https://www.facebook.com/profile.php?id=61595943214900'
USERNAME = 'https://www.facebook.com/reviewsaithatnoithat'
TARGET = dict(url=NUMBER, page_id='61595943214900', name='Sài Thật Nói Thật')
WITH_NAME = dict(TARGET, username='reviewsaithatnoithat')


class FakeTab:
    def __init__(self, url):
        self.url = url

    def evaluate(self, script):
        return {}


class UsernameUnitTests(unittest.TestCase):
    def test_a_username_address_is_the_target_pages_only_when_its_username_was_proved(self):
        self.assertEqual(_page_id(USERNAME), '')  # Nobody proved it: as before.
        self.assertEqual(_page_id(USERNAME, TARGET), '')
        for url in (USERNAME, USERNAME+'/', 'https://www.facebook.com/ReviewSaiThatNoiThat', 'https://m.facebook.com/reviewsaithatnoithat/'):
            self.assertEqual(_page_id(url, WITH_NAME), '61595943214900', url)

    def test_another_username_a_post_or_a_number_of_another_page_is_never_this_page(self):
        for url in ('https://www.facebook.com/kynangcauca', 'https://www.facebook.com/reviewsaithatnoithat/posts/123',
                    'https://www.facebook.com/reviewsaithatnoithat2', 'https://evil.test/reviewsaithatnoithat'):
            self.assertEqual(_page_id(url, WITH_NAME), '', url)
        self.assertEqual(_page_id('https://www.facebook.com/profile.php?id=999', WITH_NAME), '999')
        self.assertEqual(_page_id(NUMBER, WITH_NAME), '61595943214900')

    def test_a_username_never_works_without_a_saved_number(self):
        self.assertEqual(_page_id(USERNAME, dict(WITH_NAME, page_id='')), '')
        self.assertEqual(_page_id(USERNAME, dict(WITH_NAME, page_id='abc')), '')

    def test_the_tab_check_accepts_the_username_address_of_the_page_and_nothing_else(self):
        BrowserPagePublisher._safe_page(object(), FakeTab(USERNAME+'/'), WITH_NAME)
        BrowserPagePublisher._safe_page(object(), FakeTab(NUMBER), WITH_NAME)
        for tab, target in ((USERNAME, TARGET),  # The username was never proved.
                            ('https://www.facebook.com/kynangcauca', WITH_NAME),
                            ('https://www.facebook.com/profile.php?id=999', WITH_NAME)):
            with self.subTest(tab=tab, username=target.get('username')), self.assertRaisesRegex(ValueError, 'Page khác'):
                BrowserPagePublisher._safe_page(object(), FakeTab(tab), target)

    def test_the_page_switch_row_may_be_the_username_address_of_the_page(self):
        _matching_url(USERNAME+'/', WITH_NAME)
        _matching_url(NUMBER, WITH_NAME)
        for href, target in ((USERNAME, TARGET), ('https://www.facebook.com/kynangcauca', WITH_NAME)):
            with self.subTest(href=href), self.assertRaises(ValueError):
                _matching_url(href, target)

    def test_username_of_reads_only_a_username_address(self):
        self.assertEqual(username_of('https://www.facebook.com/ReviewSaiThatNoiThat/'), 'reviewsaithatnoithat')
        for url in (NUMBER, 'https://www.facebook.com/61595943214900', 'https://www.facebook.com/people/Ten/123/', 'https://www.facebook.com/pages/',
                    'https://www.facebook.com/groups', 'https://www.facebook.com/a', 'https://evil.test/reviewsaithatnoithat', '', 'x'):
            self.assertEqual(username_of(url), '', url)


class UsernameRecordTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = FacebookAccounts(self.temp.name)
        self.account = self.store.add_account('Một')['id']
        self.store.save_page(self.account, 'Review sản phẩm', 'facebook.com/profile.php?id=61595943214900', '61595943214900')

    def page(self):
        return self.store.load()['pages'][0]

    def test_load_page_keeps_the_username_it_proved_and_the_saved_link_stays_the_number(self):
        found = dict(name='Sài Thật Nói Thật', url='facebook.com/reviewsaithatnoithat', page_id='61595943214900')
        self.store.merge_discovered_pages(self.account, [found], rename=lambda record_id, old, new: None)
        page = self.page()
        self.assertEqual((page['username'], page['url']), ('reviewsaithatnoithat', NUMBER))

    def test_a_page_listed_only_by_a_text_link_and_no_number_gets_no_username(self):
        self.store.merge_discovered_pages(self.account, [dict(name='Page chữ', url='facebook.com/pagechu')])
        self.assertTrue(all('username' not in row for row in self.store.load()['pages']))

    def test_another_number_makes_the_old_proof_void_and_other_edits_keep_it(self):
        found = dict(name='Sài Thật Nói Thật', url='facebook.com/reviewsaithatnoithat', page_id='61595943214900')
        self.store.merge_discovered_pages(self.account, [found])
        record = self.page()['id']
        self.store.save_page(self.account, 'Tên khác', 'facebook.com/profile.php?id=61595943214900', '61595943214900', record_id=record)
        self.assertEqual(self.page()['username'], 'reviewsaithatnoithat')
        self.store.save_page(self.account, 'Tên khác', 'facebook.com/profile.php?id=111111', '111111', record_id=record)
        self.assertNotIn('username', self.page())


class UsernameResultTests(unittest.TestCase):
    """The proof that a post was published, read from a result page in a real Chrome (the fixtures of the result inspection)."""
    base = reconcile.BrowserReconcileDomTests
    setUpClass = base.__dict__['setUpClass']
    tearDownClass = base.__dict__['tearDownClass']
    setUp = base.setUp
    video = base.video
    schedule = base.schedule
    submit = base.submit
    attempted = base.attempted
    make_attempt = base.make_attempt
    duplicate_attempt = base.duplicate_attempt
    capture = base.capture
    view = base.view
    result_html = base.result_html
    tab = base.tab
    inspect = base.inspect
    unchanged = base.unchanged

    def at_username(self, saved):
        """The result page shows the owner at the username address; the Page record has `saved` as its proved username (or none)."""
        path = self.directory/'page-publishing'/'accounts.json'
        data = json.loads(path.read_text(encoding='utf-8'))
        for page in data['pages']:
            if page['id'] == self.page:
                page.pop('username', None)
                if saved:
                    page['username'] = saved
        path.write_text(json.dumps(data), encoding='utf-8')
        self.expected = self.view(self.row)
        self.document = self.document.replace('https://www.facebook.com/profile.php?id=100', 'https://www.facebook.com/fixturepage')

    def test_a_published_post_is_confirmed_when_the_owner_is_the_proved_username_of_the_page(self):
        self.at_username('fixturepage')
        result = self.inspect()
        self.assertEqual(result['state'], 'published', result)
        self.assertEqual(result['browser_receipt']['owner_url'], 'https://www.facebook.com/fixturepage')

    def test_the_same_result_is_not_confirmed_when_the_username_was_never_proved(self):
        self.at_username('')
        self.unchanged(self.inspect)

    def test_a_result_owned_by_another_username_is_not_confirmed(self):
        self.at_username('someoneelse')
        self.unchanged(self.inspect)


if __name__ == '__main__':
    unittest.main()
