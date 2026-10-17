"""Hardware compatibility and interrupted ASR identities; no real inference."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from teodub import asr, asr_runtime, voice_learning
from teodub.common import atomic_json, hash_file


def backend(types=('float32', 'int8', 'int8_float32'), count=1):
    return SimpleNamespace(get_cuda_device_count=Mock(return_value=count),
        get_supported_compute_types=Mock(side_effect=lambda device:
            set(types) if device == 'cuda' else {'int8', 'float32'}))


class RuntimeTests(unittest.TestCase):
    def resolve(self, requested='auto', memory=2048, ct=None, **kwargs):
        with patch.object(asr_runtime, 'gpu_memory_mib', return_value=memory):
            return asr_runtime.recognizer_runtime(requested, backend=ct or backend(), **kwargs)

    def test_gtx1050_auto_uses_small_and_supported_gpu_precision(self):
        runtime = self.resolve()
        self.assertEqual((runtime['model'], runtime['device'], runtime['compute_type']),
                         ('small', 'cuda', 'int8_float32'))
        self.assertIn('2048', runtime['note'])

    def test_modern_gpu_keeps_large_with_fp16_quantization(self):
        runtime = self.resolve(memory=12288, ct=backend(('int8_float16', 'int8_float32', 'float32')))
        self.assertEqual((runtime['model'], runtime['device'], runtime['compute_type']),
                         ('large-v3', 'cuda', 'int8_float16'))

    def test_intermediate_gpu_selects_medium(self):
        self.assertEqual(self.resolve(memory=4096)['model'], 'medium')

    def test_explicit_large_on_2gb_runs_same_model_on_cpu(self):
        runtime = self.resolve('large-v3')
        self.assertEqual((runtime['model'], runtime['device'], runtime['compute_type']),
                         ('large-v3', 'cpu', 'int8'))
        self.assertIn('giữ nguyên mô hình', runtime['note'])

    def test_old_auto_pin_is_not_replaced_by_new_small_default(self):
        runtime = self.resolve(resolved_model='large-v3')
        self.assertEqual((runtime['model'], runtime['device']), ('large-v3', 'cpu'))

    def test_missing_gpu_memory_does_not_attempt_large_gpu_allocation(self):
        for requested in ('auto', 'small', 'large-v3'):
            runtime = self.resolve(requested, memory=None)
            self.assertEqual(runtime['device'], 'cpu')
            self.assertIn('Chưa xác định', runtime['note'])

    def test_cpu_only_auto_does_not_query_gpu(self):
        ct = backend(count=0)
        with patch.object(asr_runtime, 'gpu_memory_mib', side_effect=AssertionError('CPU only')):
            runtime = asr_runtime.recognizer_runtime('auto', backend=ct)
        self.assertEqual((runtime['model'], runtime['device']), ('medium', 'cpu'))
        ct.get_supported_compute_types.assert_called_once_with('cpu')

    def test_float32_only_gpu_reserves_more_memory(self):
        runtime = self.resolve(memory=4096, ct=backend(('float32',)))
        self.assertEqual((runtime['model'], runtime['compute_type']), ('small', 'float32'))

    def test_incompatible_explicit_model_pin_rejected_before_hardware_query(self):
        ct = backend()
        with self.assertRaises(ValueError):
            self.resolve('small', ct=ct, resolved_model='large-v3')
        ct.get_cuda_device_count.assert_not_called()

    def test_unavailable_cpu_precision_is_reported(self):
        ct = backend(count=0)
        ct.get_supported_compute_types.side_effect = lambda _: set()
        with self.assertRaisesRegex(ValueError, 'CPU'):
            self.resolve(ct=ct)


class RecognitionRoutingTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.source = self.root/'source.mp4'
        self.source.write_bytes(b'checked source')
        self.output = self.root/'chinese.srt'
        engine = Mock()
        engine.transcribe.return_value = (iter([SimpleNamespace(start=0, end=1, text='你好')]),
                                         SimpleNamespace(duration=1))
        self.factory = Mock(return_value=engine)
        self.enterContext(patch.dict('sys.modules', {'ctranslate2': backend(),
            'faster_whisper': SimpleNamespace(WhisperModel=self.factory)}))
        self.enterContext(patch.object(asr, 'configure_models', return_value=self.root))
        self.enterContext(patch('teodub.models.configure_models', return_value=self.root))
        self.enterContext(patch.object(asr_runtime, 'gpu_memory_mib', return_value=2048))

    def test_new_asr_request_and_completed_cache_record_actual_1050_runtime(self):
        result = asr.transcribe(self.source, self.output)
        self.assertEqual(self.factory.call_args.args, ('small',))
        self.assertEqual(self.factory.call_args.kwargs['compute_type'], 'int8_float32')
        self.assertEqual(result['compute_type'], 'int8_float32')
        request = json.loads(self.output.with_suffix('.asr.request.json').read_text(encoding='utf-8'))
        self.assertEqual((request['requested_model'], request['model']), ('auto', 'small'))
        self.factory.reset_mock()
        self.assertEqual(asr.transcribe(self.source, self.output), result)
        self.factory.assert_not_called()

    def test_failed_legacy_request_preserves_model_and_request_hash(self):
        request = self.output.with_suffix('.asr.request.json')
        atomic_json(request, dict(source_sha256=hash_file(self.source), requested_model='auto', model='large-v3'))
        original = hash_file(request)
        messages = []
        result = asr.transcribe(self.source, self.output, progress=lambda text, **_: messages.append(text))
        self.assertEqual(hash_file(request), original)
        self.assertEqual((result['model'], result['device']), ('large-v3', 'cpu'))
        self.assertEqual(self.factory.call_args.args, ('large-v3',))
        self.assertTrue(any('giữ nguyên mô hình' in message for message in messages))

    def test_gpu_error_stops_once_without_retrying_another_model_or_cpu(self):
        self.factory.side_effect = RuntimeError('CUDA inference failure')
        with self.assertRaisesRegex(RuntimeError, 'CUDA inference failure'):
            asr.transcribe(self.source, self.output)
        self.factory.assert_called_once()
        self.assertFalse(self.output.exists())

    def test_voice_learning_uses_same_gpu_precision_and_preserves_explicit_model(self):
        _, device = voice_learning._recognizer('small', Mock())
        self.assertEqual(device, 'cuda')
        self.assertEqual(self.factory.call_args.kwargs['compute_type'], 'int8_float32')
        self.factory.reset_mock()
        messages = Mock()
        _, device = voice_learning._recognizer('large-v3', messages)
        self.assertEqual(device, 'cpu')
        self.assertEqual(self.factory.call_args.args, ('large-v3',))
        self.assertIn('giữ nguyên mô hình', messages.call_args.args[0])


if __name__ == '__main__':
    unittest.main()
