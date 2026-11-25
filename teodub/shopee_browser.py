"""Owner 25/11/2026: "Mở Shopee" opens an account's own Chrome profile in a PLAIN Chrome (no automation: Shopee refuses an automated
Chrome) so the owner can take Shopee affiliate links with that account. Nothing here hides automation; Tèo's posting Chrome is unchanged.

One Chrome at a time can hold a profile, so while this plain Chrome is open Tèo cannot open its own Chrome for the account: posts wait
(page_schedule_shopee), numbers wait, Load Page/Mở Facebook refuse. When a post is due, the owner is asked; only a Chrome THIS module
opened (pid + start time remembered) may be closed by Tèo, and only gently (the window's close message, like the X button), never killed.
No Tk here."""
from datetime import datetime
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
import time

from teodub.common import atomic_json

SHOPEE_URL = 'https://shopee.vn/'
RECORD_FILE = 'shopee-chrome.json'  # {account id: dict(pid, created, opened_at)} in page-publishing/
CLOSE_WAIT_SECONDS = 20  # After the close message, how long Tèo waits for Chrome to end before it says it could not.
SOON_MINUTES = 15  # The button refuses when a post of the account falls due within this many minutes.

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_SYNCHRONIZE = 0x00100000
_WM_CLOSE = 0x0010
_WAIT_OBJECT_0 = 0


def chrome_executable(environ=None):
    """The installed Google Chrome (the same one Tèo's Playwright uses), or None."""
    environ = os.environ if environ is None else environ
    for variable in ('PROGRAMFILES', 'PROGRAMW6432', 'PROGRAMFILES(X86)', 'LOCALAPPDATA'):
        root = environ.get(variable)
        if root and (Path(root)/'Google/Chrome/Application/chrome.exe').is_file():
            return Path(root)/'Google/Chrome/Application/chrome.exe'
    return None


def profile_busy(chrome_dir):
    """True while some Chrome holds this profile. Chrome keeps `<profile>/lockfile` open (no write sharing, deleted on close) for as
    long as it runs; a lockfile left by a power cut opens for writing and counts as free. Nothing is written."""
    lock = Path(chrome_dir)/'lockfile'
    if not lock.exists():
        return False
    try:
        handle = os.open(lock, os.O_WRONLY)
    except FileNotFoundError:
        return False  # Chrome closed between the two looks.
    except PermissionError:
        return True
    os.close(handle)
    return False


def _kernel32():
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.GetProcessTimes.argtypes = (wintypes.HANDLE,)+(ctypes.POINTER(wintypes.FILETIME),)*4
    kernel.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    return kernel


