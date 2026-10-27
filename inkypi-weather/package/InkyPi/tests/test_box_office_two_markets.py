"""The official China chart shares one page with the North America weekly chart."""
from datetime import datetime, timezone

from PIL import Image
import pytest

from plugins.base_plugin.render_provenance import SourceProvenance, read_source_provenance
from plugins.box_office_top_movies import two_markets
from plugins.box_office_top_movies.box_office_top_movies import BoxOfficeMovie, BoxOfficeTopMovies, MarketSnapshot
from runtime.refresh_contracts import TaskCancelled
from tests.test_box_office_top_movies import DummyDeviceConfig, canonical_theme


NOW = datetime(2026, 10, 24, 5, 0, tzinfo=timezone.utc)
SETTINGS = {"sourceMode": "official_china", "itemsCount": "5", "cacheHours": "3", "tmdbLanguage": "zh-CN",
            "tmdbRegion": "CN", "maoyanUrl": "https://piaofang.maoyan.com/dashboard-ajax",
            "chartUrl": "https://www.the-numbers.com/weekend-box-office-chart", "localizedLanguage": "zh-CN",
            "showLocalizedTitles": "true", "tmdbApiKeyEnv": "TMDB_API_KEY", "tmdbBearerTokenEnv": "TMDB_BEARER_TOKEN"}


def china_movies():
    rows = [("生化危机：爆发夜", "24.25%", "4.06亿", "20", "23.46%"), ("神探之痕迹", "16.95%", "5.96亿", "24", "20.12%"),
            ("什么意思夫妇", "7.89%", "3.59亿", "24", "11.66%"), ("八仙！", "7.01%", "20.51亿", "99", "3.54%"),
            ("小羊肖恩3：怪兽来袭", "5.01%", "48.51万", "1", "8.05%")]
    return [BoxOfficeMovie(rank=index, title=title, weekend_gross=share, total_gross=total, weeks=days,
                           extra={"source": "zgdypw_realtime", "official_chinese_title": title,
                                  "show_count_rate": sessions, "english_title": f"Film {index}",
                                  "source_metadata": {"source_updated_at": NOW.isoformat()}})
            for index, (title, share, total, days, sessions) in enumerate(rows, start=1)]


def america_movies():
    rows = [("Street Fighter", "街头霸王", "$30,775,377", "$30,775,377", "3575", "3"),
            ("You Can See Everything", "你能看到一切", "$15,601,418", "$15,601,418", "855", "3"),
            ("Other Mommy", "另一个妈咪", "$8,204,680", "$44,709,120", "3370", "10"),
            ("Whalefall", "鲸口脱险", "$7,837,153", "$7,837,153", "3065", "3"),
            ("Sense and Sensibility", "理智与情感", "$7,203,465", "$7,203,465", "2545", "3")]
    return [BoxOfficeMovie(rank=index, title=title, localized_title=zh, weekend_gross=gross, total_gross=total,
                           theaters=theaters, weeks=days)
            for index, (title, zh, gross, total, theaters, days) in enumerate(rows, start=1)]


def snapshot(movies, label, provenance=SourceProvenance.LIVE, *, ready=0):
    return MarketSnapshot(movies, label, NOW, provenance is SourceProvenance.STALE_CACHE, provenance, ready)


@pytest.fixture
def page(monkeypatch):
    plugin = BoxOfficeTopMovies({"id": "box_office_top_movies"})
    america = plugin._north_america_market()
    calls = {"china": [], "america": []}

    def market(name, result):
        def load(settings, _device, _dimensions):
            calls[name].append(dict(settings))
            if isinstance(result, Exception):
                raise result
            return result
        return load

    def wire(china=None, america_result=None):
        monkeypatch.setattr(plugin, "_market_snapshot",
                            market("china", china or snapshot(china_movies(), "中国电影票房", ready=5)))
        monkeypatch.setattr(america, "_market_snapshot", market(
            "america", america_result if america_result is not None else snapshot(america_movies(), "The Numbers",
                                                                                  ready=4)))
        return plugin, america, calls

    return wire


def test_north_america_column_keeps_its_own_source_and_shares_only_presentation_settings():
    settings = {**SETTINGS, "mainlandRealtimeUrl": "https://zgdypf.zgdypw.cn/box", "forceRefresh": True,
                "_inkypi_theme": {"mode": "night"}}

    america = two_markets.north_america_settings(settings)

    assert america["sourceMode"] == "the_numbers"
    assert america["cacheHours"] == two_markets.NORTH_AMERICA_CACHE_HOURS
    assert america["chartUrl"] == SETTINGS["chartUrl"]
    assert america["tmdbApiKeyEnv"] == "TMDB_API_KEY" and america["forceRefresh"] is True
    assert america["_inkypi_theme"] == {"mode": "night"}
    assert "mainlandRealtimeUrl" not in america and "maoyanUrl" not in america and "tmdbRegion" not in america


