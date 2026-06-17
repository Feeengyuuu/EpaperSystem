import logging
import hashlib
import re
import unicodedata
from pathlib import Path

import pytz
import requests
import datetime
from PIL import Image, ImageDraw, ImageFont, ImageOps

from plugins.weather.weather import UNITS, Weather
from utils.app_utils import get_font
from utils.theme_utils import apply_theme_to_plugin_settings, get_theme_context, get_theme_palette

logger = logging.getLogger(__name__)

REVERSE_GEOCODE_URL = (
    "https://nominatim.openstreetmap.org/reverse"
    "?lat={lat}&lon={long}&format=jsonv2&addressdetails=1&zoom=10"
)

# Simple in-memory cache for reverse-geocoded titles to avoid hitting Nominatim
# on every refresh. Keys are rounded coordinate pairs to tolerate tiny changes.
REVERSE_GEOCODE_CACHE = {}
# TTL for successful reverse geocode results (seconds)
REVERSE_GEOCODE_SUCCESS_TTL = 7 * 24 * 60 * 60  # 7 days
# TTL for failed attempts (seconds) to avoid tight retry loops
REVERSE_GEOCODE_FAIL_TTL = 60 * 60  # 1 hour
REVERSE_GEOCODE_ROUND_DECIMALS = 4

QUICK_LOCATION_LABELS = {
    "52.3676,4.9041": "Amsterdam",
    "52.5200,13.4050": "Berlin",
    "-34.6037,-58.3816": "Buenos Aires",
    "-6.2088,106.8456": "Jakarta",
    "51.5074,-0.1278": "London",
    "40.4168,-3.7038": "Madrid",
    "40.7128,-74.0060": "New York",
    "48.8566,2.3522": "Paris",
    "-22.9068,-43.1729": "Rio de Janeiro",
    "41.9028,12.4964": "Rome",
    "-23.5505,-46.6333": "São Paulo",
    "35.6762,139.6503": "Tokyo",
}

QUICK_LOCATION_COORDS = {
    city: tuple(map(float, coords.split(",")))
    for coords, city in QUICK_LOCATION_LABELS.items()
}

WEATHER_BACKGROUND_DEFAULT = "cloudy"
WEATHER_BACKGROUND_DEFAULT_STYLE = "mythic_comic_1982"
WEATHER_BACKGROUND_STYLES = {
    "classic": (),
    "mythic_comic_1982": ("mythic_comic_1982",),
}
WEATHER_ICON_DEFAULT_STYLE = "shanghai_animation"
WEATHER_ICON_STYLES = {
    "classic": None,
    "shanghai_animation": "shanghai_animation",
}
WEATHER_BACKGROUND_BY_ICON = {
    "01d": "clear_day",
    "01n": "clear_night",
    "022d": "clear_day",
    "022n": "clear_night",
    "02d": "cloudy",
    "02n": "cloudy",
    "03d": "cloudy",
    "03n": "cloudy",
    "04d": "cloudy",
    "04n": "cloudy",
    "09d": "rain",
    "09n": "rain",
    "10d": "rain",
    "10n": "rain",
    "11d": "thunderstorm",
    "11n": "thunderstorm",
    "13d": "snow",
    "13n": "snow",
    "48d": "fog",
    "48n": "fog",
    "50d": "fog",
    "50n": "fog",
    "51d": "rain",
    "51n": "rain",
    "53d": "rain",
    "53n": "rain",
    "56d": "rain",
    "56n": "rain",
    "57d": "rain",
    "57n": "rain",
    "71d": "snow",
    "71n": "snow",
    "73d": "snow",
    "73n": "snow",
    "77d": "snow",
    "77n": "snow",
}

LANGUAGE_LABELS = {
    "de": {
        "now": "JETZT",
        "days": ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"],
    },
    "en": {
        "now": "NOW",
        "days": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
    },
    "es": {
        "now": "AHORA",
        "days": ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"],
    },
    "fr": {
        "now": "MAINT",
        "days": ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"],
    },
    "id": {
        "now": "SEK",
        "days": ["Sen", "Sel", "Rab", "Kam", "Jum", "Sab", "Min"],
    },
    "it": {
        "now": "ORA",
        "days": ["Lun", "Mar", "Mer", "Gio", "Ven", "Sab", "Dom"],
    },
    "nl": {
        "now": "NU",
        "days": ["Ma", "Di", "Wo", "Do", "Vr", "Za", "Zo"],
    },
    "pt": {
        "now": "AGORA",
        "days": ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"],
    },
}

