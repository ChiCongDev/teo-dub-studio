"""Campaigns in turns (owner 23/10/2026): one video of each campaign, then the next; a campaign created while
another runs joins it after the current video. Local fixtures only; no worker, network or AI."""
import json
from pathlib import Path
import tempfile
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import Mock, patch

from teodub import campaign, campaign_batch
from teodub.campaign_batch_controller import CampaignBatchController
from teodub.common import atomic_json, hash_file
from teodub.pipeline import _job_lock

SETTINGS = dict(voice='Mai Anh', voice_provider='local', reading_style='legacy', speed=.9, original_gain=.1,
                translation_model='gpt-5.6-luna', translation_effort='low', burn_subtitles=False,
                subtitle_color='white', subtitle_weight='bold')


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.videos = []       # (campaign name, video number) in the order the video helper ran them.
        self.outcomes = {}     # (name, number) -> result to return instead of a finished video.
        self.during = {}       # (name, number) -> callback run while that video is being made.

    def make(self, name, count, start=100):
        links = '\n'.join(f'https://www.douyin.com/video/{start+number}' for number in range(count))
        return campaign.create_campaign(self.root, name, campaign.parse_links(links), SETTINGS,
                                        str(self.root/'originals'), str(self.root/'outputs'))

    def video(self, config_path, stop):
        directory = config_path.parent.parent.parent
        key = (campaign.read_json(directory/'campaign.json')['name'], int(config_path.parent.name))
        self.videos.append(key)
        if key in self.during:
            self.during[key]()
        if key in self.outcomes:
            return self.outcomes[key]
        config = campaign.read_json(config_path)
        output = Path(config['output_dir'])/(config['campaign_basename']+'.mp4')
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b'finished video '+directory.name.encode()+config_path.parent.name.encode())
        outputs = dict(video=str(output))
        atomic_json(config_path.parent/'state.json', dict(rendered=dict(outputs=outputs,
                                                                        hashes=dict(video=hash_file(output)))))
        return dict(status='completed', message='Xong', outputs=outputs)

    def turn(self, path, *, external_stop, max_videos=None, verify_done=True):
        return campaign._run_campaign_locked(path, external_stop=external_stop, runner=self.video,
                                             max_videos=max_videos, verify_done=verify_done)


class CampaignTurnTests(Fixture):
    def test_a_turn_makes_one_video_and_rests_without_calling_it_a_stop(self):
        a = self.make('A', 3)
        stop = self.root/'queue-stop.request'
        result = self.turn(a, external_stop=stop, max_videos=1)
        self.assertTrue(result['turn_over'])
        self.assertEqual(self.videos, [('A', 1)])
        manifest = campaign.read_json(a/'campaign.json')
        self.assertEqual((manifest['status'], manifest['message']), ('paused', campaign.ROTATION_WAIT))
        self.assertNotIn('turn_over', manifest)  # Only the caller hears it; nothing new is stored.
        self.assertEqual([item['status'] for item in manifest['items']], ['completed', 'pending', 'pending'])
        result = self.turn(a, external_stop=stop, max_videos=1, verify_done=False)
        self.assertEqual(self.videos, [('A', 1), ('A', 2)])
        result = self.turn(a, external_stop=stop, max_videos=1, verify_done=False)
        self.assertEqual(result['status'], 'completed')
        self.assertNotIn('turn_over', result)

    def test_a_campaign_running_on_its_own_hands_over_only_between_two_videos(self):
        a = self.make('A', 3)
        self.during[('A', 1)] = lambda: (a/campaign.ROTATION_REQUEST).write_text('x', encoding='utf-8')
        result = campaign._run_campaign_locked(a, runner=self.video)
        self.assertEqual(self.videos, [('A', 1)])          # Video 1 was finished, never cut off.
        self.assertTrue(result['turn_over'])
        manifest = campaign.read_json(a/'campaign.json')
        self.assertEqual(manifest['items'][0]['status'], 'completed')
        self.assertEqual(manifest['message'], campaign.ROTATION_WAIT)

    def test_the_hand_over_request_is_ignored_under_a_supervisor_and_without_one_nothing_changes(self):
        a = self.make('A', 2)
        (a/campaign.ROTATION_REQUEST).write_text('x', encoding='utf-8')
        result = self.turn(a, external_stop=self.root/'queue-stop.request')  # A one-after-another list.
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(self.videos, [('A', 1), ('A', 2)])
        b = self.make('B', 2, start=200)
        result = campaign._run_campaign_locked(b, runner=self.video)
        self.assertEqual(result['status'], 'completed')
        self.assertNotIn('turn_over', result)

    def test_a_stop_at_the_end_of_a_turn_is_a_stop(self):
        a = self.make('A', 2)
        stop = self.root/'queue-stop.request'
        self.during[('A', 1)] = stop.touch
        result = self.turn(a, external_stop=stop, max_videos=1)
        self.assertNotIn('turn_over', result)
        self.assertEqual(result['status'], 'paused')
        self.assertNotEqual(result['message'], campaign.ROTATION_WAIT)


