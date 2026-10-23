"""One follow-up request for a few unusable translated lines (owner-approved 22/10); fake CLI only, no quota.

21/10 video 11: speech recognition wrote line 457 as "硬移反滑排滑" (likely 印尼反华排华), Codex could not
translate it and the whole video stopped before the voice step.
"""
import json
from pathlib import Path
import shutil
import tempfile
import threading
import unittest
from unittest.mock import patch

from teodub import codex_translation as translation
from teodub.srt import write_srt
from teodub.subtitles import read_srt, read_vietnamese_srt

DATA = '\nTRANSCRIPT_DATA_JSON:\n'
META = {'authentication': 'ChatGPT', 'model': 'mock', 'usage': {'input_tokens': 10}}
TEXTS = ['我来之前一直听说', '硬移反滑排滑', '历史上确实是这样的', '但是现在至少我来这十几天了', '都挺友好的']


def payload(prompt):
    return json.loads(prompt.split(DATA, 1)[1])


def instruction(prompt):
    return prompt.split(DATA, 1)[0]


class TranslationRepairTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name)
        self.source, self.output = self.path/'chinese.srt', self.path/'vietnamese.srt'
        self.cues = [dict(start_ms=i*2000, end_ms=i*2000+1700, text=text) for i, text in enumerate(TEXTS)]
        write_srt(self.source, self.cues)
        self.job = self.output.with_suffix('.codex-work')
        self.prompts = []

    def translate(self, **kwargs):
        return translation.translate_chinese_srt(self.source, self.output, **kwargs)

    def codex(self, *replies):
        """Each reply: {'texts': {id: text}, 'review': [...]}; any request beyond them fails the test."""
        replies = list(replies)

        def answer(prompt, schema, **kwargs):
            self.prompts.append(prompt)
            if not replies:
                raise AssertionError('unexpected extra Codex request')
            reply = replies.pop(0)
            if isinstance(reply, Exception):
                raise reply
            rows = [{'id': i, 'text': reply.get('texts', {}).get(i, f'Câu dịch số {i}.')}
                    for i in payload(prompt)['translate_only_ids']]
            return {'translations': rows, 'review': reply.get('review', [])}, dict(META, call=len(self.prompts))
        return patch.object(translation, '_invoke_codex', side_effect=answer)

    def texts(self):
        return [cue['text'] for cue in read_vietnamese_srt(self.output)]

    def test_one_chinese_line_is_asked_again_once_and_the_rest_is_kept(self):
        first = {'texts': {1: 'Trước khi đến tôi luôn nghe nói', 2: '硬移反滑排滑'},
                 'review': [{'id': 2, 'reason': 'Không rõ nghĩa.'}, {'id': 5, 'reason': 'Ghi chú cũ.'}]}
        follow_up = {'texts': {2: 'Indonesia bài Hoa, xua đuổi người Hoa.'},
                     'review': [{'id': 2, 'reason': 'Có thể người nói nói "Indonesia bài Hoa".'}]}
        with self.codex(first, follow_up) as invoke:
            result = self.translate()
        self.assertEqual(invoke.call_count, 2)
        self.assertEqual(instruction(self.prompts[0]), translation.PROMPT_INSTRUCTIONS[translation.PROMPT_VERSION])
        self.assertNotIn('REPAIR REQUEST', self.prompts[0])
        self.assertEqual(instruction(self.prompts[1]),
                         translation.PROMPT_INSTRUCTIONS[translation.PROMPT_VERSION]+'\n'+translation.REPAIR_NOTE)
        self.assertEqual(payload(self.prompts[1])['translate_only_ids'], [2])
        self.assertEqual(payload(self.prompts[1])['previous_vietnamese_context'],
                         [{'id': 1, 'text': 'Trước khi đến tôi luôn nghe nói'}])
        self.assertEqual(payload(self.prompts[1])['entire_chinese_transcript'], payload(self.prompts[0])['entire_chinese_transcript'])
        self.assertEqual(self.texts(), ['Trước khi đến tôi luôn nghe nói', 'Indonesia bài Hoa, xua đuổi người Hoa.',
                                        'Câu dịch số 3.', 'Câu dịch số 4.', 'Câu dịch số 5.'])
        self.assertEqual([(c['start_ms'], c['end_ms']) for c in read_srt(self.output)],
                         [(c['start_ms'], c['end_ms']) for c in self.cues])
        notes = {note['id']: note['reason'] for note in result['review']}
        self.assertEqual(sorted(notes), [2, 5])
        self.assertEqual(notes[2], translation.REPAIR_REASON+' Có thể người nói nói "Indonesia bài Hoa".')
        self.assertEqual(notes[5], 'Ghi chú cũ.')
        self.assertEqual([call['call'] for call in result['calls']], [1, 2])
        record = json.loads((self.job/'batch-1.json').read_text(encoding='utf-8'))
        self.assertEqual(record['repair'], {'ids': [2], 'metadata': dict(META, call=2)})
        self.assertEqual(record['metadata'], dict(META, call=1))
        self.assertEqual(record['response']['translations'][1]['text'], 'Indonesia bài Hoa, xua đuổi người Hoa.')

    def test_the_repaired_line_is_listed_in_the_review_notes(self):
        with self.codex({'texts': {2: '硬移反滑排滑'}}, {}):
            self.translate()
        notes = self.path/'Ghi-chu-ban-dich.txt'
        translation.write_review_notes(self.output, notes)
        text = notes.read_text(encoding='utf-8-sig')
        self.assertIn('Câu 2', text)
        self.assertIn('Lời Trung: 硬移反滑排滑', text)
        self.assertIn(translation.REPAIR_REASON, text)

    def test_still_unusable_after_the_follow_up_stops_without_a_third_request(self):
        with self.codex({'texts': {2: '硬移反滑排滑'}}, {'texts': {2: '   '}}) as invoke:
            with self.assertRaisesRegex(ValueError, 'câu 2 rỗng.*Đã hỏi lại Codex một lần'):
                self.translate()
        self.assertEqual(invoke.call_count, 2)
        self.assertFalse(self.output.exists())
        self.assertFalse((self.job/'batch-1.json').exists())
        self.assertTrue((self.job/'batch-1.submitted.json').exists())
        with self.codex({}) as invoke:
            with self.assertRaisesRegex(ValueError, 'không tự gửi trùng'):
                self.translate()
            invoke.assert_not_called()
        with self.codex({}) as invoke:
            self.translate(resume_incomplete=True)
        self.assertEqual(invoke.call_count, 1)

    def test_a_failed_follow_up_request_stops_and_keeps_the_marker(self):
        with self.codex({'texts': {2: '硬移反滑排滑'}}, RuntimeError('mock Codex unavailable')) as invoke:
            with self.assertRaisesRegex(RuntimeError, 'unavailable'):
                self.translate()
        self.assertEqual(invoke.call_count, 2)
        self.assertFalse((self.job/'batch-1.json').exists())
        self.assertTrue((self.job/'batch-1.submitted.json').exists())

    def test_three_unusable_lines_are_asked_together_in_one_follow_up(self):
        with self.codex({'texts': {1: '', 3: '历史', 5: 'x'*1501}}, {}) as invoke:
            self.translate()
        self.assertEqual(invoke.call_count, 2)
        self.assertEqual(payload(self.prompts[1])['translate_only_ids'], [1, 3, 5])
        self.assertEqual(payload(self.prompts[1])['previous_vietnamese_context'], [])
        self.assertEqual(self.texts(), [f'Câu dịch số {i}.' for i in range(1, 6)])

    def test_more_than_three_unusable_lines_stop_as_before(self):
        with self.codex({'texts': {1: '', 2: '硬移', 3: '历史', 4: '但是'}}) as invoke:
            with self.assertRaisesRegex(ValueError, 'câu 1 rỗng') as raised:
                self.translate()
        self.assertEqual(invoke.call_count, 1)
        self.assertNotIn('hỏi lại', str(raised.exception))
        self.assertFalse((self.job/'batch-1.json').exists())

    def test_structural_errors_are_never_repaired(self):
        # A review note that does not fit is no longer structural (it is left out, see the notes test below).
        rows = [{'id': i, 'text': '硬移' if i == 2 else f'Câu {i}.'} for i in (2, 1, 3, 4, 5)]
        for response, message in (({'translations': rows, 'review': []}, 'sai thứ tự'),
                                  ({'translations': rows[:4], 'review': []}, 'thiếu hoặc thừa')):
            with self.subTest(message=message):
                shutil.rmtree(self.job, ignore_errors=True)
                with patch.object(translation, '_invoke_codex', return_value=(response, dict(META))) as invoke:
                    with self.assertRaisesRegex(ValueError, message):
                        self.translate()
                    invoke.assert_called_once()

    def test_cancel_during_the_follow_up_keeps_the_marker_and_saves_nothing(self):
        cancel = threading.Event()

        def answer(prompt, schema, **kwargs):
            self.prompts.append(prompt)
            if len(self.prompts) == 2:
                cancel.set()
            rows = [{'id': i, 'text': '硬移' if (i == 2 and len(self.prompts) == 1) else f'Câu {i}.'}
                    for i in payload(prompt)['translate_only_ids']]
            return {'translations': rows, 'review': []}, dict(META)
        with patch.object(translation, '_invoke_codex', side_effect=answer) as invoke:
            with self.assertRaisesRegex(ValueError, 'Đã dừng'):
                self.translate(cancel=cancel)
        self.assertEqual(invoke.call_count, 2)
        self.assertFalse((self.job/'batch-1.json').exists())
        self.assertTrue((self.job/'batch-1.submitted.json').exists())

    def interrupted_after_a_repaired_first_batch(self):
        with patch.object(translation, 'BATCH_CUES', 3):
            with self.codex({'texts': {2: '硬移反滑排滑'}}, {}, RuntimeError('mock interrupted batch two')) as invoke:
                with self.assertRaisesRegex(RuntimeError, 'batch two'):
                    self.translate()
            self.assertEqual(invoke.call_count, 3)
        self.assertTrue((self.job/'batch-1.json').is_file())
        self.assertTrue((self.job/'batch-2.submitted.json').is_file())

    def test_a_saved_repaired_batch_is_reused_without_asking_again(self):
        self.interrupted_after_a_repaired_first_batch()
        cached = (self.job/'batch-1.json').read_bytes()
        self.prompts.clear()
        with patch.object(translation, 'BATCH_CUES', 3), self.codex({}) as invoke:
            result = self.translate(resume_incomplete=True)
        self.assertEqual(invoke.call_count, 1)
        self.assertEqual(payload(self.prompts[0])['translate_only_ids'], [4, 5])
        self.assertEqual((self.job/'batch-1.json').read_bytes(), cached)
        self.assertEqual([call['call'] for call in result['calls']], [1, 2, 1])
        self.assertEqual([note['id'] for note in result['review']], [2])

    def test_a_tampered_saved_repair_record_is_refused_before_any_request(self):
        self.interrupted_after_a_repaired_first_batch()
        path = self.job/'batch-1.json'
        for repair in ({'ids': [9], 'metadata': {}}, {'ids': [], 'metadata': {}}, {'ids': [2]}, 'repaired'):
            with self.subTest(repair=repair):
                record = json.loads(path.read_text(encoding='utf-8'))
                record['repair'] = repair
                path.write_text(json.dumps(record, ensure_ascii=False), encoding='utf-8')
                with patch.object(translation, 'BATCH_CUES', 3), \
                        patch.object(translation, '_invoke_codex') as invoke:
                    with self.assertRaisesRegex(ValueError, 'không khớp nguồn hoặc quy tắc dịch'):
                        self.translate(resume_incomplete=True)
                    invoke.assert_not_called()

    def test_review_notes_that_do_not_fit_the_batch_are_left_out_not_fatal(self):
        # 22/10 video 15: Codex added a note that did not match the batch and the whole video stopped.
        notes = [{'id': 9, 'reason': 'Câu ngoài nhóm.'}, {'id': 2, 'reason': 'Không rõ.'},
                 {'id': 2, 'reason': 'Ghi chú lặp.'}, {'id': 3, 'reason': '   '}, {'id': 4, 'reason': 'x'*2500}]
        messages = []
        with self.codex({'review': notes}) as invoke:
            result = self.translate(progress=messages.append)
        self.assertEqual(invoke.call_count, 1)
        self.assertEqual([(note['id'], note['reason']) for note in result['review']],
                         [(2, 'Không rõ.'), (4, 'x'*2000)])
        record = json.loads((self.job/'batch-1.json').read_text(encoding='utf-8'))
        self.assertEqual(record['notes_dropped'], 3)
        self.assertTrue(any('Bỏ 3 ghi chú' in message for message in messages))
        self.assertEqual(self.texts(), [f'Câu dịch số {i}.' for i in range(1, 6)])

    def test_a_follow_up_with_a_stray_note_still_repairs_the_line(self):
        with self.codex({'texts': {2: '硬移反滑排滑'}}, {'review': [{'id': 7, 'reason': 'Câu ngoài.'}]}) as invoke:
            result = self.translate()
        self.assertEqual(invoke.call_count, 2)
        self.assertEqual([note['id'] for note in result['review']], [2])

    def test_saved_results_keep_the_strict_note_check(self):
        with self.codex({}):
            self.translate()
        report_path = translation._report_path(self.output)
        report = json.loads(report_path.read_text(encoding='utf-8'))
        report['review'] = [{'id': 99, 'reason': 'Ghi chú sửa tay.'}]
        report_path.write_text(json.dumps(report, ensure_ascii=False), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'chứng cứ'):
            translation.verify_translation(self.output)

    def test_a_clean_answer_still_makes_exactly_one_request(self):
        with self.codex({}) as invoke:
            result = self.translate()
        self.assertEqual(invoke.call_count, 1)
        self.assertNotIn('repair', json.loads((self.job/'batch-1.json').read_text(encoding='utf-8')))
        self.assertEqual(result['review'], [])


if __name__ == '__main__':
    unittest.main()
