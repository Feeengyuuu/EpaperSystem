"""Small, bounded cover thumbnails; cache-only reads have no write side effects."""

from hashlib import sha256
from io import BytesIO
from pathlib import Path

from PIL import Image

from runtime.refresh_contracts import TaskCancelled, TaskDeadlineExceeded
from utils.cache_manager import CacheBudget, cache_namespace_for_directory
from utils.safe_image import ImageLimits, safe_open_image

from .source import MAX_ITEMS, USER_AGENT, safe_thumbnail_url


LIMITS = ImageLimits(max_bytes=512 * 1024, max_width=1024, max_height=1024, max_pixels=1024 * 1024)
BUDGET = CacheBudget(max_bytes=4 * 1024 * 1024, max_files=48, max_age_seconds=14 * 86400)
THUMB_SIZE = (320, 180)
# The hero card's art: the offer's Steam store header (460x215), kept at that size.
HERO_SIZE = (460, 215)
STEAM_HEADER = "https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/{app_id}/header.jpg"


def _cached_cover(url, directory, *, size, prefix, http, context, cached_only, namespace):
    """(image or None, namespace): the cover at ``url`` from the cache, else fetched, bounded to
    ``size`` and stored; a missing or broken cover is None, never a failed refresh."""
    key = prefix + sha256(url.encode("utf-8")).hexdigest() + ".png"
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
    except (OSError, ValueError, RuntimeError):
        # Missing cover art does not turn a valid offer into a failed data refresh.
        return None, namespace


def load_covers(deals, cache_dir, *, http, context=None, cached_only=False):
    directory = Path(cache_dir)
    covers = {}
    namespace = None
    try:
        for deal in deals[:MAX_ITEMS]:
            if context:
                context.raise_if_cancelled()
            url = safe_thumbnail_url(deal.get("thumbnail_url"))
            if not url:
                continue
            # v2 preserves small official capsules without reducing them prematurely.
            cover, namespace = _cached_cover(url, directory, size=THUMB_SIZE, prefix="v2-", http=http,
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
    app_id = str((deal or {}).get("steam_app_id") or "")
    if not app_id.isdecimal():
        return None
    cover, _namespace = _cached_cover(STEAM_HEADER.format(app_id=app_id), Path(cache_dir), size=HERO_SIZE,
                                      prefix="hero-v1-", http=http, context=context, cached_only=cached_only,
                                      namespace=None)
    return cover
