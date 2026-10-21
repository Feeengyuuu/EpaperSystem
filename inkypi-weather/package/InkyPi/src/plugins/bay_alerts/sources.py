"""Bounded public feeds for the Bay Alerts page: USGS quakes, NWS alerts, NASA EONET events."""

from __future__ import annotations

import math
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from utils.http_client import get_http_client


PACIFIC = ZoneInfo("America/Los_Angeles")
USER_AGENT = "InkyPi BayAlerts/1.0 (personal e-paper display)"
USGS_URL = "https://earthquake.usgs.gov/fdsnws/event/1/query"
NWS_URL = "https://api.weather.gov/alerts/active"
EONET_URL = "https://eonet.gsfc.nasa.gov/api/v3/events"
REQUEST_TIMEOUT = (3, 9)
DEFAULT_HOME = (37.5485, -121.9886)  # Fremont, the middle of the Bay
QUAKE_WINDOW_DAYS = 7
MAX_QUAKES = 300
MAX_ALERTS = 30
MAX_EVENTS = 40
MAX_POLYGON_POINTS = 240
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
        items.append({
            "id": str(feature.get("id") or props.get("code") or "")[:40],
            "mag": round(mag, 1), "time": stamp.isoformat(),
            "lat": round(lat, 4), "lon": round(lon, 4),
            "depth_km": round(depth, 1) if depth is not None else None,
            "place": chinese_place(props.get("place")),
            "felt": int(felt) if isinstance(felt, int) and felt >= 0 else None,
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
            "counties": matched[:10], "polygon": _simplified_polygon(feature.get("geometry")),
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
