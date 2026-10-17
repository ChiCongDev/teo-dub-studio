"""Manual Douyin sign-in using the same persistent Chrome profile as scans."""
from pathlib import Path
import threading
import time

from teodub.channel_browser import launch_channel_browser
from teodub.channel_discovery import channel_url
from teodub.channel_scan_guard import channel_scan_guard
from teodub.common import check_cancel


def open_chrome_login(profile_dir, *, text='', cancel=None, finished=None, progress=None, timeout=1800):
    url = channel_url(text) if text.strip() else 'https://www.douyin.com/'
    if timeout <= 0:
        raise ValueError('Thời gian chờ đăng nhập phải lớn hơn 0.')
    check_cancel(cancel)
    from playwright.sync_api import sync_playwright
    from playwright._impl._errors import TargetClosedError
    profile_dir = Path(profile_dir).resolve()
    closed = threading.Event()
    def notify(message):
        if progress:
            progress(message)
    with channel_scan_guard(profile_dir), sync_playwright() as runtime:
        check_cancel(cancel)
        notify('Đang mở Chrome với hồ sơ đăng nhập đã lưu của Tèo…')
        context, _ = launch_channel_browser(runtime, profile_dir, browser='chrome', visible=True)
        try:
            check_cancel(cancel)
            context.on('close', lambda *_: closed.set())
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(url, wait_until='domcontentloaded', timeout=45000)
            notify('Bạn tự đăng nhập Douyin trong Chrome. Khi xong, bấm Đã xong / Lưu phiên '
                   'hoặc đóng cửa sổ Chrome này. Tèo giữ hồ sơ cho lần quét sau; chờ tối đa 30 phút.')
            deadline = time.monotonic()+timeout
            while not closed.is_set():
                check_cancel(cancel)
                if finished is not None and finished.is_set():
                    return 'finished'
                if time.monotonic() >= deadline:
                    return 'timeout'
                pages = [item for item in context.pages if not item.is_closed()]
                if not pages:
                    return 'browser_closed'
                try:
                    pages[0].wait_for_timeout(300)
                except TargetClosedError:
                    # A user may close one login tab while another tab remains open.
                    if closed.is_set() or not context.pages:
                        break
            check_cancel(cancel)
            return 'browser_closed'
        except TargetClosedError:
            check_cancel(cancel)
            return 'browser_closed'
        finally:
            try:
                context.close()
            except TargetClosedError:
                pass
