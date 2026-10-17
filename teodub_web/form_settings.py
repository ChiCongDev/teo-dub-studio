"""Personal rendering choices are separate from the administrator's AI policy."""
from teodub.ai_models import CHOICES, NEW_MODELS, local_voices, validate_models
from teodub.audio_mix import validate_audio_mix
from teodub.speech_audio import validate_voice_speed
from teodub.speech_plan import validate_style
from teodub.subtitle_style import validate_subtitle_style

MODEL_LABELS = dict(asr_model='Nhận dạng tiếng gốc', translation_model='Dịch lời Việt',
                    translation_effort='Mức suy luận', tts_model='Bộ giọng trên máy')
FORM_DEFAULTS = dict(voice='Adam', reading_style='conversation', voice_speed=1.0, speed=1.0,
                     original_gain=.1, voice_gain=1.0, burn_subtitles=True,
                     subtitle_color='white', subtitle_weight='bold', subtitle_size=13,
                     save_drive=True)
FORM_FIELDS = frozenset(FORM_DEFAULTS)


def model_policy(values):
    if not isinstance(values, dict) or set(values) != set(NEW_MODELS):
        raise ValueError('Chọn đủ bốn tùy chọn model AI của quản trị viên.')
    return validate_models(values)


def model_labels(values):
    return {key: CHOICES[key][value] for key, value in values.items()}


def preferences(values, models, *, fallback_voice=False):
    if not isinstance(values, dict) or set(values)-FORM_FIELDS:
        raise ValueError('Lựa chọn cá nhân có trường chưa được hỗ trợ.')
    data = dict(FORM_DEFAULTS, **values)
    voices = local_voices(models['tts_model'])
    if data['voice'] not in voices:
        if not fallback_voice:
            raise ValueError('Giọng chưa có trong bộ giọng do quản trị viên chọn.')
        data['voice'] = 'Adam'
    validate_style(data['reading_style'])
    voice_speed = validate_voice_speed(data['voice_speed'])
    if type(data['speed']) not in (int, float) or data['speed'] not in (.9, 1.0):
        raise ValueError('Tốc độ hình chỉ nhận 0,9× hoặc 1,0×.')
    if type(data['save_drive']) is not bool:
        raise ValueError('Lựa chọn Drive cần bật hoặc tắt.')
    return dict(voice=data['voice'], reading_style=data['reading_style'], voice_speed=voice_speed,
                speed=float(data['speed']), save_drive=data['save_drive'],
                **validate_audio_mix(data), **validate_subtitle_style(data))
