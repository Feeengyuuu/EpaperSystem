from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytz
from PIL import Image, ImageDraw, ImageFont

from plugins.base_plugin.base_plugin import BasePlugin
from plugins.context_cache import write_context
from utils.app_utils import get_font, get_fonts
from utils.http_client import get_http_session
from utils.theme_utils import get_theme_context, get_theme_palette

logger = logging.getLogger(__name__)

DICTIONARY_API_URL = "https://api.dictionaryapi.dev/api/v2/entries/en/{word}"
POETRYDB_RANDOM_URL = "https://poetrydb.org/random"
REQUEST_HEADERS = {"User-Agent": "InkyPi Daily Word Poem/1.0"}
CACHE_SCHEMA_VERSION = "daily-word-poem-v2"
DEFAULT_FONT = "Jost"
DEFAULT_TIMEZONE = "America/Los_Angeles"
DISPLAY_TRANSLATION = str.maketrans({
    "\u201c": '"',
    "\u201d": '"',
    "\u2018": "'",
    "\u2019": "'",
    "\u2014": "-",
    "\u2013": "-",
    "\u2026": "...",
})
COMMON_ENGLISH_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "for", "from",
    "had", "has", "have", "he", "her", "his", "i", "in", "is", "it", "me",
    "my", "not", "of", "on", "or", "our", "she", "so", "that", "the", "their",
    "them", "there", "they", "this", "to", "was", "we", "were", "what", "when",
    "where", "who", "will", "with", "you", "your", "first", "second", "third",
    "line",
}

