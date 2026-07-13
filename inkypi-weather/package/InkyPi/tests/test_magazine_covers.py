import sys
import json
import os
import random
import socket
import stat
import uuid
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.magazine_covers.magazine_covers import (  # noqa: E402
    ART_DEFAULT_SOURCES,
    CORE_DEFAULT_SOURCES,
    DAILY_LIBRARY_REFRESH_INTERVAL,
    DEFAULT_SOURCES,
    LEGACY_DEFAULT_SOURCES,
    PRE_ART_DEFAULT_SOURCES,
    PRE_MATURE_DEFAULT_SOURCES,
    MATURE_DEFAULT_SOURCES,
    MAX_PI_SAFE_SOURCE_PIXELS,
    MagazineCovers,
    _ImageCandidateParser,
)
from plugins.magazine_covers import magazine_covers as magazine_module  # noqa: E402
from plugins.base_plugin.presentation import (  # noqa: E402
    PresentationMode,
    PresentationRequestContext,
    bind_presentation_instance_identity,
)
from runtime.runtime_state import PresentationCommitReceipt  # noqa: E402


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


def make_banked_plugin(tmp_path):
    plugin = MagazineCovers({"id": "magazine_covers"})
    plugin._cache_dir = lambda: Path(tmp_path)
    return plugin


def bound_magazine_settings(*, instance_uuid="magazine-test-instance", count=24, **overrides):
    settings = {
        "sources": "\n".join(
            f"Magazine {index}|https://magazineshop.us/collections/magazine-{index}"
            for index in range(count)
        ),
        "rotationMode": "random",
        "fitMode": "contain",
        "showSourceLabel": "false",
        "dailyLibraryMode": "true",
    }
    settings.update(overrides)
    return bind_presentation_instance_identity(settings, instance_uuid)


def magazine_request(
    request_id,
    *,
    origin="origin-display",
    requested_at="2026-07-12T10:00:00+00:00",
):
    return PresentationRequestContext(
        request_id=request_id,
        requested_at=requested_at,
        origin_display_commit_id=origin,
        last_receipt=None,
    )


def magazine_receipt(
    request_id,
    *,
    display="prepared-display",
    committed_at="2026-07-12T10:01:00+00:00",
):
    return PresentationCommitReceipt(
        request_id=request_id,
        committed_at=committed_at,
        display_commit_id=display,
        structural_generation=1,
        settings_revision=1,
        theme_mode=None,
    )


def magazine_theme_context(mode):
    return {
        "mode": mode,
        "palette": {
            "background": (246, 238, 232) if mode == "day" else (25, 13, 18),
            "accent": (178, 58, 85) if mode == "day" else (240, 121, 145),
        },
    }


def cover_for_source(source, *, color=(80, 110, 140), suffix="current"):
    slug = source["name"].lower().replace(" ", "-")
    return {
        "image": Image.new("RGB", (240, 420), color),
        "image_url": f"https://cdn.shopify.com/{slug}-{suffix}.jpg",
        "page_url": source["url"],
        "title": source["name"],
    }


