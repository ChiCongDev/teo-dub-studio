"""Đóng gói bộ Tèo đầy đủ để gửi cho người mới (giải nén, mở là dùng; không cần cài gì).

    python scripts/package_bundle.py [--out THU_MUC] [--work THU_MUC_NGAN] [--no-models] [--no-runtime] [--dry-run]

Bộ gồm: Python chạy độc lập + thư viện bản CPU (không có gói NVIDIA), mã Tèo, mô hình (Whisper small + medium, VieNeu
Turbo + Nano, MOSS), Codex, node-runtime. KHÔNG gồm: dữ liệu người dùng, đăng nhập, giọng mẫu riêng (assets/voice-references),
Whisper large-v3, bộ xóa chữ GPU, Teo Web, .git, work/.
Python được dựng ở thư mục có ĐƯỜNG DẪN NGẮN (mặc định C:\\TeoBuild) vì Windows lỗi khi đường dẫn quá 260 ký tự."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request
import zipfile

PROJECT = Path(__file__).resolve().parents[1]
CODE_ITEMS = ('teodub', 'launch.py', 'version.json', 'requirements-lock.txt')
TOP = 'Teo'  # short top folder keeps the extracted paths short
PBS_TAG = '20261026'
PBS_FILE = 'cpython-3.13.18+20261026-x86_64-pc-windows-msvc-install_only.tar.gz'
PBS_BASE = f'https://github.com/astral-sh/python-build-standalone/releases/download/{PBS_TAG}/'
# Microsoft's Visual C++ runtime, copied from this PC next to python.exe. A new PC without the "Visual C++ Redistributable" otherwise
# fails with "Could not find module ctranslate2.dll (or one of its dependencies)" (12/11/2026). All five are taken from the same
# build (the portable Python brings an older vcruntime140 that does not match a newer msvcp140). Microsoft allows shipping these.
VC_RUNTIME_DLLS = ('msvcp140.dll', 'msvcp140_1.dll', 'vcomp140.dll', 'vcruntime140.dll', 'vcruntime140_1.dll')
MODEL_DIRS = (  # relative to models/
    'whisper/models--Systran--faster-whisper-small',
    'whisper/models--Systran--faster-whisper-medium',
    'huggingface/hub/models--pnnbao-ump--VieNeu-TTS-v3-Turbo',
    'huggingface/hub/models--pnnbao-ump--VieNeu-TTS-v3-Nano',
    'huggingface/hub/models--OpenMOSS-Team--MOSS-Audio-Tokenizer-Nano-ONNX',
)
README_TEXT = """TÈO DUB STUDIO - HƯỚNG DẪN MỞ LẦN ĐẦU

1. Giải nén thư mục "Teo" này vào một chỗ có đường dẫn NGẮN, ví dụ C:\\Teo (đừng để trong Downloads hay Desktop sâu).
2. Bấm đúp "Teo Dub Studio.cmd".
3. Ở lần đầu, cửa sổ "Kiểm tra máy" có thể hiện lên: cài Google Chrome nếu thiếu, bấm "Đăng nhập ChatGPT" và dùng
   tài khoản ChatGPT CỦA BẠN (mỗi người một tài khoản), rồi bấm "Dịch thử" để chắc chắn dịch được.
