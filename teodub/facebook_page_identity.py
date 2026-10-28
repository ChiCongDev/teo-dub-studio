"""Switch the visible Facebook profile selector, then verify the chosen actor.

Only the user's account Chrome is used. No cookies, hidden state, endpoints or
profile IDs are read; the final composer must independently verify the Page ID.
Observed Facebook layout: Your profile -> See all profiles -> Choose profile,
with named buttons and an explicit ', selected' / ', đang chọn' marker. Read on 24/10/2026 (English UI):
"Account, your profile" -> dialog "Your profile" -> See all profiles -> "Select profile" with
"<Page>, currently selected" and "<Page>, 3 notifications".
26/10/2026: choosing a profile makes Facebook replace the document; a read during that reload failed the
post ("Execution context was destroyed"), and an old tab that no longer answered held the account 20 minutes.
Reads here wait for the new document within their deadline, and other tabs are checked with a time limit.
"""
import re
import time
import unicodedata
from urllib.parse import parse_qs, urlsplit

from teodub.common import Cancelled, check_cancel
from teodub.facebook_accounts import facebook_url

# 24/10/2026: the account button reads "Account, your profile" (its menu is still the dialog "Your profile").
PROFILE_NAMES = re.compile(r'^(Trang cá nhân của bạn|Your profile|Tài khoản|Account'
                           r'|Account, your profile|Tài khoản, trang cá nhân của bạn)$', re.I)
ALL_NAMES = re.compile(r'^(Xem tất cả trang cá nhân|See all profiles)$', re.I)
CHOOSER_NAMES = re.compile(r'^(Chọn trang cá nhân|Choose a profile|Select profile|Select a profile)$', re.I)
CREATE_NAMES = re.compile(r'^(Tạo bài viết|Create post|Create a post)$', re.I)
EDIT_NAMES = re.compile(r'^(Tạo bài viết|Create post|Create a post|Tạo thước phim|Create reel|Tạo video|Create video|Chỉnh sửa bài viết|Edit post)$', re.I)
SELECTED = re.compile(r',\s*(đang chọn|đã chọn|selected|currently selected)$', re.I)
NOTIFICATIONS = re.compile(r',\s*\d+\s+(thông báo|notifications?)$', re.I)
HOSTS = {'www.facebook.com', 'facebook.com', 'm.facebook.com'}
TAB_CHECK_SECONDS = 3  # Each other tab gets this long to answer before a switch is refused.
SWITCH_RELOAD_SECONDS = 5  # After choosing a profile, how long to look for Facebook's reload before reading.

CHOICES_SCRIPT = r"""dialog => {
 const visible=e=>!!e.getClientRects().length && getComputedStyle(e).visibility!=='hidden'
   && !e.closest('[aria-hidden="true"],[inert]');
 const buttons=[...dialog.querySelectorAll('button,[role="button"],[role="radio"]')];
 return buttons.map((e,index)=>({index,label:e.getAttribute('aria-label')||'',
   text:(e.innerText||'').trim(),href:e.getAttribute('href') ? new URL(e.getAttribute('href'),location.href).href : '',
   checked:e.getAttribute('aria-checked'),current:e.getAttribute('aria-current')}))
   .filter((x,index)=>visible(buttons[index]) && (x.label||x.text));
}"""


def _name(value):
    return ' '.join(unicodedata.normalize('NFC', str(value)).split()).casefold()


def _one(locator, label):
    rows = [locator.nth(i) for i in range(locator.count()) if locator.nth(i).is_visible()]
    if len(rows) != 1:
        raise ValueError(f'Không xác định duy nhất {label} ({len(rows)} mục). Kiểm tra menu Facebook; chưa nhập hoặc đăng bài.')
    return rows[0]


def _stop(cancel, finished):
    check_cancel(cancel)
    if finished is not None and finished.is_set():
        raise Cancelled('Đã dừng chuyển Page để đóng Chrome.')


