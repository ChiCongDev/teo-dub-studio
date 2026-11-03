"""Looks for a newer Tèo release on the owner's public GitHub repository and hands it to update_apply.py.

Nothing here signs in to GitHub or sends any account detail: the repository is public and only read. The check never
raises; a PC that is offline simply sees no news."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
import urllib.error
import urllib.request
import zipfile

from teodub import update_apply
from teodub.runtime_paths import find_python

REPO = 'ChiCongDev/teo-dub-studio'
API = f'https://api.github.com/repos/{REPO}/releases/latest'
DOWNLOAD_PREFIX = f'https://github.com/{REPO}/releases/download/'
ASSET = re.compile(r'^teo-code-(\d+\.\d+\.\d+)\.zip$')
MAX_DOWNLOAD = 300*1024*1024
PROJECT = Path(__file__).resolve().parents[1]
WORKER_FLAGS = ('--worker', '--campaign-worker', '--campaign-batch-worker')


def fetch_json(url, timeout):
    request = urllib.request.Request(url, headers={'User-Agent': 'TeoDubStudio-Updater', 'Accept': 'application/vnd.github+json'})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read(2*1024*1024).decode('utf-8'))


def fetch_to_file(url, target, timeout, progress=None):
    request = urllib.request.Request(url, headers={'User-Agent': 'TeoDubStudio-Updater'})
    with urllib.request.urlopen(request, timeout=timeout) as response, open(target, 'wb') as output:
        total = int(response.headers.get('Content-Length') or 0)
        done = 0
        while True:
            block = response.read(256*1024)
            if not block:
                break
            done += len(block)
            if done > MAX_DOWNLOAD:
                raise ValueError('Tệp tải về lớn bất thường.')
            output.write(block)
            if progress:
                progress(done, total)


def fetch_text(url, timeout):
    request = urllib.request.Request(url, headers={'User-Agent': 'TeoDubStudio-Updater'})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read(4096).decode('utf-8', 'replace')


def current_version():
    from teodub.version import current
    return current()['version']


def can_update_here(project=PROJECT):
    """The project owner's own machine is a git clone: it makes releases, it must never overwrite itself with one."""
    project = Path(project)
    if (project/'.git').exists():
        return False, 'Đây là máy chủ dự án (thư mục có .git); máy này tạo bản phát hành chứ không tự cập nhật.'
    if find_python(project) is None:
        return False, 'Không tìm thấy Python của Tèo để áp dụng bản cập nhật.'
    return True, ''


def check(current, *, fetch=fetch_json, timeout=5):
    """Latest release as dict(status='newer'|'current'|'error', ...). Never raises."""
    try:
        data = fetch(API, timeout)
        tag = str(data.get('tag_name', '')).lstrip('vV')
        latest = update_apply.version_tuple(tag)
        asset = next((item for item in data.get('assets', []) if ASSET.match(str(item.get('name', '')))
                      and ASSET.match(item['name']).group(1) == tag), None)
        if asset is None:
            return dict(status='current', current=current, message='Chưa có bản phát hành nào dùng được.')
        zip_url = str(asset.get('browser_download_url', ''))
        sha_url = next((str(item.get('browser_download_url', '')) for item in data['assets']
                        if item.get('name') == asset['name']+'.sha256'), '')
        if not zip_url.startswith(DOWNLOAD_PREFIX) or (sha_url and not sha_url.startswith(DOWNLOAD_PREFIX)):
            return dict(status='error', current=current, message='Địa chỉ tải không thuộc kho của Tèo; bỏ qua để an toàn.')
        if latest <= update_apply.version_tuple(current):
            return dict(status='current', current=current, version=tag, message='Bạn đang dùng bản mới nhất.')
        return dict(status='newer', current=current, version=tag, notes=str(data.get('body') or '').strip()[:4000],
                    zip_url=zip_url, sha_url=sha_url)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:  # a repository with no release yet
            return dict(status='current', current=current, message='Chưa có bản phát hành nào.')
        return dict(status='error', current=current, message='GitHub chưa trả lời được (mã '+str(exc.code)+'). Thử lại sau.')
    except Exception:
        return dict(status='error', current=current, message='Chưa kiểm tra được bản mới (không có mạng hoặc GitHub không trả lời).')


def check_in_background(callback, *, current=None, **options):
    """Run check() on a thread; callback(result) is called from that thread."""
    def work():
        callback(check(current or current_version(), **options))
    threading.Thread(target=work, daemon=True).start()


