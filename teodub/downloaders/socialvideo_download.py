"""Resolve Douyin using SocialVideoDownload's visible Vietnamese form.

The result is the public download link shown to visitors. No parsing endpoint,
session storage, browser cookies, or hidden page state is read or submitted.
"""
import math
import re
import time
from urllib.parse import parse_qsl, urljoin, urlsplit

from teodub.downloaders.download_errors import ProviderUnavailable


SOCIALVIDEO_URL = 'https://socialvideodownload.com/douyin'
PROVIDER = 'socialvideo'


def is_socialvideo_download_url(url):
    """Accept only the exact public signed link contract observed in #result."""
    if not isinstance(url, str) or any(c.isspace() or ord(c) < 32 for c in url):
        return False
    try:
        parsed = urlsplit(url)
        query = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
        return (parsed.scheme == 'https'
                and parsed.hostname == 'socialvideodownload.com'
                and parsed.port in (None, 443)
                and parsed.username is None and parsed.password is None
                and parsed.path == '/api/download' and not parsed.fragment
                and len(query) == 1 and query[0][0] == 't'
                and re.fullmatch(r'[A-Za-z0-9_-]{16,4096}', query[0][1]) is not None)
    except (TypeError, ValueError):
        return False


def _problem(text):
    lines = [' '.join(line.split()).casefold() for line in str(text or '').splitlines()
             if line.strip()]
    cases = (
        ('maintenance',
         ('douyin (抖音) đang bảo trì', 'hệ thống đang bảo trì'),
         'SocialVideoDownload đang bảo trì tải Douyin; chuyển sang nguồn khác nếu có.'),
        ('verification_required',
         ('verify you are human', 'verify that you are human', 'complete the captcha',
          'vui lòng xác minh bạn là con người', 'vui lòng hoàn thành captcha'),
         'Trang SocialVideoDownload yêu cầu xác minh; lượt tải đã dừng.'),
        ('rate_limited',
         ('too many requests', 'rate limit exceeded', 'daily limit reached',
          'quá nhiều yêu cầu', 'bạn đã vượt quá giới hạn'),
         'SocialVideoDownload đang giới hạn yêu cầu tải. Hãy thử lại sau.'),
        ('parse_failed',
         ('không tìm thấy video', 'không thể lấy thông tin video',
          'không lấy được thông tin video', 'link không hợp lệ', 'liên kết không hợp lệ',
          'video not found', 'invalid url', 'video is unavailable'),
         'SocialVideoDownload chưa xử lý được link hoặc không tìm thấy video.'),
        ('provider_error',
         ('failed to fetch', 'network error', 'service unavailable',
          'không thể kết nối', 'có lỗi xảy ra', 'lỗi kết nối', 'lỗi máy chủ'),
         'SocialVideoDownload báo lỗi xử lý hoặc kết nối; chưa lấy được video.'),
    )
    for reason, phrases, message in cases:
        if any(re.match(re.escape(phrase) + r'(?=$|[\s.!,:;—–-])', line)
               for line in lines for phrase in phrases):
            return ProviderUnavailable(message, reason=reason, provider=PROVIDER)
    return None


def resolve_socialvideo(page, url, check, emit, timeout=75):
    """Submit once and return the observed public MP4 download link and title."""
    if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('Thời gian chờ tải phải là số dương hữu hạn.')
    deadline = time.monotonic() + timeout

    def remaining(maximum=5000):
        seconds = deadline - time.monotonic()
        if seconds <= 0:
            raise ProviderUnavailable('SocialVideoDownload chưa trả kết quả trong thời gian chờ; '
                                      'Tèo không tự gửi lại yêu cầu.',
                                      reason='timeout', provider=PROVIDER)
        return max(1, min(maximum, int(seconds * 1000)))

    def call(action):
        check()
        remaining()
        try:
            return action()
        except ProviderUnavailable:
            raise
        except Exception as exc:
            check()
            problem = None
            budget = int((deadline - time.monotonic()) * 1000)
            if budget > 0:
                try:
                    problem = _problem(page.locator('body').inner_text(timeout=min(1000, budget)))
                except Exception:
                    pass
            if problem:
                raise problem from exc
            reason = 'timeout' if type(exc).__name__ == 'TimeoutError' else 'provider_error'
            raise ProviderUnavailable('Phiên tải SocialVideoDownload bị gián đoạn hoặc phản hồi '
                                      'quá chậm; chưa lấy được video.',
                                      reason=reason, provider=PROVIDER) from exc

    emit('Đang mở SocialVideoDownload để lấy video Douyin…')
    call(lambda: page.goto(SOCIALVIDEO_URL, wait_until='load', timeout=remaining(45000)))
    problem = _problem(call(lambda: page.locator('body').inner_text(timeout=remaining(2000))))
    if problem:
        raise problem
    field = page.get_by_role('textbox', name='Link video Douyin (抖音)', exact=True)
    call(lambda: field.fill(url, timeout=remaining()))
    call(lambda: field.press('End', timeout=remaining()))
    button = page.get_by_role('button', name='Lấy thông tin', exact=True)
    call(lambda: button.click(timeout=remaining()))
    emit('SocialVideoDownload đang đọc thông tin video. Mỗi lượt chỉ gửi một yêu cầu…')
    result = page.locator('#result')
    while True:
        check()
        remaining()
        link = result.get_by_role('link', name='Tải video không logo', exact=True)
        if call(link.count) and call(link.first.is_visible):
            href = call(lambda: link.first.get_attribute('href', timeout=remaining()))
            if href:
                media_url = urljoin(SOCIALVIDEO_URL, href)
                if not is_socialvideo_download_url(media_url):
                    raise ProviderUnavailable('SocialVideoDownload chưa trả đường tải MP4 '
                                              'công khai được hỗ trợ.',
                                              reason='unsupported_media', provider=PROVIDER)
                title = 'Video Douyin'
                heading = result.get_by_role('heading')
                if call(heading.count):
                    observed = call(lambda: heading.first.inner_text(timeout=remaining()))
                    title = ' '.join(observed.split())[:500] or title
                check()
                remaining()
                emit('SocialVideoDownload đã trả đường tải video. Đang chuẩn bị tải…')
                return dict(media_url=media_url, title=title, provider=PROVIDER)
        problem = _problem(call(lambda: page.locator('body').inner_text(timeout=remaining(2000))))
        if problem:
            raise problem
        call(lambda: page.wait_for_timeout(remaining(250)))