def _safe(tab):
    parsed = urlsplit(tab.url)
    if parsed.hostname not in HOSTS or parsed.path.startswith(('/login', '/checkpoint', '/recover', '/two_step_verification')):
        raise ValueError('Facebook yêu cầu đăng nhập/xác minh hoặc đã chuyển khỏi Facebook. Bạn xử lý trong Chrome của Tèo.')
    if any(tab.locator('input[type="password"]').nth(i).is_visible()
           for i in range(tab.locator('input[type="password"]').count())):
        raise ValueError('Chrome chưa đăng nhập Facebook; đăng nhập tài khoản rồi mở Page lại.')
    from teodub.facebook_web_post import AUTH_SCRIPT
    auth = tab.evaluate(AUTH_SCRIPT)
    if auth.get('auth') or auth.get('blocked'):
        raise ValueError('Facebook yêu cầu đăng nhập/xác minh. Bạn xử lý trực tiếp trong Chrome; Tèo chưa chuyển Page.')


def _navigation(error):
    from teodub.facebook_web_post import _navigation_interrupted
    return _navigation_interrupted(error)


def _crashed(error):
    from playwright.sync_api import Error
    return isinstance(error, Error) and 'Target crashed' in str(error)


def _reloading(tab, deadline):
    """Wait, within the caller's deadline, for the document Facebook is loading; never click anything."""
    remaining = deadline-time.monotonic()
    if remaining <= 0:
        return
    try:
        tab.wait_for_load_state('domcontentloaded', timeout=max(1, int(remaining*1000)))
    except Exception:
        pass  # The caller's next read reports a closed tab or its own deadline.


def _steady(tab, read, deadline):
    """Run a read-only check; if Facebook replaced the document meanwhile, read the new one until `deadline`."""
    while True:
        try:
            return read()
        except Exception as exc:
            if not _navigation(exc) or time.monotonic() >= deadline:
                raise
        _reloading(tab, deadline)


def _visible(locator):
    return [locator.nth(i) for i in range(locator.count()) if locator.nth(i).is_visible()]


def open_editor(tab, *, seconds=TAB_CHECK_SECONDS):
    """Whether `tab` shows an unfinished composer: True/False, or None when it does not answer within `seconds`.

    count()/is_visible() have no time limit of their own, so a bounded wait for the document comes first. A
    crashed or closed tab holds no editor any more.
    """
    from playwright.sync_api import Error, TimeoutError as PlaywrightTimeout
    deadline = time.monotonic()+seconds
    while True:
        remaining = deadline-time.monotonic()
        if remaining <= 0:
            return None
        try:
            tab.locator('html').wait_for(state='attached', timeout=max(1, int(remaining*1000)))
            dialogs = tab.get_by_role('dialog').filter(has=tab.get_by_role('heading', name=EDIT_NAMES))
            labelled = tab.get_by_role('dialog', name=EDIT_NAMES)
            return bool(_visible(dialogs) or _visible(labelled))
        except PlaywrightTimeout:
            return None
        except Error as exc:
            if tab.is_closed() or _crashed(exc):
                return False
            if not _navigation(exc):
                raise
        _reloading(tab, deadline)


def _choice(row):
    label = row['label'] or row['text'].split('\n')[0]
    selected = bool(SELECTED.search(label) or row.get('checked') == 'true' or row.get('current') in {'true', 'page'})
    label = NOTIFICATIONS.sub('', SELECTED.sub('', label))
    return dict(row, name=label.strip(), selected=selected)


