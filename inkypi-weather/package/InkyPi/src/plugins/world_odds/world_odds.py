"""世界赔率榜: what the prediction markets are pricing. Polymarket's busiest event of the day
leads with its outcomes, each category's busiest beneath it; Kalshi's US-economy markets (the
Fed's next decision, recession, CPI, GDP, jobs, gas and the S&P's year end) fill the right side.
"""

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
PLUGIN_ID = "world_odds"
CACHE_SCHEMA = 1
CACHE_BUDGET = CacheBudget(7 * 86400, 8, 2 * 1024 * 1024)
DEFAULT_FONT = "Microsoft YaHei"
TASK_SECONDS = 50
# Prices move through the day; a reading still shows the shape of the odds for two.
FRESH_SECONDS = 30 * 60
USABLE_SECONDS = 2 * 86400
THUMB_PIXELS = (320, 320)
# The lead's likeliest outcomes whose week of prices the page charts.
CHARTED_OUTCOMES = 2


def _checkpoint(context):
    context.raise_if_cancelled()
    identity = current_instance_identity()
    validator = current_instance_identity_validator()
    if identity is not None and validator is not None and not validator(identity):
        raise TaskCancelled("世界赔率榜 instance changed during refresh")


class WorldOdds(BasePlugin):
    def generate_settings_template(self):
        params = super().generate_settings_template()
        params["style_settings"] = True
        params["available_fonts"] = get_available_font_names(default=DEFAULT_FONT)
        return params

    def _cache_root(self):
        return self.cache_dir(env_var="WORLD_ODDS_CACHE_DIR", leaf="cache", create=True, strip=True)

    def _cache_subdir(self, name):
        path = self._cache_root() / name
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _payload(self, settings, now, *, allow_network, context):
        force = allow_network and coerce_bool(settings.get("forceRefresh"), default=False)
        book = SourceBook(self._cache_subdir("sources"), CACHE_BUDGET, schema=CACHE_SCHEMA, max_bytes=512 * 1024,
                          checkpoint=_checkpoint)
        results = {
            "polymarket": book.load(Source("polymarket", {"tags": list(sources.CATEGORIES)}, sources.fetch_polymarket,
                                           fresh_seconds=FRESH_SECONDS, usable_seconds=USABLE_SECONDS,
                                           validate=sources.valid_polymarket, timeout_seconds=30),
                                    now, allow_network=allow_network, context=context, force=force),
            "kalshi": book.load(Source("kalshi", {"series": [row[0] for row in sources.KALSHI_SERIES]},
                                       sources.fetch_kalshi, fresh_seconds=FRESH_SECONDS, usable_seconds=USABLE_SECONDS,
                                       validate=sources.valid_kalshi, timeout_seconds=30),
                                now, allow_network=allow_network, context=context, force=force),
        }
        lead, tiles = sources.lead_and_categories((results["polymarket"].get("data") or {}).get("events"))
        histories = {}
        for outcome in ((lead or {}).get("outcomes") or [])[:CHARTED_OUTCOMES]:
            token = outcome.get("token")
            if not token:
                continue
            result = book.load(Source("history", {"token": token}, lambda ctx, token=token: sources.fetch_history(token, ctx),
                                      fresh_seconds=FRESH_SECONDS, usable_seconds=USABLE_SECONDS,
                                      validate=sources.valid_history, timeout_seconds=10),
                               now, allow_network=allow_network, context=context, force=force)
            if (result.get("data") or {}).get("points"):
                histories[token] = result["data"]["points"]
        return {**results, "lead": lead, "tiles": tiles, "histories": histories,
                "board": (results["kalshi"].get("data") or {}).get("board") or {},
                "provenance": combined_provenance(results.values())}

    def _images(self, payload, *, allow_network, context):
        """The events' pictures, and the lead's outcomes' own (a portrait, a party's mark)."""
        urls = [event.get("image") for event in [payload["lead"], *payload["tiles"]] if event]
        urls += [outcome.get("image") for outcome in ((payload["lead"] or {}).get("outcomes") or [])]
        return load_thumbnails([url for url in urls if url], self._cache_subdir("pictures"),
                               cached_only=not allow_network, size=THUMB_PIXELS, http=get_http_client(),
                               context=context, safe_url=sources.safe_image_url, user_agent=sources.USER_AGENT)

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
        images = self._images(payload, allow_network=not cache_only, context=context)
        try:
            image = render_page(self.get_dimensions(device_config), payload, settings, now, images)
        finally:
            for picture in images.values():
                picture.close()
        _checkpoint(context)
        return attach_source_provenance(image, payload["provenance"], detail=PLUGIN_ID)
