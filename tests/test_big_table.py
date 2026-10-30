"""29/10/2026: the table of a campaign with more than BIG_TABLE_ITEMS videos is read a few rows at a time. Up to that size
nothing changes. Real Tk widgets, local fixtures only (no network, browser, AI or Facebook)."""
import json
from pathlib import Path
import tempfile
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import patch

from teodub import campaign, campaign_ui
from teodub.app import Studio
from teodub.campaign import read_json
from teodub.campaign_ui import CampaignTab
from teodub.common import atomic_json


class BigTableTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = tk.Tk()
        self.root.withdraw()
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill='both', expand=True)
        single = ttk.Frame(self.notebook)
        self.notebook.add(single, text='Video đơn')
        self.studio = Studio(self.root, self.temp.name, parent=single)
        self.tab = CampaignTab(self.notebook, self.studio)
        self.notebook.add(self.tab, text='Chiến dịch hàng loạt')
        self.root.update_idletasks()
        self.addCleanup(self.cleanup)
        self.tab.original_dir.set(str(Path(self.temp.name)/'originals'))
        self.tab.output_dir.set(str(Path(self.temp.name)/'outputs'))
        self.tab.source.insert('1.0', '\n'.join(f'https://www.douyin.com/video/{100+n}' for n in range(8)))
        self.tab.after_cancel(self.tab.timer)  # The test drives `display` itself.
        self.tab.discover()
        self.tab.create()
        self.assertIsNotNone(self.tab.directory)
        self.directory = self.tab.directory

    def cleanup(self):
        if self.tab.auto_timer is not None:
            self.tab.after_cancel(self.tab.auto_timer)
        self.studio.process = None
        self.studio.finish_close()

    def manifest(self):
        return read_json(self.directory/'campaign.json')

    def shown(self):
        return {key: tuple(self.tab.tree.item(key, 'values')) for key in self.tab.tree.get_children()}

    def big(self, seconds=5.0):
        return patch.multiple(campaign_ui, BIG_TABLE_ITEMS=3, BIG_TABLE_SECONDS=seconds)

    def test_the_same_table_as_before_once_every_row_has_been_read(self):
        manifest = self.manifest()
        self.tab.display(manifest)  # 8 videos are far below 1.000: the unchanged path.
        before = self.shown()
        self.assertEqual(len(before), 8)
        self.assertEqual(self.tab.big_rows, {})
        self.tab.tree.delete(*self.tab.tree.get_children())
        with self.big():
            self.tab.display(manifest)
        self.assertEqual(self.shown(), before)
        self.assertEqual(sorted(self.tab.big_rows), list(range(1, 9)))
        self.assertEqual(set(self.tab.drive_statuses), set(range(1, 9)))

    def test_with_no_time_left_rows_show_what_the_list_knows_and_are_read_later_in_turns(self):
        manifest = self.manifest()
        with self.big(seconds=-1):
            self.tab.display(manifest)
            rows = self.shown()
            self.assertEqual(len(rows), 8)
            self.assertEqual(rows['1'][0], 'Video 1')
            self.assertEqual(rows['1'][2], 'https://www.douyin.com/video/100')
            self.assertEqual(rows['1'][7], 'Đang đọc…')
            self.assertEqual(self.tab.big_rows, {})
        with self.big(seconds=5):
            self.tab.display(manifest)
        self.assertEqual(sorted(self.tab.big_rows), list(range(1, 9)))
        self.assertEqual(self.shown()['1'][7], 'Không bật')

    def test_each_refresh_reads_only_as_many_rows_as_its_time_allows_then_goes_on_from_there(self):
        manifest = self.manifest()
        reads = []
        real = campaign_ui.tracking_row
        def counting(item, active=True, retrying=False):
            reads.append(item['number'])
            return real(item, active=active, retrying=retrying)
        clock = iter(x*0.04 for x in range(1000))  # Every call to the clock is 0.04 s later.
        with patch.multiple(campaign_ui, BIG_TABLE_ITEMS=3, BIG_TABLE_SECONDS=0.1, tracking_row=counting), \
                patch.object(campaign_ui.time, 'monotonic', side_effect=lambda: next(clock)):
            self.tab.display(manifest)
            first = list(reads)
            self.tab.display(manifest)
            second = reads[len(first):]
        self.assertTrue(0 < len(first) < 8)
        self.assertEqual(second[0], first[-1]+1)  # The next refresh starts where the last one stopped.

    def test_a_row_that_changed_and_the_running_video_are_read_at_once_even_with_no_time_left(self):
        manifest = self.manifest()
        with self.big():
            self.tab.display(manifest)
        manifest['items'][1].update(status='completed', message='Xong rồi')
        manifest['items'][2].update(status='running', message='Đang làm')
        with self.big(seconds=-1):
            self.tab.display(manifest)
        rows = self.shown()
        self.assertEqual(rows['2'][3], 'Hoàn tất')
        self.assertEqual(rows['2'][4], '100%')
        self.assertEqual(rows['3'][3], 'Gián đoạn · Tiếp tục')  # No process holds it: shown as before.
        self.assertEqual(rows['1'][3], 'Chờ xử lý')
        self.assertEqual(self.tab.tree.item('2', 'tags'), ('completed',))

    def test_a_row_read_long_ago_is_read_again(self):
        manifest = self.manifest()
        with self.big():
            self.tab.display(manifest)
            stale = {number: (entry[0], entry[1], entry[2], entry[3]-1000) for number, entry in self.tab.big_rows.items()}
            self.tab.big_rows.update(stale)
            reads = []
            real = campaign_ui.drive_upload_summary
            with patch.object(campaign_ui, 'drive_upload_summary', side_effect=lambda *a: reads.append(1) or real(*a)):
                self.tab.display(manifest)
        self.assertEqual(len(reads), 8)

    def test_a_drive_status_that_changes_in_the_files_shows_after_the_refresh_time(self):
        manifest = self.manifest()
        with self.big():
            self.tab.display(manifest)
        self.assertEqual(self.shown()['1'][7], 'Không bật')
        job = self.directory/'jobs'/'0001'
        config = read_json(job/'config.json')
        atomic_json(job/'config.json', dict(config, drive_upload=dict(enabled=True, mode='api', account_id='a', folder_id='f')))
        with self.big():
            self.tab.display(manifest)  # Not yet: the config was not reported as changed.
            self.assertEqual(self.shown()['1'][7], 'Không bật')
            self.tab.big_rows[1] = (*self.tab.big_rows[1][:3], self.tab.big_rows[1][3]-1000)
            self.tab.display(manifest)
        self.assertNotEqual(self.shown()['1'][7], 'Không bật')

    def test_nothing_is_written_to_the_table_while_nothing_changed(self):
        manifest = self.manifest()
        with self.big():
            self.tab.display(manifest)
            with patch.object(self.tab.tree, 'item', wraps=self.tab.tree.item) as item, \
                    patch.object(self.tab.tree, 'insert', wraps=self.tab.tree.insert) as insert:
                self.tab.display(manifest)
                item.assert_not_called()
                insert.assert_not_called()

    def test_a_new_posting_label_reaches_the_table(self):
        manifest = self.manifest()
        with self.big():
            self.tab.display(manifest)
            self.assertEqual(self.shown()['1'][6], 'Chưa đăng')
            labels = {number: dict(label='Đã lên lịch', details='x') for number in range(1, 9)}
            with patch.object(campaign_ui, 'campaign_posting_statuses', return_value=labels):
                self.tab.display(manifest)
        self.assertEqual(self.shown()['1'][6], 'Đã lên lịch')
        self.assertEqual(self.tab.posting_statuses[1]['label'], 'Đã lên lịch')

    def test_another_campaign_or_a_preview_starts_the_reading_over(self):
        manifest = self.manifest()
        with self.big():
            self.tab.display(manifest)
            self.assertEqual(self.tab.big_for, self.directory)
            self.tab.preview_records([dict(url='https://www.douyin.com/video/1', title='')])
            self.assertIsNone(self.tab.big_for)
            self.tab.directory = self.directory
            self.tab.display(manifest)
        self.assertEqual(self.tab.big_for, self.directory)
        self.assertEqual(len(self.shown()), 8)
        self.assertEqual(self.shown()['8'][2], 'https://www.douyin.com/video/107')

    def test_a_small_campaign_after_a_big_one_goes_back_to_the_old_path(self):
        manifest = self.manifest()
        with self.big():
            self.tab.display(manifest)
        self.assertIsNotNone(self.tab.big_for)
        self.tab.display(manifest)  # Not patched: 8 videos.
        self.assertIsNone(self.tab.big_for)

    def test_double_click_details_work_for_a_row_not_read_yet(self):
        manifest = self.manifest()
        with self.big(seconds=-1):
            self.tab.display(manifest)
            self.tab.tree.selection_set('1')
            with patch('tkinter.messagebox.showinfo') as shown:
                self.tab.show_item(detail_only=True)
        shown.assert_called_once()
        self.assertIn('https://www.douyin.com/video/100', shown.call_args.args[1])


