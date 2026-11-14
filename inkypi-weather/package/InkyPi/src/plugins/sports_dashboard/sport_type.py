"""Readable sport-specific text; branding stays local to the panel being drawn.

Scores retain score_fonts' separate routing. Display faces are used only for
large, bold labels; dense text uses an open text face and Chinese uses Noto.
See assets/fonts/TYPOGRAPHY.md for the supported brands and fallback choices.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from functools import partial, wraps
from inspect import signature
from pathlib import Path

from utils.app_utils import get_base_ui_font
from utils.fonts import load_font

from .esports_font_runs import EsportsFont
from .render_fonts import render_font
from .score_fonts import football_score_league, score_font


_ACTIVE = ContextVar("sports_text_type", default=None)
SRC = Path(__file__).resolve().parents[2]
FONT_DIR = Path(__file__).resolve().parent / "assets/fonts"
CJK_PATH = SRC / "static/fonts/NotoSansSC-VF.ttf"
INTER_DIR = SRC / "plugins/flight_radar/assets/fonts"
# The less condensed text cut keeps gridiron/hockey names compact without using
# their narrow score figures for every label. Other sports keep open counters.
COMPACT_TEXT = frozenset({"NFL", "NCAA", "NHL"})
BRAND_DISPLAY = frozenset({"NBA", "WNBA", "SOCCER", "F1"})
SPORTS = frozenset({"NBA", "WNBA", "NFL", "NCAA", "NHL", "MLB", "PGA", "F1", "SOCCER", "TENNIS", "MOTOGP"})


@contextmanager
def typography_scope(sport, league=None):
    """Own a panel's text choice, restoring the caller even after an exception."""
    sport = str(sport or "").upper()
    token = _ACTIVE.set((sport, league) if sport in SPORTS else None)
    try:
        yield
    finally:
        _ACTIVE.reset(token)


def sport_panel(sport=None, *, selection=None, game=None):
    """Scope one panel or one mixed-wall game, including direct preview calls."""
    def decorate(method):
        parameters = signature(method)

        @wraps(method)
        def wrapped(*args, **kwargs):
            values = parameters.bind(*args, **kwargs).arguments if selection or game else {}
            selected = values.get(selection) or {}
            row = values.get(game) or {}
            chosen = row.get("sport") if game else sport
            league = football_score_league(selected.get("main"), selected.get("presentation")) if selection else None
            with typography_scope(chosen, league):
                return method(*args, **kwargs)
        return wrapped
    return decorate


def _load_cjk(size, bold=False):
    try:
        font = load_font(CJK_PATH, size)
        font.set_variation_by_axes([600 if bold else 500])
        return font
    except (OSError, ValueError, TypeError):
        return get_base_ui_font(size, bold=bold)


def _load_text(size, bold=False, *, compact=False):
    path = (FONT_DIR / f"BarlowSemiCondensed-{'Bold' if bold else 'SemiBold'}.ttf" if compact
            else INTER_DIR / f"Inter-{'Bold' if bold else 'SemiBold'}.ttf")
    fallback = render_font(_load_cjk, size, bold)
    try:
        primary = load_font(path, size)
        # Match cap height, not em size: Barlow's nominal 10px is visibly smaller
        # than the Chinese labels beside it. Preserve the requested line box.
        target = get_base_ui_font(size, bold=bold).getbbox("8H")
        box = primary.getbbox("8H")
        actual = max(1, round(size * (target[3] - target[1]) / max(1, box[3] - box[1])))
        if actual != size:
            primary = load_font(path, actual)
        return EsportsFont(primary, fallback)
    except (OSError, ValueError, TypeError):
        return fallback


_TEXT_LOADERS = {False: partial(_load_text, compact=False), True: partial(_load_text, compact=True)}


def active_sport_font(size, bold=False):
    """Return a font only inside a sports text scope; never set a global UI font."""
    active = _ACTIVE.get()
    if active is None:
        return None
    sport, league = active
    size, bold = int(size), bool(bold)
    # These panels already choose their own Latin body face. Give those wrappers
    # a single CJK fallback rather than nesting a second Latin/CJK pairing.
    if sport in {"TENNIS", "MOTOGP"}:
        return render_font(_load_cjk, size, bold)
    fallback = render_font(_TEXT_LOADERS[sport in COMPACT_TEXT], size, bold)
    if sport in BRAND_DISPLAY and bold and size >= 14:
        primary = score_font(sport, size, True, league=league)
        return EsportsFont(primary, fallback.fallback if isinstance(fallback, EsportsFont) else fallback)
    return fallback
