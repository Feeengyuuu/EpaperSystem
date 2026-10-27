"""Quiet doodle patterns laid under a page area before (or after) its content.

A pattern is a tile of sparse line-art doodles in one ink on white. It is
stored as a greyscale density map and tinted at draw time, so the same doodles
take each card's own colour by day and night: the tint is laid over whatever
the area already holds at ``strength`` of each stroke's density. Laid before
the content, the type and pictures sit on top; laid after, ``grounds`` limits
it to the plain background pixels so the content stays untouched.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageOps

# How much of a stroke shows: noticed on a second look, never in the way of the type.
DOODLE_STRENGTH = 0.22


def paint_doodles(image, box, pattern, tint, *, strength=DOODLE_STRENGTH, grounds=None, radius=0, offset=(0, 0)):
    """Lay ``pattern`` (a doodle tile path) tinted ``tint`` over ``box`` (inclusive) of ``image``.

    ``grounds`` (colours) keeps the doodles to pixels that are exactly one of
    them; ``radius`` rounds the box's corners; ``offset`` shifts the tiling so
    neighbouring areas do not repeat the same corner. Returns False when
    nothing is drawn (a tiny box or a missing pattern).
    """
    x1, y1, x2, y2 = (int(value) for value in box)
    size = (x2 - x1 + 1, y2 - y1 + 1)
    if min(size) < 8:
        return False
    try:
        density = _tiled_density(str(pattern), size, tuple(int(value) for value in offset))
    except OSError:
        return False
    alpha = density.point(lambda value: round(value * strength))
    if grounds:
        region = image.crop((x1, y1, x1 + size[0], y1 + size[1])).convert("RGB")
        alpha = ImageChops.multiply(alpha, exact_colour_mask(region, grounds))
    if radius:
        alpha = ImageChops.multiply(alpha, _rounded_mask(size, int(radius)))
    image.paste(Image.new("RGB", size, tuple(int(value) for value in tint[:3])), (x1, y1), alpha)
    return True


def exact_colour_mask(region, colours):
    """255 where a pixel of the RGB ``region`` is exactly one of ``colours``, else 0."""
    mask = Image.new("L", region.size, 0)
    for colour in colours:
        ground = Image.new("RGB", region.size, tuple(int(value) for value in colour[:3]))
        red, green, blue = ImageChops.difference(region, ground).split()
        spread = ImageChops.lighter(ImageChops.lighter(red, green), blue)
        mask = ImageChops.lighter(mask, spread.point(lambda value: 255 if value == 0 else 0))
    return mask


@lru_cache(maxsize=12)
def _density_tile(pattern):
    with Image.open(Path(pattern)) as tile:
        # Ink on white: the darker the stroke, the denser.
        return ImageOps.invert(tile.convert("L"))


@lru_cache(maxsize=16)
def _tiled_density(pattern, size, offset):
    tile = _density_tile(pattern)
    canvas = Image.new("L", size, 0)
    start_x = -(offset[0] % tile.width)
    start_y = -(offset[1] % tile.height)
    for top in range(start_y, size[1], tile.height):
        for left in range(start_x, size[0], tile.width):
            canvas.paste(tile, (left, top))
    return canvas


@lru_cache(maxsize=8)
def _rounded_mask(size, radius):
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius=radius, fill=255)
    return mask
