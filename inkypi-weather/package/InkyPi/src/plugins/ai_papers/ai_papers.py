"""AI 论文日报: the AI papers the Hugging Face community upvoted most today and yesterday. The
top paper leads with its figure, organisation and summary, the next four stand in cards beneath
it, and the rest of the ranking runs down the right side; with an OpenAI key their titles and
summaries read in Chinese.
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

from . import render, sources, translate


logger = logging.getLogger(__name__)
PLUGIN_ID = "ai_papers"
CACHE_SCHEMA = 1
CACHE_BUDGET = CacheBudget(14 * 86400, 64, 4 * 1024 * 1024)
DEFAULT_FONT = "Microsoft YaHei"
TASK_SECONDS = 55
# A day's list fills through the day and its votes keep coming; a reading stands for an hour.
FRESH_SECONDS = 60 * 60
USABLE_SECONDS = 3 * 86400
# A paper's arXiv figure does not change.
FIGURE_SECONDS = 30 * 86400
# The papers on the page: the lead and four cards, pictured, and the ranking down the right.
PICTURED = 5
SHOWN = PICTURED + render.RANKING_ROWS
STILL_PIXELS = (480, 360)
# arXiv figures are often large PNGs (a 1360 x 2040 sample grid runs to 6.5 MB): one may download
# to this size; its pixels stay within the thumbnail loader's limits.
STILL_MAX_BYTES = 8 * 1024 * 1024
AVATAR_PIXELS = (96, 96)
# The translation request waits at most this long and runs only with this much of the refresh left.
TRANSLATE_MIN_SECONDS = 35


def _checkpoint(context):
    context.raise_if_cancelled()
    identity = current_instance_identity()
    validator = current_instance_identity_validator()
    if identity is not None and validator is not None and not validator(identity):
        raise TaskCancelled("AI 论文日报 instance changed during refresh")


class AiPapers(BasePlugin):
    translation_client_factory = None

    def generate_settings_template(self):
        params = super().generate_settings_template()
        params["style_settings"] = True
        params["available_fonts"] = get_available_font_names(default=DEFAULT_FONT)
        return params

    def _cache_root(self):
        return self.cache_dir(env_var="AI_PAPERS_CACHE_DIR", leaf="cache", create=True, strip=True)

    def _cache_subdir(self, name):
        path = self._cache_root() / name
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _payload(self, settings, now, *, allow_network, context):
        force = allow_network and coerce_bool(settings.get("forceRefresh"), default=False)
        book = SourceBook(self._cache_subdir("sources"), CACHE_BUDGET, schema=CACHE_SCHEMA, max_bytes=512 * 1024,
                          checkpoint=_checkpoint)
        results = [book.load(Source("papers", {"day": day}, lambda ctx, day=day: sources.fetch_day(day, ctx),
                                    fresh_seconds=FRESH_SECONDS, usable_seconds=USABLE_SECONDS,
                                    validate=sources.valid_day, timeout_seconds=15),
                             now, allow_network=allow_network, context=context, force=force)
                   for day in sources.list_days(now)]
        papers = sources.ranked(result.get("data") for result in results)[:SHOWN]
        for paper in papers[:PICTURED]:
            if paper.get("image"):
                continue
            figure = book.load(Source("figure", {"id": paper["id"]},
                                      lambda ctx, paper_id=paper["id"]: sources.fetch_arxiv_figure(paper_id, ctx),
                                      fresh_seconds=FIGURE_SECONDS, usable_seconds=FIGURE_SECONDS,
                                      validate=sources.valid_figure, timeout_seconds=10),
                               now, allow_network=allow_network, context=context)
            paper["figures"] = (figure.get("data") or {}).get("urls") or []
        return {"papers": papers, "days": [(result.get("data") or {}).get("day") for result in results],
                "states": [result.get("state") for result in results],
                "provenance": combined_provenance(results)}

    def _translations(self, papers, device_config, *, allow_network, context, settings):
        kept = translate.Translations(self._cache_root() / "translations.json")
        if not coerce_bool(settings.get("translate"), default=True):
            return {}
        missing = kept.missing(papers)
        key = self._openai_key(device_config)
        if missing and key and allow_network and context.remaining_seconds() >= TRANSLATE_MIN_SECONDS:
            try:
                kept.add(translate.translate(missing, key, client_factory=self.translation_client_factory))
            except TaskCancelled:
                raise
            except Exception as exc:  # the page stays in English until a later refresh translates it
                logger.warning("AI Papers translation failed (%s)", type(exc).__name__)
        return {paper["id"]: kept.get(paper["id"]) for paper in papers if kept.get(paper["id"])}

    @staticmethod
    def _openai_key(device_config):
        load = getattr(device_config, "load_env_key", None)
        if load is None:
            return ""
        return str(load("OPEN_AI_SECRET") or load("OPENAI_API_KEY") or "").strip()

    def _images(self, payload, *, allow_network, context):
        """The papers' figures (each paper's first that loads) and their organisations' avatars
        (their logos on the Hub)."""
        papers = payload["papers"]
        common = {"cached_only": not allow_network, "http": get_http_client(), "context": context,
                  "safe_url": sources.safe_image_url, "user_agent": sources.USER_AGENT}
        stills = {}
        for paper in papers[:PICTURED]:
            for url in render.picture_urls(paper):
                found = load_thumbnails([url], self._cache_subdir("stills"), size=STILL_PIXELS,
                                        max_bytes=STILL_MAX_BYTES, **common)
                if found:
                    stills.update(found)
                    break
        avatars = load_thumbnails([paper["org_avatar"] for paper in papers if paper.get("org_avatar")],
                                  self._cache_subdir("avatars"), size=AVATAR_PIXELS, keep_alpha=True, **common)
        return {**stills, **avatars}

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
        now = sources.local_now()
        _checkpoint(context)
        payload = self._payload(settings, now, allow_network=not cache_only, context=context)
        images = self._images(payload, allow_network=not cache_only, context=context)
        payload["chinese"] = self._translations(payload["papers"], device_config, allow_network=not cache_only,
                                                context=context, settings=settings)
        _checkpoint(context)
        try:
            image = render.render_page(self.get_dimensions(device_config), payload, settings, now, images)
        finally:
            for picture in images.values():
                picture.close()
        _checkpoint(context)
        return attach_source_provenance(image, payload["provenance"], detail=PLUGIN_ID)
