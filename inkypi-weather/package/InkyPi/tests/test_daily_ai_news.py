import sys
from datetime import datetime
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import plugins.daily_ai_news.daily_ai_news as daily_ai_news_module
from plugins.daily_ai_news.daily_ai_news import DailyAINews, TITLE_BACKGROUND_IMAGE, TITLE_BACKGROUND_SIZE


def _plugin():
    return DailyAINews({"id": "daily_ai_news"})


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
    assert (right - left, bottom - top) == TITLE_BACKGROUND_SIZE
    assert 210 <= left <= 214
    assert top == 8
    assert 535 <= right <= 539
    assert bottom == 73
