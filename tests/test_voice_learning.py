"""Real clip extraction/reference integrity and deterministic analysis contracts."""
import json
from pathlib import Path
from types import ModuleType, SimpleNamespace
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
import uuid
import subprocess

import numpy as np
import soundfile as sf

from teodub import voice_learning as lab
from teodub.common import Cancelled, atomic_json, hash_file
from tests.test_voice_profiles import tone


class VoiceLearningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root/'mẫu video.wav'
        tone(self.source, seconds=12, rate=48000)
        self.config = dict(id=uuid.uuid4().hex, data_dir=str(self.root), sources=[str(self.source)],
            asr_model='small', start_s=2, limit_s=8)
        words = [SimpleNamespace(start=i, end=i+.8, word=' lời') for i in range(7)]
        segment = SimpleNamespace(start=.1, end=7.8, text=' lời nói tiếng Việt rất rõ ràng',
            words=words, no_speech_prob=.01, avg_logprob=-.1)
        self.model = Mock()
        self.model.transcribe.return_value = (iter([segment]), SimpleNamespace(language='vi'))

    def analyze(self, **updates):
        config = {**self.config, **updates}
        with patch.object(lab, '_recognizer', return_value=(self.model, 'cuda')):
            return lab.analyze_sources(config)

    def make_profile(self):
        report = self.analyze()
        return lab.save_profile(self.root, report['id'], report['candidates'][0]['id'], 'Cô gái', 'Hoạt ngôn')

    def test_extracts_selected_offset_and_asr_is_not_forced_to_chinese(self):
        before = hash_file(self.source)
        report = self.analyze()
        self.assertFalse(report['weights_trained'])
        self.assertEqual(report['device'], 'cuda')
        c = report['candidates'][0]
        self.assertAlmostEqual(c['start_s'], 2.1)
        self.assertLessEqual(c['metrics']['duration_s'], 8)
        samples, rate = sf.read(c['path'])
        self.assertEqual(rate, 48000)
        self.assertEqual(samples.ndim, 1)
        kwargs = self.model.transcribe.call_args.kwargs
        self.assertNotIn('language', kwargs)
        self.assertTrue(kwargs['vad_filter'])
        self.assertTrue(kwargs['word_timestamps'])
        self.assertEqual(before, hash_file(self.source))

    def test_profile_has_own_copy_and_survives_source_and_analysis_removal(self):
        p = self.make_profile()
        c = p['candidate']
        self.source.unlink()
        Path(c['path']).unlink()
        (lab.library_root(self.root)/'analyses'/p['analysis_id']/'analysis.json').unlink()
        self.assertEqual(lab.checked_profile(self.root, p['id']), p)
        selection = lab.profile_selection(self.root, p['id'], 'v3nano')
        self.assertEqual(selection['voice'], 'Giọng từ mẫu')
        self.assertEqual(selection['tts_model'], 'v3nano')
        self.assertEqual(selection['reference_sha256'], hash_file(selection['reference_audio']))
        self.assertEqual((selection['reading_style'], selection['voice_speed']), ('legacy', 1.0))

    def test_new_profile_does_not_modify_previous_audio(self):
        report = self.analyze()
        candidate = report['candidates'][0]['id']
        a = lab.save_profile(self.root, report['id'], candidate, 'Cô gái', 'Hoạt ngôn')
        before = hash_file(a['reference_audio'])
        b = lab.save_profile(self.root, report['id'], candidate, 'Cô gái', 'Kể chuyện')
        self.assertNotEqual(a['id'], b['id'])
        self.assertNotEqual(a['reference_audio'], b['reference_audio'])
        self.assertEqual(before, hash_file(a['reference_audio']))
        self.assertEqual(len(lab.list_profiles(self.root)[0]), 2)

    def test_tampered_candidate_or_saved_profile_is_blocked(self):
        p = self.make_profile()
        tone(p['reference_audio'], seconds=5, rate=48000)
        with self.assertRaises(ValueError):
            lab.profile_selection(self.root, p['id'])
        tone(p['candidate']['path'], seconds=5, rate=48000)
        with self.assertRaisesRegex(ValueError, 'thay đổi'):
            lab.save_profile(self.root, p['analysis_id'], p['candidate']['id'], 'Giọng khác')

    def test_outside_candidate_path_refused(self):
        report = self.analyze()
        report['candidates'][0]['path'] = str(self.source)
        atomic_json(lab.library_root(self.root)/'analyses'/report['id']/'analysis.json', report)
        with self.assertRaisesRegex(ValueError, 'không khớp'):
            lab.checked_candidate(self.root, report['id'], report['candidates'][0]['id'])

    def test_missing_or_corrupt_library_entry_does_not_hide_other_profiles(self):
        p = self.make_profile()
        folder = lab.library_root(self.root)/'profiles'/uuid.uuid4().hex
        folder.mkdir()
        (folder/'profile.json').write_text('{bad', encoding='utf-8')
        profiles, errors = lab.list_profiles(self.root)
        self.assertEqual(profiles, [p])
        self.assertEqual(len(errors), 1)

    def test_no_speech_and_partial_input_failure_are_reported(self):
        report = self.analyze(sources=[str(self.root/'missing.mp4'), str(self.source)])
        self.assertEqual(len(report['errors']), 1)
        self.assertTrue(report['candidates'])
        self.model.transcribe.return_value = (iter([]), SimpleNamespace(language='vi'))
        with self.assertRaisesRegex(ValueError, 'Chưa tìm'):
            self.analyze(id=uuid.uuid4().hex)

    def test_short_silent_or_cancelled_input_not_saved(self):
        with self.assertRaises(ValueError):
            lab.audio_metrics(np.zeros(48000*4))
        with self.assertRaises(ValueError):
            lab.audio_metrics(np.ones(48000))
        cancel = threading.Event()
        cancel.set()
        with patch.object(lab, '_recognizer', return_value=(self.model, 'cuda')):
            with self.assertRaises(Cancelled):
                lab.analyze_sources(self.config, cancel=cancel)
        self.assertFalse((lab.library_root(self.root)/'analyses'/self.config['id']/'analysis.json').exists())

    def test_invalid_model_limits_id_and_name_are_rejected(self):
        for updates in ({'asr_model': 'auto'}, {'limit_s': 301}, {'start_s': -1}, {'limit_s': float('nan')},
                        {'sources': []}, {'id': '../escape'}):
            with self.subTest(updates=updates):
                with self.assertRaises(ValueError):
                    self.analyze(**updates)
        p = self.make_profile()
        with self.assertRaises(ValueError):
            lab.save_profile(self.root, p['analysis_id'], p['candidate']['id'], '')

    def test_speech_windows_skip_unconfident_segments_and_keep_bounds(self):
        windows = lab.speech_windows([dict(start=0, end=20, text='lời nói'),
            dict(start=21, end=25, text='nhạc', no_speech_prob=.9)], 30)
        self.assertEqual(len(windows), 3)
        for window in windows:
            self.assertGreaterEqual(window['end_s']-window['start_s'], 3)
            self.assertLessEqual(window['end_s']-window['start_s'], 8)

    def test_asr_gpu_selection_real_arguments_no_model_fallback(self):
        whisper, ct = ModuleType('faster_whisper'), ModuleType('ctranslate2')
        whisper.WhisperModel = Mock(return_value=self.model)
        ct.get_cuda_device_count = lambda: 1
        ct.get_supported_compute_types = lambda device: {'int8', 'int8_float16', 'float32'}
        with patch.dict('sys.modules', {'faster_whisper': whisper, 'ctranslate2': ct}), \
             patch('teodub.asr_runtime.gpu_memory_mib', return_value=12288), \
             patch('teodub.models.configure_models', return_value=self.root):
            engine, device = lab._recognizer('small', Mock())
            self.assertEqual(engine, self.model)
            self.assertEqual(device, 'cuda')
            self.assertEqual(whisper.WhisperModel.call_args.args, ('small',))
            self.assertEqual(whisper.WhisperModel.call_args.kwargs['device'], 'cuda')
            whisper.WhisperModel.side_effect = RuntimeError('CUDA failed')
            with self.assertRaisesRegex(RuntimeError, 'CUDA failed'):
                lab._recognizer('small', Mock())
            self.assertEqual(whisper.WhisperModel.call_count, 2)

    def test_saved_profile_preview_reaches_actual_reference_infer_and_cache(self):
        from teodub import tts
        p = self.make_profile()
        model = Mock()
        model.infer.return_value = (.15*np.sin(np.arange(48000)*.04)).astype('float32')
        config = dict(data_dir=str(self.root), id=uuid.uuid4().hex, profile_id=p['id'],
            text='Xin chào, đây là lời mới dùng giọng mẫu.', tts_model='v3turbo')
        with patch.object(tts, 'get_model', return_value=model):
            result = lab.run_preview(config)
            self.assertTrue(Path(result['path']).is_file())
            kwargs = model.infer.call_args.kwargs
            self.assertTrue(kwargs['use_ref_codes'])
            self.assertNotIn('voice', kwargs)
            self.assertEqual(hash_file(kwargs['ref_audio']), p['reference_sha256'])
            self.assertEqual(lab.run_preview(config), result)
            model.infer.assert_called_once()

    def test_worker_lock_failure_reports_error_without_touching_other_run(self):
        from teodub.pipeline import _job_lock
        folder = lab.library_root(self.root)/'analyses'/self.config['id']
        folder.mkdir(parents=True)
        config = {**self.config, 'operation': 'analyze'}
        atomic_json(folder/'config.json', config)
        execution = self.root/'execution'
        execution.mkdir()
        with _job_lock(execution), patch('teodub.common.protect_worker'), patch.object(lab, 'analyze_sources') as run:
            code = lab.worker_main(['--config', str(folder/'config.json')])
            self.assertEqual(code, 2)
            run.assert_not_called()
        result = json.loads((folder/'result.json').read_text(encoding='utf-8'))
        self.assertEqual(result['status'], 'failed')
        self.assertIn('tiến trình khác', result['message'])

    def test_worker_never_overwrites_completed_run_or_replays_model(self):
        folder = lab.library_root(self.root)/'analyses'/self.config['id']
        folder.mkdir(parents=True)
        atomic_json(folder/'config.json', {**self.config, 'operation': 'analyze'})
        result = {'status': 'completed', 'result': {'proof': 'existing'}}
        atomic_json(folder/'result.json', result)
        with patch.object(lab, 'analyze_sources') as run:
            self.assertEqual(lab.worker_main(['--config', str(folder/'config.json')]), 0)
            run.assert_not_called()
        self.assertEqual(json.loads((folder/'result.json').read_text(encoding='utf-8')), result)

    def test_import_actual_mp4_keeps_original_video_and_extracts_only_audio(self):
        import imageio_ffmpeg
        video = self.root/'video mẫu.mp4'
        command = [imageio_ffmpeg.get_ffmpeg_exe(), '-nostdin', '-v', 'error', '-f', 'lavfi',
            '-i', 'color=c=blue:s=160x120:r=10:d=12', '-i', str(self.source),
            '-c:v', 'libx264', '-c:a', 'aac', '-shortest', str(video)]
        subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        before = hash_file(video)
        report = self.analyze(sources=[str(video)])
        self.assertEqual(report['candidates'][0]['source_name'], video.name)
        self.assertEqual(hash_file(video), before)
        self.assertTrue(report['candidates'][0]['path'].endswith('.wav'))


if __name__ == '__main__':
    unittest.main()
