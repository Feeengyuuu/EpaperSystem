from __future__ import annotations

import logging
from datetime import datetime, time, timezone
from typing import Any

import pytz

from plugins.context_cache import read_contexts

logger = logging.getLogger(__name__)

DEFAULT_DAY_START = time(7, 0)
DEFAULT_NIGHT_START = time(19, 0)
WEATHER_CONTEXT_MAX_AGE_SECONDS = 72 * 60 * 60

THEME_MODE_KEYS = ("theme_mode", "display_theme_mode", "themeMode")

DAY_PALETTE = {
    "background": (255, 255, 255),
    "panel": (255, 255, 255),
    "header": (255, 255, 255),
    "ink": (10, 12, 15),
    "muted": (74, 78, 84),
    "dim": (116, 122, 130),
    "rule": (185, 188, 194),
    "border": (0, 0, 0),
    "red": (166, 38, 48),
    "gold": (128, 92, 24),
    "cyan": (24, 92, 150),
    "blue": (24, 92, 150),
    "green": (33, 112, 74),
    "accent": (24, 92, 150),
}

NIGHT_PALETTE = {
    "background": (0, 0, 0),
    "panel": (0, 0, 0),
    "header": (0, 0, 0),
    "ink": (255, 255, 255),
    "muted": (194, 196, 202),
    "dim": (112, 117, 130),
    "rule": (46, 48, 56),
    "border": (255, 255, 255),
    "red": (255, 82, 74),
    "gold": (255, 196, 92),
    "cyan": (107, 204, 255),
    "blue": (107, 204, 255),
    "green": (146, 221, 166),
    "accent": (107, 204, 255),
}

MODE_ALIASES = {
    "auto": "auto",
    "day": "day",
    "light": "day",
    "white": "day",
    "night": "night",
    "dark": "night",
    "midnight": "night",
}


def get_theme_context(device_config: Any = None, now: datetime | None = None) -> dict[str, Any]:
    tz = _device_timezone(device_config)
    current = _coerce_datetime(now, tz)

    requested = _requested_theme_mode(device_config)
    if requested in {"day", "night"}:
        return _theme_result(
            requested,
            current,
            source="config",
            reason=f"forced {requested}",
        )

    astronomy = _latest_weather_astronomy(current)
    if astronomy:
        mode = "day" if astronomy["sunrise"] <= astronomy["now"] < astronomy["sunset"] else "night"
        return _theme_result(
            mode,
            current,
            source="weather",
            reason="sunrise/sunset",
            sunrise=astronomy["sunrise"],
            sunset=astronomy["sunset"],
            date=astronomy["now"].date().isoformat(),
            timezone_name=astronomy["timezone_name"],
        )

    local_time = current.timetz().replace(tzinfo=None)
    mode = "day" if DEFAULT_DAY_START <= local_time < DEFAULT_NIGHT_START else "night"
    return _theme_result(
        mode,
        current,
        source="fallback",
        reason="07:00-19:00 local fallback",
    )


def get_theme_palette(theme: Any = None) -> dict[str, tuple[int, int, int]]:
    if isinstance(theme, dict):
        mode = _normalize_mode(theme.get("mode")) or "day"
    else:
        mode = _normalize_mode(theme) or "day"
    return dict(NIGHT_PALETTE if mode == "night" else DAY_PALETTE)


def apply_theme_to_plugin_settings(settings: dict[str, Any] | None, theme: dict[str, Any]) -> dict[str, Any]:
    themed = dict(settings or {})
    css = theme.get("css") if isinstance(theme, dict) else None
    if not isinstance(css, dict):
        css = _css_palette(get_theme_palette(theme))
    themed["backgroundOption"] = "color"
    themed["backgroundColor"] = css.get("background", "#ffffff")
    themed["textColor"] = css.get("ink", "#000000")
    return themed


def rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    r, g, b = rgb
    return f"#{r:02x}{g:02x}{b:02x}"


