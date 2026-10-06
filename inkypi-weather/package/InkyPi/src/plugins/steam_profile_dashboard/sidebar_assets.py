"""Read the bundled sidebar artwork without network or writable cache state."""

from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageOps

from utils.safe_image import ImageLimits, safe_open_image


ASSET_ROOT = Path(__file__).resolve().parent / "assets" / "sidebar"
ASSET_NAMES = frozenset({"games", "friends", "recent", "total", "badges", "xp", "level_frame"})
_LIMITS = ImageLimits(max_bytes=8 * 1024 * 1024, max_width=2048,
                      max_height=2048, max_pixels=4_194_304,
                      allowed_formats=frozenset({"PNG"}))


@lru_cache(maxsize=7)
def _source(name):
    if name not in ASSET_NAMES:
        return None
    try:
        with safe_open_image(ASSET_ROOT / f"{name}.png", limits=_LIMITS) as opened:
            image = opened.convert("RGBA")
        bounds = image.getchannel("A").getbbox()
        if bounds is None:
            image.close()
            return None
        cropped = image.crop(bounds)
        image.close()
        # Retain only a small in-memory master for the 18–54 px display assets.
        cropped.thumbnail((128, 128), Image.Resampling.LANCZOS)
        return cropped
    except (OSError, ValueError):
        return None


def sidebar_asset(name, size):
    """Contain visible alpha bounds in a fresh transparent square.

    The source PNG is immutable package artwork. Missing or invalid artwork
    returns None so the renderer can retain its existing neutral symbols.
    """
    if name not in ASSET_NAMES or type(size) is not int or not 1 <= size <= 128:
        return None
    source = _source(name)
    if source is None:
        return None
    fitted = ImageOps.contain(source, (size, size), method=Image.Resampling.LANCZOS)
    tile = Image.new("RGBA", (size, size))
    tile.alpha_composite(fitted, ((size - fitted.width) // 2, (size - fitted.height) // 2))
    return tile
