"""Local reference-library workflow. Analysis is not model-weight training.

Whisper finds speech; measured timing/audio properties help a human choose a
single speaker's reference. Each saved profile is immutable and independently
usable by the existing reference TTS dispatch, even after source media disappears.
"""
import argparse
from datetime import datetime
import json
import math
import os
from pathlib import Path
import re
import subprocess
import time
import uuid

from teodub.common import Cancelled, FileCancel, atomic_json, check_cancel, hash_file

VERSION = 1
ID = re.compile(r'[0-9a-f]{32}')
STYLE_LABELS = ('Theo mẫu', 'Hoạt ngôn', 'Tự tin', 'Kể chuyện', 'Quảng cáo', 'Hài hước')
MAX_FILES = 10
MAX_SECONDS = 300


def video_links(text):
    """Normalize pasted share links using the existing single-video allowlist."""
    from teodub.learning_download import learning_url
    text = str(text).strip()
    # Handle the Markdown form frequently copied from chat.
    text = re.sub(r'\[[^\]]*\]\((https?://[^\s)]+)\)', r'\1', text)
    urls = re.findall(r'https?://[^\s<>"\u3000]+', text)
    if not urls:
        raise ValueError('Dán link Facebook/YouTube/Douyin/TikTok hoặc đoạn chia sẻ có link.')
    links = list(dict.fromkeys(learning_url(url) for url in urls))
    if len(links) > MAX_FILES:
        raise ValueError('Tối đa 10 nguồn cho một lượt phân tích.')
    return links


def _source(raw, folder, index, cancel, progress):
    """Download into this analysis only; do not touch the main video job."""
    if isinstance(raw, str) and raw.startswith(('https://', 'http://')):
        from teodub.download import download_video
        from teodub.learning_download import learning_url, learning_platform, download_learning_video
        url = learning_url(raw)
        progress(f'Đang tải video mẫu {index+1}: {url}', None)
        try:
            callback = lambda message, percent=None: progress('Tải mẫu: '+message, None)
            if learning_platform(url):
                result = download_learning_video(url, str(folder/f'link-{index+1:02}'), cancel=cancel, progress=callback)
            else:
                result = download_video(url, str(folder/'videos'), str(folder/f'link-{index+1:02}'),
                    cancel=cancel, progress=callback)
        except Cancelled:
            raise
        except Exception as exc:
            raise ValueError('Chưa tải được video mẫu: '+str(exc)) from exc
        check_cancel(cancel)
        return Path(result['source_path']).resolve(strict=True), dict(
            source_url=url, source_title=result.get('title', ''), download_metadata=result['metadata'])
    return Path(raw).resolve(strict=True), {}


def _media_details(source):
    """Container facts only; does not infer subjects from pictures."""
    import av
    with av.open(str(source)) as media:
        video = next(iter(media.streams.video), None)
        duration = float(media.duration or 0)/av.time_base
        result = dict(duration_s=round(duration, 3), has_video=video is not None)
        if video is not None:
            if video.duration is not None:
                result['duration_s'] = round(float(video.duration*video.time_base), 3)
            w, h = video.codec_context.width, video.codec_context.height
            result.update(width=w, height=h, fps=float(video.average_rate or 0),
                orientation='Dọc' if h > w else 'Ngang' if w > h else 'Vuông')
        return result


