"""Caltrans District 4 freeway cameras: the ones near home, a district-wide patrol for the main
view, the live stills and the hourly reference stills Caltrans keeps for each camera (the
time-lapse); and what the district's changeable message signs report when it matters.
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


# --- Changeable message signs ---------------------------------------------------------------------

# District 4's changeable message signs (about 220 KB, served uncompressed, renewed every minute):
# some 190 signs, most of them blank or showing travel times. Only the ones that report something
# (a crash, blocked lanes, a closure, chain control) are kept, so the cache is a kilobyte or two.
SIGNS_URL = "https://cwwp2.dot.ca.gov/data/d4/cms/cmsStatusD04.json"
MAX_SIGNS_BYTES = 400 * 1024
MAX_SIGNS = 24
# A sign's message belongs to a camera on the same route this near it.
SIGN_TIE_KM = 20
# A travel-time line as the sign shows it: "MILPITAS  17 MIN".
TRAVEL_LINE = re.compile(r".+?\s+\d{1,3}\s*MIN")
# When a notice gives a day or an hour it is about something still to come.
SCHEDULE = re.compile(
    r"\b(?:MON|TUE|TUES|WED|THU|THUR|THURS|FRI|SAT|SUN)(?:DAY|SDAY|NESDAY|RSDAY|URDAY)?\b"
    r"|\b(?:JAN|FEB|MAR|APR|JUN|JUL|AUG|SEP|SEPT|OCT|NOV|DEC)\b|\b(?:TONIGHT|TOMORROW|NIGHTLY|WEEKEND|WEEKNIGHTS?)\b"
    r"|\d\s*(?:AM|PM)\b")
CLOSING = re.compile(r"\b(?:CLOSED|CLOSURES?|CLOSING)\b")
ROADWORK = re.compile(r"\b(?:ROAD ?WORK|CONSTRUCTION|DETOUR|PAVING|MAINTENANCE)\b")
# What a message reports, most urgent first: (kind, what its words look like).
SIGN_KINDS = (
    ("crash", re.compile(r"\b(?:CRASH|ACCIDENT|COLLISION|INCIDENT|OVERTURN\w*|SPILL|STALL\w*|DEBRIS|EMERGENCY|POLICE)\b")),
    ("alert", re.compile(r"\b(?:AMBER|SILVER|FEATHER|EBONY|ENDANGERED) ALERT\b|\bEVACUAT\w*|\bTSUNAMI\b")),
    ("blocked", re.compile(r"\b(?:CLOSED|CLOSURES?|CLOSING|BLKD|BLOCKED)\b")),
    ("weather", re.compile(r"\b(?:CHAINS?|SNOW|ICE|ICY|FOG|WINDS?|GUSTS?|FLOOD\w*|FIRE|SMOKE|SLIDE|MUDSLIDE|ROCKSLIDE)\b")),
    ("work", re.compile(r"\b(?:ROAD ?WORK|CONSTRUCTION|DETOUR|PAVING|MAINTENANCE|DELAYS?|CONGEST\w*|HEAVY TRAFFIC|"
                        r"TRAFFIC (?:AHEAD|ALERT|JAM)|EVENT TRAFFIC|BACKUP|EXPECT)\b")),
)
# A notice of something planned ranks between the weather and the roadwork under way.
SIGN_RANK = {"crash": 0, "alert": 0, "blocked": 1, "weather": 2, "planned": 3, "work": 4}
WEATHER_LABELS = (("CHAIN", "防滑链"), ("FOG", "浓雾"), ("WIND", "大风"), ("GUST", "大风"), ("FLOOD", "积水"),
                  ("SNOW", "冰雪"), ("ICE", "冰雪"), ("ICY", "冰雪"), ("FIRE", "火情"), ("SMOKE", "火情"))


def _sign_pages(message):
    """A sign's pages as lists of its lines, in capitals, without the empty or unreported ones."""
    pages = []
    for phase in ("phase1", "phase2"):
        page = message.get(phase) if isinstance(message, dict) else None
        lines = [" ".join(str((page or {}).get(f"{phase}Line{index}") or "").split()).upper()[:24] for index in (1, 2, 3)]
        lines = [line for line in lines if line and line != "NOT REPORTED"]
        if lines:
            pages.append(lines)
    return pages


def sign_kind(pages):
    """What a sign's message reports ("crash", "alert", "blocked", "weather", "planned", "work"),
    or None for travel times, a slogan or anything else that needs no one's attention."""
    lines = [line for page in pages for line in page]
    if not lines or all(TRAVEL_LINE.fullmatch(line) for line in lines):
        return None
    text = " ".join(lines)
    for kind, pattern in SIGN_KINDS:
        if pattern.search(text):
            # A closure or roadwork with a day or an hour on it is a notice of what is to come.
            if kind in ("blocked", "work") and (CLOSING.search(text) or ROADWORK.search(text)) and SCHEDULE.search(text):
                return "planned"
            return kind
    return None


