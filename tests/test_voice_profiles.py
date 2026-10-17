"""Real audio normalization/cache contracts; no voice model or network."""
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch
import wave

import imageio_ffmpeg
import numpy as np
import soundfile as sf

from teodub import voice_profiles as profiles
from teodub.common import Cancelled, atomic_json, hash_file


def tone(path, seconds=4, rate=22050, *, channels=1, subtype='PCM_16'):
    time = np.arange(round(seconds*rate), dtype=np.float64)/rate
    samples = .2*np.sin(2*np.pi*(310*time+19*time*time))
    if channels == 2:
        samples = np.stack((samples, .7*samples), axis=1)
    sf.write(path, samples, rate, subtype=subtype)


class ReferenceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='teodub-reference-test-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root/'mẫu giọng riêng.wav'
        tone(self.source)
        self.output = self.root/'bản sao mẫu'

    def prepare(self, **kwargs):
        return profiles.prepare_reference(self.source, self.output, **kwargs)

    def rewrite(self, **changes):
        path = self.output/'reference.json'
        metadata = json.loads(path.read_text(encoding='utf-8'))
        metadata.update(changes)
        atomic_json(path, metadata)

    def test_normalization_is_mono_pcm16_48k_and_preserves_original(self):
        before = self.source.read_bytes()
        tone(self.source, channels=2)
        before = self.source.read_bytes()
        metadata = self.prepare()
        with wave.open(metadata['path'], 'rb') as audio:
            self.assertEqual((audio.getnchannels(), audio.getsampwidth(), audio.getframerate()), (1, 2, 48000))
            self.assertEqual(audio.getnframes(), 192000)
        self.assertEqual(metadata['duration_s'], 4)
        self.assertEqual(metadata['source_sha256'], hash_file(self.source))
        self.assertEqual(metadata['sha256'], hash_file(metadata['path']))
        self.assertEqual(self.source.read_bytes(), before)
        self.assertEqual(profiles.validate_reference(metadata), metadata)
        self.assertEqual(set(item.name for item in self.output.iterdir()), {'reference.wav', 'reference.json'})

    def test_first_eight_seconds_only_and_fingerprint_does_not_depend_on_directory(self):
        tone(self.source, seconds=10, rate=44100)
        first = self.prepare()
        second = profiles.prepare_reference(self.source, self.root/'second')
        self.assertEqual(first['samples'], 8*48000)
        self.assertEqual(first['duration_s'], 8)
        self.assertEqual(first['sha256'], second['sha256'])
        self.assertEqual(first['fingerprint'], second['fingerprint'])
        self.assertNotEqual(first['path'], second['path'])

    def test_complete_cache_is_verified_and_reused_without_conversion(self):
        first = self.prepare()
        bytes_before = (self.output/'reference.wav').read_bytes()
        with patch.object(profiles, '_normalize', side_effect=AssertionError('cache must not convert')):
            second = self.prepare()
        self.assertEqual(first, second)
        self.assertEqual(bytes_before, (self.output/'reference.wav').read_bytes())

    def test_changed_source_is_rejected_without_replacing_cache(self):
        first = self.prepare()
        tone(self.source, seconds=5)
        with self.assertRaisesRegex(ValueError, 'không khớp'):
            self.prepare()
        self.assertEqual(hash_file(first['path']), first['sha256'])

    def test_tampered_normalized_audio_is_not_reused_or_overwritten(self):
        first = self.prepare()
        target = Path(first['path'])
        with target.open('ab') as file:
            file.write(b'changed')
        altered = target.read_bytes()
        with patch.object(profiles, '_normalize') as convert:
            with self.assertRaisesRegex(ValueError, 'thay đổi'):
                self.prepare()
        convert.assert_not_called()
        self.assertEqual(target.read_bytes(), altered)

    def test_saved_metadata_fields_are_verified(self):
        original = self.prepare()
        for field, value in [('samples', 1), ('duration_s', 100), ('sample_rate', 24000),
                             ('channels', 2), ('sample_width', 4), ('rms', .7), ('peak', .01),
                             ('fingerprint', '0'*64), ('normalization', 'changed')]:
            with self.subTest(field=field):
                with self.assertRaises(ValueError):
                    profiles.validate_reference(dict(original, **{field: value}))

    def test_frozen_source_hash_is_checked_by_validation_helper(self):
        metadata = self.prepare()
        with self.assertRaisesRegex(ValueError, 'cấu hình'):
            profiles.validate_reference(metadata, source_sha256='0'*64)

    def test_cached_path_cannot_redirect_to_another_matching_wav(self):
        first = self.prepare()
        other = self.root/'elsewhere.wav'
        other.write_bytes(Path(first['path']).read_bytes())
        self.rewrite(path=str(other))
        with self.assertRaisesRegex(ValueError, 'không khớp'):
            self.prepare()

    def test_short_reference_is_rejected_and_no_final_files_appear(self):
        tone(self.source, seconds=2.9)
        with self.assertRaisesRegex(ValueError, 'ít nhất 3 giây'):
            self.prepare()
        self.assertEqual(list(self.output.iterdir()), [])

    def test_exact_three_seconds_is_accepted(self):
        tone(self.source, seconds=3)
        self.assertEqual(self.prepare()['samples'], 144000)

    def test_silent_reference_is_rejected(self):
        sf.write(self.source, np.zeros(48000*4), 48000, subtype='PCM_16')
        with self.assertRaisesRegex(ValueError, 'im lặng'):
            self.prepare()
        self.assertEqual(list(self.output.iterdir()), [])

    def test_nonfinite_float_audio_rejected_before_pcm_conversion(self):
        for value in (float('nan'), float('inf'), -float('inf')):
            with self.subTest(value=value):
                samples = np.ones(48000*4, dtype=np.float32)*.2
                samples[12000] = value
                sf.write(self.source, samples, 48000, subtype='FLOAT')
                with self.assertRaisesRegex(ValueError, 'NaN/Inf'):
                    self.prepare()
                self.assertEqual(list(self.output.iterdir()), [])

    def test_cancel_before_preparation_creates_no_cache(self):
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(Cancelled):
            self.prepare(cancel=cancel)
        self.assertFalse(self.output.exists())

    def test_cancel_after_normalization_does_not_publish_a_reference(self):
        cancel = threading.Event()
        real_normalize = profiles._normalize
        def normalize_then_cancel(*args, **kwargs):
            result = real_normalize(*args, **kwargs)
            cancel.set()
            return result
        with patch.object(profiles, '_normalize', side_effect=normalize_then_cancel):
            with self.assertRaises(Cancelled):
                self.prepare(cancel=cancel)
        self.assertEqual(list(self.output.iterdir()), [])

    def test_source_change_during_normalization_blocks_publication(self):
        real_normalize = profiles._normalize
        def change_source(*args, **kwargs):
            result = real_normalize(*args, **kwargs)
            with self.source.open('ab') as file:
                file.write(b'changed')
            return result
        with patch.object(profiles, '_normalize', side_effect=change_source):
            with self.assertRaisesRegex(ValueError, 'gốc thay đổi'):
                self.prepare()
        self.assertEqual(list(self.output.iterdir()), [])

    def test_orphan_wav_is_preserved_and_not_regenerated(self):
        self.output.mkdir()
        target = self.output/'reference.wav'
        target.write_bytes(b'orphan')
        with self.assertRaisesRegex(ValueError, 'thiếu báo cáo'):
            self.prepare()
        self.assertEqual(target.read_bytes(), b'orphan')

    def test_crash_after_wav_publication_recovers_pending_without_normalizing(self):
        real_json = profiles._json_new
        def fail_manifest(path, data):
            if Path(path).name == 'reference.json':
                raise OSError('simulated crash after WAV')
            return real_json(path, data)
        with patch.object(profiles, '_json_new', side_effect=fail_manifest):
            with self.assertRaises(OSError):
                self.prepare()
        self.assertTrue((self.output/'reference.wav').is_file())
        self.assertTrue((self.output/'reference.pending.json').is_file())
        with patch.object(profiles, '_normalize', side_effect=AssertionError('must recover')):
            result = self.prepare()
        self.assertEqual(result['sha256'], hash_file(result['path']))
        self.assertTrue((self.output/'reference.json').is_file())
        self.assertFalse((self.output/'reference.pending.json').exists())

    def test_cancel_after_wav_publication_can_resume_safely(self):
        cancel = threading.Event()
        real_publish = profiles._publish
        def cancel_after_wav(source, target):
            real_publish(source, target)
            if Path(target).suffix == '.wav':
                cancel.set()
        with patch.object(profiles, '_publish', side_effect=cancel_after_wav):
            with self.assertRaises(Cancelled):
                self.prepare(cancel=cancel)
        before = hash_file(self.output/'reference.wav')
        cancel.clear()
        result = self.prepare(cancel=cancel)
        self.assertEqual(result['sha256'], before)

    def test_missing_cached_audio_is_not_silently_recreated(self):
        first = self.prepare()
        Path(first['path']).unlink()
        with patch.object(profiles, '_normalize') as convert:
            with self.assertRaises(ValueError):
                self.prepare()
        convert.assert_not_called()

    def test_common_compressed_audio_and_video_formats_decode_locally(self):
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        for suffix in ('mp3', 'm4a', 'mp4'):
            with self.subTest(suffix=suffix):
                source = self.root/f'âm thanh.{suffix}'
                command = [ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin', '-n', '-i', str(self.source)]
                if suffix == 'mp4':
                    command += ['-f', 'lavfi', '-i', 'color=blue:size=32x32:rate=5:duration=4',
                                '-c:v', 'libx264', '-threads', '2', '-shortest']
                command += ['-c:a', 'libmp3lame' if suffix == 'mp3' else 'aac', str(source)]
                subprocess.run(command, check=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.PIPE, timeout=30,
                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                result = profiles.prepare_reference(source, self.root/f'from-{suffix}')
                self.assertGreaterEqual(result['duration_s'], 3.9)
                self.assertLess(result['duration_s'], 4.2)
                self.assertEqual(result['sample_rate'], 48000)


if __name__ == '__main__':
    unittest.main()
