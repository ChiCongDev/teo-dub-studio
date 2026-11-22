"""Owner 22/11/2026: a Playwright failure is an English "call log" of 40+ lines; on the Quản lý Page screen it pushed the account and
Page tables out of sight. `brief` turns it into one short Vietnamese line. The post's own history (activity events) keeps the full text."""
import re

_LOG_LINE = re.compile(r'\s+(-|[0-9]+ ×)')  # "  - waiting for …", "    2 × waiting for …"


def brief(text):
    """`text` unchanged unless it carries a Playwright call log; then one line: what happened, plus Tèo's own words after the log."""
    text = str(text or '')
    if 'Call log:' not in text:
        return text
    head, _, log = text.partition('Call log:')
    lines = head.strip().splitlines()
    first = lines[0].strip() if lines else ''
    tail = ' '.join(line.strip() for line in log.splitlines()[1:] if line.strip() and not _LOG_LINE.match(line))
    timeout = re.search(r'Timeout ([0-9]+)ms exceeded', first)
    if 'intercepts pointer events' in log:
        core = 'Chrome chưa bấm được: nút trên Facebook đang bị một ô/thanh thông báo của Facebook che.'
    elif timeout:
        core = 'Chrome chờ %g giây mà nút/ô trên Facebook chưa sẵn sàng.' % (int(timeout.group(1))/1000)
    else:
        core = first[:300]
    return (core+(' '+tail if tail else '')).strip()
