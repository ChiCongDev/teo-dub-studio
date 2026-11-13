"""Bỏ giọng nói gốc, giữ nhạc nền và tiếng động (owner 13/11/2026).

The video's own sound is split locally (Demucs htdemucs, CPU, in the separate environment of the GPU subtitle remover) into the voices and
the rest. The rest replaces the original sound in the export; the Vietnamese voice, the Sub and the levels work as before. The result
is frozen per job in a directory with its binding (source video hash, model hash, engine), so a remake of a finished video reuses it
and nothing is mixed from another video. The original video is never edited. Opt-in: the choice is `remove_original_voice`."""
import json
from pathlib import Path
import subprocess
import time
import uuid

from teodub.common import AwakeTime, atomic_json, check_cancel, hash_file

ROOT = Path(__file__).resolve().parents[1]
MODEL = ROOT/'models/demucs/htdemucs-955717e8.th'
MODEL_SHA = '8726e21a993978c7ba086d3872e7608d7d5bfca646ca4aca459ffda844faa8b4'
MODEL_URL = 'https://dl.fbaipublicfiles.com/demucs/hybrid_transformer/955717e8-8726e21a.th'
ENGINE = 'htdemucs-cpu-v1'
PYTHON = ROOT/'.venv-inpaint/Scripts/python.exe'
STALL_SECONDS = 300  # No progress for this long: the work is stopped safely.


def installed():
    """Whether the tool is set up on this PC (the checkbox is only usable then)."""
    return PYTHON.is_file() and MODEL.is_file()


def runtime_ready():
    if not PYTHON.is_file() or not MODEL.is_file():
        raise ValueError('Chưa cài bộ tách giọng nói. Chạy tools/setup_inpaint.ps1 trong thư mục Tèo (cần mạng lần đầu để tải mô hình).')
    if hash_file(MODEL) != MODEL_SHA:
        raise ValueError('Mô hình tách giọng bị thay đổi; cần cài lại đúng bản htdemucs (tools/setup_inpaint.ps1).')


def preflight():
    runtime_ready()
    process = subprocess.run([str(PYTHON), '-X', 'utf8', '-B', '-m', 'teodub.voice_removal_worker', '--check'],
                             cwd=ROOT, capture_output=True, text=True, encoding='utf-8', timeout=90,
                             creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if process.returncode:
        raise ValueError('Bộ tách giọng nói chưa sẵn sàng: '+(process.stderr or process.stdout)[-1200:])
    return json.loads(process.stdout)


def verify_background(saved, source_sha256):
    """A saved result (the state of a job, or a manifest) is used only when it belongs to this video and is unchanged."""
    if (not isinstance(saved, dict) or not isinstance(saved.get('binding'), dict)
            or saved['binding'].get('source_sha256') != source_sha256 or saved['binding'].get('engine') != ENGINE
            or saved['binding'].get('model_sha256') != MODEL_SHA):
        raise ValueError('Bản tách giọng đã lưu không khớp video/mô hình; không dùng lẫn bộ nhớ đệm.')
    path = Path(saved['path'])
    if not path.is_file() or hash_file(path) != saved.get('sha256'):
        raise ValueError('Bản nhạc nền đã tách bị thiếu hoặc đã thay đổi.')
    return saved


def separate(source, directory, *, cancel=None, progress=None):
    """Return dict(path, sha256, binding, report): the sound of `source` without its voices (44.1 kHz stereo WAV)."""
    check_cancel(cancel)
    source = Path(source).resolve(strict=True)
    source_sha = hash_file(source)
    binding = dict(source_sha256=source_sha, engine=ENGINE, model_sha256=MODEL_SHA)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    manifest, target = directory/'background.json', directory/'background.wav'
    if manifest.exists():
        saved = json.loads(manifest.read_text(encoding='utf-8'))
        if saved.get('binding') != binding or not target.is_file() or hash_file(target) != saved.get('sha256'):
            raise ValueError('Bản tách giọng đã lưu không khớp video/mô hình; không dùng lẫn bộ nhớ đệm.')
        return dict(path=str(target.resolve()), **saved)
    runtime_ready()
    run_id = uuid.uuid4().hex
    partial = directory/f'{run_id}.partial.wav'
    request = directory/f'{run_id}.json'
    status = directory/f'{run_id}.status.json'
    log = directory/f'{run_id}.log'
    atomic_json(request, dict(source=str(source), target=str(partial.resolve()), status=str(status.resolve()), model=str(MODEL),
                              model_sha256=MODEL_SHA))
    process = None
    try:
        with log.open('w', encoding='utf-8') as error:
            process = subprocess.Popen([str(PYTHON), '-X', 'utf8', '-B', '-m', 'teodub.voice_removal_worker', str(request.resolve())],
                                       cwd=ROOT, stdout=error, stderr=error, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            previous, unchanged = None, AwakeTime(time.monotonic())
            while process.poll() is None:
                check_cancel(cancel)
                try:
                    update = json.loads(status.read_text(encoding='utf-8'))
                except (OSError, ValueError):
                    update = None
                now = time.monotonic()
                if update != previous:
                    unchanged = AwakeTime(now)
                    previous = update
                    if progress and update:
                        progress(f'Đang tách giọng nói gốc khỏi nhạc nền và tiếng động ({update.get("percent", 0):.0f}%).',
                                 percent=update.get('percent', 0))
                if unchanged.update(now) > STALL_SECONDS:
                    raise ValueError('Bộ tách giọng không cập nhật sau 5 phút. Đã dừng an toàn; có thể tiếp tục phiên.')
                time.sleep(.2)
        if process.returncode:
            raise ValueError('Tách giọng nói chưa thành công; không xuất bản thiếu bước này. '+log.read_text(encoding='utf-8')[-1800:])
        report = json.loads(status.read_text(encoding='utf-8'))
        if report.get('status') != 'completed' or hash_file(source) != source_sha:
            raise ValueError('Tách giọng chưa hoàn tất hoặc nguồn đã thay đổi.')
        check_cancel(cancel)
        partial.replace(target)  # Only a complete result gets a committed manifest; a retry can replace an orphan.
        saved = dict(binding=binding, sha256=hash_file(target), report=report)
        atomic_json(manifest, saved)
        return dict(path=str(target.resolve()), **saved)
    finally:
        if process and process.poll() is None:
            process.terminate()
            process.wait(timeout=15)
        if partial.exists():
            partial.unlink()
        for leftover in (request, status):
            leftover.unlink(missing_ok=True)
