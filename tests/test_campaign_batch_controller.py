from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from teodub.campaign_batch_controller import CampaignBatchController
from teodub.campaign import create_campaign
from teodub.campaign_batch import create_batch
from teodub.common import atomic_json


class BatchControllerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.studio = Mock(data_dir=self.root, process=None, login_pending=False, closing=False, campaign_busy=False)
        self.tab = Mock(scanning=False)
        campaign = create_campaign(self.root, 'Thử', [dict(url='https://www.douyin.com/video/123')],
            dict(voice='Mai Anh', voice_provider='local', reading_style='legacy', speed=.9, original_gain=.1),
            str(self.root/'goc'), str(self.root/'xuat'))
        self.batch = create_batch(self.root, [campaign])
        self.controller = CampaignBatchController(self.studio, self.tab)

    def test_restore_does_not_launch_and_interrupted_status_is_presented_paused(self):
        atomic_json(self.root/'last-campaign-batch.json', dict(directory=str(self.batch)))
        import json
        snapshot = json.loads((self.batch/'queue.json').read_text(encoding='utf-8'))
        snapshot.update(status='running', active_directory=snapshot['campaigns'][0]['directory'])
        atomic_json(self.batch/'queue.json', snapshot)
        with patch('teodub.campaign_batch_controller.subprocess.Popen') as spawn:
            controller = CampaignBatchController(self.studio, self.tab)
            spawn.assert_not_called()
            self.assertFalse(controller.busy)
            self.assertEqual(controller.snapshot()['status'], 'paused')
        self.assertEqual(__import__('json').loads((self.batch/'queue.json').read_text())['status'], 'running')

    def test_resume_launches_batch_worker_and_early_stop_is_retained(self):
        self.controller.directory = self.batch
        (self.batch/'stop.request').touch()
        child = Mock()
        child.poll.return_value = None
        with patch('teodub.campaign_batch_controller.subprocess.Popen', return_value=child) as spawn:
            self.controller.resume()
            command = spawn.call_args.args[0]
            self.assertIn('--campaign-batch-worker', command)
            self.assertIn('--keep-stop', command)
            self.assertFalse((self.batch/'stop.request').exists())
            self.controller.stop()
            self.assertTrue((self.batch/'stop.request').exists())
            with self.assertRaises(ValueError):
                self.controller.resume()
            spawn.assert_called_once()
        self.assertTrue(self.studio.campaign_busy)

    def test_single_video_and_scanner_block_batch_start(self):
        self.controller.directory = self.batch
        with patch('teodub.campaign_batch_controller.subprocess.Popen') as spawn:
            self.studio.process = Mock()
            with self.assertRaises(ValueError):
                self.controller.resume()
            self.studio.process = None
            self.tab.scanning = True
            with self.assertRaises(ValueError):
                self.controller.resume()
            spawn.assert_not_called()

    def test_pointer_outside_batch_root_is_ignored(self):
        atomic_json(self.root/'last-campaign-batch.json', dict(directory=str(self.root)))
        self.assertIsNone(CampaignBatchController(self.studio, self.tab).directory)

    def test_resume_launch_failure_uses_only_new_log_even_when_queue_was_paused(self):
        import json
        snapshot = json.loads((self.batch/'queue.json').read_text(encoding='utf-8'))
        snapshot.update(status='paused', message='Lý do cũ')
        atomic_json(self.batch/'queue.json', snapshot)
        previous = 'Lỗi của lần trước\n'.encode('utf-8')
        log = self.batch/'batch-worker.log'
        log.write_bytes(previous)
        self.controller.directory = self.batch
        child = Mock()
        child.poll.return_value = 2
        with patch('teodub.campaign_batch_controller.subprocess.Popen', return_value=child):
            self.controller.resume()
        self.assertEqual(self.controller.log_start, len(previous))
        self.assertNotIn('Lỗi của lần trước', self.controller.snapshot()['message'])
        log.write_bytes(previous+'Tiến trình khác đang xử lý\n'.encode('utf-8'))
        self.assertEqual(self.controller.snapshot()['message'], 'Chưa khởi động: Tiến trình khác đang xử lý')
        self.assertEqual(json.loads((self.batch/'queue.json').read_text(encoding='utf-8'))['message'], 'Lý do cũ')


if __name__ == '__main__':
    unittest.main()
