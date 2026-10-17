"""Deterministic speech phrases, preserving every original caption word.

Only unfinished neighbouring fragments are joined. Merge limits never truncate
an existing long caption: an oversized original remains a standalone phrase.
No inferred emotions, rewritten wording, or extra punctuation are introduced.
"""
import hashlib
import json
import re

PLAN_VERSION = 'speech-plan-v1'
STYLES = {
    'legacy': {'label': 'Theo bản cũ', 'speech_speed': 1.0, 'gap_ms': 100},
    'conversation': {'label': 'Trò chuyện tự nhiên', 'speech_speed': .98, 'gap_ms': 180},
    'story': {'label': 'Kể chuyện chậm rãi', 'speech_speed': .92, 'gap_ms': 300},
    'advertising': {'label': 'Quảng cáo năng động', 'speech_speed': 1.06, 'gap_ms': 120},
}
MAX_CUES = 3
MAX_CHARACTERS = 220
MAX_SPAN_MS = 12000
MAX_GAP_MS = 600

_TERMINAL = re.compile(r'[.!?…。！？][\s”’\"»\)\]\}]*$')
_DIALOGUE = re.compile(r'(?m)^\s*(?:[-–—]\s*\S|[“\"«]|[^\n:：]{1,32}[:：](?:\s*\S|$)|\d+[.)]\s+\S)')


def validate_style(style):
    """Return a copy of supported rendering settings, never a shared dict."""
    if not isinstance(style, str) or style not in STYLES:
        raise ValueError('Chọn cách đọc trò chuyện, kể chuyện, quảng cáo hoặc theo bản cũ.')
    return dict(STYLES[style])


def _dialogue_boundary(cue):
    # Speaker information, when available, must never be silently flattened.
    named = any(cue.get(key) not in (None, '') for key in ('speaker', 'speaker_id'))
    marked = cue.get('dialogue') is True or cue.get('new_speaker') is True
    return named or marked or bool(_DIALOGUE.search(cue['text']))


def _validate_cues(cues):
    if not isinstance(cues, (list, tuple)) or not cues:
        raise ValueError('Cần danh sách phụ đề có đầy đủ lời trước khi lập cách đọc.')
    previous_end = 0
    for cue in cues:
        if (not isinstance(cue, dict) or type(cue.get('start_ms')) is not int
                or type(cue.get('end_ms')) is not int
                or cue['start_ms'] < previous_end or cue['end_ms'] <= cue['start_ms']
                or not isinstance(cue.get('text'), str) or not cue['text'].strip()):
            raise ValueError('Phụ đề thiếu lời, sai mốc hoặc chồng thời gian; chưa lập cách đọc.')
        previous_end = cue['end_ms']
    try:
        # Preserve all cue fields in the identity, including speaker markers and
        # original punctuation/line breaks that may affect grouping boundaries.
        encoded = json.dumps(cues, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    except (ValueError, TypeError):
        raise ValueError('Thông tin phụ đề không thể lưu thành cấu hình ổn định.') from None
    return hashlib.sha256(encoded.encode('utf-8')).hexdigest()


def build_speech_plan(cues, style='conversation'):
    """Return source identity and ordered groups with exact source-cue coverage.

    Legacy leaves caption text/times untouched. Other styles normalize whitespace
    and combine fragments only when the preceding text has no sentence ending,
    neither side marks dialogue, the gap is <=600 ms, and merged size stays within
    three cues, 220 characters and 12 seconds of original time.
    """
    settings = validate_style(style)
    source_digest = _validate_cues(cues)
    groups = []
    previous_dialogue = False
    for index, cue in enumerate(cues, 1):
        text = cue['text'] if style == 'legacy' else ' '.join(cue['text'].split())
        dialogue = _dialogue_boundary(cue)
        previous = groups[-1] if groups else None
        combined = (previous['text']+' '+text) if previous else text
        can_join = (style != 'legacy' and previous is not None
                    and not previous_dialogue and not dialogue
                    and not _TERMINAL.search(previous['text'].rstrip())
                    and cue['start_ms']-previous['end_ms'] <= MAX_GAP_MS
                    and len(previous['source_ids']) < MAX_CUES
                    and len(combined) <= MAX_CHARACTERS
                    and cue['end_ms']-previous['start_ms'] <= MAX_SPAN_MS)
        if can_join:
            previous['text'] = combined
            previous['end_ms'] = cue['end_ms']
            previous['source_ids'].append(index)
        else:
            groups.append(dict(start_ms=cue['start_ms'], end_ms=cue['end_ms'], text=text, source_ids=[index]))
        previous_dialogue = dialogue
    return dict(version=PLAN_VERSION, style=style, settings=settings, source_cues_sha256=source_digest,
                source_cue_count=len(cues), groups=groups)
