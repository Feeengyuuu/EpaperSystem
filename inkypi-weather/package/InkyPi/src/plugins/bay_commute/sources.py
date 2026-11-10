"""Small, bounded public-data adapters for Bay Area closures and predicted tides, with BART's
departures and service status, Caltrans' travel-time signs and the tide station's observed level."""

from __future__ import annotations

import csv
import io
import logging
import math
import random
import re
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from runtime.refresh_contracts import TaskCancelled
from utils.http_client import get_http_client


logger = logging.getLogger(__name__)
PACIFIC = ZoneInfo("America/Los_Angeles")
CALTRANS_URL = "https://cwwp2.dot.ca.gov/data/d4/lcs/lcsStatusD04.csv"
NOAA_URL = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
NDBC_URL = "https://www.ndbc.noaa.gov/data/latest_obs/{station}.txt"
# NDBC 46026, San Francisco: the buoy 18 nautical miles west of the Golden Gate.
BUOY_STATION = "46026"
BART_URL = "https://api.bart.gov/api/etd.aspx"
# Bay Wheels' public GBFS feeds (no key): its docks (360 KB, kept a week, only those named for a BART
# station) and their bikes now (250 KB).
BIKE_INFO_URL = "https://gbfs.baywheels.com/gbfs/en/station_information.json"
BIKE_STATUS_URL = "https://gbfs.baywheels.com/gbfs/en/station_status.json"
BIKE_FEED_BYTES = 1024 * 1024
MAX_BIKE_DOCKS = 60
# The key BART publishes for anyone to use with its API (api.bart.gov/docs/overview).
BART_PUBLIC_KEY = "MW9S-E7SL-26DU-VV8V"
# Warm Springs / South Fremont, the station nearest home; any station's four-letter code works.
DEFAULT_BART_STATION = "WARM"
BART_CODE = re.compile(r"^[A-Za-z0-9]{4}$")
BART_COLOUR = re.compile(r"^#[0-9a-fA-F]{6}$")
# The lines BART letters on its maps and signs (assets/logos/bart-<line>.png).
BART_LINES = ("orange", "red", "yellow", "blue", "green")
BART_DEPARTURES = 3
# One reading of every station's departures (orig=ALL, about 100 KB) is kept and a station drawn
# from it each time the page is made; a train this many seconds behind its schedule counts as late.
BART_NETWORK_BYTES = 384 * 1024
BART_LATE_SECONDS = 60
# BART's service advisories, elevator status and trains in service (each well under 1 KB).
BART_BSA_URL = "https://api.bart.gov/api/bsa.aspx"
BART_STATUS_BYTES = 64 * 1024
MAX_ADVISORIES = 4
NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
                "nine": 9, "ten": 10}
# Caltrans District 4's changeable message signs (about 220 KB): only the travel-time signs on the
# page's routes are kept, nearest home first.
CMS_URL = "https://cwwp2.dot.ca.gov/data/d4/cms/cmsStatusD04.json"
MAX_CMS_BYTES = 1024 * 1024
MAX_CACHED_SIGNS = 12
# Home for the signs' order: Fremont, the same point Bay Alerts and Nature Nearby use.
COMMUTE_HOME = (37.5485, -121.9886)
# A travel-time line as the sign shows it: "MILPITAS  17 MIN".
TRAVEL_LINE = re.compile(r"^(?P<to>[A-Z0-9][A-Z0-9 /.&'-]{0,18}?)\s+(?P<minutes>\d{1,3})\s*MIN$")
# The NOAA station's latest observed water level, set against its prediction.
WATER_LEVEL_BYTES = 16 * 1024
COMPASS = {"N": "北", "NNE": "北东北", "NE": "东北", "ENE": "东东北", "E": "东", "ESE": "东东南", "SE": "东南",
           "SSE": "南东南", "S": "南", "SSW": "南西南", "SW": "西南", "WSW": "西西南", "W": "西", "WNW": "西西北",
           "NW": "西北", "NNW": "北西北"}
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


def _reading(pattern, text):
    match = re.search(pattern, text, re.IGNORECASE)
    if not match:
        return None
    value = float(match.group(1))
    return value if math.isfinite(value) else None


