"""Local cache and interruption contracts; never load ASR/TTS models or network."""
from contextlib import contextmanager
import json
from pathlib import Path
import tempfile
import threading
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np
import soundfile as sf

from teodub import asr, tts
from teodub.common import Cancelled, atomic_json, hash_file
from teodub.subtitles import read_srt


def tone(duration=.2):
    samples = np.arange(round(48000 * duration), dtype=np.float32) / 48000
    return (.15 * np.sin(2 * np.pi * 377 * samples)).astype(np.float32)


class TemporaryCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='teodub-speech-test-')
        self.directory = Path(self.temporary.name).resolve()
        self.addCleanup(self.temporary.cleanup)

    def rewrite_json(self, path, **updates):
        data = json.loads(Path(path).read_text(encoding='utf-8'))
        data.update(updates)
        atomic_json(path, data)


class AsrTests(TemporaryCase):
    def setUp(self):
        super().setUp()
        self.source = self.directory / 'nguồn tiếng Trung.mp4'
        self.source.write_bytes(b'not decoded: deterministic source identity')
        self.output = self.directory / 'phụ đề' / 'tieng-trung.srt'
        self.segments = [SimpleNamespace(start=0, end=1, text='  你好  '),
                         SimpleNamespace(start=1.5, end=3, text='我们走吧。')]

    @contextmanager
    def model(self, segments=None):
        engine = Mock()
        engine.transcribe.return_value = (iter(self.segments if segments is None else segments),
                                          SimpleNamespace(duration=3.0))
        factory = Mock(return_value=engine)
        whisper = ModuleType('faster_whisper')
        whisper.WhisperModel = factory
        translate = ModuleType('ctranslate2')
        translate.get_cuda_device_count = lambda: 0
        translate.get_supported_compute_types = lambda device: {'int8', 'float32'}
        with patch.dict('sys.modules', {'ctranslate2': translate, 'faster_whisper': whisper}), \
                patch.object(asr, 'configure_models', return_value=self.directory / 'models'):
            yield factory, engine

    def generate(self, **kwargs):
        with self.model() as model:
            result = asr.transcribe(self.source, self.output, **kwargs)
        return result, model

    def test_complete_cache_reused_without_loading_model(self):
        result, (_, engine) = self.generate()
        self.assertEqual(result['cue_count'], 2)
        self.assertEqual([cue['text'] for cue in read_srt(self.output)], ['你好', '我们走吧。'])
        self.assertEqual(result['source_sha256'], hash_file(self.source))
        self.assertEqual(result['sha256'], hash_file(self.output))
        self.assertEqual(engine.transcribe.call_args.kwargs['language'], 'zh')
        with patch.object(asr, 'configure_models', side_effect=AssertionError('model cache hit')):
            reused = asr.transcribe(self.source, self.output)
        self.assertEqual(reused, result)

    def test_changed_source_rejected_before_loading_model(self):
        self.generate()
        self.source.write_bytes(b'changed source')
        with patch.object(asr, 'configure_models') as load:
            with self.assertRaises(ValueError):
                asr.transcribe(self.source, self.output)
        load.assert_not_called()

    def test_changed_srt_rejected_before_loading_model(self):
        self.generate()
        self.output.write_text(self.output.read_text(encoding='utf-8').replace('你好', '再见'), encoding='utf-8')
        with patch.object(asr, 'configure_models') as load:
            with self.assertRaises(ValueError):
                asr.transcribe(self.source, self.output)
        load.assert_not_called()

    def test_cache_metadata_cannot_redirect_or_miscount_subtitles(self):
        result, _ = self.generate()
        metadata = self.output.with_suffix('.asr.json')
        for fields in ({'path': str(self.directory / 'unrelated.srt')},
                       {'cue_count': 999}, {'language': 'en'}):
            with self.subTest(fields=fields):
                atomic_json(metadata, {**result, **fields})
                with patch.object(asr, 'configure_models') as load:
                    with self.assertRaises(ValueError):
                        asr.transcribe(self.source, self.output)
                load.assert_not_called()

    def test_precancelled_cache_does_not_return_success(self):
        self.generate()
        stop = threading.Event()
        stop.set()
        with self.assertRaises(Cancelled):
            asr.transcribe(self.source, self.output, cancel=stop)

    def test_cancelled_segment_iteration_publishes_nothing_and_can_retry(self):
        stop = threading.Event()

        def interrupted():
            yield self.segments[0]
            stop.set()
            yield self.segments[1]

        with self.model(interrupted()):
            with self.assertRaises(Cancelled):
                asr.transcribe(self.source, self.output, cancel=stop)
        self.assertFalse(self.output.exists())
        self.assertFalse(self.output.with_suffix('.asr.json').exists())
        stop.clear()
        result, _ = self.generate(cancel=stop)
        self.assertEqual(result['cue_count'], 2)

    def test_source_changed_during_recognition_never_publishes(self):
        def changed():
            yield self.segments[0]
            self.source.write_bytes(b'changed while processing')
            yield self.segments[1]

        with self.model(changed()):
            with self.assertRaises(ValueError):
                asr.transcribe(self.source, self.output)
        self.assertFalse(self.output.exists())

    def test_no_speech_or_invalid_order_never_publishes(self):
        for segments in ([], [SimpleNamespace(start=0, end=1, text='  ')],
                         [self.segments[0], SimpleNamespace(start=.2, end=.5, text='重复')]):
            with self.subTest(segments=segments), self.model(segments):
                with self.assertRaises(ValueError):
                    asr.transcribe(self.source, self.output)
            self.assertFalse(self.output.exists())

    def crash_after_srt_publish(self):
        def fail_final_report(path, value):
            if Path(path) == self.output.with_suffix('.asr.json'):
                raise OSError('simulated disk error')
            return atomic_json(path, value)

        with self.model(), patch.object(asr, 'atomic_json', side_effect=fail_final_report):
            with self.assertRaises(OSError):
                asr.transcribe(self.source, self.output)

    def test_crash_after_srt_publish_recovers_verified_pending_without_model(self):
        self.crash_after_srt_publish()
        before = hash_file(self.output)
        self.assertFalse(self.output.with_suffix('.asr.json').exists())
        self.assertTrue(self.output.with_suffix('.asr.pending.json').exists())
        with patch.object(asr, 'configure_models') as load:
            result = asr.transcribe(self.source, self.output)
        load.assert_not_called()
        self.assertEqual(hash_file(self.output), before)
        self.assertEqual(result['cue_count'], 2)
        self.assertEqual(result['sha256'], before)

    def test_orphan_srt_without_pending_never_regenerated(self):
        self.generate()
        self.output.with_suffix('.asr.json').unlink()
        self.output.with_suffix('.asr.pending.json').unlink(missing_ok=True)
        before = hash_file(self.output)
        with patch.object(asr, 'configure_models') as load:
            with self.assertRaises((OSError, ValueError)):
                asr.transcribe(self.source, self.output)
        load.assert_not_called()
        self.assertEqual(hash_file(self.output), before)

    def test_pending_source_or_subtitle_tampering_cannot_trigger_asr_replay(self):
        self.crash_after_srt_publish()
        pending = self.output.with_suffix('.asr.pending.json')
        before = hash_file(self.output)
        original = json.loads(pending.read_text(encoding='utf-8'))
        for fields in ({'source_sha256': '0' * 64}, {'sha256': '0' * 64},
                       {'path': str(self.directory / 'unrelated.srt')}):
            with self.subTest(fields=fields):
                atomic_json(pending, {**original, **fields})
                with patch.object(asr, 'configure_models') as load:
                    with self.assertRaises((OSError, ValueError)):
                        asr.transcribe(self.source, self.output)
                load.assert_not_called()
                self.assertEqual(hash_file(self.output), before)
                self.output.with_suffix('.asr.json').unlink(missing_ok=True)


