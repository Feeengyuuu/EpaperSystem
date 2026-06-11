import sys
import random
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.magazine_covers.magazine_covers import (  # noqa: E402
    CORE_DEFAULT_SOURCES,
    DAILY_LIBRARY_REFRESH_INTERVAL,
    DEFAULT_SOURCES,
    MAX_PI_SAFE_SOURCE_PIXELS,
    MagazineCovers,
    _ImageCandidateParser,
)


TEST_TMP_ROOT = Path(__file__).resolve().parents[4] / ".tmp" / "magazine_covers_tests"


def make_test_tmp_dir(name):
    path = TEST_TMP_ROOT / f"{name}-{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def assert_near_color(pixel, expected, tolerance=3):
    assert max(abs(pixel[index] - expected[index]) for index in range(3)) <= tolerance


class RecordingLoader:
    def __init__(self):
        self.loaded_paths = []
        self.loaded_sizes = []
        self.resize_flags = []

    def from_file(self, path, dimensions, resize=True, focus_crop=False):
        self.loaded_paths.append(Path(path))
        self.resize_flags.append(resize)
        with Image.open(path) as image:
            self.loaded_sizes.append(image.size)
            if not resize:
                return image.copy().convert("RGB")
        return Image.new("RGB", dimensions, "white")


class DummyDeviceConfig:
    def __init__(self, resolution=(800, 480)):
        self.resolution = resolution

    def get_resolution(self):
        return self.resolution

    def get_config(self, _key, default=None):
        return default


def test_srcset_candidates_are_ordered_small_to_large():
    parser = _ImageCandidateParser("https://example.com/current")

    assert parser._srcset_urls(
        "large.jpg 2400w, small.jpg 600w, medium.jpg 1200w"
    ) == ["small.jpg", "medium.jpg", "large.jpg"]


def test_default_source_pool_has_fresh_collection_sources():
    plugin = MagazineCovers({"id": "magazine_covers"})
    sources = plugin._parse_sources(DEFAULT_SOURCES)
    source_ids = {plugin._source_id(source) for source in sources}

    assert len(sources) >= 30
    assert "Newest Releases Page 2|https://magazineshop.us/collections/new-releases?page=2" in source_ids
    assert "Newsweek|https://magazineshop.us/collections/newsweek" in source_ids
    assert "Athlon Sports|https://magazineshop.us/collections/athlon-sports" in source_ids


def test_legacy_saved_default_sources_are_expanded_with_new_pool():
    plugin = MagazineCovers({"id": "magazine_covers"})

    sources = plugin._sources_from_settings({"sources": CORE_DEFAULT_SOURCES})

    assert len(sources) == len(plugin._parse_sources(DEFAULT_SOURCES))
    assert sources[0]["name"] == "TIME"
    assert sources[-1]["name"] == "Politics"


def test_custom_saved_sources_are_not_expanded_with_defaults():
    plugin = MagazineCovers({"id": "magazine_covers"})

    sources = plugin._sources_from_settings({"sources": "Custom|https://example.com/current"})

    assert sources == [{"name": "Custom", "url": "https://example.com/current"}]


def test_default_daily_library_refresh_interval_is_six_hours():
    plugin = MagazineCovers({"id": "magazine_covers"})

    assert DAILY_LIBRARY_REFRESH_INTERVAL == timedelta(hours=6)
    assert plugin._daily_library_refresh_interval({}) == timedelta(hours=6)
    assert plugin._daily_library_refresh_interval({"libraryRefreshHours": "12"}) == timedelta(hours=6)
    assert plugin._daily_library_refresh_interval({"libraryRefreshHours": "23"}) == timedelta(hours=23)


