"""Immutable, job-local reference audio for local voice approximation.

This never changes VieNeu's packaged presets. References are input examples,
not a promise to reproduce an exact voice, accent, delivery, or provider preset.
"""
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
import wave

from teodub.common import check_cancel, hash_file

RATE = 48000
MIN_SECONDS = 3
MAX_SECONDS = 8
NORMALIZATION = 'first8s-mono48k-pcm16-v1'
_SHA = re.compile(r'[0-9a-f]{64}')


def describe_voice_settings(config):
    """Describe the frozen engine/sample, without reading audio or loading a model."""
    from teodub.ai_models import TTS_MODELS
    from teodub.speech_plan import STYLES
    voice = config.get('voice') or 'Chưa có giọng'
    provider = config.get('voice_provider', 'local')
    if provider == 'local' and voice == 'Giọng từ mẫu':
        source = config.get('reference_audio')
        if source:
            reference = Path(source).resolve()
            root = Path(__file__).resolve().parents[1]/'assets/voice-references'
            aliases = {'co-gai-hoat-ngon.wav': 'Cô Gái Hoạt Ngôn (theo mẫu)',
                       'thanh-nien-tu-tin.wav': 'Thanh Niên Tự Tin (theo mẫu)'}
            voice = next((label for name, label in aliases.items()
                          if reference == (root/name).resolve()), 'Giọng từ mẫu · '+reference.name)
            if (reference.parent.parent.name == 'adam' and reference.parent.parent.parent.name == 'voice-library'
                    and reference.name == 'reference.wav'):
                voice = 'ADAM (theo mẫu)'
            # Saved learned references keep a descriptive label in campaign previews.
            # This is display-only; engine dispatch still validates the frozen audio hash.
            if reference.name == 'reference.wav' and (reference.parent/'profile.json').is_file():
                try:
                    from teodub.voice_learning import profile_label
                    profile = json.loads((reference.parent/'profile.json').read_text(encoding='utf-8'))
                    if (profile.get('version') == 1 and profile.get('reference_audio') == str(reference)
                            and profile.get('reference_sha256') == config.get('reference_sha256')):
                        voice = profile_label(profile)
                except (OSError, ValueError, KeyError, TypeError):
                    pass
    engine = ('CapCut trực tuyến' if provider == 'capcut' else
              'Trên máy · '+TTS_MODELS.get(config.get('tts_model', 'v3turbo'), str(config.get('tts_model'))))
    style = config.get('reading_style', 'legacy')
    style_label = STYLES.get(style, {}).get('label', str(style))
    try:
        speed = f'{float(config.get("voice_speed", 1.0)):.2f}'.replace('.', ',')+'×'
    except (TypeError, ValueError):
        speed = 'Tốc độ chưa hợp lệ'
    return f'{voice} | {engine} | {style_label} | {speed}'


def _fingerprint(source_sha256, audio_sha256):
    return hashlib.sha256((NORMALIZATION+'\n'+source_sha256+'\n'+audio_sha256).encode('ascii')).hexdigest()


def _publish(source, target):
    """Publish a complete new file, refusing a concurrently created target."""
    source, target = Path(source), Path(target)
    if os.name == 'nt':
        source.rename(target)
    else:
        os.link(source, target)
        source.unlink()


def _json_new(path, data):
    path = Path(path)
    # Re-entering a matching, completed publication step is safe.
    if path.exists():
        if json.loads(path.read_text(encoding='utf-8')) != data:
            raise ValueError('Thông tin mẫu giọng đã thay đổi; không ghi đè.')
        return
    with tempfile.TemporaryDirectory(prefix='.profile-json-', dir=path.parent) as temporary:
        staged = Path(temporary)/'data.json'
        with staged.open('x', encoding='utf-8') as file:
            json.dump(data, file, ensure_ascii=False, indent=2)
            file.flush()
            os.fsync(file.fileno())
        _publish(staged, path)


