"""CPU-bounded local rendering with whole-clip narration and checked publication.

The source is read only. Vietnamese captions are either a selectable MP4 track
or visibly burned into the picture, and one sidecar SRT is always preserved.
"""
from contextlib import contextmanager
from fractions import Fraction
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
import wave

from teodub.common import AwakeTime, Cancelled, atomic_json, check_cancel, hash_file
from teodub.audio_mix import validate_audio_mix
from teodub.caption_layout import layout as layout_captions, matched_size, write_captions
from teodub.subtitle_band import band_events
from teodub.subtitle_style import ass_force_style, validate_subtitle_style

RATE = 48000
GAP_FRAMES = 4800
CJK = re.compile(r'[\u3400-\u9fff\uf900-\ufaff\U00020000-\U000323af]')
WORK_PREFIX = '.render-work-'
# Windows (antivirus, a just-stopped encoder, waking from sleep) can hold a file for a few seconds.
CLEANUP_WAITS = (.2, .5, 1, 2, 3)


def _notify(progress, message, percent=None):
    if progress:
        progress(message, percent=percent)


def _stamp(milliseconds):
    milliseconds = int(milliseconds)
    hours, rest = divmod(milliseconds, 3600000)
    minutes, rest = divmod(rest, 60000)
    seconds, ms = divmod(rest, 1000)
    return f'{hours:02}:{minutes:02}:{seconds:02},{ms:03}'


def _write_srt(path, cues):
    with Path(path).open('x', encoding='utf-8') as file:
        for index, cue in enumerate(cues, 1):
            file.write(f'{index}\n{_stamp(cue["start_ms"])} --> {_stamp(cue["end_ms"])}\n{cue["text"]}\n\n')


def _video_info(path):
    import av
    with av.open(str(path)) as container:
        if len(container.streams.video) != 1:
            raise ValueError('Video nguồn cần đúng một luồng hình.')
        video = container.streams.video[0]
        duration = float(video.duration * video.time_base) if video.duration is not None else float(container.duration or 0)/av.time_base
        fps = float(video.average_rate or 0)
        if duration <= 0 or fps <= 0 or video.width <= 0 or video.height <= 0:
            raise ValueError('Không xác định được kích thước hoặc thời lượng nguồn.')
        return dict(duration_s=duration, width=video.width, height=video.height,
                    fps=min(fps, 30.0), has_audio=bool(container.streams.audio),
                    start_s=float((video.start_time or 0)*video.time_base))


def _normalize_clip(clip, target, cancel):
    import av
    import numpy as np
    path = Path(clip['path']).resolve(strict=True)
    if not path.is_file() or hash_file(path) != clip.get('sha256'):
        raise ValueError(f'Âm thanh câu {clip["id"]} bị thiếu hoặc đã thay đổi.')
    samples = original_samples = 0
    energy = 0.0
    with av.open(str(path)) as container, wave.open(str(target), 'wb') as output:
        output.setparams((2, 2, RATE, 0, 'NONE', 'not compressed'))
        if len(container.streams.audio) != 1:
            raise ValueError('Mỗi câu phải có đúng một luồng giọng đọc.')
        source_rate = container.streams.audio[0].codec_context.sample_rate
        if source_rate != clip.get('sample_rate') or source_rate <= 0:
            raise ValueError(f'Tần số âm thanh câu {clip["id"]} không khớp dữ liệu đã lưu.')
        resampler = av.AudioResampler(format='s16', layout='stereo', rate=RATE)

        def consume(frame):
            nonlocal samples, energy
            data = bytes(frame.planes[0])[:frame.samples*4]
            output.writeframesraw(data)
            values = np.frombuffer(data, dtype='<i2').astype(np.float64)/32768
            energy += float(np.dot(values, values))
            samples += frame.samples

        for frame in container.decode(audio=0):
            check_cancel(cancel)
            original_samples += frame.samples
            for converted in resampler.resample(frame):
                consume(converted)
        for converted in resampler.resample(None):
            consume(converted)
    check_cancel(cancel)
    actual_duration = original_samples/source_rate
    declared = clip.get('duration_s')
    if (not isinstance(declared, (int, float)) or not math.isfinite(declared)
            or abs(actual_duration-declared) > max(.05, 2/source_rate)):
        raise ValueError(f'Thời lượng thực của câu {clip["id"]} không khớp dữ liệu đã lưu.')
    if samples < RATE*.025 or math.sqrt(energy/max(samples*2, 1)) < .0001:
        raise ValueError(f'Giọng câu {clip["id"]} quá ngắn hoặc không có âm thanh rõ.')
    if hash_file(path) != clip['sha256']:
        raise ValueError('Tệp giọng đọc thay đổi trong lúc dựng video.')
    return samples, path