def test_oversized_candidate_is_downsampled_before_loader(monkeypatch):
    source_path = make_test_tmp_dir("oversized") / "large-cover.jpg"
    Image.new("RGB", (1400, 1400), "black").save(source_path)

    plugin = MagazineCovers({"id": "magazine_covers"})
    loader = RecordingLoader()
    plugin.image_loader = loader
    monkeypatch.setattr(plugin, "_download_candidate_to_temp", lambda _url: source_path)

    image = plugin._download_candidate_image(
        {"url": "https://example.com/large-cover.jpg"},
        (800, 480),
    )

    assert image.size == loader.loaded_sizes[0]
    assert loader.resize_flags == [False]
    assert loader.loaded_sizes
    assert loader.loaded_sizes[0] != (1400, 1400)
    assert loader.loaded_sizes[0][0] * loader.loaded_sizes[0][1] <= MAX_PI_SAFE_SOURCE_PIXELS


def test_oversized_webp_candidate_is_skipped_without_downsample(monkeypatch):
    source_path = make_test_tmp_dir("oversized-webp") / "large-cover.webp"
    source_path.write_bytes(b"not really decoded in this test")

    plugin = MagazineCovers({"id": "magazine_covers"})
    loader = RecordingLoader()
    plugin.image_loader = loader
    monkeypatch.setattr(plugin, "_download_candidate_to_temp", lambda _url: source_path)
    monkeypatch.setattr(
        plugin,
        "_source_image_info",
        lambda _path: {
            "width": 2268,
            "height": 2858,
            "pixels": 2268 * 2858,
            "format": "WEBP",
        },
    )
    monkeypatch.setattr(
        plugin,
        "_downsample_to_pi_safe_image",
        lambda _path: (_ for _ in ()).throw(AssertionError("WebP should not be downsampled")),
    )

    try:
        plugin._download_candidate_image(
            {"url": "https://example.com/large-cover.webp"},
            (800, 480),
        )
    except RuntimeError as exc:
        assert "WebP" in str(exc)
    else:
        raise AssertionError("Expected oversized WebP source to be skipped")

    assert loader.loaded_paths == []


def test_pi_safe_candidate_uses_original_download(monkeypatch):
    source_path = make_test_tmp_dir("pi-safe") / "small-cover.jpg"
    Image.new("RGB", (600, 800), "white").save(source_path)

    plugin = MagazineCovers({"id": "magazine_covers"})
    loader = RecordingLoader()
    plugin.image_loader = loader
    monkeypatch.setattr(plugin, "_download_candidate_to_temp", lambda _url: source_path)

    plugin._download_candidate_image(
        {"url": "https://example.com/small-cover.jpg"},
        (800, 480),
    )

    assert loader.loaded_paths == [source_path]
    assert loader.loaded_sizes == [(600, 800)]
    assert loader.resize_flags == [False]


def test_random_order_retries_other_sources_when_queue_has_one_failed_source(monkeypatch):
    sources = [
        {"name": "TIME", "url": "https://example.com/time"},
        {"name": "WIRED Japan", "url": "https://example.com/wired"},
        {"name": "Billboard", "url": "https://example.com/billboard"},
    ]
    plugin = MagazineCovers({"id": "magazine_covers"})
    monkeypatch.setattr(
        plugin,
        "_read_state",
        lambda: {"random_queue": ["WIRED Japan|https://example.com/wired"]},
    )
    monkeypatch.setattr(plugin, "_write_state", lambda _state: None)

    ordered = plugin._random_order(sources)

    assert ordered[0]["name"] == "WIRED Japan"
    assert {source["name"] for source in ordered[1:]} == {"TIME", "Billboard"}


def test_random_failure_removes_source_from_queue(monkeypatch):
    state = {
        "random_queue": [
            "WIRED Japan|https://example.com/wired",
            "TIME|https://example.com/time",
        ]
    }
    writes = []
    plugin = MagazineCovers({"id": "magazine_covers"})
    monkeypatch.setattr(plugin, "_read_state", lambda: dict(state))
    monkeypatch.setattr(plugin, "_write_state", lambda next_state: writes.append(next_state))

    plugin._remember_failure({"name": "WIRED Japan", "url": "https://example.com/wired"})

    assert writes[-1]["random_queue"] == ["TIME|https://example.com/time"]


