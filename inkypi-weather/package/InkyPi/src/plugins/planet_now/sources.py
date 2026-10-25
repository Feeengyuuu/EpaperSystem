"""环球此刻's feeds: California's grid, the sky over the Bay, what America is searching, and space.

Every feed is public and needs no key: CAISO's Today's Outlook CSVs, NOAA's GOES-West
GeoColor image of the Pacific Southwest, RainViewer's radar, NASA's EPIC Earth images,
Google Trends' daily RSS, Wikipedia's featured feed, Apple's charts, NASA's Perseverance
raw images, wheretheiss.at and the people-in-space list. Pictures are cut and shrunk as they arrive and kept in the
feed's cached data (base64 JPEG), so a theme redraw needs no network. Parsers are plain
functions over the fetched text, testable offline.
"""

from __future__ import annotations

import base64
import csv
import io
import math
import re
from datetime import datetime, timedelta, timezone
from html import unescape
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

from PIL import Image

from runtime.refresh_contracts import TaskCancelled
from utils.http_client import get_http_client
from utils.safe_image import ImageLimits, safe_open_base64_image, safe_open_image

PACIFIC = ZoneInfo("America/Los_Angeles")
USER_AGENT = "InkyPi PlanetNow/1.0 (https://github.com/Feeengyuuu/EpaperSystem)"
REQUEST_TIMEOUT = (4, 15)
# Home: Fremont, as the other Bay pages place it.
HOME = (37.5485, -121.9886)

CAISO_FUEL_URL = "https://www.caiso.com/outlook/current/fuelsource.csv"
CAISO_DEMAND_URL = "https://www.caiso.com/outlook/current/demand.csv"
# CAISO's fuel columns by the group the page draws (batteries count while discharging).
FUEL_GROUPS = (
    ("solar", ("Solar",)), ("wind", ("Wind",)), ("hydro", ("Large Hydro", "Small hydro")),
    ("nuclear", ("Nuclear",)), ("gas", ("Natural Gas",)), ("imports", ("Imports",)),
    ("batteries", ("Batteries",)), ("other", ("Geothermal", "Biomass", "Biogas", "Coal", "Other")),
)
RENEWABLE_COLUMNS = ("Solar", "Wind", "Geothermal", "Biomass", "Biogas", "Small hydro")
GRID_STEP_MINUTES = 15

GOES_URL = "https://cdn.star.nesdis.noaa.gov/GOES18/ABI/SECTOR/psw/GEOCOLOR/2400x2400.jpg"
# The sector image is a fixed Mercator map, GOES_PX_PER_DEGREE pixels to a degree of longitude,
# with home (Fremont) at GOES_HOME. The page shows GOES_SPAN of it centred on home, from the
# Golden Gate and the coast to the East Bay hills and San Jose, at GOES_SIZE.
GOES_HOME = (716, 1180)
GOES_PX_PER_DEGREE = 158.0
GOES_SPAN = (420, 190)
GOES_SIZE = (366, 166)
_GOES_LIMITS = ImageLimits(max_bytes=8 * 1024 * 1024, max_width=2400, max_height=2400, max_pixels=2400 * 2400)
RAINVIEWER_URL = "https://api.rainviewer.com/public/weather-maps.json"
RAIN_ZOOM, RAIN_RADIUS_KM = 7, 30
EPIC_URL = "https://epic.gsfc.nasa.gov/api/natural"
EPIC_THUMB = "https://epic.gsfc.nasa.gov/archive/natural/{date:%Y/%m/%d}/thumbs/{image}.jpg"
# EPIC photographs Earth through the day; the Americas face it near this longitude.
EPIC_LONGITUDE = -110
TRENDS_URL = "https://trends.google.com/trending/rss?geo=US"
TRENDS_NS = {"ht": "https://trends.google.com/trending/rss"}
WIKI_FEED_URL = "https://en.wikipedia.org/api/rest_v1/feed/featured/{date:%Y/%m/%d}"
APPLE_SONGS_URL = "https://rss.marketingtools.apple.com/api/v2/us/music/most-played/5/songs.json"
MARS_URL = "https://mars.nasa.gov/rss/api/"
# Perseverance's cameras worth a look on the page, best first (the colour and wide views).
MARS_CAMERAS = ("MCZ_LEFT", "MCZ_RIGHT", "NAVCAM_LEFT", "NAVCAM_RIGHT", "FRONT_HAZCAM_LEFT_A",
                "FRONT_HAZCAM_RIGHT_A", "REAR_HAZCAM_LEFT", "REAR_HAZCAM_RIGHT", "SKYCAM")
MARS_CAMERA_NAMES = {"MCZ": "桅杆相机 Mastcam-Z", "NAVCAM": "导航相机", "HAZCAM": "避障相机", "SKYCAM": "天空相机",
                     "SHERLOC": "SHERLOC 显微相机", "SUPERCAM": "SuperCam", "PIXL": "PIXL", "CACHECAM": "采样相机"}
