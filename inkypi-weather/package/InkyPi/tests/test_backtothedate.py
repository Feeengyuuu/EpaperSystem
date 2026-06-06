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


def test_source_theme_urls_default_to_target_mao_era_themes():
    plugin = make_plugin("source-themes")

    urls = plugin._source_theme_urls({})

    assert "https://chineseposters.net/themes/great-leap-forward" in urls
    assert "https://chineseposters.net/themes/cultural-revolution-campaigns" in urls
    assert "https://chineseposters.net/themes/shanghai-commune" in urls
    assert "https://chineseposters.net/themes/shanghai-peoples-commune" not in urls
    assert urls


def test_select_random_poster_prefers_target_theme_sources(monkeypatch):
    plugin = make_plugin("select-theme")
    list_html = """
    <a href="/posters/seen">Seen poster</a>
    <a href="/posters/new">New Cultural Revolution poster</a>
    """
    detail_html = {
        "https://chineseposters.net/posters/seen": """
            <h1>Seen poster</h1>
            <img src="/sites/default/files/images/seen.jpg">
        """,
        "https://chineseposters.net/posters/new": """
            <h1>New Cultural Revolution poster</h1>
            <img src="/sites/default/files/images/new.jpg">
        """,
    }
    fetched_urls = []

    plugin._write_state({
        "discarded_page_urls": ["https://chineseposters.net/posters/seen"],
    })

    def fake_fetch(url, params=None):
        fetched_urls.append(url)
        if "/themes/" in url:
            return list_html
        return detail_html[url]

    monkeypatch.setattr(plugin, "_fetch_text", fake_fetch)
    monkeypatch.setattr("plugins.backtothedate.backtothedate.random.shuffle", lambda items: None)

    poster = plugin._select_random_poster({})

    assert poster["page_url"] == "https://chineseposters.net/posters/new"
    assert poster["image_url"] == "https://chineseposters.net/sites/default/files/images/new.jpg"
    assert fetched_urls[0] == "https://chineseposters.net/themes/great-leap-forward"
    assert "https://chineseposters.net/posters/posters" not in fetched_urls


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

    image = plugin.generate_image({"fitMode": "rotate_portrait"}, DeviceConfig())

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
        "_fit_plain_contain",
        lambda image, dimensions, settings: (
            rendered_sizes.append(image.size),
            Image.new("RGB", dimensions, "white"),
        )[1],
    )

    image = plugin.generate_image({"fitMode": "landscape"}, DeviceConfig())

    assert image.size == (800, 480)
    assert image.mode == "RGB"
    assert len(loader.calls) == 1
    assert rendered_sizes == [(500, 260)]


def test_generate_image_triptych_loads_three_posters_and_remembers_all(monkeypatch):
    plugin = make_plugin("generate-triptych")
    loader = FakeImageLoader([
        Image.new("RGB", (200, 400), (220, 0, 0)),
        Image.new("RGB", (210, 400), (0, 160, 0)),
        Image.new("RGB", (220, 400), (0, 0, 220)),
    ])
    plugin.image_loader = loader
    posters = iter([
        {
            "page_url": "https://chineseposters.net/posters/one",
            "image_url": "https://chineseposters.net/sites/default/files/images/one.jpg",
            "title": "One",
        },
        {
            "page_url": "https://chineseposters.net/posters/two",
            "image_url": "https://chineseposters.net/sites/default/files/images/two.jpg",
            "title": "Two",
        },
        {
            "page_url": "https://chineseposters.net/posters/three",
            "image_url": "https://chineseposters.net/sites/default/files/images/three.jpg",
            "title": "Three",
        },
    ])

    monkeypatch.setattr(plugin, "_select_random_poster", lambda settings: next(posters))

    image = plugin.generate_image({"fitMode": "triptych", "attempts": 3}, DeviceConfig())
    state = plugin._read_state()

    assert image.size == (800, 480)
    assert image.mode == "RGB"
    assert len(loader.calls) == 3
    assert state["last_page_urls"] == [
        "https://chineseposters.net/posters/one",
        "https://chineseposters.net/posters/two",
        "https://chineseposters.net/posters/three",
    ]


