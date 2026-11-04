"""Loại trừ video on a link's tab (owner 04/11/2026): paste video links, and Tèo marks those videos of THIS link "Đã loại trừ" — they
are never dubbed or subtitled, do not hold the link back from completing, and "Lấy thêm video mới…" does not add them again.
Nothing is deleted: the Video number, job folder and any downloaded file stay. A video already finished is not made again; it is
marked `no_post` ("Không đăng"): Page posting never lists or schedules it, and its Page schedules not sent yet are cancelled
(`cancel_unsent`; one already sent is left alone). "Bỏ loại trừ" brings a video back as pending, or lifts "Không đăng". No AI call.

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
        entry = dict(id=ident, number=None, status=None, kind='skip', reason='', nopost=False, schedules=[])
        if item is None:
            entry['reason'] = 'Link này không có video đó.'
        else:
            entry.update(number=item['number'], status=item['status'])
            if restore:
                if item['status'] == EXCLUDED or item.get('no_post'):
                    entry.update(kind='mark', nopost=item['status'] in DONE)
                else:
                    entry['reason'] = 'Video này chưa bị loại trừ.'
            elif item['status'] == EXCLUDED:
                entry['reason'] = 'Đã loại trừ từ trước.'
            elif item['status'] in DONE:
                # A finished video is never dubbed again; "loại trừ" means Không đăng: it is never posted from now on.
                if item.get('no_post'):
                    entry['reason'] = 'Đã đặt Không đăng từ trước.'
                else:
                    entry.update(kind='mark', nopost=True)
            elif item['status'] == 'running' and working:
                entry['reason'] = 'Đang làm dở; Tèo làm xong video này (không loại trừ được).'
            else:
                entry['kind'] = 'mark'
            if entry['nopost'] and not restore:
                entry['schedules'] = schedules_of(directory.parent.parent, manifest['id'], item['number'])
        entries.append(entry)
    return dict(entries=entries, unreadable=unreadable, restore=restore,
                marked=sum(entry['kind'] == 'mark' for entry in entries))


UNSENT = {'scheduled', 'armed', 'paused', 'needs_attention'}  # With no attempt yet: the same rows "Bỏ lịch chưa gửi" accepts.


def schedules_of(data_dir, campaign_id, number):
    """Read only: the Page schedules of one campaign video, as dict(id, label, state, due_at, unsent). Cancelled and removed
    ones are not listed."""
    from teodub.page_schedule import PageScheduleStore
    store = PageScheduleStore(data_dir)
    key = f'{campaign_id}:{number}'
    return [dict(id=row['id'], label=row['source'].get('label', ''), state=row['state'], due_at=row['due_at'],
                 unsent=not row['attempted_at'] and row['state'] in UNSENT)
            for row in store.list() if row['source_key'] == key and row['state'] not in ('cancelled', 'removed')]


def cancel_unsent(controller, campaign_id, numbers):
    """04/11/2026 (owner, choice A): cancel the Page schedules of these videos that Tèo has never sent anywhere ("Bỏ lịch chưa
    gửi", history and video kept). One already sent, being sent or published is left alone and only reported.
    Returns dict(cancelled=[label], kept=[(label, state)], failed=[(label, reason)])."""
    keys = {f'{campaign_id}:{number}' for number in numbers}
    result = dict(cancelled=[], kept=[], failed=[])
    for row in controller.store.list():
        if row['source_key'] not in keys or row['state'] in ('cancelled', 'removed'):
            continue
        label = row['source'].get('label', row['id'])
        if row['attempted_at'] or row['state'] not in UNSENT:
            result['kept'].append((label, row['state']))
            continue
        try:
            controller.cancel(row['id'])
            result['cancelled'].append(label)
        except (OSError, ValueError, RuntimeError) as exc:
            result['failed'].append((label, str(exc)))
    return result


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