# The cameras whose full frames make a landscape, best first; the mast cameras count only when
# looking within MARS_HORIZON degrees of the horizon.
MARS_SCENIC = ("MCZ", "NAVCAM", "FRONT_HAZCAM", "REAR_HAZCAM")
MARS_MIN_WIDTH = 1000
MARS_HORIZON = (-35.0, 20.0)
MARS_SKYLINE = -2.0  # a mast camera's elevation that puts the skyline in the frame
MARS_RECENT_SOLS = 4
AU_KM = 149_597_870.7
LIGHT_KM_S = 299_792.458
# Mean orbital elements at J2000 and their rates per Julian century (JPL's approximate positions
# of the planets, 1800-2050): a (AU), e, I, L, long. perihelion, long. node (degrees).
ORBITS = {
    "earth": ((1.00000261, 0.01671123, -0.00001531, 100.46457166, 102.93768193, 0.0),
              (0.00000562, -0.00004392, -0.01294668, 35999.37244981, 0.32327364, 0.0)),
    "mars": ((1.52371034, 0.09339410, 1.84969142, -4.55343205, -23.94362959, 49.55953891),
             (0.00001847, 0.00007882, -0.00813131, 19140.30268499, 0.44441088, -0.29257343)),
}
# The ISS's orbit for its drawn ground track: inclination, period, and the Earth's turn per minute.
ISS_INCLINATION = 51.64
ISS_PERIOD_MIN = 92.9
EARTH_DEG_PER_MIN = 360 / 1436.07
EARTH_RADIUS_KM = 6371.0
ISS_URL = "https://api.wheretheiss.at/v1/satellites/25544"
ISS_COORDINATES_URL = "https://api.wheretheiss.at/v1/coordinates"
# The ISS's track drawn on the map: these minutes either side of now.
ISS_TRACK_MINUTES = (-30, -20, -10, 10, 20, 30, 40)
PEOPLE_URL = "https://corquaid.github.io/international-space-station-APIs/JSON/people-in-space.json"
WIKI_API = {"en": "https://en.wikipedia.org/w/api.php", "zh": "https://zh.wikipedia.org/w/api.php"}
CREW_ROLES = {"commander": "指令长", "pilot": "驾驶员", "payload specialist": "载荷专家",
              "flight engineer": "飞行工程师", "mission specialist": "任务专家", "spaceflight participant": "太空游客"}
CHINESE_DIGITS = "零一二三四五六七八九"
IMAGE_HOSTS = {"encrypted-tbn0.gstatic.com", "encrypted-tbn1.gstatic.com", "encrypted-tbn2.gstatic.com",
               "encrypted-tbn3.gstatic.com", "upload.wikimedia.org", "thumb.wikimedia.org", "mars.nasa.gov",
               "is1-ssl.mzstatic.com", "is2-ssl.mzstatic.com", "is3-ssl.mzstatic.com", "is4-ssl.mzstatic.com",
               "is5-ssl.mzstatic.com"}
_LIMITS = ImageLimits(max_bytes=4 * 1024 * 1024, max_width=4096, max_height=4096, max_pixels=4096 * 4096)


def local_now(now=None):
    value = now or datetime.now(PACIFIC)
    return value.astimezone(PACIFIC) if value.tzinfo else value.replace(tzinfo=PACIFIC)


def safe_image_url(value):
    url = str(value or "").strip()
    parts = urlsplit(url)
    return url if parts.scheme == "https" and parts.hostname in IMAGE_HOSTS else None


def _text(method, url, context, *, max_bytes=512 * 1024, params=None):
    context.raise_if_cancelled()
    response = get_http_client().request_text(method, url, context=context, timeout=REQUEST_TIMEOUT,
                                              max_bytes=max_bytes, params=params,
                                              headers={"User-Agent": USER_AGENT})
    context.raise_if_cancelled()
    return response.data


def _json(url, context, *, max_bytes=512 * 1024, params=None):
    context.raise_if_cancelled()
    response = get_http_client().request_json("GET", url, context=context, timeout=REQUEST_TIMEOUT,
                                              max_bytes=max_bytes, params=params,
                                              headers={"User-Agent": USER_AGENT})
    context.raise_if_cancelled()
    return response.data


def _bytes(url, context, *, max_bytes=2 * 1024 * 1024):
    context.raise_if_cancelled()
    response = get_http_client().request_bytes("GET", url, context=context, timeout=REQUEST_TIMEOUT,
                                               max_bytes=max_bytes, headers={"User-Agent": USER_AGENT})
    context.raise_if_cancelled()
    return response


def encode_jpeg(image, quality=86):
    out = io.BytesIO()
    image.convert("RGB").save(out, format="JPEG", quality=quality, optimize=True)
    return base64.b64encode(out.getvalue()).decode("ascii")


def decode_image(value):
    """A picture kept in a feed's data, or None."""
    try:
        with safe_open_base64_image(value or "", limits=_LIMITS) as source:
            return source.convert("RGB")
    except TaskCancelled:
        raise
    except Exception:
        return None


