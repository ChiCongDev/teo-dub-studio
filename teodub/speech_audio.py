"""Measured speech pacing without changing pitch or asking the model for ignored knobs."""
from pathlib import Path
import os
import subprocess
import tempfile
import time

from teodub.common import check_cancel, hash_file

VOICE_SPEEDS = tuple(value/100 for value in range(80, 131, 5))


def validate_voice_speed(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or float(value) not in VOICE_SPEEDS:
        raise ValueError('Chọn tốc độ giọng từ 0,80× đến 1,30×, mỗi nấc 0,05×.')
    return float(value)


def apply_tempo(source, target, rate, *, cancel=None):
    """Write a job-local intermediate; retain the original engine audio for recovery."""
    import imageio_ffmpeg
    # Includes the selected speed multiplied by each reading style (0.92–1.06).
    if isinstance(rate, bool) or not isinstance(rate, (int, float)) or not .7 <= rate <= 1.4:
        raise ValueError('Nhịp đọc cuối phải nằm trong khoảng 0,70–1,40×.')
    source, target = Path(source).resolve(strict=True), Path(target).resolve()
    if source == target:
        raise ValueError('Lưu bản chỉnh nhịp riêng với giọng gốc.')
    check_cancel(cancel)
    before = hash_file(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.speech-tempo-', dir=target.parent) as folder:
        staged, log = Path(folder)/'paced.wav', Path(folder)/'ffmpeg.log'
        command = [imageio_ffmpeg.get_ffmpeg_exe(), '-hide_banner', '-loglevel', 'error', '-nostdin', '-n',
                   '-threads', '2', '-i', str(source), '-map', '0:a:0', '-vn',
                   '-af', f'atempo={rate:.8g}', '-ar', '48000', '-ac', '1', '-c:a', 'pcm_s16le', str(staged)]
        with log.open('wb') as errors:
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                       stderr=errors, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            deadline = time.monotonic()+120
            try:
                while process.poll() is None:
                    check_cancel(cancel)
                    if time.monotonic() > deadline:
                        raise ValueError('Chỉnh nhịp đọc quá thời gian chờ; chưa ghép vào video.')
                    time.sleep(.05)
                if process.wait() != 0:
                    raise ValueError('Chưa chỉnh được nhịp đọc; không thay bằng âm thanh lỗi.')
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=3)
        check_cancel(cancel)
        from teodub.tts import validate_audio
        actual = validate_audio(staged)
        original = validate_audio(source)
        expected = original['duration_s']/rate
        if abs(actual['duration_s']-expected) > max(.12, expected*.03):
            raise ValueError('Thời lượng giọng sau chỉnh nhịp không khớp; chưa dùng kết quả.')
        if hash_file(source) != before:
            raise ValueError('Giọng gốc thay đổi khi chỉnh nhịp; chưa ghép thành phẩm.')
        os.replace(staged, target)
    return validate_audio(target)
