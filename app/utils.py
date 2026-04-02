import colorsys
from functools import lru_cache
import hashlib
import os
import platform
import re
import sys
from typing import Iterable, TextIO
import unicodedata

import num2words
import urllib

import transformers
from PyQt6.QtGui import QColor

from app.constants import APP_NAME
from app.constants_qt import COLORS_RGBA
from app.translations import _

_detoxify_ = None
_detoxify_impl_ = None
_torch_hub_ = None
_torch_no_grad_ = None

_emoji_shortcode_cache: dict[str, str] = {}
EMOJI_SHORTCODE_RE = re.compile(r":[0-9A-Za-z_+-]{1,64}:")
_EMOJI_SKIN_TONES = (
    ("pink", "LIGHT SKIN TONE"),
    ("light", "LIGHT SKIN TONE"),
    ("medium-light", "MEDIUM-LIGHT SKIN TONE"),
    ("medium", "MEDIUM SKIN TONE"),
    ("medium-dark", "MEDIUM-DARK SKIN TONE"),
    ("dark", "DARK SKIN TONE"),
)
_EMOJI_NAME_ALIASES = {
    "+1": "THUMBS UP SIGN",
    "-1": "THUMBS DOWN SIGN",
    "thumbsup": "THUMBS UP SIGN",
    "thumbsdown": "THUMBS DOWN SIGN",
    "heart": "RED HEART",
}


@lru_cache(maxsize=1024)
def _lookup_unicode_name(name: str) -> str:
    try:
        return unicodedata.lookup(name)
    except KeyError:
        return ""


def get_detoxify_impl():
    global _detoxify_impl_
    if _detoxify_impl_ is None:
        import detoxify.detoxify as detoxify_impl

        _detoxify_impl_ = detoxify_impl
    return _detoxify_impl_


def get_torch_hub():
    global _torch_hub_
    if _torch_hub_ is None:
        from torch import hub as torch_hub
        from torch import set_num_threads

        set_num_threads(max(1, os.cpu_count() or 1))
        _torch_hub_ = torch_hub
    return _torch_hub_


def get_detoxify():
    global _detoxify_
    if _detoxify_ is None:
        from detoxify import Detoxify

        _detoxify_ = Detoxify
    return _detoxify_


def torch_no_grad():
    global _torch_no_grad_
    if _torch_no_grad_ is None:
        from torch import no_grad

        _torch_no_grad_ = no_grad

    return _torch_no_grad_


class _NullStream(TextIO):
    """Fallback stream used when GUI builds have no stdio handles."""

    encoding = "utf-8"

    def write(self, _text):
        return 0

    def flush(self):
        return None

    def isatty(self):
        return False


def ensure_stdio_streams():
    """Torch hub download path expects writable stderr/stdout streams."""
    if sys.stdout is None:
        sys.stdout = _NullStream()
    if sys.stderr is None:
        sys.stderr = _NullStream()


@lru_cache
def root_dir() -> str:
    return getattr(sys, "_MEIPASS", os.path.abspath("."))


@lru_cache
def resource_path(relative_path: str) -> str:
    """Resolve resource paths for source and PyInstaller onefile builds."""
    return os.path.join(root_dir(), str(relative_path))


def icon_path():
    if platform.system() == "Windows":
        return "img\\icon.ico"
    else:
        return "img/icon.png"


def get_user_data_dir() -> str:
    """Return a persistent per-user directory for runtime data."""
    if sys.platform.startswith("win"):
        appdata = os.getenv("APPDATA")
        if appdata:
            return os.path.join(appdata, APP_NAME)
    return os.path.join(
        os.path.expanduser("~"), f".{APP_NAME.lower().replace(' ', '_')}"
    )


def get_emoji_cache_path():
    _dir = get_user_data_dir()
    _dir = os.path.join(_dir, "img", "emoji")
    os.makedirs(_dir, exist_ok=True)
    return _dir


def get_banned_list_path():
    _dir = get_user_data_dir()
    _dir = os.path.join(_dir, "spam_filter")
    os.makedirs(_dir, exist_ok=True)
    return os.path.join(_dir, "banned.txt")


def get_settings_path() -> str:
    settings_dir = get_user_data_dir()
    os.makedirs(settings_dir, exist_ok=True)
    return os.path.join(settings_dir, "settings.json")