def test_cover_crop_preserves_top_masthead_area():
    plugin = MagazineCovers({"id": "magazine_covers"})
    source = Image.new("RGB", (800, 1600), "white")
    draw = ImageDraw.Draw(source)
    draw.rectangle((0, 0, 800, 180), fill="black")
    draw.rectangle((0, 1180, 800, 1600), fill="gray")

    fitted = plugin._fit_cover(
        source,
        (800, 480),
        {"fitMode": "cover", "showSourceLabel": "false"},
        {"name": "Masthead"},
    )

    top_band = fitted.crop((0, 0, 800, 140)).convert("L")
    bottom_band = fitted.crop((0, 340, 800, 480)).convert("L")
    assert sum(top_band.histogram()[:32]) > 80000
    assert sum(bottom_band.histogram()[:32]) == 0


def test_cover_crop_uses_detected_title_band_as_crop_rule():
    plugin = MagazineCovers({"id": "magazine_covers"})
    source = Image.new("RGB", (800, 1600), "white")
    draw = ImageDraw.Draw(source)
    draw.rectangle((0, 0, 800, 260), fill=(245, 245, 245))
    draw.rectangle((0, 540, 800, 700), fill="black")
    draw.rectangle((120, 575, 680, 665), fill="white")
    draw.rectangle((0, 1180, 800, 1600), fill="gray")

    fitted = plugin._fit_cover(
        source,
        (800, 480),
        {"fitMode": "cover", "showSourceLabel": "false"},
        {"name": "Detected Title"},
    )

    upper_band = fitted.crop((0, 0, 800, 190)).convert("L")
    lower_band = fitted.crop((0, 330, 800, 480)).convert("L")
    assert sum(upper_band.histogram()[:32]) > 70000
    assert sum(lower_band.histogram()[:32]) == 0


def test_source_label_adds_publication_context():
    plugin = MagazineCovers({"id": "magazine_covers"})
    source = Image.new("RGB", (800, 480), "white")

    fitted = plugin._fit_cover(
        source,
        (800, 480),
        {"fitMode": "contain"},
        {"name": "Variety"},
    )

    label_area = fitted.crop((0, 380, 260, 480)).convert("L")
    assert sum(label_area.histogram()[:32]) > 100


def test_contain_mode_uses_plain_background_without_blur():
    plugin = MagazineCovers({"id": "magazine_covers"})
    source = Image.new("RGB", (100, 50), (220, 0, 0))
    draw = ImageDraw.Draw(source)
    draw.rectangle((0, 0, 99, 49), outline=(0, 0, 0), width=2)

    fitted = plugin._fit_cover(
        source,
        (800, 480),
        {"fitMode": "contain", "backgroundStyle": "blur", "showSourceLabel": "false"},
        {"name": "Plain"},
    )

    assert fitted.getpixel((400, 0)) == (255, 255, 255)
    assert max(fitted.getpixel((400, 40))) < 16


def test_triptych_mode_loads_three_direct_sources_and_remembers_all(monkeypatch):
    plugin = MagazineCovers({"id": "magazine_covers"})
    sources = "Alpha|https://example.com/a\nBeta|https://example.com/b\nGamma|https://example.com/c"
    colors = {
        "Alpha": (255, 0, 0),
        "Beta": (0, 255, 0),
        "Gamma": (0, 0, 255),
    }
    calls = []

    def fake_load_cover(source, dimensions, force_refresh=False):
        calls.append((source["name"], force_refresh))
        return {
            "image": Image.new("RGB", (200, 400), colors[source["name"]]),
            "image_url": f"https://example.com/{source['name'].lower()}.jpg",
            "page_url": source["url"],
            "title": source["name"],
        }

    monkeypatch.setattr(plugin, "_load_cover", fake_load_cover)

    image = plugin.generate_image(
        {
            "sources": sources,
            "rotationMode": "rotate",
            "fitMode": "triptych",
            "showSourceLabel": "false",
            "dailyLibraryMode": "false",
        },
        DummyDeviceConfig(),
    )
    state = plugin._read_state()

    assert image.size == (800, 480)
    assert {name for name, _force_refresh in calls} == {"Alpha", "Beta", "Gamma"}
    assert all(force_refresh is False for _name, force_refresh in calls)
    assert image.getpixel((133, 240)) == colors[calls[0][0]]
    assert image.getpixel((400, 240)) == colors[calls[1][0]]
    assert image.getpixel((666, 240)) == colors[calls[2][0]]
    assert set(state["last_source_ids"]) == {
        "Alpha|https://example.com/a",
        "Beta|https://example.com/b",
        "Gamma|https://example.com/c",
    }