def _voice_probe(path, start_frame):
    """Select a non-silent reference window from every complete narration clip."""
    import numpy as np
    strongest = None
    position = 0
    with wave.open(str(path), 'rb') as source:
        while data := source.readframes(RATE//2):
            samples = np.frombuffer(data, dtype='<i2').reshape(-1, 2).mean(axis=1).astype(np.float32)/32768
            energy = float(np.mean(samples.astype(np.float64)**2))
            if len(samples) >= RATE*.025 and (strongest is None or energy > strongest[0]):
                strongest = (energy, position, samples.copy())
            position += len(samples)
    if strongest is None or strongest[0] < 1e-8:
        raise ValueError('Không tìm thấy phần giọng đọc để kiểm tra bản xuất.')
    _, offset, reference = strongest
    margin = int(RATE*.03)
    return dict(start=start_frame+offset-margin, reference=reference, margin=margin,
                actual=np.zeros(len(reference)+margin*2, dtype=np.float32))


def _write_narration(path, placements, duration_frames, cancel):
    silence = bytes(RATE*4)
    cursor = 0
    with wave.open(str(path), 'wb') as target:
        target.setparams((2, 2, RATE, 0, 'NONE', 'not compressed'))
        for placement in placements:
            while cursor < placement['start_frame']:
                check_cancel(cancel)
                amount = min(RATE, placement['start_frame']-cursor)
                target.writeframesraw(silence[:amount*4])
                cursor += amount
            copied = 0
            with wave.open(placement['normalized'], 'rb') as clip:
                while data := clip.readframes(RATE):
                    check_cancel(cancel)
                    target.writeframesraw(data)
                    copied += len(data)//4
            if copied != placement['frames']:
                raise ValueError('Giọng đọc bị thay đổi trước khi ghép.')
            cursor += copied
        while cursor < duration_frames:
            check_cancel(cancel)
            amount = min(RATE, duration_frames-cursor)
            target.writeframesraw(silence[:amount*4])
            cursor += amount


def _atempo(speed):
    factors = []
    while speed < .5:
        factors.append(.5)
        speed /= .5
    while speed > 2:
        factors.append(2.)
        speed /= 2
    factors.append(speed)
    return ','.join(f'atempo={value:.12g}' for value in factors)


def _run_ffmpeg(command, directory, duration, cancel, progress):
    log = directory/'ffmpeg.log'
    updates = directory/'progress.txt'
    command = [*command[:-1], '-progress', str(updates), command[-1]]
    check_cancel(cancel)
    with log.open('wb') as error:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=error, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        awake = AwakeTime(time.monotonic())
        timeout = min(14400, max(300, duration*20))
        last = 0.
        try:
            while process.poll() is None:
                check_cancel(cancel)
                now = time.monotonic()
                if awake.update(now) > timeout:
                    raise ValueError('Dựng video quá thời gian chờ. Bản xuất chưa được công bố.')
                if now-last >= .5:
                    try:
                        text = updates.read_text(encoding='utf-8')
                        values = re.findall(r'^out_time_us=(\d+)$', text, re.MULTILINE)
                        percent = min(85., 20+65*(int(values[-1])/1000000)/duration) if values else 20.
                    except (OSError, ValueError):
                        percent = 20.
                    _notify(progress, 'Đang dựng hình, giọng Việt và phụ đề…', percent)
                    last = now
                time.sleep(.15)
            check_cancel(cancel)
            if process.wait() != 0:
                with log.open('rb') as details:
                    details.seek(max(0, log.stat().st_size-6000))
                    message = details.read().decode('utf-8', errors='replace')
                raise ValueError('Không dựng được video: '+message[-3000:])
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)


def _source_brightness(source, info, cancel):
    import av
    import numpy as np
    values = []
    with av.open(str(source)) as container:
        stream = container.streams.video[0]
        stream.codec_context.thread_count = 4
        for fraction in (.15, .5, .85):
            check_cancel(cancel)
            wanted = info['start_s']+info['duration_s']*fraction
            container.seek(int(wanted/float(stream.time_base)), stream=stream, backward=True)
            for frame in container.decode(video=0):
                check_cancel(cancel)
                if frame.time is not None and frame.time+1/info['fps'] >= wanted:
                    data = frame.to_ndarray(format='gray')
                    values.append(dict(fraction=fraction, mean=float(np.mean(data)), std=float(np.std(data))))
                    break
    return values