def configure_torch_hub_cache():
    """Use a stable user cache path only for frozen builds."""
    ensure_stdio_streams()

    if not getattr(sys, "frozen", False):
        return

    home_dir = os.path.expanduser("~")
    cache_root = os.path.join(home_dir, ".cache")
    torch_home = os.path.join(cache_root, "torch")
    torch_hub_dir = os.path.join(torch_home, "hub")

    os.environ.setdefault("XDG_CACHE_HOME", cache_root)
    os.environ.setdefault("TORCH_HOME", torch_home)
    os.makedirs(torch_hub_dir, exist_ok=True)
    hub = get_torch_hub()
    hub.set_dir(torch_hub_dir)


def find_cached_silero_repo():
    hub = get_torch_hub()
    hub_dir = hub.get_dir()
    if not os.path.isdir(hub_dir):
        return None

    repo_candidates = []
    prefix = "snakers4_silero-models_"
    for entry in os.listdir(hub_dir):
        if entry.startswith(prefix):
            repo_path = os.path.join(hub_dir, entry)
            if os.path.isdir(repo_path):
                repo_candidates.append(repo_path)

    if not repo_candidates:
        return None
    return max(repo_candidates, key=os.path.getmtime)


def clear_cache_silero(model: str | None = None):
    hub = get_torch_hub()
    hub_dir = hub.get_dir()
    if not os.path.isdir(hub_dir):
        return

    prefix = "snakers4_silero-models_"

    if model:
        for entry in os.listdir(hub_dir):
            if entry.startswith(prefix):
                models_path = os.path.join(hub_dir, f"{entry}/src/silero/model/")
                if os.path.exists(models_path):
                    for file in os.listdir(models_path):
                        if file.startswith(model):
                            # os.system(f'del /F /Q "{os.path.join(file, models_path)}"')
                            os.remove(os.path.join(file, models_path))
                # else:
                #     if os.path.isdir(entry):
                #         os.rmdir(entry)
        return

    for entry in os.listdir(hub_dir):
        if entry.startswith(prefix):
            repo_path = os.path.join(hub_dir, entry)
            if os.path.isdir(repo_path):
                os.rmdir(repo_path)
                # os.system('rmdir /S /Q "{}"'.format(repo_path))


def detoxify_get_model_and_tokenizer_local_only(
    model_type,
    model_name,
    tokenizer_name,
    num_classes,
    state_dict,
    huggingface_config_path=None,
    local_files_only=True,
):
    model_class = getattr(transformers, model_name)
    source = huggingface_config_path or model_type
    config = model_class.config_class.from_pretrained(
        source,
        num_labels=num_classes,
        local_files_only=local_files_only,
    )
    model = model_class.from_pretrained(
        pretrained_model_name_or_path=None,
        config=config,
        state_dict=state_dict,
        local_files_only=local_files_only,
    )
    tokenizer = getattr(transformers, tokenizer_name).from_pretrained(
        source,
        local_files_only=local_files_only,
    )
    return model, tokenizer


def find_cached_detoxify_checkpoint(model_type="multilingual"):
    hub = get_torch_hub()
    hub_dir = hub.get_dir()
    checkpoints_dir = os.path.join(hub_dir, "checkpoints")
    if not os.path.isdir(checkpoints_dir):
        return None, None

    expected_prefixes = {
        "original": "toxic_original-",
        "unbiased": "toxic_debiased-",
        "multilingual": "multilingual_debiased-",
        "original-small": "original-albert-",
        "unbiased-small": "unbiased-albert-",
    }
    expected_prefix = expected_prefixes.get(model_type)
    if not expected_prefix:
        return None, None

    checkpoint_candidates = []
    for entry in os.listdir(checkpoints_dir):
        if not entry.endswith(".ckpt"):
            continue
        if not entry.startswith(expected_prefix):
            continue
        checkpoint_path = os.path.join(checkpoints_dir, entry)
        if not os.path.isfile(checkpoint_path):
            continue

        checkpoint_candidates.append(checkpoint_path)

    if not checkpoint_candidates:
        return None, None

    os.environ["TRANSFORMERS_OFFLINE"] = "1"

    hf_config_path = None
    hf_cache_root = os.getenv("HF_HOME") or os.path.join(
        os.path.expanduser("~"), ".cache", "huggingface"
    )
    hf_snapshots_dir = os.path.join(
        hf_cache_root, "hub", "models--xlm-roberta-base", "snapshots"
    )
    if os.path.isdir(hf_snapshots_dir):
        snapshots = [
            os.path.join(hf_snapshots_dir, entry)
            for entry in os.listdir(hf_snapshots_dir)
            if os.path.isdir(os.path.join(hf_snapshots_dir, entry))
        ]
        if snapshots:
            hf_config_path = max(snapshots, key=os.path.getmtime)
            os.environ["HF_HUB_OFFLINE"] = "1"

    return checkpoint_candidates, hf_config_path


