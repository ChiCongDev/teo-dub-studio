"""Lấy thêm video mới (owner 30/10/2026): the videos of a link's channel that no campaign has yet are added to that
same link as its next videos. Local fixtures only; no worker, network, Chrome or AI."""
import copy
from unittest.mock import Mock, patch

from teodub import campaign, campaign_append, campaign_batch
from teodub.campaign_batch_controller import CampaignBatchController
from teodub.common import atomic_json
from teodub.pipeline import _job_lock
from tests.test_campaign_rotation import Fixture, SETTINGS


def rows(start, count):
    return [dict(url=f'https://www.douyin.com/video/{start+number}', title=f'Tiêu đề {start+number}') for number in range(count)]


class KnownVideoTests(Fixture):
    def test_every_saved_campaign_counts_even_an_archived_one(self):
        a, b = self.make('A', 2), self.make('B', 2, start=200)
        manifest = campaign.read_json(b/'campaign.json')
        manifest['archived_at'] = '2026-10-30T10:00:00'
        atomic_json(b/'campaign.json', manifest)
        self.assertEqual(campaign_append.known_urls(self.root),
                         {f'https://www.douyin.com/video/{n}' for n in (100, 101, 200, 201)})

    def test_an_append_still_waiting_for_the_worker_counts_too(self):
        a = self.make('A', 1)
        queue = campaign_batch.create_batch(self.root, [a], order='rotate')
        campaign_append.request_append(queue, a, rows(500, 2))
        self.assertIn('https://www.douyin.com/video/501', campaign_append.known_urls(self.root))

    def test_nothing_saved_means_nothing_known_and_damaged_files_are_skipped(self):
        self.assertEqual(campaign_append.known_urls(self.root), set())
        broken = self.root/'campaigns'/'broken'
        broken.mkdir(parents=True)
        (broken/'campaign.json').write_text('{not json', encoding='utf-8')
        self.make('A', 1)
        self.assertEqual(campaign_append.known_urls(self.root), {'https://www.douyin.com/video/100'})

    def test_split_keeps_the_channels_order_and_drops_known_and_repeated_videos(self):
        known = {'https://www.douyin.com/video/2'}
        records = [dict(url='https://www.douyin.com/video/3', title='c'), dict(url='https://www.douyin.com/video/2', title='b'),
                   dict(url='https://www.douyin.com/video/1?modal_id=1', title='a'),
                   dict(url='https://www.douyin.com/video/3', title='again'), dict(url='not a link', title='x')]
        fresh, already = campaign_append.split_known(records, known)
        self.assertEqual([row['url'] for row in fresh], ['https://www.douyin.com/video/3', 'https://www.douyin.com/video/1'])
        self.assertEqual(already, 2)