def _selector(tab, *, cancel=None, finished=None, timeout=12):
    from playwright.sync_api import TimeoutError as PlaywrightTimeout
    deadline = time.monotonic()+timeout
    def checked(locator):
        _safe(tab)
        return _visible(locator)
    _stop(cancel, finished)
    if not _steady(tab, lambda: checked(tab.get_by_role('dialog', name=PROFILE_NAMES)), deadline):
        while True:
            _stop(cancel, finished)
            buttons = tab.get_by_role('button', name=PROFILE_NAMES)
            visible = _steady(tab, lambda: checked(buttons), deadline)
            if visible or time.monotonic() >= deadline:
                break
            tab.wait_for_timeout(100)
        _one(buttons, 'menu Trang cá nhân của bạn').click(timeout=5000)
    deadline = time.monotonic()+timeout
    opened_all = False
    while True:
        _stop(cancel, finished)
        visible = _steady(tab, lambda: checked(tab.get_by_role('dialog', name=PROFILE_NAMES)), deadline)
        if len(visible) > 1:
            raise ValueError('Facebook hiện nhiều menu tài khoản; chưa chuyển Page hoặc nhập bài.')
        if visible:
            dialog = visible[0]
            headings = dialog.get_by_role('heading', name=CHOOSER_NAMES)
            if _steady(tab, lambda: _visible(headings), deadline):
                try:
                    rows = [_choice(row) for row in _steady(tab, lambda: dialog.evaluate(CHOICES_SCRIPT,
                        timeout=max(1, int((deadline-time.monotonic())*1000))), deadline)]
                except PlaywrightTimeout:
                    rows = []  # The menu closed meanwhile; the deadline below decides.
                active = [row for row in rows if row['selected']]
                if len(active) == 1:
                    return dialog, rows, active[0]
                if len(active) > 1:
                    raise ValueError('Menu có nhiều danh tính đang chọn; chưa xác minh được người đăng.')
            elif not opened_all:
                buttons = dialog.get_by_role('button', name=ALL_NAMES)
                matches = _steady(tab, lambda: _visible(buttons), deadline)
                if len(matches) == 1:
                    _stop(cancel, finished)
                    matches[0].click(timeout=5000)
                    opened_all = True
                elif len(matches) > 1:
                    raise ValueError('Nhiều nút Xem tất cả trang cá nhân; chưa chuyển Page.')
        if time.monotonic() >= deadline:
            raise ValueError('Chưa đọc được danh tính đang chọn trong menu Facebook. Mở Trang cá nhân của bạn → Xem tất cả trang cá nhân để kiểm tra.')
        tab.wait_for_timeout(100)


def _can_switch(context):
    # An actor switch applies to the whole account, including other tabs. Keep
    # every unfinished editor intact instead of invalidating another draft.
    # Each tab answers within TAB_CHECK_SECONDS or the switch stops: on 26/10 one of 19 old tabs never
    # answered and held the account for 20 minutes. A crashed tab holds no editor and is skipped.
    for tab in context.pages:
        if tab.is_closed() or urlsplit(tab.url).hostname not in HOSTS:
            continue
        editor = open_editor(tab)
        if editor is None:
            raise ValueError(f'Một tab Facebook trong Chrome của Tèo không phản hồi sau {TAB_CHECK_SECONDS} giây '
                             '(thường do mở quá nhiều tab hoặc máy thiếu bộ nhớ). Tèo chưa chuyển Page, chưa nhập hoặc đăng bài; '
                             'đóng bớt tab Facebook hoặc đóng Chrome của Tèo rồi để lịch sau chạy tiếp.')
        if editor:
            raise ValueError('Tài khoản đang có hộp soạn bài chưa đóng. Hoàn tất hoặc đóng hộp đó trước khi chuyển sang Page khác; Tèo giữ nội dung đang soạn.')


def _matching_url(href, target):
    if not href:
        return
    url = facebook_url(href, page=True)
    parsed = urlsplit(url)
    value = parse_qs(parsed.query).get('id', [''])[0] if parsed.path == '/profile.php' else parsed.path.strip('/')
    if value.isdigit() and value != target['page_id'] or url != facebook_url(target['url'], page=True) and not value.isdigit():
        raise ValueError('Dòng chọn Page có link/Page ID khác cấu hình; chưa chuyển hoặc đăng bài.')