# month names for a handful of supported languages; keep capitalized first letter
MONTH_NAMES = {
    "en": [
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    ],
    "pt": [
        "janeiro",
        "fevereiro",
        "março",
        "abril",
        "maio",
        "junho",
        "julho",
        "agosto",
        "setembro",
        "outubro",
        "novembro",
        "dezembro",
    ],
    "es": [
        "enero",
        "febrero",
        "marzo",
        "abril",
        "mayo",
        "junio",
        "julio",
        "agosto",
        "septiembre",
        "octubre",
        "noviembre",
        "diciembre",
    ],
    "fr": [
        "janvier",
        "février",
        "mars",
        "avril",
        "mai",
        "juin",
        "juillet",
        "août",
        "septembre",
        "octobre",
        "novembre",
        "décembre",
    ],
    "de": [
        "Januar",
        "Februar",
        "März",
        "April",
        "Mai",
        "Juni",
        "Juli",
        "August",
        "September",
        "Oktober",
        "November",
        "Dezember",
    ],
    "it": [
        "gennaio",
        "febbraio",
        "marzo",
        "aprile",
        "maggio",
        "giugno",
        "luglio",
        "agosto",
        "settembre",
        "ottobre",
        "novembre",
        "dicembre",
    ],
    "nl": [
        "januari",
        "februari",
        "maart",
        "april",
        "mei",
        "juni",
        "juli",
        "augustus",
        "september",
        "oktober",
        "november",
        "december",
    ],
    "id": [
        "Januari",
        "Februari",
        "Maret",
        "April",
        "Mei",
        "Juni",
        "Juli",
        "Agustus",
        "September",
        "Oktober",
        "November",
        "Desember",
    ],
}


def format_localized_date(language, dt):
    """Return a short localized date string for the given language and datetime.

    Examples:
      en -> "March 25, 2026"
      pt -> "25 de março de 2026"
      fr/de/it/nl/es/id -> "25 mars 2026"
    """
    lang = (language or "").lower()
    # Support full locale codes like en-US or de-DE by normalizing to the short prefix
    short = lang.split("-")[0].split("_")[0]
    months = MONTH_NAMES.get(short, MONTH_NAMES.get("en"))
    raw_month = months[dt.month - 1]

    day = dt.day
    year = dt.year

    # Capitalization rules
    # - English: capitalize month (e.g., March)
    # - French: lowercase month (e.g., mars)
    # - Other languages: use the form provided in MONTH_NAMES
    if short == "en":
        month = raw_month[0].upper() + raw_month[1:]
    elif short == "fr":
        month = raw_month.lower()
    else:
        month = raw_month

    # Formatting rules per language
    if short == "en":
        # Month Day, Year -> March 25, 2026
        return f"{month} {day}, {year}"

    if short in ("fr", "de", "it", "nl", "es", "id"):
        # Day Month Year -> 25 mars 2026 (no commas/connectors)
        return f"{day} {month} {year}"

    if short == "pt":
        # Portuguese: Day de month de Year -> 25 de março de 2026
        return f"{day} de {month} de {year}"

    # Fallback: use English-style month-first formatting
    return f"{month} {day}, {year}"


def get_language_labels(language):
    lang = (language or "").lower()
    # exact key
    if lang in LANGUAGE_LABELS:
        return LANGUAGE_LABELS[lang]
    # try prefix like en-US -> en
    short = lang.split("-")[0].split("_")[0]
    if short in LANGUAGE_LABELS:
        return LANGUAGE_LABELS[short]
    # fallback to English
    return LANGUAGE_LABELS["en"]


def is_valid_title(value):
    if value is None:
        return False

    title = str(value).strip()
    if len(title) < 2:
        return False

    # Require at least one letter/number to avoid titles like "," or "'".
    return bool(re.search(r"\w", title, flags=re.UNICODE))


def is_supported_title(value):
    if not is_valid_title(value):
        return False

    title = str(value).strip()
    has_letter = False

    for char in title:
        if not char.isalpha():
            continue

        has_letter = True
        if "LATIN" not in unicodedata.name(char, ""):
            return False

    return has_letter


