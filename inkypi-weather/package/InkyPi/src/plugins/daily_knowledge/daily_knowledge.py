from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import re
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont

from plugins.base_plugin.base_plugin import BasePlugin
from plugins.context_cache import write_context
from utils.app_utils import coerce_bool, get_available_font_names, get_font
from utils.image_utils import text_width
from utils.http_client import get_http_session
from utils.theme_utils import get_theme_context, get_theme_palette

logger = logging.getLogger(__name__)

PLUGIN_ID = "daily_knowledge"
CACHE_SCHEMA_VERSION = "daily-knowledge-v3"
SENTENCE_STATE_SCHEMA_VERSION = "daily-knowledge-fallback-history-v1"
SENTENCE_HISTORY_FILENAME = "fallback_history.json"
DEFAULT_TIMEZONE = "America/Los_Angeles"
DEFAULT_FONT = "Jost"
USELESS_FACTS_BASE_URL = "https://uselessfacts.jsph.pl/api/v2/facts"
DEFAULT_RAPIDAPI_HOST = "world-fun-facts-all-languages-support.p.rapidapi.com"
DEFAULT_RAPIDAPI_PATH = "/fact"
RAPIDAPI_KEY_NAMES = (
    "Fun_Fact",
    "FUN_FACT",
    "WORLD_FUN_FACTS_KEY",
    "WORLD_FUN_FACTS_API_KEY",
    "RAPIDAPI_KEY",
    "X_RAPIDAPI_KEY",
)
RAPIDAPI_PATH_FALLBACKS = (
    "/fact",
    "/facts/random",
    "/api/v1/fact",
    "/api/v1/facts/random",
)
BLOCKED_FACT_TERMS = (
    "penis",
    "vagina",
    "testicle",
    "scrotum",
    "masturb",
    "porn",
    "sexual",
)
LOCAL_FALLBACK_FACTS = (
    {
        "text": "Honey found in ancient Egyptian tombs can still be edible because its low moisture and acidity slow microbial growth.",
        "source": "Local Knowledge",
        "source_url": "",
        "language": "en",
        "source_state": "local",
    },
    {
        "text": "Octopuses have three hearts: two move blood through the gills, and one pumps it to the rest of the body.",
        "source": "Local Knowledge",
        "source_url": "",
        "language": "en",
        "source_state": "local",
    },
    {
        "text": "A day on Venus is longer than a Venusian year because the planet rotates very slowly.",
        "source": "Local Knowledge",
        "source_url": "",
        "language": "en",
        "source_state": "local",
    },
    {
        "text": "竹子是生长最快的植物之一，在理想环境中一天可以长高很多厘米。",
        "source": "本地知识",
        "source_url": "",
        "language": "zh",
        "source_state": "local",
    },
    {
        "text": "人的指纹在胎儿时期形成，出生后通常会伴随一生保持基本纹路。",
        "source": "本地知识",
        "source_url": "",
        "language": "zh",
        "source_state": "local",
    },
    {
        "text": "海马体参与人的记忆形成，它的名字来自形状近似海马。",
        "source": "本地知识",
        "source_url": "",
        "language": "zh",
        "source_state": "local",
    },
    {
        "text": "月球正以每年约几厘米的速度远离地球，这个距离变化可以用激光测距观测。",
        "source": "本地知识",
        "source_url": "",
        "language": "zh",
        "source_state": "local",
    },
    {
        "text": "活字印刷、指南针、火药和造纸术常被合称为中国古代四大发明。",
        "source": "本地知识",
        "source_url": "",
        "language": "zh",
        "source_state": "local",
    },
    {
        "text": "青铜器上的铭文又称金文，是研究商周历史和文字演变的重要材料。",
        "source": "本地知识",
        "source_url": "",
        "language": "zh",
        "source_state": "local",
    },
    {
        "text": "地球大气中的氮气约占百分之七十八，氧气约占百分之二十一。",
        "source": "本地知识",
        "source_url": "",
        "language": "zh",
        "source_state": "local",
    },
    {
        "text": "蜜蜂会用舞蹈传递花源方向和距离，帮助同伴找到食物。",
        "source": "本地知识",
        "source_url": "",
        "language": "zh",
        "source_state": "local",
    },
    {
        "text": "光从太阳到达地球大约需要八分钟多一点，因此我们看到的是稍早的太阳。",
        "source": "本地知识",
        "source_url": "",
        "language": "zh",
        "source_state": "local",
    },
    {
        "text": "二十四节气反映太阳在黄道上的位置变化，最初常服务于农事安排。",
        "source": "本地知识",
        "source_url": "",
        "language": "zh",
        "source_state": "local",
    },
    {
        "text": "铅笔芯主要由石墨和黏土制成，并不含金属铅。",
        "source": "本地知识",
        "source_url": "",
        "language": "zh",
        "source_state": "local",
    },
    {
        "text": "人眼视网膜中的视锥细胞负责颜色感知，视杆细胞更擅长弱光感知。",
        "source": "本地知识",
        "source_url": "",
        "language": "zh",
        "source_state": "local",
    },
    {
        "text": "地图上的等高线越密集，通常表示地形坡度越陡。",
        "source": "本地知识",
        "source_url": "",
        "language": "zh",
        "source_state": "local",
    },
    {
        "text": "声音在水中传播速度比在空气中更快，主要因为水更难被压缩。",
        "source": "本地知识",
        "source_url": "",
        "language": "zh",
        "source_state": "local",
    },
    {
        "text": "树木年轮可以记录生长季节的环境线索，例如干旱、火灾和温度变化。",
        "source": "本地知识",
        "source_url": "",
        "language": "zh",
        "source_state": "local",
    },
    {
        "text": "彩虹通常由阳光在水滴中折射、反射和色散形成。",
        "source": "本地知识",
        "source_url": "",
        "language": "zh",
        "source_state": "local",
    },
    {
        "text": "太阳黑子是太阳表面较暗的磁活动区域，温度低于周围光球。",
        "source": "本地知识",
        "source_url": "",
        "language": "zh",
        "source_state": "local",
    },
    {
        "text": "DNA 双螺旋由两条反向平行的链组成，碱基配对帮助维持结构。",
        "source": "本地知识",
        "source_url": "",
        "language": "zh",
        "source_state": "local",
    },
    {
        "text": "云的形状与空气上升、湿度和温度变化有关。",
        "source": "本地知识",
        "source_url": "",
        "language": "zh",
        "source_state": "local",
    },
    {
        "text": "古代丝绸之路不是单一路线，而是多条陆海贸易网络的统称。",
        "source": "本地知识",
        "source_url": "",
        "language": "zh",
        "source_state": "local",
    },
    {
        "text": "印刷术的发展降低了书籍复制成本，也推动了知识传播。",
        "source": "本地知识",
        "source_url": "",
        "language": "zh",
        "source_state": "local",
    },
    {
        "text": "珊瑚礁由珊瑚虫和共生藻类共同构成，对水温变化非常敏感。",
        "source": "本地知识",
        "source_url": "",
        "language": "zh",
        "source_state": "local",
    },
    {
        "text": "北极星并非永远同一颗，地轴进动会让北天极附近的亮星随时代变化。",
        "source": "本地知识",
        "source_url": "",
        "language": "zh",
        "source_state": "local",
    },
    {
        "text": "茶叶中的茶多酚会影响茶汤涩感，也参与形成不同茶类的风味。",
        "source": "本地知识",
        "source_url": "",
        "language": "zh",
        "source_state": "local",
    },
    {
        "text": "火山灰颗粒细小坚硬，进入高空后会影响航空发动机安全。",
        "source": "本地知识",
        "source_url": "",
        "language": "zh",
        "source_state": "local",
    },
    {
        "text": "金属汞在常温常压下呈液态，曾用于温度计，但具有毒性。",
        "source": "本地知识",
        "source_url": "",
        "language": "zh",
        "source_state": "local",
    },
    {
        "text": "铁锈主要是铁与氧和水反应后形成的氧化物水合物。",
        "source": "本地知识",
        "source_url": "",
        "language": "zh",
        "source_state": "local",
    },
    {
        "text": "电池把化学能转化为电能，充电电池还能在一定条件下逆向反应。",
        "source": "本地知识",
        "source_url": "",
        "language": "zh",
        "source_state": "local",
    },
    {
        "text": "石英晶体能产生压电效应，因此常用于稳定的计时振荡器。",
        "source": "本地知识",
        "source_url": "",
        "language": "zh",
        "source_state": "local",
    },
    {
        "text": "章鱼的神经元有相当一部分分布在腕足中，能进行局部协调。",
        "source": "本地知识",
        "source_url": "",
        "language": "zh",
        "source_state": "local",
    },
    {
        "text": "斐波那契数列常见于叶序和花盘排列，但自然界并不总是严格遵循。",
        "source": "本地知识",
        "source_url": "",
        "language": "zh",
        "source_state": "local",
    },
)


