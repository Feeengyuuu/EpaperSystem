"""Cache-first optional portraits that cannot spend the whole dashboard deadline."""

from pathlib import Path
import threading
import time

from PIL import Image, ImageDraw, ImageOps

from runtime.long_task_executor import current_task_context
from runtime.refresh_contracts import TaskCancelled, TaskContext, TaskDeadlineExceeded
from utils.http_client import create_single_attempt_http_client
from utils.resource_cache import cached_resource_image, prune_resource_images
from utils.safe_image import ImageLimits, safe_open_image


MOSAIC_BUDGET_SECONDS = 8
AVATAR_REQUEST_SECONDS = 2.5
RENDER_RESERVE_SECONDS = 2
# Portraits one panel draws at most: three in-game rows and a crowd of small ones.
MOSAIC_PORTRAITS = 16
AVATAR_TTL = 7 * 24 * 3600
AVATAR_LIMITS = ImageLimits(max_bytes=1024 * 1024, max_width=1024, max_height=1024, max_pixels=1_000_000)


def load_mosaic_avatars(plugin, records, size, *, clock=None):
    """Return only obtained portraits; the renderer counts every omitted friend.

    Existing portraits remain usable after the optional network budget expires.
    Local timeouts are omissions; cancellation of the owning refresh still wins.
    """
    parent = current_task_context()
    clock = clock or (parent.clock if parent else time.monotonic)
    deadline = clock() + MOSAIC_BUDGET_SECONDS
    if parent is not None:
        deadline = min(deadline, parent.deadline_monotonic - RENDER_RESERVE_SECONDS)
    cancel_event = parent.cancel_event if parent else threading.Event()
    read_only = bool(getattr(plugin, "_media_read_only", False))
    client, avatars, paths = None, [], []
    complete = False

    def fetch(url):
        context = TaskContext(cancel_event, min(deadline, clock() + AVATAR_REQUEST_SECONDS), clock)
        try:
            payload = client.request_bytes("GET", url, context=context, timeout=AVATAR_REQUEST_SECONDS,
                                           max_bytes=AVATAR_LIMITS.max_bytes, allow_redirects=False).data
        except TaskDeadlineExceeded:
            if parent is not None:
                parent.raise_if_cancelled()
            raise TimeoutError("Optional friend portrait timed out") from None
        image = safe_open_image(payload, limits=AVATAR_LIMITS)
        image.info["resource_downloaded_bytes"] = len(payload)
        return image

    try:
        for friend, url in records[:MOSAIC_PORTRAITS]:
            if parent is not None:
                parent.raise_if_cancelled()
            path = plugin._avatar_cache_path(url)
            paths.append(path)
            # Read-only cache lookup also accepts an older usable avatar without HTTP.
            source = cached_resource_image(path, lambda: None, ttl=AVATAR_TTL,
                                           label="steam_friend_mosaic", read_only=True)
            if source is None and not read_only and deadline - clock() > 0.05:
                if client is None:
                    client = create_single_attempt_http_client()
                source = cached_resource_image(path, lambda: fetch(url), ttl=AVATAR_TTL,
                                               label="steam_friend_mosaic")
            try:
                if parent is not None:
                    parent.raise_if_cancelled()
            except TaskCancelled:
                if source is not None:
                    source.close()
                raise
            if source is None:
                continue
            with source, source.convert("RGB") as rgb, ImageOps.fit(rgb, (size, size),
                                      method=Image.Resampling.LANCZOS) as fitted:
                with Image.new("L", (size, size), 0) as mask:
                    ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
                    avatar = Image.new("RGBA", (size, size), (255, 255, 255, 0))
                    avatar.paste(fitted, (0, 0), mask)
            avatars.append((friend, avatar))
        if client is not None:
            prune_resource_images(Path(paths[0]).parent,
                                  prefixes=("avatar_", "gameicon_", "badgeicon_", "profilebg_"),
                                  max_files=384, max_bytes=32 * 1024 * 1024,
                                  max_age=90 * 24 * 3600, label="steam_profile_media", protected=paths)
        complete = True
        return avatars
    finally:
        if not complete:
            for _friend, avatar in avatars:
                avatar.close()
        if client is not None:
            client.close()