def _probe_score(probe):
    import numpy as np
    reference = probe['reference']
    actual = probe['actual']
    margin = probe['margin']

    def score(offset, stride=6):
        left = reference[::stride].astype(np.float64)
        right = actual[offset:offset+len(reference):stride].astype(np.float64)
        left -= left.mean()
        right -= right.mean()
        energy = float(np.dot(left, left))
        target_energy = float(np.dot(right, right))
        dot = float(np.dot(left, right))
        return dot/max(math.sqrt(energy*target_energy), 1e-15), dot/max(energy, 1e-15)

    correlation, gain = score(margin)
    offset = margin
    if correlation < .85:
        offset = max(range(0, margin*2+1, 48), key=lambda value: score(value, 24)[0])
        offset = max(range(max(0, offset-48), min(margin*2, offset+48)+1, 3), key=lambda value: score(value)[0])
        correlation, gain = score(offset)
    return dict(correlation=correlation, relative_gain=gain, offset_ms=(offset-margin)*1000/RATE)


def _fill_audio_probes(path, probes, cancel):
    """Read timed decoded samples without loading an entire soundtrack into RAM."""
    import av
    import numpy as np
    energy, samples = 0., 0
    with av.open(str(path)) as container:
        resampler = av.AudioResampler(format='fltp', layout='stereo', rate=RATE)
        for frame in container.decode(audio=0):
            check_cancel(cancel)
            for converted in resampler.resample(frame):
                mono = converted.to_ndarray().mean(axis=0).astype(np.float32)
                start = round(converted.time*RATE) if converted.time is not None else samples
                end = start+len(mono)
                energy += float(np.dot(mono.astype(np.float64), mono.astype(np.float64)))
                samples += len(mono)
                for probe in probes:
                    low = max(start, probe['start'])
                    high = min(end, probe['start']+len(probe['actual']))
                    if high > low:
                        probe['actual'][low-probe['start']:high-probe['start']] = mono[low-start:high-start]
    return math.sqrt(energy/max(samples, 1))


def _expected_audio_mix(source, narration, work, probes, duration, effective_speed,
                        has_audio, original_gain, voice_gain, cancel):
    """Check narration first, then independently encode the requested reference mix.

    AAC reference and final MP4 use the same filter operations and codec settings:
    using mathematically equivalent amix weights changes AAC rounding enough to
    mask quiet voices. This graph is constructed independently of the final video
    graph, after verifying the full-volume narration. Defaults keep the old QC.
    """
    import imageio_ffmpeg
    _fill_audio_probes(narration, probes, cancel)
    narration_checks = []
    for index, probe in enumerate(probes, 1):
        score = _probe_score(probe)
        if score['correlation'] < .999 or not .99 <= score['relative_gain'] <= 1.01:
            raise ValueError(f'Chưa xác nhận có đủ giọng câu {index} trước khi trộn âm lượng.')
        narration_checks.append(dict(id=index, **score, stage='before_mix',
                                     applied_gain=voice_gain, intentionally_muted=voice_gain == 0))
        probe['actual'].fill(0)
    check_cancel(cancel)
    reference_dir = work/'mix-reference'
    reference_dir.mkdir()
    reference = reference_dir/'expected.m4a'
    if has_audio:
        filters = (f'[0:a:0]asetpts=PTS-STARTPTS,{_atempo(effective_speed)},aresample={RATE},'
                   f'aformat=channel_layouts=stereo,volume={original_gain:.9g},apad,'
                   f'atrim=duration={duration:.9f}[base];'
                   f'[1:a:0]volume={voice_gain:.9g}[speech];'
                   '[base][speech]amix=inputs=2:duration=longest:dropout_transition=0:normalize=0,')
    else:
        filters = f'[1:a:0]volume={voice_gain:.9g},'
    filters += f'alimiter=limit=0.98:level=0:latency=1,atrim=duration={duration:.9f}[a]'
    command = [imageio_ffmpeg.get_ffmpeg_exe(), '-hide_banner', '-nostdin', '-n',
               '-threads', '4', '-i', str(source), '-i', str(narration),
               '-filter_complex_threads', '2', '-filter_complex', filters, '-map', '[a]',
               '-c:a', 'aac', '-b:a', '192k', '-ar', str(RATE), '-t', f'{duration:.9f}', str(reference)]
    _run_ffmpeg(command, reference_dir, duration, cancel, None)
    reference_rms = _fill_audio_probes(reference, probes, cancel)
    for probe in probes:
        margin, length = probe['margin'], len(probe['reference'])
        probe['reference'] = probe['actual'][margin:margin+length].copy()
        probe['actual'].fill(0)
    return dict(reference_rms=reference_rms, narration_checks=narration_checks,
                original_gain=original_gain, voice_gain=voice_gain)