def test_official_china_chart_draws_both_markets_unless_switched_off(page, monkeypatch):
    plugin, _america, calls = page()

    image = plugin.generate_image({**SETTINGS, "_inkypi_theme": canonical_theme("night")}, DummyDeviceConfig())

    assert image.size == (800, 480)
    assert read_source_provenance(image) is SourceProvenance.LIVE
    assert (image.info["inkypi_media_ready"], image.info["inkypi_media_total"]) == (9, 10)
    assert "inkypi_skip_cache" not in image.info
    assert calls["america"][0]["sourceMode"] == "the_numbers"
    assert image.getpixel((400, 300)) == two_markets.PALETTES["night"]["rule"]
    assert image.getpixel((150, 60)) == two_markets.PALETTES["night"]["china"]["banner"]
    assert image.getpixel((600, 70)) == two_markets.PALETTES["night"]["america"]["banner"]

    single = []
    monkeypatch.setattr(plugin, "_render_chart", lambda *args: single.append(args) or Image.new("RGB", (800, 480)))
    plugin.generate_image({**SETTINGS, "northAmericaColumn": "false", "_inkypi_theme": canonical_theme("day")},
                          DummyDeviceConfig())
    assert len(single) == 1 and len(calls["america"]) == 1


def test_the_page_stays_up_and_reports_stale_while_north_america_is_unavailable(page):
    plugin, _america, _calls = page(america_result=RuntimeError("offline"))

    image = plugin.generate_image({**SETTINGS, "_inkypi_theme": canonical_theme("day")}, DummyDeviceConfig())

    assert read_source_provenance(image) is SourceProvenance.STALE_CACHE
    assert image.info["inkypi_skip_cache"] is True
    assert image.info["inkypi_media_total"] == 5


def test_cancellation_while_loading_north_america_is_not_hidden(page):
    plugin, _america, _calls = page(america_result=TaskCancelled("deadline"))

    with pytest.raises(TaskCancelled):
        plugin.generate_image({**SETTINGS, "_inkypi_theme": canonical_theme("day")}, DummyDeviceConfig())


@pytest.mark.parametrize("china, america, expected", [
    (SourceProvenance.LIVE, SourceProvenance.FRESH_CACHE, SourceProvenance.FRESH_CACHE),
    (SourceProvenance.FRESH_CACHE, SourceProvenance.STALE_CACHE, SourceProvenance.STALE_CACHE),
    (SourceProvenance.LOCAL_FALLBACK, SourceProvenance.LIVE, SourceProvenance.LOCAL_FALLBACK),
    (SourceProvenance.LIVE, None, SourceProvenance.STALE_CACHE),
])
def test_the_page_is_as_fresh_as_its_staler_column(china, america, expected):
    first = snapshot([], "中国电影票房", china)
    second = None if america is None else snapshot([], "The Numbers", america)

    assert two_markets.combined_provenance(first, second) is expected


@pytest.mark.parametrize("china_ready, america_ready", [(True, False), (False, True), (True, True)])
def test_media_repair_completes_the_column_that_needs_it_and_draws_the_other_from_cache(
        page, monkeypatch, china_ready, america_ready):
    plugin, america, calls = page()
    monkeypatch.setattr(plugin, "_media_repair_ready", lambda *_a: china_ready)
    monkeypatch.setattr(america, "_media_repair_ready", lambda *_a: america_ready)

    plugin.generate_image({**SETTINGS, "_movie_media_only": True, "_inkypi_theme": canonical_theme("day")},
                          DummyDeviceConfig())

    for name, ready in (("china", china_ready), ("america", america_ready)):
        settings = calls[name][0]
        assert bool(settings.get("_movie_media_only")) is ready
        assert bool(settings.get("_theme_render_only")) is not ready


def test_media_repair_with_nothing_to_complete_waits_for_data(page, monkeypatch):
    plugin, america, calls = page()
    monkeypatch.setattr(plugin, "_media_repair_ready", lambda *_a: False)
    monkeypatch.setattr(america, "_media_repair_ready", lambda *_a: False)

    with pytest.raises(TaskCancelled, match="await DATA refresh"):
        plugin.generate_image({**SETTINGS, "_movie_media_only": True}, DummyDeviceConfig())
    assert calls == {"china": [], "america": []}


