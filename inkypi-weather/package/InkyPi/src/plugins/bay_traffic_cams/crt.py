"""The control-room look for Bay Traffic Cams: each still on a CRT security monitor, a dark console
behind them and VCR-style on-screen text.

A monitor is a charcoal bezel with a light top edge and a dark lower one, a deeper chin with its
power lamp, and a screen with rounded, bulging corners, faint scanlines and darker edges. The
on-screen text is the digital face of an old recorder with a black outline, so it reads on any
picture.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from utils.fonts import load_font


FONT = Path(__file__).resolve().parents[2] / "static" / "fonts" / "DS-DIGI" / "DS-DIGI.TTF"
# The console behind the monitors, warm phosphor text and the amber of an old status display.
PALETTE = {"paper": "#1b1d1f", "ink": "#ece4cc", "muted": "#b3ab92", "rule": "#55585b", "live": "#ff3b30",
           "stale": "#ffb000", "shade": "#000000"}
BEZEL = (54, 58, 62)
BEZEL_LIGHT = (98, 103, 108)
BEZEL_DARK = (24, 26, 28)
SCREEN_EDGE = (8, 9, 10)
LAMP = (60, 220, 90)
OSD = (245, 245, 240)
REC = (255, 46, 46)
SCANLINE_STEP = 3
SCANLINE_DIM = 0.93
VIGNETTE = 0.24


@lru_cache(maxsize=8)
def osd_font(size):
    return load_font(str(FONT), size)


def osd_text(draw, xy, text, size, fill=OSD, anchor="la"):
    """Recorder-style on-screen text, outlined in black so it holds on bright or dark pictures."""
    draw.text(xy, text, font=osd_font(size), fill=fill, anchor=anchor, stroke_width=2 if size >= 13 else 1,
              stroke_fill=(0, 0, 0))
    return draw.textlength(text, font=osd_font(size))


@lru_cache(maxsize=16)
def _scanlines(size):
    pattern = Image.new("L", size, 255)
    draw = ImageDraw.Draw(pattern)
    for row in range(0, size[1], SCANLINE_STEP):
        draw.line((0, row, size[0], row), fill=round(255 * SCANLINE_DIM))
    return pattern.convert("RGB")


@lru_cache(maxsize=16)
def _vignette(size):
    """A multiplier that leaves the middle and darkens toward the tube's edges and corners."""
    width, height = size
    small = Image.new("L", (64, 64), 0)
    draw = ImageDraw.Draw(small)
    for step in range(32):
        level = round(255 * (1 - VIGNETTE * (1 - step / 31) ** 2))
        draw.rounded_rectangle((step, step, 63 - step, 63 - step), radius=max(1, 18 - step // 2), fill=level)
    return small.resize(size, Image.Resampling.BICUBIC).filter(ImageFilter.GaussianBlur(2)).convert("RGB")


def screen_box(box, bezel, chin):
    x0, y0, x1, y1 = box
    return x0 + bezel, y0 + bezel, x1 - bezel, y1 - chin


def monitor(image, box, tile=None, *, bezel=6, chin=10, radius=12):
    """Draw a CRT monitor filling ``box`` with ``tile`` (an RGB picture at the screen's size, or
    None for a dark screen) on it, and return the screen's box."""
    x0, y0, x1, y1 = (int(value) for value in box)
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((x0, y0, x1, y1), radius=radius - 2, fill=BEZEL)
    draw.arc((x0, y0, x0 + 2 * radius, y0 + 2 * radius), 180, 270, fill=BEZEL_LIGHT, width=2)
    draw.line((x0 + radius, y0 + 1, x1 - radius, y0 + 1), fill=BEZEL_LIGHT, width=2)
    draw.line((x0 + 1, y0 + radius, x0 + 1, y1 - radius), fill=BEZEL_LIGHT, width=2)
    draw.line((x0 + radius, y1 - 1, x1 - radius, y1 - 1), fill=BEZEL_DARK, width=2)
    draw.line((x1 - 1, y0 + radius, x1 - 1, y1 - radius), fill=BEZEL_DARK, width=2)
    sx0, sy0, sx1, sy1 = screen = screen_box((x0, y0, x1, y1), bezel, chin)
    size = (max(1, sx1 - sx0), max(1, sy1 - sy0))
    draw.rounded_rectangle((sx0 - 2, sy0 - 2, sx1 + 1, sy1 + 1), radius=radius, fill=SCREEN_EDGE)
    picture = tile.convert("RGB").resize(size) if tile is not None else Image.new("RGB", size, (14, 20, 18))
    picture = ImageChops.multiply(picture, _scanlines(size))
    picture = ImageChops.multiply(picture, _vignette(size))
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius=radius, fill=255)
    image.paste(picture, (sx0, sy0), mask)
    # The chin: a power lamp at the right and two adjustment knobs.
    lamp_y = (sy1 + y1) / 2
    draw.ellipse((x1 - bezel - 7, lamp_y - 2, x1 - bezel - 3, lamp_y + 2), fill=LAMP)
    for offset in (14, 22):
        draw.ellipse((x1 - bezel - offset - 3, lamp_y - 2, x1 - bezel - offset + 1, lamp_y + 2), fill=BEZEL_DARK)
    return screen
