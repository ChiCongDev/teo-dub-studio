"""30/10/2026 (owner): put many video appointments that are already past, paused or failed before the first send onto the
Page's own daily posting times in one step, and arm them.

Nothing here posts. It only uses what already exists: `PageScheduleStore.reschedule` for an appointment that was never sent,
`PageScheduleController.recover_schedule` for one whose preparation stopped before Publish (after the owner's explicit
confirmations), and `start_many` to arm. An appointment Tèo may already have sent ("Đã gửi — chưa xác minh") is never moved.
"""
from collections import Counter
from copy import deepcopy
from datetime import datetime, timedelta
import re
import time

from teodub.page_schedule import LOCAL_TIME, display_time

SAME_SLOT_SECONDS = 60  # Two posts of one Page never share a minute (the same rule as the automatic mode).
LEAD_SECONDS = 300  # The first new time is at least this far ahead.
GONE = ('removed', 'cancelled')  # Rows the owner deleted or cancelled take no place in a day.
MAX_DAYS = 365  # `reschedule` / `recover_schedule` accept a time less than 366 days ahead.
BUSY_STATES = {'armed', 'preparing', 'submitting', 'verifying'}


def parse_times(text):
    """'11:30, 19:00' -> ['11:30', '19:00'] (sorted, zero-padded); anything else is refused."""
    pieces = [piece for piece in re.split(r'[,;\s]+', str(text).strip()) if piece]
    if not pieces:
        raise ValueError('Gõ các giờ đăng mỗi ngày, cách nhau dấu phẩy, ví dụ 11:30, 19:00.')
    result = []
    for piece in pieces:
        match = re.fullmatch(r'([0-9]{1,2}):([0-9]{1,2})', piece)
        if not match or int(match[1]) > 23 or int(match[2]) > 59:
            raise ValueError(f'Giờ "{piece}" chưa hợp lệ: dùng dạng HH:MM, ví dụ 19:00.')
        result.append(f'{int(match[1]):02d}:{int(match[2]):02d}')
    if len(set(result)) != len(result):
        raise ValueError('Các giờ trong ngày không được trùng nhau.')
    if len(result) > 48:
        raise ValueError('Tối đa 48 giờ đăng mỗi ngày.')
    return sorted(result)


def page_times(data_dir, account_id, page_key):
    """The daily posting times saved in the Page's AI settings (own times or first-time + gap), or [] when there are none."""
    try:
        from teodub.page_ai import PageAIStore, _owner_times
        policy = PageAIStore(data_dir).policy(account_id, page_key)
        config = (policy or {}).get('config') or {}
        if 'slot_times' not in config and 'slot_start' not in config:
            return []
        return sorted({f'{hour:02d}:{minute:02d}' for hour, minute in _owner_times(config) if 0 <= hour < 24})
    except (OSError, ValueError, KeyError, TypeError):
        return []


def free_slots(times, count, rows, clock, start):
    """`count` free posting times in the future, in order: each day's `times` from `start` on, at most len(times) videos a day
    (counting what the Page already has that day), never within SAME_SLOT_SECONDS of another appointment of the Page."""
    begin = max(float(start), clock+LEAD_SECONDS)
    live = [row for row in rows if row['state'] not in GONE]
    reserved = [row['due_at'] for row in live]
    used = Counter(datetime.fromtimestamp(row['due_at'], LOCAL_TIME).date()
                   for row in live if row['source'].get('kind') != 'text')
    day = datetime.fromtimestamp(begin, LOCAL_TIME).replace(hour=0, minute=0, second=0, microsecond=0)
    limit = clock+MAX_DAYS*86400
    slots = []
    while len(slots) < count and day.timestamp() <= limit:
        taken = used[day.date()]
        for text in times:
            hour, minute = map(int, text.split(':'))
            stamp = day.replace(hour=hour, minute=minute).timestamp()
            if len(slots) >= count or taken >= len(times):
                break
            if begin <= stamp <= limit and all(abs(stamp-other) >= SAME_SLOT_SECONDS for other in reserved+slots):
                slots.append(stamp)
                taken += 1
        day += timedelta(days=1)
    if len(slots) < count:
        raise ValueError('Không đủ giờ trống trong một năm tới; chọn ít video hơn hoặc thêm giờ đăng mỗi ngày.')
    return slots


