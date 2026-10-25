"""Tech & Play: service status, Epic giveaways, upcoming games, new arXiv papers and speedrun records."""

from __future__ import annotations

import logging
import os

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
PLUGIN_ID = "tech_play"
CACHE_SCHEMA = 1
CACHE_BUDGET = CacheBudget(30 * 86400, 16, 2 * 1024 * 1024)
DEFAULT_FONT = "Microsoft YaHei"
TASK_SECONDS = 50


def _checkpoint(context):
    context.raise_if_cancelled()
    identity = current_instance_identity()
    validator = current_instance_identity_validator()
    if identity is not None and validator is not None and not validator(identity):
        raise TaskCancelled("Tech & Play instance changed during refresh")


class TechPlay(BasePlugin):
    def generate_settings_template(self):
        params = super().generate_settings_template()
        params["style_settings"] = True
        params["available_fonts"] = get_available_font_names(default=DEFAULT_FONT)
        return params

    def _book(self):
        directory = self.cache_dir(env_var="TECH_PLAY_CACHE_DIR", leaf="sources", create=True, strip=True)
        return SourceBook(directory, CACHE_BUDGET, schema=CACHE_SCHEMA, max_bytes=256 * 1024,
                          checkpoint=_checkpoint)

    @staticmethod
    def _twitch_credentials(device_config):
        values = []
        for key in ("TWITCH_CLIENT_ID", "TWITCH_CLIENT_SECRET"):
            value = ""
            if device_config is not None and hasattr(device_config, "load_env_key"):
                try:
                    value = device_config.load_env_key(key) or ""
                except Exception as exc:
                    logger.warning("Could not load %s for Tech & Play: %s", key, exc.__class__.__name__)
            values.append(str(value or os.getenv(key, "") or "").strip())
        return tuple(values)

    def _refresh(self, settings, device_config, now, *, allow_network, context):
        force = allow_network and coerce_bool(settings.get("forceRefresh"), default=False)
        category = sources.arxiv_setting(settings.get("arxivCategory"))
        client_id, client_secret = self._twitch_credentials(device_config)
        book = self._book()

        def load(spec):
            return book.load(spec, now, allow_network=allow_network, context=context, force=force)

        if client_id and client_secret:
            upcoming_spec = Source("upcoming", {"source": "igdb"},
                                   lambda ctx: sources.fetch_igdb(client_id, client_secret, now, ctx),
                                   fresh_seconds=12 * 3600, usable_seconds=5 * 86400,
                                   validate=sources.valid_upcoming, timeout_seconds=12)
        else:
            upcoming_spec = Source("upcoming", {"source": "steam"}, sources.fetch_steam_upcoming,
                                   fresh_seconds=12 * 3600, usable_seconds=5 * 86400,
                                   validate=sources.valid_upcoming, timeout_seconds=12)
        payload = {
            "status": load(Source("status", {"services": [item[0] for item in sources.SERVICES]},
                                  sources.fetch_status, fresh_seconds=600, usable_seconds=6 * 3600,
                                  validate=sources.valid_status, timeout_seconds=12)),
            "epic": load(Source("epic", {"country": "US"}, lambda ctx: sources.fetch_epic(now, ctx),
                                fresh_seconds=3 * 3600, usable_seconds=3 * 86400, validate=sources.valid_epic,
                                timeout_seconds=8)),
            "upcoming": load(upcoming_spec),
            "arxiv": load(Source("arxiv", {"category": category}, lambda ctx: sources.fetch_arxiv(category, ctx),
                                 fresh_seconds=6 * 3600, usable_seconds=3 * 86400, validate=sources.valid_arxiv,
                                 timeout_seconds=10)),
            "arxiv_category": category,
        }
        # The weekly ranking folds only runs verified since the last refresh into rolling counts.
        identity = {"feed": "verified-runs", "window_days": 7}
        previous = book.read(Source("speedrun-week", identity, None, fresh_seconds=0, usable_seconds=0,
                                    validate=sources.valid_speedrun_week))
        previous_data = (previous or {}).get("data") or {}
        payload["speedrun"] = load(Source(
            "speedrun-week", identity, lambda ctx: sources.fetch_speedrun_week(previous_data, now, ctx),
            fresh_seconds=1500, usable_seconds=7 * 86400, validate=sources.valid_speedrun_week,
            timeout_seconds=20, min_remaining_seconds=10))
        epic = payload["epic"]["data"]
        # Each shown game's small Steam icon, looked up once a week for the names on the page.
        games = [(item.get("title"), None) for item in (epic.get("current", []) + epic.get("upcoming", []))[:2]]
        games += [(item.get("name"), item.get("appid")) for item in payload["upcoming"]["data"].get("items", [])[:4]]
        games = [(name, appid) for name, appid in games if name]
        payload["icons"] = load(Source("steam-icons", {"games": sorted(name for name, _appid in games)},
                                       lambda ctx: sources.fetch_steam_icons(games, ctx),
                                       fresh_seconds=7 * 86400, usable_seconds=30 * 86400,
                                       validate=sources.valid_steam_icons, timeout_seconds=15))
        icon_urls = payload["icons"]["data"].get("icons", {})
        urls = [item.get("image") for item in (epic.get("current", []) + epic.get("upcoming", []))[:2]]
        urls += [item.get("image") for item in payload["upcoming"]["data"].get("items", [])[:4]]
        urls += [icon_urls.get(name) for name, _appid in games]
        for game in payload["speedrun"]["data"].get("top", []):
            urls.append(game.get("cover"))
            urls += [sources.flag_url(run.get("country")) for run in game.get("runs", [])[:2]]
        images = load_thumbnails(urls, self.cache_dir(env_var="TECH_PLAY_CACHE_DIR", leaf="covers", create=True,
                                                      strip=True),
                                 http=get_http_client(), context=context, cached_only=not allow_network,
                                 safe_url=sources.safe_image_url, user_agent=sources.USER_AGENT)
        results = [payload[name] for name in ("status", "epic", "upcoming", "arxiv", "speedrun")]
        payload["provenance"] = combined_provenance(results)
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
        payload, images = self._refresh(settings, device_config, now, allow_network=not cache_only,
                                        context=context)
        try:
            image = render_page(self.get_dimensions(device_config), payload, settings, now, images)
        finally:
            for cover in images.values():
                cover.close()
        _checkpoint(context)
        return attach_source_provenance(image, payload["provenance"], detail=PLUGIN_ID)
