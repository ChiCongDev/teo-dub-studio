"""29/10/2026: a channel list and a campaign may hold up to 10.000 videos (was 1.000). Local fixtures only: no network,
browser, AI or Facebook; the channel pages are fake Playwright objects."""
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from teodub import campaign, campaign_batch, channel_discovery
from teodub.channel_discovery import MAX_SCAN_VIDEOS, scan_channel, scan_timeout

SETTINGS = dict(voice='Mai Anh', voice_provider='local', reading_style='legacy', speed=.9, original_gain=.1,
                translation_model='gpt-5.6-luna', translation_effort='low', burn_subtitles=True,
                subtitle_color='yellow', subtitle_weight='bold')


def rows(count):
    return [dict(url=f'https://www.douyin.com/video/{7000000000000000000+number}', title='') for number in range(count)]


class ScanLimitTests(unittest.TestCase):
    channel = 'https://www.douyin.com/user/ownerABC'

    def test_the_limit_is_ten_thousand(self):
        self.assertEqual(MAX_SCAN_VIDEOS, 10000)
        for limit in (1, 100, 1000, 1001, 10000):
            with self.subTest(limit=limit), patch.object(channel_discovery, '_scan_channel', return_value='ok') as scan, \
                    patch.object(channel_discovery, 'channel_scan_guard'):
                self.assertEqual(scan_channel(self.channel, Path('profile'), limit=limit, browser='chrome'), 'ok')
                self.assertEqual(scan.call_args.kwargs['limit'], limit)
        for limit in (0, -1, 10001, 100000, '100', 1.5):
            with self.subTest(limit=limit), patch.object(channel_discovery, '_scan_channel') as scan, \
                    patch.object(channel_discovery, 'channel_scan_guard'):
                with self.assertRaisesRegex(ValueError, 'từ 1 đến 10.000 video'):
                    scan_channel(self.channel, Path('profile'), limit=limit, browser='chrome')
                scan.assert_not_called()

    def test_reading_time_grows_with_the_list_and_never_drops_below_the_old_three_minutes(self):
        self.assertEqual([scan_timeout(limit) for limit in (1, 100, 300, 301)], [180, 180, 180, 181])
        self.assertEqual(scan_timeout(1000), 600)
        self.assertEqual(scan_timeout(10000), 6000)

    def test_an_explicit_time_is_passed_on_and_the_default_is_left_to_the_limit(self):
        with patch.object(channel_discovery, '_scan_channel', return_value='ok') as scan, \
                patch.object(channel_discovery, 'channel_scan_guard'):
            scan_channel(self.channel, Path('profile'), limit=5000, browser='chrome', timeout=30)
            self.assertEqual(scan.call_args.kwargs['timeout'], 30)
            scan_channel(self.channel, Path('profile'), limit=5000, browser='chrome')
            self.assertIsNone(scan.call_args.kwargs['timeout'])


