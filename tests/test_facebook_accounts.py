import json
from pathlib import Path
import tempfile
import unittest

from teodub.facebook_accounts import FACEBOOK_HOME, FACEBOOK_PAGES, BUSINESS_SUITE, FacebookAccounts, facebook_url
from teodub.pipeline import _job_lock


class FacebookAccountTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = FacebookAccounts(Path(self.temp.name)/'dữ liệu')

    def test_empty_registry_is_passive(self):
        self.assertEqual(self.store.load()['accounts'], [])
        self.assertFalse(self.store.directory.exists())

    def test_one_account_many_pages_and_reopen_selection(self):
        account = self.store.add_account('Facebook của tôi')
        first = self.store.save_page(account['id'], 'Page câu cá', 'https://facebook.com/cauca/?ref=share', '123')
        second = self.store.save_page(account['id'], 'Page đèn', 'https://www.facebook.com/den')
        reopened = FacebookAccounts(self.store.directory.parent)
        saved = reopened.load()
        self.assertEqual(saved['selected_account_id'], account['id'])
        self.assertEqual([row['id'] for row in saved['pages']], [first['id'], second['id']])
        self.assertTrue(all(row['account_id'] == account['id'] for row in saved['pages']))
        self.assertEqual(saved['pages'][0]['url'], 'https://www.facebook.com/cauca')
        self.assertEqual(set(saved), {'version', 'accounts', 'pages', 'selected_account_id'})

    def test_account_profiles_are_separate_and_rename_keeps_path(self):
        one = self.store.add_account('Một')
        two = self.store.add_account('Hai')
        profile = self.store.profile_directory(one['id'])
        self.assertNotEqual(profile, self.store.profile_directory(two['id']))
        self.store.rename_account(one['id'], 'Đổi tên')
        self.assertEqual(self.store.profile_directory(one['id']), profile)
        self.assertIn(one['id'], str(profile))
        self.store.select_account(one['id'])
        self.assertEqual(self.store.load()['selected_account_id'], one['id'])

    def test_duplicate_account_and_page_do_not_create_extra_records(self):
        account = self.store.add_account('Nick chính')
        with self.assertRaisesRegex(ValueError, 'đã có'):
            self.store.add_account('nick CHÍNH')
        self.store.save_page(account['id'], 'Một', 'facebook.com/PageA', '100')
        with self.assertRaisesRegex(ValueError, 'đã có'):
            self.store.save_page(account['id'], 'Hai', 'https://m.facebook.com/PageA?ref=shared')
        with self.assertRaisesRegex(ValueError, 'đã có'):
            self.store.save_page(account['id'], 'Hai', 'facebook.com/PageB', '100')
        self.assertEqual(len(self.store.load()['accounts']), 1)
        self.assertEqual(len(self.store.load()['pages']), 1)

    def test_same_page_can_be_saved_for_two_accounts_and_edit_is_scoped(self):
        one = self.store.add_account('Một')
        two = self.store.add_account('Hai')
        first = self.store.save_page(one['id'], 'Câu cá', 'facebook.com/cauca', '123')
        self.store.save_page(two['id'], 'Câu cá', 'facebook.com/cauca', '123')
        with self.assertRaisesRegex(ValueError, 'Không còn Page'):
            self.store.save_page(two['id'], 'Sai', 'facebook.com/khac', record_id=first['id'])
        with self.assertRaisesRegex(ValueError, 'Không còn Page'):
            self.store.remove_page(two['id'], first['id'])
        revised = self.store.save_page(one['id'], 'Tên mới', 'facebook.com/cauca', '123', record_id=first['id'])
        self.assertEqual(revised['created_at'], first['created_at'])
        self.store.remove_page(one['id'], first['id'])
        self.assertEqual(len(self.store.load()['pages']), 1)
        self.assertEqual(self.store.load()['pages'][0]['account_id'], two['id'])

    def test_corrupt_registry_is_preserved_and_not_reset(self):
        self.store.directory.mkdir(parents=True)
        self.store.path.write_text('{broken', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'không ghi đè'):
            self.store.add_account('Nick')
        self.assertEqual(self.store.path.read_text(encoding='utf-8'), '{broken')

    def test_orphan_page_or_profile_path_injection_is_rejected(self):
        account = self.store.add_account('Một')
        with self.assertRaises(ValueError):
            self.store.profile_directory('../chrome')
        saved = self.store.load()
        saved['pages'] = [dict(id='a'*32, account_id='b'*32, name='Orphan', url='facebook.com/page')]
        self.store.path.write_text(json.dumps(saved), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'không ghi đè'):
            self.store.profile_directory(account['id'])

    def test_registry_lock_prevents_lost_updates(self):
        self.store.add_account('Một')
        with _job_lock(self.store.directory/'registry-lock'):
            with self.assertRaises(ValueError):
                self.store.add_account('Hai')
        self.assertEqual(len(self.store.load()['accounts']), 1)
        self.store.add_account('Hai')
        self.assertEqual(len(self.store.load()['accounts']), 2)

    def test_names_page_ids_and_unknown_accounts_validate_before_save(self):
        for name in ('', 'X\nY', 'x'*151):
            with self.assertRaises(ValueError):
                self.store.add_account(name)
        one = self.store.add_account('Một')
        with self.assertRaisesRegex(ValueError, 'chữ số'):
            self.store.save_page(one['id'], 'Page', 'facebook.com/page', 'abc')
        with self.assertRaisesRegex(ValueError, 'Không còn tài khoản'):
            self.store.save_page('0'*32, 'Page', 'facebook.com/page')
        self.assertEqual(self.store.load()['pages'], [])

    def test_facebook_urls_only_and_tracking_is_removed(self):
        self.assertEqual(facebook_url(FACEBOOK_HOME), FACEBOOK_HOME)
        self.assertEqual(facebook_url(BUSINESS_SUITE), BUSINESS_SUITE)
        self.assertEqual(facebook_url('https://m.facebook.com/profile.php?id=123&ref=share', page=True),
                         'https://www.facebook.com/profile.php?id=123')
        for url in ('javascript:alert(1)', 'https://facebook.com.evil.example/page', 'file:///C:/test',
                    'https://user:secret@facebook.com/page', 'https://facebook.com:999/page',
                    'https://evil.example@facebook.com/page', 'https://facebook.com\\@evil.example/page',
                    'https://facebook.com/\npage'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                facebook_url(url)
        for url in (FACEBOOK_HOME, 'facebook.com/login.php', BUSINESS_SUITE, 'facebook.com/profile.php'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                facebook_url(url, page=True)

    def test_managed_list_url_keeps_category_only(self):
        self.assertEqual(facebook_url(FACEBOOK_PAGES+'&ref=bookmarks'), FACEBOOK_PAGES)

    def test_discovery_repeats_merge_once_per_page_and_preserve_previous_rows(self):
        account = self.store.add_account('Một')
        old = self.store.save_page(account['id'], 'Page cũ', 'facebook.com/old')
        profile = self.store.profile_directory(account['id'])
        profile.mkdir(parents=True)
        marker = profile/'fixture'
        marker.write_text('keep')
        found = dict(name='Kỹ Năng Câu Cá', url='facebook.com/profile.php?id=123', page_id='123')
        result = self.store.merge_discovered_pages(account['id'], [found, found], message='Có thể chưa đủ', status='partial')
        self.assertEqual(result, dict(added=1, updated=0, read_count=1))
        saved = self.store.load()
        row_id = saved['pages'][1]['id']
        created = saved['pages'][1]['created_at']
        result = self.store.merge_discovered_pages(account['id'], [found])
        self.assertEqual(result, dict(added=0, updated=1, read_count=1))
        reopened = FacebookAccounts(self.store.directory.parent).load()
        self.assertEqual([row['id'] for row in reopened['pages']], [old['id'], row_id])
        self.assertEqual(reopened['pages'][1]['created_at'], created)
        self.assertEqual(marker.read_text(), 'keep')

    def test_discovery_page_id_matches_new_url_and_refreshes_only_automatic_names(self):
        account = self.store.add_account('Một')
        self.store.merge_discovered_pages(account['id'], [dict(name='Tên cũ', url='facebook.com/old', page_id='123')])
        original = self.store.load()['pages'][0]
        self.store.merge_discovered_pages(account['id'], [dict(name='Tên mới', url='facebook.com/new', page_id='123')])
        changed = self.store.load()['pages'][0]
        self.assertEqual((changed['id'], changed['name'], changed['url']),
                         (original['id'], 'Tên mới', 'https://www.facebook.com/new'))
        self.store.save_page(account['id'], 'Tên do tôi đặt', 'facebook.com/custom', '123', record_id=original['id'])
        self.store.merge_discovered_pages(account['id'], [dict(name='Tên từ Facebook', url='facebook.com/newer', page_id='123')])
        manual = self.store.load()['pages'][0]
        self.assertEqual((manual['name'], manual['url'], manual['source']),
                         ('Tên do tôi đặt', 'https://www.facebook.com/custom', 'manual'))
        self.assertEqual(manual['discovered_name'], 'Tên từ Facebook')

    def test_discovery_stays_in_account_scope_and_retains_manual_label_selection_and_extensions(self):
        one = self.store.add_account('Một')
        two = self.store.add_account('Hai')
        manual = self.store.save_page(one['id'], 'Tên tôi đặt', 'facebook.com/Page')
        other = self.store.save_page(two['id'], 'Tên của Hai', 'facebook.com/Page', '123')
        saved = self.store.load()
        saved['pages'][0]['future_settings'] = {'keep': True}
        self.store.path.write_text(json.dumps(saved), encoding='utf-8')
        self.store.merge_discovered_pages(one['id'], [dict(name='Tên Facebook', url='facebook.com/Page', page_id='123')],
                                           message='Chỉ phần đã tải', status='partial')
        reopened = self.store.load()
        self.assertEqual(reopened['selected_account_id'], two['id'])
        self.assertEqual(reopened['pages'][1], other)
        self.assertEqual(reopened['pages'][0]['id'], manual['id'])
        self.assertEqual(reopened['pages'][0]['name'], 'Tên tôi đặt')
        self.assertEqual(reopened['pages'][0]['page_id'], '123')
        self.assertEqual(reopened['pages'][0]['future_settings'], {'keep': True})
        self.assertEqual(reopened['accounts'][0]['page_load']['status'], 'partial')

    def test_discovery_conflicts_invalid_input_and_registry_lock_are_atomic(self):
        account = self.store.add_account('Một')
        self.store.save_page(account['id'], 'Một', 'facebook.com/one', '100')
        self.store.save_page(account['id'], 'Hai', 'facebook.com/two', '200')
        before = self.store.path.read_bytes()
        new = dict(name='Mới', url='facebook.com/new', page_id='300')
        for invalid in ([new, dict(name='Xung đột', url='facebook.com/one', page_id='200')],
                        [new, dict(name='ID khác', url='facebook.com/one', page_id='999')],
                        [new, dict(name='Sai', url='https://evil.test', page_id='400')], [None]):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.store.merge_discovered_pages(account['id'], invalid)
            self.assertEqual(self.store.path.read_bytes(), before)
        with _job_lock(self.store.directory/'registry-lock'), self.assertRaises(ValueError):
            self.store.merge_discovered_pages(account['id'], [new])
        self.assertEqual(self.store.path.read_bytes(), before)

    def test_empty_partial_merge_never_deletes_saved_pages(self):
        account = self.store.add_account('Một')
        row = self.store.save_page(account['id'], 'Một', 'facebook.com/one', '100')
        self.store.merge_discovered_pages(account['id'], [], message='Đọc chưa đủ', status='partial')
        self.assertEqual(self.store.load()['pages'], [row])
        with self.assertRaises(ValueError):
            self.store.merge_discovered_pages(account['id'], [], status='all_pages_verified')

    def test_save_page_link_derives_id_and_local_label_without_fetching_facebook(self):
        account = self.store.add_account('Một')
        for url, name, page_id in [
            ('https://m.facebook.com/profile.php?id=123&ref=bookmarks', 'Page 123', '123'),
            ('facebook.com/456', 'Page 456', '456'),
            ('facebook.com/pages/Nhat-Tam-Review/789/', 'Nhat Tam Review', '789'),
            ('facebook.com/KyNangCauCa?ref=share', 'KyNangCauCa', '')]:
            saved = self.store.save_page_link(account['id'], url)
            self.assertEqual((saved['name'], saved['page_id'], saved['source']), (name, page_id, 'manual'))
        reopened = FacebookAccounts(self.store.directory.parent).load()
        self.assertEqual(len(reopened['pages']), 4)
        self.assertEqual(reopened['pages'][0]['url'], 'https://www.facebook.com/profile.php?id=123')

    def test_save_page_link_retains_entered_name_and_deduplicates_with_discovered_pages(self):
        account = self.store.add_account('Một')
        other = self.store.add_account('Hai')
        row = self.store.save_page_link(account['id'], 'facebook.com/profile.php?id=123', 'Kỹ Năng Câu Cá')
        before = self.store.path.read_bytes()
        with self.assertRaisesRegex(ValueError, 'đã có'):
            self.store.save_page_link(account['id'], 'facebook.com/pages/Name/123', 'Tên khác')
        self.assertEqual(self.store.path.read_bytes(), before)
        self.store.merge_discovered_pages(account['id'], [dict(name='Tên từ FB', url='facebook.com/vanity', page_id='123')])
        self.assertEqual(self.store.load()['pages'][0]['name'], row['name'])
        self.assertEqual(self.store.load()['pages'][0]['url'], row['url'])
        self.store.save_page_link(other['id'], 'facebook.com/profile.php?id=123', 'Page của Hai')
        self.assertEqual(len(self.store.load()['pages']), 2)

    def test_save_page_link_rejects_navigation_post_and_group_links_before_writing(self):
        account = self.store.add_account('Một')
        before = self.store.path.read_bytes()
        for url in ('', 'https://evil.test/page', FACEBOOK_PAGES, 'facebook.com/friends',
                    'facebook.com/groups/123', 'facebook.com/Name/posts/123',
                    'facebook.com/share/abc', 'facebook.com/profile.php?id=abc'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                self.store.save_page_link(account['id'], url)
            self.assertEqual(self.store.path.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
