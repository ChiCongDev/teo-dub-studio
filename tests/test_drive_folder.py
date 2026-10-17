import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from teodub.common import atomic_json, hash_file
from teodub.drive_delivery import drive_upload_summary, upload_completed_video
from teodub.drive_folder import normalize_folder_config
from teodub.google_drive import normalize_drive_config
from tests import test_pipeline as pipeline_fixtures
from tests import test_campaign_ui as campaign_fixtures


class DriveFolderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.directory = self.root/'runs'/'job-1'
        self.directory.mkdir(parents=True)
        self.folder = self.root/'sync folder'
        self.folder.mkdir()
        self.outputs = self.root/'outputs'/'finished'
        self.outputs.mkdir(parents=True)
        self.paths = dict(video=str(self.outputs/'video.mp4'), subtitles=str(self.outputs/'video.srt'))
        Path(self.paths['video']).write_bytes(b'validated video content')
        Path(self.paths['subtitles']).write_bytes('phụ đề Việt'.encode('utf-8'))
        self.hashes = {key: hash_file(path) for key, path in self.paths.items()}
        atomic_json(self.directory/'state.json', dict(rendered=dict(outputs=self.paths, hashes=self.hashes)))
        self.options = normalize_folder_config(dict(enabled=True, folder_path=str(self.folder)))
        self.config = dict(mode='full', directory=str(self.directory), output_dir=str(self.outputs.parent),
                           drive_data_dir=str(self.root), drive_upload=self.options)
        self.result = dict(status='completed', outputs=self.paths)
        self.cloud = self.enterContext(patch('teodub.google_drive.DriveClient'))
        self.addCleanup(self.cloud.assert_not_called)

    def deliver(self, **kwargs):
        return upload_completed_video(self.config, self.result, **kwargs)

    def test_copies_mp4_and_srt_keeps_local_and_never_claims_cloud_upload(self):
        outcome = self.deliver()
        self.assertEqual(outcome['state'], 'staged')
        self.assertIs(outcome['cloud_confirmed'], False)
        self.assertNotIn('url', outcome)
        for key, path in outcome['files'].items():
            self.assertEqual(hash_file(path), self.hashes[key])
            self.assertEqual(hash_file(self.paths[key]), self.hashes[key])
        summary = drive_upload_summary(self.config, self.directory)
        self.assertEqual(summary['state'], 'staged')
        self.assertIn('kiểm tra Drive', summary['label'])
        self.assertIn('Google Drive', summary['details'])

    def test_repeat_uses_same_folder_and_does_not_rewrite_files(self):
        first = self.deliver()
        stats = {key: Path(path).stat().st_mtime_ns for key, path in first['files'].items()}
        second = self.deliver()
        self.assertEqual(first['files'], second['files'])
        self.assertEqual(len(list(self.folder.iterdir())), 1)
        self.assertEqual(stats, {key: Path(path).stat().st_mtime_ns for key, path in second['files'].items()})

    def test_new_job_gets_own_folder_even_with_identical_filenames(self):
        first = self.deliver()
        another = self.root/'runs'/'job-2'
        another.mkdir()
        atomic_json(another/'state.json', dict(rendered=dict(outputs=self.paths, hashes=self.hashes)))
        config = dict(self.config, directory=str(another))
        second = upload_completed_video(config, self.result)
        self.assertEqual(second['state'], 'staged')
        self.assertNotEqual(first['destination'], second['destination'])

    def test_source_changed_is_rejected_before_creating_destination(self):
        Path(self.paths['video']).write_bytes(b'changed content')
        outcome = self.deliver()
        self.assertEqual(outcome['state'], 'failed')
        self.assertEqual(list(self.folder.iterdir()), [])
        self.assertEqual(self.result['status'], 'completed')

    def test_changed_destination_is_preserved_not_overwritten(self):
        first = self.deliver()
        target = Path(first['files']['video'])
        target.write_bytes(b'user changed this file')
        second = self.deliver()
        self.assertEqual(second['state'], 'failed')
        self.assertEqual(target.read_bytes(), b'user changed this file')
        self.assertEqual(hash_file(self.paths['video']), self.hashes['video'])

    def test_missing_mounted_folder_is_not_recreated_as_an_ordinary_directory(self):
        self.folder.rmdir()
        outcome = self.deliver()
        self.assertEqual(outcome['state'], 'failed')
        self.assertFalse(self.folder.exists())

    def test_cancel_does_not_copy_or_change_local_completion(self):
        cancel = threading.Event()
        cancel.set()
        outcome = self.deliver(cancel=cancel)
        self.assertEqual(outcome['state'], 'cancelled')
        self.assertEqual(list(self.folder.iterdir()), [])
        self.assertEqual(self.result['status'], 'completed')

    def test_cancel_during_copy_cleans_partial_and_retry_finishes(self):
        cancel = Mock()
        cancel.is_set.side_effect = [False, False, True]
        outcome = self.deliver(cancel=cancel)
        self.assertEqual(outcome['state'], 'cancelled')
        self.assertEqual(list(self.folder.rglob('*.part')), [])
        self.assertEqual(self.deliver()['state'], 'staged')

    def test_unfinished_preview_and_disabled_have_no_delivery(self):
        for result in (dict(status='running'), dict(status='failed'), dict(status='review_required')):
            self.assertIsNone(upload_completed_video(self.config, result))
        self.assertIsNone(upload_completed_video(dict(self.config, mode='preview'), self.result))
        self.assertIsNone(upload_completed_video(dict(self.config, drive_upload=dict(self.options, enabled=False)), self.result))
        self.assertFalse((self.directory/'drive-upload.json').exists())

    def test_source_outside_output_directory_rejected(self):
        outside = self.root/'outside.mp4'
        outside.write_bytes(Path(self.paths['video']).read_bytes())
        outputs = dict(self.paths, video=str(outside))
        atomic_json(self.directory/'state.json', dict(rendered=dict(outputs=outputs, hashes=self.hashes)))
        outcome = upload_completed_video(self.config, dict(self.result, outputs=outputs))
        self.assertEqual(outcome['state'], 'failed')
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_job_outside_data_root_rejected(self):
        self.config['drive_data_dir'] = str(self.root/'another-data')
        self.assertEqual(self.deliver()['state'], 'failed')

    def test_passive_summary_rejects_other_receipt_and_missing_file(self):
        staged = self.deliver()
        changed = dict(staged, folder_path=str(self.root/'different'))
        atomic_json(self.directory/'drive-upload.json', changed)
        self.assertNotIn('state', drive_upload_summary(self.config, self.directory))
        atomic_json(self.directory/'drive-upload.json', staged)
        Path(staged['files']['video']).unlink()
        self.assertNotIn('state', drive_upload_summary(self.config, self.directory))

    def test_config_freezes_folder_and_preserves_legacy_shape(self):
        self.assertEqual(normalize_drive_config(dict(drive_upload=self.options)), self.options)
        self.assertEqual(normalize_drive_config(self.options), self.options)
        self.assertEqual(normalize_drive_config({}), dict(version=1, enabled=False, account_id='', folder_id='', folder_name=''))
        for value in (dict(enabled=True), dict(enabled=True, folder_path='relative'),
                      dict(enabled=True, folder_path=str(self.root.anchor)), dict(enabled=1, folder_path=str(self.folder))):
            with self.assertRaises(ValueError):
                normalize_folder_config(value)


