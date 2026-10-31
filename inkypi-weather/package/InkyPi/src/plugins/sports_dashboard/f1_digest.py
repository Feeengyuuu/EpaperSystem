"""Finished F1 sessions summed up from OpenF1's free data, for the F1 panel.

OpenF1 serves keyless callers only historical data: a session half an hour after it ends, and
nothing at all while any session is running. So each finished session of the weekend (and the
last Grand Prix) is fetched once between sessions and kept: its top ten with their best laps
(practice, qualifying) or race times, and for a race or sprint each driver's tyres and how often
the safety car, the virtual safety car and the red flag came out.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

DIGEST_VERSION = "sports-dashboard-f1-digest-v1"
# A session becomes historical (open to keyless callers) this long after it ends.
HISTORICAL_AFTER = timedelta(minutes=30)
# An OpenF1 session matches the calendar's when it starts within this of it.
MATCH_WINDOW = timedelta(minutes=75)
# Pirelli's compounds by OpenF1's names, as their initials.
COMPOUNDS = {"SOFT": "S", "MEDIUM": "M", "HARD": "H", "INTERMEDIATE": "I", "WET": "W"}
TOP = 10


def parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


def is_race(session: Mapping[str, Any]) -> bool:
    """A Grand Prix or a sprint (OpenF1 types both "Race"), not their qualifying."""
    return str(session.get("session_type") or "").strip().lower() == "race"


def historical(session: Mapping[str, Any], now: datetime) -> bool:
    end = parse_time(session.get("date_end"))
    return end is not None and now >= end + HISTORICAL_AFTER and not session.get("is_cancelled")


def match_session(sessions: Iterable[Mapping[str, Any]], start: datetime | None) -> Mapping[str, Any] | None:
    """The OpenF1 session starting closest to ``start`` (the calendar's), within MATCH_WINDOW."""
    if not isinstance(start, datetime):
        return None
    best, best_gap = None, MATCH_WINDOW
    for session in sessions or ():
        begun = parse_time((session or {}).get("date_start"))
        if begun is None:
            continue
        gap = abs(begun - start)
        if gap <= best_gap:
            best, best_gap = session, gap
    return best


def best_lap(duration: Any) -> float | None:
    """A practice's best lap, or qualifying's best of Q1-Q3 (the last part a driver reached)."""
    if isinstance(duration, (list, tuple)):
        laps = [value for value in duration if isinstance(value, (int, float)) and value > 0]
        return float(laps[-1]) if laps else None
    return float(duration) if isinstance(duration, (int, float)) and duration > 0 else None


def control_counts(messages: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    """How many times the safety car, the virtual safety car and the red flag came out."""
    counts = {"SC": 0, "VSC": 0, "RED": 0}
    for message in messages or ():
        text = str((message or {}).get("message") or "").upper()
        category = str((message or {}).get("category") or "")
        if category == "SafetyCar" and "DEPLOYED" in text:
            counts["VSC" if "VIRTUAL" in text else "SC"] += 1
        elif category == "Flag" and str(message.get("flag") or "").upper() == "RED":
            counts["RED"] += 1
    return counts


def tyre_runs(stints: Iterable[Mapping[str, Any]], codes: Mapping[str, str]) -> dict[str, list[str]]:
    """Each driver's compounds stint by stint (initials), by three-letter code."""
    runs: dict[str, list[tuple[int, str]]] = {}
    for stint in stints or ():
        code = codes.get(str((stint or {}).get("driver_number") or ""))
        compound = COMPOUNDS.get(str(stint.get("compound") or "").upper())
        if code and compound:
            runs.setdefault(code, []).append((int(stint.get("stint_number") or 0), compound))
    return {code: [compound for _number, compound in sorted(items)] for code, items in runs.items()}


def session_digest(
    session: Mapping[str, Any],
    results: Iterable[Mapping[str, Any]],
    drivers: Iterable[Mapping[str, Any]],
    stints: Iterable[Mapping[str, Any]] | None = None,
    race_control: Iterable[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """A finished session in brief: its top ten (code, team, best lap or race time), and for a
    race its tyres and safety-car count."""
    people = {str(item.get("driver_number") or ""): item for item in drivers or () if isinstance(item, Mapping)}
    codes = {number: str(item.get("name_acronym") or "").upper() for number, item in people.items()}
    race = is_race(session)
    top = []
    for result in sorted((item for item in results or () if isinstance(item, Mapping) and item.get("position")),
                         key=lambda item: int(item.get("position") or 99))[:TOP]:
        number = str(result.get("driver_number") or "")
        top.append({
            "position": int(result.get("position")),
            "code": codes.get(number, ""),
            "team": str((people.get(number) or {}).get("team_name") or ""),
            "best": None if race else best_lap(result.get("duration")),
            "laps": result.get("number_of_laps"),
        })
    digest = {
        "session_key": session.get("session_key"),
        "name": str(session.get("session_name") or ""),
        "race": race,
        "date_end": session.get("date_end"),
        "top": top,
    }
    if race:
        digest["tyres"] = tyre_runs(stints or (), codes)
        digest["control"] = control_counts(race_control or ())
    return digest


def lap_label(seconds: float | None) -> str:
    """1:16.912 (or 59.870 under a minute)."""
    if not isinstance(seconds, (int, float)) or seconds <= 0:
        return ""
    minutes, rest = divmod(float(seconds), 60)
    return f"{int(minutes)}:{rest:06.3f}" if minutes else f"{rest:.3f}"
