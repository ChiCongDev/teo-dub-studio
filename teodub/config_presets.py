"""Cấu hình đã lưu (owner 10/11/2026): named sets of the settings of "Chỉnh sửa cấu hình" (voice, volume, Sub, picture speed, wording and
AI models), kept in one file for every link, to fill the boxes of that window again at any time. A preset is only a way to fill the
boxes: nothing is applied to a link until the owner presses "Lưu chỉnh sửa", and the Google Drive choice of a link is never part of it.
The file is written by the desktop only, atomically; a damaged file or entry is ignored, never fatal."""
from datetime import datetime
import json
from pathlib import Path
import uuid

from teodub.common import atomic_json

PRESETS_FILE = 'config-presets.json'
MAX_NAME = 60
MAX_PRESETS = 200
NOT_PRESET_KEYS = ('drive_upload', 'drive_data_dir')  # Each link keeps its own Google Drive folder.


def clean_name(text):
    """The name as it is kept, or ValueError with the message to show."""
    name = ' '.join(str(text if text is not None else '').split())
    if not name:
        raise ValueError('Gõ tên cho cấu hình.')
    if len(name) > MAX_NAME:
        raise ValueError(f'Tên dài quá {MAX_NAME} ký tự.')
    if any(ord(char) < 32 for char in name):
        raise ValueError('Tên có ký tự không hợp lệ.')
    return name


def preset_settings(captured):
    """What is kept of the boxes: everything the window reads except the Google Drive choice."""
    return {key: value for key, value in captured.items() if key not in NOT_PRESET_KEYS}


def read_presets(data_dir):
    """The saved presets, oldest first. A missing or damaged file means none; a damaged entry is left out."""
    try:
        saved = json.loads((Path(data_dir)/PRESETS_FILE).read_text(encoding='utf-8'))
        rows = saved['presets']
    except (OSError, ValueError, KeyError, TypeError):
        return []
    return [row for row in rows if isinstance(row, dict) and isinstance(row.get('id'), str) and isinstance(row.get('name'), str)
            and row['name'].strip() and isinstance(row.get('settings'), dict)] if isinstance(rows, list) else []


def _write(data_dir, presets):
    atomic_json(Path(data_dir)/PRESETS_FILE, dict(version=1, presets=presets))


def find_by_name(presets, name):
    key = ' '.join(str(name).split()).casefold()
    return next((row for row in presets if ' '.join(row['name'].split()).casefold() == key), None)


def find_same(presets, settings):
    """The preset that already holds exactly these settings, or None."""
    return next((row for row in presets if row['settings'] == settings), None)


def save_preset(data_dir, name, settings, *, replace=False):
    """Keep `settings` under `name`. A name that exists is refused unless `replace` (the caller asked the owner first).
    Returns the record. Raises ValueError with the message to show."""
    name = clean_name(name)
    presets = read_presets(data_dir)
    same = find_by_name(presets, name)
    if same is not None and not replace:
        raise ValueError(f'Đã có cấu hình tên "{same["name"]}".')
    if same is None and len(presets) >= MAX_PRESETS:
        raise ValueError(f'Đã có {MAX_PRESETS} cấu hình; xóa bớt trước khi lưu thêm.')
    record = dict(id=same['id'] if same else uuid.uuid4().hex, name=name,
                  saved_at=datetime.now().astimezone().isoformat(timespec='seconds'), settings=dict(settings))
    try:
        _write(data_dir, [record if row is same else row for row in presets] if same else [*presets, record])
    except (OSError, TypeError, ValueError) as exc:
        raise ValueError('Chưa lưu được cấu hình: '+str(exc)) from exc
    return record


def rename_preset(data_dir, ident, name):
    name = clean_name(name)
    presets = read_presets(data_dir)
    row = next((row for row in presets if row['id'] == ident), None)
    if row is None:
        raise ValueError('Không còn cấu hình này.')
    other = find_by_name(presets, name)
    if other is not None and other is not row:
        raise ValueError(f'Đã có cấu hình tên "{other["name"]}".')
    row['name'] = name
    try:
        _write(data_dir, presets)
    except (OSError, TypeError, ValueError) as exc:
        raise ValueError('Chưa đổi được tên: '+str(exc)) from exc
    return row


def delete_preset(data_dir, ident):
    presets = read_presets(data_dir)
    kept = [row for row in presets if row['id'] != ident]
    if len(kept) == len(presets):
        raise ValueError('Không còn cấu hình này.')
    try:
        _write(data_dir, kept)
    except (OSError, TypeError, ValueError) as exc:
        raise ValueError('Chưa xóa được cấu hình: '+str(exc)) from exc
