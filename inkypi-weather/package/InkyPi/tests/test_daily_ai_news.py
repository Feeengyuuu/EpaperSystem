import hashlib
import json
import sys
import threading
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageStat
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import plugins.daily_ai_news.daily_ai_news as daily_ai_news_module
from plugins.daily_ai_news.daily_ai_news import DailyAINews, TITLE_BACKGROUND_IMAGE, TITLE_BACKGROUND_SIZE, TITLE_WORDMARK_IMAGE, TITLE_WORDMARK_SIZE
from plugins.daily_ai_news import news_art, news_brief, news_common, news_layout, news_market, news_sources
from plugins.base_plugin.presentation import PresentationMode
from plugins.base_plugin.render_provenance import read_source_provenance


def _plugin():
    return DailyAINews({"id": "daily_ai_news"})


class _ThemeDeviceConfig:
    def get_resolution(self):
        return (800, 480)

    def get_config(self, key=None, default=None):
        values = {
            "orientation": "horizontal",
            "timezone": "America/Los_Angeles",
            "theme_mode": "day",
        }
        if key is None:
            return values
        return values.get(key, default)

    def load_env_key(self, _key):
        return ""


def _canonical_theme(mode, *, background, panel, ink, muted, rule, accent):
    palette = {
        "background": background,
        "panel": panel,
        "ink": ink,
        "muted": muted,
        "rule": rule,
        "accent": accent,
    }
    return {"mode": mode, "palette": palette, "css": {}}


def test_ai_client_has_bounded_timeout_and_no_automatic_retries(monkeypatch):
    plugin = _plugin()
    created = {}

    class _Response:
        output_text = '{"lede":"今日更新","mainland":[],"world":[],"sources":[]}'

    class _Responses:
        @staticmethod
        def create(**_kwargs):
            return _Response()

    class _Client:
        responses = _Responses()

    def fake_openai(**kwargs):
        created.update(kwargs)
        return _Client()

    monkeypatch.setattr(news_brief, "OpenAI", fake_openai)

    plugin._summarize_with_openai(
        "fake-key",
        "gpt-5-nano",
        {},
        [],
        {},
        datetime(2026, 7, 15, 2, 0),
    )

    assert created["timeout"] == news_brief.DEFAULT_AI_TIMEOUT_SECONDS
    assert created["max_retries"] == 0


def test_effective_feeds_expands_legacy_bbc_only_settings():
    plugin = _plugin()
    feeds_text = "BBC World|https://feeds.bbci.co.uk/news/world/rss.xml"

    effective = plugin._effective_feeds_text(feeds_text)
    feeds = plugin._parse_feeds(effective)
    urls = [url for _name, url in feeds]

    assert urls.count("https://feeds.bbci.co.uk/news/world/rss.xml") == 1
    assert "https://www.chinanews.com.cn/rss/importnews.xml" in urls
    assert "https://www.chinanews.com.cn/rss/china.xml" in urls
    assert "https://www.aljazeera.com/xml/rss/all.xml" in urls
    assert "https://www.france24.com/en/rss" in urls
    assert "https://rss.dw.com/rdf/rss-en-all" in urls
    assert "https://feeds.bbci.co.uk/zhongwen/simp/rss.xml" not in urls
    assert len(feeds) >= 12


def test_effective_feeds_expands_previous_default_feed_set():
    plugin = _plugin()

    effective = plugin._effective_feeds_text(news_sources.LEGACY_DEFAULT_FEEDS)
    urls = {url for _name, url in plugin._parse_feeds(effective)}

    assert "https://www.chinanews.com.cn/rss/importnews.xml" in urls
    assert "https://www.chinanews.com.cn/rss/china.xml" in urls
    assert "https://www.chinanews.com.cn/rss/scroll-news.xml" in urls
    assert "https://www.pbs.org/newshour/feeds/rss/headlines" in urls
    assert "https://abcnews.go.com/abcnews/internationalheadlines" in urls


def test_effective_feeds_expands_world_only_previous_default_feed_set():
    plugin = _plugin()

    effective = plugin._effective_feeds_text(news_sources.LEGACY_WORLD_ONLY_DEFAULT_FEEDS)
    urls = {url for _name, url in plugin._parse_feeds(effective)}

    assert "https://www.chinanews.com.cn/rss/importnews.xml" in urls
    assert "https://www.chinanews.com.cn/rss/china.xml" in urls
    assert "https://feeds.bbci.co.uk/news/world/rss.xml" in urls


def test_effective_feeds_upgrades_saved_regional_default_with_generic_bbc_chinese():
    plugin = _plugin()
    saved_old_default = """大陆新闻:新华网时政|https://www.news.cn/politics/news_politics.xml
大陆新闻:人民网时政|https://www.people.com.cn/rss/politics.xml
大陆新闻:中国新闻网国内|https://www.chinanews.com.cn/rss/china.xml
世界新闻:BBC中文|https://feeds.bbci.co.uk/zhongwen/simp/rss.xml
世界新闻:BBC世界|https://feeds.bbci.co.uk/news/world/rss.xml
世界新闻:NPR新闻|https://feeds.npr.org/1001/rss.xml
世界新闻:纽约时报国际|https://rss.nytimes.com/services/xml/rss/nyt/World.xml
世界新闻:卫报国际|https://www.theguardian.com/world/rss
世界新闻:半岛电视台|https://www.aljazeera.com/xml/rss/all.xml
世界新闻:法国24|https://www.france24.com/en/rss
世界新闻:德国之声|https://rss.dw.com/rdf/rss-en-all
世界新闻:PBS新闻一小时|https://www.pbs.org/newshour/feeds/rss/headlines
世界新闻:ABC国际|https://abcnews.go.com/abcnews/internationalheadlines"""

    effective = plugin._effective_feeds_text(saved_old_default)
    urls = {url for _name, url in plugin._parse_feeds(effective)}

    assert "https://feeds.bbci.co.uk/zhongwen/simp/rss.xml" not in urls
    assert "https://feeds.bbci.co.uk/news/world/rss.xml" in urls
    assert "https://www.chinanews.com.cn/rss/scroll-news.xml" in urls


def test_effective_feeds_upgrades_saved_regional_default_missing_instant_mainland_feed():
    plugin = _plugin()
    saved_previous_default = """大陆新闻:新华网时政|https://www.news.cn/politics/news_politics.xml
大陆新闻:人民网时政|https://www.people.com.cn/rss/politics.xml
大陆新闻:中国新闻网国内|https://www.chinanews.com.cn/rss/china.xml
世界新闻:BBC世界|https://feeds.bbci.co.uk/news/world/rss.xml
世界新闻:NPR新闻|https://feeds.npr.org/1001/rss.xml
世界新闻:纽约时报国际|https://rss.nytimes.com/services/xml/rss/nyt/World.xml
世界新闻:卫报国际|https://www.theguardian.com/world/rss
世界新闻:半岛电视台|https://www.aljazeera.com/xml/rss/all.xml
世界新闻:法国24|https://www.france24.com/en/rss
世界新闻:德国之声|https://rss.dw.com/rdf/rss-en-all
世界新闻:PBS新闻一小时|https://www.pbs.org/newshour/feeds/rss/headlines
世界新闻:ABC国际|https://abcnews.go.com/abcnews/internationalheadlines"""

    effective = plugin._effective_feeds_text(saved_previous_default)
    urls = {url for _name, url in plugin._parse_feeds(effective)}

    assert "https://www.chinanews.com.cn/rss/scroll-news.xml" in urls


def test_default_feeds_tag_mainland_and_world_sections():
    plugin = _plugin()
    feeds = plugin._parse_feeds(news_sources.DEFAULT_FEEDS)
    sections = [plugin._feed_source_and_section(name, url) for name, url in feeds]

    assert ("中国新闻网要闻", "mainland") in sections
    assert ("中国新闻网国内", "mainland") in sections
    assert ("中国新闻网即时", "mainland") in sections
    assert ("BBC世界", "world") in sections
    assert ("半岛电视台", "world") in sections


def test_default_mainland_feeds_replace_dead_sources_with_fresh_desks():
    plugin = _plugin()
    urls = {url for _name, url in plugin._parse_feeds(news_sources.DEFAULT_MAINLAND_FEEDS)}

    # 新华网时政 froze in 2022 and 人民网时政 froze in 2025-06; both keep serving
    # the same stale entries forever, which is what caused repeated mainland news.
    assert "https://www.news.cn/politics/news_politics.xml" not in urls
    assert "https://www.people.com.cn/rss/politics.xml" not in urls
    assert "https://www.chinanews.com.cn/rss/importnews.xml" in urls
    assert "https://www.chinanews.com.cn/rss/finance.xml" in urls
    assert "https://www.chinanews.com.cn/rss/society.xml" in urls
    assert "https://www.chinanews.com.cn/rss/china.xml" in urls
    assert "https://www.chinanews.com.cn/rss/scroll-news.xml" in urls


def test_effective_feeds_upgrades_saved_default_containing_dead_mainland_feeds():
    plugin = _plugin()
    saved_default_with_dead_feeds = """大陆新闻:新华网时政|https://www.news.cn/politics/news_politics.xml
大陆新闻:人民网时政|https://www.people.com.cn/rss/politics.xml
大陆新闻:中国新闻网国内|https://www.chinanews.com.cn/rss/china.xml
大陆新闻:中国新闻网即时|https://www.chinanews.com.cn/rss/scroll-news.xml
世界新闻:BBC世界|https://feeds.bbci.co.uk/news/world/rss.xml
世界新闻:NPR新闻|https://feeds.npr.org/1001/rss.xml
世界新闻:纽约时报国际|https://rss.nytimes.com/services/xml/rss/nyt/World.xml
世界新闻:卫报国际|https://www.theguardian.com/world/rss
世界新闻:半岛电视台|https://www.aljazeera.com/xml/rss/all.xml
世界新闻:法国24|https://www.france24.com/en/rss
世界新闻:德国之声|https://rss.dw.com/rdf/rss-en-all
世界新闻:PBS新闻一小时|https://www.pbs.org/newshour/feeds/rss/headlines
世界新闻:ABC国际|https://abcnews.go.com/abcnews/internationalheadlines"""

    effective = plugin._effective_feeds_text(saved_default_with_dead_feeds)
    urls = {url for _name, url in plugin._parse_feeds(effective)}

    assert "https://www.news.cn/politics/news_politics.xml" not in urls
    assert "https://www.people.com.cn/rss/politics.xml" not in urls
    assert "https://www.chinanews.com.cn/rss/importnews.xml" in urls
    assert "https://feeds.bbci.co.uk/news/world/rss.xml" in urls


def test_effective_feeds_preserves_custom_non_legacy_settings():
    plugin = _plugin()
    custom = "Custom Source|https://example.com/custom.xml"

    assert plugin._effective_feeds_text(custom) == custom


def test_fetch_items_samples_all_configured_sources(monkeypatch):
    plugin = _plugin()
    urls = [f"https://example.com/feed{i}.xml" for i in range(5)]
    feeds_text = "\n".join(f"Source {i}|{url}" for i, url in enumerate(urls))
    calls = []

    class FakeClient:
        def request_bytes(self, _method, url, **_kwargs):
            calls.append(url)
            return type("Result", (), {"data": url.encode("utf-8")})()

    def fake_parse(content):
        url = content.decode("utf-8")
        entries = [
            {
                "title": f"{url} story {index}",
                "summary": "summary",
                "published": "",
                "link": url,
            }
            for index in range(4)
        ]
        return type("FakeFeed", (), {"entries": entries})()

    monkeypatch.setattr(
        news_sources,
        "get_http_client",
        lambda: FakeClient(),
    )
    monkeypatch.setattr(news_sources.feedparser, "parse", fake_parse)

    items = plugin._fetch_items(feeds_text, 6)

    assert calls == urls
    assert {item["source"] for item in items} == {f"Source {index}" for index in range(5)}


def test_fetch_items_strips_source_section_prefix_and_tags_items(monkeypatch):
    plugin = _plugin()
    feeds_text = "\n".join([
        "大陆新闻:新华网时政|https://www.news.cn/politics/news_politics.xml",
        "世界新闻:BBC世界|https://feeds.bbci.co.uk/news/world/rss.xml",
    ])

    class FakeClient:
        def request_bytes(self, _method, url, **_kwargs):
            return type("Result", (), {"data": url.encode("utf-8")})()

    def fake_parse(content):
        url = content.decode("utf-8")
        return type("FakeFeed", (), {"entries": [{"title": f"{url} 标题", "summary": "摘要", "published": "", "link": url}]})()

    monkeypatch.setattr(
        news_sources,
        "get_http_client",
        lambda: FakeClient(),
    )
    monkeypatch.setattr(news_sources.feedparser, "parse", fake_parse)

    items = plugin._fetch_items(feeds_text, 4)

    assert [(item["source"], item["section"]) for item in items] == [("新华网时政", "mainland"), ("BBC世界", "world")]


def test_diversify_news_items_limits_single_source_dominance():
    items = [
        *({"source": "BBC", "title": f"BBC story {index}"} for index in range(10)),
        *({"source": "NPR", "title": f"NPR story {index}"} for index in range(2)),
        *({"source": "DW", "title": f"DW story {index}"} for index in range(2)),
    ]

    selected = DailyAINews._diversify_news_items(items, 6)

    assert len(selected) == 6
    assert sum(1 for item in selected if item["source"] == "BBC") == 2
    assert sum(1 for item in selected if item["source"] == "NPR") == 2
    assert sum(1 for item in selected if item["source"] == "DW") == 2
    assert {item["source"] for item in selected} >= {"NPR", "DW"}


def test_rank_news_items_accepts_naive_now_with_timezone_published_date():
    plugin = _plugin()
    items = [
        {
            "source": "BBC",
            "title": "Major world event",
            "summary": "",
            "published": "Wed, 17 Jun 2026 12:00:00 GMT",
        }
    ]

    ranked = plugin._rank_news_items(items, datetime(2026, 6, 17, 13, 0, 0))

    assert ranked == items


