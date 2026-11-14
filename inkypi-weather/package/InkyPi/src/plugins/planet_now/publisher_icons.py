"""News publishers' bundled marks and cached site favicons.

The RSS article URL identifies a publisher's site. As on Tech Pulse, only Google's
fixed favicon service is contacted: arbitrary article sites are never requested.
The finite legacy-name table serves old cached RSS rows that did not retain URLs.
Verified site icons ship with known publishers, so a cold or offline display is
complete too. New publishers need no table entry: their icons arrive in a bounded
refresh, and theme redraws only read the cache. A missing icon retains its name.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from PIL import Image, ImageOps

from runtime.refresh_contracts import TaskCancelled, TaskContext, TaskDeadlineExceeded
from utils.atomic_file import atomic_write_json
from utils.cache_manager import CacheBudget, CacheError, cache_namespace_for_directory
from utils.remote_thumbnails import MIN_SECONDS_PER_IMAGE, _key, load_thumbnails
from utils.safe_image import ImageLimits, safe_open_image

logger = logging.getLogger(__name__)
ASSETS = Path(__file__).resolve().parent / "assets" / "logos"
PUBLISHER_ASSETS = ASSETS.parent / "publishers"
NEWS_OUTLET_LOGOS = {
    "cnn": "cnn", "espn": "espn", "goal": "goal", "goal.com": "goal",
    "bbc": "bbc", "bbc news": "bbc", "bbc sport": "bbc",
    "rte": "rte", "rté": "rte", "rte.ie": "rte", "rté.ie": "rte",
    "rte news": "rte", "rté news": "rte",
    "rtp": "rtp", "rtp notícias": "rtp", "rtp noticias": "rtp",
    "yahoo sports": "yahoo_sports", "hbcu sports": "hbcu_sports",
}
LOCAL_ICONS = {"ktla": "ktla", "ktla 5": "ktla", "ktla 5 news": "ktla", "lol esports": "lol_esports"}
FAVICON_HOST = "t3.gstatic.com"
FAVICON_URL = ("https://" + FAVICON_HOST + "/faviconV2?client=SOCIAL&type=FAVICON&fallback_opts=TYPE,SIZE,URL"
               "&url=https://{host}&size=64")
_HOST = re.compile(r"(?=.{4,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{0,61}[a-z0-9]")
ICON_BUDGET = CacheBudget(21 * 86400, 96, 2 * 1024 * 1024)
ICON_BYTES, ICON_SECONDS, RENDER_RESERVE = 128 * 1024, 6, 4
ICON_LIMITS = ImageLimits(max_bytes=ICON_BYTES, max_width=2048, max_height=2048, max_pixels=2048 * 2048)
RESTED = timedelta(hours=2)
# The service's observed 16 px default globe (normally returned with HTTP 404).
# Reject its decoded pixels too, so it cannot become a publisher mark from a cache.
PLACEHOLDER_PIXELS = "85e45b1170430fafc67ed106978671cf247826d0d4375463bcb1385ea3351f7a"


def article_host(url):
    """A public-looking DNS hostname from the article URL; no arbitrary site is fetched."""
    try:
        parts = urlsplit(str(url or "").strip())
        if parts.scheme not in ("http", "https") or parts.username or parts.password:
            return ""
        if parts.port not in (None, 80 if parts.scheme == "http" else 443):
            return ""
        host = (parts.hostname or "").encode("idna").decode("ascii").lower().rstrip(".")
    except (ValueError, UnicodeError):
        return ""
    if not _HOST.fullmatch(host) or host.endswith((".local", ".localhost", ".internal", ".invalid")):
        return ""
    return host


@lru_cache(maxsize=1)
def _legacy_hosts():
    """Exact names verified from retained RSS article URLs or official publisher sites."""
    try:
        data = json.loads((ASSETS / "publisher_hosts.json").read_text(encoding="utf-8"))
        publishers = data.get("publishers", {}) if isinstance(data, dict) else {}
        return publishers if isinstance(publishers, dict) else {}
    except (OSError, ValueError):
        return {}


def publisher_host(item):
    # A present but malformed URL must not silently turn into an unrelated legacy alias.
    if item.get("source_url"):
        return article_host(item["source_url"])
    known = _legacy_hosts().get(str(item.get("source") or "").strip().casefold(), {})
    return article_host("https://" + str(known.get("host") or "")) if isinstance(known, dict) else ""


def icon_key(item):
    host = publisher_host(item)
    return f"publisher:{host}" if host else ""


def bundled_icon(item):
    """A verified public site icon in the release, available even with an empty runtime cache."""
    host = publisher_host(item)
    if not host:
        return None
    try:
        with safe_open_image(PUBLISHER_ASSETS / f"{host}.png", limits=ICON_LIMITS) as source:
            if _placeholder(source):
                return None
            return source.convert("RGBA")
    except (OSError, ValueError):
        return None


def favicon_url(host):
    return FAVICON_URL.format(host=host) if article_host("https://" + str(host)) == host else ""


def safe_icon_url(url):
    """Only the exact service address this module builds is permitted; never redirects."""
    try:
        parts = urlsplit(str(url or ""))
        if parts.scheme != "https" or parts.netloc != FAVICON_HOST or parts.path != "/faviconV2":
            return None
        site = parse_qs(parts.query).get("url", [])
        expected = favicon_url(article_host(site[0])) if len(site) == 1 else ""
        return str(url) if expected and str(url) == expected else None
    except ValueError:
        return None


def _recent(stamp, now):
    try:
        return timedelta(0) <= now - datetime.fromisoformat(str(stamp)) < RESTED
    except (ValueError, TypeError):
        return False


def _read_missed(path, now):
    try:
        if path.is_symlink() or path.stat().st_size > 32768:
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        return {url: stamp for url, stamp in data.items() if safe_icon_url(url) and _recent(stamp, now)}
    except (OSError, ValueError, AttributeError):
        return {}


def _placeholder(image):
    return image.size == (16, 16) and sha256(image.convert("RGBA").tobytes()).hexdigest() == PLACEHOLDER_PIXELS


def _checkpoint(context, images=()):
    if context is not None:
        try:
            context.raise_if_cancelled()
        except TaskCancelled:
            for image in images:
                image.close()
            raise


def load(items, directory, *, http, context, allow_network, now=None):
    """Return cached site icons by publisher key; optional refresh cannot use render time."""
    _checkpoint(context)
    wanted = {}
    for item in items[:5]:
        source = str(item.get("source") or "").strip().casefold()
        bundled = NEWS_OUTLET_LOGOS.get(source) or LOCAL_ICONS.get(source)
        if bundled and (ASSETS / f"{bundled}.png").is_file():
            continue
        host = publisher_host(item)
        local = bundled_icon(item)
        if local is not None:
            local.close()
            continue
        if host:
            wanted[f"publisher:{host}"] = favicon_url(host)
    if not wanted:
        return {}
    directory = Path(directory)
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    missed_path = directory / "missed.json"
    missed = _read_missed(missed_path, now)
    can_fetch = allow_network and context.remaining_seconds() >= MIN_SECONDS_PER_IMAGE + RENDER_RESERVE
    urls = list(dict.fromkeys(wanted.values()))
    available = [url for url in urls if url not in missed] if can_fetch else urls

    def read(urls, task, cached_only, client=http):
        return load_thumbnails(urls, directory, http=client, context=task, cached_only=cached_only,
                               safe_url=safe_icon_url, user_agent="InkyPi PlanetNow/1.0",
                               size=(64, 64), budget=ICON_BUDGET, keep_alpha=True, max_bytes=ICON_BYTES)

    icons = read(urls, context, True)
    for url, icon in list(icons.items()):
        if _placeholder(icon):
            icon.close()
            del icons[url]
            if can_fetch:
                _checkpoint(context, icons.values())
                try:
                    # Only this publisher's known placeholder is invalidated. Offline reads
                    # leave it untouched; an expired negative entry can then retry normally.
                    cache_namespace_for_directory(directory, ICON_BUDGET).remove(_key(url, True))
                except (OSError, ValueError, CacheError):
                    pass
    pending = [url for url in available if url not in icons]
    changed = False
    if can_fetch and pending:
        attempted = set()

        class RecordedRequests:
            def request_bytes(self, method, url, **kwargs):
                attempted.add(url)
                response = http.request_bytes(method, url, **kwargs)
                # Validate the service's answer before remote_thumbnails can persist it.
                # A generic globe is never claimed to identify a publisher.
                with safe_open_image(response.data, limits=ICON_LIMITS) as original:
                    if _placeholder(original):
                        raise ValueError("favicon service returned its default globe")
                return response

        child = TaskContext(context.cancel_event, min(context.deadline_monotonic - RENDER_RESERVE,
                                                     context.clock() + ICON_SECONDS), context.clock)
        try:
            downloaded = read(pending, child, False, RecordedRequests())
            icons.update(downloaded)
        except TaskDeadlineExceeded:
            try:
                context.raise_if_cancelled()
                # The shared loader closes its partial images on cancellation; recover
                # completed files without another request or spending the render reserve.
                icons.update(read(pending, context, True))
            except TaskCancelled:
                for icon in icons.values():
                    icon.close()
                raise
        except TaskCancelled:
            for icon in icons.values():
                icon.close()
            raise
        for url in attempted:
            if url not in icons:
                missed[url] = now.isoformat()
                changed = True
    for url, icon in list(icons.items()):
        if _placeholder(icon):
            icon.close()
            del icons[url]
            if can_fetch:
                missed[url] = now.isoformat()
                changed = True
    if changed:
        _checkpoint(context, icons.values())
        try:
            directory.mkdir(parents=True, exist_ok=True)
            atomic_write_json(missed_path, dict(list(missed.items())[-96:]))
        except OSError as error:
            logger.warning("Planet Now could not keep publisher icon misses: %s", error)
    _checkpoint(context, icons.values())
    return {key: icons[url] for key, url in wanted.items() if url in icons}


def on_page(icon, height=11):
    """A favicon's original colors on its conventional small white field, in either theme."""
    fitted = ImageOps.contain(icon.convert("RGBA"), (height, height), Image.Resampling.LANCZOS)
    tile = Image.new("RGBA", (height, height), "white")
    tile.alpha_composite(fitted, ((height - fitted.width) // 2, (height - fitted.height) // 2))
    return tile
