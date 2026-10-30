"""Small, bounded thumbnails of remote cover art; cache-only reads have no write side effects.

Each caller supplies its own host allowlist (``safe_url``) and User-Agent. Downloads only
happen during a data refresh and stop when the task is short on time.
"""

from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
from io import BytesIO
from pathlib import Path

from PIL import Image

from runtime.refresh_contracts import TaskCancelled
from utils.cache_manager import CacheBudget, cache_namespace_for_directory
from utils.safe_image import ImageLimits, safe_open_image


LIMITS = ImageLimits(max_bytes=1024 * 1024, max_width=2048, max_height=2048, max_pixels=2048 * 2048)
BUDGET = CacheBudget(max_bytes=6 * 1024 * 1024, max_files=64, max_age_seconds=21 * 86400)
MIN_SECONDS_PER_IMAGE = 4.0


def _key(url, keep_alpha=False):
    # A picture kept with its transparency is cached apart from one flattened before.
    return ("v1a-" if keep_alpha else "v1-") + sha256(url.encode("utf-8")).hexdigest() + ".png"


def load_thumbnails(urls, cache_dir, *, http, context, cached_only, safe_url, user_agent,
                    size=(480, 270), budget=BUDGET, keep_alpha=False, max_bytes=LIMITS.max_bytes):
    """Return {url: RGB thumbnail} for URLs that are cached or could be fetched in time; RGBA
    with ``keep_alpha``, so a transparent seal or logo keeps its transparency. A download may run
    to ``max_bytes`` (a large but poorly compressed figure); its pixels stay within ``LIMITS``."""
    directory = Path(cache_dir)
    download_limits = replace(LIMITS, max_bytes=max_bytes)
    mode = "RGBA" if keep_alpha else "RGB"
    images, namespace = {}, None
    for raw in urls:
        url = safe_url(raw)
        if not url or url in images:
            continue
        path = directory / _key(url, keep_alpha)
        try:
            if path.is_symlink():
                continue
            if path.is_file():
                with safe_open_image(path, limits=LIMITS) as cached:
                    images[url] = cached.convert(mode)
                continue
            if cached_only or context.remaining_seconds() < MIN_SECONDS_PER_IMAGE:
                continue
            response = http.request_bytes("GET", url, timeout=7, max_bytes=max_bytes, context=context,
                                          headers={"User-Agent": user_agent}, allow_redirects=False)
            with safe_open_image(response.data, limits=download_limits) as original:
                original.thumbnail(size, Image.Resampling.LANCZOS)
                thumb = original.convert(mode)
            output = BytesIO()
            thumb.save(output, format="PNG", optimize=True)
            context.raise_if_cancelled()
            namespace = namespace or cache_namespace_for_directory(directory, budget)
            namespace.put_bytes(path.name, output.getvalue())
            images[url] = thumb
        except TaskCancelled:
            for image in images.values():
                image.close()
            raise
        except (OSError, ValueError, RuntimeError):
            # A missing picture never turns valid listings into a failed refresh.
            continue
    return images
