"""Isolated worker that splits a video's sound into the voices and the rest (music, ambient sounds), locally with Demucs htdemucs.

Runs in the same separate environment as the GPU subtitle remover (.venv-inpaint: PyTorch and PyAV). It uses the CPU on purpose
(owner 13/11/2026): the video work already shares a small GPU, and a CPU run takes about 0.75x of the sound's length. Nothing is sent
anywhere. The model file is checked against a pinned SHA-256 by the caller; only the parts of Demucs that are plain Python are used.
"""
import argparse
import json
import math
import os
from pathlib import Path
import sys
import time
import types
import wave

from teodub.common import atomic_json

RATE = 44100


def _stub_optional_imports():
    """Demucs imports `dora.log` (a training helper) and `openunmix`, whose package init needs torchaudio. Neither is used to
    separate sound: give Demucs a two-line `dora.log` and let `openunmix.filtering` load without running openunmix's __init__."""
    try:
        import dora.log  # noqa: F401
    except ImportError:
        log = types.ModuleType('dora.log')
        log.fatal = lambda *args: (_ for _ in ()).throw(SystemExit(' '.join(str(item) for item in args)))
        log.bold = str
        package = types.ModuleType('dora')
        package.__path__, package.log = [], log
        sys.modules.setdefault('dora', package)
        sys.modules.setdefault('dora.log', log)
    try:
        import openunmix.filtering  # noqa: F401
    except ImportError:
        import importlib.machinery
        sys.modules.pop('openunmix', None)
        spec = importlib.machinery.PathFinder.find_spec('openunmix')
        if spec is None or not spec.submodule_search_locations:
            raise
        package = types.ModuleType('openunmix')
        package.__path__ = list(spec.submodule_search_locations)
        sys.modules['openunmix'] = package
        import openunmix.filtering  # noqa: F401


def check():
    import torch
    _stub_optional_imports()
    from demucs.apply import apply_model  # noqa: F401
    from demucs.states import load_model  # noqa: F401
    import av
    import numpy  # noqa: F401
    return dict(ok=True, torch=torch.__version__, av=av.__version__, device='cpu')


def decode(source):
    """The first audio stream as float32 stereo at 44.1 kHz, shape (2, samples)."""
    import av
    import numpy as np
    pieces = []
    with av.open(str(source)) as container:
        if not container.streams.audio:
            raise ValueError('Video này không có âm thanh để tách giọng.')
        resampler = av.AudioResampler(format='fltp', layout='stereo', rate=RATE)
        for frame in container.decode(audio=0):
            for converted in resampler.resample(frame):
                pieces.append(converted.to_ndarray())
        for converted in resampler.resample(None):
            pieces.append(converted.to_ndarray())
    if not pieces:
        raise ValueError('Không đọc được âm thanh của video.')
    return np.concatenate(pieces, axis=1).astype('float32')


def write_wav(path, data):
    import numpy as np
    pcm = (np.clip(data.T, -1, 1)*32767).astype('<i2')
    with wave.open(str(path), 'wb') as target:
        target.setparams((2, 2, RATE, 0, 'NONE', 'not compressed'))
        target.writeframes(pcm.tobytes())


def separate(request):
    import numpy as np
    import torch
    started = time.time()
    status = Path(request['status'])
    audio = decode(request['source'])
    seconds = audio.shape[1]/RATE
    atomic_json(status, dict(status='running', percent=0, seconds=round(seconds, 1)))
    threads = max(2, min(4, (os.cpu_count() or 2)//2))
    torch.set_num_threads(threads)
    _stub_optional_imports()
    from demucs.apply import apply_model
    from demucs.states import load_model
    # The model file is a PyTorch pickle of the Demucs classes: it is opened only after its SHA-256 matches the pinned one (the caller
    # checked it too), so `weights_only=False` is safe here and nothing else is ever loaded this way.
    import hashlib
    digest = hashlib.sha256(Path(request['model']).read_bytes()).hexdigest()
    if digest != request['model_sha256']:
        raise ValueError('Mô hình tách giọng không đúng bản đã chọn.')
    model = load_model(torch.load(request['model'], 'cpu', weights_only=False))
    model.eval()
    if getattr(model, 'samplerate', RATE) != RATE or list(model.sources)[-1] != 'vocals':
        raise ValueError('Mô hình tách giọng không đúng loại đã chọn.')
    wave_in = torch.from_numpy(audio)
    reference = wave_in.mean(0)
    mix = (wave_in-reference.mean())/(reference.std()+1e-8)
    overlap, segment_length = .25, int(model.samplerate*model.segment)
    total = max(1, math.ceil(mix.shape[1]/int((1-overlap)*segment_length)))
    done = [0]

    def count(_module, _inputs, _output):
        done[0] += 1
        percent = min(99, done[0]*100/total)
        atomic_json(status, dict(status='running', percent=round(percent, 1), seconds=round(seconds, 1)))

    handle = model.register_forward_hook(count)
    try:
        with torch.no_grad():
            stems = apply_model(model, mix[None], device='cpu', shifts=0, split=True, overlap=overlap, progress=False,
                                num_workers=0)[0]
    finally:
        handle.remove()
    stems = stems*(reference.std()+1e-8)+reference.mean()
    names = list(model.sources)
    rest = sum(stems[index] for index, name in enumerate(names) if name != 'vocals')
    rest = rest.numpy()
    if not np.isfinite(rest).all():
        raise ValueError('Kết quả tách giọng có dữ liệu không hợp lệ.')
    target = Path(request['target'])
    write_wav(target, rest)
    report = dict(status='completed', percent=100, seconds=round(seconds, 1), took=round(time.time()-started, 1),
                  device='cpu', threads=threads, sample_rate=RATE, samples=int(rest.shape[1]), sources=names)
    atomic_json(status, report)
    return report


def main(args=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('request', nargs='?')
    parser.add_argument('--check', action='store_true')
    arguments = parser.parse_args(args)
    if arguments.check:
        print(json.dumps(check()))
        return 0
    request = json.loads(Path(arguments.request).read_text(encoding='utf-8'))
    separate(request)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
