"""Douyin's own public video page as a download source: no login, no cookies, no third-party site.

The video page itself asks Douyin for this one video's details. Tèo only reads that answer inside its private,
throw-away browser (it never sends the request itself, never reads cookies and never logs in), picks the best
picture that may be downloaded, and the media is then streamed from Douyin's public CDN by the same transfer and
validation as every other source. Any refusal simply hands the video to the next source.
"""
import re
import time
from urllib.parse import parse_qs, urlsplit

from teodub.downloaders.download_errors import ProviderUnavailable

PROVIDER = 'douyin'
DETAIL_PATH = '/aweme/v1/web/aweme/detail/'
_HOSTS = {'www.douyin.com', 'douyin.com', 'www.iesdouyin.com', 'iesdouyin.com'}
_ID = r'\d{8,25}'
# Titles of Douyin's human-check page. Tèo never solves it; that source pauses and the next one is tried.
# Only whole titles: a normal page title carries the video's own caption, which may mention these words.
_VERIFICATION_TITLES = {'验证码中间页', '验证码', '安全验证'}


def aweme_id(url):
    """The video id of a Douyin video link (also its share-page and modal forms), else None."""
    if not isinstance(url, str):
        return None
    try:
        parsed = urlsplit(url)
    except ValueError:
        return None
    if parsed.scheme != 'https' or (parsed.hostname or '').lower() not in _HOSTS:
        return None
    match = re.fullmatch(r'/(?:video|share/video)/(' + _ID + r')/?', parsed.path)
    if match:
        return match[1]
    modal = parse_qs(parsed.query).get('modal_id', [''])[0]
    return modal if re.fullmatch(_ID, modal) else None


def _number(value):
    return value if type(value) is int and value > 0 else 0


def choose_stream(detail, media_predicate, max_bytes):
    """The best picture Douyin offers that Tèo may download: highest height, then bitrate, within max_bytes."""
    video = detail.get('video') if isinstance(detail, dict) else None
    if not isinstance(video, dict):
        return None
    rates = video.get('bit_rate') if isinstance(video.get('bit_rate'), list) else []
    entries = [(entry.get('play_addr'), entry.get('bit_rate')) for entry in rates if isinstance(entry, dict)]
    entries.append((video.get('play_addr'), 0))  # The page's default address, chosen only when nothing better fits.
    options = []
    for address, bit_rate in entries:
        if not isinstance(address, dict) or not isinstance(address.get('url_list'), list):
            continue
        url = next((item for item in address['url_list'] if isinstance(item, str) and media_predicate(item)), None)
        size = _number(address.get('data_size'))
        if url is None or size > max_bytes:
            continue
        options.append((_number(address.get('height')), _number(bit_rate), url, size or None))
    if not options:
        return None
    height, bit_rate, url, size = max(options, key=lambda option: option[:2])
    return dict(url=url, height=height, bit_rate=bit_rate, size=size)


def resolve_douyin(page, url, check, emit, media_predicate, *, max_bytes, timeout=45):
    """One look at the video's own page; the next source takes over on any refusal. Never logs in or retries."""
    from playwright.sync_api import Error, TimeoutError as BrowserTimeout
    deadline = time.monotonic()+timeout

    def left(maximum):
        return max(1, min(maximum, int((deadline-time.monotonic())*1000)))

    def problem(message, reason):
        return ProviderUnavailable(message, reason=reason, provider=PROVIDER)

    answers = []
    check()
    wanted = aweme_id(url)
    page.on('response', lambda response: answers.append(response) if DETAIL_PATH in response.url else None)
    emit('Douyin trực tiếp: đang mở trang video (không đăng nhập; mỗi video chỉ mở trang một lần)…')
    try:
        page.goto(url, wait_until='domcontentloaded', timeout=left(30000))
    except BrowserTimeout:
        check()
        raise problem('Trang Douyin mở quá chậm; chuyển sang nguồn khác.', 'timeout') from None
    check()
    if wanted is None:
        wanted = aweme_id(page.url)  # A short share link has now led to the video page.
        if wanted is None:
            raise problem('Link này không dẫn tới một video Douyin; chuyển sang nguồn khác.', 'parse_failed')
    refused = 0
    next_look = 0.0
    while True:
        check()
        while answers:
            response = answers.pop(0)
            try:
                asked = parse_qs(urlsplit(response.url).query).get('aweme_id', [''])[0]
            except ValueError:
                continue
            if asked != wanted:
                continue  # Related videos the page also looks up.
            if response.status != 200:
                refused += 1  # The page asks again by itself; only its later answers matter.
                continue
            try:
                data = response.json()
            except Exception:  # Body gone or not JSON: the page may ask again, otherwise the deadline decides.
                refused += 1
                continue
            data = data if isinstance(data, dict) else {}
            detail = data.get('aweme_detail')
            if isinstance(detail, dict) and str(detail.get('aweme_id')) == wanted:
                stream = choose_stream(detail, media_predicate, max_bytes)
                if stream is None:
                    raise problem('Douyin trực tiếp chưa có bản MP4 phù hợp cho video này.', 'unsupported_media')
                lines = str(detail.get('desc') or '').splitlines()
                title = ' '.join((lines[0] if lines else '').split())[:500] or 'Video Douyin'
                check()
                emit(f'Douyin trực tiếp đã trả bản {stream["height"]}p. Đang chuẩn bị tải video…' if stream['height']
                     else 'Douyin trực tiếp đã trả đường tải MP4. Đang chuẩn bị tải video…')
                return dict(media_url=stream['url'], title=title, provider=PROVIDER)
            hidden = data.get('filter_detail')
            if isinstance(hidden, dict) and hidden.get('filter_reason'):
                raise problem('Douyin không cho xem video này khi chưa đăng nhập (bị giới hạn, riêng tư hoặc đã xóa); '
                              'chuyển sang nguồn khác.', 'parse_failed')
            refused += 1
        now = time.monotonic()
        if now >= next_look:
            next_look = now+2
            try:
                title = page.title()
            except Error:
                title = ''
            if title.split(' - ')[0].strip() in _VERIFICATION_TITLES:
                raise problem('Douyin yêu cầu xác minh (captcha). Tèo không tự giải; chuyển sang nguồn khác.', 'blocked')
            if refused and aweme_id(page.url) != wanted:
                raise problem('Douyin chưa cho xem video này khi chưa đăng nhập; chuyển sang nguồn khác.', 'refused')
        if now >= deadline:
            if refused:
                raise problem('Douyin chưa cho xem video này khi chưa đăng nhập; chuyển sang nguồn khác.', 'refused')
            raise problem('Douyin chưa trả thông tin video trong thời gian chờ.', 'timeout')
        page.wait_for_timeout(left(250))
