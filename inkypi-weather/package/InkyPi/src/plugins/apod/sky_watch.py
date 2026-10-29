"""JPL's sky-watch feeds for the NASA Pics × Space Weather page: the near-Earth objects passing
within ten lunar distances in the next 30 days (SBDB close-approach API), and the planets above
the Bay Area tonight (Horizons). Both are keyless and small; each is cached for a few hours.

The planets are asked for at a fixed point in the middle of the Bay Area, never the home address:
a planet's height in the sky barely changes across a hundred kilometres.
"""

from __future__ import annotations

import math
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from utils.http_client import get_http_client


CAD_URL = "https://ssd-api.jpl.nasa.gov/cad.api"
HORIZONS_URL = "https://ssd.jpl.nasa.gov/api/horizons.api"
USER_AGENT = "InkyPi APOD/1.0 (personal e-paper display)"
REQUEST_TIMEOUT = (3, 10)
PACIFIC = ZoneInfo("America/Los_Angeles")
# The NEOs passing within this many lunar distances over this many days.
NEO_LUNAR_DISTANCES = 10
NEO_DAYS = 30
MAX_NEOS = 12
AU_PER_LUNAR_DISTANCE = 0.00256955529
# A diameter from an absolute magnitude when JPL has no measured one (a typical 14% albedo).
ASSUMED_ALBEDO = 0.14
SKY_SITE = "-122.10,37.55,0"
PLANETS = (("199", "水星"), ("299", "金星"), ("499", "火星"), ("599", "木星"), ("699", "土星"))
# A planet counts as up tonight above this height while the Sun is down past civil twilight.
MIN_ELEVATION = 10.0
NIGHT_FLAGS = ("", "N", "A")
COMPASS = ("北", "东北", "东", "东南", "南", "西南", "西", "西北")
_ROW = re.compile(r"^\s*(\d{4}-[A-Za-z]{3}-\d{2} \d{2}:\d{2})\s(.)(.)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+|n\.a\.)")


def _get(url, context, *, params, max_bytes):
    context.raise_if_cancelled()
    response = get_http_client().request_json("GET", url, context=context, timeout=REQUEST_TIMEOUT,
                                              max_bytes=max_bytes, params=params,
                                              headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    context.raise_if_cancelled()
    return response.data


def _float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def diameter_m(h, measured_km=None):
    """A NEO's size in metres: JPL's measured diameter, else the size its brightness implies."""
    if measured_km is not None:
        return measured_km * 1000
    if h is None:
        return None
    return 1329 / math.sqrt(ASSUMED_ALBEDO) * 10 ** (-h / 5) * 1000


# --- near-Earth objects ---------------------------------------------------------------------------

def normalize_neos(payload):
    """{"count", "objects": [{"name", "when" (UTC ISO), "lunar", "speed", "size_m"}]}, nearest first."""
    fields = (payload or {}).get("fields") or []
    rows = (payload or {}).get("data") or []
    index = {name: position for position, name in enumerate(fields)}
    if not {"des", "cd", "dist", "v_rel", "h"} <= set(index):
        raise ValueError("JPL close-approach data has unexpected fields")
    objects = []
    for row in rows:
        try:
            when = datetime.strptime(row[index["cd"]], "%Y-%b-%d %H:%M").replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            continue
        dist = _float(row[index["dist"]])
        if dist is None:
            continue
        measured = _float(row[index["diameter"]]) if "diameter" in index else None
        objects.append({"name": str(row[index["des"]]).strip(), "when": when.isoformat(),
                        "lunar": dist / AU_PER_LUNAR_DISTANCE, "speed": _float(row[index["v_rel"]]),
                        "size_m": diameter_m(_float(row[index["h"]]), measured)})
    objects.sort(key=lambda neo: neo["lunar"])
    return {"count": len(objects), "objects": objects[:MAX_NEOS]}


def fetch_neos(context):
    return normalize_neos(_get(CAD_URL, context, max_bytes=128 * 1024, params={
        "dist-max": f"{NEO_LUNAR_DISTANCES}LD", "date-min": "now", "date-max": f"+{NEO_DAYS}",
        "sort": "dist", "diameter": "true"}))


def valid_neos(data):
    return isinstance((data or {}).get("objects"), list) and "count" in (data or {})


# --- tonight's planets ----------------------------------------------------------------------------

def tonight(now):
    """The night the page means, as UTC start and stop: this evening 18:00 to tomorrow 06:00 Pacific
    (before 06:00, the night already under way)."""
    local = now.astimezone(PACIFIC)
    evening = local.replace(hour=18, minute=0, second=0, microsecond=0)
    if local.hour < 6:
        evening -= timedelta(days=1)
    return evening.astimezone(timezone.utc), (evening + timedelta(hours=12)).astimezone(timezone.utc)


def compass(azimuth):
    return COMPASS[round((azimuth % 360) / 45) % 8]


def parse_planet(result_text, name):
    """The planet's best hour tonight from a Horizons observer table: the highest one while the
    Sun is down and the planet above MIN_ELEVATION; None when it never is."""
    body = str(result_text or "")
    start, end = body.find("$$SOE"), body.find("$$EOE")
    best = None
    for line in body[start + 5:end].splitlines() if start >= 0 and end > start else []:
        match = _ROW.match(line)
        if not match:
            continue
        sun = match.group(2).strip()
        azimuth, elevation = float(match.group(4)), float(match.group(5))
        magnitude = _float(match.group(6))
        if sun not in NIGHT_FLAGS or elevation < MIN_ELEVATION:
            continue
        if best is None or elevation > best["elevation"]:
            when = datetime.strptime(match.group(1), "%Y-%b-%d %H:%M").replace(tzinfo=timezone.utc)
            best = {"name": name, "when": when.isoformat(), "azimuth": azimuth, "elevation": elevation,
                    "direction": compass(azimuth), "magnitude": magnitude}
    return best


def fetch_planets(now, context):
    """The planets up tonight, brightest first."""
    start, stop = tonight(now)
    planets = []
    for command, name in PLANETS:
        payload = _get(HORIZONS_URL, context, max_bytes=64 * 1024, params={
            "format": "json", "COMMAND": f"'{command}'", "OBJ_DATA": "'NO'", "MAKE_EPHEM": "'YES'",
            "EPHEM_TYPE": "'OBSERVER'", "CENTER": "'coord@399'", "COORD_TYPE": "'GEODETIC'",
            "SITE_COORD": f"'{SKY_SITE}'", "START_TIME": f"'{start:%Y-%m-%d %H:%M}'",
            "STOP_TIME": f"'{stop:%Y-%m-%d %H:%M}'", "STEP_SIZE": "'1h'", "QUANTITIES": "'4,9'"})
        best = parse_planet((payload or {}).get("result"), name)
        if best is not None:
            planets.append(best)
    planets.sort(key=lambda planet: planet["magnitude"] if planet["magnitude"] is not None else 99)
    return {"night": start.isoformat(), "planets": planets}


def valid_planets(data):
    return isinstance((data or {}).get("planets"), list)
