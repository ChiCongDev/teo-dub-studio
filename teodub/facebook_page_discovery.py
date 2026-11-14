"""Read only managed-Page cards in the visible DOM. No private API, cookies or tokens."""
import re
import time
from urllib.parse import parse_qs, urlsplit

from teodub.common import Cancelled, check_cancel
from teodub.facebook_accounts import FACEBOOK_PAGES, facebook_url


# Evidence for the layout: user's screenshot of /pages/?category=your_pages,
# heading 'Trang bạn quản lý', named cards with 'Tạo bài viết'/'Quảng cáo'.
# The heading of the list: "Trang bạn quản lý" / "Pages you manage" / "Your Pages", and since 31/10/2026 "Pages <name> manages"
# (Facebook also shows "Your Pages" in the left menu, which holds no cards). The heading chosen is the one whose area has
# the most "Create post" buttons; the last one when none has any.
_HEADING_HELPERS = r"""
  const norm = s => (s || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '')
    .replace(/đ/g, 'd').replace(/Đ/g, 'D').replace(/\s+/g, ' ').trim().toLowerCase();
  const visible = e => !!e && !!e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden';
  const text = e => (e.innerText || e.getAttribute('aria-label') || '').replace(/\s+/g, ' ').trim();
  const mainOf = h => h.closest('[role="main"],main') || h.parentElement.parentElement;
  const createCount = m => [...m.querySelectorAll('a,[role="button"],button')].filter(visible)
    .filter(e => /^(tao bai viet|create post|create a post)$/.test(norm(text(e)))).length;
  const pickHeading = () => {
    const found = [...document.querySelectorAll('h1,h2,h3,[role="heading"],span')].filter(e => visible(e)
      && /^(trang ban quan ly|pages you manage|your pages|pages .{1,80} manages|trang .{1,80} quan ly)$/.test(norm(text(e))));
    let best = null, bestCount = -1;
    for (const h of found) {
      const count = h.parentElement && h.parentElement.parentElement ? createCount(mainOf(h)) : -1;
      if (count >= bestCount) { best = h; bestCount = count; }
    }
    return best;
  };
"""

