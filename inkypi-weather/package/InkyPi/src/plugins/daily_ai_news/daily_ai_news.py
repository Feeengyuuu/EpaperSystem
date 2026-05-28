from __future__ import annotations

import hashlib
import html
import json
import logging
import os
import re
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import feedparser
from openai import OpenAI
from PIL import Image, ImageDraw, ImageFont
import pytz
import requests

from plugins.base_plugin.base_plugin import BasePlugin
from plugins.context_cache import write_context
from utils.app_utils import get_font, get_fonts
from utils.theme_utils import get_theme_context, get_theme_palette

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gpt-5-nano"
DEFAULT_TITLE = "整点新闻"
DEFAULT_FONT = "LXGW WenKai"
BACKGROUND_IMAGE = "background_world_news.png"
SUMMARY_SCHEMA_VERSION = "fresh-hard-news-rss-only-dedupe-v7"
DEFAULT_FEEDS = """BBC中文|https://feeds.bbci.co.uk/zhongwen/simp/rss.xml
BBC World|https://feeds.bbci.co.uk/news/world/rss.xml
NPR|https://feeds.npr.org/1001/rss.xml
NYTimes World|https://rss.nytimes.com/services/xml/rss/nyt/World.xml
Guardian World|https://www.theguardian.com/world/rss"""
MARKET_GROUPS = {
    "a_share": [
        ("000001.SS", "上证指数"),
        ("399001.SZ", "深证成指"),
        ("399006.SZ", "创业板指"),
    ],
    "us_stock": [
        ("^GSPC", "标普500"),
        ("^IXIC", "纳斯达克"),
        ("^DJI", "道琼斯"),
    ],
}

SECTION_LABELS = {
    "top": "今日头条",
    "a_share": "A股今日",
    "us_stock": "美股今日",
}


def _enabled(value: Any) -> bool:
    return value is True or str(value).lower() in {"1", "true", "on", "yes"}


def _clean_text(value: str, max_len: int = 260) -> str:
    value = re.sub(r"<[^>]+>", " ", value or "")
    value = html.unescape(value)
    value = re.sub(r"\s+", " ", value).strip()
    return value[:max_len]


