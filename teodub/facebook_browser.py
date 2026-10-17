"""Visible, manual Facebook login/inspection with one persistent profile per account."""
from contextlib import contextmanager
from pathlib import Path
import math
import inspect
import queue
import re
import threading
import time

from teodub.common import Cancelled, check_cancel
from teodub.facebook_accounts import FACEBOOK_HOME, FacebookAccounts, facebook_browser_url
from teodub.facebook_post_tasks import BrowserPostTasks
from teodub.pipeline import _job_lock


class _PostCancellation:
    def __init__(self, worker_cancel, request_cancel, expires_at):
        self.worker_cancel = worker_cancel
        self.request_cancel = request_cancel
        self.expires_at = expires_at

    def is_set(self):
        return (self.worker_cancel is not None and self.worker_cancel.is_set()
                or self.request_cancel is not None and self.request_cancel.is_set()
                or self.expires_at is not None and time.monotonic() >= self.expires_at)


@contextmanager
def facebook_profile_guard(profile_dir):
    directory = Path(profile_dir).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    # Acquisition errors are translated separately from errors inside the browser.
    guard = _job_lock(directory)
    try:
        guard.__enter__()
    except ValueError:
        raise ValueError('Hồ sơ Facebook này đang mở ở một cửa sổ Tèo khác. '
                         'Dùng cửa sổ đó hoặc đóng trình duyệt rồi mở lại.') from None
    try:
        yield directory
    finally:
        guard.__exit__(None, None, None)


