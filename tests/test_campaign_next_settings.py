"""Future settings reach the worker; no real download, AI or speech calls."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from tests import test_campaign as fixtures
from tests import test_campaign_ui as ui_fixtures
from teodub import campaign
from teodub.campaign_settings import save_next_settings, next_settings, prepare_next_job, effective_job
from teodub.campaign_preview import next_video_config
from teodub.common import atomic_json, hash_file


class FutureSettingsTests(unittest.TestCase):
    setUp = fixtures.CampaignTests.setUp
    runner = fixtures.CampaignTests.runner

    def changed(self, **overrides):
        return dict(self.settings, voice='Thùy Dung', voice_speed=1.15, tts_model='v3turbo', **overrides)

    def test_next_config_is_real_worker_config_and_original_is_preserved(self):
        original = {p: p.read_bytes() for p in (self.directory/'jobs').glob('*/config.json')}
        from teodub.campaign_batch import _identity, _completed_unchanged
        before = _identity(campaign.read_json(self.directory/'campaign.json'), self.directory)
        record = save_next_settings(self.root, self.directory, self.changed())
        manifest = campaign.read_json(self.directory/'campaign.json')
        preview = next_video_config(self.directory, manifest)
        self.assertEqual(preview['config']['voice'], 'Thùy Dung')
        configs = []
        def run(path, stop):
            configs.append(campaign.read_json(path))
            return self.runner(path, stop)
        result = campaign.run_campaign(self.directory, runner=run)
        self.assertEqual(result['status'], 'completed', result.get('message'))
        self.assertEqual(len(configs), 2)
        for config in configs:
            self.assertEqual(config['voice'], 'Thùy Dung')
            self.assertEqual(config['voice_speed'], 1.15)
            self.assertEqual(config['tts_model'], 'v3turbo')
            self.assertEqual(config['campaign_settings_version'], record['id'])
        self.assertEqual(configs[0], preview['config'])
        self.assertEqual({p: p.read_bytes() for p in original}, original)
        self.assertEqual(_identity(result, self.directory), before)
        _completed_unchanged(self.directory, result)
        campaign.run_campaign(self.directory, runner=run)
        self.assertEqual(len(configs), 2)

    def test_request_during_current_video_applies_only_to_following_video(self):
        seen = []
        def run(path, stop):
            config = campaign.read_json(path)
            seen.append(config['voice'])
            if len(seen) == 1:
                frozen = path.read_bytes()
                save_next_settings(self.root, self.directory, self.changed())
                current = next_video_config(self.directory, campaign.read_json(self.directory/'campaign.json'), active=True)
                self.assertEqual(current['config']['voice'], 'Mai Anh')
                self.assertEqual(current['future_config']['voice'], 'Thùy Dung')
                self.assertEqual(path.read_bytes(), frozen)
            return self.runner(path, stop)
        result = campaign.run_campaign(self.directory, runner=run)
        self.assertEqual(result['status'], 'completed', result.get('message'))
        self.assertEqual(seen, ['Mai Anh', 'Thùy Dung'])
        self.assertNotIn('execution_job', result['items'][0])
        self.assertIn('execution_job', result['items'][1])

    def test_completed_output_is_preserved_when_next_settings_change(self):
        def stop_after_one(path, stop):
            result = self.runner(path, stop)
            stop.touch()
            return result
        first = campaign.run_campaign(self.directory, runner=stop_after_one)['items'][0]
        state = Path(first['job'])/'state.json'
        before = state.read_bytes()
        output = Path(first['outputs']['video'])
        digest = hash_file(output)
        save_next_settings(self.root, self.directory, self.changed())
        result = campaign.run_campaign(self.directory, runner=self.runner)
        self.assertEqual(result['status'], 'completed', result.get('message'))
        self.assertEqual(result['items'][0], first)
        self.assertEqual(state.read_bytes(), before)
        self.assertEqual(hash_file(output), digest)
        self.assertNotIn('execution_job', result['items'][0])

    def test_voice_change_reuses_verified_translation_but_never_old_voice_cache(self):
        item = campaign.read_json(self.directory/'campaign.json')['items'][0]
        job = Path(item['job'])
        source = self.root/'fixture.mp4'
        source.write_bytes(b'fixture source')
        captions = job/'fixture.srt'
        captions.write_text('1\n00:00:00,000 --> 00:00:01,000\nXin chao\n', encoding='utf-8')
        atomic_json(job/'state.json', dict(source=dict(path=str(source), sha256=hash_file(source)),
            captions=dict(path=str(captions), sha256=hash_file(captions), count=1),
            voice=dict(path='old voice'), rendered=dict(outputs={})))
        save_next_settings(self.root, self.directory, self.changed())
        revision = prepare_next_job(self.directory, item)
        config = campaign.read_json(revision/'config.json')
        self.assertEqual(config['subtitles'], str(captions))
        self.assertEqual(config['subtitles_sha256'], hash_file(captions))
        self.assertEqual(config['dialogue_source_sha256'], hash_file(source))
        self.assertEqual(set(campaign.read_json(revision/'state.json')), {'source'})
        self.assertEqual(prepare_next_job(self.directory, item), revision)
        save_next_settings(self.root, self.directory, self.changed(translation_model='gpt-5.6-terra'))
        newer = prepare_next_job(self.directory, item)
        self.assertNotEqual(revision, newer)
        self.assertIsNone(campaign.read_json(newer/'config.json')['subtitles'])

    def test_latest_request_wins_and_survives_resume(self):
        save_next_settings(self.root, self.directory, self.changed())
        latest = save_next_settings(self.root, self.directory, dict(self.settings, voice='Mai Anh', voice_speed=1.2))
        item = campaign.read_json(self.directory/'campaign.json')['items'][0]
        target = prepare_next_job(self.directory, item)
        self.assertEqual(target.name, latest['id'])
        self.assertEqual(campaign.read_json(target/'config.json')['voice_speed'], 1.2)
        self.assertEqual(prepare_next_job(self.directory, item), target)
        self.assertEqual(len(list((Path(item['job'])/'revisions').iterdir())), 1)

    def test_failed_skip_and_retry_preview_match_worker(self):
        manifest = campaign.read_json(self.directory/'campaign.json')
        manifest['items'][0]['status'] = 'failed'
        atomic_json(self.directory/'campaign.json', manifest)
        save_next_settings(self.root, self.directory, self.changed())
        self.assertEqual(next_video_config(self.directory, manifest)['number'], 2)
        self.assertEqual(next_video_config(self.directory, manifest, retry_failed=True)['number'], 1)
        result = campaign.run_campaign(self.directory, runner=self.runner)
        self.assertNotIn('execution_job', result['items'][0])
        campaign.run_campaign(self.directory, retry_failed=True, runner=self.runner)
        self.assertEqual(self.calls, [effective_job(self.directory, result['items'][1]).name,
                                     next_settings(self.directory)['id']])

    def test_invalid_reference_does_not_replace_saved_selection(self):
        saved = save_next_settings(self.root, self.directory, self.changed())
        with self.assertRaises((ValueError, OSError)):
            save_next_settings(self.root, self.directory, dict(self.changed(), voice='Giọng từ mẫu',
                reference_audio=str(self.root/'missing.wav'), reference_sha256='a'*64))
        self.assertEqual(next_settings(self.directory)['id'], saved['id'])

    def test_tampered_selection_blocks_worker_without_fallback(self):
        record = save_next_settings(self.root, self.directory, self.changed())
        path = self.directory/'settings-updates'/(record['id']+'.json')
        path.write_text('{}', encoding='utf-8')
        result = campaign.run_campaign(self.directory, runner=self.runner)
        self.assertEqual(result['status'], 'paused')
        self.assertIn('thay đổi', result['message'])
        self.assertFalse(self.calls)

    def test_active_old_worker_is_not_silently_ignored(self):
        from teodub.pipeline import _job_lock
        with _job_lock(self.directory):
            with self.assertRaisesRegex(ValueError, 'bản cũ'):
                save_next_settings(self.root, self.directory, self.changed())
        self.assertFalse((self.directory/'next-settings.json').exists())

    def test_orphan_video_worker_blocks_config_revision(self):
        from teodub.pipeline import _job_lock
        item = campaign.read_json(self.directory/'campaign.json')['items'][0]
        save_next_settings(self.root, self.directory, self.changed())
        with _job_lock(Path(item['job'])):
            with self.assertRaisesRegex(ValueError, 'đang xử lý'):
                prepare_next_job(self.directory, item)
        self.assertFalse((Path(item['job'])/'revisions').exists())

    def test_page_sources_use_actual_revision_render(self):
        from teodub.page_schedule import campaign_video_source
        save_next_settings(self.root, self.directory, self.changed())
        result = campaign.run_campaign(self.directory, runner=self.runner)
        source = campaign_video_source(self.root, self.directory, 1)
        self.assertEqual(source['path'], result['items'][0]['outputs']['video'])
        self.assertEqual(source['sha256'], hash_file(source['path']))


class FutureSettingsUITests(unittest.TestCase):
    setUp = ui_fixtures.CampaignUITests.setUp
    cleanup = ui_fixtures.CampaignUITests.cleanup

    def test_button_changes_actual_next_voice_not_just_creation_preview(self):
        self.studio.fields['voice'].set('Thùy Dung')
        self.tab.create()
        before = (self.tab.directory/'jobs/0001/config.json').read_bytes()
        self.studio.fields['voice'].set('Cô Gái Hoạt Ngôn (theo mẫu)')
        expected = self.studio.voice_selection()
        with patch('teodub.campaign_ui.subprocess.Popen') as launch:
            self.tab.apply_settings_button.invoke()
            launch.assert_not_called()
        self.assertIn('Cô Gái Hoạt Ngôn', self.tab.summary.get())
        preview = next_video_config(self.tab.directory, campaign.read_json(self.tab.directory/'campaign.json'))
        for key, value in expected.items():
            self.assertEqual(preview['config'][key], value, key)
        item = campaign.read_json(self.tab.directory/'campaign.json')['items'][0]
        job = prepare_next_job(self.tab.directory, item)
        actual = campaign.read_json(job/'config.json')
        for key, value in expected.items():
            self.assertEqual(actual[key], value, key)
        self.assertEqual((self.tab.directory/'jobs/0001/config.json').read_bytes(), before)
        self.tab.display(campaign.read_json(self.tab.directory/'campaign.json'))
        self.assertIn('Đã lưu cấu hình', self.tab.notice.get())

    def test_active_ui_main_line_shows_future_config_and_separate_current_voice(self):
        self.studio.fields['voice'].set('Thùy Dung')
        self.tab.create()
        manifest = campaign.read_json(self.tab.directory/'campaign.json')
        manifest['items'][0]['status'] = 'running'
        self.studio.fields['voice'].set('Cô Gái Hoạt Ngôn (theo mẫu)')
        self.tab.apply_single_settings()
        self.tab.update_execution_summary(manifest, active=True)
        self.assertIn('Cô Gái Hoạt Ngôn', self.tab.summary.get())
        self.assertIn('Thùy Dung', self.tab.retry_summary.get())

    def test_applied_female_reference_reaches_same_speech_engine_as_single(self):
        import numpy as np
        from unittest.mock import Mock
        from teodub import tts
        self.studio.fields['voice'].set('Thùy Dung')
        self.tab.create()
        self.studio.fields['voice'].set('Cô Gái Hoạt Ngôn (theo mẫu)')
        single = self.studio.voice_selection()
        self.tab.apply_single_settings()
        item = campaign.read_json(self.tab.directory/'campaign.json')['items'][0]
        revision = prepare_next_job(self.tab.directory, item)
        actual = campaign.read_json(revision/'config.json')
        engine = Mock()
        engine.infer.return_value = (.1*np.sin(2*np.pi*220*np.arange(48000)/48000)).astype('float32')
        cues = [dict(start_ms=0, end_ms=2000, text='Cùng nghe giọng đọc này nhé.')]
        with patch.object(tts, 'get_model', return_value=engine):
            for label, config in [('single', single), ('revision', actual)]:
                tts.synthesize(cues, config['voice'], Path(self.temp.name)/label, **tts.selection_options(config))
        self.assertEqual(engine.infer.call_count, 2)
        calls = engine.infer.call_args_list
        self.assertNotIn('voice', calls[1].kwargs)
        self.assertTrue(calls[1].kwargs['use_ref_codes'])
        self.assertEqual(hash_file(calls[0].kwargs['ref_audio']), hash_file(calls[1].kwargs['ref_audio']))

    def test_editing_form_alone_does_not_change_saved_next_settings(self):
        self.tab.create()
        self.studio.fields['voice'].set('Thùy Dung')
        self.tab.capture_settings()
        self.assertIsNone(next_settings(self.tab.directory))
        self.assertNotIn('Thùy Dung', self.tab.summary.get())

    def test_creation_options_are_hidden_until_requested(self):
        self.assertFalse(self.tab.creation_settings_frame.winfo_manager())
        self.tab.toggle_creation_settings()
        self.assertEqual(self.tab.creation_settings_frame.winfo_manager(), 'pack')
        self.tab.toggle_creation_settings()
        self.assertFalse(self.tab.creation_settings_frame.winfo_manager())
