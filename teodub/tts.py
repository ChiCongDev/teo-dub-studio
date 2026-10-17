"""Selectable speech engines; one independently verified WAV per caption."""
import hashlib
import json
from pathlib import Path

from teodub.common import atomic_json, check_cancel, hash_file
from teodub.models import configure_models

VOICES = ('Mai Anh', 'Trúc Ly', 'Thùy Dung', 'Ngọc Huyền')
REFERENCE_VOICE = 'Giọng từ mẫu'
PREVIEW_TEXT = 'Xin chào bạn! Cùng Tèo nghe thử giọng đọc này nhé.'
NATURAL_PREVIEW_TEXT = 'Bạn biết không? Chỉ cần thay đổi cách ngắt nghỉ, câu chuyện sẽ dễ nghe hơn nhiều. Cùng nghe thử nhé!'
LEGACY_PREVIEW_TEXT = ('Chào bạn! Mình là giọng đọc tiếng Việt của Tèo. Bạn chỉ cần gửi video, '
                       'mình sẽ đọc lời Việt rõ ràng, tự nhiên và không chồng tiếng.')
MODEL_ID = 'VieNeu-v3-Turbo-ONNX-fp32/vieneu-3.8.3'
_model = None
_model_name = None


def validate_selection(voice, *, provider='local', reference_audio=None, reference_sha256=None,
                       reading_style='legacy', tts_model='v3turbo', voice_speed=1.0):
    """Validate a frozen voice choice before downloading or translating a video."""
    from teodub.speech_plan import validate_style
    validate_style(reading_style)
    from teodub.speech_audio import validate_voice_speed
    validate_voice_speed(voice_speed)
    from teodub.ai_models import local_voices
    supported_voices = local_voices(tts_model)
    if provider == 'capcut':
        if reference_audio or reference_sha256:
            raise ValueError('Giọng CapCut trực tuyến không dùng tệp mẫu cục bộ.')
        from teodub.capcut_tts import validate_voice
        validate_voice(voice)
    elif provider == 'local':
        if voice == REFERENCE_VOICE:
            if not reference_audio or not reference_sha256:
                raise ValueError('Chọn tệp mẫu giọng dài 3–8 giây trước khi nghe thử hoặc lồng tiếng.')
            if not Path(reference_audio).is_absolute() or hash_file(reference_audio) != reference_sha256:
                raise ValueError('Tệp mẫu giọng đã thay đổi. Chọn lại tệp và bắt đầu phiên mới.')
        elif voice not in supported_voices or reference_audio or reference_sha256:
            raise ValueError('Chọn một giọng có trong danh sách của Tèo Dub Studio.')
    else:
        raise ValueError('Phiên bản giọng đọc chưa được hỗ trợ.')


def selection_options(config):
    return dict(provider=config.get('voice_provider', 'local'),
                reference_audio=config.get('reference_audio'),
                reference_sha256=config.get('reference_sha256'),
                tts_model=config.get('tts_model', 'v3turbo'),
                reading_style=config.get('reading_style', 'legacy'),
                voice_speed=config.get('voice_speed', 1.0))


def get_model(progress=None, tts_model='v3turbo'):
    global _model, _model_name
    from teodub.ai_models import TTS_MODELS, validate_models
    validate_models(dict(tts_model=tts_model))
    if _model is None or _model_name != tts_model:
        _model = None
        configure_models()
        if progress:
            progress('Đang nạp '+TTS_MODELS[tts_model]+'. Lần đầu cần tải mô hình miễn phí.', percent=None)
        from vieneu import Vieneu
        _model = (Vieneu(mode='v3nano', threads=4, steps=16, cfg=3.0) if tts_model == 'v3nano' else
                  Vieneu(mode='v3turbo', backend='onnx', precision='fp32', threads=4))
        _model_name = tts_model
    return _model