def test_rank_news_items_demotes_recently_displayed_titles():
    plugin = _plugin()
    now = datetime(2026, 7, 5, 8, 0, 0)
    items = [
        {
            "source": "中国新闻网即时",
            "section": "mainland",
            "title": "三门峡水库增流至三千二百立方米每秒",
            "summary": "发布 调度 水库 防汛",
            "published": "Sun, 05 Jul 2026 07:30:00 GMT",
        },
        {
            "source": "新华网时政",
            "section": "mainland",
            "title": "国务院部署新能源项目审批改革",
            "summary": "发布 政府 政策 调整",
            "published": "Sun, 05 Jul 2026 07:00:00 GMT",
        },
    ]

    ranked = plugin._rank_news_items(items, now, ["三门峡水库增流至三千二百立方米每秒"])

    assert ranked[0]["title"] == "国务院部署新能源项目审批改革"


def test_drop_stale_items_removes_entries_older_than_max_age():
    plugin = _plugin()
    now = datetime(2026, 7, 5, 8, 0, 0)
    fresh = {
        "source": "中国新闻网国内",
        "section": "mainland",
        "title": "王毅同芬兰外长瓦尔托宁会谈",
        "summary": "",
        "published": "Sun, 05 Jul 2026 07:30:00 GMT",
    }
    stale = {
        "source": "人民网时政",
        "section": "mainland",
        "title": "镜观·足迹｜呵护千山万水擘画永续发展",
        "summary": "",
        "published": "Thu, 05 Jun 2025 10:00:00 GMT",
    }
    undated = {
        "source": "新华网时政",
        "section": "mainland",
        "title": "微视频｜新在中国",
        "summary": "",
        "published": "",
    }

    kept = plugin._drop_stale_items([fresh, stale, undated], now)

    assert fresh in kept
    assert stale not in kept
    assert undated in kept


def test_drop_recently_shown_items_excludes_repeats_when_section_pool_is_fresh():
    plugin = _plugin()
    repeat = {
        "source": "新华网时政",
        "section": "mainland",
        "title": "国家卫健委发布新冠病毒疫苗第二剂次加强免疫接种实施方案",
        "summary": "",
        "published": "",
    }
    fresh_items = [
        {
            "source": "中国新闻网国内",
            "section": "mainland",
            "title": f"大陆新鲜时政要闻第{index}条内容更新",
            "summary": "",
            "published": "Sun, 05 Jul 2026 07:30:00 GMT",
        }
        # Two more fresh stories than the column shows is a fresh pool.
        for index in range(1, daily_ai_news_module.MAINLAND_NEWS_MAX + 3)
    ]

    result = plugin._drop_recently_shown_items(
        fresh_items + [repeat],
        ["国家卫健委发布新冠病毒疫苗第二剂次加强免疫接种实施方案"],
    )

    assert repeat not in result
    assert result == fresh_items


def test_drop_recently_shown_items_keeps_repeats_when_section_pool_is_thin():
    plugin = _plugin()
    repeat = {
        "source": "新华网时政",
        "section": "mainland",
        "title": "国家卫健委发布新冠病毒疫苗第二剂次加强免疫接种实施方案",
        "summary": "",
        "published": "",
    }
    fresh_items = [
        {
            "source": "中国新闻网国内",
            "section": "mainland",
            "title": f"大陆新鲜时政要闻第{index}条内容更新",
            "summary": "",
            "published": "Sun, 05 Jul 2026 07:30:00 GMT",
        }
        for index in range(1, 3)
    ]

    result = plugin._drop_recently_shown_items(
        [repeat] + fresh_items,
        ["国家卫健委发布新冠病毒疫苗第二剂次加强免疫接种实施方案"],
    )

    assert repeat in result
    # thin pool keeps the repeat available, but fresh candidates come first
    assert result[:2] == fresh_items


def test_get_brief_uses_new_rss_when_api_limit_blocks_stale_cache(monkeypatch, tmp_path):
    plugin = _plugin()
    cache_file = tmp_path / "brief.json"
    cache_file.write_text(
        json.dumps(
            {
                "cache_key": "old-cache-key",
                "brief": {"top": [{"title": "old", "why": "old"}], "sources": ["BBC"]},
                "items": [{"source": "BBC", "title": "old"}],
            }
        ),
        encoding="utf-8",
    )

    class FakeDeviceConfig:
        def load_env_key(self, key):
            return "fake-key" if key == "OPENAI_API_KEY" else ""

    fresh_items = [
        {"source": "半岛电视台", "title": "新的中文新闻一", "summary": "摘要", "published": "", "link": ""},
        {"source": "法国24", "title": "新的中文新闻二", "summary": "摘要", "published": "", "link": ""},
    ]

    monkeypatch.setattr(plugin, "_cache_dir", lambda: tmp_path)
    monkeypatch.setattr(plugin, "_fetch_items", lambda _feeds_text, _max_items: fresh_items)
    monkeypatch.setattr(plugin, "_fetch_market_snapshot", lambda _now, _device_config: {})
    monkeypatch.setattr(plugin, "_allow_api_call", lambda _settings, _date_key: False)

    brief = plugin._get_brief(
        {"model": "gpt-5-nano", "feed_urls": news_sources.DEFAULT_FEEDS, "max_items": "6"},
        FakeDeviceConfig(),
        datetime(2026, 6, 17),
    )

    assert brief["from_cache"] is False
    assert {item["source"] for item in brief["items"]} == {"半岛电视台", "法国24"}
    assert brief["brief"]["sources"] == ["半岛电视台", "法国24"]
    assert "RSS" in brief["warning"]


