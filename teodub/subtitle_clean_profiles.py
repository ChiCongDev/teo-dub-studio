"""Saved removal regions belong to one source video, independently of enablement."""
import hashlib
import json
import os
from pathlib import Path

from teodub.common import atomic_json, hash_file
from teodub.subtitle_clean import validate_clean


def profile_path(data_dir, source):
    normalized = os.path.normcase(str(Path(source).resolve()))
    key = hashlib.sha256(normalized.encode('utf-8')).hexdigest()
    return Path(data_dir)/'subtitle-clean-profiles'/f'{key}.json'


def save_profile(data_dir, source, options):
    source = Path(source).resolve(strict=True)
    if not source.is_file():
        raise ValueError('Chọn video gốc đã tải trước khi lưu vùng xóa.')
    # The region is saved even if the main-screen checkbox is off.
    settings = validate_clean({'subtitle_clean': dict(options, enabled=True)})
    stamp = source.stat()
    digest = hash_file(source)
    if (stamp.st_size, stamp.st_mtime_ns) != (source.stat().st_size, source.stat().st_mtime_ns):
        raise ValueError('Video đang thay đổi; đợi tải xong rồi lưu lại vùng.')
    settings.update(source_path=str(source), source_sha256=digest)
    record = dict(version=1, settings=settings, enabled=False,
                  size=stamp.st_size, mtime_ns=stamp.st_mtime_ns)
    atomic_json(profile_path(data_dir, source), record)
    return dict(settings, enabled=False)


def load_profile(data_dir, source, *, verify_hash=False):
    source = Path(source).resolve(strict=True)
    target = profile_path(data_dir, source)
    if not target.exists():
        return dict(enabled=False)
    record = json.loads(target.read_text(encoding='utf-8'))
    settings = validate_clean({'subtitle_clean': dict(record['settings'], enabled=True)})
    if Path(settings['source_path']).resolve() != source:
        raise ValueError('Vùng xóa đã lưu thuộc video khác.')
    stamp = source.stat()
    if (stamp.st_size, stamp.st_mtime_ns) != (record['size'], record['mtime_ns']):
        raise ValueError('Video đã thay đổi sau khi lưu vùng. Hãy khoanh và lưu lại.')
    if verify_hash and hash_file(source) != settings['source_sha256']:
        raise ValueError('Nội dung video không khớp vùng đã lưu; cần lưu lại cấu hình.')
    if type(record.get('enabled')) is not bool:
        raise ValueError('Trạng thái xóa Sub đã lưu không hợp lệ.')
    return dict(settings, enabled=record['enabled'])


def set_enabled(data_dir, source, enabled):
    options = load_profile(data_dir, source, verify_hash=enabled)
    if not options.get('region'):
        raise ValueError('Hãy lưu vùng xóa Sub cho video này trước khi tick áp dụng.')
    target = profile_path(data_dir, source)
    record = json.loads(target.read_text(encoding='utf-8'))
    record['enabled'] = bool(enabled)
    atomic_json(target, record)
    return dict(options, enabled=bool(enabled))
