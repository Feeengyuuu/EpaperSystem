from __future__ import annotations

import json
import logging
import math
import os
import re
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from html import unescape
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageOps, ImageStat

from plugins.base_plugin.base_plugin import BasePlugin
from plugins.base_plugin.presentation import PresentationMode
from plugins.base_plugin.render_provenance import (
    SourceProvenance,
    attach_source_provenance,
)
from plugins.ai_ecosystem_pulse.ai_ecosystem_pulse import AiEcosystemPulse
from plugins.ai_ecosystem_pulse.sources import parameter_text, pipeline_label
from plugins.context_cache import write_context
from plugins.tech_pulse import extras as extra_boards, item_icons, openrouter, trends
from runtime.long_task_executor import task_context_or_default
from runtime.refresh_contracts import TaskCancelled, TaskContext, TaskDeadlineExceeded
from utils.app_utils import DEFAULT_FONT_FAMILY, bounded_int, coerce_bool, get_available_font_names, get_font
from utils.flat_icons import draw_icon
from utils.http_client import get_http_client, get_http_session
from utils.image_utils import text_width
from utils.doodle_backdrop import paint_doodles
from utils.local_wordmark import load_source_logo
from utils.safe_image import safe_open_image
from utils.theme_utils import relative_luminance

logger = logging.getLogger(__name__)

PLUGIN_ID = "tech_pulse"
CACHE_SCHEMA_VERSION = "tech-pulse-v1"
DEFAULT_TIMEZONE = "America/Los_Angeles"
DEFAULT_FONT = DEFAULT_FONT_FAMILY
HN_BASE_URL = "https://hacker-news.firebaseio.com/v0"
HN_DOCS_URL = "https://github.com/HackerNews/API"
HN_ITEM_URL = "https://news.ycombinator.com/item?id={id}"
TITLE_WORDMARK_IMAGE = "title_wordmark.png"
TITLE_WORDMARK_DISPLAY_SIZE = (246, 46)
TOP_COMMENT_CANDIDATES = 3
TOP_COMMENT_MAX_CHARS = 600
REQUEST_TIMEOUT = (4, 12)
FEED_ENDPOINTS = {
    "topstories": f"{HN_BASE_URL}/topstories.json",
    "beststories": f"{HN_BASE_URL}/beststories.json",
    "newstories": f"{HN_BASE_URL}/newstories.json",
}
FEED_LABELS = {
    "topstories": "Top Stories",
    "beststories": "Best Stories",
    "newstories": "New Stories",
}
# The front page's stories with their points and comments in one small request (about 7 KB),
# ordered as topstories.json orders them; only the lead story's item is still fetched, for its
# ranked comments.
ALGOLIA_SEARCH_URL = "https://hn.algolia.com/api/v1/search"
ALGOLIA_FRONT_PAGE_PARAMS = {
    "tags": "front_page",
    "hitsPerPage": 50,
    "attributesToRetrieve": "title,url,points,num_comments,author,created_at_i",
    "attributesToHighlight": "none",
}
# A story near the top that Algolia has not tagged yet is fetched on its own, up to this many.
FRONT_PAGE_ITEM_FALLBACKS = 3
USER_AGENT = "InkyPi TechPulse/1.0 (+https://github.com/HackerNews/API)"
# The AI boards come from AI Ecosystem Pulse's sources and caches, so both pages share
# one fetch schedule and GitHub's star history.
AI_PLUGIN_ID = "ai_ecosystem_pulse"
ASSET_DIR = Path(__file__).with_name("assets")
# The header names the page's four sources by their official logos, this tall, where the
# generated icon strip used to be.
HEADER_SOURCE_LOGOS = ("hn", "skills", "huggingface", "github", "openrouter_icon")
HEADER_LOGO_SIZE = 26
# A heartbeat trace runs from each source logo to the next in the live colour, so the four
# read as one pulse: (share of the gap, height as a share of HEADER_PULSE_HEIGHT, up negative),
# clear of the logos by HEADER_PULSE_GAP.
HEADER_PULSE = ((0, 0), (0.2, 0), (0.28, -0.18), (0.35, 0), (0.42, 0.22), (0.5, -1), (0.58, 0.62), (0.65, 0),
                (0.76, -0.26), (0.86, 0), (1, 0))
HEADER_PULSE_HEIGHT = 9
HEADER_PULSE_GAP = 5
HEADER_PULSE_MIN_WIDTH = 14
# Each panel's own quiet doodles in its colour, so the four read apart: discussion, tools,
# models, repositories.
PANEL_DOODLES = {"hn": "hn", "skills": "skills", "huggingface": "models", "github": "repos"}
PANEL_DOODLE_STRENGTH = 0.2
TRENDS_FILE = "trends.json"
PAGE_SIZE = (800, 480)
HN_BOX = (14, 62, 404, 414)
SKILLS_BOX = (414, 62, 786, 168)
MODELS_BOX = (414, 174, 786, 302)
REPOS_BOX = (414, 308, 786, 414)
# OpenRouter's newest models run along the foot of the page, under both columns.
OPENROUTER_BOX = (14, 420, 786, 474)
OPENROUTER_FILE = "openrouter.json"
OPENROUTER_FRESH_HOURS = 6
OPENROUTER_CARDS = 3
# The strip takes turns (extras.py): OpenRouter's new models and LMArena's text leaderboard (its
# first ARENA_CARDS places), which is kept in EXTRAS_FILE.
EXTRAS_FILE = "extras.json"
ARENA_CARDS = 4
# Each model's maker in its own colour (its brand's), on the model's chip.
PROVIDER_COLOURS = {
    "openai": (16, 163, 127), "anthropic": (204, 120, 92), "google": (66, 133, 244), "meta-llama": (0, 100, 224),
    "mistralai": (255, 112, 0), "deepseek": (77, 107, 254), "qwen": (98, 82, 255), "moonshotai": (22, 22, 22),
    "x-ai": (40, 40, 40), "z-ai": (40, 90, 220), "minimax": (230, 60, 90), "amazon": (255, 153, 0),
    "nvidia": (118, 185, 0), "cohere": (57, 89, 77), "microsoft": (0, 120, 212), "meta": (0, 100, 224),
    "tencent": (0, 82, 217), "baidu": (41, 50, 225), "xiaomi": (255, 105, 0), "perplexity": (32, 128, 141),
    "thinkingmachines": (147, 142, 142),
}
# A maker's short name on its chip, where its id is long ("moonshotai" -> "Moonshot").
PROVIDER_NAMES = {
    "openai": "OpenAI", "anthropic": "Anthropic", "google": "Google", "meta-llama": "Meta", "mistralai": "Mistral",
    "deepseek": "DeepSeek", "qwen": "Qwen", "moonshotai": "Moonshot", "x-ai": "xAI", "z-ai": "Z.ai",
    "minimax": "MiniMax", "amazon": "Amazon", "nvidia": "NVIDIA", "cohere": "Cohere", "microsoft": "Microsoft",
    "thinkingmachines": "Thinking Machines",
}
# Each OpenRouter author (a model id's part before "/") whose official mark is in assets/logos
# (tools/build_source_logos.py), by its file's key. An author not here is named by the icon
# OpenRouter itself shows for it, fetched during a refresh (item_icons.py), and until one is at
# hand by the chip with its name.
PROVIDER_LOGOS = {
    "openrouter": "openrouter_icon", "openai": "provider-openai", "anthropic": "provider-anthropic",
    "google": "provider-google", "meta-llama": "provider-meta-llama", "meta": "provider-meta-llama",
    "mistralai": "provider-mistralai", "qwen": "provider-qwen", "deepseek": "provider-deepseek",
    "x-ai": "provider-x-ai", "moonshotai": "provider-moonshotai", "z-ai": "provider-z-ai",
    "minimax": "provider-minimax", "tencent": "provider-tencent", "baidu": "provider-baidu",
    "bytedance": "provider-bytedance", "bytedance-seed": "provider-bytedance", "amazon": "provider-amazon",
    "nvidia": "provider-nvidia", "microsoft": "provider-microsoft", "cohere": "provider-cohere",
    "inception": "provider-inception", "ricursiveai": "provider-ricursiveai", "recursive": "provider-recursive",
    "particle-ai": "provider-particle-ai", "xiaomi": "provider-xiaomi", "stepfun": "provider-stepfun", "liquid": "provider-liquid", "meituan": "provider-meituan",
    "thinkingmachines": "provider-thinkingmachines",
}
PROVIDER_MARK_HEIGHT, PROVIDER_MARK_WIDTH = 16, 46
# A maker whose official mark is its name set in two lines (Thinking Machines Lab's) is drawn this
# tall, so each line reads at the size of the card's small type, and may be this wide. It hangs from
# PROVIDER_WORDMARK_TOP px under the card's top edge, clear of the card's second line.
PROVIDER_WORDMARKS = frozenset({"provider-thinkingmachines"})
PROVIDER_WORDMARK_HEIGHT, PROVIDER_WORDMARK_WIDTH, PROVIDER_WORDMARK_TOP = 21, 56, 1
# A mark on its own ground (Kimi's, Z.ai's, Meituan's squares) fills at least this share of its box,
# some of it near white or coloured: it keeps its printed colours at night, where other marks'
# near-black parts take the page's ink.
PROVIDER_BADGE_COVER, PROVIDER_BADGE_LIGHT, PROVIDER_BADGE_COLOUR = 0.55, 0.02, 0.25
HN_LIST_ROWS = 3
SKILL_ROWS = 4
MODEL_ROWS = REPO_ROWS = 3
# Every item carries its own mark (item_icons.py): a story its site's icon, a skill and a repository
# their GitHub owner's avatar, a model its author's. They are fetched within ICON_SECONDS of a
# refresh, ending ICON_RESERVE_SECONDS before the refresh's own deadline so the page is still drawn
# in time, and kept in the cache's ICON_DIR, the avatar addresses asked in ICON_LOOKUPS beside it.
# Sizes in px: the lead story's, a listed story's, a model's or repository's, a skill's.
ICON_DIR = "icons"
ICON_LOOKUPS = "icon-lookups.json"
ICON_SECONDS, ICON_RESERVE_SECONDS = 20, 15
LEAD_ICON, STORY_ICON, BOARD_ICON, SKILL_ICON = 40, 30, 20, 15
# A story's rank sits on its icon's upper left corner as a small disc, RANK_OVERHANG px past it. A
# model's or repository's icon is too small to carry one: its rank is a plain figure before the
# icon, in a column BOARD_RANK_W wide.
STORY_RANK_DISC, RANK_OVERHANG = 17, 5
BOARD_RANK_W = 12
# The trending Spaces close the Hugging Face panel on one line this tall.
SPACES_LINE_H = 22
SPACES_SHOWN = 2
# The star trend on a repository's second line, just left of its 24-hour chip.
REPO_TREND_W = 64
FONT_SPECS = {
    "lead": (17, "bold"),
    "row": (13, "bold"),
    "section": (13, "bold"),
    "name": (12, "bold"),
    "label": (12, "bold"),
    "chip": (12, "bold"),
    "body": (12, "normal"),
    "metric": (11, "bold"),
    "small": (11, "normal"),
    "tiny": (10, "normal"),
    "tiny_bold": (10, "bold"),
}
# GitHub's own language colours (github-linguist).
LANGUAGE_COLOURS = {
    "Python": (53, 114, 165),
    "TypeScript": (49, 120, 198),
    "JavaScript": (241, 224, 90),
    "Go": (0, 173, 216),
    "Rust": (222, 165, 132),
    "Java": (176, 114, 25),
    "C++": (243, 75, 125),
    "C": (85, 85, 85),
    "Jupyter Notebook": (218, 91, 11),
    "Shell": (137, 224, 81),
    "Swift": (240, 81, 56),
    "Kotlin": (169, 123, 255),
    "HTML": (227, 76, 38),
    "MDX": (252, 179, 44),
}

