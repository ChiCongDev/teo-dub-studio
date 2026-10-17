import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from teodub import campaign
from teodub.channel_discovery import ChannelPosts, channel_url
from teodub.common import atomic_json, hash_file
from teodub.wording import validate_wording


class CampaignTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.settings = dict(voice='Mai Anh', voice_provider='local', reading_style='legacy',
                             speed=.9, original_gain=.1, translation_model='gpt-5.6-luna',
                             translation_effort='low', burn_subtitles=True, subtitle_color='yellow',
                             subtitle_weight='bold')
        self.records = campaign.parse_links('https://www.douyin.com/video/111\nhttps://www.douyin.com/video/222')
        self.directory = campaign.create_campaign(self.root, 'Đèn thử', self.records,
            self.settings, str(self.root/'goc'), str(self.root/'xuat'))
        self.calls = []

    def runner(self, path, stop):
        self.calls.append(path.parent.name)
        config = campaign.read_json(path)
        result_file = Path(config['output_dir'])/(config['campaign_basename']+'.mp4')
        result_file.parent.mkdir(parents=True, exist_ok=True)
        result_file.write_bytes(b'fixture verified output '+path.parent.name.encode())
        outputs = dict(video=str(result_file))
        atomic_json(path.parent/'state.json', dict(rendered=dict(outputs=outputs, hashes=dict(video=hash_file(result_file)))))
        return dict(status='completed', message='Done', outputs=outputs)

    def test_dedup_share_text_and_invalid_host(self):
        rows = campaign.parse_links('Chia sẻ https://v.douyin.com/ABC/\nhttps://v.douyin.com/ABC/\n[video](https://www.douyin.com/video/123)')
        self.assertEqual(len(rows), 2)
        with self.assertRaises(ValueError):
            campaign.parse_links('https://example.com/test')

    def test_freeze_settings_sequence_and_separate_folders(self):
        first = campaign.read_json(self.directory/'jobs/0001/config.json')
        second = campaign.read_json(self.directory/'jobs/0002/config.json')
        self.assertTrue(first['campaign_basename'].startswith('Video 1_'))
        self.assertTrue(second['campaign_basename'].startswith('Video 2_'))
        self.assertEqual(first['subtitle_color'], 'yellow')
        self.assertIsNone(first['subtitles'])
        self.assertNotIn('execution_lock', first)
        self.assertNotEqual(first['original_dir'], first['output_dir'])
        self.settings['subtitle_color'] = 'black'
        self.assertEqual(campaign.read_json(self.directory/'jobs/0001/config.json')['subtitle_color'], 'yellow')

    def test_wording_rules_are_frozen_and_bound_to_campaign_queue_identity(self):
        from teodub.campaign_batch import _identity
        settings = dict(self.settings, wording=dict(version='vi-wording-v1', enabled=True,
                                                    rules=[dict(source='bán', replacement='báng')]))
        directory = campaign.create_campaign(self.root, 'Từ ngữ', self.records, settings,
                                             str(self.root/'goc'), str(self.root/'xuat'))
        manifest = campaign.read_json(directory/'campaign.json')
        frozen = validate_wording(settings)
        identity = _identity(manifest, directory)
        settings['wording']['rules'][0]['replacement'] = 'bán hàng'
        self.assertEqual(manifest['settings']['wording'], frozen)
        for number in ('0001', '0002'):
            config = directory/f'jobs/{number}/config.json'
            self.assertEqual(campaign.read_json(config)['wording'], frozen)
            item = manifest['items'][int(number)-1]
            self.assertEqual(hash_file(config), item['config_sha256'])
        manifest['settings']['wording']['rules'][0]['replacement'] = 'khác'
        self.assertNotEqual(_identity(manifest, directory), identity)

    def test_invalid_wording_is_rejected_before_creating_campaign_files(self):
        before = set((self.root/'campaigns').iterdir())
        settings = dict(self.settings, wording=dict(version='vi-wording-v1', enabled=True,
                                                    rules=[dict(source='', replacement='báng')]))
        with self.assertRaises(ValueError):
            campaign.create_campaign(self.root, 'Sai quy tắc', self.records, settings,
                                     str(self.root/'goc'), str(self.root/'xuat'))
        self.assertEqual(set((self.root/'campaigns').iterdir()), before)

    def test_campaign_duplicate_creation_distinguishes_wording_rules(self):
        settings = dict(self.settings, wording=dict(version='vi-wording-v1', enabled=False,
                                                    rules=[dict(source='bán', replacement='báng')]))
        def create():
            return campaign.create_campaign(self.root, 'Cấu hình từ', self.records, settings,
                                             str(self.root/'goc'), str(self.root/'xuat'), prevent_duplicates=True)
        one = create()
        self.assertEqual(create(), one)
        settings['wording']['enabled'] = True
        two = create()
        self.assertNotEqual(one, two)
        settings['wording']['rules'][0]['replacement'] = 'bán hàng'
        self.assertNotEqual(create(), two)

    def test_complete_and_resume_never_reruns_completed(self):
        result = campaign.run_campaign(self.directory, runner=self.runner)
        self.assertEqual(self.calls, ['0001', '0002'])
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(campaign.totals(result)['completed'], 2)
        campaign.run_campaign(self.directory, runner=self.runner)
        self.assertEqual(self.calls, ['0001', '0002'])

    def test_completion_time_and_active_duration_survive_resume_without_counting_pause(self):
        from datetime import datetime
        clock = [0]
        def cancelled(path, stop):
            clock[0] += 2
            return dict(status='cancelled', message='Đã dừng')
        with patch('teodub.campaign.time.monotonic', side_effect=lambda: clock[0]):
            paused = campaign.run_campaign(self.directory, runner=cancelled)
        first = paused['items'][0]
        self.assertEqual(first['processing_seconds'], 2)
        self.assertNotIn('completed_at', first)
        self.assertIn('last_attempt_finished_at', first)
        clock[0] = 100
        def done(path, stop):
            clock[0] += 3
            return self.runner(path, stop)
        with patch('teodub.campaign.time.monotonic', side_effect=lambda: clock[0]):
            complete = campaign.run_campaign(self.directory, runner=done)
        self.assertEqual(complete['items'][0]['processing_seconds'], 5)
        self.assertEqual(complete['items'][1]['processing_seconds'], 3)
        stamp = complete['items'][0]['completed_at']
        self.assertIsNotNone(datetime.fromisoformat(stamp).tzinfo)
        again = campaign.run_campaign(self.directory, runner=self.runner)
        self.assertEqual(again['items'][0]['completed_at'], stamp)
        self.assertEqual(again['items'][0]['processing_seconds'], 5)

    def test_stop_reason_is_saved_even_between_videos(self):
        def stop_after(path, stop):
            result = self.runner(path, stop)
            stop.touch()
            return result
        result = campaign.run_campaign(self.directory, runner=stop_after)
        self.assertEqual(result['status'], 'paused')
        self.assertIn('yêu cầu Dừng', result['message'])
        self.assertNotIn('completed_at', result['items'][1])

    def test_empty_failure_gets_reason_and_no_completion_time(self):
        result = campaign.run_campaign(self.directory, runner=lambda *_: dict(status='failed', message=''))
        self.assertEqual(result['status'], 'needs_attention')
        self.assertIn('chưa có nguyên nhân', result['items'][0]['message'])
        self.assertNotIn('completed_at', result['items'][0])
        self.assertIn('video lỗi', result['message'])

    def test_child_exit_without_terminal_result_reports_actual_exit_code(self):
        job = self.directory/'jobs/0001'
        child = Mock()
        child.poll.return_value = 7
        with patch('teodub.campaign.subprocess.Popen', return_value=child):
            result = campaign.run_child(job/'config.json', self.directory/'stop.request')
        self.assertEqual(result['status'], 'failed')
        self.assertIn('mã thoát: 7', result['message'])
        self.assertIn('Chưa xác định nguyên nhân', result['message'])
        child.terminate.assert_not_called()

    def test_failed_link_continues_then_explicit_retry_only_failed(self):
        def failing(path, stop):
            if path.parent.name == '0001':
                self.calls.append('failed-0001')
                return dict(status='failed', message='Video đã bị xóa')
            return self.runner(path, stop)
        result = campaign.run_campaign(self.directory, runner=failing)
        self.assertEqual(result['status'], 'needs_attention')
        self.assertEqual(self.calls, ['failed-0001', '0002'])
        campaign.run_campaign(self.directory, runner=self.runner)
        self.assertEqual(len(self.calls), 2)
        result = campaign.run_campaign(self.directory, retry_failed=True, runner=self.runner)
        self.assertEqual(self.calls, ['failed-0001', '0002', '0001'])
        self.assertEqual(result['status'], 'completed')

    def test_stop_after_first_and_resume_remaining(self):
        def stop_after(path, stop):
            result = self.runner(path, stop)
            stop.touch()
            return result
        result = campaign.run_campaign(self.directory, runner=stop_after)
        self.assertEqual(result['status'], 'paused')
        self.assertEqual(self.calls, ['0001'])
        result = campaign.run_campaign(self.directory, runner=self.runner)
        self.assertEqual(self.calls, ['0001', '0002'])
        self.assertEqual(result['status'], 'completed')

    def test_quota_pauses_before_other_videos(self):
        calls = []
        def quota(path, stop):
            calls.append(path)
            return dict(status='failed', message='Đã hết hạn mức Codex')
        result = campaign.run_campaign(self.directory, runner=quota)
        self.assertEqual(len(calls), 1)
        self.assertEqual(result['status'], 'paused')
        self.assertEqual(result['message'], 'Đã hết hạn mức Codex')
        self.assertEqual(result['items'][1]['status'], 'pending')

    def test_cancelled_and_crashed_running_items_are_resumable(self):
        manifest = campaign.read_json(self.directory/'campaign.json')
        manifest['items'][0]['status'] = 'running'
        manifest['items'][1]['status'] = 'cancelled'
        atomic_json(self.directory/'campaign.json', manifest)
        result = campaign.run_campaign(self.directory, runner=self.runner)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(self.calls, ['0001', '0002'])

    def test_changed_config_and_changed_output_stop_without_overwrite(self):
        path = self.directory/'jobs/0001/config.json'
        original = path.read_bytes()
        path.write_bytes(original+b' ')
        result = campaign.run_campaign(self.directory, runner=self.runner)
        self.assertEqual(result['status'], 'paused')
        self.assertEqual(self.calls, [])
        path.write_bytes(original)
        result = campaign.run_campaign(self.directory, runner=self.runner)
        output = Path(result['items'][0]['outputs']['video'])
        output.write_bytes(b'changed')
        self.calls.clear()
        result = campaign.run_campaign(self.directory, runner=self.runner)
        self.assertEqual(result['status'], 'paused')
        self.assertEqual(self.calls, [])
        self.assertEqual(output.read_bytes(), b'changed')

    def test_execution_lock_prevents_competing_campaigns(self):
        lane = self.root/'execution'
        lane.mkdir()
        with campaign._job_lock(lane):
            with self.assertRaises(ValueError):
                campaign.run_campaign(self.directory, runner=self.runner)
        self.assertEqual(self.calls, [])


