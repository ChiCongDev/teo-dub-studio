"""Finding, downloading and verifying a release, and starting the update; no network, no real update."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import zipfile

from teodub import updater

BASE = updater.DOWNLOAD_PREFIX+'v1.2.0/'


def release(tag='v1.2.0', assets=None, body='- Sửa lỗi đăng bài\n- Thêm nút kiểm tra máy'):
    if assets is None:
        assets = [dict(name='teo-code-1.2.0.zip', browser_download_url=BASE+'teo-code-1.2.0.zip'),
                  dict(name='teo-code-1.2.0.zip.sha256', browser_download_url=BASE+'teo-code-1.2.0.zip.sha256')]
    return dict(tag_name=tag, body=body, assets=assets)


def make_zip(path, version='1.2.0', min_from=None):
    manifest = dict(version=version)
    if min_from:
        manifest['min_from'] = min_from
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('version.json', json.dumps(manifest))
        archive.writestr('teodub/__init__.py', '')
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class CheckTests(unittest.TestCase):
    def check(self, data, current='1.0.0'):
        return updater.check(current, fetch=lambda url, timeout: data)

    def test_newer_release_is_reported_with_notes(self):
        result = self.check(release())
        self.assertEqual((result['status'], result['version']), ('newer', '1.2.0'))
        self.assertIn('Sửa lỗi đăng bài', result['notes'])
        self.assertTrue(result['zip_url'].startswith(updater.DOWNLOAD_PREFIX))

    def test_same_or_older_release_is_current(self):
        self.assertEqual(self.check(release(), current='1.2.0')['status'], 'current')
        self.assertEqual(self.check(release(), current='1.3.0')['status'], 'current')

    def test_offline_or_garbage_is_a_quiet_error_not_a_crash(self):
        def offline(url, timeout):
            raise OSError('no network')
        self.assertEqual(updater.check('1.0.0', fetch=offline)['status'], 'error')
        self.assertEqual(self.check({'tag_name': 'not-a-version'})['status'], 'error')
        self.assertEqual(self.check([])['status'], 'error')

    def test_no_release_yet_is_not_an_error(self):
        def missing(url, timeout):
            raise updater.urllib.error.HTTPError(url, 404, 'Not Found', {}, None)
        result = updater.check('1.0.0', fetch=missing)
        self.assertEqual(result['status'], 'current')
        self.assertIn('Chưa có', result['message'])

    def test_other_http_errors_are_reported(self):
        def limited(url, timeout):
            raise updater.urllib.error.HTTPError(url, 403, 'rate limit', {}, None)
        self.assertEqual(updater.check('1.0.0', fetch=limited)['status'], 'error')

    def test_release_without_the_expected_zip_is_not_offered(self):
        self.assertEqual(self.check(release(assets=[]))['status'], 'current')
        wrong = [dict(name='teo-code-9.9.9.zip', browser_download_url=BASE+'teo-code-9.9.9.zip')]
        self.assertEqual(self.check(release(assets=wrong))['status'], 'current')

    def test_download_address_outside_the_repository_is_refused(self):
        evil = [dict(name='teo-code-1.2.0.zip', browser_download_url='https://evil.example/teo-code-1.2.0.zip')]
        self.assertEqual(self.check(release(assets=evil))['status'], 'error')

    def test_the_request_carries_no_account_detail(self):
        with patch.object(updater.urllib.request, 'urlopen') as opener:
            opener.return_value.__enter__.return_value.read.return_value = json.dumps(release()).encode()
            updater.fetch_json(updater.API, 5)
        request = opener.call_args.args[0]
        self.assertEqual(set(key.lower() for key in request.headers), {'user-agent', 'accept'})

    def test_check_in_background_reports_to_the_callback(self):
        done = []
        with patch.object(updater, 'check', return_value=dict(status='current')):
            updater.check_in_background(done.append, current='1.0.0')
            for _ in range(100):
                if done:
                    break
                import time
                time.sleep(0.02)
        self.assertEqual(done, [dict(status='current')])


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.info = dict(version='1.2.0', current='1.0.0', zip_url=BASE+'teo-code-1.2.0.zip', sha_url=BASE+'teo-code-1.2.0.zip.sha256')
        self.sha = ''

    def get_file(self, url, target, timeout, progress):
        self.sha = make_zip(target)

    def run_download(self, sha_text=None, get_file=None):
        get_text = lambda url, timeout: sha_text if sha_text is not None else self.sha_for_text()
        return updater.download(self.info, self.folder, get_file=get_file or self.get_file, get_text=get_text)

    def sha_for_text(self):
        probe = self.folder/'probe.zip'
        value = make_zip(probe)
        probe.unlink()
        return value+'  teo-code-1.2.0.zip\n'

    def test_good_download_is_verified(self):
        path, sha = self.run_download()
        self.assertTrue(path.is_file())
        self.assertEqual(len(sha), 64)

    def test_damaged_download_is_refused_and_deleted(self):
        with self.assertRaises(ValueError) as caught:
            self.run_download(sha_text='f'*64)
        self.assertIn('SHA256', str(caught.exception))
        self.assertFalse((self.folder/'teo-code-1.2.0.zip').exists())

    def test_release_without_a_checksum_is_refused(self):
        self.info['sha_url'] = ''
        with self.assertRaises(ValueError):
            self.run_download()

    def test_network_failure_while_downloading_is_a_clear_message(self):
        def broken(url, target, timeout, progress):
            raise OSError('connection reset')
        with self.assertRaises(ValueError) as caught:
            self.run_download(get_file=broken)
        self.assertIn('Chưa đổi gì', str(caught.exception))

    def test_package_that_needs_a_newer_start_is_refused_before_any_change(self):
        def gated(url, target, timeout, progress):
            make_zip(target, min_from='1.1.0')
        with self.assertRaises(ValueError) as caught:
            updater.download(self.info, self.folder, get_file=gated,
                             get_text=lambda url, timeout: make_zip(self.folder/'x.zip', min_from='1.1.0'))
        self.assertIn('1.1.0', str(caught.exception))


class EnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)

    def test_the_project_owners_git_clone_never_updates_itself(self):
        (self.project/'.git').mkdir()
        allowed, why = updater.can_update_here(self.project)
        self.assertFalse(allowed)
        self.assertIn('máy chủ dự án', why)

    def test_a_bundle_with_python_may_update(self):
        (self.project/'runtime'/'python').mkdir(parents=True)
        (self.project/'runtime'/'python'/'python.exe').touch()
        self.assertTrue(updater.can_update_here(self.project)[0])

    def test_no_python_means_no_update(self):
        self.assertFalse(updater.can_update_here(self.project)[0])

    def test_previous_versions_lists_saved_ones_newest_first(self):
        for name, stamp in (('1.0.0', 100), ('1.1.0', 200)):
            folder = self.project/'versions'/name
            (folder/'teodub').mkdir(parents=True)
            (folder/'teodub'/'__init__.py').touch()
            (folder/'version.json').write_text(json.dumps(dict(version=name)))
            import os
            os.utime(folder, (stamp, stamp))
        (self.project/'versions'/'_staging').mkdir()
        self.assertEqual([version for version, path in updater.previous_versions(self.project)], ['1.1.0', '1.0.0'])

    def test_last_result_is_shown_once(self):
        (self.project/'versions').mkdir()
        (self.project/'versions'/'last_update.json').write_text(json.dumps(dict(ok=True, message='Đã cập nhật lên bản 1.1.0.')), encoding='utf-8')
        self.assertTrue(updater.pop_last_result(self.project)['ok'])
        self.assertIsNone(updater.pop_last_result(self.project))

    def test_workers_that_cannot_be_read_count_as_busy(self):
        with patch.object(updater.subprocess, 'run', side_effect=OSError('no powershell')):
            self.assertEqual(updater.running_workers(self.project), ['?'])

    def test_start_apply_runs_a_copy_with_the_runtime_python_and_a_job_file(self):
        (self.project/'runtime'/'python').mkdir(parents=True)
        for name in ('python.exe', 'pythonw.exe'):
            (self.project/'runtime'/'python'/name).touch()
        with patch.object(updater.subprocess, 'Popen') as spawn:
            updater.start_apply(self.project, zip_path=self.project/'a.zip', sha256='a'*64, new_version='1.1.0',
                                data_dir=self.project/'data', parent_pid=77)
        command = spawn.call_args.args[0]
        self.assertEqual(command[0], str(self.project/'runtime'/'python'/'python.exe'))
        self.assertIn('-I', command)
        script = Path(command[-2])
        self.assertEqual(script.name, 'update_apply.py')
        self.assertNotIn(str(self.project), str(script))  # runs from a temp copy, not from the code it replaces
        job = json.loads(Path(command[-1]).read_text(encoding='utf-8'))
        self.assertEqual((job['new_version'], job['parent_pid'], job['sha256']), ('1.1.0', 77, 'a'*64))
        self.assertEqual(job['relaunch'][0], str(self.project/'runtime'/'python'/'pythonw.exe'))
        self.assertIn('--data-dir', job['relaunch'])


if __name__ == '__main__':
    unittest.main()
