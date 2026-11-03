"""The owner-only tools: releasing a version and packaging the full bundle. No network, no real GitHub."""
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock
import urllib.error
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import package_bundle  # noqa: E402
import release  # noqa: E402
import run_tests  # noqa: E402


def run(*command, cwd=None):
    subprocess.run(command, cwd=cwd, check=True, capture_output=True)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload if isinstance(payload, bytes) else json.dumps(payload).encode()

    def read(self, limit=None):
        return self.payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class VersionAndNotesTests(unittest.TestCase):
    def test_bump(self):
        self.assertEqual(release.bump('1.2.3', 'patch'), '1.2.4')
        self.assertEqual(release.bump('1.2.3', 'minor'), '1.3.0')
        self.assertEqual(release.bump('1.2.3', 'major'), '2.0.0')

    def test_bad_versions_are_refused(self):
        for text in ('1.2', 'a.b.c', '1.2.3.4', ''):
            with self.subTest(text=text), self.assertRaises(release.ReleaseError):
                release.parse_version(text)

    def test_choose_version(self):
        self.assertEqual(release.choose_version('1.0.0', None), '1.0.1')
        self.assertEqual(release.choose_version('1.0.0', '1.0.1', requested='1.1.0'), '1.1.0')
        with self.assertRaises(release.ReleaseError):
            release.choose_version('1.0.0', '1.0.1', requested='1.0.1')  # not newer than the published one
        with self.assertRaises(release.ReleaseError):
            release.choose_version('1.2.0', None, requested='1.1.0')  # lower than the folder's own version
        self.assertEqual(release.choose_version('1.0.1', '1.0.0', requested='1.0.1'), '1.0.1')  # retry after a failed publish

    def test_notes_must_exist_and_be_reasonable(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'n.txt'
            path.write_text('- Sửa lỗi đăng bài', encoding='utf-8')
            self.assertIn('Sửa lỗi', release.read_notes(path))
            path.write_text('ok', encoding='utf-8')
            with self.assertRaises(release.ReleaseError):
                release.read_notes(path)
            path.write_text('x'*4000, encoding='utf-8')
            with self.assertRaises(release.ReleaseError):
                release.read_notes(path)
        self.assertIn('Thêm', release.read_notes(stream=io.StringIO('- Thêm nút cập nhật\n- Sửa lỗi\n\nbỏ qua\n')))

    def test_version_json_carries_min_from(self):
        data = json.loads(release.version_json_bytes('1.2.0', '1.0.0', today='2026-11-03'))
        self.assertEqual(data, dict(version='1.2.0', released='2026-11-03', min_from='1.0.0'))
        self.assertNotIn('min_from', json.loads(release.version_json_bytes('1.2.0')))


@unittest.skipUnless(os.name == 'nt', 'Windows DPAPI')
class TokenTests(unittest.TestCase):
    def test_token_round_trip_and_never_plain_text(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'sub'/'token.bin'
            release.save_token('ghp_secret_value_123', path)
            self.assertNotIn(b'ghp_secret_value_123', path.read_bytes())
            self.assertEqual(release.load_token(path), 'ghp_secret_value_123')

    def test_missing_token_says_how_to_set_it(self):
        with tempfile.TemporaryDirectory() as folder, self.assertRaises(release.ReleaseError) as caught:
            release.load_token(Path(folder)/'none.bin')
        self.assertIn('--set-token', str(caught.exception))


class GitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)/'repo'
        (self.repo/'teodub'/'__pycache__').mkdir(parents=True)
        (self.repo/'tests').mkdir()
        (self.repo/'scripts').mkdir()
        (self.repo/'teodub'/'__init__.py').write_text('')
        (self.repo/'teodub'/'__pycache__'/'x.pyc').write_bytes(b'junk')
        (self.repo/'tests'/'test_x.py').write_text('')
        (self.repo/'launch.py').write_text('# launch\n')
        (self.repo/'version.json').write_text(json.dumps(dict(version='1.0.0')))
        (self.repo/'requirements-lock.txt').write_text('numpy==1\n')
        (self.repo/'secret-notes.txt').write_text('not part of a release')
        run('git', 'init', '-b', 'main', str(self.repo))
        run('git', 'remote', 'add', 'origin', 'https://github.com/ChiCongDev/teo-dub-studio.git', cwd=self.repo)
        run('git', 'add', '-A', '-f', cwd=self.repo)
        run('git', '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-m', 'first', cwd=self.repo)

    def test_clean_repository_passes(self):
        self.assertEqual(release.check_repository(self.repo), [])

    def test_uncommitted_release_files_are_refused(self):
        (self.repo/'teodub'/'__init__.py').write_text('# changed')
        problems = release.check_repository(self.repo)
        self.assertEqual(len(problems), 1)
        self.assertIn('commit', problems[0])

    def test_other_branch_and_wrong_remote_are_refused(self):
        run('git', 'checkout', '-b', 'side', cwd=self.repo)
        run('git', 'remote', 'set-url', 'origin', 'https://github.com/someone/else.git', cwd=self.repo)
        self.assertEqual(len(release.check_repository(self.repo)), 2)

    def test_zip_has_only_release_files_with_the_new_version(self):
        out = Path(self.temp.name)/'out'/'teo-code-1.1.0.zip'
        digest, sha_file = release.build_zip(self.repo, out, release.version_json_bytes('1.1.0', today='2026-11-03'))
        with zipfile.ZipFile(out) as archive:
            names = set(archive.namelist())
            self.assertEqual(json.loads(archive.read('version.json'))['version'], '1.1.0')
        self.assertEqual(names, {'teodub/__init__.py', 'launch.py', 'version.json', 'requirements-lock.txt'})
        self.assertEqual(digest, hashlib.sha256(out.read_bytes()).hexdigest())
        self.assertEqual(sha_file.read_text().split()[0], digest)
        self.assertEqual(json.loads((self.repo/'version.json').read_text())['version'], '1.0.0')  # repo untouched

    def test_commits_use_a_neutral_identity(self):
        (self.repo/'version.json').write_text(json.dumps(dict(version='1.0.1')))
        release.git(self.repo, 'add', 'version.json')
        release.git(self.repo, 'commit', '-m', 'v', identity=True)
        author = release.git(self.repo, 'log', '-1', '--format=%an <%ae>')
        self.assertEqual(author, 'Teo Dub Studio <teo-dub-studio@users.noreply.github.com>')


class GitHubTests(unittest.TestCase):
    def test_only_github_addresses_are_called(self):
        with self.assertRaises(release.ReleaseError):
            release.api('GET', 'https://evil.example/x', 'token', opener=Mock())

    def test_token_is_sent_only_when_given(self):
        seen = []
        opener = lambda request, timeout: seen.append(request) or FakeResponse({})
        release.api('GET', 'https://api.github.com/repos/x', opener=opener)
        release.api('GET', 'https://api.github.com/repos/x', 'secret', opener=opener)
        self.assertNotIn('Authorization', seen[0].headers)
        self.assertEqual(seen[1].headers['Authorization'], 'Bearer secret')

    def test_http_error_becomes_a_readable_stop(self):
        def broken(request, timeout):
            raise urllib.error.HTTPError(request.full_url, 401, 'Bad credentials', {}, io.BytesIO(b'{"message":"Bad credentials"}'))
        with self.assertRaises(release.ReleaseError) as caught:
            release.api('POST', 'https://api.github.com/x', 'bad', {}, opener=broken)
        self.assertIn('401', str(caught.exception))

    def test_no_release_yet_means_none(self):
        def missing(request, timeout):
            raise urllib.error.HTTPError(request.full_url, 404, 'Not Found', {}, io.BytesIO(b'{}'))
        self.assertIsNone(release.latest_published_version(opener=missing))
        self.assertEqual(release.latest_published_version(opener=lambda r, timeout: FakeResponse(dict(tag_name='v1.2.3'))), '1.2.3')

    def test_release_is_created_published_and_tagged(self):
        seen = []
        opener = lambda request, timeout: seen.append(request) or FakeResponse(dict(upload_url='https://uploads.github.com/x/assets{?name,label}'))
        release.create_release('t', '1.1.0', '- ghi chú', 'abc123', opener=opener)
        body = json.loads(seen[0].data)
        self.assertEqual((body['tag_name'], body['draft'], body['prerelease'], body['target_commitish']), ('v1.1.0', False, False, 'abc123'))
        self.assertTrue(seen[0].full_url.endswith('/repos/ChiCongDev/teo-dub-studio/releases'))

    def test_assets_go_to_the_upload_address(self):
        seen = []
        opener = lambda request, timeout: seen.append(request) or FakeResponse({})
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'teo-code-1.1.0.zip'
            path.write_bytes(b'zip')
            release.upload_asset('t', dict(upload_url='https://uploads.github.com/repos/x/releases/1/assets{?name,label}'), path, 'application/zip', opener=opener)
        self.assertEqual(seen[0].full_url, 'https://uploads.github.com/repos/x/releases/1/assets?name=teo-code-1.1.0.zip')
        self.assertEqual(seen[0].data, b'zip')

    def test_verification_catches_missing_files_and_wrong_checksum(self):
        base = 'https://github.com/ChiCongDev/teo-dub-studio/releases/download/v1.1.0/'
        good = dict(tag_name='v1.1.0', assets=[dict(name='teo-code-1.1.0.zip', browser_download_url=base+'teo-code-1.1.0.zip'),
                                                dict(name='teo-code-1.1.0.zip.sha256', browser_download_url=base+'teo-code-1.1.0.zip.sha256')])

        def opener_for(data, sha):
            return lambda request, timeout: FakeResponse(sha.encode() if request.full_url.endswith('.sha256') else data)
        self.assertTrue(release.verify_published('1.1.0', 'a'*64, opener=opener_for(good, 'a'*64+'  x')))
        with self.assertRaises(release.ReleaseError):
            release.verify_published('1.1.0', 'a'*64, opener=opener_for(good, 'b'*64))
        with self.assertRaises(release.ReleaseError):
            release.verify_published('1.1.0', 'a'*64, opener=opener_for(dict(tag_name='v1.1.0', assets=[]), 'a'*64))
        with self.assertRaises(release.ReleaseError):
            release.verify_published('1.2.0', 'a'*64, opener=opener_for(good, 'a'*64))


class TestGateTests(unittest.TestCase):
    def test_only_known_failures_are_tolerated(self):
        results = [dict(failed=['a.A.t1', 'b.B.t2']), dict(failed=['w.W.t3'])]
        self.assertEqual(release.unexpected_failures(results, {'a.A.t1', 'w.W.t3'}), ['b.B.t2'])
        self.assertEqual(release.unexpected_failures(results, {'a.A.t1', 'b.B.t2', 'w.W.t3'}), [])
        prefixed = [dict(failed=['tests.a.A.t1'])]
        self.assertEqual(release.unexpected_failures(prefixed, {'a.A.t1'}), [])
        self.assertEqual(release.unexpected_failures(prefixed, set()), ['a.A.t1'])

    def test_known_failures_file_ignores_comments(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'k.txt'
            path.write_text('# note\n\nmod.Cls.test_a\n', encoding='utf-8')
            self.assertEqual(release.load_known_failures(path), {'mod.Cls.test_a'})
        self.assertEqual(release.load_known_failures(Path('does-not-exist.txt')), set())

    def test_shipped_known_failures_names_look_like_tests(self):
        for name in release.load_known_failures():
            self.assertRegex(name, r'^test_\w+\.\w+\.test_\w+$')

    def test_web_tests_are_separated_from_the_rest(self):
        web = type('W', (), {'__module__': 'test_web_portal'})()
        other = type('O', (), {'__module__': 'test_ui'})()
        failed_import = type('F', (), {'__module__': 'unittest.loader', 'id': lambda self: 'unittest.loader._FailedTest.test_web_address'})()
        self.assertEqual(run_tests.module_of(web), 'test_web_portal')
        self.assertEqual(run_tests.module_of(other), 'test_ui')
        self.assertEqual(run_tests.module_of(failed_import), 'test_web_address')


class BundleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        project = self.project = self.root/'project'
        (project/'teodub'/'__pycache__').mkdir(parents=True)
        (project/'teodub'/'__init__.py').write_text('')
        (project/'teodub'/'__pycache__'/'x.pyc').write_bytes(b'x')
        (project/'launch.py').write_text('')
        (project/'version.json').write_text('{"version": "1.0.0"}')
        (project/'requirements-lock.txt').write_text('numpy==1\n')
        (project/'assets'/'voice-references').mkdir(parents=True)
        (project/'assets'/'voice-references'/'private.wav').write_bytes(b'private')
        (project/'.venv-inpaint').mkdir()
        self.models = project/'models'
        for relative in package_bundle.MODEL_DIRS+('whisper/models--Systran--faster-whisper-large-v3',):
            (self.models/relative/'snapshots'/'abc').mkdir(parents=True)
            (self.models/relative/'snapshots'/'abc'/'model.bin').write_bytes(b'm')
        self.python = self.root/'py'
        self.python.mkdir()
        (self.python/'python.exe').write_bytes(b'p')
        self.codex = self.root/'codex'
        self.codex.mkdir()
        (self.codex/'codex.exe').write_bytes(b'c')
        self.node = self.root/'node'
        self.node.mkdir()
        (self.node/'node.exe').write_bytes(b'n')

    def names(self, **kwargs):
        found, _ = package_bundle.model_sources(self.models)
        return {name for _, name in package_bundle.iter_files(self.project, self.python, found, self.models, self.codex, self.node, **kwargs)}

    def test_cpu_list_drops_nvidia_wheels(self):
        self.assertEqual(package_bundle.cpu_requirements(['numpy==1', 'nvidia-cublas-cu12==12', '', 'NVIDIA-cudnn==9', 'torch==2']),
                         ['numpy==1', 'torch==2'])

    def test_model_sources_report_what_is_missing(self):
        found, missing = package_bundle.model_sources(self.models)
        self.assertEqual((len(found), missing), (5, []))
        import shutil
        shutil.rmtree(self.models/package_bundle.MODEL_DIRS[0])
        self.assertEqual(package_bundle.model_sources(self.models)[1], [package_bundle.MODEL_DIRS[0]])

    def test_bundle_has_what_a_new_user_needs_and_nothing_private_or_heavy(self):
        names = self.names()
        for needed in ('Teo/teodub/__init__.py', 'Teo/launch.py', 'Teo/version.json', 'Teo/requirements-lock.txt',
                       'Teo/runtime/python/python.exe', 'Teo/tools/codex/codex.exe', 'Teo/tools/node-runtime/node.exe',
                       'Teo/models/whisper/models--Systran--faster-whisper-small/snapshots/abc/model.bin'):
            self.assertIn(needed, names)
        self.assertFalse([name for name in names if 'large-v3' in name or 'voice-references' in name
                          or '.venv' in name or '__pycache__' in name or name.endswith('.pyc')])

    def test_models_and_runtime_can_be_left_out(self):
        names = self.names(include_models=False, include_runtime=False)
        self.assertFalse([name for name in names if '/models/' in name or '/runtime/' in name])

    def test_zip_gets_a_launcher_and_instructions(self):
        out = self.root/'out'/'bundle.zip'
        found, _ = package_bundle.model_sources(self.models)
        package_bundle.write_zip(package_bundle.iter_files(self.project, self.python, found, self.models, self.codex, self.node), out, say=lambda text: None)
        with zipfile.ZipFile(out) as archive:
            launcher = archive.read('Teo/Teo Dub Studio.cmd').decode('utf-8')
            self.assertIn('runtime\\python\\pythonw.exe', launcher)
            self.assertIn('C:\\Teo', archive.read('Teo/DOC-TRUOC-KHI-MO.txt').decode('utf-8'))
            self.assertIsNone(archive.testzip())


if __name__ == '__main__':
    unittest.main()
