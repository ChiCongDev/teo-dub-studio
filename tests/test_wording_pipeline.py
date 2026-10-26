"""Adapted wording reaches speech and final captions with raw evidence intact."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import test_pipeline as fixtures
from teodub import codex_translation, pipeline, wording
from teodub.common import Cancelled, atomic_json, hash_file
from teodub.speech_plan import build_speech_plan
from teodub.srt import write_srt
from teodub.subtitles import read_srt


RULES = [dict(source='bán kính', replacement='phạm vi'), dict(source='bán', replacement='chào bán')]
WORDING = dict(version='vi-wording-v1', enabled=True, rules=RULES)
CUES = [dict(start_ms=0, end_ms=400, text='Bán kính hôm nay'),
        dict(start_ms=400, end_ms=800, text='là mười mét.'),
        dict(start_ms=1000, end_ms=1600, text='Bản vẽ này để bán.')]
ADAPTED = [dict(CUES[0], text='Phạm vi hôm nay'), CUES[1],
           dict(CUES[2], text='Bản vẽ này để chào bán.')]


class WordingPipelineTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.PipelineTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.f.cues = copy.deepcopy(CUES)
        self.f.config['wording'] = copy.deepcopy(WORDING)
        self.f.notes.side_effect = lambda translated, notes, final_subtitles=None, **kwargs: self.f.fake_notes(
            translated, notes, final_subtitles)

    def adapted_path(self):
        return Path(self.f.state()['wording']['path'])

    def test_adapted_words_reach_tts_and_final_srt_while_raw_captions_are_kept(self):
        f = self.f
        result = f.run_job()
        self.assertEqual(result['status'], 'completed', result)
        self.assertEqual(f.tts.call_args.args[0], ADAPTED)
        self.assertEqual(f.render.call_args.args[1], ADAPTED)
        self.assertEqual(read_srt(result['outputs']['subtitles']), ADAPTED)
        raw = f.directory/'captions/vietnamese.srt'
        self.assertEqual(read_srt(raw), CUES)
        self.assertEqual(f.state()['captions']['sha256'], hash_file(raw))
        adapted = self.adapted_path()
        self.assertNotEqual(adapted.resolve(), raw.resolve())
        self.assertEqual(read_srt(adapted), ADAPTED)
        self.assertEqual(f.state()['wording']['sha256'], hash_file(adapted))
        wording.verify_wording(adapted, raw, f.config)
        self.assertEqual(Path(f.notes.call_args.args[0]), raw)
        self.assertEqual(Path(f.notes.call_args.kwargs['wording_path']), adapted)

    def test_recognized_last_line_past_the_picture_still_ends_with_it_after_wording(self):
        f = self.f
        f.cues = CUES[:2]+[dict(CUES[2], end_ms=2600)]
        result = f.run_job()
        self.assertEqual(result['status'], 'completed', result)
        self.assertEqual(f.render.call_args.args[1], ADAPTED[:2]+[dict(ADAPTED[2], end_ms=2000)])
        self.assertEqual(read_srt(self.adapted_path())[-1]['end_ms'], 2600)

    def test_wording_precedes_phrase_grouping_and_covers_every_raw_cue(self):
        f = self.f
        result = f.run_job(reading_style='conversation')
        self.assertEqual(result['status'], 'completed', result)
        expected = build_speech_plan(ADAPTED, 'conversation')
        plan = json.loads((f.directory/'speech-plan.json').read_text(encoding='utf-8'))
        self.assertEqual(plan, expected)
        spoken = f.tts.call_args.args[0]
        self.assertEqual([c['text'] for c in spoken], [g['text'] for g in expected['groups']])
        self.assertEqual(read_srt(result['outputs']['subtitles']), spoken)
        self.assertEqual(f.notes.call_args.kwargs['source_groups'], expected['groups'])
        self.assertEqual(read_srt(f.directory/'captions/vietnamese.srt'), CUES)

    def test_missing_and_disabled_wording_keep_legacy_pipeline(self):
        f = self.f
        del f.config['wording']
        result = f.run_job()
        self.assertEqual(result['status'], 'completed', result)
        self.assertEqual(f.tts.call_args.args[0], CUES)
        self.assertEqual(read_srt(result['outputs']['subtitles']), CUES)
        self.assertNotIn('wording', f.state())
        self.assertEqual(list(f.directory.rglob('*.wording.json')), [])
        self.assertNotIn('wording_path', f.notes.call_args.kwargs)

    def test_disabled_table_does_not_adapt_raw_captions(self):
        f = self.f
        selected = dict(WORDING, enabled=False)
        result = f.run_job(wording=selected)
        self.assertEqual(result['status'], 'completed', result)
        self.assertEqual(f.tts.call_args.args[0], CUES)
        self.assertNotIn('wording', f.state())
        self.assertEqual(list(f.directory.rglob('*.wording.json')), [])

    def test_supplied_srt_is_copied_then_adapted_without_translation(self):
        f = self.f
        supplied = f.root/'phụ đề do người dùng cung cấp.srt'
        write_srt(supplied, CUES)
        before = supplied.read_bytes()
        result = f.run_job(subtitles=str(supplied))
        self.assertEqual(result['status'], 'completed', result)
        f.asr.assert_not_called()
        f.translate.assert_not_called()
        self.assertEqual(supplied.read_bytes(), before)
        self.assertEqual((f.directory/'captions/vietnamese.srt').read_bytes(), before)
        self.assertEqual(f.tts.call_args.args[0], ADAPTED)
        self.assertEqual(read_srt(result['outputs']['subtitles']), ADAPTED)

    def test_completed_resume_reuses_frozen_wording_without_engines_or_writes(self):
        f = self.f
        first = f.run_job()
        self.assertEqual(first['status'], 'completed', first)
        state = copy.deepcopy(f.state())
        adapted = self.adapted_path()
        evidence = {p: (p.read_bytes(), p.stat().st_mtime_ns)
                    for p in [f.directory/'captions/vietnamese.srt', adapted, *f.directory.rglob('*.wording.json')]}
        for engine in (f.asr, f.translate, f.tts, f.render):
            engine.reset_mock()
        resumed = f.run_job()
        self.assertEqual(resumed['status'], 'completed', resumed)
        self.assertEqual(resumed['outputs'], first['outputs'])
        self.assertEqual(f.state(), state)
        for engine in (f.asr, f.translate, f.tts, f.render):
            engine.assert_not_called()
        self.assertEqual({p: (p.read_bytes(), p.stat().st_mtime_ns) for p in evidence}, evidence)

    def test_cancelled_tts_resumes_same_adaptation_and_raw_snapshot(self):
        f = self.f
        f.tts.side_effect = Cancelled('synthetic pause before voice generation')
        first = f.run_job()
        self.assertEqual(first['status'], 'cancelled', first)
        snapshot = copy.deepcopy(f.state()['wording'])
        raw = (f.directory/'captions/vietnamese.srt').read_bytes()
        adapted = self.adapted_path().read_bytes()
        f.tts.side_effect = f.fake_tts
        resumed = f.run_job()
        self.assertEqual(resumed['status'], 'completed', resumed)
        self.assertEqual(f.state()['wording'], snapshot)
        self.assertEqual((f.directory/'captions/vietnamese.srt').read_bytes(), raw)
        self.assertEqual(self.adapted_path().read_bytes(), adapted)
        self.assertEqual(f.recognition_count, 1)
        self.assertEqual(f.translation_count, 1)

    def test_cancelled_adaptation_resumes_raw_captions_without_rerunning_engines(self):
        f = self.f
        real_rewrite = wording.rewrite_text
        def stop_after_rewrite(*args, **kwargs):
            result = real_rewrite(*args, **kwargs)
            Path(f.config['stop_file']).touch()
            return result
        with patch.object(wording, 'rewrite_text', side_effect=stop_after_rewrite):
            cancelled = f.run_job()
        self.assertEqual(cancelled['status'], 'cancelled', cancelled)
        f.tts.assert_not_called()
        f.render.assert_not_called()
        raw = (f.directory/'captions/vietnamese.srt').read_bytes()
        Path(f.config['stop_file']).unlink()
        result = f.run_job()
        self.assertEqual(result['status'], 'completed', result)
        self.assertEqual(f.recognition_count, 1)
        self.assertEqual(f.translation_count, 1)
        self.assertEqual((f.directory/'captions/vietnamese.srt').read_bytes(), raw)
        self.assertEqual(f.tts.call_args.args[0], ADAPTED)

    def test_changed_table_fails_before_existing_tts_or_video_can_be_reused(self):
        f = self.f
        first = f.run_job()
        self.assertEqual(first['status'], 'completed', first)
        before = self.adapted_path().read_bytes()
        f.tts.reset_mock()
        f.render.reset_mock()
        changed = copy.deepcopy(WORDING)
        changed['rules'][0]['replacement'] = 'cự ly'
        result = f.run_job(wording=changed)
        self.assertEqual(result['status'], 'failed', result)
        self.assertEqual(self.adapted_path().read_bytes(), before)
        f.tts.assert_not_called()
        f.render.assert_not_called()

    def test_invalid_table_fails_before_acquisition_or_translation(self):
        f = self.f
        selected = dict(WORDING, rules=[dict(source='bán', replacement='')])
        result = f.run_job(wording=selected)
        self.assertEqual(result['status'], 'failed', result)
        f.probe.assert_not_called()
        f.download.assert_not_called()
        f.translate.assert_not_called()
        f.tts.assert_not_called()

    def test_adapted_caption_edit_during_tts_prevents_render(self):
        f = self.f
        def changed_words(*args, **kwargs):
            clips = f.fake_tts(*args, **kwargs)
            adapted = self.adapted_path()
            adapted.write_text(adapted.read_text(encoding='utf-8').replace('Phạm vi', 'Lời bị sửa'), encoding='utf-8')
            return clips
        f.tts.side_effect = changed_words
        result = f.run_job()
        self.assertEqual(result['status'], 'failed', result)
        f.render.assert_not_called()
        self.assertEqual(read_srt(f.directory/'captions/vietnamese.srt'), CUES)

    def test_adapted_caption_change_during_parse_prevents_tts(self):
        f = self.f
        real_read = pipeline.read_vietnamese_srt
        def changed_read(path):
            cues = real_read(path)
            if Path(path).name == 'vietnamese-wording.srt':
                with Path(path).open('a', encoding='utf-8') as file:
                    file.write('\n')
            return cues
        with patch.object(pipeline, 'read_vietnamese_srt', side_effect=changed_read):
            result = f.run_job()
        self.assertEqual(result['status'], 'failed', result)
        f.tts.assert_not_called()
        f.render.assert_not_called()

    def test_wording_report_edit_during_tts_prevents_render(self):
        f = self.f
        def changed_report(*args, **kwargs):
            clips = f.fake_tts(*args, **kwargs)
            report_path, = f.directory.rglob('*.wording.json')
            report = json.loads(report_path.read_text(encoding='utf-8'))
            report['version'] = 'tampered-wording-version'
            atomic_json(report_path, report)
            return clips
        f.tts.side_effect = changed_report
        result = f.run_job()
        self.assertEqual(result['status'], 'failed', result)
        f.render.assert_not_called()

    def test_raw_caption_edit_during_tts_prevents_render(self):
        f = self.f
        def changed_raw(*args, **kwargs):
            clips = f.fake_tts(*args, **kwargs)
            raw = f.directory/'captions/vietnamese.srt'
            raw.write_text(raw.read_text(encoding='utf-8').replace('Bán kính', 'Bản dịch khác'), encoding='utf-8')
            return clips
        f.tts.side_effect = changed_raw
        result = f.run_job()
        self.assertEqual(result['status'], 'failed', result)
        f.render.assert_not_called()

    def test_completed_adaptation_tamper_is_rejected_without_repair(self):
        f = self.f
        first = f.run_job()
        self.assertEqual(first['status'], 'completed', first)
        adapted = self.adapted_path()
        adapted.write_text(adapted.read_text(encoding='utf-8').replace('Phạm vi', 'Lời bị sửa'), encoding='utf-8')
        before = adapted.read_bytes()
        f.tts.reset_mock()
        f.render.reset_mock()
        resumed = f.run_job()
        self.assertEqual(resumed['status'], 'failed', resumed)
        self.assertEqual(adapted.read_bytes(), before)
        f.tts.assert_not_called()
        f.render.assert_not_called()

    def test_completed_raw_caption_tamper_is_rejected_without_repair(self):
        f = self.f
        first = f.run_job()
        self.assertEqual(first['status'], 'completed', first)
        raw = f.directory/'captions/vietnamese.srt'
        raw.write_text(raw.read_text(encoding='utf-8').replace('Bán kính', 'Bản dịch khác'), encoding='utf-8')
        before = raw.read_bytes()
        f.tts.reset_mock()
        f.render.reset_mock()
        resumed = f.run_job()
        self.assertEqual(resumed['status'], 'failed', resumed)
        self.assertEqual(raw.read_bytes(), before)
        f.tts.assert_not_called()
        f.render.assert_not_called()

    def test_completed_report_digest_change_is_rejected_even_when_words_are_valid(self):
        f = self.f
        first = f.run_job()
        self.assertEqual(first['status'], 'completed', first)
        report_path = Path(f.state()['wording']['report'])
        report_path.write_text(report_path.read_text(encoding='utf-8')+'\n', encoding='utf-8')
        before = report_path.read_bytes()
        f.tts.reset_mock()
        f.render.reset_mock()
        resumed = f.run_job()
        self.assertEqual(resumed['status'], 'failed', resumed)
        self.assertEqual(report_path.read_bytes(), before)
        f.tts.assert_not_called()
        f.render.assert_not_called()

    def test_interrupted_report_digest_change_is_rejected_before_tts_resumption(self):
        f = self.f
        f.tts.side_effect = Cancelled('synthetic pause')
        first = f.run_job()
        self.assertEqual(first['status'], 'cancelled', first)
        saved_wording = copy.deepcopy(f.state()['wording'])
        report_path = Path(saved_wording['report'])
        report_path.write_text(report_path.read_text(encoding='utf-8')+'\n', encoding='utf-8')
        before = report_path.read_bytes()
        f.tts.reset_mock()
        f.tts.side_effect = f.fake_tts
        resumed = f.run_job()
        self.assertEqual(resumed['status'], 'failed', resumed)
        self.assertEqual(f.state()['wording'], saved_wording)
        self.assertEqual(report_path.read_bytes(), before)
        f.tts.assert_not_called()
        f.render.assert_not_called()


class WordingReviewNotesTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='teodub-wording-notes-test-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.raw = self.root/'raw.srt'
        self.zh = self.root/'zh.srt'
        self.adapted = self.root/'adapted.srt'
        self.final = self.root/'final.srt'
        self.notes = self.root/'notes.txt'
        write_srt(self.raw, CUES)
        write_srt(self.zh, [dict(c, text=f'中文原文{i}') for i, c in enumerate(CUES, 1)])
        self.config = dict(wording=copy.deepcopy(WORDING))
        wording.adapt_subtitles(self.raw, self.adapted, self.config)
        write_srt(self.final, [dict(c, start_ms=c['start_ms']+5000, end_ms=c['end_ms']+5000) for c in ADAPTED])
        report = dict(chinese=str(self.zh), cue_count=len(CUES),
                      review=[dict(id=1, reason='Kiểm tra lời số một'), dict(id=3, reason='Kiểm tra lời số ba')])
        self.verify = self.enterContext(patch.object(codex_translation, 'verify_translation', return_value=report))

    def test_review_notes_verify_adaptation_and_accept_changed_final_words(self):
        result = codex_translation.write_review_notes(self.raw, self.notes, self.final, wording_path=self.adapted)
        text = Path(result).read_text(encoding='utf-8-sig')
        self.assertIn('Câu 1 — Mốc video lồng tiếng: 00:00:05,000', text)
        self.assertIn('Câu 3 — Mốc video lồng tiếng: 00:00:06,000', text)
        self.assertIn('Bản dịch: Bán kính hôm nay', text)
        self.assertIn('Phạm vi hôm nay', text)
        self.assertEqual(read_srt(self.raw), CUES)

    def test_grouped_notes_use_adapted_text_with_original_review_ids(self):
        groups = build_speech_plan(ADAPTED, 'conversation')['groups']
        self.final.unlink()
        write_srt(self.final, [dict(start_ms=5000+i*3000, end_ms=7000+i*3000, text=g['text'])
                              for i, g in enumerate(groups)])
        codex_translation.write_review_notes(self.raw, self.notes, self.final,
                                              source_groups=groups, wording_path=self.adapted)
        text = self.notes.read_text(encoding='utf-8-sig')
        self.assertIn('Câu 1 — Mốc video lồng tiếng: 00:00:05,000', text)
        self.assertIn('Câu 3 — Mốc video lồng tiếng: 00:00:08,000', text)
        self.assertIn('Bản dịch: Bản vẽ này để bán.', text)

    def test_tampered_adaptation_blocks_notes_without_writing(self):
        self.adapted.write_text(self.adapted.read_text(encoding='utf-8').replace('Phạm vi', 'Lời khác'), encoding='utf-8')
        with self.assertRaises(ValueError):
            codex_translation.write_review_notes(self.raw, self.notes, self.final, wording_path=self.adapted)
        self.assertFalse(self.notes.exists())

    def test_raw_final_words_cannot_be_passed_off_as_adapted_words(self):
        self.final.unlink()
        write_srt(self.final, CUES)
        with self.assertRaises(ValueError):
            codex_translation.write_review_notes(self.raw, self.notes, self.final, wording_path=self.adapted)
        self.assertFalse(self.notes.exists())

    def test_another_raw_source_cannot_use_the_saved_adaptation(self):
        another = self.root/'raw-copy.srt'
        another.write_bytes(self.raw.read_bytes())
        with self.assertRaises(ValueError):
            codex_translation.write_review_notes(another, self.notes, self.final, wording_path=self.adapted)
        self.assertFalse(self.notes.exists())


if __name__ == '__main__':
    unittest.main()
