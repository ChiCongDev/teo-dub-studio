"""Readable download names that also work with Windows media applications."""
from pathlib import Path
import re
import unicodedata


MAX_PATH_UNITS = 240
MAX_TITLE_UNITS = 80
_INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')
_DEVICE = re.compile(r'^(?:CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\.|$)', re.I)


def _units(text):
    return len(text.encode('utf-16-le')) // 2


def _truncate(text, limit):
    # Count supplementary characters as two units without splitting a pair.
    kept, used = [], 0
    for char in text:
        size = 2 if ord(char) > 0xffff else 1
        if used + size > limit:
            break
        kept.append(char)
        used += size
    return ''.join(kept).rstrip(' .')


def safe_filename(name):
    """Whether a recorded basename can identify a regular Windows file."""
    return bool(name and name not in ('.', '..') and
                not _INVALID.search(name) and name == name.rstrip(' .') and
                not _DEVICE.match(name) and
                not any(unicodedata.category(c) == 'Cs' for c in name))


def download_path(folder, title, download_date):
    """Choose a name only; callers publish exclusively after validating media."""
    folder = Path(folder).resolve()
    title = unicodedata.normalize('NFC', str(title or ''))
    title = ''.join(' ' if unicodedata.category(c) in ('Cs', 'Cf') else c for c in title)
    title = ' '.join(_INVALID.sub(' ', title).split()).strip(' .') or 'Video'
    if _DEVICE.match(title):
        title = 'Video ' + title
    suffix = '_' + download_date.strftime('%d-%m-%Y') + '.mp4'
    available = min(MAX_TITLE_UNITS, MAX_PATH_UNITS - _units(str(folder)) - 1 - _units(suffix))
    if available < len('Video'):
        raise ValueError('Đường dẫn thư mục tải quá dài. Chọn thư mục ngắn hơn để lưu tên video kèm ngày tải.')
    title = _truncate(title, available) or 'Video'
    # Truncation can turn a formerly harmless title into a device basename.
    if _DEVICE.match(title):
        title = _truncate('Video ' + title, available)
    return folder / (title + suffix)
