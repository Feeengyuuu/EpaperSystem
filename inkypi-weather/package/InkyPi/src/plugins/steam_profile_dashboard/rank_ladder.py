"""The Steam level read as a ranked ladder: ten tiers, the lower seven with four divisions.

Each tier has a generated promotion border (assets/rank, built by
tools/build_steam_rank_borders.py) whose empty centre carries the level number. The
ladder only reads the public level and badge XP, so it moves as the account levels up.
"""

from dataclasses import dataclass
from functools import lru_cache
import json
import math
from pathlib import Path

from PIL import Image, ImageOps

from utils.safe_image import ImageLimits, safe_open_image

ASSET_ROOT = Path(__file__).resolve().parent / "assets" / "rank"
# (key, Chinese name, English name, first level, colour for type and ladder pips)
TIERS = (
    ("iron", "黑铁", "IRON", 0, (150, 140, 134)),
    ("bronze", "青铜", "BRONZE", 10, (196, 128, 78)),
    ("silver", "白银", "SILVER", 20, (190, 204, 214)),
    ("gold", "黄金", "GOLD", 30, (240, 190, 70)),
    ("platinum", "铂金", "PLATINUM", 50, (96, 206, 194)),
    ("emerald", "翡翠", "EMERALD", 70, (60, 200, 128)),
    ("diamond", "钻石", "DIAMOND", 90, (130, 166, 255)),
    ("master", "大师", "MASTER", 120, (196, 116, 240)),
    ("grandmaster", "宗师", "GRANDMASTER", 150, (240, 96, 84)),
    ("challenger", "王者", "CHALLENGER", 200, (255, 214, 100)),
)
DIVISIONS = ("IV", "III", "II", "I")
# Master and above are single ranks, as in ranked play.
DIVIDED_TIERS = 7
_LIMITS = ImageLimits(max_bytes=4 * 1024 * 1024, max_width=1024, max_height=1024,
                      max_pixels=1_048_576, allowed_formats=frozenset({"PNG"}))


@dataclass(frozen=True)
class Rank:
    index: int
    key: str
    name: str
    english: str
    color: tuple
    division: str
    level: int
    next_name: str
    levels_to_next: int
    lp: int | None

    @property
    def title(self):
        return f"{self.name} {self.division}".strip()


def rank_for(level, badges=None):
    """The rank for a Steam level; ``lp`` is the XP share of the current level, 0-100."""
    try:
        level = max(0, int(level or 0))
    except (TypeError, ValueError):
        level = 0
    index = max(position for position, tier in enumerate(TIERS) if tier[3] <= level)
    key, name, english, start, color = TIERS[index]
    division, next_name, next_level = "", "", None
    if index + 1 < len(TIERS):
        end = TIERS[index + 1][3]
        if index < DIVIDED_TIERS:
            step = min(3, (level - start) * 4 // (end - start))
            division = DIVISIONS[step]
            if step < 3:
                next_name = f"{name} {DIVISIONS[step + 1]}"
                next_level = start + math.ceil((step + 1) * (end - start) / 4)
        if next_level is None:
            next_name, next_level = TIERS[index + 1][1], end
    return Rank(index, key, name, english, color, division, level, next_name,
                max(0, next_level - level) if next_level is not None else 0, _level_lp(badges))


def _level_lp(badges):
    badges = badges if isinstance(badges, dict) else {}
    try:
        xp = int(badges["player_xp"])
        floor = int(badges["player_xp_needed_current_level"])
        remaining = int(badges["player_xp_needed_to_level_up"])
    except (KeyError, TypeError, ValueError):
        return None
    span = xp - floor + remaining
    if span <= 0:
        return None
    return max(0, min(100, round(100 * (xp - floor) / span)))


@lru_cache(maxsize=1)
def _manifest():
    try:
        return json.loads((ASSET_ROOT / "borders.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


@lru_cache(maxsize=len(TIERS))
def _master(key):
    try:
        with safe_open_image(ASSET_ROOT / f"{key}.png", limits=_LIMITS) as opened:
            return opened.convert("RGBA")
    except (OSError, ValueError):
        return None


def rank_border(key, size):
    """The tier border contained in a ``size`` square, and its hole's centre and radius there."""
    master = _master(key)
    if master is None:
        return None, None
    fitted = ImageOps.contain(master, (size, size), method=Image.Resampling.LANCZOS)
    tile = Image.new("RGBA", (size, size))
    left, top = (size - fitted.width) // 2, (size - fitted.height) // 2
    tile.alpha_composite(fitted, (left, top))
    hole = (_manifest().get(key) or {}).get("hole")
    if not hole:
        return tile, (size / 2, size / 2, size * 0.22)
    scale = fitted.width / master.width
    cx, cy, radius = hole
    return tile, (left + cx * scale, top + cy * scale, radius * scale)
