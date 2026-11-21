"""Views numbers of a Page (Facebook Professional Dashboard -> Insights -> Views), read after a post and kept per Page.

Owner 18/11/2026. Read on the real Facebook (probe of 18/11, nothing was posted or changed):
  * https://www.facebook.com/professional_dashboard/profile_insights/views/?date_range=LAST_7D | LAST_14D | LAST_28D | LAST_60D | LAST_90D |
    LAST_MONTH | CUSTOM&start_date=YYYY-MM-DD&end_date=YYYY-MM-DD work. Anything else (THIS_MONTH, LAST_30D …) silently becomes 28 days,
    so the range label printed on the page is ALWAYS compared with what was asked.
  * Four cards, each "number / Tăng|Giảm N% / N% / label": Lượt xem, Lượt xem trong tối thiểu 3 giây, … 1 phút, Thời gian xem. The percent is
    against the previous period of the same length, so every range has its own percent.
  * Facebook counts up to YESTERDAY. The page names no Page, so the Chrome identity is verified before opening it.
Read only: nothing is clicked or typed on Facebook.
"""
import datetime
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import time

from teodub.common import Cancelled, check_cancel

BASE_URL = 'https://www.facebook.com/professional_dashboard/profile_insights/views/'
# 21/11/2026 (probe): the Audience page shows 'Tổng số người theo dõi' / 'Total followers' - the same number for every range. It is either
# its own card (number line, then the label line) or, for a Page with no new follows, the headline ('4 Tổng số người theo dõi').
AUDIENCE_URL = 'https://www.facebook.com/professional_dashboard/profile_insights/audience/'
FOLLOWER_LABELS = ('Tổng số người theo dõi', 'Total followers')
FIXED = {'7d': 7, '14d': 14, '28d': 28, '60d': 60, '90d': 90}
# Owner 18/11/2026: 'Hôm nay' too. Facebook answers a one-day custom range for today (part of the day so far, probe of 18/11).
CHOICES = [('today', 'Hôm nay'), ('yesterday', 'Hôm qua'), ('7d', '7 ngày qua'), ('14d', '14 ngày qua'), ('28d', '28 ngày qua'), ('60d', '60 ngày qua'), ('90d', '90 ngày qua'),
           ('this_month', 'Tháng này'), ('last_month', 'Tháng trước')]
# Owner 18/11/2026: choosing a range must show its numbers at once, so every standard range is read in one go (buttons, and after
# every post) and kept; choosing only reads the saved file.
STANDARD = tuple(key for key, _ in CHOICES)
AFTER_POST = STANDARD
AFTER_POST_MIN_MINUTES = 30  # Several posts close together read once.
KEEP_CUSTOM = 60  # Custom ranges kept per Page ('Hôm nay' / 'Hôm qua' add one dated range a day each).
# The Facebook account's own language decides the words on the page: Vietnamese and English were seen (probes of 18/11). Both are read.
METRICS = (('views', 'Lượt xem'), ('v3', 'Lượt xem trong tối thiểu 3 giây'),
           ('v60', 'Lượt xem trong tối thiểu 1 phút'), ('watch', 'Thời gian xem'))
LABELS = {'vi': dict(METRICS), 'en': dict(views='Views', v3='3-second views', v60='1-minute views', watch='Watch time')}
LABEL_OF = {text: key for words in LABELS.values() for key, text in words.items()}
RANGE_LINE = re.compile(r'^(?:(?:\d+) ngày qua|Tháng trước|Tùy chỉnh|Last \d+ days|Last month|Custom)(?:: .+)?$')
CHANGE_VI = re.compile(r'^(Tăng|Giảm) ([0-9.,]+)%$')
CHANGE_EN = re.compile(r'^([0-9][0-9.,]*)% (increase|decrease)$')
NO_CHANGE = {'Không thay đổi', 'No change'}
NO_VALUE = {'--', '—', '-'}  # A Page with no watch time shows "--".
MONTHS_EN = ('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec')
WAIT_SECONDS = 30
UNDERSTAND_SECONDS = 10  # A page that shows the card labels but no numbers we understand is given up after this long.


