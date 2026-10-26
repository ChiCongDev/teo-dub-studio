"""One sub-tab per link (owner 23/10/2026): each link's own Chạy / Dừng link này, the other links go on.
Local fixtures only; no worker, network or AI."""
import json
from pathlib import Path
import tempfile
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import Mock, patch

from teodub import campaign, campaign_batch
from teodub.campaign_batch_controller import CampaignBatchController
from teodub.common import atomic_json
from teodub.pipeline import _job_lock
from tests.test_campaign_rotation import Fixture, SETTINGS


class LinkStopTests(Fixture):
    def rotation(self, *paths):
        return campaign_batch.create_batch(self.root, list(paths), order='rotate')

    def test_stopping_one_link_ends_its_video_and_lets_the_others_go_on(self):
        a, b = self.make('A', 3), self.make('B', 3, start=200)
        directory = self.rotation(a, b)
        def stop_a():
            campaign_batch.remove_from_rotation(directory, a)
            (a/'stop.request').touch()
        self.during[('A', 1)] = stop_a
        self.outcomes[('A', 1)] = dict(status='cancelled', message='Đã nhận yêu cầu Dừng.')
        queue = campaign_batch.run_batch(directory, runner=self.turn)
        self.assertEqual(self.videos, [('A', 1), ('B', 1), ('B', 2), ('B', 3)])
        self.assertEqual([row['status'] for row in queue['campaigns']], ['stopped', 'completed'])
        self.assertEqual(queue['status'], 'paused')  # A link is stopped, not finished.
        self.assertIn('Không còn link nào đang chạy', queue['message'])
        self.assertFalse(list(directory.glob('leave-*.json')))
        del self.during[('A', 1)], self.outcomes[('A', 1)]
        campaign_batch.add_to_rotation(directory, a)  # A's own Chạy.
        queue = campaign_batch.run_batch(directory, runner=self.turn)
        self.assertEqual(self.videos[4:], [('A', 1), ('A', 2), ('A', 3)])
        self.assertEqual(queue['status'], 'completed')

    def test_a_link_stopped_while_waiting_gets_no_more_turns(self):
        a, b = self.make('A', 2), self.make('B', 2, start=200)
        directory = self.rotation(a, b)
        self.during[('A', 1)] = lambda: campaign_batch.remove_from_rotation(directory, b)
        queue = campaign_batch.run_batch(directory, runner=self.turn)
        self.assertEqual(self.videos, [('A', 1), ('A', 2)])
        self.assertEqual([row['status'] for row in queue['campaigns']], ['completed', 'stopped'])
        self.assertEqual(campaign.read_json(b/'campaign.json')['items'][0]['status'], 'pending')

    def test_the_later_of_stop_and_run_wins(self):
        a, b = self.make('A', 2), self.make('B', 1, start=200)
        directory = self.rotation(a, b)
        def stop_then_run():
            campaign_batch.remove_from_rotation(directory, b)
            campaign_batch.add_to_rotation(directory, b)
        self.during[('A', 1)] = stop_then_run
        queue = campaign_batch.run_batch(directory, runner=self.turn)
        self.assertEqual(self.videos, [('A', 1), ('B', 1), ('A', 2)])
        self.assertEqual(queue['status'], 'completed')

    def test_dung_tat_ca_still_pauses_every_link_together(self):
        a, b = self.make('A', 2), self.make('B', 2, start=200)
        directory = self.rotation(a, b)
        self.during[('A', 1)] = (directory/'stop.request').touch
        self.outcomes[('A', 1)] = dict(status='cancelled', message='Đã nhận yêu cầu Dừng.')
        queue = campaign_batch.run_batch(directory, runner=self.turn)
        self.assertEqual(queue['status'], 'paused')
        self.assertEqual([row['status'] for row in queue['campaigns']], ['paused', 'pending'])


