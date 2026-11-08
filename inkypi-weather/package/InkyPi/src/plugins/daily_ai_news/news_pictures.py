"""The picture under 整点新闻's lead headline, so the headline always stands on one.

In order: the photo the lead's own article page names (its og:image or twitter:image; a 中新网
story, whose pages name none, by the first photo of its body), its RSS item's picture, and last a
generated illustration of its topic (lead_backdrops/<kind>.jpg, one per news_art topic). A page is
read only on the outlets the page knows (ARTICLE_HOSTS), only its head, and only during a data
refresh; its answer is kept per link (a found picture for 7 days, a miss for 6 hours), so a redraw
reads what is kept and never fetches.
"""

from __future__ import annotations

import html
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

from PIL import Image

from runtime.refresh_contracts import TaskCancelled

from .news_sources import lead_image_url

logger = logging.getLogger(__name__)
BACKDROP_DIR = Path(__file__).resolve().parent / "lead_backdrops"
# The outlets whose article pages are read for their picture (the page's own feeds).
ARTICLE_HOSTS = ("bbc.co.uk", "bbc.com", "npr.org", "theguardian.com", "aljazeera.com", "dw.com", "pbs.org",
                 "abcnews.go.com", "abcnews.com", "chinanews.com.cn", "chinanews.com", "nytimes.com", "france24.com",
                 "rthk.hk")
PAGE_BYTES = 400 * 1024
PAGE_SECONDS = 8
PAGE_AGENT = "Mozilla/5.0 (compatible; InkyPi DailyNews/1.0; +https://github.com/Feeengyuuu/EpaperSystem)"
FOUND_TTL, MISSED_TTL = timedelta(days=7), timedelta(hours=6)
KEPT_LINKS = 64
_META = re.compile(r"<meta\b[^>]*(?:property|name)\s*=\s*[\"'](?:og:image(?::secure_url)?|twitter:image(?::src)?)[\"'][^>]*>",
                   re.I)
_CONTENT = re.compile(r"content\s*=\s*[\"']([^\"']+)[\"']", re.I)
# A 中新网 story's photos sit in its body on its image hosts (i2.chinanews.com.cn/simg/cmshd/...).
_CHINANEWS_PHOTO = re.compile(r"(?:https?:)?//i\d\.chinanews\.com\.cn/simg/cmshd/[^\"'\s<>]+?\.(?:jpe?g|png)", re.I)


def _absolute(url):
    url = html.unescape(str(url or "").strip())
    return "https:" + url if url.startswith("//") else url


def page_picture(text):
    """The picture an article page names for itself, on the outlets' image hosts; "" for none."""
    text = str(text or "")
    for tag in _META.findall(text):
        found = _CONTENT.search(tag)
        url = _absolute(found.group(1)) if found else ""
        # BBC's share picture carries its branded strip; its plain copy is at the same path.
        url = re.sub(r"/ace/branded_news/(\d+)/", r"/ace/standard/\1/", url)
        if lead_image_url(url):
            return url
    found = _CHINANEWS_PHOTO.search(text)
    url = _absolute(found.group(0)) if found else ""
    return url if lead_image_url(url) else ""


def _article_host(link):
    host = re.sub(r"^www\.", "", urlparse(str(link or "")).netloc.lower().split(":")[0])
    return urlparse(str(link or "")).scheme == "https" and any(host == item or host.endswith("." + item)
                                                               for item in ARTICLE_HOSTS)


def article_picture(link, cache_path, *, http, context, cached_only, now=None):
    """The picture the lead's article page names (kept per link), "" when it names none or is not
    at hand; the page is fetched only when ``cached_only`` is false."""
    link = str(link or "")
    if not link or not _article_host(link):
        return ""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    path = Path(cache_path)
    try:
        kept = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, ValueError):
        kept = {}
    kept = kept if isinstance(kept, dict) else {}
    entry = kept.get(link) if isinstance(kept.get(link), dict) else None
    if entry is not None:
        try:
            fresh = now - datetime.fromisoformat(entry["at"]) <= (FOUND_TTL if entry.get("picture") else MISSED_TTL)
        except (KeyError, TypeError, ValueError):
            fresh = False
        if fresh or cached_only:
            return str(entry.get("picture") or "")
    if cached_only:
        return ""
    try:
        page = http.request_text_head("GET", link, max_bytes=PAGE_BYTES, timeout=PAGE_SECONDS, context=context,
                                      headers={"User-Agent": PAGE_AGENT})
        picture = page_picture(page.data)
    except TaskCancelled:
        raise
    except Exception as exc:  # noqa: BLE001 - the RSS picture or the topic's illustration stands in.
        logger.info("Lead article picture unavailable: %s", type(exc).__name__)
        return str((entry or {}).get("picture") or "")
    kept[link] = {"picture": picture, "at": now.isoformat()}
    while len(kept) > KEPT_LINKS:
        kept.pop(next(iter(kept)))
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(kept, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        logger.warning("Failed to keep the lead's article picture: %s", exc)
    return picture


@lru_cache(maxsize=4)
def _backdrop(kind):
    for name in (kind, "news"):
        path = BACKDROP_DIR / f"{name}.jpg"
        if path.is_file():
            with Image.open(path) as image:
                return image.convert("RGB")
    return None


def topic_backdrop(kind):
    """A copy of the generated illustration for the lead's topic (the general news one for a topic
    without its own), or None without the art."""
    image = _backdrop(str(kind or "news"))
    return image.copy() if image is not None else None
