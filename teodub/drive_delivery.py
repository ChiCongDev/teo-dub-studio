"""Optional cloud delivery after a successful local render, never a render step."""
from datetime import datetime
import json
from pathlib import Path
import re

from teodub.common import atomic_json


DRIVE_LABELS = {'queued': 'Chờ tải lên', 'uploading': 'Đang tải lên',
                'uploaded': 'Đã lưu trên Drive', 'failed': 'Tải lên bị lỗi',
                'cancelled': 'Đã tạm dừng tải', 'needs_auth': 'Cần kết nối lại',
                'unknown': 'Cần kiểm tra tải lên'}


class _StopCancel:
    """Campaigns combine several stop-file sources via exists(), not is_set()."""
    def __init__(self, source):
        self.source = source

    def is_set(self):
        return self.source.exists()


def _receipt_link_matches(value):
    file_id = value.get('file_id')
    return (isinstance(file_id, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,200}', file_id) is not None
            and value.get('url') == 'https://drive.google.com/file/d/'+file_id+'/view')


def drive_delivery_settings(config, data_dir=None):
    from teodub.google_drive import normalize_drive_config
    options = normalize_drive_config(config)
    if not options['enabled']:
        return options
    root = Path(data_dir if data_dir is not None else config.get('drive_data_dir', ''))
    if not root.is_absolute():
        raise ValueError('Chưa xác định thư mục dữ liệu kết nối Google Drive.')
    if data_dir is not None and config.get('drive_data_dir') and Path(config['drive_data_dir']).resolve() != root.resolve():
        raise ValueError('Kết nối Drive không thuộc thư mục dữ liệu của Tèo này.')
    return options


def upload_completed_video(config, result, *, progress=None, cancel=None):
    """Return a separate outcome; cloud failure must not invalidate local output."""
    if (config.get('mode', 'full') != 'full' or result.get('status') != 'completed' or
            not (result.get('outputs') or {}).get('video')):
        return None
    # Legacy jobs and disabled selections have no side effects at all.
    raw = config.get('drive_upload') or {}
    if isinstance(raw, dict) and not raw.get('enabled'):
        return None
    if cancel is not None and not hasattr(cancel, 'is_set') and hasattr(cancel, 'exists'):
        cancel = _StopCancel(cancel)
    if isinstance(raw, dict) and raw.get('mode') == 'sync_folder':
        from teodub.drive_folder import stage_completed_video
        return stage_completed_video(config, result, progress=progress, cancel=cancel)
    directory = Path(config['directory']).resolve()
    status_path = directory/'drive-upload.json'
    frozen = {}

    def record(outcome):
        outcome = dict(outcome, updated_at=datetime.now().astimezone().isoformat(timespec='seconds'))
        atomic_json(status_path, outcome)
        if progress:
            progress(dict(outcome))
        return outcome

    try:
        from teodub.google_drive import DriveClient, DriveAuthError
        options = drive_delivery_settings(config)
        data_dir = Path(config['drive_data_dir']).resolve()
        if not directory.is_relative_to(data_dir) or directory == data_dir:
            raise ValueError('Phiên video không thuộc thư mục dữ liệu đã kết nối Drive.')
        source_id = directory.relative_to(data_dir).as_posix()
        state = json.loads((directory/'state.json').read_text(encoding='utf-8'))
        rendered = state.get('rendered') or {}
        outputs = result['outputs']
        digest = (rendered.get('hashes') or {}).get('video')
        video = Path(outputs['video']).resolve()
        if (rendered.get('outputs') != outputs or not isinstance(digest, str) or
                not re.fullmatch(r'[0-9a-f]{64}', digest) or
                not video.is_relative_to(Path(config['output_dir']).resolve()) or
                video.suffix.lower() not in {'.mp4', '.mov'}):
            raise ValueError('Thành phẩm không khớp hồ sơ xuất video; chưa tải lên Drive.')
        frozen = dict(account_id=options['account_id'], folder_id=options['folder_id'],
                      source_id=source_id, sha256=digest)

        def transfer(event):
            # This is delivery progress, independent of the finished rendering %.
            return record(dict(event, **frozen))

        transfer(dict(state='queued', percent=0, message='Đang kiểm tra video để tải lên Google Drive.'))
        receipt = DriveClient(data_dir).upload_video(options, source_id=source_id,
            video_path=video, sha256=digest, cancel=cancel, progress=transfer)
        if (receipt.get('state') != 'uploaded' or receipt.get('sha256') != digest or
                receipt.get('account_id') != options['account_id'] or
                receipt.get('folder_id') != options['folder_id'] or not _receipt_link_matches(receipt)):
            raise ValueError('Drive chưa trả bằng chứng tải lên khớp video và thư mục đã chọn.')
        return record(dict(receipt, source_id=source_id, percent=100,
                           message='Đã lưu video thành phẩm trên Google Drive.'))
    except Exception as exc:
        # Backend errors are redacted. Avoid exposing arbitrary network response,
        # authorization code, credential or upload-session URLs in pipeline logs.
        from teodub.common import Cancelled
        from teodub.google_drive import DriveError, DriveAuthError
        if isinstance(exc, DriveAuthError):
            state = 'needs_auth'
        elif isinstance(exc, Cancelled) or (cancel is not None and cancel.is_set()):
            state = 'cancelled'
        elif isinstance(exc, DriveError) and exc.uncertain:
            state = 'unknown'
        else:
            state = 'failed'
        message = str(exc) if isinstance(exc, (DriveError, ValueError, Cancelled)) else 'Chưa lưu được video lên Drive. Kiểm tra kết nối và tiếp tục tải; video trên máy được giữ.'
        outcome = dict(frozen, state=state, percent=0, message=message,
                       account_id=raw.get('account_id', '') if isinstance(raw, dict) else '',
                       folder_id=raw.get('folder_id', '') if isinstance(raw, dict) else '')
        # Saving a cloud outcome must not turn an already completed render into a
        # failed render, even when the status file itself cannot be written.
        try:
            return record(outcome)
        except OSError:
            return outcome


