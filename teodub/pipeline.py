"""Resumable dubbing worker. Codex translates captions; speech engine is explicit."""
import argparse
from contextlib import contextmanager
from datetime import datetime
import json
import math
import os
from pathlib import Path
import re
import shutil
import traceback

from teodub.common import Cancelled, FileCancel, atomic_json, check_cancel, hash_file, protect_worker
from teodub.subtitles import read_vietnamese_srt


def _read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


@contextmanager
def _job_lock(directory):
    # OS locks are released after a crash. Never guess from a stale PID file.
    with (directory/'worker.lock').open('a+b') as stream:
        stream.seek(0, 2)
        if stream.tell() == 0:
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise ValueError('Phiên này đang được một tiến trình khác xử lý.') from None
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def _probe_source(path):
    import av
    source = Path(path).resolve(strict=True)
    before = hash_file(source)
    with av.open(str(source)) as media:
        videos = list(media.streams.video)
        if not videos:
            raise ValueError('Tệp nguồn không có hình ảnh video.')
        video = videos[0]
        duration = float(video.duration*video.time_base) if video.duration else float(media.duration or 0)/av.time_base
        if duration <= 0 or video.width <= 0 or video.height <= 0:
            raise ValueError('Không đọc được thời lượng hoặc kích thước video nguồn.')
        next(media.decode(video))
        result = dict(path=str(source), sha256=before, duration_s=duration,
                      width=video.width, height=video.height, has_audio=bool(media.streams.audio))
    if hash_file(source) != before:
        raise ValueError('Video nguồn thay đổi trong lúc kiểm tra.')
    return result


MAX_LATE_LINES = 5   # Recognized lines starting after the picture that may be pulled inside it; more still stop.


def _end_with_picture(cues, duration_s):
    """Recognized lines past the SRT check's 200 ms are pulled inside the picture, in proportion, from the first such
    line's start (an earlier line's if it leaves no room) to the picture end, order and text kept (owner 24/10/2026:
    Whisper ended demo's video 9 at 2:46.3 in a 2:45.87 video and slotted video 21's last lines up to 2:22.2 in a
    2:19.37 one). A supplied SRT is not changed."""
    end = math.floor(duration_s*1000)
    late = next((index for index, cue in enumerate(cues) if cue['end_ms'] > duration_s*1000+200), None)
    if late is None or sum(cue['start_ms'] >= end for cue in cues) > MAX_LATE_LINES:
        return cues
    first = late
    while first > 0 and cues[first]['start_ms'] > end-(len(cues)-first):
        first -= 1
    start = cues[first]['start_ms']
    if start > end-(len(cues)-first):
        return cues  # No room inside the picture: the SRT check reports it.
    scale = (end-start)/(cues[-1]['end_ms']-start)
    fitted = [dict(cue, start_ms=start+round((cue['start_ms']-start)*scale),
                   end_ms=start+round((cue['end_ms']-start)*scale)) for cue in cues[first:]]
    return cues if any(cue['end_ms'] <= cue['start_ms'] for cue in fitted) else cues[:first]+fitted


def _safe_title(text):
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', ' ', text)
    text = ' '.join(text.split()).strip(' .')[:65] or 'Video'
    return 'Teo_'+text


