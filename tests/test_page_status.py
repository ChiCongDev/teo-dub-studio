import unittest
from unittest.mock import Mock,patch
from types import SimpleNamespace

from teodub.page_status import summarize,read_page_status


class PageStatusTests(unittest.TestCase):
    def policy(self,enabled=True):
        return dict(enabled=enabled,next_at=2000,revision='new',config=dict(auto_publish=False))

    def schedule(self,state='armed'):
        return dict(id='schedule-one',state=state,due_at=3000,message='Error explanation')

    def test_enabled_waiting_is_running_with_next_time(self):
        row=summarize(self.policy(),[],[],clock=1000)
        self.assertEqual(row['state'],'waiting')
        self.assertIn('Đang chạy',row['label'])
        self.assertEqual(row['next_ai_at'],2000)

    def test_saved_policy_without_executor_is_not_reported_running(self):
        row=summarize(self.policy(),[],[],executor_ready=False)
        self.assertEqual(row['state'],'waiting_executor')
        self.assertNotIn('Đang chạy',row['label'])

    def test_stale_armed_without_local_ownership_requires_resume(self):
        row=summarize(None,[],[self.schedule()],active_ids=set())
        self.assertEqual(row['state'],'paused')
        self.assertIsNone(row['next_post_at'])

    def test_active_post_schedule_keeps_running_when_ai_is_paused(self):
        row=summarize(self.policy(False),[],[self.schedule()],active_ids={'schedule-one'})
        self.assertEqual(row['state'],'waiting')
        self.assertEqual(row['next_post_at'],3000)
        self.assertIsNone(row['next_ai_at'])

    def test_busy_post_and_preparation_statuses(self):
        for state,expected in [('preparing','preparing'),('submitting','posting')]:
            with self.subTest(state=state):
                self.assertEqual(summarize(None,[],[self.schedule(state)])['state'],expected)

    def test_verifying_submission_is_visible_without_claiming_published_or_blocking_next(self):
        checking=self.schedule('verifying')
        following=dict(self.schedule('armed'),id='following')
        row=summarize(None,[],[checking,following],clock=4000,active_ids={'schedule-one','following'})
        self.assertEqual(row['state'],'verifying')
        self.assertEqual(row['label'],'Đã gửi — đang xác minh')
        self.assertEqual(row['verifying_posts'],1)
        self.assertEqual(row['published_posts'],0)
        self.assertEqual(row['waiting_posts'],1)
        self.assertEqual(row['next_post_at'],3000)
        self.assertIn('Các bài khác đến giờ vẫn được xử lý',row['detail'])

    def test_unowned_or_disconnected_verification_never_claims_running(self):
        for kwargs in (dict(active_ids=set()),dict(executor_ready=False)):
            with self.subTest(kwargs=kwargs):
                row=summarize(None,[],[self.schedule('verifying')],**kwargs)
                self.assertEqual(row['state'],'waiting_executor')
                self.assertEqual(row['label'],'Đã gửi — chờ kiểm tra lại')
                self.assertEqual(row['verifying_posts'],1)
                self.assertEqual(row['published_posts'],0)

    def test_next_upload_and_background_verification_counts_are_both_visible(self):
        rows=[self.schedule('verifying'),dict(self.schedule('submitting'),id='following')]
        row=summarize(None,[],rows)
        self.assertEqual(row['state'],'posting')
        self.assertEqual(row['verifying_posts'],1)
        self.assertIn('Đã gửi, chưa xác nhận: 1',row['detail'])
        self.assertEqual(row['published_posts'],0)

    def test_due_job_is_not_described_as_waiting_for_its_time(self):
        row=summarize(None,[],[self.schedule()],clock=4000)
        self.assertEqual(row['state'],'waiting')
        self.assertIn('bài đã đến giờ',row['label'])

    def test_unknown_verification_is_explicitly_unconfirmed(self):
        row=summarize(None,[],[dict(self.schedule('needs_attention'),phase='verify')])
        self.assertEqual(row['state'],'needs_attention')
        self.assertIn('Chưa xác minh đã đăng',row['label'])
        self.assertEqual(row['published_posts'],0)

    def test_running_ai_oneoff_visible_even_when_policy_no_longer_enabled(self):
        row=summarize(self.policy(False),[],[],ai_running=True)
        self.assertEqual(row['state'],'analyzing')

    def test_failed_run_explains_reason(self):
        runs=[dict(revision='new',state='needs_attention',message='Quota unavailable')]
        row=summarize(self.policy(False),runs,[])
        self.assertEqual(row['state'],'needs_attention')
        self.assertIn('Quota unavailable',row['detail'])

    def test_old_failure_not_applied_to_new_policy(self):
        row=summarize(self.policy(),[dict(revision='old',state='needs_attention',message='Old error')],[])
        self.assertEqual(row['state'],'waiting')
        self.assertNotIn('Old error',row['detail'])

    def test_saved_schedules_and_proposals_not_running(self):
        self.assertEqual(summarize(None,[],[self.schedule('scheduled')])['state'],'pending')
        runs=[dict(revision='new',state='done',plan_id='plan-one')]
        self.assertEqual(summarize(self.policy(False),runs,[])['state'],'review')

    def test_completed_only_from_published_status_and_counts(self):
        row=summarize(None,[],[self.schedule('published')])
        self.assertEqual(row['state'],'completed')
        self.assertEqual(row['published_posts'],1)
        self.assertEqual(summarize(None,[],[])['state'],'off')

    def test_errors_counted_while_other_jobs_waiting(self):
        row=summarize(self.policy(),[],[self.schedule('needs_attention')])
        self.assertEqual(row['state'],'waiting')
        self.assertEqual(row['error_posts'],1)

    def test_removed_schedules_are_not_active_page_status_or_counts(self):
        rows=[self.schedule('removed')]
        result=summarize(None,[],rows,active_ids={'schedule-one'})
        self.assertEqual(result['state'],'off')
        self.assertIsNone(result['next_post_at'])
        for key in ('waiting_posts','verifying_posts','published_posts','paused_posts','error_posts'):
            self.assertEqual(result[key],0)
        self.assertEqual(rows[0]['state'],'removed')

    def test_removed_history_does_not_hide_active_schedule_status(self):
        result=summarize(None,[],[self.schedule('removed'),
            dict(self.schedule('armed'),id='active')],active_ids={'active'})
        self.assertEqual(result['state'],'waiting')
        self.assertEqual(result['waiting_posts'],1)
        self.assertEqual(result['next_post_at'],3000)

    def test_read_does_not_construct_scheduler_or_invoke_ai(self):
        owner=SimpleNamespace(page_bridge=SimpleNamespace(guard=True,closed=False,ai_controller=SimpleNamespace(pending=None)),
            page_scheduler=None,get_page_scheduler=Mock())
        with patch('teodub.page_ai.PageAIStore') as ai,patch('teodub.page_schedule.PageScheduleStore') as schedules:
            ai.return_value.policy.return_value=self.policy()
            ai.return_value.runs.return_value=[]
            schedules.return_value.list.return_value=[]
            row=read_page_status('temporary','account','page',owner)
        self.assertEqual(row['state'],'waiting')
        owner.get_page_scheduler.assert_not_called()


if __name__=='__main__':
    unittest.main()