def _verify(source, video, expected, cues, probes, subtitle_mode, cancel, progress):
    import av
    import numpy as np
    info = _video_info(video)
    tolerance = max(.15, 2/expected['fps'])
    if abs(info['duration_s']-expected['duration_s']) > tolerance:
        raise ValueError('Thời lượng video xuất không khớp toàn bộ lời đọc.')
    if info['width'] != expected['width'] or info['height'] != expected['height']:
        raise ValueError('Kích thước hình xuất không khớp nguồn.')
    brightness = _source_brightness(source, expected['source'], cancel)
    samples = []
    decoded = 0
    audio_samples = 0
    audio_energy = 0.
    audio_peak = 0.
    output_audio_end = 0.
    wanted = iter([expected['duration_s']*value['fraction'] for value in brightness])
    next_sample = next(wanted, None)
    with av.open(str(video)) as container:
        expected_subtitle_streams = 0 if subtitle_mode == 'burned_ass' else 1
        if len(container.streams.audio) != 1 or len(container.streams.subtitles) != expected_subtitle_streams:
            raise ValueError('Bản xuất thiếu giọng trộn hoặc sai chế độ phụ đề Việt.')
        if container.streams.audio[0].codec_context.sample_rate != RATE:
            raise ValueError('Tần số âm thanh bản xuất không khớp kế hoạch.')
        container.streams.video[0].codec_context.thread_count = 4
        container.streams.audio[0].codec_context.thread_count = 4
        for packet in container.demux(container.streams.video[0], container.streams.audio[0]):
            for frame in packet.decode():
                check_cancel(cancel)
                if isinstance(frame, av.VideoFrame):
                    decoded += 1
                    while next_sample is not None and frame.time is not None and frame.time+1/info['fps'] >= next_sample:
                        pixels = frame.to_ndarray(format='gray')
                        samples.append(dict(mean=float(np.mean(pixels)), std=float(np.std(pixels))))
                        next_sample = next(wanted, None)
                else:
                    array = frame.to_ndarray()
                    if not np.isfinite(array).all():
                        raise ValueError('Âm thanh bản xuất có dữ liệu không hợp lệ.')
                    # AAC decoder yields planar float samples; enforce the format
                    # rather than interpreting another numeric representation.
                    if frame.format.name not in ('fltp', 'flt'):
                        raise ValueError('Không đọc được định dạng âm thanh bản xuất.')
                    if frame.format.is_planar:
                        mono = array.mean(axis=0).astype(np.float32)
                    else:
                        mono = array.reshape(-1, len(frame.layout.channels)).mean(axis=1).astype(np.float32)
                    if frame.time is None:
                        raise ValueError('Âm thanh bản xuất thiếu mốc thời gian.')
                    start = round(frame.time*RATE)
                    end = start+len(mono)
                    output_audio_end = max(output_audio_end, end/RATE)
                    audio_samples += len(mono)
                    audio_energy += float(np.dot(mono.astype(np.float64), mono.astype(np.float64)))
                    audio_peak = max(audio_peak, float(np.max(np.abs(array))))
                    for probe in probes:
                        low = max(start, probe['start'])
                        high = min(end, probe['start']+len(probe['actual']))
                        if high > low:
                            probe['actual'][low-probe['start']:high-probe['start']] = mono[low-start:high-start]
    if decoded < expected['duration_s']*expected['fps']-2 or abs(output_audio_end-expected['duration_s']) > .25:
        raise ValueError('Bản xuất chưa giải mã đủ hình hoặc âm thanh đến cuối video.')
    mix = expected.get('audio_mix')
    if not audio_samples or (mix is None and audio_energy/audio_samples < 1e-10):
        raise ValueError('Bản xuất không có âm thanh rõ.')
    if mix is not None:
        # Silence is valid only when it matches the independently rendered mix.
        rms = math.sqrt(audio_energy/audio_samples)
        if abs(rms-mix['reference_rms']) > max(1e-7, mix['reference_rms']*.002):
            raise ValueError('Âm lượng bản xuất không khớp hai mức âm thanh đã chọn.')
    if len(samples) != len(brightness) or not samples:
        raise ValueError('Chưa kiểm tra được hình nguồn và hình xuất.')
    failures = sum(1 for original, output in zip(brightness, samples)
                   if original['mean'] > 8 and output['mean'] < 2 and output['std'] < 2)
    if failures:
        raise ValueError('Hình xuất bị đen ở vị trí nguồn còn hình; chưa công bố video.')
    voice_checks = []
    for index, probe in enumerate(probes, 1):
        check_cancel(cancel)
        if mix is not None:
            reference_rms = float(np.sqrt(np.mean(probe['reference'].astype(np.float64)**2)))
            actual = probe['actual'][probe['margin']:probe['margin']+len(probe['reference'])]
            error = float(np.sqrt(np.mean((actual.astype(np.float64)-probe['reference'])**2)))
            if error > max(1e-7, reference_rms*.002):
                raise ValueError(f'Âm trộn câu {index} không khớp tiếng gốc và giọng lồng đã chọn.')
            if reference_rms < 1e-8:
                voice_checks.append(dict(id=index, intentionally_silent=True, rms_error=error))
                continue
        score = _probe_score(probe)
        if score['correlation'] < .5 or score['relative_gain'] < .35:
            raise ValueError(f'Chưa xác nhận có đủ giọng câu {index} trong video xuất.')
        voice_checks.append(dict(id=index, **score))
    if subtitle_mode == 'soft_mov_text':
        texts = []
        with av.open(str(video)) as container:
            for packet in container.demux(container.streams.subtitles[0]):
                data = bytes(packet)
                if len(data) >= 2:
                    length = int.from_bytes(data[:2], 'big')
                    if length:
                        texts.append(data[2:2+length].decode('utf-8'))
        if texts != [cue['text'] for cue in cues]:
            raise ValueError('Phụ đề trong MP4 không khớp đủ các câu Việt.')
    _notify(progress, 'Đã kiểm tra đủ hình, lời đọc và phụ đề.', 97)
    report = dict(technical_pass=True, duration_s=info['duration_s'], decoded_video_frames=decoded,
                decoded_audio_samples=audio_samples, audio_rms=math.sqrt(audio_energy/audio_samples),
                audio_peak=audio_peak, cue_count=len(cues), subtitle_mode=subtitle_mode,
                voice_checks=voice_checks, source_brightness=brightness, output_brightness=samples,
                content_review_required=True)
    if mix is not None:
        report.update(mix_checks=voice_checks, voice_checks=mix['narration_checks'],
                      audio_mix={key: mix[key] for key in ('original_gain', 'voice_gain')},
                      mix_reference_rms=mix['reference_rms'])
    return report


