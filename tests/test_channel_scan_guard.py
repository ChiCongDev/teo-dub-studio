from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from teodub.channel_scan_guard import channel_scan_guard


class ChannelScanGuardTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='teo scan lock ')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.profile = self.root/'isolated-coccoc'

    def test_creates_directory_and_releases_for_next_scan(self):
        self.assertFalse(self.profile.exists())
        with channel_scan_guard(self.profile):
            self.assertTrue(self.profile.is_dir())
        with channel_scan_guard(self.profile):
            pass

    def test_same_profile_cannot_be_opened_twice(self):
        with channel_scan_guard(self.profile):
            with self.assertRaisesRegex(ValueError, 'cửa sổ Tèo khác'):
                with channel_scan_guard(self.profile):
                    self.fail('A competing scan entered the same profile.')

    def test_distinct_profiles_can_be_scanned_concurrently(self):
        with channel_scan_guard(self.profile):
            with channel_scan_guard(self.root/'isolated-edge'):
                pass

    def test_scan_error_is_not_relabeled_and_lock_is_released(self):
        original = ValueError('Fixture channel response was invalid.')
        with self.assertRaises(ValueError) as raised:
            with channel_scan_guard(self.profile):
                raise original
        self.assertIs(raised.exception, original)
        with channel_scan_guard(self.profile):
            pass

    def test_preserves_existing_files_after_failed_scan(self):
        self.profile.mkdir()
        fixture = self.profile/'synthetic-marker.txt'
        fixture.write_text('untouched fixture', encoding='utf-8')
        with self.assertRaisesRegex(RuntimeError, 'fixture error'):
            with channel_scan_guard(self.profile):
                raise RuntimeError('fixture error')
        self.assertEqual(fixture.read_text(encoding='utf-8'), 'untouched fixture')

    def test_local_subprocess_competes_for_lock_then_can_enter_after_release(self):
        project = Path(__file__).resolve().parents[1]
        script = '''
from pathlib import Path
import sys
from teodub.channel_scan_guard import channel_scan_guard
try:
    with channel_scan_guard(Path(sys.argv[1])):
        print('acquired', flush=True)
except ValueError as exc:
    print(str(exc), file=sys.stderr, flush=True)
    raise SystemExit(3)
'''
        command = [sys.executable, '-X', 'utf8', '-B', '-c', script, str(self.profile)]
        options = dict(cwd=project, capture_output=True, text=True, encoding='utf-8', timeout=30)
        with channel_scan_guard(self.profile):
            denied = subprocess.run(command, **options)
        self.assertEqual(denied.returncode, 3, denied.stderr)
        self.assertIn('cửa sổ Tèo khác', denied.stderr)
        self.assertNotIn('acquired', denied.stdout)
        accepted = subprocess.run(command, **options)
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        self.assertEqual(accepted.stdout.strip(), 'acquired')


if __name__ == '__main__':
    unittest.main()
