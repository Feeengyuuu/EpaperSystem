from __future__ import annotations
from utils.resource_cache import cached_resource_image, measured_image_response, prune_resource_images

import hashlib
import html
import json
import logging
import re
import time
from copy import deepcopy
from datetime import datetime
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

from PIL import Image, ImageFont

from plugins.base_plugin.base_plugin import BasePlugin
from plugins.base_plugin.presentation import PresentationMode
from plugins.base_plugin.render_provenance import (
    SourceProvenance,
    attach_source_provenance,
)
from plugins.context_cache import write_context
from plugins.daily_wiki_page import zh_daily_picture
from runtime.refresh_contracts import TaskCancelled
from utils.app_utils import DEFAULT_FONT_FAMILY, coerce_bool, get_available_font_names, get_base_ui_font, get_font
from utils.cache_manager import CacheBudget, CachePathError
from utils.http_client import get_http_session
from utils.image_utils import text_width
from utils.safe_image import ImageLimits, safe_open_image, safe_open_image_response

logger = logging.getLogger(__name__)


class _HTMLTextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._parts = []

    def handle_data(self, data):
        if data:
            self._parts.append(data)

    def text(self):
        return "".join(self._parts)

PLUGIN_ID = "daily_wiki_page"
CACHE_SCHEMA_VERSION = "daily-wiki-page-v8"
DEFAULT_FONT = DEFAULT_FONT_FAMILY
DEFAULT_TIMEZONE = "America/Los_Angeles"
FEED_URL = "https://{language}.wikipedia.org/api/rest_v1/feed/featured/{year}/{month}/{day}"
ZH_ACTION_API_URL = "https://zh.wikipedia.org/w/api.php"
ZH_SIMPLIFIED_VARIANT = "zh-cn"
ZH_DATE_PAGE_API_URL = "https://zh.wikipedia.org/w/api.php"
# Wikimedia throttles anonymous clients whose User-Agent carries no contact URL.
USER_AGENT = "InkyPi DailyWikiPage/1.0 (https://github.com/Feeengyuuu/EpaperSystem)"
REQUEST_HEADERS = {"User-Agent": USER_AGENT, "Accept": "application/json,*/*;q=0.8"}
IMAGE_HEADERS = {"User-Agent": USER_AGENT, "Accept": "image/jpeg,image/png,image/webp,image/*;q=0.8"}
RESAMPLE = getattr(Image, "Resampling", Image).LANCZOS
DEFAULT_IMAGE_CACHE_HOURS = 24
MAX_IMAGE_CACHE_HOURS = 30 * 24
MEDIA_CACHE_BUDGET = CacheBudget(
    max_age_seconds=30 * 24 * 60 * 60,
    max_files=256,
    max_bytes=50 * 1024 * 1024,
)

TRADITIONAL_TO_SIMPLIFIED = str.maketrans({
    "俠": "侠", "盜": "盗", "獵": "猎", "車": "车", "獲": "获", "獎": "奖", "與": "与",
    "維": "维", "體": "体", "條": "条", "圖": "图", "書": "书", "門": "门", "頁": "页",
    "歷": "历", "國": "国", "華": "华", "臺": "台", "灣": "湾", "龍": "龙", "馬": "马",
    "開": "开", "發": "发", "廣": "广", "東": "东", "風": "风", "雲": "云", "電": "电",
    "學": "学", "術": "术", "藝": "艺", "畫": "画", "樂": "乐", "詩": "诗", "詞": "词",
    "語": "语", "讀": "读", "寫": "写", "聽": "听", "說": "说", "記": "记", "錄": "录",
    "數": "数", "據": "据", "網": "网", "絡": "络", "軟": "软", "軌": "轨", "轉": "转",
    "動": "动", "務": "务", "員": "员", "觀": "观", "現": "现", "實": "实", "愛": "爱",
    "長": "长", "歲": "岁", "時": "时", "間": "间", "點": "点", "處": "处", "區": "区",
    "類": "类", "別": "别", "參": "参", "萬": "万", "億": "亿", "後": "后",
    "無": "无", "為": "为", "這": "这", "個": "个", "們": "们", "來": "来", "從": "从",
    "會": "会", "還": "还", "並": "并", "於": "于", "產": "产", "業": "业", "項": "项",
    "題": "题", "號": "号", "標": "标", "準": "准", "選": "选", "獨": "独", "聯": "联",
    "勝": "胜", "敗": "败", "隊": "队", "賽": "赛", "獻": "献", "館": "馆", "傳": "传",
    "達": "达", "邊": "边", "遠": "远", "進": "进", "過": "过", "運": "运", "構": "构",
    "劃": "划", "劍": "剑", "島": "岛", "燈": "灯", "熱": "热", "裏": "里", "裡": "里",
})
LOCAL_FALLBACK_PAGES = (
    {
        "title": "Printing press",
        "description": "A machine that applies pressure to an inked surface.",
        "extract": "The printing press made books cheaper to produce and helped knowledge circulate faster across early modern Europe. Movable type and press mechanics changed publishing from a slow craft into a repeatable information system.",
        "page_url": "https://en.wikipedia.org/wiki/Printing_press",
        "language": "en",
    },
    {
        "title": "Library of Alexandria",
        "description": "One of the largest libraries of the ancient world.",
        "extract": "The Library of Alexandria became a symbol of collected knowledge because it gathered texts, scholars, and translation work in one place. Its exact fate is debated, but its cultural afterlife remains unusually strong.",
        "page_url": "https://en.wikipedia.org/wiki/Library_of_Alexandria",
        "language": "en",
    },
    {
        "title": "百科全书",
        "description": "按主题组织知识的参考工具。",
        "extract": "百科全书把零散知识整理成可检索的条目。它的价值不只是提供答案，也在于把概念、人物、地点和历史背景放进同一个知识网络里。",
        "page_url": "https://zh.wikipedia.org/wiki/百科全书",
        "language": "zh-cn",
    },
    {
        "title": "敦煌文献",
        "description": "发现于莫高窟藏经洞的大量古代文献。",
        "extract": "敦煌文献保留了宗教、文学、社会生活和语言文字等多方面材料。它们让研究者能从具体文本中观察中古时期丝绸之路上的知识流动。",
        "page_url": "https://zh.wikipedia.org/wiki/敦煌文献",
        "language": "zh-cn",
    },
)