def hydrate_magazine_bank(plugin, monkeypatch, settings, *, runs=3, fail_names=()):
    calls = []
    fail_names = set(fail_names)
    monkeypatch.setattr(
        plugin,
        "_now_utc",
        lambda: datetime(2026, 7, 12, 9, 30, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(plugin, "_presentation_date_key", lambda _device: "2026-07-12")
    monkeypatch.setattr(magazine_module, "write_context", lambda *_args, **_kwargs: None)

    def load_cover(source, _dimensions, force_refresh=False, deadline=None):
        assert deadline is not None
        calls.append((source["name"], force_refresh))
        if source["name"] in fail_names:
            raise RuntimeError("provider failure")
        return cover_for_source(source)

    monkeypatch.setattr(plugin, "_load_cover", load_cover)
    for _index in range(runs):
        plugin.generate_image(settings, DummyDeviceConfig())
    return calls


def magazine_presentation_state(plugin):
    return json.loads(plugin._presentation_state_path().read_text(encoding="utf-8"))


def magazine_profile(state, instance_uuid="magazine-test-instance"):
    fingerprint = state["instance_profiles"][instance_uuid]
    return state["profiles"][fingerprint]


def magazine_selection_ids(state, selection, instance_uuid="magazine-test-instance"):
    profile = magazine_profile(state, instance_uuid)
    records = {record["record_key"]: record for record in profile["records"]}
    return [records[key]["source_id"] for key in selection["record_keys"]]


def magazine_cache_tree(root):
    root = Path(root)
    if not root.exists():
        return {}
    result = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            result[relative] = ("symlink", os.readlink(path))
        elif path.is_dir():
            result[relative] = ("dir", None)
        else:
            result[relative] = ("file", path.read_bytes())
    return result


def magazine_png_bytes(color="red", size=(32, 48)):
    output = BytesIO()
    Image.new("RGB", size, color).save(output, format="PNG")
    return output.getvalue()


class MagazineFakeHttpResponse:
    def __init__(self, status, *, url, headers=None, payload=b""):
        self.status_code = status
        self.url = url
        self.headers = headers or {}
        self._payload = payload
        self.closed = False

    def iter_content(self, chunk_size=65536):
        del chunk_size
        if self._payload:
            yield self._payload

    def close(self):
        self.closed = True


class MagazineFakeRedirectSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if not self.responses:
            raise AssertionError("unexpected HTTP request")
        return self.responses.pop(0)


class MagazineFakeRedirectClient:
    def __init__(self, session):
        self.session = session


def magazine_resolver_for(mapping):
    def resolve(hostname, port, **_kwargs):
        address = mapping[hostname]
        return [
            (
                socket.AF_INET6 if ":" in address else socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                (address, port),
            )
        ]

    return resolve


def test_srcset_candidates_are_ordered_small_to_large():
    parser = _ImageCandidateParser("https://example.com/current")

    assert parser._srcset_urls(
        "large.jpg 2400w, small.jpg 600w, medium.jpg 1200w"
    ) == ["small.jpg", "medium.jpg", "large.jpg"]


def test_default_source_pool_has_fresh_collection_sources():
    plugin = MagazineCovers({"id": "magazine_covers"})
    sources = plugin._parse_sources(DEFAULT_SOURCES)
    source_ids = {plugin._source_id(source) for source in sources}

    assert len(sources) >= 130
    assert "Newest Releases Page 2|https://magazineshop.us/collections/new-releases?page=2" in source_ids
    assert "Newest Releases Page 20|https://magazineshop.us/collections/new-releases?page=20" in source_ids
    assert "All In Stock Page 20|https://magazineshop.us/collections/all-in-stock-products?page=20" in source_ids
    assert "All Magazines Page 20|https://magazineshop.us/collections/all?page=20" in source_ids
    assert "Digital Magazines Page 10|https://magazineshop.us/collections/digital-magazines?page=10" in source_ids
    assert "Newsweek|https://magazineshop.us/collections/newsweek" in source_ids
    assert "Athlon Sports|https://magazineshop.us/collections/athlon-sports" in source_ids
    assert "Archie Comics|https://magazineshop.us/collections/archie-comics" in source_ids
    assert "VegNews|https://magazineshop.us/collections/vegnews" in source_ids
    assert "Art in America|https://magazineshop.us/collections/art-in-america" in source_ids
    assert "Artforum|https://magazineshop.us/collections/artforum" in source_ids
    assert "Aspire Design and Home|https://magazineshop.us/collections/aspire-design-and-home" in source_ids
    assert "Decorator|https://magazineshop.us/collections/decorator" in source_ids
    assert "Home Design|https://magazineshop.us/collections/home-design" in source_ids
    assert "Playboy|https://magazineshop.us/collections/playboy" in source_ids
    assert "Playboy Magazine|https://www.playboy.com/magazine" in source_ids
    assert "Penthouse Magazine|https://penthousemagazine.com/" in source_ids
    assert "Hustler Magazine|https://hustlermagazine.com/" in source_ids


def test_legacy_saved_default_sources_are_expanded_with_new_pool():
    plugin = MagazineCovers({"id": "magazine_covers"})

    sources = plugin._sources_from_settings({"sources": CORE_DEFAULT_SOURCES})

    assert len(sources) == len(plugin._parse_sources(DEFAULT_SOURCES))
    assert sources[0]["name"] == "TIME"
    assert sources[-1]["name"] == "Maxim"


def test_legacy_saved_full_default_sources_are_expanded_with_new_pool():
    plugin = MagazineCovers({"id": "magazine_covers"})

    sources = plugin._sources_from_settings({"sources": LEGACY_DEFAULT_SOURCES})
    source_ids = {plugin._source_id(source) for source in sources}

    assert len(sources) == len(plugin._parse_sources(DEFAULT_SOURCES))
    assert "All In Stock Page 20|https://magazineshop.us/collections/all-in-stock-products?page=20" in source_ids
    assert "Hustler Magazine|https://hustlermagazine.com/" in source_ids


def test_pre_art_default_sources_are_expanded_with_art_pool():
    plugin = MagazineCovers({"id": "magazine_covers"})

    sources = plugin._sources_from_settings({"sources": PRE_ART_DEFAULT_SOURCES})
    source_ids = {plugin._source_id(source) for source in sources}
    art_source_ids = {
        plugin._source_id(source)
        for source in plugin._parse_sources(ART_DEFAULT_SOURCES)
    }

    assert len(sources) == len(plugin._parse_sources(DEFAULT_SOURCES))
    assert art_source_ids.issubset(source_ids)


def test_pre_mature_default_sources_are_expanded_with_mature_pool():
    plugin = MagazineCovers({"id": "magazine_covers"})

    sources = plugin._sources_from_settings({"sources": PRE_MATURE_DEFAULT_SOURCES})
    source_ids = {plugin._source_id(source) for source in sources}
    mature_source_ids = {
        plugin._source_id(source)
        for source in plugin._parse_sources(MATURE_DEFAULT_SOURCES)
    }

    assert len(sources) == len(plugin._parse_sources(DEFAULT_SOURCES))
    assert mature_source_ids.issubset(source_ids)

def test_art_sources_boost_candidate_score():
    plugin = MagazineCovers({"id": "magazine_covers"})

    score = plugin._candidate_score(
        {"name": "Artforum", "url": "https://magazineshop.us/collections/artforum"},
        {
            "url": "https://magazineshop.us/cdn/shop/files/artforum-cover.jpg",
            "alt": "latest cover",
            "class": "product-card",
            "id": "",
            "width": "500",
            "height": "700",
        },
    )

    assert score >= 160


def test_mature_sources_boost_candidate_score():
    plugin = MagazineCovers({"id": "magazine_covers"})

    score = plugin._candidate_score(
        {"name": "Playboy", "url": "https://magazineshop.us/collections/playboy"},
        {
            "url": "https://magazineshop.us/cdn/shop/files/playboy-cover.jpg",
            "alt": "latest cover",
            "class": "product-card",
            "id": "",
            "width": "500",
            "height": "700",
        },
    )

    assert score >= 150


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
    monkeypatch.setattr(plugin, "_download_candidate_to_temp", lambda _url, _source=None: source_path)

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
    monkeypatch.setattr(plugin, "_download_candidate_to_temp", lambda _url, _source=None: source_path)
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
    monkeypatch.setattr(plugin, "_download_candidate_to_temp", lambda _url, _source=None: source_path)

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


def test_random_order_rebuilds_saved_pool_older_than_one_week(monkeypatch):
    plugin = MagazineCovers({"id": "magazine_covers"})
    monkeypatch.setenv("INKYPI_MAGAZINE_COVERS_CACHE", str(make_test_tmp_dir("stale-random-pool")))
    now = datetime(2026, 6, 10, 8, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(plugin, "_now_utc", lambda: now)
    monkeypatch.setattr(random, "shuffle", lambda values: None)
    sources = [
        {"name": "Alpha", "url": "https://example.com/alpha"},
        {"name": "Beta", "url": "https://example.com/beta"},
        {"name": "Gamma", "url": "https://example.com/gamma"},
    ]

    path = plugin._state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({
            "random_queue": ["Beta|https://example.com/beta"],
            "random_source_ids": ["Beta|https://example.com/beta"],
            "random_pool_saved_at": (now - timedelta(days=8)).isoformat(),
        }),
        encoding="utf-8",
    )

    ordered = plugin._random_order(sources)
    state = plugin._read_state()

    assert [source["name"] for source in ordered] == ["Alpha", "Beta", "Gamma"]
    assert state["random_queue"] == [
        "Alpha|https://example.com/alpha",
        "Beta|https://example.com/beta",
        "Gamma|https://example.com/gamma",
    ]
    assert state["random_pool_saved_at"] == now.isoformat()


def test_random_order_keeps_recent_saved_pool(monkeypatch):
    plugin = MagazineCovers({"id": "magazine_covers"})
    monkeypatch.setenv("INKYPI_MAGAZINE_COVERS_CACHE", str(make_test_tmp_dir("recent-random-pool")))
    now = datetime(2026, 6, 10, 8, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(plugin, "_now_utc", lambda: now)
    monkeypatch.setattr(random, "shuffle", lambda values: None)
    sources = [
        {"name": "Alpha", "url": "https://example.com/alpha"},
        {"name": "Beta", "url": "https://example.com/beta"},
        {"name": "Gamma", "url": "https://example.com/gamma"},
    ]

    path = plugin._state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({
            "random_queue": ["Beta|https://example.com/beta"],
            "random_source_ids": ["Beta|https://example.com/beta"],
            "random_pool_saved_at": (now - timedelta(days=6, hours=23)).isoformat(),
        }),
        encoding="utf-8",
    )

    ordered = plugin._random_order(sources)

    assert [source["name"] for source in ordered] == ["Beta", "Alpha", "Gamma"]


def test_cover_cache_files_older_than_one_week_are_removed(monkeypatch):
    plugin = MagazineCovers({"id": "magazine_covers"})
    monkeypatch.setenv("INKYPI_MAGAZINE_COVERS_CACHE", str(make_test_tmp_dir("stale-cover-files")))
    now = datetime(2026, 6, 10, 8, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(plugin, "_now_utc", lambda: now)
    covers_dir = plugin._cache_dir() / "covers"
    covers_dir.mkdir(parents=True, exist_ok=True)

    old_meta = covers_dir / "old.json"
    old_image = covers_dir / "old.jpg"
    new_meta = covers_dir / "new.json"
    new_image = covers_dir / "new.jpg"
    old_image.write_bytes(b"old")
    new_image.write_bytes(b"new")
    old_meta.write_text(
        json.dumps({
            "image_path": str(old_image),
            "fetched_at": (now - timedelta(days=8)).isoformat(),
        }),
        encoding="utf-8",
    )
    new_meta.write_text(
        json.dumps({
            "image_path": str(new_image),
            "fetched_at": (now - timedelta(days=6)).isoformat(),
        }),
        encoding="utf-8",
    )

    unlink_calls = []

    def fake_unlink(path, missing_ok=False):
        unlink_calls.append(Path(path).name)

    monkeypatch.setattr(Path, "unlink", fake_unlink)

    removed = plugin._prune_stale_cover_cache_files()

    assert removed == 2
    assert unlink_calls == ["old.json", "old.jpg"]
    assert new_meta.exists()
    assert new_image.exists()

def test_daily_library_pool_older_than_one_week_is_removed(monkeypatch):
    plugin = MagazineCovers({"id": "magazine_covers"})
    monkeypatch.setenv("INKYPI_MAGAZINE_COVERS_CACHE", str(make_test_tmp_dir("stale-daily-pool")))
    now = datetime(2026, 6, 10, 8, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(plugin, "_now_utc", lambda: now)
    sources = [
        {"name": "Alpha", "url": "https://example.com/alpha"},
        {"name": "Beta", "url": "https://example.com/beta"},
    ]

    path = plugin._state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({
            "daily_library_version": "magazine-covers-daily-library-v1",
            "daily_library_pool_key": plugin._pool_key(sources),
            "daily_library_dimensions": "800x480",
            "daily_library_day_key": "2026-06-10",
            "daily_library_refreshed_at": (now - timedelta(days=8)).isoformat(),
            "daily_library_source_ids": [
                "Alpha|https://example.com/alpha",
                "Beta|https://example.com/beta",
            ],
            "daily_library_queue": ["Beta|https://example.com/beta"],
            "daily_library_next_index": 1,
        }),
        encoding="utf-8",
    )

    state = plugin._read_state()

    assert "daily_library_source_ids" not in state
    assert "daily_library_queue" not in state
    assert plugin._daily_library_needs_refresh(sources, (800, 480), {}) is True


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

    def fake_load_cover(source, dimensions, force_refresh=False, deadline=None):
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

    def fake_load_cover(source, dimensions, force_refresh=False, deadline=None):
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

    settings = bind_presentation_instance_identity(
        {
            "sources": "Alpha|https://example.com/a\nBeta|https://example.com/b\nGamma|https://example.com/c",
            "rotationMode": "rotate",
            "fitMode": "triptych",
            "showSourceLabel": "false",
            "dailyLibraryMode": "true",
        },
        "legacy-triptych",
    )
    image = plugin.generate_image(settings, DummyDeviceConfig())
    state = magazine_presentation_state(plugin)
    profile = magazine_profile(state, "legacy-triptych")

    assert calls == [("Alpha", True), ("Beta", True), ("Gamma", True)]
    assert_near_color(image.getpixel((133, 240)), colors["Alpha"])
    assert_near_color(image.getpixel((400, 240)), colors["Beta"])
    assert_near_color(image.getpixel((666, 240)), colors["Gamma"])
    assert magazine_selection_ids(state, profile["current_selection"], "legacy-triptych") == [
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

    def fake_load_cover(source, dimensions, force_refresh=False, deadline=None):
        cover = {
            "image": Image.new("RGB", (200, 400), colors[source["name"]]),
            "image_url": f"https://example.com/{source['name'].lower()}.jpg",
            "page_url": source["url"],
            "title": source["name"],
        }
        plugin._write_cached_cover(source, dimensions, cover)
        return cover

    monkeypatch.setattr(plugin, "_load_cover", fake_load_cover)
    settings = bind_presentation_instance_identity({
        "sources": "\n".join(f"{name}|https://example.com/{name.lower()}" for name in names),
        "rotationMode": "random",
        "fitMode": "triptych",
        "showSourceLabel": "false",
        "dailyLibraryMode": "true",
    }, "legacy-triptych-queue")

    plugin.generate_image(settings, DummyDeviceConfig())
    initial = magazine_presentation_state(plugin)
    first_ids = magazine_selection_ids(
        initial,
        magazine_profile(initial, "legacy-triptych-queue")["current_selection"],
        "legacy-triptych-queue",
    )
    plugin.prepare_presentation(
        settings,
        DummyDeviceConfig(),
        request=magazine_request(
            "1" * 32,
            requested_at="2026-06-01T08:00:00+00:00",
        ),
        resolved_theme_context=None,
    )
    pending = magazine_presentation_state(plugin)
    second_ids = magazine_selection_ids(
        pending,
        magazine_profile(pending, "legacy-triptych-queue")["pending_selection"],
        "legacy-triptych-queue",
    )

    assert len(first_ids) == 3
    assert len(second_ids) == 3
    assert set(first_ids).isdisjoint(second_ids)


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

    def fake_load_cover(source, dimensions, force_refresh=False, deadline=None):
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

    settings = bind_presentation_instance_identity({
        "sources": "Alpha|https://example.com/alpha\nBeta|https://example.com/beta",
        "rotationMode": "rotate",
        "fitMode": "contain",
        "showSourceLabel": "false",
        "dailyLibraryMode": "true",
    }, "legacy-daily-cache")

    first = plugin.generate_image(settings, DummyDeviceConfig())

    assert calls == [("Alpha", True), ("Beta", True)]
    first_pixel = first.getpixel((10, 10))
    assert first_pixel[0] > 240 and first_pixel[1] < 10 and first_pixel[2] < 10

    calls.clear()
    current_time[0] += timedelta(hours=1)
    second = plugin.generate_image(settings, DummyDeviceConfig())

    assert calls == []
    second_pixel = second.getpixel((10, 10))
    assert second_pixel == first_pixel


def test_daily_library_refreshes_again_after_daily_interval(monkeypatch):
    plugin = MagazineCovers({"id": "magazine_covers"})
    monkeypatch.setenv("INKYPI_MAGAZINE_COVERS_CACHE", str(make_test_tmp_dir("daily-interval")))

    current_time = [datetime(2026, 6, 1, 7, 0, tzinfo=timezone.utc)]
    monkeypatch.setattr(plugin, "_now_utc", lambda: current_time[0])
    monkeypatch.setattr(plugin, "_daily_library_day_key", lambda: "2026-06-01")
    monkeypatch.setattr(plugin, "_presentation_date_key", lambda _device: "2026-06-01")

    calls = []

    def fake_load_cover(source, dimensions, force_refresh=False, deadline=None):
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

    settings = bind_presentation_instance_identity({
        "sources": "Alpha|https://example.com/alpha\nBeta|https://example.com/beta",
        "rotationMode": "rotate",
        "fitMode": "contain",
        "showSourceLabel": "false",
        "dailyLibraryMode": "true",
        "libraryRefreshHours": "23",
    }, "legacy-daily-interval")

    plugin.generate_image(settings, DummyDeviceConfig())
    calls.clear()

    current_time[0] += timedelta(hours=22)
    plugin.generate_image(settings, DummyDeviceConfig())
    assert [call[:2] for call in calls] == [("Alpha", True), ("Beta", True)]
    calls.clear()

    current_time[0] += timedelta(hours=2)
    plugin.generate_image(settings, DummyDeviceConfig())
    assert calls == []


def test_daily_library_refreshes_when_day_pool_changes(monkeypatch):
    plugin = MagazineCovers({"id": "magazine_covers"})
    monkeypatch.setenv("INKYPI_MAGAZINE_COVERS_CACHE", str(make_test_tmp_dir("daily-day-key")))

    current_time = [datetime(2026, 6, 1, 7, 0, tzinfo=timezone.utc)]
    current_day = ["2026-06-01"]
    monkeypatch.setattr(plugin, "_now_utc", lambda: current_time[0])
    monkeypatch.setattr(plugin, "_daily_library_day_key", lambda: current_day[0])

    calls = []

    def fake_load_cover(source, dimensions, force_refresh=False, deadline=None):
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
    settings = bind_presentation_instance_identity({
        "sources": "Alpha|https://example.com/alpha\nBeta|https://example.com/beta",
        "rotationMode": "rotate",
        "fitMode": "contain",
        "showSourceLabel": "false",
        "dailyLibraryMode": "true",
        "libraryRefreshHours": "23",
    }, "legacy-day-key")
    monkeypatch.setattr(plugin, "_presentation_date_key", lambda _device: current_day[0])

    plugin.generate_image(settings, DummyDeviceConfig())
    calls.clear()

    current_time[0] += timedelta(hours=1)
    current_day[0] = "2026-06-02"
    plugin.generate_image(settings, DummyDeviceConfig())

    assert [call[:2] for call in calls] == [("Alpha", True), ("Beta", True)]
    state = magazine_presentation_state(plugin)
    assert magazine_profile(state, "legacy-day-key")["date_key"] == "2026-06-02"


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
        bind_presentation_instance_identity({
            "sources": "Alpha|https://example.com/alpha",
            "rotationMode": "rotate",
            "fitMode": "contain",
            "showSourceLabel": "false",
            "dailyLibraryMode": "true",
        }, "legacy-fail-cache"),
        DummyDeviceConfig(),
    )

    pixel = image.getpixel((10, 10))
    assert pixel[0] < 10 and pixel[1] > 240 and pixel[2] < 10


def test_magazine_manifest_declares_presentation_capability():
    manifest_path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "plugins"
        / "magazine_covers"
        / "plugin-info.json"
    )
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert payload["capabilities"]["supports_presentation_refresh"] is True


@pytest.mark.parametrize(
    ("setting", "explicit"),
    [
        ("rotationMode", "random"),
        ("fitMode", "triptych"),
        ("backgroundColor", "white"),
        ("backgroundStyle", "blur"),
        ("showSourceLabel", "true"),
        ("dailyLibraryMode", "true"),
        ("libraryRefreshHours", "6"),
    ],
)
def test_magazine_fingerprint_omitted_defaults_equal_explicit_pixel_defaults(
    setting,
    explicit,
):
    from plugins.magazine_covers.presentation_bank import settings_fingerprint

    sources = [
        {"name": "Alpha", "url": "https://magazineshop.us/collections/alpha"},
        {"name": "Beta", "url": "https://magazineshop.us/collections/beta"},
        {"name": "Gamma", "url": "https://magazineshop.us/collections/gamma"},
    ]
    omitted = {}
    explicit_settings = {setting: explicit}

    assert settings_fingerprint(omitted, sources, (800, 480), "2026-07-12") == settings_fingerprint(
        explicit_settings,
        sources,
        (800, 480),
        "2026-07-12",
    )


def test_magazine_equal_default_fingerprints_have_equal_render_pixels():
    from plugins.magazine_covers.presentation_bank import settings_fingerprint

    plugin = MagazineCovers({"id": "magazine_covers"})
    sources = [
        {"name": "Alpha", "url": "https://magazineshop.us/collections/alpha"},
        {"name": "Beta", "url": "https://magazineshop.us/collections/beta"},
        {"name": "Gamma", "url": "https://magazineshop.us/collections/gamma"},
    ]
    omitted = {"showSourceLabel": "false"}
    explicit = {
        "rotationMode": "random",
        "fitMode": "triptych",
        "backgroundColor": "white",
        "backgroundStyle": "blur",
        "showSourceLabel": "false",
        "dailyLibraryMode": "true",
        "libraryRefreshHours": "6",
    }
    source_covers = [
        (source, cover_for_source(source, color=color))
        for source, color in zip(
            sources,
            ((210, 30, 30), (30, 180, 60), (30, 70, 210)),
        )
    ]

    assert settings_fingerprint(omitted, sources, (800, 480), "2026-07-12") == settings_fingerprint(
        explicit,
        sources,
        (800, 480),
        "2026-07-12",
    )
    assert plugin._fit_cover_triptych(source_covers, (800, 480), omitted).tobytes() == plugin._fit_cover_triptych(
        source_covers,
        (800, 480),
        explicit,
    ).tobytes()


def test_magazine_data_hydrates_six_per_run_to_eighteen_without_consuming_display_state(
    tmp_path,
    monkeypatch,
):
    from plugins.magazine_covers import presentation_bank

    plugin = make_banked_plugin(tmp_path)
    settings = bound_magazine_settings()

    calls = hydrate_magazine_bank(plugin, monkeypatch, settings, runs=3)
    state = magazine_presentation_state(plugin)
    profile = magazine_profile(state)

    assert presentation_bank.READY_TARGET == 18
    assert presentation_bank.REFILL_THRESHOLD == 6
    assert len(calls) == 18
    assert [len(calls[:6]), len(calls[6:12]), len(calls[12:18])] == [6, 6, 6]
    assert len(profile["records"]) == 18
    assert profile["current_selection"] is not None
    assert profile["pending_selection"] is None
    assert profile.get("date_buckets", {}).get("2026-07-12", {}).get("seen_source_ids", []) == []
    assert "random_queue" not in state
    assert "daily_library_queue" not in state
    assert "daily_library_next_index" not in state


def test_magazine_warm_presentation_is_provider_free_and_receipt_commits_once(
    tmp_path,
    monkeypatch,
):
    plugin = make_banked_plugin(tmp_path)
    settings = bound_magazine_settings(fitMode="triptych")
    hydrate_magazine_bank(plugin, monkeypatch, settings, runs=3)
    monkeypatch.setattr(plugin, "_load_cover", lambda *_args, **_kwargs: pytest.fail("presentation used cover provider"))
    monkeypatch.setattr(plugin, "_fetch_text", lambda *_args, **_kwargs: pytest.fail("presentation used HTML provider"))
    monkeypatch.setattr(
        plugin,
        "_download_candidate_to_temp",
        lambda *_args, **_kwargs: pytest.fail("presentation downloaded media"),
    )
    monkeypatch.setattr(magazine_module, "get_http_client", lambda: pytest.fail("presentation opened HTTP"), raising=False)

    prepared = plugin.prepare_presentation(
        settings,
        DummyDeviceConfig(),
        request=magazine_request("a" * 32),
        resolved_theme_context=magazine_theme_context("night"),
    )
    pending_state = magazine_presentation_state(plugin)
    pending = magazine_profile(pending_state)["pending_selection"]
    pending_ids = magazine_selection_ids(pending_state, pending)
    assert prepared.changed is True
    assert prepared.image.info["inkypi_theme_mode"] == "night"
    assert len(pending_ids) == 3
    seen_before_pending = magazine_profile(pending_state)["date_buckets"]["2026-07-12"].get("seen_source_ids", [])
    assert not set(pending_ids).intersection(seen_before_pending)

    plugin.reconcile_presentation_receipt(settings, magazine_receipt("a" * 32))
    committed_bytes = plugin._presentation_state_path().read_bytes()
    plugin.reconcile_presentation_receipt(settings, magazine_receipt("a" * 32))
    committed_state = magazine_presentation_state(plugin)
    profile = magazine_profile(committed_state)
    assert plugin._presentation_state_path().read_bytes() == committed_bytes
    assert profile["pending_selection"] is None
    assert magazine_selection_ids(committed_state, profile["current_selection"]) == pending_ids
    assert profile["date_buckets"]["2026-07-12"]["seen_source_ids"][-3:] == pending_ids


def test_magazine_failed_foreign_canceled_and_replayed_receipts_are_byte_noops(
    tmp_path,
    monkeypatch,
):
    plugin = make_banked_plugin(tmp_path)
    settings = bound_magazine_settings()
    hydrate_magazine_bank(plugin, monkeypatch, settings, runs=3)
    plugin.prepare_presentation(
        settings,
        DummyDeviceConfig(),
        request=magazine_request("b" * 32),
        resolved_theme_context=None,
    )
    baseline = plugin._presentation_state_path().read_bytes()

    plugin.reconcile_presentation_receipt(settings, magazine_receipt("c" * 32))
    plugin.reconcile_presentation_receipt(
        settings,
        magazine_receipt("b" * 32, display="origin-display"),
    )
    assert plugin._presentation_state_path().read_bytes() == baseline

    plugin.reconcile_presentation_receipt(settings, magazine_receipt("b" * 32))
    committed = plugin._presentation_state_path().read_bytes()
    plugin.reconcile_presentation_receipt(
        settings,
        magazine_receipt("b" * 32, committed_at="2026-07-12T09:00:00+00:00"),
    )
    assert plugin._presentation_state_path().read_bytes() == committed


def test_magazine_instances_are_isolated_and_raw_identity_spoof_is_stateless(
    tmp_path,
    monkeypatch,
):
    plugin = make_banked_plugin(tmp_path)
    first = bound_magazine_settings(instance_uuid="magazine-one")
    second = bound_magazine_settings(instance_uuid="magazine-two")
    hydrate_magazine_bank(plugin, monkeypatch, first, runs=3)
    hydrate_magazine_bank(plugin, monkeypatch, second, runs=3)
    plugin.prepare_presentation(
        first,
        DummyDeviceConfig(),
        request=magazine_request("d" * 32, origin="origin-one"),
        resolved_theme_context=None,
    )
    plugin.prepare_presentation(
        second,
        DummyDeviceConfig(),
        request=magazine_request("e" * 32, origin="origin-two"),
        resolved_theme_context=None,
    )
    state = magazine_presentation_state(plugin)
    assert state["instance_profiles"]["magazine-one"] != state["instance_profiles"]["magazine-two"]
    assert magazine_profile(state, "magazine-one")["pending_selection"]["request_id"] == "d" * 32
    assert magazine_profile(state, "magazine-two")["pending_selection"]["request_id"] == "e" * 32

    baseline = magazine_cache_tree(tmp_path)
    spoofed = {
        "sources": "Alpha|https://magazineshop.us/collections/alpha",
        "_inkypi_presentation_instance_identity": {"instance_uuid": "magazine-one"},
        "dailyLibraryMode": "true",
    }
    monkeypatch.setattr(plugin, "_load_cover", lambda source, *_args, **_kwargs: cover_for_source(source))
    plugin.generate_image(spoofed, DummyDeviceConfig())
    assert magazine_cache_tree(tmp_path) == baseline


def test_magazine_pending_survives_restart_theme_and_date_profile_change(
    tmp_path,
    monkeypatch,
):
    plugin = make_banked_plugin(tmp_path)
    settings = bound_magazine_settings()
    hydrate_magazine_bank(plugin, monkeypatch, settings, runs=3)
    first = plugin.prepare_presentation(
        settings,
        DummyDeviceConfig(),
        request=magazine_request("f" * 32),
        resolved_theme_context=magazine_theme_context("day"),
    )
    old_state = magazine_presentation_state(plugin)
    old_fingerprint = old_state["instance_profiles"]["magazine-test-instance"]
    old_pending = magazine_profile(old_state)["pending_selection"]

    restarted = make_banked_plugin(tmp_path)
    monkeypatch.setattr(restarted, "_presentation_date_key", lambda _device: "2026-07-12")
    monkeypatch.setattr(restarted, "_load_cover", lambda *_args, **_kwargs: pytest.fail("restart used provider"))
    second = restarted.prepare_presentation(
        settings,
        DummyDeviceConfig(),
        request=magazine_request("f" * 32),
        resolved_theme_context=magazine_theme_context("night"),
    )
    assert magazine_profile(magazine_presentation_state(restarted))["pending_selection"] == old_pending
    assert first.image.info["inkypi_theme_mode"] == "day"
    assert second.image.info["inkypi_theme_mode"] == "night"

    monkeypatch.setattr(restarted, "_presentation_date_key", lambda _device: "2026-07-13")
    monkeypatch.setattr(restarted, "_load_cover", lambda source, *_args, **_kwargs: cover_for_source(source, suffix="next"))
    restarted.generate_image(settings, DummyDeviceConfig())
    changed = magazine_presentation_state(restarted)
    assert changed["instance_profiles"]["magazine-test-instance"] != old_fingerprint
    assert changed["profiles"][old_fingerprint]["pending_selection"] == old_pending
    restarted.reconcile_presentation_receipt(settings, magazine_receipt("f" * 32))
    committed = magazine_presentation_state(restarted)["profiles"][old_fingerprint]
    assert committed["pending_selection"] is None
    assert committed["last_applied_request_id"] == "f" * 32


def test_magazine_six_hour_library_and_twenty_hour_cover_ttls_are_independent(
    tmp_path,
    monkeypatch,
):
    plugin = make_banked_plugin(tmp_path)
    settings = bound_magazine_settings()
    now = [datetime(2026, 7, 12, 0, 0, tzinfo=timezone.utc)]
    calls = []
    monkeypatch.setattr(plugin, "_now_utc", lambda: now[0])
    monkeypatch.setattr(plugin, "_presentation_date_key", lambda _device: "2026-07-12")
    monkeypatch.setattr(magazine_module, "write_context", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        plugin,
        "_load_cover",
        lambda source, *_args, **_kwargs: calls.append(source["name"]) or cover_for_source(source),
    )

    for _index in range(3):
        plugin.generate_image(settings, DummyDeviceConfig())
    assert len(calls) == 18
    initial_names = set(calls)
    before_scan = magazine_profile(magazine_presentation_state(plugin))["library_refreshed_at"]
    calls.clear()
    now[0] += timedelta(hours=7)
    plugin.generate_image(settings, DummyDeviceConfig())
    assert len(calls) == 6
    assert initial_names.isdisjoint(calls)
    after_scan = magazine_profile(magazine_presentation_state(plugin))["library_refreshed_at"]
    assert after_scan != before_scan

    now[0] += timedelta(hours=14)
    calls.clear()
    with pytest.raises(RuntimeError, match="fresh prepared"):
        plugin.generate_image(settings, DummyDeviceConfig())
    assert len(calls) == 6
    assert DAILY_LIBRARY_REFRESH_INTERVAL == timedelta(hours=6)
    assert magazine_module.IMAGE_CACHE_TTL == timedelta(hours=20)


def test_magazine_data_attempts_are_bounded_by_six_and_wall_clock(
    tmp_path,
    monkeypatch,
):
    plugin = make_banked_plugin(tmp_path)
    settings = bound_magazine_settings()
    calls = []
    elapsed = {"value": 0.0}
    monkeypatch.setattr(plugin, "_now_utc", lambda: datetime(2026, 7, 12, tzinfo=timezone.utc))
    monkeypatch.setattr(plugin, "_presentation_date_key", lambda _device: "2026-07-12")
    monkeypatch.setattr(plugin, "_monotonic", lambda: elapsed["value"])

    deadlines = []

    def load_cover(source, *_args, deadline=None, **_kwargs):
        assert deadline is not None
        deadlines.append(deadline)
        calls.append(source["name"])
        remaining = max(0.0, deadline - elapsed["value"])
        elapsed["value"] += min(30.0, remaining)
        return cover_for_source(source)

    monkeypatch.setattr(plugin, "_load_cover", load_cover)
    plugin.generate_image(settings, DummyDeviceConfig())
    state = magazine_presentation_state(plugin)

    assert 0 < len(calls) <= 6
    assert len(calls) == 3
    assert set(deadlines) == {75.0}
    assert elapsed["value"] <= 75.0
    assert magazine_profile(state)["refill_in_progress"] is True
    assert magazine_module.DATA_PROVIDER_ATTEMPT_LIMIT == 6
    assert magazine_module.DATA_HYDRATION_TIME_LIMIT_SECONDS == 75


def test_magazine_data_recovers_exact_protected_current_or_fails_byte_stable(
    tmp_path,
    monkeypatch,
):
    plugin = make_banked_plugin(tmp_path)
    settings = bound_magazine_settings()
    hydrate_magazine_bank(plugin, monkeypatch, settings, runs=3)
    state = magazine_presentation_state(plugin)
    profile = magazine_profile(state)
    current = profile["current_selection"]
    record = next(item for item in profile["records"] if item["record_key"] == current["record_keys"][0])
    media_path = plugin._presentation_media_dir() / f"{record['media_key']}.png"
    media_path.unlink()
    recovered_urls = []
    scan_urls = []
    monkeypatch.setattr(
        plugin,
        "_load_cover",
        lambda source, *_args, **_kwargs: scan_urls.append(source["url"])
        or cover_for_source(source),
    )
    monkeypatch.setattr(
        plugin,
        "_download_candidate_image",
        lambda candidate, _dimensions, deadline=None: recovered_urls.append(candidate["url"])
        or Image.new("RGB", (240, 420), "green"),
    )

    plugin.generate_image(settings, DummyDeviceConfig())
    assert magazine_profile(magazine_presentation_state(plugin))["current_selection"] == current
    assert recovered_urls == [record["image_url"]]
    assert len(scan_urls) == 6

    media_path.unlink()
    baseline = plugin._presentation_state_path().read_bytes()
    monkeypatch.setattr(
        plugin,
        "_download_candidate_image",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("offline")),
    )
    with pytest.raises(RuntimeError, match="protected|recover"):
        plugin.generate_image(settings, DummyDeviceConfig())
    assert plugin._presentation_state_path().read_bytes() == baseline


def test_magazine_stale_and_local_fallback_provenance_do_not_claim_fresh(
    tmp_path,
    monkeypatch,
):
    from plugins.magazine_covers.presentation_bank import MagazinePresentationBank

    plugin = make_banked_plugin(tmp_path)
    settings = bound_magazine_settings()
    hydrate_magazine_bank(plugin, monkeypatch, settings, runs=3)
    state = magazine_presentation_state(plugin)
    profile = magazine_profile(state)
    record = next(item for item in profile["records"] if item["record_key"] in profile["current_selection"]["record_keys"])
    record["fetched_at"] = (datetime(2026, 7, 12, 9, 30, tzinfo=timezone.utc) - timedelta(hours=21)).isoformat()
    plugin._presentation_state_path().write_text(json.dumps(state), encoding="utf-8")
    bank = MagazinePresentationBank.from_profile(
        plugin._presentation_state_path(),
        plugin._presentation_media_dir(),
        state["instance_profiles"]["magazine-test-instance"],
        profile,
    )
    assert bank.record_provenance(record, now=datetime(2026, 7, 12, 9, 30, tzinfo=timezone.utc)) == "stale_cache"

    monkeypatch.setattr(plugin, "_load_cover", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("offline")))
    runtime_last_good = tmp_path / "runtime-last-good.png"
    runtime_last_good.write_bytes(b"last-good")
    promoted = []
    try:
        generated = plugin.generate_image(settings, DummyDeviceConfig())
        promoted.append(generated)
    except RuntimeError:
        pass
    assert promoted == []
    assert runtime_last_good.read_bytes() == b"last-good"

    preview_plugin = make_banked_plugin(tmp_path / "preview")
    monkeypatch.setattr(preview_plugin, "_load_cover", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("offline")))
    fallback = preview_plugin.generate_image(
        {"sources": "Alpha|https://magazineshop.us/collections/alpha"},
        DummyDeviceConfig(),
    )
    assert fallback.info["inkypi_source_provenance"] == "local_fallback"
    assert not preview_plugin._presentation_state_path().exists()