LOCAL_SAMPLE_STORIES = (
    {
        "id": 430001,
        "title": "SQLite on the edge: keeping local-first apps boring",
        "url": "https://example.com/sqlite-edge-local-first",
        "by": "local_sample",
        "time": 1782520200,
        "score": 428,
        "descendants": 126,
    },
    {
        "id": 430002,
        "title": "A visual guide to transformer KV cache tradeoffs",
        "url": "https://example.com/kv-cache-visual-guide",
        "by": "sample_research",
        "time": 1782516600,
        "score": 311,
        "descendants": 88,
    },
    {
        "id": 430003,
        "title": "Show HN: An offline debugger for tiny embedded devices",
        "url": "https://example.com/offline-debugger",
        "by": "showhn",
        "time": 1782511200,
        "score": 205,
        "descendants": 47,
    },
    {
        "id": 430004,
        "title": "Why modern CSS layout bugs are usually sizing bugs",
        "url": "https://example.com/css-sizing-bugs",
        "by": "frontend_lab",
        "time": 1782505800,
        "score": 184,
        "descendants": 63,
    },
    {
        "id": 430005,
        "title": "Postgres indexes explained with maintenance cost included",
        "url": "https://example.com/postgres-index-costs",
        "by": "db_notes",
        "time": 1782500400,
        "score": 173,
        "descendants": 39,
    },
)


def provider_author(value):
    """A model's maker as OpenRouter names it, from its id or author: "tencent/hy4" -> "tencent";
    an alias's "~" dropped ("~openai/gpt-latest" -> "openai")."""
    return str(value or "").strip().split("/", 1)[0].lstrip("~").lower()


def provider_logo_key(value):
    """The assets/logos key of the official mark of a model's maker (from its id or author), or
    None for a maker without one."""
    return PROVIDER_LOGOS.get(provider_author(value))


@lru_cache(maxsize=64)
def _provider_badge(path):
    """Whether the mark at ``path`` sits on its own ground (PROVIDER_BADGE_*)."""
    try:
        with safe_open_image(path) as source:
            art = source.convert("RGBA")
    except (OSError, ValueError):
        return False
    data = art.tobytes()
    drawn = [pixel for pixel in zip(data[0::4], data[1::4], data[2::4], data[3::4]) if pixel[3] > 128]
    if len(drawn) < PROVIDER_BADGE_COVER * art.width * art.height:
        return False
    light = sum(1 for red, green, blue, _ in drawn if 0.299 * red + 0.587 * green + 0.114 * blue > 225)
    coloured = sum(1 for red, green, blue, _ in drawn if max(red, green, blue) - min(red, green, blue) >= 40)
    return light >= PROVIDER_BADGE_LIGHT * len(drawn) or coloured >= PROVIDER_BADGE_COLOUR * len(drawn)