def _audio_details(path, cancel):
    import numpy as np
    before = hash_file(path)
    values = []
    with wave.open(str(path), 'rb') as audio:
        if (audio.getnchannels() != 1 or audio.getsampwidth() != 2 or audio.getframerate() != RATE
                or audio.getcomptype() != 'NONE'):
            raise ValueError('Mẫu giọng đã lưu phải là WAV mono 48 kHz PCM16.')
        total = audio.getnframes()
        if not MIN_SECONDS*RATE <= total <= MAX_SECONDS*RATE:
            raise ValueError('Mẫu giọng cần ít nhất 3 giây; chỉ dùng tối đa 8 giây đầu.')
        while chunk := audio.readframes(RATE):
            check_cancel(cancel)
            values.append(np.frombuffer(chunk, dtype='<i2').astype(np.float64)/32768)
    samples = np.concatenate(values) if values else np.empty(0)
    if len(samples) != total:
        raise ValueError('Mẫu giọng đã lưu bị thiếu dữ liệu âm thanh.')
    rms = float(np.sqrt(np.mean(samples*samples)))
    peak = float(np.max(np.abs(samples)))
    if not math.isfinite(rms) or rms < .0001:
        raise ValueError('Mẫu giọng im lặng hoặc quá nhỏ. Chọn đoạn nói rõ, ít nhạc nền.')
    check_cancel(cancel)
    if hash_file(path) != before:
        raise ValueError('Mẫu giọng thay đổi trong lúc kiểm tra.')
    return dict(sha256=before, duration_s=total/RATE, sample_rate=RATE, channels=1,
                sample_width=2, samples=total, rms=rms, peak=peak)


def validate_reference(metadata, *, source_sha256=None, cancel=None):
    """Validate saved normalized audio and its identity; no model or network.

    An optional frozen source hash binds this profile to the selected original.
    The original media is not read here; prepare_reference checks it before and
    after preparation. This lightweight check can be repeated before inference.
    """
    check_cancel(cancel)
    if not isinstance(metadata, dict) or metadata.get('schema') != 1 or metadata.get('normalization') != NORMALIZATION:
        raise ValueError('Thông tin mẫu giọng đã lưu không hợp lệ.')
    for key in ('source_sha256', 'sha256', 'fingerprint'):
        if not isinstance(metadata.get(key), str) or not _SHA.fullmatch(metadata[key]):
            raise ValueError('Mẫu giọng thiếu dấu vân tay hợp lệ.')
    if source_sha256 is not None and metadata['source_sha256'] != source_sha256:
        raise ValueError('Mẫu giọng không khớp tệp đã chọn trong cấu hình.')
    path = Path(metadata.get('path', ''))
    source = Path(metadata.get('source_path', ''))
    if (not path.is_absolute() or not source.is_absolute() or not path.is_file()
            or path.is_symlink() or getattr(path, 'is_junction', lambda: False)()):
        raise ValueError('Đường dẫn mẫu giọng đã lưu không hợp lệ.')
    if metadata['fingerprint'] != _fingerprint(metadata['source_sha256'], metadata['sha256']):
        raise ValueError('Dấu vân tay mẫu giọng không khớp.')
    actual = _audio_details(path, cancel)
    for key, value in actual.items():
        if metadata.get(key) != value:
            raise ValueError('Mẫu giọng đã thay đổi hoặc thông số không khớp; chưa dùng lại.')
    check_cancel(cancel)
    return dict(metadata)


