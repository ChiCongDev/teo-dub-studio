"""Bấm tiêu đề cột để sắp xếp (owner 10/11/2026): only the row order of a table changes, the choice is kept, and a refresh does not undo
it. Local Tk fixtures only; no worker, network or AI."""
from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import tkinter as tk
from tkinter import ttk
import unittest

from teodub import campaign
from teodub.common import atomic_json
from teodub.table_sort import DOWN, UP, TableSort, natural, sorted_ids
from test_campaign_voice_ui import LinkFixture


class KeyTests(unittest.TestCase):
    def order(self, texts, descending=False, prefixes=()):
        return sorted_ids([(str(i), text) for i, text in enumerate(texts)], descending, prefixes)

    def test_numbers_inside_text_sort_as_numbers(self):
        self.assertEqual([['Video 10', 'Video 2', 'Video 1'][int(i)] for i in self.order(['Video 10', 'Video 2', 'Video 1'])],
                         ['Video 1', 'Video 2', 'Video 10'])
        self.assertEqual([['7%', '100%', '40%'][int(i)] for i in self.order(['7%', '100%', '40%'])], ['7%', '40%', '100%'])

    def test_dates_sort_by_time_not_by_day_first(self):
        texts = ['05/11/2026 09:00:00', '24/10/2026 23:59:59', '05/11/2026 08:59:59', '01/01/2027 00:00:00']
        self.assertEqual([texts[int(i)] for i in self.order(texts)],
                         ['24/10/2026 23:59:59', '05/11/2026 08:59:59', '05/11/2026 09:00:00', '01/01/2027 00:00:00'])
        iso = ['2026-11-10 14:30', '2026-11-09 23:00', '2026-12-01 01:00']
        self.assertEqual([iso[int(i)] for i in self.order(iso)], ['2026-11-09 23:00', '2026-11-10 14:30', '2026-12-01 01:00'])

    def test_blank_cells_stay_last_in_both_directions(self):
        texts = ['—', '03/11/2026 10:00:00', '', '01/11/2026 10:00:00']
        self.assertEqual([texts[int(i)] for i in self.order(texts)], ['01/11/2026 10:00:00', '03/11/2026 10:00:00', '—', ''])
        self.assertEqual([texts[int(i)] for i in self.order(texts, True)], ['03/11/2026 10:00:00', '01/11/2026 10:00:00', '—', ''])

    def test_status_labels_group_as_listed_and_the_rest_by_letter(self):
        texts = ['Hoàn tất', 'Lỗi', 'Chờ xử lý', 'Hoàn tất · không đăng', 'Lỗi']
        shown = [texts[int(i)] for i in self.order(texts, prefixes=('Lỗi', 'Chờ xử lý', 'Hoàn tất'))]
        self.assertEqual(shown, ['Lỗi', 'Lỗi', 'Chờ xử lý', 'Hoàn tất', 'Hoàn tất · không đăng'])
        self.assertEqual([texts[int(i)] for i in self.order(texts, True, ('Lỗi', 'Chờ xử lý', 'Hoàn tất'))][0], 'Hoàn tất · không đăng')

    def test_equal_cells_keep_the_tables_own_order_and_odd_text_never_raises(self):
        texts = ['b', 'a', 'b', 'a', '3', '３', '😀 12', 'Ê']
        self.assertEqual(self.order(['x', 'x', 'x']), ['0', '1', '2'])
        self.assertEqual(len(self.order(texts)), len(texts))
        self.assertTrue(natural('Video 2') < natural('Video 10'))


class TreeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/'sort.json'
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.tree = self.new_tree()
        for ident, name, count in (('a', 'Video 3', 30), ('b', 'Video 1', 5), ('c', 'Video 2', '—'), ('d', 'Video 10', 100)):
            self.tree.insert('', 'end', iid=ident, values=(name, count))
        self.default = ['a', 'b', 'c', 'd']

    def new_tree(self):
        tree = ttk.Treeview(self.root, columns=('name', 'count'), show='headings')
        tree.heading('name', text='Video')
        tree.heading('count', text='Số')
        return tree

    def ids(self):
        return list(self.tree.get_children())

    def test_a_click_sorts_a_second_reverses_a_third_gives_the_own_order_back(self):
        sorter = TableSort(self.tree, path=self.path)
        sorter.apply(self.default)
        self.assertEqual(self.ids(), self.default)
        sorter.click('name')
        self.assertEqual(self.ids(), ['b', 'c', 'a', 'd'])
        self.assertEqual(self.tree.heading('name', 'text'), 'Video'+UP)
        self.assertEqual(self.tree.heading('count', 'text'), 'Số')
        sorter.click('name')
        self.assertEqual(self.ids(), ['d', 'a', 'c', 'b'])
        self.assertEqual(self.tree.heading('name', 'text'), 'Video'+DOWN)
        sorter.click('name')
        self.assertEqual(self.ids(), self.default)
        self.assertEqual(self.tree.heading('name', 'text'), 'Video')

    def test_another_column_starts_ascending_and_blank_cells_are_last(self):
        sorter = TableSort(self.tree, path=self.path)
        sorter.apply(self.default)
        sorter.click('name')
        sorter.click('count')
        self.assertEqual(self.ids(), ['b', 'a', 'd', 'c'])
        sorter.click('count')
        self.assertEqual(self.ids(), ['d', 'a', 'b', 'c'])
        self.assertEqual(self.tree.heading('name', 'text'), 'Video')

    def test_a_refresh_with_new_rows_keeps_the_chosen_order(self):
        sorter = TableSort(self.tree, path=self.path)
        sorter.apply(self.default)
        sorter.click('count')
        self.tree.insert('', 'end', iid='e', values=('Video 4', 20))
        self.tree.item('d', values=('Video 10', 1))  # A value changed by the refresh.
        sorter.apply(self.default+['e'])
        self.assertEqual(self.ids(), ['d', 'b', 'e', 'a', 'c'])
        self.tree.delete('d')
        sorter.apply(['a', 'b', 'c', 'e'])
        self.assertEqual(self.ids(), ['b', 'e', 'a', 'c'])

    def test_rows_keep_their_ids_and_the_selection(self):
        sorter = TableSort(self.tree, path=self.path)
        sorter.apply(self.default)
        self.tree.selection_set('d')
        sorter.click('name')
        self.assertEqual(self.tree.selection(), ('d',))
        self.assertEqual(self.tree.item('d', 'values')[0], 'Video 10')

    def test_the_choice_is_kept_for_the_next_time(self):
        sorter = TableSort(self.tree, path=self.path)
        sorter.apply(self.default)
        sorter.click('count')
        sorter.click('count')
        again = TableSort(self.new_tree(), path=self.path)
        self.assertEqual((again.column, again.descending), ('count', True))
        self.assertEqual(again.tree.heading('count', 'text'), 'Số'+DOWN)
        sorter.click('count')  # Back to the table's own order: the file says so too.
        self.assertIsNone(TableSort(self.new_tree(), path=self.path).column)

    def test_a_missing_damaged_or_unknown_file_means_no_sort(self):
        self.assertIsNone(TableSort(self.new_tree(), path=self.path).column)
        self.path.write_text('{broken', encoding='utf-8')
        self.assertIsNone(TableSort(self.new_tree(), path=self.path).column)
        atomic_json(self.path, dict(column='gone', descending=True))
        self.assertIsNone(TableSort(self.new_tree(), path=self.path).column)
        atomic_json(self.path, dict(column='name', descending='yes'))
        self.assertEqual(TableSort(self.new_tree(), path=self.path).descending, False)

    def test_a_table_that_cannot_keep_its_choice_still_sorts(self):
        sorter = TableSort(self.tree, path=None)
        sorter.apply(self.default)
        sorter.click('name')
        self.assertEqual(self.ids(), ['b', 'c', 'a', 'd'])


class LinkTabTests(LinkFixture):
    def rows(self):
        return list(self.link.tree.get_children())

    def finish_at(self, when):
        manifest = campaign.read_json(self.directory/'campaign.json')
        for item in manifest['items']:
            if item['number'] in when:
                item.update(status='completed', outputs=dict(video='x.mp4'), message='Xong', completed_at=when[item['number']])
        atomic_json(self.directory/'campaign.json', manifest)

    def test_the_videos_table_sorts_by_a_heading_and_a_refresh_keeps_it(self):
        today = datetime.now().astimezone().replace(microsecond=0)
        self.finish_at({1: (today-timedelta(hours=1)).isoformat(), 3: (today-timedelta(hours=5)).isoformat()})
        self.link.refresh(full=True)
        self.assertEqual(self.rows(), ['1', '2', '3', '4'])  # The own order, as before.
        self.link.sorter.click('completed_at')
        self.assertEqual(self.rows(), ['3', '1', '2', '4'])  # Earliest first; the videos not finished are last.
        self.link.refresh(full=True)
        self.assertEqual(self.rows(), ['3', '1', '2', '4'])
        self.link.sorter.click('completed_at')
        self.assertEqual(self.rows(), ['1', '3', '2', '4'])
        self.link.sorter.click('state')
        self.assertEqual(self.rows(), ['2', '4', '1', '3'])  # Waiting before finished ones.
        self.link.sorter.click('state')
        self.link.sorter.click('state')
        self.assertEqual(self.rows(), ['1', '2', '3', '4'])

    def test_the_choice_is_saved_in_the_links_own_folder_and_read_back(self):
        self.link.sorter.click('title')
        self.assertTrue((self.directory/'table-sort.json').is_file())
        again = TableSort(ttk.Treeview(self.root, columns=tuple(self.link.tree['columns'])), path=self.directory/'table-sort.json')
        self.assertEqual((again.column, again.descending), ('title', False))

    def test_sorting_never_changes_the_campaign(self):
        before = (self.directory/'campaign.json').read_bytes()
        for column in ('name', 'state', 'posting', 'drive', 'message'):
            self.link.sorter.click(column)
            self.link.refresh(full=True)
        self.assertEqual((self.directory/'campaign.json').read_bytes(), before)
        self.assertEqual([item['number'] for item in campaign.read_json(self.directory/'campaign.json')['items']], [1, 2, 3, 4])


if __name__ == '__main__':
    unittest.main()
