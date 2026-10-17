"""Read-only posting summaries for rendered campaign videos.

Do not construct a publishing store/controller here: those initialize databases
and can change scheduler state. A campaign refresh must only observe saved work.
"""
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import sqlite3


LOCAL_TIME = timezone(timedelta(hours=7))
WAITING = 'Đang lên lịch'
POSTING = 'Đang đăng'
SUBMITTED = 'Đã gửi — chưa xác minh'
PUBLISHED = 'Đăng thành công'
ATTENTION = 'Cần kiểm tra'
PAUSED = 'Tạm dừng lịch'
FAILED = 'Đăng thất bại'
NOT_POSTED = 'Chưa đăng'


def _summary(label, details, *, published=0, targets=0, entries=None):
    return dict(label=label, details=details, published=published, targets=targets,
                entries=entries or [])


def _object(value):
    result = json.loads(value or '{}')
    if not isinstance(result, dict):
        raise ValueError('Dữ liệu lịch đăng không đúng định dạng.')
    return result


def _connect(path):
    return sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True, timeout=0.15)


def _read_schedules(path, ident):
    with closing(_connect(path)) as conn:
        conn.row_factory = sqlite3.Row
        # The JSON still contains the original key after cancelling a schedule.
        rows = conn.execute('SELECT * FROM schedules WHERE source_key LIKE ? OR source LIKE ?',
                            (ident+':%', '%'+ident+':%')).fetchall()
        return [dict(row) for row in rows]


def _read_posts(path, schedules):
    ids = sorted({row['post_id'] for row in schedules if row.get('state') != 'cancelled'})
    if not ids:
        return {}
    if not path.is_file():
        raise ValueError('Chưa tìm thấy hồ sơ kết quả đăng bài.')
    result = {}
    with closing(_connect(path)) as conn:
        conn.row_factory = sqlite3.Row
        for offset in range(0, len(ids), 400):
            subset = ids[offset:offset+400]
            placeholders = ','.join('?' for _ in subset)
            for row in conn.execute('SELECT * FROM posts WHERE id IN ('+placeholders+')', subset):
                post = dict(row)
                result[(post['id'], post['account_id'], post['page_key'])] = post
    return result


def _released_removal(schedule):
    """Untouched removals and already cancelled rows have released keys."""
    return (schedule.get('state') == 'removed'
            and schedule.get('source_key') in {
                prefix + str(schedule.get('id', '')) for prefix in ('removed:', 'cancelled:')})


def _untouched_removed_post(schedule, post):
    # The removal key is not publication evidence. Recheck the saved post so a
    # damaged key cannot erase a real or uncertain publishing result.
    try:
        return (_released_removal(schedule)
                and not any(schedule.get(key) for key in
                            ('attempted_at', 'finished_at', 'request_id', 'phase'))
                and post.get('state') in {'draft', 'failed'} and not post.get('attempt')
                and not any(post.get(key) for key in
                            ('external_id', 'external_url', 'manual_url', 'published_at'))
                and not any(_object(post.get(key)) for key in ('browser_review', 'browser_receipt', 'browser_tracking'))
                and not json.loads(post.get('remote_assets') or '[]'))
    except (ValueError, TypeError):
        return False


def _bound_source(data_dir, directory, manifest, item, source):
    number = item['number']
    ident = manifest['id']
    if (directory.parent != data_dir/'campaigns' or directory.name != ident or
            not re.fullmatch(r'[0-9a-f]{32}', ident) or directory.is_symlink() or
            manifest.get('archived_at')):
        raise ValueError('Chiến dịch không còn khớp dữ liệu đã lưu.')
    if (source.get('key') != f'{ident}:{number}' or
            type(source.get('number')) is not int or source['number'] != number or
            Path(source.get('campaign', '')).resolve() != directory):
        raise ValueError('Lịch đăng không khớp chiến dịch và số video.')
    if item.get('status') != 'completed':
        raise ValueError('Video hiện tại chưa ở trạng thái hoàn tất.')
    job = directory/'jobs'/f'{number:04}'
    if Path(item.get('job', '')).resolve() != job or job.is_symlink():
        raise ValueError('Phiên thành phẩm không khớp chiến dịch.')
    from teodub.campaign_settings import effective_job
    job = effective_job(directory, item)
    with (job/'state.json').open(encoding='utf-8') as stream:
        saved = json.load(stream)
    rendered = saved.get('rendered', {})
    if not isinstance(rendered, dict):
        raise ValueError('Không đọc được hồ sơ dựng thành phẩm.')
    outputs = item.get('outputs') or {}
    output = outputs.get('video')
    digest = (rendered.get('hashes') or {}).get('video', '')
    if (not output or rendered.get('outputs') != outputs or
            not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest) or
            source.get('sha256') != digest):
        raise ValueError('Lịch đăng thuộc thành phẩm khác hoặc mã kiểm tra đã thay đổi.')
    video = Path(output).resolve()
    if (Path(source.get('path', '')).resolve() != video or
            not video.is_relative_to(Path(manifest['output_dir']).resolve()) or
            video.suffix.lower() not in {'.mp4', '.mov'}):
        raise ValueError('Đường dẫn video của lịch không khớp thành phẩm hiện tại.')
    # The durable render hash binds the artifact; do not read/hash an MP4 every
    # second. Actual upload verification is still the scheduler's responsibility.


