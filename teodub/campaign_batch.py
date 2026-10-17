"""Saved, serial queues of campaigns sharing the studio's execution lane."""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import stat
import sys
import uuid

from teodub import campaign
from teodub.common import atomic_json, hash_file, protect_worker
from teodub.pipeline import _job_lock


ENTRY_STATUSES = {'pending', 'running', 'paused', 'completed', 'needs_attention'}
BATCH_STATUSES = {'ready', 'running', 'paused', 'completed', 'needs_attention'}
MAX_CAMPAIGNS = 1000


def _plain_path(path):
    """Reject links/junctions before they can redirect reads or lock writes."""
    path = Path(path)
    if path.is_symlink():
        raise ValueError('Đường dẫn hàng đợi không được là liên kết.')
    try:
        attributes = path.lstat().st_file_attributes
    except (AttributeError, FileNotFoundError):
        return path
    if attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        raise ValueError('Đường dẫn hàng đợi không được là liên kết.')
    return path


def _read(path):
    _plain_path(path)
    try:
        result = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise ValueError('Không đọc được dữ liệu hàng đợi hoặc chiến dịch đã lưu.') from exc
    if not isinstance(result, dict):
        raise ValueError('Dữ liệu hàng đợi hoặc chiến dịch không hợp lệ.')
    return result


def _campaign_path(data_dir, value):
    root = _plain_path(Path(data_dir)/'campaigns')
    path = Path(value)
    if not path.is_absolute() or path.parent != root or not path.name:
        raise ValueError('Chỉ chọn chiến dịch đã lưu trong danh sách của Tèo.')
    _plain_path(path)
    if not path.is_dir() or path.resolve() != path:
        raise ValueError('Thư mục chiến dịch không tồn tại hoặc đã bị chuyển.')
    _plain_path(path/'worker.lock')
    return path


