from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import time
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from urllib.parse import urlencode

from PIL import Image, ImageDraw, ImageFont, ImageOps

from plugins.base_plugin.base_plugin import BasePlugin
from plugins.context_cache import write_context
from utils.app_utils import bounded_int, coerce_bool, get_available_font_names, get_font
from utils.http_client import get_http_session
from utils.image_utils import text_width

logger = logging.getLogger(__name__)

PLUGIN_ID = "earthspace_pulse"
PLUGIN_DIR = Path(__file__).resolve().parent
CACHE_SCHEMA_VERSION = "earthspace-pulse-v1"
DEFAULT_REFRESH_MINUTES = 30
DEFAULT_MAX_QUAKES = 4
DEFAULT_THEME = "auto"
DEFAULT_MAP_CACHE_HOURS = 24

GOOGLE_STATIC_MAPS_URL = "https://maps.googleapis.com/maps/api/staticmap"
SWPC_KP_URL = "https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json"
SWPC_KP_FORECAST_URL = "https://services.swpc.noaa.gov/products/noaa-planetary-k-index-forecast.json"
SWPC_ALERTS_URL = "https://services.swpc.noaa.gov/products/alerts.json"
SWPC_AURORA_URL = "https://services.swpc.noaa.gov/json/ovation_aurora_latest.json"
SWPC_XRAY_URL = "https://services.swpc.noaa.gov/json/goes/primary/xrays-1-day.json"
SWPC_PLASMA_URL = "https://services.swpc.noaa.gov/products/solar-wind/plasma-1-day.json"
SWPC_MAG_URL = "https://services.swpc.noaa.gov/products/solar-wind/mag-1-day.json"

USGS_FEEDS = {
    "all_day": "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_day.geojson",
    "significant_day": "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/significant_day.geojson",
    "2.5_day": "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/2.5_day.geojson",
    "4.5_day": "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_day.geojson",
    "all_week": "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_week.geojson",
}

IMAGE_HEADERS = {
    "User-Agent": "InkyPi EarthspacePulse/1.0",
    "Accept": "image/png,image/jpeg,image/*;q=0.8,*/*;q=0.5",
}

REQUEST_HEADERS = {
    "User-Agent": "InkyPi EarthspacePulse/1.0",
    "Accept": "application/json,*/*;q=0.8",
}

LOCAL_SAMPLE_PAYLOAD = {
    "schema": CACHE_SCHEMA_VERSION,
    "space_weather": {
        "kp_now": 2.67,
        "kp_trend": {"direction": "steady", "delta": 0.0},
        "kp_history": [
            {"time_tag": "2026-06-26T00:00:00", "kp": 2.0},
            {"time_tag": "2026-06-26T03:00:00", "kp": 2.33},
            {"time_tag": "2026-06-26T06:00:00", "kp": 2.67},
        ],
        "kp_forecast": [{"time_tag": "2026-06-26T12:00:00", "kp": 3.0, "noaa_scale": None}],
        "noaa_scale": "G0 quiet",
        "alerts": [
            {
                "product_id": "K04W",
                "issue_datetime": "2026-06-25 23:43:25.917",
                "headline": "EXTENDED WARNING: Geomagnetic K-index of 4 expected",
            }
        ],
        "aurora": {
            "observation_time": "2026-06-27T06:33:00Z",
            "forecast_time": "2026-06-27T07:17:00Z",
            "max": 15,
            "north_peak_lat": 74,
            "south_peak_lat": -77,
            "active_points": 1840,
        },
        "xray": {
            "time_tag": "2026-06-26T06:40:00Z",
            "class": "C1.4",
            "flux": 1.4e-6,
            "energy": "0.1-0.8nm",
        },
        "solar_wind": {
            "time_tag": "2026-06-26 06:41:00.000",
            "speed": 513.5,
            "density": 0.39,
            "temperature": 5669.0,
            "bz_gsm": 2.85,
            "bt": 5.36,
        },
    },
    "earthquakes": {
        "feed": "all_day",
        "count_24h": 3,
        "source_count": 3,
        "source_generated_at": "2026-06-27T06:40:00Z",
        "max_event": {
            "id": "sample2",
            "mag": 5.1,
            "place": "South Sandwich Islands region",
            "time_ms": 1782532000000,
            "time_iso": "2026-06-27T03:46:40Z",
            "depth_km": 35.0,
            "latitude": -58.2,
            "longitude": -25.1,
            "alert": None,
            "tsunami": False,
            "sig": 400,
            "url": "https://earthquake.usgs.gov/",
            "title": "M 5.1 - South Sandwich Islands region",
        },
        "recent_events": [
            {
                "id": "sample1",
                "mag": 1.5,
                "place": "7 km NW of The Geysers, CA",
                "time_ms": 1782540684360,
                "time_iso": "2026-06-27T06:11:24Z",
                "depth_km": 3.0,
                "latitude": 38.821,
                "longitude": -122.8125,
                "alert": None,
                "tsunami": False,
                "sig": 33,
                "url": "https://earthquake.usgs.gov/",
                "title": "M 1.5 - 7 km NW of The Geysers, CA",
            },
            {
                "id": "sample2",
                "mag": 5.1,
                "place": "South Sandwich Islands region",
                "time_ms": 1782532000000,
                "time_iso": "2026-06-27T03:46:40Z",
                "depth_km": 35.0,
                "latitude": -58.2,
                "longitude": -25.1,
                "alert": None,
                "tsunami": False,
                "sig": 400,
                "url": "https://earthquake.usgs.gov/",
                "title": "M 5.1 - South Sandwich Islands region",
            },
            {
                "id": "sample3",
                "mag": 4.6,
                "place": "Kuril Islands",
                "time_ms": 1782528000000,
                "time_iso": "2026-06-27T02:40:00Z",
                "depth_km": 82.0,
                "latitude": 45.1,
                "longitude": 149.4,
                "alert": None,
                "tsunami": False,
                "sig": 326,
                "url": "https://earthquake.usgs.gov/",
                "title": "M 4.6 - Kuril Islands",
            },
        ],
        "nearest_event": None,
    },
    "status": {
        "source_state": "local_sample",
        "generated_at": "2026-06-27T06:40:00Z",
        "source_urls": [SWPC_KP_URL, SWPC_ALERTS_URL, USGS_FEEDS["all_day"]],
    },
}


