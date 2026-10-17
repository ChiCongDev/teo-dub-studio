"""Receiver integration: read/stage workers may never arm a stale command."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from teodub.page_content_bridge import ContentPlanReceiver


ACCOUNT = 'a' * 32
PAGE = 'b' * 32
PLAN = 'c' * 32
COMMAND = 'd' * 32
DIGEST = 'e' * 64
THREAD = '01a12053-b14d-7811-b8b9-48760720e7ef'
OTHER_THREAD = '01a12053-b14d-7811-b8b9-48760720e7ee'
TARGET = dict(name='Page thử', url='https://www.facebook.com/profile.php?id=12345', page_id='12345')


class ReceiverTest(unittest.TestCase):
    def setUp(self):
        self.main_thread = threading.get_ident()
        self.page = dict(account_id=ACCOUNT, id=PAGE, **TARGET)
        self.connection = dict(thread_id=THREAD, title='Phát triển page câu cá', auto_sync=True,
                               last_sync='', last_error='')
        self.plan = dict(plan_id=PLAN, digest=DIGEST, title='Kế hoạch', state='proposed',
                         thread_id=THREAD, source_turn_id='completed-turn', fetched_at='2026-10-09', items=[])
        self.store = Mock()
        self.store.get_connection.side_effect = lambda *unused: deepcopy(self.connection)
        self.store.get_plan.side_effect = self.get_plan
        self.store.import_messages.return_value = dict(plan=self.plan, status='Đã nhận; chưa bật lịch')
        self.store.plans.return_value = [self.plan]
        self.store.approve.return_value = self.plan
        self.status_calls = []
        self.scheduler_calls = []
        self.completions = []
        self.controller = object()
        self.owner = SimpleNamespace(studio=SimpleNamespace(data_dir=Path('test-isolated-data')),
                                     status=SimpleNamespace(set=self.set_status),
                                     get_page_scheduler=self.scheduler)
        self.activity = SimpleNamespace(page=self.lookup_page,
            accounts=SimpleNamespace(load=lambda: dict(pages=[deepcopy(self.page)])))
        self.bridge = SimpleNamespace(owner=self.owner, activity=self.activity, active=None, enabled=True,
                                      complete=self.complete, validate=self.validate,
                                      capable=lambda: True)
        self.receiver = ContentPlanReceiver(self.bridge)
        self.receiver.store = Mock(return_value=self.store)
        self.addCleanup(self.receiver.close)
        self.entered = threading.Event()
        self.release = threading.Event()
        self.addCleanup(self.release.set)
        self.worker_calls = []

    def set_status(self, message):
        self.status_calls.append((threading.get_ident(), message))

    def scheduler(self):
        self.scheduler_calls.append(threading.get_ident())
        return self.controller

    def lookup_page(self, account_id, page_key):
        if (account_id, page_key) != (ACCOUNT, PAGE):
            raise ValueError('Page không thuộc tài khoản đã chọn')
        return deepcopy(self.page)

    def validate(self, row):
        payload = row['payload']
        page = self.lookup_page(payload['account_id'], payload['page_key'])
        if payload['target'] != {key: page[key] for key in TARGET}:
            raise ValueError('Page đã thay đổi sau khi gửi yêu cầu')
        return payload

    def complete(self, row, state, message, result=None):
        self.completions.append((row['id'], state, message, result))
        self.bridge.active = None

    def get_plan(self, plan_id, account_id, page_key):
        if (plan_id, account_id, page_key) != (PLAN, ACCOUNT, PAGE):
            raise ValueError('Kế hoạch không thuộc tài khoản/Page đã chọn')
        return deepcopy(self.plan)

    def command(self, action='sync_content_plan'):
        payload = dict(account_id=ACCOUNT, page_key=PAGE, target=deepcopy(TARGET))
        payload.update(dict(thread_id=THREAD) if action == 'sync_content_plan' else dict(plan_id=PLAN, plan_digest=DIGEST))
        row = dict(id=COMMAND, action=action, payload=payload, expires=time.time() + 60)
        self.bridge.active = row
        return row

    def read(self, thread_id):
        self.worker_calls.append(('read', threading.get_ident(), thread_id))
        self.entered.set()
        self.release.wait(2)
        return dict(id=THREAD, title=self.connection['title'], status='notLoaded', updated_at=1,
                    messages=[dict(turn_id='completed-turn', text='Completed final',
                                   phase='final_answer', turn_status='completed')])

    def stage(self, *args, **kwargs):
        self.worker_calls.append(('stage', threading.get_ident(), args, kwargs))
        self.entered.set()
        self.release.wait(2)
        return deepcopy(self.plan)

    def finish(self):
        self.release.set()
        deadline = time.monotonic() + 2
        while self.receiver.results.empty() and time.monotonic() < deadline:
            time.sleep(0.002)
        self.assertFalse(self.receiver.results.empty(), 'Receiver worker did not return its bounded result')
        self.receiver.observe()

    def test_read_happens_in_worker_import_on_main_and_never_arms(self):
        row = self.command()
        with patch('teodub.codex_chat.read_thread', side_effect=self.read), \
                patch('teodub.codex_translation._command', side_effect=AssertionError('No AI invocation')):
            self.receiver.dispatch(row)
            self.assertTrue(self.entered.wait(1))
            self.store.import_messages.assert_not_called()
            self.finish()
        self.assertEqual(self.worker_calls[0][0], 'read')
        self.assertNotEqual(self.worker_calls[0][1], self.main_thread)
        self.store.import_messages.assert_called_once()
        self.store.approve.assert_not_called()
        self.assertEqual(self.scheduler_calls, [])
        self.assertEqual(self.completions[0][:2], (COMMAND, 'done'))

    def test_auto_read_and_manual_sync_same_target_coalesce_one_read(self):
        with patch('teodub.codex_chat.read_thread', side_effect=self.read):
            self.receiver.start_read(ACCOUNT, PAGE)
            self.assertTrue(self.entered.wait(1))
            old_task = self.receiver.pending
            row = self.command()
            self.receiver.dispatch(row)
            self.assertIs(self.receiver.pending, old_task)
            self.finish()
        self.assertEqual(len(self.worker_calls), 1)
        self.store.import_messages.assert_called_once()
        self.assertEqual(self.completions[0][:2], (COMMAND, 'done'))

    def test_deadline_or_disabled_during_read_prevents_import_and_arming(self):
        for reason in ('expired', 'disabled'):
            with self.subTest(reason=reason):
                row = self.command()
                self.entered.clear()
                self.release.clear()
                with patch('teodub.codex_chat.read_thread', side_effect=self.read):
                    self.receiver.dispatch(row)
                    self.assertTrue(self.entered.wait(1))
                    if reason == 'expired':
                        row['expires'] = time.time() - 1
                    else:
                        self.bridge.enabled = False
                    self.finish()
                self.store.import_messages.assert_not_called()
                self.store.approve.assert_not_called()
                self.assertEqual(self.completions[-1][1], 'failed')
                self.bridge.enabled = True

    def test_closed_receiver_discards_pending_read(self):
        row = self.command()
        with patch('teodub.codex_chat.read_thread', side_effect=self.read):
            self.receiver.dispatch(row)
            self.assertTrue(self.entered.wait(1))
            self.receiver.close()
            self.finish()
        self.store.import_messages.assert_not_called()
        self.store.approve.assert_not_called()
        self.assertEqual(self.completions, [])

    def test_changed_page_after_read_started_cannot_import_stale_command(self):
        row = self.command()
        with patch('teodub.codex_chat.read_thread', side_effect=self.read):
            self.receiver.dispatch(row)
            self.assertTrue(self.entered.wait(1))
            self.page['name'] = 'Page đã đổi tên'
            self.finish()
        self.store.import_messages.assert_not_called()
        self.assertEqual(self.completions[0][1], 'failed')

    def test_changed_thread_after_read_started_cannot_import_old_reply(self):
        row = self.command()
        with patch('teodub.codex_chat.read_thread', side_effect=self.read):
            self.receiver.dispatch(row)
            self.assertTrue(self.entered.wait(1))
            self.connection['thread_id'] = OTHER_THREAD
            self.finish()
        self.store.import_messages.assert_not_called()
        self.assertEqual(self.completions[0][1], 'failed')

    def test_wrong_thread_in_command_rejected_before_worker(self):
        row = self.command()
        row['payload']['thread_id'] = OTHER_THREAD
        with patch('teodub.codex_chat.read_thread') as reader, self.assertRaises(ValueError):
            self.receiver.dispatch(row)
        reader.assert_not_called()

    def test_late_result_for_replaced_command_cannot_import_or_overwrite(self):
        row = self.command()
        with patch('teodub.codex_chat.read_thread', side_effect=self.read):
            self.receiver.dispatch(row)
            self.assertTrue(self.entered.wait(1))
            self.bridge.active = dict(row, id='f' * 32)
            self.finish()
        self.store.import_messages.assert_not_called()
        self.store.approve.assert_not_called()
        self.assertEqual(self.completions, [])

    def test_read_failure_only_updates_gui_from_main_thread(self):
        def failed_read(unused):
            self.worker_calls.append(('failed-read', threading.get_ident()))
            self.entered.set()
            self.release.wait(2)
            raise ValueError('Codex chưa trả kế hoạch')
        with patch('teodub.codex_chat.read_thread', side_effect=failed_read):
            self.receiver.start_read(ACCOUNT, PAGE)
            self.assertTrue(self.entered.wait(1))
            self.assertEqual(self.status_calls, [])
            self.finish()
        self.store.import_messages.assert_not_called()
        self.assertEqual(len(self.status_calls), 1)
        self.assertEqual(self.status_calls[0][0], self.main_thread)

    def test_wrong_account_and_digest_approval_rejected_before_staging(self):
        self.store.stage.side_effect = self.stage
        for reason in ('wrong-account', 'changed-digest'):
            with self.subTest(reason=reason):
                row = self.command('approve_content_plan')
                if reason == 'wrong-account':
                    row['payload']['account_id'] = 'f' * 32
                else:
                    row['payload']['plan_digest'] = 'f' * 64
                with self.assertRaises(ValueError):
                    self.receiver.dispatch(row)
        self.store.stage.assert_not_called()
        self.store.approve.assert_not_called()

    def test_staging_is_async_arming_calls_gui_only_after_completion(self):
        self.store.stage.side_effect = self.stage
        row = self.command('approve_content_plan')
        self.receiver.dispatch(row)
        self.assertTrue(self.entered.wait(1))
        self.assertNotEqual(self.worker_calls[0][1], self.main_thread)
        self.assertEqual(self.worker_calls[0][0], 'stage')
        self.assertEqual(self.scheduler_calls, [])
        self.store.approve.assert_not_called()
        self.finish()
        self.assertEqual(self.scheduler_calls, [self.main_thread])
        self.store.approve.assert_called_once_with(PLAN, ACCOUNT, PAGE, self.controller, expected_digest=DIGEST)
        self.assertEqual(self.completions[0][:2], (COMMAND, 'done'))

    def test_stage_result_after_expiry_disable_cancel_close_cannot_arm(self):
        for reason in ('expired', 'disabled', 'cancelled', 'closed'):
            with self.subTest(reason=reason):
                self.receiver.closed = False
                self.entered.clear()
                self.release.clear()
                self.store.stage.side_effect = self.stage
                row = self.command('approve_content_plan')
                self.receiver.dispatch(row)
                self.assertTrue(self.entered.wait(1))
                if reason == 'expired':
                    row['expires'] = time.time() - 1
                elif reason == 'disabled':
                    self.bridge.enabled = False
                elif reason == 'cancelled':
                    self.receiver.cancel(COMMAND)
                else:
                    self.receiver.close()
                self.finish()
                self.store.approve.assert_not_called()
                self.assertEqual(self.scheduler_calls, [])
                self.bridge.enabled = True

    def test_changed_page_during_stage_never_arms(self):
        self.store.stage.side_effect = self.stage
        row = self.command('approve_content_plan')
        self.receiver.dispatch(row)
        self.assertTrue(self.entered.wait(1))
        self.page['url'] = 'https://www.facebook.com/profile.php?id=54321'
        self.finish()
        self.store.approve.assert_not_called()
        self.assertEqual(self.completions[0][1], 'failed')

    def test_public_snapshot_whitelists_fields_never_full_history_paths_credentials(self):
        self.connection.update(cookie='secret-cookie', token='secret-token', profile='private-profile-path')
        self.plan.update(path='private-video-path', history='private-chat-history', token='secret-token',
            document=dict(sources={'path': 'private-video-path'}), target=TARGET)
        self.plan['items'] = [dict(id='post-1', kind='text', due_at=1792000000, body='Caption công khai',
            goal='Hữu ích', path='private-video-path', source={'token': 'secret-token'}, cookie='secret-cookie')]
        result = self.receiver.public_snapshot([self.page])
        serialized = json.dumps(result)
        for private in ('secret-cookie', 'secret-token', 'private-profile-path', 'private-video-path', 'private-chat-history'):
            self.assertNotIn(private, serialized)
        self.assertEqual(result['content_plans'][0]['items'][0]['body'], 'Caption công khai')
        self.assertNotIn('document', result['content_plans'][0])
        self.assertEqual(result['content_plans'][0]['target'],
            {key: TARGET[key] for key in ('name', 'url', 'page_id')})

    def test_auto_sync_cadence_is_sixty_seconds_and_does_not_approve(self):
        with patch.object(self.receiver, 'start_read') as reader:
            with patch('teodub.page_content_bridge.time.monotonic', return_value=100):
                self.receiver.last_sync[(ACCOUNT, PAGE)] = 50
                self.receiver.tick()
                reader.assert_not_called()
                self.receiver.last_sync[(ACCOUNT, PAGE)] = 39
                self.receiver.tick()
                reader.assert_called_once_with(ACCOUNT, PAGE)
        self.store.approve.assert_not_called()

    def test_saved_desktop_auto_sync_remains_enabled_when_web_receiver_off(self):
        from teodub.facebook_accounts import FacebookAccounts
        from teodub.page_activity import PageActivity
        from teodub.page_content_plan import ContentPlanStore
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = FacebookAccounts(root)
            account = registry.add_account('Tài khoản fixture')
            page = registry.save_page(account['id'], 'Page fixture', TARGET['url'], TARGET['page_id'])
            saved = ContentPlanStore(root)
            saved.save_connection(account['id'], page['id'], THREAD, 'Phát triển page câu cá', auto_sync=True)
            self.bridge.owner.studio.data_dir = root
            self.bridge.activity = PageActivity(root)
            self.bridge.enabled = False
            receiver = ContentPlanReceiver(self.bridge)
            self.addCleanup(receiver.close)
            with patch.object(receiver, 'start_read') as reader:
                receiver.tick()
                reader.assert_called_once_with(account['id'], page['id'])
            self.assertTrue(ContentPlanStore(root).get_connection(account['id'], page['id'])['auto_sync'])
            self.assertFalse(self.bridge.enabled)


class ContentCommandIntegrationTest(unittest.TestCase):
    def setUp(self):
        from teodub.facebook_accounts import FacebookAccounts
        from teodub.page_command_bridge import PageCommandBridge, PageCommandStore
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.desktop, self.mailbox = self.root / 'desktop', self.root / 'web' / 'page-control'
        registry = FacebookAccounts(self.desktop)
        self.account = registry.add_account('Tài khoản fixture')
        self.page = registry.save_page(self.account['id'], 'Page fixture', TARGET['url'], TARGET['page_id'])
        self.owner = SimpleNamespace(studio=SimpleNamespace(data_dir=self.desktop, closing=False, closed=False),
            destroyed=False, sessions={}, status=Mock(), reload=Mock(), get_page_scheduler=Mock())
        self.bridge = PageCommandBridge(self.owner, mailbox_root=self.mailbox)
        self.addCleanup(self.bridge.close)
        self.commands = PageCommandStore(mailbox_root=self.mailbox)
        self.content = Mock()
        self.content.get_connection.return_value = dict(thread_id=THREAD, title='Phát triển page câu cá',
                                                         auto_sync=True, last_sync='', last_error='')
        self.content.plans.return_value = []
        self.content.import_messages.return_value = dict(status='Đã nhận; chưa bật lịch')
        self.bridge.content_receiver.store = Mock(return_value=self.content)
        self.entered, self.release = threading.Event(), threading.Event()
        self.addCleanup(self.release.set)
        self.reads = 0

    def read(self, unused):
        self.reads += 1
        self.entered.set()
        self.release.wait(2)
        return dict(id=THREAD, title='Phát triển page câu cá', messages=[])

    def tick(self):
        self.bridge.last_tick = 0
        self.bridge.tick()

    def queue_sync(self):
        payload = dict(account_id=self.account['id'], page_key=self.page['id'], thread_id=THREAD,
            target={key: self.page[key] for key in TARGET})
        return self.commands.enqueue_remote('sync_content_plan', payload,
            owner_id='a' * 32, request_key='sync-integration')

    def release_read(self):
        self.release.set()
        deadline = time.monotonic() + 2
        while self.bridge.content_receiver.results.empty() and time.monotonic() < deadline:
            time.sleep(0.002)
        self.assertFalse(self.bridge.content_receiver.results.empty())
        self.tick()

    def test_queue_sync_coalesces_auto_read_and_records_done_once(self):
        queued = self.queue_sync()
        with patch('teodub.codex_chat.read_thread', side_effect=self.read):
            self.tick()
            self.assertTrue(self.entered.wait(1))
            self.assertEqual(self.commands.get(queued['id'])['state'], 'running')
            self.release_read()
        self.assertEqual(self.commands.get(queued['id'])['state'], 'done')
        self.assertEqual(self.reads, 1)
        self.content.import_messages.assert_called_once()
        self.owner.get_page_scheduler.assert_not_called()

    def test_disable_active_sync_records_failed_without_import(self):
        queued = self.queue_sync()
        with patch('teodub.codex_chat.read_thread', side_effect=self.read):
            self.tick()
            self.assertTrue(self.entered.wait(1))
            self.bridge.set_enabled(False)
            self.release_read()
        self.assertEqual(self.commands.get(queued['id'])['state'], 'failed')
        self.content.import_messages.assert_not_called()
        self.owner.get_page_scheduler.assert_not_called()

    def test_queued_approval_waits_for_auto_read_instead_of_becoming_unknown(self):
        self.content.get_plan.return_value = dict(plan_id=PLAN, digest=DIGEST)
        self.content.stage.return_value = dict(plan_id=PLAN, digest=DIGEST)
        self.content.approve.return_value = dict(plan_id=PLAN, digest=DIGEST)
        payload = dict(account_id=self.account['id'], page_key=self.page['id'], plan_id=PLAN, plan_digest=DIGEST,
            target={key: self.page[key] for key in TARGET})
        queued = self.commands.enqueue_remote('approve_content_plan', payload,
            owner_id='a' * 32, request_key='approve-waits-for-read')
        with patch('teodub.codex_chat.read_thread', side_effect=self.read):
            self.tick()
            self.assertTrue(self.entered.wait(1))
            self.assertEqual(self.commands.get(queued['id'])['state'], 'queued')
            self.content.stage.assert_not_called()
            self.release_read()
            deadline = time.monotonic() + 2
            while self.commands.get(queued['id'])['state'] in {'queued', 'running'} and time.monotonic() < deadline:
                self.tick()
                time.sleep(0.002)
        self.assertEqual(self.commands.get(queued['id'])['state'], 'done')
        self.assertEqual(self.reads, 1)
        self.content.stage.assert_called_once()
        self.content.approve.assert_called_once()

    def test_public_bridge_snapshot_keeps_connection_but_never_reads_history(self):
        self.content.get_connection.return_value.update(path='secret-profile-path', token='secret-token')
        with patch('teodub.codex_chat.read_thread', side_effect=AssertionError('No history read from snapshot')):
            self.bridge.snapshot()
            public = self.commands.public_snapshot()
        encoded = json.dumps(public)
        self.assertNotIn(str(self.desktop), encoded)
        self.assertNotIn('secret-profile-path', encoded)
        self.assertNotIn('secret-token', encoded)
        self.assertEqual(public['content_connections'][0]['thread_id'], THREAD)


if __name__ == '__main__':
    unittest.main()
