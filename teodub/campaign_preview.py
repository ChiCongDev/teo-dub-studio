"""Read the next worker config, never substitute the current single-video form."""
import hashlib
import json
from pathlib import Path

from teodub.campaign import should_process


def next_video_config(directory, manifest, *, retry_failed=False, active=False):
    items = manifest['items']
    item = next((row for row in items if row['status'] == 'running'), None) if active else None
    if item is None:
        item = next((row for row in items if should_process(row, retry_failed=retry_failed)), None)
    if item is None:
        return None
    directory = Path(directory).resolve()
    number = item['number']
    if type(number) is not int or number < 1:
        raise ValueError('Số thứ tự video không hợp lệ.')
    job = directory/'jobs'/f'{number:04}'
    config_path = job/'config.json'
    if (Path(item['job']).resolve() != job or job.resolve() != job or job.is_symlink()
            or config_path.is_symlink() or config_path.resolve().parent != job):
        raise ValueError(f'Đường dẫn cấu hình Video {number} không khớp chiến dịch.')
    data = config_path.read_bytes()
    if hashlib.sha256(data).hexdigest() != item['config_sha256']:
        raise ValueError(f'Cấu hình Video {number} đã bị thay đổi; cần kiểm tra trước khi chạy.')
    from teodub.campaign_settings import effective_job, next_settings, proposed_config
    actual_job = effective_job(directory, item)
    if actual_job != job:
        actual_path = actual_job/'config.json'
        actual_data = actual_path.read_bytes()
        if hashlib.sha256(actual_data).hexdigest() != item.get('execution_config_sha256'):
            raise ValueError('Cấu hình đã chạy của video bị thay đổi.')
        data = actual_data
        job = actual_job
    config = json.loads(data.decode('utf-8'))
    if (Path(config['directory']).resolve() != job or Path(config['stop_file']).resolve() != job/'stop.request'):
        raise ValueError(f'Phiên Video {number} không khớp cấu hình đã lưu.')
    update = next_settings(directory)
    queued = False
    if not (active and item['status'] == 'running') and update and config.get('campaign_settings_version') != update['id']:
        config = proposed_config(directory, item, config, update)
        queued = True
    return dict(number=number, status=item['status'], config=config, queued=queued,
                future_config=update['settings'] if active and item['status'] == 'running' and update
                and config.get('campaign_settings_version') != update['id'] else None)


def describe_pipeline_settings(config):
    from teodub.voice_profiles import describe_voice_settings
    from teodub.ai_models import validate_models, ASR_MODELS, TRANSLATION_MODELS, REASONING_LEVELS
    from teodub.audio_mix import validate_audio_mix
    from teodub.subtitle_style import validate_subtitle_style, SUBTITLE_COLORS, SUBTITLE_WEIGHTS
    models = validate_models(config)
    mix = validate_audio_mix(config)
    subtitles = validate_subtitle_style(config)
    def percent(value):
        return f'{value*100:g}'.replace('.', ',')+'%'
    subtitle_text = ('Ghi Sub vào hình · '+SUBTITLE_COLORS[subtitles['subtitle_color']]['label']+
                     ' · '+SUBTITLE_WEIGHTS[subtitles['subtitle_weight']]['label']+
                     f' · cỡ {subtitles["subtitle_size"]}' if subtitles['burn_subtitles'] else 'Sub bật/tắt')
    speed = f'{float(config.get("speed", .9)):g}'.replace('.', ',')+'×'
    return (describe_voice_settings(config)+
            ' | ASR: '+ASR_MODELS[models['asr_model']]+
            ' | Dịch: '+TRANSLATION_MODELS[models['translation_model']]+
            ' · suy luận '+REASONING_LEVELS[models['translation_effort']]+
            ' | Hình '+speed+
            ' | Tiếng gốc '+percent(mix['original_gain'])+' · Giọng đọc '+percent(mix['voice_gain'])+
            ' | '+subtitle_text+
            ' | Từ ngữ: '+('Bật' if (config.get('wording') or {}).get('enabled') else 'Tắt')+
            ' | Xóa Sub gốc GPU: '+('Bật' if (config.get('subtitle_clean') or {}).get('enabled') else 'Tắt')+
            ' | Drive: '+('Bật' if (config.get('drive_upload') or {}).get('enabled') else 'Tắt'))