# --- California's grid (CAISO Today's Outlook) ---------------------------------------------------

def _rows(text):
    return [row for row in csv.DictReader(io.StringIO(str(text or "").lstrip("﻿"))) if row.get("Time")]


def _number(value):
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def parse_grid(fuel_text, demand_text):
    """Today's supply in GRID_STEP_MINUTES steps by group (MW), the newest reading, the share from
    renewables, and the demand so far with the day-ahead forecast for the whole day."""
    series, latest = [], None
    for row in _rows(fuel_text):
        values = {column: _number(row.get(column)) for column in row if column != "Time"}
        if all(value is None for value in values.values()):
            continue
        groups = {name: sum(max(0.0, values.get(column) or 0.0) for column in columns)
                  for name, columns in FUEL_GROUPS}
        point = {"time": row["Time"][:5], **{name: round(value) for name, value in groups.items()}}
        latest = (point, values)
        hour, minute = (int(part) for part in point["time"].split(":"))
        if minute % GRID_STEP_MINUTES == 0:
            series.append(point)
    if latest is None:
        return {}
    point, values = latest
    if series[-1:] != [point]:
        series.append(point)
    supply = sum(value for name, value in point.items() if name != "time")
    renewables = sum(max(0.0, values.get(column) or 0.0) for column in RENEWABLE_COLUMNS)
    demand, forecast = [], []
    for row in _rows(demand_text):
        hour, minute = (int(part) for part in row["Time"][:5].split(":"))
        if minute % GRID_STEP_MINUTES:
            continue
        ahead, current = _number(row.get("Day ahead forecast")), _number(row.get("Current demand"))
        if ahead is not None:
            forecast.append({"time": row["Time"][:5], "mw": round(ahead)})
        if current is not None:
            demand.append({"time": row["Time"][:5], "mw": round(current)})
    peak = max(forecast, key=lambda item: item["mw"]) if forecast else None
    return {"series": series, "now": point, "supply_mw": round(supply),
            "renewable_share": renewables / supply if supply else 0.0,
            "demand": demand, "forecast": forecast, "peak": peak}


def fetch_grid(context):
    return parse_grid(_text("GET", CAISO_FUEL_URL, context, max_bytes=128 * 1024),
                      _text("GET", CAISO_DEMAND_URL, context, max_bytes=128 * 1024))


def valid_grid(data):
    return isinstance(data, dict) and bool(data.get("series")) and isinstance(data.get("now"), dict)


# --- The sky over the Bay --------------------------------------------------------------------------

def _mercator(lat):
    return math.degrees(math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)))


def sky_position(lat, lon):
    """Where a place falls on the page's satellite crop, in GOES_SIZE pixels (home is its centre)."""
    x = GOES_SPAN[0] / 2 + (lon - HOME[1]) * GOES_PX_PER_DEGREE
    y = GOES_SPAN[1] / 2 - (_mercator(lat) - _mercator(HOME[0])) * GOES_PX_PER_DEGREE
    return x * GOES_SIZE[0] / GOES_SPAN[0], y * GOES_SIZE[1] / GOES_SPAN[1]


def sky_pixels(km):
    """A distance on the ground as pixels across the page's satellite crop."""
    km_per_degree = 111.32 * math.cos(math.radians(HOME[0]))
    return km / km_per_degree * GOES_PX_PER_DEGREE * GOES_SIZE[0] / GOES_SPAN[0]


def cut_goes(image):
    """The crop round home of the sector image at GOES_SIZE, and home's position in it."""
    left, top = GOES_HOME[0] - GOES_SPAN[0] // 2, GOES_HOME[1] - GOES_SPAN[1] // 2
    crop = image.convert("RGB").crop((left, top, left + GOES_SPAN[0], top + GOES_SPAN[1]))
    home = sky_position(*HOME)
    return crop.resize(GOES_SIZE, Image.Resampling.LANCZOS), (round(home[0]), round(home[1]))


def fetch_goes(context):
    response = _bytes(GOES_URL, context, max_bytes=_GOES_LIMITS.max_bytes)
    image = safe_open_image(io.BytesIO(response.data), limits=_GOES_LIMITS)
    crop, home = cut_goes(image)
    taken = response.headers.get("Last-Modified") or response.headers.get("last-modified") or ""
    try:
        taken_at = datetime.strptime(taken, "%a, %d %b %Y %H:%M:%S GMT").replace(tzinfo=timezone.utc).isoformat()
    except ValueError:
        taken_at = ""
    return {"image": encode_jpeg(crop), "home": list(home), "taken_at": taken_at}


def valid_goes(data):
    return isinstance(data, dict) and bool(data.get("image"))


def tile_position(lat, lon, zoom):
    """(tile x, tile y, pixel x, pixel y) of a point in a 256 px Web Mercator tile."""
    scale = 2 ** zoom
    x = (lon + 180) / 360 * scale
    lat_rad = math.radians(lat)
    y = (1 - math.log(math.tan(lat_rad) + 1 / math.cos(lat_rad)) / math.pi) / 2 * scale
    return int(x), int(y), int((x % 1) * 256), int((y % 1) * 256)


