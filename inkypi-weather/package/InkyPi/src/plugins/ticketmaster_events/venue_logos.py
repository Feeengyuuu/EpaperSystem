"""The official logos of the Bay Area venues Local Events names.

Each venue's logo comes from its own site (assets/venues, venues.source.json lists the names
Ticketmaster gives it); the arenas and ballparks the sports page already carries are reused from
its folder. A venue is found by its name without case, punctuation or "the". On the page the
logo keeps its colours; the parts too close to the ground take the page's ink or paper.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageStat

from utils.safe_image import safe_open_image
from utils.theme_utils import contrast_ratio

VENUE_DIR = Path(__file__).with_name("assets") / "venues"
SPORTS_VENUE_DIR = Path(__file__).resolve().parents[1] / "sports_dashboard" / "assets" / "logos" / "venues"
# The sports page's venues that host concerts and shows here, by the names Ticketmaster gives them.
SPORTS_VENUES = {"chase_center": ("Chase Center",), "sap_center_at_san_jose": ("SAP Center at San Jose", "SAP Center"),
                 "levi_s_stadium": ("Levi's Stadium",), "oracle_park": ("Oracle Park",), "paypal_park": ("PayPal Park",)}
MIN_CONTRAST = 2.4
# A logo filling at least this share of its box is a badge on its own ground (a disc, a shield):
# it stays as printed rather than having its fill swapped for the page's ink.
BADGE_COVER = 0.55


def _key(name):
    words = re.findall(r"[a-z0-9]+", str(name or "").lower().replace("'", ""))
    return " ".join(word for word in words if word != "the")


@lru_cache(maxsize=1)
def _index():
    index = {}
    try:
        logos = json.loads((VENUE_DIR / "venues.source.json").read_text(encoding="utf-8")).get("logos", {})
    except (OSError, ValueError):
        logos = {}
    for key, entry in logos.items():
        for name in entry.get("names") or ():
            index.setdefault(_key(name), VENUE_DIR / f"{key}.png")
    for key, names in SPORTS_VENUES.items():
        for name in names:
            index.setdefault(_key(name), SPORTS_VENUE_DIR / f"{key}.png")
    return index


def venue_logo_path(name):
    """The logo file for a venue called ``name``, or None."""
    path = _index().get(_key(name))
    return path if path is not None and path.is_file() else None


@lru_cache(maxsize=64)
def _art(path):
    with safe_open_image(path) as source:
        return source.convert("RGBA")


@lru_cache(maxsize=64)
def _is_badge(path):
    alpha = _art(path).getchannel("A").point(lambda value: 255 if value > 128 else 0)
    return alpha.histogram()[255] >= BADGE_COVER * alpha.width * alpha.height


def paste_venue_logo(image, name, x, middle, box, ink, paper):
    """The venue's logo contained in ``box`` (width, height) from ``x``, centred on ``middle``;
    returns its width, or 0 without one. Its parts too close to the ground show in ``ink`` or
    ``paper``, whichever reads on it; its brand colours stay."""
    path = venue_logo_path(name)
    if path is None:
        return 0
    try:
        art = _art(path)
    except (OSError, ValueError):
        return 0
    scale = min(box[0] / art.width, box[1] / art.height)
    logo = art.resize((max(1, round(art.width * scale)), max(1, round(art.height * scale))), Image.Resampling.LANCZOS)
    top = round(middle - logo.height / 2)
    left = round(x)
    if _is_badge(path):
        image.paste(logo, (left, top), logo)
        return logo.width
    ground = tuple(ImageStat.Stat(image.convert("RGB").crop((left, top, left + logo.width, top + logo.height))).median)
    swap = max((tuple(ink), tuple(paper)), key=lambda colour: contrast_ratio(colour, ground))
    pixels = logo.load()
    for py in range(logo.height):
        for px in range(logo.width):
            red, green, blue, alpha = pixels[px, py]
            if alpha >= 8 and contrast_ratio((red, green, blue), ground) < MIN_CONTRAST:
                pixels[px, py] = (*swap, alpha)
    image.paste(logo, (left, top), logo)
    return logo.width
