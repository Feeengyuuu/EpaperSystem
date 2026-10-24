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


def test_icons_are_transparent_white_silhouettes():
    for name in ("pagoda", "liberty", "skyline", "bars", "reel", "calendar", "seats", "camera", "ticket"):
        with Image.open(two_markets.ICON_DIR / f"{name}.png") as icon:
            assert icon.mode == "RGBA" and max(icon.size) == 128
            assert icon.getchannel("A").getextrema()[0] == 0
            assert icon.getchannel("A").getbbox() is not None