def rain_coverage(tile, pixel, radius_px):
    """The share of the disc round ``pixel`` that the radar paints (any echo at all)."""
    alpha = tile.convert("RGBA").getchannel("A")
    cx, cy = pixel
    inside = painted = 0
    for y in range(max(0, cy - radius_px), min(alpha.height, cy + radius_px + 1)):
        for x in range(max(0, cx - radius_px), min(alpha.width, cx + radius_px + 1)):
            if (x - cx) ** 2 + (y - cy) ** 2 <= radius_px ** 2:
                inside += 1
                painted += alpha.getpixel((x, y)) > 40
    return painted / inside if inside else 0.0


def fetch_rain(context):
    maps = _json(RAINVIEWER_URL, context, max_bytes=64 * 1024)
    frames = ((maps or {}).get("radar") or {}).get("past") or []
    if not frames:
        raise ValueError("RainViewer listed no radar frame")
    frame = frames[-1]
    tile_x, tile_y, pixel_x, pixel_y = tile_position(HOME[0], HOME[1], RAIN_ZOOM)
    url = f"{maps['host']}{frame['path']}/256/{RAIN_ZOOM}/{tile_x}/{tile_y}/2/1_1.png"
    tile = safe_open_image(io.BytesIO(_bytes(url, context, max_bytes=256 * 1024).data), limits=_LIMITS)
    km_per_px = 40075 * math.cos(math.radians(HOME[0])) / (256 * 2 ** RAIN_ZOOM)
    coverage = rain_coverage(tile, (pixel_x, pixel_y), max(2, round(RAIN_RADIUS_KM / km_per_px)))
    return {"coverage": round(coverage, 3), "time": int(frame.get("time") or 0)}


def valid_rain(data):
    return isinstance(data, dict) and "coverage" in data


def pick_epic(items):
    """The day's EPIC image facing the Americas (its centroid nearest EPIC_LONGITUDE)."""
    rows = [item for item in items or [] if isinstance(item, dict) and item.get("image")]
    if not rows:
        return None
    return min(rows, key=lambda item: abs(((item.get("centroid_coordinates") or {}).get("lon", 0) - EPIC_LONGITUDE
                                           + 180) % 360 - 180))


def fetch_epic(context):
    item = pick_epic(_json(EPIC_URL, context, max_bytes=256 * 1024))
    if item is None:
        raise ValueError("EPIC listed no image")
    taken = datetime.strptime(item["date"], "%Y-%m-%d %H:%M:%S")
    response = _bytes(EPIC_THUMB.format(date=taken, image=item["image"]), context, max_bytes=512 * 1024)
    image = safe_open_image(io.BytesIO(response.data), limits=_LIMITS).convert("RGB")
    image.thumbnail((140, 140), Image.Resampling.LANCZOS)
    return {"image": encode_jpeg(image), "date": taken.strftime("%Y-%m-%d")}


def valid_epic(data):
    return isinstance(data, dict) and bool(data.get("image"))


# --- What America is searching -------------------------------------------------------------------

def traffic_number(text):
    """'100000+' -> 100000; '2,000+' -> 2000 (0 when unreadable)."""
    digits = re.sub(r"[^\d]", "", str(text or ""))
    return int(digits) if digits else 0


def parse_trends(xml_text, limit=8):
    """[{title, traffic, picture, news, source}] of Google Trends' daily RSS, the most searched
    first (the feed lists them newest first), each with its leading headline and outlet."""
    root = ElementTree.fromstring(xml_text)
    items = []
    for item in root.iter("item"):
        title = unescape((item.findtext("title") or "").strip())
        if not title:
            continue
        news = item.find("ht:news_item", TRENDS_NS)

        def field(name, news=news):
            return unescape((news.findtext(name, "", TRENDS_NS) if news is not None else "") or "").strip()

        items.append({"title": title, "traffic": traffic_number(item.findtext("ht:approx_traffic", "", TRENDS_NS)),
                      "picture": (item.findtext("ht:picture", "", TRENDS_NS) or "").strip(),
                      "news": field("ht:news_item_title"), "source": field("ht:news_item_source")})
    items.sort(key=lambda item: -item["traffic"])
    return items[:limit]


def fetch_trends(context):
    return {"items": parse_trends(_text("GET", TRENDS_URL, context, max_bytes=256 * 1024))}


def valid_trends(data):
    return isinstance(data, dict) and bool(data.get("items"))


