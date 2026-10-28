"""One public Douyin link through public downloader UIs in a private browser."""
import os
from collections import deque
from datetime import datetime
import http.client
import math
from pathlib import Path
import re
import ssl
import time
from urllib.parse import urlsplit
import urllib.request
import urllib.error
from teodub.downloaders.download_errors import ProviderUnavailable, classify_anysave
from teodub.downloaders.download_provider_registry import PROVIDERS

PROVIDER_URL = 'https://any-save.com/douyin'
# A transfer whose rest would take far too long at its current speed hands the video to the next source.
SLOW_GRACE_S = 60          # The first minute is never judged: video servers often start slowly.
SLOW_WINDOW_S = 60         # The speed is the average over the last minute.
SLOW_REMAINING_S = 15*60   # Switch when the rest would still need longer than this...
SLOW_FLOOR_BPS = 300*1024  # ...or, when the size is unknown, while the speed stays below this.


def is_public_media_url(url):
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or '').lower()
        cdn = (parsed.scheme == 'https' and not parsed.username and not parsed.password
                and parsed.port in (None, 443)
                and any(host.endswith('.'+suffix) for suffix in ('zjcdn.com', 'douyinvod.com')))
        if cdn:
            return True
        from teodub.downloaders.vienphanmem_download import is_vienphanmem_download_url
        from teodub.downloaders.socialvideo_download import is_socialvideo_download_url
        return is_vienphanmem_download_url(url) or is_socialvideo_download_url(url)
    except (ValueError, TypeError):
        return False


class MediaTransferError(ValueError):
    """Remote transfer/content failures eligible for another public provider."""

    def __init__(self, message, *, reason='invalid_media'):
        super().__init__(message)
        self.reason = reason


def _network_call(action):
    # Only wrap network operations, never local file writes or renames. In
    # particular a full disk must not trigger another website submission.
    try:
        return action()
    except urllib.error.HTTPError as exc:
        reason = 'rate_limited' if exc.code == 429 else 'http_error'
        raise MediaTransferError(f'Máy chủ tải video trả lỗi HTTP {exc.code}.', reason=reason) from exc
    except (urllib.error.URLError, http.client.HTTPException, OSError) as exc:
        raise MediaTransferError('Đường tải video bị gián đoạn hoặc không kết nối được.',
                                 reason='transfer_error') from exc


class SafeMediaRedirect(urllib.request.HTTPRedirectHandler):
    max_redirections = 5

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not is_public_media_url(newurl):
            raise MediaTransferError('Đường tải chuyển tới máy chủ chưa được hỗ trợ. Bấm Mở trang tải để kiểm tra.', reason='unsupported_media')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _check_speed(window, started, count, total):
    """Stop a transfer that would still take far too long; called only while another source can take over."""
    now = time.monotonic()
    window.append((now, count))
    while len(window) > 1 and window[1][0] <= now-SLOW_WINDOW_S:
        window.popleft()
    since, done = window[0]
    if now-started < SLOW_GRACE_S or now-since < SLOW_WINDOW_S:
        return
    rate = (count-done)/(now-since)
    speed = f'{rate/1048576:.2f}'.replace('.', ',')
    if total:
        remaining = (total-count)/rate if rate > 0 else math.inf
        if remaining <= SLOW_REMAINING_S:
            return
        wait = f'; phần còn lại cần khoảng {math.ceil(remaining/60)} phút' if math.isfinite(remaining) else ''
    elif rate >= SLOW_FLOOR_BPS:
        return
    else:
        wait = ''
    raise MediaTransferError(f'Đường tải quá chậm (khoảng {speed} MB/giây{wait}); chuyển sang nguồn khác.',
                             reason='too_slow')


