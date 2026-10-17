"""Disposable reconciliation evidence/atomicity tests; no Facebook interaction."""
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import time
import unittest
from unittest.mock import patch

from tests import test_page_schedule as fixtures
from teodub.page_activity import now
from teodub.page_browser_reconcile import can_inspect, preview, commit_verified, association_matches, validate_current
from teodub.pipeline import _job_lock


def stamp(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat(timespec='seconds')


class BrowserReconcileStoreTests(unittest.TestCase):
    setUp = fixtures.ScheduleTests.setUp
    video = fixtures.ScheduleTests.video
    schedule = fixtures.ScheduleTests.schedule
    submit = fixtures.ScheduleTests.submit

    def attempted(self, campaign='a'*32, *, state='needs_attention', body=None, submitted=None):
        row = self.schedule(campaign)
        if body is not None:
            with self.store.activity.database() as conn:
                conn.execute('UPDATE posts SET body=? WHERE id=?', (body, row['post_id']))
            snapshot = dict(row['snapshot'], body=body)
            with self.store.database() as conn:
                conn.execute('UPDATE schedules SET body=?,snapshot=? WHERE id=?', (body, json.dumps(snapshot), row['id']))
            row = self.store.get(row['id'])
        submitted = int(time.time())-600 if submitted is None else submitted
        self.make_attempt(row['post_id'], row['source']['sha256'], submitted)
        row = self.store.update(row['id'], state=state, phase='publish', request_id='finished-request',
                                attempted_at=stamp(submitted), finished_at='')
        return row

    def make_attempt(self, post_id, digest, submitted):
        scope = self.account, self.page, post_id
        activity = self.store.activity
        post = activity.transition(*scope, 'preparing', 'Disposable browser preparation', expected={'draft'})
        review = dict(version=1, account_id=self.account, page_key=self.page, post_id=post_id,
            prepared_at=stamp(submitted-20), kind='video',
            **{key: post[key] for key in ('target_name', 'target_url', 'target_id', 'body', 'browser_finish_mode')},
            media=[dict(post['media'][0], sha256=digest)],
            composer=dict(layout='reel_settings', body=post['body'],
                          video_preview=[dict(src='blob:fixture', duration=30, width=1280, height=720)]))
        activity.save_browser_review(*scope, review)
        activity.transition(*scope, 'submitting', 'Disposable one Publish click', expected={'review'})
        activity.transition(*scope, 'processing', 'Disposable pending receipt', expected={'submitting'})
        activity.transition(*scope, 'unknown', 'Disposable missing receipt', expected={'processing'})
        with activity.database() as conn:
            conn.execute('UPDATE events SET at=? WHERE post_id=?', (stamp(submitted-20), post_id))
            for state, offset in [('submitting', 0), ('processing', 1), ('unknown', 60)]:
                conn.execute('UPDATE events SET at=? WHERE post_id=? AND state=?', (stamp(submitted+offset), post_id, state))

    def duplicate_attempt(self, row, *, submitted=None, archived=False, digest=None):
        post = self.store.activity.save_draft(self.account, self.page, row['body'], [row['source']['path']],
                                            delivery_mode='browser', browser_finish_mode='tool')
        self.make_attempt(post['id'], digest or row['source']['sha256'], submitted or int(time.time())-900)
        if archived:
            with self.store.activity.database() as conn:
                conn.execute('UPDATE posts SET archived=1 WHERE id=?', (post['id'],))
        return post['id']

    def view(self, row):
        return preview(self.store, row['account_id'], row['page_key'], row['id'])

    def proof(self, capture, *, timed=False):
        result = dict(source='visible_dom', preview_key=capture['preview_key'], url='https://www.facebook.com/reel/123456',
            checked_at=now(), target_id='100', owner_url='https://www.facebook.com/profile.php?id=100',
            body=capture['post']['body'], visible_video=True, video_verified=True, attempt_confirmed=True,
            observed_duration=30, observed_width=640, observed_height=360)
        if timed:
            result.update(association_mode='visible_time', timestamp_source='visible_dom',
                          published_at=stamp(datetime.fromisoformat(capture['submitted_at']).timestamp()+30))
        else:
            result.update(association_mode='unique_attempt_user_link', confirmed_attempt=True, explicit_result_url=result['url'])
        return result

    def capture(self, row):
        with self.store.database() as conn:
            schema = conn.execute("SELECT sql FROM sqlite_master WHERE name='browser_reconciliations'").fetchone()
            history = [dict(x) for x in conn.execute('SELECT * FROM browser_reconciliations')] if schema else []
        with self.store.activity.database() as conn:
            posts = [dict(x) for x in conn.execute('SELECT * FROM posts ORDER BY id')]
            events = [dict(x) for x in conn.execute('SELECT * FROM events ORDER BY id')]
        return dict(schedule=self.store.get(row['id']), posts=posts, events=events, history=history, schema=bool(schema))

    def assert_rejected(self, row, expected, proof):
        before = self.capture(row)
        with self.assertRaises((ValueError, OSError)):
            commit_verified(self.store, expected, proof)
        self.assertEqual(self.capture(row), before)
        self.assertEqual(self.calls, [])

    def test_unique_explicit_result_link_records_old_attempt_once_and_preserves_intent(self):
        row = self.attempted()
        expected = self.view(row)
        self.assertTrue(can_inspect(self.store, row))
        before = self.capture(row)
        proof = self.proof(expected)
        result = commit_verified(self.store, expected, proof)
        after = self.capture(row)
        self.assertEqual(result['id'], row['post_id'])
        self.assertEqual(result['schedule_id'], row['id'])
        self.assertEqual(result['state'], 'published')
        self.assertEqual(result['browser_receipt'], proof)
        self.assertEqual(result['published_at'], '')
        self.assertEqual(after['schedule']['state'], 'published')
        self.assertTrue(after['schedule']['finished_at'])
        for key in row.keys()-{'state', 'message', 'finished_at'}:
            self.assertEqual(after['schedule'][key], row[key], key)
        for key in expected['post'].keys()-{'state', 'message', 'browser_receipt', 'external_url', 'published_at', 'updated_at'}:
            self.assertEqual(result[key], expected['post'][key], key)
        self.assertEqual(after['events'][:-1], before['events'])
        self.assertEqual(after['events'][-1]['state'], 'published')
        self.assertEqual(len(after['history']), 1)
        self.assertEqual(json.loads(after['history'][0]['previous']), expected)
        self.assertEqual(commit_verified(self.store, expected, proof), result)
        self.assertEqual(self.capture(row), after)
        self.assertEqual(self.calls, [])

    def test_removed_attempt_is_verified_without_restoring_row_or_releasing_duplicate_slot(self):
        row = self.attempted()
        previous_key = row['source_key']
        with self.store.database() as conn:
            conn.execute("UPDATE schedules SET state='removed',source_key=? WHERE id=?", ('removed:'+row['id'], row['id']))
        expected = self.view(row)
        result = commit_verified(self.store, expected, self.proof(expected, timed=True))
        current = self.store.get(row['id'])
        self.assertEqual(current['state'], 'removed')
        self.assertEqual(current['source_key'], 'removed:'+row['id'])
        self.assertEqual(current['source']['key'], previous_key)
        self.assertEqual(result['state'], 'published')
        self.assertEqual(result['published_at'], result['browser_receipt']['published_at'])
        self.assertTrue(current['finished_at'])

    def test_draft_new_attempt_and_review_only_cannot_inherit_old_facebook_result(self):
        row = self.schedule()
        self.assertFalse(can_inspect(self.store, row))
        with self.assertRaises(ValueError):
            self.view(row)
        row = self.attempted('b'*32)
        with self.store.activity.database() as conn:
            conn.execute("UPDATE posts SET state='review' WHERE id=?", (row['post_id'],))
        self.assertFalse(can_inspect(self.store, row))
        with self.assertRaises(ValueError):
            self.view(row)

    def test_actual_submit_processing_and_aware_times_required(self):
        for mutation in ("DELETE FROM events WHERE state='submitting'", "DELETE FROM events WHERE state='processing'",
                         "UPDATE events SET at='2026-10-14T21:00:00' WHERE state='submitting'"):
            with self.subTest(mutation=mutation):
                row = self.attempted(hex(len(self.store.list())+1)[2:]*32)
                with self.store.activity.database() as conn:
                    conn.execute(mutation+' AND post_id=?', (row['post_id'],))
                self.assertFalse(can_inspect(self.store, row))
                with self.assertRaises(ValueError): self.view(row)

    def test_frozen_scope_body_media_and_video_metadata_fail_closed(self):
        row = self.attempted()
        original = self.view(row)['post']['browser_review']
        for mutate in (lambda x:x.update(account_id=self.other), lambda x:x.update(body='different'),
                       lambda x:x['media'][0].update(sha256='0'*64),
                       lambda x:x['composer']['video_preview'][0].update(duration=0),
                       lambda x:x['composer']['video_preview'][0].update(width=0)):
            with self.subTest(mutation=mutate):
                changed = deepcopy(original); mutate(changed)
                with self.store.activity.database() as conn:
                    conn.execute('UPDATE posts SET browser_review=? WHERE id=?', (json.dumps(changed), row['post_id']))
                self.assertFalse(can_inspect(self.store, row))
                with self.assertRaises(ValueError): self.view(row)

    def test_polling_eligibility_does_not_hash_or_scan_other_attempts(self):
        row = self.attempted()
        with patch('teodub.page_browser_reconcile.hash_file', side_effect=AssertionError('UI hashed')), \
             patch('teodub.page_browser_reconcile._attempts', side_effect=AssertionError('UI scanned')):
            self.assertTrue(can_inspect(self.store, row))

    def test_ui_preflight_checks_snapshot_without_hashing_large_media(self):
        row=self.attempted(); expected=self.view(row)
        with patch('teodub.page_browser_reconcile.hash_file',side_effect=AssertionError('UI hashed')):
            self.assertTrue(validate_current(self.store,expected))
            self.store.update(row['id'],message='Later scheduler observation')
            with self.assertRaises(ValueError): validate_current(self.store,expected)

    def test_direct_post_video_permalink_is_supported_and_page_or_insights_is_not(self):
        for index,url in enumerate(('https://www.facebook.com/100/posts/123', 'https://www.facebook.com/100/videos/124',
                    'https://www.facebook.com/permalink.php?id=100&story_fbid=125')):
            row=self.attempted(str(index+1)*32,body='Different link fixture '+str(index)); expected=self.view(row)
            proof=self.proof(expected); proof.update(url=url,explicit_result_url=url)
            result=commit_verified(self.store,expected,proof)
            self.assertEqual(result['external_url'],url)
        row=self.attempted('b'*32); expected=self.view(row)
        for url in ('https://www.facebook.com/profile.php?id=100',
                    'https://www.facebook.com/content/insights/?content_id=123',
                    'https://www.facebook.com@evil.test/reel/123',
                    'https://www.facebook.com:444/reel/123'):
            proof=self.proof(expected); proof.update(url=url,explicit_result_url=url)
            self.assert_rejected(row,expected,proof)

    def test_different_known_media_hash_does_not_create_false_caption_ambiguity(self):
        row=self.attempted()
        self.duplicate_attempt(row,digest='e'*64)
        expected=self.view(row)
        self.assertEqual(len(expected['attempt_candidates']),1)
        self.assertEqual(commit_verified(self.store,expected,self.proof(expected))['state'],'published')

    def test_old_submission_without_media_hash_is_conservatively_ambiguous(self):
        row=self.attempted(); other=self.duplicate_attempt(row)
        with self.store.activity.database() as conn:
            conn.execute("UPDATE posts SET browser_review='{}' WHERE id=?",(other,))
        expected=self.view(row)
        self.assertEqual(len(expected['attempt_candidates']),2)
        self.assert_rejected(row,expected,self.proof(expected))

    def test_wrong_account_page_and_removed_registry_page_reject(self):
        row = self.attempted()
        with self.assertRaises(ValueError): preview(self.store, self.other, self.page, row['id'])
        with self.assertRaises(ValueError): preview(self.store, self.account, self.other_page, row['id'])
        with patch.object(self.store.activity, 'page', side_effect=ValueError('missing Page')):
            self.assertFalse(can_inspect(self.store, row))
            with self.assertRaises(ValueError): self.view(row)

    def test_toast_manual_url_partial_caption_wrong_owner_and_different_video_are_not_proof(self):
        row = self.attempted(); expected = self.view(row)
        variations = dict(source='toast', preview_key='0'*64, body=expected['post']['body'].split()[0],
            target_id='200', owner_url='https://www.facebook.com/profile.php?id=200',
            url='https://www.facebook.com/profile.php?id=100', visible_video=False, video_verified=1,
            attempt_confirmed=1, observed_duration=33, observed_width=1000, observed_height=0)
        for key, value in variations.items():
            with self.subTest(key=key):
                proof=self.proof(expected); proof[key]=value
                self.assert_rejected(row, expected, proof)

    def test_unique_without_time_needs_boolean_confirmation_and_exact_explicit_link(self):
        row = self.attempted(); expected = self.view(row)
        for change in (dict(confirmed_attempt=False),dict(confirmed_attempt=1),dict(explicit_result_url=''),
                       dict(explicit_result_url='https://www.facebook.com/reel/999')):
            proof=self.proof(expected); proof.update(change)
            self.assert_rejected(row, expected, proof)

    def test_archived_same_caption_media_attempt_requires_timestamp_even_after_removal(self):
        row = self.attempted()
        self.duplicate_attempt(row, archived=True)
        expected = self.view(row)
        self.assertEqual(len(expected['attempt_candidates']), 2)
        self.assert_rejected(row, expected, self.proof(expected))
        result = commit_verified(self.store, expected, self.proof(expected, timed=True))
        self.assertEqual(result['state'], 'published')

    def test_timestamp_of_older_or_later_matching_attempt_cannot_label_this_attempt(self):
        submitted=int(time.time())-600
        row = self.attempted(submitted=submitted)
        self.duplicate_attempt(row, submitted=submitted-300)
        self.duplicate_attempt(row, submitted=submitted+300)
        expected = self.view(row)
        for published in (submitted-270, submitted+330):
            proof=self.proof(expected,timed=True); proof['published_at']=stamp(published)
            self.assertFalse(association_matches(expected,proof['published_at']))
            self.assert_rejected(row,expected,proof)
        self.assertTrue(association_matches(expected,stamp(submitted+30)))

    def test_identical_submission_second_is_ambiguous_even_with_timestamp(self):
        row=self.attempted(); expected=self.view(row)
        self.duplicate_attempt(row, submitted=int(datetime.fromisoformat(expected['submitted_at']).timestamp()))
        expected=self.view(row)
        self.assert_rejected(row,expected,self.proof(expected,timed=True))

    def test_present_conflicting_timestamp_cannot_use_unique_link_fallback(self):
        row=self.attempted(); expected=self.view(row)
        proof=self.proof(expected); proof.update(published_at=stamp(time.time()-900),timestamp_source='visible_dom')
        self.assert_rejected(row,expected,proof)

    def test_observation_must_be_fresh_and_cannot_precede_submission_or_be_future(self):
        row=self.attempted(); expected=self.view(row)
        for checked in (time.time()-180,time.time()-900,time.time()+30):
            proof=self.proof(expected); proof['checked_at']=stamp(checked)
            self.assert_rejected(row,expected,proof)

    def test_full_capture_stale_post_event_schedule_target_and_matching_attempt_fail_without_writes(self):
        row=self.attempted()
        expected=self.view(row)
        with self.store.activity.database() as conn:
            conn.execute("INSERT INTO events(post_id,at,state,message) VALUES (?,?,?,?)", (row['post_id'],now(),'unknown','New evidence'))
        self.assert_rejected(row,expected,self.proof(expected))
        expected=self.view(row)
        with self.store.activity.database() as conn:
            conn.execute("UPDATE posts SET message='Changed' WHERE id=?", (row['post_id'],))
        self.assert_rejected(row,expected,self.proof(expected))
        expected=self.view(row)
        self.store.update(row['id'],due_at=row['due_at']+60)
        self.assert_rejected(row,expected,self.proof(expected))
        expected=self.view(row)
        self.accounts.save_page(self.account,'Changed Page name','facebook.com/profile.php?id=100','100',record_id=self.page)
        self.assert_rejected(row,expected,self.proof(expected))
        expected=self.view(row)
        self.duplicate_attempt(row)
        self.assert_rejected(row,expected,self.proof(expected))

    def test_source_content_changed_even_with_original_size_and_mtime_fails(self):
        row=self.attempted(); expected=self.view(row)
        path=Path(row['source']['path']); stat=path.stat()
        path.write_bytes(b'X'*stat.st_size); os.utime(path,ns=(stat.st_atime_ns,stat.st_mtime_ns))
        self.assert_rejected(row,expected,self.proof(expected))

    def test_receipt_url_cannot_be_reused_from_other_post_even_archived_or_user_reported(self):
        row=self.attempted(); expected=self.view(row)
        other=self.store.activity.save_draft(self.other,self.other_page,'Different',delivery_mode='browser')
        for field in ('external_url','manual_url','browser_receipt'):
            with self.subTest(field=field):
                value=json.dumps(dict(url='https://www.facebook.com/reel/123456?x=1')) if field=='browser_receipt' else 'https://www.facebook.com/reel/123456?x=1'
                with self.store.activity.database() as conn:
                    conn.execute(f'UPDATE posts SET {field}=?,archived=1 WHERE id=?',(value,other['id']))
                self.assert_rejected(row,expected,self.proof(expected))
                with self.store.activity.database() as conn:
                    conn.execute(f'UPDATE posts SET {field}=? WHERE id=?',('{}' if field=='browser_receipt' else '',other['id']))

    def test_original_schedule_and_post_locks_both_required(self):
        row=self.attempted(); expected=self.view(row)
        for name, ident in (('schedule-locks',row['id']),('post-locks',row['post_id'])):
            directory=self.store.path.parent/name/ident; directory.mkdir(parents=True,exist_ok=True)
            with _job_lock(directory): self.assert_rejected(row,expected,self.proof(expected))

    def test_cross_database_wal_mode_fails_without_changing_anything(self):
        row=self.attempted(); expected=self.view(row)
        for path in (self.store.path,self.store.activity.path):
            conn=sqlite3.connect(path)
            try: conn.execute('PRAGMA journal_mode=wal').fetchone()
            finally: conn.close()
            self.assert_rejected(row,expected,self.proof(expected))
            conn=sqlite3.connect(path)
            try: conn.execute('PRAGMA journal_mode=delete').fetchone()
            finally: conn.close()

    def test_failure_in_each_write_rolls_back_post_schedule_events_and_journal(self):
        row=self.attempted(); expected=self.view(row)
        with self.store.database() as conn:
            conn.execute('CREATE TABLE browser_reconciliations (schedule_id TEXT PRIMARY KEY,post_id TEXT NOT NULL,at TEXT NOT NULL,preview_key TEXT NOT NULL,previous TEXT NOT NULL,receipt TEXT NOT NULL)')
        for path,table,operation in ((self.store.path,'browser_reconciliations','INSERT'),
                    (self.store.activity.path,'posts','UPDATE'),(self.store.activity.path,'events','INSERT'),
                    (self.store.path,'schedules','UPDATE')):
            with self.subTest(table=table):
                conn=sqlite3.connect(path)
                try: conn.execute(f"CREATE TRIGGER deny_write BEFORE {operation} ON {table} BEGIN SELECT RAISE(ABORT,'Disposable rollback'); END"); conn.commit()
                finally: conn.close()
                self.assert_rejected(row,expected,self.proof(expected))
                conn=sqlite3.connect(path)
                try: conn.execute('DROP TRIGGER deny_write'); conn.commit()
                finally: conn.close()

    def test_cancellation_after_hash_and_just_before_commit_rolls_back(self):
        row=self.attempted(); expected=self.view(row); proof=self.proof(expected)
        for stop_at in (1,2):
            calls=[]
            def cancel():
                calls.append(True)
                if len(calls)==stop_at: raise ValueError('Disposable cancelled')
            before=self.capture(row)
            with self.assertRaisesRegex(ValueError,'cancelled'):
                commit_verified(self.store,expected,proof,check_cancel=cancel)
            self.assertEqual(self.capture(row),before)

    def test_registry_change_during_hash_and_before_commit_is_detected(self):
        row=self.attempted(); expected=self.view(row); proof=self.proof(expected)
        target=deepcopy(expected['target']); changed=dict(target,name='Later name')
        with patch('teodub.page_schedule_recovery._target',side_effect=[target,changed]):
            self.assert_rejected(row,expected,proof)

    def test_exact_receipt_idempotence_does_not_mask_later_history_or_allow_replacement(self):
        row=self.attempted(); expected=self.view(row); proof=self.proof(expected)
        commit_verified(self.store,expected,proof)
        replacement=dict(proof,url='https://www.facebook.com/reel/999',explicit_result_url='https://www.facebook.com/reel/999')
        self.assert_rejected(row,expected,replacement)
        with self.store.activity.database() as conn:
            conn.execute('INSERT INTO events(post_id,at,state,message) VALUES (?,?,?,?)',(row['post_id'],now(),'published','Later audit'))
        self.assert_rejected(row,expected,proof)

    def test_tampered_preview_and_malformed_structured_proof_are_rejected_without_writes(self):
        row=self.attempted(); expected=self.view(row)
        tampered=deepcopy(expected); tampered['post']['message']='Replaced capture'
        self.assert_rejected(row,tampered,self.proof(expected))
        missing=deepcopy(expected); del missing['preview_key']
        self.assert_rejected(row,missing,self.proof(expected))
        for field in ('owner_url','url','checked_at'):
            proof=self.proof(expected); proof[field]=None
            self.assert_rejected(row,expected,proof)

    def test_exact_receipt_repeat_rechecks_publication_timestamp_and_file_hash(self):
        row=self.attempted(); expected=self.view(row); proof=self.proof(expected)
        commit_verified(self.store,expected,proof)
        with self.store.activity.database() as conn:
            conn.execute('UPDATE posts SET published_at=? WHERE id=?',(stamp(time.time()-900),row['post_id']))
        self.assert_rejected(row,expected,proof)

    def test_existing_conflicting_receipt_is_preserved(self):
        row=self.attempted()
        with self.store.activity.database() as conn:
            conn.execute('UPDATE posts SET browser_receipt=? WHERE id=?',(json.dumps(dict(source='visible_dom',url='https://www.facebook.com/reel/999')),row['post_id']))
        expected=self.view(row)
        self.assert_rejected(row,expected,self.proof(expected))

    def test_reconfirming_already_published_same_permalink_preserves_original_receipt_and_history(self):
        row=self.attempted()
        scope=self.account,self.page,row['post_id']
        original=dict(source='visible_dom',url='https://www.facebook.com/reel/123456',checked_at=now(),observed='Original durable proof')
        self.store.activity.record_browser_receipt(*scope,original)
        self.store.activity.transition(*scope,'published','Previously verified',expected={'unknown'},external_url=original['url'])
        self.store.update(row['id'],state='published',finished_at=now())
        expected=self.view(row); before=self.capture(row)
        result=commit_verified(self.store,expected,self.proof(expected))
        self.assertEqual(result['browser_receipt'],original)
        self.assertEqual(self.capture(row),before)
        proof=self.proof(expected); proof.update(url='https://www.facebook.com/reel/999',explicit_result_url='https://www.facebook.com/reel/999')
        self.assert_rejected(row,expected,proof)


if __name__ == '__main__':
    unittest.main()
