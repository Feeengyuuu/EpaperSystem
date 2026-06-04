import sys
import uuid
from datetime import datetime
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.newspaper.newspaper import DEFAULT_MEDIA_SOURCES, Newspaper


TEST_STATE_ROOT = Path(__file__).resolve().parents[4] / ".tmp" / "newspaper_rotation_tests"


class DeviceConfig:
    def get_resolution(self):
        return (800, 480)

    def get_config(self, key, default=None):
        if key == "orientation":
            return "horizontal"
        return default


def make_plugin(name):
    plugin = Newspaper({"id": "newspaper"})
    base = TEST_STATE_ROOT / f"{name}-{uuid.uuid4().hex}"
    base.mkdir(parents=True, exist_ok=True)

    def plugin_dir(path=None):
        return str(base / path) if path else str(base)

    plugin.get_plugin_dir = plugin_dir
    return plugin


def test_parse_media_sources_accepts_urls_and_newspaper_slugs():
    plugin = make_plugin("parse")

    sources = plugin._parse_media_sources(
        """
        BBC News|url|https://www.bbc.com/news
        CNN|https://www.cnn.com
        China Daily|newspaper|chi_cd
        ny_nyt
        """
    )

    assert sources == [
        {
            "id": "url:https://www.bbc.com/news",
            "name": "BBC News",
            "type": "url",
            "value": "https://www.bbc.com/news",
        },
        {
            "id": "url:https://www.cnn.com",
            "name": "CNN",
            "type": "url",
            "value": "https://www.cnn.com",
        },
        {
            "id": "newspaper:CHI_CD",
            "name": "China Daily",
            "type": "newspaper",
            "value": "CHI_CD",
        },
        {
            "id": "newspaper:NY_NYT",
            "name": "NY_NYT",
            "type": "newspaper",
            "value": "NY_NYT",
        },
    ]


def test_parse_media_sources_accepts_luoyang_evening_news_source():
    plugin = make_plugin("parse-lywb")

    sources = plugin._parse_media_sources("Luoyang Evening News|lywb|A01")

    assert sources == [
        {
            "id": "lywb:A01",
            "name": "Luoyang Evening News",
            "type": "lywb",
            "value": "A01",
        }
    ]


def test_default_media_sources_include_luoyang_evening_news():
    plugin = make_plugin("default-lywb")

    sources = plugin._parse_media_sources(DEFAULT_MEDIA_SOURCES)

    assert any(source["id"] == "lywb:A01" for source in sources)


def test_select_next_source_persists_sequential_rotation():
    plugin = make_plugin("sequential")
    sources = plugin._parse_media_sources(
        """
        BBC News|url|https://www.bbc.com/news
        CNN|url|https://www.cnn.com
        Xinhua|url|https://www.xinhuanet.com/
        """
    )

    selected = [plugin._select_next_source(sources)["name"] for _ in range(4)]

    assert selected == ["BBC News", "CNN", "Xinhua", "BBC News"]


def test_rotating_image_skips_failed_source(monkeypatch):
    plugin = make_plugin("skip_failed")
    sources = plugin._parse_media_sources(
        """
        Broken|url|https://example.invalid
        Working|newspaper|chi_cd
        """
    )
    expected = Image.new("RGB", (10, 10), "white")

    def fake_fetch_source_image(source, device_config):
        if source["name"] == "Broken":
            return None
        return expected

    monkeypatch.setattr(plugin, "_fetch_source_image", fake_fetch_source_image)

    image = plugin._generate_rotating_image(sources, DeviceConfig())

    assert image is expected
    assert plugin._select_next_source(sources)["name"] == "Broken"


def test_url_source_returns_none_when_screenshot_fails(monkeypatch):
    plugin = make_plugin("url-no-fallback")
    source = plugin._parse_media_sources("BBC News|url|https://www.bbc.com/news")[0]

    monkeypatch.setattr(plugin, "_fetch_url_screenshot", lambda url, device_config: None)

    image = plugin._fetch_source_image(source, DeviceConfig())

    assert image is None


