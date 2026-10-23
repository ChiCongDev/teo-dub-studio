"""The band that hides a video's own captions under the burned Vietnamese caption.

libass draws it from a separate script that holds only rectangles, never caption text, in the picture's pixel
coordinates. Its size follows the caption libass draws from the SRT with the usual style: font size, margins and
opaque-box padding are in ffmpeg's SRT script units (384x288), and the number of lines is predicted with the same
Arial font, so the band and the caption's own box line up (checked against libass on 21/10/2026).
"""
from functools import lru_cache
import math
import os
from pathlib import Path

from teodub.subtitle_style import SUBTITLE_BOXES, box_padding, validate_subtitle_size

SCRIPT_WIDTH, SCRIPT_HEIGHT = 384, 288  # ffmpeg's script size for SRT captions; the caption style uses these units.
MARGIN_SIDES, MARGIN_BOTTOM = 22, 18    # As in subtitle_style.ass_force_style and ass_box_style.
BAND_WIDTH = .92                        # Share of the picture width the band spans.
BAND_EXTRA = .15                        # Extra height above and below the caption box, in lines.
BRIDGE_MS = 1000                        # Lines closer than this keep one continuous band, so it does not flicker.
FALLBACK_EM = .5                        # Average character width (em) when the font file cannot be read.
# libass breaks lines on the font's own advance widths, which Pillow measures exactly (9/9 sample captions on
# 1920x1080, 1280x720 and 1080x1920 matched). Its drawn boxes are ~2% wider, but that does not move line breaks.


@lru_cache(maxsize=2)
def _font(bold):
    try:
        from PIL import ImageFont
        fonts = Path(os.environ.get('WINDIR', r'C:\Windows'))/'Fonts'
        return ImageFont.truetype(str(fonts/('arialbd.ttf' if bold else 'arial.ttf')), 1000)
    except (ImportError, OSError):
        return None


def _width_em(text, bold):
    font = _font(bold)
    return len(text)*FALLBACK_EM if font is None else font.getlength(text)/1000


def _em_per_line(bold):
    """libass scales a font so that ascent plus descent equals the font size; the em is slightly smaller."""
    font = _font(bold)
    if font is None:
        return 1/1.12
    ascent, descent = font.getmetrics()
    return 1000/(ascent+descent)


def caption_lines(text, size, width, height, bold):
    """How many lines libass wraps this caption into; like libass it breaks only at spaces."""
    line = validate_subtitle_size(size)*height/SCRIPT_HEIGHT
    em = line*_em_per_line(bold)
    available = (SCRIPT_WIDTH-2*MARGIN_SIDES)*width/SCRIPT_WIDTH
    space = _width_em(' ', bold)*em
    lines, used = 1, 0.
    for word in str(text).split():
        length = _width_em(word, bold)*em
        if used and used+space+length > available:
            lines, used = lines+1, length
        else:
            used = used+space+length if used else length
    return lines


def band_events(cues, width, height, size, weight):
    """One rectangle per caption in pixels, joined over short pauses; none without captions."""
    size = validate_subtitle_size(size)
    scale = height/SCRIPT_HEIGHT
    line = size*scale
    padding = box_padding(size)*scale
    extra = BAND_EXTRA*line
    bottom = min(float(height), height-MARGIN_BOTTOM*scale+padding+extra)
    band_width = BAND_WIDTH*width
    events = []
    for index, cue in enumerate(cues):
        lines = caption_lines(cue['text'], size, width, height, weight == 'bold')
        top = max(0., bottom-(lines*line+2*padding+2*extra))
        end = cue['end_ms']
        following = cues[index+1] if index+1 < len(cues) else None
        if following is not None and 0 <= following['start_ms']-cue['end_ms'] <= BRIDGE_MS:
            end = following['start_ms']
        events.append(dict(start_ms=cue['start_ms'], end_ms=end, x=(width-band_width)/2, y=top,
                           w=band_width, h=bottom-top))
    return events


def _ass_time(milliseconds, *, up=False):
    centiseconds = math.ceil(milliseconds/10) if up else math.floor(milliseconds/10)
    hours, rest = divmod(int(centiseconds), 360000)
    minutes, rest = divmod(rest, 6000)
    seconds, hundredths = divmod(rest, 100)
    return f'{hours}:{minutes:02}:{seconds:02}.{hundredths:02}'


def write_band(path, events, width, height, box):
    """A script of plain rectangles in the box colour. It never contains caption text."""
    if box == 'none' or box not in SUBTITLE_BOXES:
        raise ValueError('Kiểu khung che Sub gốc chưa được hỗ trợ.')
    colour = SUBTITLE_BOXES[box]['drawing']
    lines = ['[Script Info]', 'ScriptType: v4.00+', f'PlayResX: {int(width)}', f'PlayResY: {int(height)}',
             'ScaledBorderAndShadow: yes', 'WrapStyle: 2', 'YCbCr Matrix: None', '',
             '[V4+ Styles]',
             'Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, '
             'Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, '
             'MarginL, MarginR, MarginV, Encoding',
             'Style: Band,Arial,20,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1',
             '', '[Events]', 'Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text']
    for event in events:
        # Round outwards so the band never ends up a pixel short of the caption box.
        left, top = math.floor(event['x']), math.floor(event['y'])
        right, bottom = math.ceil(event['x']+event['w']), math.ceil(event['y']+event['h'])
        w, h = right-left, bottom-top
        drawing = '{\\an7\\pos(%d,%d)\\bord0\\shad0\\1c%s\\p1}m 0 0 l %d 0 %d %d 0 %d{\\p0}' % (
            left, top, colour, w, w, h, h)
        lines.append('Dialogue: 0,%s,%s,Band,,0,0,0,,%s' % (
            _ass_time(event['start_ms']), _ass_time(event['end_ms'], up=True), drawing))
    with Path(path).open('x', encoding='utf-8') as file:
        file.write('\n'.join(lines)+'\n')
