"""First-run checks of this PC: browser, ChatGPT sign-in for Codex, models, install path. Pure logic, no windows."""
from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess

from teodub import codex_translation
from teodub.models import model_directory

MARKER = 'first_run_check.json'
LONG_PATH = 60  # an install folder longer than this risks Windows' 260-character path limit
REQUIRED_MODELS = (
    ('Whisper small (nhận dạng lời)', 'whisper', 'models--Systran--faster-whisper-small', 'model.bin'),
    ('VieNeu Turbo (giọng đọc)', 'huggingface/hub', 'models--pnnbao-ump--VieNeu-TTS-v3-Turbo', None),
)


@dataclass
class Check:
    key: str
    title: str
    ok: bool
    message: str
    action: str = ''  # key of the fix button the window offers: 'login', 'translate' or ''


def check_chrome(environ=None):
    environ = os.environ if environ is None else environ
    for variable in ('PROGRAMFILES', 'PROGRAMW6432', 'PROGRAMFILES(X86)', 'LOCALAPPDATA'):
        root = environ.get(variable)
        if root and (Path(root)/'Google/Chrome/Application/chrome.exe').is_file():
            return Check('chrome', 'Google Chrome', True, 'Đã có Google Chrome.')
    return Check('chrome', 'Google Chrome', False,
                 'Chưa thấy Google Chrome. Cài Chrome từ google.com/chrome rồi bấm Kiểm tra lại; '
                 'Tèo dùng Chrome để đăng nhập Douyin và đăng lên Page.')


def check_codex_login(run=subprocess.run):
    title = 'Dịch bằng ChatGPT (Codex)'
    try:
        executable = codex_translation.find_codex()
    except ValueError as exc:
        return Check('codex', title, False, str(exc))
    try:
        done = run([executable, 'login', 'status'], capture_output=True, text=True, timeout=20,
                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        text = (done.stdout or '')+(done.stderr or '')
    except (OSError, subprocess.SubprocessError):
        return Check('codex', title, False, 'Chưa hỏi được trạng thái đăng nhập Codex. Bấm Kiểm tra lại sau vài giây.')
    if not done.returncode and 'logged in using chatgpt' in text.casefold():
        return Check('codex', title, True, 'Đã đăng nhập ChatGPT trên máy này.')
    return Check('codex', title, False,
                 'Chưa đăng nhập ChatGPT. Bấm “Đăng nhập ChatGPT”, rồi đăng nhập tài khoản của bạn trên trình duyệt mở ra.', 'login')


def start_codex_login():
    """Open Codex's own sign-in (a browser page). Returns the process so the window can wait for it."""
    executable = codex_translation.find_codex()
    return subprocess.Popen([executable, 'login'], creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


def check_models(directory=None):
    directory = Path(directory) if directory else model_directory()
    missing = []
    for title, folder, name, needed_file in REQUIRED_MODELS:
        snapshots = directory/folder/name/'snapshots'
        found = [path for path in snapshots.glob('*') if path.is_dir()] if snapshots.is_dir() else []
        if not any(any(path.iterdir()) and (needed_file is None or (path/needed_file).exists()) for path in found):
            missing.append(title)
    if missing:
        return Check('models', 'Mô hình trên máy', False,
                     'Còn thiếu: '+', '.join(missing)+'. Tèo sẽ tự tải khi dùng lần đầu (cần mạng, vài trăm MB tới vài GB).')
    return Check('models', 'Mô hình trên máy', True, 'Đủ mô hình cần cho lần chạy đầu.')


def check_install_path(project):
    length = len(str(project))
    if length > LONG_PATH:
        return Check('path', 'Thư mục cài đặt', False,
                     f'Đường dẫn thư mục Tèo dài {length} ký tự. Windows giới hạn 260 ký tự cho cả đường dẫn tệp, '
                     'nên có thể lỗi khi tải mô hình. Nên chuyển Tèo tới thư mục ngắn như C:\\Teo.')
    return Check('path', 'Thư mục cài đặt', True, 'Đường dẫn thư mục đủ ngắn.')


def test_translation(invoke=None):
    """One real sentence through Codex, so a quota or sign-in problem shows up now and not in the middle of a video."""
    title = 'Dịch thử một câu'
    invoke = invoke or codex_translation._invoke_codex
    schema = {'type': 'object', 'properties': {'vi': {'type': 'string'}}, 'required': ['vi'], 'additionalProperties': False}
    prompt = 'Dịch sang tiếng Việt tự nhiên, ngắn gọn: "这个锅不粘，炒菜特别方便。" Trả JSON {"vi": "..."}'
    try:
        response, _ = invoke(prompt, schema)
        text = str(response.get('vi', '')).strip()
    except ValueError as exc:
        return Check('translate', title, False, str(exc))
    except Exception:
        return Check('translate', title, False, 'Chưa dịch thử được. Kiểm tra mạng rồi thử lại.')
    if not text:
        return Check('translate', title, False, 'Codex trả về bản dịch rỗng. Thử lại sau.')
    return Check('translate', title, True, 'Dịch thử được: “'+text[:80]+'”.')


def quick_checks(project):
    return [check_chrome(), check_codex_login(), check_models(), check_install_path(project)]


def needs_attention(checks):
    return any(not check.ok for check in checks)


def first_run_pending(data_dir):
    return not (Path(data_dir)/MARKER).is_file()


def mark_first_run_shown(data_dir):
    folder = Path(data_dir)
    folder.mkdir(parents=True, exist_ok=True)
    (folder/MARKER).write_text(json.dumps(dict(shown=True)), encoding='utf-8')