SNAPSHOT_SCRIPT = "() => {" + _HEADING_HELPERS + r"""
  const actionName = s => /^(tao bai viet|create post|create a post|quang cao|advertise|promote|quang ba|(?:\d+ )?thong bao|(?:\d+ )?notifications|tin nhan|messages|\.{3}|…)$/.test(norm(s));
  const isButton = s => {
    const n = norm(String(s).replace(/[•·●○∙]/g, ' '));
    return actionName(n) || (n.length < 40 && /(thong bao|notification|tin nhan|message)/.test(n));
  };
  const numericAddress = a => {
    try { return /^\/\d{5,}\/?$/.test(new URL(a.getAttribute('href'), location.href).pathname); } catch { return false; }
  };
  const heading = pickHeading();
  const auth = !![...document.querySelectorAll('input[type="password"],form[action*="login"]')].find(visible);
  if (!heading) return {managed:false, auth, items:[], busy:false, bottom:false};
  const main = mainOf(heading);
  const excluded = new Set(['pages','login','login.php','checkpoint','recover','friends','me','settings',
    'messages','notifications','groups','watch','marketplace','gaming','reel','reels','ads','ad_center',
    'business','help','privacy','policies','bookmarks','home.php','photo','photo.php','stories','profile_switcher','people']);
  const candidate = a => {
    let u; try { u = new URL(a.getAttribute('href'), location.href); } catch { return null; }
    if (!['facebook.com','www.facebook.com','m.facebook.com','web.facebook.com'].includes(u.hostname)
        || !['http:','https:'].includes(u.protocol) || u.username || u.password) return null;
    const path = u.pathname.replace(/\/$/, '');
    const bits = path.split('/').filter(Boolean);
    let id = '', url = '';
    if (path === '/profile.php' && /^\d+$/.test(u.searchParams.get('id') || '')) {
      id = u.searchParams.get('id'); url = 'https://www.facebook.com/profile.php?id=' + id;
    } else if (bits.length === 1 && !excluded.has(bits[0].toLowerCase()) && !bits[0].endsWith('.php')) {
      id = /^\d+$/.test(bits[0]) ? bits[0] : ''; url = 'https://www.facebook.com/' + bits[0];
    } else if (bits.length === 3 && (bits[0] === 'pages' || bits[0] === 'people') && /^\d+$/.test(bits[2])) {
      id = bits[2]; url = 'https://www.facebook.com' + path;
    } else return null;
    return {url, page_id:id};
  };
  const items = new Map();
  const debug = [];
  for (const link of main.querySelectorAll('a[href]')) {
    if (!visible(link)) continue;
    const found = candidate(link);
    if (!found) continue;
    const name = text(link);
    if (!name || name.length > 150 || actionName(name)) {
      debug.push({text: (name || '').slice(0, 60), url: found.url, outcome: 'bỏ qua: tên rỗng, quá dài hoặc là nút'});
      continue;
    }
    let outcome = 'không thấy thẻ có nút Tạo bài viết và Quảng cáo/Thông báo quanh link này';
    let card = link.parentElement;
    for (let depth = 0; card && depth < 9 && card !== main; depth++, card = card.parentElement) {
      // Never borrow actions from a sibling card or the whole managed list.
      if (card.contains(heading)) { outcome = 'chạm tiêu đề danh sách trước khi thấy thẻ'; break; }
      const actions = [...card.querySelectorAll('a,[role="button"],button')].filter(visible).map(e => norm(text(e)));
      const post = actions.some(s => /^(tao bai viet|create post|create a post)$/.test(s));
      const secondary = actions.some(s => /^(quang cao|advertise|promote|quang ba|thong bao|\d+ thong bao|notifications|messages|tin nhan)$/.test(s));
      if (!post || !secondary) {
        if (card.matches('article,[role="article"]')) { outcome = 'gặp khối article trước khi thấy đủ nút'; break; }
        continue;
      }
      // Owner 11/11/2026 (log of the other machine, Page identity). Two things made good cards look like they belonged to another Page:
      // "Tạo bài viết" is there a link to an address made only of numbers (not another Page: an action never names a Page), and the
      // notification link reads "1 thông báo •" (a dot after the count). Both are ignored. Everything else is as before: a button that
      // points to a named Page, a texted link to another Page, or several Pages in one block still stop the card.
      const links = [...card.querySelectorAll('a[href]')].filter(visible);
      const ids = new Set(links.filter(a => !(isButton(text(a)) && numericAddress(a))).map(candidate).filter(Boolean).map(x => x.url));
      const shown = a => (a.innerText || '').replace(/\s+/g, ' ').trim();  // Not the label of a picture (aria-label).
      const sameCard = links.every(a => !shown(a) || isButton(shown(a)) || candidate(a)?.url === found.url);
      if (sameCard && ids.size === 1 && ids.has(found.url)) { items.set(found.url, {name, ...found}); outcome = 'nhận'; }
      else outcome = 'thẻ bị từ chối: link Page trong thẻ: ' + [...ids].join(' | ') + '; link có chữ lạ: '
        + links.filter(a => shown(a) && !isButton(shown(a)) && candidate(a)?.url !== found.url)
               .map(a => shown(a).slice(0, 30) + ' -> ' + (candidate(a)?.url || '(không phải Page)')).slice(0, 6).join(' / ');
      break;
    }
    debug.push({text: name.slice(0, 60), url: found.url, outcome});
  }
  let scroller = heading.parentElement;
  while (scroller && scroller !== document.body) {
    const style = getComputedStyle(scroller);
    if (/(auto|scroll)/.test(style.overflowY) && scroller.scrollHeight > scroller.clientHeight + 10) break;
    scroller = scroller.parentElement;
  }
  scroller = scroller && scroller !== document.body ? scroller : document.scrollingElement;
  return {managed:true, auth, items:[...items.values()], heading:text(heading).slice(0, 80), debug:debug.slice(0, 80),
    busy:!![...main.querySelectorAll('[role="progressbar"],[aria-busy="true"]')].find(visible),
    bottom:scroller.scrollTop + scroller.clientHeight >= scroller.scrollHeight - 12};
}"""

