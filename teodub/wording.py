"""Optional literal wording edits, separate from the verified AI translation.

These are user editing preferences, not platform moderation or copyright rules.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import unicodedata
import uuid

from teodub.common import check_cancel, hash_file
from teodub.srt import write_srt
from teodub.subtitles import read_vietnamese_srt

VERSION = 'vi-wording-v1'
MAX_RULES = 50
MAX_TERM_CHARS = 100
_CJK = re.compile(r'[\u3400-\u9fff\uf900-\ufaff\U00020000-\U000323af]')
# A generic sales-word rule should not damage common technical compounds.
_COMPOUNDS = {
    'bán': ('bán kính', 'bán cầu', 'bán đảo', 'bán dẫn', 'bán tự động'),
    'giá': ('giá trị', 'giá đỡ', 'giá thể'),
}


def _term(value):
    if (not isinstance(value, str) or any(unicodedata.category(c).startswith('C') for c in value)
            or _CJK.search(value)):
        raise ValueError('Từ thay thế phải là chữ Việt/Latin, không chứa xuống dòng hoặc ký tự điều khiển.')
    value = ' '.join(unicodedata.normalize('NFC', value).split())
    if not value or len(value) > MAX_TERM_CHARS:
        raise ValueError('Từ gốc và từ thay phải có nội dung, tối đa 100 ký tự mỗi ô.')
    return value


def _rules(values):
    if not isinstance(values, list) or len(values) > MAX_RULES:
        raise ValueError('Bảng thay từ phải là danh sách, tối đa 50 quy tắc.')
    result, seen = [], set()
    for value in values:
        if not isinstance(value, dict) or set(value) != {'source', 'replacement'}:
            raise ValueError('Mỗi quy tắc cần đúng hai ô: từ gốc và từ thay.')
        source, replacement = _term(value['source']), _term(value['replacement'])
        if '=>' in source or '→' in source:
            raise ValueError('Từ gốc không được chứa dấu phân cách => hoặc →.')
        key = source.casefold()
        if key in seen:
            raise ValueError('Từ gốc bị lặp trong bảng thay từ: ' + source)
        seen.add(key)
        result.append(dict(source=source, replacement=replacement))
    return result


def validate_wording(config):
    value = config.get('wording')
    if value is None and 'wording' not in config:
        return dict(version=VERSION, enabled=False, rules=[])
    if not isinstance(value, dict) or set(value) - {'version', 'enabled', 'rules'}:
        raise ValueError('Cấu hình từ ngữ không hợp lệ.')
    if value.get('version', VERSION) != VERSION or type(value.get('enabled', False)) is not bool:
        raise ValueError('Phiên bản hoặc lựa chọn bật bảng thay từ không hợp lệ.')
    rules = _rules(value.get('rules', []))
    enabled = value.get('enabled', False)
    if enabled and not rules:
        raise ValueError('Đã bật thay từ nhưng chưa có quy tắc. Nhập bảng hoặc tắt lựa chọn này.')
    return dict(version=VERSION, enabled=enabled, rules=rules)


def parse_rule_text(text):
    if not isinstance(text, str):
        raise ValueError('Bảng thay từ phải là văn bản.')
    values = []
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        parts = re.split(r'=>|→', line, maxsplit=1)
        if len(parts) != 2:
            raise ValueError(f'Dòng {number}: nhập theo dạng từ gốc => từ thay.')
        values.append(dict(source=parts[0], replacement=parts[1]))
    return _rules(values)


def format_rule_text(rules):
    return '\n'.join(row['source'] + ' => ' + row['replacement'] for row in _rules(rules))


def _pattern(text):
    return r'(?<!\w)' + re.escape(text).replace(r'\ ', r'\s+') + r'(?!\w)'


def rewrite_text(text, rules):
    if not isinstance(text, str):
        raise ValueError('Nội dung xem thử phải là văn bản.')
    rules = _rules(rules)
    if not rules:
        return text, []
    text = unicodedata.normalize('NFC', text)
    ordered = sorted(rules, key=lambda row: len(row['source']), reverse=True)
    patterns = '|'.join('(?P<rule' + str(i) + '>' + _pattern(row['source']) + ')'
                        for i, row in enumerate(ordered))
    protected = {}
    for word, phrases in _COMPOUNDS.items():
        protected[word] = [match.span() for phrase in phrases
                           for match in re.finditer(_pattern(phrase), text, re.IGNORECASE)]
    changes = []

    def replace(match):
        rule = ordered[int(match.lastgroup[4:])]
        if any(start <= match.start() and match.end() <= end
               for start, end in protected.get(rule['source'].casefold(), [])):
            return match.group()
        original, replacement = match.group(), rule['replacement']
        if original.isupper():
            replacement = replacement.upper()
        elif original.istitle():
            replacement = replacement.title()
        elif original[0].isupper():
            replacement = replacement[:1].upper() + replacement[1:]
        if replacement != original:
            changes.append(dict(source=rule['source'], replacement=replacement, matched=original,
                                start=match.start(), end=match.end()))
        return replacement

    return re.sub(patterns, replace, text, flags=re.IGNORECASE), changes


def _settings_hash(settings):
    return hashlib.sha256(json.dumps(settings, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode('utf-8')).hexdigest()


def _adapt(cues, rules):
    result, changes = [], []
    for index, cue in enumerate(cues, 1):
        text, replacements = rewrite_text(cue['text'], rules)
        result.append(dict(cue, text=text))
        if text != cue['text']:
            changes.append(dict(id=index, before=cue['text'], after=text, replacements=replacements))
    return result, changes


def _report_path(path):
    return Path(path).with_suffix('.wording.json')


def _pending_path(path):
    return Path(path).with_suffix('.wording.pending.json')


def _publish(source, target):
    if os.name == 'nt':
        Path(source).rename(target)
    else:
        os.link(source, target)
        Path(source).unlink()


def _create_json(path, value):
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('x', encoding='utf-8') as file:
            json.dump(value, file, ensure_ascii=False, indent=2)
            file.flush()
            os.fsync(file.fileno())
        _publish(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _verify(report, output, raw_path=None, config=None):
    raw = Path(report['raw_path']).resolve()
    settings = validate_wording(dict(wording=report['settings']))
    if (type(report['version']) is not int or report['version'] != 1 or
            type(report['cue_count']) is not int or report['engine'] != VERSION or not settings['enabled'] or
            settings != report['settings'] or Path(report['output']).resolve() != output or raw == output or
            (raw_path is not None and Path(raw_path).resolve() != raw) or
            (config is not None and validate_wording(config) != settings) or
            report['settings_sha256'] != _settings_hash(settings) or
            hash_file(raw) != report['raw_sha256'] or hash_file(output) != report['output_sha256']):
        raise ValueError('binding')
    raw_cues = read_vietnamese_srt(raw)
    expected, changes = _adapt(raw_cues, settings['rules'])
    if (read_vietnamese_srt(output) != expected or report['cue_count'] != len(raw_cues) or
            _settings_hash(report['changes']) != _settings_hash(changes) or hash_file(raw) != report['raw_sha256'] or
            hash_file(output) != report['output_sha256']):
        raise ValueError('wording changed')


def verify_wording(output_path, raw_path=None, config=None):
    output = Path(output_path).resolve()
    path = _report_path(output)
    try:
        report_hash = hash_file(path)
        report = json.loads(path.read_text(encoding='utf-8'))
        _verify(report, output, raw_path, config)
        if hash_file(path) != report_hash:
            raise ValueError('report changed while reading')
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        raise ValueError('Bản thay từ hoặc chứng cứ đã thay đổi/không khớp. Giữ dữ liệu và bắt đầu phiên mới nếu đổi bảng.') from None
    return dict(report, report=str(path), report_sha256=report_hash)


def adapt_subtitles(raw_path, output_path, config, *, cancel=None):
    settings = validate_wording(config)
    if not settings['enabled']:
        return None
    check_cancel(cancel)
    raw, output = Path(raw_path).resolve(), Path(output_path).resolve()
    if raw == output:
        raise ValueError('Bản thay từ phải lưu riêng, không ghi đè phụ đề/bản dịch gốc.')
    report_path, pending = _report_path(output), _pending_path(output)
    if report_path.exists():
        result = verify_wording(output, raw, config)
        check_cancel(cancel)
        if pending.exists():
            original_report = {k: v for k, v in result.items() if k not in ('report', 'report_sha256')}
            if _settings_hash(json.loads(pending.read_text(encoding='utf-8'))) != _settings_hash(original_report):
                raise ValueError('Chứng cứ thay từ đang ghi dở không khớp; không ghi đè.')
            pending.unlink()
        return result
    if output.exists():
        if not pending.exists():
            raise ValueError('Bản thay từ chưa có chứng cứ, không ghi đè hoặc tự nhận lại.')
        try:
            report = json.loads(pending.read_text(encoding='utf-8'))
            _verify(report, output, raw, config)
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            raise ValueError('Bản thay từ đang ghi dở đã thay đổi; không ghi đè.') from None
        check_cancel(cancel)
        _create_json(report_path, report)
        result = verify_wording(output, raw, config)
        pending.unlink()
        return result
    raw_hash = hash_file(raw)
    cues = read_vietnamese_srt(raw)
    adapted, changes = _adapt(cues, settings['rules'])
    if hash_file(raw) != raw_hash:
        raise ValueError('Phụ đề gốc thay đổi trong lúc thay từ.')
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        write_srt(temporary, adapted)
        read_vietnamese_srt(temporary)
        report = dict(version=1, engine=VERSION, raw_path=str(raw), raw_sha256=raw_hash,
                      output=str(output), output_sha256=hash_file(temporary), settings=settings,
                      settings_sha256=_settings_hash(settings), cue_count=len(cues), changes=changes)
        if pending.exists():
            if _settings_hash(json.loads(pending.read_text(encoding='utf-8'))) != _settings_hash(report):
                raise ValueError('Chứng cứ thay từ đang ghi dở không khớp cấu hình; không ghi đè.')
        else:
            check_cancel(cancel)
            _create_json(pending, report)
        check_cancel(cancel)
        if hash_file(raw) != raw_hash:
            raise ValueError('Phụ đề gốc đã đổi; chưa xuất bản thay từ.')
        _publish(temporary, output)
        check_cancel(cancel)
        _create_json(report_path, report)
    finally:
        temporary.unlink(missing_ok=True)
    result = verify_wording(output, raw, config)
    pending.unlink(missing_ok=True)
    return result
