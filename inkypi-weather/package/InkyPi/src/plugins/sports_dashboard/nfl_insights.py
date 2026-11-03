"""ESPN's NFL models for the game the NFL card features, key-free and bounded.

The matchup predictor of the featured game before kick-off (sports.core.api, about 3 KB: each
side's win chance, the projected margin and the matchup quality) and the Football Power Index of
the season (one request of about 310 KB, refreshed by ESPN once a day: each team's FPI rank and its
chances of the playoffs and the title) for the two teams on the card. The predictor is cached for
three hours, the FPI for twelve; a failed request leaves the card as it was and keeps nothing
expired. The league-wide injuries feed (about 8.7 MB) is never used.
"""

from datetime import datetime, timedelta, timezone
import logging
import math
import re
import time

from runtime.refresh_contracts import TaskCancelled, TaskDeadlineExceeded
from utils.http_client import create_single_attempt_http_client

logger = logging.getLogger(__name__)
CORE_URL = "https://sports.core.api.espn.com/v2/sports/football/leagues/nfl"
PREDICTOR_URL = CORE_URL + "/events/{event_id}/competitions/{event_id}/predictor"
FPI_URL = CORE_URL + "/seasons/{season}/powerindex"
VERSION = "nfl-espn-insights-v1"
CACHE_FILE = "nfl_espn_insights.json"
PREDICTOR_TTL, FPI_TTL, RETRY_TTL = 3 * 3600, 12 * 3600, 600
MAX_BYTES = 1024 * 1024
DEADLINE_SECONDS = 12
FPI_FIELDS = {"fpirank": "rank", "probmakeplayoffs": "playoffs", "probwintitle": "title"}


def _number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _team_id(ref):
    match = re.search(r"/teams/(\d+)", str((ref or {}).get("$ref") or "") if isinstance(ref, dict) else "")
    return match.group(1) if match else ""


def parse_predictor(payload):
    """{"home": {...}, "away": {...}} with each side's "team_id", "win" (0-1) and "margin", and the
    game's "quality" (0-100); {} unless both sides give a win chance."""
    if not isinstance(payload, dict):
        return {}
    result = {}
    for key, side in (("homeTeam", "home"), ("awayTeam", "away")):
        block = payload.get(key) if isinstance(payload.get(key), dict) else {}
        stats = {str(item.get("name")): _number(item.get("value"))
                 for item in block.get("statistics") or [] if isinstance(item, dict)}
        win = stats.get("gameProjection")
        if win is None or not 0 <= win <= 100:
            return {}
        result[side] = {"team_id": _team_id(block.get("team")), "win": win / 100, "margin": stats.get("teamPredPtDiff")}
        quality = stats.get("matchupQuality")
        if quality is not None and 0 <= quality <= 100:
            result["quality"] = quality
    return result


def parse_fpi(payload):
    """{team id: {"rank": 3, "playoffs": 0.979, "title": 0.126}} from the season's power index."""
    teams = {}
    for item in (payload or {}).get("items") or [] if isinstance(payload, dict) else []:
        if not isinstance(item, dict) or not (team_id := _team_id(item.get("team"))):
            continue
        values = {}
        for stat in item.get("predictives") or []:
            field = FPI_FIELDS.get(str((stat or {}).get("name"))) if isinstance(stat, dict) else None
            number = _number(stat.get("value")) if field else None
            if field and number is not None:
                values[field] = int(number) if field == "rank" else max(0.0, min(1.0, number / 100))
        if "rank" in values:
            teams[team_id] = values
    return teams


def nfl_season(start):
    """The NFL season a game belongs to: a January or February game counts to the year before."""
    return start.year - 1 if start.month < 3 else start.year


def attach_nfl_insights(plugin, card, settings, now=None):
    """``card`` with its featured game carrying ``predictor`` (pregame) and ``fpi_a``/``fpi_b``."""
    main = (card or {}).get("main") or {}
    event_id = str(main.get("event_id") or "")
    start = main.get("start")
    if not event_id.isdigit() or not isinstance(start, datetime):
        return card
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    path = plugin._sports_dashboard_cache_dir() / CACHE_FILE
    cache = plugin._read_json_file(path)
    cache = cache if cache.get("version") == VERSION else {}
    predictors = cache.get("predictors") if isinstance(cache.get("predictors"), dict) else {}
    fpi = cache.get("fpi") if isinstance(cache.get("fpi"), dict) else {}
    season = nfl_season(start)
    scheduled = str(main.get("state") or "") == "scheduled" and start > now
    cache_only = plugin._provider_cache_only(settings)
    changed = False
    deadline = time.monotonic() + DEADLINE_SECONDS
    record = predictors.get(event_id) if isinstance(predictors.get(event_id), dict) else {}
    wants_predictor = scheduled and start - now <= timedelta(days=7)
    wants_fpi = not (fpi.get("season") == season and plugin._cache_is_fresh_seconds(fpi, FPI_TTL if fpi.get("teams") else RETRY_TTL, now))
    wants_predictor = wants_predictor and not plugin._cache_is_fresh_seconds(
        record, PREDICTOR_TTL if record.get("data") else RETRY_TTL, now)
    if not cache_only and (wants_predictor or wants_fpi):
        with create_single_attempt_http_client() as client:
            for kind, url, params, parse in (
                ("predictor", PREDICTOR_URL.format(event_id=event_id), None, parse_predictor),
                ("fpi", FPI_URL.format(season=season), {"limit": 40}, parse_fpi),
            ):
                if (kind == "predictor" and not wants_predictor) or (kind == "fpi" and not wants_fpi):
                    continue
                remaining = deadline - time.monotonic()
                if remaining <= 1:
                    break
                changed = True
                try:
                    response = client.request_json("GET", url, params=params, timeout=min(8, remaining), max_bytes=MAX_BYTES)
                    data = parse(response.data)
                except (TaskCancelled, TaskDeadlineExceeded):
                    raise
                except Exception as exc:
                    logger.warning("NFL %s unavailable: %s", kind, type(exc).__name__)
                    data = {}
                if kind == "predictor":
                    record = {"fetched_at": now.isoformat(), "data": data}
                else:
                    fpi = {"season": season, "fetched_at": now.isoformat(), "teams": data}
    if changed:
        try:
            plugin._write_json_file(path, {"version": VERSION, "predictors": {event_id: record} if record else {}, "fpi": fpi})
        except OSError:
            logger.warning("Failed to cache the NFL insights")
    extra = {}
    predictor = record.get("data") if scheduled and plugin._cache_is_fresh_seconds(record, PREDICTOR_TTL, now) else {}
    if predictor:
        sides = {"away": "a", "home": "b"}
        ids = {str(main.get("team_a_id") or ""): "a", str(main.get("team_b_id") or ""): "b"}
        oriented = {}
        for side_key, target in sides.items():
            block = predictor.get(side_key) or {}
            target = ids.get(str(block.get("team_id") or ""), target)
            oriented[target] = block
        if set(oriented) == {"a", "b"}:
            extra["predictor"] = {"win_a": oriented["a"]["win"], "win_b": oriented["b"]["win"],
                                  "margin_a": oriented["a"].get("margin"), "quality": predictor.get("quality")}
    teams = fpi.get("teams") if fpi.get("season") == season and plugin._cache_is_fresh_seconds(fpi, FPI_TTL * 2, now) else {}
    for side in ("a", "b"):
        values = (teams or {}).get(str(main.get(f"team_{side}_id") or ""))
        if values:
            extra[f"fpi_{side}"] = values
    if not extra:
        return card
    return {**card, "main": {**main, **extra}}
