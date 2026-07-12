from __future__ import annotations

import hashlib
import html
import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytz
from PIL import Image, ImageDraw, ImageFont, ImageOps

from plugins.base_plugin.base_plugin import BasePlugin
from plugins.context_cache import write_context
from utils.app_utils import DEFAULT_FONT_FAMILY, get_available_font_names, get_font
from utils.image_utils import text_width
from utils.http_client import get_http_session
from utils.theme_utils import get_theme_palette

logger = logging.getLogger(__name__)

DICTIONARY_API_URL = "https://api.dictionaryapi.dev/api/v2/entries/en/{word}"
WIKIQUOTE_QOTD_URL = "https://wq-quote-of-the-day-parser.toolforge.org/api/quote_of_the_day"
WIKIQUOTE_QOTD_DATE_URL = "https://wq-quote-of-the-day-parser.toolforge.org/api/quotes/{date}"
WIKIQUOTE_DAY_RAW_URL = "https://en.wikiquote.org/w/index.php?title=Wikiquote:Quote_of_the_day/{day_slug}&action=raw"
WIKIQUOTE_DAY_PAGE_URL = "https://en.wikiquote.org/wiki/Wikiquote:Quote_of_the_day/{day_slug}"
REQUEST_HEADERS = {"User-Agent": "InkyPi Daily Word Quote/1.0"}
CACHE_SCHEMA_VERSION = "daily-word-quote-v4"
DEFAULT_FONT = DEFAULT_FONT_FAMILY
DEFAULT_TIMEZONE = "America/Los_Angeles"
TITLE_WORDMARK_IMAGE = "title_wordmark.png"
TITLE_WORDMARK_SIZE = (224, 48)
QUOTE_TEXT_MAX_LEN = 360
QUOTE_SOURCE_MAX_LEN = 520
WIKIQUOTE_MONTH_NAMES = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)
DISPLAY_TRANSLATION = str.maketrans({
    "\u201c": '"',
    "\u201d": '"',
    "\u2018": "'",
    "\u2019": "'",
    "\u2014": "-",
    "\u2013": "-",
    "\u2026": "...",
})
QUOTE_WRAPPER_PAIRS = (
    ('"', '"'),
    ("'", "'"),
    ("\u00ab", "\u00bb"),
    ("\u2039", "\u203a"),
    ("\u300c", "\u300d"),
    ("\u300e", "\u300f"),
)
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
        "example": "The short quote was quiet but eloquent.",
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
        "example": "The quote drew him into a brief reverie.",
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

LOCAL_QUOTES = [
    {
        "text": "The unexamined life is not worth living.",
        "author": "Socrates",
        "topic": "reflection",
    },
    {
        "text": "I think, therefore I am.",
        "author": "Rene Descartes",
        "topic": "reason",
    },
    {
        "text": "Brevity is the soul of wit.",
        "author": "William Shakespeare",
        "topic": "clarity",
    },
    {
        "text": "Knowledge is power.",
        "author": "Francis Bacon",
        "topic": "learning",
    },
    {
        "text": "Well begun is half done.",
        "author": "Aristotle",
        "topic": "action",
    },
    {
        "text": "No great mind has ever existed without a touch of madness.",
        "author": "Aristotle",
        "topic": "genius",
    },
    {
        "text": "Difficulties strengthen the mind, as labor does the body.",
        "author": "Seneca",
        "topic": "resilience",
    },
    {
        "text": "Luck is what happens when preparation meets opportunity.",
        "author": "Seneca",
        "topic": "preparation",
    },
    {
        "text": "Waste no more time arguing what a good person should be. Be one.",
        "author": "Marcus Aurelius",
        "topic": "character",
    },
    {
        "text": "The obstacle is the way.",
        "author": "Marcus Aurelius",
        "topic": "resilience",
    },
    {
        "text": "A journey of a thousand miles begins with a single step.",
        "author": "Lao Tzu",
        "topic": "progress",
    },
    {
        "text": "Nature does not hurry, yet everything is accomplished.",
        "author": "Lao Tzu",
        "topic": "patience",
    },
    {
        "text": "It does not matter how slowly you go, as long as you do not stop.",
        "author": "Confucius",
        "topic": "persistence",
    },
    {
        "text": "Our greatest glory is not in never falling, but in rising every time we fall.",
        "author": "Confucius",
        "topic": "resilience",
    },
    {
        "text": "Do what you can, with what you have, where you are.",
        "author": "Theodore Roosevelt",
        "topic": "action",
    },
    {
        "text": "The only thing we have to fear is fear itself.",
        "author": "Franklin D. Roosevelt",
        "topic": "courage",
    },
    {
        "text": "Genius is one percent inspiration and ninety-nine percent perspiration.",
        "author": "Thomas Edison",
        "topic": "work",
    },
    {
        "text": "Turn your wounds into wisdom.",
        "author": "Oprah Winfrey",
        "topic": "growth",
    },
    {
        "text": "Stay hungry, stay foolish.",
        "author": "Steve Jobs",
        "topic": "curiosity",
    },
    {
        "text": "Simplicity is the ultimate sophistication.",
        "author": "Leonardo da Vinci",
        "topic": "simplicity",
    },
    {
        "text": "Imagination is more important than knowledge.",
        "author": "Albert Einstein",
        "topic": "creativity",
    },
    {
        "text": "What you do speaks so loudly that I cannot hear what you say.",
        "author": "Ralph Waldo Emerson",
        "topic": "integrity",
    },
    {
        "text": "The secret of getting ahead is getting started.",
        "author": "Mark Twain",
        "topic": "momentum",
    },
    {
        "text": "It always seems impossible until it is done.",
        "author": "Nelson Mandela",
        "topic": "resolve",
    },
]


