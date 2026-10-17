"""Select a supported Whisper runtime before loading, preserving frozen models."""
import ctypes
import os


# Conservative routing budgets, not guarantees about peak allocation. Other
# programs and the audio length can still affect available GPU memory.
GPU_BUDGET_MIB = {'small': 1024, 'medium': 3072, 'large-v3': 5120}


def gpu_memory_mib():
    """Read device 0 total memory without creating a CUDA context or a process."""
    if os.environ.get('CUDA_VISIBLE_DEVICES', '').strip() not in ('', '0'):
        return None  # Do not confuse physical and remapped device ordinals.
    try:
        cuda = ctypes.WinDLL('nvcuda.dll') if os.name == 'nt' else ctypes.CDLL('libcuda.so.1')
        cuda.cuInit.argtypes, cuda.cuInit.restype = [ctypes.c_uint], ctypes.c_int
        cuda.cuDeviceGet.argtypes = [ctypes.POINTER(ctypes.c_int), ctypes.c_int]
        cuda.cuDeviceGet.restype = ctypes.c_int
        total_memory = cuda.cuDeviceTotalMem_v2
        total_memory.argtypes = [ctypes.POINTER(ctypes.c_size_t), ctypes.c_int]
        total_memory.restype = ctypes.c_int
        device, size = ctypes.c_int(), ctypes.c_size_t()
        if (cuda.cuInit(0) or cuda.cuDeviceGet(ctypes.byref(device), 0)
                or total_memory(ctypes.byref(size), device.value)):
            return None
        return size.value // (1024 * 1024) or None
    except (OSError, AttributeError):
        return None


def _compute_type(backend, device):
    supported = backend.get_supported_compute_types(device)
    choices = (('int8_float16', 'int8_float32', 'int8', 'float16', 'float32')
               if device == 'cuda' else ('int8', 'int8_float32', 'float32'))
    return next((choice for choice in choices if choice in supported), None)


def recognizer_runtime(requested_model, *, resolved_model=None, backend=None):
    if requested_model not in ('auto', *GPU_BUDGET_MIB):
        raise ValueError('Mô hình nhận dạng không được hỗ trợ.')
    if resolved_model is not None and (resolved_model not in GPU_BUDGET_MIB or
            requested_model != 'auto' and requested_model != resolved_model):
        raise ValueError('Mô hình nhận dạng đã lưu không khớp lựa chọn.')
    if backend is None:
        import ctranslate2 as backend
    cuda = bool(backend.get_cuda_device_count())
    gpu_type = _compute_type(backend, 'cuda') if cuda else None
    memory = gpu_memory_mib() if gpu_type else None
    multiplier = 1 if gpu_type and gpu_type.startswith('int8') else 2
    model = resolved_model or (requested_model if requested_model != 'auto' else None)
    note = ''
    if model is None:
        if memory is not None:
            model = next((name for name in ('large-v3', 'medium', 'small')
                          if memory >= GPU_BUDGET_MIB[name] * multiplier), None)
        if model is None:
            model = 'medium'
        elif model != 'large-v3':
            note = f'GPU {memory} MB: Tự động chọn Whisper {model} để giảm dùng bộ nhớ.'
    if gpu_type and memory is not None and memory >= GPU_BUDGET_MIB[model] * multiplier:
        return dict(model=model, device='cuda', compute_type=gpu_type, note=note)
    if cuda:
        reason = (f'GPU {memory} MB chưa đủ ngưỡng dùng Whisper {model}' if memory is not None else
                  'Chưa xác định được bộ nhớ hoặc chế độ Whisper phù hợp trên GPU')
        note = reason + '; chạy bằng CPU, giữ nguyên mô hình.'
    cpu_type = _compute_type(backend, 'cpu')
    if cpu_type is None:
        raise ValueError('CPU không có chế độ tính toán Whisper được hỗ trợ; kiểm tra CTranslate2.')
    return dict(model=model, device='cpu', compute_type=cpu_type, note=note)
