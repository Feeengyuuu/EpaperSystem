"""Read-only vehicle status from the private Epaper Vehicle Bridge."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import logging
import math
from pathlib import Path
import threading
import time

from PIL import Image, ImageDraw

from plugins.base_plugin.base_plugin import BasePlugin
from plugins.base_plugin.render_provenance import (
    SourceProvenance,
    attach_source_provenance,
)
from utils.app_utils import get_base_ui_font
from utils.atomic_file import atomic_write_json
from utils.http_client import HttpStatusError, get_http_client
from utils.safe_image import ImageLimits, safe_open_image


logger = logging.getLogger(__name__)

BRIDGE_ORIGIN = "https://epaper-vehicle-bridge.superxfy.workers.dev"
BRIDGE_SUMMARY_URL = f"{BRIDGE_ORIGIN}/api/vehicle-summary"
BRIDGE_TOKEN_ENV = "EPAPER_VEHICLE_BRIDGE_TOKEN"
SUMMARY_MAX_BYTES = 64 * 1024
CACHE_MAX_BYTES = 64 * 1024
CACHE_FILE_NAME = "summary-v1.json"
VEHICLE_IMAGE_NAME = "vehicle.png"
SKIP_CACHE_IMAGE_INFO_KEY = "inkypi_skip_cache"
LOCAL_MAX_STALE_SECONDS = 86_400
VEHICLE_ART_BOX = (350, 50, 620, 165)
HEADER_IDENTITY_RIGHT = VEHICLE_ART_BOX[0] - 16

_CACHE_LOCK = threading.RLock()
_TOP_KEYS = {"schema_version", "served_at", "snapshot", "vehicle", "battery", "climate", "closures"}
_SNAPSHOT_KEYS = {"captured_at", "freshness", "age_seconds", "vehicle_connectivity"}
_VEHICLE_KEYS = {"key", "display_name", "model", "trim", "locked", "software_version", "odometer"}
_BATTERY_KEYS = {
    "level_percent",
    "estimated_range",
    "charging_state",
    "charge_limit_percent",
    "time_to_full_minutes",
    "power_kw",
}
_CLIMATE_KEYS = {"inside_temp_c", "outside_temp_c", "is_climate_on"}
_CLOSURE_KEYS = {"all_closed", "open", "charge_port_open"}
_MEASUREMENT_KEYS = {"value", "unit"}
_FRESHNESS = {"live", "fresh_cache", "stale_cache"}
_CONNECTIVITY = {"online", "asleep", "offline", "unknown", "unavailable", "in_service"}
_OPEN_CLOSURES = {
    "driver_front_door",
    "driver_rear_door",
    "passenger_front_door",
    "passenger_rear_door",
    "front_trunk",
    "rear_trunk",
    "driver_front_window",
    "driver_rear_window",
    "passenger_front_window",
    "passenger_rear_window",
}


class SummaryContractError(ValueError):
    """The bridge returned data outside the public sanitized contract."""


class VehicleStatus(BasePlugin):
    def generate_settings_template(self):
        params = super().generate_settings_template()
        params["api_key"] = {
            "required": False,
            "service": "Epaper Vehicle Bridge",
            "expected_key": BRIDGE_TOKEN_ENV,
        }
        params["style_settings"] = False
        return params

    def generate_image(self, settings, device_config):
        settings = dict(settings or {})
        dimensions = self.get_dimensions(device_config)
        theme = settings.get("_inkypi_theme")
        if not isinstance(theme, dict):
            theme = self.resolve_theme(settings, device_config)

        if _bool_setting(settings, "_theme_render_only", False):
            return self._theme_only_image(settings, dimensions, theme)

        cache_seconds = _int_setting(settings, "cacheSeconds", 900, 0, 86_400)
        force_refresh = any(
            _bool_setting(settings, key, False)
            for key in ("forceRefresh", "force_refresh")
        )

        with _CACHE_LOCK:
            cached = self._read_cache_unlocked()
            now = time.time()
            cached_is_usable = _cache_within_max_stale(cached, now)
            if (
                not force_refresh
                and cached_is_usable
                and self._cache_is_fresh(cached, cache_seconds, now)
            ):
                summary = _advance_cached_age(cached["summary"], cached["fetched_at"], now)
                provenance = _local_cache_provenance(summary)
                return self._render_attested(summary, dimensions, theme, settings, provenance)

            token = str(device_config.load_env_key(BRIDGE_TOKEN_ENV) or "").strip()
            if not token:
                if cached_is_usable:
                    summary = _advance_cached_age(cached["summary"], cached["fetched_at"], now)
                    return self._render_attested(
                        summary,
                        dimensions,
                        theme,
                        settings,
                        SourceProvenance.STALE_CACHE,
                    )
                if cached:
                    return self._render_local_message(
                        dimensions,
                        theme,
                        "STATUS TOO OLD",
                        "Refresh the bridge before trusting vehicle status.",
                    )
                return self._render_local_message(
                    dimensions,
                    theme,
                    "CONNECT BRIDGE",
                    f"Add {BRIDGE_TOKEN_ENV} in API Keys.",
                )

            try:
                result = get_http_client().request_json(
                    "GET",
                    BRIDGE_SUMMARY_URL,
                    headers={"Authorization": f"Bearer {token}"},
                    allow_redirects=False,
                    timeout=(4, 12),
                    max_bytes=SUMMARY_MAX_BYTES,
                )
                summary = sanitize_summary(result.data)
                if not _summary_within_max_stale(summary):
                    raise SummaryContractError("bridge summary is too stale")
            except Exception as exc:
                _log_bridge_failure(exc, has_cache=bool(cached))
                if cached_is_usable:
                    summary = _advance_cached_age(cached["summary"], cached["fetched_at"], now)
                    return self._render_attested(
                        summary,
                        dimensions,
                        theme,
                        settings,
                        SourceProvenance.STALE_CACHE,
                    )
                if cached:
                    return self._render_local_message(
                        dimensions,
                        theme,
                        "STATUS TOO OLD",
                        "Refresh the bridge before trusting vehicle status.",
                    )
                return self._render_local_message(
                    dimensions,
                    theme,
                    "STATUS UNAVAILABLE",
                    "No cached vehicle status is available yet.",
                )

            provenance = _bridge_provenance(summary)
            image = self._render_attested(
                summary,
                dimensions,
                theme,
                settings,
                provenance,
            )
            if _should_replace_local_cache(cached, summary, now):
                try:
                    self._write_cache_unlocked({"fetched_at": now, "summary": summary})
                except Exception as exc:
                    logger.warning(
                        "Vehicle cache write failed type=%s",
                        type(exc).__name__,
                    )
            return image

    def _theme_only_image(self, settings, dimensions, theme):
        with _CACHE_LOCK:
            cached = self._read_cache_unlocked(create=False)
        if not cached or not cached.get("summary"):
            return self._render_local_message(
                dimensions,
                theme,
                "NO CACHED STATUS",
                "Refresh the plugin once after authorization.",
            )
        now = time.time()
        if not _cache_within_max_stale(cached, now):
            return self._render_local_message(
                dimensions,
                theme,
                "STATUS TOO OLD",
                "Refresh the bridge before trusting vehicle status.",
            )
        cache_seconds = _int_setting(settings, "cacheSeconds", 900, 0, 86_400)
        summary = _advance_cached_age(cached["summary"], cached["fetched_at"], now)
        provenance = (
            _local_cache_provenance(summary)
            if self._cache_is_fresh(cached, cache_seconds, now)
            else SourceProvenance.STALE_CACHE
        )
        return self._render_attested(
            summary,
            dimensions,
            theme,
            settings,
            provenance,
        )

    def _render_attested(self, summary, dimensions, theme, settings, provenance):
        image = self._render_summary(summary, dimensions, theme, settings)
        if provenance in {SourceProvenance.STALE_CACHE, SourceProvenance.LOCAL_FALLBACK}:
            image.info[SKIP_CACHE_IMAGE_INFO_KEY] = True
        return attach_source_provenance(image, provenance)

    def _render_local_message(self, dimensions, theme, title, message):
        image = self._render_message(dimensions, theme, title, message)
        image.info[SKIP_CACHE_IMAGE_INFO_KEY] = True
        return attach_source_provenance(image, SourceProvenance.LOCAL_FALLBACK)

    def _cache_file(self, *, create=True):
        root = self.cache_dir(leaf="cache", create=create)
        return Path(root) / CACHE_FILE_NAME

    def _read_cache(self, *, create=False):
        with _CACHE_LOCK:
            return self._read_cache_unlocked(create=create)

    def _read_cache_unlocked(self, *, create=False):
        path = self._cache_file(create=create)
        try:
            if not path.is_file() or path.stat().st_size > CACHE_MAX_BYTES:
                return None
            payload = json.loads(path.read_text(encoding="utf-8"))
            if type(payload) is not dict or set(payload) != {"fetched_at", "summary"}:
                return None
            fetched_at = _number(payload.get("fetched_at"), 0, 100_000_000_000)
            if fetched_at is None:
                return None
            return {
                "fetched_at": fetched_at,
                "summary": sanitize_summary(payload.get("summary")),
            }
        except (OSError, UnicodeError, json.JSONDecodeError, SummaryContractError):
            return None

    def _write_cache(self, payload):
        with _CACHE_LOCK:
            self._write_cache_unlocked(payload)

    def _write_cache_unlocked(self, payload):
        fetched_at = _number(payload.get("fetched_at"), 0, 100_000_000_000)
        if fetched_at is None:
            raise SummaryContractError("cache fetched_at is invalid")
        summary = sanitize_summary(payload.get("summary"))
        path = self._cache_file(create=True)
        atomic_write_json(
            path,
            {"fetched_at": fetched_at, "summary": summary},
            mode=0o600,
        )

    @staticmethod
    def _cache_is_fresh(cached, cache_seconds, now):
        if not cached or cache_seconds <= 0:
            return False
        age = now - cached["fetched_at"]
        return 0 <= age < cache_seconds

    def _render_summary(self, summary, dimensions, theme, settings):
        colors = _render_colors(theme)
        canvas = Image.new("RGB", (800, 480), colors["background"])
        draw = ImageDraw.Draw(canvas)
        vehicle = summary["vehicle"]
        battery = summary["battery"]
        climate = summary["climate"]
        closures = summary["closures"]
        snapshot = summary["snapshot"]

        draw.rounded_rectangle((22, 18, 778, 462), radius=22, fill=colors["surface"], outline=colors["rule"], width=2)
        draw.text((48, 39), "VEHICLE / READ ONLY", font=_font(17, True), fill=colors["muted"])
        status_label = snapshot["vehicle_connectivity"].upper()
        status_color = colors["good"] if snapshot["vehicle_connectivity"] == "online" else colors["warning"]
        _pill(draw, (650, 33, 750, 66), status_label, status_color, colors["surface"])

        name_font = _font(37, True)
        model_font = _font(18)
        name = _ellipsize_text(
            draw,
            vehicle["display_name"],
            name_font,
            HEADER_IDENTITY_RIGHT - 48,
        )
        model_line = " · ".join(item for item in (vehicle["model"], vehicle["trim"]) if item) or "Vehicle"
        model_line = _ellipsize_text(
            draw,
            model_line,
            model_font,
            HEADER_IDENTITY_RIGHT - 50,
        )
        draw.text((48, 82), name, font=name_font, fill=colors["ink"])
        draw.text((50, 128), model_line, font=model_font, fill=colors["muted"])
        vehicle_art = _load_vehicle_art()
        art_left, art_top, art_right, art_bottom = VEHICLE_ART_BOX
        vehicle_art.thumbnail(
            (art_right - art_left, art_bottom - art_top),
            Image.Resampling.LANCZOS,
        )
        art_x = art_right - vehicle_art.width
        art_y = art_top + ((art_bottom - art_top - vehicle_art.height) // 2)
        canvas.paste(vehicle_art, (art_x, art_y), vehicle_art)

        _card(draw, (42, 176, 352, 382), colors)
        draw.text((64, 194), "BATTERY", font=_font(15, True), fill=colors["muted"])
        level = battery["level_percent"]
        level_text = "--" if level is None else str(int(round(level)))
        draw.text((64, 224), level_text, font=_font(70, True), fill=colors["ink"])
        draw.text((166, 264), "%", font=_font(24, True), fill=colors["muted"])
        _battery_bar(draw, (65, 319, 326, 350), level, colors)
        limit = battery["charge_limit_percent"]
        limit_text = "--" if limit is None else f"{int(round(limit))}%"
        draw.text((65, 358), f"LIMIT {limit_text}", font=_font(13, True), fill=colors["muted"])
        charge = battery["charging_state"] or "Unknown"
        _right_text(draw, 327, 358, charge.upper(), _font(13, True), colors["accent"])

        range_text = _measurement_text(battery["estimated_range"], settings)
        lock_text = "LOCKED" if vehicle["locked"] is True else "UNLOCKED" if vehicle["locked"] is False else "UNKNOWN"
        closure_text = "ALL CLOSED" if closures["all_closed"] is True else _closure_text(closures)
        inside = _temperature_text(climate["inside_temp_c"])
        outside = _temperature_text(climate["outside_temp_c"])
        climate_text = f"{inside} IN / {outside} OUT"

        _metric_card(draw, (374, 176, 742, 235), "EST. RANGE", range_text, colors)
        _metric_card(draw, (374, 246, 552, 305), "SECURITY", lock_text, colors)
        _metric_card(draw, (564, 246, 742, 305), "OPENINGS", closure_text, colors)
        if _bool_setting(settings, "showClimate", True):
            _metric_card(draw, (374, 316, 742, 382), "CLIMATE", climate_text, colors)
        else:
            odometer = _measurement_text(vehicle["odometer"], settings)
            _metric_card(draw, (374, 316, 742, 382), "ODOMETER", odometer, colors)

        age = summary["snapshot"]["age_seconds"]
        age_text = _age_text(age)
        freshness = summary["snapshot"]["freshness"].replace("_", " ").upper()
        draw.line((48, 407, 752, 407), fill=colors["rule"], width=1)
        draw.text((49, 421), f"UPDATED {age_text} AGO  ·  {freshness}", font=_font(14, True), fill=colors["muted"])
        _right_text(draw, 751, 421, "NO WAKE · NO COMMANDS", _font(14, True), colors["accent"])

        if dimensions != (800, 480):
            canvas = canvas.resize(dimensions, Image.Resampling.LANCZOS)
        return canvas

    def _render_message(self, dimensions, theme, title, message):
        colors = _render_colors(theme)
        canvas = Image.new("RGB", (800, 480), colors["background"])
        draw = ImageDraw.Draw(canvas)
        draw.rounded_rectangle((52, 48, 748, 432), radius=28, fill=colors["surface"], outline=colors["rule"], width=2)
        _draw_vehicle_silhouette(draw, (282, 105), colors, scale=1.55)
        _center_text(draw, 400, 252, title, _font(31, True), colors["ink"])
        _center_text(draw, 400, 305, message, _font(17), colors["muted"])
        _center_text(draw, 400, 365, "Read-only · no wake · no commands · no location", _font(14, True), colors["accent"])
        if dimensions != (800, 480):
            canvas = canvas.resize(dimensions, Image.Resampling.LANCZOS)
        return canvas


def sanitize_summary(payload):
    root = _object(payload, _TOP_KEYS, "summary")
    if type(root["schema_version"]) is not int or root["schema_version"] != 1:
        raise SummaryContractError("schema version is unsupported")
    served_at = _timestamp(root["served_at"], "served_at")

    snapshot_raw = _object(root["snapshot"], _SNAPSHOT_KEYS, "snapshot")
    freshness = _string(snapshot_raw["freshness"], 20, "snapshot.freshness")
    if freshness not in _FRESHNESS:
        raise SummaryContractError("snapshot freshness is invalid")
    connectivity = _string(snapshot_raw["vehicle_connectivity"], 32, "snapshot.vehicle_connectivity").lower()
    if connectivity not in _CONNECTIVITY:
        connectivity = "unknown"
    snapshot = {
        "captured_at": _nullable_timestamp(snapshot_raw["captured_at"], "snapshot.captured_at"),
        "freshness": freshness,
        "age_seconds": _nullable_number(snapshot_raw["age_seconds"], 0, 604_800, "snapshot.age_seconds"),
        "vehicle_connectivity": connectivity,
    }

    vehicle_raw = _object(root["vehicle"], _VEHICLE_KEYS, "vehicle")
    if vehicle_raw["key"] != "primary":
        raise SummaryContractError("vehicle key is invalid")
    vehicle = {
        "key": "primary",
        "display_name": _string(vehicle_raw["display_name"], 64, "vehicle.display_name"),
        "model": _nullable_string(vehicle_raw["model"], 40, "vehicle.model"),
        "trim": _nullable_string(vehicle_raw["trim"], 40, "vehicle.trim"),
        "locked": _nullable_bool(vehicle_raw["locked"], "vehicle.locked"),
        "software_version": _nullable_string(vehicle_raw["software_version"], 64, "vehicle.software_version"),
        "odometer": _measurement(vehicle_raw["odometer"], 0, 2_000_000, "vehicle.odometer"),
    }

    battery_raw = _object(root["battery"], _BATTERY_KEYS, "battery")
    battery = {
        "level_percent": _nullable_number(battery_raw["level_percent"], 0, 100, "battery.level_percent"),
        "estimated_range": _measurement(battery_raw["estimated_range"], 0, 2_500, "battery.estimated_range"),
        "charging_state": _nullable_string(battery_raw["charging_state"], 40, "battery.charging_state"),
        "charge_limit_percent": _nullable_number(battery_raw["charge_limit_percent"], 0, 100, "battery.charge_limit_percent"),
        "time_to_full_minutes": _nullable_number(battery_raw["time_to_full_minutes"], 0, 10_000, "battery.time_to_full_minutes"),
        "power_kw": _nullable_number(battery_raw["power_kw"], 0, 1_000, "battery.power_kw"),
    }

    climate_raw = _object(root["climate"], _CLIMATE_KEYS, "climate")
    climate = {
        "inside_temp_c": _nullable_number(climate_raw["inside_temp_c"], -100, 100, "climate.inside_temp_c"),
        "outside_temp_c": _nullable_number(climate_raw["outside_temp_c"], -100, 100, "climate.outside_temp_c"),
        "is_climate_on": _nullable_bool(climate_raw["is_climate_on"], "climate.is_climate_on"),
    }

    closures_raw = _object(root["closures"], _CLOSURE_KEYS, "closures")
    open_raw = closures_raw["open"]
    if type(open_raw) is not list or len(open_raw) > len(_OPEN_CLOSURES):
        raise SummaryContractError("closures.open is invalid")
    open_items = []
    for item in open_raw:
        label = _string(item, 40, "closures.open")
        if label not in _OPEN_CLOSURES or label in open_items:
            raise SummaryContractError("closures.open contains an invalid label")
        open_items.append(label)
    all_closed = _nullable_bool(closures_raw["all_closed"], "closures.all_closed")
    charge_port_open = _nullable_bool(
        closures_raw["charge_port_open"],
        "closures.charge_port_open",
    )
    if all_closed is True and (open_items or charge_port_open is not False):
        raise SummaryContractError("closures contradict all_closed")
    if all_closed is False and not open_items and charge_port_open is not True:
        raise SummaryContractError("closures do not identify an opening")
    closures = {
        "all_closed": all_closed,
        "open": open_items,
        "charge_port_open": charge_port_open,
    }

    return {
        "schema_version": 1,
        "served_at": served_at,
        "snapshot": snapshot,
        "vehicle": vehicle,
        "battery": battery,
        "climate": climate,
        "closures": closures,
    }


def _object(value, expected_keys, field):
    if type(value) is not dict or set(value) != expected_keys:
        raise SummaryContractError(f"{field} fields are invalid")
    return value


def _string(value, max_length, field):
    if not isinstance(value, str):
        raise SummaryContractError(f"{field} must be text")
    normalized = " ".join(value.strip().split())
    if not normalized or len(normalized) > max_length or any(ord(char) < 32 for char in normalized):
        raise SummaryContractError(f"{field} is invalid")
    return normalized


def _nullable_string(value, max_length, field):
    return None if value is None else _string(value, max_length, field)


def _nullable_bool(value, field):
    if value is None or type(value) is bool:
        return value
    raise SummaryContractError(f"{field} must be boolean or null")


def _number(value, minimum, maximum):
    if type(value) not in {int, float}:
        return None
    number = float(value)
    if not math.isfinite(number) or number < minimum or number > maximum:
        return None
    return number


def _nullable_number(value, minimum, maximum, field):
    if value is None:
        return None
    number = _number(value, minimum, maximum)
    if number is None:
        raise SummaryContractError(f"{field} is invalid")
    return number


def _measurement(value, minimum, maximum, field):
    if value is None:
        return None
    item = _object(value, _MEASUREMENT_KEYS, field)
    number = _number(item["value"], minimum, maximum)
    if number is None:
        raise SummaryContractError(f"{field}.value is invalid")
    unit = item["unit"]
    if type(unit) is not str or unit not in {"mi", "km"}:
        raise SummaryContractError(f"{field}.unit is invalid")
    return {"value": number, "unit": unit}


def _timestamp(value, field):
    if not isinstance(value, str) or len(value) > 40:
        raise SummaryContractError(f"{field} is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SummaryContractError(f"{field} is invalid") from exc
    if parsed.tzinfo is None:
        raise SummaryContractError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _nullable_timestamp(value, field):
    return None if value is None else _timestamp(value, field)


def _advance_cached_age(summary, fetched_at, now):
    result = deepcopy(summary)
    age = result["snapshot"]["age_seconds"]
    if age is not None:
        result["snapshot"]["age_seconds"] = min(
            604_800,
            max(0, int(age + max(0, now - fetched_at))),
        )
    return result


def _summary_within_max_stale(summary):
    age = summary["snapshot"]["age_seconds"]
    return age is None or age <= LOCAL_MAX_STALE_SECONDS


def _cache_within_max_stale(cached, now):
    content_age = _cached_content_age(cached, now)
    return content_age is not None and content_age <= LOCAL_MAX_STALE_SECONDS


def _cached_content_age(cached, now):
    if not cached or not cached.get("summary"):
        return None
    elapsed = now - cached["fetched_at"]
    if not math.isfinite(elapsed) or elapsed < 0:
        return None
    age = cached["summary"]["snapshot"]["age_seconds"]
    return elapsed if age is None else age + elapsed


def _should_replace_local_cache(cached, summary, now):
    if summary["snapshot"]["freshness"] != "stale_cache" or not cached:
        return True
    if not _cache_within_max_stale(cached, now):
        return True
    new_age = summary["snapshot"]["age_seconds"]
    old_age = _cached_content_age(cached, now)
    return new_age is not None and (old_age is None or new_age < old_age)


def _bridge_provenance(summary):
    return {
        "live": SourceProvenance.LIVE,
        "fresh_cache": SourceProvenance.FRESH_CACHE,
        "stale_cache": SourceProvenance.STALE_CACHE,
    }[summary["snapshot"]["freshness"]]


def _local_cache_provenance(summary):
    if summary["snapshot"]["freshness"] == "stale_cache":
        return SourceProvenance.STALE_CACHE
    return SourceProvenance.FRESH_CACHE


def _log_bridge_failure(exc, *, has_cache):
    status = exc.status if isinstance(exc, HttpStatusError) else None
    detail = f" status={status}" if status is not None else ""
    logger.warning(
        "Vehicle bridge unavailable type=%s%s cached=%s",
        type(exc).__name__,
        detail,
        bool(has_cache),
    )


def _bool_setting(settings, key, default):
    value = settings.get(key, default)
    if type(value) is bool:
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off", ""}:
            return False
    return bool(default)


def _int_setting(settings, key, default, minimum, maximum):
    try:
        value = int(settings.get(key, default))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _font(size, bold=False):
    return get_base_ui_font(size, bold=bold)


def _ellipsize_text(draw, text, font, max_width):
    if draw.textbbox((0, 0), text, font=font)[2] <= max_width:
        return text
    ellipsis = "…"
    ellipsis_width = draw.textbbox((0, 0), ellipsis, font=font)[2]
    if ellipsis_width > max_width:
        return ""
    low, high = 0, len(text)
    while low < high:
        midpoint = (low + high + 1) // 2
        candidate = text[:midpoint].rstrip() + ellipsis
        if draw.textbbox((0, 0), candidate, font=font)[2] <= max_width:
            low = midpoint
        else:
            high = midpoint - 1
    return text[:low].rstrip() + ellipsis


def _render_colors(theme):
    night = isinstance(theme, dict) and theme.get("mode") == "night"
    roles = theme.get("palette", {}) if isinstance(theme, dict) else {}
    defaults = {
        "background": (11, 16, 23) if night else (242, 237, 228),
        "surface": (22, 29, 38) if night else (251, 249, 244),
        "ink": (240, 244, 247) if night else (24, 28, 33),
        "muted": (161, 174, 188) if night else (91, 96, 102),
        "rule": (59, 70, 83) if night else (203, 199, 190),
        "accent": (244, 111, 96) if night else (180, 54, 44),
    }

    def role(name, fallback=None):
        fallback = defaults.get(name, defaults["surface"]) if fallback is None else fallback
        value = roles.get(name, fallback) if isinstance(roles, dict) else fallback
        if isinstance(value, (tuple, list)) and len(value) == 3:
            try:
                channels = tuple(max(0, min(255, int(item))) for item in value)
                return channels
            except (TypeError, ValueError):
                pass
        return fallback

    background = role("background")
    surface = role("panel", defaults["surface"])
    return {
        "background": background,
        "surface": surface,
        "ink": role("ink"),
        "muted": role("muted"),
        "rule": role("rule"),
        "accent": role("accent"),
        "good": (45, 151, 100) if not night else (91, 207, 146),
        "warning": (190, 119, 35) if not night else (239, 179, 83),
        "track": (219, 215, 206) if not night else (50, 59, 70),
    }


def _card(draw, box, colors):
    draw.rounded_rectangle(box, radius=17, fill=colors["background"], outline=colors["rule"], width=1)


def _metric_card(draw, box, label, value, colors):
    _card(draw, box, colors)
    left, top, right, _bottom = box
    draw.text((left + 18, top + 10), label, font=_font(12, True), fill=colors["muted"])
    text = str(value)
    font = _font(20 if len(text) <= 18 else 15, True)
    _right_text(draw, right - 18, top + 29, text, font, colors["ink"])


def _pill(draw, box, text, fill, surface):
    draw.rounded_rectangle(box, radius=16, fill=fill)
    left, top, right, bottom = box
    font = _font(12, True)
    bounds = draw.textbbox((0, 0), text, font=font)
    width = bounds[2] - bounds[0]
    height = bounds[3] - bounds[1]
    draw.text(((left + right - width) / 2, (top + bottom - height) / 2 - 2), text, font=font, fill=surface)


def _battery_bar(draw, box, level, colors):
    left, top, right, bottom = box
    draw.rounded_rectangle(box, radius=9, fill=colors["track"])
    if level is None:
        return
    ratio = max(0, min(100, level)) / 100
    width = max(0, int((right - left) * ratio))
    if width > 0:
        draw.rounded_rectangle((left, top, left + width, bottom), radius=9, fill=colors["accent"])


def _draw_vehicle_silhouette(draw, origin, colors, scale=1.0):
    x, y = origin
    points = [(0, 48), (24, 22), (70, 10), (132, 12), (169, 35), (205, 42), (216, 61), (0, 61)]
    points = [(x + int(px * scale), y + int(py * scale)) for px, py in points]
    draw.polygon(points, fill=colors["accent"])
    for wheel_x in (48, 170):
        cx = x + int(wheel_x * scale)
        cy = y + int(61 * scale)
        radius = int(14 * scale)
        draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=colors["ink"])
        hub = max(3, int(6 * scale))
        draw.ellipse((cx - hub, cy - hub, cx + hub, cy + hub), fill=colors["surface"])


def _load_vehicle_art():
    image = safe_open_image(
        Path(__file__).with_name(VEHICLE_IMAGE_NAME),
        limits=ImageLimits(
            max_bytes=2 * 1024 * 1024,
            max_width=2048,
            max_height=1024,
            max_pixels=2_000_000,
            allowed_formats=frozenset({"PNG"}),
        ),
    )
    return image.convert("RGBA")


def _measurement_text(measurement, settings):
    if measurement is None:
        return "--"
    value = measurement["value"]
    if value is None:
        return "--"
    unit = measurement["unit"]
    requested = str(settings.get("distanceUnit") or "auto").strip().lower()
    if requested == "km" and unit == "mi":
        value, unit = value * 1.609344, "km"
    elif requested == "mi" and unit == "km":
        value, unit = value / 1.609344, "mi"
    return f"{value:,.0f} {unit}"


def _temperature_text(value):
    return "--" if value is None else f"{value:.0f}°C"


def _closure_text(closures):
    count = len(closures["open"]) + (1 if closures["charge_port_open"] is True else 0)
    if count:
        return f"{count} OPEN"
    return "UNKNOWN"


def _age_text(seconds):
    if seconds is None:
        return "UNKNOWN"
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}S"
    if seconds < 3_600:
        return f"{seconds // 60}M"
    return f"{seconds // 3_600}H"


def _right_text(draw, right, top, text, font, fill):
    bounds = draw.textbbox((0, 0), text, font=font)
    draw.text((right - (bounds[2] - bounds[0]), top), text, font=font, fill=fill)


def _center_text(draw, center_x, top, text, font, fill):
    bounds = draw.textbbox((0, 0), text, font=font)
    draw.text((center_x - (bounds[2] - bounds[0]) / 2, top), text, font=font, fill=fill)