class LinkControllerTests(Fixture):
    def setUp(self):
        super().setUp()
        self.studio = Mock(data_dir=self.root, process=None, login_pending=False, closing=False, campaign_busy=False)
        self.tab = Mock(scanning=False, process=None, worker_directory=None)
        self.controller = CampaignBatchController(self.studio, self.tab)
        self.a, self.b = self.make('A', 2), self.make('B', 2, start=200)

    def spawned(self):
        child = Mock()
        child.poll.return_value = None
        return patch('teodub.campaign_batch_controller.subprocess.Popen', return_value=child)

    def test_chay_with_nothing_running_starts_turns_with_only_that_link(self):
        with self.spawned() as spawn:
            self.assertEqual(self.controller.run_link(self.a), 'started')
            self.assertIn('--campaign-batch-worker', spawn.call_args.args[0])
        queue = campaign_batch.read_batch(self.controller.directory)
        self.assertEqual((queue['order'], [row['name'] for row in queue['campaigns']]), ('rotate', ['A']))

    def test_chay_while_turns_run_joins_and_dung_stops_only_that_link(self):
        rotation = campaign_batch.create_batch(self.root, [self.a], order='rotate')
        self.controller.directory = rotation
        atomic_json(rotation/'queue.json', dict(campaign.read_json(rotation/'queue.json'), status='running',
                                                active_directory=str(self.a)))
        with _job_lock(rotation):  # The rotation's worker runs.
            self.assertEqual(self.controller.run_link(self.b), 'joined')
            self.assertEqual(self.controller.stop_link(self.a), 'stopping')
        self.assertEqual(len(list(rotation.glob('join-*.json'))), 1)
        self.assertEqual(len(list(rotation.glob('leave-*.json'))), 1)
        self.assertTrue((self.a/'stop.request').exists())  # Its video in progress stops, keeping finished steps.
        self.assertFalse((self.b/'stop.request').exists())

    def test_dung_on_a_link_of_a_rotation_that_is_not_running(self):
        rotation = campaign_batch.create_batch(self.root, [self.a, self.b], order='rotate')
        self.controller.directory = rotation
        self.assertEqual(self.controller.stop_link(self.b), 'stopping')
        rows = campaign_batch.read_batch(rotation)['campaigns']
        self.assertEqual([row['status'] for row in rows], ['pending', 'stopped'])
        self.assertIsNone(self.controller.stop_link(self.b))  # Already stopped.
        with self.spawned():
            self.assertEqual(self.controller.run_link(self.b), 'started')  # Reuses the rotation, B back in turns.
        self.assertEqual(self.controller.directory, rotation)
        self.assertEqual(len(list(rotation.glob('join-*.json'))), 1)

    def test_chay_while_a_campaign_runs_on_its_own_hands_over_after_its_video(self):
        worker = Mock()
        worker.poll.return_value = None
        self.tab.process, self.tab.worker_directory = worker, self.a
        self.assertEqual(self.controller.run_link(self.a), 'already')
        self.assertEqual(self.controller.run_link(self.b), 'handoff')
        self.assertTrue((self.a/campaign.ROTATION_REQUEST).is_file())
        self.assertEqual(self.controller.link_state(self.a, campaign.read_json(self.a/'campaign.json')), 'running')
        self.assertEqual(self.controller.link_state(self.b, campaign.read_json(self.b/'campaign.json')), 'handoff')
        # Dừng link này on the running one: it stops, and the waiting link still starts after it.
        self.assertEqual(self.controller.stop_link(self.a), 'stopping')
        self.assertTrue((self.a/'stop.request').exists())
        self.tab.process = None
        manifest = campaign.read_json(self.a/'campaign.json')
        manifest.update(status='paused', message='Đã nhận yêu cầu Dừng.')
        atomic_json(self.a/'campaign.json', manifest)
        with self.spawned() as spawn:
            self.assertEqual(self.controller.complete_handoff(), 'started')
            spawn.assert_called_once()

    def test_link_states_read_only_saved_files(self):
        rotation = campaign_batch.create_batch(self.root, [self.a, self.b], order='rotate')
        self.controller.directory = rotation
        queue = campaign.read_json(rotation/'queue.json')
        queue.update(status='running', active_directory=str(self.a))
        queue['campaigns'][1]['status'] = 'stopped'
        atomic_json(rotation/'queue.json', queue)
        self.studio.campaign_busy = True
        manifest = campaign.read_json(self.a/'campaign.json')
        self.assertEqual(self.controller.link_state(self.a, manifest), 'running')
        self.assertEqual(self.controller.link_state(self.b, manifest), 'stopped')
        queue['campaigns'][1]['status'] = 'pending'
        atomic_json(rotation/'queue.json', queue)
        self.assertEqual(self.controller.link_state(self.b, manifest), 'waiting')
        self.studio.campaign_busy = False
        self.assertEqual(self.controller.link_state(self.b, dict(manifest, status='ready')), 'ready')
        self.assertEqual(self.controller.link_state(self.b, dict(manifest, status='completed')), 'done')

    def test_a_chay_or_dung_the_worker_has_not_taken_yet_shows_at_once(self):
        # 24/10: after Chạy the tab still read 'đã dừng' until the other link's video ended; it was pressed 3 times.
        import os
        rotation = campaign_batch.create_batch(self.root, [self.a, self.b], order='rotate')
        self.controller.directory = rotation
        queue = campaign.read_json(rotation/'queue.json')
        queue.update(status='running', active_directory=str(self.a))
        queue['campaigns'][1]['status'] = 'stopped'
        atomic_json(rotation/'queue.json', queue)
        self.studio.campaign_busy = True
        a, b = campaign.read_json(self.a/'campaign.json'), campaign.read_json(self.b/'campaign.json')
        self.assertEqual(self.controller.link_state(self.b, b), 'stopped')
        join = campaign_batch.add_to_rotation(rotation, self.b)  # B's Chạy, not taken yet.
        self.assertEqual(self.controller.link_state(self.b, b), 'waiting')
        leave = campaign_batch.remove_from_rotation(rotation, self.b)  # Then its Dừng link này.
        later = join.stat().st_mtime_ns+1_000_000
        os.utime(leave, ns=(later, later))
        self.assertEqual(self.controller.link_state(self.b, b), 'stopped')
        campaign_batch.remove_from_rotation(rotation, self.a)
        self.assertEqual(self.controller.link_state(self.a, a), 'running')  # Its video is still being made.
        c = self.make('C', 1, start=300)
        campaign_batch.add_to_rotation(rotation, c)  # A new link: no row in the turns yet.
        manifest = campaign.read_json(c/'campaign.json')
        self.assertEqual(self.controller.link_state(c, manifest), 'waiting')
        self.studio.campaign_busy = False  # Nothing runs: a request alone changes nothing.
        self.assertEqual(self.controller.link_state(c, manifest), 'ready')