class LoginWaitTests(unittest.TestCase):
    """A long list reads for longer, but waiting for the owner's login keeps its 3 minutes."""
    owner = 'ownerABC'
    channel = 'https://www.douyin.com/user/'+owner
    endpoint = 'https://www.douyin.com/aweme/v1/web/aweme/post'

    def gated_scan(self, **options):
        clock = [0.0]
        page = Mock(name='gated channel page')
        page.url = self.channel
        page.is_closed.return_value = False
        callbacks = {}
        page.on.side_effect = lambda event, handler: callbacks.setdefault(event, handler)
        response = Mock()
        response.url = self.endpoint+'/?sec_user_id='+self.owner
        response.json.return_value = {'aweme_list': [dict(aweme_id='123', author={'sec_uid': self.owner},
                                                          video={'duration': 10}, desc='Video 123')], 'has_more': 1}
        page.goto.side_effect = lambda *a, **k: callbacks['response'](response)
        page.wait_for_timeout.side_effect = lambda ms: clock.__setitem__(0, clock[0]+20)
        links = Mock()
        links.evaluate_all.return_value = []
        body = Mock()
        body.inner_text.return_value = '登录后查看更多作品'
        listing = Mock()
        listing.first.count.return_value = 0
        locators = {'[data-e2e="user-post-list"] a[href*="/video/"]': links, '[data-e2e="user-post-list"]': listing,
                    'body': body}
        page.locator.side_effect = locators.__getitem__
        context = Mock()
        context.pages = [page]
        manager = Mock()
        manager.__enter__ = Mock(return_value=Mock())
        manager.__exit__ = Mock(return_value=False)
        playwright = ModuleType('playwright')
        sync_api = ModuleType('playwright.sync_api')
        sync_api.sync_playwright = Mock(return_value=manager)
        errors = ModuleType('playwright._impl._errors')
        errors.TargetClosedError = type('TargetClosedError', (Exception,), {})
        playwright.sync_api = sync_api
        fake_time = SimpleNamespace(monotonic=lambda: clock[0], sleep=lambda s: None)
        with tempfile.TemporaryDirectory(prefix='teo login fixtures ') as directory, \
                patch.dict(sys.modules, {'playwright': playwright, 'playwright.sync_api': sync_api,
                                         'playwright._impl._errors': errors}), \
                patch.object(channel_discovery, 'launch_channel_browser', return_value=(context, 'chrome')), \
                patch.object(channel_discovery, 'time', fake_time):
            result = scan_channel(self.channel, Path(directory)/'profile', browser='chrome', visible=True, **options)
        return result, clock[0]

    def test_a_ten_thousand_list_still_waits_only_three_minutes_for_the_login(self):
        result, elapsed = self.gated_scan(limit=10000)
        self.assertEqual(result['reason'], 'login_required')
        self.assertEqual(len(result['items']), 1)
        self.assertLess(elapsed, 240)  # Not the 6000 s a 10.000-video list may read for.

    def test_a_small_list_waits_three_minutes_as_before(self):
        result, elapsed = self.gated_scan(limit=100)
        self.assertEqual(result['reason'], 'login_required')
        self.assertLess(elapsed, 240)

    def test_an_explicit_time_still_decides_how_long_the_login_is_waited_for(self):
        result, elapsed = self.gated_scan(limit=10000, timeout=600)
        self.assertEqual(result['reason'], 'login_required')
        self.assertGreaterEqual(elapsed, 600)
        self.assertLess(elapsed, 700)


class CampaignLimitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def create(self, count):
        return campaign.create_campaign(self.root, 'Lớn', rows(count), SETTINGS, str(self.root/'goc'), str(self.root/'xuat'))

    def test_a_campaign_takes_up_to_ten_thousand_videos_and_no_more(self):
        self.assertEqual(campaign.MAX_CAMPAIGN_VIDEOS, 10000)
        with self.assertRaisesRegex(ValueError, 'từ 1 đến 10.000 video'):
            self.create(10001)
        with self.assertRaisesRegex(ValueError, 'từ 1 đến 10.000 video'):
            self.create(0)
        self.assertFalse((self.root/'campaigns').exists())  # Refused before any file is written.

    def test_pasted_links_take_up_to_ten_thousand(self):
        text = '\n'.join(row['url'] for row in rows(10000))
        self.assertEqual(len(campaign.parse_links(text)), 10000)
        with self.assertRaisesRegex(ValueError, 'tối đa 10.000 video'):
            campaign.parse_links(text+'\nhttps://www.douyin.com/video/1')

    def test_a_ten_thousand_video_campaign_is_created_read_and_accepted_into_the_turns(self):
        directory = self.create(10000)
        manifest = campaign.read_json(directory/'campaign.json')
        self.assertEqual(len(manifest['items']), 10000)
        self.assertEqual(manifest['items'][-1]['number'], 10000)
        self.assertTrue((directory/'jobs'/'10000'/'config.json').is_file())
        self.assertEqual(len((directory/'links.txt').read_text(encoding='utf-8').split()), 10000)
        campaign_batch._identity(manifest, directory)  # No refusal for the number of videos.
        manifest['items'].append(dict(manifest['items'][0], number=10001))
        with self.assertRaises(ValueError):
            campaign_batch._identity(manifest, directory)


if __name__ == '__main__':
    unittest.main()