def _caption_boxes(picture, start_s, cues, effective_speed, width, height, options, cancel, progress):
    """One box per burned line that also hides the video's own caption; the wide band if the picture cannot be read."""
    from teodub.caption_cover import cover_boxes
    _notify(progress, 'Đang tìm phụ đề gốc trong hình để che vừa đủ…', 18)
    # The picture plays at 1/effective_speed, so output time t shows the source at start_s + t*effective_speed.
    intervals = [(start_s+cue['start_ms']/1000*effective_speed, start_s+cue['end_ms']/1000*effective_speed)
                 for cue in cues]
    size, weight = options['subtitle_size'], options['subtitle_weight']
    try:
        regions, report = cover_boxes(picture, intervals, check=lambda: check_cancel(cancel),
            progress=lambda share: _notify(progress, f'Đang tìm phụ đề gốc trong hình: {share*100:.0f}%', 18+share))
    except Cancelled:
        raise
    except Exception as exc:  # A picture that cannot be analysed must not leave the original captions bare.
        regions = [(band['x'], band['y'], band['x']+band['w'], band['y']+band['h'])
                   for band in band_events(cues, width, height, size, weight)]
        report = dict(fallback='wide_band', error=str(exc)[:300])
    # Big own captions get big Vietnamese text, the same size for every line of the video (owner 22/10).
    report = dict(report, layout='one_box', text_size=matched_size(size, report.get('caption_rows'), height))
    return layout_captions(cues, regions, report.get('caption_rows'), report['text_size'], weight, width, height), report


