"""Bay Area closure and tide page; independent providers, local-only rotation."""

from __future__ import annotations

import hashlib
import json
import logging
import math

from plugins.base_plugin.base_plugin import BasePlugin
from plugins.base_plugin.render_provenance import SourceProvenance, attach_source_provenance
from runtime.long_task_executor import (
    current_instance_identity, current_instance_identity_validator, task_context_or_default,
)
from runtime.refresh_contracts import TaskCancelled, TaskContext
from utils.app_utils import bounded_int, coerce_bool, get_available_font_names
from utils.cache_manager import CacheBudget

from . import sources


logger = logging.getLogger(__name__)
PLUGIN_ID = "bay_commute"
CACHE_SCHEMA = 2
MAX_CACHE_BYTES = 512 * 1024
CACHE_BUDGET = CacheBudget(7 * 86400, 12, 3 * 1024 * 1024)
DEFAULT_FONT = "Microsoft YaHei"


def _checkpoint(context):
    context.raise_if_cancelled()
    identity = current_instance_identity()
    validator = current_instance_identity_validator()
    if identity is not None and validator is not None and not validator(identity):
        raise TaskCancelled("Bay Commute instance changed during refresh")


class BayCommute(BasePlugin):
    def generate_settings_template(self):
        params = super().generate_settings_template()
        params["style_settings"] = True
        params["available_fonts"] = get_available_font_names(default=DEFAULT_FONT)
        return params

    def _cache_dir(self, create=False):
        return self.cache_dir(env_var="BAY_COMMUTE_CACHE_DIR", leaf="cache", create=create, strip=True)

    @staticmethod
    def _cache_key(name, identity):
        key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:16]
        return f"{name}-{key}.json"

    def _read_cache(self, name, identity):
        path = self._cache_dir(create=False) / self._cache_key(name, identity)
        try:
            if path.is_symlink() or path.stat().st_size > MAX_CACHE_BYTES:
                return None
            payload = json.loads(path.read_text(encoding="utf-8"))
            if (not isinstance(payload, dict) or payload.get("schema") != CACHE_SCHEMA
                    or payload.get("identity") != identity or not sources.parse_time(payload.get("fetched_at"))
                    or not self._valid_data(name, payload.get("data"))):
                return None
            return payload
        except (OSError, ValueError, TypeError):
            return None

    @staticmethod
    def _valid_data(name, data):
        if not isinstance(data, dict):
            return False
        if name == "roads":
            rows = data.get("items")
            return (isinstance(rows, list) and len(rows) <= sources.MAX_CACHED_ROADS
                    and all(isinstance(row, dict) and row.get("route") and row.get("id")
                            and sources.parse_time(row.get("start"))
                            and (sources.parse_time(row.get("end")) or row.get("indefinite") is True)
                            and all((row.get(prefix + "_lat") is None and row.get(prefix + "_lon") is None)
                                    or sources.valid_coordinates(row.get(prefix + "_lat"), row.get(prefix + "_lon"))
                                    for prefix in ("begin", "end"))
                            for row in rows))
        if not data.get("prediction_date") or not data.get("end_date"):
            return False
        for key in ("extremes", "curve"):
            rows = data.get(key)
            if not isinstance(rows, list) or not 1 <= len(rows) <= 600:
                return False
            for row in rows:
                if (not isinstance(row, dict) or not sources.parse_time(row.get("time"))
                        or type(row.get("height")) not in (int, float)
                        or not math.isfinite(row["height"])):
                    return False
                if key == "extremes" and row.get("type") not in {"H", "L"}:
                    return False
        return True

    def _write_cache(self, name, identity, payload):
        encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        if len(encoded) > MAX_CACHE_BYTES:
            raise ValueError("Bay Commute cache is too large")
        namespace = self.managed_cache_namespace(self._cache_dir(create=True), CACHE_BUDGET)
        namespace.put_bytes(self._cache_key(name, identity), encoded)

    @staticmethod
    def _fresh(name, cache, now):
        fetched = sources.parse_time(cache.get("fetched_at"))
        if fetched is None or (now - fetched).total_seconds() < -300:
            return False
        if name == "roads":
            source = sources.parse_time(cache["data"].get("source_updated_at"))
            return (0 <= (now - fetched).total_seconds() < 3600
                    and (source is None or -300 <= (now - source).total_seconds() <= 3600))
        return (cache["data"].get("prediction_date") == now.date().isoformat()
                and -300 <= (now - fetched).total_seconds() < 27 * 3600)

    @staticmethod
    def _usable(name, cache, now):
        fetched = sources.parse_time(cache.get("fetched_at"))
        if not fetched or not -300 <= (now - fetched).total_seconds() <= 48 * 3600:
            return False
        if name == "tides":
            return cache["data"].get("end_date", "") >= now.date().isoformat()
        return True

    def _source(self, name, identity, now, *, allow_network, force, context, fetcher):
        cached = self._read_cache(name, identity)
        if cached and not force and self._fresh(name, cached, now):
            return {**cached, "state": "fresh_cache", "error": ""}
        error = ""
        if allow_network:
            try:
                _checkpoint(context)
                data = fetcher()
                if not self._valid_data(name, data):
                    raise ValueError("Provider data is invalid")
                _checkpoint(context)
                payload = {"schema": CACHE_SCHEMA, "identity": identity,
                           "fetched_at": now.isoformat(), "data": data}
                if name == "roads" and cached:
                    old_stamp = sources.parse_time(cached["data"].get("source_updated_at"))
                    new_stamp = sources.parse_time(data.get("source_updated_at"))
                    if old_stamp and new_stamp and new_stamp < old_stamp:
                        raise ValueError("Caltrans returned an older source snapshot")
                self._write_cache(name, identity, payload)
                fresh = self._fresh(name, payload, now)
                return {**payload, "state": "live" if fresh else "stale_cache",
                        "error": "" if fresh else "source_timestamp_stale"}
            except TaskCancelled:
                raise
            except Exception as exc:
                error = type(exc).__name__
                logger.warning("Bay Commute %s refresh failed (%s)", name, error)
        if cached and self._usable(name, cached, now):
            return {**cached, "state": "stale_cache", "error": error}
        return {"data": {}, "fetched_at": None, "state": "unavailable", "error": error or "no_cache"}

    def _payload(self, settings, now, *, allow_network, context):
        routes = sources.routes_setting(settings.get("routes"))
        station = sources.station_setting(settings.get("tideStation"))
        window_days = bounded_int(settings.get("windowDays"), 7, 1, 7)
        force = allow_network and coerce_bool(settings.get("forceRefresh"), default=False)
        roads = self._source("roads", {"routes": list(routes), "window_days": window_days}, now,
                             allow_network=allow_network, force=force, context=context,
                             fetcher=lambda: sources.fetch_roads(routes, now, context, window_days=window_days))
        # Force refresh deliberately keeps a valid same-day NOAA prediction cache.
        tides = self._source("tides", {"station": station}, now,
                             allow_network=allow_network, force=False, context=context,
                             fetcher=lambda: sources.fetch_tides(station, now, context))
        states = {roads["state"], tides["state"]}
        provenance = (SourceProvenance.LOCAL_FALLBACK if "unavailable" in states
                      else SourceProvenance.STALE_CACHE if "stale_cache" in states
                      else SourceProvenance.LIVE if "live" in states else SourceProvenance.FRESH_CACHE)
        return {"roads": roads, "tides": tides, "routes": list(routes), "station": station,
                "window_days": window_days, "provenance": provenance}

    def render_cached_display(self, settings, device_config, *, resolved_theme_context):
        return self.render_themed_image(settings, device_config, theme_render_only=True,
                                        resolved_theme_context=resolved_theme_context)

    def generate_image(self, settings, device_config):
        from .render import render_page

        settings = dict(settings or {})
        settings["_inkypi_theme"] = settings.get("_inkypi_theme") or self.resolve_theme(settings, device_config)
        cache_only = any(coerce_bool(settings.get(key), default=False)
                         for key in ("_theme_render_only", "_cached_render_only", "cached_render_only"))
        parent = task_context_or_default(timeout_seconds=35)
        context = TaskContext(parent.cancel_event, min(parent.deadline_monotonic, parent.clock()+35), parent.clock)
        now = sources.local_now()
        _checkpoint(context)
        payload = self._payload(settings, now, allow_network=not cache_only, context=context)
        image = render_page(self.get_dimensions(device_config), payload, settings, now)
        _checkpoint(context)
        return attach_source_provenance(image, payload["provenance"], detail=PLUGIN_ID)
