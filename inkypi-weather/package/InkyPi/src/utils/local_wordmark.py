"""Small, bounded local wordmarks with their original colors and transparency."""

from functools import lru_cache
from pathlib import Path

from PIL import Image

from utils.safe_image import ImageLimits, safe_open_image


_LIMITS = ImageLimits(max_bytes=4 * 1024 * 1024, max_width=2048, max_height=2048,
                      max_pixels=2048 * 2048, allowed_formats=frozenset({"PNG"}))
_MAX_TARGET = (512, 128)


@lru_cache(maxsize=3)
def _load_fitted_wordmark(path, modified_ns, byte_size, width, height):
    # The stat fields invalidate an edited asset. Only the small fitted image is
    # retained; the decoded source, alpha channel and transparent padding close here.
    with safe_open_image(path, limits=_LIMITS) as source, source.convert("RGBA") as rgba:
        with rgba.getchannel("A") as alpha, alpha.point([0] * 9 + [255] * 247) as visible:
            bounds = visible.getbbox()
        if bounds is None:
            return None
        # Tiny alpha specks outside the lettering must not dictate its scale.
        # Keep the original RGBA, including soft edges, within a small safety pad.
        left, top, right, bottom = bounds
        bounds = (max(0, left - 2), max(0, top - 2),
                  min(rgba.width, right + 2), min(rgba.height, bottom + 2))
        fitted = rgba.crop(bounds)
        fitted.thumbnail((width, height), Image.Resampling.LANCZOS)
        return fitted


def paste_local_wordmark(image, path, box):
    """Paste original-color artwork inside box, or return False for text fallback.

    Near-transparent outer dust is excluded from layout, content is only shrunk,
    and each axis is centered. At most three small RGBA images remain cached.
    """
    left, top, right, bottom = box
    width, height = right - left, bottom - top
    if (not all(type(value) is int for value in box)
            or not 0 <= left < right <= image.width
            or not 0 <= top < bottom <= image.height
            or width > _MAX_TARGET[0] or height > _MAX_TARGET[1]):
        return False
    try:
        asset = Path(path)
        info = asset.stat()
        fitted = _load_fitted_wordmark(str(asset), info.st_mtime_ns, info.st_size, width, height)
        if fitted is None:
            return False
        image.paste(fitted, (left + (width - fitted.width) // 2,
                             top + (height - fitted.height) // 2), fitted)
        return True
    except (OSError, ValueError, SyntaxError):
        return False
