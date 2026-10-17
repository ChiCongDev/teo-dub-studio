"""Resolve a Douyin video using SaveTik's public download form, once per run."""
import math
import re
import time

from teodub.downloaders.download_errors import ProviderUnavailable


SAVETIK_URL = 'https://savetik.io/en/douyin-video-downloader'
_MP4_LINKS = ('Download MP4 [2]', 'Download MP4 [1]', 'Download MP4 HD')


def _problem(text):
    """Match current status lines, not FAQ prose about possible failures."""
    lines = [re.sub(r'^(?:error|oops|sorry)\s*[:!\-–—]\s*', '',
                    ' '.join(line.split()), flags=re.IGNORECASE)
             for line in str(text or '').splitlines() if line.strip()]
    cases = (
        ('verification_required',
         ('verify you are human', 'verify that you are human', 'complete the captcha'),
         'SaveTik yêu cầu xác minh. Lượt tải đã dừng để bạn kiểm tra trên trang.'),
        ('rate_limited',
         ('too many requests', 'rate limit exceeded', 'daily limit reached',
          'you have reached your daily limit'),
         'SaveTik đang giới hạn yêu cầu tải. Hãy thử lại sau.'),
        ('parse_failed',
         ('video not found', 'unable to parse', 'failed to parse', 'could not parse',
          'invalid url', 'invalid link', 'video is unavailable'),
         'SaveTik chưa xử lý được link hoặc không tìm thấy video.'),
        ('provider_error',
         ('something went wrong', 'network error', 'failed to fetch', 'request failed',
          'service unavailable'),
         'SaveTik báo lỗi xử lý hoặc kết nối; chưa lấy được video.'),
    )
    for reason, phrases, message in cases:
        if any(re.match(re.escape(phrase) + r'(?=$|[\s.!,:;—–-])', line,
                        flags=re.IGNORECASE)
               for line in lines for phrase in phrases):
            return ProviderUnavailable(message, reason=reason, provider='savetik')
    return None


def resolve_savetik(page, url, check, emit, media_predicate, timeout=90):
    """Read an observed MP4 link; never click result links or repeat submission.

    The browser owner manages browser lifetime and failure screenshots. No user
    browser state, cookies, private endpoints, or downloaded bytes are involved.
    ``check`` is called between page operations and cancellation propagates intact.
    The timeout covers navigation, form submission, and waiting for the result.
    """
    if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('Thời gian chờ tải phải là số dương hữu hạn.')
    deadline = time.monotonic() + timeout

    def time_left(maximum=5000):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ProviderUnavailable('SaveTik chưa trả kết quả trong thời gian chờ; '
                                      'Tèo không tự gửi lại yêu cầu.',
                                      reason='timeout', provider='savetik')
        return max(1, min(maximum, int(remaining * 1000)))

    def browser_call(action):
        # Keep cancellation outside the exception wrapper. Provider failures must
        # never turn a user's Stop into a fallback attempt on another website.
        check()
        time_left()
        try:
            return action()
        except ProviderUnavailable:
            raise
        except Exception as exc:
            check()
            # A challenge can replace the form while a selector is waiting.
            # Preserve that explicit restriction instead of calling it a timeout
            # that the browser manager could mistake for ordinary unavailability.
            remaining_ms = int((deadline - time.monotonic()) * 1000)
            problem = None
            if remaining_ms > 0:
                try:
                    problem = _problem(page.locator('body').inner_text(
                        timeout=min(1000, remaining_ms)))
                except Exception:
                    pass
            if problem:
                raise problem from exc
            reason = 'timeout' if type(exc).__name__ == 'TimeoutError' else 'provider_error'
            message = ('SaveTik phản hồi quá chậm hoặc đã đổi giao diện.' if reason == 'timeout'
                       else 'Phiên tải SaveTik bị gián đoạn; chưa lấy được video.')
            raise ProviderUnavailable(message, reason=reason, provider='savetik') from exc

    emit('Đang mở SaveTik để lấy video Douyin…')
    browser_call(lambda: page.goto(SAVETIK_URL, wait_until='load', timeout=time_left(45000)))
    problem = _problem(browser_call(lambda: page.locator('body').inner_text(
        timeout=time_left(2000))))
    if problem:
        raise problem
    field = page.get_by_role('textbox', name='Search', exact=True)
    browser_call(lambda: field.fill(url, timeout=time_left()))
    # SaveTik enables its form from a keyboard event. Playwright fill alone
    # leaves the Paste state active. Its event handler can hydrate after the
    # field appears, so wait for the observed Clear state before submitting.
    form_deadline = min(deadline, time.monotonic() + 15)

    def form_time_left(maximum=5000):
        remaining = form_deadline - time.monotonic()
        if remaining <= 0:
            raise ProviderUnavailable('SaveTik chưa sẵn sàng nhận link; Tèo chưa gửi yêu cầu tải.',
                                      reason='form_not_ready', provider='savetik')
        return max(1, min(maximum, int(remaining * 1000)))

    while True:
        check()
        form_time_left()
        browser_call(lambda: field.press('End', timeout=form_time_left()))
        form_text = browser_call(lambda: page.locator('#search-form').inner_text(
            timeout=form_time_left(2000)))
        problem = _problem(browser_call(lambda: page.locator('body').inner_text(
            timeout=form_time_left(2000))))
        if problem:
            raise problem
        check()
        form_time_left()
        if any(line.strip() == 'Clear' for line in form_text.splitlines()):
            break
        browser_call(lambda: page.wait_for_timeout(form_time_left(250)))
    button = page.get_by_role('button', name='Download', exact=True)
    browser_call(lambda: button.click(timeout=time_left()))
    emit('SaveTik đang lấy video. Mỗi lượt chỉ gửi một yêu cầu…')
    result = page.locator('#download-result')
    while True:
        check()
        time_left()
        unsupported = False
        for name in _MP4_LINKS:
            links = result.get_by_role('link', name=name, exact=True)
            if not browser_call(links.count):
                continue
            link = links.first
            if not browser_call(link.is_visible):
                continue
            href = browser_call(lambda: link.get_attribute('href', timeout=time_left()))
            if not href:
                # The public form may append its label before setting the URL.
                continue
            if not media_predicate(href):
                unsupported = True
                continue
            headings = result.get_by_role('heading')
            title = 'Video Douyin'
            if browser_call(headings.count):
                observed = browser_call(lambda: headings.first.inner_text(timeout=time_left()))
                title = ' '.join(observed.split())[:500] or title
            check()
            time_left()
            emit('SaveTik đã trả đường tải MP4. Đang chuẩn bị tải video…')
            return dict(media_url=href, title=title, provider='savetik')

        body = browser_call(lambda: page.locator('body').inner_text(timeout=time_left(2000)))
        problem = _problem(body)
        if problem:
            raise problem
        if unsupported:
            raise ProviderUnavailable('SaveTik chưa trả đường tải MP4 trực tiếp được hỗ trợ.',
                                      reason='unsupported_media', provider='savetik')
        browser_call(lambda: page.wait_for_timeout(time_left(250)))