class FolderPipelineTests(unittest.TestCase):
    def test_resume_delivery_only_reuses_all_engines(self):
        fixture = pipeline_fixtures.PipelineTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        folder = fixture.root/'sync'
        folder.mkdir()
        options = normalize_folder_config(dict(enabled=True, folder_path=str(folder)))
        config = dict(fixture.config, drive_upload=options, drive_data_dir=str(fixture.root))
        from teodub.pipeline import run
        with patch('teodub.google_drive.DriveClient') as cloud:
            first = run(config)
            self.assertEqual(first['status'], 'completed')
            self.assertEqual(first['google_drive']['state'], 'staged')
            frozen = copy.deepcopy(fixture.state())
            for engine in (fixture.asr, fixture.translate, fixture.tts, fixture.render):
                engine.reset_mock()
            second = run(config)
            self.assertEqual(second['google_drive']['files'], first['google_drive']['files'])
            self.assertEqual(fixture.state(), frozen)
            for engine in (fixture.asr, fixture.translate, fixture.tts, fixture.render):
                engine.assert_not_called()
            cloud.assert_not_called()

    def test_delivery_failure_keeps_completed_local_result(self):
        fixture = pipeline_fixtures.PipelineTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        options = normalize_folder_config(dict(enabled=True, folder_path=str(fixture.root/'missing-sync')))
        from teodub.pipeline import run
        result = run(dict(fixture.config, drive_upload=options, drive_data_dir=str(fixture.root)))
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['google_drive']['state'], 'failed')
        self.assertTrue(Path(result['outputs']['video']).is_file())


