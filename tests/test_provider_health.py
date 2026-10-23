import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from teodub.downloaders import browser_download
from teodub.downloaders.download_errors import ProviderUnavailable
from teodub.downloaders.download_provider_registry import PROVIDERS
from teodub.downloaders.provider_health import ProviderHealth, reported_seconds

URL = 'https://www.douyin.com/video/7686425290511289'
IDS = [p.id for p in PROVIDERS]


class HealthMemoryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name)/'health.json'
        self.health = ProviderHealth(self.path)

    def test_the_site_reported_time_is_read_in_its_own_words(self):
        self.assertEqual(reported_seconds('14h 11m'), 14*3600+11*60)
        self.assertEqual(reported_seconds('1 day 2 hours'), 86400+7200)
        self.assertIsNone(reported_seconds(''))
        self.assertIsNone(reported_seconds(None))

    def test_a_used_up_daily_quota_waits_as_long_as_the_site_says(self):
        until = self.health.record_failure('anysave', 'daily_quota', '14h 1m', now=1000)
        self.assertEqual(until, 1000+14*3600+60)
        self.assertEqual(self.health.unavailable('anysave', now=1001), (until, 'daily_quota'))
        self.assertIsNone(self.health.unavailable('anysave', now=until+1))
        self.assertIsNone(self.health.unavailable('savetik', now=1001))

    def test_quota_without_a_reported_time_and_other_service_failures_use_short_pauses(self):
        self.assertEqual(self.health.record_failure('anysave', 'daily_quota', None, now=0), 6*3600)
        self.assertEqual(self.health.record_failure('savetik', 'timeout', now=0), 1800)
        self.assertEqual(self.health.record_failure('socialvideo', 'maintenance', now=0), 3600)
        self.assertEqual(self.health.record_failure('vienphanmem', 'provider_error', now=0), 1200)

    def test_never_longer_than_a_day_whatever_the_site_says(self):
        self.assertEqual(self.health.record_failure('anysave', 'daily_quota', '9 days', now=0), 86400)

    def test_douyin_refusing_one_video_is_not_remembered_but_a_verification_page_pauses_it(self):
        self.assertIsNone(self.health.record_failure('douyin', 'refused', now=0))
        self.assertIsNone(self.health.unavailable('douyin', now=1))
        self.assertEqual(self.health.record_failure('douyin', 'blocked', now=0), 3600)
        self.assertEqual(self.health.unavailable('douyin', now=1), (3600, 'blocked'))

    def test_a_problem_with_one_video_does_not_mark_the_service(self):
        for reason in ('parse_failed', 'unsupported_media', 'invalid_media', 'transfer_error', 'refused'):
            self.assertIsNone(self.health.record_failure('savetik', reason, now=0))
        self.assertIsNone(self.health.unavailable('savetik', now=1))
        self.assertFalse(self.path.exists())

    def test_memory_is_shared_between_processes_and_a_success_clears_it(self):
        self.health.record_failure('savetik', 'timeout', now=100)
        self.assertEqual(ProviderHealth(self.path).unavailable('savetik', now=101), (1900, 'timeout'))
        ProviderHealth(self.path).record_success('savetik')
        self.assertIsNone(self.health.unavailable('savetik', now=101))

    def test_unreadable_or_foreign_data_is_ignored_and_never_raises(self):
        for text in ('not json', '[]', '{"savetik": 5, "anysave": {"until": "x"}}'):
            self.path.write_text(text, encoding='utf-8')
            self.assertIsNone(self.health.unavailable('savetik', now=0))
            self.assertIsNone(self.health.unavailable('anysave', now=0))
        self.path.parent.joinpath('block').write_text('file', encoding='utf-8')
        broken = ProviderHealth(self.path.parent/'block'/'health.json')
        self.assertIsNotNone(broken.record_failure('savetik', 'timeout', now=0))  # Saving failed quietly.

    def test_earliest_retry_is_none_while_any_service_is_usable(self):
        self.health.record_failure('savetik', 'timeout', now=0)
        self.assertIsNone(self.health.earliest_retry(IDS, now=1))
        for provider, reason in (('douyin', 'blocked'), ('vienphanmem', 'timeout'), ('socialvideo', 'maintenance')):
            self.health.record_failure(provider, reason, now=0)
        self.health.record_failure('anysave', 'daily_quota', '5h', now=0)
        self.assertEqual(self.health.earliest_retry(IDS, now=1), 1800)


class FallbackWithMemoryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name)/'job'
        self.folder.mkdir()
        self.health = ProviderHealth(Path(temp.name)/'health.json')
        self.context = Mock()
        self.context.new_page.side_effect = lambda: Mock()
        self.messages = []

    def resolve(self, **kwargs):
        return browser_download.resolve_providers(self.context, URL, self.folder, lambda: None,
            lambda message, **_k: self.messages.append(message), health=self.health, **kwargs)

    def test_a_failed_service_is_remembered_and_skipped_for_the_next_video(self):
        errors = [ProviderUnavailable('xác minh', provider='douyin', reason='blocked'),
                  ProviderUnavailable('chậm', provider='savetik', reason='timeout'),
                  ProviderUnavailable('chậm', provider='vienphanmem', reason='timeout'),
                  ProviderUnavailable('bảo trì', provider='socialvideo', reason='maintenance')]
        with patch.object(browser_download, '_resolve_provider',
                          side_effect=errors+[dict(media_url='https://example.invalid/v')]) as resolver, \
                patch.object(browser_download, 'save_provider_failure'):
            self.resolve()
            self.assertEqual([c.args[0] for c in resolver.call_args_list], IDS)
        with patch.object(browser_download, '_resolve_provider',
                          side_effect=[dict(media_url='https://example.invalid/v')]) as resolver:
            result = self.resolve()
        self.assertEqual([c.args[0] for c in resolver.call_args_list], ['anysave'])
        self.assertEqual(result['provider'], 'anysave')
        self.assertTrue(any('Bỏ qua Douyin trực tiếp' in m for m in self.messages))
        self.assertTrue(any('Bỏ qua SaveTik' in m for m in self.messages))
        self.assertEqual(self.context.new_page.call_count, 6)  # Five first time, one the second time.

    def test_when_every_service_is_unavailable_nothing_is_opened_and_the_reason_is_clear(self):
        for provider in IDS:
            self.health.record_failure(provider, 'daily_quota', '3h')
        with patch.object(browser_download, '_resolve_provider') as resolver:
            with self.assertRaises(ProviderUnavailable) as caught:
                self.resolve()
        resolver.assert_not_called()
        self.context.new_page.assert_not_called()
        self.assertEqual(caught.exception.reason, 'all_unavailable')
        self.assertTrue(str(caught.exception).startswith('Chờ nguồn tải'))
        self.assertGreater(caught.exception.retry_at, 0)

    def fail_all(self, errors):
        with patch.object(browser_download, '_resolve_provider', side_effect=errors), \
                patch.object(browser_download, 'save_provider_failure'):
            with self.assertRaises(ProviderUnavailable) as caught:
                self.resolve()
        return caught.exception

    def test_every_service_failing_in_this_attempt_also_ends_as_waiting_for_a_source(self):
        errors = [ProviderUnavailable('chậm', provider=p, reason='timeout') for p in IDS[:-1]]
        errors.append(ProviderUnavailable('hết lượt', provider='anysave', reason='daily_quota', retry_after_text='13h'))
        caught = self.fail_all(errors)
        self.assertEqual(caught.reason, 'all_unavailable')
        self.assertTrue(str(caught).startswith('Chờ nguồn tải'))

    def test_a_video_that_every_service_rejects_is_a_real_failure_not_a_wait(self):
        errors = [ProviderUnavailable('Douyin từ chối', provider='douyin', reason='refused')]
        errors += [ProviderUnavailable('không thấy video', provider=p, reason='parse_failed') for p in IDS[1:]]
        caught = self.fail_all(errors)
        self.assertEqual(caught.reason, 'parse_failed')

    def test_douyin_refusing_one_video_while_the_sites_are_down_waits_for_the_sites(self):
        errors = [ProviderUnavailable('Douyin từ chối', provider='douyin', reason='refused'),
                  ProviderUnavailable('chậm', provider='savetik', reason='timeout'),
                  ProviderUnavailable('chậm', provider='vienphanmem', reason='timeout'),
                  ProviderUnavailable('bảo trì', provider='socialvideo', reason='maintenance'),
                  ProviderUnavailable('hết lượt', provider='anysave', reason='daily_quota', retry_after_text='13h')]
        caught = self.fail_all(errors)
        self.assertEqual(caught.reason, 'all_unavailable')
        self.assertTrue(str(caught).startswith('Chờ nguồn tải'))
        self.assertIsNone(self.health.unavailable('douyin'))  # The next video still tries Douyin first.

    def test_a_success_clears_the_memory_and_explicit_verification_still_stops_at_once(self):
        self.health.record_failure('douyin', 'timeout', now=0)
        with patch.object(browser_download, '_resolve_provider',
                          return_value=dict(media_url='https://example.invalid/v')):
            self.resolve()
        self.assertIsNone(self.health.unavailable('douyin', now=1))  # Cleared, not merely expired.
        stop = ProviderUnavailable('xác minh', provider='douyin', reason='verification_required')
        with patch.object(browser_download, '_resolve_provider', side_effect=stop) as resolver, \
                patch.object(browser_download, 'save_provider_failure'):
            with self.assertRaises(ProviderUnavailable) as caught:
                self.resolve()
        self.assertEqual(resolver.call_count, 1)
        self.assertEqual(caught.exception.reason, 'verification_required')
        self.assertIsNotNone(self.health.unavailable('douyin'))


if __name__ == '__main__':
    unittest.main()