def test_daily_library_triptych_uses_three_cached_covers(monkeypatch):
    plugin = MagazineCovers({"id": "magazine_covers"})
    monkeypatch.setenv("INKYPI_MAGAZINE_COVERS_CACHE", str(make_test_tmp_dir("triptych-cache")))
    monkeypatch.setattr(
        plugin,
        "_now_utc",
        lambda: datetime(2026, 6, 1, 7, 0, tzinfo=timezone.utc),
    )
    colors = {
        "Alpha": (255, 0, 0),
        "Beta": (0, 255, 0),
        "Gamma": (0, 0, 255),
    }
    calls = []

    def fake_load_cover(source, dimensions, force_refresh=False):
        calls.append((source["name"], force_refresh))
        cover = {
            "image": Image.new("RGB", (200, 400), colors[source["name"]]),
            "image_url": f"https://example.com/{source['name'].lower()}.jpg",
            "page_url": source["url"],
            "title": source["name"],
        }
        plugin._write_cached_cover(source, dimensions, cover)
        return cover

    monkeypatch.setattr(plugin, "_load_cover", fake_load_cover)

    image = plugin.generate_image(
        {
            "sources": "Alpha|https://example.com/a\nBeta|https://example.com/b\nGamma|https://example.com/c",
            "rotationMode": "rotate",
            "fitMode": "triptych",
            "showSourceLabel": "false",
            "dailyLibraryMode": "true",
        },
        DummyDeviceConfig(),
    )
    state = plugin._read_state()

    assert calls == [("Alpha", True), ("Beta", True), ("Gamma", True)]
    assert_near_color(image.getpixel((133, 240)), colors["Alpha"])
    assert_near_color(image.getpixel((400, 240)), colors["Beta"])
    assert_near_color(image.getpixel((666, 240)), colors["Gamma"])
    assert state["last_source_ids"] == [
        "Alpha|https://example.com/a",
        "Beta|https://example.com/b",
        "Gamma|https://example.com/c",
    ]


def test_daily_library_triptych_consumes_three_new_sources_each_refresh(monkeypatch):
    plugin = MagazineCovers({"id": "magazine_covers"})
    monkeypatch.setenv("INKYPI_MAGAZINE_COVERS_CACHE", str(make_test_tmp_dir("triptych-queue")))
    monkeypatch.setattr(plugin, "_now_utc", lambda: datetime(2026, 6, 1, 7, 0, tzinfo=timezone.utc))
    monkeypatch.setattr(random, "shuffle", lambda values: None)

    names = ["Alpha", "Beta", "Gamma", "Delta", "Echo", "Foxtrot"]
    colors = {
        name: ((index + 1) * 30, (index + 1) * 20, (index + 1) * 10)
        for index, name in enumerate(names)
    }

    def fake_load_cover(source, dimensions, force_refresh=False):
        cover = {
            "image": Image.new("RGB", (200, 400), colors[source["name"]]),
            "image_url": f"https://example.com/{source['name'].lower()}.jpg",
            "page_url": source["url"],
            "title": source["name"],
        }
        plugin._write_cached_cover(source, dimensions, cover)
        return cover

    monkeypatch.setattr(plugin, "_load_cover", fake_load_cover)
    settings = {
        "sources": "\n".join(f"{name}|https://example.com/{name.lower()}" for name in names),
        "rotationMode": "random",
        "fitMode": "triptych",
        "showSourceLabel": "false",
        "dailyLibraryMode": "true",
    }

    plugin.generate_image(settings, DummyDeviceConfig())
    first_sources = plugin._read_state()["last_sources"]

    plugin.generate_image(settings, DummyDeviceConfig())
    second_sources = plugin._read_state()["last_sources"]

    plugin.generate_image(settings, DummyDeviceConfig())
    third_sources = plugin._read_state()["last_sources"]

    assert first_sources == ["Alpha", "Beta", "Gamma"]
    assert second_sources == ["Delta", "Echo", "Foxtrot"]
    assert set(first_sources).isdisjoint(second_sources)
    assert third_sources == ["Alpha", "Beta", "Gamma"]


