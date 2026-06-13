import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.box_office_top_movies.box_office_top_movies import BoxOfficeMovie  # noqa: E402
from plugins.china_box_office_top_movies.china_box_office_top_movies import (  # noqa: E402
    ChinaBoxOfficeTopMovies,
)


class DummyDeviceConfig:
    def get_resolution(self):
        return (800, 480)

    def get_config(self, _key, default=None):
        return default

    def load_env_key(self, _key):
        return None


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
