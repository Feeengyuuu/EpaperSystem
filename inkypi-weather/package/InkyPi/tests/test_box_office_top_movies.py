import sys
from pathlib import Path

from PIL import Image, ImageFont

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


def maoyan_sample_payload():
    return {
        "movieList": {
            "data": {
                "list": [
                    {
                        "boxRate": "32.1%",
                        "movieInfo": {"movieId": 1490532, "movieName": "\u73a9\u5177\u603b\u52a8\u54585", "releaseInfo": "\u4e0a\u66202\u5929"},
                        "showCount": 114406,
                        "showCountRate": "24.6%",
                        "sumBoxDesc": "7708.9\u4e07",
                    },
                    {
                        "boxRate": "14.2%",
                        "movieInfo": {"movieId": 1522873, "movieName": "\u6293\u7279\u52a1", "releaseInfo": "\u4e0a\u66202\u5929"},
                        "showCount": 72579,
                        "showCountRate": "15.6%",
                        "sumBoxDesc": "3600.6\u4e07",
                    },
                    {
                        "boxRate": "8.7%",
                        "movieInfo": {"movieId": 1595371, "movieName": "\u706b\u906e\u773c", "releaseInfo": "\u4e0a\u662010\u5929"},
                        "showCount": 48755,
                        "showCountRate": "10.5%",
                        "sumBoxDesc": "1.56\u4ebf",
                    },
                ]
            }
        }
    }


def test_parse_maoyan_dashboard_uses_simplified_chinese_titles():
    plugin = BoxOfficeTopMovies({"id": "box_office_top_movies"})

    movies = plugin._parse_maoyan_dashboard(maoyan_sample_payload())

    assert [movie.title for movie in movies] == ["\u73a9\u5177\u603b\u52a8\u54585", "\u6293\u7279\u52a1", "\u706b\u906e\u773c"]
    assert movies[0].weekend_gross == "32.1%"
    assert movies[0].total_gross == "7708.9\u4e07"
    assert movies[0].localized_title == ""
    assert movies[0].localized_language == "zh-CN"
    assert movies[0].extra["source"] == "maoyan"


def test_maoyan_china_source_fetches_mainland_chart(monkeypatch):
    class FakeResponse:
        encoding = None

        def raise_for_status(self):
            pass

        def json(self):
            return maoyan_sample_payload()

    class FakeSession:
        def get(self, url, timeout=None, headers=None):
            assert url == box_office_module.MAOYAN_DASHBOARD_URL
            assert headers["Referer"] == "https://piaofang.maoyan.com/dashboard"
            assert timeout == 16
            return FakeResponse()

    monkeypatch.setattr(box_office_module, "get_http_session", lambda: FakeSession())
    plugin = BoxOfficeTopMovies({"id": "box_office_top_movies"})

    movies, source_label = plugin._load_movies({"sourceMode": "maoyan_china"}, 1)

    assert source_label == box_office_module.MAOYAN_SOURCE_LABEL
    assert [movie.title for movie in movies] == ["\u73a9\u5177\u603b\u52a8\u54585"]


def test_china_chart_copy_uses_simplified_chinese_labels():
    plugin = BoxOfficeTopMovies({"id": "box_office_top_movies"})

    copy = plugin._chart_copy({"sourceMode": "maoyan_china"}, box_office_module.MAOYAN_SOURCE_LABEL, 5)

    assert copy["title"] == "\u4e2d\u56fd\u5927\u9646\u7535\u5f71\u7968\u623f"
    assert copy["subtitle"] == "\u5b9e\u65f6\u699c TOP 5"
    assert copy["primary_metric_label"] == "\u4eca\u65e5\u5360\u6bd4"
    assert copy["total_prefix"] == "\u7d2f\u8ba1"

