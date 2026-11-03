"""Keyless CS2 top-tier matches from bo3.gg, for when PandaScore has nothing fresh to show.

bo3.gg's public API lists the S-tier matches under way or to come (one request, about 90 KB with
teams, tournament and stage) and the latest S-tier results (about 30 KB). Both are turned into
PandaScore-shaped rows, so the auto-follow card's own parser (cs2_cards), event branding and team
logos serve them unchanged: the card follows the same teams, keeps one event's matches together and
credits bo3.gg as its source. While the featured match is live, its own record (about 50 KB, with
its games) adds each map's name and rounds. Every feed is cached with a short TTL, stale rows are
kept through an outage, and nothing is fetched by theme-only or cache-only renders.
"""

from collections.abc import Mapping
from datetime import timedelta
import logging
import re
import time

from runtime.refresh_contracts import TaskCancelled, TaskDeadlineExceeded
from utils.http_client import create_single_attempt_http_client
from .cs2_cards import normalized_name, parse_cs2_card, utc_datetime
from .cs2_branding import enrich_event_branding
from .cs2_team_logos import enrich_team_logos


logger = logging.getLogger(__name__)
VERSION = "cs2-bo3-v1"
MATCHES_URL = "https://api.bo3.gg/api/v1/matches"
CACHE_FILE = "cs2_bo3.json"
SOURCE = "bo3.gg"
MAX_BYTES = 1024 * 1024
DEADLINE_SECONDS = 20
# Feed -> (statuses, sort, page size); the active feed's TTL shortens while a match is near or live.
FEEDS = {"active": ("upcoming,current", "start_date", 50), "past": ("finished", "-start_date", 10)}
ACTIVE_TTL, NEAR_TTL, PAST_TTL, DETAIL_TTL = 900, 180, 1800, 120
STALE_LIMIT = timedelta(days=2)
STATUSES = {"current": "running", "upcoming": "not_started", "finished": "finished"}
# bo3.gg spells some series out where PandaScore, HLTV and the bundled event logos abbreviate them.
EVENT_NAME_FORMS = ((re.compile(r"^Intel Extreme Masters\b", re.IGNORECASE), "IEM"),)


def _mapping(value):
    return value if isinstance(value, Mapping) else {}


def _integer(value):
    try:
        return int(value) if not isinstance(value, bool) else None
    except (TypeError, ValueError, OverflowError):
        return None


def stage_label(match):
    """'Group B' from bo3's stage title ('Intel Extreme Masters Beijing 2026 Group B')."""
    title = " ".join(str(_mapping(match.get("stage")).get("title") or "").split())
    name = " ".join(str(_mapping(match.get("tournament")).get("name") or "").split())
    if name and title.casefold().startswith(name.casefold()):
        title = title[len(name):].strip(" -:|")
    return title[:60]


def event_name(value):
    """'IEM Beijing 2026' for bo3's 'Intel Extreme Masters Beijing 2026'."""
    name = " ".join(str(value or "").split())
    for pattern, replacement in EVENT_NAME_FORMS:
        name = pattern.sub(replacement, name)
    return name[:140]


