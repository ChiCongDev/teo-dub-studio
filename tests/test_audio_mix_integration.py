"""Final-video audio mix regressions. Hidden Tk, temporary state, fake engines only.

Run against the edited Teo Dub Studio package; no download, model, playback,
browser, account, or real user-data operation is allowed by these fixtures.
"""
import copy
import io
from pathlib import Path
import sys
import tempfile
import tkinter as tk
import unittest
from unittest.mock import Mock, patch
import wave

from teodub import pipeline
from teodub.app import Studio
from teodub.campaign import read_json
from teodub.campaign_ui import CampaignTab
from teodub.common import atomic_json, hash_file
from teodub.srt import write_srt


DEFAULT_MIX = {'original_gain': .1, 'voice_gain': 1.0}


@unittest.skipUnless(sys.platform == 'win32', 'Windows Tk application')
class AudioMixStudioTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='teodub-audio-mix-ui-')
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.close_root)
        self.sound = self.enterContext(patch('teodub.app.winsound.PlaySound'))
        self.enterContext(patch('teodub.app.os.startfile'))
        self.enterContext(patch('teodub.app.worker_command', return_value=['fixture-worker']))
        self.worker = Mock()
        self.worker.poll.return_value = None
        self.spawn = self.enterContext(patch('teodub.app.subprocess.Popen', return_value=self.worker))
        self.studios = []
        self.tabs = []
        self.studio = self.new_studio(self.path/'data')
        self.studio.fields['original_dir'].set(str(self.path/'gốc'))
        self.studio.fields['output_dir'].set(str(self.path/'thành phẩm'))
        self.studio.input.insert('1.0', 'https://www.douyin.com/video/111')

    def close_root(self):
        for tab in self.tabs:
            for timer in (tab.timer, tab.auto_timer):
                if timer is not None:
                    # The tab owns these callbacks and its Tcl command registry.
                    tab.after_cancel(timer)
        for studio in self.studios:
            studio.process = None
            studio.closed = True
            studio.stop_playback(refresh=False)
            self.root.after_cancel(studio.timer)
        self.root.destroy()

    def new_studio(self, directory):
        studio = Studio(self.root, directory)
        self.studios.append(studio)
        return studio

    def set_percent(self, original, voice, studio=None):
        studio = studio or self.studio
        studio.fields['original_volume'].set(str(original))
        studio.fields['voice_volume'].set(str(voice))

    def assert_mix(self, config, original, voice):
        self.assertAlmostEqual(config['original_gain'], original)
        self.assertAlmostEqual(config['voice_gain'], voice)

    def finish_job(self, status='failed', **values):
        atomic_json(Path(self.studio.current['directory'])/'result.json',
                    dict(status=status, message='Fixture result', **values))
        self.studio.complete()

    def campaign_tab(self):
        tab = CampaignTab(self.root, self.studio)
        self.tabs.append(tab)
        tab.auto_fetch.set(False)
        tab.original_dir.set(str(self.path/'originals'))
        tab.output_dir.set(str(self.path/'outputs'))
        tab.source.insert('1.0', 'https://www.douyin.com/video/111\nhttps://www.douyin.com/video/222')
        return tab

    def test_percent_defaults_decimal_mute_and_upper_boundary(self):
        self.assertEqual(self.studio.fields['original_volume'].get(), '10')
        self.assertEqual(self.studio.fields['voice_volume'].get(), '100')
        self.assertEqual(self.studio.audio_mix_selection(), DEFAULT_MIX)
        for original, voice in ((0, 0), (200, 200), (12.5, 87.25), (0, 150)):
            with self.subTest(original=original, voice=voice):
                self.set_percent(original, voice)
                self.assert_mix(self.studio.audio_mix_selection(), original/100, voice/100)
        self.studio.fill_audio_mix(dict(original_gain=.175, voice_gain=1.425))
        self.assert_mix(self.studio.audio_mix_selection(), .175, 1.425)

    def test_full_job_freezes_both_gains_and_disables_mix_controls(self):
        self.set_percent(0, 137.5)
        self.studio.start('full')
        self.spawn.assert_called_once()
        frozen = copy.deepcopy(self.studio.current)
        self.assert_mix(frozen, 0, 1.375)
        self.assertEqual(read_json(Path(frozen['directory'])/'config.json'), frozen)
        controls = self.studio.audio_mix_controls
        for widget in (*controls.scales.values(), *controls.entries.values(), controls.reset_button):
            self.assertTrue(widget.instate(['disabled']))
        self.set_percent(100, 50)
        self.assertEqual(self.studio.current, frozen)
        self.assertEqual(read_json(Path(frozen['directory'])/'config.json'), frozen)

    def test_controls_follow_all_busy_states_and_reset_to_default_only_when_idle(self):
        controls = self.studio.audio_mix_controls
        widgets = (*controls.scales.values(), *controls.entries.values(), controls.reset_button)
        self.assertEqual(set(controls.scales), {'original_volume', 'voice_volume'})
        self.assertEqual(set(controls.entries), {'original_volume', 'voice_volume'})
        for reason in ('process', 'campaign_busy', 'login_pending', 'closing'):
            with self.subTest(reason=reason):
                self.set_percent(25, 80)
                setattr(self.studio, reason, self.worker if reason == 'process' else True)
                self.studio.refresh_buttons()
                for widget in widgets:
                    self.assertTrue(widget.instate(['disabled']))
                controls.reset_button.invoke()
                self.assert_mix(self.studio.audio_mix_selection(), .25, .8)
                setattr(self.studio, reason, None if reason == 'process' else False)
                self.studio.refresh_buttons()
                for widget in widgets:
                    self.assertFalse(widget.instate(['disabled']))
        controls.reset_button.invoke()
        self.assertEqual(self.studio.audio_mix_selection(), DEFAULT_MIX)

    def test_invalid_final_percent_rejects_without_worker_or_job(self):
        for field in ('original_volume', 'voice_volume'):
            for invalid in ('', '-1', '200.1', 'nan', 'inf', 'không phải số'):
                with self.subTest(field=field, invalid=invalid):
                    self.set_percent(10, 100)
                    self.studio.fields[field].set(invalid)
                    with self.assertRaises(ValueError):
                        self.studio.audio_mix_selection()
                    with self.assertRaises(ValueError):
                        self.studio.config_from_form('full')
                    self.studio.start('full')
                    self.spawn.assert_not_called()
                    self.assertIsNone(self.studio.current)
        self.assertEqual(list(self.studio.jobs_dir.glob('*/config.json')), [])

    def test_bad_final_percent_does_not_block_preview_download_or_captions_launch(self):
        for mode in ('preview', 'download', 'captions'):
            with self.subTest(mode=mode):
                self.set_percent('invalid', '-20')
                self.spawn.reset_mock()
                self.studio.start(mode)
                self.spawn.assert_called_once()
                self.assertEqual(self.studio.current['mode'], mode)
                if mode == 'preview':
                    self.assert_mix(self.studio.current, .1, 1)
                else:
                    self.assertNotIn('voice_gain', self.studio.current)
                self.finish_job()

    def test_mix_changes_do_not_invalidate_cached_voice_preview(self):
        self.studio.start('preview')
        audio = Path(self.studio.current['directory'])/'preview.wav'
        with wave.open(str(audio), 'wb') as stream:
            stream.setparams((1, 2, 8000, 0, 'NONE', 'not compressed'))
            stream.writeframes(b'\x01\x00'*800)
        self.finish_job('completed', outputs={'audio': str(audio)})
        before = audio.read_bytes()
        self.spawn.reset_mock()
        for original, voice in ((0, 0), (200, 150), ('invalid', 'nan')):
            self.set_percent(original, voice)
            self.studio.refresh_buttons()
            self.assertTrue(self.studio.matching_preview())
            self.assertFalse(self.studio.replay_button.instate(['disabled']))
        self.assertEqual(audio.read_bytes(), before)
        self.spawn.assert_not_called()

    def test_preferences_without_last_job_restore_canonical_gain_values(self):
        self.set_percent(18.75, 112.5)
        self.studio.save_preferences()
        saved = read_json(self.studio.data_dir/'preferences.json')
        self.assert_mix(saved, .1875, 1.125)
        # A canonical-only preference file must restore without UI-only strings.
        saved.pop('original_volume', None)
        saved.pop('voice_volume', None)
        atomic_json(self.studio.data_dir/'preferences.json', saved)
        other = self.new_studio(self.studio.data_dir)
        self.assert_mix(other.audio_mix_selection(), .1875, 1.125)
        self.assertIsNone(other.current)
        self.spawn.assert_not_called()

    def test_latest_preferences_survive_last_job_but_resume_uses_frozen_gains(self):
        self.set_percent(25, 125)
        self.studio.start('full')
        frozen = copy.deepcopy(self.studio.current)
        self.finish_job()
        self.set_percent(0, 55)
        self.studio.save_preferences()
        self.spawn.reset_mock()
        other = self.new_studio(self.studio.data_dir)
        self.assert_mix(other.audio_mix_selection(), 0, .55)
        self.assertEqual(other.current, frozen)
        self.spawn.assert_not_called()
        other.resume()
        self.spawn.assert_called_once()
        self.assert_mix(other.audio_mix_selection(), .25, 1.25)
        self.assertEqual(other.current, frozen)
        self.assertEqual(read_json(Path(frozen['directory'])/'config.json'), frozen)

    def test_legacy_job_resume_keeps_absent_gain_fields_and_uses_defaults_in_form(self):
        legacy = self.studio.config_from_form('full')
        legacy.pop('original_gain', None)
        legacy.pop('voice_gain', None)
        directory = Path(legacy['directory'])
        atomic_json(directory/'config.json', legacy)
        atomic_json(directory/'result.json', dict(status='failed', message='Legacy fixture'))
        atomic_json(self.studio.data_dir/'last-job.json', dict(directory=str(directory)))
        other = self.new_studio(self.studio.data_dir)
        self.assertEqual(other.audio_mix_selection(), DEFAULT_MIX)
        self.assertEqual(other.current, legacy)
        other.resume()
        self.spawn.assert_called_once()
        self.assertEqual(other.current, legacy)
        self.assertEqual(read_json(directory/'config.json'), legacy)

    def test_campaign_capture_freezes_mix_and_new_capture_affects_only_new_campaign(self):
        tab = self.campaign_tab()
        self.set_percent(7.5, 140)
        tab.capture_settings()
        self.assert_mix(tab.settings, .075, 1.4)
        self.set_percent(0, 60)
        tab.create()
        self.assertIsNotNone(tab.directory)
        first_directory = tab.directory
        first_manifest = read_json(first_directory/'campaign.json')
        self.assert_mix(first_manifest['settings'], .075, 1.4)
        first_files = {}
        for item in first_manifest['items']:
            path = Path(item['job'])/'config.json'
            self.assert_mix(read_json(path), .075, 1.4)
            self.assertEqual(hash_file(path), item['config_sha256'])
            first_files[path] = path.read_bytes()
        tab.capture_settings()
        tab.create()
        self.assertNotEqual(tab.directory, first_directory)
        second_manifest = read_json(tab.directory/'campaign.json')
        self.assert_mix(second_manifest['settings'], 0, .6)
        for item in second_manifest['items']:
            self.assert_mix(read_json(Path(item['job'])/'config.json'), 0, .6)
        for path, contents in first_files.items():
            self.assertEqual(path.read_bytes(), contents)
        self.assertEqual(read_json(first_directory/'campaign.json'), first_manifest)
        self.spawn.assert_not_called()

    def test_invalid_campaign_capture_blocks_creation_until_corrected(self):
        tab = self.campaign_tab()
        self.set_percent(10, 'nan')
        tab.capture_settings()
        self.assertTrue(tab.settings_error)
        tab.create()
        self.assertIsNone(tab.directory)
        self.assertEqual(list((self.studio.data_dir/'campaigns').glob('*/campaign.json')), [])
        self.spawn.assert_not_called()


class AudioMixPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='teodub-audio-mix-pipeline-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root/'source.mp4'
        self.source.write_bytes(b'synthetic media: decoding is mocked')
        self.job = self.root/'job'
        self.config = dict(directory=str(self.job), stop_file=str(self.job/'stop.request'),
                           input=str(self.source), original_dir=str(self.root/'originals'),
                           output_dir=str(self.root/'outputs'), voice='Mai Anh', mode='full',
                           speed=.9, subtitles=None, reading_style='legacy')
        self.cues = [dict(start_ms=0, end_ms=400, text='Xin chào mọi người.')]
        self.enterContext(patch('sys.stdout', new=io.StringIO()))
        self.probe = self.enterContext(patch.object(pipeline, '_probe_source', side_effect=self.fake_probe))
        self.asr = self.enterContext(patch('teodub.asr.transcribe', side_effect=self.fake_asr))
        self.translate = self.enterContext(patch('teodub.codex_translation.translate_chinese_srt', side_effect=self.fake_translate))
        self.tts = self.enterContext(patch('teodub.tts.synthesize', side_effect=self.fake_tts))
        self.render = self.enterContext(patch('teodub.render.render_video', side_effect=self.fake_render))
        self.download = self.enterContext(patch('teodub.download.download_video', side_effect=AssertionError('No network permitted')))
        self.preflight = self.enterContext(patch('teodub.capcut_tts.preflight'))
        self.enterContext(patch('teodub.codex_translation.write_review_notes'))

    def fake_probe(self, path):
        return dict(path=str(Path(path).resolve()), sha256=hash_file(path),
                    duration_s=2, width=96, height=64, has_audio=True)

    def fake_asr(self, source, target, **kwargs):
        write_srt(target, [dict(cue, text='你好') for cue in self.cues])
        return dict(path=str(target))

    def fake_translate(self, source, target, **kwargs):
        write_srt(target, self.cues)
        return str(target)

    def fake_tts(self, cues, voice, directory, **kwargs):
        path = Path(directory)/'001.wav'
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_bytes(b'unchanged raw voice-cache bytes')
        return [dict(id=1, path=str(path), sha256=hash_file(path), duration_s=.6, sample_rate=48000)]

    def fake_render(self, source, cues, clips, destination, **kwargs):
        destination = Path(destination)
        destination.mkdir(parents=True, exist_ok=False)
        outputs = dict(video=str(destination/'final.mp4'), subtitles=str(destination/'final.srt'),
                       plan=str(destination/'render-plan.json'), quality=str(destination/'quality.json'))
        Path(outputs['video']).write_bytes(b'validated rendered fixture')
        write_srt(outputs['subtitles'], cues)
        atomic_json(outputs['plan'], dict(source=str(source), source_sha256=hash_file(source),
                    cues=cues, clips=clips, requested_speed=kwargs['speed'],
                    original_gain=kwargs['original_gain'], voice_gain=kwargs['voice_gain']))
        atomic_json(outputs['quality'], dict(technical_pass=True, source_sha256=hash_file(source),
                    cue_count=len(cues), video_sha256=hash_file(outputs['video']),
                    subtitles_sha256=hash_file(outputs['subtitles']), render_plan_sha256=hash_file(outputs['plan'])))
        return outputs

    def new_config(self, name, **values):
        directory = self.root/name
        return dict(self.config, directory=str(directory), stop_file=str(directory/'stop.request'), **values)

    def assert_no_costly_work(self):
        for mocked in (self.probe, self.asr, self.translate, self.tts, self.render,
                       self.download, self.preflight):
            mocked.assert_not_called()

    def test_full_gains_reach_renderer_but_never_raw_tts(self):
        for index, (original, voice) in enumerate(((0, 0), (2, 2), (.175, 1.375))):
            with self.subTest(original=original, voice=voice):
                result = pipeline.run(self.new_config(str(index), original_gain=original, voice_gain=voice))
                self.assertEqual(result['status'], 'completed', result)
                self.assertEqual(self.render.call_args.kwargs['original_gain'], original)
                self.assertEqual(self.render.call_args.kwargs['voice_gain'], voice)
                for key in ('original_gain', 'voice_gain', 'original_volume', 'voice_volume'):
                    self.assertNotIn(key, self.tts.call_args.kwargs)

    def test_invalid_full_gains_fail_before_source_or_any_costly_engine(self):
        for field in ('original_gain', 'voice_gain'):
            for index, invalid in enumerate((-.01, 2.01, float('nan'), float('inf'), 'invalid', None)):
                with self.subTest(field=field, invalid=invalid):
                    result = pipeline.run(self.new_config(f'{field}-{index}', **{field: invalid}))
                    self.assertEqual(result['status'], 'failed', result)
                    self.assert_no_costly_work()

    def test_invalid_full_gains_fail_before_capcut_network_preflight(self):
        result = pipeline.run(self.new_config('capcut', voice_provider='capcut',
                    voice='Cô Gái Hoạt Ngôn', original_gain=-1, voice_gain=1))
        self.assertEqual(result['status'], 'failed', result)
        self.assert_no_costly_work()

    def test_legacy_missing_gains_use_defaults_without_mutating_bound_request(self):
        before = copy.deepcopy(self.config)
        result = pipeline.run(self.config)
        self.assertEqual(result['status'], 'completed', result)
        self.assertEqual(self.render.call_args.kwargs['original_gain'], .1)
        self.assertEqual(self.render.call_args.kwargs['voice_gain'], 1)
        self.assertEqual(self.config, before)
        self.assertEqual(read_json(self.job/'request.json'), before)
        self.assertNotIn('voice_gain', read_json(self.job/'request.json'))

    def test_completed_resume_reuses_output_and_raw_cache_but_changed_mix_is_rejected(self):
        chosen = dict(self.config, original_gain=.35, voice_gain=1.25)
        first = pipeline.run(chosen)
        self.assertEqual(first['status'], 'completed', first)
        raw = self.job/'voice'/'001.wav'
        raw_before = raw.read_bytes()
        request_before = (self.job/'request.json').read_bytes()
        for mocked in (self.asr, self.translate, self.tts, self.render):
            mocked.reset_mock()
        second = pipeline.run(chosen)
        self.assertEqual(second['status'], 'completed', second)
        self.assertEqual(second['outputs'], first['outputs'])
        for field, changed in (('original_gain', .45), ('voice_gain', .75)):
            with self.subTest(field=field):
                rejected = pipeline.run(dict(chosen, **{field: changed}))
                self.assertEqual(rejected['status'], 'failed', rejected)
        for mocked in (self.asr, self.translate, self.tts, self.render):
            mocked.assert_not_called()
        self.assertEqual(raw.read_bytes(), raw_before)
        self.assertEqual((self.job/'request.json').read_bytes(), request_before)

    def test_download_and_caption_modes_ignore_invalid_final_mix(self):
        for mode in ('download', 'captions'):
            with self.subTest(mode=mode):
                result = pipeline.run(self.new_config(mode, mode=mode, original_gain='invalid', voice_gain=-1))
                self.assertEqual(result['status'], 'completed', result)
                self.tts.assert_not_called()
                self.render.assert_not_called()
                self.preflight.assert_not_called()

    def test_preview_ignores_invalid_mix_and_does_not_adjust_preview_audio(self):
        sample = self.root/'raw-preview.wav'
        sample.write_bytes(b'raw preview fixture; validation mocked')
        with patch('teodub.tts.preview_voice', return_value=str(sample)) as preview, \
             patch('teodub.tts.validate_audio', return_value={'duration_s': 1}):
            result = pipeline.run(self.new_config('preview', mode='preview', original_gain='invalid', voice_gain=-1))
        self.assertEqual(result['status'], 'completed', result)
        preview.assert_called_once()
        for key in ('original_gain', 'voice_gain', 'original_volume', 'voice_volume'):
            self.assertNotIn(key, preview.call_args.kwargs)
        self.assertEqual(Path(result['outputs']['audio']).read_bytes(), sample.read_bytes())
        self.assert_no_costly_work()


if __name__ == '__main__':
    unittest.main()
