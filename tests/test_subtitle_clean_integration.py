"""Reuse fixture lifecycles only, with every paid/AI engine replaced by fakes."""
from unittest.mock import patch
from tests.test_audio_mix_integration import AudioMixPipelineTests, AudioMixStudioTests
from tests.test_subtitle_clean import SETTINGS
from teodub import pipeline
from teodub.subtitle_clean_ui import SubtitleCleanDialog


class CleanPipelineTests(AudioMixPipelineTests):
    def test_wrong_video_region_stops_before_translation_tts_or_gpu(self):
        self.config['subtitle_clean'] = dict(SETTINGS, source_sha256='0'*64, source_path=str(self.source))
        with patch('teodub.subtitle_clean.preflight') as gpu:
            result = pipeline.run(self.config)
        self.assertNotEqual(result['status'], 'completed')
        self.translate.assert_not_called()
        self.tts.assert_not_called()
        gpu.assert_not_called()
        self.render.assert_not_called()

    def test_clean_gpu_preflight_failure_does_not_spend_translation_or_tts(self):
        self.config['subtitle_clean'] = SETTINGS
        with patch('teodub.subtitle_clean.preflight', side_effect=ValueError('GPU not ready')):
            result = pipeline.run(self.config)
        self.assertNotEqual(result['status'], 'completed')
        self.translate.assert_not_called()
        self.tts.assert_not_called()
        self.render.assert_not_called()

    def test_selected_clean_output_reaches_renderer_original_source_kept(self):
        self.config['subtitle_clean'] = SETTINGS
        cleaned = {'path': 'fake-clean', 'sha256': 'fixture'}
        with patch('teodub.subtitle_clean.preflight') as preflight, \
                patch('teodub.subtitle_clean.clean_video', return_value=cleaned) as clean:
            result = pipeline.run(self.config)
        self.assertEqual(result['status'], 'completed')
        preflight.assert_called_once()
        self.assertEqual(clean.call_args.args[0], str(self.source.resolve()))
        self.assertEqual(self.render.call_args.kwargs['cleaned_picture'], cleaned)
        self.assertEqual(self.render.call_args.args[0], str(self.source.resolve()))


class CleanStudioTests(AudioMixStudioTests):
    def test_clean_snapshot_preferences_and_campaign(self):
        from teodub.subtitle_clean_profiles import save_profile
        source = self.path/'source.mp4'
        source.write_bytes(b'video fixture')
        self.studio.input.delete('1.0', 'end')
        self.studio.input.insert('1.0', str(source))
        save_profile(self.studio.data_dir, source, SETTINGS)
        self.studio.sync_clean_source(force=True)
        self.studio.clean_enabled.set(True)
        self.studio.toggle_clean()
        expected = self.studio.clean_options.copy()
        config = self.studio.config_from_form('full')
        self.assertEqual(config['subtitle_clean'], expected)
        self.assertNotIn('subtitle_clean', self.studio.config_from_form('preview'))
        self.studio.save_preferences()
        restored = self.new_studio(self.studio.data_dir)
        restored.sync_clean_source(force=True)
        self.assertEqual(restored.clean_options, expected)
        tab = self.campaign_tab()
        tab.capture_settings()
        self.assertEqual(tab.settings['subtitle_clean'], {'enabled': False})
        self.studio.clean_options = {'enabled': False}
        self.assertEqual(config['subtitle_clean'], expected)
        self.assertEqual(tab.settings['subtitle_clean'], {'enabled': False})

    def test_dialog_and_busy_controls(self):
        dialog = SubtitleCleanDialog(self.studio)
        dialog.enabled.set(True)
        dialog.region = SETTINGS['region']
        self.assertEqual(dialog.settings(), SETTINGS)
        dialog.close()
        self.studio.campaign_busy = True
        self.studio.refresh_buttons()
        self.assertEqual(str(self.studio.clean_button['state']), 'disabled')
