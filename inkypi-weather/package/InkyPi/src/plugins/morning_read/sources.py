"""晨读's feeds, each read through the plugin that owns it:

- the word and the quote of the day from daily_word_poem (Merriam-Webster's word of the day with
  its definition, Wikiquote's quote of the day; its local lists stand in when they fail);
- two short facts from daily_knowledge (uselessfacts and its other providers, its local notes
  standing in);
- today's comic strip from comic's feeds: the strips take turns by the day, and one whose feed
  is down gives its day to the next.

The two daily plugins keep their own day caches; this page keeps one more copy of what they gave
(source_book), so a cached redraw never asks them.
"""

from __future__ import annotations

from datetime import datetime
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from plugins.comic import comic_parser
from plugins.daily_knowledge.daily_knowledge import DailyKnowledge
from plugins.daily_word_poem.daily_word_poem import DailyWordPoem

PACIFIC = ZoneInfo("America/Los_Angeles")
USER_AGENT = "InkyPi MorningRead/1.0 (personal e-paper display)"
WEEKDAYS = "一二三四五六日"
# A day tries this many strips before the comic card gives up: each feed may take its full timeout.
COMIC_TRIES = 3
# Where the strips' pictures live; Dinosaur Comics' feed still names plain http.
COMIC_HOSTS = frozenset({
    "imgs.xkcd.com", "static.explosm.net", "www.smbc-comics.com", "smbc-comics.com", "pbfcomics.com",
    "www.questionablecontent.net", "questionablecontent.net", "poorlydrawnlines.com", "www.qwantz.com",
    "qwantz.com",
})
COMIC_HOST_SUFFIXES = (".media.tumblr.com",)


def device_zone(name):
    try:
        return ZoneInfo(str(name)) if name else PACIFIC
    except (ZoneInfoNotFoundError, ValueError):
        return PACIFIC


def local_now(now=None, zone=PACIFIC):
    value = now or datetime.now(zone)
    return value.replace(tzinfo=zone) if value.tzinfo is None else value.astimezone(zone)


# --- word and quote ----------------------------------------------------------------------------------

def fetch_word_and_quote(now):
    """{word, quote} for ``now``'s day as daily_word_poem gives them."""
    payload = DailyWordPoem({"id": "daily_word_poem"})._daily_payload({}, now)
    word, quote = payload.get("word") or {}, payload.get("quote") or {}
    keep_word = ("word", "phonetic", "part_of_speech", "definition", "example", "source")
    return {"word": {key: str(word.get(key) or "") for key in keep_word},
            "quote": {key: str(quote.get(key) or "") for key in ("text", "author", "source")}}


def valid_word_and_quote(data):
    return (isinstance(data, dict) and bool((data.get("word") or {}).get("word"))
            and bool((data.get("quote") or {}).get("text")))


# --- facts --------------------------------------------------------------------------------------------

def fetch_facts(device_config, now):
    payload = DailyKnowledge({"id": "daily_knowledge"})._daily_payload({}, device_config, now)
    facts = [{"text": " ".join(str(fact.get("text") or "").split()), "source": str(fact.get("source") or ""),
              "language": str(fact.get("language") or "")}
             for fact in payload.get("facts") or [] if isinstance(fact, dict)]
    return {"facts": [fact for fact in facts if fact["text"]][:2]}


def valid_facts(data):
    return isinstance(data, dict) and bool(data.get("facts"))


# --- comic --------------------------------------------------------------------------------------------

def safe_comic_image(value):
    """The strip's picture as an https URL on one of the strips' own hosts, else None."""
    url = str(value or "").strip()
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if not (host in COMIC_HOSTS or host.endswith(COMIC_HOST_SUFFIXES)):
        return None
    if parts.scheme == "http":
        parts = parts._replace(scheme="https")
    return urlunsplit(parts) if parts.scheme == "https" else None


def comic_order(day):
    """The strips in the order ``day`` tries them: one more each day, round the list."""
    names = list(comic_parser.COMICS)
    start = day.toordinal() % len(names)
    return names[start:] + names[:start]


def fetch_comic(now, tries=COMIC_TRIES, get_panel=comic_parser.get_panel):
    """{name, title, caption, image} of the day's strip, or the next one whose feed answers."""
    for name in comic_order(now.date())[:tries]:
        panel = get_panel(name) or {}
        image = safe_comic_image(panel.get("image_url"))
        if panel.get("placeholder") or not image:
            continue
        return {"name": name, "title": " ".join(str(panel.get("title") or "").split()),
                "caption": " ".join(str(panel.get("caption") or "").split()), "image": image}
    raise RuntimeError("no comic feed answered")


def valid_comic(data):
    return isinstance(data, dict) and bool(safe_comic_image(data.get("image"))) and bool(data.get("name"))