def parse_most_read(feed, limit=5):
    """[{title, views, description, thumbnail}] of a featured feed's most-read articles, skipping
    Wikipedia's own pages (the Main Page, Special: pages)."""
    articles = ((feed or {}).get("mostread") or {}).get("articles") or []
    rows = []
    for article in articles:
        title = str(article.get("normalizedtitle") or article.get("title") or "")
        if not title or ":" in str(article.get("title") or "") or title == "Main Page" or title.startswith("Deaths in"):
            continue
        rows.append({"title": title, "views": int(article.get("views") or 0),
                     "description": str(article.get("description") or ""),
                     "thumbnail": str((article.get("thumbnail") or {}).get("source") or "")})
        if len(rows) >= limit:
            break
    return {"date": str(((feed or {}).get("mostread") or {}).get("date") or "")[:10], "articles": rows}


def fetch_wiki(now, context):
    # The most-read block appears a day or so late, and not in every day's feed.
    for days in (0, 1, 2, 3):
        feed = _json(WIKI_FEED_URL.format(date=now - timedelta(days=days)), context, max_bytes=1024 * 1024)
        parsed = parse_most_read(feed)
        if parsed["articles"]:
            return parsed
    raise ValueError("Wikipedia listed no most-read articles")


def valid_wiki(data):
    return isinstance(data, dict) and bool(data.get("articles"))


def parse_apple(feed):
    results = (((feed or {}).get("feed") or {}).get("results")) or []
    return {"songs": [{"name": str(row.get("name") or ""), "artist": str(row.get("artistName") or ""),
                       "artwork": str(row.get("artworkUrl100") or "")} for row in results if row.get("name")][:5]}


def fetch_apple(context):
    return parse_apple(_json(APPLE_SONGS_URL, context, max_bytes=128 * 1024))


def valid_apple(data):
    return isinstance(data, dict) and bool(data.get("songs"))


# --- Space --------------------------------------------------------------------------------------

def camera_label(instrument):
    instrument = str(instrument or "").upper()
    for key, label in MARS_CAMERA_NAMES.items():
        if key in instrument:
            return label
    return instrument.replace("_", " ").title()


def _dimension(image):
    match = re.fullmatch(r"\(?\s*(\d+)\s*,\s*(\d+)\s*\)?", str((image.get("extended") or {}).get("dimension") or ""))
    return (int(match.group(1)), int(match.group(2))) if match else (0, 0)


def _mast_elevation(image):
    try:
        value = float((image.get("extended") or {}).get("mastEl"))
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _sequence(image):
    match = re.search(r"([A-Z]{4}\d{5})_", str(image.get("imageid") or ""))
    return match.group(1) if match else ""


def scenic_rank(image, mosaics=frozenset()):
    """How good a landscape a raw picture is, smaller first, or None for one never shown: the
    colour mast camera, then the navigation and hazard cameras, full frames only. A mast camera
    frame counts when it belongs to a mosaic (a sequence panning across several azimuths, not a
    close-up held on one target), is the colour frame (EBY) rather than its grey companion (ECM),
    and looks near the horizon, the skyline nearest best."""
    instrument = str((image.get("camera") or {}).get("instrument") or "")
    family = next((index for index, prefix in enumerate(MARS_SCENIC) if instrument.startswith(prefix)), None)
    width, _height = _dimension(image)
    if family is None or width < MARS_MIN_WIDTH:
        return None
    mast = MARS_SCENIC[family] in ("MCZ", "NAVCAM")
    elevation = _mast_elevation(image) if mast else None
    off_horizon = elevation is not None and not MARS_HORIZON[0] <= elevation <= MARS_HORIZON[1]
    held = mast and (instrument[:3], _sequence(image)) not in mosaics
    grey = bool(re.search(r"_\d{3}ECM_", str(image.get("imageid") or "")))
    skyline = round(abs(elevation - MARS_SKYLINE), 1) if elevation is not None else 0.0
    return int(off_horizon), int(held), int(grey), family, skyline


def mosaic_sequences(images):
    """{(camera prefix, sequence)} of the mast camera sequences that pan across 3+ azimuths."""
    azimuths = {}
    for image in images:
        try:
            azimuth = round(float((image.get("extended") or {}).get("mastAz")))
        except (TypeError, ValueError):
            continue
        key = (str((image.get("camera") or {}).get("instrument") or "")[:3], _sequence(image))
        azimuths.setdefault(key, set()).add(azimuth)
    return frozenset(key for key, values in azimuths.items() if len(values) >= 3)


def pick_mars(payload):
    """The best landscape of the last few sols (scenic_rank, the newest on a tie), else the newest
    full-size picture from the most scenic camera."""
    images = [image for image in (payload or {}).get("images") or [] if isinstance(image, dict)
              and (image.get("image_files") or {}).get("medium") and image.get("sample_type") == "Full"]
    if not images:
        return None
    latest_sol = max(int(image.get("sol") or 0) for image in images)
    recent = [image for image in images if int(image.get("sol") or 0) >= latest_sol - MARS_RECENT_SOLS]
    mosaics = mosaic_sequences(recent)
    scenic = [(scenic_rank(image, mosaics), image) for image in recent]
    scenic = [(rank, image) for rank, image in scenic if rank is not None]
    if scenic:
        best = min(scenic, key=lambda pair: (pair[0], -int(pair[1].get("sol") or 0)))[1]
    else:
        def rank(image):
            instrument = str((image.get("camera") or {}).get("instrument") or "")
            order = MARS_CAMERAS.index(instrument) if instrument in MARS_CAMERAS else len(MARS_CAMERAS)
            return order, -int(image.get("sol") or 0)

        best = min(recent or images, key=rank)
    instrument = (best.get("camera") or {}).get("instrument")
    files = best["image_files"]
    return {"sol": int(best.get("sol") or 0), "camera": camera_label(instrument),
            "taken_utc": str(best.get("date_taken_utc") or ""),
            "image": files.get("large") if _dimension(best)[0] >= MARS_MIN_WIDTH and files.get("large") else files["medium"]}


