from plugins.base_plugin.base_plugin import BasePlugin
from plugins.base_plugin.render_provenance import (
    SourceProvenance,
    attach_source_provenance,
)
from PIL import Image, ImageDraw
import os
import hashlib
import requests
import logging
from datetime import datetime, timedelta, timezone, date
from astral import moon
import pytz
from io import BytesIO
import math
from plugins.context_cache import write_context
from utils.theme_utils import (
    EFFECTIVE_THEME_CONTEXT_INFO_KEY,
    apply_theme_to_plugin_settings,
    canonical_weather_astronomy,
    pinned_theme_context,
)
from utils.plugin_cache import read_json, write_json
from utils.http_client import get_http_session
from utils.app_utils import get_base_ui_font

logger = logging.getLogger(__name__)
        
def get_moon_phase_name(phase_age: float) -> str:
    """Determines the name of the lunar phase based on the age of the moon."""
    PHASES_THRESHOLDS = [
        (1.0, "newmoon"),
        (7.0, "waxingcrescent"),
        (8.5, "firstquarter"),
        (14.0, "waxinggibbous"),
        (15.5, "fullmoon"),
        (22.0, "waninggibbous"),
        (23.5, "lastquarter"),
        (29.0, "waningcrescent"),
    ]

    for threshold, phase_name in PHASES_THRESHOLDS:
        if phase_age <= threshold:
            return phase_name  
    return "newmoon"

UNITS = {
    "standard": {
        "temperature": "K",
        "speed": "m/s",
        "distance":"km"
    },
    "metric": {
        "temperature": "°C",
        "speed": "m/s",
        "distance":"km"

    },
    "imperial": {
        "temperature": "°F",
        "speed": "mph",
        "distance":"mi"
    }
}

WEATHER_URL = "https://api.openweathermap.org/data/3.0/onecall?lat={lat}&lon={long}&units={units}&exclude=minutely&appid={api_key}"
AIR_QUALITY_URL = "https://api.openweathermap.org/data/2.5/air_pollution?lat={lat}&lon={long}&appid={api_key}"
GEOCODING_URL = "https://api.openweathermap.org/geo/1.0/reverse?lat={lat}&lon={long}&limit=1&appid={api_key}"

OPEN_METEO_FORECAST_URL = "https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={long}&hourly=weather_code,temperature_2m,precipitation,precipitation_probability,relative_humidity_2m,surface_pressure,visibility&daily=weathercode,temperature_2m_max,temperature_2m_min,sunrise,sunset&current=temperature,windspeed,winddirection,is_day,precipitation,weather_code,apparent_temperature&timezone=auto&models=best_match&forecast_days={forecast_days}"
OPEN_METEO_AIR_QUALITY_URL = "https://air-quality-api.open-meteo.com/v1/air-quality?latitude={lat}&longitude={long}&hourly=european_aqi,uv_index,uv_index_clear_sky&timezone=auto"
OPEN_METEO_UNIT_PARAMS = {
    "standard": "temperature_unit=celsius&wind_speed_unit=ms&precipitation_unit=mm",  # temperature is converted to Kelvin later
    "metric":   "temperature_unit=celsius&wind_speed_unit=ms&precipitation_unit=mm",
    "imperial": "temperature_unit=fahrenheit&wind_speed_unit=mph&precipitation_unit=inch"
}

OPENWEATHER_ONECALL_FREE_DAILY_MAX = 1000
OPENWEATHER_ONECALL_DAILY_LIMIT_DEFAULT = 900
OPENWEATHER_ONECALL_MIN_SECONDS_DEFAULT = 1800
OPENWEATHER_AUX_MIN_SECONDS_DEFAULT = 1800
OPENWEATHER_LOCATION_MIN_SECONDS_DEFAULT = 86400

