from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from zoneinfo import ZoneInfo
import hashlib
import json
import logging
import math
import os
import re
import time

import requests
from PIL import Image, ImageDraw, ImageFont

from plugins.base_plugin.base_plugin import BasePlugin
from plugins.context_cache import write_context
from utils.app_utils import get_font

logger = logging.getLogger(__name__)

PLUGIN_DIR = Path(__file__).resolve().parent
CACHE_SCHEMA_VERSION = "flight-radar-multisource-v1"

DEFAULT_LATITUDE = 37.6213
DEFAULT_LONGITUDE = -122.3790
DEFAULT_CENTER_LABEL = "SFO"
DEFAULT_RADIUS_NM = 160
DEFAULT_DISPLAY_RADIUS_NM = 20
DEFAULT_MAX_AIRCRAFT = 90
DEFAULT_LOCAL_MAP_IMAGE = "sfo_bay_map_crop.png"
DEFAULT_ROUTE_CACHE_SECONDS = 1800
DEFAULT_ROUTE_LOOKUP_LIMIT = 12
ROUTE_CACHE_SCHEMA_VERSION = "flight-radar-routes-v2"
TRACK_HISTORY_SCHEMA_VERSION = "flight-radar-track-history-v1"
DEFAULT_TRACK_HISTORY_SECONDS = 45 * 60
DEFAULT_TRACK_HISTORY_POINTS = 6
DEFAULT_SOURCE_ORDER = "\n".join(
    [
        "adsb_lol",
        "airplanes_live",
        "opensky",
        "flightaware",
        "rapidapi_custom",
    ]
)

MATERIAL_FLIGHT_ICON_POINTS = (
    (21.0, 16.0),
    (21.0, 14.0),
    (13.0, 9.0),
    (13.0, 3.5),
    (12.9, 2.7),
    (12.3, 2.1),
    (11.5, 2.0),
    (10.7, 2.1),
    (10.1, 2.7),
    (10.0, 3.5),
    (10.0, 9.0),
    (2.0, 14.0),
    (2.0, 16.0),
    (10.0, 13.5),
    (10.0, 19.0),
    (8.0, 20.5),
    (8.0, 22.0),
    (11.5, 21.0),
    (15.0, 22.0),
    (15.0, 20.5),
    (13.0, 19.0),
    (13.0, 13.5),
)

ADSB_LOL_POINT_URL = "https://api.adsb.lol/v2/point/{lat:.4f}/{lon:.4f}/{radius:.0f}"
AIRPLANES_LIVE_POINT_URL = "https://api.airplanes.live/v2/point/{lat:.4f}/{lon:.4f}/{radius:.0f}"
OPENSKY_STATES_URL = "https://opensky-network.org/api/states/all"
FLIGHTAWARE_POSITIONS_URL = "https://aeroapi.flightaware.com/aeroapi/flights/search/positions"
GOOGLE_STATIC_MAPS_URL = "https://maps.googleapis.com/maps/api/staticmap"
AIRPING_ROUTESET_URL = "https://api.airping.app/routeset"
ADSB_LOL_ROUTESET_URL = "https://api.adsb.lol/api/0/routeset"

SOURCE_LABELS = {
    "adsb_lol": "ADSB.lol",
    "airplanes_live": "Airplanes",
    "opensky": "OpenSky",
    "flightaware": "FlightAware",
    "rapidapi_custom": "RapidAPI",
}

HTTP_HEADERS = {
    "User-Agent": "InkyPi-SkyRadar/1.0 (+https://github.com/aceisace/InkyPi)",
    "Accept": "application/json,text/plain;q=0.9,*/*;q=0.5",
}