def clear_cache_detoxify():
    hf_cache_root = os.getenv("HF_HOME") or os.path.join(
        os.path.expanduser("~"), ".cache", "huggingface"
    )
    hf_snapshots_dir = os.path.join(hf_cache_root, "hub", "models--xlm-roberta-base")

    if os.path.isdir(hf_snapshots_dir):
        os.rmdir(hf_snapshots_dir)
        # os.system('rmdir /S /Q "{}"'.format(hf_snapshots_dir))

    hub = get_torch_hub()
    hub_dir = hub.get_dir()
    checkpoints_dir = os.path.join(hub_dir, "checkpoints")
    if not os.path.isdir(checkpoints_dir):
        return

    for entry in os.listdir(checkpoints_dir):
        if not entry.startswith("multilingual_debiased-"):
            continue
        checkpoint_path = os.path.join(checkpoints_dir, entry)
        if not os.path.isfile(checkpoint_path):
            continue
        os.remove(checkpoint_path)


def clean_symbol_spam(text: str) -> str:
    if not isinstance(text, str) or not text:
        return text

    _text = text
    if re.search(r"(.)\1{3,}", text):
        _text = re.sub(r"(.)\1{3,}", r"\1", text)

    # If the message already looks like human language (after collapsing repeats),
    # do not attempt to remove separators/spaces — it causes long messages to be glued.
    if _is_normal_text(_text):
        return _text

    _text_ = re.sub(r"[\s\-_\.]", "", _text)
    if not _is_normal_text(_text_):
        _text = _text_

    if _is_normal_text(_text):
        return _text

    words = re.findall(r"[a-zа-яё0-9]+", _text.lower())

    cleaned = []
    for w in words:
        w = _clean_word(w)
        if w:
            cleaned.append(w)

    deduped = []
    for w in cleaned:
        if not deduped or deduped[-1] != w:
            deduped.append(w)

    if len(deduped) > 3 and len(set(deduped)) <= 2:
        return deduped[0]

    return " ".join(deduped)


def _is_normal_text(text: str) -> bool:
    if text.rstrip().endswith((".", "!", "?")):
        words = text.split()
        if len(words) > 1:
            sentences = re.split(r"[.!?]+", text)
            for s in sentences:
                s = s.strip()
                if s and s[0].isupper():
                    return True

    has_uppercase = any(c.isupper() for c in text if c.isalpha())
    has_punctuation = any(c in text for c in ".!?,;:-")

    letters = sum(c.isalpha() for c in text)
    total = len(text.strip())
    if total == 0:
        return False

    letter_ratio = letters / total

    tokens = re.findall(r"[^\W_]+", text, flags=re.UNICODE)
    meaningful_tokens = [t for t in tokens if len(t) >= 3]
    if len(meaningful_tokens) >= 4:
        token_lengths_sum = sum(len(t) for t in meaningful_tokens)
        avg_token_len = (
            token_lengths_sum / len(meaningful_tokens) if meaningful_tokens else 0
        )
        tokens_lower = [t.lower() for t in meaningful_tokens]
        unique_token_ratio = (
            (len(set(tokens_lower)) / len(tokens_lower)) if tokens_lower else 0
        )
        if letter_ratio >= 0.6 and avg_token_len >= 4 and unique_token_ratio >= 0.25:
            return True

    unique_chars = len(set(text.lower()))
    unique_ratio_denom = min(total, 80)
    unique_ratio = unique_chars / unique_ratio_denom if unique_ratio_denom > 0 else 0

    return (
        letter_ratio > 0.5 or (has_punctuation and has_uppercase)
    ) and unique_ratio > 0.3


