"""Public Facebook/YouTube video imports for the local learning library.

No browser profile, cookies, account API or personal browser is consulted.
Douyin/TikTok keep their existing isolated download path.
"""
from datetime import datetime
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import time
from urllib.parse import parse_qs, urlsplit

from teodub.common import Cancelled, atomic_json, check_cancel

MAX_BYTES = 2 * 1024**3
YT_ID = re.compile(r'[A-Za-z0-9_-]{11}')
FB_TOKEN = r'[A-Za-z0-9_-]+'


def learning_url(text):
    from teodub.downloaders.video_download import share_url
    text = re.sub(r'\[[^\]]*\]\((https?://[^\s)]+)\)', r'\1', str(text).strip())
    urls = re.findall(r'https?://[^\s<>"\u3000]+', text)
    urls = list(dict.fromkeys(u.rstrip('.,;!?)\'】》。，；！）') for u in urls))
    if len(urls) != 1:
        raise ValueError('Dán một link video Facebook, YouTube, Douyin hoặc TikTok.')
    parsed = urlsplit(urls[0])
    if parsed.username or parsed.password or parsed.port not in (None, 80, 443):
        raise ValueError('Link video không hợp lệ.')
    host, path = (parsed.hostname or '').lower(), parsed.path
    query = parse_qs(parsed.query)
    if host in ('youtube.com', 'www.youtube.com', 'm.youtube.com', 'youtu.be', 'www.youtu.be',
                'youtube-nocookie.com', 'www.youtube-nocookie.com'):
        ident = None
        if host in ('youtu.be', 'www.youtu.be'):
            ident = path.strip('/')
        elif path == '/watch':
            values = query.get('v', [])
            ident = values[0] if len(values) == 1 else None
        else:
            match = re.fullmatch(r'/(?:shorts|embed|live)/([A-Za-z0-9_-]{11})/?', path)
            if match:
                ident = match[1]
        if ident and YT_ID.fullmatch(ident):
            return 'https://www.youtube.com/watch?v='+ident
        raise ValueError('Dán link một video/Shorts YouTube; chưa nhận link kênh hoặc danh sách.')
    if host in ('fb.watch', 'www.fb.watch'):
        if re.fullmatch('/'+FB_TOKEN+'/?', path):
            return 'https://fb.watch/'+path.strip('/')+'/'
        raise ValueError('Link Facebook Watch không hợp lệ.')
    if host in ('facebook.com', 'www.facebook.com', 'm.facebook.com', 'mbasic.facebook.com', 'web.facebook.com'):
        if path.rstrip('/') in ('/watch', '/video.php'):
            values = query.get('v', [])
            if len(values) == 1 and values[0].isdigit():
                return 'https://www.facebook.com/watch/?v='+values[0]
        if re.fullmatch(r'/reel/\d+/?', path):
            return 'https://www.facebook.com/reel/'+path.strip('/').split('/')[-1]
        if re.fullmatch(r'/share/[rv]/'+FB_TOKEN+'/?', path):
            return 'https://www.facebook.com'+path.rstrip('/')+'/'
        # Named Page videos, including Facebook's optional video-title segment.
        if re.fullmatch(r'/[^/?#]+/videos/(?:[^/?#]+/)?\d+/?', path):
            return 'https://www.facebook.com'+path.rstrip('/')+'/'
        raise ValueError('Dán link Facebook video, Reel, Watch hoặc link chia sẻ video; chưa nhận link Page/bảng tin.')
    return share_url(urls[0])


def learning_platform(url):
    host = urlsplit(url).hostname or ''
    if host == 'www.youtube.com':
        return 'YouTube'
    if host in ('www.facebook.com', 'fb.watch'):
        return 'Facebook'
    return None


