import hashlib
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.box_office_top_movies.box_office_top_movies import BoxOfficeMovie  # noqa: E402
from plugins.us_tv_hot_shows.us_tv_hot_shows import UsTvHotShows  # noqa: E402


def test_streaming_background_stays_solid_for_color_epaper():
    plugin = UsTvHotShows({"id": "us_tv_hot_shows"})

    for colors in (
        {"mode": "paper", "paper": (244, 238, 246), "line": (185, 188, 194), "shadow": (255, 255, 255)},
        {"mode": "streaming", "paper": (20, 13, 25), "line": (46, 48, 56), "shadow": (0, 0, 0)},
    ):
        image = Image.new("RGB", (96, 64), colors["paper"])

        plugin._draw_streaming_background(image, colors)

        assert image.getcolors(maxcolors=image.width * image.height) == [
            (image.width * image.height, colors["paper"])
        ]


class DummyDeviceConfig:
    def get_resolution(self):
        return (800, 480)

    def get_config(self, _key, default=None):
        return default

    def load_env_key(self, _key):
        return None


def canonical_theme(mode):
    palette = {
        "background": (244, 238, 246) if mode == "day" else (20, 13, 25),
        "panel": (255, 255, 255) if mode == "day" else (0, 0, 0),
        "ink": (10, 12, 15) if mode == "day" else (255, 255, 255),
        "muted": (74, 78, 84) if mode == "day" else (194, 196, 202),
        "rule": (185, 188, 194) if mode == "day" else (46, 48, 56),
        "accent": (125, 74, 161) if mode == "day" else (190, 134, 223),
    }
    return {
        "mode": mode,
        "requested_mode": "auto",
        "palette": palette,
        "css": {
            role: "#{:02x}{:02x}{:02x}".format(*color)
            for role, color in palette.items()
        },
    }


def image_digest(image):
    return hashlib.sha256(image.tobytes()).hexdigest()


def test_shows_from_tmdb_results_uses_localized_title():
    plugin = UsTvHotShows({"id": "us_tv_hot_shows"})
    shows = plugin._shows_from_tmdb_results(
        [{
            "id": 10,
            "name": "测试美剧",
            "original_name": "Test Show",
            "origin_country": ["US"],
            "poster_path": "/poster.jpg",
            "first_air_date": "2026-06-01",
            "popularity": 88.82,
            "vote_average": 7.92,
            "overview": "Overview",
        }],
        5,
        "TMDb 热度",
        "on_air",
    )

    assert shows[0].rank == 1
    assert shows[0].title == "Test Show"
    assert shows[0].localized_title == "测试美剧"
    assert shows[0].weekend_gross == "88.8"
    assert shows[0].total_gross == "2026-06-01"
    assert shows[0].poster_url.endswith("/poster.jpg")


def test_base_discover_params_defaults_to_us_english():
    plugin = UsTvHotShows({"id": "us_tv_hot_shows"})

    params = plugin._base_discover_params({})

    assert params["with_origin_country"] == "US"
    assert params["with_original_language"] == "en"
    assert params["language"] == "zh-CN"


def test_us_tv_uses_injected_canonical_palette_and_source_only_cache_key():
    plugin = UsTvHotShows({"id": "us_tv_hot_shows"})
    day_theme = canonical_theme("day")
    settings = {"themeMode": "streaming", "_inkypi_theme": day_theme}

    palette = plugin._palette(settings)

    assert palette == {
        "mode": "paper",
        "paper": day_theme["palette"]["background"],
        "ink": day_theme["palette"]["ink"],
        "muted": day_theme["palette"]["muted"],
        "accent": day_theme["palette"]["accent"],
        "localized": day_theme["palette"]["accent"],
        "line": day_theme["palette"]["rule"],
        "outline": day_theme["palette"]["ink"],
        "shadow": day_theme["palette"]["panel"],
    }
    assert plugin._palette({**settings, "themeMode": "paper"}) == palette
    assert plugin._cache_key(settings, (800, 480), 5) == plugin._cache_key(
        {
            **settings,
            "themeMode": "paper",
            "_inkypi_theme": canonical_theme("night"),
        },
        (480, 800),
        5,
    )


