"""Caltrans District 4 freeway cameras: the ones near home, a district-wide patrol for the main
view, the live stills and the hourly reference stills Caltrans keeps for each camera (the
time-lapse).
"""

from __future__ import annotations

import math
import random
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from PIL import ImageStat

from utils.http_client import get_http_client


PACIFIC = ZoneInfo("America/Los_Angeles")
USER_AGENT = "InkyPi BayTrafficCams/1.0 (personal e-paper display)"
CAMERAS_URL = "https://cwwp2.dot.ca.gov/data/d4/cctv/cctvStatusD04.json"
IMAGE_HOST = "cwwp2.dot.ca.gov"
FREMONT = (37.5485, -121.9886)
# Cameras kept from the district-wide list: within this distance of home, nearest first.
RADIUS_KM = 30
MAX_KEPT = 60
# The main view patrols the whole district instead: a new camera each slot, the same one for
# every render within it (so a cached redraw matches), bridges, tolls and tunnels more often.
PATROL_SLOT_SECONDS = 900
PATROL_KEPT = 400
LANDMARK_WEIGHT = 4
LANDMARK = re.compile(r"bridge|toll|plaza|tunnel|golden gate|sfobb|caldecott|maze|treasure|span", re.IGNORECASE)
TIMELAPSE_HOURS = 3
PLACEHOLDER_WHITE = 0.5
PLACEHOLDER_FLAT = 8
# A "No video" frame over a garbled feed: dark, yet strongly coloured, where a night road is grey.
GARBLED_DARK = 60
GARBLED_SATURATION = 100
# Each still ends in a 20 px caption bar (of 260) with Caltrans' own date; the page shows its age instead.
CAPTION_BAR = 20 / 260
# The legend of the cardinal direction plaque posted with a route shield (MUTCD M3-1 to M3-4).
DIRECTIONS = {"North": "NORTH", "South": "SOUTH", "East": "EAST", "West": "WEST"}
# Caltrans writes the same kind of place many ways ("Just North Of X", "JNO X", "NOF X"):
# each prefix becomes the Chinese side of X, read after the name.
SIDE_PREFIXES = (
    (r"(?:just\s+)?north\s+of|jno|nof", "以北"), (r"(?:just\s+)?south\s+of|jso|sof", "以南"),
    (r"(?:just\s+)?east\s+of|jeo|eof", "以东"), (r"(?:just\s+)?west\s+of|jwo|wof", "以西"),
)
RAMP_SUFFIXES = ((r"\s*-\s*looking\s+\w+$", ""), (r"\s+(?:oc|uc)$", ""),
                 (r"\s+(?:or|on-?ramp|on-s-diag)$", " 入口"), (r"\s+(?:fr|off-?ramp)$", " 出口"))
STREET_WORDS = {"BL": "Blvd", "BLVD": "Blvd", "AV": "Ave", "AVE": "Ave", "RD": "Rd", "ST": "St", "DR": "Dr",
                "PKWY": "Pkwy", "HWY": "Hwy", "LN": "Ln", "WY": "Way", "MT": "Mt", "SR": "SR", "US": "US",
                "EB": "EB", "WB": "WB", "NB": "NB", "SB": "SB"}


def local_now(now=None):
    value = now or datetime.now(PACIFIC)
    return value.replace(tzinfo=PACIFIC) if value.tzinfo is None else value.astimezone(PACIFIC)


def safe_image_url(value):
    url = str(value or "").strip()
    parts = urlsplit(url)
    return url if parts.scheme == "https" and parts.hostname == IMAGE_HOST and parts.path.endswith(".jpg") else None


def distance_km(a, b):
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h))


def _street_word(word):
    upper = word.upper()
    if upper in STREET_WORDS:
        return STREET_WORDS[upper]
    if re.fullmatch(r"\d+(?:ST|ND|RD|TH)", upper):
        return word.lower()
    return word.capitalize() if word.isalpha() and word.isupper() and len(word) > 1 else word


