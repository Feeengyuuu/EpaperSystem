"""How much a game deserves the screen right now.

Every region of the page ranks its candidates with one score:

    score = state points (live, how soon it starts, how recently it ended)
          + tier points (finals and championships above playoffs above the
            regular season), in full only on the game's day
          - a fairness penalty for what the panel showed last time

"The game's day" is while it is live, the 24 hours before it and the 24
hours after it ends; further out a big game keeps a quarter of its bonus. So
the World Series opener takes the panel on its day ahead of a regular-season
game tonight, while Worlds on a rest day does not hide a match starting soon.

The page reaches the panel only every couple of hours, so "last time" means
the last real display (see display_rotation), not the last render, and two
close candidates take turns from one display to the next.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta

TIER_POINTS = {"S": 60.0, "A": 40.0, "B": 20.0, "C": 0.0}
LIVE_POINTS = 100.0
# Shown at the last display, and at the one before it.
FAIRNESS_PENALTIES = (25.0, 12.0)
FINAL_TYPICAL_LENGTH = timedelta(hours=3)
GAME_DAY = timedelta(hours=24)
FAR_TIER_SHARE = 0.25

# (minutes until start, points) knots; points fall linearly between them.
_UPCOMING_KNOTS = (
    (0, 70.0), (180, 50.0), (720, 35.0), (1440, 25.0), (4320, 10.0), (10080, 2.0),
)
# (hours since the end, points) knots for finished games.
_FINAL_KNOTS = ((0, 45.0), (2, 45.0), (6, 30.0), (24, 12.0), (72, 3.0), (96, 0.0))


def _interpolate(knots, value):
    if value <= knots[0][0]:
        return knots[0][1]
    for (x1, y1), (x2, y2) in zip(knots, knots[1:]):
        if value <= x2:
            return y1 + (y2 - y1) * (value - x1) / (x2 - x1)
    # Past the last knot: a token point for far-off games, nothing for old results.
    last = knots[-1][1]
    return min(last, 1.0) if last > 0 else 0.0


def state_points(state, start, now, *, end=None):
    """Points for being live, about to start or just finished."""
    state = str(state or "").strip().lower()
    if state == "live":
        return LIVE_POINTS
    if not isinstance(start, datetime) or not isinstance(now, datetime):
        return 0.0
    if state == "final":
        finished = end if isinstance(end, datetime) else start + FINAL_TYPICAL_LENGTH
        hours = max(0.0, (now - finished).total_seconds() / 3600)
        return _interpolate(_FINAL_KNOTS, hours)
    minutes = (start - now).total_seconds() / 60
    if minutes < 0:
        # Past its start without a live or final status: about to go.
        return _UPCOMING_KNOTS[0][1]
    return _interpolate(_UPCOMING_KNOTS, minutes)


def tier_points(tier):
    return TIER_POINTS.get(str(tier or "C").upper(), 0.0)


def on_game_day(state, start, now, *, end=None):
    """Live, starting within a day, or ended within a day."""
    state = str(state or "").strip().lower()
    if state == "live":
        return True
    if not isinstance(start, datetime) or not isinstance(now, datetime):
        return False
    if state == "final":
        finished = end if isinstance(end, datetime) else start + FINAL_TYPICAL_LENGTH
        return now - finished <= GAME_DAY
    return start - now <= GAME_DAY


def score(state, start, now, tier="C", *, end=None):
    share = 1.0 if on_game_day(state, start, now, end=end) else FAR_TIER_SHARE
    return state_points(state, start, now, end=end) + tier_points(tier) * share


def fairness_penalty(key, recent_shown):
    """Penalty for a key shown at the last display(s); recent_shown is newest first."""
    penalty = 0.0
    for index, shown in enumerate(list(recent_shown or [])[: len(FAIRNESS_PENALTIES)]):
        if key in (shown or ()):
            penalty += FAIRNESS_PENALTIES[index]
    return penalty


def rank(candidates, recent_shown=()):
    """Sort {"key", "score", "order"?} candidates best first after fairness."""

    def sort_key(item):
        adjusted = float(item.get("score") or 0.0) - fairness_penalty(item.get("key"), recent_shown)
        return (-adjusted, item.get("order", 0), str(item.get("key") or ""))

    return sorted(candidates or [], key=sort_key)


# Tiers ------------------------------------------------------------------------


def _text(event, *keys):
    return " ".join(str((event or {}).get(key) or "") for key in keys).upper()


def _has(pattern, text):
    return re.search(pattern, text) is not None


def team_sport_tier(sport, event):
    """S: a final/championship game; A: playoffs; B: notable; C: regular season."""
    sport = str(sport or "").upper()
    event = event if isinstance(event, Mapping) else {}
    if sport == "MLB":
        series = _text(event, "series_game", "series_summary", "headline")
        if "WORLD SERIES" in series:
            return "S"
        if _has(r"\b(ALCS|NLCS|ALDS|NLDS)\b|CHAMPIONSHIP SERIES|DIVISION SERIES|WILD CARD", series):
            return "A"
        return "C"
    if sport in {"NBA", "WNBA"}:
        text = _text(event, "headline", "block", "series_summary")
        if _has(r"(?<![A-Z])(NBA |WNBA )?FINALS", text) and not _has(r"(CONF(ERENCE|\.)?|SEMI)[ -]?FINALS", text):
            return "S"
        if _has(r"CONF(ERENCE|\.)? FINALS|SEMIFINALS|PLAYOFF|POSTSEASON|\bPOST\b|ROUND|FIRST ROUND", text):
            return "A"
        if "NBA CUP" in text:
            return "A" if _has(r"CHAMPIONSHIP|FINAL", text) else "B"
        if "PLAY-IN" in text or "PLAY IN" in text:
            return "B"
        return "C"
    if sport == "NFL":
        text = _text(event, "headline", "note", "week_label", "season_label")
        if "SUPER BOWL" in text:
            return "S"
        if _has(r"WILD CARD|DIVISIONAL|CONFERENCE CHAMPIONSHIP|(AFC|NFC) CHAMPIONSHIP|POSTSEASON|PLAYOFF", text):
            return "A"
        # Seventeen weekly games a team: every NFL game is an event.
        return "B"
    if sport == "NCAA":
        text = _text(event, "headline", "note", "week_label")
        if _has(r"NATIONAL CHAMPIONSHIP|CFP NATIONAL", text):
            return "S"
        if _has(r"PLAYOFF|\bCFP\b|BOWL|CONFERENCE CHAMPIONSHIP|CHAMPIONSHIP GAME", text):
            return "A"
        ranks = [_rank(event.get("team_a_rank")), _rank(event.get("team_b_rank"))]
        if all(value <= 25 for value in ranks) or min(ranks) <= 5:
            return "B"
        return "C"
    if sport == "PGA":
        name = _text(event, "name_en", "name")
        if _has(r"MASTERS|PGA CHAMPIONSHIP|U\.?S\.? OPEN|OPEN CHAMPIONSHIP|THE OPEN\b", name):
            return "A"
        if _has(r"PLAYERS|TOUR CHAMPIONSHIP|FEDEX|BMW CHAMPIONSHIP|RYDER CUP", name):
            return "B"
        return "C"
    return "C"


def _rank(value):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 99
    return number if 0 < number < 99 else 99


def f1_session_tier(session_name):
    name = str(session_name or "").upper()
    if "SPRINT" in name and "QUAL" not in name and "SHOOTOUT" not in name:
        return "B"
    if "RACE" in name or name in {"GRAND PRIX", "GP"}:
        return "A"
    if "QUAL" in name or "SHOOTOUT" in name:
        return "B"
    return "C"


def esports_tier(kind, league="", name=""):
    """Esports tiers: world championships S; international events, league playoffs
    and grand finals A; everything else C."""
    kind = str(kind or "").lower()
    league = str(league or "").upper()
    name = str(name or "").upper()
    if league == "WORLDS" or (kind == "valve" and league == "TI"):
        return "S"
    if kind == "valve" and league == "CS" and "MAJOR" in name:
        return "S"
    if league == "MSI" or kind == "ewc":
        return "A"
    if "GRAND FINAL" in name:
        return "A"
    # A LoL league's season playoffs matter; every CS2 tournament has a playoff
    # bracket, so there only the grand final stands out.
    if kind == "lol" and _has(r"FINALS?\b|PLAYOFF", name):
        return "A"
    # A followed team's ordinary CS2 match ranks with a regular-season LoL match:
    # between those two, the sooner one wins.
    return "C"


# Golf -------------------------------------------------------------------------


def golf_in_play(event):
    """A PGA event is live only while someone is on the course (thru 1-17).

    ESPN marks the whole tournament week in progress, overnight included,
    so between rounds the leaderboard is a result, not a live game.
    """
    rows = (event or {}).get("leaderboard") if isinstance(event, Mapping) else None
    for row in rows if isinstance(rows, Sequence) and not isinstance(rows, (str, bytes)) else ():
        thru = str((row or {}).get("thru") or "").strip()
        if thru.isdigit() and 0 < int(thru) < 18:
            return True
    return False