def test_china_column_without_a_cache_during_north_america_repair_waits_for_data(page, monkeypatch):
    plugin, america, _calls = page(china=None)
    monkeypatch.setattr(plugin, "_media_repair_ready", lambda *_a: False)
    monkeypatch.setattr(america, "_media_repair_ready", lambda *_a: True)
    monkeypatch.setattr(plugin, "_market_snapshot", lambda *_a: (_ for _ in ()).throw(RuntimeError("no cache")))

    with pytest.raises(TaskCancelled, match="await DATA refresh"):
        plugin.generate_image({**SETTINGS, "_movie_media_only": True}, DummyDeviceConfig())


def test_live_poster_repair_follows_whichever_column_misses_posters(monkeypatch):
    plugin = BoxOfficeTopMovies({"id": "box_office_top_movies"})
    america = plugin._north_america_market()
    seen = []
    monkeypatch.setattr(plugin, "_media_repair_state", lambda *_a: None)
    monkeypatch.setattr(america, "get_live_refresh_state",
                        lambda settings, _now: seen.append(settings) or {"active": True, "interval_seconds": 300})

    assert plugin.get_live_refresh_state(SETTINGS, NOW) == {"active": True, "interval_seconds": 300}
    assert seen[0]["sourceMode"] == "the_numbers"
    assert plugin.get_live_refresh_state({**SETTINGS, "northAmericaColumn": "false"}, NOW) is None
    assert len(seen) == 1


def test_chips_and_totals_read_each_market_in_its_own_units():
    china, america = china_movies(), america_movies()

    assert two_markets.china_chips(china[0]) == (("bars", "累计", "4.06亿"), ("reel", "排片", "23.46%"),
                                                 ("calendar", "上映", "20天"))
    assert two_markets.america_chips(america[0]) == (("bars", "累计", "$30.8M"), ("seats", "影院", "3,575"),
                                                     ("calendar", "上映", "3天"))
    assert two_markets.america_chips(BoxOfficeMovie(rank=1, title="Unknown")) == (
        ("bars", "累计", "—"), ("seats", "影院", "—"), ("calendar", "上映", "—"))
    assert two_markets.china_total(china) == "TOP5 合计占比 61.1%"
    assert two_markets.america_total(america) == "TOP5 合计 $69.6M"
    assert two_markets.compact_money("$512,300") == "$512.3K"
    assert two_markets.compact_money("暂无") == "暂无"


@pytest.mark.parametrize("mode", ["day", "night"])
def test_both_themes_render_the_unavailable_column_inside_its_panel(mode):
    plugin = BoxOfficeTopMovies({"id": "box_office_top_movies"})
    china = snapshot(china_movies(), "中国电影票房")

    image = two_markets.render_two_markets(plugin, (800, 480), china, None, {"_inkypi_theme": {"mode": mode}})

    colors = two_markets.PALETTES[mode]
    assert image.getpixel((5, 300)) == colors["page"]
    assert image.getpixel((600, 70)) == colors["america"]["banner"]
    panel = image.crop((410, 230, 790, 270))
    assert colors["text"] in {color for _count, color in panel.getcolors(maxcolors=panel.width * panel.height)}


@pytest.mark.parametrize("mode", ["day", "night"])
def test_the_page_title_is_its_wordmark_in_the_themes_ink(mode, monkeypatch):
    plugin = BoxOfficeTopMovies({"id": "box_office_top_movies"})
    typed = []
    original = two_markets._Page.text

    def record(self, xy, text, *args, **kwargs):
        typed.append(text)
        return original(self, xy, text, *args, **kwargs)

    monkeypatch.setattr(two_markets._Page, "text", record)
    image = two_markets.render_two_markets(plugin, (800, 480), snapshot(china_movies(), "中国电影票房"), None,
                                           {"_inkypi_theme": {"mode": mode}})

    assert "全球票房" not in typed
    # The white lettering stays white at night and turns to ink by day; 双城榜 stays red.
    lettering = [image.getpixel((x, y)) for x in range(14, 120) for y in range(6, 34)]
    ink = two_markets.PALETTES[mode]["text"]
    assert sum(1 for pixel in lettering if max(abs(a - b) for a, b in zip(pixel, ink)) < 40) > 150


def test_banner_icons_sit_on_the_tile_centre_and_the_right_margin():
    canvas = Image.new("RGB", (120, 40), (0, 0, 0))

    width = two_markets.paste_icon(canvas, "liberty", 22, 4, 27, (255, 255, 255), anchor="center")
    columns = [x for x in range(120) if any(canvas.getpixel((x, y))[0] > 128 for y in range(40))]
    assert abs((columns[0] + columns[-1]) / 2 - 22) <= 1.5 and width < 20

    canvas = Image.new("RGB", (120, 40), (0, 0, 0))
    two_markets.paste_icon(canvas, "skyline", 110, 4, 24, (255, 255, 255), anchor="right")
    columns = [x for x in range(120) if any(canvas.getpixel((x, y))[0] > 128 for y in range(40))]
    assert 106 <= columns[-1] <= 110