def parse_buoy(text):
    """NDBC's latest-observation page (imperial units) as {time, wave_ft, period_s, swell_dir,
    water_f, wind_kt, wind_dir}: the significant wave height when the buoy reports one, else
    the swell's, with the matching period."""
    text = str(text or "")
    stamp = re.search(r"(\d{4})\s+GMT\s+(\d{2})/(\d{2})/(\d{2})", text)
    if not stamp:
        raise ValueError("NDBC returned no observation time")
    clock, month, day, year = stamp.groups()
    observed = datetime(2000 + int(year), int(month), int(day), int(clock[:2]), int(clock[2:]), tzinfo=timezone.utc)
    swell = re.search(r"Swell:\s*([\d.]+)\s*ft\s*Period:\s*([\d.]+)\s*sec\s*Direction:\s*([A-Z]{1,3})", text)
    wave = _reading(r"Wave Height:\s*([\d.]+)\s*ft", text)
    period = _reading(r"Dominant Wave Period:\s*([\d.]+)\s*sec", text)
    data = {"time": observed.isoformat(), "wave_ft": wave if wave is not None else (float(swell.group(1)) if swell else None),
            "period_s": period if period is not None else (float(swell.group(2)) if swell else None),
            "swell_dir": swell.group(3) if swell else "",
            "water_f": _reading(r"Water Temp:\s*([\d.]+)", text),
            "wind_kt": _reading(r"Wind:\s*[A-Z]{1,3}\s*\([^)]*\),\s*([\d.]+)\s*kt", text)}
    wind = re.search(r"Wind:\s*([A-Z]{1,3})\b", text)
    data["wind_dir"] = wind.group(1) if wind else ""
    if data["wave_ft"] is None and data["water_f"] is None:
        raise ValueError("NDBC reported neither waves nor water temperature")
    return data


def fetch_buoy(context):
    context.raise_if_cancelled()
    # The page is Latin-1 (its degree signs are not UTF-8), so it is read as bytes.
    response = get_http_client().request_bytes(
        "GET", NDBC_URL.format(station=BUOY_STATION), context=context, timeout=REQUEST_TIMEOUT,
        max_bytes=32 * 1024, headers={"User-Agent": USER_AGENT})
    context.raise_if_cancelled()
    return parse_buoy(bytes(response.data).decode("latin-1"))


def valid_buoy(data):
    if not isinstance(data, dict) or not parse_time(data.get("time")):
        return False
    numbers = [data.get(key) for key in ("wave_ft", "period_s", "water_f", "wind_kt")]
    return (all(value is None or (type(value) in (int, float) and math.isfinite(value)) for value in numbers)
            and (data.get("wave_ft") is not None or data.get("water_f") is not None))


def bart_station_setting(value):
    code = str(value or "").strip().upper()
    return code if BART_CODE.match(code) else DEFAULT_BART_STATION


def _int(value):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _bart_station(station, now):
    """One station's next trains by destination: the line's colour, when each train leaves (as clock
    times, so a reading kept for a while still says when, not how long ago it said), how far behind
    each runs, how many cars each has (None when BART leaves it out) and which line each runs on
    ("" when not one BART letters: a destination two lines serve mixes them), and the next train's
    cars and platform."""
    lines = []
    for etd in station.get("etd") or []:
        if not isinstance(etd, dict) or not str(etd.get("destination") or "").strip():
            continue
        trains, colour, direction, name = [], None, "", ""
        for estimate in etd.get("estimate") or []:
            if not isinstance(estimate, dict) or str(estimate.get("cancelflag")) == "1":
                continue
            minutes = str(estimate.get("minutes") or "").strip()
            wait = 0 if minutes.lower() == "leaving" else _int(minutes)
            if wait is None:
                continue
            runs_on = str(estimate.get("color") or "").strip().lower()
            trains.append(((now + timedelta(minutes=wait)).isoformat(), max(0, _int(estimate.get("delay")) or 0),
                           _int(estimate.get("length")), str(estimate.get("platform") or "").strip()[:2],
                           runs_on if runs_on in BART_LINES else ""))
            colour = colour or (estimate.get("hexcolor") if BART_COLOUR.match(str(estimate.get("hexcolor") or "")) else None)
            direction = direction or str(estimate.get("direction") or "")
            name = name or runs_on
        if trains:
            # In time order only: a train without a length never compares against one with.
            trains = sorted(trains, key=lambda train: train[0])[:BART_DEPARTURES]
            lines.append({"destination": str(etd["destination"]).strip()[:40], "colour": colour or "#8c96a0",
                          "line": name if name in BART_LINES else "", "direction": direction[:10],
                          "departures": [train[0] for train in trains], "delays": [train[1] for train in trains],
                          "lengths": [train[2] for train in trains], "train_lines": [train[4] for train in trains],
                          "cars": trains[0][2], "platform": trains[0][3]})
    return {"station": str(station["name"])[:40], "code": str(station.get("abbr") or "")[:4],
            "time": now.isoformat(), "lines": lines}


