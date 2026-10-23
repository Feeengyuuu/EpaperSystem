"""Metric history behind Tech Pulse's gains and trend lines.

None of the sources publish history, so the page keeps its own snapshots: one point
per item per hour for the last two days, then one per day, for up to a week. A
point is ``{"t": <UTC ISO time>, "v": {metric: number}}``; the time is when the
source was fetched, so re-rendering a cached payload adds nothing new.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

SCHEMA = "tech-pulse-trends-v1"
BOARDS = ("hn", "skills", "models")
HISTORY_DAYS = 7
HOURLY_HOURS = 48
MAX_KEYS_PER_BOARD = 160


def parse_time(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def _points(points, now):
    """Valid points inside the history window, oldest first."""
    cutoff = now - timedelta(days=HISTORY_DAYS)
    kept = []
    for point in points if isinstance(points, list) else []:
        captured = parse_time(point.get("t")) if isinstance(point, dict) else None
        values = point.get("v") if isinstance(point, dict) else None
        if captured is None or not isinstance(values, dict) or not cutoff <= captured <= now:
            continue
        numbers = {key: value for key, value in values.items()
                   if isinstance(value, (int, float)) and not isinstance(value, bool)}
        if numbers:
            kept.append((captured, numbers))
    return sorted(kept, key=lambda item: item[0])


def record(points, values, captured_at, now):
    """Add ``values`` at ``captured_at`` (latest wins within its hour or day) and thin old points."""
    now = now.astimezone(timezone.utc)
    captured = parse_time(captured_at) or now
    numbers = {key: value for key, value in values.items()
               if isinstance(value, (int, float)) and not isinstance(value, bool)}
    merged = _points(points, now)
    if numbers and captured <= now:
        merged.append((captured, numbers))
    slots = {}
    for when, point_values in sorted(merged, key=lambda item: item[0]):
        if now - when <= timedelta(hours=HOURLY_HOURS):
            slot = when.replace(minute=0, second=0, microsecond=0)
        else:
            slot = when.date()
        slots[slot] = (when, point_values)
    return [{"t": when.isoformat(), "v": point_values}
            for when, point_values in sorted(slots.values(), key=lambda item: item[0])]


def series(points, metric, now, hours=HOURLY_HOURS):
    """The metric's values over the last ``hours``, oldest first."""
    now = now.astimezone(timezone.utc)
    start = now - timedelta(hours=hours)
    return [values[metric] for when, values in _points(points, now) if when >= start and metric in values]


def change(points, metric, now, hours=24):
    """(change, span hours) of ``metric`` from the point nearest ``hours`` ago to the latest.

    Points younger than an hour are too close to say anything, so a fresh item has no change yet.
    """
    now = now.astimezone(timezone.utc)
    usable = [(when, values[metric]) for when, values in _points(points, now) if metric in values]
    if len(usable) < 2:
        return None
    latest_time, latest = usable[-1]
    older = [(when, value) for when, value in usable if latest_time - when >= timedelta(hours=1)]
    if not older:
        return None
    target = latest_time - timedelta(hours=hours)
    when, value = min(older, key=lambda item: abs((item[0] - target).total_seconds()))
    return latest - value, (latest_time - when).total_seconds() / 3600


def prune(board, keep, now):
    """Drop keys with no point left in the window, then the stalest past the cap (``keep`` stays)."""
    now = now.astimezone(timezone.utc)
    live = {}
    for key, points in (board or {}).items():
        cleaned = record(points, {}, None, now) if points else []
        if cleaned:
            live[str(key)] = cleaned
    removable = sorted((key for key in live if key not in keep), key=lambda key: live[key][-1]["t"])
    while len(live) > MAX_KEYS_PER_BOARD and removable:
        live.pop(removable.pop(0))
    return live


def empty_state():
    return {"schema": SCHEMA, **{board: {} for board in BOARDS}}


def normalise_state(state):
    if not isinstance(state, dict) or state.get("schema") != SCHEMA:
        return empty_state()
    return {"schema": SCHEMA, **{board: dict(state.get(board) or {}) for board in BOARDS}}
