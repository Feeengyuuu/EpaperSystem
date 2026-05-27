import sys
import uuid
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.backtothedate.backtothedate import BacktotheDate


TEST_STATE_ROOT = Path(__file__).resolve().parents[4] / ".tmp" / "backtothedate_tests"


class DeviceConfig:
    def get_resolution(self):
        return (800, 480)

    def get_config(self, key, default=None):
        if key == "orientation":
            return "horizontal"
        return default


class FakeImageLoader:
    def __init__(self, image):
        self.images = image if isinstance(image, list) else [image]
        self.calls = []

    def from_url(self, url, dimensions, timeout_ms=40000, resize=True, headers=None):
        self.calls.append({
            "url": url,
            "dimensions": dimensions,
            "timeout_ms": timeout_ms,
            "resize": resize,
            "headers": headers,
        })
        index = min(len(self.calls) - 1, len(self.images) - 1)
        return self.images[index].copy()


def make_plugin(name):
    plugin = BacktotheDate({"id": "backtothedate"})
    base = TEST_STATE_ROOT / f"{name}-{uuid.uuid4().hex}"
    base.mkdir(parents=True, exist_ok=True)

    def plugin_dir(path=None):
        return str(base / path) if path else str(base)

    plugin.get_plugin_dir = plugin_dir
    return plugin


def test_extract_poster_links_deduplicates_image_and_text_links():
    plugin = make_plugin("links")
    html = """
    <a href="/posters/d12-729"><img src="/thumb.jpg" alt=""></a>
    <a href="/posters/d12-729">An order given to the Shanghai Municipal Police</a>
    <a href="/posters/posters">Posters</a>
    <a href="/about/faqs">FAQ</a>
    """

    links = plugin._extract_poster_links(html)

    assert links == [
        {
            "url": "https://chineseposters.net/posters/d12-729",
            "title": "An order given to the Shanghai Municipal Police",
        }
    ]


def test_extract_poster_data_finds_direct_image_url_and_title():
    plugin = make_plugin("detail")
    html = """
    <h1>An order given to the Shanghai Municipal Police: Shoot to kill</h1>
    <img src="/sites/default/files/images/d12-729.jpg" alt="Poster image">
    """

    poster = plugin._extract_poster_data(html, "https://chineseposters.net/posters/d12-729")

    assert poster == {
        "page_url": "https://chineseposters.net/posters/d12-729",
        "image_url": "https://chineseposters.net/sites/default/files/images/d12-729.jpg",
        "title": "An order given to the Shanghai Municipal Police: Shoot to kill",
    }


def test_discover_max_page_from_pagination_links():
    plugin = make_plugin("pages")

    assert plugin._discover_max_page('<a href="?page=1">2</a><a href="?page=141">last</a>') == 141


def test_generate_image_rotates_portrait_poster_by_default(monkeypatch):
    plugin = make_plugin("generate")
    loader = FakeImageLoader(Image.new("RGB", (200, 400), (220, 0, 0)))
    plugin.image_loader = loader
    rendered_sizes = []

    monkeypatch.setattr(
        plugin,
        "_select_random_poster",
        lambda settings: {
            "page_url": "https://chineseposters.net/posters/one",
            "image_url": "https://chineseposters.net/sites/default/files/images/one.jpg",
            "title": "One",
        },
    )
    monkeypatch.setattr(
        plugin,
        "_fit_blur_contain",
        lambda image, dimensions, settings, max_width_ratio=1.0: (
            rendered_sizes.append(image.size),
            Image.new("RGB", dimensions, "white"),
        )[1],
    )

    image = plugin.generate_image({}, DeviceConfig())

    assert image.size == (800, 480)
    assert image.mode == "RGB"
    assert len(loader.calls) == 1
    assert rendered_sizes == [(400, 200)]
    assert loader.calls[0]["resize"] is False


def test_generate_image_keeps_landscape_poster_orientation(monkeypatch):
    plugin = make_plugin("generate-landscape")
    source = Image.new("RGB", (500, 260), (20, 120, 220))
    loader = FakeImageLoader(source)
    plugin.image_loader = loader
    rendered_sizes = []

    monkeypatch.setattr(
        plugin,
        "_select_random_poster",
        lambda settings: {
            "page_url": "https://chineseposters.net/posters/landscape",
            "image_url": "https://chineseposters.net/sites/default/files/images/landscape.jpg",
            "title": "Landscape",
        },
    )
    monkeypatch.setattr(
        plugin,
        "_fit_blur_contain",
        lambda image, dimensions, settings, max_width_ratio=1.0: (
            rendered_sizes.append(image.size),
            Image.new("RGB", dimensions, "white"),
        )[1],
    )

    image = plugin.generate_image({}, DeviceConfig())

    assert image.size == (800, 480)
    assert image.mode == "RGB"
    assert len(loader.calls) == 1
    assert rendered_sizes == [(500, 260)]


def test_blur_contain_preserves_complete_landscape_poster():
    plugin = make_plugin("blur-contain")
    source = Image.new("RGB", (100, 50), (220, 0, 0))
    draw = ImageDraw.Draw(source)
    draw.rectangle((0, 0, 99, 49), outline=(0, 0, 0), width=2)

    image = plugin._fit_blur_contain(source, (800, 480), {"backgroundColor": "white"})

    assert image.size == (800, 480)
    # A 2:1 source fits as 800x400 on an 800x480 screen, so the black top
    # border should remain visible at the first clear-image row.
    assert max(image.getpixel((400, 40))) < 16


def test_generate_image_can_preserve_plain_full_poster(monkeypatch):
    plugin = make_plugin("generate-contain")
    source = Image.new("RGB", (200, 400), (220, 0, 0))
    loader = FakeImageLoader(source)
    plugin.image_loader = loader

    monkeypatch.setattr(
        plugin,
        "_select_random_poster",
        lambda settings: {
            "page_url": "https://chineseposters.net/posters/d12-729",
            "image_url": "https://chineseposters.net/sites/default/files/images/d12-729.jpg",
            "title": "Poster",
        },
    )

    image = plugin.generate_image({"fitMode": "contain"}, DeviceConfig())

    assert image.size == (800, 480)
    assert image.mode == "RGB"
    assert image.getpixel((0, 0)) == (255, 255, 255)