class AppendTests(Fixture):
    def test_new_videos_follow_the_old_ones_which_stay_exactly_as_they_were(self):
        a = self.make('A', 3)
        self.assertEqual(campaign._run_campaign_locked(a, runner=self.video)['status'], 'completed')
        before = campaign.read_json(a/'campaign.json')
        result = campaign_append.append_videos(a, rows(300, 2))
        self.assertEqual(result, dict(first=4, count=2, skipped=0))
        after = campaign.read_json(a/'campaign.json')
        self.assertEqual(after['items'][:3], before['items'])  # Number, status, outputs, hashes: untouched.
        self.assertEqual([(item['number'], item['status'], item['url']) for item in after['items'][3:]],
                         [(4, 'pending', 'https://www.douyin.com/video/300'), (5, 'pending', 'https://www.douyin.com/video/301')])
        for key in ('id', 'name', 'created_at', 'original_dir', 'output_dir', 'settings', 'discovery'):
            self.assertEqual(after.get(key), before.get(key))
        self.assertEqual(len(after['appended']), 1)
        self.assertEqual((after['appended'][0]['first'], after['appended'][0]['count']), (4, 2))
        self.assertEqual((a/'links.txt').read_text(encoding='utf-8').splitlines()[3:],
                         ['https://www.douyin.com/video/300', 'https://www.douyin.com/video/301'])

    def test_a_new_video_is_set_up_exactly_like_one_made_when_the_campaign_was_created(self):
        a = self.make('A', 2)
        campaign_append.append_videos(a, rows(300, 2))
        whole = campaign.create_campaign(self.root, 'A', campaign.parse_links('\n'.join(
            f'https://www.douyin.com/video/{n}' for n in (100, 101, 300, 301))), SETTINGS,
            str(self.root/'originals'), str(self.root/'outputs'))
        mine, theirs = campaign.read_json(a/'campaign.json'), campaign.read_json(whole/'campaign.json')
        for number in (3, 4):
            old = campaign.read_json(a/'jobs'/f'{number:04}'/'config.json')
            new = campaign.read_json(whole/'jobs'/f'{number:04}'/'config.json')
            for config, base in ((old, a), (new, whole)):  # Only where each campaign keeps its folders differs.
                for key in ('directory', 'stop_file', 'original_dir', 'output_dir'):
                    config.pop(key)
            self.assertEqual(old, new)
            self.assertEqual(mine['items'][number-1]['config_sha256'],
                             campaign.hash_file(a/'jobs'/f'{number:04}'/'config.json'))
        self.assertEqual(campaign.read_json(a/'jobs'/'0003'/'config.json')['original_dir'], mine['original_dir'])
        self.assertEqual(campaign.read_json(a/'jobs'/'0004'/'config.json')['output_dir'], mine['output_dir'])
        self.assertEqual(mine['items'][2].keys(), theirs['items'][2].keys())

    def test_a_finished_campaign_waits_for_chay_and_one_with_failures_stays_as_it_is(self):
        a, b = self.make('A', 1), self.make('B', 1, start=200)
        campaign._run_campaign_locked(a, runner=self.video)
        campaign_append.append_videos(a, rows(300, 1))
        manifest = campaign.read_json(a/'campaign.json')
        self.assertEqual(manifest['status'], 'paused')
        self.assertIn('bấm Chạy', manifest['message'])
        self.outcomes[('B', 1)] = dict(status='failed', message='Lỗi thử.')
        campaign._run_campaign_locked(b, runner=self.video)
        campaign_append.append_videos(b, rows(400, 1))
        self.assertEqual(campaign.read_json(b/'campaign.json')['status'], 'needs_attention')

    def test_the_added_videos_are_made_by_the_next_run(self):
        a = self.make('A', 2)
        campaign._run_campaign_locked(a, runner=self.video)
        campaign_append.append_videos(a, rows(300, 2))
        result = campaign._run_campaign_locked(a, runner=self.video)
        self.assertEqual(self.videos, [('A', 1), ('A', 2), ('A', 3), ('A', 4)])
        self.assertEqual(result['status'], 'completed')

    def test_videos_the_campaign_has_are_skipped_so_a_request_applied_twice_adds_nothing_twice(self):
        a = self.make('A', 2)
        self.assertEqual(campaign_append.append_videos(a, rows(99, 4))['count'], 2)  # 99, 100 (has), 101 (has), 102
        self.assertEqual(campaign_append.append_videos(a, rows(99, 4)), dict(first=None, count=0, skipped=4))
        self.assertEqual(len(campaign.read_json(a/'campaign.json')['items']), 4)
        self.assertEqual(len(campaign.read_json(a/'campaign.json')['appended']), 1)

    def test_nothing_is_written_when_there_is_nothing_to_add_or_the_list_is_empty(self):
        a = self.make('A', 1)
        before = (a/'campaign.json').read_bytes()
        for bad in ([], None, 'x'):
            with self.assertRaises(ValueError):
                campaign_append.append_videos(a, bad)
        campaign_append.append_videos(a, rows(100, 1))
        self.assertEqual((a/'campaign.json').read_bytes(), before)

    def test_a_campaign_that_is_working_is_refused_and_nothing_is_written(self):
        a = self.make('A', 2)
        before = (a/'campaign.json').read_bytes()
        with _job_lock(a):
            with self.assertRaisesRegex(ValueError, 'đang làm video'):
                campaign_append.append_videos(a, rows(300, 2))
        self.assertEqual((a/'campaign.json').read_bytes(), before)
        self.assertFalse((a/'jobs'/'0003').exists())

    def test_the_limit_of_a_campaign_is_kept_and_nothing_is_written(self):
        a = self.make('A', 3)
        before = (a/'campaign.json').read_bytes()
        with patch.object(campaign_append, 'MAX_CAMPAIGN_VIDEOS', 4):
            with self.assertRaisesRegex(ValueError, 'tối đa 4 video'):
                campaign_append.append_videos(a, rows(300, 2))
            self.assertEqual(campaign_append.append_videos(a, rows(300, 1))['count'], 1)
        self.assertNotEqual((a/'campaign.json').read_bytes(), before)
        self.assertFalse((a/'jobs'/'0005').exists())

    def test_a_deleted_or_damaged_campaign_is_refused(self):
        a = self.make('A', 1)
        manifest = campaign.read_json(a/'campaign.json')
        atomic_json(a/'campaign.json', dict(manifest, archived_at='2026-10-30T10:00:00'))
        with self.assertRaises(ValueError):
            campaign_append.append_videos(a, rows(300, 1))
        atomic_json(a/'campaign.json', dict(manifest, items=[]))
        with self.assertRaises(ValueError):
            campaign_append.append_videos(a, rows(300, 1))
        self.assertFalse((a/'jobs'/'0002').exists())


