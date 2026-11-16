"""A video whose new voice is not ready is never posted with the old one (owner 16/11/2026).

* `blocked_reason(row)`: why a Page schedule's video must not be posted now: it waits to be made again with the new voice (the link's
  `redo` mark), its last remake failed (`redo_failed`, an older failure that was not asked for again), or it was dropped because the
  remake failed 3 times in a row (`redo_dropped`). Videos nobody asked to be made again,
  text posts and anything whose campaign cannot be read are not blocked.
* `hold_or_swap(controller, row, reason)`: at the due time of such a video the SAME Page's nearest LATER appointment of a ready video
  takes this time (and its own caption), and this video takes that later time. Nothing later is ready: this schedule is paused (the time
  is kept) and nothing is posted.
* `apply_dropped(controller)`: a dropped video's Page schedule is removed (the files and the history stay) and the nearest later ready
  video of the Page takes its time, when that time is still ahead.
Nothing here sends anything to Facebook; only appointments of this machine's schedule move."""
from pathlib import Path
import time

from teodub.campaign import read_json
from teodub.page_schedule import display_time

PULL_UP_LEAD_SECONDS = 300  # A removed video's time is only taken over when it is more than this far ahead.


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


def _label(row):
    number = row['source'].get('number')
    return f'Video {number}' if number is not None else str(row['source'].get('label', 'video'))


def next_ready(controller, row, manifests):
    """The nearest LATER, armed, untouched appointment of this Page whose video is ready (has its voice), or None."""
    found = [other for other in controller.store.list(row['account_id'], row['page_key'])
             if other['id'] != row['id'] and other['id'] in controller.guards and other['state'] == 'armed'
             and other['due_at'] > row['due_at'] and not any(other[key] for key in ('attempted_at', 'request_id', 'finished_at', 'phase'))
             and other['source'].get('kind') != 'text' and other['source'].get('campaign') and not blocked_reason(other, manifests)]
    return min(found, key=lambda other: (other['due_at'], other['created_at'], other['id']), default=None)


def hold_or_swap(controller, row, reason):
    """Returns 'swapped' or 'held'."""
    manifests = {}
    partner = next_ready(controller, row, manifests)
    store = controller.store
    if partner is not None:
        store.retime(
            row['account_id'], row['page_key'],
            {row['id']: partner['due_at'], partner['id']: row['due_at']},
            {row['id']: row['due_at'], partner['id']: partner['due_at']},
            {row['id']: f'{reason}: đã đổi chỗ với {_label(partner)}, giờ đăng mới {display_time(partner["due_at"])} (UTC+7). '
                        'Giữ Tèo chạy và Chrome có phiên đăng nhập.',
             partner['id']: f'Lên giờ {display_time(row["due_at"])} thay {_label(row)} ({reason.lower()}). '
                            'Giữ Tèo chạy và Chrome có phiên đăng nhập.'})
        return 'swapped'
    store.update(row['id'], expected={'armed'}, state='paused',
                 message=f'Tạm dừng — {reason}; Tèo không đăng bản giọng cũ và chưa có video nào sẵn sàng phía sau để đổi chỗ. '
                         'Giờ đăng được giữ; khi có bản giọng mới, đổi giờ rồi bật lại.')
    controller.release(row['id'])
    return 'held'


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
