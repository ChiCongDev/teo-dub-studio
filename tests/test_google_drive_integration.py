"""Offline contracts for optional Drive delivery after local video completion."""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from teodub import campaign, pipeline
from teodub.common import atomic_json, check_cancel, hash_file
from tests import test_campaign as campaign_fixtures
from tests import test_campaign_ui as campaign_ui_fixtures
from tests import test_pipeline as pipeline_fixtures


def selected_drive(account_id='a' * 32, folder_id='folder_fixture'):
    return dict(version=1, enabled=True, account_id=account_id,
                folder_id=folder_id, folder_name='Thành phẩm')


def uploaded():
    return dict(state='uploaded', file_id='uploaded_fixture',
                url='https://drive.google.com/file/d/uploaded_fixture/view',
                message='Đã lưu video lên Google Drive.')


class DrivePipelineIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.f = pipeline_fixtures.PipelineTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.config = dict(self.f.config, drive_upload=selected_drive(),
                           drive_data_dir=str(self.f.root))

    def run_job(self, **overrides):
        return pipeline.run(dict(self.config, **overrides))

    def test_local_completion_is_durable_before_cloud_delivery(self):
        def deliver(config, result, **kwargs):
            saved = json.loads((self.f.directory/'result.json').read_text(encoding='utf-8'))
            self.assertEqual(saved['status'], 'completed')
            self.assertEqual(saved['outputs'], result['outputs'])
            self.assertEqual(self.f.state()['rendered']['outputs'], result['outputs'])
            self.assertEqual(config['drive_upload'], selected_drive())
            self.assertEqual(config['drive_data_dir'], str(self.f.root))
            return uploaded()
        with patch('teodub.drive_delivery.upload_completed_video', side_effect=deliver) as delivery:
            result = self.run_job()
        delivery.assert_called_once()
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['google_drive']['state'], 'uploaded')
        self.assertEqual(json.loads((self.f.directory/'result.json').read_text(encoding='utf-8')), result)
        self.f.render.assert_called_once()

    def test_drive_error_does_not_fail_local_video_or_hide_outputs(self):
        outcome = dict(state='failed', message='Google Drive hết dung lượng.')
        with patch('teodub.drive_delivery.upload_completed_video', return_value=outcome):
            result = self.run_job()
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['google_drive'], outcome)
        self.assertTrue(Path(result['outputs']['video']).is_file())
        self.assertNotIn('Google Drive hết dung lượng', result['message'])
        self.assertEqual(self.f.state()['rendered']['outputs'], result['outputs'])

    def test_unexpected_delivery_exception_cannot_destroy_completed_result(self):
        with patch('teodub.drive_delivery.upload_completed_video', side_effect=OSError('cloud log unavailable')):
            result = self.run_job()
        self.assertEqual(result['status'], 'completed')
        self.assertTrue(Path(result['outputs']['video']).is_file())
        self.assertEqual(result['google_drive']['state'], 'failed')
        self.assertEqual(json.loads((self.f.directory/'result.json').read_text(encoding='utf-8'))['status'], 'completed')

    def test_cloud_retry_resume_reuses_all_local_engines_and_original_state(self):
        with patch('teodub.drive_delivery.upload_completed_video',
                   return_value=dict(state='failed', message='Mạng bị ngắt.')):
            first = self.run_job()
        frozen = copy.deepcopy(self.f.state())
        for engine in (self.f.asr, self.f.translate, self.f.tts, self.f.render):
            engine.reset_mock()
        with patch('teodub.drive_delivery.upload_completed_video', return_value=uploaded()) as delivery:
            second = self.run_job()
        delivery.assert_called_once()
        self.assertEqual(second['status'], 'completed')
        self.assertEqual(second['outputs'], first['outputs'])
        self.assertEqual(self.f.state(), frozen)
        for engine in (self.f.asr, self.f.translate, self.f.tts, self.f.render):
            engine.assert_not_called()

    def test_upload_cancellation_preserves_local_completion(self):
        with patch('teodub.drive_delivery.upload_completed_video',
                   return_value=dict(state='cancelled', message='Đã dừng tải lên Drive.')):
            result = self.run_job()
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['google_drive']['state'], 'cancelled')
        self.assertTrue(Path(result['outputs']['video']).is_file())

    def test_changed_cloud_destination_cannot_change_a_frozen_job(self):
        with patch('teodub.drive_delivery.upload_completed_video', return_value=uploaded()):
            self.run_job()
        for changes in (dict(account_id='b' * 32), dict(folder_id='another_folder')):
            with self.subTest(changes=changes):
                chosen = dict(selected_drive(), **changes)
                with patch('teodub.drive_delivery.upload_completed_video') as delivery:
                    result = self.run_job(drive_upload=chosen)
                self.assertEqual(result['status'], 'failed')
                delivery.assert_not_called()

    def test_failed_dubbing_does_not_attempt_cloud_delivery(self):
        self.f.tts.side_effect = RuntimeError('Tạo giọng chưa xong.')
        with patch('teodub.drive_delivery.upload_completed_video') as delivery:
            result = self.run_job()
        self.assertEqual(result['status'], 'failed')
        delivery.assert_not_called()


class DriveCampaignIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.f = campaign_fixtures.CampaignTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.settings = dict(self.f.settings, drive_upload=selected_drive(),
                             drive_data_dir=str(self.f.root))
        self.directory = campaign.create_campaign(self.f.root, 'Cloud fixtures', self.f.records,
            self.settings, str(self.f.root/'goc'), str(self.f.root/'xuat'))

    def test_campaign_settings_and_every_child_freeze_drive_destination(self):
        manifest = campaign.read_json(self.directory/'campaign.json')
        frozen = copy.deepcopy(self.settings['drive_upload'])
        self.settings['drive_upload']['folder_id'] = 'changed_later'
        self.assertEqual(manifest['settings']['drive_upload'], frozen)
        self.assertEqual(manifest['settings']['drive_data_dir'], str(self.f.root))
        for item in manifest['items']:
            config_file = Path(item['job'])/'config.json'
            child = campaign.read_json(config_file)
            self.assertEqual(child['drive_upload'], frozen)
            self.assertEqual(child['drive_data_dir'], str(self.f.root))
            self.assertEqual(hash_file(config_file), item['config_sha256'])

    def complete_campaign(self):
        def runner(path, stop):
            result = self.f.runner(path, stop)
            atomic_json(path.parent/'result.json', result)
            return result
        with patch('teodub.drive_delivery.upload_completed_video', return_value=None):
            return campaign.run_campaign(self.directory, runner=runner)

    def test_completed_campaign_continues_upload_without_relaunching_dubbing(self):
        first = self.complete_campaign()
        before = copy.deepcopy(first)
        runner = Mock(side_effect=AssertionError('Completed video must never rerender'))
        with patch('teodub.drive_delivery.upload_completed_video', return_value=uploaded()) as delivery:
            resumed = campaign.run_campaign(self.directory, runner=runner)
        runner.assert_not_called()
        self.assertEqual(delivery.call_count, 2)
        self.assertEqual(resumed['status'], 'completed')
        for earlier, after in zip(before['items'], resumed['items']):
            self.assertEqual(after['status'], earlier['status'])
            self.assertEqual(after['outputs'], earlier['outputs'])
            self.assertEqual(after['completed_at'], earlier['completed_at'])
            self.assertEqual(after['processing_seconds'], earlier['processing_seconds'])
            self.assertEqual(after['google_drive']['state'], 'uploaded')

    def test_real_delivery_resume_receives_a_compatible_local_cancellation_adapter(self):
        self.complete_campaign()
        def upload(options, *, source_id, video_path, sha256, cancel=None, progress=None):
            check_cancel(cancel)
            return dict(uploaded(), account_id=options['account_id'], folder_id=options['folder_id'],
                        sha256=sha256, name=Path(video_path).name, size=Path(video_path).stat().st_size)
        runner = Mock(side_effect=AssertionError('Only resume delivery'))
        with patch('teodub.google_drive.DriveClient') as client:
            client.return_value.upload_video.side_effect = upload
            resumed = campaign.run_campaign(self.directory, runner=runner)
        self.assertEqual(client.return_value.upload_video.call_count, 2)
        self.assertTrue(all(item['google_drive']['state'] == 'uploaded' for item in resumed['items']))
        self.assertEqual(resumed['status'], 'completed')
        runner.assert_not_called()

    def test_delivery_resume_observes_external_batch_stop_without_failing_local_video(self):
        self.complete_campaign()
        external_stop = self.f.root/'external-batch.stop'
        calls = []
        def upload(options, *, source_id, video_path, sha256, cancel=None, progress=None):
            check_cancel(cancel)
            calls.append(source_id)
            external_stop.touch()
            check_cancel(cancel)
            self.fail('External batch stop must cancel transfer')
        runner = Mock(side_effect=AssertionError('Only resume delivery'))
        with patch('teodub.google_drive.DriveClient') as client:
            client.return_value.upload_video.side_effect = upload
            resumed = campaign._run_campaign_locked(self.directory, runner=runner, external_stop=external_stop)
        self.assertEqual(len(calls), 1)
        self.assertEqual(resumed['status'], 'paused')
        self.assertEqual(resumed['items'][0]['status'], 'completed')
        self.assertEqual(resumed['items'][0]['google_drive']['state'], 'cancelled')
        self.assertTrue(Path(resumed['items'][0]['outputs']['video']).is_file())
        runner.assert_not_called()

    def test_drive_failure_does_not_change_campaign_completion_or_error_count(self):
        self.complete_campaign()
        with patch('teodub.drive_delivery.upload_completed_video',
                   return_value=dict(state='failed', message='Chưa kết nối Google Drive.')):
            resumed = campaign.run_campaign(self.directory, runner=Mock())
        self.assertEqual(resumed['status'], 'completed')
        self.assertEqual(campaign.totals(resumed)['failed'], 0)
        self.assertTrue(all(item['status'] == 'completed' for item in resumed['items']))
        self.assertTrue(all(item['google_drive']['state'] == 'failed' for item in resumed['items']))

    def test_cloud_failure_from_new_child_does_not_stop_the_next_video(self):
        calls = []
        def runner(path, stop):
            calls.append(path.parent.name)
            return dict(self.f.runner(path, stop),
                        google_drive=dict(state='failed', message='Drive đăng nhập đã hết hạn.'))
        with patch('teodub.drive_delivery.upload_completed_video', return_value=None):
            result = campaign.run_campaign(self.directory, runner=runner)
        self.assertEqual(calls, ['0001', '0002'])
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(campaign.totals(result)['failed'], 0)
        self.assertTrue(all(item['google_drive']['state'] == 'failed' for item in result['items']))

    def test_new_child_drive_outcome_is_kept_separately_from_dubbing(self):
        def runner(path, stop):
            return dict(self.f.runner(path, stop), google_drive=uploaded())
        with patch('teodub.drive_delivery.upload_completed_video', return_value=None):
            result = campaign.run_campaign(self.directory, runner=runner)
        self.assertEqual(result['status'], 'completed')
        for item in result['items']:
            self.assertEqual(item['google_drive']['state'], 'uploaded')
            self.assertEqual(item['message'], 'Done')

    def test_review_required_done_row_is_not_automatically_uploaded(self):
        self.complete_campaign()
        manifest = campaign.read_json(self.directory/'campaign.json')
        for item in manifest['items']:
            item['status'] = 'review_required'
        atomic_json(self.directory/'campaign.json', manifest)
        with patch('teodub.drive_delivery.upload_completed_video') as delivery:
            resumed = campaign.run_campaign(self.directory, runner=Mock())
        delivery.assert_not_called()
        self.assertTrue(all(item['status'] == 'review_required' for item in resumed['items']))

    def test_tampered_completed_video_is_rejected_before_upload_or_rerender(self):
        complete = self.complete_campaign()
        Path(complete['items'][0]['outputs']['video']).write_bytes(b'replaced video')
        runner = Mock()
        with patch('teodub.drive_delivery.upload_completed_video') as delivery:
            result = campaign.run_campaign(self.directory, runner=runner)
        self.assertEqual(result['status'], 'paused')
        self.assertIn('đã thay đổi', result['message'])
        delivery.assert_not_called()
        runner.assert_not_called()

    def test_completed_job_config_tamper_is_rejected_before_cloud_delivery(self):
        self.complete_campaign()
        config_file = self.directory/'jobs/0001/config.json'
        config = campaign.read_json(config_file)
        config['drive_upload']['folder_id'] = 'changed_without_approval'
        atomic_json(config_file, config)
        runner = Mock()
        with patch('teodub.drive_delivery.upload_completed_video') as delivery:
            result = campaign.run_campaign(self.directory, runner=runner)
        self.assertEqual(result['status'], 'paused')
        self.assertIn('đã bị thay đổi', result['message'])
        delivery.assert_not_called()
        runner.assert_not_called()

    def test_cloud_destination_participates_in_duplicate_campaign_identity(self):
        def create(settings):
            return campaign.create_campaign(self.f.root, 'Same request', self.f.records,
                settings, str(self.f.root/'goc'), str(self.f.root/'xuat'), prevent_duplicates=True)
        first = create(self.settings)
        self.assertEqual(create(self.settings), first)
        changed = dict(self.settings, drive_upload=dict(selected_drive(), folder_id='another_folder'))
        self.assertNotEqual(create(changed), first)


