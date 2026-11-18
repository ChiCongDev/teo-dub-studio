"""Page ID read from the Page's own screen (18/11/2026): a username Page with no saved Page to probe with.

Layout of the fixture = what the real Facebook showed for 'Mẹ Thiên Nhiên' on 18/11 (probe, read only): the profile-form number sits
right after facebook.com/ in the ad-centre link and in the picture set "pb.<ID>."; the older number is after page_id= / asset_id=.
"""
import threading
import unittest
from unittest.mock import Mock

import test_facebook_page_discovery as discovery
import test_facebook_page_cards_js as cards
import test_facebook_accounts as accounts
from teodub.common import Cancelled
from teodub.facebook_page_discovery import PROFILE_IDS_SCRIPT, read_ids_from_pages

USERNAME_URL = 'https://www.facebook.com/vevoimethiennhien'
NEW_ID = '61596474600079'
OLD_ID = '1384993184704724'


class ScreenIdTests(unittest.TestCase):
    setUp = discovery.FacebookDiscoveryTests.setUp
    advance = discovery.FacebookDiscoveryTests.advance
    scan = discovery.FacebookDiscoveryTests.scan

    def screen(self, found, landings, *, screen_url=USERNAME_URL+'/'):
        """The extra tab: opening the Page shows `found` numbers; each profile.php?id= opening lands on the next of `landings`."""
        tab = Mock()
        tab.url = ''
        tab.opened = []
        queue = iter(landings)

        def goto(url, **kwargs):
            tab.opened.append(url)
            tab.url = next(queue) if 'profile.php?id=' in url else screen_url
        tab.goto.side_effect = goto
        tab.evaluate.side_effect = lambda script: found if script == PROFILE_IDS_SCRIPT else None
        tab.wait_for_timeout.side_effect = self.advance
        self.context.new_page.side_effect = [self.page, tab]
        self.snapshot['items'] = [dict(name='Mẹ Thiên Nhiên', url=USERNAME_URL)]
        return tab

    def test_number_that_lands_on_the_username_becomes_the_page_id(self):
        tab = self.screen([NEW_ID], [USERNAME_URL+'/'])
        row = self.scan()['items'][0]
        self.assertEqual(row['page_id'], NEW_ID)
        self.assertEqual(tab.opened, [USERNAME_URL, 'https://www.facebook.com/profile.php?id='+NEW_ID])  # Read only: two openings.
        tab.close.assert_called_once()

    def test_noise_numbers_that_land_on_other_people_are_ignored(self):
        self.screen(['100090657532346', NEW_ID, '3711564432316231'],
                    ['https://www.facebook.com/le.ba.dan.2024/', USERNAME_URL+'/', 'https://www.facebook.com/profile.php?id=3711564432316231'])
        self.assertEqual(self.scan()['items'][0]['page_id'], NEW_ID)

    def test_no_number_verified_keeps_the_page_without_an_id(self):
        self.screen([NEW_ID], ['https://www.facebook.com/profile.php?id='+NEW_ID])
        result = self.scan()
        self.assertEqual(result['items'][0]['page_id'], '')
        self.assertIn('không số nào xác minh được', '\n'.join(result['log']))
        self.assertIn('Sửa Page', result['message'])  # The old hand-fix hint stays.

    def test_two_numbers_for_the_same_page_are_not_chosen(self):
        self.screen([NEW_ID, '61596474600080'], [USERNAME_URL+'/', USERNAME_URL+'/'])
        result = self.scan()
        self.assertEqual(result['items'][0]['page_id'], '')
        self.assertIn('nhiều số', '\n'.join(result['log']))

    def test_screen_that_is_not_the_page_is_not_read(self):
        tab = self.screen([NEW_ID], [USERNAME_URL+'/'], screen_url='https://www.facebook.com/login/?next=x')
        self.assertEqual(self.scan()['items'][0]['page_id'], '')
        self.assertEqual(tab.opened, [USERNAME_URL])

    def test_page_that_cannot_be_opened_changes_nothing(self):
        tab = self.screen([NEW_ID], [])
        tab.goto.side_effect = RuntimeError('net::ERR_TIMED_OUT')
        self.assertEqual(self.scan()['items'][0]['page_id'], '')
        tab.close.assert_called_once()

    def test_page_that_already_has_an_id_opens_nothing(self):
        self.snapshot['items'] = [dict(name='Kỹ Năng Câu Cá', url='https://www.facebook.com/profile.php?id=123')]
        self.assertEqual(self.scan()['items'][0]['page_id'], '123')
        self.assertEqual(self.context.new_page.call_count, 1)  # Only the list tab.

    def test_stop_request_closes_the_tab_and_raises(self):
        tab = self.screen([NEW_ID], [USERNAME_URL+'/'])
        self.context.new_page.side_effect = [tab]  # Called directly: the first tab it opens is this one.
        stop = threading.Event()
        stop.set()
        with self.assertRaises(Cancelled):
            read_ids_from_pages(self.context, [dict(name='x', url=USERNAME_URL, page_id='')], cancel=stop)
        self.assertEqual(tab.opened, [])
        tab.close.assert_called_once()

    def test_no_spare_tab_keeps_the_scan(self):
        self.context.new_page.side_effect = [self.page, RuntimeError('no tab')]
        self.snapshot['items'] = [dict(name='Mẹ Thiên Nhiên', url=USERNAME_URL)]
        self.assertEqual(self.scan()['items'][0]['page_id'], '')


