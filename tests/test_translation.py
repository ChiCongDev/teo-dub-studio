"""Recovery invariants with a fake CLI; no Codex requests or quota are used."""
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from teodub import codex_translation as translation
from teodub.srt import write_srt
from teodub.subtitles import read_srt


def response_for(prompt, schema, **kwargs):
    payload = json.loads(prompt.split('\nTRANSCRIPT_DATA_JSON:\n', 1)[1])
    rows = [{'id': i, 'text': f'Câu dịch số {i}.'} for i in payload['translate_only_ids']]
    return {'translations': rows, 'review': []}, {'authentication': 'ChatGPT', 'model': 'mock', 'usage': {'input_tokens': 10}}


class TranslationRecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name)
        self.source = self.path/'中文.srt'
        self.output = self.path/'tiếng-việt.srt'
        self.cues = [dict(start_ms=i*2000, end_ms=i*2000+1700, text=f'这是第{i+1}句话。') for i in range(3)]
        write_srt(self.source, self.cues)
        self.source_bytes = self.source.read_bytes()
        self.job = self.output.with_suffix('.codex-work')
        self.report = translation._report_path(self.output)
        self.pending = translation._pending_report_path(self.output)
        self.enterContext(patch.object(translation, 'BATCH_CUES', 1))

    def translate(self, **kwargs):
        return translation.translate_chinese_srt(self.source, self.output, **kwargs)

    def fail_first_attempt(self):
        with patch.object(translation, '_invoke_codex', side_effect=ValueError('mock interrupted')) as invoke:
            with self.assertRaisesRegex(ValueError, 'mock interrupted'):
                self.translate()
            invoke.assert_called_once()
        return self.job/'batch-1.submitted.json'

    def publication_failure(self, *, before_srt=False):
        original_json, original_publish = translation._json, translation._publish
        def json_fault(path, value):
            if Path(path) == self.report:
                raise OSError('mock report publication failure')
            return original_json(path, value)
        def publish_fault(source, target):
            if Path(target) == self.output:
                raise OSError('mock SRT publication failure')
            return original_publish(source, target)
        target, fault = ('_publish', publish_fault) if before_srt else ('_json', json_fault)
        with patch.object(translation, '_invoke_codex', side_effect=response_for) as invoke, \
                patch.object(translation, target, side_effect=fault):
            with self.assertRaises(OSError):
                self.translate()
            self.assertEqual(invoke.call_count, 3)
        self.assertTrue(self.pending.exists())
        self.assertFalse(self.report.exists())
        self.assertEqual(self.output.exists(), not before_srt)

    def test_default_refuses_submitted_marker_without_changes_or_cli(self):
        marker = self.fail_first_attempt()
        before = marker.read_bytes()
        with patch.object(translation, '_invoke_codex') as invoke:
            with self.assertRaisesRegex(ValueError, 'không tự gửi trùng'):
                self.translate()
            invoke.assert_not_called()
        self.assertEqual(marker.read_bytes(), before)
        self.assertFalse((self.job/'attempt-history').exists())

    def test_explicit_continue_reuses_finished_batch_and_retries_only_unfinished_batch_once(self):
        def first(prompt, schema, **kwargs):
            payload = json.loads(prompt.split('\nTRANSCRIPT_DATA_JSON:\n', 1)[1])
            if payload['translate_only_ids'] == [2]:
                raise ValueError('mock interrupted batch two')
            return response_for(prompt, schema, **kwargs)
        with patch.object(translation, '_invoke_codex', side_effect=first) as invoke:
            with self.assertRaisesRegex(ValueError, 'batch two'):
                self.translate()
            self.assertEqual(invoke.call_count, 2)
        cached = (self.job/'batch-1.json').read_bytes()
        marker = (self.job/'batch-2.submitted.json').read_bytes()
        with patch.object(translation, '_invoke_codex', side_effect=response_for) as invoke:
            result = self.translate(resume_incomplete=True)
        ids = [json.loads(call.args[0].split('\nTRANSCRIPT_DATA_JSON:\n', 1)[1])['translate_only_ids']
               for call in invoke.call_args_list]
        self.assertEqual(ids, [[2], [3]])
        self.assertEqual((self.job/'batch-1.json').read_bytes(), cached)
        history = list((self.job/'attempt-history').glob('*.submitted.json'))
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].read_bytes(), marker)
        self.assertNotEqual((self.job/'batch-2.submitted.json').read_bytes(), marker)
        self.assertEqual(result['cue_count'], 3)
        self.assertEqual(self.source.read_bytes(), self.source_bytes)
        self.assertEqual([(c['start_ms'], c['end_ms']) for c in read_srt(self.output)],
                         [(c['start_ms'], c['end_ms']) for c in self.cues])

    def test_each_explicit_continue_has_one_attempt_and_unique_preserved_history(self):
        marker = self.fail_first_attempt()
        expected = [marker.read_bytes()]
        for _ in range(2):
            with patch.object(translation, '_invoke_codex', side_effect=ValueError('mock still unavailable')) as invoke:
                with self.assertRaisesRegex(ValueError, 'still unavailable'):
                    self.translate(resume_incomplete=True)
                invoke.assert_called_once()
            history = list((self.job/'attempt-history').glob('*.submitted.json'))
            self.assertEqual(sorted(p.read_bytes() for p in history), sorted(expected))
            expected.append(marker.read_bytes())
        self.assertFalse((self.job/'batch-1.json').exists())

    def test_cancelled_continue_keeps_marker_and_never_invokes_cli(self):
        marker = self.fail_first_attempt()
        before = marker.read_bytes()
        cancel = threading.Event()
        cancel.set()
        with patch.object(translation, '_invoke_codex') as invoke:
            with self.assertRaisesRegex(ValueError, 'Đã dừng'):
                self.translate(resume_incomplete=True, cancel=cancel)
            invoke.assert_not_called()
        self.assertEqual(marker.read_bytes(), before)
        self.assertFalse((self.job/'attempt-history').exists())

    def test_cancel_after_response_does_not_complete_batch_or_retry_in_same_call(self):
        cancel = threading.Event()
        def cancelled_response(*args, **kwargs):
            result = response_for(*args, **kwargs)
            cancel.set()
            return result
        with patch.object(translation, '_invoke_codex', side_effect=cancelled_response) as invoke:
            with self.assertRaisesRegex(ValueError, 'Đã dừng'):
                self.translate(cancel=cancel)
            invoke.assert_called_once()
        self.assertTrue((self.job/'batch-1.submitted.json').exists())
        self.assertFalse((self.job/'batch-1.json').exists())

    def test_crash_gap_after_archive_still_refuses_default_and_allows_explicit_continue(self):
        marker = self.fail_first_attempt()
        old_bytes = marker.read_bytes()
        cancel = threading.Event()
        original = translation._publish
        def archive_then_stop(source, target):
            result = original(source, target)
            if Path(source) == marker:
                cancel.set()
            return result
        with patch.object(translation, '_publish', side_effect=archive_then_stop), \
                patch.object(translation, '_invoke_codex') as invoke:
            with self.assertRaisesRegex(ValueError, 'Đã dừng'):
                self.translate(resume_incomplete=True, cancel=cancel)
            invoke.assert_not_called()
        self.assertFalse(marker.exists())
        history = list((self.job/'attempt-history').glob('*.submitted.json'))
        self.assertEqual([p.read_bytes() for p in history], [old_bytes])
        with patch.object(translation, '_invoke_codex') as invoke:
            with self.assertRaisesRegex(ValueError, 'không tự gửi trùng'):
                self.translate()
            invoke.assert_not_called()
        with patch.object(translation, '_invoke_codex', side_effect=response_for) as invoke:
            self.translate(resume_incomplete=True)
            self.assertEqual(invoke.call_count, 3)
        self.assertEqual([p.read_bytes() for p in history], [old_bytes])

    def test_wrong_marker_binding_cannot_be_archived_or_retried(self):
        marker = self.fail_first_attempt()
        value = json.loads(marker.read_text(encoding='utf-8'))
        value['ids'] = [2]
        marker.write_text(json.dumps(value), encoding='utf-8')
        before = marker.read_bytes()
        with patch.object(translation, '_invoke_codex') as invoke:
            with self.assertRaisesRegex(ValueError, 'không khớp'):
                self.translate(resume_incomplete=True)
            invoke.assert_not_called()
        self.assertEqual(marker.read_bytes(), before)
        self.assertFalse((self.job/'attempt-history').exists())

    def test_changed_chinese_source_rejects_continue_before_archiving(self):
        marker = self.fail_first_attempt()
        self.source.write_text(self.source.read_text(encoding='utf-8').replace('这是', '那是'), encoding='utf-8')
        before = marker.read_bytes()
        with patch.object(translation, '_invoke_codex') as invoke:
            with self.assertRaisesRegex(ValueError, 'phụ đề khác'):
                self.translate(resume_incomplete=True)
            invoke.assert_not_called()
        self.assertEqual(marker.read_bytes(), before)

    def test_malformed_response_fails_after_exactly_one_explicit_attempt(self):
        self.fail_first_attempt()
        invalid = {'translations': [{'id': 99, 'text': 'Câu sai số thứ tự.'}], 'review': []}
        with patch.object(translation, '_invoke_codex', return_value=(invalid, {})) as invoke:
            with self.assertRaisesRegex(ValueError, 'sai thứ tự'):
                self.translate(resume_incomplete=True)
            invoke.assert_called_once()
        self.assertFalse(self.output.exists())
        self.assertFalse((self.job/'batch-1.json').exists())

    def test_srt_published_before_report_recovers_without_another_request(self):
        self.publication_failure()
        before = self.output.read_bytes()
        with patch.object(translation, '_invoke_codex') as invoke:
            result = self.translate()
            invoke.assert_not_called()
        self.assertEqual(self.output.read_bytes(), before)
        self.assertEqual(result['cue_count'], 3)
        self.assertFalse(self.pending.exists())
        self.assertTrue(self.report.exists())

    def test_pending_report_before_srt_reconstructs_from_finished_batches_without_request(self):
        self.publication_failure(before_srt=True)
        cached = {p.name: p.read_bytes() for p in self.job.glob('batch-*.json')}
        with patch.object(translation, '_invoke_codex') as invoke:
            self.translate()
            invoke.assert_not_called()
        self.assertEqual({p.name: p.read_bytes() for p in self.job.glob('batch-*.json')}, cached)
        self.assertFalse(self.pending.exists())
        self.assertEqual(translation.verify_translation(self.output, self.source)['cue_count'], 3)

    def test_pending_publication_with_missing_batch_cache_never_sends_cli(self):
        self.publication_failure(before_srt=True)
        (self.job/'batch-2.json').unlink()
        with patch.object(translation, '_invoke_codex') as invoke:
            with self.assertRaisesRegex(ValueError, 'thiếu bộ câu'):
                self.translate(resume_incomplete=True)
            invoke.assert_not_called()
        self.assertTrue(self.pending.exists())
        self.assertFalse(self.output.exists())

    def test_tampered_published_srt_is_not_repaired_or_overwritten(self):
        self.publication_failure()
        self.output.write_text(self.output.read_text(encoding='utf-8').replace('Câu dịch', 'Văn bản đã sửa'), encoding='utf-8')
        before = self.output.read_bytes()
        with patch.object(translation, '_invoke_codex') as invoke:
            with self.assertRaisesRegex(ValueError, 'đã thay đổi'):
                self.translate(resume_incomplete=True)
            invoke.assert_not_called()
        self.assertEqual(self.output.read_bytes(), before)
        self.assertTrue(self.pending.exists())
        self.assertFalse(self.report.exists())

    def test_legacy_orphan_srt_without_writeahead_evidence_is_not_adopted(self):
        write_srt(self.output, [dict(c, text='Bản dịch chưa có chứng cứ.') for c in self.cues])
        before = self.output.read_bytes()
        with patch.object(translation, '_invoke_codex') as invoke:
            with self.assertRaisesRegex(ValueError, 'chứng cứ'):
                self.translate(resume_incomplete=True)
            invoke.assert_not_called()
        self.assertEqual(self.output.read_bytes(), before)

    def test_completed_translation_remains_read_only_even_with_explicit_resume(self):
        with patch.object(translation, '_invoke_codex', side_effect=response_for):
            self.translate()
        before = {str(p.relative_to(self.path)): p.read_bytes() for p in self.path.rglob('*') if p.is_file()}
        with patch.object(translation, '_invoke_codex') as invoke:
            self.translate(resume_incomplete=True)
            invoke.assert_not_called()
        self.assertEqual({str(p.relative_to(self.path)): p.read_bytes() for p in self.path.rglob('*') if p.is_file()}, before)

    def test_resume_requires_a_boolean_flag(self):
        for value in (1, 'true', None):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'bấm Tiếp tục'):
                self.translate(resume_incomplete=value)


if __name__ == '__main__':
    unittest.main()
