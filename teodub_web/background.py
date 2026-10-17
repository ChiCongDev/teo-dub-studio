"""Launch the local web independently of the window/process that opened it."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import time
from urllib.request import ProxyHandler, build_opener

from teodub.web_tracking import web_data_root

PROJECT = Path(__file__).resolve().parents[1]


def detached_process(command, directory):
    if os.name != 'nt':
        raise ValueError('Mở web nền hiện hỗ trợ trên Windows.')
    # WMI creates the process outside the caller's Job Object. Break away from
    # the WMI provider's job too; the server still protects its own descendants.
    request = json.dumps(dict(command=subprocess.list2cmdline(command), directory=str(directory)))
    try:
        result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive',
            '-ExecutionPolicy', 'Bypass', '-File', str(PROJECT/'tools'/'web_background.ps1')],
            input=request, capture_output=True, text=True, encoding='utf-8', errors='replace',
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), timeout=15)
        reply = json.loads(result.stdout.lstrip('\ufeff'))
        if result.returncode or reply.get('code') != 0 or type(reply.get('pid')) is not int or reply['pid'] <= 0:
            raise ValueError()
    except (OSError, subprocess.SubprocessError, ValueError, AttributeError):
        # Never fall back to an inherited child, and never echo request/arguments.
        raise ValueError('Windows chưa mở được Tèo Web chạy nền. Kiểm tra nhật ký trên máy và thử lại.') from None
    return reply['pid']


def start_background(root):
    root = Path(root).resolve()
    launched_at = time.time()
    detached_process([str(PROJECT/'.venv-web'/'Scripts'/'python.exe'), '-X', 'utf8', '-B', '-m',
                      'teodub_web.server', '--root', str(root), '--public', '--background-logs'], PROJECT)
    opener = build_opener(ProxyHandler({}))
    deadline = time.monotonic()+12
    while time.monotonic() < deadline:
        try:
            instance = json.loads((root/'server-instance.json').read_text(encoding='utf-8'))
            with opener.open('http://127.0.0.1:8765/health', timeout=1) as response:
                health = json.loads(response.read(4096))
                if (response.status == 200 and instance['time'] >= launched_at-1
                        and health.get('status') == 'ok' and health.get('instance') == instance['instance']):
                    return instance['pid']
        except (OSError, ValueError, KeyError, TypeError):
            pass
        time.sleep(.25)
    raise ValueError('Tèo Web chưa sẵn sàng. Kiểm tra server-error.log trên máy; không mở thêm phiên trùng.')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root')
    args = parser.parse_args()
    try:
        start_background(web_data_root(args.root))
    except (OSError, ValueError) as exc:
        print(str(exc))
        return 1
    print('Tèo Web đã chạy nền trên máy.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