def test_magazine_library_due_does_not_advance_when_deadline_prevents_scan(
    tmp_path,
    monkeypatch,
):
    plugin = make_banked_plugin(tmp_path)
    settings = bound_magazine_settings()
    now = [datetime(2026, 7, 12, 0, 0, tzinfo=timezone.utc)]
    monkeypatch.setattr(plugin, "_now_utc", lambda: now[0])
    monkeypatch.setattr(plugin, "_presentation_date_key", lambda _device: "2026-07-12")
    monkeypatch.setattr(
        plugin,
        "_load_cover",
        lambda source, *_args, **_kwargs: cover_for_source(source),
    )
    for _index in range(3):
        plugin.generate_image(settings, DummyDeviceConfig())
    before = magazine_profile(magazine_presentation_state(plugin))["library_refreshed_at"]
    now[0] += timedelta(hours=7)
    clock = {"calls": 0}

    def expired_clock():
        clock["calls"] += 1
        return 0.0 if clock["calls"] == 1 else 76.0

    monkeypatch.setattr(plugin, "_monotonic", expired_clock)
    monkeypatch.setattr(
        plugin,
        "_load_cover",
        lambda *_args, **_kwargs: pytest.fail("expired scan issued provider request"),
    )
    with pytest.raises(RuntimeError, match="deadline|fresh prepared|scan"):
        plugin.generate_image(settings, DummyDeviceConfig())
    after = magazine_profile(magazine_presentation_state(plugin))["library_refreshed_at"]
    assert after == before


