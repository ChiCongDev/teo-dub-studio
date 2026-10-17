"""VieNeu Adam presets stay distinct from sample cloning; no network or real TTS."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from teodub import tts
from teodub.common import hash_file
from tests import test_ui
from tests.test_speech import tone as output_tone
from tests.test_voice_profiles import tone as reference_tone


PRESETS = (('v3turbo', 'Adam'), ('v3turbo', 'Adam bựa'), ('v3nano', 'Adam'))


class AdamPresetDispatchTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='teodub-adam-presets-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.cues = [dict(start_ms=0, end_ms=1000, text='Xin chào bạn.')]
        self.engine = Mock()
        self.engine.infer.return_value = output_tone(.25)
        self.load = self.enterContext(patch.object(tts, 'get_model', return_value=self.engine))

    def reference(self):
        path = self.root/'sample.wav'
        reference_tone(path)
        return dict(reference_audio=str(path), reference_sha256=hash_file(path))

    def test_supported_adam_presets_reach_exact_engine_voice_and_reuse_cache(self):
        for index, (model, voice) in enumerate(PRESETS):
            with self.subTest(model=model, voice=voice):
                self.engine.reset_mock()
                self.load.reset_mock()
                directory = self.root/str(index)
                clips = tts.synthesize(self.cues, voice, directory, tts_model=model)
                self.engine.infer.assert_called_once_with(self.cues[0]['text'], voice=voice)
                self.assertEqual(clips[0]['voice'], voice)
                self.assertNotIn('/reference-', clips[0]['model'])
                self.assertFalse((directory/'reference').exists())
                self.assertEqual(tts.synthesize(self.cues, voice, directory, tts_model=model), clips)
                self.engine.infer.assert_called_once()
                self.load.assert_called_once()

    def test_nano_rejects_turbo_only_adam_before_loading(self):
        with self.assertRaises(ValueError):
            tts.synthesize(self.cues, 'Adam bựa', self.root/'bad', tts_model='v3nano')
        self.load.assert_not_called()
        self.assertFalse((self.root/'bad').exists())

    def test_preset_does_not_silently_accept_reference_fields(self):
        reference = self.reference()
        for model, voice in PRESETS:
            with self.subTest(model=model, voice=voice), self.assertRaises(ValueError):
                tts.synthesize(self.cues, voice, self.root/'bad', tts_model=model, **reference)
        self.load.assert_not_called()

    def test_preset_cache_rejects_other_voice_model_and_reference_without_overwrite(self):
        directory = self.root/'cache'
        tts.synthesize(self.cues, 'Adam', directory)
        before = {name: (directory/name).read_bytes() for name in ('request.json', '00001.wav', '00001.json')}
        reference = self.reference()
        self.load.reset_mock()
        changes = (('Adam bựa', {}), ('Mai Anh', {}), ('Adam', {'tts_model': 'v3nano'}),
                   (tts.REFERENCE_VOICE, reference))
        for voice, options in changes:
            with self.subTest(voice=voice, options=options), self.assertRaises(ValueError):
                tts.synthesize(self.cues, voice, directory, **options)
        self.load.assert_not_called()
        for name, content in before.items():
            self.assertEqual((directory/name).read_bytes(), content)

    def test_reference_cache_cannot_be_reinterpreted_as_adam_preset(self):
        directory = self.root/'reference-cache'
        tts.synthesize(self.cues, tts.REFERENCE_VOICE, directory, **self.reference())
        before = hash_file(directory/'00001.wav')
        self.load.reset_mock()
        with self.assertRaises(ValueError):
            tts.synthesize(self.cues, 'Adam', directory)
        self.load.assert_not_called()
        self.assertEqual(hash_file(directory/'00001.wav'), before)


class AdamPresetUITests(unittest.TestCase):
    def setUp(self):
        self.f = test_ui.StudioTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.studio = self.f.studio
        self.studio.input.insert('1.0', 'example.mp4')
        # An old sample path must not leak into any preset configuration.
        self.studio.fields['reference_audio'].set(str(self.f.path/'unused-sample.wav'))

    def select(self, model, voice):
        self.studio.fields['tts_model'].set(model)
        self.studio.fields['voice'].set(voice)
        self.assertEqual(self.studio.fields['voice'].get(), voice)
        self.assertIn(voice, tuple(self.studio.voice_picker['values']))

    def assert_preset(self, config, model, voice):
        self.assertEqual(config['voice_provider'], 'local')
        self.assertEqual(config['voice'], voice)
        self.assertEqual(config['tts_model'], model)
        self.assertNotIn('reference_audio', config)
        self.assertNotIn('reference_sha256', config)

    def test_selection_full_and_preview_keep_presets_without_any_sample(self):
        for model, voice in PRESETS:
            with self.subTest(model=model, voice=voice):
                self.select(model, voice)
                self.assert_preset(self.studio.voice_selection(), model, voice)
                for mode in ('full', 'preview'):
                    self.assert_preset(self.studio.config_from_form(mode), model, voice)
                self.assertIn('VieNeu', self.studio.voice_help.get())
                self.assertFalse(self.studio.sample_row.winfo_manager())
        self.f.spawn.assert_not_called()

    def test_switch_to_nano_keeps_adam_but_excludes_adam_bua(self):
        self.select('v3turbo', 'Adam')
        self.studio.fields['tts_model'].set('v3nano')
        self.assert_preset(self.studio.voice_selection(), 'v3nano', 'Adam')
        self.select('v3turbo', 'Adam bựa')
        self.studio.fields['tts_model'].set('v3nano')
        self.assertNotIn('Adam bựa', tuple(self.studio.voice_picker['values']))
        self.assertNotEqual(self.studio.voice_selection()['voice'], 'Adam bựa')

    def test_preview_routes_exact_preset_and_never_matches_other_voice_or_engine(self):
        self.select('v3turbo', 'Adam')
        self.studio.fields['reading_style'].set('legacy')
        config = self.studio.config_from_form('preview')
        engine = Mock()
        engine.infer.return_value = output_tone(.25)
        with patch.object(tts, 'get_model', return_value=engine):
            audio = tts.preview_voice(config['voice'], self.f.path/'preview', **tts.selection_options(config))
        engine.infer.assert_called_once_with(tts.PREVIEW_TEXT, voice='Adam')
        self.studio.preview_path = Path(audio)
        self.studio.preview_config = dict(config)
        self.assertTrue(self.studio.matching_preview())
        for model, voice in (('v3turbo', 'Adam bựa'), ('v3turbo', 'Mai Anh'), ('v3nano', 'Adam')):
            self.select(model, voice)
            self.assertFalse(self.studio.matching_preview())
        from teodub.adam_voice import LABEL
        self.studio.fields['voice'].set(LABEL)
        self.assertFalse(self.studio.matching_preview())
        self.f.spawn.assert_not_called()

    def test_campaign_freezes_preset_in_settings_and_child_job_without_sample(self):
        from teodub.campaign import create_campaign
        from teodub.campaign_ui import CampaignTab
        tab = CampaignTab(self.f.root, self.studio)
        try:
            for model, voice in PRESETS:
                with self.subTest(model=model, voice=voice):
                    self.select(model, voice)
                    tab.capture_settings()
                    self.assertFalse(tab.settings_error)
                    self.assert_preset(tab.settings, model, voice)
                    self.studio.fields['voice'].set('Mai Anh')
                    self.assert_preset(tab.settings, model, voice)
                    directory = create_campaign(self.studio.data_dir, 'Adam fixture',
                        [dict(url='https://www.douyin.com/video/111', title='fixture')],
                        tab.settings, str(self.f.path/'originals'), str(self.f.path/'outputs'))
                    manifest = json.loads((directory/'campaign.json').read_text(encoding='utf-8'))
                    child = json.loads((directory/'jobs/0001/config.json').read_text(encoding='utf-8'))
                    self.assert_preset(manifest['settings'], model, voice)
                    self.assert_preset(child, model, voice)
        finally:
            tab.after_cancel(tab.timer)
            if tab.auto_timer is not None:
                tab.after_cancel(tab.auto_timer)
            tab.destroy()
        self.f.spawn.assert_not_called()


if __name__ == '__main__':
    unittest.main()
