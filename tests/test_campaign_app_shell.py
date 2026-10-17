import tempfile
from pathlib import Path
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import patch

from teodub import app


class CampaignAppShellTests(unittest.TestCase):
    def test_main_mounts_all_eight_tabs_without_starting_work(self):
        with tempfile.TemporaryDirectory() as data_dir:
            root = tk.Tk()
            root.withdraw()
            try:
                def inspect_tabs():
                    notebook = next(widget for widget in root.winfo_children() if isinstance(widget, ttk.Notebook))
                    self.assertEqual([notebook.tab(tab, 'text') for tab in notebook.tabs()],
                                     ['Video đơn', 'Chiến dịch hàng loạt', 'Danh sách chiến dịch', 'Quản lý Page',
                                      'Công việc web & thống kê', 'Học giọng & phong cách', 'Nguồn kênh tự động',
                                      'Duyệt caption'])
                    library = root.nametowidget(notebook.tabs()[2])
                    self.assertEqual(library.tree.get_children(), ())
                    self.assertIsNone(library.batch_controller.snapshot())
                    facebook = root.nametowidget(notebook.tabs()[3])
                    self.assertEqual(facebook.tree.get_children(), ())
                    self.assertFalse(facebook.browser_pending)
                    monitor = root.nametowidget(notebook.tabs()[4])
                    self.assertEqual(monitor.records, {})
                    self.assertEqual(monitor.data_root, (Path(data_dir)/'web').resolve())
                    voices = root.nametowidget(notebook.tabs()[5])
                    self.assertFalse(voices.busy)
                    self.assertEqual(voices.library_tree.get_children(), ())
                    sources = root.nametowidget(notebook.tabs()[6])
                    self.assertEqual(sources.tree.get_children(), ())
                    self.assertIsNone(sources.pilot.scanning)
                    self.assertEqual(sources.pilot.processing_blocked(), '')
                    reviews = root.nametowidget(notebook.tabs()[7])
                    self.assertEqual((reviews.rows, reviews.tree.get_children()), ({}, ()))
                with patch('teodub.app.tk.Tk', return_value=root), \
                     patch.object(root, 'mainloop', side_effect=inspect_tabs), \
                     patch('teodub.campaign_batch_controller.subprocess.Popen') as worker, \
                     patch('teodub.channel_discovery.scan_channel') as browser, \
                     patch('teodub.facebook_browser.open_facebook_browser') as facebook_browser:
                    self.assertEqual(app.main(['--data-dir', data_dir, '--web-data-dir', str(Path(data_dir)/'web')]), 0)
                    worker.assert_not_called()
                    browser.assert_not_called()
                    facebook_browser.assert_not_called()
            finally:
                for widget in root.winfo_children():
                    if isinstance(widget, ttk.Notebook):
                        for tab in widget.tabs():
                            frame = root.nametowidget(tab)
                            if hasattr(frame, 'scan_cancel'):
                                frame.after_cancel(frame.timer)
                                if frame.auto_timer is not None:
                                    frame.after_cancel(frame.auto_timer)
                            elif hasattr(frame, 'batch_controller'):
                                root.after_cancel(frame.studio.timer)
                root.destroy()


if __name__ == '__main__':
    unittest.main()
