from __future__ import annotations

import logging
from collections.abc import Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, time, timezone
from typing import Any

import pytz

from plugins.context_cache import read_contexts

logger = logging.getLogger(__name__)

DEFAULT_DAY_START = time(7, 0)
DEFAULT_NIGHT_START = time(19, 0)
EFFECTIVE_THEME_CONTEXT_INFO_KEY = "inkypi_effective_theme_context"

_PINNED_THEME_CONTEXT: ContextVar[dict[str, Any] | None] = ContextVar(
    "inkypi_pinned_theme_context",
    default=None,
)

THEME_MODE_KEYS = ("theme_mode", "display_theme_mode", "themeMode")
PLUGIN_THEME_MODE_KEYS = ("themeMode", "theme_mode", "theme", "sportsDashboardTheme")
PALETTE_ROLES = ("background", "panel", "ink", "muted", "rule", "accent")

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
    "paper": "day",
    "comic": "day",
    "white": "day",
    "night": "night",
    "dark": "night",
    "cinema": "night",
    "streaming": "night",
    "midnight": "night",
}


def normalize_theme_mode(value: Any, default: str | None = None) -> str | None:
    if value is None:
        return default
    return MODE_ALIASES.get(str(value).strip().lower(), default)


def get_theme_context(
    device_config: Any = None,
    now: datetime | None = None,
    astronomy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    tz = _device_timezone(device_config)
    current = _coerce_datetime(now, tz)

    if astronomy is None:
        pinned = _PINNED_THEME_CONTEXT.get()
        if pinned is not None:
            return _defensive_copy(pinned)

    requested = _requested_theme_mode(device_config)
    if requested in {"day", "night"}:
        return _theme_result(
            requested,
            current,
            source="config",
            reason=f"forced {requested}",
        )

    astronomy_state = (
        _validated_astronomy_state(astronomy, current)
        if astronomy is not None
        else _latest_weather_astronomy(current)
    )
    if astronomy_state:
        mode = (
            "day"
            if astronomy_state["sunrise_dt"]
            <= astronomy_state["now_dt"]
            < astronomy_state["sunset_dt"]
            else "night"
        )
        return _theme_result(
            mode,
            current,
            source="weather",
            reason="sunrise/sunset",
            sunrise=astronomy_state["sunrise"],
            sunset=astronomy_state["sunset"],
            date=astronomy_state["date"],
            timezone_name=astronomy_state["timezone"],
        )

    local_time = current.timetz().replace(tzinfo=None)
    mode = "day" if DEFAULT_DAY_START <= local_time < DEFAULT_NIGHT_START else "night"
    return _theme_result(
        mode,
        current,
        source="fallback",
        reason="07:00-19:00 local fallback",
    )


def resolve_plugin_theme(
    settings: Mapping[str, Any] | None = None,
    device_config: Any = None,
    now: datetime | None = None,
    palette: Mapping[str, Any] | None = None,
    astronomy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    plugin_settings = settings if isinstance(settings, Mapping) else {}
    raw_mode = next(
        (
            plugin_settings.get(key)
            for key in PLUGIN_THEME_MODE_KEYS
            if plugin_settings.get(key) not in (None, "")
        ),
        "auto",
    )
    requested_mode = normalize_theme_mode(raw_mode, "auto") or "auto"
    context = get_theme_context(
        device_config,
        now=now,
        astronomy=astronomy,
    )
    mode = context["mode"] if requested_mode == "auto" else requested_mode

    result = dict(context)
    result.update({"requested_mode": requested_mode, "mode": mode})
    result["palette"] = resolve_palette_roles(palette, mode)
    result["css"] = _css_palette(result["palette"])
    return result


def resolve_palette_roles(
    palette: Mapping[str, Any] | None,
    mode: Any,
) -> dict[str, tuple[int, int, int]]:
    resolved_mode = normalize_theme_mode(mode, "day")
    resolved_mode = resolved_mode if resolved_mode in {"day", "night"} else "day"
    fallback = NIGHT_PALETTE if resolved_mode == "night" else DAY_PALETTE
    supplied = _palette_for_mode(palette, resolved_mode)

    resolved = {
        role: _coerce_rgb(supplied.get(role), fallback[role])
        for role in PALETTE_ROLES
    }
    if _contrast_ratio(resolved["background"], resolved["ink"]) < 4.5:
        resolved["ink"] = max(
            ((0, 0, 0), (255, 255, 255)),
            key=lambda candidate: _contrast_ratio(resolved["background"], candidate),
        )
    return resolved


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
    sunrise: datetime | str | None = None,
    sunset: datetime | str | None = None,
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
        "sunrise": _iso_value(sunrise),
        "sunset": _iso_value(sunset),
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
    return normalize_theme_mode(value)


def _palette_for_mode(palette: Mapping[str, Any] | None, mode: str) -> Mapping[str, Any]:
    if not isinstance(palette, Mapping):
        return {}
    selected = palette.get(mode)
    if isinstance(selected, Mapping):
        return selected
    if "day" in palette or "night" in palette:
        return {}
    return palette


def _coerce_rgb(value: Any, default: tuple[int, int, int]) -> tuple[int, int, int]:
    if isinstance(value, str):
        raw = value.strip().removeprefix("#")
        if len(raw) == 3:
            raw = "".join(channel * 2 for channel in raw)
        if len(raw) == 6:
            try:
                return tuple(int(raw[index : index + 2], 16) for index in (0, 2, 4))
            except ValueError:
                return default
    if isinstance(value, (list, tuple)) and len(value) == 3:
        channels_are_valid = all(
            isinstance(channel, int) and not isinstance(channel, bool) and 0 <= channel <= 255
            for channel in value
        )
        if channels_are_valid:
            return tuple(value)
    return default


def _contrast_ratio(first: tuple[int, int, int], second: tuple[int, int, int]) -> float:
    lighter, darker = sorted((_relative_luminance(first), _relative_luminance(second)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def _relative_luminance(rgb: tuple[int, int, int]) -> float:
    channels = []
    for channel in rgb:
        value = channel / 255
        channels.append(value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4)
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


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


@contextmanager
def pinned_theme_context(context: Mapping[str, Any]):
    """Pin one defensive theme projection for nested renderer reads."""
    if not isinstance(context, Mapping):
        raise TypeError("pinned theme context must be a mapping")
    pinned = _defensive_copy(context)
    if pinned.get("mode") not in {"day", "night"}:
        raise ValueError("pinned theme context must have day or night mode")
    token = _PINNED_THEME_CONTEXT.set(pinned)
    try:
        yield _defensive_copy(pinned)
    finally:
        _PINNED_THEME_CONTEXT.reset(token)


def canonical_weather_astronomy(
    astronomy: Mapping[str, Any] | None,
    *,
    now: datetime,
) -> dict[str, Any] | None:
    """Return the exact validated five-field Weather projection."""
    state = _validated_astronomy_state(astronomy, now)
    if state is None:
        return None
    return {
        key: state[key]
        for key in ("source", "date", "timezone", "sunrise", "sunset")
    }


def is_valid_effective_theme_context(context: Any) -> bool:
    """Validate the narrow renderer-to-runtime theme handoff shape."""
    if not isinstance(context, Mapping):
        return False
    if context.get("mode") not in {"day", "night"}:
        return False
    if context.get("requested_mode") not in {"auto", "day", "night"}:
        return False
    if context.get("source") not in {"weather", "fallback", "config"}:
        return False
    timezone_name = context.get("timezone")
    tz = _timezone_from_name(timezone_name)
    if tz is None:
        return False
    try:
        context_date = datetime.fromisoformat(str(context.get("date"))).date()
    except (TypeError, ValueError):
        return False
    if context.get("source") == "weather":
        sunrise = _parse_aware_sun_datetime(context.get("sunrise"), tz)
        sunset = _parse_aware_sun_datetime(context.get("sunset"), tz)
        if sunrise is None or sunset is None or sunrise >= sunset:
            return False
        if sunrise.date() != context_date or sunset.date() != context_date:
            return False
    elif context.get("sunrise") is not None or context.get("sunset") is not None:
        return False
    return isinstance(context.get("palette"), Mapping) and isinstance(
        context.get("css"),
        Mapping,
    )


def _latest_weather_astronomy(current: datetime) -> dict[str, Any] | None:
    try:
        contexts = read_contexts(
            ["weather"],
            now=current.astimezone(timezone.utc),
            include_stale=False,
        )
    except Exception as exc:
        logger.warning("Could not read weather context for theme mode: %s", exc)
        return None

    for entry in contexts:
        if entry.get("stale") is True:
            continue
        payload = entry.get("payload") or {}
        astronomy = payload.get("astronomy") if isinstance(payload, dict) else None
        validated = _validated_astronomy_state(astronomy, current)
        if validated is not None:
            return validated
    return None


def _validated_astronomy_state(
    astronomy: Mapping[str, Any] | None,
    current: datetime,
) -> dict[str, Any] | None:
    if not isinstance(astronomy, Mapping):
        return None
    if astronomy.get("source") != "weather":
        return None
    timezone_name = astronomy.get("timezone")
    tz = _timezone_from_name(timezone_name)
    if tz is None:
        return None
    now_for_sun = _coerce_datetime(current, tz)
    expected_date = now_for_sun.date().isoformat()
    if astronomy.get("date") != expected_date:
        return None
    sunrise = _parse_aware_sun_datetime(astronomy.get("sunrise"), tz)
    sunset = _parse_aware_sun_datetime(astronomy.get("sunset"), tz)
    if sunrise is None or sunset is None:
        return None
    if sunrise.date().isoformat() != expected_date:
        return None
    if sunset.date().isoformat() != expected_date:
        return None
    if sunrise >= sunset:
        return None
    return {
        "source": "weather",
        "date": expected_date,
        "timezone": str(timezone_name),
        "sunrise": str(astronomy.get("sunrise")),
        "sunset": str(astronomy.get("sunset")),
        "now_dt": now_for_sun,
        "sunrise_dt": sunrise,
        "sunset_dt": sunset,
    }


def _timezone_from_name(value: Any):
    if not value:
        return None
    name = str(value).strip()
    if name != "UTC" and "/" not in name:
        return None
    try:
        return pytz.timezone(name)
    except Exception:
        return None


def _parse_aware_sun_datetime(value: Any, tz) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except Exception:
            return None

    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    localized = parsed.astimezone(tz)
    if parsed.utcoffset() != localized.utcoffset():
        return None
    return localized


def _iso_value(value: datetime | str | None) -> str | None:
    if value is None:
        return None
    return value.isoformat() if isinstance(value, datetime) else str(value)


def _defensive_copy(value: Any):
    if isinstance(value, Mapping):
        return {key: _defensive_copy(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_defensive_copy(item) for item in value]
    return value


def _tz_name(tzinfo: Any) -> str:
    return getattr(tzinfo, "zone", None) or str(tzinfo or "UTC")
