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
# The new papers on the page each show their arXiv figure and the organisation behind them, looked
# up once a paper (neither changes) and only with this much of the refresh left.
PICTURED_PAPERS = 3
PAPER_SECONDS = 30 * 86400
PAPER_MIN_SECONDS = 20
# Their lookups keep a book of their own, so a month of them never crowds out the page's feeds.
PAPER_CACHE_BUDGET = CacheBudget(PAPER_SECONDS, 24, 512 * 1024)
FIGURE_PIXELS = (320, 240)
# arXiv figures are often large PNGs; one may download to this size (as on AI 论文日报).
FIGURE_MAX_BYTES = 8 * 1024 * 1024
LOGO_PIXELS = (250, 250)


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

    def _book(self, leaf="sources", budget=CACHE_BUDGET):
        directory = self.cache_dir(env_var="TECH_PLAY_CACHE_DIR", leaf=leaf, create=True, strip=True)
        return SourceBook(directory, budget, schema=CACHE_SCHEMA, max_bytes=256 * 1024, checkpoint=_checkpoint)

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
        games += [(item.get("name"), item.get("appid") or sources.steam_appid_from_url(item.get("image")))
                  for item in payload["upcoming"]["data"].get("items", [])[:4]]
        games = [(name, appid) for name, appid in games if name]
        # "match" versions the lookup rule, so a rule change looks the names up again.
        payload["icons"] = load(Source("steam-icons", {"games": sorted(name for name, _appid in games),
                                                       "match": "whole-words"},
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
        covers = self.cache_dir(env_var="TECH_PLAY_CACHE_DIR", leaf="covers", create=True, strip=True)
        common = {"http": get_http_client(), "context": context, "cached_only": not allow_network,
                  "safe_url": sources.safe_image_url, "user_agent": sources.USER_AGENT}
        images = load_thumbnails(urls, covers, **common)
        paper_book = self._book("papers", PAPER_CACHE_BUDGET)

        def load_paper(spec):
            return paper_book.load(spec, now, allow_network=allow_network, context=context, force=force)

        images.update(self._paper_images(payload["arxiv"]["data"].get("papers", []), load_paper, covers, common))
        results = [payload[name] for name in ("status", "epic", "upcoming", "arxiv", "speedrun")]
        payload["provenance"] = combined_provenance(results)
        return payload, images

    @staticmethod
    def _paper_images(papers, load, covers, common):
        """The shown papers' figures (of each paper's first few, the one whose shape best fills its
        box) and their organisations' logos; each paper gains its ``figure`` and ``institution``.
        The small lookups and logos come first, so a slow large figure never leaves them no time."""
        from .render import figure_misfit

        shown = papers[:PICTURED_PAPERS]
        figures = {}
        for paper in shown:
            details = load(Source("arxiv-paper", {"id": paper.get("id")},
                                  lambda ctx, paper_id=paper.get("id"): sources.fetch_arxiv_paper(paper_id, ctx),
                                  fresh_seconds=PAPER_SECONDS, usable_seconds=PAPER_SECONDS,
                                  validate=sources.valid_arxiv_paper, timeout_seconds=10,
                                  min_remaining_seconds=PAPER_MIN_SECONDS))["data"]
            figures[id(paper)] = details.get("figures") or []
            affiliations = details.get("affiliations") or []
            if affiliations:
                paper["institution"] = load(Source(
                    "institution", {"affiliations": affiliations},
                    lambda ctx, found=tuple(affiliations): sources.fetch_institution(found, ctx),
                    fresh_seconds=PAPER_SECONDS, usable_seconds=PAPER_SECONDS, validate=sources.valid_institution,
                    timeout_seconds=12, min_remaining_seconds=PAPER_MIN_SECONDS))["data"] or None
        logos = [(paper.get("institution") or {}).get("logo") for paper in shown]
        images = load_thumbnails([logo for logo in logos if logo], covers, size=LOGO_PIXELS, keep_alpha=True,
                                 **common)
        for paper in shown:
            candidates = figures[id(paper)]
            found = load_thumbnails(candidates, covers, size=FIGURE_PIXELS, max_bytes=FIGURE_MAX_BYTES, **common)
            if found:
                best = min(found, key=lambda url: figure_misfit(found[url], candidates.index(url)))
                paper["figure"] = best
                images[best] = found.pop(best)
                for picture in found.values():
                    picture.close()
        return images

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