def _entry(schedule, post, binding_error, posts_error):
    try:
        snapshot = _object(schedule.get('snapshot'))
    except (ValueError, TypeError):
        snapshot = {}
    name = snapshot.get('target_name') or schedule.get('page_key') or 'Page chưa xác định'
    account = schedule.get('account_id', '')
    try:
        due = datetime.fromtimestamp(float(schedule['due_at']), LOCAL_TIME).strftime('%d/%m/%Y %H:%M')
    except (ValueError, TypeError, KeyError, OverflowError, OSError):
        due = 'Chưa đọc được giờ đăng'
    state = schedule.get('state')
    message = str(schedule.get('message') or '')
    url = ''
    removed_history = False
    if state == 'cancelled':
        label = 'Đã bỏ lịch'
    elif binding_error or posts_error:
        label = ATTENTION
        message = binding_error or posts_error
    elif post is None:
        label = ATTENTION
        message = 'Không tìm thấy bài đăng khớp tài khoản và Page của lịch.'
    elif not _matches_snapshot(schedule, post, snapshot):
        label = ATTENTION
        message = 'Page, nội dung hoặc video của bài không khớp lịch đã duyệt.'
    else:
        post_state = post.get('state')
        try:
            receipt = _object(post.get('browser_receipt'))
        except (ValueError, TypeError):
            receipt = {}
        external_url = post.get('external_url')
        if (post_state == 'published' and external_url and
                receipt.get('source') == 'visible_dom' and receipt.get('url') == external_url):
            label, url = PUBLISHED, external_url
            message = 'Facebook đã được xác nhận qua trang bài đăng.'
        elif post_state == 'published' or state == 'published':
            label = ATTENTION
            message = 'Có trạng thái đã đăng nhưng thiếu bằng chứng xác nhận bài trên Facebook.'
        elif state == 'submitted' and post_state in {'unknown','processing'}:
            label = SUBMITTED
            message = str(post.get('message') or message or 'Đã tạm dừng kiểm tra trạng thái sau khi gửi.')
        elif post_state == 'unknown' or post.get('manual_url'):
            label = ATTENTION
            message = str(post.get('message') or message or 'Kết quả đăng chưa được xác nhận; không tự gửi lại.')
        elif state == 'removed' and _untouched_removed_post(schedule, post):
            label = 'Đã xóa khỏi danh sách Tèo'
            removed_history = True
            message = 'Lịch chưa thao tác đã xóa khỏi danh sách Tèo; giữ tệp và lịch sử.'
        elif post_state == 'failed':
            label = FAILED
            message = str(post.get('message') or message or 'Lượt đăng báo lỗi.')
        elif state == 'removed':
            label = ATTENTION
            message = 'Đã xóa khỏi danh sách Tèo; lượt cũ cần kiểm tra, không tự gửi lại. ' + str(
                post.get('message') or message)
        elif state == 'needs_attention':
            label = ATTENTION
        elif state == 'paused':
            label = PAUSED if not schedule.get('attempted_at') and post_state == 'draft' else ATTENTION
        elif post_state in {'preparing', 'submitting', 'processing'}:
            label = POSTING
            message = str(post.get('message') or message)
        elif state in {'preparing', 'submitting'} and post_state in {'draft', 'review'}:
            label = POSTING
        elif state in {'scheduled', 'armed'} and post_state == 'draft':
            label = WAITING
        else:
            label = ATTENTION
            message = message or 'Trạng thái lịch và bài đăng chưa khớp; cần kiểm tra.'
    details = f'{name} · tài khoản {account}\nGiờ đăng: {due} · {label}'
    if message:
        details += '\n'+message
    if url:
        details += '\n'+url
    return dict(label=label, details=details, account_id=account,
                page_key=schedule.get('page_key'), schedule_id=schedule.get('id'),
                cancelled=state == 'cancelled' or removed_history, url=url)


