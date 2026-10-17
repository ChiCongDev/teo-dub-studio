"""Web rectangles bind to owned, verified sources without running costly engines."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from tests import test_web_portal, test_pipeline
from teodub.common import atomic_json, hash_file
from teodub_web.runner import config_for, describe, validate_spec, Runner


class WebSourceCleanTests(unittest.TestCase):
    def setUp(self):
        self.f = test_web_portal.PortalTest()
        self.f.setUp()
        self.addCleanup(self.f.tearDown)
        self.store, self.client = self.f.store, self.f.client

    def prepared(self, **kwargs):
        response = self.f.submit(prepare_only=True, **kwargs)
        self.assertEqual(response.status_code, 201, response.json)
        ident = response.json['id']
        config = config_for(self.store, self.store.job(ident), self.f.settings)
        directory = Path(config['directory'])
        directory.mkdir(parents=True)
        source = self.store.root/'originals'/ident/'original.mp4'
        source.parent.mkdir(parents=True)
        source.write_bytes(b'original-video-fixture')
        atomic_json(directory/'config.json', config)
        atomic_json(directory/'state.json', {'source':dict(path=str(source), sha256=hash_file(source),
            width=320, height=240, duration_s=2, has_audio=True)})
        atomic_json(directory/'result.json', dict(status='completed', outputs=dict(source=str(source))))
        self.store.set_state(ident, 'completed')
        return ident, source, config

    def cleaned(self, ident, **kwargs):
        return self.f.submit(source_job_id=ident, subtitle_clean=dict(enabled=True, region=[.1,.75,.9,.9]), **kwargs)

    def test_download_only_runs_no_ai_render_drive_or_gpu(self):
        ident = self.f.submit(prepare_only=True).json['id']
        config = config_for(self.store, self.store.job(ident), self.f.settings)
        self.assertEqual(config['mode'], 'download')
        self.assertFalse(config['drive_upload']['enabled'])
        engines = test_pipeline.PipelineTests(); engines.setUp(); self.addCleanup(engines.doCleanups)
        with patch('teodub.subtitle_clean.preflight') as gpu:
            result = test_pipeline.pipeline.run(config)
        self.assertEqual(result['status'], 'completed')
        engines.asr.assert_not_called(); engines.translate.assert_not_called()
        engines.tts.assert_not_called(); engines.render.assert_not_called(); gpu.assert_not_called()

    def test_source_range_is_inline_authenticated_and_metadata_has_no_paths(self):
        ident, source, _ = self.prepared()
        response = self.client.get(f'/files/{ident}/source', headers={'Range':'bytes=0-7'})
        self.assertEqual(response.status_code, 206)
        self.assertEqual(response.data, source.read_bytes()[:8]); response.close()
        info = self.client.get(f'/api/jobs/{ident}/source').json
        self.assertEqual(info['width'], 320)
        self.assertNotIn(str(self.store.root), json.dumps(info))
        anon = self.f.app.test_client()
        self.assertEqual(anon.get(f'/files/{ident}/source').status_code, 401)
        other = self.f.app.test_client(); self.f.login('other', other)
        self.assertEqual(other.get(f'/api/jobs/{ident}/source').status_code, 404)
        self.assertEqual(other.get(f'/files/{ident}/source').status_code, 404)
        self.assertEqual(self.f.submit(other, source_job_id=ident).status_code, 400)

    def test_full_clean_config_binds_source_hash_and_engine_and_reuses_download(self):
        ident, source, _ = self.prepared()
        result = self.cleaned(ident); self.assertEqual(result.status_code, 201, result.json)
        job = self.store.job(result.json['id'])
        config = config_for(self.store, job, self.f.settings)
        self.assertEqual(config['input'], str(source))
        self.assertEqual(config['mode'], 'full')
        self.assertEqual(config['subtitle_clean'],dict(enabled=True, engine='sttn-masked-v1',
            region=[.1,.75,.9,.9], source_path=str(source), source_sha256=hash_file(source)))
        self.assertNotIn(str(self.store.root), job['spec'])
        self.assertNotIn('subtitle_clean', self.store.form_preferences(self.f.staff_id))
        engines = test_pipeline.PipelineTests(); engines.setUp(); self.addCleanup(engines.doCleanups)
        def clean(path, settings, directory, **kwargs):
            output = Path(directory)/'picture.mkv'; output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b'cleaned-picture')
            return dict(path=str(output), sha256=hash_file(output))
        with patch('teodub.subtitle_clean.preflight') as gpu, patch('teodub.subtitle_clean.clean_video', side_effect=clean) as inpaint:
            outcome = test_pipeline.pipeline.run(config)
        self.assertEqual(outcome['status'], 'completed', outcome)
        engines.download.assert_not_called(); gpu.assert_called_once(); inpaint.assert_called_once()
        self.assertEqual(engines.render.call_args.kwargs['cleaned_picture']['path'], str(Path(config['directory'])/'subtitle-clean'/'picture.mkv'))
        self.assertEqual(inpaint.call_args.args[1]['subtitle_clean']['source_sha256'], hash_file(source))
        self.assertEqual(hash_file(source), config['subtitle_clean']['source_sha256'])

    def test_reject_region_without_source_invalid_options_and_client_paths(self):
        for values in [dict(subtitle_clean={'enabled':True,'region':[0,.7,1,.9]}),
            dict(prepare_only='yes'), dict(source_job_id='../private'),
            dict(subtitle_clean={'enabled':False,'source_path':'C:/private.mp4'}),
            dict(subtitle_clean={'enabled':True,'region':[0,0,1,1]},source_job_id='a'*32),
            dict(subtitle_clean={'enabled':True,'region':[0,.7,True,.9]},source_job_id='a'*32),
            dict(subtitle_clean={'enabled':True,'region':[0,.7,1,.9]},source_job_id='a'*32,prepare_only=True)]:
            with self.subTest(values=values):self.assertEqual(self.f.submit(**values).status_code, 400)

    def test_tampered_or_other_link_source_is_never_accepted(self):
        ident, source, _ = self.prepared()
        self.assertEqual(self.cleaned(ident,input='https://www.douyin.com/video/111').status_code,400)
        source.write_bytes(b'tampered')
        self.assertEqual(self.client.get(f'/files/{ident}/source').status_code,404)
        self.assertEqual(self.cleaned(ident).status_code,400)

    def test_external_source_cannot_be_served_or_used_even_with_matching_hash(self):
        ident, _, config = self.prepared()
        external = self.store.root/'private.mp4'; external.write_bytes(b'private')
        atomic_json(Path(config['directory'])/'state.json', {'source':{'path':str(external),'sha256':hash_file(external)}})
        atomic_json(Path(config['directory'])/'result.json',dict(status='completed',outputs={'source':str(external)}))
        self.assertEqual(self.client.get(f'/files/{ident}/source').status_code,404)
        self.assertEqual(self.cleaned(ident).status_code,400)

    def test_completed_download_has_source_action_without_dubbed_output(self):
        ident, _, _ = self.prepared()
        value = describe(self.store,self.store.job(ident))
        self.assertTrue(value['has_source']);self.assertFalse(value['has_output'])
        self.assertTrue(value['prepare_only']);self.assertEqual(value['drive'],'Chưa tạo thành phẩm')
        self.assertIn('Khoanh vùng',value['message'])

    def test_stopping_retries_keep_frozen_rectangle(self):
        ident, _, _ = self.prepared()
        result = self.cleaned(ident)
        job = self.store.job(result.json['id']);before=job['spec']
        self.f.post(f"/api/jobs/{job['id']}/stop")
        self.f.post(f"/api/jobs/{job['id']}/retry")
        self.assertEqual(self.store.job(job['id'])['spec'],before)
        config = config_for(self.store, self.store.job(job['id']), self.f.settings)
        directory=Path(config['directory']);directory.mkdir(parents=True)
        frozen=dict(config,voice_gain=1.25);atomic_json(directory/'config.json',frozen)
        class Process:
            def poll(self):return 0
            def wait(self):return 0
        def launch(path, log):
            self.assertEqual(json.loads(Path(path).read_text(encoding='utf-8')),frozen)
            atomic_json(directory/'result.json',dict(status='cancelled'))
            return Process()
        Runner(self.store,self.f.settings,launcher=launch).run_one(self.store.job(job['id']))
        self.assertEqual(self.store.job(job['id'])['state'],'cancelled')

    def test_preparation_never_changes_personal_or_admin_model_fields(self):
        ident,_,_=self.prepared()
        policy=self.store.model_policy()
        for key,value in policy.items():self.assertEqual(json.loads(self.store.job(ident)['spec'])[key],value)
        response=self.f.post('/api/preferences',{'subtitle_clean':{'enabled':True,'region':[0,.7,1,.9]}})
        self.assertEqual(response.status_code,400)

    def test_region_saved_for_source_survives_reload_and_does_not_modify_job_config(self):
        ident, source, config = self.prepared()
        before=(Path(config['directory'])/'config.json').read_bytes()
        saved=self.f.post(f'/api/jobs/{ident}/source',{'subtitle_clean':{'enabled':True,'region':[.1,.75,.9,.9]}})
        self.assertEqual(saved.status_code,200,saved.json)
        self.assertEqual(self.client.get(f'/api/jobs/{ident}/source').json['region'],[.1,.75,.9,.9])
        self.assertEqual((Path(config['directory'])/'config.json').read_bytes(),before)
        other=self.f.app.test_client();self.f.login('other',other)
        self.assertEqual(self.f.post(f'/api/jobs/{ident}/source',{'subtitle_clean':{'enabled':True,'region':[0,.7,1,.9]}},other).status_code,404)
        self.assertEqual(self.client.post(f'/api/jobs/{ident}/source',json={}).status_code,403)
        source.write_bytes(b'changed')
        self.assertEqual(self.client.get(f'/api/jobs/{ident}/source').status_code,404)

    def test_home_has_source_picker_and_script_without_inline_code(self):
        response=self.client.get('/')
        self.assertIn('Tải video để khoanh vùng',response.get_data(as_text=True))
        self.assertIn('source_clean.js',response.get_data(as_text=True))
        self.assertIn("script-src 'self'",response.headers['Content-Security-Policy'])
