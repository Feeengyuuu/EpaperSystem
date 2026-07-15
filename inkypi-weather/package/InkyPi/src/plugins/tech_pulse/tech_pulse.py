from __future__ import annotations

import hashlib
from io import BytesIO
import json
import logging
import math
import os
import re
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont, ImageOps

from plugins.base_plugin.base_plugin import BasePlugin
from plugins.base_plugin.presentation import PresentationMode
from plugins.base_plugin.render_provenance import (
    SourceProvenance,
    attach_source_provenance,
)
from plugins.context_cache import write_context
from security.ssrf import validate_browser_target
from utils.app_utils import DEFAULT_FONT_FAMILY, bounded_int, coerce_bool, get_available_font_names, get_font
from utils.cache_manager import CacheBudget
from utils.http_client import get_http_session
from utils.image_utils import take_screenshot, text_width
from utils.safe_image import safe_open_image

logger = logging.getLogger(__name__)

PLUGIN_ID = "tech_pulse"
CACHE_SCHEMA_VERSION = "tech-pulse-v1"
DEFAULT_TIMEZONE = "America/Los_Angeles"
DEFAULT_FONT = DEFAULT_FONT_FAMILY
HN_BASE_URL = "https://hacker-news.firebaseio.com/v0"
HN_DOCS_URL = "https://github.com/HackerNews/API"
HN_HOME_URL = "https://news.ycombinator.com/"
HN_ITEM_URL = "https://news.ycombinator.com/item?id={id}"
TITLE_WORDMARK_IMAGE = "title_wordmark.png"
TITLE_WORDMARK_DISPLAY_SIZE = (246, 46)
STORY_PREVIEW_CACHE_VERSION = "v1"
STORY_PREVIEW_CAPTURE_SIZE = (1100, 720)
STORY_PREVIEW_TIMEOUT_MS = 15000
STORY_PREVIEW_CROP_TOP = 0
STORY_PREVIEW_CROP_HEIGHT = 520
STORY_PREVIEW_CACHE_BUDGET = CacheBudget(
    30 * 24 * 60 * 60,
    256,
    50 * 1024 * 1024,
)
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
        if not settings.get("_theme_render_only"):
            self._write_context(payload, now)
        image = self._render_page(dimensions, payload, settings, now)
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
        inspect_count = min(len(story_ids), max(20, max_stories * 6))
        for raw_id in story_ids[:inspect_count]:
            try:
                item_response = session.get(
                    f"{HN_BASE_URL}/item/{int(raw_id)}.json",
                    timeout=REQUEST_TIMEOUT,
                    headers=headers,
                )
                item_response.raise_for_status()
                story = self._parse_story_item(item_response.json(), now=now)
            except Exception as exc:
                logger.debug("Skipping HN item %s: %s", raw_id, exc)
                continue
            if not story:
                continue
            if min_score is not None and story["score"] < min_score:
                continue
            story["rank"] = len(stories) + 1
            stories.append(story)
            if len(stories) >= max_stories:
                break

        if not stories:
            raise RuntimeError("No displayable Hacker News stories were returned")

        return self._build_payload(feed, stories, "live", now, [feed_url, HN_DOCS_URL])

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
        now_utc = self._to_utc(now)
        age_hours = 0
        if published:
            age_hours = max(0, int((now_utc - published).total_seconds() // 3600))

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

    def _render_page(self, dimensions, payload, settings, now):
        width, height = dimensions
        scale = max(0.72, min(width / 800, height / 480))
        palette = self._palette(settings)
        image = Image.new("RGB", dimensions, palette["background"])
        draw = ImageDraw.Draw(image)

        font_family = settings.get("fontFamily") or settings.get("font_family") or DEFAULT_FONT
        title_font = self._load_font(font_family, int(31 * scale), "bold")
        label_font = self._load_font(font_family, int(12 * scale), "bold")
        small_font = self._load_font(font_family, int(12 * scale))
        row_title_font = self._load_font(font_family, int(14 * scale), "bold")
        metric_font = self._load_font(font_family, int(17 * scale), "bold")
        hero_font = self._load_font(font_family, int(23 * scale), "bold")

        margin = int(24 * scale)
        header_y = int(22 * scale)
        header_h = int(58 * scale)
        footer_h = int(42 * scale)
        gap = int(14 * scale)
        left_w = int(width * 0.43)
        content_top = header_y + header_h + int(14 * scale)
        content_bottom = height - margin - footer_h

        self._draw_background_grid(draw, width, height, palette, scale)
        self._draw_header(image, draw, payload, now, margin, header_y, width - margin, palette, title_font, label_font, small_font, scale)

        stories = payload.get("stories") or []
        hero = stories[0] if stories else {}
        preview_story = hero
        left_box = (margin, content_top, margin + left_w, content_bottom)
        right_box = (left_box[2] + gap, content_top, width - margin, content_bottom)
        self._draw_lead_story(image, draw, left_box, hero, settings, palette, label_font, hero_font, small_font, metric_font, scale, preview_story=preview_story)
        self._draw_story_list(draw, right_box, stories[1:], settings, payload, palette, label_font, row_title_font, small_font, metric_font, scale)
        self._draw_footer(draw, payload, margin, height - margin - footer_h + int(9 * scale), width - margin, palette, small_font, label_font, scale)
        return image

    def _draw_header(self, image, draw, payload, now, x0, y0, x1, palette, title_font, label_font, small_font, scale):
        wordmark_drawn = self._draw_title_wordmark(
            image,
            x0,
            y0 - int(3 * scale),
            (int(TITLE_WORDMARK_DISPLAY_SIZE[0] * scale), int(TITLE_WORDMARK_DISPLAY_SIZE[1] * scale)),
        )
        if not wordmark_drawn:
            icon_size = int(38 * scale)
            draw.rounded_rectangle((x0, y0 + int(2 * scale), x0 + icon_size, y0 + icon_size + int(2 * scale)), radius=int(7 * scale), fill=palette["orange"])
            self._draw_text(draw, (x0 + int(13 * scale), y0 + int(7 * scale)), "Y", title_font, palette["background"])
            self._draw_text(draw, (x0 + icon_size + int(13 * scale), y0 - int(2 * scale)), "Tech Pulse", title_font, palette["ink"])

        self._draw_text(draw, (x0 + int(61 * scale), y0 + int(38 * scale)), "Hacker News v0 current signal", small_font, palette["muted"])

        status = (payload.get("status") or {}).get("source_state") or "local_sample"
        generated = self._format_generated_at((payload.get("status") or {}).get("generated_at"), now)
        chip = f"{status.upper()}  {generated}"
        chip_w = self._text_width(draw, chip, label_font) + int(22 * scale)
        self._pill(draw, (x1 - chip_w, y0 + int(8 * scale), x1, y0 + int(34 * scale)), palette["chip"], palette["rule"], int(10 * scale))
        self._draw_text(draw, (x1 - chip_w + int(11 * scale), y0 + int(14 * scale)), chip, label_font, palette["ink"])

    def _draw_lead_story(self, image, draw, box, story, settings, palette, label_font, hero_font, small_font, metric_font, scale, preview_story=None):
        x0, y0, x1, y1 = box
        self._panel(draw, box, palette, scale)
        pad = int(18 * scale)
        self._draw_text(draw, (x0 + pad, y0 + int(15 * scale)), "LEAD STORY", label_font, palette["orange"])
        rank = f"#{int(story.get('rank') or 1):02d}"
        rank_w = self._text_width(draw, rank, label_font) + int(18 * scale)
        self._pill(draw, (x1 - pad - rank_w, y0 + int(12 * scale), x1 - pad, y0 + int(36 * scale)), palette["chip"], palette["rule"], int(9 * scale))
        self._draw_text(draw, (x1 - pad - rank_w + int(9 * scale), y0 + int(18 * scale)), rank, label_font, palette["muted"])

        title = story.get("title") or "No story available"
        lines = self._wrap_text(draw, title, hero_font, x1 - x0 - pad * 2, max_lines=2)
        line_y = y0 + int(53 * scale)
        for line in lines:
            self._draw_text(draw, (x0 + pad, line_y), line, hero_font, palette["ink"])
            line_y += int(29 * scale)

        metric_y = y1 - int(62 * scale)
        meta_y = metric_y - int(26 * scale)
        preview_top = y0 + int(118 * scale)
        preview_bottom = meta_y - int(8 * scale)
        if preview_bottom > preview_top + int(42 * scale):
            self._draw_hn_story_preview(
                image,
                draw,
                (x0 + pad, preview_top, x1 - pad, preview_bottom),
                preview_story or story,
                palette,
                scale,
                not bool(settings.get("_theme_render_only")),
            )

        meta_line = []
        if self._enabled(settings.get("showDomain"), default=True):
            meta_line.append(story.get("domain") or "news.ycombinator.com")
        if self._enabled(settings.get("showByline"), default=True):
            meta_line.append(self._byline(story))
        if meta_line:
            self._draw_text(draw, (x0 + pad, meta_y), self._fit_text(draw, " | ".join(meta_line), small_font, x1 - x0 - pad * 2), small_font, palette["muted"])

        score = self._compact_number(story.get("score") or 0)
        comments = self._compact_number(story.get("comments") or 0)
        half = (x1 - x0 - pad * 2 - int(10 * scale)) // 2
        self._metric_card(draw, (x0 + pad, metric_y, x0 + pad + half, y1 - pad), "SCORE", score, palette, label_font, metric_font, scale)
        self._metric_card(draw, (x0 + pad + half + int(10 * scale), metric_y, x1 - pad, y1 - pad), "COMMENTS", comments, palette, label_font, metric_font, scale)

    def _draw_story_list(self, draw, box, stories, settings, payload, palette, label_font, row_title_font, small_font, metric_font, scale):
        x0, y0, x1, y1 = box
        self._panel(draw, box, palette, scale)
        pad = int(16 * scale)
        self._draw_text(draw, (x0 + pad, y0 + int(14 * scale)), "HN TOP 5", label_font, palette["orange"])
        feed_label = payload.get("feed_label") or "Top Stories"
        self._draw_text(draw, (x0 + pad + int(78 * scale), y0 + int(14 * scale)), feed_label.upper(), label_font, palette["muted"])

        row_top = y0 + int(43 * scale)
        row_gap = int(6 * scale)
        display = (stories or [])[:5]
        if display:
            row_count = len(display)
            row_h = int((y1 - row_top - pad - row_gap * max(0, row_count - 1)) / row_count)
            for index, story in enumerate(display):
                top = row_top + index * (row_h + row_gap)
                bottom = min(top + row_h, y1 - pad)
                self._story_row(draw, (x0 + pad, top, x1 - pad, bottom), story, settings, palette, row_title_font, small_font, label_font, scale)
        else:
            self._draw_text(draw, (x0 + pad, row_top + int(30 * scale)), "No stories available", metric_font, palette["muted"])

    def _story_row(self, draw, box, story, settings, palette, row_title_font, small_font, label_font, scale):
        x0, y0, x1, y1 = box
        radius = int(8 * scale)
        draw.rounded_rectangle(box, radius=radius, fill=palette["row"], outline=palette["rule"], width=max(1, int(scale)))
        rank = str(int(story.get("rank") or 0))
        rank_box = (x0 + int(8 * scale), y0 + int(8 * scale), x0 + int(32 * scale), y0 + int(32 * scale))
        draw.rounded_rectangle(rank_box, radius=int(7 * scale), fill=palette["orange"])
        self._draw_text(draw, (rank_box[0] + int(7 * scale), rank_box[1] + int(5 * scale)), rank, label_font, palette["background"])

        metrics_w = int(78 * scale)
        title_x = x0 + int(41 * scale)
        title_w = x1 - title_x - metrics_w - int(10 * scale)
        title = self._fit_text(draw, story.get("title") or "", row_title_font, title_w)
        self._draw_text(draw, (title_x, y0 + int(7 * scale)), title, row_title_font, palette["ink"])

        meta = []
        if self._enabled(settings.get("showDomain"), default=True):
            meta.append(story.get("domain") or "news.ycombinator.com")
        if self._enabled(settings.get("showByline"), default=True):
            meta.append(self._byline(story))
        if meta:
            meta_text = self._fit_text(draw, " | ".join(meta), small_font, title_w)
            self._draw_text(draw, (title_x, y0 + int(29 * scale)), meta_text, small_font, palette["muted"])

        score = self._compact_number(story.get("score") or 0)
        comments = self._compact_number(story.get("comments") or 0)
        metric_x = x1 - metrics_w
        self._draw_text(draw, (metric_x, y0 + int(8 * scale)), f"{score} pts", label_font, palette["amber"])
        self._draw_text(draw, (metric_x, y0 + int(28 * scale)), f"{comments} cmt", label_font, palette["cyan"])

    def _draw_footer(self, draw, payload, x0, y0, x1, palette, small_font, label_font, scale):
        stats = payload.get("stats") or {}
        source = (payload.get("status") or {}).get("source_state") or "local_sample"
        parts = [
            f"{stats.get('count', 0)} stories",
            f"{self._compact_number(stats.get('total_score', 0))} total pts",
            f"{self._compact_number(stats.get('total_comments', 0))} comments",
            f"source {source}",
        ]
        text = "  /  ".join(parts)
        self._draw_text(draw, (x0, y0 + int(11 * scale)), self._fit_text(draw, text, small_font, x1 - x0), small_font, palette["muted"])
        docs = "Official Hacker News Firebase API"
        docs_w = self._text_width(draw, docs, label_font)
        self._draw_text(draw, (x1 - docs_w, y0 + int(11 * scale)), docs, label_font, palette["cyan"])

    def _metric_card(self, draw, box, label, value, palette, label_font, metric_font, scale):
        draw.rounded_rectangle(box, radius=int(9 * scale), fill=palette["metric"], outline=palette["rule"], width=max(1, int(scale)))
        x0, y0, x1, y1 = box
        label_text = str(label)
        value_text = str(value)
        label_x = x0 + max(0, ((x1 - x0) - self._text_width(draw, label_text, label_font)) // 2)
        value_x = x0 + max(0, ((x1 - x0) - self._text_width(draw, value_text, metric_font)) // 2)
        self._draw_text(draw, (label_x, y0 + int(3 * scale)), label_text, label_font, palette["muted"])
        self._draw_text(draw, (value_x, y0 + int(17 * scale)), value_text, metric_font, palette["ink"])

    def _draw_hn_story_preview(
        self,
        image,
        draw,
        box,
        story,
        palette,
        scale,
        allow_fetch=True,
    ):
        x0, y0, x1, y1 = [int(value) for value in box]
        width = max(1, x1 - x0)
        height = max(1, y1 - y0)
        preview = (
            self._story_preview_image(story)
            if allow_fetch
            else self._story_preview_image(story, allow_fetch=False)
        )
        if preview is None:
            preview = self._fallback_story_preview((width, height), palette, scale, story)
        try:
            fitted = ImageOps.fit(preview.convert("RGB"), (width, height), method=Image.LANCZOS, centering=(0.5, 0.38))
            radius = max(6, int(8 * scale))
            mask = Image.new("L", (width, height), 0)
            mask_draw = ImageDraw.Draw(mask)
            mask_draw.rounded_rectangle((0, 0, width - 1, height - 1), radius=radius, fill=255)
            image.paste(fitted, (x0, y0), mask)
            draw.rounded_rectangle((x0, y0, x1 - 1, y1 - 1), radius=radius, outline=palette["rule"], width=max(1, int(scale)))
        except Exception as exc:
            logger.debug("Could not draw story preview: %s", exc)

    def _story_preview_image(self, story=None, allow_fetch=True):
        for preview_url in self._story_preview_candidate_urls(story):
            cache_path = self._story_preview_cache_path(preview_url)
            cached = self._load_story_preview_cache(cache_path)
            if cached is not None:
                return cached
            if not allow_fetch:
                continue
            captured = self._capture_story_preview_page(preview_url)
            if captured is None:
                continue
            prepared = self._prepare_story_screenshot(captured)
            try:
                output = BytesIO()
                prepared.save(output, format="PNG", optimize=True)
                self._story_preview_namespace().put_bytes(
                    cache_path.stem,
                    output.getvalue(),
                    suffix=cache_path.suffix,
                )
            except Exception as exc:
                logger.debug("Could not cache story preview %s: %s", cache_path, exc)
            return prepared
        return None

    def _capture_story_preview_page(self, url):
        return self._capture_story_preview_page_direct(url)

    def _capture_story_preview_page_direct(self, url):
        return take_screenshot(
            url,
            STORY_PREVIEW_CAPTURE_SIZE,
            timeout_ms=STORY_PREVIEW_TIMEOUT_MS,
            validator=validate_browser_target,
        )

    def _prepare_story_screenshot(self, image):
        page = image.convert("RGB")
        width, height = page.size
        crop_height = min(height, STORY_PREVIEW_CROP_HEIGHT)
        top = min(max(0, STORY_PREVIEW_CROP_TOP), max(0, height - crop_height))
        return page.crop((0, top, width, top + crop_height))

    def _load_story_preview_cache(self, path):
        try:
            data = self._story_preview_namespace().get_bytes(
                path.stem,
                suffix=path.suffix,
            )
            if data is not None:
                return safe_open_image(data).convert("RGB")
        except Exception as exc:
            logger.debug("Could not load story preview cache %s: %s", path, exc)
        return None

    def _story_preview_cache_path(self, url=None):
        target_url = url or "story-preview"
        parsed = urlparse(target_url)
        key = f"{parsed.netloc}{parsed.path}"
        if parsed.query:
            key = f"{key}-{parsed.query}"
        slug = re.sub(r"[^A-Za-z0-9._-]+", "-", key.strip("/")).strip("-").lower() or "story-preview"
        digest = hashlib.sha256(target_url.encode("utf-8")).hexdigest()[:16]
        cache_key = f"{slug[:120]}-{digest}-{STORY_PREVIEW_CACHE_VERSION}"
        return self._story_preview_namespace().path(cache_key, ".png")

    def _story_preview_namespace(self):
        return self.managed_cache_namespace(
            self._cache_dir() / "story_preview",
            STORY_PREVIEW_CACHE_BUDGET,
        )

    def _fallback_story_preview(self, size, palette, scale, story=None):
        width, height = [max(1, int(value)) for value in size]
        preview = Image.new("RGB", (width, height), palette["background"])
        draw = ImageDraw.Draw(preview)
        top_h = max(18, min(height // 3, int(24 * scale)))
        draw.rectangle((0, 0, width, top_h), fill=palette["ink"])
        title_font = self._load_font(DEFAULT_FONT, max(9, int(11 * scale)), "bold")
        small_font = self._load_font(DEFAULT_FONT, max(8, int(9 * scale)))
        tiny_font = self._load_font(DEFAULT_FONT, max(7, int(8 * scale)))
        domain = self._domain(self._story_preview_url(story)) or "target page"
        self._draw_text(draw, (int(8 * scale), int(5 * scale)), self._fit_text(draw, domain, title_font, width - int(16 * scale)), title_font, palette["background"])
        body_y = top_h + int(8 * scale)
        card = (int(7 * scale), body_y, width - int(7 * scale), height - int(7 * scale))
        draw.rounded_rectangle(card, radius=max(4, int(6 * scale)), fill=palette["panel"], outline=palette["rule"], width=1)
        title = self._clean_text((story or {}).get("title")) if isinstance(story, dict) else ""
        self._draw_text(draw, (card[0] + int(9 * scale), body_y + int(8 * scale)), self._fit_text(draw, title or "Story target page", title_font, width - int(32 * scale)), title_font, palette["ink"])
        self._draw_text(draw, (card[0] + int(9 * scale), body_y + int(27 * scale)), "Preview unavailable", small_font, palette["muted"])
        y = body_y + int(46 * scale)
        for line in (self._story_preview_url(story), "HN title target page"):
            if y + int(10 * scale) > height - int(8 * scale):
                break
            self._draw_text(draw, (card[0] + int(9 * scale), y), self._fit_text(draw, line, tiny_font, width - int(32 * scale)), tiny_font, palette["ink"])
            y += int(14 * scale)
        return preview

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

    def _story_preview_candidate_urls(self, story):
        urls = []
        for url in (self._story_preview_url(story), HN_HOME_URL):
            if url and url not in urls:
                urls.append(url)
        return urls

    def _panel(self, draw, box, palette, scale):
        draw.rounded_rectangle(box, radius=int(14 * scale), fill=palette["panel"], outline=palette["rule"], width=max(1, int(scale)))

    def _pill(self, draw, box, fill, outline, radius):
        draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=1)

    def _draw_title_wordmark(self, image, x, y, size):
        source = self._load_title_wordmark()
        if source is None:
            return False
        try:
            target_w, target_h = [max(1, int(value)) for value in size]
            fitted = ImageOps.contain(source, (target_w, target_h), method=Image.LANCZOS)
            layer = Image.new("RGBA", (target_w, target_h), (0, 0, 0, 0))
            layer.alpha_composite(fitted, ((target_w - fitted.width) // 2, (target_h - fitted.height) // 2))
            image.paste(layer.convert("RGB"), (int(x), int(y)), layer.getchannel("A"))
            return True
        except Exception as exc:
            logger.debug("Could not draw Tech Pulse title wordmark: %s", exc)
            return False

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
                "amber": (176, 102, 12),
                "cyan": (0, 108, 135),
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
            "orange": roles["accent"],
            "amber": roles["accent"],
            "cyan": roles["accent"],
        }

    def _write_context(self, payload, now):
        try:
            write_context(
                PLUGIN_ID,
                {
                    "feed": payload.get("feed"),
                    "stories": payload.get("stories") or [],
                    "stats": payload.get("stats") or {},
                    "status": payload.get("status") or {},
                    "source_provenance": payload.get("_source_provenance"),
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


    def _trim_flat_background(self, image):
        if image.width < 2 or image.height < 2:
            return image
        if image.mode == "RGBA":
            alpha_bbox = image.getchannel("A").getbbox()
            if alpha_bbox:
                pad = 2
                return image.crop((
                    max(0, alpha_bbox[0] - pad),
                    max(0, alpha_bbox[1] - pad),
                    min(image.width, alpha_bbox[2] + pad),
                    min(image.height, alpha_bbox[3] + pad),
                ))
        background = image.getpixel((0, 0))[:3]
        pixels = image.load()
        xs = []
        ys = []
        for y in range(image.height):
            for x in range(image.width):
                pixel = pixels[x, y]
                r, g, b = pixel[:3]
                if abs(r - background[0]) + abs(g - background[1]) + abs(b - background[2]) > 18:
                    xs.append(x)
                    ys.append(y)
        if not xs or not ys:
            return image
        pad = 2
        left = max(0, min(xs) - pad)
        top = max(0, min(ys) - pad)
        right = min(image.width, max(xs) + 1 + pad)
        bottom = min(image.height, max(ys) + 1 + pad)
        return image.crop((left, top, right, bottom))

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

    def _format_generated_at(self, value, fallback):
        parsed = self._parse_datetime(value) or self._to_utc(fallback)
        return parsed.strftime("%H:%M UTC")

    def _published_at(self, raw_time):
        if raw_time is None:
            return None
        try:
            return datetime.fromtimestamp(int(raw_time), timezone.utc)
        except Exception:
            return None

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
        age = int(story.get("age_hours") or 0)
        age_text = f"{age}h ago" if age < 48 else f"{age // 24}d ago"
        return f"by {by} | {age_text}"

    def _compact_number(self, value):
        try:
            number = int(value or 0)
        except Exception:
            number = 0
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

    def _wrap_text(self, draw, text, font, max_width, max_lines=2):
        words = str(text or "").split()
        lines = []
        current = ""
        for word in words:
            candidate = f"{current} {word}".strip()
            if current and self._text_width(draw, candidate, font) > max_width:
                lines.append(current)
                current = word
                if len(lines) >= max_lines:
                    break
            else:
                current = candidate
        if len(lines) < max_lines and current:
            lines.append(current)
        if not lines:
            lines = [""]
        if len(lines) > max_lines:
            lines = lines[:max_lines]
        return [self._fit_text(draw, line, font, max_width) for line in lines]

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

    def _draw_text(self, draw, xy, text, font, fill):
        draw.text(xy, str(text), font=font, fill=fill)

    def _enabled(self, value, default=False):
        return coerce_bool(value, default=default, truthy=("1", "true", "yes", "on"))