class RotationQueueTests(Fixture):
    def rotation(self, *paths, **options):
        return campaign_batch.create_batch(self.root, list(paths), order='rotate', **options)

    def test_one_video_of_each_campaign_in_turn_until_all_are_done(self):
        a, b, c = self.make('A', 3), self.make('B', 2, start=200), self.make('C', 1, start=300)
        queue = campaign_batch.run_batch(self.rotation(a, b, c), runner=self.turn)
        self.assertEqual(self.videos, [('A', 1), ('B', 1), ('C', 1), ('A', 2), ('B', 2), ('A', 3)])
        self.assertEqual(queue['status'], 'completed')
        self.assertEqual([row['status'] for row in queue['campaigns']], ['completed']*3)
        for path in (a, b, c):
            self.assertEqual(campaign.read_json(path/'campaign.json')['status'], 'completed')

    def test_a_campaign_added_while_running_takes_its_turn_after_the_others(self):
        a, b, c = self.make('A', 2), self.make('B', 2, start=200), self.make('C', 2, start=300)
        directory = self.rotation(a, b)
        self.during[('A', 1)] = lambda: campaign_batch.add_to_rotation(directory, c)
        queue = campaign_batch.run_batch(directory, runner=self.turn)
        self.assertEqual(self.videos, [('A', 1), ('B', 1), ('C', 1), ('A', 2), ('B', 2), ('C', 2)])
        self.assertEqual(queue['status'], 'completed')
        self.assertEqual([row['name'] for row in queue['campaigns']], ['A', 'B', 'C'])
        self.assertFalse(list(directory.glob('join-*.json')))

    def test_dung_pauses_every_campaign_and_resuming_continues_the_interrupted_turn(self):
        a, b = self.make('A', 2), self.make('B', 2, start=200)
        directory = self.rotation(a, b)
        def stop():
            (directory/'stop.request').touch()
        self.during[('B', 1)] = stop
        self.outcomes[('B', 1)] = dict(status='cancelled', message='Đã nhận yêu cầu Dừng.')
        queue = campaign_batch.run_batch(directory, runner=self.turn)
        self.assertEqual(queue['status'], 'paused')
        self.assertEqual([row['status'] for row in queue['campaigns']], ['pending', 'paused'])
        self.assertEqual(queue['next_index'], 1)
        del self.during[('B', 1)], self.outcomes[('B', 1)]
        queue = campaign_batch.run_batch(directory, runner=self.turn)
        self.assertEqual(self.videos, [('A', 1), ('B', 1), ('B', 1), ('A', 2), ('B', 2)])
        self.assertEqual(queue['status'], 'completed')

    def test_an_ordinary_failed_video_does_not_stop_the_turns(self):
        a, b = self.make('A', 2), self.make('B', 2, start=200)
        self.outcomes[('A', 1)] = dict(status='failed', message='Không có lời nói trong video.')
        queue = campaign_batch.run_batch(self.rotation(a, b), runner=self.turn)
        self.assertEqual(self.videos, [('A', 1), ('B', 1), ('A', 2), ('B', 2)])
        self.assertEqual(queue['status'], 'needs_attention')
        self.assertEqual([row['status'] for row in queue['campaigns']], ['needs_attention', 'completed'])

    def test_waiting_for_a_download_source_pauses_the_whole_rotation(self):
        a, b = self.make('A', 2), self.make('B', 2, start=200)
        self.outcomes[('B', 1)] = dict(status='failed', message=campaign.WAITING_SOURCE+': mọi nguồn tải đang bận.')
        with patch.object(campaign, '_source_retry_at', return_value=12345.0):
            queue = campaign_batch.run_batch(self.rotation(a, b), runner=self.turn)
        self.assertEqual(self.videos, [('A', 1), ('B', 1)])
        self.assertEqual(queue['status'], 'paused')
        manifest = campaign.read_json(b/'campaign.json')
        self.assertEqual((manifest['status'], manifest['resume_after']), ('paused', 12345.0))
        self.assertTrue(campaign.auto_resume_due(manifest, 99999))

    def test_finished_videos_are_checked_on_a_campaigns_first_turn_only(self):
        a, b = self.make('A', 3), self.make('B', 1, start=200)
        checked = []
        real = campaign.hash_file
        def hashed(path):
            checked.append(Path(path).suffix)
            return real(path)
        with patch.object(campaign, 'hash_file', side_effect=hashed):
            campaign_batch.run_batch(self.rotation(a, b), runner=self.turn)
        self.assertNotIn('.mp4', checked)  # No finished video was hashed again between turns.
        output = Path(campaign.read_json(a/'campaign.json')['items'][0]['outputs']['video'])
        output.write_bytes(b'changed later')
        c = self.make('C', 1, start=300)
        queue = campaign_batch.run_batch(self.rotation(a, c), runner=self.turn)
        self.assertEqual(queue['status'], 'paused')
        self.assertIn('đã thay đổi', queue['message'])  # A campaign's first turn still checks its finished videos.
        self.assertEqual(len(self.videos), 4)                # ...before any new video is made.

    def test_one_after_another_lists_are_saved_exactly_as_before(self):
        a = self.make('A', 1)
        queue = campaign_batch.read_batch(campaign_batch.create_batch(self.root, [a]))
        self.assertEqual(set(queue), {'schema', 'id', 'created_at', 'status', 'message', 'campaigns', 'active_directory'})
        self.assertEqual(queue['campaigns'][0]['message'], 'Chờ chạy')

    def test_rotation_fields_are_validated(self):
        a, b = self.make('A', 1), self.make('B', 1, start=200)
        directory = self.rotation(a, b)
        path = directory/'queue.json'
        good = json.loads(path.read_text(encoding='utf-8'))
        for change in (dict(order='random'), dict(next_index=-1), dict(next_index='1'),
                       dict(handoff_from=str(self.root))):
            with self.subTest(change=change):
                atomic_json(path, dict(good, **change))
                with self.assertRaises(ValueError):
                    campaign_batch.read_batch(directory)
        serial = campaign_batch.create_batch(self.root, [a])
        atomic_json(serial/'queue.json', dict(campaign.read_json(serial/'queue.json'), next_index=0))
        with self.assertRaises(ValueError):
            campaign_batch.read_batch(serial)
        with self.assertRaises(ValueError):
            campaign_batch.create_batch(self.root, [a], handoff_from=a)


