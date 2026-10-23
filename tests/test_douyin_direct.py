"""Douyin's own video page as the first download source, against offline fixtures.

Every request is answered by the test's route: no network, no login, no cookies, no profile.
"""
import json
import time
import unittest
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import sync_playwright

from teodub.common import Cancelled
from teodub.downloaders.browser_download import is_public_media_url
from teodub.downloaders.douyin_direct import aweme_id, choose_stream, resolve_douyin
from teodub.downloaders.download_errors import ProviderUnavailable

ID = '7694990343825050914'
PAGE = 'https://www.douyin.com/video/' + ID
CDN = 'https://v5-dy-ov-experiment.zjcdn.com/abc/video/tos/cn/{}.mp4'
MAX = 2*1024**3


def rate(height, bit_rate, name, size=None, urls=None):
    address = dict(height=height, width=height*16//9, url_list=urls or [CDN.format(name)])
    if size is not None:
        address['data_size'] = size
    return dict(gear_name=name, bit_rate=bit_rate, play_addr=address)


def detail(ident=ID, desc='一个人背包环球旅行\n第二行', rates=None, plain=None):
    video = dict(bit_rate=rates if rates is not None else [
        rate(720, 900000, 'p720'), rate(1080, 3500000, 'h264_1080'), rate(1080, 1700000, 'hevc_1080')])
    video['play_addr'] = plain if plain is not None else dict(height=720, url_list=[CDN.format('plain')])
    return dict(status_code=0, aweme_detail=dict(aweme_id=ident, desc=desc, video=video))


class StreamChoiceTests(unittest.TestCase):
    def test_ids_are_read_only_from_douyin_video_links(self):
        self.assertEqual(aweme_id(PAGE), ID)
        self.assertEqual(aweme_id(PAGE+'/?previous_page=web'), ID)
        self.assertEqual(aweme_id('https://www.douyin.com/jingxuan?modal_id='+ID), ID)
        self.assertEqual(aweme_id('https://www.iesdouyin.com/share/video/'+ID+'/'), ID)
        for other in ('https://www.douyin.com/jingxuan', 'https://example.com/video/'+ID,
                      'https://v.douyin.com/AbC/', 'https://www.douyin.com/user/abc', 'http://www.douyin.com/video/'+ID,
                      None, '', 42):
            with self.subTest(other=other):
                self.assertIsNone(aweme_id(other))

    def test_the_best_picture_is_chosen_highest_height_then_bitrate(self):
        stream = choose_stream(detail()['aweme_detail'], is_public_media_url, MAX)
        self.assertTrue(stream['url'].endswith('h264_1080.mp4'))
        self.assertEqual((stream['height'], stream['bit_rate']), (1080, 3500000))

    def test_addresses_outside_douyins_cdn_are_never_used(self):
        rates = [rate(1080, 9000000, 'bad', urls=['https://www.douyin.com/aweme/v1/play/?video_id=1',
                                                   'http://v5.zjcdn.com/insecure.mp4',
                                                   'https://zjcdn.com.example.org/x.mp4']),
                 rate(720, 800000, 'good')]
        stream = choose_stream(detail(rates=rates)['aweme_detail'], is_public_media_url, MAX)
        self.assertTrue(stream['url'].endswith('good.mp4'))

    def test_a_variant_above_the_size_limit_falls_back_to_a_smaller_one(self):
        rates = [rate(1080, 3500000, 'big', size=3*1024**3), rate(1080, 1700000, 'fits', size=500*1024**2)]
        stream = choose_stream(detail(rates=rates)['aweme_detail'], is_public_media_url, MAX)
        self.assertTrue(stream['url'].endswith('fits.mp4'))
        self.assertEqual(stream['size'], 500*1024**2)
        huge = dict(height=720, data_size=4*1024**3, url_list=[CDN.format('huge')])
        self.assertIsNone(choose_stream(detail(rates=rates[:1], plain=huge)['aweme_detail'], is_public_media_url, MAX))

    def test_no_usable_address_gives_none_and_odd_data_never_raises(self):
        self.assertIsNone(choose_stream(detail(rates=[], plain={})['aweme_detail'], is_public_media_url, MAX))
        for odd in ({}, {'video': None}, {'video': {'bit_rate': 'x', 'play_addr': 5}},
                    {'video': {'bit_rate': [None, 3, {'play_addr': {'url_list': [None, 7]}}]}}):
            with self.subTest(odd=odd):
                self.assertIsNone(choose_stream(odd, is_public_media_url, MAX))


class ResolveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runtime = sync_playwright().start()
        # A fresh ephemeral test browser. No persistent profile, no network.
        cls.browser = cls.runtime.chromium.launch(channel='chrome', headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.runtime.stop()

    def setUp(self):
        self.context = self.browser.new_context()
        self.addCleanup(self.context.close)
        self.answers, self.detail_calls, self.documents, self.others = [], [], [], []
        self.page_script(asks=[ID])
        self.context.route('**/*', self.route)

    def page_script(self, asks, after='', title='抖音'):
        self.html = ('<!doctype html><meta charset="utf-8"><title>%s</title><script>(async () => {'
                     'for (const id of %s) { const r = await fetch("/aweme/v1/web/aweme/detail/?device_platform=webapp&aweme_id=" + id);'
                     ' if (r.ok && id === "%s") break; } %s })();</script>') % (title, json.dumps(asks), ID, after)

    def route(self, request_route):
        url = request_route.request.url
        parsed = urlsplit(url)
        if parsed.hostname == 'v.douyin.com':
            self.documents.append(url)
            request_route.fulfill(status=302, headers={'location': PAGE})
        elif parsed.path.startswith('/aweme/v1/web/aweme/detail/'):
            self.detail_calls.append(parse_qs(parsed.query).get('aweme_id', [''])[0])
            status, body = self.answers.pop(0) if self.answers else (404, {})
            request_route.fulfill(status=status, content_type='application/json', body=json.dumps(body))
        elif request_route.request.resource_type == 'document':
            self.documents.append(url)
            body = self.html if parsed.path.startswith('/video/') else '<title>精选</title>feed'
            request_route.fulfill(status=200, content_type='text/html', body=body)
        else:
            self.others.append(url)
            request_route.fulfill(status=404, body='')

    def resolve(self, url=PAGE, timeout=20, check=lambda: None):
        page = self.context.new_page()
        page.set_default_timeout(5000)
        self.messages = []
        return resolve_douyin(page, url, check, lambda message, **_k: self.messages.append(message),
                              is_public_media_url, max_bytes=MAX, timeout=timeout)

    def refused(self, reason, **kwargs):
        with self.assertRaises(ProviderUnavailable) as caught:
            self.resolve(**kwargs)
        self.assertEqual(caught.exception.reason, reason)
        self.assertEqual(caught.exception.provider, 'douyin')
        return caught.exception

    def test_the_pages_own_answer_gives_the_best_cdn_address_title_and_one_page_view(self):
        self.answers = [(200, detail())]
        result = self.resolve()
        self.assertTrue(result['media_url'].endswith('h264_1080.mp4'))
        self.assertEqual(result['title'], '一个人背包环球旅行')
        self.assertEqual(result['provider'], 'douyin')
        self.assertEqual(self.documents, [PAGE])
        self.assertFalse([url for url in self.others if 'zjcdn' in url])  # Only the address; the transfer comes later.
        self.assertTrue(any('1080p' in message for message in self.messages))

    def test_a_refusal_that_the_page_retries_by_itself_still_works(self):
        self.page_script(asks=[ID, ID])
        self.answers = [(403, {}), (200, detail())]
        self.assertTrue(self.resolve()['media_url'].endswith('h264_1080.mp4'))
        self.assertEqual(self.detail_calls, [ID, ID])

    def test_answers_about_other_videos_on_the_page_are_ignored(self):
        self.page_script(asks=['999', ID])
        self.answers = [(200, detail('999', desc='video khác', rates=[rate(1080, 9000000, 'wrong')])), (200, detail())]
        result = self.resolve()
        self.assertEqual(result['title'], '一个人背包环球旅行')
        self.assertTrue(result['media_url'].endswith('h264_1080.mp4'))

    def test_a_video_douyin_hides_without_login_is_a_problem_of_that_video_only(self):
        self.answers = [(200, dict(status_code=0, aweme_detail=None,
                                   filter_detail=dict(aweme_id=ID, filter_reason='core_dep')))]
        self.refused('parse_failed')

    def test_repeated_refusals_and_a_move_to_the_feed_end_quickly_as_refused(self):
        self.page_script(asks=[ID, ID, ID], after='location.href = "/jingxuan";')
        self.answers = [(403, {})]*3
        started = time.monotonic()
        self.refused('refused', timeout=30)
        self.assertLess(time.monotonic()-started, 10)

    def test_a_verification_page_stops_this_source_without_solving_it(self):
        self.page_script(asks=[], title='验证码中间页')
        started = time.monotonic()
        self.refused('blocked', timeout=30)
        self.assertLess(time.monotonic()-started, 10)

    def test_no_answer_at_all_ends_at_the_deadline(self):
        self.page_script(asks=[])
        started = time.monotonic()
        self.refused('timeout', timeout=1.5)
        self.assertGreaterEqual(time.monotonic()-started, 1.4)

    def test_a_short_share_link_is_followed_to_the_video_page(self):
        self.answers = [(200, detail())]
        result = self.resolve(url='https://v.douyin.com/AbC/')
        self.assertTrue(result['media_url'].endswith('h264_1080.mp4'))
        self.assertEqual(self.documents, ['https://v.douyin.com/AbC/', PAGE])

    def test_a_link_that_leads_to_no_video_is_refused_as_that_videos_problem(self):
        self.refused('parse_failed', url='https://www.douyin.com/user/abc')
        self.assertEqual(self.detail_calls, [])

    def test_a_detail_without_any_cdn_address_is_unsupported_media(self):
        bad = ['https://www.douyin.com/aweme/v1/play/?video_id=1']
        self.answers = [(200, detail(rates=[rate(1080, 1, 'x', urls=bad)], plain=dict(height=720, url_list=bad)))]
        self.refused('unsupported_media')

    def test_stop_is_honoured_before_anything_is_opened(self):
        def stop():
            raise Cancelled('Đã dừng.')
        with self.assertRaises(Cancelled):
            self.resolve(check=stop)
        self.assertEqual(self.documents, [])


if __name__ == '__main__':
    unittest.main()
