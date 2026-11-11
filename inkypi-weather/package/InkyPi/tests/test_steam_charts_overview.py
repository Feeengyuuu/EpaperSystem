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
        "metacritic": {"score": 94, "url": "https://www.metacritic.com/game/pc/x"},
        "achievements": {"total": 42, "highlighted": []},
    })

    assert facts == {"genres": ["动作", "冒险"], "developer": "Valve", "price": "$15.99", "discount": 20,
                     "release": "2026 年 10 月 16 日", "recommendations": 12_345, "metacritic": 94,
                     "achievements": 42}
    assert SteamCharts._store_facts({"is_free": True})["price"] == "免费"
    assert SteamCharts._store_facts(None)["genres"] == []
    # A missing or odd score or count is none at all, never a zero on the page.
    odd = SteamCharts._store_facts({"metacritic": {"score": "94"}, "achievements": {"total": True}})
    assert (odd["metacritic"], odd["achievements"]) == (0, 0)


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
    # Steam's own discount block, then the price (its dollar sign drawn apart in green); the
    # record and the month's average.
    assert {"-20%", "$", "15.99", "月均 80.8万"} <= set(drawn)
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
    monkeypatch.setattr(plugin, "_steam_weekly_ranks", lambda: {})

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


def test_the_header_names_steamcharts_by_its_site_icon():
    plugin = _plugin()
    totals = {"online": 35_630_000, "online_peak": 40_120_000, "spark": [1, 3, 2]}

    image = plugin._render_live_overview((800, 480), [], totals, {"mode": "day"}, "刷新时间 10/23 10:51")

    # The icon's green bars on its dark tile stand just left of "steamcharts.com" in the top right corner.
    corner = [image.getpixel((x, y)) for x in range(690, 760) for y in range(8, 27)]
    assert any(green > 150 and red < 140 and blue < 110 for red, green, blue in corner)
    assert overview_page.SOURCE_ICON_PATH.is_file()


class _Steam:
    """Steam's store, its clan pages and its avatar CDN: what a studio logo lookup asks for."""

    def __init__(self, clans, avatars, refuse=()):
        self.clans, self.avatars, self.refuse, self.asked = clans, avatars, set(refuse), []

    def request_json(self, method, url, params=None, **_kwargs):
        query = json.loads(params["input_json"])
        self.asked.append("items")
        items = [{"appid": entry["appid"], "basic_info": {"developers": [
            {"name": "Studio", "creator_clan_account_id": self.clans.get(entry["appid"], 0)}]}}
            for entry in query["ids"]]
        return type("R", (), {"data": {"response": {"store_items": items}}})()

    def request_bytes(self, method, url, **_kwargs):
        from io import BytesIO

        from PIL import Image

        if "memberslistxml" in url:
            clan = str(int(url.split("/gid/")[1].split("/")[0]) - 103582791429521408)
            self.asked.append(f"clan {clan}")
            if clan in self.refuse:
                raise RuntimeError("429")
            avatar = self.avatars.get(clan, "")
            page = f"<avatarFull><![CDATA[{avatar}]]></avatarFull>" if avatar else "<memberList/>"
            return type("R", (), {"data": page.encode()})()
        self.asked.append("avatar")
        buffer = BytesIO()
        Image.new("RGB", (184, 184), (200, 30, 30)).save(buffer, format="JPEG")
        return type("R", (), {"data": buffer.getvalue()})()


def test_rows_get_their_studios_logos_a_few_new_ones_a_refresh_and_kept(tmp_path, monkeypatch):
    monkeypatch.setenv("INKYPI_CACHE_DIR", str(tmp_path))
    avatar = "https://avatars.fastly.steamstatic.com/" + "a" * 40 + "_full.jpg"
    steam = _Steam({730: 4, 570: 4, 578080: 7, 1: 9, 2: 0}, {"4": avatar, "7": avatar, "9": ""})
    monkeypatch.setattr("plugins.steam_charts.steam_charts.get_http_client", lambda: steam)
    plugin = _plugin()

    def games():
        return [{"app_id": app_id, "store": {"developer": "Studio"}} for app_id in (730, 570, 578080, 1, 2)]

    first = games()
    plugin._attach_studio_logos(first)
    # One batch for the clans; Valve's logo once for both its games, then PUBG's (two new a refresh).
    assert steam.asked == ["items", "clan 4", "avatar", "clan 7", "avatar"]
    assert [bool(game["store"].get("studio_logo")) for game in first] == [True, True, True, False, False]

    second = games()
    plugin._attach_studio_logos(second)
    # The next refresh asks only for the studio still unknown (one without an avatar), then keeps all.
    assert steam.asked[5:] == ["clan 9"]
    assert second[0]["store"]["studio_logo"].startswith("data:image/png;base64,")
    plugin._attach_studio_logos(games())
    assert len(steam.asked) == 6