def validate_audio(path):
    import numpy as np
    import soundfile as sf
    data, rate = sf.read(str(path), dtype='float32')
    if (rate != 48000 or data.ndim != 1 or len(data) < rate * .08 or
            not np.isfinite(data).all() or float(np.sqrt(np.mean(data * data))) < .0001):
        raise ValueError('Giọng vừa tạo bị rỗng hoặc âm thanh không hợp lệ; chưa ghép vào video.')
    return dict(path=str(Path(path).resolve()), sha256=hash_file(path), duration_s=len(data) / rate,
                sample_rate=rate, samples=len(data))


def synthesize(cues, voice, directory, *, cancel=None, progress=None, provider='local',
               reference_audio=None, reference_sha256=None, reading_style='legacy', tts_model='v3turbo',
               voice_speed=1.0):
    check_cancel(cancel)
    from teodub.speech_plan import validate_style, PLAN_VERSION
    settings = validate_style(reading_style)
    from teodub.speech_audio import validate_voice_speed
    voice_speed = validate_voice_speed(voice_speed)
    validate_selection(voice, provider=provider, reference_audio=reference_audio,
                       reference_sha256=reference_sha256, reading_style=reading_style,
                       tts_model=tts_model, voice_speed=voice_speed)
    if not cues or any(not isinstance(c.get('text'), str) or not c['text'].strip() for c in cues):
        raise ValueError('Cần lời Việt đầy đủ trước khi tạo giọng.')
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    model_id = 'VieNeu-v3-Nano-ONNX-steps16-cfg3/vieneu-3.8.3' if tts_model == 'v3nano' else MODEL_ID
    reference = None
    if provider == 'capcut':
        from teodub.capcut_tts import MODEL_ID as model_id
    elif voice == REFERENCE_VOICE:
        from teodub.voice_profiles import prepare_reference
        reference = prepare_reference(reference_audio, directory/'reference', cancel=cancel)
        if reference['source_sha256'] != reference_sha256 or hash_file(reference_audio) != reference_sha256:
            raise ValueError('Tệp mẫu giọng thay đổi trong lúc đọc. Chưa tạo giọng.')
        model_id += '/reference-v1/' + reference_sha256 + '/' + reference['sha256']
    if reading_style != 'legacy':
        model_id += f'/{PLAN_VERSION}/{reading_style}/tempo-{settings["speech_speed"]}'
    final_tempo = settings['speech_speed'] * voice_speed
    if voice_speed != 1.0:
        model_id += f'/voice-speed-{voice_speed:g}/final-tempo-{final_tempo:.8g}'
    request = dict(model=model_id, voice=voice, cues=cues)
    request_path = directory/'request.json'
    if request_path.exists():
        if json.loads(request_path.read_text(encoding='utf-8')) != request:
            raise ValueError('Danh sách lời đọc đã thay đổi; tạo phiên mới để không bỏ sót câu.')
    else:
        if any(directory.glob('*.wav')) or (directory/'manifest.json').exists():
            raise ValueError('Cache giọng bị mất danh sách lời gốc; chưa dùng lại để tránh bỏ sót câu.')
        atomic_json(request_path, request)
    clips = []
    for index, cue in enumerate(cues, 1):
        check_cancel(cancel)
        text = cue['text']
        fingerprint = hashlib.sha256((model_id + '\n' + voice + '\n' + text).encode('utf-8')).hexdigest()
        wav, meta = directory / f'{index:05}.wav', directory / f'{index:05}.json'
        pending = directory/f'{index:05}.pending.json'
        if wav.exists() and not meta.exists() and pending.exists():
            recovery = json.loads(pending.read_text(encoding='utf-8'))
            if recovery.get('sha256') == hash_file(wav) and recovery.get('fingerprint') == fingerprint:
                atomic_json(meta, recovery)
        if meta.exists():
            saved = json.loads(meta.read_text(encoding='utf-8'))
            actual = validate_audio(wav)
            expected = dict(actual, id=index, fingerprint=fingerprint, text=text, voice=voice, model=model_id)
            if saved != expected:
                raise ValueError(f'Lời đọc câu {index} đã đổi; không trộn vào công việc đang tiếp tục.')
            clips.append(expected)
            if progress:
                progress(f'Dùng lại giọng câu {index}/{len(cues)} đã kiểm tra.', percent=round(index / len(cues) * 100))
            continue
        # Never replace a completed WAV which lacks its manifest after a crash.
        if wav.exists():
            raise ValueError(f'Giọng câu {index} có tệp nhưng thiếu báo cáo. Cần kiểm tra trước khi tiếp tục.')
        check_cancel(cancel)
        if progress:
            progress(f'Đang đọc câu {index}/{len(cues)} bằng giọng {voice}…', percent=round((index - 1) / len(cues) * 100))
        temporary = directory / f'{index:05}.writing.wav'
        needs_tempo = reading_style != 'legacy' or voice_speed != 1.0
        raw = directory/f'{index:05}.unpaced.wav' if needs_tempo else temporary
        # Network synthesis is never retried automatically after an ambiguous result.
        for attempt in range(1 if provider == 'capcut' else 2):
            check_cancel(cancel)
            if provider == 'capcut':
                from teodub.capcut_tts import synthesize_to_file
                synthesize_to_file(text, voice, raw, cancel=cancel, progress=progress)
            else:
                model = get_model(progress, tts_model=tts_model) if tts_model != 'v3turbo' else get_model(progress)
                check_cancel(cancel)
                if reference:
                    from teodub.voice_profiles import validate_reference
                    validate_reference(reference, source_sha256=reference_sha256, cancel=cancel)
                    audio = model.infer(text, ref_audio=reference['path'], denoise=True, use_ref_codes=True)
                    validate_reference(reference, source_sha256=reference_sha256, cancel=cancel)
                else:
                    audio = model.infer(text, voice=voice)
                check_cancel(cancel)
                import soundfile as sf
                if tts_model == 'v3nano':
                    # Nano emits 24 kHz. Resample samples, never relabel the rate.
                    from scipy.signal import resample_poly
                    audio = resample_poly(audio, 2, 1)
                sf.write(str(raw), audio, 48000, subtype='PCM_16')
            check_cancel(cancel)
            try:
                if needs_tempo:
                    from teodub.speech_audio import apply_tempo
                    apply_tempo(raw, temporary, final_tempo, cancel=cancel)
                data = validate_audio(temporary)
                if data['duration_s'] > max(15, len(text) * .7):
                    raise ValueError('Lời đọc dài bất thường so với văn bản.')
                break
            except ValueError:
                if attempt or provider == 'capcut':
                    raise
                if progress:
                    progress(f'Tự tạo lại câu {index} một lần vì âm thanh chưa đạt.', percent=None)
        check_cancel(cancel)
        data.update(id=index, path=str(wav), fingerprint=fingerprint, text=text, voice=voice, model=model_id)
        atomic_json(pending, data)
        temporary.rename(wav)
        atomic_json(meta, data)
        pending.unlink(missing_ok=True)
        clips.append(data)
        if progress:
            progress(f'Đã tạo giọng {index}/{len(cues)} câu.', percent=round(index / len(cues) * 100))
    check_cancel(cancel)
    atomic_json(directory / 'manifest.json', {'model': model_id, 'voice': voice, 'clips': clips})
    return clips


def preview_voice(voice, directory, *, cancel=None, progress=None, **options):
    text = NATURAL_PREVIEW_TEXT if options.get('reading_style', 'legacy') != 'legacy' else PREVIEW_TEXT
    existing = Path(directory)/'request.json'
    if existing.exists():
        # Allow an interrupted preview from before this update to finish unchanged.
        previous = json.loads(existing.read_text(encoding='utf-8'))
        if previous.get('cues') == [{'start_ms': 0, 'end_ms': 10000, 'text': LEGACY_PREVIEW_TEXT}]:
            text = LEGACY_PREVIEW_TEXT
    cues = [{'start_ms': 0, 'end_ms': 10000, 'text': text}]
    return synthesize(cues, voice, directory, cancel=cancel, progress=progress, **options)[0]['path']