def analysis_text(report):
    """Readable saved evidence; ASR text is source material, never instructions."""
    lines = ['BÁO CÁO PHÂN TÍCH VIDEO',
        f'Whisper {report["model"]} · {report["device"]} · {report.get("created_at", "")}',
        'Lời thoại do AI nhận dạng, có thể sai. Phong cách là gợi ý từ nhịp nói; nghe mẫu để kiểm tra.']
    for item in report.get('videos', []):
        m, media = item['metrics'], item['media']
        lines.extend(['', item.get('source_title') or item['source_name']])
        if item.get('source_url'):
            lines.append('Link nguồn: '+item['source_url'])
        lines.append(f'Đoạn phân tích: {item["start_s"]:.2f}–{item["end_s"]:.2f} giây · '
                     f'Ngôn ngữ: {item["language"]}')
        if media['has_video']:
            lines.append(f'Hình: {media["width"]}×{media["height"]} · {media["orientation"]} · '
                         f'{media["fps"]:.2f} fps · Video dài {media["duration_s"]} giây')
        lines.append(f'Nhịp đọc: {m["estimated_syllables_per_s"] or "chưa đo được"} âm tiết/giây · '
                     f'Khoảng nghỉ: {m["pause_median_s"] or "chưa đo được"} giây')
        lines.append('Gợi ý phong cách: '+item['style_hint']+'. '+item['style_reason'])
        lines.extend(m['warnings'])
        lines.append('LỜI THOẠI TRONG ĐOẠN ĐÃ PHÂN TÍCH:')
        for segment in item['segments']:
            lines.append(f'[{segment["start_s"]:.2f}–{segment["end_s"]:.2f}] {segment["text"]}')
    for error in report.get('errors', []):
        lines.append('\nNguồn chưa xử lý: '+error['source']+' · '+error['message'])
    if not report.get('videos'):
        lines.append('Lượt cũ chỉ lưu các đoạn giọng mẫu; phân tích lại để có báo cáo đầy đủ.')
    lines.append('\n'+report.get('limitations', ''))
    return '\n'.join(lines)


def library_root(data_dir):
    return Path(data_dir).resolve() / 'voice-learning'


def _read(path):
    value = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise ValueError('Hồ sơ giọng không hợp lệ.')
    return value


def _folder(data_dir, family, ident):
    if family not in ('analyses', 'profiles', 'previews', 'content') or not isinstance(ident, str) or not ID.fullmatch(ident):
        raise ValueError('Mã hồ sơ giọng không hợp lệ.')
    root = library_root(data_dir)
    folder = root / family / ident
    if not folder.resolve().is_relative_to(root):
        raise ValueError('Hồ sơ nằm ngoài thư viện giọng.')
    return folder


