"""Apply local connection settings only when both web and desktop execution are idle."""
import ctypes
import json
from pathlib import Path
import sqlite3
import subprocess
import os
import sys

from teodub.pipeline import _job_lock
from teodub.web_address import load_address
from teodub.web_tracking import web_data_root
from teodub_web.fixed_tunnel import connector_command
from teodub_web.background import start_background

PROJECT = Path(__file__).resolve().parents[1]


def command_arguments(command):
    count = ctypes.c_int()
    function = ctypes.windll.shell32.CommandLineToArgvW
    function.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int)]
    function.restype = ctypes.POINTER(ctypes.c_wchar_p)
    pointer = function(command, ctypes.byref(count))
    if not pointer:
        raise ValueError('Chưa xác định được phiên web đang mở.')
    try:
        return [pointer[index] for index in range(count.value)]
    finally:
        ctypes.windll.kernel32.LocalFree(ctypes.cast(pointer, ctypes.c_void_p))


def listener():
    try:
        result = subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(PROJECT/'tools'/'web_listener.ps1')],
                capture_output=True, text=True, encoding='utf-8', errors='replace',
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), timeout=8)
    except subprocess.SubprocessError:
        raise ValueError('Chưa kiểm tra được máy chủ web. Cấu hình đã lưu; thử áp dụng lại sau ít giây.') from None
    if result.returncode:
        raise ValueError('Chưa kiểm tra được máy chủ web đang mở. Cấu hình đã lưu; thử áp dụng lại khi web nghỉ.')
    try:
        return json.loads(result.stdout.lstrip('\ufeff'))
    except ValueError:
        raise ValueError('Chưa xác định được máy chủ web đang mở.') from None


def owned_listener(info, root):
    if not info or not isinstance(info.get('command'), str) or not info.get('executable'):
        return False
    executable = (PROJECT/'.venv-web'/'Scripts'/'python.exe').resolve()
    if Path(info['executable']).resolve() not in (executable, Path(sys._base_executable).resolve()):
        return False
    try:
        arguments = command_arguments(info['command'])
        module_index = arguments.index('-m')
        active_root = (Path(arguments[arguments.index('--root')+1]).resolve() if '--root' in arguments
                       else web_data_root())
        port = int(arguments[arguments.index('--port')+1]) if '--port' in arguments else 8765
        return (Path(arguments[0]).resolve() == executable and arguments[module_index+1] == 'teodub_web.server'
                and active_root == Path(root).resolve() and port == 8765)
    except (ValueError, IndexError):
        return False


def restart_when_idle(root):
    if os.name != 'nt':
        raise ValueError('Áp dụng kết nối hiện hỗ trợ trên máy Windows xử lý video.')
    import win32api
    import win32con
    import win32event
    import win32process
    import pywintypes
    root = Path(root).resolve()
    address = load_address(root)
    (root/'server').mkdir(exist_ok=True)
    if address['mode'] == 'fixed':
        # Check token decryption and connector availability before touching a running service.
        connector_command(address, root, 8765)
    info = listener()
    if info and not owned_listener(info, root):
        raise ValueError('Cổng web đang được một chương trình khác dùng. Không đóng chương trình đó; cấu hình đã được lưu.')
    settings = json.loads((root/'settings.json').read_text(encoding='utf-8'))
    execution = Path(settings['execution_lock']).resolve()
    execution.mkdir(parents=True, exist_ok=True)
    try:
        with _job_lock(execution):
            db = sqlite3.connect((root/'portal.sqlite3').as_uri()+'?mode=rw', uri=True, timeout=.4)
            handle = None
            try:
                # Freeze submissions and claims while checking idle and closing only our listener.
                db.execute('BEGIN IMMEDIATE')
                active = db.execute("SELECT count(*) FROM jobs WHERE state IN ('queued','running','stopping')").fetchone()[0]
                if active:
                    raise ValueError(f'Web còn {active} công việc chờ/chạy. Đợi xử lý xong rồi bấm Áp dụng lại; '
                                     'cấu hình đã lưu và việc hiện tại tiếp tục bình thường.')
                current = listener()
                if current != info:
                    raise ValueError('Phiên web vừa thay đổi. Không đóng phiên mới; bấm Áp dụng lại sau vài giây.')
                if current:
                    handle = win32api.OpenProcess(win32con.PROCESS_TERMINATE | win32con.PROCESS_QUERY_INFORMATION | win32con.PROCESS_VM_READ
                                                  | win32con.SYNCHRONIZE, False, current['pid'])
                    actual = Path(win32process.GetModuleFileNameEx(handle, 0)).resolve()
                    if actual not in ((PROJECT/'.venv-web'/'Scripts'/'python.exe').resolve(), Path(sys._base_executable).resolve()):
                        raise ValueError('Không xác nhận được máy chủ Tèo. Giữ chương trình đang mở.')
                    win32api.TerminateProcess(handle, 0)
                    if win32event.WaitForSingleObject(handle, 5000) != win32con.WAIT_OBJECT_0:
                        raise ValueError('Máy chủ cũ chưa đóng xong. Cấu hình đã lưu; mở Tèo Web sau ít giây.')
            finally:
                if handle is not None:
                    handle.Close()
                db.close()
    except sqlite3.Error:
        raise ValueError('Hàng đợi đang bận cập nhật. Cấu hình đã lưu, bấm Áp dụng lại khi web nghỉ.') from None
    except pywintypes.error:
        raise ValueError('Windows chưa cho mở lại máy chủ web. Cấu hình đã lưu; kiểm tra phiên đang chạy trên máy.') from None
    except ValueError as exc:
        if 'tiến trình khác' in str(exc):
            raise ValueError('Máy đang xử lý một phiên Tèo. Cấu hình đã lưu; áp dụng khi phiên đã xong.') from None
        raise
    start_background(root)
