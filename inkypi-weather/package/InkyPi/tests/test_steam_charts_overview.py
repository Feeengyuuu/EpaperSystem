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


def test_the_wordmark_skyline_grows_into_the_header_space():
    plugin = _plugin()
    source = plugin._theme_title_wordmark({"ink": (0, 0, 0), "paper": (255, 255, 255)})
    lettering, skyline = overview_page._split_wordmark(source)
    # Every shown pixel lands in one half or the other (both keep the underline where they join).
    for x in range(0, source.width, 3):
        for y in range(source.height):
            if source.getpixel((x, y))[3] > 24:
                assert lettering.getpixel((x, y))[3] or skyline.getpixel((x, y))[3]

    image = plugin._render_live_overview((800, 480), [{"games": []}, {"games": []}], {"online": 35_000_000},
                                         {"mode": "day"}, "")

    def inked(x_range, y_range):
        return [(x, y) for x in x_range for y in y_range if sum(image.getpixel((x, y))) < 600]

    # "STEAM CHARTS" stands about twice its old height on the header rule (its underline and the
    # rule are one line), and the skyline reaches on to WORDMARK_RIGHT.
    lettering = inked(range(70, 230), range(2, overview_page.HEADER_H - 3))
    assert min(y for _x, y in lettering) < 30
    assert not inked(range(70, 230), range(overview_page.HEADER_H + 3, overview_page.HEADER_H + 6))
    assert max(x for x, _y in inked(range(236, overview_page.WORDMARK_RIGHT + 1), range(2, 52))) > 350
    # The users-online badge sits right of the 48-hour line, by the source and refresh time.
    badge = [x for x, _y in inked(range(380, 700), range(10, 28))
             if image.getpixel((x, _y))[2] > 150 and image.getpixel((x, _y))[0] < 80]
    assert badge and min(badge) > 500


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
    assert {"3563万", "5,979 在线", "+320%", "1,146,659", "峰值 1,274,983", "Valve · 动作 / 免费开玩"} <= set(drawn)
    # Steam's own discount block, then the price; the record and the month's average.
    assert {"-20%", "$15.99", "月均 80.8万"} <= set(drawn)
    assert any(text.startswith("181.8万") for text in drawn)


def test_a_top_games_daily_line_ends_on_the_past_day_not_the_new_utc_day():
    day = 24 * HOUR
    # The newest point is half an hour into a UTC day: by calendar days that day's peak is tiny.
    latest = 1_700_006_400_000 + 30 * 60 * 1000
    points = [[latest - step * HOUR, 5000 if (latest - step * HOUR) % day < 30 * 60 * 1000 else 9000]
              for step in range(30 * 24)][::-1]

    values = SteamCharts._spark_values(points, daily=True)

    assert len(values) == 30 and values[-1] == 9000 and min(values) == 9000


def test_rows_show_their_move_since_the_board_last_changed(tmp_path, monkeypatch):
    monkeypatch.setenv("INKYPI_CACHE_DIR", str(tmp_path))
    plugin = _plugin()

    def board(*ids):
        return [{"key": "trending", "games": [{"app_id": app_id, "rank": rank} for rank, app_id in enumerate(ids, 1)]}]

    first = board(10, 20, 30)
    plugin._mark_rank_moves(first)
    assert all("rank_move" not in game for game in first[0]["games"])

    second = board(30, 10, 40)
    plugin._mark_rank_moves(second)
    assert [game.get("rank_move") for game in second[0]["games"]] == [2, -1, "new"]

    # The same order again keeps showing how it last changed.
    again = board(30, 10, 40)
    plugin._mark_rank_moves(again)
    assert [game.get("rank_move") for game in again[0]["games"]] == [2, -1, "new"]


def test_new_releases_wear_a_first_week_or_new_tag():
    from datetime import date

    def tag(release):
        return overview_page.LiveOverviewMixin._overview_release_tag({"store": {"release": release}},
                                                                     today=date(2026, 10, 24))

    assert tag("2026 年 10 月 20 日") == "首周"
    assert tag("Oct 5, 2026") == "新品"
    assert tag("2026 年 8 月 1 日") == "" and tag("2026 年 11 月 1 日") == "" and tag("即将推出") == ""


def test_the_top_board_ranks_by_the_count_it_shows(monkeypatch):
    plugin = _plugin()
    games = {"steamcharts_trending": [], "steamcharts_top_games": [
        {"rank": 1, "app_id": 730, "name": "CS2", "current_players": 576_038},
        {"rank": 2, "app_id": 570, "name": "Dota 2", "current_players": 629_214}]}
    monkeypatch.setattr(plugin, "_fetch_games", lambda source, count: [dict(game) for game in games[source]])
    monkeypatch.setattr(plugin, "_apply_store_metadata", lambda games, include_images=True: None)
    monkeypatch.setattr(plugin, "_mark_rank_moves", lambda groups: None)

    top = plugin._fetch_combined_chart_groups(5, show_images=False)[1]["games"]

    assert [(game["name"], game["rank"]) for game in top] == [("Dota 2", 1), ("CS2", 2)]


def test_a_long_game_name_runs_on_to_the_player_count(monkeypatch):
    plugin = _plugin()
    drawn = []
    original = ImageDraw.ImageDraw.text

    def capture(self, xy, text, *args, **kwargs):
        drawn.append((str(text), xy))
        return original(self, xy, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", capture)
    game = {"rank": 1, "name": "Waifusitter - Furry Collection", "current_players": 3201,
            "change_24h_fmt": "+1,515%", "spark": [1, 2, 3]}
    plugin._render_live_overview((800, 480), [{"table_variant": "trending", "games": [game]},
                                              {"table_variant": "top_games", "games": []}], {}, {"mode": "day"}, "")

    assert "Waifusitter - Furry Collection" in [text for text, _xy in drawn]
