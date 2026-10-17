"""Passive posting summaries over synthetic campaign files and temporary SQLite stores."""
from copy import deepcopy
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

from teodub.campaign import read_json
from teodub.campaign_posting import campaign_posting_statuses
from teodub.common import atomic_json, hash_file
from teodub.facebook_accounts import FacebookAccounts
from teodub.page_schedule import PageScheduleStore, campaign_video_source


class CampaignPostingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data_dir = Path(self.temp.name)
        self.directory, self.manifest = self.video_campaign('a' * 32)
        self.accounts = FacebookAccounts(self.data_dir)
        self.account = self.accounts.add_account('Synthetic A')['id']
        self.page = self.accounts.save_page(
            self.account, 'Synthetic Page A', 'facebook.com/profile.php?id=100', '100')['id']
        self.second_page = self.accounts.save_page(
            self.account, 'Synthetic Page A2', 'facebook.com/profile.php?id=101', '101')['id']
        self.other = self.accounts.add_account('Synthetic B')['id']
        self.other_page = self.accounts.save_page(
            self.other, 'Synthetic Page B', 'facebook.com/profile.php?id=200', '200')['id']
        self.store = PageScheduleStore(self.data_dir)

    def video_campaign(self, ident, count=2):
        directory = self.data_dir / 'campaigns' / ident
        output_dir = self.data_dir / 'outputs' / ident
        output_dir.mkdir(parents=True)
        items = []
        for number in range(1, count + 1):
            job = directory / 'jobs' / f'{number:04}'
            video = output_dir / f'Video {number}.mp4'
            video.write_bytes(f'Synthetic rendered video {ident} {number}'.encode())
            outputs = dict(video=str(video))
            atomic_json(job / 'state.json', dict(rendered=dict(
                outputs=outputs, hashes=dict(video=hash_file(video)))))
            items.append(dict(number=number, status='completed', job=str(job), outputs=outputs))
        manifest = dict(id=ident, name='Synthetic campaign', output_dir=str(output_dir), items=items)
        atomic_json(directory / 'campaign.json', manifest)
        return directory, manifest

    def schedule(self, number=1, *, account=None, page=None, directory=None):
        source = campaign_video_source(self.data_dir, directory or self.directory, number)
        return self.store.create(account or self.account, page or self.page, source,
                                 'Synthetic approved caption', '#fixture', time.time() + 3600)

    def summaries(self, *, manifest=None, directory=None):
        return campaign_posting_statuses(self.data_dir, directory or self.directory,
                                        self.manifest if manifest is None else manifest)

    def summary(self, number=1):
        return self.summaries()[number]

    def scope(self, row):
        return row['account_id'], row['page_key'], row['post_id']

    def post_state(self, row, state):
        return self.store.activity.transition(*self.scope(row), state, 'Synthetic state')

    def publish(self, row):
        scope = self.scope(row)
        post = self.post_state(row, 'preparing')
        review = dict(account_id=row['account_id'], page_key=row['page_key'], post_id=row['post_id'],
                      **{key: post[key] for key in ('target_name', 'target_url', 'target_id',
                                                    'body', 'kind', 'browser_finish_mode')},
                      media=[dict(saved, sha256=hash_file(saved['path'])) for saved in post['media']])
        self.store.activity.save_browser_review(*scope, review)
        self.post_state(row, 'submitting')
        self.post_state(row, 'processing')
        url = f"https://www.facebook.com/{post['target_id']}/posts/{row['post_id']}"
        self.store.activity.record_browser_receipt(*scope, dict(source='visible_dom', url=url,
                                                               checked_at='synthetic-fixture'))
        return self.store.activity.transition(*scope, 'published', 'Synthetic observed publication',
                                               external_url=url)

    def change_schedule(self, row, **changes):
        with closing(sqlite3.connect(self.store.path)) as conn, conn:
            conn.execute('UPDATE schedules SET ' + ','.join(key + '=?' for key in changes) + ' WHERE id=?',
                         (*changes.values(), row['id']))

    def change_post(self, row, **changes):
        with closing(sqlite3.connect(self.store.activity.path)) as conn, conn:
            conn.execute('UPDATE posts SET ' + ','.join(key + '=?' for key in changes) + ' WHERE id=?',
                         (*changes.values(), row['post_id']))

    def snapshot_files(self):
        return {str(path.relative_to(self.data_dir)): path.read_bytes()
                for path in self.data_dir.rglob('*') if path.is_file()}

    def assert_unconfirmed(self, result):
        self.assertEqual(result['published'], 0)
        self.assertFalse(result['label'].startswith('Đăng thành công'), result)

    def test_missing_stores_show_each_video_unposted_without_creating_files(self):
        before = self.snapshot_files()
        directories = {str(path) for path in self.data_dir.rglob('*') if path.is_dir()}
        results = self.summaries()
        self.assertEqual(set(results), {1, 2})
        for result in results.values():
            self.assertEqual((result['label'], result['published'], result['targets']), ('Chưa đăng', 0, 0))
        self.assertEqual(self.snapshot_files(), before)
        self.assertEqual({str(path) for path in self.data_dir.rglob('*') if path.is_dir()}, directories)
        self.assertFalse(self.store.path.exists())
        self.assertFalse(self.store.activity.path.exists())

    def test_saved_and_armed_schedules_are_waiting_not_publication(self):
        row = self.schedule()
        for state in ('scheduled', 'armed'):
            with self.subTest(state=state):
                self.store.update(row['id'], state=state)
                result = self.summary()
                self.assertEqual(result['label'], 'Đang lên lịch')
                self.assertEqual((result['published'], result['targets']), (0, 1))
                self.assertEqual(self.summary(2)['label'], 'Chưa đăng')

    def test_preparing_and_submitting_schedule_states_are_active(self):
        row = self.schedule()
        for state in ('preparing', 'submitting'):
            with self.subTest(state=state):
                self.store.update(row['id'], state=state)
                self.assertEqual(self.summary()['label'], 'Đang đăng')

    def test_bound_post_preparation_submission_and_processing_are_active(self):
        row = self.schedule()
        for state in ('preparing', 'submitting', 'processing'):
            with self.subTest(state=state):
                self.post_state(row, state)
                self.assertEqual(self.summary()['label'], 'Đang đăng')

    def test_submitted_without_verification_is_neither_success_nor_posting(self):
        row=self.schedule()
        self.change_schedule(row,state='submitted',phase='publish',attempted_at='Synthetic prior send')
        self.post_state(row,'unknown')
        result=self.summary()
        self.assertEqual(result['label'],'Đã gửi — chưa xác minh')
        self.assertEqual(result['published'],0)

    def test_paused_schedule_has_an_explicit_pause_label(self):
        row = self.schedule()
        self.store.update(row['id'], state='paused')
        result = self.summary()
        self.assertEqual(result['label'], 'Tạm dừng lịch')
        self.assertEqual((result['published'], result['targets']), (0, 1))

    def test_unknown_and_attempted_attention_are_not_reported_as_never_posted(self):
        row = self.schedule()
        self.post_state(row, 'unknown')
        self.store.update(row['id'], state='needs_attention', attempted_at='synthetic-attempt')
        result = self.summary()
        self.assertEqual(result['label'], 'Cần kiểm tra')
        self.assert_unconfirmed(result)

    def test_failed_post_is_explicit_and_never_counts_as_success(self):
        row = self.schedule()
        self.post_state(row, 'failed')
        self.assertEqual(self.summary()['label'], 'Đăng thất bại')
        self.assert_unconfirmed(self.summary())

    def test_durable_success_wins_over_paused_or_attention_schedule(self):
        row = self.schedule()
        self.publish(row)
        for state in ('scheduled', 'paused', 'needs_attention', 'published'):
            with self.subTest(state=state):
                self.store.update(row['id'], state=state, attempted_at='synthetic-attempt')
                result = self.summary()
                self.assertEqual(result['label'], 'Đăng thành công')
                self.assertEqual((result['published'], result['targets']), (1, 1))

    def test_schedule_publication_claim_without_durable_post_success_is_not_success(self):
        row = self.schedule()
        self.store.update(row['id'], state='published', finished_at='synthetic-finish')
        result = self.summary()
        self.assert_unconfirmed(result)
        self.assertEqual(result['label'], 'Cần kiểm tra')

    def test_page_manager_publishing_a_replaced_draft_video_cannot_confirm_the_original(self):
        row = self.schedule()
        self.store.activity.save_draft(row['account_id'], row['page_key'], 'Different approved Page draft',
                                       [self.manifest['items'][1]['outputs']['video']], post_id=row['post_id'])
        self.publish(row)
        result = self.summary()
        self.assertEqual(result['label'], 'Cần kiểm tra')
        self.assert_unconfirmed(result)

    def test_interrupted_attempted_schedule_is_not_live_posting_from_a_stale_busy_post(self):
        row = self.schedule()
        for state in ('paused', 'needs_attention'):
            for post_state in ('preparing', 'submitting', 'processing'):
                with self.subTest(state=state, post_state=post_state):
                    self.store.update(row['id'], state=state, attempted_at='synthetic-interrupted-attempt')
                    self.post_state(row, post_state)
                    result = self.summary()
                    self.assertEqual(result['label'], 'Cần kiểm tra')
                    self.assert_unconfirmed(result)

    def test_manual_url_is_only_unverified_evidence(self):
        row = self.schedule()
        self.post_state(row, 'unknown')
        self.store.update(row['id'], state='needs_attention', attempted_at='synthetic-attempt')
        self.store.activity.record_manual_result(*self.scope(row), 'https://facebook.com/100/posts/123')
        result = self.summary()
        self.assertEqual(result['label'], 'Cần kiểm tra')
        self.assert_unconfirmed(result)

    def test_post_publication_requires_a_matching_visible_dom_receipt_and_url(self):
        row = self.schedule()
        post = self.publish(row)
        receipt = post['browser_receipt']
        cases = [
            dict(browser_receipt='{}'),
            dict(browser_receipt=json.dumps(dict(receipt, source='manual'))),
            dict(browser_receipt=json.dumps(dict(receipt, url='https://www.facebook.com/100/posts/wrong'))),
            dict(browser_receipt=json.dumps(dict(receipt, url=''))),
            dict(external_url=''),
        ]
        for changes in cases:
            with self.subTest(changes=changes):
                self.change_post(row, browser_receipt=json.dumps(receipt), external_url=post['external_url'])
                self.change_post(row, **changes)
                self.assert_unconfirmed(self.summary())
        self.change_post(row, browser_receipt=json.dumps(receipt), external_url=post['external_url'])
        self.assertEqual(self.summary()['label'], 'Đăng thành công')

    def test_all_pages_and_accounts_are_aggregated_and_partial_success_is_visible(self):
        first = self.schedule()
        second = self.schedule(account=self.other, page=self.other_page)
        self.publish(first)
        result = self.summary()
        self.assertEqual(result['label'], 'Đang lên lịch · đã đăng 1/2')
        self.assertEqual((result['published'], result['targets']), (1, 2))
        self.publish(second)
        result = self.summary()
        self.assertEqual(result['label'], 'Đăng thành công')
        self.assertEqual((result['published'], result['targets']), (2, 2))

    def test_active_target_takes_precedence_while_preserving_partial_success(self):
        first = self.schedule()
        second = self.schedule(page=self.second_page)
        self.publish(first)
        self.post_state(second, 'processing')
        result = self.summary()
        self.assertEqual(result['label'], 'Đang đăng · đã đăng 1/2')
        self.assertEqual((result['published'], result['targets']), (1, 2))

    def test_partial_failure_does_not_hide_a_successful_target(self):
        first = self.schedule()
        second = self.schedule(account=self.other, page=self.other_page)
        self.publish(first)
        self.post_state(second, 'failed')
        result = self.summary()
        self.assertEqual(result['label'], 'Đăng thất bại · đã đăng 1/2')
        self.assertEqual((result['published'], result['targets']), (1, 2))

    def test_cancelled_history_does_not_count_as_an_active_target(self):
        old = self.schedule()
        self.store.cancel_unattempted(old['id'])
        result = self.summary()
        self.assertEqual((result['label'], result['published'], result['targets']), ('Chưa đăng', 0, 0))
        self.assertTrue(result['details'])
        replacement = self.schedule()
        self.publish(replacement)
        result = self.summary()
        self.assertEqual((result['label'], result['published'], result['targets']), ('Đăng thành công', 1, 1))
        self.assertTrue(result['details'])

    def test_cancelled_second_page_does_not_block_verified_success(self):
        first = self.schedule()
        cancelled = self.schedule(account=self.other, page=self.other_page)
        self.store.cancel_unattempted(cancelled['id'])
        self.publish(first)
        result = self.summary()
        self.assertEqual((result['label'], result['published'], result['targets']), ('Đăng thành công', 1, 1))

    def test_removed_untouched_history_does_not_count_as_waiting_or_active_target(self):
        row=self.schedule()
        self.change_schedule(row,state='removed',source_key='removed:'+row['id'])
        before=self.snapshot_files()
        result=self.summary()
        self.assertEqual((result['label'],result['published'],result['targets']),('Chưa đăng',0,0))
        self.assertIn('Đã xóa khỏi danh sách Tèo',result['details'])
        self.assertEqual(self.snapshot_files(),before)

    def test_removed_untouched_second_target_preserves_another_verified_success(self):
        first=self.schedule()
        removed=self.schedule(page=self.second_page)
        self.change_schedule(removed,state='removed',source_key='removed:'+removed['id'])
        self.publish(first)
        result=self.summary()
        self.assertEqual((result['label'],result['published'],result['targets']),('Đăng thành công',1,1))

    def test_removed_already_cancelled_row_keeps_its_history_without_reserving_target(self):
        row=self.store.cancel_unattempted(self.schedule()['id'])
        self.change_schedule(row,state='removed')
        result=self.summary()
        self.assertEqual((result['label'],result['published'],result['targets']),('Chưa đăng',0,0))
        self.assertEqual(len(result['entries']),1)
        self.assertEqual(result['entries'][0]['schedule_id'],row['id'])
        self.assertIn('Đã xóa khỏi danh sách Tèo',result['details'])

    def test_removed_unknown_publication_stays_uncertain_and_keeps_proof(self):
        row=self.schedule()
        self.post_state(row,'unknown')
        self.change_schedule(row,state='removed',attempted_at='synthetic-attempt',phase='publish')
        before=self.snapshot_files()
        result=self.summary()
        self.assertEqual((result['label'],result['targets']),('Cần kiểm tra',1))
        self.assert_unconfirmed(result)
        self.assertFalse(result['entries'][0]['cancelled'])
        self.assertEqual(self.snapshot_files(),before)

    def test_removed_verified_publication_remains_success_even_with_bad_release_key(self):
        row=self.schedule()
        post=self.publish(row)
        for key in (row['source_key'],'removed:'+row['id']):
            with self.subTest(source_key=key):
                self.change_schedule(row,state='removed',source_key=key)
                before=self.snapshot_files()
                result=self.summary()
                self.assertEqual((result['label'],result['published'],result['targets']),('Đăng thành công',1,1))
                self.assertEqual(result['entries'][0]['url'],post['external_url'])
                self.assertEqual(self.snapshot_files(),before)

    def test_removed_stale_busy_post_does_not_claim_live_posting(self):
        row=self.schedule()
        self.change_schedule(row,state='removed',attempted_at='synthetic-interrupted-attempt')
        for state in ('preparing','review','submitting','processing'):
            with self.subTest(post_state=state):
                self.post_state(row,state)
                result=self.summary()
                self.assertEqual(result['label'],'Cần kiểm tra')
                self.assertIn('Đã xóa khỏi danh sách Tèo',result['details'])
                self.assert_unconfirmed(result)

    def test_removed_false_release_with_attempt_or_corrupt_proof_is_not_assumed_untouched(self):
        row=self.schedule()
        self.change_schedule(row,state='removed',source_key='removed:'+row['id'])
        for changes in (dict(attempt=1),dict(browser_receipt='{broken'),dict(browser_review='{broken'),
                        dict(browser_tracking='{"submitted_at":1792033200}'),
                        dict(remote_assets='{broken'),dict(manual_url='https://facebook.com/100/posts/unverified')):
            with self.subTest(changes=changes):
                self.change_post(row,attempt=0,browser_receipt='{}',browser_review='{}',browser_tracking='{}',remote_assets='[]',manual_url='')
                self.change_post(row,**changes)
                result=self.summary()
                self.assertEqual((result['label'],result['targets']),('Cần kiểm tra',1))
                self.assert_unconfirmed(result)

    def test_archiving_verified_post_preserves_publication_evidence(self):
        row = self.schedule()
        self.publish(row)
        self.store.activity.archive(*self.scope(row))
        self.assertEqual(self.summary()['label'], 'Đăng thành công')

    def test_foreign_account_or_page_post_cannot_supply_publication_evidence(self):
        row = self.schedule()
        self.publish(row)
        for field, foreign in (('account_id', self.other), ('page_key', self.other_page)):
            with self.subTest(field=field):
                self.change_post(row, **{field: foreign})
                self.assert_unconfirmed(self.summary())
                self.change_post(row, **{field: row[field]})
        self.assertEqual(self.summary()['label'], 'Đăng thành công')

    def test_another_campaign_schedule_never_supplies_this_campaign_status(self):
        foreign_directory, _ = self.video_campaign('b' * 32)
        foreign = self.schedule(directory=foreign_directory)
        self.publish(foreign)
        self.assertEqual((self.summary()['label'], self.summary()['targets']), ('Chưa đăng', 0))

    def test_tampered_source_identity_and_hash_never_show_confirmed_publication(self):
        row = self.schedule()
        self.publish(row)
        source = row['source']
        cases = [dict(key='b' * 32 + ':1'), dict(number=2),
                 dict(campaign=str(self.data_dir / 'campaigns' / ('b' * 32))),
                 dict(path=self.manifest['items'][1]['outputs']['video']), dict(sha256='0' * 64)]
        for changes in cases:
            with self.subTest(changes=changes):
                self.change_schedule(row, source=json.dumps(dict(source, **changes)))
                self.assert_unconfirmed(self.summary())
        self.change_schedule(row, source=json.dumps(source))
        self.assertEqual(self.summary()['label'], 'Đăng thành công')

    def test_active_column_key_keeps_corrupt_source_on_its_original_video_row(self):
        row = self.schedule()
        self.publish(row)
        wrong_source = campaign_video_source(self.data_dir, self.directory, 2)
        self.change_schedule(row, source=json.dumps(wrong_source))
        results = self.summaries()
        self.assertEqual((results[1]['label'], results[1]['published'], results[1]['targets']),
                         ('Cần kiểm tra', 0, 1))
        self.assertEqual((results[2]['label'], results[2]['published'], results[2]['targets']),
                         ('Chưa đăng', 0, 0))

    def test_durable_post_must_match_the_approved_snapshot_body_and_modes(self):
        row = self.schedule()
        post = self.publish(row)
        for key, changed in (('body', 'Different caption'), ('delivery_mode', 'api'),
                             ('browser_finish_mode', 'manual')):
            with self.subTest(key=key):
                self.change_post(row, **{key: changed})
                self.assert_unconfirmed(self.summary())
                self.change_post(row, **{key: post[key]})
        self.assertEqual(self.summary()['label'], 'Đăng thành công')

    def test_matching_post_and_snapshot_cannot_publish_a_different_source_video(self):
        row = self.schedule()
        self.store.activity.save_draft(row['account_id'], row['page_key'], row['body'],
                                       [self.manifest['items'][1]['outputs']['video']], post_id=row['post_id'])
        post = self.publish(row)
        snapshot = dict(row['snapshot'], media=post['media'])
        self.change_schedule(row, snapshot=json.dumps(snapshot))
        self.assert_unconfirmed(self.summary())

    def test_current_output_path_replacement_is_not_the_previously_published_video(self):
        row = self.schedule()
        self.publish(row)
        manifest = deepcopy(self.manifest)
        video = Path(manifest['output_dir']) / 'replacement.mp4'
        video.write_bytes(b'Synthetic replacement output')
        manifest['items'][0]['outputs'] = dict(video=str(video))
        atomic_json(Path(manifest['items'][0]['job']) / 'state.json', dict(rendered=dict(
            outputs=manifest['items'][0]['outputs'], hashes=dict(video=hash_file(video)))))
        self.assert_unconfirmed(self.summaries(manifest=manifest)[1])

    def test_changed_current_render_hash_never_reuses_previous_success(self):
        row = self.schedule()
        self.publish(row)
        state_path = Path(self.manifest['items'][0]['job']) / 'state.json'
        state = read_json(state_path)
        state['rendered']['hashes']['video'] = '0' * 64
        atomic_json(state_path, state)
        self.assert_unconfirmed(self.summary())

    def test_missing_render_state_never_reuses_previous_success(self):
        row = self.schedule()
        self.publish(row)
        (Path(self.manifest['items'][0]['job']) / 'state.json').unlink()
        self.assert_unconfirmed(self.summary())

    def test_missing_bound_post_requires_checking_and_does_not_modify_stores(self):
        row = self.schedule()
        with closing(sqlite3.connect(self.store.activity.path)) as conn, conn:
            conn.execute('DELETE FROM posts WHERE id=?', (row['post_id'],))
        before = self.snapshot_files()
        result = self.summary()
        self.assertEqual(result['label'], 'Cần kiểm tra')
        self.assert_unconfirmed(result)
        self.assertEqual(self.snapshot_files(), before)

    def test_missing_activity_store_with_a_schedule_is_not_created_or_assumed_unposted(self):
        self.schedule()
        self.store.activity.path.unlink()
        before = self.snapshot_files()
        result = self.summary()
        self.assertEqual(result['label'], 'Cần kiểm tra')
        self.assert_unconfirmed(result)
        self.assertFalse(self.store.activity.path.exists())
        self.assertEqual(self.snapshot_files(), before)

    def test_legacy_activity_schema_is_read_without_migration_and_cannot_confirm(self):
        row = self.schedule()
        self.publish(row)
        with closing(sqlite3.connect(self.store.activity.path)) as conn, conn:
            conn.execute('ALTER TABLE posts DROP COLUMN browser_receipt')
        before = self.snapshot_files()
        self.assert_unconfirmed(self.summary())
        self.assertEqual(self.snapshot_files(), before)
        with closing(sqlite3.connect(self.store.activity.path)) as conn, conn:
            names = {row[1] for row in conn.execute('PRAGMA table_info(posts)')}
        self.assertNotIn('browser_receipt', names)

    def test_invalid_sqlite_store_fails_closed_without_overwriting_it(self):
        self.schedule()
        for path in (self.store.path, self.store.activity.path):
            with self.subTest(path=path.name):
                original = path.read_bytes()
                path.write_bytes(b'This synthetic file is not a SQLite database.')
                before = self.snapshot_files()
                result = self.summary()
                self.assertEqual(result['label'], 'Cần kiểm tra')
                self.assert_unconfirmed(result)
                self.assertEqual(self.snapshot_files(), before)
                path.write_bytes(original)

    def test_sqlite_lock_or_read_error_is_visible_and_never_success(self):
        self.schedule()
        with patch('teodub.campaign_posting.sqlite3.connect', side_effect=sqlite3.OperationalError('synthetic read failure')):
            result = self.summary()
        self.assertEqual(result['label'], 'Cần kiểm tra')
        self.assert_unconfirmed(result)
        self.assertTrue(result['details'])

    def test_malformed_source_json_fails_closed_without_crashing(self):
        row = self.schedule()
        self.publish(row)
        self.change_schedule(row, source='{not json')
        self.assert_unconfirmed(self.summary())

    def test_statuses_reload_durable_changes_without_a_browser_or_controller(self):
        row = self.schedule()
        self.assertEqual(self.summary()['label'], 'Đang lên lịch')
        self.post_state(row, 'processing')
        self.assertEqual(self.summary()['label'], 'Đang đăng')
        self.post_state(row, 'unknown')
        self.assertEqual(self.summary()['label'], 'Cần kiểm tra')
        self.publish(row)
        self.assertEqual(self.summary()['label'], 'Đăng thành công')

    def test_read_only_summary_never_enters_mutating_stores_or_initializes_a_scheduler(self):
        row = self.schedule()
        self.publish(row)
        before = self.snapshot_files()
        with (patch('teodub.page_schedule.PageScheduleStore.database', side_effect=AssertionError('mutable schedule store')),
              patch('teodub.page_activity.PageActivity.database', side_effect=AssertionError('mutable activity store')),
              patch('teodub.page_schedule.PageScheduleController', side_effect=AssertionError('scheduler initialization'))):
            result = self.summary()
        self.assertEqual(result['label'], 'Đăng thành công')
        self.assertEqual(self.snapshot_files(), before)

    def test_sqlite_connections_are_explicitly_read_only(self):
        row = self.schedule()
        self.publish(row)
        connect = sqlite3.connect
        with patch('teodub.campaign_posting.sqlite3.connect', wraps=connect) as spy:
            self.assertEqual(self.summary()['label'], 'Đăng thành công')
        self.assertTrue(spy.call_args_list)
        for call in spy.call_args_list:
            self.assertTrue(call.kwargs.get('uri'), call)
            self.assertIn('mode=ro', str(call.args[0]))


if __name__ == '__main__':
    unittest.main()