@unittest.skipIf(cards.sync_playwright is None, 'playwright is not installed')
class ScreenScriptTests(unittest.TestCase):
    setUpClass = classmethod(cards.PageCardTests.setUpClass.__func__)
    tearDownClass = classmethod(cards.PageCardTests.tearDownClass.__func__)

    def read(self, body):
        page = self.browser.new_page()
        self.addCleanup(page.close)
        page.route('**/*', lambda route: route.abort())
        page.set_content('<main>'+body+'</main>')
        return page.evaluate(PROFILE_IDS_SCRIPT)

    def test_real_layout_gives_only_the_profile_form_number(self):
        found = self.read(
            f'<a href="https://www.facebook.com/{NEW_ID}/ad_center/">Trung tâm quảng cáo</a>'
            f'<a href="https://www.facebook.com/ad_center/create/consolidatedad/?entry_point=x&page_id={OLD_ID}">Tạo quảng cáo</a>'
            f'<a href="https://www.facebook.com/latest/?asset_id={OLD_ID}&nav_ref=x">Meta Business Suite</a>'
            f'<a href="https://www.facebook.com/photo/?fbid=122097952149549153&set=pb.{NEW_ID}.-2207520000"></a>')
        self.assertEqual(found, [NEW_ID])

    def test_ad_centre_first_then_picture_set_then_other_links_and_nothing_foreign(self):
        found = self.read(
            '<a href="https://www.facebook.com/100090657532346/posts">Một người</a>'
            f'<a href="https://www.facebook.com/photo/?fbid=1&set=pb.{NEW_ID}.-1"></a>'
            '<a href="https://www.facebook.com/100036857140548">Chỉ một đoạn</a>'
            '<a href="https://example.com/777777777/ad_center/">Không phải Facebook</a>'
            '<a href="https://user:pw@www.facebook.com/888888888/ad_center/">Có mật khẩu trong link</a>'
            f'<a href="/{NEW_ID}/ad_center/">Trung tâm quảng cáo</a>')
        self.assertEqual(found, [NEW_ID, '100090657532346'])  # The picture set and the ad centre agree; single-segment links are skipped.


class AccountMergeTests(accounts.FacebookAccountTests):
    """Load Page that now brings the number: a saved Page without an ID gets it, and the address is remembered as its username."""

    def run(self, result=None):  # Only the tests below, not the inherited ones.
        if self._testMethodName.startswith('test_merge_'):
            return super().run(result)
        return unittest.TestResult() if result is None else result

    def found(self, page_id):
        return [dict(name='Mẹ Thiên Nhiên', url=USERNAME_URL, page_id=page_id)]

    def test_merge_gives_a_text_link_page_its_id_and_username(self):
        account = self.store.add_account('Nick fb F17')
        self.store.merge_discovered_pages(account['id'], self.found(''))
        before = [p for p in self.store.load()['pages'] if p['account_id'] == account['id']]
        self.assertEqual(before[0]['page_id'], '')
        self.store.merge_discovered_pages(account['id'], self.found(NEW_ID))
        pages = [p for p in self.store.load()['pages'] if p['account_id'] == account['id']]
        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0]['page_id'], NEW_ID)
        self.assertEqual(pages[0]['username'], 'vevoimethiennhien')

    def test_merge_does_not_replace_an_id_the_owner_saved(self):
        account = self.store.add_account('Nick fb F17')
        self.store.save_page(account['id'], 'Mẹ Thiên Nhiên', USERNAME_URL, '999999')
        with self.assertRaises(ValueError):
            self.store.merge_discovered_pages(account['id'], self.found(NEW_ID))
        self.assertEqual(self.store.load()['pages'][0]['page_id'], '999999')

    def test_merge_gives_a_hand_added_page_without_an_id_its_username_too(self):
        account = self.store.add_account('Nick fb F17')
        self.store.save_page(account['id'], 'Mẹ Thiên Nhiên', USERNAME_URL)
        self.store.merge_discovered_pages(account['id'], self.found(NEW_ID))
        row = self.store.load()['pages'][0]
        self.assertEqual((row['page_id'], row.get('username')), (NEW_ID, 'vevoimethiennhien'))


if __name__ == '__main__':
    unittest.main()
