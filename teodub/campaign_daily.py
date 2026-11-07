"""Số video mỗi ngày on a link's tab (owner 07/11/2026): a link made only N videos a day, then rests until 0h; empty = all of them.

The number lives in its own small file next to campaign.json (`daily-limit.json`), written only by the desktop and only read by the
worker, so changing it never competes with the worker for campaign.json and works while the link runs. The worker looks at it before
it STARTS each video: a video already running is never cut off. Only videos newly finished that day count (a finished video made again
and failed videos do not), and a video made again is never held back. The day is 0h-24h of this machine's clock."""
from datetime import datetime, timedelta
from pathlib import Path
import time

from teodub.campaign import read_json
from teodub.common import atomic_json

DAILY_FILE = 'daily-limit.json'
MAX_PER_DAY = 999
REST = 'Đã đủ video hôm nay'  # The worker's message while the link rests; the rotation treats it like a wait for a source.
BAD_NUMBER = 'Nhập một số từ 1 đến 999, hoặc để trống để làm hết video của link.'


def read_limit(directory):
    """The saved number of videos a day, or None (= no limit). A damaged file means no limit, never a stopped link."""
    try:
        value = read_json(Path(directory)/DAILY_FILE).get('per_day')
    except (OSError, ValueError, AttributeError):
        return None
    return value if type(value) is int and 1 <= value <= MAX_PER_DAY else None


def parse_limit(text):
    """What the owner typed -> a number, or None for empty. Raises ValueError with a message to show."""
    text = str(text if text is not None else '').strip()
    if not text:
        return None
    if not text.isascii() or not text.isdigit() or not 1 <= int(text) <= MAX_PER_DAY:
        raise ValueError(BAD_NUMBER)
    return int(text)


def write_limit(directory, text):
    """Save (or, for empty, remove) the number. Returns it."""
    value = parse_limit(text)
    path = Path(directory)/DAILY_FILE
    if value is None:
        path.unlink(missing_ok=True)
    else:
        atomic_json(path, dict(version=1, per_day=value))
    return value


def finished_day(item):
    """The day a video was FIRST finished, or None. A video made again keeps counting for the day it was first made (the worker
    stores `first_completed_at`); one made again before that was stored is not counted at all."""
    if item.get('status') != 'completed':
        return None
    stamp = item.get('first_completed_at') or (None if item.get('redone') else item.get('completed_at'))
    try:
        return datetime.fromisoformat(stamp).astimezone().date()
    except (TypeError, ValueError):
        return None


def done_today(manifest, now=None):
    today = datetime.fromtimestamp(time.time() if now is None else now).astimezone().date()
    return sum(finished_day(item) == today for item in manifest['items'])


def midnight_after(now=None):
    """The next 0h (this machine's clock) as a timestamp."""
    moment = datetime.fromtimestamp(time.time() if now is None else now).astimezone()
    return (moment.replace(hour=0, minute=0, second=0, microsecond=0)+timedelta(days=1)).timestamp()


def summary(directory, manifest, now=None):
    """None without a limit; else dict(limit, done, full, until) for the day now."""
    limit = read_limit(directory)
    if limit is None:
        return None
    now = time.time() if now is None else now
    done = done_today(manifest, now)
    return dict(limit=limit, done=done, full=done >= limit, until=midnight_after(now))


def rest_message(info):
    day = datetime.fromtimestamp(info['until']).strftime('%d/%m')
    return f'{REST}: {info["done"]}/{info["limit"]} video · nghỉ tới 0h ngày {day}.'


def rest_for(directory, manifest, now=None):
    """What the worker asks before it starts a new video: None = go on, else dict(message, until) = rest now."""
    info = summary(directory, manifest, now)
    if info is None or not info['full']:
        return None
    return dict(message=rest_message(info), until=info['until'])


def note(directory, manifest, now=None):
    """The line the link's tab shows under the box."""
    info = summary(directory, manifest, now)
    if info is None:
        return 'Để trống: link làm hết video của nó.'
    text = f'Hôm nay đã xong {info["done"]}/{info["limit"]} video.'
    if info['full']:
        day = datetime.fromtimestamp(info['until']).strftime('%d/%m')
        text += f' Đã đủ, nghỉ tới 0h ngày {day}.'
    return text