def _enabled(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    return value is True or str(value).lower() in {"1", "true", "on", "yes"}


def _normalized_text(value: Any) -> str:
    text = html.unescape(str(value or "")).translate(DISPLAY_TRANSLATION)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _clean_text(value: Any, max_len: int = 280) -> str:
    return _normalized_text(value)[:max_len]


def _strip_wrapping_quotes(text: str) -> str:
    cleaned = str(text or "").strip()
    while len(cleaned) >= 2:
        stripped = False
        for opener, closer in QUOTE_WRAPPER_PAIRS:
            if cleaned.startswith(opener) and cleaned.endswith(closer):
                inner = cleaned[len(opener):-len(closer)].strip()
                if inner:
                    cleaned = inner
                    stripped = True
                break
        if not stripped:
            break
    return cleaned


def _clean_quote_text(value: Any, max_len: int = QUOTE_TEXT_MAX_LEN) -> str:
    return _strip_wrapping_quotes(_normalized_text(value))[:max_len]


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
        params["available_fonts"] = get_available_font_names(default=DEFAULT_FONT)
        return params

    def generate_image(self, settings, device_config):
        settings = settings or {}
        dimensions = self._display_dimensions(device_config)
        now = self._localized_now(device_config)
        payload = self._daily_payload(settings, now)
        self._write_daily_word_context(payload, now)
        theme_context = settings.get("_inkypi_theme") or self.resolve_theme(settings, device_config, now=now)
        return self._render(dimensions, settings, payload, now, theme_context)

    def _display_dimensions(self, device_config):
        return self.get_dimensions(device_config)

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
        quote_entry = self._daily_quote(settings, now)
        cache_key = self._cache_key(date_key, word_entry["word"], settings)
        cache_file = self._cache_dir() / "daily.json"
        cached = _safe_json_load(cache_file, {})

        theme_render_only = _enabled(settings.get("_theme_render_only"))
        force_refresh = _enabled(settings.get("force_refresh")) and not theme_render_only
        if cached.get("cache_key") == cache_key and not force_refresh:
            cached["from_cache"] = True
            return cached
        if theme_render_only:
            raise RuntimeError("Theme-only redraw requires a warm Daily Word cache.")

        payload = {
            "cache_key": cache_key,
            "date": date_key,
            "word": word_entry,
            "quote": quote_entry,
            "sources": ["local word list", quote_entry.get("source") or "local golden sentences"],
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

        if (
            _enabled(settings.get("fetch_wikiquote"), default=True)
            and quote_entry.get("source") != "custom golden sentences"
        ):
            try:
                wikiquote = self._fetch_wikiquote_quote(date_key)
                if wikiquote:
                    payload["quote"] = wikiquote
                    payload["sources"][1] = wikiquote.get("source") or "Wikiquote QOTD"
            except Exception as exc:
                logger.warning("Wikiquote quote of the day fetch failed: %s", exc)
                payload["warnings"].append("quote offline")

        _safe_json_write(cache_file, payload)
        return payload

    def _write_daily_word_context(self, payload: dict[str, Any], now: datetime) -> None:
        word = payload.get("word") if isinstance(payload, dict) else {}
        quote = payload.get("quote") if isinstance(payload, dict) else {}
        if not isinstance(word, dict):
            word = {}
        if not isinstance(quote, dict):
            quote = {}

        word_text = _clean_text(word.get("word"), 60)
        definition = _clean_text(word.get("definition"), 180)
        quote_text = _clean_quote_text(quote.get("text"), 180)
        quote_author = _clean_text(quote.get("author"), 80)

        summary_parts = []
        if word_text:
            summary_parts.append(f"Daily word: {word_text}")
        if quote_text:
            byline = f" by {quote_author}" if quote_author else ""
            summary_parts.append(f"quote: {quote_text}{byline}")

        write_context(
            "daily_word_poem",
            {
                "kind": "word_quote",
                "source": "Daily Word & Quote",
                "summary": "; ".join(summary_parts)[:180],
                "facts": [
                    {"label": "word", "value": word_text},
                    {"label": "definition", "value": definition},
                    {"label": "quote", "value": quote_text},
                ],
                "items": [{
                    "word": word_text,
                    "part_of_speech": _clean_text(word.get("part_of_speech"), 40),
                    "definition": definition,
                    "example": _clean_text(word.get("example"), 120),
                    "quote": quote_text,
                    "author": quote_author,
                    "topic": _clean_text(quote.get("topic"), 40),
                    "quote_source": _clean_text(quote.get("source"), 80),
                    "quote_source_url": _clean_text(quote.get("source_url"), 180),
                }],
                "sources": payload.get("sources") or [],
                "from_cache": bool(payload.get("from_cache")),
            },
            generated_at=payload.get("generated_at") or now,
            ttl_seconds=24 * 60 * 60,
        )

    def _cache_key(self, date_key: str, word: str, settings: dict[str, Any]) -> str:
        raw = "\n".join([
            CACHE_SCHEMA_VERSION,
            date_key,
            word.lower(),
            str(_enabled(settings.get("fetch_dictionary"), default=True)),
            str(_enabled(settings.get("fetch_wikiquote"), default=True)),
            settings.get("word_list") or "",
            settings.get("quote_list") or "",
        ])
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _cache_dir(self) -> Path:
        return self.cache_dir(leaf="cache", create=True)

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

    def _fetch_wikiquote_quote(self, date_key: str) -> dict[str, str]:
        session = get_http_session()
        urls = [
            WIKIQUOTE_QOTD_DATE_URL.format(date=quote(date_key)),
            WIKIQUOTE_QOTD_URL,
        ]
        last_error: Exception | None = None

        for url in urls:
            try:
                response = session.get(url, timeout=10, headers=REQUEST_HEADERS)
                if response.status_code == 404:
                    last_error = RuntimeError(f"Wikiquote API returned 404 for {url}")
                    continue
                response.raise_for_status()
                return self._parse_wikiquote_quote(response.json())
            except Exception as exc:
                last_error = exc

        try:
            return self._fetch_wikiquote_day_quote(date_key, session)
        except Exception as exc:
            if last_error:
                raise RuntimeError(f"{last_error}; official Wikiquote date page failed: {exc}") from exc
            raise

    def _fetch_wikiquote_day_quote(self, date_key: str, session=None) -> dict[str, str]:
        session = session or get_http_session()
        day_slug = self._wikiquote_day_slug(date_key)
        raw_url = WIKIQUOTE_DAY_RAW_URL.format(day_slug=quote(day_slug, safe="_"))
        source_url = WIKIQUOTE_DAY_PAGE_URL.format(day_slug=quote(day_slug, safe="_"))

        response = session.get(raw_url, timeout=10, headers=REQUEST_HEADERS)
        response.raise_for_status()
        return self._parse_wikiquote_day_raw(response.text, source_url, date_key)

    def _wikiquote_day_slug(self, date_key: str) -> str:
        date_value = datetime.strptime(date_key, "%Y-%m-%d")
        month = WIKIQUOTE_MONTH_NAMES[date_value.month - 1]
        return f"{month}_{date_value.day}"

    def _parse_wikiquote_quote(self, data: Any) -> dict[str, str]:
        if isinstance(data, list) and data:
            data = data[0]
        if not isinstance(data, dict):
            raise RuntimeError("Wikiquote response is not a JSON object.")

        quote_text = _clean_quote_text(data.get("quote") or data.get("text") or data.get("content"))
        author = _clean_text(data.get("author") or data.get("attribution") or "Wikiquote", 80)
        featured_date = _clean_text(data.get("featured_date") or data.get("date"), 20)
        if not quote_text:
            raise RuntimeError("Wikiquote response has no quote text.")

        return {
            "text": quote_text,
            "author": author or "Wikiquote",
            "topic": "Wikiquote QOTD",
            "featured_date": featured_date,
            "source": "Wikiquote QOTD",
            "source_url": "https://en.wikiquote.org/wiki/Wikiquote:Quote_of_the_day",
        }

    def _parse_wikiquote_day_raw(self, data: Any, source_url: str, date_key: str) -> dict[str, str]:
        if not isinstance(data, str) or not data.strip():
            raise RuntimeError("Wikiquote date page is empty.")

        raw = html.unescape(data)
        raw = raw.split("{{QoDfooter", 1)[0]
        raw = re.sub(r"\[\[(?:File|Image):[^\]]+\]\]", " ", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\{\{[^{}]*\}\}", " ", raw)
        raw = re.sub(r"\[\[(?:[^|\]]+\|)?([^\]]+)\]\]", r"\1", raw)
        raw = re.sub(r"<br\s*/?>", "\n", raw, flags=re.IGNORECASE)

        paragraphs = re.findall(r"<p[^>]*>(.*?)</p>", raw, flags=re.IGNORECASE | re.DOTALL)
        if paragraphs:
            parts = [_clean_text(re.sub(r"<[^>]+>", " ", part), QUOTE_SOURCE_MAX_LEN) for part in paragraphs]
        else:
            text = re.sub(r"<[^>]+>", " ", raw)
            text = re.sub(r"^\s*(?:\{\||\|\}|!|\|[-}]?|\|\s*align=.*)$", " ", text, flags=re.MULTILINE)
            parts = [_clean_text(part, QUOTE_SOURCE_MAX_LEN) for part in text.splitlines()]

        parts = [part for part in parts if part and not part.lower().startswith("image")]
        joined = "\n".join(parts)
        match = re.search(r"(?P<quote>.+?)\s*~\s*(?P<author>[^~\n]+)\s*~", joined, flags=re.DOTALL)
        if match:
            quote_text = _clean_quote_text(match.group("quote"))
            author = _clean_text(match.group("author"), 80)
        else:
            quote_text = _clean_quote_text(parts[0] if parts else "")
            author = "Wikiquote"

        if not quote_text:
            raise RuntimeError("Wikiquote date page has no quote text.")

        return {
            "text": quote_text,
            "author": author or "Wikiquote",
            "topic": "Wikiquote QOTD",
            "featured_date": date_key,
            "source": "Wikiquote QOTD",
            "source_url": source_url,
        }

    def _daily_quote(self, settings, now: datetime) -> dict[str, str]:
        custom_quotes = self._custom_quotes(settings.get("quote_list"))
        quotes = custom_quotes or LOCAL_QUOTES
        quote = quotes[now.date().toordinal() % len(quotes)]
        return {
            "text": _clean_quote_text(quote.get("text")),
            "author": _clean_text(quote.get("author") or "Unknown", 80),
            "topic": _clean_text(quote.get("topic") or "golden sentence", 40),
            "source": _clean_text(quote.get("source") or ("custom golden sentences" if custom_quotes else "local golden sentences"), 80),
            "source_url": _clean_text(quote.get("source_url"), 180),
        }

    def _custom_quotes(self, text: Any) -> list[dict[str, str]]:
        quotes = []
        seen = set()
        for line in str(text or "").splitlines():
            line = _clean_text(line, QUOTE_SOURCE_MAX_LEN)
            if not line:
                continue

            parts = re.split(r"\s+-\s+|\s+--\s+", line, maxsplit=1)
            quote_text = _clean_quote_text(parts[0])
            author = _clean_text(parts[1], 80) if len(parts) > 1 else "Custom"
            if not quote_text:
                continue

            key = quote_text.lower()
            if key in seen:
                continue
            seen.add(key)
            quotes.append({
                "text": quote_text,
                "author": author or "Custom",
                "topic": "custom",
                "source": "custom golden sentences",
            })
        return quotes

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

        if not self._draw_title_wordmark(image, left, top - 1, TITLE_WORDMARK_SIZE, bg, accent):
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
        self._render_quote_panel(
            draw,
            payload.get("quote") or {},
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
        injected_theme = settings.get("_inkypi_theme") if isinstance(settings, dict) else None
        if isinstance(injected_theme, dict) and isinstance(injected_theme.get("palette"), dict):
            palette = injected_theme["palette"]
            mode = injected_theme.get("mode", "day")
            theme_label = "MIDNIGHT READING" if mode == "night" else "DAY READING"
            return (
                palette["background"],
                palette["ink"],
                palette["accent"],
                palette["muted"],
                palette["rule"],
                theme_label,
            )

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

    def _draw_title_wordmark(self, canvas, x, y, size, background, accent):
        source = self._load_title_wordmark()
        if source is None:
            return False

        try:
            target_w, target_h = [int(value) for value in size]
            resample = Image.Resampling.LANCZOS if hasattr(Image, "Resampling") else Image.LANCZOS
            art = ImageOps.contain(source.copy(), (target_w, target_h), method=resample)
            art = self._prepare_title_wordmark(art, background, accent)
            layer = Image.new("RGBA", (target_w, target_h), (0, 0, 0, 0))
            layer.alpha_composite(art, ((target_w - art.width) // 2, (target_h - art.height) // 2))
            canvas.paste(layer.convert("RGB"), (int(x), int(y)), layer.getchannel("A"))
            return True
        except Exception as exc:
            logger.warning("Daily Word title wordmark unavailable: %s", exc)
            return False

    def _title_wordmark_file(self):
        return Path(__file__).with_name(TITLE_WORDMARK_IMAGE)

    def _load_title_wordmark(self):
        path = self._title_wordmark_file()
        if not path.is_file():
            return None
        try:
            return Image.open(path).convert("RGBA")
        except Exception as exc:
            logger.warning("Failed to load Daily Word title wordmark %s: %s", path, exc)
            return None

    @staticmethod
    def _prepare_title_wordmark(source, background, accent):
        if sum(tuple(int(value) for value in background[:3])) > 120:
            return source

        accent_rgb = tuple(int(value) for value in accent[:3])
        wordmark = source.convert("RGBA")
        pixels = wordmark.load()
        for y in range(wordmark.height):
            for x in range(wordmark.width):
                r, g, b, a = pixels[x, y]
                if a:
                    pixels[x, y] = accent_rgb + (a,)
        return wordmark

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

    def _render_quote_panel(self, draw, quote, box, font_family, text, accent, muted):
        x, y, max_width, bottom = box
        label_font = self._load_font(font_family, 15, "bold")
        author_font = self._load_font(font_family, 17)
        topic_font = self._load_font(font_family, 13, "bold")

        draw.text((x, y), "GOLDEN SENTENCE", font=label_font, fill=accent)
        y += self._line_height(draw, label_font) + max(12, int((bottom - y) * 0.035))

        quote_text = _clean_quote_text(quote.get("text") or "Keep going.")
        quoted = f'"{quote_text}"'
        topic = _clean_text(quote.get("topic"), 40).upper()
        author_reserved = self._line_height(draw, author_font)
        topic_reserved = self._line_height(draw, topic_font) if topic else 0
        reserved_height = author_reserved + topic_reserved + 28
        available_height = max(70, bottom - y - reserved_height)
        quote_font = self._fit_quote_font(draw, quoted, font_family, max_width, available_height)
        line_height = self._quote_line_height(draw, quote_font)

        for line in self._wrap_text(draw, quoted, quote_font, max_width):
            if y + line_height > bottom - reserved_height:
                break
            draw.text((x, y), line, font=quote_font, fill=text)
            y += line_height
        y += max(8, line_height // 5)

        author = "- " + _clean_text(quote.get("author") or "Unknown", 80)
        draw.text((x, y), self._fit_single_line(draw, author, author_font, max_width), font=author_font, fill=muted)
        y += self._line_height(draw, author_font) + 8

        if topic and y + self._line_height(draw, topic_font) <= bottom - 22:
            draw.text((x, y), self._fit_single_line(draw, topic, topic_font, max_width), font=topic_font, fill=accent)

    def _fit_quote_font(self, draw, text, font_family, max_width, max_height):
        max_size = min(92, max(42, int(max_height * 0.62)))
        for size in range(max_size, 11, -1):
            font = self._load_font(font_family, size)
            wrapped = self._wrap_text(draw, text, font, max_width)
            needed = len(wrapped) * self._quote_line_height(draw, font)
            if needed <= max_height:
                return font
        return self._load_font(font_family, 11)

    def _quote_line_height(self, draw, font):
        return max(11, int(self._text_height(draw, "Ag", font) * 1.14))

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
            font = get_font(font_family or DEFAULT_FONT, int(size), weight)
            if font:
                return font
        except OSError as exc:
            logger.warning("Could not load font %s (%s): %s", font_family, weight, exc)
        return ImageFont.load_default()

    def _line_height(self, draw, font):
        return max(12, int(self._text_height(draw, "Ag", font) * 1.36))

    def _text_width(self, draw, text, font):
        return text_width(draw, str(text or ""), font)

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
