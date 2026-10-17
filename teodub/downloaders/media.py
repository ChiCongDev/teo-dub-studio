"""Read-only complete video decoding for downloaded sources."""
from pathlib import Path
import time
from teodub.common import hash_file


def inspect_video(path, timeout=300, check=None):
    """Read metadata and decode every video frame without modifying the source."""
    import av
    if check:
        check()
    path = Path(path).resolve(strict=True)
    if not path.is_file() or path.suffix.lower() not in (".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"):
        raise ValueError("Chọn tệp video MP4, MOV, MKV, AVI, WEBM hoặc M4V.")
    before = path.stat()
    if not before.st_size:
        raise ValueError("Tệp rỗng.")
    started = time.monotonic()
    frames = 0
    with av.open(str(path), timeout=(10, 10)) as container:
        streams = list(container.streams.video)
        if not streams:
            raise ValueError("Tệp không có luồng hình ảnh.")
        stream = streams[0]
        width, height = stream.codec_context.width, stream.codec_context.height
        rate = float(stream.average_rate or 0)
        duration = float(stream.duration * stream.time_base) if stream.duration is not None else float(container.duration or 0) / av.time_base
        audio = bool(container.streams.audio)
        codec = stream.codec_context.name
        for frame in container.decode(stream):
            if check:
                check()
            frames += 1
            if time.monotonic() - started > timeout:
                raise ValueError("Quá 5 phút kiểm tra. Video chưa được xác nhận; hãy thử tệp ngắn hơn.")
        if frames < 2 or width <= 0 or height <= 0 or duration <= 0:
            raise ValueError("Không đủ khung hình hoặc thời lượng video không hợp lệ.")
    digest = hash_file(path)
    if check:
        check()
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError("Tệp đang thay đổi. Chờ sao chép hoàn tất rồi nhập lại.")
    return dict(path=str(path), size=after.st_size, mtime_ns=after.st_mtime_ns, sha256=digest,
                width=width, height=height, fps=rate, duration=round(duration, 3),
                decoded_frames=frames, has_audio=audio, codec=codec,
                validation="Đã giải mã toàn bộ luồng hình; chưa đánh giá chất lượng âm thanh/nội dung.")