def _parse_int(value: Any, default: int, low: int, high: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(low, min(high, parsed))


def _safe_json_load(path: Path, default: Any) -> Any:
    try:
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Could not read JSON cache %s: %s", path, exc)
    return default


def _safe_json_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    except Exception:
        path.write_text(text, encoding="utf-8")
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass


class DailyAINews(BasePlugin):
    def generate_settings_template(self):
        params = super().generate_settings_template()
        params["style_settings"] = True
        params["api_key"] = {
            "required": True,
            "service": "OpenAI",
            "expected_key": "OPEN_AI_SECRET",
        }
        params["available_fonts"] = sorted({
            f.get("name") or f.get("font_family")
            for f in get_fonts()
            if f.get("name") or f.get("font_family")
        })
        if DEFAULT_FONT not in params["available_fonts"]:
            params["available_fonts"].append(DEFAULT_FONT)
        return params

    def generate_image(self, settings, device_config):
        dimensions = device_config.get_resolution()
        if device_config.get_config("orientation") == "vertical":
            dimensions = dimensions[::-1]

        tz_name = device_config.get_config("timezone") or "America/Los_Angeles"
        now = datetime.now(pytz.timezone(tz_name))

        try:
            brief = self._get_brief(settings, device_config, now)
        except Exception as exc:
            logger.exception("Daily AI news failed")
            brief = self._fallback_brief(settings, now, str(exc))

        self._write_news_context(brief, now)
        theme_context = get_theme_context(device_config, now=now)
        return self._render(dimensions, settings, brief, now, theme_context)

    def _write_news_context(self, brief: dict[str, Any], now: datetime) -> None:
        payload = brief.get("brief") if isinstance(brief, dict) else {}
        if not isinstance(payload, dict):
            return

        items = []
        for item in (payload.get("top") or [])[:7]:
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
            },
            generated_at=brief.get("generated_at") or now,
            ttl_seconds=24 * 60 * 60,
        )

    def _get_brief(self, settings, device_config, now: datetime) -> dict[str, Any]:
        model = (settings.get("model") or DEFAULT_MODEL).strip()
        feeds_text = settings.get("feed_urls") or DEFAULT_FEEDS
        max_items = _parse_int(settings.get("max_items"), 22, 6, 40)
        force_refresh = _enabled(settings.get("force_refresh"))
        date_key = now.strftime("%Y-%m-%d")
        cache_key = self._cache_key(date_key, model, feeds_text, max_items, settings.get("region_focus"))

        cache_file = self._cache_dir() / "brief.json"
        cached = _safe_json_load(cache_file, {})
        if cached.get("cache_key") == cache_key and not force_refresh:
            cached["from_cache"] = True
            return cached

        stale = cached if cached.get("brief") else None
        api_key = device_config.load_env_key("OPEN_AI_SECRET") or device_config.load_env_key("OPENAI_API_KEY")
        if not api_key:
            if stale:
                stale["from_cache"] = True
                stale["warning"] = "未配置 OPEN_AI_SECRET，显示旧缓存。"
                return stale
            raise RuntimeError("OPEN_AI_SECRET is not configured.")

        if not self._allow_api_call(settings, date_key):
            if stale:
                stale["from_cache"] = True
                stale["warning"] = "已达到今日 API 调用上限，显示旧缓存。"
                return stale
            raise RuntimeError("Daily API limit reached and no cache is available.")

        items = self._fetch_items(feeds_text, max_items)
        items = self._rank_news_items(items, now)[:max_items]
        if not items:
            if stale:
                stale["from_cache"] = True
                stale["warning"] = "新闻源暂不可用，显示旧缓存。"
                return stale
            raise RuntimeError("No RSS items could be fetched.")

        market_snapshot = self._fetch_market_snapshot(now)
        brief = self._summarize_with_openai(api_key, model, settings, items, market_snapshot, now)
        payload = {
            "cache_key": cache_key,
            "date": date_key,
            "generated_at": now.isoformat(),
            "model": model,
            "items": items[:max_items],
            "market_snapshot": market_snapshot,
            "brief": brief,
            "from_cache": False,
        }
        _safe_json_write(cache_file, payload)
        self._record_api_call(date_key)
        return payload

    def _cache_dir(self) -> Path:
        path = Path(self.get_plugin_dir("cache"))
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _cache_key(self, date_key: str, model: str, feeds_text: str, max_items: int, region_focus: Any) -> str:
        raw = "\n".join([SUMMARY_SCHEMA_VERSION, date_key, model, feeds_text, str(max_items), str(region_focus or "")])
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _allow_api_call(self, settings, date_key: str) -> bool:
        limit = _parse_int(settings.get("daily_api_limit"), 1, 1, 5)
        state = _safe_json_load(self._cache_dir() / "state.json", {})
        if state.get("date") != date_key:
            return True
        return int(state.get("calls") or 0) < limit

    def _record_api_call(self, date_key: str) -> None:
        state_file = self._cache_dir() / "state.json"
        state = _safe_json_load(state_file, {})
        if state.get("date") != date_key:
            state = {"date": date_key, "calls": 0}
        state["calls"] = int(state.get("calls") or 0) + 1
        _safe_json_write(state_file, state)

    def _parse_feeds(self, feeds_text: str) -> list[tuple[str, str]]:
        feeds = []
        for line in (feeds_text or "").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "|" in line:
                name, url = [part.strip() for part in line.split("|", 1)]
            else:
                url = line
                name = re.sub(r"^https?://", "", url).split("/")[0]
            if url.startswith("http"):
                feeds.append((name or url, url))
        return feeds

    def _fetch_items(self, feeds_text: str, max_items: int) -> list[dict[str, str]]:
        items: list[dict[str, str]] = []
        seen = set()
        for source, url in self._parse_feeds(feeds_text):
            try:
                resp = requests.get(
                    url,
                    timeout=12,
                    headers={"User-Agent": "InkyPi Daily AI News/1.0"},
                )
                resp.raise_for_status()
                feed = feedparser.parse(resp.content)
            except Exception as exc:
                logger.warning("RSS fetch failed for %s: %s", url, exc)
                continue

            per_feed = 0
            for entry in feed.entries[:12]:
                title = _clean_text(entry.get("title", ""), 150)
                if not title:
                    continue
                key = re.sub(r"\W+", "", title.lower())
                if key in seen:
                    continue
                seen.add(key)
                items.append({
                    "source": source,
                    "title": title,
                    "summary": _clean_text(
                        entry.get("summary", "") or entry.get("description", ""),
                        280,
                    ),
                    "published": _clean_text(entry.get("published", "") or entry.get("updated", ""), 80),
                    "link": _clean_text(entry.get("link", ""), 220),
                })
                per_feed += 1
                if len(items) >= max_items or per_feed >= 5:
                    break
            if len(items) >= max_items:
                break
        return items

    def _rank_news_items(self, items: list[dict[str, str]], now: datetime) -> list[dict[str, str]]:
        def score(index: int, item: dict[str, str]) -> float:
            title = item.get("title", "")
            summary = item.get("summary", "")
            text = f"{title} {summary}".lower()
            value = 100 - index * 0.05

            published = self._parse_published(item.get("published", ""))
            if published:
                age_hours = max(0.0, (now - published.astimezone(now.tzinfo)).total_seconds() / 3600)
                if age_hours <= 30:
                    value += 35
                elif age_hours <= 72:
                    value += 18
                elif age_hours > 168:
                    value -= 35

            hard_terms = [
                "空袭", "袭击", "打击", "冲突", "谈判", "协议", "制裁", "关税",
                "事故", "调查", "追责", "死亡", "警告", "宣布", "发布", "通过",
                "选举", "法院", "央行", "利率", "通胀", "油价", "股价", "上涨",
                "下跌", "收盘", "升级", "停火", "导弹", "军", "政府", "总统",
            ]
            broad_terms = [
                "人生", "心理健康", "遗产", "生活", "旅行", "文化", "观点",
                "专访", "研究显示", "为什么", "如何", "可能对你有益", "意义",
            ]
            value += sum(10 for term in hard_terms if term in text)
            value -= sum(16 for term in broad_terms if term in text)
            if not self._has_cjk(title):
                value -= 5
            return value

        return [item for _score, item in sorted(
            ((score(index, item), item) for index, item in enumerate(items)),
            key=lambda pair: pair[0],
            reverse=True,
        )]

    def _parse_published(self, value: str) -> datetime | None:
        if not value:
            return None
        try:
            parsed = parsedate_to_datetime(value)
        except Exception:
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except Exception:
                return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=pytz.UTC)
        return parsed

    def _fetch_market_snapshot(self, now: datetime) -> dict[str, Any]:
        snapshot: dict[str, Any] = {"generated_at": now.isoformat(), "groups": {}}
        for group, symbols in MARKET_GROUPS.items():
            rows = []
            for symbol, name in symbols:
                row = self._fetch_yahoo_quote(symbol, name)
                if row:
                    rows.append(row)
            snapshot["groups"][group] = rows
        return snapshot

    def _fetch_yahoo_quote(self, symbol: str, name: str) -> dict[str, Any] | None:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(symbol, safe='')}"
        try:
            resp = requests.get(
                url,
                params={"range": "5d", "interval": "1d"},
                timeout=10,
                headers={"User-Agent": "InkyPi Daily AI News/1.0"},
            )
            resp.raise_for_status()
            result = resp.json()["chart"]["result"][0]
        except Exception as exc:
            logger.warning("Market quote fetch failed for %s: %s", symbol, exc)
            return None

        meta = result.get("meta") or {}
        timestamps = result.get("timestamp") or []
        quote_data = ((result.get("indicators") or {}).get("quote") or [{}])[0]
        closes = [value for value in quote_data.get("close", []) if isinstance(value, (int, float))]

        price = meta.get("regularMarketPrice")
        if not isinstance(price, (int, float)) and closes:
            price = closes[-1]
        previous = meta.get("chartPreviousClose") or meta.get("previousClose")
        if not isinstance(previous, (int, float)) and len(closes) >= 2:
            previous = closes[-2]
        if not isinstance(price, (int, float)):
            return None

        change = None
        change_pct = None
        if isinstance(previous, (int, float)) and previous:
            change = price - previous
            change_pct = change / previous * 100

        as_of = ""
        if timestamps:
            try:
                as_of = datetime.fromtimestamp(timestamps[-1], tz=pytz.UTC).isoformat()
            except Exception:
                as_of = ""

        return {
            "symbol": symbol,
            "name": name,
            "price": round(float(price), 2),
            "change": round(float(change), 2) if isinstance(change, (int, float)) else None,
            "change_pct": round(float(change_pct), 2) if isinstance(change_pct, (int, float)) else None,
            "as_of": as_of,
            "currency": meta.get("currency") or "",
            "exchange": meta.get("exchangeName") or meta.get("fullExchangeName") or "",
        }

    def _summarize_with_openai(
        self,
        api_key: str,
        model: str,
        settings,
        items: list[dict[str, str]],
        market_snapshot: dict[str, Any],
        now: datetime,
    ) -> dict[str, Any]:
        client = OpenAI(api_key=api_key)
        system = (
            "你是中文新闻编辑。只根据用户提供的 RSS 条目写简体中文每日简报。"
            "top 新闻只能来自 RSS 条目，不能使用市场行情、常识或背景知识补充。"
            "新闻必须强调今天或最近一次更新的具体变化，标题要具体，避免宏观空话。"
            "输出必须是一个 JSON object，不要 Markdown。"
        )
        user = {
            "date": now.strftime("%Y-%m-%d"),
            "style": settings.get("region_focus") or "china_global",
            "output_schema": {
                "lede": "不超过32字的总览",
                "top": [{"title": "包含对象、动作、结果的具体标题", "why": "为什么重要"}],
                "sources": ["来源名"],
            },
            "rules": [
                "top 给 7 条，每条 title 18到28字，why 16到26字",
                "top 只选最近 24-48 小时内有明确新进展的硬新闻；优先冲突、政策、事故、市场、外交、法律、重大科技治理",
                "top 不允许使用 market_snapshot、股票指数或你自己的背景知识生成新闻",
                "不得写 RSS items 中不存在的人名、机构、政策或市场事件",
                "top 中同一事件只能出现一次，不要用不同标题重复同一军事行动、谈判或事故",
                "title 必须包含具体人物/机构/地点/事件动作/结果，禁止只写宏观分类",
                "避免使用“引发讨论”“风险升级”“议题焦点”“全球媒体聚焦”这类宽泛标题，除非同时写清具体事件",
                "不要把人生、心理健康、生活方式、科普解释稿、旧背景稿放进 top，除非它们是当天重大政策或公共事件",
                "lede 必须概括今天最重要的新变化，不要写“今日新闻简报已生成”",
                "尽量保留素材里的数字、地点、人物、机构和动作",
                "sources 只列实际用到的来源名，最多5个",
            ],
            "items": items,
        }

        user_text = json.dumps(user, ensure_ascii=False)
        try:
            response_kwargs = {
                "model": model,
                "instructions": system,
                "input": user_text,
                "max_output_tokens": 3000,
            }
            if model.startswith("gpt-5"):
                response_kwargs["reasoning"] = {"effort": "minimal"}
                response_kwargs["text"] = {"verbosity": "low"}
            response = client.responses.create(**response_kwargs)
            content = (getattr(response, "output_text", "") or "").strip()
        except Exception as exc:
            logger.warning("Responses API failed for %s, falling back to chat completions: %s", model, exc)
            chat_kwargs = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_text},
                ],
            }
            try:
                response = client.chat.completions.create(max_completion_tokens=3000, **chat_kwargs)
            except TypeError:
                response = client.chat.completions.create(max_tokens=3000, **chat_kwargs)
            content = (response.choices[0].message.content or "").strip()

        if not content:
            raise RuntimeError("OpenAI returned an empty summary.")
        brief = self._parse_brief_json(content)
        brief["top"] = self._dedupe_top_items(brief.get("top") or [], items)
        if not str(brief.get("lede") or "").strip():
            brief["lede"] = self._fallback_lede(brief["top"])
        return brief

    def _parse_brief_json(self, content: str) -> dict[str, Any]:
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", content, re.S)
            if not match:
                raise
            data = json.loads(match.group(0))

        top = self._as_list(data.get("top"), 7)
        lede = str(data.get("lede") or "").strip()
        if not lede or lede == "今日新闻简报已生成。":
            lede = self._fallback_lede(top)
        return {
            "lede": lede[:48],
            "top": top,
            "a_share": self._as_market_block(data.get("a_share")),
            "us_stock": self._as_market_block(data.get("us_stock")),
            "sources": self._as_list(data.get("sources"), 5),
        }

    def _as_list(self, value: Any, limit: int) -> list[Any]:
        if isinstance(value, list):
            return value[:limit]
        if value:
            return [value]
        return []

    def _as_text_list(self, value: Any, limit: int) -> list[str]:
        return [self._module_text(item) for item in self._as_list(value, limit) if self._module_text(item)]

    def _as_market_block(self, value: Any) -> dict[str, str]:
        if isinstance(value, dict):
            return {
                "summary": str(value.get("summary") or value.get("title") or value.get("text") or "")[:48],
                "analysis": str(value.get("analysis") or value.get("why") or value.get("reason") or "")[:56],
            }
        items = self._as_text_list(value, 2)
        return {
            "summary": items[0] if items else "",
            "analysis": items[1] if len(items) > 1 else "",
        }

    def _fallback_lede(self, top: list[Any]) -> str:
        if top:
            headline, _why = self._news_text(top[0])
            if headline:
                return headline[:32]
        return "今日硬新闻更新"

    def _dedupe_top_items(self, top: list[Any], source_items: list[dict[str, str]]) -> list[Any]:
        result = []
        for item in top:
            headline, _why = self._news_text(item)
            if not headline:
                continue
            if any(self._similar_news_title(headline, self._news_text(existing)[0]) for existing in result):
                continue
            result.append(item)
            if len(result) >= 7:
                return result

        for source in source_items:
            title = str(source.get("title") or "").strip()
            if not title or not self._has_cjk(title):
                continue
            if any(self._similar_news_title(title, self._news_text(existing)[0]) for existing in result):
                continue
            summary = str(source.get("summary") or source.get("source") or "").strip()
            result.append({"title": title[:32], "why": summary[:36]})
            if len(result) >= 7:
                break
        return result

    def _similar_news_title(self, left: str, right: str) -> bool:
        left_chars = {char for char in left if "\u4e00" <= char <= "\u9fff"}
        right_chars = {char for char in right if "\u4e00" <= char <= "\u9fff"}
        if not left_chars or not right_chars:
            return False
        overlap = len(left_chars & right_chars) / max(1, min(len(left_chars), len(right_chars)))
        shared_hot_terms = any(term in left and term in right for term in ("伊朗", "空袭", "煤矿", "太空人", "黎巴嫩", "基辅", "教宗"))
        return overlap >= 0.56 or (shared_hot_terms and overlap >= 0.38)

    def _fallback_brief(self, settings, now: datetime, error: str) -> dict[str, Any]:
        return {
            "date": now.strftime("%Y-%m-%d"),
            "generated_at": now.isoformat(),
            "model": settings.get("model") or DEFAULT_MODEL,
            "brief": {
                "lede": "AI新闻暂不可用，等待下次刷新。",
                "top": [{"title": "新闻简报生成失败", "why": error[:48]}],
                "a_share": {"summary": "A股行情暂不可用", "analysis": "等待下一次刷新。"},
                "us_stock": {"summary": "美股行情暂不可用", "analysis": "等待下一次刷新。"},
                "sources": [],
            },
            "from_cache": False,
            "warning": error,
        }

    def _render(self, dimensions, settings, payload: dict[str, Any], now: datetime, theme_context=None) -> Image.Image:
        width, height = dimensions
        raw_title = str(settings.get("brief_title") or "").strip()
        title = DEFAULT_TITLE if not raw_title or raw_title == "二狗新闻" else raw_title
        brief = payload.get("brief") or {}

        palette = get_theme_palette(theme_context)
        bg = palette["background"]
        header_bg = palette["header"]
        ink = palette["ink"]
        muted = palette["muted"]
        dim = palette["dim"]
        rule = palette["rule"]
        red = palette["red"]
        gold = palette["gold"]
        cyan = palette["cyan"]
        green = palette["green"]

        img = self._base_background(dimensions, bg, (theme_context or {}).get("mode", "day"))
        draw = ImageDraw.Draw(img)

        font_family = settings.get("font_family") or DEFAULT_FONT
        title_font = self._font("方正新楷近似", 44, "bold")
        meta_font = self._font("Jost", 14)
        lede_font = self._font(font_family, 25, "bold")
        section_font = self._font(font_family, 18, "bold")
        headline_font = self._font(font_family, 19, "bold")
        side_font = self._font(font_family, 18, "bold")
        body_font = self._font(font_family, 17)
        small_font = self._font(font_family, 16)
        footer_font = self._font(font_family, 13)

        margin = 24
        draw.rectangle((0, 0, width, 74), fill=header_bg)
        draw.text((margin, 17), title, font=title_font, fill=ink)
        draw.line((margin, 64, margin + min(210, self._tw(draw, title, title_font)), 64), fill=red, width=3)

        date_label = self._date_label(payload, now)
        meta = f"{date_label}  |  {payload.get('model', DEFAULT_MODEL)}"
        if payload.get("from_cache"):
            meta += "  |  cache"
        draw.text((width - margin - self._tw(draw, meta, meta_font), 20), meta, font=meta_font, fill=muted)
        theme_label = "MIDNIGHT BRIEF" if (theme_context or {}).get("mode") == "night" else "DAY BRIEF"
        draw.text((width - margin - self._tw(draw, theme_label, meta_font), 45), theme_label, font=meta_font, fill=cyan)

        lede = str(brief.get("lede") or "")
        draw.line((margin, 88, margin, 124), fill=gold, width=4)
        lede_end = self._draw_wrapped_full(draw, lede, margin + 14, 86, width - margin * 2 - 14, lede_font, ink, 30)

        top_items = list(brief.get("top") or [])
        main_gap = 14
        side_w = 350
        main_w = width - margin * 2 - main_gap - side_w
        top_x = margin
        side_x = top_x + main_w + main_gap
        y = max(136, lede_end + 8)
        top_limit_y = module_y = 352

        self._section_header(draw, "◆ " + SECTION_LABELS["top"], top_x, y, main_w, section_font, red, rule)
        self._section_header(draw, "◇ 快讯补充", side_x, y, side_w, section_font, cyan, rule)
        self._draw_news_items(
            draw,
            top_items[:3],
            top_x,
            y + 28,
            main_w,
            headline_font,
            small_font,
            gold,
            ink,
            muted,
            max_y=top_limit_y,
            start_index=1,
            force_all=True,
            fit_family=font_family,
        )

        side_items = top_items[3:6]
        if len(side_items) < 3:
            side_items.extend(self._rss_sidebar_items(payload, len(top_items), 3 - len(side_items)))
        self._draw_news_items(
            draw,
            side_items,
            side_x,
            y + 28,
            side_w,
            side_font,
            small_font,
            cyan,
            ink,
            muted,
            max_y=top_limit_y,
            start_index=4,
            compact=True,
            force_all=True,
            fit_family=font_family,
            drop_why_if_needed=True,
        )

        module_h = 104
        gap = 20
        col_w = (width - margin * 2 - gap) // 2
        modules = [
            ("▣ " + SECTION_LABELS["a_share"], self._market_lines(brief, payload, "a_share"), red),
            ("◎ " + SECTION_LABELS["us_stock"], self._market_lines(brief, payload, "us_stock"), green),
        ]
        for i, (label, items, color) in enumerate(modules):
            x = margin + i * (col_w + gap)
            self._draw_module(draw, label, items, x, module_y + 6, col_w, section_font, body_font, color, ink, dim, max_y=module_y + module_h)

        footer = self._footer_text(payload, brief)
        draw.text((margin, height - 20), footer, font=footer_font, fill=dim)
        return img

    def _base_background(self, dimensions, bg, theme_mode="day") -> Image.Image:
        base = Image.new("RGB", dimensions, bg)
        path = Path(__file__).with_name(BACKGROUND_IMAGE)
        if not path.is_file():
            return base
        if theme_mode != "night":
            return base
        try:
            try:
                resample = Image.Resampling.LANCZOS
            except AttributeError:
                resample = Image.LANCZOS
            gray = Image.open(path).convert("L").resize(dimensions, resample)
            detail = gray.point(lambda px: 255 - px)
            alpha = detail.point(lambda px: 0 if px < 18 else min(50, int((px - 18) * 0.50)))
            tint = Image.new("RGB", dimensions, (125, 150, 170))
            base.paste(tint, (0, 0), alpha)
        except Exception as exc:
            logger.warning("Could not render daily news background %s: %s", path, exc)
        return base

    def _font(self, family: str, size: int, weight: str = "normal"):
        for candidate in (family, DEFAULT_FONT, "方正新楷近似", "FandolKai"):
            try:
                font = get_font(candidate, size, weight)
                if font:
                    return font
            except OSError:
                continue
        return ImageFont.load_default()

    def _date_label(self, payload: dict[str, Any], now: datetime) -> str:
        date = payload.get("date") or now.strftime("%Y-%m-%d")
        try:
            return datetime.fromisoformat(date).strftime("%Y.%m.%d")
        except ValueError:
            return now.strftime("%Y.%m.%d")

    def _footer_text(self, payload: dict[str, Any], brief: dict[str, Any]) -> str:
        sources = brief.get("sources") or []
        if sources:
            source_text = " / ".join(str(s) for s in sources[:2])
        else:
            source_text = "RSS + OpenAI"
        warning = payload.get("warning")
        if warning:
            return f"来源: {source_text}  |  {warning[:54]}"
        generated_at = str(payload.get("generated_at") or "")[:16].replace("T", " ")
        return f"来源: {source_text}  |  生成: {generated_at}"

    def _section_header(self, draw, label: str, x: int, y: int, width: int, font, accent, rule) -> None:
        draw.text((x, y), label, font=font, fill=accent)
        underline_w = min(width, max(48, self._tw(draw, label, font) + 8))
        draw.line((x, y + 22, x + underline_w, y + 22), fill=accent, width=2)

    def _market_lines(self, brief: dict[str, Any], payload: dict[str, Any], key: str) -> list[str]:
        block = brief.get(key) if isinstance(brief.get(key), dict) else {}
        rows = (((payload.get("market_snapshot") or {}).get("groups") or {}).get(key) or [])
        if rows:
            summary = self._market_summary(key, rows, str(payload.get("date") or ""))
            analysis = self._market_tone(rows)
            return [summary, analysis]

        lines = [
            str(block.get("summary") or "").strip(),
            str(block.get("analysis") or "").strip(),
        ]
        lines = [line for line in lines if line]
        if lines:
            return lines[:2]

        label = "A股" if key == "a_share" else "美股"
        return [f"{label}行情暂不可用", "等待下一次刷新。"]

    def _market_summary(self, key: str, rows: list[dict[str, Any]], date_key: str) -> str:
        names = {
            "上证指数": "上证",
            "深证成指": "深成",
            "创业板指": "创业板",
            "标普500": "标普",
            "纳斯达克": "纳指",
            "道琼斯": "道指",
        }
        parts = []
        latest_date = ""
        for row in rows[:3]:
            name = names.get(str(row.get("name") or ""), str(row.get("name") or row.get("symbol") or "指数"))
            pct = row.get("change_pct")
            if not isinstance(pct, (int, float)):
                continue
            parts.append(f"{name}{self._market_pct(pct)}")
            as_of = str(row.get("as_of") or "")[:10]
            if as_of > latest_date:
                latest_date = as_of
        if not parts:
            return "行情数据暂不可用"
        prefix = "上日 " if key == "us_stock" and date_key and latest_date and latest_date < date_key else ""
        return prefix + " ".join(parts)

    def _market_pct(self, pct: float) -> str:
        text = f"{pct:+.2f}%"
        return text.replace("+0.", "+.").replace("-0.", "-.")

    def _market_tone(self, rows: list[dict[str, Any]]) -> str:
        pct_values = [row.get("change_pct") for row in rows if isinstance(row.get("change_pct"), (int, float))]
        if not pct_values:
            return "指数方向不明，等待更多数据。"
        positive = sum(1 for pct in pct_values if pct > 0)
        negative = sum(1 for pct in pct_values if pct < 0)
        if positive == len(pct_values):
            return "主要指数同步走强，风险偏好改善。"
        if negative == len(pct_values):
            return "主要指数同步走弱，避险情绪升温。"
        if positive > negative:
            return "指数分化偏强，资金仍在选择方向。"
        if negative > positive:
            return "指数分化偏弱，市场情绪较谨慎。"
        return "涨跌互现，市场缺少一致主线。"

    def _draw_module(self, draw, label, items, x, y, width, section_font, body_font, accent, ink, rule, max_y=None) -> int:
        self._section_header(draw, label, x, y, width, section_font, accent, rule)
        y += 31
        for item in list(items)[:2]:
            text = self._module_text(item)
            if not text:
                continue
            lines = self._wrap(draw, f"— {text}", body_font, width)
            needed = len(lines) * 20 + 3
            if max_y is not None and y + needed > max_y:
                break
            for line in lines:
                draw.text((x, y), line, font=body_font, fill=ink)
                y += 20
            y += 3
        return y

    def _draw_wrapped_full(self, draw, text, x, y, max_width, font, fill, line_height) -> int:
        for line in self._wrap(draw, text, font, max_width):
            draw.text((x, y), line, font=font, fill=fill)
            y += line_height
        return y

    def _draw_news_items(
        self,
        draw,
        items,
        x,
        y,
        width,
        headline_font,
        why_font,
        marker_color,
        ink,
        muted,
        max_y,
        start_index=1,
        compact=False,
        force_all=False,
        fit_family=None,
        drop_why_if_needed=False,
    ) -> int:
        if force_all:
            return self._draw_news_items_fit(
                draw,
                items,
                x,
                y,
                width,
                marker_color,
                ink,
                muted,
                max_y,
                start_index,
                fit_family or DEFAULT_FONT,
                drop_why_if_needed,
            )

        markers = ["①", "②", "③", "④", "⑤", "⑥", "⑦", "⑧", "⑨"]
        marker_w = 34 if compact else 40
        title_h = 22 if compact else 24
        why_h = 19 if compact else 20
        gap = 4 if compact else 7
        for offset, item in enumerate(items):
            headline, why = self._news_text(item)
            if not headline and not why:
                continue
            title_lines = self._wrap(draw, headline, headline_font, width - marker_w)
            why_lines = self._wrap(draw, why, why_font, width - marker_w) if why else []
            needed = len(title_lines) * title_h + len(why_lines) * why_h + gap
            if y + needed > max_y:
                break
            number = start_index + offset
            marker = markers[number - 1] if 0 < number <= len(markers) else f"{number}."
            draw.text((x, y - 1), marker, font=headline_font, fill=marker_color)
            text_x = x + marker_w
            for line in title_lines:
                draw.text((text_x, y), line, font=headline_font, fill=ink)
                y += title_h
            for line in why_lines:
                draw.text((text_x, y), line, font=why_font, fill=muted)
                y += why_h
            y += gap
        return y

    def _draw_news_items_fit(
        self,
        draw,
        items,
        x,
        y,
        width,
        marker_color,
        ink,
        muted,
        max_y,
        start_index,
        font_family,
        drop_why_if_needed=False,
    ) -> int:
        styles = [
            {"title": 17, "why": 14, "marker_w": 36, "title_h": 20, "why_h": 16, "gap": 4},
            {"title": 16, "why": 13, "marker_w": 34, "title_h": 18, "why_h": 15, "gap": 3},
            {"title": 15, "why": 12, "marker_w": 32, "title_h": 17, "why_h": 14, "gap": 2},
            {"title": 14, "why": 11, "marker_w": 30, "title_h": 16, "why_h": 13, "gap": 1},
            {"title": 13, "why": 10, "marker_w": 28, "title_h": 15, "why_h": 12, "gap": 0},
            {"title": 12, "why": 9, "marker_w": 26, "title_h": 14, "why_h": 10, "gap": 0},
        ]
        prepared = []
        available = max_y - y
        why_modes = (False, True) if drop_why_if_needed else (False,)
        for omit_why in why_modes:
            prepared = []
            for style in styles:
                headline_font = self._font(font_family, style["title"], "bold")
                why_font = self._font(font_family, style["why"])
                rows = []
                needed_total = 0
                for item in items:
                    headline, why = self._news_text(item)
                    if not headline and not why:
                        continue
                    if omit_why:
                        why = ""
                    text_w = width - style["marker_w"]
                    title_lines = self._wrap(draw, headline, headline_font, text_w)
                    why_lines = self._wrap(draw, why, why_font, text_w) if why else []
                    needed = (
                        len(title_lines) * style["title_h"]
                        + len(why_lines) * style["why_h"]
                        + style["gap"]
                    )
                    rows.append((title_lines, why_lines, needed, headline_font, why_font, style))
                    needed_total += needed
                prepared = rows
                if needed_total <= available:
                    break
            if not prepared or needed_total <= available:
                break

        markers = ["①", "②", "③", "④", "⑤", "⑥", "⑦", "⑧", "⑨"]
        for offset, (title_lines, why_lines, _needed, headline_font, why_font, style) in enumerate(prepared):
            number = start_index + offset
            marker = markers[number - 1] if 0 < number <= len(markers) else f"{number}."
            draw.text((x, y - 1), marker, font=headline_font, fill=marker_color)
            text_x = x + style["marker_w"]
            for line in title_lines:
                draw.text((text_x, y), line, font=headline_font, fill=ink)
                y += style["title_h"]
            for line in why_lines:
                draw.text((text_x, y), line, font=why_font, fill=muted)
                y += style["why_h"]
            y += style["gap"]
        return y

    def _news_text(self, item) -> tuple[str, str]:
        if isinstance(item, dict):
            return str(item.get("title") or ""), str(item.get("why") or "")
        return str(item), ""

    def _module_text(self, item) -> str:
        if isinstance(item, dict):
            primary = (
                item.get("risk")
                or item.get("signal")
                or item.get("watch")
                or item.get("title")
                or item.get("text")
                or item.get("summary")
                or ""
            )
            why = item.get("why") or item.get("reason") or ""
            if primary and why:
                return f"{primary}，{why}"
            return str(primary or why)
        return str(item)

    def _rss_sidebar_items(self, payload: dict[str, Any], start_index: int, limit: int) -> list[dict[str, str]]:
        result = []
        for item in list(payload.get("items") or [])[start_index:]:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip()
            if not title or not self._has_cjk(title):
                continue
            source = str(item.get("source") or "").strip()
            result.append({"title": title, "why": source})
            if len(result) >= limit:
                break
        return result

    def _has_cjk(self, text: str) -> bool:
        return bool(re.search(r"[\u3400-\u9fff]", text))

    def _wrap(self, draw, text: str, font, max_width: int) -> list[str]:
        text = re.sub(r"\s+", " ", str(text)).strip()
        lines = []
        current = ""
        for char in text:
            candidate = current + char
            if self._tw(draw, candidate, font) <= max_width or not current:
                current = candidate
            else:
                lines.append(current)
                current = char
        if current:
            lines.append(current)
        return lines or [""]

    def _tw(self, draw, text: str, font) -> int:
        box = draw.textbbox((0, 0), text, font=font)
        return box[2] - box[0]