def audio_metrics(samples, rate=48000, *, text='', language='vi', cancel=None):
    """Describe observable audio, never infer personality/gender or certify purity."""
    import numpy as np
    x = np.asarray(samples, dtype='float32')
    if x.ndim != 1 or len(x) < 3 * rate or not np.isfinite(x).all():
        raise ValueError('Mẫu phải có ít nhất 3 giây âm thanh hợp lệ.')
    rms = float(np.sqrt(np.mean(x.astype('float64') ** 2)))
    if rms < .0005:
        raise ValueError('Đoạn mẫu quá nhỏ hoặc im lặng; chọn đoạn nói rõ hơn.')
    duration = len(x) / rate
    frames = x[:len(x)//960*960].reshape(-1, 960)
    levels = np.sqrt(np.mean(frames.astype('float64') ** 2, axis=1))
    threshold = max(.002, float(np.max(levels)) * .12)
    active = levels > threshold
    active_s = float(np.sum(active)) * .02
    pauses = []
    pause = 0
    for talking in active:
        if talking:
            if pause >= 8:
                pauses.append(round(pause * .02, 2))
            pause = 0
        else:
            pause += 1
    # Approximate voiced pitch. Music can bias this; the UI explicitly says so.
    y = x[::3]
    pitches = []
    for start in range(0, len(y)-640, 320):
        check_cancel(cancel)
        frame = y[start:start+640].astype('float64')
        frame -= np.mean(frame)
        if np.sqrt(np.mean(frame ** 2)) < threshold:
            continue
        fft = np.fft.rfft(frame * np.hanning(640), 2048)
        corr = np.fft.irfft(fft * np.conjugate(fft), 2048)[:640]
        lag = 40 + int(np.argmax(corr[40:267]))
        if corr[0] > 0 and corr[lag] / corr[0] >= .55:
            pitches.append(16000 / lag)
    clip_fraction = float(np.mean(np.abs(x) >= .995))
    syllables = len(text.split()) if language == 'vi' else None
    rate_s = round(syllables / max(active_s, .1), 2) if syllables else None
    warnings = []
    if active_s / duration < .5:
        warnings.append('Nhiều khoảng nhỏ tiếng; nghe kiểm tra lời nói và nhạc nền.')
    if clip_fraction > .003:
        warnings.append('Có dấu hiệu âm thanh vỡ/đạt trần.')
    if language != 'vi':
        warnings.append('Mẫu không phải tiếng Việt; chất giọng khi đọc tiếng Việt cần nghe thử.')
    return dict(duration_s=round(duration, 3), rms=round(rms, 5),
                activity_ratio=round(active_s/duration, 3), clipped_fraction=round(clip_fraction, 5),
                median_pitch_hz=round(float(np.median(pitches)), 1) if pitches else None,
                pitch_span_hz=round(float(np.percentile(pitches, 90)-np.percentile(pitches, 10)), 1) if pitches else None,
                pause_median_s=round(float(np.median(pauses)), 2) if pauses else None,
                estimated_syllables_per_s=rate_s, warnings=warnings)


def speech_windows(segments, duration):
    """Bounded, distinct 3–8 s candidates from confident ASR speech intervals."""
    spans = []
    for segment in segments:
        if (segment.get('no_speech_prob', 0) > .6 or segment.get('avg_logprob', 0) < -1.2
                or not segment.get('text', '').strip()):
            continue
        start, end = max(0., float(segment['start'])), min(duration, float(segment['end']))
        if not math.isfinite(start+end) or end <= start:
            continue
        if spans and start-spans[-1][1] <= .5:
            spans[-1][1] = max(end, spans[-1][1])
        else:
            spans.append([start, end])
    windows = []
    for start, end in spans:
        cursor = start
        while end-cursor >= 3:
            stop = min(end, cursor+7.5)
            matching = [s for s in segments if s['end'] > cursor and s['start'] < stop]
            words = [w.get('word', '').strip() for s in matching for w in s.get('words', [])
                     if w.get('start') is not None and w.get('end') is not None
                     and w['end'] > cursor and w['start'] < stop]
            text = ' '.join(words) if words else ' '.join(s['text'].strip() for s in matching)
            windows.append(dict(start_s=round(cursor, 3), end_s=round(stop, 3), text=text[:1200]))
            cursor = stop
    return windows


def style_hint(metrics):
    """Timing suggestion only, not an emotion/personality classifier."""
    speed = metrics.get('estimated_syllables_per_s')
    if speed is None:
        return 'Theo mẫu', 'Chưa ước tính nhịp tiếng Việt; nghe mẫu để chọn phong cách.'
    if speed >= 5.5:
        return 'Hoạt ngôn', 'Nhịp nói nhanh; gợi ý dựa trên tốc độ đo được, không xác nhận cảm xúc.'
    if speed <= 3.5:
        return 'Kể chuyện', 'Nhịp nói chậm; gợi ý dựa trên tốc độ đo được, không xác nhận cảm xúc.'
    return 'Theo mẫu', 'Nhịp nói vừa; nghe độ nhấn và biểu cảm để chọn tên phong cách.'


def _extract(source, target, start_s, limit_s, cancel):
    import av
    import imageio_ffmpeg
    with av.open(str(source)) as media:
        if len(media.streams.audio) != 1:
            raise ValueError('Video mẫu cần đúng một luồng âm thanh. Chọn tệp có giọng rõ.')
    command = [imageio_ffmpeg.get_ffmpeg_exe(), '-nostdin', '-hide_banner', '-loglevel', 'error',
               '-ss', str(start_s), '-i', str(source), '-t', str(limit_s), '-map', '0:a:0',
               '-vn', '-ac', '1', '-ar', '48000', '-c:a', 'pcm_s16le', '-n', str(target)]
    log = target.with_suffix('.log')
    with log.open('wb') as error:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=error, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        deadline = time.monotonic() + max(120, limit_s * 2)
        try:
            while process.poll() is None:
                check_cancel(cancel)
                if time.monotonic() > deadline:
                    raise ValueError('Tách âm thanh quá thời gian; chưa phân tích mẫu.')
                time.sleep(.1)
            check_cancel(cancel)
            if process.returncode:
                raise ValueError('Không đọc được âm thanh: '+log.read_text(encoding='utf-8', errors='replace')[-1400:])
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)