def bo3_row(match, feed="", fresh=True):
    """A bo3.gg match as a PandaScore match row (team ids negated, apart from PandaScore's; logos on
    bo3's own image host), or None."""
    if not isinstance(match, Mapping):
        return None
    status = STATUSES.get(str(match.get("status") or ""))
    match_id = _integer(match.get("id"))
    tournament = _mapping(match.get("tournament"))
    teams = [_mapping(match.get("team1")), _mapping(match.get("team2"))]
    if not status or not match_id or not tournament.get("name") or not all(team.get("name") for team in teams):
        return None
    opponents, results = [], []
    for team, score in zip(teams, (match.get("team1_score"), match.get("team2_score"))):
        team_id = _integer(team.get("id"))
        if team_id is None:
            return None
        opponents.append({"opponent": {"id": -team_id, "name": str(team.get("name"))[:80], "acronym": "",
                                       "image_url": str(team.get("image_url") or "")}})
        if _integer(score) is not None:
            results.append({"team_id": -team_id, "score": _integer(score)})
    return {
        "id": f"bo3:{match_id}",
        "slug": str(match.get("slug") or "")[:120],
        "status": status,
        "begin_at": match.get("start_date"),
        "end_at": match.get("end_date") if status == "finished" else None,
        "number_of_games": match.get("bo_type"),
        "opponents": opponents,
        "results": results,
        "serie": {"full_name": event_name(tournament.get("name")), "begin_at": tournament.get("start_date"),
                  "end_at": tournament.get("last_match_date") or tournament.get("end_date"),
                  "image_url": str(tournament.get("image_url") or "")},
        "league": {"name": ""},
        "tournament": {"name": stage_label(match) or "Main Event", "tier": str(match.get("tier") or tournament.get("tier") or "")},
        "_cs2_feed": feed,
        "_cs2_feed_fresh": fresh,
    }


def map_name(value):
    """'Mirage' from 'de_mirage'."""
    name = re.sub(r"^(?:de|cs)_", "", str(value or "").strip().lower())
    return name.replace("_", " ").title()[:16] if name else ""


def parse_match_maps(payload, team_a, team_b):
    """[{"name", "team_a_score", "team_b_score"}] of a match's maps played or in play, in order."""
    games = payload.get("games") if isinstance(payload, Mapping) else None
    sides = {normalized_name(team_a): "a", normalized_name(team_b): "b"}
    maps = []
    for game in sorted((game for game in games or [] if isinstance(game, Mapping)), key=lambda game: _integer(game.get("number")) or 0):
        if str(game.get("status") or "") not in {"finished", "current"} or not map_name(game.get("map_name")):
            continue
        scores = {"a": None, "b": None}
        for prefix in ("winner", "loser"):
            side = sides.get(normalized_name(game.get(f"{prefix}_clan_name")))
            score = _integer(game.get(f"{prefix}_clan_score"))
            if side and score is not None and 0 <= score <= 99:
                scores[side] = score
        maps.append({"name": map_name(game.get("map_name")), "team_a_score": scores["a"], "team_b_score": scores["b"]})
    return maps[:5]


def _fresh(snapshot, ttl, now):
    stamp = utc_datetime(snapshot.get("fetched_at"))
    return bool(stamp and timedelta(0) <= now - stamp < timedelta(seconds=ttl))


def _rows(feeds, ttls, now):
    rows, slugs = [], {}
    for feed, snapshot in feeds.items():
        stamp = utc_datetime(snapshot.get("fetched_at"))
        if not stamp or now - stamp > STALE_LIMIT or not isinstance(snapshot.get("matches"), list):
            continue
        fresh = _fresh(snapshot, ttls[feed], now) and not snapshot.get("failed")
        for match in snapshot["matches"]:
            row = bo3_row(match, feed, fresh)
            if row:
                rows.append(row)
                slugs[row["id"]] = row["slug"]
    return rows, slugs


def _card(plugin, feeds, ttls, tz, now, settings, game_logo_path):
    rows, slugs = _rows(feeds, ttls, now)
    card = parse_cs2_card(rows, tz, now, settings, game_logo_path=game_logo_path)
    if card:
        for event in card["events"] + card["upcoming"] + [card["main"]]:
            event["source"] = SOURCE
        card.update(source=SOURCE, polling_blocked=False, polling_failure_kind="", bo3_slug=slugs.get(card["main"]["match_id"], ""))
    return card


