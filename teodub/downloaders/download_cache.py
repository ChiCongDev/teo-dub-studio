"""Reuse a verified download of this exact share link from sibling Teo jobs."""
import hashlib
import json
import os
from pathlib import Path
import re


CHUNK_BYTES = 256 * 1024
MAX_REPORT_BYTES = 128 * 1024


def reuse_download(url, folder, check, emit):
    """Copy a matching local download to a fresh job, or return None.

    Only the selected download root is searched. Reports and paths are not
    trusted: the media must remain inside its sibling job and the complete
    copied bytes must match the recorded digest. The normal download caller
    still decodes the video and audio before declaring success.
    """
    from teodub.downloaders.video_download import MAX_BYTES, share_url
    from teodub.downloaders.media_names import safe_filename

    wanted = share_url(url)
    folder = Path(folder).resolve(strict=True)
    root = folder.parent
    target = folder / 'video.mp4'
    part = folder / 'video.mp4.part'
    check()
    if target.exists() or part.exists():
        raise FileExistsError('Tệp video đích đã tồn tại; không ghi đè.')
    try:
        candidates = sorted(root.glob('Teo-*'), key=lambda path: path.name)
    except OSError:
        return None
    for candidate in candidates:
        check()
        owned_part = False
        matched = False
        try:
            if candidate.is_symlink() or candidate.is_junction():
                continue
            base = candidate.resolve(strict=True)
            # Reject directory links escaping the selected download root.
            if base == folder or base.parent != root or not base.is_dir():
                continue
            report_path = base / 'result.json'
            if not report_path.is_file():
                continue
            if report_path.resolve(strict=True).parent != base:
                continue
            if report_path.stat().st_size > MAX_REPORT_BYTES:
                continue
            result = json.loads(report_path.read_text(encoding='utf-8'))
            if not isinstance(result, dict) or result.get('status') != 'downloaded':
                continue
            cached_url = result.get('url')
            if not isinstance(cached_url, str) or share_url(cached_url) != wanted:
                continue
            matched = True
            metadata = result.get('metadata')
            digest = metadata.get('sha256') if isinstance(metadata, dict) else None
            video_value = result.get('video')
            if not isinstance(digest, str) or not re.fullmatch('[0-9a-fA-F]{64}', digest):
                raise ValueError('missing digest')
            if not isinstance(video_value, str) or not video_value:
                raise ValueError('missing media path')
            source = Path(video_value)
            if not source.is_absolute():
                source = base / source
            if (not safe_filename(source.name) or source.suffix.lower() != '.mp4' or
                    source.is_symlink() or source.is_junction()):
                raise ValueError('unsafe media path')
            source = source.resolve(strict=True)
            if source.parent != base or not source.is_file():
                raise ValueError('media path outside job')
            before = source.stat()
            if not 0 < before.st_size <= MAX_BYTES:
                raise ValueError('invalid media size')
            check()
            emit('Đã thấy video của đúng link này trong thư mục tải. Đang kiểm tra và sao chép…')
            hasher = hashlib.sha256()
            count = 0
            # Exclusive creation means a concurrent download is never replaced.
            with source.open('rb') as original, part.open('xb') as copied:
                owned_part = True
                while True:
                    check()
                    chunk = original.read(CHUNK_BYTES)
                    if not chunk:
                        break
                    count += len(chunk)
                    if count > MAX_BYTES:
                        raise ValueError('media grew beyond limit')
                    copied.write(chunk)
                    hasher.update(chunk)
            check()
            after = source.stat()
            if (count != before.st_size or
                    (before.st_size, before.st_mtime_ns, before.st_ino) !=
                    (after.st_size, after.st_mtime_ns, after.st_ino) or
                    hasher.hexdigest() != digest.lower()):
                raise ValueError('cached media changed')
            check()
            if os.name == 'nt':
                # Windows rename refuses an existing destination and works on
                # USB/exFAT too, where creating hard links is unsupported.
                part.rename(target)
            else:
                # Unlike Windows, POSIX rename can overwrite a racing writer.
                os.link(part, target)
                part.unlink()
            owned_part = False
            emit('Đã dùng bản video tải trước đó, khớp hoàn toàn dữ liệu đã kiểm tra. Không cần gửi lại link lên web.')
            return dict(title=str(result.get('title') or 'Video đã tải')[:240],
                        provider='local_cache', cache_source=str(source),
                        original_provider=str(result.get('original_provider') or
                                              result.get('provider') or 'unknown')[:80])
        except FileExistsError:
            # Do not reinterpret a destination collision as an invalid cache
            # and proceed with a network download into the same destination.
            raise
        except (OSError, ValueError, TypeError):
            if matched:
                emit('Bản video đã tải trước đó thiếu hoặc đã thay đổi; bỏ qua bản này để kiểm tra nguồn khác.')
            continue
        finally:
            if owned_part:
                try:
                    part.unlink()
                except OSError:
                    # Keep cancellation/original failure authoritative even if
                    # antivirus temporarily holds this worker's partial file.
                    pass
    check()
    return None
