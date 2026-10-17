"""Distinct upload diagnostics without weakening attachment or retention guards.

These tests use synthetic observations and do not connect to any user browser,
Facebook session, publisher store, or account registry.
"""
from copy import deepcopy
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from teodub.facebook_web_post import BrowserPagePublisher


class FacebookUploadFileDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        # Sentinels deliberately resemble sensitive data. Diagnostics must show
        # the failed check, not expose file paths, values, captions or player URIs.
        self.secrets = (
            'private-auth-sentinel.mp4', 'caption-secret-sentinel',
            'blob:private-player-sentinel', 'private-hash-sentinel',
        )
        self.files = [dict(path='C:/private/private-auth-sentinel.mp4', size=524288,
                           mtime_ns=3330000000, sha256=self.secrets[3])]
        self.selected = [dict(name=Path(self.files[0]['path']).name, size=524288,
                              last_modified=3330, type='video/mp4')]
        self.data = dict(files=deepcopy(self.selected), preview_count=1, busy=False,
                         upload_error=False, upload_complete=False,
                         body=self.secrets[1], videos=[dict(src=self.secrets[2])])

    def failure(self, data, files=None):
        with self.assertRaises(ValueError) as caught:
            BrowserPagePublisher._check_files(data, self.files if files is None else files)
        message = str(caught.exception)
        for secret in self.secrets:
            self.assertNotIn(secret, message)
        return message

    def test_matched_metadata_and_unique_preview_remain_accepted_without_new_marker_requirement(self):
        before = deepcopy(self.data)
        self.assertIsNone(BrowserPagePublisher._check_files(self.data, self.files))
        self.assertEqual(self.data, before)

    def test_zero_preview_has_counts_and_differs_from_file_metadata_error(self):
        missing = dict(self.data, preview_count=0)
        preview_message = self.failure(missing)
        altered = deepcopy(self.data)
        altered['files'][0]['size'] += 1
        file_message = self.failure(altered)
        self.assertNotEqual(preview_message, file_message)
        self.assertIn('0', preview_message)
        self.assertIn('1', preview_message)
        self.assertRegex(preview_message.casefold(), 'xem trước|đính kèm')

    def test_ambiguous_preview_remains_rejected_and_shows_counts(self):
        message = self.failure(dict(self.data, preview_count=2))
        self.assertIn('2', message)
        self.assertIn('1', message)

    def test_processing_is_distinct_from_missing_preview_and_altered_file(self):
        processing = self.failure(dict(self.data, busy=True))
        missing_preview = self.failure(dict(self.data, preview_count=0))
        changed = deepcopy(self.data)
        changed['files'][0]['last_modified'] += 1
        changed_file = self.failure(changed)
        self.assertEqual(len({processing, missing_preview, changed_file}), 3)
        self.assertIn('xử lý', processing.casefold())

    def test_each_file_identity_component_remains_required_and_values_are_private(self):
        messages = []
        for field, value in (('name', 'wrong-private-name.mp4'), ('size', 524289),
                             ('last_modified', 3331)):
            with self.subTest(field=field):
                data = deepcopy(self.data)
                data['files'][0][field] = value
                message = self.failure(data)
                self.assertNotIn(str(value), message)
                messages.append(message)
        self.assertEqual(len(set(messages)), 1)

    def test_empty_picker_differs_from_nonempty_mismatched_file_metadata(self):
        released = self.failure(dict(self.data, files=[]))
        changed = deepcopy(self.data)
        changed['files'][0]['name'] = 'wrong-private-name.mp4'
        mismatch = self.failure(changed)
        self.assertNotEqual(released, mismatch)

    def test_retained_picker_failure_reasons_are_distinct_and_do_not_echo_payload(self):
        messages = []
        for reason in ('processing', 'completion', 'preview', 'identity', 'body', 'video'):
            with self.subTest(reason=reason):
                data = dict(self.data, files=[], input_release_wait=reason)
                messages.append(self.failure(data))
        self.assertEqual(len(set(messages)), 6)

    def test_unknown_retention_reason_does_not_echo_untrusted_value(self):
        message = self.failure(dict(self.data, files=[],
                                    input_release_wait='private-auth-sentinel.mp4'))
        self.assertNotIn('private-auth-sentinel.mp4', message)

    def test_upload_alert_still_blocks_otherwise_valid_attachment(self):
        message = self.failure(dict(self.data, upload_error=True))
        self.assertIn('lỗi', message.casefold())

    def test_text_without_attachments_passes_and_unexpected_file_does_not(self):
        self.assertIsNone(BrowserPagePublisher._check_files(
            dict(self.data, files=[], preview_count=0), []))
        self.failure(dict(self.data, preview_count=0), [])

    def test_complete_guard_matrix_preserves_previous_acceptance_and_observation(self):
        for expected_files in ([], self.files):
            for observed_files in ([], self.selected,
                                   [dict(self.selected[0], last_modified=9999)]):
                for previews in (0, 1, 2):
                    for busy in (False, True):
                        for error in (False, True):
                            with self.subTest(expected=len(expected_files), observed=observed_files,
                                              previews=previews, busy=busy, error=error):
                                expected = [(Path(x['path']).name, x['size'],
                                             x['mtime_ns'] // 1000000) for x in expected_files]
                                observed = [(x['name'], x['size'], x['last_modified'])
                                            for x in observed_files]
                                formerly_accepted = (not (expected_files and error)
                                                     and observed == expected
                                                     and previews == len(expected_files) and not busy)
                                data = dict(self.data, files=deepcopy(observed_files),
                                            preview_count=previews, busy=busy, upload_error=error)
                                before = deepcopy(data)
                                if formerly_accepted:
                                    self.assertIsNone(BrowserPagePublisher._check_files(data, expected_files))
                                else:
                                    self.failure(data, expected_files)
                                self.assertEqual(data, before)


class FacebookUploadRetentionDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.publisher = BrowserPagePublisher.__new__(BrowserPagePublisher)
        self.target = dict(page_id='123', name='Page Demo',
                           url='https://www.facebook.com/profile.php?id=123')
        self.files = [dict(path='C:/offline/selected.mp4', size=1234,
                           mtime_ns=4500000000, sha256='offline-hash')]
        self.metadata = [dict(name='selected.mp4', size=1234, type='video/mp4',
                              last_modified=4500)]
        self.retained = dict(files=deepcopy(self.metadata), preview_count=1,
                             upload_complete=True, busy=False, upload_error=False,
                             identity=deepcopy(self.target), body='Approved caption',
                             videos=[dict(src='blob:original', poster='initial', width=720,
                                          height=1280, duration=12.5)])
        self.current = deepcopy(self.retained)
        self.current['files'] = []
        self.dialog = object()
        self.tab = Mock(spec=['wait_for_timeout'])

    def observed(self, current=None, retained=None, *, without_retained=False):
        data = deepcopy(self.current if current is None else current)
        binding = None if without_retained else deepcopy(self.retained if retained is None else retained)
        with patch.object(self.publisher, '_observe', return_value=(self.dialog, data)) as observe, \
                patch.object(self.publisher, '_cancel'):
            dialog, actual = self.publisher._observe_upload_stage(self.tab, self.target, binding)
        self.assertIs(dialog, self.dialog)
        observe.assert_called_once_with(self.tab, self.target)
        self.tab.wait_for_timeout.assert_not_called()
        return actual

    def test_same_bound_video_restores_actual_file_metadata_and_returns_no_wait_reason(self):
        result = self.observed()
        self.assertEqual(result['files'], self.metadata)
        self.assertTrue(result['input_released_after_upload'])
        self.assertNotIn('input_release_wait', result)
        self.assertIsNone(BrowserPagePublisher._check_files(result, self.files))
        self.assertEqual(self.current['files'], [])

    def test_decode_poster_change_and_equivalent_caption_spacing_do_not_change_video_identity(self):
        current = deepcopy(self.current)
        current['videos'][0]['poster'] = 'decoded-new-poster'
        current['body'] = 'Approved\n  caption'
        result = self.observed(current)
        self.assertTrue(result['input_released_after_upload'])
        self.assertIsNone(BrowserPagePublisher._check_files(result, self.files))

    def test_each_retention_guard_reports_its_reason_without_restoring_metadata(self):
        variants = (
            ('processing', lambda data: data.update(busy=True)),
            ('completion', lambda data: data.update(upload_complete=False)),
            ('preview', lambda data: data.update(preview_count=0)),
            ('preview', lambda data: data.update(preview_count=2)),
            ('identity', lambda data: data['identity'].update(page_id='456')),
            ('body', lambda data: data.update(body='Changed caption')),
            ('video', lambda data: data['videos'][0].update(src='blob:replacement')),
            ('video', lambda data: data.update(videos=[])),
        )
        for reason, change in variants:
            with self.subTest(reason=reason):
                data = deepcopy(self.current)
                change(data)
                result = self.observed(data)
                self.assertEqual(result['files'], [])
                self.assertNotIn('input_released_after_upload', result)
                self.assertEqual(result.get('input_release_wait'), reason)
                with self.assertRaises(ValueError):
                    BrowserPagePublisher._check_files(result, self.files)

    def test_different_blob_with_same_loaded_geometry_is_not_rebound_before_next(self):
        data = deepcopy(self.current)
        data['videos'][0]['src'] = 'blob:same-dimensions-other-video'
        result = self.observed(data)
        self.assertEqual(result.get('input_release_wait'), 'video')
        self.assertEqual(result['files'], [])
        self.assertNotIn('input_released_after_upload', result)

    def test_original_ambiguous_retained_preview_cannot_restore_current_unique_preview(self):
        retained = dict(self.retained, preview_count=2)
        result = self.observed(retained=retained)
        self.assertEqual(result.get('input_release_wait'), 'preview')
        self.assertEqual(result['files'], [])

    def test_missing_retention_never_guesses_metadata_or_writes_a_retention_reason(self):
        result = self.observed(without_retained=True)
        self.assertEqual(result['files'], [])
        self.assertNotIn('input_release_wait', result)
        self.assertNotIn('input_released_after_upload', result)
        with self.assertRaises(ValueError):
            BrowserPagePublisher._check_files(result, self.files)

    def test_nonempty_wrong_picker_metadata_is_never_replaced_by_retained_files(self):
        current = deepcopy(self.current)
        current['files'] = [dict(self.metadata[0], size=5678)]
        result = self.observed(current)
        self.assertEqual(result['files'], current['files'])
        self.assertNotIn('input_release_wait', result)
        self.assertNotIn('input_released_after_upload', result)
        with self.assertRaises(ValueError):
            BrowserPagePublisher._check_files(result, self.files)


if __name__ == '__main__':
    unittest.main()
