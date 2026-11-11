"""你知道吗: the questions Chinese Wikipedia's front page asks about its newest articles.

Template:Dyk holds six of them, changed a few at a time through the day. In each, the words in
bold link the article that answers it (``<b><a title="U-267号潜艇">哪艘潜艇</a></b>是二战中…？``), and
the one the block's picture goes with ends "（图）". They are read to plain text: a fact is its
question, the article that answers it and whether the picture is its own.
"""

from __future__ import annotations

import html
import re
from urllib.parse import urlsplit

API_URL = "https://zh.wikipedia.org/w/api.php"
API_PARAMS = {"action": "parse", "page": "Template:Dyk", "prop": "text", "format": "json", "formatversion": "2",
              "variant": "zh-cn", "disablelimitreport": "1", "disableeditsection": "1"}
CACHE_FILE = "did_you_know.json"
CACHE_SCHEMA = 1
# The block changes a few times a day; a fact is still worth reading a few days later.
FRESH_SECONDS = 6 * 3600
USABLE_SECONDS = 3 * 86400
CLOCK_SKEW_SECONDS = 300
MAX_FACTS = 8
MAX_QUESTION_CHARS = 160
MAX_ANSWER_CHARS = 40
_BLOCK = re.compile(r'id=["\']column-dyk["\']')
_LIST = re.compile(r"<ul\b[^>]*>(.*?)</ul>", flags=re.IGNORECASE | re.DOTALL)
_ITEM = re.compile(r"<li\b[^>]*>(.*?)</li>", flags=re.IGNORECASE | re.DOTALL)
# The article a question is about: the link in bold, written either way round.
_ANSWER = re.compile(r'<b>\s*<a\b[^>]*?\btitle="([^"]+)"|<a\b[^>]*?\btitle="([^"]+)"[^>]*>\s*<b>', flags=re.IGNORECASE)
_PICTURED = re.compile(r"\s*[（(]\s*[左右上下]?[图圖]\s*[）)]")
_DISAMBIGUATION = re.compile(r"\s*[（(][^（）()]*[）)]\s*$")
_IMAGE = re.compile(r"<img\b([^>]*)>", flags=re.IGNORECASE)
_SRCSET_2X = re.compile(r'\bsrcset="([^"\s]+)\s+2x"', flags=re.IGNORECASE)
_SRC = re.compile(r'\bsrc="([^"]+)"', flags=re.IGNORECASE)


def safe_picture_url(address):
    """``address`` over https and without its query when it is on a wikimedia.org host, else ""."""
    address = str(address or "").strip()
    if address.startswith("//"):
        address = "https:" + address
    parts = urlsplit(address)
    host = str(parts.hostname or "")
    if parts.scheme != "https" or not (host == "wikimedia.org" or host.endswith(".wikimedia.org")):
        return ""
    return f"https://{host}{parts.path}"


def picture_url(fragment):
    """The block's picture from its <img>: the larger of the sizes it names (250 px), over https,
    without the tracking query, and only from a wikimedia.org host; "" otherwise."""
    tag = _IMAGE.search(str(fragment or ""))
    if not tag:
        return ""
    found = _SRCSET_2X.search(tag.group(1)) or _SRC.search(tag.group(1))
    return safe_picture_url(html.unescape(found.group(1))) if found else ""


def facts_from_html(html_text, html_to_text):
    """{"facts": [{"question", "answer", "pictured"}], "image_url"} from Template:Dyk's HTML;
    ``html_to_text`` turns a fragment into the page's plain text. ValueError when the block or
    its questions are missing (the template's documentation below it has lists of its own)."""
    text = str(html_text or "")
    block = _BLOCK.search(text)
    listed = _LIST.search(text, block.end()) if block else None
    if not listed:
        raise ValueError("Template:Dyk has no question list")
    facts = []
    for item in _ITEM.findall(listed.group(1)):
        plain = html_to_text(item)
        pictured = bool(_PICTURED.search(plain))
        question = _PICTURED.sub("", plain).strip()
        if not question or len(question) > MAX_QUESTION_CHARS:
            continue
        link = _ANSWER.search(item)
        answer = html.unescape(link.group(1) or link.group(2)) if link else ""
        answer = _DISAMBIGUATION.sub("", " ".join(answer.split()))
        facts.append({"question": question, "answer": answer if len(answer) <= MAX_ANSWER_CHARS else "",
                      "pictured": pictured})
        if len(facts) == MAX_FACTS:
            break
    if not facts:
        raise ValueError("Template:Dyk listed no questions")
    return {"facts": facts, "image_url": picture_url(text[block.end():listed.start()])}


def usable(entry, now_seconds, limit_seconds):
    """Whether a kept entry is this schema's, has facts and is at most ``limit_seconds`` old."""
    if not isinstance(entry, dict) or entry.get("schema") != CACHE_SCHEMA:
        return False
    facts = entry.get("facts")
    if not isinstance(facts, list) or not facts or not all(
            isinstance(fact, dict) and isinstance(fact.get("question"), str) for fact in facts):
        return False
    try:
        age = now_seconds - float(entry.get("fetched_at"))
    except (TypeError, ValueError):
        return False
    return -CLOCK_SKEW_SECONDS <= age < limit_seconds