def sign_label(kind, pages):
    """The message's kind in Chinese, by its own words where the kind covers several things."""
    text = " ".join(line for page in pages for line in page)
    if kind == "blocked":
        return "封闭" if CLOSING.search(text) else "车道受阻"
    if kind == "planned":
        return "封闭预告" if CLOSING.search(text) else "施工预告"
    if kind == "weather":
        return next((label for word, label in WEATHER_LABELS if re.search(rf"\b{word}", text)), "路况警示")
    if kind == "work":
        return "施工" if ROADWORK.search(text) else "延误"
    return {"crash": "事故", "alert": "警报"}.get(kind, "情报板")


def normalize_signs(payload, home):
    """The in-service signs whose message matters, most urgent first and then nearest home:
    {signs: [{id, route, direction, place, lat, lon, distance_km, pages, kind, posted}]}."""
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or len(rows) > 2000:
        raise ValueError("Caltrans returned no sign list")
    signs = []
    for row in rows:
        sign = row.get("cms") if isinstance(row, dict) else None
        if not isinstance(sign, dict) or str(sign.get("inService")).lower() != "true":
            continue
        location, message = sign.get("location") or {}, sign.get("message") or {}
        pages = _sign_pages(message)
        kind = sign_kind(pages)
        try:
            point = (float(location.get("latitude")), float(location.get("longitude")))
        except (TypeError, ValueError):
            continue
        if kind is None or not (-90 <= point[0] <= 90 and -180 <= point[1] <= 180):
            continue
        stamp = message.get("messageTimestamp") or {}
        try:
            posted = datetime.fromisoformat(f"{stamp.get('messageDate')}T{stamp.get('messageTime')}").replace(tzinfo=PACIFIC)
        except (TypeError, ValueError):
            posted = None
        signs.append({"id": str(sign.get("index") or "")[:12], "route": str(location.get("route") or "")[:10],
                      "direction": str(location.get("direction") or "")[:10],
                      "place": str(location.get("nearbyPlace") or "")[:24],
                      "lat": round(point[0], 5), "lon": round(point[1], 5),
                      "distance_km": round(distance_km(home, point), 1), "pages": pages, "kind": kind,
                      "posted": posted.isoformat() if posted else None})
    signs.sort(key=lambda sign: (SIGN_RANK[sign["kind"]], sign["distance_km"], sign["id"]))
    return {"signs": signs[:MAX_SIGNS]}


def fetch_signs(home, context):
    context.raise_if_cancelled()
    response = get_http_client().request_json("GET", SIGNS_URL, context=context, timeout=(4, 8),
                                              max_bytes=MAX_SIGNS_BYTES, headers={"User-Agent": USER_AGENT})
    context.raise_if_cancelled()
    return normalize_signs(response.data, home)


def valid_signs(data):
    signs = data.get("signs") if isinstance(data, dict) else None
    return isinstance(signs, list) and len(signs) <= MAX_SIGNS and all(
        isinstance(sign, dict) and isinstance(sign.get("route"), str) and sign.get("kind") in SIGN_RANK
        and isinstance(sign.get("pages"), list) and sign["pages"]
        and all(isinstance(page, list) and 1 <= len(page) <= 3 and all(isinstance(line, str) for line in page)
                for page in sign["pages"])
        and isinstance(sign.get("distance_km"), (int, float)) for sign in signs)


def _tied_camera(sign, cameras):
    """The first camera on screen that looks at the sign's road near it (its index), else None."""
    for index, camera in enumerate(cameras or []):
        try:
            point = (float(camera["latitude"]), float(camera["longitude"]))
        except (KeyError, TypeError, ValueError):
            continue
        if camera.get("route") == sign["route"] and distance_km(point, (sign["lat"], sign["lon"])) <= SIGN_TIE_KM:
            return index
    return None


def pick_message(data, cameras):
    """The one message for the page's board: the most urgent kind first, then a message on the
    road of a camera on screen (the main view before the wall), then the nearest to home. The
    same message on several signs is one message, told at the sign that put it first; of a
    sign's two pages the board takes the one that says what is the matter.
    {kind, label, lines, route, direction, place, posted, signs, camera, more}, or None
    (``camera`` is the index in ``cameras`` of the one on the sign's road, else None)."""
    groups = {}
    for sign in (data or {}).get("signs") or []:
        tie = _tied_camera(sign, cameras)
        order = (SIGN_RANK[sign["kind"]], tie is None, tie or 0, sign["distance_km"])
        group = groups.setdefault((sign["kind"], tuple(sign["pages"][0][:2])), {"count": 0, "best": None})
        group["count"] += 1
        if group["best"] is None or order < group["best"][0]:
            group["best"] = (order, sign, tie)
    if not groups:
        return None
    ranked = sorted(groups.values(), key=lambda group: group["best"][0])
    _order, sign, tie = ranked[0]["best"]
    page = next((page for page in sign["pages"] if sign_kind([page]) is not None), sign["pages"][0])
    return {"kind": sign["kind"], "label": sign_label(sign["kind"], sign["pages"]), "lines": list(page),
            "route": sign["route"], "direction": sign["direction"], "place": sign["place"], "posted": sign["posted"],
            "signs": ranked[0]["count"], "camera": tie, "more": len(ranked) - 1}


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
