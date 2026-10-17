"""Stage verified results for Drive for desktop; never claim cloud delivery."""
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import uuid

from teodub.common import Cancelled, atomic_json, check_cancel, hash_file


def normalize_folder_config(value):
    enabled = value.get('enabled', False)
    folder = value.get('folder_path', '')
    if (type(enabled) is not bool or type(value.get('version', 1)) is not int
            or value.get('version', 1) != 1 or not isinstance(folder, str)
            or any(ord(char) < 32 for char in folder)):
        raise ValueError('Lựa chọn thư mục đồng bộ không hợp lệ. Chọn lại thư mục.')
    if folder:
        path = Path(folder)
        if not path.is_absolute() or path == Path(path.anchor):
            raise ValueError('Chọn một thư mục đồng bộ cụ thể, không chọn cả ổ đĩa.')
        folder = str(path.resolve())
    if enabled and not folder:
        raise ValueError('Chọn thư mục được Google Drive đồng bộ trước khi bật tự lưu.')
    return dict(version=1, enabled=enabled, mode='sync_folder', folder_path=folder)


def _copy_verified(source, target, digest, cancel):
    check_cancel(cancel)
    if target.is_symlink() or getattr(target, 'is_junction', lambda: False)():
        raise ValueError('Tệp nhận không thuộc thư mục đồng bộ thông thường.')
    if target.exists():
        if target.is_file() and hash_file(target) == digest:
            return
        raise ValueError('Thư mục đồng bộ đã có tệp khác cùng tên. Giữ nguyên tệp; chọn phiên mới để kiểm tra.')
    temporary = target.parent/('.teo-copy-'+uuid.uuid4().hex+'.part')
    before = source.stat()
    try:
        checksum = hashlib.sha256()
        with source.open('rb') as reader, temporary.open('xb') as writer:
            for chunk in iter(lambda: reader.read(1024*1024), b''):
                check_cancel(cancel)
                checksum.update(chunk)
                writer.write(chunk)
            writer.flush()
            os.fsync(writer.fileno())
        after = source.stat()
        if (checksum.hexdigest() != digest or
                (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns)
                or hash_file(temporary) != digest):
            raise ValueError('Thành phẩm thay đổi trong lúc chép. Video gốc trên máy được giữ.')
        check_cancel(cancel)
        # Windows rename fails if another file appeared; never replace it.
        if target.exists():
            raise ValueError('Tệp cùng tên vừa xuất hiện. Chưa ghi đè tệp đồng bộ.')
        if os.name == 'nt':
            temporary.rename(target)
        else:
            os.link(temporary, target)
            temporary.unlink()
    finally:
        temporary.unlink(missing_ok=True)