def test_maoyan_display_uses_official_chinese_primary_and_english_secondary():
    plugin = BoxOfficeTopMovies({"id": "box_office_top_movies"})
    movie = BoxOfficeMovie(
        rank=1,
        title="\u6211\u770b\u89c1\u4e24\u6735\u4e00\u6837\u7684\u4e91",
        localized_title="\u6211\u62ac\u5934\u53d1\u73b0\u4e24\u6735\u4e00\u6837\u7684\u4e91",
        extra={
            "source": "maoyan",
            "official_chinese_title": "\u6211\u770b\u89c1\u4e24\u6735\u4e00\u6837\u7684\u4e91",
            "english_title": "Two Like Clouds",
        },
    )

    assert plugin._display_titles(movie) == (
        "\u6211\u770b\u89c1\u4e24\u6735\u4e00\u6837\u7684\u4e91",
        "Two Like Clouds",
    )


def test_maoyan_tmdb_enrichment_keeps_official_chinese_and_sets_english_title(monkeypatch):
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
                assert params["language"] == "zh-CN"
                assert params["region"] == "CN"
                return FakeResponse({
                    "results": [{
                        "id": 200,
                        "poster_path": "/china-poster.jpg",
                        "release_date": "2026-06-20",
                        "title": "\u6211\u62ac\u5934\u53d1\u73b0\u4e24\u6735\u4e00\u6837\u7684\u4e91",
                        "original_title": "\u6211\u770b\u89c1\u4e24\u6735\u4e00\u6837\u7684\u4e91",
                    }]
                })
            if url.endswith("/movie/200/images"):
                assert params["include_image_language"] == "zh,null"
                return FakeResponse({"posters": []})
            if url.endswith("/movie/200"):
                assert params["language"] == "en-US"
                return FakeResponse({
                    "title": "Two Like Clouds",
                    "original_title": "\u6211\u770b\u89c1\u4e24\u6735\u4e00\u6837\u7684\u4e91",
                })
            raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr(box_office_module, "get_http_session", lambda: FakeSession())
    plugin = BoxOfficeTopMovies({"id": "box_office_top_movies"})
    movie = BoxOfficeMovie(
        rank=1,
        title="\u6211\u770b\u89c1\u4e24\u6735\u4e00\u6837\u7684\u4e91",
        extra={
            "source": "maoyan",
            "official_chinese_title": "\u6211\u770b\u89c1\u4e24\u6735\u4e00\u6837\u7684\u4e91",
        },
    )

    plugin._enrich_with_tmdb(
        [movie],
        {"sourceMode": "maoyan_china"},
        EnvDeviceConfig({"TMDB_API_KEY": "device-key"}),
    )

    assert movie.localized_title == ""
    assert movie.extra["official_chinese_title"] == "\u6211\u770b\u89c1\u4e24\u6735\u4e00\u6837\u7684\u4e91"
    assert movie.extra["english_title"] == "Two Like Clouds"
    assert plugin._display_titles(movie) == (
        "\u6211\u770b\u89c1\u4e24\u6735\u4e00\u6837\u7684\u4e91",
        "Two Like Clouds",
    )

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


def test_tmdb_enrichment_prefers_english_market_poster(monkeypatch):
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
                assert params["language"] == "en-US"
                assert params["region"] == "US"
                return FakeResponse({
                    "results": [{
                        "id": 100,
                        "poster_path": "/default-poster.jpg",
                        "release_date": "2026-05-01",
                        "overview": "English overview",
                    }]
                })
            if url.endswith("/movie/100/images"):
                assert params["include_image_language"] == "en,null"
                return FakeResponse({
                    "posters": [
                        {"file_path": "/no-language-poster.jpg", "iso_639_1": None, "aspect_ratio": 0.667, "vote_average": 9, "vote_count": 20, "width": 1000},
                        {"file_path": "/english-market-poster.jpg", "iso_639_1": "en", "aspect_ratio": 0.667, "vote_average": 6, "vote_count": 5, "width": 1000},
                    ]
                })
            raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr(box_office_module, "get_http_session", lambda: FakeSession())
    plugin = BoxOfficeTopMovies({"id": "box_office_top_movies"})
    movie = BoxOfficeMovie(rank=1, title="Test Movie")

    plugin._enrich_with_tmdb(
        [movie],
        {"tmdbLanguage": "en-US", "tmdbRegion": "US", "showLocalizedTitles": False},
        EnvDeviceConfig({"TMDB_API_KEY": "device-key"}),
    )

    assert movie.poster_path == "/english-market-poster.jpg"
    assert movie.poster_url.endswith("/english-market-poster.jpg")
    assert movie.extra["poster_language"] == "en"
    assert movie.extra["poster_market"] == "US"


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


