"""Synthetic rendered videos and browser messages; no Facebook or real schedules."""
from datetime import datetime
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from teodub.common import atomic_json, hash_file
from teodub.facebook_accounts import FacebookAccounts
from teodub.page_schedule import (PageScheduleStore, PageScheduleController, campaign_video_source,
                                 display_time, parse_time)


class ScheduleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.accounts = FacebookAccounts(self.directory)
        self.account = self.accounts.add_account('Fixture A')['id']
        self.page = self.accounts.save_page(self.account,'Fixture Page','facebook.com/profile.php?id=100','100')['id']
        self.other = self.accounts.add_account('Fixture B')['id']
        self.other_page = self.accounts.save_page(self.other,'Other','facebook.com/profile.php?id=200','200')['id']
        self.store = PageScheduleStore(self.directory)
        self.calls = []
        self.owner = SimpleNamespace(studio=SimpleNamespace(data_dir=self.directory),sessions={},status=Mock())
        self.owner.status.get.return_value = 'Chưa mở được Chrome (fixture)'
        self.owner.submit_page_browser = self.submit
        self.controller = PageScheduleController(self.owner)
        self.addCleanup(self.controller.close)

    def video(self, number=1, campaign_id='a'*32, status='completed'):
        # Windows TEMP can use an 8.3 alias. Recorded job paths must have the
        # same canonical form as the production campaign identity checks.
        root = self.directory.resolve()
        folder = root/'campaigns'/campaign_id
        job = folder/'jobs'/f'{number:04}'
        output_dir = root/'outputs'/campaign_id
        output_dir.mkdir(parents=True,exist_ok=True)
        output = output_dir/f'Video {number}.mp4'
        output.write_bytes(b'synthetic video '+str(number).encode())
        outputs = dict(video=str(output))
        atomic_json(job/'state.json',dict(rendered=dict(outputs=outputs,hashes=dict(video=hash_file(output)))))
        atomic_json(folder/'campaign.json',dict(id=campaign_id,name='Synthetic campaign',output_dir=str(output_dir),
            items=[dict(number=number,status=status,job=str(job),outputs=outputs)]))
        return folder,number

    def schedule(self, campaign_id='a'*32):
        source = campaign_video_source(self.directory,*self.video(campaign_id=campaign_id))
        return self.store.create(self.account,self.page,source,'Caption cố định','#ca #video',time.time()+3600)

    def test_bulk_start_is_page_scoped_deduplicated_and_never_replays_attempts(self):
        good = self.schedule()
        old = self.schedule('b'*32)
        uncertain = self.schedule('c'*32)
        finished = self.schedule('d'*32)
        other_source = campaign_video_source(self.directory,*self.video(campaign_id='e'*32))
        other = self.store.create(self.other,self.other_page,other_source,'Other caption','#Other',time.time()+3600)
        self.store.update(old['id'],due_at=time.time()-10)
        self.store.update(uncertain['id'],state='needs_attention',attempted_at='synthetic attempted')
        self.store.update(finished['id'],state='published')
        ids = [good['id'],good['id'],old['id'],uncertain['id'],finished['id'],other['id']]
        result = self.controller.start_many(self.account,self.page,ids)
        self.assertEqual([r['outcome'] for r in result],['started','skipped','skipped','skipped','skipped'])
        self.assertEqual(self.controller.active,{good['id']})
        self.assertEqual(self.store.get(old['id'])['state'],'scheduled')
        self.assertEqual(self.store.get(uncertain['id'])['attempted_at'],'synthetic attempted')
        self.assertEqual(self.controller.start_many(self.account,self.page,[good['id']])[0]['outcome'],'active')
        self.assertEqual(self.calls,[])

    def submit(self, account,page,post_id,operation, *, request_id=None):
        request = request_id or str(len(self.calls))
        self.calls.append((account,page,post_id,operation))
        session = self.owner.sessions.setdefault(account,SimpleNamespace(loading_pages=False,post_actions={}))
        session.post_actions[request] = dict(state='queued',operation=operation,message='fixture',result=None)
        return request

    def tick(self):
        self.controller.last_tick = 0
        self.controller.tick()

    def ready_for_validation(self, row):
        self.controller.start(row['id'])
        self.store.update(row['id'],due_at=time.time()-1)
        with patch('teodub.page_schedule.threading.Thread') as thread:
            thread.return_value.start.side_effect = lambda: self.controller.validate_video(self.store.get(row['id']))
            self.tick()
        self.tick()
        return self.store.get(row['id'])

    def reply(self,row,state,post_state):
        post = self.store.activity.post(row['account_id'],row['page_key'],row['post_id'])
        scope = (row['account_id'],row['page_key'],row['post_id'])
        if post_state=='review':
            self.store.activity.transition(*scope,'preparing','fixture preparing',expected={'draft'})
            review = dict(account_id=row['account_id'],page_key=row['page_key'],post_id=row['post_id'],
                **{key:post[key] for key in ('target_name','target_url','target_id','body','browser_finish_mode')},
                media=[dict(saved,sha256=hash_file(saved['path'])) for saved in post['media']])
            post = self.store.activity.save_browser_review(*scope,review)
        elif post_state=='published':
            self.store.activity.transition(*scope,'submitting','fixture submitting',expected={'review'})
            self.store.activity.transition(*scope,'processing','fixture processing',expected={'submitting'})
            url = 'https://www.facebook.com/100/posts/'+str(int(row['post_id'], 16))
            self.store.activity.record_browser_receipt(*scope,dict(source='visible_dom',url=url,checked_at='fixture'))
            post = self.store.activity.transition(*scope,'published','fixture confirmed',expected={'processing'},external_url=url)
        else:
            post = self.store.activity.transition(*scope,post_state,'fixture result')
        request = self.owner.sessions[row['account_id']].post_actions[row['request_id']]
        request.update(state=state,result=post,message='fixture result')

    def test_timezone_future_validation_and_fixed_body_no_auto_on_create(self):
        self.assertEqual(display_time(parse_time('2026-10-03 21:12')),'2026-10-03 21:12')
        instant = datetime.fromtimestamp(parse_time('2026-10-03 21:12'),__import__('datetime').timezone.utc)
        self.assertEqual(instant.hour,14)
        for value in ('tomorrow','2026-10-03 99:00',''):
            with self.assertRaises(ValueError): parse_time(value)
        row = self.schedule()
        self.assertEqual(row['state'],'scheduled')
        self.assertEqual(row['body'],'Caption cố định\n\n#ca #video')
        self.assertEqual(row['snapshot']['delivery_mode'],'browser')
        self.assertEqual(row['snapshot']['browser_finish_mode'],'tool')
        self.assertEqual(self.calls,[])
        self.assertEqual(self.store.template(self.account,self.page)['hashtags'],'#ca #video')
        self.assertEqual(self.store.template(self.other,self.other_page),dict(caption='',hashtags=''))
        with self.assertRaises(ValueError):
            self.store.create(self.account,self.page,row['source'],'new','',time.time()+900)

    def test_sources_reject_unfinished_review_and_original_outside_output(self):
        for status in ('pending','running','review_required','failed'):
            folder,number = self.video(status=status)
            with self.assertRaises(ValueError): campaign_video_source(self.directory,folder,number)
        folder,number = self.video()
        manifest = __import__('json').loads((folder/'campaign.json').read_text())
        manifest['output_dir'] = str(self.directory/'other output')
        atomic_json(folder/'campaign.json',manifest)
        with self.assertRaises(ValueError): campaign_video_source(self.directory,folder,number)
        with self.assertRaises(ValueError): campaign_video_source(self.directory,self.directory,number)

    def test_one_prepare_one_publish_only_after_approved_snapshot_then_verified_result(self):
        row = self.ready_for_validation(self.schedule())
        self.assertEqual([x[-1] for x in self.calls],['prepare'])
        self.reply(row,'done','review')
        self.tick()
        row = self.store.get(row['id'])
        self.assertEqual([x[-1] for x in self.calls],['prepare','publish'])
        self.assertEqual(self.calls[0][:3],(self.account,self.page,row['post_id']))
        self.reply(row,'done','published')
        self.tick(); self.tick()
        self.assertEqual(self.store.get(row['id'])['state'],'published')
        self.assertEqual(len(self.calls),2)
        self.assertNotIn(row['id'],self.controller.active)

    def test_changed_body_blocks_publish_and_error_no_auto_retry(self):
        row = self.ready_for_validation(self.schedule())
        self.reply(row,'done','review')
        with self.assertRaises(ValueError):
            self.store.activity.save_draft(self.account,self.page,'Changed',post_id=row['post_id'],delivery_mode='browser',browser_finish_mode='tool')
        # Simulate a corrupt/externally modified temporary database despite the edit guard.
        with self.store.activity.database() as conn:
            conn.execute('UPDATE posts SET body=? WHERE id=?',('Changed',row['post_id']))
        self.tick(); self.tick()
        self.assertEqual([x[-1] for x in self.calls],['prepare'])
        self.assertEqual(self.store.get(row['id'])['state'],'needs_attention')
        with self.assertRaises(ValueError): self.controller.start(row['id'])

    def test_hash_change_preserving_size_and_mtime_fails_before_any_upload(self):
        row = self.schedule()
        path = Path(row['source']['path'])
        before = path.stat()
        path.write_bytes(b'X'*before.st_size)
        __import__('os').utime(path,ns=(before.st_atime_ns,before.st_mtime_ns))
        row = self.ready_for_validation(row)
        self.assertEqual(row['state'],'needs_attention')
        self.assertEqual(self.calls,[])

    def test_unknown_publish_result_preserved_no_second_click(self):
        row = self.ready_for_validation(self.schedule())
        self.reply(row,'done','review'); self.tick()
        row = self.store.get(row['id'])
        self.reply(row,'done','unknown'); self.tick(); self.tick()
        self.assertEqual(self.store.get(row['id'])['state'],'needs_attention')
        self.assertEqual(len(self.calls),2)
        with self.assertRaises(ValueError): self.store.reschedule(row['id'],time.time()+7200)

    def test_missed_time_requires_rescheduling_without_attempt(self):
        row = self.schedule()
        self.controller.start(row['id'])
        self.store.update(row['id'],due_at=time.time()-121)
        self.tick()
        self.assertEqual(self.store.get(row['id'])['state'],'needs_attention')
        self.assertEqual(self.calls,[])
        self.store.reschedule(row['id'],time.time()+7200)
        self.controller.start(row['id'])
        self.assertEqual(self.store.get(row['id'])['state'],'armed')

    def test_two_controllers_cannot_own_or_resume_same_active_schedule(self):
        row = self.schedule()
        self.controller.start(row['id'])
        other = PageScheduleController(self.owner)
        self.addCleanup(other.close)
        self.assertEqual(self.store.get(row['id'])['state'],'armed')
        with self.assertRaises(ValueError): other.start(row['id'])
        self.controller.close()
        reopened = PageScheduleController(self.owner)
        self.addCleanup(reopened.close)
        self.assertEqual(self.store.get(row['id'])['state'],'paused')
        reopened.tick()
        self.assertEqual(self.calls,[])

    def test_pause_preparation_does_not_automatically_publish_when_reply_arrives(self):
        row = self.ready_for_validation(self.schedule())
        self.controller.pause(row['id'])
        self.reply(row,'done','review'); self.tick()
        self.assertEqual([x[-1] for x in self.calls],['prepare'])
        self.assertEqual(self.store.get(row['id'])['state'],'needs_attention')

    def test_missing_browser_reply_or_launch_rejection_is_actionable_and_not_retried(self):
        row = self.ready_for_validation(self.schedule())
        self.owner.sessions.clear(); self.tick(); self.tick()
        self.assertEqual(self.store.get(row['id'])['state'],'needs_attention')
        self.assertEqual(len(self.calls),1)

    def test_page_change_and_foreign_account_do_not_send(self):
        row = self.schedule()
        self.accounts.save_page(self.account,'Different','facebook.com/profile.php?id=100','100',record_id=row['page_key'])
        row = self.ready_for_validation(row)
        self.assertEqual(row['state'],'needs_attention')
        self.assertEqual(self.calls,[])
        with self.assertRaises(ValueError):
            self.store.create(self.other,self.page,row['source'],'','',time.time()+3600)

    def test_changed_frozen_page_preview_rejected_before_schedule(self):
        source = campaign_video_source(self.directory,*self.video())
        with self.assertRaises(ValueError):
            self.store.create(self.account,self.page,source,'','',time.time()+3600,
                expected_target=dict(name='Old name',url='https://www.facebook.com/profile.php?id=100',page_id='100'))
        self.assertEqual(self.store.list(),[])
        self.assertEqual(self.store.activity.posts(self.account,self.page),[])

    def test_missing_durable_review_prevents_final_click(self):
        row = self.ready_for_validation(self.schedule())
        result = self.store.activity.post(row['account_id'],row['page_key'],row['post_id'])
        result['state'] = 'review'
        self.owner.sessions[row['account_id']].post_actions[row['request_id']].update(state='done',result=result)
        self.tick()
        self.assertEqual(len(self.calls),1)
        self.assertEqual(self.store.get(row['id'])['state'],'needs_attention')

    def test_claimed_published_message_without_durable_receipt_is_not_success(self):
        row = self.ready_for_validation(self.schedule())
        self.reply(row,'done','review'); self.tick()
        row = self.store.get(row['id'])
        result = self.store.activity.post(row['account_id'],row['page_key'],row['post_id'])
        result['state'] = 'published'
        self.owner.sessions[row['account_id']].post_actions[row['request_id']].update(state='done',result=result)
        self.tick()
        self.assertEqual(self.store.get(row['id'])['state'],'needs_attention')
        self.assertEqual(len(self.calls),2)

    def test_validation_thread_start_failure_does_not_escape_or_leave_lock(self):
        row = self.schedule()
        self.controller.start(row['id'])
        self.store.update(row['id'],due_at=time.time()-1)
        with patch('teodub.page_schedule.threading.Thread',side_effect=RuntimeError('fixture thread failure')):
            self.tick()
        self.assertEqual(self.store.get(row['id'])['state'],'needs_attention')
        self.assertNotIn(row['id'],self.controller.guards)
        self.assertEqual(self.calls,[])
        self.assertEqual(self.store.get(row['id'])['attempted_at'],'')
        self.store.reschedule(row['id'],time.time()+7200)

    def test_unattempted_cancel_keeps_history_video_and_allows_new_approval(self):
        row = self.schedule()
        self.controller.start(row['id'])
        self.controller.cancel(row['id'])
        self.assertEqual(self.store.get(row['id'])['state'],'cancelled')
        self.assertTrue(Path(row['source']['path']).is_file())
        self.assertEqual(self.store.activity.post(self.account,self.page,row['post_id'])['state'],'draft')
        self.assertTrue(self.store.activity.post(self.account,self.page,row['post_id'])['archived'])
        replacement = self.store.create(self.account,self.page,row['source'],'Different reviewed caption','',time.time()+3600)
        self.assertNotEqual(replacement['id'],row['id'])
        with self.assertRaises(ValueError): self.controller.start(row['id'])

    def test_attempted_schedule_cannot_cancel_to_make_a_duplicate(self):
        row = self.ready_for_validation(self.schedule())
        with self.assertRaises(ValueError): self.controller.cancel(row['id'])
        with self.assertRaises(ValueError):
            self.store.create(self.account,self.page,row['source'],'','',time.time()+3600)

    def test_request_is_durably_reserved_before_queue(self):
        row = self.schedule()
        original = self.owner.submit_page_browser
        def submit(account,page,post,operation,*,request_id=None):
            current = self.store.get(row['id'])
            self.assertEqual(current['phase'],operation)
            self.assertEqual(current['request_id'],request_id)
            self.assertTrue(current['attempted_at'])
            return original(account,page,post,operation,request_id=request_id)
        self.owner.submit_page_browser = submit
        self.ready_for_validation(row)
        self.assertEqual(len(self.calls),1)

    def test_same_account_starts_earliest_due_video_only_and_keeps_other_armed(self):
        later = self.schedule('b'*32)
        earlier = self.schedule('a'*32)
        self.controller.start(later['id']); self.controller.start(earlier['id'])
        self.store.update(later['id'],due_at=time.time()-1)
        self.store.update(earlier['id'],due_at=time.time()-2)
        with patch('teodub.page_schedule.threading.Thread') as thread:
            self.tick()
        self.assertEqual(thread.call_count,1)
        self.assertEqual(thread.call_args.kwargs['args'][0]['id'],earlier['id'])
        self.assertEqual(self.store.get(later['id'])['state'],'armed')
        self.assertEqual(self.store.get(later['id'])['attempted_at'],'')

    def test_missing_page_identity_rejected_before_saving_or_arming(self):
        source = campaign_video_source(self.directory,*self.video())
        page = self.accounts.save_page(self.account,'Unknown name','facebook.com/unknown')['id']
        with self.assertRaisesRegex(ValueError,'ID bằng số'):
            self.store.create(self.account,page,source,'','',time.time()+3600)
        self.assertEqual(self.store.list(),[])
        self.assertEqual(self.calls,[])

    def test_independent_page_manager_preparation_blocks_cancel_and_new_duplicate(self):
        row = self.schedule()
        scope = (self.account,self.page,row['post_id'])
        self.store.activity.transition(*scope,'preparing','fixture independent Page manager')
        self.store.activity.save_browser_review(*scope,dict(identity='fixture preview'))
        with self.assertRaisesRegex(ValueError,'cửa sổ Page'):
            self.controller.cancel(row['id'])
        with self.assertRaises(ValueError):
            self.store.create(self.account,self.page,row['source'],'','',time.time()+3600)
        self.assertFalse(self.store.activity.post(*scope)['archived'])
        row = self.ready_for_validation(row)
        self.assertEqual(self.calls,[])
        self.assertEqual(row['state'],'needs_attention')


if __name__ == '__main__':
    unittest.main()