def _recognizer(requested_model, progress):
    from teodub.models import configure_models
    cache = configure_models()
    import ctranslate2
    from faster_whisper import WhisperModel
    from teodub.asr_runtime import recognizer_runtime
    runtime = recognizer_runtime(requested_model, backend=ctranslate2)
    device = runtime['device']
    progress('Đang nạp Whisper '+requested_model+' trên '+('GPU' if device == 'cuda' else 'CPU')+
             ' ('+runtime['compute_type']+'). '+runtime['note']+
             ' Lần đầu có thể tải mô hình miễn phí.', 8)
    # A CUDA failure is reported, not silently retried on CPU or a bigger model.
    model = WhisperModel(requested_model, device=device,
        compute_type=runtime['compute_type'], cpu_threads=4,
        num_workers=1, download_root=str(cache/'whisper'))
    return model, device


def analyze_sources(config, *, cancel=None, progress=lambda *_: None):
    check_cancel(cancel)
    import numpy as np
    import soundfile as sf
    from teodub.ai_models import ASR_MODELS
    files = config.get('sources')
    model_name = config.get('asr_model', 'small')
    if model_name not in ASR_MODELS or model_name == 'auto':
        raise ValueError('Chọn Whisper Small, Medium hoặc Large v3 để phân tích giọng.')
    if not isinstance(files, list) or not 1 <= len(files) <= MAX_FILES:
        raise ValueError('Chọn từ 1 đến 10 video hoặc tệp âm thanh mẫu.')
    start_s, limit_s = float(config.get('start_s', 0)), float(config.get('limit_s', 120))
    if not math.isfinite(start_s+limit_s) or start_s < 0 or not 3 <= limit_s <= MAX_SECONDS:
        raise ValueError('Mốc bắt đầu phải từ 0; phân tích 3–300 giây mỗi video.')
    folder = _folder(config['data_dir'], 'analyses', config['id'])
    if (folder/'analysis.json').exists():
        raise ValueError('Lượt phân tích đã hoàn tất; không ghi đè mẫu cũ.')
    folder.mkdir(parents=True, exist_ok=True)
    model, device = None, 'chưa nạp'
    results, errors, videos = [], [], []
    for index, raw in enumerate(files):
        check_cancel(cancel)
        try:
            source, origin = _source(raw, folder, index, cancel, progress)
            if not source.is_file():
                raise ValueError('Nguồn mẫu không phải tệp.')
            digest = hash_file(source)
            part = folder / f'source-{index+1:02}'
            part.mkdir(exist_ok=False)
            audio = part/'audio.wav'
            progress(f'Tách âm thanh {index+1}/{len(files)}: {source.name}', 10+index*80/len(files))
            _extract(source, audio, start_s, limit_s, cancel)
            samples, rate = sf.read(audio, dtype='float32')
            if rate != 48000 or samples.ndim != 1 or len(samples) < rate*3 or not np.isfinite(samples).all():
                raise ValueError('Mốc chọn chưa có đủ 3 giây âm thanh.')
            if model is None:
                model, device = _recognizer(model_name, progress)
            stream, info = model.transcribe(str(audio), task='transcribe', beam_size=3,
                vad_filter=True, word_timestamps=True, condition_on_previous_text=False,
                vad_parameters={'min_silence_duration_ms': 300})
            segments = []
            for segment in stream:
                check_cancel(cancel)
                segments.append(dict(start=segment.start, end=segment.end, text=segment.text,
                    no_speech_prob=segment.no_speech_prob, avg_logprob=segment.avg_logprob,
                    words=[dict(start=w.start, end=w.end, word=w.word) for w in (segment.words or [])]))
                progress(f'Nhận dạng mẫu {index+1}/{len(files)}: {segment.end:.0f}/{len(samples)/rate:.0f} giây',
                         10+(index+min(1, segment.end/(len(samples)/rate)))*80/len(files))
            if hash_file(source) != digest:
                raise ValueError('Video nguồn thay đổi trong lúc phân tích; chưa lưu mẫu.')
            transcript = ' '.join(s['text'].strip() for s in segments)
            metrics = audio_metrics(samples, rate, text=transcript, language=info.language, cancel=cancel)
            hint, reason = style_hint(metrics)
            videos.append(dict(source_path=str(source), source_sha256=digest, source_name=origin.get('source_title') or source.name,
                **origin, media=_media_details(source), language=info.language,
                start_s=start_s, end_s=round(start_s+len(samples)/rate, 3),
                metrics=metrics, style_hint=hint, style_reason=reason,
                segments=[dict(start_s=round(start_s+s['start'], 3),
                    end_s=round(start_s+s['end'], 3), text=s['text'].strip()) for s in segments]))
            candidates = []
            for window in speech_windows(segments, len(samples)/rate):
                clip = samples[round(window['start_s']*rate):round(window['end_s']*rate)]
                try:
                    metrics = audio_metrics(clip, rate, text=window['text'], language=info.language, cancel=cancel)
                except ValueError:
                    continue
                rank = metrics['activity_ratio'] - metrics['clipped_fraction']*20
                candidates.append((rank, window, clip, metrics))
            chosen = sorted(candidates, key=lambda c: c[0], reverse=True)[:8]
            chosen.sort(key=lambda c: c[1]['start_s'])
            for number, (_, window, clip, metrics) in enumerate(chosen, 1):
                ident = uuid.uuid4().hex
                clip_path = part/f'mau-{number:02}.wav'
                sf.write(clip_path, clip, rate, subtype='PCM_16')
                hint, reason = style_hint(metrics)
                results.append(dict(id=ident, path=str(clip_path), sha256=hash_file(clip_path),
                    source_path=str(source), source_sha256=digest, source_name=origin.get('source_title') or source.name, **origin,
                    start_s=round(start_s+window['start_s'], 3), end_s=round(start_s+window['end_s'], 3),
                    text=window['text'], language=info.language, metrics=metrics,
                    style_hint=hint, style_reason=reason))
            if not chosen:
                raise ValueError('Chưa tìm được đoạn lời nói rõ dài 3–8 giây. Chọn mốc khác hoặc video ít nhạc hơn.')
        except (OSError, ValueError) as exc:
            errors.append(dict(source=str(raw), message=str(exc)))
    check_cancel(cancel)
    report = dict(version=VERSION, id=config['id'], model=model_name, device=device,
        created_at=datetime.now().astimezone().isoformat(timespec='seconds'),
        start_s=start_s, limit_s=limit_s, candidates=results, errors=errors, videos=videos,
        method='whisper-vad+audio-metrics-v1', weights_trained=False,
        limitations='Không tự tách nhạc hoặc người nói; cần nghe duyệt từng mẫu. '
            'Chưa phân tích nội dung hình ảnh hay dựng cảnh; chưa huấn luyện lại trọng số mô hình.')
    atomic_json(folder/'analysis.json', report)
    (folder/'bao-cao.txt').write_text(analysis_text(report), encoding='utf-8')
    if not results:
        raise ValueError('; '.join(e['message'] for e in errors) or 'Chưa tìm được mẫu giọng.')
    progress(f'Đã tìm {len(results)} mẫu; nghe kiểm tra rồi lưu hồ sơ.', 100)
    return report


