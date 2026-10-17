"""Public single-video downloads in an isolated, cancellable worker."""
from datetime import datetime
import json
import os
from pathlib import Path
import re
import time
from urllib.parse import parse_qs, urlsplit, urlunsplit
from teodub.common import Cancelled, check_cancel

MAX_BYTES = 2 * 1024**3
_cert_dir = None


def configure_tls():
    """libcurl on Windows needs an ASCII CA path; keep TLS verification enabled."""
    global _cert_dir
    import certifi
    import shutil
    import tempfile
    original = Path(certifi.where())
    if os.name == 'nt' and not str(original).isascii() and not os.environ.get('SSL_CERT_FILE'):
        _cert_dir = tempfile.TemporaryDirectory(prefix='teo-ca-')
        target = Path(_cert_dir.name)/'cacert.pem'
        shutil.copyfile(original, target)
        os.environ['SSL_CERT_FILE'] = str(target)


def share_url(text):
    # Chat apps may copy Markdown links rather than plain share text.
    text = re.sub(r'\[[^\]]*\]\((https?://[^\s)]+)\)', r'\1', text)
    urls = re.findall(r'https?://[^\s<>"\u3000]+', text.strip())
    urls = list(dict.fromkeys(u.rstrip('.,;!?)\'】》。，；！）') for u in urls))
    if len(urls) != 1:
        raise ValueError('Dán một link video Douyin/TikTok, hoặc đoạn chia sẻ chứa một link.')
    u = urlsplit(urls[0])
    if u.username or u.password or u.port not in (None, 80, 443):
        raise ValueError('Link video không hợp lệ.')
    host, path = (u.hostname or '').lower(), u.path
    if host in ('douyin.com', 'www.douyin.com'):
        ident = re.fullmatch(r'/video/(\d+)/?', path)
        modal = parse_qs(u.query).get('modal_id', [''])[0]
        if ident or modal.isdigit():
            return 'https://www.douyin.com/video/' + (ident[1] if ident else modal)
    elif host in ('iesdouyin.com', 'www.iesdouyin.com'):
        ident = re.fullmatch(r'/share/video/(\d+)/?', path)
        if ident:
            return 'https://www.douyin.com/video/' + ident[1]
    elif host in ('tiktok.com', 'www.tiktok.com', 'm.tiktok.com'):
        if re.fullmatch(r'/@[^/]+/video/\d+/?', path) or re.fullmatch(r'/t/[A-Za-z0-9]+/?', path):
            return urlunsplit(('https', 'www.tiktok.com', path, '', ''))
    elif host in ('v.douyin.com', 'vm.tiktok.com', 'vt.tiktok.com'):
        if re.fullmatch(r'/[A-Za-z0-9_-]+/?', path):
            return urlunsplit(('https', host, path, '', ''))
    raise ValueError('Hiện nhận link một video Douyin/TikTok; chưa nhận trang cá nhân, danh sách hoặc livestream.')


DownloadCancelled = Cancelled


def user_error(exc):
    text = str(exc).lower()
    if isinstance(exc, ValueError):
        return str(exc)
    if 'fresh cookies' in text:
        return 'Cách tải trực tiếp chưa đọc được video này. Lỗi này không có nghĩa video đã bị xóa hay bắt buộc đăng nhập. Tèo chưa tải được video.'
    if any(s in text for s in ('cookies', 'login', 'sign in', 'captcha', 'verify you', '403', '429')):
        return 'Nền tảng yêu cầu xác minh/đăng nhập hoặc đang giới hạn tải. Mở link trong trình duyệt để kiểm tra, hoặc nhập video đã tải về. Tèo chưa tải được video này.'
    if any(s in text for s in ('timed out', 'timeout', 'connection', 'resolve', 'network')):
        return 'Không kết nối được tới video. Kiểm tra mạng rồi thử lại.'
    if 'format' in text:
        return 'Link này chưa cung cấp MP4 có cả hình và tiếng phù hợp để tải trực tiếp.'
    return 'Chưa tải được video: link có thể đã hết hạn, bị xóa hoặc chưa được hỗ trợ. Thử link chia sẻ khác.'


def validate_downloaded_video(video, check):
    """Decode downloaded bytes; distinguish bad remote media from local failures."""
    import av
    from teodub.downloaders.media import inspect_video
    from teodub.downloaders.browser_download import MediaTransferError
    video = Path(video)
    check()
    if not video.is_file() or not video.stat().st_size:
        raise MediaTransferError('Chưa nhận được tệp MP4 tải hoàn chỉnh.', reason='invalid_media')
    if video.stat().st_size > MAX_BYTES:
        raise ValueError('Video vượt giới hạn tải 2 GB của bản này.')
    try:
        metadata = inspect_video(video, check=check)
        samples = 0
        if metadata.get('has_audio'):
            with av.open(str(video)) as container:
                for frame in container.decode(audio=0):
                    check()
                    samples += frame.samples
            if not samples:
                raise ValueError('Chưa đọc được âm thanh của video tải về.')
    except (ValueError, av.error.InvalidDataError, av.error.EOFError,
            av.error.DecoderNotFoundError) as exc:
        check()
        raise MediaTransferError('Tệp từ nguồn tải chưa đọc được đầy đủ hình và tiếng: ' + str(exc),
                                 reason='invalid_media') from exc
    metadata['audio_decoded_samples'] = samples
    check()
    return metadata