def fetch_mars(context):
    payload = _json(MARS_URL, context, max_bytes=2 * 1024 * 1024,
                    params={"feed": "raw_images", "category": "mars2020", "feedtype": "json", "num": 200,
                            "page": 0, "order": "sol desc", "extended": "sample_type::full,"})
    picked = pick_mars(payload)
    if picked is None:
        raise ValueError("Perseverance listed no picture")
    return picked


def valid_mars(data):
    return isinstance(data, dict) and bool(data.get("image"))


OCEANS = (("北冰洋", lambda lat, lon: lat > 66), ("南大洋", lambda lat, lon: lat < -55),
          ("印度洋", lambda lat, lon: 20 <= lon <= 120 and lat < 25),
          ("大西洋", lambda lat, lon: -70 <= lon <= 20), ("太平洋", lambda lat, lon: True))


def ocean_name(lat, lon):
    return next(name for name, test in OCEANS if test(lat, lon))


def fetch_iss(now, context):
    stamp = int(now.timestamp())
    track = _json(f"{ISS_URL}/positions", context, max_bytes=64 * 1024,
                  params={"timestamps": ",".join(str(stamp + minutes * 60) for minutes in ISS_TRACK_MINUTES),
                          "units": "kilometers"})
    current = _json(ISS_URL, context, max_bytes=16 * 1024)
    try:
        place = _json(f"{ISS_COORDINATES_URL}/{float(current['latitude']):.3f},{float(current['longitude']):.3f}",
                      context, max_bytes=16 * 1024)
        country = str((place or {}).get("country_code") or "").upper()
    except TaskCancelled:
        raise
    except Exception:
        country = ""
    points = [[float(row["latitude"]), float(row["longitude"])] for row in track or []]
    later = [row for row, minutes in zip(points, ISS_TRACK_MINUTES) if minutes > 0]
    return {"lat": float(current["latitude"]), "lon": float(current["longitude"]), "country": country,
            "altitude": float(current.get("altitude") or 0), "velocity": float(current.get("velocity") or 0),
            "daylight": current.get("visibility") == "daylight",
            "ascending": (later[0][0] >= float(current["latitude"])) if later else True,
            "time": now.isoformat(), "track": points}


def _julian_centuries(now):
    return (now.timestamp() - 946728000) / 86400 / 36525  # since J2000.0 (2000-01-01 12:00 TT ~ UTC)


def heliocentric(planet, now):
    """A planet's ecliptic position (x, y, z) in AU from its mean orbital elements."""
    base, rate = ORBITS[planet]
    t = _julian_centuries(now)
    a, e, inclination, mean_longitude, perihelion, node = (b + r * t for b, r in zip(base, rate))
    anomaly = math.radians((mean_longitude - perihelion + 180) % 360 - 180)
    eccentric = anomaly
    for _ in range(8):
        eccentric -= (eccentric - e * math.sin(eccentric) - anomaly) / (1 - e * math.cos(eccentric))
    x_orbit, y_orbit = a * (math.cos(eccentric) - e), a * math.sqrt(1 - e * e) * math.sin(eccentric)
    w, o, i = (math.radians(value) for value in (perihelion - node, node, inclination))
    x = ((math.cos(w) * math.cos(o) - math.sin(w) * math.sin(o) * math.cos(i)) * x_orbit
         + (-math.sin(w) * math.cos(o) - math.cos(w) * math.sin(o) * math.cos(i)) * y_orbit)
    y = ((math.cos(w) * math.sin(o) + math.sin(w) * math.cos(o) * math.cos(i)) * x_orbit
         + (-math.sin(w) * math.sin(o) + math.cos(w) * math.cos(o) * math.cos(i)) * y_orbit)
    z = math.sin(w) * math.sin(i) * x_orbit + math.cos(w) * math.sin(i) * y_orbit
    return x, y, z


def mars_distance_km(now):
    """How far Mars is from the Earth now, to within about one per cent."""
    earth, mars = heliocentric("earth", now), heliocentric("mars", now)
    return math.dist(earth, mars) * AU_KM


