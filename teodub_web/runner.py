import json
import math
from pathlib import Path
import subprocess
import threading
import time

from teodub.ai_models import NEW_MODELS, validate_models
from teodub.common import atomic_json, hash_file
from teodub.downloaders.video_download import share_url
from teodub.pipeline import _job_lock
from teodub_web.form_settings import FORM_FIELDS, preferences
from teodub_web.source_clean import clean_options, prepared_source, source_record
import re

PROJECT = Path(__file__).resolve().parents[1]


def validate_spec(data):
    if not isinstance(data, dict):
        raise ValueError('Cấu hình chưa hợp lệ.')
    allowed = {'input', 'title', 'submit_key', 'voice', 'reading_style', 'voice_speed', 'speed',
               'original_gain', 'voice_gain', 'burn_subtitles', 'subtitle_color', 'subtitle_weight',
               'subtitle_size', 'save_drive', 'prepare_only', 'source_job_id', 'subtitle_clean', *NEW_MODELS}
    if set(data)-allowed:
        raise ValueError('Cấu hình có trường chưa được hỗ trợ.')
    if not isinstance(data.get('input'), str) or len(data['input']) > 3000:
        raise ValueError('Nhập một link video Douyin hoặc TikTok.')
    url = share_url(data['input'])
    title = data.get('title', '')
    if not isinstance(title, str) or len(title) > 100:
        raise ValueError('Tên công việc tối đa 100 ký tự.')
    title = title.strip()
    model = validate_models({key: data.get(key, value) for key, value in NEW_MODELS.items()})
    spec = dict(model, voice_provider='local',
                **preferences({key: data[key] for key in FORM_FIELDS if key in data}, model))
    prepare = data.get('prepare_only', False)
    if type(prepare) is not bool:
        raise ValueError('Lựa chọn tải video không hợp lệ.')
    source_id = data.get('source_job_id')
    if source_id is not None and (not isinstance(source_id, str) or not re.fullmatch('[a-f0-9]{32}', source_id)):
        raise ValueError('Chọn video gốc đã tải từ danh sách.')
    clean = clean_options(data)
    if clean['enabled'] and (prepare or not source_id):
        raise ValueError('Tải video gốc và khoanh vùng trước khi bật xóa Sub Trung.')
    if prepare and source_id:
        raise ValueError('Phiên tải mới không dùng vùng xóa của video trước.')
    if prepare:
        spec['prepare_only'] = True
    if source_id:
        spec['source_job_id'] = source_id
    if 'subtitle_clean' in data:
        spec['subtitle_clean'] = clean
    return url, title or 'Video mới', spec


def config_for(store, job, settings):
    spec = json.loads(job['spec'])
    directory = store.root/'jobs'/job['id']
    drive = dict(version=1, mode='sync_folder', enabled=spec.pop('save_drive'), folder_path=settings['drive_folder'])
    prepare = spec.pop('prepare_only', False)
    source_id = spec.pop('source_job_id', None)
    input_value = job['input']
    if source_id:
        source = prepared_source(store, job['owner'], source_id, job['input'])
        input_value = source['path']
        if (spec.get('subtitle_clean') or {}).get('enabled'):
            spec['subtitle_clean'].update(source_path=source['path'], source_sha256=source['sha256'])
    if prepare:
        drive['enabled'] = False
    return dict(spec, mode='download' if prepare else 'full', input=input_value, directory=str(directory),
                original_dir=str(store.root/'originals'), output_dir=str(store.root/'outputs'/job['id']),
                subtitles=None, stop_file=str(directory/'stop.request'),
                execution_lock=settings['execution_lock'], drive_upload=drive, drive_data_dir=str(store.root))


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def public_message(message):
    # Worker errors sometimes contain local paths. Keep those in the local log.
    text = str(message)
    if ':\\' in text or str(Path.home()) in text or len(text) > 1200:
        return 'Cần kiểm tra trên máy xử lý. Quản trị viên xem nhật ký của công việc.'
    return text


def verified_output(store, job, kind):
    if kind not in ('video', 'subtitles') or job['state'] not in ('completed', 'review_required'):
        raise FileNotFoundError()
    directory = store.root/'jobs'/job['id']
    result = read_json(directory/'result.json')
    state = read_json(directory/'state.json')
    outputs = result.get('outputs') or {}
    rendered = state.get('rendered') or {}
    if result.get('status') not in ('completed', 'review_required') or outputs != rendered.get('outputs'):
        raise FileNotFoundError()
    path = Path(outputs.get(kind, ''))
    expected = rendered.get('hashes', {}).get(kind)
    root = store.root/'outputs'/job['id']
    if (not path.is_absolute() or not path.is_file() or path.is_symlink()
            or not path.resolve().is_relative_to(root.resolve())
            or path.suffix.lower() != ('.mp4' if kind == 'video' else '.srt')
            or hash_file(path) != expected):
        raise FileNotFoundError()
    return path


