"""Download a public Douyin/TikTok source without an editor or native UI.

Run this in the application's dedicated worker process. Browser providers use
their own headless browser and never attach to the user's browser profile.
"""
import json
import math
from pathlib import Path
import re
import uuid

from teodub.common import Cancelled, atomic_json, check_cancel, hash_file
from teodub.downloaders.video_download import download as _download, share_url


def _absolute_directory(value, label):
    path = Path(value)
    if not path.is_absolute():
        raise ValueError(label + ' phải là đường dẫn đầy đủ.')
    return path.resolve()


def _source_result(result, folder):
    if not isinstance(result, dict) or result.get('status') != 'downloaded':
        raise ValueError('Chưa có video tải hoàn chỉnh.')
    raw = Path(result.get('video', ''))
    if (not raw.is_absolute() or raw.suffix.lower() != '.mp4' or raw.is_symlink()
            or getattr(raw, 'is_junction', lambda: False)()):
        raise ValueError('Đường dẫn video tải về không hợp lệ.')
    source = raw.resolve(strict=True)
    if source.parent != folder or not source.is_file() or not source.stat().st_size:
        raise ValueError('Video tải về không thuộc đúng thư mục của lượt tải.')
    metadata = result.get('metadata')
    if (not isinstance(metadata, dict) or not isinstance(metadata.get('sha256'), str)
            or not re.fullmatch('[0-9a-f]{64}', metadata['sha256'])):
        raise ValueError('Video tải về thiếu dấu vân tay đã kiểm tra.')
    for key in ('duration', 'width', 'height'):
        value = metadata.get(key)
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise ValueError('Video tải về thiếu thông số hình hoặc thời lượng hợp lệ.')
    if metadata.get('path') and Path(metadata['path']).resolve() != source:
        raise ValueError('Thông tin nguồn không khớp video tải về.')
    if hash_file(source) != metadata['sha256']:
        raise ValueError('Video đã thay đổi sau khi kiểm tra tải xuống.')
    return dict(result, source_path=str(source), metadata=dict(metadata, path=str(source)))


def download_video(text, output_root, job_dir, *, cancel=None, progress=None):
    """Return a verified source, or raise; never retry an existing download job.

    The selected originals root contains ``Teo-original-<id>`` directories and
    their cache reports. Only matching, hash-verified sibling jobs are reused.
    ``job_dir/download`` is reserved once and contains this attempt's records.
    Progress uses ``callback(message, percent=None)``; unknown totals stay None.
    """
    check_cancel(cancel)
    url = share_url(text)
    root = _absolute_directory(output_root, 'Thư mục video gốc')
    job = _absolute_directory(job_dir, 'Thư mục công việc')
    job.mkdir(parents=True, exist_ok=True)
    workspace = job/'download'
    workspace.mkdir(exist_ok=False)
    original = root/('Teo-original-'+uuid.uuid4().hex)
    stop = Path(getattr(cancel, 'path', None) or workspace/'stop')
    config = dict(url=url, folder=str(original), stop_file=str(stop.resolve()))
    atomic_json(workspace/'request.json', config)

    def emit(message, **details):
        with (workspace/'events.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(dict(message=message, **details), ensure_ascii=False)+'\n')
        if progress is not None:
            progress(message, percent=details.get('percent'))

    try:
        root.mkdir(parents=True, exist_ok=True)
        check_cancel(cancel)
        result = _download(config, cancel=cancel, progress_callback=emit)
        check_cancel(cancel)
        result = _source_result(result, original.resolve(strict=True))
        check_cancel(cancel)
        # Cache publication occurs only after validation and cancellation gates.
        atomic_json(original/'result.json', result)
        atomic_json(workspace/'result.json', result)
        return result
    except Exception as exc:
        # Preserve the cancellation reason even if a provider wrapped it.
        try:
            check_cancel(cancel)
        except Cancelled as cancelled:
            exc = cancelled
        failure = dict(status='cancelled' if isinstance(exc, Cancelled) else 'failed',
                       url=url, message=str(exc) or type(exc).__name__)
        for key in ('provider', 'reason', 'retry_after_text', 'attempts'):
            if hasattr(exc, key):
                failure['provider_attempts' if key == 'attempts' else key] = getattr(exc, key)
        atomic_json(workspace/'result.json', failure)
        if original.is_dir():
            atomic_json(original/'result.json', failure)
        raise exc
