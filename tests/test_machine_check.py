from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from teodub import machine_check as mc


class FakeRun:
    def __init__(self, code=0, out='', err=''):
        self.returncode, self.stdout, self.stderr = code, out, err


class MachineCheckTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_chrome_found_and_missing(self):
        exe = self.root/'Google/Chrome/Application/chrome.exe'
        exe.parent.mkdir(parents=True)
        exe.touch()
        self.assertTrue(mc.check_chrome({'PROGRAMFILES': str(self.root)}).ok)
        bad = mc.check_chrome({'PROGRAMFILES': str(self.root/'nothing')})
        self.assertFalse(bad.ok)
        self.assertIn('Chrome', bad.message)

    def test_codex_logged_in_and_not_logged_in(self):
        with patch.object(mc.codex_translation, 'find_codex', return_value='codex.exe'):
            ok = mc.check_codex_login(Mock(return_value=FakeRun(0, 'Logged in using ChatGPT')))
            self.assertTrue(ok.ok)
            no = mc.check_codex_login(Mock(return_value=FakeRun(1, 'Not logged in')))
            self.assertFalse(no.ok)
            self.assertEqual(no.action, 'login')

    def test_codex_missing_or_unresponsive(self):
        with patch.object(mc.codex_translation, 'find_codex', side_effect=ValueError('Chưa tìm thấy Codex CLI.')):
            self.assertIn('Codex', mc.check_codex_login().message)
        with patch.object(mc.codex_translation, 'find_codex', return_value='codex.exe'):
            result = mc.check_codex_login(Mock(side_effect=subprocess.TimeoutExpired('codex', 20)))
            self.assertFalse(result.ok)

    def test_models_need_real_files(self):
        self.assertFalse(mc.check_models(self.root).ok)
        small = self.root/'whisper/models--Systran--faster-whisper-small/snapshots/abc'
        small.mkdir(parents=True)
        (small/'model.bin').touch()
        tts = self.root/'huggingface/hub/models--pnnbao-ump--VieNeu-TTS-v3-Turbo/snapshots/abc'
        tts.mkdir(parents=True)
        self.assertFalse(mc.check_models(self.root).ok)  # an empty snapshot is not a model
        (tts/'config.json').touch()
        self.assertTrue(mc.check_models(self.root).ok)

    def test_models_report_what_is_missing(self):
        message = mc.check_models(self.root).message
        self.assertIn('Whisper small', message)
        self.assertIn('VieNeu', message)

    def test_long_install_path_is_warned(self):
        self.assertTrue(mc.check_install_path(Path('C:/Teo')).ok)
        self.assertFalse(mc.check_install_path(Path('C:/'+'a'*70)).ok)

    def test_translation_test_ok_and_failures(self):
        ok = mc.test_translation(lambda prompt, schema: ({'vi': 'Chiếc chảo chống dính.'}, {}))
        self.assertTrue(ok.ok)
        self.assertIn('chống dính', ok.message)
        limit = mc.test_translation(Mock(side_effect=ValueError('Codex đang hết hạn mức.')))
        self.assertFalse(limit.ok)
        self.assertIn('hạn mức', limit.message)
        self.assertFalse(mc.test_translation(Mock(side_effect=RuntimeError('boom'))).ok)
        self.assertFalse(mc.test_translation(lambda prompt, schema: ({'vi': ' '}, {})).ok)

    def test_first_run_marker(self):
        self.assertTrue(mc.first_run_pending(self.root))
        mc.mark_first_run_shown(self.root)
        self.assertFalse(mc.first_run_pending(self.root))

    def test_needs_attention(self):
        good, bad = mc.Check('a', 'a', True, ''), mc.Check('b', 'b', False, '')
        self.assertFalse(mc.needs_attention([good]))
        self.assertTrue(mc.needs_attention([good, bad]))


if __name__ == '__main__':
    unittest.main()
