"""Browser delivery persistence tests using temporary, synthetic local records."""
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from teodub.facebook_accounts import FacebookAccounts
from teodub.page_activity import PageActivity


class BrowserStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.accounts = FacebookAccounts(self.directory)
        self.account = self.accounts.add_account('Synthetic browser account')['id']
        self.page = self.accounts.save_page(self.account, 'Synthetic Page', 'facebook.com/syntheticpage', '100')['id']
        self.other = self.accounts.add_account('Synthetic other account')['id']
        self.other_page = self.accounts.save_page(self.other, 'Other Page', 'facebook.com/otherpage', '200')['id']
        self.store = PageActivity(self.directory)
        self.snapshot = {'page_id': '100', 'page_name': 'Synthetic Page',
                         'body': 'Nội dung đã quan sát', 'media': [{'name': 'ảnh.png'}]}

    def draft(self, **kwargs):
        return self.store.save_draft(self.account, self.page, 'Bài thử ngoại tuyến', **kwargs)

    def state(self, row, state):
        kwargs = {'external_url': 'https://www.facebook.com/syntheticpage/posts/77', 'external_id': '100_77'} if state == 'published' else {}
        return self.store.transition(self.account, self.page, row['id'], state, 'Synthetic '+state, **kwargs)

    def review(self, **kwargs):
        row = self.draft(delivery_mode='browser', **kwargs)
        self.state(row, 'preparing')
        return self.store.save_browser_review(self.account, self.page, row['id'], self.snapshot)

    def test_legacy_schema_migrates_to_api_without_changing_evidence_or_posting(self):
        self.store.path.parent.mkdir(parents=True, exist_ok=True)
        post_id = 'c'*32
        with closing(sqlite3.connect(self.store.path)) as conn, conn:
            conn.execute('''CREATE TABLE posts (
                id TEXT PRIMARY KEY, account_id TEXT NOT NULL, page_key TEXT NOT NULL,
                target_name TEXT NOT NULL, target_url TEXT NOT NULL, target_id TEXT NOT NULL,
                body TEXT NOT NULL, kind TEXT NOT NULL, media TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL, state TEXT NOT NULL,
                message TEXT NOT NULL DEFAULT '', external_url TEXT NOT NULL DEFAULT '',
                external_id TEXT NOT NULL DEFAULT '', published_at TEXT NOT NULL DEFAULT '',
                attempt INTEGER NOT NULL DEFAULT 0, archived INTEGER NOT NULL DEFAULT 0)''')
            conn.execute('INSERT INTO posts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (post_id, self.account, self.page, 'Old Page', 'https://www.facebook.com/syntheticpage', '100',
                 'Bản nháp trước nâng cấp', 'text', '[]', 'old-created', 'old-updated', 'draft',
                 'Old message', '', '', '', 0, 0))
        row = self.store.post(self.account, self.page, post_id)
        self.assertEqual((row['delivery_mode'], row['browser_finish_mode']), ('api', 'manual'))
        self.assertEqual((row['browser_review'], row['browser_receipt'], row['manual_url']), ({}, {}, ''))
        self.assertEqual((row['remote_assets'], row['metrics']), ([], {}))
        self.assertEqual((row['body'], row['state'], row['attempt'], row['updated_at']),
                         ('Bản nháp trước nâng cấp', 'draft', 0, 'old-updated'))
        self.assertEqual(self.store.events(self.account, self.page, post_id), [])
        self.assertEqual(PageActivity(self.directory).post(self.account, self.page, post_id), row)

    def test_new_delivery_modes_and_browser_finish_modes_survive_reopen_and_edit(self):
        api = self.draft()
        self.assertEqual((api['delivery_mode'], api['browser_finish_mode']), ('api', 'manual'))
        for finish_mode in ('manual', 'tool'):
            with self.subTest(finish_mode=finish_mode):
                row = self.draft(delivery_mode='browser', browser_finish_mode=finish_mode)
                reopened = PageActivity(self.directory)
                edited = reopened.save_draft(self.account, self.page, 'Nội dung sửa', post_id=row['id'])
                self.assertEqual((edited['delivery_mode'], edited['browser_finish_mode']), ('browser', finish_mode))
                self.assertEqual((edited['state'], edited['attempt']), ('draft', 0))
                self.assertEqual(edited['body'], 'Nội dung sửa')

    def test_invalid_delivery_settings_do_not_create_or_change_a_draft(self):
        row = self.draft(delivery_mode='browser', browser_finish_mode='tool')
        for kwargs in ({'delivery_mode': 'automatic'}, {'delivery_mode': ''},
                       {'browser_finish_mode': 'automatic'}, {'browser_finish_mode': ''}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    self.draft(**kwargs)
                with self.assertRaises(ValueError):
                    self.store.save_draft(self.account, self.page, 'Changed', post_id=row['id'], **kwargs)
        self.assertEqual(self.store.posts(self.account, self.page), [row])

    def test_api_uploaded_assets_block_changing_delivery_mode(self):
        row = self.draft()
        self.state(row, 'preparing')
        self.store.record_assets(self.account, self.page, row['id'], ['synthetic-upload-id'])
        before = self.state(row, 'failed')
        with self.assertRaises(ValueError):
            self.store.save_draft(self.account, self.page, 'Changed', post_id=row['id'], delivery_mode='browser')
        self.assertEqual(self.store.post(self.account, self.page, row['id']), before)

    def test_review_is_one_immutable_persisted_snapshot(self):
        row = self.review()
        self.assertEqual(row['state'], 'review')
        self.assertEqual(row['browser_review'], self.snapshot)
        row['browser_review']['media'][0]['name'] = 'Caller mutation'
        self.snapshot['media'][0]['name'] = 'Input mutation'
        before = self.store.post(self.account, self.page, row['id'])
        self.assertEqual(before['browser_review']['media'][0]['name'], 'ảnh.png')
        before_events = self.store.events(self.account, self.page, row['id'])
        with self.assertRaises(ValueError):
            self.store.save_browser_review(self.account, self.page, row['id'], {'body': 'Replacement'})
        self.assertEqual(PageActivity(self.directory).post(self.account, self.page, row['id']), before)
        self.assertEqual(self.store.events(self.account, self.page, row['id']), before_events)
        self.state(row, 'failed')
        retry = self.state(row, 'preparing')
        with self.assertRaises(ValueError):
            self.store.save_browser_review(self.account, self.page, row['id'], {'body': 'Replacement'})
        self.assertEqual(self.store.post(self.account, self.page, row['id']), retry)

    def test_review_requires_unarchived_preparing_browser_post_and_correct_scope(self):
        for mode, state, archived in (('api', 'preparing', False), ('browser', 'draft', False),
                                      ('browser', 'unknown', False), ('browser', 'preparing', True)):
            with self.subTest(mode=mode, state=state, archived=archived):
                row = self.draft(delivery_mode=mode)
                if state != 'draft':
                    self.state(row, state)
                if archived:
                    with self.store.database() as conn:
                        conn.execute('UPDATE posts SET archived=1 WHERE id=?', (row['id'],))
                before = self.store.post(self.account, self.page, row['id'])
                with self.assertRaises(ValueError):
                    self.store.save_browser_review(self.account, self.page, row['id'], self.snapshot)
                self.assertEqual(self.store.post(self.account, self.page, row['id']), before)
        row = self.draft(delivery_mode='browser')
        before = self.state(row, 'preparing')
        for account, page in ((self.other, self.other_page), (self.other, self.page), (self.account, self.other_page)):
            with self.subTest(account=account, page=page):
                with self.assertRaises(ValueError):
                    self.store.save_browser_review(account, page, row['id'], self.snapshot)
        self.assertEqual(self.store.post(self.account, self.page, row['id']), before)

    def test_explicit_failed_draft_edit_clears_browser_evidence_for_new_review(self):
        row = self.review(browser_finish_mode='tool')
        self.state(row, 'unknown')
        self.store.record_browser_receipt(self.account, self.page, row['id'], {'state': 'unknown', 'message': 'Synthetic result'})
        self.store.record_manual_result(self.account, self.page, row['id'], 'https://facebook.com/syntheticpage/posts/77')
        self.state(row, 'failed')
        edited = self.store.save_draft(self.account, self.page, 'Nội dung sửa', post_id=row['id'])
        self.assertEqual((edited['delivery_mode'], edited['browser_finish_mode']), ('browser', 'tool'))
        self.assertEqual((edited['browser_review'], edited['browser_receipt'], edited['manual_url']), ({}, {}, ''))
        self.state(row, 'preparing')
        refreshed = self.store.save_browser_review(self.account, self.page, row['id'], {'body': 'Nội dung sửa'})
        self.assertEqual(refreshed['browser_review'], {'body': 'Nội dung sửa'})

    def test_manual_link_persists_without_promoting_state_or_overwriting_verified_evidence(self):
        for state in ('review', 'unknown', 'processing', 'published'):
            with self.subTest(state=state):
                row = self.review()
                before = row if state == 'review' else self.state(row, state)
                events_before = self.store.events(self.account, self.page, row['id'])
                result = self.store.record_manual_result(self.account, self.page, row['id'],
                    'https://facebook.com/story.php?story_fbid=88&id=100&access_token=discard&ref=tracking#fragment')
                self.assertEqual(result['manual_url'], 'https://www.facebook.com/story.php?story_fbid=88&id=100')
                for key in ('state', 'external_url', 'external_id', 'published_at', 'attempt', 'body',
                            'browser_review', 'browser_receipt', 'message'):
                    self.assertEqual(result[key], before[key], key)
                self.assertEqual(PageActivity(self.directory).post(self.account, self.page, row['id']), result)
                events = self.store.events(self.account, self.page, row['id'])
                self.assertEqual(len(events), len(events_before)+1)
                self.assertEqual(events[-1]['state'], state)
                self.assertIn('chưa xác minh', events[-1]['message'])
                self.assertNotIn('discard', str(result)+str(events))

    def test_manual_link_rejects_unprepared_api_cross_scope_and_invalid_urls_atomically(self):
        for mode, state in (('api', 'review'), ('browser', 'draft'), ('browser', 'preparing'),
                            ('browser', 'submitting'), ('browser', 'failed')):
            with self.subTest(mode=mode, state=state):
                row = self.draft(delivery_mode=mode)
                before = row if state == 'draft' else self.state(row, state)
                with self.assertRaises(ValueError):
                    self.store.record_manual_result(self.account, self.page, row['id'], 'https://facebook.com/syntheticpage/posts/77')
                self.assertEqual(self.store.post(self.account, self.page, row['id']), before)
        row = self.review()
        before_events = self.store.events(self.account, self.page, row['id'])
        for bad_url in ('https://evil.example/posts/77', 'https://www.facebook.com/',
                        'https://www.facebook.com/watch/?v=not-a-number'):
            with self.subTest(url=bad_url):
                with self.assertRaises(ValueError):
                    self.store.record_manual_result(self.account, self.page, row['id'], bad_url)
        with self.assertRaises(ValueError):
            self.store.record_manual_result(self.other, self.other_page, row['id'], 'https://facebook.com/syntheticpage/posts/77')
        self.assertEqual(self.store.post(self.account, self.page, row['id']), row)
        self.assertEqual(self.store.events(self.account, self.page, row['id']), before_events)


if __name__ == '__main__':
    unittest.main()