def parse_bart(payload, now):
    """The first station of a BART departures reading (see _bart_station)."""
    stations = (((payload or {}).get("root") or {}).get("station")) or []
    station = stations[0] if isinstance(stations, list) and stations and isinstance(stations[0], dict) else {}
    if not station.get("name"):
        raise ValueError("BART returned no station")
    return _bart_station(station, now)


def parse_bart_network(payload, now):
    """Every station's departures from one orig=ALL reading."""
    stations = (((payload or {}).get("root") or {}).get("station")) or []
    parsed = [_bart_station(station, now) for station in stations if isinstance(station, dict) and station.get("name")]
    if not parsed:
        raise ValueError("BART returned no stations")
    return {"time": now.isoformat(), "stations": parsed}


def pick_bart_station(network, code=None, choose=None):
    """The station named by ``code`` from a network reading, else one drawn at random (by ``choose``,
    random.choice) among those with trains to come; None when there is none."""
    stations = [station for station in (network or {}).get("stations") or [] if station.get("lines")]
    if code:
        named = [station for station in (network or {}).get("stations") or [] if station.get("code") == code]
        if named:
            return named[0]
    return (choose or random.choice)(stations) if stations else None


def fetch_bart_network(now, context):
    context.raise_if_cancelled()
    response = get_http_client().request_json(
        "GET", BART_URL, context=context, timeout=REQUEST_TIMEOUT, max_bytes=BART_NETWORK_BYTES,
        params={"cmd": "etd", "orig": "ALL", "key": BART_PUBLIC_KEY, "json": "y"},
        headers={"User-Agent": USER_AGENT})
    context.raise_if_cancelled()
    return parse_bart_network(response.data, now)


def valid_bart(data):
    return (isinstance(data, dict) and bool(data.get("station")) and isinstance(data.get("lines"), list)
            and all(isinstance(line, dict) and line.get("destination") and isinstance(line.get("departures"), list)
                    and all(parse_time(stamp) for stamp in line["departures"]) for line in data["lines"]))


def valid_bart_network(data):
    stations = (data or {}).get("stations") if isinstance(data, dict) else None
    return isinstance(stations, list) and 0 < len(stations) <= 80 and all(valid_bart(station) for station in stations)


def _cdata(value):
    if isinstance(value, dict):
        value = value.get("#cdata-section")
    return " ".join(str(value or "").split())


def _bsa_rows(payload):
    root = (payload or {}).get("root") if isinstance(payload, dict) else None
    rows = (root or {}).get("bsa") if isinstance(root, dict) else None
    rows = [rows] if isinstance(rows, dict) else rows
    if not isinstance(rows, list):
        raise ValueError("BART returned no advisory list")
    return [row for row in rows if isinstance(row, dict)]


def parse_bart_advisories(payload):
    """BART's current service advisories ({type, station, text, sms}); none when it reports no delays."""
    items = []
    for row in _bsa_rows(payload):
        text = _cdata(row.get("description"))
        if not text or re.search(r"\bno (?:delays reported|advisories issued)\b", text, re.IGNORECASE):
            continue
        items.append({"type": str(row.get("type") or "").strip().upper()[:20],
                      "station": str(row.get("station") or "").strip().upper()[:8],
                      "text": text[:400], "sms": _cdata(row.get("sms_text"))[:240]})
    return items[:MAX_ADVISORIES]


