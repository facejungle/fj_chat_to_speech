APP_NAME = "FJ Chat to Speech"
APP_VERSION = "1.1.4"

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
    "ru": "v5_3_ru",
    "en": "v3_en",
}

SPEECH_RATE_INDEX = {
    0: "x-slow",
    1: "slow",
    2: "medium",
    3: "fast",
    4: "x-fast",
}

SAMPLE_RATE = 48000

DEFAULTS = {
    "voice": "random",
    "auto_scroll": True,
    "add_accents": True,
    "read_author_names": False,
    "read_platform_names": False,
    "read_filter": ("Regular", "Donation", "Sponsor", "Author", "Moderator"),
    "auto_translate": False,
    "buffer_maxsize": 5,
    "min_text_length": 2,
    "max_text_length": 300,
    "toxic_sense": 0.6,
    "ban_limit": 5,
    "volume": 100,
    "speech_rate": "medium",
    "speech_delay": 1.5,
}
