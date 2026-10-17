"""Edit campaign metadata and reversibly remove entries without deleting videos."""
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path

from teodub.campaign import read_json
from teodub.campaign_batch import _campaign_path, _plain_path
from teodub.common import atomic_json
from teodub.pipeline import _job_lock


def update_campaign(data_dir, directory, *, name=None, note=None, archived=None):
    data_dir = Path(data_dir).resolve()
    directory = _campaign_path(data_dir, Path(directory))
    if name is not None and (not isinstance(name, str) or not name.strip() or len(name.strip()) > 150):
        raise ValueError('Tên chiến dịch cần từ 1 đến 150 ký tự.')
    if note is not None and (not isinstance(note, str) or len(note) > 2000):
        raise ValueError('Ghi chú tối đa 2.000 ký tự.')
    if archived is not None and type(archived) is not bool:
        raise ValueError('Trạng thái xóa không hợp lệ.')
    execution = _plain_path(data_dir/'execution')
    execution.mkdir(exist_ok=True)
    _plain_path(execution/'worker.lock')
    path = _plain_path(directory/'campaign.json')
    with ExitStack() as stack:
        stack.enter_context(_job_lock(execution))
        stack.enter_context(_job_lock(directory))
        manifest = read_json(path)
        if not isinstance(manifest, dict) or manifest.get('id') != directory.name or not isinstance(manifest.get('items'), list):
            raise ValueError('Dữ liệu chiến dịch không hợp lệ.')
        # An orphan video worker may remain after its supervisor stopped.
        _plain_path(directory/'jobs')
        for item in manifest['items']:
            job = _plain_path(directory/'jobs'/f'{item["number"]:04}')
            if Path(item['job']) != job or not job.is_dir():
                raise ValueError('Đường dẫn video trong chiến dịch không hợp lệ.')
            _plain_path(job/'worker.lock')
            stack.enter_context(_job_lock(job))
            from teodub.campaign_settings import effective_job
            execution_job = effective_job(directory, item)
            if execution_job != job:
                _plain_path(execution_job/'worker.lock')
                stack.enter_context(_job_lock(execution_job))
        if name is not None:
            manifest.setdefault('batch_identity_name', manifest['name'])
            manifest['name'] = name.strip()
        if note is not None:
            manifest['note'] = note.strip()
        if archived is True:
            manifest.setdefault('archived_at', datetime.now().isoformat())
        elif archived is False:
            manifest.pop('archived_at', None)
        manifest['updated_at'] = datetime.now().isoformat()
        atomic_json(path, manifest)
        return manifest