class TtsTests(TemporaryCase):
    def setUp(self):
        super().setUp()
        self.output = self.directory / 'lời đọc'
        self.cues = [dict(start_ms=0, end_ms=1000, text='Chào mọi người.'),
                     dict(start_ms=1300, end_ms=2300, text='Cùng bắt đầu nhé.')]
        self.voice = tts.VOICES[0]
        self.engine = Mock()
        self.engine.infer.return_value = tone()
        self.get_model = self.enterContext(patch.object(tts, 'get_model', return_value=self.engine))

    def perform(self, cues=None, **kwargs):
        return tts.synthesize(self.cues if cues is None else cues, self.voice, self.output, **kwargs)

    def test_complete_cache_reuses_all_cues_without_inference(self):
        clips = self.perform()
        self.assertEqual([clip['id'] for clip in clips], [1, 2])
        self.assertEqual([clip['text'] for clip in clips], [cue['text'] for cue in self.cues])
        self.assertEqual(self.engine.infer.call_count, 2)
        self.get_model.reset_mock()
        self.engine.infer.reset_mock()
        reused = self.perform()
        self.assertEqual(reused, clips)
        self.get_model.assert_not_called()
        self.engine.infer.assert_not_called()
        manifest = json.loads((self.output / 'manifest.json').read_text(encoding='utf-8'))
        self.assertEqual(manifest['clips'], clips)

    def test_changed_audio_rejected_without_inference(self):
        self.perform()
        sf.write(str(self.output / '00002.wav'), tone(.3), 48000, subtype='PCM_16')
        self.engine.infer.reset_mock()
        with self.assertRaises(ValueError):
            self.perform()
        self.engine.infer.assert_not_called()

    def test_cache_fields_cannot_redirect_clip_or_forge_identity(self):
        self.perform()
        metadata = self.output / '00001.json'
        original = json.loads(metadata.read_text(encoding='utf-8'))
        changes = ({'path': str(self.directory / 'unrelated.wav')}, {'duration_s': 99.0},
                   {'samples': 1}, {'sample_rate': 22050}, {'id': 8}, {'text': 'Sai lời.'},
                   {'voice': tts.VOICES[1]}, {'model': 'unrelated-model'})
        for fields in changes:
            with self.subTest(fields=fields):
                atomic_json(metadata, {**original, **fields})
                self.engine.infer.reset_mock()
                with self.assertRaises(ValueError):
                    self.perform()
                self.engine.infer.assert_not_called()

    def test_changed_text_or_voice_rejected_without_inference(self):
        self.perform()
        self.engine.infer.reset_mock()
        with self.assertRaises(ValueError):
            self.perform(cues=[{**self.cues[0], 'text': 'Lời khác.'}, self.cues[1]])
        with self.assertRaises(ValueError):
            tts.synthesize(self.cues, tts.VOICES[1], self.output)
        self.engine.infer.assert_not_called()

    def test_empty_cues_never_publish_success(self):
        with self.assertRaises(ValueError):
            self.perform(cues=[])
        self.engine.infer.assert_not_called()
        self.assertFalse((self.output / 'manifest.json').exists())

    def test_shorter_resume_does_not_replace_complete_manifest(self):
        self.perform()
        before = hash_file(self.output / 'manifest.json')
        self.engine.infer.reset_mock()
        with self.assertRaises(ValueError):
            self.perform(cues=self.cues[:1])
        self.engine.infer.assert_not_called()
        self.assertEqual(hash_file(self.output / 'manifest.json'), before)

    def test_precancelled_request_loads_no_model(self):
        stop = threading.Event()
        stop.set()
        with self.assertRaises(Cancelled):
            self.perform(cancel=stop)
        self.get_model.assert_not_called()
        self.assertFalse((self.output / 'manifest.json').exists())

    def test_cancellation_after_inference_publishes_no_clip(self):
        stop = threading.Event()

        def interrupted(*args, **kwargs):
            stop.set()
            return tone()

        self.engine.infer.side_effect = interrupted
        with self.assertRaises(Cancelled):
            self.perform(cancel=stop)
        self.assertFalse((self.output / '00001.wav').exists())
        self.assertFalse((self.output / '00001.json').exists())
        self.assertFalse((self.output / 'manifest.json').exists())

    def test_cancel_after_one_cue_keeps_it_and_resume_only_generates_remaining(self):
        stop = threading.Event()

        def progress(message, percent=None):
            if (self.output / '00001.json').exists():
                stop.set()

        with self.assertRaises(Cancelled):
            self.perform(cancel=stop, progress=progress)
        first_hash = hash_file(self.output / '00001.wav')
        self.assertFalse((self.output / '00002.wav').exists())
        self.assertFalse((self.output / 'manifest.json').exists())
        self.engine.infer.reset_mock()
        stop.clear()
        clips = self.perform(cancel=stop)
        self.engine.infer.assert_called_once_with(self.cues[1]['text'], voice=self.voice)
        self.assertEqual(len(clips), 2)
        self.assertEqual(hash_file(self.output / '00001.wav'), first_hash)

    def test_shortened_partial_resume_is_rejected(self):
        stop = threading.Event()

        def progress(message, percent=None):
            if (self.output / '00001.json').exists():
                stop.set()

        with self.assertRaises(Cancelled):
            self.perform(cancel=stop, progress=progress)
        stop.clear()
        self.engine.infer.reset_mock()
        with self.assertRaises(ValueError):
            self.perform(cues=self.cues[:1], cancel=stop)
        self.engine.infer.assert_not_called()
        self.assertFalse((self.output / 'manifest.json').exists())

    def test_cancel_after_last_cue_does_not_publish_success_manifest(self):
        stop = threading.Event()

        def progress(message, percent=None):
            if (self.output / '00002.json').exists():
                stop.set()

        with self.assertRaises(Cancelled):
            self.perform(cancel=stop, progress=progress)
        self.assertTrue((self.output / '00002.wav').exists())
        self.assertFalse((self.output / 'manifest.json').exists())
        stop.clear()
        self.engine.infer.reset_mock()
        self.assertEqual(len(self.perform(cancel=stop)), 2)
        self.engine.infer.assert_not_called()

    def test_orphan_wav_without_report_never_overwritten_or_regenerated(self):
        self.output.mkdir()
        wav = self.output / '00001.wav'
        sf.write(str(wav), tone(), 48000, subtype='PCM_16')
        before = hash_file(wav)
        with self.assertRaises(ValueError):
            self.perform()
        self.engine.infer.assert_not_called()
        self.assertEqual(hash_file(wav), before)

    def crash_between_wav_and_report(self):
        original_atomic_json = atomic_json

        def failing_clip_report(path, value):
            if Path(path).name == '00001.json':
                raise OSError('simulated disk error')
            return original_atomic_json(path, value)

        with patch.object(tts, 'atomic_json', side_effect=failing_clip_report):
            with self.assertRaises(OSError):
                self.perform()

    def test_crash_between_wav_and_report_recovers_pending_without_reinfer(self):
        self.crash_between_wav_and_report()
        wav = self.output / '00001.wav'
        before = hash_file(wav)
        self.assertFalse((self.output / '00001.json').exists())
        self.assertTrue((self.output / '00001.pending.json').exists())
        self.engine.infer.reset_mock()
        clips = self.perform()
        self.engine.infer.assert_called_once_with(self.cues[1]['text'], voice=self.voice)
        self.assertEqual(len(clips), 2)
        self.assertEqual(hash_file(wav), before)

    def test_pending_identity_or_hash_tampering_fails_without_reinfer(self):
        self.crash_between_wav_and_report()
        pending = self.output / '00001.pending.json'
        original = json.loads(pending.read_text(encoding='utf-8'))
        before = hash_file(self.output / '00001.wav')
        for fields in ({'sha256': '0' * 64}, {'fingerprint': '0' * 64},
                       {'path': str(self.directory / 'unrelated.wav')}, {'text': 'Lời giả.'}):
            with self.subTest(fields=fields):
                atomic_json(pending, {**original, **fields})
                self.engine.infer.reset_mock()
                with self.assertRaises(ValueError):
                    self.perform()
                self.engine.infer.assert_not_called()
                self.assertEqual(hash_file(self.output / '00001.wav'), before)
                self.assertFalse((self.output / 'manifest.json').exists())
                (self.output / '00001.json').unlink(missing_ok=True)

    def test_final_manifest_write_failure_resumes_without_recreating_any_audio(self):
        def failed_manifest(path, value):
            if Path(path).name == 'manifest.json':
                raise OSError('simulated full disk')
            return atomic_json(path, value)

        with patch.object(tts, 'atomic_json', side_effect=failed_manifest):
            with self.assertRaises(OSError):
                self.perform()
        self.engine.infer.reset_mock()
        self.assertFalse((self.output / 'manifest.json').exists())
        self.assertEqual(len(self.perform()), 2)
        self.engine.infer.assert_not_called()

    def test_missing_cached_wav_never_regenerated(self):
        self.perform()
        (self.output / '00001.wav').unlink()
        self.engine.infer.reset_mock()
        with self.assertRaises((OSError, ValueError, RuntimeError)):
            self.perform()
        self.engine.infer.assert_not_called()

    def test_invalid_audio_retries_once_then_fails_without_completion(self):
        self.engine.infer.return_value = np.zeros(4800, dtype=np.float32)
        with self.assertRaises(ValueError):
            self.perform()
        self.assertEqual(self.engine.infer.call_count, 2)
        self.assertFalse((self.output / '00001.wav').exists())
        self.assertFalse((self.output / 'manifest.json').exists())

    def test_invalid_first_audio_can_retry_once_to_complete(self):
        self.engine.infer.side_effect = [np.zeros(4800, dtype=np.float32), tone(), tone()]
        clips = self.perform()
        self.assertEqual(len(clips), 2)
        self.assertEqual(self.engine.infer.call_count, 3)

    def test_wrong_rate_stereo_short_and_silent_audio_rejected(self):
        path = self.directory / 'invalid.wav'
        for data, rate in ((tone(), 22050), (np.stack([tone(), tone()], axis=1), 48000),
                           (tone(.04), 48000), (np.zeros(4800, dtype=np.float32), 48000)):
            with self.subTest(shape=data.shape, rate=rate):
                sf.write(str(path), data, rate, subtype='PCM_16')
                with self.assertRaises(ValueError):
                    tts.validate_audio(path)


if __name__ == '__main__':
    unittest.main()