def _normalize(source, destination, cancel):
    import av
    import numpy as np
    remaining = MAX_SECONDS*RATE
    chunks = []
    with av.open(str(source)) as container:
        if len(container.streams.audio) != 1:
            raise ValueError('Tệp mẫu cần đúng một luồng âm thanh có giọng nói.')
        container.streams.audio[0].codec_context.thread_count = 2
        resampler = av.AudioResampler(format='fltp', layout='mono', rate=RATE)

        def consume(frame):
            nonlocal remaining
            data = frame.to_ndarray().reshape(-1)
            if not np.isfinite(data).all():
                raise ValueError('Tệp mẫu chứa âm thanh không hợp lệ (NaN/Inf).')
            take = min(remaining, len(data))
            if take:
                chunks.append(data[:take].copy())
                remaining -= take

        for frame in container.decode(audio=0):
            check_cancel(cancel)
            # Check before format conversion, which could hide float NaNs.
            if not np.isfinite(frame.to_ndarray()).all():
                raise ValueError('Tệp mẫu chứa âm thanh không hợp lệ (NaN/Inf).')
            for converted in resampler.resample(frame):
                consume(converted)
            if not remaining:
                break
        if remaining:
            for converted in resampler.resample(None):
                consume(converted)
    check_cancel(cancel)
    samples = np.concatenate(chunks) if chunks else np.empty(0)
    if len(samples) < MIN_SECONDS*RATE:
        raise ValueError('Mẫu giọng cần ít nhất 3 giây lời nói rõ.')
    # Format conversion only; never amplify noise or apply a voice effect.
    pcm = np.rint(np.clip(samples, -1, 1)*32767).astype('<i2')
    with wave.open(str(destination), 'wb') as audio:
        audio.setparams((1, 2, RATE, 0, 'NONE', 'not compressed'))
        audio.writeframes(pcm.tobytes())
    return _audio_details(destination, cancel)


def prepare_reference(source_path, directory, *, cancel=None):
    """Copy the first 3–8 s into an immutable, checked job-local profile."""
    check_cancel(cancel)
    source = Path(source_path).resolve(strict=True)
    if not source.is_file():
        raise ValueError('Chọn tệp âm thanh hoặc video làm mẫu giọng.')
    digest = hash_file(source)
    check_cancel(cancel)
    folder = Path(directory).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    audio, manifest, pending = folder/'reference.wav', folder/'reference.json', folder/'reference.pending.json'
    if source == audio:
        raise ValueError('Chọn tệp mẫu gốc, không dùng tệp nội bộ của phiên làm nguồn.')

    def bind(saved):
        if (saved.get('path') != str(audio) or saved.get('source_path') != str(source)
                or saved.get('source_sha256') != digest or saved.get('normalization') != NORMALIZATION):
            raise ValueError('Tệp mẫu đã thay đổi hoặc không khớp phiên đang tiếp tục.')

    def unchanged():
        check_cancel(cancel)
        if hash_file(source) != digest:
            raise ValueError('Tệp mẫu gốc thay đổi trong lúc chuẩn bị; chưa dùng giọng này.')

    if manifest.exists() or audio.exists():
        record = manifest if manifest.exists() else pending
        if not record.exists():
            raise ValueError('Mẫu giọng có tệp nhưng thiếu báo cáo; không tự ghi đè.')
        saved = json.loads(record.read_text(encoding='utf-8'))
        bind(saved)
        result = validate_reference(saved, source_sha256=digest, cancel=cancel)
        unchanged()
        if not manifest.exists():
            _json_new(manifest, result)
        pending.unlink(missing_ok=True)
        return result

    if pending.exists():
        bind(json.loads(pending.read_text(encoding='utf-8')))
    with tempfile.TemporaryDirectory(prefix='.reference-work-', dir=folder) as temporary:
        staged = Path(temporary)/'reference.wav'
        details = _normalize(source, staged, cancel)
        unchanged()
        result = dict(schema=1, normalization=NORMALIZATION, source_path=str(source), source_sha256=digest,
                      path=str(audio), fingerprint=_fingerprint(digest, details['sha256']), **details)
        _json_new(pending, result)
        check_cancel(cancel)
        _publish(staged, audio)
        check_cancel(cancel)
        _json_new(manifest, result)
    pending.unlink(missing_ok=True)
    unchanged()
    return validate_reference(result, source_sha256=digest, cancel=cancel)
