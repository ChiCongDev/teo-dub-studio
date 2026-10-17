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
    return dict(burn_subtitles=burned, subtitle_color=color, subtitle_weight=weight, subtitle_size=size)


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