class TechPulse(BasePlugin):
    def presentation_mode(self, settings):
        return PresentationMode.NO_CHANGE

    def generate_settings_template(self):
        params = super().generate_settings_template()
        params["style_settings"] = True
        params["available_fonts"] = get_available_font_names(default=DEFAULT_FONT)
        return params

    def generate_image(self, settings, device_config):
        settings = dict(settings or {})
        settings["_inkypi_theme"] = settings.get(
            "_inkypi_theme"
        ) or self.resolve_theme(settings, device_config)
        dimensions = self.get_dimensions(device_config)
        now = self._now_for_device(device_config)
        payload = self._payload(settings, now)
        ai = self._ai_payload(settings, device_config, now)
        theme_only = bool(settings.get("_theme_render_only"))
        history = self._history(payload, ai, now, record=not theme_only)
        if not theme_only:
            self._write_context(payload, now, ai)
        models = self._openrouter(now, cache_only=theme_only)
        boards = self._extras(now, cache_only=theme_only)
        icons = self._icons(payload, ai, models, cache_only=theme_only)
        try:
            # The boards go to the page only when they hold something: without them it is drawn as ever.
            image = self._render_page(dimensions, payload, settings, now, ai, history, models, icons,
                                      **({"extras": boards} if boards else {}))
        finally:
            for icon in icons.values():
                icon.close()
        return attach_source_provenance(
            image,
            payload.get("_source_provenance", SourceProvenance.LOCAL_FALLBACK),
            detail="tech_pulse",
        )

    def _payload(self, settings, now):
        feed = self._feed(settings)
        max_stories = bounded_int(settings.get("maxStories"), 5, 1, 8)
        min_score = self._optional_int(settings.get("minScore"))
        refresh_minutes = bounded_int(settings.get("refreshMinutes"), 30, 5, 720)
        cache_key = self._cache_key(feed, max_stories, min_score)
        theme_render_only = bool(settings.get("_theme_render_only"))
        cache_dir = self._cache_dir(create=False) if theme_render_only else self._cache_dir()
        cache = self._read_json(cache_dir / "state.json", {})
        cache_is_fresh = self._is_fresh_cache(
            cache,
            cache_key,
            now,
            refresh_minutes,
        )
        payload = self._payload_unclassified(settings, now)
        source_state = (payload.get("status") or {}).get("source_state")
        if source_state == "live":
            provenance = SourceProvenance.LIVE
        elif source_state == "cache":
            provenance = (
                SourceProvenance.FRESH_CACHE
                if cache_is_fresh
                else SourceProvenance.STALE_CACHE
            )
        else:
            provenance = SourceProvenance.LOCAL_FALLBACK
        result = dict(payload)
        result["_source_provenance"] = provenance.value
        return result

    def _ai_payload(self, settings, device_config, now):
        """Skills, models and repos from AI Ecosystem Pulse's sources, or None when unavailable.

        A theme-only render reads that plugin's last aggregate and touches neither network nor disk.
        """
        source = AiEcosystemPulse({"id": AI_PLUGIN_ID})
        try:
            if settings.get("_theme_render_only"):
                cached = self._read_json(source._cache_dir(create=False) / "aggregate.json", {})
                payload = cached if source._valid_aggregate_payload(cached) else None
            else:
                payload = source._payload({"forceRefresh": settings.get("forceRefresh")}, device_config, now)
            if not payload:
                return None
            cache_dir = source._cache_dir(create=False)
            payload = dict(payload)
            payload["_fetched_at"] = {}
            for name in ("skills", "huggingface", "github"):
                cached = self._read_json(cache_dir / f"{name}.json", {})
                payload["_fetched_at"][name] = cached.get("fetched_at") if isinstance(cached, dict) else None
            stars = self._read_json(source._data_dir(create=False) / "github-stars.json", {})
            stars = stars if isinstance(stars, dict) else {}
            payload["_star_history"] = {
                str(repo.get("full_name")): stars.get(source._github_candidate_key(repo)) or []
                for repo in payload.get("repos") or []
            }
            return payload
        except TaskCancelled:
            raise
        except Exception as exc:
            logger.warning("Tech Pulse AI boards unavailable: %s", exc)
            return None

    def _unmarked_makers(self, models):
        """(author, the Hub organisation OpenRouter names for the model) of each card the strip
        draws whose maker has no official mark in the package."""
        makers = []
        for model in ((models or {}).get("models") or [])[:OPENROUTER_CARDS]:
            model = model if isinstance(model, dict) else {}
            author = provider_author(model.get("id") or model.get("provider"))
            if author and author not in PROVIDER_LOGOS:
                makers.append((author, model.get("hub")))
        return makers

    def _icons(self, payload, ai, models=None, *, cache_only):
        """Each drawn item's own mark (item_icons.py), {key: RGBA icon}: what is kept, and during a
        refresh what can be fetched within ICON_SECONDS; with them the icons of the strip's makers
        that have no bundled mark. A missing icon only leaves its item a lettered tile (a maker its
        chip); the icons' own time running out never fails the page."""
        # The stand-in stories of an offline page name no real sites.
        sample = (payload.get("status") or {}).get("source_state") == "local_sample"
        wants = item_icons.wanted([] if sample else (payload.get("stories") or [])[:1 + HN_LIST_ROWS],
                                  (self._board_rows(ai, "skills", "skills") or [])[:SKILL_ROWS],
                                  (self._board_rows(ai, "huggingface", "models") or [])[:MODEL_ROWS],
                                  (self._board_rows(ai, "github", "repos") or [])[:REPO_ROWS],
                                  self._unmarked_makers(models))
        if not wants:
            return {}
        parent = task_context_or_default(timeout_seconds=ICON_SECONDS + ICON_RESERVE_SECONDS)
        deadline = min(parent.deadline_monotonic - ICON_RESERVE_SECONDS, parent.clock() + ICON_SECONDS)
        context = TaskContext(parent.cancel_event, deadline, parent.clock)
        try:
            cache = self._cache_dir(create=not cache_only)
            options = {"context": context, "user_agent": USER_AGENT}
            try:
                return item_icons.load(wants, cache / ICON_DIR, cache / ICON_LOOKUPS, cached_only=cache_only,
                                       http=None if cache_only else get_http_client(), **options)
            except TaskCancelled:
                # The page's own task ending passes on; the icons' time alone running out leaves
                # the page the ones already kept.
                parent.raise_if_cancelled()
                return item_icons.load(wants, cache / ICON_DIR, cache / ICON_LOOKUPS, cached_only=True, http=None,
                                       **options)
        except TaskCancelled:
            raise
        except (OSError, ValueError, RuntimeError) as exc:
            logger.warning("Tech Pulse icons unavailable: %s", exc)
            return {}

    def _history(self, payload, ai, now, record=True):
        """The boards' snapshot history; a refresh first adds what this fetch saw."""
        path = self._data_dir(create=record) / TRENDS_FILE
        state = trends.normalise_state(self._read_json(path, {}))
        if not record:
            return state
        keep = {board: set() for board in trends.BOARDS}
        status = payload.get("status") or {}
        if status.get("source_state") in ("live", "cache"):
            for story in payload.get("stories") or []:
                key = str(story.get("id") or "")
                if key:
                    keep["hn"].add(key)
                    values = {"score": int(story.get("score") or 0), "comments": int(story.get("comments") or 0)}
                    state["hn"][key] = trends.record(state["hn"].get(key), values, status.get("generated_at"), now)
        sources = ((ai or {}).get("status") or {}).get("sources") or {}
        fetched = (ai or {}).get("_fetched_at") or {}
        if sources.get("skills") not in (None, "fixture"):
            for skill in ai.get("skills") or []:
                key = self._skill_key(skill)
                keep["skills"].add(key)
                values = {"installs": int(skill.get("installs") or 0), "rank": int(skill.get("rank") or 0)}
                state["skills"][key] = trends.record(state["skills"].get(key), values, fetched.get("skills"), now)
        if sources.get("huggingface") not in (None, "fixture"):
            for index, model in enumerate(ai.get("models") or []):
                key = str(model.get("id") or "")
                keep["models"].add(key)
                values = {
                    "trend": int(model.get("trending_score") or 0),
                    "likes": int(model.get("likes") or 0),
                    "downloads": int(model.get("downloads_30d") or 0),
                    "rank": index + 1,
                }
                state["models"][key] = trends.record(state["models"].get(key), values,
                                                     fetched.get("huggingface"), now)
        for board in trends.BOARDS:
            state[board] = trends.prune(state[board], keep[board], now)
        try:
            self._write_json(path, state)
        except OSError as exc:
            logger.warning("Tech Pulse could not save its trend history: %s", exc)
        return state

    def _payload_unclassified(self, settings, now):
        feed = self._feed(settings)
        max_stories = bounded_int(settings.get("maxStories"), 5, 1, 8)
        min_score = self._optional_int(settings.get("minScore"))
        refresh_minutes = bounded_int(settings.get("refreshMinutes"), 30, 5, 720)
        cache_key = self._cache_key(feed, max_stories, min_score)
        theme_render_only = bool(settings.get("_theme_render_only"))
        cache_dir = self._cache_dir(create=False) if theme_render_only else self._cache_dir()
        cache_file = cache_dir / "state.json"
        cache = self._read_json(cache_file, {})
        force_refresh = self._enabled(settings.get("forceRefresh"), default=False)

        if settings.get("_theme_render_only"):
            if self._valid_cache(cache, cache_key):
                cached = dict(cache)
                cached["status"] = dict(cached.get("status") or {})
                cached["status"]["source_state"] = "cache"
                return cached
            raise RuntimeError(
                "Tech Pulse theme-only render requires matching cached source data."
            )

        if not force_refresh and self._is_fresh_cache(cache, cache_key, now, refresh_minutes):
            cached = dict(cache)
            cached["status"] = dict(cached.get("status") or {})
            cached["status"]["source_state"] = "cache"
            return cached

        live_error = ""
        try:
            payload = self._fetch_live_payload(feed, max_stories, min_score, now)
            payload["cache_key"] = cache_key
            self._write_json(cache_file, payload)
            return payload
        except TaskCancelled:
            raise
        except Exception as exc:
            live_error = str(exc)
            logger.warning("Tech Pulse live fetch failed: %s", exc)

        if self._valid_stale_cache(cache):
            cached = dict(cache)
            cached["status"] = dict(cached.get("status") or {})
            cached["status"]["source_state"] = "cache"
            cached["status"]["live_error"] = live_error
            return cached

        return self._local_sample_payload(feed, max_stories, min_score, now, cache_key, error=live_error)

    def _fetch_live_payload(self, feed, max_stories, min_score, now):
        session = get_http_session()
        headers = {"User-Agent": USER_AGENT}
        feed_url = FEED_ENDPOINTS[feed]
        response = session.get(feed_url, timeout=REQUEST_TIMEOUT, headers=headers)
        response.raise_for_status()
        story_ids = response.json()
        if not isinstance(story_ids, list):
            raise RuntimeError(f"Unexpected Hacker News feed response for {feed}")

        front_page = self._fetch_front_page(session, headers, now) if feed == "topstories" else {}
        stories, lead_kids = (self._front_page_stories(session, headers, story_ids, front_page, max_stories,
                                                       min_score, now) if front_page else ([], None))
        source_urls = [feed_url, ALGOLIA_SEARCH_URL, HN_DOCS_URL] if stories else [feed_url, HN_DOCS_URL]
        if not stories:
            stories, lead_kids = self._item_stories(session, headers, story_ids, max_stories, min_score, now)

        if not stories:
            raise RuntimeError("No displayable Hacker News stories were returned")

        top_comment = self._fetch_top_comment(session, headers, lead_kids, now)
        if top_comment:
            stories[0]["top_comment"] = top_comment

        return self._build_payload(feed, stories, "live", now, source_urls)

    def _item_stories(self, session, headers, story_ids, max_stories, min_score, now):
        """The feed's first displayable stories, one item request each; with the lead's comment ids."""
        stories = []
        lead_kids = None
        inspect_count = min(len(story_ids), max(20, max_stories * 6))
        for raw_id in story_ids[:inspect_count]:
            item = self._fetch_item(session, headers, raw_id)
            story = self._parse_story_item(item, now=now)
            if not story:
                continue
            if min_score is not None and story["score"] < min_score:
                continue
            if not stories:
                lead_kids = item.get("kids")
            story["rank"] = len(stories) + 1
            stories.append(story)
            if len(stories) >= max_stories:
                break
        return stories, lead_kids

    def _fetch_item(self, session, headers, raw_id):
        """One HN item, or None when it cannot be read."""
        try:
            response = session.get(f"{HN_BASE_URL}/item/{int(raw_id)}.json", timeout=REQUEST_TIMEOUT,
                                   headers=headers)
            response.raise_for_status()
            return response.json()
        except TaskCancelled:
            raise  # a cancelled refresh must not cache the stories fetched so far
        except Exception as exc:
            logger.debug("Skipping HN item %s: %s", raw_id, exc)
            return None

    def _fetch_front_page(self, session, headers, now):
        """{story id: story} for the stories Algolia tags as on the front page; {} when it fails."""
        try:
            response = session.get(ALGOLIA_SEARCH_URL, params=ALGOLIA_FRONT_PAGE_PARAMS, timeout=REQUEST_TIMEOUT,
                                   headers=headers)
            response.raise_for_status()
            hits = (response.json() or {}).get("hits")
        except TaskCancelled:
            raise
        except Exception as exc:
            logger.info("Tech Pulse falls back to HN items: Algolia front page unavailable (%s)", exc)
            return {}
        stories = {}
        for hit in hits if isinstance(hits, list) else []:
            story = self._parse_algolia_hit(hit, now)
            if story:
                stories[story["id"]] = story
        return stories

    def _parse_algolia_hit(self, hit, now):
        """An Algolia front-page hit as the page's story (the same fields an HN item gives)."""
        if not isinstance(hit, dict):
            return None
        story_id = self._optional_int(hit.get("objectID"))
        if not story_id:
            return None
        return self._parse_story_item({
            "id": story_id, "type": "story", "title": hit.get("title"), "url": hit.get("url"),
            "by": hit.get("author"), "time": hit.get("created_at_i"), "score": hit.get("points"),
            "descendants": hit.get("num_comments"),
        }, now=now)

    def _front_page_stories(self, session, headers, story_ids, front_page, max_stories, min_score, now):
        """The front page's stories in HN's order from Algolia's hits; a story near the top Algolia
        has not tagged yet is fetched on its own. Returns (stories, the lead's comment ids)."""
        stories = []
        fallbacks = 0
        for raw_id in story_ids[:len(front_page) + FRONT_PAGE_ITEM_FALLBACKS]:
            story_id = self._optional_int(raw_id)
            story = dict(front_page[story_id]) if story_id in front_page else None
            if story is None and fallbacks < FRONT_PAGE_ITEM_FALLBACKS:
                fallbacks += 1
                story = self._parse_story_item(self._fetch_item(session, headers, raw_id), now=now)
            if not story or (min_score is not None and story["score"] < min_score):
                continue
            story["rank"] = len(stories) + 1
            stories.append(story)
            if len(stories) >= max_stories:
                break
        lead_kids = None
        if stories:
            # HN's item lists the lead's comments in their ranked order; Algolia's do not.
            lead = self._fetch_item(session, headers, stories[0]["id"]) or {}
            lead_kids = lead.get("kids")
        return stories, lead_kids

    def _fetch_top_comment(self, session, headers, kids, now):
        # HN lists kids in ranked display order, so the first live comment is
        # the one the discussion page shows on top.
        if not isinstance(kids, list):
            return None
        for raw_id in kids[:TOP_COMMENT_CANDIDATES]:
            try:
                response = session.get(
                    f"{HN_BASE_URL}/item/{int(raw_id)}.json",
                    timeout=REQUEST_TIMEOUT,
                    headers=headers,
                )
                response.raise_for_status()
                comment = self._parse_comment_item(response.json(), now=now)
            except TaskCancelled:
                raise
            except Exception as exc:
                logger.debug("Skipping HN comment %s: %s", raw_id, exc)
                continue
            if comment:
                return comment
        return None

    def _parse_comment_item(self, item, now=None):
        if not isinstance(item, dict):
            return None
        if item.get("deleted") or item.get("dead") or item.get("type") != "comment":
            return None
        text = self._comment_text(item.get("text"))
        if not text:
            return None
        return {
            "id": self._optional_int(item.get("id")) or 0,
            "by": self._clean_text(item.get("by")) or "unknown",
            "text": text,
            "age_hours": self._age_hours(self._published_at(self._optional_int(item.get("time"))), now),
        }

    def _comment_text(self, value):
        # Tags are stripped before entities are decoded so escaped markup in
        # the comment survives as literal text.
        text = re.sub(r"<p>", " ", str(value or ""), flags=re.IGNORECASE)
        text = self._clean_text(re.sub(r"<[^>]*>", "", text))
        if len(text) > TOP_COMMENT_MAX_CHARS:
            text = text[:TOP_COMMENT_MAX_CHARS - 3].rsplit(" ", 1)[0].rstrip() + "..."
        return text

    def _parse_story_item(self, item, now=None):
        if not isinstance(item, dict):
            return None
        if item.get("deleted") or item.get("dead"):
            return None
        if item.get("type") not in (None, "story"):
            return None
        title = self._clean_text(item.get("title"))
        if not title:
            return None

        story_id = self._optional_int(item.get("id")) or 0
        raw_time = self._optional_int(item.get("time"))
        published = self._published_at(raw_time)
        age_hours = self._age_hours(published, now)

        url = str(item.get("url") or "").strip()
        hn_url = HN_ITEM_URL.format(id=story_id) if story_id else ""
        domain = self._domain(url or hn_url)
        comments = self._optional_int(item.get("descendants")) or 0
        score = self._optional_int(item.get("score")) or 0

        return {
            "id": story_id,
            "rank": 0,
            "title": title,
            "url": url,
            "hn_url": hn_url,
            "domain": domain,
            "by": self._clean_text(item.get("by")) or "unknown",
            "time": raw_time,
            "time_iso": published.isoformat() if published else "",
            "age_hours": age_hours,
            "score": score,
            "comments": comments,
        }

    def _local_sample_payload(self, feed, max_stories, min_score, now, cache_key, error=""):
        stories = []
        for item in LOCAL_SAMPLE_STORIES:
            story = self._parse_story_item({"type": "story", **item}, now=now)
            if not story:
                continue
            if min_score is not None and story["score"] < min_score:
                continue
            story["rank"] = len(stories) + 1
            stories.append(story)
            if len(stories) >= max_stories:
                break
        if not stories:
            stories = [self._parse_story_item({"type": "story", **LOCAL_SAMPLE_STORIES[0]}, now=now)]
            stories[0]["rank"] = 1
        payload = self._build_payload(feed, stories, "local_sample", now, [HN_DOCS_URL])
        payload["cache_key"] = cache_key
        if error:
            payload["status"]["live_error"] = error
        return payload

    def _build_payload(self, feed, stories, source_state, now, source_urls):
        total_score = sum(int(story.get("score") or 0) for story in stories)
        total_comments = sum(int(story.get("comments") or 0) for story in stories)
        top_score = max((int(story.get("score") or 0) for story in stories), default=0)
        return {
            "schema": CACHE_SCHEMA_VERSION,
            "feed": feed,
            "feed_label": FEED_LABELS.get(feed, FEED_LABELS["topstories"]),
            "stories": stories,
            "stats": {
                "count": len(stories),
                "total_score": total_score,
                "total_comments": total_comments,
                "top_score": top_score,
            },
            "status": {
                "source_state": source_state,
                "generated_at": self._to_utc(now).isoformat(),
                "source_urls": source_urls,
            },
        }

    def _openrouter(self, now, *, cache_only=False):
        """OpenRouter's newest models: the cached list while it is fresh (or a theme render asks
        for no network), else a new fetch; the last list when OpenRouter is down; None without one."""
        path = self._cache_dir(create=not cache_only) / OPENROUTER_FILE
        cached = self._read_json(path, {})
        cached = cached if isinstance(cached, dict) and isinstance(cached.get("models"), list) else {}
        fetched = self._parse_datetime(cached.get("fetched_at")) if cached else None
        fresh = fetched is not None and 0 <= (self._to_utc(now) - fetched).total_seconds() < OPENROUTER_FRESH_HOURS * 3600
        if cache_only or fresh:
            return cached or None
        try:
            data = openrouter.fetch_models(now)
            data["fetched_at"] = self._to_utc(now).isoformat()
            self._write_json(path, data)
            return data
        except TaskCancelled:
            raise
        except Exception as exc:
            logger.warning("Tech Pulse OpenRouter models unavailable: %s", exc)
            return cached or None

    def _extras(self, now, *, cache_only=False):
        """The boards that take turns with OpenRouter's strip (extras.py), from their file: brought
        up to date on a refresh, read as they are for a theme render; None without the file. They
        are optional: nothing here can fail the page."""
        try:
            path = self._cache_dir(create=not cache_only) / EXTRAS_FILE
            cached = self._read_json(path, {})
            cached = cached if isinstance(cached, dict) and cached else None
            if cache_only:
                return cached
            state = extra_boards.refresh(cached or {}, now)
            if state != cached:
                self._write_json(path, state)
            return state if extra_boards.arena_models(state) else None
        except (TaskCancelled, TaskDeadlineExceeded):
            raise
        except Exception as exc:
            logger.warning("Tech Pulse extra boards unavailable: %s", type(exc).__name__)
            return None

    def _render_page(self, dimensions, payload, settings, now, ai=None, history=None, models=None, icons=None,
                     extras=None):
        palette = self._palette(settings)
        image = Image.new("RGB", PAGE_SIZE, palette["background"])
        draw = ImageDraw.Draw(image)
        family = settings.get("fontFamily") or settings.get("font_family") or DEFAULT_FONT
        fonts = {name: self._load_font(family, size, weight) for name, (size, weight) in FONT_SPECS.items()}
        history = history or {}
        icons = icons or {}

        self._draw_background_grid(draw, *PAGE_SIZE, palette, 1.0)
        self._draw_header(image, draw, payload, ai, now, palette, fonts)
        self._draw_hn_panel(image, draw, HN_BOX, payload, settings, palette, fonts, history, now, icons=icons)
        self._draw_skills(image, draw, SKILLS_BOX, ai, palette, fonts, history, now, icons=icons)
        self._draw_models(image, draw, MODELS_BOX, ai, palette, fonts, history, now, icons=icons)
        self._draw_repos(image, draw, REPOS_BOX, ai, palette, fonts, now, icons=icons)
        self._draw_bottom_strip(image, draw, OPENROUTER_BOX, extra_boards.strip_turn(now, extras), models, extras,
                                palette, fonts, now, icons=icons)
        if tuple(dimensions) != PAGE_SIZE:
            image = image.resize(tuple(dimensions), Image.LANCZOS)
        return image

    def _draw_header(self, image, draw, payload, ai, now, palette, fonts):
        light_page = sum(palette["background"]) > 600
        if not self._draw_title_wordmark(image, 14, 8, (214, 40), ink=palette["ink"] if light_page else None):
            self._draw_text(draw, (14, 30), "Tech Pulse", fonts["lead"], palette["ink"], anchor="lm")
        title, subtitle = "HACKER NEWS + AI", "stories / skills / models / repos"
        self._draw_text(draw, (238, 22), title, fonts["label"], palette["ink"], anchor="lm")
        self._draw_text(draw, (238, 38), subtitle, fonts["small"], palette["muted"], anchor="lm")
        subtitle_right = 238 + max(self._text_width(draw, title, fonts["label"]),
                                   self._text_width(draw, subtitle, fonts["small"]))

        status = self._status_label(payload, ai)
        chip = f"{status}  {now.strftime('%H:%M')}"
        chip_box = (786 - self._text_width(draw, chip, fonts["chip"]) - 34, 16, 786, 42)
        self._pill(draw, chip_box, palette["panel"], palette["rule"], 13)
        dot = palette["up"] if status == "LIVE" else palette["amber"]
        draw.ellipse((chip_box[0] + 11, 25, chip_box[0] + 19, 33), fill=dot)
        self._draw_text(draw, (chip_box[0] + 25, 29), chip, fonts["chip"], palette["ink"], anchor="lm")
        self._draw_header_sources(image, subtitle_right + 14, chip_box[0] - 14, 30, palette)
        draw.line((14, 55, 786, 55), fill=palette["rule"], width=2)

    def _draw_header_sources(self, image, left, right, middle, palette):
        """The sources' official logos spread evenly between ``left`` and ``right``, each linked
        to the next by a heartbeat trace."""
        count = len(HEADER_SOURCE_LOGOS)
        step = (right - left - HEADER_LOGO_SIZE) / max(1, count - 1)
        if step < HEADER_LOGO_SIZE + 6:
            return
        for index, key in enumerate(HEADER_SOURCE_LOGOS):
            self._paste_logo(image, key, (left + index * step, middle - HEADER_LOGO_SIZE / 2), HEADER_LOGO_SIZE,
                             palette)
            if index:
                self._draw_pulse(image, (left + (index - 1) * step + HEADER_LOGO_SIZE + HEADER_PULSE_GAP, middle,
                                         left + index * step - HEADER_PULSE_GAP), palette["up"])

    @staticmethod
    def _draw_pulse(image, span, colour):
        """HEADER_PULSE from x1 to x2 on the line y of ``span``: a bright 2 px trace over a soft
        5 px glow, drawn four times over and scaled down so its slopes stay smooth."""
        x1, y, x2 = span
        if x2 - x1 < HEADER_PULSE_MIN_WIDTH:
            return
        scale, pad = 4, 3
        width, height = math.ceil(x2 - x1) + 2 * pad, 2 * (HEADER_PULSE_HEIGHT + pad)
        layer = Image.new("RGBA", (width * scale, height * scale), (*colour, 0))
        pen = ImageDraw.Draw(layer)
        points = [((pad + share * (x2 - x1)) * scale, (height / 2 + rise * HEADER_PULSE_HEIGHT) * scale)
                  for share, rise in HEADER_PULSE]
        pen.line(points, fill=(*colour, 70), width=5 * scale, joint="curve")
        pen.line(points, fill=(*colour, 255), width=2 * scale, joint="curve")
        layer = layer.resize((width, height), Image.LANCZOS)
        image.paste(layer.convert("RGB"), (round(x1) - pad, round(y - height / 2)), layer.getchannel("A"))

    def _status_label(self, payload, ai):
        hn = (payload.get("status") or {}).get("source_state")
        boards = ((ai or {}).get("status") or {}).get("aggregate")
        if hn == "local_sample" and boards in (None, "DEMO"):
            return "DEMO"
        if hn in ("live", "cache") and boards == "LIVE":
            return "LIVE"
        return "PARTIAL"

    def _section_title(self, image, draw, box, logo, title, detail, colour, palette, fonts):
        """The panel with a source logo, title and detail line; returns the title's centre line."""
        x0, y0, x1, y1 = box
        self._panel(draw, box, palette, 1.0)
        if logo in PANEL_DOODLES:
            inside = (x0 + 2, y0 + 2, x1 - 2, y1 - 2)
            paint_doodles(image, inside, ASSET_DIR / "doodles" / f"{PANEL_DOODLES[logo]}.png", colour,
                          strength=PANEL_DOODLE_STRENGTH, radius=12, offset=inside[:2])
        centre = y0 + 14
        self._paste_logo(image, logo, (x0 + 12, centre - 8), 16, palette)
        self._draw_text(draw, (x0 + 34, centre), title, fonts["section"], colour, anchor="lm")
        title_right = x0 + 34 + self._text_width(draw, title, fonts["section"])
        self._draw_text(draw, (title_right + 8, centre + 1), detail, fonts["tiny"], palette["muted"], anchor="lm")
        return centre

    def _mark(self, image, key, xy, size, palette, fonts, icons):
        """An item's own mark ``size`` px with its upper left corner at ``xy``: its icon without a
        plate (item_icons.on_page), or its lettered tile when the icon is not at hand. A story on
        Hacker News itself (no key) carries Hacker News' logo."""
        x, y = round(xy[0]), round(xy[1])
        if not key:
            self._paste_logo(image, "hn", (x, y), size, palette)
            return
        icon, tile = (icons or {}).get(key), None
        if icon is not None:
            try:
                tile = item_icons.on_page(icon, size, palette["panel"])
            except (OSError, ValueError):
                tile = None
        if tile is None:
            font = fonts["lead" if size >= 28 else "label" if size >= 20 else "tiny_bold"]
            tile = item_icons.monogram(key, size, font)
        image.paste(tile, (x, y), tile)

    def _rank_disc(self, draw, corner, size, rank, fill, ink, palette, fonts):
        """A story's rank as a disc over its icon's upper left ``corner``, RANK_OVERHANG px past it,
        ringed in the panel's colour so it parts from the icon."""
        left, top = corner[0] - RANK_OVERHANG, corner[1] - RANK_OVERHANG
        draw.ellipse((left - 1, top - 1, left + size, top + size), fill=palette["panel"])
        draw.ellipse((left, top, left + size - 1, top + size - 1), fill=fill)
        self._draw_text(draw, (left + size / 2, top + size / 2), str(rank), fonts["metric"], ink, anchor="mm")

    def _draw_hn_panel(self, image, draw, box, payload, settings, palette, fonts, history, now, icons=None):
        x0, y0, x1, y1 = box
        stories = payload.get("stories") or []
        stats = payload.get("stats") or {}
        feed = (payload.get("feed_label") or "Top Stories").upper()
        centre = self._section_title(image, draw, box, "hn", "HACKER NEWS", feed, palette["orange"], palette, fonts)
        total = (f"{self._compact_number(stats.get('total_score', 0))} pts"
                 f" / {self._compact_number(stats.get('total_comments', 0))} cmt")
        self._draw_text(draw, (x1 - 12, centre), total, fonts["small"], palette["muted"], anchor="rm")
        if not stories:
            self._draw_text(draw, (x0 + 12, y0 + 60), "No stories available", fonts["row"], palette["muted"])
            return
        board = history.get("hn") or {}
        lead_bottom = self._draw_lead(image, draw, (x0 + 12, y0 + 30, x1 - 12, y1), stories[0], settings, palette,
                                      fonts, board, now, icons=icons)
        draw.line((x0 + 12, lead_bottom + 6, x1 - 12, lead_bottom + 6), fill=palette["rule"], width=1)
        self._draw_story_list(draw, (x0 + 12, lead_bottom + 8, x1 - 12, y1 - 4), stories[1:1 + HN_LIST_ROWS],
                              settings, palette, fonts, board, now, HN_LIST_ROWS, image, icons)

    def _draw_lead(self, image, draw, box, story, settings, palette, fonts, history, now, icons=None):
        """The top story: its site's icon with the rank on its corner, title, meta, points trend and
        top comment; returns its bottom edge."""
        x0, y0, x1, _y1 = box
        icon_x = x0 + RANK_OVERHANG
        self._mark(image, item_icons.story_key(story), (icon_x, y0), LEAD_ICON, palette, fonts, icons)
        self._rank_disc(draw, (icon_x, y0), STORY_RANK_DISC, story.get("rank") or 1, palette["orange"],
                        (255, 255, 255), palette, fonts)
        # The title and the line under it share one column beside the icon.
        text_x = icon_x + LEAD_ICON + 8
        lines = self._wrap_text(draw, story.get("title") or "No story available", fonts["lead"], x1 - text_x,
                                max_lines=2, ellipsis=True)
        for index, line in enumerate(lines):
            self._draw_text(draw, (text_x, y0 + 11 + index * 23), line, fonts["lead"], palette["ink"], anchor="lm")

        meta_y = y0 + 11 + len(lines) * 23 + 2
        right = self._draw_story_metrics(draw, x1, meta_y, story, palette, fonts)
        meta = self._story_meta(story, settings, byline=True)
        if meta:
            self._draw_text(draw, (text_x, meta_y), self._fit_text(draw, meta, fonts["small"], right - text_x - 10),
                            fonts["small"], palette["muted"], anchor="lm")

        trend_y = meta_y + 20
        rate, measured = self._velocity(story, history, now)
        rate_text = self._rate_text(rate, measured)
        rate_left = x1
        if rate_text:
            rate_left = self._draw_text(draw, (x1, trend_y), rate_text, fonts["metric"],
                                        palette["up"] if measured and rate > 0 else palette["muted"], anchor="rm")
        self._draw_text(draw, (x0, trend_y), "POINTS", fonts["tiny_bold"], palette["muted"], anchor="lm")
        spark_box = (x0 + 46, trend_y - 7, rate_left - 12, trend_y + 7)
        values = trends.series(history.get(str(story.get("id"))), "score", now)
        if not self._sparkline(draw, spark_box, values, palette):
            self._dotted(draw, spark_box[0], spark_box[2], trend_y, palette["rule"])

        preview_top = trend_y + 13
        self._draw_hn_story_preview(image, draw, (x0, preview_top, x1, preview_top + 78), story, palette, 1.0,
                                    fonts["label"], fonts["body"])
        return preview_top + 78

    def _draw_story_list(self, draw, box, stories, settings, palette, fonts, history, now, rows=HN_LIST_ROWS,
                         image=None, icons=None):
        x0, y0, x1, y1 = box
        row_h = (y1 - y0) / max(1, rows)
        for index, story in enumerate(stories):
            top = y0 + index * row_h
            self._story_row(draw, (x0, top, x1, top + row_h), story, settings, palette, fonts, history, now,
                            image=image, icons=icons)
            if index < len(stories) - 1:
                self._dotted(draw, x0, x1, top + row_h, palette["rule"])

    def _story_row(self, draw, box, story, settings, palette, fonts, history, now, image=None, icons=None):
        """A listed story: its site's icon with the rank on its corner, then its title over its
        source, age and figures. Without an ``image`` to paste on, only the rank's disc stands there."""
        x0, y0, x1, y1 = box
        middle = (y0 + y1) / 2
        title_y, meta_y = middle - 9, middle + 10
        icon_x, icon_y = x0 + RANK_OVERHANG, middle - STORY_ICON / 2
        if image is not None:
            self._mark(image, item_icons.story_key(story), (icon_x, icon_y), STORY_ICON, palette, fonts, icons)
        self._rank_disc(draw, (icon_x, round(icon_y)), STORY_RANK_DISC, story.get("rank") or "", palette["orange"],
                        (255, 255, 255), palette, fonts)
        text_x = icon_x + STORY_ICON + 8
        title = self._fit_text(draw, story.get("title") or "", fonts["row"], x1 - text_x)
        self._draw_text(draw, (text_x, title_y), title, fonts["row"], palette["ink"], anchor="lm")

        right = self._draw_story_metrics(draw, x1, meta_y, story, palette, fonts)
        rate, measured = self._velocity(story, history, now)
        rate_text = self._rate_text(rate, measured)
        if rate_text:
            right = self._draw_text(draw, (right - 8, meta_y), rate_text, fonts["tiny_bold"],
                                    palette["up"] if measured and rate > 0 else palette["muted"], anchor="rm")
        values = trends.series(history.get(str(story.get("id"))), "score", now)
        if self._sparkline(draw, (right - 44, meta_y - 5, right - 6, meta_y + 5), values, palette):
            right -= 44
        meta = self._story_meta(story, settings, byline=False)
        if meta:
            self._draw_text(draw, (text_x, meta_y), self._fit_text(draw, meta, fonts["small"], right - text_x - 8),
                            fonts["small"], palette["muted"], anchor="lm")

    def _draw_story_metrics(self, draw, right, y, story, palette, fonts):
        """Points and comments ending at ``right``; returns where they start."""
        right = self._metric(draw, right, y, "comment", self._compact_number(story.get("comments") or 0),
                             palette["cyan"], palette, fonts)
        return self._metric(draw, right - 8, y, "up", self._compact_number(story.get("score") or 0),
                            palette["orange"], palette, fonts)

    def _metric(self, draw, right, y, icon, text, colour, palette, fonts, font="metric"):
        """An icon and a number ending at ``right``; returns the icon's left edge."""
        # One pixel in: a right-anchored glyph's ink can overhang its advance by a pixel.
        left = self._draw_text(draw, (right - 1, y), text, fonts[font], palette["ink"], anchor="rm")
        draw_icon(draw, icon, (left - 13, y - 5), 10, colour, palette["panel"])
        return left - 13

    def _story_meta(self, story, settings, byline):
        parts = []
        if self._enabled(settings.get("showDomain"), default=True):
            parts.append(story.get("domain") or "news.ycombinator.com")
        if self._enabled(settings.get("showByline"), default=True):
            parts.append(self._byline(story) if byline else self._age_text(story.get("age_hours")))
        return " | ".join(parts)

    def _velocity(self, story, history, now):
        """(points per hour, whether it was measured between snapshots) for a story."""
        found = trends.change(history.get(str(story.get("id"))), "score", now, hours=3)
        if found and found[1] >= 1:
            return found[0] / found[1], True
        age = self._optional_int(story.get("age_hours")) or 0
        score = self._optional_int(story.get("score")) or 0
        if age >= 1 and score:
            return score / age, False
        return None, False

    def _rate_text(self, rate, measured):
        """'+42/h' between snapshots, '~42/h' averaged since posting."""
        if rate is None:
            return ""
        value = f"{round(rate):d}/h"
        return f"+{value}" if measured and rate > 0 else f"~{value}"

    def _draw_skills(self, image, draw, box, ai, palette, fonts, history, now, icons=None):
        x0, y0, x1, y1 = box
        centre = self._section_title(image, draw, box, "skills", "AGENT SKILLS", "skills.sh / 24H", palette["violet"],
                                     palette, fonts)
        self._draw_text(draw, (x1 - 12, centre), "INSTALLS", fonts["tiny_bold"], palette["muted"], anchor="rm")
        rows = self._board_rows(ai, "skills", "skills")
        if rows is None:
            return self._board_offline(draw, box, palette, fonts)
        board = history.get("skills") or {}
        row_h = (y1 - y0 - 32) / SKILL_ROWS
        for index, skill in enumerate(rows[:SKILL_ROWS]):
            middle = y0 + 29 + row_h * (index + 0.5)
            if index == 0:
                draw.rounded_rectangle((x0 + 6, middle - row_h / 2, x1 - 6, middle + row_h / 2), radius=4,
                                       fill=palette["violet_tint"])
            points = board.get(self._skill_key(skill))
            self._draw_text(draw, (x0 + 30, middle), str(skill.get("rank") or ""), fonts["name"],
                            palette["violet"] if index == 0 else palette["ink"], anchor="rm")
            self._rank_move(draw, x0 + 34, middle, points, now, palette, fonts)
            installs = skill.get("installs_display") or self._compact_number(skill.get("installs") or 0)
            right = self._metric(draw, x1 - 12, middle, "download", installs, palette["violet"], palette, fonts,
                                 font="name")
            right = self._delta(draw, right - 6, middle, trends.change(points, "installs", now), palette, fonts)
            if self._sparkline(draw, (right - 40, middle - 5, right - 6, middle + 5),
                               trends.series(points, "installs", now), palette):
                right -= 40
            # Its GitHub owner's avatar leads the name.
            owner = item_icons.owner_key(skill.get("source")) or f"skill:{skill.get('name') or '?'}"
            self._mark(image, owner, (x0 + 56, middle - SKILL_ICON / 2), SKILL_ICON, palette, fonts, icons)
            name_x = x0 + 56 + SKILL_ICON + 5
            # The name keeps its full width while the source can still show a little after it (the
            # avatar already stands for the owner, so the name keeps the width it had without it).
            room = right - name_x - 8
            name = self._fit_text(draw, skill.get("name") or "", fonts["name"], room - 26)
            self._draw_text(draw, (name_x, middle), name, fonts["name"], palette["ink"], anchor="lm")
            source_x = name_x + 6 + self._text_width(draw, name, fonts["name"])
            if right - source_x - 8 >= 24:
                source = self._fit_text(draw, skill.get("source") or "", fonts["tiny"], right - source_x - 8)
                self._draw_text(draw, (source_x, middle + 1), source, fonts["tiny"], palette["muted"], anchor="lm")

    def _board_mark(self, image, draw, key, row, rank, colour, palette, fonts, icons):
        """A model's or repository's rank as a plain figure in ``colour`` at the left of its ``row``
        (left, top, height), then its own mark; the x its name starts at."""
        left, top, height = row
        self._draw_text(draw, (left + BOARD_RANK_W / 2 - 1, top + height / 2), str(rank), fonts["name"], colour,
                        anchor="mm")
        icon_x = left + BOARD_RANK_W
        self._mark(image, key or "item:?", (icon_x, top + (height - BOARD_ICON) / 2), BOARD_ICON, palette, fonts,
                   icons)
        return icon_x + BOARD_ICON + 6

    def _draw_models(self, image, draw, box, ai, palette, fonts, history, now, icons=None):
        x0, y0, x1, y1 = box
        self._section_title(image, draw, box, "huggingface", "HUGGING FACE", "TRENDING MODELS", palette["hf_text"],
                            palette, fonts)
        rows = self._board_rows(ai, "huggingface", "models")
        if rows is None:
            return self._board_offline(draw, box, palette, fonts)
        board = history.get("models") or {}
        spaces = [space for space in (ai or {}).get("spaces") or []
                  if isinstance(space, dict) and space.get("title")][:SPACES_SHOWN]
        row_h = (y1 - y0 - 30 - (SPACES_LINE_H if spaces else 0)) / 3
        columns = (("trend", "fire", "trending_score", palette["orange"]),
                   ("likes", "heart", "likes", palette["down"]),
                   ("downloads", "download", "downloads_30d", palette["cyan"]))
        for index, model in enumerate(rows[:MODEL_ROWS]):
            top = y0 + 28 + row_h * index
            line1, line2 = top + row_h * 0.31, top + row_h * 0.77
            points = board.get(str(model.get("id") or ""))
            # Its rank in the Hub's yellow, then its author's avatar.
            text_x = self._board_mark(image, draw, item_icons.author_key(model), (x0 + 12, top, row_h), index + 1,
                                      palette["hf_text"], palette, fonts, icons)
            moved_w = self._rank_move(draw, text_x, line2, points, now, palette, fonts)
            right = x1 - 12
            for metric, icon, field, colour in reversed(columns):
                self._metric(draw, right, line1, icon, self._compact_number(model.get(field) or 0), colour, palette,
                             fonts)
                self._delta(draw, right, line2, trends.change(points, metric, now), palette, fonts)
                right -= 60
            width = right - text_x
            self._draw_text(draw, (text_x, line1), self._fit_text(draw, model.get("id") or "", fonts["name"], width),
                            fonts["name"], palette["ink"], anchor="lm")
            self._model_facts(draw, text_x + (moved_w + 6 if moved_w else 0), line2, right - 6, model, palette, fonts)
            if index < 2:
                self._dotted(draw, x0 + 12, x1 - 12, top + row_h, palette["rule"])
        if spaces:
            self._draw_spaces(draw, (x0 + 12, y1 - 4 - SPACES_LINE_H, x1 - 12, y1 - 4), spaces, palette, fonts)

    def _model_facts(self, draw, x, y, right, model, palette, fonts):
        """A model's task, then its size in the Hub's colour and how many inference providers serve it;
        the task gives way first."""
        tag = pipeline_label(model.get("pipeline_tag"))
        size = parameter_text(model.get("params"))
        providers = model.get("providers") if isinstance(model.get("providers"), list) else []
        served = f"{len(providers)} 家推理" if providers else ""
        size_w = self._text_width(draw, size, fonts["tiny_bold"]) if size else 0
        served_w = self._text_width(draw, served, fonts["tiny"]) if served else 0
        facts_w = size_w + (6 if size_w else 0) + served_w + (6 if served_w else 0)
        tag_w = min(self._text_width(draw, tag, fonts["tiny"]), max(0, right - x - facts_w))
        if tag_w >= 24:
            self._draw_text(draw, (x, y), self._fit_text(draw, tag, fonts["tiny"], tag_w), fonts["tiny"],
                            palette["muted"], anchor="lm")
            x += tag_w + 6
        if size and x + size_w <= right:
            self._draw_text(draw, (x, y), size, fonts["tiny_bold"], palette["hf_text"], anchor="lm")
            x += size_w + 6
        if served and x + served_w <= right:
            self._draw_text(draw, (x, y), served, fonts["tiny"], palette["muted"], anchor="lm")

    def _draw_spaces(self, draw, box, spaces, palette, fonts):
        """The trending Spaces on one line under the models: a SPACES chip, then each Space's title and
        likes while they fit."""
        x0, y0, x1, y1 = box
        self._dotted(draw, x0, x1, y0, palette["rule"])
        middle = (y0 + y1) / 2 + 1
        label = "SPACES"
        chip_w = self._text_width(draw, label, fonts["tiny_bold"]) + 10
        draw.rounded_rectangle((x0, middle - 7, x0 + chip_w, middle + 7), radius=4, fill=palette["hf"])
        self._draw_text(draw, (x0 + chip_w / 2, middle), label, fonts["tiny_bold"], (24, 27, 31), anchor="mm")
        x = x0 + chip_w + 8
        for index, space in enumerate(spaces):
            likes = self._compact_number(space.get("likes") or 0)
            likes_w = self._text_width(draw, likes, fonts["metric"]) + 14
            room = x1 - x - likes_w - 4
            if room < 40:
                break
            if index < len(spaces) - 1:
                # Leave the next Space a little room when this one's title is long.
                room = min(room, max(70, (x1 - x) / (len(spaces) - index) - likes_w - 14))
            title = self._fit_text(draw, self._clean_text(space.get("title")), fonts["small"], room)
            self._draw_text(draw, (x, middle), title, fonts["small"], palette["ink"], anchor="lm")
            end = x + self._text_width(draw, title, fonts["small"]) + 4 + likes_w
            self._metric(draw, end, middle, "heart", likes, palette["down"], palette, fonts)
            x = end + 14

    def _draw_repos(self, image, draw, box, ai, palette, fonts, now, icons=None):
        x0, y0, x1, y1 = box
        self._section_title(image, draw, box, "github", "GITHUB", "AI RISING / STARS", palette["gh"], palette, fonts)
        rows = self._board_rows(ai, "github", "repos")
        if rows is None:
            return self._board_offline(draw, box, palette, fonts)
        stars = (ai or {}).get("_star_history") or {}
        row_h = (y1 - y0 - 30) / 3
        for index, repo in enumerate(rows[:REPO_ROWS]):
            top = y0 + 28 + row_h * index
            # A little more apart than the models' lines: the star count sits right above the chip.
            line1, line2 = top + row_h * 0.29, top + row_h * 0.78
            # Its rank in GitHub's green, then its owner's avatar.
            text_x = self._board_mark(image, draw, item_icons.owner_key(repo.get("full_name")), (x0 + 12, top, row_h),
                                      index + 1, palette["gh"], palette, fonts, icons)
            right = self._metric(draw, x1 - 12, line1, "star", self._compact_number(repo.get("stars") or 0),
                                 palette["amber"], palette, fonts)
            delta = repo.get("stars_24h")
            badge = "NEW" if delta is None else f"{self._signed(delta) if delta else '+0'}/24H"
            badge_w = self._text_width(draw, badge, fonts["tiny_bold"]) + 12
            badge_box = (x1 - 12 - badge_w, line2 - 6, x1 - 12, line2 + 6)
            draw.rounded_rectangle(badge_box, radius=6, fill=palette["gh"] if delta else palette["muted"])
            self._draw_text(draw, ((badge_box[0] + badge_box[2]) / 2, line2), badge, fonts["tiny_bold"],
                            palette["panel"], anchor="mm")
            values = self._star_series(stars.get(str(repo.get("full_name"))), now)
            trend_box = (badge_box[0] - 8 - REPO_TREND_W, line2 - 5, badge_box[0] - 8, line2 + 5)
            if not self._sparkline(draw, trend_box, values, palette) and delta is None:
                self._draw_text(draw, (badge_box[0] - 6, line2), "tracking", fonts["tiny"], palette["muted"],
                                anchor="rm")
            width = right - text_x - 8
            self._draw_text(draw, (text_x, line1), self._fit_text(draw, repo.get("full_name") or "", fonts["name"],
                                                                  width), fonts["name"], palette["ink"], anchor="lm")
            language = str(repo.get("language") or "Other")
            draw.ellipse((text_x, line2 - 4, text_x + 8, line2 + 4), fill=LANGUAGE_COLOURS.get(language, palette["muted"]))
            self._draw_text(draw, (text_x + 13, line2), language, fonts["tiny"], palette["muted"], anchor="lm")
            if index < 2:
                self._dotted(draw, x0 + 12, x1 - 12, top + row_h, palette["rule"])

    def _board_rows(self, ai, source, key):
        """The board's rows, or None when only demo data stands in for them."""
        if not ai:
            return None
        states = (ai.get("status") or {}).get("sources") or {}
        rows = ai.get(key) or []
        if states.get(source) == "fixture" or not rows:
            return None
        return rows

    def _board_offline(self, draw, box, palette, fonts):
        x0, y0, x1, y1 = box
        self._draw_text(draw, ((x0 + x1) / 2, (y0 + 28 + y1) / 2), "暂时无法获取", fonts["small"], palette["muted"],
                        anchor="mm")

    def _skill_key(self, skill):
        return f"{skill.get('source') or ''}/{skill.get('name') or ''}".casefold()

    def _rank_move(self, draw, x, y, points, now, palette, fonts):
        """An arrow and the places moved, from ``x``; returns its width (0 when the rank held)."""
        found = trends.change(points, "rank", now)
        moved = -int(found[0]) if found else 0
        if not moved:
            return 0
        colour = palette["up"] if moved > 0 else palette["down"]
        draw_icon(draw, "up" if moved > 0 else "down", (x, y - 4), 7, colour, palette["panel"])
        label = str(abs(moved))
        self._draw_text(draw, (x + 8, y), label, fonts["tiny_bold"], colour, anchor="lm")
        return 8 + self._text_width(draw, label, fonts["tiny_bold"])

    def _delta(self, draw, right, y, found, palette, fonts):
        """A coloured signed change ending at ``right``; returns its left edge (``right`` when there is none)."""
        if not found or not round(found[0]):
            return right
        colour = palette["up"] if found[0] > 0 else palette["down"]
        return self._draw_text(draw, (right, y), self._signed(found[0]), fonts["tiny_bold"], colour, anchor="rm")

    def _sparkline(self, draw, box, values, palette):
        """A trend line coloured by its direction; False when there are fewer than two points."""
        x0, y0, x1, y1 = box
        values = [float(value) for value in values or []]
        if len(values) < 2 or x1 - x0 < 12:
            return False
        low, high = min(values), max(values)
        points = [
            (x0 + (x1 - x0) * index / (len(values) - 1),
             (y0 + y1) / 2 if high == low else y1 - (y1 - y0) * (value - low) / (high - low))
            for index, value in enumerate(values)
        ]
        colour = self._trend_colour(values[-1] - values[0], palette)
        draw.line(points, fill=colour, width=2, joint="curve")
        end_x, end_y = points[-1]
        draw.ellipse((end_x - 2.5, end_y - 2.5, end_x + 2.5, end_y + 2.5), fill=colour)
        return True

    def _star_series(self, history, now):
        start = self._to_utc(now) - timedelta(hours=trends.HOURLY_HOURS)
        points = []
        for point in history if isinstance(history, list) else []:
            if not isinstance(point, dict):
                continue
            captured = trends.parse_time(point.get("captured_at"))
            stars = self._optional_int(point.get("stars"))
            if captured is not None and stars is not None and captured >= start:
                points.append((captured, stars))
        return [stars for _captured, stars in sorted(points)]

    def _trend_colour(self, change, palette):
        if change > 0:
            return palette["up"]
        if change < 0:
            return palette["down"]
        return palette["muted"]

    def _signed(self, value):
        number = int(round(value))
        return f"+{self._compact_number(number)}" if number >= 0 else f"-{self._compact_number(-number)}"

    def _dotted(self, draw, x0, x1, y, colour):
        for x in range(int(x0), int(x1), 4):
            draw.point((x, int(y)), fill=colour)

    def _draw_openrouter(self, image, draw, box, models, palette, fonts, now, icons=None):
        """The strip of OpenRouter's newest models: its logo and how many arrived this week, then a
        card per model with its maker's colour, name, the day it arrived, its context and prices.
        A maker is named by its official mark from the package, else by the icon fetched for it
        (``icons``), else by a chip with its name."""
        x0, y0, x1, y1 = box
        self._panel(draw, box, palette, 1.0)
        middle = (y0 + y1) / 2
        cards_left = x0 + 148
        # The strip's own title sits in a thin frame of its own, apart from the model cards beside it
        # (a line only: a tinted block behind it was turned down).
        draw.rounded_rectangle((x0 + 6, y0 + 6, cards_left - 6, y1 - 6), radius=8, outline=palette["violet"], width=1)
        logo = self._wordmark("openrouter", 15, palette["background"])
        label_x = x0 + 14
        if logo is not None:
            image.paste(logo.convert("RGB"), (int(label_x), int(middle - 11 - logo.height / 2)), logo.getchannel("A"))
        else:
            self._draw_text(draw, (label_x, middle - 11), "OpenRouter", fonts["section"], palette["ink"], anchor="lm")
        rows = (models or {}).get("models") or []
        week = (models or {}).get("week")
        caption = f"新模型上架 · 本周 {week} 个" if week else "新模型上架"
        self._draw_text(draw, (label_x, middle + 10), caption, fonts["tiny_bold"], palette["muted"], anchor="lm")
        if not rows:
            self._draw_text(draw, ((cards_left + x1) / 2, middle), "暂时无法获取", fonts["small"], palette["muted"],
                            anchor="mm")
            return
        width = (x1 - 8 - cards_left - 6 * (OPENROUTER_CARDS - 1)) / OPENROUTER_CARDS
        for index, model in enumerate(rows[:OPENROUTER_CARDS]):
            left = cards_left + index * (width + 6)
            card = (left, y0 + 6, left + width, y1 - 6)
            draw.rounded_rectangle(card, radius=8, fill=palette["row"])
            author = provider_author(model.get("id") or model.get("provider"))
            colour = PROVIDER_COLOURS.get(author, palette["muted"])
            if sum(colour) < 200 and sum(palette["background"]) < 240:
                # A near-black maker colour would vanish on the night page: its chip takes the muted ink.
                colour = palette["muted"]
            draw.rounded_rectangle((card[0], card[1], card[0] + 4, card[3]), radius=2, fill=colour)
            top, bottom = card[1] + 11, card[3] - 10
            mark = self._provider_mark(author, palette)
            if mark is None:
                mark = self._maker_mark(author, model, palette, icons or {})
            if mark is not None:
                # The maker's mark names it; the chip with its name is for a maker with none at hand.
                # A two-line wordmark, taller than the line, stays inside the card.
                mark_top = max(card[1] + PROVIDER_WORDMARK_TOP, top - mark.height / 2)
                image.paste(mark.convert("RGB"), (int(card[0] + 10), int(mark_top)), mark.getchannel("A"))
                chip_w = mark.width
            else:
                provider = PROVIDER_NAMES.get(author, author.split("-")[0].title())
                chip_w = self._text_width(draw, provider, fonts["tiny_bold"]) + 10
                draw.rounded_rectangle((card[0] + 10, top - 7, card[0] + 10 + chip_w, top + 7), radius=7, fill=colour)
                # White on a dark chip; a light one (the night page's muted grey) takes dark lettering.
                lettering = (255, 255, 255) if relative_luminance(colour) < 0.3 else (24, 27, 31)
                self._draw_text(draw, (card[0] + 10 + chip_w / 2, top), provider, fonts["tiny_bold"], lettering,
                                anchor="mm")
            added = self._parse_datetime(model.get("created"))
            when = ""
            if added is not None:
                days = (self._to_utc(now) - added).days
                when = "今天" if days <= 0 else (f"{days} 天前" if days < 7 else f"{added.month}/{added.day}")
            name_x = card[0] + 16 + chip_w
            right = card[2] - 8
            if when:
                right = self._draw_text(draw, (right, top), when, fonts["tiny_bold"],
                                        palette["up"] if when == "今天" else palette["muted"], anchor="rm") - 6
            self._draw_text(draw, (name_x, top), self._fit_text(draw, model.get("name") or "", fonts["name"],
                                                                 right - name_x), fonts["name"], palette["ink"],
                            anchor="lm")
            # Context, then dollars per million tokens in and out, then whether it reads images.
            facts = [part for part in (
                f"{openrouter.context_text(model.get('context'))} 上下文" if model.get("context") else "",
                openrouter.price_text(model) and f"{openrouter.price_text(model)}/M",
                "识图" if model.get("vision") else "") if part]
            self._draw_text(draw, (card[0] + 10, bottom), self._fit_text(draw, " · ".join(facts), fonts["tiny"],
                                                                          card[2] - card[0] - 18),
                            fonts["tiny"], palette["muted"], anchor="lm")

    def _draw_bottom_strip(self, image, draw, box, turn, models, extras, palette, fonts, now, icons=None):
        """The bottom strip on its ``turn``: LMArena's leaderboard when it is its turn and it has
        data, else OpenRouter's new models as ever."""
        if turn == "arena" and extra_boards.arena_models(extras):
            return self._draw_arena(image, draw, box, extras, palette, fonts)
        return self._draw_openrouter(image, draw, box, models, palette, fonts, now, icons=icons)

    def _strip_title(self, draw, box, title, caption, palette, fonts):
        """The strip's frame and its title in the thin-framed box OpenRouter's has; where the cards start."""
        x0, y0, _x1, y1 = box
        self._panel(draw, box, palette, 1.0)
        middle = (y0 + y1) / 2
        cards_left = x0 + 148
        draw.rounded_rectangle((x0 + 6, y0 + 6, cards_left - 6, y1 - 6), radius=8, outline=palette["violet"], width=1)
        self._draw_text(draw, (x0 + 14, middle - 11), title, fonts["section"], palette["ink"], anchor="lm")
        self._draw_text(draw, (x0 + 14, middle + 10), caption, fonts["tiny_bold"], palette["muted"], anchor="lm")
        return cards_left

    def _draw_arena(self, image, draw, box, extras, palette, fonts):
        """LMArena's text leaderboard: a card for each of its first ARENA_CARDS places with the
        rank, the maker's official mark (its name without one), the score, and the model under them."""
        _x0, y0, x1, y1 = box
        day = str(extras["arena"].get("date") or "")
        caption = f"文本总榜 · {day[5:7]}/{day[8:10]}" if len(day) == 10 else "文本总榜"
        cards_left = self._strip_title(draw, box, "LMArena", caption, palette, fonts)
        width = (x1 - 8 - cards_left - 6 * (ARENA_CARDS - 1)) / ARENA_CARDS
        for index, model in enumerate(extra_boards.arena_models(extras)[:ARENA_CARDS]):
            left = cards_left + index * (width + 6)
            card = (left, y0 + 6, left + width, y1 - 6)
            draw.rounded_rectangle(card, radius=8, fill=palette["row"])
            maker = provider_author(model.get("maker"))
            colour = PROVIDER_COLOURS.get(maker, palette["muted"])
            if sum(colour) < 200 and sum(palette["background"]) < 240:
                colour = palette["muted"]
            draw.rounded_rectangle((card[0], card[1], card[0] + 4, card[3]), radius=2, fill=colour)
            top, bottom = card[1] + 11, card[3] - 10
            cursor = card[0] + 10
            cursor += self._text_width(draw, str(model.get("rank")), fonts["name"]) + 6
            self._draw_text(draw, (card[0] + 10, top), str(model.get("rank")), fonts["name"], palette["ink"], anchor="lm")
            score = str(model.get("score"))
            right = card[2] - 8 - self._text_width(draw, score, fonts["name"]) - 6
            self._draw_text(draw, (card[2] - 8, top), score, fonts["name"], palette["ink"], anchor="rm")
            mark = self._provider_mark(maker, palette) if maker else None
            if mark is not None and cursor + mark.width <= right:
                image.paste(mark.convert("RGB"), (int(cursor), int(top - mark.height / 2)), mark.getchannel("A"))
            elif maker:
                name = PROVIDER_NAMES.get(maker, maker.title())
                self._draw_text(draw, (cursor, top), self._fit_text(draw, name, fonts["tiny_bold"], right - cursor),
                                fonts["tiny_bold"], palette["muted"], anchor="lm")
            self._draw_text(draw, (card[0] + 10, bottom), self._fit_text(draw, model.get("name") or "", fonts["small"],
                                                                          card[2] - card[0] - 18),
                            fonts["small"], palette["ink"], anchor="lm")

    def _provider_mark(self, provider, palette):
        """The official mark of a model's maker (PROVIDER_LOGOS) PROVIDER_MARK_HEIGHT tall and at most
        PROVIDER_MARK_WIDTH wide (a two-line wordmark, PROVIDER_WORDMARKS, its own larger box), None
        without one. On a dark page its near-black parts take the page's ink (xAI's X, Amazon's a,
        its smile still orange); a mark on its own ground stays as printed."""
        key = provider_logo_key(provider)
        if key is None:
            return None
        path = ASSET_DIR / "logos" / f"{key}.png"
        ink = palette["ink"] if sum(palette["background"]) < 240 and not _provider_badge(str(path)) else None
        height, width = ((PROVIDER_WORDMARK_HEIGHT, PROVIDER_WORDMARK_WIDTH) if key in PROVIDER_WORDMARKS
                         else (PROVIDER_MARK_HEIGHT, PROVIDER_MARK_WIDTH))
        mark = load_source_logo(path, height, ink)
        if mark is not None and mark.width > width:
            mark = load_source_logo(path, max(1, height * width // mark.width), ink)
        return mark

    def _maker_mark(self, author, model, palette, icons):
        """For a maker without a mark in the package, the icon fetched for it (item_icons.py): its
        own from OpenRouter's page before the avatar of the Hub organisation OpenRouter names for
        the model. It stands in a bundled mark's slot, set for the card's ground without a plate
        (a dark mark turned light on the night page); None with neither at hand."""
        icon = icons.get(item_icons.maker_key(author))
        if icon is None:
            icon = icons.get(item_icons.hub_key(model.get("hub")))
        if icon is None:
            return None
        return item_icons.mark_within(icon, PROVIDER_MARK_WIDTH, PROVIDER_MARK_HEIGHT, palette["row"])

    def _wordmark(self, key, height, background):
        """A source's wide logo (assets/logos) ``height`` tall; its dark lettering turns light on a dark page."""
        try:
            with Image.open(ASSET_DIR / "logos" / f"{key}.png") as source:
                logo = source.convert("RGBA")
        except (OSError, ValueError):
            return None
        logo = logo.resize((max(1, round(logo.width * height / logo.height)), height), Image.LANCZOS)
        if sum(background) < 240 and self._mark_luminance(logo) < 60:
            red, green, blue, alpha = logo.split()
            logo = Image.merge("RGBA", (*ImageOps.invert(Image.merge("RGB", (red, green, blue))).split(), alpha))
        return logo

    def _paste_logo(self, image, key, xy, size, palette):
        logo = self._logo(key, size, palette["background"])
        if logo is not None:
            image.paste(logo.convert("RGB"), (int(xy[0]), int(xy[1])), logo.getchannel("A"))

    def _logo(self, key, size, background):
        """A source's official logo; a dark mark turns to its light form on a dark page."""
        path = ASSET_DIR / f"logo-{key}.png"
        if not path.is_file():
            # The logos built by tools/build_source_logos.py live in assets/logos.
            path = ASSET_DIR / "logos" / f"{key}.png"
        try:
            with Image.open(path) as source:
                logo = ImageOps.contain(source.convert("RGBA"), (size, size), method=Image.LANCZOS)
        except (OSError, ValueError):
            return None
        layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        layer.alpha_composite(logo, ((size - logo.width) // 2, (size - logo.height) // 2))
        if sum(background) < 240 and self._mark_luminance(layer) < 60:
            red, green, blue, alpha = layer.split()
            inverted = ImageOps.invert(Image.merge("RGB", (red, green, blue)))
            layer = Image.merge("RGBA", (*inverted.split(), alpha))
        return layer

    @staticmethod
    def _mark_luminance(layer):
        mask = layer.getchannel("A").point(lambda alpha: 255 if alpha > 128 else 0)
        if mask.getbbox() is None:
            return 255
        return ImageStat.Stat(layer.convert("L"), mask=mask).mean[0]

    def _draw_hn_story_preview(self, image, draw, box, story, palette, scale, label_font, body_font):
        x0, y0, x1, y1 = [int(value) for value in box]
        width = max(1, x1 - x0)
        height = max(1, y1 - y0)
        card = self._story_preview_card((width, height), story, palette, scale, label_font, body_font)
        radius = max(6, int(8 * scale))
        mask = Image.new("L", (width, height), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, width - 1, height - 1), radius=radius, fill=255)
        image.paste(card, (x0, y0), mask)
        draw.rounded_rectangle((x0, y0, x1 - 1, y1 - 1), radius=radius, outline=palette["rule"], width=max(1, int(scale)))

    def _story_preview_card(self, size, story, palette, scale, label_font, body_font):
        width, height = size
        card = Image.new("RGB", (width, height), palette["row"])
        draw = ImageDraw.Draw(card)
        story = story if isinstance(story, dict) else {}
        comment = story.get("top_comment")
        if isinstance(comment, dict) and self._clean_text(comment.get("text")):
            heading = "TOP COMMENT"
            detail = f"{comment.get('by') or 'unknown'} | {self._age_text(comment.get('age_hours'))}"
            body = self._clean_text(comment.get("text"))
            body_fill = palette["ink"]
        else:
            heading = "HN DISCUSSION"
            detail = f"{self._compact_number(story.get('comments') or 0)} comments"
            body = self._story_preview_url(story) or "news.ycombinator.com"
            body_fill = palette["muted"]

        pad = int(9 * scale)
        bar_h = int(22 * scale)
        bar_text_y = int(4 * scale)
        draw.rectangle((0, 0, width, bar_h), fill=palette["orange"])
        self._draw_text(draw, (pad, bar_text_y), heading, label_font, palette["background"])
        detail_w = width - pad * 3 - self._text_width(draw, heading, label_font)
        if detail_w > 0:
            detail = self._fit_text(draw, detail, body_font, detail_w)
            detail_x = width - pad - self._text_width(draw, detail, body_font)
            self._draw_text(draw, (detail_x, bar_text_y), detail, body_font, palette["background"])

        line_h = int(16 * scale)
        body_top = bar_h + int(5 * scale)
        max_lines = max(1, (height - body_top - int(3 * scale)) // line_h)
        lines = self._wrap_text(draw, body, body_font, width - pad * 2, max_lines=max_lines, ellipsis=True)
        for index, line in enumerate(lines):
            self._draw_text(draw, (pad, body_top + index * line_h), line, body_font, body_fill)
        return card

    def _story_preview_url(self, story):
        if isinstance(story, str):
            raw_url = story.strip()
        elif isinstance(story, dict):
            raw_url = str(story.get("url") or story.get("hn_url") or "").strip()
        else:
            raw_url = ""
        if not raw_url:
            return ""
        parsed = urlparse(raw_url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return ""
        return raw_url

    def _panel(self, draw, box, palette, scale):
        draw.rounded_rectangle(box, radius=int(14 * scale), fill=palette["panel"], outline=palette["rule"], width=max(1, int(scale)))

    def _pill(self, draw, box, fill, outline, radius):
        draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=1)

    def _draw_title_wordmark(self, image, x, y, size, ink=None):
        """Paste the wordmark; with ``ink`` its pale lettering takes that colour (for a light page)."""
        source = self._load_title_wordmark()
        if source is None:
            return False
        try:
            target_w, target_h = [max(1, int(value)) for value in size]
            if ink is not None:
                source = self._ink_pale_lettering(source, ink)
            fitted = ImageOps.contain(source, (target_w, target_h), method=Image.LANCZOS)
            layer = Image.new("RGBA", (target_w, target_h), (0, 0, 0, 0))
            layer.alpha_composite(fitted, ((target_w - fitted.width) // 2, (target_h - fitted.height) // 2))
            image.paste(layer.convert("RGB"), (int(x), int(y)), layer.getchannel("A"))
            return True
        except Exception as exc:
            logger.debug("Could not draw Tech Pulse title wordmark: %s", exc)
            return False

    @staticmethod
    def _ink_pale_lettering(source, ink):
        """Recolour the near-white, unsaturated pixels; the orange prompt and cyan brackets keep theirs."""
        rgb = source.convert("RGB")
        low = ImageChops.darker(ImageChops.darker(*rgb.split()[:2]), rgb.split()[2])
        high = ImageChops.lighter(ImageChops.lighter(*rgb.split()[:2]), rgb.split()[2])
        pale = low.point(lambda value: 255 if value >= 170 else 0)
        neutral = ImageChops.subtract(high, low).point(lambda value: 255 if value <= 40 else 0)
        mask = ImageChops.multiply(pale, neutral)
        inked = source.copy()
        inked.paste(Image.new("RGBA", source.size, (*ink, 255)), (0, 0), mask)
        inked.putalpha(source.getchannel("A"))
        return inked

    def _load_title_wordmark(self):
        path = self._title_wordmark_path()
        if not path.is_file():
            return None
        try:
            return Image.open(path).convert("RGBA")
        except Exception as exc:
            logger.debug("Could not load Tech Pulse title wordmark %s: %s", path, exc)
            return None

    def _title_wordmark_path(self):
        return Path(self.get_plugin_dir(TITLE_WORDMARK_IMAGE))

    def _draw_background_grid(self, draw, width, height, palette, scale):
        return None

    def _palette(self, settings):
        theme = settings.get("_inkypi_theme") or self.resolve_theme(settings, None)
        # Gains, losses and each source keep their own colours rather than the panel's six inks.
        if theme.get("mode") != "night":
            return {
                "background": (245, 240, 226),
                "panel": (255, 251, 241),
                "row": (250, 245, 233),
                "metric": (244, 237, 220),
                "chip": (239, 230, 211),
                "grid": (234, 225, 207),
                "rule": (202, 188, 160),
                "ink": (24, 27, 31),
                "muted": (91, 96, 104),
                "dim": (128, 128, 128),
                "orange": (255, 102, 0),
                "amber": (204, 138, 0),
                "cyan": (0, 120, 150),
                "up": (22, 150, 96),
                "down": (214, 62, 72),
                "violet": (108, 74, 226),
                "violet_tint": (236, 230, 255),
                "hf": (255, 210, 30),
                "hf_text": (196, 120, 0),
                "gh": (31, 136, 61),
            }
        roles = {
            name: tuple(value)
            for name, value in theme["palette"].items()
        }
        return {
            **roles,
            "row": roles["panel"],
            "metric": roles["panel"],
            "chip": roles["panel"],
            "grid": roles["rule"],
            "dim": roles["muted"],
            "orange": (255, 122, 40),
            "amber": (255, 190, 70),
            "cyan": (92, 200, 232),
            "up": (78, 212, 138),
            "down": (255, 107, 112),
            "violet": (172, 150, 255),
            "violet_tint": (38, 34, 70),
            "hf": (255, 210, 30),
            "hf_text": (255, 210, 30),
            "gh": (72, 196, 106),
        }

    def _write_context(self, payload, now, ai=None):
        boards = {key: (ai or {}).get(key) or [] for key in ("skills", "models", "repos")}
        try:
            write_context(
                PLUGIN_ID,
                {
                    "feed": payload.get("feed"),
                    "stories": payload.get("stories") or [],
                    "stats": payload.get("stats") or {},
                    "status": payload.get("status") or {},
                    "source_provenance": payload.get("_source_provenance"),
                    "ai": {**boards, "status": (ai or {}).get("status") or {}},
                },
                generated_at=now,
                ttl_seconds=2 * 60 * 60,
            )
        except Exception as exc:
            logger.debug("Could not write Tech Pulse context: %s", exc)

    def _cache_dir(self, create=True):
        return self.cache_dir(
            env_var="TECH_PULSE_CACHE_DIR",
            leaf="cache",
            create=create,
            strip=True,
        )

    def _data_dir(self, create=True):
        return self.data_dir(
            env_var="TECH_PULSE_DATA_DIR",
            leaf="state",
            create=create,
            strip=True,
        )


    def _cache_key(self, feed, max_stories, min_score):
        return f"{feed}|{max_stories}|{min_score if min_score is not None else ''}"

    def _valid_cache(self, cache, cache_key):
        return (
            isinstance(cache, dict)
            and cache.get("schema") == CACHE_SCHEMA_VERSION
            and cache.get("cache_key") == cache_key
            and isinstance(cache.get("stories"), list)
            and bool(cache.get("stories"))
        )

    @staticmethod
    def _valid_stale_cache(cache):
        return (
            isinstance(cache, dict)
            and cache.get("schema") == CACHE_SCHEMA_VERSION
            and isinstance(cache.get("stories"), list)
            and bool(cache.get("stories"))
        )

    def _is_fresh_cache(self, cache, cache_key, now, refresh_minutes):
        if not self._valid_cache(cache, cache_key):
            return False
        generated = self._parse_datetime((cache.get("status") or {}).get("generated_at"))
        if not generated:
            return False
        age_seconds = (self._to_utc(now) - generated).total_seconds()
        return 0 <= age_seconds <= refresh_minutes * 60

    def _read_json(self, path, default):
        try:
            return json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception:
            return default

    def _write_json(self, path, payload):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)

    def _feed(self, settings):
        value = str(settings.get("feed") or "topstories").strip().lower()
        return value if value in FEED_ENDPOINTS else "topstories"

    def _now_for_device(self, device_config):
        tz_name = DEFAULT_TIMEZONE
        try:
            tz_name = device_config.get_config("timezone") or DEFAULT_TIMEZONE
        except Exception:
            pass
        for candidate in (tz_name, DEFAULT_TIMEZONE, "UTC"):
            try:
                return datetime.now(ZoneInfo(candidate))
            except Exception:
                continue
        return datetime.now(timezone.utc)

    def _published_at(self, raw_time):
        if raw_time is None:
            return None
        try:
            return datetime.fromtimestamp(int(raw_time), timezone.utc)
        except Exception:
            return None

    def _age_hours(self, published, now):
        if not published:
            return 0
        return max(0, int((self._to_utc(now) - published).total_seconds() // 3600))

    def _parse_datetime(self, value):
        if isinstance(value, datetime):
            return self._to_utc(value)
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
        except Exception:
            return None

    def _to_utc(self, value):
        if isinstance(value, datetime):
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            return value.astimezone(timezone.utc)
        return datetime.now(timezone.utc)

    def _optional_int(self, value):
        if value in (None, ""):
            return None
        try:
            if isinstance(value, float) and math.isnan(value):
                return None
            return int(value)
        except (TypeError, ValueError):
            return None

    def _domain(self, url):
        candidate = str(url or "").strip()
        if not candidate:
            return "news.ycombinator.com"
        try:
            parsed = urlparse(candidate)
            netloc = parsed.netloc or parsed.path.split("/")[0]
        except Exception:
            netloc = ""
        netloc = netloc.lower()
        if netloc.startswith("www."):
            netloc = netloc[4:]
        return netloc or "news.ycombinator.com"

    def _clean_text(self, value):
        text = unescape(str(value or "")).replace("\u2019", "'").replace("\u2018", "'")
        text = text.replace("\u201c", '"').replace("\u201d", '"')
        text = text.replace("\u2014", "-").replace("\u2013", "-")
        return re.sub(r"\s+", " ", text).strip()

    def _byline(self, story):
        by = story.get("by") or "unknown"
        return f"by {by} | {self._age_text(story.get('age_hours'))}"

    def _age_text(self, age_hours):
        age = self._optional_int(age_hours) or 0
        return f"{age}h ago" if age < 48 else f"{age // 24}d ago"

    def _compact_number(self, value):
        try:
            number = int(value or 0)
        except Exception:
            number = 0
        if number >= 1_000_000:
            return f"{number / 1_000_000:.1f}M".replace(".0M", "M")
        if number >= 1000:
            whole = number / 1000
            return f"{whole:.1f}k".replace(".0k", "k")
        return str(number)

    def _load_font(self, font_family, size, weight="normal"):
        try:
            font = get_font(font_family or DEFAULT_FONT, size, weight)
            if font:
                return font
        except Exception as exc:
            logger.debug("Could not load font %s: %s", font_family, exc)
        try:
            font = get_font(DEFAULT_FONT, size, weight)
            if font:
                return font
        except Exception:
            pass
        return ImageFont.load_default()

    def _wrap_text(self, draw, text, font, max_width, max_lines=2, ellipsis=False):
        words = str(text or "").split()
        lines = []
        current = ""
        truncated = False
        for word in words:
            candidate = f"{current} {word}".strip()
            if current and self._text_width(draw, candidate, font) > max_width:
                lines.append(current)
                current = word
                if len(lines) >= max_lines:
                    truncated = True
                    break
            else:
                current = candidate
        if len(lines) < max_lines and current:
            lines.append(current)
        if not lines:
            lines = [""]
        if len(lines) > max_lines:
            lines = lines[:max_lines]
        lines = [self._fit_text(draw, line, font, max_width) for line in lines]
        if ellipsis and truncated and not lines[-1].endswith("..."):
            last = lines[-1]
            while last and self._text_width(draw, last.rstrip() + "...", font) > max_width:
                last = last[:-1]
            lines[-1] = last.rstrip() + "..."
        return lines

    def _fit_text(self, draw, text, font, max_width):
        text = str(text or "")
        if self._text_width(draw, text, font) <= max_width:
            return text
        suffix = "..."
        while text and self._text_width(draw, text + suffix, font) > max_width:
            text = text[:-1]
        return text + suffix if text else suffix

    def _text_width(self, draw, text, font):
        return text_width(draw, str(text), font)

    def _draw_text(self, draw, xy, text, font, fill, anchor=None):
        """Draw ``text`` and return its left edge (handy with a right anchor)."""
        if anchor is None:
            draw.text(xy, str(text), font=font, fill=fill)
            return xy[0]
        draw.text(xy, str(text), font=font, fill=fill, anchor=anchor)
        return draw.textbbox(xy, str(text), font=font, anchor=anchor)[0]

    def _enabled(self, value, default=False):
        return coerce_bool(value, default=default, truthy=("1", "true", "yes", "on"))
