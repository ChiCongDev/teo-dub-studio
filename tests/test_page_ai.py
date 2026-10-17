"""Real local persistence/scheduling, synthetic AI and files; no inference or FB."""
from copy import deepcopy
from datetime import datetime,timedelta
import json
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
import uuid

from teodub.common import atomic_json,hash_file
from teodub.facebook_accounts import FacebookAccounts
from teodub.page_ai import PageAIController,PageAIStore,make_prompt
from teodub.page_schedule import LOCAL_TIME,PageScheduleController


class PageAITests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        accounts=FacebookAccounts(self.root)
        self.account=accounts.add_account('Synthetic FB')['id']
        self.page=accounts.save_page(self.account,'Synthetic Page','facebook.com/profile.php?id=100','100')['id']
        self.thread=str(uuid.uuid4())
        self.store=PageAIStore(self.root)
        self.store.plans.save_connection(self.account,self.page,self.thread,'Phát triển page câu cá')
        self.campaign='a'*32
        output=self.root/'outputs'
        output.mkdir()
        self.video=output/'Video 1.mp4'
        self.video.write_bytes(b'synthetic finished video')
        self.subs=output/'Video 1.srt'
        self.subs.write_text('1\n00:00:00,000 --> 00:00:03,000\nMẹo chọn mồi câu cá.\n',encoding='utf-8')
        job=self.root/'campaigns'/self.campaign/'jobs'/'0001'
        outputs=dict(video=str(self.video),subtitles=str(self.subs))
        atomic_json(job/'state.json',dict(rendered=dict(outputs=outputs,hashes={k:hash_file(v) for k,v in outputs.items()})))
        atomic_json(job.parent.parent/'campaign.json',dict(id=self.campaign,name='Câu cá',output_dir=str(output),
            items=[dict(number=1,status='completed',job=str(job),outputs=outputs,title='Mẹo câu cá')]))
        self.config=dict(campaigns=[self.campaign],model='gpt-5.6-luna',effort='low',days=7,
            videos_per_day=2,texts_per_week=3,interval_days=7,goal='Nội dung hữu ích',auto_publish=False)
        self.owner=SimpleNamespace(studio=SimpleNamespace(data_dir=self.root),sessions={},status=Mock(),submit_page_browser=Mock())
        self.scheduler=PageScheduleController(self.owner)
        self.addCleanup(self.scheduler.close)
        self.owner.get_page_scheduler=Mock(return_value=self.scheduler)
        self.bridge=SimpleNamespace(owner=self.owner,active=None,content_receiver=SimpleNamespace(pending=None))
        self.invoke=Mock()
        self.reader=Mock(return_value=dict(id=self.thread,messages=[dict(phase='final_answer',turn_status='completed',text='Chiến lược hữu ích')]))
        self.controller=PageAIController(self.bridge,invoke=self.invoke,reader=self.reader)
        self.addCleanup(self.controller.close)

    def saved(self,**values):
        self.config.update(values)
        return self.store.save(self.account,self.page,self.config,time.time()+30,True)

    def task(self,**values):
        row=self.saved(**values)
        task=self.store.claim(row,row['next_at']+1)
        task.update(cancel=threading.Event(),plan_uuid=str(uuid.uuid4()))
        return task

    def answer(self,task,*,video=True):
        when=(datetime.fromtimestamp(task['clock'],LOCAL_TIME)+timedelta(hours=2)).isoformat(timespec='seconds')
        item=dict(id='post-1',kind='video' if video else 'text',due_at=when,body='Mẹo chọn mồi câu cá. #cauca',
                  video_key=self.campaign+':1' if video else '',goal='Hữu ích')
        return dict(analysis='Chưa có số liệu view, thử nghiệm giờ đăng.',plan=dict(format='teo-page-plan',version=1,
            plan_id=task['plan_uuid'],title='Kế hoạch AI',page_id='100',time_zone='Asia/Bangkok',items=[item]))

    def finish(self,task,answer=None):
        self.controller.pending=task
        self.invoke.return_value=(answer or self.answer(task),dict(requested_model='gpt-5.6-luna',observed_model=None,
            usage=dict(input_tokens=300,output_tokens=100)))
        self.controller.work(task)
        self.controller.observe()
        return self.store.runs(self.account,self.page)[0]

    def test_restart_waiting_does_not_claim_or_disable_next_ai_policy(self):
        row = self.saved()
        self.owner.studio.restart_pending = True
        with patch('teodub.page_ai.time.time',return_value=row['next_at']+1):
            self.controller.tick()
        self.invoke.assert_not_called()
        self.reader.assert_not_called()
        saved = self.store.policy(self.account,self.page)
        self.assertTrue(saved['enabled'])
        self.assertEqual(saved['next_at'],row['next_at'])
        self.assertEqual(self.store.runs(self.account,self.page),[])

    def test_waiting_ticks_make_no_ai_calls(self):
        self.saved()
        for _ in range(3):
            self.controller.last_poll=0
            self.controller.tick()
        self.invoke.assert_not_called()
        self.reader.assert_not_called()

    def test_due_claim_runs_once_and_passes_model_to_engine(self):
        row=self.saved()
        with patch('teodub.page_ai.time.time',return_value=row['next_at']+1),patch('teodub.page_ai.threading.Thread') as thread:
            self.controller.tick()
            task=self.controller.pending
            self.invoke.return_value=(self.answer(task),dict(usage=dict(input_tokens=20,output_tokens=10)))
            thread.call_args.kwargs['target'](*thread.call_args.kwargs['args'])
            self.controller.observe()
            self.controller.last_poll=0
            self.controller.tick()
        self.assertEqual(self.invoke.call_count,1)
        self.assertEqual(self.invoke.call_args.kwargs['model'],'gpt-5.6-luna')
        self.assertEqual(self.invoke.call_args.kwargs['reasoning_effort'],'low')
        self.assertEqual(self.store.runs(self.account,self.page)[0]['state'],'done')

    def test_six_day_repeat_keeps_policy_and_excludes_previous_plan_video(self):
        task = self.task(days=6,interval_days=6,auto_publish=True)
        run = self.finish(task)
        self.assertEqual(run['state'],'armed',run['message'])
        policy = self.store.policy(self.account,self.page)
        self.assertTrue(policy['enabled'])
        self.assertEqual(policy['next_at'],task['next_at']+6*86400)
        next_task = self.store.claim(policy,policy['next_at']+1)
        self.assertIsNotNone(next_task)
        self.assertEqual(next_task['config']['days'],6)
        self.assertEqual(self.store.context(next_task)['videos'],[])
        self.assertEqual(self.store.policy(self.account,self.page)['next_at'],policy['next_at']+6*86400)

    def test_claim_durable_double_dispatch_prevented(self):
        row=self.saved()
        self.assertIsInstance(self.store.claim(row,row['next_at']+1),dict)
        self.assertIsNone(self.store.claim(row,row['next_at']+1))

    def test_oneoff_can_finish_without_enabled_recurring_policy(self):
        task=self.task(interval_days=0)
        self.assertFalse(self.store.policy(self.account,self.page)['enabled'])
        run=self.finish(task)
        self.assertEqual(run['state'],'done')
        self.assertEqual(self.invoke.call_count,1)

    def test_proposal_is_passive_with_honest_provenance(self):
        task=self.task()
        run=self.finish(task)
        plan=self.store.plans.get_plan(run['plan_id'],self.account,self.page)
        self.assertEqual(plan['source_kind'],'scheduled_ai')
        self.assertEqual(plan['source_run_id'],task['id'])
        self.assertFalse(plan['approved_at'])
        self.assertEqual(self.scheduler.store.list(),[])
        self.assertIn('usage',run['metadata'])
        self.assertIn('Tèo → AI',[r['actor'] for r in self.store.history(self.account,self.page)])

    def test_auto_policy_arms_exact_generated_plan_no_fb_before_due(self):
        task=self.task(auto_publish=True)
        run=self.finish(task)
        self.assertEqual(run['state'],'armed',run['message'])
        rows=self.scheduler.store.list()
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['state'],'armed')
        self.assertEqual(rows[0]['body'],self.answer(task)['plan']['items'][0]['body'])
        self.owner.submit_page_browser.assert_not_called()
        self.store.observe_posting()
        first=len(self.store.history(self.account,self.page))
        self.store.observe_posting()
        self.assertEqual(len(self.store.history(self.account,self.page)),first)
        self.scheduler.store.update(rows[0]['id'],state='needs_attention',message='Synthetic failure')
        self.store.observe_posting()
        self.assertEqual(len(self.store.history(self.account,self.page)),first+1)

    def test_pause_while_ai_runs_never_arms(self):
        task=self.task(auto_publish=True)
        def call(*args,**kwargs):
            self.store.pause(self.account,self.page)
            return self.answer(task),dict(usage=None)
        self.invoke.side_effect=call
        run=self.finish(task)
        self.assertEqual(run['state'],'needs_attention')
        self.assertEqual(self.scheduler.store.list(),[])

    def test_changed_binding_blocks_before_inference(self):
        task=self.task()
        self.store.plans.save_connection(self.account,self.page,str(uuid.uuid4()),'Changed chat')
        self.finish(task)
        self.invoke.assert_not_called()

    def test_changed_video_after_response_blocks_auto_upload(self):
        task=self.task(auto_publish=True)
        def call(*args,**kwargs):
            self.video.write_bytes(b'changed video')
            return self.answer(task),dict(usage=None)
        self.invoke.side_effect=call
        run=self.finish(task)
        self.assertEqual(run['state'],'needs_attention')
        self.assertEqual(self.scheduler.store.list(),[])

    def test_ai_error_stops_policy_no_fallback_or_retry(self):
        task=self.task()
        self.invoke.side_effect=ValueError('Model unavailable')
        run=self.finish(task)
        self.assertEqual(run['state'],'needs_attention')
        self.assertFalse(self.store.policy(self.account,self.page)['enabled'])
        self.controller.tick()
        self.assertEqual(self.invoke.call_count,1)

    def test_recovery_marks_unknown_without_repeat(self):
        task=self.task()
        self.store.recover()
        self.assertEqual(self.store.runs(self.account,self.page)[0]['state'],'needs_attention')
        self.assertFalse(self.store.policy(self.account,self.page)['enabled'])
        self.controller.tick()
        self.invoke.assert_not_called()

    def test_missed_due_stops_without_catchup(self):
        row=self.saved()
        result=self.store.claim(row,row['next_at']+601)
        self.assertIsInstance(result,str)
        self.assertFalse(self.store.policy(self.account,self.page)['enabled'])
        self.assertEqual(self.store.runs(self.account,self.page),[])

    def test_daily_budget_blocks_second_new_configuration(self):
        self.task()
        row=self.saved()
        self.assertIsInstance(self.store.claim(row,row['next_at']+1),str)

    def test_context_only_selected_completed_verified_sources(self):
        task=self.task()
        context=self.store.context(task,'Strategy')
        self.assertEqual(len(context['videos']),1)
        self.assertTrue(context['videos'][0]['content_verified'])
        prompt=make_prompt(task,context)
        self.assertNotIn(str(self.root),prompt)
        self.assertIn('Mẹo chọn mồi',prompt)
        self.assertNotIn('credential',prompt)
        self.assertEqual(context['statistics']['page_observations'],[])

    def test_modified_srt_untrusted_not_used(self):
        task=self.task()
        self.subs.write_text('bad replacement',encoding='utf-8')
        context=self.store.context(task)
        self.assertFalse(context['videos'][0]['content_verified'])
        with self.assertRaisesRegex(ValueError,'kiểm chứng'):
            self.store.validate_plan(task,self.answer(task),context)

    def test_pending_proposal_not_reused_next_plan(self):
        task=self.task()
        self.finish(task)
        context=self.store.context(task)
        self.assertEqual(context['videos'],[])

    def test_wrong_page_time_or_cadence_rejected(self):
        task=self.task()
        context=self.store.context(task)
        for reason in ('page','time','cadence'):
            answer=self.answer(task)
            if reason=='page':
                answer['plan']['page_id']='200'
            elif reason=='time':
                answer['plan']['items'][0]['due_at']=(datetime.now(LOCAL_TIME)+timedelta(days=20)).isoformat(timespec='seconds')
            else:
                task['config']['videos_per_day']=0
            with self.subTest(reason=reason),self.assertRaises(ValueError):
                self.store.validate_plan(task,answer,context)

    def test_no_items_records_analysis_not_fake_plan(self):
        task=self.task()
        answer=self.answer(task)
        answer['plan']['items']=[]
        run=self.finish(task,answer)
        self.assertEqual(run['state'],'done')
        self.assertEqual(run['plan_id'],'')
        self.assertEqual(self.scheduler.store.list(),[])

    def test_fixed_caption_template_enforced(self):
        task=self.task()
        self.scheduler.store.save_template(self.account,self.page,'Mẫu cố định','#Fishing')
        context=self.store.context(task)
        with self.assertRaisesRegex(ValueError,'caption'):
            self.store.validate_plan(task,self.answer(task),context)

    def test_invalid_settings_dont_enable_automation(self):
        for change in (dict(interval_days=1),dict(campaigns=[['bad']]),dict(model='invented-model'),dict(days=True)):
            config=dict(self.config,**change)
            with self.subTest(change=change),self.assertRaises(ValueError):
                self.store.save(self.account,self.page,config,time.time()+30,True)
        self.assertIsNone(self.store.policy(self.account,self.page))

    def test_local_statistics_use_saved_real_sources(self):
        self.store.plans.activity.observe(self.account,self.page,dict(name='Synthetic',followers=250),source='meta_api')
        context=self.store.context(self.task())
        row=context['statistics']['page_observations'][0]
        self.assertEqual(row['followers'],250)
        self.assertEqual(row['source'],'meta_api')
        self.assertIn('at',row)


if __name__=='__main__':
    unittest.main()
