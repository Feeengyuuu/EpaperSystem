"""全网热搜: Baidu, Toutiao, Zhihu and Douyin's hot lists side by side, the story that is on
the most of them across the top.

Each list marks how its entries moved since the previous refresh; the lead story names its
place on every list it is on, with the summary and picture a platform gives for it.
"""

from __future__ import annotations

import json
import logging
import os

from plugins.base_plugin.base_plugin import BasePlugin
from plugins.base_plugin.render_provenance import attach_source_provenance
from plugins.base_plugin.source_book import LIVE, Source, SourceBook, combined_provenance
from runtime.long_task_executor import (
    current_instance_identity, current_instance_identity_validator, task_context_or_default,
)
from runtime.refresh_contracts import TaskCancelled, TaskContext
from utils.app_utils import coerce_bool, get_available_font_names
from utils.atomic_file import atomic_write_json
from utils.cache_manager import CacheBudget
from utils.http_client import get_http_client
from utils.remote_thumbnails import load_thumbnails

from . import sources


logger = logging.getLogger(__name__)
PLUGIN_ID = "cn_hot_search"
CACHE_SCHEMA = 1
CACHE_BUDGET = CacheBudget(7 * 86400, 12, 4 * 1024 * 1024)
DEFAULT_FONT = "Microsoft YaHei"
TASK_SECONDS = 45
HISTORY_FILE = "history.json"
# The lists turn over within the hour; a cached one still reads as today's news for a day.
FRESH_SECONDS = 20 * 60
USABLE_SECONDS = 86400
# Each card shows a list's first ROWS_SHOWN rows, every one with its picture at THUMB_PIXELS at most.
ROWS_SHOWN = 3
THUMB_PIXELS = (320, 200)


def _checkpoint(context):
    context.raise_if_cancelled()
    identity = current_instance_identity()
    validator = current_instance_identity_validator()
    if identity is not None and validator is not None and not validator(identity):
        raise TaskCancelled("全网热搜 instance changed during refresh")


def list_moves(current, previous):
    """{title: move} of a list against its previous reading: "new", or places gained (+) or lost (-).
    A title counts as the same entry when it names the same story (lists reword as they update)."""
    moves = {}
    for row in current or []:
        before = next((old for old in previous or [] if old.get("title") == row.get("title")), None)
        if before is None:
            before = next((old for old in previous or [] if sources.same_topic(old.get("title"), row.get("title"))),
                          None)
        moves[row.get("title")] = "new" if before is None else int(before.get("rank") or 0) - int(row.get("rank") or 0)
    return moves


class CnHotSearch(BasePlugin):
    def generate_settings_template(self):
        params = super().generate_settings_template()
        params["style_settings"] = True
        params["available_fonts"] = get_available_font_names(default=DEFAULT_FONT)
        return params

    def _cache_root(self):
        return self.cache_dir(env_var="CN_HOT_SEARCH_CACHE_DIR", leaf="cache", create=True, strip=True)

    def _cache_subdir(self, name):
        path = self._cache_root() / name
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _book(self):
        return SourceBook(self._cache_subdir("sources"), CACHE_BUDGET, schema=CACHE_SCHEMA, max_bytes=512 * 1024,
                          checkpoint=_checkpoint)

    def _history_path(self):
        return self._cache_root() / HISTORY_FILE

    def _read_history(self):
        try:
            data = json.loads(self._history_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _roll(history, platform, result):
        """When a list was just fetched, its last reading becomes the previous one."""
        entry = dict(history.get(platform) or {})
        rows = (result.get("data") or {}).get("rows")
        slim = [{"title": row.get("title"), "rank": row.get("rank")} for row in rows or []]
        if result.get("state") == LIVE and slim and slim != entry.get("current"):
            history[platform] = {"previous": entry.get("current"), "previous_at": entry.get("current_at"),
                                 "current": slim, "current_at": result.get("fetched_at")}
            return True
        return False

    @staticmethod
    def _weibo_cookie(device_config):
        """The owner's saved weibo.com login cookie, or "" (the page then leaves Weibo out)."""
        value = str(os.getenv(sources.COOKIE_KEY, "") or "").strip()
        if not value and device_config is not None and hasattr(device_config, "load_env_key"):
            try:
                value = device_config.load_env_key(sources.COOKIE_KEY) or ""
            except TaskCancelled:
                raise
            except Exception as exc:  # noqa: BLE001 - an unreadable key only leaves Weibo out.
                logger.warning("Could not read %s from device config: %s", sources.COOKIE_KEY, exc)
        return str(value or "").strip()

    def _payload(self, settings, now, *, allow_network, context, cookie=""):
        force = allow_network and coerce_bool(settings.get("forceRefresh"), default=False)
        book = self._book()
        fetchers = dict(sources.FETCHERS)
        if cookie:
            # The cookie is the owner's secret: it reaches the request, never the cache's identity.
            fetchers = {"weibo": lambda ctx: sources.fetch_weibo(cookie, ctx), **fetchers}
        results = {}
        for platform, fetch in fetchers.items():
            spec = Source(platform, {"board": "realtime"}, fetch, fresh_seconds=FRESH_SECONDS,
                          usable_seconds=USABLE_SECONDS, validate=sources.valid_rows, timeout_seconds=9)
            results[platform] = book.load(spec, now, allow_network=allow_network, context=context, force=force)
        history = self._read_history()
        if allow_network and any([self._roll(history, platform, result) for platform, result in results.items()]):
            try:
                atomic_write_json(self._history_path(), history)
            except OSError as exc:
                logger.warning("全网热搜 history not saved: %s", exc)
        lists = {platform: (result.get("data") or {}).get("rows") or [] for platform, result in results.items()}
        return {**results, "lists": lists, "topics": sources.cross_topics(lists), "history": history,
                "provenance": combined_provenance(results.values())}

    def _images(self, payload, *, allow_network, context):
        """The lead story's pictures and each card's first rows' (the page shows ROWS_SHOWN of a list)."""
        urls = []
        for topic in payload["topics"][:4]:
            urls += [row.get("image") for row in topic["rows"].values()]
        for rows in payload["lists"].values():
            urls += [row.get("image") for row in rows[:ROWS_SHOWN]]
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
        payload = self._payload(settings, now, allow_network=not cache_only, context=context,
                                cookie=self._weibo_cookie(device_config))
        images = self._images(payload, allow_network=not cache_only, context=context)
        try:
            image = render_page(self.get_dimensions(device_config), payload, settings, now, images)
        finally:
            for picture in images.values():
                picture.close()
        _checkpoint(context)
        return attach_source_provenance(image, payload["provenance"], detail=PLUGIN_ID)
