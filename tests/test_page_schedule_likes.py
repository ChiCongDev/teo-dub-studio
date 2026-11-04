"""Xếp lịch đăng theo lượt thích (owner 03/11/2026): the Page keeps its posting times, the most liked videos take the earliest.
Synthetic videos and campaigns; no Facebook, Chrome or real schedules."""
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from teodub.common import atomic_json, hash_file
from teodub.facebook_accounts import FacebookAccounts
from teodub.page_schedule import PageScheduleController, PageScheduleStore, campaign_video_source
from teodub.page_schedule_likes import SAFE_SECONDS, apply_plan, build_plan

HOUR = 3600


class LikesScheduleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.accounts = FacebookAccounts(self.directory)
        self.account = self.accounts.add_account('Fixture A')['id']
        self.page = self.accounts.save_page(self.account, 'Fixture Page', 'facebook.com/profile.php?id=100', '100')['id']
        self.store = PageScheduleStore(self.directory)
        self.owner = SimpleNamespace(studio=SimpleNamespace(data_dir=self.directory), sessions={}, status=Mock())
        self.owner.submit_page_browser = Mock(return_value='request')
        self.controller = PageScheduleController(self.owner)
        self.addCleanup(self.controller.close)
        self.start = time.time()+3*HOUR  # Every fixture time is well beyond SAFE_SECONDS.

    def video(self, number, likes):
        root = self.directory.resolve()
        campaign_id = f'{number:032x}'
        folder = root/'campaigns'/campaign_id
        job = folder/'jobs'/f'{number:04}'
        output_dir = root/'outputs'/campaign_id
        output_dir.mkdir(parents=True, exist_ok=True)
        output = output_dir/f'Video {number}.mp4'
        output.write_bytes(b'synthetic video '+str(number).encode())
        outputs = dict(video=str(output))
        atomic_json(job/'state.json', dict(rendered=dict(outputs=outputs, hashes=dict(video=hash_file(output)))))
        item = dict(number=number, status='completed', job=str(job), outputs=outputs)
        if likes is not None:
            item['likes'] = likes
        atomic_json(folder/'campaign.json', dict(id=campaign_id, name='Synthetic campaign', output_dir=str(output_dir), items=[item]))
        return folder, number

    def armed(self, number, likes, hours):
        source = campaign_video_source(self.directory, *self.video(number, likes))
        row = self.store.create(self.account, self.page, source, f'Caption {number}', '#tag', self.start+hours*HOUR)
        self.controller.start(row['id'])
        return row['id']

    def times(self):
        return {number: self.store.get(ident)['due_at'] for number, ident in self.ids.items()}

    def test_most_liked_takes_the_earliest_time_and_the_times_themselves_stay(self):
        self.ids = {1: self.armed(1, 10, 0), 2: self.armed(2, 500, 1), 3: self.armed(3, 50, 2), 4: self.armed(4, None, 3)}
        before = {ident: self.store.get(ident) for ident in self.ids.values()}
        slots = sorted(row['due_at'] for row in before.values())
        plan = build_plan(self.controller, self.account, self.page)
        self.assertEqual(plan['changed'], 3)  # The video without a count already holds the last time.
        self.assertEqual(apply_plan(self.controller, self.account, self.page, plan), 3)
        after = self.times()
        self.assertEqual([number for number, _ in sorted(after.items(), key=lambda pair: pair[1])], [2, 3, 1, 4])  # 500, 50, 10, none
        self.assertEqual(sorted(after.values()), slots)  # The Page's posting times are exactly the same set.
        for ident, old in before.items():
            row = self.store.get(ident)
            for key in ('post_id', 'body', 'snapshot', 'source', 'state', 'attempted_at', 'request_id'):
                self.assertEqual(row[key], old[key], key)  # Each appointment keeps its own video, caption and draft.
        with self.store.database() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM schedule_reorders').fetchone()[0], 3)
        self.assertEqual(self.controller.active, set(self.ids.values()))
        self.owner.submit_page_browser.assert_not_called()

    def test_ties_keep_their_old_order_and_a_correct_order_changes_nothing(self):
        self.ids = {1: self.armed(1, 20, 0), 2: self.armed(2, 20, 1), 3: self.armed(3, 5, 2)}
        plan = build_plan(self.controller, self.account, self.page)
        self.assertEqual(plan['changed'], 0)
        self.assertEqual(apply_plan(self.controller, self.account, self.page, plan), 0)

    def test_text_posts_touched_stopped_and_soon_due_appointments_keep_their_place(self):
        self.ids = {1: self.armed(1, 1, 0), 2: self.armed(2, 99, 1), 3: self.armed(3, 50, 2), 4: self.armed(4, 70, 3)}
        self.store.update(self.ids[3], attempted_at='synthetic attempted')  # Touched: never moved.
        self.controller.pause(self.ids[4])  # Paused by the owner: not armed, never moved.
        soon = self.armed(5, 1000, 0)
        self.store.update(soon, due_at=time.time()+SAFE_SECONDS-60)  # About to run: left alone.
        plan = build_plan(self.controller, self.account, self.page)
        self.assertEqual({entry['id'] for entry in plan['entries']}, {self.ids[1], self.ids[2]})
        self.assertEqual(plan['skipped'], {'busy': 1, 'soon': 1})
        apply_plan(self.controller, self.account, self.page, plan)
        self.assertEqual(self.store.get(self.ids[3])['state'], 'armed')
        self.assertEqual(self.store.get(self.ids[4])['state'], 'paused')
        self.assertLess(self.store.get(self.ids[2])['due_at'], self.store.get(self.ids[1])['due_at'])

    def test_a_plan_that_no_longer_matches_changes_nothing(self):
        self.ids = {1: self.armed(1, 1, 0), 2: self.armed(2, 9, 1)}
        plan = build_plan(self.controller, self.account, self.page)
        self.store.update(self.ids[2], due_at=self.start+9*HOUR)  # Someone moved a time after the preview.
        before = self.times()
        with self.assertRaises(ValueError):
            apply_plan(self.controller, self.account, self.page, plan)
        self.assertEqual(self.times(), before)

    def test_the_store_changes_all_rows_or_none(self):
        self.ids = {1: self.armed(1, 1, 0), 2: self.armed(2, 9, 1)}
        old = self.times()
        with self.assertRaises(ValueError):  # The second row's expected time is wrong: the first must not move either.
            self.store.reorder_times(self.account, self.page, {self.ids[1]: old[2], self.ids[2]: old[1]},
                                     {self.ids[1]: old[1], self.ids[2]: old[2]+1})
        self.assertEqual(self.times(), old)
        with self.store.database() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM schedule_reorders').fetchone()[0], 0)
        with self.assertRaises(ValueError):  # A time in the past is refused as a whole.
            self.store.reorder_times(self.account, self.page, {self.ids[1]: time.time()-10, self.ids[2]: old[1]},
                                     {self.ids[1]: old[1], self.ids[2]: old[2]})
        self.assertEqual(self.times(), old)


if __name__ == '__main__':
    unittest.main()