def _theme_result(
    mode: str,
    current: datetime,
    *,
    source: str,
    reason: str,
    sunrise: datetime | None = None,
    sunset: datetime | None = None,
    date: str | None = None,
    timezone_name: str | None = None,
) -> dict[str, Any]:
    palette = get_theme_palette(mode)
    return {
        "mode": mode,
        "source": source,
        "reason": reason,
        "date": date or current.date().isoformat(),
        "timezone": timezone_name or _tz_name(current.tzinfo),
        "sunrise": sunrise.isoformat() if sunrise else None,
        "sunset": sunset.isoformat() if sunset else None,
        "palette": palette,
        "css": _css_palette(palette),
    }


def _css_palette(palette: dict[str, tuple[int, int, int]]) -> dict[str, str]:
    return {key: rgb_to_hex(value) for key, value in palette.items()}


def _requested_theme_mode(device_config: Any) -> str:
    for key in THEME_MODE_KEYS:
        mode = _normalize_mode(_config_value(device_config, key, None))
        if mode:
            return mode
    return "auto"


def _normalize_mode(value: Any) -> str | None:
    if value is None:
        return None
    return MODE_ALIASES.get(str(value).strip().lower())


def _device_timezone(device_config: Any):
    tz_name = _config_value(device_config, "timezone", "UTC") or "UTC"
    try:
        return pytz.timezone(str(tz_name))
    except Exception:
        logger.warning("Invalid timezone %r; falling back to UTC.", tz_name)
        return pytz.timezone("UTC")


def _config_value(device_config: Any, key: str, default: Any = None) -> Any:
    if device_config is None:
        return default
    if hasattr(device_config, "get_config"):
        try:
            return device_config.get_config(key, default=default)
        except TypeError:
            return device_config.get_config(key) or default
    if isinstance(device_config, dict):
        return device_config.get(key, default)
    return getattr(device_config, key, default)


def _coerce_datetime(value: datetime | None, tz) -> datetime:
    if value is None:
        return datetime.now(tz)
    if value.tzinfo is None:
        if hasattr(tz, "localize"):
            return tz.localize(value)
        return value.replace(tzinfo=tz)
    return value.astimezone(tz)


def _latest_weather_astronomy(current: datetime) -> dict[str, Any] | None:
    try:
        contexts = read_contexts(
            ["weather"],
            now=current.astimezone(timezone.utc),
            max_age_seconds=WEATHER_CONTEXT_MAX_AGE_SECONDS,
            include_stale=True,
        )
    except Exception as exc:
        logger.warning("Could not read weather context for theme mode: %s", exc)
        return None

    for entry in contexts:
        payload = entry.get("payload") or {}
        astronomy = payload.get("astronomy") if isinstance(payload, dict) else None
        if not isinstance(astronomy, dict):
            continue

        tz = _timezone_from_name(astronomy.get("timezone")) or current.tzinfo
        now_for_sun = current.astimezone(tz)
        sunrise = _parse_sun_datetime(astronomy.get("sunrise"), tz, now_for_sun.date())
        sunset = _parse_sun_datetime(astronomy.get("sunset"), tz, now_for_sun.date())
        if sunrise and sunset and sunrise < sunset:
            return {
                "now": now_for_sun,
                "sunrise": sunrise,
                "sunset": sunset,
                "timezone_name": _tz_name(tz),
            }
    return None


def _timezone_from_name(value: Any):
    if not value:
        return None
    try:
        return pytz.timezone(str(value))
    except Exception:
        return None


def _parse_sun_datetime(value: Any, tz, target_date) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except Exception:
            return None

    if parsed.tzinfo is None:
        parsed = _coerce_datetime(parsed, tz)
    else:
        parsed = parsed.astimezone(tz)

    if parsed.date() != target_date:
        local_naive = datetime.combine(target_date, parsed.timetz().replace(tzinfo=None))
        parsed = _coerce_datetime(local_naive, tz)
    return parsed


def _tz_name(tzinfo: Any) -> str:
    return getattr(tzinfo, "zone", None) or str(tzinfo or "UTC")
