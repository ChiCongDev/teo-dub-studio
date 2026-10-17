import json
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from teodub.common import hash_file
from teodub.facebook_accounts import FacebookAccounts
from teodub.page_activity import PageActivity
from teodub.page_command_bridge import PageCommandBridge, PageCommandStore, _digest, _post_binding, public_post


class BridgeTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.desktop = self.root/'desktop'
        self.mailbox = self.root/'web'/'page-control'
        self.registry = FacebookAccounts(self.desktop)
        self.account = self.registry.add_account('Tài khoản thử')
        self.other = self.registry.add_account('Tài khoản khác')
        self.page = self.registry.save_page(self.account['id'],'Page thử','https://www.facebook.com/profile.php?id=12345','12345')
        self.activity = PageActivity(self.desktop)
        self.post = self.activity.save_draft(self.account['id'],self.page['id'],'Nội dung đã duyệt',
            delivery_mode='browser',browser_finish_mode='tool')
        self.owner = SimpleNamespace(studio=SimpleNamespace(data_dir=self.desktop,closing=False,closed=False),
            destroyed=False,sessions={},status=Mock(),reload=Mock())
        self.owner.status.get.return_value = 'Chưa nhận được yêu cầu.'
        self.owner.open_browser = self.open_browser
        self.owner.load_pages = self.load_pages
        self.owner.submit_page_browser = self.post_browser
        self.calls = []
        self.bridge = PageCommandBridge(self.owner,mailbox_root=self.mailbox)
        self.addCleanup(self.bridge.close)
        self.store = PageCommandStore(mailbox_root=self.mailbox)
        self.user = 'a'*32

    def session(self, account):
        if account not in self.owner.sessions:
            self.owner.sessions[account] = SimpleNamespace(pending=True,opened=True,loading_pages=False,
                navigation={},page_load_requests={},post_actions={})
        return self.owner.sessions[account]

    def open_browser(self, url, *, account_id, request_id):
        self.calls.append(('open',account_id,url,request_id))
        self.session(account_id).navigation[request_id] = dict(state='queued',message='Đã nhận')
        return True

    def load_pages(self, account_id, *, request_id):
        self.calls.append(('load',account_id,request_id))
        self.session(account_id).page_load_requests[request_id] = dict(state='queued',message='Đã nhận')
        return True

    def post_browser(self, account_id,page_key,post_id,operation, *, request_id):
        self.calls.append(('post',account_id,page_key,post_id,operation,request_id))
        self.session(account_id).post_actions[request_id] = dict(state='queued',message='Đã nhận',cancel=threading.Event())
        return request_id

    def tick(self):
        self.bridge.last_tick = 0
        self.bridge.last_snapshot = 0
        self.bridge.tick()

    def payload(self, action):
        data = dict(account_id=self.account['id'])
        if action not in {'open_account','load_pages'}:
            data.update(page_key=self.page['id'],target={key:self.page[key] for key in ('name','url','page_id')})
        if action.endswith('_post'):
            post = self.activity.post(self.account['id'],self.page['id'],self.post['id'])
            data.update(post_id=post['id'],post_key=_digest(_post_binding(post)))
            if action == 'publish_post':
                data['review_key'] = _digest(post['browser_review'])
        return data

    def enqueue(self, action, payload=None, key='test-key'):
        return self.store.enqueue_remote(action,payload or self.payload(action),owner_id=self.user,request_key=key)

    def reviewed(self):
        account,page,post = self.account['id'],self.page['id'],self.post['id']
        self.activity.transition(account,page,post,'preparing','fixture',expected={'draft'})
        review = dict(version=1,account_id=account,page_key=page,post_id=post,
            target_name=self.page['name'],target_url=self.page['url'],target_id=self.page['page_id'],
            body=self.post['body'],kind='text',media=[],browser_finish_mode='tool',
            identity=dict(page_id=self.page['page_id'],name=self.page['name'],url=self.page['url']))
        return self.activity.save_browser_review(account,page,post,review)

    def test_remote_store_never_opens_desktop_registry_or_browser(self):
        with patch('teodub.page_command_bridge.FacebookAccounts',side_effect=AssertionError('No registry read')):
            remote = PageCommandStore(mailbox_root=self.root/'isolated')
            row = remote.enqueue_remote('open_account',dict(account_id=self.account['id']),owner_id=self.user,request_key='first')
            self.assertEqual(row['state'],'queued')
            self.assertFalse(remote.status()['online'])
            self.assertEqual(remote.public_snapshot()['accounts'],[])
        self.assertEqual(self.calls,[])

    def test_stable_request_is_idempotent_and_different_payload_rejected(self):
        first = self.enqueue('open_account')
        self.assertEqual(self.enqueue('open_account')['id'],first['id'])
        with self.assertRaises(ValueError):
            self.enqueue('load_pages')
        other = self.store.enqueue_remote('open_account',self.payload('open_account'),owner_id='b'*32,request_key='test-key')
        self.assertNotEqual(other['id'],first['id'])
        self.assertEqual(first['created_at'],first['created'])

    def test_shape_scope_and_lifetime_reject_untrusted_commands(self):
        for action, payload in [('bad',{}),('open_account',{'account_id':'no'}),
                ('open_account',dict(account_id=self.account['id'],url='https://evil.test')),
                ('open_page',dict(account_id=self.account['id'],page_key=self.page['id'],target=dict(name='p',url='https://evil.test',page_id='1')))]:
            with self.assertRaises(ValueError):
                self.enqueue(action,payload,key='bad-'+action)
        for ttl in (0,601,float('nan'),True):
            with self.assertRaises(ValueError):
                self.store.enqueue_remote('open_account',self.payload('open_account'),owner_id=self.user,request_key='ttl',ttl=ttl)
        self.assertEqual(self.store.list(),[])

    def test_desktop_routes_explicit_account_without_reloading_and_tracks_ack(self):
        row = self.enqueue('open_account')
        self.tick()
        self.assertEqual(self.store.get(row['id'])['state'],'running')
        self.assertEqual(self.calls[0][1],self.account['id'])
        self.assertEqual(self.calls[0][-1],row['id'])
        self.owner.sessions[self.account['id']].navigation[row['id']].update(state='opened',message='Chrome đã mở')
        self.tick()
        self.assertEqual(self.store.get(row['id'])['state'],'done')
        self.assertEqual(len(self.calls),1)
        self.owner.reload.assert_not_called()

    def test_load_uses_request_scoped_reply_and_cannot_consume_other_reply(self):
        row = self.enqueue('load_pages')
        self.tick()
        session = self.owner.sessions[self.account['id']]
        session.page_load_requests['old'] = dict(state='done',message='old result')
        self.tick()
        self.assertEqual(self.store.get(row['id'])['state'],'running')
        session.page_load_requests[row['id']].update(state='done',message='Đã đọc Page',result=dict(added=2))
        self.tick()
        self.assertEqual(self.store.get(row['id'])['result'],dict(added=2))

    def test_changed_page_or_wrong_account_fails_before_dispatch(self):
        row = self.enqueue('open_page')
        self.registry.save_page(self.account['id'],'Đã đổi','https://www.facebook.com/profile.php?id=12345','12345',record_id=self.page['id'])
        self.tick()
        self.assertEqual(self.store.get(row['id'])['state'],'failed')
        self.assertEqual(self.calls,[])
        payload = self.payload('prepare_post')
        payload['account_id'] = self.other['id']
        row = self.enqueue('prepare_post',payload,key='wrong-account')
        self.tick()
        self.assertEqual(self.store.get(row['id'])['state'],'failed')
        self.assertEqual(self.calls,[])

    def test_post_changes_and_archiving_block_queued_prepare(self):
        row = self.enqueue('prepare_post')
        self.activity.save_draft(self.account['id'],self.page['id'],'Nội dung mới',post_id=self.post['id'])
        self.tick()
        self.assertEqual(self.store.get(row['id'])['state'],'failed')
        self.assertEqual(self.calls,[])

    def test_publish_requires_actual_immutable_review_and_tool_mode(self):
        row = self.enqueue('publish_post')
        self.tick()
        self.assertEqual(self.store.get(row['id'])['state'],'failed')
        self.assertEqual(self.calls,[])
        post = self.reviewed()
        local = PageCommandStore(self.desktop,mailbox_root=self.mailbox)
        with self.assertRaises(ValueError):
            local.enqueue('publish_post',self.account['id'],page_key=self.page['id'],post_id=post['id'],expected_review={'body':'wrong'},idempotency_key='wrong')
        accepted = local.enqueue('publish_post',self.account['id'],page_key=self.page['id'],post_id=post['id'],expected_review=post['browser_review'],idempotency_key='actual')
        self.tick()
        self.assertEqual(self.calls[-1][-2],'publish')
        self.assertEqual(self.store.get(accepted['id'])['state'],'running')

    def test_review_changed_after_web_approval_blocks_publish(self):
        self.reviewed()
        row = self.enqueue('publish_post')
        with self.activity.database() as conn:
            conn.execute("UPDATE posts SET browser_review=? WHERE id=?",(json.dumps({'body':'tampered'}),self.post['id']))
        self.tick()
        self.assertEqual(self.store.get(row['id'])['state'],'failed')
        self.assertEqual(self.calls,[])

    def test_queued_or_unknown_post_cannot_be_sent_with_new_request_key(self):
        row = self.enqueue('prepare_post')
        with self.assertRaises(ValueError):
            self.enqueue('prepare_post',key='duplicate')
        self.tick()
        self.bridge.close()
        self.assertEqual(self.store.get(row['id'])['state'],'unknown')
        with self.assertRaises(ValueError):
            self.enqueue('prepare_post',key='new-after-interruption')
        self.assertEqual(self.enqueue('prepare_post')['id'],row['id'])
        self.assertEqual(len(self.calls),1)

    def test_lost_executor_running_command_is_unknown_never_requeued(self):
        row = self.enqueue('open_account')
        self.store.claim(row['id'],'dead-executor')
        self.tick()
        self.assertEqual(self.store.get(row['id'])['state'],'unknown')
        self.assertEqual(self.calls,[])

    def test_executor_os_lock_prevents_second_window_recovering_live_command(self):
        row = self.enqueue('open_account')
        self.tick()
        second = PageCommandBridge(self.owner,mailbox_root=self.mailbox)
        self.addCleanup(second.close)
        second.tick()
        self.assertIsNone(second.guard)
        self.assertEqual(self.store.get(row['id'])['state'],'running')
        self.assertEqual(len(self.calls),1)

    def test_expired_queue_and_disabled_preferences_never_dispatch(self):
        row = self.enqueue('open_account')
        self.bridge.set_enabled(False)
        self.tick()
        self.assertEqual(self.calls,[])
        restored = PageCommandBridge(self.owner,mailbox_root=self.mailbox)
        self.assertFalse(restored.enabled)
        restored.close()
        with self.store.database() as conn:
            conn.execute('UPDATE commands SET expires=? WHERE id=?',(time.time()-1,row['id']))
        self.bridge.set_enabled(True)
        self.tick()
        self.assertEqual(self.store.get(row['id'])['state'],'failed')
        self.assertEqual(self.calls,[])

    def test_timeout_cancels_post_and_late_ack_never_overwrites_unknown(self):
        row = self.enqueue('prepare_post')
        self.tick()
        self.bridge.active['expires'] = 0
        session = self.owner.sessions[self.account['id']]
        request = session.post_actions[row['id']]
        self.tick()
        self.assertTrue(request['cancel'].is_set())
        self.assertEqual(self.store.get(row['id'])['state'],'unknown')
        request.update(state='done',result=dict(id=self.post['id'],account_id=self.account['id'],page_key=self.page['id']))
        self.tick()
        self.assertEqual(self.store.get(row['id'])['state'],'unknown')

    def test_prepare_ack_requires_scope_and_publish_requires_durable_receipt(self):
        row = self.enqueue('prepare_post')
        self.tick()
        post = self.reviewed()
        session = self.owner.sessions[self.account['id']]
        session.post_actions[row['id']].update(state='done',result=dict(post,account_id=self.other['id']))
        self.tick()
        self.assertEqual(self.store.get(row['id'])['state'],'unknown')
        # A separate already-reviewed post avoids the ambiguity barrier of the first.
        self.post = self.activity.save_draft(self.account['id'],self.page['id'],'Second',delivery_mode='browser',browser_finish_mode='tool')
        post = self.reviewed()
        row = self.enqueue('publish_post',key='publish-second')
        self.tick()
        self.activity.transition(post['account_id'],post['page_key'],post['id'],'published','claimed',expected={'review'},external_url='https://www.facebook.com/12345/posts/999')
        result = self.activity.post(post['account_id'],post['page_key'],post['id'])
        session.post_actions[row['id']].update(state='done',result=result)
        self.tick()
        self.assertEqual(self.store.get(row['id'])['state'],'unknown')

    def test_sanitized_snapshot_has_no_profile_paths_or_media_parent_paths(self):
        self.reviewed()
        self.tick()
        snapshot = self.store.public_snapshot()
        self.assertEqual(len(snapshot['accounts']),2)
        self.assertEqual(snapshot['posts'][0]['body'],self.post['body'])
        self.assertEqual(len(snapshot['posts'][0]['review_key']),64)
        encoded = json.dumps(snapshot)
        self.assertNotIn(str(self.desktop),encoded)
        self.assertNotIn('browser-profiles',encoded)
        self.assertTrue(self.store.status()['online'])
        self.bridge.close()
        self.assertFalse(self.store.status()['online'])

    def test_remote_create_account_page_and_text_draft_are_local_only(self):
        row = self.enqueue('add_account',dict(name='Web account'))
        self.tick()
        account = self.store.get(row['id'])['result']['account_id']
        row = self.enqueue('save_page',dict(account_id=account,name='Web Page',url='https://www.facebook.com/98765',page_id='98765'),key='web-page')
        self.tick()
        page = self.store.get(row['id'])['result']
        row = self.enqueue('save_draft',dict(account_id=account,page_key=page['page_key'],
            target={k:page[k] for k in ('name','url','page_id')},body='Bài từ web',asset=None),key='web-draft')
        self.tick()
        result = self.store.get(row['id'])['result']
        self.assertEqual(result['state'],'draft')
        self.assertEqual(result['post_id'],result['id'])
        self.assertEqual(result['delivery_mode'],'browser')
        self.assertEqual(self.calls,[])

    def test_web_asset_requires_output_root_stat_hash_and_only_reads_file(self):
        job = 'c'*32
        output = self.mailbox.parent/'outputs'/job/'video.mp4'
        output.parent.mkdir(parents=True)
        output.write_bytes(b'synthetic video')
        stat = output.stat()
        asset = dict(job_id=job,path=str(output),sha256=hash_file(output),size=stat.st_size,mtime_ns=stat.st_mtime_ns)
        self.assertEqual(self.bridge.asset(asset),[str(output)])
        external = self.root/'outside.mp4'
        external.write_bytes(output.read_bytes())
        with self.assertRaises(ValueError):
            self.bridge.asset(dict(asset,path=str(external)))
        output.write_bytes(b'changed content')
        with self.assertRaises(ValueError):
            self.bridge.asset(asset)
        self.assertEqual(self.calls,[])

    def test_web_video_hash_runs_off_tk_then_draft_remains_unpublished(self):
        job = 'd'*32
        output = self.mailbox.parent/'outputs'/job/'video.mp4'
        output.parent.mkdir(parents=True)
        output.write_bytes(b'fixture')
        stat = output.stat()
        asset = dict(job_id=job,path=str(output),sha256=hash_file(output),size=stat.st_size,mtime_ns=stat.st_mtime_ns)
        payload = self.payload('open_page')
        payload.update(body='Video thử',asset=asset)
        row = self.enqueue('save_draft',payload)
        with patch('teodub.page_command_bridge.threading.Thread') as worker:
            self.tick()
            self.assertEqual(self.store.get(row['id'])['state'],'running')
            worker.return_value.start.assert_called_once()
        self.bridge.check_asset(self.bridge.active)
        self.tick()
        result = self.store.get(row['id'])['result']
        self.assertEqual(result['state'],'draft')
        self.assertEqual(result['media'][0]['name'],'video.mp4')
        self.assertNotIn('path',result['media'][0])
        self.assertEqual(self.calls,[])

    def test_cancel_only_queued_and_never_resends(self):
        row = self.enqueue('open_account')
        self.store.cancel_queued(row['id'])
        self.tick()
        self.assertEqual(self.calls,[])
        with self.assertRaises(ValueError):
            self.store.cancel_queued(row['id'])

    def test_text_schedule_uses_frozen_post_without_publishing_and_exports_status(self):
        payload = self.payload('prepare_post')
        payload['due_at'] = time.time()+300
        row = self.enqueue('schedule_post',payload)
        schedule = dict(id='e'*32,account_id=self.account['id'],page_key=self.page['id'],post_id=self.post['id'],
            state='armed',due_at=payload['due_at'],body=self.post['body'],message='Đang chờ',source={'kind':'text','path':'never public'})
        scheduler = Mock()
        self.owner.get_page_scheduler = Mock(return_value=scheduler)
        with patch('teodub.page_schedule.PageScheduleStore') as schedules:
            schedules.return_value.create_post.return_value = schedule
            schedules.return_value.list.return_value = [schedule]
            self.tick()
            schedules.return_value.create_post.assert_called_once_with(self.account['id'],self.page['id'],self.post['id'],
                payload['due_at'],expected_target=payload['target'],expected_post_key=payload['post_key'])
        self.assertEqual(self.store.get(row['id'])['result']['schedule_id'],schedule['id'])
        scheduler.start.assert_called_once_with(schedule['id'])
        self.assertEqual(self.calls,[])
        self.assertNotIn('source',self.store.public_snapshot()['schedules'][0])

    def test_cancellation_reaches_navigation_and_page_load_requests(self):
        for action, table in [('open_account','navigation'),('load_pages','page_load_requests')]:
            row = self.enqueue(action,key=action)
            self.tick()
            session = self.owner.sessions[self.account['id']]
            request = getattr(session,table)[row['id']]
            request['cancel'] = threading.Event()
            self.bridge.active['expires'] = 0
            self.tick()
            self.assertTrue(request['cancel'].is_set())
            self.assertEqual(self.store.get(row['id'])['state'],'unknown')

    def test_disable_from_another_window_stops_executor_and_status_immediately(self):
        row = self.enqueue('prepare_post')
        self.tick()
        request = self.owner.sessions[self.account['id']].post_actions[row['id']]
        other = PageCommandBridge(self.owner,mailbox_root=self.mailbox)
        self.addCleanup(other.close)
        other.set_enabled(False)
        self.assertFalse(self.store.status()['online'])
        self.tick()
        self.assertFalse(self.bridge.enabled)
        self.assertTrue(request['cancel'].is_set())
        new = self.enqueue('open_account',key='queued-while-disabled')
        request.update(state='error',message='Đã dừng')
        self.tick()
        self.assertEqual(self.store.get(row['id'])['state'],'failed')
        self.assertEqual(self.store.get(new['id'])['state'],'queued')
        self.assertEqual(len(self.calls),1)

    def test_malformed_persisted_control_preference_is_disabled(self):
        self.tick()
        (self.mailbox/'bridge-settings.json').write_text('{broken',encoding='utf-8')
        self.assertFalse(self.store.status()['online'])
        self.tick()
        self.assertFalse(self.bridge.enabled)
        self.assertEqual(self.calls,[])

    def test_exact_schedule_retry_after_due_returns_original_without_new_attempt(self):
        payload = self.payload('prepare_post')
        payload['due_at'] = time.time()+300
        first = self.enqueue('schedule_post',payload)
        with patch('teodub.page_command_bridge.time.time',return_value=payload['due_at']+120):
            repeated = self.enqueue('schedule_post',payload)
        self.assertEqual(repeated['id'],first['id'])
        self.assertEqual(len(self.store.list()),1)
        self.assertEqual(self.calls,[])

    def test_saved_or_active_text_schedule_rejects_competing_prepare_and_publish(self):
        from teodub.page_schedule import PageScheduleStore
        schedules = PageScheduleStore(self.desktop)
        saved = schedules.create_post(self.account['id'],self.page['id'],self.post['id'],time.time()+300,
                                      expected_post_key=public_post(self.post)['post_key'])
        for state in ('scheduled','armed','paused','preparing','submitting','verifying','needs_attention'):
            schedules.update(saved['id'],state=state)
            row = self.enqueue('prepare_post',key=state)
            self.tick()
            self.assertEqual(self.store.get(row['id'])['state'],'failed')
        self.assertEqual(self.calls,[])
        self.reviewed()
        row = self.enqueue('publish_post',key='competing-publish')
        self.tick()
        self.assertEqual(self.store.get(row['id'])['state'],'failed')
        self.assertEqual(self.calls,[])

    def test_clean_preparation_error_allows_only_new_explicit_retry(self):
        row = self.enqueue('prepare_post')
        self.tick()
        session = self.owner.sessions[self.account['id']]
        session.post_actions[row['id']].update(state='error',message='Chưa đăng nhập; chưa mở hộp bài.')
        self.tick()
        self.assertEqual(self.store.get(row['id'])['state'],'failed')
        self.assertEqual(len(self.calls),1)
        # A refresh/idempotency retry is not a new request and must not resend.
        self.assertEqual(self.enqueue('prepare_post')['id'],row['id'])
        self.tick()
        self.assertEqual(len(self.calls),1)
        fresh = self.enqueue('prepare_post',key='explicit-new-request-after-login')
        self.tick()
        self.assertEqual(self.store.get(fresh['id'])['state'],'running')
        self.assertEqual(len(self.calls),2)

    def test_post_error_after_possible_editor_or_upload_change_remains_unknown(self):
        row = self.enqueue('prepare_post')
        self.tick()
        self.activity.transition(self.account['id'],self.page['id'],self.post['id'],'unknown','Đã thay nội dung, bị gián đoạn',expected={'draft'})
        self.owner.sessions[self.account['id']].post_actions[row['id']].update(state='error',message='Đã dừng sau upload')
        self.tick()
        self.assertEqual(self.store.get(row['id'])['state'],'unknown')
        with self.assertRaises(ValueError):
            self.enqueue('prepare_post',key='try-again')

    def test_saved_tracking_prevents_error_being_treated_as_clean_retry(self):
        row = self.enqueue('prepare_post')
        self.tick()
        with self.activity.database() as conn:
            conn.execute('UPDATE posts SET browser_tracking=? WHERE id=?',
                ('{"submitted_at":1792033200}',self.post['id']))
        self.owner.sessions[self.account['id']].post_actions[row['id']].update(state='error',
            message='Synthetic inconsistent draft with saved submission')
        self.tick()
        self.assertEqual(self.store.get(row['id'])['state'],'unknown')
        with self.assertRaises(ValueError):
            self.enqueue('prepare_post',key='unsafe-new-key')

    def test_text_schedule_automatic_dispatch_bypasses_manual_bridge_conflict_guard(self):
        from teodub.page_schedule import PageScheduleController
        scheduler = PageScheduleController(self.owner)
        self.addCleanup(scheduler.close)
        self.owner.get_page_scheduler = Mock(return_value=scheduler)
        payload = self.payload('prepare_post')
        payload['due_at'] = time.time()+300
        row = self.enqueue('schedule_post',payload)
        self.tick()
        ident = self.store.get(row['id'])['result']['schedule_id']
        scheduler.store.update(ident,due_at=time.time()-1)
        def launch_validation(*,target,args,daemon):
            worker = Mock()
            worker.start.side_effect = lambda: target(*args)
            return worker
        with patch('teodub.page_schedule.threading.Thread',side_effect=launch_validation):
            scheduler.tick()
            scheduler.last_tick = 0
            scheduler.tick()
        self.assertEqual(self.calls[-1][-2],'prepare')
        self.assertEqual(scheduler.store.get(ident)['phase'],'prepare')
        self.assertEqual(self.store.get(row['id'])['state'],'done')

    def test_own_window_disable_while_hashing_cannot_save_late_draft(self):
        job = 'f'*32
        output = self.mailbox.parent/'outputs'/job/'video.mp4'
        output.parent.mkdir(parents=True)
        output.write_bytes(b'fixture')
        stat = output.stat()
        asset = dict(job_id=job,path=str(output),sha256=hash_file(output),size=stat.st_size,mtime_ns=stat.st_mtime_ns)
        payload = self.payload('open_page')
        payload.update(body='Không được lưu sau khi tắt',asset=asset)
        row = self.enqueue('save_draft',payload)
        with patch('teodub.page_command_bridge.threading.Thread') as worker:
            self.tick()
            worker.return_value.start.assert_called_once()
        active = self.bridge.active
        self.bridge.set_enabled(False)
        self.assertIsNone(self.bridge.active)
        self.assertEqual(self.store.get(row['id'])['state'],'failed')
        self.bridge.check_asset(active)  # A late read-only worker result is harmless.
        self.tick()
        self.assertEqual(len(self.activity.posts(self.account['id'],self.page['id'])),1)
        self.assertEqual(self.store.get(row['id'])['state'],'failed')
        self.assertEqual(self.calls,[])

    def test_public_snapshot_exports_text_and_video_schedules_without_sources(self):
        common = dict(account_id=self.account['id'],page_key=self.page['id'],post_id=self.post['id'],
            state='armed',due_at=time.time()+300,body='caption',message='Đang chờ')
        text = dict(common,id='1'*32,source={'kind':'text','key':'post:'+self.post['id']})
        video = dict(common,id='2'*32,source={'campaign':'private-directory','path':'private-video.mp4'})
        with patch('teodub.page_schedule.PageScheduleStore') as schedules:
            schedules.return_value.list.return_value = [video,text]
            self.tick()
        exported = self.store.public_snapshot()['schedules']
        self.assertEqual([row['id'] for row in exported],[video['id'],text['id']])
        self.assertTrue(all('source' not in row for row in exported))
        self.assertNotIn('private-directory',json.dumps(exported))
        self.assertNotIn('private-video.mp4',json.dumps(exported))


