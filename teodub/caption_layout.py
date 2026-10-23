"""One box per burned Vietnamese line: the caption in the middle of a single rectangle that also hides the video's
own caption shown during that line (owner's choice 22/10/2026: one clean box with square corners, no steps).

Sizes follow the usual SRT caption style: the font size is in ffmpeg's 384x288 script units, a video whose own
captions are larger gets its Vietnamese text as large (matched_size), and the box keeps a sixth of the line height
as padding on every side. The box is no larger than needed (owner's
choice 22/10, "cách A"): vertically it is as tall as the text or the own caption, whichever is taller, covering the
own caption as close as possible to the video's caption rows, so an ordinary line stays at one height for the whole
video; horizontally it is always centred on the picture (owner 22/10), as wide as the text or as needed to reach
the own caption on both sides.
A video without its own captions keeps the caption where it always was. Lines are broken here, balanced the way
libass balances them, and written with explicit breaks, so the box always matches the lines drawn. Measured against
libass on 22/10/2026 (64 lines, 1024x576, 1920x1080 and 1080x1920, sizes 13-24): the ink never reaches past the
advance width Pillow predicts and stays within half a line of the line's middle.
"""
import math
from pathlib import Path

from teodub.subtitle_band import (MARGIN_BOTTOM, MARGIN_SIDES, SCRIPT_HEIGHT, SCRIPT_WIDTH, _ass_time, _em_per_line,
                                  _width_em)
from teodub.subtitle_style import MAX_SUBTITLE_SIZE, SUBTITLE_BOXES, SUBTITLE_WEIGHTS, validate_subtitle_size

PADDING = 1/6   # Space between the text and the box edge, as a share of the line height (owner 22/10).
MAX_GROW = 1.5  # A video's Vietnamese text grows to its own captions' height up to this times the chosen size.


def _metrics(size, height, bold):
    line = validate_subtitle_size(size)*height/SCRIPT_HEIGHT
    return line, line*_em_per_line(bold)


def matched_size(chosen, rows, height):
    """The Vietnamese size for one video: as tall as the video's own caption letters (its caption rows), never smaller
    than the chosen size and at most MAX_GROW times it, the same for every line (owner 22/10: big original captions
    get big Vietnamese text). Lines are not enlarged one by one: a large region there is too often a white plate or a
    plastic bag (checked on six real videos)."""
    chosen = validate_subtitle_size(chosen)
    if not rows:
        return chosen
    letters = (rows[1]-rows[0])*SCRIPT_HEIGHT/height
    return int(min(max(chosen, round(letters)), round(chosen*MAX_GROW), MAX_SUBTITLE_SIZE))


def wrap(text, size, width, height, bold):
    """The caption's lines: broken at spaces within the caption width, then balanced like libass's smart wrapping
    (a word moves down while that brings two neighbouring lines closer in width)."""
    _line, em = _metrics(size, height, bold)
    available = (SCRIPT_WIDTH-2*MARGIN_SIDES)*width/SCRIPT_WIDTH

    def measure(words):
        return _width_em(' '.join(words), bold)*em
    lines = []
    for word in str(text).split():
        if lines and measure(lines[-1]+[word]) <= available:
            lines[-1].append(word)
        else:
            lines.append([word])
    moved = True
    while moved:
        moved = False
        for index in range(len(lines)-1):
            upper, lower = lines[index], lines[index+1]
            if len(upper) < 2:
                continue
            shorter, longer = upper[:-1], [upper[-1]]+lower
            if (measure(longer) <= available and
                    abs(measure(shorter)-measure(longer)) < abs(measure(upper)-measure(lower))):
                lines[index], lines[index+1] = shorter, longer
                moved = True
    return [' '.join(words) for words in lines] or ['']


def _axis(half, low, high, usual):
    """Centre and half size along one axis: the text's box at its usual place when the own caption (low-high) fits
    inside it; otherwise the smallest box, as large as the text or the caption, that covers the caption and stays as
    close as possible to the usual place."""
    if usual-half <= low and high <= usual+half:
        return usual, half
    if high-low >= 2*half:
        return (low+high)/2, (high-low)/2
    return (low+half if low < usual-half else high-half), half