def download(info, folder, *, progress=None, timeout=60, get_file=fetch_to_file, get_text=fetch_text):
    """Download the release zip into `folder` and verify it. Returns (zip_path, sha256). Raises ValueError (Vietnamese)."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder/f'teo-code-{info["version"]}.zip'
    try:
        expected = ''
        if info.get('sha_url'):
            match = re.search(r'\b[0-9a-fA-F]{64}\b', get_text(info['sha_url'], timeout))
            expected = match.group(0).lower() if match else ''
        if not expected:
            raise ValueError('Bản phát hành thiếu mã kiểm tra SHA256; không tải để an toàn.')
        get_file(info['zip_url'], target, timeout, progress)
    except ValueError:
        raise
    except Exception:
        raise ValueError('Tải bản cập nhật không xong (mạng bị ngắt?). Chưa đổi gì, thử lại sau.') from None
    if update_apply.sha256_of(target) != expected:
        target.unlink(missing_ok=True)
        raise ValueError('Tệp tải về bị hỏng (SHA256 không khớp). Chưa đổi gì, thử lại sau.')
    try:
        with zipfile.ZipFile(target) as archive:
            manifest = json.loads(archive.read('version.json').decode('utf-8'))
    except (OSError, KeyError, ValueError, zipfile.BadZipFile):
        raise ValueError('Gói cập nhật không đọc được. Chưa đổi gì.') from None
    minimum = manifest.get('min_from')
    if minimum and update_apply.version_tuple(info['current']) < update_apply.version_tuple(minimum):
        raise ValueError(f'Bản {info["version"]} cần bạn đang dùng từ bản {minimum} trở lên (đang là {info["current"]}). '
                         'Hãy tải lại bộ Tèo đầy đủ từ chủ dự án.')
    return target, expected


def running_workers(project=PROJECT):
    """Process ids of dubbing/campaign workers of THIS project. ['?'] when it could not be read (treated as busy)."""
    command = ("Get-CimInstance Win32_Process -Filter \"Name='python.exe' or Name='pythonw.exe'\" | "
               "Where-Object { $_.CommandLine -like '*"+str(project).replace("'", "''")+"*' -and $_.CommandLine -match '--(worker|campaign-worker|campaign-batch-worker)' } | "
               "Select-Object -ExpandProperty ProcessId")
    try:
        done = subprocess.run(['powershell', '-NoProfile', '-Command', command], capture_output=True, text=True, timeout=20)
        if done.returncode:
            return ['?']
        return [line.strip() for line in done.stdout.splitlines() if line.strip()]
    except (OSError, subprocess.SubprocessError):
        return ['?']


def previous_versions(project=PROJECT):
    versions = Path(project)/'versions'
    if not versions.is_dir():
        return []
    found = []
    for path in versions.iterdir():
        if path.is_dir() and not path.name.startswith('_') and (path/'teodub'/'__init__.py').is_file():
            found.append((update_apply.read_version(path), path))
    return sorted(found, key=lambda item: item[1].stat().st_mtime, reverse=True)


def pop_last_result(project=PROJECT):
    """The outcome of the last update, once: shown by the new Tèo then forgotten."""
    path = Path(project)/'versions'/'last_update.json'
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        path.unlink()
        return data
    except (OSError, ValueError):
        return None


def start_apply(project, *, zip_path=None, sha256=None, source_dir=None, new_version=None, data_dir, web_data_dir=None,
                parent_pid=None):
    """Start update_apply.py detached; the caller must then close Tèo. Returns nothing."""
    project = Path(project)
    python = find_python(project)
    windowed = find_python(project, windowed=True)
    if python is None or windowed is None:
        raise ValueError('Không tìm thấy Python của Tèo để áp dụng bản cập nhật.')
    relaunch = [str(windowed), '-X', 'utf8', '-B', str(project/'launch.py'), '--data-dir', str(data_dir)]
    if web_data_dir:
        relaunch += ['--web-data-dir', str(web_data_dir)]
    work = Path(tempfile.mkdtemp(prefix='teo-update-'))
    script = work/'update_apply.py'
    shutil.copy2(update_apply.__file__, script)
    job = dict(project=str(project), zip=str(zip_path) if zip_path else None, sha256=sha256,
               source_dir=str(source_dir) if source_dir else None, new_version=new_version,
               parent_pid=parent_pid or os.getpid(), relaunch=relaunch, python=str(python))
    (work/'job.json').write_text(json.dumps(job, ensure_ascii=False), encoding='utf-8')
    flags = (getattr(subprocess, 'DETACHED_PROCESS', 0) | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0)
             | getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    subprocess.Popen([str(python), '-I', '-X', 'utf8', str(script), str(work/'job.json')], cwd=str(work), creationflags=flags)
