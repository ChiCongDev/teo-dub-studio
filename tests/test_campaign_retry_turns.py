"""Thử lại video lỗi while links take turns (owner 24/10/2026): each video failed when asked gets one more try in its
link's turns while the other links keep theirs. Local fixtures only; no worker, network or AI."""
import unittest
from unittest.mock import patch

from teodub import campaign, campaign_batch
from teodub.campaign_tracking import RETRYING, tracking_row
from teodub.common import atomic_json
from tests.test_campaign_rotation import Fixture


class RetryInTurnsTests(Fixture):
    def fail(self, directory, *numbers):
        manifest = campaign.read_json(directory/'campaign.json')
        for item in manifest['items']:
            if item['number'] in numbers:
                item.update(status='failed', message='Thời lượng câu đọc sau khi đọc nhanh không khớp.')
        manifest['status'] = 'needs_attention'
        atomic_json(directory/'campaign.json', manifest)

    def items(self, directory):
        return [(item['status'], item.get('retry_once')) for item in campaign.read_json(directory/'campaign.json')['items']]

    def rotation(self, *paths):
        return campaign_batch.create_batch(self.root, list(paths), order='rotate')

    def test_retried_videos_come_first_in_the_links_turns_and_the_other_link_keeps_its_turns(self):
        a, b = self.make('A', 3), self.make('B', 2, start=200)
        self.fail(a, 1, 2)
        self.assertEqual(campaign.request_retry(a), 2)
        queue = campaign_batch.run_batch(self.rotation(a, b), runner=self.turn)
        self.assertEqual(self.videos, [('A', 1), ('B', 1), ('A', 2), ('B', 2), ('A', 3)])
        self.assertEqual(queue['status'], 'completed')
        self.assertEqual(self.items(a), [('completed', None)]*3)
        self.assertFalse(list(a.glob(campaign.RETRY_REQUESTS)))

    def test_a_video_failing_again_is_tried_only_once(self):
        a, b = self.make('A', 2), self.make('B', 3, start=200)
        self.fail(a, 1)
        campaign.request_retry(a)
        self.outcomes[('A', 1)] = dict(status='failed', message='Vẫn lỗi.')
        queue = campaign_batch.run_batch(self.rotation(a, b), runner=self.turn)
        self.assertEqual(self.videos, [('A', 1), ('B', 1), ('A', 2), ('B', 2), ('B', 3)])
        self.assertEqual(self.items(a), [('failed', None), ('completed', None)])
        self.assertEqual([row['status'] for row in queue['campaigns']], ['needs_attention', 'completed'])

    def test_chay_alone_still_skips_failed_videos_and_only_those_failed_when_asked_are_tried(self):
        a = self.make('A', 3)
        self.fail(a, 1)
        result = campaign._run_campaign_locked(a, runner=self.video)
        self.assertEqual(self.videos, [('A', 2), ('A', 3)])  # No request: as before, no automatic retry.
        self.assertEqual(result['status'], 'needs_attention')
        campaign.request_retry(a)
        self.fail(a, 2)  # Failed after the press: not part of it.
        result = campaign._run_campaign_locked(a, runner=self.video)
        self.assertEqual(self.videos[2:], [('A', 1)])
        self.assertEqual(self.items(a), [('completed', None), ('failed', None), ('completed', None)])
        self.assertEqual(result['status'], 'needs_attention')

    def test_a_stopped_link_keeps_its_marks_for_its_next_chay(self):
        a, b = self.make('A', 3), self.make('B', 1, start=200)
        self.fail(a, 1, 2, 3)
        campaign.request_retry(a)
        directory = self.rotation(a, b)
        self.during[('A', 1)] = lambda: campaign_batch.remove_from_rotation(directory, a)
        campaign_batch.run_batch(directory, runner=self.turn)
        self.assertEqual(self.videos, [('A', 1), ('B', 1)])
        self.assertEqual(self.items(a), [('completed', None), ('failed', True), ('failed', True)])
        campaign_batch.add_to_rotation(directory, a)  # A's own Chạy.
        queue = campaign_batch.run_batch(directory, runner=self.turn)
        self.assertEqual(self.videos[2:], [('A', 2), ('A', 3)])
        self.assertEqual(queue['status'], 'completed')

    def test_a_waiting_download_source_keeps_the_other_marks(self):
        a = self.make('A', 3)
        self.fail(a, 1, 2)
        campaign.request_retry(a)
        self.outcomes[('A', 1)] = dict(status='failed', message=campaign.WAITING_SOURCE+': chưa nguồn tải nào lấy được video này.')
        with patch.object(campaign, '_source_retry_at', return_value=0):
            result = campaign._run_campaign_locked(a, runner=self.video)
        self.assertEqual(result['status'], 'paused')
        self.assertEqual(self.items(a), [('cancelled', None), ('failed', True), ('pending', None)])

    def test_requests_are_plain_files_listing_video_numbers_and_are_used_once(self):
        a = self.make('A', 2)
        self.assertEqual(campaign.request_retry(a), 0)  # Nothing failed: nothing asked.
        self.assertFalse(list(a.glob(campaign.RETRY_REQUESTS)))
        (a/'retry-bad.json').write_text('{not json', encoding='utf-8')
        atomic_json(a/'retry-odd.json', dict(videos=['1', None, 2.0, True]))
        self.assertEqual(campaign.requested_retries(a), set())
        campaign._run_campaign_locked(a, runner=self.video)
        self.assertEqual(self.videos, [('A', 1), ('A', 2)])
        self.assertFalse(list(a.glob(campaign.RETRY_REQUESTS)))

    def test_a_video_waiting_for_its_retry_reads_cho_thu_lai(self):
        a = self.make('A', 1)
        self.fail(a, 1)
        item = campaign.read_json(a/'campaign.json')['items'][0]
        self.assertEqual(tracking_row(item)[3], 'Lỗi')
        self.assertEqual(tracking_row(item, retrying=True)[3], RETRYING)
        self.assertEqual(tracking_row(dict(item, retry_once=True))[3], RETRYING)
        self.assertEqual(tracking_row(dict(item, status='pending'), retrying=True)[3], 'Chờ xử lý')


if __name__ == '__main__':
    unittest.main()