def test_luoyang_evening_news_builds_a01_pdf_url():
    plugin = make_plugin("lywb-url")

    url = plugin._build_lywb_pdf_url(datetime(2026, 6, 3))

    assert url == (
        "https://lywb.lyd.com.cn/images2/2/2026-06/03/"
        "A01/20260603A01_pdf.pdf"
    )


def test_luoyang_evening_news_fetches_pdf_front_page(monkeypatch):
    plugin = make_plugin("lywb-fetch")
    raw_page = Image.new("RGB", (700, 1000), "white")
    requested_urls = []

    monkeypatch.setattr(
        plugin,
        "_lywb_candidate_dates",
        lambda: [datetime(2026, 6, 3)],
    )
    monkeypatch.setattr(
        plugin,
        "_download_pdf",
        lambda url: requested_urls.append(url) or b"%PDF-1.7 fake",
    )
    monkeypatch.setattr(plugin, "_render_pdf_first_page", lambda pdf_bytes: raw_page)

    image = plugin._fetch_luoyang_evening_news_cover(DeviceConfig())

    assert requested_urls == [
        "https://lywb.lyd.com.cn/images2/2/2026-06/03/"
        "A01/20260603A01_pdf.pdf"
    ]
    assert image.size == raw_page.size
    assert image.mode == "RGB"


def test_render_pdf_first_page_returns_rgb_image():
    fitz = pytest.importorskip("fitz")
    plugin = make_plugin("lywb-render")
    document = fitz.open()
    page = document.new_page(width=100, height=120)
    page.insert_text((12, 24), "A01")
    pdf_bytes = document.tobytes()
    document.close()

    image = plugin._render_pdf_first_page(pdf_bytes)

    assert image.size == (200, 240)
    assert image.mode == "RGB"


def test_extract_headlines_from_frontpage_html_normalizes_simplified_chinese():
    plugin = make_plugin("extract-html")
    traditional_headline = (
        "\u570b\u969b\u65b0\u805e\u767c\u4f48"
        "\u6700\u65b0\u7d93\u6fdf\u89c0\u5bdf\u5831\u544a"
    )

    headlines = plugin._extract_headlines(
        f"""
        <html><body>
          <nav><a>Sign in</a><a>Weather</a></nav>
          <h1>China and US officials open new round of trade talks</h1>
          <a href="/story">{traditional_headline}</a>
          <script><a>Hidden fake headline should not appear</a></script>
        </body></html>
        """
    )

    assert headlines == [
        "China and US officials open new round of trade talks",
        "\u56fd\u9645\u65b0\u95fb\u53d1\u5e03"
        "\u6700\u65b0\u7ecf\u6d4e\u89c2\u5bdf\u62a5\u544a",
    ]


def test_clean_html_text_repairs_common_chinese_mojibake():
    plugin = make_plugin("mojibake")
    expected = "\u65b0\u534e\u793e\u53d1\u5e03\u6700\u65b0\u7ecf\u6d4e\u89c2\u5bdf\u62a5\u544a"
    mojibake = expected.encode("utf-8").decode("latin1")

    assert plugin._clean_html_text(mojibake) == expected


def test_render_headlines_page_supports_simplified_chinese_text():
    plugin = make_plugin("render-cn")
    source = plugin._parse_media_sources("Xinhua|url|https://www.xinhuanet.com/")[0]
    headlines = [
        "\u65b0\u534e\u793e\u53d1\u5e03\u6700\u65b0\u7ecf\u6d4e\u89c2\u5bdf\u62a5\u544a",
        "\u591a\u5730\u63a8\u51fa\u4fbf\u6c11\u670d\u52a1\u65b0\u4e3e\u63aa",
    ]

    image = plugin._render_headlines_page(source, headlines, DeviceConfig())

    assert image.size == (800, 480)
    assert image.mode == "RGB"