class SavingProgressTests(unittest.TestCase):
    def test_creating_a_campaign_reports_its_progress_and_works_without_it(self):
        settings = dict(voice='Mai Anh', voice_provider='local', reading_style='legacy', speed=.9, original_gain=.1,
                        translation_model='gpt-5.6-luna', translation_effort='low', burn_subtitles=True,
                        subtitle_color='yellow', subtitle_weight='bold')
        records = [dict(url=f'https://www.douyin.com/video/{500+n}', title='') for n in range(7)]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            reports = []
            with patch.object(campaign, 'CREATE_PROGRESS_EVERY', 3):
                campaign.create_campaign(root, 'A', records, settings, str(root/'g'), str(root/'x'),
                                         progress=lambda done, total: reports.append((done, total)))
                self.assertEqual(reports, [(3, 7), (6, 7)])
                reports.clear()
                campaign.create_campaign(root, 'B', records, settings, str(root/'g'), str(root/'x'), prevent_duplicates=True,
                                         progress=lambda done, total: reports.append((done, total)))
                self.assertEqual(reports, [(3, 7), (6, 7)])
                again = campaign.create_campaign(root, 'C', records, settings, str(root/'g'), str(root/'x'))
            self.assertEqual(len(read_json(again/'campaign.json')['items']), 7)


if __name__ == '__main__':
    unittest.main()
