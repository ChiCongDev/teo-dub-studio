"""28/10/2026: a link waiting for a download source rests alone (the others keep their turns), and a video Douyin
reports deleted/locked is a failure of that video, not a wait. Local fixtures only; no network, browser or AI."""
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from teodub import campaign, campaign_batch
from teodub.campaign_batch import SOURCE_WAIT_MIN
from teodub.downloaders import browser_download
from teodub.downloaders.download_errors import ProviderUnavailable
from teodub.downloaders.download_provider_registry import PROVIDERS
from teodub.downloaders.provider_health import ProviderHealth
from tests.test_campaign_rotation import Fixture

URL = 'https://www.douyin.com/video/7626260130841103652'
IDS = [p.id for p in PROVIDERS]
WAIT = campaign.WAITING_SOURCE+': mọi nguồn tải đang bận.'


class RotationWaitTests(Fixture):
    def rotation(self, *paths, **options):
        return campaign_batch.create_batch(self.root, list(paths), order='rotate', **options)

    def waiting(self, name, number, until):
        self.outcomes[(name, number)] = dict(status='failed', message=WAIT)
        return patch.object(campaign, '_source_retry_at', return_value=until)

    def rows(self, directory):
        return {row['name']: row for row in campaign_batch.read_batch(directory)['campaigns']}

    def test_the_other_links_keep_their_turns_while_one_waits_for_a_source(self):
        a, b, c = self.make('A', 2), self.make('B', 2, start=200), self.make('C', 2, start=300)
        directory = self.rotation(a, b, c)
        with self.waiting('B', 1, time.time()+3600):
            queue = campaign_batch.run_batch(directory, runner=self.turn)
        self.assertEqual(self.videos, [('A', 1), ('B', 1), ('C', 1), ('A', 2), ('C', 2)])
        rows = {row['name']: row for row in queue['campaigns']}
        self.assertEqual((rows['A']['status'], rows['C']['status']), ('completed', 'completed'))
        # Every link that can still run is waiting: only then does the rotation pause, on the waiting link.
        self.assertEqual((queue['status'], rows['B']['status']), ('paused', 'paused'))
        self.assertTrue(rows['B']['message'].startswith(campaign.WAITING_SOURCE))
        self.assertGreater(rows['B']['wait_until'], time.time()+3000)
        self.assertEqual(queue['message'], rows['B']['message'])
        manifest = campaign.read_json(b/'campaign.json')
        self.assertEqual(manifest['status'], 'paused')
        self.assertTrue(campaign.auto_resume_due(manifest, time.time()+7200))  # The desktop restarts it from this.

    def test_a_waiting_link_is_not_tried_again_in_the_same_pass_even_with_a_stale_time(self):
        a, b = self.make('A', 2), self.make('B', 2, start=200)
        directory = self.rotation(a, b)
        with self.waiting('B', 1, 12345.0):  # A time long past: still never sooner than SOURCE_WAIT_MIN.
            queue = campaign_batch.run_batch(directory, runner=self.turn)
        self.assertEqual(self.videos, [('A', 1), ('B', 1), ('A', 2)])
        self.assertEqual(queue['status'], 'paused')
        self.assertGreaterEqual(self.rows(directory)['B']['wait_until'], time.time()+SOURCE_WAIT_MIN-5)

    def test_the_link_comes_back_in_its_turn_once_its_time_is_over(self):
        a, b = self.make('A', 2), self.make('B', 2, start=200)
        directory = self.rotation(a, b)
        with self.waiting('B', 1, time.time()+3600):
            campaign_batch.run_batch(directory, runner=self.turn)
        queue = campaign_batch.read_batch(directory)
        for row in queue['campaigns']:
            if row['name'] == 'B':
                row['wait_until'] = time.time()-1  # The time passes (what the desktop's auto-resume waits for).
        campaign_batch.atomic_json(directory/'queue.json', queue)
        del self.outcomes[('B', 1)]
        queue = campaign_batch.run_batch(directory, runner=self.turn)
        self.assertEqual(self.videos, [('A', 1), ('B', 1), ('A', 2), ('B', 1), ('B', 2)])
        self.assertEqual(queue['status'], 'completed')
        self.assertNotIn('wait_until', self.rows(directory)['B'])

    def test_chay_or_tiep_tuc_while_every_link_waits_tries_them_now_as_before(self):
        a, b = self.make('A', 1), self.make('B', 2, start=200)
        directory = self.rotation(a, b)
        with self.waiting('B', 1, time.time()+3600):
            campaign_batch.run_batch(directory, runner=self.turn)
        self.assertEqual(self.videos, [('A', 1), ('B', 1)])
        del self.outcomes[('B', 1)]
        queue = campaign_batch.run_batch(directory, runner=self.turn)  # The owner presses Chạy / Tiếp tục.
        self.assertEqual(self.videos, [('A', 1), ('B', 1), ('B', 1), ('B', 2)])
        self.assertEqual(queue['status'], 'completed')

    def test_the_links_own_chay_brings_it_back_at_once(self):
        a, b, c = self.make('A', 1), self.make('B', 2, start=200), self.make('C', 1, start=300)
        directory = self.rotation(a, b, c)
        with self.waiting('B', 1, time.time()+3600):
            campaign_batch.run_batch(directory, runner=self.turn)
        del self.outcomes[('B', 1)]
        campaign_batch.add_to_rotation(directory, b)
        queue = campaign_batch.run_batch(directory, runner=self.turn)
        self.assertEqual(self.videos[3:], [('B', 1), ('B', 2)])
        self.assertEqual(queue['status'], 'completed')

    def test_dung_still_pauses_everything_even_when_that_video_was_waiting_for_a_source(self):
        a, b = self.make('A', 2), self.make('B', 2, start=200)
        directory = self.rotation(a, b)
        self.during[('B', 1)] = (directory/'stop.request').touch
        with self.waiting('B', 1, time.time()+3600):
            queue = campaign_batch.run_batch(directory, runner=self.turn, clear_stop=False)
        self.assertEqual(self.videos, [('A', 1), ('B', 1)])
        self.assertEqual(queue['status'], 'paused')
        self.assertNotIn('wait_until', self.rows(directory)['B'])

    def test_dung_link_nay_on_a_waiting_link_takes_it_out_of_the_turns(self):
        a, b, c = self.make('A', 2), self.make('B', 2, start=200), self.make('C', 2, start=300)
        directory = self.rotation(a, b, c)
        self.during[('C', 1)] = lambda: campaign_batch.remove_from_rotation(directory, b)  # B is waiting by then.
        with self.waiting('B', 1, time.time()+3600):
            queue = campaign_batch.run_batch(directory, runner=self.turn)
        self.assertEqual(self.videos, [('A', 1), ('B', 1), ('C', 1), ('A', 2), ('C', 2)])
        self.assertEqual({row['name']: row['status'] for row in queue['campaigns']}['B'], 'stopped')

    def test_other_pauses_still_pause_the_whole_rotation_as_before(self):
        a, b = self.make('A', 2), self.make('B', 2, start=200)
        self.outcomes[('B', 1)] = dict(status='failed', message='Hết hạn mức AI Codex.')
        queue = campaign_batch.run_batch(self.rotation(a, b), runner=self.turn)
        self.assertEqual(self.videos, [('A', 1), ('B', 1)])
        self.assertEqual(queue['status'], 'paused')

    def test_one_link_alone_waiting_pauses_the_rotation_exactly_as_before(self):
        a = self.make('A', 2)
        directory = self.rotation(a)
        with self.waiting('A', 1, time.time()+3600):
            queue = campaign_batch.run_batch(directory, runner=self.turn)
        self.assertEqual((queue['status'], queue['campaigns'][0]['status']), ('paused', 'paused'))
        self.assertEqual(self.videos, [('A', 1)])


class GoneVideoTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name)/'job'
        self.folder.mkdir()
        self.health = ProviderHealth(Path(temp.name)/'health.json')
        self.context = Mock()
        self.context.new_page.side_effect = lambda: Mock()

    def resolve(self):
        return browser_download.resolve_providers(self.context, URL, self.folder, lambda: None,
                                                  lambda message, **_k: None, health=self.health)

    def fail_all(self, errors):
        with patch.object(browser_download, '_resolve_provider', side_effect=errors), \
                patch.object(browser_download, 'save_provider_failure'):
            with self.assertRaises(ProviderUnavailable) as caught:
                self.resolve()
        return caught.exception

    @staticmethod
    def gone():
        return ProviderUnavailable('Douyin báo video đã bị xóa hoặc khóa', provider='douyin', reason='video_gone')

    def sites_down(self):
        return [ProviderUnavailable('chậm', provider='savetik', reason='timeout'),
                ProviderUnavailable('chậm', provider='vienphanmem', reason='timeout'),
                ProviderUnavailable('bảo trì', provider='socialvideo', reason='maintenance'),
                ProviderUnavailable('hết lượt', provider='anysave', reason='daily_quota', retry_after_text='13h')]

    def test_a_video_douyin_says_is_gone_fails_for_that_video_instead_of_waiting_for_the_sites(self):
        caught = self.fail_all([self.gone()]+self.sites_down())
        self.assertEqual(caught.reason, 'video_gone')
        message = str(caught)
        self.assertFalse(message.startswith(campaign.WAITING_SOURCE))
        for word in ('hạn mức', 'quota', 'usage limit', 'rate limit', 'đăng nhập', 'login', 'credits'):
            self.assertNotIn(word, message.lower())  # Else the campaign would pause instead of going on.
        self.assertIn('xóa hoặc khóa', message)
        self.assertEqual([item['provider'] for item in caught.attempts if item['status'] == 'failed'], IDS)
        self.assertIsNone(self.health.unavailable('douyin'))  # Douyin itself stays usable for the next video.

    def test_the_same_when_the_other_sites_were_already_paused_and_skipped(self):
        for provider, reason in (('socialvideo', 'maintenance'), ('anysave', 'daily_quota')):
            self.health.record_failure(provider, reason, '13h')
        errors = [self.gone(), ProviderUnavailable('chậm', provider='savetik', reason='timeout'),
                  ProviderUnavailable('chậm', provider='vienphanmem', reason='timeout')]
        caught = self.fail_all(errors)
        self.assertEqual(caught.reason, 'video_gone')

    def test_another_source_that_does_get_the_video_still_wins(self):
        errors = [self.gone(), dict(media_url='https://example.invalid/v')]
        with patch.object(browser_download, '_resolve_provider', side_effect=errors), \
                patch.object(browser_download, 'save_provider_failure'):
            result = self.resolve()
        self.assertEqual(result['provider'], 'savetik')

    def test_a_refusal_that_is_not_a_deleted_video_still_waits_for_the_sites(self):
        errors = [ProviderUnavailable('Douyin từ chối', provider='douyin', reason='refused')]+self.sites_down()
        caught = self.fail_all(errors)
        self.assertEqual(caught.reason, 'all_unavailable')
        self.assertTrue(str(caught).startswith(campaign.WAITING_SOURCE))

    def test_a_gone_video_is_a_red_failure_and_the_campaign_goes_on_to_the_next_video(self):
        fixture = Fixture('setUp')
        fixture.setUp()
        self.addCleanup(fixture.temp.cleanup)
        directory = fixture.make('A', 3)
        message = self.fail_all([self.gone()]+self.sites_down()).args[0]
        fixture.outcomes[('A', 1)] = dict(status='failed', message=message)
        manifest = campaign._run_campaign_locked(directory, runner=fixture.video)
        self.assertEqual(fixture.videos, [('A', 1), ('A', 2), ('A', 3)])
        self.assertEqual([item['status'] for item in manifest['items']], ['failed', 'completed', 'completed'])
        self.assertEqual(manifest['status'], 'needs_attention')
        self.assertNotIn('resume_after', manifest)
        self.assertIn('xóa hoặc khóa', manifest['items'][0]['message'])


if __name__ == '__main__':
    unittest.main()
