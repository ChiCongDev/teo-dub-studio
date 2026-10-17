"""Read only managed-Page cards in the visible DOM. No private API, cookies or tokens."""
import re
import time
from urllib.parse import parse_qs, urlsplit

from teodub.common import Cancelled, check_cancel
from teodub.facebook_accounts import FACEBOOK_PAGES, facebook_url


# Evidence for the layout: user's screenshot of /pages/?category=your_pages,
# heading 'Trang bạn quản lý', named cards with 'Tạo bài viết'/'Quảng cáo'.
SNAPSHOT_SCRIPT = r"""() => {
  const norm = s => (s || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '')
    .replace(/đ/g, 'd').replace(/Đ/g, 'D').replace(/\s+/g, ' ').trim().toLowerCase();
  const visible = e => !!e && !!e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden';
  const text = e => (e.innerText || e.getAttribute('aria-label') || '').replace(/\s+/g, ' ').trim();
  const actionName = s => /^(tao bai viet|create post|create a post|quang cao|advertise|(?:\d+ )?thong bao|(?:\d+ )?notifications|tin nhan|messages|\.{3}|…)$/.test(norm(s));
  const headings = [...document.querySelectorAll('h1,h2,h3,[role="heading"],span')];
  const heading = headings.find(e => visible(e) && /^(trang ban quan ly|pages you manage|your pages)$/.test(norm(text(e))));
  const auth = !![...document.querySelectorAll('input[type="password"],form[action*="login"]')].find(visible);
  if (!heading) return {managed:false, auth, items:[], busy:false, bottom:false};
  const main = heading.closest('[role="main"],main') || heading.parentElement.parentElement;
  const excluded = new Set(['pages','login','login.php','checkpoint','recover','friends','me','settings',
    'messages','notifications','groups','watch','marketplace','gaming','reel','reels','ads','ad_center',
    'business','help','privacy','policies','bookmarks','home.php','photo','photo.php','stories','profile_switcher']);
  const candidate = a => {
    let u; try { u = new URL(a.getAttribute('href'), location.href); } catch { return null; }
    if (!['facebook.com','www.facebook.com','m.facebook.com'].includes(u.hostname)
        || !['http:','https:'].includes(u.protocol) || u.username || u.password) return null;
    const path = u.pathname.replace(/\/$/, '');
    const bits = path.split('/').filter(Boolean);
    let id = '', url = '';
    if (path === '/profile.php' && /^\d+$/.test(u.searchParams.get('id') || '')) {
      id = u.searchParams.get('id'); url = 'https://www.facebook.com/profile.php?id=' + id;
    } else if (bits.length === 1 && !excluded.has(bits[0].toLowerCase()) && !bits[0].endsWith('.php')) {
      id = /^\d+$/.test(bits[0]) ? bits[0] : ''; url = 'https://www.facebook.com/' + bits[0];
    } else if (bits.length === 3 && bits[0] === 'pages' && /^\d+$/.test(bits[2])) {
      id = bits[2]; url = 'https://www.facebook.com' + path;
    } else return null;
    return {url, page_id:id};
  };
  const items = new Map();
  for (const link of main.querySelectorAll('a[href]')) {
    if (!visible(link)) continue;
    const found = candidate(link);
    if (!found) continue;
    const name = text(link);
    if (!name || name.length > 150 || actionName(name)) continue;
    let card = link.parentElement;
    for (let depth = 0; card && depth < 9 && card !== main; depth++, card = card.parentElement) {
      // Never borrow actions from a sibling card or the whole managed list.
      if (card.contains(heading)) break;
      const actions = [...card.querySelectorAll('a,[role="button"],button')].filter(visible).map(e => norm(text(e)));
      const post = actions.some(s => /^(tao bai viet|create post|create a post)$/.test(s));
      const secondary = actions.some(s => /^(quang cao|advertise|thong bao|\d+ thong bao|notifications|messages|tin nhan)$/.test(s));
      if (!post || !secondary) {
        if (card.matches('article,[role="article"]')) break;
        continue;
      }
      const links = [...card.querySelectorAll('a[href]')].filter(visible);
      const ids = new Set(links.map(candidate).filter(Boolean).map(x => x.url));
      const sameCard = links.every(a => !text(a) || actionName(text(a)) || candidate(a)?.url === found.url);
      if (sameCard && ids.size === 1 && ids.has(found.url)) items.set(found.url, {name, ...found});
      break;
    }
  }
  let scroller = heading.parentElement;
  while (scroller && scroller !== document.body) {
    const style = getComputedStyle(scroller);
    if (/(auto|scroll)/.test(style.overflowY) && scroller.scrollHeight > scroller.clientHeight + 10) break;
    scroller = scroller.parentElement;
  }
  scroller = scroller && scroller !== document.body ? scroller : document.scrollingElement;
  return {managed:true, auth, items:[...items.values()],
    busy:!![...main.querySelectorAll('[role="progressbar"],[aria-busy="true"]')].find(visible),
    bottom:scroller.scrollTop + scroller.clientHeight >= scroller.scrollHeight - 12};
}"""

