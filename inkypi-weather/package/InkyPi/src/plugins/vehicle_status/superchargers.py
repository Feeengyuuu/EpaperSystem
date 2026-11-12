"""California Supercharger news for the Vehicle Status page, from supercharge.info's filtered,
paged endpoints (never its allSites / allChanges dumps): the newest sites opened and the sites
under construction nearest downtown San Francisco. The requests carry no place: the distance is
worked out here from a fixed point, never from the car or its owner."""

from __future__ import annotations

import json
import logging
import math
from datetime import datetime
from pathlib import Path

from runtime.long_task_executor import current_task_context
from runtime.refresh_contracts import TaskCancelled, TaskDeadlineExceeded
from utils.atomic_file import atomic_write_json
from utils.http_client import get_http_client


logger = logging.getLogger(__name__)
SERVICE_URL = "https://supercharge.info/service/supercharge"
USER_AGENT = "InkyPi VehicleStatus/1.0 (personal e-paper display)"
CALIFORNIA = {"regionId": 100, "countryId": 100, "state": "CA", "start": 0}
# The five newest changes (4.7 KB) and every site under construction in the state (39 of them,
# 21 KB of JSON, 3.5 KB on the wire), of which the nearest few are kept.
CHANGES_LENGTH, SITES_LENGTH = 5, 60
MAX_BYTES = 256 * 1024
REQUEST_TIMEOUT = (3, 8)
DOWNTOWN_SF = (37.7749, -122.4194)
KEEP = 4
CACHE_NAME = "superchargers-v1.json"
CACHE_SCHEMA = 1
CACHE_MAX_BYTES = 16 * 1024
# Sites open and break ground days apart: read twice a day, shown for a week without a new reading.
FRESH_SECONDS, USABLE_SECONDS = 12 * 3600, 7 * 86400
TEXT = {
    "zh-CN": {"title": "超充", "opened": "{date} 开放", "building": "在建 {days} 天", "stalls": "{count} 桩"},
    "en": {"title": "CHARGERS", "opened": "Opened {date}", "building": "Building {days} d", "stalls": "{count} stalls"},
}


def _whole(value, low, high):
    return value if type(value) is int and low <= value <= high else None


def _results(payload):
    rows = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise ValueError("supercharge.info returned no results")
    return [row for row in rows if isinstance(row, dict)]


def _town(name):
    """A site's town: "Castaic, CA - 31783 Castaic Rd" reads "Castaic"."""
    return " ".join(str(name or "").split(",")[0].split())[:32]


def _row(site, name):
    return {"name": _town(name), "stalls": _whole(site.get("stallCount"), 1, 999),
            "kw": _whole(site.get("powerKilowatt"), 1, 2000)}


def parse_opened(payload):
    """The changes that opened a site, newest first: [{name, date, stalls, kw}]."""
    opened = []
    for row in _results(payload):
        day = str(row.get("date") or "")[:10]
        if row.get("siteStatus") != "OPEN" or row.get("prevStatus") == "OPEN" or not _town(row.get("siteName")):
            continue
        try:
            datetime.strptime(day, "%Y-%m-%d")
        except ValueError:
            continue
        opened.append({**_row(row, row.get("siteName")), "date": day})
    return sorted(opened, key=lambda item: item["date"], reverse=True)[:KEEP]


def _km(first, second):
    lat1, lon1, lat2, lon2 = map(math.radians, (*first, *second))
    arc = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371.0 * 2 * math.asin(min(1.0, math.sqrt(arc)))


def parse_building(payload):
    """The sites under construction nearest downtown San Francisco: [{name, stalls, kw, days, km}]."""
    building = []
    for row in _results(payload):
        gps = row.get("gps") if isinstance(row.get("gps"), dict) else {}
        latitude, longitude = gps.get("latitude"), gps.get("longitude")
        if (row.get("status") != "CONSTRUCTION" or not _town(row.get("name"))
                or type(latitude) not in (int, float) or type(longitude) not in (int, float)
                or not (-90 <= latitude <= 90 and -180 <= longitude <= 180)):
            continue
        building.append((_km(DOWNTOWN_SF, (latitude, longitude)),
                         {**_row(row, row.get("name")), "days": _whole(row.get("statusDays"), 0, 9999)}))
    building.sort(key=lambda found: (found[0], found[1]["name"]))
    return [{**item, "km": round(distance)} for distance, item in building[:KEEP]]