def test_magazine_download_uses_remaining_absolute_deadline_for_request_and_stream(
    tmp_path,
    monkeypatch,
):
    from security.ssrf import SSRFPolicy

    plugin = make_banked_plugin(tmp_path)
    source = {"name": "Shop", "url": "https://magazineshop.us/collections/shop"}
    url = "https://cdn.shopify.com/cover.jpg"
    clock = {"value": 10.0}
    policy = SSRFPolicy(
        resolver=magazine_resolver_for({"cdn.shopify.com": "93.184.216.34"})
    )
    response = MagazineFakeHttpResponse(200, url=url, payload=magazine_png_bytes())
    session = MagazineFakeRedirectSession([response])
    original_request = session.request

    def timed_request(method, request_url, **kwargs):
        assert kwargs["timeout"][0] <= 10.0
        assert kwargs["timeout"][1] <= 10.0
        clock["value"] = 19.0
        return original_request(method, request_url, **kwargs)

    session.request = timed_request
    monkeypatch.setattr(plugin, "_monotonic", lambda: clock["value"])
    monkeypatch.setattr(magazine_module, "get_ssrf_policy", lambda: policy, raising=False)
    monkeypatch.setattr(
        magazine_module,
        "get_http_client",
        lambda: MagazineFakeRedirectClient(session),
        raising=False,
    )

    payload = plugin._download_provider_bytes(
        url,
        source=source,
        kind="image",
        max_bytes=1024,
        timeout=35,
        deadline=20.0,
    )
    assert payload == magazine_png_bytes()
    assert response.closed is True

    clock["value"] = 20.0
    with pytest.raises(RuntimeError, match="deadline"):
        plugin._download_provider_bytes(
            url,
            source=source,
            kind="image",
            max_bytes=1024,
            timeout=35,
            deadline=20.0,
        )
    assert len(session.calls) == 1


