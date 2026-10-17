"""No network, browser, account or synthesis: exercise the online safety gates."""
from contextlib import nullcontext
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import numpy as np
import soundfile as sf

from teodub import capcut_tts as online
from teodub.common import Cancelled, hash_file


def catalog(voice_id='BV074_streaming', resource_id='7102355709945188865', is_vip=False):
    return {'data': {'items': [{'common_attr': dict(id=resource_id, title='Giọng nữ dễ thương',
        extra=json.dumps(dict(is_vip=is_vip, tonetype=json.dumps(dict(voice_type=voice_id)))))}]}}


class FakePage:
    def __init__(self, source, price='Tạo 0 tín dụng', failure=None):
        self.source, self.price, self.failure = source, price, failure
        self.submitted = False
        self.generate = Mock()
        self.generate.count.return_value = 1
        self.generate.inner_text.return_value = price
        self.generate.click.side_effect = self.submit
        self.download = Mock()
        self.download.count.side_effect = lambda: int(self.submitted)
        self.download.is_visible.return_value = True
        self.download.is_enabled.return_value = True
        self.downloaded = SimpleNamespace(path=lambda: str(source))
        self.editor = Mock()
        self.editor.count.return_value = 1
        self.body = Mock()
        self.body.inner_text.return_value = 'Chuyển văn bản thành lời nói\nTạo'

    def submit(self):
        self.submitted = True
        if self.failure:
            raise self.failure

    def get_by_text(self, text, **kwargs):
        item = Mock()
        item.count.return_value = 0 if text == 'Bỏ qua' else 1
        item.first = item
        return item

    def locator(self, selector):
        return self.body if selector == 'body' else self.editor

    def get_by_role(self, role, name):
        return self.generate if 'Generate' in name.pattern else self.download

    def expect_download(self, **kwargs):
        return nullcontext(SimpleNamespace(value=self.downloaded))


class CapCutTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='teodub-capcut-test-')
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.target = self.directory/'result.wav'
        self.voice = 'Cô Gái Hoạt Ngôn'

    def audio(self, silent=False):
        source = self.directory/'download.wav'
        t = np.arange(22050)/22050
        signal = np.zeros(len(t)) if silent else .15*np.sin(2*np.pi*377*t)
        sf.write(str(source), np.column_stack((signal, signal)), 22050, subtype='PCM_16')
        return source

    def fake_session(self, page):
        session = Mock()
        session.page = page
        session.check_voice.return_value = {'title': 'Giọng nữ dễ thương'}
        return nullcontext(session)

    def completed_audio(self):
        page = FakePage(self.audio())
        with patch.object(online, '_WebSession', return_value=self.fake_session(page)):
            result = online.synthesize_to_file('Xin chào.', self.voice, self.target)
        marker = self.target.with_name('result.wav.capcut-request.json')
        return result, marker, json.loads(marker.read_text(encoding='utf-8'))

    def test_exact_names_map_to_correct_ids_without_network(self):
        self.assertEqual(online.validate_voice(self.voice), 'BV074_streaming')
        self.assertEqual(online.validate_voice('Thanh Niên Tự Tin'), 'BV075_streaming')
        for bad in ('Mai Anh', 'BV074_streaming', '', None):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                online.validate_voice(bad)

    def test_catalog_requires_matching_resource_and_voice_identity(self):
        found = online._catalog_voices(catalog())
        self.assertEqual(found['BV074_streaming']['title'], 'Giọng nữ dễ thương')
        self.assertIs(found['BV074_streaming']['is_vip'], False)
        self.assertEqual(online._catalog_voices(catalog(resource_id='unrelated')), {})
        self.assertEqual(online._catalog_voices(catalog(voice_id='other-voice')), {})

    def test_unknown_premium_flag_is_not_free(self):
        session = online._WebSession(None, None)
        for flag in (None, True, 'false', 0):
            session.catalog = online._catalog_voices(catalog(is_vip=flag))
            with self.subTest(flag=flag), self.assertRaises(online.CapCutAccessRequired):
                session.check_voice(self.voice)

    def test_login_verification_and_credit_messages_stop_actionably(self):
        cases = [('Chào mừng bạn đến với CapCut', 'login_required'),
                 ('Chuyển văn bản thành lời nói\nĐăng nhập\nTạo', 'login_required'),
                 ('Verify you are human', 'verification_required'),
                 ('Hãy nâng cấp để nhận điểm tín dụng', 'credits_required'),
                 ('Insufficient credits', 'credits_required')]
        for text, code in cases:
            with self.subTest(text=text), self.assertRaises(online.CapCutAccessRequired) as caught:
                online._check_access_text(text)
            self.assertEqual(caught.exception.code, code)

    def test_zero_charge_requires_price_on_generate_not_balance_or_pro_flag(self):
        for text in ('Tạo 0 tín dụng', 'Generate 0 credits', 'Tạo miễn phí', 'Generate for free'):
            with self.subTest(text=text):
                self.assertTrue(online._zero_price_button_text(text))
        for text in ('Tạo', 'Tạo 5 tín dụng', '0 credits remaining', 'Tạo 0 tín dụng còn lại',
                     'Tạo miễn phí 10 tín dụng', 'Generate free trial', 'Tạo không miễn phí',
                     'Generate not free', 'Generate premium', 'is_vip: false', '0 credits'):
            with self.subTest(text=text):
                self.assertFalse(online._zero_price_button_text(text))

    def test_open_login_launches_only_dedicated_profile_and_returns(self):
        profile = self.directory/'own-profile'
        with patch.object(online, 'profile_directory', return_value=profile), \
                patch.object(online, '_browser_executable', return_value=Path('C:/Edge/msedge.exe')), \
                patch.object(online.subprocess, 'Popen', return_value=SimpleNamespace(pid=123)) as start:
            result = online.open_capcut_login()
        self.assertEqual(result, dict(url=online.SERVICE_URL, profile=str(profile), pid=123))
        command = start.call_args.args[0]
        self.assertIn('--user-data-dir='+str(profile), command)
        self.assertIn(online.SERVICE_URL, command)
        self.assertNotIn('--headless', command)

    def test_preflight_does_not_claim_synthesis_tested(self):
        session = Mock()
        session.check_voice.side_effect = lambda name: {'voice_id': online.VOICE_IDS[name]}
        with patch.object(online, '_WebSession', return_value=nullcontext(session)):
            result = online.preflight()
        self.assertFalse(result['synthesis_verified'])
        self.assertTrue(result['requires_zero_price_check'])
        self.assertEqual(set(result['voices']), set(online.VOICES))
        session.page.assert_not_called()

    def test_preflight_only_checks_selected_voice(self):
        session = Mock()
        session.check_voice.side_effect = lambda name: {'voice_id': online.VOICE_IDS[name]}
        with patch.object(online, '_WebSession', return_value=nullcontext(session)):
            result = online.preflight(voice=self.voice)
        self.assertEqual(set(result['voices']), {self.voice})
        session.check_voice.assert_called_once_with(self.voice)

    def test_preflight_invalid_voice_never_opens_browser(self):
        with patch.object(online, '_WebSession') as launch:
            with self.assertRaises(ValueError):
                online.preflight(voice='unknown')
        launch.assert_not_called()

    def test_login_failure_creates_no_output_or_submission_marker(self):
        failure = online.CapCutAccessRequired('Cần đăng nhập.', 'login_required')
        with patch.object(online, '_WebSession', side_effect=failure):
            with self.assertRaises(online.CapCutAccessRequired):
                online.synthesize_to_file('Xin chào.', self.voice, self.target)
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_unknown_price_does_not_click_generate_or_write_marker(self):
        page = FakePage(self.audio(), price='Tạo')
        with patch.object(online, '_WebSession', return_value=self.fake_session(page)):
            with self.assertRaises(online.CapCutAccessRequired) as error:
                online.synthesize_to_file('Xin chào.', self.voice, self.target)
        self.assertEqual(error.exception.code, 'zero_price_unverified')
        page.generate.click.assert_not_called()
        self.assertFalse(self.target.exists())
        self.assertFalse(self.target.with_name('result.wav.capcut-request.json').exists())

    def test_guarded_flow_normalizes_download_and_records_completion(self):
        page = FakePage(self.audio())
        with patch.object(online, '_WebSession', return_value=self.fake_session(page)):
            result = online.synthesize_to_file('Xin chào.', self.voice, self.target)
        page.generate.click.assert_called_once()
        audio, rate = sf.read(self.target)
        self.assertEqual(rate, 48000)
        self.assertEqual(audio.ndim, 1)
        self.assertEqual(sf.info(self.target).subtype, 'PCM_16')
        self.assertAlmostEqual(result['duration_s'], 1, places=3)
        marker = json.loads(self.target.with_name('result.wav.capcut-request.json').read_text())
        self.assertEqual(marker['status'], 'completed')
        self.assertEqual(marker['audio']['sha256'], hash_file(self.target))

    def test_completed_audio_recovers_without_network_or_file_changes(self):
        expected, marker, _ = self.completed_audio()
        before = self.target.read_bytes(), marker.read_bytes()
        with patch.object(online, '_WebSession') as launch:
            result = online.synthesize_to_file('Xin chào.', self.voice, self.target)
        self.assertEqual(result, expected)
        self.assertEqual(before, (self.target.read_bytes(), marker.read_bytes()))
        launch.assert_not_called()

    def test_completed_audio_rejects_changed_request_or_ambiguous_status_without_network(self):
        _, marker, original = self.completed_audio()
        for field, value in (('text', 'Lời khác'), ('voice', 'Thanh Niên Tự Tin'),
                             ('voice_id', 'BV075_streaming'), ('model', 'other-model'),
                             ('target', str(self.directory/'another.wav')),
                             ('status', 'submission_started'), ('status', 'submission_uncertain')):
            changed = dict(original, **{field: value})
            marker.write_text(json.dumps(changed), encoding='utf-8')
            before = self.target.read_bytes(), marker.read_bytes()
            with self.subTest(field=field, value=value), patch.object(online, '_WebSession') as launch:
                with self.assertRaises(online.CapCutAccessRequired) as caught:
                    online.synthesize_to_file('Xin chào.', self.voice, self.target)
                self.assertEqual(caught.exception.code, 'submission_already_recorded')
                launch.assert_not_called()
                self.assertEqual(before, (self.target.read_bytes(), marker.read_bytes()))

    def test_completed_audio_rejects_tampered_metadata_without_network(self):
        _, marker, original = self.completed_audio()
        for field, value in (('path', str(self.directory/'another.wav')), ('sha256', '0'*64),
                             ('samples', 48001), ('sample_rate', 22050), ('duration_s', 2.0),
                             ('duration_s', True)):
            changed = dict(original, audio=dict(original['audio'], **{field: value}))
            marker.write_text(json.dumps(changed), encoding='utf-8')
            with self.subTest(field=field, value=value), patch.object(online, '_WebSession') as launch:
                with self.assertRaises(online.CapCutAccessRequired):
                    online.synthesize_to_file('Xin chào.', self.voice, self.target)
                launch.assert_not_called()

    def test_completed_audio_rejects_tampered_wav_without_network(self):
        _, marker, _ = self.completed_audio()
        audio, rate = sf.read(self.target)
        sf.write(self.target, audio*.7, rate, subtype='PCM_16')
        before = self.target.read_bytes(), marker.read_bytes()
        with patch.object(online, '_WebSession') as launch:
            with self.assertRaises(online.CapCutAccessRequired):
                online.synthesize_to_file('Xin chào.', self.voice, self.target)
        launch.assert_not_called()
        self.assertEqual(before, (self.target.read_bytes(), marker.read_bytes()))

    def test_completed_audio_requires_normalized_nonsilent_wav_even_with_matching_hash(self):
        _, marker, original = self.completed_audio()
        for rate, channels, subtype, silent in ((22050, 1, 'PCM_16', False),
                                                (48000, 2, 'PCM_16', False),
                                                (48000, 1, 'FLOAT', False),
                                                (48000, 1, 'PCM_16', True)):
            audio = np.zeros(rate) if silent else .1*np.sin(np.arange(rate)*.05)
            if channels == 2:
                audio = np.column_stack((audio, audio))
            sf.write(self.target, audio, rate, subtype=subtype)
            changed = dict(original, audio=dict(path=str(self.target), sha256=hash_file(self.target),
                                                duration_s=1.0, samples=rate, sample_rate=rate))
            marker.write_text(json.dumps(changed), encoding='utf-8')
            with self.subTest(rate=rate, channels=channels, subtype=subtype, silent=silent), \
                    patch.object(online, '_WebSession') as launch:
                with self.assertRaises(online.CapCutAccessRequired):
                    online.synthesize_to_file('Xin chào.', self.voice, self.target)
                launch.assert_not_called()

    def test_missing_completion_evidence_never_resubmits(self):
        _, marker, _ = self.completed_audio()
        valid_marker, valid_audio = marker.read_bytes(), self.target.read_bytes()
        for state in ('missing-marker', 'missing-audio', 'invalid-json', 'invalid-record'):
            marker.write_bytes(valid_marker)
            self.target.write_bytes(valid_audio)
            if state == 'missing-marker':
                marker.unlink()
            elif state == 'missing-audio':
                self.target.unlink()
            else:
                marker.write_text('broken' if state == 'invalid-json' else 'null', encoding='utf-8')
            with self.subTest(state=state), patch.object(online, '_WebSession') as launch:
                with self.assertRaises(online.CapCutAccessRequired):
                    online.synthesize_to_file('Xin chào.', self.voice, self.target)
                launch.assert_not_called()

    def test_cancel_while_verifying_completed_audio_preserves_evidence(self):
        _, marker, _ = self.completed_audio()
        before = self.target.read_bytes(), marker.read_bytes()
        cancel = Mock()
        cancel.is_set.side_effect = [False, False, True]
        with patch.object(online, '_WebSession') as launch:
            with self.assertRaises(Cancelled):
                online.synthesize_to_file('Xin chào.', self.voice, self.target, cancel=cancel)
        launch.assert_not_called()
        self.assertEqual(before, (self.target.read_bytes(), marker.read_bytes()))

    def test_submit_uncertainty_cannot_repeat_generation(self):
        page = FakePage(self.audio(), failure=RuntimeError('network disconnected after click'))
        with patch.object(online, '_WebSession', return_value=self.fake_session(page)):
            with self.assertRaises(RuntimeError):
                online.synthesize_to_file('Xin chào.', self.voice, self.target)
        marker = json.loads(self.target.with_name('result.wav.capcut-request.json').read_text())
        self.assertEqual(marker['status'], 'submission_uncertain')
        with patch.object(online, '_WebSession') as launch:
            with self.assertRaises(online.CapCutAccessRequired):
                online.synthesize_to_file('Xin chào.', self.voice, self.target)
        launch.assert_not_called()
        self.assertFalse(self.target.exists())

    def test_cancel_before_synthesis_never_opens_browser(self):
        stop = threading.Event()
        stop.set()
        with patch.object(online, '_WebSession') as launch:
            with self.assertRaises(Cancelled):
                online.synthesize_to_file('Xin chào.', self.voice, self.target, cancel=stop)
        launch.assert_not_called()

    def test_normalization_preserves_download_and_existing_target(self):
        source = self.audio()
        before = hash_file(source)
        online._normalize_download(source, self.target, None)
        result_hash = hash_file(self.target)
        with self.assertRaises(ValueError):
            online._normalize_download(source, self.target, None)
        self.assertEqual(hash_file(self.target), result_hash)
        self.assertEqual(hash_file(source), before)

    def test_silent_download_rejected_without_output(self):
        with self.assertRaises(ValueError):
            online._normalize_download(self.audio(silent=True), self.target, None)
        self.assertFalse(self.target.exists())
        self.assertEqual(list(self.directory.glob('*.pending.wav')), [])

    def test_cancelled_normalization_leaves_no_output(self):
        stop = threading.Event()
        stop.set()
        with self.assertRaises(Cancelled):
            online._normalize_download(self.audio(), self.target, stop)
        self.assertFalse(self.target.exists())
        self.assertEqual(list(self.directory.glob('*.pending.wav')), [])


if __name__ == '__main__':
    unittest.main()
