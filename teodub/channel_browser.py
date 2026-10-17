"""Browser selection for channel scans; no access to a user's daily profile."""
import os
from pathlib import Path

BROWSER_LABELS = {'auto': 'Tự chọn (Edge / Chrome)', 'coccoc': 'Cốc Cốc (thử nghiệm)',
                  'msedge': 'Microsoft Edge', 'chrome': 'Google Chrome'}


def find_coccoc(environ=None):
    environ = os.environ if environ is None else environ
    seen = set()
    for variable in ('LOCALAPPDATA', 'PROGRAMFILES', 'PROGRAMW6432', 'PROGRAMFILES(X86)'):
        root = environ.get(variable)
        if not root:
            continue
        path = Path(root)/'CocCoc'/'Browser'/'Application'/'browser.exe'
        if path in seen:
            continue
        seen.add(path)
        if path.is_file():
            return path.resolve()
    return None


def profile_directory(base, browser):
    if browser not in BROWSER_LABELS:
        raise ValueError('Chọn trình duyệt đọc kênh trong danh sách.')
    # Preserve the existing automatic Edge/Chrome profile. Explicit choices are separate.
    return Path(base).resolve() if browser == 'auto' else Path(base).resolve()/browser


def launch_channel_browser(runtime, profile_dir, *, browser='auto', visible=False):
    if browser not in BROWSER_LABELS:
        raise ValueError('Chọn trình duyệt đọc kênh trong danh sách.')
    common = dict(headless=not visible, viewport=dict(width=1280, height=850),
                  args=['--mute-audio'], timeout=30000)
    choices = ('msedge', 'chrome') if browser == 'auto' else (browser,)
    last_error = None
    for selected in choices:
        options = dict(common)
        if selected == 'coccoc':
            executable = find_coccoc()
            if executable is None:
                raise ValueError('Chưa tìm thấy Cốc Cốc đã cài trên máy. Chọn Edge / Chrome để đọc kênh.')
            options['executable_path'] = str(executable)
        else:
            options['channel'] = selected
        try:
            context = runtime.chromium.launch_persistent_context(str(Path(profile_dir).resolve()), **options)
            return context, selected
        except Exception as exc:
            last_error = exc
            if browser != 'auto':
                raise ValueError('Chưa mở được '+BROWSER_LABELS[selected]+'. Đóng cửa sổ đọc kênh riêng cũ rồi thử lại; '
                                 'nếu vẫn lỗi, chọn Edge / Chrome. Chiến dịch đang chạy được giữ nguyên.') from exc
    raise ValueError('Chưa mở được Edge / Chrome để đọc kênh. Đóng cửa sổ đọc kênh riêng cũ rồi thử lại.') from last_error
