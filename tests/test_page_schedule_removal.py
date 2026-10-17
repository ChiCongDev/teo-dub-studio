"""Local video-list removal in disposable stores; no files or Facebook posts deleted."""
from copy import deepcopy
from pathlib import Path
import sqlite3
import time
from types import SimpleNamespace
import unittest

from tests import test_page_schedule as fixtures
from teodub.page_schedule import PageScheduleController, PageScheduleStore


class ScheduleRemovalTests(unittest.TestCase):
    setUp = fixtures.ScheduleTests.setUp
    video = fixtures.ScheduleTests.video
    schedule = fixtures.ScheduleTests.schedule
    submit = fixtures.ScheduleTests.submit
    tick = fixtures.ScheduleTests.tick
    reply = fixtures.ScheduleTests.reply
    ready_for_validation = fixtures.ScheduleTests.ready_for_validation

    def preview(self, *rows, account=None, page=None):
        return self.store.removal_preview([row['id'] for row in rows],
            account_id=account or self.account, page_key=page or self.page)

    def remove(self, *rows, previews=None, store=False, account=None, page=None):
        return (self.store if store else self.controller).remove_many(
            self.preview(*rows) if previews is None else previews,
            account_id=account or self.account, page_key=page or self.page)

    def capture(self, row):
        return dict(schedule=self.store.get(row['id']),
            posts=self.store.activity.posts(self.account, self.page, include_archived=True),
            events=self.store.activity.events(self.account, self.page, row['post_id']),
            history=self.store.removal_history(row['id']))

    def assert_blocked(self, row, *, previews=None, store=False, account=None, page=None):
        before = self.capture(row)
        try:
            result = self.remove(row, previews=previews, store=store, account=account, page=page)
        except (OSError, ValueError):
            pass
        else:
            self.assertEqual(result['removed'], [])
            self.assertIn(row['id'], result['skipped'])
        self.assertEqual(self.capture(row), before)
        self.assertEqual(self.calls, [])

    def stopped_publish(self, campaign_id='a'*32, *, published=False):
        row = self.schedule(campaign_id)
        scope = self.account, self.page, row['post_id']
        activity = self.store.activity
        activity.transition(*scope, 'preparing', 'Synthetic browser upload', expected={'draft'})
        post = activity.post(*scope)
        review = dict(account_id=self.account, page_key=self.page, post_id=row['post_id'],
            **{key: post[key] for key in ('target_name', 'target_url', 'target_id', 'body', 'browser_finish_mode')},
            media=[dict(saved, sha256=row['source']['sha256']) for saved in post['media']])
        activity.save_browser_review(*scope, review)
        activity.transition(*scope, 'submitting', 'Synthetic one publish click', expected={'review'})
        activity.transition(*scope, 'processing', 'Synthetic awaiting receipt', expected={'submitting'})
        if published:
            url = 'https://www.facebook.com/100/posts/123'
            activity.record_browser_receipt(*scope, dict(source='visible_dom', url=url, checked_at='fixture'))
            activity.transition(*scope, 'published', 'Synthetic confirmed receipt',
                expected={'processing'}, external_url=url)
        else:
            activity.transition(*scope, 'unknown', 'Synthetic notification lookup timed out', expected={'processing'})
        return self.store.update(row['id'], state='published' if published else 'paused',
            phase='publish', request_id='completed-publish-request',
            attempted_at='2026-10-14T14:35:48+07:00',
            finished_at='2026-10-14T14:37:49+07:00' if published else '')

    def test_unknown_publish_can_leave_list_without_erasing_evidence_or_releasing_source(self):
        row = self.stopped_publish()
        before = self.capture(row)
        video = Path(row['source']['path'])
        video_bytes, video_stat = video.read_bytes(), video.stat()
        preview = self.preview(row)
        self.assertEqual(preview[0]['id'], row['id'])
        self.assertEqual(preview[0]['schedule'], row)
        self.assertEqual(preview[0]['post'], before['posts'][0])
        self.assertEqual(preview[0]['events'], before['events'])
        self.assertEqual(self.remove(row, previews=preview), dict(removed=[row['id']], skipped={}))
        removed = self.store.get(row['id'])
        self.assertEqual(removed['state'], 'removed')
        for key in row.keys()-{'state', 'message'}:
            self.assertEqual(removed[key], row[key], key)
        self.assertEqual(self.store.activity.posts(self.account, self.page, include_archived=True), before['posts'])
        self.assertEqual(self.store.activity.events(self.account, self.page, row['post_id']), before['events'])
        self.assertEqual(video.read_bytes(), video_bytes)
        self.assertEqual(video.stat().st_mtime_ns, video_stat.st_mtime_ns)
        history = self.store.removal_history(row['id'])
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]['previous'], preview[0])
        self.assertFalse(history[0]['source_released'])
        self.assertEqual(self.calls, [])
        with self.assertRaises(ValueError):
            self.store.create(self.account, self.page, row['source'], 'Another caption', '', time.time()+7200)

    def test_confirmed_publication_removal_retains_remote_receipt_and_duplicate_slot(self):
        row = self.stopped_publish(published=True)
        before = self.capture(row)
        result = self.remove(row)
        self.assertEqual(result['removed'], [row['id']])
        post = self.store.activity.post(self.account, self.page, row['post_id'])
        self.assertEqual(post, before['posts'][0])
        self.assertEqual(post['state'], 'published')
        self.assertEqual(post['external_url'], 'https://www.facebook.com/100/posts/123')
        self.assertEqual(self.store.get(row['id'])['source_key'], row['source_key'])
        self.assertEqual(self.store.activity.events(self.account, self.page, row['post_id']), before['events'])
        self.assertEqual(self.calls, [])

    def test_unattempted_draft_removal_frees_video_for_new_schedule_and_preserves_old_caption(self):
        row = self.schedule()
        before = self.capture(row)
        self.assertEqual(self.remove(row)['removed'], [row['id']])
        removed = self.store.get(row['id'])
        self.assertEqual(removed['source_key'], 'removed:'+row['id'])
        self.assertEqual(removed['post_id'], row['post_id'])
        self.assertEqual(removed['source'], row['source'])
        self.assertEqual(removed['body'], row['body'])
        self.assertEqual(self.store.activity.posts(self.account, self.page, include_archived=True), before['posts'])
        self.assertEqual(self.store.activity.events(self.account, self.page, row['post_id']), before['events'])
        self.assertTrue(self.store.removal_history(row['id'])[0]['source_released'])
        fresh = self.store.create(self.account, self.page, row['source'], 'New explicit caption', '', time.time()+7200)
        self.assertNotEqual(fresh['id'], row['id'])
        self.assertNotEqual(fresh['post_id'], row['post_id'])
        self.assertEqual(fresh['state'], 'scheduled')
        self.assertEqual(fresh['source_key'], row['source_key'])
        self.assertEqual(self.calls, [])

    def test_old_unknown_preparation_removal_preserves_attempt_even_without_review(self):
        row = self.schedule()
        scope = self.account, self.page, row['post_id']
        self.store.activity.transition(*scope, 'preparing', 'Synthetic upload', expected={'draft'})
        self.store.activity.transition(*scope, 'unknown', 'Synthetic abandoned preparation', expected={'preparing'})
        row = self.store.update(row['id'], state='needs_attention', phase='prepare', attempted_at='Prior upload')
        self.assertEqual(self.remove(row)['removed'], [row['id']])
        self.assertEqual(self.store.get(row['id'])['source_key'], row['source_key'])
        self.assertFalse(self.store.removal_history(row['id'])[0]['source_released'])
        self.assertEqual(self.store.activity.post(*scope)['attempt'], 1)

    def test_sparse_or_inconsistent_attempt_evidence_never_frees_campaign_source(self):
        cases = {'attempted_at': 'Prior attempt', 'phase': 'prepare', 'request_id': 'Prior request',
                 'finished_at': 'Prior completion'}
        for index, (field, value) in enumerate(cases.items(), 1):
            with self.subTest(field=field):
                row = self.store.update(self.schedule(f'{index:032x}')['id'], **{field: value})
                self.assertEqual(self.remove(row)['removed'], [row['id']])
                self.assertEqual(self.store.get(row['id'])['source_key'], row['source_key'])
                self.assertFalse(self.store.removal_history(row['id'])[0]['source_released'])

    def test_post_evidence_without_schedule_attempt_still_preserves_duplicate_source(self):
        for index, (field, value) in enumerate((('attempt', 1), ('browser_receipt', '{"url":"observed"}'),
                ('browser_tracking', '{"submitted_at":1792033200}'),
                ('external_url', 'https://www.facebook.com/100/posts/123'),
                ('browser_review', '{"body":"Approved composer"}')), 1):
            with self.subTest(field=field):
                row = self.schedule(f'{index:032x}')
                with self.store.activity.database() as conn:
                    conn.execute(f'UPDATE posts SET {field}=? WHERE id=?', (value, row['post_id']))
                before = self.capture(row)
                self.assertEqual(self.remove(row)['removed'], [row['id']])
                self.assertEqual(self.store.get(row['id'])['source_key'], row['source_key'])
                self.assertEqual(self.store.activity.posts(self.account, self.page, include_archived=True), before['posts'])

    def test_submission_event_still_reserves_source_if_other_attempt_fields_were_lost(self):
        row = self.schedule()
        with self.store.activity.database() as conn:
            self.store.activity._event(conn, row['post_id'], 'submitting', 'Historical one click evidence')
        self.assertEqual(self.remove(row)['removed'], [row['id']])
        self.assertEqual(self.store.get(row['id'])['source_key'], row['source_key'])
        self.assertFalse(self.store.removal_history(row['id'])[0]['source_released'])
        with self.assertRaises(ValueError):
            self.store.create(self.account, self.page, row['source'], 'Unsafe duplicate', '', time.time()+7200)

    def test_removed_schedules_remain_available_to_duplicate_inventory_and_history_after_reload(self):
        row = self.stopped_publish()
        self.remove(row)
        reloaded = PageScheduleStore(self.directory)
        self.assertEqual(reloaded.get(row['id']), self.store.get(row['id']))
        self.assertEqual(reloaded.list(self.account, self.page), [self.store.get(row['id'])])
        self.assertEqual(reloaded.removal_history(row['id']), self.store.removal_history(row['id']))
        second = PageScheduleController(self.owner)
        self.addCleanup(second.close)
        second.last_tick = 0
        second.tick()
        self.assertEqual(second.active, set())
        self.assertEqual(second.guards, {})
        self.assertEqual(self.calls, [])

    def test_removed_row_cannot_update_arm_change_time_retry_or_recover(self):
        row = self.schedule()
        self.remove(row)
        after = self.capture(row)
        actions = [lambda: self.store.update(row['id'], state='scheduled'),
            lambda: self.store.reschedule(row['id'], time.time()+7200),
            lambda: self.controller.start(row['id']),
            lambda: self.controller.start_now(self.account, self.page, row['id'], expected_schedule=row),
            lambda: self.store.cancel_unattempted(row['id']),
            lambda: self.store.reschedule_failed_preparation(row['id'], time.time()+7200),
            lambda: self.store.recovery_preview(self.account, self.page, row['id'])]
        for action in actions:
            with self.subTest(action=actions.index(action)):
                with self.assertRaises(ValueError):
                    action()
                self.assertEqual(self.capture(row), after)
        results = self.controller.start_many(self.account, self.page, [row['id']])
        self.assertEqual(results[0]['outcome'], 'skipped')
        self.assertEqual(self.calls, [])

    def test_repeated_confirmation_is_idempotent_without_extra_journal_or_post(self):
        row = self.schedule()
        preview = self.preview(row)
        self.remove(row, previews=preview)
        self.assert_blocked(row, previews=preview)
        self.assertEqual(len(self.store.removal_history(row['id'])), 1)
        self.assertEqual(len(self.store.activity.posts(self.account, self.page, include_archived=True)), 1)

    def test_changed_due_time_after_confirmation_preview_requires_new_confirmation(self):
        row = self.schedule()
        preview = self.preview(row)
        self.store.update(row['id'], due_at=time.time()+7200)
        self.assert_blocked(row, previews=preview)
        self.assertEqual(self.remove(row)['removed'], [row['id']])

    def test_changed_post_or_event_after_preview_refuses_stale_confirmation(self):
        row = self.schedule()
        preview = self.preview(row)
        with self.store.activity.database() as conn:
            conn.execute('UPDATE posts SET body=? WHERE id=?', ('Concurrent changed caption', row['post_id']))
        self.assert_blocked(row, previews=preview)
        preview = self.preview(row)
        with self.store.activity.database() as conn:
            self.store.activity._event(conn, row['post_id'], 'draft', 'Concurrent evidence recorded')
        self.assert_blocked(row, previews=preview)

    def test_caller_modified_preview_cannot_bypass_stale_confirmation_guard(self):
        row = self.schedule()
        for key in ('schedule', 'post', 'events'):
            with self.subTest(key=key):
                preview = deepcopy(self.preview(row))
                if key == 'events':
                    preview[0][key] = []
                else:
                    preview[0][key]['body'] = 'Changed by caller'
                self.assert_blocked(row, previews=preview)

    def test_cross_account_page_and_malformed_identifier_never_redirect_removal(self):
        row = self.schedule()
        preview = self.preview(row)
        for account, page in ((self.other, self.other_page), (self.account, self.other_page),
                              (self.other, self.page)):
            with self.subTest(account=account, page=page):
                self.assert_blocked(row, previews=preview, account=account, page=page)
        malformed = deepcopy(preview)
        malformed[0]['id'] = '../unrelated-path'
        before = self.capture(row)
        result = self.remove(previews=malformed)
        self.assertEqual(result['removed'], [])
        self.assertIn('../unrelated-path', result['skipped'])
        self.assertEqual(self.capture(row), before)
        self.assertEqual(self.store.removal_history(row['id']), [])

    def test_bulk_selection_is_deduplicated_and_stale_rows_skip_without_losing_valid_rows(self):
        fresh = self.schedule()
        stale = self.schedule('b'*32)
        preview = self.preview(fresh, stale)
        preview.append(deepcopy(preview[0]))
        self.store.update(stale['id'], message='Changed after confirmation preview')
        stale_before = self.capture(stale)
        result = self.remove(previews=preview)
        self.assertEqual(result['removed'], [fresh['id']])
        self.assertEqual(set(result['skipped']), {stale['id']})
        self.assertEqual(self.capture(stale), stale_before)
        self.assertEqual(len(self.store.removal_history(fresh['id'])), 1)
        self.assertEqual(self.calls, [])

    def test_inflight_schedule_and_post_states_cannot_be_removed(self):
        for index, state in enumerate(('preparing', 'submitting', 'processing', 'verifying'), 1):
            with self.subTest(schedule_state=state):
                row = self.schedule(f'{index:032x}')
                preview = self.preview(row)
                row = self.store.update(row['id'], state=state)
                self.assert_blocked(row, previews=preview)
                self.assert_blocked(row)
        for index, state in enumerate(('preparing', 'submitting', 'processing'), 10):
            with self.subTest(post_state=state):
                row = self.schedule(f'{index:032x}')
                preview = self.preview(row)
                with self.store.activity.database() as conn:
                    conn.execute('UPDATE posts SET state=? WHERE id=?', (state, row['post_id']))
                self.assert_blocked(row, previews=preview)
                self.assert_blocked(row)

    def test_matching_queued_running_or_opening_request_blocks_removal_without_chrome_changes(self):
        row = self.store.update(self.schedule()['id'], request_id='same-request')
        preview = self.preview(row)
        for state in ('queued', 'running', 'opening', 'verifying'):
            with self.subTest(state=state):
                session = SimpleNamespace(pending=True, opened=True, loading_pages=False,
                    post_actions={'same-request': dict(state=state)}, navigation={}, page_load_requests={})
                self.owner.sessions[self.account] = session
                self.assert_blocked(row, previews=preview)
                self.assertTrue(session.pending)
                self.assertTrue(session.opened)
                self.assertEqual(session.post_actions['same-request']['state'], state)
        self.owner.sessions.clear()

    def test_matching_post_request_blocks_even_when_schedule_request_id_was_not_saved(self):
        row = self.schedule()
        self.owner.sessions[self.account] = SimpleNamespace(loading_pages=False,
            post_actions={'independent-action': dict(state='running', post_id=row['post_id'])},
            navigation={}, page_load_requests={})
        self.assert_blocked(row)

    def test_unrelated_account_work_and_idle_open_chrome_do_not_require_discarding_composer(self):
        row = self.schedule()
        session = SimpleNamespace(pending=True, opened=True, loading_pages=False,
            post_actions={'another-action': dict(state='running', post_id='b'*32)},
            navigation={}, page_load_requests={})
        self.owner.sessions[self.account] = session
        self.assertEqual(self.remove(row)['removed'], [row['id']])
        self.assertTrue(session.pending)
        self.assertTrue(session.opened)
        self.assertEqual(session.post_actions['another-action']['state'], 'running')
        self.assertEqual(self.calls, [])

    def test_completed_request_record_does_not_permanently_lock_local_cleanup(self):
        row = self.stopped_publish()
        self.owner.sessions[self.account] = SimpleNamespace(pending=True, opened=True, loading_pages=False,
            post_actions={row['request_id']: dict(state='error', post_id=row['post_id'])})
        self.assertEqual(self.remove(row)['removed'], [row['id']])
        self.assertEqual(self.store.get(row['id'])['source_key'], row['source_key'])

    def test_controller_closing_or_restarting_never_mutates_rows_after_ui_confirmation(self):
        row = self.schedule()
        preview = self.preview(row)
        for flag in ('closing', 'closed', 'restart_draining'):
            with self.subTest(flag=flag):
                setattr(self.owner.studio, flag, True)
                self.assert_blocked(row, previews=preview)
                setattr(self.owner.studio, flag, False)
        self.controller.closed = True
        self.assert_blocked(row, previews=preview)
        self.controller.closed = False

    def test_text_schedule_rejects_video_button_without_changing_text_or_history(self):
        post = self.store.activity.save_draft(self.account, self.page, 'Unrelated text post',
            delivery_mode='browser', browser_finish_mode='tool')
        row = self.store.create_post(self.account, self.page, post['id'], time.time()+3600)
        before = self.capture(row)
        with self.assertRaises(ValueError):
            self.preview(row)
        self.assertEqual(self.capture(row), before)
        self.assertEqual(self.calls, [])

    def test_future_armed_idle_row_removal_releases_own_guard_and_prevents_timer_send(self):
        row = self.schedule()
        self.controller.start(row['id'])
        self.assertIn(row['id'], self.controller.guards)
        self.assertEqual(self.remove(row)['removed'], [row['id']])
        self.assertNotIn(row['id'], self.controller.guards)
        self.assertNotIn(row['id'], self.controller.active)
        self.tick()
        self.assertEqual(self.calls, [])
        self.assertEqual(self.store.get(row['id'])['state'], 'removed')

    def test_another_controller_guard_or_post_lock_blocks_removal_without_partial_write(self):
        row = self.schedule()
        preview = self.preview(row)
        with self.controller.guard(row['id']):
            self.assert_blocked(row, previews=preview)
        from teodub.pipeline import _job_lock
        post_lock = self.store.path.parent/'post-locks'/row['post_id']
        post_lock.mkdir(parents=True, exist_ok=True)
        with _job_lock(post_lock):
            self.assert_blocked(row, previews=preview)
        self.assertEqual(self.remove(row)['removed'], [row['id']])

    def test_controller_owned_attempted_or_preparing_guard_cannot_be_silently_abandoned(self):
        row = self.schedule()
        self.controller.start(row['id'])
        row = self.store.update(row['id'], state='paused', phase='prepare', attempted_at='In-flight attempt')
        self.assert_blocked(row)
        self.assertIn(row['id'], self.controller.guards)
        self.assertIn(row['id'], self.controller.active)

    def test_removed_cancelled_row_keeps_already_released_slot_and_original_post_archive(self):
        row = self.store.cancel_unattempted(self.schedule()['id'])
        before = self.capture(row)
        self.assertEqual(self.remove(row)['removed'], [row['id']])
        removed = self.store.get(row['id'])
        self.assertEqual(removed['state'], 'removed')
        self.assertEqual(removed['source_key'], row['source_key'])
        self.assertEqual(self.store.activity.posts(self.account, self.page, include_archived=True), before['posts'])
        self.assertEqual(self.store.activity.events(self.account, self.page, row['post_id']), before['events'])

    def test_missing_video_and_changed_page_name_do_not_prevent_local_list_cleanup(self):
        row = self.schedule()
        Path(row['source']['path']).unlink()
        self.accounts.save_page(self.account, 'Corrected Page name', 'facebook.com/renamedpage',
                                '100', record_id=self.page)
        self.assertEqual(self.remove(row)['removed'], [row['id']])
        self.assertEqual(self.calls, [])

    def test_journal_insert_failure_rolls_back_state_and_source_slot_together(self):
        row = self.schedule()
        preview = self.preview(row)
        # Initializing this optional journal is read-only for existing schedule rows.
        self.store.removal_history(row['id'])
        with self.store.database() as conn:
            conn.execute('''CREATE TRIGGER reject_removal_audit BEFORE INSERT ON schedule_removals
                BEGIN SELECT RAISE(ABORT, 'Synthetic final audit write failure'); END''')
        self.assert_blocked(row, previews=preview)
        with self.assertRaises(ValueError):
            self.store.create(self.account, self.page, row['source'], 'Duplicate after rollback', '', time.time()+7200)

    def test_schedule_update_failure_rolls_back_journal_already_inserted_in_transaction(self):
        row = self.schedule()
        preview = self.preview(row)
        with self.store.database() as conn:
            conn.execute('''CREATE TRIGGER reject_removal_update BEFORE UPDATE ON schedules
                WHEN NEW.state='removed'
                BEGIN SELECT RAISE(ABORT, 'Synthetic schedule write failure'); END''')
        self.assert_blocked(row, previews=preview)
        self.assertEqual(self.store.removal_history(row['id']), [])

    def test_wal_store_local_removal_keeps_activity_evidence_and_durable_single_database_commit(self):
        row = self.stopped_publish()
        before = self.capture(row)
        with self.store.database() as conn:
            self.assertEqual(conn.execute('PRAGMA journal_mode=WAL').fetchone()[0], 'wal')
        self.assertEqual(self.remove(row)['removed'], [row['id']])
        reloaded = PageScheduleStore(self.directory)
        self.assertEqual(reloaded.get(row['id'])['state'], 'removed')
        self.assertEqual(len(reloaded.removal_history(row['id'])), 1)
        self.assertEqual(reloaded.activity.posts(self.account, self.page, include_archived=True), before['posts'])
        self.assertEqual(reloaded.activity.events(self.account, self.page, row['post_id']), before['events'])

    def test_busy_database_transaction_never_leaves_partial_source_release_or_history(self):
        row = self.schedule()
        preview = self.preview(row)
        conn = sqlite3.connect(self.store.path)
        try:
            conn.execute('BEGIN IMMEDIATE')
            self.assert_blocked(row, previews=preview)
        finally:
            conn.rollback()
            conn.close()
        self.assertEqual(self.remove(row)['removed'], [row['id']])


if __name__ == '__main__':
    unittest.main()