def parse_bart_elevators(payload):
    """{out, stations} from BART's elevator notice: how many elevators are out of service and at
    which stations (0 when all are in service, None when the notice cannot be read)."""
    text = " ".join(_cdata(row.get("description")) for row in _bsa_rows(payload)).strip()
    if not text:
        raise ValueError("BART returned no elevator notice")
    if re.search(r"\ball elevators are in service\b", text, re.IGNORECASE):
        return {"out": 0, "stations": []}
    count = re.search(r"\b(\d+|" + "|".join(NUMBER_WORDS) + r")\s+elevators?\b", text, re.IGNORECASE)
    clause = re.search(r"out of service[^:]*:\s*(.+?)(?:\.\s*Thank|\.?\s*$)", text, re.IGNORECASE)
    stations = []
    if clause:
        for part in re.split(r",|\band\b", clause.group(1)):
            name = re.sub(r"\s*elevators?\b.*$", "", part.strip(" ."), flags=re.IGNORECASE).strip()
            if name and name not in stations:
                stations.append(name[:40])
    out = count.group(1).lower() if count else None
    out = NUMBER_WORDS.get(out) if out in NUMBER_WORDS else _int(out) if out else (len(stations) or None)
    return {"out": out, "stations": stations[:8]}


def parse_bart_count(payload):
    root = (payload or {}).get("root") if isinstance(payload, dict) else None
    count = _int((root or {}).get("traincount")) if isinstance(root, dict) else None
    if count is None or not 0 <= count <= 300:
        raise ValueError("BART returned no train count")
    return count


def fetch_bart_status(now, context):
    """BART's advisories, with its elevator notice and trains in service when they answer (each
    request well under 1 KB)."""
    def read(command):
        context.raise_if_cancelled()
        response = get_http_client().request_json(
            "GET", BART_BSA_URL, context=context, timeout=REQUEST_TIMEOUT, max_bytes=BART_STATUS_BYTES,
            params={"cmd": command, "key": BART_PUBLIC_KEY, "json": "y"}, headers={"User-Agent": USER_AGENT})
        context.raise_if_cancelled()
        return response.data

    status = {"time": local_now(now).isoformat(), "advisories": parse_bart_advisories(read("bsa")),
              "elevators": None, "trains": None}
    for key, command, parse in (("elevators", "elev", parse_bart_elevators), ("trains", "count", parse_bart_count)):
        try:
            status[key] = parse(read(command))
        except TaskCancelled:
            raise
        except Exception as exc:
            status[key] = None
            logger.info("BART %s unavailable (%s)", command, type(exc).__name__)
    return status


def valid_bart_status(data):
    if not isinstance(data, dict) or not parse_time(data.get("time")):
        return False
    advisories, elevators, trains = data.get("advisories"), data.get("elevators"), data.get("trains")
    return (isinstance(advisories, list) and len(advisories) <= MAX_ADVISORIES
            and all(isinstance(item, dict) and item.get("text") for item in advisories)
            and (elevators is None or (isinstance(elevators, dict) and isinstance(elevators.get("stations"), list)))
            and (trains is None or (type(trains) is int and 0 <= trains <= 300)))


def _sign_lines(message, phase):
    """A sign page's travel times as [{to, minutes}], or None when it says anything else."""
    page = message.get(phase) if isinstance(message, dict) else None
    if not isinstance(page, dict):
        return None
    lines = [" ".join(str(page.get(f"{phase}Line{index}") or "").split()).upper() for index in (1, 2, 3)]
    lines = [line for line in lines if line]
    matched = [TRAVEL_LINE.match(line) for line in lines]
    if not lines or not all(matched):
        return None
    return [{"to": match.group("to").strip()[:16], "minutes": int(match.group("minutes"))} for match in matched]


def _local_stamp(date, clock):
    stamp = parse_time(f"{date or ''}T{clock or ''}")
    return stamp.astimezone(PACIFIC) if stamp else None