def checked_candidate(data_dir, analysis_id, candidate_id):
    folder = _folder(data_dir, 'analyses', analysis_id)
    report = _read(folder/'analysis.json')
    if report.get('id') != analysis_id or report.get('version') != VERSION:
        raise ValueError('Lượt phân tích không khớp.')
    matches = [c for c in report['candidates'] if c['id'] == candidate_id]
    if len(matches) != 1:
        raise ValueError('Chọn đúng một đoạn mẫu đã phân tích.')
    candidate = matches[0]
    path = Path(candidate['path'])
    if not path.is_absolute() or not path.resolve().is_relative_to(folder) or path.is_symlink():
        raise ValueError('Đường dẫn đoạn mẫu không khớp thư viện.')
    if hash_file(path) != candidate['sha256']:
        raise ValueError('Đoạn mẫu đã thay đổi; phân tích lại trước khi dùng.')
    return dict(candidate)


def profile_label(profile):
    return f'Học giọng · {profile["name"]} · {profile["style"]} [{profile["id"][:8]}]'


def list_profiles(data_dir):
    root = library_root(data_dir)/'profiles'
    profiles, errors = [], []
    for file in sorted(root.glob('*/profile.json')) if root.exists() else []:
        try:
            item = _read(file)
            if item.get('version') != VERSION or _folder(data_dir, 'profiles', item['id']) != file.parent:
                raise ValueError('Mã hồ sơ không khớp thư mục.')
            if not isinstance(item.get('name'), str) or not isinstance(item.get('style'), str):
                raise ValueError('Thiếu tên hồ sơ.')
            if (not isinstance(item.get('created_at'), str) or not isinstance(item.get('candidate'), dict)
                    or not isinstance(item['candidate'].get('source_name'), str)
                    or item.get('reference_audio') != str(file.parent/'reference.wav')
                    or not isinstance(item.get('reference_sha256'), str)
                    or not re.fullmatch('[0-9a-f]{64}', item['reference_sha256'])):
                raise ValueError('Hồ sơ thiếu thông tin mẫu giọng.')
            profiles.append(item)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            errors.append(f'{file.parent.name}: {exc}')
    return profiles, errors


