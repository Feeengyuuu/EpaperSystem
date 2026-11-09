"""晨读: a morning's reading on one page.

Merriam-Webster's word of the day with its definition and example, Wikiquote's quote of the day,
two short facts, and today's comic strip large enough to read (the strips take turns by the day).
Each feed keeps its own cache and freshness (sources.py): one that fails only hides its card.
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

from . import render, sources


logger = logging.getLogger(__name__)
PLUGIN_ID = "morning_read"
CACHE_SCHEMA = 1
CACHE_BUDGET = CacheBudget(7 * 86400, 16, 512 * 1024)
DEFAULT_FONT = "Microsoft YaHei"
TASK_SECONDS = 50
HOUR, DAY = 3600, 86400
# (fresh, usable) seconds per feed: the word, quote and facts are the day's; a strip's feed is
# asked every few hours so a new strip shows the day it comes out.
FRESHNESS = {"words": (6 * HOUR, 2 * DAY), "facts": (6 * HOUR, 2 * DAY), "comic": (4 * HOUR, 3 * DAY)}
# The strip prints at most about 380 x 360 px; it is kept a little sharper than that.
COMIC_PIXELS = (760, 760)
COMIC_BUDGET = CacheBudget(10 * 86400, 12, 6 * 1024 * 1024)


def _checkpoint(context):
    context.raise_if_cancelled()
    identity = current_instance_identity()
    validator = current_instance_identity_validator()
    if identity is not None and validator is not None and not validator(identity):
        raise TaskCancelled("晨读 instance changed during refresh")


class MorningRead(BasePlugin):
    def generate_settings_template(self):
        params = super().generate_settings_template()
        params["style_settings"] = True
        params["available_fonts"] = get_available_font_names(default=DEFAULT_FONT)
        return params

    def _cache_root(self):
        return self.cache_dir(env_var="MORNING_READ_CACHE_DIR", leaf="cache", create=True, strip=True)

    def _cache_subdir(self, name):
        path = self._cache_root() / name
        path.mkdir(parents=True, exist_ok=True)
        return path

    @staticmethod
    def _zone(device_config):
        get = getattr(device_config, "get_config", None)
        return sources.device_zone(get("timezone") if get else None)

    def _payload(self, settings, device_config, now, *, allow_network, context):
        force = allow_network and coerce_bool(settings.get("forceRefresh"), default=False)
        book = SourceBook(self._cache_subdir("sources"), CACHE_BUDGET, schema=CACHE_SCHEMA, max_bytes=64 * 1024,
                          checkpoint=_checkpoint)
        day = now.date().isoformat()

        def load(name, fetch, validate, timeout):
            fresh, usable = FRESHNESS[name]
            return book.load(Source(name, {"day": day}, fetch, fresh_seconds=fresh, usable_seconds=usable,
                                    validate=validate, timeout_seconds=timeout),
                             now, allow_network=allow_network, context=context, force=force)

        return {
            "words": load("words", lambda _ctx: sources.fetch_word_and_quote(now), sources.valid_word_and_quote, 25),
            "facts": load("facts", lambda _ctx: sources.fetch_facts(device_config, now), sources.valid_facts, 15),
            "comic": load("comic", lambda _ctx: sources.fetch_comic(now), sources.valid_comic, 25),
        }

    def _comic_image(self, payload, *, allow_network, context):
        url = sources.safe_comic_image(((payload.get("comic") or {}).get("data") or {}).get("image"))
        if not url:
            return None
        found = load_thumbnails([url], self._cache_subdir("comics"), http=get_http_client(), context=context,
                                cached_only=not allow_network, safe_url=sources.safe_comic_image,
                                user_agent=sources.USER_AGENT, size=COMIC_PIXELS, budget=COMIC_BUDGET,
                                max_bytes=8 * 1024 * 1024)
        return found.get(url)

    def render_cached_display(self, settings, device_config, *, resolved_theme_context):
        return self.render_themed_image(settings, device_config, theme_render_only=True,
                                        resolved_theme_context=resolved_theme_context)

    def generate_image(self, settings, device_config):
        settings = dict(settings or {})
        settings["_inkypi_theme"] = settings.get("_inkypi_theme") or self.resolve_theme(settings, device_config)
        cache_only = any(coerce_bool(settings.get(key), default=False)
                         for key in ("_theme_render_only", "_cached_render_only", "cached_render_only"))
        parent = task_context_or_default(timeout_seconds=TASK_SECONDS)
        context = TaskContext(parent.cancel_event, min(parent.deadline_monotonic, parent.clock() + TASK_SECONDS),
                              parent.clock)
        now = sources.local_now(zone=self._zone(device_config))
        _checkpoint(context)
        payload = self._payload(settings, device_config, now, allow_network=not cache_only, context=context)
        comic = self._comic_image(payload, allow_network=not cache_only, context=context)
        _checkpoint(context)
        try:
            image = render.render_page(self.get_dimensions(device_config), payload, settings, now, comic)
        finally:
            if comic is not None:
                comic.close()
        _checkpoint(context)
        return attach_source_provenance(image, combined_provenance(list(payload.values())), detail=PLUGIN_ID)