LOCAL_WORDS = [
    {
        "word": "luminous",
        "phonetic": "/loo-muh-nuhs/",
        "part_of_speech": "adjective",
        "definition": "Full of light; bright or shining.",
        "example": "The luminous screen softened the room before dawn.",
    },
    {
        "word": "resilient",
        "phonetic": "/ri-zil-yuhnt/",
        "part_of_speech": "adjective",
        "definition": "Able to recover quickly after difficulty.",
        "example": "A resilient plan keeps working when the network drops.",
    },
    {
        "word": "lucid",
        "phonetic": "/loo-sid/",
        "part_of_speech": "adjective",
        "definition": "Clear, easy to understand, or mentally sharp.",
        "example": "Her lucid note made the problem obvious.",
    },
    {
        "word": "serene",
        "phonetic": "/suh-reen/",
        "part_of_speech": "adjective",
        "definition": "Calm, peaceful, and untroubled.",
        "example": "The morning felt serene after the rain stopped.",
    },
    {
        "word": "tenacious",
        "phonetic": "/tuh-nay-shuhs/",
        "part_of_speech": "adjective",
        "definition": "Holding firmly to a goal, idea, or task.",
        "example": "A tenacious engineer keeps testing the real path.",
    },
    {
        "word": "ephemeral",
        "phonetic": "/ih-fem-er-uhl/",
        "part_of_speech": "adjective",
        "definition": "Lasting for only a short time.",
        "example": "The cloud shadow was ephemeral, gone in seconds.",
    },
    {
        "word": "meticulous",
        "phonetic": "/muh-tik-yuh-luhs/",
        "part_of_speech": "adjective",
        "definition": "Very careful and precise about details.",
        "example": "The meticulous layout left no text clipped.",
    },
    {
        "word": "eloquent",
        "phonetic": "/el-uh-kwuhnt/",
        "part_of_speech": "adjective",
        "definition": "Expressing meaning clearly and gracefully.",
        "example": "The short poem was quiet but eloquent.",
    },
    {
        "word": "pragmatic",
        "phonetic": "/prag-mat-ik/",
        "part_of_speech": "adjective",
        "definition": "Focused on practical results rather than theory.",
        "example": "The pragmatic fix avoided a fragile dependency.",
    },
    {
        "word": "austere",
        "phonetic": "/aw-steer/",
        "part_of_speech": "adjective",
        "definition": "Plain, simple, and without decoration.",
        "example": "The austere black text suited the e-paper display.",
    },
    {
        "word": "verdant",
        "phonetic": "/vur-duhnt/",
        "part_of_speech": "adjective",
        "definition": "Green with growing plants.",
        "example": "A verdant hillside filled the edge of the sketch.",
    },
    {
        "word": "quietude",
        "phonetic": "/kwy-uh-tood/",
        "part_of_speech": "noun",
        "definition": "A state of stillness, calm, or rest.",
        "example": "The room settled into quietude after midnight.",
    },
    {
        "word": "clarity",
        "phonetic": "/klar-uh-tee/",
        "part_of_speech": "noun",
        "definition": "The quality of being clear and easy to perceive.",
        "example": "Clarity matters more than clever wording.",
    },
    {
        "word": "solace",
        "phonetic": "/sol-is/",
        "part_of_speech": "noun",
        "definition": "Comfort during sadness or difficulty.",
        "example": "He found solace in a familiar line of poetry.",
    },
    {
        "word": "wanderlust",
        "phonetic": "/won-der-luhst/",
        "part_of_speech": "noun",
        "definition": "A strong desire to travel or explore.",
        "example": "The old map woke a little wanderlust.",
    },
    {
        "word": "resolve",
        "phonetic": "/ri-zolv/",
        "part_of_speech": "noun",
        "definition": "Firm determination to do something.",
        "example": "Her resolve held through the long debugging session.",
    },
    {
        "word": "evoke",
        "phonetic": "/ih-vohk/",
        "part_of_speech": "verb",
        "definition": "To bring a feeling, memory, or image to mind.",
        "example": "The first line can evoke an entire season.",
    },
    {
        "word": "illuminate",
        "phonetic": "/ih-loo-muh-nayt/",
        "part_of_speech": "verb",
        "definition": "To light up or make something clear.",
        "example": "A good example can illuminate a hard word.",
    },
    {
        "word": "murmur",
        "phonetic": "/mur-mer/",
        "part_of_speech": "verb",
        "definition": "To speak or sound softly and continuously.",
        "example": "The distant traffic seemed to murmur below.",
    },
    {
        "word": "contemplate",
        "phonetic": "/kon-tuhm-playt/",
        "part_of_speech": "verb",
        "definition": "To think carefully and calmly about something.",
        "example": "She paused to contemplate the final sentence.",
    },
    {
        "word": "coalesce",
        "phonetic": "/koh-uh-les/",
        "part_of_speech": "verb",
        "definition": "To come together and form one whole.",
        "example": "Several ideas began to coalesce into a plan.",
    },
    {
        "word": "steadfast",
        "phonetic": "/sted-fast/",
        "part_of_speech": "adjective",
        "definition": "Firm, loyal, and not easily changed.",
        "example": "The steadfast routine made each morning easier.",
    },
    {
        "word": "radiant",
        "phonetic": "/ray-dee-uhnt/",
        "part_of_speech": "adjective",
        "definition": "Sending out light, joy, or energy.",
        "example": "The radiant sky made the window glow.",
    },
    {
        "word": "nimble",
        "phonetic": "/nim-buhl/",
        "part_of_speech": "adjective",
        "definition": "Quick, light, and able to move or adapt easily.",
        "example": "A nimble design handles small screens well.",
    },
    {
        "word": "savor",
        "phonetic": "/say-ver/",
        "part_of_speech": "verb",
        "definition": "To enjoy something slowly and fully.",
        "example": "Take a moment to savor a well-chosen word.",
    },
    {
        "word": "glisten",
        "phonetic": "/glis-uhn/",
        "part_of_speech": "verb",
        "definition": "To shine with small flashes of reflected light.",
        "example": "Rain made the pavement glisten under the lamp.",
    },
    {
        "word": "reverie",
        "phonetic": "/rev-uh-ree/",
        "part_of_speech": "noun",
        "definition": "A pleasant state of dreamy thought.",
        "example": "The poem drew him into a brief reverie.",
    },
    {
        "word": "cadence",
        "phonetic": "/kay-duhns/",
        "part_of_speech": "noun",
        "definition": "A rhythm or flow in sound, speech, or movement.",
        "example": "The cadence of the lines made them easy to remember.",
    },
    {
        "word": "tranquil",
        "phonetic": "/trang-kwil/",
        "part_of_speech": "adjective",
        "definition": "Free from disturbance; peaceful.",
        "example": "The lake looked tranquil in the pale morning.",
    },
    {
        "word": "kindle",
        "phonetic": "/kin-duhl/",
        "part_of_speech": "verb",
        "definition": "To start a fire, feeling, or idea.",
        "example": "One surprising phrase can kindle curiosity.",
    },
]

