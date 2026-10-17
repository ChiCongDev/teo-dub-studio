"""Strict UTF-8 SRT parsing and read-only comparison."""
import re
import unicodedata
from pathlib import Path

TIMING = re.compile(r"^(\d{2,}):([0-5]\d):([0-5]\d),(\d{3}) --> (\d{2,}):([0-5]\d):([0-5]\d),(\d{3})$")


def normalized_text(text):
    return " ".join(unicodedata.normalize("NFC", text).split())


def read_srt(path):
    text = Path(path).read_text(encoding="utf-8-sig")
    return parse_srt(text)


def read_vietnamese_srt(path):
    """Refuse residual Chinese text before narration and final caption import."""
    cues = read_srt(path)
    if any(re.search(r'[\u3400-\u9fff\U00020000-\U0002fa1f]', cue['text']) for cue in cues):
        raise ValueError('Phụ đề Việt còn chữ Trung. Hãy kiểm tra bản dịch trước khi lồng tiếng.')
    return cues


def parse_srt(text):
    if not text.strip():
        raise ValueError("Phụ đề rỗng.")
    cues = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = block.splitlines()
        if len(lines) < 3 or not lines[0].isdigit() or int(lines[0]) != len(cues) + 1:
            raise ValueError("SRT thiếu câu hoặc số thứ tự không liên tục.")
        match = TIMING.fullmatch(lines[1])
        if match is None:
            raise ValueError(f"Mốc SRT không hợp lệ ở câu {len(cues) + 1}.")
        h, m, s, ms, eh, em, es, ems = map(int, match.groups())
        start, end = h * 3600000 + m * 60000 + s * 1000 + ms, eh * 3600000 + em * 60000 + es * 1000 + ems
        content = normalized_text("\n".join(lines[2:]))
        if start >= end or (cues and start < cues[-1]["end_ms"]):
            raise ValueError("SRT có câu đảo mốc hoặc chồng thời gian.")
        if not content:
            raise ValueError("SRT có câu không chứa văn bản.")
        cues.append({"start_ms": start, "end_ms": end, "text": content})
    return cues


def split_capcut_bilingual(path, output_dir):
    """Split CapCut's two sequential caption tracks, requiring exact paired timings.

    This preserves CapCut's translation verbatim. It does not claim that the
    translation is correct, and rejects mixed/unpaired/unknown arrangements.
    """
    text = Path(path).read_text(encoding="utf-8-sig")
    blocks = re.split(r"\n\s*\n", text.strip())
    if not blocks or len(blocks) % 2:
        raise ValueError("SRT song ngữ không có hai nhóm câu bằng nhau.")
    half = len(blocks) // 2
    tracks = []
    for offset in (0, half):
        track = []
        for index, block in enumerate(blocks[offset:offset+half], 1):
            lines = block.splitlines()
            if len(lines) < 3 or lines[0] != str(index+offset):
                raise ValueError("Cấu trúc SRT song ngữ không liên tục.")
            track.append(str(index) + "\n" + "\n".join(lines[1:]))
        encoded = "\n\n".join(track) + "\n"
        tracks.append((encoded, parse_srt(encoded)))
    cjk = re.compile(r"[\u3400-\u9fff]")
    # Chinese speech can contain standalone "OK", Latin names or numbers.
    # Require a majority of CJK cues; the remaining cues may be ASCII only.
    # The Vietnamese track must still be completely free of CJK and paired.
    is_chinese = [sum(bool(cjk.search(cue['text'])) for cue in cues) > len(cues)/2
                  and all(cjk.search(cue['text']) or cue['text'].isascii() for cue in cues)
                  for _, cues in tracks]
    if is_chinese.count(True) != 1:
        raise ValueError("Không xác định chắc chắn một nhóm Trung và một nhóm Việt.")
    chinese = is_chinese.index(True)
    vi_text, vi = tracks[1-chinese]
    zh_text, zh = tracks[chinese]
    if any(cjk.search(cue["text"]) for cue in vi) or not re.search("[ăâđêôơưĂÂĐÊÔƠƯàáảãạèéẻẽẹìíỉĩịòóỏõọùúủũụỳýỷỹỵ]", vi_text):
        raise ValueError("Nhóm đích chưa xác nhận được là văn bản Việt riêng.")
    if any((v["start_ms"], v["end_ms"]) != (z["start_ms"], z["end_ms"]) for v, z in zip(vi, zh)):
        raise ValueError("Hai nhóm phụ đề không khớp mốc; cần đối soát.")
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=False)
    for name, content in (("tieng-viet.srt", vi_text), ("tieng-trung.srt", zh_text)):
        (target / name).write_text(content, encoding="utf-8")
    return {"vietnamese": str(target / "tieng-viet.srt"), "chinese": str(target / "tieng-trung.srt"),
            "cue_count": half, "translation_review_required": True}


def compare_cues(actual, reference, tolerance_ms):
    if len(actual) != len(reference):
        raise ValueError("Số câu phụ đề xuất không khớp phụ đề chuẩn.")
    drift = 0
    for index, (left, right) in enumerate(zip(actual, reference), 1):
        if left["text"] != right["text"]:
            raise ValueError(f"Lời phụ đề khác bản chuẩn ở câu {index}.")
        drift = max(drift, *(abs(left[k] - right[k]) for k in ("start_ms", "end_ms")))
    if drift > tolerance_ms:
        raise ValueError(f"Mốc phụ đề lệch {drift} ms, vượt mức {tolerance_ms} ms.")
    return drift
