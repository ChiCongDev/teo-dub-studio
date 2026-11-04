"""Loại trừ video on a link's tab (owner 04/11/2026): paste video links, and Tèo marks those videos of THIS link "Đã loại trừ" — they
are never dubbed or subtitled, do not hold the link back from completing, and "Lấy thêm video mới…" does not add them again.
Nothing is deleted: the Video number, job folder and any downloaded file stay. A finished video is never touched. "Bỏ loại trừ"
brings a video back as pending. No AI call.

While the link's worker runs it is the only writer of campaign.json, so only a request (`exclude-*.json` in the campaign folder)
is left; the worker marks the videos between two videos (`campaign.absorb_exclusions`): a video already being made is made to the
end. Otherwise the videos are marked here, under the campaign's lock."""
from pathlib import Path
import re
import uuid

from teodub.campaign import DONE, EXCLUDE_REQUESTS, EXCLUDED, is_running, mark_excluded, read_json
from teodub.common import atomic_json
from teodub.pipeline import _job_lock

LINK_ID = re.compile(r'(?:/video/|modal_id=|aweme_id=)(\d{10,})|(?<!\d)(\d{15,})(?!\d)')


def parse_links(text):
    """(ids, unreadable): the Douyin video ids of every link or bare id in `text` (any whitespace between), in order and without
    repeats, and the pieces that hold none (a short v.douyin.com link cannot be opened here)."""
    ids, unreadable = [], []
    for piece in re.split(r'\s+', str(text).strip()):
        if not piece:
            continue
        found = LINK_ID.search(piece)
        value = (found[1] or found[2]) if found else None
        if value is None:
            unreadable.append(piece[:80])
        elif value not in ids:
            ids.append(value)
    return ids, unreadable


def _id_of(item):
    found = re.search(r'/video/(\d+)', str(item.get('url', '')))
    return found[1] if found else None


def build_plan(directory, text, *, restore=False):
    """Read only. Entries: id, number, status, kind ('mark' or 'skip') and the reason a video is left alone."""
    directory = Path(directory).resolve()
    manifest = read_json(directory/'campaign.json')
    ids, unreadable = parse_links(text)
    by_id = {value: item for item in manifest['items'] if (value := _id_of(item))}
    working = is_running(directory)
    entries = []
    for ident in ids:
        item = by_id.get(ident)
        entry = dict(id=ident, number=None, status=None, kind='skip', reason='')
        if item is None:
            entry['reason'] = 'Link này không có video đó.'
        else:
            entry.update(number=item['number'], status=item['status'])
            if restore:
                if item['status'] == EXCLUDED:
                    entry['kind'] = 'mark'
                else:
                    entry['reason'] = 'Video này chưa bị loại trừ.'
            elif item['status'] == EXCLUDED:
                entry['reason'] = 'Đã loại trừ từ trước.'
            elif item['status'] in DONE:
                entry['reason'] = 'Đã xong; giữ nguyên.'
            elif item['status'] == 'running' and working:
                entry['reason'] = 'Đang làm dở; Tèo làm xong video này (không loại trừ được).'
            else:
                entry['kind'] = 'mark'
        entries.append(entry)
    return dict(entries=entries, unreadable=unreadable, restore=restore,
                marked=sum(entry['kind'] == 'mark' for entry in entries))


def apply(directory, numbers, *, restore=False):
    """Mark (or restore) these video numbers. Returns dict(mode, changed): mode 'now' = saved here, 'request' = left for the
    worker of a running link to apply before its next video."""
    directory = Path(directory).resolve()
    numbers = sorted({number for number in numbers if type(number) is int})
    if not numbers:
        raise ValueError('Không có video nào để đổi.')
    action = 'restore' if restore else 'exclude'
    try:
        lock = _job_lock(directory)
        lock.__enter__()
    except (OSError, ValueError):  # Only taking the lock: the link's worker holds it while it works.
        atomic_json(directory/f'exclude-{uuid.uuid4().hex}.json', dict(action=action, numbers=numbers))
        return dict(mode='request', changed=[])
    try:
        from teodub.campaign_batch import _identity
        manifest = read_json(directory/'campaign.json')
        _identity(manifest, directory)  # Archived or damaged data is refused before anything is written.
        changed = mark_excluded(manifest, numbers, restore=restore)
        if changed:
            atomic_json(directory/'campaign.json', manifest)
        return dict(mode='now', changed=changed)
    finally:
        lock.__exit__(None, None, None)


def pending_requests(directory):
    """How many requests the worker has not taken yet (read only)."""
    return len(list(Path(directory).glob(EXCLUDE_REQUESTS)))
