import json
from pathlib import Path
import tempfile
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import Mock, patch

from teodub.app import Studio
from teodub.campaign_ui import CampaignTab


class ChannelImportUITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = tk.Tk()
        self.root.withdraw()
        self.notebook = ttk.Notebook(self.root)
        self.single = ttk.Frame(self.notebook)
        self.studio = Studio(self.root, self.temp.name, parent=self.single)
        self.tab = CampaignTab(self.notebook, self.studio)
        self.addCleanup(self.close)
        self.html = Path(self.temp.name)/'kenh.html'
        self.html.write_text('<link rel="canonical" href="https://www.douyin.com/user/owner">'
            '<div data-e2e="user-post-list"><a href="/video/123">Đèn thử</a></div>', encoding='utf-8')

    def close(self):
        self.tab.after_cancel(self.tab.timer)
        if self.tab.auto_timer is not None:
            self.tab.after_cancel(self.tab.auto_timer)
        self.studio.process = None
        self.studio.finish_close()

    def test_saved_page_import_binds_owner_and_never_opens_browser_or_worker(self):
        self.tab.source.insert('1.0', 'https://v.douyin.com/short/')
        with patch('tkinter.messagebox.showinfo'), \
             patch('teodub.campaign_ui.filedialog.askopenfilename', return_value=str(self.html)), \
             patch('teodub.channel_discovery.scan_channel') as browser, \
             patch('teodub.campaign_ui.subprocess.Popen') as worker:
            self.tab.import_channel_page()
            self.tab.auto_discover()
            self.tab.create()
            browser.assert_not_called()
            worker.assert_not_called()
        manifest = json.loads((self.tab.directory/'campaign.json').read_text(encoding='utf-8'))
        self.assertEqual(manifest['items'][0]['url'], 'https://www.douyin.com/video/123')
        self.assertEqual(manifest['discovery']['reason'], 'saved_page')
        self.assertFalse(manifest['discovery']['complete'])
        self.assertFalse(self.tab.auto_fetch.get())
        self.assertEqual(self.tab.discovery['input'], 'https://www.douyin.com/user/owner')

    def test_wrong_channel_retains_existing_form_and_records(self):
        self.tab.source.insert('1.0', 'https://www.douyin.com/user/other')
        self.tab.preview_records([dict(url='https://www.douyin.com/video/999')])
        with patch('tkinter.messagebox.showinfo'), \
             patch('teodub.campaign_ui.filedialog.askopenfilename', return_value=str(self.html)):
            self.tab.import_channel_page()
        self.assertIn('kênh khác', self.tab.status.get())
        self.assertEqual(self.tab.records[0]['url'], 'https://www.douyin.com/video/999')
        self.assertEqual(self.tab.source.get('1.0', 'end').strip(), 'https://www.douyin.com/user/other')

    def test_busy_import_does_not_open_dialog_or_change_source(self):
        self.studio.campaign_busy = True
        with patch('tkinter.messagebox.showinfo') as prompt, \
             patch('teodub.campaign_ui.filedialog.askopenfilename') as picker:
            self.tab.import_channel_page()
            prompt.assert_not_called()
            picker.assert_not_called()
        self.assertIsNone(self.tab.discovery)

    def test_cancel_picker_does_not_change_scan_settings(self):
        with patch('tkinter.messagebox.showinfo'), \
             patch('teodub.campaign_ui.filedialog.askopenfilename', return_value=''):
            self.tab.import_channel_page()
        self.assertTrue(self.tab.auto_fetch.get())
        self.assertIsNone(self.tab.discovery)

    def test_browser_failure_report_keeps_underlying_launch_cause(self):
        self.tab.source.insert('1.0', 'https://v.douyin.com/short/')
        self.tab.browser_label.set('Cốc Cốc (thử nghiệm)')
        def failed(*args, **kwargs):
            kwargs['progress']('Đang mở Cốc Cốc')
            try:
                raise RuntimeError('Browser launch failed\nprivate multiline details')
            except RuntimeError as exc:
                raise ValueError('Không mở được Cốc Cốc riêng.') from exc
        with patch('teodub.channel_discovery.scan_channel', side_effect=failed) as scanner, \
             patch('teodub.campaign_ui.threading.Thread', side_effect=lambda **kw: Mock(start=kw['target'])):
            self.tab.discover()
            self.tab.after_cancel(self.tab.timer)
            self.tab.poll()
            scanner.assert_called_once()
        report = json.loads((self.tab.data_dir/'channel-scan-last.json').read_text(encoding='utf-8'))
        self.assertEqual(report['requested_browser'], 'coccoc')
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(report['last_message'], 'Đang mở Cốc Cốc')
        self.assertEqual(report['causes'][1]['message'], 'Browser launch failed')
        self.assertNotIn('private multiline', json.dumps(report))
        self.assertFalse(self.tab.scanning)


if __name__ == '__main__':
    unittest.main()