def test_generate_image_triptych_displays_landscape_poster_as_single_full_image(monkeypatch):
    plugin = make_plugin("triptych-landscape-single")
    source = Image.new("RGB", (500, 260), (20, 120, 220))
    loader = FakeImageLoader(source)
    plugin.image_loader = loader
    rendered_sizes = []

    poster = {
        "page_url": "https://chineseposters.net/posters/landscape",
        "image_url": "https://chineseposters.net/sites/default/files/images/landscape.jpg",
        "title": "Landscape",
    }

    def fake_fit_landscape(image, dimensions, settings):
        rendered_sizes.append(image.size)
        return Image.new("RGB", dimensions, (12, 34, 56))

    monkeypatch.setattr(plugin, "_select_random_poster", lambda settings: poster)
    monkeypatch.setattr(plugin, "_fit_landscape", fake_fit_landscape)

    image = plugin.generate_image({"fitMode": "triptych", "attempts": 3}, DeviceConfig())
    state = plugin._read_state()

    assert image.size == (800, 480)
    assert image.getpixel((0, 0)) == (12, 34, 56)
    assert rendered_sizes == [(500, 260)]
    assert len(loader.calls) == 1
    assert state["last_page_urls"] == ["https://chineseposters.net/posters/landscape"]


def test_generate_image_forced_landscape_preview_uses_single_full_image(monkeypatch):
    plugin = make_plugin("forced-landscape-preview")
    source = Image.new("RGB", (500, 260), (20, 120, 220))
    loader = FakeImageLoader(source)
    plugin.image_loader = loader
    rendered_sizes = []

    def fake_fit_landscape(image, dimensions, settings):
        rendered_sizes.append(image.size)
        return Image.new("RGB", dimensions, (12, 34, 56))

    monkeypatch.setattr(plugin, "_fit_landscape", fake_fit_landscape)

    image = plugin.generate_image(
        {
            "fitMode": "triptych",
            "posterImageUrl": "https://chineseposters.net/sites/default/files/images/landscape.jpg",
            "posterPageUrl": "https://chineseposters.net/posters/landscape",
            "posterTitle": "Landscape",
        },
        DeviceConfig(),
    )
    state = plugin._read_state()

    assert image.size == (800, 480)
    assert image.getpixel((0, 0)) == (12, 34, 56)
    assert rendered_sizes == [(500, 260)]
    assert len(loader.calls) == 1
    assert state["last_page_urls"] == ["https://chineseposters.net/posters/landscape"]


def test_generate_image_triptych_does_not_use_landscape_as_fallback_column(monkeypatch):
    plugin = make_plugin("triptych-landscape-not-column")
    loader = FakeImageLoader([
        Image.new("RGB", (200, 400), (220, 0, 0)),
        Image.new("RGB", (210, 400), (0, 160, 0)),
        Image.new("RGB", (500, 260), (20, 120, 220)),
    ])
    plugin.image_loader = loader
    rendered_sizes = []
    landscape = {
        "page_url": "https://chineseposters.net/posters/landscape",
        "image_url": "https://chineseposters.net/sites/default/files/images/landscape.jpg",
        "title": "Landscape",
    }
    posters = [
        {
            "page_url": "https://chineseposters.net/posters/one",
            "image_url": "https://chineseposters.net/sites/default/files/images/one.jpg",
            "title": "One",
        },
        {
            "page_url": "https://chineseposters.net/posters/two",
            "image_url": "https://chineseposters.net/sites/default/files/images/two.jpg",
            "title": "Two",
        },
        landscape,
    ]

    def fake_select(_settings):
        if posters:
            return posters.pop(0)
        return landscape

    def fake_fit_landscape(image, dimensions, settings):
        rendered_sizes.append(image.size)
        return Image.new("RGB", dimensions, (12, 34, 56))

    def fail_triptych(_poster_images, _dimensions, _settings):
        raise AssertionError("Landscape posters must not be placed in triptych columns")

    monkeypatch.setattr(plugin, "_select_random_poster", fake_select)
    monkeypatch.setattr(plugin, "_fit_landscape", fake_fit_landscape)
    monkeypatch.setattr(plugin, "_compose_triptych_display_image", fail_triptych)

    image = plugin.generate_image({"fitMode": "triptych", "attempts": 1}, DeviceConfig())
    state = plugin._read_state()

    assert image.size == (800, 480)
    assert image.getpixel((0, 0)) == (12, 34, 56)
    assert rendered_sizes == [(500, 260)]
    assert len(loader.calls) == 3
    assert state["last_page_urls"] == ["https://chineseposters.net/posters/landscape"]


def test_landscape_mode_uses_plain_full_image_without_blur_backdrop():
    plugin = make_plugin("landscape-plain")
    source = Image.new("RGB", (100, 50), (220, 0, 0))
    draw = ImageDraw.Draw(source)
    draw.rectangle((0, 0, 99, 49), outline=(0, 0, 0), width=2)

    image = plugin._fit_landscape(source, (800, 480), {"backgroundColor": "white"})

    assert image.size == (800, 480)
    assert image.getpixel((400, 0)) == (255, 255, 255)
    assert max(image.getpixel((400, 40))) < 16


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


