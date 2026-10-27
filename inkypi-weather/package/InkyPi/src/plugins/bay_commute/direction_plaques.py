"""Compact UI adaptations of FHWA M3 cardinal-direction auxiliary plaques.

Reference: MUTCD 11th Edition, section 2D.15 / figure 2D-5:
https://mutcd.fhwa.dot.gov/pdfs/11th_Edition/Chapter2d.pdf
These labels report the source's closure direction, never a camera's viewing angle.
"""

from functools import lru_cache
from pathlib import Path
import re

from utils.app_utils import get_base_ui_font
from utils.fonts import load_font


FONT_PATH = Path(__file__).resolve().parent.parent / "bay_traffic_cams/assets/fonts/RG2014C.ttf"
ALIASES = {"N": "NORTH", "NB": "NORTH", "NORTH": "NORTH", "NORTHBOUND": "NORTH", "北向": "NORTH",
           "S": "SOUTH", "SB": "SOUTH", "SOUTH": "SOUTH", "SOUTHBOUND": "SOUTH", "南向": "SOUTH",
           "E": "EAST", "EB": "EAST", "EAST": "EAST", "EASTBOUND": "EAST", "东向": "EAST",
           "W": "WEST", "WB": "WEST", "WEST": "WEST", "WESTBOUND": "WEST", "西向": "WEST"}


def cardinal_directions(value):
    """Accept only explicit cardinal directions or a complete, opposite-direction pair."""
    raw = str(value or "").strip().upper()
    if raw in {"东西双向", "南北双向"}:
        return ("EAST", "WEST") if raw == "东西双向" else ("NORTH", "SOUTH")
    parts = [part.strip() for part in re.split(r"\s*/\s*|\s+AND\s+|\s*&\s*", raw)]
    values = tuple(ALIASES.get(part) for part in parts)
    if len(values) == 1 and values[0]:
        return values
    if len(values) == 2:
        for pair in (("NORTH", "SOUTH"), ("EAST", "WEST")):
            if set(values) == set(pair):
                return pair
    return ()


@lru_cache(maxsize=4)
def _legend_font(cap_height):
    try:
        for size in range(cap_height, cap_height * 2 + 1):
            face = load_font(FONT_PATH, size)
            box = face.getbbox("H")
            if box[3] - box[1] >= cap_height:
                return face
    except OSError:
        pass
    return get_base_ui_font(cap_height, bold=True)


def draw_direction(page, value, *, right, top, interstate=False):
    directions = cardinal_directions(value)
    if not directions:
        page.text((right, top + 4), "方向未报", 10, "muted", align="right", width=112)
        return
    # M3's first letter is 10% taller; 12/11 cap heights approximate that at native resolution.
    first, rest = _legend_font(12), _legend_font(11)
    width, height, gap = 44, 18, 4
    left = right - (width * len(directions) + gap * (len(directions) - 1))
    ink, paper = ("#ffffff", "#00549f") if interstate else ("#111111", "#ffffff")
    d = page.draw
    for label in directions:
        box = (left, top, left + width - 1, top + height - 1)
        d.rounded_rectangle(box, radius=2, fill=paper)
        d.rounded_rectangle((left + 1, top + 1, left + width - 2, top + height - 2),
                            radius=1, outline=ink, width=1)
        text_width = d.textlength(label[0], font=first) + d.textlength(label[1:], font=rest)
        x, baseline = left + (width - text_width) / 2, top + 14
        bounds = []
        for text, face in ((label[0], first), (label[1:], rest)):
            d.text((x, baseline), text, font=face, fill=ink, anchor="ls")
            bounds.append(d.textbbox((x, baseline), text, font=face, anchor="ls"))
            x += d.textlength(text, font=face)
        page.text_bounds.append((label, (min(b[0] for b in bounds), min(b[1] for b in bounds),
                                         max(b[2] for b in bounds), max(b[3] for b in bounds))))
        left += width + gap
