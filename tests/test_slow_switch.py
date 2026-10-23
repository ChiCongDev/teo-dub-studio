"""A transfer that would take far too long hands the video to the next source; the last source always finishes."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from teodub.downloaders import browser_download
from teodub.downloaders.browser_download import MediaTransferError, download_media
from teodub.downloaders.download_errors import ProviderUnavailable
from teodub.downloaders.download_provider_registry import PROVIDERS

URL = 'https://v5-dy-ov-experiment.zjcdn.com/abc/video/tos/cn/x.mp4'
CHUNK = 256*1024
FIRST = b'\x00\x00\x00\x18ftypisom' + b'\x00'*(CHUNK-12)


class Clock:
    def __init__(self):
        self.now = 1000.

    def __call__(self):
        return self.now


class Response:
    """A CDN answer that delivers `chunks` pieces, each taking `seconds` of the fake clock."""

    def __init__(self, clock, chunks, seconds, length):
        self.clock, self.left, self.seconds = clock, chunks, seconds
        self.headers = {'Content-Type': 'video/mp4', 'Content-Length': str(length)}
        self.first = True

    def geturl(self):
        return URL

    def read(self, _size):
        if not self.left:
            return b''
        self.left -= 1
        self.clock.now += self.seconds
        if self.first:
            self.first = False
            return FIRST
        return b'\x00'*CHUNK

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class SlowTransferTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.target = Path(temporary.name)/'video.mp4'
        self.clock = Clock()
        # Short limits keep the arithmetic readable: judge after 10 s, over 10 s, switch beyond 100 s left.
        for name, value in (('SLOW_GRACE_S', 10), ('SLOW_WINDOW_S', 10), ('SLOW_REMAINING_S', 100),
                            ('SLOW_FLOOR_BPS', 50*1024)):
            patcher = patch.object(browser_download, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.object(browser_download.time, 'monotonic', self.clock)
        patcher.start()
        self.addCleanup(patcher.stop)

    def fetch(self, chunks, seconds, *, length=None, may_switch=True):
        response = Response(self.clock, chunks, seconds, chunks*CHUNK if length is None else length)
        opener = Mock()
        opener.open.return_value = response
        with patch.object(browser_download.urllib.request, 'build_opener', return_value=opener):
            download_media(URL, self.target, lambda: None, lambda _data: None, 2*1024**3, may_switch=may_switch)
        return response

    def test_a_transfer_that_would_need_far_too_long_stops_early_and_cleans_up(self):
        # 256 KB every 5 s is ~51 KB/s; the remaining ~95 MB would take about half an hour.
        with self.assertRaises(MediaTransferError) as caught:
            self.fetch(400, 5)
        self.assertEqual(caught.exception.reason, 'too_slow')
        self.assertIn('quá chậm', str(caught.exception))
        self.assertLess(self.clock.now-1000, 30)  # Decided right after the first full measuring window.
        self.assertFalse(self.target.exists())
        self.assertFalse(self.target.with_name('video.mp4.part').exists())

    def test_the_last_source_is_never_dropped_for_being_slow(self):
        self.fetch(60, 5, may_switch=False)
        self.assertEqual(self.target.stat().st_size, 60*CHUNK)

    def test_slow_but_soon_finished_and_fast_transfers_complete(self):
        self.fetch(25, 2)  # ~128 KB/s, but the rest needs well under 100 s.
        self.assertEqual(self.target.stat().st_size, 25*CHUNK)
        self.target.unlink()
        self.fetch(300, .01)
        self.assertEqual(self.target.stat().st_size, 300*CHUNK)

    def test_a_very_slow_but_tiny_file_still_completes(self):
        self.fetch(2, 9)  # ~28 KB/s, but nothing is left to wait for.
        self.assertEqual(self.target.stat().st_size, 2*CHUNK)

    def test_a_slow_start_is_forgiven_when_the_speed_recovers(self):
        response = Response(self.clock, 200, 4, 200*CHUNK)
        original = response.read
        def read(size):  # Two slow pieces within the first window, then ~5 MB/s; judged only once fast.
            if response.left <= 198:
                response.seconds = .05
            return original(size)
        response.read = read
        opener = Mock()
        opener.open.return_value = response
        with patch.object(browser_download.urllib.request, 'build_opener', return_value=opener):
            download_media(URL, self.target, lambda: None, lambda _data: None, 2*1024**3, may_switch=True)
        self.assertEqual(self.target.stat().st_size, 200*CHUNK)

    def test_without_a_size_only_a_crawl_switches(self):
        with self.assertRaises(MediaTransferError) as caught:
            self.fetch(400, 10, length=0)  # ~26 KB/s, under the 50 KB/s floor.
        self.assertEqual(caught.exception.reason, 'too_slow')
        self.target.with_name('video.mp4.part').unlink(missing_ok=True)
        self.fetch(40, 2, length=0)  # ~128 KB/s, above the floor: finishes.
        self.assertEqual(self.target.stat().st_size, 40*CHUNK)


class SwitchPermissionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.context = Mock()
        self.context.new_page.side_effect = lambda: Mock()

    def run_all(self, transfer, health=None):
        # Viện Phần Mềm hands over a finished browser download, so speed cannot be judged there; fail it plainly.
        browser_failure = MediaTransferError('hỏng', reason='transfer_error')
        with patch.object(browser_download, '_resolve_provider', return_value=dict(media_url=URL)), \
                patch.object(browser_download, 'download_media', side_effect=transfer) as download, \
                patch('teodub.downloaders.vienphanmem_download.transfer_vienphanmem', side_effect=browser_failure), \
                patch.object(browser_download, 'save_provider_failure'):
            result = browser_download.resolve_providers(self.context, URL, self.folder, lambda: None,
                lambda *_a, **_k: None, progress=Mock(), validate=Mock(return_value={}), health=health)
        return result, download

    def test_every_source_but_the_last_may_be_dropped_for_being_slow_and_the_next_one_is_used(self):
        def too_slow(_url, target, *_args, **kwargs):
            if kwargs.get('may_switch'):
                raise MediaTransferError('quá chậm', reason='too_slow')
            target.write_bytes(FIRST)
        result, download = self.run_all(too_slow)
        self.assertEqual(result['provider'], PROVIDERS[-1].id)
        flags = [call.kwargs.get('may_switch') for call in download.call_args_list]
        self.assertEqual(flags[-1], False)
        self.assertTrue(all(flags[:-1]))

    def test_a_slow_source_is_not_remembered_as_down(self):
        from teodub.downloaders.provider_health import ProviderHealth
        health = ProviderHealth(self.folder/'health.json')
        calls = []
        def first_slow(_url, target, *_args, **kwargs):
            calls.append(kwargs.get('may_switch'))
            if len(calls) == 1:
                raise MediaTransferError('quá chậm', reason='too_slow')
            target.write_bytes(FIRST)
        result, _download = self.run_all(first_slow, health)
        self.assertEqual(result['provider'], PROVIDERS[1].id)
        self.assertIsNone(health.unavailable(PROVIDERS[0].id))

    def test_when_every_later_source_is_paused_the_current_one_finishes(self):
        from teodub.downloaders.provider_health import ProviderHealth
        health = ProviderHealth(self.folder/'health.json')
        for service in PROVIDERS[1:]:
            health.record_failure(service.id, 'maintenance')
        seen = []
        def record(_url, target, *_args, **kwargs):
            seen.append(kwargs.get('may_switch'))
            target.write_bytes(FIRST)
        result, _download = self.run_all(record, health)
        self.assertEqual(result['provider'], PROVIDERS[0].id)
        self.assertEqual(seen, [False])


if __name__ == '__main__':
    unittest.main()