class MiniWeather(Weather):
    def generate_settings_template(self):
        template_params = super().generate_settings_template()
        template_params['api_key'] = {
            "required": True,
            "service": "OpenWeatherMap",
            "expected_key": "OPEN_WEATHER_MAP_SECRET"
        }
        return template_params
    def generate_image(self, settings, device_config):
        lat_value = settings.get("latitude")
        long_value = settings.get("longitude")
        if lat_value in (None, "") or long_value in (None, ""):
            raise RuntimeError("Latitude and Longitude are required.")

        # Validate and parse numeric coordinates with clear error messages.
        try:
            lat = float(str(lat_value).strip())
            long = float(str(long_value).strip())
        except (ValueError, TypeError):
            raise RuntimeError("Latitude and Longitude must be valid numeric values.")

        # Range checks: latitude [-90, 90], longitude [-180, 180]
        if not (-90.0 <= lat <= 90.0):
            raise RuntimeError("Latitude must be between -90 and 90.")
        if not (-180.0 <= long <= 180.0):
            raise RuntimeError("Longitude must be between -180 and 180.")

        units = settings.get("units")
        if units not in UNITS:
            raise RuntimeError("Units are required.")

        language = str(settings.get("language", "en")).strip() or "en"
        weather_provider = settings.get("weatherProvider", "OpenMeteo")
        timezone_name = device_config.get_config("timezone", default="America/New_York")
        time_format = device_config.get_config("time_format", default="12h")
        local_tz = pytz.timezone(timezone_name)

        try:
            template_params, provider_tz, api_key = self._get_template_params(
                weather_provider,
                settings,
                units,
                lat,
                long,
                local_tz,
                time_format,
                device_config,
            )
        except Exception as exc:
            logger.error("%s request failed: %s", weather_provider, exc)
            raise RuntimeError(f"{weather_provider} request failure, please check logs.") from exc

        title = self._resolve_title_with_fallback(settings, weather_provider, lat, long, api_key)

        forecast = template_params.get("forecast", [])
        if not forecast:
            raise RuntimeError("Forecast data unavailable.")

        current_day = forecast[0]
        forecast_days = max(1, min(4, int(settings.get("forecastDays", 4))))
        forecast_rows = forecast[1:1 + forecast_days] if len(forecast) > 1 else forecast[:forecast_days]
        labels = get_language_labels(language)
        weather_icon_style = self._weather_icon_style(settings)
        self._apply_weather_icon_style(template_params, forecast_rows, weather_icon_style)

        # localized date string
        # Use the provider timezone that was returned from _get_template_params.
        # This matches the timezone used to parse the forecast and respects the
        # user's `weatherTimeZone` selection (locationTimeZone vs device timezone).
        now = datetime.datetime.now(provider_tz)
        theme_context = get_theme_context(device_config, now=now)
        localized_date = format_localized_date(language, now)
        weather_background_enabled = settings.get("weatherBackgrounds", "true") != "false"
        weather_background = (
            self._select_weather_background(template_params.get("current_day_icon"), settings, now.date())
            if weather_background_enabled
            else None
        )

        # Fix weekday labels: the parent parser may derive day labels from
        # date-only strings forced to UTC midnight, which shifts the weekday
        # backwards for timezones west of UTC.  Override weekday_index using
        # calendar math so the localization always maps to the correct day.
        # forecast_rows[0] = tomorrow, forecast_rows[1] = day-after, etc.
        logger.debug("Mini Weather NOW date: %s (%s)", now.strftime("%Y-%m-%d"), now.strftime("%A"))
        for i, row in enumerate(forecast_rows):
            target_date = now + datetime.timedelta(days=i + 1)
            row["weekday_index"] = target_date.weekday()  # Monday=0 .. Sunday=6
            logger.debug(
                "  Forecast row %d: %s (%s) weekday_index=%d",
                i + 1, target_date.strftime("%Y-%m-%d"), target_date.strftime("%A"), row["weekday_index"],
            )

        template_params.update(
            {
                "title": title,
                "current_label": labels["days"][now.weekday()],
                "date": localized_date,
                "current_high": current_day["high"],
                "current_low": current_day["low"],
                "forecast_rows": self._localize_forecast_rows(forecast_rows, labels),
                "forecast_days": len(forecast_rows),
                "provider_timezone": provider_tz.zone,
                "plugin_settings": apply_theme_to_plugin_settings(settings, theme_context),
                "show_icons": settings.get("showIcons", "true") != "false",
                "color_icons": settings.get("colorIcons", "false") == "true" or weather_icon_style != "classic",
                "weather_icon_style": weather_icon_style,
                "theme": theme_context,
                "weather_background_enabled": weather_background_enabled,
                "weather_background_file": weather_background["uri"] if weather_background else "",
                "weather_background_path": weather_background["path"] if weather_background else "",
                "weather_background_slug": weather_background["slug"] if weather_background else "",
                "weather_background_style": weather_background["style"] if weather_background else "",
                "weather_background_color": weather_background["is_color"] if weather_background else False,
            }
        )
        self._write_weather_context(template_params, now)

        dimensions = self.get_dimensions(device_config)

        image = self.render_image(dimensions, "mini_weather.html", "mini_weather.css", template_params)
        if not image:
            logger.warning("Mini Weather HTML render failed; using PIL fallback renderer.")
            image = self._render_pil_fallback(dimensions, template_params)
        return image

    def _render_pil_fallback(self, dimensions, data):
        width, height = dimensions
        palette = get_theme_palette(data.get("theme"))
        bg = palette["background"]
        panel = palette["panel"]
        ink = palette["ink"]
        muted = palette["muted"]
        red = palette["red"]
        blue = palette["blue"]
        rule = palette["border"]

        img = self._build_pil_background(dimensions, bg, data)
        draw = ImageDraw.Draw(img)

        margin = max(18, int(width * 0.035))
        title_font = self._pil_font(52, "bold")
        date_font = self._pil_font(32)
        label_font = self._pil_font(64, "bold")
        range_font = self._pil_font(34, "bold")
        day_font = self._pil_font(38, "bold")

        title = str(data.get("title") or "Mini Weather")
        date_text = str(data.get("date") or "")
        self._draw_center_fit(draw, title, margin, 22, width - margin * 2, title_font, ink, 52, "bold")
        draw.text(((width - self._text_width(draw, date_text, date_font)) // 2, 82), date_text, font=date_font, fill=muted)
        draw.line((margin, 124, width - margin, 124), fill=rule, width=2)

        top = 148
        card_h = height - top - margin
        current_w = int((width - margin * 2) * 0.38)
        gap = 22
        forecast_x = margin + current_w + gap
        forecast_w = width - forecast_x - margin

        current_panel_alpha, forecast_panel_alpha = self._fallback_panel_alphas(data)
        self._rounded_panel(
            img,
            draw,
            (margin, top, margin + current_w, top + card_h),
            18,
            panel,
            current_panel_alpha,
            outline=rule,
            width=2,
        )
        self._draw_center(draw, str(data.get("current_label") or "NOW"), margin, top + 14, current_w, label_font, ink)

        temp = str(data.get("current_temperature") or "--")
        unit = self._unit_label(data)
        self._draw_current_conditions(
            img,
            draw,
            data.get("current_day_icon"),
            temp,
            unit,
            margin,
            top + 108,
            current_w,
            112,
            ink,
            data.get("color_icons"),
        )

        current_range = f"{data.get('current_high', '--')}{chr(176)}  {data.get('current_low', '--')}{chr(176)}"
        self._draw_center(draw, current_range, margin, top + card_h - 56, current_w, range_font, muted)

        rows = list(data.get("forecast_rows") or [])[: max(1, int(data.get("forecast_days") or 4))]
        row_gap = 10
        row_h = max(54, (card_h - row_gap * (len(rows) - 1)) // max(1, len(rows)))
        for idx, row in enumerate(rows):
            y = top + idx * (row_h + row_gap)
            if idx % 2 == 0:
                self._rounded_panel(
                    img,
                    draw,
                    (forecast_x, y, forecast_x + forecast_w, y + row_h),
                    14,
                    panel,
                    forecast_panel_alpha,
                    outline=rule,
                    width=1,
                )
            elif idx > 0:
                draw.line((forecast_x + 16, y, forecast_x + forecast_w - 16, y), fill=rule, width=1)
            icon = self._load_icon(row.get("icon"), min(48, row_h - 12), data.get("color_icons"))
            x = forecast_x + 18
            if icon:
                img.paste(icon, (x, y + (row_h - icon.height) // 2), icon if icon.mode == "RGBA" else None)
                x += icon.width + 18
            draw.text((x, y + (row_h - 40) // 2), str(row.get("day") or ""), font=day_font, fill=ink)
            high = f"{row.get('high', '--')}{chr(176)}"
            low = f"{row.get('low', '--')}{chr(176)}"
            low_w = self._text_width(draw, low, range_font)
            high_w = self._text_width(draw, high, range_font)
            right = forecast_x + forecast_w - 20
            draw.text((right - low_w, y + (row_h - 34) // 2), low, font=range_font, fill=blue)
            draw.text((right - low_w - high_w - 30, y + (row_h - 34) // 2), high, font=range_font, fill=red)

        return img

    def _select_weather_background(self, current_icon_path, settings=None, selected_date=None):
        icon_name = Path(str(current_icon_path or "")).stem.lower()
        slug = WEATHER_BACKGROUND_BY_ICON.get(icon_name)

        if not slug and icon_name:
            code = icon_name[:2]
            suffix = icon_name[-1] if icon_name[-1:] in ("d", "n") else "d"
            if code == "01":
                slug = "clear_night" if suffix == "n" else "clear_day"
            elif code in ("02", "03", "04"):
                slug = "cloudy"
            elif code in ("09", "10", "51", "53", "56", "57"):
                slug = "rain"
            elif code == "11":
                slug = "thunderstorm"
            elif code in ("13", "71", "73", "77"):
                slug = "snow"
            elif code in ("48", "50"):
                slug = "fog"

        slug = slug or WEATHER_BACKGROUND_DEFAULT
        style = self._weather_background_style(settings)
        candidates = self._weather_background_candidates(slug, style)
        if not candidates:
            logger.debug("Mini Weather background missing: %s (%s)", slug, style)
            return None
        background_path = candidates[self._stable_weather_background_index(slug, style, selected_date, len(candidates))]

        try:
            uri = background_path.resolve().as_uri()
        except ValueError:
            uri = str(background_path)

        return {
            "slug": slug,
            "path": str(background_path),
            "uri": uri,
            "style": style,
            "is_color": "backgrounds_color" in background_path.parts,
        }

    def _weather_background_style(self, settings):
        style = str((settings or {}).get("weatherBackgroundStyle") or WEATHER_BACKGROUND_DEFAULT_STYLE).strip()
        if style in WEATHER_BACKGROUND_STYLES:
            return style
        return WEATHER_BACKGROUND_DEFAULT_STYLE

    def _weather_background_candidates(self, slug, style):
        plugin_dir = Path(__file__).resolve().parent
        if style != "classic":
            candidates = []
            color_dir = plugin_dir / "backgrounds_color"
            for style_name in WEATHER_BACKGROUND_STYLES.get(style, ()):
                candidates.extend(self._weather_background_candidates_from_dir(color_dir / style_name, slug))
            if candidates:
                return sorted(dict.fromkeys(candidates))
        return self._weather_background_candidates_from_dir(plugin_dir / "backgrounds", slug)

    def _weather_background_candidates_from_dir(self, directory, slug):
        candidates = []
        for pattern in (f"{slug}.png", f"{slug}_*.png"):
            candidates.extend(path for path in directory.glob(pattern) if path.is_file())
        variant_dir = directory / slug
        if variant_dir.is_dir():
            candidates.extend(path for path in variant_dir.glob("*.png") if path.is_file())
        return sorted(dict.fromkeys(candidates))

    def _stable_weather_background_index(self, slug, style, selected_date, count):
        if count <= 1:
            return 0
        if hasattr(selected_date, "toordinal"):
            date_number = selected_date.toordinal()
        else:
            date_number = datetime.date.today().toordinal()
        digest = hashlib.sha256(f"{slug}|{style}".encode("utf-8")).hexdigest()
        offset = int(digest[:12], 16)
        return (date_number + offset) % count

    def _weather_icon_style(self, settings):
        style = str((settings or {}).get("weatherIconStyle") or WEATHER_ICON_DEFAULT_STYLE).strip()
        if style in WEATHER_ICON_STYLES:
            return style
        return WEATHER_ICON_DEFAULT_STYLE

    def _apply_weather_icon_style(self, template_params, forecast_rows, style):
        if style == "classic":
            return
        template_params["current_day_icon"] = self._styled_weather_icon_path(
            template_params.get("current_day_icon"),
            style,
        )
        for row in forecast_rows:
            row["icon"] = self._styled_weather_icon_path(row.get("icon"), style)

    def _styled_weather_icon_path(self, icon_path, style):
        icon_name = Path(str(icon_path or "")).name
        if not icon_name:
            return icon_path
        style_dir = WEATHER_ICON_STYLES.get(style)
        if not style_dir:
            return icon_path
        candidate = Path(self.get_plugin_dir(f"icons_color/{style_dir}/{icon_name}"))
        if candidate.is_file():
            return str(candidate)
        return icon_path

    def _build_pil_background(self, dimensions, base_color, data):
        img = Image.new("RGB", dimensions, base_color)
        background_path = data.get("weather_background_path")
        if not background_path:
            return img

        try:
            background = Image.open(background_path).convert("RGB")
            background = ImageOps.fit(background, dimensions, method=Image.LANCZOS)
            if data.get("weather_background_color"):
                return Image.blend(img, background, 0.42)
            background = ImageOps.grayscale(background)
            if (data.get("theme") or {}).get("mode") == "night":
                background = ImageOps.invert(background)
            return Image.blend(img, background.convert("RGB"), 0.24)
        except Exception as exc:
            logger.debug("Could not load Mini Weather background %s: %s", background_path, exc)
            return img

    def _pil_font(self, size, weight="normal"):
        system_fonts = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if weight == "bold" else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if weight == "bold" else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
        ]
        for path in system_fonts:
            try:
                if Path(path).is_file():
                    return ImageFont.truetype(path, size)
            except Exception:
                pass
        for family in ("Jost", "LXGW WenKai", "FandolKai"):
            try:
                font = get_font(family, size, weight)
                if font:
                    return font
            except Exception:
                pass
        return ImageFont.load_default()

    def _unit_label(self, data):
        units = ((data.get("plugin_settings") or {}).get("units") or "metric")
        if units == "metric":
            return f"{chr(176)}C"
        if units == "imperial":
            return f"{chr(176)}F"
        return "K"

    def _draw_current_conditions(self, img, draw, icon_path, temp, unit, x, y, width, height, fill, color_icons):
        inner_x = x + 18
        inner_w = width - 36
        gap = 12
        unit_gap = 6

        chosen = None
        for temp_size in range(96, 61, -4):
            unit_size = max(24, int(temp_size * 0.34))
            icon_size = max(62, min(96, int(temp_size * 0.92)))
            temp_font = self._pil_font(temp_size, "bold")
            unit_font = self._pil_font(unit_size, "bold")
            temp_w = self._text_width(draw, temp, temp_font)
            unit_w = self._text_width(draw, unit, unit_font)
            total_w = icon_size + gap + temp_w + unit_gap + unit_w
            if total_w <= inner_w:
                chosen = (temp_font, unit_font, icon_size, total_w, temp_w, unit_w)
                break

        if chosen is None:
            temp_font = self._pil_font(60, "bold")
            unit_font = self._pil_font(22, "bold")
            icon_size = 54
            temp_w = self._text_width(draw, temp, temp_font)
            unit_w = self._text_width(draw, unit, unit_font)
            total_w = icon_size + gap + temp_w + unit_gap + unit_w
            chosen = (temp_font, unit_font, icon_size, total_w, temp_w, unit_w)

        temp_font, unit_font, icon_size, total_w, temp_w, unit_w = chosen
        start_x = inner_x + max(0, (inner_w - total_w) // 2)
        icon = self._load_icon(icon_path, icon_size, color_icons)
        if icon:
            icon_y = y + (height - icon.height) // 2
            img.paste(icon, (start_x, icon_y), icon if icon.mode == "RGBA" else None)

        temp_box = draw.textbbox((0, 0), temp, font=temp_font)
        unit_box = draw.textbbox((0, 0), unit, font=unit_font)
        temp_h = temp_box[3] - temp_box[1]
        temp_x = start_x + icon_size + gap
        temp_y = y + (height - temp_h) // 2 - 4
        draw.text((temp_x, temp_y), temp, font=temp_font, fill=fill)

        unit_x = temp_x + temp_w + unit_gap
        unit_y = temp_y + max(0, int(temp_h * 0.43) - (unit_box[3] - unit_box[1]) // 2)
        draw.text((unit_x, unit_y), unit, font=unit_font, fill=fill)

    def _load_icon(self, path, size, color):
        if not path:
            return None
        try:
            icon = Image.open(Path(path)).convert("RGBA")
            icon.thumbnail((size, size), Image.LANCZOS)
            if not color:
                alpha = icon.getchannel("A")
                gray = ImageOps.grayscale(icon)
                alpha = alpha.point(lambda px: 0 if px < 20 else min(255, int(px * 1.2)))
                gray = ImageOps.autocontrast(gray)
                gray = gray.point(lambda px: max(18, min(245, int((px - 128) * 1.25 + 128))))
                icon = gray.convert("RGBA")
                icon.putalpha(alpha)
            return icon
        except Exception as exc:
            logger.debug("Could not load Mini Weather icon %s: %s", path, exc)
            return None

    def _rounded(self, draw, box, radius, fill, outline=None, width=1):
        try:
            draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)
        except AttributeError:
            draw.rectangle(box, fill=fill, outline=outline, width=width)

    def _rounded_panel(self, img, draw, box, radius, fill, alpha, outline=None, width=1):
        if alpha >= 255:
            self._rounded(draw, box, radius, fill, outline=outline, width=width)
            return

        fill_rgba = (*fill[:3], max(0, min(255, int(alpha))))
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        overlay_draw = ImageDraw.Draw(overlay)
        self._rounded(overlay_draw, box, radius, fill_rgba)
        composed = Image.alpha_composite(img.convert("RGBA"), overlay)
        img.paste(composed.convert("RGB"))
        if outline:
            self._rounded(draw, box, radius, None, outline=outline, width=width)

    def _fallback_panel_alphas(self, data):
        if not data.get("weather_background_path"):
            return 255, 255
        if data.get("weather_background_color"):
            return 36, 26
        return 190, 160

    def _draw_center(self, draw, text, x, y, width, font, fill):
        draw.text((x + (width - self._text_width(draw, text, font)) // 2, y), text, font=font, fill=fill)

    def _draw_center_fit(self, draw, text, x, y, width, font, fill, size, weight):
        current = font
        while self._text_width(draw, text, current) > width and size > 20:
            size -= 2
            current = self._pil_font(size, weight)
        self._draw_center(draw, text, x, y, width, current, fill)

    def _text_width(self, draw, text, font):
        box = draw.textbbox((0, 0), str(text), font=font)
        return box[2] - box[0]

    def _localize_forecast_rows(self, forecast_rows, labels):
        localized_rows = []
        # English abbreviations and full names used as fallback mapping
        EN_ABBR = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        EN_FULL = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

        for row in forecast_rows:
            row_copy = dict(row)
            weekday_index = row_copy.get("weekday_index")

            # If weekday_index not present, try to derive it from the day label
            if weekday_index is None:
                day_lbl = str(row_copy.get("day", "")).strip()
                if day_lbl:
                    # try to match common 3-letter English abbreviations
                    for idx, abbr in enumerate(EN_ABBR):
                        if day_lbl.startswith(abbr) or day_lbl.lower().startswith(abbr.lower()):
                            weekday_index = idx
                            break
                    else:
                        # try full English name
                        for idx, full in enumerate(EN_FULL):
                            if day_lbl.lower().startswith(full.lower()):
                                weekday_index = idx
                                break

            if isinstance(weekday_index, int):
                row_copy["day"] = labels["days"][weekday_index % 7]

            localized_rows.append(row_copy)

        return localized_rows

    def _get_template_params(
        self,
        weather_provider,
        settings,
        units,
        lat,
        long,
        local_tz,
        time_format,
        device_config,
    ):
        timezone_selection = settings.get("weatherTimeZone", "locationTimeZone")
        api_key = None

        if weather_provider == "OpenWeatherMap":
            api_key = device_config.load_env_key("OPEN_WEATHER_MAP_SECRET")
            if not api_key:
                raise RuntimeError("Open Weather Map API Key not configured.")

            weather_data = self.get_weather_data(api_key, units, lat, long)
            aqi_data = self.get_air_quality(api_key, lat, long)
            tz = self.parse_timezone(weather_data) if timezone_selection == "locationTimeZone" else local_tz
            template_params = self.parse_weather_data(weather_data, aqi_data, tz, units, time_format, lat)
            return template_params, tz, api_key

        if weather_provider == "OpenMeteo":
            weather_data = self.get_open_meteo_data(lat, long, units, 5)
            aqi_data = self.get_open_meteo_air_quality(lat, long)
            tz = self.parse_open_meteo_timezone(weather_data) if timezone_selection == "locationTimeZone" else local_tz
            template_params = self.parse_open_meteo_data(weather_data, aqi_data, tz, units, time_format, lat)
            return template_params, tz, api_key

        raise RuntimeError(f"Unknown weather provider: {weather_provider}")

    def _resolve_title(self, settings, weather_provider, lat, long, api_key):
        title_selection = settings.get("titleSelection", "location")
        custom_title = (settings.get("customTitle") or "").strip()

        if title_selection == "custom":
            if not custom_title:
                raise RuntimeError("Custom title is required.")
            return custom_title

        if weather_provider == "OpenWeatherMap":
            return self.get_location(api_key, lat, long)

        return self.get_reverse_geocoded_location(lat, long)

    def _resolve_title_with_fallback(self, settings, weather_provider, lat, long, api_key):
        try:
            title = self._resolve_title(settings, weather_provider, lat, long, api_key)
            if is_supported_title(title):
                return title
        except Exception as exc:
            logger.warning("Mini Weather title resolution failed, using fallback: %s", exc)

        quick_location = (settings.get("quickLocation") or "").strip()
        quick_location_label = QUICK_LOCATION_LABELS.get(quick_location)
        if quick_location_label:
            return quick_location_label

        matched_city = self._match_quick_location_by_coordinates(lat, long)
        if matched_city:
            return matched_city

        return self.format_coordinates(lat, long)

    def _match_quick_location_by_coordinates(self, lat, long, tolerance=0.02):
        for city, (city_lat, city_long) in QUICK_LOCATION_COORDS.items():
            if abs(lat - city_lat) <= tolerance and abs(long - city_long) <= tolerance:
                return city
        return None

    def parse_open_meteo_timezone(self, weather_data):
        timezone_name = weather_data.get("timezone")
        if not timezone_name:
            raise RuntimeError("Timezone not found in weather data.")

        logger.info("Using timezone from Open-Meteo data: %s", timezone_name)
        return pytz.timezone(timezone_name)

    def get_reverse_geocoded_location(self, lat, long):
        # Use rounded coordinates as cache key to avoid tiny float differences
        key = (round(float(lat), REVERSE_GEOCODE_ROUND_DECIMALS), round(float(long), REVERSE_GEOCODE_ROUND_DECIMALS))

        now_ts = datetime.datetime.now().timestamp()
        cached = REVERSE_GEOCODE_CACHE.get(key)
        if cached:
            age = now_ts - cached.get("ts", 0)
            if cached.get("title") and age < REVERSE_GEOCODE_SUCCESS_TTL:
                return cached["title"]
            if cached.get("failed") and age < REVERSE_GEOCODE_FAIL_TTL:
                # recent failure — avoid retrying too quickly
                return self.format_coordinates(lat, long)

        headers = {"User-Agent": "InkyPi Mini Weather/1.0 (+https://github.com/inkypi)"}
        try:
            response = requests.get(
                REVERSE_GEOCODE_URL.format(lat=lat, long=long),
                headers=headers,
                timeout=30,
            )
        except Exception as exc:
            logger.warning("Reverse geocode request failed: %s", exc)
            # store a failed marker to avoid hammering the service
            REVERSE_GEOCODE_CACHE[key] = {"failed": True, "ts": now_ts}
            return self.format_coordinates(lat, long)

        if not 200 <= response.status_code < 300:
            logger.warning("Failed to reverse geocode location: %s", response.content)
            REVERSE_GEOCODE_CACHE[key] = {"failed": True, "ts": now_ts}
            return self.format_coordinates(lat, long)

        try:
            location_data = response.json()
        except Exception as exc:
            logger.warning("Invalid JSON from reverse geocode: %s", exc)
            REVERSE_GEOCODE_CACHE[key] = {"failed": True, "ts": now_ts}
            return self.format_coordinates(lat, long)

        address = location_data.get("address", {})

        city = (
            address.get("city")
            or address.get("town")
            or address.get("village")
            or address.get("municipality")
            or address.get("county")
        )
        region = address.get("state") or address.get("country")

        if city and region:
            title = f"{city}, {region}"
        elif city:
            title = city
        elif region:
            title = region
        else:
            display_name = location_data.get("display_name", "")
            if display_name:
                title = ", ".join(display_name.split(", ")[:2])
            else:
                title = self.format_coordinates(lat, long)

        # Cache successful result
        REVERSE_GEOCODE_CACHE[key] = {"title": title, "ts": now_ts}
        return title

    def format_coordinates(self, lat, long):
        return f"{lat:.2f}, {long:.2f}"
