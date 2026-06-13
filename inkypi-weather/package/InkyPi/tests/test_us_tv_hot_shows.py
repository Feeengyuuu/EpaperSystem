import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.box_office_top_movies.box_office_top_movies import BoxOfficeMovie  # noqa: E402
from plugins.us_tv_hot_shows.us_tv_hot_shows import UsTvHotShows  # noqa: E402


class DummyDeviceConfig:
    def get_resolution(self):
        return (800, 480)

    def get_config(self, _key, default=None):
        return default

    def load_env_key(self, _key):
        return None


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
