"""Independent export levels verified with small real local FFmpeg fixtures."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import wave

import numpy as np

from teodub import render
from teodub.audio_mix import validate_audio_mix, validate_percent
from teodub.common import hash_file
from tests.test_render import source_video, tone_clip


class AudioMixValidationTests(unittest.TestCase):
    def test_legacy_defaults_and_independent_boundaries(self):
        self.assertEqual(validate_audio_mix({}), dict(original_gain=.1, voice_gain=1.))
        for original, voice in ((0, 2), (2, 0), (.135, 1.25)):
            self.assertEqual(validate_audio_mix(dict(original_gain=original, voice_gain=voice)),
                             dict(original_gain=original, voice_gain=voice))

    def test_bad_numeric_gains_are_rejected_not_coerced(self):
        for key in ('original_gain', 'voice_gain'):
            for value in (True, False, None, '1', [], {}, -.01, 2.01,
                          float('nan'), float('inf'), -float('inf'), 10**500):
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    validate_audio_mix({key: value})

    def test_percent_accepts_numeric_and_vietnamese_decimal_input(self):
        for value, expected in ((0, 0.), (200, 2.), (' 12,5 ', .125), ('100', 1.), (.5, .005)):
            self.assertEqual(validate_percent(value, 'Âm lượng'), expected)
        for value in ('', 'một', '10%', '-1', '201', 'NaN', 'Inf', None, True, [], 10**500):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_percent(value, 'Âm lượng')


class AudioMixRenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.shared = tempfile.TemporaryDirectory(prefix='teodub-audio-levels-')
        cls.root = Path(cls.shared.name)
        cls.source, cls.silent = cls.root/'source.mp4', cls.root/'no-audio.mp4'
        source_video(cls.source)
        source_video(cls.silent, audio=False)
        cls.clip = tone_clip(cls.root/'voice.wav', 1)
        cls.cues = [dict(start_ms=100, end_ms=1000, text='Kiểm tra hai mức âm lượng.')]

    @classmethod
    def tearDownClass(cls):
        cls.shared.cleanup()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='case-', dir=self.root)
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.counter = 0

    def perform(self, *, source=None, clip=None, **options):
        self.counter += 1
        output = self.directory/str(self.counter)
        paths = render.render_video(source or self.source, self.cues, [clip or self.clip],
                                    output, speed=1., **options)
        plan = json.loads(Path(paths['plan']).read_text(encoding='utf-8'))
        quality = json.loads(Path(paths['quality']).read_text(encoding='utf-8'))
        self.assertTrue(quality['technical_pass'])
        self.assertEqual(quality['video_sha256'], hash_file(paths['video']))
        return paths, plan, quality

    def test_voice_levels_work_without_source_audio_including_quiet_gain(self):
        _, _, baseline = self.perform(source=self.silent, original_gain=0, voice_gain=1.)
        for gain in (.01, .2, 2.):
            with self.subTest(gain=gain):
                _, plan, quality = self.perform(source=self.silent, original_gain=2., voice_gain=gain)
                self.assertEqual(plan['original_gain'], 2.)
                self.assertEqual(plan['voice_gain'], gain)
                self.assertAlmostEqual(quality['audio_rms']/baseline['audio_rms'], gain, delta=max(.002, gain*.02))
                self.assertFalse(quality['voice_checks'][0]['intentionally_muted'])

    def test_source_levels_are_independent_when_voice_is_muted(self):
        _, _, baseline = self.perform(original_gain=1., voice_gain=0)
        for gain in (.01, .5, 2.):
            with self.subTest(gain=gain):
                _, plan, quality = self.perform(original_gain=gain, voice_gain=0)
                self.assertEqual(plan['voice_gain'], 0)
                self.assertAlmostEqual(quality['audio_rms']/baseline['audio_rms'], gain, delta=max(.002, gain*.02))
                self.assertTrue(quality['voice_checks'][0]['intentionally_muted'])

    def test_quiet_narration_with_loud_original_remains_a_valid_mix(self):
        _, plan, quality = self.perform(original_gain=2., voice_gain=.01)
        self.assertEqual(plan['voice_gain'], .01)
        self.assertEqual(quality['audio_mix'], dict(original_gain=2., voice_gain=.01))
        self.assertGreater(quality['mix_checks'][0]['correlation'], .99)

    def test_nonstationary_noise_and_multiple_cues_with_extended_picture(self):
        rng = np.random.default_rng(918)
        clips = []
        for index in range(1, 4):
            path = self.directory/f'varying-{index}.wav'
            rate, duration = 24000, .8
            t = np.arange(round(rate*duration))/rate
            envelope = np.maximum(0, np.sin(2*np.pi*3.1*t))**2
            signal = .22*envelope*(rng.normal(0, .5, len(t))+
                                   np.sin(2*np.pi*(170*index*t+60*t*t)))
            data = np.rint(np.clip(signal, -1, 1)*32767).astype('<i2')
            with wave.open(str(path), 'wb') as output:
                output.setparams((1, 2, rate, 0, 'NONE', 'not compressed'))
                output.writeframes(data.tobytes())
            clips.append(dict(id=index, path=str(path), sha256=hash_file(path),
                              sample_rate=rate, duration_s=duration))
        cues = [dict(start_ms=index*200, end_ms=index*200+300, text=f'Câu kiểm tra {index+1}.')
                for index in range(3)]
        for gain in (.01, .65, 2.):
            with self.subTest(gain=gain):
                paths = render.render_video(self.source, cues, clips, self.directory/f'noise-{gain}',
                                            speed=.9, original_gain=.8, voice_gain=gain)
                quality = json.loads(Path(paths['quality']).read_text(encoding='utf-8'))
                self.assertGreater(quality['duration_s'], 2.5)
                self.assertEqual(len(quality['mix_checks']), 3)
                self.assertTrue(all(check['correlation'] > .99 for check in quality['mix_checks']))

    def test_both_muted_or_missing_source_audio_export_real_silent_track(self):
        for source, original in ((self.source, 0.), (self.silent, 2.)):
            with self.subTest(source=source, original=original):
                paths, plan, quality = self.perform(source=source, original_gain=original, voice_gain=0)
                self.assertEqual(quality['audio_rms'], 0)
                self.assertGreater(quality['decoded_audio_samples'], 0)
                self.assertTrue(quality['mix_checks'][0]['intentionally_silent'])
                self.assertEqual(plan['voice_gain'], 0)
                self.assertTrue(Path(paths['video']).is_file())

    def test_gain_boost_is_limited_and_still_passes_mix_verification(self):
        loud = self.directory/'loud.wav'
        with wave.open(self.clip['path'], 'rb') as original:
            params = original.getparams()
            samples = np.frombuffer(original.readframes(original.getnframes()), dtype='<i2')
        boosted = np.clip(samples.astype(np.float64)*3, -32767, 32767).astype('<i2')
        with wave.open(str(loud), 'wb') as output:
            output.setparams(params)
            output.writeframes(boosted.tobytes())
        clip = dict(self.clip, path=str(loud), sha256=hash_file(loud))
        _, plan, quality = self.perform(clip=clip, original_gain=2., voice_gain=2.)
        self.assertEqual(plan['voice_gain'], 2.)
        self.assertGreater(quality['audio_peak'], .9)
        # Lossy AAC can overshoot a limiter's sample peak slightly when decoded.
        self.assertLess(quality['audio_peak'], 1.12)

    def test_malformed_gain_rejected_before_output_creation(self):
        for key in ('original_gain', 'voice_gain'):
            for value in (True, None, '1', float('nan'), -1, 2.1):
                output = self.directory/'bad'
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    render.render_video(self.source, self.cues, [self.clip], output, **{key: value})
                self.assertFalse(output.exists())

    def test_missing_or_silent_voice_file_is_not_excused_by_mute(self):
        for gain in (0., .01):
            output = self.directory/f'invalid-{gain}'
            clip = dict(self.clip, sha256='0'*64)
            with self.subTest(gain=gain), self.assertRaisesRegex(ValueError, 'thiếu|thay đổi'):
                render.render_video(self.source, self.cues, [clip], output, voice_gain=gain)
            self.assertEqual(list(output.iterdir()), [])

    def test_missing_narration_is_detected_at_low_voice_gain_before_export(self):
        def silence(path, placements, duration_frames, cancel):
            with wave.open(str(path), 'wb') as output:
                output.setparams((2, 2, render.RATE, 0, 'NONE', 'not compressed'))
                output.writeframes(bytes(duration_frames*4))
        with patch.object(render, '_write_narration', side_effect=silence):
            with self.assertRaisesRegex(ValueError, 'giọng câu'):
                self.perform(original_gain=2., voice_gain=.01)
        self.assertEqual(list((self.directory/'1').iterdir()), [])

    def test_final_filter_dropping_quiet_voice_cannot_pass_using_loud_original(self):
        real_run = render._run_ffmpeg
        def omit_voice(command, directory, duration, cancel, progress):
            if '-filter_complex_script' in command:
                path = Path(command[command.index('-filter_complex_script')+1])
                text = path.read_text(encoding='utf-8')
                self.assertIn('[1:a:0]volume=0.01[voice]', text)
                path.write_text(text.replace('[1:a:0]volume=0.01[voice]', '[1:a:0]volume=0[voice]'),
                                encoding='utf-8')
            return real_run(command, directory, duration, cancel, progress)
        with patch.object(render, '_run_ffmpeg', side_effect=omit_voice):
            with self.assertRaisesRegex(ValueError, 'không khớp'):
                self.perform(original_gain=2., voice_gain=.01)
        self.assertEqual(list((self.directory/'1').iterdir()), [])


if __name__ == '__main__':
    unittest.main()