def download_media(url, target, check, progress, max_bytes, *, may_switch=False):
    """Stream only observed CDN links; never forward browser cookies or credentials.

    With may_switch, a transfer that would still take far too long stops early (reason too_slow) so the next
    source can try; the last source is always allowed to finish however slow it is.
    """
    target = Path(target)
    if target.exists():
        raise FileExistsError('Video đích đã tồn tại; không ghi đè.')
    if not is_public_media_url(url):
        raise MediaTransferError('Đường tải chưa được hỗ trợ. Bấm Mở trang tải để kiểm tra.', reason='unsupported_media')
    check()
    opener = urllib.request.build_opener(SafeMediaRedirect(),
        urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    request = urllib.request.Request(url, headers={'User-Agent': 'TeoDubStudio/1.0'})
    part = target.with_name(target.name+'.part')
    if part.exists():
        raise FileExistsError('Tệp video đang tải đã tồn tại; không ghi đè.')
    owned_part = False
    try:
        with _network_call(lambda: opener.open(request, timeout=20)) as response:
            if not is_public_media_url(response.geturl()):
                raise MediaTransferError('Đường tải cuối không hợp lệ.', reason='unsupported_media')
            content_type = response.headers.get('Content-Type', '').split(';')[0].lower()
            if content_type not in ('video/mp4', 'application/octet-stream', 'binary/octet-stream'):
                raise MediaTransferError('Máy chủ chưa trả về video MP4; không lưu trang lỗi làm video.')
            try:
                total = int(response.headers.get('Content-Length') or 0)
                if total < 0:
                    raise ValueError('negative length')
            except ValueError as exc:
                raise MediaTransferError('Máy chủ trả kích thước video không hợp lệ.') from exc
            if total > max_bytes:
                raise ValueError('Video vượt giới hạn tải 2 GB của bản này.')
            count = 0
            started = time.monotonic()
            window = deque([(started, 0)])
            with part.open('xb') as output:
                owned_part = True
                while True:
                    check()
                    chunk = _network_call(lambda: response.read(256*1024))
                    if not chunk:
                        break
                    if not count and b'ftyp' not in chunk[:32]:
                        raise MediaTransferError('Dữ liệu tải về không phải MP4 hợp lệ.')
                    count += len(chunk)
                    if count > max_bytes:
                        raise ValueError('Video vượt giới hạn tải 2 GB của bản này.')
                    output.write(chunk)
                    progress(dict(status='downloading', downloaded_bytes=count, total_bytes=total))
                    if may_switch:
                        _check_speed(window, started, count, total)
            check()
            if not count or (total and count != total):
                raise MediaTransferError('Tải bị gián đoạn; chưa nhận đủ video.')
        progress(dict(status='finished', downloaded_bytes=count, total_bytes=count))
        check()
        _publish_media(part, target)
        owned_part = False
    finally:
        if owned_part:
            try:
                part.unlink(missing_ok=True)
            except OSError:
                pass  # Preserve the real failure; this job is never reused.


def _publish_media(source, target):
    """Publish a completed file without replacing a concurrently created one."""
    if target.exists():
        raise FileExistsError('Video đích đã tồn tại; không ghi đè.')
    if os.name == 'nt':
        source.rename(target)
    else:
        os.link(source, target)
        source.unlink()


def protect_browser_processes():
    """Worker death closes its own browser tree, never the user's Edge/Chrome."""
    from teodub.common import protect_worker
    protect_worker()


def _site_problem(page):
    problem = classify_anysave(page.locator('body').inner_text(timeout=2000))
    if problem:
        raise problem


def resolve_anysave(page, context, url, check, emit):
    observed = []
    def watch(tab):
        tab.on('download', lambda item: observed.append(item.url))
    context.on('page', watch)
    watch(page)
    page.goto(PROVIDER_URL, wait_until='load', timeout=45000)
    check()
    _site_problem(page)
    field = page.get_by_role('textbox', name='Douyin Video Downloader', exact=True)
    field.fill(url)
    field.press('Enter')
    emit('AnySave đang lấy video. Mỗi lượt chỉ gửi một yêu cầu…')
    deadline = time.monotonic()+90
    button = page.get_by_role('button', name='Download', exact=True).first
    while not button.is_visible():
        check()
        _site_problem(page)
        if time.monotonic() >= deadline:
            raise ProviderUnavailable('AnySave chưa trả kết quả sau 90 giây.', reason='timeout')
        page.wait_for_timeout(250)
    check()
    _site_problem(page)
    button.click()
    emit('AnySave đã có kết quả. Đang lấy đường tải video…')
    deadline = time.monotonic()+40
    while True:
        check()
        candidates = observed + [tab.url for tab in context.pages]
        media_url = next((u for u in candidates if is_public_media_url(u)), None)
        if media_url:
            return dict(media_url=media_url, title='Video Douyin', provider='anysave')
        _site_problem(page)
        if time.monotonic() >= deadline:
            raise ProviderUnavailable('AnySave chưa trả đường tải MP4 phù hợp.', reason='unsupported_media')
        page.wait_for_timeout(250)


def save_provider_failure(page, folder, provider):
    # Only the dedicated public provider page, never a user's browser/profile.
    try:
        text = page.locator('body').inner_text(timeout=2000)[:6000]
        (folder/(provider+'-status.txt')).write_text(text, encoding='utf-8')
        (folder/'website-status.txt').write_text(text, encoding='utf-8')
        page.screenshot(path=str(folder/(provider+'-status.png')), timeout=2000)
    except Exception:
        pass


def _close_browser_object(handle):
    """A browser disconnect during cleanup must not replace the run's result."""
    from playwright.sync_api import Error
    try:
        handle.close()
    except Error:
        pass


def _resolve_provider(provider, page, context, url, check, emit):
    if provider == 'douyin':
        from teodub.downloaders.douyin_direct import resolve_douyin
        from teodub.downloaders.video_download import MAX_BYTES
        return resolve_douyin(page, url, check, emit, is_public_media_url, max_bytes=MAX_BYTES)
    if provider == 'savetik':
        from teodub.downloaders.savetik_download import resolve_savetik
        return resolve_savetik(page, url, check, emit, is_public_media_url)
    if provider == 'vienphanmem':
        from teodub.downloaders.vienphanmem_download import resolve_vienphanmem
        return resolve_vienphanmem(page, url, check, emit, is_public_media_url)
    if provider == 'socialvideo':
        from teodub.downloaders.socialvideo_download import resolve_socialvideo
        return resolve_socialvideo(page, url, check, emit)
    if provider == 'anysave':
        return resolve_anysave(page, context, url, check, emit)
    raise ValueError('Nguồn tải chưa được hỗ trợ: '+provider)


def _all_unavailable(health, failures):
    """The ending for "no source can take this video now": a waiting state with a retry time, not a per-video failure.

    Sources that failed for this video only (Douyin refused it, a site could not read the link) cannot help by
    waiting, so the video waits for the sources that are paused as a whole (quota, maintenance, no answer).
    """
    from teodub.downloaders.provider_health import PAUSES
    video_only = {item['provider'] for item in failures
                  if item.get('status') == 'failed' and item.get('reason') not in PAUSES}
    retry_at = health.earliest_retry([service.id for service in PROVIDERS if service.id not in video_only])
    if retry_at is None:
        return None
    when = datetime.fromtimestamp(retry_at).strftime('%H:%M %d/%m')
    exc = ProviderUnavailable(f'Chờ nguồn tải: chưa nguồn tải nào lấy được video này lúc này '
                              f'(hết lượt, bảo trì, không phản hồi hoặc từ chối video). Tèo thử lại sau {when}.',
                              provider='all', reason='all_unavailable')
    exc.retry_at = retry_at
    exc.attempts = failures
    return exc


def _gone_ending(failures):
    """The ending when Douyin itself reports the video deleted or locked and no source got it (28/10/2026).

    No download site can have such a video, so waiting for the sites is pointless: it is a failure of this video
    (the campaign goes on to the next one; Thử lại video lỗi tries it again by hand). The message must neither start
    with the waiting text nor mention a login/limit, or the campaign would pause instead.
    """
    if not any(item.get('provider') == 'douyin' and item.get('status') == 'failed' and item.get('reason') == 'video_gone'
               for item in failures):
        return None
    exc = ProviderUnavailable('Video đã bị xóa hoặc khóa trên Douyin (Douyin báo status_deleted) và không nguồn tải nào '
                              'lấy được. Tèo bỏ qua video này để làm video kế tiếp; bấm Thử lại video lỗi nếu muốn thử lại sau.',
                              provider='all', reason='video_gone')
    exc.attempts = failures
    return exc


def resolve_providers(context, url, folder, check, emit, *, progress=None, validate=None, health=None):
    """Try each service once, through verified transfer when progress is supplied.

    The default remains resolver-only for callers that only need the URL.
    Production supplies progress and a full media validation callback so an
    expired link or broken MP4 can advance to an independent source too.
    """
    from playwright.sync_api import Error, TimeoutError as BrowserTimeout
    from teodub.downloaders.video_download import MAX_BYTES
    folder = Path(folder)
    target = folder/'video.mp4'
    if progress is not None and target.exists():
        raise FileExistsError('Video đích đã tồn tại; không ghi đè.')
    failures = []
    for index, service in enumerate(PROVIDERS, start=1):
        provider = service.id
        check()
        state = health.unavailable(provider) if health is not None else None
        if state is not None:
            when = datetime.fromtimestamp(state[0]).strftime('%H:%M')
            failures.append(dict(provider=provider, stage='skipped', status='skipped', reason=state[1]))
            emit(f'Bỏ qua {service.name}: đang không dùng được, thử lại sau {when}.',
                 provider=provider, provider_name=service.name, provider_index=index,
                 provider_count=len(PROVIDERS), stage='skipped')
            continue
        page = None
        candidate = None
        owned_candidate = False
        stage = 'resolve'

        def provider_emit(message, **extra):
            emit(message, **dict(dict(provider=provider, provider_name=service.name,
                 provider_index=index, provider_count=len(PROVIDERS), stage=stage), **extra))

        def provider_progress(data):
            progress(dict(data, provider=provider, provider_name=service.name,
                          provider_index=index, provider_count=len(PROVIDERS), stage=stage))

        provider_emit(f'Nguồn {index}/{len(PROVIDERS)}: {service.name}. Đang lấy video…', percent=0)
        try:
            page = context.new_page()
            page.set_default_timeout(5000)
            info = dict(_resolve_provider(provider, page, context, url, check, provider_emit))
            info['provider'] = provider
            check()
            if progress is not None:
                stage = 'download'
                attempt = folder/'attempts'/provider
                attempt.mkdir(parents=True, exist_ok=False)
                candidate = attempt/'video.mp4'
                provider_emit(f'{service.name}: đang tải MP4 từ đường dẫn vừa nhận…', percent=0)
                media_url = info.pop('media_url')
                if provider == 'vienphanmem':
                    # This site's official button uses its own browser download
                    # flow. Copy that download without a separate raw request,
                    # forwarding cookies, or submitting the form again.
                    from teodub.downloaders.vienphanmem_download import transfer_vienphanmem
                    transfer_vienphanmem(page, candidate, check, provider_progress, MAX_BYTES)
                else:
                    # A slow transfer may be dropped only while a later, not paused source can take over.
                    may_switch = any(health is None or health.unavailable(later.id) is None
                                     for later in PROVIDERS[index:])
                    download_media(media_url, candidate, check, provider_progress, MAX_BYTES, may_switch=may_switch)
                owned_candidate = True
                check()
                if validate is not None:
                    stage = 'validate'
                    provider_emit(f'{service.name}: đã tải xong, đang kiểm tra hình và tiếng…', percent=100)
                    info['verified_metadata'] = validate(candidate)
                    check()
                _publish_media(candidate, target)
                owned_candidate = False
                if 'verified_metadata' in info:
                    info['verified_metadata'] = dict(info['verified_metadata'], path=str(target.resolve()))
                status = 'downloaded'
            else:
                status = 'resolved'
            info['provider_attempts'] = failures + [dict(provider=provider, status=status)]
            if health is not None:
                health.record_success(provider)
            return info
        except (ProviderUnavailable, MediaTransferError, Error) as exc:
            check()
            if page is not None:
                save_provider_failure(page, folder, provider)
            if isinstance(exc, MediaTransferError):
                exc = ProviderUnavailable(str(exc), provider=provider, reason=exc.reason)
            elif not isinstance(exc, ProviderUnavailable):
                exc = ProviderUnavailable(
                    ('Trang tải phản hồi quá chậm hoặc đã đổi giao diện.' if isinstance(exc, BrowserTimeout)
                     else 'Chưa kết nối được trang tải.'), provider=provider, reason='provider_error')
            exc.provider = provider
            failures.append(dict(provider=provider, stage=stage, status='failed', reason=exc.reason, message=str(exc)))
            provider_emit(str(exc), reason=exc.reason)
            if health is not None:
                health.record_failure(provider, exc.reason, exc.retry_after_text)
            if exc.reason in ('verification_required', 'rate_limited') or index == len(PROVIDERS):
                # Respect explicit verification/rate restrictions; never loop.
                if exc.reason not in ('verification_required', 'rate_limited'):
                    exc = _gone_ending(failures) or (_all_unavailable(health, failures) if health is not None else None) or exc
                exc.attempts = failures
                raise exc
            following = PROVIDERS[index]
            provider_emit(f'{service.name} chưa tải được video. Đang chuyển sang {following.name} một lần…')
        finally:
            if owned_candidate and candidate is not None and candidate.exists():
                # This path is inside the newly created attempt directory. Never
                # remove other attempts, an old download or the final target.
                try:
                    candidate.unlink()
                except OSError:
                    pass
            if page is not None:
                _close_browser_object(page)
    # Reached only when the last service was skipped, so nothing else could end the attempt.
    ending = _gone_ending(failures) or (_all_unavailable(health, failures) if health is not None else None)
    if ending is None:
        ending = ProviderUnavailable('Chưa tải được video từ các trang tải.', provider='all', reason='provider_error')
        ending.attempts = failures
    raise ending


def download_via_browser(url, folder, check, progress, emit, validate=None):
    from playwright.sync_api import sync_playwright, Error
    protect_browser_processes()
    folder = Path(folder)
    emit('Đang mở phiên tải Douyin riêng…')
    check()
    with sync_playwright() as runtime:
        browser = None
        for channel in ('msedge', 'chrome'):
            try:
                browser = runtime.chromium.launch(channel=channel, headless=True,
                    chromium_sandbox=True, timeout=20000)
                break
            except Error:
                check()
        if browser is None:
            raise ValueError('Chưa mở được Edge hoặc Chrome để tải Douyin. Cài/mở một trong hai trình duyệt rồi thử lại.')
        context = None
        try:
            context = browser.new_context(accept_downloads=True, locale='en-US')
            from teodub.downloaders.provider_health import ProviderHealth
            info = resolve_providers(context, url, folder, check, emit,
                                     progress=progress, validate=validate, health=ProviderHealth())
        finally:
            if context is not None:
                _close_browser_object(context)
            _close_browser_object(browser)
    check()
    return dict(info, browser_channel=channel)
