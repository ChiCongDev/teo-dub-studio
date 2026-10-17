"""Explicit new attempts after local removal, using disposable video/account stores."""
from copy import deepcopy
import os
from pathlib import Path
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tests import test_page_schedule as fixtures
from tests import test_page_schedule_removal as removal_fixtures
from teodub.common import atomic_json
from teodub.page_schedule import PageScheduleController, PageScheduleStore


class NewAttemptTests(unittest.TestCase):
    setUp = fixtures.ScheduleTests.setUp
    video = fixtures.ScheduleTests.video
    schedule = fixtures.ScheduleTests.schedule
    submit = fixtures.ScheduleTests.submit
    tick = fixtures.ScheduleTests.tick
    reply = fixtures.ScheduleTests.reply
    ready_for_validation = fixtures.ScheduleTests.ready_for_validation
    stopped_publish = removal_fixtures.ScheduleRemovalTests.stopped_publish

    def remove_row(self, row):
        preview = self.store.removal_preview([row['id']], account_id=self.account, page_key=self.page)
        result = self.controller.remove_many(preview, account_id=self.account, page_key=self.page)
        self.assertEqual(result, dict(removed=[row['id']], skipped={}))
        return self.store.get(row['id'])

    def removed(self, campaign_id='a'*32, *, post_state='unknown'):
        if post_state == 'unknown':
            return self.remove_row(self.stopped_publish(campaign_id))
        row = self.schedule(campaign_id)
        scope = self.account, self.page, row['post_id']
        activity = self.store.activity
        if post_state != 'draft':
            activity.transition(*scope, 'preparing', 'Synthetic earlier attempt', expected={'draft'})
            if post_state == 'review':
                post = activity.post(*scope)
                review = dict(account_id=self.account, page_key=self.page, post_id=row['post_id'],
                    **{key: post[key] for key in ('target_name', 'target_url', 'target_id', 'body', 'browser_finish_mode')},
                    media=[dict(saved, sha256=row['source']['sha256']) for saved in post['media']])
                activity.save_browser_review(*scope, review)
            else:
                activity.transition(*scope, post_state, 'Synthetic stopped attempt', expected={'preparing'})
        row = self.store.update(row['id'], state='paused', phase='prepare',
            attempted_at='Prior abandoned attempt', request_id='prior-inactive-request')
        return self.remove_row(row)

    def preview(self, row, *, account=None, page=None):
        return self.store.new_attempt_preview(account or self.account, page or self.page, row['id'])

    def create(self, row, *, preview=None, due_at=None, account=None, page=None, store=False,
               checked_unpublished=True, closed_composer=True):
        return (self.store if store else self.controller).create_new_attempt(
            account or self.account, page or self.page, row['id'], due_at or time.time()+7200,
            expected_preview=self.preview(row) if preview is None else preview,
            checked_unpublished=checked_unpublished, closed_composer=closed_composer)

    def capture(self, row):
        return dict(schedule=self.store.get(row['id']), schedules=self.store.list(),
            posts=self.store.activity.posts(self.account, self.page, include_archived=True),
            events=self.store.activity.events(self.account, self.page, row['post_id']),
            removals=self.store.removal_history(row['id']),
            attempts=self.store.new_attempt_history(row['id']))

    def assert_unchanged(self, row, before):
        self.assertEqual(self.capture(row), before)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.controller.active, set())
        self.assertEqual(self.controller.guards, {})

    def test_explicit_unknown_publish_new_attempt_preserves_every_old_evidence_field(self):
        row = self.removed()
        before = self.capture(row)
        preview = self.preview(row)
        self.assertEqual(preview['classification'], 'removed_unknown')
        self.assertEqual(preview['schedule'], row)
        self.assertEqual(preview['post'], before['posts'][0])
        self.assertEqual(preview['events'], before['events'])
        due = time.time()+7200
        fresh = self.create(row, preview=preview, due_at=due)
        self.assertNotEqual(fresh['id'], row['id'])
        self.assertNotEqual(fresh['post_id'], row['post_id'])
        self.assertEqual(fresh['state'], 'scheduled')
        self.assertEqual(fresh['due_at'], due)
        self.assertEqual(fresh['source_key'], row['source_key'])
        self.assertEqual(fresh['source'], row['source'])
        self.assertEqual(fresh['body'], row['body'])
        self.assertEqual((fresh['account_id'], fresh['page_key']), (self.account, self.page))
        for key in ('attempted_at', 'request_id', 'phase', 'finished_at'):
            self.assertEqual(fresh[key], '', key)
        old = self.store.get(row['id'])
        self.assertEqual(old['source_key'], 'removed:'+row['id'])
        for key in row.keys()-{'source_key'}:
            self.assertEqual(old[key], row[key], key)
        self.assertEqual(self.store.activity.post(self.account, self.page, row['post_id']), before['posts'][0])
        self.assertEqual(self.store.activity.events(self.account, self.page, row['post_id']), before['events'])
        self.assertEqual(self.store.removal_history(row['id']), before['removals'])
        post = self.store.activity.post(self.account, self.page, fresh['post_id'])
        self.assertEqual(post['state'], 'draft')
        self.assertEqual(post['attempt'], 0)
        self.assertEqual(post['archived'], 0)
        for key in ('body', 'kind', 'media', 'delivery_mode', 'browser_finish_mode'):
            self.assertEqual(post[key], before['posts'][0][key], key)
        for key in ('browser_review', 'browser_receipt', 'external_id', 'external_url', 'manual_url', 'published_at'):
            self.assertFalse(post[key], key)
        self.assertEqual(len(self.store.list()), 2)
        self.assertEqual(len(self.store.new_attempt_history(row['id'])), 1)
        self.assertEqual(self.store.new_attempt_history(row['id'])[0]['previous'], preview)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.controller.active, set())
        self.assertEqual(self.controller.guards, {})

    def test_removed_failed_draft_and_review_history_each_require_explicit_unarmed_new_row(self):
        for index, state in enumerate(('failed', 'draft', 'review'), 1):
            with self.subTest(state=state):
                row = self.removed(f'{index:032x}', post_state=state)
                original = self.store.activity.post(self.account, self.page, row['post_id'])
                fresh = self.create(row)
                self.assertEqual(fresh['state'], 'scheduled')
                self.assertNotEqual(fresh['id'], row['id'])
                self.assertEqual(self.store.activity.post(self.account, self.page, row['post_id']), original)
                self.assertEqual(self.store.activity.post(self.account, self.page, fresh['post_id'])['state'], 'draft')
        self.assertEqual(self.calls, [])

    def test_both_acknowledgments_must_be_exact_booleans_before_any_write(self):
        row = self.removed()
        preview = self.preview(row)
        before = self.capture(row)
        for checked, closed in ((False, False), (False, True), (True, False),
                ('yes', True), (True, 1), (1, True), (True, 'closed'), (None, True)):
            with self.subTest(checked=checked, closed=closed):
                with self.assertRaises(ValueError):
                    self.create(row, preview=preview, checked_unpublished=checked, closed_composer=closed)
                self.assert_unchanged(row, before)

    def test_double_click_and_fresh_second_preview_do_not_create_extra_schedule_or_free_slot(self):
        row = self.removed()
        preview = self.preview(row)
        fresh = self.create(row, preview=preview)
        after = self.capture(row)
        with self.assertRaises(ValueError):
            self.create(row, preview=preview)
        self.assert_unchanged(row, after)
        with self.assertRaises(ValueError):
            self.preview(row)
        self.assertEqual(self.store.get(fresh['id'])['source_key'], row['source_key'])
        with self.assertRaises(ValueError):
            self.store.create(self.account, self.page, row['source'], 'Another duplicate', '', time.time()+7200)
        self.assertEqual(len(after['attempts']), 1)

    def test_new_row_can_only_publish_through_normal_explicit_start_and_verified_pipeline(self):
        row = self.removed()
        fresh = self.create(row)
        self.tick()
        self.assertEqual(self.calls, [])
        running = self.ready_for_validation(fresh)
        self.assertEqual(self.calls, [(self.account, self.page, fresh['post_id'], 'prepare')])
        self.reply(running, 'done', 'review')
        self.tick()
        self.assertEqual([call[-1] for call in self.calls], ['prepare', 'publish'])
        self.reply(self.store.get(fresh['id']), 'done', 'published')
        self.tick(); self.tick()
        self.assertEqual(self.store.get(fresh['id'])['state'], 'published')
        self.assertEqual(self.store.get(row['id'])['state'], 'removed')
        self.assertEqual(self.store.activity.post(self.account, self.page, row['post_id'])['state'], 'unknown')

    def test_same_numeric_page_id_allows_current_exact_name_and_url_in_new_draft(self):
        row = self.removed()
        old_post = self.store.activity.post(self.account, self.page, row['post_id'])
        self.accounts.save_page(self.account, 'Minh châu sneaker', 'facebook.com/renamedpage',
                                '100', record_id=self.page)
        preview = self.preview(row)
        self.assertEqual(preview['target'], dict(name='Minh châu sneaker',
            url='https://www.facebook.com/renamedpage', page_id='100'))
        fresh = self.create(row, preview=preview)
        self.assertEqual(fresh['snapshot']['target_name'], 'Minh châu sneaker')
        self.assertEqual(fresh['snapshot']['target_url'], 'https://www.facebook.com/renamedpage')
        self.assertEqual(fresh['snapshot']['target_id'], '100')
        post = self.store.activity.post(self.account, self.page, fresh['post_id'])
        self.assertEqual(post['target_name'], 'Minh châu sneaker')
        self.assertEqual(post['target_url'], 'https://www.facebook.com/renamedpage')
        self.assertEqual(post['target_id'], '100')
        self.assertEqual(self.store.activity.post(self.account, self.page, row['post_id']), old_post)

    def test_different_page_id_and_cross_scope_never_redirect_old_video(self):
        row = self.removed()
        preview = self.preview(row)
        before = self.capture(row)
        for account, page in ((self.other, self.other_page), (self.account, self.other_page),
                              (self.other, self.page)):
            with self.subTest(account=account, page=page):
                with self.assertRaises(ValueError):
                    self.create(row, preview=preview, account=account, page=page)
                self.assert_unchanged(row, before)
        self.accounts.save_page(self.account, 'Different ID', 'facebook.com/profile.php?id=999',
                                '999', record_id=self.page)
        with self.assertRaises(ValueError):
            self.preview(row)
        with self.assertRaises(ValueError):
            self.create(row, preview=preview)
        self.assert_unchanged(row, before)

    def test_non_numeric_legacy_target_cannot_be_reused(self):
        row = self.removed()
        with self.store.activity.database() as conn:
            conn.execute('UPDATE posts SET target_id=? WHERE id=?', ('invalid-target', row['post_id']))
        before = self.capture(row)
        with self.assertRaises(ValueError):
            self.preview(row)
        self.assert_unchanged(row, before)

    def test_page_name_changed_after_preview_needs_new_confirmation(self):
        row = self.removed()
        preview = self.preview(row)
        before = self.capture(row)
        self.accounts.save_page(self.account, 'Renamed after preview', 'facebook.com/profile.php?id=100',
                                '100', record_id=self.page)
        with self.assertRaises(ValueError):
            self.create(row, preview=preview)
        self.assert_unchanged(row, before)
        self.assertEqual(self.create(row)['state'], 'scheduled')

    def test_confirmed_published_post_never_gains_new_attempt_from_list_removal(self):
        row = self.remove_row(self.stopped_publish(published=True))
        before = self.capture(row)
        with self.assertRaises(ValueError):
            self.preview(row)
        with self.assertRaises(ValueError):
            self.create(row, preview={'schedule': row})
        self.assert_unchanged(row, before)

    def submitted_removed(self, campaign_id='a'*32):
        row = self.stopped_publish(campaign_id)
        self.controller._finish_without_verification(row)
        row = self.store.get(row['id'])
        self.assertEqual((row['state'], bool(row['finished_at'])), ('submitted', True))
        return self.remove_row(row)

    def test_unverified_send_while_checks_paused_allows_explicit_new_attempt(self):
        row = self.submitted_removed()
        old_post = self.store.activity.post(self.account, self.page, row['post_id'])
        old_events = self.store.activity.events(self.account, self.page, row['post_id'])
        removals = self.store.removal_history(row['id'])
        preview = self.preview(row)
        self.assertEqual(preview['classification'], 'removed_unknown')
        created = self.create(row, preview=preview)
        self.assertEqual(created['state'], 'scheduled')
        self.assertNotEqual(created['id'], row['id'])
        self.assertEqual(created['source_key'], row['source_key'])
        old = self.store.get(row['id'])
        self.assertEqual(old['source_key'], 'removed:'+row['id'])
        for key in ('state', 'finished_at', 'attempted_at', 'phase', 'post_id', 'body', 'snapshot'):
            self.assertEqual(old[key], row[key], key)
        self.assertEqual(self.store.activity.post(self.account, self.page, row['post_id']), old_post)
        self.assertEqual(self.store.activity.events(self.account, self.page, row['post_id']), old_events)
        self.assertEqual(self.store.removal_history(row['id']), removals)
        self.assertEqual(self.calls, [])
        with self.assertRaises(ValueError):
            self.create(row, preview=preview)

    def test_other_finished_removed_rows_stay_blocked_without_matching_unverified_send(self):
        stopped = self.removed()
        with self.store.database() as conn:
            conn.execute('UPDATE schedules SET finished_at=? WHERE id=?', ('2026-10-15T16:05:40+07:00', stopped['id']))
        sent = self.submitted_removed('b'*32)
        sent_preview = self.preview(sent)
        with self.store.database() as conn:
            conn.execute('UPDATE schedules SET finished_at=? WHERE id=?', ('2026-10-16T00:00:00+07:00', sent['id']))
        receipt = self.submitted_removed('c'*32)
        receipt_preview = self.preview(receipt)
        with self.store.activity.database() as conn:
            conn.execute('UPDATE posts SET browser_receipt=? WHERE id=?', ('{"url":"observed receipt"}', receipt['post_id']))
        for row, preview in ((stopped, None), (sent, sent_preview), (receipt, receipt_preview)):
            with self.subTest(row=row['id']):
                row = self.store.get(row['id'])
                before = self.capture(row)
                with self.assertRaises(ValueError):
                    self.preview(row)
                if preview is not None:
                    with self.assertRaises(ValueError):
                        self.create(row, preview=preview)
                self.assert_unchanged(row, before)

    def test_any_result_receipt_url_id_assets_or_publication_time_blocks_unknown_replay(self):
        cases = dict(browser_receipt='{"url":"observed receipt"}', external_id='123',
            external_url='https://www.facebook.com/100/posts/123',
            manual_url='https://www.facebook.com/100/posts/123',
            published_at='2026-10-14T14:36:19+07:00', remote_assets='["uploaded asset"]')
        for index, (field, value) in enumerate(cases.items(), 1):
            with self.subTest(field=field):
                row = self.removed(f'{index:032x}')
                preview = self.preview(row)
                with self.store.activity.database() as conn:
                    conn.execute(f'UPDATE posts SET {field}=? WHERE id=?', (value, row['post_id']))
                before = self.capture(row)
                with self.assertRaises(ValueError):
                    self.preview(row)
                with self.assertRaises(ValueError):
                    self.create(row, preview=preview)
                self.assert_unchanged(row, before)

    def test_published_event_blocks_even_if_current_post_was_reset_to_unknown(self):
        row = self.removed()
        preview = self.preview(row)
        with self.store.activity.database() as conn:
            self.store.activity._event(conn, row['post_id'], 'published', 'Historical confirmed publication')
        before = self.capture(row)
        with self.assertRaises(ValueError):
            self.preview(row)
        with self.assertRaises(ValueError):
            self.create(row, preview=preview)
        self.assert_unchanged(row, before)

    def test_old_schedule_must_be_removed_and_still_reserve_original_campaign_slot(self):
        row = self.stopped_publish()
        before = self.capture(row)
        with self.assertRaises(ValueError):
            self.preview(row)
        self.assert_unchanged(row, before)
        untouched = self.remove_row(self.schedule('b'*32))
        before = self.capture(untouched)
        self.assertTrue(untouched['source_key'].startswith('removed:'))
        with self.assertRaises(ValueError):
            self.preview(untouched)
        self.assert_unchanged(untouched, before)

    def test_inflight_old_post_and_archived_evidence_never_create_new_draft(self):
        for index, state in enumerate(('preparing', 'submitting', 'processing'), 1):
            with self.subTest(state=state):
                row = self.removed(f'{index:032x}')
                preview = self.preview(row)
                with self.store.activity.database() as conn:
                    conn.execute('UPDATE posts SET state=? WHERE id=?', (state, row['post_id']))
                before = self.capture(row)
                with self.assertRaises(ValueError):
                    self.preview(row)
                with self.assertRaises(ValueError):
                    self.create(row, preview=preview)
                self.assert_unchanged(row, before)
        row = self.removed('f'*32)
        preview = self.preview(row)
        with self.store.activity.database() as conn:
            conn.execute('UPDATE posts SET archived=1 WHERE id=?', (row['post_id'],))
        before = self.capture(row)
        with self.assertRaises(ValueError):
            self.create(row, preview=preview)
        self.assert_unchanged(row, before)

    def test_changed_old_post_body_source_snapshot_or_event_refuses_latest_confirmation(self):
        for index, field in enumerate(('body', 'snapshot', 'event'), 1):
            with self.subTest(field=field):
                row = self.removed(f'{index:032x}')
                preview = self.preview(row)
                if field == 'body':
                    with self.store.activity.database() as conn:
                        conn.execute('UPDATE posts SET body=? WHERE id=?', ('Changed caption', row['post_id']))
                elif field == 'snapshot':
                    import json
                    value = deepcopy(row['snapshot'])
                    value['body'] = 'Changed old snapshot'
                    with self.store.database() as conn:
                        conn.execute('UPDATE schedules SET snapshot=? WHERE id=?',
                                     (json.dumps(value), row['id']))
                else:
                    with self.store.activity.database() as conn:
                        self.store.activity._event(conn, row['post_id'], 'unknown', 'Later observed result')
                before = self.capture(row)
                with self.assertRaises(ValueError):
                    self.create(row, preview=preview)
                self.assert_unchanged(row, before)

    def test_tampered_preview_is_never_an_authorization_to_change_caption_target_or_row(self):
        row = self.removed()
        before = self.capture(row)
        for key in ('schedule', 'post', 'target', 'events'):
            with self.subTest(key=key):
                preview = deepcopy(self.preview(row))
                if key == 'events':
                    preview[key] = []
                elif key == 'target':
                    preview[key]['page_id'] = '999'
                else:
                    preview[key]['body'] = 'Injected caption'
                with self.assertRaises(ValueError):
                    self.create(row, preview=preview)
                self.assert_unchanged(row, before)

    def test_missing_or_changed_video_bytes_preserving_stat_blocks_preview_and_save(self):
        row = self.removed()
        preview = self.preview(row)
        video = Path(row['source']['path'])
        data = video.read_bytes()
        video.write_bytes(b'X'+data[1:])
        os.utime(video, ns=(row['source']['mtime_ns'], row['source']['mtime_ns']))
        before = self.capture(row)
        with self.assertRaises(ValueError):
            self.preview(row)
        with self.assertRaises(ValueError):
            self.create(row, preview=preview)
        self.assert_unchanged(row, before)
        video.unlink()
        with self.assertRaises((OSError, ValueError)):
            self.create(row, preview=preview)
        self.assert_unchanged(row, before)

    def test_campaign_video_no_longer_completed_blocks_new_attempt_without_freeing_source(self):
        import json
        row = self.removed()
        preview = self.preview(row)
        path = Path(row['source']['campaign'])/'campaign.json'
        manifest = json.loads(path.read_text(encoding='utf-8'))
        manifest['items'][0]['status'] = 'review_required'
        atomic_json(path, manifest)
        before = self.capture(row)
        with self.assertRaises(ValueError):
            self.create(row, preview=preview)
        self.assert_unchanged(row, before)

    def test_invalid_or_past_new_due_times_never_create_or_release_anything(self):
        row = self.removed()
        preview = self.preview(row)
        before = self.capture(row)
        for due in (time.time()-60, time.time()+1, time.time()+367*86400, float('nan'), float('inf'), True):
            with self.subTest(due=due):
                with self.assertRaises(ValueError):
                    self.create(row, preview=preview, due_at=due)
                self.assert_unchanged(row, before)

    def test_malformed_preview_shapes_raise_value_error_before_any_write_or_lock(self):
        row = self.removed()
        before = self.capture(row)
        for preview in ([], 'preview', {}, {'post': []}, {'post': None}, {'post': 'wrong shape'}):
            with self.subTest(preview=preview):
                with self.assertRaises(ValueError):
                    self.create(row, preview=preview)
                self.assert_unchanged(row, before)

    def test_due_time_conversion_failure_preserves_reserved_source_and_old_evidence(self):
        row = self.removed()
        preview = self.preview(row)
        before = self.capture(row)
        for due in (None, object(), [], {}, 'tomorrow', ''):
            with self.subTest(due=type(due).__name__):
                with self.assertRaises(ValueError):
                    self.controller.create_new_attempt(self.account, self.page, row['id'], due,
                        expected_preview=preview, checked_unpublished=True, closed_composer=True)
                self.assert_unchanged(row, before)

    def test_due_time_expiring_during_source_hashing_does_not_save_past_appointment(self):
        from teodub.common import hash_file
        row = self.removed()
        preview = self.preview(row)
        before = self.capture(row)
        clock = [time.time()]
        due = clock[0]+30

        def slow_source_hash(path):
            digest = hash_file(path)
            clock[0] = due+1
            return digest

        with patch('teodub.page_schedule_new_attempt.time.time', side_effect=lambda: clock[0]), \
                patch('teodub.page_schedule_recovery.hash_file', side_effect=slow_source_hash):
            with self.assertRaises(ValueError):
                self.create(row, preview=preview, due_at=due)
        self.assert_unchanged(row, before)

    def test_due_time_expiring_after_database_inserts_rolls_back_both_stores_and_slot_transfer(self):
        from teodub.page_schedule_recovery import _target
        row = self.removed()
        preview = self.preview(row)
        before = self.capture(row)
        clock = [time.time()]
        due = clock[0]+30
        checks = [0]

        def delayed_final_target_check(*args):
            target = _target(*args)
            checks[0] += 1
            if checks[0] == 2:
                clock[0] = due+1
            return target

        with patch('teodub.page_schedule_new_attempt.time.time', side_effect=lambda: clock[0]), \
                patch('teodub.page_schedule_new_attempt._target', side_effect=delayed_final_target_check):
            with self.assertRaises(ValueError):
                self.create(row, preview=preview, due_at=due)
        self.assertEqual(checks[0], 2)
        self.assert_unchanged(row, before)

    def test_real_chrome_open_pending_loading_or_queued_jobs_block_despite_checked_boxes(self):
        row = self.removed()
        preview = self.preview(row)
        before = self.capture(row)
        cases = ((True, False, False, 'done'), (False, True, False, 'done'),
                 (False, False, True, 'done'), (False, False, False, 'queued'),
                 (False, False, False, 'running'), (False, False, False, 'opening'))
        for pending, opened, loading, state in cases:
            with self.subTest(pending=pending, opened=opened, loading=loading, state=state):
                self.owner.sessions[self.account] = SimpleNamespace(pending=pending, opened=opened,
                    loading_pages=loading, post_actions={'unrelated': dict(state=state)},
                    navigation={}, page_load_requests={})
                with self.assertRaises(ValueError):
                    self.create(row, preview=preview)
                self.assert_unchanged(row, before)
        self.owner.sessions.clear()

    def test_other_account_browser_work_does_not_redirect_or_block_verified_closed_scope(self):
        row = self.removed()
        self.owner.sessions[self.other] = SimpleNamespace(pending=True, opened=True, loading_pages=True,
            post_actions={'another': dict(state='running')})
        fresh = self.create(row)
        self.assertEqual((fresh['account_id'], fresh['page_key']), (self.account, self.page))
        self.assertTrue(self.owner.sessions[self.other].opened)
        self.assertEqual(self.calls, [])

    def test_owned_or_foreign_schedule_post_and_creation_locks_prevent_recreation(self):
        row = self.removed()
        preview = self.preview(row)
        before = self.capture(row)
        self.controller.active.add(row['id'])
        with self.assertRaises(ValueError):
            self.create(row, preview=preview)
        self.controller.active.clear()
        self.assert_unchanged(row, before)
        from teodub.pipeline import _job_lock
        paths = (self.store.path.parent/'schedule-locks'/row['id'],
                 self.store.path.parent/'post-locks'/row['post_id'],
                 self.store.path.parent/'schedule-creation')
        for path in paths:
            with self.subTest(lock=path.name):
                path.mkdir(parents=True, exist_ok=True)
                with _job_lock(path):
                    with self.assertRaises((OSError, ValueError)):
                        self.create(row, preview=preview)
                self.assert_unchanged(row, before)

    def test_closing_closed_or_restart_draining_controller_never_creates_new_row(self):
        row = self.removed()
        preview = self.preview(row)
        before = self.capture(row)
        for flag in ('closing', 'closed', 'restart_draining'):
            with self.subTest(flag=flag):
                setattr(self.owner.studio, flag, True)
                with self.assertRaises(ValueError):
                    self.create(row, preview=preview)
                setattr(self.owner.studio, flag, False)
                self.assert_unchanged(row, before)
        self.controller.closed = True
        with self.assertRaises(ValueError):
            self.create(row, preview=preview)
        self.controller.closed = False
        self.assert_unchanged(row, before)

    def test_atomic_new_slot_old_evidence_and_journal_survive_reload_without_auto_start(self):
        row = self.removed()
        fresh = self.create(row)
        reloaded = PageScheduleStore(self.directory)
        self.assertEqual(reloaded.get(fresh['id']), fresh)
        self.assertEqual(reloaded.get(row['id']), self.store.get(row['id']))
        self.assertEqual(reloaded.new_attempt_history(row['id']), self.store.new_attempt_history(row['id']))
        second = PageScheduleController(self.owner)
        self.addCleanup(second.close)
        second.last_tick = 0
        second.tick()
        self.assertEqual(second.active, set())
        self.assertEqual(second.guards, {})
        self.assertEqual(self.calls, [])

    def test_wal_on_either_database_refuses_cross_file_atomicity_without_partial_rows(self):
        for index, database in enumerate(('schedule', 'activity'), 1):
            with self.subTest(database=database):
                row = self.removed(f'{index:032x}')
                preview = self.preview(row)
                target = self.store if database == 'schedule' else self.store.activity
                with target.database() as conn:
                    self.assertEqual(conn.execute('PRAGMA journal_mode=WAL').fetchone()[0], 'wal')
                before = self.capture(row)
                with self.assertRaises(ValueError):
                    self.create(row, preview=preview)
                self.assert_unchanged(row, before)
                with target.database() as conn:
                    self.assertEqual(conn.execute('PRAGMA journal_mode=DELETE').fetchone()[0], 'delete')

    def test_new_post_insert_failure_never_frees_old_reserved_source(self):
        row = self.removed()
        preview = self.preview(row)
        with self.store.activity.database() as conn:
            conn.execute('''CREATE TRIGGER reject_new_attempt_post BEFORE INSERT ON posts
                BEGIN SELECT RAISE(ABORT, 'Synthetic draft insert failure'); END''')
        before = self.capture(row)
        with self.assertRaises(ValueError):
            self.create(row, preview=preview)
        self.assert_unchanged(row, before)

    def test_new_schedule_insert_failure_rolls_back_draft_and_old_source_release(self):
        row = self.removed()
        preview = self.preview(row)
        with self.store.database() as conn:
            conn.execute('''CREATE TRIGGER reject_new_attempt_schedule BEFORE INSERT ON schedules
                BEGIN SELECT RAISE(ABORT, 'Synthetic new schedule insert failure'); END''')
        before = self.capture(row)
        with self.assertRaises(ValueError):
            self.create(row, preview=preview)
        self.assert_unchanged(row, before)

    def test_final_journal_write_failure_rolls_back_both_databases_and_source_reservation(self):
        row = self.removed()
        preview = self.preview(row)
        self.store.new_attempt_history(row['id'])
        with self.store.database() as conn:
            conn.execute('''CREATE TABLE schedule_new_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,schedule_id TEXT NOT NULL UNIQUE,at TEXT NOT NULL,
                previous TEXT NOT NULL,new_schedule_id TEXT NOT NULL,new_post_id TEXT NOT NULL,
                new_due_at REAL NOT NULL,checked_unpublished INTEGER NOT NULL,closed_composer INTEGER NOT NULL)''')
            conn.execute('''CREATE TRIGGER reject_new_attempt_journal BEFORE INSERT ON schedule_new_attempts
                BEGIN SELECT RAISE(ABORT, 'Synthetic final journal insert failure'); END''')
        before = self.capture(row)
        with self.assertRaises(ValueError):
            self.create(row, preview=preview)
        self.assert_unchanged(row, before)

    def test_page_registry_change_during_hashing_rolls_back_every_database_write(self):
        from teodub.common import hash_file
        row = self.removed()
        preview = self.preview(row)
        before = self.capture(row)

        def hashing_with_registry_change(path):
            value = hash_file(path)
            self.accounts.save_page(self.account, 'Changed during creation',
                'facebook.com/changed-during-hash', '100', record_id=self.page)
            return value

        with patch('teodub.page_schedule_recovery.hash_file', side_effect=hashing_with_registry_change):
            with self.assertRaises(ValueError):
                self.create(row, preview=preview)
        self.assert_unchanged(row, before)


if __name__ == '__main__':
    unittest.main()