SCROLL_SCRIPT = "() => {" + _HEADING_HELPERS + r"""
  const heading = pickHeading();
  if (!heading) return;
  let node = heading.parentElement;
  while (node && node !== document.body) {
    if (/(auto|scroll)/.test(getComputedStyle(node).overflowY) && node.scrollHeight > node.clientHeight + 10) break;
    node = node.parentElement;
  }
  node = node && node !== document.body ? node : document.scrollingElement;
  node.scrollBy(0, Math.max(450, node.clientHeight * 0.8));
}"""


def _list_url(url):
    parts = urlsplit(url)
    return (parts.hostname in {'facebook.com', 'www.facebook.com', 'm.facebook.com', 'web.facebook.com'}
            and parts.path.rstrip('/') == '/pages'
            and parse_qs(parts.query).get('category') == ['your_pages'])


def normalize_items(items):
    rows = {}
    for item in items:
        name = re.sub(r'\s+', ' ', str(item.get('name', ''))).strip()
        if not name or len(name) > 150:
            continue
        try:
            url = facebook_url(item['url'], page=True)
        except (ValueError, KeyError):
            continue
        parts = urlsplit(url)
        bits = parts.path.strip('/').split('/')
        if (parts.path != '/profile.php' and not (len(bits) == 1 and bits[0]
                and bits[0].lower() not in {'pages', 'people', 'friends', 'me', 'groups', 'messages', 'notifications',
                                          'settings', 'watch', 'marketplace', 'help', 'reels', 'ads'}
                and not bits[0].endswith('.php'))
                and not (len(bits) == 3 and bits[0] in ('pages', 'people') and bits[-1].isdigit())):
            continue
        page_id = parse_qs(parts.query).get('id', [''])[0] if parts.path == '/profile.php' else ''
        if not page_id and bits[-1].isdigit():
            page_id = bits[-1]
        key = page_id or url.casefold()
        rows[key] = dict(name=name, url=url, page_id=page_id)
    return list(rows.values())


# Owner 11/11/2026: a Page that has a username is listed with a text link (facebook.com/name) and no number, so Tèo could not tell it
# was a Page it already knew (renamed on Facebook) and added a second row. Reading the number from the Page itself found nothing on the
# real Facebook (11/11 09:08). What does work (checked by the owner the same day): the NUMBER link of a saved Page,
# facebook.com/profile.php?id=NUMBER, is sent by Facebook to that Page's text link. So for every saved Page of this account that the
# list did not show this time, Tèo opens its number link once and looks where Facebook lands: when it lands on a text link of the
# list, that row is that saved Page.
MAX_ID_LOOKUPS = 12
REDIRECT_WAIT_MS = 6000


