import json
import time
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
import uuid

from teodub.common import atomic_json
from teodub.facebook_accounts import FacebookAccounts
from teodub.page_activity import PageActivity
from teodub.page_command_bridge import PageCommandBridge, PageCommandStore
from teodub.page_schedule import PageScheduleStore, display_time
from tests.test_web_portal import PortalTest as _PortalFixture


# Compose the fixture without importing a discoverable TestCase alias.
_setup, _tear, _login, _post, _csrf, _submit, _fixture = (
    _PortalFixture.setUp, _PortalFixture.tearDown, _PortalFixture.login, _PortalFixture.post,
    _PortalFixture.csrf, _PortalFixture.submit, _PortalFixture.fixture)
del _PortalFixture


class WebFacebookTests(unittest.TestCase):
    login, post, csrf, submit, fixture = _login, _post, _csrf, _submit, _fixture

    def setUp(self):
        _setup(self)
        self.login('admin')
        self.desktop = self.store.root/'desktop-fixture'
        self.registry = FacebookAccounts(self.desktop)
        self.fb = self.registry.add_account('Tài khoản thử')['id']
        self.page = self.registry.save_page(self.fb, 'Page thử', 'facebook.com/100', '100')['id']
        self.activity = PageActivity(self.desktop)
        self.owner = SimpleNamespace(studio=SimpleNamespace(data_dir=self.desktop, closing=False, closed=False),
            sessions={}, destroyed=False, reload=Mock(), status=Mock(),
            open_browser=Mock(return_value=True), load_pages=Mock(return_value=True),
            submit_page_browser=Mock(side_effect=lambda *a, **k:k['request_id']))
        self.bridge = PageCommandBridge(self.owner, mailbox_root=self.store.root/'page-control')
        self.tick()

    def tearDown(self):
        self.bridge.close()
        _tear(self)

    def tick(self):
        self.bridge.last_tick = self.bridge.last_snapshot = 0
        self.bridge.tick()

    def data(self):
        response = self.client.get('/api/facebook')
        self.assertEqual(response.status_code, 200, response.json)
        return response.json

    def command(self, action, **values):
        return self.post('/api/facebook/commands', dict(action=action, account_id=self.fb,
            request_key=uuid.uuid4().hex, **values))

    def test_auth_admin_only_and_csrf_origin(self):
        anon = self.app.test_client()
        self.assertEqual(anon.get('/api/facebook').status_code, 401)
        self.assertEqual(anon.get('/facebook').status_code, 302)
        self.login('staff')
        self.assertEqual(self.client.get('/api/facebook').status_code, 403)
        self.assertEqual(self.post('/api/facebook/accounts', dict(name='x',request_key=uuid.uuid4().hex)).status_code,403)
        self.login('admin')
        self.assertEqual(self.client.post('/api/facebook/commands',json={}).status_code,403)
        self.assertEqual(self.client.post('/api/facebook/commands',json={},headers={
            'X-CSRF-Token':self.csrf(),'Origin':'https://evil.test'}).status_code,403)

    def test_snapshot_excludes_profiles_credentials_and_local_paths(self):
        self.activity.save_draft(self.fb,self.page,'Bài chữ',delivery_mode='browser',browser_finish_mode='tool')
        self.tick()
        data = self.data()
        self.assertEqual(data['accounts'][0]['pages'][0]['name'],'Page thử')
        self.assertTrue(data['bridge']['online'])
        encoded = json.dumps(data)
        for forbidden in ('browser-profiles','credential_ref','cookies',str(self.desktop), 'secret'):
            self.assertNotIn(forbidden,encoded)
        self.assertEqual(len(data['posts']),1)

    def test_web_queues_browser_request_without_executing_browser(self):
        response = self.command('open_account')
        self.assertEqual(response.status_code,202,response.json)
        self.owner.open_browser.assert_not_called()
        self.tick()
        self.owner.open_browser.assert_called_once()
        kwargs = self.owner.open_browser.call_args.kwargs
        self.assertEqual(kwargs['account_id'],self.fb)
        self.assertEqual(kwargs['request_id'],response.json['command']['id'])

    def test_offline_or_disabled_rejects_new_commands_preserves_history(self):
        response = self.command('open_account')
        command_id = response.json['command']['id']
        self.bridge.set_enabled(False)
        self.assertEqual(self.command('open_account').status_code,409)
        self.assertEqual(self.data()['commands'][0]['id'],command_id)
        self.bridge.set_enabled(True)
        atomic_json(self.bridge.store.directory/'bridge.json',dict(updated_at=0,enabled=True))
        self.assertEqual(self.command('load_pages').status_code,409)

    def test_account_creation_and_text_draft_execute_only_on_desktop(self):
        response = self.post('/api/facebook/accounts',dict(name='Tài khoản hai',request_key=uuid.uuid4().hex))
        self.assertEqual(response.status_code,202,response.json)
        self.assertEqual(len(self.registry.load()['accounts']),1)
        self.tick()
        self.assertEqual(len(self.registry.load()['accounts']),2)
        response = self.post('/api/facebook/drafts',dict(account_id=self.fb,page_key=self.page,
            body='Caption Việt\n#cauca',request_key=uuid.uuid4().hex))
        self.assertEqual(response.status_code,202,response.json)
        self.assertEqual(self.activity.posts(self.fb,self.page),[])
        self.tick()
        post = self.activity.posts(self.fb,self.page)[0]
        self.assertEqual((post['body'],post['delivery_mode'],post['browser_finish_mode']),
                         ('Caption Việt\n#cauca','browser','tool'))
        self.assertEqual(post['state'],'draft')
        self.owner.submit_page_browser.assert_not_called()

    def test_video_draft_only_completed_own_verified_output_no_paths_in_public_response(self):
        self.login('staff')
        staff_job = self.submit().json['id']
        self.fixture(staff_job)
        self.login('admin')
        job = self.submit().json['id']
        _,output = self.fixture(job)
        payload = dict(account_id=self.fb,page_key=self.page,body='',job_id=staff_job,request_key=uuid.uuid4().hex)
        self.assertEqual(self.post('/api/facebook/drafts',payload).status_code,409)
        payload.update(job_id=job,request_key=uuid.uuid4().hex)
        response = self.post('/api/facebook/drafts',payload)
        self.assertEqual(response.status_code,202,response.json)
        self.assertNotIn(output['video'],json.dumps(response.json))
        row = self.bridge.store.get(response.json['command']['id'])
        self.assertEqual(row['payload']['asset']['path'],output['video'])
        Path(output['video']).write_bytes(b'tampered')
        payload['request_key']=uuid.uuid4().hex
        self.assertEqual(self.post('/api/facebook/drafts',payload).status_code,409)

    def test_target_and_post_snapshot_changed_before_dispatch_never_sends(self):
        post = self.activity.save_draft(self.fb,self.page,'Trước',delivery_mode='browser',browser_finish_mode='tool')
        self.tick()
        response=self.command('prepare_post',page_key=self.page,post_id=post['id'])
        self.assertEqual(response.status_code,202,response.json)
        self.activity.save_draft(self.fb,self.page,'Sau',post_id=post['id'])
        self.tick()
        self.owner.submit_page_browser.assert_not_called()
        row=self.bridge.store.get(response.json['command']['id'])
        self.assertIn(row['state'],{'failed','unknown'})

    def test_publish_requires_exact_concrete_review_key(self):
        post = self.activity.save_draft(self.fb,self.page,'Đã duyệt',delivery_mode='browser',browser_finish_mode='tool')
        self.tick()
        self.assertEqual(self.command('publish_post',page_key=self.page,post_id=post['id'],review_key='a'*64).status_code,409)
        self.activity.transition(self.fb,self.page,post['id'],'preparing','Fixture')
        review=dict(account_id=self.fb,page_key=self.page,post_id=post['id'],target_name='Page thử',
            target_url='https://www.facebook.com/100',target_id='100',body='Đã duyệt',kind='text',media=[],browser_finish_mode='tool')
        self.activity.save_browser_review(self.fb,self.page,post['id'],review)
        self.tick()
        key=self.data()['posts'][0]['review_key']
        self.assertEqual(self.command('publish_post',page_key=self.page,post_id=post['id'],review_key='f'*64).status_code,409)
        response=self.command('publish_post',page_key=self.page,post_id=post['id'],review_key=key)
        self.assertEqual(response.status_code,202,response.json)
        self.owner.submit_page_browser.assert_not_called()
        self.tick()
        self.owner.submit_page_browser.assert_called_once_with(self.fb,self.page,post['id'],'publish',
            request_id=response.json['command']['id'])

    def test_idempotency_prevents_duplicate_requests_and_cross_scope(self):
        payload=dict(action='open_account',account_id=self.fb,request_key=uuid.uuid4().hex)
        first=self.post('/api/facebook/commands',payload)
        second=self.post('/api/facebook/commands',payload)
        self.assertEqual(first.json['command']['id'],second.json['command']['id'])
        payload['action']='load_pages'
        self.assertEqual(self.post('/api/facebook/commands',payload).status_code,409)
        other=self.registry.add_account('Khác')['id']
        self.tick()
        self.assertEqual(self.post('/api/facebook/commands',dict(action='open_page',account_id=other,
            page_key=self.page,request_key=uuid.uuid4().hex)).status_code,409)
        self.assertEqual(self.command('open_account',path='C:/secret',password='not-accepted').status_code,409)

    def test_page_ui_visible_without_api_or_facebook_password_form(self):
        response=self.client.get('/facebook')
        self.assertEqual(response.status_code,200)
        html=response.get_data(as_text=True)
        self.assertIn('fb-confirm-review',html)
        self.assertIn('fb-load-pages',html)
        self.assertNotIn('type="password"',html)
        self.assertIn('/facebook',self.client.get('/').get_data(as_text=True))

    def test_text_schedule_is_frozen_and_created_only_on_desktop(self):
        post = self.activity.save_draft(self.fb,self.page,'Bài chữ hẹn giờ\n#cauca',
            delivery_mode='browser',browser_finish_mode='tool')
        schedules = PageScheduleStore(self.desktop)
        scheduler = SimpleNamespace(store=schedules,start=Mock())
        self.owner.get_page_scheduler = lambda:scheduler
        self.tick()
        due = display_time(time.time()+3600)
        response = self.command('schedule_post',page_key=self.page,post_id=post['id'],due_at=due)
        self.assertEqual(response.status_code,202,response.json)
        self.assertEqual(schedules.list(),[])
        scheduler.start.assert_not_called()
        self.tick()
        row = schedules.list()[0]
        self.assertEqual(row['body'],post['body'])
        self.assertEqual(row['source']['kind'],'text')
        self.assertEqual(row['snapshot']['target_id'],'100')
        scheduler.start.assert_called_once_with(row['id'])
        self.owner.submit_page_browser.assert_not_called()
        exported = self.data()['schedules'][0]
        self.assertEqual(exported['post_id'],post['id'])
        self.assertNotIn('snapshot',exported)
        self.assertNotIn('source',exported)
        self.assertNotIn(str(self.desktop),json.dumps(exported))
        self.assertEqual(self.command('schedule_post',page_key=self.page,post_id=post['id'],due_at='bad').status_code,409)