def subsolar_point(now):
    """(lat, lon) where the Sun is overhead now (declination and equation of time, to ~0.1 deg)."""
    utc = now.astimezone(timezone.utc)
    day = utc.timetuple().tm_yday + (utc.hour + utc.minute / 60) / 24
    angle = 2 * math.pi / 365 * (day - 1)
    declination = (0.006918 - 0.399912 * math.cos(angle) + 0.070257 * math.sin(angle)
                   - 0.006758 * math.cos(2 * angle) + 0.000907 * math.sin(2 * angle)
                   - 0.002697 * math.cos(3 * angle) + 0.00148 * math.sin(3 * angle))
    equation = 229.18 * (0.000075 + 0.001868 * math.cos(angle) - 0.032077 * math.sin(angle)
                         - 0.014615 * math.cos(2 * angle) - 0.040849 * math.sin(2 * angle))
    minutes = utc.hour * 60 + utc.minute + utc.second / 60
    longitude = -(minutes + equation - 720) / 4
    return math.degrees(declination), (longitude + 180) % 360 - 180


def sun_elevation(lat, lon, sun):
    """The Sun's elevation in degrees seen from (lat, lon), given the subsolar point."""
    lat, lon, slat, slon = (math.radians(value) for value in (lat, lon, *sun))
    cosine = math.sin(lat) * math.sin(slat) + math.cos(lat) * math.cos(slat) * math.cos(lon - slon)
    return math.degrees(math.asin(max(-1.0, min(1.0, cosine))))


def iss_sunlit(lat, lon, altitude_km, sun):
    """Whether the ISS is in sunlight: the Sun clears its horizon, lowered by its height."""
    dip = math.degrees(math.acos(EARTH_RADIUS_KM / (EARTH_RADIUS_KM + max(0.0, altitude_km))))
    return sun_elevation(lat, lon, sun) > -dip


def iss_ground_track(lat, lon, ascending, minutes):
    """[(minute, lat, lon)] of the ISS's ground track for a circular orbit through its position now."""
    inclination = math.radians(ISS_INCLINATION)
    ratio = max(-1.0, min(1.0, math.sin(math.radians(lat)) / math.sin(inclination)))
    argument = math.asin(ratio) if ascending else math.pi - math.asin(ratio)

    def from_node(u):
        return math.degrees(math.atan2(math.cos(inclination) * math.sin(u), math.cos(u)))

    node = lon - from_node(argument)
    points = []
    for minute in minutes:
        u = argument + 2 * math.pi * minute / ISS_PERIOD_MIN
        point_lat = math.degrees(math.asin(math.sin(inclination) * math.sin(u)))
        point_lon = (node + from_node(u) - EARTH_DEG_PER_MIN * minute + 180) % 360 - 180
        points.append((minute, point_lat, point_lon))
    return points


def iss_next_switch(iss, now, horizon=100):
    """(minutes, entering_daylight) to the ISS's next sunrise or sunset along its track, or None."""
    sun = subsolar_point(now)
    ascending = iss_ascending(iss)
    altitude = float(iss.get("altitude") or 420)
    lit = iss_sunlit(iss["lat"], iss["lon"], altitude, sun)
    for minute, lat, lon in iss_ground_track(iss["lat"], iss["lon"], ascending, range(1, horizon + 1)):
        moment_sun = subsolar_point(now + timedelta(minutes=minute))
        if iss_sunlit(lat, lon, altitude, moment_sun) != lit:
            return minute, not lit
    return None


