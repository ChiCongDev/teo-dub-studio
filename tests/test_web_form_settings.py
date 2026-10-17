import json
from pathlib import Path
import tempfile
import unittest
import uuid

from teodub.ai_models import NEW_MODELS
from teodub_web.app import create_app
from teodub_web.form_settings import FORM_DEFAULTS
from teodub_web.runner import config_for, validate_spec
from teodub_web.store import Store

URL = 'https://www.douyin.com/video/1964367600664655903'


class FormSettingsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)
        self.password = 'settings-fixture-password'
        self.admin_id = self.store.add_user('admin', 'Admin', self.password, 'admin')
        self.staff_id = self.store.add_user('staff', 'Staff', self.password)
        self.other_id = self.store.add_user('other', 'Other', self.password)
        self.settings = dict(secret='fixture-secret', execution_lock=str(Path(self.tmp.name)/'execution'),
                             drive_folder=str(Path(self.tmp.name)/'drive'))
        self.app = create_app(self.store, self.settings)
        self.app.config['TESTING'] = True
        self.staff = self.login('staff')
        self.admin = self.login('admin')
        self.other = self.login('other')

    def tearDown(self):
        self.tmp.cleanup()

    def login(self, username, app=None):
        client = (app or self.app).test_client()
        client.get('/login')
        with client.session_transaction() as session:
            csrf = session['csrf']
        self.assertEqual(client.post('/login', data=dict(username=username, password=self.password,
                                                       csrf=csrf)).status_code, 302)
        return client

    def csrf(self, client):
        with client.session_transaction() as session:
            return session['csrf']

    def post(self, client, endpoint, data):
        return client.post(endpoint, json=data, headers={'X-CSRF-Token': self.csrf(client)})

    def submit(self, client=None, **values):
        return self.post(client or self.staff, '/api/jobs', dict(input=URL, submit_key=uuid.uuid4().hex, **values))

    def save_models(self, **values):
        return self.admin.post('/ai-settings', data=dict(NEW_MODELS, csrf=self.csrf(self.admin), **values))

    def test_staff_cannot_change_policy_via_page_api_or_store(self):
        self.assertEqual(self.staff.get('/ai-settings').status_code, 403)
        self.assertEqual(self.staff.post('/ai-settings', data=dict(NEW_MODELS, csrf=self.csrf(self.staff))).status_code, 403)
        self.assertEqual(self.post(self.staff, '/api/preferences', {'translation_model': 'gpt-6-astra'}).status_code, 400)
        with self.assertRaises(PermissionError):
            self.store.save_model_policy(dict(NEW_MODELS, translation_model='gpt-6-astra'), self.staff_id)
        self.assertEqual(self.store.model_policy(), NEW_MODELS)
        self.assertEqual(self.app.test_client().get('/api/preferences').status_code, 401)

    def test_admin_policy_saved_reopened_and_displayed_without_staff_selectors(self):
        self.assertEqual(self.save_models(asr_model='small', tts_model='v3nano').status_code, 302)
        reopened = Store(self.tmp.name)
        self.assertEqual(reopened.model_policy(), dict(NEW_MODELS, asr_model='small', tts_model='v3nano'))
        page = self.staff.get('/').get_data(as_text=True)
        self.assertIn('GPT-6 Luna', page)
        self.assertIn('VieNeu v3 Nano', page)
        for key in NEW_MODELS:
            self.assertNotIn('select name="'+key+'"', page)
        self.assertNotIn('href="/ai-settings"', page)
        self.assertIn('href="/ai-settings"', self.admin.get('/').get_data(as_text=True))

    def test_invalid_policy_and_missing_csrf_do_not_change_saved_models(self):
        self.assertEqual(self.save_models(translation_model='invalid').status_code, 200)
        self.assertEqual(self.store.model_policy(), NEW_MODELS)
        self.assertEqual(self.admin.post('/ai-settings', data=NEW_MODELS).status_code, 403)
        self.assertEqual(self.store.model_policy(), NEW_MODELS)

    def test_forged_expensive_model_rejected_and_job_uses_current_policy(self):
        self.assertEqual(self.submit(translation_model='gpt-6-astra', translation_effort='high').status_code, 400)
        self.assertEqual(self.submit(role='admin').status_code, 400)
        self.assertFalse(self.store.jobs())
        self.save_models(asr_model='small', tts_model='v3nano')
        result = self.submit(voice='Mạnh Dũng')
        self.assertEqual(result.status_code, 201)
        spec = json.loads(self.store.job(result.json['id'])['spec'])
        for key, value in self.store.model_policy().items():
            self.assertEqual(spec[key], value)

    def test_policy_change_between_validation_and_insert_is_refused_atomically(self):
        url, title, spec = validate_spec(dict(input=URL))
        self.save_models(asr_model='small')
        with self.assertRaises(ValueError):
            self.store.submit(self.staff_id, title, url, spec, uuid.uuid4().hex)
        self.assertFalse(self.store.jobs())
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM web_preferences').fetchone()[0], 0)

    def test_personal_choices_isolated_and_restored_in_new_browser_session(self):
        values = dict(voice='Adam bựa', reading_style='advertising', voice_speed=1.25, speed=.9,
                      original_gain=0, voice_gain=1.5, burn_subtitles=False, save_drive=False,
                      subtitle_color='yellow', subtitle_weight='regular', subtitle_size=24)
        self.assertEqual(self.post(self.staff, '/api/preferences', values).json['preferences'], values)
        self.assertEqual(self.other.get('/api/preferences').json['preferences'], FORM_DEFAULTS)
        reopened = create_app(Store(self.tmp.name), self.settings)
        client = self.login('staff', reopened)
        self.assertEqual(client.get('/api/preferences').json['preferences'], values)
        page = client.get('/').get_data(as_text=True)
        self.assertIn('<option selected>Adam bựa</option>', page)
        self.assertIn('value="1.25" selected', page)
        self.assertIn('value="advertising" selected', page)
        self.assertIn('value="24"', page)
        self.assertNotIn('name="burn_subtitles" checked', page)
        self.assertNotIn('name="save_drive" checked', page)

    def test_preferences_never_accept_owner_models_url_or_job_title(self):
        for extra in [dict(user_id=self.other_id), dict(input=URL), dict(title='private'),
                      dict(tts_model='v3nano'), dict(role='admin')]:
            self.assertEqual(self.post(self.staff, '/api/preferences', extra).status_code, 400)
        self.assertEqual(self.staff.get('/api/preferences').json['preferences'], FORM_DEFAULTS)

    def test_invalid_partial_changes_leave_previous_preferences_intact(self):
        saved = self.post(self.staff, '/api/preferences', dict(subtitle_size=22)).json['preferences']
        for values in [dict(voice_speed=True), dict(voice_speed=9), dict(subtitle_size=99),
                       dict(original_gain=3), dict(voice_gain='1'), dict(save_drive='false'),
                       dict(burn_subtitles=1), dict(voice='invalid'), dict(reading_style=[])]:
            self.assertEqual(self.post(self.staff, '/api/preferences', values).status_code, 400)
            self.assertEqual(self.staff.get('/api/preferences').json['preferences'], saved)
        self.assertEqual(self.staff.post('/api/preferences', json={'subtitle_size': 24}).status_code, 403)

    def test_successful_submit_remembers_choices_and_duplicate_does_not_overwrite_newer_choices(self):
        body = dict(input=URL, submit_key=uuid.uuid4().hex, subtitle_size=19, save_drive=False)
        response = self.post(self.staff, '/api/jobs', body)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(self.staff.get('/api/preferences').json['preferences']['subtitle_size'], 19)
        self.post(self.staff, '/api/preferences', dict(subtitle_size=27))
        duplicate = self.post(self.staff, '/api/jobs', body)
        self.assertEqual(duplicate.json['id'], response.json['id'])
        self.assertEqual(self.staff.get('/api/preferences').json['preferences']['subtitle_size'], 27)

    def test_existing_account_uses_latest_job_choices_ignoring_historical_models(self):
        self.submit(subtitle_size=18)
        second = self.submit(voice='Mai Anh', original_gain=.25, subtitle_size=23)
        with self.store.db() as db:
            db.execute('DELETE FROM web_preferences WHERE user_id=?', (self.staff_id,))
            spec = json.loads(self.store.job(second.json['id'])['spec'])
            spec['translation_model'] = 'gpt-6-astra'
            db.execute('UPDATE jobs SET spec=? WHERE id=?', (json.dumps(spec), second.json['id']))
        choices = self.staff.get('/api/preferences').json['preferences']
        self.assertEqual(choices['voice'], 'Mai Anh')
        self.assertEqual(choices['subtitle_size'], 23)
        self.assertEqual(choices['original_gain'], .25)
        self.assertNotIn('translation_model', choices)
        self.assertEqual(self.store.model_policy(), NEW_MODELS)

    def test_model_change_and_personal_save_preserve_frozen_job_and_resume(self):
        self.save_models(translation_model='gpt-6-astra', translation_effort='high')
        first = self.submit(subtitle_size=24).json['id']
        before = self.store.job(first)['spec']
        self.save_models()
        self.post(self.staff, '/api/preferences', dict(subtitle_size=13))
        self.post(self.staff, f'/api/jobs/{first}/stop', {})
        self.post(self.staff, f'/api/jobs/{first}/retry', {})
        self.assertEqual(self.store.job(first)['spec'], before)
        config = config_for(self.store, self.store.job(first), self.settings)
        self.assertEqual(config['translation_model'], 'gpt-6-astra')
        self.assertEqual(config['subtitle_size'], 24)
        second = self.submit().json['id']
        self.assertEqual(json.loads(self.store.job(second)['spec'])['translation_model'], 'gpt-6-luna')

    def test_voice_fallback_after_admin_changes_tts_keeps_other_choices(self):
        self.post(self.staff, '/api/preferences', dict(voice='Adam bựa', subtitle_size=25, save_drive=False))
        self.save_models(tts_model='v3nano')
        values = self.staff.get('/api/preferences').json['preferences']
        self.assertEqual(values['voice'], 'Adam')
        self.assertEqual(values['subtitle_size'], 25)
        self.assertFalse(values['save_drive'])
        page = self.staff.get('/').get_data(as_text=True)
        self.assertNotIn('Adam bựa', page)
        self.assertEqual(self.submit(voice='Adam bựa').status_code, 400)
        self.assertEqual(self.submit(voice='Mạnh Dũng').status_code, 201)


if __name__ == '__main__':
    unittest.main()
