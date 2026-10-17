"""Recognize public downloader status messages without treating FAQs as errors."""
import re


class ProviderUnavailable(ValueError):
    """A provider's reported failure, as distinct from an unexplained timeout."""

    def __init__(self, message, *, reason, provider='anysave', retry_after_text=None):
        super().__init__(message)
        self.reason = reason
        self.provider = provider
        # Display text supplied by the site, not a calculated promise of recovery.
        self.retry_after_text = retry_after_text


def _status_lines(text):
    for line in str(text or '').splitlines():
        line = ' '.join(line.split()).strip()
        if not line:
            continue
        # A status may have a short alert label. Do not search arbitrary prose:
        # FAQ questions, examples and instructions also mention these phrases.
        yield re.sub(r'^(?:error|oops|sorry)\s*[:!\-–—]\s*', '', line,
                     flags=re.IGNORECASE)


def _starts_status(line, phrases):
    for phrase in phrases:
        if re.match(re.escape(phrase) + r'(?=$|[\s.!,:;—–-])', line, re.IGNORECASE):
            return True
    return False


def classify_anysave(text):
    """Return an explicit AnySave failure, or None for an ordinary page.

    The caller should pass visible status text or visible body text. Matching
    starts of lines keeps instructional paragraphs such as "If you see a daily
    limit reached error ..." from being confused with the current result.
    """
    lines = list(_status_lines(text))
    quota = ('your free web tries are used up for today',
             'your free tries are used up for today',
             'daily limit reached', 'daily download limit reached',
             'you have reached your daily limit')
    for index, line in enumerate(lines):
        if not _starts_status(line, quota):
            continue
        # Restrict extraction to the quota's own nearby status lines. Never
        # include an unrelated page paragraph or a link in the user message.
        nearby = ' '.join(lines[index:index + 3])
        match = re.search(r'\brefreshes in\s+((?:\d+\s*(?:days?|d|hours?|hrs?|h|minutes?|mins?|m|seconds?|secs?|s)\b\s*)+)',
                          nearby, re.IGNORECASE)
        retry_after = match.group(1).strip() if match else None
        message = 'AnySave đã hết lượt tải miễn phí trong ngày.'
        if retry_after:
            message += f' Trang báo làm mới sau {retry_after}.'
        return ProviderUnavailable(message, reason='daily_quota', retry_after_text=retry_after)

    cases = (
        ('verification_required',
         ('verify you are human', 'verify that you are human', 'complete the captcha'),
         'AnySave yêu cầu xác minh. Mở trang để xử lý thủ công; lượt tải này chưa lấy được video.'),
        ('rate_limited',
         ('too many requests', 'rate limit exceeded'),
         'AnySave đang giới hạn số yêu cầu tải. Lượt tải này chưa lấy được video; hãy thử lại sau.'),
        ('parse_failed',
         ('video not found', 'unable to parse', 'failed to parse', 'could not parse'),
         'AnySave chưa xử lý được link này hoặc không tìm thấy video.'),
        ('provider_error',
         ('something went wrong', 'network error', 'failed to fetch', 'request failed',
          'service unavailable'),
         'AnySave báo lỗi xử lý hoặc kết nối. Lượt tải này chưa lấy được video.'),
    )
    for reason, phrases, message in cases:
        if any(_starts_status(line, phrases) for line in lines):
            return ProviderUnavailable(message, reason=reason)
    return None
