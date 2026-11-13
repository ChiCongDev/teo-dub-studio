"""Independent export audio levels, with defaults for existing jobs."""
import math


def _gain(value, label):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not 0 <= value <= 2 or not math.isfinite(value)):
        raise ValueError(f'{label} phải nằm trong khoảng 0–200%.')
    return float(value)


def validate_audio_mix(config):
    """Return validated numeric gains; zero deliberately mutes that source."""
    return dict(original_gain=_gain(config.get('original_gain', .1), 'Âm lượng tiếng gốc'),
                voice_gain=_gain(config.get('voice_gain', 1.0), 'Âm lượng giọng lồng'))


def validate_voice_removal(config):
    """13/11/2026 (owner): whether the original voices are removed from the video's own sound (default: no)."""
    value = config.get('remove_original_voice', False)
    if type(value) is not bool:
        raise ValueError('Lựa chọn bỏ giọng nói gốc không hợp lệ.')
    return dict(remove_original_voice=value)


def validate_percent(value, label):
    """Parse a UI percentage (including Vietnamese decimals) into a gain."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError(f'{label} phải là số từ 0 đến 200%.')
    try:
        number = float(value.strip().replace(',', '.') if isinstance(value, str) else value)
    except (ValueError, OverflowError):
        raise ValueError(f'{label} phải là số từ 0 đến 200%.') from None
    return _gain(number/100, label)
