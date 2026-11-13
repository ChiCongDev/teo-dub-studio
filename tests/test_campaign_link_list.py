"""13/11/2026: the links of Chiến dịch hàng loạt can be chosen from a drop-down that shows only the link on screen, as well as
from the tab strip. Only how a link is chosen changes; no worker, network or AI (a worker start would fail the test)."""
import json
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import patch

from tests import test_campaign_link_tabs as fixtures


class LinkListTests(unittest.TestCase):
    setUp = fixtures.LinkTabsUITests.setUp
    cleanup = fixtures.LinkTabsUITests.cleanup
    create = fixtures.LinkTabsUITests.create
    titles = fixtures.LinkTabsUITests.titles

    def shown(self):
        return bool(self.area.picker.winfo_manager())

    def three(self):
        return [self.create(f'https://www.douyin.com/video/{n}', f'Kênh {name}') for n, name in ((111, 'A'), (222, 'B'), (333, 'C'))]

    def test_the_tab_strip_is_the_default_and_the_drop_down_is_hidden(self):
        self.assertEqual(self.area.mode, 'row')
        self.assertFalse(self.shown())
        self.assertEqual(str(self.area.tabs.cget('style')), 'TNotebook')
        self.assertEqual(self.area.mode_var.get(), 'row')

    def test_the_drop_down_lists_every_link_and_shows_the_one_on_screen(self):
        a, b, c = self.three()
        self.area.set_mode('list')
        self.assertTrue(self.shown())
        self.assertEqual(list(self.area.picker['values']), self.titles())
        self.assertEqual(self.area.picker.get(), 'Kênh C · 0/1 · sẵn sàng')  # The last created link is on screen.
        self.assertEqual(self.area.tabs.cget('style'), 'LinkList.TNotebook')
        self.assertEqual(ttk.Style(self.area).layout('LinkList.TNotebook.Tab'), [('null', {'sticky': 'nswe'})])  # Empty: no tab strip is drawn.

    def test_choosing_a_row_shows_only_that_link(self):
        a, b, c = self.three()
        self.area.set_mode('list')
        self.area.picker.current(1)
        self.area.picked()
        self.assertEqual(self.area.tabs.select(), str(self.area.links[str(a)]))
        self.assertEqual(self.area.picker.get(), 'Kênh A · 0/1 · sẵn sàng')
        self.area.picker.current(0)
        self.area.picked()
        self.assertEqual(self.area.tabs.select(), str(self.tab))  # '＋ Thêm link' is the first row.

    def test_a_link_opened_elsewhere_in_the_app_shows_in_the_drop_down(self):
        a, b, c = self.three()
        self.area.set_mode('list')
        self.area.open_link(a)  # "Page đăng"/"Mở link" paths select a tab this way.
        self.assertEqual(self.area.picker.current(), 1)

    def test_progress_in_a_title_reaches_the_drop_down(self):
        a, b, c = self.three()
        self.area.set_mode('list')
        tab = self.area.links[str(b)]
        self.area.tabs.tab(tab, text='Kênh B · 5/9 · đang làm')
        self.area.sync_list()
        self.assertEqual(list(self.area.picker['values'])[2], 'Kênh B · 5/9 · đang làm')

    def test_closing_a_link_and_adding_one_update_the_list(self):
        a, b, c = self.three()
        self.area.set_mode('list')
        self.area.links[str(b)].close()
        self.assertEqual(list(self.area.picker['values']), self.titles())
        self.assertEqual(len(self.area.picker['values']), 3)
        self.create('https://www.douyin.com/video/444', 'Kênh D')
        self.assertEqual(len(self.area.picker['values']), 4)
        self.assertEqual(self.area.picker.get(), 'Kênh D · 0/1 · sẵn sàng')

    def test_two_links_with_the_same_title_are_each_picked_correctly(self):
        a = self.create('https://www.douyin.com/video/111', 'Trùng')
        b = self.create('https://www.douyin.com/video/222', 'Trùng')
        self.area.set_mode('list')
        names = list(self.area.picker['values'])
        self.assertEqual(len(set(names)), 3)
        self.area.picker.current(1)
        self.area.picked()
        self.assertEqual(self.area.tabs.select(), str(self.area.links[str(a)]))
        self.area.picker.current(2)
        self.area.picked()
        self.assertEqual(self.area.tabs.select(), str(self.area.links[str(b)]))

    def test_going_back_to_the_tab_strip_restores_it(self):
        self.three()
        self.area.set_mode('list')
        self.area.set_mode('row')
        self.assertFalse(self.shown())
        self.assertEqual(str(self.area.tabs.cget('style')), 'TNotebook')

    def test_the_choice_is_remembered_and_comes_back(self):
        from teodub.campaign_tabs_ui import CampaignArea
        a, b, c = self.three()
        self.area.set_mode('list')
        saved = json.loads((self.data/'campaign-tabs.json').read_text(encoding='utf-8'))
        self.assertEqual((saved['list_mode'], len(saved['campaigns'])), ('list', 3))
        again = CampaignArea(self.notebook, self.studio)
        try:
            again.attach(ttk.Frame(again.tabs))
            self.assertEqual(again.mode, 'list')
            self.assertEqual(len(again.picker['values']), 4)
        finally:
            again.after_cancel(again.timer)
            again.destroy()

    def test_an_old_or_damaged_file_means_the_tab_strip(self):
        for content in ('{"version": 1, "campaigns": []}', '{"version": 1, "campaigns": [], "list_mode": "weird"}', 'not json'):
            with self.subTest(content=content):
                (self.data/'campaign-tabs.json').write_text(content, encoding='utf-8')
                self.assertEqual(self.area.saved_mode(), 'row')

    def test_switching_modes_changes_nothing_else(self):
        a, b, c = self.three()
        before = (list(self.area.links), self.titles(), self.area.tabs.select(), self.tab.directory)
        with patch('subprocess.Popen') as spawn:
            for mode in ('list', 'row', 'list', 'row'):
                self.area.set_mode(mode)
            self.area.poll()
        spawn.assert_not_called()
        self.assertEqual((list(self.area.links), self.titles(), self.area.tabs.select(), self.tab.directory), before)
        saved = json.loads((self.data/'campaign-tabs.json').read_text(encoding='utf-8'))
        self.assertEqual(saved['campaigns'], [a.name, b.name, c.name])

    def test_a_link_that_is_not_on_screen_still_refreshes_in_the_list_mode(self):
        a, b, c = self.three()
        self.area.set_mode('list')
        seen = []
        with patch.object(type(self.area.links[str(a)]), 'refresh', lambda link, full=False, background=False: seen.append(link.directory.name)):
            self.area.poll()
        self.assertEqual(sorted(seen), sorted(path.name for path in (a, b, c)))  # Hidden is not paused.


if __name__ == '__main__':
    unittest.main()