class RotationAppendTests(Fixture):
    def rotation(self, *paths):
        return campaign_batch.create_batch(self.root, list(paths), order='rotate')

    def test_videos_asked_for_before_the_rotation_starts_join_that_links_turns(self):
        a, b = self.make('A', 2), self.make('B', 3, start=200)
        directory = self.rotation(a, b)
        campaign_append.request_append(directory, a, rows(300, 2))
        queue = campaign_batch.run_batch(directory, runner=self.turn)
        self.assertEqual(self.videos, [('A', 1), ('B', 1), ('A', 2), ('B', 2), ('A', 3), ('B', 3), ('A', 4)])
        self.assertEqual(queue['status'], 'completed')
        self.assertFalse(list(directory.glob(campaign_append.APPEND_REQUESTS)))

    def test_videos_asked_for_in_the_middle_of_a_turn_are_added_between_turns_without_stopping_the_list(self):
        a, b = self.make('A', 2), self.make('B', 2, start=200)
        directory = self.rotation(a, b)
        self.during[('A', 1)] = lambda: campaign_append.request_append(directory, b, rows(400, 2))
        queue = campaign_batch.run_batch(directory, runner=self.turn)
        self.assertEqual(self.videos, [('A', 1), ('B', 1), ('A', 2), ('B', 2), ('B', 3), ('B', 4)])
        self.assertEqual(queue['status'], 'completed')
        self.assertEqual(queue['campaigns'][1]['config_identity'],
                         campaign_batch._identity(campaign.read_json(b/'campaign.json'), b))

    def test_a_finished_link_comes_back_with_its_own_chay_after_videos_were_added(self):
        a, b = self.make('A', 1), self.make('B', 3, start=200)
        directory = self.rotation(a, b)

        def ask():
            campaign_append.request_append(directory, a, rows(300, 2))
            campaign_batch.add_to_rotation(directory, a)  # What the tab does next: the link's Chạy.
        self.during[('B', 1)] = ask
        queue = campaign_batch.run_batch(directory, runner=self.turn)
        self.assertEqual(self.videos, [('A', 1), ('B', 1), ('A', 2), ('B', 2), ('A', 3), ('B', 3)])
        self.assertEqual(queue['status'], 'completed')

    def test_without_the_links_own_chay_a_finished_link_does_not_run_by_itself(self):
        a, b = self.make('A', 1), self.make('B', 3, start=200)
        directory = self.rotation(a, b)
        self.during[('B', 1)] = lambda: campaign_append.request_append(directory, a, rows(300, 1))
        campaign_batch.run_batch(directory, runner=self.turn)
        self.assertEqual(self.videos, [('A', 1), ('B', 1), ('B', 2), ('B', 3)])
        self.assertEqual(len(campaign.read_json(a/'campaign.json')['items']), 2)  # Added, waiting for Chạy.

    def test_the_other_links_keep_their_turns_and_their_files(self):
        a, b = self.make('A', 2), self.make('B', 2, start=200)
        directory = self.rotation(a, b)
        b_before = (b/'campaign.json').read_bytes()
        campaign_append.request_append(directory, a, rows(300, 1))
        self.during[('A', 1)] = lambda: self.assertEqual((b/'campaign.json').read_bytes(), b_before)
        campaign_batch.run_batch(directory, runner=self.turn)
        self.assertEqual([item['status'] for item in campaign.read_json(b/'campaign.json')['items']], ['completed']*2)

    def test_a_request_for_a_link_outside_the_rotation_is_dropped_with_a_notice(self):
        a, b, c = self.make('A', 1), self.make('B', 1, start=200), self.make('C', 1, start=300)
        directory = self.rotation(a, b)
        campaign_append.request_append(directory, c, rows(900, 2))
        before = (c/'campaign.json').read_bytes()
        notes = campaign_batch._absorb_appends(directory, campaign_batch.read_batch(directory), self.root)
        self.assertEqual(len(notes), 1)
        self.assertIn('không thuộc vòng xen kẽ', notes[0])
        self.assertEqual((c/'campaign.json').read_bytes(), before)
        self.assertFalse(list(directory.glob(campaign_append.APPEND_REQUESTS)))

    def test_a_campaign_changed_by_anything_else_is_still_refused_and_gets_no_new_videos(self):
        a, b = self.make('A', 2), self.make('B', 1, start=200)
        directory = self.rotation(a, b)
        manifest = campaign.read_json(a/'campaign.json')
        manifest['items'][0]['title'] = 'Đổi tay'
        atomic_json(a/'campaign.json', manifest)
        campaign_append.request_append(directory, a, rows(300, 2))
        queue = campaign_batch.run_batch(directory, runner=self.turn)
        self.assertEqual(self.videos, [])
        self.assertEqual(queue['status'], 'paused')
        self.assertIn('đã thay đổi', queue['message'])
        self.assertEqual(len(campaign.read_json(a/'campaign.json')['items']), 2)
        self.assertFalse(list(directory.glob(campaign_append.APPEND_REQUESTS)))

    def test_a_request_for_a_link_that_is_working_is_dropped_and_the_video_in_hand_is_untouched(self):
        a, b = self.make('A', 2), self.make('B', 1, start=200)
        directory = self.rotation(a, b)
        campaign_append.request_append(directory, a, rows(300, 1))
        with _job_lock(a):
            notes = campaign_batch._absorb_appends(directory, campaign_batch.read_batch(directory), self.root)
        self.assertIn('đang làm video', notes[0])
        self.assertEqual(len(campaign.read_json(a/'campaign.json')['items']), 2)

    def test_the_serial_list_and_the_single_campaign_run_do_not_look_at_requests(self):
        a = self.make('A', 2)
        serial = campaign_batch.create_batch(self.root, [a])
        campaign_append.request_append(serial, a, rows(300, 1))
        campaign_batch.run_batch(serial, runner=self.turn)
        self.assertEqual(self.videos, [('A', 1), ('A', 2)])
        self.assertTrue(list(serial.glob(campaign_append.APPEND_REQUESTS)))  # Waits for a rotation; nothing lost.


