"""Xếp theo lượt thích on a saved link (owner 03/11/2026): after the channel was read again, give each video the like count
Douyin reports and set the campaign's picking order to `order: likes` (most liked first).

Nothing is renumbered, moved or deleted: Video numbers, job folders, outputs and Page schedules stay tied to their number; only
the order `campaign.run_order` picks the next video in changes. Finished videos are untouched. A video whose count was not read
keeps its old position after the counted ones. Refuses while the campaign's worker holds its lock. No AI call."""
from copy import deepcopy
from datetime import datetime
from pathlib import Path

from teodub.campaign import DONE, EXCLUDED, likes_of, read_json, run_order
from teodub.campaign_append import _normal
from teodub.common import atomic_json
from teodub.pipeline import _job_lock

PREVIEW_ROWS = 40


def _counts(records):
    counts = {}
    for row in records:
        url, likes = _normal(row.get('url', '')), likes_of(row)
        if url and likes is not None:
            counts[url] = likes
    return counts


def _merge(items, counts):
    matched = 0
    for item in items:
        url = _normal(item.get('url', ''))
        if url in counts:
            item['likes'] = counts[url]
            matched += 1
    return matched


def preview(directory, records):
    """Read only: how many videos get a count, and the next videos in the new order (not finished ones)."""
    manifest = deepcopy(read_json(Path(directory)/'campaign.json'))
    counts = _counts(records)
    matched = _merge(manifest['items'], counts)
    manifest['order'] = 'likes'
    pending = [item for item in run_order(manifest) if item['status'] not in DONE and item['status'] != EXCLUDED]
    old = [item['number'] for item in manifest['items'] if item['status'] not in DONE and item['status'] != EXCLUDED]
    return dict(matched=matched, total=len(manifest['items']), pending=len(pending),
                unknown=sum(likes_of(item) is None for item in pending),
                changed=[item['number'] for item in pending] != old,
                first=[dict(number=item['number'], likes=likes_of(item), title=item.get('title', '')) for item in pending[:PREVIEW_ROWS]])


def apply_likes(directory, records):
    """Save the counts and the order. Returns dict(matched, total)."""
    from teodub.campaign_batch import _identity
    directory = Path(directory).resolve()
    counts = _counts(records)
    if not counts:
        raise ValueError('Lần đọc kênh này không có số lượt thích; chưa đổi thứ tự.')
    try:
        lock = _job_lock(directory)
        lock.__enter__()
    except (OSError, ValueError) as exc:  # Only taking the lock: the campaign's worker holds it while it works.
        raise ValueError('Link này đang làm video; đợi xong hoặc bấm Dừng link này rồi xếp theo lượt thích.') from exc
    try:
        manifest = read_json(directory/'campaign.json')
        _identity(manifest, directory)  # Archived or damaged data is refused before anything is written.
        matched = _merge(manifest['items'], counts)
        if not matched:
            raise ValueError('Không video nào của link này có trong lần đọc kênh; chưa đổi thứ tự.')
        manifest['order'] = 'likes'
        manifest['likes_read'] = dict(at=datetime.now().astimezone().isoformat(timespec='seconds'),
                                      read=len(records), matched=matched, of=len(manifest['items']))
        atomic_json(directory/'campaign.json', manifest)
        return dict(matched=matched, total=len(manifest['items']))
    finally:
        lock.__exit__(None, None, None)