def _publish(source, target):
    if Path(target).exists():
        raise FileExistsError('Tệp thành phẩm đã tồn tại; không ghi đè.')
    if os.name == 'nt':
        Path(source).rename(target)
    else:
        os.link(source, target)
        Path(source).unlink()


def _remove_tree(path):
    """Delete a work folder, waiting briefly for files Windows still holds. Never raises."""
    for wait in (*CLEANUP_WAITS, None):
        try:
            shutil.rmtree(path)
            return True
        except FileNotFoundError:
            return True
        except OSError:
            if wait is None:
                return False
            time.sleep(wait)


@contextmanager
def _work_directory(output):
    """Temporary render files inside the result folder.

    Cleanup must not replace the render's own error: on 21/10 a locked file turned
    "quá thời gian chờ" into WinError 32. A folder that stays locked is left for
    clear_leftover_work on the next render of the same video.
    """
    work = Path(tempfile.mkdtemp(prefix=WORK_PREFIX, dir=output))
    try:
        yield work
    finally:
        _remove_tree(work)


def clear_leftover_work(folder):
    """Remove what a failed render left in this job's own result folder.

    Only Tèo's temporary work folders go; the result folder itself is removed only when
    nothing else is in it, so a retry keeps the video's name. Published or unknown
    files are never touched.
    """
    folder = Path(folder)
    try:
        entries = list(folder.iterdir())
    except OSError:
        return
    for entry in entries:
        if (entry.name.startswith(WORK_PREFIX) and entry.is_dir()
                and not entry.is_symlink() and not entry.is_junction()):
            _remove_tree(entry)
    try:
        folder.rmdir()
    except OSError:
        pass


