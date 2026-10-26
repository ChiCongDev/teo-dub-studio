"""Free local Chinese transcription, independent of video-editor state."""
import json
from pathlib import Path

from teodub.common import atomic_json, check_cancel, hash_file
from teodub.models import configure_models, saved_first
from teodub.srt import write_srt
from teodub.subtitles import normalized_text, read_srt


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
                                     condition_on_previous_text=False)
    cues = []
    for segment in segments:
        check_cancel(cancel)
        text = normalized_text(segment.text)
        if not text:
            continue
        start = max(round(segment.start * 1000), cues[-1]['end_ms'] if cues else 0)
        end = round(segment.end * 1000)
        if end <= start:
            raise ValueError('Nhận dạng trả về câu có thời gian không hợp lệ. Đã giữ video để kiểm tra.')
        cues.append(dict(start_ms=start, end_ms=end, text=text))
        if progress:
            progress(f'Đã nhận dạng {segment.end:.0f}/{info.duration:.0f} giây, {len(cues)} câu.',
                     percent=min(100, round(segment.end / max(info.duration, 1) * 100)))
    if not cues:
        raise ValueError('Chưa nhận được lời nói trong video. Có thể cung cấp SRT Việt đã chuẩn bị.')
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
    atomic_json(pending, data)
    temporary.rename(output)
    atomic_json(metadata_path, data)
    pending.unlink(missing_ok=True)
    del model
    return data
