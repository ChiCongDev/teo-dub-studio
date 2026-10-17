"""Explicit future settings, applied between videos into isolated revision jobs."""
from datetime import datetime
import json
from pathlib import Path
import re
import uuid

from teodub.common import atomic_json, hash_file


def effective_job(directory, item):
    from teodub.campaign_batch import _plain_path
    directory = Path(directory).resolve()
    if type(item['number']) is not int or item['number'] < 1:
        raise ValueError('Số thứ tự video không hợp lệ.')
    base = directory/'jobs'/f'{item["number"]:04}'
    if Path(item['job']) != base:
        raise ValueError('Phiên video không khớp chiến dịch.')
    _plain_path(directory/'jobs')
    _plain_path(base)
    revision = item.get('execution_job')
    if not revision:
        return base
    path = Path(revision)
    if path.parent != base/'revisions' or not re.fullmatch(r'[0-9a-f]{32}', path.name):
        raise ValueError('Phiên cấu hình mới không thuộc video này.')
    _plain_path(base/'revisions')
    return _plain_path(path)


def validate_settings(settings):
    from teodub.campaign import CONFIG_KEYS
    from teodub.ai_models import validate_models
    from teodub.audio_mix import validate_audio_mix
    from teodub.subtitle_style import validate_subtitle_style
    from teodub.subtitle_clean import validate_clean
    from teodub.tts import validate_selection, selection_options
    from teodub.wording import validate_wording
    from teodub.drive_delivery import drive_delivery_settings
    selected = {key: settings[key] for key in CONFIG_KEYS if key in settings}
    selected.update(validate_models(selected), **validate_audio_mix(selected), **validate_subtitle_style(selected))
    selected['wording'] = validate_wording(selected)
    selected['subtitle_clean'] = validate_clean(selected)
    selected['drive_upload'] = drive_delivery_settings(selected)
    validate_selection(selected['voice'], **selection_options(selected))
    if selected.get('speed', .9) not in (.9, 1.0):
        raise ValueError('Tốc độ hình không hợp lệ.')
    return selected


def save_next_settings(data_dir, directory, settings):
    from teodub.campaign_batch import _campaign_path, _plain_path
    from teodub.campaign import read_json, DONE, is_running
    from teodub.pipeline import _job_lock
    directory = _campaign_path(Path(data_dir).resolve(), Path(directory).resolve())
    manifest = read_json(directory/'campaign.json')
    if manifest.get('archived_at'):
        raise ValueError('Khôi phục chiến dịch trước khi đổi cấu hình.')
    remaining = [row for row in manifest['items'] if row['status'] not in DONE]
    if not remaining:
        raise ValueError('Chiến dịch đã hoàn tất; không còn video để áp dụng cấu hình mới.')
    if is_running(directory):
        try:
            runtime = read_json(directory/'campaign-runtime.json')
        except (OSError, ValueError):
            runtime = {}
        if runtime.get('future_settings') != 1:
            raise ValueError('Worker đang chạy là bản cũ. Dừng chiến dịch rồi khởi động lại giữ công việc trước khi đổi cấu hình.')
    selected = validate_settings(settings)
    from teodub.drive_delivery import drive_delivery_settings
    selected['drive_upload'] = drive_delivery_settings(selected, data_dir)
    if selected['drive_upload']['enabled']:
        selected['drive_data_dir'] = str(Path(data_dir).resolve())
    root = _plain_path(directory/'settings-updates')
    root.mkdir(exist_ok=True)
    with _job_lock(root):
        ident = uuid.uuid4().hex
        record = dict(version=1, id=ident, campaign_id=manifest['id'], settings=selected,
                      created_at=datetime.now().astimezone().isoformat(timespec='seconds'))
        path = root/(ident+'.json')
        atomic_json(path, record)
        atomic_json(directory/'next-settings.json', dict(id=ident, sha256=hash_file(path)))
    return record


def next_settings(directory):
    from teodub.campaign_batch import _plain_path
    directory = Path(directory).resolve()
    pointer = _plain_path(directory/'next-settings.json')
    if not pointer.exists():
        return None
    saved = json.loads(pointer.read_text(encoding='utf-8'))
    ident = saved.get('id')
    if not isinstance(ident, str) or not re.fullmatch(r'[0-9a-f]{32}', ident):
        raise ValueError('Mã cấu hình tiếp theo không hợp lệ.')
    root = _plain_path(directory/'settings-updates')
    path = _plain_path(root/(ident+'.json'))
    data = path.read_bytes()
    import hashlib
    if hashlib.sha256(data).hexdigest() != saved.get('sha256'):
        raise ValueError('Cấu hình tiếp theo đã bị thay đổi; cần kiểm tra.')
    result = json.loads(data.decode('utf-8'))
    if result.get('version') != 1 or result.get('id') != ident or result.get('campaign_id') != directory.name:
        raise ValueError('Cấu hình tiếp theo không khớp chiến dịch.')
    result['settings'] = validate_settings(result['settings'])
    return result