def _matches_snapshot(schedule, post, snapshot):
    keys = ('target_name', 'target_url', 'target_id', 'body', 'media',
            'delivery_mode', 'browser_finish_mode')
    if any(key not in snapshot for key in keys):
        return False
    try:
        media = json.loads(post.get('media') or 'null')
        source = _object(schedule.get('source'))
        if (not isinstance(media, list) or len(media) != 1 or
                not isinstance(media[0], dict) or
                Path(media[0].get('path', '')).resolve() != Path(source['path']).resolve() or
                any(media[0].get(key) != source.get(key) for key in ('size', 'mtime_ns'))):
            return False
    except (ValueError, TypeError, KeyError, OSError):
        return False
    return (all((media if key == 'media' else post.get(key)) == snapshot[key] for key in keys)
            and schedule.get('body') == snapshot['body']
            and snapshot['delivery_mode'] == 'browser' and snapshot['browser_finish_mode'] == 'tool')


def campaign_posting_statuses(data_dir, directory, manifest):
    """Observe local schedule/receipt evidence without starting any work."""
    items = manifest.get('items') or []
    empty = 'Chưa có lịch đăng từ video này trong Tèo. Bài đăng thủ công ngoài Tèo chưa được kiểm tra.'
    summaries = {item['number']: _summary(NOT_POSTED, empty) for item in items}
    data_dir, directory = Path(data_dir).resolve(), Path(directory).resolve()
    ident = manifest.get('id', '')
    if (not isinstance(ident, str) or not re.fullmatch(r'[0-9a-f]{32}', ident) or
            directory.parent != data_dir/'campaigns' or directory.name != ident):
        return {number: _summary(ATTENTION, 'Không đọc được định danh chiến dịch để đối chiếu lịch đăng.')
                for number in summaries}
    path = data_dir/'page-publishing'/'schedules.sqlite3'
    try:
        path.stat()
    except FileNotFoundError:
        return summaries
    except OSError as exc:
        return {number: _summary(ATTENTION, 'Chưa đọc được lịch đăng: '+str(exc))
                for number in summaries}
    try:
        schedules = _read_schedules(path, ident)
    except (OSError, ValueError, sqlite3.Error) as exc:
        return {number: _summary(ATTENTION, 'Chưa đọc được lịch đăng: '+str(exc))
                for number in summaries}
    try:
        posts = _read_posts(path.with_name('activity.sqlite3'), schedules)
        posts_error = ''
    except (OSError, ValueError, KeyError, sqlite3.Error) as exc:
        posts, posts_error = {}, 'Chưa đọc được kết quả đăng: '+str(exc)
    by_number = {item['number']: item for item in items}
    grouped = {number: [] for number in summaries}
    # Validate each distinct source snapshot once when it has many Page schedules.
    binding_cache = {}
    for schedule in schedules:
        source, parse_error = {}, ''
        try:
            source = _object(schedule.get('source'))
        except (ValueError, TypeError):
            parse_error = 'Dữ liệu nguồn của lịch đăng không đọc được.'
        released_removal = _released_removal(schedule)
        key = source.get('key') if schedule.get('state') == 'cancelled' or released_removal else schedule.get('source_key', '')
        match = re.fullmatch(re.escape(ident)+r':([1-9][0-9]*)', str(key))
        if not match:
            # A damaged source with an intact column key still belongs to this row.
            match = re.fullmatch(re.escape(ident)+r':([1-9][0-9]*)', str(schedule.get('source_key', '')))
            if not match:
                continue
            parse_error = parse_error or 'Định danh nguồn của lịch đăng không khớp.'
        number = int(match.group(1))
        item = by_number.get(number)
        if item is None:
            continue
        binding_error = parse_error
        if schedule.get('state') != 'cancelled' and not binding_error:
            cache_key = (number, json.dumps(source, sort_keys=True))
            if cache_key not in binding_cache:
                try:
                    _bound_source(data_dir, directory, manifest, item, source)
                    binding_cache[cache_key] = ''
                except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
                    binding_cache[cache_key] = 'Chưa đối chiếu được video: '+str(exc)
            binding_error = binding_cache[cache_key]
            if not released_removal and schedule.get('source_key') != f'{ident}:{number}':
                binding_error = 'Định danh lịch không khớp video hiện tại.'
        post = posts.get((schedule.get('post_id'), schedule.get('account_id'), schedule.get('page_key')))
        grouped[number].append(_entry(schedule, post, binding_error, posts_error))
    for number, entries in grouped.items():
        if not entries:
            continue
        active = [entry for entry in entries if not entry['cancelled']]
        published = sum(entry['label'] == PUBLISHED for entry in active)
        if active:
            labels = {entry['label'] for entry in active}
            label = next((state for state in (POSTING, ATTENTION, FAILED, WAITING, PAUSED, SUBMITTED, PUBLISHED)
                          if state in labels), ATTENTION)
            if 0 < published < len(active):
                label += f' · đã đăng {published}/{len(active)}'
        else:
            label = NOT_POSTED
        details = '\n\n'.join(entry['details'] for entry in entries)
        if not active:
            details = empty+'\n\nLịch sử:\n'+details
        summaries[number] = _summary(label, details, published=published,
                                     targets=len(active), entries=entries)
    return summaries
