"""Bay Area alerts page: USGS quakes, NWS warnings and NASA EONET events on one hazard map."""

from __future__ import annotations

import logging

from plugins.base_plugin.base_plugin import BasePlugin
from plugins.base_plugin.render_provenance import attach_source_provenance
from plugins.base_plugin.source_book import Source, SourceBook, combined_provenance
from runtime.long_task_executor import (
    current_instance_identity, current_instance_identity_validator, task_context_or_default,
)
from runtime.refresh_contracts import TaskCancelled, TaskContext
from utils.app_utils import bounded_int, coerce_bool, get_available_font_names
from utils.cache_manager import CacheBudget

from . import sources


logger = logging.getLogger(__name__)
PLUGIN_ID = "bay_alerts"
CACHE_SCHEMA = 1
CACHE_BUDGET = CacheBudget(7 * 86400, 12, 4 * 1024 * 1024)
DEFAULT_FONT = "Microsoft YaHei"
TASK_SECONDS = 40


def _checkpoint(context):
    context.raise_if_cancelled()
    identity = current_instance_identity()
    validator = current_instance_identity_validator()
    if identity is not None and validator is not None and not validator(identity):
        raise TaskCancelled("Bay Alerts instance changed during refresh")


def _magnitude_setting(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 1.5
    return min(max(round(number, 1), 1.0), 4.0)


class BayAlerts(BasePlugin):
    def generate_settings_template(self):
        params = super().generate_settings_template()
        params["style_settings"] = True
        params["available_fonts"] = get_available_font_names(default=DEFAULT_FONT)
        params["bay_counties"] = sorted(sources.BAY_COUNTIES.items(), key=lambda item: item[1])
        return params

    def _book(self):
        directory = self.cache_dir(env_var="BAY_ALERTS_CACHE_DIR", leaf="sources", create=True, strip=True)
        return SourceBook(directory, CACHE_BUDGET, schema=CACHE_SCHEMA, max_bytes=768 * 1024,
                          checkpoint=_checkpoint)

    @staticmethod
    def _sources(settings, now):
        home = sources.home_setting(settings.get("latitude"), settings.get("longitude"))
        radius = bounded_int(settings.get("radiusKm"), 150, 30, 300)
        magnitude = _magnitude_setting(settings.get("minMagnitude"))
        counties = sources.counties_setting(settings.get("counties"))
        return home, radius, counties, [
            Source("quakes", {"home": list(home), "radius": radius, "min_mag": magnitude},
                   lambda context: sources.fetch_quakes(home, radius, magnitude, now, context),
                   fresh_seconds=900, usable_seconds=2 * 86400, validate=sources.valid_quakes, timeout_seconds=10),
            Source("alerts", {"counties": list(counties)},
                   lambda context: sources.fetch_alerts(counties, now, context),
                   fresh_seconds=900, usable_seconds=3 * 3600, validate=sources.valid_alerts, timeout_seconds=12),
            Source("events", {"bbox": sources.CALIFORNIA_BBOX},
                   lambda context: sources.fetch_events(now, context),
                   fresh_seconds=3 * 3600, usable_seconds=2 * 86400, validate=sources.valid_events,
                   timeout_seconds=12),
        ]

    def _payload(self, settings, now, *, allow_network, context):
        home, radius, counties, specs = self._sources(settings, now)
        force = allow_network and coerce_bool(settings.get("forceRefresh"), default=False)
        book = self._book()
        results = {spec.name: book.load(spec, now, allow_network=allow_network, context=context, force=force)
                   for spec in specs}
        label = " ".join(str(settings.get("homeLabel") or "家").split())[:8]
        return {**results, "home": list(home), "home_label": label, "radius_km": radius,
                "counties": list(counties), "provenance": combined_provenance(results.values())}

    def render_cached_display(self, settings, device_config, *, resolved_theme_context):
        return self.render_themed_image(settings, device_config, theme_render_only=True,
                                        resolved_theme_context=resolved_theme_context)

    def generate_image(self, settings, device_config):
        from .render import render_page

        settings = dict(settings or {})
        settings["_inkypi_theme"] = settings.get("_inkypi_theme") or self.resolve_theme(settings, device_config)
        cache_only = any(coerce_bool(settings.get(key), default=False)
                         for key in ("_theme_render_only", "_cached_render_only", "cached_render_only"))
        parent = task_context_or_default(timeout_seconds=TASK_SECONDS)
        context = TaskContext(parent.cancel_event, min(parent.deadline_monotonic, parent.clock() + TASK_SECONDS),
                              parent.clock)
        now = sources.local_now()
        _checkpoint(context)
        payload = self._payload(settings, now, allow_network=not cache_only, context=context)
        image = render_page(self.get_dimensions(device_config), payload, settings, now)
        _checkpoint(context)
        return attach_source_provenance(image, payload["provenance"], detail=PLUGIN_ID)