def checked_profile(data_dir, profile_id):
    from teodub.voice_profiles import validate_reference
    folder = _folder(data_dir, 'profiles', profile_id)
    record = _read(folder/'profile.json')
    if (record.get('version') != VERSION or record.get('id') != profile_id
            or record.get('reference_audio') != str(folder/'reference.wav')):
        raise ValueError('Hồ sơ giọng không khớp.')
    metadata = _read(folder/'reference.json')
    if Path(metadata['path']) != folder/'reference.wav' or record.get('reference_sha256') != metadata.get('sha256'):
        raise ValueError('Mẫu giọng không thuộc đúng hồ sơ đã lưu.')
    validate_reference(metadata)
    return record


def save_profile(data_dir, analysis_id, candidate_id, name, style='Theo mẫu'):
    from teodub.voice_profiles import prepare_reference
    name, style = str(name).strip(), str(style).strip()
    if not 1 <= len(name) <= 70 or not 1 <= len(style) <= 50 or any(ord(c) < 32 for c in name+style):
        raise ValueError('Nhập tên giọng (1–70 ký tự) và phong cách (1–50 ký tự).')
    candidate = checked_candidate(data_dir, analysis_id, candidate_id)
    ident = uuid.uuid4().hex
    folder = _folder(data_dir, 'profiles', ident)
    folder.mkdir(parents=True, exist_ok=False)
    reference = prepare_reference(candidate['path'], folder)
    if reference['source_sha256'] != candidate['sha256']:
        raise ValueError('Đoạn mẫu thay đổi trong lúc lưu; chưa công bố hồ sơ.')
    record = dict(version=VERSION, id=ident, name=name, style=style,
        created_at=datetime.now().astimezone().isoformat(timespec='seconds'),
        reference_audio=reference['path'], reference_sha256=reference['sha256'],
        analysis_id=analysis_id, candidate=candidate, reading_style='legacy', voice_speed=1.0,
        weights_trained=False)
    atomic_json(folder/'profile.json', record)
    return checked_profile(data_dir, ident)