class PageUnreadable(ValueError):
    """The dashboard of this Page cannot be read as it is (not opened, or shown in a form Tèo does not know): do not try its other ranges."""


def yesterday(today=None):
    return (today or datetime.date.today()) - datetime.timedelta(days=1)


def custom_key(start, end):
    return f'custom:{start.isoformat()}:{end.isoformat()}'


def parse_range(key, today=None):
    """-> dict(key, query, kind, days/start/end, name). Raises ValueError for a range that cannot be asked (owner-facing message)."""
    today = today or datetime.date.today()
    last = yesterday(today)
    if key in FIXED:
        return dict(key=key, kind='fixed', days=FIXED[key], query='date_range=LAST_%dD' % FIXED[key], name='%d ngày qua' % FIXED[key])
    if key == 'today':  # Stored under the dated key, so tomorrow it is a new, empty range.
        return _custom(custom_key(today, today), today, today, 'Hôm nay')
    if key == 'yesterday':
        return _custom(custom_key(last, last), last, last, 'Hôm qua')
    if key == 'last_month':
        return dict(key=key, kind='last_month', query='date_range=LAST_MONTH', name='Tháng trước')
    if key == 'this_month':
        start = today.replace(day=1)
        if last < start:
            raise ValueError('Hôm nay mùng 1: Facebook chưa có số liệu của tháng này (chỉ tính đến hôm qua).')
        return _custom(key, start, last, 'Tháng này')
    if isinstance(key, str) and key.startswith('custom:'):
        try:
            start, end = (datetime.date.fromisoformat(part) for part in key.split(':')[1:3])
        except (ValueError, IndexError):
            raise ValueError('Khoảng ngày tùy chọn không hợp lệ.') from None
        if end > last:
            end = last  # Facebook stops at yesterday.
        if start > end:
            raise ValueError('Ngày bắt đầu phải trước ngày kết thúc và không muộn hơn hôm qua.')
        return _custom(custom_key(start, end), start, end, 'Từ %s đến %s' % (start.strftime('%d/%m/%Y'), end.strftime('%d/%m/%Y')))
    raise ValueError('Khoảng thời gian không hợp lệ.')


def _custom(key, start, end, name):
    return dict(key=key, kind='custom', start=start, end=end, name=name,
                query='date_range=CUSTOM&start_date=%s&end_date=%s' % (start.isoformat(), end.isoformat()))


def parse_date_text(text):
    """dd/mm/yyyy (also dd-mm-yyyy or yyyy-mm-dd) -> date. Raises ValueError with an owner-facing message."""
    text = str(text).strip()
    for pattern in ('%d/%m/%Y', '%d-%m-%Y', '%Y-%m-%d'):
        try:
            return datetime.datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    raise ValueError('Ngày “'+text+'” không hợp lệ. Gõ dạng ngày/tháng/năm, ví dụ 05/11/2026.')


def _clean(text):
    return text.replace('﻿', '').replace('​', '').replace('\xa0', ' ').strip()


def _percent(text, language='vi'):
    if language == 'en':
        number = float(text.replace(',', ''))
    elif re.fullmatch(r'[0-9]{1,3}([.,][0-9]{3})+', text):  # 20/11: Facebook writes 'Tăng 29,469%' (comma = thousands) too.
        number = int(text.replace('.', '').replace(',', ''))
    else:
        number = float(text.replace(',', '.'))
    return int(number) if number == int(number) else number


def _change_of(chunk):
    """The percent of one card from its lines: signed number, 0 for 'no change', None when the card carries none."""
    for line in chunk:
        if line in NO_CHANGE:
            return 0
        match = CHANGE_VI.match(line)
        try:
            if match:
                return _percent(match.group(2)) * (-1 if match.group(1) == 'Giảm' else 1)
            match = CHANGE_EN.match(line)
            if match:
                return _percent(match.group(1), 'en') * (-1 if match.group(2) == 'decrease' else 1)
        except ValueError:
            return None
    return None


