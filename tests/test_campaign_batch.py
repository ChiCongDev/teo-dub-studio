import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from teodub import campaign, campaign_batch
from teodub.common import atomic_json, hash_file


class CampaignBatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        settings = dict(voice='Mai Anh', voice_provider='local', reading_style='legacy',
                        speed=.9, original_gain=.1, translation_model='gpt-5.6-luna',
                        translation_effort='low', burn_subtitles=False,
                        subtitle_color='white', subtitle_weight='bold')
        self.paths = [campaign.create_campaign(self.root, name,
                      campaign.parse_links(f'https://www.douyin.com/video/{number}'), settings,
                      str(self.root/'originals'), str(self.root/'outputs'))
                      for number, name in enumerate(('Chiến dịch một', 'Chiến dịch hai', 'Chiến dịch ba'), 1)]
        self.calls = []
        self.video_calls = []

    def make_batch(self, paths=None):
        return campaign_batch.create_batch(self.root, paths or self.paths)

    def video_runner(self, config_path, stop):
        self.video_calls.append(config_path)
        config = campaign.read_json(config_path)
        output = Path(config['output_dir'])/(config['campaign_basename']+'.mp4')
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b'completed video fixture '+config_path.parent.parent.parent.name.encode())
        outputs = dict(video=str(output))
        atomic_json(config_path.parent/'state.json', dict(rendered=dict(
                    outputs=outputs, hashes=dict(video=hash_file(output)))))
        return dict(status='completed', message='Done', outputs=outputs)

    def runner(self, path, *, external_stop):
        self.calls.append(path)
        return campaign._run_campaign_locked(path, external_stop=external_stop,
                                             runner=self.video_runner)

    def test_create_deduplicates_in_selected_order_without_running_or_mutation(self):
        before = {p: (p/'campaign.json').read_bytes() for p in self.paths}
        directory = self.make_batch([self.paths[2], self.paths[0], self.paths[2]])
        result = campaign_batch.read_batch(directory)
        self.assertEqual(result['status'], 'ready')
        self.assertEqual([x['directory'] for x in result['campaigns']], list(map(str, [self.paths[2], self.paths[0]])))
        self.assertEqual([x['name'] for x in result['campaigns']], ['Chiến dịch ba', 'Chiến dịch một'])
        self.assertEqual(campaign.read_json(self.root/'last-campaign-batch.json'), dict(directory=str(directory)))
        self.assertEqual(before, {p: (p/'campaign.json').read_bytes() for p in self.paths})
        self.assertEqual(self.video_calls, [])

    def test_real_helper_runs_serially_and_completed_resume_does_no_work(self):
        directory = self.make_batch()
        result = campaign_batch.run_batch(directory, runner=self.runner)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(self.calls, self.paths)
        self.assertIsNone(result['active_directory'])
        config_contents = [(p/'jobs/0001/config.json').read_bytes() for p in self.paths]
        result = campaign_batch.run_batch(directory, runner=self.runner)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(self.calls, self.paths)
        self.assertEqual(len(self.video_calls), 3)
        self.assertEqual(config_contents, [(p/'jobs/0001/config.json').read_bytes() for p in self.paths])

    def test_batch_holds_lane_across_campaigns(self):
        directory = self.make_batch()
        def checked(path, *, external_stop):
            with self.assertRaises(ValueError):
                campaign.run_campaign(self.paths[-1], runner=self.video_runner)
            queue = campaign_batch.read_batch(directory)
            self.assertEqual(queue['active_directory'], str(path))
            self.assertEqual(queue['status'], 'running')
            return self.runner(path, external_stop=external_stop)
        result = campaign_batch.run_batch(directory, runner=checked)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(self.calls, self.paths)

    def test_stop_pauses_before_next_campaign_and_resume_skips_completed(self):
        directory = self.make_batch()
        def stop_after(path, *, external_stop):
            result = self.runner(path, external_stop=external_stop)
            external_stop.touch()
            return result
        result = campaign_batch.run_batch(directory, runner=stop_after)
        self.assertEqual(result['status'], 'paused')
        self.assertEqual(self.calls, self.paths[:1])
        self.assertEqual([x['status'] for x in result['campaigns']], ['completed', 'pending', 'pending'])
        result = campaign_batch.run_batch(directory, runner=self.runner)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(self.calls, self.paths)

    def test_shared_stop_reaches_current_video_helper(self):
        directory = self.make_batch()
        def stopped_video(config_path, stop):
            (directory/'stop.request').touch()
            self.assertTrue(stop.exists())
            return dict(status='cancelled', message='Đã dừng video')
        def stopped_campaign(path, *, external_stop):
            return campaign._run_campaign_locked(path, external_stop=external_stop, runner=stopped_video)
        result = campaign_batch.run_batch(directory, runner=stopped_campaign)
        self.assertEqual(result['status'], 'paused')
        self.assertEqual([x['status'] for x in result['campaigns']], ['paused', 'pending', 'pending'])
        self.assertEqual(campaign.read_json(self.paths[0]/'campaign.json')['items'][0]['status'], 'cancelled')

    def test_keep_stop_prevents_startup_race_and_read_never_clears_stop(self):
        directory = self.make_batch()
        stop = directory/'stop.request'
        stop.touch()
        campaign_batch.read_batch(directory)
        self.assertTrue(stop.exists())
        result = campaign_batch.run_batch(directory, runner=self.runner, clear_stop=False)
        self.assertEqual(result['status'], 'paused')
        self.assertEqual(self.calls, [])
        result = campaign_batch.run_batch(directory, runner=self.runner)
        self.assertEqual(result['status'], 'completed')
        self.assertFalse(stop.exists())

    def test_quota_pauses_entire_queue_without_retry_or_next_campaign(self):
        directory = self.make_batch()
        def quota(config_path, stop):
            self.video_calls.append(config_path)
            return dict(status='failed', message='Hết hạn mức Codex; đăng nhập lại')
        def limited(path, *, external_stop):
            self.calls.append(path)
            return campaign._run_campaign_locked(path, external_stop=external_stop, runner=quota)
        result = campaign_batch.run_batch(directory, runner=limited)
        self.assertEqual(result['status'], 'paused')
        self.assertEqual(self.calls, self.paths[:1])
        self.assertEqual(len(self.video_calls), 1)
        self.assertEqual(result['campaigns'][1]['status'], 'pending')
        # Explicit resume does not authorize an automatic paid retry of the failed video.
        result = campaign_batch.run_batch(directory, runner=self.runner)
        self.assertEqual(result['status'], 'needs_attention')
        self.assertEqual(len(self.video_calls), 3)
        self.assertEqual(campaign.read_json(self.paths[0]/'campaign.json')['items'][0]['status'], 'failed')

    def test_ordinary_failures_continue_and_are_retained_on_resume(self):
        directory = self.make_batch()
        def regular(path, *, external_stop):
            if path == self.paths[1]:
                self.calls.append(path)
                return campaign._run_campaign_locked(path, external_stop=external_stop,
                    runner=lambda config, stop: dict(status='failed', message='Video bị xóa'))
            return self.runner(path, external_stop=external_stop)
        result = campaign_batch.run_batch(directory, runner=regular)
        self.assertEqual(result['status'], 'needs_attention')
        self.assertEqual(self.calls, self.paths)
        self.assertEqual([x['status'] for x in result['campaigns']], ['completed', 'needs_attention', 'completed'])
        result = campaign_batch.run_batch(directory, runner=self.runner)
        self.assertEqual(result['status'], 'needs_attention')
        self.assertEqual(self.calls, self.paths)

    def test_external_manual_failed_video_retry_updates_saved_queue(self):
        directory = self.make_batch(self.paths[:1])
        def failed(path, *, external_stop):
            return campaign._run_campaign_locked(path, external_stop=external_stop,
                runner=lambda config, stop: dict(status='failed', message='Video không tải được'))
        result = campaign_batch.run_batch(directory, runner=failed)
        self.assertEqual(result['status'], 'needs_attention')
        campaign.run_campaign(self.paths[0], retry_failed=True, runner=self.video_runner)
        result = campaign_batch.run_batch(directory, runner=self.runner)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(self.calls, [])

    def test_crashed_running_row_only_restarts_after_explicit_resume(self):
        directory = self.make_batch()
        queue = campaign_batch.read_batch(directory)
        queue.update(status='running', active_directory=str(self.paths[0]))
        queue['campaigns'][0]['status'] = 'running'
        atomic_json(directory/'queue.json', queue)
        self.assertEqual(campaign_batch.read_batch(directory)['status'], 'running')
        self.assertEqual(self.calls, [])
        result = campaign_batch.run_batch(directory, runner=self.runner)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(self.calls, self.paths)

    def test_runner_error_pauses_queue(self):
        directory = self.make_batch()
        result = campaign_batch.run_batch(directory, runner=lambda *args, **kwargs: (_ for _ in ()).throw(ValueError('Worker vẫn đang chạy')))
        self.assertEqual(result['status'], 'paused')
        self.assertIn('Worker vẫn', result['message'])
        self.assertEqual(result['campaigns'][0]['status'], 'paused')
        self.assertIsNone(result['active_directory'])

    def test_changed_campaign_snapshot_pauses_without_running(self):
        directory = self.make_batch()
        manifest = campaign.read_json(self.paths[0]/'campaign.json')
        manifest['settings']['translation_model'] = 'gpt-6-astra'
        atomic_json(self.paths[0]/'campaign.json', manifest)
        result = campaign_batch.run_batch(directory, runner=self.runner)
        self.assertEqual(result['status'], 'paused')
        self.assertIn('đã thay đổi', result['message'])
        self.assertEqual(self.calls, [])

    def test_changed_completed_config_output_or_status_never_regenerates(self):
        for change in ('config', 'output', 'status'):
            with self.subTest(change=change):
                directory = self.make_batch(self.paths[:1])
                campaign_batch.run_batch(directory, runner=self.runner)
                manifest = campaign.read_json(self.paths[0]/'campaign.json')
                if change == 'config':
                    target = self.paths[0]/'jobs/0001/config.json'
                    original = target.read_bytes()
                    target.write_bytes(original+b' ')
                elif change == 'output':
                    target = Path(manifest['items'][0]['outputs']['video'])
                    original = target.read_bytes()
                    target.write_bytes(b'altered output')
                else:
                    target = self.paths[0]/'campaign.json'
                    original = target.read_bytes()
                    manifest['status'] = 'ready'
                    atomic_json(target, manifest)
                before_calls = list(self.calls)
                result = campaign_batch.run_batch(directory, runner=self.runner)
                self.assertEqual(result['status'], 'paused')
                self.assertEqual(self.calls, before_calls)
                target.write_bytes(original)

    def test_campaign_or_lane_mutex_prevents_competing_queues(self):
        directory = self.make_batch()
        lane = self.root/'execution'
        lane.mkdir()
        for locked in (directory, lane):
            with self.subTest(lock=locked):
                with campaign._job_lock(locked):
                    with self.assertRaises(ValueError):
                        campaign_batch.run_batch(directory, runner=self.runner)
        self.assertEqual(campaign_batch.read_batch(directory)['status'], 'ready')
        self.assertEqual(self.calls, [])

    def test_create_rejects_empty_missing_outside_and_nested_directories(self):
        outside = self.root/'outside'
        outside.mkdir()
        for paths in ([], [outside], [self.paths[0]/'jobs/0001'], [self.root/'campaigns/missing']):
            with self.subTest(paths=paths), self.assertRaises(ValueError):
                campaign_batch.create_batch(self.root, paths)
        self.assertFalse((self.root/'last-campaign-batch.json').exists())

    def test_resume_rejects_outside_campaign_before_any_write(self):
        directory = self.make_batch()
        queue = campaign_batch.read_batch(directory)
        queue['campaigns'][0]['directory'] = str(self.root/'outside')
        atomic_json(directory/'queue.json', queue)
        before = (directory/'queue.json').read_bytes()
        (directory/'stop.request').touch()
        with self.assertRaises(ValueError):
            campaign_batch.run_batch(directory, runner=self.runner)
        self.assertEqual((directory/'queue.json').read_bytes(), before)
        self.assertTrue((directory/'stop.request').exists())
        self.assertEqual(self.calls, [])

    def test_corrupted_queue_and_duplicates_are_not_executed(self):
        directory = self.make_batch()
        pristine = (directory/'queue.json').read_bytes()
        for content in ('[]', '{broken', json.dumps(dict(schema=1, id=directory.name, status='ready', campaigns=[]))):
            with self.subTest(content=content):
                (directory/'queue.json').write_text(content, encoding='utf-8')
                with self.assertRaises(ValueError):
                    campaign_batch.run_batch(directory, runner=self.runner)
        (directory/'queue.json').write_bytes(pristine)
        queue = campaign_batch.read_batch(directory)
        queue['campaigns'].append(dict(queue['campaigns'][0]))
        atomic_json(directory/'queue.json', queue)
        with self.assertRaises(ValueError):
            campaign_batch.run_batch(directory, runner=self.runner)
        self.assertEqual(self.calls, [])

    def test_symlink_campaign_and_queue_json_are_rejected(self):
        alias = self.root/'campaigns/alias'
        try:
            alias.symlink_to(self.paths[0], target_is_directory=True)
        except OSError as exc:
            self.skipTest(f'Symlinks unavailable: {exc}')
        with self.assertRaises(ValueError):
            self.make_batch([alias])
        directory = self.make_batch()
        target = directory/'queue.json'
        saved = self.root/'queue-copy.json'
        target.replace(saved)
        target.symlink_to(saved)
        with self.assertRaises(ValueError):
            campaign_batch.run_batch(directory, runner=self.runner)
        self.assertEqual(self.calls, [])

    def test_link_rejection_without_windows_symlink_privileges(self):
        directory = self.make_batch()
        for suspect in (self.root/'campaigns', self.paths[0], directory,
                        directory/'queue.json', self.root/'execution',
                        self.paths[0]/'jobs/0001/config.json'):
            with self.subTest(suspect=suspect):
                original = Path.is_symlink
                with patch.object(Path, 'is_symlink', lambda path: path == suspect or original(path)):
                    if suspect.name == 'config.json':
                        self.assertEqual(campaign_batch.run_batch(directory, runner=self.runner)['status'], 'paused')
                    else:
                        with self.assertRaises(ValueError):
                            campaign_batch.run_batch(directory, runner=self.runner)
        self.assertEqual(self.calls, [])

    def test_underlying_campaign_mutex_pauses_without_starting_next(self):
        directory = self.make_batch()
        with campaign._job_lock(self.paths[0]):
            result = campaign_batch.run_batch(directory, runner=self.runner)
        self.assertEqual(result['status'], 'paused')
        self.assertEqual(self.calls, self.paths[:1])
        self.assertEqual(self.video_calls, [])
        self.assertEqual(result['campaigns'][1]['status'], 'pending')

    def test_invalid_status_types_have_clear_validation_error(self):
        directory = self.make_batch()
        original = campaign_batch.read_batch(directory)
        for scope in ('queue', 'entry'):
            queue = json.loads(json.dumps(original))
            target = queue if scope == 'queue' else queue['campaigns'][0]
            target['status'] = ['running']
            atomic_json(directory/'queue.json', queue)
            with self.subTest(scope=scope), self.assertRaises(ValueError):
                campaign_batch.read_batch(directory)

    def test_main_keep_stop_and_exit_codes(self):
        directory = self.make_batch()
        with patch.object(campaign_batch, 'protect_worker'), patch.object(campaign_batch, 'run_batch', return_value=dict(status='completed')) as run:
            self.assertEqual(campaign_batch.main(['--directory', str(directory), '--keep-stop']), 0)
            run.assert_called_once_with(str(directory), clear_stop=False)
        with patch.object(campaign_batch, 'protect_worker'), patch.object(campaign_batch, 'run_batch', return_value=dict(status='paused')):
            self.assertEqual(campaign_batch.main(['--directory', str(directory)]), 2)


if __name__ == '__main__':
    unittest.main()