def _identity(manifest, directory):
    """Mutable progress is deliberately absent from the configuration snapshot."""
    if manifest.get('archived_at'):
        raise ValueError('Chiến dịch đã xóa khỏi danh sách; khôi phục hoặc tạo hàng đợi mới.')
    if (manifest.get('schema') != 1 or manifest.get('id') != directory.name
            or not isinstance(manifest.get('name'), str)
            or not isinstance(manifest.get('settings'), dict)
            or not isinstance(manifest.get('items'), list)
            or not 1 <= len(manifest['items']) <= 1000):
        raise ValueError('Dữ liệu chiến dịch đã lưu không hợp lệ.')
    try:
        frozen = {key: manifest[key] for key in
                  ('id', 'name', 'created_at', 'original_dir', 'output_dir', 'settings')}
        # Display-name edits must not invalidate existing queues or job caches.
        frozen['name'] = manifest.get('batch_identity_name', manifest['name'])
        frozen['items'] = [{key: item[key] for key in
                           ('number', 'title', 'url', 'job', 'config_sha256')}
                          for item in manifest['items']]
        if [item['number'] for item in manifest['items']] != list(range(1, len(manifest['items'])+1)):
            raise ValueError('Thứ tự video trong chiến dịch đã bị thay đổi.')
        for item in manifest['items']:
            job = directory/'jobs'/f'{item["number"]:04}'
            _plain_path(directory/'jobs')
            _plain_path(job)
            _plain_path(job/'config.json')
            _plain_path(job/'worker.lock')
            if Path(item['job']) != job or not isinstance(item['config_sha256'], str):
                raise ValueError('Đường dẫn video trong chiến dịch không hợp lệ.')
            from teodub.campaign_settings import effective_job
            execution = effective_job(directory, item)
            _plain_path(execution/'config.json')
            _plain_path(execution/'worker.lock')
    except (KeyError, TypeError) as exc:
        raise ValueError('Cấu hình chiến dịch đã lưu không đầy đủ.') from exc
    content = json.dumps(frozen, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
    return hashlib.sha256(content.encode('utf-8')).hexdigest()


def create_batch(data_dir, directories):
    """Save a selection in its given order; this never launches any work."""
    data_dir = Path(data_dir).resolve()
    selected, seen = [], set()
    for value in directories:
        path = _campaign_path(data_dir, value)
        if path in seen:
            continue
        seen.add(path)
        manifest = _read(path/'campaign.json')
        identity = _identity(manifest, path)
        selected.append(dict(directory=str(path), name=manifest['name'],
                             config_identity=identity, status='pending', message='Chờ chạy'))
    if not selected or len(selected) > MAX_CAMPAIGNS:
        raise ValueError('Chọn từ 1 đến 1.000 chiến dịch để chạy lần lượt.')
    batches = _plain_path(data_dir/'campaign-batches')
    batches.mkdir(parents=True, exist_ok=True)
    ident = uuid.uuid4().hex
    directory = batches/ident
    directory.mkdir(exist_ok=False)
    queue = dict(schema=1, id=ident, created_at=datetime.now().isoformat(),
                 status='ready', message='Đã lưu danh sách; chưa chạy.',
                 campaigns=selected, active_directory=None)
    atomic_json(directory/'queue.json', queue)
    atomic_json(data_dir/'last-campaign-batch.json', dict(directory=str(directory)))
    return directory


def _queue_directory(value):
    path = Path(value)
    if (not path.is_absolute() or path.parent.name != 'campaign-batches'
            or not re.fullmatch(r'[0-9a-f]{32}', path.name)):
        raise ValueError('Thư mục hàng đợi chiến dịch không hợp lệ.')
    _plain_path(path.parent)
    _plain_path(path)
    if not path.is_dir() or path.resolve() != path:
        raise ValueError('Thư mục hàng đợi chiến dịch không tồn tại hoặc đã bị chuyển.')
    _plain_path(path/'worker.lock')
    _plain_path(path/'stop.request')
    return path


def read_batch(directory):
    """Read a queue without starting, clearing stops, or changing its status."""
    directory = _queue_directory(directory)
    queue = _read(directory/'queue.json')
    rows = queue.get('campaigns')
    if (queue.get('schema') != 1 or queue.get('id') != directory.name
            or not isinstance(queue.get('status'), str) or queue['status'] not in BATCH_STATUSES
            or not isinstance(rows, list) or not 1 <= len(rows) <= MAX_CAMPAIGNS):
        raise ValueError('Dữ liệu hàng đợi chiến dịch không hợp lệ.')
    seen = set()
    for row in rows:
        if (not isinstance(row, dict) or not isinstance(row.get('directory'), str)
                or not isinstance(row.get('name'), str)
                or not isinstance(row.get('status'), str) or row['status'] not in ENTRY_STATUSES
                or not re.fullmatch(r'[0-9a-f]{64}', str(row.get('config_identity', '')))):
            raise ValueError('Một dòng trong hàng đợi chiến dịch không hợp lệ.')
        path = _campaign_path(directory.parent.parent, row['directory'])
        if path in seen:
            raise ValueError('Hàng đợi bị trùng chiến dịch; chưa chạy tiếp.')
        seen.add(path)
    active = queue.get('active_directory')
    if active is not None and active not in {row['directory'] for row in rows}:
        raise ValueError('Chiến dịch đang chạy không thuộc hàng đợi.')
    return queue


def _completed_unchanged(directory, manifest):
    """Verify previously completed outputs without invoking their pipeline."""
    if manifest.get('status') != 'completed' or any(item.get('status') not in campaign.DONE
                                                    for item in manifest['items']):
        raise ValueError('Chiến dịch đã xong có trạng thái thay đổi; chưa chạy lại.')
    for item in manifest['items']:
        job = directory/'jobs'/f'{item["number"]:04}'
        config = _plain_path(job/'config.json')
        if hash_file(config) != item['config_sha256']:
            raise ValueError('Cấu hình chiến dịch đã xong bị thay đổi; chưa chạy lại.')
        from teodub.campaign_settings import effective_job
        job = effective_job(directory, item)
        if item.get('execution_job') and hash_file(_plain_path(job/'config.json')) != item.get('execution_config_sha256'):
            raise ValueError('Cấu hình video đã xong bị thay đổi.')
        state = _read(job/'state.json')
        rendered = state.get('rendered', {})
        if not isinstance(rendered, dict) or not rendered.get('outputs') or rendered['outputs'] != item.get('outputs'):
            raise ValueError('Thành phẩm chiến dịch đã xong không khớp dữ liệu đã lưu.')
        hashes = rendered.get('hashes', {})
        for key, path in rendered['outputs'].items():
            if hash_file(path) != hashes.get(key):
                raise ValueError('Thành phẩm chiến dịch đã xong đã thay đổi; không ghi đè.')


def run_batch(directory, runner=None, *, clear_stop=True):
    """Explicit start/resume; hold one execution lock across the entire list."""
    directory = _queue_directory(directory)
    # Validate all destinations before any status or stop-file mutation.
    read_batch(directory)
    data_dir = directory.parent.parent
    execution = _plain_path(data_dir/'execution')
    execution.mkdir(exist_ok=True)
    _plain_path(execution/'worker.lock')
    runner = runner or campaign._run_campaign_locked
    with _job_lock(execution), _job_lock(directory):
        queue = read_batch(directory)
        stop = directory/'stop.request'
        if clear_stop:
            stop.unlink(missing_ok=True)
        queue.update(status='running', message='Đang chạy các chiến dịch lần lượt.', active_directory=None)
        atomic_json(directory/'queue.json', queue)
        try:
            for row in queue['campaigns']:
                if stop.exists():
                    queue.update(status='paused', message='Đã dừng hàng đợi. Bấm Tiếp tục để chạy phần còn lại.')
                    break
                path = _campaign_path(data_dir, row['directory'])
                manifest = _read(path/'campaign.json')
                if _identity(manifest, path) != row['config_identity']:
                    raise ValueError(f'Cấu hình chiến dịch {row["name"]} đã thay đổi; chưa chạy tiếp.')
                if row['status'] == 'completed':
                    _completed_unchanged(path, manifest)
                    continue
                if row['status'] == 'needs_attention':
                    if manifest.get('status') == 'completed':
                        _completed_unchanged(path, manifest)
                        row.update(status='completed', message='Đã hoàn tất sau khi xử lý video lỗi.')
                        atomic_json(directory/'queue.json', queue)
                    elif manifest.get('status') != 'needs_attention':
                        raise ValueError(f'Trạng thái chiến dịch {row["name"]} đã thay đổi; kiểm tra trước khi tiếp tục.')
                    continue
                row.update(status='running', message='Đang chạy chiến dịch')
                queue['active_directory'] = str(path)
                atomic_json(directory/'queue.json', queue)
                result = runner(path, external_stop=stop)
                if (not isinstance(result, dict) or not isinstance(result.get('status'), str)
                        or result['status'] not in {'completed', 'needs_attention', 'paused'}):
                    raise ValueError('Chiến dịch chưa trả trạng thái kết thúc hợp lệ; có thể tiếp tục sau khi kiểm tra.')
                status = result['status']
                row.update(status=status, message=result.get('message') or
                           {'completed': 'Đã hoàn tất', 'needs_attention': 'Có video lỗi; chưa tự thử lại',
                            'paused': 'Chiến dịch tạm dừng'}[status])
                queue['active_directory'] = None
                atomic_json(directory/'queue.json', queue)
                if status == 'paused':
                    queue.update(status='paused', message=row['message'])
                    break
            else:
                finished = all(row['status'] == 'completed' for row in queue['campaigns'])
                queue.update(status='completed' if finished else 'needs_attention',
                             message='Đã hoàn tất các chiến dịch.' if finished else
                             'Đã chạy hết danh sách; có video lỗi cần kiểm tra riêng.')
            if stop.exists():
                queue.update(status='paused', message='Đã dừng hàng đợi. Bấm Tiếp tục để chạy phần còn lại.')
        except Exception as exc:
            queue.update(status='paused', message=str(exc))
            for row in queue['campaigns']:
                if row['status'] == 'running':
                    row.update(status='paused', message=str(exc))
        queue['active_directory'] = None
        atomic_json(directory/'queue.json', queue)
        return queue


def main(args=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--directory', required=True)
    parser.add_argument('--keep-stop', action='store_true')
    args = parser.parse_args(args)
    protect_worker()
    try:
        result = run_batch(args.directory, clear_stop=not args.keep_stop)
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0 if result['status'] == 'completed' else 2
