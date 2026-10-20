"""CS2 team logos missing from the provider, looked up once on HLTV and cached.

PandaScore has no image for many tier-2/3 teams. HLTV's search endpoint names a
team's day/night logo on its image CDN, which the renderer already accepts. Only
a unique exact-name team with a PNG logo counts (HLTV draws placeholders and some
SVGs that the image decoder cannot open). Found and missing teams are both cached,
lookups per refresh are bounded, and a failure backs off for an hour.
"""

from collections.abc import Mapping
from datetime import timedelta
import logging
import time
from urllib.parse import quote, urlparse

from runtime.refresh_contracts import TaskContext
from utils.http_client import HttpClient
from .cs2_cards import normalized_name, safe_logo_url, utc_datetime


logger = logging.getLogger(__name__)
CACHE_FILE = "cs2_team_logos.json"
FOUND_TTL = timedelta(days=14)
MISSING_TTL = timedelta(days=3)
RETRY_DELAY = timedelta(hours=1)
MAX_LOOKUPS_PER_REFRESH = 4
MAX_RECORDS = 256


def _hltv_png(url):
    url = safe_logo_url(url)
    parsed = urlparse(url)
    if parsed.hostname != "img-cdn.hltv.org" or not parsed.path.startswith("/teamlogo/"):
        return ""
    return url if parsed.path.casefold().endswith(".png") else ""


def parse_team_search(payload, name):
    """The day/night PNG logo of the one HLTV team named exactly ``name``."""
    wanted = normalized_name(name)
    teams = []
    for group in payload if isinstance(payload, list) else []:
        found = group.get("teams") if isinstance(group, Mapping) else None
        for team in found if isinstance(found, list) else []:
            if isinstance(team, Mapping) and wanted and normalized_name(team.get("name")) == wanted:
                teams.append(team)
    if len(teams) != 1:
        return {}
    day = _hltv_png(teams[0].get("teamLogoDay"))
    if not day:
        return {}
    return {"logo_url": day, "logo_url_dark": _hltv_png(teams[0].get("teamLogoNight")), "hltv_id": teams[0].get("id")}


def _search(client, term, context):
    return client.request_json(
        "GET",
        "https://www.hltv.org/search?term=" + quote(term),
        context=context,
        timeout=8,
        max_bytes=256 * 1024,
        headers={"User-Agent": "EpaperSystem/SportsDashboard"},
    ).data


def _card_events(card):
    seen, events = set(), []
    for event in [card.get("main"), *(card.get(key) or [] for key in ("live", "upcoming", "recent", "events"))]:
        for item in event if isinstance(event, list) else [event]:
            if isinstance(item, dict) and id(item) not in seen:
                seen.add(id(item))
                events.append(item)
    return events


def _apply(event, side, record):
    event[f"team_{side}_logo"] = safe_logo_url(record.get("logo_url"))
    event[f"team_{side}_logo_dark"] = safe_logo_url(record.get("logo_url_dark"))


def enrich_team_logos(plugin, card, settings, now, session, *, allow_network=True):
    """Fill empty team logos from cached or freshly looked-up HLTV records."""
    if not card or not plugin._bool_setting(settings, "pandaScoreCs2HltvLogos", True):
        return card
    path = plugin._sports_dashboard_cache_dir() / CACHE_FILE
    cache = plugin._read_json_file(path)
    records = cache.get("records")
    records = dict(records) if isinstance(records, Mapping) else {}
    retry = utc_datetime(cache.get("retry_until"))
    can_fetch = allow_network and not (retry and now < retry)
    client = context = None
    lookups = 0
    changed = False
    for event in _card_events(card):
        for side in ("a", "b"):
            name = str(event.get(f"team_{side}") or "").strip()
            key = normalized_name(name)
            if not key or key == "tbd" or safe_logo_url(event.get(f"team_{side}_logo")):
                continue
            record = records.get(key)
            valid_until = utc_datetime(record.get("valid_until")) if isinstance(record, Mapping) else None
            if valid_until and now < valid_until:
                if record.get("logo_url"):
                    _apply(event, side, record)
                continue
            if not can_fetch or lookups >= MAX_LOOKUPS_PER_REFRESH:
                continue
            if client is None:
                client = HttpClient(session=session, max_attempts=1)
                context = TaskContext.never_cancelled(deadline_monotonic=time.monotonic() + 12)
            lookups += 1
            try:
                logos = parse_team_search(_search(client, name, context), name)
            except Exception as exc:
                cache["retry_until"] = (now + RETRY_DELAY).isoformat()
                can_fetch = False
                changed = True
                logger.info("CS2 team logo lookup unavailable; keeping provider logos: %s", type(exc).__name__)
                continue
            ttl = FOUND_TTL if logos else MISSING_TTL
            records[key] = {**logos, "name": name[:80], "valid_until": (now + ttl).isoformat(), "updated_at": now.isoformat()}
            changed = True
            if logos:
                _apply(event, side, records[key])
            else:
                logger.info("CS2 team has no usable logo on HLTV either | team: %s", " ".join(name.split()))
    if changed:
        records = {
            key: value
            for key, value in records.items()
            if isinstance(value, Mapping) and (utc_datetime(value.get("valid_until")) or now) > now
        }
        cache["records"] = dict(
            sorted(records.items(), key=lambda pair: str(pair[1].get("updated_at")), reverse=True)[:MAX_RECORDS]
        )
        try:
            plugin._write_json_file(path, cache)
        except OSError:
            logger.info("CS2 team logo metadata could not be cached")
    return card