class HandOverTests(Fixture):
    def setUp(self):
        super().setUp()
        self.studio = Mock(data_dir=self.root, process=None, login_pending=False, closing=False, campaign_busy=False)
        self.tab = Mock(scanning=False)
        self.controller = CampaignBatchController(self.studio, self.tab)
        self.a, self.b = self.make('A', 3), self.make('B', 2, start=200)

    def test_nothing_running_means_nothing_to_join(self):
        self.assertIsNone(self.controller.join(self.b, None))
        self.assertFalse((self.root/'campaign-batches').exists())

    def test_joining_a_campaign_running_on_its_own_prepares_turns_without_starting_anything(self):
        with patch('teodub.campaign_batch_controller.subprocess.Popen') as spawn:
            self.assertEqual(self.controller.join(self.b, self.a), 'handoff')
            spawn.assert_not_called()
        queue = campaign_batch.read_batch(self.controller.directory)
        self.assertEqual((queue['order'], queue['next_index'], queue['status']), ('rotate', 1, 'ready'))
        self.assertEqual(queue['handoff_from'], str(self.a))
        self.assertEqual([row['name'] for row in queue['campaigns']], ['A', 'B'])
        self.assertTrue((self.a/campaign.ROTATION_REQUEST).is_file())
        c = self.make('C', 1, start=300)
        self.assertEqual(self.controller.join(c, self.a), 'handoff')  # Still waiting: C joins the same turns.
        self.assertEqual(len(list(self.controller.directory.glob('join-*.json'))), 1)

    def test_the_turns_start_once_the_running_campaign_handed_over(self):
        self.controller.join(self.b, self.a)
        with _job_lock(self.a):  # Its current video is still being made.
            self.assertIsNone(self.controller.complete_handoff())
        manifest = campaign.read_json(self.a/'campaign.json')
        manifest.update(status='paused', message=campaign.ROTATION_WAIT)
        atomic_json(self.a/'campaign.json', manifest)
        child = Mock()
        child.poll.return_value = None
        with patch('teodub.campaign_batch_controller.subprocess.Popen', return_value=child) as spawn:
            self.assertEqual(self.controller.complete_handoff(), 'started')
            self.assertIn('--campaign-batch-worker', spawn.call_args.args[0])
        self.assertFalse((self.a/campaign.ROTATION_REQUEST).exists())
        self.assertTrue(self.studio.campaign_busy)

    def test_a_campaign_stopped_before_handing_over_leaves_the_turns_to_the_owner(self):
        self.controller.join(self.b, self.a)
        manifest = campaign.read_json(self.a/'campaign.json')
        manifest.update(status='paused', message='Đã nhận yêu cầu Dừng. Giữ tiến độ.')
        atomic_json(self.a/'campaign.json', manifest)
        with patch('teodub.campaign_batch_controller.subprocess.Popen') as spawn:
            self.assertEqual(self.controller.complete_handoff(), 'paused')
            spawn.assert_not_called()
        queue = campaign_batch.read_batch(self.controller.directory)
        self.assertEqual(queue['status'], 'paused')
        self.assertNotIn('handoff_from', queue)
        self.assertFalse((self.a/campaign.ROTATION_REQUEST).exists())

    def test_joining_a_running_rotation_or_a_one_after_another_list(self):
        rotation = campaign_batch.create_batch(self.root, [self.a], order='rotate')
        self.controller.directory = rotation
        with _job_lock(rotation):  # The rotation's worker holds its queue.
            self.assertEqual(self.controller.join(self.b, None), 'joined')
        self.assertEqual(len(list(rotation.glob('join-*.json'))), 1)
        serial = campaign_batch.create_batch(self.root, [self.a])
        self.controller.directory = serial
        c = self.make('C', 1, start=300)
        with _job_lock(serial):
            self.assertEqual(self.controller.join(c, None), 'serial')
        self.assertFalse(list(serial.glob('join-*.json')))

    def test_a_whole_hand_over_from_one_running_campaign_to_turns(self):
        """A runs on its own; B is created during A's video 1; A finishes video 1, then B 1, A 2, B 2, A 3."""
        self.during[('A', 1)] = lambda: self.controller.join(self.b, self.a)
        first = campaign._run_campaign_locked(self.a, runner=self.video)
        self.assertTrue(first['turn_over'])
        queue = campaign_batch.run_batch(self.controller.directory, runner=self.turn)
        self.assertEqual(self.videos, [('A', 1), ('B', 1), ('A', 2), ('B', 2), ('A', 3)])
        self.assertEqual(queue['status'], 'completed')
        self.assertNotIn('handoff_from', queue)
        self.assertFalse((self.a/campaign.ROTATION_REQUEST).exists())