SCROLL_SCRIPT = r"""() => {
  const norm = s => (s || '').normalize('NFD').replace(/[\u0300-\u036f]/g,'').replace(/đ/g,'d').trim().toLowerCase();
  const heading = [...document.querySelectorAll('h1,h2,h3,[role="heading"],span')].find(e =>
    e.getClientRects().length && /^(trang ban quan ly|pages you manage|your pages)$/.test(norm(e.innerText)));
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
    return (parts.hostname in {'facebook.com', 'www.facebook.com', 'm.facebook.com'}
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
                and bits[0].lower() not in {'pages', 'friends', 'me', 'groups', 'messages', 'notifications',
                                          'settings', 'watch', 'marketplace', 'help', 'reels', 'ads'}
                and not bits[0].endswith('.php'))
                and not (len(bits) == 3 and bits[0] == 'pages' and bits[-1].isdigit())):
            continue
        page_id = parse_qs(parts.query).get('id', [''])[0] if parts.path == '/profile.php' else ''
        if not page_id and bits[-1].isdigit():
            page_id = bits[-1]
        key = page_id or url.casefold()
        rows[key] = dict(name=name, url=url, page_id=page_id)
    return list(rows.values())


def read_managed_pages(context, *, cancel=None, finished=None, progress=None, timeout=180, limit=1000):
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
            if parts.hostname not in {'facebook.com', 'www.facebook.com', 'm.facebook.com'}:
                raise ValueError('Trang đọc Page chuyển sang địa chỉ khác Facebook; chưa lưu danh sách.')
            if parts.path.startswith(('/login', '/checkpoint', '/recover')) or not _list_url(tab.url):
                notify('Chưa ở Trang bạn quản lý. Bạn tự đăng nhập/xác minh hoặc mở danh sách Page trong '
                       'tab Load Page này; Tèo chờ tối đa 3 phút.')
                tab.wait_for_timeout(500)
                continue
            snapshot = tab.evaluate(SNAPSHOT_SCRIPT)
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
                    return dict(items=list(rows.values()), status='partial',
                        message=f'Đã đọc {len(rows)} Page; tới giới hạn của lượt này. Giữ mọi Page cũ.')
            if len(rows) != before or snapshot.get('busy') or not snapshot.get('bottom'):
                changed = time.monotonic()
            notify(f'Đã đọc {len(rows)} Page trên Trang bạn quản lý; đang kiểm tra danh sách tải thêm…')
            if snapshot.get('bottom') and not snapshot.get('busy') and time.monotonic() - changed >= 6:
                if not rows:
                    raise ValueError('Chưa đọc được thẻ Page hợp lệ trên Trang bạn quản lý. '
                                     'Có thể tài khoản chưa có Page hoặc giao diện đã đổi; danh sách cũ được giữ.')
                return dict(items=list(rows.values()), status='observed',
                    message=f'Đã đọc {len(rows)} Page từ danh sách đang hiển thị và cuộn tới cuối phần đã tải. '
                            'Chưa xác minh tổng Page hoặc quyền API.')
            tab.evaluate(SCROLL_SCRIPT)
            tab.wait_for_timeout(500)
        if rows:
            return dict(items=list(rows.values()), status='partial',
                message=f'Hết thời gian chờ; đã đọc {len(rows)} Page. Có thể chưa đủ, giữ mọi Page cũ.')
        raise ValueError('Hết 3 phút nhưng chưa đọc được Page. Kiểm tra đăng nhập và màn hình Trang bạn quản lý, '
                         'sau đó chọn Load Page lại. Danh sách cũ được giữ.')
    except TargetClosedError:
        raise ValueError('Tab hoặc cửa sổ Load Page đã đóng trước khi đọc xong; danh sách cũ được giữ.') from None
    except Error as exc:
        raise ValueError('Chưa đọc được Page do trang/trình duyệt lỗi. Danh sách cũ được giữ. Chi tiết: '+str(exc)) from None
