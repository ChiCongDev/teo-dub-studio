"""Page đăng (owner 10/11/2026): the Pages that use one link's videos, and opening a Page's Quản lý Page window from the link's tab. Read only;
local temp folders and fake windows, no Facebook, Chrome or network."""
import json
from pathlib import Path
import tempfile
import tkinter as tk
from tkinter import ttk
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from teodub import campaign_pages
from teodub.facebook_accounts import FacebookAccounts
from teodub.facebook_ui import FacebookTab
from teodub.page_ai import PageAIStore
from teodub.page_schedule import PageScheduleStore
from test_campaign_voice_ui import LinkFixture

CAMPAIGN, OTHER = 'a1b2c3d4e5f60718293a4b5c6d7e8f90', 'a1b2c3d4e5f60718293a4b5c6d7e8f91'


class Data:
    """Helpers that put a Page, an AI source and schedules into a temp data folder."""
    def make_pages(self, data):
        self.registry = FacebookAccounts(str(data))
        self.account = self.registry.add_account('Nick F2')['id']
        self.page = self.registry.save_page(self.account, 'du lịch toàn cầu', 'https://www.facebook.com/profile.php?id=111111', '111111')['id']
        self.other_account = self.registry.add_account('Nick F17')['id']
        self.second = self.registry.save_page(self.other_account, 'Ẩm thực', 'https://www.facebook.com/profile.php?id=222222', '222222')['id']

    def source(self, data, account, page, campaigns):
        store = PageAIStore(data)
        with store.database() as db:
            db.execute('INSERT OR REPLACE INTO policies(account_id,page_key,revision,config,enabled,next_at) VALUES (?,?,?,?,?,?)',
                       (account, page, 'r1', json.dumps(dict(campaigns=campaigns)), 0, 0))

    def schedule(self, data, account, page, campaign, number, state):
        store = PageScheduleStore(data)
        with store.database() as db:
            db.execute('INSERT INTO schedules(id,account_id,page_key,post_id,source_key,source,body,snapshot,due_at,created_at,state,message) '
                       'VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                       (f'{page}-{campaign}-{number}', account, page, 'p', f'{campaign}:{number}', '{}', '', '{}', 1.0, '2026-11-10', state, ''))


class PagesOfCampaignTests(unittest.TestCase, Data):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name).resolve()

    def test_nothing_saved_means_no_page(self):
        self.assertEqual(campaign_pages.pages_of_campaign(self.data, CAMPAIGN), [])
        self.make_pages(self.data)
        self.assertEqual(campaign_pages.pages_of_campaign(self.data, CAMPAIGN), [])

    def test_a_page_that_names_the_link_as_a_source_is_listed_without_counts(self):
        self.make_pages(self.data)
        self.source(self.data, self.account, self.page, [CAMPAIGN, OTHER])
        self.source(self.data, self.other_account, self.second, [OTHER])
        rows = campaign_pages.pages_of_campaign(self.data, CAMPAIGN)
        self.assertEqual([(row['page_name'], row['account_name'], row['via']) for row in rows],
                         [('du lịch toàn cầu', 'Nick F2', 'Nguồn video')])
        self.assertEqual((rows[0]['waiting'], rows[0]['sent'], rows[0]['attention'], rows[0]['in_teo']), (0, 0, 0, True))
        self.assertEqual((rows[0]['account_id'], rows[0]['page_key']), (self.account, self.page))

    def test_schedules_of_this_link_only_are_counted_by_what_the_owner_does_next(self):
        self.make_pages(self.data)
        for number, state in enumerate(['armed', 'armed', 'scheduled', 'submitted', 'verifying', 'published', 'needs_attention', 'paused'], 1):
            self.schedule(self.data, self.account, self.page, CAMPAIGN, number, state)
        self.schedule(self.data, self.account, self.page, OTHER, 1, 'armed')  # Another link's video: not counted.
        self.schedule(self.data, self.account, self.page, CAMPAIGN, 90, 'removed')
        self.schedule(self.data, self.account, self.page, CAMPAIGN, 91, 'cancelled')
        rows = campaign_pages.pages_of_campaign(self.data, CAMPAIGN)
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]['waiting'], rows[0]['sent'], rows[0]['attention']), (3, 3, 2))
        self.assertEqual(rows[0]['via'], 'Lịch đăng')

    def test_a_page_with_only_removed_or_cancelled_schedules_is_not_listed(self):
        self.make_pages(self.data)
        self.schedule(self.data, self.account, self.page, CAMPAIGN, 1, 'removed')
        self.schedule(self.data, self.account, self.page, CAMPAIGN, 2, 'cancelled')
        self.assertEqual(campaign_pages.pages_of_campaign(self.data, CAMPAIGN), [])

    def test_source_and_schedules_together_and_pages_are_sorted_by_name(self):
        self.make_pages(self.data)
        self.source(self.data, self.account, self.page, [CAMPAIGN])
        self.schedule(self.data, self.account, self.page, CAMPAIGN, 1, 'armed')
        self.schedule(self.data, self.other_account, self.second, CAMPAIGN, 1, 'submitted')
        rows = campaign_pages.pages_of_campaign(self.data, CAMPAIGN)
        self.assertEqual([(row['page_name'], row['via'], row['waiting'], row['sent']) for row in rows],
                         [('du lịch toàn cầu', 'Nguồn video · Lịch đăng', 1, 0), ('Ẩm thực', 'Lịch đăng', 0, 1)])

    def test_a_campaign_id_never_matches_another_that_only_starts_the_same(self):
        self.make_pages(self.data)
        self.schedule(self.data, self.account, self.page, CAMPAIGN+'ff', 1, 'armed')
        self.schedule(self.data, self.account, self.page, 'a1_b%', 1, 'armed')
        self.assertEqual(campaign_pages.pages_of_campaign(self.data, CAMPAIGN), [])
        self.assertEqual(campaign_pages.pages_of_campaign(self.data, 'a1_b%')[0]['waiting'], 1)
        self.assertEqual(campaign_pages.pages_of_campaign(self.data, 'a1xb1'), [])

    def test_a_page_removed_from_teo_is_still_shown_but_cannot_be_opened(self):
        self.make_pages(self.data)
        self.schedule(self.data, self.account, self.page, CAMPAIGN, 1, 'armed')
        self.registry.remove_page(self.account, self.page)
        rows = campaign_pages.pages_of_campaign(self.data, CAMPAIGN)
        self.assertEqual((len(rows), rows[0]['in_teo']), (1, False))
        self.assertIn('đã xóa', rows[0]['page_name'])

    def test_a_damaged_policy_or_database_never_hides_the_rest_or_raises(self):
        self.make_pages(self.data)
        self.source(self.data, self.account, self.page, [CAMPAIGN])
        store = PageAIStore(self.data)
        with store.database() as db:
            db.execute('INSERT INTO policies(account_id,page_key,revision,config,enabled,next_at) VALUES (?,?,?,?,?,?)',
                       (self.other_account, self.second, 'r', '{broken', 0, 0))
        self.assertEqual([row['page_name'] for row in campaign_pages.pages_of_campaign(self.data, CAMPAIGN)], ['du lịch toàn cầu'])
        (self.data/'page-publishing'/'schedules.sqlite3').write_bytes(b'not a database')
        self.assertEqual([row['page_name'] for row in campaign_pages.pages_of_campaign(self.data, CAMPAIGN)], ['du lịch toàn cầu'])

    def test_reading_writes_nothing(self):
        self.make_pages(self.data)
        self.source(self.data, self.account, self.page, [CAMPAIGN])
        self.schedule(self.data, self.account, self.page, CAMPAIGN, 1, 'armed')
        before = {path: path.read_bytes() for path in (self.data/'page-publishing').rglob('*') if path.is_file()}
        campaign_pages.pages_of_campaign(self.data, CAMPAIGN)
        after = {path: path.read_bytes() for path in (self.data/'page-publishing').rglob('*') if path.is_file()}
        self.assertEqual(before, after)


