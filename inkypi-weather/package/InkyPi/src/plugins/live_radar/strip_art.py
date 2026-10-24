"""Sticker parades for the empty strip beside each section title.

Every section has its own set of generated comic stickers: an audience going wild beside
LIVE NOW, more channels (a TV, a controller, popcorn) beside LIVE TOO and REPLAY, and a
sleeping room beside OFFLINE. They march along a dotted track the width of the strip,
shuffled and bobbing differently each refresh, with speed lines behind the live ones; the
more streamers a section holds, the closer they crowd.
"""

from __future__ import annotations

from functools import lru_cache
import os
import random

from PIL import Image, ImageDraw

STRIP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "strip_art")
STICKERS_PER_SET = 8
SECTION_STICKERS = {"LIVE NOW": "live_now", "LIVE TOO": "live_too", "REPLAY": "live_too", "OFFLINE": "offline"}
# Gap between stickers as a share of their height at CROWD_REFERENCE streamers: the live room
# is crowded, the sleeping one calm. Fewer streamers spread them out, more pack them in.
GAP_SHARE = {"live_now": 1.0, "live_too": 1.2, "offline": 1.6}
CROWD_REFERENCE = 8
CROWD_LIMITS = (0.6, 1.6)
MOVING = {"live_now", "live_too"}
TRACK_PITCH = 5
# The tallest sticker is this much shorter than the strip, which leaves room to bob.
STICKER_INSET = 2
MAX_BOB = 1
MIN_STICKERS_WIDE = 2.2


@lru_cache(maxsize=64)
def _sticker(kind, number, scale_height):
    """A sticker at the strip's scale (scale_height is what the set's tallest becomes)."""
    path = os.path.join(STRIP_DIR, f"{kind}_{number}.png")
    try:
        with Image.open(path) as source:
            art = source.convert("RGBA")
    except OSError:
        return None
    scale = scale_height / 44
    size = (max(1, round(art.width * scale)), max(1, round(art.height * scale)))
    return art.resize(size, Image.Resampling.LANCZOS)


def _mix(colour, other, share):
    return tuple(round(a + (b - a) * share) for a, b in zip(colour, other))


def parade(image, box, title, seed, ink, page, count=CROWD_REFERENCE):
    """Fill ``box`` with the section's stickers. Returns False when it is too narrow or has none."""
    kind = SECTION_STICKERS.get(str(title or "").upper())
    if kind is None:
        return False
    left, top, right, bottom = (int(round(value)) for value in box)
    height = bottom - top
    scale_height = height - STICKER_INSET
    if scale_height < 8 or right - left < scale_height * MIN_STICKERS_WIDE:
        return False
    rng = random.Random(f"{kind}:{seed}")
    crowd = min(CROWD_LIMITS[1], max(CROWD_LIMITS[0], (count or 0) / CROWD_REFERENCE))
    gap = round(scale_height * GAP_SHARE[kind] / crowd)
    chosen, used, bag = [], 0, []
    while True:
        if not bag:
            bag = rng.sample(range(1, STICKERS_PER_SET + 1), STICKERS_PER_SET)
            if chosen and bag[-1] == chosen[-1][0]:
                bag.reverse()
        number = bag.pop()
        art = _sticker(kind, number, scale_height)
        if art is None:
            return False
        need = art.width + (gap if chosen else 0)
        if used + need > right - left:
            break
        chosen.append((number, art))
        used += need
    if not chosen:
        return False
    draw = ImageDraw.Draw(image)
    middle = top + height // 2
    track = _mix(ink, page, 0.55)
    for x in range(left, right, TRACK_PITCH):
        draw.rectangle((x, middle, x + 1, middle + 1), fill=track)
    # What is left over spreads evenly, so the parade spans the whole strip.
    step = (right - left - sum(art.width for _number, art in chosen)) / len(chosen)
    x = left + step / 2
    for _number, art in chosen:
        bob = rng.randint(-MAX_BOB, MAX_BOB)
        y = max(top, min(bottom - art.height, top + (height - art.height) // 2 + bob))
        if kind in MOVING:
            for share, length in ((0.38, 5), (0.62, 3)):
                line_y = y + round(art.height * share)
                draw.line((round(x) - 3 - length, line_y, round(x) - 3, line_y), fill=ink, width=1)
        image.paste(art, (round(x), y), art)
        x += art.width + step
    return True
