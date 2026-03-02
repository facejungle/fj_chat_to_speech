from app.translations import DEFAULT_LANGUAGE

APP_NAME = "FJ Chat to Speech"
APP_VERSION = "1.1.0"

PADDING = 20
VOICES = {
    "ru": ("xenia", "aidar", "baya", "kseniya", "eugene"),
    "en": (
        "en_0",
        "en_1",
        "en_2",
        "en_3",
        "en_4",
        "en_5",
        "en_6",
        "en_7",
        "en_8",
        "en_9",
        "en_10",
        "en_11",
        "en_12",
        "en_13",
        "en_14",
        "en_15",
        "en_16",
        "en_17",
        "en_18",
        "en_19",
        "en_20",
    ),
}

MODELS = {
    "ru": "v5_1_ru",
    "en": "v3_en",
}

DEFAULTS = {
    "language": DEFAULT_LANGUAGE,
    "voice_language": DEFAULT_LANGUAGE,
    "voice": "random",
    "auto_scroll": True,
    "add_accents": True,
    "read_author_names": False,
    "read_platform_names": False,
    "subscribers_only": False,
    "auto_translate": False,
    "buffer_maxsize": 5,
    "min_msg_length": 2,
    "max_msg_length": 180,
    "toxic_sense": 0.6,
    "ban_limit": 5,
    "volume": 100,
    "speech_rate": 1.0,
    "speech_delay": 1.5,
}
