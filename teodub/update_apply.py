"""Applies a Tèo code update or a roll-back. It runs as its own process, copied to a temp folder, after Tèo has
closed, so it can replace the code it came from. Standard library only; it must not import anything from teodub.

Job file (JSON): project, zip, sha256, source_dir, new_version, parent_pid, relaunch, python, smoke.
Exactly one of `zip` (a downloaded release) or `source_dir` (a saved older version, for "Quay lại bản trước").
Never touches models/, runtime/, tools/ or the user's data folder; only the names in ITEMS."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import zipfile

ITEMS = ('teodub', 'launch.py', 'version.json', 'requirements-lock.txt')
MAX_UNZIPPED = 400*1024*1024
KEEP_VERSIONS = 3
DEFAULT_SMOKE = ('teodub.version', 'teodub.app', 'teodub.pipeline', 'teodub.campaign', 'teodub.machine_check')


class UpdateError(Exception):
    pass


def version_tuple(text):
    parts = str(text).split('.')
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        raise ValueError('bad version')
    return tuple(int(part) for part in parts)


def read_version(folder):
    try:
        data = json.loads((Path(folder)/'version.json').read_text(encoding='utf-8'))
        version_tuple(data['version'])
        return str(data['version'])
    except (OSError, ValueError, KeyError, TypeError):
        return '0.0.0'


def sha256_of(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(1024*1024), b''):
            digest.update(block)
    return digest.hexdigest()


def safe_extract(zip_path, target):
    """Extract only the ITEMS names, refusing absolute paths, '..' and oversized archives."""
    target = Path(target)
    total = 0
    with zipfile.ZipFile(zip_path) as archive:
        for info in archive.infolist():
            name = info.filename.replace('\\', '/')
            parts = [part for part in name.split('/') if part]
            if not parts:
                continue
            if name.startswith('/') or ':' in parts[0] or '..' in parts:
                raise UpdateError('Tệp cập nhật có đường dẫn không an toàn; không áp dụng.')
            if parts[0] not in ITEMS:
                continue
            total += info.file_size
            if total > MAX_UNZIPPED:
                raise UpdateError('Tệp cập nhật lớn bất thường; không áp dụng.')
            destination = target.joinpath(*parts)
            if info.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, open(destination, 'wb') as output:
                shutil.copyfileobj(source, output)


def lock_lines(path):
    """The requirement lines of a lock file; line endings (CRLF from a Windows checkout vs LF in a zip) do not count."""
    try:
        return [line.strip() for line in Path(path).read_text(encoding='utf-8').splitlines() if line.strip()]
    except OSError:
        return []


def cpu_requirements(lock_path, target):
    """Write the lock without the NVIDIA GPU wheels (about 1 GB). The standalone bundle runs Whisper on the CPU, so an
    update must never pull them in; returns the path to install from."""
    lines = [line for line in Path(lock_path).read_text(encoding='utf-8').splitlines()
             if not line.strip().lower().startswith('nvidia-')]
    Path(target).write_text('\n'.join(lines)+'\n', encoding='utf-8')
    return Path(target)


def copy_items(source, project, items=ITEMS):
    """Replace each item that exists in `source` inside `project`; an item missing from source is left alone."""
    for name in items:
        origin, destination = Path(source)/name, Path(project)/name
        if not origin.exists():
            continue
        if destination.is_dir():
            shutil.rmtree(destination)
        elif destination.exists():
            destination.unlink()
        if origin.is_dir():
            shutil.copytree(origin, destination, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        else:
            shutil.copy2(origin, destination)


def wait_for_exit(pid, timeout=90):
    if not pid:
        return
    if os.name != 'nt':
        deadline = time.time()+timeout
        while time.time() < deadline:
            try:
                os.kill(pid, 0)
            except OSError:
                return
            time.sleep(0.5)
        return
    import ctypes
    handle = ctypes.windll.kernel32.OpenProcess(0x00100000, False, int(pid))  # SYNCHRONIZE
    if handle:
        ctypes.windll.kernel32.WaitForSingleObject(handle, int(timeout*1000))
        ctypes.windll.kernel32.CloseHandle(handle)


def backup_folder(versions, version):
    candidate = versions/version
    index = 2
    while candidate.exists():
        candidate = versions/f'{version}-{index}'
        index += 1
    return candidate


def prune(versions, keep=KEEP_VERSIONS):
    folders = sorted((path for path in versions.iterdir() if path.is_dir() and not path.name.startswith('_')),
                     key=lambda path: path.stat().st_mtime, reverse=True)
    for path in folders[keep:]:
        shutil.rmtree(path, ignore_errors=True)


def smoke_test(project, python, new_version, modules, runner):
    code = ('import sys; sys.path.insert(0, %r)\n' % str(project) +
            ''.join(f'import {name}\n' for name in modules) +
            'import teodub.version as v; assert v.current()["version"] == %r, v.current()\n' % new_version)
    done = runner([str(python), '-I', '-X', 'utf8', '-B', '-c', code], cwd=str(project), capture_output=True,
                  text=True, errors='replace', timeout=180)
    if done.returncode:
        raise UpdateError('Bản mới không tự kiểm tra qua được: '+(done.stderr or done.stdout or '').strip()[-300:])


def run(job, *, wait=wait_for_exit, runner=subprocess.run, launcher=subprocess.Popen):
    project = Path(job['project'])
    versions = project/'versions'
    versions.mkdir(exist_ok=True)
    result = dict(ok=False, rolled_back=False, to=job.get('new_version', ''), at=time.strftime('%Y-%m-%d %H:%M:%S'))
    old_version = read_version(project)
    result['from'] = old_version
    log = versions/'update.log'

    def say(text):
        with open(log, 'a', encoding='utf-8') as handle:
            handle.write(time.strftime('%Y-%m-%d %H:%M:%S ')+text+'\n')

    backup = None
    try:
        say(f'Bắt đầu: {old_version} -> {job.get("new_version")}')
        wait(job.get('parent_pid'))
        staging = versions/'_staging'
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir()
        if job.get('zip'):
            if job.get('sha256') and sha256_of(job['zip']).lower() != job['sha256'].lower():
                raise UpdateError('Tệp tải về bị hỏng (mã kiểm tra SHA256 không khớp). Chưa đổi gì.')
            safe_extract(job['zip'], staging)
        elif job.get('source_dir'):
            copy_items(job['source_dir'], staging)
        else:
            raise UpdateError('Thiếu nguồn cập nhật.')
        new_version = read_version(staging)
        if new_version == '0.0.0' or (job.get('new_version') and new_version != job['new_version']):
            raise UpdateError('Gói cập nhật không đúng số phiên bản mong đợi. Chưa đổi gì.')
        if not (staging/'teodub'/'__init__.py').is_file():
            raise UpdateError('Gói cập nhật thiếu mã chương trình. Chưa đổi gì.')
        result['to'] = new_version
        manifest = json.loads((staging/'version.json').read_text(encoding='utf-8'))
        minimum = manifest.get('min_from')
        if minimum and version_tuple(old_version) < version_tuple(minimum):
            raise UpdateError(f'Bản này cần bạn đang ở bản {minimum} trở lên (đang là {old_version}). Hãy tải lại bộ đầy đủ.')
        backup = backup_folder(versions, old_version)
        backup.mkdir()
        for name in ITEMS:
            source = project/name
            if source.is_dir():
                shutil.copytree(source, backup/name, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
            elif source.is_file():
                shutil.copy2(source, backup/name)
        say(f'Đã lưu bản cũ vào {backup.name}')
        old_lock = lock_lines(project/'requirements-lock.txt')
        copy_items(staging, project)
        say('Đã thay mã mới')
        python = job.get('python')
        new_lock = project/'requirements-lock.txt'
        if python and new_lock.is_file() and lock_lines(new_lock) != old_lock:
            say('Danh sách thư viện đổi; cài thêm')
            wanted = cpu_requirements(new_lock, versions/'_requirements-cpu.txt')
            done = runner([str(python), '-m', 'pip', 'install', '-r', str(wanted), '--disable-pip-version-check',
                           '--no-input'], capture_output=True, text=True, errors='replace', timeout=1800)
            if done.returncode:
                raise UpdateError('Cài thư viện của bản mới bị lỗi: '+(done.stderr or done.stdout or '').strip()[-300:])
        if python:
            smoke_test(project, python, new_version, job.get('smoke') or DEFAULT_SMOKE, runner)
        verb = 'quay lại' if job.get('source_dir') else 'cập nhật lên'
        result.update(ok=True, message=f'Đã {verb} bản {new_version}.')
        say('Xong')
    except Exception as exc:
        message = str(exc) if isinstance(exc, UpdateError) else f'Lỗi bất ngờ khi cập nhật: {type(exc).__name__}: {exc}'
        say('Lỗi: '+message)
        result.update(ok=False, message=message)
        if backup is not None and backup.is_dir():
            try:
                copy_items(backup, project)
                result.update(rolled_back=True, message=message+f' Đã quay lại bản {old_version}.')
                say(f'Đã quay lại bản {old_version}')
            except OSError as inner:
                result['message'] = message+f' Chưa quay lại được tự động ({inner}); bản cũ nằm ở {backup}.'
                say(result['message'])
    finally:
        shutil.rmtree(versions/'_staging', ignore_errors=True)
        (versions/'_requirements-cpu.txt').unlink(missing_ok=True)
        try:
            prune(versions)
        except OSError:
            pass
        (versions/'last_update.json').write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
        if job.get('relaunch'):
            flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
            launcher(job['relaunch'], cwd=str(project), creationflags=flags)
    return result


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    job = json.loads(Path(argv[0]).read_text(encoding='utf-8'))
    return 0 if run(job)['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
