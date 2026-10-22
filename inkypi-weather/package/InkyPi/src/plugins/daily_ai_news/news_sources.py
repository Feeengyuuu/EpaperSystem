"""RSS sources for 整点新闻: feed lists and sections, fetching, ranking and the titles
shown in recent days, which later briefs avoid repeating."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

import feedparser
import pytz

from utils.http_client import get_http_client

from . import news_common as common
from .news_common import MAINLAND_NEWS_MAX, WORLD_NEWS_MAX

logger = logging.getLogger(__name__)

DEFAULT_FEED_FETCH_TIMEOUT_SECONDS = 8
DEFAULT_MAX_FEEDS = 16
RECENT_NEWS_HISTORY_DAYS = 4
RECENT_NEWS_HISTORY_LIMIT = 80
MAX_NEWS_ITEM_AGE_DAYS = 10
LEGACY_DEFAULT_FEEDS = """BBC中文|https://feeds.bbci.co.uk/zhongwen/simp/rss.xml
BBC World|https://feeds.bbci.co.uk/news/world/rss.xml
NPR|https://feeds.npr.org/1001/rss.xml
NYTimes World|https://rss.nytimes.com/services/xml/rss/nyt/World.xml
Guardian World|https://www.theguardian.com/world/rss"""
LEGACY_WORLD_ONLY_DEFAULT_FEEDS = """BBC中文|https://feeds.bbci.co.uk/zhongwen/simp/rss.xml
BBC世界|https://feeds.bbci.co.uk/news/world/rss.xml
NPR新闻|https://feeds.npr.org/1001/rss.xml
纽约时报国际|https://rss.nytimes.com/services/xml/rss/nyt/World.xml
卫报国际|https://www.theguardian.com/world/rss
半岛电视台|https://www.aljazeera.com/xml/rss/all.xml
法国24|https://www.france24.com/en/rss
德国之声|https://rss.dw.com/rdf/rss-en-all
PBS新闻一小时|https://www.pbs.org/newshour/feeds/rss/headlines
ABC国际|https://abcnews.go.com/abcnews/internationalheadlines"""
# 新华网时政 xml 停更于 2022 年、人民网时政 xml 停更于 2025-06，二者会永远返回同一批
# 旧条目并导致大陆新闻反复出现，因此默认源只保留仍在按日更新的中新网各频道。
LEGACY_MAINLAND_FEEDS_V1 = """大陆新闻:新华网时政|https://www.news.cn/politics/news_politics.xml
大陆新闻:人民网时政|https://www.people.com.cn/rss/politics.xml
大陆新闻:中国新闻网国内|https://www.chinanews.com.cn/rss/china.xml
大陆新闻:中国新闻网即时|https://www.chinanews.com.cn/rss/scroll-news.xml"""
DEAD_DEFAULT_FEED_URLS = {
    "https://www.news.cn/politics/news_politics.xml",
    "https://www.people.com.cn/rss/politics.xml",
}
DEFAULT_MAINLAND_FEEDS = """大陆新闻:中国新闻网要闻|https://www.chinanews.com.cn/rss/importnews.xml
大陆新闻:中国新闻网国内|https://www.chinanews.com.cn/rss/china.xml
大陆新闻:中国新闻网财经|https://www.chinanews.com.cn/rss/finance.xml
大陆新闻:中国新闻网社会|https://www.chinanews.com.cn/rss/society.xml
大陆新闻:中国新闻网即时|https://www.chinanews.com.cn/rss/scroll-news.xml"""
DEFAULT_WORLD_FEEDS = """世界新闻:BBC世界|https://feeds.bbci.co.uk/news/world/rss.xml
世界新闻:NPR新闻|https://feeds.npr.org/1001/rss.xml
世界新闻:纽约时报国际|https://rss.nytimes.com/services/xml/rss/nyt/World.xml
世界新闻:卫报国际|https://www.theguardian.com/world/rss
世界新闻:半岛电视台|https://www.aljazeera.com/xml/rss/all.xml
世界新闻:法国24|https://www.france24.com/en/rss
世界新闻:德国之声|https://rss.dw.com/rdf/rss-en-all
世界新闻:PBS新闻一小时|https://www.pbs.org/newshour/feeds/rss/headlines
世界新闻:ABC国际|https://abcnews.go.com/abcnews/internationalheadlines"""
DEFAULT_FEEDS = f"{DEFAULT_MAINLAND_FEEDS}\n{DEFAULT_WORLD_FEEDS}"
LEGACY_REGIONAL_DEFAULT_FEEDS_V1 = f"{LEGACY_MAINLAND_FEEDS_V1}\n{DEFAULT_WORLD_FEEDS}"
FEED_SECTION_PREFIXES = {
    "大陆": "mainland",
    "大陆新闻": "mainland",
    "国内": "mainland",
    "中国": "mainland",
    "世界": "world",
    "世界新闻": "world",
    "世界快报": "world",
    "国际": "world",
    "全球": "world",
}
MAINLAND_FEED_HOST_HINTS = (
    "chinanews.com",
    "news.cn",
    "xinhuanet.com",
    "people.com.cn",
    "cctv.com",
)