4. Dữ liệu và đăng nhập nằm trên máy này, không chia sẻ với ai. Để cập nhật, bấm "Cập nhật phiên bản" trong Tèo.
"""
LAUNCHER = ('@echo off\r\nsetlocal DisableDelayedExpansion\r\nchcp 65001 >nul\r\nset "TEODUB_PROJECT=%~dp0"\r\n'
            'set "TEODUB_PYTHONW=%TEODUB_PROJECT%runtime\\python\\pythonw.exe"\r\n'
            'if not exist "%TEODUB_PYTHONW%" (\r\n  echo Thiếu runtime\\python. Hãy giải nén lại đầy đủ bộ Tèo.\r\n  pause\r\n  exit /b 1\r\n)\r\n'
            'set "PATH=%TEODUB_PROJECT%tools\\node-runtime;%PATH%"\r\n'
            'start "" "%TEODUB_PYTHONW%" -X utf8 -B "%TEODUB_PROJECT%launch.py"\r\nexit /b 0\r\n')


def cpu_requirements(lines):
    """The lock without NVIDIA wheels: the bundle runs Whisper on the CPU."""
    return [line for line in lines if line.strip() and not line.strip().lower().startswith('nvidia-')]


def model_sources(models_dir):
    """The whitelisted model folders that exist, and the ones that do not (reported, never silently skipped)."""
    found, missing = [], []
    for relative in MODEL_DIRS:
        (found if (Path(models_dir)/relative).is_dir() else missing).append(relative)
    return found, missing


def codex_folder(local_appdata=None):
    root = Path(local_appdata or os.environ.get('LOCALAPPDATA', ''))/'OpenAI'/'Codex'/'bin'
    candidates = sorted(root.glob('*/codex.exe'), key=lambda path: path.stat().st_mtime, reverse=True)
    return candidates[0].parent if candidates else None


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(1024*1024), b''):
            digest.update(block)
    return digest.hexdigest()


def download(url, target):
    request = urllib.request.Request(url, headers={'User-Agent': 'TeoDubStudio-Package'})
    with urllib.request.urlopen(request, timeout=120) as response, open(target, 'wb') as output:
        shutil.copyfileobj(response, output)


def build_runtime(work, requirements_lines, say=print):
    """Portable Python + CPU libraries under `work`/runtime/python. Reused when it already exists and is complete."""
    work = Path(work)
    python_dir = work/'runtime'/'python'
    marker = work/'runtime'/'built.json'
    wanted = hashlib.sha256('\n'.join(requirements_lines).encode()).hexdigest()
    if marker.is_file() and json.loads(marker.read_text(encoding='utf-8')).get('requirements') == wanted:
        say('Dùng lại runtime Python đã dựng.')
        return python_dir
    shutil.rmtree(work/'runtime', ignore_errors=True)
    (work/'runtime').mkdir(parents=True)
    archive = work/PBS_FILE.replace('+', '_')
    say('Tải Python độc lập…')
    download(PBS_BASE+PBS_FILE.replace('+', '%2B'), archive)
    sums = work/'SHA256SUMS'
    download(PBS_BASE+'SHA256SUMS', sums)
    expected = next((line.split()[0] for line in sums.read_text(encoding='utf-8').splitlines()
                     if line.strip().endswith(PBS_FILE)), None)
    if not expected or sha256_file(archive) != expected:
        raise SystemExit('Python tải về không khớp SHA256; dừng.')
    with tarfile.open(archive) as source:
        source.extractall(work/'runtime', filter='data')
    if not (python_dir/'python.exe').is_file():
        raise SystemExit('Giải nén Python xong nhưng không thấy python.exe.')
    requirements = work/'requirements-cpu.txt'
    requirements.write_text('\n'.join(requirements_lines)+'\n', encoding='utf-8')
    say('Cài thư viện (bản CPU)… (vài phút)')
    done = subprocess.run([str(python_dir/'python.exe'), '-X', 'utf8', '-m', 'pip', 'install', '--no-warn-script-location',
                           '--disable-pip-version-check', '-r', str(requirements)])
    if done.returncode:
        raise SystemExit('Cài thư viện bị lỗi; xem thông báo ở trên.')
    marker.write_text(json.dumps(dict(requirements=wanted, built=time.strftime('%Y-%m-%d %H:%M:%S'))), encoding='utf-8')
    return python_dir


def system_runtime_dlls(system_dir=None):
    """The Visual C++ runtime files of this PC, as (path, file name); stops when one is missing."""
    folder = Path(system_dir) if system_dir else Path(os.environ.get('SystemRoot', r'C:\Windows'))/'System32'
    missing = [name for name in VC_RUNTIME_DLLS if not (folder/name).is_file()]
    if missing:
        raise SystemExit('Máy này thiếu '+', '.join(missing)+f' trong {folder}. Cài "Microsoft Visual C++ Redistributable" '
                         '(https://aka.ms/vs/17/release/vc_redist.x64.exe) rồi đóng gói lại.')
    return [(folder/name, name) for name in VC_RUNTIME_DLLS]


def iter_files(project, python_dir, models, models_dir, codex, node_runtime, include_models=True, include_runtime=True,
               system_dlls=()):
    """(source Path, archive name) for everything that goes into the bundle. `system_dlls`: (path, name) of files that go next
    to python.exe and replace a file of the same name that came with Python."""
    project = Path(project)
    for name in CODE_ITEMS:
        path = project/name
        if path.is_dir():
            for item in sorted(path.rglob('*')):
                if item.is_file() and '__pycache__' not in item.parts and item.suffix != '.pyc':
                    yield item, f'{TOP}/{item.relative_to(project).as_posix()}'
        elif path.is_file():
            yield path, f'{TOP}/{name}'
    if include_runtime and python_dir:
        replaced = {name.lower() for _, name in system_dlls}
        for item in sorted(Path(python_dir).rglob('*')):
            if item.is_file() and '__pycache__' not in item.parts:
                relative = item.relative_to(python_dir).as_posix()
                if relative.lower() not in replaced:
                    yield item, f'{TOP}/runtime/python/{relative}'
        for source, name in system_dlls:
            yield source, f'{TOP}/runtime/python/{name}'
    if include_models:
        for relative in models:
            base = Path(models_dir)/relative
            for item in sorted(base.rglob('*')):
                if item.is_file():
                    yield item, f'{TOP}/models/{item.relative_to(models_dir).as_posix()}'
    if codex:
        for item in sorted(Path(codex).glob('*.exe')):
            yield item, f'{TOP}/tools/codex/{item.name}'
    if node_runtime and Path(node_runtime).is_dir():
        for item in sorted(Path(node_runtime).rglob('*')):
            if item.is_file():
                yield item, f'{TOP}/tools/node-runtime/{item.relative_to(node_runtime).as_posix()}'


def write_zip(files, out_zip, say=print):
    out_zip = Path(out_zip)
    out_zip.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with zipfile.ZipFile(out_zip, 'w', zipfile.ZIP_DEFLATED, compresslevel=1, allowZip64=True) as archive:
        archive.writestr(f'{TOP}/Teo Dub Studio.cmd', LAUNCHER.encode('utf-8'))
        archive.writestr(f'{TOP}/DOC-TRUOC-KHI-MO.txt', README_TEXT.replace('\n', '\r\n').encode('utf-8'))
        for source, name in files:
            archive.write(source, name)
            count += 1
            if count % 2000 == 0:
                say(f'  …{count} tệp')
    return count


def main(argv=None):
    parser = argparse.ArgumentParser(description='Đóng gói bộ Tèo đầy đủ.')
    parser.add_argument('--out', default=str(PROJECT/'dist-bundle'))
    parser.add_argument('--work', default=r'C:\TeoBuild')
    parser.add_argument('--no-models', action='store_true')
    parser.add_argument('--no-runtime', action='store_true')
    parser.add_argument('--dry-run', action='store_true', help='Chỉ liệt kê và đếm, không dựng, không nén.')
    options = parser.parse_args(argv)
    version = json.loads((PROJECT/'version.json').read_text(encoding='utf-8'))['version']
    lock = (PROJECT/'requirements-lock.txt').read_text(encoding='utf-8').splitlines()
    models_dir = PROJECT/'models'
    found, missing = model_sources(models_dir)
    codex = codex_folder()
    print(f'Phiên bản {version}. Mô hình tìm thấy: {len(found)}/{len(MODEL_DIRS)}; Codex: {codex or "KHÔNG THẤY"}.')
    if missing and not options.no_models:
        raise SystemExit('Thiếu mô hình trong models/: '+', '.join(missing))
    if codex is None:
        raise SystemExit('Không thấy codex.exe (cài Codex trên máy này để chép vào bộ).')
    node = PROJECT/'tools'/'node-runtime'
    python_dir = None
    if not options.no_runtime and not options.dry_run:
        python_dir = build_runtime(options.work, cpu_requirements(lock))
    system_dlls = system_runtime_dlls() if python_dir is not None else ()
    files = list(iter_files(PROJECT, python_dir, found, models_dir, codex, node, not options.no_models, python_dir is not None,
                            system_dlls))
    total = sum(source.stat().st_size for source, _ in files)
    print(f'{len(files)} tệp, khoảng {total/1e9:.2f} GB trước khi nén.')
    if options.dry_run:
        return 0
    target = Path(options.out)/f'TeoDubStudio-{version}.zip'
    print(f'Nén vào {target} … (có thể vài chục phút)')
    write_zip(files, target)
    print(f'Xong: {target} ({target.stat().st_size/1e9:.2f} GB), SHA256 {sha256_file(target)}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
