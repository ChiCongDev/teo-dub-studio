"""Single/campaign voice routing regressions; temporary stores, no AI work."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from unittest.mock import Mock

from tests.test_campaign_ui import CampaignUITests as _CampaignFixture
from teodub.app import REFERENCE_ALIASES, VOICE_SPEED_LABELS
from teodub.common import hash_file


class CampaignVoiceConsistencyTests(unittest.TestCase):
    setUp = _CampaignFixture.setUp
    cleanup = _CampaignFixture.cleanup

    def configure_female(self):
        self.studio.fields['voice'].set('Cô Gái Hoạt Ngôn (theo mẫu)')
        self.studio.fields['reading_style'].set('advertising')
        self.studio.fields['voice_speed'].set(next(k for k, v in VOICE_SPEED_LABELS.items() if v == 1.15))

    def test_creation_uses_current_single_voice_instead_of_old_capture(self):
        self.studio.fields['voice'].set('Thùy Dung')
        self.tab.capture_settings()
        self.configure_female()
        expected = self.studio.voice_selection()
        with patch('teodub.campaign_ui.subprocess.Popen') as worker:
            self.tab.create()
            worker.assert_not_called()
        self.assertIsNotNone(self.tab.directory)
        manifest = json.loads((self.tab.directory/'campaign.json').read_text(encoding='utf-8'))
        for key, value in expected.items():
            self.assertEqual(manifest['settings'][key], value, key)
        for item in manifest['items']:
            config = json.loads((Path(item['job'])/'config.json').read_text(encoding='utf-8'))
            for key, value in expected.items():
                self.assertEqual(config[key], value, key)
            self.assertEqual(item['config_sha256'], hash_file(Path(item['job'])/'config.json'))
        self.assertIn('Cô Gái Hoạt Ngôn', self.tab.saved_voice.get())
        self.assertIn('1,15', self.tab.saved_voice.get())

    def test_saved_campaign_voice_is_visible_and_unchanged_after_form_changes(self):
        self.configure_female()
        self.tab.capture_settings()
        self.tab.create()
        path = self.tab.directory/'campaign.json'
        before = path.read_bytes()
        jobs = {p: hash_file(p) for p in (self.tab.directory/'jobs').glob('*/config.json')}
        self.studio.fields['voice'].set('Adam')
        self.tab.capture_settings()
        self.tab.display(json.loads(before))
        self.assertIn('Cô Gái Hoạt Ngôn', self.tab.saved_voice.get())
        self.assertIn('Cô Gái Hoạt Ngôn', self.tab.summary.get())
        self.assertIn('Adam', self.tab.creation_summary.get())
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual({p: hash_file(p) for p in jobs}, jobs)

    def test_new_campaign_and_single_send_same_sample_to_tts_engine(self):
        import numpy as np
        from teodub import tts
        self.configure_female()
        single = self.studio.voice_selection()
        self.tab.create()
        campaign = json.loads((self.tab.directory/'jobs/0001/config.json').read_text(encoding='utf-8'))
        engine = Mock()
        engine.infer.return_value = (.1*np.sin(2*np.pi*220*np.arange(48000)/48000)).astype('float32')
        cues = [dict(start_ms=0, end_ms=2000, text='Cùng nghe giọng đọc này nhé.')]
        with patch.object(tts, 'get_model', return_value=engine):
            for name, config in [('single', single), ('campaign', campaign)]:
                tts.synthesize(cues, config['voice'], Path(self.temp.name)/name,
                               **tts.selection_options(config))
        self.assertEqual(engine.infer.call_count, 2)
        calls = engine.infer.call_args_list
        for call in calls:
            self.assertNotIn('voice', call.kwargs)
            self.assertTrue(call.kwargs['use_ref_codes'])
            self.assertTrue(call.kwargs['denoise'])
        self.assertEqual(hash_file(calls[0].kwargs['ref_audio']), hash_file(calls[1].kwargs['ref_audio']))
        for name in ('single', 'campaign'):
            report = json.loads((Path(self.temp.name)/name/'reference/reference.json').read_text(encoding='utf-8'))
            self.assertEqual(report['source_sha256'], single['reference_sha256'])

    def test_invalid_new_reference_cannot_reuse_old_valid_snapshot(self):
        self.studio.fields['voice'].set('Thùy Dung')
        self.tab.capture_settings()
        self.studio.fields['voice'].set('Giọng từ mẫu')
        self.studio.fields['reference_audio'].set(str(Path(self.temp.name)/'missing.wav'))
        self.tab.create()
        self.assertIsNone(self.tab.directory)
        self.assertIn('Chưa tạo', self.tab.status.get())
        self.assertFalse(list((self.tab.data_dir/'campaigns').glob('*/campaign.json')))

    def test_female_and_male_aliases_never_collapse_to_generic_display(self):
        from teodub.voice_profiles import describe_voice_settings
        for label, path in REFERENCE_ALIASES.items():
            config = dict(voice='Giọng từ mẫu', reference_audio=str(path), tts_model='v3turbo',
                          voice_provider='local', voice_speed=1.1, reading_style='conversation')
            text = describe_voice_settings(config)
            self.assertIn(label, text)
            self.assertIn('Trên máy', text)
            self.assertIn('Turbo', text)
        custom = dict(config, reference_audio=str(Path(self.temp.name)/'co-gai-hoat-ngon.wav'))
        self.assertNotIn('Cô Gái Hoạt Ngôn', describe_voice_settings(custom))