def test_remember_success_adds_posters_to_discard_pools():
    plugin = make_plugin("discard-pool")
    plugin._write_state({
        "discarded_page_urls": ["https://chineseposters.net/posters/old/"],
        "discarded_image_urls": ["https://chineseposters.net/sites/default/files/images/old.jpg?download=1"],
        "last_page_url": "https://chineseposters.net/posters/legacy",
        "last_image_url": "https://chineseposters.net/sites/default/files/images/legacy.jpg",
    })

    plugin._remember_success([
        {
            "page_url": "https://chineseposters.net/posters/old",
            "image_url": "https://chineseposters.net/sites/default/files/images/old.jpg",
            "title": "Old",
        },
        {
            "page_url": "https://chineseposters.net/posters/new",
            "image_url": "https://chineseposters.net/sites/default/files/images/new.jpg",
            "title": "New",
        },
    ])

    state = plugin._read_state()
    assert state["discarded_page_urls"] == [
        "https://chineseposters.net/posters/old/",
        "https://chineseposters.net/posters/legacy",
        "https://chineseposters.net/posters/new",
    ]
    assert state["discarded_image_urls"] == [
        "https://chineseposters.net/sites/default/files/images/old.jpg?download=1",
        "https://chineseposters.net/sites/default/files/images/legacy.jpg",
        "https://chineseposters.net/sites/default/files/images/new.jpg",
    ]
    assert state["last_page_urls"] == [
        "https://chineseposters.net/posters/old",
        "https://chineseposters.net/posters/new",
    ]


def test_select_random_poster_skips_discarded_page_and_image_urls(monkeypatch):
    plugin = make_plugin("select-unseen")
    plugin._write_state({
        "discarded_page_urls": ["https://chineseposters.net/posters/seen-page"],
        "discarded_image_urls": ["https://chineseposters.net/sites/default/files/images/seen-image.jpg"],
    })

    list_html = """
    <a href="/posters/seen-page">Seen page</a>
    <a href="/posters/image-seen">Seen image</a>
    <a href="/posters/new">New poster</a>
    """
    detail_html = {
        "https://chineseposters.net/posters/image-seen": """
            <h1>Seen image</h1>
            <img src="/sites/default/files/images/seen-image.jpg">
        """,
        "https://chineseposters.net/posters/new": """
            <h1>New poster</h1>
            <img src="/sites/default/files/images/new.jpg">
        """,
    }
    fetched_urls = []

    def fake_fetch(url, params=None):
        fetched_urls.append(url)
        if url.endswith("/posters/posters"):
            return list_html
        return detail_html[url]

    monkeypatch.setattr(plugin, "_fetch_text", fake_fetch)
    monkeypatch.setattr("plugins.backtothedate.backtothedate.random.randint", lambda low, high: 0)
    monkeypatch.setattr("plugins.backtothedate.backtothedate.random.shuffle", lambda items: None)

    poster = plugin._select_random_poster({"maxPage": 0, "sourceMode": "all_archive"})

    assert poster["page_url"] == "https://chineseposters.net/posters/new"
    assert poster["image_url"] == "https://chineseposters.net/sites/default/files/images/new.jpg"
    assert "https://chineseposters.net/posters/seen-page" not in fetched_urls


def test_select_random_poster_can_fallback_when_only_seen_posters_exist(monkeypatch):
    plugin = make_plugin("select-seen-fallback")
    plugin._write_state({
        "discarded_page_urls": ["https://chineseposters.net/posters/seen"],
        "discarded_image_urls": ["https://chineseposters.net/sites/default/files/images/seen.jpg"],
    })

    def fake_fetch(url, params=None):
        if url.endswith("/posters/posters"):
            return '<a href="/posters/seen">Seen poster</a>'
        return '<h1>Seen poster</h1><img src="/sites/default/files/images/seen.jpg">'

    monkeypatch.setattr(plugin, "_fetch_text", fake_fetch)
    monkeypatch.setattr("plugins.backtothedate.backtothedate.random.randint", lambda low, high: 0)
    monkeypatch.setattr("plugins.backtothedate.backtothedate.random.shuffle", lambda items: None)
    monkeypatch.setattr("plugins.backtothedate.backtothedate.random.choice", lambda items: items[0])

    poster = plugin._select_random_poster({"maxPage": 0, "sourceMode": "all_archive"})

    assert poster["page_url"] == "https://chineseposters.net/posters/seen"
    assert poster["image_url"] == "https://chineseposters.net/sites/default/files/images/seen.jpg"