def _node_runtime():
    """Prefer the app-local runtime; require yt-dlp's documented minimum."""
    candidates = [Path(__file__).resolve().parents[1]/'tools/node-runtime/node.exe', shutil.which('node')]
    for raw in candidates:
        if not raw or not Path(raw).is_file():
            continue
        try:
            version = subprocess.run([str(raw), '--version'], stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=5, text=True,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if version.returncode == 0 and int(version.stdout.strip().lstrip('v').split('.')[0]) >= 22:
                return str(Path(raw).resolve())
        except (OSError, ValueError, subprocess.TimeoutExpired):
            continue
    raise ValueError('Tải YouTube cần Node.js 22 trở lên. Chưa tìm thấy bộ chạy trong Tèo hoặc trên máy.')


def _user_error(exc, platform):
    message = str(exc).lower()
    if any(word in message for word in ('private', 'login', 'log in', 'sign in', 'cookies', 'confirm your age', 'not a bot')):
        return platform+' yêu cầu đăng nhập/xác minh hoặc video không công khai. Chọn video công khai hoặc dùng tệp đã tải trên máy.'
    if any(word in message for word in ('403', '429', 'rate limit', 'too many requests')):
        return platform+' đang giới hạn tải video. Thử lại sau hoặc dùng tệp trên máy.'
    if any(word in message for word in ('removed', 'unavailable', 'not found', 'deleted', '404')):
        return platform+' chưa cung cấp video này: có thể bị xóa, hạn chế truy cập hoặc link không đúng.'
    if any(word in message for word in ('timeout', 'timed out', 'connection', 'resolve')):
        return 'Không kết nối được tới '+platform+'. Kiểm tra mạng rồi thử lại.'
    return 'Chưa tải được video '+platform+'. Thử link video trực tiếp khác hoặc dùng tệp trên máy.'


def download_learning_video(text, job_dir, *, cancel=None, progress=lambda *_a, **_k: None, ydl_factory=None):
    """One isolated download, with audio/video merge and actual media checks."""
    check_cancel(cancel)
    url = learning_url(text)
    platform = learning_platform(url)
    if not platform:
        raise ValueError('Bộ tải học video này chỉ nhận Facebook/YouTube.')
    job = Path(job_dir)
    if not job.is_absolute():
        raise ValueError('Thư mục học video cần đường dẫn đầy đủ.')
    folder = job.resolve()/'media'
    folder.mkdir(parents=True, exist_ok=False)
    atomic_json(job/'request.json', dict(url=url, platform=platform))
    began = time.monotonic()
    counts = {}

    def check():
        check_cancel(cancel)
        if time.monotonic()-began > 1200:
            raise ValueError('Tải mẫu quá 20 phút; dùng video ngắn hơn hoặc tệp trên máy.')

    def hook(data):
        check()
        key = str(data.get('filename') or data.get('format_id') or 'stream')
        counts[key] = max(counts.get(key, 0), data.get('downloaded_bytes') or 0)
        if sum(counts.values()) > MAX_BYTES or (data.get('total_bytes') or data.get('total_bytes_estimate') or 0) > MAX_BYTES:
            raise ValueError('Video mẫu vượt giới hạn tải 2 GB.')
        progress(f'{platform}: đã tải {sum(counts.values())/1048576:.1f} MB', percent=None)

    class QuietLogger:
        def debug(self, _message): pass
        def warning(self, _message): pass
        def error(self, _message): pass

    try:
        import imageio_ffmpeg
        opts = dict(format='bv[ext=mp4][height<=720]+ba[ext=m4a]/b[ext=mp4][height<=720]/bv[height<=720]+ba/b[height<=720]',
            outtmpl=str(folder/'video.%(ext)s'), merge_output_format='mp4',
            postprocessors=[dict(key='FFmpegVideoRemuxer', preferedformat='mp4')],
            ffmpeg_location=imageio_ffmpeg.get_ffmpeg_exe(), noplaylist=True,
            quiet=True, no_warnings=True, logger=QuietLogger(), progress_hooks=[hook],
            postprocessor_hooks=[lambda data: (check(), progress(platform+': đang ghép hình và tiếng…', percent=None))],
            socket_timeout=20, retries=1, fragment_retries=1, max_filesize=MAX_BYTES,
            cachedir=False, windowsfilenames=True, overwrites=False, enable_file_urls=False,
            remote_components=set())
        if platform == 'YouTube':
            opts['js_runtimes'] = {'node': {'path': _node_runtime()}}
        if ydl_factory is None:
            from teodub.downloaders.video_download import configure_tls
            configure_tls()
            from yt_dlp import YoutubeDL
            ydl_factory = YoutubeDL
        check()
        progress(platform+': đang lấy video công khai…', percent=None)
        with ydl_factory(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            check()
            if not isinstance(info, dict) or info.get('_type', 'video') != 'video' or info.get('entries') is not None:
                raise ValueError('Link cần trỏ đến một video, không phải kênh hoặc danh sách.')
            if info.get('is_live') or info.get('live_status') in ('is_live', 'is_upcoming'):
                raise ValueError('Chưa học livestream đang phát hoặc sắp phát. Chọn bản video đã lưu.')
            formats = info.get('requested_formats') or [info]
            size = sum(f.get('filesize') or f.get('filesize_approx') or 0 for f in formats)
            if not math.isfinite(size) or size > MAX_BYTES:
                raise ValueError('Video mẫu vượt giới hạn tải 2 GB.')
            ydl.process_info(info)
        check()
        source = folder/'video.mp4'
        if source.is_symlink() or not source.is_file() or not 0 < source.stat().st_size <= MAX_BYTES:
            raise ValueError('Chưa nhận được video MP4 tải và ghép hoàn chỉnh.')
        progress(platform+': kiểm tra video và âm thanh…', percent=None)
        from teodub.downloaders.video_download import validate_downloaded_video
        metadata = validate_downloaded_video(source, check)
        if not metadata.get('has_audio'):
            raise ValueError('Video không có âm thanh để học giọng.')
        result = dict(status='downloaded', source_path=str(source), video=str(source), url=url,
            title=str(info.get('title') or platform+' video')[:240], provider='yt-dlp', platform=platform,
            downloaded_at=datetime.now().astimezone().isoformat(timespec='seconds'), metadata=metadata)
        check()
        atomic_json(job/'result.json', result)
        return result
    except Exception as exc:
        try:
            check_cancel(cancel)
        except Cancelled as stopped:
            exc = stopped
        if isinstance(exc, Cancelled):
            failure = dict(status='cancelled', message=str(exc))
        else:
            failure = dict(status='failed', message=str(exc) if isinstance(exc, ValueError) else _user_error(exc, platform))
        atomic_json(job/'result.json', dict(url=url, platform=platform, **failure))
        if isinstance(exc, Cancelled):
            raise exc
        raise ValueError(failure['message']) from exc