def _clean_word(word: str) -> str:
    if len(word) < 2:
        return word

    if re.fullmatch(r"\d+(?:[.,]\d+)?", word):
        if _is_repetitive(word):
            return ""
        if _is_low_diversity(word, min_len=5):
            return ""
        if len(word) >= 8:
            for block_len in range(1, min(5, len(word) // 2)):
                if len(word) % block_len == 0:
                    block = word[:block_len]
                    if (
                        block * (len(word) // block_len) == word
                        and len(word) // block_len >= 2
                    ):
                        return ""
        return word

    word = _collapse_repeats(word)

    if _is_repetitive(word) or _is_low_diversity(word):
        return ""

    return word


def _collapse_repeats(s: str) -> str:
    if len(s) < 2:
        return s
    result = [s[0]]
    for ch in s[1:]:
        if ch != result[-1]:
            result.append(ch)
    return "".join(result)


def _is_repetitive(s: str, min_repeats: int = 3, max_block: int = 4) -> bool:
    length = len(s)
    for block_len in range(1, min(max_block, length // 2) + 1):
        if length % block_len != 0:
            continue
        block = s[:block_len]
        repeats = length // block_len
        if repeats >= min_repeats and block * repeats == s:
            return True
    return False


def _is_low_diversity(s: str, threshold: float = 0.34, min_len: int = 10) -> bool:
    if len(s) < min_len:
        return False
    unique_ratio = len(set(s)) / len(s)
    return unique_ratio < threshold


def clean_emoji(text):
    text = re.sub(r"<a?:[A-Za-z0-9_]{2,32}:\d{1,20}>", "", text)
    return re.sub(r":[0-9A-Za-z_+-]{1,64}:", "", text).strip()


def clean_links(text, lang: str = "en"):
    link_text = _(lang, "Link")
    _text = re.sub(r"https?://\S+", f" -{link_text}- ", text)
    _text = re.sub(r"http?://\S+", f" -{link_text}- ", _text)
    _text = re.sub(r"www\.\S+", f" -{link_text}- ", _text)
    _text = re.sub(r"\S+\.\S+/\S+", f" -{link_text}- ", _text)
    return _text


def clean_message(
    text: str,
    lang: str,
    convert_numbers: bool = True,
    clean_spam: bool = True,
) -> str:
    """Clean message from garbage"""
    text = str(text or "")
    text = re.sub(r"[^0-9A-Za-zА-Яа-яЁё\s!,-.:?]", " ", text).strip()

    if not text:
        return text

    text = re.sub(r"\s+", " ", text)
    if convert_numbers:
        text = convert_numbers_to_words(text, lang)
    if clean_spam:
        text = clean_symbol_spam(text.strip())
    return text.strip()


def load_stop_words(lang):
    source_path = resource_path(f"spam_filter/{lang}.txt")
    try:
        with open(source_path, "r", encoding="utf-8") as file:
            return tuple(line.strip() for line in file if line.strip())
    except FileNotFoundError:
        pass
    except Exception as e:
        pass
    return tuple()


def contains_stop_words(text: str, stop_words: Iterable[str]) -> bool:
    """
    Check if text contains any stop words.

    Args:
        text: Input text to check
        stop_words: Iterable stop words list

    Returns:
        True if text contains any stop words, False otherwise
    """
    if not text or not stop_words:
        return False

    text_lower = text.lower()
    text_lower = text.replace("ё", "е")
    text_lower = clean_symbols(text_lower)
    text_words = text_lower.split(" ")
    text_words_join = "".join(text_words)

    for word in text_words:
        if word in stop_words:
            return True

    for stop_word in stop_words:
        if len(stop_word) >= 4 and (
            stop_word in text_lower or stop_word in text_words_join
        ):
            return True

    return False


def clean_stop_words(text: str, stop_words: Iterable[str]) -> str:
    if not text or not stop_words:
        return text

    stop_words_set = set(stop_words)
    if not stop_words_set:
        return text

    normalized_text = text.lower().replace("ё", "е")
    cleaned_text = clean_symbols(normalized_text)
    spans = []

    for match in re.finditer(r"[^\s]+", cleaned_text):
        word = match.group(0)
        if word in stop_words_set:
            spans.append((match.start(), match.end()))

    long_stop_words = [word for word in stop_words_set if len(word) >= 4]

    if long_stop_words:
        long_stop_words.sort(key=len, reverse=True)
        overlap_pattern = re.compile(
            f"(?=({'|'.join(re.escape(word) for word in long_stop_words)}))"
        )

        for match in overlap_pattern.finditer(cleaned_text):
            found = match.group(1)
            spans.append((match.start(), match.start() + len(found)))

        chars = []
        index_map = []
        append_char = chars.append
        append_index = index_map.append
        for idx, char in enumerate(cleaned_text):
            if char != " ":
                append_char(char)
                append_index(idx)
        joined_text = "".join(chars)

        for match in overlap_pattern.finditer(joined_text):
            found = match.group(1)
            start = match.start()
            spans.append((index_map[start], index_map[start + len(found) - 1] + 1))

    if not spans:
        return text

    spans.sort()
    merged_spans = []
    current_start, current_end = spans[0]
    for start, end in spans[1:]:
        if start <= current_end:
            current_end = max(current_end, end)
        else:
            merged_spans.append((current_start, current_end))
            current_start, current_end = start, end
    merged_spans.append((current_start, current_end))

    result = []
    last_end = 0
    for start, end in merged_spans:
        result.append(text[last_end:start])
        result.append("-_-")
        last_end = end
    result.append(text[last_end:])

    return "".join(result)


def clean_symbols(text: str):
    string = ""
    for ch in text:
        if ch.isalpha():
            string += ch
        else:
            string += " "
    return string.strip()


def all_letters_is(text: str, lang: str = "en") -> bool:
    letters = [ch for ch in text if ch.isalpha()]

    if lang == "en":
        return all(re.match(r"[A-Za-z]", ch) for ch in letters)
    if lang == "ru":
        return all(re.match(r"[А-Яа-яЁё]", ch) for ch in letters)

    return True


def contain_words_or_nums(text: str, lang: str = "en") -> bool:
    value = str(text or "").strip()
    if not value:
        return False

    if lang == "en":
        return bool(re.search(r"[A-Za-z0-9]", value))
    if lang == "ru":
        return bool(re.search(r"[А-Яа-яЁё0-9]", value))

    return bool(re.search(r"[^\W_]", value, flags=re.UNICODE))


def contrast_color_from_rgb(r, g, b):
    lum = 0.299 * r + 0.587 * g + 0.114 * b
    color = "#000000" if lum > 0.6 else "#ffffff"
    return color


@lru_cache
def avatar_colors_from_name(name: str):
    """Deterministic avatar background and foreground color from author name."""
    if not name:
        return "#777777", "#ffffff"

    # Use MD5 hash to get stable value from name
    digest = hashlib.md5(name.encode("utf-8")).digest()
    # Take 3 bytes to form a value for hue
    val = int.from_bytes(digest[:3], "big")
    hue = val % 360

    # HLS -> colorsys uses H,L,S where H in [0,1]
    h = hue / 360.0
    l = 0.50
    s = 0.65
    r, g, b = colorsys.hls_to_rgb(h, l, s)
    r_i, g_i, b_i = int(r * 255), int(g * 255), int(b * 255)
    bg = f"#{r_i:02x}{g_i:02x}{b_i:02x}"

    fg = contrast_color_from_rgb(r, g, b)
    return bg, fg


def convert_numbers_to_words(text: str, lang: str) -> str:
    """Convert numbers to text representation"""

    def replace_number(match):
        num = match.group()
        try:
            if "." in num:
                parts = num.split(".")
                integer_part = num2words.num2words(int(parts[0]), lang=lang)
                fractional_part = num2words.num2words(int(parts[1]), lang=lang)
                return f" {integer_part} {_(lang, 'point')} {fractional_part} "
            elif "," in num:
                parts = num.split(",")
                integer_part = num2words.num2words(int(parts[0]), lang=lang)
                fractional_part = num2words.num2words(int(parts[1]), lang=lang)
                return f" {integer_part} {_(lang, 'comma')} {fractional_part} "
            else:
                return f" {num2words.num2words(int(num), lang=lang)} "
        except Exception:
            return num

    number_pattern = r"\d+(?:[.,]\d+)?"
    converted_text = re.sub(number_pattern, replace_number, text)
    return converted_text


def parse_youtube_video_id(url: str) -> str | None:
    try:
        if (
            url
            and "/" not in url
            and "?" not in url
            and "#" not in url
            and "." not in url
        ):
            return url

        if url.startswith("watch?v="):
            return url.removeprefix("watch?v=")

        has_scheme = "://" in url
        parsed = urllib.parse.urlparse(url)

        if not has_scheme:
            path_parts = parsed.path.split("/")
            if path_parts:
                possible_domain = path_parts[0].lower()
                allowed_domains = {
                    "youtube.com",
                    "www.youtube.com",
                    "m.youtube.com",
                    "youtu.be",
                    "studio.youtube.com",
                }

                if possible_domain in allowed_domains:
                    netloc = possible_domain
                    remaining_path = (
                        "/" + "/".join(path_parts[1:]) if len(path_parts) > 1 else ""
                    )
                    query = parsed.query

                    if netloc == "youtu.be":
                        video_id = remaining_path[1:] if remaining_path else None

                    elif netloc in ["youtube.com", "www.youtube.com", "m.youtube.com"]:
                        if remaining_path in ["/watch", "/watch/"]:
                            query_dict = urllib.parse.parse_qs(query)
                            video_id = query_dict.get("v", [None])[0]

                        elif remaining_path.startswith("/live/"):
                            video_id = remaining_path.split("/")[-1]

                        elif remaining_path.startswith("/shorts/"):
                            video_id = remaining_path.split("/")[-1]

                        elif remaining_path.startswith("/embed/"):
                            video_id = remaining_path.split("/")[-1]

                    elif netloc == "studio.youtube.com" and "/video/" in remaining_path:
                        path_parts_clean = [
                            part for part in remaining_path.split("/") if part
                        ]
                        if "video" in path_parts_clean:
                            video_index = path_parts_clean.index("video")
                            if video_index + 1 < len(path_parts_clean):
                                video_id = path_parts_clean[video_index + 1]

                    if video_id:
                        return video_id

        netloc = parsed.netloc.split(":")[0].lower()
        allowed_domains = {
            "youtube.com",
            "www.youtube.com",
            "m.youtube.com",
            "youtu.be",
            "studio.youtube.com",
        }

        if netloc not in allowed_domains:
            return

        video_id = None

        if netloc == "youtu.be":
            video_id = parsed.path[1:]

        elif netloc in ["youtube.com", "www.youtube.com", "m.youtube.com"]:
            if parsed.path in ["/watch", "/watch/"]:
                query = urllib.parse.parse_qs(parsed.query)
                video_id = query.get("v", [None])[0]

            elif parsed.path.startswith("/live/"):
                video_id = parsed.path.split("/")[-1]

            elif parsed.path.startswith("/shorts/"):
                video_id = parsed.path.split("/")[-1]

            elif parsed.path.startswith("/embed/"):
                video_id = parsed.path.split("/")[-1]

        elif netloc == "studio.youtube.com" and "/video/" in parsed.path:
            path_parts = [part for part in parsed.path.split("/") if part]
            if "video" in path_parts:
                video_index = path_parts.index("video")
                if video_index + 1 < len(path_parts):
                    video_id = path_parts[video_index + 1]

        if not video_id:
            return

        return video_id

    except Exception:
        return


def _emoji_from_shortcode(shortcode: str) -> str:
    cached = _emoji_shortcode_cache.get(shortcode)
    if cached is not None:
        return cached

    raw = shortcode.strip(":").replace("_", "-").casefold()
    if not raw:
        _emoji_shortcode_cache[shortcode] = shortcode
        return shortcode

    tone = ""
    padded_raw = f"-{raw}-"
    for token, tone_name in _EMOJI_SKIN_TONES:
        marker = f"-{token}-"
        if marker in padded_raw:
            raw = raw.replace(token, "").replace("--", "-").strip("-")
            tone = tone_name
            break

    words = [word for word in raw.split("-") if word]
    if not words:
        _emoji_shortcode_cache[shortcode] = shortcode
        return shortcode

    normalized = "-".join(words)
    candidates = []
    alias_name = _EMOJI_NAME_ALIASES.get(normalized)
    if alias_name:
        candidates.append(alias_name)

    joined = " ".join(words).upper()
    candidates.append(joined)

    reversed_joined = " ".join(reversed(words)).upper()
    if reversed_joined != joined:
        candidates.append(reversed_joined)

    if len(words) == 2:
        sign_name = f"{joined} SIGN"
        if sign_name not in candidates:
            candidates.append(sign_name)

        reversed_sign_name = f"{reversed_joined} SIGN"
        if reversed_sign_name not in candidates:
            candidates.append(reversed_sign_name)

    emoji = ""
    for candidate in candidates:
        emoji = _lookup_unicode_name(candidate)
        if emoji:
            break

    if emoji and tone:
        emoji += _lookup_unicode_name(tone)

    result = emoji or shortcode
    _emoji_shortcode_cache[shortcode] = result
    return result


def convert_emojis(text: str) -> str:
    text = str(text or "")
    if ":" not in text:
        return text

    return EMOJI_SHORTCODE_RE.sub(
        lambda match: _emoji_from_shortcode(match.group(0)),
        text,
    )


def to_color(value, fallback) -> QColor:
    if isinstance(value, QColor):
        return value
    color = QColor(value) if value else to_color(fallback, COLORS_RGBA["BLACK"])
    if not color.isValid():
        color = to_color(fallback, COLORS_RGBA["BLACK"])
    return color