class DriveDeliveryBindingTests(unittest.TestCase):
    def setUp(self):
        self.f = pipeline_fixtures.PipelineTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.result = self.f.run_job()
        self.config = dict(self.f.config, drive_upload=selected_drive(),
                           drive_data_dir=str(self.f.root))
        self.saved_state = copy.deepcopy(self.f.state())

    def receipt(self, **changes):
        value = dict(uploaded(), sha256=self.f.state()['rendered']['hashes']['video'],
                    account_id='a' * 32, folder_id='folder_fixture',
                    name=Path(self.result['outputs']['video']).name,
                    size=Path(self.result['outputs']['video']).stat().st_size)
        return dict(value, **changes)

    def deliver(self, config=None, result=None, **options):
        from teodub.drive_delivery import upload_completed_video
        return upload_completed_video(config or self.config, result or self.result, **options)

    def test_only_the_verified_final_video_is_passed_to_drive(self):
        with patch('teodub.google_drive.DriveClient') as client:
            client.return_value.upload_video.return_value = self.receipt()
            outcome = self.deliver()
        self.assertEqual(outcome['state'], 'uploaded')
        client.assert_called_once_with(self.f.root.resolve())
        args = client.return_value.upload_video.call_args
        self.assertEqual(args.args[0], selected_drive())
        self.assertEqual(args.kwargs['source_id'], 'job-one')
        self.assertEqual(Path(args.kwargs['video_path']), Path(self.result['outputs']['video']))
        self.assertEqual(args.kwargs['sha256'], self.saved_state['rendered']['hashes']['video'])
        self.assertEqual(self.f.state(), self.saved_state)
        stored = json.loads((self.f.directory/'drive-upload.json').read_text(encoding='utf-8'))
        self.assertEqual(stored['state'], 'uploaded')
        self.assertEqual(stored['sha256'], self.saved_state['rendered']['hashes']['video'])

    def test_old_disabled_preview_and_unfinished_jobs_do_not_create_cloud_state(self):
        cases = [(self.f.config, self.result),
                 (dict(self.config, drive_upload=dict(version=1, enabled=False)), self.result),
                 (dict(self.config, mode='preview'), dict(status='completed', outputs=dict(audio='preview.wav'))),
                 (self.config, dict(self.result, status='review_required')),
                 (self.config, dict(self.result, status='failed'))]
        with patch('teodub.google_drive.DriveClient') as client:
            for config, result in cases:
                with self.subTest(config=config, status=result['status']):
                    self.assertIsNone(self.deliver(config, result))
        client.assert_not_called()
        self.assertFalse((self.f.directory/'drive-upload.json').exists())

    def test_missing_or_invalid_durable_hash_is_never_recomputed_as_approval(self):
        for hashes in ({}, dict(video='invalid_hash')):
            with self.subTest(hashes=hashes):
                state = copy.deepcopy(self.saved_state)
                state['rendered']['hashes'] = hashes
                atomic_json(self.f.directory/'state.json', state)
                with patch('teodub.google_drive.DriveClient') as client:
                    outcome = self.deliver()
                self.assertEqual(outcome['state'], 'failed')
                client.assert_not_called()

    def test_rendered_output_mismatch_is_rejected_before_drive_client(self):
        result = dict(self.result, outputs=dict(self.result['outputs'], video=str(self.f.source)))
        with patch('teodub.google_drive.DriveClient') as client:
            outcome = self.deliver(result=result)
        self.assertEqual(outcome['state'], 'failed')
        client.assert_not_called()

    def test_job_outside_selected_data_directory_is_not_uploaded(self):
        elsewhere = self.f.root/'different data root'
        with patch('teodub.google_drive.DriveClient') as client:
            outcome = self.deliver(config=dict(self.config, drive_data_dir=str(elsewhere)))
        self.assertEqual(outcome['state'], 'failed')
        client.assert_not_called()

    def test_success_must_match_account_folder_hash_file_id_and_url(self):
        original = self.receipt()
        for field, wrong in (('account_id', 'b' * 32), ('folder_id', 'wrong_folder'),
                             ('sha256', '0' * 64), ('file_id', ''), ('url', ''),
                             ('file_id', '../another'),
                             ('url', 'https://example.com/not-a-drive-receipt'),
                             ('url', 'https://drive.google.com/file/d/different_file/view'),
                             ('state', 'unknown')):
            with self.subTest(field=field):
                receipt = dict(original, **{field: wrong})
                with patch('teodub.google_drive.DriveClient') as client:
                    client.return_value.upload_video.return_value = receipt
                    outcome = self.deliver()
                self.assertEqual(outcome['state'], 'failed')
                self.assertNotEqual(outcome.get('url'), original['url'])

    def test_cloud_status_write_failure_preserves_local_render_state(self):
        with patch('teodub.google_drive.DriveClient') as client:
            client.return_value.upload_video.return_value = self.receipt()
            with patch('teodub.drive_delivery.atomic_json', side_effect=OSError('Disk unavailable')):
                outcome = self.deliver()
        self.assertEqual(outcome['state'], 'failed')
        self.assertEqual(self.f.state(), self.saved_state)
        self.assertEqual(self.result['status'], 'completed')
        self.assertTrue(Path(self.result['outputs']['video']).exists())
        client.return_value.upload_video.assert_not_called()

    def test_passive_summary_does_not_open_client_or_create_files(self):
        from teodub.drive_delivery import drive_upload_summary
        before = set(self.f.directory.iterdir())
        with patch('teodub.google_drive.DriveClient') as client:
            summary = drive_upload_summary(self.config, self.f.directory)
        client.assert_not_called()
        self.assertEqual(summary['label'], 'Chờ tải lên')
        self.assertEqual(set(self.f.directory.iterdir()), before)

    def test_uploaded_summary_is_bound_to_this_job_and_current_render_hash(self):
        from teodub.drive_delivery import drive_upload_summary
        receipt = dict(self.receipt(), source_id='job-one', percent=100)
        for changes in ({'source_id': 'another job'}, {'sha256': '0' * 64},
                        {'account_id': 'b' * 32}, {'folder_id': 'wrong folder'},
                        {'file_id': ''}, {'url': ''}, {'file_id': '../another'},
                        {'url': 'https://example.com/not-a-drive-receipt'},
                        {'url': 'https://drive.google.com/file/d/different_file/view'}):
            with self.subTest(changes=changes):
                atomic_json(self.f.directory/'drive-upload.json', dict(receipt, **changes))
                with patch('teodub.google_drive.DriveClient') as client:
                    summary = drive_upload_summary(self.config, self.f.directory)
                self.assertEqual(summary['label'], 'Cần kiểm tra tải lên')
                client.assert_not_called()

    def test_matching_uploaded_summary_shows_verified_receipt_link(self):
        from teodub.drive_delivery import drive_upload_summary
        receipt = dict(self.receipt(), source_id='job-one', percent=100)
        atomic_json(self.f.directory/'drive-upload.json', receipt)
        with patch('teodub.google_drive.DriveClient') as client:
            summary = drive_upload_summary(self.config, self.f.directory)
        self.assertEqual(summary['label'], 'Đã lưu trên Drive')
        self.assertIn(receipt['url'], summary['details'])
        client.assert_not_called()