def _is_change_line(line):
    return line in NO_CHANGE or bool(CHANGE_VI.match(line) or CHANGE_EN.match(line))


def parse_page_text(text):
    """The visible text of the Views page -> (range label, {views|v3|v60|watch: {value, change}}). None until the four cards are there."""
    lines = [_clean(line) for line in str(text).splitlines()]
    lines = [line for line in lines if line]
    for start, line in enumerate(lines):
        if not RANGE_LINE.match(line):
            continue
        found, chunk = {}, []
        for item in lines[start+1:start+60]:
            key = LABEL_OF.get(item)
            if key and key not in found:
                found[key] = chunk
                chunk = []
                if len(found) == len(METRICS):
                    break
            else:
                chunk.append(item)
        if len(found) != len(METRICS):
            continue
        metrics = {}
        for key, chunk in found.items():
            value = chunk[0] if chunk else ''
            if (not (re.search(r'[0-9]', value) or value in NO_VALUE) or len(value) > 40 or _is_change_line(value)
                    or value.endswith('%')):
                break
            metrics[key] = dict(value=value, change=_change_of(chunk[1:]))
        else:
            return line, metrics
    return None


def is_complete(key, entry):
    """A one-day (or any custom) range read on or before its last day holds only part of it: 'Hôm nay' read today is stored under
    today's dated key, and tomorrow that same key is 'Hôm qua'. Only numbers read AFTER the last day count as the day's own."""
    if not isinstance(key, str) or not key.startswith('custom:'):
        return True
    try:
        end = datetime.date.fromisoformat(key.split(':')[2])
        return datetime.datetime.fromisoformat(entry['at']).date() > end
    except (IndexError, ValueError, KeyError, TypeError):
        return False


def parse_followers_text(text):
    """The visible text of the Audience page -> the total number of followers exactly as Facebook writes it ('3,545', '4'), or None."""
    lines = [line for line in (_clean(item) for item in str(text).splitlines()) if line]
    for position, line in enumerate(lines):
        match = re.fullmatch(r'([0-9][0-9.,]*) (?:Tổng số người theo dõi|Total followers)', line)
        if match:
            return match.group(1)
        if line in FOLLOWER_LABELS and position and re.fullmatch(r'[0-9][0-9.,]*', lines[position-1]):
            return lines[position-1]
    return None


def label_matches(label, spec):
    """Did Facebook apply the range that was asked (it silently falls back to 28 days for unknown ones)?"""
    if spec['kind'] == 'fixed':
        return bool(re.match(r'^(?:%d ngày qua|Last %d days)(?:: |$)' % (spec['days'], spec['days']), label))
    if spec['kind'] == 'last_month':
        return bool(re.match(r'^(?:Tháng trước|Last month)(?:: |$)', label))
    if not re.match(r'^(?:Tùy chỉnh|Custom): ', label):
        return False
    return all('%d Tháng %d' % (day.day, day.month) in label or '%s %d' % (MONTHS_EN[day.month-1], day.day) in label
               for day in (spec['start'], spec['end']))


def span_of(label):
    return label.partition(': ')[2] or label


def show_change(change):
    if change is None:
        return ''
    if change == 0:
        return ' ▬0%'
    return (' ▲' if change > 0 else ' ▼') + str(abs(change)) + '%'


def show_metric(entry):
    if not entry:
        return '—'
    return ('—' if entry['value'] in NO_VALUE else entry['value']) + show_change(entry.get('change'))


