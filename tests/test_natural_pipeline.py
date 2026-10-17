"""Grouped speech keeps frozen source coverage, one SRT and useful review times."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import test_pipeline as fixtures
from teodub.common import Cancelled, atomic_json, hash_file
from teodub import codex_translation
from teodub.speech_plan import build_speech_plan, STYLES
from teodub.srt import write_srt
from teodub.subtitles import read_srt


FRAGMENTS = [dict(start_ms=0, end_ms=400, text='Dù ở Vân Nam'),
             dict(start_ms=400, end_ms=800, text='hay Trùng Khánh,'),
             dict(start_ms=800, end_ms=1200, text='trời mưa vẫn sáng.'),
             dict(start_ms=1200, end_ms=1600, text='Đặt hàng hôm nay.')]


class NaturalPipelineTests(unittest.TestCase):
    def setUp(self):
        # Reuse the engine-free fixture without rerunning its inherited tests.
        self.f = fixtures.PipelineTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.f.cues = copy.deepcopy(FRAGMENTS)
        self.f.config['reading_style'] = 'conversation'
        self.f.notes.side_effect = lambda translated, notes, final_subtitles=None, **kwargs: self.f.fake_notes(
            translated, notes, final_subtitles)

    def test_grouped_text_goes_to_tts_and_single_srt_preserving_original_coverage(self):
        f = self.f
        result = f.run_job()
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['cue_count'], 4)
        self.assertEqual(result['speech_group_count'], 2)
        self.assertTrue(result['human_review_required'])
        plan = json.loads((f.directory/'speech-plan.json').read_text(encoding='utf-8'))
        self.assertEqual(plan, build_speech_plan(f.cues, 'conversation'))
        self.assertEqual(f.state()['speech_plan']['sha256'], hash_file(f.directory/'speech-plan.json'))
        spoken = f.tts.call_args.args[0]
        self.assertEqual(len(spoken), 2)
        self.assertEqual(' '.join(c['text'] for c in spoken).split(), ' '.join(c['text'] for c in f.cues).split())
        self.assertNotIn('source_ids', spoken[0])
        self.assertEqual(f.tts.call_args.kwargs['reading_style'], 'conversation')
        self.assertEqual(f.render.call_args.args[1], spoken)
        self.assertEqual(f.render.call_args.kwargs['gap_ms'], STYLES['conversation']['gap_ms'])
        final = Path(result['outputs']['subtitles'])
        self.assertEqual(read_srt(final), spoken)
        self.assertEqual(len(list(final.parent.glob('*.srt'))), 1)
        self.assertEqual(f.notes.call_args.kwargs['source_groups'], plan['groups'])

    def test_story_settings_reach_tts_and_renderer_without_changing_words(self):
        f = self.f
        result = f.run_job(reading_style='story')
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(f.tts.call_args.kwargs['reading_style'], 'story')
        self.assertEqual(f.render.call_args.kwargs['gap_ms'], STYLES['story']['gap_ms'])
        self.assertEqual(f.render.call_args.kwargs['original_gain'], .1)
        self.assertEqual(f.tts.call_args.args[0][0]['text'], ' '.join(c['text'] for c in f.cues[:3]))

    def test_legacy_job_without_style_keeps_exact_old_cues_and_no_plan(self):
        f = self.f
        del f.config['reading_style']
        result = f.run_job()
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(f.tts.call_args.args[0], f.cues)
        self.assertEqual(f.tts.call_args.kwargs['reading_style'], 'legacy')
        self.assertEqual(f.render.call_args.kwargs['gap_ms'], 100)
        self.assertFalse((f.directory/'speech-plan.json').exists())
        self.assertNotIn('source_groups', f.notes.call_args.kwargs)

    def test_cancelled_grouped_tts_resumes_identical_plan_and_captions(self):
        f = self.f
        f.tts.side_effect = Cancelled('Stop before group completes.')
        self.assertEqual(f.run_job()['status'], 'cancelled')
        snapshot = (f.directory/'speech-plan.json').read_bytes()
        metadata = copy.deepcopy(f.state()['speech_plan'])
        f.tts.side_effect = f.fake_tts
        second = f.run_job()
        self.assertEqual(second['status'], 'completed')
        self.assertEqual((f.directory/'speech-plan.json').read_bytes(), snapshot)
        self.assertEqual(f.state()['speech_plan'], metadata)
        self.assertEqual(f.recognition_count, 1)
        self.assertEqual(f.translation_count, 1)

    def test_style_change_cannot_reuse_existing_job_or_voice_cache(self):
        f = self.f
        f.run_job()
        f.tts.reset_mock()
        f.render.reset_mock()
        result = f.run_job(reading_style='advertising')
        self.assertEqual(result['status'], 'failed')
        f.tts.assert_not_called()
        f.render.assert_not_called()

    def test_altered_plan_is_rejected_before_resuming_tts(self):
        f = self.f
        f.tts.side_effect = Cancelled('Stop')
        f.run_job()
        path = f.directory/'speech-plan.json'
        plan = json.loads(path.read_text(encoding='utf-8'))
        plan['groups'][0]['text'] = 'Nội dung bị sửa.'
        atomic_json(path, plan)
        f.tts.reset_mock()
        result = f.run_job()
        self.assertEqual(result['status'], 'failed')
        f.tts.assert_not_called()
        f.render.assert_not_called()

    def test_plan_changed_during_tts_never_reaches_renderer(self):
        f = self.f
        def change_plan(*args, **kwargs):
            clips = f.fake_tts(*args, **kwargs)
            path = f.directory/'speech-plan.json'
            plan = json.loads(path.read_text(encoding='utf-8'))
            plan['groups'][0]['source_ids'] = [1, 3]
            atomic_json(path, plan)
            return clips
        f.tts.side_effect = change_plan
        result = f.run_job()
        self.assertEqual(result['status'], 'failed')
        f.render.assert_not_called()

    def test_completed_resume_retains_group_count_without_synthesis(self):
        f = self.f
        first = f.run_job()
        f.tts.reset_mock()
        f.render.reset_mock()
        second = f.run_job()
        self.assertEqual(second['status'], 'completed')
        self.assertEqual(second['cue_count'], 4)
        self.assertEqual(second['speech_group_count'], 2)
        self.assertEqual(second['outputs'], first['outputs'])
        f.tts.assert_not_called()
        f.render.assert_not_called()


class GroupedReviewNotesTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='teodub-group-review-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.vi, self.zh = self.root/'vi.srt', self.root/'zh.srt'
        self.final, self.notes = self.root/'final.srt', self.root/'notes.txt'
        write_srt(self.vi, FRAGMENTS)
        write_srt(self.zh, [dict(c, text=f'中文原文{i}') for i, c in enumerate(FRAGMENTS, 1)])
        self.groups = build_speech_plan(FRAGMENTS)['groups']
        self.final_cues = [dict(start_ms=2500, end_ms=6200, text=self.groups[0]['text']),
                           dict(start_ms=6500, end_ms=8500, text=self.groups[1]['text'])]
        write_srt(self.final, self.final_cues)
        report = dict(chinese=str(self.zh), cue_count=4, review=[dict(id=i, reason=f'Kiểm tra số {i}') for i in (2, 3, 4)])
        self.verify = self.enterContext(patch.object(codex_translation, 'verify_translation', return_value=report))

    def test_original_review_ids_map_to_the_start_of_their_spoken_group(self):
        result = codex_translation.write_review_notes(self.vi, self.notes, self.final, source_groups=self.groups)
        text = Path(result).read_text(encoding='utf-8-sig')
        self.assertIn('Câu 2 — Mốc video lồng tiếng: 00:00:02,500', text)
        self.assertIn('Câu 3 — Mốc video lồng tiếng: 00:00:02,500', text)
        self.assertIn('Câu 4 — Mốc video lồng tiếng: 00:00:06,500', text)
        self.assertIn('Lời Trung: 中文原文2', text)
        self.assertIn('Bản dịch: hay Trùng Khánh,', text)
        self.assertIn('Bản dịch: trời mưa vẫn sáng.', text)
        self.assertEqual(len(list(self.root.glob('*.srt'))), 3)

    def test_missing_duplicate_reordered_or_boolean_source_ids_are_rejected(self):
        for ids in ([1, 2], [1, 2, 2], [1, 3, 2], [True, 2, 3]):
            groups = copy.deepcopy(self.groups)
            groups[0]['source_ids'] = ids
            with self.subTest(ids=ids), self.assertRaises(ValueError):
                codex_translation.write_review_notes(self.vi, self.notes, self.final, source_groups=groups)
            self.assertFalse(self.notes.exists())

    def test_changed_group_or_final_word_is_rejected(self):
        groups = copy.deepcopy(self.groups)
        groups[0]['text'] = 'Một lời bị sửa.'
        with self.assertRaises(ValueError):
            codex_translation.write_review_notes(self.vi, self.notes, self.final, source_groups=groups)
        self.assertFalse(self.notes.exists())
        self.final.unlink()
        write_srt(self.final, [dict(c, text='Sai lời.') for c in self.final_cues])
        with self.assertRaises(ValueError):
            codex_translation.write_review_notes(self.vi, self.notes, self.final, source_groups=self.groups)
        self.assertFalse(self.notes.exists())

    def test_legacy_ungrouped_notes_keep_one_to_one_mapping(self):
        self.final.unlink()
        final = [dict(c, start_ms=c['start_ms']+5000, end_ms=c['end_ms']+5000) for c in FRAGMENTS]
        write_srt(self.final, final)
        codex_translation.write_review_notes(self.vi, self.notes, self.final)
        text = self.notes.read_text(encoding='utf-8-sig')
        self.assertIn('Câu 2 — Mốc video lồng tiếng: 00:00:05,400', text)

    def test_existing_notes_are_not_overwritten(self):
        self.notes.write_text('Keep this human note.', encoding='utf-8')
        with self.assertRaises(FileExistsError):
            codex_translation.write_review_notes(self.vi, self.notes, self.final, source_groups=self.groups)
        self.assertEqual(self.notes.read_text(encoding='utf-8'), 'Keep this human note.')


if __name__ == '__main__':
    unittest.main()
