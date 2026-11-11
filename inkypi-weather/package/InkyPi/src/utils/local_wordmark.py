"""Small, bounded local wordmarks with their original colors and transparency."""

from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path

from PIL import Image

from utils.safe_image import ImageLimits, safe_open_image
from utils.theme_utils import relative_luminance


_LIMITS = ImageLimits(max_bytes=4 * 1024 * 1024, max_width=2048, max_height=2048,
                      max_pixels=2048 * 2048, allowed_formats=frozenset({"PNG"}))
_MAX_TARGET = (512, 128)


@lru_cache(maxsize=8)
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


def paste_local_wordmark(image, path, box, *, align="center"):
    """Paste original-color artwork inside box; return its pasted box, or False for text fallback.

    Near-transparent outer dust is excluded from layout and content is only shrunk.
    It is centered vertically, and horizontally unless ``align="left"`` pins it to the
    box's left edge. At most eight small RGBA images (one page's worth) remain cached.
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
        x = left if align == "left" else left + (width - fitted.width) // 2
        y = top + (height - fitted.height) // 2
        image.paste(fitted, (x, y), fitted)
        return (x, y, x + fitted.width, y + fitted.height)
    except (OSError, ValueError, SyntaxError):
        return False


@lru_cache(maxsize=32)
def _load_source_logo(path, modified_ns, byte_size):
    with safe_open_image(path, limits=_LIMITS) as source:
        logo = source.convert("RGBA")
    bounds = logo.getchannel("A").point(lambda value: 255 if value > 16 else 0).getbbox()
    return logo.crop(bounds) if bounds else None


@lru_cache(maxsize=128)
def _sized_source_logo(path, modified_ns, byte_size, height, ink, dark_parts):
    # One entry per logo as a page draws it (its height and night ink), so a redraw costs neither
    # the resize nor the pixel walk. The entries are small: a logo is a few dozen pixels tall.
    logo = _load_source_logo(path, modified_ns, byte_size)
    if logo is None:
        return None
    logo = logo.resize((max(1, round(logo.width * height / logo.height)), int(height)), Image.Resampling.LANCZOS)
    if ink is not None:
        pixels = logo.load()
        for y in range(logo.height):
            for x in range(logo.width):
                red, green, blue, alpha = pixels[x, y]
                if not alpha:
                    continue
                neutral = max(red, green, blue) < 110 and max(red, green, blue) - min(red, green, blue) < 40
                if neutral or (dark_parts and relative_luminance((red, green, blue)) < 0.09):
                    pixels[x, y] = (*ink, alpha)
    return logo


def load_source_logo(path, height, ink=None, *, dark_parts=False):
    """A data source's official logo ``height`` px tall (tools/build_source_logos.py), or None
    without the file. With ``ink`` (a dark page) its near-black lettering takes that colour, and
    with ``dark_parts`` every dark part of it too (a logo its maker prints light on dark grounds);
    its coloured parts stay as printed. The image is the caller's own to paste or change."""
    ink = None if ink is None else tuple(ink)[:3]
    try:
        asset = Path(path)
        info = asset.stat()
        if height < 1:
            return None
        logo = _sized_source_logo(str(asset), info.st_mtime_ns, info.st_size, height, ink,
                                  bool(dark_parts) and ink is not None)
    except (OSError, ValueError, SyntaxError):
        return None
    return None if logo is None else logo.copy()


def source_logo_width(path, height):
    """How wide load_source_logo makes the logo at ``height``, without scaling it; None without
    the file. For choosing a row's height before any logo is drawn."""
    try:
        asset = Path(path)
        info = asset.stat()
        logo = _load_source_logo(str(asset), info.st_mtime_ns, info.st_size)
    except (OSError, ValueError, SyntaxError):
        return None
    if logo is None or height < 1:
        return None
    return max(1, round(logo.width * height / logo.height))


def paste_source_logos(image, logos, left, middle, gap):
    """``logos`` (images) side by side from ``left``, centred on ``middle``; the row's right end."""
    x = left
    for logo in logos:
        image.paste(logo, (round(x), round(middle - logo.height / 2)), logo)
        x += logo.width + gap
    return x - gap if logos else left


def paste_header_logo_row(image, logos, left, right, middle, gap, spare=(), *, snap=True):
    """A page header's source logos in one row centred between ``left`` and ``right``, each
    centred on ``middle`` and ``gap`` apart; the row's (left, right), or None when none was drawn.

    ``logos`` is a mapping of name to image in the row's order, or a list of images; a missing
    logo (None) is left out. A row too wide gives up the ``spare`` names first, in that order,
    then its last logos. With ``snap`` the row starts on a whole pixel, so its logos stand exactly
    ``gap`` apart; without it the row keeps its exact centre and each logo is rounded on its own.
    """
    row = dict(logos) if isinstance(logos, Mapping) else dict(enumerate(logos))
    row = {name: logo for name, logo in row.items() if logo is not None}
    spare = [name for name in spare if name in row]

    def width():
        return sum(logo.width for logo in row.values()) + gap * (len(row) - 1)

    while row and width() > right - left:
        row.pop(spare.pop(0) if spare else next(reversed(row)))
    if not row:
        return None
    start = (left + right - width()) / 2
    if snap:
        start = round(start)
    return start, paste_source_logos(image, list(row.values()), start, middle, gap)
