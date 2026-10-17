import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import Mock, patch

from teodub.common import atomic_json
from teodub.web_address import fixed_url, load_address, normalize_address, save_address
from teodub_web.address_credentials import AddressCredentials
from teodub_web.address_setup import AddressSetup
from teodub_web import address_restart
from teodub_web.fixed_tunnel import FixedTunnel, connector_command, public_probe
from teodub_web.app import create_app
from teodub_web.store import Store


class WebAddressTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        (self.root/'server').mkdir()
        self.address = dict(version=1, mode='fixed', provider='cloudflare',
                            url='https://teo.example.com', credential='a'*32)

    def test_normalize_https_exact_hostname(self):
        self.assertEqual(fixed_url(' TEO.Example.com '), 'https://teo.example.com')
        self.assertEqual(fixed_url('https://teo.example.com:443/'), 'https://teo.example.com')

    def test_reject_insecure_wildcard_credentials_path_ip_and_trial_url(self):
        for value in ('http://teo.example.com', 'https://teo.example.com/path', 'https://teo.example.com?token=x',
                      'https://a:b@teo.example.com', 'https://*.example.com', 'https://teo.example.com#x',
                      'https://127.0.0.1', 'https://[::1]', 'https://localhost', 'https://a.local',
                      'https://a.trycloudflare.com', 'https://teo.example.com:8765', 'https://a.example.com\\evil'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                fixed_url(value)

    def test_missing_address_keeps_quick_mode_without_creating_files(self):
        self.assertEqual(load_address(self.root), dict(version=1, mode='quick'))
        self.assertFalse((self.root/'web-address.json').exists())

    def test_fixed_address_persists_and_corrupt_config_never_falls_back(self):
        save_address(self.root, self.address)
        self.assertEqual(load_address(self.root), self.address)
        (self.root/'web-address.json').write_text('{broken', encoding='utf-8')
        with self.assertRaises(ValueError):
            load_address(self.root)

    def test_ngrok_requires_provider_domain_and_config_has_no_inline_secret(self):
        value = dict(self.address, provider='ngrok', url='https://assigned.ngrok-free.dev')
        self.assertEqual(normalize_address(value), value)
        for changes in (dict(url='https://example.com'), dict(token='secret'), dict(credential='../bad')):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                normalize_address(dict(value, **changes))

    def test_windows_token_sealed_roundtrip_and_provider_binding(self):
        token = 'SyntheticTokenForUnitTestOnly123456789'
        credentials = AddressCredentials(self.root)
        reference = credentials.save('cloudflare', token)
        self.assertNotIn(token.encode(), credentials.path(reference).read_bytes())
        self.assertEqual(credentials.load('cloudflare', reference), token)
        with self.assertRaises(ValueError):
            credentials.load('ngrok', reference)
        with self.assertRaises(ValueError):
            credentials.load('cloudflare', '../bad')

    def test_full_shell_command_is_not_accepted_as_a_token(self):
        with self.assertRaises(ValueError):
            AddressCredentials.validate('cloudflared.exe service install abcdefghijklmnopqrstuvwxyz')

    def test_cloudflare_token_only_in_child_env_not_argv_or_disk(self):
        project = self.root/'project'
        (project/'tools').mkdir(parents=True)
        (project/'tools'/'cloudflared.exe').touch()
        token = 'SyntheticTokenForUnitTestOnly123456789'
        with patch.object(AddressCredentials, 'load', return_value=token):
            command, env = connector_command(self.address, self.root, 8765, project=project)
        self.assertNotIn(token, str(command))
        self.assertEqual(env['TUNNEL_TOKEN'], token)
        self.assertNotIn('--url', command)

    def test_ngrok_keeps_url_and_disables_request_inspection_without_plaintext_token(self):
        project = self.root/'project'
        (project/'tools').mkdir(parents=True)
        (project/'tools'/'ngrok.exe').touch()
        value = dict(self.address, provider='ngrok', url='https://assigned.ngrok-free.dev')
        with patch.object(AddressCredentials, 'load', return_value='syntheticTokenForTests123456'):
            command, env = connector_command(value, self.root, 8765, project=project)
        self.assertIn(value['url'], command)
        self.assertIn('--inspect=false', command)
        self.assertNotIn('syntheticToken', (self.root/'server'/'ngrok-agent.yml').read_text())
        self.assertNotIn('TUNNEL_TOKEN', env)

    def test_missing_connector_error_contains_no_token(self):
        with patch.object(AddressCredentials, 'load', return_value='SyntheticSecretForTests123456'):
            with self.assertRaisesRegex(ValueError, 'bộ kết nối') as error:
                connector_command(self.address, self.root, 8765, project=self.root)
        self.assertNotIn('SyntheticSecret', str(error.exception))

    def test_failed_fixed_tunnel_retains_url_instead_of_starting_quick(self):
        app = Mock(config={'TEO_INSTANCE': 'test-instance'})
        settings = {}
        tunnel = FixedTunnel(app, settings, self.root, 8765, self.address)
        with (patch('teodub_web.fixed_tunnel.connector_command', side_effect=ValueError('private-secret')),
             patch.object(tunnel.stop_event, 'wait', return_value=True),
             patch('teodub_web.fixed_tunnel.subprocess.Popen') as launch):
            tunnel.loop()
        launch.assert_not_called()
        status = (self.root/'address-status.json').read_text()
        self.assertNotIn('private-secret', status)
        self.assertEqual(json.loads(status)['url'], self.address['url'])
        self.assertEqual(settings['address_state'], 'offline')

    def test_probe_rejects_other_server_or_redirect(self):
        response = Mock(status=200)
        response.read.return_value = b'{"status":"ok","instance":"other"}'
        response.geturl.return_value = self.address['url']+'/health'
        opener = Mock()
        opener.open.return_value.__enter__ = Mock(return_value=response)
        opener.open.return_value.__exit__ = Mock(return_value=False)
        with patch('teodub_web.fixed_tunnel.build_opener', return_value=opener):
            self.assertFalse(public_probe(self.address['url'], 'ours'))
            response.read.return_value = b'{"status":"ok","instance":"ours"}'
            self.assertTrue(public_probe(self.address['url'], 'ours'))
            response.geturl.return_value = 'https://another.example.com/health'
            self.assertFalse(public_probe(self.address['url'], 'ours'))

    def test_ngrok_successful_probe_is_not_repeated_every_poll(self):
        app = Mock(config={'TEO_INSTANCE': 'ours'})
        settings = {}
        address = dict(self.address, provider='ngrok', url='https://assigned.ngrok-free.dev')
        probe = Mock(return_value=True)
        tunnel = FixedTunnel(app, settings, self.root, 8765, address, probe=probe)
        calls = []
        def wait(_seconds):
            calls.append(1)
            if len(calls) == 3:
                tunnel.stop_event.set()
                return True
            return False
        process = Mock()
        process.poll.return_value = None
        with (patch('teodub_web.fixed_tunnel.connector_command', return_value=(['fake-connector'], {})),
              patch('teodub_web.fixed_tunnel.subprocess.Popen', return_value=process),
              patch.object(tunnel.stop_event, 'wait', side_effect=wait),
              patch('teodub_web.fixed_tunnel.time.monotonic', side_effect=[100,100,200])):
            tunnel.loop()
        probe.assert_called_once_with(address['url'], 'ours')
        self.assertEqual(settings['address_state'], 'online')

    def test_static_host_tls_cookie_login_origin_and_staff_scope(self):
        store = Store(self.root)
        store.add_user('staff', 'Staff', 'password-for-testing-123')
        settings = dict(secret='test-secret', fixed_public_url=self.address['url'])
        app = create_app(store, settings)
        client = app.test_client()
        headers = {'Host': 'teo.example.com', 'X-Forwarded-Proto': 'https'}
        login = client.get('/login', headers=headers)
        self.assertEqual(login.status_code, 200)
        self.assertIn('Secure', login.headers['Set-Cookie'])
        csrf = login.data.decode().split('name="csrf" value="')[1].split('"')[0]
        response = client.post('/login', headers=dict(headers, Origin=self.address['url']),
                               data=dict(username='staff', password='password-for-testing-123', csrf=csrf))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(client.get('/api/jobs', headers=headers).status_code, 200)
        self.assertIsNone(client.get('/api/jobs', headers=headers).json['public_address'])
        self.assertEqual(client.get('/login', headers={'Host':'other.example.com'}).status_code, 400)
        self.assertEqual(client.post('/logout', headers=dict(headers, Origin='https://evil.example.com'),
                                    data=dict(csrf=csrf)).status_code, 403)
        self.assertEqual(client.get('/health', headers=headers).json['instance'], app.config['TEO_INSTANCE'])

    def test_listener_verifies_venv_module_root_port_and_base_python(self):
        executable = address_restart.PROJECT/'.venv-web'/'Scripts'/'python.exe'
        info = dict(executable=sys._base_executable,
                    command=subprocess.list2cmdline([str(executable), '-m', 'teodub_web.server', '--root', str(self.root)]))
        self.assertTrue(address_restart.owned_listener(info, self.root))
        for command in ([str(executable), '-m','other'],
                        [str(executable), '-m','teodub_web.server','--root',str(self.root),'--port','9999'],
                        [str(executable), '-m','teodub_web.server','--root',str(self.root/'other')]):
            self.assertFalse(address_restart.owned_listener(dict(info, command=subprocess.list2cmdline(command)), self.root))

    def test_restart_refuses_active_jobs_and_preserves_database(self):
        store = Store(self.root)
        owner = store.add_user('staff', 'Staff', 'password-for-testing-123')
        store.submit(owner, 'Video', 'https://www.douyin.com/video/123', {}, 'a'*32)
        execution = self.root/'execution'
        atomic_json(self.root/'settings.json', dict(execution_lock=str(execution)))
        with (patch.object(address_restart, 'listener', return_value=None),
             patch('teodub_web.address_restart.start_background') as start):
            with self.assertRaisesRegex(ValueError, 'công việc chờ/chạy'):
                address_restart.restart_when_idle(self.root)
        start.assert_not_called()
        self.assertEqual(store.jobs()[0]['state'], 'queued')

    def test_restart_does_not_close_foreign_listener(self):
        with (patch.object(address_restart, 'listener', return_value={'command':'other','executable':'other'}),
             patch('teodub_web.address_restart.start_background') as start):
            with self.assertRaisesRegex(ValueError, 'chương trình khác'):
                address_restart.restart_when_idle(self.root)
        start.assert_not_called()

    def test_idle_restart_holds_submission_lock_before_closing_only_owned_service(self):
        store = Store(self.root)
        atomic_json(self.root/'settings.json', dict(execution_lock=str(self.root/'execution')))
        executable = address_restart.PROJECT/'.venv-web'/'Scripts'/'python.exe'
        info = dict(pid=123, executable=sys._base_executable,
                    command=subprocess.list2cmdline([str(executable), '-m', 'teodub_web.server', '--root', str(self.root)]))
        handle = Mock()
        def terminate(selected_handle, _code):
            self.assertIs(selected_handle, handle)
            connection = sqlite3.connect(store.path, timeout=0)
            try:
                with self.assertRaises(sqlite3.OperationalError):
                    connection.execute("INSERT INTO audit VALUES(1,NULL,'racing-submit',NULL)")
            finally:
                connection.close()
        with (patch.object(address_restart, 'listener', return_value=info),
              patch('win32api.OpenProcess', return_value=handle),
              patch('win32process.GetModuleFileNameEx', return_value=sys._base_executable),
              patch('win32api.TerminateProcess', side_effect=terminate) as stop,
              patch('win32event.WaitForSingleObject', return_value=0),
              patch('teodub_web.address_restart.start_background') as start):
            address_restart.restart_when_idle(self.root)
        stop.assert_called_once()
        start.assert_called_once()
        handle.Close.assert_called_once()
        self.assertEqual(store.jobs(), [])

    def test_restart_while_desktop_execution_busy_preserves_service(self):
        from teodub.pipeline import _job_lock
        Store(self.root)
        execution = self.root/'execution'
        execution.mkdir()
        atomic_json(self.root/'settings.json', dict(execution_lock=str(execution)))
        with (_job_lock(execution), patch.object(address_restart, 'listener', return_value=None),
              patch('teodub_web.address_restart.start_background') as start):
            with self.assertRaisesRegex(ValueError, 'Máy đang xử lý'):
                address_restart.restart_when_idle(self.root)
        start.assert_not_called()


class WebAddressUITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root_path = Path(self.temp.name)
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.tab = AddressSetup(self.root, self.root_path)

    def test_save_local_token_then_keep_sealed_connection_when_token_blank(self):
        self.tab.url.set('teo.example.com')
        self.tab.token.set('SyntheticTokenForUnitTestOnly123456789')
        self.assertTrue(self.tab.save())
        reference = load_address(self.root_path)['credential']
        self.assertEqual(self.tab.token.get(), '')
        self.assertTrue(self.tab.save())
        self.assertEqual(load_address(self.root_path)['credential'], reference)
        self.assertNotIn('SyntheticToken', (self.root_path/'web-address.json').read_text())

    def test_invalid_address_does_not_replace_saved_config(self):
        self.tab.url.set('teo.example.com')
        self.tab.token.set('SyntheticTokenForUnitTestOnly123456789')
        self.tab.save()
        before = load_address(self.root_path)
        self.tab.url.set('http://invalid.local')
        self.assertFalse(self.tab.save())
        self.assertEqual(load_address(self.root_path), before)

    def test_save_does_not_restart_service_and_apply_busy_keeps_profile(self):
        self.tab.mode.set('quick')
        with patch('teodub_web.address_restart.restart_when_idle', side_effect=ValueError('Web đang bận')) as restart:
            self.assertTrue(self.tab.save())
            restart.assert_not_called()
            self.tab.apply()
            restart.assert_called_once_with(self.root_path)
        self.assertIn('đang bận', self.tab.status.get())
        self.assertEqual(load_address(self.root_path)['mode'], 'quick')


if __name__ == '__main__':
    unittest.main()
