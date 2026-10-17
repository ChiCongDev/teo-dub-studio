"""Read public Douyin channel posts through a normal, isolated browser session."""
from pathlib import Path
import re
import time
from urllib.parse import parse_qs, urlsplit

from teodub.common import check_cancel
from teodub.channel_browser import BROWSER_LABELS, launch_channel_browser
from teodub.channel_scan_guard import channel_scan_guard


def channel_url(text):
    text = re.sub(r'\[[^\]]*\]\((https?://[^\s)]+)\)', r'\1', text)
    urls = re.findall(r'https?://[^\s<>"\u3000]+', text)
    urls = list(dict.fromkeys(x.rstrip('.,;!?)\'】》。，；！）') for x in urls))
    if len(urls) != 1:
        raise ValueError('Dán một link trang cá nhân/kênh Douyin.')
    parsed = urlsplit(urls[0])
    if parsed.scheme not in ('http', 'https') or parsed.username or parsed.password or parsed.port not in (None, 80, 443):
        raise ValueError('Link kênh không hợp lệ.')
    if parsed.hostname == 'v.douyin.com' and re.fullmatch(r'/[\w-]+/?', parsed.path):
        return 'https://v.douyin.com'+parsed.path
    if parsed.hostname in ('douyin.com', 'www.douyin.com', 'm.douyin.com', 'www.iesdouyin.com', 'iesdouyin.com'):
        match = re.fullmatch(r'/(?:share/)?user/([\w-]+)/?', parsed.path)
        if match:
            return 'https://www.douyin.com/user/'+match[1]
    raise ValueError('Cần link kênh /user/… hoặc link chia sẻ kênh v.douyin.com; link một video dùng chế độ Danh sách link.')


def classify_input(text, kind='Tự nhận dạng link'):
    """Select the channel reader without mistaking a profile for a video."""
    from teodub.campaign import parse_links
    if kind == 'Danh sách link video':
        return 'videos', parse_links(text)
    if kind == 'Link kênh Douyin':
        return 'channel', channel_url(text)
    try:
        return 'channel', channel_url(text)
    except ValueError:
        return 'videos', parse_links(text)


def resolved_video_url(url):
    parsed = urlsplit(url)
    if parsed.hostname not in ('www.douyin.com', 'douyin.com', 'www.iesdouyin.com', 'm.douyin.com'):
        return None
    match = re.fullmatch(r'/(?:share/)?video/(\d+)/?', parsed.path)
    ident = match[1] if match else parse_qs(parsed.query).get('modal_id', [''])[0]
    return 'https://www.douyin.com/video/'+ident if ident.isdigit() else None


class ChannelPosts:
    def __init__(self, owner, limit):
        self.owner, self.limit = owner, limit
        self.items = {}
        self.complete = False
        self.skipped = set()

    def consume(self, response_url, payload):
        parsed = urlsplit(response_url)
        if (parsed.hostname != 'www.douyin.com' or parsed.path.rstrip('/') != '/aweme/v1/web/aweme/post'
                or parse_qs(parsed.query).get('sec_user_id', [''])[0] != self.owner
                or not isinstance(payload, dict) or payload.get('status_code', 0) != 0):
            return
        posts = payload.get('aweme_list')
        if not isinstance(posts, list):
            return
        for post in posts:
            if not isinstance(post, dict):
                continue
            ident = str(post.get('aweme_id', ''))
            author = post.get('author')
            if not ident.isdigit() or not isinstance(author, dict) or author.get('sec_uid') != self.owner:
                continue
            if post.get('images') or not post.get('video'):
                self.skipped.add(ident)
                continue
            if ident not in self.items and len(self.items) < self.limit:
                self.items[ident] = dict(url='https://www.douyin.com/video/'+ident,
                                        title=str(post.get('desc', ''))[:300])
        # A terminal page is evidence only if the caller has not truncated it.
        if payload.get('has_more') in (0, False) and len(self.items) < self.limit:
            self.complete = True


