"""Owner 25/11/2026: while the account's plain Chrome ("Mở Shopee") holds its profile, Tèo cannot open its own Chrome for the account.
A post falling due then WAITS (stays armed, never fails, nothing is sent); once that Chrome is closed it gets the time now and is posted
at once (its old time stays in schedule_reorders), like a post waiting for its new voice (page_schedule_voice). Asking the owner and
closing that Chrome is the desktop's job (FacebookTab.shopee_waiting); this module only decides wait / go and keeps the waits.

A wait only STARTS within LATE_START_SECONDS of the post's time (Tèo's own 2-minute rule): a post already missed (Tèo was closed, say) is
never caught up through this wait."""
from datetime import datetime
import json
from pathlib import Path
import time

from teodub.common import atomic_json
from teodub.page_schedule import display_time
from teodub.shopee_browser import profile_busy

HOLDS_FILE = 'shopee-holds.json'  # {schedule id: dict(due_at=the time it waits past, since=when the wait began, since_at=epoch)}
LATE_START_SECONDS = 120
CONFIRM_SECONDS = 5  # The profile must stay held this long before a wait starts (Tèo's own Chrome may be just closing).


def holds_path(store):
    return Path(store.path).parent/HOLDS_FILE


def read_holds(store):
    """The schedules waiting for the account's Shopee Chrome to close. A missing or damaged file means none."""
    try:
        data = json.loads(holds_path(store).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {key: value for key, value in data.items() if isinstance(value, dict) and type(value.get('due_at')) in (int, float)}


def write_holds(store, holds):
    atomic_json(holds_path(store), holds)


def is_waiting(row, holds):
    entry = holds.get(row['id'])
    return bool(entry) and entry.get('due_at') == row['due_at']


def outside_chrome(owner, account_id):
    """True when a Chrome that is not Tèo's own session holds the account's profile. Anything unknown counts as no."""
    try:
        session = owner.sessions.get(account_id)
        if session is not None and session.pending:
            return False  # Tèo's own Chrome: the usual queue handles it.
        return profile_busy(owner.store.profile_directory(account_id))
    except Exception:
        return False


def wait_message(row, since_at, wall):
    waited = max(0, int(wall-since_at))//60
    return (f'Đang chờ Chrome Shopee của tài khoản đóng (đã chờ {waited} phút). Đóng xong là Tèo đăng ngay. '
            f'Giờ cũ {display_time(row["due_at"])} (UTC+7). Chưa tải/gửi video này.')


def wait_or_go(controller, row, wall):
    """For an armed row at (or past) its time. True: it waits (or was just given the time now and goes from the next look on).
    False: nothing to do with Shopee, the usual path goes on."""
    store = controller.store
    holds = read_holds(store)
    waiting = is_waiting(row, holds)
    seen = controller.__dict__.setdefault('shopee_seen', {})  # account id -> wall time the outside Chrome was first seen
    if outside_chrome(controller.owner, row['account_id']):
        if not waiting:
            if wall-row['due_at'] > LATE_START_SECONDS:
                return False  # Already missed: the usual "lỡ giờ" path, never a catch-up.
            first = seen.setdefault(row['account_id'], wall)
            if wall-first < CONFIRM_SECONDS:
                return True  # Tèo's own Chrome may still be letting go of the profile: looked at again in a second.
            holds[row['id']] = dict(due_at=row['due_at'], since=datetime.now().astimezone().isoformat(timespec='seconds'),
                                    since_at=wall)
            write_holds(store, holds)
            log = getattr(controller.owner, 'chrome_log', None)
            if log:
                log(row['account_id'], 'Tới giờ đăng nhưng Chrome Shopee của tài khoản đang mở: bài chờ, chưa gửi gì.')
        message = wait_message(row, holds[row['id']].get('since_at', wall), wall)
        if row['message'] != message:
            store.update(row['id'], expected={'armed'}, message=message)
        hook = getattr(controller.owner, 'shopee_waiting', None)
        if hook:
            hook(row['account_id'])
        return True
    seen.pop(row['account_id'], None)
    if not waiting:
        return False
    holds.pop(row['id'], None)
    write_holds(store, holds)  # Even when the move below fails: the row then goes the usual way (reported, never stuck).
    store.retime(row['account_id'], row['page_key'], {row['id']: time.time()}, {row['id']: row['due_at']},
                 {row['id']: f'Chrome Shopee đã đóng: Tèo đăng ngay (giờ cũ {display_time(row["due_at"])}, UTC+7).'})
    return True


def tidy_holds(store):
    """Forget the waits of schedules that are gone, sent, removed or given another time."""
    holds = read_holds(store)
    keep = {}
    for ident, entry in holds.items():
        try:
            row = store.get(ident)
        except (OSError, ValueError, KeyError):
            continue
        if row['due_at'] == entry['due_at'] and row['state'] == 'armed' and not row['attempted_at']:
            keep[ident] = entry
    if keep != holds:
        write_holds(store, keep)