class RotationTabTests(unittest.TestCase):
    def setUp(self):
        from teodub.app import Studio
        from teodub.campaign_ui import CampaignTab
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = tk.Tk()
        self.root.withdraw()
        self.notebook = ttk.Notebook(self.root)
        single = ttk.Frame(self.notebook)
        self.notebook.add(single, text='Video đơn')
        self.studio = Studio(self.root, self.temp.name, parent=single)
        self.tab = CampaignTab(self.notebook, self.studio)
        self.notebook.add(self.tab, text='Chiến dịch hàng loạt')
        self.studio.batch_controller = CampaignBatchController(self.studio, self.tab)
        self.addCleanup(self.cleanup)
        self.tab.original_dir.set(str(Path(self.temp.name)/'originals'))
        self.tab.output_dir.set(str(Path(self.temp.name)/'outputs'))

    def cleanup(self):
        self.tab.after_cancel(self.tab.timer)
        if self.tab.auto_timer is not None:
            self.tab.after_cancel(self.tab.auto_timer)
        self.studio.process = None
        self.studio.finish_close()

    def create(self, links, name):
        self.tab.source.delete('1.0', 'end')
        self.tab.source.insert('1.0', links)
        self.tab.name.set(name)
        self.tab.create()
        return self.tab.directory

    def test_reading_a_new_list_and_creating_a_campaign_are_allowed_while_campaigns_run(self):
        self.studio.campaign_busy = True
        self.tab.refresh_buttons()
        self.assertEqual(str(self.tab.create_button['state']), 'normal')
        self.assertEqual(str(self.tab.scan_button['state']), 'normal')
        self.assertEqual(str(self.tab.start_button['state']), 'disabled')
        self.studio.process = Mock()  # A single video still blocks them, as before.
        self.tab.refresh_buttons()
        self.assertEqual(str(self.tab.create_button['state']), 'disabled')

    def test_a_campaign_created_while_another_runs_asks_then_joins_it_in_turns(self):
        a = self.create('https://www.douyin.com/video/111', 'Kênh A')
        worker = Mock()
        worker.poll.return_value = None
        self.tab.process, self.tab.worker_directory, self.studio.campaign_busy = worker, a, True
        with patch('tkinter.messagebox.askyesno', return_value=False):
            self.assertEqual(self.create('https://www.douyin.com/video/222', 'Kênh B'), a)  # Declined: nothing made.
        with patch('tkinter.messagebox.askyesno', return_value=True) as asked:
            b = self.create('https://www.douyin.com/video/333', 'Kênh B')
        self.assertIn('xen kẽ', asked.call_args.args[1])
        self.assertNotEqual(b, a)
        self.assertIn('xen kẽ', self.tab.status.get())
        queue = campaign_batch.read_batch(self.studio.batch_controller.directory)
        self.assertEqual(queue['handoff_from'], str(a))
        self.assertTrue((a/campaign.ROTATION_REQUEST).is_file())
        self.tab.display(campaign.read_json(b/'campaign.json'))
        self.assertIn('Chờ chạy xen kẽ', self.tab.status.get())

    def test_run_on_a_campaign_of_a_stopped_rotation_continues_the_rotation(self):
        a = self.create('https://www.douyin.com/video/111', 'Kênh A')
        b = self.create('https://www.douyin.com/video/222', 'Kênh B')
        rotation = campaign_batch.create_batch(Path(self.temp.name), [a, b], order='rotate')
        atomic_json(rotation/'queue.json', dict(campaign.read_json(rotation/'queue.json'), status='paused'))
        self.studio.batch_controller.directory = rotation
        self.tab.directory = a
        child = Mock()
        child.poll.return_value = None
        with patch('subprocess.Popen', return_value=child) as spawn:  # Both modules share one subprocess module.
            self.tab.start()
            spawn.assert_called_once()
            self.assertIn('--campaign-batch-worker', spawn.call_args.args[0])
            self.assertNotIn('--campaign-worker', spawn.call_args.args[0])

    def test_dung_while_reading_a_list_leaves_the_running_campaign_alone(self):
        a = self.create('https://www.douyin.com/video/111', 'Kênh A')
        worker = Mock()
        worker.poll.return_value = None
        self.tab.process, self.tab.worker_directory, self.studio.campaign_busy = worker, a, True
        self.tab.scanning = True
        with _job_lock(a):
            self.tab.stop()
        self.assertTrue(self.tab.scan_cancel.is_set())
        self.assertFalse((a/'stop.request').exists())
        self.assertIn('vẫn tiếp tục', self.tab.status.get())

    def test_a_campaign_resting_between_turns_shows_its_turn_not_a_stop(self):
        a = self.create('https://www.douyin.com/video/111\nhttps://www.douyin.com/video/112', 'Kênh A')
        b = self.create('https://www.douyin.com/video/222', 'Kênh B')
        rotation = campaign_batch.create_batch(Path(self.temp.name), [a, b], order='rotate')
        self.studio.batch_controller.directory = rotation
        manifest = campaign.read_json(a/'campaign.json')
        manifest.update(status='paused', message=campaign.ROTATION_WAIT)
        atomic_json(a/'campaign.json', manifest)
        self.tab.directory = a
        with _job_lock(rotation):
            self.tab.display(manifest)
            self.assertIn('Chạy xen kẽ · chờ tới lượt', self.tab.status.get())
            self.assertEqual(self.tab.notice.get(), '')
            self.tab.refresh_buttons()
            self.assertEqual(str(self.tab.stop_button['state']), 'normal')
            self.tab.stop()
            self.assertTrue((rotation/'stop.request').exists())
        self.tab.display(manifest)  # Rotation no longer running: say so plainly.
        self.assertIn('Vòng chạy xen kẽ đang dừng', self.tab.notice.get())


