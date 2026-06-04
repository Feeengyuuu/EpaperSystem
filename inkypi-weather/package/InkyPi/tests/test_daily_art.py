import sys
from datetime import datetime
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.daily_art.daily_art import ArtworkCandidate, DailyArt  # noqa: E402


class FakeDeviceConfig:
    def __init__(self, env=None, resolution=(800, 480), timezone="America/Los_Angeles", orientation="horizontal"):
        self.env = env or {}
        self.resolution = resolution
        self.timezone = timezone
        self.orientation = orientation

    def get_resolution(self):
        return self.resolution

    def get_config(self, key=None, default=None):
        values = {
            "timezone": self.timezone,
            "orientation": self.orientation,
        }
        if key is None:
            return values
        return values.get(key, default)

    def load_env_key(self, key):
        return self.env.get(key)


def make_plugin(tmp_path):
    plugin = DailyArt({"id": "daily_art"})
    plugin._cache_dir = lambda: tmp_path
    return plugin


def test_enabled_sources_skips_keyed_sources_without_keys(tmp_path):
    plugin = make_plugin(tmp_path)

    assert plugin._enabled_sources({"sourceMode": "all"}, FakeDeviceConfig()) == ["met", "artic"]


def test_enabled_sources_uses_manual_device_keys(tmp_path):
    plugin = make_plugin(tmp_path)
    device = FakeDeviceConfig(env={"Europeana_Key": "eu-secret", "Harvard_Art_Key": "ha-secret"})

    assert plugin._enabled_sources({"sourceMode": "all"}, device) == ["met", "artic", "europeana", "harvard"]


def test_harvard_key_accepts_device_harverd_typo(tmp_path):
    plugin = make_plugin(tmp_path)
    device = FakeDeviceConfig(env={"Harverd_Key": "ha-secret"})

    assert plugin._enabled_sources({"sourceMode": "keyed"}, device) == ["harvard"]


def test_artic_candidates_build_iiif_urls(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path)

    def fake_get_json(url, params, headers=None):
        assert "artworks/search" in url
        assert params["query[term][is_public_domain]"] == "true"
        return {
            "config": {"iiif_url": "https://www.artic.edu/iiif/2"},
            "data": [{
                "id": 27992,
                "title": "A Sunday on La Grande Jatte",
                "artist_title": "Georges Seurat",
                "date_display": "1884",
                "image_id": "abc",
                "medium_display": "Oil on canvas",
                "place_of_origin": "France",
            }],
        }

    monkeypatch.setattr(plugin, "_get_json", fake_get_json)

    candidates = plugin._fetch_artic_candidates("painting", 5, {"iiifWidth": "900"}, __import__("random").Random(1))

    assert len(candidates) == 1
    assert candidates[0].artwork_id == "artic:27992"
    assert candidates[0].image_url == "https://www.artic.edu/iiif/2/abc/full/900,/0/default.jpg"


def test_europeana_candidates_use_full_media_url(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path)

    def fake_get_json(url, params, headers=None):
        assert params["wskey"] == "secret"
        assert params["media"] == "true"
        return {
            "items": [{
                "id": "/123/test",
                "title": ["The Test Painting"],
                "dcCreator": ["Example Artist"],
                "dataProvider": ["Example Museum"],
                "edmIsShownBy": ["https://example.org/full.jpg"],
                "edmPreview": ["https://example.org/thumb.jpg"],
                "rights": ["http://creativecommons.org/publicdomain/mark/1.0/"],
            }],
        }

    monkeypatch.setattr(plugin, "_get_json", fake_get_json)

    candidates = plugin._fetch_europeana_candidates("vermeer", 5, "secret", __import__("random").Random(1))

    assert candidates[0].source == "europeana"
    assert candidates[0].image_url == "https://example.org/full.jpg"
    assert candidates[0].museum == "Example Museum"


def test_harvard_image_url_prefers_iiif_base(tmp_path):
    plugin = make_plugin(tmp_path)

    url = plugin._harvard_image_url({"images": [{"baseimageurl": "https://nrs.harvard.edu/urn-3:HUAM:799974"}]})

    assert url == "https://nrs.harvard.edu/urn-3:HUAM:799974/full/1200,/0/default.jpg"


def test_candidate_order_resets_after_all_seen(tmp_path):
    plugin = make_plugin(tmp_path)
    candidates = [
        ArtworkCandidate("met", "The Met", "met:1", "One", image_url="https://example.com/1.jpg"),
        ArtworkCandidate("artic", "Art Institute of Chicago", "artic:2", "Two", image_url="https://example.com/2.jpg"),
    ]
    state = {"schema": "daily-art-state-v1", "buckets": {"2026-06-03": {"seen_artwork_ids": ["met:1", "artic:2"]}}}

    ordered = plugin._candidate_order(candidates, state, "2026-06-03")

    assert {candidate.artwork_id for candidate in ordered} == {"met:1", "artic:2"}
    assert state["buckets"]["2026-06-03"]["seen_artwork_ids"] == []


