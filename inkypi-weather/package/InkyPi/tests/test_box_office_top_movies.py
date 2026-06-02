import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import plugins.box_office_top_movies.box_office_top_movies as box_office_module  # noqa: E402
from plugins.box_office_top_movies.box_office_top_movies import (  # noqa: E402
    BoxOfficeMovie,
    BoxOfficeTopMovies,
)


class DummyDeviceConfig:
    def get_resolution(self):
        return (800, 480)

    def get_config(self, _key, default=None):
        return default

    def load_env_key(self, _key):
        return None


class EnvDeviceConfig(DummyDeviceConfig):
    def __init__(self, values):
        self.values = values

    def load_env_key(self, key):
        return self.values.get(key)


def test_parse_the_numbers_weekend_table():
    html = """
    <table>
      <tr><th>Rank</th><th>Last</th><th>Movie</th><th>Distributor</th><th>Gross</th><th>Theaters</th><th>Total Gross</th><th>Week</th></tr>
      <tr><td>1</td><td>-</td><td><a href="/movie/Test-Movie#tab=box-office">Test Movie</a></td><td>Studio</td><td>$55,100,000</td><td>4,200</td><td>$55,100,000</td><td>1</td></tr>
      <tr><td>2</td><td>1</td><td><a href="/movie/Second-Film#tab=box-office">Second Film</a></td><td>Studio</td><td>$20,500,000</td><td>3,500</td><td>$80,000,000</td><td>2</td></tr>
    </table>
    """
    plugin = BoxOfficeTopMovies({"id": "box_office_top_movies"})

    movies = plugin._parse_the_numbers_chart(html, "https://www.the-numbers.com/box-office-chart/weekend")

    assert [movie.title for movie in movies] == ["Test Movie", "Second Film"]
    assert movies[0].rank == 1
    assert movies[0].weekend_gross == "$55,100,000"
    assert movies[0].total_gross == "$55,100,000"
    assert movies[0].theaters == "4200"


def test_tmdb_auth_prefers_bearer_env(monkeypatch):
    plugin = BoxOfficeTopMovies({"id": "box_office_top_movies"})
    monkeypatch.setenv("TMDB_BEARER_TOKEN", "bearer-value")
    monkeypatch.setenv("TMDB_API_KEY", "api-key-value")

    assert plugin._tmdb_auth({}) == {"type": "bearer", "value": "bearer-value"}


def test_tmdb_auth_uses_configured_env_key(monkeypatch):
    plugin = BoxOfficeTopMovies({"id": "box_office_top_movies"})
    monkeypatch.setenv("MY_TMDB_TOKEN", "custom-bearer")

    assert plugin._tmdb_auth({"tmdbBearerTokenEnv": "MY_TMDB_TOKEN"}) == {
        "type": "bearer",
        "value": "custom-bearer",
    }


def test_tmdb_auth_loads_device_api_keys():
    plugin = BoxOfficeTopMovies({"id": "box_office_top_movies"})
    device_config = EnvDeviceConfig({"TMDB_Access_Token": "device-bearer"})

    assert plugin._tmdb_auth({}, device_config) == {
        "type": "bearer",
        "value": "device-bearer",
    }


def test_tmdb_enrichment_loads_simplified_chinese_title(monkeypatch):
    class FakeResponse:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            pass

        def json(self):
            return self.payload

    class FakeSession:
        def get(self, url, params=None, headers=None, timeout=None):
            if url.endswith("/search/movie"):
                return FakeResponse({
                    "results": [{
                        "id": 100,
                        "poster_path": "/poster.jpg",
                        "release_date": "2026-05-01",
                        "overview": "English overview",
                    }]
                })
            if url.endswith("/movie/100"):
                return FakeResponse({"title": "测试电影"})
            raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr(box_office_module, "get_http_session", lambda: FakeSession())
    plugin = BoxOfficeTopMovies({"id": "box_office_top_movies"})
    movie = BoxOfficeMovie(rank=1, title="Test Movie")

    plugin._enrich_with_tmdb([movie], {"localizedLanguage": "zh-CN"}, EnvDeviceConfig({"TMDB_API_KEY": "device-key"}))

    assert movie.tmdb_id == 100
    assert movie.localized_title == "测试电影"
    assert movie.localized_language == "zh-CN"
    assert movie.poster_url.endswith("/poster.jpg")


def test_render_chart_smoke(monkeypatch):
    plugin = BoxOfficeTopMovies({"id": "box_office_top_movies"})
    smoke_dir = Path(__file__).resolve().parents[4] / "tmp" / "box_office_render_chart_smoke"
    smoke_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("INKYPI_BOX_OFFICE_CACHE", str(smoke_dir))

    poster = smoke_dir / "poster.jpg"
    Image.new("RGB", (300, 450), (180, 30, 40)).save(poster)
    movies = [
        BoxOfficeMovie(rank=1, title="The First Feature", localized_title="第一部电影", weekend_gross="$55.1M", total_gross="$55.1M", poster_path=str(poster)),
        BoxOfficeMovie(rank=2, title="Second Film", localized_title="第二部电影", weekend_gross="$20.5M", total_gross="$80.0M"),
        BoxOfficeMovie(rank=3, title="Third Movie", localized_title="第三部电影", weekend_gross="$14.2M", total_gross="$34.4M"),
        BoxOfficeMovie(rank=4, title="Fourth Title", localized_title="第四部电影", weekend_gross="$8.1M", total_gross="$120.2M"),
        BoxOfficeMovie(rank=5, title="Fifth Release", localized_title="第五部电影", weekend_gross="$5.7M", total_gross="$5.7M"),
    ]

    image = plugin._render_chart((800, 480), movies, {"themeMode": "cinema"}, "The Numbers", plugin._now_for_device(DummyDeviceConfig()))

    assert image.size == (800, 480)
    assert image.getbbox() is not None
