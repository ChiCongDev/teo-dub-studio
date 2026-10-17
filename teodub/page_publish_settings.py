"""Local switch for result checks; independent of posting and login settings."""
from contextlib import contextmanager
import json
from pathlib import Path
import threading

from teodub.common import atomic_json


_RESULT_CHECKS_LOCK = threading.RLock()


def _path(data_dir):
    return Path(data_dir).resolve() / 'page-publishing' / 'publish-settings.json'


def _read(data_dir):
    path = _path(data_dir)
    try:
        saved = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return True  # Existing installations retain their choice until changed.
    except (OSError, ValueError, TypeError):
        return False
    return (isinstance(saved, dict) and type(saved.get('version')) is int and saved.get('version') == 1
            and saved.get('result_checks_enabled') is True)


def result_checks_enabled(data_dir):
    with _RESULT_CHECKS_LOCK:
        return _read(data_dir)


@contextmanager
def result_checks_guard(data_dir):
    """Linearize a final result commit against a UI enable/disable change."""
    with _RESULT_CHECKS_LOCK:
        yield _read(data_dir)


def set_result_checks_enabled(data_dir, enabled):
    if type(enabled) is not bool:
        raise ValueError('Tùy chọn kiểm tra trạng thái phải là bật hoặc tắt.')
    with _RESULT_CHECKS_LOCK:
        path = _path(data_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_json(path, dict(version=1, result_checks_enabled=enabled))
    return enabled