class ChannelTests(unittest.TestCase):
    def test_channel_links_and_foreign_urls(self):
        self.assertEqual(channel_url('[kênh](https://v.douyin.com/ABC/)'), 'https://v.douyin.com/ABC/')
        self.assertEqual(channel_url('https://m.douyin.com/share/user/MS4wABC?x=1'), 'https://www.douyin.com/user/MS4wABC')
        self.assertEqual(channel_url('https://v.douyin.com/ABC/'), 'https://v.douyin.com/ABC/')
        for url in ('https://evil.com/user/a', 'https://www.douyin.com/video/123', 'https://www.douyin.com:8080/user/a'):
            with self.assertRaises(ValueError):
                channel_url(url)

    def test_filter_owner_images_duplicates_and_terminal_page(self):
        posts = ChannelPosts('owner', 10)
        url = 'https://www.douyin.com/aweme/v1/web/aweme/post/?sec_user_id=owner'
        video = dict(aweme_id='123', author=dict(sec_uid='owner'), video={'duration': 10}, desc='caption')
        wrong = dict(video, aweme_id='456', author=dict(sec_uid='other'))
        photo = dict(video, aweme_id='789', images=[{}])
        posts.consume(url, dict(status_code=0, aweme_list=[video, video, wrong, photo], has_more=0))
        self.assertEqual(len(posts.items), 1)
        self.assertTrue(posts.complete)
        self.assertEqual(posts.skipped, {'789'})

    def test_limit_is_not_reported_as_entire_channel(self):
        posts = ChannelPosts('owner', 1)
        url = 'https://www.douyin.com/aweme/v1/web/aweme/post/?sec_user_id=owner'
        video = dict(aweme_id='123', author=dict(sec_uid='owner'), video={'duration': 10})
        posts.consume(url, dict(aweme_list=[video], has_more=0))
        self.assertFalse(posts.complete)


if __name__ == '__main__':
    unittest.main()