def classify(controller, rows):
    """For each selected row: kind 'fresh' (never sent), 'recover' (preparation stopped before Publish) or 'skip' + the reason."""
    result = []
    for row in rows:
        entry = dict(id=row['id'], label=row['source'].get('label', row['id']), old_due_at=row['due_at'], kind='skip', reason='')
        source = row['source']
        if row['state'] == 'removed':
            entry['reason'] = 'Đã xóa khỏi danh sách.'
        elif source.get('kind') == 'text' or not source.get('path'):
            entry['reason'] = 'Bài chữ: dùng nút Đổi giờ trong Chi tiết / Tùy chọn.'
        elif row['id'] in controller.active or row['state'] in BUSY_STATES:
            entry['reason'] = 'Đang bật lịch hoặc đang xử lý; bấm Tạm dừng trước nếu muốn đổi giờ.'
        elif row['state'] in {'published', 'cancelled'}:
            entry['reason'] = 'Đã đăng hoặc đã bỏ lịch.'
        elif not row['attempted_at'] and row['state'] in {'scheduled', 'paused', 'needs_attention'}:
            entry['kind'] = 'fresh'
        elif row['state'] == 'submitted' or row['phase'] in {'publish', 'verify'}:
            entry['reason'] = ('Tèo đã bấm Đăng hoặc chưa rõ kết quả: kiểm tra bài trên Facebook, không tự đăng lại. '
                               'Nếu chắc chắn chưa có bài, dùng nút "Bỏ lượt cũ và tạo lịch mới…".')
        elif controller.store.can_recover_schedule(row):
            entry['kind'] = 'recover'
        else:
            entry['reason'] = 'Lượt cũ không đủ điều kiện xếp lại tự động; kiểm tra Facebook rồi dùng "Bỏ lượt cũ và tạo lịch mới…".'
        result.append(entry)
    return result


def build_plan(controller, account_id, page_key, identifiers, times, start, *, clock=None):
    clock = time.time() if clock is None else clock
    rows = []
    for ident in dict.fromkeys(identifiers):
        row = controller.store.get(ident)
        if (row['account_id'], row['page_key']) != (account_id, page_key):
            raise ValueError('Lịch không thuộc tài khoản/Page đang mở.')
        rows.append(row)
    if not rows:
        raise ValueError('Chọn một hoặc nhiều lịch video.')
    times = parse_times(','.join(times) if isinstance(times, (list, tuple)) else times)
    entries = classify(controller, rows)
    by_id = {row['id']: row for row in rows}
    movable = sorted((entry for entry in entries if entry['kind'] != 'skip'),
                     key=lambda entry: (entry['old_due_at'], by_id[entry['id']]['source'].get('number', 0), entry['id']))
    taken = {entry['id'] for entry in movable}
    others = [row for row in controller.store.list(account_id, page_key) if row['id'] not in taken]
    slots = free_slots(times, len(movable), others, clock, start) if movable else []
    for entry, stamp in zip(movable, slots):
        entry['due_at'] = stamp
        if entry['kind'] == 'recover':
            entry['preview'] = controller.recovery_preview(account_id, page_key, entry['id'])
    return dict(entries=entries, times=times, start=float(start), clock=clock, moved=len(movable),
                recover=sum(entry['kind'] == 'recover' for entry in entries),
                skipped=sum(entry['kind'] == 'skip' for entry in entries),
                first=slots[0] if slots else None, last=slots[-1] if slots else None)


def signature(plan):
    return [(entry['id'], entry['kind'], entry.get('due_at')) for entry in plan['entries']]


def apply_plan(controller, account_id, page_key, previewed, *, checked_unpublished=False, closed_composer=False):
    """Rebuild the plan now. If it differs from what the owner saw, change nothing; otherwise move and arm every row."""
    if previewed['recover'] and not (checked_unpublished is True and closed_composer is True):
        raise ValueError('Cần xác nhận đã kiểm tra Facebook chưa có bài và đã đóng Chrome cũ trước khi xếp lại.')
    plan = build_plan(controller, account_id, page_key, [entry['id'] for entry in previewed['entries']],
                      previewed['times'], previewed['start'])
    if signature(plan) != signature(previewed):
        raise ValueError('Lịch trống hoặc trạng thái các video đã thay đổi từ lúc xem trước; bấm Xem lịch mới rồi lưu lại. '
                         'Chưa đổi lịch nào.')
    results, to_arm = [], []
    for entry in plan['entries']:
        outcome = dict(id=entry['id'], label=entry['label'], outcome='skipped', message=entry['reason'])
        if entry['kind'] != 'skip':
            try:
                if entry['kind'] == 'fresh':
                    controller.store.reschedule(entry['id'], entry['due_at'])
                else:
                    controller.recover_schedule(account_id, page_key, entry['id'], entry['due_at'],
                        expected_preview=deepcopy(entry['preview']), checked_unpublished=True, closed_composer=True)
                to_arm.append(entry['id'])
                outcome.update(outcome='moved', message='Giờ mới '+display_time(entry['due_at'])+' (UTC+7).')
            except (OSError, ValueError, RuntimeError) as exc:
                outcome.update(outcome='failed', message=str(exc))
        results.append(outcome)
    armed = {row['id']: row for row in controller.start_many(account_id, page_key, to_arm)} if to_arm else {}
    for outcome in results:
        if outcome['id'] in armed:
            started = armed[outcome['id']]
            if started['outcome'] == 'started':
                outcome.update(outcome='armed', message='Đã bật lịch lúc '+outcome['message'].removeprefix('Giờ mới '))
            else:
                outcome.update(outcome='moved', message=outcome['message']+' Chưa bật được: '+started['message'])
    return results