def drive_upload_summary(config, directory):
    """A passive local snapshot for UI; never creates a client or connection."""
    options = config.get('drive_upload') or {}
    if not isinstance(options, dict) or not options.get('enabled'):
        return dict(label='Không bật', details='Không bật tự lưu Google Drive cho phiên này.')
    if options.get('mode') == 'sync_folder':
        from teodub.drive_folder import folder_delivery_summary
        return folder_delivery_summary(config, directory)
    try:
        value = json.loads((Path(directory)/'drive-upload.json').read_text(encoding='utf-8'))
        if not isinstance(value, dict):
            raise ValueError('Trạng thái Drive chưa đọc được.')
        if (value.get('account_id') != options.get('account_id') or
                value.get('folder_id') != options.get('folder_id')):
            raise ValueError('Kết quả Drive không khớp nơi lưu đã chọn.')
        if value.get('state') == 'uploaded':
            root = Path(config.get('drive_data_dir', ''))
            if not root.is_absolute():
                raise ValueError('Chưa xác định thư mục dữ liệu của phiên Drive.')
            root = root.resolve()
            job = Path(directory).resolve()
            source_id = job.relative_to(root).as_posix()
            saved = json.loads((job/'state.json').read_text(encoding='utf-8'))
            digest = ((saved.get('rendered') or {}).get('hashes') or {}).get('video')
            if (not digest or value.get('sha256') != digest or value.get('source_id') != source_id or
                    not _receipt_link_matches(value)):
                raise ValueError('Kết quả Drive chưa khớp video đã xuất của phiên này.')
        label = DRIVE_LABELS.get(value.get('state'), 'Cần kiểm tra tải lên')
        percent = value.get('percent')
        if value.get('state') == 'uploading' and type(percent) in (int, float):
            label += f' {max(0, min(100, int(percent)))}%'
        details = str(value.get('message') or label)
        if value.get('state') == 'uploaded' and value.get('url'):
            details += '\n'+value['url']
        return dict(value, label=label, details=details)
    except FileNotFoundError:
        return dict(label='Chờ tải lên', details='Tèo sẽ tải video lên Drive sau khi xuất thành công.')
    except (OSError, ValueError, TypeError, AttributeError):
        return dict(label='Cần kiểm tra tải lên', details='Chưa đọc được trạng thái Drive của phiên này.')
