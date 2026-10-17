import threading
import time
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import patch

from teodub.web_tracking_ui import WebTrackingTab
from tests import test_web_tracking


class WebTrackingUITests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_web_tracking.WebTrackingTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.ident = self.fixture.job(1)
        self.finished = self.fixture.job(2, 'completed', 'b')
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill='both', expand=True)
        self.tab = WebTrackingTab(self.notebook, self.fixture.root, autostart=False)
        self.notebook.add(self.tab, text='Công việc web & thống kê')
        self.tab.apply_snapshot(self.fixture.read())

    def test_statistics_real_rows_and_filter_values_include_disabled_employee(self):
        self.assertEqual(self.tab.metrics['total'].get(), '2')
        self.assertEqual(self.tab.metrics['queued'].get(), '1')
        self.assertEqual(self.tab.metrics['completed'].get(), '1')
        self.assertEqual(len(self.tab.tree.get_children()), 2)
        self.assertIn('Bình (binh)', self.tab.owner_picker.cget('values'))
        self.assertEqual(self.tab.people_tree.item('b')['values'][4], 1)

    def test_selection_survives_refresh_and_detail_progress_changes(self):
        self.tab.tree.selection_set(self.ident)
        self.tab.show_selection()
        with self.fixture.db() as db:
            db.execute("UPDATE jobs SET state='running',message='Đang tải' WHERE id=?", (self.ident,))
        self.tab.apply_snapshot(self.fixture.read())
        self.assertEqual(self.tab.tree.selection(), (self.ident,))
        text = self.tab.details.get('1.0', 'end')
        self.assertIn('Đang xử lý', text)
        self.assertIn('Đang tải', text)
        self.assertIn('Adam', text)

    def test_filter_removal_clears_selection_and_open_buttons(self):
        self.tab.tree.selection_set(self.ident)
        self.tab.apply_snapshot(self.fixture.read(owner='b'))
        self.assertIsNone(self.tab.selected())
        self.assertEqual(str(self.tab.output_button.cget('state')), 'disabled')
        self.assertEqual(str(self.tab.log_button.cget('state')), 'disabled')

    def wait_read(self):
        deadline = time.monotonic()+3
        while self.tab.results.empty() and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertFalse(self.tab.results.empty())

    def test_async_reader_uses_selected_filters_and_never_touches_tk(self):
        calls = []
        def reader(root, **kwargs):
            calls.append((threading.get_ident(), kwargs))
            return self.fixture.read(**kwargs)
        self.tab.owner.set('Bình (binh)')
        self.tab.period.set('Hôm nay')
        self.tab.state.set('Hoàn tất')
        with patch('teodub.web_tracking_ui.snapshot', side_effect=reader):
            self.tab.filters_changed()
            self.wait_read()
            self.tab.poll()
        self.assertNotEqual(calls[0][0], threading.get_ident())
        self.assertEqual(calls[0][1], dict(owner='b', period='Hôm nay', state='completed'))
        self.assertEqual(self.tab.tree.get_children(), (self.finished,))

    def test_late_response_for_old_filter_does_not_overwrite_new_selection(self):
        old = self.fixture.read()
        self.tab.generation = 1
        self.tab.pending = True
        self.tab.results.put((0, old, None))
        self.tab.owner.set('Bình (binh)')
        with patch('teodub.web_tracking_ui.snapshot', return_value=self.fixture.read(owner='b')):
            self.tab.poll()
            self.wait_read()
            self.tab.poll()
        self.assertEqual(self.tab.tree.get_children(), (self.finished,))

    def test_database_error_retains_last_snapshot_with_stale_notice(self):
        self.tab.pending = True
        self.tab.results.put((0, None, 'database is locked'))
        self.tab.poll()
        self.assertEqual(self.tab.metrics['total'].get(), '2')
        self.assertIn('Giữ số liệu lần', self.tab.status.get())
        self.assertEqual(len(self.tab.tree.get_children()), 2)

    def test_open_completed_output_uses_job_bound_folder_and_missing_is_disabled(self):
        self.tab.tree.selection_set(self.finished)
        self.tab.show_selection()
        self.assertEqual(str(self.tab.output_button.cget('state')), 'disabled')
        folder = self.fixture.root/'outputs'/self.finished
        folder.mkdir(parents=True)
        video = folder/'video.mp4'
        video.write_bytes(b'fixture')
        outputs = dict(video=str(video))
        self.fixture.metadata(self.finished, 'result.json', dict(status='completed', outputs=outputs))
        self.fixture.metadata(self.finished, 'state.json', dict(rendered=dict(outputs=outputs)))
        self.tab.show_selection()
        self.assertEqual(str(self.tab.output_button.cget('state')), 'normal')
        with patch('teodub.web_tracking_ui.os.startfile') as open_file:
            self.tab.open_output()
        open_file.assert_called_once_with(folder)

    def test_copy_link_and_destroy_cancel_poll_without_starting_worker(self):
        (self.fixture.root/'LINK_WEB.txt').write_text('https://example.trycloudflare.com/\n', encoding='utf-8')
        self.tab.copy_link()
        self.assertEqual(self.tab.clipboard_get(), 'https://example.trycloudflare.com/')
        self.tab.timer = self.tab.after(3000, self.tab.poll)
        self.tab.destroy()
        self.assertTrue(self.tab.destroyed)
        self.assertIsNone(self.tab.timer)


if __name__ == '__main__':
    unittest.main()