def place_label(location):
    """ "TVB11 -- I-880 : AT JCT-84" -> "84 号交汇"; "JNO MILLBRAE AVE OC" -> "Millbrae Ave 以北"."""
    name = str(location.get("locationName") or "")
    place = (name.split(":", 1)[1] if ":" in name else str(location.get("nearbyPlace") or "")).strip()
    place = re.sub(r"^(?:at\s+)?[NSEW]\d+\s+at\s+", "", place, flags=re.IGNORECASE)
    junction = re.match(r"^at\s+jct[- ]?(\w+)$", place, flags=re.IGNORECASE)
    if junction:
        route = junction.group(1)
        return f"{route} 号交汇" if route.isdigit() else f"{route} 交汇"
    place = re.sub(r"^at\s+", "", place, flags=re.IGNORECASE)
    side = ""
    for pattern, label in SIDE_PREFIXES:
        match = re.match(rf"^(?:{pattern})\s+", place, flags=re.IGNORECASE)
        if match:
            place, side = place[match.end():], f" {label}"
            break
    ramp = ""
    for pattern, label in RAMP_SUFFIXES:
        match = re.search(pattern, place, flags=re.IGNORECASE)
        if match:
            place, ramp = place[:match.start()], ramp or label
    words = " ".join(_street_word(word) for word in place.split())
    return f"{words}{ramp}{side}".strip()[:32]