class BrowserRequestAcknowledgementTest(unittest.TestCase):
    def setUp(self):
        # Reuse the established fake-runtime fixture, not its test methods.
        try:
            from tests import test_facebook_browser as fixtures
        except ImportError:
            import test_facebook_browser as fixtures
        self.fixture = fixtures.FacebookBrowserTests(methodName='runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_load_ack_echoes_only_current_request_and_preserves_legacy_shape(self):
        f = self.fixture
        request = 'a'*32
        f.commands.put(dict(action='load_pages',request_id=request))
        result = dict(items=[],status='partial',message='fixture')
        with patch('teodub.facebook_page_discovery.read_managed_pages',return_value=result):
            self.assertEqual(f.browse(),'finished')
        loaded = [value for kind,value in f.messages if kind=='pages_loaded']
        self.assertEqual(loaded,[dict(result,request_id=request)])
        self.assertTrue(all(value['request_id']==request for kind,value in f.messages if kind=='pages_progress'))
        f.context.cookies.assert_not_called()

    def test_expired_or_cancelled_load_does_not_call_reader(self):
        f = self.fixture
        stop = threading.Event()
        stop.set()
        f.commands.put(dict(action='load_pages',request_id='b'*32,cancel_request=stop,expires_at=time.monotonic()+300))
        with patch('teodub.facebook_page_discovery.read_managed_pages') as read:
            self.assertEqual(f.browse(),'finished')
            read.assert_not_called()
        errors = [value for kind,value in f.messages if kind=='pages_error']
        self.assertEqual(errors[0]['request_id'],'b'*32)

    def test_expired_navigation_does_not_create_or_navigate_queued_tab(self):
        f = self.fixture
        f.commands.put(dict(action='open_url',url='https://www.facebook.com/12345',request_id='c'*32,expires_at=time.monotonic()-1))
        self.assertEqual(f.browse(),'finished')
        f.context.new_page.assert_not_called()
        events = [value for kind,value in f.messages if kind=='navigation']
        self.assertEqual([(row['request_id'],row['state']) for row in events],[('c'*32,'error')])


if __name__ == '__main__':
    unittest.main()
