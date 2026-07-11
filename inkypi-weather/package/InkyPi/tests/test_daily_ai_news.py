import sys
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageStat

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import plugins.daily_ai_news.daily_ai_news as daily_ai_news_module
from plugins.daily_ai_news.daily_ai_news import DailyAINews, TITLE_BACKGROUND_IMAGE, TITLE_BACKGROUND_SIZE, TITLE_WORDMARK_IMAGE, TITLE_WORDMARK_SIZE, SECTION_WORDMARK_IMAGES, SECTION_WORDMARK_SIZES


def _plugin():
    return DailyAINews({"id": "daily_ai_news"})


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

    effective = plugin._effective_feeds_text(daily_ai_news_module.LEGACY_DEFAULT_FEEDS)
    urls = {url for _name, url in plugin._parse_feeds(effective)}

    assert "https://www.chinanews.com.cn/rss/importnews.xml" in urls
    assert "https://www.chinanews.com.cn/rss/china.xml" in urls
    assert "https://www.chinanews.com.cn/rss/scroll-news.xml" in urls
    assert "https://www.pbs.org/newshour/feeds/rss/headlines" in urls
    assert "https://abcnews.go.com/abcnews/internationalheadlines" in urls


def test_effective_feeds_expands_world_only_previous_default_feed_set():
    plugin = _plugin()

    effective = plugin._effective_feeds_text(daily_ai_news_module.LEGACY_WORLD_ONLY_DEFAULT_FEEDS)
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
    feeds = plugin._parse_feeds(daily_ai_news_module.DEFAULT_FEEDS)
    sections = [plugin._feed_source_and_section(name, url) for name, url in feeds]

    assert ("中国新闻网要闻", "mainland") in sections
    assert ("中国新闻网国内", "mainland") in sections
    assert ("中国新闻网即时", "mainland") in sections
    assert ("BBC世界", "world") in sections
    assert ("半岛电视台", "world") in sections


