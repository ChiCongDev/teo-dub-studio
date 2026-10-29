"""Free local Chinese transcription, independent of video-editor state."""
import gc
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time

from teodub.common import Cancelled, atomic_json, check_cancel, hash_file
from teodub.models import configure_models, saved_first
from teodub.srt import write_srt
from teodub.subtitles import normalized_text, read_srt


# 28/10/2026: on the 2 GB GPU "auto" is Whisper small, which skipped real speech (a video's opening line, "Là cá
# mập!", short cries). In "auto" mode only the stretches it left empty are listened to again with a better model.
GAP_MIN_S = 4.0  # An empty stretch at least this long is listened to again.
GAP_MODEL = 'medium'
GAP_MAX_TOTAL_S = 600.0  # At most this much audio per video is re-listened to (time stays bounded).
GAP_MAX_NO_SPEECH = .6  # A recovered line the model itself doubts is speech is dropped...
GAP_MIN_LOGPROB = -1.1  # ...or one it is unsure of (short real cries score about -1.05)...
GAP_MAX_COMPRESSION = 2.4  # ...or one that is mostly repetition (a noise the model "reads" as words).
GAP_REPEATS = 3  # The same recovered text this many times in one video is a hallucination: all of it is dropped.


def empty_stretches(cues, duration_s, minimum=GAP_MIN_S):
    """(start_ms, end_ms) of every stretch with no recognized line, at least `minimum` seconds long."""
    end_ms, stretches, previous = round(duration_s*1000), [], 0
    for cue in sorted(cues, key=lambda cue: cue['start_ms']):
        if cue['start_ms']-previous >= minimum*1000:
            stretches.append((previous, cue['start_ms']))
        previous = max(previous, cue['end_ms'])
    if end_ms-previous >= minimum*1000:
        stretches.append((previous, end_ms))
    return stretches


CHINESE = re.compile('[一-鿿]')  # A Chinese video's line without one Chinese character ("YES SIR") is noise,
ENGLISH_WORD = re.compile('[A-Za-z]{3,}')  # and so is one with an English word in it ("二撒 tones").


def _repeating(text):
    plain = re.sub(r'[\W_]+', '', text)
    return bool(re.fullmatch(r'(.{1,4}?)\1{2,}', plain)) if len(plain) >= 3 else False


def _keep_recovered(found):
    """Drop what is probably not speech. `found` = dicts with text, start_ms, end_ms and the model's own scores."""
    kept = [item for item in found if item['no_speech'] <= GAP_MAX_NO_SPEECH and item['logprob'] >= GAP_MIN_LOGPROB
            and item['compression'] <= GAP_MAX_COMPRESSION and CHINESE.search(item['text'])
            and not ENGLISH_WORD.search(item['text']) and not _repeating(item['text'])]
    kept.sort(key=lambda item: item['start_ms'])
    # The same line twice in a row is the model looping on noise ("一二三 一二三"), not two people saying it.
    kept = [item for index, item in enumerate(kept)
            if not any(0 <= other < len(kept) and kept[other]['text'] == item['text'] for other in (index-1, index+1))]
    counts = {}
    for item in kept:
        counts[item['text']] = counts.get(item['text'], 0)+1
    return [item for item in kept if counts[item['text']] < GAP_REPEATS]


