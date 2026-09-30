"""Small, bounded public-data adapters for Bay Area closures and predicted tides."""

from __future__ import annotations

import csv
import io
import math
import re
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from utils.http_client import get_http_client


PACIFIC = ZoneInfo("America/Los_Angeles")
CALTRANS_URL = "https://cwwp2.dot.ca.gov/data/d4/lcs/lcsStatusD04.csv"
NOAA_URL = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
DEFAULT_ROUTES = ("I-880", "I-680", "SR-84")
DEFAULT_STATION = "9414523"
USER_AGENT = "InkyPi BayCommute/1.0"
REQUEST_TIMEOUT = (3, 8)
MAX_ROADS_BYTES = 2 * 1024 * 1024
MAX_TIDES_BYTES = 128 * 1024
MAX_ROWS = 10000
MAX_CACHED_ROADS = 500


def parse_time(value):
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return result if result.tzinfo else result.replace(tzinfo=PACIFIC)
    except (TypeError, ValueError):
        return None


def local_now(now=None):
    value = now or datetime.now(PACIFIC)
    return value.replace(tzinfo=PACIFIC) if value.tzinfo is None else value.astimezone(PACIFIC)


def routes_setting(value=None):
    if value is None or not str(value).strip():
        return DEFAULT_ROUTES
    parts = re.split(r"[,，;；\s]+", str(value).upper().strip())
    routes = []
    for part in parts:
        compact = part.replace("-", "")
        match = re.fullmatch(r"(I|SR|US)?(\d{1,3})", compact)
        if not match:
            raise ValueError("路线格式应为 I-880、I-680、SR-84")
        prefix, number = match.groups()
        number = str(int(number))
        prefix = prefix or ("I" if number in {"880", "680", "580", "80", "280"} else "SR")
        route = f"{prefix}-{number}"
        if route not in routes:
            routes.append(route)
    if not 1 <= len(routes) <= 8:
        raise ValueError("请设置 1 至 8 条路线")
    return tuple(routes)


def station_setting(value=None):
    station = str(value or DEFAULT_STATION).strip()
    if not re.fullmatch(r"\d{7}", station):
        raise ValueError("NOAA 站点编号须为 7 位数字")
    return station


def _text(value, limit=120):
    return " ".join(str(value or "").split())[:limit]


def _flag(value):
    return str(value).strip().lower() in {"true", "1"}


def valid_coordinates(latitude, longitude):
    """Accept actual numeric positions; missing coordinates are never map points."""
    return (type(latitude) in (int, float) and type(longitude) in (int, float)
            and math.isfinite(latitude) and math.isfinite(longitude)
            and -90 <= latitude <= 90 and -180 <= longitude <= 180
            and (latitude, longitude) != (0, 0))


def _coordinates(row, prefix):
    try:
        latitude = float(row.get(prefix + "Latitude") or "")
        longitude = float(row.get(prefix + "Longitude") or "")
    except (TypeError, ValueError):
        return None, None
    return (latitude, longitude) if valid_coordinates(latitude, longitude) else (None, None)