def _bounded_output_name(root, name, directory):
    """Leave room for both the export filename and temporary audio on Windows.

    Applied only before publishing a render; completed outputs retain their paths.
    Windows packaged apps may redirect AppData to a much deeper physical path.
    """
    if os.name != 'nt':
        return name
    def longest(value):
        paths = (root/value/(value+'.mp4'), root/value/'.render-work-xxxxxxxx'/'normalized'/'group-001.wav')
        return max(len(str(p).encode('utf-16-le'))//2 for p in paths)
    if longest(name) <= 230:
        return name
    short = 'Teo_'+directory.name[:12]
    if longest(short) > 230:
        raise ValueError('Đường dẫn lưu thành phẩm quá dài trên Windows. Chọn thư mục lưu ngắn hơn cho phiên mới.')
    return short


class Job:
    def __init__(self, config):
        self.config = dict(config)
        self.directory = Path(config['directory']).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        if Path(config['stop_file']).resolve() != self.directory/'stop.request':
            raise ValueError('Điểm dừng không thuộc phiên xử lý này.')
        self.cancel = FileCancel(config['stop_file'])
        self.state_path = self.directory/'state.json'
        self.state = _read(self.state_path) if self.state_path.exists() else {}

    def save(self, **values):
        self.state.update(values)
        atomic_json(self.state_path, self.state)

    def emit(self, stage, message, percent=None):
        row = dict(time=datetime.now().isoformat(timespec='seconds'), stage=stage, message=str(message), percent=percent)
        with (self.directory/'events.jsonl').open('a', encoding='utf-8') as file:
            file.write(json.dumps(row, ensure_ascii=False)+'\n')
        print(message, flush=True)

    def progress(self, stage, start, end):
        return lambda message, percent=None: self.emit(stage, message,
            start if percent is None else round(start+(end-start)*max(0, min(100, percent))/100, 1))

    def bind(self):
        path = self.directory/'request.json'
        self.resuming = path.exists()
        if self.resuming:
            if _read(path) != self.config:
                raise ValueError('Cấu hình phiên đã thay đổi. Bấm Bắt đầu để tạo phiên riêng cho cấu hình mới.')
        else:
            atomic_json(path, self.config)
        check_cancel(self.cancel)

    def acquire(self):
        saved = self.state.get('source')
        if saved:
            actual = _probe_source(saved['path'])
            if actual['sha256'] != saved['sha256']:
                raise ValueError('Video nguồn của phiên đã bị thay đổi. Chưa dùng lại.')
            self.emit('source', 'Dùng lại video nguồn đã kiểm tra.', 15)
            return actual
        text = str(self.config.get('input', '')).strip()
        if not text:
            raise ValueError('Chưa chọn video hoặc nhập link.')
        if not re.search(r'https?://', text):
            source = _probe_source(text)
        else:
            from teodub.download import download_video
            # Recover a finished download after a crash before our state save.
            previous = sorted(self.directory.glob('acquisition-*/download/result.json'))
            source = None
            for report_path in previous:
                report = _read(report_path)
                if report.get('status') == 'downloaded':
                    source = _probe_source(report['source_path'])
                    if source['sha256'] != report['metadata']['sha256']:
                        raise ValueError('Video tải về đã bị thay đổi.')
                    break
            if source is None:
                attempt = self.directory/f'acquisition-{len(previous)+1:03}'
                while attempt.exists():
                    attempt = self.directory/f'acquisition-{int(attempt.name.split("-")[-1])+1:03}'
                downloaded = download_video(text, self.config['original_dir'], str(attempt),
                    cancel=self.cancel, progress=self.progress('download', 0, 15))
                source = _probe_source(downloaded['source_path'])
                if source['sha256'] != downloaded['metadata']['sha256']:
                    raise ValueError('Video tải về thay đổi sau kiểm tra.')
        check_cancel(self.cancel)
        basename = self.config.get('campaign_basename')
        if basename:
            if not re.fullmatch(r'Video [1-9]\d*_\d{2}-\d{2}-\d{4}', basename):
                raise ValueError('Tên video trong chiến dịch không hợp lệ.')
            target = Path(self.config['original_dir'])/(basename+'.mp4')
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                if hash_file(target) != source['sha256']:
                    raise ValueError('Video gốc có tên này đã tồn tại với nội dung khác; không ghi đè.')
            else:
                # A same-volume hard link saves space; otherwise copy and verify.
                try:
                    os.link(source['path'], target)
                except FileExistsError:
                    if hash_file(target) != source['sha256']:
                        raise ValueError('Video gốc đã tồn tại với nội dung khác.')
                except OSError:
                    from teodub.render import _publish
                    temporary = self.directory/'original-copy.partial'
                    with open(source['path'], 'rb') as original, temporary.open('wb') as output:
                        while block := original.read(1024*1024):
                            check_cancel(self.cancel)
                            output.write(block)
                    if hash_file(temporary) != source['sha256']:
                        raise ValueError('Bản sao video gốc chưa toàn vẹn.')
                    # Publication must occur on the destination volume.
                    import tempfile
                    with tempfile.TemporaryDirectory(prefix='.teo-copy-', dir=target.parent) as folder:
                        staged = Path(folder)/target.name
                        shutil.copyfile(temporary, staged)
                        if hash_file(staged) != source['sha256']:
                            raise ValueError('Bản sao video gốc chưa toàn vẹn.')
                        check_cancel(self.cancel)
                        _publish(staged, target)
                    temporary.unlink(missing_ok=True)
            if hash_file(target) != source['sha256']:
                raise ValueError('Video gốc bị thay đổi sau khi đặt tên.')
            source = dict(source, path=str(target.resolve()))
        self.save(source=source)
        self.emit('source', 'Video nguồn đã sẵn sàng.', 15)
        return source

    def captions(self, source):
        folder = self.directory/'captions'
        folder.mkdir(exist_ok=True)
        vietnamese = folder/'vietnamese.srt'
        supplied = self.config.get('subtitles')
        if self.config.get('require_subtitles') and not supplied:
            raise ValueError('Chưa có lời Việt. Chạy Tải video và dịch Sub hoặc chọn SRT trước khi lồng tiếng.')
        if supplied:
            digest = hash_file(supplied)
            if self.config.get('subtitles_sha256') and digest != self.config['subtitles_sha256']:
                raise ValueError('Lời thoại đã thay đổi sau khi bắt đầu. Hãy lưu và tạo phiên mới.')
            if (self.config.get('dialogue_source_sha256') and
                    source['sha256'] != self.config['dialogue_source_sha256']):
                raise ValueError('Lời thoại đã sửa thuộc video khác. Chọn lại đúng video hoặc bản lời.')
            cues = read_vietnamese_srt(supplied)
            if hash_file(supplied) != digest:
                raise ValueError('SRT Việt đã thay đổi trong lúc đọc.')
            if vietnamese.exists():
                if hash_file(vietnamese) != digest:
                    raise ValueError('SRT Việt không khớp phiên đang tiếp tục.')
            else:
                with vietnamese.open('xb') as file:
                    file.write(Path(supplied).read_bytes())
            if hash_file(vietnamese) != digest:
                raise ValueError('Chưa sao chép được SRT Việt nguyên vẹn.')
            self.emit('translation', 'Dùng nguyên lời trong SRT Việt, không gửi dịch.', 45)
        else:
            if not source['has_audio']:
                raise ValueError('Video không có âm thanh. Chọn SRT Việt có sẵn để tạo lời đọc.')
            from teodub.asr import transcribe
            from teodub.codex_translation import translate_chinese_srt
            chinese = folder/'chinese.srt'
            transcribe(source['path'], chinese, cancel=self.cancel, progress=self.progress('recognition', 15, 35),
                       asr_model=self.config.get('asr_model', 'auto'))
            translate_chinese_srt(chinese, vietnamese, cancel=self.cancel,
                                 resume_incomplete=self.resuming,
                                 model=self.config.get('translation_model', 'default'),
                                 reasoning_effort=self.config.get('translation_effort', 'medium'),
                                 progress=lambda message: self.emit('translation', message, 40))
            digest = hash_file(vietnamese)
            cues = read_vietnamese_srt(vietnamese)
            if hash_file(vietnamese) != digest:
                raise ValueError('Bản dịch thay đổi trong lúc đọc; chưa tạo giọng.')
            cues = _end_with_picture(cues, source['duration_s'])
        if any(c['end_ms'] > source['duration_s']*1000+200 for c in cues):
            raise ValueError('SRT dài hơn video đã chọn. Chọn SRT đúng video trước khi tạo giọng.')
        self.save(captions=dict(path=str(vietnamese), sha256=digest, count=len(cues)))
        return vietnamese, cues

    def completed_outputs(self):
        rendered = self.state.get('rendered')
        if rendered:
            for key, path in rendered['outputs'].items():
                if hash_file(path) != rendered['hashes'][key]:
                    raise ValueError('Thành phẩm đã thay đổi; không ghi đè hoặc báo hoàn tất sai.')
            return rendered['outputs']
        # Renderer publishes MP4 last. A complete directory may survive an app crash.
        active = self.state.get('render_directory')
        if active:
            output = Path(active)
            quality_path, plan_path = output/'quality.json', output/'render-plan.json'
            if quality_path.exists() and plan_path.exists():
                quality = _read(quality_path)
                videos, subtitles = list(output.glob('*.mp4')), list(output.glob('*.srt'))
                if (quality.get('technical_pass') is True
                        and len(videos) == len(subtitles) == 1 and hash_file(videos[0]) == quality.get('video_sha256')
                        and hash_file(subtitles[0]) == quality.get('subtitles_sha256')
                        and hash_file(plan_path) == quality.get('render_plan_sha256')):
                    if quality.get('source_sha256') != self.state['source']['sha256']:
                        raise ValueError('Thành phẩm không khớp video của phiên.')
                    outputs = dict(video=str(videos[0]), subtitles=str(subtitles[0]), quality=str(quality_path), plan=str(plan_path))
                    self.record_outputs(outputs)
                    return outputs
        return None

    def record_outputs(self, outputs):
        self.save(rendered=dict(outputs=outputs, hashes={key: hash_file(path) for key, path in outputs.items()}))

    def verify_wording_snapshot(self):
        """Check frozen evidence before reuse; never refresh a changed hash."""
        from teodub.wording import validate_wording, verify_wording, _report_path
        settings = validate_wording(self.config)
        saved = self.state.get('wording')
        if saved is None:
            if settings['enabled'] and self.state.get('rendered'):
                raise ValueError('Thành phẩm thiếu chứng cứ bảng thay từ của phiên; không tự nhận lại.')
            return
        try:
            expected = (self.directory/'captions'/'vietnamese-wording.srt').resolve()
            raw = self.state['captions']
            if (not settings['enabled'] or Path(saved['path']).resolve() != expected or
                    Path(saved['report']).resolve() != _report_path(expected) or
                    hash_file(saved['path']) != saved['sha256'] or
                    hash_file(saved['report']) != saved['report_sha256'] or
                    hash_file(raw['path']) != raw['sha256']):
                raise ValueError('binding')
            report = verify_wording(expected, raw['path'], self.config)
            if report['settings_sha256'] != saved['settings_sha256'] or report['cue_count'] != raw['count']:
                raise ValueError('settings')
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            raise ValueError('Lời thay từ, bản gốc hoặc chứng cứ của phiên đã thay đổi. Không ghi đè; tạo phiên mới nếu đổi bảng.') from None

    def ensure_review_notes(self, outputs):
        """Optional reading notes must not hide a verified, completed video."""
        if self.config.get('subtitles'):
            return
        notes = Path(outputs['video']).parent/'Ghi-chu-ban-dich.txt'
        if notes.exists():
            return
        try:
            from teodub.codex_translation import write_review_notes
            vietnamese = self.state['captions']['path']
            wording = self.state.get('wording')
            wording_options = {}
            if wording:
                from teodub.wording import verify_wording
                if hash_file(wording['report']) != wording['report_sha256']:
                    raise ValueError('Chứng cứ thay từ đã thay đổi.')
                verify_wording(wording['path'], vietnamese, self.config)
                wording_options['wording_path'] = wording['path']
            speech = self.state.get('speech_plan')
            if speech:
                if hash_file(speech['path']) != speech['sha256']:
                    raise ValueError('Kế hoạch lời đọc đã thay đổi.')
                plan = _read(speech['path'])
                write_review_notes(vietnamese, notes, outputs['subtitles'], source_groups=plan['groups'], **wording_options)
            else:
                write_review_notes(vietnamese, notes, outputs['subtitles'], **wording_options)
        except Exception as exc:
            self.emit('review_warning', 'Video đã đủ lời đọc; chưa lưu được ghi chú bản dịch: '+str(exc))

    def execute(self):
        self.bind()
        basename = self.config.get('campaign_basename')
        if basename is not None and not re.fullmatch(r'Video [1-9]\d*_\d{2}-\d{2}-\d{4}', str(basename)):
            raise ValueError('Tên video trong chiến dịch không hợp lệ.')
        if self.config.get('mode') == 'download':
            source = self.acquire()
            return dict(status='completed', outputs=dict(source=source['path']),
                        message='Video gốc đã sẵn sàng. Bấm Tải video và dịch Sub để chuẩn bị lời Việt.')
        from teodub.ai_models import validate_models
        validate_models(self.config)
        if self.config.get('mode') == 'captions':
            source = self.acquire()
            saved = self.state.get('captions')
            if saved:
                vietnamese = Path(saved['path'])
                if hash_file(vietnamese) != saved['sha256']:
                    raise ValueError('Bản lời đã thay đổi sau khi chuẩn bị. Hãy mở một bản SRT riêng.')
                cues = read_vietnamese_srt(vietnamese)
            else:
                vietnamese, cues = self.captions(source)
            return dict(status='completed', outputs=dict(subtitles=str(vietnamese)),
                        cue_count=len(cues), message='Đã có lời Việt. Bạn có thể Xem / sửa lời thoại rồi bấm Lồng tiếng và xuất video.')
        from teodub.subtitle_style import validate_subtitle_style
        subtitle_options = validate_subtitle_style(self.config)
        from teodub.wording import validate_wording
        wording_settings = validate_wording(self.config)
        self.verify_wording_snapshot()
        from teodub.tts import VOICES, selection_options, validate_selection
        voice = self.config.get('voice', VOICES[0])
        voice_options = selection_options(self.config)
        validate_selection(voice, **voice_options)
        from teodub.audio_mix import validate_audio_mix
        audio_options = validate_audio_mix(self.config) if self.config.get('mode', 'full') == 'full' else {}
        from teodub.subtitle_clean import validate_clean, preflight as clean_preflight, clean_video, verify_clean_source
        clean_options = validate_clean(self.config) if self.config.get('mode', 'full') == 'full' else dict(enabled=False)
        speech_cache = self.directory/('preview-cache' if self.config.get('mode') == 'preview' else 'voice')
        if (voice_options['provider'] == 'capcut' and not self.state.get('rendered')
                and not (speech_cache/'manifest.json').exists()):
            from teodub.capcut_tts import preflight
            preflight(voice=voice, cancel=self.cancel, progress=self.progress('voice_connection', 0, 0))
        if self.config.get('mode', 'full') == 'preview':
            from teodub.tts import preview_voice, validate_audio
            audio = Path(preview_voice(voice, self.directory/'preview-cache', cancel=self.cancel,
                                     progress=self.progress('voice', 0, 95), **voice_options))
            target = self.directory/'preview.wav'
            if target.exists():
                if hash_file(target) != hash_file(audio):
                    raise ValueError('Giọng thử đã thay đổi.')
            else:
                with target.open('xb') as file, audio.open('rb') as original:
                    shutil.copyfileobj(original, file)
            measured = validate_audio(target)
            label = 'CapCut trực tuyến' if voice_options['provider'] == 'capcut' else 'trên máy'
            return dict(status='completed', outputs=dict(audio=str(target)),
                        message=f'Đã tạo giọng nghe thử {label} ({measured["duration_s"]:.1f} giây).')
        source = self.acquire()
        outputs = self.completed_outputs()
        if outputs:
            self.ensure_review_notes(outputs)
            return dict(status='completed', outputs=outputs, cue_count=self.state['captions']['count'],
                        speech_group_count=self.state.get('speech_plan', {}).get('group_count', self.state['captions']['count']),
                        human_review_required=True, message='Thành phẩm đã hoàn tất và được kiểm tra lại.')
        speed = self.config.get('speed', .9)
        verify_clean_source(source['path'], clean_options)
        if clean_options['enabled'] and not (self.directory/'subtitle-clean'/'clean.json').exists():
            self.emit('subtitle_clean', 'Đang kiểm tra bộ xóa chữ và GPU trước khi dịch/tạo giọng.', 2)
            clean_preflight()
        if speed not in (.9, 1.0):
            raise ValueError('Chọn tốc độ hình 0,9× hoặc 1×.')
        vietnamese, cues = self.captions(source)
        if wording_settings['enabled']:
            from teodub.wording import adapt_subtitles
            adapted = adapt_subtitles(vietnamese, self.directory/'captions'/'vietnamese-wording.srt',
                                      self.config, cancel=self.cancel)
            cues = read_vietnamese_srt(adapted['output'])
            if not self.config.get('subtitles'):
                cues = _end_with_picture(cues, source['duration_s'])
            if (hash_file(adapted['output']) != adapted['output_sha256'] or
                    hash_file(adapted['report']) != adapted['report_sha256'] or
                    hash_file(vietnamese) != self.state['captions']['sha256']):
                raise ValueError('Bản thay từ hoặc chứng cứ đã thay đổi trong lúc đọc; chưa tạo giọng.')
            self.save(wording=dict(path=adapted['output'], sha256=adapted['output_sha256'],
                                   report=adapted['report'], report_sha256=adapted['report_sha256'],
                                   settings_sha256=adapted['settings_sha256']))
            self.emit('wording', f'Thay từ theo bảng đã chọn: {len(adapted["changes"])} dòng thay đổi. Giữ bản dịch gốc để đối chiếu.', 45)
        from teodub.speech_plan import build_speech_plan, validate_style
        reading_style = voice_options['reading_style']
        settings = validate_style(reading_style)
        spoken_cues = cues
        if reading_style != 'legacy':
            plan = build_speech_plan(cues, reading_style)
            plan_path = self.directory/'speech-plan.json'
            if plan_path.exists():
                if _read(plan_path) != plan:
                    raise ValueError('Kế hoạch lời đọc đã thay đổi; bắt đầu phiên mới, không trộn cách đọc.')
            else:
                atomic_json(plan_path, plan)
            self.save(speech_plan=dict(path=str(plan_path), sha256=hash_file(plan_path),
                                       source_count=len(cues), group_count=len(plan['groups'])))
            spoken_cues = [{key: group[key] for key in ('start_ms', 'end_ms', 'text')} for group in plan['groups']]
            self.emit('speech_plan', f'{settings["label"]}: {len(cues)} dòng lời thành {len(spoken_cues)} cụm đọc, giữ đủ nội dung.', 45)
        from teodub.tts import synthesize
        clips = synthesize(spoken_cues, voice, self.directory/'voice', cancel=self.cancel,
                           progress=self.progress('voice', 45, 75), **voice_options)
        check_cancel(self.cancel)
        if len(clips) != len(spoken_cues):
            raise ValueError('Chưa có đủ lời đọc cho mọi câu; chưa xuất video.')
        if hash_file(source['path']) != source['sha256'] or hash_file(vietnamese) != self.state['captions']['sha256']:
            raise ValueError('Video hoặc phụ đề đã thay đổi trong lúc tạo giọng; chưa ghép thành phẩm.')
        wording = self.state.get('wording')
        if wording:
            from teodub.wording import verify_wording
            if (hash_file(wording['path']) != wording['sha256'] or
                    hash_file(wording['report']) != wording['report_sha256']):
                raise ValueError('Lời thay từ hoặc chứng cứ đã thay đổi trong lúc tạo giọng; chưa ghép thành phẩm.')
            verify_wording(wording['path'], vietnamese, self.config)
        speech_plan = self.state.get('speech_plan')
        if speech_plan and hash_file(speech_plan['path']) != speech_plan['sha256']:
            raise ValueError('Kế hoạch lời đọc đã thay đổi trong lúc tạo giọng; chưa ghép thành phẩm.')
        from teodub.render import clear_leftover_work, render_video
        cleaned = None
        if clean_options['enabled']:
            self.emit('subtitle_clean', 'Đang xóa vùng chữ gốc bằng GPU; Sub Việt sẽ được chèn sau.', 75)
            cleaned = clean_video(source['path'], self.config, self.directory/'subtitle-clean',
                                  cancel=self.cancel, progress=self.progress('subtitle_clean', 75, 85))
        output_root = Path(self.config['output_dir'])
        if not output_root.is_absolute():
            raise ValueError('Chọn thư mục thành phẩm đầy đủ.')
        name = self.state.get('output_name') or self.config.get('campaign_basename') or (_safe_title(Path(source['path']).stem)+'_'+datetime.now().strftime('%d-%m-%Y')+'_'+self.directory.name[:8])
        name = _bounded_output_name(output_root, name, self.directory)
        if self.state.get('render_directory'):
            # A failed render of this job may have left its temporary folder (21/10: a locked file after sleep).
            clear_leftover_work(self.state['render_directory'])
        destination = output_root/name
        attempt = 1
        while destination.exists():
            attempt += 1
            destination = output_root/(name+f'_lan-{attempt}')
        self.save(output_name=name, render_directory=str(destination.resolve()))
        outputs = render_video(source['path'], spoken_cues, clips, destination, speed=speed, **audio_options,
                               gap_ms=settings['gap_ms'], **subtitle_options,
                               **({'cleaned_picture': cleaned} if cleaned else {}),
                               cancel=self.cancel, progress=self.progress('render', 85 if cleaned else 75, 99))
        if _read(outputs['quality']).get('source_sha256') != source['sha256']:
            raise ValueError('Thành phẩm không khớp video nguồn đã nhận dạng; chưa báo hoàn tất.')
        self.record_outputs(outputs)
        self.ensure_review_notes(outputs)
        subtitle_message = ('Sub Việt đã ghi trực tiếp vào hình' if subtitle_options['burn_subtitles']
                            else 'phụ đề Việt có thể bật/tắt')
        subtitle_message += ('; đã chạy GPU xóa vùng Sub gốc' if cleaned else '; KHÔNG chạy xóa Sub gốc')
        return dict(status='completed', outputs=outputs, cue_count=len(cues), speech_group_count=len(spoken_cues),
                    human_review_required=True,
                    message=f'Đã xuất đủ nội dung {len(cues)} dòng lời: video lồng tiếng ({subtitle_message}) + một SRT Việt. Hãy nghe duyệt nội dung.')


def run(config):
    job = Job(config)
    with _job_lock(job.directory):
        job.state = _read(job.state_path) if job.state_path.exists() else {}
        atomic_json(job.directory/'result.json', dict(status='running', message='Đang xử lý.'))
        try:
            result = job.execute()
            job.emit('completed', result['message'], 100)
        except Exception as exc:
            cancelled = isinstance(exc, Cancelled) or job.cancel.is_set()
            result = dict(status='cancelled' if cancelled else 'failed', message=str(exc) or type(exc).__name__,
                          directory=str(job.directory), resumable=True)
            job.emit(result['status'], result['message'])
            if not cancelled:
                with (job.directory/'error.log').open('a', encoding='utf-8') as file:
                    traceback.print_exc(file=file)
        atomic_json(job.directory/'result.json', result)
        if (result.get('status') == 'completed' and config.get('mode', 'full') == 'full'
                and (result.get('outputs') or {}).get('video')):
            from teodub.drive_delivery import upload_completed_video

            def delivery_progress(event):
                message = 'Google Drive: '+str(event.get('message') or '')
                percent = event.get('percent')
                if event.get('state') == 'uploading' and type(percent) in (int, float):
                    message += f' ({max(0, min(100, int(percent)))}%)'
                job.emit('google_drive', message)

            try:
                delivery = upload_completed_video(config, result, progress=delivery_progress, cancel=job.cancel)
            except Exception:
                delivery = dict(state='failed', message='Chưa lưu được video lên Google Drive. Video đã xuất trên máy được giữ; có thể tiếp tục tải.')
            if delivery is not None:
                result = dict(result, google_drive=delivery)
                atomic_json(job.directory/'result.json', result)
        return result


def main(args=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    arguments = parser.parse_args(args)
    config = _read(arguments.config)
    try:
        protect_worker()
        if config.get('execution_lock'):
            execution = Path(config['execution_lock']).resolve()
            execution.mkdir(parents=True, exist_ok=True)
            with _job_lock(execution):
                result = run(config)
        else:
            result = run(config)
        return 0 if result['status'] == 'completed' else 2
    except Exception as exc:
        # Do not replace another active worker's result when its lock is held.
        print('Chưa chạy: '+str(exc), flush=True)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
