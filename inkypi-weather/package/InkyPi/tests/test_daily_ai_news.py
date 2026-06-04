import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.daily_ai_news.daily_ai_news import DailyAINews


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