def test_a_refused_studio_page_stops_the_lookups_until_the_next_refresh(tmp_path, monkeypatch):
    monkeypatch.setenv("INKYPI_CACHE_DIR", str(tmp_path))
    steam = _Steam({730: 4, 578080: 7}, {"7": "https://avatars.steamstatic.com/" + "b" * 40 + "_full.jpg"}, refuse={"4"})
    monkeypatch.setattr("plugins.steam_charts.steam_charts.get_http_client", lambda: steam)
    games = [{"app_id": app_id, "store": {"developer": "Studio"}} for app_id in (730, 578080)]

    _plugin()._attach_studio_logos(games)

    assert steam.asked == ["items", "clan 4"]
    assert not any(game["store"].get("studio_logo") for game in games)


def test_a_rows_studio_logo_leads_its_second_line_with_the_studio_first(monkeypatch):
    import base64
    from io import BytesIO

    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (48, 48), (200, 30, 30)).save(buffer, format="PNG")
    logo = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
    plugin = _plugin()
    drawn = []
    original = ImageDraw.ImageDraw.text

    def capture(self, xy, text, *args, **kwargs):
        drawn.append((str(text), xy))
        return original(self, xy, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", capture)
    game = {"rank": 1, "name": "影之刃零", "secondary_name": "Phantom Blade Zero", "current_players": 321_591,
            "peak_24h": 775_647, "spark": [1, 2, 3],
            "store": {"genres": ["动作"], "developer": "S-GAME", "studio_logo": logo}}
    image = plugin._render_live_overview((800, 480), [{"table_variant": "trending", "games": []},
                                                      {"table_variant": "top_games", "games": [game]}], {}, {"mode": "day"}, "")

    text, (x, y) = next(item for item in drawn if item[0].startswith("S-GAME"))
    # The studio first, then the English name (the line is cut to the row).
    assert text.startswith("S-GAME · Phantom")
    # The logo stands just before it, on the same line.
    assert image.getpixel((round(x) - overview_page.STUDIO_MARK // 2 - 4, round(y) + 6)) == (200, 30, 30)


def test_a_studio_mark_that_melts_into_the_page_takes_its_ink_and_one_that_reads_keeps_its_colours():
    from PIL import Image

    paper, ink = (255, 255, 255), (20, 20, 20)
    white = Image.new("RGBA", (10, 5), (255, 255, 255, 255))
    assert overview_page._on_page(white, paper, ink).getpixel((1, 1))[:3] == ink
    red = Image.new("RGBA", (10, 5), (244, 80, 77, 255))
    assert overview_page._on_page(red, paper, ink).getpixel((1, 1))[:3] == (244, 80, 77)
    # By night a dark mark turns to the page's light ink; its bright parts keep their colour.
    mark = Image.new("RGBA", (10, 5), (30, 40, 60, 255))
    mark.putpixel((9, 4), (230, 120, 20, 255))
    night = overview_page._on_page(mark, (0, 0, 0), (240, 240, 240))
    assert night.getpixel((1, 1))[:3] == (240, 240, 240) and night.getpixel((9, 4))[:3] == (230, 120, 20)


def test_steams_weekly_chart_moves_the_most_played_board_and_fills_a_missing_peak(monkeypatch):
    payload = {"response": {"rollup_date": 1793491200, "ranks": [
        {"rank": 1, "appid": 730, "last_week_rank": 1, "peak_in_game": 1_288_020},
        {"rank": 5, "appid": 4115450, "last_week_rank": -1, "peak_in_game": 833_921},
        {"rank": 9, "appid": 1091500, "last_week_rank": 7, "peak_in_game": 168_742},
        {"rank": "x", "appid": 1, "last_week_rank": 1}]}}
    requests = []

    class Client:
        def request_json(self, method, url, **kwargs):
            requests.append((url, kwargs))
            return type("R", (), {"data": payload})()

    monkeypatch.setattr("plugins.steam_charts.steam_charts.get_http_client", lambda: Client())
    SteamCharts._steam_weekly_ranks_cached.cache_clear()
    plugin = _plugin()
    groups = [{"key": "trending", "table_variant": "trending", "games": [{"app_id": 1091500, "rank_move": 2}]},
              {"key": "player_count", "table_variant": "top_games", "games": [
                  {"app_id": 730, "rank_move": 3, "peak_24h": 1_300_000},
                  {"app_id": 4115450, "peak_24h": None},
                  {"app_id": 1091500},
                  {"app_id": 999, "rank_move": -1}]}]

    plugin._mark_weekly_moves(groups)
    plugin._mark_weekly_moves(groups)

    cs2, new, cyberpunk, unlisted = groups[1]["games"]
    assert (cs2["rank_move"], cs2["steam_rank"], cs2["peak_24h"]) == (0, 1, 1_300_000)
    assert (new["rank_move"], new["peak_24h"]) == ("new", 833_921)
    assert cyberpunk["rank_move"] == -2 and cyberpunk["rank_move_weekly"] is True
    # A game off Steam's top 100 keeps the board's own move; the trending board is untouched.
    assert unlisted == {"app_id": 999, "rank_move": -1}
    assert groups[0]["games"] == [{"app_id": 1091500, "rank_move": 2}]
    # One small request an hour, with a byte limit.
    assert len(requests) == 1 and requests[0][1]["max_bytes"] == 128 * 1024
    SteamCharts._steam_weekly_ranks_cached.cache_clear()


def test_a_failed_weekly_chart_leaves_the_boards_own_moves(monkeypatch):
    class Client:
        def request_json(self, *args, **kwargs):
            raise RuntimeError("offline")

    monkeypatch.setattr("plugins.steam_charts.steam_charts.get_http_client", lambda: Client())
    SteamCharts._steam_weekly_ranks_cached.cache_clear()
    groups = [{"key": "player_count", "table_variant": "top_games", "games": [{"app_id": 730, "rank_move": 2}]}]
    _plugin()._mark_weekly_moves(groups)
    assert groups[0]["games"] == [{"app_id": 730, "rank_move": 2}]
    SteamCharts._steam_weekly_ranks_cached.cache_clear()


def test_rows_wear_metacritic_on_the_art_a_weekly_move_and_the_achievement_count(monkeypatch):
    plugin = _plugin()
    drawn = []
    original = ImageDraw.ImageDraw.text

    def capture(self, xy, text, *args, **kwargs):
        drawn.append(str(text))
        return original(self, xy, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", capture)
    trending = {"table_variant": "trending", "games": [
        {"rank": 1, "name": "Leaf it Alone", "current_players": 5002, "change_24h_fmt": "+164%", "spark": [1, 2, 5],
         "store": {"developer": "Eternity", "price": "$2.99", "discount": 50, "achievements": 54, "metacritic": 81}}]}
    top = {"table_variant": "top_games", "games": [
        {"rank": 1, "name": "Dota 2", "current_players": 423_683, "peak_24h": 697_106, "spark": [3, 2, 4],
         "rank_move": -12, "rank_move_weekly": True, "store": {"developer": "Valve", "metacritic": 90}}]}

    image = plugin._render_live_overview((800, 480), [trending, top], {}, {"mode": "day"}, "")

    assert {"MC 81", "MC 90", "周", "12", "当前 / 24h 峰值 ‧ 周榜升降"} <= set(drawn)
    # Behind the large price a tight row keeps the achievement count's figure by its gold star.
    assert "54 成就" in drawn or "54" in drawn
    # The score sits on the art's top right corner in Metacritic's green.
    colours = {image.getpixel((x, y)) for x in range(90, 128) for y in range(97, 108)}
    assert overview_page.ACCENTS["day"]["up"] in colours
