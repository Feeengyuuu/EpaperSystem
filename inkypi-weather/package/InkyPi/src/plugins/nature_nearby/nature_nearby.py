"""Nature Nearby: what people are observing around home on iNaturalist, refreshed through the day,
and the notable birds eBird reports nearby when an EBIRD_API_KEY is configured."""

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
from utils.http_client import get_http_client
from utils.remote_thumbnails import load_thumbnails

from . import sources


logger = logging.getLogger(__name__)
PLUGIN_ID = "nature_nearby"
CACHE_SCHEMA = 1
CACHE_BUDGET = CacheBudget(14 * 86400, 12, 1024 * 1024)
DEFAULT_FONT = "Microsoft YaHei"
TASK_SECONDS = 45


def _checkpoint(context):
    context.raise_if_cancelled()
    identity = current_instance_identity()
    validator = current_instance_identity_validator()
    if identity is not None and validator is not None and not validator(identity):
        raise TaskCancelled("Nature Nearby instance changed during refresh")


class NatureNearby(BasePlugin):
    def generate_settings_template(self):
        params = super().generate_settings_template()
        params["style_settings"] = True
        params["available_fonts"] = get_available_font_names(default=DEFAULT_FONT)
        return params

    def _cache(self, leaf):
        return self.cache_dir(env_var="NATURE_NEARBY_CACHE_DIR", leaf=leaf, create=True, strip=True)

    def _refresh(self, settings, now, *, allow_network, context, ebird_key=""):
        home = sources.home_setting(settings.get("latitude"), settings.get("longitude"))
        radius = bounded_int(settings.get("radiusKm"), 40, 5, 150)
        force = allow_network and coerce_bool(settings.get("forceRefresh"), default=False)
        book = SourceBook(self._cache("sources"), CACHE_BUDGET, schema=CACHE_SCHEMA, checkpoint=_checkpoint)
        identity = {"home": list(home), "radius": radius}

        def load(spec):
            return book.load(spec, now, allow_network=allow_network, context=context, force=force)

        payload = {
            "recent": load(Source("recent", identity, lambda ctx: sources.fetch_recent(home, radius, ctx),
                                  fresh_seconds=1800, usable_seconds=2 * 86400, validate=sources.valid_recent,
                                  timeout_seconds=10)),
            "species": load(Source("species", identity, lambda ctx: sources.fetch_species(home, radius, now, ctx),
                                   fresh_seconds=3 * 3600, usable_seconds=3 * 86400, validate=sources.valid_species,
                                   timeout_seconds=10)),
            "groups": load(Source("groups", identity, lambda ctx: sources.fetch_groups(home, radius, now, ctx),
                                  fresh_seconds=6 * 3600, usable_seconds=3 * 86400, validate=sources.valid_groups,
                                  timeout_seconds=10)),
            "stats": load(Source("stats", identity, lambda ctx: sources.fetch_stats(home, radius, now, ctx),
                                 fresh_seconds=3 * 3600, usable_seconds=3 * 86400, validate=sources.valid_stats,
                                 timeout_seconds=10)),
            "home": list(home), "radius_km": radius,
            # Notable birds are an optional extra: without a key the page leaves them out.
            "birds": load(Source("birds", identity,
                                 lambda ctx: sources.fetch_notable(home, radius, ebird_key, ctx),
                                 fresh_seconds=3600, usable_seconds=2 * 86400, validate=sources.valid_notable,
                                 timeout_seconds=10)) if ebird_key else None,
        }
        recent = payload["recent"]["data"].get("items", [])
        species = payload["species"]["data"].get("items", [])
        urls = [recent[0]["photo"]] if recent else []
        urls += [item.get("photo") for item in species[:6]] + [item.get("photo") for item in recent[1:6]]
        images = load_thumbnails(urls, self._cache("photos"), http=get_http_client(), context=context,
                                 cached_only=not allow_network, safe_url=sources.safe_photo_url,
                                 user_agent=sources.USER_AGENT, size=(420, 320))
        payload["provenance"] = combined_provenance(
            [payload[name] for name in ("recent", "species", "groups", "stats")])
        return payload, images

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
        payload, images = self._refresh(settings, now, allow_network=not cache_only, context=context,
                                        ebird_key=(device_config.load_env_key("EBIRD_API_KEY") or "").strip())
        try:
            image = render_page(self.get_dimensions(device_config), payload, settings, now, images)
        finally:
            for photo in images.values():
                photo.close()
        _checkpoint(context)
        return attach_source_provenance(image, payload["provenance"], detail=PLUGIN_ID)
