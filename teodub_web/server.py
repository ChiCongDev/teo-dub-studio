import argparse
import atexit
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import threading
import time

from waitress import serve

from teodub.common import atomic_json, protect_worker
from teodub.pipeline import _job_lock
from teodub_web.app import create_app
from teodub_web.runner import PROJECT, Runner
from teodub_web.store import Store
from teodub.web_address import load_address
from teodub_web.fixed_tunnel import FixedTunnel


def initialize(root):
    store = Store(root)
    settings_path = store.root/'settings.json'
    if settings_path.exists():
        settings = json.loads(settings_path.read_text(encoding='utf-8'))
    else:
        local_data = Path(os.environ.get('LOCALAPPDATA', Path.home()))/'TeoDubStudio'
        drive = Path.home()/'Videos'/'Teo Drive'
        settings = dict(version=1, secret=secrets.token_hex(32), drive_folder=str(drive),
                        execution_lock=str(local_data/'execution'), public_url='')
        atomic_json(settings_path, settings)
    if not store.users():
        password = secrets.token_urlsafe(18)
        store.add_user('quantri', 'Quản trị viên', password, role='admin')
        (store.root/'TAI_KHOAN_QUAN_TRI.txt').write_text(
            'TÈO WEB — TÀI KHOẢN QUẢN TRỊ RIÊNG\n\nTên đăng nhập: quantri\nMật khẩu: '+password+
            '\n\nMở web và chọn Đổi mật khẩu trước khi cấp tài khoản nhân viên.\n'
            'Không gửi tệp này cho nhân viên. Mỗi nhân viên được tạo tài khoản riêng trong mục Nhân viên.\n', encoding='utf-8')
    return store, settings


def start_tunnel(app, settings, root, port):
    exe = PROJECT/'tools'/'cloudflared.exe'
    if not exe.is_file():
        print('Chưa có bộ nối web. Web trên máy vẫn hoạt động.', flush=True)
        return None
    process = subprocess.Popen([str(exe), 'tunnel', '--url', f'http://127.0.0.1:{port}',
                                '--no-autoupdate', '--protocol', 'http2'],
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                               encoding='utf-8', errors='replace',
                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    atexit.register(lambda: process.terminate() if process.poll() is None else None)

    def monitor():
        with (root/'tunnel.log').open('a', encoding='utf-8') as log:
            for line in process.stdout:
                log.write(line)
                log.flush()
                match = re.search(r'https://([a-z0-9-]+\.trycloudflare\.com)', line)
                if match:
                    host = match[1]
                    app.config['TRUSTED_HOSTS'] = ['127.0.0.1', 'localhost', host]
                    settings['public_url'] = 'https://'+host
                    (root/'LINK_WEB.txt').write_text(settings['public_url']+'\n', encoding='utf-8')
                    print('Web thử: '+settings['public_url'], flush=True)
            settings['public_url'] = ''
            print('Đường dẫn online đã dừng. Web trên máy vẫn hoạt động.', flush=True)

    threading.Thread(target=monitor, daemon=True, name='teo-web-tunnel').start()
    return process


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', default=str(Path(os.environ.get('LOCALAPPDATA', Path.home()))/'TeoDubWeb'))
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--public', action='store_true')
    parser.add_argument('--background-logs', action='store_true')
    args = parser.parse_args()
    if args.background_logs:
        root = Path(args.root).resolve()
        root.mkdir(parents=True, exist_ok=True)
        sys.stdout = (root/'server.log').open('a', encoding='utf-8', buffering=1)
        sys.stderr = (root/'server-error.log').open('a', encoding='utf-8', buffering=1)
    store, settings = initialize(args.root)
    # Persist the physical path: Windows packaged-host redirection can differ
    # from a later launch from Explorer, so do not derive a second data store.
    (PROJECT/'tools'/'web-data-root.txt').write_text(str(store.root)+'\n', encoding='utf-8')
    settings['public_url'] = ''
    try:
        address = load_address(store.root)
    except ValueError:
        address = None
        print('Cấu hình địa chỉ web cần kiểm tra. Chỉ mở web trên máy, không tự thay bằng link tạm.', flush=True)
    if address and address['mode'] == 'fixed':
        settings['fixed_public_url'] = address['url']
    (store.root/'server').mkdir(exist_ok=True)
    try:
        with _job_lock(store.root/'server'):
            protect_worker()
            app = create_app(store, settings)
            atomic_json(store.root/'server-instance.json', dict(version=1, pid=os.getpid(),
                        instance=app.config['TEO_INSTANCE'], port=args.port, time=time.time()))
            runner = Runner(store, settings)
            runner.start()
            atexit.register(runner.stop.set)
            if args.public:
                if address and address['mode'] == 'fixed':
                    FixedTunnel(app, settings, store.root, args.port, address).start()
                elif address:
                    start_tunnel(app, settings, store.root, args.port)
            print(f'Tèo Web trên máy: http://127.0.0.1:{args.port}', flush=True)
            serve(app, host='127.0.0.1', port=args.port, threads=8,
                  max_request_body_size=16384, max_request_header_size=16384,
                  channel_timeout=30, clear_untrusted_proxy_headers=False)
    except ValueError:
        print('Tèo Web đã chạy hoặc chưa khóa được phiên máy chủ.', flush=True)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
