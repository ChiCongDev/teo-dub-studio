"""Make FINISHED videos of a link again with a newly chosen voice (owner 05/11/2026; choices 1A, 2A, 3A).

A finished video is never touched silently. Here the owner asks for it, and Tèo makes it again in a new revision job: the
downloaded video and the Vietnamese lines are reused (no download, no AI call); only the voice and the render are made again.
* A video already posted or sent to a Page (a Page schedule with any attempt) is left alone: Facebook's copy cannot change.
* A video whose Page schedule has not been sent keeps that schedule: the same time and caption move to the new file when it is
  ready (`page_schedule_redo`). Owner 22/11/2026: also when it is due soon (no more 30-minute rule): at its time it waits for the new
  voice and is posted as soon as it is ready (`page_schedule_voice`).
* The old output is kept as it is; the new one is written beside it ("..._lan-2"). Until the new one is ready the video stays
  "Hoàn tất" with the old file, and if the new one fails the old one simply stays.
While the link's worker runs it is the only writer of campaign.json, so only a request (`redo-*.json`) is left, applied between
two videos (`campaign.absorb_redo`). Otherwise the videos are marked here under the campaign's lock."""
from pathlib import Path
import time
import uuid

from teodub.campaign import REDO_REQUESTS, mark_redo, read_json
from teodub.common import atomic_json
from teodub.pipeline import _job_lock

UNSENT = {'scheduled', 'armed', 'paused', 'needs_attention'}  # With no attempt yet: the rows that can still follow a new file.
FOLLOW_FILE = 'redo-follow.json'  # 22/11/2026: the link's saved settings that finished videos must follow ("làm lại" ticked).


def schedule_rows(data_dir, campaign_id):
    """Read only: {video number: its Page schedule rows} for one campaign, without cancelled and removed ones."""
    from teodub.page_schedule import PageScheduleStore
    prefix, rows = campaign_id+':', {}
    for row in PageScheduleStore(data_dir).list():
        number = row['source_key'][len(prefix):] if row['source_key'].startswith(prefix) else ''
        if number.isdigit() and row['state'] not in ('cancelled', 'removed'):
            rows.setdefault(int(number), []).append(row)
    return rows


def next_due(data_dir, campaign_id, now=None):
    """Read only (no store is built, nothing initialised): {video number: the soonest FUTURE due time of its saved or armed
    Page schedule}. Used to make first the finished videos that will be posted first. 22/11/2026: an ARMED schedule already due
    counts too (it waits for its new voice), with its time, so its video comes first of all."""
    import re
    from teodub.campaign_posting import _read_schedules
    path = Path(data_dir)/'page-publishing'/'schedules.sqlite3'
    now = time.time() if now is None else now
    try:
        rows = _read_schedules(path, campaign_id)
    except Exception:  # No schedule file, a busy database...: the plain order is used.
        return {}
    result = {}
    for row in rows:
        match = re.fullmatch(re.escape(campaign_id)+r':([1-9][0-9]*)', str(row.get('source_key', '')))
        due = row.get('due_at')
        if (match and type(due) in (int, float)
                and (row.get('state') == 'armed' or row.get('state') == 'scheduled' and due >= now)):
            number = int(match.group(1))
            result[number] = min(due, result.get(number, due))
    return result


def redo_priority(directory, manifest, now=None):
    """Owner 06/11/2026: of the finished videos waiting to be made again, those whose Page schedule is soonest come first, in the
    order of their due time; the others keep the link's usual order after them. Called before every video, so a time changed
    while the link runs changes the order from the next video on."""
    waiting = [item for item in manifest['items'] if item['status'] == 'completed' and item.get('redo')]
    if not waiting:
        return []
    due = next_due(Path(directory).resolve().parent.parent, manifest['id'], now)
    return sorted((item for item in waiting if item['number'] in due), key=lambda item: (due[item['number']], item['number']))


def overdue_redo(directory, manifest, item, now=None):
    """Owner 22/11/2026: a finished video waiting to be made again whose Page post is already due (its schedule waits for it). It runs
    even when the link's "Số video mỗi ngày" is reached."""
    if item.get('status') != 'completed' or not item.get('redo'):
        return False
    now = time.time() if now is None else now
    due = next_due(Path(directory).resolve().parent.parent, manifest['id'], now)
    return item['number'] in due and due[item['number']] <= now


def remember_follow(directory, settings_id):
    """Owner 22/11/2026: "làm lại" was ticked when `settings_id` was saved (None: not ticked). A video still being made then finishes
    with the older settings; `follow_after_finish` makes it again. Never raises (only this convenience would be lost)."""
    try:
        path = Path(directory)/FOLLOW_FILE
        if settings_id:
            atomic_json(path, dict(version=1, settings_id=str(settings_id)))
        else:
            path.unlink(missing_ok=True)
    except OSError:
        pass


def follow_settings(directory):
    try:
        value = read_json(Path(directory)/FOLLOW_FILE).get('settings_id')
    except (OSError, ValueError, AttributeError):
        return None
    return value if isinstance(value, str) and value else None


