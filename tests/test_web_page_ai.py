from datetime import datetime,timedelta
from unittest.mock import patch

from tests.test_web_page_content import WebPageContentTests
from teodub.page_schedule import LOCAL_TIME


class WebPageAITests(WebPageContentTests):
    """HTTP auth/CSRF and desktop persistence; inherited plan tests stay valid."""
    def config(self):
        return dict(campaigns=[],model='gpt-5.6-luna',effort='low',days=7,videos_per_day=0,
            texts_per_week=3,interval_days=7,goal='Useful content',auto_publish=False)

    def test_page_schedule_status_is_scoped_and_visible_without_starting_work(self):
        data=self.data()
        row=next(r for r in data['page_statuses'] if r['account_id']==self.fb and r['page_key']==self.page)
        self.assertEqual(row['state'],'off')
        self.assertEqual(row['published_posts'],0)
        self.assertIn('Trạng thái lịch Page',self.client.get('/facebook').text)
        self.assertEqual(self.scheduler.active,set())

    def ai(self,**kwargs):
        due=(datetime.now(LOCAL_TIME)+timedelta(days=1)).strftime('%Y-%m-%d %H:%M')
        return self.command('save_ai_policy',page_key=self.page,thread_id=self.thread,config=self.config(),due_at=due,enabled=True,**kwargs)

    def test_web_saves_policy_on_desktop_without_running_ai(self):
        with patch('teodub.codex_translation._invoke_codex') as call:
            response=self.ai()
            self.assertEqual(response.status_code,202,response.text)
            result=self.finish(response.json['command']['id'])
            self.assertEqual(result['state'],'done',result['message'])
            row=self.bridge.ai_controller.store.policy(self.fb,self.page)
            self.assertEqual(row['config']['model'],'gpt-5.6-luna')
            self.assertTrue(row['enabled'])
            call.assert_not_called()
            data=self.data()
            self.assertEqual(data['ai_policies'][0]['config']['goal'],'Useful content')

    def test_pause_command_changes_only_ai_policy(self):
        self.finish(self.ai().json['command']['id'])
        response=self.command('pause_ai_policy',page_key=self.page)
        self.assertEqual(self.finish(response.json['command']['id'])['state'],'done')
        self.assertFalse(self.bridge.ai_controller.store.policy(self.fb,self.page)['enabled'])

    def test_staff_cannot_arm_ai(self):
        self.login('staff')
        self.assertEqual(self.ai().status_code,403)

    def test_changed_chat_after_web_queue_rejected(self):
        import uuid
        response=self.ai()
        self.content.save_connection(self.fb,self.page,str(uuid.uuid4()),'Another strategy')
        result=self.finish(response.json['command']['id'])
        self.assertNotEqual(result['state'],'done')
        self.assertIsNone(self.bridge.ai_controller.store.policy(self.fb,self.page))