class PageInsights:
    """page-publishing/page-insights.json: {items: {"<account>:<page>": {<range key>: {at, label, metrics}}}}. Written atomically."""
    lock = threading.Lock()

    def __init__(self, data_dir):
        self.path = Path(data_dir).resolve()/'page-publishing'/'page-insights.json'

    def load(self):
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
            return data['items'] if isinstance(data, dict) and isinstance(data.get('items'), dict) else {}
        except (OSError, ValueError, KeyError):
            return {}

    def get(self, account_id, page_key, key, items=None):
        entry = ((items if items is not None else self.load()).get(account_id+':'+page_key) or {}).get(key)
        return entry if isinstance(entry, dict) and isinstance(entry.get('metrics'), dict) else None

    def save(self, account_id, page_key, key, label, metrics, *, at=None):
        with self.lock:
            items = self.load()
            page = items.setdefault(account_id+':'+page_key, {})
            page[key] = dict(at=at or datetime.datetime.now().isoformat(timespec='seconds'), label=label, metrics=metrics)
            customs = sorted((k for k in page if k.startswith('custom:')), key=lambda k: page[k].get('at', ''))
            for old in customs[:-KEEP_CUSTOM]:
                del page[old]
            self._write(items)

    def followers(self, account_id, page_key, items=None):
        """{at, value} of the last read of the follower total, or None."""
        entry = ((items if items is not None else self.load()).get(account_id+':'+page_key) or {}).get('followers')
        return entry if isinstance(entry, dict) and isinstance(entry.get('value'), str) else None

    def save_followers(self, account_id, page_key, value, *, at=None):
        with self.lock:
            items = self.load()
            items.setdefault(account_id+':'+page_key, {})['followers'] = dict(
                at=at or datetime.datetime.now().isoformat(timespec='seconds'), value=str(value))
            self._write(items)

    def _write(self, items):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(dir=str(self.path.parent), prefix='page-insights-', suffix='.tmp')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as handle:
                json.dump(dict(version=1, items=items), handle, ensure_ascii=False, indent=1)
            os.replace(temp, self.path)
        except BaseException:
            try:
                os.unlink(temp)
            except OSError:
                pass
            raise

    def age_minutes(self, account_id, page_key, key, items=None, now=None):
        entry = self.get(account_id, page_key, key, items)
        try:
            then = datetime.datetime.fromisoformat(entry['at'])
        except (TypeError, KeyError, ValueError):
            return None
        return ((now or datetime.datetime.now()) - then).total_seconds()/60


def read_range(tab, spec, *, cancel=None, finished=None):
    """Open one range in `tab` and return (label, metrics) once Facebook shows the four cards for THIS range."""
    tab.goto(BASE_URL+'?'+spec['query'], wait_until='domcontentloaded', timeout=45000)
    started = time.monotonic()
    deadline = started+WAIT_SECONDS
    seen = ''
    while True:
        check_cancel(cancel)
        if finished is not None and finished.is_set():
            raise Cancelled('Đã dừng lấy số liệu để đóng trình duyệt.')
        tab.wait_for_timeout(1000)
        if 'professional_dashboard' not in tab.url:
            raise PageUnreadable('Facebook không mở trang Bảng điều khiển dành cho doanh nghiệp (đang ở '+tab.url[:80]+').')
        body = tab.evaluate('document.body.innerText')
        parsed = parse_page_text(body)
        if parsed:
            label, metrics = parsed
            if label_matches(label, spec):
                return label, metrics
            seen = label
        elif time.monotonic()-started >= UNDERSTAND_SECONDS and any(word in str(body) for word in LABEL_OF):
            raise PageUnreadable('Trang số liệu có hiện các ô nhưng Tèo chưa hiểu cách Facebook trình bày (ngôn ngữ hoặc kiểu hiển thị mới).')
        if time.monotonic() >= deadline:
            if seen:
                raise ValueError('Facebook không áp dụng khoảng đã chọn (đang hiện: '+seen+').')
            raise PageUnreadable('Chưa thấy số liệu sau %d giây (Page chưa có Bảng điều khiển số liệu, hoặc mạng chậm).' % WAIT_SECONDS)


