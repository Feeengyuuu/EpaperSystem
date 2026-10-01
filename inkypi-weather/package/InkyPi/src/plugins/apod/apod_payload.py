"""Normalize the official APOD WordPress response into the cached APOD fields."""

from html.parser import HTMLParser
from typing import Any, Mapping
from urllib.parse import urlsplit


class _PlainText(HTMLParser):
    _HIDDEN = frozenset({"script", "style", "template", "noscript"})
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in self._HIDDEN:
            self.hidden.append(tag)
        elif not self.hidden:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if self.hidden:
            if tag == self.hidden[-1]:
                self.hidden.pop()
        else:
            self.parts.append(" ")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def _plain_text(value: Any) -> str:
    parser = _PlainText()
    parser.feed(str(value or ""))
    parser.close()
    return " ".join("".join(parser.parts).split())


def _not_article(value: Any, permalink: Any) -> Any:
    """A NASA article is never a usable image candidate, even on a trusted host."""

    if not value:
        return None
    if permalink and str(value).strip() == str(permalink).strip():
        return None
    try:
        parsed = urlsplit(str(value))
        if parsed.hostname == "science.nasa.gov" and (
            parsed.path.rstrip("/") in {"", "/apod"}
            or parsed.path.startswith("/image-article/")
            or parsed.path.startswith("/apod/")
        ):
            return None
    except ValueError:
        return None
    return value


def normalize_official_payload(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Keep plain legacy-shaped data compatible; never scrape basic_html for media."""

    normalized = dict(raw)
    for field in ("title", "explanation"):
        normalized[field] = _plain_text(raw.get(field))
    normalized["copyright"] = _plain_text(raw.get("copyright") or raw.get("credit"))
    if str(raw.get("media_type") or "").strip().casefold() == "iframe":
        normalized["media_type"] = "video"
    # NASA's WordPress API uses url for the article permalink, not a standard image.
    wordpress = "permalink" in raw or "post_id" in raw
    permalink = raw.get("permalink")
    normalized["url"] = None if wordpress else _not_article(raw.get("url"), permalink)
    normalized["hdurl"] = _not_article(raw.get("hdurl"), permalink)
    return normalized
