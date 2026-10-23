"""Persistent serial campaigns. Each video retains its own immutable pipeline job."""
import argparse
import hashlib
from datetime import datetime
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid

from teodub.common import atomic_json, hash_file, protect_worker
from teodub.downloaders.video_download import share_url
from teodub.pipeline import _job_lock
from teodub.wording import validate_wording

DONE = {'completed', 'review_required'}
CONFIG_KEYS = ('voice_provider', 'voice', 'reference_audio', 'reference_sha256',
               'reading_style', 'voice_speed', 'speed', 'original_gain', 'voice_gain',
               'asr_model', 'translation_model', 'translation_effort', 'tts_model',
               'burn_subtitles', 'subtitle_color', 'subtitle_weight', 'subtitle_size', 'subtitle_box', 'wording',
               'drive_upload', 'drive_data_dir', 'subtitle_clean')


def should_process(item, *, retry_failed=False):
    """One selection rule shared by the worker and its read-only GUI preview."""
    return item['status'] not in DONE and (retry_failed or item['status'] != 'failed')


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


WAITING_SOURCE = 'Chờ nguồn tải'
WAIT_FALLBACK_S = 30*60  # With no download service paused as a whole, the waiting video is tried again after this.


def _source_retry_at(now=None):
    """When to try a waiting video again: the first moment one of the paused download services is usable again.

    Services that failed only for that video are not paused, so they cannot say when; the video then simply waits
    WAIT_FALLBACK_S.
    """
    from teodub.downloaders.download_provider_registry import PROVIDERS
    from teodub.downloaders.provider_health import ProviderHealth
    now = time.time() if now is None else now
    health = ProviderHealth()
    paused = [state[0] for state in (health.unavailable(service.id, now) for service in PROVIDERS) if state]
    return min(paused) if paused else now+WAIT_FALLBACK_S


def auto_resume_due(manifest, now):
    """A campaign paused only because no download service worked, once the wait is over."""
    after = manifest.get('resume_after')
    return (manifest.get('status') == 'paused' and not manifest.get('archived_at')
            and type(after) in (int, float) and math.isfinite(after) and after <= now)


def parse_links(text):
    text = re.sub(r'\[[^\]]*\]\((https?://[^\s)]+)\)', r'\1', text)
    urls = re.findall(r'https?://[^\s<>"\u3000]+', text)
    result, seen = [], set()
    for raw in urls:
        url = share_url(raw)
        if url not in seen:
            seen.add(url)
            result.append(dict(url=url, title=''))
    if not result:
        raise ValueError('Dán ít nhất một link video Douyin/TikTok.')
    if len(result) > 1000:
        raise ValueError('Mỗi chiến dịch tối đa 1.000 video; chia thành nhiều chiến dịch.')
    return result


def safe_name(value):
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', ' ', value)
    return ' '.join(value.split()).strip(' .')[:55] or 'Chien dich'


def is_running(directory):
    directory = Path(directory)
    if not directory.exists():
        return False
    try:
        with _job_lock(directory):
            return False
    except (OSError, ValueError):
        return True


def totals(manifest):
    items = manifest['items']
    return dict(total=len(items), completed=sum(x['status'] in DONE for x in items),
                failed=sum(x['status'] == 'failed' for x in items),
                running=sum(x['status'] == 'running' for x in items),
                waiting=sum(x['status'] in ('pending', 'cancelled') for x in items))


