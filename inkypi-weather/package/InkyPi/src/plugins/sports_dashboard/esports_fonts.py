"""Scoped esports website typography; proprietary font bytes stay in device data.

These are website/organizer references, not a claim to each edition's broadcast
package. Unverified event identities retain the game's readable body face.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from functools import partial, wraps
import os
from pathlib import Path
import re

from utils.app_utils import get_base_ui_font
from utils.fonts import load_font

from .esports_font_runs import EsportsFont
from .render_fonts import render_font


_ACTIVE = ContextVar("esports_type", default=None)
_ROLE = ContextVar("esports_type_role", default="body")
PRIVATE_KEYS = (
    "esports-cs2", "esports-cs2-regular", "esports-dota", "esports-dota-regular",
    "esports-riot-heading", "esports-riot", "esports-riot-regular",
    "esports-blast-heading", "esports-blast", "esports-esl-heading", "esports-iem-heading",
    "esports-pgl-event", "esports-tesla-event", "esports-roboto-event",
    "esports-industry-event", "esports-gear-event", "esports-cct-event",
    "esports-dust2-event", "esports-montserrat-event",
)

# Match event identity only. Team/player names and arbitrary source labels never
# select a brand. ESEA / European Pro League are deliberately not ESL aliases.
_EVENT_RULES = (
    (r"\bBLAST\b", "blast"), (r"\bPGL\b", "pgl"),
    (r"\bIEM\b|Intel Extreme Masters", "iem"), (r"\bESL\b", "esl"),
    (r"\bTesla Cup\b", "tesla"),
    (r"StarLadder|StarSeries|Stake Ranked|Thunderpick", "roboto"),
    (r"CS Asia Championships|\bCAC\b|Perfect World.*Shanghai", "industry"),
    (r"Esports (?:Nations|World) Cup", "gear"), (r"\bCCT\b", "cct"),
    (r"Dust2(?:\.dk)?|POWER Ligaen", "dust2"),
    (r"European Pro League|Svenska|GOEXANIMO", "montserrat"),
)


def event_family(primary):
    primary = primary or {}
    main = primary.get("main") or {}
    name = " ".join(str(value or "") for value in (
        primary.get("event_name"), primary.get("sport"), main.get("event_name"), main.get("league_name"),
    ))
    return next((family for pattern, family in _EVENT_RULES if re.search(pattern, name, re.I)), "cs2")


@contextmanager
def type_scope(game, family=None):
    token = _ACTIVE.set((game, family or game))
    try:
        yield
    finally:
        _ACTIVE.reset(token)


def esports_sidebar(kind):
    def decorate(method):
        @wraps(method)
        def wrapped(self, image, left_width, selected, source_state, now, *args, **kwargs):
            primary = (selected or {}).get("primary") or {}
            game = "riot" if kind == "riot" else "dota" if primary.get("series") == "TI" else "cs2"
            family = event_family(primary) if game == "cs2" else game
            with type_scope(game, family):
                return method(self, image, left_width, selected, source_state, now, *args, **kwargs)
        return wrapped
    return decorate


def esports_role(role):
    def decorate(method):
        @wraps(method)
        def wrapped(*args, **kwargs):
            token = _ROLE.set(role)
            try:
                return method(*args, **kwargs)
            finally:
                _ROLE.reset(token)
        return wrapped
    return decorate


def _keys(game, family, role, bold):
    base = "esports-" + game + ("" if bold else "-regular")
    keys = []
    if family == "blast":
        keys.append("esports-blast-heading" if role == "heading" else "esports-blast")
    elif role == "heading" and game == "riot":
        keys.append("esports-riot-heading")
    elif role == "event" and game == "cs2":
        if family in {"esl", "iem"}:
            keys.append("esports-" + family + "-heading")
        elif family != "cs2":
            keys.append("esports-" + family + "-event")
    return [key for key in (*keys, base) if key in PRIVATE_KEYS]


def _load(size, bold=False, *, game, family, role):
    reference = render_font(get_base_ui_font, size, bold)
    data = os.environ.get("INKYPI_DATA_DIR", "").strip()
    if not data:
        return reference
    for key in _keys(game, family, role, bold):
        paths = [Path(data) / "fonts" / f"sports-{key}-score.{ext}" for ext in ("ttf", "otf", "woff", "woff2")]
        present = [path for path in paths if path.is_file()]
        if len(present) != 1:
            continue
        try:
            font = load_font(present[0], size)
            if not font.covers("0123456789-"):
                continue
            # A website variable font may default to its thin master.
            if key == "esports-montserrat-event":
                font.set_variation_by_axes([700])
            target, box = reference.getbbox("8H"), font.getbbox("8H")
            actual = max(1, round(size * (target[3] - target[1]) / max(1, box[3] - box[1])))
            if actual != size:
                font = font.font_variant(size=actual)
            return EsportsFont(font, reference)
        except (OSError, ValueError):
            continue
    return reference


_LOADERS = {}
for _game, _families in (("cs2", {"cs2", *(family for _, family in _EVENT_RULES)}),
                         ("dota", {"dota"}), ("riot", {"riot"})):
    for _family in _families:
        for _role in ("body", "heading", "event"):
            _LOADERS[(_game, _family, _role)] = partial(_load, game=_game, family=_family, role=_role)


def active_font(size, bold=False):
    active = _ACTIVE.get()
    if not active or _ROLE.get() == "original":
        return None
    return render_font(_LOADERS[(*active, _ROLE.get())], size, bold)


def active():
    return _ACTIVE.get() is not None and _ROLE.get() != "original"
