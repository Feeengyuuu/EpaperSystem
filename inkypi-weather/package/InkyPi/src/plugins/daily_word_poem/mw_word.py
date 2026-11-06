"""Merriam-Webster's Word of the Day from its keyless RSS feed (about 47 KB, the last ten days).

Each item gives the word, its respelled pronunciation (\\KWAHN-dum\\), part of speech, an editor's
definition, an example sentence and the "Did you know?" note on its story; the page shows them
in place of the local word list, so no dictionary lookup is needed for it.
"""

from __future__ import annotations

import html
import re

import feedparser


FEED_URL = "https://www.merriam-webster.com/wotd/feed/rss2"
SOURCE = "Merriam-Webster Word of the Day"
FEED_MAX_BYTES = 256 * 1024
STORY_MAX_LEN = 240
DAY = re.compile(r"-(\d{4}-\d{2}-\d{2})/?$")
HEADWORD = re.compile(
    r"<strong>[^<]*</strong>\s*(?:&#x2022;|•)\s*\\([^\\]{1,40})\\(?:&nbsp;|\s)*(?:&#x2022;|•)\s*"
    r"<em>([^<]{1,30})</em>")
# Paragraphs with no paragraph inside them (the feed nests its sections' <p>s in outer ones).
PARAGRAPH = re.compile(r"<p>((?:(?!<p>).)*?)</p>", re.S)


def _text(fragment):
    value = re.sub(r"<[^>]+>", " ", str(fragment or ""))
    value = html.unescape(value).replace(" ", " ")
    value = " ".join(value.split())
    return re.sub(r"\s+([,.;:?!”’)])", r"\1", value).replace("“ ", "“").replace("( ", "(")


def _story(text):
    """The note's opening sentences, as many as fit STORY_MAX_LEN (a first one too long ends on a
    word boundary with an ellipsis)."""
    sentences = re.findall(r".+?[.?!][”’\"]?(?=\s|$)", text) or [text]
    story = ""
    for sentence in sentences:
        candidate = f"{story} {sentence.strip()}".strip()
        if len(candidate) > STORY_MAX_LEN:
            break
        story = candidate
    if not story:
        story = text[:STORY_MAX_LEN - 1].rsplit(" ", 1)[0].rstrip(" ,;:") + "…"
    return story


def parse_item(summary, word, link):
    """One day's entry, or None when the item does not read as one."""
    word = " ".join(str(word or "").split())
    if not word or len(word) > 40:
        return None
    raw = str(summary or "")
    head = HEADWORD.search(raw)
    examples_at = raw.find("Examples:")
    story_at = raw.find("Did you know?")
    definition, example = "", ""
    for match in PARAGRAPH.finditer(raw[:examples_at] if examples_at >= 0 else raw):
        text = _text(match.group(1))
        if not text or text.startswith("Merriam-Webster's Word of the Day") or text.startswith("See the entry"):
            continue
        if text.startswith("//"):
            example = example or text.lstrip("/ ").strip()
        elif not definition:
            definition = text
    if not definition:
        return None
    story = ""
    if story_at >= 0:
        story = _story(" ".join(_text(match.group(1)) for match in PARAGRAPH.finditer(raw[story_at:])).strip())
    return {"word": word, "phonetic": f"/{head.group(1).strip()}/" if head else "",
            "part_of_speech": _text(head.group(2)) if head else "", "definition": definition[:260],
            "example": example[:180], "did_you_know": story, "source": SOURCE, "source_url": str(link or "")}


def parse_feed(text):
    """{YYYY-MM-DD: entry} for the feed's days."""
    words = {}
    for item in feedparser.parse(text or "").entries:
        link = str(item.get("link") or "")
        day = DAY.search(link)
        summary = item.get("summary") or ((item.get("content") or [{}])[0].get("value"))
        entry = parse_item(summary, item.get("title"), link) if day else None
        if entry:
            words[day.group(1)] = entry
    return words
