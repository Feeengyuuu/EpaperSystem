"""Conservative HLTV event identity lookup, independent of score freshness."""

from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from functools import lru_cache
import json
import logging
from pathlib import Path
import re
import time
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from runtime.refresh_contracts import TaskContext
from utils.http_client import HttpClient
from .cs2_cards import normalized_name, safe_logo_url, utc_datetime


logger = logging.getLogger(__name__)
CATALOG_TTL = timedelta(hours=6)
RETRY_DELAY = timedelta(hours=1)
MAX_RECORDS = 64
CATALOG_VERSION = 2
LOCAL_EVENT_DIR = Path(__file__).parent / "assets" / "logos" / "cs2_events"


def _canonical(name):
    # Only strip a calendar year; edition/season numbers remain significant.
    return normalized_name(re.sub(r"\b20\d{2}\b", "", str(name or "")))


def parse_catalog(html):
    soup = BeautifulSoup(html, "html.parser")
    entries = {}
    for anchor in soup.select("a.ongoing-event, a.big-event, a.small-event"):
        link = str(anchor.get("href") or "")
        match = re.fullmatch(r"/events/(\d{1,10})/[a-zA-Z0-9-]+", link)
        title = anchor.select_one(".event-name-small .text-ellipsis, .event-col .text-ellipsis")
        if title is None:
            title = anchor.select_one(".big-event-name, .event-name-small, .small-event-name")
        if not match or title is None:
            continue
        dates = []
        for node in anchor.select("[data-unix]"):
            try:
                dates.append(datetime.fromtimestamp(int(node["data-unix"]) / 1000, timezone.utc))
            except (ValueError, TypeError, OverflowError, OSError):
                pass
        if not dates:
            continue
        name = title.get_text(" ", strip=True)
        if not name or len(name) > 180:
            continue
        entries[match[1]] = {
            "id": match[1],
            "name": name,
            "path": link,
            "start": min(dates).isoformat(),
            "end": max(dates).isoformat(),
            **_parse_logo_nodes(anchor.select("img.logo"), name),
        }
    return list(entries.values())[:200]


def match_event(entries, main):
    start = utc_datetime(main.get("start"))
    if not start:
        return None
    name = str(main.get("event_name") or "")
    league = str(main.get("league_name") or "")
    names = {_canonical(name)}
    if league and normalized_name(league) not in normalized_name(name):
        names.add(_canonical(league + " " + name))
    matches = []
    for entry in entries:
        if not isinstance(entry, Mapping) or _canonical(entry.get("name")) not in names:
            continue
        begin, end = utc_datetime(entry.get("start")), utc_datetime(entry.get("end"))
        if begin and end and begin - timedelta(days=1) <= start <= end + timedelta(days=1):
            matches.append(entry)
    return matches[0] if len(matches) == 1 else None


def parse_event_logos(html, name):
    soup = BeautifulSoup(html, "html.parser")
    return _parse_logo_nodes(soup.select("img.event-logo"), name)


def _parse_logo_nodes(nodes, name):
    logos = {}
    for node in nodes:
        if normalized_name(name) not in {normalized_name(node.get("alt")), normalized_name(node.get("title"))}:
            continue
        url = safe_logo_url(node.get("src"))
        # Keep the publisher's signed high-DPI URL intact.
        for candidate in str(node.get("srcset") or "").split(","):
            parts = candidate.split()
            if len(parts) == 2 and parts[1] == "2x" and safe_logo_url(parts[0]):
                url = safe_logo_url(parts[0])
        parsed = urlparse(url)
        if parsed.hostname != "img-cdn.hltv.org" or not parsed.path.startswith("/eventlogo/"):
            continue
        key = "event_logo_url_dark" if "night-only" in (node.get("class") or []) else "event_logo_url"
        logos.setdefault(key, url)
    return logos if logos.get("event_logo_url") else {}


@lru_cache(maxsize=1)
def _local_events():
    try:
        data = json.loads((LOCAL_EVENT_DIR / "manifest.json").read_text(encoding="utf-8"))
        return data.get("events", [])
    except (OSError, ValueError):
        return []