class Weather(BasePlugin):
    def generate_settings_template(self):
        template_params = super().generate_settings_template()
        template_params['api_key'] = {
            "required": True,
            "service": "OpenWeatherMap",
            "expected_key": "OPEN_WEATHER_MAP_SECRET"
        }
        template_params['style_settings'] = True
        return template_params

    @staticmethod
    def _settings_for_theme(settings, theme_context):
        if not isinstance(theme_context, dict) or theme_context.get("mode") != "night":
            return dict(settings or {})
        return apply_theme_to_plugin_settings(settings, theme_context)

    def generate_image(self, settings, device_config):
        self._openweather_force_refresh = self._force_refresh_requested(settings)
        self._openweather_cache_hits = set()
        lat = float(settings.get('latitude'))
        long = float(settings.get('longitude'))
        if not lat or not long:
            raise RuntimeError("Latitude and Longitude are required.")

        units = settings.get('units')
        if not units or units not in ['metric', 'imperial', 'standard']:
            raise RuntimeError("Units are required.")

        weather_provider = settings.get('weatherProvider', 'OpenWeatherMap')
        title = settings.get('customTitle', '')

        timezone = device_config.get_config("timezone", default="America/New_York")
        time_format = device_config.get_config("time_format", default="12h")
        device_tz = pytz.timezone(timezone)
        effective_tz = device_tz
        self._openweather_request_metadata = {}

        try:
            if weather_provider == "OpenWeatherMap":
                api_key = device_config.load_env_key("OPEN_WEATHER_MAP_SECRET")
                if not api_key:
                    raise RuntimeError("Open Weather Map API Key not configured.")
                weather_data = self.get_weather_data(api_key, units, lat, long)
                aqi_data = self.get_air_quality(api_key, lat, long)
                if settings.get('titleSelection', 'location') == 'location':
                    title = self.get_location(api_key, lat, long)
                if settings.get('weatherTimeZone', 'locationTimeZone') == 'locationTimeZone':
                    logger.info("Using location timezone for OpenWeatherMap data.")
                    effective_tz = self.parse_timezone(weather_data)
                else:
                    logger.info("Using configured timezone for OpenWeatherMap data.")
                    effective_tz = device_tz
                template_params = self.parse_weather_data(
                    weather_data,
                    aqi_data,
                    effective_tz,
                    units,
                    time_format,
                    lat,
                )
            elif weather_provider == "OpenMeteo":
                forecast_days = 7
                location_timezone = (
                    settings.get('weatherTimeZone', 'locationTimeZone')
                    == 'locationTimeZone'
                )
                requested_timezone = "auto" if location_timezone else timezone
                weather_data = self.get_open_meteo_data(
                    lat,
                    long,
                    units,
                    forecast_days + 1,
                    timezone_name=requested_timezone,
                )
                aqi_data = self.get_open_meteo_air_quality(
                    lat,
                    long,
                    timezone_name=requested_timezone,
                )
                effective_timezone_name = (
                    weather_data.get("timezone")
                    if location_timezone
                    else timezone
                )
                try:
                    effective_tz = pytz.timezone(str(effective_timezone_name))
                except Exception as exc:
                    raise RuntimeError(
                        "Open-Meteo response did not contain a valid IANA timezone."
                    ) from exc
                template_params = self.parse_open_meteo_data(
                    weather_data,
                    aqi_data,
                    effective_tz,
                    units,
                    time_format,
                    lat,
                )
            else:
                raise RuntimeError(f"Unknown weather provider: {weather_provider}")

            template_params['title'] = title
        except Exception as e:
            logger.error(f"{weather_provider} request failed: {str(e)}")
            raise RuntimeError(f"{weather_provider} request failure, please check logs.")
       
        now = self._now(effective_tz)
        candidate_astronomy = template_params.get("astronomy")
        provider_stale = bool(
            weather_provider == "OpenWeatherMap"
            and self._openweather_request_metadata.get("onecall", {}).get("stale")
        )
        source_stale = bool(
            weather_provider == "OpenWeatherMap"
            and any(
                metadata.get("stale")
                for metadata in self._openweather_request_metadata.values()
                if isinstance(metadata, dict)
            )
        )
        canonical_astronomy = (
            None
            if provider_stale
            else canonical_weather_astronomy(
                candidate_astronomy,
                now=now,
            )
        )
        if canonical_astronomy is None:
            template_params.pop("astronomy", None)
        else:
            template_params["astronomy"] = canonical_astronomy
        template_params["data_points"] = self._canonical_sun_data_points(
            template_params.get("data_points"),
            canonical_astronomy,
            effective_tz,
            time_format,
        )

        # Add last refresh time before atomically publishing the canonical facts.
        if time_format == "24h":
            last_refresh_time = now.strftime("%Y-%m-%d %H:%M")
        else:
            last_refresh_time = now.strftime("%Y-%m-%d %I:%M %p")
        template_params["last_refresh_time"] = last_refresh_time

        if not source_stale and not self._write_weather_context(template_params, now):
            raise RuntimeError("Weather context publication failed.")

        theme_context = self.resolve_theme(
            settings,
            device_config,
            now=now,
            astronomy=canonical_astronomy or {},
        )
        template_params["theme"] = theme_context
        template_params["plugin_settings"] = self._settings_for_theme(
            settings,
            theme_context,
        )

        dimensions = self.get_dimensions(device_config)
        with pinned_theme_context(theme_context):
            image = self.render_image(
                dimensions,
                "weather.html",
                "weather.css",
                template_params,
            )

        if not image:
            logger.warning("Weather HTML render failed; using fresh-data Pillow fallback.")
            image = self._render_fallback_image(
                dimensions,
                template_params,
                theme_context,
            )
            image.info["inkypi_visual_fallback"] = "weather_pillow"
        image.info[EFFECTIVE_THEME_CONTEXT_INFO_KEY] = theme_context
        if weather_provider != "OpenWeatherMap":
            provenance = SourceProvenance.LIVE
        elif source_stale:
            provenance = SourceProvenance.STALE_CACHE
        elif self._openweather_cache_hits:
            provenance = SourceProvenance.FRESH_CACHE
        else:
            provenance = SourceProvenance.LIVE
        if provenance is SourceProvenance.STALE_CACHE:
            image.info["inkypi_skip_cache"] = True
        return attach_source_provenance(image, provenance)

    @staticmethod
    def _fit_fallback_font(draw, text, preferred, minimum, max_width, *, bold=True):
        value = str(text or "")
        for size in range(max(int(preferred), int(minimum)), int(minimum) - 1, -1):
            font = get_base_ui_font(size, bold=bold)
            bounds = draw.textbbox((0, 0), value, font=font)
            if bounds[2] - bounds[0] <= max_width:
                return font
        return get_base_ui_font(int(minimum), bold=bold)

    def _render_fallback_image(self, dimensions, template_params, theme_context):
        width, height = (int(dimensions[0]), int(dimensions[1]))
        night = str((theme_context or {}).get("mode") or "day").lower() == "night"
        palette = {
            "background": (0, 0, 0) if night else (255, 255, 255),
            "panel": (18, 18, 18) if night else (246, 244, 236),
            "ink": (255, 255, 255) if night else (0, 0, 0),
            "muted": (210, 210, 210) if night else (58, 58, 58),
            "rule": (255, 190, 0) if night else (0, 0, 0),
            "hot": (255, 92, 72) if night else (176, 36, 48),
            "cool": (89, 172, 255) if night else (25, 88, 158),
        }
        image = Image.new("RGB", (width, height), palette["background"])
        draw = ImageDraw.Draw(image)
        temperature = str(template_params.get("current_temperature") or "--")
        unit = str(template_params.get("temperature_unit") or "")
        current_value = f"{temperature}{unit}"

        if width < 240 or height < 120:
            font = self._fit_fallback_font(
                draw,
                current_value,
                max(10, int(height * 0.58)),
                8,
                max(8, width - 8),
            )
            bounds = draw.textbbox((0, 0), current_value, font=font)
            draw.text(
                (
                    max(2, (width - (bounds[2] - bounds[0])) // 2),
                    max(1, (height - (bounds[3] - bounds[1])) // 2 - bounds[1]),
                ),
                current_value,
                font=font,
                fill=palette["ink"],
            )
            draw.line((2, height - 2, width - 3, height - 2), fill=palette["rule"], width=1)
            return image

        scale = min(width / 800.0, height / 480.0)
        margin = max(12, int(22 * scale))
        gap = max(8, int(12 * scale))
        title = str(template_params.get("title") or "Weather")
        title_font = self._fit_fallback_font(
            draw,
            title,
            int(30 * scale),
            max(14, int(18 * scale)),
            width - margin * 2 - int(190 * scale),
        )
        meta_font = get_base_ui_font(max(10, int(14 * scale)), bold=True)
        draw.text((margin, margin), title, font=title_font, fill=palette["ink"])
        refreshed = str(template_params.get("last_refresh_time") or "")
        refreshed_box = draw.textbbox((0, 0), refreshed, font=meta_font)
        draw.text(
            (width - margin - (refreshed_box[2] - refreshed_box[0]), margin + 5),
            refreshed,
            font=meta_font,
            fill=palette["muted"],
        )
        header_bottom = margin + max(int(42 * scale), refreshed_box[3] - refreshed_box[1])
        draw.line(
            (margin, header_bottom, width - margin, header_bottom),
            fill=palette["rule"],
            width=max(2, int(3 * scale)),
        )

        current_top = header_bottom + gap
        current_bottom = current_top + int(150 * scale)
        left_width = int(width * 0.43)
        current_box = (margin, current_top, left_width, current_bottom)
        draw.rounded_rectangle(
            current_box,
            radius=max(6, int(10 * scale)),
            fill=palette["panel"],
            outline=palette["rule"],
            width=max(1, int(2 * scale)),
        )
        temperature_font = self._fit_fallback_font(
            draw,
            current_value,
            int(68 * scale),
            max(28, int(42 * scale)),
            current_box[2] - current_box[0] - margin,
        )
        draw.text(
            (current_box[0] + int(16 * scale), current_box[1] + int(12 * scale)),
            current_value,
            font=temperature_font,
            fill=palette["hot"],
        )
        feels = str(template_params.get("feels_like") or "--")
        forecast = list(template_params.get("forecast") or [])
        today = forecast[0] if forecast else {}
        detail = f"Feels {feels}{unit}"
        if today:
            detail += f"  High {today.get('high', '--')}  Low {today.get('low', '--')}"
        draw.text(
            (current_box[0] + int(18 * scale), current_box[3] - int(35 * scale)),
            detail,
            font=meta_font,
            fill=palette["ink"],
        )

        metrics = list(template_params.get("data_points") or [])[:6]
        metrics_left = current_box[2] + gap
        metrics_right = width - margin
        metric_width = max(1, (metrics_right - metrics_left - gap) // 2)
        metric_height = max(1, (current_bottom - current_top - gap * 2) // 3)
        metric_label_font = get_base_ui_font(max(9, int(12 * scale)), bold=True)
        metric_value_font = get_base_ui_font(max(11, int(18 * scale)), bold=True)
        for index, point in enumerate(metrics):
            column = index % 2
            row = index // 2
            x0 = metrics_left + column * (metric_width + gap)
            y0 = current_top + row * (metric_height + gap)
            x1 = x0 + metric_width
            y1 = y0 + metric_height
            draw.rounded_rectangle(
                (x0, y0, x1, y1),
                radius=max(4, int(7 * scale)),
                fill=palette["panel"],
                outline=palette["rule"],
                width=1,
            )
            label = str(point.get("label") or "Metric")
            value = f"{point.get('measurement', '--')}{point.get('unit') or ''}"
            draw.text((x0 + 9, y0 + 6), label, font=metric_label_font, fill=palette["muted"])
            draw.text((x0 + 9, y0 + int(24 * scale)), value, font=metric_value_font, fill=palette["ink"])

        forecast_top = current_bottom + gap
        visible_forecast = forecast[:4]
        if visible_forecast:
            card_gap = gap
            card_width = (width - margin * 2 - card_gap * (len(visible_forecast) - 1)) // len(visible_forecast)
            day_font = get_base_ui_font(max(10, int(14 * scale)), bold=True)
            temp_font = get_base_ui_font(max(12, int(20 * scale)), bold=True)
            for index, day in enumerate(visible_forecast):
                x0 = margin + index * (card_width + card_gap)
                x1 = x0 + card_width
                y1 = height - margin - int(24 * scale)
                draw.rounded_rectangle(
                    (x0, forecast_top, x1, y1),
                    radius=max(5, int(8 * scale)),
                    fill=palette["panel"],
                    outline=palette["rule"],
                    width=1,
                )
                label = str(day.get("day") or ("TODAY" if index == 0 else f"DAY {index + 1}"))
                values = f"{day.get('high', '--')} / {day.get('low', '--')}"
                draw.text((x0 + 10, forecast_top + 10), label, font=day_font, fill=palette["muted"])
                draw.text((x0 + 10, forecast_top + int(42 * scale)), values, font=temp_font, fill=palette["cool"])

        draw.text(
            (margin, height - margin - int(15 * scale)),
            "PIL SAFE MODE - LIVE WEATHER DATA",
            font=get_base_ui_font(max(8, int(10 * scale)), bold=True),
            fill=palette["muted"],
        )
        return image

    @staticmethod
    def _now(tz):
        return datetime.now(tz)

    def _write_weather_context(self, template_params, generated_at):
        title = str(template_params.get("title") or "Weather").strip()
        temperature = template_params.get("current_temperature")
        unit = str(template_params.get("temperature_unit") or "").strip()
        feels_like = template_params.get("feels_like")
        forecast = template_params.get("forecast") or []
        today = forecast[0] if forecast else {}

        facts = []
        for point in (template_params.get("data_points") or [])[:8]:
            label = str(point.get("label") or "").strip()
            measurement = point.get("measurement")
            point_unit = str(point.get("unit") or "").strip()
            if label and measurement not in (None, ""):
                facts.append({
                    "label": label,
                    "value": f"{measurement}{point_unit}",
                })

        summary_parts = [title]
        if temperature not in (None, ""):
            summary_parts.append(f"current {temperature}{unit}")
        if feels_like not in (None, ""):
            summary_parts.append(f"feels like {feels_like}{unit}")
        if today:
            summary_parts.append(f"today high {today.get('high')} low {today.get('low')}")

        payload = {
            "kind": "weather",
            "source": title,
            "summary": "; ".join(part for part in summary_parts if part),
            "facts": facts,
            "forecast": [
                {
                    "day": item.get("day"),
                    "high": item.get("high"),
                    "low": item.get("low"),
                }
                for item in forecast[:4]
            ],
        }
        icon_code = self._normalize_context_icon_code(
            template_params.get("current_day_icon")
        )
        background_slug = self._background_slug_for_icon_code(icon_code)
        if icon_code:
            payload["icon_code"] = icon_code
        if background_slug:
            payload["background_slug"] = background_slug
            # Keep the established consumer alias during the schema migration.
            payload["weather_background_slug"] = background_slug
        astronomy = template_params.get("astronomy")
        if isinstance(astronomy, dict):
            payload["astronomy"] = {
                "date": astronomy.get("date"),
                "timezone": astronomy.get("timezone"),
                "sunrise": astronomy.get("sunrise"),
                "sunset": astronomy.get("sunset"),
                "source": astronomy.get("source") or "weather",
            }

        return write_context(
            "weather",
            payload,
            generated_at=generated_at,
            ttl_seconds=2 * 60 * 60,
        )

    def _canonical_sun_data_points(
        self,
        data_points,
        astronomy,
        tz,
        time_format,
    ):
        points = [
            point
            for point in (data_points or [])
            if point.get("label") not in {"Sunrise", "Sunset"}
        ]
        if not astronomy:
            return points
        sunrise = datetime.fromisoformat(astronomy["sunrise"]).astimezone(tz)
        sunset = datetime.fromisoformat(astronomy["sunset"]).astimezone(tz)
        sun_points = []
        for label, value, icon in (
            ("Sunrise", sunrise, "sunrise.png"),
            ("Sunset", sunset, "sunset.png"),
        ):
            sun_points.append(
                {
                    "label": label,
                    "measurement": self.format_time(
                        value,
                        time_format,
                        include_am_pm=False,
                    ),
                    "unit": "" if time_format == "24h" else value.strftime("%p"),
                    "icon": self.get_plugin_dir(f"icons/{icon}"),
                }
            )
        return sun_points + points

    @staticmethod
    def _normalize_context_icon_code(value):
        icon_code = os.path.splitext(
            os.path.basename(str(value or "").strip())
        )[0].lower()
        if not icon_code:
            return None
        suffix = icon_code[-1] if icon_code[-1:] in {"d", "n"} else ""
        numeric_code = icon_code[:-1] if suffix else icon_code
        if not numeric_code.isdigit():
            return None
        return f"{numeric_code}{suffix}"

    @staticmethod
    def _background_slug_for_icon_code(icon_code):
        if not icon_code:
            return None
        suffix = icon_code[-1] if icon_code[-1:] in {"d", "n"} else "d"
        numeric_code = icon_code[:-1] if icon_code[-1:] in {"d", "n"} else icon_code
        if numeric_code in {"01", "022"}:
            return "clear_night" if suffix == "n" else "clear_day"
        if numeric_code in {"02", "03", "04"}:
            return "cloudy"
        if numeric_code in {
            "09", "10", "51", "53", "55", "56", "57", "61",
            "63", "65", "66", "67", "80", "81", "82",
        }:
            return "rain"
        if numeric_code in {"11", "95", "96", "99"}:
            return "thunderstorm"
        if numeric_code in {"13", "71", "73", "75", "77", "85", "86"}:
            return "snow"
        if numeric_code in {"45", "48", "50"}:
            return "fog"
        return None

    def parse_weather_data(self, weather_data, aqi_data, tz, units, time_format, lat):
        current = weather_data.get("current")
        daily_forecast = weather_data.get("daily", [])
        dt = datetime.fromtimestamp(current.get('dt'), tz=timezone.utc).astimezone(tz)
        current_icon = current.get("weather")[0].get("icon")
        icon_codes_to_preserve = ["01", "02", "10"]
        icon_code = current_icon[:2]
        current_suffix = current_icon[-1]

        if icon_code not in icon_codes_to_preserve:
            if current_icon.endswith('n'):
                current_icon = current_icon.replace("n", "d")
        data = {
            "current_date": dt.strftime("%A, %B %d"),
            "current_day_icon": self.get_plugin_dir(f'icons/{current_icon}.png'),
            "current_temperature": str(round(current.get("temp"))),
            "feels_like": str(round(current.get("feels_like"))),
            "temperature_unit": UNITS[units]["temperature"],
            "units": units,
            "time_format": time_format
        }
        data['astronomy'] = self.parse_openweather_astronomy(current, daily_forecast, tz)
        data['forecast'] = self.parse_forecast(weather_data.get('daily'), tz, current_suffix, lat)
        data['data_points'] = self.parse_data_points(weather_data, aqi_data, tz, units, time_format)

        data['hourly_forecast'] = self.parse_hourly(weather_data.get('hourly'), tz, time_format, units, daily_forecast)
        return data

    def parse_open_meteo_data(self, weather_data, aqi_data, tz, units, time_format, lat):
        current = weather_data.get("current", {})
        daily = weather_data.get('daily', {})
        dt = self._parse_local_datetime(current.get('time'), tz) if current.get('time') else self._now(tz)
        weather_code = current.get("weather_code", 0)
        is_day = current.get("is_day", 1)
        current_icon = self.map_weather_code_to_icon(weather_code, is_day)
        
        temperature_conversion = 273.15 if units == "standard" else 0.

        data = {
            "current_date": dt.strftime("%A, %B %d"),
            "current_day_icon": self.get_plugin_dir(f'icons/{current_icon}.png'),
            "current_temperature": str(round(current.get("temperature", 0) + temperature_conversion)),
            "feels_like": str(round(current.get("apparent_temperature", current.get("temperature", 0)) + temperature_conversion)),
            "temperature_unit": UNITS[units]["temperature"],
            "units": units,
            "time_format": time_format
        }
        data['astronomy'] = self.parse_open_meteo_astronomy(daily, tz)

        data['forecast'] = self.parse_open_meteo_forecast(weather_data.get('daily', {}), units, tz, is_day, lat)
        data['data_points'] = self.parse_open_meteo_data_points(weather_data, aqi_data, units, tz, time_format)
        
        data['hourly_forecast'] = self.parse_open_meteo_hourly(weather_data.get('hourly', {}), units, tz, time_format, daily.get('sunrise', []), daily.get('sunset', []))
        return data

    def parse_openweather_astronomy(self, current, daily_forecast, tz):
        daily_today = daily_forecast[0] if daily_forecast else {}
        sunrise_epoch = (current or {}).get("sunrise") or daily_today.get("sunrise")
        sunset_epoch = (current or {}).get("sunset") or daily_today.get("sunset")
        try:
            sunrise = datetime.fromtimestamp(sunrise_epoch, tz=timezone.utc).astimezone(tz) if sunrise_epoch else None
            sunset = datetime.fromtimestamp(sunset_epoch, tz=timezone.utc).astimezone(tz) if sunset_epoch else None
        except Exception:
            logger.warning("Could not parse OpenWeather sunrise/sunset data.", exc_info=True)
            return None
        return self._astronomy_payload(sunrise, sunset, tz)

    def parse_open_meteo_astronomy(self, daily, tz):
        sunrise_times = (daily or {}).get("sunrise") or []
        sunset_times = (daily or {}).get("sunset") or []
        sunrise = self._parse_local_datetime(sunrise_times[0], tz) if sunrise_times else None
        sunset = self._parse_local_datetime(sunset_times[0], tz) if sunset_times else None
        return self._astronomy_payload(sunrise, sunset, tz)

    def _parse_local_datetime(self, value, tz):
        try:
            parsed = datetime.fromisoformat(str(value))
        except Exception:
            return None
        if parsed.tzinfo is None:
            parsed = tz.localize(parsed) if hasattr(tz, "localize") else parsed.replace(tzinfo=tz)
        return parsed.astimezone(tz)

    def _astronomy_payload(self, sunrise, sunset, tz):
        if not sunrise or not sunset:
            return None
        return {
            "date": sunrise.date().isoformat(),
            "timezone": getattr(tz, "zone", None) or str(tz),
            "sunrise": sunrise.isoformat(),
            "sunset": sunset.isoformat(),
            "source": "weather",
        }

    def map_weather_code_to_icon(self, weather_code, is_day):

        icon = "01d" # Default to clear day icon
        
        if weather_code in [0]:   # Clear sky
            icon = "01d"
        elif weather_code in [1]: # Mainly clear
            icon = "022d"
        elif weather_code in [2]: # Partly cloudy
            icon = "02d"
        elif weather_code in [3]: # Overcast
            icon = "04d"
        elif weather_code in [51, 61, 80]: # Drizzle, showers, rain: Light
            icon = "51d"          
        elif weather_code in [53, 63, 81]: # Drizzle, showers, rain: Moderatr
            icon = "53d"
        elif weather_code in [55, 65, 82]: # Drizzle, showers, rain: Heavy
            icon = "09d"
        elif weather_code in [45]: # Fog
            icon = "50d"                       
        elif weather_code in [48]: # Icy fog
            icon = "48d"
        elif weather_code in [56, 66]: # Light freezing Drizzle
            icon = "56d"            
        elif weather_code in [57, 67]: # Freezing Drizzle
            icon = "57d"            
        elif weather_code in [71, 85]: # Snow fall: Slight
            icon = "71d"
        elif weather_code in [73]:     # Snow fall: Moderate
            icon = "73d"
        elif weather_code in [75, 86]: # Snow fall: Heavy
            icon = "13d"
        elif weather_code in [77]:     # Snow grain
            icon = "77d"
        elif weather_code in [95]: # Thunderstorm
            icon = "11d"
        elif weather_code in [96, 99]: # Thunderstorm with slight and heavy hail
            icon = "11d"

        if is_day == 0:
            if icon == "01d":
                icon = "01n"      # Clear sky night
            elif icon == "022d":
                icon = "022n"     # Mainly clear night
            elif icon == "02d":
                icon = "02n"      # Partly cloudy night                
            elif icon == "10d":
                icon = "10n"      # Rain night

        return icon

    def get_moon_phase_icon_path(self, phase_name: str, lat: float) -> str:
        """Determines the path to the moon icon, inverting it if the location is in the Southern Hemisphere."""
        # Waxing, Waning, First and Last quarter phases are inverted between hemispheres.
        if lat < 0: # Southern Hemisphere
            if phase_name == "waxingcrescent":
                phase_name = "waningcrescent"
            elif phase_name == "waxinggibbous":
                phase_name = "waninggibbous"
            elif phase_name == "waningcrescent":
                phase_name = "waxingcrescent"
            elif phase_name == "waninggibbous":
                phase_name = "waxinggibbous"
            elif phase_name == "firstquarter":
                phase_name = "lastquarter"
            elif phase_name == "lastquarter":
                phase_name = "firstquarter"
        
        return self.get_plugin_dir(f"icons/{phase_name}.png")

    def parse_forecast(self, daily_forecast, tz, current_suffix, lat):
        """
        - daily_forecast: list of daily entries from One‑Call v3 (each has 'dt', 'weather', 'temp', 'moon_phase')
        - tz: your target tzinfo (e.g. from zoneinfo or pytz)
        """
        PHASES = [
            (0.0, "newmoon"),
            (0.25, "firstquarter"),
            (0.5, "fullmoon"),
            (0.75, "lastquarter"),
            (1.0, "newmoon"),
        ]

        def choose_phase_name(phase: float) -> str:
            for target, name in PHASES:
                if math.isclose(phase, target, abs_tol=1e-3):
                    return name
            if 0.0 < phase < 0.25:
                return "waxingcrescent"
            elif 0.25 < phase < 0.5:
                return "waxinggibbous"
            elif 0.5 < phase < 0.75:
                return "waninggibbous"
            else:
                return "waningcrescent"

        forecast = []
        icon_codes_to_apply_current_suffix = ["01", "02", "10"]
        for day in daily_forecast:
            # --- weather icon ---
            weather_icon = day["weather"][0]["icon"]  # e.g. "10d", "01n"
            icon_code = weather_icon[:2]
            if icon_code in icon_codes_to_apply_current_suffix:
                weather_icon_base = weather_icon[:-1]
                weather_icon = weather_icon_base + current_suffix
            else:
                if weather_icon.endswith('n'):
                    weather_icon = weather_icon.replace("n", "d")
            weather_icon = f"{icon_code}d"        
            weather_icon_path = self.get_plugin_dir(f"icons/{weather_icon}.png")

            # --- moon phase & icon ---
            moon_phase = float(day["moon_phase"])  # [0.0–1.0]
            phase_name_north_hemi = choose_phase_name(moon_phase)
            moon_icon_path = self.get_moon_phase_icon_path(phase_name_north_hemi, lat)
            # --- true illumination percent, no decimals ---
            illum_fraction = (1 - math.cos(2 * math.pi * moon_phase)) / 2
            moon_pct = f"{illum_fraction * 100:.0f}"

            # --- date & temps ---
            dt = datetime.fromtimestamp(day["dt"], tz=timezone.utc).astimezone(tz)
            day_label = dt.strftime("%a")

            forecast.append(
                {
                    "day": day_label,
                    "high": int(day["temp"]["max"]),
                    "low": int(day["temp"]["min"]),
                    "icon": weather_icon_path,
                    "moon_phase_pct": moon_pct,
                    "moon_phase_icon": moon_icon_path,
                }
            )

        return forecast
        
    def parse_open_meteo_forecast(self, daily_data, units, tz, is_day, lat):
        """
        Parse the daily forecast from Open-Meteo API and calculate moon phase and illumination using the local 'astral' library.
        """
        times = daily_data.get('time', [])
        weather_codes = daily_data.get('weathercode', [])
        temp_max = daily_data.get('temperature_2m_max', [])
        temp_min = daily_data.get('temperature_2m_min', [])
        if units == "standard":
            temp_max = [T + 273.15 for T in temp_max]
            temp_min = [T + 273.15 for T in temp_min]

        forecast = []

        for i in range(0, len(times)): 
            dt = self._parse_local_datetime(times[i], tz)
            if dt is None:
                continue
            day_label = dt.strftime("%a")

            code = weather_codes[i] if i < len(weather_codes) else 0
            weather_icon = self.map_weather_code_to_icon(code, is_day=1)
            weather_icon_path = self.get_plugin_dir(f"icons/{weather_icon}.png")

            timestamp = int(dt.replace(hour=12, minute=0, second=0).timestamp())
            target_date: date = dt.date() + timedelta(days=1)

            try:
                phase_age = moon.phase(target_date)
                phase_name_north_hemi = get_moon_phase_name(phase_age)
                LUNAR_CYCLE_DAYS = 29.530588853
                phase_fraction = phase_age / LUNAR_CYCLE_DAYS
                illum_pct = (1 - math.cos(2 * math.pi * phase_fraction)) / 2 * 100
            except Exception as e:
                logger.error(f"Error calculating moon phase for {target_date}: {e}")
                illum_pct = 0
                phase_name_north_hemi = "newmoon"
            moon_icon_path = self.get_moon_phase_icon_path(phase_name_north_hemi, lat)

            forecast.append({
                "day": day_label,
                "high": int(temp_max[i]) if i < len(temp_max) else 0,
                "low": int(temp_min[i]) if i < len(temp_min) else 0,
                "icon": weather_icon_path,
                "moon_phase_pct": f"{illum_pct:.0f}",
                "moon_phase_icon": moon_icon_path
            })

        return forecast

    def parse_hourly(self, hourly_forecast, tz, time_format, units, daily_forecast):
        hourly = []
        icon_codes_to_preserve = ["01", "02", "10"]
        
        sun_map = {}
        for day in daily_forecast or []:
            try:
                day_date = datetime.fromtimestamp(
                    day.get("dt"),
                    tz=timezone.utc,
                ).astimezone(tz).date()
                sunrise = float(day.get("sunrise"))
                sunset = float(day.get("sunset"))
                if (
                    not math.isfinite(sunrise)
                    or not math.isfinite(sunset)
                    or sunrise >= sunset
                ):
                    continue
            except (TypeError, ValueError, OSError, OverflowError):
                continue
            sun_map[day_date] = (sunrise, sunset)
        
        for hour in hourly_forecast[:24]:
            dt_epoch = hour.get('dt')
            dt = datetime.fromtimestamp(dt_epoch, tz=timezone.utc).astimezone(tz)
            rain_mm = hour.get("rain", {}).get("1h", 0.0)
            snow_mm = hour.get("snow", {}).get("1h", 0.0)
            total_precip_mm = rain_mm + snow_mm
            sunrise, sunset = sun_map.get(dt.date(), (None, None))

            is_day = (
                sunrise is not None
                and sunset is not None
                and sunrise <= dt_epoch < sunset
            )
            suffix = 'd' if is_day else 'n'
        
            raw_icon = hour.get("weather", [{}])[0].get("icon", "01d")
            icon_base = raw_icon[:2]
            icon_name = f"{icon_base}{suffix}" if icon_base in icon_codes_to_preserve else f"{icon_base}d"
            
            if units == "imperial":
                precip_value = total_precip_mm / 25.4
            else:
                precip_value = total_precip_mm 
            hour_forecast = {
                "time": self.format_time(dt, time_format, hour_only=True),
                "temperature": int(hour.get("temp")),
                "precipitation": hour.get("pop"),
                "rain": round(precip_value, 2),
                "icon": self.get_plugin_dir(f'icons/{icon_name}.png')
            }
            hourly.append(hour_forecast)
        return hourly

    def parse_open_meteo_hourly(self, hourly_data, units, tz, time_format, sunrises, sunsets):
        hourly = []
        times = hourly_data.get('time', [])
        temperatures = hourly_data.get('temperature_2m', [])
        if units == "standard":
            temperatures = [temperature + 273.15 for temperature in temperatures]
        precipitation_probabilities = hourly_data.get('precipitation_probability', [])
        rain = hourly_data.get('precipitation', [])
        codes = hourly_data.get('weather_code', [])
        
        sun_map = {}
        for sr_s, ss_s in zip(sunrises, sunsets):
            sr_dt = self._parse_local_datetime(sr_s, tz)
            ss_dt = self._parse_local_datetime(ss_s, tz)
            if sr_dt is None or ss_dt is None:
                continue
            sun_map[sr_dt.date()] = (sr_dt, ss_dt)
        
        current_time_in_tz = self._now(tz)
        start_index = 0
        for i, time_str in enumerate(times):
            try:
                dt_hourly = self._parse_local_datetime(time_str, tz)
                if dt_hourly is None:
                    continue
                if dt_hourly.date() == current_time_in_tz.date() and dt_hourly.hour >= current_time_in_tz.hour:
                    start_index = i
                    break
                if dt_hourly.date() > current_time_in_tz.date():
                    break
            except ValueError:
                logger.warning(f"Could not parse time string {time_str} in hourly data.")
                continue

        sliced_times = times[start_index:]
        sliced_temperatures = temperatures[start_index:]
        sliced_precipitation_probabilities = precipitation_probabilities[start_index:]
        sliced_rain = rain[start_index:]
        sliced_codes = codes[start_index:]

        for i in range(min(24, len(sliced_times))):
            dt = self._parse_local_datetime(sliced_times[i], tz)
            if dt is None:
                continue
            sunrise, sunset = sun_map.get(dt.date(), (None, None))
            is_day = 0
            if sunrise and sunset:
                is_day = 1 if sunrise <= dt < sunset else 0
            code = sliced_codes[i] if i < len(sliced_codes) else 0
            icon_name = self.map_weather_code_to_icon(code, is_day)
            hour_forecast = {
                "time": self.format_time(dt, time_format, True),
                "temperature": int(sliced_temperatures[i]) if i < len(sliced_temperatures) else 0,
                "precipitation": (sliced_precipitation_probabilities[i] / 100) if i < len(sliced_precipitation_probabilities) else 0,
                "rain": (sliced_rain[i]) if i < len(sliced_rain) else 0,
                "icon": self.get_plugin_dir(f"icons/{icon_name}.png")
            }
            hourly.append(hour_forecast)
        return hourly

    def parse_data_points(self, weather, air_quality, tz, units, time_format):
        data_points = []

        wind_deg = weather.get('current', {}).get("wind_deg", 0)
        wind_arrow = self.get_wind_arrow(wind_deg)
        data_points.append({
            "label": "Wind",
            "measurement": weather.get('current', {}).get("wind_speed"),
            "unit": UNITS[units]["speed"],
            "icon": self.get_plugin_dir('icons/wind.png'),
            "arrow": wind_arrow
        })

        data_points.append({
            "label": "Humidity",
            "measurement": weather.get('current', {}).get("humidity"),
            "unit": '%',
            "icon": self.get_plugin_dir('icons/humidity.png')
        })

        data_points.append({
            "label": "Pressure",
            "measurement": weather.get('current', {}).get("pressure"),
            "unit": 'hPa',
            "icon": self.get_plugin_dir('icons/pressure.png')
        })

        data_points.append({
            "label": "UV Index",
            "measurement": weather.get('current', {}).get("uvi"),
            "unit": '',
            "icon": self.get_plugin_dir('icons/uvi.png')
        })

        visibility = weather.get('current', {}).get("visibility")
        if units == "imperial":
            # convert from m to mi
            visibility /= 1609.
            at_max_visibility = visibility >= 6.2
        else:
            # convert from m to km
            visibility /= 1000.
            at_max_visibility = visibility >= 10
        visibility_str = f"{visibility:.1f}"
        if at_max_visibility:
            visibility_str = u"\u2265" + visibility_str
        data_points.append({
            "label": "Visibility",
            "measurement": visibility_str,
            "unit": UNITS[units]["distance"],
            "icon": self.get_plugin_dir('icons/visibility.png')
        })

        aqi = air_quality.get('list', [])[0].get("main", {}).get("aqi")
        data_points.append({
            "label": "Air Quality",
            "measurement": aqi,
            "unit": ["Good", "Fair", "Moderate", "Poor", "Very Poor"][int(aqi)-1],
            "icon": self.get_plugin_dir('icons/aqi.png')
        })

        return data_points

    def parse_open_meteo_data_points(self, weather_data, aqi_data, units, tz, time_format):
        """Parses current data points from Open-Meteo API response."""
        data_points = []
        current_data = weather_data.get('current', {})
        hourly_data = weather_data.get('hourly', {})

        current_time = self._now(tz)

        # Wind
        wind_speed = current_data.get("windspeed", 0)
        wind_deg = current_data.get("winddirection", 0)
        wind_arrow = self.get_wind_arrow(wind_deg)
        wind_unit = UNITS[units]["speed"]
        data_points.append({
            "label": "Wind", "measurement": wind_speed, "unit": wind_unit,
            "icon": self.get_plugin_dir('icons/wind.png'), "arrow": wind_arrow
        })

        # Humidity
        current_humidity = "N/A"
        humidity_hourly_times = hourly_data.get('time', [])
        humidity_values = hourly_data.get('relative_humidity_2m', [])
        for i, time_str in enumerate(humidity_hourly_times):
            try:
                parsed_time = self._parse_local_datetime(time_str, tz)
                if parsed_time and parsed_time.hour == current_time.hour:
                    current_humidity = int(humidity_values[i])
                    break
            except ValueError:
                logger.warning(f"Could not parse time string {time_str} for humidity.")
                continue
        data_points.append({
            "label": "Humidity", "measurement": current_humidity, "unit": '%',
            "icon": self.get_plugin_dir('icons/humidity.png')
        })

        # Pressure
        current_pressure = "N/A"
        pressure_hourly_times = hourly_data.get('time', [])
        pressure_values = hourly_data.get('surface_pressure', [])
        for i, time_str in enumerate(pressure_hourly_times):
            try:
                parsed_time = self._parse_local_datetime(time_str, tz)
                if parsed_time and parsed_time.hour == current_time.hour:
                    current_pressure = int(pressure_values[i])
                    break
            except ValueError:
                logger.warning(f"Could not parse time string {time_str} for pressure.")
                continue
        data_points.append({
            "label": "Pressure", "measurement": current_pressure, "unit": 'hPa',
            "icon": self.get_plugin_dir('icons/pressure.png')
        })

        # UV Index
        uv_index_hourly_times = aqi_data.get('hourly', {}).get('time', [])
        uv_index_values = aqi_data.get('hourly', {}).get('uv_index', [])
        current_uv_index = "N/A"
        for i, time_str in enumerate(uv_index_hourly_times):
            try:
                parsed_time = self._parse_local_datetime(time_str, tz)
                if parsed_time and parsed_time.hour == current_time.hour:
                    current_uv_index = uv_index_values[i]
                    break
            except ValueError:
                logger.warning(f"Could not parse time string {time_str} for UV Index.")
                continue
        data_points.append({
            "label": "UV Index", "measurement": current_uv_index, "unit": '',
            "icon": self.get_plugin_dir('icons/uvi.png')
        })

        # Visibility
        current_visibility = None
        at_max_visibility = False
        visibility_hourly_times = hourly_data.get('time', [])
        visibility_values = hourly_data.get('visibility', [])
        if units == "imperial":
            visibility_conversion = 1/5280.     # ft to mi
            visibility_max = 6.2                # mi
        else:
            visibility_conversion = 0.001       # m to km
            visibility_max = 10.                # km
        for i, time_str in enumerate(visibility_hourly_times):
            try:
                parsed_time = self._parse_local_datetime(time_str, tz)
                if parsed_time and parsed_time.hour == current_time.hour:
                    current_visibility = visibility_values[i]*visibility_conversion
                    at_max_visibility = current_visibility >= visibility_max
                    break
            except ValueError:
                logger.warning(f"Could not parse time string {time_str} for visibility.")
                continue
        visibility_str = (
            "N/A"
            if current_visibility is None
            else f"{current_visibility:.1f}"
        )
        if current_visibility is not None and at_max_visibility:
            visibility_str = u"\u2265" + visibility_str
        data_points.append({
            "label": "Visibility", 
            "measurement": visibility_str, 
            "unit": UNITS[units]["distance"],
            "icon": self.get_plugin_dir('icons/visibility.png')
        })

        # Air Quality
        aqi_hourly_times = aqi_data.get('hourly', {}).get('time', [])
        aqi_values = aqi_data.get('hourly', {}).get('european_aqi', [])
        current_aqi = "N/A"
        for i, time_str in enumerate(aqi_hourly_times):
            try:
                parsed_time = self._parse_local_datetime(time_str, tz)
                if parsed_time and parsed_time.hour == current_time.hour:
                    current_aqi = round(aqi_values[i], 1)
                    break
            except ValueError:
                logger.warning(f"Could not parse time string {time_str} for AQI.")
                continue
        scale = ""
        if current_aqi and current_aqi != "N/A":
            scale = ["Good","Fair","Moderate","Poor","Very Poor","Ext Poor"][min(current_aqi//20,5)]
        data_points.append({
            "label": "Air Quality", "measurement": current_aqi,
            "unit": scale, "icon": self.get_plugin_dir('icons/aqi.png')
        })

        return data_points

    def get_wind_arrow(self, wind_deg: float) -> str:
        DIRECTIONS = [
            ("↓", 22.5),    # North (N)
            ("↙", 67.5),    # North-East (NE)
            ("←", 112.5),   # East (E)
            ("↖", 157.5),   # South-East (SE)
            ("↑", 202.5),   # South (S)
            ("↗", 247.5),   # South-West (SW)
            ("→", 292.5),   # West (W)
            ("↘", 337.5),   # North-West (NW)
            ("↓", 360.0)    # Wrap back to North
        ]
        wind_deg = wind_deg % 360
        for arrow, upper_bound in DIRECTIONS:
            if wind_deg < upper_bound:
                return arrow

        return "↑"

    def _read_env_int(self, name, default, minimum=None, maximum=None):
        raw_value = os.getenv(name)
        try:
            value = int(raw_value) if raw_value not in (None, "") else default
        except ValueError:
            logger.warning(f"Invalid {name} value '{raw_value}', using {default}.")
            value = default

        if minimum is not None:
            value = max(minimum, value)
        if maximum is not None:
            if value > maximum:
                logger.warning(f"{name}={value} is above the hard cap {maximum}; using {maximum}.")
            value = min(maximum, value)
        return value

    def _openweather_cache_dir(self):
        cache_dir = os.getenv("OPENWEATHER_CACHE_DIR")
        runtime_root = os.getenv("INKYPI_CACHE_DIR", "").strip()
        if not cache_dir:
            if runtime_root:
                cache_dir = os.path.join(os.path.expanduser(runtime_root), "weather")
            else:
                cache_dir = os.path.join(os.path.dirname(__file__), ".openweather_cache")
        elif not os.path.isabs(cache_dir):
            if runtime_root:
                cache_dir = os.path.join(os.path.expanduser(runtime_root), "weather", cache_dir)
            else:
                cache_dir = os.path.join(os.path.dirname(__file__), cache_dir)
        os.makedirs(cache_dir, exist_ok=True)
        return cache_dir

    def _read_json_file(self, path, default):
        return read_json(path, default=default)

    def _write_json_file(self, path, data):
        write_json(path, data, ensure_ascii=False, indent=None)

    def _cache_path_for_url(self, cache_dir, namespace, url):
        key = hashlib.sha256(url.encode("utf-8")).hexdigest()[:32]
        return os.path.join(cache_dir, f"{namespace}_{key}.json")

    def _read_cache_entry(self, path):
        entry = self._read_json_file(path, None)
        if not isinstance(entry, dict) or "data" not in entry:
            return None
        return entry

    def _cache_entry_age_seconds(self, entry, now):
        fetched_at = entry.get("fetched_at")
        if not fetched_at:
            return None
        try:
            fetched_dt = datetime.fromisoformat(fetched_at)
        except ValueError:
            return None
        if fetched_dt.tzinfo is None:
            fetched_dt = fetched_dt.replace(tzinfo=timezone.utc)
        return (now - fetched_dt).total_seconds()

    def _onecall_usage_state(self, cache_dir):
        state_path = os.path.join(cache_dir, "onecall_usage.json")
        today = datetime.now(timezone.utc).date().isoformat()
        state = self._read_json_file(state_path, {})
        if state.get("date") != today:
            state = {"date": today, "onecall_requests": 0}
        try:
            state["onecall_requests"] = int(state.get("onecall_requests", 0))
        except (TypeError, ValueError):
            state["onecall_requests"] = 0
        return state_path, state

    def _remember_openweather_request(self, namespace, entry):
        metadata = getattr(self, "_openweather_request_metadata", None)
        if not isinstance(metadata, dict):
            metadata = {}
            self._openweather_request_metadata = metadata
        metadata[namespace] = {
            "fetched_at": entry.get("fetched_at"),
            "stale": bool(entry.get("stale")),
        }

    def _use_stale_cache_or_raise(self, cache_path, entry, message, namespace):
        if entry:
            logger.warning(f"{message}; using cached OpenWeather data.")
            entry["stale"] = True
            self._write_json_file(cache_path, entry)
            self._remember_openweather_request(namespace, entry)
            return entry["data"]
        raise RuntimeError(message)

    def _request_openweather_json(self, url, namespace, min_seconds, daily_limit=None):
        cache_dir = self._openweather_cache_dir()
        cache_path = self._cache_path_for_url(cache_dir, namespace, url)
        cache_entry = self._read_cache_entry(cache_path)
        now = datetime.now(timezone.utc)
        force_refresh = bool(getattr(self, "_openweather_force_refresh", False))

        if cache_entry and not force_refresh:
            age_seconds = self._cache_entry_age_seconds(cache_entry, now)
            if age_seconds is not None and age_seconds < min_seconds:
                logger.info(f"Using cached OpenWeather {namespace} data.")
                self._remember_openweather_request(namespace, cache_entry)
                cache_hits = getattr(self, "_openweather_cache_hits", None)
                if not isinstance(cache_hits, set):
                    cache_hits = set()
                    self._openweather_cache_hits = cache_hits
                cache_hits.add(namespace)
                return cache_entry["data"]

        if daily_limit is not None:
            state_path, state = self._onecall_usage_state(cache_dir)
            # An explicit administrator force refresh is also the live-acceptance
            # path: keep accounting the call, but do not let the local safety
            # threshold turn that request into a stale-cache false positive.
            if state["onecall_requests"] >= daily_limit and not force_refresh:
                message = f"OpenWeather One Call daily safety limit reached ({daily_limit})."
                return self._use_stale_cache_or_raise(
                    cache_path,
                    cache_entry,
                    message,
                    namespace,
                )
            state["onecall_requests"] += 1
            self._write_json_file(state_path, state)

        try:
            response = get_http_session().get(url)
        except requests.RequestException as e:
            message = f"OpenWeather {namespace} request failed: {type(e).__name__}"
            return self._use_stale_cache_or_raise(
                cache_path,
                cache_entry,
                message,
                namespace,
            )

        if not 200 <= response.status_code < 300:
            message = f"OpenWeather {namespace} request returned HTTP {response.status_code}."
            return self._use_stale_cache_or_raise(
                cache_path,
                cache_entry,
                message,
                namespace,
            )

        try:
            data = response.json()
        except ValueError:
            message = f"OpenWeather {namespace} response was not valid JSON."
            return self._use_stale_cache_or_raise(
                cache_path,
                cache_entry,
                message,
                namespace,
            )

        fresh_entry = {
            "fetched_at": now.isoformat(),
            "stale": False,
            "data": data
        }
        self._write_json_file(cache_path, fresh_entry)
        self._remember_openweather_request(namespace, fresh_entry)
        return data

    def get_weather_data(self, api_key, units, lat, long):
        url = WEATHER_URL.format(lat=lat, long=long, units=units, api_key=api_key)
        daily_limit = self._read_env_int(
            "OPENWEATHER_ONECALL_DAILY_LIMIT",
            OPENWEATHER_ONECALL_DAILY_LIMIT_DEFAULT,
            minimum=0,
            maximum=OPENWEATHER_ONECALL_FREE_DAILY_MAX,
        )
        min_seconds = self._read_env_int(
            "OPENWEATHER_ONECALL_MIN_SECONDS",
            OPENWEATHER_ONECALL_MIN_SECONDS_DEFAULT,
            minimum=600,
        )
        return self._request_openweather_json(url, "onecall", min_seconds, daily_limit=daily_limit)

    def get_air_quality(self, api_key, lat, long):
        url = AIR_QUALITY_URL.format(lat=lat, long=long, api_key=api_key)
        min_seconds = self._read_env_int(
            "OPENWEATHER_AUX_MIN_SECONDS",
            OPENWEATHER_AUX_MIN_SECONDS_DEFAULT,
            minimum=600,
        )
        return self._request_openweather_json(url, "air_quality", min_seconds)

    def get_location(self, api_key, lat, long):
        url = GEOCODING_URL.format(lat=lat, long=long, api_key=api_key)
        min_seconds = self._read_env_int(
            "OPENWEATHER_LOCATION_MIN_SECONDS",
            OPENWEATHER_LOCATION_MIN_SECONDS_DEFAULT,
            minimum=3600,
        )
        location_response = self._request_openweather_json(url, "geocoding", min_seconds)
        location_data = location_response[0]
        location_str = f"{location_data.get('name')}, {location_data.get('state', location_data.get('country'))}"

        return location_str

    def get_open_meteo_data(
        self,
        lat,
        long,
        units,
        forecast_days,
        *,
        timezone_name="auto",
    ):
        unit_params = OPEN_METEO_UNIT_PARAMS[units]
        url = OPEN_METEO_FORECAST_URL.format(lat=lat, long=long, forecast_days=forecast_days) + f"&{unit_params}"
        url = url.replace("timezone=auto", f"timezone={timezone_name}")
        response = get_http_session().get(url)

        if not 200 <= response.status_code < 300:
            logger.error(f"Failed to retrieve Open-Meteo weather data: {response.content}")
            raise RuntimeError("Failed to retrieve Open-Meteo weather data.")
        
        return response.json()

    def get_open_meteo_air_quality(self, lat, long, *, timezone_name="auto"):
        url = OPEN_METEO_AIR_QUALITY_URL.format(lat=lat, long=long)
        url = url.replace("timezone=auto", f"timezone={timezone_name}")
        response = get_http_session().get(url)
        if not 200 <= response.status_code < 300:
            logger.error(f"Failed to retrieve Open-Meteo air quality data: {response.content}")
            raise RuntimeError("Failed to retrieve Open-Meteo air quality data.")
        
        return response.json()

    @staticmethod
    def _force_refresh_requested(settings):
        truthy = {"1", "true", "yes", "on"}
        for key in ("forceRefresh", "force_refresh"):
            value = (settings or {}).get(key)
            if isinstance(value, bool):
                if value:
                    return True
            elif str(value or "").strip().lower() in truthy:
                return True
        return False
    
    def format_time(self, dt, time_format, hour_only=False, include_am_pm=True):
        """Format datetime based on 12h or 24h preference"""
        if time_format == "24h":
            return dt.strftime("%H:00" if hour_only else "%H:%M")
        
        if include_am_pm:
            fmt = "%I %p" if hour_only else "%I:%M %p"
        else:
            fmt = "%I" if hour_only else "%I:%M"

        return dt.strftime(fmt).lstrip("0")
    
    def parse_timezone(self, weatherdata):
        """Parse timezone from weather data"""
        if 'timezone' in weatherdata:
            logger.info(f"Using timezone from weather data: {weatherdata['timezone']}")
            return pytz.timezone(weatherdata['timezone'])
        else:
            logger.error("Failed to retrieve Timezone from weather data")
            raise RuntimeError("Timezone not found in weather data.")
