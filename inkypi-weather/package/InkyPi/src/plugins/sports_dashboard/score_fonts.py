"""Score-only type, with optional owner-supplied fonts and render-scoped ownership."""

from functools import partial
import os
from pathlib import Path

from utils.app_utils import get_base_ui_font
from utils.fonts import load_font

from .render_fonts import render_font


FONT_DIR = Path(__file__).resolve().parent / "assets" / "fonts"
SCORE_SPORTS = frozenset({"NBA", "WNBA", "MLB", "NFL", "NCAA", "SOCCER", "PGA", "F1"})
CLUB_PRIVATE_FONT_KEYS = {
    "MLS": "mls", "PL": "epl", "PD": "laliga", "BL1": "bundesliga",
    "SA": "seriea", "FL1": "ligue1",
}


def football_score_league(event, presentation=None):
    """Use normalized provider league codes, never team names or display labels."""
    code = str((event or {}).get("league_code") or "").upper()
    if code:
        return code if code in CLUB_PRIVATE_FONT_KEYS else None
    if (presentation or {}).get("competition") == "club":
        code = str(presentation.get("league_code") or "").upper()
        return code if code in CLUB_PRIVATE_FONT_KEYS else None
    return None


def _load_score_font(size, bold=False, *, sport, private_key=None):
    reference = render_font(get_base_ui_font, size, bold)
    data = os.environ.get("INKYPI_DATA_DIR", "").strip()
    paths = []
    if private_key is None and sport in {"NBA", "WNBA", "F1"}:
        private_key = sport.lower()
    if data and private_key:
        candidates = [Path(data) / "fonts" / f"sports-{private_key}-score.{suffix}"
                      for suffix in ("ttf", "otf", "woff", "woff2")]
        existing = [path for path in candidates if path.is_file()]
        # Keep the original file format and avoid choosing between conflicting copies.
        paths = existing if len(existing) == 1 else []
    family = "BarlowCondensed" if sport in {"NBA", "WNBA"} else "BarlowSemiCondensed"
    if sport != "F1":
        paths.append(FONT_DIR / f"{family}-{'Bold' if bold else 'SemiBold'}.ttf")
    target = reference.getbbox("8")
    target_height = target[3] - target[1]
    for path in paths:
        try:
            font = load_font(path, size)
            if not font.covers("0123456789-"):
                continue
            box = font.getbbox("8")
            height = max(1, box[3] - box[1])
            # Compare visible digits, not unlike nominal em sizes across families.
            actual = max(1, round(size * target_height / height))
            return font if actual == size else load_font(path, actual)
        except (OSError, ValueError):
            continue
    return reference


# Only loader functions live here; native font objects retain the existing weak scope.
_LOADERS = {sport: partial(_load_score_font, sport=sport) for sport in SCORE_SPORTS}
_CLUB_LOADERS = {
    code: partial(_load_score_font, sport="SOCCER", private_key=key)
    for code, key in CLUB_PRIVATE_FONT_KEYS.items()
}


def score_font(sport, size, bold=False, *, league=None):
    sport = str(sport).upper()
    loader = _LOADERS.get(sport, get_base_ui_font)
    if sport == "SOCCER":
        loader = _CLUB_LOADERS.get(str(league).upper(), loader)
    return render_font(loader, size, bold)


def fit_score(draw, text, max_width, size, bold=False, min_size=8, *, sport, max_height=None, league=None):
    """Fit every score character; never ellipsize or drop a digit at a size cutoff."""
    text = str(text) if text is not None else "-"
    # The familiar lower bound is preferred; unusually wide scores can go smaller.
    sizes = [*range(size, min(size, min_size) - 1, -1), *range(min(size, min_size) - 1, 0, -1)]
    for candidate in sizes:
        font = score_font(sport, candidate, bold, league=league)
        if not font.covers(font.safe_text(text)):
            font = render_font(get_base_ui_font, candidate, bold)
        box = draw.textbbox((0, 0), text, font=font)
        if box[2] - box[0] <= max_width and (max_height is None or box[3] - box[1] <= max_height):
            return text, font
    return text, font