def place(text, region, rows, size, weight, width, height):
    """Lines, box (left, top, right, bottom) in whole pixels and the text's centre for one caption.

    region: the video's own caption to hide during this line (x0, y0, x1, y1), or None.
    rows: the video's caption rows (y0, y1), the box's usual place; without them the region's own middle.
    """
    bold = weight == 'bold'
    lines = wrap(text, size, width, height, bold)
    line, em = _metrics(size, height, bold)
    padding = line*PADDING
    half_w = max(_width_em(item, bold)*em for item in lines)/2+padding
    half_h = len(lines)*line/2+padding
    cx = width/2
    if region is None:
        # Where the caption always sat: its last line on the usual bottom margin.
        cy = height-MARGIN_BOTTOM*height/SCRIPT_HEIGHT-len(lines)*line/2
    else:
        x0, y0, x1, y1 = region
        # Always centred on the picture (owner 22/10): the box widens evenly when the own caption reaches further.
        half_w = max(half_w, cx-x0, x1-cx)
        cy, half_h = _axis(half_h, y0, y1, (rows[0]+rows[1])/2 if rows else (y0+y1)/2)
    half_w, half_h = min(half_w, width/2), min(half_h, height/2)
    # Moving the box inside the picture keeps the region covered: the box is at least as large as the region.
    cx = min(max(cx, half_w), width-half_w)
    cy = min(max(cy, half_h), height-half_h)
    left, top = math.floor(cx-half_w), math.floor(cy-half_h)
    right, bottom = math.ceil(cx+half_w), math.ceil(cy+half_h)
    left, top, right, bottom = max(0, left), max(0, top), min(int(width), right), min(int(height), bottom)
    return dict(lines=lines, box=(left, top, right, bottom), centre=((left+right)/2, (top+bottom)/2))


def layout(cues, regions, rows, size, weight, width, height):
    """One placed box per cue (see place); regions holds one entry per cue."""
    if len(regions) != len(cues):
        raise ValueError('Số vùng che không khớp số câu Việt.')
    return [dict(place(cue['text'], region, rows, size, weight, width, height),
                 start_ms=cue['start_ms'], end_ms=cue['end_ms']) for cue, region in zip(cues, regions)]


def _plain(text):
    """Caption text is data: braces and backslashes would become libass override tags."""
    return str(text).replace('\\', '/').replace('{', '(').replace('}', ')')


def write_captions(path, placed, size, weight, box, width, height):
    """libass script in picture pixels: per line its rectangle in the box colour, then the caption centred on it."""
    if box == 'none' or box not in SUBTITLE_BOXES:
        raise ValueError('Kiểu khung che Sub gốc chưa được hỗ trợ.')
    colours = SUBTITLE_BOXES[box]
    line, _em = _metrics(size, height, weight == 'bold')
    styles = ('Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, '
              'Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, '
              'MarginL, MarginR, MarginV, Encoding')
    script = ['[Script Info]', 'ScriptType: v4.00+', f'PlayResX: {int(width)}', f'PlayResY: {int(height)}',
              'ScaledBorderAndShadow: yes', 'WrapStyle: 2', 'YCbCr Matrix: None', '', '[V4+ Styles]', styles,
              f'Style: Box,Arial,20,{colours["box"]},{colours["box"]},&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1',
              f'Style: Caption,Arial,{line:.3f},{colours["text"]},{colours["text"]},&H00000000,&H00000000,'
              f'{SUBTITLE_WEIGHTS[weight]["bold"]},0,0,0,100,100,0,0,1,0,0,5,0,0,0,1',
              '', '[Events]', 'Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text']
    for item in placed:
        start, end = _ass_time(item['start_ms']), _ass_time(item['end_ms'], up=True)
        left, top, right, bottom = item['box']
        w, h = right-left, bottom-top
        script.append('Dialogue: 0,%s,%s,Box,,0,0,0,,{\\an7\\pos(%d,%d)\\bord0\\shad0\\1c%s\\p1}m 0 0 l %d 0 %d %d 0 %d{\\p0}' % (
            start, end, left, top, colours['drawing'], w, w, h, h))
        script.append('Dialogue: 1,%s,%s,Caption,,0,0,0,,{\\an5\\pos(%.1f,%.1f)}%s' % (
            start, end, item['centre'][0], item['centre'][1], '\\N'.join(_plain(text) for text in item['lines'])))
    with Path(path).open('x', encoding='utf-8') as file:
        file.write('\n'.join(script)+'\n')
