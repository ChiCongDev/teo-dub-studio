"""23/11/2026 (owner): a DLL Windows refuses (Smart App Control blocked llvmlite.dll on a staff PC, error 0xc0e90002) must fail
at once instead of holding a Tèo process behind a "Bad Image" box until someone presses OK. No Tèo data, no browser."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import launch

PROBE = r'''
import ctypes, msvcrt, sys
msvcrt.SetErrorMode(0)  # What a Tèo opened from Explorer starts with (a terminal may hand down more).
sys.path.insert(0, sys.argv[1])
import launch
launch.quiet_error_boxes()
assert msvcrt.GetErrorMode() & msvcrt.SEM_FAILCRITICALERRORS, msvcrt.GetErrorMode()
try:
    ctypes.CDLL(sys.argv[2])  # The way llvmlite loads llvmlite.dll; CPython's own .pyd import turns the box off by itself.
except OSError as error:
    print('refused at once:', error)
else:
    print('LOADED')
'''


class QuietErrorBoxTests(unittest.TestCase):
    def test_main_turns_the_boxes_off_before_anything_else(self):
        calls = []
        with patch.object(launch, 'quiet_error_boxes', side_effect=lambda: calls.append('quiet')), \
             patch.object(launch, 'bootstrap_environment', side_effect=lambda args: calls.append('bootstrap') or 5):
            self.assertEqual(launch.main(['--worker', '--config', 'request.json']), 5)
        self.assertEqual(calls, ['quiet', 'bootstrap'])

    def test_never_raises(self):
        if sys.platform != 'win32':
            self.skipTest('Windows only')
        import msvcrt
        with patch.object(msvcrt, 'SetErrorMode', side_effect=OSError('refused')):
            launch.quiet_error_boxes()
        with patch.object(launch.sys, 'platform', 'linux'), patch.object(msvcrt, 'SetErrorMode') as set_mode:
            launch.quiet_error_boxes()
        set_mode.assert_not_called()

    def test_a_broken_dll_fails_at_once_instead_of_waiting_for_ok(self):
        if sys.platform != 'win32':
            self.skipTest('Windows only')
        with tempfile.TemporaryDirectory(prefix='teo bad image ') as folder:
            broken = Path(folder)/'broken-test.dll'
            broken.write_bytes(bytes(65536))  # All zeros: no code ever runs; Windows calls it a bad image (0xc000012f).
            try:
                done = subprocess.run([sys.executable, '-I', '-c', PROBE, str(Path(launch.__file__).resolve().parent), str(broken)],
                                      capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=60,
                                      creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            except subprocess.TimeoutExpired:
                self.fail('the load waited for a Windows error box instead of failing')
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn('refused at once:', done.stdout)


if __name__ == '__main__':
    unittest.main()
