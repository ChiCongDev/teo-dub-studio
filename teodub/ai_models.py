"""Explicit, supported engine selections. Missing fields retain legacy jobs."""

ASR_MODELS = {
    'auto': 'Tự động theo cấu hình máy',
    'small': 'Whisper Small · nhẹ',
    'medium': 'Whisper Medium · cân bằng',
    'large-v3': 'Whisper Large v3 · ưu tiên chính xác',
}
TRANSLATION_MODELS = {
    'gpt-6-luna': 'GPT-6 Luna · tiết kiệm',
    'gpt-6.1-sol': 'GPT-6.1 Sol · cân bằng, ưu tiên chất lượng',
    'gpt-6-sol': 'GPT-6 Sol · cân bằng',
    'gpt-6-astra': 'GPT-6 Astra · nội dung khó',
    'gpt-5.6-luna': 'GPT-5.6 Luna · tiết kiệm hơn',
    'gpt-5.6-terra': 'GPT-5.6 Terra · cân bằng chi phí',
    'gpt-5.6-sol': 'GPT-5.6 Sol · chất lượng cao',
    'default': 'Mặc định Codex · như bản cũ',
}
REASONING_LEVELS = {'low': 'Thấp · ít suy luận hơn', 'medium': 'Vừa', 'high': 'Cao · suy luận kỹ hơn'}
TTS_MODELS = {'v3turbo': 'VieNeu v3 Turbo · 48 kHz', 'v3nano': 'VieNeu v3 Nano · nhẹ, 24 kHz'}
TURBO_VOICES = ('Mai Anh', 'Trúc Ly', 'Thùy Dung', 'Ngọc Huyền', 'Adam', 'Adam bựa')
NANO_VOICES = ('Mai Anh', 'Trúc Ly', 'Minh Quân', 'Mạnh Dũng', 'Adam')
LEGACY_MODELS = dict(asr_model='auto', translation_model='default', translation_effort='medium', tts_model='v3turbo')
NEW_MODELS = dict(asr_model='auto', translation_model='gpt-6-luna', translation_effort='low', tts_model='v3turbo')
CHOICES = dict(asr_model=ASR_MODELS, translation_model=TRANSLATION_MODELS,
               translation_effort=REASONING_LEVELS, tts_model=TTS_MODELS)


def validate_models(config):
    values = {key: config.get(key, default) for key, default in LEGACY_MODELS.items()}
    for key, value in values.items():
        if not isinstance(value, str) or value not in CHOICES[key]:
            raise ValueError('Mô hình AI hoặc mức suy luận không có trong danh sách; hãy chọn lại.')
    return values


def translation_selection(model='default', reasoning_effort='medium'):
    validate_models(dict(translation_model=model, translation_effort=reasoning_effort))
    return dict(model=model, reasoning_effort=reasoning_effort)


def local_voices(model):
    validate_models(dict(tts_model=model))
    return NANO_VOICES if model == 'v3nano' else TURBO_VOICES