def first_available_yahei_path(plugin, bold=False):
    for path in plugin._microsoft_yahei_paths(bold):
        path = Path(path)
        if path.is_file():
            return path
    raise AssertionError("Microsoft YaHei font is not available")


def assert_uses_yahei(font, expected_path):
    path = Path(getattr(font, "path", ""))
    family = font.getname()[0] if hasattr(font, "getname") else ""

    assert path == expected_path
    assert "Microsoft YaHei" in family


def test_default_font_uses_microsoft_yahei():
    plugin = BoxOfficeTopMovies({"id": "box_office_top_movies"})
    expected_path = first_available_yahei_path(plugin, bold=False)
    selected_font = plugin._font(18, bold=False, cjk=False)
    fallback_font = plugin._default_yahei_font(18, bold=False)

    assert expected_path.name == box_office_module.YAHEI_REGULAR_FILE
    assert_uses_yahei(selected_font, expected_path)
    assert_uses_yahei(fallback_font, expected_path)


def test_cjk_bold_font_uses_microsoft_yahei():
    plugin = BoxOfficeTopMovies({"id": "box_office_top_movies"})
    expected_path = first_available_yahei_path(plugin, bold=True)
    selected_font = plugin._font(24, bold=True, cjk=True)
    fallback_font = plugin._default_yahei_font(24, bold=True)

    assert expected_path.name == box_office_module.YAHEI_BOLD_FILE
    assert_uses_yahei(selected_font, expected_path)
    assert_uses_yahei(fallback_font, expected_path)
    assert plugin._font_has_cjk_glyphs(selected_font)


def test_cjk_glyph_detector_rejects_latin_tofu_boxes():
    arial_path = Path(r"C:\Windows\Fonts\arial.ttf")
    if not arial_path.is_file():
        return

    font = ImageFont.truetype(str(arial_path), 24)

    assert not BoxOfficeTopMovies._font_has_cjk_glyphs(font)


def test_cinema_placeholder_asset_is_transparent_project_png():
    path = Path(box_office_module.PLUGIN_DIR) / box_office_module.CINEMA_PLACEHOLDER_FILE

    with Image.open(path) as image:
        image = image.convert("RGBA")
        alpha = image.getchannel("A")

    assert image.size == box_office_module.CINEMA_PLACEHOLDER_SIZE
    assert alpha.getextrema() == (0, 255)
    assert [image.getpixel(point)[3] for point in [(0, 0), (299, 0), (0, 89), (299, 89)]] == [0, 0, 0, 0]


def test_cinema_placeholder_is_drawn_into_the_right_header_slot():
    plugin = BoxOfficeTopMovies({"id": "box_office_top_movies"})
    image = Image.new("RGB", (800, 480), (18, 21, 24))
    box = plugin._cinema_placeholder_box(800, 480, 18, 78, 198)

    assert box == (482, 108, 300, 90)
    plugin._load_cinema_placeholder_asset.cache_clear()
    plugin._draw_cinema_placeholder(image, box)

    crop = image.crop((box[0], box[1], box[0] + box[2], box[1] + box[3]))
    colors = crop.getcolors(maxcolors=box[2] * box[3] + 1) or []
    changed_pixels = sum(count for count, color in colors if color != (18, 21, 24))
    assert changed_pixels > 1000
