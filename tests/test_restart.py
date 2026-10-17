"""Only synthetic jobs/processes. Never restart a user's desktop or post to FB."""
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock,patch

from teodub.common import atomic_json
from teodub.facebook_accounts import FacebookAccounts
from teodub.page_schedule import PageScheduleController
from teodub.restart import (AttachedProcess,process_identity,job_directory,checkpoint,restore_transfer,
    schedule_fingerprint,worker_fingerprint,accept_transfer,transfer_directory,RestartController,busy_reasons)


class RestartTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        registry = FacebookAccounts(self.root)
        self.account = registry.add_account('Synthetic')['id']
        self.page = registry.save_page(self.account,'Synthetic Page','facebook.com/profile.php?id=100','100')['id']
        self.campaign = NS(directory=None,process=None,scanning=False,chrome_login_pending=False,
            display=Mock(),save_campaign_preferences=Mock())
        self.studio = NS(data_dir=self.root,current=None,process=None,login_pending=False,stop_started=None,
            closed=False,closing=False,restart_pending=False,restart_draining=False,
            window=Mock(),timer='timer',batch_controller=NS(directory=None,process=None,busy=False),
            owned_config=lambda path: __import__('json').loads((Path(path)/'config.json').read_text()),
            read_events=Mock(),refresh_buttons=Mock(),save_preferences=Mock(),stop_playback=Mock(),status=Mock())
        bridge = NS(active=None,content_receiver=NS(pending=None),ai_controller=NS(pending=None))
        self.facebook = NS(studio=self.studio,page_bridge=bridge,store=registry,sessions={},manager_windows={},
            account_id=self.account,open_browser=Mock(),reload=Mock())
        self.scheduler = PageScheduleController(self.facebook)
        self.addCleanup(self.scheduler.close)
        self.facebook.page_scheduler = self.scheduler
        self.facebook.get_page_scheduler = Mock(return_value=self.scheduler)
        self.notebook = Mock()
        self.notebook.index.return_value = 1
        self.notebook.tabs.return_value = ('one','two','three')

    def saved_text(self):
        post = self.scheduler.store.activity.save_draft(self.account,self.page,'Synthetic post',[],
            delivery_mode='browser',browser_finish_mode='tool')
        return self.scheduler.store.create_post(self.account,self.page,post['id'],time.time()+3600)

    def synthetic_worker(self, seconds=3):
        process = subprocess.Popen([sys.executable,'-c',f'import time; time.sleep({seconds})'],
            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        def cleanup():
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=5)
        self.addCleanup(cleanup)
        return process

    def test_actual_process_reconnect_and_pid_creation_identity(self):
        process = self.synthetic_worker()
        identity = process_identity(process)
        attached = AttachedProcess(identity)
        self.assertIsNone(attached.poll())
        with self.assertRaises(ValueError):
            AttachedProcess(dict(identity,created='different process'))
        process.wait(timeout=5)
        self.assertIsNotNone(attached.poll())
        self.assertIsNone(AttachedProcess(process_identity(NS(pid=os.getpid())),control=False).poll())

    def test_checkpoint_reconnects_same_worker_without_spawning_or_clearing_stops(self):
        directory = self.root/'runs'/('a'*32)
        config = dict(directory=str(directory),mode='full',stop_file=str(directory/'stop.request'))
        atomic_json(directory/'config.json',config)
        self.studio.current = config
        self.studio.process = self.synthetic_worker()
        data = checkpoint(self.studio,self.campaign,self.facebook,self.notebook,[])
        pid = self.studio.process.pid
        self.studio.process = None
        (directory/'stop.request').touch()  # A user's stop must survive handoff.
        with patch('teodub.restart.subprocess.Popen') as spawn:
            result = restore_transfer(self.studio,self.campaign,self.facebook,self.notebook,dict(checkpoint=data))
        self.assertEqual(self.studio.process.pid,pid)
        self.assertIsNone(self.studio.process.poll())
        self.assertTrue((directory/'stop.request').exists())
        self.assertTrue(any('tiến trình cũ' in r for r in result))
        spawn.assert_not_called()

    def test_changed_worker_config_never_restarts_or_attaches(self):
        directory = self.root/'runs'/('a'*32)
        atomic_json(directory/'config.json',dict(directory=str(directory),mode='full'))
        self.studio.current = dict(directory=str(directory))
        self.studio.process = self.synthetic_worker()
        data = checkpoint(self.studio,self.campaign,self.facebook,self.notebook,[])
        atomic_json(directory/'config.json',dict(directory=str(directory),mode='captions'))
        attach = Mock()
        self.studio.process = None
        result = restore_transfer(self.studio,self.campaign,self.facebook,self.notebook,dict(checkpoint=data),attach=attach)
        attach.assert_not_called()
        self.assertIsNone(self.studio.process)
        self.assertIn('Cấu hình công việc đã đổi',' '.join(result))

    def test_only_owned_armed_schedule_restored_without_publishing(self):
        row = self.saved_text()
        self.scheduler.start(row['id'])
        data = checkpoint(self.studio,self.campaign,self.facebook,self.notebook,[])
        self.scheduler.close()
        self.scheduler = PageScheduleController(self.facebook)
        self.addCleanup(self.scheduler.close)
        self.facebook.page_scheduler = self.scheduler
        self.facebook.get_page_scheduler.return_value = self.scheduler
        with patch.object(self.scheduler,'submit') as submit:
            restore_transfer(self.studio,self.campaign,self.facebook,self.notebook,dict(checkpoint=data))
        self.assertEqual(self.scheduler.active,{row['id']})
        self.assertEqual(self.scheduler.store.get(row['id'])['state'],'armed')
        submit.assert_not_called()

    def test_attempted_or_changed_or_past_schedule_never_replayed(self):
        row = self.saved_text()
        self.scheduler.start(row['id'])
        data = checkpoint(self.studio,self.campaign,self.facebook,self.notebook,[])
        self.scheduler.pause(row['id'])
        self.scheduler.store.update(row['id'],attempted_at='uncertain result',state='needs_attention')
        result = restore_transfer(self.studio,self.campaign,self.facebook,self.notebook,dict(checkpoint=data))
        self.assertFalse(self.scheduler.active)
        self.assertIn('chưa rõ kết quả',' '.join(result))
        self.scheduler.store.update(row['id'],attempted_at='',state='paused',due_at=time.time()-1)
        result = restore_transfer(self.studio,self.campaign,self.facebook,self.notebook,dict(checkpoint=data))
        self.assertFalse(self.scheduler.active)
        self.assertIn('đã đổi',' '.join(result))

    def test_controller_waits_for_ai_and_never_cancels_or_reissues_current_work(self):
        self.facebook.page_bridge.ai_controller.pending = dict(id='inference')
        controller = RestartController(self.studio,self.campaign,self.facebook,self.notebook,Mock())
        with patch('teodub.restart.subprocess.Popen') as spawn:
            controller.request()
        self.assertEqual(controller.phase,'waiting')
        self.assertTrue(self.studio.restart_pending)
        self.assertIn('AI đang',controller.status.set.call_args.args[0])
        spawn.assert_not_called()
        controller.cancel()
        self.assertFalse(self.studio.restart_pending)
        self.assertIsNotNone(self.facebook.page_bridge.ai_controller.pending)

    def test_restart_waits_for_background_verification_without_cancelling_it(self):
        request = dict(state='verifying',cancel=Mock())
        self.facebook.sessions[self.account] = NS(pending=True,opened=True,loading_pages=False,
            navigation={},page_load_requests={},post_actions={'receipt':request})
        controller = RestartController(self.studio,self.campaign,self.facebook,self.notebook,Mock())
        with patch('teodub.restart.subprocess.Popen') as spawn:
            controller.request()
        self.assertEqual(controller.phase,'waiting')
        self.assertIn('Facebook đang thao tác',controller.status.set.call_args.args[0])
        self.assertEqual(request['state'],'verifying')
        request['cancel'].set.assert_not_called()
        spawn.assert_not_called()
        controller.cancel()

    def test_verifying_schedule_without_request_id_blocks_restart(self):
        row = self.saved_text()
        self.scheduler.start(row['id'])
        self.scheduler.store.update(row['id'],state='verifying',phase='verify',request_id='',
            attempted_at='Synthetic submission boundary')
        reasons = busy_reasons(self.studio,self.campaign,self.facebook)
        self.assertIn('lượt đăng đang xử lý; chờ bằng chứng hoàn tất',reasons)

    def test_spawn_failure_keeps_current_window_worker_and_schedule(self):
        row = self.saved_text()
        self.scheduler.start(row['id'])
        controller = RestartController(self.studio,self.campaign,self.facebook,self.notebook,Mock())
        with patch('teodub.restart.subprocess.Popen',side_effect=OSError('synthetic startup failure')):
            controller.request()
        self.assertEqual(controller.phase,'idle')
        self.assertFalse(self.studio.restart_pending)
        self.studio.window.destroy.assert_not_called()
        self.assertEqual(self.scheduler.active,{row['id']})

    def test_cancel_releases_quiescence_even_when_disk_is_full(self):
        controller = RestartController(self.studio,self.campaign,self.facebook,self.notebook,Mock())
        controller.phase = 'waiting'
        controller.folder = transfer_directory(self.root,'e'*32)
        self.studio.restart_pending = self.studio.restart_draining = True
        with patch('teodub.restart.atomic_json',side_effect=OSError('disk full')):
            controller.cancel()
        self.assertFalse(self.studio.restart_pending)
        self.assertFalse(self.studio.restart_draining)
        self.assertEqual(controller.phase,'idle')

    def test_commit_saves_exact_handoff_without_stop_or_terminate(self):
        row = self.saved_text()
        self.scheduler.start(row['id'])
        controller = RestartController(self.studio,self.campaign,self.facebook,self.notebook,Mock())
        controller.folder = transfer_directory(self.root,'e'*32)
        atomic_json(controller.folder/'transfer.json',dict(token='e'*32,state='preflight'))
        self.studio.process = None
        controller.commit()
        saved = __import__('json').loads((controller.folder/'transfer.json').read_text())
        self.assertEqual(saved['state'],'committed')
        self.assertEqual(saved['checkpoint']['schedules'][0]['fingerprint'],schedule_fingerprint(row))
        self.studio.window.destroy.assert_called_once()
        self.studio.save_preferences.assert_called_once()
        self.assertEqual(self.scheduler.active,{row['id']})

    def test_transfer_one_use_expiry_and_parent_binding(self):
        token = 'f'*32
        folder = transfer_directory(self.root,token)
        envelope = dict(version=1,token=token,state='preflight',data_dir=str(self.root),created_at=time.time(),
            parent=process_identity(NS(pid=os.getpid())))
        atomic_json(folder/'transfer.json',envelope)
        def ack(path,value):
            atomic_json(path,value)
            if path.name=='ready.json':
                atomic_json(folder/'transfer.json',dict(envelope,state='committed',checkpoint={}))
        with patch('teodub.restart.AttachedProcess') as parent,patch('teodub.restart.atomic_json',side_effect=ack):
            parent.return_value.poll.return_value = 0
            self.assertEqual(accept_transfer(self.root,token)['state'],'claimed')
        self.assertEqual(__import__('json').loads((folder/'transfer.json').read_text())['state'],'claimed')
        with self.assertRaises(ValueError):
            accept_transfer(self.root,token)
        atomic_json(folder/'transfer.json',dict(envelope,created_at=time.time()-601))
        with self.assertRaises(ValueError):
            accept_transfer(self.root,token)

    def test_path_and_token_cannot_point_outside_studio(self):
        with self.assertRaises(ValueError):
            job_directory(self.root,self.root/'other'/('a'*32),'runs')
        with self.assertRaises(ValueError):
            transfer_directory(self.root,'../other')

    def test_campaign_and_batch_progress_changes_do_not_change_frozen_identity(self):
        directory = self.root/'campaigns'/('a'*32)
        job = directory/'jobs'/'0001'
        atomic_json(job/'config.json',dict(directory=str(job)))
        from teodub.common import hash_file
        manifest = dict(schema=1,id=directory.name,name='Synthetic',created_at='2026-10-10',
            settings={},original_dir=str(self.root/'in'),output_dir=str(self.root/'out'),status='running',
            items=[dict(number=1,title='Synthetic',url='https://v.douyin.com/fixture/',job=str(job),
                config_sha256=hash_file(job/'config.json'),status='running',message='10%',outputs={})])
        atomic_json(directory/'campaign.json',manifest)
        identity = worker_fingerprint(directory,'campaign')
        manifest['items'][0].update(message='90%',status='completed')
        atomic_json(directory/'campaign.json',manifest)
        self.assertEqual(worker_fingerprint(directory,'campaign'),identity)
        batch = self.root/'campaign-batches'/('b'*32)
        queue = dict(id=batch.name,status='running',campaigns=[dict(directory=str(directory),name='Synthetic',
            config_identity=identity,status='running',message='10%')])
        atomic_json(batch/'queue.json',queue)
        identity = worker_fingerprint(batch,'batch')
        queue['campaigns'][0].update(status='completed',message='100%')
        atomic_json(batch/'queue.json',queue)
        self.assertEqual(worker_fingerprint(batch,'batch'),identity)
        # Changing the displayed campaign must not retarget the running worker.
        other = self.root/'campaigns'/('c'*32)
        other.mkdir(parents=True)
        self.campaign.directory, self.campaign.worker_directory = other,directory
        self.campaign.process = self.synthetic_worker()
        saved = checkpoint(self.studio,self.campaign,self.facebook,self.notebook,[])
        self.assertEqual(saved['workers'][0]['directory'],str(directory))
        self.assertEqual(saved['campaign_directory'],str(other))

    def test_open_editor_blocks_profile_flush_without_closing_browser(self):
        from teodub.restart import browser_restart_check
        tab = Mock()
        tab.url = 'https://www.facebook.com/'
        tab.is_closed.return_value = False
        tab.evaluate.return_value = True
        context = NS(pages=[tab])
        with patch('teodub.facebook_page_identity._can_switch'):
            with self.assertRaisesRegex(ValueError,'đang soạn'):
                browser_restart_check(context)
        tab.close.assert_not_called()
        tab.evaluate.return_value = False
        with patch('teodub.facebook_page_identity._can_switch'):
            browser_restart_check(context)

    def test_real_hidden_gui_handoff_keeps_worker_and_rearms_future_schedule(self):
        # Separate, hidden test desktops use only this TemporaryDirectory and local mailboxes.
        row = self.saved_text()
        parent_script = self.root/'parent.py'
        child_script = self.root/'child.py'
        worker_script = self.root/'worker.py'
        worker_script.write_text('import time,os\nfrom pathlib import Path\nfrom types import SimpleNamespace\n'
            'from teodub.common import protect_worker,atomic_json\nfrom teodub.restart import process_identity\n'
            'protect_worker()\natomic_json(Path(__file__).with_name("worker-native.json"),'
            'process_identity(SimpleNamespace(pid=os.getpid())))\ntime.sleep(30)\n',encoding='utf-8')
        parent_script.write_text(r'''
import sys,subprocess,json
from pathlib import Path
from unittest.mock import patch
from tkinter import ttk
from teodub import app
from teodub.common import atomic_json
from teodub.restart import process_identity
from teodub.page_command_bridge import PageCommandBridge
import teodub.restart as restart
rootdir=Path(sys.argv[1]); project=Path(sys.argv[2]); sys.path.insert(0,str(project))
directory=rootdir/'runs'/('d'*32)
config=dict(directory=str(directory),stop_file=str(directory/'stop.request'),mode='full',
 original_dir=str(rootdir/'original'),output_dir=str(rootdir/'out'))
atomic_json(directory/'config.json',config)
process=subprocess.Popen([sys.executable,str(rootdir/'worker.py')],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
atomic_json(rootdir/'worker-identity.json',process_identity(process))
orig_init=app.Studio.__init__; real_tk=app.tk.Tk
def hidden():
 root=real_tk(); root.withdraw(); root.after(16000,root.destroy); return root
def mounted(self,*args,**kwargs):
 orig_init(self,*args,**kwargs); self.current=config; self.process=process
 def run():
  fb=self.facebook_tab
  for row in fb.get_page_scheduler().store.list(): fb.page_scheduler.start(row['id'])
  self.restart_controller.request()
 self.window.after(500,run)
def command(data_dir,token,web_data_dir=None):
 return [sys.executable,'-X','utf8',str(rootdir/'child.py'),str(project),'--data-dir',str(data_dir),
  '--web-data-dir',str(rootdir/'web'),'--restart-token',token]
with patch.object(app.tk,'Tk',side_effect=hidden),patch.object(app.Studio,'__init__',mounted), \
 patch('teodub.facebook_ui.create_page_bridge',side_effect=lambda owner:PageCommandBridge(owner)), \
 patch.object(restart,'restart_command',side_effect=command):
 app.main(['--data-dir',str(rootdir),'--web-data-dir',str(rootdir/'web')])
''',encoding='utf-8')
        child_script.write_text(r'''
import sys,json
from pathlib import Path
from unittest.mock import patch
project=Path(sys.argv[1]);sys.path.insert(0,str(project))
from teodub import app
from teodub.common import atomic_json
from teodub.page_command_bridge import PageCommandBridge
real_tk=app.tk.Tk;capture={}
def hidden():
 root=real_tk();root.withdraw();root.deiconify=lambda:None;return root
orig_init=app.Studio.__init__
def mounted(self,*args,**kwargs):
 orig_init(self,*args,**kwargs)
 def done():
  atomic_json(self.data_dir/'gui-restored.json',dict(pid=self.process.pid,alive=self.process.poll() is None,
   schedules=[r['state'] for r in self.facebook_tab.get_page_scheduler().store.list()]))
  self.window.destroy()
 self.window.after(800,done)
with patch.object(app.tk,'Tk',side_effect=hidden),patch.object(app.Studio,'__init__',mounted), \
 patch('teodub.facebook_ui.create_page_bridge',side_effect=lambda owner:PageCommandBridge(owner)):
 app.main(sys.argv[2:])
''',encoding='utf-8')
        project = Path(__file__).resolve().parents[1]
        with (self.root/'parent.log').open('wb') as log:
            parent = subprocess.Popen([sys.executable,'-X','utf8',str(parent_script),str(self.root),str(project)],
                cwd=str(project),env=dict(os.environ,PYTHONPATH=str(project)),stdout=log,stderr=log)
        self.addCleanup(lambda: parent.terminate() if parent.poll() is None else None)
        identity_path = self.root/'worker-identity.json'
        def cleanup_worker():
            for path in (self.root/'worker-native.json',identity_path):
                if path.exists():
                    try:
                        AttachedProcess(__import__('json').loads(path.read_text())).terminate()
                    except ValueError:
                        pass
        self.addCleanup(cleanup_worker)
        parent.wait(timeout=20)
        deadline = time.monotonic()+10
        output = self.root/'gui-restored.json'
        while not output.exists() and time.monotonic()<deadline:
            time.sleep(.1)
        logs = (self.root/'parent.log').read_text(errors='replace')
        for path in (self.root/'restart-transfers').glob('*/startup.log'):
            logs += path.read_text(errors='replace')
            if path.with_name('transfer.json').exists():
                logs += path.with_name('transfer.json').read_text(errors='replace')
        self.assertTrue(output.exists(),logs)
        result = __import__('json').loads(output.read_text())
        identity = __import__('json').loads(identity_path.read_text())
        self.assertEqual(result['pid'],identity['pid'])
        self.assertTrue(result['alive'])
        self.assertEqual(result['schedules'],['armed'])
        self.assertIsNone(AttachedProcess(identity).poll())
        # Wait for the test child to finish its normal Tk cleanup before removing fixture files.
        transfer = next((self.root/'restart-transfers').glob('*/transfer.json'))
        saved = __import__('json').loads(transfer.read_text(encoding='utf-8'))
        self.assertEqual(saved['state'],'restored')
        ready = __import__('json').loads(transfer.with_name('ready.json').read_text())
        try:
            child = AttachedProcess(ready['process'],control=False)
        except ValueError:
            return  # The synthetic child has already completed normal cleanup.
        deadline = time.monotonic()+5
        while child.poll() is None and time.monotonic()<deadline:
            time.sleep(.1)


if __name__=='__main__':
    unittest.main()
