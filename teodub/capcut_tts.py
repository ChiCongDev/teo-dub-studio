"""CapCut's official TTS website in an isolated, user-owned browser profile.

No community device identities, copied cookies, signing code or private APIs are
used. Account entry and purchases are never automated. The synthesis branch is
experimental until tested with the user's signed-in profile; it requires an
explicit zero-price indication adjacent to Generate before submitting anything.
"""
import json
import os
from pathlib import Path
import re
import subprocess
import time
import uuid

from teodub.common import atomic_json, check_cancel, hash_file

MODEL_ID = 'CapCut-official-web-TTS-guarded-v1'
SERVICE_URL = 'https://www.capcut.com/magic-tools/text-to-speech'
VOICE_IDS = {
    'Cô Gái Hoạt Ngôn': 'BV074_streaming',
    'Thanh Niên Tự Tin': 'BV075_streaming',
}
RESOURCE_IDS = {
    'BV074_streaming': '7102355709945188865',
    'BV075_streaming': '7102355803792740865',
}
VOICES = tuple(VOICE_IDS)


class CapCutAccessRequired(ValueError):
    def __init__(self, message, code='capcut_not_ready'):
        super().__init__(message)
        self.code = code


def validate_voice(voice):
    """Validate the two supported display names without contacting any service."""
    if not isinstance(voice, str) or voice not in VOICE_IDS:
        raise ValueError('Chọn Cô Gái Hoạt Ngôn hoặc Thanh Niên Tự Tin cho CapCut trực tuyến.')
    return VOICE_IDS[voice]


def profile_directory():
    return Path(os.environ.get('LOCALAPPDATA', Path.home()/'AppData'/'Local'))/'TeoDubStudio'/'capcut-voice-browser'


def _browser_executable():
    roots = [Path(os.environ.get(key, fallback)) for key, fallback in
             (('PROGRAMFILES(X86)', 'C:/Program Files (x86)'), ('PROGRAMFILES', 'C:/Program Files'))]
    for relative in ('Microsoft/Edge/Application/msedge.exe', 'Google/Chrome/Application/chrome.exe'):
        for root in roots:
            candidate = root/relative
            if candidate.is_file():
                return candidate
    raise CapCutAccessRequired('Cần cài Microsoft Edge hoặc Google Chrome để dùng CapCut trực tuyến.', 'browser_missing')