def test_daily_library_refreshes_all_sources_once_then_rotates_from_cache(monkeypatch):
    plugin = MagazineCovers({"id": "magazine_covers"})
    monkeypatch.setenv("INKYPI_MAGAZINE_COVERS_CACHE", str(make_test_tmp_dir("daily-cache")))

    current_time = [datetime(2026, 6, 1, 7, 0, tzinfo=timezone.utc)]
    monkeypatch.setattr(plugin, "_now_utc", lambda: current_time[0])

    calls = []
    colors = {
        "Alpha": (255, 0, 0),
        "Beta": (0, 0, 255),
    }

    def fake_load_cover(source, dimensions, force_refresh=False):
        calls.append((source["name"], force_refresh))
        image = Image.new("RGB", dimensions, colors[source["name"]])
        cover = {
            "image": image,
            "image_url": f"https://example.com/{source['name'].lower()}.jpg",
            "page_url": source["url"],
            "title": source["name"],
        }
        plugin._write_cached_cover(source, dimensions, cover)
        return cover

    monkeypatch.setattr(plugin, "_load_cover", fake_load_cover)

    settings = {
        "sources": "Alpha|https://example.com/alpha\nBeta|https://example.com/beta",
        "rotationMode": "rotate",
        "fitMode": "contain",
        "showSourceLabel": "false",
        "dailyLibraryMode": "true",
    }

    first = plugin.generate_image(settings, DummyDeviceConfig())

    assert calls == [("Alpha", True), ("Beta", True)]
    first_pixel = first.getpixel((10, 10))
    assert first_pixel[0] > 240 and first_pixel[1] < 10 and first_pixel[2] < 10

    calls.clear()
    current_time[0] += timedelta(hours=1)
    second = plugin.generate_image(settings, DummyDeviceConfig())

    assert calls == []
    second_pixel = second.getpixel((10, 10))
    assert second_pixel[0] < 10 and second_pixel[1] < 10 and second_pixel[2] > 240


def test_daily_library_refreshes_again_after_daily_interval(monkeypatch):
    plugin = MagazineCovers({"id": "magazine_covers"})
    monkeypatch.setenv("INKYPI_MAGAZINE_COVERS_CACHE", str(make_test_tmp_dir("daily-interval")))

    current_time = [datetime(2026, 6, 1, 7, 0, tzinfo=timezone.utc)]
    monkeypatch.setattr(plugin, "_now_utc", lambda: current_time[0])
    monkeypatch.setattr(plugin, "_daily_library_day_key", lambda: "2026-06-01")

    calls = []

    def fake_load_cover(source, dimensions, force_refresh=False):
        calls.append((source["name"], force_refresh, current_time[0].isoformat()))
        image = Image.new("RGB", dimensions, "white")
        cover = {
            "image": image,
            "image_url": f"https://example.com/{source['name'].lower()}.jpg",
            "page_url": source["url"],
            "title": source["name"],
        }
        plugin._write_cached_cover(source, dimensions, cover)
        return cover

    monkeypatch.setattr(plugin, "_load_cover", fake_load_cover)

    settings = {
        "sources": "Alpha|https://example.com/alpha\nBeta|https://example.com/beta",
        "rotationMode": "rotate",
        "fitMode": "contain",
        "showSourceLabel": "false",
        "dailyLibraryMode": "true",
        "libraryRefreshHours": "23",
    }

    plugin.generate_image(settings, DummyDeviceConfig())
    calls.clear()

    current_time[0] += timedelta(hours=22)
    plugin.generate_image(settings, DummyDeviceConfig())
    assert calls == []

    current_time[0] += timedelta(hours=2)
    plugin.generate_image(settings, DummyDeviceConfig())
    assert [call[:2] for call in calls] == [("Alpha", True), ("Beta", True)]