def local_event_branding(card):
    """Exact edition/date matches also work for already persisted cards offline."""
    main = (card or {}).get("main") or {}
    event = match_event(_local_events(), {**main, "event_name": card.get("event_name") or main.get("event_name")})
    if not event:
        return {}
    result = {}
    for key in ("event_logo_path", "event_logo_path_dark"):
        filename = str(event.get(key) or "")
        if re.fullmatch(r"[a-z0-9_-]+\.(?:png|webp)", filename):
            result[key] = str(LOCAL_EVENT_DIR / filename)
    return result


def _text(client, path, context):
    return client.request_text(
        "GET",
        "https://www.hltv.org" + path,
        context=context,
        timeout=8,
        max_bytes=1024 * 1024,
        headers={"User-Agent": "EpaperSystem/SportsDashboard"},
    ).data


def enrich_event_branding(plugin, card, settings, now, session, *, allow_network=True):
    """Reuse validated event metadata and the existing managed image disk cache."""
    if not card or not plugin._bool_setting(settings, "pandaScoreCs2HltvLogos", True):
        return card
    local = local_event_branding(card)
    if local:
        card.update(local)
        return card
    path = plugin._sports_dashboard_cache_dir() / "cs2_event_branding.json"
    cache = plugin._read_json_file(path)
    records = cache.get("records")
    records = dict(records) if isinstance(records, Mapping) else {}
    key = card["event_id"]
    record = records.get(key)
    if isinstance(record, Mapping) and record.get("name") == card["event_name"]:
        valid_until = utc_datetime(record.get("valid_until"))
        if valid_until and now < valid_until:
            card.update({k: safe_logo_url(record.get(k)) for k in ("event_logo_url", "event_logo_url_dark")})
            card["event_logo_source"] = "HLTV"
            return card
    retry = utc_datetime(cache.get("retry_until"))
    if not allow_network or (retry and now < retry):
        return card
    context = TaskContext.never_cancelled(deadline_monotonic=time.monotonic() + 12)
    client = HttpClient(session=session, max_attempts=1)
    stamp = utc_datetime(cache.get("catalog_at"))
    entries = cache.get("catalog")
    changed = False
    try:
        if (
            cache.get("catalog_version") != CATALOG_VERSION
            or not isinstance(entries, list)
            or not stamp
            or not timedelta(0) <= now - stamp < CATALOG_TTL
        ):
            entries = parse_catalog(_text(client, "/events", context))
            if not entries:
                raise ValueError("HLTV event catalog unavailable")
            cache.update(catalog=entries, catalog_at=now.isoformat(), catalog_version=CATALOG_VERSION)
            changed = True
        event = match_event(entries, card["main"])
        if event:
            # Catalog paths are revalidated before using a persisted URL.
            event_path = str(event.get("path") or "")
            if not re.fullmatch(r"/events/\d{1,10}/[a-zA-Z0-9-]+", event_path):
                raise ValueError("Invalid event path")
            logos = {
                field: safe_logo_url(event.get(field))
                for field in ("event_logo_url", "event_logo_url_dark")
            }
            if not logos["event_logo_url"]:
                logos = parse_event_logos(_text(client, event_path, context), event["name"])
            if not logos:
                raise ValueError("No matching event logo")
            valid_until = (utc_datetime(event["end"]) + timedelta(days=3)).isoformat()
            records[key] = {
                **logos,
                "name": card["event_name"],
                "hltv_id": event["id"],
                "valid_until": valid_until,
                "updated_at": now.isoformat(),
            }
            card.update(logos, event_logo_url_dark=logos.get("event_logo_url_dark", ""), event_logo_source="HLTV")
            changed = True
        # A missing match needs no retry delay: the catalog TTL already bounds
        # requests, and another event must still be able to use that catalog.
    except Exception as exc:
        cache["retry_until"] = (now + RETRY_DELAY).isoformat()
        changed = True
        logger.info("CS2 event branding unavailable; retaining provider branding: %s", type(exc).__name__)
    if changed:
        records = {
            k: v
            for k, v in records.items()
            if isinstance(v, Mapping) and (utc_datetime(v.get("valid_until")) or now) > now
        }
        cache["records"] = dict(
            sorted(records.items(), key=lambda pair: str(pair[1].get("updated_at")), reverse=True)[:MAX_RECORDS]
        )
        try:
            plugin._write_json_file(path, cache)
        except OSError:
            logger.info("CS2 event branding metadata could not be cached")
    return card
