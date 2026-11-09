"""时光书签: the time, the moon and the days on one page.

A sentence from a classical Chinese novel and a line from an English book that name this minute
(the two literature clocks), the moon's phase with the day's moon and sun rise and set, how much of
the day, week, month and year has passed, and the days until the next holidays. Only the rise and
set times come over the network (USNO, cached per day); the rest is worked out at every redraw, so
the cached redraw at display time shows the minute it is shown (sources.py).
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

from . import render, sources


logger = logging.getLogger(__name__)
PLUGIN_ID = "time_almanac"
CACHE_SCHEMA = 1
CACHE_BUDGET = CacheBudget(7 * 86400, 16, 256 * 1024)
DEFAULT_FONT = "Microsoft YaHei"
TASK_SECONDS = 20
HOUR = 3600
# USNO's table for one day: fetched once that day, standing in for a day and a half if it fails.
SKY_FRESH, SKY_USABLE = 12 * HOUR, 36 * HOUR


def _checkpoint(context):
    context.raise_if_cancelled()
    identity = current_instance_identity()
    validator = current_instance_identity_validator()
    if identity is not None and validator is not None and not validator(identity):
        raise TaskCancelled("时光书签 instance changed during refresh")


class TimeAlmanac(BasePlugin):
    def generate_settings_template(self):
        params = super().generate_settings_template()
        params["style_settings"] = True
        params["available_fonts"] = get_available_font_names(default=DEFAULT_FONT)
        return params

    def _cache_root(self):
        return self.cache_dir(env_var="TIME_ALMANAC_CACHE_DIR", leaf="cache", create=True, strip=True)

    @staticmethod
    def _zone(device_config):
        get = getattr(device_config, "get_config", None)
        return sources.device_zone(get("timezone") if get else None)

    def _sky(self, settings, device_config, now, *, allow_network, context):
        home, offset = sources.home_and_offset(settings, device_config, now)
        fetch = sources.sky_fetcher(home, now.date(), offset) if home else None
        identity = sources.sky_identity(home, now.date(), offset) if home else {"home": None}
        book = SourceBook(self._cache_root(), CACHE_BUDGET, schema=CACHE_SCHEMA, max_bytes=16 * 1024,
                          checkpoint=_checkpoint)
        force = allow_network and coerce_bool(settings.get("forceRefresh"), default=False)
        return book.load(Source("sky", identity, fetch, fresh_seconds=SKY_FRESH, usable_seconds=SKY_USABLE,
                                validate=sources.valid_sky, timeout_seconds=10),
                         now, allow_network=allow_network, context=context, force=force)

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
        sky = self._sky(settings, device_config, now, allow_network=not cache_only, context=context)
        payload = {
            "sky": sky,
            "clocks": sources.clock_lines(now),
            "moon": sources.moon(now),
            "progress": sources.progress(now),
            "countdowns": sources.countdowns(now.date(), settings),
        }
        _checkpoint(context)
        image = render.render_page(self.get_dimensions(device_config), payload, settings, now)
        _checkpoint(context)
        return attach_source_provenance(image, combined_provenance([sky]), detail=PLUGIN_ID)
