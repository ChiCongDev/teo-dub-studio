import hashlib
import json
import os
from pathlib import Path
import uuid
import time


class Cancelled(Exception):
    pass


class FileCancel:
    def __init__(self, path):
        self.path = Path(path)

    def is_set(self):
        return self.path.exists()


def check_cancel(cancel=None):
    if cancel is not None and cancel.is_set():
        raise Cancelled('Đã dừng. Các bước đã hoàn tất được giữ để tiếp tục.')


SLEEP_GAP_S = 60


class AwakeTime:
    """Seconds the computer was awake between frequent polls of a local process.

    time.monotonic() keeps counting while Windows sleeps, so a plain deadline fires
    the moment the computer wakes (21/10: a render stopped 12 s before the end).
    A gap longer than SLEEP_GAP_S between two polls is sleep, not work.
    """
    def __init__(self, now):
        self.last, self.seconds = now, 0.

    def update(self, now):
        step, self.last = now-self.last, now
        if 0 <= step <= SLEEP_GAP_S:
            self.seconds += step
        return self.seconds


def hash_file(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as file:
        for block in iter(lambda: file.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('x', encoding='utf-8') as file:
            json.dump(value, file, ensure_ascii=False, indent=2)
            file.flush()
            os.fsync(file.fileno())
        # Windows readers may briefly hold a handle without FILE_SHARE_DELETE.
        # Keep the old complete document visible while a bounded retry waits.
        for attempt in range(7):
            try:
                os.replace(temporary, path)
                break
            except OSError as exc:
                if os.name != 'nt' or getattr(exc, 'winerror', None) not in (5, 32, 33) or attempt == 6:
                    raise
                time.sleep(.01 * 2**attempt)
    finally:
        temporary.unlink(missing_ok=True)


_worker_job = None


def protect_worker():
    """Terminate only this worker's descendants when its process exits."""
    global _worker_job
    if os.name != 'nt' or _worker_job is not None:
        return
    import win32api
    import win32job
    job = win32job.CreateJobObject(None, '')
    limits = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
    limits['BasicLimitInformation']['LimitFlags'] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, limits)
    try:
        win32job.AssignProcessToJobObject(job, win32api.GetCurrentProcess())
    except Exception:
        job.Close()
        raise ValueError('Chưa tạo được phiên xử lý riêng có thể dừng an toàn.') from None
    _worker_job = job.Detach()