def ensure_page_actor(context, tab, target, *, cancel=None, finished=None, progress=None):
    """Return only after an explicit selected marker matches the saved Page.

    A matching title is necessary but not enough for publication: publisher
    additionally requires exact numeric Page ID in the visible author link.
    """
    if not re.fullmatch(r'[0-9]+', str(target.get('page_id', ''))) or not target.get('name'):
        raise ValueError('Lưu tên và Page ID bằng số trước khi chuyển danh tính Facebook.')
    facebook_url(target['url'], page=True)
    if progress:
        progress('Đang kiểm tra danh tính Facebook và chọn Page '+target['name']+'…')
    dialog, rows, active = _selector(tab, cancel=cancel, finished=finished)
    matches = [row for row in rows if _name(row['name']) == _name(target['name'])]
    if len(matches) != 1:
        raise ValueError('Không tìm thấy duy nhất Page “'+target['name']+'” trong danh sách chuyển trang cá nhân. '
                         'Kiểm tra đúng tài khoản/quyền quản lý hoặc tên Page trong Tèo; chưa nhập bài.')
    chosen = matches[0]
    _matching_url(chosen['href'], target)
    switched = not chosen['selected']
    if switched:
        _can_switch(context)
        _stop(cancel, finished)
        deadline = time.monotonic()+15
        # A new document has another time origin: that is how Facebook's reload after the choice is seen.
        origin = _steady(tab, lambda: tab.evaluate('performance.timeOrigin'), deadline)
        try:
            dialog.locator('button,[role="button"],[role="radio"]').nth(chosen['index']).click(timeout=5000)
        except Exception as exc:
            if not _navigation(exc):
                raise
            # The document was replaced after the choice went out: observe; NEVER choose a profile again.
        reload_until = time.monotonic()+SWITCH_RELOAD_SECONDS
        # Wait for Facebook to finish the switch, close its selector and (usually) reload the page, so the
        # verification below reads the new document. Do not re-click a profile if the network outcome is
        # still uncertain.
        def switching():
            _safe(tab)
            return (bool(_visible(tab.get_by_role('dialog', name=PROFILE_NAMES))),
                    tab.evaluate('performance.timeOrigin') != origin)
        while True:
            _stop(cancel, finished)
            menu_open, reloaded = _steady(tab, switching, deadline)
            if not menu_open and (reloaded or time.monotonic() >= reload_until):
                if reloaded:
                    _reloading(tab, deadline)
                break
            if time.monotonic() >= deadline:
                raise ValueError('Đã chọn Page nhưng Facebook chưa xác nhận chuyển xong. Kiểm tra Chrome; Tèo không bấm chọn lại.')
            tab.wait_for_timeout(100)
        dialog, rows, active = _selector(tab, cancel=cancel, finished=finished)
        matches = [row for row in rows if _name(row['name']) == _name(target['name'])]
        if len(matches) != 1 or not matches[0]['selected'] or _name(active['name']) != _name(target['name']):
            raise ValueError('Facebook chưa chuyển sang đúng Page sau lựa chọn. Giữ Chrome để kiểm tra; chưa nhập hoặc đăng bài.')
        _matching_url(matches[0]['href'], target)
    _stop(cancel, finished)
    _one(tab.get_by_role('button', name=PROFILE_NAMES), 'menu tài khoản sau khi chọn Page').click(timeout=5000)
    if progress:
        progress('Facebook đã chọn danh tính Page '+target['name']+'. Đang mở đúng trang để đăng bài…')
    return dict(name=target['name'], page_id=target['page_id'], switched=switched,
                source='visible_profile_selector')


def verify_page_actor(context, target, *, cancel=None, finished=None):
    """Read the current actor without changing it or touching an open composer."""
    _stop(cancel, finished)
    tab = context.new_page()
    try:
        tab.goto('https://www.facebook.com/', wait_until='domcontentloaded', timeout=45000)
        _, rows, active = _selector(tab, cancel=cancel, finished=finished)
        matches = [row for row in rows if _name(row['name']) == _name(target['name'])]
        if len(matches) != 1 or not matches[0]['selected'] or _name(active['name']) != _name(target['name']):
            raise ValueError('Danh tính Facebook đã đổi sau khi chuẩn bị bài. Chọn lại đúng Page và kiểm tra bản nháp; chưa bấm Đăng.')
        _matching_url(matches[0]['href'], target)
        return True
    finally:
        # This scratch tab belongs only to this check; never close a user tab.
        tab.close()