def _fetch(path, params, parse):
    response = get_http_client().request_json(
        "GET", f"{SERVICE_URL}/{path}", params={**CALIFORNIA, **params}, timeout=REQUEST_TIMEOUT,
        max_bytes=MAX_BYTES, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    return parse(response.data)


SOURCES = {"opened": ("changes", {"length": CHANGES_LENGTH}, parse_opened),
           "building": ("sites", {"status": "CONSTRUCTION", "length": SITES_LENGTH}, parse_building)}


def _valid(entry):
    if not isinstance(entry, dict) or type(entry.get("fetched_at")) not in (int, float):
        return False
    items = entry.get("items")
    return (isinstance(items, list) and len(items) <= KEEP
            and all(isinstance(item, dict) and isinstance(item.get("name"), str) and item["name"] for item in items))


def _load(directory):
    path = Path(directory) / CACHE_NAME
    try:
        if path.is_symlink() or path.stat().st_size > CACHE_MAX_BYTES:
            return {}
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    if not isinstance(payload, dict) or payload.get("schema") != CACHE_SCHEMA:
        return {}
    return {name: payload[name] for name in SOURCES if _valid(payload.get(name))}


def refresh(directory, now):
    """Read whichever of the two lists is older than FRESH_SECONDS and keep it beside the page's
    cache. A failure is logged and leaves the last copy; it never fails the page."""
    kept = _load(directory)
    changed = False
    for name, (path, params, parse) in SOURCES.items():
        entry = kept.get(name)
        if entry and -300 <= now - entry["fetched_at"] < FRESH_SECONDS:
            continue
        try:
            kept[name] = {"fetched_at": float(now), "items": _fetch(path, params, parse)}
            changed = True
        except TaskDeadlineExceeded:
            # HTTP owns a short request budget. Exhausting it must not cancel the vehicle page;
            # only a stopped parent refresh does that. Keep this source's previous reading.
            parent = current_task_context()
            if parent is not None:
                parent.raise_if_cancelled()
            logger.info("Supercharger %s unavailable type=source_deadline", name)
        except TaskCancelled:
            raise
        except Exception as exc:
            logger.info("Supercharger %s unavailable type=%s", name, type(exc).__name__)
    if changed:
        try:
            Path(directory).mkdir(parents=True, exist_ok=True)
            atomic_write_json(Path(directory) / CACHE_NAME, {"schema": CACHE_SCHEMA, **kept}, mode=0o600)
        except OSError as exc:
            logger.info("Supercharger cache not written type=%s", type(exc).__name__)


def read(directory, now):
    """{opened, building} from the kept copy while it is young enough to show; None with neither."""
    kept = _load(directory)
    lists = {name: (kept[name]["items"] if name in kept and -300 <= now - kept[name]["fetched_at"] <= USABLE_SECONDS
                    else []) for name in SOURCES}
    return lists if any(lists.values()) else None


def for_display(directory, now):
    """The news on its turn (the odd hours of the day; the even ones are the vehicle strip's)."""
    return read(directory, now) if datetime.fromtimestamp(now).hour % 2 == 1 else None


def cells(news, language):
    """The strip's cells as (label, value): the two newest openings, then the two nearest sites
    under construction (with how far each is from downtown San Francisco), each under its stalls
    and power."""
    text = TEXT.get(language) or TEXT["en"]

    def detail(lead, item):
        parts = [lead] + ([text["stalls"].format(count=item["stalls"])] if item.get("stalls") else [])
        return " · ".join(parts + ([f"{item['kw']} kW"] if item.get("kw") else []))

    found = []
    for item in (news.get("opened") or [])[:2]:
        month, day = item["date"][5:7], item["date"][8:10]
        found.append((detail(text["opened"].format(date=f"{month}/{day}"), item), item["name"]))
    for item in (news.get("building") or [])[:2]:
        lead = text["building"].format(days=item["days"]) if item.get("days") is not None else text["building"].split()[0]
        found.append((detail(lead, item), f"{item['name']} · {item['km']} km"))
    return found