def stage_completed_video(config, result, *, progress=None, cancel=None):
    from teodub.google_drive import normalize_drive_config
    options = normalize_drive_config(config)
    if not options['enabled'] or config.get('mode', 'full') != 'full' or result.get('status') != 'completed':
        return None
    directory = Path(config['directory']).resolve()
    status_path = directory/'drive-upload.json'
    frozen = dict(mode='sync_folder', folder_path=options['folder_path'], cloud_confirmed=False)

    def record(state, message, **extra):
        outcome = dict(frozen, state=state, message=message, **extra,
                       updated_at=datetime.now().astimezone().isoformat(timespec='seconds'))
        atomic_json(status_path, outcome)
        if progress:
            try:
                progress(dict(outcome))
            except Exception:
                pass
        return outcome

    try:
        check_cancel(cancel)
        root = Path(config.get('drive_data_dir', ''))
        if not root.is_absolute() or not directory.is_relative_to(root.resolve()) or directory == root.resolve():
            raise ValueError('Phiên video không thuộc thư mục dữ liệu Tèo đã chọn.')
        state = json.loads((directory/'state.json').read_text(encoding='utf-8'))
        rendered = state.get('rendered') or {}
        outputs = result.get('outputs') or {}
        if rendered.get('outputs') != outputs:
            raise ValueError('Thành phẩm không khớp hồ sơ xuất của phiên; chưa chép vào thư mục đồng bộ.')
        source_id = directory.relative_to(root.resolve()).as_posix()
        frozen.update(source_id=source_id, hashes={})
        folder = Path(options['folder_path'])
        if not folder.is_dir():
            raise ValueError('Thư mục đồng bộ chưa có hoặc ổ Google Drive chưa sẵn sàng. Mở Drive rồi bấm Tiếp tục.')
        output_root = Path(config['output_dir']).resolve()
        sources = {}
        for key, suffix in (('subtitles', '.srt'), ('video', '.mp4')):
            raw = Path(outputs.get(key, ''))
            digest = (rendered.get('hashes') or {}).get(key)
            if (not raw.is_absolute() or raw.is_symlink() or not raw.is_file()
                    or not raw.resolve().is_relative_to(output_root) or raw.suffix.lower() != suffix
                    or not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest)
                    or hash_file(raw) != digest):
                raise ValueError('Video hoặc SRT đã thay đổi sau khi xuất. Chưa chép thành phẩm.')
            sources[key] = raw.resolve()
            frozen['hashes'][key] = digest
        ident = hashlib.sha256(source_id.encode('utf-8')).hexdigest()[:24]
        target_folder = folder/('Teo-'+ident)
        if target_folder.is_symlink() or getattr(target_folder, 'is_junction', lambda: False)():
            raise ValueError('Thư mục nhận của phiên đã thay đổi; chưa chép tệp.')
        target_folder.mkdir(exist_ok=True)
        if target_folder.resolve().parent != folder.resolve():
            raise ValueError('Thư mục nhận nằm ngoài nơi đồng bộ đã chọn.')
        record('copying', 'Đang chép MP4 và SRT vào thư mục để Google Drive đồng bộ.')
        files = {}
        for key, source in sources.items():
            target = target_folder/source.name
            if target.resolve() != source:
                _copy_verified(source, target, frozen['hashes'][key], cancel)
            files[key] = str(target)
        return record('staged', 'Đã chép MP4 và SRT vào thư mục đồng bộ. Kiểm tra ứng dụng Google Drive để biết đã tải lên hay chưa.',
                      files=files, destination=str(target_folder))
    except Exception as exc:
        message = str(exc) if isinstance(exc, (ValueError, Cancelled)) else 'Chưa chép được thành phẩm. Kiểm tra thư mục, dung lượng ổ đĩa và mở Google Drive rồi tiếp tục.'
        outcome = dict(frozen, state='cancelled' if isinstance(exc, Cancelled) else 'failed', message=message)
        try:
            return record(outcome['state'], message)
        except OSError:
            return outcome


def folder_delivery_summary(config, directory):
    """Read only local receipts; a staged file is not proof of a cloud upload."""
    from teodub.google_drive import normalize_drive_config
    try:
        options = normalize_drive_config(config)
    except ValueError:
        return dict(label='Cần chọn thư mục', details='Lựa chọn thư mục đồng bộ chưa hợp lệ.')
    if not options['enabled']:
        return dict(label='Tắt', details='Không chép vào thư mục đồng bộ.')
    try:
        value = json.loads((Path(directory)/'drive-upload.json').read_text(encoding='utf-8'))
        if value.get('mode') != 'sync_folder' or value.get('folder_path') != options['folder_path']:
            raise ValueError('destination')
        root = Path(config.get('drive_data_dir', ''))
        job = Path(directory).resolve()
        if not root.is_absolute() or job == root.resolve():
            raise ValueError('root')
        source_id = job.relative_to(root.resolve()).as_posix()
        if value.get('source_id') not in (None, source_id):
            raise ValueError('source')
        if value.get('state') == 'staged':
            saved = json.loads((job/'state.json').read_text(encoding='utf-8'))
            hashes = (saved.get('rendered') or {}).get('hashes') or {}
            ident = hashlib.sha256(source_id.encode('utf-8')).hexdigest()[:24]
            destination = Path(options['folder_path'])/('Teo-'+ident)
            if value.get('destination') != str(destination) or not destination.is_dir():
                raise ValueError('folder')
            for key in ('video', 'subtitles'):
                source = Path(saved['rendered']['outputs'][key])
                target = destination/source.name
                if value.get('hashes', {}).get(key) != hashes.get(key) or value.get('files', {}).get(key) != str(target) or not target.is_file():
                    raise ValueError('receipt')
        labels = {'staged': 'Đã chép · kiểm tra Drive', 'copying': 'Đang chép vào thư mục',
                  'cancelled': 'Đã dừng chép', 'failed': 'Chép bị lỗi'}
        return dict(value, label=labels.get(value.get('state'), 'Chờ chép'),
                    details=str(value.get('message') or 'Kiểm tra trạng thái trong ứng dụng Drive.'))
    except FileNotFoundError:
        return dict(label='Chờ chép', details='Sau khi xuất, Tèo chép MP4/SRT vào thư mục đồng bộ đã chọn.')
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return dict(label='Cần kiểm tra thư mục', details='Chưa xác nhận bản chép của đúng phiên. Video trên máy được giữ.')