SANS_FONT_PATHS = {
    "normal": (
        r"C:\Windows\Fonts\segoeui.ttf",
        r"C:\Windows\Fonts\arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    ),
    "bold": (
        r"C:\Windows\Fonts\segoeuib.ttf",
        r"C:\Windows\Fonts\arialbd.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
    ),
}

CITY_FONT_PATHS = {
    "normal": (
        PLUGIN_DIR / "fonts" / "msyh.ttc",
        PLUGIN_DIR / "fonts" / "msyh.ttf",
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\msyhl.ttc",
        "/usr/share/fonts/opentype/microsoft/msyh.ttc",
        PLUGIN_DIR.parent / "live_radar" / "fonts" / "NotoSansSC-VF.ttf",
        PLUGIN_DIR.parent / "steam_charts" / "fonts" / "NotoSansSC-VF.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    ),
    "bold": (
        PLUGIN_DIR / "fonts" / "msyhbd.ttc",
        PLUGIN_DIR / "fonts" / "msyh.ttc",
        PLUGIN_DIR / "fonts" / "msyh.ttf",
        r"C:\Windows\Fonts\msyhbd.ttc",
        r"C:\Windows\Fonts\msyh.ttc",
        "/usr/share/fonts/opentype/microsoft/msyhbd.ttc",
        "/usr/share/fonts/opentype/microsoft/msyh.ttc",
        PLUGIN_DIR.parent / "live_radar" / "fonts" / "NotoSansSC-VF.ttf",
        PLUGIN_DIR.parent / "steam_charts" / "fonts" / "NotoSansSC-VF.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
    ),
}

CITY_NAME_ZH_CN = {
    "ALBUQUERQUE": "阿尔伯克基",
    "ANCHORAGE": "安克雷奇",
    "ATLANTA": "亚特兰大",
    "AUSTIN": "奥斯汀",
    "AUCKLAND": "奥克兰",
    "BALTIMORE": "巴尔的摩",
    "BANGKOK": "曼谷",
    "BARCELONA": "巴塞罗那",
    "BEIJING": "北京",
    "BOSTON": "波士顿",
    "BURBANK": "伯班克",
    "CHARLOTTE": "夏洛特",
    "CHICAGO": "芝加哥",
    "CLEVELAND": "克利夫兰",
    "COLUMBUS": "哥伦布",
    "DALLAS": "达拉斯",
    "DALLAS FORT WORTH": "达拉斯",
    "DENVER": "丹佛",
    "DETROIT": "底特律",
    "DOHA": "多哈",
    "DUBAI": "迪拜",
    "FREMONT": "弗里蒙特",
    "HONOLULU": "檀香山",
    "HONG KONG": "香港",
    "HOUSTON": "休斯敦",
    "INDIANAPOLIS": "印第安纳波利斯",
    "ISTANBUL": "伊斯坦布尔",
    "KAHULUI": "卡胡卢伊",
    "KANSAS CITY": "堪萨斯城",
    "KONA": "科纳",
    "LAS VEGAS": "拉斯维加斯",
    "LIHUE": "利胡埃",
    "LONDON": "伦敦",
    "LOS ANGELES": "洛杉矶",
    "MADRID": "马德里",
    "MANILA": "马尼拉",
    "MELBOURNE": "墨尔本",
    "MEXICO CITY": "墨西哥城",
    "MIAMI": "迈阿密",
    "MINNEAPOLIS": "明尼阿波利斯",
    "MONTREAL": "蒙特利尔",
    "MOUNTAIN VIEW": "芒廷维尤",
    "MUNICH": "慕尼黑",
    "NASHVILLE": "纳什维尔",
    "NEW ORLEANS": "新奥尔良",
    "NEW YORK": "纽约",
    "NEWARK": "纽瓦克",
    "OAKLAND": "奥克兰",
    "ORLANDO": "奥兰多",
    "PALM SPRINGS": "棕榈泉",
    "PANAMA CITY": "巴拿马城",
    "PARIS": "巴黎",
    "PAPEETE": "帕皮提",
    "PHILADELPHIA": "费城",
    "PHOENIX": "凤凰城",
    "PITTSBURGH": "匹兹堡",
    "PORTLAND": "波特兰",
    "RENO": "里诺",
    "ROME": "罗马",
    "SACRAMENTO": "萨克拉门托",
    "SALT LAKE CITY": "盐湖城",
    "SAN ANTONIO": "圣安东尼奥",
    "SAN DIEGO": "圣迭戈",
    "SAN FRANCISCO": "旧金山",
    "SAN JOSE": "圣何塞",
    "SAN JOSE CA": "圣何塞",
    "SANTA ANA": "圣安娜",
    "SEATTLE": "西雅图",
    "SEOUL": "首尔",
    "SHANGHAI": "上海",
    "SINGAPORE": "新加坡",
    "ST LOUIS": "圣路易斯",
    "ST. LOUIS": "圣路易斯",
    "SYDNEY": "悉尼",
    "TAIPEI": "台北",
    "TAMPA": "坦帕",
    "TIANJIN": "天津",
    "TOCUMEN": "巴拿马城",
    "TOKYO": "东京",
    "TORONTO": "多伦多",
    "PRAGUE": "布拉格",
    "VANCOUVER": "温哥华",
    "WASHINGTON": "华盛顿",
    "ZURICH": "苏黎世",
}

AIRPORT_CITY_ZH_CN = {
    "ABQ": "阿尔伯克基",
    "ANC": "安克雷奇",
    "ATL": "亚特兰大",
    "AUS": "奥斯汀",
    "BCN": "巴塞罗那",
    "BNA": "纳什维尔",
    "BOS": "波士顿",
    "BUR": "伯班克",
    "BWI": "巴尔的摩",
    "CDG": "巴黎",
    "CLE": "克利夫兰",
    "CLT": "夏洛特",
    "CMH": "哥伦布",
    "CUN": "坎昆",
    "DAL": "达拉斯",
    "DCA": "华盛顿",
    "DEN": "丹佛",
    "DFW": "达拉斯",
    "DOH": "多哈",
    "DTW": "底特律",
    "DXB": "迪拜",
    "EWR": "纽瓦克",
    "FCO": "罗马",
    "FLL": "劳德代尔堡",
    "FRA": "法兰克福",
    "GDL": "瓜达拉哈拉",
    "HKG": "香港",
    "HNL": "檀香山",
    "HND": "东京",
    "HOU": "休斯敦",
    "IAD": "华盛顿",
    "IAH": "休斯敦",
    "ICN": "首尔",
    "IND": "印第安纳波利斯",
    "IST": "伊斯坦布尔",
    "JFK": "纽约",
    "KIX": "大阪",
    "KOA": "科纳",
    "LAS": "拉斯维加斯",
    "LAX": "洛杉矶",
    "LGA": "纽约",
    "LHR": "伦敦",
    "LIH": "利胡埃",
    "MAD": "马德里",
    "MCI": "堪萨斯城",
    "MCO": "奥兰多",
    "MEL": "墨尔本",
    "MEX": "墨西哥城",
    "MIA": "迈阿密",
    "MNL": "马尼拉",
    "MSP": "明尼阿波利斯",
    "MSY": "新奥尔良",
    "MUC": "慕尼黑",
    "NGO": "名古屋",
    "NRT": "东京",
    "OAK": "奥克兰",
    "OGG": "卡胡卢伊",
    "ONT": "安大略",
    "ORD": "芝加哥",
    "PPT": "帕皮提",
    "PDX": "波特兰",
    "PEK": "北京",
    "PHL": "费城",
    "PHX": "凤凰城",
    "PIT": "匹兹堡",
    "PKX": "北京",
    "PRG": "布拉格",
    "PSP": "棕榈泉",
    "PTY": "巴拿马城",
    "PVG": "上海",
    "RNO": "里诺",
    "SAN": "圣迭戈",
    "SAT": "圣安东尼奥",
    "SEA": "西雅图",
    "SFO": "旧金山",
    "SIN": "新加坡",
    "SJC": "圣何塞",
    "SLC": "盐湖城",
    "SMF": "萨克拉门托",
    "SNA": "圣安娜",
    "STL": "圣路易斯",
    "SYD": "悉尼",
    "TSN": "天津",
    "TPE": "台北",
    "YVR": "温哥华",
    "YYZ": "多伦多",
    "ZRH": "苏黎世",
    "YUL": "蒙特利尔",
}


@dataclass
class SourceStatus:
    key: str
    label: str
    state: str
    count: int = 0
    elapsed_ms: int = 0
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "state": self.state,
            "count": self.count,
            "elapsed_ms": self.elapsed_ms,
            "detail": self.detail,
        }


class FlightRadar(BasePlugin):
    def generate_settings_template(self):
        params = super().generate_settings_template()
        params["style_settings"] = False
        params["api_key"] = {
            "required": False,
            "service": "Optional FlightAware or RapidAPI",
            "expected_key": "FLIGHTAWARE_API_KEY or RAPIDAPI_KEY",
        }
        return params

    def generate_image(self, settings, device_config):
        settings = settings or {}
        dimensions = device_config.get_resolution()
        if device_config.get_config("orientation") == "vertical":
            dimensions = dimensions[::-1]

        now = self._now(device_config)
        snapshot = self._get_snapshot(settings, device_config, now)
        self._write_radar_context(snapshot, now)
        return self._render(snapshot, dimensions, settings, now, device_config)

    def _get_snapshot(self, settings, device_config, now):
        lat = self._float_setting(settings, "latitude", DEFAULT_LATITUDE, -90.0, 90.0)
        lon = self._float_setting(settings, "longitude", DEFAULT_LONGITUDE, -180.0, 180.0)
        radius_nm = self._int_setting(settings, "radiusNm", DEFAULT_RADIUS_NM, 25, 250)
        max_aircraft = self._int_setting(settings, "maxAircraft", DEFAULT_MAX_AIRCRAFT, 10, 180)
        cache_seconds = self._int_setting(settings, "cacheSeconds", 240, 0, 1800)
        force_refresh = self._bool_setting(settings.get("forceRefresh"), False)
        source_order = self._source_order(settings)
        center_label = self._clean_label(settings.get("centerLabel") or DEFAULT_CENTER_LABEL, 32)
        cache_key = self._cache_key(lat, lon, radius_nm, max_aircraft, source_order)
        cache_file = self._cache_file(cache_key)
        cached = self._read_json(cache_file)
        now_ts = time.time()

        if (
            not force_refresh
            and cache_seconds > 0
            and cached.get("schema") == CACHE_SCHEMA_VERSION
            and now_ts - float(cached.get("fetched_at") or 0) < cache_seconds
            and isinstance(cached.get("snapshot"), dict)
        ):
            snapshot = dict(cached["snapshot"])
            snapshot["from_cache"] = True
            snapshot["warning"] = "CACHE"
            snapshot["statuses"] = [
                SourceStatus("cache", "Cache", "cache", len(snapshot.get("aircraft") or [])).to_dict()
            ]
            return snapshot

        try:
            aircraft, source_key, statuses = self._fetch_sources(
                settings,
                device_config,
                lat,
                lon,
                radius_nm,
                max_aircraft,
                source_order,
            )
            self._enrich_routes(aircraft, settings, device_config, lat, lon)
            self._attach_track_history(aircraft, settings, lat, lon, radius_nm)
            snapshot = {
                "schema": CACHE_SCHEMA_VERSION,
                "center": {"lat": lat, "lon": lon, "label": center_label},
                "radius_nm": radius_nm,
                "aircraft": aircraft,
                "source": source_key,
                "source_label": SOURCE_LABELS.get(source_key, source_key),
                "statuses": [status.to_dict() for status in statuses],
                "generated_at": now.isoformat(),
                "from_cache": False,
                "warning": "",
            }
            self._write_json(cache_file, {"schema": CACHE_SCHEMA_VERSION, "fetched_at": now_ts, "snapshot": snapshot})
            return snapshot
        except Exception as exc:
            logger.warning("FlightRadar all sources failed: %s", exc)
            if cached.get("schema") == CACHE_SCHEMA_VERSION and isinstance(cached.get("snapshot"), dict):
                snapshot = dict(cached["snapshot"])
                snapshot["from_cache"] = True
                snapshot["warning"] = "STALE CACHE"
                snapshot["statuses"] = (snapshot.get("statuses") or []) + [
                    SourceStatus("all", "All sources", "failed", detail=str(exc)[:80]).to_dict()
                ]
                return snapshot

            return {
                "schema": CACHE_SCHEMA_VERSION,
                "center": {"lat": lat, "lon": lon, "label": center_label},
                "radius_nm": radius_nm,
                "aircraft": [],
                "source": "none",
                "source_label": "No source",
                "statuses": [SourceStatus("all", "All sources", "failed", detail=str(exc)[:80]).to_dict()],
                "generated_at": now.isoformat(),
                "from_cache": False,
                "warning": "NO DATA",
            }

    def _fetch_sources(self, settings, device_config, lat, lon, radius_nm, max_aircraft, source_order):
        statuses: list[SourceStatus] = []
        last_error = ""

        for source_key in source_order:
            started = time.time()
            label = SOURCE_LABELS.get(source_key, source_key)
            try:
                aircraft = self._fetch_source(settings, device_config, source_key, lat, lon, radius_nm)
                aircraft = self._rank_aircraft(aircraft, lat, lon)[:max_aircraft]
                elapsed = int((time.time() - started) * 1000)
                if aircraft:
                    statuses.append(SourceStatus(source_key, label, "ok", len(aircraft), elapsed))
                    return aircraft, source_key, statuses
                statuses.append(SourceStatus(source_key, label, "empty", 0, elapsed, "no aircraft"))
            except _SkippedSource as exc:
                elapsed = int((time.time() - started) * 1000)
                statuses.append(SourceStatus(source_key, label, "skipped", 0, elapsed, str(exc)[:80]))
            except Exception as exc:
                elapsed = int((time.time() - started) * 1000)
                last_error = str(exc)
                statuses.append(SourceStatus(source_key, label, "failed", 0, elapsed, last_error[:80]))
                logger.warning("FlightRadar source %s failed: %s", source_key, exc)

        raise RuntimeError(last_error or "No configured source returned aircraft.")

    def _fetch_source(self, settings, device_config, source_key, lat, lon, radius_nm):
        timeout = self._int_setting(settings, "timeoutSeconds", 10, 4, 25)
        if source_key == "adsb_lol":
            url = ADSB_LOL_POINT_URL.format(lat=lat, lon=lon, radius=radius_nm)
            return self._fetch_readsb_point(url, "adsb_lol", lat, lon, timeout)
        if source_key == "airplanes_live":
            url = AIRPLANES_LIVE_POINT_URL.format(lat=lat, lon=lon, radius=radius_nm)
            return self._fetch_readsb_point(url, "airplanes_live", lat, lon, timeout)
        if source_key == "opensky":
            return self._fetch_opensky(device_config, lat, lon, radius_nm, timeout)
        if source_key == "flightaware":
            return self._fetch_flightaware(settings, device_config, lat, lon, radius_nm, timeout)
        if source_key == "rapidapi_custom":
            return self._fetch_rapidapi_custom(settings, device_config, lat, lon, radius_nm, timeout)
        raise _SkippedSource("unknown source")

    def _fetch_readsb_point(self, url, source_key, center_lat, center_lon, timeout):
        response = self._session().get(url, headers=HTTP_HEADERS, timeout=(4, timeout))
        response.raise_for_status()
        data = response.json()
        raw_aircraft = data.get("ac") or data.get("aircraft") or data.get("data") or []
        if not isinstance(raw_aircraft, list):
            raise RuntimeError("unexpected aircraft payload")

        aircraft = []
        for item in raw_aircraft:
            normalized = self._normalize_readsb_aircraft(item, source_key, center_lat, center_lon)
            if normalized:
                aircraft.append(normalized)
        return aircraft

    def _fetch_opensky(self, device_config, lat, lon, radius_nm, timeout):
        lat_delta = radius_nm / 60.0
        lon_delta = radius_nm / max(12.0, 60.0 * math.cos(math.radians(lat)))
        params = {
            "lamin": f"{lat - lat_delta:.4f}",
            "lamax": f"{lat + lat_delta:.4f}",
            "lomin": f"{lon - lon_delta:.4f}",
            "lomax": f"{lon + lon_delta:.4f}",
        }
        auth = None
        username = self._load_env(device_config, "OPENSKY_USERNAME")
        password = self._load_env(device_config, "OPENSKY_PASSWORD")
        if username and password:
            auth = (username, password)

        response = self._session().get(
            OPENSKY_STATES_URL,
            params=params,
            headers=HTTP_HEADERS,
            timeout=(4, timeout),
            auth=auth,
        )
        response.raise_for_status()
        data = response.json()
        states = data.get("states") or []
        if not isinstance(states, list):
            raise RuntimeError("unexpected OpenSky payload")

        aircraft = []
        for state in states:
            normalized = self._normalize_opensky_state(state, lat, lon, radius_nm)
            if normalized:
                aircraft.append(normalized)
        return aircraft

    def _fetch_flightaware(self, settings, device_config, lat, lon, radius_nm, timeout):
        api_key = self._load_env(device_config, "FLIGHTAWARE_API_KEY")
        if not api_key:
            raise _SkippedSource("missing FLIGHTAWARE_API_KEY")
        query = str(settings.get("flightawareQuery") or "").strip()
        if not query:
            raise _SkippedSource("missing FlightAware query")

        response = self._session().get(
            FLIGHTAWARE_POSITIONS_URL,
            params={"query": query, "max_pages": 1},
            headers={**HTTP_HEADERS, "x-apikey": api_key},
            timeout=(4, timeout),
        )
        response.raise_for_status()
        records = self._extract_generic_records(response.json())
        return self._normalize_generic_records(records, "flightaware", lat, lon, radius_nm)

    def _fetch_rapidapi_custom(self, settings, device_config, lat, lon, radius_nm, timeout):
        api_key = self._load_env(device_config, "RAPIDAPI_KEY") or self._load_env(device_config, "X_RAPIDAPI_KEY")
        url = str(settings.get("rapidapiUrl") or "").strip()
        host = str(settings.get("rapidapiHost") or "").strip()
        if not api_key:
            raise _SkippedSource("missing RAPIDAPI_KEY")
        if not url:
            raise _SkippedSource("missing RapidAPI URL")

        params = {
            "lat": f"{lat:.4f}",
            "lon": f"{lon:.4f}",
            "radius": str(radius_nm),
            "radius_nm": str(radius_nm),
        }
        params.update(self._parse_key_value_lines(settings.get("rapidapiParams") or ""))
        headers = {**HTTP_HEADERS, "X-RapidAPI-Key": api_key}
        if host:
            headers["X-RapidAPI-Host"] = host

        response = self._session().get(url, params=params, headers=headers, timeout=(4, timeout))
        response.raise_for_status()
        records = self._extract_generic_records(response.json())
        return self._normalize_generic_records(records, "rapidapi_custom", lat, lon, radius_nm)

    def _normalize_readsb_aircraft(self, item, source_key, center_lat, center_lon):
        if not isinstance(item, dict):
            return None
        lat = self._number(item.get("lat"))
        lon = self._number(item.get("lon"))
        if lat is None or lon is None:
            return None
        seen = self._number(item.get("seen") if item.get("seen") is not None else item.get("seen_pos"))
        if seen is not None and seen > 300:
            return None

        altitude_ft, on_ground = self._readsb_altitude(item)
        speed_kt = self._number(item.get("gs") or item.get("ias") or item.get("tas"))
        track = self._number(item.get("track") or item.get("true_heading") or item.get("mag_heading"))
        hex_id = self._clean_label(item.get("hex") or item.get("icao") or item.get("icao24") or "", 12).upper()
        callsign = self._clean_label(item.get("flight") or item.get("r") or hex_id or "UNKNOWN", 14).upper()
        distance_nm = self._number(item.get("r_dst"))
        if distance_nm is None:
            distance_nm = self._distance_nm(center_lat, center_lon, lat, lon)

        return {
            "callsign": callsign,
            "hex": hex_id,
            "lat": lat,
            "lon": lon,
            "altitude_ft": altitude_ft,
            "speed_kt": speed_kt,
            "track": track,
            "distance_nm": distance_nm,
            "on_ground": on_ground,
            "type": self._clean_label(item.get("t") or item.get("desc") or "", 18).upper(),
            "registration": self._clean_label(item.get("r") or "", 12).upper(),
            "route": "",
            "origin": "",
            "destination": "",
            "route_label": "",
            "origin_city": "",
            "destination_city": "",
            "source": source_key,
        }

    def _normalize_opensky_state(self, state, center_lat, center_lon, radius_nm):
        if not isinstance(state, list) or len(state) < 11:
            return None
        lon = self._number(state[5])
        lat = self._number(state[6])
        if lat is None or lon is None:
            return None
        distance_nm = self._distance_nm(center_lat, center_lon, lat, lon)
        if distance_nm > radius_nm:
            return None

        callsign = self._clean_label(state[1] or state[0] or "UNKNOWN", 14).upper()
        altitude_m = self._number(state[7] if state[7] is not None else (state[13] if len(state) > 13 else None))
        altitude_ft = int(altitude_m * 3.28084) if altitude_m is not None else None
        speed_ms = self._number(state[9])
        track = self._number(state[10])
        on_ground = bool(state[8])

        return {
            "callsign": callsign,
            "hex": self._clean_label(state[0] or "", 12).upper(),
            "lat": lat,
            "lon": lon,
            "altitude_ft": 0 if on_ground else altitude_ft,
            "speed_kt": speed_ms * 1.94384 if speed_ms is not None else None,
            "track": track,
            "distance_nm": distance_nm,
            "on_ground": on_ground,
            "type": "",
            "registration": "",
            "route": "",
            "origin": "",
            "destination": "",
            "route_label": "",
            "origin_city": "",
            "destination_city": "",
            "source": "opensky",
        }

    def _normalize_generic_records(self, records, source_key, center_lat, center_lon, radius_nm):
        aircraft = []
        for record in records:
            if not isinstance(record, dict):
                continue
            lat = self._first_number(record, ["lat", "latitude", "position.lat", "position.latitude", "live.lat", "live.latitude"])
            lon = self._first_number(record, ["lon", "lng", "long", "longitude", "position.lon", "position.lng", "position.longitude", "live.lon", "live.lng", "live.longitude"])
            if lat is None or lon is None:
                continue
            distance_nm = self._distance_nm(center_lat, center_lon, lat, lon)
            if distance_nm > radius_nm:
                continue
            altitude = self._first_number(record, ["altitude_ft", "altitude", "alt", "position.altitude", "live.altitude"])
            speed = self._first_number(record, ["speed_kt", "speed", "groundspeed", "gs", "position.speed", "live.speed"])
            track = self._first_number(record, ["track", "heading", "direction", "course", "position.heading", "live.heading"])
            callsign = self._first_text(record, ["callsign", "ident", "flight", "flight_number", "icao24", "hex"])
            origin = self._first_text(record, ["origin", "origin.iata", "origin.code", "departure.iata", "departure.airport.iata", "dep_iata", "from"])
            destination = self._first_text(record, ["destination", "destination.iata", "destination.code", "arrival.iata", "arrival.airport.iata", "arr_iata", "to"])
            route = self._first_text(record, ["route", "_airport_codes_iata", "airport_codes", "flight.route"])
            origin_city = self._clean_city_name(self._first_text(record, ["origin.city", "departure.city", "departure.airport.city", "from_city", "origin_city"]))
            destination_city = self._clean_city_name(self._first_text(record, ["destination.city", "arrival.city", "arrival.airport.city", "to_city", "destination_city"]))
            route_label = self._clean_route_label(self._first_text(record, ["route_label", "city_route", "flight.route_label"]))
            aircraft.append(
                {
                    "callsign": self._clean_label(callsign or "UNKNOWN", 14).upper(),
                    "hex": self._clean_label(self._first_text(record, ["hex", "icao24", "icao"]) or "", 12).upper(),
                    "lat": lat,
                    "lon": lon,
                    "altitude_ft": altitude,
                    "speed_kt": speed,
                    "track": track,
                    "distance_nm": distance_nm,
                    "on_ground": bool(record.get("on_ground") or record.get("ground")),
                    "type": self._clean_label(self._first_text(record, ["aircraft_type", "type", "model"]) or "", 18).upper(),
                    "registration": self._clean_label(self._first_text(record, ["registration", "reg", "r"]) or "", 12).upper(),
                    "route": self._clean_route(route, origin, destination),
                    "origin": self._clean_airport_code(origin),
                    "destination": self._clean_airport_code(destination),
                    "route_label": route_label,
                    "origin_city": origin_city,
                    "destination_city": destination_city,
                    "source": source_key,
                }
            )
        return aircraft

    def _enrich_routes(self, aircraft, settings, device_config, center_lat, center_lon):
        if not self._bool_setting((settings or {}).get("routeLookupEnabled"), True):
            return
        limit = self._int_setting(settings or {}, "routeLookupLimit", DEFAULT_ROUTE_LOOKUP_LIMIT, 0, 40)
        if limit <= 0:
            return

        cache_seconds = self._int_setting(settings or {}, "routeCacheSeconds", DEFAULT_ROUTE_CACHE_SECONDS, 0, 86400)
        timeout = self._int_setting(settings or {}, "routeTimeoutSeconds", 4, 2, 10)
        now_ts = time.time()
        cache_file = self._route_cache_file()
        cache = self._read_json(cache_file)
        if cache.get("schema") != ROUTE_CACHE_SCHEMA_VERSION or not isinstance(cache.get("routes"), dict):
            cache = {"schema": ROUTE_CACHE_SCHEMA_VERSION, "routes": {}}

        candidates = []
        seen = set()
        for plane in aircraft:
            callsign = self._route_candidate_callsign(plane)
            if not callsign or callsign in seen:
                continue
            seen.add(callsign)
            candidates.append((callsign, plane))
            if len(candidates) >= limit:
                break

        missing = []
        for callsign, plane in candidates:
            cached = cache["routes"].get(callsign)
            if (
                isinstance(cached, dict)
                and cache_seconds > 0
                and now_ts - float(cached.get("fetched_at") or 0) < cache_seconds
            ):
                self._apply_route_info(plane, cached)
            else:
                missing.append((callsign, plane))

        if missing:
            fetched = {}
            for source in self._route_source_order(settings or {}):
                try:
                    fetched = self._fetch_route_source(source, missing, timeout)
                    if fetched:
                        break
                except Exception as exc:
                    logger.debug("FlightRadar route source %s failed: %s", source, exc)

            for callsign, plane in missing:
                route_info = fetched.get(
                    callsign,
                    {
                        "route": "",
                        "origin": "",
                        "destination": "",
                        "route_label": "",
                        "origin_city": "",
                        "destination_city": "",
                    },
                )
                route_info["fetched_at"] = now_ts
                cache["routes"][callsign] = route_info
                self._apply_route_info(plane, route_info)

            self._write_json(cache_file, cache)

    def _fetch_route_source(self, source, missing, timeout):
        url = {
            "airping": AIRPING_ROUTESET_URL,
            "adsb_lol": ADSB_LOL_ROUTESET_URL,
        }.get(source)
        if not url:
            return {}
        planes = []
        for callsign, plane in missing:
            lat = self._number(plane.get("lat")) or DEFAULT_LATITUDE
            lon = self._number(plane.get("lon")) or DEFAULT_LONGITUDE
            planes.append({"callsign": callsign, "lat": lat, "lng": lon})
        if not planes:
            return {}

        response = self._session().post(
            url,
            json={"planes": planes},
            headers={**HTTP_HEADERS, "Accept": "application/json"},
            timeout=(3, timeout),
        )
        response.raise_for_status()
        if not response.text.strip():
            return {}
        data = response.json()
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except Exception:
                return {}
        if isinstance(data, dict):
            data = data.get("planes") or data.get("routes") or data.get("data") or []
        if not isinstance(data, list):
            return {}

        routes = {}
        for item in data:
            if not isinstance(item, dict):
                continue
            callsign = self._clean_route_callsign(item.get("callsign") or item.get("flight") or item.get("ident"))
            if not callsign:
                continue
            origin = self._first_text(item, ["origin", "origin.iata", "departure.iata"])
            destination = self._first_text(item, ["destination", "destination.iata", "arrival.iata"])
            route = self._clean_route(item.get("_airport_codes_iata") or item.get("airport_codes") or item.get("route"), origin, destination)
            if route:
                parts = route.split("-")
                city_info = self._route_city_info(item, route)
                routes[callsign] = {
                    "route": route,
                    "origin": self._clean_airport_code(origin) or parts[0],
                    "destination": self._clean_airport_code(destination) or parts[-1],
                    "route_label": city_info.get("route_label", ""),
                    "origin_city": city_info.get("origin_city", ""),
                    "destination_city": city_info.get("destination_city", ""),
                }
        return routes

    def _attach_track_history(self, aircraft, settings, center_lat, center_lon, radius_nm):
        history_seconds = self._int_setting(settings or {}, "trackHistorySeconds", DEFAULT_TRACK_HISTORY_SECONDS, 300, 7200)
        max_points = self._int_setting(settings or {}, "trackHistoryPoints", DEFAULT_TRACK_HISTORY_POINTS, 2, 12)
        now_ts = time.time()
        cache_file = self._track_history_file()
        cache = self._read_json(cache_file)
        if cache.get("schema") != TRACK_HISTORY_SCHEMA_VERSION or not isinstance(cache.get("tracks"), dict):
            cache = {"schema": TRACK_HISTORY_SCHEMA_VERSION, "tracks": {}}

        tracks = cache["tracks"]
        cutoff = now_ts - history_seconds
        active_keys = set()
        for plane in aircraft:
            key = self._track_key(plane)
            lat = self._number(plane.get("lat"))
            lon = self._number(plane.get("lon"))
            if not key or lat is None or lon is None:
                continue
            active_keys.add(key)
            points = tracks.get(key) if isinstance(tracks.get(key), list) else []
            cleaned = [
                point
                for point in points
                if isinstance(point, dict)
                and (self._number(point.get("t")) or 0) >= cutoff
                and self._number(point.get("lat")) is not None
                and self._number(point.get("lon")) is not None
            ]
            current = {"lat": lat, "lon": lon, "t": now_ts}
            if not cleaned or self._track_distance_nm(cleaned[-1], current, center_lat) >= 0.08:
                cleaned.append(current)
            else:
                cleaned[-1] = current
            cleaned = cleaned[-max_points:]
            tracks[key] = cleaned
            plane["track_points"] = cleaned

        for key in list(tracks.keys()):
            if key in active_keys:
                continue
            points = tracks.get(key) if isinstance(tracks.get(key), list) else []
            points = [
                point
                for point in points
                if isinstance(point, dict) and (self._number(point.get("t")) or 0) >= cutoff
            ]
            if points:
                tracks[key] = points[-max_points:]
            else:
                tracks.pop(key, None)

        self._write_json(cache_file, cache)

    @staticmethod
    def _track_key(plane):
        hex_id = str((plane or {}).get("hex") or "").strip().upper()
        if hex_id:
            return f"hex:{hex_id}"
        callsign = FlightRadar._clean_route_callsign((plane or {}).get("callsign"))
        return f"flight:{callsign}" if callsign else ""

    @staticmethod
    def _track_distance_nm(first, second, center_lat):
        lat1 = FlightRadar._number((first or {}).get("lat"))
        lon1 = FlightRadar._number((first or {}).get("lon"))
        lat2 = FlightRadar._number((second or {}).get("lat"))
        lon2 = FlightRadar._number((second or {}).get("lon"))
        if lat1 is None or lon1 is None or lat2 is None or lon2 is None:
            return 999.0
        nm_y = (lat2 - lat1) * 60.0
        nm_x = (lon2 - lon1) * 60.0 * math.cos(math.radians(center_lat))
        return math.hypot(nm_x, nm_y)

    @staticmethod
    def _project_track_points(points, center_lat, center_lon, radius_nm, cx, cy, radius_px):
        projected = []
        for point in points:
            if not isinstance(point, dict):
                continue
            projected_point = FlightRadar._project_geo(
                point.get("lat"),
                point.get("lon"),
                center_lat,
                center_lon,
                radius_nm,
                cx,
                cy,
                radius_px,
            )
            if not projected_point:
                continue
            if projected and math.hypot(projected_point[0] - projected[-1][0], projected_point[1] - projected[-1][1]) < 2:
                projected[-1] = projected_point
            else:
                projected.append(projected_point)
        return projected

    @staticmethod
    def _limit_trail_points(points, max_pixels=96):
        if len(points) < 2:
            return points
        limited = [points[-1]]
        total = 0.0
        for point in reversed(points[:-1]):
            last = limited[-1]
            segment = math.hypot(point[0] - last[0], point[1] - last[1])
            if segment <= 0:
                continue
            if segment > max_pixels * 1.7:
                break
            if total + segment > max_pixels:
                remaining = max_pixels - total
                if remaining > 0:
                    ratio = max(0.0, min(1.0, remaining / segment))
                    limited.append(
                        (
                            last[0] + (point[0] - last[0]) * ratio,
                            last[1] + (point[1] - last[1]) * ratio,
                        )
                    )
                break
            limited.append(point)
            total += segment
        return list(reversed(limited))

    def _render(self, snapshot, dimensions, settings, now, device_config=None):
        width, height = dimensions
        theme = self._theme()
        image = Image.new("RGB", dimensions, theme["bg"])
        draw = ImageDraw.Draw(image)
        horizontal = width >= height

        if horizontal:
            self._render_horizontal(draw, image, snapshot, width, height, theme, now, settings, device_config)
        else:
            self._render_vertical(draw, image, snapshot, width, height, theme, now, settings, device_config)
        return image

    def _render_horizontal(self, draw, image, snapshot, width, height, theme, now, settings, device_config=None):
        margin = 16
        top_h = 66
        bottom_h = 38
        radar_box = (margin, top_h + 8, int(width * 0.665), height - bottom_h - 10)
        side_box = (radar_box[2] + 12, top_h + 8, width - margin, height - bottom_h - 10)
        bottom_box = (margin, height - bottom_h + 1, width - margin, height - 10)

        self._draw_header(draw, snapshot, width, theme, now, margin)
        self._draw_radar_panel(draw, image, snapshot, radar_box, theme, settings, device_config)
        self._draw_aircraft_list(draw, snapshot, side_box, theme, max_cards=5)
        self._draw_bottom_legend(draw, snapshot, bottom_box, theme)

    def _render_vertical(self, draw, image, snapshot, width, height, theme, now, settings, device_config=None):
        margin = 14
        top_h = 72
        radar_box = (margin, top_h + 8, width - margin, min(width + top_h - 8, int(height * 0.58)))
        side_box = (margin, radar_box[3] + 12, width - margin, height - 52)
        bottom_box = (margin, height - 42, width - margin, height - 10)

        self._draw_header(draw, snapshot, width, theme, now, margin, compact=True)
        self._draw_radar_panel(draw, image, snapshot, radar_box, theme, settings, device_config)
        self._draw_aircraft_list(draw, snapshot, side_box, theme, max_cards=6)
        self._draw_bottom_legend(draw, snapshot, bottom_box, theme)

    def _draw_header(self, draw, snapshot, width, theme, now, margin, compact=False):
        title_font = self._font(32 if not compact else 27, "bold")
        sub_font = self._font(11 if not compact else 10, "bold")
        small_font = self._font(10, "normal")
        title = "SKY RADAR"
        draw.rectangle((0, 0, width, 66), fill=theme["header_bg"])
        draw.rectangle((0, 62, width, 66), fill=theme["header_rule"])
        draw.text((margin, 11), title, fill=theme["header_ink"], font=title_font)
        source = snapshot.get("source_label") or "No source"
        count = len(snapshot.get("aircraft") or [])
        subtitle = f"{source} / {count} aircraft / {snapshot.get('center', {}).get('label', DEFAULT_CENTER_LABEL)}"
        draw.text((margin + 2, 48), subtitle, fill=theme["header_muted"], font=sub_font)
        ts = now.strftime("%H:%M %Z").strip()
        self._draw_text_fit(draw, ts, (width - margin - 130, 13), 130, small_font, theme["header_muted"], anchor="ra")
        if snapshot.get("warning"):
            self._draw_text_fit(draw, str(snapshot.get("warning")), (width - margin - 130, 31), 130, small_font, theme["amber"], anchor="ra")

        statuses = snapshot.get("statuses") or []
        chip_x = min(width - margin - 408, margin + 218)
        chip_x = max(margin + 180, chip_x)
        chip_y = 16
        for status in statuses[:5]:
            chip_w = 75 if not compact else 58
            if chip_x + chip_w > width - margin:
                break
            self._draw_source_chip(draw, chip_x, chip_y, chip_w, 24, status, theme)
            chip_x += chip_w + 6

        draw.line((margin, 65, width - margin, 65), fill=theme["line"], width=1)

    def _draw_source_chip(self, draw, x, y, w, h, status, theme):
        state = status.get("state", "unknown")
        color = {
            "ok": theme["green"],
            "cache": theme["blue"],
            "empty": theme["amber"],
            "skipped": theme["muted"],
            "failed": theme["red"],
        }.get(state, theme["muted"])
        draw.rounded_rectangle((x, y, x + w, y + h), radius=5, fill=theme["chip_bg"], outline=color, width=1)
        dot_r = 3
        draw.ellipse((x + 7, y + h / 2 - dot_r, x + 7 + dot_r * 2, y + h / 2 + dot_r), fill=color)
        label = str(status.get("label") or status.get("key") or "")[:10]
        font = self._font(9, "bold")
        self._draw_text_fit(draw, label, (x + 16, y + 6), w - 20, font, theme["ink"])

    def _draw_radar_panel(self, draw, image, snapshot, box, theme, settings=None, device_config=None):
        x1, y1, x2, y2 = box
        draw.rounded_rectangle(box, radius=7, fill=theme["panel"], outline=theme["line"], width=1)
        inner = (x1 + 12, y1 + 12, x2 - 12, y2 - 12)
        ix1, iy1, ix2, iy2 = inner
        cx = (ix1 + ix2) / 2
        cy = (iy1 + iy2) / 2
        radius_px = min(ix2 - ix1, iy2 - iy1) / 2 - 14
        center = snapshot.get("center") or {}
        center_lat = self._number(center.get("lat")) or DEFAULT_LATITUDE
        center_lon = self._number(center.get("lon")) or DEFAULT_LONGITUDE
        radius_nm = self._number(snapshot.get("radius_nm")) or DEFAULT_RADIUS_NM
        display_radius_nm = self._display_radius(settings or {}, radius_nm)

        self._draw_map_layer(draw, image, inner, center_lat, center_lon, display_radius_nm, cx, cy, radius_px, theme, settings or {}, device_config)

        aircraft = snapshot.get("aircraft") or []
        plotted = []
        for plane in aircraft:
            point = self._project_plane(plane, center_lat, center_lon, display_radius_nm, cx, cy, radius_px)
            if point:
                plotted.append((plane, point))

        center_label = center.get("label") or DEFAULT_CENTER_LABEL
        trail_overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
        trail_draw = ImageDraw.Draw(trail_overlay)
        for plane, (px, py) in plotted:
            self._draw_plane_trail(
                trail_draw,
                px,
                py,
                plane,
                cx,
                cy,
                center_label,
                center_lat,
                center_lon,
                display_radius_nm,
                radius_px,
                theme,
            )
        if plotted:
            composited = Image.alpha_composite(image.convert("RGBA"), trail_overlay)
            image.paste(composited.convert("RGB"))

        for plane, (px, py) in plotted:
            self._draw_plane_marker(draw, px, py, plane, theme)

        label_font = self._font(10, "bold")
        labels_drawn = 0
        for plane, (px, py) in plotted:
            if labels_drawn >= 8:
                break
            callsign = str(plane.get("callsign") or "").strip()
            if callsign and self._should_label_plane(plane, center_label, cx, cy, px, py):
                self._draw_text_fit(draw, callsign, (px + 7, py - 8), 72, label_font, theme["ink"])
                labels_drawn += 1

        title_font = self._font(12, "bold")
        draw.text((x1 + 16, y1 + 12), "SFO AIRSPACE", fill=theme["muted"], font=title_font)
        range_text = f"{int(display_radius_nm)} NM VIEW"
        self._draw_text_fit(draw, range_text, (x2 - 104, y1 + 12), 88, title_font, theme["ink"], anchor="ra")

        if not plotted:
            empty_font = self._font(18, "bold")
            self._draw_text_fit(draw, "NO AIRCRAFT DATA", (cx - 120, cy - 8), 240, empty_font, theme["muted"], anchor="mm")

    def _draw_map_layer(self, draw, image, inner, center_lat, center_lon, radius_nm, cx, cy, radius_px, theme, settings, device_config=None):
        mode = str(settings.get("mapMode") or "stylized").strip().lower()
        if mode in {"none", "off", "false"}:
            return

        map_image = None
        local_mode = mode in {"local", "local_image", "local_calibrated"}
        if local_mode:
            map_image = self._load_local_map(settings)
        elif mode in {"image", "static_image", "google", "google_static"}:
            map_image = self._load_external_map(settings, device_config, center_lat, center_lon, radius_nm, inner)

        if map_image is not None:
            ix1, iy1, ix2, iy2 = [int(v) for v in inner]
            target = map_image.resize((ix2 - ix1, iy2 - iy1), self._resampling_filter()).convert("RGB")
            shade = Image.new("RGB", target.size, theme["map_shade"])
            target = Image.blend(target, shade, 0.14 if local_mode else 0.22)
            image.paste(target, (ix1, iy1))
        else:
            ix1, iy1, ix2, iy2 = [int(v) for v in inner]
            map_layer = Image.new("RGB", (max(1, ix2 - ix1), max(1, iy2 - iy1)), theme["panel"])
            map_draw = ImageDraw.Draw(map_layer)
            self._draw_stylized_bay_map(
                map_draw,
                (0, 0, ix2 - ix1, iy2 - iy1),
                center_lat,
                center_lon,
                radius_nm,
                cx - ix1,
                cy - iy1,
                radius_px,
                theme,
            )
            image.paste(map_layer, (ix1, iy1))

        ix1, iy1, ix2, iy2 = inner
        draw.rectangle((ix1, iy1, ix2, iy2), outline=theme["map_border"], width=1)

    def _draw_stylized_bay_map(self, draw, inner, center_lat, center_lon, radius_nm, cx, cy, radius_px, theme):
        ix1, iy1, ix2, iy2 = inner
        water = theme["map_water"]
        land_line = theme["map_land_line"]
        city = theme["map_city"]
        road = theme["map_road"]

        coastline = [
            (38.60, -123.35),
            (38.35, -123.05),
            (38.05, -122.86),
            (37.84, -122.60),
            (37.70, -122.52),
            (37.50, -122.48),
            (37.28, -122.42),
            (37.02, -122.23),
            (36.78, -122.04),
            (36.55, -121.93),
        ]
        coast_pts = [
            self._project_geo(lat, lon, center_lat, center_lon, radius_nm, cx, cy, radius_px)
            for lat, lon in coastline
        ]
        coast_pts = [point for point in coast_pts if point]
        if len(coast_pts) >= 2:
            ocean_poly = [(ix1, iy1), (ix1, iy2), *reversed(coast_pts), (ix1, iy1)]
            draw.polygon(ocean_poly, fill=water)
            draw.line(coast_pts, fill=land_line, width=2)

        bay_shapes = [
            [
                (38.12, -122.50),
                (37.96, -122.38),
                (37.83, -122.31),
                (37.68, -122.24),
                (37.52, -122.13),
                (37.39, -122.03),
                (37.43, -121.92),
                (37.62, -122.06),
                (37.78, -122.22),
                (37.94, -122.34),
                (38.10, -122.42),
            ],
            [
                (37.70, -122.31),
                (37.58, -122.24),
                (37.42, -122.13),
                (37.31, -122.05),
                (37.42, -121.96),
                (37.55, -122.06),
                (37.69, -122.18),
            ],
        ]
        for shape in bay_shapes:
            pts = [self._project_geo(lat, lon, center_lat, center_lon, radius_nm, cx, cy, radius_px) for lat, lon in shape]
            pts = [point for point in pts if point]
            if len(pts) >= 3:
                draw.polygon(pts, fill=water)
                draw.line(pts + [pts[0]], fill=theme["map_water_line"], width=1)

        roads = [
            # US-101 spine
            [(38.20, -122.65), (37.80, -122.43), (37.62, -122.38), (37.36, -122.03), (37.00, -121.57), (36.67, -121.65)],
            # I-80 / Bay Bridge corridor
            [(38.57, -121.49), (38.02, -122.12), (37.82, -122.32), (37.77, -122.42)],
            # I-880 / East Bay
            [(37.90, -122.30), (37.73, -122.20), (37.50, -122.00), (37.34, -121.89)],
            # I-5 distant inland line
            [(39.20, -121.75), (38.50, -121.55), (37.80, -121.35), (37.10, -121.05), (36.35, -120.70)],
        ]
        for road_line in roads:
            pts = [self._project_geo(lat, lon, center_lat, center_lon, radius_nm, cx, cy, radius_px) for lat, lon in road_line]
            pts = [point for point in pts if point]
            if len(pts) >= 2:
                draw.line(pts, fill=road, width=1)

        label_font = self._font(8, "bold")
        point_font = self._font(7, "bold")
        for label, lat, lon in [
            ("SFO", 37.6213, -122.3790),
            ("OAK", 37.7126, -122.2197),
            ("SJC", 37.3639, -121.9289),
            ("SF", 37.7749, -122.4194),
            ("SJ", 37.3382, -121.8863),
            ("SAC", 38.5816, -121.4944),
        ]:
            point = self._project_geo(lat, lon, center_lat, center_lon, radius_nm, cx, cy, radius_px)
            if not point:
                continue
            px, py = point
            if not (ix1 <= px <= ix2 and iy1 <= py <= iy2):
                continue
            draw.ellipse((px - 2, py - 2, px + 2, py + 2), fill=theme["map_city_dot"])
            self._draw_text_fit(draw, label, (px + 5, py - 6), 36, label_font if len(label) <= 3 else point_font, city)

        compass_font = self._font(9, "bold")
        draw.text((ix1 + 8, iy2 - 20), "PACIFIC", fill=theme["map_label_dim"], font=compass_font)
        draw.text((ix2 - 84, iy2 - 20), "BAY AREA", fill=theme["map_label_dim"], font=compass_font)

    def _load_local_map(self, settings):
        path_text = str(settings.get("mapImagePath") or DEFAULT_LOCAL_MAP_IMAGE).strip()
        if not path_text:
            return None
        path = Path(path_text)
        if not path.is_absolute():
            path = PLUGIN_DIR / path
        try:
            if not path.is_file() or path.stat().st_size > 4 * 1024 * 1024:
                return None
            return Image.open(path).convert("RGB")
        except Exception as exc:
            logger.warning("FlightRadar local map image load failed: %s", exc)
            return None

    def _load_external_map(self, settings, device_config, center_lat, center_lon, radius_nm, inner):
        mode = str(settings.get("mapMode") or "").strip().lower()
        url = ""
        if mode in {"image", "static_image"}:
            url = str(settings.get("mapImageUrl") or "").strip()
        elif mode in {"google", "google_static"}:
            key = (
                str(settings.get("googleMapsApiKey") or "").strip()
                or self._load_env(device_config, "GOOGLE_MAPS_API_KEY")
                or self._load_env(device_config, "Google_KEY")
                or self._load_env(device_config, "GOOGLE_KEY")
            )
            if not key:
                return None
            url = self._google_static_map_url(settings, key, center_lat, center_lon, radius_nm, inner, device_config)

        if not url:
            return None

        cache_hours = self._int_setting(settings, "mapCacheHours", 24, 1, 168)
        cache_file = self._map_cache_file(url)
        try:
            if cache_file.is_file() and time.time() - cache_file.stat().st_mtime < cache_hours * 3600:
                return Image.open(cache_file).convert("RGB")
        except Exception as exc:
            logger.debug("Could not use cached FlightRadar map image: %s", exc)

        timeout = self._int_setting(settings, "mapTimeoutSeconds", 6, 3, 12)
        try:
            response = self._session().get(url, headers=HTTP_HEADERS, timeout=(4, timeout))
            response.raise_for_status()
            if len(response.content) > 4 * 1024 * 1024:
                raise RuntimeError("map image too large")
            image = Image.open(BytesIO(response.content)).convert("RGB")
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            image.save(cache_file)
            return image
        except Exception as exc:
            logger.warning("FlightRadar map image fetch failed: %s", exc)
            return None

    def _google_static_map_url(self, settings, api_key, center_lat, center_lon, radius_nm, inner, device_config=None):
        ix1, iy1, ix2, iy2 = inner
        width = max(240, min(640, int(ix2 - ix1)))
        height = max(240, min(640, int(iy2 - iy1)))
        zoom = self._google_zoom(radius_nm)
        map_type = str(settings.get("googleMapType") or "roadmap").strip().lower()
        if map_type not in {"roadmap", "terrain", "satellite", "hybrid"}:
            map_type = "roadmap"
        map_theme = self._google_map_theme(settings, device_config)
        params = [
            ("center", f"{center_lat:.5f},{center_lon:.5f}"),
            ("zoom", str(zoom)),
            ("size", f"{width}x{height}"),
            ("scale", "1"),
            ("format", "png"),
            ("maptype", map_type),
            *[("style", style) for style in self._google_map_styles(map_theme)],
            ("key", api_key),
        ]
        return f"{GOOGLE_STATIC_MAPS_URL}?{urlencode(params)}"

    @staticmethod
    def _google_map_theme(settings, device_config=None):
        value = str((settings or {}).get("googleMapTheme") or "day").strip().lower()
        if value in {"night", "dark"}:
            return "night"
        if value in {"auto", "automatic"}:
            try:
                now = FlightRadar._now(device_config)
                return "night" if now.hour >= 18 or now.hour < 6 else "day"
            except Exception:
                return "day"
        return "day"

    @staticmethod
    def _google_map_styles(map_theme):
        base = [
            "feature:poi|visibility:off",
            "feature:transit|visibility:off",
            "feature:road|element:labels|visibility:off",
        ]
        if map_theme == "night":
            return base + [
                "feature:all|element:labels.text.fill|color:0xf4e4b0",
                "feature:all|element:labels.text.stroke|color:0x171714",
                "feature:water|element:geometry|color:0x234f63",
                "feature:landscape|element:geometry|color:0x2f2d24",
                "feature:road|element:geometry|color:0xc58a3a",
                "feature:administrative|element:geometry|color:0x746b4e",
            ]
        return base + [
            "feature:all|element:labels.text.fill|color:0x5c5246",
            "feature:all|element:labels.text.stroke|color:0xfbf5df",
            "feature:water|element:geometry|color:0x6bc2d6",
            "feature:landscape|element:geometry|color:0xf4edd0",
            "feature:road|element:geometry|color:0xd8b778",
        ]

    @staticmethod
    def _google_zoom(radius_nm):
        radius_nm = FlightRadar._number(radius_nm) or DEFAULT_RADIUS_NM
        if radius_nm <= 35:
            return 9
        if radius_nm <= 75:
            return 8
        if radius_nm <= 170:
            return 7
        return 6

    @staticmethod
    def _map_cache_file(url):
        digest = hashlib.sha1(str(url).encode("utf-8")).hexdigest()[:18]
        return PLUGIN_DIR / "cache" / f"map_{digest}.png"

    def _draw_radar_grid(self, draw, cx, cy, radius_px, radius_nm, theme):
        for fraction in (0.25, 0.5, 0.75, 1.0):
            r = radius_px * fraction
            color = theme["ring"] if fraction < 1.0 else theme["line"]
            draw.ellipse((cx - r, cy - r, cx + r, cy + r), outline=color, width=1)
            label = f"{int(radius_nm * fraction)}"
            font = self._font(8, "normal")
            draw.text((cx + r - 18, cy + 3), label, fill=theme["muted"], font=font)
        draw.line((cx - radius_px, cy, cx + radius_px, cy), fill=theme["ring"], width=1)
        draw.line((cx, cy - radius_px, cx, cy + radius_px), fill=theme["ring"], width=1)
        for angle in range(30, 360, 30):
            rad = math.radians(angle)
            x = cx + math.sin(rad) * radius_px
            y = cy - math.cos(rad) * radius_px
            draw.line((cx, cy, x, y), fill=theme["grid_faint"], width=1)

    def _draw_center_cross(self, draw, cx, cy, theme):
        draw.ellipse((cx - 5, cy - 5, cx + 5, cy + 5), outline=theme["amber"], width=2)
        draw.line((cx - 12, cy, cx - 7, cy), fill=theme["amber"], width=1)
        draw.line((cx + 7, cy, cx + 12, cy), fill=theme["amber"], width=1)
        draw.line((cx, cy - 12, cx, cy - 7), fill=theme["amber"], width=1)
        draw.line((cx, cy + 7, cx, cy + 12), fill=theme["amber"], width=1)

    def _draw_plane_marker(self, draw, x, y, plane, theme):
        color = self._altitude_color(plane.get("altitude_ft"), plane.get("on_ground"), theme)
        track = self._number(plane.get("track"))
        if track is None:
            track = 0
        angle = math.radians(track)
        silhouette = self._material_flight_points(1.04)
        halo = self._scale_points(silhouette, 1.34)
        outline = self._scale_points(silhouette, 1.14)
        draw.polygon(self._rotate_plane_points(halo, angle, x, y), fill=theme["plane_halo"])
        draw.polygon(self._rotate_plane_points(outline, angle, x, y), fill=theme["plane_outline"])
        draw.polygon(self._rotate_plane_points(silhouette, angle, x, y), fill=color)

    @staticmethod
    def _material_flight_points(scale=1.0):
        # Google Material Design `flight` icon geometry, adapted from 24px viewBox.
        return [((px - 11.5) * scale, (12.0 - py) * scale) for px, py in MATERIAL_FLIGHT_ICON_POINTS]

    @staticmethod
    def _scale_points(points, factor):
        return [(right * factor, forward * factor) for right, forward in points]

    @staticmethod
    def _rotate_plane_points(points, angle, x, y):
        return [
            (
                x + math.cos(angle) * right + math.sin(angle) * forward,
                y + math.sin(angle) * right - math.cos(angle) * forward,
            )
            for right, forward in points
        ]

    def _draw_plane_trail(self, draw, x, y, plane, cx, cy, center_label, center_lat, center_lon, radius_nm, radius_px, theme):
        track = self._number(plane.get("track"))
        if plane.get("on_ground"):
            return
        flow = self._aircraft_flow(plane, center_label, cx, cy, x, y, track)
        color = theme["trail_departure"] if flow == "departure" else theme["trail_arrival"]
        history_points = self._project_track_points(
            plane.get("track_points") or [],
            center_lat,
            center_lon,
            radius_nm,
            cx,
            cy,
            radius_px,
        )
        if history_points:
            last_x, last_y = history_points[-1]
            if math.hypot(last_x - x, last_y - y) > 2:
                history_points.append((x, y))
            history_points = self._limit_trail_points(history_points)
        if len(history_points) >= 2:
            draw.line(history_points, fill=self._rgba(color, 150), width=2)
            return
        if track is None:
            return
        angle = math.radians(track)
        speed = self._number(plane.get("speed_kt")) or 220
        length = max(16, min(34, 15 + speed / 24))
        start = (x - math.sin(angle) * 7, y + math.cos(angle) * 7)
        end = (x - math.sin(angle) * length, y + math.cos(angle) * length)
        draw.line((start[0], start[1], end[0], end[1]), fill=self._rgba(color, 150), width=2)

    def _draw_aircraft_list(self, draw, snapshot, box, theme, max_cards=5):
        x1, y1, x2, y2 = box
        draw.rounded_rectangle(box, radius=7, fill=theme["panel"], outline=theme["line"], width=1)
        title_font = self._font(14, "bold")
        small_font = self._font(9, "normal")
        draw.text((x1 + 12, y1 + 10), "NEAREST AIRCRAFT", fill=theme["ink"], font=title_font)
        source_text = str(snapshot.get("source_label") or "No source").upper()
        self._draw_text_fit(draw, source_text, (x2 - 112, y1 + 12), 96, small_font, theme["muted"], anchor="ra")

        aircraft = self._ordered_aircraft_for_list(snapshot)
        card_y = y1 + 38
        card_h = max(46, int((y2 - card_y - 10) / max_cards) - 5)
        for plane in aircraft[:max_cards]:
            if card_y + card_h > y2 - 6:
                break
            self._draw_aircraft_card(draw, (x1 + 10, card_y, x2 - 10, card_y + card_h), plane, theme)
            card_y += card_h + 5

        if not aircraft:
            empty_font = self._font(13, "bold")
            self._draw_text_fit(draw, "WAITING FOR DATA", ((x1 + x2) / 2, (y1 + y2) / 2), x2 - x1 - 24, empty_font, theme["muted"], anchor="mm")

    def _draw_aircraft_card(self, draw, box, plane, theme):
        x1, y1, x2, y2 = box
        color = self._altitude_color(plane.get("altitude_ft"), plane.get("on_ground"), theme)
        draw.rounded_rectangle(box, radius=5, fill=theme["panel2"], outline=theme["card_line"], width=1)
        draw.rectangle((x1, y1, x1 + 4, y2), fill=color)
        call_font = self._font(16, "bold")
        meta_font = self._font(10, "normal")
        callsign = str(plane.get("callsign") or "UNKNOWN")
        self._draw_text_fit(draw, callsign, (x1 + 11, y1 + 6), x2 - x1 - 94, call_font, theme["ink"])
        distance = self._format_distance(plane.get("distance_nm"))
        self._draw_text_fit(draw, distance, (x2 - 67, y1 + 8), 55, meta_font, theme["muted"], anchor="ra")

        route = self._format_route_line(plane)
        if route:
            self._draw_text_fit(
                draw,
                route,
                (x1 + 11, y1 + 28),
                x2 - x1 - 20,
                self._city_font(13, "bold"),
                theme["ink"],
                weight="bold",
                font_getter=self._city_font,
            )
        else:
            self._draw_text_fit(draw, self._format_aircraft_identity(plane), (x1 + 11, y1 + 28), x2 - x1 - 20, meta_font, theme["muted"])

    def _draw_bottom_legend(self, draw, snapshot, box, theme):
        x1, y1, x2, y2 = box
        draw.rounded_rectangle(box, radius=6, fill=theme["panel"], outline=theme["line"], width=1)
        font = self._font(9, "bold")
        items = [
            ("LOW", theme["plane_low"]),
            ("CRUISE", theme["plane_cruise"]),
            ("HIGH", theme["plane_high"]),
            ("GND", theme["plane_ground"]),
        ]
        x = x1 + 12
        for label, color in items:
            draw.ellipse((x, y1 + 11, x + 9, y1 + 20), fill=color)
            draw.text((x + 14, y1 + 9), label, fill=theme["muted"], font=font)
            x += 74
        for label, color in (("ARR", theme["trail_arrival"]), ("DEP", theme["trail_departure"])):
            draw.line((x, y1 + 16, x + 18, y1 + 16), fill=self._blend_color(color, theme["panel"], 0.58), width=2)
            draw.text((x + 24, y1 + 9), label, fill=theme["muted"], font=font)
            x += 66
        status = "STALE CACHE" if snapshot.get("warning") == "STALE CACHE" else "LIVE SNAPSHOT"
        if snapshot.get("warning") == "NO DATA":
            status = "NO DATA"
        status_color = theme["amber"] if snapshot.get("from_cache") else theme["green"]
        self._draw_text_fit(draw, status, (x2 - 136, y1 + 9), 124, font, status_color, anchor="ra")

    def _project_plane(self, plane, center_lat, center_lon, radius_nm, cx, cy, radius_px):
        lat = self._number(plane.get("lat"))
        lon = self._number(plane.get("lon"))
        if lat is None or lon is None:
            return None
        nm_y = (lat - center_lat) * 60.0
        nm_x = (lon - center_lon) * 60.0 * math.cos(math.radians(center_lat))
        if math.hypot(nm_x, nm_y) > radius_nm:
            return None
        px = cx + (nm_x / radius_nm) * radius_px
        py = cy - (nm_y / radius_nm) * radius_px
        return px, py

    @staticmethod
    def _project_geo(lat, lon, center_lat, center_lon, radius_nm, cx, cy, radius_px):
        lat = FlightRadar._number(lat)
        lon = FlightRadar._number(lon)
        if lat is None or lon is None:
            return None
        nm_y = (lat - center_lat) * 60.0
        nm_x = (lon - center_lon) * 60.0 * math.cos(math.radians(center_lat))
        px = cx + (nm_x / radius_nm) * radius_px
        py = cy - (nm_y / radius_nm) * radius_px
        return px, py

    def _rank_aircraft(self, aircraft, center_lat, center_lon):
        cleaned = []
        seen = set()
        for plane in aircraft:
            if not isinstance(plane, dict):
                continue
            lat = self._number(plane.get("lat"))
            lon = self._number(plane.get("lon"))
            if lat is None or lon is None:
                continue
            distance = self._number(plane.get("distance_nm"))
            if distance is None:
                distance = self._distance_nm(center_lat, center_lon, lat, lon)
                plane["distance_nm"] = distance
            key = plane.get("hex") or plane.get("callsign") or f"{lat:.3f},{lon:.3f}"
            if key in seen:
                continue
            seen.add(key)
            cleaned.append(plane)
        return sorted(cleaned, key=lambda p: (self._number(p.get("distance_nm")) or 9999, -(self._number(p.get("altitude_ft")) or 0)))

    @staticmethod
    def _ordered_aircraft_for_list(snapshot):
        aircraft = (snapshot or {}).get("aircraft") or []
        center = (snapshot or {}).get("center") or {}
        return sorted(aircraft, key=lambda plane: FlightRadar._aircraft_list_sort_key(plane, center))

    @staticmethod
    def _aircraft_list_sort_key(plane, center):
        flow = FlightRadar._aircraft_list_flow(plane, center)
        on_ground = bool((plane or {}).get("on_ground"))
        group = 2 if on_ground else (1 if flow == "arrival" else 0)

        distance = FlightRadar._number((plane or {}).get("distance_nm"))
        if distance is None:
            lat = FlightRadar._number((plane or {}).get("lat"))
            lon = FlightRadar._number((plane or {}).get("lon"))
            center_lat = FlightRadar._number((center or {}).get("lat"))
            center_lon = FlightRadar._number((center or {}).get("lon"))
            if lat is not None and lon is not None and center_lat is not None and center_lon is not None:
                distance = FlightRadar._distance_nm(center_lat, center_lon, lat, lon)

        altitude = FlightRadar._number((plane or {}).get("altitude_ft")) or 0
        return group, distance if distance is not None else 9999, -altitude

    @staticmethod
    def _aircraft_list_flow(plane, center):
        center_label = (center or {}).get("label") or DEFAULT_CENTER_LABEL
        route_flow = FlightRadar._route_flow(plane, center_label)
        if route_flow:
            return route_flow

        track = FlightRadar._number((plane or {}).get("track"))
        lat = FlightRadar._number((plane or {}).get("lat"))
        lon = FlightRadar._number((plane or {}).get("lon"))
        center_lat = FlightRadar._number((center or {}).get("lat"))
        center_lon = FlightRadar._number((center or {}).get("lon"))
        if None in (track, lat, lon, center_lat, center_lon):
            return ""

        nm_y = (lat - center_lat) * 60.0
        nm_x = (lon - center_lon) * 60.0 * math.cos(math.radians(center_lat))
        return FlightRadar._aircraft_flow(plane, center_label, 0.0, 0.0, nm_x, -nm_y, track)

    @staticmethod
    def _route_cache_file():
        return PLUGIN_DIR / "cache" / "routes_v2.json"

    @staticmethod
    def _track_history_file():
        return PLUGIN_DIR / "cache" / "tracks_v1.json"

    @staticmethod
    def _route_source_order(settings):
        text = str((settings or {}).get("routeSourceOrder") or "airping\nadsb_lol")
        aliases = {
            "adsb": "adsb_lol",
            "adsb.lol": "adsb_lol",
            "adsblol": "adsb_lol",
        }
        order = []
        for line in re.split(r"[\n,]+", text):
            key = re.sub(r"[^a-z0-9_.-]+", "_", line.strip().lower())
            key = aliases.get(key, key)
            if key in {"airping", "adsb_lol"} and key not in order:
                order.append(key)
        return order or ["airping", "adsb_lol"]

    @staticmethod
    def _clean_route_callsign(value):
        text = re.sub(r"[^A-Z0-9]+", "", str(value or "").upper())
        if not text or text == "UNKNOWN" or not re.search(r"\d", text):
            return ""
        if re.match(r"^N\d", text):
            return ""
        if not re.match(r"^[A-Z]{2,4}\d[A-Z0-9]{0,4}$", text):
            return ""
        return text[:10]

    @staticmethod
    def _route_candidate_callsign(plane):
        return FlightRadar._clean_route_callsign((plane or {}).get("callsign"))

    @staticmethod
    def _clean_airport_code(value):
        text = re.sub(r"[^A-Z0-9]+", "", str(value or "").upper())
        if len(text) == 4 and text.startswith("K"):
            text = text[1:]
        if 3 <= len(text) <= 4:
            return text
        return ""

    @staticmethod
    def _clean_route(route, origin="", destination=""):
        raw = str(route or "").upper()
        parts = [FlightRadar._clean_airport_code(part) for part in re.split(r"[^A-Z0-9]+", raw)]
        parts = [part for part in parts if part]
        if not parts:
            origin_code = FlightRadar._clean_airport_code(origin)
            destination_code = FlightRadar._clean_airport_code(destination)
            parts = [part for part in (origin_code, destination_code) if part]
        if len(parts) >= 2:
            return "-".join(parts[:4])
        return ""

    @staticmethod
    def _clean_city_name(value, max_len=28):
        text = re.sub(r"\s+", " ", str(value or "")).strip()
        if not text:
            return ""
        if "," in text:
            first = text.split(",", 1)[0].strip()
            if len(first) > 2:
                text = first
        text = re.sub(r"\s*/\s*", "-", text)
        text = re.sub(r"\bInternational\b", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\bMunicipal\b", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\bRegional\b", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\bMetropolitan\b", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\bCounty\b", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\bExecutive\b", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\bAirport\b", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\bIntl\b", "", text, flags=re.IGNORECASE)
        text = re.sub(r"[^A-Za-z0-9\u4e00-\u9fff .'/#&-]+", "", text)
        text = re.sub(r"\s+", " ", text).strip(" .,-/")
        return text[:max_len].strip(" .,-/")

    @staticmethod
    def _clean_route_label(value):
        text = re.sub(r"\s+", " ", str(value or "")).strip()
        if not text:
            return ""
        text = re.sub(r"\s*[-=]+>\s*", " -> ", text)
        parts = [FlightRadar._clean_city_name(part) for part in text.split("->")]
        parts = [part for part in parts if part]
        if len(parts) >= 2:
            return " -> ".join(parts[:4])
        return ""

    @staticmethod
    def _city_lookup_key(value):
        text = re.sub(r"[^A-Z0-9]+", " ", str(value or "").upper())
        text = re.sub(
            r"\b(AIRPORT|INTL|INTERNATIONAL|MUNICIPAL|REGIONAL|METROPOLITAN|COUNTY|EXECUTIVE)\b",
            " ",
            text,
        )
        text = re.sub(
            r"\b(AL|AK|AZ|AR|CA|CO|CT|FL|GA|HI|IL|IN|LA|MA|MD|MI|MN|MO|NC|NJ|NM|NV|NY|OH|OR|PA|TN|TX|UT|VA|WA)\b$",
            "",
            text,
        )
        return re.sub(r"\s+", " ", text).strip()

    @staticmethod
    def _localized_city_name(value):
        code = FlightRadar._clean_airport_code(value)
        if code:
            return AIRPORT_CITY_ZH_CN.get(code, code)
        city = FlightRadar._clean_city_name(value)
        if not city:
            return ""
        return CITY_NAME_ZH_CN.get(FlightRadar._city_lookup_key(city), city)

    @staticmethod
    def _localized_route_label(value):
        route_label = FlightRadar._clean_route_label(value)
        if not route_label:
            return ""
        parts = [FlightRadar._localized_city_name(part) for part in route_label.split("->")]
        parts = [part for part in parts if part]
        if len(parts) >= 2:
            return " -> ".join(parts[:4])
        return route_label

    @staticmethod
    def _route_city_info(item, route):
        route_parts = [part for part in str(route or "").split("-") if part]
        airports = item.get("_airports") if isinstance(item, dict) else None
        code_to_city = {}
        ordered_cities = []
        if isinstance(airports, list):
            for airport in airports:
                if not isinstance(airport, dict):
                    continue
                city = FlightRadar._clean_city_name(
                    FlightRadar._first_text(airport, ["location", "city", "municipality", "name"])
                )
                if not city:
                    continue
                codes = [
                    FlightRadar._clean_airport_code(airport.get("iata")),
                    FlightRadar._clean_airport_code(airport.get("icao")),
                    FlightRadar._clean_airport_code(airport.get("code")),
                ]
                for code in {code for code in codes if code}:
                    code_to_city[code] = city
                if not ordered_cities or ordered_cities[-1] != city:
                    ordered_cities.append(city)

        city_parts = [code_to_city.get(code, "") for code in route_parts[:4]]
        if route_parts and not any(city_parts) and len(ordered_cities) >= len(route_parts):
            city_parts = ordered_cities[: len(route_parts)]
        elif route_parts and ordered_cities:
            city_parts = [
                city or (ordered_cities[index] if index < len(ordered_cities) else "")
                for index, city in enumerate(city_parts)
            ]

        label_parts = []
        for index, code in enumerate(route_parts[:4]):
            city = city_parts[index] if index < len(city_parts) else ""
            label_parts.append(city or code)
        if len(label_parts) < 2 and len(ordered_cities) >= 2:
            label_parts = ordered_cities[:4]

        route_label = " -> ".join(label_parts) if len(label_parts) >= 2 else ""
        return {
            "route_label": route_label,
            "origin_city": city_parts[0] if city_parts else "",
            "destination_city": city_parts[-1] if len(city_parts) >= 2 else "",
        }

    @staticmethod
    def _apply_route_info(plane, route_info):
        route = FlightRadar._clean_route(
            (route_info or {}).get("route"),
            (route_info or {}).get("origin"),
            (route_info or {}).get("destination"),
        )
        route_label = FlightRadar._clean_route_label((route_info or {}).get("route_label"))
        origin_city = FlightRadar._clean_city_name((route_info or {}).get("origin_city"))
        destination_city = FlightRadar._clean_city_name((route_info or {}).get("destination_city"))
        if route:
            plane["route"] = route
            parts = route.split("-")
            plane["origin"] = parts[0]
            plane["destination"] = parts[-1]
        if route_label:
            plane["route_label"] = route_label
        if origin_city:
            plane["origin_city"] = origin_city
        if destination_city:
            plane["destination_city"] = destination_city

    def _write_radar_context(self, snapshot, now):
        try:
            aircraft = snapshot.get("aircraft") or []
            write_context(
                "flight_radar",
                {
                    "kind": "flight_radar",
                    "source": snapshot.get("source_label") or "No source",
                    "summary": f"{len(aircraft)} aircraft near {snapshot.get('center', {}).get('label', DEFAULT_CENTER_LABEL)}",
                    "items": [
                        {
                            "callsign": item.get("callsign"),
                            "route": item.get("route"),
                            "origin": item.get("origin"),
                            "destination": item.get("destination"),
                            "route_label": item.get("route_label"),
                            "origin_city": item.get("origin_city"),
                            "destination_city": item.get("destination_city"),
                            "altitude_ft": item.get("altitude_ft"),
                            "speed_kt": item.get("speed_kt"),
                            "distance_nm": item.get("distance_nm"),
                        }
                        for item in aircraft[:8]
                    ],
                    "from_cache": bool(snapshot.get("from_cache")),
                    "warning": snapshot.get("warning") or "",
                },
                generated_at=now,
                ttl_seconds=30 * 60,
            )
        except Exception as exc:
            logger.debug("FlightRadar context write skipped: %s", exc)

    @staticmethod
    def _theme():
        # Comic color e-paper tokens: cold paper blues, process black linework,
        # and a limited set of flat cyan/violet accents.
        return {
            "bg": (55, 91, 119),
            "panel": (163, 213, 222),
            "panel2": (117, 180, 203),
            "chip_bg": (190, 236, 231),
            "header_bg": (18, 18, 16),
            "header_ink": (207, 241, 241),
            "header_muted": (93, 211, 226),
            "header_rule": (80, 193, 214),
            "ink": (18, 18, 16),
            "muted": (28, 55, 70),
            "line": (18, 18, 16),
            "ring": (50, 94, 111),
            "grid_faint": (82, 128, 145),
            "card_line": (18, 18, 16),
            "map_border": (18, 18, 16),
            "map_shade": (28, 40, 48),
            "map_water": (50, 158, 190),
            "map_water_line": (18, 92, 116),
            "map_land_line": (18, 18, 16),
            "map_road": (92, 145, 170),
            "map_city": (18, 18, 16),
            "map_city_dot": (18, 18, 16),
            "map_label_dim": (42, 70, 82),
            "cyan": (49, 186, 213),
            "amber": (255, 181, 37),
            "green": (45, 161, 130),
            "red": (218, 62, 55),
            "magenta": (193, 78, 178),
            "blue": (50, 122, 211),
            "plane_halo": (255, 237, 162),
            "plane_outline": (18, 18, 16),
            "plane_low": (255, 218, 43),
            "plane_cruise": (255, 135, 39),
            "plane_high": (224, 54, 67),
            "plane_unknown": (255, 172, 55),
            "plane_ground": (176, 97, 39),
            "trail_arrival": (255, 166, 43),
            "trail_departure": (224, 54, 67),
        }

    @staticmethod
    def _rgba(color, alpha):
        return (int(color[0]), int(color[1]), int(color[2]), int(alpha))

    @staticmethod
    def _blend_color(foreground, background, opacity):
        opacity = max(0.0, min(1.0, float(opacity)))
        return tuple(
            int(background[index] * (1.0 - opacity) + foreground[index] * opacity)
            for index in range(3)
        )

    @staticmethod
    def _altitude_color(altitude_ft, on_ground, theme):
        if on_ground:
            return theme["plane_ground"]
        altitude = FlightRadar._number(altitude_ft)
        if altitude is None:
            return theme["plane_unknown"]
        if altitude < 10000:
            return theme["plane_low"]
        if altitude < 32000:
            return theme["plane_cruise"]
        return theme["plane_high"]

    @staticmethod
    def _aircraft_flow(plane, center_label, cx, cy, x, y, track):
        route_flow = FlightRadar._route_flow(plane, center_label)
        if route_flow:
            return route_flow
        angle = math.radians(FlightRadar._number(track) or 0)
        heading_x = math.sin(angle)
        heading_y = -math.cos(angle)
        radial_x = x - cx
        radial_y = y - cy
        return "departure" if heading_x * radial_x + heading_y * radial_y >= 0 else "arrival"

    @staticmethod
    def _should_label_plane(plane, center_label, cx, cy, x, y):
        if (plane or {}).get("on_ground"):
            return False
        if not FlightRadar._is_map_label_callsign((plane or {}).get("callsign")):
            return False
        route_flow = FlightRadar._route_flow(plane, center_label)
        if route_flow == "arrival":
            return False
        track = FlightRadar._number((plane or {}).get("track"))
        if track is None:
            return True
        return FlightRadar._aircraft_flow(plane, center_label, cx, cy, x, y, track) != "arrival"

    @staticmethod
    def _is_map_label_callsign(value):
        text = re.sub(r"[^A-Z0-9]+", "", str(value or "").upper())
        return bool(re.match(r"^[A-Z]{2,4}\d", text))

    @staticmethod
    def _route_flow(plane, center_label):
        aliases = FlightRadar._center_aliases(center_label)
        route_label = str((plane or {}).get("route_label") or "")
        route_label_parts = [part.strip() for part in route_label.split("->") if part.strip()]
        route = FlightRadar._clean_route(
            (plane or {}).get("route"),
            (plane or {}).get("origin"),
            (plane or {}).get("destination"),
        )
        route_parts = [part for part in route.split("-") if part]

        origin_values = [
            (plane or {}).get("origin"),
            (plane or {}).get("origin_city"),
            route_parts[0] if route_parts else "",
            route_label_parts[0] if route_label_parts else "",
        ]
        destination_values = [
            (plane or {}).get("destination"),
            (plane or {}).get("destination_city"),
            route_parts[-1] if len(route_parts) >= 2 else "",
            route_label_parts[-1] if len(route_label_parts) >= 2 else "",
        ]
        origin_matches = any(FlightRadar._matches_center_alias(value, aliases) for value in origin_values)
        destination_matches = any(FlightRadar._matches_center_alias(value, aliases) for value in destination_values)
        if origin_matches and not destination_matches:
            return "departure"
        if destination_matches and not origin_matches:
            return "arrival"
        return ""

    @staticmethod
    def _center_aliases(center_label):
        code = FlightRadar._clean_airport_code(center_label)
        raw = str(center_label or "").strip().upper()
        aliases = {value for value in (code, raw) if value}
        city_aliases = {
            "SFO": ("SAN FRANCISCO", "SAN FRANCISCO CA", "SAN FRANCISCO INTERNATIONAL"),
            "OAK": ("OAKLAND", "OAKLAND CA"),
            "SJC": ("SAN JOSE", "SAN JOSE CA"),
        }
        aliases.update(city_aliases.get(code, ()))
        return aliases

    @staticmethod
    def _matches_center_alias(value, aliases):
        normalized = re.sub(r"[^A-Z0-9]+", "", str(value or "").upper())
        if not normalized:
            return False
        for alias in aliases:
            alias_normalized = re.sub(r"[^A-Z0-9]+", "", str(alias or "").upper())
            if alias_normalized and (normalized == alias_normalized or alias_normalized in normalized):
                return True
        return False

    @staticmethod
    def _readsb_altitude(item):
        alt = item.get("alt_baro")
        on_ground = str(alt).lower() == "ground" or bool(item.get("on_ground"))
        if on_ground:
            return 0, True
        altitude = FlightRadar._number(alt)
        if altitude is None:
            altitude = FlightRadar._number(item.get("alt_geom"))
        return altitude, False

    @staticmethod
    def _distance_nm(lat1, lon1, lat2, lon2):
        radius_nm = 3440.065
        phi1 = math.radians(lat1)
        phi2 = math.radians(lat2)
        d_phi = math.radians(lat2 - lat1)
        d_lambda = math.radians(lon2 - lon1)
        a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
        return 2 * radius_nm * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    @staticmethod
    def _extract_generic_records(data):
        if isinstance(data, list):
            return data
        if not isinstance(data, dict):
            return []
        for key in ("aircraft", "flights", "positions", "data", "results", "items"):
            value = data.get(key)
            if isinstance(value, list):
                return value
            if isinstance(value, dict):
                nested = FlightRadar._extract_generic_records(value)
                if nested:
                    return nested
        return []

    @staticmethod
    def _first_number(record, paths):
        for path in paths:
            value = FlightRadar._nested_value(record, path)
            parsed = FlightRadar._number(value)
            if parsed is not None:
                return parsed
        return None

    @staticmethod
    def _first_text(record, paths):
        for path in paths:
            value = FlightRadar._nested_value(record, path)
            if value is not None and str(value).strip():
                return str(value).strip()
        return ""

    @staticmethod
    def _nested_value(record, path):
        value = record
        for part in path.split("."):
            if not isinstance(value, dict):
                return None
            value = value.get(part)
        return value

    @staticmethod
    def _source_order(settings):
        text = str(settings.get("sourceOrder") or DEFAULT_SOURCE_ORDER)
        aliases = {
            "adsb": "adsb_lol",
            "adsb.lol": "adsb_lol",
            "airplanes": "airplanes_live",
            "airplanes.live": "airplanes_live",
            "open_sky": "opensky",
            "rapidapi": "rapidapi_custom",
            "flight_aware": "flightaware",
        }
        order = []
        for line in re.split(r"[\n,]+", text):
            key = re.sub(r"[^a-z0-9_.-]+", "_", line.strip().lower())
            key = aliases.get(key, key)
            if key and key not in order:
                order.append(key)
        return order or ["adsb_lol", "airplanes_live", "opensky"]

    @staticmethod
    def _parse_key_value_lines(text):
        params = {}
        for line in str(text or "").splitlines():
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if key:
                params[key] = value.strip()
        return params

    @staticmethod
    def _format_altitude(altitude_ft, on_ground=False):
        if on_ground:
            return "GND"
        value = FlightRadar._number(altitude_ft)
        if value is None:
            return "ALT --"
        if abs(value) >= 10000:
            return f"{value / 1000:.1f}k ft"
        return f"{int(value):,} ft"

    @staticmethod
    def _format_route_line(plane):
        route_label = FlightRadar._localized_route_label((plane or {}).get("route_label"))
        if route_label:
            return route_label
        origin_city = FlightRadar._localized_city_name((plane or {}).get("origin_city"))
        destination_city = FlightRadar._localized_city_name((plane or {}).get("destination_city"))
        if origin_city and destination_city:
            return f"{origin_city} -> {destination_city}"
        route = FlightRadar._clean_route(
            (plane or {}).get("route"),
            (plane or {}).get("origin"),
            (plane or {}).get("destination"),
        )
        if route:
            parts = [FlightRadar._localized_city_name(part) for part in route.split("-")]
            return " -> ".join([part for part in parts if part])
        return ""

    @staticmethod
    def _format_aircraft_identity(plane):
        aircraft_type = str((plane or {}).get("type") or "").strip().upper()
        registration = str((plane or {}).get("registration") or "").strip().upper()
        pieces = [piece for piece in (aircraft_type, registration) if piece]
        if pieces:
            return " / ".join(pieces[:2])
        alt = FlightRadar._format_altitude((plane or {}).get("altitude_ft"), (plane or {}).get("on_ground"))
        speed = FlightRadar._format_speed((plane or {}).get("speed_kt"))
        return f"{alt}   {speed}"

    @staticmethod
    def _format_speed(speed_kt):
        value = FlightRadar._number(speed_kt)
        if value is None:
            return "-- kt"
        return f"{int(value)} kt"

    @staticmethod
    def _format_heading(track):
        value = FlightRadar._number(track)
        if value is None:
            return "---"
        return f"{int(value) % 360:03d} deg"

    @staticmethod
    def _format_distance(distance_nm):
        value = FlightRadar._number(distance_nm)
        if value is None:
            return "-- NM"
        if value < 10:
            return f"{value:.1f} NM"
        return f"{int(value)} NM"

    @staticmethod
    def _cache_key(lat, lon, radius_nm, max_aircraft, source_order):
        payload = json.dumps(
            {
                "lat": round(lat, 4),
                "lon": round(lon, 4),
                "radius_nm": radius_nm,
                "max_aircraft": max_aircraft,
                "sources": source_order,
                "schema": CACHE_SCHEMA_VERSION,
            },
            sort_keys=True,
        )
        return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _cache_file(cache_key):
        return PLUGIN_DIR / "cache" / f"{cache_key}.json"

    @staticmethod
    def _read_json(path: Path):
        try:
            if path.is_file():
                return json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.debug("Could not read FlightRadar cache %s: %s", path, exc)
        return {}

    @staticmethod
    def _write_json(path: Path, payload):
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        text = json.dumps(payload, ensure_ascii=False)
        try:
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, path)
        except Exception:
            path.write_text(text, encoding="utf-8")
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass

    @staticmethod
    def _clean_label(value, max_len=24):
        text = re.sub(r"\s+", " ", str(value or "")).strip()
        text = re.sub(r"[^\w .:/#-]+", "", text)
        return text[:max_len]

    @staticmethod
    def _load_env(device_config, key):
        try:
            if hasattr(device_config, "load_env_key"):
                return device_config.load_env_key(key)
        except Exception:
            pass
        return os.getenv(key)

    @staticmethod
    def _now(device_config):
        tz_name = "America/Los_Angeles"
        try:
            tz_name = device_config.get_config("timezone") or tz_name
        except Exception:
            pass
        try:
            return datetime.now(ZoneInfo(tz_name))
        except Exception:
            return datetime.now()

    @staticmethod
    def _session():
        session = requests.Session()
        session.trust_env = True
        return session

    @staticmethod
    def _number(value):
        if value is None or value == "":
            return None
        if isinstance(value, bool):
            return None
        try:
            return float(str(value).replace(",", "").strip())
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _float_setting(settings, key, default, low, high):
        value = FlightRadar._number(settings.get(key))
        if value is None:
            value = default
        return max(low, min(high, float(value)))

    @staticmethod
    def _display_radius(settings, data_radius_nm):
        data_radius_nm = FlightRadar._number(data_radius_nm) or DEFAULT_RADIUS_NM
        value = FlightRadar._number((settings or {}).get("displayRadiusNm"))
        if value is None:
            value = min(DEFAULT_DISPLAY_RADIUS_NM, data_radius_nm)
        return max(15, min(int(value), int(data_radius_nm), 250))

    @staticmethod
    def _int_setting(settings, key, default, low, high):
        value = FlightRadar._number(settings.get(key))
        if value is None:
            value = default
        return max(low, min(high, int(value)))

    @staticmethod
    def _bool_setting(value, default=False):
        if value is None:
            return default
        return value is True or str(value).lower() in {"1", "true", "on", "yes"}

    @staticmethod
    def _font(size, weight="normal"):
        font = FlightRadar._font_from_paths(SANS_FONT_PATHS["bold" if weight == "bold" else "normal"], size)
        if font:
            return font
        for family in ("Microsoft YaHei", "WenQuanYi Micro Hei", "Noto Sans CJK SC", "Jost"):
            try:
                font = get_font(family, int(size), "bold" if weight == "bold" else "normal")
                if font:
                    return font
            except Exception:
                continue
        return ImageFont.load_default()

    @staticmethod
    def _city_font(size, weight="normal"):
        font = FlightRadar._font_from_paths(CITY_FONT_PATHS["bold" if weight == "bold" else "normal"], size)
        if font:
            return font
        for family in ("Microsoft YaHei", "Microsoft YaHei UI", "Noto Sans SC", "Noto Sans CJK SC", "WenQuanYi Micro Hei"):
            try:
                font = get_font(family, int(size), "bold" if weight == "bold" else "normal")
                if font:
                    return font
            except Exception:
                continue
        return FlightRadar._font(size, weight)

    @staticmethod
    def _font_from_paths(paths, size):
        for path in paths:
            try:
                if os.path.isfile(path):
                    return ImageFont.truetype(path, int(size))
            except Exception:
                continue
        return None

    @staticmethod
    def _resampling_filter():
        return getattr(Image, "Resampling", Image).LANCZOS

    def _draw_text_fit(self, draw, text, xy, max_width, font, fill, anchor=None, weight="normal", font_getter=None):
        text = str(text or "")
        size = getattr(font, "size", 12)
        font_getter = font_getter or self._font
        fit = font
        while size > 7 and draw.textlength(text, font=fit) > max_width:
            size -= 1
            fit = font_getter(size, weight)
        if anchor == "ra":
            x, y = xy
            draw.text((x + max_width, y), text, fill=fill, font=fit, anchor="ra")
        elif anchor == "mm":
            draw.text(xy, text, fill=fill, font=fit, anchor="mm")
        else:
            draw.text(xy, text, fill=fill, font=fit)


class _SkippedSource(RuntimeError):
    pass
