"""Today's moonrise, moonset and civil twilight for home, from USNO's one-day table.

Home is the page's own latitude/longitude when set, otherwise the weather page's (the
same lookup the calendar uses). One 1.2 KB request per day; any gap simply hides the row.
"""

from __future__ import annotations

import math
import re
from datetime import date, datetime

from utils.http_client import get_http_client

USNO_ONEDAY_URL = "https://aa.usno.navy.mil/api/rstt/oneday"
USER_AGENT = "InkyPi MoonPhase/1.0"
WEATHER_PLUGINS = ("weather", "mini_weather")
_CLOCK = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
FIELDS = ("moonrise", "moonset", "transit", "civil_begin", "civil_end", "sunrise", "sunset")


def _coordinate(value, limit):
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and -limit <= number <= limit else None


def _pair(values):
    lat = _coordinate((values or {}).get("latitude"), 90)
    lon = _coordinate((values or {}).get("longitude"), 180)
    return (round(lat, 4), round(lon, 4)) if lat is not None and lon is not None else None


def _config_dict(device_config):
    if isinstance(device_config, dict):
        return device_config
    getter = getattr(device_config, "get_config", None)
    if not callable(getter):
        return {}
    try:
        value = getter()
    except TypeError:
        return {}
    return value if isinstance(value, dict) else {}


def find_home(settings, device_config):
    """(lat, lon) of the page's own setting, else of a weather page on the device, else None."""
    own = _pair(settings)
    if own:
        return own
    playlists = ((_config_dict(device_config).get("playlist_config") or {}).get("playlists")) or []
    for playlist in playlists if isinstance(playlists, list) else []:
        for plugin in (playlist or {}).get("plugins") or []:
            if not isinstance(plugin, dict) or (plugin.get("plugin_id") or plugin.get("id")) not in WEATHER_PLUGINS:
                continue
            found = _pair(plugin.get("plugin_settings") or plugin.get("settings"))
            if found:
                return found
    return None


def utc_offset_hours(local_noon: datetime) -> float:
    offset = local_noon.utcoffset()
    return round(offset.total_seconds() / 3600.0, 2) if offset is not None else 0.0


def fetch_sky_times(home, day: date, offset_hours: float, context) -> dict:
    """USNO's table for ``day`` at ``home``; times are local clock strings (HH:MM) or absent."""
    context.raise_if_cancelled()
    response = get_http_client().request_json(
        "GET", USNO_ONEDAY_URL, context=context, timeout=10, max_bytes=64 * 1024,
        # tz is the full offset in force that day, so USNO must not add DST on top.
        params={"date": day.isoformat(), "coords": f"{home[0]:.4f},{home[1]:.4f}",
                "tz": f"{offset_hours:g}", "dst": "false"},
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    context.raise_if_cancelled()
    return parse_oneday(response.data, day)


def parse_oneday(payload, day: date) -> dict:
    data = ((payload or {}).get("properties") or {}).get("data") or {}
    moon = {row.get("phen"): row.get("time") for row in data.get("moondata") or [] if isinstance(row, dict)}
    sun = {row.get("phen"): row.get("time") for row in data.get("sundata") or [] if isinstance(row, dict)}
    values = {
        "moonrise": moon.get("Rise"), "moonset": moon.get("Set"), "transit": moon.get("Upper Transit"),
        "civil_begin": sun.get("Begin Civil Twilight"), "civil_end": sun.get("End Civil Twilight"),
        "sunrise": sun.get("Rise"), "sunset": sun.get("Set"),
    }
    result = {"date": day.isoformat()}
    for key, value in values.items():
        if isinstance(value, str) and _CLOCK.match(value.strip()):
            result[key] = value.strip()
    return result


def valid_sky_times(data) -> bool:
    if not isinstance(data, dict) or not isinstance(data.get("date"), str):
        return False
    present = [data[key] for key in FIELDS if key in data]
    return bool(present) and all(isinstance(value, str) and _CLOCK.match(value) for value in present)
