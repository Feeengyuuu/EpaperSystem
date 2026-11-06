"""Bounded public feeds for the Bay Alerts page: USGS quakes (with how they were felt), NWS alerts
and the forecasters' key messages, NASA EONET events, CAL FIRE's active incidents, the Air
District's Spare the Air status and, with a FIRMS map key, NASA's satellite fire hotspots."""

from __future__ import annotations

import csv
import io
import math
import re
from datetime import datetime, timedelta, timezone
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

from runtime.refresh_contracts import TaskCancelled
from utils.http_client import get_http_client


PACIFIC = ZoneInfo("America/Los_Angeles")
USER_AGENT = "InkyPi BayAlerts/1.0 (personal e-paper display)"
USGS_URL = "https://earthquake.usgs.gov/fdsnws/event/1/query"
NWS_URL = "https://api.weather.gov/alerts/active"
EONET_URL = "https://eonet.gsfc.nasa.gov/api/v3/events"
CALFIRE_URL = "https://incidents.fire.ca.gov/umbraco/api/IncidentApi/List"
FIRMS_URL = "https://firms.modaps.eosdis.nasa.gov/api/area/csv/{key}/{sensor}/{bbox}/{days}"
FIRMS_SENSORS = ("VIIRS_NOAA20_NRT", "VIIRS_SNPP_NRT")
FIRMS_BBOX = "-122.8,37.2,-121.5,38.2"  # the Bay map's extent, west,south,east,north
MAX_FIRES = 20
MAX_HOTSPOTS = 200
REQUEST_TIMEOUT = (3, 9)
DEFAULT_HOME = (37.5485, -121.9886)  # Fremont, the middle of the Bay
QUAKE_WINDOW_DAYS = 7
MAX_QUAKES = 300
MAX_ALERTS = 30
MAX_EVENTS = 40
MAX_POLYGON_POINTS = 240
MAX_ALERT_ZONES = 30
ZONE_CODE = re.compile(r"[A-Z]{2}[CZ]\d{3}")  # NWS UGC: CAZ508 (forecast zone), CAC001 (county)
# NWS geocode SAME values for the Bay Area counties (state 06 + county FIPS).
BAY_COUNTIES = {
    "006001": "Alameda", "006013": "Contra Costa", "006041": "Marin", "006055": "Napa",
    "006075": "San Francisco", "006081": "San Mateo", "006085": "Santa Clara",
    "006087": "Santa Cruz", "006095": "Solano", "006097": "Sonoma",
}
CALIFORNIA_BBOX = "-124.6,42.1,-114.0,32.4"  # EONET order: west,north,east,south
SEVERITY_RANK = {"Extreme": 4, "Severe": 3, "Moderate": 2, "Minor": 1}
EVENT_NAMES = {
    "Red Flag Warning": "红旗火险警告", "Fire Weather Watch": "火险天气监视",
    "Wind Advisory": "大风提示", "High Wind Warning": "强风警告", "High Wind Watch": "强风监视",
    "Heat Advisory": "高温提示", "Excessive Heat Warning": "酷热警告", "Excessive Heat Watch": "酷热监视",
    "Extreme Heat Warning": "极端高温警告", "Extreme Heat Watch": "极端高温监视",
    "Flood Watch": "洪水监视", "Flood Warning": "洪水警告", "Flood Advisory": "洪水提示",
    "Flash Flood Warning": "山洪警告", "Flash Flood Watch": "山洪监视",
    "Coastal Flood Advisory": "沿海洪水提示", "Coastal Flood Warning": "沿海洪水警告",
    "Beach Hazards Statement": "海滩危险声明", "Rip Current Statement": "离岸流声明",
    "High Surf Advisory": "大浪提示", "High Surf Warning": "大浪警告",
    "Dense Fog Advisory": "浓雾提示", "Dense Smoke Advisory": "浓烟提示", "Air Quality Alert": "空气质量警报",
    "Frost Advisory": "霜冻提示", "Freeze Warning": "冰冻警告", "Freeze Watch": "冰冻监视",
    "Winter Storm Warning": "冬季风暴警告", "Winter Storm Watch": "冬季风暴监视",
    "Winter Weather Advisory": "冬季天气提示", "Special Weather Statement": "特别天气声明",
    "Severe Thunderstorm Warning": "强雷暴警告", "Severe Thunderstorm Watch": "强雷暴监视",
    "Tornado Warning": "龙卷风警告", "Tsunami Warning": "海啸警告", "Tsunami Advisory": "海啸提示",
    "Tsunami Watch": "海啸监视", "Hazardous Weather Outlook": "危险天气展望",
}
COMPASS = {
    "N": "北", "NNE": "北偏东", "NE": "东北", "ENE": "东偏北", "E": "东", "ESE": "东偏南",
    "SE": "东南", "SSE": "南偏东", "S": "南", "SSW": "南偏西", "SW": "西南", "WSW": "西偏南",
    "W": "西", "WNW": "西偏北", "NW": "西北", "NNW": "北偏西",
}
EONET_KINDS = {
    "wildfires": "fire", "severeStorms": "storm", "volcanoes": "volcano", "floods": "flood",
    "earthquakes": "quake", "landslides": "landslide", "snow": "snow", "seaLakeIce": "snow",
    "dustHaze": "haze", "drought": "heat", "tempExtremes": "heat",
}
# USGS PAGER's alert levels, in Chinese, mildest first.
PAGER_LEVELS = {"green": "绿色", "yellow": "黄色", "orange": "橙色", "red": "红色"}
ROMAN = ("", "I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII")
# The San Francisco Bay Area forecast office's latest Area Forecast Discussion (about 7 KB), for
# the forecasters' key messages; it is issued a few times a day.
AFD_URL = "https://api.weather.gov/products/types/AFD/locations/MTR/latest"
MAX_KEY_MESSAGES = 5
# The Bay Area Air District's Spare the Air status (about 0.5 KB).
SPARE_THE_AIR_URL = "https://www.baaqmd.gov/Feeds/AlertRSS.aspx"
WEEKDAY_SHORT = {"Monday": "Mon", "Tuesday": "Tue", "Wednesday": "Wed", "Thursday": "Thu", "Friday": "Fri",
                 "Saturday": "Sat", "Sunday": "Sun"}