def test_us_tv_theme_only_opposite_palette_reuses_warm_source_cache(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("INKYPI_US_TV_HOT_SHOWS_CACHE", str(tmp_path))
    plugin = UsTvHotShows({"id": "us_tv_hot_shows"})
    calls = {"load": 0, "posters": 0}

    def fake_load_shows(_settings, _items_count):
        calls["load"] += 1
        return [BoxOfficeMovie(rank=1, title="Theme Test Show")], "TMDb US TV On Air"

    def fake_download(*_args, **_kwargs):
        calls["posters"] += 1

    monkeypatch.setattr(plugin, "_load_shows", fake_load_shows)
    monkeypatch.setattr(plugin, "_download_posters", fake_download)
    monkeypatch.setattr(plugin, "_write_us_tv_context", lambda *_args: None)

    day = plugin.generate_image(
        {"themeMode": "streaming", "_inkypi_theme": canonical_theme("day")},
        DummyDeviceConfig(),
    )
    night = plugin.generate_image(
        {
            "themeMode": "paper",
            "_inkypi_theme": canonical_theme("night"),
            "_theme_render_only": True,
        },
        DummyDeviceConfig(),
    )

    assert calls == {"load": 1, "posters": 1}
    assert image_digest(day) != image_digest(night)


def test_render_chart_smoke(monkeypatch):
    plugin = UsTvHotShows({"id": "us_tv_hot_shows"})
    smoke_dir = Path(__file__).resolve().parents[4] / "tmp" / "us_tv_hot_shows_render_smoke"
    smoke_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("INKYPI_US_TV_HOT_SHOWS_CACHE", str(smoke_dir))

    poster = smoke_dir / "poster.jpg"
    Image.new("RGB", (300, 450), (42, 120, 190)).save(poster)
    shows = [
        BoxOfficeMovie(rank=1, title="Test Show One", localized_title="测试美剧一", weekend_gross="128.4", total_gross="2026-05-01", poster_path=str(poster), extra={"metric_label": "TMDb 热度", "total_label": "首播"}),
        BoxOfficeMovie(rank=2, title="Test Show Two", localized_title="测试美剧二", weekend_gross="92.7", total_gross="2026-04-18", extra={"metric_label": "TMDb 热度", "total_label": "首播"}),
        BoxOfficeMovie(rank=3, title="Test Show Three", localized_title="测试美剧三", weekend_gross="88.1", total_gross="2026-03-22", extra={"metric_label": "TMDb 热度", "total_label": "首播"}),
        BoxOfficeMovie(rank=4, title="Test Show Four", localized_title="测试美剧四", weekend_gross="76.5", total_gross="2026-02-14", extra={"metric_label": "TMDb 热度", "total_label": "首播"}),
        BoxOfficeMovie(rank=5, title="Test Show Five", localized_title="测试美剧五", weekend_gross="61.9", total_gross="2026-01-09", extra={"metric_label": "TMDb 热度", "total_label": "首播"}),
    ]

    image = plugin._render_chart((800, 480), shows, {"themeMode": "auto"}, "TMDb US TV On Air", plugin._now_for_device(DummyDeviceConfig()))

    assert image.size == (800, 480)
    assert image.getbbox() is not None


# --- Douban titles and ratings, streaming services and the next episode ------------------------

import json as _json  # noqa: E402
from datetime import date as _date  # noqa: E402

import pytest  # noqa: E402
import requests  # noqa: E402
from PIL import ImageDraw  # noqa: E402

import plugins.us_tv_hot_shows.us_tv_hot_shows as us_tv_module  # noqa: E402
from runtime.refresh_contracts import TaskCancelled  # noqa: E402


class _OfflineSession:
    def get(self, *_args, **_kwargs):
        raise requests.ConnectionError("offline in tests")


@pytest.fixture(autouse=True)
def _offline_douban_and_logos(monkeypatch):
    """Douban's collection and the provider logos would be fetched live; the tests stay offline."""
    monkeypatch.setattr(us_tv_module, "get_http_session", lambda: _OfflineSession())


class _JsonSession:
    def __init__(self, payload):
        self.payload, self.calls = payload, []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        response = requests.Response()
        response.status_code = 200
        response._content = _json.dumps(self.payload).encode("utf-8")
        return response


DOUBAN_ITEMS = {"subject_collection_items": [
    {"id": "36689816", "title": "流人 第六季", "rating": {"value": 7.2, "count": 19422}, "episodes_info": ""},
    {"id": "36227197", "title": "终极名单 第二季", "rating": {"value": 8.0}, "episodes_info": "更新至6集"},
    {"id": "36229588", "title": "夜曲", "rating": {"value": 0}, "episodes_info": ""},
    {"id": "1", "title": "", "rating": None}, "junk"]}


def test_tmdb_details_give_the_us_streaming_services_and_the_next_episode():
    details = {"next_episode_to_air": {"air_date": "2026-11-05", "season_number": 6, "episode_number": 4},
               "watch/providers": {"results": {"US": {"flatrate": [
                   {"provider_id": 1899, "provider_name": "HBO Max", "logo_path": "/jbe4gVSfRlbPTdESXhEKpornsfu.jpg",
                    "display_priority": 3},
                   {"provider_id": 350, "provider_name": "Apple TV+", "logo_path": "/2E03IAZsX4ZaUqM7tXlctEPMGWS.jpg",
                    "display_priority": 1},
                   {"provider_id": 9, "provider_name": "Bad", "logo_path": "../../etc/passwd"},
                   {"provider_id": 15, "provider_name": "Hulu", "logo_path": "/bxBlRPEPpMVDc4jMhSrTf2339DW.jpg"},
                   {"provider_id": 531, "provider_name": "Paramount+", "logo_path": "/h5DcR0J2EESLitnhR8xLG1QymTE.jpg"},
               ]}, "GB": {"flatrate": [{"provider_id": 8, "logo_path": "/x.jpg"}]}}}}

    extra = UsTvHotShows._details_extra(details)

    assert (extra["next_episode_date"], extra["next_episode_code"]) == ("2026-11-05", "S6E4")
    assert [provider["id"] for provider in extra["providers"]] == [350, 1899, 15]
    assert UsTvHotShows._details_extra({"next_episode_to_air": None}) == {}


def test_each_show_takes_its_details_from_one_tmdb_call(monkeypatch):
    plugin = UsTvHotShows({"id": "us_tv_hot_shows"})
    asked = []

    def fake_tmdb(url, params):
        asked.append((url, params))
        if url.endswith("/2"):
            raise requests.HTTPError("404")
        return {"next_episode_to_air": {"air_date": "2026-11-09", "season_number": 2, "episode_number": 3}}

    monkeypatch.setattr(plugin, "_tmdb_source_json", fake_tmdb)
    shows = [BoxOfficeMovie(rank=1, title="One", tmdb_id=1), BoxOfficeMovie(rank=2, title="Two", tmdb_id=2),
             BoxOfficeMovie(rank=3, title="No id")]

    plugin._enrich_show_details(shows, {})

    assert [url.rsplit("/", 1)[-1] for url, _params in asked] == ["1", "2"]
    assert asked[0][1] == {"language": "zh-CN", "append_to_response": "watch/providers"}
    assert shows[0].extra["next_episode_code"] == "S2E3" and "next_episode_date" not in shows[1].extra
    monkeypatch.setattr(plugin, "_tmdb_source_json", lambda *_a: (_ for _ in ()).throw(TaskCancelled("stop")))
    with pytest.raises(TaskCancelled):
        plugin._enrich_show_details(shows, {})


def test_douban_names_rates_and_counts_the_shows_it_lists(monkeypatch):
    plugin = UsTvHotShows({"id": "us_tv_hot_shows"})
    session = _JsonSession(DOUBAN_ITEMS)
    monkeypatch.setattr(us_tv_module, "get_http_session", lambda: session)
    shows = [BoxOfficeMovie(rank=1, title="Slow Horses", localized_title="流人"),
             BoxOfficeMovie(rank=2, title="终极名单"),
             BoxOfficeMovie(rank=3, title="Nocturne", localized_title="夜曲"),
             BoxOfficeMovie(rank=4, title="Lanterns")]

    plugin._enrich_with_douban(shows)

    assert (shows[0].extra["douban_title"], shows[0].extra["douban_rating"]) == ("流人 第六季", 7.2)
    # A show TMDB names only in English takes Douban's Chinese name, its season dropped.
    assert shows[1].localized_title == "终极名单" and shows[1].extra["douban_episodes"] == "更新至6集"
    assert "douban_rating" not in shows[2].extra  # Douban's 0 is "not yet rated"
    assert shows[3].extra == {} and shows[3].localized_title == ""
    assert session.calls[0][1]["headers"]["Referer"].startswith("https://m.douban.com/")
    assert session.calls[0][1]["params"] == {"start": 0, "count": us_tv_module.DOUBAN_COUNT}


def test_douban_unavailable_leaves_tmdbs_names_alone():
    plugin = UsTvHotShows({"id": "us_tv_hot_shows"})
    shows = [BoxOfficeMovie(rank=1, title="Slow Horses", localized_title="流人")]

    plugin._enrich_with_douban(shows)  # the autouse fixture's session is offline

    assert shows[0].extra == {}


def test_the_next_episode_shows_only_from_today_on():
    show = BoxOfficeMovie(rank=1, title="One", extra={"next_episode_date": "2026-11-05", "next_episode_code": "S6E4"})

    assert UsTvHotShows._next_episode_text(show, _date(2026, 11, 2)) == "下集 11/05 S6E4"
    assert UsTvHotShows._next_episode_text(show, _date(2026, 11, 6)) == ""
    assert UsTvHotShows._next_episode_text(BoxOfficeMovie(rank=1, title="Two"), _date(2026, 11, 2)) == ""


@pytest.mark.parametrize("mode", ["day", "night"])
def test_the_chart_shows_douban_ratings_streaming_logos_and_credits(monkeypatch, tmp_path, mode):
    monkeypatch.setenv("INKYPI_US_TV_HOT_SHOWS_CACHE", str(tmp_path))
    plugin = UsTvHotShows({"id": "us_tv_hot_shows"})
    logo = plugin._provider_logo_path(350)
    logo.parent.mkdir(parents=True)
    Image.new("RGB", (92, 92), (12, 120, 220)).save(logo)
    providers = [{"id": 350, "name": "Apple TV+", "logo_path": "/a.jpg"}]
    shows = [BoxOfficeMovie(rank=index, title=f"Show {index}", localized_title=f"美剧{index}", weekend_gross="99.1",
                            total_gross="2022-04-01",
                            extra={"metric_label": "TMDb 热度", "total_label": "首播", "douban_title": f"美剧{index}",
                                   "douban_rating": 7.0 + index / 10, "providers": providers,
                                   "next_episode_date": "2099-11-05", "next_episode_code": "S6E4"})
             for index in range(1, 6)]
    drawn = []
    original = ImageDraw.ImageDraw.text
    monkeypatch.setattr(ImageDraw.ImageDraw, "text", lambda draw, xy, text, *args, **kwargs:
                        drawn.append((xy, text)) or original(draw, xy, text, *args, **kwargs))

    image = plugin._render_chart((800, 480), shows, {"_inkypi_theme": canonical_theme(mode)}, "TMDb US TV On Air",
                                 plugin._now_for_device(DummyDeviceConfig()))

    texts = [text for _xy, text in drawn]
    assert {"豆瓣 7.1", "豆瓣 7.5", "下集 11/05 S6E4"} <= set(texts) and any("观看平台" in str(text) for text in texts)
    assert any("下集 11/05 S6E4" in str(text) and "首播 2022-04-01" in str(text) for text in texts)  # the headliner
    pixels = set(image.crop((540, 180, 800, 430)).get_flattened_data())
    assert (12, 120, 220) in pixels and us_tv_module.DOUBAN_GREEN in pixels