def iss_ascending(iss):
    """Whether the ISS is heading north: from its stored flag, else its next track point."""
    if "ascending" in iss:
        return bool(iss["ascending"])
    track = iss.get("track") or []
    later = track[len(track) // 2] if track else None
    return later is None or later[0] >= iss["lat"]


def valid_iss(data):
    return isinstance(data, dict) and "lat" in data and "lon" in data


def wiki_thumbnail(url, width=120):
    """A Wikimedia Commons file's thumbnail at ``width`` pixels (other URLs as they are)."""
    parts = urlsplit(str(url or ""))
    match = re.fullmatch(r"/wikipedia/commons/(\w)/(\w\w)/([^/]+\.(?:jpe?g|png))", parts.path, re.IGNORECASE)
    if parts.hostname != "upload.wikimedia.org" or not match:
        return str(url or "")
    first, second, name = match.groups()
    return f"https://upload.wikimedia.org/wikipedia/commons/thumb/{first}/{second}/{name}/{width}px-{name}"


def chinese_number(number):
    """23 -> '二十三' (1 to 99)."""
    tens, ones = divmod(int(number), 10)
    if not tens:
        return CHINESE_DIGITS[ones]
    return (CHINESE_DIGITS[tens] if tens > 1 else "") + "十" + (CHINESE_DIGITS[ones] if ones else "")


def craft_name(craft):
    """'Shenzhou 23' -> '神舟二十三号'; 'Soyuz MS-29' -> '联盟 MS-29'; 'Crew-13 Dragon' -> '龙飞船 Crew-13'."""
    text = " ".join(str(craft or "").split())
    match = re.fullmatch(r"Shenzhou[- ]?(\d{1,2})", text, re.IGNORECASE)
    if match:
        return f"神舟{chinese_number(match.group(1))}号"
    match = re.fullmatch(r"Soyuz\s+(.+)", text, re.IGNORECASE)
    if match:
        return f"联盟 {match.group(1)}"
    match = re.fullmatch(r"(.+?)\s+Dragon", text, re.IGNORECASE)
    if match:
        return f"龙飞船 {match.group(1)}"
    return text.replace("Starliner", "星际客机")[:24]


def wiki_title(url):
    """'https://en.wikipedia.org/wiki/Luke_Delaney_(astronaut)' -> 'Luke Delaney (astronaut)'."""
    parts = urlsplit(str(url or ""))
    if parts.hostname != "en.wikipedia.org" or not parts.path.startswith("/wiki/"):
        return ""
    return unquote(parts.path[len("/wiki/"):]).replace("_", " ")[:100]


def short_chinese_name(title):
    """'张志远 (航天员)' -> '张志远'; '安娜·基金娜' -> '基金娜' (a transliterated name keeps its surname)."""
    name = re.sub(r"\s*[(（].*?[)）]\s*", "", str(title or "")).strip()
    return name.split("·")[-1].strip()[:6] if name else ""


def add_chinese_names(people, context):
    """Each crew member's Chinese name from Wikipedia: the zh article their English one links to,
    in its simplified title. Best effort: a missing name keeps the English one."""
    crew = [person for station in people.get("stations") or [] for person in station.get("crew") or []
            if person.get("wiki")]
    if not crew:
        return people
    pages = _json(WIKI_API["en"], context, max_bytes=256 * 1024, params={
        "action": "query", "prop": "langlinks", "lllang": "zh", "redirects": 1, "format": "json", "formatversion": 2,
        "titles": "|".join(person["wiki"] for person in crew)})
    query = (pages or {}).get("query") or {}
    renamed = {row.get("from"): row.get("to") for row in (query.get("normalized") or []) + (query.get("redirects") or [])}
    linked = {page.get("title"): ((page.get("langlinks") or [{}])[0]).get("title") for page in query.get("pages") or []}
    zh_titles = {title: linked.get(renamed.get(title, title)) for title in (person["wiki"] for person in crew)}
    wanted = sorted({title for title in zh_titles.values() if title})
    simplified = {}
    if wanted:
        variants = _json(WIKI_API["zh"], context, max_bytes=256 * 1024, params={
            "action": "query", "prop": "info", "inprop": "varianttitles", "format": "json", "formatversion": 2,
            "titles": "|".join(wanted)})
        for page in ((variants or {}).get("query") or {}).get("pages") or []:
            simplified[page.get("title")] = (page.get("varianttitles") or {}).get("zh-cn") or page.get("title")
    for person in crew:
        title = zh_titles.get(person["wiki"])
        if title:
            person["zh_name"] = short_chinese_name(simplified.get(title, title))
    return people


def _station(person):
    if person.get("iss"):
        return "iss"
    craft = str(person.get("spacecraft") or "")
    return "tiangong" if craft.lower().startswith("shenzhou") else craft


def parse_people(payload):
    """Everyone in orbit by station, Tiangong and the ISS first: {total, stations: [{station,
    launched (the crew's first launch), expedition (the ISS's), crew: [{name, country, commander,
    image}]}]}."""
    stations = {}
    for person in (payload or {}).get("people") or []:
        if not isinstance(person, dict) or not person.get("name"):
            continue
        key = _station(person)
        station = stations.setdefault(key, {"station": key, "launched": 0, "crew": []})
        launched = int(person.get("launched") or 0)
        if launched and (not station["launched"] or launched < station["launched"]):
            station["launched"] = launched
        position = str(person.get("position") or "").strip()
        station["crew"].append({"name": str(person["name"]), "country": str(person.get("flag_code") or "")[:2].lower(),
                                "commander": "commander" in position.lower(),
                                "role": CREW_ROLES.get(position.lower(), position[:16]),
                                "craft": craft_name(person.get("spacecraft")),
                                "wiki": wiki_title(person.get("url")),
                                "image": wiki_thumbnail(person.get("image"))})
    order = {"tiangong": 0, "iss": 1}
    rows = sorted(stations.values(), key=lambda row: (order.get(row["station"], 2), row["station"]))
    for row in rows:
        row["crew"].sort(key=lambda person: not person["commander"])
        if row["station"] == "iss" and str((payload or {}).get("iss_expedition") or "").isdigit():
            row["expedition"] = int(payload["iss_expedition"])
    return {"total": sum(len(row["crew"]) for row in rows), "stations": rows}


def fetch_people(context):
    people = parse_people(_json(PEOPLE_URL, context, max_bytes=128 * 1024))
    try:
        return add_chinese_names(people, context)
    except TaskCancelled:
        raise
    except Exception:
        return people


def valid_people(data):
    return isinstance(data, dict) and bool(data.get("stations"))
