import sys
from datetime import datetime
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import plugins.daily_ai_news.daily_ai_news as daily_ai_news_module
from plugins.daily_ai_news.daily_ai_news import DailyAINews, TITLE_BACKGROUND_IMAGE, TITLE_BACKGROUND_SIZE


def _plugin():
    return DailyAINews({"id": "daily_ai_news"})


def test_effective_feeds_expands_legacy_bbc_only_settings():
    plugin = _plugin()
    feeds_text = "BBC World|https://feeds.bbci.co.uk/news/world/rss.xml"

    effective = plugin._effective_feeds_text(feeds_text)
    feeds = plugin._parse_feeds(effective)
    urls = [url for _name, url in feeds]

    assert urls.count("https://feeds.bbci.co.uk/news/world/rss.xml") == 1
    assert "https://www.aljazeera.com/xml/rss/all.xml" in urls
    assert "https://www.france24.com/en/rss" in urls
    assert "https://rss.dw.com/rdf/rss-en-all" in urls
    assert len(feeds) >= 10


def test_effective_feeds_expands_previous_default_feed_set():
    plugin = _plugin()

    effective = plugin._effective_feeds_text(daily_ai_news_module.LEGACY_DEFAULT_FEEDS)
    urls = {url for _name, url in plugin._parse_feeds(effective)}

    assert "https://www.pbs.org/newshour/feeds/rss/headlines" in urls
    assert "https://abcnews.go.com/abcnews/internationalheadlines" in urls


def test_effective_feeds_preserves_custom_non_legacy_settings():
    plugin = _plugin()
    custom = "Custom Source|https://example.com/custom.xml"

    assert plugin._effective_feeds_text(custom) == custom


def test_fetch_items_samples_all_configured_sources(monkeypatch):
    plugin = _plugin()
    urls = [f"https://example.com/feed{i}.xml" for i in range(5)]
    feeds_text = "\n".join(f"Source {i}|{url}" for i, url in enumerate(urls))
    calls = []

    class FakeResponse:
        def __init__(self, url):
            self.content = url.encode("utf-8")

        def raise_for_status(self):
            return None

    def fake_get(url, **_kwargs):
        calls.append(url)
        return FakeResponse(url)

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

    monkeypatch.setattr(daily_ai_news_module.requests, "get", fake_get)
    monkeypatch.setattr(daily_ai_news_module.feedparser, "parse", fake_parse)

    items = plugin._fetch_items(feeds_text, 6)

    assert calls == urls
    assert {item["source"] for item in items} == {f"Source {index}" for index in range(5)}


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

    assert plugin._theme_label({"mode": "night"}) == "午夜简报"
    assert plugin._theme_label({"mode": "day"}) == "日间简报"
    assert plugin._footer_text({"generated_at": "2026-06-17T21:12:00"}, {"sources": []}).startswith("来源: 新闻源 + AI摘要")


def test_daily_ai_news_loads_microsoft_yahei_font():
    plugin = _plugin()

    font = plugin._font("Microsoft YaHei", 18, "bold")

    assert hasattr(font, "getbbox")
    assert "msyh" in str(getattr(font, "path", "")).lower()


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


def test_daily_ai_news_market_headers_use_plain_text_labels(monkeypatch):
    plugin = _plugin()
    seen_labels = []

    def capture_market_module(draw, label, brief, payload, key, x, y, width, section_font, body_font, accent, ink, rule, up_color, down_color, max_y=None):
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

    image = plugin._render((800, 480), {"brief_title": ""}, payload, datetime(2026, 6, 8), {"mode": "day"})

    assert image.size == (800, 480)
    left, top, right, bottom = seen["box"]
    assert right - left >= TITLE_BACKGROUND_SIZE[0]
    assert bottom - top == TITLE_BACKGROUND_SIZE[1]
    assert 210 <= left <= 214
    assert top == 8
    assert 565 <= right <= 575
    assert bottom == 73
