"""What the panel actually showed, so close candidates take turns per display.

The page renders every few minutes but reaches the panel only every couple of
hours, so rotating on the wall clock makes the shown choice close to random
(and with a five-way rotation, a display period five slots long shows the same
league every time). Instead each region records the content keys it chose for
every render; the runtime passes the page's recent display times. A display at
T showed the newest render whose three regions had all finished by T, and the
keys shown at the last two displays feed event_ranking.fairness_penalty.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

# Mirrors display.display_history.RECENT_DISPLAYS_SETTING.
RECENT_DISPLAYS_SETTING = "_inkypiRecentDisplays"
REGIONS = ("football", "lower", "esports")
STATE_VERSION = "sports-display-rotation-v1"
MAX_RENDERS_PER_REGION = 48
RENDER_RETENTION = timedelta(hours=18)
SHOWN_DEPTH = 2


def _utc(value):
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def display_times(settings):
    """The page's recent display times from the runtime, newest first."""
    raw = (settings or {}).get(RECENT_DISPLAYS_SETTING)
    if not isinstance(raw, (list, tuple)):
        return []
    return sorted({stamp for stamp in map(_utc, raw) if stamp is not None}, reverse=True)


def _journal(state):
    journal = (state or {}).get("renders") if isinstance(state, dict) else None
    if not isinstance(journal, dict) or (state or {}).get("version") != STATE_VERSION:
        return {}
    clean = {}
    for region in REGIONS:
        entries = []
        for entry in journal.get(region) or []:
            if not isinstance(entry, dict):
                continue
            render, recorded = _utc(entry.get("render")), _utc(entry.get("recorded"))
            keys = entry.get("keys")
            if render is None or recorded is None or not isinstance(keys, list):
                continue
            entries.append({"render": render, "recorded": recorded, "keys": tuple(str(key) for key in keys)})
        clean[region] = entries
    return clean


def shown_at_displays(state, displays):
    """Per display (newest first): {region: keys} of the composite on the panel."""
    journal = _journal(state)
    composites = {}
    for region, entries in journal.items():
        for entry in entries:
            composites.setdefault(entry["render"], {})[region] = entry
    complete = sorted(
        (max(entry["recorded"] for entry in parts.values()), render, parts)
        for render, parts in composites.items()
        if len(parts) == len(REGIONS)
    )
    shown = []
    for display in displays:
        finished = [item for item in complete if item[0] <= display]
        parts = finished[-1][2] if finished else {}
        shown.append({region: entry["keys"] for region, entry in parts.items()})
    return shown


def recent_shown(state, settings, region):
    """Keys this region showed at the last SHOWN_DEPTH displays, newest first."""
    displays = display_times(settings)[:SHOWN_DEPTH]
    return [parts.get(region, ()) for parts in shown_at_displays(state, displays)]


def with_render(state, region, render_at, keys, *, recorded_at=None):
    """A new state recording this region's choice for one render."""
    render = _utc(render_at)
    recorded = _utc(recorded_at or datetime.now(timezone.utc))
    journal = _journal(state)
    if region not in REGIONS or render is None or recorded is None:
        return state if isinstance(state, dict) else {}
    cutoff = recorded - RENDER_RETENTION
    document = {}
    for name in REGIONS:
        entries = [entry for entry in journal.get(name, []) if entry["recorded"] >= cutoff]
        if name == region:
            entries = [entry for entry in entries if entry["render"] != render]
            entries.append({"render": render, "recorded": recorded, "keys": tuple(str(key) for key in keys or ())})
        entries.sort(key=lambda entry: entry["recorded"])
        document[name] = [
            {"render": entry["render"].isoformat(), "recorded": entry["recorded"].isoformat(),
             "keys": list(entry["keys"])}
            for entry in entries[-MAX_RENDERS_PER_REGION:]
        ]
    return {"version": STATE_VERSION, "renders": document}


# Rotation is a nicety: an unreadable or unwritable journal only costs fairness.
_JOURNAL_ERRORS = (OSError, ValueError, TypeError, AttributeError)


class DisplayRotationMixin:
    def _display_rotation_state_path(self):
        return self._sports_dashboard_cache_dir() / "display_rotation.json"

    def _region_recent_shown(self, settings, region):
        if not display_times(settings):
            return []
        try:
            state = self._read_json_file(self._display_rotation_state_path())
        except _JOURNAL_ERRORS:
            state = {}
        return recent_shown(state, settings, region)

    def _record_region_choice(self, region, now, keys):
        """Remember this render's choice for attributing later displays."""
        try:
            path = self._display_rotation_state_path()
            try:
                state = self._read_json_file(path)
            except _JOURNAL_ERRORS:
                state = {}
            self._write_json_file(path, with_render(state, region, now, keys))
        except _JOURNAL_ERRORS as exc:
            logger.debug("Sports display rotation was not recorded: %s", exc)
