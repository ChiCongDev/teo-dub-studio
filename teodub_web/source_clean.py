"""Source-bound manual inpainting for authenticated web jobs."""
import json
from pathlib import Path
import re

from teodub.common import atomic_json, hash_file
from teodub.subtitle_clean import validate_clean


def clean_options(data):
    value = data.get('subtitle_clean', {'enabled': False})
    if not isinstance(value, dict) or set(value)-{'enabled', 'region'}:
        raise ValueError('Vùng xóa chữ chỉ nhận lựa chọn bật/tắt và vùng khoanh.')
    return validate_clean({'subtitle_clean': value})


def source_record(store, job):
    """Metadata for UI only; serving/using media additionally checks its hash."""
    if not job or not re.fullmatch('[a-f0-9]{32}', job['id']) or job['state'] not in ('completed', 'review_required'):
        raise ValueError('Đợi tải xong video gốc trước khi khoanh vùng.')
    directory = store.root/'jobs'/job['id']
    state = json.loads((directory/'state.json').read_text(encoding='utf-8'))
    result = json.loads((directory/'result.json').read_text(encoding='utf-8'))
    config = json.loads((directory/'config.json').read_text(encoding='utf-8'))
    source = state['source']
    if (result.get('status') not in ('completed', 'review_required') or
            config.get('directory') != str(directory) or
            config.get('original_dir') != str(store.root/'originals')):
        raise ValueError('Video gốc chưa được kiểm chứng.')
    path = Path(source['path'])
    digest = source.get('sha256')
    if (not path.is_absolute() or not path.is_file() or path.is_symlink() or
            not path.resolve().is_relative_to((store.root/'originals').resolve()) or
            path.suffix.lower() != '.mp4' or not isinstance(digest, str) or
            not re.fullmatch('[a-f0-9]{64}', digest)):
        raise ValueError('Video gốc không nằm trong nơi lưu của Tèo.')
    if config.get('mode') == 'download' and result.get('outputs', {}).get('source') != str(path):
        raise ValueError('Video gốc không khớp phiên tải.')
    return source


def verified_source(store, job):
    source = source_record(store, job)
    if hash_file(source['path']) != source['sha256']:
        raise ValueError('Video gốc đã thay đổi. Hãy tải và khoanh vùng lại.')
    return source


def prepared_source(store, owner, ident, url):
    job = store.job(ident, {'id': owner, 'role': 'staff'})
    if not job or job['input'] != url:
        raise ValueError('Vùng xóa không thuộc video / tài khoản đang chọn. Hãy tải và khoanh vùng lại.')
    return verified_source(store, job)


def saved_region(store, job, source):
    try:
        value = json.loads((store.root/'jobs'/job['id']/'web-clean-region.json').read_text(encoding='utf-8'))
        if value['source_sha256'] != source['sha256'] or value['owner'] != job['owner']:
            return None
        return clean_options({'subtitle_clean':dict(enabled=True, region=value['region'])})['region']
    except (OSError, ValueError, KeyError, TypeError):
        return None


def save_region(store, job, data):
    if not isinstance(data, dict) or set(data) != {'subtitle_clean'}:
        raise ValueError('Chỉ lưu vùng chữ đã khoanh cho video này.')
    clean = clean_options(data)
    if not clean['enabled']:
        raise ValueError('Hãy khoanh vùng chữ trước khi lưu.')
    source = verified_source(store, job)
    atomic_json(store.root/'jobs'/job['id']/'web-clean-region.json', dict(version=1,
        owner=job['owner'], source_sha256=source['sha256'], region=clean['region']))
    return clean['region']