def download(config, ydl_factory=None, *, cancel=None, progress_callback=None):
    from teodub.downloaders.media_names import download_path
    started_at = datetime.now().astimezone()
    url = share_url(config['url'])
    folder = Path(config['folder']).resolve()
    # Fail before contacting a provider if even a short dated name cannot fit.
    download_path(folder, 'Video', started_at.date())
    folder.mkdir(parents=True, exist_ok=False)
    stop = Path(config['stop_file'])
    def check():
        check_cancel(cancel)
        if stop.exists():
            raise DownloadCancelled('Đã hủy tải. Video nguồn đang chọn được giữ nguyên.')
    def emit(message, **extra):
        with (folder/'events.jsonl').open('a', encoding='utf-8') as f:
            f.write(json.dumps(dict(message=message, **extra), ensure_ascii=False)+'\n')
        if progress_callback is not None:
            progress_callback(message, **extra)
    check()
    emit('Đang lấy thông tin video…')
    last_update = [0.0]
    def progress(data):
        check()
        count = data.get('downloaded_bytes') or 0
        total = data.get('total_bytes') or data.get('total_bytes_estimate') or 0
        if max(count, total) > MAX_BYTES:
            raise ValueError('Video vượt giới hạn tải 2 GB của bản này.')
        now = time.monotonic()
        if now-last_update[0] >= .5 or data.get('status') == 'finished':
            percent = min(100, count*100/total) if total else None
            source = data.get('provider_name')
            message = f'Đang tải: {percent:.0f}%' if percent is not None else f'Đã tải {count/1048576:.1f} MB'
            emit((source + ': ' if source else '') + message, percent=percent,
                 **{key: data[key] for key in ('provider', 'provider_name', 'provider_index',
                                               'provider_count', 'stage') if key in data})
            last_update[0] = now
    class QuietLogger:
        def debug(self, message): pass
        def warning(self, message): pass
        def error(self, message): pass
    opts = dict(format='best[ext=mp4][vcodec!=none][acodec!=none][protocol^=http]',
                outtmpl=str(folder/'video.%(ext)s'), noplaylist=True, quiet=True,
                no_warnings=True, logger=QuietLogger(), progress_hooks=[progress],
                socket_timeout=20, retries=2, fragment_retries=2, max_filesize=MAX_BYTES,
                cachedir=False, windowsfilenames=True, overwrites=False,
                fixup='never', postprocessors=[], enable_file_urls=False)
    info = None
    if ydl_factory is None:
        from teodub.downloaders.download_cache import reuse_download
        info = reuse_download(url, folder, check, emit)
    if info is not None:
        check()
    elif ydl_factory is None and 'douyin.com' in (urlsplit(url).hostname or ''):
        from teodub.downloaders.browser_download import download_via_browser
        info = download_via_browser(url, folder, check, progress, emit,
                                    validate=lambda path: validate_downloaded_video(path, check))
    else:
        if ydl_factory is None:
            configure_tls()
            from yt_dlp import YoutubeDL
            ydl_factory = YoutubeDL
        with ydl_factory(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            check()
            if not info or info.get('_type', 'video') != 'video' or info.get('entries') is not None:
                raise ValueError('Link phải trỏ đến một video, không phải danh sách.')
            if info.get('is_live') or info.get('live_status') in ('is_live', 'is_upcoming'):
                raise ValueError('Chưa hỗ trợ tải livestream.')
            if (info.get('filesize') or 0) > MAX_BYTES:
                raise ValueError('Video vượt giới hạn tải 2 GB của bản này.')
            emit('Đã tìm thấy video. Đang tải…')
            ydl.process_info(info)
    check()
    video = folder/'video.mp4'
    if not video.is_file() or not 0 < video.stat().st_size <= MAX_BYTES:
        raise ValueError('Chưa nhận được tệp MP4 tải hoàn chỉnh.')
    emit('Đã tải xong. Đang kiểm tra hình và tiếng…', percent=100)
    metadata = dict(info.get('verified_metadata') or validate_downloaded_video(video, check))
    check()
    title = str(info.get('title') or 'Video đã tải')[:240]
    destination = download_path(folder, info.get('title'), started_at.date())
    from teodub.downloaders.browser_download import _publish_media
    _publish_media(video, destination)
    video = destination
    metadata['path'] = str(video)
    check()
    return dict(status='downloaded', video=str(video), url=url,
                downloaded_at=started_at.isoformat(timespec='seconds'),
                download_date=started_at.date().isoformat(),
                provider=info.get('provider', 'direct'),
                **{key: info[key] for key in ('cache_source', 'original_provider', 'provider_attempts') if key in info},
                title=title, metadata=metadata,
                message=(('Đã dùng lại và kiểm tra video đã tải trước đó; không tốn thêm lượt tải. Sẵn sàng xử lý âm thanh.'
                          if info.get('provider') == 'local_cache' else 'Đã tải và kiểm tra video. Bấm Dùng để lồng tiếng để chọn video này.')
                         if metadata.get('has_audio') else 'Đã tải video nhưng không có tiếng gốc. Cần SRT Việt sẵn nếu muốn lồng tiếng.'))