def open_facebook_browser(profile_dir, *, url=FACEBOOK_HOME, cancel=None, finished=None,
                          commands=None, progress=None, request_id=None,
                          navigation_cancel=None, navigation_expires_at=None, page_navigation=None):
    """Hold the lock through graceful close. Do not read or export authentication data."""
    url = facebook_browser_url(url)
    check_cancel(cancel)
    try:
        from playwright.sync_api import sync_playwright
        from playwright._impl._errors import Error, TargetClosedError
    except ImportError:
        raise ValueError('Thiếu thành phần mở Chrome. Chạy cài đặt của Tèo để bổ sung Playwright.') from None
    closed = threading.Event()

    def notify(kind, message):
        if progress:
            progress(kind, message)

    with facebook_profile_guard(profile_dir) as directory, sync_playwright() as runtime:
        check_cancel(cancel)
        notify('progress', 'Đang mở Chrome của tài khoản đã chọn…')
        post_tasks = BrowserPostTasks()
        try:
            context = runtime.chromium.launch_persistent_context(
                str(directory), channel='chrome', headless=False, no_viewport=True,
                ignore_default_args=['--mute-audio'], timeout=30000)
        except Error as exc:
            raise ValueError('Chưa mở được Chrome. Kiểm tra Google Chrome đã cài và hồ sơ này '
                             'không đang mở ở nơi khác. Chi tiết: '+str(exc)) from None
        try:
            check_cancel(cancel)
            if finished is not None and finished.is_set():
                return 'finished'
            context.on('close', lambda *_: closed.set())
            page = context.pages[0] if context.pages else context.new_page()
            publishers = {}
            last_page_error = ['']

            def page_post(command):
                fields = {key: command.get(key, '') for key in ('request_id', 'operation', 'account_id', 'page_key', 'post_id')}
                def event(kind, message, **extra):
                    notify(kind, dict(fields, message=str(message), **extra))
                try:
                    request_cancel = command.get('cancel_request')
                    expires_at = command.get('expires_at')
                    if request_cancel is not None and not isinstance(request_cancel, threading.Event):
                        raise ValueError('Tín hiệu hủy yêu cầu bài viết không hợp lệ.')
                    if expires_at is not None and (isinstance(expires_at, bool) or not isinstance(expires_at, (int, float))
                                                  or not math.isfinite(expires_at)):
                        raise ValueError('Hạn xử lý yêu cầu bài viết không hợp lệ.')
                    command_cancel = (_PostCancellation(cancel, request_cancel, expires_at)
                                      if request_cancel is not None or expires_at is not None else cancel)
                    if request_cancel is not None and request_cancel.is_set():
                        raise Cancelled('Yêu cầu bài viết đã bị hủy trước khi xử lý; Chrome giữ nguyên, không bấm Đăng.')
                    if expires_at is not None and time.monotonic() >= expires_at:
                        raise Cancelled('Yêu cầu bài viết đã hết hạn trước khi xử lý; Chrome giữ nguyên, không bấm Đăng.')
                    check_cancel(command_cancel)
                    if (not all(isinstance(fields[key], str) and re.fullmatch(r'[0-9a-f]{32}', fields[key])
                                for key in ('account_id', 'page_key', 'post_id'))
                            or not isinstance(fields['request_id'], str) or not fields['request_id']
                            or len(fields['request_id']) > 128 or any(ord(c) < 32 for c in fields['request_id'])):
                        raise ValueError('Yêu cầu bài viết thiếu mã tài khoản/Page/bản nháp hoặc mã phản hồi hợp lệ.')
                    if fields['operation'] not in {'prepare', 'publish', 'review', 'inspect_result', 'verify'}:
                        raise ValueError('Thao tác bài viết trình duyệt không hợp lệ.')
                    data_dir = command.get('data_dir')
                    if not isinstance(data_dir, str) or not data_dir:
                        raise ValueError('Yêu cầu bài viết thiếu thư mục dữ liệu của Tèo.')
                    registry = FacebookAccounts(data_dir)
                    expected = registry.profile_directory(fields['account_id']).resolve()
                    if expected != directory or directory.parent.name != fields['account_id']:
                        raise ValueError('Yêu cầu bài thuộc hồ sơ/tài khoản khác. Chrome hiện tại giữ nguyên; chưa thao tác bài.')
                    from teodub.facebook_web_post import BrowserPagePublisher
                    key = str(registry.directory.resolve())
                    if key not in publishers:
                        publishers[key] = BrowserPagePublisher(data_dir)
                    publisher = publishers[key]
                    if post_tasks.owns_post(fields['account_id'], fields['page_key'], fields['post_id']):
                        raise ValueError('Lượt bài này đang được xử lý/xác minh; không tạo thêm lượt gửi.')
                    inspection = {}
                    if fields['operation'] == 'inspect_result':
                        if (not isinstance(command.get('result_url', ''), str)
                                or not isinstance(command.get('expected_reconcile'), dict)
                                or command.get('confirmed_attempt') is not True):
                            raise ValueError('Yêu cầu kiểm tra kết quả thiếu bản đối chiếu hoặc xác nhận đúng lượt đã gửi.')
                        inspection = dict(result_url=command.get('result_url', ''),
                                          expected_reconcile=command['expected_reconcile'], confirmed_attempt=True)
                    kwargs = dict(cancel=command_cancel, finished=finished,
                                  progress=lambda message: event('post_progress', message), **inspection)
                    steps = getattr(publisher, fields['operation']+'_steps', None)
                    if fields['operation'] in {'prepare', 'publish', 'verify'} and inspect.isgeneratorfunction(steps):
                        iterator = steps(context, fields['account_id'], fields['page_key'], fields['post_id'], **kwargs)
                        post_tasks.add(fields['request_id'], iterator,
                            scope=(fields['account_id'], fields['page_key'], fields['post_id']),
                            cancel=command_cancel, event=event)
                        return
                    method = publisher.focus_review if fields['operation'] == 'review' else getattr(publisher, fields['operation'])
                    result = method(context, fields['account_id'], fields['page_key'], fields['post_id'], **kwargs)
                    event('post_result', result.get('message', 'Đã hoàn tất thao tác bài trong Chrome.'), result=result)
                except Exception as exc:
                    # Every request receives its own meaningful failure; no retry,
                    # relaunch, account switch or silent swallowing of DOM errors.
                    event('post_error', str(exc) or type(exc).__name__)

            def navigate(target, *, initial=False, navigation_id=None, request_cancel=None, expires_at=None):
                target = facebook_browser_url(target)
                def navigation(state, message):
                    if navigation_id:
                        notify('navigation', dict(request_id=navigation_id,state=state,message=message))
                if request_cancel is not None and not isinstance(request_cancel, threading.Event):
                    raise ValueError('Tín hiệu hủy mở trang không hợp lệ.')
                if expires_at is not None and (isinstance(expires_at,bool) or not isinstance(expires_at,(int,float)) or not math.isfinite(expires_at)):
                    raise ValueError('Thời hạn mở trang không hợp lệ.')
                request_stop = _PostCancellation(cancel,request_cancel,expires_at)
                if request_stop.is_set():
                    check_cancel(cancel)
                    navigation('error','Yêu cầu mở trang đã dừng hoặc hết hạn; chưa điều hướng.')
                    return False
                navigation('opening', 'Đang mở '+target+' trong Chrome của tài khoản này…')
                existing = next((tab for tab in context.pages
                    if not tab.is_closed() and tab.url == target), None)
                try:
                    if existing is not None:
                        # Raising a tab must not reload a user's unfinished form.
                        check_cancel(request_stop)
                        existing.bring_to_front()
                        navigation('opened', 'Đã đưa tab '+target+' ra trước trong Chrome của Tèo. Nếu đang ở Facebook login, bạn tự đăng nhập/xác minh.')
                        return True
                    check_cancel(request_stop)
                    tab = page if initial and page.url == 'about:blank' else context.new_page()
                    check_cancel(request_stop)
                    tab.goto(target, wait_until='domcontentloaded', timeout=45000)
                    tab.bring_to_front()
                    navigation('opened', 'Đã mở '+target+' trong Chrome của Tèo. Nếu Chrome nằm phía sau cửa sổ Tèo, chọn cửa sổ Chrome trên thanh tác vụ để xem.')
                    return True
                except Cancelled:
                    check_cancel(cancel)
                    navigation('error','Yêu cầu mở trang đã dừng/hết hạn. Chrome được giữ để kiểm tra.')
                    return False
                except TargetClosedError:
                    navigation('error', 'Tab Chrome đã đóng trước khi mở xong trang. Đợi trình duyệt đóng xong rồi bấm mở lại.')
                    raise
                except Error:
                    # Leave the visible browser open for manual retry/verification.
                    notify('progress', 'Trang chưa tải xong. Chrome vẫn mở; bạn có thể kiểm tra mạng '
                           'và tải lại trực tiếp trong trình duyệt.')
                    navigation('error', 'Chrome đã mở nhưng trang chưa tải xong. Kiểm tra mạng/tải lại trong Chrome của Tèo, rồi bấm mở lại nếu cần.')
                    return False

            def activate_page(command, *, navigation_id=None, request_cancel=None, expires_at=None):
                def event(state, message):
                    notify('progress', message)
                    if navigation_id:
                        notify('navigation', dict(request_id=navigation_id,state=state,message=message))
                try:
                    if request_cancel is not None and not isinstance(request_cancel, threading.Event):
                        raise ValueError('Tín hiệu hủy mở Page không hợp lệ.')
                    if expires_at is not None and (isinstance(expires_at,bool) or not isinstance(expires_at,(int,float)) or not math.isfinite(expires_at)):
                        raise ValueError('Thời hạn mở Page không hợp lệ.')
                    stop = _PostCancellation(cancel, request_cancel, expires_at)
                    check_cancel(stop)
                    if finished is not None and finished.is_set():
                        raise Cancelled('Chrome đang đóng; chưa chuyển Page.')
                    data_dir, account_id, page_key = (command.get(key) for key in ('data_dir','account_id','page_key'))
                    if not isinstance(data_dir,str) or not data_dir or not all(
                            isinstance(value,str) and re.fullmatch(r'[0-9a-f]{32}',value) for value in (account_id,page_key)):
                        raise ValueError('Yêu cầu mở Page thiếu tài khoản/Page đã lưu.')
                    registry = FacebookAccounts(data_dir)
                    if registry.profile_directory(account_id).resolve() != directory:
                        raise ValueError('Page không thuộc hồ sơ Chrome đang mở; chưa chuyển danh tính.')
                    from teodub.page_activity import PageActivity
                    target = PageActivity(data_dir).page(account_id,page_key)
                    if command.get('url') != target['url']:
                        raise ValueError('Link Page đã đổi; chọn lại đúng Page trước khi mở.')
                    from teodub.facebook_page_identity import ensure_page_actor
                    from teodub.facebook_web_post import BrowserPagePublisher
                    event('opening','Đang mở Page '+target['name']+' và chuyển danh tính Facebook…')
                    tab = context.new_page()
                    tab.goto(target['url'],wait_until='domcontentloaded',timeout=45000)
                    tab.bring_to_front()
                    publisher = BrowserPagePublisher(data_dir)
                    publisher._safe_page(tab,target)
                    actor = ensure_page_actor(context,tab,target,cancel=stop,finished=finished,
                        progress=lambda message:event('opening',message))
                    if actor['switched']:
                        tab.goto(target['url'],wait_until='domcontentloaded',timeout=45000)
                    publisher._safe_page(tab,target)
                    check_cancel(stop)
                    last_page_error[0] = ''
                    event('opened','Đã mở đúng Page '+target['name']+' bằng danh tính Page. Có thể chuẩn bị và đăng bài đã duyệt.')
                    return True
                except Exception as exc:
                    last_page_error[0] = str(exc) or 'Chưa chuyển được sang danh tính Page; kiểm tra Chrome.'
                    event('error',last_page_error[0])
                    return False

            loaded = (activate_page(page_navigation,navigation_id=request_id,
                         request_cancel=navigation_cancel,expires_at=navigation_expires_at) if page_navigation else
                      navigate(url, initial=True, navigation_id=request_id,
                         request_cancel=navigation_cancel, expires_at=navigation_expires_at))
            notify('opened', (('Chrome đang mở đúng danh tính Page. ' if loaded else
                             'Chrome đã mở nhưng chưa chuyển được Page: '+last_page_error[0]+'. ') if page_navigation else
                            ('Chrome đang mở. ' if loaded else 'Chrome đã mở nhưng trang chưa tải xong; kiểm tra mạng và tải lại. '))
                   + 'Bạn tự đăng nhập Facebook hoặc kiểm tra Page. '
                   'Phiên được giữ trong hồ sơ này; bấm Đóng trình duyệt / Lưu phiên khi xong.')
            while not closed.is_set():
                check_cancel(cancel)
                if finished is not None and finished.is_set():
                    return 'finished'
                # A Page actor switch or a second composer must wait for the
                # current preparation. Read-only receipt jobs do not reserve
                # this slot; their generators continue between upload waits.
                if commands is not None and not post_tasks.busy:
                    try:
                        target = commands.get_nowait()
                    except queue.Empty:
                        pass
                    else:
                        try:
                            if isinstance(target, dict) and target.get('action') == 'restart_close':
                                try:
                                    from teodub.restart import browser_restart_check
                                    browser_restart_check(context)
                                except ValueError as exc:
                                    notify('restart_blocked', str(exc))
                                else:
                                    return 'restart_saved'  # finally flushes this account profile.
                            elif isinstance(target, dict) and target.get('action') == 'page_post':
                                page_post(target)
                            elif isinstance(target, dict) and target.get('action') == 'load_pages':
                                from teodub.facebook_page_discovery import read_managed_pages
                                load_id = target.get('request_id')
                                def load_notify(kind, value):
                                    if load_id:
                                        value = (dict(value, request_id=load_id) if isinstance(value, dict)
                                                 else dict(request_id=load_id, message=str(value)))
                                    notify(kind, value)
                                load_notify('pages_progress', 'Đang mở Trang bạn quản lý để đọc Page…')
                                try:
                                    load_cancel = target.get('cancel_request')
                                    load_expires = target.get('expires_at')
                                    if load_cancel is not None and not isinstance(load_cancel,threading.Event):
                                        raise ValueError('Tín hiệu hủy Load Page không hợp lệ.')
                                    if load_expires is not None and (isinstance(load_expires,bool) or not isinstance(load_expires,(int,float)) or not math.isfinite(load_expires)):
                                        raise ValueError('Thời hạn Load Page không hợp lệ.')
                                    load_stop = (_PostCancellation(cancel,load_cancel,load_expires)
                                                 if load_cancel is not None or load_expires is not None else cancel)
                                    check_cancel(load_stop)
                                    result = read_managed_pages(context, cancel=load_stop, finished=finished,
                                        progress=lambda message: load_notify('pages_progress', message))
                                    load_notify('pages_loaded', result)
                                except (ValueError, Cancelled) as exc:
                                    load_notify('pages_error', str(exc))
                            elif isinstance(target, dict) and target.get('action') == 'open_url':
                                navigate(target['url'], navigation_id=target.get('request_id'),
                                         request_cancel=target.get('cancel_request'),expires_at=target.get('expires_at'))
                            elif isinstance(target, dict) and target.get('action') == 'open_page':
                                activate_page(target,navigation_id=target.get('request_id'),
                                    request_cancel=target.get('cancel_request'),expires_at=target.get('expires_at'))
                            elif isinstance(target, str) and navigate(target):
                                notify('progress', 'Đã mở trang trong cùng hồ sơ Facebook. '
                                       'Bạn tự kiểm tra tài khoản/Page trong trình duyệt.')
                        except ValueError as exc:
                            notify('progress', str(exc))
                        except TargetClosedError:
                            if closed.is_set():
                                break
                post_tasks.step()
                pages = [tab for tab in context.pages if not tab.is_closed()]
                if not pages:
                    break
                try:
                    pages[0].wait_for_timeout(250)
                except TargetClosedError:
                    if closed.is_set():
                        break
            check_cancel(cancel)
            return 'browser_closed'
        except TargetClosedError:
            check_cancel(cancel)
            return 'browser_closed'
        finally:
            post_tasks.close()
            try:
                context.close()
            except TargetClosedError:
                pass