def open_capcut_login():
    """Open the dedicated interactive profile and return immediately.

    The user logs in themselves, then closes this window before a worker runs.
    No browser profile belonging to another application is inspected or copied.
    """
    executable = _browser_executable()
    profile = profile_directory()
    profile.mkdir(parents=True, exist_ok=True)
    process = subprocess.Popen([str(executable), '--no-first-run', '--new-window',
                                '--user-data-dir='+str(profile), SERVICE_URL],
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
    return dict(url=SERVICE_URL, profile=str(profile), pid=process.pid)


def _notify(progress, message):
    if progress:
        progress(message, percent=None)


def _object(value):
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            data = json.loads(value)
            return data if isinstance(data, dict) else {}
        except ValueError:
            pass
    return {}


def _catalog_voices(payload):
    """Keep only public voice identity and price flags from the official catalog."""
    result = {}

    def visit(item):
        if isinstance(item, dict):
            common = item.get('common_attr')
            if isinstance(common, dict):
                extra = _object(common.get('extra'))
                tone = _object(extra.get('tonetype'))
                identity = tone.get('voice_type')
                if identity in RESOURCE_IDS and str(common.get('id')) == RESOURCE_IDS[identity]:
                    title = common.get('title')
                    if isinstance(title, str) and title.strip():
                        result[identity] = dict(voice_id=identity, resource_id=RESOURCE_IDS[identity],
                                                title=title, is_vip=extra.get('is_vip'))
            for value in item.values():
                if isinstance(value, (dict, list)):
                    visit(value)
        elif isinstance(item, list):
            for value in item:
                visit(value)
    visit(payload)
    return result


def _check_access_text(body):
    lower = body.casefold()
    if any(text in lower for text in ('captcha', 'xác minh bạn là con người', 'verify you are human')):
        raise CapCutAccessRequired('CapCut yêu cầu xác minh. Mở CapCut trực tuyến để tự hoàn tất xác minh.', 'verification_required')
    if (any(text in lower for text in ('chào mừng bạn đến với capcut', 'tiếp tục bằng google',
                                       'continue with google', 'welcome to capcut')) or
            any(line.strip() in ('đăng nhập', 'log in', 'sign in') for line in lower.splitlines())):
        raise CapCutAccessRequired('Cần đăng nhập CapCut: bấm Mở CapCut / đăng nhập, đăng nhập trong cửa sổ riêng rồi đóng cửa sổ đó và thử lại. Chưa tạo giọng hay dùng tín dụng.', 'login_required')
    if any(text in lower for text in ('hãy nâng cấp để nhận điểm tín dụng', 'upgrade to get credits',
                                      'không đủ tín dụng', 'insufficient credits', 'purchase credits', 'mua tín dụng')):
        raise CapCutAccessRequired('CapCut đang yêu cầu tín dụng hoặc nâng cấp. Tèo chưa bấm Tạo và không tự thanh toán. Có thể chọn Giọng từ mẫu để chạy miễn phí trên máy.', 'credits_required')


def _zero_price_button_text(text):
    """Require price text on the Generate control, never a balance of 0 credits."""
    lower = ' '.join(text.casefold().split())
    if any(word in lower for word in ('còn lại', 'remaining', 'balance', 'số dư', 'nâng cấp', 'upgrade',
                                      'mua', 'purchase', 'trial', 'dùng thử', 'không miễn phí',
                                      'not free', 'premium', 'trả phí', 'paid')):
        return False
    if not re.search(r'\b(?:tạo|generate)\b', lower):
        return False
    prices = re.findall(r'(\d+(?:[.,]\d+)?)\s*(?:điểm tín dụng|tín dụng|credits?)', lower)
    if prices:
        return all(float(price.replace(',', '.')) == 0 for price in prices)
    return bool(re.search(r'\b(?:miễn phí|free)\b', lower))


class _WebSession:
    def __init__(self, cancel, progress):
        self.cancel, self.progress = cancel, progress
        self.catalog = {}
        self.playwright = self.context = self.page = None

    def __enter__(self):
        check_cancel(self.cancel)
        from playwright.sync_api import sync_playwright
        self.playwright = sync_playwright().start()
        profile = profile_directory()
        profile.mkdir(parents=True, exist_ok=True)
        try:
            self.context = self.playwright.chromium.launch_persistent_context(
                str(profile), executable_path=str(_browser_executable()), headless=True,
                locale='vi-VN', accept_downloads=True)
            self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
            self.page.set_default_timeout(8000)
            self.page.on('response', self._response)
            _notify(self.progress, 'Đang kiểm tra CapCut trực tuyến bằng phiên đăng nhập riêng…')
            self.page.goto(SERVICE_URL, wait_until='domcontentloaded', timeout=25000)
            deadline = time.monotonic()+20
            while time.monotonic()<deadline:
                check_cancel(self.cancel)
                body = self.page.locator('body').inner_text()
                _check_access_text(body)
                if self.catalog:
                    # Let the account gate render before considering the page ready.
                    self.page.wait_for_timeout(600)
                    _check_access_text(self.page.locator('body').inner_text())
                    return self
                self.page.wait_for_timeout(250)
            raise CapCutAccessRequired('CapCut chưa tải được danh mục giọng. Mở CapCut trực tuyến để kiểm tra mạng hoặc thông báo trên trang.', 'catalog_unavailable')
        except Exception as error:
            self.__exit__(None, None, None)
            if isinstance(error, CapCutAccessRequired):
                raise
            check_cancel(self.cancel)
            raise CapCutAccessRequired('Chưa mở được phiên CapCut riêng. Hãy đóng cửa sổ CapCut trực tuyến đang mở rồi thử lại; không cần đóng trình duyệt thường.', 'browser_unavailable') from error

    def _response(self, response):
        if response.status == 200 and '/artist/v1/' in response.url:
            try:
                self.catalog.update(_catalog_voices(response.json()))
            except Exception:
                pass

    def __exit__(self, *args):
        if self.context is not None:
            self.context.close()
            self.context = None
        if self.playwright is not None:
            self.playwright.stop()
            self.playwright = None

    def check_voice(self, voice):
        identity = validate_voice(voice)
        data = self.catalog.get(identity)
        if not data:
            raise CapCutAccessRequired(f'CapCut chưa cung cấp giọng {voice} trong phiên hiện tại. Không thay bằng giọng khác.', 'voice_unavailable')
        if data.get('is_vip') is not False:
            raise CapCutAccessRequired(f'Chưa xác nhận giọng {voice} là giọng không yêu cầu Pro. Tèo chưa tạo giọng.', 'voice_entitlement_unknown')
        return data


def preflight(*, voice=None, cancel=None, progress=None):
    """Worker-only, non-billable login/catalog check. Never submits text or TTS."""
    if voice is not None:
        validate_voice(voice)
    requested = (voice,) if voice is not None else VOICES
    with _WebSession(cancel, progress) as session:
        voices = {name: session.check_voice(name) for name in requested}
        check_cancel(cancel)
        return dict(authenticated=True, catalog_available=True, voices=voices,
                    synthesis_verified=False, requires_zero_price_check=True,
                    message='Đã đọc danh mục giọng. Chi phí sẽ được kiểm tra trước mỗi lần tạo; chưa gửi văn bản.')


def _normalize_download(source, target, cancel):
    """Decode the complete download to mono PCM16/48 kHz and publish once."""
    import av
    import numpy as np
    import soundfile as sf
    import wave
    target = Path(target).resolve()
    if target.exists():
        raise ValueError('Tệp giọng đích đã tồn tại; chưa ghi đè.')
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name+'.'+uuid.uuid4().hex+'.pending.wav')
    try:
        with av.open(str(source)) as media, wave.open(str(temporary), 'wb') as output:
            if len(media.streams.audio) != 1:
                raise ValueError('Audio CapCut phải có đúng một luồng giọng.')
            output.setparams((1, 2, 48000, 0, 'NONE', 'not compressed'))
            converter = av.AudioResampler(format='s16', layout='mono', rate=48000)
            samples = 0
            for frame in media.decode(audio=0):
                check_cancel(cancel)
                for converted in converter.resample(frame):
                    output.writeframesraw(converted.to_ndarray().tobytes())
                    samples += converted.samples
                    if samples > 48000*600:
                        raise ValueError('Audio CapCut dài quá mức cho một câu; cần kiểm tra kết quả.')
            for converted in converter.resample(None):
                output.writeframesraw(converted.to_ndarray().tobytes())
        audio, rate = sf.read(str(temporary), dtype='float32')
        if rate != 48000 or audio.ndim != 1 or len(audio)<3840 or not np.isfinite(audio).all() or float(np.sqrt(np.mean(audio*audio)))<.0001:
            raise ValueError('CapCut chưa trả về giọng có âm thanh hợp lệ.')
        check_cancel(cancel)
        # Hard-link publication never replaces an existing target on Windows/POSIX.
        os.link(temporary, target)
        return dict(path=str(target), sha256=hash_file(target), duration_s=len(audio)/rate,
                    samples=len(audio), sample_rate=rate)
    finally:
        temporary.unlink(missing_ok=True)