def parse_time(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def local_now(now=None):
    value = now or datetime.now(PACIFIC)
    return value.replace(tzinfo=PACIFIC) if value.tzinfo is None else value.astimezone(PACIFIC)


def _finite(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def valid_coordinates(lat, lon):
    lat, lon = _finite(lat), _finite(lon)
    return lat is not None and lon is not None and -90 <= lat <= 90 and -180 <= lon <= 180


def home_setting(latitude=None, longitude=None):
    if latitude in (None, "") and longitude in (None, ""):
        return DEFAULT_HOME
    lat, lon = _finite(latitude), _finite(longitude)
    if lat is None or lon is None or not (35.0 <= lat <= 40.0 and -125.0 <= lon <= -119.0):
        raise ValueError("家的位置须在湾区附近（纬度 35–40，经度 -125 至 -119）")
    return (round(lat, 4), round(lon, 4))


def counties_setting(value=None):
    if value is None or (isinstance(value, str) and not value.strip()):
        return tuple(BAY_COUNTIES)
    parts = value if isinstance(value, (list, tuple)) else re.split(r"[,，;\s]+", str(value))
    chosen = [part.strip() for part in parts if str(part).strip() in BAY_COUNTIES]
    if not chosen:
        raise ValueError("请至少选择一个湾区县")
    return tuple(dict.fromkeys(chosen))


def distance_km(lat1, lon1, lat2, lon2):
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi, dlmb = phi2 - phi1, math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlmb / 2) ** 2
    return 6371.0 * 2 * math.asin(min(1.0, math.sqrt(a)))


def chinese_place(place):
    """'5 km NW of San Ramon, CA' -> 'San Ramon 西北 5 公里'."""
    text = " ".join(str(place or "").split())
    text = re.sub(r",\s*(CA|California)$", "", text)
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*km\s+([NSEW]{1,3})\s+of\s+(.+)", text)
    if match and match.group(2) in COMPASS:
        return f"{match.group(3)} {COMPASS[match.group(2)]} {match.group(1)} 公里"
    return text[:60]


def event_name(event):
    return EVENT_NAMES.get(event, event)


def hazard_kind(event):
    text = str(event or "").lower()
    for kind, words in (
        ("fire", ("fire", "red flag")), ("heat", ("heat",)), ("wind", ("wind", "gale")),
        ("storm", ("thunder", "tornado")), ("snow", ("winter", "snow", "freeze", "frost", "ice")),
        ("flood", ("flood", "surf", "rip", "tsunami", "beach", "coastal")),
        ("haze", ("fog", "smoke", "air quality", "dust")),
    ):
        if any(word in text for word in words):
            return kind
    return "alert"


def _get_json(url, params, context, max_bytes):
    context.raise_if_cancelled()
    response = get_http_client().request_json(
        "GET", url, context=context, timeout=REQUEST_TIMEOUT, max_bytes=max_bytes,
        params=params, headers={"User-Agent": USER_AGENT, "Accept": "application/geo+json, application/json"},
    )
    context.raise_if_cancelled()
    return response.data


def normalize_quakes(payload, home, now):
    features = payload.get("features") if isinstance(payload, dict) else None
    if not isinstance(features, list):
        raise ValueError("USGS returned no feature list")
    items = []
    for feature in features[: MAX_QUAKES * 2]:
        props = feature.get("properties") if isinstance(feature, dict) else None
        geometry = feature.get("geometry") if isinstance(feature, dict) else None
        coords = geometry.get("coordinates") if isinstance(geometry, dict) else None
        if not isinstance(props, dict) or not isinstance(coords, list) or len(coords) < 2:
            continue
        if str(props.get("type") or "earthquake") != "earthquake":
            continue  # quarry blasts and explosions are not earthquakes
        mag, lon, lat = _finite(props.get("mag")), _finite(coords[0]), _finite(coords[1])
        millis = _finite(props.get("time"))
        if mag is None or millis is None or not valid_coordinates(lat, lon):
            continue
        stamp = datetime.fromtimestamp(millis / 1000, timezone.utc)
        if stamp > now + timedelta(minutes=5):
            continue
        depth = _finite(coords[2]) if len(coords) > 2 else None
        felt = props.get("felt")
        # How it was felt: "Did You Feel It?" reports and their highest intensity, ShakeMap's
        # intensity, the PAGER alert level, the tsunami flag and USGS's significance.
        cdi, mmi, sig = _finite(props.get("cdi")), _finite(props.get("mmi")), _finite(props.get("sig"))
        alert = str(props.get("alert") or "").lower()
        items.append({
            "id": str(feature.get("id") or props.get("code") or "")[:40],
            "mag": round(mag, 1), "time": stamp.isoformat(),
            "lat": round(lat, 4), "lon": round(lon, 4),
            "depth_km": round(depth, 1) if depth is not None else None,
            "place": chinese_place(props.get("place")),
            "felt": int(felt) if isinstance(felt, int) and felt >= 0 else None,
            "cdi": round(cdi, 1) if cdi is not None and 0 < cdi <= 12 else None,
            "mmi": round(mmi, 1) if mmi is not None and 0 < mmi <= 12 else None,
            "alert": alert if alert in PAGER_LEVELS else None,
            "tsunami": props.get("tsunami") == 1,
            "sig": int(sig) if sig is not None and 0 <= sig <= 3000 else None,
            "distance_km": round(distance_km(home[0], home[1], lat, lon), 1),
        })
        if len(items) >= MAX_QUAKES:
            break
    items.sort(key=lambda item: item["time"], reverse=True)
    return {"items": items, "home": list(home), "window_days": QUAKE_WINDOW_DAYS}


def fetch_quakes(home, radius_km, min_magnitude, now, context):
    params = {
        "format": "geojson", "orderby": "time", "limit": MAX_QUAKES,
        "starttime": (now - timedelta(days=QUAKE_WINDOW_DAYS)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
        "latitude": home[0], "longitude": home[1], "maxradiuskm": radius_km, "minmagnitude": min_magnitude,
    }
    data = normalize_quakes(_get_json(USGS_URL, params, context, 1024 * 1024), home, now)
    data["radius_km"] = radius_km
    return data


def _simplified_polygon(geometry):
    if not isinstance(geometry, dict) or geometry.get("type") != "Polygon":
        return None
    rings = geometry.get("coordinates")
    if not isinstance(rings, list) or not rings or not isinstance(rings[0], list):
        return None
    ring = [point for point in rings[0] if isinstance(point, list) and len(point) >= 2
            and valid_coordinates(point[1], point[0])]
    if len(ring) < 3:
        return None
    step = max(1, math.ceil(len(ring) / MAX_POLYGON_POINTS))
    return [[round(float(point[0]), 3), round(float(point[1]), 3)] for point in ring[::step]]


def alert_zones(props):
    """The NWS zones an alert covers (its UGC codes, else its affectedZones links), for the map."""
    codes = (props.get("geocode") or {}).get("UGC") or [
        str(link).rstrip("/").rsplit("/", 1)[-1] for link in props.get("affectedZones") or []]
    zones = [code for code in codes if isinstance(code, str) and ZONE_CODE.fullmatch(code)]
    return list(dict.fromkeys(zones))[:MAX_ALERT_ZONES]


def normalize_alerts(payload, counties, now):
    features = payload.get("features") if isinstance(payload, dict) else None
    if not isinstance(features, list):
        raise ValueError("NWS returned no feature list")
    wanted = set(counties)
    items, seen = [], set()
    for feature in features:
        props = feature.get("properties") if isinstance(feature, dict) else None
        if not isinstance(props, dict) or props.get("status") != "Actual" or props.get("messageType") == "Cancel":
            continue
        same = (props.get("geocode") or {}).get("SAME") or []
        matched = [BAY_COUNTIES[code] for code in same if code in wanted]
        if not matched:
            continue
        ends = parse_time(props.get("ends") or props.get("expires"))
        if ends is not None and ends <= now:
            continue
        event = str(props.get("event") or "").strip()[:60]
        key = (event, tuple(sorted(matched)), props.get("onset"))
        if not event or key in seen:
            continue
        seen.add(key)
        onset = parse_time(props.get("onset") or props.get("effective"))
        items.append({
            "id": str(props.get("id") or "")[-60:], "event": event, "name": event_name(event),
            "kind": hazard_kind(event), "severity": str(props.get("severity") or "Unknown")[:12],
            "urgency": str(props.get("urgency") or "")[:12],
            "headline": " ".join(str(props.get("headline") or "").split())[:180],
            "onset": onset.isoformat() if onset else None, "ends": ends.isoformat() if ends else None,
            "counties": matched[:10], "zones": alert_zones(props),
            "polygon": _simplified_polygon(feature.get("geometry")),
        })
        if len(items) >= MAX_ALERTS:
            break
    items.sort(key=lambda item: (-SEVERITY_RANK.get(item["severity"], 0), item["onset"] or ""))
    return {"items": items, "counties": list(counties)}


def fetch_alerts(counties, now, context):
    return normalize_alerts(_get_json(NWS_URL, {"area": "CA"}, context, 2 * 1024 * 1024), counties, now)


def _event_point(geometries):
    for geometry in reversed(geometries):
        if not isinstance(geometry, dict):
            continue
        coords = geometry.get("coordinates")
        if geometry.get("type") == "Point" and isinstance(coords, list) and len(coords) >= 2:
            if valid_coordinates(coords[1], coords[0]):
                return geometry, float(coords[1]), float(coords[0])
        if geometry.get("type") == "Polygon" and isinstance(coords, list) and coords and isinstance(coords[0], list):
            ring = [point for point in coords[0] if isinstance(point, list) and len(point) >= 2]
            if ring:
                lat = sum(float(point[1]) for point in ring) / len(ring)
                lon = sum(float(point[0]) for point in ring) / len(ring)
                if valid_coordinates(lat, lon):
                    return geometry, lat, lon
    return None, None, None


def _event_title(title):
    text = " ".join(str(title or "").split())
    text = re.sub(r"^(Wildfires?|Fire)\b\s*[-–:]?\s*", "", text, flags=re.I)
    text = re.sub(r",?\s*(California|CA)(,?\s*United States)?$", "", text)
    return text[:70]


def normalize_events(payload, now):
    events = payload.get("events") if isinstance(payload, dict) else None
    if not isinstance(events, list):
        raise ValueError("EONET returned no event list")
    items = []
    for event in events:
        if not isinstance(event, dict) or event.get("closed"):
            continue
        categories = [str(item.get("id")) for item in event.get("categories") or [] if isinstance(item, dict)]
        geometry, lat, lon = _event_point(event.get("geometry") or [])
        stamp = parse_time(geometry.get("date")) if geometry else None
        if geometry is None or stamp is None or not (32.3 <= lat <= 42.2 and -124.8 <= lon <= -113.9):
            continue
        magnitude = _finite(geometry.get("magnitudeValue"))
        kind = next((EONET_KINDS[item] for item in categories if item in EONET_KINDS), "alert")
        items.append({
            "id": str(event.get("id") or "")[:40], "title": _event_title(event.get("title")),
            "kind": kind, "categories": categories[:4], "date": stamp.isoformat(),
            "lat": round(lat, 3), "lon": round(lon, 3),
            "magnitude": round(magnitude, 1) if magnitude is not None else None,
            "unit": str(geometry.get("magnitudeUnit") or "")[:12],
        })
    items.sort(key=lambda item: item["date"], reverse=True)
    return {"items": items[:MAX_EVENTS]}


def fetch_events(now, context):
    params = {"status": "open", "bbox": CALIFORNIA_BBOX, "days": 30}
    return normalize_events(_get_json(EONET_URL, params, context, 1024 * 1024), now)


def fire_name(name):
    """'Cooley Fire' -> 'Cooley 山火'."""
    text = " ".join(str(name or "").split())[:60]
    return re.sub(r"\s+Fire$", " 山火", text, flags=re.I)


def normalize_fires(rows, home):
    """CAL FIRE's active incidents in California as page events: newest first, each with its acres
    and containment."""
    if not isinstance(rows, list):
        raise ValueError("CAL FIRE returned no incident list")
    items = []
    for row in rows:
        if not isinstance(row, dict) or not row.get("Name") or row.get("IsActive") is False or row.get("Final"):
            continue
        lat, lon = _finite(row.get("Latitude")), _finite(row.get("Longitude"))
        stamp = parse_time(row.get("Started"))
        if stamp is None or not valid_coordinates(lat, lon) or not (32.3 <= lat <= 42.2 and -124.8 <= lon <= -113.9):
            continue
        acres, contained = _finite(row.get("AcresBurned")), _finite(row.get("PercentContained"))
        items.append({
            "id": "calfire-" + str(row.get("UniqueId") or row["Name"])[:40], "title": fire_name(row["Name"]),
            "kind": "fire", "categories": ["calfire"], "date": stamp.isoformat(),
            "lat": round(lat, 3), "lon": round(lon, 3),
            "magnitude": round(acres, 1) if acres is not None else None, "unit": "acres" if acres is not None else "",
            "contained": round(contained) if contained is not None else None,
            "county": str(row.get("County") or "")[:30],
            "distance_km": round(distance_km(home[0], home[1], lat, lon), 1),
        })
    items.sort(key=lambda item: item["date"], reverse=True)
    return {"items": items[:MAX_FIRES]}


def fetch_fires(home, context):
    return normalize_fires(_get_json(CALFIRE_URL, {"inactive": "false"}, context, 512 * 1024), home)


def normalize_hotspots(text, now):
    """FIRMS' VIIRS detections as [{lat, lon, time, frp}], low-confidence ones left out."""
    rows = list(csv.DictReader(io.StringIO(str(text or ""))))
    if rows and "latitude" not in rows[0]:
        raise ValueError("FIRMS returned no detections table")
    items = []
    for row in rows:
        lat, lon = _finite(row.get("latitude")), _finite(row.get("longitude"))
        if not valid_coordinates(lat, lon) or str(row.get("confidence") or "").lower() in ("l", "low"):
            continue
        clock = str(row.get("acq_time") or "").zfill(4)
        stamp = parse_time(f"{row.get('acq_date')}T{clock[:2]}:{clock[2:]}:00+00:00")
        if stamp is None or stamp > now + timedelta(minutes=5):
            continue
        frp = _finite(row.get("frp"))
        items.append({"lat": round(lat, 4), "lon": round(lon, 4), "time": stamp.isoformat(),
                      "frp": round(frp, 1) if frp is not None else None})
    items.sort(key=lambda item: item["time"], reverse=True)
    return {"items": items[:MAX_HOTSPOTS]}


def fetch_hotspots(key, now, context):
    """The last day's hotspots over the Bay map from both VIIRS satellites. The map key sits in
    the URL path, so a failure is reported without the URL."""
    items = []
    for sensor in FIRMS_SENSORS:
        url = FIRMS_URL.format(key=key, sensor=sensor, bbox=FIRMS_BBOX, days=1)
        context.raise_if_cancelled()
        try:
            response = get_http_client().request_text("GET", url, context=context, timeout=REQUEST_TIMEOUT,
                                                      max_bytes=512 * 1024, headers={"User-Agent": USER_AGENT})
        except TaskCancelled:
            raise
        except Exception as error:
            raise RuntimeError(f"FIRMS request failed ({type(error).__name__})") from None
        items += normalize_hotspots(response.data, now)["items"]
    items.sort(key=lambda item: item["time"], reverse=True)
    return {"items": items[:MAX_HOTSPOTS]}


def valid_fires(data):
    items = data.get("items") if isinstance(data, dict) else None
    return isinstance(items, list) and len(items) <= MAX_FIRES and all(
        isinstance(item, dict) and parse_time(item.get("date")) and valid_coordinates(item.get("lat"), item.get("lon"))
        for item in items)


def valid_hotspots(data):
    items = data.get("items") if isinstance(data, dict) else None
    return isinstance(items, list) and len(items) <= MAX_HOTSPOTS and all(
        isinstance(item, dict) and valid_coordinates(item.get("lat"), item.get("lon")) for item in items)


def valid_quakes(data):
    items = data.get("items") if isinstance(data, dict) else None
    return isinstance(items, list) and len(items) <= MAX_QUAKES and all(
        isinstance(item, dict) and _finite(item.get("mag")) is not None and parse_time(item.get("time"))
        and valid_coordinates(item.get("lat"), item.get("lon")) for item in items)


def valid_alerts(data):
    items = data.get("items") if isinstance(data, dict) else None
    return isinstance(items, list) and len(items) <= MAX_ALERTS and all(
        isinstance(item, dict) and item.get("event") and isinstance(item.get("counties"), list)
        for item in items)


def valid_events(data):
    items = data.get("items") if isinstance(data, dict) else None
    return isinstance(items, list) and len(items) <= MAX_EVENTS and all(
        isinstance(item, dict) and parse_time(item.get("date")) and valid_coordinates(item.get("lat"), item.get("lon"))
        for item in items)


def intensity_roman(value):
    """An intensity (CDI or MMI, 1-12) as USGS writes it, rounded: 3.8 -> 'IV'."""
    number = _finite(value)
    return ROMAN[max(1, min(12, int(number + 0.5)))] if number is not None and number > 0 else ""


def compact_message(text):
    """A forecaster's key message a little shorter for a narrow card: weekdays abbreviated,
    'through' as 'thru', 'temperatures' as 'temps'."""
    text = " ".join(str(text or "").split())
    for day, short in WEEKDAY_SHORT.items():
        text = re.sub(rf"\b{day}\b", short, text)
    text = re.sub(r"\bthrough\b", "thru", text)
    return re.sub(r"\btemperatures\b", "temps", text)


def parse_key_messages(text):
    """The '.KEY MESSAGES...' block of an Area Forecast Discussion as its bullets (each joined onto
    one line), with the time it says it was updated; ValueError when the product has none."""
    match = re.search(r"^\.KEY MESSAGES\.\.\.\s*$(.*?)^&&", str(text or ""), re.MULTILINE | re.DOTALL)
    if not match:
        raise ValueError("The discussion has no key messages")
    block = match.group(1)
    updated = re.search(r"Updated at\s+(\d{3,4}\s+[AP]M\s+[A-Z]{3})", block)
    messages, current = [], None
    for line in block.splitlines():
        bullet = re.match(r"^\s*[-*]\s+(.*)$", line)
        if bullet:
            current = [bullet.group(1).strip()]
            messages.append(current)
        elif current is not None and line.strip():
            current.append(line.strip())
        elif not line.strip():
            current = None
    messages = [" ".join(parts)[:240] for parts in messages if parts and parts[0]]
    if not messages:
        raise ValueError("The key messages are empty")
    return {"messages": messages[:MAX_KEY_MESSAGES], "updated": updated.group(1) if updated else ""}


def fetch_key_messages(context):
    product = _get_json(AFD_URL, None, context, 256 * 1024)
    if not isinstance(product, dict) or product.get("productCode") not in (None, "AFD"):
        raise ValueError("NWS returned no forecast discussion")
    issued = parse_time(product.get("issuanceTime"))
    return {**parse_key_messages(product.get("productText")), "issued": issued.isoformat() if issued else None,
            "office": str(product.get("issuingOffice") or "KMTR")[:6]}


def valid_key_messages(data):
    messages = data.get("messages") if isinstance(data, dict) else None
    return (isinstance(messages, list) and 0 < len(messages) <= MAX_KEY_MESSAGES
            and all(isinstance(message, str) and message for message in messages))


def parse_spare_the_air(text):
    """The Air District's feed as [{date, alert, status}]: one item a day (today, and tomorrow once
    it is forecast), each an alert or not."""
    try:
        root = ElementTree.fromstring(str(text or "").strip())
    except ElementTree.ParseError:
        raise ValueError("The Spare the Air feed is not XML") from None
    days = []
    for item in root.iter("item"):
        status = " ".join(str(item.findtext("description") or "").split())[:120]
        raw_date = " ".join(str(item.findtext("date") or "").split())
        try:
            date = datetime.strptime(raw_date, "%A, %B %d, %Y").date().isoformat()
        except ValueError:
            continue
        if status:
            days.append({"date": date, "status": status,
                         "alert": not re.search(r"\bno alert\b", status, re.IGNORECASE),
                         "winter": bool(re.search(r"\bwinter\b|\bwood\b|\bburn", status, re.IGNORECASE))})
    if not days:
        raise ValueError("The Spare the Air feed has no day")
    return {"days": days[:3]}


def fetch_spare_the_air(context):
    context.raise_if_cancelled()
    response = get_http_client().request_text("GET", SPARE_THE_AIR_URL, context=context, timeout=REQUEST_TIMEOUT,
                                              max_bytes=64 * 1024, headers={"User-Agent": USER_AGENT})
    context.raise_if_cancelled()
    return parse_spare_the_air(response.data)


def valid_spare_the_air(data):
    days = data.get("days") if isinstance(data, dict) else None
    return isinstance(days, list) and 0 < len(days) <= 3 and all(
        isinstance(day, dict) and day.get("date") and isinstance(day.get("alert"), bool) for day in days)


def air_today(data, now):
    """Today's Spare the Air day from the feed, with tomorrow's when it is an alert; None without today."""
    days = {day["date"]: day for day in (data or {}).get("days") or [] if isinstance(day, dict)}
    today = days.get(now.date().isoformat())
    if today is None:
        return None
    tomorrow = days.get((now + timedelta(days=1)).date().isoformat())
    return {**today, "tomorrow_alert": bool(tomorrow and tomorrow.get("alert"))}


# --- California's flu activity (CDC ILINet through CMU Delphi's keyless FluView API) -----------

FLU_URL = "https://api.delphi.cmu.edu/epidata/fluview/"
FLU_WEEKS = 8


def mmwr_week(day):
    """The CDC's (MMWR) epidemiological week of ``day`` as YYYYWW: weeks run Sunday to Saturday,
    and week 1 is the first one with at least four days in the new year."""
    def first_sunday(year):
        new_year = datetime(year, 1, 1).date()
        back = (new_year.weekday() + 1) % 7
        return new_year - timedelta(days=back) + timedelta(days=7 if back > 3 else 0)

    year = day.year
    if day >= first_sunday(year + 1):
        year += 1
    elif day < first_sunday(year):
        year -= 1
    return year * 100 + (day - first_sunday(year)).days // 7 + 1


def normalize_flu(payload):
    """The latest week's share of outpatient visits for influenza-like illness in California (%),
    the week before and the last weeks' series; {} without readings."""
    rows = payload.get("epidata") if isinstance(payload, dict) else None
    weeks = {}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or not isinstance(row.get("epiweek"), int):
            continue
        try:
            ili = float(row.get("ili"))
        except (TypeError, ValueError):
            continue
        if 0 <= ili <= 100:
            weeks[row["epiweek"]] = (ili, str(row.get("release_date") or "")[:10])
    if not weeks:
        return {}
    ordered = sorted(weeks)
    series = [round(weeks[week][0], 1) for week in ordered]
    return {"week": ordered[-1], "ili": series[-1], "previous": series[-2] if len(series) > 1 else None,
            "series": series, "released": weeks[ordered[-1]][1]}


def fetch_flu(now, context):
    weeks = f"{mmwr_week((now - timedelta(weeks=FLU_WEEKS)).date())}-{mmwr_week(now.date())}"
    return normalize_flu(_get_json(FLU_URL, {"regions": "ca", "epiweeks": weeks}, context, 64 * 1024))


def valid_flu(data):
    return isinstance(data, dict) and isinstance(data.get("ili"), float) and isinstance(data.get("series"), list)