def read_followers(tab, *, cancel=None, finished=None):
    """Open the Audience page in `tab` and return the total number of followers as text, once Facebook shows it."""
    tab.goto(AUDIENCE_URL, wait_until='domcontentloaded', timeout=45000)
    deadline = time.monotonic()+WAIT_SECONDS
    while True:
        check_cancel(cancel)
        if finished is not None and finished.is_set():
            raise Cancelled('Đã dừng lấy số liệu để đóng trình duyệt.')
        tab.wait_for_timeout(1000)
        if 'professional_dashboard' not in tab.url:
            raise PageUnreadable('Facebook không mở trang Khán giả (đang ở '+tab.url[:80]+').')
        value = parse_followers_text(tab.evaluate('document.body.innerText'))
        if value is not None:
            return value
        if time.monotonic() >= deadline:
            raise PageUnreadable('Chưa thấy số người theo dõi sau %d giây.' % WAIT_SECONDS)


def collect(context, data_dir, account_id, page_keys, range_keys, *, switch=False, cancel=None, finished=None, progress=None, today=None,
            followers=False):
    """Read `range_keys` for each Page and save what verified. Never raises for one Page or range; only a stop request ends it.
    switch=False (after a post): the Chrome identity must already be that Page. switch=True (owner pressed the button): Tèo may switch to
    the Page the way it does when opening one. Returns dict(saved=[(page name, range key)], problems=[text])."""
    from teodub.facebook_page_identity import ensure_page_actor, verify_page_actor
    from teodub.page_activity import PageActivity
    store = PageInsights(data_dir)
    out = dict(saved=[], problems=[])
    specs, skipped = [], []
    for key in range_keys:
        try:
            specs.append(parse_range(key, today))
        except ValueError as exc:
            skipped.append(str(exc))
    out['problems'] += skipped
    for page_key in page_keys:
        try:
            target = PageActivity(data_dir).page(account_id, page_key)
        except (ValueError, OSError) as exc:
            out['problems'].append(str(exc))
            continue
        name = target['name']
        try:
            check_cancel(cancel)
            if progress:
                progress('Đang kiểm tra danh tính Page '+name+' để lấy số liệu…')
            if switch:
                first = context.new_page()
                try:
                    first.goto(target['url'], wait_until='domcontentloaded', timeout=45000)
                    ensure_page_actor(context, first, target, cancel=cancel, finished=finished)
                finally:
                    try:
                        first.close()
                    except Exception:
                        pass
            else:
                verify_page_actor(context, target, cancel=cancel, finished=finished)
            tab = context.new_page()
            gave_up = False
            try:
                for number, spec in enumerate(specs):
                    check_cancel(cancel)
                    if progress:
                        progress('Đang lấy số liệu '+spec['name'].lower()+' của '+name+'…')
                    try:
                        label, metrics = read_range(tab, spec, cancel=cancel, finished=finished)
                    except Cancelled:
                        raise
                    except PageUnreadable as exc:
                        if number == 0:  # Its first range cannot be read: the others would fail the same way, so do not wait for them.
                            out['problems'].append(name+': '+str(exc)[:200]+' Bỏ qua các khoảng còn lại của Page này.')
                            gave_up = True
                            break
                        out['problems'].append(name+' · '+spec['name']+': '+str(exc)[:160])
                        continue
                    except Exception as exc:
                        out['problems'].append(name+' · '+spec['name']+': '+(str(exc) or type(exc).__name__)[:160])
                        continue
                    store.save(account_id, page_key, spec['key'], label, metrics)
                    out['saved'].append((name, spec['key']))
                if followers and not gave_up:  # 21/11/2026: the follower total of the Audience page, once per Page.
                    check_cancel(cancel)
                    if progress:
                        progress('Đang lấy số người theo dõi của '+name+'…')
                    try:
                        store.save_followers(account_id, page_key, read_followers(tab, cancel=cancel, finished=finished))
                        out['saved'].append((name, 'followers'))
                    except Cancelled:
                        raise
                    except Exception as exc:
                        out['problems'].append(name+' · Người theo dõi: '+(str(exc) or type(exc).__name__)[:160])
            finally:
                try:
                    tab.close()
                except Exception:
                    pass
        except Cancelled:
            raise
        except Exception as exc:
            out['problems'].append(name+': '+(str(exc) or type(exc).__name__)[:200])
    return out
