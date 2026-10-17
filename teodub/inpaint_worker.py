"""Isolated CUDA worker. STTN runs locally; no cloud calls or CPU inference fallback."""
import argparse
from fractions import Fraction
import json
from pathlib import Path
import time

from teodub.common import atomic_json


def gpu():
    import torch
    if not torch.cuda.is_available():
        raise RuntimeError('Không tìm thấy GPU NVIDIA/CUDA. Không chuyển ngầm sang CPU.')
    free, total = torch.cuda.mem_get_info()
    if free < 2.5*1024**3:
        raise RuntimeError('GPU cần ít nhất 2,5 GB bộ nhớ trống. Đóng tác vụ GPU khác rồi thử lại.')
    torch.set_num_threads(4)
    return dict(device=torch.cuda.get_device_name(0), torch=torch.__version__, free_bytes=free, total_bytes=total)


class Inpainter:
    def __init__(self, model_path):
        import torch
        from teodub.vendor.sttn.network import InpaintGenerator
        self.model = InpaintGenerator(init_weights=False).eval().to('cuda')
        self.model.load_state_dict(torch.load(model_path, map_location='cpu', weights_only=True)['netG'])

    def frames(self, originals, region):
        import cv2
        import numpy as np
        import torch
        height, width = originals[0].shape[:2]
        x1, y1, x2, y2 = [round(v*s) for v, s in zip(region, (width, height, width, height))]
        if x2 <= x1 or y2 <= y1:
            raise ValueError('Vùng chữ quá nhỏ ở độ phân giải video này.')
        crop_h = min(height, max(y2-y1+32, round(width*240/432)))
        top = min(height-crop_h, max(0, (y1+y2-crop_h)//2))
        mask = np.zeros((crop_h, width), np.float32)
        mask[y1-top:y2-top, x1:x2] = 1
        small_mask = cv2.resize(mask, (432, 240), interpolation=cv2.INTER_NEAREST)
        arrays = np.stack([cv2.resize(f[top:top+crop_h], (432, 240)) for f in originals])
        data = torch.from_numpy(arrays).permute(0, 3, 1, 2).float().to('cuda')/127.5-1
        masks = torch.from_numpy(small_mask).to('cuda')[None, None].expand(len(data), 1, 240, 432)
        results, counts = [None]*len(data), [0]*len(data)
        with torch.inference_mode():
            feats = self.model.encoder(data*(1-masks))
            for center in range(0, len(data), 4):
                neighbors = list(range(max(0, center-4), min(len(data), center+5)))
                refs = [i for i in range(0, len(data), 10) if i not in neighbors]
                ids = neighbors+refs
                features = self.model.infer(feats[ids], masks[ids])
                prediction = (torch.tanh(self.model.decoder(features[:len(neighbors)]))+1)*127.5
                prediction = prediction.permute(0, 2, 3, 1).cpu().numpy()
                for index, value in zip(neighbors, prediction):
                    results[index] = value if results[index] is None else results[index]+value
                    counts[index] += 1
        final = []
        for index, original in enumerate(originals):
            patch = cv2.resize(results[index]/counts[index], (width, crop_h)).clip(0, 255).astype(np.uint8)
            frame = original.copy()
            # Never resample or replace pixels outside the selected rectangle.
            frame[y1:y2, x1:x2] = patch[y1-top:y2-top, x1:x2]
            final.append(frame)
        return final


def run(request):
    import av
    import numpy as np
    import torch
    info = gpu()
    from teodub.subtitle_clean import MODEL_SHA
    from teodub.common import hash_file
    if hash_file(request['model']) != MODEL_SHA:
        raise ValueError('Mô hình không đúng phiên bản đã kiểm tra.')
    started = time.monotonic()
    painter = Inpainter(request['model'])
    target, status = Path(request['target']), Path(request['status'])
    if target.exists():
        raise ValueError('Không ghi đè tệp xử lý đang tồn tại.')
    count, first_pts, last_time, pending, stamps, previous = 0, None, 0., [], [], None
    prefix_skip = 0
    with av.open(request['source']) as source, av.open(str(target), 'w') as output:
        stream = source.streams.video[0]
        duration = float(stream.duration*stream.time_base) if stream.duration else source.duration/av.time_base
        rate = stream.average_rate or Fraction(30)
        destination = output.add_stream('ffv1', rate=rate)
        destination.width, destination.height = stream.width, stream.height
        destination.pix_fmt = 'bgr0'
        destination.time_base = Fraction(1, 90000)
        destination.codec_context.thread_count = 4

        def flush(final=True):
            nonlocal count, last_time, pending, stamps, prefix_skip
            if not pending:
                return
            end = len(pending) if final else len(pending)-4
            predictions = painter.frames(pending, request['region'])
            for pixels, stamp in zip(predictions[prefix_skip:end], stamps[prefix_skip:end]):
                frame = av.VideoFrame.from_ndarray(pixels, format='rgb24')
                frame.pts = round(stamp*90000)
                frame.time_base = Fraction(1, 90000)
                for packet in destination.encode(frame):
                    output.mux(packet)
                count += 1
                last_time = stamp
            if final:
                pending, stamps, prefix_skip = [], [], 0
            else:
                # Four past and four future frames bridge each model batch.
                pending, stamps, prefix_skip = pending[end-4:], stamps[end-4:], 4
            atomic_json(status, dict(status='running', frames=count, percent=min(99, 100*last_time/duration), **info))

        for frame in source.decode(video=0):
            if frame.pts is None:
                raise ValueError('Nguồn thiếu mốc khung hình; chưa xử lý để tránh lệch hình/tiếng.')
            stamp = float(frame.pts*frame.time_base)
            if first_pts is None:
                first_pts = stamp
            pixels = frame.to_ndarray(format='rgb24')
            thumbnail = pixels[::16, ::16].astype(np.float32)
            # Do not use reference frames across a hard scene cut.
            if previous is not None and np.abs(thumbnail-previous).mean() > 45:
                flush()
            previous = thumbnail
            pending.append(pixels)
            stamps.append(stamp-first_pts)
            if len(pending) >= 24+prefix_skip:
                flush(final=False)
        flush()
        if not count:
            raise ValueError('Nguồn không có khung hình.')
        for packet in destination.encode():
            output.mux(packet)
    # Decode every saved frame before calling this cache complete.
    with av.open(str(target)) as result:
        decoded = sum(1 for _ in result.decode(video=0))
    if decoded != count:
        raise ValueError('Tệp xóa chữ thiếu khung hình; chưa dùng để xuất.')
    atomic_json(status, dict(status='completed', frames=count, percent=100, elapsed_s=time.monotonic()-started,
                peak_gpu_bytes=torch.cuda.max_memory_allocated(), **info))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('request', nargs='?')
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    if args.check:
        import av, cv2
        print(json.dumps(gpu()))
    else:
        run(json.loads(Path(args.request).read_text(encoding='utf-8')))
