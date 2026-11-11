"""Đổi tên Page (owner 11/11/2026): the new Facebook name follows into everything of the Page that is not sent yet, or nothing changes.
Built with Tèo's own stores (posts, schedules, plans, policies) in a temp folder; no Facebook, no real data."""
import json
from pathlib import Path
import sqlite3
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from teodub.common import atomic_json, hash_file
from teodub.facebook_accounts import FacebookAccounts
from teodub.page_ai import PageAIStore
from teodub.page_content_plan import ContentPlanStore, _digest, _json
from teodub.page_rename import RenameBlocked, rename_page
from teodub.page_schedule import PageScheduleController, PageScheduleStore, campaign_video_source

OLD, NEW = 'Review sản phẩm', 'Sài Thật Nói Thật'


class RenameTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.accounts = FacebookAccounts(self.directory)
        self.account = self.accounts.add_account('Fixture A')['id']
        self.page = self.accounts.save_page(self.account, OLD, 'facebook.com/profile.php?id=100', '100')['id']
        self.other = self.accounts.save_page(self.account, 'Trang khác', 'facebook.com/profile.php?id=200', '200')['id']
        self.store = PageScheduleStore(self.directory)
        owner = SimpleNamespace(studio=SimpleNamespace(data_dir=self.directory), sessions={}, status=Mock())
        owner.status.get.return_value = 'fixture'
        self.controller = PageScheduleController(owner)
        self.addCleanup(self.controller.close)
        self.number = 0

    def video(self, campaign_id):
        self.number += 1
        root = self.directory.resolve()
        folder = root/'campaigns'/campaign_id
        job = folder/'jobs'/f'{self.number:04}'
        output_dir = root/'outputs'/campaign_id
        output_dir.mkdir(parents=True, exist_ok=True)
        output = output_dir/f'Video {self.number}.mp4'
        output.write_bytes(b'synthetic video '+str(self.number).encode())
        outputs = dict(video=str(output))
        atomic_json(job/'state.json', dict(rendered=dict(outputs=outputs, hashes=dict(video=hash_file(output)))))
        atomic_json(folder/'campaign.json', dict(id=campaign_id, name='Synthetic campaign', output_dir=str(output_dir),
                    items=[dict(number=self.number, status='completed', job=str(job), outputs=outputs)]))
        return folder, self.number

    def schedule(self, state='paused', page=None, due=3600):
        page = page or self.page
        source = campaign_video_source(self.directory, *self.video(f'{self.number+1:032x}'))
        row = self.store.create(self.account, page, source, 'Caption', '#a', time.time()+due)
        if state != 'scheduled':
            self.store.update(row['id'], state=state)
        return self.store.get(row['id'])

    def rename_registry(self):
        self.accounts.save_page(self.account, NEW, 'facebook.com/profile.php?id=100', '100', record_id=self.page)

    def post_name(self, post_id):
        with self.store.activity.database() as db:
            return db.execute('SELECT target_name FROM posts WHERE id=?', (post_id,)).fetchone()[0]

    def plan(self, page=None, name=OLD):
        page = page or self.page
        target = dict(name=name, page_id='100', url='https://www.facebook.com/profile.php?id=100')
        document = dict(plan_id='p1', title='Kế hoạch '+name, items=[], sources={})
        with ContentPlanStore(self.directory).database() as db:
            db.execute('INSERT INTO plans (record_id,plan_id,account_id,page_key,thread_id,title,digest,target,document,source_turn_id,fetched_at,'
                       'state) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                       ('r'+page, 'p1', self.account, page, 't1', 'Kế hoạch', _digest(dict(thread_id='t1', target=target, document=document)),
                        _json(target), _json(document), 'turn', 'now', 'approved'))
        return 'r'+page, document

    def policy(self, name=OLD):
        config = dict(target=dict(name=name, page_id='100', url='https://www.facebook.com/profile.php?id=100'), goal='g', interval_days=1)
        with PageAIStore(self.directory).database() as db:
            db.execute('INSERT INTO policies(account_id,page_key,revision,config,enabled,next_at) VALUES (?,?,?,?,?,?)',
                       (self.account, self.page, 'rev1', json.dumps(config, sort_keys=True, ensure_ascii=False), 0, 1.0))

    def plan_row(self, record):
        with ContentPlanStore(self.directory).database() as db:
            return dict(db.execute('SELECT * FROM plans WHERE record_id=?', (record,)).fetchone())

    def snapshot_name(self, ident):
        return self.store.get(ident)['snapshot']['target_name']

    def test_the_old_name_blocks_posting_and_the_rename_lets_it_through(self):
        waiting = self.schedule('paused')
        self.rename_registry()  # Facebook (and the Page list) now say the new name: the approved snapshot no longer matches.
        with self.assertRaisesRegex(ValueError, 'Page đã đổi'):
            self.controller.verify_snapshot(self.store.get(waiting['id']))
        counts = rename_page(self.directory, self.account, self.page, OLD, NEW)
        self.assertEqual((counts['posts'], counts['schedules']), (1, 1))
        self.assertEqual(self.snapshot_name(waiting['id']), NEW)
        self.assertEqual(self.post_name(waiting['post_id']), NEW)
        self.controller.verify_snapshot(self.store.get(waiting['id']))  # The real check Tèo makes before posting passes again.

    def test_waiting_armed_and_paused_schedules_all_follow_and_the_rest_is_left_alone(self):
        paused = self.schedule('paused')
        later = self.schedule('scheduled', due=7200)
        sent = self.schedule('submitted')
        done = self.schedule('published')
        attention = self.schedule('needs_attention')
        elsewhere = self.schedule('paused', page=self.other)
        counts = rename_page(self.directory, self.account, self.page, OLD, NEW)
        self.assertEqual((counts['posts'], counts['schedules']), (2, 2))
        self.assertEqual([self.snapshot_name(row['id']) for row in (paused, later)], [NEW, NEW])
        for row in (sent, done, attention):
            with self.subTest(state=row['state']):
                self.assertEqual(self.snapshot_name(row['id']), OLD)
                self.assertEqual(self.post_name(row['post_id']), OLD)
        self.assertEqual((self.snapshot_name(elsewhere['id']), self.post_name(elsewhere['post_id'])), ('Trang khác', 'Trang khác'))

    def test_plans_follow_with_a_digest_made_again_and_other_pages_plans_do_not(self):
        record, document = self.plan()
        other_record, _ = self.plan(page=self.other)
        before = self.plan_row(record)
        counts = rename_page(self.directory, self.account, self.page, OLD, NEW)
        self.assertEqual(counts['plans'], 1)
        after = self.plan_row(record)
        target = json.loads(after['target'])
        self.assertEqual(target['name'], NEW)
        self.assertEqual(after['digest'], _digest(dict(thread_id='t1', target=target, document=document)))
        self.assertNotEqual(after['digest'], before['digest'])
        self.assertEqual((after['document'], after['title']), (before['document'], before['title']))  # The text is history; not rewritten.
        self.assertEqual(self.plan_row(other_record)['digest'], _digest(dict(thread_id='t1', target=json.loads(self.plan_row(other_record)['target']),
                                                                              document=document)))

    def test_the_automatic_posting_policy_keeps_its_revision_and_only_the_name_changes(self):
        self.policy()
        self.assertEqual(rename_page(self.directory, self.account, self.page, OLD, NEW)['policies'], 1)
        with PageAIStore(self.directory).database() as db:
            row = db.execute('SELECT revision,config FROM policies').fetchone()
        config = json.loads(row['config'])
        self.assertEqual((row['revision'], config['target']['name'], config['goal']), ('rev1', NEW, 'g'))

    def test_nothing_changes_while_a_post_is_being_sent_or_is_about_to_go(self):
        for state, due in (('submitting', 3600), ('verifying', 3600), ('preparing', 3600), ('scheduled', 300), ('armed', 60)):
            with self.subTest(state=state):
                row = self.schedule(state, due=due)
                paused = self.schedule('paused')
                with self.assertRaises(RenameBlocked):
                    rename_page(self.directory, self.account, self.page, OLD, NEW)
                self.assertEqual(self.snapshot_name(paused['id']), OLD)  # All or nothing.
                self.assertEqual(self.post_name(paused['post_id']), OLD)
                self.store.update(row['id'], state='removed')
                self.store.update(paused['id'], state='removed')

    def test_a_plan_that_cannot_be_checked_stops_the_whole_rename_and_undoes_the_schedules(self):
        waiting = self.schedule('paused')
        record, _ = self.plan()
        db = sqlite3.connect(self.directory/'page-publishing'/'content-plans.sqlite3')
        db.execute('UPDATE plans SET digest=? WHERE record_id=?', ('f'*64, record))
        db.commit()
        db.close()
        with self.assertRaisesRegex(RenameBlocked, 'mã kiểm tra'):
            rename_page(self.directory, self.account, self.page, OLD, NEW)
        self.assertEqual(self.snapshot_name(waiting['id']), OLD)
        self.assertEqual(self.post_name(waiting['post_id']), OLD)
        self.assertEqual(json.loads(self.plan_row(record)['target'])['name'], OLD)

    def test_a_waiting_schedule_that_already_says_something_else_stops_the_rename(self):
        waiting = self.schedule('paused')
        with self.store.activity.database() as db:
            db.execute('UPDATE posts SET target_name=? WHERE id=?', ('Tên lạ', waiting['post_id']))
        with self.assertRaisesRegex(RenameBlocked, 'khác tên đã lưu'):
            rename_page(self.directory, self.account, self.page, OLD, NEW)

    def test_running_it_again_after_it_was_done_changes_nothing_and_does_not_block(self):
        waiting = self.schedule('paused')
        self.plan()
        rename_page(self.directory, self.account, self.page, OLD, NEW)
        again = rename_page(self.directory, self.account, self.page, OLD, NEW)  # e.g. the Page list could not be saved the first time
        self.assertEqual(again, dict(posts=0, schedules=0, plans=0, policies=0))
        self.assertEqual(self.snapshot_name(waiting['id']), NEW)

    def test_a_page_with_nothing_saved_or_bad_names_is_handled(self):
        self.assertEqual(rename_page(self.directory/'nothing', self.account, self.page, OLD, NEW), dict(posts=0, schedules=0, plans=0, policies=0))
        for old, new in (('', NEW), (OLD, ''), (OLD, OLD)):
            with self.subTest(old=old, new=new), self.assertRaises(RenameBlocked):
                rename_page(self.directory, self.account, self.page, old, new)

    def test_a_database_that_is_locked_blocks_without_changing_anything(self):
        waiting = self.schedule('paused')
        holder = sqlite3.connect(self.directory/'page-publishing'/'content-plans.sqlite3', isolation_level=None)
        self.plan()
        holder.execute('BEGIN IMMEDIATE')
        self.addCleanup(holder.close)
        started = time.monotonic()
        with self.assertRaisesRegex(RenameBlocked, 'bận'):
            rename_page(self.directory, self.account, self.page, OLD, NEW)
        self.assertLess(time.monotonic()-started, 15)
        self.assertEqual(self.snapshot_name(waiting['id']), OLD)


if __name__ == '__main__':
    unittest.main()
