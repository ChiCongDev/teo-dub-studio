"""Resumption and publication contracts with all costly engines mocked."""
from contextlib import ExitStack, contextmanager
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from teodub import pipeline
from teodub.common import Cancelled, atomic_json, hash_file
from teodub.srt import write_srt


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='teodub-pipeline-test-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.source = self.root/'nguồn 钓鱼.mp4'
        self.source.write_bytes(b'synthetic source fixture, no decode needed')
        self.directory = self.root/'job-one'
        self.config = dict(directory=str(self.directory), stop_file=str(self.directory/'stop.request'),
                           input=str(self.source), output_dir=str(self.root/'Video lồng tiếng'),
                           original_dir=str(self.root/'Video gốc'), voice='Mai Anh', speed=.9,
                           original_gain=.1, mode='full', subtitles='')
        self.cues = [dict(start_ms=0, end_ms=400, text='Xin chào mọi người.'),
                     dict(start_ms=600, end_ms=1400, text='Đây là câu thứ hai.')]
        self.download_count = self.recognition_count = self.translation_count = 0
        self.has_audio = True
        stack = self.enterContext(ExitStack())
        self.probe = stack.enter_context(patch.object(pipeline, '_probe_source', side_effect=self.fake_probe))
        self.asr = stack.enter_context(patch('teodub.asr.transcribe', side_effect=self.fake_asr))
        self.translate = stack.enter_context(patch('teodub.codex_translation.translate_chinese_srt', side_effect=self.fake_translate))
        self.tts = stack.enter_context(patch('teodub.tts.synthesize', side_effect=self.fake_tts))
        self.render = stack.enter_context(patch('teodub.render.render_video', side_effect=self.fake_render))
        self.download = stack.enter_context(patch('teodub.download.download_video', side_effect=self.fake_download))
        self.notes = stack.enter_context(patch('teodub.codex_translation.write_review_notes', side_effect=self.fake_notes))
        stack.enter_context(patch('sys.stdout', new=io.StringIO()))

    def fake_probe(self, path):
        path = Path(path).resolve(strict=True)
        return dict(path=str(path), sha256=hash_file(path), duration_s=2, width=96, height=64,
                    has_audio=self.has_audio)

    def fake_asr(self, source, target, **kwargs):
        if not Path(target).exists():
            self.recognition_count += 1
            write_srt(target, [dict(cue, text='你好') for cue in self.cues])
        return dict(path=str(target))

    def fake_translate(self, source, target, **kwargs):
        if not Path(target).exists():
            self.translation_count += 1
            write_srt(target, self.cues)
        return str(target)

    def fake_tts(self, cues, voice, directory, **kwargs):
        directory = Path(directory)
        directory.mkdir(exist_ok=True, parents=True)
        clips = []
        for index, cue in enumerate(cues, 1):
            path = directory/f'{index}.wav'
            if not path.exists():
                path.write_bytes(cue['text'].encode('utf-8'))
            clips.append(dict(id=index, path=str(path), sha256=hash_file(path), duration_s=.6, sample_rate=48000))
        return clips

    def fake_render(self, source, cues, clips, destination, **kwargs):
        directory = Path(destination).resolve()
        directory.mkdir(parents=True, exist_ok=False)
        outputs = dict(video=str(directory/f'{directory.name}.mp4'),
                       subtitles=str(directory/f'{directory.name}.srt'),
                       plan=str(directory/'render-plan.json'), quality=str(directory/'quality.json'))
        Path(outputs['video']).write_bytes(b'completed validated mp4')
        write_srt(outputs['subtitles'], cues)
        atomic_json(outputs['plan'], dict(source=str(source), source_sha256=hash_file(source),
                                          cues=cues, clips=clips, requested_speed=kwargs['speed'],
                                          original_gain=kwargs['original_gain']))
        atomic_json(outputs['quality'], dict(technical_pass=True, source_sha256=hash_file(source),
                                             cue_count=len(cues), video_sha256=hash_file(outputs['video']),
                                             subtitles_sha256=hash_file(outputs['subtitles']),
                                             render_plan_sha256=hash_file(outputs['plan'])))
        return outputs

    def fake_download(self, text, original_dir, attempt, **kwargs):
        self.download_count += 1
        report = dict(status='downloaded', source_path=str(self.source), metadata=self.fake_probe(self.source))
        atomic_json(Path(attempt)/'download/result.json', report)
        return report

    def fake_notes(self, translated, notes, final_subtitles=None):
        Path(notes).write_text('Review these captions.', encoding='utf-8')

    def run_job(self, **overrides):
        return pipeline.run(dict(self.config, **overrides))

    def state(self):
        return json.loads((self.directory/'state.json').read_text(encoding='utf-8'))

    def prepare_published_but_uncheckpointed(self):
        with patch.object(pipeline.Job, 'record_outputs', side_effect=RuntimeError('crash before checkpoint')):
            result = self.run_job()
        self.assertEqual(result['status'], 'failed')
        self.assertNotIn('rendered', self.state())
        return Path(self.state()['render_directory'])

    def test_complete_local_pipeline_and_reuse_without_engines(self):
        first = self.run_job()
        self.assertEqual(first['status'], 'completed')
        self.assertEqual(first['cue_count'], 2)
        self.assertEqual(self.recognition_count, 1)
        self.assertEqual(self.translation_count, 1)
        self.assertFalse(self.translate.call_args.kwargs['resume_incomplete'])
        self.tts.assert_called_once()
        self.render.assert_called_once()
        frozen = copy.deepcopy(self.state())
        self.asr.reset_mock()
        self.translate.reset_mock()
        self.tts.reset_mock()
        self.render.reset_mock()
        second = self.run_job()
        self.assertEqual(second['outputs'], first['outputs'])
        self.assertEqual(second['status'], 'completed')
        self.assertEqual(second['cue_count'], 2)
        self.assertTrue(second['human_review_required'])
        self.assertEqual(self.state(), frozen)
        self.asr.assert_not_called()
        self.translate.assert_not_called()
        self.tts.assert_not_called()
        self.render.assert_not_called()
        self.assertEqual(json.loads((self.directory/'result.json').read_text(encoding='utf-8')), second)

    def test_invalid_reference_fails_before_acquiring_or_translating(self):
        result = self.run_job(voice='Giọng từ mẫu', voice_provider='local',
                              reference_audio=str(self.root/'missing.wav'), reference_sha256='0'*64)
        self.assertEqual(result['status'], 'failed')
        self.probe.assert_not_called()
        self.translate.assert_not_called()
        self.tts.assert_not_called()

    def test_selected_models_reach_all_three_engines_and_resume_is_frozen(self):
        chosen = dict(asr_model='small', translation_model='gpt-6-luna', translation_effort='low', tts_model='v3nano')
        result = self.run_job(**chosen)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(self.asr.call_args.kwargs['asr_model'], 'small')
        self.assertEqual(self.translate.call_args.kwargs['model'], 'gpt-6-luna')
        self.assertEqual(self.translate.call_args.kwargs['reasoning_effort'], 'low')
        self.assertEqual(self.tts.call_args.kwargs['tts_model'], 'v3nano')
        self.translate.reset_mock()
        changed = self.run_job(**dict(chosen, translation_model='gpt-6-astra'))
        self.assertEqual(changed['status'], 'failed')
        self.translate.assert_not_called()

    def test_selected_voice_speed_reaches_tts_and_is_frozen(self):
        result = self.run_job(voice_speed=1.2)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(self.tts.call_args.kwargs['voice_speed'], 1.2)
        self.tts.reset_mock()
        changed = self.run_job(voice_speed=1.1)
        self.assertEqual(changed['status'], 'failed')
        self.tts.assert_not_called()

    def test_selected_burned_subtitle_style_reaches_renderer_and_is_frozen(self):
        selected = dict(burn_subtitles=True, subtitle_color='black', subtitle_weight='regular')
        result = self.run_job(**selected)
        self.assertEqual(result['status'], 'completed')
        for key, value in selected.items():
            self.assertEqual(self.render.call_args.kwargs[key], value)
        self.render.reset_mock()
        changed = self.run_job(**dict(selected, subtitle_color='yellow'))
        self.assertEqual(changed['status'], 'failed')
        self.render.assert_not_called()

    def test_campaign_numbers_originals_and_outputs_without_changing_source(self):
        original = self.source.read_bytes()
        result = self.run_job(campaign_basename='Video 1_30-09-2026')
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(Path(result['outputs']['video']).name, 'Video 1_30-09-2026.mp4')
        saved = Path(self.config['original_dir'])/'Video 1_30-09-2026.mp4'
        self.assertEqual(saved.read_bytes(), original)
        self.assertEqual(self.source.read_bytes(), original)
        self.assertEqual(self.state()['source']['path'], str(saved.resolve()))
        self.asr.reset_mock()
        self.render.reset_mock()
        resumed = self.run_job(campaign_basename='Video 1_30-09-2026')
        self.assertEqual(resumed['status'], 'completed')
        self.asr.assert_not_called()
        self.render.assert_not_called()

    def test_reference_config_reaches_speech_engine(self):
        sample = self.root/'reference.wav'
        sample.write_bytes(b'Wave form decoded and validated by the speech engine')
        result = self.run_job(voice='Giọng từ mẫu', voice_provider='local',
                              reference_audio=str(sample), reference_sha256=hash_file(sample))
        self.assertEqual(result['status'], 'completed')
        options = self.tts.call_args.kwargs
        self.assertEqual(options['provider'], 'local')
        self.assertEqual(options['reference_audio'], str(sample))
        self.assertEqual(options['reference_sha256'], hash_file(sample))

    def test_provider_change_cannot_resume_an_old_voice_job(self):
        self.run_job()
        self.tts.reset_mock()
        result = self.run_job(voice_provider='capcut', voice='Cô Gái Hoạt Ngôn')
        self.assertEqual(result['status'], 'failed')
        self.tts.assert_not_called()

    def test_capcut_login_gate_happens_before_download_and_translation(self):
        with patch('teodub.capcut_tts.preflight', side_effect=ValueError('Cần đăng nhập CapCut')) as connection:
            result = self.run_job(voice_provider='capcut', voice='Cô Gái Hoạt Ngôn')
        self.assertEqual(result['status'], 'failed')
        connection.assert_called_once()
        self.probe.assert_not_called()
        self.download.assert_not_called()
        self.translate.assert_not_called()
        self.tts.assert_not_called()

    def test_completed_capcut_video_can_be_reused_without_connection(self):
        config = dict(voice_provider='capcut', voice='Cô Gái Hoạt Ngôn')
        with patch('teodub.capcut_tts.preflight') as connection:
            first = self.run_job(**config)
            connection.assert_called_once()
        self.tts.reset_mock()
        with patch('teodub.capcut_tts.preflight', side_effect=AssertionError('Must use completed result')):
            second = self.run_job(**config)
        self.assertEqual(first['status'], 'completed')
        self.assertEqual(second['outputs'], first['outputs'])
        self.tts.assert_not_called()

    def test_supplied_srt_skips_asr_codex_and_accepts_silent_video(self):
        supplied = self.root/'phụ đề Việt.srt'
        write_srt(supplied, self.cues)
        self.has_audio = False
        result = self.run_job(subtitles=str(supplied))
        self.assertEqual(result['status'], 'completed')
        self.asr.assert_not_called()
        self.translate.assert_not_called()
        self.notes.assert_not_called()
        self.assertEqual((self.directory/'captions/vietnamese.srt').read_bytes(), supplied.read_bytes())

    def test_source_without_audio_or_supplied_srt_fails_before_engines(self):
        self.has_audio = False
        result = self.run_job()
        self.assertEqual(result['status'], 'failed')
        self.assertIn('không có âm thanh', result['message'])
        self.asr.assert_not_called()
        self.translate.assert_not_called()
        self.tts.assert_not_called()
        self.render.assert_not_called()

    def test_cancelled_before_start_creates_no_expensive_work(self):
        self.directory.mkdir()
        Path(self.config['stop_file']).touch()
        result = self.run_job()
        self.assertEqual(result['status'], 'cancelled')
        self.probe.assert_not_called()
        self.tts.assert_not_called()
        self.render.assert_not_called()

    def test_cancelled_tts_resumes_same_snapshot_and_existing_captions(self):
        def cancel_after_partial(cues, voice, directory, **kwargs):
            self.fake_tts(cues[:1], voice, directory, **kwargs)
            Path(self.config['stop_file']).touch()
            raise Cancelled('Dừng giữa câu.')
        self.tts.side_effect = cancel_after_partial
        first = self.run_job()
        self.assertEqual(first['status'], 'cancelled')
        self.assertTrue((self.directory/'voice/1.wav').is_file())
        source = copy.deepcopy(self.state()['source'])
        captions = copy.deepcopy(self.state()['captions'])
        Path(self.config['stop_file']).unlink()
        self.tts.side_effect = self.fake_tts
        second = self.run_job()
        self.assertEqual(second['status'], 'completed')
        self.assertEqual(self.state()['source'], source)
        self.assertEqual(self.state()['captions'], captions)
        self.assertEqual(self.recognition_count, 1)
        self.assertEqual(self.translation_count, 1)
        self.render.assert_called_once()
        self.assertTrue(self.translate.call_args.kwargs['resume_incomplete'])

    def test_notes_failure_does_not_hide_completed_video_and_resume_retries_notes(self):
        self.notes.side_effect = OSError('notes temporarily unavailable')
        first = self.run_job()
        self.assertEqual(first['status'], 'completed')
        notes = Path(first['outputs']['video']).parent/'Ghi-chu-ban-dich.txt'
        self.assertFalse(notes.exists())
        self.notes.side_effect = self.fake_notes
        self.render.reset_mock()
        second = self.run_job()
        self.assertEqual(second['status'], 'completed')
        self.assertEqual(first['outputs'], second['outputs'])
        self.assertTrue(notes.is_file())
        self.render.assert_not_called()

    def test_changed_config_never_reuses_or_restarts_stages(self):
        first = self.run_job()
        outputs = first['outputs'].copy()
        self.tts.reset_mock()
        self.render.reset_mock()
        for field, value in [('voice', 'Trúc Ly'), ('speed', 1.0), ('input', 'another source'),
                             ('output_dir', str(self.root/'another output'))]:
            with self.subTest(field=field):
                result = self.run_job(**{field: value})
                self.assertEqual(result['status'], 'failed')
                self.assertIn('Cấu hình phiên đã thay đổi', result['message'])
                self.assertEqual(self.state()['rendered']['outputs'], outputs)
        self.tts.assert_not_called()
        self.render.assert_not_called()

    def test_changed_source_on_resume_is_rejected(self):
        self.run_job()
        self.source.write_bytes(b'replaced source')
        self.render.reset_mock()
        result = self.run_job()
        self.assertEqual(result['status'], 'failed')
        self.assertIn('nguồn của phiên đã bị thay đổi', result['message'])
        self.render.assert_not_called()

    def test_modified_completed_video_is_not_reported_success(self):
        first = self.run_job()
        Path(first['outputs']['video']).write_bytes(b'tampered result')
        self.render.reset_mock()
        result = self.run_job()
        self.assertEqual(result['status'], 'failed')
        self.assertIn('Thành phẩm đã thay đổi', result['message'])
        self.render.assert_not_called()

    def test_published_render_is_recovered_after_checkpoint_crash(self):
        directory = self.prepare_published_but_uncheckpointed()
        self.asr.reset_mock()
        self.tts.reset_mock()
        self.render.reset_mock()
        result = self.run_job()
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(Path(result['outputs']['video']).parent, directory)
        self.assertIn('rendered', self.state())
        self.asr.assert_not_called()
        self.tts.assert_not_called()
        self.render.assert_not_called()

    def test_recovery_does_not_approve_tampered_render_plan(self):
        directory = self.prepare_published_but_uncheckpointed()
        (directory/'render-plan.json').write_text('{}', encoding='utf-8')
        self.render.reset_mock()
        result = self.run_job()
        if result['status'] == 'completed':
            self.assertNotEqual(Path(result['outputs']['video']).parent, directory)
            self.render.assert_called_once()
        else:
            self.assertEqual(result['status'], 'failed')

    def test_missing_video_after_crash_renders_into_new_directory(self):
        directory = self.prepare_published_but_uncheckpointed()
        next(directory.glob('*.mp4')).unlink()
        self.render.reset_mock()
        result = self.run_job()
        self.assertEqual(result['status'], 'completed')
        self.assertNotEqual(Path(result['outputs']['video']).parent, directory)
        self.assertTrue(directory.is_dir())
        self.render.assert_called_once()
        self.assertEqual(self.recognition_count, 1)
        self.assertEqual(self.translation_count, 1)

    def test_completed_download_recovery_does_not_redownload(self):
        self.config['input'] = '抖音 https://v.douyin.com/Example123/'
        report = dict(status='downloaded', source_path=str(self.source), metadata=self.fake_probe(self.source))
        atomic_json(self.directory/'acquisition-001/download/result.json', report)
        result = self.run_job()
        self.assertEqual(result['status'], 'completed')
        self.download.assert_not_called()

    def test_changed_recovered_download_is_rejected(self):
        self.config['input'] = 'https://v.douyin.com/Example123/'
        report = dict(status='downloaded', source_path=str(self.source), metadata=self.fake_probe(self.source))
        atomic_json(self.directory/'acquisition-001/download/result.json', report)
        self.source.write_bytes(b'download changed')
        result = self.run_job()
        self.assertEqual(result['status'], 'failed')
        self.download.assert_not_called()
        self.asr.assert_not_called()

    def test_missing_tts_clip_prevents_render(self):
        self.tts.side_effect = lambda *args, **kwargs: []
        result = self.run_job()
        self.assertEqual(result['status'], 'failed')
        self.assertIn('đủ lời đọc', result['message'])
        self.render.assert_not_called()

    def test_caption_edit_while_tts_runs_prevents_render(self):
        def edit_caption(*args, **kwargs):
            clips = self.fake_tts(*args, **kwargs)
            target = self.directory/'captions/vietnamese.srt'
            target.write_text('1\n00:00:00,000 --> 00:00:00,400\nMột nội dung khác.\n', encoding='utf-8')
            return clips
        self.tts.side_effect = edit_caption
        result = self.run_job()
        self.assertEqual(result['status'], 'failed')
        self.render.assert_not_called()

    def test_auto_caption_change_during_parse_prevents_tts(self):
        real_read = pipeline.read_vietnamese_srt
        def changed_read(path):
            cues = real_read(path)
            with Path(path).open('a', encoding='utf-8') as file:
                file.write('\n')
            return cues
        with patch.object(pipeline, 'read_vietnamese_srt', side_effect=changed_read):
            result = self.run_job()
        self.assertEqual(result['status'], 'failed')
        self.tts.assert_not_called()
        self.render.assert_not_called()

    def test_saved_caption_digest_belongs_to_the_parsed_snapshot(self):
        target = self.directory/'captions/vietnamese.srt'
        reads = 0
        real_hash = pipeline.hash_file
        def change_later(path):
            nonlocal reads
            if Path(path) == target:
                reads += 1
                if reads == 3:
                    with target.open('a', encoding='utf-8') as file:
                        file.write('\n')
            return real_hash(path)
        with patch.object(pipeline, 'hash_file', side_effect=change_later):
            result = self.run_job()
        self.assertEqual(result['status'], 'failed')
        self.render.assert_not_called()

    def test_render_quality_must_match_recognized_source(self):
        def wrong_source(*args, **kwargs):
            outputs = self.fake_render(*args, **kwargs)
            path = Path(outputs['quality'])
            quality = json.loads(path.read_text(encoding='utf-8'))
            quality['source_sha256'] = '0'*64
            atomic_json(path, quality)
            return outputs
        self.render.side_effect = wrong_source
        result = self.run_job()
        self.assertEqual(result['status'], 'failed')
        self.assertNotIn('rendered', self.state())

    def test_recovery_never_accepts_quality_marked_failed(self):
        directory = self.prepare_published_but_uncheckpointed()
        path = directory/'quality.json'
        quality = json.loads(path.read_text(encoding='utf-8'))
        quality['technical_pass'] = False
        atomic_json(path, quality)
        self.render.reset_mock()
        result = self.run_job()
        if result['status'] == 'completed':
            self.assertNotEqual(Path(result['outputs']['video']).parent, directory)
            self.render.assert_called_once()
        else:
            self.assertEqual(result['status'], 'failed')

    def test_source_edit_while_tts_runs_prevents_render(self):
        def edit_source(*args, **kwargs):
            clips = self.fake_tts(*args, **kwargs)
            self.source.write_bytes(b'changed while synthesizing')
            return clips
        self.tts.side_effect = edit_source
        result = self.run_job()
        self.assertEqual(result['status'], 'failed')
        self.render.assert_not_called()

    def test_lock_conflict_preserves_other_workers_result(self):
        self.directory.mkdir()
        atomic_json(self.directory/'result.json', dict(status='running', message='Other worker'))
        with pipeline._job_lock(self.directory):
            try:
                result = self.run_job()
            except ValueError as exc:
                self.assertIn('tiến trình khác', str(exc))
            else:
                self.assertNotEqual(result['status'], 'completed')
        self.assertEqual(json.loads((self.directory/'result.json').read_text(encoding='utf-8')),
                         dict(status='running', message='Other worker'))
        self.probe.assert_not_called()

    def test_state_is_reloaded_after_lock_acquisition(self):
        first = self.run_job()
        completed = self.state()
        atomic_json(self.directory/'state.json', {})
        real_lock = pipeline._job_lock
        @contextmanager
        def updated_while_acquiring(directory):
            with real_lock(directory):
                atomic_json(directory/'state.json', completed)
                yield
        self.render.reset_mock()
        self.tts.reset_mock()
        with patch.object(pipeline, '_job_lock', side_effect=updated_while_acquiring):
            second = self.run_job()
        self.assertEqual(second['status'], 'completed')
        self.assertEqual(second['outputs'], first['outputs'])
        self.tts.assert_not_called()
        self.render.assert_not_called()


if __name__ == '__main__':
    unittest.main()
