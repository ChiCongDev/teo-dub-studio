"""Voice-provider identity and immutable reference cache integration."""
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import numpy as np
import soundfile as sf

from teodub import tts
from teodub.common import Cancelled, hash_file


class VoiceDispatchTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='teodub-voice-dispatch-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.output = self.root/'speech'
        self.cues = [dict(start_ms=0, end_ms=1000, text='Xin chào bạn.')]
        self.audio = (.1*np.sin(2*np.pi*200*np.arange(48000*4)/48000)).astype('float32')
        self.engine = Mock()
        self.engine.infer.return_value = self.audio[:12000]
        self.factory = self.enterContext(patch.object(tts, 'get_model', return_value=self.engine))

    def reference(self):
        path = self.root/'sample.wav'
        sf.write(path, self.audio, 48000, subtype='PCM_16')
        return dict(reference_audio=str(path), reference_sha256=hash_file(path))

    def test_custom_reference_used_and_resume_does_not_reinfer(self):
        options = self.reference()
        first = tts.synthesize(self.cues, tts.REFERENCE_VOICE, self.output, **options)
        kwargs = self.engine.infer.call_args.kwargs
        self.assertNotIn('voice', kwargs)
        self.assertTrue(Path(kwargs['ref_audio']).is_file())
        self.assertTrue(kwargs['use_ref_codes'])
        self.assertIn(options['reference_sha256'], first[0]['model'])
        self.engine.infer.reset_mock()
        second = tts.synthesize(self.cues, tts.REFERENCE_VOICE, self.output, **options)
        self.assertEqual(second, first)
        self.engine.infer.assert_not_called()

    def test_modified_reference_is_rejected_before_loading_model(self):
        options = self.reference()
        sf.write(options['reference_audio'], self.audio[:160000], 48000, subtype='PCM_16')
        with self.assertRaisesRegex(ValueError, 'đã thay đổi'):
            tts.synthesize(self.cues, tts.REFERENCE_VOICE, self.output, **options)
        self.factory.assert_not_called()

    def test_changed_reference_cannot_reuse_existing_clips(self):
        options = self.reference()
        tts.synthesize(self.cues, tts.REFERENCE_VOICE, self.output, **options)
        before = hash_file(self.output/'00001.wav')
        other = self.root/'other.wav'
        sf.write(other, self.audio*.5, 48000, subtype='PCM_16')
        self.engine.infer.reset_mock()
        with self.assertRaises(ValueError):
            tts.synthesize(self.cues, tts.REFERENCE_VOICE, self.output,
                           reference_audio=str(other), reference_sha256=hash_file(other))
        self.engine.infer.assert_not_called()
        self.assertEqual(hash_file(self.output/'00001.wav'), before)

    def test_unknown_provider_and_stale_sample_on_preset_fail(self):
        with self.assertRaises(ValueError):
            tts.synthesize(self.cues, 'Mai Anh', self.output, provider='unexpected')
        with self.assertRaises(ValueError):
            tts.synthesize(self.cues, 'Mai Anh', self.output, **self.reference())
        self.factory.assert_not_called()

    def test_stop_while_model_loads_never_starts_inference(self):
        stop = threading.Event()
        def loaded(*args):
            stop.set()
            return self.engine
        self.factory.side_effect = loaded
        with self.assertRaises(Cancelled):
            tts.synthesize(self.cues, 'Mai Anh', self.output, cancel=stop)
        self.engine.infer.assert_not_called()

    def test_reference_changed_during_inference_is_not_published(self):
        options = self.reference()
        def infer(text, **kwargs):
            sf.write(kwargs['ref_audio'], self.audio*.5, 48000, subtype='PCM_16')
            return self.audio[:12000]
        self.engine.infer.side_effect = infer
        with self.assertRaises(ValueError):
            tts.synthesize(self.cues, tts.REFERENCE_VOICE, self.output, **options)
        self.assertFalse((self.output/'00001.wav').exists())
        self.assertFalse((self.output/'manifest.json').exists())

    def test_capcut_failure_never_uses_local_voice(self):
        with patch('teodub.capcut_tts.synthesize_to_file', side_effect=ValueError('Service unavailable')) as online:
            with self.assertRaisesRegex(ValueError, 'Service unavailable'):
                tts.synthesize(self.cues, 'Cô Gái Hoạt Ngôn', self.output, provider='capcut')
        online.assert_called_once()
        self.factory.assert_not_called()
        self.assertFalse((self.output/'manifest.json').exists())

    def test_capcut_completed_audio_has_separate_model_and_cache(self):
        def online(text, voice, target, **kwargs):
            sf.write(target, self.audio[:12000], 48000, subtype='PCM_16')
        with patch('teodub.capcut_tts.synthesize_to_file', side_effect=online) as generate:
            first = tts.synthesize(self.cues, 'Thanh Niên Tự Tin', self.output, provider='capcut')
            again = tts.synthesize(self.cues, 'Thanh Niên Tự Tin', self.output, provider='capcut')
        generate.assert_called_once()
        self.assertEqual(first, again)
        self.assertNotEqual(first[0]['model'], tts.MODEL_ID)
        self.factory.assert_not_called()

    def test_short_preview_does_not_require_video(self):
        path = tts.preview_voice('Mai Anh', self.output)
        self.assertTrue(Path(path).is_file())
        self.engine.infer.assert_called_once_with(tts.PREVIEW_TEXT, voice='Mai Anh')
        self.assertLess(len(tts.PREVIEW_TEXT), 65)

    def test_legacy_preset_request_remains_unchanged(self):
        tts.synthesize(self.cues, 'Mai Anh', self.output)
        request = json.loads((self.output/'request.json').read_text(encoding='utf-8'))
        self.assertEqual(request, dict(model=tts.MODEL_ID, voice='Mai Anh', cues=self.cues))
        self.assertEqual(tts.selection_options({}), dict(provider='local', reference_audio=None,
                                                    reference_sha256=None, reading_style='legacy',
                                                    tts_model='v3turbo', voice_speed=1.0))

    def test_voice_speed_is_pitch_preserving_and_bound_to_cache(self):
        fast = tts.synthesize(self.cues, 'Mai Anh', self.output, voice_speed=1.2)
        self.assertIn('/voice-speed-1.2/', fast[0]['model'])
        self.assertAlmostEqual(fast[0]['duration_s'], .25/1.2, delta=.04)
        self.engine.infer.reset_mock()
        self.assertEqual(tts.synthesize(self.cues, 'Mai Anh', self.output, voice_speed=1.2), fast)
        with self.assertRaises(ValueError):
            tts.synthesize(self.cues, 'Mai Anh', self.output, voice_speed=1.1)
        self.engine.infer.assert_not_called()

    def test_invalid_voice_speed_fails_before_model_load(self):
        for speed in (.75, 1.35, 1.12, True, '1.2'):
            with self.subTest(speed=speed), self.assertRaises(ValueError):
                tts.synthesize(self.cues, 'Mai Anh', self.output, voice_speed=speed)
        self.factory.assert_not_called()

    def test_style_is_bound_to_cache_and_never_passed_as_ignored_model_argument(self):
        first = tts.synthesize(self.cues, 'Mai Anh', self.output, reading_style='story')
        self.engine.infer.assert_called_once_with(self.cues[0]['text'], voice='Mai Anh')
        self.assertIn('/story/', first[0]['model'])
        self.engine.infer.reset_mock()
        self.assertEqual(tts.synthesize(self.cues, 'Mai Anh', self.output, reading_style='story'), first)
        with self.assertRaises(ValueError):
            tts.synthesize(self.cues, 'Mai Anh', self.output, reading_style='advertising')
        self.engine.infer.assert_not_called()

    def test_new_style_preview_uses_longer_common_comparison_text(self):
        tts.preview_voice('Mai Anh', self.output, reading_style='conversation')
        self.engine.infer.assert_called_once_with(tts.NATURAL_PREVIEW_TEXT, voice='Mai Anh')


if __name__ == '__main__':
    unittest.main()
