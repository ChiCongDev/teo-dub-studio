"""Real pitch-preserving tempo checks using locally generated audio."""
from pathlib import Path
import tempfile
import threading
import unittest
import numpy as np
import soundfile as sf

from teodub.common import Cancelled, hash_file
from teodub.speech_audio import apply_tempo, VOICE_SPEEDS, validate_voice_speed


class TempoTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='teodub-tempo-test-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.source, self.target = self.root/'source.wav', self.root/'paced.wav'
        t = np.arange(96000)/48000
        sf.write(self.source, .15*np.sin(2*np.pi*440*t), 48000, subtype='PCM_16')

    def test_slow_and_fast_preserve_pitch_source_and_all_expected_time(self):
        original = hash_file(self.source)
        for rate in (.736, .8, .85, .92, .98, 1.06, 1.15, 1.2, 1.25, 1.3, 1.378):
            with self.subTest(rate=rate):
                result = apply_tempo(self.source, self.target, rate)
                self.assertAlmostEqual(result['duration_s'], 2/rate, delta=.08)
                data, sr = sf.read(self.target)
                part = data[sr//4:sr//4+sr]
                spectrum = np.abs(np.fft.rfft(part*np.hanning(len(part))))
                peak = np.fft.rfftfreq(len(part), 1/sr)[spectrum.argmax()]
                self.assertAlmostEqual(peak, 440, delta=2)
                self.assertEqual(sr, 48000)
                self.assertEqual(original, hash_file(self.source))

    def test_precancelled_does_not_publish_or_modify_existing_target(self):
        self.target.write_bytes(b'existing intermediate')
        before = hash_file(self.target)
        stop = threading.Event()
        stop.set()
        with self.assertRaises(Cancelled):
            apply_tempo(self.source, self.target, .92, cancel=stop)
        self.assertEqual(hash_file(self.target), before)

    def test_invalid_tempo_or_same_destination_never_changes_source(self):
        before = hash_file(self.source)
        for rate in (0, .69, 1.41, 4, True, float('nan'), 'fast'):
            with self.subTest(rate=rate), self.assertRaises(ValueError):
                apply_tempo(self.source, self.target, rate)
        with self.assertRaises(ValueError):
            apply_tempo(self.source, self.source, .92)
        self.assertEqual(hash_file(self.source), before)

    def test_silent_engine_audio_not_published(self):
        sf.write(self.source, np.zeros(96000), 48000, subtype='PCM_16')
        with self.assertRaises(ValueError):
            apply_tempo(self.source, self.target, .98)
        self.assertFalse(self.target.exists())

    def test_all_speed_steps_and_invalid_values(self):
        self.assertEqual(VOICE_SPEEDS, (.8, .85, .9, .95, 1., 1.05, 1.1, 1.15, 1.2, 1.25, 1.3))
        for speed in VOICE_SPEEDS:
            self.assertEqual(validate_voice_speed(speed), speed)
        for speed in (.75, .81, 1.35, True, float('nan'), float('inf'), '0.8'):
            with self.subTest(speed=speed), self.assertRaises(ValueError):
                validate_voice_speed(speed)


if __name__ == '__main__':
    unittest.main()
