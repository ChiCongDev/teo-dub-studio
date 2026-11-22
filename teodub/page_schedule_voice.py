"""A video whose new voice is not ready is never posted with the old one (owner 16/11/2026; 22/11/2026: it waits, then posts).

* `blocked_reason(row)`: why a Page schedule's video must not be posted now: it waits to be made again with the new voice (the link's
  `redo` mark), its last remake failed (`redo_failed`, an older failure that was not asked for again), or it was dropped because the
  remake failed 3 times in a row (`redo_dropped`). Videos nobody asked to be made again,
  text posts and anything whose campaign cannot be read are not blocked.
* Owner 22/11/2026: every schedule keeps ITS video. A video made again before its time is posted at that time (the schedule follows
  the new file, `page_schedule_redo`). At the due time of a video still being made again the schedule stays armed and WAITS
  (`wait_for_voice`; the wait is kept in page-publishing/voice-holds.json, so it survives closing Tèo); as soon as the new voice is
  ready the schedule is given the time "now" and posted (`end_wait`). (Until then a later ready video took its time: no more.)
  A video whose last remake failed and was not asked for again is paused: nothing is posted, as before.
* `apply_dropped(controller)`: a dropped video's Page schedule is removed (the files and the history stay) and the nearest later ready
  video of the Page takes its time, when that time is still ahead.
Nothing here sends anything to Facebook; only appointments of this machine's schedule move."""
from datetime import datetime
from pathlib import Path
import time

from teodub.campaign import is_running, read_json
from teodub.common import atomic_json
from teodub.page_schedule import display_time

PULL_UP_LEAD_SECONDS = 300  # A removed video's time is only taken over when it is more than this far ahead.
HOLDS_FILE = 'voice-holds.json'  # {schedule id: dict(due_at=the time it waits past, since=when the wait began)}
LINK_CHECK_SECONDS = 30  # How often a waiting schedule looks again whether its link runs (only for its message).


def _item(row, manifests):
    source = row['source']
    if source.get('kind') == 'text' or not source.get('campaign'):
        return None
    directory = Path(source['campaign'])
    if directory not in manifests:
        try:
            manifests[directory] = read_json(directory/'campaign.json')
        except (OSError, ValueError):
            manifests[directory] = None
    return next((x for x in (manifests[directory] or {}).get('items', []) if x.get('number') == source.get('number')), None)


def blocked_reason(row, manifests=None):
    """'' when the video may be posted; else the reason (short, for the schedule's message)."""
    item = _item(row, {} if manifests is None else manifests)
    if not item:
        return ''
    if item.get('redo_dropped'):
        return 'Video đã bị bỏ vì làm lại giọng mới lỗi 3 lần'
    if item.get('redo'):
        return 'Video chưa có bản giọng mới'
    if item.get('redo_failed'):  # The last remake failed and nobody asked again: it still has the old voice.
        return 'Video chưa có bản giọng mới (lần làm lại gần nhất bị lỗi)'
    return ''


def waiting_for_remake(row, manifests=None):
    """True while the video is (or waits to be) made again: its new voice will come, so its schedule waits for it."""
    item = _item(row, {} if manifests is None else manifests)
    return bool(item and item.get('redo') and not item.get('redo_dropped'))


def holds_path(store):
    return Path(store.path).parent/HOLDS_FILE


def read_holds(store):
    """The schedules waiting for their new voice past their time. A missing or damaged file means none."""
    try:
        data = read_json(holds_path(store))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {key: value for key, value in data.items() if isinstance(value, dict) and type(value.get('due_at')) in (int, float)}


def write_holds(store, holds):
    atomic_json(holds_path(store), holds)


def is_waiting(row, holds):
    """`row` waits for its new voice past its time (and still has the time it had when the wait began)."""
    entry = holds.get(row['id'])
    return bool(entry) and entry.get('due_at') == row['due_at']


def _label(row):
    number = row['source'].get('number')
    return f'Video {number}' if number is not None else str(row['source'].get('label', 'video'))


def wait_message(row, link_name, running):
    link = (f'link "{link_name}" đang làm' if running
            else f'link "{link_name}" đang dừng: bấm Chạy ở tab link để làm lại video này')
    return (f'Đang chờ bản giọng mới của {_label(row)} ({link}); xong là Tèo đăng ngay. '
            f'Giờ cũ {display_time(row["due_at"])} (UTC+7). Tèo không đăng bản giọng cũ.')


