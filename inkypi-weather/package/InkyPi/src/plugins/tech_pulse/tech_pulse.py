from __future__ import annotations

import json
import logging
import math
import os
import re
from datetime import datetime, timedelta, timezone
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
from plugins.context_cache import write_context
from plugins.tech_pulse import openrouter, trends
from runtime.refresh_contracts import TaskCancelled
from utils.app_utils import DEFAULT_FONT_FAMILY, bounded_int, coerce_bool, get_available_font_names, get_font
from utils.flat_icons import draw_icon
from utils.http_client import get_http_session
from utils.image_utils import text_width
from utils.doodle_backdrop import paint_doodles

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
SKILLS_BOX = (414, 62, 786, 186)
MODELS_BOX = (414, 192, 786, 302)
REPOS_BOX = (414, 308, 786, 414)
# OpenRouter's newest models run along the foot of the page, under both columns.
OPENROUTER_BOX = (14, 420, 786, 474)
OPENROUTER_FILE = "openrouter.json"
OPENROUTER_FRESH_HOURS = 6
OPENROUTER_CARDS = 3
# Each model's maker in its own colour (its brand's), on the model's chip.
PROVIDER_COLOURS = {
    "openai": (16, 163, 127), "anthropic": (204, 120, 92), "google": (66, 133, 244), "meta-llama": (0, 100, 224),
    "mistralai": (255, 112, 0), "deepseek": (77, 107, 254), "qwen": (98, 82, 255), "moonshotai": (22, 22, 22),
    "x-ai": (40, 40, 40), "z-ai": (40, 90, 220), "minimax": (230, 60, 90), "amazon": (255, 153, 0),
    "nvidia": (118, 185, 0), "cohere": (57, 89, 77), "microsoft": (0, 120, 212),
}
# A maker's short name on its chip, where its id is long ("moonshotai" -> "Moonshot").
PROVIDER_NAMES = {
    "openai": "OpenAI", "anthropic": "Anthropic", "google": "Google", "meta-llama": "Meta", "mistralai": "Mistral",
    "deepseek": "DeepSeek", "qwen": "Qwen", "moonshotai": "Moonshot", "x-ai": "xAI", "z-ai": "Z.ai",
    "minimax": "MiniMax", "amazon": "Amazon", "nvidia": "NVIDIA", "cohere": "Cohere", "microsoft": "Microsoft",
}
PROVIDER_MARK_HEIGHT, PROVIDER_MARK_WIDTH = 16, 46
HN_LIST_ROWS = 3
SKILL_ROWS = 5
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
        image = self._render_page(dimensions, payload, settings, now, ai, history, models)
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

        stories = []
        lead_kids = None
        inspect_count = min(len(story_ids), max(20, max_stories * 6))
        for raw_id in story_ids[:inspect_count]:
            try:
                item_response = session.get(
                    f"{HN_BASE_URL}/item/{int(raw_id)}.json",
                    timeout=REQUEST_TIMEOUT,
                    headers=headers,
                )
                item_response.raise_for_status()
                item = item_response.json()
                story = self._parse_story_item(item, now=now)
            except TaskCancelled:
                raise  # a cancelled refresh must not cache the stories fetched so far
            except Exception as exc:
                logger.debug("Skipping HN item %s: %s", raw_id, exc)
                continue
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

        if not stories:
            raise RuntimeError("No displayable Hacker News stories were returned")

        top_comment = self._fetch_top_comment(session, headers, lead_kids, now)
        if top_comment:
            stories[0]["top_comment"] = top_comment

        return self._build_payload(feed, stories, "live", now, [feed_url, HN_DOCS_URL])

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

    def _render_page(self, dimensions, payload, settings, now, ai=None, history=None, models=None):
        palette = self._palette(settings)
        image = Image.new("RGB", PAGE_SIZE, palette["background"])
        draw = ImageDraw.Draw(image)
        family = settings.get("fontFamily") or settings.get("font_family") or DEFAULT_FONT
        fonts = {name: self._load_font(family, size, weight) for name, (size, weight) in FONT_SPECS.items()}
        history = history or {}

        self._draw_background_grid(draw, *PAGE_SIZE, palette, 1.0)
        self._draw_header(image, draw, payload, ai, now, palette, fonts)
        self._draw_hn_panel(image, draw, HN_BOX, payload, settings, palette, fonts, history, now)
        self._draw_skills(image, draw, SKILLS_BOX, ai, palette, fonts, history, now)
        self._draw_models(image, draw, MODELS_BOX, ai, palette, fonts, history, now)
        self._draw_repos(image, draw, REPOS_BOX, ai, palette, fonts, now)
        self._draw_openrouter(image, draw, OPENROUTER_BOX, models, palette, fonts, now)
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

    def _draw_hn_panel(self, image, draw, box, payload, settings, palette, fonts, history, now):
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
                                      fonts, board, now)
        draw.line((x0 + 12, lead_bottom + 6, x1 - 12, lead_bottom + 6), fill=palette["rule"], width=1)
        self._draw_story_list(draw, (x0 + 12, lead_bottom + 8, x1 - 12, y1 - 4), stories[1:1 + HN_LIST_ROWS],
                              settings, palette, fonts, board, now)

    def _draw_lead(self, image, draw, box, story, settings, palette, fonts, history, now):
        """The top story: title, meta, points trend and top comment; returns its bottom edge."""
        x0, y0, x1, _y1 = box
        badge = (x0, y0, x0 + 22, y0 + 22)
        draw.rounded_rectangle(badge, radius=5, fill=palette["orange"])
        self._draw_text(draw, (x0 + 11, y0 + 11), str(story.get("rank") or 1), fonts["label"], (255, 255, 255),
                        anchor="mm")
        lines = self._wrap_text(draw, story.get("title") or "No story available", fonts["lead"], x1 - x0 - 30,
                                max_lines=2, ellipsis=True)
        for index, line in enumerate(lines):
            self._draw_text(draw, (x0 + 30, y0 + 11 + index * 23), line, fonts["lead"], palette["ink"], anchor="lm")

        meta_y = y0 + 11 + len(lines) * 23 + 2
        right = self._draw_story_metrics(draw, x1, meta_y, story, palette, fonts)
        meta = self._story_meta(story, settings, byline=True)
        if meta:
            self._draw_text(draw, (x0, meta_y), self._fit_text(draw, meta, fonts["small"], right - x0 - 10),
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

    def _draw_story_list(self, draw, box, stories, settings, palette, fonts, history, now):
        x0, y0, x1, y1 = box
        row_h = (y1 - y0) / HN_LIST_ROWS
        for index, story in enumerate(stories):
            top = y0 + index * row_h
            self._story_row(draw, (x0, top, x1, top + row_h), story, settings, palette, fonts, history, now)
            if index < len(stories) - 1:
                self._dotted(draw, x0, x1, top + row_h, palette["rule"])

    def _story_row(self, draw, box, story, settings, palette, fonts, history, now):
        x0, y0, x1, y1 = box
        middle = (y0 + y1) / 2
        title_y, meta_y = middle - 9, middle + 10
        badge = (x0, title_y - 10, x0 + 20, title_y + 10)
        draw.rounded_rectangle(badge, radius=5, fill=palette["orange"])
        self._draw_text(draw, (x0 + 10, title_y), str(story.get("rank") or ""), fonts["label"], (255, 255, 255),
                        anchor="mm")
        title = self._fit_text(draw, story.get("title") or "", fonts["row"], x1 - x0 - 28)
        self._draw_text(draw, (x0 + 28, title_y), title, fonts["row"], palette["ink"], anchor="lm")

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
            self._draw_text(draw, (x0 + 28, meta_y), self._fit_text(draw, meta, fonts["small"], right - x0 - 36),
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

    def _draw_skills(self, image, draw, box, ai, palette, fonts, history, now):
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
            # The name keeps its full width while the source can still show ~40 px after it.
            room = right - (x0 + 56) - 8
            name = self._fit_text(draw, skill.get("name") or "", fonts["name"], room - 46)
            self._draw_text(draw, (x0 + 56, middle), name, fonts["name"], palette["ink"], anchor="lm")
            source_x = x0 + 62 + self._text_width(draw, name, fonts["name"])
            if right - source_x - 8 >= 24:
                source = self._fit_text(draw, skill.get("source") or "", fonts["tiny"], right - source_x - 8)
                self._draw_text(draw, (source_x, middle + 1), source, fonts["tiny"], palette["muted"], anchor="lm")

    def _draw_models(self, image, draw, box, ai, palette, fonts, history, now):
        x0, y0, x1, y1 = box
        self._section_title(image, draw, box, "huggingface", "HUGGING FACE", "TRENDING MODELS", palette["hf_text"],
                            palette, fonts)
        rows = self._board_rows(ai, "huggingface", "models")
        if rows is None:
            return self._board_offline(draw, box, palette, fonts)
        board = history.get("models") or {}
        row_h = (y1 - y0 - 30) / 3
        columns = (("trend", "fire", "trending_score", palette["orange"]),
                   ("likes", "heart", "likes", palette["down"]),
                   ("downloads", "download", "downloads_30d", palette["cyan"]))
        for index, model in enumerate(rows[:3]):
            top = y0 + 28 + row_h * index
            line1, line2 = top + row_h * 0.33, top + row_h * 0.74
            points = board.get(str(model.get("id") or ""))
            badge = (x0 + 12, line1 - 8, x0 + 30, line1 + 8)
            draw.rounded_rectangle(badge, radius=4, fill=palette["hf"])
            self._draw_text(draw, (x0 + 21, line1), str(index + 1), fonts["tiny_bold"], (24, 27, 31), anchor="mm")
            moved_w = self._rank_move(draw, x0 + 38, line2, points, now, palette, fonts)
            right = x1 - 12
            for metric, icon, field, colour in reversed(columns):
                self._metric(draw, right, line1, icon, self._compact_number(model.get(field) or 0), colour, palette,
                             fonts)
                self._delta(draw, right, line2, trends.change(points, metric, now), palette, fonts)
                right -= 60
            width = right - (x0 + 38)
            self._draw_text(draw, (x0 + 38, line1), self._fit_text(draw, model.get("id") or "", fonts["name"], width),
                            fonts["name"], palette["ink"], anchor="lm")
            tag = str(model.get("pipeline_tag") or "model")
            tag_x = x0 + 38 + (moved_w + 6 if moved_w else 0)
            self._draw_text(draw, (tag_x, line2), self._fit_text(draw, tag, fonts["tiny"], width - (tag_x - x0 - 38)),
                            fonts["tiny"], palette["muted"], anchor="lm")
            if index < 2:
                self._dotted(draw, x0 + 12, x1 - 12, top + row_h, palette["rule"])

    def _draw_repos(self, image, draw, box, ai, palette, fonts, now):
        x0, y0, x1, y1 = box
        self._section_title(image, draw, box, "github", "GITHUB", "AI RISING / STARS", palette["gh"], palette, fonts)
        rows = self._board_rows(ai, "github", "repos")
        if rows is None:
            return self._board_offline(draw, box, palette, fonts)
        stars = (ai or {}).get("_star_history") or {}
        row_h = (y1 - y0 - 30) / 3
        for index, repo in enumerate(rows[:3]):
            top = y0 + 28 + row_h * index
            # A little more apart than the models' lines: the star count sits right above the chip.
            line1, line2 = top + row_h * 0.29, top + row_h * 0.78
            self._draw_text(draw, (x0 + 21, line1), str(index + 1), fonts["name"], palette["gh"], anchor="mm")
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
            width = right - (x0 + 38) - 8
            self._draw_text(draw, (x0 + 38, line1), self._fit_text(draw, repo.get("full_name") or "", fonts["name"],
                                                                    width), fonts["name"], palette["ink"], anchor="lm")
            language = str(repo.get("language") or "Other")
            draw.ellipse((x0 + 38, line2 - 4, x0 + 46, line2 + 4), fill=LANGUAGE_COLOURS.get(language, palette["muted"]))
            self._draw_text(draw, (x0 + 51, line2), language, fonts["tiny"], palette["muted"], anchor="lm")
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

    def _draw_openrouter(self, image, draw, box, models, palette, fonts, now):
        """The strip of OpenRouter's newest models: its logo and how many arrived this week, then a
        card per model with its maker's colour, name, the day it arrived, its context and prices."""
        x0, y0, x1, y1 = box
        self._panel(draw, box, palette, 1.0)
        middle = (y0 + y1) / 2
        logo = self._wordmark("openrouter", 15, palette["background"])
        label_x = x0 + 12
        if logo is not None:
            image.paste(logo.convert("RGB"), (int(label_x), int(middle - 11 - logo.height / 2)), logo.getchannel("A"))
        else:
            self._draw_text(draw, (label_x, middle - 11), "OpenRouter", fonts["section"], palette["ink"], anchor="lm")
        rows = (models or {}).get("models") or []
        week = (models or {}).get("week")
        caption = f"新模型上架 · 本周 {week} 个" if week else "新模型上架"
        self._draw_text(draw, (label_x, middle + 10), caption, fonts["tiny_bold"], palette["muted"], anchor="lm")
        cards_left = x0 + 148
        if not rows:
            self._draw_text(draw, ((cards_left + x1) / 2, middle), "暂时无法获取", fonts["small"], palette["muted"],
                            anchor="mm")
            return
        width = (x1 - 8 - cards_left - 6 * (OPENROUTER_CARDS - 1)) / OPENROUTER_CARDS
        for index, model in enumerate(rows[:OPENROUTER_CARDS]):
            left = cards_left + index * (width + 6)
            card = (left, y0 + 6, left + width, y1 - 6)
            draw.rounded_rectangle(card, radius=8, fill=palette["row"])
            colour = PROVIDER_COLOURS.get(model.get("provider"), palette["muted"])
            if sum(colour) < 200 and sum(palette["background"]) < 240:
                # A near-black maker colour would vanish on the night page: its chip takes the muted ink.
                colour = palette["muted"]
            draw.rounded_rectangle((card[0], card[1], card[0] + 4, card[3]), radius=2, fill=colour)
            top, bottom = card[1] + 11, card[3] - 10
            mark = self._provider_mark(model.get("provider"), palette["background"])
            if mark is not None:
                # The maker's own mark names it; the chip is for makers without one.
                image.paste(mark.convert("RGB"), (int(card[0] + 10), int(top - mark.height / 2)), mark.getchannel("A"))
                chip_w = mark.width
            else:
                provider = PROVIDER_NAMES.get(model.get("provider"),
                                              str(model.get("provider") or "").split("-")[0].title())
                chip_w = self._text_width(draw, provider, fonts["tiny_bold"]) + 10
                draw.rounded_rectangle((card[0] + 10, top - 7, card[0] + 10 + chip_w, top + 7), radius=7, fill=colour)
                self._draw_text(draw, (card[0] + 10 + chip_w / 2, top), provider, fonts["tiny_bold"], (255, 255, 255),
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

    def _provider_mark(self, provider, background):
        """A model maker's mark (assets/logos/provider-<id>.png) PROVIDER_MARK_HEIGHT tall and at most
        PROVIDER_MARK_WIDTH wide; a dark mark turns light on a dark page. None without one."""
        try:
            with Image.open(ASSET_DIR / "logos" / f"provider-{provider}.png") as source:
                mark = ImageOps.contain(source.convert("RGBA"), (PROVIDER_MARK_WIDTH, PROVIDER_MARK_HEIGHT),
                                        method=Image.LANCZOS)
        except (OSError, ValueError):
            return None
        if sum(background) < 240 and self._mark_luminance(mark) < 60:
            red, green, blue, alpha = mark.split()
            mark = Image.merge("RGBA", (*ImageOps.invert(Image.merge("RGB", (red, green, blue))).split(), alpha))
        return mark

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