def test_magazine_connect_and_read_timeouts_share_one_remaining_deadline(
    tmp_path,
    monkeypatch,
):
    from security.ssrf import SSRFPolicy

    plugin = make_banked_plugin(tmp_path)
    source = {"name": "Shop", "url": "https://magazineshop.us/collections/shop"}
    url = "https://cdn.shopify.com/cover.jpg"
    clock = {"value": 10.0}
    policy = SSRFPolicy(
        resolver=magazine_resolver_for({"cdn.shopify.com": "93.184.216.34"})
    )
    response = MagazineFakeHttpResponse(200, url=url, payload=magazine_png_bytes())
    session = MagazineFakeRedirectSession([response])
    original_request = session.request

    def consume_connect_and_read(method, request_url, **kwargs):
        connect_timeout, read_timeout = kwargs["timeout"]
        clock["value"] += connect_timeout + read_timeout
        return original_request(method, request_url, **kwargs)

    session.request = consume_connect_and_read
    monkeypatch.setattr(plugin, "_monotonic", lambda: clock["value"])
    monkeypatch.setattr(magazine_module, "get_ssrf_policy", lambda: policy, raising=False)
    monkeypatch.setattr(
        magazine_module,
        "get_http_client",
        lambda: MagazineFakeRedirectClient(session),
        raising=False,
    )

    with pytest.raises(RuntimeError, match="deadline"):
        plugin._download_provider_bytes(
            url,
            source=source,
            kind="image",
            max_bytes=1024,
            timeout=35,
            deadline=20.0,
        )

    assert clock["value"] <= 20.0
    assert response.closed is True


