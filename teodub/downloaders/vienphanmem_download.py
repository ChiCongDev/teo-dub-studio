"""Read the observed public Viện Phần Mềm downloader form and result button."""
import math
from pathlib import Path
import re
import time
from urllib.parse import parse_qs, urlsplit

from teodub.downloaders.download_errors import ProviderUnavailable


VIENPHANMEM_URL = 'https://vienphanmem.com/snaptik/'
_DOWNLOAD_PATH = '/wp-content/plugins/snaptik-app/api/download.php'


def is_vienphanmem_download_url(url):
    """Only the signed download destination exposed by the visible result button."""
    if not isinstance(url, str) or any(c.isspace() or ord(c) < 32 for c in url):
        return False
    try:
        parsed = urlsplit(url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        return bool(parsed.scheme == 'https' and parsed.hostname == 'vienphanmem.com'
                    and parsed.port in (None, 443) and parsed.username is None
                    and parsed.password is None and not parsed.fragment
                    and parsed.path == _DOWNLOAD_PATH and set(query) == {'token'}
                    and len(query['token']) == 1
                    and re.fullmatch(r'[A-Za-z0-9_.-]{16,10000}', query['token'][0]))
    except (TypeError, ValueError):
        return False


def _problem(text):
    lines = [' '.join(line.split()).casefold() for line in str(text or '').splitlines()]
    cases = (
        ('verification_required', ('verify you are human', 'verify that you are human',
          'complete the captcha', 'vui lòng xác minh'), 'yêu cầu xác minh'),
        ('rate_limited', ('too many requests', 'rate limit exceeded'), 'đang giới hạn yêu cầu'),
        ('daily_quota', ('đã hết lượt tải', 'bạn đã hết lượt tải'), 'đã hết lượt tải trong ngày'),
        ('parse_failed', ('không tìm thấy video', 'không thể lấy thông tin video',
          'link không hợp lệ', 'liên kết không hợp lệ'), 'chưa xử lý được link video'),
        ('provider_error', ('đã xảy ra lỗi', 'có lỗi xảy ra', 'lỗi kết nối',
          'something went wrong', 'failed to fetch'), 'báo lỗi xử lý hoặc kết nối'),
    )
    for reason, prefixes, description in cases:
        if any(re.match(re.escape(prefix) + r'(?=$|[\s.!,:;—–-])', line)
               for prefix in prefixes for line in lines):
            return ProviderUnavailable('Viện Phần Mềm ' + description + '.',
                                       provider='vienphanmem', reason=reason)
    return None


def resolve_vienphanmem(page, url, check, emit, media_predicate, timeout=90):
    if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('Thời gian chờ tải phải là số dương hữu hạn.')
    deadline = time.monotonic() + timeout

    def remaining(maximum=5000):
        left = deadline - time.monotonic()
        if left <= 0:
            raise ProviderUnavailable('Viện Phần Mềm chưa trả kết quả trong thời gian chờ.',
                                      provider='vienphanmem', reason='timeout')
        return max(1, min(maximum, int(left * 1000)))

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
            try:
                problem = _problem(page.locator('body').inner_text(timeout=remaining(1000)))
            except Exception:
                pass
            if problem:
                raise problem from exc
            raise ProviderUnavailable('Phiên tải Viện Phần Mềm bị gián đoạn hoặc trang đã đổi giao diện.',
                                      provider='vienphanmem', reason='provider_error') from exc

    def check_status():
        problem = _problem(call(lambda: page.locator('body').inner_text(timeout=remaining(1500))))
        if problem:
            raise problem

    emit('Đang mở Viện Phần Mềm để lấy video Douyin…')
    call(lambda: page.goto(VIENPHANMEM_URL, wait_until='load', timeout=remaining(45000)))
    check_status()
    field = page.get_by_role('textbox', name='URL', exact=True)
    button = page.get_by_role('button', name='Get', exact=True)
    call(lambda: field.fill(url, timeout=remaining()))
    ready_deadline = min(deadline, time.monotonic() + 15)

    def form_remaining(maximum=5000):
        left = ready_deadline - time.monotonic()
        if left <= 0:
            raise ProviderUnavailable('Viện Phần Mềm chưa sẵn sàng nhận link; chưa gửi yêu cầu tải.',
                                      provider='vienphanmem', reason='form_not_ready')
        return max(1, min(maximum, int(left * 1000)))

    while True:
        form_remaining()
        call(lambda: field.press('End', timeout=form_remaining()))
        check_status()
        enabled = call(button.is_enabled)
        form_remaining()
        if enabled:
            break
        call(lambda: page.wait_for_timeout(form_remaining(250)))
    call(lambda: button.click(timeout=remaining()))
    emit('Viện Phần Mềm đang xử lý link. Mỗi lượt chỉ gửi một yêu cầu…')
    result = page.locator('.download-box')
    download = result.get_by_role('button', name='Tải chất lượng Full HD', exact=True)
    while True:
        check()
        remaining()
        if call(download.count) and call(download.first.is_visible):
            href = call(lambda: download.first.get_attribute('data-download-url', timeout=remaining()))
            if href:
                if not is_vienphanmem_download_url(href) or not media_predicate(href):
                    raise ProviderUnavailable('Viện Phần Mềm trả đường tải chưa được hỗ trợ.',
                                              provider='vienphanmem', reason='unsupported_media')
                title = call(lambda: result.locator('.video-header').inner_text(timeout=remaining()))
                check()
                return dict(provider='vienphanmem', media_url=href,
                            title=' '.join((title.splitlines() or [''])[0].split())[:500] or 'Video Douyin')
        check_status()
        call(lambda: page.wait_for_timeout(remaining(250)))


def transfer_vienphanmem(page, target, check, progress, max_bytes):
    """Use the result's own download action, then copy its completed private file.

    No browser cookies or HTTP headers leave the browser. The public download
    event is bounded to 120 seconds and polled for cancellation. Playwright's
    synchronous Download.path() waits for completion without a timeout option;
    the owning worker's timeout/termination remains authoritative during that
    step. Byte progress is available only while copying the completed download.
    The context owner removes browser temporary files on context.close().
    """
    from playwright.sync_api import Error as BrowserError
    from teodub.downloaders.browser_download import (MediaTransferError, _publish_media,
                                      is_public_media_url)

    if not isinstance(max_bytes, int) or max_bytes <= 0:
        raise ValueError('Giới hạn kích thước video phải là số nguyên dương.')
    target = Path(target)
    part = target.with_name(target.name + '.part')
    if target.exists() or part.exists():
        raise FileExistsError('Tệp video đích đã tồn tại; không ghi đè.')
    check()
    captured = []

    def capture(download):
        captured.append(download)
        return True

    deadline = time.monotonic() + 120
    button = page.locator('.download-box').get_by_role(
        'button', name='Tải chất lượng Full HD', exact=True)
    progress(dict(status='downloading', downloaded_bytes=0, total_bytes=0))
    try:
        with page.expect_download(predicate=capture, timeout=120000) as event:
            check()
            button.click(timeout=10000)
            while not captured:
                check()
                left = deadline - time.monotonic()
                if left <= 0:
                    raise MediaTransferError('Viện Phần Mềm chưa bắt đầu tải video sau 120 giây.',
                                             reason='timeout')
                page.wait_for_timeout(max(1, min(250, int(left * 1000))))
        download = event.value
        check()
        observed_url = download.url
        blob_allowed = False
        if isinstance(observed_url, str) and observed_url.startswith('blob:'):
            try:
                origin = urlsplit(observed_url[5:])
                blob_allowed = (origin.scheme == 'https'
                                and origin.hostname == 'vienphanmem.com'
                                and origin.port in (None, 443)
                                and origin.username is None and origin.password is None
                                and bool(origin.path.strip('/'))
                                and not origin.query and not origin.fragment
                                and not any(c.isspace() or ord(c) < 32 for c in observed_url))
            except (TypeError, ValueError):
                pass
        if not blob_allowed and not is_public_media_url(observed_url):
            raise MediaTransferError('Nút tải Viện Phần Mềm mở nguồn không được hỗ trợ.',
                                     reason='unsupported_media')
        # Public Playwright API; it returns only this context's completed file.
        source_value = download.path()
        check()
    except BrowserError as exc:
        check()
        raise MediaTransferError('Trình duyệt chưa tải được video từ Viện Phần Mềm.',
                                 reason='transfer_error') from exc

    if source_value is None:
        raise MediaTransferError('Viện Phần Mềm chưa tạo được tệp video tải về.')
    source = Path(source_value)
    before = source.stat()
    if before.st_size > max_bytes:
        raise ValueError('Video vượt giới hạn tải 2 GB của bản này.')
    if before.st_size <= 0:
        raise MediaTransferError('Viện Phần Mềm trả tệp video rỗng.')
    owned_part = False
    count = 0
    try:
        with source.open('rb') as original, part.open('xb') as output:
            owned_part = True
            while True:
                check()
                chunk = original.read(256 * 1024)
                if not chunk:
                    break
                if not count and b'ftyp' not in chunk[:32]:
                    raise MediaTransferError('Dữ liệu từ Viện Phần Mềm không phải MP4 hợp lệ.')
                count += len(chunk)
                if count > max_bytes:
                    raise ValueError('Video vượt giới hạn tải 2 GB của bản này.')
                output.write(chunk)
                progress(dict(status='downloading', downloaded_bytes=count,
                              total_bytes=before.st_size))
        check()
        after = source.stat()
        if (count != before.st_size or
                (before.st_size, before.st_mtime_ns, before.st_ino) !=
                (after.st_size, after.st_mtime_ns, after.st_ino)):
            raise MediaTransferError('Tệp tải từ Viện Phần Mềm thay đổi hoặc chưa đầy đủ.')
        progress(dict(status='finished', downloaded_bytes=count, total_bytes=count))
        check()
        _publish_media(part, target)
        owned_part = False
    finally:
        if owned_part:
            try:
                part.unlink(missing_ok=True)
            except OSError:
                pass  # Keep the original disk, cancellation, or content failure.
