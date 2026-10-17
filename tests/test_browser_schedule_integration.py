"""Hidden Tk, synthetic campaign outputs, mocked browser workers and no live accounts."""
from pathlib import Path
import tempfile
import tkinter as tk
from tkinter import ttk
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from teodub.campaign_ui import CampaignTab
from teodub.common import atomic_json, hash_file
from teodub.facebook_ui import BrowserSession, FacebookTab
from teodub.meta_page_api import PageApiService
from teodub.page_activity import PageActivity
from teodub.page_schedule import campaign_video_source
from tests.test_page_activity_api import FakeVault


def rendered_campaign(data_dir, ident='a'*32, statuses=('completed','completed')):
    """Use the complete output mapping produced by pipeline.record_outputs."""
    directory = data_dir/'campaigns'/ident
    output_dir = data_dir/'outputs'/ident
    output_dir.mkdir(parents=True,exist_ok=True)
    items = []
    for number,status in enumerate(statuses,1):
        job = directory/'jobs'/f'{number:04}'
        outputs = {}
        for key,suffix in (('video','.mp4'),('subtitles','.srt'),('quality','-quality.json'),('plan','-plan.json')):
            path = output_dir/f'Video {number}{suffix}'
            path.write_bytes(f'Synthetic {key} {number} {ident}'.encode())
            outputs[key] = str(path)
        atomic_json(job/'state.json',dict(rendered=dict(outputs=outputs,
            hashes={key:hash_file(value) for key,value in outputs.items()})))
        items.append(dict(number=number,status=status,job=str(job),outputs=outputs,
            title=f'Synthetic video {number}',url=f'https://www.douyin.com/video/{number}',message=''))
    manifest = dict(id=ident,name='Synthetic campaign '+ident[:2],output_dir=str(output_dir),
        original_dir=str(data_dir/'originals'/ident),status='completed',items=items)
    atomic_json(directory/'campaign.json',manifest)
    return directory,manifest


class BrowserScheduleIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.notebook = ttk.Notebook(self.root)
        self.studio = SimpleNamespace(data_dir=self.directory,closing=False,closed=False,
            process=None,campaign_busy=False,login_pending=False,refresh_buttons=Mock(),
            fields={name:tk.StringVar(self.root,str(self.directory/name)) for name in ('original_dir','output_dir')})
        self.enterContext(patch('teodub.facebook_ui.create_page_bridge', return_value=Mock(enabled=True)))
        self.owner = FacebookTab(self.notebook,self.studio)
        self.studio.facebook_tab = self.owner
        self.a = self.owner.store.add_account('Synthetic A')['id']
        self.pa = self.owner.store.save_page(self.a,'Synthetic Page A','facebook.com/synthetic-a','100')['id']
        self.b = self.owner.store.add_account('Synthetic B')['id']
        self.pb = self.owner.store.save_page(self.b,'Synthetic Page B','facebook.com/synthetic-b','200')['id']
        self.activity = PageActivity(self.directory)
        self.owner.reload()
        self.owner.activate_account(self.b)
        # Campaign settings are unrelated to opening an already-rendered video menu.
        self.enterContext(patch.object(CampaignTab,'capture_settings',return_value=None))
        self.campaign = CampaignTab(self.notebook,self.studio)
        self.browser = self.enterContext(patch('teodub.facebook_browser.open_facebook_browser',return_value='finished'))
        self.thread = self.enterContext(patch('teodub.facebook_ui.threading.Thread',return_value=Mock()))
        self.process = self.enterContext(patch('teodub.campaign_ui.subprocess.Popen'))
        self.dialog = self.enterContext(patch('teodub.page_schedule_ui.ScheduleVideoDialog'))
        self.enterContext(patch.object(self.campaign.video_menu,'tk_popup',return_value=None))
        self.enterContext(patch.object(self.campaign.video_menu,'grab_release',return_value=None))

    def draft(self, account=None, page=None, mode='browser'):
        return self.activity.save_draft(account or self.a,page or self.pa,'Synthetic scheduled body',
            delivery_mode=mode,browser_finish_mode='tool')

    def session(self, account=None, *, opened=True, loading=False):
        account = account or self.a
        session = BrowserSession(account,'Synthetic '+('A' if account == self.a else 'B'),
            opened=opened,loading_pages=loading)
        self.owner.sessions[account] = session
        return session

    def campaign_rows(self, statuses=('completed','completed'), ident='a'*32):
        directory,manifest = rendered_campaign(self.directory,ident,statuses)
        self.campaign.directory = directory
        self.campaign.tree.delete(*self.campaign.tree.get_children())
        for item in manifest['items']:
            self.campaign.tree.insert('','end',iid=str(item['number']),
                values=(f'Video {item["number"]}',item['title'],item['url'],item['status'],'100%','—',''))
        return directory,manifest

    def right_click(self, number):
        with patch.object(self.campaign.tree,'identify_row',return_value=str(number)):
            self.campaign.video_context_menu(SimpleNamespace(y=1,x_root=1,y_root=1))

    def poll_owner(self):
        self.owner.after_cancel(self.owner.timer)
        self.owner.poll()

    def assert_no_publish_worker(self):
        self.browser.assert_not_called()
        self.thread.assert_not_called()
        self.process.assert_not_called()

    def test_context_menu_captures_clicked_completed_video_across_selection_and_campaign_change(self):
        directory,manifest = self.campaign_rows()
        self.right_click(1)
        self.assertEqual(str(self.campaign.video_menu.entrycget(0,'state')),'normal')
        self.assertEqual(self.campaign.tree.selection(),('1',))
        second,_ = rendered_campaign(self.directory,'b'*32)
        self.campaign.directory = second
        self.campaign.tree.selection_set('2')
        self.campaign.video_menu.invoke(0)
        self.dialog.assert_called_once()
        parent,owner,source = self.dialog.call_args.args
        self.assertIs(parent,self.campaign)
        self.assertIs(owner,self.owner)
        self.assertEqual(source['campaign'],str(directory))
        self.assertEqual(source['number'],1)
        self.assertEqual(source['path'],manifest['items'][0]['outputs']['video'])
        self.assertEqual(source['sha256'],hash_file(source['path']))
        self.assert_no_publish_worker()

    def test_unfinished_video_menu_stays_disabled_and_direct_invocation_does_not_create_schedule(self):
        for status in ('pending','running','review_required','failed','cancelled'):
            with self.subTest(status=status):
                self.campaign_rows(('completed',status))
                self.right_click(2)
                self.assertEqual(str(self.campaign.video_menu.entrycget(0,'state')),'disabled')
                self.campaign.schedule_selected_video()
                self.assertIn('Chỉ xếp lịch video đã hoàn tất',self.campaign.notice.get())
        self.dialog.assert_not_called()
        self.assert_no_publish_worker()

    def test_original_download_cannot_be_used_as_completed_campaign_output(self):
        directory,manifest = self.campaign_rows()
        original = self.directory/'originals'/'download.mp4'
        original.parent.mkdir(parents=True,exist_ok=True)
        original.write_bytes(b'Synthetic original download')
        item = manifest['items'][0]
        item['outputs']['video'] = str(original)
        atomic_json(Path(item['job'])/'state.json',dict(rendered=dict(outputs=item['outputs'],
            hashes={key:hash_file(value) for key,value in item['outputs'].items()})))
        atomic_json(directory/'campaign.json',manifest)
        self.right_click(1)
        self.assertEqual(str(self.campaign.video_menu.entrycget(0,'state')),'disabled')
        self.campaign.schedule_selected_video()
        self.assertIn('thành phẩm',self.campaign.notice.get())
        with self.assertRaises(ValueError):
            campaign_video_source(self.directory,directory,1)
        self.dialog.assert_not_called()
        self.assert_no_publish_worker()

    def test_explicit_account_starts_its_own_profile_and_queues_scoped_post_without_changing_selection(self):
        post = self.draft()
        other = self.session(self.b)
        request = self.owner.submit_page_browser(self.a,self.pa,post['id'],'prepare')
        self.assertTrue(request)
        session = self.owner.sessions[self.a]
        command = session.commands.get_nowait()
        self.assertEqual((command['account_id'],command['page_key'],command['post_id']),
            (self.a,self.pa,post['id']))
        self.assertEqual((command['action'],command['operation'],command['request_id']),('page_post','prepare',request))
        self.assertEqual(session.post_actions[request]['state'],'queued')
        self.assertTrue(other.commands.empty())
        self.assertEqual(self.owner.account_id,self.b)
        self.assertFalse(self.thread.call_args.kwargs['daemon'])
        self.browser.assert_not_called()
        self.thread.call_args.kwargs['target']()
        self.assertEqual(self.browser.call_args.args[0],self.owner.store.profile_directory(self.a))
        self.assertEqual(self.browser.call_args.kwargs['url'],post['target_url'])
        self.assertIs(self.browser.call_args.kwargs['commands'],session.commands)
        self.assertIs(self.owner.sessions[self.b],other)
        self.process.assert_not_called()

    def test_startup_and_page_scan_keep_request_in_original_session_without_relaunch(self):
        for opened,loading in ((False,False),(True,True)):
            with self.subTest(opened=opened,loading=loading):
                session = self.session(opened=opened,loading=loading)
                post = self.draft()
                request = self.owner.submit_page_browser(self.a,self.pa,post['id'],'prepare')
                self.assertTrue(request)
                command = session.commands.get_nowait()
                self.assertEqual(command['account_id'],self.a)
                self.assertEqual(command['request_id'],request)
                self.assertEqual(session.post_actions[request]['post_id'],post['id'])
                self.assertEqual(self.owner.account_id,self.b)
        self.assert_no_publish_worker()

    def test_wrong_account_result_is_rejected_and_cannot_claim_publication(self):
        session = self.session()
        post = self.draft()
        request = self.owner.submit_page_browser(self.a,self.pa,post['id'],'prepare')
        session.commands.get_nowait()
        wrong = dict(post,account_id=self.b,page_key=self.pb,state='published',
            external_url='https://www.facebook.com/synthetic-b/posts/77',message='Wrong account fixture')
        session.messages.put(('post_result',dict(request_id=request,operation='prepare',account_id=self.b,
            page_key=self.pb,post_id=post['id'],message=wrong['message'],result=wrong)))
        self.poll_owner()
        self.assertEqual(session.post_actions[request]['state'],'error')
        self.assertIn('không đúng tài khoản/Page',session.post_actions[request]['message'])
        self.assertEqual(self.activity.post(self.a,self.pa,post['id'])['state'],'draft')
        self.assertEqual(self.activity.post(self.a,self.pa,post['id'])['external_url'],'')
        self.assertTrue(session.commands.empty())
        self.assert_no_publish_worker()

    def test_wrong_operation_response_does_not_complete_pending_prepare(self):
        session = self.session()
        post = self.draft()
        request = self.owner.submit_page_browser(self.a,self.pa,post['id'],'prepare')
        session.commands.get_nowait()
        session.messages.put(('post_result',dict(request_id=request,operation='publish',account_id=self.a,
            page_key=self.pa,post_id=post['id'],message='Wrong phase',result=dict(post,state='published'))))
        self.poll_owner()
        self.assertEqual(session.post_actions[request]['state'],'queued')
        self.assertEqual(self.activity.post(self.a,self.pa,post['id'])['state'],'draft')
        self.assertTrue(session.commands.empty())
        self.assert_no_publish_worker()

    def test_browser_close_errors_pending_post_without_retry_or_touching_other_account(self):
        session = self.session()
        other = self.session(self.b)
        post = self.draft()
        request = self.owner.submit_page_browser(self.a,self.pa,post['id'],'prepare')
        session.commands.get_nowait()
        session.messages.put(('done','Synthetic browser closed'))
        self.poll_owner()
        self.assertEqual(session.post_actions[request]['state'],'error')
        self.assertIn('không tự gửi lại',session.post_actions[request]['message'])
        self.assertFalse(session.pending)
        self.assertTrue(other.pending)
        self.assertTrue(other.opened)
        self.assertFalse(other.cancel.is_set())
        self.assertFalse(other.finished.is_set())
        self.assertEqual(self.activity.post(self.a,self.pa,post['id'])['state'],'draft')
        self.assertTrue(session.commands.empty())
        self.assertTrue(other.commands.empty())
        self.assert_no_publish_worker()

    def test_closing_profile_rejects_post_and_active_request_cannot_queue_duplicate(self):
        session = self.session()
        post = self.draft()
        session.finished.set()
        self.assertIsNone(self.owner.submit_page_browser(self.a,self.pa,post['id'],'prepare'))
        self.assertTrue(session.commands.empty())
        session.finished.clear()
        first = self.owner.submit_page_browser(self.a,self.pa,post['id'],'prepare')
        self.assertTrue(first)
        self.assertIsNone(self.owner.submit_page_browser(self.a,self.pa,post['id'],'prepare'))
        self.assertEqual(session.commands.qsize(),1)
        self.assertEqual(list(session.post_actions),[first])
        self.assert_no_publish_worker()

    def test_foreign_page_or_api_draft_cannot_enter_browser_queue(self):
        self.session()
        post = self.draft()
        api = self.draft(mode='api')
        self.assertIsNone(self.owner.submit_page_browser(self.a,self.pb,post['id'],'prepare'))
        self.assertIsNone(self.owner.submit_page_browser(self.a,self.pa,api['id'],'prepare'))
        self.assertEqual(self.owner.sessions[self.a].post_actions,{})
        self.assertTrue(self.owner.sessions[self.a].commands.empty())
        self.assert_no_publish_worker()

    def test_api_service_rejects_browser_delivery_before_any_client_or_credential_calls(self):
        post = self.draft()
        factory = Mock()
        vault = FakeVault()
        vault.load = Mock(side_effect=AssertionError('No credential load for browser delivery'))
        api = PageApiService(self.directory,client_factory=factory,credentials=vault)
        for function in (api.publish,api.check_post):
            with self.assertRaisesRegex(ValueError,'Trình duyệt web'):
                function(self.a,self.pa,post['id'])
        factory.assert_not_called()
        vault.load.assert_not_called()
        self.assertEqual(self.activity.post(self.a,self.pa,post['id'])['state'],'draft')
        self.assert_no_publish_worker()

    def test_request_deadline_cancels_queued_command_and_ignores_late_success_message(self):
        session = self.session()
        post = self.draft()
        request = self.owner.submit_page_browser(self.a,self.pa,post['id'],'prepare')
        command = session.commands.get_nowait()
        self.assertIs(command['cancel_request'],session.post_actions[request]['cancel'])
        session.post_actions[request]['expires_at'] = 0
        self.poll_owner()
        self.assertTrue(command['cancel_request'].is_set())
        self.assertEqual(session.post_actions[request]['state'],'error')
        minutes = session.post_actions[request]['timeout_seconds']//60
        self.assertIn(f'{minutes} phút',session.post_actions[request]['message'])
        session.messages.put(('post_result',dict(request_id=request,operation='prepare',
            account_id=self.a,page_key=self.pa,post_id=post['id'],result=dict(post,state='published'))))
        self.poll_owner()
        self.assertEqual(session.post_actions[request]['state'],'error')
        self.assertEqual(self.activity.post(self.a,self.pa,post['id'])['state'],'draft')
        self.assertTrue(session.commands.empty())
        self.assert_no_publish_worker()

    def test_video_prepare_survives_old_six_minute_cutoff_then_cancels_at_shared_limit(self):
        from teodub.facebook_post_timing import UPLOAD_WAIT_SECONDS, VIDEO_PREPARATION_SECONDS
        video = self.directory/'slow-video.mp4'
        video.write_bytes(b'Synthetic selected video; no upload')
        post = self.activity.save_draft(self.a, self.pa, 'Video approved body', [str(video)],
            delivery_mode='browser', browser_finish_mode='tool')
        session = self.session()
        with patch('teodub.facebook_ui.time.monotonic', return_value=1000):
            request = self.owner.submit_page_browser(self.a, self.pa, post['id'], 'prepare')
        command = session.commands.get_nowait()
        action = session.post_actions[request]
        self.assertEqual(action['timeout_seconds'], VIDEO_PREPARATION_SECONDS)
        self.assertGreater(VIDEO_PREPARATION_SECONDS, UPLOAD_WAIT_SECONDS)
        self.assertEqual(command['expires_at'], 1000+VIDEO_PREPARATION_SECONDS)
        payload = dict(request_id=request, operation='prepare', account_id=self.a, page_key=self.pa,
            post_id=post['id'], message='Đang chờ Facebook tải/xử lý xong video')
        session.messages.put(('post_progress', payload))
        with patch('teodub.facebook_ui.time.monotonic', return_value=1000+UPLOAD_WAIT_SECONDS):
            self.poll_owner()
        self.assertEqual(action['state'], 'running')
        self.assertFalse(command['cancel_request'].is_set())
        self.assertEqual(self.activity.post(self.a, self.pa, post['id'])['state'], 'draft')
        with patch('teodub.facebook_ui.time.monotonic', return_value=1001+VIDEO_PREPARATION_SECONDS):
            self.poll_owner()
        self.assertEqual(action['state'], 'error')
        self.assertTrue(command['cancel_request'].is_set())
        self.assertIn('20 phút', action['message'])
        self.assertIn('Bước cuối: Đang chờ Facebook', action['message'])
        session.messages.put(('post_result', dict(payload, result=dict(post, state='published'))))
        self.poll_owner()
        self.assertEqual(action['state'], 'error')
        self.assertTrue(session.commands.empty())
        self.assert_no_publish_worker()

    def test_non_video_and_publish_review_keep_short_deadline(self):
        from teodub.facebook_post_timing import BROWSER_ACTION_SECONDS
        for operation in ('prepare', 'publish', 'review'):
            with self.subTest(operation=operation):
                session = self.session()
                post = self.draft()
                with patch('teodub.facebook_ui.time.monotonic', return_value=1000):
                    request = self.owner.submit_page_browser(self.a, self.pa, post['id'], operation)
                command = session.commands.get_nowait()
                self.assertEqual(command['expires_at'], 1000+BROWSER_ACTION_SECONDS)
                self.assertEqual(session.post_actions[request]['timeout_seconds'], BROWSER_ACTION_SECONDS)
        self.assert_no_publish_worker()


if __name__ == '__main__':
    unittest.main()