def test_default_mainland_feeds_replace_dead_sources_with_fresh_desks():
    plugin = _plugin()
    urls = {url for _name, url in plugin._parse_feeds(daily_ai_news_module.DEFAULT_MAINLAND_FEEDS)}

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
        daily_ai_news_module,
        "get_http_client",
        lambda: FakeClient(),
    )
    monkeypatch.setattr(daily_ai_news_module.feedparser, "parse", fake_parse)

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
        daily_ai_news_module,
        "get_http_client",
        lambda: FakeClient(),
    )
    monkeypatch.setattr(daily_ai_news_module.feedparser, "parse", fake_parse)

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
        for index in range(1, 7)
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
        daily_ai_news_module.json.dumps(
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
        {"model": "gpt-5-nano", "feed_urls": daily_ai_news_module.DEFAULT_FEEDS, "max_items": "6"},
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
        daily_ai_news_module.json.dumps(
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
        {"model": "gpt-5-nano", "feed_urls": daily_ai_news_module.DEFAULT_FEEDS, "max_items": "6"},
        FakeDeviceConfig(),
        datetime(2026, 6, 17),
    )

    assert brief["from_cache"] is True
    assert brief["brief"]["top"][0]["title"] == "旧中文新闻"
    assert "中文缓存" in brief["warning"]


def test_base_background_uses_plain_theme_color_in_night_mode():
    plugin = _plugin()
    bg = (7, 11, 13)

    img = plugin._base_background((8, 6), bg, "night")

    assert img.getpixel((0, 0)) == bg
    assert img.getpixel((7, 5)) == bg


def test_market_summary_parts_keep_previous_us_close_label_and_values():
    plugin = _plugin()
    rows = [
        {"name": "标普500", "change_pct": 1.69, "as_of": "2026-06-01"},
        {"name": "纳斯达克", "change_pct": 2.82, "as_of": "2026-06-01"},
        {"name": "道琼斯", "change_pct": -0.99, "as_of": "2026-06-01"},
    ]

    prefix, parts = plugin._market_summary_parts("us_stock", rows, "2026-06-02")

    assert prefix == "上日 "
    assert parts == [("标普", 1.69), ("纳指", 2.82), ("道指", -0.99)]
    assert plugin._market_summary("us_stock", rows, "2026-06-02") == "上日 标普+1.69% 纳指+2.82% 道指-.99%"


def test_market_change_color_uses_us_convention():
    plugin = _plugin()
    up = (0, 180, 90)
    down = (220, 40, 50)
    neutral = (30, 30, 30)

    assert plugin._market_change_color(0.01, up, down, neutral) == up
    assert plugin._market_change_color(-0.01, up, down, neutral) == down
    assert plugin._market_change_color(0.0, up, down, neutral) == neutral


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
        }

    monkeypatch.setattr("plugins.daily_ai_news.daily_ai_news.load_massive_api_key", lambda device_config: "massive-key")
    monkeypatch.setattr("plugins.daily_ai_news.daily_ai_news.MassiveMarketData", FakeMassiveClient)
    monkeypatch.setattr(plugin, "_fetch_yahoo_quote", fake_yahoo)

    snapshot = plugin._fetch_market_snapshot(datetime(2026, 6, 3), object())

    assert snapshot["macro"] == {
        "source": "massive",
        "treasury_yields": [{"date": "2026-06-02", "yield_2_year": 4.1, "yield_10_year": 4.3}],
    }
    assert snapshot["groups"]["us_stock"][0]["source"] == "massive"
    assert snapshot["groups"]["us_stock"][0]["massive_symbol"] == "I:SPX"
    assert "^GSPC" not in yahoo_calls
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
        return {"symbol": symbol, "name": name, "price": prices.get(symbol, 100.0), "change_pct": 1.0, "as_of": "2026-06-19", "source": "yahoo"}

    monkeypatch.setattr("plugins.daily_ai_news.daily_ai_news.load_massive_api_key", lambda device_config: "massive-key")
    monkeypatch.setattr("plugins.daily_ai_news.daily_ai_news.MassiveMarketData", FakeMassiveClient)
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
        "a_share": {"summary": "AI 摘要可用", "analysis": "RSS 条目已更新"},
        "sources": ["BBC中文"],
    }

    sanitized = plugin._sanitize_brief_visible_text(brief)

    assert sanitized["lede"] == "BBC中文关注美国与伊朗重建方案"
    assert sanitized["top"][0]["title"] == "美国拟对伊朗提案"
    assert sanitized["top"][0]["why"] == "NPR关注制裁与加沙"
    assert sanitized["a_share"]["summary"] == "AI摘要可用"
    assert sanitized["a_share"]["analysis"] == "RSS条目已更新"


def test_static_render_labels_use_simplified_chinese():
    plugin = _plugin()

    assert daily_ai_news_module.SECTION_LABELS["top"] == "大陆新闻"
    assert daily_ai_news_module.SECTION_LABELS["quick"] == "世界快报"
    assert plugin._theme_label({"mode": "night"}) == "午夜简报"
    assert plugin._theme_label({"mode": "day"}) == "日间简报"
    assert plugin._footer_text({"generated_at": "2026-06-17T21:12:00"}, {"sources": []}).startswith("来源: 新闻源 + AI摘要")


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

def test_draw_news_items_fit_prefers_style_that_uses_available_height(monkeypatch):
    plugin = _plugin()
    image = Image.new("RGB", (420, 360), "white")
    draw = ImageDraw.Draw(image)
    selected_sizes = []

    def fake_prepare(_draw, _items, _width, font_family, style):
        needed_by_title_size = {24: 360, 23: 150, 22: 292, 21: 230}
        needed_total = needed_by_title_size.get(style["title"], 90)
        headline_font = plugin._font(font_family, style["title"], "bold")
        why_font = plugin._font(font_family, style["why"])
        rows = [([f"title-size-{style['title']}"], [], needed_total, headline_font, why_font, style)]
        return rows, needed_total

    original_text = ImageDraw.ImageDraw.text

    def capture_text(self, xy, text, *args, **kwargs):
        value = str(text)
        if value.startswith("title-size-"):
            selected_sizes.append(int(value.rsplit("-", 1)[-1]))
        return original_text(self, xy, text, *args, **kwargs)

    monkeypatch.setattr(plugin, "_prepare_news_rows_for_style", fake_prepare)
    monkeypatch.setattr(ImageDraw.ImageDraw, "text", capture_text)

    plugin._draw_news_items(
        draw,
        [{"title": "短新闻", "why": "短正文"}],
        0,
        0,
        360,
        plugin._font("Microsoft YaHei", 18, "bold"),
        plugin._font("Microsoft YaHei", 16),
        (180, 120, 20),
        (0, 0, 0),
        (70, 70, 70),
        max_y=300,
        force_all=True,
        fit_family="Microsoft YaHei",
    )

    assert selected_sizes == [22]


