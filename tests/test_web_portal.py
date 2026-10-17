import concurrent.futures
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import uuid

from teodub.common import atomic_json, hash_file
from teodub.pipeline import _job_lock
from teodub_web.app import create_app
from teodub_web.runner import PROJECT, Runner, config_for, describe, validate_spec, verified_output
from teodub_web.server import initialize
from teodub_web.store import Store

URL = 'https://www.douyin.com/video/1964367600664655903'


class PortalTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)
        self.password = 'a-secure-test-password-1'
        self.admin_id = self.store.add_user('admin', 'Admin', self.password, 'admin')
        self.staff_id = self.store.add_user('staff', 'Staff', self.password)
        self.other_id = self.store.add_user('other', 'Other', self.password)
        self.settings = dict(secret='test-secret', drive_folder=str(Path(self.tmp.name)/'drive'),
                             execution_lock=str(Path(self.tmp.name)/'execution'))
        Path(self.settings['drive_folder']).mkdir()
        self.app = create_app(self.store, self.settings)
        self.app.config['TESTING'] = True
        self.client = self.app.test_client()
        self.login()

    def tearDown(self):
        self.tmp.cleanup()

    def csrf(self, client=None):
        with (client or self.client).session_transaction() as session:
            return session['csrf']

    def login(self, username='staff', client=None):
        client = client or self.client
        client.get('/login')
        return client.post('/login', data=dict(username=username, password=self.password, csrf=self.csrf(client)))

    def post(self, path, data=None, client=None):
        client = client or self.client
        return client.post(path, json=data or {}, headers={'X-CSRF-Token': self.csrf(client)})

    def submit(self, client=None, **values):
        return self.post('/api/jobs', {'input':URL, 'submit_key':uuid.uuid4().hex, **values}, client)

    def fixture(self, ident):
        job = self.store.job(ident)
        config = config_for(self.store, job, self.settings)
        directory = Path(config['directory'])
        outputs = Path(config['output_dir'])
        directory.mkdir(parents=True, exist_ok=True)
        outputs.mkdir(parents=True, exist_ok=True)
        video, srt = outputs/'result.mp4', outputs/'result.srt'
        video.write_bytes(b'verified-video-fixture')
        srt.write_text('1\n00:00:00,000 --> 00:00:01,000\nXin chào\n', encoding='utf-8')
        out = dict(video=str(video), subtitles=str(srt))
        atomic_json(directory/'state.json', dict(rendered=dict(outputs=out, hashes={k: hash_file(p) for k,p in out.items()})))
        atomic_json(directory/'result.json', dict(status='completed', outputs=out, message='Đã xuất video.'))
        self.store.set_state(ident, 'completed', 'Đã xuất video.')
        return config, out

    def test_login_required_for_jobs_files_team(self):
        client = self.app.test_client()
        self.assertEqual(client.get('/api/jobs').status_code, 401)
        self.assertEqual(client.get('/files/anything/video').status_code, 401)
        self.assertEqual(client.get('/team').status_code, 302)

    def test_csrf_required_and_bad_origin_rejected(self):
        self.assertEqual(self.client.post('/api/jobs', json={'input': URL}).status_code, 403)
        self.assertEqual(self.client.post('/api/jobs', json={'input': URL},
                         headers={'X-CSRF-Token': self.csrf(), 'Origin': 'https://evil.test'}).status_code, 403)

    def test_employee_only_own_jobs_admin_all(self):
        first = self.submit().json['id']
        other = self.app.test_client(); self.login('other', other)
        second = self.submit(other).json['id']
        self.assertEqual([x['id'] for x in self.client.get('/api/jobs').json['jobs']], [first])
        self.assertIsNone(self.store.job(second, dict(id=self.staff_id,role='staff')))
        admin = self.app.test_client(); self.login('admin', admin)
        self.assertEqual(len(admin.get('/api/jobs').json['jobs']), 2)

    def test_employee_cannot_change_other_jobs(self):
        other = self.app.test_client(); self.login('other', other)
        ident = self.submit(other).json['id']
        self.assertEqual(self.post(f'/api/jobs/{ident}/stop').status_code, 404)
        self.assertEqual(self.store.job(ident)['state'], 'queued')

    def test_duplicate_submission_one_job(self):
        data = dict(input=URL, submit_key=uuid.uuid4().hex)
        a,b = self.post('/api/jobs',data), self.post('/api/jobs',data)
        self.assertEqual(a.json['id'], b.json['id'])
        self.assertEqual(len(self.store.jobs()), 1)

    def test_concurrent_submit_and_claim(self):
        key=uuid.uuid4().hex; _,title,spec=validate_spec(dict(input=URL))
        with concurrent.futures.ThreadPoolExecutor(4) as pool:
            ids=list(pool.map(lambda _:self.store.submit(self.staff_id,title,URL,spec,key), range(4)))
        self.assertEqual(len(set(ids)),1)
        with concurrent.futures.ThreadPoolExecutor(4) as pool:
            claims=list(pool.map(lambda _:self.store.claim(),range(4)))
        self.assertEqual(sum(bool(x) for x in claims),1)

    def test_config_uses_server_paths_existing_execution_lock(self):
        ident=self.submit(voice='Adam bựa',burn_subtitles=True,original_gain=0,voice_gain=1.5).json['id']
        config=config_for(self.store,self.store.job(ident),self.settings)
        self.assertEqual(config['voice'],'Adam bựa')
        self.assertEqual(config['execution_lock'],self.settings['execution_lock'])
        self.assertTrue(Path(config['directory']).is_relative_to(self.store.root))
        self.assertEqual(config['drive_upload']['folder_path'],self.settings['drive_folder'])
        self.assertEqual(config['original_gain'],0)

    def test_reject_local_urls_paths_and_injected_options(self):
        for value in ['http://127.0.0.1/private', 'http://localhost/a', 'file:///C:/secret', r'C:\secret.mp4',
                      'https://www.douyin.com.evil.test/video/1','https://www.douyin.com:8888/video/1']:
            with self.subTest(value=value):
                self.assertEqual(self.submit(input=value).status_code,400)
        for key,value in [('directory',r'C:\secret'),('voice_provider','capcut'),('reference_audio','secret'),('execution_lock','new')]:
            self.assertEqual(self.submit(**{key:value}).status_code,400)

    def test_invalid_options_and_types(self):
        for values in [dict(title=42),dict(title='a'*101),dict(subtitle_size=True),dict(subtitle_size=99),
                       dict(original_gain=3),dict(voice_gain='1'),dict(speed=True),dict(voice_speed=4),
                       dict(save_drive='yes'),dict(burn_subtitles='true'),dict(voice='unknown'),
                       dict(tts_model='v3nano',voice='Adam bựa'),dict(translation_model='injected')]:
            with self.subTest(values=values):
                self.assertEqual(self.submit(**values).status_code,400)

    def test_per_user_queue_limit(self):
        for _ in range(10): self.assertEqual(self.submit().status_code,201)
        self.assertEqual(self.submit().status_code,400)

    def test_queue_stop_and_resume_keep_spec(self):
        ident=self.submit().json['id']; before=self.store.job(ident)['spec']
        self.assertEqual(self.post(f'/api/jobs/{ident}/stop').status_code,200)
        self.assertEqual(self.store.job(ident)['state'],'cancelled')
        self.assertEqual(self.post(f'/api/jobs/{ident}/retry').status_code,200)
        self.assertEqual(self.store.job(ident)['spec'],before)

    def test_running_stop_publishes_marker(self):
        ident=self.submit().json['id']; self.store.claim()
        self.post(f'/api/jobs/{ident}/stop')
        self.assertTrue((self.store.root/'jobs'/ident/'stop.request').exists())
        self.assertEqual(self.store.job(ident)['state'],'stopping')

    def test_completed_cannot_rerun(self):
        ident=self.submit().json['id'];self.fixture(ident)
        self.assertEqual(self.post(f'/api/jobs/{ident}/retry').status_code,409)

    def test_verified_download_and_cross_user_block(self):
        ident=self.submit().json['id'];self.fixture(ident)
        response=self.client.get(f'/files/{ident}/video')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.data,b'verified-video-fixture')
        self.assertIn('attachment',response.headers['Content-Disposition']); response.close()
        other=self.app.test_client();self.login('other',other)
        self.assertEqual(other.get(f'/files/{ident}/video').status_code,404)
        self.assertEqual(self.client.get(f'/files/{ident}/quality').status_code,404)

    def test_tampered_video_and_external_output_refused(self):
        ident=self.submit().json['id'];cfg,out=self.fixture(ident)
        Path(out['video']).write_bytes(b'tampered')
        self.assertEqual(self.client.get(f'/files/{ident}/video').status_code,404)
        external=self.store.root/'private.mp4'; external.write_bytes(b'private')
        out['video']=str(external)
        atomic_json(Path(cfg['directory'])/'result.json',dict(status='completed',outputs=out))
        atomic_json(Path(cfg['directory'])/'state.json',dict(rendered=dict(outputs=out,hashes={k:hash_file(p) for k,p in out.items()})))
        self.assertEqual(self.client.get(f'/files/{ident}/video').status_code,404)

    def test_range_download(self):
        ident=self.submit().json['id'];self.fixture(ident)
        response=self.client.get(f'/files/{ident}/video',headers={'Range':'bytes=0-7'})
        self.assertEqual(response.status_code,206);self.assertEqual(response.data,b'verified');response.close()

    def test_no_config_or_credentials_endpoints(self):
        for path in ['/settings.json','/portal.sqlite3','/TAI_KHOAN_QUAN_TRI.txt','/../settings.json']:
            self.assertEqual(self.client.get(path).status_code,404)

    def test_host_and_secure_cookie(self):
        self.assertEqual(self.client.get('/login',headers={'Host':'evil.test'}).status_code,400)
        client=self.app.test_client()
        response=client.get('/login',headers={'X-Forwarded-Proto':'https'})
        cookie=response.headers['Set-Cookie'];self.assertIn('Secure',cookie);self.assertIn('HttpOnly',cookie);self.assertIn('SameSite=Lax',cookie)

    def test_account_change_revokes_old_sessions(self):
        another=self.app.test_client();self.login('staff',another)
        response=self.client.post('/account',data=dict(password='new-password-123456',confirm='new-password-123456',csrf=self.csrf()))
        self.assertEqual(response.status_code,302)
        self.assertEqual(another.get('/api/jobs').status_code,401)

    def test_admin_create_and_disable_user(self):
        self.assertEqual(self.client.get('/team').status_code,403)
        self.login('admin')
        response=self.client.post('/team',data=dict(action='create',name='New colleague',username='newstaff',csrf=self.csrf()))
        self.assertEqual(response.status_code,200);self.assertIn(b'newstaff',response.data)
        self.client.post('/team',data=dict(action='disable',user_id=self.other_id,csrf=self.csrf()))
        with self.assertRaises(ValueError):self.store.authenticate('other',self.password,'test')

    def test_brute_force_gate(self):
        for _ in range(5):
            with self.assertRaises(ValueError):self.store.authenticate('staff','wrong','attempt-ip')
        with self.assertRaisesRegex(ValueError,'10 phút'):self.store.authenticate('staff',self.password,'new-ip')

    def test_templates_and_xss_escaped(self):
        for url in ['/','/account']:
            response=self.client.get(url);self.assertEqual(response.status_code,200)
            self.assertIn("script-src 'self'",response.headers['Content-Security-Policy'])
        self.submit(title='<script>alert(1)</script>')
        self.assertIn('<script>',self.client.get('/api/jobs').json['jobs'][0]['title'])
        # JS uses textContent; never interpolates this JSON into HTML.
        source=(PROJECT/'teodub_web'/'static'/'portal.js').read_text(encoding='utf-8')
        self.assertNotIn('innerHTML',source)

    def test_drive_staged_never_claims_cloud(self):
        ident=self.submit().json['id'];cfg,out=self.fixture(ident)
        atomic_json(Path(cfg['directory'])/'drive-upload.json',dict(state='staged',cloud_confirmed=False))
        desc=describe(self.store,self.store.job(ident))
        self.assertEqual(desc['drive'],'Đã chép vào thư mục Drive')
        self.assertNotIn('cloud_confirmed',desc)

    def test_runner_mock_completion_and_busy_retry(self):
        ident=self.submit().json['id'];job=self.store.claim()
        class Process:
            def poll(self):return 0
            def wait(self):return 0
        def launcher(path,log):
            self.fixture(ident);return Process()
        runner=Runner(self.store,self.settings,launcher)
        runner.run_one(job)
        self.assertEqual(self.store.job(ident)['state'],'completed')
        second=self.submit().json['id'];job=self.store.claim()
        class Busy:
            def poll(self):return 75
            def wait(self):return 75
        runner=Runner(self.store,self.settings,lambda *a:Busy());runner.stop.set()
        runner.run_one(job)
        self.assertEqual(self.store.job(second)['state'],'queued')

    def test_restart_recovery_preserves_outputs_and_marks_interruption(self):
        first=self.submit().json['id'];self.store.claim();self.fixture(first)
        self.store.set_state(first,'running')
        runner=Runner(self.store,self.settings);runner.recover()
        self.assertEqual(self.store.job(first)['state'],'completed')
        second=self.submit().json['id'];self.store.claim();runner.recover()
        self.assertEqual(self.store.job(second)['state'],'interrupted')

    def test_recovery_does_not_reclaim_live_worker(self):
        ident=self.submit().json['id'];self.store.claim()
        directory=self.store.root/'jobs'/ident;directory.mkdir(parents=True)
        with _job_lock(directory):Runner(self.store,self.settings).recover()
        self.assertEqual(self.store.job(ident)['state'],'running')

    def test_resume_uses_frozen_config_after_settings_change(self):
        ident=self.submit().json['id'];job=self.store.claim();cfg=config_for(self.store,job,self.settings)
        directory=Path(cfg['directory']);directory.mkdir(parents=True);atomic_json(directory/'config.json',cfg)
        new=dict(self.settings,drive_folder=str(self.store.root/'another'))
        class Process:
            def poll(self):return 0
            def wait(self):return 0
        def launcher(path,log):
            self.assertEqual(json.loads(path.read_text(encoding='utf-8')),cfg)
            self.fixture(ident);return Process()
        Runner(self.store,new,launcher).run_one(job)

    def test_bootstrap_keeps_accounts_on_update(self):
        with tempfile.TemporaryDirectory() as root:
            a,settings=initialize(root);credential=Path(root)/'TAI_KHOAN_QUAN_TRI.txt';before=credential.read_bytes()
            b,after=initialize(root)
            self.assertEqual(settings,after);self.assertEqual(a.users(),b.users());self.assertEqual(before,credential.read_bytes())

    @unittest.skipUnless(os.name=='nt','Original worker is Windows-only')
    def test_original_worker_process_honors_shared_execution_lock(self):
        ident=self.submit().json['id'];job=self.store.claim();cfg=config_for(self.store,job,self.settings)
        directory=Path(cfg['directory']);directory.mkdir(parents=True);atomic_json(directory/'config.json',cfg)
        execution=Path(cfg['execution_lock']);execution.mkdir()
        with _job_lock(execution), (directory/'worker.log').open('ab') as log:
            process=Runner(self.store,self.settings).launch(directory/'config.json',log)
            self.assertEqual(process.wait(timeout=20),75)
        self.assertFalse((directory/'result.json').exists())

    @unittest.skipUnless(os.name=='nt','Original worker is Windows-only')
    def test_original_worker_cancelled_without_ai_or_network(self):
        ident=self.submit().json['id'];job=self.store.claim();directory=self.store.root/'jobs'/ident
        directory.mkdir(parents=True);(directory/'stop.request').touch()
        Runner(self.store,self.settings).run_one(job)
        self.assertEqual(self.store.job(ident)['state'],'cancelled')
        self.assertFalse((directory/'captions').exists())


if __name__=='__main__':unittest.main()