def _recover_completed_audio(marker, target, text, voice, identity, cancel):
    """Reuse only a completed, identical request and fully verified local WAV."""
    import numpy as np
    import soundfile as sf
    try:
        if marker.is_symlink() or not marker.is_file() or not target.is_file():
            raise ValueError('Missing completion evidence')
        record = json.loads(marker.read_text(encoding='utf-8'))
        expected = dict(status='completed', text=text, voice=voice, voice_id=identity,
                        model=MODEL_ID, target=str(target))
        if not isinstance(record, dict) or any(record.get(key) != value for key, value in expected.items()):
            raise ValueError('Request identity changed or submission incomplete')
        saved = record.get('audio')
        if not isinstance(saved, dict):
            raise ValueError('Missing audio evidence')
        check_cancel(cancel)
        before = hash_file(target)
        info = sf.info(str(target))
        if (info.format != 'WAV' or info.subtype != 'PCM_16' or info.channels != 1 or
                info.samplerate != 48000 or not 3840 <= info.frames <= 48000*600):
            raise ValueError('Audio is not a normalized completed result')
        audio, rate = sf.read(str(target), dtype='float32')
        check_cancel(cancel)
        if (len(audio) != info.frames or audio.ndim != 1 or not np.isfinite(audio).all() or
                float(np.sqrt(np.mean(audio*audio))) < .0001 or hash_file(target) != before):
            raise ValueError('Audio changed or is incomplete')
        measured = dict(path=str(target), sha256=before, duration_s=len(audio)/rate,
                        samples=len(audio), sample_rate=rate)
        if (saved != measured or type(saved.get('samples')) is not int or
                type(saved.get('sample_rate')) is not int or
                type(saved.get('duration_s')) not in (float, int)):
            raise ValueError('Audio metadata changed')
        check_cancel(cancel)
        return measured
    except (OSError, ValueError, RuntimeError, TypeError) as error:
        raise CapCutAccessRequired('Lần tạo giọng này đã có dấu vết nhưng chưa xác minh được kết quả hoàn tất đúng lời và giọng. Cần kiểm tra kết quả CapCut trước khi gửi lại để tránh tạo trùng.', 'submission_already_recorded') from error