def _recheck_gaps(source, cues, duration_s, *, cancel, progress, cache):
    """Return (cues with the lines found in the empty stretches added, report). The caller keeps the first
    recognition if this raises anything but Cancelled."""
    import ctranslate2
    from faster_whisper import WhisperModel
    import imageio_ffmpeg
    from teodub.asr_runtime import recognizer_runtime
    stretches = empty_stretches(cues, duration_s)
    report = dict(model=GAP_MODEL, stretches=len(stretches), listened=0, audio_s=0., added=0, dropped=0)
    started = time.monotonic()
    if not stretches:
        return cues, dict(report, status='no_empty_stretch')
    runtime = recognizer_runtime(GAP_MODEL, backend=ctranslate2)
    if progress:
        progress(f'Nghe lại {len(stretches)} khoảng chưa có lời bằng Whisper {runtime["model"]}'
                 f'{" (GPU)" if runtime["device"] == "cuda" else " (CPU)"}…', percent=None)
    model = saved_first(lambda: WhisperModel(runtime['model'], device=runtime['device'], compute_type=runtime['compute_type'],
                                             cpu_threads=4, num_workers=1, download_root=str(cache / 'whisper')))
    ffmpeg, found, budget = imageio_ffmpeg.get_ffmpeg_exe(), [], GAP_MAX_TOTAL_S
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0) if os.name == 'nt' else 0
    with tempfile.TemporaryDirectory(prefix='teo-gap-') as temporary:
        for number, (start_ms, end_ms) in enumerate(stretches, 1):
            check_cancel(cancel)
            length = (end_ms-start_ms)/1000
            if length > budget:
                break
            budget -= length
            wav = Path(temporary)/f'{number}.wav'
            subprocess.run([ffmpeg, '-v', 'error', '-nostdin', '-y', '-ss', f'{start_ms/1000:.3f}', '-t', f'{length:.3f}',
                            '-i', str(source), '-vn', '-ac', '1', '-ar', '16000', str(wav)], check=True,
                           capture_output=True, creationflags=flags)
            # No voice-activity filter here: on these stretches it removed real speech (the opening line of a video);
            # the scores in _keep_recovered guard against noise instead.
            segments, _ = model.transcribe(str(wav), language='zh', task='transcribe', beam_size=5, vad_filter=False,
                                           condition_on_previous_text=False)
            report['listened'] += 1
            report['audio_s'] = round(report['audio_s']+length, 1)
            for segment in segments:
                check_cancel(cancel)
                text = normalized_text(segment.text)
                start = max(start_ms, start_ms+round(segment.start*1000))
                end = min(end_ms, start_ms+round(segment.end*1000))
                if text and end > start:
                    found.append(dict(text=text, start_ms=start, end_ms=end,
                                      no_speech=getattr(segment, 'no_speech_prob', 0.),
                                      logprob=getattr(segment, 'avg_logprob', 0.),
                                      compression=getattr(segment, 'compression_ratio', 1.)))
            if progress:
                progress(f'Đã nghe lại {number}/{len(stretches)} khoảng chưa có lời.', percent=None)
    del model
    gc.collect()
    kept = _keep_recovered(found)
    report.update(added=len(kept), dropped=len(found)-len(kept), status='done', seconds=round(time.monotonic()-started),
                  added_lines=[dict(start_ms=item['start_ms'], end_ms=item['end_ms'], text=item['text']) for item in kept][:60])
    merged = sorted(cues+[dict(start_ms=item['start_ms'], end_ms=item['end_ms'], text=item['text']) for item in kept],
                    key=lambda cue: cue['start_ms'])
    return merged, report


WORD_SPLIT_GAP_S = 2.0  # A silence this long between two spoken words ends the line (owner 29/10/2026).


def _spoken_pieces(segment):
    """(start_s, end_s, text) of what one recognized segment says, from its first spoken word to its last, split where
    two neighbouring words are more than WORD_SPLIT_GAP_S apart. Whisper may glue a cry to a sentence 12 s later; the
    line then began at the cry and the sentence was read and shown far too early. Without word times: the segment."""
    words = [word for word in (getattr(segment, 'words', None) or [])
             if getattr(word, 'start', None) is not None and getattr(word, 'end', None) is not None and word.end >= word.start]
    if not words:
        return [(segment.start, segment.end, segment.text)]
    groups = [[words[0]]]
    for word in words[1:]:
        if word.start-groups[-1][-1].end > WORD_SPLIT_GAP_S:
            groups.append([word])
        else:
            groups[-1].append(word)
    return [(group[0].start, group[-1].end, ''.join(word.word for word in group)) for group in groups]