def process_created(pid):
    """The start time (FILETIME ticks) of a RUNNING process `pid`, or None when it has ended. With the pid it names one process
    (a pid is reused by Windows; the start time is not)."""
    kernel = _kernel32()
    handle = kernel.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return None
    try:
        code = wintypes.DWORD()
        if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value != 259:  # 259 = STILL_ACTIVE
            return None
        times = [wintypes.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(handle, *(ctypes.byref(item) for item in times)):
            return None
        return (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
    finally:
        kernel.CloseHandle(handle)


def record_path(data_dir):
    return Path(data_dir)/'page-publishing'/RECORD_FILE


def read_records(data_dir):
    """The Chromes this button opened. A missing or damaged file means none."""
    try:
        data = json.loads(record_path(data_dir).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {key: value for key, value in data.items()
            if isinstance(value, dict) and type(value.get('pid')) is int and type(value.get('created')) is int}


def _write_records(data_dir, records):
    path = record_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(path, records)


def opened_by_button(data_dir, account_id, chrome_dir):
    """The pid of the plain Chrome this button opened for the account, while it still runs and holds the profile; else None
    (and a stale record is forgotten)."""
    records = read_records(data_dir)
    entry = records.get(account_id)
    if not entry:
        return None
    if process_created(entry['pid']) == entry['created'] and profile_busy(chrome_dir):
        return entry['pid']
    records.pop(account_id, None)
    try:
        _write_records(data_dir, records)
    except OSError:
        pass
    return None


def open_shopee(data_dir, account_id, chrome_dir, *, executable=None, url=SHOPEE_URL, popen=subprocess.Popen):
    """Open the account's profile in a plain Chrome at Shopee and remember which process it is. Raises ValueError (Vietnamese) when
    the profile is already held by a Chrome or Chrome is missing."""
    chrome_dir = Path(chrome_dir)
    if profile_busy(chrome_dir):
        raise ValueError('Chrome của tài khoản này đang mở. Tắt Chrome đó (bấm X) rồi bấm Mở Shopee lại.')
    executable = executable or chrome_executable()
    if not executable:
        raise ValueError('Chưa thấy Google Chrome trên máy.')
    chrome_dir.mkdir(parents=True, exist_ok=True)
    flags = getattr(subprocess, 'DETACHED_PROCESS', 0) | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0)
    process = popen([str(executable), '--user-data-dir='+str(chrome_dir), '--new-window', url],
                    creationflags=flags, close_fds=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL)
    created = None
    for _ in range(20):  # The process exists at once; its start time is read right away (a few tries if Windows is slow).
        created = process_created(process.pid)
        if created is not None:
            break
        time.sleep(0.05)
    if created is None:
        raise ValueError('Chrome vừa mở đã đóng ngay. Thử bấm Mở Shopee lại.')
    records = read_records(data_dir)
    records[account_id] = dict(pid=int(process.pid), created=int(created),
                               opened_at=datetime.now().astimezone().isoformat(timespec='seconds'))
    _write_records(data_dir, records)
    return int(process.pid)


def _windows_of(pid):
    user = ctypes.WinDLL('user32', use_last_error=True)
    user.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    user.IsWindowVisible.argtypes = (wintypes.HWND,)
    user.GetWindow.argtypes = (wintypes.HWND, wintypes.UINT)
    user.GetWindow.restype = wintypes.HWND
    user.PostMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    found = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def visit(hwnd, _):
        owner = wintypes.DWORD()
        user.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user.IsWindowVisible(hwnd) and not user.GetWindow(hwnd, 4):  # 4 = GW_OWNER: top-level only
            found.append(hwnd)
        return True

    user.EnumWindows(callback_type(visit), 0)
    return user, found


def close_gently(pid, created, *, wait_seconds=CLOSE_WAIT_SECONDS):
    """Ask Chrome `pid` (only if it is still the process started at `created`) to close, as the X button does, and wait for it to
    end. Never kills it. True when it ended (or was already gone); False when it is still open (a 'leave this page?' box, say)."""
    if process_created(pid) != created:
        return True
    kernel = _kernel32()
    handle = kernel.OpenProcess(_SYNCHRONIZE | _PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return True
    try:
        deadline = time.monotonic()+wait_seconds
        while True:
            user, windows = _windows_of(pid)  # Each round, like pressing X again on every window still open.
            for hwnd in windows:
                user.PostMessageW(hwnd, _WM_CLOSE, 0, 0)
            left = deadline-time.monotonic()
            if kernel.WaitForSingleObject(handle, int(max(0, min(2, left))*1000)) == _WAIT_OBJECT_0:
                return True
            if left <= 2:
                return False
    finally:
        kernel.CloseHandle(handle)


def close_account_chrome(data_dir, account_id, chrome_dir, *, wait_seconds=CLOSE_WAIT_SECONDS):
    """Close the plain Chrome this button opened for the account (gently). True when no such Chrome is open any more."""
    entry = read_records(data_dir).get(account_id)
    if not entry or not opened_by_button(data_dir, account_id, chrome_dir):
        return not profile_busy(chrome_dir)
    closed = close_gently(entry['pid'], entry['created'], wait_seconds=wait_seconds)
    if closed:
        opened_by_button(data_dir, account_id, chrome_dir)  # Forgets the record.
    return closed and not profile_busy(chrome_dir)