FALLBACK_POEMS = [
    {
        "title": "Hope is the thing with feathers",
        "author": "Emily Dickinson",
        "lines": [
            "Hope is the thing with feathers",
            "That perches in the soul,",
            "And sings the tune without the words,",
            "And never stops at all,",
        ],
    },
    {
        "title": "A Red, Red Rose",
        "author": "Robert Burns",
        "lines": [
            "O my Luve is like a red, red rose",
            "That's newly sprung in June;",
            "O my Luve is like the melodie",
            "That's sweetly played in tune.",
        ],
    },
    {
        "title": "The Lake Isle of Innisfree",
        "author": "W. B. Yeats",
        "lines": [
            "I will arise and go now, and go to Innisfree,",
            "And a small cabin build there, of clay and wattles made;",
            "Nine bean-rows will I have there, a hive for the honey-bee,",
            "And live alone in the bee-loud glade.",
        ],
    },
    {
        "title": "Sonnet 18",
        "author": "William Shakespeare",
        "lines": [
            "Shall I compare thee to a summer's day?",
            "Thou art more lovely and more temperate:",
            "Rough winds do shake the darling buds of May,",
            "And summer's lease hath all too short a date;",
        ],
    },
]


def _enabled(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    return value is True or str(value).lower() in {"1", "true", "on", "yes"}


def _parse_int(value: Any, default: int, low: int, high: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(low, min(high, parsed))


def _clean_text(value: Any, max_len: int = 280) -> str:
    text = str(value or "").translate(DISPLAY_TRANSLATION)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_len]


def _safe_json_load(path: Path, default: Any) -> Any:
    try:
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Could not read daily word cache %s: %s", path, exc)
    return default


def _safe_json_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    except Exception:
        path.write_text(text, encoding="utf-8")
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass


class DailyWordPoem(BasePlugin):
    def generate_settings_template(self):
        params = super().generate_settings_template()
        params["style_settings"] = True
        params["available_fonts"] = sorted({
            f.get("name") or f.get("font_family")
            for f in get_fonts()
            if f.get("name") or f.get("font_family")
        })
        if DEFAULT_FONT not in params["available_fonts"]:
            params["available_fonts"].append(DEFAULT_FONT)
        return params

    def generate_image(self, settings, device_config):
        settings = settings or {}
        dimensions = self._display_dimensions(device_config)
        now = self._localized_now(device_config)
        payload = self._daily_payload(settings, now)
        self._write_daily_word_context(payload, now)
        theme_context = get_theme_context(device_config, now=now)
        return self._render(dimensions, settings, payload, now, theme_context)

    def _display_dimensions(self, device_config):
        dimensions = device_config.get_resolution()
        if device_config.get_config("orientation") == "vertical":
            dimensions = dimensions[::-1]
        return dimensions

    def _localized_now(self, device_config):
        tz_name = device_config.get_config("timezone") or DEFAULT_TIMEZONE
        try:
            tz = pytz.timezone(tz_name)
        except pytz.UnknownTimeZoneError:
            tz = pytz.timezone(DEFAULT_TIMEZONE)
        return datetime.now(tz)

    def _daily_payload(self, settings, now: datetime) -> dict[str, Any]:
        date_key = now.strftime("%Y-%m-%d")
        word_entry = self._daily_word(settings, now)
        poem_line_limit = _parse_int(settings.get("poem_line_limit"), 4, 2, 8)
        cache_key = self._cache_key(date_key, word_entry["word"], settings, poem_line_limit)
        cache_file = self._cache_dir() / "daily.json"
        cached = _safe_json_load(cache_file, {})

        if cached.get("cache_key") == cache_key and not _enabled(settings.get("force_refresh")):
            cached["from_cache"] = True
            return cached

        payload = {
            "cache_key": cache_key,
            "date": date_key,
            "word": word_entry,
            "poem": self._fallback_poem(now, poem_line_limit),
            "sources": ["local word list", "local poem fallback"],
            "warnings": [],
            "from_cache": False,
            "generated_at": now.isoformat(),
        }

        if _enabled(settings.get("fetch_dictionary"), default=True):
            try:
                enriched = self._fetch_dictionary_entry(word_entry["word"])
                if enriched:
                    payload["word"] = {**word_entry, **enriched}
                    payload["sources"][0] = "Free Dictionary API"
            except Exception as exc:
                logger.warning("Daily word definition fetch failed: %s", exc)
                payload["warnings"].append("definition offline")

        if _enabled(settings.get("fetch_poem"), default=True):
            try:
                poem = self._fetch_poem(poem_line_limit)
                if poem:
                    payload["poem"] = poem
                    payload["sources"][1] = "PoetryDB"
            except Exception as exc:
                logger.warning("Daily poem fetch failed: %s", exc)
                payload["warnings"].append("poem offline")

        _safe_json_write(cache_file, payload)
        return payload

    def _write_daily_word_context(self, payload: dict[str, Any], now: datetime) -> None:
        word = payload.get("word") if isinstance(payload, dict) else {}
        poem = payload.get("poem") if isinstance(payload, dict) else {}
        if not isinstance(word, dict):
            word = {}
        if not isinstance(poem, dict):
            poem = {}

        word_text = _clean_text(word.get("word"), 60)
        definition = _clean_text(word.get("definition"), 180)
        poem_title = _clean_text(poem.get("title"), 100)
        poem_author = _clean_text(poem.get("author"), 80)
        poem_lines = [_clean_text(line, 120) for line in (poem.get("lines") or []) if _clean_text(line)]

        summary_parts = []
        if word_text:
            summary_parts.append(f"Daily word: {word_text}")
        if poem_title:
            byline = f" by {poem_author}" if poem_author else ""
            summary_parts.append(f"poem: {poem_title}{byline}")

        write_context(
            "daily_word_poem",
            {
                "kind": "word_poem",
                "source": "Daily Word Poem",
                "summary": "; ".join(summary_parts)[:180],
                "facts": [
                    {"label": "word", "value": word_text},
                    {"label": "definition", "value": definition},
                    {"label": "poem", "value": poem_title},
                ],
                "items": [{
                    "word": word_text,
                    "part_of_speech": _clean_text(word.get("part_of_speech"), 40),
                    "definition": definition,
                    "example": _clean_text(word.get("example"), 120),
                    "title": poem_title,
                    "author": poem_author,
                    "line": poem_lines[0] if poem_lines else "",
                }],
                "sources": payload.get("sources") or [],
                "from_cache": bool(payload.get("from_cache")),
            },
            generated_at=payload.get("generated_at") or now,
            ttl_seconds=24 * 60 * 60,
        )

    def _cache_key(self, date_key: str, word: str, settings: dict[str, Any], poem_line_limit: int) -> str:
        raw = "\n".join([
            CACHE_SCHEMA_VERSION,
            date_key,
            word.lower(),
            str(poem_line_limit),
            str(_enabled(settings.get("fetch_dictionary"), default=True)),
            str(_enabled(settings.get("fetch_poem"), default=True)),
            settings.get("word_list") or "",
        ])
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _cache_dir(self) -> Path:
        path = Path(self.get_plugin_dir("cache"))
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _daily_word(self, settings, now: datetime) -> dict[str, str]:
        custom_words = self._custom_words(settings.get("word_list"))
        if custom_words:
            word = custom_words[now.date().toordinal() % len(custom_words)]
            return {
                "word": word,
                "phonetic": "",
                "part_of_speech": "",
                "definition": "Custom daily word.",
                "example": "Add a Free Dictionary lookup or local note for richer detail.",
            }

        return LOCAL_WORDS[now.date().toordinal() % len(LOCAL_WORDS)]

    def _custom_words(self, text: Any) -> list[str]:
        words = []
        seen = set()
        for line in str(text or "").splitlines():
            word = re.sub(r"[^A-Za-z -]", "", line).strip().lower()
            word = re.sub(r"\s+", " ", word)
            if not word or word in seen:
                continue
            seen.add(word)
            words.append(word)
        return words

    def _fetch_dictionary_entry(self, word: str) -> dict[str, str]:
        url = DICTIONARY_API_URL.format(word=quote(word))
        session = get_http_session()
        response = session.get(url, timeout=8, headers=REQUEST_HEADERS)
        response.raise_for_status()
        return self._parse_dictionary_entry(response.json(), word)

    def _parse_dictionary_entry(self, data: Any, fallback_word: str) -> dict[str, str]:
        if not isinstance(data, list) or not data:
            raise RuntimeError("Dictionary response is empty.")

        entry = data[0]
        phonetic = _clean_text(entry.get("phonetic"), 80)
        for item in entry.get("phonetics") or []:
            if not phonetic and item.get("text"):
                phonetic = _clean_text(item.get("text"), 80)
                break

        selected_meaning = {}
        selected_definition = {}
        for meaning in entry.get("meanings") or []:
            definitions = meaning.get("definitions") or []
            if definitions:
                selected_meaning = meaning
                selected_definition = definitions[0]
                break

        if not selected_definition:
            raise RuntimeError("Dictionary response has no definitions.")

        example = _clean_text(selected_definition.get("example"), 180)
        if not example:
            example = _clean_text((selected_meaning.get("synonyms") or [""])[0], 180)
            if example:
                example = f"Related word: {example}."

        return {
            "word": _clean_text(entry.get("word") or fallback_word, 60),
            "phonetic": phonetic,
            "part_of_speech": _clean_text(selected_meaning.get("partOfSpeech"), 40),
            "definition": _clean_text(selected_definition.get("definition"), 260),
            "example": example,
        }

    def _fetch_poem(self, line_limit: int) -> dict[str, Any]:
        session = get_http_session()
        response = session.get(POETRYDB_RANDOM_URL, timeout=10, headers=REQUEST_HEADERS)
        response.raise_for_status()
        return self._parse_poem_response(response.json(), line_limit)

    def _parse_poem_response(self, data: Any, line_limit: int) -> dict[str, Any]:
        if not isinstance(data, list) or not data:
            raise RuntimeError("PoetryDB response is empty.")

        poem = data[0]
        lines = self._select_poem_lines(poem.get("lines") or [], line_limit)

        if not lines:
            raise RuntimeError("PoetryDB response has no poem lines.")

        return {
            "title": _clean_text(poem.get("title") or "Untitled", 100),
            "author": _clean_text(poem.get("author") or "Unknown", 80),
            "lines": lines,
        }

    def _select_poem_lines(self, raw_lines, line_limit: int) -> list[str]:
        candidates = []
        for raw_line in raw_lines:
            line = _clean_text(raw_line, 120)
            if not line:
                continue
            if self._poem_line_is_displayable(line):
                candidates.append(line)
                if len(candidates) >= line_limit:
                    return candidates
            elif candidates:
                candidates = []
        return candidates[:line_limit]

    def _poem_line_is_displayable(self, line: str) -> bool:
        if not line:
            return False

        letters = re.findall(r"[A-Za-z]+", line)
        if not letters:
            return False

        letter_text = "".join(letters)
        upper = re.sub(r"[^A-Za-z ]", "", line).strip().upper()
        if len(letter_text) >= 6 and letter_text.isupper():
            return False
        if re.match(r"^(CANTO|BOOK|CHAPTER|PART|STANZA)\b", upper):
            return False
        if re.fullmatch(r"[IVXLCDM]+", upper.replace(" ", "")):
            return False

        tokens = [token.lower() for token in letters]
        common_count = sum(1 for token in tokens if token in COMMON_ENGLISH_WORDS)
        if len(tokens) >= 2 and common_count == 0:
            return False
        return True

    def _fallback_poem(self, now: datetime, line_limit: int) -> dict[str, Any]:
        poem = FALLBACK_POEMS[now.date().toordinal() % len(FALLBACK_POEMS)]
        return {
            "title": poem["title"],
            "author": poem["author"],
            "lines": poem["lines"][:line_limit],
        }

    def _render(self, dimensions, settings, payload, now, theme_context=None):
        width, height = dimensions
        bg, text, accent, muted, faint, theme_label = self._page_palette(settings, theme_context)

        image = Image.new("RGB", dimensions, bg)
        draw = ImageDraw.Draw(image)

        margin = max(18, int(min(width, height) * 0.055))
        top = margin
        left = margin
        right = width - margin
        bottom = height - margin
        content_width = right - left
        gutter = max(18, width // 32)
        left_width = int(content_width * 0.49)
        divider_x = left + left_width + gutter // 2
        right_x = divider_x + gutter
        right_width = right - right_x

        font_family = settings.get("font_family") or DEFAULT_FONT
        header_font = self._load_font(font_family, max(13, height // 32), "bold")
        small_font = self._load_font(font_family, max(12, height // 36))
        meta_font = self._load_font(font_family, max(11, height // 42))

        draw.text((left, top), "DAILY WORD", font=header_font, fill=accent)
        date_text = now.strftime("%b %d, %Y")
        date_w = self._text_width(draw, date_text, small_font)
        draw.text((right - date_w, top), date_text, font=small_font, fill=muted)
        label_w = self._text_width(draw, theme_label, meta_font)
        label_y = top + self._line_height(draw, small_font)
        draw.text((right - label_w, label_y), theme_label, font=meta_font, fill=accent)
        header_stack_height = max(
            self._line_height(draw, header_font),
            self._line_height(draw, small_font) + self._line_height(draw, meta_font),
        )
        header_y = top + header_stack_height + max(8, height // 70)
        draw.line((left, header_y, right, header_y), fill=faint, width=2)

        self._render_word_panel(
            draw,
            payload.get("word") or {},
            (left, header_y + max(14, height // 32), left_width, bottom),
            font_family,
            text,
            accent,
            muted,
        )

        draw.line((divider_x, header_y + 12, divider_x, bottom - 8), fill=faint, width=2)
        self._render_poem_panel(
            draw,
            payload.get("poem") or {},
            (right_x, header_y + max(14, height // 32), right_width, bottom),
            font_family,
            text,
            accent,
            muted,
        )

        source = " / ".join(payload.get("sources") or [])
        if payload.get("from_cache"):
            source += " / cache"
        warnings = ", ".join(payload.get("warnings") or [])
        footer = source
        if warnings:
            footer = f"{source} ({warnings})"
        footer = self._fit_single_line(draw, footer, meta_font, content_width)
        draw.text((left, bottom - self._line_height(draw, meta_font)), footer, font=meta_font, fill=muted)
        return image

    def _page_palette(self, settings, theme_context=None):
        palette = get_theme_palette(theme_context)
        mode = (theme_context or {}).get("mode", "day") if isinstance(theme_context, dict) else "day"

        if mode == "night":
            fallback_bg = palette["background"]
            fallback_text = palette["ink"]
            fallback_accent = palette["green"]
            theme_label = "MIDNIGHT READING"
        else:
            fallback_bg = palette["background"]
            fallback_text = (18, 18, 16)
            fallback_accent = palette["green"]
            theme_label = "DAY READING"

        bg = self._settings_color(settings, ("backgroundColor", "background_color"), fallback_bg)
        text = self._settings_color(settings, ("textColor", "text_color"), fallback_text)
        accent = self._settings_color(settings, ("accentColor", "highlight_color"), fallback_accent)
        muted_amount = 0.76 if mode == "night" else 0.68
        faint_amount = 0.32 if mode == "night" else 0.28
        muted = self._mix(text, bg, muted_amount)
        faint = self._mix(text, bg, faint_amount)
        return bg, text, accent, muted, faint, theme_label

    def _render_word_panel(self, draw, word, box, font_family, text, accent, muted):
        x, y, max_width, bottom = box
        word_text = _clean_text(word.get("word") or "word", 64)
        title_font = self._fit_single_text(draw, word_text, font_family, max_width, 78, 40, "bold")
        draw.text((x, y), word_text, font=title_font, fill=text)
        y += self._line_height(draw, title_font) + 4

        meta_bits = [self._display_phonetic(word.get("phonetic")), word.get("part_of_speech")]
        meta = "  ".join(_clean_text(bit, 80) for bit in meta_bits if bit)
        if meta:
            meta_font = self._load_font(font_family, 22)
            draw.text((x, y), self._fit_single_line(draw, meta, meta_font, max_width), font=meta_font, fill=accent)
            y += self._line_height(draw, meta_font) + 18

        label_font = self._load_font(font_family, 15, "bold")
        body_font = self._fit_wrapped_text(
            draw,
            _clean_text(word.get("definition"), 260),
            font_family,
            max_width,
            max(44, bottom - y - 116),
            28,
            18,
        )
        draw.text((x, y), "Definition", font=label_font, fill=accent)
        y += self._line_height(draw, label_font) + 6
        y = self._draw_wrapped(draw, _clean_text(word.get("definition"), 260), (x, y), body_font, max_width, bottom - 96, text)
        y += 12

        example = _clean_text(word.get("example"), 180)
        if example and y < bottom - 48:
            draw.text((x, y), "Example", font=label_font, fill=accent)
            y += self._line_height(draw, label_font) + 6
            example_font = self._load_font(font_family, 17)
            self._draw_wrapped(draw, example, (x, y), example_font, max_width, bottom - 28, muted)

    def _render_poem_panel(self, draw, poem, box, font_family, text, accent, muted):
        x, y, max_width, bottom = box
        label_font = self._load_font(font_family, 15, "bold")
        title_font = self._load_font(font_family, 24, "bold")
        author_font = self._load_font(font_family, 17)

        draw.text((x, y), "ENGLISH POEM", font=label_font, fill=accent)
        y += self._line_height(draw, label_font) + 8

        title = self._fit_single_line(draw, _clean_text(poem.get("title") or "Untitled", 100), title_font, max_width)
        draw.text((x, y), title, font=title_font, fill=text)
        y += self._line_height(draw, title_font) + 4

        author = "- " + _clean_text(poem.get("author") or "Unknown", 80)
        draw.text((x, y), self._fit_single_line(draw, author, author_font, max_width), font=author_font, fill=muted)
        y += self._line_height(draw, author_font) + 18

        lines = [_clean_text(line, 120) for line in poem.get("lines") or [] if _clean_text(line)]
        available_height = max(40, bottom - y - 26)
        poem_font = self._fit_poem_font(draw, lines, font_family, max_width, available_height)
        line_height = self._line_height(draw, poem_font)

        for raw_line in lines:
            for line in self._wrap_text(draw, raw_line, poem_font, max_width):
                if y + line_height > bottom - 22:
                    return
                draw.text((x, y), line, font=poem_font, fill=text)
                y += line_height
            y += max(2, line_height // 7)

    def _fit_poem_font(self, draw, lines, font_family, max_width, max_height):
        for size in range(24, 13, -1):
            font = self._load_font(font_family, size)
            wrapped = []
            for line in lines:
                wrapped.extend(self._wrap_text(draw, line, font, max_width))
            needed = len(wrapped) * self._line_height(draw, font) + max(0, len(lines) - 1) * 3
            if needed <= max_height:
                return font
        return self._load_font(font_family, 14)

    def _fit_wrapped_text(self, draw, text, font_family, max_width, max_height, max_size, min_size):
        for size in range(max_size, min_size - 1, -1):
            font = self._load_font(font_family, size)
            lines = self._wrap_text(draw, text, font, max_width)
            if len(lines) * self._line_height(draw, font) <= max_height:
                return font
        return self._load_font(font_family, min_size)

    def _fit_single_text(self, draw, text, font_family, max_width, max_size, min_size, weight="normal"):
        for size in range(max_size, min_size - 1, -2):
            font = self._load_font(font_family, size, weight)
            if self._text_width(draw, text, font) <= max_width:
                return font
        return self._load_font(font_family, min_size, weight)

    def _draw_wrapped(self, draw, text, position, font, max_width, max_bottom, fill):
        x, y = position
        line_height = self._line_height(draw, font)
        lines = self._wrap_text(draw, text, font, max_width)
        for index, line in enumerate(lines):
            if y + line_height > max_bottom:
                if index > 0:
                    return y
                line = self._fit_single_line(draw, line, font, max_width)
            draw.text((x, y), line, font=font, fill=fill)
            y += line_height
        return y

    def _wrap_text(self, draw, text, font, max_width):
        words = str(text or "").split()
        lines = []
        current = ""
        for word in words:
            candidate = word if not current else f"{current} {word}"
            if self._text_width(draw, candidate, font) <= max_width or not current:
                current = candidate
            else:
                lines.append(current)
                current = word
        if current:
            lines.append(current)
        return lines or [""]

    def _fit_single_line(self, draw, text, font, max_width):
        text = str(text or "")
        if self._text_width(draw, text, font) <= max_width:
            return text
        suffix = "..."
        while text and self._text_width(draw, text + suffix, font) > max_width:
            text = text[:-1]
        return text + suffix if text else suffix

    def _display_phonetic(self, phonetic):
        text = str(phonetic or "").strip()
        if not text:
            return ""
        replacements = {
            "ˈ": "'",
            "ˌ": ",",
            "ɪ": "i",
            "iː": "ee",
            "ʊ": "u",
            "uː": "oo",
            "ə": "uh",
            "ɜ": "ur",
            "ɜː": "ur",
            "ɛ": "e",
            "æ": "a",
            "ɑ": "a",
            "ɑː": "a",
            "ɔ": "o",
            "ɔː": "aw",
            "ɒ": "o",
            "ʌ": "uh",
            "θ": "th",
            "ð": "th",
            "ʃ": "sh",
            "ʒ": "zh",
            "ŋ": "ng",
            "j": "y",
        }
        for src, dst in sorted(replacements.items(), key=lambda item: len(item[0]), reverse=True):
            text = text.replace(src, dst)
        text = re.sub(r"[^A-Za-z0-9/'.,:; -]", "", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text

    def _load_font(self, font_family, size, weight="normal"):
        try:
            font = get_font(font_family, int(size), weight)
            if font:
                return font
        except OSError as exc:
            logger.warning("Could not load font %s (%s): %s", font_family, weight, exc)
        return ImageFont.load_default()

    def _line_height(self, draw, font):
        return max(12, int(self._text_height(draw, "Ag", font) * 1.36))

    def _text_width(self, draw, text, font):
        bbox = draw.textbbox((0, 0), str(text or ""), font=font)
        return bbox[2] - bbox[0]

    def _text_height(self, draw, text, font):
        bbox = draw.textbbox((0, 0), str(text or ""), font=font)
        return bbox[3] - bbox[1]

    def _settings_color(self, settings, keys, fallback):
        for key in keys:
            if settings.get(key):
                return self._parse_color(settings.get(key), fallback)
        return fallback

    def _parse_color(self, value, fallback):
        value = str(value or "").strip().lstrip("#")
        try:
            if len(value) == 3:
                value = "".join(ch * 2 for ch in value)
            if len(value) == 6:
                return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))
        except ValueError:
            return fallback
        return fallback

    def _mix(self, foreground, background, amount):
        return tuple(
            int(background[i] + (foreground[i] - background[i]) * amount)
            for i in range(3)
        )