def profile_selection(data_dir, profile_id, tts_model='v3turbo'):
    from teodub.tts import validate_selection, REFERENCE_VOICE
    profile = checked_profile(data_dir, profile_id)
    validate_selection(REFERENCE_VOICE, reference_audio=profile['reference_audio'],
        reference_sha256=profile['reference_sha256'], reading_style='legacy', tts_model=tts_model)
    return dict(voice_provider='local', voice=REFERENCE_VOICE, reference_audio=profile['reference_audio'],
        reference_sha256=profile['reference_sha256'], reading_style='legacy', voice_speed=1.0, tts_model=tts_model)


def run_preview(config, *, cancel=None, progress=lambda *_: None):
    from teodub import tts
    text = str(config.get('text', '')).strip()
    if not 10 <= len(text) <= 300:
        raise ValueError('Lời nghe thử cần 10–300 ký tự.')
    if config.get('profile_id'):
        profile = checked_profile(config['data_dir'], config['profile_id'])
        candidate = dict(path=profile['reference_audio'], sha256=profile['reference_sha256'])
    else:
        candidate = checked_candidate(config['data_dir'], config['analysis_id'], config['candidate_id'])
    folder = _folder(config['data_dir'], 'previews', config['id'])
    options = dict(provider='local', reference_audio=candidate['path'], reference_sha256=candidate['sha256'],
        reading_style='legacy', voice_speed=1.0, tts_model=config.get('tts_model', 'v3turbo'))
    clips = tts.synthesize([dict(start_ms=0, end_ms=15000, text=text)], tts.REFERENCE_VOICE,
        folder, cancel=cancel, progress=lambda message, percent=None: progress(message, percent), **options)
    return dict(path=clips[0]['path'], sha256=clips[0]['sha256'], text=text,
        tts_model=options['tts_model'], candidate_sha256=candidate['sha256'])


def worker_main(args=None):
    from teodub.common import protect_worker
    from teodub.pipeline import _job_lock
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    options = parser.parse_args(args)
    config = _read(options.config)
    operation = config.get('operation')
    family = 'content' if operation == 'content' else 'analyses' if operation == 'analyze' else 'previews'
    folder = _folder(config['data_dir'], family, config['id'])
    if Path(options.config).resolve() != folder/'config.json':
        raise ValueError('Cấu hình không thuộc lượt xử lý giọng.')
    cancel = FileCancel(folder/'stop.request')
    if (folder/'result.json').exists():
        # A completed run is never replayed or replaced by a second launch.
        return 0 if _read(folder/'result.json').get('status') == 'completed' else 2
    acquired = False
    def progress(message, percent=None):
        atomic_json(folder/'state.json', dict(message=message, percent=percent, updated_at=time.time()))
    try:
        protect_worker()
        execution = Path(config['data_dir']).resolve()/'execution'
        execution.mkdir(parents=True, exist_ok=True)
        with _job_lock(folder), _job_lock(execution):
            acquired = True
            check_cancel(cancel)
            if operation == 'analyze':
                result = analyze_sources(config, cancel=cancel, progress=progress)
            elif operation == 'preview':
                result = run_preview(config, cancel=cancel, progress=progress)
            elif operation == 'content':
                from teodub.video_content import analyze_content
                result = analyze_content(config, cancel=cancel, progress=progress)
            else:
                raise ValueError('Thao tác học giọng chưa được hỗ trợ.')
            check_cancel(cancel)
            atomic_json(folder/'result.json', dict(status='completed', result=result))
        return 0
    except Exception as exc:
        if (folder/'result.json').exists():
            return 2
        if acquired or not (folder/'worker.lock').exists():
            atomic_json(folder/'result.json', dict(status='cancelled' if isinstance(exc, Cancelled) else 'failed',
                message=str(exc)))
        else:
            # A different run may hold the global execution lock. Report the
            # cause in this run, but don't overwrite a worker owning this folder.
            try:
                with _job_lock(folder):
                    atomic_json(folder/'result.json', dict(status='failed', message=str(exc)))
            except ValueError:
                pass
        return 2


if __name__ == '__main__':
    raise SystemExit(worker_main())