def synthesize_to_file(text, voice, target, *, cancel=None, progress=None):
    """Guarded website flow; no submission without a verified zero-price button.

    Any failure after clicking Generate is marked ambiguous and must be inspected
    manually; retrying this target never submits another potentially billable task.
    """
    identity = validate_voice(voice)
    check_cancel(cancel)
    if not isinstance(text, str) or not text.strip():
        raise ValueError('Chưa có lời Việt để gửi CapCut.')
    target = Path(target).resolve()
    marker = target.with_name(target.name+'.capcut-request.json')
    if target.exists() or marker.exists():
        result = _recover_completed_audio(marker, target, text, voice, identity, cancel)
        _notify(progress, 'Dùng lại giọng CapCut đã hoàn tất và kiểm chứng; không gửi tạo lại.')
        return result
    with _WebSession(cancel, progress) as session:
        details = session.check_voice(voice)
        page = session.page
        skip = page.get_by_text('Bỏ qua', exact=True)
        if skip.count() and skip.first.is_visible():
            skip.first.click()
        page.get_by_text('Tiếng Việt', exact=True).first.click()
        page.get_by_text(details['title'], exact=True).click()
        editor = page.locator('[contenteditable="true"]')
        if editor.count() != 1:
            raise CapCutAccessRequired('Giao diện nhập lời CapCut đã thay đổi; chưa gửi tạo giọng.', 'ui_changed')
        editor.fill(text)
        _check_access_text(page.locator('body').inner_text())
        generate = page.get_by_role('button', name=re.compile(r'^(?:Tạo|Generate)(?:\s|$)'))
        if generate.count() != 1 or not _zero_price_button_text(generate.inner_text()):
            raise CapCutAccessRequired('CapCut chưa hiển thị rõ lượt tạo này miễn phí hoặc 0 tín dụng trên nút Tạo. Tèo chưa gửi tạo giọng và không tự chấp nhận chi phí. Mở CapCut trực tuyến để kiểm tra.', 'zero_price_unverified')
        download = page.get_by_role('button', name=re.compile(r'^(?:Tải xuống|Download)$'))
        if download.count():
            raise CapCutAccessRequired('Trang đang có kết quả cũ. Mở một lượt tạo trống trong CapCut trước khi chạy lại.', 'old_result_present')
        check_cancel(cancel)
        marker.parent.mkdir(parents=True, exist_ok=True)
        record = dict(status='submission_started', voice=voice, voice_id=identity, text=text,
                      model=MODEL_ID, target=str(target), started_at=time.time())
        atomic_json(marker, record)
        try:
            generate.click()
            _notify(progress, 'Đã gửi một lượt tạo CapCut miễn phí; đang chờ kết quả…')
            deadline = time.monotonic()+120
            while time.monotonic()<deadline:
                check_cancel(cancel)
                _check_access_text(page.locator('body').inner_text())
                if download.count()==1 and download.is_visible() and download.is_enabled():
                    break
                page.wait_for_timeout(250)
            else:
                raise CapCutAccessRequired('Chưa xác nhận được kết quả CapCut. Không tự gửi lại yêu cầu.', 'submission_uncertain')
            check_cancel(cancel)
            with page.expect_download(timeout=30000) as event:
                download.click()
            downloaded = event.value
            source = downloaded.path()
            if source is None:
                raise ValueError('CapCut chưa tải được tệp audio.')
            result = _normalize_download(source, target, cancel)
            atomic_json(marker, dict(record, status='completed', audio=result))
            return result
        except BaseException:
            atomic_json(marker, dict(record, status='submission_uncertain'))
            raise
