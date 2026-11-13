"""Chạy video đã chọn (owner 13/11/2026): pick any videos in a link's table and run them FIRST, with the link's current config.

The desktop side. `plan` reads (and changes nothing) what each chosen video would do; `apply` marks them, under the link's lock when it
is free or as a request (`first-*.json`) the running worker takes before its next video. The worker side is `campaign.mark_selected`.

* pending / stopped / interrupted: just first in line.
* failed: one more try.   * excluded: brought back, then first in line.
* finished: made again with the link's current config in a new revision job (the old output stays until the new one is ready). Same rules as
  "làm lại" (campaign_redo.build_plan): a finished video already sent or posted to a Page is NOT made again (the posting column binds a
  Page schedule to the video's current output, so a new file would make a posted video read "Cần kiểm tra"), nor one due within 30 minutes,
  one marked Không đăng, or one still waiting for approval. Each skipped video gets its reason."""
from pathlib import Path
import uuid

from teodub.campaign import DONE, EXCLUDED, PRIORITY_REQUESTS, mark_selected, read_json
from teodub.campaign_redo import build_plan
from teodub.campaign_settings import made_with_everything, next_settings, same_language_models
from teodub.common import atomic_json
from teodub.pipeline import _job_lock

KIND_TEXT = {
    'run': 'chưa làm xong: làm trước',
    'retry': 'đang lỗi: thử lại một lần, làm trước',
    'restore': 'đang loại trừ: bỏ loại trừ rồi làm trước',
    'redo': 'đã xong: làm lại theo cấu hình hiện tại (giữ bản cũ)',
    'queued': 'đã xếp làm lại: đưa lên trước',
}
NOT_NOW = 'Chưa đọc được lịch đăng hoặc cấu hình của link; thử lại sau ít phút.'
NO_NEW_CONFIG = ('Link chưa có cấu hình mới nào (Chỉnh sửa cấu hình… / Giọng & âm lượng…) nên làm lại cũng ra y hệt. '
                 'Đổi cấu hình của link trước, rồi chọn lại video.')
SAME_CONFIG = 'Đã làm bằng đúng cấu hình hiện tại của link; làm lại cũng ra y hệt.'
APPROVAL = 'Đang chờ bạn duyệt (Xong · cần duyệt); xử lý việc duyệt trước.'


def plan(directory, numbers, *, now=None):
    """Read only. `entries` (in the order given): number, kind ('run', 'retry', 'restore', 'redo', 'queued' or 'skip'), reason,
    scheduled (a finished video whose unsent Page schedule will follow the new file). `numbers`: the ones that will be done."""
    directory = Path(directory).resolve()
    manifest = read_json(directory/'campaign.json')
    by_number = {item['number']: item for item in manifest['items']}
    wanted = [number for number in dict.fromkeys(numbers) if number in by_number]
    finished, no_config = {}, False
    if any(by_number[number]['status'] == 'completed' and not by_number[number].get('redo') for number in wanted):
        try:
            update = next_settings(directory)
            if update is None:
                no_config = True  # Nothing newer than what every video was made with: a finished video would come out the same.
            else:
                settings = update['settings']
                finished = {entry['number']: entry for entry in build_plan(
                    directory, settings_id=update['id'], now=now, same_reason=SAME_CONFIG,
                    unchanged=lambda config: made_with_everything(config, settings) and same_language_models(config, settings))['entries']}
        except Exception:  # The config or the Page schedules cannot be read now: no finished video is touched blindly.
            finished = None
    entries = []
    for number in wanted:
        item = by_number[number]
        status = item['status']
        entry = dict(number=number, kind='run', reason='', scheduled=False)
        if status == EXCLUDED:
            entry['kind'] = 'restore'
        elif status == 'failed':
            entry['kind'] = 'retry'
        elif status == 'completed':
            if item.get('redo'):
                entry['kind'] = 'queued'
            elif finished is None:
                entry.update(kind='skip', reason=NOT_NOW)
            elif no_config:
                entry.update(kind='skip', reason=NO_NEW_CONFIG)
            else:
                found = finished.get(number)
                if found is None:
                    entry.update(kind='skip', reason='Chưa có thành phẩm.')
                else:
                    reason = SAME_CONFIG if found['reason'] == 'Đã làm bằng giọng này.' else found['reason']
                    entry.update(kind=found['kind'], reason=reason, scheduled=found['scheduled'])
        elif status in DONE:
            entry.update(kind='skip', reason=APPROVAL)
        entries.append(entry)
    todo = [entry['number'] for entry in entries if entry['kind'] != 'skip']
    return dict(entries=entries, numbers=todo)


def describe(result):
    """The text of the confirmation window: one line per kind with the video numbers, and the skipped ones with their reasons."""
    groups = {}
    for entry in result['entries']:
        groups.setdefault(entry['kind'], []).append(entry)
    lines = []
    for kind in ('run', 'retry', 'restore', 'redo', 'queued'):
        found = groups.get(kind)
        if found:
            lines.append(f'• {len(found)} video {KIND_TEXT[kind]}: '+', '.join(str(entry['number']) for entry in found))
            if kind == 'redo' and any(entry['scheduled'] for entry in found):
                lines.append('   (lịch đăng chưa gửi của video làm lại sẽ chuyển sang file mới, giữ giờ và caption)')
    for entry in groups.get('skip', []):
        lines.append(f'• Video {entry["number"]}: bỏ qua. {entry["reason"]}')
    return '\n'.join(lines)


def apply(directory, numbers):
    """Put these videos first. Returns dict(mode, changed): 'now' = marked here (the link's worker is not running), 'request' =
    left for the worker of a running link to take before its next video."""
    directory = Path(directory).resolve()
    numbers = list(dict.fromkeys(number for number in numbers if type(number) is int))
    if not numbers:
        raise ValueError('Không có video nào để chạy trước.')
    try:
        lock = _job_lock(directory)
        lock.__enter__()
    except (OSError, ValueError):  # Only taking the lock: the link's worker holds it while it works.
        atomic_json(directory/f'first-{uuid.uuid4().hex}.json', dict(numbers=numbers))
        return dict(mode='request', changed=[])
    try:
        from teodub.campaign_batch import _identity
        manifest = read_json(directory/'campaign.json')
        _identity(manifest, directory)  # Archived or damaged data is refused before anything is written.
        changed = mark_selected(manifest, numbers)
        if changed:
            atomic_json(directory/'campaign.json', manifest)
        return dict(mode='now', changed=changed)
    finally:
        lock.__exit__(None, None, None)


def pending_requests(directory):
    """How many requests the worker has not taken yet (read only)."""
    return len(list(Path(directory).glob(PRIORITY_REQUESTS)))
