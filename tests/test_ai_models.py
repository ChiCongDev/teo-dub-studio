"""Model routing, cache identity and rate conversion; no network or AI usage."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np
import soundfile as sf
from teodub import ai_models, asr, tts, codex_translation as tr
from teodub.srt import write_srt


class ModelTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.cues = [dict(start_ms=0, end_ms=1000, text='Xin chào bạn.')]

    def test_missing_fields_keep_legacy_and_new_defaults_are_explicit(self):
        self.assertEqual(ai_models.validate_models({}), ai_models.LEGACY_MODELS)
        self.assertEqual(ai_models.NEW_MODELS['translation_model'], 'gpt-6-luna')
        self.assertEqual(ai_models.NEW_MODELS['translation_effort'], 'low')
        for key in ai_models.CHOICES:
            for invalid in (None, [], '', '--model=other'):
                with self.subTest(key=key, value=invalid), self.assertRaises(ValueError):
                    ai_models.validate_models({key: invalid})

    def test_tts_factory_uses_distinct_models_and_does_not_reuse_wrong_engine(self):
        factory = Mock(side_effect=[object(), object()])
        with patch.dict('sys.modules', {'vieneu': SimpleNamespace(Vieneu=factory)}), \
             patch.object(tts, 'configure_models'), patch.object(tts, '_model', None), patch.object(tts, '_model_name', None):
            first = tts.get_model(tts_model='v3nano')
            self.assertIs(tts.get_model(tts_model='v3nano'), first)
            tts.get_model()
        self.assertEqual(factory.call_args_list[0].kwargs, dict(mode='v3nano', threads=4, steps=16, cfg=3.0))
        self.assertEqual(factory.call_args_list[1].kwargs['mode'], 'v3turbo')

    def test_nano_resampling_preserves_pitch_duration_and_has_own_cache(self):
        wave = (.2*np.sin(2*np.pi*440*np.arange(24000)/24000)).astype('float32')
        engine = Mock()
        engine.infer.return_value = wave
        directory = self.root/'voice'
        with patch.object(tts, 'get_model', return_value=engine) as load:
            clips = tts.synthesize(self.cues, 'Mai Anh', directory, tts_model='v3nano')
        load.assert_called_once_with(None, tts_model='v3nano')
        samples, rate = sf.read(clips[0]['path'])
        self.assertEqual(rate, 48000)
        self.assertAlmostEqual(len(samples)/rate, 1.0, places=4)
        self.assertAlmostEqual(np.argmax(abs(np.fft.rfft(samples)))*rate/len(samples), 440, delta=1)
        self.assertIn('Nano', clips[0]['model'])
        with patch.object(tts, 'get_model') as load:
            self.assertEqual(tts.synthesize(self.cues, 'Mai Anh', directory, tts_model='v3nano'), clips)
            with self.assertRaises(ValueError):
                tts.synthesize(self.cues, 'Mai Anh', directory, tts_model='v3turbo')
            load.assert_not_called()

    def test_nano_presets_reject_unsupported_voice_before_model_load(self):
        with patch.object(tts, 'get_model') as load:
            with self.assertRaises(ValueError):
                tts.synthesize(self.cues, 'Thùy Dung', self.root/'voice', tts_model='v3nano')
            load.assert_not_called()
        tts.validate_selection('Minh Quân', tts_model='v3nano')

    def test_explicit_asr_model_reaches_engine_and_cache_refuses_change(self):
        source = self.root/'source.mp4'
        source.write_bytes(b'source')
        for name in ('small', 'medium', 'large-v3'):
            with self.subTest(model=name):
                output = self.root/(name+'.srt')
                engine = Mock()
                engine.transcribe.return_value = (iter([SimpleNamespace(start=0,end=1,text='你好')]), SimpleNamespace(duration=1))
                factory = Mock(return_value=engine)
                with patch.dict('sys.modules', {'ctranslate2': SimpleNamespace(get_cuda_device_count=lambda:0,
                                               get_supported_compute_types=lambda device: {'int8', 'float32'}),
                                               'faster_whisper': SimpleNamespace(WhisperModel=factory)}), \
                     patch.object(asr, 'configure_models', return_value=self.root):
                    result = asr.transcribe(source, output, asr_model=name)
                self.assertEqual(factory.call_args.args, (name,))
                self.assertEqual(result['requested_model'], name)
                self.assertEqual(asr.transcribe(source, output, asr_model=name), result)
                with self.assertRaises(ValueError):
                    asr.transcribe(source, output, asr_model='auto')

    def test_asr_incomplete_auto_pins_resolved_model_across_hardware_changes(self):
        source = self.root/'source.mp4'; source.write_bytes(b'source')
        output = self.root/'out.srt'
        engine = Mock(); engine.transcribe.side_effect = RuntimeError('interrupted')
        factory = Mock(return_value=engine)
        devices = Mock(side_effect=[1,0])
        with patch.dict('sys.modules', {'ctranslate2': SimpleNamespace(get_cuda_device_count=devices,
                                       get_supported_compute_types=lambda device: {'int8', 'int8_float16', 'float32'}),
                                       'faster_whisper': SimpleNamespace(WhisperModel=factory)}), \
             patch('teodub.asr_runtime.gpu_memory_mib', return_value=12288), \
             patch.object(asr, 'configure_models', return_value=self.root):
            for _ in range(2):
                with self.assertRaises(RuntimeError): asr.transcribe(source, output)
        self.assertEqual([call.args[0] for call in factory.call_args_list], ['large-v3','large-v3'])

    def test_cli_selection_is_passed_as_arguments_with_legacy_compatibility(self):
        legacy = tr._command('codex', 'schema', 'out', self.root)
        self.assertNotIn('--model', legacy)
        for name in ('gpt-6-luna','gpt-6.1-sol','gpt-6-sol','gpt-6-astra',
                     'gpt-5.6-luna','gpt-5.6-terra','gpt-5.6-sol'):
            command = tr._command('codex', 'schema', 'out', self.root, model=name, reasoning_effort='low')
            self.assertEqual(command[command.index('--model')+1], name)
            self.assertIn('model_reasoning_effort="low"', command)
            self.assertIn('--ignore-user-config', command)
        with self.assertRaises(ValueError): tr._command('codex', 'schema', 'out', self.root, model='unknown')

    def test_translation_completion_and_partial_cache_bind_model_and_effort(self):
        source = self.root/'zh.srt'; write_srt(source, [dict(start_ms=0,end_ms=1000,text='你好')])
        out = self.root/'vi.srt'
        response = {'translations':[{'id':1,'text':'Xin chào.'}],'review':[]}
        with patch.object(tr, '_invoke_codex', return_value=(response, {'model':'unknown'})) as invoke:
            result = tr.translate_chinese_srt(source,out,model='gpt-6-luna',reasoning_effort='low')
        self.assertEqual(invoke.call_args.kwargs['model'], 'gpt-6-luna')
        self.assertEqual(result['selection'], {'model':'gpt-6-luna','reasoning_effort':'low'})
        with patch.object(tr, '_invoke_codex') as invoke:
            for kwargs in ({'model':'gpt-6-sol','reasoning_effort':'low'}, {'model':'gpt-6-luna','reasoning_effort':'high'}, {}):
                with self.assertRaises(ValueError): tr.translate_chinese_srt(source,out,**kwargs)
            tr.translate_chinese_srt(source,out,model='gpt-6-luna',reasoning_effort='low')
            invoke.assert_not_called()
        out = self.root/'partial.srt'
        with patch.object(tr, '_invoke_codex', side_effect=RuntimeError('interrupted')):
            with self.assertRaises(RuntimeError): tr.translate_chinese_srt(source,out,model='gpt-6-luna',reasoning_effort='low')
        with patch.object(tr, '_invoke_codex') as invoke:
            with self.assertRaises(ValueError): tr.translate_chinese_srt(source,out,model='gpt-6-astra',reasoning_effort='low',resume_incomplete=True)
            invoke.assert_not_called()

    def test_sol_61_reaches_translator_and_completed_cache_without_changing_defaults(self):
        source = self.root/'sol-source.srt'
        write_srt(source, [dict(start_ms=0, end_ms=1000, text='可能是接口松了。')])
        output = self.root/'sol-vietnamese.srt'
        response = {'translations': [{'id': 1, 'text': 'Có thể đầu nối bị lỏng.'}], 'review': []}
        with patch.object(tr, '_invoke_codex', return_value=(response, {})) as invoke:
            result = tr.translate_chinese_srt(source, output, model='gpt-6.1-sol', reasoning_effort='medium')
        self.assertEqual(invoke.call_args.kwargs['model'], 'gpt-6.1-sol')
        self.assertEqual(invoke.call_args.kwargs['reasoning_effort'], 'medium')
        self.assertEqual(result['selection'], {'model': 'gpt-6.1-sol', 'reasoning_effort': 'medium'})
        with patch.object(tr, '_invoke_codex') as invoke:
            tr.translate_chinese_srt(source, output, model='gpt-6.1-sol', reasoning_effort='medium')
            with self.assertRaises(ValueError):
                tr.translate_chinese_srt(source, output, model='gpt-6-sol', reasoning_effort='medium')
            invoke.assert_not_called()
        self.assertEqual(ai_models.NEW_MODELS['translation_model'], 'gpt-6-luna')

    def test_cli_metadata_distinguishes_requested_model_from_observed(self):
        for observed in (None, 'gpt-6-luna', 'gpt-6-astra'):
            def run(command, directory, input_path, env, **kwargs):
                if command[1:3] == ['login','status']: return 0,'Logged in using ChatGPT',''
                Path(command[command.index('--output-last-message')+1]).write_text('{"translations":[],"review":[]}',encoding='utf-8')
                event = dict(type='turn.completed',usage={'input_tokens':42})
                if observed: event['model'] = observed
                return 0,json.dumps(event),''
            with self.subTest(observed=observed), patch.object(tr,'find_codex',return_value='codex'),patch.object(tr,'_run_process',side_effect=run):
                if observed == 'gpt-6-astra':
                    with self.assertRaises(ValueError): tr._invoke_codex('test',{},model='gpt-6-luna',reasoning_effort='low')
                else:
                    _,metadata=tr._invoke_codex('test',{},model='gpt-6-luna',reasoning_effort='low')
                    self.assertEqual(metadata['requested_model'],'gpt-6-luna')
                    self.assertEqual(metadata['observed_model'],observed)


if __name__ == '__main__': unittest.main()