class LinkTabTests(LinkFixture, Data):
    def test_the_button_opens_one_window_listing_the_pages_and_double_click_opens_the_page(self):
        self.make_pages(self.data)
        ident = self.link.manifest['id']
        self.source(self.data, self.account, self.page, [ident])
        self.schedule(self.data, self.account, self.page, ident, 1, 'armed')
        self.schedule(self.data, self.account, self.page, ident, 2, 'submitted')
        self.assertEqual(str(self.link.pages_button['text']), 'Page đăng')
        self.link.open_pages()
        window = self.link.pages_window
        self.addCleanup(lambda: window.winfo_exists() and window.close())
        self.link.open_pages()
        self.assertIs(self.link.pages_window, window)  # One window per link.
        rows = [window.tree.item(row, 'values') for row in window.tree.get_children()]
        self.assertEqual([tuple(map(str, row)) for row in rows],
                         [('du lịch toàn cầu', 'Nick F2', 'Nguồn video · Lịch đăng', '1', '1', '0')])
        self.assertEqual(str(window.open_button['state']), 'disabled')
        facebook = Mock()
        facebook.manage_page_of.return_value = object()
        self.studio.facebook_tab = facebook
        window.tree.selection_set(window.tree.get_children()[0])
        window.update()  # The selection event is handled.
        self.assertEqual(str(window.open_button['state']), 'normal')
        window.open_page()
        facebook.manage_page_of.assert_called_once_with(self.account, self.page)
        self.assertIn('Đã mở Quản lý Page', window.status.get())
        self.link.close()
        self.assertFalse(window.winfo_exists())

    def test_a_link_no_page_uses_says_how_to_add_it_and_a_failed_open_says_why(self):
        self.link.open_pages()
        window = self.link.pages_window
        self.addCleanup(lambda: window.winfo_exists() and window.close())
        self.assertEqual(window.tree.get_children(), ())
        self.assertIn('Thêm nguồn', window.status.get())
        self.make_pages(self.data)
        self.schedule(self.data, self.account, self.page, self.link.manifest['id'], 1, 'armed')
        window.load()
        window.tree.selection_set(window.tree.get_children()[0])
        window.open_page()
        self.assertIn('Chưa có tab Quản lý Page', window.status.get())
        self.studio.facebook_tab = Mock(manage_page_of=Mock(side_effect=ValueError('Page không còn trong tài khoản này.')))
        window.open_page()
        self.assertIn('Chưa mở được Quản lý Page: Page không còn', window.status.get())

    def test_opening_the_window_changes_no_saved_file(self):
        self.make_pages(self.data)
        ident = self.link.manifest['id']
        self.source(self.data, self.account, self.page, [ident])
        before = {path: path.read_bytes() for path in self.data.rglob('*') if path.is_file() and path.suffix in {'.json', '.sqlite3'}}
        self.link.open_pages()
        window = self.link.pages_window
        self.addCleanup(lambda: window.winfo_exists() and window.close())
        window.load()
        after = {path: path.read_bytes() for path in before if path.exists()}
        self.assertEqual(before, after)


class ManagePageOfTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = tk.Tk()
        self.root.withdraw()
        self.studio = SimpleNamespace(data_dir=Path(self.temp.name), closing=False, closed=False,
                                      login_pending=False, campaign_busy=True, process=object())
        self.enterContext(patch('teodub.facebook_ui.create_page_bridge', return_value=Mock(enabled=True)))
        notebook = ttk.Notebook(self.root)
        self.tab = FacebookTab(notebook, self.studio)
        notebook.add(self.tab, text='Quản lý Page')
        self.addCleanup(self.root.destroy)
        store = self.tab.store
        self.first = store.add_account('Nick F2')['id']
        self.page_one = store.save_page(self.first, 'Page một', 'https://www.facebook.com/profile.php?id=111111', '111111')['id']
        self.second = store.add_account('Nick F17')['id']
        self.page_two = store.save_page(self.second, 'Page hai', 'https://www.facebook.com/profile.php?id=222222', '222222')['id']
        self.tab.reload()
        self.tab.activate_account(self.first)

    def fake_window(self):
        window = Mock()
        window.winfo_exists.return_value = True
        return window

    def test_a_page_of_another_account_opens_with_that_account_selected(self):
        window = self.fake_window()
        with patch('teodub.page_manager_ui.PageManager', return_value=window) as manager:
            self.assertIs(self.tab.manage_page_of(self.second, self.page_two), window)
        manager.assert_called_once_with(self.tab, self.second, self.page_two)
        self.assertEqual(self.tab.account_id, self.second)

    def test_an_open_window_is_brought_to_the_front_not_opened_twice(self):
        window = self.fake_window()
        with patch('teodub.page_manager_ui.PageManager', return_value=window) as manager:
            self.tab.manage_page_of(self.first, self.page_one)
            self.assertIs(self.tab.manage_page_of(self.first, self.page_one), window)
        self.assertEqual(manager.call_count, 1)
        window.lift.assert_called()

    def test_a_page_not_saved_any_more_is_refused_and_the_tab_stays_as_it_is(self):
        with patch('teodub.page_manager_ui.PageManager') as manager:
            with self.assertRaisesRegex(ValueError, 'không còn'):
                self.tab.manage_page_of(self.first, 'unknown-page')
            with self.assertRaisesRegex(ValueError, 'không còn'):
                self.tab.manage_page_of(self.first, self.page_two)  # Right Page, wrong account.
        manager.assert_not_called()
        self.assertEqual(self.tab.account_id, self.first)

    def test_nothing_opens_while_tèo_is_closing(self):
        self.studio.closing = True
        with patch('teodub.page_manager_ui.PageManager') as manager:
            self.assertIsNone(self.tab.manage_page_of(self.first, self.page_one))
        manager.assert_not_called()


if __name__ == '__main__':
    unittest.main()