def _field_time(row, prefix):
    try:
        value = float(row.get(prefix + "Epoch") or "")
        if math.isfinite(value) and value > 0:
            return datetime.fromtimestamp(value, timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        pass
    return parse_time(f"{row.get(prefix + 'Date', '')} {row.get(prefix + 'Time', '')}")


def road_state(row, now):
    start = parse_time(row.get("start"))
    end = parse_time(row.get("end"))
    if start is None or (end is not None and end <= now):
        return "ended"
    if start <= now:
        return "active" if row.get("confirmed") is True else "scheduled_now"
    return "planned"


def rank_roads(rows, now):
    now = local_now(now)
    def priority(row):
        state = road_state(row, now)
        severity = {"Full": 0, "One-Way Traffic": 1, "Alternating Lanes": 2, "Lane": 3}.get(row.get("kind"), 4)
        start = parse_time(row.get("start"))
        return ({"active": 0, "scheduled_now": 1, "planned": 2}[state], severity,
                start.timestamp(), row.get("route", ""), row.get("id", ""))
    return sorted((row for row in rows if road_state(row, now) != "ended"), key=priority)


def select_roads(rows, now, limit=3):
    """Keep the highest-impact closure per route visible before extra rows."""
    ranked = rank_roads(rows, now)
    selected, seen = [], set()
    for row in ranked:
        if row["route"] not in seen:
            selected.append(row)
            seen.add(row["route"])
            if len(selected) >= limit:
                return selected
    selected.extend(row for row in ranked if row not in selected)
    return selected[:limit]


def parse_roads_csv(text, routes, now, *, window_days=7):
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
    required = {"index", "beginRoute", "closureStartDate", "closureStartTime",
                "closureEndDate", "closureEndTime", "typeOfClosure", "isCode1097",
                "isCode1098", "isCode1022", "recordEpoch"}
    if not required.issubset(set(reader.fieldnames or ())):
        raise ValueError("Caltrans CSV schema is invalid")
    now = local_now(now)
    rows, seen, source_times = [], set(), []
    feed_rows = 0
    for index, raw in enumerate(reader):
        feed_rows += 1
        if index >= MAX_ROWS:
            raise ValueError("Caltrans row limit exceeded")
        recorded = _field_time(raw, "record")
        if recorded:
            source_times.append(recorded)
        if raw.get("beginRoute") not in routes:
            continue
        if _flag(raw.get("isCode1098")) or _flag(raw.get("isCode1022")):
            continue
        start = _field_time(raw, "closureStart")
        end = _field_time(raw, "closureEnd")
        indefinite = _flag(raw.get("isClosureEndIndefinite"))
        # Caltrans uses 2999-12-31 23:59 / 32503708740 for an indefinite end.
        # The explicit flag controls this; that sentinel is not a promised end date.
        if indefinite:
            end = None
        if not start or (not end and not indefinite) or not raw.get("index"):
            raise ValueError("Caltrans selected closure has invalid dates or identity")
        if end is not None and end <= now:
            continue
        if start > now + timedelta(days=window_days):
            continue
        if raw["index"] in seen:
            continue
        seen.add(raw["index"])
        begin_lat, begin_lon = _coordinates(raw, "begin")
        end_lat, end_lon = _coordinates(raw, "end")
        rows.append({
            "id": _text(raw["index"]), "route": _text(raw["beginRoute"]),
            "direction": _text(raw.get("travelFlowDirection"), 40),
            "begin": _text(raw.get("beginLocationName")),
            "end_location": _text(raw.get("endLocationName")),
            "place": _text(raw.get("beginNearbyPlace")),
            "end_place": _text(raw.get("endNearbyPlace")),
            "begin_lat": begin_lat, "begin_lon": begin_lon,
            "end_lat": end_lat, "end_lon": end_lon,
            "start": start.isoformat(), "end": end.isoformat() if end else None,
            "indefinite": indefinite, "confirmed": _flag(raw.get("isCode1097")),
            "kind": _text(raw.get("typeOfClosure"), 60),
            "facility": _text(raw.get("facility"), 60),
            "work": _text(raw.get("typeOfWork"), 80),
            "lanes": _text(raw.get("lanesClosed"), 60),
        })
    if feed_rows and not source_times:
        raise ValueError("Caltrans source timestamp is unavailable")
    ordered = rank_roads(rows, now)
    return {"items": ordered[:MAX_CACHED_ROADS], "total": len(ordered),
            "source_updated_at": max(source_times).isoformat() if source_times else None,
            "routes": list(routes)}


def fetch_roads(routes, now, context, *, window_days=7):
    context.raise_if_cancelled()
    result = get_http_client().request_text(
        "GET", CALTRANS_URL, context=context, timeout=REQUEST_TIMEOUT,
        max_bytes=MAX_ROADS_BYTES, encoding="utf-8-sig",
        headers={"User-Agent": USER_AGENT},
    )
    context.raise_if_cancelled()
    return parse_roads_csv(result.data, routes, now, window_days=window_days)


def normalize_tides(payload, *, interval, begin, end):
    rows = payload.get("predictions") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or not rows or len(rows) > 600:
        raise ValueError("NOAA returned no usable predictions")
    clean, seen = [], set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("NOAA prediction row is invalid")
        try:
            stamp = datetime.fromisoformat(str(row.get("t", "")).replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            stamp = None
        try:
            height = float(row.get("v"))
        except (TypeError, ValueError):
            raise ValueError("NOAA prediction height is invalid") from None
        if not stamp or not math.isfinite(height) or not -30 <= height <= 60:
            raise ValueError("NOAA prediction is invalid")
        # Requests use GMT, so timestamp interpretation is unambiguous at DST changes.
        if not begin <= stamp < end or stamp.isoformat() in seen:
            raise ValueError("NOAA prediction dates are outside the requested range")
        seen.add(stamp.isoformat())
        item = {"time": stamp.isoformat(), "height": height}
        if interval == "hilo":
            if row.get("type") not in {"H", "L"}:
                raise ValueError("NOAA high/low type is invalid")
            item["type"] = row["type"]
        clean.append(item)
    clean.sort(key=lambda row: row["time"])
    if interval == "hilo":
        for date in (begin.astimezone(PACIFIC).date(), (end - timedelta(minutes=1)).astimezone(PACIFIC).date()):
            types = {row["type"] for row in clean if parse_time(row["time"]).astimezone(PACIFIC).date() == date}
            if types != {"H", "L"}:
                raise ValueError("NOAA high/low coverage is incomplete")
    else:
        expected = int((end.astimezone(timezone.utc) - begin.astimezone(timezone.utc)).total_seconds() / 900)
        if len(clean) != expected or parse_time(clean[0]["time"]) != begin:
            raise ValueError("NOAA curve coverage is incomplete")
        for left, right in zip(clean, clean[1:]):
            if (parse_time(right["time"]) - parse_time(left["time"])).total_seconds() != 900:
                raise ValueError("NOAA curve contains a gap")
    return clean


def fetch_tides(station, now, context):
    now = local_now(now)
    begin = datetime.combine(now.date(), time.min, PACIFIC)
    end = datetime.combine(now.date() + timedelta(days=2), time.min, PACIFIC)
    params = {
        "product": "predictions", "application": "InkyPi_BayCommute",
        "station": station, "datum": "MLLW", "time_zone": "gmt", "units": "english",
        "begin_date": begin.astimezone(timezone.utc).strftime("%Y%m%d %H:%M"),
        "end_date": (end.astimezone(timezone.utc) - timedelta(minutes=1)).strftime("%Y%m%d %H:%M"),
        "format": "json",
    }
    result = {"station": station, "prediction_date": now.date().isoformat(),
              "end_date": (now.date() + timedelta(days=1)).isoformat()}
    for name, interval in (("extremes", "hilo"), ("curve", "15")):
        context.raise_if_cancelled()
        response = get_http_client().request_json(
            "GET", NOAA_URL, context=context, timeout=REQUEST_TIMEOUT,
            max_bytes=MAX_TIDES_BYTES, params={**params, "interval": interval},
            headers={"User-Agent": USER_AGENT},
        )
        context.raise_if_cancelled()
        result[name] = normalize_tides(response.data, interval=interval, begin=begin, end=end)
    return result


def next_tides(data, now):
    now = local_now(now)
    future = sorted((row for row in data.get("extremes", [])
                     if parse_time(row.get("time")) and parse_time(row["time"]) > now),
                    key=lambda row: parse_time(row["time"]))
    return {kind: next((row for row in future if row.get("type") == kind), None) for kind in ("H", "L")}