def transcribe(source_path, output_path, *, cancel=None, progress=None, asr_model='auto'):
    from teodub.ai_models import validate_models
    validate_models(dict(asr_model=asr_model))
    check_cancel(cancel)
    source, output = Path(source_path).resolve(), Path(output_path).resolve()
    source_hash = hash_file(source)
    metadata_path = output.with_suffix('.asr.json')
    pending = output.with_suffix('.asr.pending.json')
    if output.exists() and not metadata_path.exists() and pending.exists():
        recovery = json.loads(pending.read_text(encoding='utf-8'))
        if recovery.get('source_sha256') == source_hash and recovery.get('sha256') == hash_file(output):
            atomic_json(metadata_path, recovery)
    if output.exists():
        data = json.loads(metadata_path.read_text(encoding='utf-8'))
        if data.get('requested_model', 'auto') != asr_model:
            raise ValueError('Mô hình nhận dạng đã đổi; bắt đầu phiên mới để giữ đúng lời nguồn.')
        if data['source_sha256'] != source_hash or data['sha256'] != hash_file(output):
            raise ValueError('Phụ đề nhận dạng đã đổi hoặc không còn khớp video gốc.')
        cues = read_srt(output)
        if data.get('path') != str(output) or data.get('cue_count') != len(cues) or data.get('language') != 'zh':
            raise ValueError('Thông tin nhận dạng không khớp phụ đề đã lưu.')
        check_cancel(cancel)
        return data
    check_cancel(cancel)
    cache = configure_models()
    import ctranslate2
    from faster_whisper import WhisperModel
    from teodub.asr_runtime import recognizer_runtime
    model_name = None
    request_path = output.with_suffix('.asr.request.json')
    if request_path.exists():
        request = json.loads(request_path.read_text(encoding='utf-8'))
        if request.get('source_sha256') != source_hash or request.get('requested_model') != asr_model:
            raise ValueError('Phiên nhận dạng thuộc nguồn hoặc mô hình khác; chưa chạy lại.')
        model_name = request['model']
        if model_name not in ('small', 'medium', 'large-v3'):
            raise ValueError('Mô hình nhận dạng đã lưu không được hỗ trợ.')
        if asr_model != 'auto' and model_name != asr_model:
            raise ValueError('Mô hình nhận dạng đã lưu không khớp lựa chọn.')
    runtime = recognizer_runtime(asr_model, resolved_model=model_name, backend=ctranslate2)
    model_name, device = runtime['model'], runtime['device']
    if not request_path.exists():
        output.parent.mkdir(parents=True, exist_ok=True)
        atomic_json(request_path, dict(source_sha256=source_hash, requested_model=asr_model, model=model_name))
    if progress:
        progress('Đang nạp Whisper '+model_name + (' bằng GPU' if device == 'cuda' else ' bằng CPU') +
                 ' ('+runtime['compute_type']+'). '+runtime['note']+
                 ' Lần đầu cần tải mô hình miễn phí; các lần sau dùng bản đã lưu.', percent=None)
    model = saved_first(lambda: WhisperModel(model_name, device=device, compute_type=runtime['compute_type'],
                                             cpu_threads=4, num_workers=1, download_root=str(cache / 'whisper')))
    check_cancel(cancel)
    segments, info = model.transcribe(str(source), language='zh', task='transcribe', beam_size=5,
                                     vad_filter=True, vad_parameters={'min_silence_duration_ms': 500},
                                     condition_on_previous_text=False, word_timestamps=True)
    cues = []
    for segment in segments:
        check_cancel(cancel)
        pieces = _spoken_pieces(segment)
        for begin, finish, spoken in pieces:
            text = normalized_text(spoken)
            if not text:
                continue
            start = max(round(begin * 1000), cues[-1]['end_ms'] if cues else 0)
            end = round(finish * 1000)
            if end <= start:
                if len(pieces) == 1:
                    raise ValueError('Nhận dạng trả về câu có thời gian không hợp lệ. Đã giữ video để kiểm tra.')
                end = start+200  # One word of a split sentence: keep what was said, never drop it.
            cues.append(dict(start_ms=start, end_ms=end, text=text))
        if progress:
            progress(f'Đã nhận dạng {segment.end:.0f}/{info.duration:.0f} giây, {len(cues)} câu.',
                     percent=min(100, round(segment.end / max(info.duration, 1) * 100)))
    if not cues:
        raise ValueError('Chưa nhận được lời nói trong video. Có thể cung cấp SRT Việt đã chuẩn bị.')
    del model
    gc.collect()
    recheck = None
    if asr_model == 'auto' and model_name == 'small':  # A model the owner chose, or an already bigger one, is respected.
        try:
            cues, recheck = _recheck_gaps(source, cues, info.duration, cancel=cancel, progress=progress, cache=cache)
        except Cancelled:
            raise
        except Exception as exc:  # The first recognition stands: a failed second listen must never stop a video.
            recheck = dict(model=GAP_MODEL, status='failed',
                           message=str(exc).splitlines()[0][:300] if str(exc) else type(exc).__name__)
    check_cancel(cancel)
    if hash_file(source) != source_hash:
        raise ValueError('Video nguồn thay đổi trong khi nhận dạng; chưa gửi dịch.')
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix('.writing.srt')
    # Only this job's incomplete temporary file is disposable on explicit resume.
    temporary.unlink(missing_ok=True)
    write_srt(temporary, cues)
    read_srt(temporary)
    data = dict(path=str(output), sha256=hash_file(temporary), source_sha256=source_hash,
                model=model_name, requested_model=asr_model, device=device,
                compute_type=runtime['compute_type'], language='zh', cue_count=len(cues),
                duration_s=info.duration, human_review_required=True)
    if recheck is not None:
        data['gap_recheck'] = recheck
    atomic_json(pending, data)
    temporary.rename(output)
    atomic_json(metadata_path, data)
    pending.unlink(missing_ok=True)
    return data