def load_bo3_cs2_card(plugin, settings, tz, now, *, session, game_logo_path):
    """(card, source state) from bo3.gg's S-tier feeds, refreshing those past their TTL."""
    now = utc_datetime(now)
    path = plugin._sports_dashboard_cache_dir() / CACHE_FILE
    cache = plugin._read_json_file(path)
    cache = cache if cache.get("version") == VERSION else {}
    feeds = {feed: dict(value) for feed, value in _mapping(cache.get("feeds")).items() if feed in FEEDS and isinstance(value, Mapping)}
    detail = dict(_mapping(cache.get("detail")))
    ttls = {"active": ACTIVE_TTL, "past": PAST_TTL}
    previous = _card(plugin, feeds, ttls, tz, now, settings, game_logo_path)
    if previous and (previous["status"] == "LIVE" or previous["main"]["start"] <= now + timedelta(minutes=30)):
        ttls = {"active": NEAR_TTL, "past": NEAR_TTL * 2}
    allow_network = not plugin._provider_cache_only(settings)
    updated, changed = set(), False
    if allow_network:
        deadline = time.monotonic() + DEADLINE_SECONDS
        with create_single_attempt_http_client() as client:
            for feed, (statuses, sort, limit) in FEEDS.items():
                old = feeds.get(feed, {})
                if _fresh(old, ttls[feed], now) and not old.get("failed"):
                    continue
                remaining = deadline - time.monotonic()
                if remaining <= 1:
                    break
                changed = True
                try:
                    result = client.request_json("GET", MATCHES_URL, timeout=min(10, remaining), max_bytes=MAX_BYTES, params={
                        "filter[matches.status][in]": statuses, "filter[matches.tier][in]": "s",
                        "with": "teams,tournament,stage", "sort": sort, "page[limit]": limit,
                    })
                    matches = _mapping(result.data).get("results")
                    if not isinstance(matches, list):
                        raise ValueError("bo3.gg match list must be a list")
                    feeds[feed] = {"matches": [match for match in matches[:limit] if isinstance(match, Mapping)],
                                   "fetched_at": now.isoformat()}
                    updated.add(feed)
                except (TaskCancelled, TaskDeadlineExceeded):
                    raise
                except Exception as exc:
                    logger.warning("bo3.gg CS2 %s feed unavailable: %s", feed, type(exc).__name__)
                    feeds[feed] = {**old, "failed": True, "last_attempt_at": now.isoformat()}
            card = _card(plugin, feeds, ttls, tz, now, settings, game_logo_path)
            slug = (card or {}).get("bo3_slug", "")
            if card and card["status"] == "LIVE" and slug and not (detail.get("slug") == slug and _fresh(detail, DETAIL_TTL, now)):
                remaining = deadline - time.monotonic()
                if remaining > 1:
                    changed = True
                    try:
                        result = client.request_json("GET", f"{MATCHES_URL}/{slug}", timeout=min(8, remaining),
                                                     max_bytes=MAX_BYTES, params={"with": "games"})
                        detail = {"slug": slug, "fetched_at": now.isoformat(),
                                  "maps": parse_match_maps(result.data, card["main"]["team_a"], card["main"]["team_b"])}
                    except (TaskCancelled, TaskDeadlineExceeded):
                        raise
                    except Exception as exc:
                        logger.warning("bo3.gg CS2 match detail unavailable: %s", type(exc).__name__)
    card = _card(plugin, feeds, ttls, tz, now, settings, game_logo_path)
    if card and card["status"] == "LIVE" and detail.get("slug") == card.get("bo3_slug") and _fresh(detail, DETAIL_TTL * 3, now):
        card["main"]["maps"] = [item for item in detail.get("maps") or [] if isinstance(item, Mapping)]
    if changed:
        try:
            plugin._write_json_file(path, {"version": VERSION, "feeds": feeds, "detail": detail})
        except OSError as exc:
            logger.warning("bo3.gg CS2 cache write failed: %s", type(exc).__name__)
    if not card:
        return None, "BO3 NO DATA"
    main = card["main"]
    if not main["feed_fresh"]:
        state = "BO3 STALE"
    else:
        state = "BO3 " + ("LIVE" if main["feed"] in updated else "CACHE")
    card["source_state"] = state
    card = enrich_event_branding(plugin, card, settings, now, session, allow_network=allow_network)
    card = enrich_team_logos(plugin, card, settings, now, session, allow_network=allow_network)
    return card, state