@dataclass
class KnowledgeFact:
    title: str
    text: str
    source: str
    source_url: str = ""
    language: str = "en"
    source_state: str = "live"


class DailyKnowledge(BasePlugin):
    def generate_settings_template(self):
        params = super().generate_settings_template()
        params["style_settings"] = True
        params["available_fonts"] = get_available_font_names(default=DEFAULT_FONT)
        return params

    def generate_image(self, settings, device_config):
        dimensions = self.get_dimensions(device_config)

        tz_name = device_config.get_config("timezone") or DEFAULT_TIMEZONE
        now = self._now_for_timezone(tz_name)
        payload = self._daily_payload(settings or {}, device_config, now)
        self._write_daily_context(payload, now)

        theme = get_theme_context(device_config, now=now)
        palette = get_theme_palette(theme)
        return self._render_page(dimensions, payload, settings or {}, now, palette)

    def _now_for_timezone(self, tz_name):
        for candidate in (tz_name, DEFAULT_TIMEZONE):
            try:
                return datetime.now(ZoneInfo(candidate))
            except Exception:
                continue
        return datetime.now(timezone.utc)

    def _daily_payload(self, settings, device_config, now):
        language = self._language(settings)
        date_key = now.strftime("%Y-%m-%d")
        cache_file = self._cache_dir() / "daily.json"
        cache_key = self._cache_key(date_key, settings, language)
        cache = self._read_json(cache_file, {})
        force_refresh = self._enabled(settings.get("force_refresh"), default=False)

        if cache.get("schema") == CACHE_SCHEMA_VERSION and cache.get("cache_key") == cache_key and not force_refresh:
            cache["from_cache"] = True
            return cache

        facts = []
        if self._enabled(settings.get("use_useless_facts"), default=True):
            fact = self._fetch_useless_fact(settings, language)
            if fact:
                facts.append(fact)

        if self._enabled(settings.get("use_world_fun_facts"), default=True):
            fact = self._fetch_world_fun_fact(settings, device_config, language)
            if fact:
                facts.append(fact)

        while len(facts) < 2:
            facts.append(self._fallback_fact(language, date_key, offset=len(facts)))

        payload = {
            "schema": CACHE_SCHEMA_VERSION,
            "cache_key": cache_key,
            "date": date_key,
            "language": language,
            "facts": [asdict(fact) for fact in facts[:2]],
            "generated_at": now.isoformat(),
            "from_cache": False,
        }
        self._write_json(cache_file, payload)
        return payload

    def _fetch_useless_fact(self, settings, language):
        mode = str(settings.get("useless_mode") or "today").strip().lower()
        if mode not in {"today", "random"}:
            mode = "today"
        params = {}
        if language in {"en", "de"}:
            params["language"] = language
        else:
            params["language"] = "en"

        try:
            response = get_http_session().get(
                f"{USELESS_FACTS_BASE_URL}/{mode}",
                params=params,
                headers={"Accept": "application/json", "User-Agent": "InkyPi DailyKnowledge/1.0"},
                timeout=6,
            )
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            logger.warning("Useless Facts fetch failed: %s", exc)
            return None

        text = self._extract_fact_text(data)
        if not text or not self._is_display_safe(text):
            return None
        return KnowledgeFact(
            title="Useless Fact",
            text=text,
            source="uselessfacts",
            source_url=str(data.get("permalink") or data.get("source_url") or ""),
            language=str(data.get("language") or params["language"]),
            source_state="live",
        )

    def _fetch_world_fun_fact(self, settings, device_config, language):
        api_key = self._rapidapi_key(settings, device_config)
        if not api_key:
            return None

        host = str(settings.get("rapidapi_host") or DEFAULT_RAPIDAPI_HOST).strip()
        configured_path = str(settings.get("rapidapi_path") or DEFAULT_RAPIDAPI_PATH).strip() or DEFAULT_RAPIDAPI_PATH
        paths = [configured_path]
        paths.extend(path for path in RAPIDAPI_PATH_FALLBACKS if path not in paths)
        headers = {
            "X-RapidAPI-Key": api_key,
            "X-RapidAPI-Host": host,
            "Accept": "application/json",
            "User-Agent": "InkyPi DailyKnowledge/1.0",
        }
        params = {"language": language}

        for path in paths:
            url = f"https://{host}/{path.lstrip('/')}"
            try:
                response = get_http_session().get(url, params=params, headers=headers, timeout=4)
                if response.status_code in {401, 403, 429}:
                    logger.warning("World Fun Facts fetch stopped at %s with status %s", path, response.status_code)
                    return None
                if response.status_code in {400, 404, 405} and path != paths[-1]:
                    continue
                response.raise_for_status()
                data = response.json()
            except Exception as exc:
                logger.warning("World Fun Facts fetch failed at %s: %s", path, exc)
                continue

            text = self._extract_fact_text(data)
            if text and self._is_display_safe(text):
                return KnowledgeFact(
                    title="World Fun Fact",
                    text=text,
                    source="World Fun Facts",
                    source_url="https://rapidapi.com/vintarok-vintarok-default/api/world-fun-facts-all-languages-support",
                    language=language,
                    source_state="live",
                )
        return None

    def _rapidapi_key(self, settings, device_config):
        for key in ("rapidapi_key", "fun_fact_api_key", "world_fun_facts_key"):
            value = str(settings.get(key) or "").strip()
            if value:
                return value

        for env_name in RAPIDAPI_KEY_NAMES:
            value = ""
            if device_config is not None and hasattr(device_config, "load_env_key"):
                try:
                    value = device_config.load_env_key(env_name) or ""
                except Exception as exc:
                    logger.warning("Could not read env key %s: %s", env_name, exc)
            if not value:
                value = os.getenv(env_name, "")
            value = str(value or "").strip()
            if value:
                return value
        return ""

    def _fallback_fact(self, language, date_key, offset=0):
        candidates = [item for item in LOCAL_FALLBACK_FACTS if item["language"] == language]
        if not candidates:
            candidates = [item for item in LOCAL_FALLBACK_FACTS if item["language"] == "en"]
        item = self._select_fallback_candidate(candidates, language, date_key, offset)
        return KnowledgeFact(
            title="Knowledge Note" if language != "zh" else "知识札记",
            text=item["text"],
            source=item["source"],
            source_url=item.get("source_url", ""),
            language=item.get("language", language),
            source_state=item.get("source_state", "local"),
        )

    def _select_fallback_candidate(self, candidates, language, date_key, offset=0):
        indexed = {self._fallback_fact_id(item): dict(item) for item in candidates}
        if not indexed:
            return {}

        ids = list(indexed.keys())
        state_file = self._cache_dir() / SENTENCE_HISTORY_FILENAME
        state = self._read_json(state_file, {})
        if state.get("schema") != SENTENCE_STATE_SCHEMA_VERSION or not isinstance(state.get("languages"), dict):
            state = {"schema": SENTENCE_STATE_SCHEMA_VERSION, "languages": {}}

        languages = state["languages"]
        lang_state = languages.setdefault(language, {})
        if not isinstance(lang_state, dict):
            lang_state = {}
            languages[language] = lang_state

        daily = lang_state.get("daily")
        if not isinstance(daily, dict):
            daily = {}
            lang_state["daily"] = daily

        today_ids = [item_id for item_id in daily.get(date_key, []) if item_id in indexed]
        daily[date_key] = today_ids
        if len(today_ids) > offset:
            return dict(indexed[today_ids[offset]])

        recent = [item_id for item_id in lang_state.get("recent", []) if item_id in indexed]
        blocked = set(recent) | set(today_ids)
        available = [item_id for item_id in ids if item_id not in blocked]
        if not available:
            recent = []
            available = [item_id for item_id in ids if item_id not in set(today_ids)]
        if not available:
            available = ids

        rng = random.Random(f"{date_key}|{language}|{offset}|{len(ids)}")
        selected_id = rng.choice(available)
        today_ids.append(selected_id)
        daily[date_key] = today_ids
        lang_state["recent"] = (recent + [selected_id])[-len(ids):]
        self._prune_daily_sentence_history(daily)
        self._write_json(state_file, state)
        return dict(indexed[selected_id])

    def _fallback_fact_id(self, item):
        text = str(item.get("text") or "")
        language = str(item.get("language") or "")
        return hashlib.sha1(f"{language}|{text}".encode("utf-8")).hexdigest()[:16]

    def _prune_daily_sentence_history(self, daily):
        if len(daily) <= 45:
            return
        for date_key in sorted(daily)[:-45]:
            daily.pop(date_key, None)

    def _render_page(self, dimensions, payload, settings, now, palette):
        width, height = dimensions
        image = Image.new("RGB", dimensions, palette["background"])
        draw = ImageDraw.Draw(image)

        margin = max(24, int(width * 0.055))
        top = max(22, int(height * 0.055))
        inner_width = width - margin * 2
        font_family = settings.get("font_family") or DEFAULT_FONT

        title_font = self._load_font(font_family, max(26, min(44, width // 17)), "bold")
        sub_font = self._load_font(font_family, max(12, width // 58))
        label_font = self._load_font(font_family, max(12, width // 56), "bold")
        body_font = self._load_font(font_family, max(22, min(34, width // 25)))
        meta_font = self._load_font(font_family, max(11, width // 68))

        title = "DAILY KNOWLEDGE"
        subtitle = now.strftime("%A, %b %d").upper()
        draw.text((margin, top), title, font=title_font, fill=palette["ink"])
        title_h = self._text_height(draw, title, title_font)
        subtitle_w = self._text_width(draw, subtitle, sub_font)
        draw.text((width - margin - subtitle_w, top + max(6, title_h // 4)), subtitle, font=sub_font, fill=palette["dim"])

        rule_y = top + title_h + 14
        draw.line((margin, rule_y, width - margin, rule_y), fill=palette["rule"], width=1)

        facts = payload.get("facts") or []
        card_gap = max(14, height // 34)
        available_h = height - rule_y - 22 - top
        card_h = (available_h - card_gap) // 2
        y = rule_y + 18

        accents = [palette.get("cyan", palette["accent"]), palette.get("gold", palette["accent"])]
        for index, fact in enumerate(facts[:2]):
            self._draw_fact_card(
                draw,
                fact,
                margin,
                y,
                inner_width,
                card_h,
                palette,
                accents[index % len(accents)],
                label_font,
                body_font,
                meta_font,
            )
            y += card_h + card_gap

        return image

    def _draw_fact_card(self, draw, fact, x, y, width, height, palette, accent, label_font, body_font, meta_font):
        title = str(fact.get("title") or "Knowledge").upper()
        source = str(fact.get("source") or "").upper()
        source_state = str(fact.get("source_state") or "live").upper()
        text = self._display_text(str(fact.get("text") or ""))
        title_font = self._font_for_text(title, label_font)
        pad_x = max(14, width // 34)
        pad_y = max(12, height // 12)

        draw.rounded_rectangle((x, y, x + width, y + height), radius=8, outline=palette["rule"], width=1)
        draw.rectangle((x, y, x + 5, y + height), fill=accent)

        draw.text((x + pad_x, y + pad_y), title, font=title_font, fill=accent)
        badge = source_state if source_state != "LIVE" else source
        badge = self._fit_single_line(draw, badge, meta_font, max(60, width // 3))
        badge_w = self._text_width(draw, badge, meta_font)
        draw.text((x + width - pad_x - badge_w, y + pad_y), badge, font=meta_font, fill=palette["dim"])

        body_top = y + pad_y + self._text_height(draw, title, title_font) + max(8, height // 14)
        max_body_width = width - pad_x * 2
        max_body_height = height - (body_top - y) - pad_y - self._text_height(draw, "Ag", meta_font) - 7
        lines, used_font = self._fit_wrapped_text(draw, text, body_font, max_body_width, max_body_height)
        line_h = int(self._text_height(draw, "Ag", used_font) * 1.28)
        current_y = body_top
        for line in lines:
            draw.text((x + pad_x, current_y), line, font=used_font, fill=palette["ink"])
            current_y += line_h

        meta = self._source_meta(fact)
        meta_line_font = self._font_for_text(meta, meta_font)
        meta = self._fit_single_line(draw, meta, meta_line_font, max_body_width)
        draw.text((x + pad_x, y + height - pad_y - self._text_height(draw, meta, meta_line_font)), meta, font=meta_line_font, fill=palette["muted"])

    def _fit_wrapped_text(self, draw, text, font, max_width, max_height):
        size = getattr(font, "size", 20) or 20
        family = getattr(font, "family", None) or DEFAULT_FONT
        if self._contains_cjk(text):
            family = "__cjk__"
        for candidate_size in range(size, 11, -2):
            candidate = self._load_font(family, candidate_size)
            lines = self._wrap_text(draw, text, candidate, max_width)
            line_h = int(self._text_height(draw, "Ag", candidate) * 1.28)
            if lines and len(lines) * line_h <= max_height:
                return lines, candidate
        candidate = self._load_font(family, 12)
        return self._wrap_text(draw, text, candidate, max_width)[:4], candidate

    def _wrap_text(self, draw, text, font, max_width):
        text = re.sub(r"\s+", " ", text or "").strip()
        if not text:
            return [""]
        if self._contains_cjk(text):
            return self._wrap_chars(draw, text, font, max_width)

        lines = []
        current = ""
        for word in text.split():
            candidate = word if not current else f"{current} {word}"
            if self._text_width(draw, candidate, font) <= max_width or not current:
                current = candidate
            else:
                lines.append(current)
                current = word
        if current:
            lines.append(current)
        return lines

    def _wrap_chars(self, draw, text, font, max_width):
        lines = []
        current = ""
        for char in text:
            candidate = current + char
            if self._text_width(draw, candidate, font) <= max_width or not current:
                current = candidate
            else:
                lines.append(current)
                current = char
        if current:
            lines.append(current)
        return lines

    def _source_meta(self, fact):
        source = str(fact.get("source") or "source")
        language = str(fact.get("language") or "").upper()
        state = str(fact.get("source_state") or "live")
        suffix = "cache/local" if state in {"cache", "local"} else "daily refresh"
        return f"{source} · {language} · {suffix}"

    def _write_daily_context(self, payload, now):
        try:
            write_context(
                PLUGIN_ID,
                {
                    "date": payload.get("date"),
                    "language": payload.get("language"),
                    "facts": payload.get("facts") or [],
                },
                generated_at=now,
                ttl_seconds=36 * 60 * 60,
            )
        except Exception as exc:
            logger.warning("Could not write DailyKnowledge context: %s", exc)

    def _cache_key(self, date_key, settings, language):
        parts = [
            CACHE_SCHEMA_VERSION,
            date_key,
            language,
            str(settings.get("useless_mode") or "today"),
            str(self._enabled(settings.get("use_useless_facts"), default=True)),
            str(self._enabled(settings.get("use_world_fun_facts"), default=True)),
            str(settings.get("rapidapi_host") or DEFAULT_RAPIDAPI_HOST),
            str(settings.get("rapidapi_path") or DEFAULT_RAPIDAPI_PATH),
        ]
        return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()

    def _cache_dir(self):
        override = os.getenv("INKYPI_DAILY_KNOWLEDGE_CACHE", "").strip()
        path = Path(override) if override else Path(self.get_plugin_dir("cache"))
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _read_json(self, path, default):
        try:
            with path.open("r", encoding="utf-8") as f:
                payload = json.load(f)
            return payload if isinstance(payload, dict) else default
        except FileNotFoundError:
            return default
        except Exception as exc:
            logger.warning("Could not read DailyKnowledge cache %s: %s", path, exc)
            return default

    def _write_json(self, path, payload):
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
        except Exception as exc:
            logger.warning("Could not write DailyKnowledge cache %s: %s", path, exc)

    def _language(self, settings):
        language = str(settings.get("language") or "zh").strip().lower()
        return language or "zh"

    def _extract_fact_text(self, data):
        if isinstance(data, str):
            return self._display_text(data)
        if isinstance(data, list):
            for item in data:
                text = self._extract_fact_text(item)
                if text:
                    return text
            return ""
        if not isinstance(data, dict):
            return ""

        for key in ("text", "fact", "fun_fact", "content", "description", "value", "message"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return self._display_text(value)

        for key in ("data", "result", "payload", "item"):
            text = self._extract_fact_text(data.get(key))
            if text:
                return text
        return ""

    def _display_text(self, value):
        value = str(value or "").strip()
        value = value.replace("\u201c", '"').replace("\u201d", '"')
        value = value.replace("\u2018", "'").replace("\u2019", "'")
        value = value.replace("\u2014", "-").replace("\u2013", "-")
        return re.sub(r"\s+", " ", value)

    def _is_display_safe(self, text):
        normalized = str(text or "").lower()
        return not any(term in normalized for term in BLOCKED_FACT_TERMS)

    def _load_font(self, font_family, size, weight="normal"):
        if font_family == "__cjk__":
            cjk = self._cjk_font_path()
            if cjk:
                try:
                    return ImageFont.truetype(str(cjk), size)
                except OSError:
                    pass
        if self._contains_cjk(str(font_family or "")):
            font_family = DEFAULT_FONT
        try:
            font = get_font(font_family or DEFAULT_FONT, size, weight)
            if font:
                return font
        except Exception as exc:
            logger.debug("Could not load font %s: %s", font_family, exc)

        cjk = self._cjk_font_path()
        if cjk:
            try:
                return ImageFont.truetype(str(cjk), size)
            except OSError:
                pass
        return ImageFont.load_default()

    def _font_for_text(self, text, fallback_font):
        if not self._contains_cjk(text):
            return fallback_font
        size = getattr(fallback_font, "size", 14) or 14
        return self._load_font("__cjk__", size)

    def _cjk_font_path(self):
        plugin_root = Path(self.get_plugin_dir()).parent
        for relative in (
            "chinese_literature_clock/fonts/FandolKai-Regular.otf",
            "../static/fonts/LXGWWenKai-Regular.ttf",
            "chinese_literature_clock/fonts/I.Ming-8.10.ttf",
        ):
            path = plugin_root / relative
            if path.is_file():
                return path
        return None

    def _contains_cjk(self, text):
        return any("\u3400" <= char <= "\u9fff" for char in str(text or ""))

    def _fit_single_line(self, draw, text, font, max_width):
        text = str(text or "")
        if self._text_width(draw, text, font) <= max_width:
            return text
        suffix = "..."
        while text and self._text_width(draw, text + suffix, font) > max_width:
            text = text[:-1]
        return text + suffix if text else suffix

    def _text_width(self, draw, text, font):
        return text_width(draw, text, font)

    def _text_height(self, draw, text, font):
        bbox = draw.textbbox((0, 0), text, font=font)
        return bbox[3] - bbox[1]

    def _enabled(self, value, default=False):
        return coerce_bool(value, default=default, truthy=tuple({"1", "true", "yes", "on"}))