def test_icons_are_transparent_white_silhouettes():
    for name in ("pagoda", "liberty", "skyline", "bars", "reel", "calendar", "seats", "camera", "ticket"):
        with Image.open(two_markets.ICON_DIR / f"{name}.png") as icon:
            assert icon.mode == "RGBA" and max(icon.size) == 128
            assert icon.getchannel("A").getextrema()[0] == 0
            assert icon.getchannel("A").getbbox() is not None


def test_footer_names_the_sources_by_their_logos():
    page = two_markets._Page(BoxOfficeTopMovies({"id": "box_office_top_movies"}), (800, 480), {})

    runs = two_markets._source_runs(page, ["中国电影票房", "The Numbers", "Box Office Mojo"])

    assert isinstance(runs[0], Image.Image) and runs[1] == " 中国电影票房"
    assert runs[2] == " / " and isinstance(runs[3], Image.Image) and runs[3].height == 11
    assert runs[4:] == [" / ", "Box Office Mojo"]
    assert [type(run) for run in two_markets._source_runs(page, ["TMDb", "猫眼"])] == [Image.Image, str, Image.Image]


@pytest.mark.parametrize("mode", ["day", "night"])
def test_champion_cards_show_extra_source_facts_without_touching_lower_rankings(mode, monkeypatch):
    plugin = BoxOfficeTopMovies({"id": "box_office_top_movies"})
    china, america = china_movies(), america_movies()
    china[0].extra["today_box_wan"] = "24.60"
    america[0].extra = {"theaters_verified": True, "theater_average": "$8,609"}
    drawn = []
    original = two_markets.ImageDraw.ImageDraw.text

    def capture(draw, xy, text, *args, **kwargs):
        drawn.append((xy, text, draw.textbbox(xy, text, font=kwargs.get("font"))))
        return original(draw, xy, text, *args, **kwargs)

    monkeypatch.setattr(two_markets.ImageDraw.ImageDraw, "text", capture)
    image = two_markets.render_two_markets(plugin, (800, 480), snapshot(china, "中国电影票房"),
                                         snapshot(america, "The Numbers"), {"_inkypi_theme": {"mode": mode}})
    assert image.size == (800, 480)
    texts = [text for _xy, text, _box in drawn]
    assert "24.60万" in texts and "$8,609" in texts and "本期票房" in texts
    assert "24.25%" in texts and "$30,775,377" in texts
    # Supplemental facts remain in their own headliner; the next four films keep their rows.
    for xy, text, box in drawn:
        if text in ("24.60万", "$8,609", "+7.30pp"):
            assert box[1] >= 130 and box[3] < 234
            assert (128 <= box[0] < box[2] <= 384) if xy[0] < 400 else (526 <= box[0] < box[2] <= 782)
    assert sum(text == "#2" and xy[1] == 252 for xy, text, _box in drawn) == 2


def test_missing_or_unverified_source_values_do_not_become_new_hero_statistics():
    movie = BoxOfficeMovie(rank=1, title="Cached film", weekend_gross="$24,002,731", theaters="3458")
    assert two_markets.theater_average(movie) is None
    assert all(label != "单馆" for _icon, label, _value in two_markets.hero_details("america", movie))
    for raw in (None, "", "—", "nan", "not available"):
        movie.extra = {"today_box_wan": raw}
        assert two_markets.china_today_gross(movie) is None
    movie.extra = {"today_box_wan": "0"}
    assert two_markets.china_today_gross(movie) == "0.00万"
    movie.extra = {"today_box_wan": "12000"}
    assert two_markets.china_today_gross(movie) == "1.20亿"


@pytest.mark.parametrize("count, expected", [("7", "$500"), ("-", None), ("0", None)])
def test_single_cinema_average_uses_the_theaters_column_not_rank_or_release_days(count, expected):
    plugin = BoxOfficeTopMovies({"id": "box_office_top_movies"})
    html = f"""<table><tr><th>Rank</th><th>Movie</th><th>Gross</th><th>Theaters</th><th>Total Gross</th><th>Days</th></tr>
      <tr><td>12</td><td><a href='/movie/Small-Release'>Small Release</a></td><td>$3,500</td>
      <td>{count}</td><td>$10,000</td><td>17</td></tr></table>"""
    movie = plugin._parse_the_numbers_chart(html)[0]
    assert movie.theaters == (count if count == "7" else "")
    assert two_markets.theater_average(movie) == expected
