"""Portable download boundaries and provider fallback; no network/browser runs."""
import ast
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from teodub.common import Cancelled, FileCancel, hash_file
from teodub.download import download_video
from teodub.downloaders import browser_download, video_download
from teodub.downloaders.download_errors import ProviderUnavailable
from teodub.downloaders.download_provider_registry import PROVIDERS


URL = 'https://v.douyin.com/Example123/'
BODY = b'fixture video bytes for adapter hash verification'


class DownloadAdapterTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.originals = self.root/'Video gốc'
        self.job = self.root/'work'/'one'
        self.cancel = FileCancel(self.root/'stop')
        self.events = []

    def metadata(self, path, *_):
        return dict(path=str(path), sha256=hash_file(path), duration=12.5, width=1080, height=1920,
                    has_audio=True, audio_decoded_samples=16000, decoded_frames=375)

    def fake_browser(self, url, folder, check, progress, emit, validate=None):
        check()
        path = Path(folder)/'video.mp4'
        path.write_bytes(BODY)
        progress(dict(status='finished', downloaded_bytes=len(BODY), total_bytes=len(BODY)))
        return dict(title='Đèn 太阳能', provider='savetik', verified_metadata=validate(path))

    def run_download(self, **kwargs):
        return download_video(URL, self.originals, self.job, cancel=self.cancel,
                              progress=lambda text, percent=None: self.events.append((text, percent)), **kwargs)

    def test_public_adapter_dates_name_verifies_source_and_preserves_progress(self):
        date = datetime(2026, 9, 29, 10, tzinfo=timezone(timedelta(hours=7)))
        with patch.object(browser_download, 'download_via_browser', side_effect=self.fake_browser) as browser, \
                patch.object(video_download, 'validate_downloaded_video', side_effect=self.metadata), \
                patch.object(video_download, 'datetime') as clock:
            clock.now.return_value.astimezone.return_value = date
            result = self.run_download()
        browser.assert_called_once()
        source = Path(result['source_path'])
        self.assertTrue(source.is_absolute())
        self.assertEqual(source.parent.parent, self.originals)
        self.assertEqual(source.name, 'Đèn 太阳能_29-09-2026.mp4')
        self.assertEqual(source.read_bytes(), BODY)
        self.assertEqual(result['metadata']['sha256'], hash_file(source))
        self.assertEqual(result['metadata']['path'], str(source))
        self.assertTrue(any(percent == 100 for _, percent in self.events))
        self.assertEqual(json.loads((self.job/'download/result.json').read_text(encoding='utf-8')), result)
        self.assertEqual(json.loads((source.parent/'result.json').read_text(encoding='utf-8')), result)

    def test_exact_link_reuses_hash_checked_dated_cache_without_network(self):
        with patch.object(browser_download, 'download_via_browser', side_effect=self.fake_browser), \
                patch.object(video_download, 'validate_downloaded_video', side_effect=self.metadata):
            first = self.run_download()
        original = Path(first['source_path'])
        original_report = (original.parent/'result.json').read_bytes()
        self.job = self.root/'work'/'two'
        with patch.object(browser_download, 'download_via_browser') as browser, \
                patch.object(video_download, 'validate_downloaded_video', side_effect=self.metadata) as validate:
            second = self.run_download()
        browser.assert_not_called()
        validate.assert_called_once()
        self.assertEqual(second['provider'], 'local_cache')
        self.assertEqual(second['cache_source'], str(original))
        self.assertNotEqual(second['source_path'], first['source_path'])
        self.assertEqual(Path(second['source_path']).read_bytes(), BODY)
        self.assertEqual((original.parent/'result.json').read_bytes(), original_report)

    def test_changed_cache_is_not_used_and_old_video_is_not_modified(self):
        with patch.object(browser_download, 'download_via_browser', side_effect=self.fake_browser), \
                patch.object(video_download, 'validate_downloaded_video', side_effect=self.metadata):
            first = self.run_download()
            original = Path(first['source_path'])
            original.write_bytes(b'changed by user')
            self.job = self.root/'work'/'two'
            with patch.object(browser_download, 'download_via_browser', side_effect=self.fake_browser) as browser:
                second = self.run_download()
        browser.assert_called_once()
        self.assertEqual(second['provider'], 'savetik')
        self.assertEqual(original.read_bytes(), b'changed by user')

    def test_tiktok_uses_direct_adapter_without_browser(self):
        class FakeYDL:
            def __init__(self, options): self.options = options
            def __enter__(self): return self
            def __exit__(self, *_): pass
            def extract_info(self, url, download=False): return dict(title='TikTok source')
            def process_info(self, info):
                Path(self.options['outtmpl'].replace('%(ext)s', 'mp4')).write_bytes(BODY)
        with patch('yt_dlp.YoutubeDL', FakeYDL), \
                patch.object(browser_download, 'download_via_browser') as browser, \
                patch.object(video_download, 'validate_downloaded_video', side_effect=self.metadata):
            result = download_video('https://www.tiktok.com/@test/video/123', self.originals, self.job)
        browser.assert_not_called()
        self.assertEqual(result['provider'], 'direct')
        self.assertTrue(Path(result['source_path']).is_file())

    def test_pre_cancel_does_not_create_a_job_or_contact_network(self):
        self.cancel.path.touch()
        with patch('teodub.download._download') as engine:
            with self.assertRaises(Cancelled):
                self.run_download()
        engine.assert_not_called()
        self.assertFalse(self.job.exists())

    def test_cancel_after_provider_returns_never_publishes_a_successful_cache(self):
        def cancelled_browser(*args, **kwargs):
            info = self.fake_browser(*args, **kwargs)
            self.cancel.path.touch()
            return info
        with patch.object(browser_download, 'download_via_browser', side_effect=cancelled_browser), \
                patch.object(video_download, 'validate_downloaded_video', side_effect=self.metadata):
            with self.assertRaises(Cancelled):
                self.run_download()
        result = json.loads((self.job/'download/result.json').read_text(encoding='utf-8'))
        self.assertEqual(result['status'], 'cancelled')
        original = next(self.originals.iterdir())
        self.assertEqual(json.loads((original/'result.json').read_text(encoding='utf-8'))['status'], 'cancelled')

    def test_reusing_download_job_cannot_overwrite_or_download_twice(self):
        with patch.object(browser_download, 'download_via_browser', side_effect=self.fake_browser), \
                patch.object(video_download, 'validate_downloaded_video', side_effect=self.metadata):
            self.run_download()
        before = (self.job/'download/result.json').read_bytes()
        with patch('teodub.download._download') as engine:
            with self.assertRaises(FileExistsError):
                self.run_download()
        engine.assert_not_called()
        self.assertEqual((self.job/'download/result.json').read_bytes(), before)

    def test_missing_metadata_or_external_media_path_fails_before_handoff(self):
        for name in ('invalid_metadata', 'external_path', 'changed_hash'):
            with self.subTest(name=name):
                self.job = self.root/'work'/name
                def invalid(config, **_):
                    folder = Path(config['folder'])
                    folder.mkdir(parents=True)
                    target = folder/'video.mp4' if name != 'external_path' else self.root/'outside.mp4'
                    target.write_bytes(BODY)
                    metadata = self.metadata(target)
                    if name == 'invalid_metadata': metadata['duration'] = 0
                    if name == 'changed_hash': metadata['sha256'] = '0'*64
                    return dict(status='downloaded', video=str(target), metadata=metadata)
                with patch('teodub.download._download', side_effect=invalid):
                    with self.assertRaises(ValueError):
                        self.run_download()
                self.assertEqual(json.loads((self.job/'download/result.json').read_text(encoding='utf-8'))['status'], 'failed')

    def test_share_links_are_normalized_and_invalid_input_stops_before_downloader(self):
        self.assertEqual(video_download.share_url('复制 [Video]('+URL+') 打开'), URL)
        for text in ('file:///C:/secret', 'https://www.tiktok.com/@profile', URL+' https://v.douyin.com/Other/'):
            with self.subTest(text=text), patch('teodub.download._download') as engine:
                with self.assertRaises(ValueError):
                    download_video(text, self.originals, self.job)
                engine.assert_not_called()

    def test_no_runtime_import_of_old_project_or_desktop_automation(self):
        import teodub.download as adapter
        files = [Path(adapter.__file__), *Path(adapter.__file__).with_name('downloaders').glob('*.py')]
        for file in files:
            for node in ast.walk(ast.parse(file.read_text(encoding='utf-8'))):
                names = ([node.module] if isinstance(node, ast.ImportFrom) else
                         [n.name for n in node.names] if isinstance(node, ast.Import) else [])
                for name in names:
                    self.assertNotIn((name or '').split('.')[0], ('teo', 'pywinauto', 'winrt', 'comtypes'))


class ProviderFallbackTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name)
        self.context = Mock()
        self.context.new_page.side_effect = lambda: Mock()

    def resolve(self, **kwargs):
        return browser_download.resolve_providers(self.context, URL, self.folder, lambda: None,
            lambda *_a, **_k: None, **kwargs)

    def test_douyins_own_page_comes_first_then_the_four_sites_each_attempted_once(self):
        ids = [p.id for p in PROVIDERS]
        self.assertEqual(ids, ['douyin', 'savetik', 'vienphanmem', 'socialvideo', 'anysave'])
        errors = [ProviderUnavailable('unavailable', provider=p, reason='provider_error') for p in ids[:4]]
        with patch.object(browser_download, '_resolve_provider', side_effect=errors+[dict(media_url='https://example.invalid/video')]) as resolver, \
                patch.object(browser_download, 'save_provider_failure'):
            result = self.resolve()
        self.assertEqual([call.args[0] for call in resolver.call_args_list], ids)
        self.assertEqual(result['provider'], 'anysave')
        self.assertEqual(len(result['provider_attempts']), 5)

    def test_first_success_prevents_other_provider_submissions(self):
        with patch.object(browser_download, '_resolve_provider', return_value=dict(media_url='fixture')) as resolver:
            result = self.resolve()
        resolver.assert_called_once()
        self.assertEqual(result['provider'], 'douyin')

    def test_cancellation_and_verification_challenges_never_fall_back(self):
        for error in (Cancelled('stop'), ProviderUnavailable('verify', provider='savetik', reason='verification_required'),
                      ProviderUnavailable('slow down', provider='savetik', reason='rate_limited')):
            with self.subTest(error=error), patch.object(browser_download, '_resolve_provider', side_effect=error) as resolver, \
                    patch.object(browser_download, 'save_provider_failure'):
                with self.assertRaises(type(error)):
                    self.resolve()
                resolver.assert_called_once()

    def test_invalid_first_media_advances_before_publication_and_cleans_only_owned_candidate(self):
        def transfer(_url, target, *_args, **_kwargs): target.write_bytes(BODY)
        with patch.object(browser_download, '_resolve_provider', return_value=dict(media_url='fixture')), \
                patch.object(browser_download, 'download_media', side_effect=transfer), \
                patch('teodub.downloaders.vienphanmem_download.transfer_vienphanmem', side_effect=transfer), \
                patch.object(browser_download, 'save_provider_failure'):
            validate = Mock(side_effect=[browser_download.MediaTransferError('bad data'), dict(sha256='verified')])
            result = self.resolve(progress=Mock(), validate=validate)
        self.assertEqual(result['provider'], 'savetik')
        self.assertEqual(validate.call_count, 2)
        self.assertEqual((self.folder/'video.mp4').read_bytes(), BODY)
        self.assertFalse(list((self.folder/'attempts').rglob('video.mp4')))


if __name__ == '__main__':
    unittest.main()