def test_daily_library_refreshes_when_day_pool_changes(monkeypatch):
    plugin = MagazineCovers({"id": "magazine_covers"})
    monkeypatch.setenv("INKYPI_MAGAZINE_COVERS_CACHE", str(make_test_tmp_dir("daily-day-key")))

    current_time = [datetime(2026, 6, 1, 7, 0, tzinfo=timezone.utc)]
    current_day = ["2026-06-01"]
    monkeypatch.setattr(plugin, "_now_utc", lambda: current_time[0])
    monkeypatch.setattr(plugin, "_daily_library_day_key", lambda: current_day[0])

    calls = []

    def fake_load_cover(source, dimensions, force_refresh=False):
        calls.append((source["name"], force_refresh, current_day[0]))
        cover = {
            "image": Image.new("RGB", dimensions, "white"),
            "image_url": f"https://example.com/{source['name'].lower()}-{current_day[0]}.jpg",
            "page_url": source["url"],
            "title": source["name"],
        }
        plugin._write_cached_cover(source, dimensions, cover)
        return cover

    monkeypatch.setattr(plugin, "_load_cover", fake_load_cover)
    settings = {
        "sources": "Alpha|https://example.com/alpha\nBeta|https://example.com/beta",
        "rotationMode": "rotate",
        "fitMode": "contain",
        "showSourceLabel": "false",
        "dailyLibraryMode": "true",
        "libraryRefreshHours": "23",
    }

    plugin.generate_image(settings, DummyDeviceConfig())
    calls.clear()

    current_time[0] += timedelta(hours=1)
    current_day[0] = "2026-06-02"
    plugin.generate_image(settings, DummyDeviceConfig())

    assert [call[:2] for call in calls] == [("Alpha", True), ("Beta", True)]
    assert plugin._read_state()["daily_library_day_key"] == "2026-06-02"


def test_daily_library_keeps_cached_cover_when_refresh_source_fails(monkeypatch):
    plugin = MagazineCovers({"id": "magazine_covers"})
    monkeypatch.setenv("INKYPI_MAGAZINE_COVERS_CACHE", str(make_test_tmp_dir("daily-fail-cache")))
    monkeypatch.setattr(
        plugin,
        "_now_utc",
        lambda: datetime(2026, 6, 1, 7, 0, tzinfo=timezone.utc),
    )

    source = {"name": "Alpha", "url": "https://example.com/alpha"}
    plugin._write_cached_cover(
        source,
        (800, 480),
        {
            "image": Image.new("RGB", (800, 480), (0, 255, 0)),
            "image_url": "https://example.com/alpha-old.jpg",
            "page_url": source["url"],
            "title": "Alpha",
        },
    )

    def fail_load_cover(_source, _dimensions, force_refresh=False):
        assert force_refresh is True
        raise RuntimeError("temporary source failure")

    monkeypatch.setattr(plugin, "_load_cover", fail_load_cover)

    image = plugin.generate_image(
        {
            "sources": "Alpha|https://example.com/alpha",
            "rotationMode": "rotate",
            "fitMode": "contain",
            "showSourceLabel": "false",
            "dailyLibraryMode": "true",
        },
        DummyDeviceConfig(),
    )

    pixel = image.getpixel((10, 10))
    assert pixel[0] < 10 and pixel[1] > 240 and pixel[2] < 10
