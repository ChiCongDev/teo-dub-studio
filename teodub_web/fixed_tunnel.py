"""Keep a configured URL; never silently replace it with a random trial URL."""
import atexit
import json
import os
from pathlib import Path
import subprocess
import threading
import time
from urllib.request import ProxyHandler, build_opener, Request

from teodub.common import atomic_json
from teodub.web_address import normalize_address
from teodub_web.address_credentials import AddressCredentials

PROJECT = Path(__file__).resolve().parents[1]


def public_probe(url, instance):
    try:
        request = Request(url+'/health', headers={'ngrok-skip-browser-warning': '1',
                                                  'User-Agent': 'TeoWebAddressCheck/1.0'})
        with build_opener(ProxyHandler({})).open(request, timeout=4) as response:
            body = json.loads(response.read(4096))
            return (response.status == 200 and response.geturl() == url+'/health'
                    and body.get('instance') == instance and body.get('status') == 'ok')
    except (OSError, ValueError, AttributeError):
        return False


def connector_command(address, root, port, *, project=PROJECT):
    address = normalize_address(address)
    if address['mode'] != 'fixed':
        raise ValueError('Chưa cấu hình địa chỉ cố định.')
    token = AddressCredentials(root).load(address['provider'], address['credential'])
    env = os.environ.copy()
    for key in ('TUNNEL_TOKEN', 'TUNNEL_TOKEN_FILE', 'NGROK_AUTHTOKEN'):
        env.pop(key, None)
    if address['provider'] == 'cloudflare':
        executable = project/'tools'/'cloudflared.exe'
        command = [str(executable), 'tunnel', '--no-autoupdate', '--protocol', 'http2', 'run']
        env['TUNNEL_TOKEN'] = token
    else:
        executable = project/'tools'/'ngrok.exe'
        # This file contains no token. Authentication stays in the child's environment.
        config = root/'server'/'ngrok-agent.yml'
        config.write_text('version: "3"\nagent:\n  web_addr: false\n  inspect_db_size: -1\n'
                          '  remote_management: false\n  update_check: false\n', encoding='utf-8')
        command = [str(executable), 'http', f'http://127.0.0.1:{port}', '--url', address['url'],
                   '--config', str(config), '--inspect=false', '--log=stdout', '--log-format=json']
        env['NGROK_AUTHTOKEN'] = token
    if not executable.is_file():
        raise ValueError('Chưa có bộ kết nối '+address['provider']+'. Mở cấu hình địa chỉ web để cài bộ kết nối.')
    return command, env


class FixedTunnel:
    def __init__(self, app, settings, root, port, address, *, probe=public_probe):
        self.app, self.settings, self.root, self.port = app, settings, Path(root), port
        self.address, self.probe = normalize_address(address), probe
        self.stop_event = threading.Event()
        self.process = None
        self.thread = None
        self.checked_at = None

    def status(self, state, message):
        atomic_json(self.root/'address-status.json', dict(version=1, mode='fixed', provider=self.address['provider'],
                    url=self.address['url'], state=state, message=message, time=time.time(),
                    checked_at=self.checked_at,
                    instance=self.app.config['TEO_INSTANCE']))

    def start(self):
        (self.root/'server').mkdir(parents=True, exist_ok=True)
        # Retain the stable address even while reconnecting; status is separate.
        (self.root/'LINK_WEB.txt').write_text(self.address['url']+'\n', encoding='utf-8')
        self.settings['public_url'] = self.address['url']
        self.settings['address_state'] = 'connecting'
        self.status('connecting', 'Đang kết nối địa chỉ cố định.')
        self.thread = threading.Thread(target=self.loop, daemon=True, name='teo-fixed-address')
        self.thread.start()
        atexit.register(self.close)
        return self

    def close(self):
        self.stop_event.set()
        process = self.process
        if process is not None and process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass

    def loop(self):
        while not self.stop_event.is_set():
            try:
                command, env = connector_command(self.address, self.root, self.port)
                # Connector output is deliberately not persisted: an upstream error
                # could echo an account credential. Only our own status is recorded.
                self.process = subprocess.Popen(command, env=env, cwd=PROJECT,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                if self.stop_event.is_set():
                    self.close()
                next_probe = 0
                while not self.stop_event.wait(8) and self.process.poll() is None:
                    if time.monotonic() < next_probe:
                        continue
                    ready = self.probe(self.address['url'], self.app.config['TEO_INSTANCE'])
                    # Free ngrok meters these requests too. Avoid consuming its
                    # monthly quota with a continuous external health poll.
                    next_probe = time.monotonic()+(1800 if ready and self.address['provider']=='ngrok' else 60)
                    if ready:
                        self.checked_at = time.time()
                    self.settings['address_state'] = 'online' if ready else 'connecting'
                    self.status(self.settings['address_state'], 'Địa chỉ cố định đang hoạt động.' if ready else
                        'Chưa xác nhận được kết nối. Kiểm tra DNS, tuyến web về http://127.0.0.1:8765 và tài khoản dịch vụ.')
                if self.stop_event.is_set():
                    break
                self.settings['address_state'] = 'offline'
                self.status('offline', 'Bộ kết nối đã dừng. Kiểm tra mã kết nối hoặc hạn mức của dịch vụ; tự thử lại sau 60 giây.')
            except Exception:
                self.settings['address_state'] = 'offline'
                self.status('offline', 'Chưa mở được kết nối cố định. Mở cấu hình trên máy để kiểm tra bộ kết nối và mã đã lưu.')
            if self.stop_event.wait(60):
                break
