"""Explicit recovery in disposable stores; no real accounts or publishing workers."""
from copy import deepcopy
import os
from pathlib import Path
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tests import test_page_schedule as fixtures
from teodub.common import atomic_json
from teodub.page_schedule import PageScheduleController, PageScheduleStore


class ScheduleRecoveryTests(unittest.TestCase):
    setUp = fixtures.ScheduleTests.setUp
    video = fixtures.ScheduleTests.video
    schedule = fixtures.ScheduleTests.schedule
    submit = fixtures.ScheduleTests.submit
    tick = fixtures.ScheduleTests.tick
    reply = fixtures.ScheduleTests.reply
    ready_for_validation = fixtures.ScheduleTests.ready_for_validation

    def attempted(self, *, campaign_id='a'*32, post_state='failed', schedule_state='paused'):
        row = self.schedule(campaign_id)
        activity = self.store.activity
        scope = (self.account, self.page, row['post_id'])
        activity.transition(*scope, 'preparing', 'Synthetic preparation', expected={'draft'})
        activity.transition(*scope, post_state, 'Synthetic failed preparation or abandoned upload',
                            expected={'preparing'})
        return self.store.update(row['id'], state=schedule_state, phase='prepare',
            request_id='previous-request-'+campaign_id, attempted_at='2026-10-14T09:41:00+07:00',
            message='Synthetic schedule paused after preparation')

    def preview(self, row):
        return self.controller.recovery_preview(self.account, self.page, row['id'])

    def recover(self, row, *, preview=None, due_at=None, **flags):
        return self.controller.recover_schedule(self.account, self.page, row['id'],
            due_at or time.time()+3600, expected_preview=preview or self.preview(row),
            checked_unpublished=flags.get('checked_unpublished', True),
            closed_composer=flags.get('closed_composer', True))

    def capture(self, row):
        return dict(schedule=self.store.get(row['id']),
            post=self.store.activity.post(self.account, self.page, row['post_id']),
            events=self.store.activity.events(self.account, self.page, row['post_id']),
            posts=self.store.activity.posts(self.account, self.page, include_archived=True),
            history=self.store.recovery_history(row['id']))

    def assert_unchanged(self, row, before):
        self.assertEqual(self.capture(row), before)
        self.assertEqual(self.calls, [])
        self.assertNotIn(row['id'], self.controller.active)

    def assert_archived_unchanged(self, row, original_post, original_events):
        post = self.store.activity.post(self.account, self.page, row['post_id'])
        self.assertEqual(post['archived'], 1)
        for field, value in original_post.items():
            if field not in {'archived', 'updated_at'}:
                self.assertEqual(post[field], value, field)
        self.assertEqual(self.store.activity.events(self.account, self.page, row['post_id']), original_events)

    def test_failed_preparation_recovery_keeps_schedule_source_caption_and_old_evidence(self):
        old = self.attempted()
        before = self.capture(old)
        preview = self.preview(old)
        self.assertEqual(preview['schedule'], old)
        self.assertEqual(preview['post'], before['post'])
        self.assertEqual(preview['events'], before['events'])
        self.assertEqual(preview['target'], dict(name='Fixture Page',
            url='https://www.facebook.com/profile.php?id=100', page_id='100'))
        self.assertTrue(self.store.can_recover_schedule(old))
        due_at = time.time()+7200
        result = self.recover(old, preview=preview, due_at=due_at)
        self.assertEqual(result['state'], 'scheduled')
        self.assertEqual(result['due_at'], due_at)
        for key in ('id', 'account_id', 'page_key', 'source_key', 'source', 'body', 'created_at'):
            self.assertEqual(result[key], old[key], key)
        for key in ('attempted_at', 'request_id', 'phase', 'finished_at'):
            self.assertEqual(result[key], '', key)
        self.assertNotEqual(result['post_id'], old['post_id'])
        new_post = self.store.activity.post(self.account, self.page, result['post_id'])
        self.assertEqual(new_post['state'], 'draft')
        self.assertEqual(new_post['attempt'], 0)
        for key in ('body', 'kind', 'media', 'delivery_mode', 'browser_finish_mode'):
            self.assertEqual(new_post[key], before['post'][key], key)
        self.assert_archived_unchanged(old, before['post'], before['events'])
        self.assertEqual(len(self.store.list()), 1)
        self.assertEqual(len(self.store.activity.posts(self.account, self.page, include_archived=True)), 2)
        self.assertEqual(len(self.store.recovery_history(old['id'])), 1)
        event = self.store.recovery_history(old['id'])[0]
        self.assertEqual(event['previous'], preview)
        self.assertEqual(event['new_post_id'], result['post_id'])
        self.assertEqual(event['target'], preview['target'])
        self.assertEqual(event['new_due_at'], due_at)
        self.assertTrue(event['checked_unpublished'])
        self.assertTrue(event['closed_composer'])
        self.assertEqual(self.calls, [])
        self.assertNotIn(old['id'], self.controller.active)
        self.assertEqual(self.controller.guards, {})

    def test_same_numeric_page_id_allows_approved_name_and_url_alias_correction(self):
        old = self.attempted()
        before = self.capture(old)
        self.accounts.save_page(self.account, 'Minh châu sneaker', 'facebook.com/correctedpage',
                                '100', record_id=self.page)
        preview = self.preview(old)
        self.assertEqual(preview['schedule']['snapshot']['target_name'], 'Fixture Page')
        self.assertEqual(preview['target'], dict(name='Minh châu sneaker',
            url='https://www.facebook.com/correctedpage', page_id='100'))
        result = self.recover(old, preview=preview)
        self.assertEqual(result['snapshot']['target_name'], 'Minh châu sneaker')
        self.assertEqual(result['snapshot']['target_url'], 'https://www.facebook.com/correctedpage')
        self.assertEqual(result['snapshot']['target_id'], '100')
        post = self.store.activity.post(self.account, self.page, result['post_id'])
        self.assertEqual(post['target_name'], 'Minh châu sneaker')
        self.assertEqual(post['target_url'], 'https://www.facebook.com/correctedpage')
        self.assertEqual(post['target_id'], '100')
        self.assertEqual(result['body'], old['body'])
        self.assert_archived_unchanged(old, before['post'], before['events'])
        self.assertEqual(self.calls, [])

    def test_unknown_upload_recovery_requires_both_explicit_confirmations(self):
        old = self.attempted(post_state='unknown')
        before = self.capture(old)
        preview = self.preview(old)
        for checked_unpublished, closed_composer in ((False, False), (False, True), (True, False),
                                                     ('yes', True), (True, 1)):
            with self.subTest(checked_unpublished=checked_unpublished, closed_composer=closed_composer):
                with self.assertRaises(ValueError):
                    self.recover(old, preview=preview, checked_unpublished=checked_unpublished,
                                 closed_composer=closed_composer)
                self.assert_unchanged(old, before)
        result = self.recover(old, preview=preview)
        self.assertEqual(result['state'], 'scheduled')
        self.assertEqual(self.store.activity.post(self.account, self.page, old['post_id'])['state'], 'unknown')
        self.assertTrue(self.store.activity.events(self.account, self.page, old['post_id'])[-1]['state'] == 'unknown')
        self.assertEqual(self.calls, [])

    def test_failed_attempt_also_requires_confirmations_and_never_implicitly_starts(self):
        old = self.attempted()
        before = self.capture(old)
        for key in ('checked_unpublished', 'closed_composer'):
            with self.subTest(flag=key):
                with self.assertRaises(ValueError):
                    self.recover(old, **{key: False})
                self.assert_unchanged(old, before)

    def test_no_auto_replay_from_start_bulk_or_post_now_before_explicit_recovery(self):
        old = self.attempted(post_state='unknown')
        before = self.capture(old)
        with self.assertRaises(ValueError):
            self.controller.start(old['id'])
        self.assertEqual(self.controller.start_many(self.account, self.page, [old['id']])[0]['outcome'], 'skipped')
        with self.assertRaises(ValueError):
            self.controller.start_now(self.account, self.page, old['id'], expected_schedule=old)
        self.assert_unchanged(old, before)

    def test_three_distinct_recoveries_only_save_then_use_normal_validation_and_publish(self):
        originals = [self.attempted(campaign_id=str(i)*32, post_state=state)
                     for i, state in ((1, 'failed'), (2, 'unknown'), (3, 'failed'))]
        recovered = [self.recover(row, due_at=time.time()+3600+i*3600)
                     for i, row in enumerate(originals)]
        self.assertEqual([row['id'] for row in recovered], [row['id'] for row in originals])
        self.assertEqual([row['state'] for row in recovered], ['scheduled']*3)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.controller.active, set())
        running = self.ready_for_validation(recovered[0])
        self.assertEqual(self.calls, [(self.account, self.page, recovered[0]['post_id'], 'prepare')])
        self.reply(running, 'done', 'review')
        self.tick()
        self.assertEqual([call[-1] for call in self.calls], ['prepare', 'publish'])
        self.reply(self.store.get(running['id']), 'done', 'published')
        self.tick(); self.tick()
        self.assertEqual(self.store.get(running['id'])['state'], 'published')
        self.assertEqual(self.store.get(recovered[1]['id'])['state'], 'scheduled')
        self.assertEqual(self.store.get(recovered[2]['id'])['state'], 'scheduled')
        self.assertEqual(len(self.store.recovery_history(running['id'])), 1)

    def test_recovery_keeps_duplicate_slot_and_is_durable_after_reload(self):
        old = self.attempted()
        result = self.recover(old)
        before = self.capture(old)
        with self.assertRaises(ValueError):
            self.store.create(self.account, self.page, result['source'], 'Another caption', '', time.time()+7200)
        self.assert_unchanged(old, before)
        reloaded = PageScheduleStore(self.directory)
        self.assertEqual(reloaded.get(result['id']), result)
        self.assertEqual(reloaded.recovery_history(result['id']), self.store.recovery_history(result['id']))
        second = PageScheduleController(self.owner)
        self.addCleanup(second.close)
        self.assertEqual(second.active, set())
        self.assertEqual(second.guards, {})
        self.assertEqual(second.store.get(result['id']), result)
        self.assertEqual(self.calls, [])

    def test_double_approval_and_tampered_expected_preview_cannot_create_extra_post(self):
        old = self.attempted()
        preview = self.preview(old)
        tampered = deepcopy(preview)
        tampered['schedule']['body'] = 'Caller changed caption'
        before = self.capture(old)
        with self.assertRaises(ValueError):
            self.recover(old, preview=tampered)
        self.assert_unchanged(old, before)
        self.recover(old, preview=preview)
        after = self.capture(old)
        with self.assertRaises(ValueError):
            self.recover(old, preview=preview)
        self.assert_unchanged(old, after)

    def test_changed_target_id_cross_account_and_page_never_redirect_recovery(self):
        old = self.attempted()
        preview = self.preview(old)
        before = self.capture(old)
        for account, page in ((self.other, self.other_page), (self.other, self.page),
                              (self.account, self.other_page)):
            with self.subTest(account=account, page=page):
                with self.assertRaises(ValueError):
                    self.controller.recover_schedule(account, page, old['id'], time.time()+3600,
                        expected_preview=preview, checked_unpublished=True, closed_composer=True)
                self.assert_unchanged(old, before)
        self.accounts.save_page(self.account, 'Other destination', 'facebook.com/profile.php?id=999',
                                '999', record_id=self.page)
        self.assertFalse(self.store.can_recover_schedule(old))
        with self.assertRaises(ValueError):
            self.recover(old, preview=preview)
        self.assert_unchanged(old, before)

    def test_target_name_changed_after_preview_requires_fresh_approval(self):
        old = self.attempted()
        preview = self.preview(old)
        before = self.capture(old)
        self.accounts.save_page(self.account, 'Another corrected name', 'facebook.com/profile.php?id=100',
                                '100', record_id=self.page)
        with self.assertRaises(ValueError):
            self.recover(old, preview=preview)
        self.assert_unchanged(old, before)
        result = self.recover(old)
        self.assertEqual(result['snapshot']['target_name'], 'Another corrected name')

    def test_schedule_post_or_event_changed_after_preview_rejects_without_recovery(self):
        for index, changed in enumerate(('schedule', 'post', 'events')):
            with self.subTest(changed=changed):
                old = self.attempted(campaign_id=str(index+1)*32)
                preview = self.preview(old)
                if changed == 'schedule':
                    self.store.update(old['id'], message='Changed after preview')
                elif changed == 'post':
                    with self.store.activity.database() as conn:
                        conn.execute('UPDATE posts SET message=? WHERE id=?',
                                     ('Changed after preview', old['post_id']))
                else:
                    with self.store.activity.database() as conn:
                        self.store.activity._event(conn, old['post_id'], 'failed', 'New event after preview')
                before = self.capture(old)
                with self.assertRaises(ValueError):
                    self.recover(old, preview=preview)
                self.assert_unchanged(old, before)

    def test_incompatible_schedule_and_post_states_never_expose_recovery(self):
        cases = [('scheduled', 'failed', 'prepare', True), ('armed', 'failed', 'prepare', True),
                 ('preparing', 'failed', 'prepare', True), ('submitting', 'failed', 'prepare', True),
                 ('verifying', 'failed', 'prepare', True),
                 ('published', 'failed', 'prepare', True), ('cancelled', 'failed', 'prepare', True),
                 ('paused', 'failed', 'publish', True), ('paused', 'failed', 'validate', True),
                 ('paused', 'failed', 'prepare', False), ('paused', 'draft', 'prepare', True),
                 ('paused', 'preparing', 'prepare', True), ('paused', 'review', 'prepare', True),
                 ('paused', 'submitting', 'prepare', True), ('paused', 'processing', 'prepare', True)]
        for index, (schedule_state, post_state, phase, attempted) in enumerate(cases):
            with self.subTest(schedule=schedule_state, post=post_state, phase=phase, attempted=attempted):
                old = self.attempted(campaign_id=f'{index+1:032x}')
                with self.store.activity.database() as conn:
                    conn.execute('UPDATE posts SET state=? WHERE id=?', (post_state, old['post_id']))
                old = self.store.update(old['id'], state=schedule_state, phase=phase,
                    attempted_at=old['attempted_at'] if attempted else '')
                before = self.capture(old)
                self.assertFalse(self.store.can_recover_schedule(old))
                with self.assertRaises(ValueError):
                    self.preview(old)
                self.assert_unchanged(old, before)

    def test_prior_review_submit_processing_or_publish_event_cannot_be_hidden_by_failed_state(self):
        for index, state in enumerate(('review', 'submitting', 'processing', 'published')):
            with self.subTest(prior_state=state):
                old = self.attempted(campaign_id=str(index+1)*32)
                with self.store.activity.database() as conn:
                    self.store.activity._event(conn, old['post_id'], state, 'Previously reached protected stage')
                before = self.capture(old)
                self.assertFalse(self.store.can_recover_schedule(old))
                with self.assertRaises(ValueError):
                    self.preview(old)
                self.assert_unchanged(old, before)

    def test_finished_schedule_never_gains_recovery_permission_from_paused_state(self):
        old = self.attempted()
        old = self.store.update(old['id'], finished_at='2026-10-14T10:00:00+07:00')
        before = self.capture(old)
        self.assertFalse(self.store.can_recover_schedule(old))
        with self.assertRaises(ValueError):
            self.preview(old)
        self.assert_unchanged(old, before)

    def test_any_persisted_publication_or_prepared_evidence_blocks_recovery(self):
        cases = {'browser_review': '{"body":"Previously prepared"}',
                 'browser_receipt': '{"state":"unknown","url":"https://www.facebook.com/100/posts/123"}',
                 'browser_tracking': '{"submitted_at":1792033200}',
                 'remote_assets': '["uploaded-api-asset"]',
                 'external_id': '123', 'external_url': 'https://www.facebook.com/100/posts/123',
                 'manual_url': 'https://www.facebook.com/100/posts/123', 'published_at': '2026-10-14T10:00:00+07:00',
                 'archived': 1}
        for index, (field, value) in enumerate(cases.items()):
            with self.subTest(field=field):
                old = self.attempted(campaign_id=f'{index+1:032x}')
                with self.store.activity.database() as conn:
                    conn.execute(f'UPDATE posts SET {field}=? WHERE id=?', (value, old['post_id']))
                before = self.capture(old)
                self.assertFalse(self.store.can_recover_schedule(old))
                with self.assertRaises(ValueError):
                    self.preview(old)
                self.assert_unchanged(old, before)

    def test_changed_body_or_media_does_not_inherit_old_caption_approval(self):
        for index, field in enumerate(('body', 'media', 'delivery_mode', 'browser_finish_mode')):
            with self.subTest(field=field):
                old = self.attempted(campaign_id=str(index+1)*32)
                value = {'body': 'Changed caption', 'media': '[]', 'delivery_mode': 'api',
                         'browser_finish_mode': 'manual'}[field]
                with self.store.activity.database() as conn:
                    conn.execute(f'UPDATE posts SET {field}=? WHERE id=?', (value, old['post_id']))
                before = self.capture(old)
                self.assertFalse(self.store.can_recover_schedule(old))
                with self.assertRaises(ValueError):
                    self.preview(old)
                self.assert_unchanged(old, before)

    def test_text_posts_and_non_numeric_saved_target_do_not_gain_video_recovery_permission(self):
        post = self.store.activity.save_draft(self.account, self.page, 'Synthetic text post',
            delivery_mode='browser', browser_finish_mode='tool')
        row = self.store.create_post(self.account, self.page, post['id'], time.time()+3600)
        scope = (self.account, self.page, post['id'])
        self.store.activity.transition(*scope, 'preparing', 'Synthetic preparation', expected={'draft'})
        self.store.activity.transition(*scope, 'failed', 'Synthetic failed preparation', expected={'preparing'})
        row = self.store.update(row['id'], state='paused', phase='prepare', attempted_at='Prior text attempt')
        before = self.capture(row)
        self.assertFalse(self.store.can_recover_schedule(row))
        with self.assertRaises(ValueError):
            self.preview(row)
        self.assert_unchanged(row, before)

        old = self.attempted()
        before = self.capture(old)
        with self.store.activity.database() as conn:
            conn.execute('UPDATE posts SET target_id=? WHERE id=?', ('not-numeric', old['post_id']))
        self.assertFalse(self.store.can_recover_schedule(old))
        with self.assertRaises(ValueError):
            self.preview(old)
        after_mutation = self.capture(old)
        self.assertEqual(after_mutation['schedule'], before['schedule'])
        self.assertEqual(after_mutation['history'], [])
        self.assertEqual(self.calls, [])

    def test_video_hash_changed_with_same_size_and_mtime_blocks_atomic_save(self):
        old = self.attempted()
        preview = self.preview(old)
        video = Path(old['source']['path'])
        data = video.read_bytes()
        video.write_bytes(b'X'+data[1:])
        os.utime(video, ns=(old['source']['mtime_ns'], old['source']['mtime_ns']))
        before = self.capture(old)
        with self.assertRaises(ValueError):
            self.recover(old, preview=preview)
        self.assert_unchanged(old, before)

    def test_missing_video_changed_source_or_invalid_due_time_never_creates_new_post(self):
        old = self.attempted()
        preview = self.preview(old)
        before = self.capture(old)
        for due in (time.time()-60, time.time()+1, time.time()+367*86400, float('nan'), float('inf')):
            with self.subTest(due=due):
                with self.assertRaises(ValueError):
                    self.recover(old, preview=preview, due_at=due)
                self.assert_unchanged(old, before)
        Path(old['source']['path']).unlink()
        with self.assertRaises(ValueError):
            self.recover(old, preview=preview)
        self.assert_unchanged(old, before)

    def test_source_no_longer_completed_after_preview_blocks_save_without_new_draft(self):
        from teodub.campaign import read_json
        old = self.attempted()
        preview = self.preview(old)
        path = Path(old['source']['campaign'])/'campaign.json'
        manifest = read_json(path)
        manifest['items'][0]['status'] = 'review_required'
        atomic_json(path, manifest)
        before = self.capture(old)
        with self.assertRaises(ValueError):
            self.recover(old, preview=preview)
        self.assert_unchanged(old, before)

    def test_live_account_jobs_loading_and_controller_schedule_lock_block_recovery(self):
        old = self.attempted()
        preview = self.preview(old)
        before = self.capture(old)
        for loading, request_state, pending, opened in ((True, 'done', True, True),
                (False, 'queued', True, True), (False, 'running', True, True),
                (False, 'done', True, True), (False, 'done', True, False)):
            with self.subTest(loading=loading, state=request_state, opened=opened):
                self.owner.sessions[self.account] = SimpleNamespace(loading_pages=loading, pending=pending,
                    opened=opened,
                    post_actions={'other': dict(state=request_state)})
                with self.assertRaises(ValueError):
                    self.recover(old, preview=preview)
                self.assert_unchanged(old, before)
        self.owner.sessions.clear()
        with self.controller.guard(old['id']):
            with self.assertRaises((OSError, ValueError)):
                self.recover(old, preview=preview)
        self.assert_unchanged(old, before)

    def test_closed_restarting_or_active_controller_never_resets_attempt(self):
        old = self.attempted()
        preview = self.preview(old)
        before = self.capture(old)
        self.owner.studio.restart_draining = True
        with self.assertRaises(ValueError):
            self.recover(old, preview=preview)
        self.owner.studio.restart_draining = False
        self.controller.active.add(old['id'])
        with self.assertRaises(ValueError):
            self.recover(old, preview=preview)
        self.controller.active.clear()
        self.controller.closed = True
        with self.assertRaises(ValueError):
            self.recover(old, preview=preview)
        self.controller.closed = False
        self.assert_unchanged(old, before)

    def test_post_insert_failure_rolls_back_schedule_audit_and_new_draft_together(self):
        old = self.attempted()
        preview = self.preview(old)
        before = self.capture(old)
        with patch('teodub.page_schedule.uuid.uuid4', return_value=SimpleNamespace(hex=old['post_id'])):
            with self.assertRaises(ValueError):
                self.recover(old, preview=preview)
        self.assert_unchanged(old, before)

    def test_late_audit_write_failure_rolls_back_new_post_archival_and_schedule_binding(self):
        old = self.attempted()
        preview = self.preview(old)
        with self.store.database() as conn:
            conn.execute('''CREATE TABLE schedule_recoveries (
                id INTEGER PRIMARY KEY AUTOINCREMENT, schedule_id TEXT NOT NULL, at TEXT NOT NULL,
                previous TEXT NOT NULL, new_post_id TEXT NOT NULL, target TEXT NOT NULL,
                new_due_at REAL NOT NULL, checked_unpublished INTEGER NOT NULL, closed_composer INTEGER NOT NULL)''')
            conn.execute('''CREATE TRIGGER reject_recovery_audit BEFORE INSERT ON schedule_recoveries
                BEGIN SELECT RAISE(ABORT, 'Synthetic last-stage disk/write failure'); END''')
        before = self.capture(old)
        with self.assertRaises(ValueError):
            self.recover(old, preview=preview)
        self.assert_unchanged(old, before)

    def test_wal_database_refuses_cross_file_recovery_without_partial_writes(self):
        old = self.attempted()
        preview = self.preview(old)
        with self.store.database() as conn:
            self.assertEqual(conn.execute('PRAGMA journal_mode=WAL').fetchone()[0], 'wal')
        before = self.capture(old)
        with self.assertRaises(ValueError):
            self.recover(old, preview=preview)
        self.assert_unchanged(old, before)

    def test_registry_changed_during_file_hash_rolls_back_recovery_before_commit(self):
        from teodub.common import hash_file
        old = self.attempted()
        preview = self.preview(old)
        before = self.capture(old)

        def hashing_and_page_edit(path):
            digest = hash_file(path)
            self.accounts.save_page(self.account, 'Concurrent registry edit',
                'facebook.com/profile.php?id=100', '100', record_id=self.page)
            return digest

        with patch('teodub.page_schedule_recovery.hash_file', side_effect=hashing_and_page_edit):
            with self.assertRaises(ValueError):
                self.recover(old, preview=preview)
        self.assert_unchanged(old, before)

    def test_recovery_of_second_failed_attempt_preserves_both_prior_posts_and_audits(self):
        original = self.attempted()
        first_preview = self.preview(original)
        first = self.recover(original, preview=first_preview)
        scope = (self.account, self.page, first['post_id'])
        self.store.activity.transition(*scope, 'preparing', 'Second synthetic preparation', expected={'draft'})
        self.store.activity.transition(*scope, 'unknown', 'Second abandoned upload', expected={'preparing'})
        first = self.store.update(first['id'], state='needs_attention', phase='prepare',
            attempted_at='2026-10-14T11:00:00+07:00', request_id='second-request')
        second = self.recover(first)
        self.assertEqual(second['id'], original['id'])
        self.assertEqual(second['source_key'], original['source_key'])
        self.assertEqual(len({original['post_id'], first['post_id'], second['post_id']}), 3)
        self.assertEqual(len(self.store.list()), 1)
        self.assertEqual(len(self.store.recovery_history(original['id'])), 2)
        self.assert_archived_unchanged(original, first_preview['post'], first_preview['events'])
        self.assertEqual(self.store.activity.post(self.account, self.page, first['post_id'])['state'], 'unknown')
        self.assertEqual(self.store.activity.post(self.account, self.page, second['post_id'])['state'], 'draft')
        self.assertEqual(self.calls, [])


if __name__ == '__main__':
    unittest.main()
