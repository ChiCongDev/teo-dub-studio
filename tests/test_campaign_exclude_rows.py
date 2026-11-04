"""Excluded videos in the link tab's table (owner 04/11/2026): the whole row greyed, "Không đăng" in the posting column, a finished
video says "Hoàn tất · Không đăng". Local fixtures only; no worker, network or AI."""
from pathlib import Path
import tempfile
import tkinter as tk
from tkinter import ttk
import unittest

from teodub import campaign, campaign_exclude
from teodub.campaign_batch_controller import CampaignBatchController
from teodub.campaign_tracking import NOT_POSTED, is_skipped, posting_label, row_tag, tracking_row
from teodub.common import atomic_json


class RowHelperTests(unittest.TestCase):
    def test_row_tag_label_and_posting_cell(self):
        done = dict(number=1, status='completed', job='x', url='https://www.douyin.com/video/1')
        stopped = dict(done, number=2, no_post='2026-11-04')
        excluded = dict(done, number=3, status='excluded')
        failed = dict(done, number=4, status='failed')
        self.assertEqual([row_tag(i) for i in (done, stopped, excluded, failed)], ['completed', 'excluded', 'excluded', 'failed'])
        self.assertEqual(row_tag(failed, retrying=True), 'retrying')
        self.assertEqual([is_skipped(i) for i in (done, stopped, excluded, failed)], [False, True, True, False])
        statuses = {1: dict(label='Đang lên lịch'), 2: dict(label='Đang lên lịch'), 3: dict(label='Chưa đăng')}
        self.assertEqual([posting_label(i, statuses) for i in (done, stopped, excluded, failed)],
                         ['Đang lên lịch', NOT_POSTED, NOT_POSTED, 'Cần kiểm tra'])
        self.assertEqual(tracking_row(done)[3], 'Hoàn tất')
        self.assertEqual(tracking_row(stopped)[3], 'Hoàn tất · Không đăng')
        self.assertEqual(tracking_row(excluded)[3], 'Đã loại trừ')


class RowTableTests(unittest.TestCase):
    def setUp(self):
        from teodub.app import Studio
        from teodub.campaign_tabs_ui import CampaignArea
        from teodub.campaign_ui import CampaignTab
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name).resolve()
        self.root = tk.Tk()
        self.root.withdraw()
        self.notebook = ttk.Notebook(self.root)
        single = ttk.Frame(self.notebook)
        self.notebook.add(single, text='Video đơn')
        self.studio = Studio(self.root, str(self.data), parent=single)
        self.area = CampaignArea(self.notebook, self.studio)
        self.studio.campaign_area = self.area
        self.tab = CampaignTab(self.area.tabs, self.studio)
        self.notebook.add(self.area, text='Chiến dịch hàng loạt')
        self.studio.batch_controller = CampaignBatchController(self.studio, self.tab)
        self.area.attach(self.tab)
        self.addCleanup(self.cleanup)
        self.tab.original_dir.set(str(self.data/'originals'))
        self.tab.output_dir.set(str(self.data/'outputs'))

    def cleanup(self):
        for owner in (self.tab, self.area):
            for name in ('timer', 'auto_timer'):
                if getattr(owner, name, None) is not None:
                    owner.after_cancel(getattr(owner, name))
        self.studio.process = None
        self.studio.finish_close()

    def test_excluded_rows_have_their_own_tag_and_say_not_posted(self):
        links = '\n'.join(f'https://www.douyin.com/video/76900000000000000{n:02d}' for n in range(1, 5))
        self.tab.source.insert('1.0', links)
        self.tab.name.set('Kênh A')
        self.tab.create()
        directory = self.tab.directory
        manifest = campaign.read_json(directory/'campaign.json')
        for item in manifest['items'][:2]:  # Videos 1 and 2 are finished.
            item.update(status='completed', message='Xong')
        atomic_json(directory/'campaign.json', manifest)
        campaign_exclude.apply(directory, [2, 3])  # 2 finished -> Không đăng, 3 pending -> excluded
        link = self.area.links[str(directory)]
        link.refresh(full=True)
        tags = {key: link.tree.item(key, 'tags') for key in link.tree.get_children()}
        self.assertEqual(tags, {'1': ('completed',), '2': ('excluded',), '3': ('excluded',), '4': ('pending',)})
        status = {key: link.tree.item(key, 'values')[3] for key in tags}
        self.assertEqual(status, {'1': 'Hoàn tất', '2': 'Hoàn tất · Không đăng', '3': 'Đã loại trừ', '4': 'Chờ xử lý'})
        posting = {key: link.tree.item(key, 'values')[6] for key in tags}
        self.assertEqual((posting['2'], posting['3']), (NOT_POSTED, NOT_POSTED))
        self.assertNotEqual(posting['1'], NOT_POSTED)
        self.assertEqual(str(link.tree.tag_configure('excluded', 'background')), '#dedede')
        self.assertIn('Loại trừ 1', link.status.get())
        self.assertIn('Không đăng 1', link.status.get())