def test_magazine_protected_recovery_obeys_same_absolute_data_deadline(
    tmp_path,
    monkeypatch,
):
    plugin = make_banked_plugin(tmp_path)
    settings = bound_magazine_settings()
    hydrate_magazine_bank(plugin, monkeypatch, settings, runs=3)
    state = magazine_presentation_state(plugin)
    profile = magazine_profile(state)
    record_key = profile["current_selection"]["record_keys"][0]
    record = next(item for item in profile["records"] if item["record_key"] == record_key)
    (plugin._presentation_media_dir() / f"{record['media_key']}.png").unlink()
    clock = {"calls": 0}

    def expired_clock():
        clock["calls"] += 1
        return 0.0 if clock["calls"] == 1 else 76.0

    monkeypatch.setattr(plugin, "_monotonic", expired_clock)
    monkeypatch.setattr(
        plugin,
        "_download_candidate_image",
        lambda *_args, **_kwargs: pytest.fail("expired protected recovery issued request"),
    )
    baseline = plugin._presentation_state_path().read_bytes()
    with pytest.raises(RuntimeError, match="deadline|protected"):
        plugin.generate_image(settings, DummyDeviceConfig())
    assert plugin._presentation_state_path().read_bytes() == baseline


def test_magazine_redirect_to_private_and_foreign_final_authority_are_rejected(
    tmp_path,
    monkeypatch,
):
    from security.ssrf import SSRFPolicy, UnsafeTarget

    plugin = make_banked_plugin(tmp_path)
    source = {"name": "Shop", "url": "https://magazineshop.us/collections/shop"}
    start = "https://magazineshop.us/start"
    private = "https://private.magazineshop.us/secret"
    policy = SSRFPolicy(
        resolver=magazine_resolver_for(
            {
                "magazineshop.us": "93.184.216.34",
                "private.magazineshop.us": "169.254.169.254",
            }
        )
    )
    session = MagazineFakeRedirectSession(
        [MagazineFakeHttpResponse(302, url=start, headers={"Location": private})]
    )
    monkeypatch.setattr(magazine_module, "get_ssrf_policy", lambda: policy, raising=False)
    monkeypatch.setattr(magazine_module, "get_http_client", lambda: MagazineFakeRedirectClient(session), raising=False)

    with pytest.raises(UnsafeTarget, match="metadata|non-public"):
        plugin._download_provider_bytes(start, source=source, kind="html", max_bytes=1024, timeout=5)
    assert len(session.calls) == 1

    foreign = "https://evil.example.org/final.jpg"
    public_policy = SSRFPolicy(
        resolver=magazine_resolver_for(
            {
                "cdn.shopify.com": "93.184.216.34",
                "evil.example.org": "93.184.216.35",
            }
        )
    )
    foreign_response = MagazineFakeHttpResponse(200, url=foreign, payload=magazine_png_bytes())
    foreign_session = MagazineFakeRedirectSession([foreign_response])
    monkeypatch.setattr(magazine_module, "get_ssrf_policy", lambda: public_policy, raising=False)
    monkeypatch.setattr(
        magazine_module,
        "get_http_client",
        lambda: MagazineFakeRedirectClient(foreign_session),
        raising=False,
    )
    with pytest.raises(RuntimeError, match="authority|allowed"):
        plugin._download_provider_bytes(
            "https://cdn.shopify.com/start.jpg",
            source=source,
            kind="image",
            max_bytes=1024,
            timeout=5,
        )
    assert foreign_response.closed is True


@pytest.mark.parametrize(
    "unsafe_url",
    [
        "http://127.0.0.1/cover.jpg",
        "https://169.254.169.254/cover.jpg",
        "https://[::1]/cover.jpg",
    ],
)
def test_magazine_bank_rejects_literal_private_media_urls(tmp_path, unsafe_url):
    from plugins.magazine_covers.presentation_bank import MagazinePresentationBank

    bank = MagazinePresentationBank(
        tmp_path / "presentation-state.json",
        tmp_path / "presentation-media",
        fingerprint="a" * 64,
        base_fingerprint="b" * 64,
        profile_settings_key="c" * 64,
        instance_uuid="instance",
        date_key="2026-07-12",
    )
    source = {"name": "Alpha", "url": "https://magazineshop.us/collections/alpha"}
    cover = {**cover_for_source(source), "image_url": unsafe_url}

    with pytest.raises(RuntimeError, match="public|private|authority|network"):
        bank.ingest(profile=bank.load_for_data()[1], source=source, cover=cover, image=cover["image"])


def test_magazine_download_never_inherits_browser_private_host_allowlist(
    tmp_path,
    monkeypatch,
):
    from security.ssrf import SSRFPolicy

    plugin = make_banked_plugin(tmp_path)
    source = {"name": "Shop", "url": "https://magazineshop.us/collections/shop"}
    start = "https://cdn.shopify.com/private.jpg"
    policy = SSRFPolicy(
        resolver=magazine_resolver_for({"cdn.shopify.com": "127.0.0.1"}),
        allowed_private_hosts=("cdn.shopify.com",),
    )
    session = MagazineFakeRedirectSession(
        [MagazineFakeHttpResponse(200, url=start, payload=magazine_png_bytes())]
    )
    monkeypatch.setattr(magazine_module, "get_ssrf_policy", lambda: policy, raising=False)
    monkeypatch.setattr(
        magazine_module,
        "get_http_client",
        lambda: MagazineFakeRedirectClient(session),
        raising=False,
    )

    with pytest.raises(RuntimeError, match="public|private|non-public"):
        plugin._download_provider_bytes(
            start,
            source=source,
            kind="image",
            max_bytes=1024,
            timeout=5,
        )
    assert session.calls == []


