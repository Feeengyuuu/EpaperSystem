"""Bounded ESPN NBA schedule discovery with independent match-day updates."""

from datetime import datetime, timedelta, timezone
import logging
import time
from zoneinfo import ZoneInfo

from utils.http_client import create_single_attempt_http_client

logger = logging.getLogger(__name__)
VERSION = "nba-calendar-v1"
EVENT_LIMIT = 1000
MONTH_TTL_SECONDS = 12 * 3600
ESPN_TIMEZONE = ZoneInfo("America/New_York")


def _stamp(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except (TypeError, ValueError):
        return None


def _event_start(event):
    competitions = event.get("competitions") or []
    return _stamp((competitions[0].get("date") if competitions else None) or event.get("date"))


def _months(start, end):
    # Provider days can cross UTC/local month boundaries. Include the adjacent
    # provider month at an edge, then filter by the actual configured timezone.
    current = (start - timedelta(days=1)).replace(day=1)
    last = (end + timedelta(days=1)).replace(day=1)
    while current <= last:
        yield current.strftime("%Y%m")
        current = (current.replace(day=28) + timedelta(days=4)).replace(day=1)


def _compact(event):
    keys = ("id", "date", "status", "season", "series", "venue", "broadcasts", "odds")
    result = {key: event[key] for key in keys if key in event}
    competition_keys = (*keys, "competitors", "notes", "type")
    result["competitions"] = [
        {key: item[key] for key in competition_keys if key in item}
        for item in event.get("competitions", [])[:1]
    ]
    competitor_keys = ("id", "uid", "type", "order", "homeAway", "score", "winner",
                       "linescores", "lineScores", "records", "record", "team")
    team_keys = ("id", "uid", "abbreviation", "shortDisplayName", "displayName",
                 "name", "location", "logo", "logos")
    for competition in result["competitions"]:
        competitors = []
        for item in competition.get("competitors", []):
            competitor = {key: item[key] for key in competitor_keys if key in item}
            team = item.get("team") or {}
            competitor["team"] = {key: team[key] for key in team_keys if key in team}
            competitors.append(competitor)
        competition["competitors"] = competitors
    return result


def load_calendar(plugin, settings, tz, now, session):
    """Return a merged, timestamped scoreboard; failed shards retain their data."""
    if getattr(session, "_inkypi_adapter_retries", False):
        # Adapter retries are invisible to our per-request budget and deadline.
        with create_single_attempt_http_client() as owned:
            return load_calendar(plugin, settings, tz, now, owned.session)
    start, end = plugin._nba_scoreboard_date_range(settings, tz, now)
    url = plugin._nba_scoreboard_url(settings)
    path = plugin._sports_dashboard_cache_dir() / "nba_calendar.json"
    cache = plugin._read_json_file(path)
    snapshots = cache.get("snapshots", {}) if cache.get("version") == VERSION and cache.get("url") == url else {}
    snapshots = dict(snapshots) if isinstance(snapshots, dict) else {}
    storage_upgrade = cache.get("compact_format") != 2
    if storage_upgrade:
        # Upgrade legacy caches in memory, keeping every fixture, score and
        # provenance timestamp. No network success is invented by this rewrite.
        for record in snapshots.values():
            if isinstance(record, dict) and isinstance(record.get("events"), list):
                record["events"] = [_compact(event) for event in record["events"]]
    force = plugin._force_refresh_requested(settings)
    cache_only = plugin._bool_setting(settings, "_inkypi_ewc_cache_only", False)
    deadline = time.monotonic() + 50
    updated = set()
    used = {}

    def snapshot(token, ttl):
        old = snapshots.get(token) or {}
        fetched = _stamp(old.get("fetched_at"))
        retry = _stamp(old.get("retry_until"))
        fresh = bool(fetched and timedelta(0) <= now - fetched < timedelta(seconds=ttl) and not retry)
        if fresh and not force:
            used[token] = (old, True)
            return old
        if cache_only or (retry and retry > now and not force):
            used[token] = (old, fresh)
            return old
        response = None
        try:
            if not force and plugin._nba_scoreboard_calls_left(settings, now) <= 0:
                raise RuntimeError("NBA request budget exhausted")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("NBA calendar request deadline reached")
            try:
                response = session.get(
                    url, params={"dates": token, "limit": str(EVENT_LIMIT)},
                    headers={"Accept": "application/json", "User-Agent": "InkyPi/1.0"},
                    timeout=min(15, remaining),
                )
            finally:
                plugin._record_nba_scoreboard_call(settings, now)
            response.raise_for_status()
            payload = response.json()
            events = payload.get("events") if isinstance(payload, dict) else None
            if not isinstance(events, list) or len(events) >= EVENT_LIMIT:
                raise ValueError("NBA scoreboard missing events or possibly truncated")
            if any(not isinstance(event, dict) or not event.get("id") or not _event_start(event) for event in events):
                raise ValueError("NBA scoreboard contains an invalid event")
            result = {"fetched_at": now.isoformat(), "events": [_compact(event) for event in events]}
            snapshots[token] = result
            updated.add(token)
            used[token] = (result, True)
            return result
        except Exception as exc:
            logger.warning("NBA scoreboard shard unavailable: dates=%s reason=%s status=%s retained=%s",
                           token, type(exc).__name__, getattr(response, "status_code", None), bool(fetched))
            snapshots[token] = {**old, "retry_until": (now + timedelta(minutes=5)).isoformat()}
            used[token] = (old, False)
            return old
        finally:
            if response is not None and hasattr(response, "close"):
                response.close()

    month_tokens = list(_months(start, end))
    provider_today = now.astimezone(ESPN_TIMEZONE).date()
    day_tokens = {provider_today.strftime("%Y%m%d")}
    known = {}
    for token, item in snapshots.items():
        fetched = _stamp(item.get("fetched_at"))
        for event in item.get("events", []) if fetched else []:
            event_start = _event_start(event)
            if not event_start or abs((event_start.astimezone(ESPN_TIMEZONE).date() - provider_today).days) > 1:
                continue
            rank = (fetched, len(token))
            key = str(event.get("id"))
            if key not in known or rank >= known[key][0]:
                known[key] = (rank, event)
    candidates = plugin._parse_nba_espn_events({"events": [item[1] for item in known.values()]}, tz)
    live_days = {
        event["start"].astimezone(ESPN_TIMEZONE).strftime("%Y%m%d")
        for event in candidates if plugin._is_nba_live_poll_candidate(event, now)
        and abs((event["start"].astimezone(ESPN_TIMEZONE).date() - provider_today).days) <= 1
    }
    day_tokens.update(live_days)
    scheduled_days = {
        event["start"].astimezone(ESPN_TIMEZONE).strftime("%Y%m%d")
        for event in candidates if not plugin._is_nba_finished_event(event)
    }
    # Scores get the remaining daily request budget before schedule maintenance.
    for token in sorted(day_tokens):
        ttl = MONTH_TTL_SECONDS
        if token in scheduled_days:
            ttl = plugin._int_setting(settings, "nbaCacheHours", 1, 1, 12) * 3600
        if token in live_days:
            ttl = plugin._nba_live_refresh_seconds(settings)
        snapshot(token, ttl)
    for token in month_tokens:
        snapshot(token, MONTH_TTL_SECONDS)

    merged = {}
    for token in month_tokens:
        record, _fresh = used[token]
        fetched = _stamp(record.get("fetched_at"))
        for event in record.get("events", []) if fetched else []:
            key = str(event["id"])
            if key not in merged or fetched >= merged[key][0]:
                merged[key] = (fetched, event)
    for token in sorted(day_tokens):
        record, fresh = used[token]
        fetched = _stamp(record.get("fetched_at"))
        if not fetched:
            continue
        if fresh:
            # Successful empty days retract cancelled/removed fixtures. An old
            # failed day must never erase a newer monthly schedule.
            merged = {key: value for key, value in merged.items()
                      if value[0] > fetched or _event_start(value[1]).astimezone(ESPN_TIMEZONE).strftime("%Y%m%d") != token}
        for event in record.get("events", []):
            key = str(event["id"])
            if key not in merged or fetched >= merged[key][0]:
                merged[key] = (fetched, event)
    events = [event for _fetched, event in merged.values()
              if start <= _event_start(event).astimezone(tz).date() <= end]
    events.sort(key=_event_start)
    successful = [record for record, _fresh in used.values() if _stamp(record.get("fetched_at"))]
    source = "ESPN LIVE" if updated else "ESPN CACHE"
    if not all(fresh for _record, fresh in used.values()):
        source = "ESPN STALE" if successful else "NBA NO DATA"
    next_cache = {"version": VERSION, "url": url, "compact_format": 2,
                  "snapshots": {token: snapshots[token] for token in used if token in snapshots}}
    if not cache_only and next_cache != cache:
        # Retain only this horizon and at most the relevant recent match days.
        try:
            plugin._write_json_file(path, next_cache)
        except OSError:
            logger.warning("NBA calendar cache could not be persisted")
    return {"scoreboard": {"events": events}, "source_state": source,
            "range_start": start.isoformat(), "range_end": end.isoformat(),
            "shards": {token: {"fetched_at": record.get("fetched_at"), "fresh": fresh}
                       for token, (record, fresh) in used.items()},
            "requests_updated": sorted(updated)}