def test_get_brief_keeps_stale_chinese_cache_instead_of_showing_english_rss(monkeypatch, tmp_path):
    plugin = _plugin()
    cache_file = tmp_path / "brief.json"
    cache_file.write_text(
        json.dumps(
            {
                "cache_key": "old-cache-key",
                "brief": {"top": [{"title": "旧中文新闻", "why": "中文摘要"}], "sources": ["BBC中文"]},
                "items": [{"source": "BBC中文", "title": "旧中文新闻"}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    class FakeDeviceConfig:
        def load_env_key(self, key):
            return "fake-key" if key == "OPENAI_API_KEY" else ""

    english_items = [
        {"source": "BBC世界", "title": "English headline one", "summary": "", "published": "", "link": ""},
        {"source": "NPR新闻", "title": "English headline two", "summary": "", "published": "", "link": ""},
    ]

    monkeypatch.setattr(plugin, "_cache_dir", lambda: tmp_path)
    monkeypatch.setattr(plugin, "_fetch_items", lambda _feeds_text, _max_items: english_items)
    monkeypatch.setattr(plugin, "_fetch_market_snapshot", lambda _now, _device_config: {})
    monkeypatch.setattr(plugin, "_allow_api_call", lambda _settings, _date_key: False)

    brief = plugin._get_brief(
        {"model": "gpt-5-nano", "feed_urls": news_sources.DEFAULT_FEEDS, "max_items": "6"},
        FakeDeviceConfig(),
        datetime(2026, 6, 17),
    )

    assert brief["from_cache"] is True
    assert brief["brief"]["top"][0]["title"] == "旧中文新闻"
    assert "中文缓存" in brief["warning"]


def test_forced_refresh_treats_today_policy_limited_cache_as_fresh(monkeypatch, tmp_path):
    plugin = _plugin()
    now = datetime(2026, 6, 17, 8, 0)
    settings = {
        "model": "gpt-5-nano",
        "feed_urls": news_sources.DEFAULT_FEEDS,
        "max_items": "6",
        "force_refresh": "true",
    }
    cache_key = plugin._cache_key(
        "2026-06-17",
        "gpt-5-nano",
        plugin._effective_feeds_text(settings["feed_urls"]),
        6,
        None,
    )
    (tmp_path / "brief.json").write_text(
        json.dumps(
            {
                "cache_key": cache_key,
                "date": "2026-06-17",
                "generated_at": "2026-06-17T07:41:00",
                "brief": {"top": [{"title": "今日新闻", "why": "今日摘要"}]},
                "items": [{"source": "BBC", "title": "今日新闻"}],
            }
        ),
        encoding="utf-8",
    )

    class FakeDeviceConfig:
        def load_env_key(self, _key):
            return "configured"

    monkeypatch.setattr(plugin, "_cache_dir", lambda: tmp_path)
    monkeypatch.setattr(
        plugin,
        "_fetch_items",
        lambda *_args: [
            {"source": "BBC", "title": "Fresh English", "summary": "Details", "published": "", "link": ""}
        ],
    )
    monkeypatch.setattr(plugin, "_fetch_market_snapshot", lambda *_args: {})
    monkeypatch.setattr(plugin, "_allow_api_call", lambda *_args: False)

    brief = plugin._get_brief(settings, FakeDeviceConfig(), now)

    assert brief["from_cache"] is True
    assert brief["_source_provenance"] == "fresh_cache"
    assert brief["brief"]["top"][0]["title"] == "今日新闻"
    # Reusing today's brief under the API limit is normal, so it is a notice, not a warning.
    assert "warning" not in brief
    assert "限额" in brief["notice"]


def test_base_background_uses_plain_theme_color_in_night_mode():
    plugin = _plugin()
    bg = (7, 11, 13)

    img = plugin._base_background((8, 6), bg, "night")

    assert img.getpixel((0, 0)) == bg
    assert img.getpixel((7, 5)) == bg


def test_night_palette_forces_pure_black_canvas_and_header():
    palette = DailyAINews._render_palette(
        _canonical_theme(
            "night",
            background=(5, 20, 18),
            panel=(8, 26, 23),
            ink=(236, 242, 241),
            muted=(160, 188, 181),
            rule=(38, 79, 70),
            accent=(91, 218, 190),
        )
    )

    assert palette["background"] == (0, 0, 0)
    assert palette["header"] == (0, 0, 0)


def test_theme_only_warm_brief_uses_injected_palette_without_provider_calls(monkeypatch, tmp_path):
    plugin = _plugin()
    device = _ThemeDeviceConfig()
    monkeypatch.setattr(plugin, "_cache_dir", lambda create=True: tmp_path)
    items = [
        {
            "source": "中国新闻网要闻" if index < 4 else "BBC世界",
            "source_section": "mainland" if index < 4 else "world",
            "title": f"测试新闻 {index}",
            "summary": f"测试摘要 {index}",
            "published": "",
            "link": "",
        }
        for index in range(8)
    ]
    monkeypatch.setattr(plugin, "_fetch_items", lambda *_args: items)
    monkeypatch.setattr(plugin, "_fetch_market_snapshot", lambda *_args: {})
    settings = {"feed_urls": news_sources.DEFAULT_FEEDS, "max_items": "8"}

    plugin.generate_image(settings, device)

    def fail_provider(*_args, **_kwargs):
        raise AssertionError("theme-only redraw must not call a provider")

    monkeypatch.setattr(plugin, "_fetch_items", fail_provider)
    monkeypatch.setattr(plugin, "_fetch_market_snapshot", fail_provider)
    monkeypatch.setattr(plugin, "_summarize_with_ai", fail_provider)
    day = _canonical_theme(
        "day",
        background=(242, 237, 226),
        panel=(224, 216, 198),
        ink=(20, 22, 24),
        muted=(72, 74, 78),
        rule=(130, 126, 118),
        accent=(176, 42, 54),
    )
    night = _canonical_theme(
        "night",
        background=(7, 9, 12),
        panel=(22, 26, 32),
        ink=(245, 246, 248),
        muted=(178, 182, 190),
        rule=(58, 64, 72),
        accent=(76, 190, 238),
    )

    day_image = plugin.generate_image({**settings, "_theme_render_only": True, "_inkypi_theme": day}, device)
    night_image = plugin.generate_image({**settings, "_theme_render_only": True, "_inkypi_theme": night}, device)

    original_day = daily_ai_news_module.get_theme_palette("day")
    assert day_image.getpixel((0, 479)) == original_day["background"]
    assert day_image.getpixel((0, 0)) == original_day["header"]
    assert night_image.getpixel((0, 479)) == (0, 0, 0)
    assert night_image.getpixel((0, 0)) == (0, 0, 0)
    assert hashlib.sha256(day_image.tobytes()).digest() != hashlib.sha256(night_image.tobytes()).digest()


def test_theme_only_brief_cache_miss_fails_without_provider_calls(monkeypatch, tmp_path):
    plugin = _plugin()
    monkeypatch.setattr(plugin, "_cache_dir", lambda create=True: tmp_path)
    calls = {"items": 0}

    def fake_items(*_args):
        calls["items"] += 1
        return []

    monkeypatch.setattr(plugin, "_fetch_items", fake_items)

    with pytest.raises(RuntimeError, match="warm .*cache"):
        plugin._get_brief(
            {"_theme_render_only": True, "feed_urls": news_sources.DEFAULT_FEEDS},
            _ThemeDeviceConfig(),
            datetime(2026, 7, 11, 9, 0),
        )

    assert calls == {"items": 0}


def test_theme_only_generate_propagates_cold_cache_failure(monkeypatch, tmp_path):
    plugin = _plugin()
    monkeypatch.setattr(plugin, "_cache_dir", lambda create=True: tmp_path)

    def fail_provider(*_args, **_kwargs):
        raise AssertionError("theme-only redraw must not call a provider")

    monkeypatch.setattr(plugin, "_fetch_items", fail_provider)

    with pytest.raises(RuntimeError, match="warm .*cache"):
        plugin.generate_image(
            {
                "_theme_render_only": True,
                "_inkypi_theme": _canonical_theme(
                    "day",
                    background=(255, 255, 255),
                    panel=(255, 255, 255),
                    ink=(10, 12, 15),
                    muted=(74, 78, 84),
                    rule=(185, 188, 194),
                    accent=(24, 92, 150),
                ),
            },
            _ThemeDeviceConfig(),
        )


def test_market_snapshot_prefers_massive_and_keeps_yahoo_fallback(monkeypatch):
    plugin = _plugin()

    class FakeMassiveClient:
        def __init__(self, api_key):
            assert api_key == "massive-key"

        def fetch_treasury_yields(self, limit=1):
            return [{"date": "2026-06-02", "yield_2_year": 4.1, "yield_10_year": 4.3}]

        def fetch_quote(self, symbol, name):
            if symbol == "^GSPC":
                return {
                    "symbol": symbol,
                    "name": name,
                    "price": 6000.0,
                    "change": 30.0,
                    "change_pct": 0.5,
                    "as_of": "2026-06-02",
                    "source": "massive",
                    "massive_symbol": "I:SPX",
                }
            return None

    yahoo_calls = []

    def fake_yahoo(symbol, name):
        yahoo_calls.append(symbol)
        return {
            "symbol": symbol,
            "name": name,
            "price": 100.0,
            "change_pct": 1.0,
            "as_of": "2026-06-02",
            "source": "yahoo",
            "closes": [99.0, 100.0],
        }

    monkeypatch.setattr("plugins.daily_ai_news.news_market.load_massive_api_key", lambda device_config: "massive-key")
    monkeypatch.setattr("plugins.daily_ai_news.news_market.MassiveMarketData", FakeMassiveClient)
    monkeypatch.setattr(plugin, "_fetch_yahoo_quote", fake_yahoo)

    snapshot = plugin._fetch_market_snapshot(datetime(2026, 6, 3), object())

    assert snapshot["macro"] == {
        "source": "massive",
        "treasury_yields": [{"date": "2026-06-02", "yield_2_year": 4.1, "yield_10_year": 4.3}],
    }
    assert snapshot["groups"]["us_stock"][0]["source"] == "massive"
    assert snapshot["groups"]["us_stock"][0]["massive_symbol"] == "I:SPX"
    assert snapshot["groups"]["us_stock"][0]["price"] == 6000.0
    assert snapshot["groups"]["us_stock"][0]["change_pct"] == 0.5
    # Massive has no history; Yahoo's daily closes only feed the sparkline.
    assert snapshot["groups"]["us_stock"][0]["closes"] == [99.0, 100.0]
    assert "^IXIC" in yahoo_calls
    assert "000001.SS" in yahoo_calls


def test_market_snapshot_rejects_massive_etf_proxy_for_index(monkeypatch):
    plugin = _plugin()

    class FakeMassiveClient:
        def __init__(self, api_key):
            assert api_key == "massive-key"

        def fetch_treasury_yields(self, limit=1):
            return []

        def fetch_quote(self, symbol, name):
            if symbol == "^GSPC":
                return {"symbol": symbol, "name": name, "price": 746.74, "change_pct": 0.8, "as_of": "2026-06-18", "source": "massive", "massive_symbol": "SPY"}
            if symbol == "^IXIC":
                return {"symbol": symbol, "name": name, "price": 19944.2, "change_pct": 0.6, "as_of": "2026-06-18", "source": "massive", "massive_symbol": "I:COMP"}
            if symbol == "^DJI":
                return {"symbol": symbol, "name": name, "price": 515.52, "change_pct": -0.1, "as_of": "2026-06-18", "source": "massive", "massive_symbol": "DIA"}
            return None

    def fake_yahoo(symbol, name):
        prices = {"^GSPC": 6200.0, "^DJI": 43000.0}
        # Same session as Massive, so an accepted Massive index quote is kept.
        return {"symbol": symbol, "name": name, "price": prices.get(symbol, 100.0), "change_pct": 1.0, "as_of": "2026-06-18", "source": "yahoo"}

    monkeypatch.setattr("plugins.daily_ai_news.news_market.load_massive_api_key", lambda device_config: "massive-key")
    monkeypatch.setattr("plugins.daily_ai_news.news_market.MassiveMarketData", FakeMassiveClient)
    monkeypatch.setattr(plugin, "_fetch_yahoo_quote", fake_yahoo)

    snapshot = plugin._fetch_market_snapshot(datetime(2026, 6, 19), object())
    rows = snapshot["groups"]["us_stock"]

    assert rows[0]["symbol"] == "^GSPC"
    assert rows[0]["source"] == "yahoo"
    assert rows[0]["price"] == 6200.0
    assert rows[1]["source"] == "massive"
    assert rows[1]["massive_symbol"] == "I:COMP"
    assert rows[2]["symbol"] == "^DJI"
    assert rows[2]["source"] == "yahoo"
    assert rows[2]["price"] == 43000.0


def test_market_snapshot_fetches_independent_quotes_concurrently(monkeypatch):
    plugin = _plugin()
    barrier = threading.Barrier(2)

    monkeypatch.setattr(
        "plugins.daily_ai_news.news_market.load_massive_api_key",
        lambda _device_config: "",
    )

    def paired_yahoo(symbol, name):
        barrier.wait(timeout=1.0)
        return {
            "symbol": symbol,
            "name": name,
            "price": 100.0,
            "change_pct": 1.0,
            "as_of": "2026-07-20",
            "source": "yahoo",
        }

    monkeypatch.setattr(plugin, "_fetch_yahoo_quote", paired_yahoo)

    snapshot = plugin._fetch_market_snapshot(datetime(2026, 7, 20), object())

    assert sum(len(rows) for rows in snapshot["groups"].values()) == 6


def test_parse_brief_json_repairs_model_trailing_commas():
    plugin = _plugin()
    content = """
    ```json
    {
      "lede": "今日硬新闻更新",
      "top": [
        {"title": "欧洲议会通过新法案", "why": "监管压力继续上升",}
      ],
      "sources": ["BBC中文",],
    }
    ```
    """

    brief = plugin._parse_brief_json(content)

    assert brief["lede"] == "今日硬新闻更新"
    assert brief["top"] == [{"title": "欧洲议会通过新法案", "why": "监管压力继续上升"}]
    assert brief["sources"] == ["BBC中文"]


def test_parse_brief_json_accepts_mainland_and_world_sections():
    plugin = _plugin()
    content = """
    {
      "lede": "大陆与世界新闻同步更新",
      "mainland": [
        {"title": "国务院发布新政策安排", "why": "影响后续执行"}
      ],
      "world": [
        {"title": "欧洲多国推进安全谈判", "why": "外交压力上升"},
        {"title": "中东停火谈判出现新进展", "why": "地区风险缓和"}
      ],
      "sources": ["新华网时政", "BBC世界"]
    }
    """

    brief = plugin._parse_brief_json(content)

    assert brief["mainland"] == [{"title": "国务院发布新政策安排", "why": "影响后续执行"}]
    assert brief["world"] == [
        {"title": "欧洲多国推进安全谈判", "why": "外交压力上升"},
        {"title": "中东停火谈判出现新进展", "why": "地区风险缓和"},
    ]
    assert brief["top"] == brief["mainland"] + brief["world"]


def test_postprocess_drops_cross_section_ai_items_and_backfills():
    plugin = _plugin()
    brief = {
        "mainland": [{"title": "委内瑞拉强震遇难人数升至1450人", "why": "错放到大陆栏"}],
        "world": [{"title": "欧洲热浪致多国高温", "why": "世卫组织提醒"}],
        "top": [],
    }
    items = [
        {
            "title": "第六届海峡两岸中山论坛在广东中山开幕",
            "summary": "活动在广东中山开幕",
            "source": "新华网时政",
            "section": "mainland",
        },
        {
            "title": "欧洲热浪致多国高温 世卫组织发出提醒",
            "summary": "欧洲多国迎来高温",
            "source": "BBC世界",
            "section": "world",
        },
    ]

    result = plugin._postprocess_brief_news(brief, items)

    assert result["mainland"][0]["title"].startswith("第六届海峡两岸中山论坛")
    assert all("委内瑞拉" not in item["title"] for item in result["mainland"])
    assert result["world"] == [{"title": "欧洲热浪致多国高温", "why": "世卫组织提醒"}]


def test_postprocess_keeps_translated_world_ai_items_from_english_feeds():
    plugin = _plugin()
    brief = {
        "mainland": [],
        "world": [{"title": "欧洲多国热浪升级，世卫组织提醒公共卫生风险", "why": "英文 RSS 已由 AI 中文化"}],
        "top": [],
    }
    items = [
        {
            "title": "Europe heatwave intensifies as WHO warns health systems are not ready",
            "summary": "Several countries face extreme heat.",
            "source": "BBC世界",
            "section": "world",
        },
    ]

    result = plugin._postprocess_brief_news(brief, items)

    assert result["world"] == brief["world"]

def test_simplifies_common_traditional_chinese_payload():
    plugin = _plugin()

    payload = {
        "brief": {
            "lede": "臺灣與烏克蘭會議關注國際經濟",
            "top": [{"title": "美國總統發表談話", "why": "市場風險升高"}],
            "sources": ["BBC繁體中文"],
        },
        "items": [{"title": "歐盟發布新規", "summary": "企業應對"}],
    }

    simplified = plugin._simplify_chinese_payload(payload)

    assert simplified["brief"]["lede"] == "台湾与乌克兰会议关注国际经济"
    assert simplified["brief"]["top"][0]["title"] == "美国总统发表谈话"
    assert simplified["brief"]["top"][0]["why"] == "市场风险升高"
    assert simplified["brief"]["sources"] == ["BBC简体中文"]
    assert simplified["items"][0]["title"] == "欧盟发布新规"
    assert simplified["items"][0]["summary"] == "企业应对"


def test_simplifies_traditional_characters_seen_in_live_ai_output():
    plugin = _plugin()

    simplified = plugin._simplify_chinese_text("礦物質傷腎，這批短劇從業者稱人贏不了AI，老闆賺得滿鉢滿")

    assert simplified == "矿物质伤肾，这批短剧从业者称人赢不了AI，老板赚得满钵满"


def test_simplifies_common_english_news_terms_and_chinese_spacing():
    plugin = _plugin()

    payload = {
        "brief": {
            "lede": "U.S. 与 Iran 谈判使 Moscow 承压",
            "top": [{"title": "美 伊谈判影响 Moscow", "why": "UN 关注 Gaza 局势"}],
            "sources": ["BBC World"],
        }
    }

    simplified = plugin._simplify_chinese_payload(payload)

    assert simplified["brief"]["lede"] == "美国与伊朗谈判使莫斯科承压"
    assert simplified["brief"]["top"][0]["title"] == "美伊谈判影响莫斯科"
    assert simplified["brief"]["top"][0]["why"] == "联合国关注加沙局势"


def test_clean_brief_sources_only_keeps_actual_rss_labels():
    plugin = _plugin()
    items = [
        {"source": "BBC中文"},
        {"source": "法国24"},
        {"source": "半岛电视台"},
    ]

    sources = plugin._clean_brief_sources(["BBC中文", "法方报道", "半岛电视台", "BBC中文"], items)

    assert sources == ["BBC中文", "半岛电视台"]


def test_sanitize_brief_visible_text_removes_untranslated_english_leaks():
    plugin = _plugin()

    brief = {
        "lede": "BBC中文关注 U.S. 与 Iran reconstruction 方案",
        "top": [
            {"title": "美国拟对伊朗 unknownword 提案", "why": "NPR 关注 sanctions 与 Gaza"},
        ],
        "lead": {"tag": "外交 Iran", "title": "美国与 Iran 会谈", "why": "BBC中文关注 sanctions"},
        "sources": ["BBC中文"],
    }

    sanitized = plugin._sanitize_brief_visible_text(brief)

    assert sanitized["lede"] == "BBC中文关注美国与伊朗重建方案"
    assert sanitized["top"][0]["title"] == "美国拟对伊朗提案"
    assert sanitized["top"][0]["why"] == "NPR关注制裁与加沙"
    assert sanitized["lead"] == {"tag": "外交伊朗", "title": "美国与伊朗会谈", "why": "BBC中文关注制裁"}


def test_daily_ai_news_uses_shared_base_ui_resolver(monkeypatch):
    plugin = _plugin()
    sentinel = object()
    calls = []
    monkeypatch.setattr(
        daily_ai_news_module,
        "get_base_ui_font",
        lambda size, bold=False: calls.append((size, bold)) or sentinel,
    )

    font = plugin._font("Microsoft YaHei", 18, "bold")

    assert font is sentinel
    assert calls == [(18, True)]


def test_daily_ai_news_render_forces_microsoft_yahei(monkeypatch):
    plugin = _plugin()
    original_font = plugin._font
    font_calls = []

    def record_font(family, size, weight="normal"):
        font_calls.append((family, size, weight))
        return original_font(family, size, weight)

    monkeypatch.setattr(plugin, "_font", record_font)
    payload = {
        "date": "2026-06-05",
        "generated_at": "2026-06-05T08:00:00",
        "model": "test-model",
        "brief": {
            "lede": "臺灣與烏克蘭會議關注國際經濟",
            "top": [
                {"title": "美國總統發表談話", "why": "市場風險升高"},
                {"title": "歐盟發布新規", "why": "企業應對"},
            ],
            "a_share": {"summary": "市場暫穩", "analysis": "等待數據"},
            "us_stock": {"summary": "美股收高", "analysis": "科技股領漲"},
            "sources": ["BBC繁體中文"],
        },
        "items": [],
        "market_snapshot": {},
    }

    image = plugin._render(
        (800, 480),
        {"font_family": "LXGW WenKai", "brief_title": "整點新聞"},
        payload,
        datetime(2026, 6, 5),
        {"mode": "day"},
    )

    assert image.size == (800, 480)
    assert font_calls
    assert {family for family, _size, _weight in font_calls} == {"Microsoft YaHei"}

def test_postprocess_brief_news_prefers_fresh_mainland_sources_over_recent_ai_title():
    plugin = _plugin()
    brief = {
        "mainland": [
            {"title": "三门峡水库增流至三千二百立方米每秒", "why": "连续旧标题"},
        ],
        "world": [],
    }
    items = [
        {
            "source": "中国新闻网即时",
            "section": "mainland",
            "title": "三门峡水库增流至三千二百立方米每秒",
            "summary": "旧标题摘要",
        },
        {
            "source": "新华网时政",
            "section": "mainland",
            "title": "国务院部署新能源项目审批改革",
            "summary": "新标题摘要",
        },
    ]

    result = plugin._postprocess_brief_news(brief, items, ["三门峡水库增流至三千二百立方米每秒"])

    assert result["mainland"][0]["title"] == "国务院部署新能源项目审批改革"
    assert result["mainland"][1]["title"] == "三门峡水库增流至三千二百立方米每秒"


def test_render_news_section_items_do_not_force_backfill_when_summary_is_sparse():
    plugin = _plugin()
    brief = {
        "mainland": [
            {"title": "大陆新闻一", "why": "国内更新"},
            {"title": "大陆新闻二", "why": "政策进展"},
        ],
    }
    payload = {
        "items": [
            {"title": f"RSS 大陆新闻 {index}", "summary": "补充", "section": "mainland"}
            for index in range(1, 7)
        ]
    }

    items = plugin._render_news_section_items(brief, payload, "mainland")

    assert [item["title"] for item in items] == ["大陆新闻一", "大陆新闻二"]


def test_title_background_asset_is_transparent_measured_strip():
    path = daily_ai_news_module.PLUGIN_DIR / TITLE_BACKGROUND_IMAGE

    with Image.open(path) as image:
        assert image.mode == "RGBA"
        assert image.size == TITLE_BACKGROUND_SIZE
        assert image.getchannel("A").getextrema()[0] == 0


def test_title_wordmark_asset_is_transparent_measured_strip():
    path = daily_ai_news_module.PLUGIN_DIR / TITLE_WORDMARK_IMAGE

    with Image.open(path) as image:
        assert image.mode == "RGBA"
        assert image.size == TITLE_WORDMARK_SIZE
        alpha = image.getchannel("A")
        assert alpha.getextrema()[0] == 0
        assert alpha.getbbox() is not None
        assert alpha.getpixel((0, 0)) == 0
        assert alpha.getpixel((image.width - 1, 0)) == 0

def test_render_uses_title_wordmark_instead_of_plain_title(monkeypatch):
    plugin = _plugin()
    seen = {}
    text_calls = []

    def fake_draw_title_wordmark(image, x, y, size, ink):
        seen["wordmark"] = (int(x), int(y), tuple(int(value) for value in size))
        return (int(x), int(y), int(x) + 205, int(y) + int(size[1]))

    original_text = ImageDraw.ImageDraw.text

    def capture_text(self, xy, text, *args, **kwargs):
        text_calls.append(str(text))
        return original_text(self, xy, text, *args, **kwargs)

    monkeypatch.setattr(plugin, "_draw_title_wordmark", fake_draw_title_wordmark)
    monkeypatch.setattr(plugin, "_draw_title_background", lambda image, box: True)
    monkeypatch.setattr(ImageDraw.ImageDraw, "text", capture_text)

    payload = {
        "date": "2026-06-08",
        "generated_at": "2026-06-08T05:51:00",
        "model": "gpt-5-nano",
        "brief": {"lede": "新闻摘要", "top": [], "a_share": {}, "us_stock": {}},
        "items": [],
        "market_snapshot": {},
    }

    image = plugin._render((800, 480), {"brief_title": "整点新闻"}, payload, datetime(2026, 6, 8), {"mode": "day"})

    assert image.size == (800, 480)
    assert seen["wordmark"] == (18, 6, (152, 42))
    assert "整点新闻" not in text_calls


def test_render_falls_back_to_plain_title_when_wordmark_missing(monkeypatch):
    plugin = _plugin()
    text_calls = []
    original_text = ImageDraw.ImageDraw.text

    def capture_text(self, xy, text, *args, **kwargs):
        text_calls.append(str(text))
        return original_text(self, xy, text, *args, **kwargs)

    monkeypatch.setattr(plugin, "_draw_title_wordmark", lambda *args, **kwargs: None)
    monkeypatch.setattr(plugin, "_draw_title_background", lambda image, box: True)
    monkeypatch.setattr(ImageDraw.ImageDraw, "text", capture_text)

    payload = {
        "date": "2026-06-08",
        "generated_at": "2026-06-08T05:51:00",
        "model": "gpt-5-nano",
        "brief": {"lede": "新闻摘要", "top": [], "a_share": {}, "us_stock": {}},
        "items": [],
        "market_snapshot": {},
    }

    plugin._render((800, 480), {"brief_title": "整点新闻"}, payload, datetime(2026, 6, 8), {"mode": "day"})

    assert "整点新闻" in text_calls

def test_daily_ai_presentation_is_explicit_no_change(monkeypatch):
    plugin = _plugin()

    def forbidden(*_args, **_kwargs):
        raise AssertionError("NO_CHANGE presentation must not render, fetch, or write")

    monkeypatch.setattr(plugin, "generate_image", forbidden)
    monkeypatch.setattr(plugin, "_get_brief", forbidden)
    monkeypatch.setattr(plugin, "_write_news_context", forbidden)

    assert "presentation_mode" in DailyAINews.__dict__
    assert plugin.presentation_mode({}) is PresentationMode.NO_CHANGE


def test_daily_ai_provenance_covers_fresh_stale_live_and_local(monkeypatch, tmp_path):
    plugin = _plugin()
    now = datetime(2026, 7, 12, 8, 0)
    settings = {"feed_urls": news_sources.DEFAULT_FEEDS, "max_items": "6"}
    monkeypatch.setattr(plugin, "_cache_dir", lambda: tmp_path)

    class Device:
        def load_env_key(self, _key):
            return ""

    key = plugin._cache_key(
        "2026-07-12",
        daily_ai_news_module.DEFAULT_MODEL,
        plugin._effective_feeds_text(settings["feed_urls"]),
        6,
        None,
    )
    cache_file = tmp_path / "brief.json"
    cached = {
        "cache_key": key,
        "brief": {"top": [{"title": "cached", "why": "cached"}]},
        "items": [{"source": "cached", "title": "cached"}],
    }
    cache_file.write_text(json.dumps(cached), encoding="utf-8")
    fresh = plugin._get_brief(settings, Device(), now)

    cached["cache_key"] = "wrong-key"
    cache_file.write_text(json.dumps(cached), encoding="utf-8")
    monkeypatch.setattr(plugin, "_fetch_items", lambda *_args: [])
    stale = plugin._get_brief(settings, Device(), now)

    cache_file.unlink()
    items = [
        {"source": "RSS", "title": f"fresh-{index}", "summary": "summary", "published": "", "link": ""}
        for index in range(8)
    ]
    monkeypatch.setattr(plugin, "_fetch_items", lambda *_args: items)
    monkeypatch.setattr(plugin, "_fetch_market_snapshot", lambda *_args: {})
    live = plugin._get_brief(settings, Device(), now)

    secret = "sk-task6-super-secret"
    monkeypatch.setattr(
        plugin,
        "_get_brief",
        lambda *_args: (_ for _ in ()).throw(RuntimeError(secret)),
    )
    monkeypatch.setattr(plugin, "_write_news_context", lambda *_args: None)
    monkeypatch.setattr(
        plugin,
        "_render",
        lambda *_args, **_kwargs: Image.new("RGB", (2, 1), "white"),
    )
    fallback = plugin.generate_image({}, _ThemeDeviceConfig())

    assert fresh["_source_provenance"] == "fresh_cache"
    assert stale["_source_provenance"] == "stale_cache"
    assert live["_source_provenance"] == "live"
    assert read_source_provenance(fallback).value == "local_fallback"
    metadata = repr(fallback.info)
    assert secret not in metadata
    assert "http://" not in metadata and "https://" not in metadata
    assert len(metadata) <= 256


def test_daily_ai_theme_provenance_is_cache_only_and_context_has_health_label(
    monkeypatch,
    tmp_path,
):
    plugin = _plugin()

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            value = cls(2026, 7, 12, 8, 0)
            return tz.localize(value) if tz is not None else value

    monkeypatch.setattr(daily_ai_news_module, "datetime", FrozenDateTime)
    now = FrozenDateTime.now()
    settings = {
        "feed_urls": news_sources.DEFAULT_FEEDS,
        "max_items": "6",
        "_theme_render_only": True,
        "_inkypi_theme": _canonical_theme(
            "day",
            background=(255, 255, 255),
            panel=(255, 255, 255),
            ink=(0, 0, 0),
            muted=(80, 80, 80),
            rule=(120, 120, 120),
            accent=(20, 90, 140),
        ),
    }
    monkeypatch.setattr(plugin, "_cache_dir", lambda create=True: tmp_path)
    key = plugin._cache_key(
        now.strftime("%Y-%m-%d"),
        daily_ai_news_module.DEFAULT_MODEL,
        plugin._effective_feeds_text(settings["feed_urls"]),
        6,
        None,
    )
    cache_file = tmp_path / "brief.json"
    cache_file.write_text(
        json.dumps(
            {
                "cache_key": key,
                "brief": {"top": [{"title": "cached", "why": "cached"}]},
                "items": [],
            }
        ),
        encoding="utf-8",
    )
    before = cache_file.read_bytes()
    captured = []
    monkeypatch.setattr(
        daily_ai_news_module,
        "write_context",
        lambda *args, **kwargs: captured.append((args, kwargs)),
    )
    plugin._write_news_context(
        {
            "brief": {"top": []},
            "_source_provenance": "stale_cache",
        },
        now,
    )
    assert captured[0][0][1]["source_provenance"] == "stale_cache"

    def forbidden(*_args, **_kwargs):
        raise AssertionError("theme-only work must not fetch or write")

    monkeypatch.setattr(plugin, "_fetch_items", forbidden)
    monkeypatch.setattr(plugin, "_write_news_context", forbidden)
    monkeypatch.setattr(news_common, "_safe_json_write", forbidden)
    monkeypatch.setattr(
        plugin,
        "_render",
        lambda *_args, **_kwargs: Image.new("RGB", (2, 1), "white"),
    )

    image = plugin.generate_image(settings, _ThemeDeviceConfig())

    assert read_source_provenance(image).value == "fresh_cache"
    assert cache_file.read_bytes() == before


def test_daily_ai_news_cold_theme_render_does_not_create_cache_tree(
    monkeypatch,
    tmp_path,
):
    cache_root = tmp_path / "runtime-cache"
    monkeypatch.setenv("INKYPI_CACHE_DIR", str(cache_root))
    plugin = _plugin()

    def forbidden(*_args, **_kwargs):
        raise AssertionError("cold theme-only render must not fetch or write")

    data_writer = news_common._safe_json_write
    monkeypatch.setattr(plugin, "_fetch_items", forbidden)
    monkeypatch.setattr(plugin, "_write_news_context", forbidden)
    monkeypatch.setattr(news_common, "_safe_json_write", forbidden)

    with pytest.raises(RuntimeError, match="warm Daily AI News cache"):
        plugin.generate_image(
            {
                "_theme_render_only": True,
                "_inkypi_theme": _canonical_theme(
                    "day",
                    background=(255, 255, 255),
                    panel=(255, 255, 255),
                    ink=(0, 0, 0),
                    muted=(80, 80, 80),
                    rule=(120, 120, 120),
                    accent=(20, 90, 140),
                ),
            },
            _ThemeDeviceConfig(),
        )

    assert not cache_root.exists()

    writer = _plugin()
    data_writer(
        writer._cache_dir() / "data-writer.json",
        {"ok": True},
    )
    assert (
        cache_root
        / "plugins"
        / "daily_ai_news"
        / "cache"
        / "data-writer.json"
    ).is_file()


def _freeze_daily_ai_news_clock(monkeypatch):
    moment = {"value": (2026, 10, 8, 7, 30)}

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            value = cls(*moment["value"])
            return tz.localize(value) if tz is not None else value

    monkeypatch.setattr(daily_ai_news_module, "datetime", FrozenDateTime)
    return moment


def _night_theme():
    return _canonical_theme(
        "night",
        background=(7, 9, 12),
        panel=(22, 26, 32),
        ink=(245, 246, 248),
        muted=(178, 182, 190),
        rule=(58, 64, 72),
        accent=(76, 190, 238),
    )


def _seed_scheduled_daily_brief(monkeypatch, plugin, moment, settings):
    """Run the 07:30 DATA refresh once against fixed RSS items."""

    items = [
        {
            "source": "中国新闻网要闻" if index < 4 else "BBC世界",
            "source_section": "mainland" if index < 4 else "world",
            "title": f"午夜追赶测试新闻 {index}",
            "summary": f"午夜追赶测试摘要 {index}",
            "published": "",
            "link": "",
        }
        for index in range(8)
    ]
    monkeypatch.setattr(plugin, "_fetch_items", lambda *_args: items)
    monkeypatch.setattr(plugin, "_fetch_market_snapshot", lambda *_args: {})
    monkeypatch.setattr(
        daily_ai_news_module,
        "write_context",
        lambda *_args, **_kwargs: True,
    )
    moment["value"] = (2026, 10, 8, 7, 30)
    plugin.generate_image(settings, _ThemeDeviceConfig())


def _forbid_daily_ai_news_side_effects(monkeypatch, plugin):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("theme-only redraw must not fetch or write")

    for name in (
        "_fetch_items",
        "_fetch_market_snapshot",
        "_summarize_with_ai",
        "_write_news_context",
        "_record_recent_news_titles",
        "_record_api_call",
    ):
        monkeypatch.setattr(plugin, name, forbidden)
    monkeypatch.setattr(news_common, "_safe_json_write", forbidden)


def test_theme_catchup_after_local_midnight_redraws_the_scheduled_brief(
    monkeypatch,
    tmp_path,
):
    plugin = _plugin()
    monkeypatch.setattr(plugin, "_cache_dir", lambda create=True: tmp_path)
    moment = _freeze_daily_ai_news_clock(monkeypatch)
    settings = {"feed_urls": news_sources.DEFAULT_FEEDS, "max_items": "8"}
    _seed_scheduled_daily_brief(monkeypatch, plugin, moment, settings)
    cache_file = tmp_path / "brief.json"
    before = cache_file.read_bytes()
    _forbid_daily_ai_news_side_effects(monkeypatch, plugin)
    night_settings = {
        **settings,
        "_theme_render_only": True,
        "_inkypi_theme": _night_theme(),
    }

    moment["value"] = (2026, 10, 8, 23, 0)
    before_midnight = plugin.generate_image(night_settings, _ThemeDeviceConfig())
    # The next DATA refresh is not due until 07:30, so the catch-up must keep
    # drawing the brief that is on screen instead of failing until morning.
    moment["value"] = (2026, 10, 9, 1, 43)
    after_midnight = plugin.generate_image(night_settings, _ThemeDeviceConfig())

    assert after_midnight.size == before_midnight.size == (800, 480)
    assert after_midnight.tobytes() == before_midnight.tobytes()
    assert read_source_provenance(before_midnight).value == "fresh_cache"
    assert read_source_provenance(after_midnight).value == "stale_cache"
    assert cache_file.read_bytes() == before


@pytest.mark.parametrize("change", ["settings", "summary_schema"])
def test_theme_only_still_rejects_a_brief_built_for_other_settings(
    monkeypatch,
    tmp_path,
    change,
):
    plugin = _plugin()
    monkeypatch.setattr(plugin, "_cache_dir", lambda create=True: tmp_path)
    moment = _freeze_daily_ai_news_clock(monkeypatch)
    settings = {"feed_urls": news_sources.DEFAULT_FEEDS, "max_items": "8"}
    _seed_scheduled_daily_brief(monkeypatch, plugin, moment, settings)
    _forbid_daily_ai_news_side_effects(monkeypatch, plugin)
    night_settings = {
        **settings,
        "_theme_render_only": True,
        "_inkypi_theme": _night_theme(),
    }
    if change == "settings":
        night_settings["max_items"] = "6"
    else:
        monkeypatch.setattr(
            daily_ai_news_module,
            "SUMMARY_SCHEMA_VERSION",
            daily_ai_news_module.SUMMARY_SCHEMA_VERSION + "-next",
        )

    for value in ((2026, 10, 8, 23, 0), (2026, 10, 9, 1, 43)):
        moment["value"] = value
        with pytest.raises(RuntimeError, match="matching Daily AI News cache"):
            plugin.generate_image(night_settings, _ThemeDeviceConfig())


class _ChartResponse:
    def __init__(self, data):
        self.data = data


def _yahoo_chart(closes, timestamps, market_time, price, gmtoffset=-14400):
    return {
        "chart": {
            "result": [
                {
                    "meta": {
                        "regularMarketPrice": price,
                        "regularMarketTime": market_time,
                        "chartPreviousClose": 1.0,
                        "gmtoffset": gmtoffset,
                        "currency": "USD",
                        "exchangeName": "SNP",
                    },
                    "timestamp": timestamps,
                    "indicators": {"quote": [{"close": closes}]},
                }
            ]
        }
    }


def _patch_chart(monkeypatch, chart):
    seen = {}

    class _Client:
        def request_json(self, method, url, params=None, **_kwargs):
            seen["params"] = params
            return _ChartResponse(chart)

    monkeypatch.setattr(news_market, "get_http_client", lambda: _Client())
    return seen


DAY = 86400
# 2026-10-19 and 2026-10-20 13:30 UTC session opens (09:30 New York).
MONDAY_OPEN, TUESDAY_OPEN = 1792416600, 1792416600 + DAY


def test_yahoo_quote_change_compares_consecutive_daily_closes(monkeypatch):
    plugin = _plugin()
    seen = _patch_chart(
        monkeypatch,
        _yahoo_chart([7600.0, None, 7690.73, 7712.57], [MONDAY_OPEN - 2 * DAY, MONDAY_OPEN - DAY, MONDAY_OPEN, TUESDAY_OPEN],
                     TUESDAY_OPEN + 6 * 3600 + 1800, 7712.57),
    )

    row = plugin._fetch_yahoo_quote("^GSPC", "标普500")

    assert seen["params"]["range"] == "1mo"
    assert row["change_pct"] == pytest.approx(0.28, abs=0.005)
    assert row["change"] == pytest.approx(21.84, abs=0.01)
    assert row["closes"] == [7600.0, 7690.73, 7712.57]
    assert row["as_of"].startswith("2026-10-20")


def test_yahoo_quote_without_todays_bar_compares_with_the_last_close(monkeypatch):
    plugin = _plugin()
    _patch_chart(
        monkeypatch,
        _yahoo_chart([7690.73, 7712.57], [MONDAY_OPEN, TUESDAY_OPEN], TUESDAY_OPEN + DAY + 3600, 7750.0),
    )

    row = plugin._fetch_yahoo_quote("^GSPC", "标普500")

    assert row["change_pct"] == pytest.approx((7750.0 / 7712.57 - 1) * 100, abs=0.005)


def test_yahoo_quote_with_one_close_has_no_daily_change(monkeypatch):
    plugin = _plugin()
    _patch_chart(monkeypatch, _yahoo_chart([2736.6], [TUESDAY_OPEN], TUESDAY_OPEN + 3600, 2736.6, gmtoffset=28800))

    row = plugin._fetch_yahoo_quote("399006.SZ", "创业板指")

    assert row["price"] == 2736.6
    assert row["change_pct"] is None
    assert row["closes"] == [2736.6]


def test_parse_brief_json_reads_lead_and_tags():
    plugin = _plugin()
    content = """
    {
      "lead": {"tag": "公共卫生", "title": "尼日利亚确诊埃博拉病例", "why": "患者自乌干达抵达阿布贾", "section": "world"},
      "mainland": [{"tag": "金融", "title": "数字人民币体系全面升级", "why": "推出央行端系统"}],
      "world": [{"tag": "中东", "title": "英国派战机协防沙特", "why": "应对胡塞武装袭击"}],
      "sources": ["BBC世界"]
    }
    """

    brief = plugin._parse_brief_json(content)

    assert brief["lead"]["title"] == "尼日利亚确诊埃博拉病例"
    assert brief["lead"]["tag"] == "公共卫生"
    assert brief["lede"] == "尼日利亚确诊埃博拉病例"
    assert brief["mainland"][0]["tag"] == "金融"
    assert "a_share" not in brief


def test_postprocess_keeps_a_sourced_mainland_lead_out_of_the_columns():
    plugin = _plugin()
    brief = {
        "lead": {"tag": "基建", "title": "前三季度全国铁路完成投资6012亿元", "why": "同比增长1.3%"},
        "mainland": [
            {"tag": "基建", "title": "全国铁路前三季度完成投资6012亿元", "why": "重复的头条"},
            {"tag": "金融", "title": "数字人民币基础设施服务体系全面升级", "why": "推出央行端系统"},
        ],
        "world": [],
    }
    items = [
        {"source": "中国新闻网要闻", "section": "mainland", "title": "今年前三季度全国铁路完成固定资产投资6012亿元", "summary": ""},
        {"source": "中国新闻网财经", "section": "mainland", "title": "数字人民币基础设施服务体系全面升级", "summary": ""},
    ]

    result = plugin._postprocess_brief_news(brief, items)

    assert result["lead"]["section"] == "mainland"
    assert [item["title"] for item in result["mainland"]] == ["数字人民币基础设施服务体系全面升级"]
    assert result["lede"] == "前三季度全国铁路完成投资6012亿元"


def test_postprocess_drops_a_mainland_lead_without_a_source():
    plugin = _plugin()
    brief = {
        "lead": {"tag": "政策", "title": "国务院出台全新住房补贴政策", "why": "编造的头条", "section": "mainland"},
        "mainland": [{"tag": "金融", "title": "数字人民币基础设施服务体系全面升级", "why": "推出央行端系统"}],
        "world": [],
    }
    items = [{"source": "中国新闻网财经", "section": "mainland", "title": "数字人民币基础设施服务体系全面升级", "summary": ""}]

    result = plugin._postprocess_brief_news(brief, items)

    assert result["lead"] is None


def test_postprocess_trims_titles_facts_and_tags():
    plugin = _plugin()
    brief = {
        "mainland": [],
        "world": [{"tag": "国际公共卫生事件", "title": "标" * 40, "why": "事" * 60}],
    }

    result = plugin._postprocess_brief_news(brief, [{"source": "BBC世界", "section": "world", "title": "Ebola", "summary": ""}])

    item = result["world"][0]
    assert item["tag"] == "公卫"
    assert len(item["title"]) == news_brief.NEWS_TITLE_MAX_CHARS
    assert len(item["why"]) == news_brief.NEWS_FACT_MAX_CHARS


def test_rss_cards_take_their_tag_from_the_chinanews_desk():
    plugin = _plugin()
    items = [
        {"source": "中国新闻网财经", "section": "mainland", "title": "数字人民币基础设施服务体系全面升级", "summary": "推出央行端系统"},
        {"source": "地方日报", "section": "mainland", "title": "某地举办秋季农产品交易会", "summary": "吸引众多客商"},
    ]

    cards = plugin._rss_news_cards(items, 4)

    assert [card["tag"] for card in cards] == ["经济", "要闻"]


def test_prompt_asks_for_a_lead_tags_and_one_sentence_facts(monkeypatch):
    plugin = _plugin()
    captured = {}

    class _Responses:
        @staticmethod
        def create(**kwargs):
            captured.update(kwargs)

            class _Response:
                output_text = '{"lead":{"tag":"金融","title":"数字人民币体系升级","why":"推出央行端系统"},"mainland":[],"world":[]}'

            return _Response()

    class _Client:
        responses = _Responses()

    monkeypatch.setattr(news_brief, "OpenAI", lambda **_kwargs: _Client())

    plugin._summarize_with_openai("key", "gpt-5-nano", {}, [], {}, datetime(2026, 10, 21, 7, 0))

    request = captured["input"]
    assert '"lead"' in request and '"tag"' in request
    assert "一句话事实" in request
    assert "不得再出现在 mainland 或 world" in request


def test_prompt_numbers_items_and_asks_each_story_for_its_ref(monkeypatch):
    plugin = _plugin()
    captured = {}

    class _Responses:
        @staticmethod
        def create(**kwargs):
            captured.update(kwargs)

            class _Response:
                output_text = '{"lead":null,"mainland":[],"world":[]}'

            return _Response()

    class _Client:
        responses = _Responses()

    monkeypatch.setattr(news_brief, "OpenAI", lambda **_kwargs: _Client())
    items = [{"source": "BBC世界", "section": "world", "title": "Earthquake hits Chile", "summary": ""},
             {"source": "NPR新闻", "section": "world", "title": "Pope recovers", "summary": ""}]

    plugin._summarize_with_openai("key", "gpt-5-nano", {}, items, {}, datetime(2026, 10, 21, 7, 0))

    request = json.loads(captured["input"])
    assert [item["id"] for item in request["items"]] == ["1", "2"]
    assert all("ref" in request["output_schema"][key][0] for key in ("mainland", "world"))
    assert "ref" in request["output_schema"]["lead"]
    rules = "".join(request["rules"])
    assert "同一个 ref 只能用一次" in rules and "国际局势" in rules and "相关报道增多" in rules
    assert items[0] == {"source": "BBC世界", "section": "world", "title": "Earthquake hits Chile", "summary": ""}


WORLD_SOURCE_ITEMS = [
    {"source": "中国新闻网要闻", "section": "mainland", "title": "数字人民币基础设施服务体系全面升级", "summary": ""},
    {"source": "BBC世界", "section": "world", "title": "Missiles hit Kyiv after Russia agrees to resume talks", "summary": ""},
    {"source": "半岛电视台", "section": "world", "title": "One killed in attack on Saudi airport", "summary": ""},
    {"source": "德国之声", "section": "world", "title": "Pope will not need further cancer treatment", "summary": ""},
]


def test_world_stories_must_each_name_one_world_item_and_a_specific_story():
    plugin = _plugin()
    brief = {
        "mainland": [],
        "world": [
            {"ref": "2", "tag": "冲突", "title": "俄同意恢复和谈后基辅遭导弹袭击", "why": "莫斯科称原则上准备恢复谈判"},
            {"ref": "2", "tag": "冲突", "title": "基辅再遭袭击", "why": "同一条的重复"},
            {"ref": "1", "tag": "金融", "title": "借用国内条目的世界新闻", "why": "跨区挪用"},
            {"ref": "9", "tag": "要闻", "title": "编号不存在的新闻", "why": "编造"},
            {"ref": "4", "tag": "冲突", "title": "加剧冲突风险的国际局势", "why": "多方报道显示局势变化"},
            {"ref": 3, "tag": "航空", "title": "沙特法赫德国王机场遇袭致1死8伤", "why": "伤者已送医"},
        ],
    }

    result = plugin._postprocess_brief_news(brief, WORLD_SOURCE_ITEMS)

    assert [item["title"] for item in result["world"]] == ["俄同意恢复和谈后基辅遭导弹袭击", "沙特法赫德国王机场遇袭致1死8伤"]
    assert all("ref" not in item for item in result["world"])


def test_a_world_lead_is_checked_by_ref_and_keeps_its_story_out_of_the_column():
    plugin = _plugin()
    world = [{"ref": "2", "tag": "冲突", "title": "基辅在恢复和谈前夕遭导弹袭击", "why": "同一条"},
             {"ref": "4", "tag": "公卫", "title": "教宗利奥术后无需进一步癌症治疗", "why": "梵蒂冈称手术成功"}]
    lead = {"ref": "2", "section": "world", "tag": "冲突", "title": "俄同意恢复和谈后基辅遭袭", "why": "莫斯科表态"}

    result = plugin._postprocess_brief_news({"lead": lead, "mainland": [], "world": world}, WORLD_SOURCE_ITEMS)

    assert result["lead"]["title"] == "俄同意恢复和谈后基辅遭袭" and "ref" not in result["lead"]
    assert [item["title"] for item in result["world"]] == ["教宗利奥术后无需进一步癌症治疗"]
    for bad_lead in ({**lead, "ref": "1"}, {**lead, "ref": "9"}, {**lead, "title": "美加强对乌克兰的地区局势应对"}):
        assert plugin._postprocess_brief_news({"lead": bad_lead, "mainland": [], "world": []}, WORLD_SOURCE_ITEMS)["lead"] is None


def test_a_brief_without_refs_keeps_its_world_stories_but_not_vague_ones():
    plugin = _plugin()
    brief = {"mainland": [], "world": [
        {"tag": "冲突", "title": "俄同意恢复和谈后基辅遭导弹袭击", "why": "莫斯科表态"},
        {"tag": "能源", "title": "能源与地缘冲突报道增多", "why": "区域能源供应相关报道增多"},
    ]}

    result = plugin._postprocess_brief_news(brief, WORLD_SOURCE_ITEMS)

    assert [item["title"] for item in result["world"]] == ["俄同意恢复和谈后基辅遭导弹袭击"]


def test_page_news_uses_the_first_mainland_story_as_lead_for_legacy_briefs():
    plugin = _plugin()
    brief = {
        "mainland": [{"title": "数字人民币基础设施服务体系全面升级", "why": "国内更新"},
                     {"title": "前三季度全国铁路投资6012亿元", "why": "政策进展"}],
        "world": [{"title": "尼日利亚确诊埃博拉病例", "why": "国际进展"}],
    }

    lead, mainland, world = plugin._page_news(brief, {"items": []})

    assert lead["title"] == "数字人民币基础设施服务体系全面升级" and lead["section_label"] == "国内"
    assert [item["title"] for item in mainland] == ["前三季度全国铁路投资6012亿元"]
    assert [item["title"] for item in world] == ["尼日利亚确诊埃博拉病例"]


def test_page_news_keeps_an_explicit_world_lead():
    plugin = _plugin()
    brief = {
        "lead": {"tag": "公共卫生", "title": "尼日利亚确诊埃博拉病例", "why": "患者自乌干达抵达", "section": "world"},
        "mainland": [{"title": "大陆新闻一", "why": "国内更新", "tag": "金融"}],
        "world": [{"title": "尼日利亚确诊埃博拉病例，疫情扩散", "why": "重复"}, {"title": "英国派战机协防沙特", "why": "应对袭击"}],
    }

    lead, mainland, world = plugin._page_news(brief, {"items": []})

    assert lead["section_label"] == "国际" and lead["tag"] == "公卫"
    assert [item["title"] for item in mainland] == ["大陆新闻一"]
    assert [item["title"] for item in world] == ["英国派战机协防沙特"]


def _page_payload(**overrides):
    payload = {
        "date": "2026-10-21",
        "generated_at": "2026-10-21T07:34:00-07:00",
        "model": "gpt-5-nano",
        "brief": {
            "lead": {"tag": "公共卫生", "title": "尼日利亚确诊埃博拉病例", "why": "患者自乌干达抵达阿布贾", "section": "world"},
            "mainland": [
                {"tag": "金融", "title": f"大陆新闻{index}", "why": f"国内事实{index}"} for index in range(1, 5)
            ],
            "world": [{"tag": "中东", "title": "英国派战机协防沙特", "why": "应对胡塞武装袭击"}],
        },
        "items": [
            {"source": "中国新闻网要闻", "section": "mainland", "title": "大陆新闻1"},
            {"source": "BBC世界", "section": "world", "title": "UK jets"},
            {"source": "NPR新闻", "section": "world", "title": "Ebola"},
        ],
        "market_snapshot": {
            "groups": {
                "a_share": [{"name": "上证指数", "price": 3733.52, "change_pct": -0.05, "as_of": "2026-10-21T01:30:00+00:00",
                             "closes": [3735.37, 3733.52]}],
                "us_stock": [{"name": "标普500", "price": 7712.57, "change_pct": 0.28, "as_of": "2026-10-20T13:30:00+00:00",
                              "closes": [7690.73, 7712.57]}],
            }
        },
    }
    payload.update(overrides)
    return payload


def _rendered_text(monkeypatch, plugin, payload, theme=None):
    seen = []
    original_text = ImageDraw.ImageDraw.text

    def capture_text(self, xy, text, *args, **kwargs):
        seen.append(str(text))
        return original_text(self, xy, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", capture_text)
    image = plugin._render((800, 480), {"brief_title": "整点新闻"}, payload, datetime(2026, 10, 21, 8, 0), theme or {"mode": "day"})
    return image, seen


def test_render_draws_lead_columns_meta_and_market_tiles(monkeypatch):
    plugin = _plugin()

    image, seen = _rendered_text(monkeypatch, plugin, _page_payload())

    assert image.size == (800, 480)
    assert "10月21日 周三" in seen
    assert "07:34 更新 · 3 个信源" in seen
    assert {"头", "条", "国际", "公卫"} <= set(seen)
    assert "尼日利亚确诊埃博拉病例" in seen
    assert {"大陆新闻1", "大陆新闻4", "英国派战机协防沙特"} <= set(seen)
    assert {"上证", "-0.05%", "3,733.52", "标普", "+0.28%", "截至 10/21", "截至 10/20"} <= set(seen)
    assert "午夜简报" not in seen


def test_render_marks_cache_and_shows_warnings_in_the_header(monkeypatch):
    plugin = _plugin()

    _image, cached = _rendered_text(monkeypatch, plugin, _page_payload(from_cache=True))
    _image, warned = _rendered_text(monkeypatch, plugin, _page_payload(warning="AI 摘要失败，使用 RSS 兜底"))

    assert "07:34 更新 · 3 个信源 · 缓存" in cached
    assert "AI 摘要失败，使用 RSS 兜底" in warned


def test_render_fills_a_short_column_with_the_globe_and_its_sources(monkeypatch):
    plugin = _plugin()
    globes = []
    monkeypatch.setattr(news_art, "draw_globe", lambda draw, cx, cy, radius, *args, **kwargs: globes.append((cx, cy, radius)))

    _image, seen = _rendered_text(monkeypatch, plugin, _page_payload())

    assert len(globes) == 1 and globes[0][0] > 400
    assert "国际信源持续追踪" in seen
    assert any("BBC世界" in text and "NPR新闻" in text for text in seen)


def test_render_skips_the_globe_when_both_columns_are_full(monkeypatch):
    plugin = _plugin()
    globes = []
    monkeypatch.setattr(news_art, "draw_globe", lambda *args, **kwargs: globes.append(args))
    payload = _page_payload()
    payload["brief"]["world"] = [
        {"tag": "中东", "title": f"世界新闻标题足够长需要折成两行显示的内容{index}", "why": "事实"} for index in range(1, 6)
    ]

    plugin._render((800, 480), {"brief_title": "整点新闻"}, payload, datetime(2026, 10, 21), {"mode": "day"})

    assert globes == []


def test_render_positions_the_radar_band_between_title_and_meta(monkeypatch):
    plugin = _plugin()
    seen = {}

    def fake_draw_title_background(image, box):
        seen["box"] = tuple(int(value) for value in box)
        return True

    monkeypatch.setattr(plugin, "_draw_title_background", fake_draw_title_background)
    monkeypatch.setattr(plugin, "_draw_title_wordmark", lambda image, x, y, size, ink: (18, 10, 168, 46))

    plugin._render((800, 480), {"brief_title": ""}, _page_payload(), datetime(2026, 10, 21), {"mode": "day"})

    left, top, right, bottom = seen["box"]
    assert left == 168 + 12
    assert (top, bottom) == (4, 52)
    # The band ends 14 px before the date and update line at the right edge.
    assert 620 <= right <= 680


def test_market_changes_are_red_and_green_figures_beside_the_index_mark(monkeypatch):
    plugin = _plugin()
    logos = []
    original_logo = news_layout.NewsPage.paste_logo

    def record_logo(self, key, x, middle):
        width = original_logo(self, key, x, middle)
        logos.append((key, width))
        return width

    monkeypatch.setattr(news_layout.NewsPage, "paste_logo", record_logo)
    _image, fills = _rendered_fills(monkeypatch, plugin)

    # Chinese quotes: gains are red and losses green, as words, never as filled blocks.
    assert fills["+0.28%"] == (255, 0, 0) and fills["-0.05%"] == (0, 255, 0)
    assert ("sse", 16) in logos and ("sp500", 29) in logos


def test_every_index_has_a_mark_in_pure_panel_inks():
    names = [name for rows in news_market.MARKET_GROUPS.values() for _symbol, name in rows]
    inks = {(255, 255, 255), (255, 0, 0), (0, 0, 255)}

    for name in names:
        logo = daily_ai_news_module.DailyAINews._load_market_logo(news_market.MARKET_LOGOS[name])
        assert logo is not None and logo.height <= 16
        pixels = logo.load()
        seen = [pixels[x, y] for y in range(logo.height) for x in range(logo.width)]
        assert {pixel[:3] for pixel in seen if pixel[3]} <= inks
        assert {pixel[3] for pixel in seen} <= {0, 255}


def _rendered_fills(monkeypatch, plugin):
    fills = {}
    original_text = ImageDraw.ImageDraw.text

    def capture_text(self, xy, text, *args, **kwargs):
        fills.setdefault(str(text), kwargs.get("fill"))
        return original_text(self, xy, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", capture_text)
    image = plugin._render((800, 480), {"brief_title": "整点新闻"}, _page_payload(), datetime(2026, 10, 21), {"mode": "day"})
    return image, fills


def test_words_stay_ink_with_red_and_blue_as_sparing_accents(monkeypatch):
    plugin = _plugin()

    image, fills = _rendered_fills(monkeypatch, plugin)

    ink = daily_ai_news_module.get_theme_palette("day")["ink"]
    assert fills["大陆新闻1"] == fills["尼日利亚确诊埃博拉病例"] == fills["公卫"] == ink
    # 国内 numbers are red; the 国际 kicker and a business tag are blue.
    assert fills["01"] == (255, 0, 0)
    assert fills["国际"] == fills["金融"] == (0, 0, 255)
    # The lead's seal is the panel's red with white characters.
    assert fills["头"] == fills["条"] == (255, 255, 255)
    palette = plugin._page_palette({"mode": "day"})
    assert palette["lead"] == palette["up"] == (255, 0, 0) and palette["down"] == (0, 255, 0)
    assert "domestic" not in palette and "world" not in palette
    assert image.getpixel((22, 100)) == (255, 0, 0)
    assert image.getpixel((400, 100)) == (255, 255, 255)


def test_night_render_keeps_a_black_canvas(monkeypatch):
    plugin = _plugin()
    night = _canonical_theme(
        "night", background=(7, 9, 12), panel=(22, 26, 32), ink=(245, 246, 248),
        muted=(178, 182, 190), rule=(58, 64, 72), accent=(93, 198, 178),
    )

    image = plugin._render((800, 480), {"brief_title": "整点新闻"}, _page_payload(), datetime(2026, 10, 21), night)

    assert image.getpixel((0, 479)) == (0, 0, 0)
    assert image.getpixel((799, 300)) == (0, 0, 0)


@pytest.mark.parametrize(
    ("tag", "title", "kind"),
    [
        ("公共卫生", "尼日利亚确诊埃博拉病例", "health"),
        ("中东", "英国派战机协防沙特", "conflict"),
        ("航空", "波音737获批", "aviation"),
        ("金融", "数字人民币体系升级", "economy"),
        ("两岸", "国台办回应", "politics"),
        ("", "某地举办秋季农产品交易会", "news"),
    ],
)
def test_topic_kind_reads_the_tag_before_the_title(tag, title, kind):
    assert news_art.topic_kind(tag, title) == kind


def test_recent_titles_and_news_context_include_the_lead(monkeypatch, tmp_path):
    plugin = _plugin()
    monkeypatch.setattr(plugin, "_cache_dir", lambda create=True: tmp_path)
    written = {}
    monkeypatch.setattr(daily_ai_news_module, "write_context", lambda key, payload, **_kwargs: written.update(payload))
    now = datetime(2026, 10, 21, 7, 34)
    brief = {
        "lede": "尼日利亚确诊埃博拉病例",
        "lead": {"tag": "公共卫生", "title": "尼日利亚确诊埃博拉病例", "why": "患者自乌干达抵达", "section": "world"},
        "mainland": [{"tag": "金融", "title": "数字人民币体系升级", "why": "推出央行端系统"}],
        "world": [],
    }

    plugin._record_recent_news_titles({"brief": brief}, now)
    plugin._write_news_context({"brief": brief}, now)

    assert "尼日利亚确诊埃博拉病例" in plugin._load_recent_news_titles(now)
    assert [item["title"] for item in written["items"]] == ["尼日利亚确诊埃博拉病例", "数字人民币体系升级"]


def test_an_empty_world_section_never_borrows_mainland_stories():
    plugin = _plugin()
    titles = ["台当局禁赴陆参加孙中山诞辰纪念活动", "数字人民币基础设施服务体系全面升级",
              "工信部建设专精特新赋能中心", "我国应对气候变化工作取得显著成效"]
    items = [
        {"source": "中国新闻网要闻", "section": "mainland", "title": title, "summary": "摘要"} for title in titles
    ] + [{"source": "BBC世界", "section": "world", "title": "English only headline", "summary": ""}]
    brief = plugin._rss_only_brief(items, "", [])

    world = plugin._render_news_section_items(brief, {"items": items}, "world")

    assert world == []


def test_legacy_top_only_briefs_still_split_into_both_columns():
    plugin = _plugin()
    brief = {"top": [{"title": f"旧新闻{index}", "why": ""} for index in range(1, 6)]}

    mainland = plugin._render_news_section_items(brief, {"items": []}, "mainland")
    world = plugin._render_news_section_items(brief, {"items": []}, "world")

    assert [item["title"] for item in mainland] == ["旧新闻1", "旧新闻2", "旧新闻3"]
    assert [item["title"] for item in world] == ["旧新闻4", "旧新闻5"]


def test_rss_cards_skip_english_titles_with_a_translated_word():
    plugin = _plugin()
    items = [
        {"source": "BBC世界", "section": "world", "title": "Zelensky says 美国 intelligence sharing works now", "summary": ""},
        {"source": "中国新闻网财经", "section": "mainland", "title": "数字人民币基础设施服务体系全面升级", "summary": ""},
        {"source": "法国24", "section": "world", "title": "波音737 MAX 10获美国联邦航空局批准", "summary": ""},
    ]

    cards = plugin._rss_news_cards(items, 5)

    assert [card["title"] for card in cards] == ["数字人民币基础设施服务体系全面升级", "波音737 MAX 10获美国联邦航空局批准"]


def test_topic_kind_prefers_a_specific_title_topic_over_a_generic_tag():
    assert news_art.topic_kind("外交", "巴以冲突再起，西岸农作区遭袭致多名平民受伤") == "conflict"
    assert news_art.topic_kind("外交", "中美举行新一轮经贸磋商") == "economy"


def test_page_news_drops_a_column_story_that_shares_the_leads_event():
    plugin = _plugin()
    brief = {
        "lead": {"tag": "外交", "title": "巴以冲突再起，西岸农作区遭袭致多名平民受伤", "why": "事实", "section": "world"},
        "mainland": [{"title": "碳交易市场成交量破10亿吨", "why": "事实", "tag": "公共安全"}],
        "world": [{"title": "以巴冲突再起引发国际关注", "why": "评论", "tag": "外交"}, {"title": "波音获美国监管批准", "why": "事实"}],
    }

    _lead, _mainland, world = plugin._page_news(brief, {"items": []})

    assert [item["title"] for item in world] == ["波音获美国监管批准"]


def test_sanitizer_keeps_english_model_names_next_to_numbers():
    plugin = _plugin()

    assert plugin._strip_untranslated_english_terms("波音737 MAX 10获美国监管批准") == "波音737 MAX 10获美国监管批准"
    assert plugin._strip_untranslated_english_terms("美国拟对伊朗 unknownword 提案") == "美国拟对伊朗提案"


def test_rss_cards_turn_summaries_into_facts_without_datelines():
    plugin = _plugin()
    items = [
        {"source": "中国新闻网要闻", "section": "mainland", "title": "工信部建设一批专精特新赋能中心",
         "summary": "中新网10月21日电 据工业和信息化部网站消息，工业和信息化部21日发布通知。通知明确了任务。"},
        {"source": "中国新闻网财经", "section": "mainland", "title": "数字人民币基础设施服务体系全面升级",
         "summary": "新华社北京10月21日电(记者吴雨)10月21日，数字人民币运营管理中心对外公布服务体系。"},
    ]

    cards = plugin._rss_news_cards(items, 4)

    assert cards[0]["why"] == "工业和信息化部21日发布通知"
    assert cards[1]["why"] == "数字人民币运营管理中心对外公布服务体系"


def test_prompt_bans_vague_commentary_in_facts(monkeypatch):
    plugin = _plugin()
    captured = {}

    class _Responses:
        @staticmethod
        def create(**kwargs):
            captured.update(kwargs)

            class _Response:
                output_text = '{"mainland":[],"world":[]}'

            return _Response()

    class _Client:
        responses = _Responses()

    monkeypatch.setattr(news_brief, "OpenAI", lambda **_kwargs: _Client())

    plugin._summarize_with_openai("key", "gpt-5-nano", {}, [], {}, datetime(2026, 10, 21, 7, 0))

    assert "成为关注焦点" in captured["input"] and "引发关注" in captured["input"]


def test_market_uses_yahoo_when_massive_lags_a_session(monkeypatch):
    plugin = _plugin()

    class FakeMassiveClient:
        def __init__(self, api_key):
            pass

        def fetch_treasury_yields(self, limit=1):
            return []

        def fetch_quote(self, symbol, name):
            if symbol == "^IXIC":
                return {"symbol": symbol, "name": name, "price": 26430.87, "change_pct": 1.11, "as_of": "2026-10-19",
                        "source": "massive", "massive_symbol": "I:COMP"}
            return None

    def fake_yahoo(symbol, name):
        return {"symbol": symbol, "name": name, "price": 26424.82, "change_pct": -0.02, "as_of": "2026-10-20T13:30:00+00:00",
                "source": "yahoo", "closes": [26430.89, 26424.82]}

    monkeypatch.setattr("plugins.daily_ai_news.news_market.load_massive_api_key", lambda device_config: "massive-key")
    monkeypatch.setattr("plugins.daily_ai_news.news_market.MassiveMarketData", FakeMassiveClient)
    monkeypatch.setattr(plugin, "_fetch_yahoo_quote", fake_yahoo)

    row = plugin._fetch_market_snapshot(datetime(2026, 10, 21), object())["groups"]["us_stock"][1]

    assert row["source"] == "yahoo" and row["change_pct"] == -0.02


def test_yahoo_quote_without_daily_history_uses_hourly_bars(monkeypatch):
    plugin = _plugin()
    day = 86400
    today_open = 1792546200  # 2026-10-21 01:30 UTC (09:30 Shanghai)
    hourly_stamps = [today_open - 2 * day + hour * 3600 for hour in range(3)] + \
        [today_open - day + hour * 3600 for hour in range(3)] + [today_open + hour * 3600 for hour in range(2)]
    hourly_closes = [2700.0, 2705.0, 2710.0, 2730.0, 2735.0, 2739.2, 2720.0, 2711.33]
    responses = {
        "1d": _yahoo_chart([2711.33], [today_open + 3600], today_open + 3600, 2711.33, gmtoffset=28800),
        "60m": _yahoo_chart(hourly_closes, hourly_stamps, today_open + 3600, 2711.33, gmtoffset=28800),
    }
    responses["60m"]["chart"]["result"][0]["meta"]["previousClose"] = 2739.2031
    seen = []

    class _Client:
        def request_json(self, method, url, params=None, **_kwargs):
            seen.append(params)
            return _ChartResponse(responses[params["interval"]])

    monkeypatch.setattr(news_market, "get_http_client", lambda: _Client())

    row = plugin._fetch_yahoo_quote("399006.SZ", "创业板指")

    assert [params["interval"] for params in seen] == ["1d", "60m"]
    # Measured from the previous day's last hourly bar (2739.2), not the chart's previousClose.
    assert row["change_pct"] == pytest.approx((2711.33 / 2739.2 - 1) * 100, abs=0.005)
    assert row["closes"] == [2710.0, 2739.2, 2711.33]


def test_long_column_titles_take_a_second_line_when_the_column_has_room(monkeypatch):
    plugin = _plugin()
    long_title = "工信部：“十五五”期间我国将建设一批专精特新赋能中心并推广"
    payload = _page_payload()
    payload["brief"]["mainland"] = [
        {"tag": "要闻", "title": long_title, "why": "按先试点后推广模式推进"},
        {"tag": "金融", "title": "数字人民币体系升级", "why": "推出央行端系统"},
    ]

    _image, seen = _rendered_text(monkeypatch, plugin, payload)

    first, second = next((line, seen[index + 1]) for index, line in enumerate(seen) if long_title.startswith(line) and line != long_title)
    assert first + second == long_title
    assert not any(line.endswith("…") and long_title.startswith(line[:-1]) for line in seen)


def test_a_full_column_keeps_every_story_on_one_line_before_adding_second_lines():
    plugin = _plugin()
    page = news_layout.NewsPage(plugin, Image.new("RGB", (800, 480), "white"), plugin._page_palette({"mode": "day"}), "Microsoft YaHei")
    items = [{"title": f"足够长的标题需要两行才能完整显示在栏目里的第{index}条新闻内容", "why": "", "tag": ""} for index in range(4)]

    lines, gap = page.plan_column(items, 330, news_layout.COLUMN_BOTTOM - news_layout.COLUMN_TOP - 32)

    # All four stories stay; the tighter gap frees room for two clipped titles' second lines.
    assert lines == [2, 2, 1, 1]
    assert gap == news_layout.COMPACT_ITEM_GAP


def test_a_full_column_shows_five_one_line_stories():
    plugin = _plugin()
    page = news_layout.NewsPage(plugin, Image.new("RGB", (800, 480), "white"), plugin._page_palette({"mode": "day"}), "Microsoft YaHei")
    items = [{"title": f"足够长的标题需要两行才能完整显示在栏目里的第{index}条新闻内容", "why": "", "tag": ""} for index in range(5)]

    lines, gap = page.plan_column(items, 330, news_layout.COLUMN_BOTTOM - news_layout.COLUMN_TOP - 32)

    # Every story is shown before any title gets a second line.
    assert lines == [1, 1, 1, 1, 1]
    assert gap == news_layout.COMPACT_ITEM_GAP


def test_the_lead_keeps_a_two_sentence_fact():
    plugin = _plugin()
    fact = "台湾多地宣布23日停班停课，台股也休市一天。气象部门预计台风将在东部沿海登陆，最大风力可达14级。"
    lead = {"tag": "两岸", "title": "台风蔷琵临近台湾多地停课", "why": fact, "section": "mainland"}
    items = [{"source": "中国新闻网要闻", "section": "mainland", "title": "台风蔷琵临近台湾多地停课", "summary": ""}]

    checked = plugin._checked_lead(lead, items, items)

    assert len(fact) > news_brief.NEWS_FACT_MAX_CHARS
    assert checked["why"] == fact


def test_a_column_keeps_the_normal_gap_when_no_title_is_clipped():
    plugin = _plugin()
    page = news_layout.NewsPage(plugin, Image.new("RGB", (800, 480), "white"), plugin._page_palette({"mode": "day"}), "Microsoft YaHei")
    items = [{"title": f"短标题{index}", "why": "", "tag": ""} for index in range(4)]

    lines, gap = page.plan_column(items, 330, news_layout.COLUMN_BOTTOM - news_layout.COLUMN_TOP - 32)

    assert (lines, gap) == ([1, 1, 1, 1], news_layout.ITEM_GAP)


def test_story_tag_sits_under_the_number_and_the_fact_follows_it(monkeypatch):
    plugin = _plugin()
    chips, texts = {}, {}
    original_chip = news_layout.NewsPage.chip
    original_text = ImageDraw.ImageDraw.text

    def record_chip(self, x, y, text, colour, *args, **kwargs):
        width = original_chip(self, x, y, text, colour, *args, **kwargs)
        chips.setdefault(text, (x, width))
        return width

    def capture_text(self, xy, text, *args, **kwargs):
        texts.setdefault(str(text), xy)
        return original_text(self, xy, text, *args, **kwargs)

    monkeypatch.setattr(news_layout.NewsPage, "chip", record_chip)
    monkeypatch.setattr(ImageDraw.ImageDraw, "text", capture_text)
    payload = _page_payload()
    payload["brief"]["mainland"] = [{"tag": "金融", "title": "数字人民币体系升级", "why": "推出央行端系统"}]

    plugin._render((800, 480), {"brief_title": "整点新闻"}, payload, datetime(2026, 10, 21), {"mode": "day"})

    number_x = texts["01"][0]
    chip_x, chip_w = chips["金融"]
    assert chip_x == number_x == news_layout.MARGIN
    assert texts["推出央行端系统"][0] == chip_x + chip_w + 6


def test_yahoo_as_of_follows_the_price_session_when_it_is_newer_than_the_last_bar(monkeypatch):
    plugin = _plugin()
    _patch_chart(
        monkeypatch,
        _yahoo_chart([7690.73, 7712.57], [MONDAY_OPEN, TUESDAY_OPEN], TUESDAY_OPEN + DAY + 3600, 7750.0),
    )

    row = plugin._fetch_yahoo_quote("^GSPC", "标普500")

    assert row["as_of"].startswith("2026-10-21")


@pytest.mark.parametrize(
    ("lead", "story"),
    [
        ("中国人民银行下调存款准备金率", "中国人民解放军东部战区开展联合演训"),
        ("外交部发言人就日本防卫政策答问", "外交部发言人宣布王毅将访问欧洲"),
        ("美国总统特朗普抵达北京国事访问", "美国总统签署对欧盟关税行政令"),
        ("联合国秘书长呼吁苏丹停火", "联合国秘书长谴责加沙空袭"),
    ],
)
def test_page_news_keeps_stories_that_only_share_a_subject_with_the_lead(lead, story):
    plugin = _plugin()
    brief = {"lead": {"title": lead, "why": "", "section": "mainland"}, "mainland": [{"title": story, "why": ""}], "world": []}

    _lead, mainland, _world = plugin._page_news(brief, {"items": []})

    assert [item["title"] for item in mainland] == [story]


def test_sanitizer_drops_an_english_name_before_a_date():
    plugin = _plugin()

    assert plugin._strip_untranslated_english_terms("Trump 20日宣布对欧盟加征关税") == "20日宣布对欧盟加征关税"


def test_a_rejected_lead_does_not_stay_as_the_lede():
    plugin = _plugin()
    brief = {
        "lede": "国务院宣布全面取消房贷利率下限",
        "lead": {"tag": "政策", "title": "国务院宣布全面取消房贷利率下限", "why": "编造", "section": "mainland"},
        "mainland": [{"tag": "金融", "title": "数字人民币基础设施服务体系全面升级", "why": "推出央行端系统"}],
        "world": [],
    }
    items = [{"source": "中国新闻网财经", "section": "mainland", "title": "数字人民币基础设施服务体系全面升级", "summary": ""}]

    result = plugin._postprocess_brief_news(brief, items)

    assert result["lead"] is None
    assert result["lede"] == "数字人民币基础设施服务体系全面升级"


def test_list_or_number_tags_never_render_as_python_reprs():
    plugin = _plugin()
    brief = {
        "lead": {"tag": ["经济", "贸易"], "title": "美欧达成新贸易协议", "why": "事实", "section": "world"},
        "mainland": [{"tag": 2026, "title": "数字人民币体系升级", "why": "事实"}],
        "world": [],
    }

    lead, mainland, _world = plugin._page_news(brief, {"items": []})

    assert lead["tag"] == "经济"
    # A number is not a category; the title (数字人民币) places it.
    assert mainland[0]["tag"] == "金融"


@pytest.mark.parametrize("dimensions", [(640, 400), (400, 300), (480, 800), (250, 122)])
def test_render_fits_other_display_sizes_without_crashing(dimensions):
    plugin = _plugin()

    image = plugin._render(dimensions, {"brief_title": "整点新闻"}, _page_payload(), datetime(2026, 10, 21), {"mode": "day"})

    assert image.size == dimensions


def test_world_stories_are_capped_at_what_the_column_shows():
    plugin = _plugin()
    page = news_layout.NewsPage(plugin, Image.new("RGB", (800, 480), "white"), plugin._page_palette({"mode": "day"}), "Microsoft YaHei")
    items = [{"title": f"世界短新闻{index}", "why": "", "tag": ""} for index in range(daily_ai_news_module.WORLD_NEWS_MAX)]

    lines, _gap = page.plan_column(items, 330, news_layout.COLUMN_BOTTOM - news_layout.COLUMN_TOP - 32)

    assert len(lines) == daily_ai_news_module.WORLD_NEWS_MAX


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("法国正式承认巴勒斯坦国", "王毅会见巴勒斯坦外长"),
        ("约旦河西岸定居者焚烧清真寺", "以军在约旦河西岸逮捕百名嫌犯"),
        ("巴勒斯坦总统访华", "法国承认巴勒斯坦国"),
    ],
)
def test_a_shared_place_name_alone_does_not_make_two_stories_one(left, right):
    assert not _plugin()._similar_news_title(left, right)


def test_lede_follows_the_deduplicated_first_story_when_the_ai_gives_no_lead():
    plugin = _plugin()
    brief = plugin._parse_brief_json(
        '{"mainland": [{"title": "国务院宣布全面取消房贷利率下限", "why": "编造"},'
        ' {"title": "数字人民币基础设施服务体系全面升级", "why": "事实"}], "world": []}'
    )
    items = [{"source": "中国新闻网财经", "section": "mainland", "title": "数字人民币基础设施服务体系全面升级", "summary": ""}]

    result = plugin._postprocess_brief_news(brief, items)

    assert result["lede"] == "数字人民币基础设施服务体系全面升级"


def test_news_context_keeps_every_shown_story_after_the_lead(monkeypatch):
    plugin = _plugin()
    written = {}
    monkeypatch.setattr(daily_ai_news_module, "write_context", lambda key, payload, **_kwargs: written.update(payload))
    mainland = [{"title": f"国内新闻第{index}条", "why": ""} for index in range(4)]
    world = [{"title": f"国际新闻第{index}条", "why": ""} for index in range(4)]
    brief = {"lede": "头条", "lead": {"title": "头条新闻", "why": ""}, "mainland": mainland, "world": world, "top": mainland + world}

    plugin._write_news_context({"brief": brief}, datetime(2026, 10, 21))

    assert len(written["items"]) == 9 and written["items"][-1]["title"] == "国际新闻第3条"


def test_a_tag_list_skips_blank_and_boolean_entries():
    assert DailyAINews._field_text([True, "经济"]) == "经济"
    assert DailyAINews._field_text(["", " ", "经济"]) == "经济"


def test_postprocess_drops_a_mainland_copy_of_a_world_story():
    plugin = _plugin()
    brief = {
        "mainland": [
            {"tag": "社会", "title": "乌克兰愿与美俄三方会晤", "why": "泽连斯基表示愿同美俄举行三方会晤"},
            {"tag": "司法", "title": "法院发布司法责任追究条例", "why": "明确司法追究范围"},
        ],
        "world": [{"tag": "外交", "title": "乌克兰期待美俄三方会谈", "why": "泽连斯基表达愿与美俄会谈的立场"}],
    }
    items = [
        {"source": "中国新闻网即时", "section": "mainland", "title": "泽连斯基：乌克兰愿与美俄举行三方会晤", "summary": ""},
        {"source": "中国新闻网要闻", "section": "mainland", "title": "最高法发布司法责任追究条例", "summary": ""},
    ]

    result = plugin._postprocess_brief_news(brief, items)

    assert [item["title"] for item in result["mainland"]] == ["法院发布司法责任追究条例"]
    assert [item["title"] for item in result["world"]] == ["乌克兰期待美俄三方会谈"]


def test_page_news_drops_a_mainland_copy_of_a_world_story_from_cached_briefs():
    plugin = _plugin()
    brief = {
        "lead": {"title": "爱尔眼科陈杰获全国退役军人服务先进个人", "why": "", "section": "mainland"},
        "mainland": [{"title": "乌克兰愿与美俄三方会晤", "why": ""}, {"title": "法院发布司法责任追究条例", "why": ""}],
        "world": [{"title": "乌克兰期待美俄三方会谈", "why": ""}],
    }

    _lead, mainland, world = plugin._page_news(brief, {"items": []})

    assert [item["title"] for item in mainland] == ["法院发布司法责任追究条例"]
    assert [item["title"] for item in world] == ["乌克兰期待美俄三方会谈"]


def test_prompt_steers_the_lead_to_hard_news_and_mainland_to_china(monkeypatch):
    plugin = _plugin()
    captured = {}

    class _Responses:
        @staticmethod
        def create(**kwargs):
            captured.update(kwargs)

            class _Response:
                output_text = '{"mainland":[],"world":[]}'

            return _Response()

    class _Client:
        responses = _Responses()

    monkeypatch.setattr(news_brief, "OpenAI", lambda **_kwargs: _Client())

    plugin._summarize_with_openai("key", "gpt-5-nano", {}, [], {}, datetime(2026, 10, 21, 7, 0))

    request = captured["input"]
    assert "不要选人物表彰" in request
    assert "外国新闻" in request and "不要放进 mainland" in request
    assert "表彰、荣誉" in request and "不能复述标题" in request


def test_summary_facts_drop_a_day_only_leading_date():
    assert DailyAINews._summary_fact("21日，国新办就中国积极应对气候变化举行发布会。更多内容") == "国新办就中国积极应对气候变化举行发布会"
    assert DailyAINews._summary_fact("中新网10月21日电 21日，国务院台办举行例行新闻发布会。") == "国务院台办举行例行新闻发布会"


def test_hourly_fallback_measures_from_the_previous_days_last_bar_not_a_stale_meta_close(monkeypatch):
    plugin = _plugin()
    day = 86400
    today_open = 1792546200  # 2026-10-21 01:30 UTC (09:30 Shanghai)
    stamps = [today_open - day + hour * 3600 for hour in range(3)] + [today_open + hour * 3600 for hour in range(2)]
    responses = {
        "1d": _yahoo_chart([2711.33], [today_open + 3600], today_open + 3600, 2711.33, gmtoffset=28800),
        "60m": _yahoo_chart([2730.0, 2735.0, 2739.69, 2720.0, 2711.33], stamps, today_open + 3600, 2711.33, gmtoffset=28800),
    }
    # Yahoo's hourly previousClose can be a session behind (seen live: 3730.92 for 上证 vs a 3735.37 close).
    responses["60m"]["chart"]["result"][0]["meta"]["previousClose"] = 2700.0

    class _Client:
        def request_json(self, method, url, params=None, **_kwargs):
            return _ChartResponse(responses[params["interval"]])

    monkeypatch.setattr(news_market, "get_http_client", lambda: _Client())

    row = plugin._fetch_yahoo_quote("399006.SZ", "创业板指")

    assert row["change_pct"] == pytest.approx((2711.33 / 2739.69 - 1) * 100, abs=0.005)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("SpaceX/特斯拉合并传闻受关注", "SpaceX/特斯拉合并传闻受关注"),
        ("OpenAI 发布新模型", "OpenAI发布新模型"),
        ("美国FAA批准 NASA 新任务", "美国FAA批准NASA新任务"),
        ("unknownword/特斯拉合并传闻受关注", "特斯拉合并传闻受关注"),
        ("特斯拉合并传闻、 unknownword", "特斯拉合并传闻"),
    ],
)
def test_sanitizer_keeps_brand_names_and_cleans_dangling_separators(text, expected):
    assert _plugin()._strip_untranslated_english_terms(text) == expected


def test_a_notice_shows_as_the_normal_cached_detail_not_a_red_warning(monkeypatch):
    plugin = _plugin()
    fills = {}
    original_text = ImageDraw.ImageDraw.text

    def capture_text(self, xy, text, *args, **kwargs):
        fills[str(text)] = kwargs.get("fill")
        return original_text(self, xy, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", capture_text)
    payload = _page_payload(from_cache=True, notice="已按今日 API 限额复用计划内最新缓存。")

    plugin._render((800, 480), {"brief_title": "整点新闻"}, payload, datetime(2026, 10, 21), {"mode": "day"})

    assert "07:34 更新 · 3 个信源 · 缓存" in fills
    assert fills["07:34 更新 · 3 个信源 · 缓存"] == daily_ai_news_module.get_theme_palette("day")["ink"]
    assert not any("限额" in text for text in fills)


def test_cached_titles_and_facts_are_tidied_when_drawn():
    plugin = _plugin()
    brief = {
        "mainland": [{"title": "前三季度全国铁路投资6012亿元", "why": ""}],
        "world": [{"tag": "金融", "title": "/特斯拉传合并传闻受关注", "why": "OpenAI 发布新模型、"}],
    }

    _lead, _mainland, world = plugin._page_news(brief, {"items": []})

    assert world[0]["title"] == "特斯拉传合并传闻受关注"
    assert world[0]["why"] == "OpenAI发布新模型"


@pytest.mark.parametrize(
    ("tag", "title", "expected"),
    [
        ("公共安全", "上海浦东机场口岸出入境破3000万", "航空"),
        ("公共安全", "中钢协：9月粗钢产量增0.1%", "产业"),
        ("公共安全", "农产品批发价指数微增0.22点", "经济"),
        ("科技治理", "埃博拉疫情在非洲再起关注", "公卫"),
        ("国际冲突", "美俄对话前景再受关注", "外交"),
        ("国际冲突", "局势再受关注", "冲突"),
        ("公共卫生", "某地开展专项行动", "公卫"),
        ("两岸", "国台办回应", "两岸"),
        ("", "某地举办秋季民俗文化节", "要闻"),
    ],
)
def test_tags_outside_the_vocabulary_are_mapped_from_the_title(tag, title, expected):
    assert DailyAINews._normalized_tag(tag, title) == expected


def test_postprocess_puts_every_tag_in_the_vocabulary():
    plugin = _plugin()
    brief = {
        "lead": {"tag": "公共安全", "title": "上海浦东机场口岸出入境破3000万", "why": "事实", "section": "world"},
        "mainland": [],
        "world": [{"tag": "科技治理", "title": "埃博拉疫情在非洲再起关注", "why": "事实"}],
    }

    result = plugin._postprocess_brief_news(brief, [{"source": "BBC世界", "section": "world", "title": "Ebola", "summary": ""}])

    assert result["lead"]["tag"] == "航空"
    assert result["world"][0]["tag"] == "公卫"


def test_cached_tags_are_mapped_when_drawn():
    plugin = _plugin()
    brief = {
        "mainland": [{"tag": "公共安全", "title": "中钢协：9月粗钢产量增0.1%", "why": ""},
                     {"tag": "社会", "title": "江淮分水岭创意创业", "why": ""}],
        "world": [],
    }

    lead, mainland, _world = plugin._page_news(brief, {"items": []})

    assert lead["tag"] == "产业"
    assert mainland[0]["tag"] == "社会"


def test_prompt_lists_the_fixed_tag_vocabulary(monkeypatch):
    plugin = _plugin()
    captured = {}

    class _Responses:
        @staticmethod
        def create(**kwargs):
            captured.update(kwargs)

            class _Response:
                output_text = '{"mainland":[],"world":[]}'

            return _Response()

    class _Client:
        responses = _Responses()

    monkeypatch.setattr(news_brief, "OpenAI", lambda **_kwargs: _Client())

    plugin._summarize_with_openai("key", "gpt-5-nano", {}, [], {}, datetime(2026, 10, 21, 7, 0))

    assert "只能从" in captured["input"]
    assert all(tag in captured["input"] for tag in news_brief.NEWS_TAGS)


@pytest.mark.parametrize(
    ("tag", "title", "expected"),
    [
        ("环境", "尼日利亚发现埃博拉病例", "公卫"),
        ("科技", "FAA批准波音737-10进入服务", "航空"),
        ("时政", "山东煤电新能源融合加速", "能源"),
        ("社会", "云南新能源电池产值增速42.0%", "能源"),
        ("社会", "锂电储能项目落地", "能源"),
        ("要闻", "某地新增输入病例", "公卫"),
        ("要闻", "国务院部署秋冬农业生产", "时政"),
        ("要闻", "某地举办秋季民俗文化节", "要闻"),
        ("时政", "外交部敦促美方停止对古巴封锁", "外交"),
        ("社会", "三明举办火场生存挑战赛", "社会"),
        ("冲突", "普京泽连斯基相关动态再受关注", "冲突"),
        ("外交", "美国总统宣布对欧盟加征关税", "外交"),
    ],
)
def test_a_decisive_title_keyword_overrides_a_wrong_vocabulary_tag(tag, title, expected):
    assert DailyAINews._normalized_tag(tag, title) == expected


def test_english_written_against_chinese_is_translated_or_dropped():
    plugin = _plugin()

    assert plugin._strip_untranslated_english_terms("Kyiv遭导弹袭击后美方谈判意向") == "基辅遭导弹袭击后美方谈判意向"
    assert plugin._strip_untranslated_english_terms("总统Trump宣布新关税") == "总统宣布新关税"
    assert plugin._strip_untranslated_english_terms("AIPAC调整捐助策略") == "AIPAC调整捐助策略"
    assert plugin._strip_untranslated_english_terms("US大选进入最后一周") == "美国大选进入最后一周"