class DailyWikiPage(BasePlugin):
    def presentation_mode(self, settings):
        return PresentationMode.NO_CHANGE

    def generate_settings_template(self):
        params = super().generate_settings_template()
        params["style_settings"] = False
        params["available_fonts"] = get_available_font_names(default=DEFAULT_FONT)
        return params

    def generate_image(self, settings, device_config):
        settings = dict(settings or {})
        settings["_inkypi_theme"] = settings.get(
            "_inkypi_theme"
        ) or self.resolve_theme(settings, device_config)
        if settings.get("_daily_wiki_cached_display") and settings.get("_theme_render_only"):
            return self._render_cached_snapshot(settings, device_config)
        now = self._now_for_device(device_config)
        payload = self._daily_payload(settings, now)
        if not settings.get("_theme_render_only"):
            self._write_context(payload, now)
        image = self._render_page(
            self.get_dimensions(device_config),
            payload,
            settings,
            now,
        )
        return attach_source_provenance(
            image,
            payload.get("_source_provenance", SourceProvenance.LOCAL_FALLBACK),
            detail="daily_wiki_page",
        )

    def render_cached_display(self, settings, device_config, *, resolved_theme_context):
        """Redraw the current UI from an exact local source snapshot, without DATA."""
        return self.render_themed_image(
            {**(settings or {}), "_daily_wiki_cached_display": True}, device_config,
            theme_render_only=True, resolved_theme_context=resolved_theme_context,
        )

    def _cached_display_source(self, settings, now):
        path = self._cache_path(create=False).absolute()
        try:
            if any(part.is_symlink() for part in (path, *path.parents)):
                raise ValueError("source cache traverses a symlink")
            if path.stat().st_size > 8 * 1024 * 1024:
                raise ValueError("source cache exceeds the size limit")
            entry = self._read_cache(create=False)
            payload = entry.get("payload")
            if entry.get("schema") != CACHE_SCHEMA_VERSION or not isinstance(payload, dict):
                raise ValueError("source schema or payload is invalid")
            source_date = datetime.strptime(str(payload.get("date", "")), "%Y-%m-%d").date()
            if source_date.isoformat() != payload.get("date"):
                raise ValueError("source date is invalid")
            generated_at = entry.get("generated_at")
            generated = datetime.fromisoformat(generated_at)
            clock = now if now.tzinfo is not None else now.replace(tzinfo=ZoneInfo(DEFAULT_TIMEZONE))
            if generated.tzinfo is None:
                generated = generated.replace(tzinfo=clock.tzinfo)
            if (generated.timestamp() <= 0 or generated.timestamp() > clock.timestamp() + 300
                    or source_date > clock.date()
                    or generated.date() != source_date):
                raise ValueError("source timestamp or date is inconsistent")
            language = self._language(settings)
            fallback = self._fallback_language(settings, language)
            expected = self._cache_key(source_date.isoformat(), settings, language, fallback)
            if entry.get("cache_key") != expected or payload.get("cache_key", expected) != expected:
                raise ValueError("source cache does not match the current settings")
        except (OSError, TypeError, ValueError, OverflowError) as exc:
            raise RuntimeError("Daily Wiki cached display requires a valid matching local source snapshot.") from exc
        return deepcopy(payload), generated_at, source_date == clock.date()

    def _render_cached_snapshot(self, settings, device_config):
        now = self._now_for_device(device_config)
        payload, generated_at, fresh = self._cached_display_source(settings, now)
        payload["source_state"] = "cache"
        image = self._render_page(self.get_dimensions(device_config), payload, settings, now)
        complete = image.info.get("daily_wiki_layout", {}).get("complete", False)
        image.info.update(
            daily_wiki_cached_display=True,
            daily_wiki_source_date=payload["date"],
            daily_wiki_source_generated_at=generated_at,
        )
        if not fresh or not complete:
            image.info["inkypi_skip_cache"] = True
        return attach_source_provenance(
            image,
            SourceProvenance.FRESH_CACHE if fresh and complete else SourceProvenance.STALE_CACHE,
            detail="daily_wiki_page_cached_display",
        )

    def _now_for_device(self, device_config):
        timezone_name = DEFAULT_TIMEZONE
        if device_config is not None and hasattr(device_config, "get_config"):
            timezone_name = device_config.get_config("timezone", DEFAULT_TIMEZONE) or DEFAULT_TIMEZONE
        try:
            return datetime.now(ZoneInfo(str(timezone_name)))
        except Exception:
            return datetime.now(ZoneInfo(DEFAULT_TIMEZONE))

    def _daily_payload(self, settings, now):
        language = self._language(settings)
        fallback_language = self._fallback_language(settings, language)
        date_key = now.strftime("%Y-%m-%d")
        cache_key = self._cache_key(date_key, settings, language, fallback_language)
        theme_render_only = bool(settings.get("_theme_render_only"))
        cache = self._read_cache(create=not theme_render_only)
        source_cache_ready = (
            cache.get("schema") == CACHE_SCHEMA_VERSION
            and cache.get("cache_key") == cache_key
            and isinstance(cache.get("payload"), dict)
        )
        force_refresh = self._enabled(
            settings.get("forceRefresh") or settings.get("force_refresh"),
            default=False,
        )
        payload = self._daily_payload_unclassified(settings, now)
        source_state = payload.get("source_state")
        if source_state == "live":
            provenance = SourceProvenance.LIVE
        elif source_state == "cache":
            provenance = (
                SourceProvenance.FRESH_CACHE
                if source_cache_ready and not force_refresh
                else SourceProvenance.STALE_CACHE
            )
        else:
            provenance = SourceProvenance.LOCAL_FALLBACK
        result = dict(payload)
        result["_source_provenance"] = provenance.value
        return result

    def _daily_payload_unclassified(self, settings, now):
        language = self._language(settings)
        fallback_language = self._fallback_language(settings, language)
        date_key = now.strftime("%Y-%m-%d")
        cache_key = self._cache_key(date_key, settings, language, fallback_language)
        theme_render_only = bool(settings.get("_theme_render_only"))
        cache = self._read_cache(create=not theme_render_only)
        force_refresh = self._enabled(settings.get("forceRefresh") or settings.get("force_refresh"), default=False)
        cached = cache.get("payload")
        source_cache_ready = (
            cache.get("schema") == CACHE_SCHEMA_VERSION
            and cache.get("cache_key") == cache_key
            and isinstance(cached, dict)
        )
        if source_cache_ready and (
            bool(settings.get("_theme_render_only")) or not force_refresh
        ):
            payload = dict(cached)
            payload["source_state"] = "cache"
            return payload
        if settings.get("_theme_render_only"):
            raise RuntimeError(
                "Daily Wiki theme-only render requires matching cached source data."
            )

        try:
            payload = self._fetch_live_payload(now, language, fallback_language, settings)
            payload.update({"date": date_key, "source_state": "live", "cache_key": cache_key})
            self._write_cache({"schema": CACHE_SCHEMA_VERSION, "cache_key": cache_key, "generated_at": now.isoformat(), "payload": payload})
            return payload
        except Exception as exc:
            logger.warning("DailyWikiPage live fetch failed: %s", exc)

        cached = cache.get("payload")
        if cache.get("schema") == CACHE_SCHEMA_VERSION and isinstance(cached, dict):
            payload = dict(cached)
            payload["source_state"] = "cache"
            return payload
        payload = self._local_fallback_payload(language, date_key)
        payload["source_state"] = "local"
        payload["cache_key"] = cache_key
        return payload

    def _fetch_live_payload(self, now, language, fallback_language, settings):
        errors = []
        primary_failure_type = "UnknownError"
        languages = [language]
        if fallback_language and fallback_language not in languages:
            languages.append(fallback_language)
        for current_language in languages:
            try:
                feed = self._fetch_feed(now, self._feed_language(current_language))
                payload = self._payload_from_feed(feed, current_language, settings, now=now)
                if payload.get("title") and payload.get("extract"):
                    if current_language != language:
                        logger.warning(
                            "DailyWikiPage primary language %s failed; using fallback language %s (%s)",
                            language,
                            current_language,
                            primary_failure_type,
                        )
                    return payload
                errors.append(f"{current_language}: EmptyArticle")
                if current_language == language:
                    primary_failure_type = "EmptyArticle"
            except Exception as exc:
                errors.append(f"{current_language}: {type(exc).__name__}")
                if current_language == language:
                    primary_failure_type = type(exc).__name__
        raise RuntimeError("; ".join(errors) or "no Wikimedia payload")

    def _fetch_feed(self, now, language):
        return self._get_json(FEED_URL.format(language=language, year=now.strftime("%Y"), month=now.strftime("%m"), day=now.strftime("%d")))

    def _payload_from_feed(self, feed, language, settings, now=None):
        feed = feed if isinstance(feed, dict) else {}
        article = feed.get("tfa") if isinstance(feed.get("tfa"), dict) else None
        article_source = "featured article"
        if not article:
            article = self._first_most_read_article(feed)
            article_source = "most read"
        if not article:
            raise RuntimeError("Wikimedia feed did not include an article")

        featured_image = feed.get("image") if isinstance(feed.get("image"), dict) else {}
        title = self._page_title(article)
        description = self._clean_text(self._text(article, "description"))
        extract = self._clean_text(self._text(article, "extract")) or description
        if description and extract and description.lower() not in extract.lower():
            extract = f"{description}. {extract}"
        date_page_events = []
        if now is not None and self._wants_simplified_chinese(language):
            try:
                date_page_events = self._fetch_zh_date_page_events(now)
            except Exception as exc:
                logger.warning("DailyWikiPage zh-cn date-page history enrichment failed: %s", exc)
        on_this_day_items = self._on_this_day_items(feed, settings, date_page_events=date_page_events)
        history_image = self._history_image_from_feed(feed, on_this_day_items) if on_this_day_items else {}
        featured_image_url = self._image_url(featured_image)
        article_image_url = self._image_url(article)
        if featured_image_url:
            image = {
                "image_url": featured_image_url,
                "image_caption": self._image_caption(featured_image) or self._page_title(featured_image) or description,
                "daily_image_title": self._page_title(featured_image),
                "image_credit": self._image_credit(featured_image),
                "image_source": "daily_image",
            }
        else:
            image = {
                "image_url": article_image_url,
                "image_caption": description,
                "daily_image_title": "",
                "image_credit": "",
                "image_source": "article_image" if article_image_url else "",
            }
        if self._wants_simplified_chinese(language):
            image.update(self._chinese_daily_image(featured_image, image, now))
        payload = {
            "schema": CACHE_SCHEMA_VERSION,
            "language": language,
            "source": "Wikimedia",
            "article_source": article_source,
            "title": title,
            "description": description,
            "extract": extract,
            "page_url": self._page_url(article),
            **image,
            "history_image_url": history_image.get("url") or "",
            "history_image_title": history_image.get("title") or "",
            "history_image_year": history_image.get("year") or "",
            "history_image_event_index": history_image.get("event_index"),
            "on_this_day": on_this_day_items,
            "most_read": [],
        }
        if self._wants_simplified_chinese(language):
            payload = self._apply_simplified_chinese_variant(payload, article)
        return payload

    def _chinese_daily_image(self, feed_image, image, now):
        """Image fields that keep the photograph caption Chinese on a Chinese page.

        Order: Chinese Wikipedia's own daily picture page (Chinese caption, normally
        the same Commons picture) -> a Chinese caption already in the feed -> a
        generic Chinese label. The English Commons description is never used.
        """
        if now is not None:
            try:
                picture = self._fetch_zh_daily_picture(now, feed_image)
                if picture:
                    return picture
            except TaskCancelled:
                raise
            except Exception as exc:
                logger.warning("DailyWikiPage zh daily picture unavailable: %s", exc)
        if image.get("image_source") == "daily_image":
            caption = zh_daily_picture.feed_image_chinese_caption(feed_image)
            return {"image_caption": caption or zh_daily_picture.GENERIC_ZH_IMAGE_CAPTION}
        if not zh_daily_picture.contains_cjk(image.get("image_caption")):
            # The renderer then captions the article image with the Chinese article title.
            return {"image_caption": ""}
        return {}

    def _fetch_zh_daily_picture(self, now, feed_image):
        data = self._get_json(
            ZH_ACTION_API_URL,
            params={
                "action": "query",
                "format": "json",
                "formatversion": "2",
                "prop": "revisions",
                "rvprop": "content",
                "rvslots": "main",
                "titles": zh_daily_picture.daily_picture_page_title(now),
            },
        )
        page = self._first_query_page(data)
        revisions = page.get("revisions") if isinstance(page.get("revisions"), list) else []
        revision = revisions[0] if revisions and isinstance(revisions[0], dict) else {}
        main_slot = (revision.get("slots") or {}).get("main") or {}
        file_name, content = zh_daily_picture.daily_picture_fields(main_slot.get("content"))
        if not file_name:
            return {}
        caption = self._zh_caption_text(content)
        if not zh_daily_picture.contains_cjk(caption):
            return {}
        feed_url = self._image_url(feed_image)
        if feed_url and zh_daily_picture.file_key(file_name) == zh_daily_picture.file_key(self._page_title(feed_image)):
            return {"image_caption": caption}
        # Chinese Wikipedia occasionally features a different picture; show that
        # picture so the caption describes what is on screen.
        info = self._fetch_zh_image_info(file_name)
        if not info:
            return {}
        return {**info, "image_caption": caption, "image_source": "daily_image"}

    def _zh_caption_text(self, wikitext):
        """Simplified-Chinese plain text for daily-picture caption wikitext."""
        wikitext = zh_daily_picture.without_references(wikitext)
        try:
            data = self._post_json(
                ZH_ACTION_API_URL,
                data={
                    "action": "parse",
                    "format": "json",
                    "formatversion": "2",
                    "contentmodel": "wikitext",
                    "prop": "text",
                    "text": wikitext,
                    "variant": ZH_SIMPLIFIED_VARIANT,
                    "disablelimitreport": "1",
                    "disableeditsection": "1",
                    "disabletoc": "1",
                },
            )
            html_text = data.get("parse", {}).get("text") if isinstance(data, dict) else ""
            html_text = re.sub(r"<(style|script)\b.*?</\1\s*>", "", str(html_text or ""),
                               flags=re.IGNORECASE | re.DOTALL)
            text = self._html_text(html_text)
        except TaskCancelled:
            raise
        except Exception as exc:
            logger.warning("DailyWikiPage zh daily picture caption parse failed: %s", exc)
            text = ""
        return text or self._to_simplified_cn(zh_daily_picture.strip_caption_markup(wikitext))

    def _fetch_zh_image_info(self, file_name):
        data = self._get_json(
            ZH_ACTION_API_URL,
            params={
                "action": "query",
                "format": "json",
                "formatversion": "2",
                "titles": f"File:{file_name}",
                "prop": "imageinfo",
                "iiprop": "url|extmetadata",
                "iiurlwidth": "960",
                "iiextmetadatafilter": "Artist",
                "redirects": "1",
            },
        )
        page = self._first_query_page(data)
        infos = page.get("imageinfo") if isinstance(page.get("imageinfo"), list) else []
        info = infos[0] if infos and isinstance(infos[0], dict) else {}
        url = info.get("thumburl") or info.get("url")
        if not url:
            return {}
        artist = ((info.get("extmetadata") or {}).get("Artist") or {}).get("value")
        return {
            "image_url": str(url),
            "daily_image_title": self._clean_text(page.get("title")) or f"File:{file_name}",
            "image_credit": self._clean_text(artist),
        }

    def _first_query_page(self, data):
        pages = data.get("query", {}).get("pages") if isinstance(data, dict) else None
        return pages[0] if isinstance(pages, list) and pages and isinstance(pages[0], dict) else {}

    def _render_page(self, dimensions, payload, settings, now):
        from plugins.daily_wiki_page.swiss_renderer import render_page

        return render_page(self, dimensions, payload, settings, now)


    def _download_image(self, image_url, target_size, settings):
        theme_only = self._enabled(settings.get("_theme_render_only"), default=False)
        path = self._media_cache_path(image_url, read_only=theme_only)
        cache_hours = self._int(settings.get("imageCacheHours"), DEFAULT_IMAGE_CACHE_HOURS, 1, MAX_IMAGE_CACHE_HOURS)

        def fetch():
            response = get_http_session().get(
                image_url, headers=IMAGE_HEADERS,
                timeout=(5, self._int(settings.get("imageTimeoutSeconds"), 12, 4, 30)), stream=True,
            )
            image = measured_image_response(
                response,
                limits=ImageLimits(max_bytes=self._int(settings.get("maxImageBytes"), 10_000_000, 1_000_000, 20_000_000)),
                draft_size=(target_size[0] * 3, target_size[1] * 3),
            ).convert("RGB")
            image.thumbnail((target_size[0] * 3, target_size[1] * 3), RESAMPLE)
            return image

        image = cached_resource_image(path, fetch, ttl=cache_hours * 3600,
                                      label="wiki_images", read_only=theme_only)
        if image is not None:
            image.thumbnail((target_size[0] * 3, target_size[1] * 3), RESAMPLE)
        if not theme_only:
            prune_resource_images(path.parent, prefixes=("",), max_files=256,
                                  max_bytes=50 * 1024 * 1024, max_age=30 * 24 * 3600,
                                  label="wiki_images", protected=(path,))
        return image

    def _media_cache_path(self, image_url, *, read_only=False):
        digest = hashlib.sha256(str(image_url).encode("utf-8")).hexdigest()
        if read_only:
            # Namespace registration can create directories and prune entries.
            # DISPLAY/PRESENTATION may only resolve the exact URL object path.
            path = (self._cache_dir(create=False) / "media" / f"{digest}.png").absolute()
            if any(part.is_symlink() for part in (path, *path.parents)):
                raise CachePathError("Daily Wiki read-only media cache must not traverse symlinks")
            return path
        return self._media_cache_namespace().path(digest, ".png")

    def _media_cache_namespace(self):
        return self.managed_cache_namespace(
            self._cache_dir() / "media",
            MEDIA_CACHE_BUDGET,
        )

    def _cached_media_is_fresh(self, path, settings):
        cache_hours = self._int(
            settings.get("imageCacheHours"),
            DEFAULT_IMAGE_CACHE_HOURS,
            1,
            MAX_IMAGE_CACHE_HOURS,
        )
        try:
            if path.is_symlink() or not path.is_file():
                return False
            return time.time() - path.stat(follow_symlinks=False).st_mtime < cache_hours * 60 * 60
        except OSError:
            return False

    def _open_cached_media(self, path):
        if path.is_symlink() or not path.is_file():
            return None
        try:
            cached = safe_open_image(path)
            try:
                return cached.convert("RGB")
            finally:
                cached.close()
        except Exception as exc:
            logger.warning("Could not read DailyWikiPage media cache %s: %s", path, exc)
            return None

    def _write_cached_media(self, path, image):
        try:
            output = BytesIO()
            image.save(output, format="PNG")
            self._media_cache_namespace().put_bytes(
                path.stem,
                output.getvalue(),
                suffix=path.suffix,
            )
        except Exception as exc:
            logger.warning("Could not write DailyWikiPage media cache %s: %s", path, exc)

    def _first_most_read_article(self, feed):
        mostread = feed.get("mostread") if isinstance(feed.get("mostread"), dict) else {}
        articles = mostread.get("articles") if isinstance(mostread.get("articles"), list) else []
        for article in articles:
            if not isinstance(article, dict):
                continue
            title = self._page_title(article).strip().lower()
            if title and title not in {"main page", "wikipedia"}:
                return article
        return None

    def _fetch_zh_date_page_events(self, now):
        page_title = f"{now.month}\u6708{now.day}\u65e5"
        data = self._get_json(
            ZH_DATE_PAGE_API_URL,
            params={
                "action": "parse",
                "format": "json",
                "formatversion": "2",
                "page": page_title,
                "prop": "text",
                "variant": ZH_SIMPLIFIED_VARIANT,
                "disablelimitreport": "1",
                "disableeditsection": "1",
            },
        )
        html_text = data.get("parse", {}).get("text") if isinstance(data, dict) else ""
        html_text = str(html_text or "")
        section_html = self._date_page_history_section(html_text)
        events = []
        for item_html in re.findall(r"<li\b[^>]*>(.*?)</li>", section_html, flags=re.IGNORECASE | re.DOTALL):
            item_text = self._html_text(item_html)
            match = re.match(r"^\s*(\d{1,4})\u5e74\s*[\uff1a:]\s*(.+)$", item_text)
            if match:
                events.append({"year": match.group(1), "text": self._clean_text(match.group(2))})
        return events

    def _date_page_history_section(self, html_text):
        start_match = re.search(r'id=["\']\u5927\u4e8b[\u8bb0\u8a18]["\']', html_text)
        if not start_match:
            return html_text
        end_match = re.search(r'id=["\']\u51fa\u751f["\']', html_text[start_match.end():])
        if not end_match:
            return html_text[start_match.end():]
        return html_text[start_match.end():start_match.end() + end_match.start()]

    def _html_text(self, html_fragment):
        parser = _HTMLTextExtractor()
        parser.feed(str(html_fragment or ""))
        parser.close()
        return self._clean_text(parser.text())

    def _on_this_day_items(self, feed, settings, date_page_events=None):
        if not self._enabled(settings.get("showOnThisDay"), default=True):
            return []
        events = feed.get("onthisday") if isinstance(feed.get("onthisday"), list) else []
        date_page_events = date_page_events or []
        items = []
        seen_event_signatures = set()
        for event in events:
            if not isinstance(event, dict):
                continue
            text = self._clean_text(self._text(event, "text"))
            year = self._clean_text(self._text(event, "year"))
            if text:
                page_titles = self._event_page_titles(event)
                event_signature = self._history_event_signature(year, text)
                if event_signature in seen_event_signatures:
                    continue
                enriched_text = self._match_date_page_event_text(year, text, page_titles, date_page_events)
                items.append({"year": year, "text": enriched_text or text})
                if event_signature is not None:
                    seen_event_signatures.add(event_signature)
            if len(items) >= 5:
                break
        return items

    def _history_event_signature(self, year, text):
        year = self._clean_text(year)
        normalized_text = self._clean_text(text).casefold()
        normalized_text = re.sub(
            r"[\(\uff08]\s*(?:(?:both\s+)?pictured|[图圖])\s*[\)\uff09]",
            " ",
            normalized_text,
            flags=re.IGNORECASE,
        )
        normalized_text = re.sub(r"[\W_]+", " ", normalized_text).strip()
        return (year, normalized_text) if year and normalized_text else None

    def _event_page_titles(self, event):
        pages = event.get("pages") if isinstance(event, dict) else []
        titles = []
        if not isinstance(pages, list):
            return titles
        for page in pages:
            if not isinstance(page, dict):
                continue
            title = self._page_title(page)
            if not title or re.fullmatch(r"\d+\u5e74", title):
                continue
            if title not in titles:
                titles.append(title)
        return titles

    def _match_date_page_event_text(self, year, feed_text, page_titles, date_page_events):
        year = self._clean_text(year)
        candidates = [item for item in date_page_events if self._clean_text(item.get("year")) == year]
        if not candidates:
            return ""
        feed_text = self._clean_text(feed_text)
        best_text = ""
        best_score = -1
        for candidate in candidates:
            candidate_text = self._clean_text(candidate.get("text"))
            if not candidate_text:
                continue
            score = 0
            if feed_text and feed_text in candidate_text:
                score += 100
            score += len(set(feed_text) & set(candidate_text))
            for title in page_titles:
                title = self._clean_text(title)
                if title and title in candidate_text:
                    score += 25
            if score > best_score:
                best_score = score
                best_text = candidate_text
        return best_text

    def _history_image_from_feed(self, feed, selected_items=None):
        events = feed.get("onthisday") if isinstance(feed.get("onthisday"), list) else []
        selected_items = [item for item in (selected_items or [])[:5] if isinstance(item, dict)]
        fallback = {}
        candidates = []
        for event_index, event in enumerate(events):
            if not isinstance(event, dict):
                continue
            selected_index = self._history_selected_event_index(event, selected_items)
            if selected_items and selected_index is None:
                continue
            rank = selected_index if selected_index is not None else event_index
            event_year = self._clean_text(event.get("year"))
            event_text = self._clean_text(self._text(event, "text"))
            if selected_index is not None and rank < len(selected_items):
                selected_text = self._clean_text(selected_items[rank].get("text"))
            else:
                selected_text = event_text
            pages = event.get("pages") if isinstance(event, dict) else []
            if not isinstance(pages, list):
                continue
            for page in pages:
                if not isinstance(page, dict):
                    continue
                title = self._page_title(page)
                if not title or re.fullmatch(r"\d+\u5e74", title):
                    continue
                url = self._image_url(page)
                if not url:
                    continue
                item = {"url": url, "title": title, "year": event_year,
                        "event_index": selected_index}
                if not fallback:
                    fallback = item
                marker = f"{title} {url}".lower()
                symbolic = any(token in marker for token in ("flag", "emblem", "seal", "logo", ".svg"))
                score = 1000 - (rank * 100) - (35 if symbolic else 0)
                clean_title = self._clean_text(title)
                if clean_title and selected_text and clean_title in selected_text:
                    score += 25
                candidates.append((score, item))
        if not candidates:
            return fallback
        candidates.sort(key=lambda pair: pair[0], reverse=True)
        return candidates[0][1]

    def _history_selected_event_index(self, event, selected_items):
        if not selected_items:
            return None
        event_year = self._clean_text(event.get("year"))
        event_text = self._clean_text(self._text(event, "text"))
        best_index = None
        best_score = -1
        for index, item in enumerate(selected_items):
            item_year = self._clean_text(item.get("year"))
            if event_year and item_year and event_year != item_year:
                continue
            item_text = self._clean_text(item.get("text"))
            score = 0
            if event_year and item_year == event_year:
                score += 10
            if event_text and item_text:
                if event_text in item_text or item_text in event_text:
                    score += 100
                score += len(set(event_text) & set(item_text))
            if score > best_score:
                best_score = score
                best_index = index
        return best_index if best_score >= 10 else None

    def _most_read_items(self, feed, title, settings):
        if not self._enabled(settings.get("showMostRead"), default=True):
            return []
        mostread = feed.get("mostread") if isinstance(feed.get("mostread"), dict) else {}
        articles = mostread.get("articles") if isinstance(mostread.get("articles"), list) else []
        items = []
        title_key = self._clean_text(title).lower()
        for article in articles:
            if not isinstance(article, dict):
                continue
            item_title = self._page_title(article)
            if item_title and item_title.lower() != title_key:
                views = article.get("views")
                items.append({"title": item_title, "views": views if isinstance(views, int) else None})
            if len(items) >= 4:
                break
        return items

    def _local_fallback_payload(self, language, date_key):
        candidates = [item for item in LOCAL_FALLBACK_PAGES if item["language"] == language] or list(LOCAL_FALLBACK_PAGES)
        digest = hashlib.sha1(f"{date_key}|{language}|daily-wiki".encode("utf-8")).hexdigest()
        item = candidates[int(digest[:8], 16) % len(candidates)]
        return {
            "schema": CACHE_SCHEMA_VERSION,
            "date": date_key,
            "language": item.get("language") or language,
            "source": "Local Encyclopedia",
            "article_source": "local fallback",
            "title": item.get("title") or "Daily Wiki Page",
            "description": item.get("description") or "",
            "extract": item.get("extract") or "",
            "page_url": item.get("page_url") or "",
            "image_url": "",
            "image_caption": "",
            "image_credit": "",
            "image_source": "",
            "on_this_day": [],
            "most_read": [],
        }

    def _apply_simplified_chinese_variant(self, payload, article):
        payload = dict(payload)
        payload["language"] = ZH_SIMPLIFIED_VARIANT
        payload["description"] = self._to_simplified_cn(payload.get("description"))
        payload["image_caption"] = self._to_simplified_cn(payload.get("image_caption"))
        payload["on_this_day"] = [
            {**item, "text": self._to_simplified_cn(item.get("text"))}
            for item in payload.get("on_this_day", [])
            if isinstance(item, dict)
        ]
        payload["most_read"] = [
            {**item, "title": self._to_simplified_cn(item.get("title"))}
            for item in payload.get("most_read", [])
            if isinstance(item, dict)
        ]
        try:
            payload = self._convert_payload_short_texts(payload)
        except Exception as exc:
            logger.warning("DailyWikiPage zh-cn short text conversion failed: %s", exc)

        try:
            page = self._fetch_zh_cn_page(article)
            display_title = self._fetch_zh_cn_display_title(article, page)
            title = display_title or page.get("title") or payload.get("title")
            extract = self._clean_text(page.get("extract") or "")
            thumbnail = page.get("thumbnail") if isinstance(page.get("thumbnail"), dict) else {}
            if title:
                payload["title"] = self._to_simplified_cn(title)
            if extract:
                payload["extract"] = extract
            else:
                payload["extract"] = self._to_simplified_cn(payload.get("extract"))
            if thumbnail.get("source") and payload.get("image_source") != "daily_image":
                payload["image_url"] = str(thumbnail["source"])
                payload["image_source"] = "article_image"
            if payload.get("title"):
                payload["page_url"] = "https://zh.wikipedia.org/zh-cn/" + quote(str(payload["title"]).replace(" ", "_"))
            elif page.get("fullurl"):
                payload["page_url"] = str(page["fullurl"])
        except Exception as exc:
            logger.warning("DailyWikiPage zh-cn enrichment failed: %s", exc)
            payload["title"] = self._to_simplified_cn(payload.get("title"))
            payload["extract"] = self._to_simplified_cn(payload.get("extract"))
        return payload

    def _convert_payload_short_texts(self, payload):
        payload = dict(payload)
        events = [item for item in payload.get("on_this_day", []) if isinstance(item, dict)]
        values = [payload.get("description"), payload.get("image_caption"), payload.get("daily_image_title")]
        values.extend(item.get("text") for item in events)
        converted = self._convert_zh_cn_texts(values)
        if len(converted) != len(values):
            return payload
        payload["description"] = converted[0]
        payload["image_caption"] = converted[1]
        payload["daily_image_title"] = converted[2]
        event_texts = converted[3:3 + len(events)]
        for item, text in zip(events, event_texts):
            item["text"] = text
            item.pop("topics", None)
            item.pop("topics_text", None)
        payload["on_this_day"] = events
        return payload

    def _convert_zh_cn_texts(self, values):
        cleaned = [self._clean_text(value) for value in values]
        if not any(cleaned):
            return cleaned
        sentinel = "INKYPI_DAILY_WIKI_SPLIT_6F9A"
        data = self._post_json(
            ZH_ACTION_API_URL,
            data={
                "action": "parse",
                "format": "json",
                "formatversion": "2",
                "contentmodel": "wikitext",
                "prop": "text",
                "text": f"\n{sentinel}\n".join(cleaned),
                "variant": ZH_SIMPLIFIED_VARIANT,
                "disablelimitreport": "1",
                "disableeditsection": "1",
                "disabletoc": "1",
            },
        )
        html_text = data.get("parse", {}).get("text") if isinstance(data, dict) else ""
        converted = self._clean_text(html_text)
        parts = [part.strip() for part in converted.split(sentinel)]
        if len(parts) != len(cleaned):
            return [self._to_simplified_cn(value) for value in cleaned]
        return parts
    def _fetch_zh_cn_page(self, article):
        params = {
            "action": "query",
            "format": "json",
            "formatversion": "2",
            "prop": "extracts|pageimages|info",
            "inprop": "url",
            "exintro": "1",
            "explaintext": "1",
            "pithumbsize": "1200",
            "redirects": "1",
            "variant": ZH_SIMPLIFIED_VARIANT,
        }
        params.update(self._article_lookup_params(article))
        data = self._get_json(ZH_ACTION_API_URL, params=params)
        pages = data.get("query", {}).get("pages") if isinstance(data, dict) else None
        if not isinstance(pages, list) or not pages:
            raise RuntimeError("zh-cn query returned no pages")
        page = pages[0]
        if page.get("missing"):
            raise RuntimeError("zh-cn query page is missing")
        return page

    def _fetch_zh_cn_display_title(self, article, page):
        params = {
            "action": "parse",
            "format": "json",
            "formatversion": "2",
            "prop": "displaytitle",
            "variant": ZH_SIMPLIFIED_VARIANT,
        }
        pageid = page.get("pageid") if isinstance(page, dict) else None
        if pageid:
            params["pageid"] = str(pageid)
        else:
            params.update(self._article_lookup_params(article))
        data = self._get_json(ZH_ACTION_API_URL, params=params)
        display_title = data.get("parse", {}).get("displaytitle") if isinstance(data, dict) else ""
        return self._clean_text(display_title)

    def _article_lookup_params(self, article):
        pageid = article.get("pageid") if isinstance(article, dict) else None
        if pageid:
            return {"pageids": str(pageid)}
        title = self._page_title(article)
        if title:
            return {"titles": title}
        raise RuntimeError("article has no pageid or title")

    def _get_json(self, url, params=None):
        response = get_http_session().get(url, params=params, headers=REQUEST_HEADERS, timeout=(5, 12))
        response.raise_for_status()
        try:
            return response.json()
        except ValueError as exc:
            raise RuntimeError(f"response from {url} was not JSON") from exc
    def _post_json(self, url, data=None):
        response = get_http_session().post(url, data=data, headers=REQUEST_HEADERS, timeout=(5, 12))
        response.raise_for_status()
        try:
            return response.json()
        except ValueError as exc:
            raise RuntimeError(f"response from {url} was not JSON") from exc
    def _write_context(self, payload, now):
        try:
            write_context(
                PLUGIN_ID,
                {
                    "kind": "daily_wiki_page",
                    "source": payload.get("source") or "Wikimedia",
                    "title": payload.get("title"),
                    "summary": self._clean_text(payload.get("image_caption") or payload.get("daily_image_title") or "")[:260],
                    "language": payload.get("language"),
                    "page_url": payload.get("page_url"),
                    "source_state": payload.get("source_state"),
                    "source_provenance": payload.get("_source_provenance"),
                    "on_this_day": payload.get("on_this_day") or [],
                },
                generated_at=now,
                ttl_seconds=30 * 60 * 60,
            )
        except Exception as exc:
            logger.warning("Could not write DailyWikiPage context: %s", exc)

    def _image_url(self, item):
        if not isinstance(item, dict):
            return ""
        for key in ("thumbnail", "originalimage"):
            nested = item.get(key)
            if isinstance(nested, dict) and nested.get("source"):
                return str(nested["source"])
        return ""

    def _image_caption(self, item):
        if not isinstance(item, dict):
            return ""
        for key in ("description", "caption"):
            value = item.get(key)
            if isinstance(value, dict):
                value = value.get("text") or value.get("html")
            text = self._clean_text(value)
            if text:
                return text
        return ""

    def _image_credit(self, item):
        value = item.get("artist") if isinstance(item, dict) else None
        if isinstance(value, dict):
            value = value.get("text") or value.get("html")
        return self._clean_text(value)

    def _page_title(self, item):
        if not isinstance(item, dict):
            return ""
        titles = item.get("titles")
        if isinstance(titles, dict):
            title = titles.get("normalized") or titles.get("display")
            if title:
                return self._clean_text(title)
        return self._clean_text(item.get("normalizedtitle") or item.get("title"))

    def _page_url(self, item):
        urls = item.get("content_urls") if isinstance(item, dict) else None
        if isinstance(urls, dict):
            for channel in ("desktop", "mobile"):
                nested = urls.get(channel)
                if isinstance(nested, dict) and nested.get("page"):
                    return str(nested["page"])
        return str(item.get("url") or "") if isinstance(item, dict) else ""

    def _text(self, data, key):
        if not isinstance(data, dict):
            return ""
        value = data.get(key)
        if isinstance(value, str):
            return value
        if value is None:
            return ""
        return str(value)

    def _event_line(self, item):
        year = self._clean_text(item.get("year") if isinstance(item, dict) else "")
        text = self._clean_text(item.get("text") if isinstance(item, dict) else str(item))
        return f"{year} - {text}" if year else text

    def _source_label(self, payload):
        parts = [payload.get("source") or "Wikimedia", payload.get("language"), payload.get("source_state")]
        return " / ".join(str(part).upper() for part in parts if part)

    def _palette(self, settings):
        theme = settings.get("_inkypi_theme") or self.resolve_theme(settings, None)
        night = theme.get("mode") == "night"
        return {
            "background": (0, 0, 0) if night else (255, 255, 255),
            "panel": (0, 0, 0) if night else (255, 255, 255),
            "ink": (255, 255, 255) if night else (0, 0, 0),
            "dim": (210, 210, 210) if night else (48, 48, 48),
            "muted": (210, 210, 210) if night else (48, 48, 48),
            "accent": (92, 158, 255) if night else (0, 82, 255),
            "rule": (255, 255, 255) if night else (0, 0, 0),
        }

    def _cache_key(self, date_key, settings, language, fallback_language):
        parts = [CACHE_SCHEMA_VERSION, date_key, language, fallback_language or "", str(self._enabled(settings.get("showImage"), True)), str(self._enabled(settings.get("showOnThisDay"), True))]
        return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()

    def _cache_dir(self, create=True):
        return self.cache_dir(
            env_var="INKYPI_DAILY_WIKI_PAGE_CACHE",
            leaf="cache",
            create=create,
            strip=True,
        )

    def _cache_path(self, create=True):
        return self._cache_dir(create=create) / "daily.json"

    def _read_cache(self, create=True):
        try:
            path = self._cache_path(create=create)
            if not path.is_file():
                return {}
            payload = json.loads(path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else {}
        except Exception as exc:
            logger.warning("Could not read DailyWikiPage cache: %s", exc)
            return {}

    def _write_cache(self, payload):
        path = self._cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            tmp.replace(path)
        except PermissionError:
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            try:
                tmp.unlink()
            except Exception:
                pass

    def _language(self, settings):
        language = str(settings.get("language") or ZH_SIMPLIFIED_VARIANT).strip().lower()
        language = re.sub(r"[^a-z-]", "", language) or ZH_SIMPLIFIED_VARIANT
        if language in {"zh", "zh-hans", "zh-cn", "zh-sg", "zh-my"}:
            return ZH_SIMPLIFIED_VARIANT
        return language

    def _fallback_language(self, settings, language):
        fallback_value = settings.get("fallbackLanguage", "en")
        fallback = str(fallback_value).strip().lower()
        fallback = re.sub(r"[^a-z-]", "", fallback)
        if fallback in {"zh", "zh-hans", "zh-cn", "zh-sg", "zh-my"}:
            fallback = ZH_SIMPLIFIED_VARIANT
        return "" if fallback == language else fallback

    def _feed_language(self, language):
        return "zh" if self._wants_simplified_chinese(language) else language

    def _wants_simplified_chinese(self, language):
        return str(language or "").lower() in {"zh", "zh-cn", "zh-hans", "zh-sg", "zh-my"}
    def _enabled(self, value, default=False):
        return coerce_bool(value, default=default, truthy=("1", "true", "yes", "on"))

    def _int(self, value, default, minimum, maximum):
        try:
            number = int(value)
        except (TypeError, ValueError):
            number = default
        return max(minimum, min(maximum, number))

    def _to_simplified_cn(self, value):
        return self._clean_text(value).translate(TRADITIONAL_TO_SIMPLIFIED)

    def _clean_text(self, value):
        value = html.unescape(str(value or ""))
        value = re.sub(r"<[^>]+>", " ", value)
        value = value.replace("\u201c", '"').replace("\u201d", '"')
        value = value.replace("\u2018", "'").replace("\u2019", "'")
        value = value.replace("\u2014", "-").replace("\u2013", "-").replace("\u2026", "...")
        value = re.sub(r"([\u3002\uff01\uff1f])\1+", r"\1", value)
        return re.sub(r"\s+", " ", value).strip()

    def _fit_lines(self, draw, text, font, max_width, max_height, max_lines=6):
        text = self._clean_text(text)
        size = getattr(font, "size", 20) or 20
        family = "__cjk__" if self._contains_cjk(text) else getattr(font, "family", None) or DEFAULT_FONT
        for candidate_size in range(size, 11, -2):
            candidate = self._font(family, candidate_size)
            lines = self._wrap(draw, text, candidate, max_width, max_lines=max_lines)
            line_h = int(self._text_height(draw, "Ag", candidate) * 1.24)
            if lines and len(lines) * line_h <= max_height:
                return lines, candidate
        candidate = self._font(family, 12)
        return self._wrap(draw, text, candidate, max_width, max_lines=max_lines), candidate

    def _wrap(self, draw, text, font, max_width, max_lines=6):
        text = self._clean_text(text)
        if not text:
            return []
        return self._wrap_chars(draw, text, font, max_width, max_lines) if self._contains_cjk(text) else self._wrap_words(draw, text, font, max_width, max_lines)

    def _wrap_all(self, draw, text, font, max_width):
        text = self._clean_text(text)
        if not text:
            return []
        max_lines = max(1, len(text))
        return self._wrap_chars(draw, text, font, max_width, max_lines) if self._contains_cjk(text) else self._wrap_words(draw, text, font, max_width, max_lines)

    def _wrap_words(self, draw, text, font, max_width, max_lines):
        lines, current = [], ""
        words = text.split()
        consumed = 0
        for word in words:
            consumed += 1
            candidate = word if not current else f"{current} {word}"
            if self._text_width(draw, candidate, font) <= max_width or not current:
                current = candidate
            else:
                lines.append(current)
                current = word
            if len(lines) >= max_lines:
                break
        if current and len(lines) < max_lines:
            lines.append(current)
        if consumed < len(words) and lines:
            lines[-1] = self._ellipsize(draw, lines[-1], font, max_width)
        return lines

    def _wrap_chars(self, draw, text, font, max_width, max_lines):
        lines, current = [], ""
        consumed = 0
        for char in text:
            consumed += 1
            candidate = current + char
            if self._text_width(draw, candidate, font) <= max_width or not current:
                current = candidate
            else:
                lines.append(current)
                current = char
            if len(lines) >= max_lines:
                break
        if current and len(lines) < max_lines:
            lines.append(current)
        if consumed < len(text) and lines:
            lines[-1] = self._ellipsize(draw, lines[-1], font, max_width)
        return lines

    def _ellipsize(self, draw, text, font, max_width):
        suffix = "..."
        text = str(text or "")
        while text and self._text_width(draw, text + suffix, font) > max_width:
            text = text[:-1]
        return text + suffix if text else suffix

    def _font(self, font_family, size, weight="normal"):
        if font_family == "__cjk__":
            return get_base_ui_font(int(size), bold=weight == "bold")
        try:
            font = get_font(font_family or DEFAULT_FONT, size, weight)
            if font:
                return font
        except Exception:
            pass
        cjk = self._cjk_font_path()
        if cjk:
            try:
                return ImageFont.truetype(str(cjk), size)
            except OSError:
                pass
        return ImageFont.load_default()

    def _resolved_font_family(self, settings):
        font_family = str((settings or {}).get("fontFamily") or "").strip()
        return font_family or DEFAULT_FONT

    def _font_for_text(self, text, fallback_font):
        if not self._contains_cjk(text):
            return fallback_font
        return self._font("__cjk__", getattr(fallback_font, "size", 14) or 14)

    def _cjk_font_path(self):
        plugin_root = Path(self.get_plugin_dir()).parent
        for relative in (
            "../static/fonts/msyh.ttf",
            "../static/fonts/msyh.ttc",
            "../static/fonts/NotoSansSC-VF.ttf",
            "../static/fonts/LXGWWenKai-Regular.ttf",
            "chinese_literature_clock/fonts/FandolKai-Regular.otf",
            "chinese_literature_clock/fonts/I.Ming-8.10.ttf",
        ):
            path = plugin_root / relative
            if path.is_file():
                return path
        return None

    def _contains_cjk(self, text):
        return any("\u3400" <= char <= "\u9fff" for char in str(text or ""))

    def _language_is_cjk(self, language):
        return str(language or "").lower().startswith(("zh", "ja"))

    def _text_width(self, draw, text, font):
        return text_width(draw, str(text), font)

    def _text_height(self, draw, text, font):
        bbox = draw.textbbox((0, 0), str(text), font=font)
        return bbox[3] - bbox[1]
