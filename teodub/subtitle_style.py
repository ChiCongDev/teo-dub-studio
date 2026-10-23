"""Validated presentation options for optional hard-burned Vietnamese captions."""

SUBTITLE_COLORS = {
    'white': dict(label='Trắng', primary='&H00FFFFFF', outline='&H00000000'),
    'black': dict(label='Đen', primary='&H00000000', outline='&H00FFFFFF'),
    'yellow': dict(label='Vàng', primary='&H0000FFFF', outline='&H00000000'),
}

SUBTITLE_WEIGHTS = {
    'regular': dict(label='Chữ nhạt', bold=0),
    'bold': dict(label='Chữ đậm', bold=-1),
}

# A solid band under the burned caption hides the video's own captions there. The box decides the text colour.
SUBTITLE_BOXES = {
    'none': dict(label='Không khung'),
    'white': dict(label='Khung trắng · chữ đen', box='&H00FFFFFF', text='&H00000000', drawing='&HFFFFFF&'),
    'black': dict(label='Khung đen · chữ trắng', box='&H00000000', text='&H00FFFFFF', drawing='&H000000&'),
}

DEFAULT_SUBTITLE_SIZE = 13
MIN_SUBTITLE_SIZE, MAX_SUBTITLE_SIZE = 8, 36


def validate_subtitle_size(value):
    if isinstance(value, str) and value.strip().isascii() and value.strip().isdigit():
        value = int(value.strip())
    if type(value) is not int or not MIN_SUBTITLE_SIZE <= value <= MAX_SUBTITLE_SIZE:
        raise ValueError('Cỡ chữ Sub phải là số nguyên từ 8 đến 36; mặc định 13.')
    return value


def validate_subtitle_style(config):
    """Return immutable renderer options, with safe defaults for old jobs."""
    burned = config.get('burn_subtitles', False)
    color = config.get('subtitle_color', 'white')
    weight = config.get('subtitle_weight', 'bold')
    size = validate_subtitle_size(config.get('subtitle_size', DEFAULT_SUBTITLE_SIZE))
    if type(burned) is not bool:
        raise ValueError('Tùy chọn Ghi Sub phải là bật hoặc tắt.')
    if color not in SUBTITLE_COLORS:
        raise ValueError('Màu chữ Sub chưa được hỗ trợ.')
    if weight not in SUBTITLE_WEIGHTS:
        raise ValueError('Kiểu đậm/nhạt của Sub chưa được hỗ trợ.')
    box = config.get('subtitle_box', 'none')
    if not isinstance(box, str) or box not in SUBTITLE_BOXES:
        raise ValueError('Kiểu khung che Sub gốc chưa được hỗ trợ.')
    result = dict(burn_subtitles=burned, subtitle_color=color, subtitle_weight=weight, subtitle_size=size)
    if burned and box != 'none':
        # Absent means no box, so every setting saved before this option stays exactly as it was.
        result['subtitle_box'] = box
    return result


def box_padding(size):
    """Space between the caption text and the edge of its box, in the same script units as the font size."""
    return validate_subtitle_size(size)/4


def ass_force_style(color, weight, size=DEFAULT_SUBTITLE_SIZE):
    """Create a constrained libass style; no user text enters the filter graph."""
    selected_color = SUBTITLE_COLORS[color]
    selected_weight = SUBTITLE_WEIGHTS[weight]
    return ','.join((
        'FontName=Arial',
        f'FontSize={validate_subtitle_size(size)}',
        f'PrimaryColour={selected_color["primary"]}',
        f'OutlineColour={selected_color["outline"]}',
        'BackColour=&H00000000',
        f'Bold={selected_weight["bold"]}',
        'Italic=0',
        'BorderStyle=1',
        'Outline=1.3',
        'Shadow=0',
        'Alignment=2',
        'MarginL=22',
        'MarginR=22',
        'MarginV=18',
    ))


def ass_box_style(box, weight, size=DEFAULT_SUBTITLE_SIZE):
    """The same caption on an opaque box in the band's colour: same font, size and place as ass_force_style."""
    if box == 'none' or box not in SUBTITLE_BOXES:
        raise ValueError('Kiểu khung che Sub gốc chưa được hỗ trợ.')
    selected_box = SUBTITLE_BOXES[box]
    selected_weight = SUBTITLE_WEIGHTS[weight]
    return ','.join((
        'FontName=Arial',
        f'FontSize={validate_subtitle_size(size)}',
        f'PrimaryColour={selected_box["text"]}',
        f'OutlineColour={selected_box["box"]}',
        f'BackColour={selected_box["box"]}',
        f'Bold={selected_weight["bold"]}',
        'Italic=0',
        'BorderStyle=3',
        f'Outline={box_padding(size):g}',
        'Shadow=0',
        'Alignment=2',
        'MarginL=22',
        'MarginR=22',
        'MarginV=18',
    ))
