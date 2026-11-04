"""03/11/2026 (owner): "Xếp theo lượt thích" on a Page's schedule — the most liked videos take the earliest posting times.

The Page keeps exactly the posting times it already has; only WHICH video goes at which time changes. Each appointment keeps its
video, caption and draft, and only its time moves (`PageScheduleStore.reorder_times`, one transaction). Only armed appointments
Tèo never touched and that are not about to run are moved; text posts, sent, stopped and soon-due ones keep their place. A video
without a like count goes after the counted ones; ties keep the old order. Nothing here posts and no AI is called."""
from collections import Counter
from pathlib import Path
import time

from teodub.campaign import likes_first, likes_of, read_json

SAFE_SECONDS = 900  # An appointment this close to its time is left alone so nothing changes under a preparation.


def campaign_likes(data_dir, source, cache):
    """The like count saved for the campaign video of a schedule's source, or None."""
    directory = Path(str(source.get('campaign', ''))).resolve()
    if directory.parent != (Path(data_dir)/'campaigns').resolve():
        return None
    if directory not in cache:
        try:
            cache[directory] = {item['number']: likes_of(item) for item in read_json(directory/'campaign.json')['items']}
        except (OSError, ValueError, KeyError, TypeError):
            cache[directory] = {}
    return cache[directory].get(source.get('number'))


def build_plan(controller, account_id, page_key, *, clock=None):
    clock = time.time() if clock is None else clock
    skipped, pool, cache = Counter(), [], {}
    for row in controller.store.list(account_id, page_key):
        if row['state'] != 'armed':
            continue
        source = row['source']
        if source.get('kind') == 'text' or not source.get('path'):
            skipped['text'] += 1
        elif (row['attempted_at'] or row['request_id'] or row['finished_at'] or row['id'] not in controller.active):
            skipped['busy'] += 1
        elif row['due_at'] <= clock+SAFE_SECONDS:
            skipped['soon'] += 1
        else:
            row['likes'] = campaign_likes(controller.store.data_dir, source, cache)
            pool.append(row)
    pool.sort(key=lambda row: (row['due_at'], row['created_at'], row['id']))
    slots = [row['due_at'] for row in pool]
    entries = [dict(id=row['id'], label=row['source'].get('label', row['id']), likes=likes_of(row), due_at=slot)
               for slot, row in zip(slots, likes_first(pool))]
    old = {row['id']: row for row in pool}
    for entry in entries:
        entry['old_due_at'] = old[entry['id']]['due_at']
    by_slot = {row['due_at']: row for row in pool}  # What each time holds now, to show "now → new".
    for entry in entries:
        holder = by_slot.get(entry['due_at'])
        entry['was'] = None if holder is None else dict(label=holder['source'].get('label', holder['id']), likes=likes_of(holder))
    return dict(entries=entries, clock=clock, skipped=dict(skipped),
                changed=sum(entry['due_at'] != entry['old_due_at'] for entry in entries),
                known=sum(entry['likes'] is not None for entry in entries))


def signature(plan):
    return [(entry['id'], entry['old_due_at'], entry['due_at']) for entry in plan['entries']]


def apply_plan(controller, account_id, page_key, previewed):
    """Rebuild the plan now. If it differs from what the owner saw, change nothing; otherwise move the times. Returns how many moved."""
    plan = build_plan(controller, account_id, page_key)
    if signature(plan) != signature(previewed):
        raise ValueError('Lịch của Page đã thay đổi từ lúc xem trước; bấm Xem lại rồi lưu lại. Chưa đổi lịch nào.')
    moves = [entry for entry in plan['entries'] if entry['due_at'] != entry['old_due_at']]
    if not moves:
        return 0
    controller.store.reorder_times(account_id, page_key, {entry['id']: entry['due_at'] for entry in moves},
                                   {entry['id']: entry['old_due_at'] for entry in moves})
    return len(moves)
