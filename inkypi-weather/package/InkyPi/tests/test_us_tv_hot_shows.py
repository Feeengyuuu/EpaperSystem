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