class ControllerAppendTests(Fixture):
    def setUp(self):
        super().setUp()
        self.studio = Mock(data_dir=self.root, process=None, login_pending=False, closing=False, campaign_busy=False)
        self.tab = Mock(scanning=False, process=None, worker_directory=None)
        self.controller = CampaignBatchController(self.studio, self.tab)
        self.a, self.b = self.make('A', 2), self.make('B', 2, start=200)

    def test_with_nothing_running_the_videos_are_added_at_once_and_the_saved_list_keeps_its_guard(self):
        directory = campaign_batch.create_batch(self.root, [self.a, self.b], order='rotate')
        self.controller.directory = directory
        self.assertEqual(self.controller.append_videos(self.a, rows(300, 3)), 3)
        self.assertEqual(len(campaign.read_json(self.a/'campaign.json')['items']), 5)
        queue = campaign_batch.run_batch(directory, runner=self.turn)  # No "đã thay đổi".
        self.assertEqual(queue['status'], 'completed')
        self.assertEqual([key for key in self.videos if key[0] == 'A'], [('A', n) for n in range(1, 6)])

    def test_a_link_that_is_in_no_saved_list_is_added_to_without_one(self):
        self.assertEqual(self.controller.append_videos(self.a, rows(300, 1)), 1)
        self.assertFalse((self.root/'campaign-batches').exists())

    def test_while_the_rotation_has_this_link_only_a_request_is_left_for_the_worker(self):
        directory = campaign_batch.create_batch(self.root, [self.a, self.b], order='rotate')
        self.controller.directory = directory
        before = (self.a/'campaign.json').read_bytes()
        with _job_lock(directory):  # The rotation's worker holds its queue.
            self.assertEqual(self.controller.append_videos(self.a, rows(300, 2)), 2)
        self.assertEqual((self.a/'campaign.json').read_bytes(), before)
        requests = list(directory.glob(campaign_append.APPEND_REQUESTS))
        self.assertEqual(len(requests), 1)
        self.assertEqual(campaign.read_json(requests[0])['directory'], str(self.a))

    def test_while_the_rotation_runs_a_link_it_does_not_have_is_added_to_directly(self):
        directory = campaign_batch.create_batch(self.root, [self.a], order='rotate')
        self.controller.directory = directory
        with _job_lock(directory):
            self.assertEqual(self.controller.append_videos(self.b, rows(300, 1)), 1)
        self.assertEqual(len(campaign.read_json(self.b/'campaign.json')['items']), 3)
        self.assertFalse(list(directory.glob(campaign_append.APPEND_REQUESTS)))

    def test_a_one_after_another_list_that_runs_refuses(self):
        serial = campaign_batch.create_batch(self.root, [self.a, self.b])
        self.controller.directory = serial
        with _job_lock(serial):
            with self.assertRaisesRegex(ValueError, 'chạy lần lượt'):
                self.controller.append_videos(self.a, rows(300, 1))
        self.assertEqual(len(campaign.read_json(self.a/'campaign.json')['items']), 2)

    def test_a_link_running_on_its_own_refuses(self):
        worker = Mock()
        worker.poll.return_value = None
        self.tab.process, self.tab.worker_directory = worker, self.a
        with self.assertRaisesRegex(ValueError, 'chạy riêng'):
            self.controller.append_videos(self.a, rows(300, 1))
        self.assertEqual(len(campaign.read_json(self.a/'campaign.json')['items']), 2)
        self.assertEqual(self.controller.append_videos(self.b, rows(300, 1)), 1)  # Another link is no obstacle.


if __name__ == '__main__':
    unittest.main()