def bearing(home, point):
    """Compass bearing in degrees from home to the camera, 0 = north."""
    lat1, lon1, lat2, lon2 = map(math.radians, (*home, *point))
    x = math.sin(lon2 - lon1) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(lon2 - lon1)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def compass_label(degrees):
    """Eight-point Chinese compass name for a bearing: 0 -> 北, 45 -> 东北."""
    index = int(((degrees % 360) + 22.5) // 45) % 8
    return ("北", "东北", "东", "东南", "南", "西南", "西", "西北")[index]


def history_steps(static):
    """Reference-image numbers one, two and three hours back; Caltrans keeps 12 of them,
    every 15 minutes on some cameras and every hour on others."""
    try:
        minutes = int(str(static.get("referenceImageUpdateFrequency") or "").strip())
    except ValueError:
        minutes = 60
    step = max(1, round(60 / minutes)) if minutes > 0 else 1
    return [step * hours for hours in range(1, TIMELAPSE_HOURS + 1) if step * hours <= 12]


def normalize_cameras(payload, home):
    rows = (payload or {}).get("data") if isinstance(payload, dict) else None
    cameras, patrol = [], []
    for row in rows or []:
        camera = (row or {}).get("cctv") or {}
        location = camera.get("location") or {}
        static = (camera.get("imageData") or {}).get("static") or {}
        image = safe_image_url(static.get("currentImageURL"))
        if str(camera.get("inService")).lower() != "true" or not image:
            continue
        try:
            point = (float(location.get("latitude")), float(location.get("longitude")))
        except (TypeError, ValueError):
            continue
        distance = distance_km(home, point)
        history = [safe_image_url(static.get(f"referenceImage{step}UpdatesAgoURL" if step > 1
                                             else "referenceImage1UpdateAgoURL"))
                   for step in history_steps(static)]
        name = str(location.get("locationName") or "")
        camera = {
            "id": name.split(" ", 1)[0][:12],
            "route": str(location.get("route") or "")[:10], "direction": str(location.get("direction") or ""),
            "latitude": point[0], "longitude": point[1],
            "place": place_label(location), "town": str(location.get("nearbyPlace") or "")[:20],
            "distance_km": round(distance, 1), "bearing": round(bearing(home, point)), "image": image,
            "history": [url for url in history if url], "landmark": bool(LANDMARK.search(name)),
        }
        patrol.append(camera)
        if distance <= RADIUS_KM:
            cameras.append(camera)
    cameras.sort(key=lambda camera: camera["distance_km"])
    if not cameras:
        raise ValueError("Caltrans returned no cameras near home")
    # Landmarks first, so a district larger than the cap keeps all of them.
    patrol.sort(key=lambda camera: (not camera["landmark"], camera["id"]))
    return {"cameras": cameras[:MAX_KEPT], "patrol": patrol[:PATROL_KEPT]}


def fetch_cameras(home, context):
    context.raise_if_cancelled()
    response = get_http_client().request_json("GET", CAMERAS_URL, context=context, timeout=(4, 20),
                                              max_bytes=6 * 1024 * 1024, headers={"User-Agent": USER_AGENT})
    context.raise_if_cancelled()
    return normalize_cameras(response.data, home)


def valid_cameras(data):
    return bool((data or {}).get("cameras"))


def choose(cameras, count, main_id=""):
    """The main camera (the named one, else the nearest) and the next nearest on other roads.

    One camera per route and direction comes first, so the wall covers different roads
    before it shows a second view of the same one.
    """
    pool = list(cameras or [])
    main = next((camera for camera in pool if main_id and camera["id"].upper() == main_id.upper()), None)
    main = main or (pool[0] if pool else None)
    chosen = [main] if main else []
    seen = {(main["route"], main["direction"])} if main else set()
    for camera in pool:
        if len(chosen) >= count:
            break
        key = (camera["route"], camera["direction"])
        if camera is not main and key not in seen:
            chosen.append(camera)
            seen.add(key)
    for camera in pool:
        if len(chosen) >= count:
            break
        if camera not in chosen:
            chosen.append(camera)
    return chosen


def patrol(cameras, now, exclude=(), count=6):
    """This slot's patrol: ``count`` district cameras in a weighted random order (landmarks
    LANDMARK_WEIGHT times as likely), leaving out ``exclude`` ids; the first that has a still
    leads the page and the rest stand by."""
    rng = random.Random(int(now.timestamp() // PATROL_SLOT_SECONDS))
    remaining = [camera for camera in cameras or [] if camera["id"] not in set(exclude)]
    order = []
    while remaining and len(order) < count:
        weights = [LANDMARK_WEIGHT if camera.get("landmark") else 1 for camera in remaining]
        order.append(remaining.pop(rng.choices(range(len(remaining)), weights=weights)[0]))
    return order


def frame_time(headers):
    """When the still was taken, from its Last-Modified header (None when absent, or when it is
    the epoch some cameras send for stills they never renewed)."""
    value = (headers or {}).get("Last-Modified") or (headers or {}).get("last-modified")
    try:
        stamp = parsedate_to_datetime(value) if value else None
    except (TypeError, ValueError):
        return None
    if stamp is None or stamp.year < 2000:
        return None
    return stamp.astimezone(timezone.utc)


def history_is_fresh(camera, frames, now, slack_minutes=90):
    """True when each of the camera's past stills is in hand and was taken near the hour it
    stands for; a camera whose reference stills Caltrans stopped renewing has no time-lapse."""
    history = camera.get("history") or []
    if not history:
        return False
    for hours, url in enumerate(history, start=1):
        try:
            taken = datetime.fromisoformat(str((frames.get(url) or {}).get("taken")))
        except (TypeError, ValueError):
            return False
        if abs((now - taken).total_seconds() / 60 - hours * 60) > slack_minutes:
            return False
    return True


def direction_label(direction):
    """The cardinal plaque's legend for Caltrans' direction ("North" -> "NORTH"), "" when unknown."""
    return DIRECTIONS.get(str(direction or "").strip().title(), "")


def is_placeholder(image):
    """True for a card Caltrans shows in place of a camera that is down: the white
    "Temporarily Unavailable" card (mostly plain white, which no road view is), a flat
    black "no video" frame (almost no variation, where even a night road has lights) or a
    "No video" frame over a garbled feed (dark but strongly coloured, where night is grey)."""
    width, height = image.size
    view = image.convert("RGB").crop((0, 0, width, height - round(height * CAPTION_BAR))).resize((32, 24))
    _hue, saturation, brightness = ImageStat.Stat(view.convert("HSV")).mean
    if brightness < GARBLED_DARK and saturation > GARBLED_SATURATION:
        return True
    small = view.convert("L")
    counts = small.histogram()
    total = sum(counts)
    white = sum(counts[236:]) / total
    mean = sum(value * count for value, count in enumerate(counts)) / total
    spread = (sum((value - mean) ** 2 * count for value, count in enumerate(counts)) / total) ** 0.5
    return white > PLACEHOLDER_WHITE or spread < PLACEHOLDER_FLAT