class NewsSourcesMixin:
    """Feed lists, RSS fetching and ranking, and the recently shown titles."""

    def _effective_feeds_text(self, feeds_text: Any) -> str:
        raw = str(feeds_text or "").strip()
        if not raw:
            return DEFAULT_FEEDS
        feeds = self._parse_feeds(raw)
        if self._should_expand_legacy_feeds(feeds):
            return DEFAULT_FEEDS
        return raw

    def _should_expand_legacy_feeds(self, feeds: list[tuple[str, str]]) -> bool:
        urls = {self._normalize_feed_url(url) for _name, url in feeds if self._normalize_feed_url(url)}
        if not urls:
            return True
        legacy_feed_sets = (LEGACY_DEFAULT_FEEDS, LEGACY_WORLD_ONLY_DEFAULT_FEEDS, LEGACY_REGIONAL_DEFAULT_FEEDS_V1)
        for legacy_text in legacy_feed_sets:
            legacy_urls = {self._normalize_feed_url(url) for _name, url in self._parse_feeds(legacy_text)}
            if urls == legacy_urls:
                return True
        historical_default_urls = {
            self._normalize_feed_url(url)
            for _name, url in self._parse_feeds(f"{DEFAULT_FEEDS}\n{LEGACY_REGIONAL_DEFAULT_FEEDS_V1}")
        }
        historical_default_urls.add("https://feeds.bbci.co.uk/zhongwen/simp/rss.xml")
        required_regional_default_urls = {
            "https://www.chinanews.com.cn/rss/china.xml",
            "https://feeds.bbci.co.uk/news/world/rss.xml",
        }
        if urls <= historical_default_urls and required_regional_default_urls <= urls:
            if urls & DEAD_DEFAULT_FEED_URLS:
                return True
            if "https://feeds.bbci.co.uk/zhongwen/simp/rss.xml" in urls:
                return True
            if "https://www.chinanews.com.cn/rss/scroll-news.xml" not in urls:
                return True
        if urls and all("bbci.co.uk" in url or "bbc.co.uk" in url or "bbc.com" in url for url in urls):
            return True
        return False

    @staticmethod
    def _normalize_feed_url(url: str) -> str:
        return str(url or "").strip().rstrip("/")

    def _feed_source_and_section(self, source: str, url: str) -> tuple[str, str]:
        label = str(source or "").strip()
        section = ""
        for prefix, mapped_section in FEED_SECTION_PREFIXES.items():
            for separator in (":", "："):
                marker = f"{prefix}{separator}"
                if label.startswith(marker):
                    label = label[len(marker):].strip()
                    section = mapped_section
                    break
            if section:
                break

        if not section:
            normalized_url = self._normalize_feed_url(url).lower()
            section = "mainland" if any(hint in normalized_url for hint in MAINLAND_FEED_HOST_HINTS) else "world"

        return label or str(source or url).strip(), section

    def _item_section(self, item: dict[str, Any]) -> str:
        section = str(item.get("section") or "").strip().lower()
        return section if section in {"mainland", "world"} else "world"

    def _recent_news_file(self) -> Path:
        return self._cache_dir() / "recent_news.json"

    def _load_recent_news_titles(self, now: datetime) -> list[str]:
        history = common._safe_json_load(self._recent_news_file(), {})
        entries = self._pruned_recent_news_entries(history, now)
        titles = []
        seen = set()
        for entry in entries:
            title = str(entry.get("title") or "").strip() if isinstance(entry, dict) else ""
            key = self._news_history_key(title)
            if title and key not in seen:
                titles.append(title)
                seen.add(key)
        return titles

    def _record_recent_news_titles(self, payload: dict[str, Any], now: datetime) -> None:
        brief = payload.get("brief") if isinstance(payload, dict) else {}
        if not isinstance(brief, dict):
            return

        history_file = self._recent_news_file()
        history = common._safe_json_load(history_file, {})
        entries = self._pruned_recent_news_entries(history, now)
        date_key = now.strftime("%Y-%m-%d")
        existing = {
            (str(entry.get("date") or ""), str(entry.get("key") or self._news_history_key(entry.get("title") or "")))
            for entry in entries
            if isinstance(entry, dict)
        }
        additions = []
        lead = brief.get("lead") if isinstance(brief.get("lead"), dict) else None
        stories = [(str(lead.get("section") or "world"), lead)] if lead else []
        for section, limit in (("mainland", MAINLAND_NEWS_MAX), ("world", WORLD_NEWS_MAX)):
            stories.extend((section, item) for item in self._as_list(brief.get(section), limit))
        for section, item in stories:
            title, _why = self._news_text(item)
            title = title.strip()
            key = self._news_history_key(title)
            if not title or not key or (date_key, key) in existing:
                continue
            additions.append({"date": date_key, "section": section, "title": title[:120], "key": key})
            existing.add((date_key, key))

        if additions:
            entries.extend(additions)
            entries = entries[-RECENT_NEWS_HISTORY_LIMIT:]
            common._safe_json_write(history_file, {"updated_at": now.isoformat(), "entries": entries})

    def _pruned_recent_news_entries(self, history: Any, now: datetime) -> list[dict[str, Any]]:
        raw_entries = history.get("entries") if isinstance(history, dict) else []
        if not isinstance(raw_entries, list):
            return []
        cutoff = (now - timedelta(days=RECENT_NEWS_HISTORY_DAYS)).date()
        entries = []
        for entry in raw_entries:
            if not isinstance(entry, dict):
                continue
            entry_date = self._history_entry_date(entry)
            if entry_date is not None and entry_date < cutoff:
                continue
            title = str(entry.get("title") or "").strip()
            if title:
                entries.append(entry)
        return entries[-RECENT_NEWS_HISTORY_LIMIT:]

    @staticmethod
    def _history_entry_date(entry: dict[str, Any]):
        raw = str(entry.get("date") or "")[:10]
        try:
            return datetime.fromisoformat(raw).date()
        except Exception:
            return None

    @staticmethod
    def _news_history_key(title: Any) -> str:
        return re.sub(r"[\W_]+", "", str(title or ""), flags=re.UNICODE).casefold()

    def _matches_recent_news_title(self, title: str, recent_titles: list[str] | None) -> bool:
        title_key = self._news_history_key(title)
        if not title_key:
            return False
        for recent_title in recent_titles or []:
            recent_key = self._news_history_key(recent_title)
            if title_key == recent_key or self._similar_news_title(str(title or ""), str(recent_title or "")):
                return True
        return False

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
        feeds = self._parse_feeds(feeds_text)[:DEFAULT_MAX_FEEDS]
        if not feeds:
            return items
        per_feed_limit = max(3, min(8, (max_items * 2 + len(feeds) - 1) // len(feeds)))
        entry_scan_limit = max(12, per_feed_limit * 2)
        for source, url in feeds:
            display_source, section = self._feed_source_and_section(source, url)
            try:
                resp = get_http_client().request_bytes(
                    "GET",
                    url,
                    timeout=DEFAULT_FEED_FETCH_TIMEOUT_SECONDS,
                    headers={"User-Agent": "InkyPi Daily AI News/1.0"},
                    max_bytes=4 * 1024 * 1024,
                )
                feed = feedparser.parse(resp.data)
            except Exception as exc:
                logger.warning("RSS fetch failed for %s: %s", url, exc)
                continue

            per_feed = 0
            for entry in feed.entries[:entry_scan_limit]:
                title = common._clean_text(entry.get("title", ""), 150)
                if not title:
                    continue
                key = re.sub(r"\W+", "", title.lower())
                if key in seen:
                    continue
                seen.add(key)
                items.append({
                    "source": display_source,
                    "section": section,
                    "title": title,
                    "summary": common._clean_text(
                        entry.get("summary", "") or entry.get("description", ""),
                        280,
                    ),
                    "published": common._clean_text(entry.get("published", "") or entry.get("updated", ""), 80),
                    "link": common._clean_text(entry.get("link", ""), 220),
                })
                per_feed += 1
                if per_feed >= per_feed_limit:
                    break
        return items

    def _drop_stale_items(self, items: list[dict[str, str]], now: datetime) -> list[dict[str, str]]:
        reference = now if now.tzinfo is not None else now.replace(tzinfo=pytz.UTC)
        max_age = timedelta(days=MAX_NEWS_ITEM_AGE_DAYS)
        kept = []
        for item in items:
            published = self._parse_published(str(item.get("published") or ""))
            if published is not None and reference - published.astimezone(reference.tzinfo) > max_age:
                continue
            kept.append(item)
        return kept

    def _drop_recently_shown_items(
        self,
        items: list[dict[str, str]],
        recent_titles: list[str] | None,
    ) -> list[dict[str, str]]:
        if not recent_titles:
            return list(items)
        fresh: list[dict[str, str]] = []
        repeats: list[dict[str, str]] = []
        fresh_counts = {"mainland": 0, "world": 0}
        for item in items:
            if self._matches_recent_news_title(str(item.get("title") or ""), recent_titles):
                repeats.append(item)
            else:
                fresh.append(item)
                fresh_counts[self._item_section(item)] += 1
        if not repeats:
            return fresh
        # A section keeps recently-shown candidates only while it lacks enough
        # fresh material; otherwise repeats are cut so each day reads new.
        section_minimums = {"mainland": MAINLAND_NEWS_MAX + 2, "world": WORLD_NEWS_MAX + 2}
        kept_repeats = [
            item
            for item in repeats
            if fresh_counts[self._item_section(item)] < section_minimums[self._item_section(item)]
        ]
        return fresh + kept_repeats

    def _rank_news_items(
        self,
        items: list[dict[str, str]],
        now: datetime,
        recent_titles: list[str] | None = None,
    ) -> list[dict[str, str]]:
        recent_titles = list(recent_titles or [])

        def score(index: int, item: dict[str, str]) -> float:
            title = item.get("title", "")
            summary = item.get("summary", "")
            text = f"{title} {summary}".lower()
            value = 100 - index * 0.05

            published = self._parse_published(item.get("published", ""))
            if published:
                age_now = now
                if age_now.tzinfo is None:
                    age_now = age_now.replace(tzinfo=pytz.UTC)
                age_hours = max(0.0, (age_now - published.astimezone(age_now.tzinfo)).total_seconds() / 3600)
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
            if self._matches_recent_news_title(title, recent_titles):
                value -= 70
            if not self._has_cjk(title):
                value -= 5
            return value

        return [item for _score, item in sorted(
            ((score(index, item), item) for index, item in enumerate(items)),
            key=lambda pair: pair[0],
            reverse=True,
        )]

    def _rss_items_need_translation(self, items: list[dict[str, str]]) -> bool:
        titled_items = [item for item in items if str(item.get("title") or "").strip()]
        if not titled_items:
            return False
        non_cjk_count = sum(1 for item in titled_items if not self._has_cjk(str(item.get("title") or "")))
        return non_cjk_count > len(titled_items) / 2

    @staticmethod
    def _diversify_news_items(items: list[dict[str, str]], max_items: int) -> list[dict[str, str]]:
        if not items or max_items <= 0:
            return []
        buckets: dict[str, list[dict[str, str]]] = {}
        for item in items:
            source = str(item.get("source") or "").strip() or "_unknown"
            buckets.setdefault(source, []).append(item)
        if len(buckets) <= 1:
            return items[:max_items]

        selected = []
        sources = list(buckets)
        while len(selected) < max_items:
            made_progress = False
            for source in sources:
                bucket = buckets[source]
                if not bucket:
                    continue
                selected.append(bucket.pop(0))
                made_progress = True
                if len(selected) >= max_items:
                    break
            if not made_progress:
                break
        return selected

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