class FolderUITests(unittest.TestCase):
    def setUp(self):
        self.fixture = campaign_fixtures.CampaignUITests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.studio, self.tab = self.fixture.studio, self.fixture.tab
        self.folder = Path(self.fixture.temp.name)/'sync'
        self.folder.mkdir()
        self.options = normalize_folder_config(dict(enabled=True, folder_path=str(self.folder)))
        self.cloud = self.enterContext(patch('teodub.google_drive.DriveClient'))
        self.addCleanup(self.cloud.assert_not_called)

    def test_default_dialog_needs_no_google_client_or_json(self):
        dialog = self.studio.choose_drive()
        dialog.withdraw()
        self.addCleanup(dialog.close)
        self.assertIn('thư mục đồng bộ', dialog.title())
        self.assertFalse(hasattr(dialog, 'client_path'))
        self.assertFalse(dialog.ready.get())
        dialog.folder.set(str(self.folder))
        dialog.enabled.set(True)
        dialog.save()
        self.assertFalse(dialog.closed)
        self.assertIn('đánh dấu', dialog.status.get())
        dialog.ready.set(True)
        dialog.save()
        self.assertTrue(dialog.closed)
        self.assertEqual(self.studio.drive_selection(), self.options)

    def test_new_single_campaign_and_preferences_keep_exact_folder(self):
        self.studio.fill_drive({'drive_upload': self.options})
        self.studio.input.insert('1.0', 'https://www.douyin.com/video/111')
        self.studio.fields['original_dir'].set(str(self.folder.parent/'originals'))
        self.studio.fields['output_dir'].set(str(self.folder.parent/'outputs'))
        config = self.studio.config_from_form('full')
        self.assertEqual(config['drive_upload'], self.options)
        self.assertNotIn('drive_upload', self.studio.config_from_form('preview'))
        self.studio.save_preferences()
        prefs = json.loads((self.studio.data_dir/'preferences.json').read_text(encoding='utf-8'))
        self.assertEqual(prefs['drive_upload'], self.options)
        self.tab.capture_settings()
        self.tab.create()
        manifest = json.loads((self.tab.directory/'campaign.json').read_text(encoding='utf-8'))
        self.assertEqual(manifest['settings']['drive_upload'], self.options)
        for item in manifest['items']:
            self.assertEqual(json.loads((Path(item['job'])/'config.json').read_text(encoding='utf-8'))['drive_upload'], self.options)

    def test_changed_form_does_not_change_old_job_and_staged_disables_continue(self):
        self.studio.fill_drive({'drive_upload': self.options})
        self.studio.current = dict(mode='full', drive_upload=copy.deepcopy(self.options))
        self.studio.result = dict(status='completed', google_drive=dict(state='staged', cloud_confirmed=False))
        self.studio.refresh_buttons()
        self.assertEqual(str(self.studio.continue_button['state']), 'disabled')
        second = dict(self.options, folder_path=str(self.folder.parent/'new'))
        self.studio.fill_drive({'drive_upload': second})
        self.assertEqual(self.studio.current['drive_upload'], self.options)
        self.studio.result['google_drive']['state'] = 'failed'
        self.studio.refresh_buttons()
        self.assertEqual(str(self.studio.continue_button['state']), 'normal')

    def test_editing_path_clears_sync_confirmation(self):
        self.studio.fill_drive({'drive_upload': self.options})
        dialog = self.studio.choose_drive()
        dialog.withdraw()
        self.addCleanup(dialog.close)
        self.assertTrue(dialog.ready.get())
        dialog.folder.set(str(self.folder.parent/'another'))
        self.assertFalse(dialog.ready.get())

    def test_small_window_keeps_save_visible_and_content_scrollable(self):
        dialog = self.studio.choose_drive()
        self.addCleanup(dialog.close)
        dialog.attributes('-alpha', 0)
        dialog.geometry('640x400')
        dialog.update_idletasks()
        self.assertLessEqual(dialog.save_button.winfo_rooty()+dialog.save_button.winfo_height(),
                             dialog.winfo_rooty()+dialog.winfo_height())
        bounds = dialog.canvas.bbox('all')
        self.assertGreater(bounds[3], dialog.canvas.winfo_height())
        dialog.canvas.yview_moveto(1)
        dialog.update_idletasks()
        self.assertAlmostEqual(dialog.canvas.yview()[1], 1.0, places=2)

    def test_advanced_api_is_optional_and_does_not_modify_folder_selection(self):
        self.studio.fill_drive({'drive_upload': self.options})
        dialog = self.studio.choose_drive()
        dialog.withdraw()
        with patch('teodub.google_drive_ui.configure_google_drive') as advanced:
            dialog.open_advanced()
        self.assertTrue(dialog.closed)
        self.assertEqual(self.studio.drive_selection(), self.options)
        self.assertFalse(advanced.call_args.args[2]['enabled'])


if __name__ == '__main__':
    unittest.main()
