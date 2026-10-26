"""整点新闻: the plugin entry, the day's cached brief and the page.

The work is split by concern; each part is a mixin of DailyAINews:
- news_sources: feed lists and sections, RSS fetching and ranking, recently shown titles;
- news_market: A-share and US index quotes for the market strip;
- news_brief: the AI prompt (as data at the top), the call, and the checks that turn its
  reply into a brief, plus the RSS-only fallback;
- news_text: traditional-to-simplified and English-name tables for visible text;
- news_common: helpers and column limits shared by all of them;
- news_layout and news_art: drawing the page.
This module decides when to fetch or reuse the cached brief and hands it to the layout.
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from PIL import Image, ImageFont, ImageOps
import pytz

from plugins.base_plugin.base_plugin import BasePlugin
from plugins.base_plugin.presentation import PresentationMode
from plugins.base_plugin.render_provenance import (
    SourceProvenance,
    attach_source_provenance,
)
from plugins.context_cache import write_context
from utils.app_utils import (
    get_available_font_names,
    get_base_ui_font,
    get_font,
)
from utils.image_utils import text_width
from utils.theme_utils import get_theme_palette

from . import news_common as common
from .news_brief import DEFAULT_MODEL, NEWS_TAG_MAX_CHARS, NewsBriefMixin, summary_model  # noqa: F401
from .news_common import MAINLAND_NEWS_MAX, WORLD_NEWS_MAX
from .news_layout import NewsPage, date_text as news_date_text
from .news_market import MARKET_GROUPS, MARKET_LOGOS, MARKET_SHORT_NAMES, NewsMarketMixin
from .news_sources import NewsSourcesMixin

logger = logging.getLogger(__name__)

DEFAULT_TITLE = "整点新闻"
DEFAULT_FONT = "Microsoft YaHei"
PLUGIN_DIR = Path(__file__).resolve().parent
# The header strip between the wordmark and the date: a generated TV newsroom (its source record
# beside it).
TITLE_BACKGROUND_IMAGE = "title_bg_newsroom.png"
TITLE_BACKGROUND_SIZE = (922, 96)
TITLE_WORDMARK_IMAGE = "title_wordmark.png"
TITLE_WORDMARK_SIZE = (220, 58)
MARKET_LOGO_DIR = PLUGIN_DIR / "market_logos"
SUMMARY_SCHEMA_VERSION = "mainland-world-lead-tags-v20"
# The page's accents are refined modern tones, not the panel's six pure inks: a crimson
# for the 头条 seal and mainland news, a cobalt for world news, and an emerald that
# pairs with the crimson for market moves (each board in its own convention, see
# news_layout). Night lifts each tone so it reads on black.
PAGE_DAY_COLOURS = {"lead": (200, 30, 50), "red": (200, 30, 50), "blue": (37, 84, 199), "green": (5, 150, 105)}
PAGE_NIGHT_COLOURS = {"lead": (220, 48, 66), "red": (255, 99, 112), "blue": (122, 162, 255),
                      "green": (52, 211, 153), "panel": (0, 0, 0)}
# The page is laid out for the 800x480 panel; other displays get it scaled to fit.
PAGE_SIZE = (800, 480)


class DailyAINews(NewsSourcesMixin, NewsMarketMixin, NewsBriefMixin, BasePlugin):
    def presentation_mode(self, settings):
        return PresentationMode.NO_CHANGE

    def generate_settings_template(self):
        params = super().generate_settings_template()
        params["style_settings"] = True
        params["api_key"] = {
            "required": True,
            "service": "OpenAI or Groq",
            "expected_key": "OPEN_AI_SECRET or GROQ_API_KEY",
        }
        params["available_fonts"] = get_available_font_names(default=DEFAULT_FONT)
        return params

    def generate_image(self, settings, device_config):
        settings = settings or {}
        dimensions = self.get_dimensions(device_config)

        tz_name = device_config.get_config("timezone") or "America/Los_Angeles"
        now = datetime.now(pytz.timezone(tz_name))

        try:
            brief = self._get_brief(settings, device_config, now)
        except Exception as exc:
            if common._enabled(settings.get("_theme_render_only")):
                raise
            logger.exception("Daily AI news failed")
            brief = self._fallback_brief(settings, now, str(exc))
            brief = self._mark_source_provenance(
                brief,
                SourceProvenance.LOCAL_FALLBACK,
            )

        brief = self._simplify_chinese_payload(brief)
        if not common._enabled(settings.get("_theme_render_only")):
            self._write_news_context(brief, now)
        theme_context = settings.get("_inkypi_theme") or self.resolve_theme(settings, device_config, now=now)
        image = self._render(dimensions, settings, brief, now, theme_context)
        return attach_source_provenance(
            image,
            brief.get("_source_provenance", SourceProvenance.LOCAL_FALLBACK),
            detail="daily_ai_news",
        )

    def _write_news_context(self, brief: dict[str, Any], now: datetime) -> None:
        payload = brief.get("brief") if isinstance(brief, dict) else {}
        if not isinstance(payload, dict):
            return

        context_news = self._as_list(payload.get("top"), MAINLAND_NEWS_MAX + WORLD_NEWS_MAX)
        if not context_news:
            context_news = self._as_list(payload.get("mainland"), MAINLAND_NEWS_MAX) + self._as_list(payload.get("world"), WORLD_NEWS_MAX)
        if isinstance(payload.get("lead"), dict):
            context_news = [payload["lead"], *context_news]

        items = []
        for item in context_news[:MAINLAND_NEWS_MAX + WORLD_NEWS_MAX + 1]:
            if isinstance(item, dict):
                title = str(item.get("title") or "").strip()
                why = str(item.get("why") or "").strip()
            else:
                title = str(item or "").strip()
                why = ""
            if title:
                items.append({"title": title[:120], "why": why[:140]})

        write_context(
            "daily_ai_news",
            {
                "kind": "news",
                "source": "Daily AI News",
                "summary": str(payload.get("lede") or "").strip()[:160],
                "items": items,
                "sources": payload.get("sources") or [],
                "from_cache": bool(brief.get("from_cache")),
                "source_provenance": brief.get("_source_provenance"),
            },
            generated_at=brief.get("generated_at") or now,
            ttl_seconds=24 * 60 * 60,
        )

    def _get_brief(self, settings, device_config, now: datetime) -> dict[str, Any]:
        model = summary_model(settings.get("model"))
        feeds_text = self._effective_feeds_text(settings.get("feed_urls"))
        max_items = common._parse_int(settings.get("max_items"), 22, 6, 40)
        theme_render_only = common._enabled(settings.get("_theme_render_only"))
        cache_only_render = theme_render_only or common._enabled(
            settings.get("_cached_render_only") or settings.get("cached_render_only")
        )
        force_refresh = common._enabled(settings.get("force_refresh")) and not cache_only_render
        date_key = now.strftime("%Y-%m-%d")
        cache_key = self._cache_key(
            date_key,
            model,
            feeds_text,
            max_items,
            settings.get("region_focus"),
        )
        cache_dir = self._cache_dir(create=False) if theme_render_only else self._cache_dir()
        cached = common._safe_json_load(cache_dir / "brief.json", {})
        cache_matches = cached.get("cache_key") == cache_key
        # A theme redraw only recolors the brief on screen. After local midnight
        # that is still the previous day's brief until the scheduled DATA
        # refresh, but never one built for other settings or another schema.
        if (
            theme_render_only
            and cached.get("brief")
            and not cache_matches
            and not self._brief_built_for_settings(
                cached,
                model,
                feeds_text,
                max_items,
                settings.get("region_focus"),
            )
        ):
            raise RuntimeError(
                "Theme-only redraw requires a matching Daily AI News cache."
            )
        payload = self._get_brief_unclassified(settings, device_config, now)
        if payload.get("from_cache"):
            policy_fresh = bool(payload.pop("_policy_fresh_cache", False))
            provenance = (
                SourceProvenance.FRESH_CACHE
                if policy_fresh or (cache_matches and not force_refresh)
                else SourceProvenance.STALE_CACHE
            )
        else:
            provenance = SourceProvenance.LIVE
        return self._mark_source_provenance(payload, provenance)

    def _get_brief_unclassified(
        self,
        settings,
        device_config,
        now: datetime,
    ) -> dict[str, Any]:
        model = summary_model(settings.get("model"))
        feeds_text = self._effective_feeds_text(settings.get("feed_urls"))
        max_items = common._parse_int(settings.get("max_items"), 22, 6, 40)
        theme_render_only = common._enabled(settings.get("_theme_render_only"))
        cache_only_render = theme_render_only or common._enabled(settings.get("_cached_render_only") or settings.get("cached_render_only"))
        force_refresh = common._enabled(settings.get("force_refresh")) and not cache_only_render
        date_key = now.strftime("%Y-%m-%d")
        cache_key = self._cache_key(date_key, model, feeds_text, max_items, settings.get("region_focus"))

        cache_dir = self._cache_dir(create=False) if theme_render_only else self._cache_dir()
        cache_file = cache_dir / "brief.json"
        cached = common._safe_json_load(cache_file, {})
        cache_matches_current_settings = cached.get("cache_key") == cache_key
        if cached.get("brief") and (cache_only_render or (cache_matches_current_settings and not force_refresh)):
            cached["from_cache"] = True
            if cache_only_render and not (
                cache_matches_current_settings
                or self._brief_built_for_settings(
                    cached,
                    model,
                    feeds_text,
                    max_items,
                    settings.get("region_focus"),
                )
            ):
                # Normal operation, not a failure: the header shows it as plain "· 缓存".
                cached["notice"] = "仅重渲染显示，复用现有新闻缓存。"
            return cached
        if theme_render_only:
            raise RuntimeError("Theme-only redraw requires a warm Daily AI News cache.")

        if (
            force_refresh
            and cache_matches_current_settings
            and cached.get("date") == date_key
            and not self._allow_api_call(settings, date_key)
        ):
            cached["from_cache"] = True
            cached["_policy_fresh_cache"] = True
            cached["notice"] = "已按今日 API 限额复用计划内最新缓存。"
            return cached

        recent_titles = self._load_recent_news_titles(now)
        items = self._drop_stale_items(self._fetch_items(feeds_text, max_items), now)
        items = self._rank_news_items(items, now, recent_titles)
        items = self._drop_recently_shown_items(items, recent_titles)
        items = self._diversify_news_items(items, max_items)
        if not items:
            stale = cached if cached.get("brief") else None
            if stale:
                stale["from_cache"] = True
                stale["warning"] = "新闻源暂不可用，显示旧缓存。"
                return stale
            raise RuntimeError("No RSS items could be fetched.")

        stale = cached if cached.get("brief") else None
        stale_matches_current_settings = bool(stale and cache_matches_current_settings)
        market_snapshot = self._fetch_market_snapshot(now, device_config)
        openai_key = device_config.load_env_key("OPEN_AI_SECRET") or device_config.load_env_key("OPENAI_API_KEY")
        groq_key = device_config.load_env_key("GROQ_API_KEY")
        can_call_ai = self._allow_api_call(settings, date_key)
        rss_needs_translation = self._rss_items_need_translation(items)

        if openai_key or groq_key:
            if not can_call_ai:
                if stale_matches_current_settings or (stale and rss_needs_translation):
                    stale["from_cache"] = True
                    stale["warning"] = "已达到今日 API 调用上限，显示旧中文缓存。"
                    return stale
                brief = self._rss_only_brief(items, "已达到今日 API 调用上限，使用 RSS 兜底。", recent_titles)
            else:
                try:
                    brief = self._summarize_with_ai(openai_key, groq_key, model, settings, items, market_snapshot, now, recent_titles)
                except Exception as exc:
                    logger.warning("AI summary failed; using RSS fallback: %s", exc)
                    if stale_matches_current_settings or (stale and rss_needs_translation):
                        stale["from_cache"] = True
                        stale["warning"] = f"AI 摘要失败，显示旧中文缓存：{str(exc)[:80]}"
                        return stale
                    brief = self._rss_only_brief(items, f"AI 摘要失败，使用 RSS 兜底：{str(exc)[:60]}", recent_titles)
        else:
            if stale and rss_needs_translation:
                stale["from_cache"] = True
                stale["warning"] = "未配置 AI 密钥，显示旧中文缓存。"
                return stale
            brief = self._rss_only_brief(items, "未配置 AI 密钥，使用 RSS 兜底。", recent_titles)

        payload_model = brief.pop("_model", model)
        used_ai = bool(brief.pop("_used_ai", False))
        payload_warning = brief.pop("_fallback_warning", "")
        payload = {
            "cache_key": cache_key,
            "date": date_key,
            "generated_at": now.isoformat(),
            "model": payload_model,
            "items": items[:max_items],
            "market_snapshot": market_snapshot,
            "brief": brief,
            "from_cache": False,
        }
        if payload_warning:
            payload["warning"] = payload_warning
        payload = self._simplify_chinese_payload(payload)
        common._safe_json_write(cache_file, payload)
        self._record_recent_news_titles(payload, now)
        if used_ai:
            self._record_api_call(date_key)
        return payload

    @staticmethod
    def _mark_source_provenance(payload, provenance):
        result = dict(payload)
        result["_source_provenance"] = SourceProvenance(provenance).value
        return result

    def _cache_dir(self, create=True) -> Path:
        return self.cache_dir(leaf="cache", create=create)

    def _cache_key(self, date_key: str, model: str, feeds_text: str, max_items: int, region_focus: Any) -> str:
        raw = "\n".join([SUMMARY_SCHEMA_VERSION, date_key, model, feeds_text, str(max_items), str(region_focus or "")])
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _brief_built_for_settings(
        self,
        cached: dict[str, Any],
        model: str,
        feeds_text: str,
        max_items: int,
        region_focus: Any,
    ) -> bool:
        """Return whether a cached brief matches these settings on its own date."""
        date_key = cached.get("date")
        if not isinstance(date_key, str) or not date_key:
            return False
        return cached.get("cache_key") == self._cache_key(
            date_key,
            model,
            feeds_text,
            max_items,
            region_focus,
        )


    def _render(self, dimensions, settings, payload: dict[str, Any], now: datetime, theme_context=None) -> Image.Image:
        raw_title = str(settings.get("brief_title") or "").strip()
        title = DEFAULT_TITLE if not raw_title or raw_title == "二狗新闻" else raw_title
        title = self._simplify_chinese_text(title)
        payload = self._simplify_chinese_payload(payload)
        brief = payload.get("brief") or {}
        palette = self._page_palette(theme_context)
        img = self._base_background(PAGE_SIZE, palette["background"], (theme_context or {}).get("mode", "day"))
        lead, mainland, world = self._page_news(brief, payload)
        NewsPage(self, img, palette, DEFAULT_FONT).render(
            title=title,
            lead=lead,
            mainland=mainland,
            world=world,
            quotes=self._page_quotes(payload),
            meta=self._page_meta(payload, now),
        )
        if tuple(dimensions) != PAGE_SIZE:
            img = self._fit_page(img, dimensions, palette["background"])
        return img

    @staticmethod
    def _fit_page(page: Image.Image, dimensions, background) -> Image.Image:
        """The 800x480 page scaled to fit another display, centred on the page colour."""
        resample = Image.Resampling.LANCZOS if hasattr(Image, "Resampling") else Image.LANCZOS
        fitted = ImageOps.contain(page, tuple(int(value) for value in dimensions), method=resample)
        canvas = Image.new("RGB", tuple(int(value) for value in dimensions), background)
        canvas.paste(fitted, ((canvas.width - fitted.width) // 2, (canvas.height - fitted.height) // 2))
        return canvas

    def _page_palette(self, theme_context) -> dict[str, Any]:
        palette = dict(self._render_palette(theme_context))
        night = isinstance(theme_context, dict) and theme_context.get("mode") == "night"
        palette.update(PAGE_NIGHT_COLOURS if night else PAGE_DAY_COLOURS)
        return palette

    @staticmethod
    def _contrast_ink(colour, palette) -> tuple[int, int, int]:
        red, green, blue = [int(value) for value in tuple(colour)[:3]]
        return (0, 0, 0) if 0.299 * red + 0.587 * green + 0.114 * blue > 150 else (255, 255, 255)

    def _page_news(self, brief: dict[str, Any], payload: dict[str, Any]):
        """The lead story and the two columns, the lead never repeated in a column."""
        mainland = [self._page_item(item) for item in self._render_news_section_items(brief, payload, "mainland")]
        world = [self._page_item(item) for item in self._render_news_section_items(brief, payload, "world")]
        mainland = self._without_world_copies([item for item in mainland if item], world)
        world = [item for item in world if item]
        raw_lead = brief.get("lead") if isinstance(brief.get("lead"), dict) else None
        lead = self._page_item(raw_lead) if raw_lead else None
        section = str((raw_lead or {}).get("section") or "")
        if lead:
            mainland = [item for item in mainland if not self._similar_news_title(item["title"], lead["title"])]
            world = [item for item in world if not self._similar_news_title(item["title"], lead["title"])]
        elif mainland:
            lead, section = mainland.pop(0), "mainland"
        elif world:
            lead, section = world.pop(0), "world"
        if lead:
            lead["section_label"] = "国际" if section == "world" else "国内"
            lead["source_note"] = self._lead_source_note(lead["title"], payload)
        return lead, mainland, world

    def _lead_source_note(self, title: str, payload: dict[str, Any]) -> str:
        """Where and when the lead's RSS item was published, e.g. "中新网要闻 · 10:52 发布"."""
        item = next(
            (item for item in payload.get("items") or []
             if isinstance(item, dict) and self._similar_news_title(title, str(item.get("title") or ""))),
            None,
        )
        if item is None:
            return ""
        source = str(item.get("source") or "").strip().replace("中国新闻网", "中新网")
        published = self._parse_published(str(item.get("published") or ""))
        if published is not None:
            try:
                zone = datetime.fromisoformat(str(payload.get("generated_at") or "")).tzinfo
            except ValueError:
                zone = None
            published = published.astimezone(zone) if zone else published
        when = f"{published:%H:%M} 发布" if published is not None else ""
        return " · ".join(part for part in (source, when) if part)

    def _page_item(self, item) -> dict[str, str] | None:
        title, why = self._news_text(item)
        # Briefs cached before a sanitizer fix are tidied here as well.
        title = self._tidy_visible_text(title)
        if not title:
            return None
        # Cached tags from before the fixed vocabulary are mapped onto it too.
        tag = self._normalized_tag(item.get("tag"), title) if isinstance(item, dict) and "tag" in item else ""
        return {"title": title, "why": self._tidy_visible_text(why), "tag": tag[:NEWS_TAG_MAX_CHARS]}

    def _page_quotes(self, payload: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
        groups = (payload.get("market_snapshot") or {}).get("groups") or {}
        quotes = {}
        for key in MARKET_GROUPS:
            rows = groups.get(key) if isinstance(groups.get(key), list) else []
            quotes[key] = [
                {
                    "label": MARKET_SHORT_NAMES.get(str(row.get("name") or ""), str(row.get("name") or row.get("symbol") or "")),
                    "logo": MARKET_LOGOS.get(str(row.get("name") or "")),
                    "price": row.get("price"),
                    "change_pct": row.get("change_pct"),
                    "closes": row.get("closes") or [],
                    "as_of_label": self._month_day(row.get("as_of")),
                }
                for row in rows[:3]
                if isinstance(row, dict)
            ]
        return quotes

    @staticmethod
    def _month_day(value: Any) -> str:
        match = re.match(r"\d{4}-(\d{2})-(\d{2})", str(value or ""))
        return f"{match.group(1)}/{match.group(2)}" if match else ""

    def _page_meta(self, payload: dict[str, Any], now: datetime) -> dict[str, Any]:
        try:
            generated = datetime.fromisoformat(str(payload.get("generated_at") or ""))
        except ValueError:
            generated = now
        sources = {"mainland": [], "world": []}
        for item in payload.get("items") or []:
            if not isinstance(item, dict):
                continue
            source = str(item.get("source") or "").strip().replace("中国新闻网", "中新网")
            names = sources[self._item_section(item)]
            if source and source not in names:
                names.append(source)
        count = len(sources["mainland"]) + len(sources["world"])
        detail = f"{generated:%H:%M} 更新" + (f" · {count} 个信源" if count else "")
        if payload.get("from_cache"):
            detail += " · 缓存"
        warning = str(payload.get("warning") or "").strip()
        return {
            "date_text": news_date_text(generated),
            "detail": warning or detail,
            "warning": bool(warning),
            "mainland_sources": sources["mainland"],
            "world_sources": sources["world"],
        }

    @staticmethod
    def _render_palette(theme_context):
        if not isinstance(theme_context, dict) or theme_context.get("mode") != "night":
            return get_theme_palette("day")
        canonical = theme_context.get("palette")
        if not isinstance(canonical, dict):
            return get_theme_palette("night")
        red = canonical["accent"]
        muted = canonical["muted"]
        return {
            "background": (0, 0, 0),
            "header": (0, 0, 0),
            "ink": canonical["ink"],
            "muted": muted,
            "dim": muted,
            "rule": canonical["rule"],
            "red": red,
            "gold": tuple(
                round((accent * 3 + secondary) / 4)
                for accent, secondary in zip(red, muted)
            ),
            "cyan": tuple(
                round((accent + secondary) / 2)
                for accent, secondary in zip(red, muted)
            ),
            "green": tuple(
                round((accent + secondary * 3) / 4)
                for accent, secondary in zip(red, muted)
            ),
        }

    def _render_news_section_items(self, brief: dict[str, Any], payload: dict[str, Any], section: str) -> list[Any]:
        max_count = MAINLAND_NEWS_MAX if section == "mainland" else WORLD_NEWS_MAX
        existing = self._as_list(brief.get(section), max_count)
        # Only a brief from before the two sections splits its "top" list; an empty
        # section of a current brief must not borrow the other section's stories.
        if not existing and "mainland" not in brief and "world" not in brief:
            legacy_mainland, legacy_world = self._split_legacy_top_sections(brief.get("top"))
            existing = legacy_mainland if section == "mainland" else legacy_world

        target_count = self._section_display_limit(payload, section, len(existing))
        selected = existing[:target_count]
        if len(selected) < target_count:
            rss_items = [item for item in list(payload.get("items") or []) if isinstance(item, dict) and self._item_section(item) == section]
            selected = self._rss_news_cards(rss_items, target_count, selected)
        return selected[:target_count]

    def _section_display_limit(self, payload: dict[str, Any], section: str, existing_count: int) -> int:
        max_count = MAINLAND_NEWS_MAX if section == "mainland" else WORLD_NEWS_MAX
        if existing_count:
            return min(max_count, max(0, existing_count))
        rss_count = sum(
            1
            for item in list(payload.get("items") or [])
            if isinstance(item, dict) and self._item_section(item) == section
        )
        return min(max_count, max(0, rss_count))

    def _base_background(self, dimensions, bg, theme_mode="day") -> Image.Image:
        return Image.new("RGB", dimensions, bg)

    def _draw_title_wordmark(self, image: Image.Image, x, y, size, ink):
        source = self._load_title_wordmark()
        if source is None:
            return None

        try:
            target_w, target_h = [int(value) for value in size]
            resample = Image.Resampling.LANCZOS if hasattr(Image, "Resampling") else Image.LANCZOS
            art = ImageOps.contain(source.copy(), (target_w, target_h), method=resample)
            art = self._prepare_title_wordmark(art, ink)
            layer = Image.new("RGBA", (target_w, target_h), (0, 0, 0, 0))
            paste_x = int((target_w - art.width) / 2)
            paste_y = int((target_h - art.height) / 2)
            layer.alpha_composite(art, (paste_x, paste_y))
            image.paste(layer.convert("RGB"), (int(x), int(y)), layer.getchannel("A"))
            bbox = layer.getchannel("A").getbbox()
            if not bbox:
                return None
            return (int(x) + bbox[0], int(y) + bbox[1], int(x) + bbox[2], int(y) + bbox[3])
        except Exception as exc:
            logger.warning("Daily AI News title wordmark unavailable: %s", exc)
            return None

    def _draw_title_background(self, image: Image.Image, box, align="middle") -> bool:
        left, top, right, bottom = [int(round(value)) for value in box]
        target_w = max(0, right - left)
        target_h = max(0, bottom - top)
        if target_w < 80 or target_h < 24:
            return False

        source = self._load_title_background()
        if source is None:
            return False

        try:
            resample = Image.Resampling.LANCZOS if hasattr(Image, "Resampling") else Image.LANCZOS
            art = ImageOps.contain(source.copy(), (target_w, target_h), method=resample)
            layer = Image.new("RGBA", (target_w, target_h), (0, 0, 0, 0))
            paste_x = int((target_w - art.width) / 2)
            paste_y = target_h - art.height if align == "bottom" else int((target_h - art.height) / 2)
            layer.alpha_composite(art, (paste_x, paste_y))
            image.paste(layer.convert("RGB"), (left, top), layer.getchannel("A"))
            return True
        except Exception as exc:
            logger.warning("Daily AI News title background unavailable: %s", exc)
            return False

    @staticmethod
    def _prepare_title_wordmark(source: Image.Image, ink):
        wordmark = source.convert("RGBA")
        ink_rgb = tuple(int(value) for value in tuple(ink)[:3])
        if sum(ink_rgb) < 384:
            return wordmark

        pixels = wordmark.load()
        for y in range(wordmark.height):
            for x in range(wordmark.width):
                r, g, b, a = pixels[x, y]
                if not a:
                    continue
                is_red_accent = r > 120 and r > g * 1.25 and r > b * 1.25
                if not is_red_accent:
                    pixels[x, y] = ink_rgb + (a,)
        return wordmark

    @staticmethod
    @lru_cache(maxsize=1)
    def _load_title_wordmark():
        path = PLUGIN_DIR / TITLE_WORDMARK_IMAGE
        if not path.is_file():
            return None
        try:
            return Image.open(path).convert("RGBA")
        except Exception as exc:
            logger.warning("Could not load Daily AI News title wordmark %s: %s", path, exc)
            return None

    @staticmethod
    @lru_cache(maxsize=8)
    def _load_market_logo(key):
        """An index mark from market_logos/ in its original colours, or None."""
        path = MARKET_LOGO_DIR / f"{key}.png"
        if not key or not path.is_file():
            return None
        try:
            return Image.open(path).convert("RGBA")
        except (OSError, ValueError) as exc:
            logger.warning("Could not load Daily AI News market logo %s: %s", path, exc)
            return None

    @staticmethod
    @lru_cache(maxsize=1)
    def _load_title_background():
        path = PLUGIN_DIR / TITLE_BACKGROUND_IMAGE
        if not path.is_file():
            return None
        try:
            return Image.open(path).convert("RGBA")
        except Exception as exc:
            logger.warning("Could not load Daily AI News title background %s: %s", path, exc)
            return None

    @lru_cache(maxsize=128)
    def _font(self, family: str, size: int, weight: str = "normal"):
        if str(family or "").strip().lower() == DEFAULT_FONT.lower():
            font = self._microsoft_yahei_font(size, weight)
            if font:
                return font

        seen = set()
        for candidate in (family, DEFAULT_FONT, "方正新楷近似", "FandolKai"):
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            try:
                font = get_font(candidate, size, weight)
                if font:
                    return font
            except OSError:
                continue
            except Exception:
                continue
        return ImageFont.load_default()

    def _microsoft_yahei_font(self, size: int, weight: str = "normal"):
        return get_base_ui_font(
            int(size),
            bold=str(weight or "normal").lower() == "bold",
        )

    def _news_text(self, item) -> tuple[str, str]:
        if isinstance(item, dict):
            return str(item.get("title") or ""), str(item.get("why") or "")
        return str(item), ""

    def _is_chinese_title(self, text: str) -> bool:
        """A displayable RSS title: mostly Chinese, not an English one with a translated word."""
        cjk = len(re.findall(r"[\u3400-\u9fff]", text))
        latin = len(re.findall(r"[A-Za-z]", text))
        return cjk >= 2 and cjk * 2 >= latin

    def _has_cjk(self, text: str) -> bool:
        return bool(re.search(r"[\u3400-\u9fff]", text))

    def _tw(self, draw, text: str, font) -> int:
        return text_width(draw, text, font)