def save_next_voice_settings(data_dir, directory, selection):
    """Change only voice choices; retain this campaign's other pending settings."""
    from teodub.campaign_batch import _campaign_path
    from teodub.campaign import read_json
    from teodub.campaign_preview import next_video_config
    keys = {'voice_provider', 'voice', 'reference_audio', 'reference_sha256',
            'reading_style', 'voice_speed', 'tts_model'}
    if set(selection) != keys:
        raise ValueError('Cấu hình giọng phải có đúng mẫu, phong cách, tốc độ và bộ giọng.')
    directory = _campaign_path(Path(data_dir).resolve(), Path(directory).resolve())
    manifest = read_json(directory/'campaign.json')
    # Verify the actual job even when a queued override already exists. Do not
    # repair a broken campaign merely by installing another reference sample.
    preview = next_video_config(directory, manifest, retry_failed=True, active=True)
    if preview is None:
        raise ValueError('Chiến dịch đã hoàn tất; không còn video để áp dụng giọng mới.')
    update = next_settings(directory)
    settings = dict(update['settings'] if update else preview['config'])
    settings.update(selection)
    return save_next_settings(data_dir, directory, settings)


def proposed_config(directory, item, config, update):
    from teodub.campaign import CONFIG_KEYS
    if not update:
        return dict(config)
    base = Path(item['job'])
    target = base/'revisions'/update['id']
    result = {key: value for key, value in config.items() if key not in CONFIG_KEYS}
    result.update(update['settings'], directory=str(target), stop_file=str(target/'stop.request'),
                  campaign_settings_version=update['id'])
    # A prior supplied SRT must not silently bind a changed translation model.
    result['subtitles'] = None
    for key in ('subtitles_sha256', 'dialogue_source_sha256', 'require_subtitles'):
        result.pop(key, None)
    return result


def prepare_next_job(directory, item):
    from teodub.campaign import read_json, is_running
    from teodub.campaign_batch import _plain_path
    from teodub.pipeline import _job_lock
    job = effective_job(directory, item)
    config_path = _plain_path(job/'config.json')
    expected = item.get('execution_config_sha256') if item.get('execution_job') else item['config_sha256']
    if hash_file(config_path) != expected:
        raise ValueError('Cấu hình video đã thay đổi; chưa dùng lại.')
    config = read_json(config_path)
    update = next_settings(directory)
    if not update or config.get('campaign_settings_version') == update['id']:
        return job
    if is_running(job):
        raise ValueError('Video vẫn đang xử lý; chưa đổi cấu hình của phiên đang chạy.')
    selected = validate_settings(update['settings'])
    update = dict(update, settings=selected)
    proposed = proposed_config(directory, item, config, update)
    target = _plain_path(Path(proposed['directory']))
    _plain_path(target.parent)
    target.mkdir(parents=True, exist_ok=True)
    with _job_lock(job):
        # Share only verified source/captions. New voice/render caches are isolated.
        state_path = _plain_path(job/'state.json')
        state = read_json(state_path) if state_path.exists() else {}
        source = state.get('source')
        reused = {}
        if source:
            if hash_file(source['path']) != source['sha256']:
                raise ValueError('Video nguồn đã thay đổi; chưa áp dụng cấu hình mới.')
            reused['source'] = source
        caption = state.get('captions')
        same_language_models = all(config.get(key, default) == proposed.get(key, default) for key, default in
            (('asr_model', 'auto'), ('translation_model', 'default'), ('translation_effort', 'medium')))
        if source and caption and same_language_models:
            if hash_file(caption['path']) != caption['sha256']:
                raise ValueError('Lời Việt đã thay đổi; chưa dùng lại.')
            proposed.update(subtitles=caption['path'], subtitles_sha256=caption['sha256'],
                            dialogue_source_sha256=source['sha256'])
        path = _plain_path(target/'config.json')
        if path.exists():
            if read_json(path) != proposed:
                raise ValueError('Phiên cấu hình mới không khớp; không ghi đè.')
        else:
            if reused:
                atomic_json(target/'state.json', reused)
            atomic_json(path, proposed)
    item.update(execution_job=str(target), execution_config_sha256=hash_file(path),
                settings_version=update['id'])
    return target
