"""The text of the check "Kiểm tra video làm lại được" (owner 06/11/2026): which finished videos that are not posted yet are made again, in
which order, and why the others are left. (The Sub button of a link's tab that used this was removed 10/11/2026; Chỉnh sửa cấu hình… does
it.) The finished videos are made again with the Sub Việt choice, which is the one the link saves. They are made again in new revision jobs (no download, no AI call, the voice already made is
reused), the ones whose Page schedule is soonest first. A video already posted or sent to a Page is never touched; a schedule not sent
yet keeps its time, Page and caption and moves to the new file when it is ready, and posts the old file at its time if the new one
is not ready (campaign_redo, page_schedule_redo)."""
from datetime import datetime, timedelta, timezone

from teodub.subtitle_style import SUBTITLE_BOXES

LOCAL_TIME = timezone(timedelta(hours=7))
SHOWN = 12  # Videos listed with their time; the rest are counted.


def sub_text(burn, box):
    if not burn:
        return 'không ghi Sub Việt vào hình (chỉ có Sub bật/tắt)'
    return 'ghi Sub Việt vào hình, '+(SUBTITLE_BOXES[box]['label'].lower() if box != 'none' else 'không khung che')


def describe(plan, order, name_of=lambda number: f'Video {number}'):
    """The text of the check: how many videos will be made again, in which order, and why the others are left."""
    todo = [e for e in plan['entries'] if e['kind'] == 'redo']
    skipped = [e for e in plan['entries'] if e['kind'] == 'skip']
    lines = [f'Sẽ làm lại: {len(todo)} video đã hoàn thành, chưa đăng.'
             +(f' Trong đó {plan["scheduled"]} video có lịch đăng; lịch giữ giờ, Page và caption, chuyển sang file mới khi nó xong.'
               if plan['scheduled'] else '')]
    again = plan.get('retranslate') or []
    if again:
        lines.append(f'  Trong đó {len(again)} video làm bằng mô hình nhận dạng/dịch khác: sẽ dịch lại bằng AI (tốn AI; lời Việt có thể '
                     f'khác bản cũ một chút).')
    if order:
        lines.append('Làm trước, theo giờ đăng gần nhất:')
        lines += [f'  {index}. {name_of(number)} — đăng {datetime.fromtimestamp(due, LOCAL_TIME).strftime("%d/%m %H:%M")}'
                  for index, (number, due) in enumerate(order[:SHOWN], 1)]
        if len(order) > SHOWN:
            lines.append(f'  … và {len(order)-SHOWN} video có lịch khác.')
        rest = len(todo)-len(order)
        if rest > 0:
            lines.append(f'Sau đó: {rest} video chưa có lịch hoặc lịch đã lỡ giờ, theo số video.')
    lines.append(f'Bỏ qua: {len(skipped)} video.')
    reasons = {}
    for entry in skipped:
        reasons.setdefault(entry['reason'], []).append(entry['number'])
    for reason, numbers in reasons.items():
        shown = ', '.join(str(number) for number in numbers[:15])+(' …' if len(numbers) > 15 else '')
        lines.append(f'  · {len(numbers)} video — {reason} ({shown})')
    return '\n'.join(lines)
