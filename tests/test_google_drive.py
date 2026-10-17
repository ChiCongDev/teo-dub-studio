"""Drive tests use an in-memory API/vault; no Google login or upload occurs."""
import base64
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlsplit
import uuid

import requests

from teodub.common import Cancelled, atomic_json
from teodub.google_drive import (API_URL, AUTH_URL, TOKEN_URL, UPLOAD_URL, FOLDER_MIME,
    SCOPE, DriveClient, DriveError, DriveAuthError, DriveVault, normalize_drive_config)


ACCOUNT = 'a'*32
FOLDER = 'folder_A'
CONFIG = dict(version=1, enabled=True, account_id=ACCOUNT, folder_id=FOLDER, folder_name='Tèo')
SESSION_URL = UPLOAD_URL+'?uploadType=resumable&upload_id=test-session-secret'


class FakeVault:
    def __init__(self):
        self.values = {}
        self.loads = []

    def save(self, subject, value):
        reference = uuid.uuid4().hex
        self.values[reference] = (subject, dict(value))
        return reference

    def load(self, subject, reference):
        self.loads.append((subject, reference))
        try:
            saved_subject, value = self.values[reference]
            if saved_subject != subject:
                raise KeyError()
            return dict(value)
        except KeyError:
            raise DriveAuthError('Kết nối giả đã ngắt.') from None

    def remove(self, reference):
        self.values.pop(reference, None)


class Response:
    def __init__(self, status=200, body=None, headers=None):
        self.status_code = status
        self.payload = body or {}
        self.headers = headers or {}

    def json(self):
        return self.payload


class FakeDrive:
    def __init__(self):
        self.calls = []
        self.created = []
        self.metadata = {}
        self.chunks = bytearray()
        self.remote = None
        self.session_valid = True
        self.count = 0
        self.after = None
        self.folder_override = None
        self.range_override = None
        self.token_error = None

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        assert kwargs['allow_redirects'] is False
        assert kwargs['timeout'] == (10, 60)
        if url == TOKEN_URL:
            result = self.token_error or Response(body=dict(access_token='access-secret', refresh_token='refresh-secret',
                expires_in=3600, scope=SCOPE, token_type='Bearer'))
        elif url == API_URL+'/about':
            result = Response(body={'user': dict(displayName='Test Google', emailAddress='test@example.invalid', permissionId='123456789')})
        elif method == 'GET' and url == API_URL+'/files':
            result = Response(body={'files': [{'id': FOLDER, 'name': 'Tèo'}]})
        elif method == 'POST' and url == API_URL+'/files':
            self.created.append(dict(kwargs['json']))
            result = Response(body={'id': 'new_folder', 'name': kwargs['json']['name']})
        elif url == API_URL+'/files/generateIds':
            self.count += 1
            result = Response(body={'ids': ['file_'+str(self.count)]})
        elif method == 'GET' and url in {API_URL+'/files/'+FOLDER, API_URL+'/files/root'}:
            result = Response(body=self.folder_override or dict(id=FOLDER, mimeType=FOLDER_MIME,
                trashed=False, capabilities={'canAddChildren': True}))
        elif method == 'GET' and '/files/file_' in url:
            result = Response(body=dict(self.remote)) if self.remote else Response(404)
        elif method == 'POST' and url == UPLOAD_URL:
            self.metadata = dict(kwargs['json'])
            self.created.append(self.metadata)
            self.chunks = bytearray()
            self.session_valid = True
            result = Response(headers={'Location': SESSION_URL})
        elif method == 'PUT' and url == SESSION_URL:
            if not self.session_valid:
                result = Response(404)
            elif kwargs['headers']['Content-Range'].startswith('bytes */'):
                result = Response(200, self.remote) if self.remote else Response(308, headers=
                    {'Range': 'bytes=0-'+str(len(self.chunks)-1)} if self.chunks else {})
            else:
                span, total = kwargs['headers']['Content-Range'][6:].split('/')
                start, finish = map(int, span.split('-'))
                assert start == len(self.chunks)
                assert finish-start+1 == len(kwargs['data'])
                self.chunks.extend(kwargs['data'])
                if len(self.chunks) == int(total):
                    self.remote = dict(self.metadata, size=str(len(self.chunks)),
                        md5Checksum=hashlib.md5(self.chunks).hexdigest(),
                        sha256Checksum=hashlib.sha256(self.chunks).hexdigest(), trashed=False)
                    result = Response(200, self.remote)
                else:
                    result = Response(308, headers={'Range': self.range_override or 'bytes=0-'+str(len(self.chunks)-1)})
        else:
            raise AssertionError((method, url, kwargs))
        if self.after:
            self.after(method, url, kwargs, result)
        return result


class DriveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name)
        self.api = FakeDrive()
        self.vault = FakeVault()
        self.client = DriveClient(self.data, session=self.api, vault=self.vault)
        self.video = self.data/'Video 1.mp4'
        self.video.write_bytes(b'complete-video-fixture'*31)
        self.sha = hashlib.sha256(self.video.read_bytes()).hexdigest()
        self.register()

    def tearDown(self):
        self.temp.cleanup()

    def register(self, account_id=ACCOUNT, *, expires_at=None):
        reference = self.vault.save(account_id, dict(client_id='123.apps.googleusercontent.com',
            client_secret='client-secret', access_token='access-secret', refresh_token='refresh-secret',
            expires_at=expires_at if expires_at is not None else time.time()+3600, scope=SCOPE))
        registry = self.client._registry()
        registry['accounts'].append(dict(id=account_id, name='Test', email='test@example.invalid', connected=True,
            generation=uuid.uuid4().hex, credential_ref=reference))
        atomic_json(self.client.registry_path, registry)

    def upload(self, **kwargs):
        return self.client.upload_video(CONFIG, source_id='job:1', video_path=self.video, sha256=self.sha, **kwargs)

    def journal(self, config=CONFIG, source='job:1'):
        key = self.client._journal_key(config, source, self.sha)
        return self.client.directory/'uploads'/(key+'.json')

    def test_config_direct_nested_default_and_disabled(self):
        self.assertEqual(normalize_drive_config(CONFIG), CONFIG)
        self.assertEqual(normalize_drive_config({'drive_upload': CONFIG}), CONFIG)
        self.assertFalse(normalize_drive_config({'tts': 'v3turbo'})['enabled'])
        self.assertEqual(normalize_drive_config(None)['account_id'], '')
        for value in ({'enabled': 1}, {'enabled': True}, {'enabled': False, 'version': True},
                      {'enabled': False, 'account_id': '../../secret'}, {'enabled': False, 'folder_id': 'https://evil.test'}):
            with self.subTest(value=value), self.assertRaises(DriveError):
                normalize_drive_config(value)

    def test_passive_reads_create_nothing_and_do_not_open_credentials(self):
        empty = self.data/'absent'
        client = DriveClient(empty, session=self.api, vault=self.vault)
        self.assertEqual(client.accounts(), [])
        self.assertIsNone(client.upload_status(CONFIG, 'job:1', self.sha))
        self.assertFalse(empty.exists())
        self.assertEqual(self.vault.loads, [])
        self.assertEqual(self.api.calls, [])

    def test_happy_path_fixed_id_checksum_receipt_and_repeat_no_network(self):
        progress = []
        receipt = self.upload(progress=progress.append)
        self.assertEqual(receipt['file_id'], 'file_1')
        self.assertEqual(receipt['folder_id'], FOLDER)
        self.assertEqual(receipt['state'], 'uploaded')
        self.assertEqual(self.api.created[0]['id'], 'file_1')
        self.assertEqual(self.api.created[0]['parents'], [FOLDER])
        journal = json.loads(self.journal().read_text(encoding='utf-8'))
        self.assertEqual(journal['receipt'], receipt)
        self.assertEqual(journal['percent'], 100)
        self.assertEqual(progress[-1]['percent'], 100)
        calls = len(self.api.calls)
        self.assertEqual(self.upload(), receipt)
        self.assertEqual(len(self.api.calls), calls)
        self.assertEqual(self.client.upload_status(CONFIG, 'job:1', self.sha)['receipt'], receipt)

    def test_public_records_do_not_contain_tokens_or_upload_capability(self):
        self.upload()
        for file in self.client.directory.rglob('*.json'):
            text = file.read_text(encoding='utf-8')
            for secret in ('client-secret', 'access-secret', 'refresh-secret', 'test-session-secret', 'upload_id=', 'code_verifier'):
                self.assertNotIn(secret, text)
        self.assertEqual(set(self.client.accounts()[0]), {'id', 'name', 'email', 'connected'})

    def test_sha_mismatch_and_missing_file_prevent_network(self):
        with self.assertRaises(DriveError):
            self.client.upload_video(CONFIG, source_id='job:1', video_path=self.video, sha256='b'*64)
        self.video.unlink()
        with self.assertRaises(DriveError):
            self.upload()
        self.assertEqual(self.api.calls, [])

    def test_cancel_before_network_keeps_video(self):
        event = threading.Event()
        event.set()
        with self.assertRaises(Cancelled):
            self.upload(cancel=event)
        self.assertTrue(self.video.exists())
        self.assertEqual(self.api.calls, [])

    def test_disconnect_stops_requests_and_prevents_cached_upload_claim(self):
        self.upload()
        self.client.disconnect(ACCOUNT)
        self.assertFalse(self.client.accounts()[0]['connected'])
        calls = len(self.api.calls)
        with self.assertRaises(DriveAuthError):
            self.upload()
        self.assertEqual(len(self.api.calls), calls)
        self.assertEqual(self.client.upload_status(CONFIG, 'job:1', self.sha)['state'], 'uploaded')

    def test_timeout_after_commit_reconciles_generated_id_no_second_post(self):
        def fault(method, url, args, response):
            if method == 'PUT' and response.status_code == 200:
                raise requests.Timeout('secret URL must not escape')
        self.api.after = fault
        with self.assertRaises(DriveError) as caught:
            self.upload()
        self.assertTrue(caught.exception.uncertain)
        self.assertNotIn('secret URL', str(caught.exception))
        self.assertEqual(json.loads(self.journal().read_text(encoding='utf-8'))['state'], 'needs_check')
        self.api.after = None
        fresh = DriveClient(self.data, session=self.api, vault=self.vault)
        receipt = fresh.upload_video(CONFIG, source_id='job:1', video_path=self.video, sha256=self.sha)
        self.assertEqual(receipt['file_id'], 'file_1')
        self.assertEqual(len(self.api.created), 1)
        self.assertEqual(self.api.count, 1)

    def test_partial_timeout_resumes_actual_server_offset(self):
        self.video.write_bytes(b'x'*(256*1024*2+23))
        self.sha = hashlib.sha256(self.video.read_bytes()).hexdigest()
        def fault(method, url, args, response):
            if method == 'PUT' and len(self.api.chunks) == 256*1024*2:
                raise requests.Timeout('private')
        self.api.after = fault
        with patch('teodub.google_drive.CHUNK_SIZE', 256*1024), self.assertRaises(DriveError):
            self.upload()
        journal = json.loads(self.journal().read_text(encoding='utf-8'))
        self.assertEqual(journal['transferred'], 256*1024)
        self.api.after = None
        with patch('teodub.google_drive.CHUNK_SIZE', 256*1024):
            receipt = self.upload()
        self.assertEqual(receipt['state'], 'uploaded')
        data_calls = [args['headers']['Content-Range'] for method, url, args in self.api.calls if method == 'PUT']
        self.assertIn('bytes */'+str(self.video.stat().st_size), data_calls)
        self.assertIn(f'bytes {256*1024*2}-{self.video.stat().st_size-1}/{self.video.stat().st_size}', data_calls)
        self.assertEqual(len(self.api.created), 1)

    def test_expired_session_uses_same_id(self):
        self.video.write_bytes(b'x'*(256*1024+23))
        self.sha = hashlib.sha256(self.video.read_bytes()).hexdigest()
        event = threading.Event()
        def pause(method, url, args, response):
            if method == 'PUT' and response.status_code == 308:
                event.set()
        self.api.after = pause
        with patch('teodub.google_drive.CHUNK_SIZE', 256*1024), self.assertRaises(Cancelled):
            self.upload(cancel=event)
        self.api.after = None
        self.api.session_valid = False
        receipt = self.upload()
        self.assertEqual(receipt['file_id'], 'file_1')
        self.assertEqual(self.api.count, 1)
        self.assertEqual([row['id'] for row in self.api.created], ['file_1', 'file_1'])

    def test_remote_identity_checksum_or_parent_mismatch_never_success(self):
        for field, wrong in [('sha256Checksum', 'c'*64), ('parents', ['wrong_folder']), ('size', '2'), ('id', 'different'),
                             ('appProperties', {}), ('trashed', True)]:
            with self.subTest(field=field):
                self.api.remote = None
                def mutate(method, url, args, response):
                    if method == 'PUT' and response.status_code == 200:
                        self.api.remote[field] = wrong
                self.api.after = mutate
                source = 'job:'+field
                with self.assertRaises(DriveError):
                    self.client.upload_video(CONFIG, source_id=source, video_path=self.video, sha256=self.sha)
                status = self.client.upload_status(CONFIG, source, self.sha)
                self.assertNotEqual(status['state'], 'uploaded')
                self.assertIsNone(status['receipt'])

    def test_remote_md5_fallback_is_verified(self):
        def omit_sha(method, url, args, response):
            if method == 'PUT' and response.status_code == 200:
                self.api.remote.pop('sha256Checksum')
        self.api.after = omit_sha
        self.assertEqual(self.upload()['state'], 'uploaded')

    def test_response_redirect_and_non_google_session_are_rejected(self):
        def attack(method, url, args, response):
            if method == 'POST' and url == UPLOAD_URL:
                response.headers['Location'] = 'https://evil.example/upload?access_token=secret'
        self.api.after = attack
        with self.assertRaises(DriveError):
            self.upload()
        self.assertFalse(any(method == 'PUT' for method, _, _ in self.api.calls))

    def test_folder_scope_and_journal_parent_corruption_block_create(self):
        self.api.folder_override = dict(id='wrong_folder', mimeType=FOLDER_MIME, trashed=False, capabilities={'canAddChildren': True})
        with self.assertRaises(DriveError):
            self.upload()
        self.assertEqual(self.api.count, 0)
        self.assertEqual(self.api.created, [])
        self.api.folder_override = None
        self.upload()
        journal = json.loads(self.journal().read_text(encoding='utf-8'))
        journal['parent_id'] = 'wrong_folder'
        atomic_json(self.journal(), journal)
        calls = len(self.api.calls)
        with self.assertRaises(DriveError):
            self.upload()
        self.assertEqual(len(self.api.calls), calls)

    def test_root_alias_resolves_to_real_parent(self):
        config = dict(CONFIG, folder_id='root')
        receipt = self.client.upload_video(config, source_id='job:1', video_path=self.video, sha256=self.sha)
        self.assertEqual(receipt['folder_id'], 'root')
        self.assertEqual(self.api.created[0]['parents'], [FOLDER])

    def test_corrupt_receipt_cannot_skip_upload_verification(self):
        self.upload()
        journal = json.loads(self.journal().read_text(encoding='utf-8'))
        journal.pop('confirmed_remote')
        atomic_json(self.journal(), journal)
        calls = len(self.api.calls)
        with self.assertRaises(DriveError):
            self.upload()
        self.assertEqual(len(self.api.calls), calls)

    def test_auth_error_does_not_resubmit_or_use_another_account(self):
        self.register('b'*32)
        def unauthorized(method, url, args, response):
            response.status_code = 401
            response.payload = {'error': {'message': 'access-secret'}}
        self.api.after = unauthorized
        with self.assertRaises(DriveAuthError) as caught:
            self.upload()
        self.assertNotIn('access-secret', str(caught.exception))
        self.assertEqual(len(self.api.calls), 1)
        self.assertEqual(self.api.created, [])

    def test_disconnect_during_refresh_cannot_restore_credentials(self):
        registry = self.client._registry()
        row = registry['accounts'][0]
        self.vault.values[row['credential_ref']][1]['expires_at'] = 0
        def disconnect(method, url, args, response):
            if url == TOKEN_URL:
                self.client.disconnect(ACCOUNT)
        self.api.after = disconnect
        with self.assertRaises(DriveAuthError):
            self.upload()
        self.assertFalse(self.client.accounts()[0]['connected'])
        self.assertEqual(self.vault.values, {})
        self.assertEqual(len(self.api.calls), 1)

    def test_refresh_is_saved_encrypted_and_uses_offline_token(self):
        row = self.client._registry()['accounts'][0]
        self.vault.values[row['credential_ref']][1]['expires_at'] = 0
        self.upload()
        call = self.api.calls[0]
        self.assertEqual(call[1], TOKEN_URL)
        self.assertEqual(call[2]['data']['grant_type'], 'refresh_token')
        self.assertEqual(call[2]['data']['refresh_token'], 'refresh-secret')
        self.assertNotEqual(self.client._registry()['accounts'][0]['credential_ref'], row['credential_ref'])

    def test_disconnect_during_final_chunk_stops_receipt_and_next_requests(self):
        def disconnect(method, url, args, response):
            if method == 'PUT':
                self.client.disconnect(ACCOUNT)
        self.api.after = disconnect
        with self.assertRaises(DriveAuthError):
            self.upload()
        self.assertFalse(self.client.accounts()[0]['connected'])
        self.assertEqual(self.client.upload_status(CONFIG, 'job:1', self.sha)['state'], 'needs_reconnect')
        self.assertIsNone(self.client.upload_status(CONFIG, 'job:1', self.sha)['receipt'])
        self.assertEqual(self.api.calls[-1][0], 'PUT')

    def test_folder_list_only_drive_file_and_explicit_create(self):
        self.assertEqual(self.client.folders(ACCOUNT)[0]['id'], 'root')
        self.assertEqual(self.api.created, [])
        result = self.client.create_folder(ACCOUNT, 'Thành phẩm')
        self.assertEqual(result['name'], 'Thành phẩm')
        self.assertEqual(self.api.created[0]['mimeType'], FOLDER_MIME)

    def test_error_redaction_and_storage_quota(self):
        def quota(method, url, args, response):
            response.status_code = 403
            response.payload = {'error': {'message': 'access-secret', 'errors': [{'reason': 'storageQuotaExceeded'}]}}
        self.api.after = quota
        with self.assertRaises(DriveError) as caught:
            self.upload()
        self.assertIn('hết dung lượng', str(caught.exception))
        self.assertNotIn('access-secret', str(caught.exception))

    def test_malformed_range_and_file_changed_mid_upload(self):
        self.video.write_bytes(b'x'*(256*1024+23))
        self.sha = hashlib.sha256(self.video.read_bytes()).hexdigest()
        self.api.range_override = 'bytes=1-99'
        with patch('teodub.google_drive.CHUNK_SIZE', 256*1024), self.assertRaises(DriveError):
            self.upload()
        self.assertEqual(self.client.upload_status(CONFIG, 'job:1', self.sha)['state'], 'needs_check')

    def test_journal_id_saved_before_session_creation(self):
        def inspect(method, url, args, response):
            if method == 'POST' and url == UPLOAD_URL:
                value = json.loads(self.journal().read_text(encoding='utf-8'))
                self.assertEqual(value['file_id'], args['json']['id'])
                self.assertEqual(value['state'], 'pending')
        self.api.after = inspect
        self.upload()

    def test_subject_source_account_folder_hash_are_separate_journals(self):
        self.upload()
        keys = {self.client._journal_key(CONFIG, 'job:1', self.sha), self.client._journal_key(CONFIG, 'job:2', self.sha),
                self.client._journal_key(dict(CONFIG, account_id='b'*32), 'job:1', self.sha),
                self.client._journal_key(dict(CONFIG, folder_id='folder_B'), 'job:1', self.sha),
                self.client._journal_key(CONFIG, 'job:1', 'c'*64)}
        self.assertEqual(len(keys), 5)

    def test_unsafe_credential_ref_and_client_json_rejected(self):
        with self.assertRaises(DriveError):
            DriveVault(self.data).path('../../secret')
        path = self.data/'oauth.json'
        atomic_json(path, {'web': {'client_id': '123.apps.googleusercontent.com'}})
        with self.assertRaises(DriveError):
            self.client.connect(path)
        atomic_json(path, {'installed': {'client_id': '123.apps.googleusercontent.com', 'token_uri': 'https://evil.example'}})
        with self.assertRaises(DriveError):
            self.client.connect(path)
        self.assertEqual(self.api.calls, [])

    def test_google_downloaded_client_json_legacy_auth_uri_is_accepted(self):
        path = self.data/'desktop-client.json'
        atomic_json(path, {'installed': {'client_id': '123.apps.googleusercontent.com',
            'client_secret': 'synthetic', 'auth_uri': 'https://accounts.google.com/o/oauth2/auth',
            'token_uri': TOKEN_URL, 'redirect_uris': ['http://localhost']}})
        self.assertEqual(self.client._client_json(path)['client_id'], '123.apps.googleusercontent.com')
        self.assertEqual(self.api.calls, [])

    def test_oauth_loopback_state_pkce_offline_and_identity_are_saved(self):
        path = self.data/'oauth.json'
        atomic_json(path, {'installed': {'client_id': '123.apps.googleusercontent.com',
                                       'client_secret': 'client-secret', 'auth_uri': AUTH_URL, 'token_uri': TOKEN_URL}})
        threads, authorization = [], {}
        def browser(url):
            authorization.update({k: v[0] for k, v in parse_qs(urlsplit(url).query).items()})
            def callback():
                redirect = authorization['redirect_uri']
                # Wrong state must not end the listener or exchange a code.
                self.assertEqual(requests.get(redirect+'?'+urlencode(dict(state='wrong', code='evil')), timeout=2).status_code, 400)
                self.assertEqual(requests.get(redirect+'?'+urlencode(dict(state=authorization['state'], code='oauth-code-secret')), timeout=2).status_code, 200)
            thread = threading.Thread(target=callback)
            thread.start()
            threads.append(thread)
            return True
        account = self.client.connect(path, open_browser=browser, timeout=5)
        for thread in threads:
            thread.join(3)
            self.assertFalse(thread.is_alive())
        self.assertEqual(account['email'], 'test@example.invalid')
        self.assertEqual(authorization['scope'], SCOPE)
        self.assertEqual(authorization['access_type'], 'offline')
        self.assertEqual(authorization['code_challenge_method'], 'S256')
        self.assertEqual(urlsplit(authorization['redirect_uri']).hostname, '127.0.0.1')
        data = next(args['data'] for _, url, args in self.api.calls if url == TOKEN_URL)
        expected = base64.urlsafe_b64encode(hashlib.sha256(data['code_verifier'].encode('ascii')).digest()).decode('ascii').rstrip('=')
        self.assertEqual(authorization['code_challenge'], expected)
        self.assertEqual(data['code'], 'oauth-code-secret')
        text = self.client.registry_path.read_text(encoding='utf-8')
        self.assertNotIn('oauth-code-secret', text)
        self.assertNotIn('refresh-secret', text)

    def test_oauth_browser_fail_cancel_and_missing_refresh_do_not_save(self):
        path = self.data/'oauth.json'
        atomic_json(path, {'installed': {'client_id': '123.apps.googleusercontent.com'}})
        before = self.client.registry_path.read_bytes()
        with self.assertRaises(DriveError):
            self.client.connect(path, open_browser=lambda _: False)
        event = threading.Event()
        event.set()
        with self.assertRaises(Cancelled):
            self.client.connect(path, cancel=event)
        self.assertEqual(self.client.registry_path.read_bytes(), before)
        self.assertEqual(self.api.calls, [])

    def test_passive_receipt_rejects_corrupt_evidence_and_scope(self):
        self.upload()
        journal = json.loads(self.journal().read_text(encoding='utf-8'))
        journal['confirmed_remote']['parents'] = ['other']
        atomic_json(self.journal(), journal)
        calls = len(self.api.calls)
        with self.assertRaises(DriveError):
            self.client.upload_status(CONFIG, 'job:1', self.sha)
        self.assertEqual(len(self.api.calls), calls)

    def test_closed_ui_callback_does_not_lose_receipt(self):
        def closed(_):
            raise RuntimeError('UI closed')
        self.assertEqual(self.upload(progress=closed)['state'], 'uploaded')

    def test_same_source_worker_lock_prevents_duplicate_network(self):
        visited = []
        def competing(method, url, args, response):
            if url == API_URL+'/files/generateIds':
                count = len(self.api.calls)
                with self.assertRaises(DriveError):
                    self.upload()
                visited.append(True)
                self.assertEqual(len(self.api.calls), count)
        self.api.after = competing
        self.upload()
        self.assertEqual(visited, [True])
        self.assertEqual(len(self.api.created), 1)

    def test_file_change_during_upload_prevents_final_chunk(self):
        self.video.write_bytes(b'x'*(256*1024+23))
        self.sha = hashlib.sha256(self.video.read_bytes()).hexdigest()
        def mutate(method, url, args, response):
            if method == 'PUT' and response.status_code == 308:
                self.video.write_bytes(b'changed source')
        self.api.after = mutate
        with patch('teodub.google_drive.CHUNK_SIZE', 256*1024), self.assertRaises(DriveError):
            self.upload()
        self.assertIsNone(self.api.remote)
        self.assertEqual(sum(method == 'PUT' for method, _, _ in self.api.calls), 1)

    def test_invalid_refresh_and_malformed_token_fields_safe_error(self):
        registry = self.client._registry()
        row = registry['accounts'][0]
        token = self.vault.values[row['credential_ref']][1]
        token['expires_at'] = 0
        self.api.token_error = Response(400, {'error': 'invalid_grant', 'error_description': 'refresh-secret'})
        with self.assertRaises(DriveAuthError) as caught:
            self.upload()
        self.assertNotIn('refresh-secret', str(caught.exception))
        from teodub.google_drive import _checked_token
        for body in (dict(access_token=123), dict(access_token='x', scope=['secret']),
                     dict(access_token='x', token_type=123), dict(access_token='x', expires_in='secret'),
                     dict(access_token='x', expires_in=float('inf')), dict(access_token='x', expires_in=float('nan'))):
            with self.subTest(body=body), self.assertRaises(DriveAuthError) as caught:
                _checked_token(body)
            self.assertNotIn('secret', str(caught.exception))

    @unittest.skipUnless(os.name == 'nt', 'Windows DPAPI is required')
    def test_real_windows_dpapi_roundtrip_wrong_subject_and_ciphertext(self):
        vault = DriveVault(self.data/'test-vault')
        value = dict(access_token='synthetic-access-value', refresh_token='synthetic-refresh-value')
        reference = vault.save(ACCOUNT, value)
        data = vault.path(reference).read_bytes()
        self.assertNotIn(b'synthetic-access-value', data)
        self.assertEqual(vault.load(ACCOUNT, reference), value)
        with self.assertRaises(DriveAuthError):
            vault.load('b'*32, reference)
        vault.remove(reference)
        self.assertFalse(vault.path(reference).exists())

    def test_resume_unknown_init_timeout_uses_same_generated_id(self):
        def fault(method, url, args, response):
            if method == 'POST' and url == UPLOAD_URL:
                raise requests.Timeout('unknown session secret')
        self.api.after = fault
        with self.assertRaises(DriveError):
            self.upload()
        journal = json.loads(self.journal().read_text(encoding='utf-8'))
        self.assertEqual(journal['file_id'], 'file_1')
        self.assertEqual(journal['session_ref'], '')
        self.api.after = None
        self.assertEqual(self.upload()['file_id'], 'file_1')
        self.assertEqual(self.api.count, 1)
        self.assertEqual([row['id'] for row in self.api.created], ['file_1', 'file_1'])


if __name__ == '__main__':
    unittest.main()