def follow_after_finish(directory, manifest, item, job):
    """In the worker, right after a video finished: when the link's newest saved settings were saved with "làm lại" ticked and this
    video was made with older ones (it was being made then), it is marked to be made again. Returns True when marked; never raises."""
    try:
        wanted = follow_settings(directory)
        if not wanted:
            return False
        from teodub.campaign_settings import next_settings
        current = next_settings(directory)
        if not current or current['id'] != wanted:
            return False
        if read_json(Path(job)/'config.json').get('campaign_settings_version') == wanted:
            return False
        return bool(mark_redo(manifest, [item['number']]))
    except Exception:  # Only this extra remake is missed; the finished video stays as it is.
        return False


def build_plan(directory, *, settings_id=None, now=None, unchanged=None, blocked=None,
               same_reason='Đã có đúng Sub, giọng và âm lượng này.'):
    """Read only. `entries`: number, kind ('redo' or 'skip'), reason, scheduled (an unsent Page schedule will follow the new file).
    `settings_id`: the saved settings the new versions would use; a video already made with them is skipped. `unchanged`: a
    function of a video's job config; a video it accepts would come out the same, so it is skipped. `blocked`: a function of a
    video's job config that returns the reason (text) a video cannot be made again, or ''."""
    directory = Path(directory).resolve()
    manifest = read_json(directory/'campaign.json')
    now = time.time() if now is None else now
    rows = schedule_rows(directory.parent.parent, manifest['id'])
    entries = []
    for item in manifest['items']:
        if item['status'] != 'completed':
            continue
        entry = dict(number=item['number'], kind='skip', reason='', scheduled=False)
        found = rows.get(item['number'], [])
        if item.get('no_post'):
            entry['reason'] = 'Đã đặt Không đăng.'
        elif item.get('redo_dropped'):
            entry['reason'] = 'Đã bỏ vì làm lại lỗi 3 lần liên tiếp.'
        elif item.get('redo'):
            entry['reason'] = 'Đã xếp hàng làm lại.'
        elif not item.get('outputs'):
            entry['reason'] = 'Chưa có thành phẩm.'
        elif any(row['attempted_at'] or row['request_id'] or row['finished_at'] or row['state'] not in UNSENT for row in found):
            entry['reason'] = 'Đã đăng hoặc đã gửi lên Page; bản trên Facebook không đổi được.'
        elif settings_id and _uses(directory, item, settings_id):
            entry['reason'] = 'Đã làm bằng giọng này.'
        elif blocked is not None and _answer(directory, item, blocked):
            entry['reason'] = _answer(directory, item, blocked)
        elif unchanged is not None and _accepts(directory, item, unchanged):
            entry['reason'] = same_reason
        else:
            entry.update(kind='redo', scheduled=bool(found))
        entries.append(entry)
    return dict(entries=entries, numbers=[e['number'] for e in entries if e['kind'] == 'redo'],
                scheduled=sum(e['scheduled'] for e in entries if e['kind'] == 'redo'))


def _uses(directory, item, settings_id):
    from teodub.campaign_settings import effective_job
    try:
        return read_json(effective_job(directory, item)/'config.json').get('campaign_settings_version') == settings_id
    except (OSError, ValueError, KeyError):
        return False


def _accepts(directory, item, unchanged):
    from teodub.campaign_settings import effective_job
    try:
        return bool(unchanged(read_json(effective_job(directory, item)/'config.json')))
    except (OSError, ValueError, KeyError, TypeError):
        return False


def job_config_matches(directory, number, check):
    """Read only: `check(config)` for the job config of video `number` (False when it cannot be read)."""
    try:
        item = next(x for x in read_json(Path(directory)/'campaign.json')['items'] if x['number'] == number)
    except (OSError, ValueError, KeyError, StopIteration):
        return False
    return _accepts(Path(directory).resolve(), item, check)


def _answer(directory, item, blocked):
    from teodub.campaign_settings import effective_job
    try:
        return str(blocked(read_json(effective_job(directory, item)/'config.json')) or '')
    except (OSError, ValueError, KeyError, TypeError):
        return ''


def apply(directory, numbers):
    """Ask for these finished videos to be made again. Returns dict(mode, changed): mode 'now' = marked here, 'request' = left for
    the worker of a running link to take before its next video."""
    directory = Path(directory).resolve()
    numbers = sorted({number for number in numbers if type(number) is int})
    if not numbers:
        raise ValueError('Không có video nào để làm lại.')
    try:
        lock = _job_lock(directory)
        lock.__enter__()
    except (OSError, ValueError):  # Only taking the lock: the link's worker holds it while it works.
        atomic_json(directory/f'redo-{uuid.uuid4().hex}.json', dict(numbers=numbers))
        return dict(mode='request', changed=[])
    try:
        from teodub.campaign_batch import _identity
        manifest = read_json(directory/'campaign.json')
        _identity(manifest, directory)  # Archived or damaged data is refused before anything is written.
        changed = mark_redo(manifest, numbers)
        if changed:
            atomic_json(directory/'campaign.json', manifest)
        return dict(mode='now', changed=changed)
    finally:
        lock.__exit__(None, None, None)


def pending_requests(directory):
    """How many requests the worker has not taken yet (read only)."""
    return len(list(Path(directory).glob(REDO_REQUESTS)))
