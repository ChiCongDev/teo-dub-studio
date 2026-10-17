"""Real HTTP/mailbox/scheduler wiring, with temporary data and no Facebook/AI."""
from datetime import datetime, timedelta, timezone
import json
import time
import unittest
from unittest.mock import Mock, patch
import uuid

from tests import test_web_facebook_control as fb_fixture
from teodub.page_content_plan import ContentPlanStore
from teodub.page_schedule import PageScheduleController, PageScheduleStore


class WebPageContentTests(unittest.TestCase):
    login, post, csrf, submit, fixture = (fb_fixture.WebFacebookTests.login,
        fb_fixture.WebFacebookTests.post, fb_fixture.WebFacebookTests.csrf,
        fb_fixture.WebFacebookTests.submit, fb_fixture.WebFacebookTests.fixture)
    tick, data, command = (fb_fixture.WebFacebookTests.tick,
        fb_fixture.WebFacebookTests.data, fb_fixture.WebFacebookTests.command)

    def setUp(self):
        fb_fixture.WebFacebookTests.setUp(self)
        self.content = ContentPlanStore(self.desktop)
        self.thread = str(uuid.uuid4())
        self.content.save_connection(self.fb, self.page, self.thread, 'Phát triển page câu cá')
        self.scheduler = PageScheduleController(self.owner)
        self.owner.get_page_scheduler = lambda:self.scheduler
        self.tick()

    def tearDown(self):
        self.scheduler.close()
        fb_fixture.WebFacebookTests.tearDown(self)

    def snapshot(self):
        due = datetime.now(timezone(timedelta(hours=7)))+timedelta(days=1)
        document = dict(format='teo-page-plan', version=1, plan_id=str(uuid.uuid4()),
            title='Kế hoạch thử', page_id='100', time_zone='Asia/Bangkok',
            items=[dict(id='muc_01',kind='text',due_at=due.isoformat(timespec='seconds'),
                        body='Mẹo buộc lưỡi cho người mới.\n#cauca',video_key='',goal='Chia sẻ kỹ năng')])
        return dict(id=self.thread, messages=[dict(turn_id='completed',phase='final_answer',
            turn_status='completed',text='```teo-page-plan\n'+json.dumps(document)+'\n```')])

    def imported(self):
        result = self.content.import_messages(self.fb,self.page,self.snapshot())
        self.assertFalse(result['error'],result['status'])
        self.tick()
        return result['plan']

    def finish(self, command_id):
        deadline = time.monotonic()+3
        while time.monotonic()<deadline:
            self.tick()
            row = self.bridge.store.get(command_id)
            if row['state'] not in {'queued','running'}:
                return row
            time.sleep(.005)
        self.fail('Temporary command did not settle')

    def test_admin_html_and_connection_are_visible_without_chat_history(self):
        html=self.client.get('/facebook').text
        self.assertIn('Kế hoạch từ Codex',html)
        self.assertIn('page_content.js',html)
        data=self.data()
        self.assertEqual(data['content_connections'][0]['thread_id'],self.thread)
        for secret in ('browser-profiles','cookie','credential_ref','messages',str(self.desktop)):
            self.assertNotIn(secret,json.dumps(data))

    def test_staff_and_csrf_cannot_receive_or_approve_plans(self):
        self.login('staff')
        self.assertEqual(self.command('sync_content_plan',page_key=self.page,thread_id=self.thread).status_code,403)
        self.assertEqual(self.command('approve_content_plan',page_key=self.page,plan_id=str(uuid.uuid4()),plan_digest='a'*64).status_code,403)
        self.login('admin')
        self.assertEqual(self.client.post('/api/facebook/commands',json=dict(action='sync_content_plan')).status_code,403)

    def test_sync_executes_only_on_desktop_and_imports_passively(self):
        with patch('teodub.codex_chat.read_thread',return_value=self.snapshot()) as reader:
            response=self.command('sync_content_plan',page_key=self.page,thread_id=self.thread)
            self.assertEqual(response.status_code,202,response.json)
            reader.assert_not_called()
            result=self.finish(response.json['command']['id'])
            self.assertEqual(result['state'],'done',result)
            reader.assert_called_once_with(self.thread)
        self.assertEqual(len(self.content.plans(self.fb,self.page)),1)
        self.assertEqual(PageScheduleStore(self.desktop).list(),[])
        self.owner.submit_page_browser.assert_not_called()

    def test_wrong_connection_and_plan_digest_reject_before_queue(self):
        self.assertEqual(self.command('sync_content_plan',page_key=self.page,thread_id=str(uuid.uuid4())).status_code,409)
        plan=self.imported()
        response=self.command('approve_content_plan',page_key=self.page,plan_id=plan['plan_id'],plan_digest='a'*64)
        self.assertEqual(response.status_code,409)
        self.assertEqual(PageScheduleStore(self.desktop).list(),[])

    def test_plan_approval_stages_then_arms_without_publishing_now(self):
        plan=self.imported()
        response=self.command('approve_content_plan',page_key=self.page,plan_id=plan['plan_id'],plan_digest=plan['digest'])
        self.assertEqual(response.status_code,202,response.json)
        self.assertEqual(PageScheduleStore(self.desktop).list(),[])
        result=self.finish(response.json['command']['id'])
        self.assertEqual(result['state'],'done',result)
        schedule=PageScheduleStore(self.desktop).list()[0]
        self.assertEqual(schedule['state'],'armed')
        self.owner.submit_page_browser.assert_not_called()
        self.assertEqual(self.data()['content_plans'][0]['items'][0]['state'],'armed')

    def test_approval_same_key_is_idempotent_and_repeat_does_not_resume_pause(self):
        plan=self.imported()
        body=dict(action='approve_content_plan',account_id=self.fb,page_key=self.page,
            plan_id=plan['plan_id'],plan_digest=plan['digest'],request_key=uuid.uuid4().hex)
        response=self.post('/api/facebook/commands',body)
        self.assertEqual(self.post('/api/facebook/commands',body).json['command']['id'],response.json['command']['id'])
        self.assertEqual(self.finish(response.json['command']['id'])['state'],'done')
        schedule=PageScheduleStore(self.desktop).list()[0]
        self.scheduler.pause(schedule['id'])
        body['request_key']=uuid.uuid4().hex
        response=self.post('/api/facebook/commands',body)
        self.assertEqual(self.finish(response.json['command']['id'])['state'],'done')
        self.assertEqual(PageScheduleStore(self.desktop).get(schedule['id'])['state'],'paused')
        self.assertEqual(len(PageScheduleStore(self.desktop).list()),1)

    def test_page_changed_after_review_does_not_stage_or_arm(self):
        plan=self.imported()
        response=self.command('approve_content_plan',page_key=self.page,plan_id=plan['plan_id'],plan_digest=plan['digest'])
        self.registry.save_page(self.fb,'Page đã đổi','facebook.com/100','100',record_id=self.page)
        result=self.finish(response.json['command']['id'])
        self.assertEqual(result['state'],'failed')
        self.assertEqual(PageScheduleStore(self.desktop).list(),[])
        self.owner.submit_page_browser.assert_not_called()