def render_video(source_path, cues, clips, output_dir, *, speed=.9, original_gain=.1, voice_gain=1.0,
                 gap_ms=100, burn_subtitles=False, subtitle_color='white',
                 subtitle_weight='bold', subtitle_size=13, subtitle_box='none', cleaned_picture=None, cancel=None,
                 progress=None):
    """Render one fresh result directory and return absolute artifact paths."""
    import imageio_ffmpeg
    check_cancel(cancel)
    source = Path(source_path).resolve(strict=True)
    if not source.is_file() or not isinstance(speed, (int, float)) or not .05 <= speed <= 2:
        raise ValueError('Nguồn hoặc tốc độ video không hợp lệ.')
    audio_options = validate_audio_mix(dict(original_gain=original_gain, voice_gain=voice_gain))
    original_gain, voice_gain = audio_options['original_gain'], audio_options['voice_gain']
    if type(gap_ms) is not int or not 80 <= gap_ms <= 1000:
        raise ValueError('Khoảng nghỉ giữa các câu phải từ 80 đến 1000 mili giây.')
    subtitle_options = validate_subtitle_style(dict(
        burn_subtitles=burn_subtitles, subtitle_color=subtitle_color,
        subtitle_weight=subtitle_weight, subtitle_size=subtitle_size, subtitle_box=subtitle_box))
    box = subtitle_options.get('subtitle_box', 'none')
    gap_frames = round(gap_ms*RATE/1000)
    if not cues or len(cues) != len(clips):
        raise ValueError('Phải có đủ một tệp giọng cho từng câu phụ đề.')
    clean_cues = []
    for index, (cue, clip) in enumerate(zip(cues, clips), 1):
        text = ' '.join(cue.get('text', '').split()) if isinstance(cue.get('text'), str) else ''
        if (type(clip.get('id')) is not int or clip['id'] != index or not text or CJK.search(text)
                or type(cue.get('start_ms')) is not int or type(cue.get('end_ms')) is not int
                or cue['start_ms'] < 0 or cue['end_ms'] <= cue['start_ms']):
            raise ValueError('Câu Việt hoặc thứ tự các tệp giọng không hợp lệ.')
        clean_cues.append(dict(cue, text=text))
    info = _video_info(source)
    if any(cue['end_ms']/1000 > info['duration_s']+.2 for cue in clean_cues):
        raise ValueError('Mốc phụ đề nguồn vượt thời lượng video.')
    source_hash = hash_file(source)
    picture_source = source
    if cleaned_picture:
        picture_source = Path(cleaned_picture['path']).resolve(strict=True)
        binding = cleaned_picture['binding']
        if binding['source_sha256'] != source_hash or hash_file(picture_source) != cleaned_picture['sha256']:
            raise ValueError('Bản xóa chữ không khớp nguồn hoặc đã thay đổi.')
        picture_info = _video_info(picture_source)
        if (any(picture_info[k] != info[k] for k in ('width', 'height')) or
                abs(picture_info['duration_s']-info['duration_s']) > max(.1, 2/info['fps'])):
            raise ValueError('Bản xóa chữ bị sai kích thước hoặc thời lượng; chưa xuất.')
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=False)
    destinations = dict(video=output/f'{output.name}.mp4', subtitles=output/f'{output.name}.srt',
                        plan=output/'render-plan.json', quality=output/'quality.json')
    with _work_directory(output) as work:
        placements, probes, final_cues = [], [], []
        cursor = -gap_frames
        for index, (cue, clip) in enumerate(zip(clean_cues, clips), 1):
            check_cancel(cancel)
            _notify(progress, f'Đang kiểm tra và xếp giọng câu {index}/{len(clips)}…', 5+10*index/len(clips))
            normalized = work/f'voice-{index}.wav'
            frames, original = _normalize_clip(clip, normalized, cancel)
            start = max(round(cue['start_ms']*RATE/(1000*speed)), cursor+gap_frames)
            end = start+frames
            placement = dict(id=index, start_frame=start, end_frame=end, frames=frames,
                             source=str(original), sha256=clip['sha256'], normalized=str(normalized))
            placements.append(placement)
            probes.append(_voice_probe(normalized, start))
            final_cues.append(dict(start_ms=round(start*1000/RATE), end_ms=math.ceil(end*1000/RATE), text=cue['text']))
            cursor = end
        duration = max(info['duration_s']/speed, cursor/RATE+.2)
        fps = Fraction(info['fps']).limit_denominator(1001)
        duration = math.ceil(duration*float(fps))/float(fps)
        effective_speed = info['duration_s']/duration
        width, height = info['width']+info['width']%2, info['height']+info['height']%2
        narration = work/'narration.wav'
        _write_narration(narration, placements, math.ceil(duration*RATE), cancel)
        mix = None
        if original_gain != .1 or voice_gain != 1.0:
            _notify(progress, 'Đang kiểm tra hai mức âm lượng đã chọn…', 17)
            mix = _expected_audio_mix(source, narration, work, probes, duration, effective_speed,
                                      info['has_audio'], original_gain, voice_gain, cancel)
        subtitles, video = work/'final.srt', work/'render.partial.mp4'
        _write_srt(subtitles, final_cues)
        picture_index = 2 if cleaned_picture else 0
        picture = (f'[{picture_index}:v:0]setpts=(PTS-STARTPTS)/{effective_speed:.12g},'
                   f'fps={fps.numerator}/{fps.denominator},pad=ceil(iw/2)*2:ceil(ih/2)*2,'
                   f'tpad=stop_mode=clone:stop_duration={duration:.9f},trim=duration={duration:.9f},format=yuv420p')
        subtitle_mode = 'burned_ass' if subtitle_options['burn_subtitles'] else 'soft_mov_text'
        cover = None
        if subtitle_options['burn_subtitles'] and box == 'none':
            escaped = str(subtitles.resolve()).replace('\\', '/').replace(':', '\\:').replace("'", "\\'")
            style = ass_force_style(subtitle_options['subtitle_color'], subtitle_options['subtitle_weight'],
                                    subtitle_options['subtitle_size'])
            picture += (f",subtitles=filename='{escaped}':original_size={width}x{height}:"
                        f"force_style='{style}'[v]")
        elif subtitle_options['burn_subtitles']:
            # One rectangle per line (owner 22/10): the Vietnamese caption in its middle, sitting on the video's own
            # caption rows and just large enough to hide the caption shown there. Text and box come from one script.
            picture_start = (picture_info if cleaned_picture else info)['start_s']
            placed, cover = _caption_boxes(picture_source, picture_start, final_cues, effective_speed,
                                           width, height, subtitle_options, cancel, progress)
            captions = work/'captions.ass'
            write_captions(captions, placed, cover['text_size'], subtitle_options['subtitle_weight'], box, width, height)
            escaped = str(captions.resolve()).replace('\\', '/').replace(':', '\\:').replace("'", "\\'")
            picture += f",subtitles=filename='{escaped}':original_size={width}x{height}[v]"
        else:
            picture += '[v]'
        if info['has_audio']:
            audio = (f'[0:a:0]asetpts=PTS-STARTPTS,{_atempo(effective_speed)},aresample={RATE},'
                     f'aformat=channel_layouts=stereo,volume={original_gain:.9g},apad,atrim=duration={duration:.9f}[original];'
                     f'[1:a:0]volume={voice_gain:.9g}[voice];'
                     '[original][voice]amix=inputs=2:duration=longest:dropout_transition=0:normalize=0,')
        else:
            audio = f'[1:a:0]volume={voice_gain:.9g},'
        audio += f'alimiter=limit=0.98:level=0:latency=1,atrim=duration={duration:.9f}[a]'
        filters = work/'filters.txt'
        filters.write_text(picture+';\n'+audio, encoding='utf-8')
        command = [imageio_ffmpeg.get_ffmpeg_exe(), '-hide_banner', '-nostdin', '-n',
                   '-threads', '4', '-i', str(source), '-i', str(narration)]
        if cleaned_picture:
            command += ['-i', str(picture_source)]
        if not subtitle_options['burn_subtitles']:
            command += ['-i', str(subtitles)]
        command += ['-filter_complex_threads', '2', '-filter_complex_script', str(filters),
                    '-map', '[v]', '-map', '[a]']
        if not subtitle_options['burn_subtitles']:
            command += ['-map', '3:0' if cleaned_picture else '2:0']
        command += ['-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20', '-threads', '4',
                    '-c:a', 'aac', '-b:a', '192k', '-ar', str(RATE)]
        if not subtitle_options['burn_subtitles']:
            command += ['-c:s', 'mov_text', '-metadata:s:s:0', 'language=vie',
                        '-metadata:s:s:0', 'title=Tiếng Việt', '-disposition:s:0', 'default']
        command += ['-movflags', '+faststart', '-t', f'{duration:.9f}', str(video)]
        _notify(progress, 'Đang dựng video bằng CPU; có thể dùng máy cho việc khác.', 20)
        _run_ffmpeg(command, work, duration, cancel, progress)
        _notify(progress, 'Đang kiểm tra toàn bộ thành phẩm trước khi lưu…', 86)
        quality = _verify(source, video, dict(duration_s=duration, fps=float(fps), width=width, height=height,
                                            source=info, audio_mix=mix),
                          final_cues, probes, subtitle_mode, cancel, progress)
        check_cancel(cancel)
        if cleaned_picture and hash_file(picture_source) != cleaned_picture['sha256']:
            raise ValueError('Bản xóa chữ đã thay đổi khi dựng; không công bố bản xuất.')
        if hash_file(source) != source_hash or any(hash_file(item['source']) != item['sha256'] for item in placements):
            raise ValueError('Nguồn hoặc giọng đã thay đổi trong lúc dựng; không công bố bản xuất.')
        plan = dict(schema=1, source=str(source), source_sha256=source_hash, source_duration_s=info['duration_s'],
                    requested_speed=speed, effective_picture_speed=effective_speed, duration_s=duration,
                    original_gain=original_gain, voice_gain=voice_gain, gap_ms=gap_ms, sample_rate=RATE,
                    source_embedded_text_removed=False, subtitle_clean=cleaned_picture,
                    source_text_removal_scope=('selected_rectangle_only' if cleaned_picture else 'none'),
                    subtitle_mode=subtitle_mode, **({} if cover is None else {'caption_cover': cover}),
                    subtitle_style=(dict(color=subtitle_options['subtitle_color'],
                                         weight=subtitle_options['subtitle_weight'], size=subtitle_options['subtitle_size'],
                                         **({} if box == 'none' else {'box': box}))
                                    if subtitle_options['burn_subtitles'] else None),
                    cues=final_cues, clips=[{k: v for k, v in item.items() if k != 'normalized'} for item in placements])
        quality.update(source_sha256=source_hash, video_sha256=hash_file(video), subtitles_sha256=hash_file(subtitles))
        atomic_json(work/'plan.json', plan)
        quality['render_plan_sha256'] = hash_file(work/'plan.json')
        atomic_json(work/'quality.json', quality)
        # The MP4 appears last, after all verification and companion artifacts.
        for staged, key in ((subtitles, 'subtitles'), (work/'plan.json', 'plan'), (work/'quality.json', 'quality'), (video, 'video')):
            check_cancel(cancel)
            _publish(staged, destinations[key])
    caption_message = 'Sub Việt đã ghi vào hình' if subtitle_options['burn_subtitles'] else 'phụ đề Việt có thể bật/tắt'
    _notify(progress, f'Đã lưu video lồng tiếng ({caption_message}) và một SRT Việt. Cần nghe duyệt nội dung.', 100)
    return {key: str(path) for key, path in destinations.items()}
