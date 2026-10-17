"""Local model storage and per-worker GPU DLL discovery."""
import os
from pathlib import Path
import site
import sys

_dll_handles = []


def model_directory():
    root = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parents[1]
    directory = Path(os.environ.get('TEO_DUB_MODELS', root / 'models')).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def configure_models():
    directory = model_directory()
    os.environ['HF_HOME'] = str(directory / 'huggingface')
    os.environ['HF_HUB_DISABLE_SYMLINKS_WARNING'] = '1'
    os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'
    os.environ['HF_HUB_DOWNLOAD_TIMEOUT'] = '120'
    os.environ['HF_HUB_ETAG_TIMEOUT'] = '30'
    # Limit CPU contention with other applications. Only this worker is affected.
    os.environ.setdefault('OMP_NUM_THREADS', '4')
    os.environ.setdefault('MKL_NUM_THREADS', '4')
    os.environ.setdefault('NUMBA_NUM_THREADS', '4')
    if os.name == 'nt' and not _dll_handles:
        for base in site.getsitepackages():
            for library in ('cublas', 'cudnn', 'cuda_nvrtc', 'cuda_runtime'):
                folder = Path(base) / 'nvidia' / library / 'bin'
                if folder.is_dir():
                    _dll_handles.append(os.add_dll_directory(str(folder)))
                    os.environ['PATH'] = str(folder) + os.pathsep + os.environ.get('PATH', '')
    return directory
