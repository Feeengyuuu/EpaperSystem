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


def load_covers(deals, cache_dir, *, http, context=None, cached_only=False):
    directory = Path(cache_dir)
    covers = {}
    namespace = None
    for deal in deals[:MAX_ITEMS]:
        if context:
            context.raise_if_cancelled()
        url = safe_thumbnail_url(deal.get("thumbnail_url"))
        if not url:
            continue
        # v2 preserves small official capsules for the full-height cropped layout.
        key = "v2-" + sha256(url.encode("utf-8")).hexdigest() + ".png"
        path = directory / key
        try:
            if path.is_symlink():
                continue
            if path.is_file():
                covers[deal["game_id"]] = safe_open_image(path, limits=LIMITS)
                continue
            if cached_only:
                continue
            response = http.request_bytes(
                "GET", url, timeout=7, max_bytes=LIMITS.max_bytes, context=context,
                headers={"User-Agent": USER_AGENT}, allow_redirects=False,
            )
            with safe_open_image(response.data, limits=LIMITS) as original:
                original.thumbnail(THUMB_SIZE, Image.Resampling.LANCZOS)
                thumb = original.convert("RGB")
            output = BytesIO()
            thumb.save(output, format="PNG")
            if context:
                context.raise_if_cancelled()
            namespace = namespace or cache_namespace_for_directory(directory, BUDGET)
            namespace.put_bytes(key, output.getvalue())
            covers[deal["game_id"]] = thumb
        except (TaskCancelled, TaskDeadlineExceeded):
            for image in covers.values():
                image.close()
            raise
        except (OSError, ValueError, RuntimeError):
            # Missing cover art does not turn a valid offer into a failed data refresh.
            continue
    return covers