def wait_for_voice(controller, row, clock):
    """At (or past) its time, a video still being made again: the schedule stays armed and waits. Returns 'waiting'."""
    store = controller.store
    holds = read_holds(store)
    if not is_waiting(row, holds):
        holds[row['id']] = dict(due_at=row['due_at'], since=datetime.now().astimezone().isoformat(timespec='seconds'))
        write_holds(store, holds)
        controller.voice_checked.pop(row['id'], None)
    if clock-controller.voice_checked.get(row['id'], float('-inf')) < LINK_CHECK_SECONDS:
        return 'waiting'
    controller.voice_checked[row['id']] = clock
    directory = Path(row['source']['campaign'])
    try:
        name = read_json(directory/'campaign.json').get('name') or directory.name
    except (OSError, ValueError):
        name = directory.name
    message = wait_message(row, name, is_running(directory))
    if row['message'] != message:
        store.update(row['id'], expected={'armed'}, message=message)
    return 'waiting'


def hold_or_pause(controller, row, reason, clock):
    """A due video without its new voice. Being made again: it waits ('waiting'). Its last remake failed and nobody asked again:
    nothing will come by itself, so the schedule is paused ('paused'). Never posted with the old voice."""
    if waiting_for_remake(row):
        return wait_for_voice(controller, row, clock)
    controller.store.update(row['id'], expected={'armed'}, state='paused',
                            message=f'Tạm dừng — {reason}; Tèo không đăng bản giọng cũ. Giờ đăng được giữ; khi có bản giọng mới, '
                                    'đổi giờ rồi bật lại.')
    controller.release(row['id'])
    return 'paused'


def end_wait(controller, row):
    """The new voice of a waiting video is ready: its schedule is given the time now (the old time stays in schedule_reorders), so it
    is posted at once. True when it was moved (posted from the next look on); False when it was not waiting."""
    store = controller.store
    holds = read_holds(store)
    if not is_waiting(row, holds):
        return False
    holds.pop(row['id'], None)
    write_holds(store, holds)  # Even when the move below fails: the row then goes the usual way (it is reported, never stuck).
    controller.voice_checked.pop(row['id'], None)
    store.retime(row['account_id'], row['page_key'], {row['id']: time.time()}, {row['id']: row['due_at']},
                 {row['id']: f'Bản giọng mới của {_label(row)} đã xong: Tèo đăng ngay (giờ cũ {display_time(row["due_at"])}, UTC+7).'})
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
        if row['due_at'] == entry['due_at'] and row['state'] in {'armed', 'paused'} and not row['attempted_at']:
            keep[ident] = entry
    if keep != holds:
        write_holds(store, keep)


def next_ready(controller, row, manifests):
    """The nearest LATER, armed, untouched appointment of this Page whose video is ready (has its voice), or None."""
    found = [other for other in controller.store.list(row['account_id'], row['page_key'])
             if other['id'] != row['id'] and other['id'] in controller.guards and other['state'] == 'armed'
             and other['due_at'] > row['due_at'] and not any(other[key] for key in ('attempted_at', 'request_id', 'finished_at', 'phase'))
             and other['source'].get('kind') != 'text' and other['source'].get('campaign') and not blocked_reason(other, manifests)]
    return min(found, key=lambda other: (other['due_at'], other['created_at'], other['id']), default=None)


def apply_dropped(controller):
    """Remove the Page schedules of videos given up after 3 failed remakes; the next ready video takes the time. Returns the removed ids."""
    store = controller.store
    manifests, removed = {}, []
    for row in store.list():
        if (row['state'] not in store.REPLACEABLE or row['attempted_at'] or row['request_id'] or row['finished_at']):
            continue
        item = _item(row, manifests)
        if not item or not item.get('redo_dropped') or item.get('redo'):
            continue
        previews = store.removal_preview([row['id']], row['account_id'], row['page_key'])
        result = controller.remove_many(previews, row['account_id'], row['page_key'])
        if row['id'] not in result['removed']:
            continue
        removed.append(row['id'])
        if row['due_at'] > time.time()+PULL_UP_LEAD_SECONDS:
            partner = next_ready(controller, row, manifests)
            if partner is not None:
                try:
                    store.retime(row['account_id'], row['page_key'], {partner['id']: row['due_at']}, {partner['id']: partner['due_at']},
                                 {partner['id']: f'Lên giờ {display_time(row["due_at"])} thay {_label(row)} (bị bỏ vì làm lại giọng mới lỗi 3 lần). '
                                                 'Giữ Tèo chạy và Chrome có phiên đăng nhập.'})
                except ValueError:
                    pass  # It changed meanwhile: it keeps its own time.
    return removed
