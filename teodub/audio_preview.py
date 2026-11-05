"""Nghe thử âm lượng (owner 05/11/2026): the first seconds of a video this link has already finished, exported again with the
two levels the owner is trying (the original sound, the dubbed voice), through Tèo's own export (`render.render_video`: the
original sound is lowered under the voice as always). It reuses the finished video's source, lines and voice files, so it makes no
new voice, calls no AI and changes nothing of the link; the short result goes to a temporary folder and is opened to be heard."""
from pathlib import Path
import shutil
import subprocess
import time
import uuid

from teodub.campaign import read_json
from teodub.common import check_cancel

SECONDS = 20
KEEP_SECONDS = 6*3600  # Old previews are removed the next time one is made.


def preview_root(data_dir):
    return Path(data_dir).resolve()/'preview-mix'


def cleanup_old(data_dir, now=None):
    root, now = preview_root(data_dir), time.time() if now is None else now
    for folder in root.iterdir() if root.is_dir() else []:
        try:
            if folder.is_dir() and now-folder.stat().st_mtime > KEEP_SECONDS:
                shutil.rmtree(folder, ignore_errors=True)
        except OSError:
            continue


def finished_material(directory):
    """(source, cues, clips, speed, gap_ms, number) of the first finished video of the link whose export record, lines and voice
    files are all still there. Raises ValueError when no finished video can be used."""
    from teodub.campaign_settings import effective_job
    directory = Path(directory).resolve()
    manifest = read_json(directory/'campaign.json')
    reasons = []
    for item in manifest['items']:
        if item['status'] != 'completed' or not (item.get('outputs') or {}).get('plan'):
            continue
        try:
            plan = read_json(item['outputs']['plan'])
            clips = read_json(effective_job(directory, item)/'voice'/'manifest.json')['clips']
            cues, source = plan['cues'], Path(plan['source'])
            if not cues or len(cues) != len(clips) or not source.is_file() or not all(Path(c['path']).is_file() for c in clips):
                raise ValueError('thiếu video gốc hoặc file giọng')
            return source, cues, clips, plan.get('requested_speed', 1.0), int(plan.get('gap_ms', 100)), item['number']
        except (OSError, ValueError, KeyError, TypeError) as exc:
            reasons.append(f'Video {item["number"]}: {exc}')
    raise ValueError('Chưa có video đã xong nào còn đủ dữ liệu để nghe thử.'+(' '+'; '.join(reasons[:3]) if reasons else ''))


def _trim(source, seconds, target, cancel):
    import imageio_ffmpeg
    check_cancel(cancel)
    command = [imageio_ffmpeg.get_ffmpeg_exe(), '-hide_banner', '-loglevel', 'error', '-nostdin', '-n', '-i', str(source),
               '-t', f'{seconds:.3f}', '-map', '0:v:0', '-map', '0:a:0?', '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '30',
               '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(target)]
    done = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=300,
                          creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if done.returncode or not Path(target).is_file():
        raise ValueError('Chưa cắt được đoạn đầu của video: '+done.stderr.decode('utf-8', 'replace')[-300:])


def render_preview(source, cues, clips, output_dir, *, original_gain, voice_gain, speed=1.0, gap_ms=100, seconds=SECONDS,
                   cancel=None, progress=None):
    """Export the first `seconds` of the video (whole lines only, at least one) with these levels; returns the video path."""
    from teodub.render import render_video
    from teodub.audio_mix import validate_audio_mix
    levels = validate_audio_mix(dict(original_gain=original_gain, voice_gain=voice_gain))
    count = sum(cue['end_ms'] <= seconds*1000 for cue in cues) or 1
    cut = cues[count-1]['end_ms']/1000+1.5
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=False)
    trimmed = output_dir/'doan-dau.mp4'
    _trim(source, cut, trimmed, cancel)
    outputs = render_video(trimmed, cues[:count], clips[:count], output_dir/'ket-qua', speed=speed, gap_ms=gap_ms,
                           burn_subtitles=False, cancel=cancel, progress=progress, **levels)
    return Path(outputs['video'])


def make_preview(data_dir, directory, original_gain, voice_gain, *, cancel=None, progress=None):
    """Return the preview video of the link with the given levels (0–2 = 0–200%). Nothing of the link changes."""
    cleanup_old(data_dir)
    source, cues, clips, speed, gap_ms, number = finished_material(directory)
    folder = preview_root(data_dir)/uuid.uuid4().hex
    return render_preview(source, cues, clips, folder, original_gain=original_gain, voice_gain=voice_gain, speed=speed,
                          gap_ms=gap_ms, cancel=cancel, progress=progress)