class LinkTabsUITests(unittest.TestCase):
    def setUp(self):
        from teodub.app import Studio
        from teodub.campaign_tabs_ui import CampaignArea
        from teodub.campaign_ui import CampaignTab
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name).resolve()
        self.root = tk.Tk()
        self.root.withdraw()
        self.notebook = ttk.Notebook(self.root)
        single = ttk.Frame(self.notebook)
        self.notebook.add(single, text='Video đơn')
        self.studio = Studio(self.root, str(self.data), parent=single)
        self.area = CampaignArea(self.notebook, self.studio)
        self.studio.campaign_area = self.area
        self.tab = CampaignTab(self.area.tabs, self.studio)
        self.notebook.add(self.area, text='Chiến dịch hàng loạt')
        self.studio.batch_controller = CampaignBatchController(self.studio, self.tab)
        self.area.attach(self.tab)
        self.addCleanup(self.cleanup)
        self.tab.original_dir.set(str(self.data/'originals'))
        self.tab.output_dir.set(str(self.data/'outputs'))

    def cleanup(self):
        for owner in (self.tab, self.area):
            for name in ('timer', 'auto_timer'):
                if getattr(owner, name, None) is not None:
                    owner.after_cancel(getattr(owner, name))
        self.studio.process = None
        self.studio.finish_close()

    def create(self, links, name):
        self.tab.source.delete('1.0', 'end')
        self.tab.source.insert('1.0', links)
        self.tab.name.set(name)
        self.tab.create()
        return self.tab.directory

    def titles(self):
        return [self.area.tabs.tab(tab, 'text') for tab in self.area.tabs.tabs()]

    def test_the_workspace_comes_first_and_each_created_link_gets_its_own_named_tab(self):
        self.assertEqual(self.titles(), ['＋ Thêm link'])
        a = self.create('https://www.douyin.com/video/111\nhttps://www.douyin.com/video/112', 'Kênh A')
        b = self.create('https://www.douyin.com/video/222', 'Kênh B')
        self.assertEqual(self.titles(), ['＋ Thêm link', 'Kênh A · 0/2 · sẵn sàng', 'Kênh B · 0/1 · sẵn sàng'])
        self.assertEqual(self.area.tabs.select(), str(self.area.links[str(b)]))
        self.assertEqual(self.notebook.select(), str(self.area))
        link = self.area.links[str(a)]
        link.refresh(full=True)
        self.assertEqual(link.header.get(), 'Kênh A')
        self.assertEqual(len(link.tree.get_children()), 2)
        self.assertEqual(str(link.run_button['state']), 'normal')
        self.assertEqual(str(link.stop_button['state']), 'disabled')
        saved = json.loads((self.data/'campaign-tabs.json').read_text(encoding='utf-8'))
        self.assertEqual(saved['campaigns'], [a.name, b.name])

    def test_closing_a_tab_only_hides_it_and_open_tabs_come_back(self):
        from teodub.campaign_tabs_ui import CampaignArea
        a = self.create('https://www.douyin.com/video/111', 'Kênh A')
        b = self.create('https://www.douyin.com/video/222', 'Kênh B')
        self.area.links[str(a)].close()
        self.assertEqual(self.titles(), ['＋ Thêm link', 'Kênh B · 0/1 · sẵn sàng'])
        self.assertTrue((a/'campaign.json').is_file())
        again = CampaignArea(self.notebook, self.studio)
        workspace = ttk.Frame(again.tabs)
        again.attach(workspace)
        try:
            self.assertEqual([again.tabs.tab(tab, 'text') for tab in again.tabs.tabs()],
                             ['＋ Thêm link', 'Kênh B · 0/1 · sẵn sàng'])
        finally:
            again.after_cancel(again.timer)
            again.destroy()

    def test_chay_on_a_link_tab_runs_that_link_and_dung_is_only_for_it(self):
        a = self.create('https://www.douyin.com/video/111', 'Kênh A')
        self.create('https://www.douyin.com/video/222', 'Kênh B')
        link = self.area.links[str(a)]
        child = Mock()
        child.poll.return_value = None
        with patch('subprocess.Popen', return_value=child) as spawn:
            link.run()
            self.assertIn('--campaign-batch-worker', spawn.call_args.args[0])
        queue = campaign_batch.read_batch(self.studio.batch_controller.directory)
        self.assertEqual([row['name'] for row in queue['campaigns']], ['Kênh A'])
        self.assertIn('Đã bắt đầu', link.message.get())

    def test_chay_while_turns_run_shows_cho_luot_at_once_and_is_not_pressed_twice(self):
        a = self.create('https://www.douyin.com/video/111', 'Kênh A')
        b = self.create('https://www.douyin.com/video/222', 'Kênh B')
        child = Mock()
        child.poll.return_value = None
        with patch('subprocess.Popen', return_value=child):
            self.area.links[str(b)].run()  # Kênh B runs in turns.
        rotation = self.studio.batch_controller.directory
        atomic_json(rotation/'queue.json', dict(campaign.read_json(rotation/'queue.json'), status='running',
                                                active_directory=str(b)))  # As its worker writes it.
        link = self.area.links[str(a)]
        link.refresh(full=True)
        self.assertEqual(str(link.run_button['state']), 'normal')
        with patch('subprocess.Popen', side_effect=AssertionError('the running worker takes it')):
            link.run()
        self.assertEqual(link.state, 'waiting')
        self.assertIn('chờ lượt', self.area.tabs.tab(link, 'text'))
        self.assertEqual(str(link.run_button['state']), 'disabled')  # No second press.
        self.assertEqual(str(link.stop_button['state']), 'normal')

    def test_retry_on_a_link_tab_retries_that_link_when_nothing_runs(self):
        a = self.create('https://www.douyin.com/video/111\nhttps://www.douyin.com/video/112', 'Kênh A')
        manifest = campaign.read_json(a/'campaign.json')
        manifest['items'][0].update(status='failed', message='Không có lời nói.')
        manifest['status'] = 'needs_attention'
        atomic_json(a/'campaign.json', manifest)
        link = self.area.links[str(a)]
        link.refresh(full=True)
        self.assertEqual(str(link.retry_button['state']), 'normal')
        self.assertEqual(str(link.run_button['state']), 'disabled')  # Nothing left but the failed video.
        worker = Mock()
        worker.poll.return_value = None
        with patch('subprocess.Popen', return_value=worker) as spawn:
            link.retry()
            command = spawn.call_args.args[0]
        self.assertIn('--campaign-batch-worker', command)  # In turns, like Chạy (owner 24/10/2026).
        queue = campaign_batch.read_batch(self.studio.batch_controller.directory)
        self.assertEqual([row['name'] for row in queue['campaigns']], ['Kênh A'])
        self.assertEqual(campaign.requested_retries(a), {1})
        self.assertIn('Đã xếp 1 video lỗi', link.message.get())
        self.assertEqual(link.tree.set('1', 'state'), 'Chờ thử lại')
        self.assertIn('chờ thử lại 1', link.status.get())
        self.assertEqual(str(link.retry_button['state']), 'disabled')  # Every failed video is already asked for.

    def test_retry_while_another_link_runs_puts_the_link_in_the_turns(self):
        a = self.create('https://www.douyin.com/video/111\nhttps://www.douyin.com/video/112', 'Kênh A')
        b = self.create('https://www.douyin.com/video/222', 'Kênh B')
        manifest = campaign.read_json(a/'campaign.json')
        manifest['items'][0].update(status='failed', message='Thời lượng câu đọc sau khi đọc nhanh không khớp.')
        atomic_json(a/'campaign.json', manifest)
        child = Mock()
        child.poll.return_value = None
        with patch('subprocess.Popen', return_value=child):
            self.area.links[str(b)].run()  # Kênh B runs in turns.
        link = self.area.links[str(a)]
        link.refresh(full=True)
        self.assertTrue(self.studio.campaign_busy)
        self.assertEqual(str(link.retry_button['state']), 'normal')  # Even while a link runs.
        with patch('subprocess.Popen', side_effect=AssertionError('the running worker takes it')):
            link.retry()
        joins = list(self.studio.batch_controller.directory.glob('join-*.json'))
        self.assertEqual([Path(campaign.read_json(path)['directory']).resolve() for path in joins], [a.resolve()])
        self.assertEqual(campaign.requested_retries(a), {1})
        self.assertIn('xen kẽ', link.message.get())

    def test_opening_a_campaign_from_the_library_opens_its_tab(self):
        from teodub.campaign_library_ui import CampaignLibraryTab
        a = self.create('https://www.douyin.com/video/111', 'Kênh A')
        self.area.links[str(a)].close()
        controller = Mock(busy=False)
        controller.snapshot.return_value = None
        library = CampaignLibraryTab(self.notebook, self.studio, self.tab, controller)
        self.notebook.add(library, text='Danh sách chiến dịch')
        try:
            library.refresh()
            library.tree.selection_set(str(a))
            library.open_selected()
            self.assertIn(str(a), self.area.links)
            self.assertEqual(self.notebook.select(), str(self.area))
            self.assertEqual(self.area.tabs.select(), str(self.area.links[str(a)]))
        finally:
            library.destroy()


if __name__ == '__main__':
    unittest.main()