def parse_signs(payload, routes, home=COMMUTE_HOME):
    """District 4's in-service travel-time signs on ``routes``, nearest ``home`` first:
    {signs: [{id, route, direction, place, lat, lon, distance_km, lines, time}], source_updated_at}."""
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or len(rows) > 2000:
        raise ValueError("Caltrans returned no sign list")
    wanted, signs, stamps = set(routes), [], []
    for entry in rows:
        sign = (entry or {}).get("cms") if isinstance(entry, dict) else None
        if not isinstance(sign, dict):
            continue
        location, message = sign.get("location") or {}, sign.get("message") or {}
        shown = message.get("messageTimestamp") or {}
        stamp = _local_stamp(shown.get("messageDate"), shown.get("messageTime"))
        # The feed's own record time ran an hour ahead after the change from daylight time, so
        # its age is read from the newest message any sign shows (travel times change minutes apart).
        if stamp and str(sign.get("inService")).lower() == "true":
            stamps.append(stamp)
        if str(sign.get("inService")).lower() != "true" or location.get("route") not in wanted:
            continue
        lines = _sign_lines(message, "phase1") or _sign_lines(message, "phase2")
        try:
            lat, lon = float(location.get("latitude")), float(location.get("longitude"))
        except (TypeError, ValueError):
            continue
        if not lines or not valid_coordinates(lat, lon):
            continue
        signs.append({"id": _text(sign.get("index"), 12), "route": _text(location.get("route"), 10),
                      "direction": _text(location.get("direction"), 10), "place": _text(location.get("nearbyPlace"), 40),
                      "lat": round(lat, 5), "lon": round(lon, 5),
                      "distance_km": round(_distance_km(home, (lat, lon)), 1), "lines": lines,
                      "time": stamp.isoformat() if stamp else None})
    if rows and not stamps:
        raise ValueError("Caltrans sign timestamps are unavailable")
    signs.sort(key=lambda sign: (sign["distance_km"], sign["id"]))
    return {"signs": signs[:MAX_CACHED_SIGNS], "routes": list(routes),
            "source_updated_at": max(stamps).isoformat() if stamps else None}


def _distance_km(first, second):
    lat1, lon1, lat2, lon2 = map(math.radians, (*first, *second))
    a = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371.0 * 2 * math.asin(min(1.0, math.sqrt(a)))


def fetch_signs(routes, context):
    context.raise_if_cancelled()
    response = get_http_client().request_json(
        "GET", CMS_URL, context=context, timeout=REQUEST_TIMEOUT, max_bytes=MAX_CMS_BYTES,
        headers={"User-Agent": USER_AGENT})
    context.raise_if_cancelled()
    return parse_signs(response.data, routes)


def valid_signs(data):
    signs = data.get("signs") if isinstance(data, dict) else None
    return (isinstance(signs, list) and len(signs) <= MAX_CACHED_SIGNS
            and all(isinstance(sign, dict) and sign.get("route") and isinstance(sign.get("lines"), list)
                    and 1 <= len(sign["lines"]) <= 3
                    and all(isinstance(line, dict) and line.get("to") and type(line.get("minutes")) is int
                            for line in sign["lines"]) for sign in signs))


def pick_sign(data, now, choose=None, nearest=4, max_age_minutes=45):
    """One of the ``nearest`` travel-time signs, drawn at random (by ``choose``) each time the page
    is made; None without any, or when Caltrans' reading is older than ``max_age_minutes`` (its
    travel times would no longer hold)."""
    recorded = parse_time((data or {}).get("source_updated_at"))
    if recorded is None or not -300 <= (local_now(now) - recorded).total_seconds() <= max_age_minutes * 60:
        return None
    signs = [sign for sign in (data or {}).get("signs") or [] if sign.get("lines")][:nearest]
    return (choose or random.choice)(signs) if signs else None