class LibraryOrderTests(unittest.TestCase):
    def test_in_turns_is_offered_next_to_one_after_another(self):
        from teodub.app import Studio
        from teodub.campaign_library_ui import ORDERS, CampaignLibraryTab
        from teodub.campaign_ui import CampaignTab
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = tk.Tk()
        root.withdraw()
        notebook = ttk.Notebook(root)
        single = ttk.Frame(notebook)
        notebook.add(single, text='Video đơn')
        studio = Studio(root, temp.name, parent=single)
        tab = CampaignTab(notebook, studio)
        controller = Mock(busy=False)
        controller.snapshot.return_value = None
        library = CampaignLibraryTab(notebook, studio, tab, controller)
        try:
            directory = campaign.create_campaign(Path(temp.name), 'Kênh A', campaign.parse_links(
                'https://www.douyin.com/video/111'), SETTINGS, str(Path(temp.name)/'o'), str(Path(temp.name)/'x'))
            library.refresh()
            library.tree.selection_set(str(directory.resolve()))
            library.order.set(ORDERS[1])
            library.run_selected()
            self.assertEqual(controller.start.call_args.kwargs, dict(order='rotate'))
            library.order.set(ORDERS[0])
            library.run_selected()
            self.assertEqual(controller.start.call_args.kwargs, {})
        finally:
            library.destroy()
            tab.after_cancel(tab.timer)
            if tab.auto_timer is not None:
                tab.after_cancel(tab.auto_timer)
            studio.finish_close()


if __name__ == '__main__':
    unittest.main()