def scan_channel(text, profile_dir, *, limit=100, visible=False, cancel=None, progress=None,
                 on_items=None, allow_video=False, browser='auto', timeout=180):
    # Keep the profile exclusive through launch, reading and context.close().
    # Validation and cancellation happen before the guard creates any files.
    channel_url(text)
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError('Giới hạn lấy danh sách từ 1 đến 1.000 video.')
    if browser not in BROWSER_LABELS:
        raise ValueError('Chọn trình duyệt đọc kênh trong danh sách.')
    check_cancel(cancel)
    with channel_scan_guard(profile_dir):
        check_cancel(cancel)
        return _scan_channel(text, profile_dir, limit=limit, visible=visible, cancel=cancel,
                             progress=progress, on_items=on_items, allow_video=allow_video,
                             browser=browser, timeout=timeout)


def _scan_channel(text, profile_dir, *, limit=100, visible=False, cancel=None, progress=None,
                  on_items=None, allow_video=False, browser='auto', timeout=180):
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError('Giới hạn lấy danh sách từ 1 đến 1.000 video.')
    url = channel_url(text)
    if browser not in BROWSER_LABELS:
        raise ValueError('Chọn trình duyệt đọc kênh trong danh sách.')
    try:
        from playwright.sync_api import sync_playwright
        from playwright._impl._errors import TargetClosedError
    except ModuleNotFoundError as exc:
        raise ValueError('Thiếu thành phần đọc kênh. Đóng tool rồi mở lại bằng “Teo Dub Studio.cmd” trên Desktop '
                         'hoặc “Mo Teo Dub Studio.cmd” trong thư mục tool. Nếu vẫn lỗi, cần sửa môi trường chạy Tèo.') from exc
    profile_dir = Path(profile_dir).resolve()
    profile_dir.mkdir(parents=True, exist_ok=True)
    def notify(message):
        if progress:
            progress(message)
    with sync_playwright() as runtime:
        check_cancel(cancel)
        notify('Đang mở '+BROWSER_LABELS[browser]+(' · hiện cửa sổ.' if visible else ' · chạy ẩn.')+' Chờ tối đa 30 giây cho mỗi trình duyệt.')
        context, selected_browser = launch_channel_browser(runtime, profile_dir, browser=browser, visible=visible)
        try:
            check_cancel(cancel)
            page = context.pages[0] if context.pages else context.new_page()
            holder = {'posts': None, 'early': []}
            def response_received(response):
                try:
                    if urlsplit(response.url).path.rstrip('/') != '/aweme/v1/web/aweme/post':
                        return
                    data = response.json()
                    if holder['posts']:
                        holder['posts'].consume(response.url, data)
                    elif len(holder['early']) < 20:
                        holder['early'].append((response.url, data))
                except Exception:
                    pass
            page.on('response', response_received)
            notify('Đang đọc kênh bằng '+BROWSER_LABELS[selected_browser]+'…'+(' Cửa sổ riêng của Tèo; bạn có thể đăng nhập/xác minh nếu trang yêu cầu.' if visible else ''))
            page.goto(url, wait_until='domcontentloaded', timeout=45000)
            check_cancel(cancel)
            video_url = resolved_video_url(page.url) if allow_video else None
            if video_url:
                items = [dict(url=video_url, title='')]
                if on_items:
                    on_items(items)
                return dict(channel=None, items=items, complete=True, reason='single_video',
                            message='Link chia sẻ dẫn tới một video.', skipped_image_posts=0,
                            requested_browser=browser, browser=selected_browser)
            canonical = channel_url(page.url)
            owner = urlsplit(canonical).path.split('/')[-1]
            if urlsplit(canonical).hostname == 'v.douyin.com':
                raise ValueError('Link chưa chuyển đến trang kênh. Dùng link đầy đủ https://www.douyin.com/user/…')
            posts = ChannelPosts(owner, limit)
            holder['posts'] = posts
            for response_url, data in holder['early']:
                posts.consume(response_url, data)
            holder['early'].clear()
            started, last_change, last_count = time.monotonic(), time.monotonic(), 0
            reason = 'timeout'
            login_notified = False
            try:
                while time.monotonic()-started < timeout:
                    check_cancel(cancel)
                    if page.is_closed():
                        posts.complete = False
                        reason = 'browser_closed'
                        break
                    # Only links inside the channel's post list, never recommended videos.
                    links = page.locator('[data-e2e="user-post-list"] a[href*="/video/"]').evaluate_all(
                        '(nodes) => nodes.map(n => ({url:n.href,title:n.innerText || n.title || ""}))')
                    for item in links:
                        found = re.fullmatch(r'https://www\.douyin\.com/video/(\d+)(?:\?.*)?', item['url'])
                        if found and found[1] not in posts.skipped and len(posts.items) < limit:
                            posts.items.setdefault(found[1], dict(url='https://www.douyin.com/video/'+found[1], title=item['title'][:300]))
                    count = len(posts.items)
                    if count != last_count:
                        last_change, last_count = time.monotonic(), count
                        if on_items:
                            on_items([dict(row) for row in posts.items.values()])
                        notify(f'Đã tìm thấy {count} video; đang cuộn lấy tiếp…')
                    body = page.locator('body').inner_text(timeout=5000)
                    gated = '登录后查看更多作品' in body
                    if gated:
                        posts.complete = False
                    if count >= limit:
                        reason = 'limit'
                        posts.complete = False
                        break
                    # A login hint can coexist with posts still loading. Allow a
                    # short normal scroll window before treating it as a barrier.
                    if gated and time.monotonic()-last_change >= 8:
                        posts.complete = False
                        reason = 'login_required'
                        if not visible:
                            break
                        if not login_notified:
                            notify(f'Đã đọc {count} video. Douyin yêu cầu đăng nhập để xem tiếp; hãy đăng nhập trong cửa sổ vừa mở (tối đa 3 phút).')
                            login_notified = True
                        page.wait_for_timeout(1200)
                        continue
                    reason = 'timeout'
                    login_notified = False
                    if posts.complete and not gated:
                        reason = 'end_of_public_posts'
                        break
                    if time.monotonic()-last_change > (60 if visible else 25):
                        reason = 'no_new_posts'
                        break
                    listing = page.locator('[data-e2e="user-post-list"]').first
                    if listing.count():
                        # Scroll the real channel container, including nested layouts.
                        listing.evaluate('''n => {
                            for (let p=n; p; p=p.parentElement) {
                                const y=getComputedStyle(p).overflowY;
                                if ((y==='auto'||y==='scroll') && p.scrollHeight>p.clientHeight) {
                                    p.scrollTop=Math.min(p.scrollTop+1100,p.scrollHeight); return;
                                }
                            }
                        }''')
                    page.mouse.move(900, 650)
                    page.mouse.wheel(0, 1800)
                    page.wait_for_timeout(1200)
            except TargetClosedError:
                check_cancel(cancel)
                posts.complete = False
                reason = 'browser_closed'
            if not posts.items and reason == 'browser_closed':
                raise ValueError('Trình duyệt đã đóng trước khi đọc được video. Bấm Lấy danh sách để mở lại, hoặc nhập trang HTML từ Cốc Cốc bạn đang dùng.')
            if not posts.items:
                raise ValueError('Chưa đọc được video của kênh. Bật “Hiện trình duyệt để đăng nhập/xác minh” rồi thử lại, hoặc dán nhiều link video. Không có video nào được tải/lồng tiếng.')
            labels = {'limit': f'Đạt giới hạn {limit} video; chưa khẳng định hết kênh.',
                      'end_of_public_posts': 'Trang kênh báo đã hết danh sách video công khai.',
                      'no_new_posts': 'Không tải thêm được bài; danh sách có thể chưa đầy đủ.',
                      'timeout': 'Hết thời gian lấy danh sách; có thể chưa đầy đủ.',
                      'login_required': 'Douyin yêu cầu đăng nhập để xem thêm video. Danh sách hiện chưa đủ kênh; bật Hiện trình duyệt để đăng nhập rồi Lấy danh sách lại.',
                      'browser_closed': 'Trình duyệt đã đóng; giữ các link đã lấy. Danh sách chưa đủ kênh.'}
            return dict(channel=canonical, items=list(posts.items.values()), complete=posts.complete,
                        reason=reason, message=labels[reason], skipped_image_posts=len(posts.skipped),
                        requested_browser=browser, browser=selected_browser)
        except TargetClosedError as exc:
            check_cancel(cancel)
            raise ValueError('Trình duyệt đã đóng trước khi đọc được danh sách. Bấm Lấy danh sách để mở lại, hoặc nhập trang HTML từ Cốc Cốc bạn đang dùng.') from exc
        finally:
            try:
                context.close()
            except Exception:
                pass