def test_draw_news_items_fit_uses_larger_type_for_sparse_news(monkeypatch):
    plugin = _plugin()
    image = Image.new("RGB", (420, 220), "white")
    draw = ImageDraw.Draw(image)
    original_font = plugin._font
    font_calls = []

    def record_font(family, size, weight="normal"):
        font_calls.append((family, size, weight))
        return original_font(family, size, weight)

    monkeypatch.setattr(plugin, "_font", record_font)

    y = plugin._draw_news_items(
        draw,
        [
            {"title": "短标题一", "why": "短正文一"},
            {"title": "短标题二", "why": "短正文二"},
        ],
        0,
        0,
        360,
        original_font("Microsoft YaHei", 18, "bold"),
        original_font("Microsoft YaHei", 16),
        (180, 120, 20),
        (0, 0, 0),
        (70, 70, 70),
        max_y=196,
        force_all=True,
        fit_family="Microsoft YaHei",
    )

    assert y <= 196
    # Sparse news should scale type up beyond the 18/16 defaults; exact sizes
    # vary with the rendering environment's font metrics.
    assert max(size for _family, size, weight in font_calls if weight == "bold") > 18
    assert max(size for _family, size, weight in font_calls if weight == "normal") > 16


def test_draw_news_items_fit_caps_body_size_for_many_short_news(monkeypatch):
    plugin = _plugin()
    image = Image.new("RGB", (420, 360), "white")
    draw = ImageDraw.Draw(image)
    original_text = ImageDraw.ImageDraw.text
    why_sizes = []

    def capture_text(self, xy, text, *args, **kwargs):
        if str(text).startswith("多新闻正文"):
            font = kwargs.get("font")
            why_sizes.append(getattr(font, "size", None))
        return original_text(self, xy, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", capture_text)
    items = [
        {"title": f"多新闻标题{index}", "why": f"多新闻正文{index}"}
        for index in range(1, 6)
    ]

    y = plugin._draw_news_items(
        draw,
        items,
        0,
        0,
        360,
        plugin._font("Microsoft YaHei", 18, "bold"),
        plugin._font("Microsoft YaHei", 16),
        (20, 120, 180),
        (0, 0, 0),
        (70, 70, 70),
        max_y=320,
        force_all=True,
        fit_family="Microsoft YaHei",
    )

    assert y <= 320
    assert len(why_sizes) == 5
    assert max(size for size in why_sizes if size is not None) <= 15


def test_draw_news_items_fit_shrinks_without_dropping_body(monkeypatch):
    plugin = _plugin()
    image = Image.new("RGB", (420, 240), "white")
    draw = ImageDraw.Draw(image)
    original_text = ImageDraw.ImageDraw.text
    drawn_text = []

    def capture_text(self, xy, text, *args, **kwargs):
        drawn_text.append(str(text))
        return original_text(self, xy, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", capture_text)
    items = [
        {"title": f"第{index}条国际新闻标题包含较多事实细节与地点", "why": f"第{index}条正文说明影响范围和后续观察重点"}
        for index in range(1, 5)
    ]

    y = plugin._draw_news_items(
        draw,
        items,
        0,
        0,
        330,
        plugin._font("Microsoft YaHei", 18, "bold"),
        plugin._font("Microsoft YaHei", 16),
        (20, 120, 180),
        (0, 0, 0),
        (70, 70, 70),
        max_y=196,
        start_index=4,
        force_all=True,
        fit_family="Microsoft YaHei",
    )

    rendered = "".join(drawn_text)
    assert y <= 196
    assert "..." not in rendered
    for item in items:
        assert item["title"][:8] in rendered
        assert item["why"] in rendered

def test_draw_news_items_fit_keeps_four_item_news_readable_when_given_full_news_area(monkeypatch):
    plugin = _plugin()
    image = Image.new("RGB", (430, 340), "white")
    draw = ImageDraw.Draw(image)
    original_text = ImageDraw.ImageDraw.text
    why_sizes = []
    title_sizes = []
    drawn_text = []

    def capture_text(self, xy, text, *args, **kwargs):
        value = str(text)
        drawn_text.append(value)
        font = kwargs.get("font")
        if font is not None:
            if value.startswith("第") and "标题" in value:
                title_sizes.append(getattr(font, "size", None))
            if value.startswith("第") and "正文" in value:
                why_sizes.append(getattr(font, "size", None))
        return original_text(self, xy, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", capture_text)
    items = [
        {"title": f"第{index}条新闻标题包含较多事实细节", "why": f"第{index}条正文说明影响范围"}
        for index in range(1, 5)
    ]

    y = plugin._draw_news_items(
        draw,
        items,
        0,
        0,
        360,
        plugin._font("Microsoft YaHei", 18, "bold"),
        plugin._font("Microsoft YaHei", 16),
        (20, 120, 180),
        (0, 0, 0),
        (70, 70, 70),
        max_y=296,
        force_all=True,
        fit_family="Microsoft YaHei",
    )

    rendered = "".join(drawn_text)
    # Allow one text line of overflow tolerance: absolute pixel height varies
    # with the rendering environment's font metrics.
    assert y <= 296 + 8
    assert "..." not in rendered
    assert why_sizes and min(size for size in why_sizes if size is not None) >= 15
    assert title_sizes and min(size for size in title_sizes if size is not None) >= 18


def test_draw_news_items_fit_fills_column_when_short_titles_undershoot():
    plugin = _plugin()
    image = Image.new("RGB", (800, 480), "white")
    draw = ImageDraw.Draw(image)
    items = [
        {"title": "王毅同芬兰外长瓦尔托宁会谈", "why": ""},
        {"title": "中国成功发射千帆极轨15组卫星", "why": ""},
        {"title": "四川绵竹连发三次四级以上地震", "why": ""},
        {"title": "全国农业普查通知印发", "why": ""},
    ]

    end_y = plugin._draw_news_items_fit(
        draw,
        items,
        24,
        164,
        388,
        (200, 60, 40),
        (0, 0, 0),
        (90, 90, 90),
        360,
        1,
        "Microsoft YaHei",
        True,
    )

    assert end_y <= 360 + 4
    # the column should end near its bottom edge instead of leaving a large void
    assert 360 - end_y <= 16


def test_draw_news_items_fit_fills_column_for_two_long_items():
    plugin = _plugin()
    image = Image.new("RGB", (800, 480), "white")
    draw = ImageDraw.Draw(image)
    items = [
        {
            "title": "河南三门峡水利枢纽开启前汛调水调沙，4小时后下泄增至3200立方米/秒",
            "why": "报道聚焦黄河主汛前调水调沙的最新进展和水库调度水平，关系区域防汛与生态安全。",
        },
        {
            "title": "新疆调研强调产业赋能与就业导向，推动中央企业援疆再出发",
            "why": "报道中央企业在新疆产业布局与吸纳就业的具体动向和新举措，影响区域经济和民生。",
        },
    ]

    end_y = plugin._draw_news_items_fit(
        draw,
        items,
        24,
        164,
        388,
        (200, 60, 40),
        (0, 0, 0),
        (90, 90, 90),
        360,
        1,
        "Microsoft YaHei",
        True,
    )

    assert end_y <= 360 + 4
    assert 360 - end_y <= 16


def test_render_keeps_market_modules_while_shrinking_dense_news(monkeypatch):
    plugin = _plugin()
    market_calls = []
    drawn_text = []
    original_text = ImageDraw.ImageDraw.text

    def capture_market_module(*args, **kwargs):
        market_calls.append(args)
        return 0

    def capture_text(self, xy, text, *args, **kwargs):
        drawn_text.append(str(text))
        return original_text(self, xy, text, *args, **kwargs)

    monkeypatch.setattr(plugin, "_draw_market_module", capture_market_module)
    monkeypatch.setattr(ImageDraw.ImageDraw, "text", capture_text)
    mainland = [
        {"title": f"第{index}条大陆新闻标题包含较多事实细节与地点", "why": f"第{index}条大陆正文说明影响范围和后续观察重点"}
        for index in range(1, 5)
    ]
    world = [
        {"title": f"第{index}条世界新闻标题包含较多事实细节与地点", "why": f"第{index}条世界正文说明影响范围和后续观察重点"}
        for index in range(1, 5)
    ]
    payload = {
        "date": "2026-07-01",
        "generated_at": "2026-07-01T08:00:00",
        "model": "gpt-5-nano",
        "brief": {
            "lede": "整点新闻摘要",
            "mainland": mainland,
            "world": world,
            "a_share": {},
            "us_stock": {},
            "sources": ["测试来源"],
        },
        "items": [],
        "market_snapshot": {},
    }

    image = plugin._render((800, 480), {"brief_title": "整点新闻"}, payload, datetime(2026, 7, 1), {"mode": "night"})

    rendered = "".join(drawn_text)
    assert image.size == (800, 480)
    assert len(market_calls) == 2
    assert "..." not in rendered
    for item in mainland + world:
        assert item["why"] in rendered

def test_render_includes_seventh_top_item_in_quick_sidebar(monkeypatch):
    plugin = _plugin()
    news_calls = []

    def capture_news_items(draw, items, x, y, *args, **kwargs):
        news_calls.append([item["title"] for item in items])
        return y

    monkeypatch.setattr(plugin, "_draw_news_items", capture_news_items)
    payload = {
        "date": "2026-06-27",
        "generated_at": "2026-06-27T08:00:00",
        "model": "gpt-5-nano",
        "brief": {
            "lede": "整点新闻摘要",
            "top": [
                {"title": f"新闻标题{index}", "why": f"新闻正文{index}"}
                for index in range(1, 8)
            ],
            "a_share": {},
            "us_stock": {},
        },
        "items": [],
        "market_snapshot": {},
    }

    image = plugin._render((800, 480), {"brief_title": "整点新闻"}, payload, datetime(2026, 6, 27), {"mode": "day"})

    assert image.size == (800, 480)
    assert news_calls[0] == ["新闻标题1", "新闻标题2", "新闻标题3"]
    assert news_calls[1] == ["新闻标题4", "新闻标题5", "新闻标题6", "新闻标题7"]


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


def test_render_prefers_mainland_and_world_sections_over_legacy_top(monkeypatch):
    plugin = _plugin()
    news_calls = []

    def capture_news_items(draw, items, x, y, *args, **kwargs):
        news_calls.append([item["title"] for item in items])
        return y

    monkeypatch.setattr(plugin, "_draw_news_items", capture_news_items)
    payload = {
        "date": "2026-06-28",
        "generated_at": "2026-06-28T08:00:00",
        "model": "gpt-5-nano",
        "brief": {
            "lede": "整点新闻摘要",
            "mainland": [
                {"title": "大陆新闻一", "why": "国内更新"},
                {"title": "大陆新闻二", "why": "政策进展"},
            ],
            "world": [
                {"title": "世界新闻一", "why": "国际进展"},
                {"title": "世界新闻二", "why": "外交动态"},
                {"title": "世界新闻三", "why": "地区变化"},
            ],
            "top": [{"title": "旧头条", "why": "不应优先"}],
            "a_share": {},
            "us_stock": {},
        },
        "items": [],
        "market_snapshot": {},
    }

    image = plugin._render((800, 480), {"brief_title": "整点新闻"}, payload, datetime(2026, 6, 28), {"mode": "day"})

    assert image.size == (800, 480)
    assert news_calls[0] == ["大陆新闻一", "大陆新闻二"]
    assert news_calls[1] == ["世界新闻一", "世界新闻二", "世界新闻三"]

def test_daily_ai_news_market_headers_use_plain_text_labels(monkeypatch):
    plugin = _plugin()
    seen_labels = []

    def capture_market_module(draw, label, brief, payload, key, x, y, width, section_font, body_font, accent, ink, rule, up_color, down_color, max_y=None, target_image=None):
        seen_labels.append(label)
        return y

    monkeypatch.setattr(plugin, "_draw_market_module", capture_market_module)
    payload = {
        "date": "2026-06-18",
        "generated_at": "2026-06-18T05:53:00",
        "model": "gpt-5-nano",
        "brief": {
            "lede": "多方新闻聚焦地区安全与能源流动。",
            "top": [],
            "a_share": {"summary": "A股上涨", "analysis": "主要指数同步走强。"},
            "us_stock": {"summary": "美股走弱", "analysis": "主要指数同步走弱。"},
        },
        "items": [],
        "market_snapshot": {},
    }

    image = plugin._render((800, 480), {"brief_title": "整点新闻"}, payload, datetime(2026, 6, 18), {"mode": "day"})

    assert image.size == (800, 480)
    assert seen_labels == ["A股今日", "美股今日"]


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

def test_section_wordmark_assets_are_transparent_measured_strips():
    assert set(SECTION_WORDMARK_IMAGES) == {"top", "quick", "a_share", "us_stock"}

    for key, filename in SECTION_WORDMARK_IMAGES.items():
        path = daily_ai_news_module.PLUGIN_DIR / filename
        with Image.open(path) as image:
            assert image.mode == "RGBA"
            assert image.size == SECTION_WORDMARK_SIZES[key]
            alpha = image.getchannel("A")
            assert alpha.getextrema()[0] == 0
            assert alpha.getbbox() is not None
            assert alpha.getpixel((0, 0)) == 0
            assert alpha.getpixel((image.width - 1, 0)) == 0


def test_section_wordmarks_are_recolored_for_night_readability():
    plugin = _plugin()
    accents = {
        "top": (255, 82, 74),
        "quick": (107, 204, 255),
        "a_share": (255, 82, 74),
        "us_stock": (146, 221, 166),
    }

    for key, accent in accents.items():
        source = plugin._load_section_wordmark(key)
        assert source is not None
        prepared = plugin._prepare_section_wordmark(source, accent)
        alpha = prepared.getchannel("A")
        dark_panel = Image.new("RGBA", prepared.size, (0, 0, 0, 255))
        dark_panel.alpha_composite(prepared)
        bbox = alpha.getbbox()
        assert bbox is not None
        luminance_values = []
        solid_luminance_values = []
        for py in range(prepared.height):
            for px in range(prepared.width):
                alpha_value = alpha.getpixel((px, py))
                if alpha_value < 128:
                    continue
                red, green, blue = dark_panel.getpixel((px, py))[:3]
                luminance = (red * 299 + green * 587 + blue * 114) / 1000
                luminance_values.append(luminance)
                if alpha_value >= 220:
                    solid_luminance_values.append(luminance)
        assert luminance_values
        assert solid_luminance_values
        assert min(solid_luminance_values) >= 150
        assert sum(luminance_values) / len(luminance_values) >= 150

def test_section_wordmark_headers_draw_thin_divider(monkeypatch):
    plugin = _plugin()
    image = Image.new("RGB", (220, 70), "white")
    draw = ImageDraw.Draw(image)
    rule = (185, 188, 194)

    def fake_wordmark(target_image, key, x, y, accent):
        return (x, y, x + 80, y + 24)

    monkeypatch.setattr(plugin, "_draw_section_wordmark", fake_wordmark)
    plugin._section_header(
        draw,
        "大陆新闻",
        10,
        12,
        180,
        plugin._font("Microsoft YaHei", 18, "bold"),
        (166, 38, 48),
        rule,
        image=image,
        asset_key="top",
    )

    assert image.getpixel((10, 39)) == rule
    assert image.getpixel((190, 39)) == rule

def test_render_uses_section_wordmarks_for_fixed_headers(monkeypatch):
    plugin = _plugin()
    seen_keys = []
    text_calls = []

    def fake_draw_section_wordmark(image, key, x, y, accent):
        seen_keys.append(key)
        return (int(x), int(y), int(x) + SECTION_WORDMARK_SIZES[key][0], int(y) + SECTION_WORDMARK_SIZES[key][1])

    original_text = ImageDraw.ImageDraw.text

    def capture_text(self, xy, text, *args, **kwargs):
        text_calls.append(str(text))
        return original_text(self, xy, text, *args, **kwargs)

    monkeypatch.setattr(plugin, "_draw_section_wordmark", fake_draw_section_wordmark)
    monkeypatch.setattr(ImageDraw.ImageDraw, "text", capture_text)

    payload = {
        "date": "2026-06-26",
        "generated_at": "2026-06-26T05:51:00",
        "model": "gpt-5-nano",
        "brief": {"lede": "新闻摘要", "top": [], "a_share": {}, "us_stock": {}},
        "items": [],
        "market_snapshot": {},
    }

    image = plugin._render((800, 480), {"brief_title": "整点新闻"}, payload, datetime(2026, 6, 26), {"mode": "day"})

    assert image.size == (800, 480)
    assert seen_keys == ["top", "quick", "a_share", "us_stock"]
    assert "大陆新闻" not in text_calls
    assert "世界新闻" not in text_calls
    assert "世界快报" not in text_calls
    assert "今日头条" not in text_calls
    assert "快讯补充" not in text_calls
    assert "A股今日" not in text_calls
    assert "美股今日" not in text_calls


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
    assert seen["wordmark"] == (24, 8, TITLE_WORDMARK_SIZE)
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

def test_render_positions_title_background_between_title_and_meta(monkeypatch):
    plugin = _plugin()
    seen = {}

    def fake_draw_title_background(image, box):
        seen["box"] = tuple(int(value) for value in box)
        return True

    monkeypatch.setattr(plugin, "_draw_title_background", fake_draw_title_background)
    payload = {
        "date": "2026-06-08",
        "generated_at": "2026-06-08T05:51:00",
        "model": "gpt-5-nano",
        "from_cache": True,
        "brief": {
            "lede": "朝韩互访成为最新热点，平壤迎来中国领导人访问并再度举行高规格接待",
            "top": [],
            "a_share": {"summary": "上证-2.84%", "analysis": "主要指数同步走弱。"},
            "us_stock": {"summary": "标普-2.58%", "analysis": "主要指数同步走弱。"},
        },
        "items": [],
        "market_snapshot": {},
    }

    now = datetime(2026, 6, 8)
    theme_context = {"mode": "day"}
    image = plugin._render((800, 480), {"brief_title": ""}, payload, now, theme_context)

    assert image.size == (800, 480)
    left, top, right, bottom = seen["box"]
    measure = ImageDraw.Draw(Image.new("RGB", (800, 480), "white"))
    meta_font = plugin._font("Microsoft YaHei", 14)
    meta = f"{plugin._date_label(payload, now)}  |  \u667a\u80fd\u751f\u6210  |  \u7f13\u5b58"
    theme_label = plugin._theme_label(theme_context)
    expected_meta_left = 800 - 24 - max(
        plugin._tw(measure, meta, meta_font),
        plugin._tw(measure, theme_label, meta_font),
    )
    assert right - left >= 290
    assert bottom - top == TITLE_BACKGROUND_SIZE[1]
    assert 250 <= left <= 260
    assert top == 8
    assert right == expected_meta_left - 12
    assert bottom == 73
