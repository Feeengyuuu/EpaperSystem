"""Bounded, image-bound Mars motion for the web UI; never a hardware animation."""

from __future__ import annotations

import base64
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import time

from PIL import Image, ImageDraw

from runtime.refresh_contracts import TaskCancelled, TaskDeadlineExceeded
from utils.cache_manager import CacheBudget, cache_namespace_for_directory
from utils.safe_image import ImageLimits, safe_open_image
from utils.space_typography import SpaceCanvas

from . import render

BOX = (320, 304, 432, 468)
BUDGET = CacheBudget(4 * 86400, 12, 3 * 1024 * 1024)
MAX_PACKAGE = 512 * 1024
LIMITS = ImageLimits(max_bytes=3 * 1024 * 1024, max_width=800, max_height=480, max_pixels=800 * 480)


def _key(image):
    with image.convert("RGB") as rgb:
        return sha256(rgb.tobytes()).hexdigest()


def _uri(image, format="PNG"):
    output = BytesIO()
    image.save(output, format=format, **({"quality": 88} if format == "JPEG" else {}))
    return f"data:image/{format.lower()};base64," + base64.b64encode(output.getvalue()).decode("ascii")


def publish_motion(directory, image, payload, settings, now, images):
    """Write a derivative only after the matching static render exists in memory."""
    if image.size != (800, 480):
        return
    mars = (payload.get("mars") or {}).get("data") or {}
    rows = mars.get("photos")
    rows = rows if isinstance(rows, list) and rows else [mars]
    photos = [p for p in rows[:3] if isinstance(p, dict) and p.get("image") in images]
    if not photos:
        return
    # Start at the same photo the e-paper snapshot shows.
    photos.sort(key=lambda p: p.get("image") != mars.get("image"))
    slides = []
    for photo in photos:
        canvas = render.PageCanvas(render._palette(settings), family=settings.get("fontFamily") or "Microsoft YaHei",
                                   size=(112, 164))
        canvas.image = Image.new("RGBA", (112, 164), (0, 0, 0, 0))
        canvas.draw = ImageDraw.Draw(canvas.image)
        render._mars_facts(SpaceCanvas(canvas), photo, now, (0, 0, 112, 164), shade=False)
        thumbnail = images[photo["image"]].convert("RGB")
        thumbnail.thumbnail((480, 300), Image.Resampling.LANCZOS)
        slides.append({"photo": _uri(thumbnail, "JPEG"), "facts": _uri(canvas.image),
                       "sol": photo.get("sol"), "camera": str(photo.get("camera") or "")[:80],
                       "taken_utc": str(photo.get("taken_utc") or "")[:80]})
        thumbnail.close()
        canvas.image.close()
    key = _key(image)
    body = json.dumps({"schema": 1, "image_key": key, "size": [800, 480], "box": BOX,
                       "duration_ms": 10000, "fade_ms": 900, "slides": slides}, separators=(",", ":")).encode()
    if len(body) <= MAX_PACKAGE:
        cache_namespace_for_directory(directory, BUDGET).put_bytes(key + ".json", body)


def read_motion(image_path, *, directory=None):
    """Read an exact rendered-image package, without fetching or mutating any cache.

    Include the base frame from the same open file so an image refresh racing this
    request cannot pair the old web image with another render's Mars facts.
    """
    image = None
    try:
        if directory is None:
            from .planet_now import PlanetNow

            directory = PlanetNow({"id": "planet_now"}).cache_dir(leaf="mars-motion", create=False)
        with safe_open_image(image_path, limits=LIMITS) as original:
            if original.size != (800, 480):
                return None
            image = original.convert("RGB")
        key = _key(image)
        path = Path(directory) / (key + ".json")
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_PACKAGE:
            return None
        if time.time() - path.stat().st_mtime > BUDGET.max_age_seconds:
            return None
        package = json.loads(path.read_bytes())
        if not isinstance(package, dict):
            return None
        if package.get("schema") != 1 or package.get("image_key") != key:
            return None
        if package.get("size") != [800, 480] or package.get("box") != list(BOX):
            return None
        slides = package.get("slides")
        if not isinstance(slides, list) or not 1 <= len(slides) <= 3:
            return None
        for slide in slides:
            if not isinstance(slide, dict) or not str(slide.get("photo", "")).startswith("data:image/jpeg;base64,"):
                return None
            if not str(slide.get("facts", "")).startswith("data:image/png;base64,"):
                return None
        package["base"] = _uri(image)
        return package
    except (TaskCancelled, TaskDeadlineExceeded):
        raise
    except (OSError, ValueError, TypeError, KeyError, RuntimeError):
        return None
    finally:
        if image is not None:
            image.close()
