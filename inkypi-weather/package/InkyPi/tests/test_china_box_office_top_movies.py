import hashlib
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.box_office_top_movies.box_office_top_movies import BoxOfficeMovie, BoxOfficeTopMovies  # noqa: E402
import plugins.china_box_office_top_movies.china_box_office_top_movies as china_box_office_module  # noqa: E402
from plugins.china_box_office_top_movies.china_box_office_top_movies import (  # noqa: E402
    ChinaBoxOfficeTopMovies,
)
from plugins.base_plugin.render_provenance import (  # noqa: E402
    SourceProvenance,
    read_source_provenance,
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


def canonical_theme(mode):
    palette = {
        "background": (255, 240, 223) if mode == "day" else (18, 13, 11),
        "panel": (255, 255, 255) if mode == "day" else (0, 0, 0),
        "ink": (10, 12, 15) if mode == "day" else (255, 255, 255),
        "muted": (74, 78, 84) if mode == "day" else (194, 196, 202),
        "rule": (185, 188, 194) if mode == "day" else (46, 48, 56),
        "accent": (182, 59, 34) if mode == "day" else (255, 121, 91),
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


def test_north_america_cache_invalidates_when_tmdb_credentials_appear(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("INKYPI_CHINA_BOX_OFFICE_CACHE", str(tmp_path))
    plugin = ChinaBoxOfficeTopMovies({"id": "china_box_office_top_movies"})
    load_calls = []

    def fake_load_movies(_settings, _items_count):
        load_calls.append(True)
        return [BoxOfficeMovie(rank=1, title="Test Movie")], "The Numbers"

    monkeypatch.setattr(plugin, "_load_movies", fake_load_movies)
    monkeypatch.setattr(plugin, "_enrich_with_tmdb", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(plugin, "_download_posters", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(plugin, "_write_box_office_context", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        plugin,
        "_render_chart",
        lambda *_args, **_kwargs: Image.new("RGB", (800, 480), "white"),
    )

    plugin.generate_image({}, DummyDeviceConfig())
    plugin.generate_image(
        {},
        EnvDeviceConfig({"TMDB_BEARER_TOKEN": "newly-configured"}),
    )

    assert len(load_calls) == 2


def test_force_refresh_aliases_bypass_fresh_china_box_office_cache(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("INKYPI_CHINA_BOX_OFFICE_CACHE", str(tmp_path))
    plugin = ChinaBoxOfficeTopMovies({"id": "china_box_office_top_movies"})
    load_calls = []

    def load_movies(_settings, _items_count):
        load_calls.append(True)
        return [BoxOfficeMovie(rank=1, title="Live Movie")], "The Numbers"

    monkeypatch.setattr(plugin, "_load_movies", load_movies)
    monkeypatch.setattr(plugin, "_enrich_with_tmdb", lambda *_a, **_k: None)
    monkeypatch.setattr(plugin, "_download_posters", lambda *_a, **_k: None)
    monkeypatch.setattr(plugin, "_write_box_office_context", lambda *_a, **_k: None)
    monkeypatch.setattr(
        plugin,
        "_render_chart",
        lambda *_a, **_k: Image.new("RGB", (800, 480), "white"),
    )

    plugin.generate_image({}, DummyDeviceConfig())
    for force_key in ("forceRefresh", "force_refresh"):
        image = plugin.generate_image(
            {force_key: "true"},
            DummyDeviceConfig(),
        )
        assert read_source_provenance(image) is SourceProvenance.LIVE

    assert len(load_calls) == 3


def test_china_sample_fallback_is_local_and_not_persisted(monkeypatch, tmp_path):
    monkeypatch.setenv("INKYPI_CHINA_BOX_OFFICE_CACHE", str(tmp_path))
    plugin = ChinaBoxOfficeTopMovies({"id": "china_box_office_top_movies"})
    context_writes = []
    unavailable = lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("offline"))
    monkeypatch.setattr(plugin, "_load_zgdypw_weekly", unavailable)
    monkeypatch.setattr(plugin, "_load_tmdb_now_playing", unavailable)
    monkeypatch.setattr(plugin, "_load_tmdb_cn_popular", unavailable)
    monkeypatch.setattr(plugin, "_enrich_with_tmdb", lambda *_a, **_k: None)
    monkeypatch.setattr(plugin, "_download_posters", lambda *_a, **_k: None)
    monkeypatch.setattr(
        plugin,
        "_write_box_office_context",
        lambda *args, **kwargs: context_writes.append((args, kwargs)),
    )
    monkeypatch.setattr(
        plugin,
        "_render_chart",
        lambda *_a, **_k: Image.new("RGB", (800, 480), "white"),
    )

    image = plugin.generate_image(
        {"sourceMode": "legacy_auto", "forceRefresh": True},
        DummyDeviceConfig(),
    )

    assert read_source_provenance(image) is SourceProvenance.LOCAL_FALLBACK
    assert image.info["inkypi_skip_cache"] is True
    assert not plugin._cache_path().exists()
    assert context_writes == []


def test_china_box_office_uses_injected_canonical_palette_and_source_only_cache_key():
    plugin = ChinaBoxOfficeTopMovies({"id": "china_box_office_top_movies"})
    day_theme = canonical_theme("day")
    settings = {"themeMode": "cinema", "_inkypi_theme": day_theme}

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


def test_china_box_office_theme_only_opposite_palette_reuses_warm_source_cache(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("INKYPI_CHINA_BOX_OFFICE_CACHE", str(tmp_path))
    plugin = ChinaBoxOfficeTopMovies({"id": "china_box_office_top_movies"})
    calls = {"load": 0, "enrich": 0, "posters": 0}

    def fake_load_movies(_settings, _items_count):
        calls["load"] += 1
        return [BoxOfficeMovie(rank=1, title="Theme Test Movie")], "The Numbers"

    def fake_enrich(*_args, **_kwargs):
        calls["enrich"] += 1

    def fake_download(*_args, **_kwargs):
        calls["posters"] += 1

    monkeypatch.setattr(plugin, "_load_movies", fake_load_movies)
    monkeypatch.setattr(plugin, "_enrich_with_tmdb", fake_enrich)
    monkeypatch.setattr(plugin, "_download_posters", fake_download)
    monkeypatch.setattr(plugin, "_write_box_office_context", lambda *_args: None)

    day = plugin.generate_image(
        {"themeMode": "cinema", "_inkypi_theme": canonical_theme("day")},
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

    assert calls == {"load": 1, "enrich": 1, "posters": 1}
    assert image_digest(day) != image_digest(night)


def test_parse_report_links():
    plugin = ChinaBoxOfficeTopMovies({"id": "china_box_office_top_movies"})
    html = """
    <ul>
      <li><a href="./202606/t20260610_1.shtml">全国电影票房周报（2026.06.01-06.07）</a></li>
      <li><a href="./month.shtml">全国电影票房月报（2026年5月）</a></li>
    </ul>
    """

    links = plugin._parse_report_links(html, "https://www.zgdypw.cn/sc/sjbg/")

    assert links == [
        (
            "全国电影票房周报（2026.06.01-06.07）",
            "https://www.zgdypw.cn/sc/sjbg/202606/t20260610_1.shtml",
        )
    ]


def test_parse_zgdypw_weekly_table():
    plugin = ChinaBoxOfficeTopMovies({"id": "china_box_office_top_movies"})
    html = """
    <table>
      <tr><th>排名</th><th>影片名称</th><th>本周票房</th><th>累计票房</th></tr>
      <tr><td>1</td><td>测试电影一</td><td>3287.4万元</td><td>2.1亿元</td></tr>
      <tr><td>2</td><td>测试电影二</td><td>2110.8万元</td><td>6.8亿元</td></tr>
    </table>
    """

    movies = plugin._parse_zgdypw_report(html, "https://www.zgdypw.cn/report.shtml")

    assert [movie.title for movie in movies] == ["测试电影一", "测试电影二"]
    assert movies[0].rank == 1
    assert movies[0].localized_title == "测试电影一"
    assert movies[0].weekend_gross == "3287.4万元"
    assert movies[0].total_gross == "2.1亿元"


def test_movies_from_tmdb_results():
    plugin = ChinaBoxOfficeTopMovies({"id": "china_box_office_top_movies"})
    movies = plugin._movies_from_tmdb_results(
        [{
            "id": 10,
            "title": "测试电影",
            "original_title": "Original Test",
            "poster_path": "/poster.jpg",
            "release_date": "2026-06-01",
            "popularity": 38.72,
            "overview": "Overview",
        }],
        5,
        "TMDb 热度",
    )

    assert movies[0].rank == 1
    assert movies[0].title == "Original Test"
    assert movies[0].localized_title == "测试电影"
    assert movies[0].weekend_gross == "38.7"
    assert movies[0].poster_url.endswith("/poster.jpg")


def test_default_source_is_north_america_weekly_box_office(monkeypatch):
    plugin = ChinaBoxOfficeTopMovies({"id": "china_box_office_top_movies"})
    captured = {}

    def fake_load_movies(self, settings, items_count):
        captured["settings"] = settings
        captured["items_count"] = items_count
        return [BoxOfficeMovie(rank=1, title="Disclosure Day", weekend_gross="$44,530,925")], "The Numbers"

    monkeypatch.setattr(BoxOfficeTopMovies, "_load_movies", fake_load_movies)

    movies, label = plugin._load_movies({"sourceMode": "tmdb_cn_now_playing"}, 5)

    assert label == "The Numbers"
    assert captured["settings"]["sourceMode"] == "the_numbers"
    assert captured["items_count"] == 5
    assert movies[0].extra["metric_label"] == "本周票房"
    assert movies[0].extra["total_label"] == "累计票房"


def test_north_america_weekly_copy():
    plugin = ChinaBoxOfficeTopMovies({"id": "china_box_office_top_movies"})

    assert plugin._title_for_source("The Numbers") == "北美本周票房榜"
    assert plugin._subtitle_for_source("The Numbers", 5) == "北美本周票房 TOP 5"
    assert plugin._footer_for_source("The Numbers", []) == "Data: The Numbers | Posters pending TMDb"


def test_north_america_enrichment_forces_us_english_poster_settings(monkeypatch):
    plugin = ChinaBoxOfficeTopMovies({"id": "china_box_office_top_movies"})
    captured = {}

    def fake_enrich(self, movies, settings, device_config=None):
        captured.update(settings)

    monkeypatch.setattr(BoxOfficeTopMovies, "_enrich_with_tmdb", fake_enrich)

    plugin._enrich_with_tmdb(
        [BoxOfficeMovie(rank=1, title="Disclosure Day")],
        {"sourceMode": "tmdb_cn_now_playing", "tmdbLanguage": "zh-CN", "tmdbRegion": "CN"},
        DummyDeviceConfig(),
    )

    assert captured["tmdbLanguage"] == "en-US"
    assert captured["tmdbRegion"] == "US"
    assert captured["localizedLanguage"] == "zh-CN"


def test_render_chart_smoke(monkeypatch):
    plugin = ChinaBoxOfficeTopMovies({"id": "china_box_office_top_movies"})
    smoke_dir = Path(__file__).resolve().parents[4] / "tmp" / "china_box_office_render_smoke"
    smoke_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("INKYPI_CHINA_BOX_OFFICE_CACHE", str(smoke_dir))

    poster = smoke_dir / "poster.jpg"
    Image.new("RGB", (300, 450), (170, 36, 43)).save(poster)
    movies = [
        BoxOfficeMovie(rank=1, title="测试电影一", localized_title="测试电影一", weekend_gross="3287.4万", total_gross="2.1亿", poster_path=str(poster), extra={"metric_label": "本周票房", "total_label": "累计"}),
        BoxOfficeMovie(rank=2, title="测试电影二", localized_title="测试电影二", weekend_gross="2110.8万", total_gross="6.8亿", extra={"metric_label": "本周票房", "total_label": "累计"}),
        BoxOfficeMovie(rank=3, title="测试电影三", localized_title="测试电影三", weekend_gross="980.5万", total_gross="1.3亿", extra={"metric_label": "本周票房", "total_label": "累计"}),
        BoxOfficeMovie(rank=4, title="测试电影四", localized_title="测试电影四", weekend_gross="721.2万", total_gross="7212万", extra={"metric_label": "本周票房", "total_label": "累计"}),
        BoxOfficeMovie(rank=5, title="测试电影五", localized_title="测试电影五", weekend_gross="510.0万", total_gross="3.4亿", extra={"metric_label": "本周票房", "total_label": "累计"}),
    ]

    image = plugin._render_chart((800, 480), movies, {"themeMode": "cinema"}, "中国电影数据信息网 2026.06.01-06.07", plugin._now_for_device(DummyDeviceConfig()))

    assert image.size == (800, 480)
    assert image.getbbox() is not None


def test_mainland_placeholder_asset_is_transparent_project_png():
    path = Path(china_box_office_module.CHINA_PLUGIN_DIR) / china_box_office_module.MAINLAND_PLACEHOLDER_FILE

    with Image.open(path) as image:
        image = image.convert("RGBA")
        alpha = image.getchannel("A")

    assert image.size == china_box_office_module.MAINLAND_PLACEHOLDER_SIZE
    assert alpha.getextrema() == (0, 255)
    assert [image.getpixel(point)[3] for point in [(0, 0), (319, 0), (0, 83), (319, 83)]] == [0, 0, 0, 0]


def test_north_america_title_wordmark_asset_is_transparent_project_png():
    path = Path(china_box_office_module.CHINA_PLUGIN_DIR) / china_box_office_module.NORTH_AMERICA_TITLE_WORDMARK_FILE

    with Image.open(path) as image:
        image = image.convert("RGBA")
        alpha = image.getchannel("A")

    assert image.size == china_box_office_module.NORTH_AMERICA_TITLE_WORDMARK_SIZE
    assert alpha.getextrema() == (0, 255)
    w, h = image.size
    assert [image.getpixel(point)[3] for point in [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)]] == [0, 0, 0, 0]


def test_north_america_title_wordmark_is_drawn_into_header():
    plugin = ChinaBoxOfficeTopMovies({"id": "china_box_office_top_movies"})
    image = Image.new("RGB", (800, 480), (18, 20, 22))
    box = plugin._north_america_title_wordmark_box(800, 480, 18)

    assert box == (18, 10, 323, 72)
    plugin._load_north_america_title_wordmark_asset.cache_clear()
    assert plugin._draw_north_america_title_wordmark(image, box) is True

    crop = image.crop((box[0], box[1], box[0] + box[2], box[1] + box[3]))
    colors = crop.getcolors(maxcolors=box[2] * box[3] + 1) or []
    changed_pixels = sum(count for count, color in colors if color != (18, 20, 22))
    changed_x = [
        x
        for x in range(image.width)
        for y in range(image.height)
        if image.getpixel((x, y)) != (18, 20, 22)
    ]
    assert changed_pixels > 1000
    assert min(changed_x) == 18

def test_mainland_placeholder_is_drawn_into_the_right_header_slot():
    plugin = ChinaBoxOfficeTopMovies({"id": "china_box_office_top_movies"})
    image = Image.new("RGB", (800, 480), (18, 20, 22))
    box = plugin._mainland_placeholder_box(800, 480, 18, 78)

    assert box == (434, 86, 320, 84)
    plugin._load_mainland_placeholder_asset.cache_clear()
    plugin._draw_mainland_placeholder(image, box)

    crop = image.crop((box[0], box[1], box[0] + box[2], box[1] + box[3]))
    colors = crop.getcolors(maxcolors=box[2] * box[3] + 1) or []
    changed_pixels = sum(count for count, color in colors if color != (18, 20, 22))
    assert changed_pixels > 1000
