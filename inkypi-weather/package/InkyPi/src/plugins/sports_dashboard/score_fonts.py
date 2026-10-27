"""Score-only type, with optional owner-supplied fonts and render-scoped ownership."""

from functools import partial
import os
from pathlib import Path

from utils.app_utils import get_base_ui_font
from utils.fonts import load_font

from .render_fonts import render_font


FONT_DIR = Path(__file__).resolve().parent / "assets" / "fonts"
SCORE_SPORTS = frozenset({"NBA", "WNBA", "MLB", "NFL", "NCAA", "SOCCER", "PGA"})


def _load_score_font(size, bold=False, *, sport):
    reference = render_font(get_base_ui_font, size, bold)
    data = os.environ.get("INKYPI_DATA_DIR", "").strip()
    paths = []
    if data and sport in {"NBA", "WNBA"}:
        candidates = [Path(data) / "fonts" / f"sports-{sport.lower()}-score.{suffix}"
                      for suffix in ("ttf", "otf", "woff2")]
        existing = [path for path in candidates if path.is_file()]
        # Keep the original file format and avoid choosing between conflicting copies.
        paths = existing if len(existing) == 1 else []
    family = "BarlowCondensed" if sport in {"NBA", "WNBA"} else "BarlowSemiCondensed"
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


def score_font(sport, size, bold=False):
    loader = _LOADERS.get(str(sport).upper(), get_base_ui_font)
    return render_font(loader, size, bold)


def fit_score(draw, text, max_width, size, bold=False, min_size=8, *, sport, max_height=None):
    """Fit every score character; never ellipsize or drop a digit at a size cutoff."""
    text = str(text) if text is not None else "-"
    # The familiar lower bound is preferred; unusually wide scores can go smaller.
    sizes = [*range(size, min(size, min_size) - 1, -1), *range(min(size, min_size) - 1, 0, -1)]
    for candidate in sizes:
        font = score_font(sport, candidate, bold)
        if not font.covers(font.safe_text(text)):
            font = render_font(get_base_ui_font, candidate, bold)
        box = draw.textbbox((0, 0), text, font=font)
        if box[2] - box[0] <= max_width and (max_height is None or box[3] - box[1] <= max_height):
            return text, font
    return text, font
