"""Display genuine worker progress without estimating AI completion."""
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

STAGES = {'download': 'Đang tải', 'source': 'Kiểm tra nguồn',
          'recognition': 'Nhận dạng lời', 'translation': 'Đang dịch',
          'speech_plan': 'Chuẩn bị lời đọc', 'voice_connection': 'Kết nối giọng',
          'voice': 'Tạo giọng', 'render': 'Dựng / kiểm tra video',
          'google_drive': 'Video xong · lưu Drive'}
STATUS = {'pending': 'Chờ xử lý', 'running': 'Đang khởi động', 'completed': 'Hoàn tất',
          'review_required': 'Xong · cần duyệt', 'cancelled': 'Đã dừng', 'failed': 'Lỗi'}
DISPLAY_TIMEZONE = timezone(timedelta(hours=7))


def completion_time(item, event=None):
    """Only a recorded completion timestamp can populate this column."""
    if item.get('status') not in ('completed', 'review_required'):
        return '—'
    event = event or {}
    candidates = [item.get('completed_at')]
    if event.get('stage') == 'completed':
        candidates.append(event.get('time'))
    for value in candidates:
        try:
            date = datetime.fromisoformat(value)
            if date.tzinfo is not None:
                date = date.astimezone(DISPLAY_TIMEZONE)
            return date.strftime('%d/%m/%Y %H:%M:%S')
        except (ValueError, TypeError):
            continue
    return '—'


def processing_time(item):
    seconds = item.get('processing_seconds')
    if type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds < 0:
        return 'Chưa ghi nhận'
    seconds = int(seconds)
    return f'{seconds//3600:02}:{seconds//60%60:02}:{seconds%60:02}'


def action_for_error(message, status='failed'):
    text = str(message).casefold()
    if any(word in text for word in ('hạn mức', 'quota', 'usage limit', 'rate limit', 'credits')):
        return 'Kiểm tra hạn mức tài khoản; khi có hạn mức, tiếp tục hoặc thử lại video lỗi.'
    if any(word in text for word in ('đăng nhập', 'login', 'sign in', 'unauthorized')):
        return 'Kiểm tra đăng nhập của dịch vụ đang báo lỗi, rồi tiếp tục hoặc thử lại video lỗi.'
    if any(word in text for word in ('timeout', 'timed out', 'network', 'connection', 'mạng', 'kết nối')):
        return 'Kiểm tra kết nối mạng và dịch vụ; khi ổn định, tiếp tục hoặc thử lại video lỗi.'
    if any(word in text for word in ('đã bị thay đổi', 'không khớp', 'không hợp lệ')):
        return 'Kiểm tra cấu hình, tệp nguồn và thành phẩm đã lưu trước khi chạy tiếp.'
    if status == 'failed':
        return 'Xem chi tiết hoặc nhật ký của video; bấm Thử lại video lỗi sau khi xử lý nguyên nhân.'
    return 'Các bước đã hoàn tất được giữ lại. Bấm Chạy / Tiếp tục khi sẵn sàng.'


def campaign_notice(manifest, *, active=False):
    """Persist the actual reason and suggested next action without modal spam."""
    status = manifest.get('status')
    message = ' '.join(str(manifest.get('message') or '').split())[:800]
    if status == 'running' and not active:
        return ('Chiến dịch bị gián đoạn: không còn tiến trình giữ phiên; chưa xác định nguyên nhân. '
                'Kiểm tra nhật ký rồi bấm Chạy / Tiếp tục.')
    if status == 'paused':
        message = message or 'Phiên đã tạm dừng nhưng chưa ghi nguyên nhân.'
        return 'Tạm dừng: '+message+' '+action_for_error(message, status)
    errors = [item for item in manifest.get('items', []) if item.get('status') == 'failed']
    if errors:
        latest = max(errors, key=lambda item: str(item.get('last_attempt_finished_at') or ''))
        reason = ' '.join(str(latest.get('message') or 'Chưa ghi nguyên nhân lỗi.').split())[:800]
        continuation = ' Chiến dịch vẫn đang xử lý các video tiếp theo.' if active else ''
        return (f'{len(errors)} video lỗi. Video {latest.get("number", "?")}: {reason}.'
                +continuation+' '+action_for_error(reason))
    if status == 'needs_attention':
        return 'Cần kiểm tra: '+(message or 'Còn video chưa hoàn tất.')
    return ''


def latest_progress(job):
    """Ignore partial final writes, retaining the latest reported numeric percent."""
    try:
        path = Path(job)/'events.jsonl'
        with path.open('rb') as stream:
            stream.seek(max(0, path.stat().st_size-24000))
            lines = stream.read().splitlines()
    except OSError:
        return {}, None
    latest, percent = {}, None
    for line in reversed(lines):
        try:
            event = json.loads(line)
        except (ValueError, UnicodeError):
            continue
        if not isinstance(event, dict):
            continue
        if not latest:
            latest = event
        value = event.get('percent')
        if type(value) in (int, float) and 0 <= value <= 100:
            percent = value
            break
    return latest, percent


def tracking_row(item, active=True):
    status = item['status']
    event, percent = latest_progress(item.get('execution_job') or item['job']) if item.get('job') and status != 'pending' else ({}, None)
    label = STATUS.get(status, status)
    message = item.get('message', '')
    if status == 'running':
        label = STAGES.get(event.get('stage'), label) if active else 'Gián đoạn · Tiếp tục'
        message = str(event.get('message') or message)
        if not active:
            message = 'Không còn tiến trình giữ phiên; chưa xác định nguyên nhân. Bấm Chạy / Tiếp tục. Bước cuối: '+message
    if status in ('completed', 'review_required'):
        percent = 100
    elif status == 'pending':
        percent = 0
    return (f'Video {item["number"]}', item.get('title') or '(Chưa có tiêu đề)', item['url'],
            label, f'{percent:.0f}%' if percent is not None else '—', completion_time(item, event), message[:600])