class DriveCampaignSelectionUITests(unittest.TestCase):
    def setUp(self):
        self.f = campaign_ui_fixtures.CampaignUITests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)

    def test_invalid_ticked_drive_does_not_silently_create_an_unbacked_campaign(self):
        self.f.studio.fields['drive_enabled'].set(True)
        self.f.tab.capture_settings()
        with patch('teodub.campaign_ui.subprocess.Popen') as spawn:
            self.f.tab.create()
        self.assertIsNone(self.f.tab.directory)
        self.assertEqual(list((self.f.tab.data_dir/'campaigns').glob('*/campaign.json')), [])
        self.assertIn('Google Drive', self.f.tab.status.get())
        spawn.assert_not_called()

    def test_failed_drive_selection_preserves_the_last_complete_settings_snapshot(self):
        original = copy.deepcopy(self.f.tab.settings)
        self.f.studio.fields['drive_enabled'].set(True)
        self.f.tab.capture_settings()
        self.assertEqual(self.f.tab.settings, original)
        self.f.studio.fields['drive_enabled'].set(False)
        self.f.tab.capture_settings()
        self.f.tab.create()
        self.assertIsNotNone(self.f.tab.directory)
        manifest = campaign.read_json(self.f.tab.directory/'campaign.json')
        self.assertFalse(manifest['settings']['drive_upload']['enabled'])


if __name__ == '__main__':
    unittest.main()