def test_magazine_presentation_bank_limits_media_state_and_reparse_paths(
    tmp_path,
):
    from plugins.magazine_covers import presentation_bank

    assert presentation_bank.READY_TARGET == 18
    assert presentation_bank.REFILL_THRESHOLD == 6
    assert presentation_bank.MAX_PROFILES == 64
    assert presentation_bank.MAX_SEEN_SOURCES == 5000
    assert presentation_bank.MAX_STATE_BYTES == 4 * 1024 * 1024
    assert presentation_bank.MEDIA_MAX_AGE_SECONDS == 7 * 24 * 60 * 60
    assert presentation_bank.MEDIA_MAX_FILES == 48
    assert presentation_bank.MEDIA_MAX_BYTES == 128 * 1024 * 1024
    assert presentation_bank.MEDIA_MAX_OBJECT_BYTES == 16 * 1024 * 1024
    assert presentation_bank.MEDIA_MAX_DIMENSION == 8192
    assert presentation_bank.MEDIA_MAX_PIXELS == 32_000_000

    class FakeStat:
        st_mode = 0o040000
        st_file_attributes = getattr(__import__("stat"), "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)

    assert presentation_bank._is_link_like(FakeStat()) is True


def test_magazine_bank_rejects_oversize_dimension_media_symlink_and_state_symlink(
    tmp_path,
):
    from plugins.magazine_covers import presentation_bank

    bank = presentation_bank.MagazinePresentationBank(
        tmp_path / "presentation-state.json",
        tmp_path / "presentation-media",
        fingerprint="a" * 64,
        base_fingerprint="b" * 64,
        profile_settings_key="c" * 64,
        instance_uuid="instance",
        date_key="2026-07-12",
    )
    document, profile = bank.load_for_data()
    source = {"name": "Alpha", "url": "https://magazineshop.us/collections/alpha"}
    with pytest.raises(RuntimeError, match="dimension|safety"):
        bank.ingest(
            profile,
            source,
            cover_for_source(source),
            Image.new("RGB", (presentation_bank.MEDIA_MAX_DIMENSION + 1, 1), "red"),
        )

    record = bank.ingest(profile, source, cover_for_source(source), Image.new("RGB", (40, 60), "red"))
    bank.save(document)
    media_path = bank.media.path(record["media_key"], suffix=".png")
    media_path.write_bytes(b"x" * (presentation_bank.MEDIA_MAX_OBJECT_BYTES + 1))
    with pytest.raises(RuntimeError, match="budget|media"):
        bank.load_media(record)

    outside = tmp_path / "outside.png"
    outside.write_bytes(b"outside")
    media_path.unlink()
    try:
        media_path.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks unavailable")
    with pytest.raises(RuntimeError, match="regular|media|symlink"):
        bank.load_media(record)
    assert outside.read_bytes() == b"outside"

    state_outside = tmp_path / "outside.json"
    state_outside.write_text('{"sentinel": true}', encoding="utf-8")
    state_path = tmp_path / "linked-state.json"
    try:
        state_path.symlink_to(state_outside)
    except OSError:
        pytest.skip("symlinks unavailable")
    linked_bank = presentation_bank.MagazinePresentationBank(
        state_path,
        tmp_path / "other-media",
        fingerprint="d" * 64,
        base_fingerprint="e" * 64,
        profile_settings_key="f" * 64,
        instance_uuid="linked",
        date_key="2026-07-12",
    )
    with pytest.raises(RuntimeError, match="state|regular|symlink"):
        linked_bank.load_for_data()
    assert json.loads(state_outside.read_text(encoding="utf-8")) == {"sentinel": True}


def test_magazine_state_parent_symlink_is_rejected_and_atomic_mode_is_private(tmp_path):
    from plugins.magazine_covers import presentation_bank

    outside = tmp_path / "outside-root"
    outside.mkdir()
    linked_root = tmp_path / "linked-root"
    try:
        linked_root.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks unavailable")
    linked_bank = presentation_bank.MagazinePresentationBank(
        linked_root / "presentation-state.json",
        linked_root / "presentation-media",
        fingerprint="a" * 64,
        base_fingerprint="b" * 64,
        profile_settings_key="c" * 64,
        instance_uuid="instance",
        date_key="2026-07-12",
    )
    document, _profile = linked_bank.load_for_data()
    with pytest.raises(RuntimeError, match="root|parent|directory|reparse|unsafe"):
        linked_bank.save(document)
    assert not (outside / "presentation-state.json").exists()

    bank = presentation_bank.MagazinePresentationBank(
        tmp_path / "private" / "presentation-state.json",
        tmp_path / "private" / "presentation-media",
        fingerprint="d" * 64,
        base_fingerprint="e" * 64,
        profile_settings_key="f" * 64,
        instance_uuid="private",
        date_key="2026-07-12",
    )
    private_document, _private_profile = bank.load_for_data()
    bank.save(private_document)
    if os.name == "posix":
        assert bank.state_path.stat().st_mode & 0o777 == 0o600


def test_magazine_media_cleanup_is_bounded_and_protects_provider_cache(tmp_path):
    from plugins.magazine_covers import presentation_bank

    provider_cache = tmp_path / "provider-cache.json"
    provider_cache.write_bytes(b"provider")
    bank = presentation_bank.MagazinePresentationBank(
        tmp_path / "presentation-state.json",
        tmp_path / "presentation-media",
        fingerprint="a" * 64,
        base_fingerprint="b" * 64,
        profile_settings_key="c" * 64,
        instance_uuid="instance",
        date_key="2026-07-12",
    )
    for index in range(presentation_bank.MEDIA_MAX_FILES + 4):
        bank.media.put_bytes(f"{index:064x}", b"small", suffix=".png")

    assert len(list(bank.media_dir.glob("*.png"))) <= presentation_bank.MEDIA_MAX_FILES
    assert provider_cache.read_bytes() == b"provider"


def test_magazine_protected_current_and_pending_survive_full_cache_without_touch_heuristic(
    tmp_path,
    monkeypatch,
):
    from plugins.magazine_covers import presentation_bank

    bank = presentation_bank.MagazinePresentationBank(
        tmp_path / "presentation-state.json",
        tmp_path / "presentation-media",
        fingerprint="a" * 64,
        base_fingerprint="b" * 64,
        profile_settings_key="c" * 64,
        instance_uuid="instance",
        date_key="2026-07-12",
    )
    document, profile = bank.load_for_data()
    sources = [
        {"name": "Current", "url": "https://magazineshop.us/collections/current"},
        {"name": "Pending", "url": "https://magazineshop.us/collections/pending"},
    ]
    records = [
        bank.ingest(profile, source, cover_for_source(source), Image.new("RGB", (40, 60), color))
        for source, color in zip(sources, ("red", "blue"))
    ]
    profile["current_selection"] = {
        "record_keys": [records[0]["record_key"]],
        "request_id": None,
        "date_key": "2026-07-12",
        "layout": "single",
        "reset_seen": False,
    }
    profile["pending_selection"] = {
        "record_keys": [records[1]["record_key"]],
        "request_id": "a" * 32,
        "origin_display_commit_id": "origin",
        "requested_at": "2026-07-12T10:00:00+00:00",
        "date_key": "2026-07-12",
        "layout": "single",
        "reset_seen": False,
    }
    bank.save(document)
    protected_paths = [bank.media.path(record["media_key"], suffix=".png") for record in records]
    old = datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp()
    for path in protected_paths:
        os.utime(path, (old, old))
    for index in range(presentation_bank.MEDIA_MAX_FILES - len(records)):
        bank.media.put_bytes(f"{index + 100:064x}", b"filler", suffix=".png")
    monkeypatch.setattr(Path, "touch", lambda *_args, **_kwargs: None)
    new_source = {"name": "New", "url": "https://magazineshop.us/collections/new"}

    bank.ingest(
        profile,
        new_source,
        cover_for_source(new_source),
        Image.new("RGB", (40, 60), "green"),
    )

    assert all(path.is_file() for path in protected_paths)
    assert len(list(bank.media_dir.glob("*.png"))) <= presentation_bank.MEDIA_MAX_FILES


def test_magazine_admission_fails_closed_when_all_48_media_are_protected(
    tmp_path,
    monkeypatch,
):
    from plugins.magazine_covers import presentation_bank

    bank = presentation_bank.MagazinePresentationBank(
        tmp_path / "presentation-state.json",
        tmp_path / "presentation-media",
        fingerprint="a" * 64,
        base_fingerprint="b" * 64,
        profile_settings_key="c" * 64,
        instance_uuid="instance",
        date_key="2026-07-12",
    )
    protected_keys = [f"{index:064x}" for index in range(presentation_bank.MEDIA_MAX_FILES)]
    for media_key in protected_keys:
        bank.media.put_bytes(media_key, b"protected", suffix=".png")
    profiles = {}
    for group in range(16):
        records = []
        record_keys = []
        for offset in range(3):
            media_key = protected_keys[group * 3 + offset]
            record_key = f"{group * 3 + offset + 1000:064x}"
            record_keys.append(record_key)
            records.append({"record_key": record_key, "media_key": media_key})
        profiles[f"{group + 2000:064x}"] = {
            "records": records,
            "current_selection": {"record_keys": record_keys},
            "pending_selection": None,
        }
    bank._loaded_document = {"profiles": profiles}
    monkeypatch.setattr(Path, "touch", lambda *_args, **_kwargs: None)

    with pytest.raises(RuntimeError, match="protected|budget|capacity"):
        bank.media.put_bytes("f" * 64, b"new", suffix=".png")
    assert all(bank.media.path(key, suffix=".png").is_file() for key in protected_keys)


def test_magazine_admission_enforces_namespace_and_global_byte_budgets(
    tmp_path,
    monkeypatch,
):
    from plugins.magazine_covers import presentation_bank

    monkeypatch.setattr(presentation_bank, "MEDIA_MAX_BYTES", 100)
    monkeypatch.setattr(presentation_bank, "GLOBAL_MEDIA_ROOT_MAX_BYTES", 120)
    provider_cache = tmp_path / "provider-cache.bin"
    provider_cache.write_bytes(b"p" * 80)
    bank = presentation_bank.MagazinePresentationBank(
        tmp_path / "presentation-state.json",
        tmp_path / "presentation-media",
        fingerprint="a" * 64,
        base_fingerprint="b" * 64,
        profile_settings_key="c" * 64,
        instance_uuid="instance",
        date_key="2026-07-12",
    )
    protected_key = "1" * 64
    protected_record_key = "2" * 64
    bank.media.put_bytes(protected_key, b"x" * 40, suffix=".png")
    bank._loaded_document = {
        "profiles": {
            "a" * 64: {
                "records": [
                    {
                        "record_key": protected_record_key,
                        "media_key": protected_key,
                    }
                ],
                "current_selection": {"record_keys": [protected_record_key]},
                "pending_selection": None,
            }
        }
    }

    with pytest.raises(RuntimeError, match="global|budget|protected|capacity"):
        bank.media.put_bytes("3" * 64, b"y" * 50, suffix=".png")
    assert bank.media.path(protected_key, suffix=".png").read_bytes() == b"x" * 40
    assert provider_cache.read_bytes() == b"p" * 80


def test_magazine_admission_permission_failure_is_byte_and_count_atomic(
    tmp_path,
    monkeypatch,
):
    from plugins.magazine_covers import presentation_bank

    monkeypatch.setattr(presentation_bank, "MEDIA_MAX_FILES", 1)
    bank = presentation_bank.MagazinePresentationBank(
        tmp_path / "presentation-state.json",
        tmp_path / "presentation-media",
        fingerprint="a" * 64,
        base_fingerprint="b" * 64,
        profile_settings_key="c" * 64,
        instance_uuid="instance",
        date_key="2026-07-12",
    )
    old_key = "1" * 64
    new_key = "2" * 64
    old_path = bank.media.put_bytes(old_key, b"old", suffix=".png")
    new_path = bank.media.path(new_key, suffix=".png")
    before = {
        path.name: path.read_bytes()
        for path in bank.media_dir.iterdir()
        if path.is_file()
    }
    original_unlink = Path.unlink

    def deny_old_victim(path, *args, **kwargs):
        if Path(path) == old_path:
            raise PermissionError("victim is locked")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", deny_old_victim)
    with pytest.raises(RuntimeError, match="admit|victim|atomic|safe"):
        bank.media.put_bytes(new_key, b"new", suffix=".png")

    after = {
        path.name: path.read_bytes()
        for path in bank.media_dir.iterdir()
        if path.is_file()
    }
    assert new_path.exists() is False
    assert old_path.read_bytes() == b"old"
    assert after == before
    assert sum(len(payload) for payload in after.values()) == sum(
        len(payload) for payload in before.values()
    )
    assert list(tmp_path.glob(".magazine-admission-*")) == []


def test_magazine_namespace_budget_counts_anomalous_regular_files(tmp_path):
    from plugins.magazine_covers import presentation_bank

    media_dir = tmp_path / "presentation-media"
    media_dir.mkdir(parents=True)
    (media_dir / "orphan-large.bin").write_bytes(b"x" * 1024 * 1024)
    for index in range(presentation_bank.MEDIA_MAX_FILES - 1):
        (media_dir / f"unexpected-{index}.cache").write_bytes(b"junk")
    bank = presentation_bank.MagazinePresentationBank(
        tmp_path / "presentation-state.json",
        media_dir,
        fingerprint="a" * 64,
        base_fingerprint="b" * 64,
        profile_settings_key="c" * 64,
        instance_uuid="instance",
        date_key="2026-07-12",
    )

    bank.media.put_bytes("f" * 64, b"valid", suffix=".png")

    ordinary = []
    for path in media_dir.iterdir():
        info = path.lstat()
        if stat.S_ISREG(info.st_mode):
            ordinary.append(info)
    assert len(ordinary) <= presentation_bank.MEDIA_MAX_FILES
    assert sum(info.st_size for info in ordinary) <= presentation_bank.MEDIA_MAX_BYTES


def test_magazine_admission_restores_already_deleted_victim_on_later_failure(
    tmp_path,
    monkeypatch,
):
    from plugins.magazine_covers import presentation_bank

    monkeypatch.setattr(presentation_bank, "MEDIA_MAX_FILES", 1)
    media_dir = tmp_path / "presentation-media"
    media_dir.mkdir(parents=True)
    first = media_dir / "first.bin"
    second = media_dir / "second.bin"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    old = datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp()
    os.utime(first, (old, old))
    os.utime(second, (old + 1, old + 1))
    before = {path.name: path.read_bytes() for path in media_dir.iterdir()}
    bank = presentation_bank.MagazinePresentationBank(
        tmp_path / "presentation-state.json",
        media_dir,
        fingerprint="a" * 64,
        base_fingerprint="b" * 64,
        profile_settings_key="c" * 64,
        instance_uuid="instance",
        date_key="2026-07-12",
    )
    original_unlink = Path.unlink

    def deny_second_victim(path, *args, **kwargs):
        if Path(path) == second:
            raise PermissionError("second victim is locked")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", deny_second_victim)
    with pytest.raises(RuntimeError, match="atomic"):
        bank.media.put_bytes("f" * 64, b"new", suffix=".png")

    after = {path.name: path.read_bytes() for path in media_dir.iterdir()}
    assert after == before
    assert list(tmp_path.glob(".magazine-admission-*")) == []


def test_magazine_namespace_byte_budget_counts_anomalous_regular_file(
    tmp_path,
    monkeypatch,
):
    from plugins.magazine_covers import presentation_bank

    monkeypatch.setattr(presentation_bank, "MEDIA_MAX_BYTES", 100)
    media_dir = tmp_path / "presentation-media"
    media_dir.mkdir(parents=True)
    anomalous = media_dir / "large-unmanaged.cache"
    anomalous.write_bytes(b"x" * 90)
    bank = presentation_bank.MagazinePresentationBank(
        tmp_path / "presentation-state.json",
        media_dir,
        fingerprint="a" * 64,
        base_fingerprint="b" * 64,
        profile_settings_key="c" * 64,
        instance_uuid="instance",
        date_key="2026-07-12",
    )

    bank.media.put_bytes("e" * 64, b"y" * 20, suffix=".png")

    ordinary_bytes = sum(
        path.lstat().st_size
        for path in media_dir.iterdir()
        if stat.S_ISREG(path.lstat().st_mode)
    )
    assert ordinary_bytes <= presentation_bank.MEDIA_MAX_BYTES


def test_magazine_restarted_bank_preserves_current_and_pending_media(tmp_path):
    from plugins.magazine_covers import presentation_bank

    kwargs = {
        "fingerprint": "a" * 64,
        "base_fingerprint": "b" * 64,
        "profile_settings_key": "c" * 64,
        "instance_uuid": "instance",
        "date_key": "2026-07-12",
    }
    state_path = tmp_path / "presentation-state.json"
    media_dir = tmp_path / "presentation-media"
    bank = presentation_bank.MagazinePresentationBank(state_path, media_dir, **kwargs)
    document, profile = bank.load_for_data()
    sources = [
        {"name": "Current", "url": "https://magazineshop.us/collections/current"},
        {"name": "Pending", "url": "https://magazineshop.us/collections/pending"},
    ]
    records = [
        bank.ingest(profile, source, cover_for_source(source), Image.new("RGB", (40, 60), color))
        for source, color in zip(sources, ("red", "blue"))
    ]
    profile["current_selection"] = {
        "record_keys": [records[0]["record_key"]],
        "request_id": None,
        "date_key": "2026-07-12",
        "layout": "single",
        "reset_seen": False,
    }
    profile["pending_selection"] = {
        "record_keys": [records[1]["record_key"]],
        "request_id": "a" * 32,
        "origin_display_commit_id": "origin",
        "requested_at": "2026-07-12T10:00:00+00:00",
        "date_key": "2026-07-12",
        "layout": "single",
        "reset_seen": False,
    }
    bank.save(document)
    protected_paths = [bank.media.path(record["media_key"], suffix=".png") for record in records]
    old = datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp()
    for path in protected_paths:
        os.utime(path, (old, old))

    restarted = presentation_bank.MagazinePresentationBank(state_path, media_dir, **kwargs)
    restarted.load_for_data()
    for index in range(presentation_bank.MEDIA_MAX_FILES - len(records)):
        restarted.media.put_bytes(f"{index + 100:064x}", b"filler", suffix=".png")
    restarted.media.put_bytes("f" * 64, b"new", suffix=".png")

    assert all(path.is_file() for path in protected_paths)
    assert len([path for path in media_dir.iterdir() if path.is_file()]) <= presentation_bank.MEDIA_MAX_FILES


def test_magazine_theme_only_changes_chrome_without_provider_selection_or_state_write(
    tmp_path,
    monkeypatch,
):
    plugin = make_banked_plugin(tmp_path)
    plugin.config["_manifest"] = SimpleNamespace(
        theme=SimpleNamespace(presentation="media")
    )
    settings = bound_magazine_settings()
    hydrate_magazine_bank(plugin, monkeypatch, settings, runs=3)
    baseline = plugin._presentation_state_path().read_bytes()
    before_selection = magazine_profile(magazine_presentation_state(plugin))["current_selection"]
    monkeypatch.setattr(plugin, "_load_cover", lambda *_args, **_kwargs: pytest.fail("theme used provider"))
    monkeypatch.setattr(plugin, "_fetch_text", lambda *_args, **_kwargs: pytest.fail("theme fetched HTML"))
    monkeypatch.setattr(plugin, "_download_candidate_image", lambda *_args, **_kwargs: pytest.fail("theme downloaded"))

    day = plugin.render_themed_image(
        settings,
        DummyDeviceConfig(),
        theme_render_only=True,
        resolved_theme_context=magazine_theme_context("day"),
    )
    night = plugin.render_themed_image(
        settings,
        DummyDeviceConfig(),
        theme_render_only=True,
        resolved_theme_context=magazine_theme_context("night"),
    )
    after = magazine_presentation_state(plugin)

    assert day.info["inkypi_theme_mode"] == "day"
    assert night.info["inkypi_theme_mode"] == "night"
    assert day.tobytes() != night.tobytes()
    assert plugin._presentation_state_path().read_bytes() == baseline
    assert magazine_profile(after)["current_selection"] == before_selection


def test_magazine_theme_only_reports_stale_record_truthfully_without_provider(
    tmp_path,
    monkeypatch,
):
    plugin = make_banked_plugin(tmp_path)
    settings = bound_magazine_settings()
    now = datetime(2026, 7, 12, 9, 30, tzinfo=timezone.utc)
    hydrate_magazine_bank(plugin, monkeypatch, settings, runs=3)
    state = magazine_presentation_state(plugin)
    profile = magazine_profile(state)
    for record in profile["records"]:
        if record["record_key"] in profile["current_selection"]["record_keys"]:
            record["fetched_at"] = (now - timedelta(hours=21)).isoformat()
    plugin._presentation_state_path().write_text(json.dumps(state), encoding="utf-8")
    monkeypatch.setattr(plugin, "_now_utc", lambda: now)
    monkeypatch.setattr(plugin, "_load_cover", lambda *_args, **_kwargs: pytest.fail("theme used provider"))
    monkeypatch.setattr(plugin, "_download_candidate_image", lambda *_args, **_kwargs: pytest.fail("theme downloaded"))

    image = plugin.generate_image(
        {**settings, "_theme_render_only": True},
        DummyDeviceConfig(),
    )

    assert image.info["inkypi_source_provenance"] == "stale_cache"
    assert plugin._presentation_state_path().read_bytes() == json.dumps(state).encode("utf-8")


def test_magazine_stateless_preview_leaves_entire_plugin_cache_tree_unchanged(
    tmp_path,
    monkeypatch,
):
    plugin = make_banked_plugin(tmp_path)
    settings = {
        "sources": "Alpha|https://magazineshop.us/collections/alpha",
        "rotationMode": "random",
        "fitMode": "contain",
        "showSourceLabel": "false",
        "dailyLibraryMode": "true",
    }
    sentinel = tmp_path / "provider-sentinel.json"
    sentinel.parent.mkdir(parents=True, exist_ok=True)
    sentinel.write_bytes(b"provider")
    baseline = magazine_cache_tree(tmp_path)
    monkeypatch.setattr(plugin, "_load_cover", lambda source, *_args, **_kwargs: cover_for_source(source))

    image = plugin.generate_image(settings, DummyDeviceConfig())

    assert image.size == (800, 480)
    assert magazine_cache_tree(tmp_path) == baseline
