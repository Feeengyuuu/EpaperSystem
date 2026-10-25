"""环球此刻 (Planet Now): the sky over the Bay, California's grid, America's searches and space."""

from __future__ import annotations

import logging

from plugins.base_plugin.base_plugin import BasePlugin
from plugins.base_plugin.render_provenance import attach_source_provenance
from plugins.base_plugin.source_book import Source, SourceBook, combined_provenance
from runtime.long_task_executor import (
    current_instance_identity, current_instance_identity_validator, task_context_or_default,
)
from runtime.refresh_contracts import TaskCancelled, TaskContext
from utils.app_utils import coerce_bool, get_available_font_names
from utils.cache_manager import CacheBudget
from utils.http_client import get_http_client
from utils.remote_thumbnails import load_thumbnails

from . import sources


logger = logging.getLogger(__name__)
PLUGIN_ID = "planet_now"
CACHE_SCHEMA = 1
CACHE_BUDGET = CacheBudget(30 * 86400, 24, 2 * 1024 * 1024)
DEFAULT_FONT = "Microsoft YaHei"
TASK_SECONDS = 50
MINUTE, HOUR, DAY = 60, 3600, 86400
# The page counts as live from its three main feeds; the rest (rain, Earth, Wikipedia, Apple,
# Mars, the ISS, the crews in orbit) only fill in, so one of them lagging does not mark the page stale.
CORE_FEEDS = ("grid", "goes", "trends")


def _checkpoint(context):
    context.raise_if_cancelled()
    identity = current_instance_identity()
    validator = current_instance_identity_validator()
    if identity is not None and validator is not None and not validator(identity):
        raise TaskCancelled("Planet Now instance changed during refresh")


class PlanetNow(BasePlugin):
    def generate_settings_template(self):
        params = super().generate_settings_template()
        params["style_settings"] = True
        params["available_fonts"] = get_available_font_names(default=DEFAULT_FONT)
        return params

    def _book(self):
        directory = self.cache_dir(env_var="PLANET_NOW_CACHE_DIR", leaf="sources", create=True, strip=True)
        return SourceBook(directory, CACHE_BUDGET, schema=CACHE_SCHEMA, max_bytes=256 * 1024, checkpoint=_checkpoint)

    def _refresh(self, settings, now, *, allow_network, context):
        force = allow_network and coerce_bool(settings.get("forceRefresh"), default=False)
        book = self._book()

        def load(name, fetch, fresh, usable, validate, timeout=12):
            return book.load(Source(name, {"v": 1}, fetch, fresh_seconds=fresh, usable_seconds=usable,
                                    validate=validate, timeout_seconds=timeout),
                             now, allow_network=allow_network, context=context, force=force)

        payload = {
            "grid": load("grid", sources.fetch_grid, 10 * MINUTE, 6 * HOUR, sources.valid_grid),
            "goes": load("goes", sources.fetch_goes, 20 * MINUTE, 6 * HOUR, sources.valid_goes, timeout=15),
            "rain": load("rain", sources.fetch_rain, 10 * MINUTE, 3 * HOUR, sources.valid_rain),
            "epic": load("epic", sources.fetch_epic, 6 * HOUR, 4 * DAY, sources.valid_epic),
            "trends": load("trends", sources.fetch_trends, HOUR, DAY, sources.valid_trends),
            "wiki": load("wiki", lambda ctx: sources.fetch_wiki(now, ctx), 6 * HOUR, 4 * DAY, sources.valid_wiki),
            "apple": load("apple", sources.fetch_apple, 12 * HOUR, 4 * DAY, sources.valid_apple),
            "mars": load("mars", sources.fetch_mars, 6 * HOUR, 4 * DAY, sources.valid_mars, timeout=18),
            "iss": load("iss", lambda ctx: sources.fetch_iss(now, ctx), 4 * MINUTE, 3 * HOUR, sources.valid_iss),
            "people": load("people", sources.fetch_people, 6 * HOUR, 7 * DAY, sources.valid_people),
        }
        trends = (payload["trends"]["data"] or {}).get("items") or []
        wiki = (payload["wiki"]["data"] or {}).get("articles") or []
        urls = [item.get("picture") for item in trends[:5]]
        urls += [wiki[0].get("thumbnail")] if wiki else []
        urls.append((payload["mars"]["data"] or {}).get("image"))
        urls += [person.get("image") for station in (payload["people"]["data"] or {}).get("stations") or []
                 for person in station.get("crew") or []][:12]
        images = load_thumbnails(urls, self.cache_dir(env_var="PLANET_NOW_CACHE_DIR", leaf="pictures", create=True,
                                                      strip=True),
                                 http=get_http_client(), context=context, cached_only=not allow_network,
                                 safe_url=sources.safe_image_url, user_agent=sources.USER_AGENT)
        payload["provenance"] = combined_provenance([payload[name] for name in CORE_FEEDS])
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
        payload, images = self._refresh(settings, now, allow_network=not cache_only, context=context)
        try:
            image = render_page(self.get_dimensions(device_config), payload, settings, now, images)
        finally:
            for picture in images.values():
                picture.close()
        _checkpoint(context)
        return attach_source_provenance(image, payload["provenance"], detail=PLUGIN_ID)