def create_campaign(data_dir, name, records, settings, original_dir, output_dir, discovery=None, *, prevent_duplicates=False):
    """Serialize GUI creation and reuse an identical, recently created campaign."""
    from teodub.drive_delivery import drive_delivery_settings
    settings = dict(settings, wording=validate_wording(settings),
                    drive_upload=drive_delivery_settings(settings, data_dir))
    if settings['drive_upload']['enabled']:
        settings['drive_data_dir'] = str(Path(data_dir).resolve())
    if not prevent_duplicates:
        return _create_campaign(data_dir, name, records, settings, original_dir, output_dir, discovery)
    records = list(records)
    request = dict(name=name.strip(), records=records,
                   settings={key: settings[key] for key in CONFIG_KEYS if key in settings},
                   original_dir=str(Path(original_dir).resolve()), output_dir=str(Path(output_dir).resolve()),
                   discovery=discovery)
    fingerprint = hashlib.sha256(json.dumps(request, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()
    lock = Path(data_dir).resolve()/'campaign-creation'
    lock.mkdir(parents=True, exist_ok=True)
    with _job_lock(lock):
        for path in (Path(data_dir).resolve()/'campaigns').glob('*/campaign.json'):
            try:
                old = read_json(path)
                age = (datetime.now()-datetime.fromisoformat(old['created_at'])).total_seconds()
                if (old.get('creation_fingerprint') == fingerprint and old.get('name') == name.strip() and not old.get('archived_at')
                        and old.get('status') == 'ready' and 0 <= age <= 15):
                    return path.parent
            except (OSError, ValueError, KeyError, TypeError):
                continue
        directory = _create_campaign(data_dir, name, records, settings, original_dir, output_dir, discovery)
        manifest = read_json(directory/'campaign.json')
        manifest['creation_fingerprint'] = fingerprint
        atomic_json(directory/'campaign.json', manifest)
        return directory


def _create_campaign(data_dir, name, records, settings, original_dir, output_dir, discovery=None):
    """Create only local records; never start paid/network/model work here."""
    if not name.strip():
        raise ValueError('Đặt tên chiến dịch trước.')
    for value in (original_dir, output_dir):
        if not value or not Path(value).is_absolute():
            raise ValueError('Chọn đầy đủ thư mục video gốc và video lồng tiếng.')
    unique, seen = [], set()
    for row in records:
        url = share_url(row['url'])
        if url not in seen:
            unique.append(dict(url=url, title=str(row.get('title', ''))[:300]))
            seen.add(url)
    if not unique or len(unique) > 1000:
        raise ValueError('Chiến dịch cần từ 1 đến 1.000 video.')
    from teodub.ai_models import validate_models
    from teodub.subtitle_style import validate_subtitle_style
    from teodub.tts import selection_options, validate_selection
    from teodub.audio_mix import validate_audio_mix
    validate_audio_mix(settings)
    from teodub.subtitle_clean import validate_clean
    validate_clean(settings)
    validate_models(settings)
    validate_subtitle_style(settings)
    validate_selection(settings['voice'], **selection_options(settings))
    from teodub.drive_delivery import drive_delivery_settings
    settings = dict(settings, wording=validate_wording(settings),
                    drive_upload=drive_delivery_settings(settings, data_dir))
    if settings['drive_upload']['enabled']:
        settings['drive_data_dir'] = str(Path(data_dir).resolve())
    ident = uuid.uuid4().hex
    directory = Path(data_dir).resolve()/'campaigns'/ident
    directory.mkdir(parents=True, exist_ok=False)
    date = datetime.now().strftime('%d-%m-%Y')
    folder = safe_name(name)+'_'+date+'_'+ident[:8]
    originals, outputs = Path(original_dir).resolve()/folder, Path(output_dir).resolve()/folder
    if originals == outputs:
        originals, outputs = originals/'Video goc', outputs/'Video long tieng'
    snapshot = {key: settings[key] for key in CONFIG_KEYS if key in settings}
    items = []
    for number, row in enumerate(unique, 1):
        job = directory/'jobs'/f'{number:04}'
        basename = f'Video {number}_{date}'
        config = dict(snapshot, mode='full', directory=str(job), stop_file=str(job/'stop.request'),
                      input=row['url'], subtitles=None, original_dir=str(originals),
                      output_dir=str(outputs), campaign_basename=basename)
        atomic_json(job/'config.json', config)
        items.append(dict(number=number, title=row['title'], url=row['url'], config_sha256=hash_file(job/'config.json'),
                          job=str(job), status='pending', message='Chờ xử lý', outputs={}))
    manifest = dict(schema=1, id=ident, name=name.strip(), created_at=datetime.now().isoformat(),
                    original_dir=str(originals), output_dir=str(outputs), settings=snapshot,
                    discovery=discovery, status='ready', items=items)
    atomic_json(directory/'campaign.json', manifest)
    (directory/'links.txt').write_text('\n'.join(row['url'] for row in unique)+'\n', encoding='utf-8')
    return directory


def child_command(config):
    if getattr(sys, 'frozen', False):
        prefix = [sys.executable]
    else:
        prefix = [sys.executable, '-X', 'utf8', '-B', str(Path(__file__).resolve().parents[1]/'launch.py')]
    return prefix+['--worker', '--config', str(config)]


def run_child(config_path, stop):
    job = config_path.parent
    config = read_json(config_path)
    if Path(config['directory']).resolve() != job.resolve() or Path(config['stop_file']).resolve() != job/'stop.request':
        raise ValueError('Phiên video không khớp chiến dịch.')
    # Do not relaunch while an orphan child still owns the job lock.
    if is_running(job):
        raise ValueError('Video vẫn đang xử lý ở tiến trình khác. Chờ rồi bấm Tiếp tục.')
    (job/'stop.request').unlink(missing_ok=True)
    # Clear the prior status before launch so a crash cannot masquerade as success.
    atomic_json(job/'result.json', dict(status='running', message='Đang khởi động video.'))
    with (job/'worker.log').open('ab') as log:
        process = subprocess.Popen(child_command(config_path), cwd=str(job), stdout=log, stderr=log,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        stopping = None
        try:
            while process.poll() is None:
                if stop.exists():
                    (job/'stop.request').touch()
                    stopping = stopping or time.monotonic()
                    if time.monotonic()-stopping > 12:
                        process.terminate()
                        try:
                            process.wait(timeout=3)
                        except subprocess.TimeoutExpired:
                            process.kill()
                        process.wait(timeout=3)
                        break
                time.sleep(.25)
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
    try:
        result = read_json(job/'result.json')
    except (OSError, ValueError):
        result = {}
    if result.get('status') not in DONE | {'failed', 'cancelled'}:
        code = process.poll()
        result = dict(status='cancelled' if stop.exists() else 'failed',
                      message=('Đã nhận yêu cầu Dừng. Có thể tiếp tục các bước đã lưu.' if stop.exists()
                               else f'Phiên video kết thúc nhưng chưa có kết quả hợp lệ (mã thoát: {code}). '
                                    'Chưa xác định nguyên nhân; xem nhật ký video trước khi thử lại.'))
    return result


def run_campaign(directory, retry_failed=False, runner=None):
    directory = Path(directory).resolve()
    # All campaigns in this data directory share one execution lane.
    execution = directory.parent.parent/'execution'
    execution.mkdir(parents=True, exist_ok=True)
    with _job_lock(execution):
        return _run_campaign_locked(directory, retry_failed, runner)


class _StopRequests:
    """A batch stop and the ordinary campaign stop both cancel the current child."""
    def __init__(self, local, external):
        self.local, self.external = local, external

    def exists(self):
        return self.local.exists() or self.external.exists()

    def touch(self):
        self.local.touch()


def _run_campaign_locked(directory, retry_failed=False, runner=None, *, external_stop=None):
    """Internal entry for a supervisor that already holds the shared execution lock."""
    directory = Path(directory).resolve()
    runner = runner or run_child
    with _job_lock(directory):
        manifest = read_json(directory/'campaign.json')
        if manifest.get('archived_at'):
            raise ValueError('Chiến dịch đã xóa khỏi danh sách. Khôi phục chiến dịch trước khi chạy.')
        local_stop = directory/'stop.request'
        local_stop.unlink(missing_ok=True)
        stop = _StopRequests(local_stop, Path(external_stop)) if external_stop is not None else local_stop
        manifest['status'] = 'running'
        manifest.pop('message', None)
        manifest.pop('resume_after', None)
        manifest.pop('resume_retry_failed', None)
        atomic_json(directory/'campaign.json', manifest)
        atomic_json(directory/'campaign-runtime.json', dict(future_settings=1))
        try:
            for item in manifest['items']:
                if stop.exists():
                    break
                job = directory/'jobs'/f'{item["number"]:04}'
                if Path(item['job']).resolve() != job or job.is_symlink():
                    raise ValueError('Đường dẫn video trong chiến dịch không hợp lệ.')
                if hash_file(job/'config.json') != item['config_sha256']:
                    raise ValueError(f'Cấu hình Video {item["number"]} đã bị thay đổi; chưa chạy tiếp.')
                from teodub.campaign_settings import effective_job, prepare_next_job
                if item['status'] in DONE:
                    job = effective_job(directory, item)
                    if item.get('execution_job') and hash_file(job/'config.json') != item.get('execution_config_sha256'):
                        raise ValueError('Cấu hình đã chạy của video hoàn tất bị thay đổi.')
                    state = read_json(job/'state.json')
                    rendered = state.get('rendered', {})
                    if not rendered.get('outputs') or rendered['outputs'] != item['outputs']:
                        raise ValueError(f'Thành phẩm Video {item["number"]} không khớp dữ liệu đã lưu.')
                    for key, path in rendered['outputs'].items():
                        if hash_file(path) != rendered['hashes'][key]:
                            raise ValueError(f'Thành phẩm Video {item["number"]} đã thay đổi; không ghi đè.')
                    if item['status'] == 'completed':
                        from teodub.drive_delivery import upload_completed_video
                        config = read_json(job/'config.json')
                        result = dict(status='completed', outputs=item['outputs'], message=item.get('message', ''))
                        try:
                            delivery = upload_completed_video(config, result, cancel=stop)
                        except Exception:
                            delivery = dict(state='failed', message='Chưa tiếp tục được tải Google Drive. Video đã xuất trên máy được giữ.')
                        if delivery is not None:
                            item['google_drive'] = delivery
                            # This is delivery-only resumption; keep original video
                            # timestamps, processing duration and rendered output.
                            atomic_json(directory/'campaign.json', manifest)
                    continue
                if not should_process(item, retry_failed=retry_failed):
                    continue
                job = prepare_next_job(directory, item)
                item.update(status='running', message='Đang tải và lồng tiếng')
                attempt_started = time.monotonic()
                item.setdefault('started_at', datetime.now().astimezone().isoformat(timespec='seconds'))
                item['last_attempt_started_at'] = datetime.now().astimezone().isoformat(timespec='seconds')
                item.pop('completed_at', None)
                atomic_json(directory/'campaign.json', manifest)
                try:
                    result = runner(job/'config.json', stop)
                except Exception as exc:
                    result = dict(status='failed', message=str(exc) or type(exc).__name__)
                previous_seconds = item.get('processing_seconds', 0)
                if type(previous_seconds) not in (int, float) or not math.isfinite(previous_seconds) or previous_seconds < 0:
                    previous_seconds = 0
                item['processing_seconds'] = round(previous_seconds+max(0, time.monotonic()-attempt_started), 3)
                item['last_attempt_finished_at'] = datetime.now().astimezone().isoformat(timespec='seconds')
                if not isinstance(result, dict) or result.get('status') not in DONE | {'failed', 'cancelled'}:
                    result = dict(status='failed', message='Video chưa trả kết quả kết thúc hợp lệ. Kiểm tra rồi thử lại video lỗi.')
                if result['status'] in DONE:
                    item['completed_at'] = item['last_attempt_finished_at']
                item.update(status=result['status'], message=str(result.get('message') or ''), outputs=result.get('outputs', {}))
                if result.get('google_drive') is not None:
                    item['google_drive'] = result['google_drive']
                # No download service is usable: not this video's fault, so no red error; wait and try it again.
                waiting_source = item['status'] == 'failed' and item['message'].startswith(WAITING_SOURCE)
                if waiting_source:
                    item['status'] = 'cancelled'
                if item['status'] == 'failed' and not item['message']:
                    item['message'] = 'Video xử lý thất bại; chưa có nguyên nhân được ghi. Mở chi tiết để kiểm tra nhật ký.'
                elif item['status'] == 'cancelled' and not item['message']:
                    item['message'] = ('Đã nhận yêu cầu Dừng. Các bước đã hoàn tất được giữ lại.' if stop.exists()
                                       else 'Bước xử lý đã bị ngắt; chưa xác định nguyên nhân. Có thể tiếp tục phần đã lưu.')
                atomic_json(directory/'campaign.json', manifest)
                # Pause on interruptions or account limits instead of failing the entire channel.
                text = item['message'].lower()
                if waiting_source:
                    manifest['resume_after'] = _source_retry_at()
                    # A wait during "Thử lại video lỗi" resumes as that same pass, so later failed videos are not skipped.
                    manifest['resume_retry_failed'] = bool(retry_failed)
                if item['status'] == 'cancelled' or (item['status'] == 'failed' and any(
                    value in text for value in ('hạn mức', 'quota', 'usage limit', 'rate limit', 'đăng nhập', 'login', 'credits'))):
                    manifest['status'] = 'paused'
                    manifest['message'] = item['message']
                    break
            else:
                manifest['status'] = 'completed' if all(x['status'] in DONE for x in manifest['items']) else 'needs_attention'
                if manifest['status'] == 'needs_attention':
                    manifest['message'] = 'Chiến dịch còn video lỗi. Chọn dòng lỗi để xem nguyên nhân; bấm Thử lại video lỗi khi đã xử lý.'
            if stop.exists() or manifest['status'] == 'running':
                manifest['status'] = 'paused'
                if stop.exists():
                    manifest['message'] = 'Đã nhận yêu cầu Dừng. Giữ tiến độ và các bước đã hoàn tất; bấm Chạy / Tiếp tục khi sẵn sàng.'
                elif not manifest.get('message'):
                    manifest['message'] = 'Chiến dịch bị gián đoạn; chưa xác định nguyên nhân. Kiểm tra nhật ký rồi bấm Chạy / Tiếp tục.'
        except Exception as exc:
            manifest['status'] = 'paused'
            manifest['message'] = str(exc) or type(exc).__name__
        atomic_json(directory/'campaign.json', manifest)
        (directory/'campaign-runtime.json').unlink(missing_ok=True)
        return manifest


def main(args=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--directory', required=True)
    parser.add_argument('--retry-failed', action='store_true')
    args = parser.parse_args(args)
    protect_worker()
    result = run_campaign(args.directory, args.retry_failed)
    return 0 if result['status'] == 'completed' else 2