def parse_water_level(payload):
    """CO-OPS's latest observed water level as {time, height, quality}."""
    rows = payload.get("data") if isinstance(payload, dict) else None
    row = rows[0] if isinstance(rows, list) and rows and isinstance(rows[0], dict) else None
    if row is None:
        raise ValueError("NOAA returned no water level")
    try:
        stamp = datetime.strptime(str(row.get("t")), "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
        height = float(row.get("v"))
    except (TypeError, ValueError):
        raise ValueError("NOAA water level is invalid") from None
    if not math.isfinite(height) or not -30 <= height <= 60:
        raise ValueError("NOAA water level is out of range")
    return {"time": stamp.isoformat(), "height": round(height, 3), "quality": str(row.get("q") or "")[:2]}


def fetch_water_level(station, context):
    context.raise_if_cancelled()
    response = get_http_client().request_json(
        "GET", NOAA_URL, context=context, timeout=REQUEST_TIMEOUT, max_bytes=WATER_LEVEL_BYTES,
        params={"product": "water_level", "application": "InkyPi_BayCommute", "station": station,
                "date": "latest", "datum": "MLLW", "time_zone": "gmt", "units": "english", "format": "json"},
        headers={"User-Agent": USER_AGENT})
    context.raise_if_cancelled()
    return parse_water_level(response.data)


def valid_water_level(data):
    return (isinstance(data, dict) and parse_time(data.get("time")) is not None
            and type(data.get("height")) in (int, float) and math.isfinite(data["height"]))


def predicted_height(curve, stamp):
    """The predicted tide at ``stamp`` by linear interpolation of the 15-minute curve; None outside it."""
    points = [(parse_time(row.get("time")), row.get("height")) for row in curve or []]
    points = [(time_, height) for time_, height in points if time_ and type(height) in (int, float)]
    for (start, low), (end, high) in zip(points, points[1:]):
        if start <= stamp <= end:
            share = (stamp - start).total_seconds() / max(1.0, (end - start).total_seconds())
            return low + (high - low) * share
    return None


def water_versus_prediction(water, tides, now, max_age_minutes=45):
    """{height, delta, time} for an observation no older than ``max_age_minutes``: how far the water
    stands above (+) or below (-) the prediction for that moment; None without either."""
    stamp = parse_time((water or {}).get("time"))
    if stamp is None or not valid_water_level(water):
        return None
    age = (local_now(now) - stamp).total_seconds()
    if not -300 <= age <= max_age_minutes * 60:
        return None
    predicted = predicted_height((tides or {}).get("curve"), stamp)
    if predicted is None:
        return None
    return {"height": water["height"], "delta": round(water["height"] - predicted, 2), "time": stamp.isoformat()}


def coming_trains(data, now):
    """The lines whose next train has not left yet, soonest first, each with only its trains to come.
    When the reading keeps each train's length and line, ``cars`` and ``next_line`` are the next
    train still to come's (an older reading without them keeps its first train's cars, and no
    ``next_line``)."""
    now = local_now(now)
    lines = []
    for line in (data or {}).get("lines") or []:
        departures = [parse_time(stamp) for stamp in line.get("departures") or []]
        delays = list(line.get("delays") or []) + [0] * len(departures)
        upcoming = [index for index, stamp in enumerate(departures)
                    if stamp and (stamp - now).total_seconds() >= -30]
        if upcoming:
            coming = {**line, "departures": [departures[index] for index in upcoming],
                      "delays": [delays[index] for index in upcoming]}
            # Each train's own length and line, kept with it as the trains ahead leave.
            for key in ("lengths", "train_lines"):
                if isinstance(line.get(key), list):
                    values = list(line[key]) + [None] * len(departures)
                    coming[key] = [values[index] for index in upcoming]
            if "lengths" in coming:
                coming["cars"] = coming["lengths"][0]
            if "train_lines" in coming:
                coming["next_line"] = coming["train_lines"][0] or ""
            lines.append(coming)
    return sorted(lines, key=lambda line: line["departures"][0])


def next_tides(data, now):
    now = local_now(now)
    future = sorted((row for row in data.get("extremes", [])
                     if parse_time(row.get("time")) and parse_time(row["time"]) > now),
                    key=lambda row: parse_time(row["time"]))
    return {kind: next((row for row in future if row.get("type") == kind), None) for kind in ("H", "L")}


def _bike_words(name):
    """A station name's words for matching a dock to its BART station: lower case, "street" as "st",
    without BART's qualifiers ("(SF)", "/UN Plaza") or the dock's ("BART", "North", "2")."""
    name = re.split(r"[/(]", str(name or ""))[0].lower().replace("street", "st")
    words = re.findall(r"[a-z0-9]+", name)
    while words and words[-1] in {"bart", "north", "south", "east", "west", "station", "2", "3"}:
        words.pop()
    return [word for word in words if word != "bart"]


def parse_bike_stations(payload):
    """Bay Wheels' docks named for a BART station: {stations: [{id, name, words}]}."""
    rows = (((payload or {}).get("data") or {}).get("stations") or []) if isinstance(payload, dict) else []
    stations = []
    for row in rows:
        name = " ".join(str((row or {}).get("name") or "").split())[:60]
        if not isinstance(row, dict) or "BART" not in name.upper() or not row.get("station_id"):
            continue
        stations.append({"id": str(row["station_id"])[:64], "name": name, "words": _bike_words(name)})
    return {"stations": stations[:MAX_BIKE_DOCKS]}


def valid_bike_stations(data):
    rows = (data or {}).get("stations") if isinstance(data, dict) else None
    return isinstance(rows, list) and 0 < len(rows) <= MAX_BIKE_DOCKS and all(
        isinstance(row, dict) and row.get("id") and row.get("name") and isinstance(row.get("words"), list)
        for row in rows)


def parse_bike_status(payload, ids):
    """The bikes at the docks ``ids`` now: {stations: {id: {bikes, ebikes, free, renting}}}, the
    classic bikes apart from the e-bikes."""
    rows = (((payload or {}).get("data") or {}).get("stations") or []) if isinstance(payload, dict) else []
    found = {}
    for row in rows:
        if not isinstance(row, dict) or str(row.get("station_id")) not in ids:
            continue
        total, ebikes = _int(row.get("num_bikes_available")) or 0, _int(row.get("num_ebikes_available")) or 0
        found[str(row["station_id"])] = {"bikes": max(0, total - ebikes), "ebikes": ebikes,
                                         "free": _int(row.get("num_docks_available")) or 0,
                                         "renting": bool(row.get("is_renting", 1))}
    return {"stations": found}


def valid_bike_status(data):
    rows = (data or {}).get("stations") if isinstance(data, dict) else None
    return isinstance(rows, dict) and len(rows) <= MAX_BIKE_DOCKS and all(
        isinstance(row, dict) and all(isinstance(row.get(key), int) for key in ("bikes", "ebikes", "free"))
        for row in rows.values())


def bikes_at(stations, status, bart_name):
    """The bikes at the Bay Wheels docks named for the BART station ``bart_name`` (its docks added
    up; a dock not renting counts none): {name, docks_count, bikes, ebikes, free}, else None."""
    words = _bike_words(bart_name)
    if not words:
        return None
    live = (status or {}).get("stations") or {}
    docks = [row for row in (stations or {}).get("stations") or []
             if row["words"] and (row["words"] == words[:len(row["words"])] or words == row["words"][:len(words)])
             and row["id"] in live]
    if not docks:
        return None
    counts = [live[row["id"]] for row in docks]
    renting = [count for count in counts if count.get("renting", True)]
    name = re.sub(r"\s+(North|South|East|West|\d)$", "", docks[0]["name"])
    return {"name": name, "docks_count": len(docks), "bikes": sum(count["bikes"] for count in renting),
            "ebikes": sum(count["ebikes"] for count in renting), "free": sum(count["free"] for count in counts)}


def fetch_bike_stations(context):
    context.raise_if_cancelled()
    response = get_http_client().request_json(
        "GET", BIKE_INFO_URL, context=context, timeout=REQUEST_TIMEOUT, max_bytes=BIKE_FEED_BYTES,
        headers={"User-Agent": USER_AGENT})
    return parse_bike_stations(response.data)


def fetch_bike_status(ids, context):
    context.raise_if_cancelled()
    response = get_http_client().request_json(
        "GET", BIKE_STATUS_URL, context=context, timeout=REQUEST_TIMEOUT, max_bytes=BIKE_FEED_BYTES,
        headers={"User-Agent": USER_AGENT})
    return parse_bike_status(response.data, set(ids))
