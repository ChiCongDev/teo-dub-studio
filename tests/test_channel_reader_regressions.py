from pathlib import Path
import sys
import tempfile
from types import ModuleType
import unittest
from unittest.mock import Mock, patch

from teodub import channel_discovery


class ChannelReaderRegressionTests(unittest.TestCase):
    owner = 'ownerABC'
    channel = 'https://www.douyin.com/user/'+owner
    endpoint = 'https://www.douyin.com/aweme/v1/web/aweme/post'

    def video(self, ident='123'):
        return dict(aweme_id=ident, author={'sec_uid': self.owner},
                    video={'duration': 10}, desc='Video '+ident)

    def mocked_scan(self, response_url, payload, dom_rows=None):
        """Run the public scan API with an entirely fake Playwright module."""
        page = Mock(name='synthetic channel page')
        page.url = self.channel
        page.is_closed.side_effect = [False, True]
        callbacks = {}
        page.on.side_effect = lambda event, handler: callbacks.setdefault(event, handler)
        response = Mock(name='synthetic post response')
        response.url = response_url
        response.json.return_value = payload
        page.goto.side_effect = lambda *args, **kwargs: callbacks['response'](response)

        links = Mock(name='synthetic post anchors')
        links.evaluate_all.return_value = dom_rows or []
        body = Mock(name='synthetic body')
        body.inner_text.return_value = ''
        listing = Mock(name='synthetic listing')
        listing.first.count.return_value = 0
        locators = {
            '[data-e2e="user-post-list"] a[href*="/video/"]': links,
            '[data-e2e="user-post-list"]': listing,
            'body': body,
        }
        page.locator.side_effect = locators.__getitem__
        context = Mock(name='fake context')
        context.pages = [page]
        runtime = Mock(name='fake runtime')
        manager = Mock(name='fake Playwright manager')
        manager.__enter__ = Mock(return_value=runtime)
        manager.__exit__ = Mock(return_value=False)
        playwright = ModuleType('playwright')
        sync_api = ModuleType('playwright.sync_api')
        sync_api.sync_playwright = Mock(return_value=manager)
        errors = ModuleType('playwright._impl._errors')
        errors.TargetClosedError = type('TargetClosedError', (Exception,), {})
        playwright.sync_api = sync_api

        with tempfile.TemporaryDirectory(prefix='teo reader fixtures ') as directory, \
                patch.dict(sys.modules, {'playwright': playwright, 'playwright.sync_api': sync_api,
                                        'playwright._impl._errors': errors}), \
                patch.object(channel_discovery, 'launch_channel_browser',
                             return_value=(context, 'coccoc')) as launch:
            result = channel_discovery.scan_channel(
                self.channel, Path(directory)/'synthetic-app-profile',
                browser='coccoc', timeout=5)
            launch.assert_called_once()
            context.close.assert_called_once()
            return result

    def test_malformed_author_does_not_drop_later_valid_posts(self):
        for author in (None, [], 'ownerABC', 42):
            with self.subTest(author=author):
                malformed = dict(self.video('999'), author=author)
                posts = channel_discovery.ChannelPosts(self.owner, 10)
                posts.consume(self.endpoint+'/?sec_user_id='+self.owner,
                              {'aweme_list': [malformed, self.video()], 'has_more': 0})
                self.assertEqual(list(posts.items), ['123'])
                self.assertEqual(posts.items['123']['title'], 'Video 123')
                self.assertTrue(posts.complete)

    def test_response_callback_accepts_endpoint_without_trailing_slash(self):
        for suffix in ('', '/'):
            with self.subTest(suffix=suffix):
                result = self.mocked_scan(self.endpoint+suffix+'?sec_user_id='+self.owner,
                    {'aweme_list': [self.video()], 'has_more': 0})
                self.assertEqual(result['items'], [
                    {'url': 'https://www.douyin.com/video/123', 'title': 'Video 123'},
                ])
                self.assertEqual(result['reason'], 'end_of_public_posts')
                self.assertTrue(result['complete'])

    def test_dom_fallback_does_not_readd_an_api_identified_image_post(self):
        photo = dict(self.video('789'), images=[{'uri': 'synthetic-image'}])
        result = self.mocked_scan(self.endpoint+'/?sec_user_id='+self.owner,
            {'aweme_list': [photo, self.video()], 'has_more': 0}, dom_rows=[
                {'url': 'https://www.douyin.com/video/789', 'title': 'Photo title'},
                {'url': 'https://www.douyin.com/video/123', 'title': 'DOM duplicate'},
            ])
        self.assertEqual(result['items'], [
            {'url': 'https://www.douyin.com/video/123', 'title': 'Video 123'},
        ])
        self.assertEqual(result['skipped_image_posts'], 1)
        self.assertTrue(result['complete'])


if __name__ == '__main__':
    unittest.main()