def describe(store, job):
    directory = store.root/'jobs'/job['id']
    result = read_json(directory/'result.json')
    receipt = read_json(directory/'drive-upload.json')
    progress, message = None, job['message']
    if job['state'] in ('running', 'stopping'):
        try:
            # Read only the last block, including when a writer is appending.
            with (directory/'events.jsonl').open('rb') as stream:
                stream.seek(max(0, stream.seek(0, 2)-16384))
                for line in reversed(stream.read().splitlines()):
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    if not message or job['state'] != 'stopping':
                        message = event.get('message') or message
                    number = event.get('percent')
                    if type(number) in (float, int) and math.isfinite(number):
                        progress = min(100, max(0, number))
                        break
        except OSError:
            pass
    label = {'queued': 'Đang chờ', 'running': 'Đang xử lý', 'stopping': 'Đang dừng',
             'completed': 'Hoàn thành', 'failed': 'Cần kiểm tra', 'cancelled': 'Đã dừng',
             'interrupted': 'Chờ tiếp tục', 'review_required': 'Cần nghe duyệt'}[job['state']]
    drive = 'Tắt'
    if json.loads(job['spec']).get('save_drive'):
        drive = {'staged': 'Đã chép vào thư mục Drive', 'failed': 'Chưa chép được vào Drive',
                 'copying': 'Đang chép vào Drive', 'cancelled': 'Đã dừng chép'}.get(receipt.get('state'), 'Chờ thành phẩm')
    spec = json.loads(job['spec'])
    if spec.get('prepare_only'):
        drive = 'Chưa tạo thành phẩm'
        if job['state'] == 'completed':
            message = 'Video gốc đã tải xong. Bấm Khoanh vùng chữ gốc để chuẩn bị xóa Sub Trung.'
    try:
        source_record(store, job)
        has_source = True
    except (OSError, ValueError, KeyError, TypeError):
        has_source = False
    return dict(id=job['id'], title=job['title'], input=job['input'], owner_name=job['owner_name'],
                created=job['created'], state=job['state'], label=label, message=public_message(message),
                progress=progress, drive=drive, can_retry=job['state'] in ('failed', 'cancelled', 'interrupted'),
                can_stop=job['state'] in ('queued', 'running'),
                has_output=job['state'] in ('completed', 'review_required') and bool(result.get('outputs', {}).get('video')),
                spec=spec, has_source=has_source, prepare_only=bool(spec.get('prepare_only')))


class Runner:
    def __init__(self, store, settings, launcher=None):
        self.store, self.settings = store, settings
        self.launcher = launcher or self.launch
        self.stop = threading.Event()
        self.thread = None

    def launch(self, config_path, log):
        return subprocess.Popen([str(PROJECT/'.venv'/'Scripts'/'python.exe'), '-X', 'utf8', '-B',
                                 '-m', 'teodub_web.worker', '--config', str(config_path)],
                                cwd=PROJECT, stdout=log, stderr=subprocess.STDOUT,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))

    def start(self):
        self.thread = threading.Thread(target=self.loop, daemon=True, name='teo-web-queue')
        self.thread.start()

    def finalize(self, job, code=None):
        result = read_json(self.store.root/'jobs'/job['id']/'result.json')
        status = result.get('status')
        if status in ('completed', 'review_required', 'failed', 'cancelled'):
            self.store.set_state(job['id'], status, public_message(result.get('message', '')))
        else:
            self.store.set_state(job['id'], 'interrupted', 'Phiên bị gián đoạn. Bấm Tiếp tục để dùng lại các bước đã lưu.')

    def recover(self):
        # A prior child may still hold its OS lock. Never start a duplicate.
        for job in self.store.jobs():
            if job['state'] not in ('running', 'stopping'):
                continue
            directory = self.store.root/'jobs'/job['id']
            directory.mkdir(parents=True, exist_ok=True)
            try:
                with _job_lock(directory):
                    self.finalize(job)
            except ValueError:
                pass

    def run_one(self, job):
        directory = self.store.root/'jobs'/job['id']
        directory.mkdir(parents=True, exist_ok=True)
        path = directory/'config.json'
        config = config_for(self.store, job, self.settings)
        expected_input = config['input']
        if path.exists() and read_json(path) != config:
            # Settings may have changed with an update; resume keeps frozen paths.
            config = read_json(path)
            if config.get('directory') != str(directory) or config.get('input') != expected_input:
                self.store.set_state(job['id'], 'failed', 'Hồ sơ phiên không khớp. Quản trị viên kiểm tra trên máy.')
                return
        elif not path.exists():
            atomic_json(path, config)
        # Honor a stop racing with config publication or process creation.
        if self.store.job(job['id'])['state'] == 'stopping':
            (directory/'stop.request').touch()
        with (directory/'worker.log').open('ab') as log:
            process = self.launcher(path, log)
            start = time.monotonic()
            while process.poll() is None:
                if self.stop.wait(.5):
                    (directory/'stop.request').touch()
                if time.monotonic()-start > 7200:
                    (directory/'stop.request').touch()
                    self.store.set_state(job['id'], 'stopping', 'Đã yêu cầu dừng phiên quá 2 giờ; giữ các bước đã lưu.')
                    if time.monotonic()-start > 7260:
                        process.kill()
            code = process.wait()
        if code == 75:
            current = self.store.job(job['id'])
            if current['state'] == 'stopping':
                self.store.set_state(job['id'], 'cancelled', 'Đã dừng trước khi xử lý.')
            else:
                self.store.set_state(job['id'], 'queued', 'Máy đang xử lý phiên Tèo khác; web sẽ chờ.')
                self.stop.wait(3)
        else:
            self.finalize(job, code)

    def loop(self):
        while not self.stop.is_set():
            try:
                self.recover()
                job = self.store.claim()
                if job:
                    try:
                        self.run_one(job)
                    except Exception:
                        self.store.set_state(job['id'], 'failed', 'Không mở được phiên xử lý. Quản trị viên kiểm tra máy.')
                else:
                    self.stop.wait(1)
            except Exception:
                self.stop.wait(3)
