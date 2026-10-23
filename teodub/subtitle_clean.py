"""Opt-in GPU inpainting, frozen per job; originals are never edited."""
import json
import math
from pathlib import Path
import subprocess
import time
import uuid

from teodub.common import AwakeTime, atomic_json, check_cancel, hash_file

ROOT = Path(__file__).resolve().parents[1]
MODEL = ROOT/'models/inpaint/sttn.pth'
MODEL_SHA = '25b0c2c30042d82efd1893bd42ec726764262d94115393a1718f8d65d2a7817b'
ENGINE = 'sttn-masked-v1'
PYTHON = ROOT/'.venv-inpaint/Scripts/python.exe'


def validate_clean(config):
    value = config.get('subtitle_clean', {})
    if not isinstance(value, dict) or type(value.get('enabled', False)) is not bool:
        raise ValueError('Cấu hình xóa chữ không hợp lệ.')
    if not value.get('enabled', False):
        return dict(enabled=False)
    if value.get('engine', ENGINE) != ENGINE:
        raise ValueError('Phiên này dùng bộ xóa chữ khác; không tự đổi mô hình.')
    region = value.get('region')
    if (not isinstance(region, (list, tuple)) or len(region) != 4 or
            any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in region)):
        raise ValueError('Hãy khoanh vùng chữ gốc trước khi bật xóa chữ.')
    x1, y1, x2, y2 = region
    if not (0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1) or (x2-x1)*(y2-y1) > .25:
        raise ValueError('Chỉ khoanh vùng phụ đề, tối đa 25% diện tích hình.')
    result = dict(enabled=True, engine=ENGINE, region=[round(v, 6) for v in region])
    if 'source_sha256' in value or 'source_path' in value:
        digest, path = value.get('source_sha256'), value.get('source_path')
        if (not isinstance(digest, str) or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest)
                or not isinstance(path, str) or not Path(path).is_absolute()):
            raise ValueError('Cấu hình vùng xóa chưa gắn đúng video nguồn.')
        result.update(source_sha256=digest, source_path=path)
    return result


def verify_clean_source(source, settings):
    if settings.get('enabled') and settings.get('source_sha256'):
        if hash_file(source) != settings['source_sha256']:
            raise ValueError('Vùng xóa Sub được lưu cho video khác hoặc nguồn đã thay đổi. Chưa chạy dịch/lồng tiếng.')


def runtime_ready():
    if not PYTHON.is_file() or not MODEL.is_file():
        raise ValueError('Chưa cài bộ xóa chữ GPU. Chạy tools/setup_inpaint.ps1 trong thư mục Tèo.')
    if hash_file(MODEL) != MODEL_SHA:
        raise ValueError('Mô hình xóa chữ bị thay đổi; cần cài lại đúng bản STTN.')


def preflight():
    runtime_ready()
    process = subprocess.run([str(PYTHON), '-X', 'utf8', '-B', '-m', 'teodub.inpaint_worker', '--check'],
                             cwd=ROOT, capture_output=True, text=True, encoding='utf-8', timeout=45,
                             creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if process.returncode:
        raise ValueError('Bộ xóa chữ GPU chưa sẵn sàng: '+(process.stderr or process.stdout)[-1200:])
    return json.loads(process.stdout)


def clean_video(source, config, directory, *, cancel=None, progress=None):
    settings = validate_clean(config)
    if not settings['enabled']:
        return None
    check_cancel(cancel)
    source = Path(source).resolve(strict=True)
    source_sha = hash_file(source)
    verify_clean_source(source, settings)
    binding = dict(source_sha256=source_sha, settings=settings, model_sha256=MODEL_SHA)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    manifest, target = directory/'clean.json', directory/'picture.mkv'
    if manifest.exists():
        saved = json.loads(manifest.read_text(encoding='utf-8'))
        if saved.get('binding') != binding or not target.is_file() or hash_file(target) != saved.get('sha256'):
            raise ValueError('Bản xóa chữ đã lưu không khớp video/cấu hình; không dùng lẫn bộ nhớ đệm.')
        return dict(path=str(target.resolve()), **saved)
    runtime_ready()
    run_id = uuid.uuid4().hex
    partial = directory/f'{run_id}.partial.mkv'
    request = directory/f'{run_id}.json'
    status = directory/f'{run_id}.status.json'
    log = directory/f'{run_id}.log'
    atomic_json(request, dict(source=str(source), target=str(partial.resolve()),
                             region=settings['region'], status=str(status.resolve()), model=str(MODEL)))
    process = None
    try:
        with log.open('w', encoding='utf-8') as error:
            process = subprocess.Popen([str(PYTHON), '-X', 'utf8', '-B', '-m', 'teodub.inpaint_worker', str(request.resolve())],
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
                        progress(f'GPU đang xóa chữ: {update.get("frames", 0)} khung hình ({update.get("percent", 0):.0f}%).',
                                 percent=update.get('percent', 0))
                if unchanged.update(now) > 300:
                    raise ValueError('GPU không cập nhật sau 5 phút. Đã dừng an toàn; có thể tiếp tục phiên.')
                time.sleep(.2)
        if process.returncode:
            message = log.read_text(encoding='utf-8')[-1800:]
            raise ValueError('Xóa chữ GPU chưa thành công; không xuất bản thiếu bước này. '+message)
        report = json.loads(status.read_text(encoding='utf-8'))
        if report.get('status') != 'completed' or hash_file(source) != source_sha:
            raise ValueError('Xóa chữ chưa hoàn tất hoặc nguồn đã thay đổi.')
        check_cancel(cancel)
        # Only a complete model result gets a committed manifest. Retry can replace an orphan.
        partial.replace(target)
        saved = dict(binding=binding, sha256=hash_file(target), report=report)
        atomic_json(manifest, saved)
        return dict(path=str(target.resolve()), **saved)
    finally:
        if process and process.poll() is None:
            process.terminate()
            process.wait(timeout=15)
        if partial.exists():
            partial.unlink()
