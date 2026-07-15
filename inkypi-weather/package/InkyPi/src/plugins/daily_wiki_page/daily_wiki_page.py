from __future__ import annotations

import hashlib
import html
import json
import logging
import re
import time
from datetime import datetime
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageEnhance, ImageFont, ImageOps

from plugins.base_plugin.base_plugin import BasePlugin
from plugins.base_plugin.presentation import PresentationMode
from plugins.base_plugin.render_provenance import (
    SourceProvenance,
    attach_source_provenance,
)
from plugins.context_cache import write_context
from utils.app_utils import DEFAULT_FONT_FAMILY, coerce_bool, get_available_font_names, get_base_ui_font, get_font
from utils.cache_manager import CacheBudget
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
PLUGIN_DIR = Path(__file__).resolve().parent
DAILY_IMAGE_TITLE_PATH = PLUGIN_DIR / "assets" / "daily_image_title.png"
DAILY_HEADER_FILLER_PATH = PLUGIN_DIR / "assets" / "daily_header_pixel_filler.png"
HISTORY_TITLE_WORDMARK_PATH = PLUGIN_DIR / "assets" / "history_title_wordmark.png"
TOPIC_PLACEHOLDER_PATH = PLUGIN_DIR / "assets" / "topic_pixel_placeholder.png"
CACHE_SCHEMA_VERSION = "daily-wiki-page-v6"
DEFAULT_FONT = DEFAULT_FONT_FAMILY
DEFAULT_TIMEZONE = "America/Los_Angeles"
FEED_URL = "https://api.wikimedia.org/feed/v1/wikipedia/{language}/featured/{year}/{month}/{day}"
ZH_ACTION_API_URL = "https://zh.wikipedia.org/w/api.php"
ZH_SIMPLIFIED_VARIANT = "zh-cn"
ZH_DATE_PAGE_API_URL = "https://zh.wikipedia.org/w/api.php"
REQUEST_HEADERS = {"User-Agent": "InkyPi DailyWikiPage/1.0", "Accept": "application/json,*/*;q=0.8"}
IMAGE_HEADERS = {"User-Agent": "InkyPi DailyWikiPage/1.0", "Accept": "image/jpeg,image/png,image/webp,image/*;q=0.8"}
RESAMPLE = getattr(Image, "Resampling", Image).LANCZOS
PIXEL_RESAMPLE = getattr(Image, "Resampling", Image).NEAREST
YEAR_LABEL_Y_OFFSET = -10
HISTORY_TITLE_Y_OFFSET = 0
HISTORY_TITLE_RULE_GAP = 12
HISTORY_BODY_Y_OFFSET = 8
HISTORY_LINE_SPACING = 1.08
HISTORY_MIN_EVENT_FONT_SIZE = 15
HISTORY_IMAGE_WIDTH = 104
HISTORY_IMAGE_HEIGHT = 68
HISTORY_IMAGE_GAP = 10
HISTORY_FLOAT_MIN_TEXT_WIDTH = 118
HISTORY_TEXT_INDENT = 17
HISTORY_TOPIC_PLACEHOLDER_TOP_OFFSET = 20
DAILY_CAPTION_GAP = 4
DAILY_CAPTION_LINE_SPACING = 1.12
EPAPER_RULE_WIDTH = 2
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
        if isinstance(cached, dict):
            payload = dict(cached)
            payload["source_state"] = "cache"
            return payload
        payload = self._local_fallback_payload(language, date_key)
        payload["source_state"] = "local"
        payload["cache_key"] = cache_key
        return payload

    def _fetch_live_payload(self, now, language, fallback_language, settings):
        errors = []
        languages = [language]
        if fallback_language and fallback_language not in languages:
            languages.append(fallback_language)
        for current_language in languages:
            try:
                feed = self._fetch_feed(now, self._feed_language(current_language))
                payload = self._payload_from_feed(feed, current_language, settings, now=now)
                if payload.get("title") and payload.get("extract"):
                    return payload
                errors.append(f"{current_language}: empty article")
            except Exception as exc:
                errors.append(f"{current_language}: {exc}")
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
            image_url = featured_image_url
            image_caption = self._image_caption(featured_image) or self._page_title(featured_image) or description
            daily_image_title = self._page_title(featured_image)
            image_credit = self._image_credit(featured_image)
            image_source = "daily_image"
        else:
            image_url = article_image_url
            image_caption = description
            daily_image_title = ""
            image_credit = ""
            image_source = "article_image" if article_image_url else ""
        payload = {
            "schema": CACHE_SCHEMA_VERSION,
            "language": language,
            "source": "Wikimedia",
            "article_source": article_source,
            "title": title,
            "description": description,
            "extract": extract,
            "page_url": self._page_url(article),
            "image_url": image_url,
            "image_caption": image_caption,
            "daily_image_title": daily_image_title,
            "image_credit": image_credit,
            "image_source": image_source,
            "history_image_url": history_image.get("url") or "",
            "history_image_title": history_image.get("title") or "",
            "history_image_year": history_image.get("year") or "",
            "on_this_day": on_this_day_items,
            "most_read": [],
        }
        if self._wants_simplified_chinese(language):
            payload = self._apply_simplified_chinese_variant(payload, article)
        return payload
    def _render_page(self, dimensions, payload, settings, now):
        width, height = dimensions
        palette = self._palette(settings)
        image = Image.new("RGB", dimensions, palette["background"])
        draw = ImageDraw.Draw(image)
        margin = max(20, min(width, height) // 18)
        gap = max(16, width // 50)
        header_h = max(46, height // 10)
        footer_h = max(18, height // 32)

        font_family = self._resolved_font_family(settings)
        cjk = self._language_is_cjk(payload.get("language"))
        label_font = self._font(font_family, max(22, min(26, width // 30)), "bold")
        date_font = self._font(font_family, max(11, width // 72))
        caption_font = self._font(font_family, max(17, min(19, width // 42 - 1)))
        event_title_font = self._font(font_family, max(23, min(30, width // 27)), "bold")
        year_font = self._font(font_family, max(20, min(23, width // 34)), "bold")
        event_font = self._font(font_family, max(19, min(21, width // 40)))
        small_font = self._font(font_family, max(10, width // 80))

        header = "\u6bcf\u65e5\u56fe\u7247" if cjk else "DAILY IMAGE"
        date_text = now.strftime("%Y.%m.%d")
        date_x = width - margin - self._text_width(draw, date_text, date_font)
        rule_y = margin + header_h - 10
        title_asset = self._load_daily_image_title() if cjk else None
        title_drawn = False
        title_y = margin - 4
        title_right = margin + self._text_width(draw, header, label_font)
        if title_asset is not None:
            title_y = max(2, margin - max(21, height // 23))
            title_max_h = max(28, min(58, rule_y - title_y - 2))
            title_max_w = min(max(190, int(width * 0.34)), max(1, width - margin * 2 - self._text_width(draw, date_text, date_font) - gap))
            if title_max_w > 0 and title_max_h > 0:
                title_w, _title_h = self._daily_image_title_fitted_size(title_asset, title_max_w, title_max_h)
                self._draw_daily_image_title(image, title_asset, margin, title_y, title_max_w, title_max_h, rule_y=rule_y)
                title_right = margin + title_w
                title_drawn = True
        if not title_drawn:
            draw.text((margin, margin - 4), header, font=self._font_for_text(header, label_font), fill=palette["accent"])
        if cjk and title_drawn:
            header_filler = self._load_daily_header_filler()
            if header_filler is not None:
                filler_left = int(title_right + max(24, gap + 8))
                filler_right = int(date_x - max(22, gap + 6))
                self._draw_daily_header_filler(image, header_filler, filler_left, filler_right, title_y, rule_y)
        draw.text((date_x, margin - 2), date_text, font=date_font, fill=palette["muted"])
        draw.line((margin, rule_y, width - margin, rule_y), fill=palette["rule"], width=EPAPER_RULE_WIDTH)

        content_y = rule_y + max(12, height // 42)
        content_bottom = height - margin - footer_h
        usable_w = width - margin * 2 - gap
        left_w = max(350, min(430, int(usable_w * 0.54)))
        right_x = margin + left_w + gap
        right_w = width - margin - right_x
        right_content_bottom = height - margin
        caption_gap = DAILY_CAPTION_GAP
        caption = self._clean_text(payload.get("image_caption") or payload.get("daily_image_title") or payload.get("title") or "")
        caption_draw_font = self._font_for_text(caption, caption_font)
        caption_line_h = max(1, int(self._text_height(draw, "Ag", caption_draw_font) * DAILY_CAPTION_LINE_SPACING))
        caption_all_lines = self._wrap_all(draw, caption, caption_draw_font, left_w) if caption else []
        available_left_h = max(1, content_bottom - content_y)
        min_image_h = min(max(120, int(height * 0.34)), max(1, available_left_h - caption_gap - caption_line_h))
        preferred_image_h = min(max(210, int(height * 0.61)), available_left_h)
        max_caption_lines_with_min_image = max(0, (available_left_h - min_image_h - caption_gap) // caption_line_h)
        caption_line_count = min(len(caption_all_lines), max_caption_lines_with_min_image)
        caption_budget = caption_gap + caption_line_count * caption_line_h + 2 if caption_line_count else 0
        image_h = min(preferred_image_h, max(1, available_left_h - caption_budget))
        image_h = max(min_image_h, image_h)

        article_image = None
        if self._enabled(settings.get("showImage"), default=True) and payload.get("image_url"):
            article_image = self._download_image(payload.get("image_url"), (left_w, image_h), settings)
        if article_image:
            self._draw_article_image(draw, image, article_image, palette, margin, content_y, left_w, image_h)
        else:
            self._draw_placeholder(draw, margin, content_y, left_w, image_h, palette)

        history_image = None
        if self._enabled(settings.get("showOnThisDay"), default=True) and payload.get("history_image_url"):
            history_target = self._history_image_download_size(right_w, right_content_bottom - content_y)
            history_image = self._download_image(payload.get("history_image_url"), history_target, settings)

        caption_y = content_y + image_h + caption_gap
        max_caption_lines = max(0, (content_bottom - caption_y) // caption_line_h)
        caption_lines = caption_all_lines[:max_caption_lines]
        if len(caption_lines) < len(caption_all_lines) and caption_lines:
            caption_lines[-1] = self._ellipsize(draw, caption_lines[-1], caption_draw_font, left_w)
        for line in caption_lines:
            if caption_y + self._text_height(draw, line, caption_draw_font) > content_bottom:
                break
            draw.text((margin, caption_y), line, font=self._font_for_text(line, caption_font), fill=palette["ink"])
            caption_y += caption_line_h

        self._draw_on_this_day_panel(
            draw,
            payload.get("on_this_day") or [],
            palette,
            right_x,
            content_y,
            right_w,
            right_content_bottom - content_y,
            event_title_font,
            year_font,
            event_font,
            small_font,
            cjk,
            date_key=payload.get("date") or now.strftime("%Y-%m-%d"),
            history_image=history_image,
            target_image=image,
        )

        source_label = self._source_label(payload)
        footer_y = height - margin - self._text_height(draw, source_label, small_font)
        draw.text((margin, footer_y), source_label, font=self._font_for_text(source_label, small_font), fill=palette["muted"])
        return image

    def _draw_on_this_day_panel(self, draw, events, palette, x, y, width, height, title_font, year_font, event_font, small_font, cjk, date_key=None, history_image=None, target_image=None):
        title = "\u5386\u53f2\u4e0a\u7684\u4eca\u5929" if cjk else "ON THIS DAY"
        title_y = y + HISTORY_TITLE_Y_OFFSET
        title_draw_font = self._font_for_text(title, title_font)
        title_h = 0
        title_drawn = False
        if cjk and target_image is not None and width >= 260:
            title_asset = self._load_history_title_wordmark()
            if title_asset is not None:
                text_h = self._text_height(draw, title, title_draw_font)
                title_max_h = max(28, min(38, int(text_h * 1.16)))
                title_max_w = min(width, max(180, int(width * 0.68)))
                if title_max_w > 0 and title_max_h > 0:
                    title_result = self._draw_history_title_wordmark(target_image, title_asset, x, title_y - 2, title_max_w, title_max_h)
                    if isinstance(title_result, tuple):
                        title_h, wordmark_rule_y = title_result
                    else:
                        title_h = title_result
                        wordmark_rule_y = None
                    title_drawn = True
        if not title_drawn:
            draw.text((x, title_y), title, font=title_draw_font, fill=palette["ink"])
            title_h = self._text_height(draw, title, title_draw_font)
            wordmark_rule_y = None
        line_y = int(wordmark_rule_y) if wordmark_rule_y is not None else title_y + title_h + HISTORY_TITLE_RULE_GAP
        draw.line((x, line_y, x + width, line_y), fill=palette["rule"], width=EPAPER_RULE_WIDTH)
        usable_bottom = y + height
        current_y = line_y + max(9, height // 42) + HISTORY_BODY_Y_OFFSET
        available_h = max(0, usable_bottom - current_y)
        if not events:
            empty = "\u4eca\u5929\u6682\u65e0\u5386\u53f2\u4e8b\u4ef6" if cjk else "No history notes today"
            for line in self._wrap(draw, empty, self._font_for_text(empty, event_font), width, max_lines=3):
                if current_y + self._text_height(draw, line, event_font) > usable_bottom:
                    break
                draw.text((x, current_y), line, font=self._font_for_text(line, event_font), fill=palette["ink"])
                current_y += int(self._text_height(draw, "Ag", event_font) * 1.18)
            return

        text_width_px = max(90, width - HISTORY_TEXT_INDENT)
        float_image_box = None
        float_width_px = 0
        float_height_px = 0
        rows = []
        if history_image and target_image is not None:
            float_image_box = self._history_image_float_box(x, current_y, width, available_h, text_width_px, history_image)
            if float_image_box is not None:
                _image_x, _image_y, image_w, image_h = float_image_box
                float_width_px = image_w + HISTORY_IMAGE_GAP
                float_height_px = image_h + HISTORY_IMAGE_GAP
                event_font, rows = self._fit_history_event_rows(
                    draw,
                    events,
                    text_width_px,
                    available_h,
                    year_font,
                    event_font,
                    date_key=date_key,
                    cjk=cjk,
                    float_width_px=float_width_px,
                    float_height_px=float_height_px,
                )
        if not rows:
            event_font, rows = self._fit_history_event_rows(
                draw,
                events,
                text_width_px,
                available_h,
                year_font,
                event_font,
                date_key=date_key,
                cjk=cjk,
            )

        for row in rows:
            if current_y + row["height"] > usable_bottom:
                break
            marker_x = x
            text_x = x + HISTORY_TEXT_INDENT
            text_y = current_y + row.get("text_offset_y", 0)
            marker_y = text_y + max(6, min(14, row["line_h"] // 2))
            draw.ellipse((marker_x, marker_y, marker_x + 7, marker_y + 7), fill=palette["accent"])
            date_label = row.get("date_label") or ""
            for line_index, body_line in enumerate(row["body_lines"]):
                body_font = self._font_for_text(body_line, event_font)
                draw.text((text_x, text_y), body_line, font=body_font, fill=palette["ink"])
                if line_index == 0 and date_label and body_line.startswith(date_label):
                    date_font = self._font_for_text(date_label, body_font)
                    draw.text((text_x, text_y), date_label, font=date_font, fill=palette["accent"])
                text_y += row["line_h"]
            current_y += row["height"]

        if target_image is not None and history_image and float_image_box is not None:
            image_area_x, image_area_y, image_area_w, image_area_h = float_image_box
            self._draw_history_image(target_image, history_image, image_area_x, image_area_y, image_area_w, image_area_h)
            return

        image_area_x = x + HISTORY_TEXT_INDENT
        image_area_y = current_y + HISTORY_TOPIC_PLACEHOLDER_TOP_OFFSET
        image_area_w = max(1, text_width_px)
        image_area_h = usable_bottom - image_area_y
        if target_image is not None and image_area_h >= 28:
            placeholder = self._load_topic_placeholder()
            if placeholder is not None:
                self._draw_topic_placeholder(target_image, placeholder, image_area_x, image_area_y, image_area_w, image_area_h)

    def _fit_history_event_rows(self, draw, events, text_width_px, available_h, year_font, event_font, date_key=None, cjk=False, float_width_px=0, float_rows=0, float_height_px=0):
        target_count = len([item for item in events[:5] if isinstance(item, dict)])
        if target_count <= 0:
            return event_font, []
        start_size = getattr(event_font, "size", 19) or 19
        min_size = min(start_size, HISTORY_MIN_EVENT_FONT_SIZE)
        best_font = event_font
        best_rows = []
        for candidate_size in range(start_size, min_size - 1, -1):
            candidate_font = event_font if candidate_size == start_size else self._history_event_font_for_size(event_font, candidate_size, cjk)
            rows = self._event_rows_for_height(
                draw,
                events,
                text_width_px,
                available_h,
                year_font,
                candidate_font,
                date_key=date_key,
                cjk=cjk,
                float_width_px=float_width_px,
                float_rows=float_rows,
                float_height_px=float_height_px,
            )
            if len(rows) > len(best_rows):
                best_font = candidate_font
                best_rows = rows
            if len(rows) >= target_count:
                return candidate_font, rows
        return best_font, best_rows

    def _history_event_font_for_size(self, fallback_font, size, cjk):
        if cjk:
            return self._font("__cjk__", size)
        family = getattr(fallback_font, "family", None) or DEFAULT_FONT
        return self._font(family, size)

    def _event_rows_for_height(self, draw, events, text_width_px, available_h, year_font, event_font, date_key=None, cjk=False, float_width_px=0, float_rows=0, float_height_px=0):
        events = [item for item in events[:5] if isinstance(item, dict)]
        if not events or available_h <= 0:
            return []
        line_h = max(15, int(self._text_height(draw, "Ag", event_font) * HISTORY_LINE_SPACING))
        body_gap_h = max(2, line_h // 8)
        row_gap_h = max(3, line_h // 6)
        if float_height_px <= 0 and float_rows > 0:
            float_height_px = float_rows * line_h
        fitted = []
        used = 0
        for item in events:
            row = self._measure_event_row(
                draw,
                item,
                text_width_px,
                year_font,
                event_font,
                line_h,
                body_gap_h,
                row_gap_h,
                date_key=date_key,
                cjk=cjk,
                float_width_px=float_width_px,
                float_height_px=float_height_px,
                row_top_px=used,
            )
            if used + row["height"] > available_h:
                break
            fitted.append(row)
            used += row["height"]
        self._stretch_history_rows_to_bottom(fitted, available_h)
        return fitted

    def _stretch_history_rows_to_bottom(self, rows, available_h):
        if not rows:
            return
        extra_h = int(available_h - sum(row.get("height", 0) for row in rows))
        if extra_h <= 0:
            return
        stretchable_count = max(1, len(rows) - 1)
        per_row = extra_h // stretchable_count
        remainder = extra_h % stretchable_count
        for index, row in enumerate(rows[:stretchable_count]):
            row["height"] += per_row + (1 if index < remainder else 0)
        last_row = rows[-1]
        ink_h = max(1, int(last_row.get("ink_height", 0)))
        last_row["text_offset_y"] = max(0, int(last_row.get("height", 0)) - ink_h)

    def _measure_event_rows(self, draw, events, text_width_px, year_font, event_font, date_key=None, cjk=False):
        line_h = max(15, int(self._text_height(draw, "Ag", event_font) * HISTORY_LINE_SPACING))
        body_gap_h = max(2, line_h // 8)
        row_gap_h = max(3, line_h // 6)
        return [
            self._measure_event_row(draw, item, text_width_px, year_font, event_font, line_h, body_gap_h, row_gap_h, date_key=date_key, cjk=cjk)
            for item in events
        ]

    def _measure_event_row(self, draw, item, text_width_px, year_font, event_font, line_h, body_gap_h, row_gap_h, date_key=None, cjk=False, float_width_px=0, float_height_px=0, row_top_px=0):
        year = self._clean_text(item.get("year"))
        date_label = self._event_date_label(year, date_key, cjk)
        body_text = self._clean_text(item.get("text"))
        display_text = body_text
        if date_label:
            display_text = f"{date_label}  {body_text}" if body_text else date_label
        body_font = self._font_for_text(display_text, event_font)
        body_lines, line_widths = self._wrap_history_text_around_float(
            draw,
            display_text,
            body_font,
            text_width_px,
            line_h,
            float_width_px,
            float_height_px,
            row_top_px,
        ) if display_text else ([], [])
        content_h = len(body_lines) * line_h
        ink_h = self._history_text_block_ink_height(draw, body_lines, event_font, line_h)
        return {
            "year": year,
            "date_label": date_label,
            "year_h": 0,
            "topic_lines": [],
            "body_lines": body_lines,
            "line_h": line_h,
            "section_gap_h": 0,
            "body_gap_h": body_gap_h,
            "height": max(32, content_h + row_gap_h),
            "ink_height": ink_h,
            "text_offset_y": 0,
            "source_text": body_text,
            "line_widths": line_widths,
        }

    def _history_text_block_ink_height(self, draw, lines, font, line_h):
        if not lines:
            return 0
        last_line = lines[-1]
        return (len(lines) - 1) * line_h + max(1, self._text_height(draw, last_line, self._font_for_text(last_line, font)))

    def _wrap_history_text_around_float(self, draw, text, font, text_width_px, line_h, float_width_px=0, float_height_px=0, row_top_px=0):
        text = self._clean_text(text)
        if not text:
            return [], []
        narrow_width = max(HISTORY_FLOAT_MIN_TEXT_WIDTH, text_width_px - max(0, float_width_px))
        widths = []

        def width_for_line(line_index):
            line_top = row_top_px + line_index * line_h
            if float_width_px > 0 and line_top < float_height_px:
                return min(text_width_px, narrow_width)
            return text_width_px

        if self._contains_cjk(text):
            lines = self._wrap_chars_variable_width(draw, text, font, width_for_line)
        else:
            lines = self._wrap_words_variable_width(draw, text, font, width_for_line)
        for index, _line in enumerate(lines):
            widths.append(width_for_line(index))
        return lines, widths

    def _wrap_chars_variable_width(self, draw, text, font, width_for_line):
        lines, current = [], ""
        line_index = 0
        for char in text:
            max_width = max(1, width_for_line(line_index))
            candidate = current + char
            if self._text_width(draw, candidate, font) <= max_width or not current:
                current = candidate
                continue
            lines.append(current)
            line_index += 1
            current = char
        if current:
            lines.append(current)
        return lines

    def _wrap_words_variable_width(self, draw, text, font, width_for_line):
        lines, current = [], ""
        line_index = 0
        for word in text.split():
            max_width = max(1, width_for_line(line_index))
            candidate = word if not current else f"{current} {word}"
            if self._text_width(draw, candidate, font) <= max_width or not current:
                current = candidate
                continue
            lines.append(current)
            line_index += 1
            current = word
        if current:
            lines.append(current)
        return lines

    def _event_date_label(self, year, date_key, cjk=False):
        year = self._clean_text(year)
        if not year:
            return ""
        date_key = self._clean_text(date_key)
        match = re.match(r"^\d{4}-(\d{1,2})-(\d{1,2})$", date_key)
        if not match:
            return year
        month = int(match.group(1))
        day = int(match.group(2))
        if cjk:
            return f"{year}\u5e74{month}\u6708{day}\u65e5"
        return f"{year}-{month:02d}-{day:02d}"


    def _draw_article_image(self, draw, image, article_image, palette, x, y, width, height):
        fitted = ImageOps.contain(article_image, (width, height), method=RESAMPLE)
        paste_x = x + (width - fitted.width) // 2
        paste_y = y + (height - fitted.height) // 2
        image.paste(fitted, (paste_x, paste_y))

    def _draw_history_image(self, image, history_image, x, y, width, height):
        width = max(1, int(width))
        height = max(1, int(height))
        fitted = ImageOps.fit(history_image, (width, height), method=RESAMPLE, centering=(0.5, 0.5))
        image.paste(fitted, (int(x), int(y)))

    def _history_image_float_box(self, x, y, panel_width, available_h, text_width_px, history_image=None):
        if available_h < HISTORY_IMAGE_HEIGHT + 20 or text_width_px < HISTORY_FLOAT_MIN_TEXT_WIDTH + 70:
            return None
        aspect = 1.45
        if history_image is not None:
            source_w, source_h = getattr(history_image, "size", (0, 0))
            if source_w > 0 and source_h > 0:
                aspect = max(0.45, min(3.6, source_w / source_h))

        max_image_w = max(70, text_width_px - HISTORY_FLOAT_MIN_TEXT_WIDTH - HISTORY_IMAGE_GAP)
        max_image_h = max(
            HISTORY_IMAGE_HEIGHT,
            min(int(available_h * 0.44), int(panel_width * 0.58)),
        )
        if aspect >= 1:
            image_w = max_image_w
            image_h = max(42, int(round(image_w / aspect)))
            if image_h > max_image_h:
                image_h = max_image_h
                image_w = min(max_image_w, max(70, int(round(image_h * aspect))))
        else:
            image_h = max_image_h
            image_w = min(max_image_w, max(70, int(round(image_h * aspect))))

        image_h = min(max(42, image_h), max(42, available_h - HISTORY_IMAGE_GAP))
        if image_w < 70 or image_h < 42:
            return None
        image_x = x + panel_width - image_w
        image_y = y
        return (image_x, image_y, image_w, image_h)
    def _history_image_download_size(self, panel_width, panel_height):
        target_w = max(HISTORY_IMAGE_WIDTH, max(1, panel_width - HISTORY_TEXT_INDENT))
        target_h = max(HISTORY_IMAGE_HEIGHT, max(1, int(panel_height * 0.5)))
        return (target_w, target_h)

    def _load_daily_image_title(self):
        try:
            with Image.open(DAILY_IMAGE_TITLE_PATH) as asset:
                return asset.convert("RGBA")
        except Exception as exc:
            logger.warning("DailyWikiPage title image load failed: %s", exc)
            return None

    def _draw_daily_image_title(self, image, title_image, x, y, max_width, max_height, rule_y=None):
        fitted = self._epaper_wordmark_image(ImageOps.contain(title_image, (max_width, max_height), method=RESAMPLE))
        if rule_y is None:
            paste_y = y + (max_height - fitted.height) // 2
        else:
            paste_y = int(round(rule_y - self._title_image_rule_offset(fitted)))
        image.paste(fitted, (x, paste_y), fitted)
        return paste_y, fitted.height

    def _daily_image_title_fitted_size(self, title_image, max_width, max_height):
        fitted = ImageOps.contain(title_image, (max_width, max_height), method=RESAMPLE)
        return fitted.size

    def _load_daily_header_filler(self):
        try:
            with Image.open(DAILY_HEADER_FILLER_PATH) as asset:
                return asset.convert("RGBA")
        except Exception as exc:
            logger.warning("DailyWikiPage header filler load failed: %s", exc)
            return None

    def _draw_daily_header_filler(self, image, filler, x1, x2, y, rule_y):
        x1 = int(x1)
        x2 = int(x2)
        available_w = x2 - x1
        available_h = int(rule_y) - int(y) - 2
        if available_w < 80 or available_h < 16:
            return None
        target_w = min(int(filler.width), available_w)
        target_h = min(int(filler.height), available_h)
        fitted = filler
        if fitted.size != (target_w, target_h):
            fitted = ImageOps.contain(filler, (target_w, target_h), method=RESAMPLE)
        paste_x = x1 + (available_w - fitted.width) // 2
        paste_y = int(rule_y) - fitted.height - 2
        image.paste(fitted, (paste_x, paste_y), fitted)
        return (paste_x, paste_y, fitted.width, fitted.height)

    def _epaper_wordmark_image(self, title_image):
        rgba = title_image.convert("RGBA")
        rgb = Image.merge("RGB", rgba.split()[:3])
        rgb = ImageEnhance.Color(rgb).enhance(1.08)
        rgb = ImageEnhance.Contrast(rgb).enhance(1.16)
        rgb = rgb.point(lambda value: max(0, int(value * 0.78) - 8))
        alpha = rgba.getchannel("A").point(lambda value: 0 if value <= 0 else min(255, int(value * 1.14) + 8))
        return Image.merge("RGBA", (*rgb.split(), alpha))

    def _title_image_rule_offset(self, title_image):
        title_image = title_image.convert("RGBA")
        width, height = title_image.size
        if width <= 0 or height <= 0:
            return 0
        pixels = title_image.load()
        start_y = max(0, int(height * 0.55))
        min_run = max(8, int(width * 0.12))
        best_run = 0
        best_count = 0
        best_y = height - 1
        for row_y in range(start_y, height):
            current_run = 0
            longest_run = 0
            count = 0
            for pixel_x in range(width):
                red, green, blue, alpha = pixels[pixel_x, row_y]
                luma = (red * 299 + green * 587 + blue * 114) / 1000
                if alpha > 72 and luma < 235:
                    count += 1
                    current_run += 1
                    longest_run = max(longest_run, current_run)
                else:
                    current_run = 0
            if (longest_run, count, row_y) > (best_run, best_count, best_y):
                best_run = longest_run
                best_count = count
                best_y = row_y
        if best_run >= min_run:
            return best_y
        bbox = title_image.getchannel("A").getbbox()
        return bbox[3] - 1 if bbox else height - 1

    def _load_history_title_wordmark(self):
        try:
            with Image.open(HISTORY_TITLE_WORDMARK_PATH) as asset:
                return asset.convert("RGBA")
        except Exception as exc:
            logger.warning("DailyWikiPage history title wordmark load failed: %s", exc)
            return None

    def _draw_history_title_wordmark(self, image, title_image, x, y, max_width, max_height):
        fitted = self._epaper_wordmark_image(ImageOps.contain(title_image, (max_width, max_height), method=RESAMPLE))
        paste_y = y + (max_height - fitted.height) // 2
        image.paste(fitted, (x, paste_y), fitted)
        return fitted.height, paste_y + self._title_image_rule_offset(fitted)

    def _load_topic_placeholder(self):
        try:
            with Image.open(TOPIC_PLACEHOLDER_PATH) as asset:
                return asset.convert("RGBA")
        except Exception as exc:
            logger.warning("DailyWikiPage topic placeholder load failed: %s", exc)
            return None

    def _draw_topic_placeholder(self, image, placeholder, x, y, width, height):
        fitted = ImageOps.contain(placeholder, (width, height), method=PIXEL_RESAMPLE)
        paste_x = x + (width - fitted.width) // 2
        paste_y = y + (height - fitted.height) // 2
        image.paste(fitted, (paste_x, paste_y), fitted)

    def _draw_placeholder(self, draw, x, y, width, height, palette):
        draw.rectangle((x, y, x + width, y + height), fill=palette["panel"], outline=palette["rule"], width=1)
        cx, cy = x + width // 2, y + height // 2
        radius = min(width, height) // 4
        draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), outline=palette["rule"], width=2)
        draw.line((cx - radius, cy, cx + radius, cy), fill=palette["rule"], width=1)
        draw.line((cx, cy - radius, cx, cy + radius), fill=palette["rule"], width=1)

    def _download_image(self, image_url, target_size, settings):
        theme_render_only = self._enabled(
            settings.get("_theme_render_only"),
            default=False,
        )
        cache_path = self._media_cache_path(image_url)
        if theme_render_only or self._cached_media_is_fresh(cache_path, settings):
            cached = self._open_cached_media(cache_path)
            if cached is not None:
                cached.thumbnail((target_size[0] * 3, target_size[1] * 3), RESAMPLE)
                return cached
        if theme_render_only:
            return None

        max_bytes = self._int(settings.get("maxImageBytes"), 10_000_000, 1_000_000, 20_000_000)
        try:
            response = get_http_session().get(
                image_url,
                headers=IMAGE_HEADERS,
                timeout=(5, self._int(settings.get("imageTimeoutSeconds"), 12, 4, 30)),
                stream=True,
            )
            loaded = safe_open_image_response(
                response,
                limits=ImageLimits(max_bytes=max_bytes),
                draft_size=(target_size[0] * 3, target_size[1] * 3),
            ).convert("RGB")
            loaded.thumbnail((target_size[0] * 3, target_size[1] * 3), RESAMPLE)
            self._write_cached_media(cache_path, loaded)
            return loaded
        except Exception as exc:
            logger.warning("DailyWikiPage image download failed: %s", exc)
            return None

    def _media_cache_path(self, image_url):
        digest = hashlib.sha256(str(image_url).encode("utf-8")).hexdigest()
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
        for event in events[:6]:
            if not isinstance(event, dict):
                continue
            text = self._clean_text(self._text(event, "text"))
            year = self._text(event, "year")
            if text:
                page_titles = self._event_page_titles(event)
                enriched_text = self._match_date_page_event_text(year, text, page_titles, date_page_events)
                items.append({"year": str(year) if year not in (None, "") else "", "text": enriched_text or text})
            if len(items) >= 5:
                break
        return items

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
        for event_index, event in enumerate(events[:6]):
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
                item = {"url": url, "title": title, "year": event_year}
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
        if theme.get("mode") != "night":
            return {
                "background": (232, 226, 214),
                "panel": (222, 215, 200),
                "ink": (18, 20, 19),
                "dim": (58, 60, 56),
                "muted": (74, 70, 62),
                "accent": (102, 56, 24),
                "rule": (124, 111, 92),
            }
        palette = theme["palette"]
        return {
            "background": tuple(palette["background"]),
            "panel": tuple(palette["panel"]),
            "ink": tuple(palette["ink"]),
            "dim": tuple(palette["muted"]),
            "muted": tuple(palette["muted"]),
            "accent": tuple(palette["accent"]),
            "rule": tuple(palette["rule"]),
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
