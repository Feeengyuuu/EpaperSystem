"""16x16 pixel icons for the MLB card's people rows.

W (winning pitcher) a trophy, L (losing pitcher) a falling arrow, S (save) a
lifebuoy, P (pitcher) a pitch in flight and AB (at bat) a bat. ``K`` takes the
theme's outline ink; every other letter is a fixed fill, upper case lit and
lower case in shade, so the icons keep their colours by day and night.
"""

from __future__ import annotations

from functools import lru_cache

from PIL import Image

BASEBALL_ICON_SIZE = 16

BASEBALL_ICON_FILLS = {
    "G": (230, 179, 62), "g": (185, 131, 34), "H": (255, 239, 176),  # gold
    "R": (207, 46, 62), "r": (150, 32, 51),  # crimson
    "O": (238, 106, 44), "o": (185, 74, 24),  # lifebuoy orange
    "C": (251, 246, 234), "c": (216, 207, 187),  # cream
    "T": (215, 164, 100), "t": (169, 116, 63),  # ash wood
}

BASEBALL_ICONS = {
    "W": (
        "................",
        "................",
        "..KKKKKKKKKKKK..",
        "KKKGHGGGGGGggKKK",
        "K.KGHGGGGGGggK.K",
        "K.KGHGGGGGGggK.K",
        ".KKGHGGGGGGggKK.",
        "..KKGHGGGGggKK..",
        "...KKGGGGggKK...",
        "....KKGGggKK....",
        "......KGgK......",
        "......KGgK......",
        ".....KKGgKK.....",
        "....KGGGGggK....",
        "....KKKKKKKK....",
        "................",
    ),
    "L": (
        "................",
        "................",
        ".....KKKKKK.....",
        ".....KRRrrK.....",
        ".....KRRrrK.....",
        ".....KRRrrK.....",
        ".....KRRrrK.....",
        ".KKKKKRRrrKKKKK.",
        ".KRRRRRRrrrrrrK.",
        "..KRRRRRrrrrrK..",
        "...KRRRRrrrrK...",
        "....KRRRrrrK....",
        ".....KRRrrK.....",
        "......KRrK......",
        ".......KK.......",
        "................",
    ),
    "S": (
        ".......KK.......",
        "....KKKOOKKK....",
        "...KCCOOOOCCK...",
        "..KCCCOOOOCCCK..",
        ".KCCCCCOOCCCCCK.",
        ".KCCCCKKKKCCCcK.",
        ".KOOCK....KCooK.",
        "KOOOOK....KooooK",
        "KOOOOK....KooooK",
        ".KOOCK....KcooK.",
        ".KCCCCKKKKccccK.",
        ".KCCCCCoocccccK.",
        "..KCCCoooocccK..",
        "...KCcooooccK...",
        "....KKKooKKK....",
        ".......KK.......",
    ),
    "P": (
        "................",
        "................",
        ".......KKKKKK...",
        "......KKCCCCKK..",
        "..KKKKCCCCCCCCK.",
        "....KKRCCCCCCRKK",
        "....KCCRCCCCRCCK",
        "....KCCRCCCCRCcK",
        ".KKKKCCRCCCCRccK",
        "....KCCRCCCCRccK",
        "....KKRCCCCccRKK",
        "..KKKKCCCCccccK.",
        "......KKCcccKK..",
        ".......KKKKKK...",
        "................",
        "................",
    ),
    "AB": (
        "................",
        "...........KKK..",
        "..........KTTTK.",
        ".........KTTTtK.",
        "........KKTTttK.",
        "........KTTtttK.",
        ".......KTTttKK..",
        "......KTTttKK...",
        ".....KKTttK.....",
        "....KKTtKK......",
        "....KTtKK.......",
        "...KTtKK........",
        "..KTtK..........",
        ".KKKK...........",
        ".KKK............",
        "..K.............",
    ),
}


@lru_cache(maxsize=32)
def baseball_icon(name, outline):
    """The RGBA icon for a W/L/S/P/AB label drawn with ``outline``, or None."""
    rows = BASEBALL_ICONS.get(str(name or ""))
    if rows is None:
        return None
    inks = {**BASEBALL_ICON_FILLS, "K": tuple(outline)}
    icon = Image.new("RGBA", (BASEBALL_ICON_SIZE, BASEBALL_ICON_SIZE), (0, 0, 0, 0))
    icon.putdata([(*inks[ch], 255) if ch != "." else (0, 0, 0, 0) for row in rows for ch in row])
    return icon
