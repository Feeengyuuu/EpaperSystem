"""The native live overview: Steam-wide totals, store facts, peaks and the two boards."""

import json
import sys
from pathlib import Path

from PIL import ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.steam_charts import overview_page  # noqa: E402
from plugins.steam_charts.steam_charts import SteamCharts  # noqa: E402

HOUR = 3_600_000


def _plugin():
    return SteamCharts({"id": "steam_charts"})


def test_chart_stats_add_day_and_all_time_peaks_month_average_and_a_trend(monkeypatch):
    plugin = _plugin()
    start = 1_700_000_000_000
    data = [[start + hour * HOUR, 1000 + (hour % 24) * 10] for hour in range(24 * 40)]
    data[100][1] = 99_999  # an old all-time peak
    monkeypatch.setattr("plugins.steam_charts.steam_charts.get_http_client",
                        lambda: type("C", (), {"request_json": lambda self, *a, **k: type("R", (), {"data": data})()})())
    monkeypatch.setattr("plugins.steam_charts.steam_charts.steamcharts_rate_limiter.wait", lambda: None)

    line = plugin._fetch_chart_stats(730, sparkline_hours=48)
    daily = plugin._fetch_chart_stats(730, sparkline_hours=30 * 24, sparkline_style="bars")

    assert line["peak_24h"] == 1230 and line["peak_all"] == 99_999
    assert line["peak_all_ts"] == data[100][0]
    assert 1000 < line["avg_30d"] < 1230
    assert 2 <= len(line["spark"]) <= 40
    # The 30-day window becomes one value per day: that day's peak.
    assert len(daily["spark"]) in (30, 31) and set(daily["spark"]) == {1230}


def test_store_facts_pick_genres_developer_price_release_and_reviews():
    facts = SteamCharts._store_facts({
        "genres": [{"description": "动作"}, {"description": "冒险"}, {"description": "独立"}],
        "developers": ["Valve", "Hidden Path"],
        "price_overview": {"final_formatted": "$15.99", "discount_percent": 20},
        "release_date": {"date": "2026 年 10 月 16 日"},
        "recommendations": {"total": 12_345},
    })

    assert facts == {"genres": ["动作", "冒险"], "developer": "Valve", "price": "$15.99", "discount": 20,
                     "release": "2026 年 10 月 16 日", "recommendations": 12_345}
    assert SteamCharts._store_facts({"is_free": True})["price"] == "免费"
    assert SteamCharts._store_facts(None)["genres"] == []


def test_steam_totals_read_the_logged_in_series(monkeypatch):
    payload = [{"label": "Steam users logged in", "data": [[1, 30_000_000], [2, 41_000_000], [3, 35_700_000]]}]
    monkeypatch.setattr("plugins.steam_charts.steam_charts.get_http_client",
                        lambda: type("C", (), {"request_json": lambda self, *a, **k: type("R", (), {"data": payload})()})())

    totals = _plugin()._steam_totals()

    assert (totals["online"], totals["online_peak"]) == (35_700_000, 41_000_000)
    assert totals["spark"] == [30_000_000, 41_000_000, 35_700_000] and "ingame" not in totals


def test_counts_read_in_wan_and_yi():
    assert overview_page.wan(35_724_548) == "3572万"
    assert overview_page.wan(1_818_000) == "181.8万"
    assert overview_page.wan(250_000_000) == "2.50亿"
    assert overview_page.wan(876) == "876" and overview_page.wan(None) == "--"


def test_trending_chip_shows_steamcharts_own_gain():
    assert overview_page.LiveOverviewMixin._overview_change({"change_24h_fmt": "+1,424.3%", "change_24h": -11.8}) == 1424.3
    assert overview_page.LiveOverviewMixin._overview_change({"change_24h_fmt": "--", "change_24h": 5.0}) == 5.0


def test_every_overview_icon_is_a_generated_badge_with_its_provenance():
    names = ("users", "ingame", "trending", "live", "peak", "release", "price", "reviews")
    record = json.loads((overview_page.ICON_DIR / "icons.source.json").read_text(encoding="utf-8"))

    assert set(record["icons"]) == set(names)
    for name in names:
        icon = overview_page._icon(name, 18)
        assert icon is not None and icon.size == (18, 18) and icon.mode == "RGBA"
        # A round badge: clear corners, an opaque middle.
        assert icon.getpixel((0, 0))[3] == 0 and icon.getpixel((9, 9))[3] == 255
    assert overview_page._icon("no-such-icon", 18) is None


def test_overview_draws_both_boards_with_totals_and_store_facts(monkeypatch):
    plugin = _plugin()
    drawn = []
    original = ImageDraw.ImageDraw.text

    def capture(self, xy, text, *args, **kwargs):
        drawn.append(str(text))
        return original(self, xy, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", capture)
    store = {"genres": ["动作", "冒险"], "developer": "Evil Raptor", "price": "$15.99", "discount": 20,
             "release": "2026 年 10 月 16 日", "recommendations": 1200}
    trending = {"table_variant": "trending", "games": [
        {"rank": 1, "name": "遥遥西土", "secondary_name": "Far Far West", "current_players": 5979,
         "change_24h_fmt": "+320.0%", "spark": [1, 2, 5], "store": store}]}
    top = {"table_variant": "top_games", "games": [
        {"rank": 1, "name": "Counter-Strike 2", "current_players": 1_146_659, "peak_24h": 1_274_983,
         "peak_all": 1_818_000, "peak_all_ts": 1_742_000_000_000, "avg_30d": 808_000, "spark": [3, 2, 4],
         "store": {"genres": ["动作", "免费开玩"], "developer": "Valve"}}]}
    totals = {"online": 35_630_000, "online_peak": 40_120_000, "spark": [1, 3, 2]}

    image = plugin._render_live_overview((800, 480), [trending, top], totals, {"mode": "night"}, "刷新时间 10/23 10:51")

    assert image.size == (800, 480) and image.getpixel((5, 5)) == (0, 0, 0)
    assert {"3563万", "5,979", "+320%", "1,146,659", "峰值 1,274,983", "Valve"} <= set(drawn)
    assert "$15.99 -20%" in drawn and "动作 / 免费开玩 · 月均 80.8万" in drawn
    assert any(text.startswith("181.8万") for text in drawn)
