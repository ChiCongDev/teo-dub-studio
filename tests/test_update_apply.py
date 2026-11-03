"""The update process end to end on a throw-away project folder, with the real Python running the self-check."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock
import zipfile

from teodub import update_apply

VERSION_PY = '''import json
from pathlib import Path


def current():
    data = json.loads((Path(__file__).resolve().parents[1]/'version.json').read_text(encoding='utf-8'))
    return dict(version=data['version'])
'''


def write_release(path, version, *, min_from=None, version_py=VERSION_PY, lock='numpy==1\n', extra=None, init=True):
    manifest = dict(version=version, released='2026-11-03')
    if min_from:
        manifest['min_from'] = min_from
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('version.json', json.dumps(manifest))
        if init:
            archive.writestr('teodub/__init__.py', '')
        archive.writestr('teodub/version.py', version_py)
        archive.writestr('teodub/feature.py', f'NAME = "{version}"\n')
        archive.writestr('launch.py', f'# launch {version}\n')
        archive.writestr('requirements-lock.txt', lock)
        archive.writestr('README.md', 'ignored: not one of the replaceable names')
        for name, data in (extra or {}).items():
            archive.writestr(name, data)
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class ApplyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='teo update ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root/'Teo'
        (self.project/'teodub').mkdir(parents=True)
        (self.project/'teodub'/'__init__.py').write_text('')
        (self.project/'teodub'/'version.py').write_text(VERSION_PY)
        (self.project/'teodub'/'feature.py').write_text('NAME = "1.0.0"\n')
        (self.project/'launch.py').write_text('# launch 1.0.0\n')
        (self.project/'version.json').write_text(json.dumps(dict(version='1.0.0')))
        (self.project/'requirements-lock.txt').write_text('numpy==1\n')
        for keep in ('models', 'runtime', 'tools'):
            (self.project/keep).mkdir()
            (self.project/keep/'precious.bin').write_text('do not touch')
        self.zip = self.root/'teo-code-1.1.0.zip'
        self.sha = write_release(self.zip, '1.1.0')
        self.launcher = Mock()

    def job(self, **extra):
        base = dict(project=str(self.project), zip=str(self.zip), sha256=self.sha, new_version='1.1.0',
                    parent_pid=None, relaunch=['teo-open'], python=sys.executable, smoke=['teodub.version'])
        base.update(extra)
        return base

    def apply(self, **extra):
        return update_apply.run(self.job(**extra), wait=Mock(), launcher=self.launcher)

    def feature(self):
        return (self.project/'teodub'/'feature.py').read_text()

    def assert_untouched(self):
        for keep in ('models', 'runtime', 'tools'):
            self.assertEqual((self.project/keep/'precious.bin').read_text(), 'do not touch')

    def test_update_replaces_code_keeps_backup_and_relaunches(self):
        result = self.apply()
        self.assertTrue(result['ok'], result)
        self.assertIn('1.1.0', self.feature())
        self.assertEqual(update_apply.read_version(self.project), '1.1.0')
        self.assertIn('1.0.0', (self.project/'versions'/'1.0.0'/'teodub'/'feature.py').read_text())
        self.assertFalse((self.project/'README.md').exists())
        self.assertFalse((self.project/'versions'/'_staging').exists())
        self.assert_untouched()
        self.launcher.assert_called_once()
        self.assertEqual(self.launcher.call_args.args[0], ['teo-open'])
        self.assertTrue(json.loads((self.project/'versions'/'last_update.json').read_text(encoding='utf-8'))['ok'])

    def test_waits_for_the_app_to_close_first(self):
        wait = Mock()
        update_apply.run(self.job(parent_pid=4321), wait=wait, launcher=self.launcher)
        wait.assert_called_once_with(4321)

    def test_damaged_download_changes_nothing(self):
        result = self.apply(sha256='0'*64)
        self.assertFalse(result['ok'])
        self.assertIn('SHA256', result['message'])
        self.assertIn('1.0.0', self.feature())
        self.assertEqual(update_apply.read_version(self.project), '1.0.0')
        self.launcher.assert_called_once()  # Tèo is always reopened

    def test_failed_self_check_rolls_back(self):
        broken = self.root/'broken.zip'
        sha = write_release(broken, '1.1.0', version_py='raise RuntimeError("new code is broken")\n')
        result = self.apply(zip=str(broken), sha256=sha)
        self.assertFalse(result['ok'])
        self.assertTrue(result['rolled_back'])
        self.assertIn('kiểm tra', result['message'])
        self.assertEqual(update_apply.read_version(self.project), '1.0.0')
        self.assertIn('1.0.0', self.feature())
        self.assert_untouched()

    def test_library_install_failure_rolls_back(self):
        changed = self.root/'changed.zip'
        sha = write_release(changed, '1.1.0', lock='numpy==2\n')

        def runner(command, **kwargs):
            if 'pip' in command:
                return Mock(returncode=1, stdout='', stderr='no matching distribution')
            return subprocess.run(command, **kwargs)
        result = update_apply.run(self.job(zip=str(changed), sha256=sha), wait=Mock(), runner=runner, launcher=self.launcher)
        self.assertFalse(result['ok'])
        self.assertTrue(result['rolled_back'])
        self.assertIn('thư viện', result['message'])
        self.assertEqual((self.project/'requirements-lock.txt').read_text(), 'numpy==1\n')
        self.assertEqual(update_apply.read_version(self.project), '1.0.0')

    def test_gpu_wheels_are_never_installed_by_an_update(self):
        changed = self.root/'gpu.zip'
        sha = write_release(changed, '1.1.0', lock='numpy==2\nnvidia-cublas-cu12==12.9\nrequests==3\n')
        seen = []

        def runner(command, **kwargs):
            if 'pip' in command:
                seen.append(Path(command[command.index('-r')+1]).read_text())
                return Mock(returncode=0, stdout='', stderr='')
            return subprocess.run(command, **kwargs)
        update_apply.run(self.job(zip=str(changed), sha256=sha), wait=Mock(), runner=runner, launcher=self.launcher)
        self.assertEqual(len(seen), 1)
        self.assertIn('numpy==2', seen[0])
        self.assertNotIn('nvidia', seen[0])
        self.assertIn('nvidia', (self.project/'requirements-lock.txt').read_text())

    def test_libraries_are_installed_only_when_the_list_changed(self):
        runner = Mock(side_effect=lambda command, **kwargs: subprocess.run(command, **kwargs))
        update_apply.run(self.job(), wait=Mock(), runner=runner, launcher=self.launcher)
        self.assertFalse(any('pip' in call.args[0] for call in runner.call_args_list))

    def test_unsafe_paths_in_the_archive_are_refused(self):
        evil = self.root/'evil.zip'
        sha = write_release(evil, '1.1.0', extra={'teodub/../../outside.txt': 'pwned'})
        result = self.apply(zip=str(evil), sha256=sha)
        self.assertFalse(result['ok'])
        self.assertFalse((self.root/'outside.txt').exists())
        self.assertFalse((self.project.parent/'outside.txt').exists())
        self.assertEqual(update_apply.read_version(self.project), '1.0.0')

    def test_too_old_to_update_is_refused_with_the_reason(self):
        gated = self.root/'gated.zip'
        sha = write_release(gated, '2.0.0', min_from='1.5.0')
        result = self.apply(zip=str(gated), sha256=sha, new_version='2.0.0')
        self.assertFalse(result['ok'])
        self.assertIn('1.5.0', result['message'])
        self.assertEqual(update_apply.read_version(self.project), '1.0.0')

    def test_wrong_version_inside_the_package_is_refused(self):
        result = self.apply(new_version='1.2.0')
        self.assertFalse(result['ok'])
        self.assertEqual(update_apply.read_version(self.project), '1.0.0')

    def test_package_without_program_code_is_refused(self):
        empty = self.root/'empty.zip'
        sha = write_release(empty, '1.1.0', init=False)
        result = self.apply(zip=str(empty), sha256=sha)
        self.assertFalse(result['ok'])
        self.assertEqual(update_apply.read_version(self.project), '1.0.0')

    def test_hand_edited_code_is_replaced_but_saved_in_the_backup(self):
        (self.project/'teodub'/'feature.py').write_text('NAME = "edited by hand"\n')
        (self.project/'teodub'/'my_extra.py').write_text('x = 1\n')
        self.assertTrue(self.apply()['ok'])
        self.assertIn('1.1.0', self.feature())
        self.assertFalse((self.project/'teodub'/'my_extra.py').exists())
        self.assertIn('edited by hand', (self.project/'versions'/'1.0.0'/'teodub'/'feature.py').read_text())

    def test_go_back_to_the_previous_version_and_forward_again(self):
        self.assertTrue(self.apply()['ok'])
        back = update_apply.run(dict(project=str(self.project), source_dir=str(self.project/'versions'/'1.0.0'),
                                     new_version='1.0.0', relaunch=['teo-open'], python=sys.executable,
                                     smoke=['teodub.version']), wait=Mock(), launcher=self.launcher)
        self.assertTrue(back['ok'], back)
        self.assertEqual(update_apply.read_version(self.project), '1.0.0')
        self.assertIn('1.0.0', self.feature())
        self.assertTrue((self.project/'versions'/'1.1.0'/'teodub'/'feature.py').is_file())
        self.assert_untouched()

    def test_only_the_three_newest_backups_are_kept(self):
        versions = self.project/'versions'
        versions.mkdir()
        for index in range(5):
            old = versions/f'0.{index}.0'
            (old/'teodub').mkdir(parents=True)
            (old/'teodub'/'__init__.py').write_text('')
            (old/'version.json').write_text(json.dumps(dict(version=f'0.{index}.0')))
            import os
            os.utime(old, (1000+index, 1000+index))
        self.assertTrue(self.apply()['ok'])
        names = sorted(path.name for path in versions.iterdir() if path.is_dir())
        self.assertEqual(len(names), 3)
        self.assertIn('1.0.0', names)

    def test_a_second_backup_of_the_same_version_does_not_overwrite_the_first(self):
        (self.project/'versions'/'1.0.0').mkdir(parents=True)
        (self.project/'versions'/'1.0.0'/'marker.txt').write_text('older backup')
        self.assertTrue(self.apply()['ok'])
        self.assertEqual((self.project/'versions'/'1.0.0'/'marker.txt').read_text(), 'older backup')
        self.assertTrue((self.project/'versions'/'1.0.0-2'/'teodub'/'feature.py').is_file())


if __name__ == '__main__':
    unittest.main()