def _first_segment(url):
    """'reviewsaithatnoithat' for https://www.facebook.com/reviewsaithatnoithat/ (lower case); '' for anything else."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return ''
    if parts.hostname not in {'facebook.com', 'www.facebook.com', 'm.facebook.com', 'web.facebook.com'}:
        return ''
    bits = [bit for bit in parts.path.split('/') if bit]
    if len(bits) != 1 or bits[0].endswith('.php') or bits[0].isdigit():
        return ''
    return bits[0].casefold()


def resolve_page_ids(context, rows, known=None, *, cancel=None, finished=None, progress=None, trace=None):
    """Give a number to the rows that only have a text link, using the saved Pages (`known`: [{page_id, url}]) the list did not show.
    Never raises for a Page that cannot be opened (the row stays without a number, exactly as before); only a stop request ends it.
    A row gets a number only when exactly one saved Page lands on it."""
    waiting = {_first_segment(row['url']): row for row in rows if not row.get('page_id') and _first_segment(row['url'])}
    shown = {row['page_id'] for row in rows if row.get('page_id')}
    candidates = []
    for page in known or []:
        number = str((page or {}).get('page_id', '')) if isinstance(page, dict) else ''
        if re.fullmatch(r'[0-9]{5,25}', number) and number not in shown and number not in candidates:
            candidates.append(number)
    candidates = candidates[:MAX_ID_LOOKUPS]
    if trace is not None:
        trace['waiting'] = sorted(waiting)
        trace['candidates'] = list(candidates)
        trace['known'] = len(known or [])
    if not waiting or not candidates:
        return rows
    landed = {}
    tab = None
    try:
        try:
            tab = context.new_page()
        except Exception:  # No spare tab: keep the scan as it is.
            return rows
        for count, number in enumerate(candidates, 1):
            check_cancel(cancel)
            if finished is not None and finished.is_set():
                raise Cancelled('Đã dừng Load Page để đóng trình duyệt. Danh sách đã lưu trước đó được giữ.')
            if progress:
                progress(f'Đang kiểm tra Page đã lưu có đổi tên/đổi link không ({count}/{len(candidates)})…')
            try:
                tab.goto('https://www.facebook.com/profile.php?id='+number, wait_until='domcontentloaded', timeout=25000)
                segment = _first_segment(tab.url)
                if trace is not None:
                    trace.setdefault('probes', []).append(f'link số {number}: Facebook mở tới {str(tab.url)[:120]}')
                waited = 0
                while not segment and waited < REDIRECT_WAIT_MS:  # Facebook may change the address after the page loads.
                    tab.wait_for_timeout(500)
                    waited += 500
                    segment = _first_segment(tab.url)
            except Cancelled:
                raise
            except Exception as exc:  # A Page that will not open is left as it was.
                if trace is not None:
                    trace.setdefault('probes', []).append(f'link số {number}: không mở được ({str(exc)[:100]})')
                continue
            if segment in waiting:
                landed.setdefault(segment, []).append(number)
    finally:
        if tab is not None:
            try:
                tab.close()
            except Exception:
                pass
    for segment, numbers in landed.items():
        if len(numbers) == 1:
            waiting[segment]['page_id'] = numbers[0]
    return rows


def _read_managed_pages(context, trace, *, cancel=None, finished=None, progress=None, timeout=180, limit=1000, known=None):
    """Only read the known Pages-you-manage view; leave auth/verification to user."""
    from playwright._impl._errors import Error, TargetClosedError
    if not 1 <= limit <= 1000 or timeout <= 0:
        raise ValueError('Giới hạn đọc Page hoặc thời gian chờ không hợp lệ.')
    check_cancel(cancel)
    if finished is not None and finished.is_set():
        raise Cancelled('Đã dừng Load Page để đóng trình duyệt.')
    rows = {}
    started = changed = time.monotonic()
    last_message = ''
    def notify(message):
        nonlocal last_message
        if progress and message != last_message:
            progress(message)
        last_message = message
    try:
        # Use a dedicated tab so a refresh never destroys an unfinished post/form.
        tab = context.new_page()
        tab.goto(FACEBOOK_PAGES, wait_until='domcontentloaded', timeout=45000)
        tab.bring_to_front()
        while time.monotonic() - started < timeout:
            check_cancel(cancel)
            if finished is not None and finished.is_set():
                raise Cancelled('Đã dừng Load Page để đóng trình duyệt. Danh sách đã lưu trước đó được giữ.')
            parts = urlsplit(tab.url)
            if parts.hostname not in {'facebook.com', 'www.facebook.com', 'm.facebook.com', 'web.facebook.com'}:
                raise ValueError('Trang đọc Page chuyển sang địa chỉ khác Facebook; chưa lưu danh sách.')
            if parts.path.startswith(('/login', '/checkpoint', '/recover')) or not _list_url(tab.url):
                notify('Chưa ở Trang bạn quản lý. Bạn tự đăng nhập/xác minh hoặc mở danh sách Page trong '
                       'tab Load Page này; Tèo chờ tối đa 3 phút.')
                tab.wait_for_timeout(500)
                continue
            snapshot = tab.evaluate(SNAPSHOT_SCRIPT)
            trace['snapshots'] = trace.get('snapshots', 0)+1
            if isinstance(snapshot, dict):
                trace['heading'] = snapshot.get('heading', trace.get('heading'))
                for entry in snapshot.get('debug') or []:
                    if isinstance(entry, dict):
                        trace.setdefault('debug', {})[(entry.get('url'), entry.get('outcome'))] = entry
            if snapshot.get('auth'):
                notify('Bạn tự đăng nhập/xác minh Facebook trong Chrome; Tèo chờ tối đa 3 phút để đọc Page.')
                tab.wait_for_timeout(500)
                continue
            if not snapshot.get('managed'):
                notify('Đang chờ màn hình Trang bạn quản lý. Nếu giao diện khác ảnh mẫu, danh sách cũ sẽ được giữ.')
                tab.wait_for_timeout(500)
                continue
            found = normalize_items(snapshot.get('items', []))
            before = len(rows)
            for row in found:
                rows[row['page_id'] or row['url'].casefold()] = row
                if len(rows) >= limit:
                    return dict(items=resolve_page_ids(context, list(rows.values()), known, cancel=cancel, finished=finished, progress=progress, trace=trace),
                        status='partial', message=f'Đã đọc {len(rows)} Page; tới giới hạn của lượt này. Giữ mọi Page cũ.')
            if len(rows) != before or snapshot.get('busy') or not snapshot.get('bottom'):
                changed = time.monotonic()
            notify(f'Đã đọc {len(rows)} Page trên Trang bạn quản lý; đang kiểm tra danh sách tải thêm…')
            if snapshot.get('bottom') and not snapshot.get('busy') and time.monotonic() - changed >= 6:
                if not rows:
                    raise ValueError('Chưa đọc được thẻ Page hợp lệ trên Trang bạn quản lý. '
                                     'Có thể tài khoản chưa có Page hoặc giao diện đã đổi; danh sách cũ được giữ.')
                return dict(items=resolve_page_ids(context, list(rows.values()), known, cancel=cancel, finished=finished, progress=progress, trace=trace),
                    status='observed', message=f'Đã đọc {len(rows)} Page từ danh sách đang hiển thị và cuộn tới cuối phần đã tải. '
                            'Chưa xác minh tổng Page hoặc quyền API.')
            tab.evaluate(SCROLL_SCRIPT)
            tab.wait_for_timeout(500)
        if rows:
            return dict(items=resolve_page_ids(context, list(rows.values()), known, cancel=cancel, finished=finished, progress=progress, trace=trace),
                status='partial', message=f'Hết thời gian chờ; đã đọc {len(rows)} Page. Có thể chưa đủ, giữ mọi Page cũ.')
        raise ValueError('Hết 3 phút nhưng chưa đọc được Page. Kiểm tra đăng nhập và màn hình Trang bạn quản lý, '
                         'sau đó chọn Load Page lại. Danh sách cũ được giữ.')
    except TargetClosedError:
        raise ValueError('Tab hoặc cửa sổ Load Page đã đóng trước khi đọc xong; danh sách cũ được giữ.') from None
    except Error as exc:
        raise ValueError('Chưa đọc được Page do trang/trình duyệt lỗi. Danh sách cũ được giữ. Chi tiết: '+str(exc)) from None


def scan_log(trace, items):
    """The lines that say what a Load Page saw (owner 11/11/2026: so a Page that is missed, or not recognised, can be found out from
    what Facebook really showed). Only addresses, names and the reasons; nothing private."""
    lines = [f'Tiêu đề danh sách: {trace.get("heading")!r} · số lần đọc màn hình: {trace.get("snapshots", 0)}']
    entries = list((trace.get('debug') or {}).values())
    lines.append(f'Link Tèo xem là Page ({len(entries)}):')
    lines += [f'  - {entry.get("text")!r} | {entry.get("url")} | {entry.get("outcome")}' for entry in entries]
    lines.append(f'Page Tèo giữ lại từ lượt đọc ({len(items)}):')
    lines += [f'  - {row["name"]!r} | ID {row.get("page_id") or "(chưa có)"} | {row["url"]}' for row in items]
    lines.append(f'Page đã lưu có ID: {trace.get("known", 0)} · hàng chỉ có link chữ: {trace.get("waiting", [])} · '
                 f'Page đã lưu không thấy trong danh sách (sẽ mở link số): {trace.get("candidates", [])}')
    lines += ['  ' + line for line in trace.get('probes', [])]
    return lines


def read_managed_pages(context, **kwargs):
    """Same as before, and the result also carries `log`: what the page showed and what Tèo made of it."""
    trace = {}
    result = _read_managed_pages(context, trace, **kwargs)
    try:
        result['log'] = scan_log(trace, result.get('items', []))
    except Exception:  # The log never changes the result.
        pass
    return result
