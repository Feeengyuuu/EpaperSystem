"""Small, bounded cover thumbnails; cache-only reads have no write side effects."""

from hashlib import sha256
from io import BytesIO
import logging
from pathlib import Path

from PIL import Image

from runtime.refresh_contracts import TaskCancelled, TaskDeadlineExceeded
from utils.cache_manager import CacheBudget, cache_namespace_for_directory
from utils.safe_image import ImageLimits, safe_open_image

from .source import MAX_ITEMS, USER_AGENT, safe_thumbnail_url


logger = logging.getLogger(__name__)

LIMITS = ImageLimits(max_bytes=512 * 1024, max_width=1024, max_height=1024, max_pixels=1024 * 1024)
BUDGET = CacheBudget(max_bytes=4 * 1024 * 1024, max_files=48, max_age_seconds=14 * 86400)
THUMB_SIZE = (320, 180)
# The hero card's art: the offer's Steam store header (460x215), kept at that size.
HERO_SIZE = (460, 215)
STEAM_HEADER = "https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/{app_id}/header.jpg"
# v2 preserves small official capsules without reducing them prematurely.
THUMB_PREFIX, HERO_PREFIX = "v2-", "hero-v1-"


def _key(url, prefix):
    return prefix + sha256(url.encode("utf-8")).hexdigest() + ".png"


def _hero_url(deal):
    """The offer's Steam store header, or "" without a Steam app id."""
    app_id = str((deal or {}).get("steam_app_id") or "")
    return STEAM_HEADER.format(app_id=app_id) if app_id.isdecimal() else ""


def _mark_used(directory, keys):
    """Count the kept covers a refresh draws again as just used; the cache's namespace, or None.

    The cache drops its least recently used files first (when new covers fill it, when the whole
    cache is over its budget, and again from the file times at every start), and a cover read
    straight from disk is never seen in use: a long-running offer's cover would be the oldest file
    and the first one dropped, leaving the next cache-only redraw a placeholder."""
    kept = [key for key in dict.fromkeys(keys) if (directory / key).is_file() and not (directory / key).is_symlink()]
    if not kept:
        return None
    try:
        namespace = cache_namespace_for_directory(directory, BUDGET)
        for key in kept:
            namespace.get_path(key)
    except (OSError, ValueError, RuntimeError):
        return None
    return namespace


def _cached_cover(url, directory, *, size, prefix, http, context, cached_only, namespace):
    """(image or None, namespace): the cover at ``url`` from the cache, else fetched, bounded to
    ``size`` and stored; a missing or broken cover is None, never a failed refresh."""
    key = _key(url, prefix)
    path = directory / key
    try:
        if path.is_symlink():
            return None, namespace
        if path.is_file():
            return safe_open_image(path, limits=LIMITS), namespace
        if cached_only:
            return None, namespace
        response = http.request_bytes(
            "GET", url, timeout=7, max_bytes=LIMITS.max_bytes, context=context,
            headers={"User-Agent": USER_AGENT}, allow_redirects=False,
        )
        with safe_open_image(response.data, limits=LIMITS) as original:
            original.thumbnail(size, Image.Resampling.LANCZOS)
            thumb = original.convert("RGB")
        output = BytesIO()
        thumb.save(output, format="PNG")
        if context:
            context.raise_if_cancelled()
        namespace = namespace or cache_namespace_for_directory(directory, BUDGET)
        namespace.put_bytes(key, output.getvalue())
        return thumb, namespace
    except (TaskCancelled, TaskDeadlineExceeded):
        raise
    except (OSError, ValueError, RuntimeError) as error:
        # Missing cover art does not turn a valid offer into a failed data refresh.
        logger.info("Game deal cover unavailable: %s (%s)", url, type(error).__name__)
        return None, namespace


def load_covers(deals, cache_dir, *, http, context=None, cached_only=False):
    directory = Path(cache_dir)
    covers = {}
    namespace = None
    listed = [(deal, safe_thumbnail_url(deal.get("thumbnail_url"))) for deal in deals[:MAX_ITEMS]]
    if not cached_only:
        # Before anything new is stored: the covers kept for these offers, and the store header of
        # any of them that has led the page, are in use.
        namespace = _mark_used(directory, [_key(url, THUMB_PREFIX) for _deal, url in listed if url]
                               + [_key(_hero_url(deal), HERO_PREFIX) for deal, _url in listed if _hero_url(deal)])
    try:
        for deal, url in listed:
            if context:
                context.raise_if_cancelled()
            if not url:
                continue
            cover, namespace = _cached_cover(url, directory, size=THUMB_SIZE, prefix=THUMB_PREFIX, http=http,
                                             context=context, cached_only=cached_only, namespace=namespace)
            if cover is not None:
                covers[deal["game_id"]] = cover
    except (TaskCancelled, TaskDeadlineExceeded):
        for image in covers.values():
            image.close()
        raise
    return covers


def load_hero_cover(deal, cache_dir, *, http, context=None, cached_only=False):
    """The hero offer's Steam store header, or None (no Steam app id, or no art)."""
    url = _hero_url(deal)
    if not url:
        return None
    directory = Path(cache_dir)
    namespace = None if cached_only else _mark_used(directory, [_key(url, HERO_PREFIX)])
    cover, _namespace = _cached_cover(url, directory, size=HERO_SIZE, prefix=HERO_PREFIX, http=http,
                                      context=context, cached_only=cached_only, namespace=namespace)
    return cover