class EarthspacePulse(BasePlugin):
    def generate_settings_template(self):
        params = super().generate_settings_template()
        params["style_settings"] = False
        params["available_fonts"] = get_available_font_names(default="Jost")
        params["api_key"] = {
            "required": False,
            "service": "Google Maps Static API",
            "expected_key": "GOOGLE_MAPS_API_KEY or Google_KEY",
        }
        return params

    def generate_image(self, settings, device_config):
        settings = settings or {}
        now = datetime.now(timezone.utc)
        payload = self._payload(settings, now)
        self._write_context(payload, now)
        return self._render_page(self.get_dimensions(device_config), payload, settings, now, device_config=device_config)

    def _payload(self, settings, now):
        cache = self._read_cache()
        force_refresh = self._enabled(settings.get("forceRefresh") or settings.get("force_refresh"), default=False)
        refresh_seconds = self._refresh_minutes(settings) * 60
        if not force_refresh and self._cache_is_fresh(cache, now, refresh_seconds):
            payload = dict(cache.get("payload") or {})
            payload["status"] = dict(payload.get("status") or {})
            payload["status"]["source_state"] = "cache"
            return payload

        try:
            payload = self._fetch_live_payload(settings, now)
            self._write_cache({"schema": CACHE_SCHEMA_VERSION, "generated_at": now.isoformat(), "payload": payload})
            return payload
        except Exception as exc:
            logger.warning("EarthspacePulse live fetch failed: %s", exc)

        cached = cache.get("payload") if self._cache_is_fresh(cache, now, refresh_seconds) else None
        if isinstance(cached, dict):
            payload = dict(cached)
            payload["status"] = dict(payload.get("status") or {})
            payload["status"]["source_state"] = "cache"
            return payload
        sample = json.loads(json.dumps(LOCAL_SAMPLE_PAYLOAD))
        sample["status"]["generated_at"] = now.isoformat()
        return sample

    def _fetch_live_payload(self, settings, now):
        space_weather = self._fetch_space_weather()
        earthquakes = self._fetch_earthquakes(settings)
        return {
            "schema": CACHE_SCHEMA_VERSION,
            "space_weather": space_weather,
            "earthquakes": earthquakes,
            "status": {
                "source_state": "live",
                "generated_at": now.isoformat(),
                "source_urls": [
                    SWPC_KP_URL,
                    SWPC_KP_FORECAST_URL,
                    SWPC_ALERTS_URL,
                    SWPC_AURORA_URL,
                    SWPC_XRAY_URL,
                    SWPC_PLASMA_URL,
                    SWPC_MAG_URL,
                    self._quake_feed_url(settings),
                ],
            },
        }

    def _fetch_space_weather(self):
        kp_data = self._get_json(SWPC_KP_URL, timeout=(5, 15))
        kp_forecast = self._get_json(SWPC_KP_FORECAST_URL, timeout=(5, 15))
        alerts = self._get_json(SWPC_ALERTS_URL, timeout=(5, 15))
        aurora = self._get_json(SWPC_AURORA_URL, timeout=(5, 20))
        xray = self._get_json(SWPC_XRAY_URL, timeout=(5, 15))
        plasma = self._get_json(SWPC_PLASMA_URL, timeout=(5, 15))
        mag = self._get_json(SWPC_MAG_URL, timeout=(5, 15))

        kp_payload = self._parse_kp(kp_data, kp_forecast)
        solar_wind = self._parse_solar_wind(plasma, mag)
        return {
            **kp_payload,
            "alerts": self._parse_alerts(alerts),
            "aurora": self._parse_aurora(aurora),
            "xray": self._parse_xray(xray),
            "solar_wind": solar_wind,
        }

    def _fetch_earthquakes(self, settings):
        feed = self._quake_feed(settings)
        data = self._get_json(USGS_FEEDS[feed], timeout=(5, 18))
        return self._parse_earthquakes(data, settings, feed=feed)

    def _get_json(self, url, timeout=(5, 15)):
        response = get_http_session().get(url, headers=REQUEST_HEADERS, timeout=timeout)
        response.raise_for_status()
        return response.json()

    def _parse_kp(self, observed_rows, forecast_rows=None):
        observed = []
        for row in observed_rows if isinstance(observed_rows, list) else []:
            kp = self._float(row.get("Kp") if isinstance(row, dict) else None)
            time_tag = row.get("time_tag") if isinstance(row, dict) else None
            if kp is not None and time_tag:
                observed.append({"time_tag": str(time_tag), "kp": kp})
        observed = sorted(observed, key=lambda item: item["time_tag"])
        latest = observed[-1] if observed else {"time_tag": "", "kp": None}
        previous = observed[-2] if len(observed) > 1 else None
        delta = 0.0
        direction = "steady"
        if previous and latest["kp"] is not None:
            delta = round(latest["kp"] - previous["kp"], 2)
            if delta > 0.15:
                direction = "rising"
            elif delta < -0.15:
                direction = "falling"

        forecasts = []
        forecast_scale = None
        for row in forecast_rows if isinstance(forecast_rows, list) else []:
            if not isinstance(row, dict):
                continue
            kp = self._float(row.get("kp") if row.get("kp") is not None else row.get("Kp"))
            if kp is None:
                continue
            item = {
                "time_tag": str(row.get("time_tag") or ""),
                "kp": kp,
                "observed": row.get("observed"),
                "noaa_scale": row.get("noaa_scale"),
            }
            forecasts.append(item)
            if not forecast_scale and row.get("noaa_scale"):
                forecast_scale = str(row.get("noaa_scale"))

        kp_now = latest["kp"]
        return {
            "kp_now": kp_now,
            "kp_trend": {"direction": direction, "delta": delta},
            "kp_history": observed[-8:],
            "kp_forecast": forecasts[-8:],
            "noaa_scale": forecast_scale or self._kp_scale(kp_now),
        }

    def _parse_alerts(self, rows):
        alerts = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            message = str(row.get("message") or "")
            headline = self._alert_headline(message)
            if not headline:
                continue
            alerts.append({
                "product_id": str(row.get("product_id") or ""),
                "issue_datetime": str(row.get("issue_datetime") or ""),
                "headline": headline,
            })
            if len(alerts) >= 4:
                break
        return alerts

    def _parse_aurora(self, data):
        data = data if isinstance(data, dict) else {}
        coords = data.get("coordinates") if isinstance(data.get("coordinates"), list) else []
        max_value = 0
        active_points = 0
        north_peak = None
        south_peak = None
        north_value = -1
        south_value = -1
        for point in coords:
            if not isinstance(point, (list, tuple)) or len(point) < 3:
                continue
            lat = self._float(point[1])
            value = self._float(point[2])
            if lat is None or value is None:
                continue
            value = int(round(value))
            max_value = max(max_value, value)
            if value > 0:
                active_points += 1
            if lat >= 0 and value > north_value:
                north_value = value
                north_peak = int(round(lat))
            if lat < 0 and value > south_value:
                south_value = value
                south_peak = int(round(lat))
        return {
            "observation_time": str(data.get("Observation Time") or ""),
            "forecast_time": str(data.get("Forecast Time") or ""),
            "max": max_value,
            "north_peak_lat": north_peak,
            "south_peak_lat": south_peak,
            "active_points": active_points,
        }

    def _parse_xray(self, rows):
        candidates = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            if str(row.get("energy") or "") != "0.1-0.8nm":
                continue
            flux = self._float(row.get("flux"))
            if flux is None:
                continue
            candidates.append({
                "time_tag": str(row.get("time_tag") or ""),
                "flux": flux,
                "observed_flux": self._float(row.get("observed_flux")),
                "energy": str(row.get("energy") or ""),
            })
        latest = sorted(candidates, key=lambda item: item["time_tag"])[-1] if candidates else {}
        flux = latest.get("flux")
        return {
            "time_tag": latest.get("time_tag", ""),
            "class": self._xray_class(flux),
            "flux": flux,
            "observed_flux": latest.get("observed_flux"),
            "energy": latest.get("energy", ""),
        }

    def _parse_solar_wind(self, plasma_rows, mag_rows):
        plasma = self._row_table_latest(plasma_rows)
        mag = self._row_table_latest(mag_rows)
        return {
            "time_tag": plasma.get("time_tag") or mag.get("time_tag") or "",
            "density": self._float(plasma.get("density")),
            "speed": self._float(plasma.get("speed")),
            "temperature": self._float(plasma.get("temperature")),
            "bz_gsm": self._float(mag.get("bz_gsm")),
            "bt": self._float(mag.get("bt")),
        }

    def _parse_earthquakes(self, data, settings, feed="all_day"):
        data = data if isinstance(data, dict) else {}
        metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
        min_mag = self._optional_float(settings.get("minMagnitude") or settings.get("min_magnitude"))
        events = []
        for feature in data.get("features") if isinstance(data.get("features"), list) else []:
            event = self._quake_event(feature)
            if not event:
                continue
            if min_mag is not None and (event.get("mag") is None or event["mag"] < min_mag):
                continue
            events.append(event)
        recent = sorted(events, key=lambda item: item.get("time_ms") or 0, reverse=True)
        max_event = max(events, key=lambda item: (item.get("mag") is not None, item.get("mag") or -999), default=None)
        nearest = None
        if self._enabled(settings.get("showNearestToLocation"), default=False):
            location = self._location(settings)
            if location:
                nearest = self._nearest_event(events, location)
        return {
            "feed": feed,
            "count_24h": len(events),
            "source_count": self._int(metadata.get("count"), len(events)),
            "source_generated_at": self._millis_to_iso(metadata.get("generated")),
            "max_event": max_event,
            "recent_events": recent[: self._max_quakes(settings)],
            "nearest_event": nearest,
        }

    def _quake_event(self, feature):
        if not isinstance(feature, dict):
            return None
        props = feature.get("properties") if isinstance(feature.get("properties"), dict) else {}
        geometry = feature.get("geometry") if isinstance(feature.get("geometry"), dict) else {}
        coords = geometry.get("coordinates") if isinstance(geometry.get("coordinates"), list) else []
        mag = self._float(props.get("mag"))
        if mag is None:
            return None
        lon = self._float(coords[0]) if len(coords) > 0 else None
        lat = self._float(coords[1]) if len(coords) > 1 else None
        depth = self._float(coords[2]) if len(coords) > 2 else None
        time_ms = self._int(props.get("time"), 0)
        return {
            "id": str(feature.get("id") or props.get("code") or ""),
            "mag": mag,
            "place": self._clean_text(props.get("place") or ""),
            "time_ms": time_ms,
            "time_iso": self._millis_to_iso(time_ms),
            "depth_km": depth,
            "latitude": lat,
            "longitude": lon,
            "alert": props.get("alert"),
            "tsunami": bool(self._int(props.get("tsunami"), 0)),
            "sig": self._int(props.get("sig"), 0),
            "url": str(props.get("url") or ""),
            "title": self._clean_text(props.get("title") or ""),
        }

    def _render_page(self, dimensions, payload, settings, now, device_config=None):
        width, height = (int(dimensions[0]), int(dimensions[1]))
        palette = self._palette(settings)
        image = Image.new("RGB", (width, height), palette["background"])
        draw = ImageDraw.Draw(image)

        margin = max(18, min(width, height) // 24)
        gap = max(12, width // 64)
        header_h = max(54, height // 8)
        footer_h = max(18, height // 30)
        content_top = margin + header_h
        content_bottom = height - margin - footer_h
        content_h = content_bottom - content_top
        left_w = int((width - margin * 2 - gap) * 0.55)
        right_w = width - margin * 2 - gap - left_w
        left = (margin, content_top, margin + left_w, content_bottom)
        right = (margin + left_w + gap, content_top, width - margin, content_bottom)

        self._draw_background_grid(draw, (0, 0, width, height), palette)
        self._draw_header(draw, (margin, margin, width - margin, margin + header_h), payload, palette, now)
        self._draw_panel(draw, left, palette, accent=palette["cyan"])
        self._draw_panel(draw, right, palette, accent=palette["amber"])
        self._draw_space_panel(image, draw, left, payload.get("space_weather") or {}, settings, palette)
        self._draw_earth_panel(image, draw, right, payload.get("earthquakes") or {}, settings, palette, device_config=device_config)
        self._draw_footer(draw, (margin, height - margin - footer_h, width - margin, height - margin), payload, palette)
        return image

    def _draw_header(self, draw, box, payload, palette, now):
        x0, y0, x1, y1 = box
        title_font = self._font(max(30, (y1 - y0) // 2), bold=True)
        sub_font = self._font(max(11, (y1 - y0) // 5))
        status_font = self._font(max(10, (y1 - y0) // 6), bold=True)
        draw.text((x0, y0 - 2), "Earthspace Pulse", font=title_font, fill=palette["ink"])
        draw.text((x0 + 2, y0 + max(34, (y1 - y0) // 2 + 10)), "NOAA SWPC space weather + USGS global earthquakes", font=sub_font, fill=palette["muted"])
        status = (payload.get("status") or {}).get("source_state") or "local"
        generated = self._format_time((payload.get("status") or {}).get("generated_at") or now.isoformat())
        chip = f"{status.upper()}  {generated}"
        chip_w = self._text_width(draw, chip, status_font) + 18
        chip_h = max(24, self._text_height(draw, chip, status_font) + 8)
        chip_box = (x1 - chip_w, y0 + 2, x1, y0 + 2 + chip_h)
        draw.rounded_rectangle(chip_box, radius=8, fill=palette["chip"], outline=palette["rule"], width=1)
        draw.text((chip_box[0] + 9, chip_box[1] + 5), chip, font=status_font, fill=palette["ink"])
        draw.line((x0, y1 - 7, x1, y1 - 7), fill=palette["rule"], width=2)

    def _draw_space_panel(self, image, draw, box, space, settings, palette):
        x0, y0, x1, y1 = box
        pad = 14
        title_font = self._font(18, bold=True)
        label_font = self._font(10, bold=True)
        body_font = self._font(12)
        big_font = self._font(42, bold=True)
        draw.text((x0 + pad, y0 + pad), "SPACE WEATHER", font=title_font, fill=palette["cyan"])

        kp = space.get("kp_now")
        kp_text = "--" if kp is None else f"{float(kp):.1f}"
        draw.text((x0 + pad, y0 + 42), "Kp", font=label_font, fill=palette["muted"])
        draw.text((x0 + pad + 2, y0 + 54), kp_text, font=big_font, fill=palette["ink"])
        scale = str(space.get("noaa_scale") or "G0 quiet")
        draw.text((x0 + 116, y0 + 60), scale, font=self._font(20, bold=True), fill=palette["ink"])
        trend = space.get("kp_trend") or {}
        trend_text = f"{str(trend.get('direction') or 'steady').upper()} {self._signed(trend.get('delta'))}"
        draw.text((x0 + 118, y0 + 88), trend_text, font=body_font, fill=palette["muted"])

        gauge_box = (x0 + pad, y0 + 120, x1 - pad, y0 + 168)
        self._draw_kp_history(draw, gauge_box, space.get("kp_history") or [], palette)

        alert_y = gauge_box[3] + 10
        self._draw_alert_strip(draw, (x0 + pad, alert_y, x1 - pad, alert_y + 52), space.get("alerts") or [], palette)

        cursor = alert_y + 66
        if self._enabled(settings.get("showSolarWind"), default=True):
            self._draw_solar_chips(draw, (x0 + pad, cursor, x1 - pad, cursor + 54), space, palette)
            cursor += 66
        if self._enabled(settings.get("showAurora"), default=True):
            self._draw_aurora_band(draw, (x0 + pad, cursor, x1 - pad, y1 - pad), space.get("aurora") or {}, palette)

    def _draw_earth_panel(self, image, draw, box, quakes, settings, palette, device_config=None):
        x0, y0, x1, y1 = box
        pad = 14
        title_font = self._font(18, bold=True)
        label_font = self._font(10, bold=True)
        body_font = self._font(12)
        draw.text((x0 + pad, y0 + pad), "EARTH PULSE", font=title_font, fill=palette["amber"])
        count_text = f"{self._int(quakes.get('count_24h'), 0)} events"
        count_w = self._text_width(draw, count_text, label_font) + 14
        draw.rounded_rectangle((x1 - pad - count_w, y0 + pad, x1 - pad, y0 + pad + 22), radius=7, fill=palette["chip"], outline=palette["rule"])
        draw.text((x1 - pad - count_w + 7, y0 + pad + 5), count_text, font=label_font, fill=palette["ink"])

        map_box = (x0 + pad, y0 + 46, x1 - pad, y0 + 170)
        map_image = self._load_quake_map(settings, device_config, quakes, (map_box[2] - map_box[0], map_box[3] - map_box[1]))
        if map_image:
            self._draw_google_quake_map(image, draw, map_box, map_image, palette)
        else:
            self._draw_quake_map(draw, map_box, quakes.get("recent_events") or [], quakes.get("max_event"), palette)

        max_event = quakes.get("max_event") or {}
        max_y = map_box[3] + 10
        mag_text = self._mag_text(max_event)
        draw.text((x0 + pad, max_y), "MAX", font=label_font, fill=palette["muted"])
        draw.text((x0 + pad, max_y + 14), mag_text, font=self._font(26, bold=True), fill=palette["red"] if (max_event.get("mag") or 0) >= 5 else palette["amber"])
        max_place = self._ellipsize(draw, str(max_event.get("place") or "No event"), body_font, max(60, x1 - x0 - 96))
        draw.text((x0 + pad + 82, max_y + 4), max_place, font=body_font, fill=palette["ink"])
        meta = self._quake_meta(max_event)
        draw.text((x0 + pad + 82, max_y + 25), meta, font=self._font(10), fill=palette["muted"])

        list_y = max_y + 58
        self._draw_recent_quakes(draw, (x0 + pad, list_y, x1 - pad, y1 - pad), quakes.get("recent_events") or [], palette)

    def _draw_kp_history(self, draw, box, history, palette):
        x0, y0, x1, y1 = box
        draw.rounded_rectangle(box, radius=9, fill=palette["subpanel"], outline=palette["rule"], width=1)
        inner = (x0 + 10, y0 + 8, x1 - 10, y1 - 10)
        draw.line((inner[0], inner[3], inner[2], inner[3]), fill=palette["dim"], width=1)
        bars = history[-8:] if history else []
        if not bars:
            draw.text((inner[0], inner[1] + 8), "Kp history unavailable", font=self._font(11), fill=palette["muted"])
            return
        bar_gap = 4
        bar_w = max(4, (inner[2] - inner[0] - bar_gap * (len(bars) - 1)) // len(bars))
        for index, item in enumerate(bars):
            kp = max(0.0, min(9.0, float(item.get("kp") or 0)))
            h = int((inner[3] - inner[1]) * (kp / 9.0))
            bx0 = inner[0] + index * (bar_w + bar_gap)
            color = palette["cyan"] if kp < 5 else palette["amber"] if kp < 7 else palette["red"]
            draw.rounded_rectangle((bx0, inner[3] - h, bx0 + bar_w, inner[3]), radius=3, fill=color)
        draw.text((inner[0], inner[1] - 1), "last Kp intervals", font=self._font(9), fill=palette["muted"])

    def _draw_alert_strip(self, draw, box, alerts, palette):
        x0, y0, x1, y1 = box
        alert = alerts[0] if alerts else None
        fill = palette["alert"] if alert else palette["subpanel"]
        draw.rounded_rectangle(box, radius=9, fill=fill, outline=palette["rule"], width=1)
        title_font = self._font(11, bold=True)
        body_font = self._font(11)
        if alert:
            code = str(alert.get("product_id") or "SWPC")
            headline = self._ellipsize(draw, str(alert.get("headline") or ""), body_font, x1 - x0 - 58)
            draw.text((x0 + 10, y0 + 8), code, font=title_font, fill=palette["ink"])
            draw.text((x0 + 10, y0 + 27), headline, font=body_font, fill=palette["ink"])
        else:
            draw.text((x0 + 10, y0 + 17), "No active SWPC alert in feed", font=body_font, fill=palette["muted"])

    def _draw_solar_chips(self, draw, box, space, palette):
        x0, y0, x1, y1 = box
        wind = space.get("solar_wind") or {}
        xray = space.get("xray") or {}
        chips = [
            ("X-RAY", str(xray.get("class") or "--")),
            ("WIND", self._unit(wind.get("speed"), "km/s", digits=0)),
            ("Bz", self._unit(wind.get("bz_gsm"), "nT", signed=True)),
        ]
        gap = 8
        chip_w = max(70, (x1 - x0 - gap * 2) // 3)
        for index, (label, value) in enumerate(chips):
            cx0 = x0 + index * (chip_w + gap)
            chip_box = (cx0, y0, min(x1, cx0 + chip_w), y1)
            draw.rounded_rectangle(chip_box, radius=9, fill=palette["subpanel"], outline=palette["rule"])
            draw.text((chip_box[0] + 8, chip_box[1] + 7), label, font=self._font(9, bold=True), fill=palette["muted"])
            draw.text((chip_box[0] + 8, chip_box[1] + 25), value, font=self._font(14, bold=True), fill=palette["ink"])

    def _draw_aurora_band(self, draw, box, aurora, palette):
        x0, y0, x1, y1 = box
        if y1 <= y0 + 28:
            return
        draw.rounded_rectangle(box, radius=9, fill=palette["subpanel"], outline=palette["rule"])
        max_value = self._int(aurora.get("max"), 0)
        north = aurora.get("north_peak_lat")
        south = aurora.get("south_peak_lat")
        label = f"AURORA max {max_value}  N {north if north is not None else '--'} deg  S {south if south is not None else '--'} deg"
        draw.text((x0 + 10, y0 + 8), label, font=self._font(10, bold=True), fill=palette["ink"])
        band_x0, band_y0, band_x1, band_y1 = x0 + 10, y0 + 28, x1 - 10, y1 - 10
        steps = 24
        for index in range(steps):
            t = index / max(1, steps - 1)
            intensity = math.sin(t * math.pi)
            color = self._blend(palette["aurora"], palette["panel"], 0.2 + intensity * 0.65)
            sx0 = int(band_x0 + (band_x1 - band_x0) * index / steps)
            sx1 = int(band_x0 + (band_x1 - band_x0) * (index + 1) / steps)
            draw.rectangle((sx0, band_y0, sx1, band_y1), fill=color)
        draw.line((band_x0, (band_y0 + band_y1) // 2, band_x1, (band_y0 + band_y1) // 2), fill=palette["background"], width=1)

    def _draw_quake_map(self, draw, box, events, max_event, palette):
        x0, y0, x1, y1 = box
        draw.rounded_rectangle(box, radius=9, fill=palette["subpanel"], outline=palette["rule"])
        for frac in (0.25, 0.5, 0.75):
            x = int(x0 + (x1 - x0) * frac)
            y = int(y0 + (y1 - y0) * frac)
            draw.line((x, y0 + 8, x, y1 - 8), fill=palette["dim"], width=1)
            draw.line((x0 + 8, y, x1 - 8, y), fill=palette["dim"], width=1)
        draw.arc((x0 + 14, y0 + 18, x1 - 14, y1 + 22), 190, 350, fill=palette["rule"], width=1)
        max_id = (max_event or {}).get("id")
        for event in events[:24]:
            lon = event.get("longitude")
            lat = event.get("latitude")
            if lon is None or lat is None:
                continue
            px = int(x0 + 10 + (x1 - x0 - 20) * ((float(lon) + 180.0) / 360.0))
            py = int(y0 + 10 + (y1 - y0 - 20) * ((90.0 - float(lat)) / 180.0))
            mag = float(event.get("mag") or 0)
            radius = max(2, min(8, int(2 + mag)))
            color = palette["red"] if event.get("id") == max_id or mag >= 5 else palette["amber"]
            draw.ellipse((px - radius, py - radius, px + radius, py + radius), fill=color, outline=palette["background"], width=1)
        draw.text((x0 + 10, y0 + 8), "global quake pulse", font=self._font(9, bold=True), fill=palette["muted"])

    def _load_quake_map(self, settings, device_config, quakes, target_size):
        mode = str(settings.get("quakeMapMode") or settings.get("mapMode") or "auto").strip().lower()
        if mode in {"drawn", "manual", "off", "none"}:
            return None
        key = self._google_maps_api_key(settings, device_config)
        if not key:
            return None
        url = self._google_quake_map_url(settings, key, quakes, target_size)
        if not url:
            return None

        cache_hours = bounded_int(settings.get("mapCacheHours") or settings.get("map_cache_hours"), DEFAULT_MAP_CACHE_HOURS, 1, 168)
        cache_file = self._map_cache_file(url)
        try:
            if cache_file.is_file() and time.time() - cache_file.stat().st_mtime < cache_hours * 3600:
                with Image.open(cache_file) as cached:
                    return cached.convert("RGB")
        except Exception as exc:
            logger.debug("Could not use cached EarthspacePulse map image: %s", exc)

        timeout = bounded_int(settings.get("mapTimeoutSeconds") or settings.get("map_timeout_seconds"), 8, 3, 15)
        try:
            response = get_http_session().get(url, headers=IMAGE_HEADERS, timeout=(4, timeout))
            response.raise_for_status()
            if len(response.content) > 4 * 1024 * 1024:
                raise RuntimeError("map image too large")
            with Image.open(BytesIO(response.content)) as loaded:
                image = ImageOps.exif_transpose(loaded).convert("RGB")
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            image.save(cache_file)
            return image
        except Exception as exc:
            logger.warning("EarthspacePulse Google map fetch failed: %s", exc)
            return None

    def _draw_google_quake_map(self, image, draw, box, map_image, palette):
        x0, y0, x1, y1 = [int(value) for value in box]
        width = max(1, x1 - x0)
        height = max(1, y1 - y0)
        resampling = getattr(getattr(Image, "Resampling", Image), "LANCZOS", Image.BICUBIC)
        fitted = ImageOps.fit(map_image.convert("RGB"), (width, height), method=resampling)
        image.paste(fitted, (x0, y0))
        draw.rounded_rectangle(box, radius=9, outline=palette["rule"], width=2)
        draw.rounded_rectangle((x0 + 8, y0 + 7, x0 + 118, y0 + 24), radius=6, fill=palette["chip"], outline=palette["rule"])
        draw.text((x0 + 14, y0 + 10), "Google quake map", font=self._font(9, bold=True), fill=palette["ink"])

    def _google_maps_api_key(self, settings, device_config):
        explicit = str(settings.get("googleMapsApiKey") or settings.get("google_maps_api_key") or "").strip()
        if explicit:
            return explicit
        for key in ("GOOGLE_MAPS_API_KEY", "Google_KEY", "GOOGLE_KEY"):
            value = self._load_env(device_config, key)
            if value:
                return value
        return ""

    def _google_quake_map_url(self, settings, api_key, quakes, target_size):
        width = max(160, min(640, int(target_size[0])))
        height = max(80, min(640, int(target_size[1])))
        zoom = bounded_int(settings.get("quakeMapZoom") or settings.get("mapZoom"), 1, 1, 6)
        map_type = str(settings.get("googleMapType") or "terrain").strip().lower()
        if map_type not in {"roadmap", "terrain", "satellite", "hybrid"}:
            map_type = "terrain"
        map_theme = str(settings.get("googleMapTheme") or "").strip().lower()
        if map_theme not in {"day", "night"}:
            map_theme = "day" if str(settings.get("themeMode") or "").strip().lower() == "paper" else "night"

        max_event = (quakes or {}).get("max_event") or {}
        max_id = max_event.get("id")
        events = []
        seen = set()
        for event in [max_event] + list((quakes or {}).get("recent_events") or []):
            lat_lon = self._event_lat_lon(event)
            event_id = str((event or {}).get("id") or lat_lon or len(events))
            if not lat_lon or event_id in seen:
                continue
            seen.add(event_id)
            events.append(event)
            if len(events) >= 12:
                break
        if not events:
            return ""

        params = [
            ("center", "15,0"),
            ("zoom", str(zoom)),
            ("size", f"{width}x{height}"),
            ("scale", "1"),
            ("format", "png"),
            ("maptype", map_type),
            *[("style", style) for style in self._google_map_styles(map_theme)],
        ]
        for event in events:
            lat, lon = self._event_lat_lon(event)
            mag = float((event or {}).get("mag") or 0)
            color = "0xf25a4e" if (event or {}).get("id") == max_id or mag >= 5 else "0xf6b24a"
            label = "|label:M" if (event or {}).get("id") == max_id else ""
            params.append(("markers", f"color:{color}|size:small{label}|{lat:.5f},{lon:.5f}"))
        params.append(("key", api_key))
        return f"{GOOGLE_STATIC_MAPS_URL}?{urlencode(params)}"

    @staticmethod
    def _google_map_styles(map_theme):
        base = [
            "feature:poi|visibility:off",
            "feature:transit|visibility:off",
            "feature:road|element:labels|visibility:off",
        ]
        if map_theme == "night":
            return base + [
                "feature:all|element:labels.text.fill|color:0xf2eee0",
                "feature:all|element:labels.text.stroke|color:0x111721",
                "feature:water|element:geometry|color:0x173548",
                "feature:landscape|element:geometry|color:0x202a24",
                "feature:road|element:geometry|color:0x5e4d33",
                "feature:administrative|element:geometry|color:0x4e5b63",
            ]
        return base + [
            "feature:all|element:labels.text.fill|color:0x4d5358",
            "feature:all|element:labels.text.stroke|color:0xf8f4e0",
            "feature:water|element:geometry|color:0x6bc2d6",
            "feature:landscape|element:geometry|color:0xf2ead0",
            "feature:road|element:geometry|color:0xd8b778",
            "feature:administrative|element:geometry|color:0x687070",
        ]

    def _map_cache_file(self, url):
        digest = hashlib.sha1(str(url).encode("utf-8")).hexdigest()[:18]
        return self._cache_dir() / f"map_{digest}.png"

    @staticmethod
    def _event_lat_lon(event):
        if not isinstance(event, dict):
            return None
        try:
            lat = float(event.get("latitude"))
            lon = float(event.get("longitude"))
        except (TypeError, ValueError):
            return None
        return lat, lon

    @staticmethod
    def _load_env(device_config, key):
        try:
            if hasattr(device_config, "load_env_key"):
                return str(device_config.load_env_key(key) or "").strip()
        except Exception:
            pass
        return str(os.getenv(key, "") or "").strip()
    def _draw_recent_quakes(self, draw, box, events, palette):
        x0, y0, x1, y1 = box
        font = self._font(11)
        label_font = self._font(10, bold=True)
        row_h = max(30, (y1 - y0) // max(1, min(len(events), 4))) if events else 30
        for index, event in enumerate(events[:4]):
            ry0 = y0 + index * row_h
            if ry0 + row_h > y1 + 3:
                break
            if index:
                draw.line((x0, ry0, x1, ry0), fill=palette["rule"], width=1)
            mag = self._mag_text(event)
            mag_color = palette["red"] if (event.get("mag") or 0) >= 5 else palette["amber"]
            draw.text((x0, ry0 + 5), mag, font=label_font, fill=mag_color)
            place = self._ellipsize(draw, str(event.get("place") or ""), font, x1 - x0 - 54)
            draw.text((x0 + 48, ry0 + 3), place, font=font, fill=palette["ink"])
            meta = self._quake_meta(event)
            draw.text((x0 + 48, ry0 + 18), meta, font=self._font(9), fill=palette["muted"])
        if not events:
            draw.text((x0, y0 + 6), "No earthquake records in feed", font=font, fill=palette["muted"])

    def _draw_footer(self, draw, box, payload, palette):
        x0, y0, x1, y1 = box
        status = payload.get("status") or {}
        sources = status.get("source_urls") or []
        label = "Sources: NOAA SWPC + USGS GeoJSON"
        if status.get("source_state") == "local_sample":
            label += " | sample fallback"
        if sources:
            label += f" | {len(sources)} endpoints"
        draw.text((x0, y0 + 2), label, font=self._font(10), fill=palette["muted"])

    def _draw_panel(self, draw, box, palette, accent):
        draw.rounded_rectangle(box, radius=14, fill=palette["panel"], outline=palette["rule"], width=2)
        x0, y0, x1, _y1 = box
        draw.line((x0 + 14, y0 + 34, x1 - 14, y0 + 34), fill=accent, width=2)

    def _draw_background_grid(self, draw, box, palette):
        x0, y0, x1, y1 = box
        step = 40
        for x in range(x0, x1 + 1, step):
            draw.line((x, y0, x, y1), fill=palette["grid"], width=1)
        for y in range(y0, y1 + 1, step):
            draw.line((x0, y, x1, y), fill=palette["grid"], width=1)

    def _palette(self, settings):
        mode = str(settings.get("themeMode") or settings.get("theme") or DEFAULT_THEME).lower()
        if mode == "paper":
            return {
                "background": (232, 227, 210),
                "panel": (248, 244, 224),
                "subpanel": (238, 234, 217),
                "chip": (225, 221, 204),
                "alert": (255, 228, 176),
                "ink": (10, 12, 14),
                "muted": (77, 83, 88),
                "rule": (104, 112, 112),
                "dim": (186, 181, 164),
                "grid": (218, 213, 196),
                "cyan": (0, 112, 150),
                "aurora": (27, 153, 106),
                "amber": (198, 116, 21),
                "red": (190, 48, 40),
            }
        return {
            "background": (9, 13, 19),
            "panel": (21, 27, 36),
            "subpanel": (29, 37, 48),
            "chip": (34, 44, 57),
            "alert": (84, 58, 26),
            "ink": (242, 238, 224),
            "muted": (168, 178, 180),
            "rule": (78, 91, 99),
            "dim": (45, 55, 64),
            "grid": (16, 22, 30),
            "cyan": (79, 197, 220),
            "aurora": (79, 220, 145),
            "amber": (246, 178, 74),
            "red": (242, 90, 78),
        }

    def _cache_is_fresh(self, cache, now, refresh_seconds):
        if not isinstance(cache, dict) or cache.get("schema") != CACHE_SCHEMA_VERSION:
            return False
        payload = cache.get("payload")
        if not isinstance(payload, dict):
            return False
        generated = self._parse_datetime(cache.get("generated_at"))
        if not generated:
            return False
        return (now - generated).total_seconds() <= refresh_seconds

    def _read_cache(self):
        return self._read_json(self._cache_path(), {})

    def _write_cache(self, payload):
        self._write_json(self._cache_path(), payload)

    def _cache_dir(self):
        return self.cache_dir(env_var="INKYPI_EARTHSPACE_PULSE_CACHE", leaf="cache", create=True, strip=True)

    def _cache_path(self):
        return self._cache_dir() / "state.json"

    def _read_json(self, path, default):
        try:
            if Path(path).is_file():
                return json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Could not read EarthspacePulse cache %s: %s", path, exc)
        return default

    def _write_json(self, path, payload):
        try:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as exc:
            logger.warning("Could not write EarthspacePulse cache %s: %s", path, exc)

    def _write_context(self, payload, now):
        try:
            write_context(
                PLUGIN_ID,
                {
                    "schema": CACHE_SCHEMA_VERSION,
                    "generated_at": now.isoformat(),
                    "space_weather": payload.get("space_weather"),
                    "earthquakes": payload.get("earthquakes"),
                    "status": payload.get("status"),
                },
            )
        except Exception as exc:
            logger.debug("EarthspacePulse context write skipped: %s", exc)

    def _row_table_latest(self, rows):
        normalized = []
        for row in rows if isinstance(rows, list) else []:
            if isinstance(row, dict) and isinstance(row.get("value"), list):
                normalized.append(row.get("value"))
            elif isinstance(row, list):
                normalized.append(row)
        if len(normalized) < 2:
            return {}
        header = [str(value) for value in normalized[0]]
        for raw in reversed(normalized[1:]):
            if not raw or len(raw) != len(header):
                continue
            return {header[index]: raw[index] for index in range(len(header))}
        return {}

    def _alert_headline(self, message):
        for line in re.split(r"[\r\n]+", message):
            line = self._clean_text(line)
            if not line:
                continue
            lowered = line.lower()
            if lowered.startswith("space weather message code") or lowered.startswith("serial number") or lowered.startswith("issue time"):
                continue
            if "noaa space weather scale" in lowered:
                continue
            return line[:180]
        return ""

    def _kp_scale(self, kp):
        if kp is None:
            return "G0 quiet"
        if kp >= 9:
            return "G5 extreme"
        if kp >= 8:
            return "G4 severe"
        if kp >= 7:
            return "G3 strong"
        if kp >= 6:
            return "G2 moderate"
        if kp >= 5:
            return "G1 minor"
        return "G0 quiet"

    def _xray_class(self, flux):
        if flux is None or flux <= 0:
            return "--"
        levels = [("X", 1e-4), ("M", 1e-5), ("C", 1e-6), ("B", 1e-7), ("A", 1e-8)]
        for label, base in levels:
            if flux >= base:
                return f"{label}{flux / base:.1f}"
        return f"A{flux / 1e-8:.1f}"

    def _quake_feed(self, settings):
        feed = str(settings.get("quakeFeed") or settings.get("quake_feed") or "all_day")
        return feed if feed in USGS_FEEDS else "all_day"

    def _quake_feed_url(self, settings):
        return USGS_FEEDS[self._quake_feed(settings)]

    def _refresh_minutes(self, settings):
        return bounded_int(settings.get("refreshMinutes") or settings.get("refresh_minutes"), DEFAULT_REFRESH_MINUTES, 5, 720)

    def _max_quakes(self, settings):
        return bounded_int(settings.get("maxQuakes") or settings.get("max_quakes"), DEFAULT_MAX_QUAKES, 1, 8)

    def _location(self, settings):
        lat = self._optional_float(settings.get("latitude"))
        lon = self._optional_float(settings.get("longitude"))
        if lat is None or lon is None:
            return None
        return {"latitude": lat, "longitude": lon, "name": str(settings.get("locationName") or "selected location")}

    def _nearest_event(self, events, location):
        nearest = None
        for event in events:
            lat = event.get("latitude")
            lon = event.get("longitude")
            if lat is None or lon is None:
                continue
            distance = self._haversine_km(location["latitude"], location["longitude"], lat, lon)
            candidate = dict(event)
            candidate["distance_km"] = distance
            if nearest is None or distance < nearest["distance_km"]:
                nearest = candidate
        return nearest

    @staticmethod
    def _haversine_km(lat1, lon1, lat2, lon2):
        radius = 6371.0088
        phi1, phi2 = math.radians(float(lat1)), math.radians(float(lat2))
        dphi = math.radians(float(lat2) - float(lat1))
        dlambda = math.radians(float(lon2) - float(lon1))
        a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
        return radius * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    def _font(self, size, bold=False):
        weight = "bold" if bold else "normal"
        font = get_font("Jost", int(size), weight)
        if font:
            return font
        return ImageFont.load_default()

    def _text_width(self, draw, text, font):
        return text_width(draw, str(text), font)

    @staticmethod
    def _text_height(draw, text, font):
        bbox = draw.textbbox((0, 0), str(text), font=font)
        return bbox[3] - bbox[1]

    def _ellipsize(self, draw, text, font, max_width):
        text = self._clean_text(text)
        if self._text_width(draw, text, font) <= max_width:
            return text
        suffix = "..."
        while text and self._text_width(draw, text + suffix, font) > max_width:
            text = text[:-1]
        return (text.rstrip() + suffix) if text else suffix

    @staticmethod
    def _clean_text(value):
        return re.sub(r"\s+", " ", str(value or "")).strip()

    @staticmethod
    def _float(value):
        try:
            if value is None or value == "":
                return None
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _optional_float(value):
        try:
            if value is None or str(value).strip() == "":
                return None
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _int(value, default=0):
        try:
            if value is None or value == "":
                return default
            return int(float(value))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _enabled(value, default=False):
        return coerce_bool(value, default=default)

    @staticmethod
    def _millis_to_iso(value):
        try:
            millis = int(float(value))
            return datetime.fromtimestamp(millis / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except (TypeError, ValueError, OSError, OverflowError):
            return ""

    @staticmethod
    def _parse_datetime(value):
        if not value:
            return None
        text = str(value).replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except ValueError:
            return None

    @staticmethod
    def _format_time(value):
        parsed = EarthspacePulse._parse_datetime(value)
        if not parsed:
            return str(value or "")[:16]
        return parsed.strftime("%H:%M UTC")

    @staticmethod
    def _mag_text(event):
        mag = (event or {}).get("mag")
        return "M --" if mag is None else f"M {float(mag):.1f}"

    def _quake_meta(self, event):
        if not event:
            return ""
        depth = event.get("depth_km")
        time_iso = str(event.get("time_iso") or "")
        parts = []
        if time_iso:
            parts.append(time_iso[11:16] + " UTC")
        if depth is not None:
            parts.append(f"{float(depth):.0f} km")
        if event.get("tsunami"):
            parts.append("TSUNAMI")
        if event.get("alert"):
            parts.append(str(event.get("alert")).upper())
        return " | ".join(parts)

    @staticmethod
    def _signed(value):
        try:
            number = float(value)
        except (TypeError, ValueError):
            return "+0.0"
        return f"{number:+.1f}"

    @staticmethod
    def _unit(value, unit, digits=1, signed=False):
        try:
            number = float(value)
        except (TypeError, ValueError):
            return "--"
        sign = "+" if signed and number >= 0 else ""
        return f"{sign}{number:.{digits}f} {unit}"

    @staticmethod
    def _blend(foreground, background, amount):
        amount = max(0.0, min(1.0, float(amount)))
        return tuple(int(background[index] + (foreground[index] - background[index]) * amount) for index in range(3))
