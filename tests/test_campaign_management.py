from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from teodub import campaign, campaign_batch
from teodub.campaign_management import update_campaign
from teodub.common import atomic_json, hash_file
from teodub.pipeline import _job_lock


class CampaignManagementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.settings = dict(voice='Mai Anh', voice_provider='local', reading_style='legacy',
                             speed=.9, original_gain=.1, translation_model='gpt-5.6-luna', translation_effort='low')
        self.rows = campaign.parse_links('https://www.douyin.com/video/111')
        self.directory = self.create()

    def create(self, **kwargs):
        return campaign.create_campaign(self.root, 'Câu cá', self.rows, self.settings,
                                        str(self.root/'originals'), str(self.root/'outputs'), **kwargs)

    def test_rename_keeps_config_hash_and_existing_queue_identity(self):
        batch = campaign_batch.create_batch(self.root, [self.directory])
        before = campaign.read_json(self.directory/'campaign.json')
        config_hash = hash_file(self.directory/'jobs/0001/config.json')
        identity = campaign_batch.read_batch(batch)['campaigns'][0]['config_identity']
        after = update_campaign(self.root, self.directory, name='Tên mới', note=' Ghi chú mới ')
        self.assertEqual(after['name'], 'Tên mới')
        self.assertEqual(after['note'], 'Ghi chú mới')
        self.assertEqual(before['items'], after['items'])
        self.assertEqual(hash_file(self.directory/'jobs/0001/config.json'), config_hash)
        self.assertEqual(campaign_batch._identity(after, self.directory), identity)
        after = update_campaign(self.root, self.directory, name='Đổi lần hai')
        self.assertEqual(campaign_batch._identity(after, self.directory), identity)

    def test_archive_and_restore_keep_videos_config_and_old_queue(self):
        batch = campaign_batch.create_batch(self.root, [self.directory])
        before = campaign.read_json(self.directory/'campaign.json')
        files = []
        for key in ('original_dir', 'output_dir'):
            folder = Path(before[key])
            folder.mkdir(parents=True)
            path = folder/'preserve.mp4'
            path.write_bytes(b'video fixture')
            files.append(path)
        config = self.directory/'jobs/0001/config.json'
        config_hash = hash_file(config)
        update_campaign(self.root, self.directory, archived=True)
        runner = Mock()
        with self.assertRaisesRegex(ValueError, 'đã xóa'):
            campaign.run_campaign(self.directory, runner=runner)
        with self.assertRaisesRegex(ValueError, 'đã xóa'):
            campaign_batch.create_batch(self.root, [self.directory])
        result = campaign_batch.run_batch(batch, runner=runner)
        self.assertEqual(result['status'], 'paused')
        runner.assert_not_called()
        restored = update_campaign(self.root, self.directory, archived=False)
        self.assertNotIn('archived_at', restored)
        self.assertEqual(before['items'], restored['items'])
        self.assertEqual(hash_file(config), config_hash)
        self.assertTrue(all(path.read_bytes() == b'video fixture' for path in files))

    def test_execution_and_orphan_job_locks_block_management(self):
        execution = self.root/'execution'
        execution.mkdir(exist_ok=True)
        for lock in (execution, self.directory, self.directory/'jobs/0001'):
            with self.subTest(lock=lock), _job_lock(lock):
                with self.assertRaises(ValueError):
                    update_campaign(self.root, self.directory, archived=True)
            self.assertNotIn('archived_at', campaign.read_json(self.directory/'campaign.json'))

    def test_invalid_metadata_or_outside_path_never_changes_manifest(self):
        before = (self.directory/'campaign.json').read_bytes()
        for fields in (dict(name=' '), dict(name='x'*151), dict(note='x'*2001), dict(archived='yes')):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                update_campaign(self.root, self.directory, **fields)
        with self.assertRaises(ValueError):
            update_campaign(self.root, self.root, name='wrong')
        self.assertEqual((self.directory/'campaign.json').read_bytes(), before)

    def test_duplicate_gui_creation_reuses_one_campaign(self):
        one = self.create(prevent_duplicates=True)
        two = self.create(prevent_duplicates=True)
        self.assertEqual(one, two)
        self.assertEqual(len(list((self.root/'campaigns').glob('*/campaign.json'))), 2)

    def test_changed_settings_or_archived_campaign_are_not_reused(self):
        one = self.create(prevent_duplicates=True)
        update_campaign(self.root, one, archived=True)
        two = self.create(prevent_duplicates=True)
        self.assertNotEqual(one, two)
        self.settings['translation_model'] = 'gpt-5.6-terra'
        three = self.create(prevent_duplicates=True)
        self.assertNotEqual(two, three)

    def test_expired_duplicate_window_allows_intentional_new_campaign(self):
        one = self.create(prevent_duplicates=True)
        manifest = campaign.read_json(one/'campaign.json')
        manifest['created_at'] = '2020-01-01T00:00:00'
        atomic_json(one/'campaign.json', manifest)
        self.assertNotEqual(one, self.create(prevent_duplicates=True))


if __name__ == '__main__':
    unittest.main()