def test_generate_image_writes_and_reuses_daily_cache(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path)
    calls = {"download": 0}
    candidate = ArtworkCandidate(
        "met",
        "The Met",
        "met:1",
        "The Daily Test",
        artist="Artist",
        date="1900",
        museum="The Met",
        image_url="https://example.com/art.jpg",
        page_url="https://example.com/art",
    )
    device = FakeDeviceConfig()
    now = datetime(2026, 6, 3, 9, 30)

    monkeypatch.setattr(plugin, "_now_for_device", lambda _device: now)
    monkeypatch.setattr(plugin, "_candidate_pool", lambda _settings, _device, _now: [candidate])
    monkeypatch.setattr("plugins.daily_art.daily_art.write_context", lambda *args, **kwargs: None)

    def fake_download(url, dimensions, settings):
        calls["download"] += 1
        return Image.new("RGB", (300, 420), (120, 80, 40))

    monkeypatch.setattr(plugin, "_download_image_preview", fake_download)

    first = plugin.generate_image({}, device)
    second = plugin.generate_image({}, device)

    assert first.size == (800, 480)
    assert second.size == (800, 480)
    assert calls["download"] == 1
    assert plugin._read_daily_cache()["artwork"]["artwork_id"] == "met:1"


def test_generate_image_auto_gallery_collects_portrait_artworks(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path)
    device = FakeDeviceConfig()
    now = datetime(2026, 6, 3, 9, 30)
    candidates = [
        ArtworkCandidate("met", "The Met", "met:landscape", "Wide", image_url="https://example.com/wide.jpg"),
        ArtworkCandidate("met", "The Met", "met:red", "Red", image_url="https://example.com/red.jpg"),
        ArtworkCandidate("artic", "Art Institute of Chicago", "artic:green", "Green", image_url="https://example.com/green.jpg"),
        ArtworkCandidate("harvard", "Harvard Art Museums", "harvard:blue", "Blue", image_url="https://example.com/blue.jpg"),
    ]
    images = {
        "https://example.com/wide.jpg": Image.new("RGB", (640, 300), (220, 220, 220)),
        "https://example.com/red.jpg": Image.new("RGB", (300, 500), (220, 20, 20)),
        "https://example.com/green.jpg": Image.new("RGB", (300, 500), (20, 160, 40)),
        "https://example.com/blue.jpg": Image.new("RGB", (300, 500), (20, 70, 220)),
    }

    monkeypatch.setattr(plugin, "_now_for_device", lambda _device: now)
    monkeypatch.setattr(plugin, "_candidate_pool", lambda _settings, _device, _now: candidates)
    monkeypatch.setattr(plugin, "_candidate_order", lambda items, _state, _rotation_key: items)
    monkeypatch.setattr(plugin, "_download_image_preview", lambda url, _dimensions, _settings: images[url])
    monkeypatch.setattr("plugins.daily_art.daily_art.write_context", lambda *args, **kwargs: None)

    image = plugin.generate_image({"layoutMode": "auto_gallery", "galleryCount": "3"}, device)
    cache = plugin._read_daily_cache()

    assert image.size == (800, 480)
    assert cache["layout"] == "gallery"
    assert [item["artwork_id"] for item in cache["artworks"]] == ["met:red", "artic:green", "harvard:blue"]
    assert image.getpixel((133, 240)) == (220, 20, 20)
    assert image.getpixel((399, 240)) == (20, 160, 40)
    assert image.getpixel((666, 240)) == (20, 70, 220)


def test_generate_image_auto_gallery_falls_back_to_landscape_single(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path)
    device = FakeDeviceConfig()
    now = datetime(2026, 6, 3, 9, 30)
    candidate = ArtworkCandidate(
        "met",
        "The Met",
        "met:wide",
        "Wide Landscape",
        image_url="https://example.com/wide.jpg",
    )
    calls = {"download": 0}

    monkeypatch.setattr(plugin, "_now_for_device", lambda _device: now)
    monkeypatch.setattr(plugin, "_candidate_pool", lambda _settings, _device, _now: [candidate])
    monkeypatch.setattr(plugin, "_candidate_order", lambda items, _state, _rotation_key: items)
    monkeypatch.setattr("plugins.daily_art.daily_art.write_context", lambda *args, **kwargs: None)

    def fake_download(url, dimensions, settings):
        calls["download"] += 1
        return Image.new("RGB", (640, 300), (40, 90, 150))

    monkeypatch.setattr(plugin, "_download_image_preview", fake_download)

    image = plugin.generate_image({"layoutMode": "auto_gallery", "galleryCount": "3"}, device)
    cache = plugin._read_daily_cache()

    assert image.size == (800, 480)
    assert calls["download"] == 1
    assert cache["layout"] == "single"
    assert cache["artworks"][0]["artwork_id"] == "met:wide"
