import json
import os
import socket
import sys
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.daily_art import daily_art as daily_art_module  # noqa: E402
from plugins.daily_art.daily_art import ArtworkCandidate, DailyArt  # noqa: E402
from plugins.base_plugin.presentation import (  # noqa: E402
    PresentationMode,
    PresentationRequestContext,
    bind_presentation_instance_identity,
)
from runtime.runtime_state import PresentationCommitReceipt  # noqa: E402


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


def bound_settings(*, cadence="every_refresh", instance_uuid="daily-art-test-instance", **overrides):
    settings = {
        "rotationCadence": cadence,
        "sourceMode": "open",
        "layoutMode": "single",
        "galleryCount": 3,
        "maxAttempts": 40,
    }
    settings.update(overrides)
    return bind_presentation_instance_identity(settings, instance_uuid)


def art_candidate(index, *, portrait=True):
    suffix = "portrait" if portrait else "landscape"
    source = "met" if index % 2 == 0 else "artic"
    if source == "met":
        image_url = f"https://images.metmuseum.org/{suffix}/{index}.jpg"
        page_url = f"https://www.metmuseum.org/art/{index}"
    else:
        image_url = f"https://www.artic.edu/iiif/2/{suffix}-{index}/full/1200,/0/default.jpg"
        page_url = f"https://www.artic.edu/artworks/{index}"
    return ArtworkCandidate(
        source=source,
        source_label="The Met" if source == "met" else "Art Institute of Chicago",
        artwork_id=f"art:{index}",
        title=f"Artwork {index}",
        artist=f"Artist {index}",
        date="1900",
        museum="Example Museum",
        rights="Public Domain",
        image_url=image_url,
        page_url=page_url,
    )


def request(request_id, *, origin="origin-display", requested_at="2026-07-12T10:00:00+00:00"):
    return PresentationRequestContext(
        request_id=request_id,
        requested_at=requested_at,
        origin_display_commit_id=origin,
        last_receipt=None,
    )


def receipt(request_id, *, display="prepared-display", committed_at="2026-07-12T10:01:00+00:00"):
    return PresentationCommitReceipt(
        request_id=request_id,
        committed_at=committed_at,
        display_commit_id=display,
        structural_generation=1,
        settings_revision=1,
        theme_mode=None,
    )


def theme_context(mode):
    return {
        "mode": mode,
        "palette": {
            "background": (242, 238, 230) if mode == "day" else (21, 16, 13),
            "accent": (138, 81, 48) if mode == "day" else (215, 160, 113),
        },
    }


def hydrate_bank(plugin, monkeypatch, settings, *, count=24, portrait=True):
    candidates = [art_candidate(index, portrait=portrait) for index in range(count)]
    calls = {"pool": 0, "download": 0}

    def candidate_pool(_settings, _device, _now):
        calls["pool"] += 1
        return list(candidates)

    def download(url, _dimensions, _settings):
        calls["download"] += 1
        if "portrait" in url:
            return Image.new("RGB", (240, 420), (80, 110, 140))
        return Image.new("RGB", (640, 300), (80, 110, 140))

    monkeypatch.setattr(plugin, "_candidate_pool", candidate_pool)
    monkeypatch.setattr(plugin, "_download_image_preview", download)
    monkeypatch.setattr(plugin, "_now_for_device", lambda _device: datetime(2026, 7, 12, 9, 30))
    plugin.generate_image(settings, FakeDeviceConfig())
    return candidates, calls


def presentation_state(plugin):
    return json.loads(plugin._presentation_state_path().read_text(encoding="utf-8"))


def profile_for(state, instance_uuid="daily-art-test-instance"):
    fingerprint = state["instance_profiles"][instance_uuid]
    return state["profiles"][fingerprint]


def selection_artwork_ids(state, selection, instance_uuid="daily-art-test-instance"):
    profile = profile_for(state, instance_uuid)
    records = {record["record_key"]: record for record in profile["records"]}
    return [records[key]["artwork_id"] for key in selection["record_keys"]]


def cache_tree(root):
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


def png_bytes(color="red"):
    output = BytesIO()
    Image.new("RGB", (32, 48), color).save(output, format="PNG")
    return output.getvalue()


class FakeHttpResponse:
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


class FakeRedirectSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if not self.responses:
            raise AssertionError("unexpected HTTP request")
        return self.responses.pop(0)


class FakeRedirectClient:
    def __init__(self, session):
        self.session = session


def resolver_for(mapping):
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


def test_default_font_is_yahei_but_explicit_jost_is_preserved(monkeypatch):
    sentinel = object()
    calls = []

    def fake_get_font(family, size, weight="normal"):
        calls.append((family, size, weight))
        return sentinel

    monkeypatch.setattr(daily_art_module, "get_font", fake_get_font)

    assert daily_art_module._font(None, 18) is sentinel
    assert daily_art_module._font("", 18) is sentinel
    assert daily_art_module._font("Jost", 18, "bold") is sentinel
    assert calls == [
        ("Microsoft YaHei", 18, "normal"),
        ("Microsoft YaHei", 18, "normal"),
        ("Jost", 18, "bold"),
    ]


def test_settings_default_font_is_microsoft_yahei():
    settings_path = Path(__file__).resolve().parents[1] / "src" / "plugins" / "daily_art" / "settings.html"
    html = settings_path.read_text(encoding="utf-8")
    script = " ".join(html.split())
    missing = object()
    native_initial = daily_art_module.get_available_font_names(default=daily_art_module.DEFAULT_FONT)[0]

    def submitted_font(stored=missing):
        current = native_initial if stored is missing else stored
        has_stored = stored is not missing
        if "const hasStoredFont =" in script:
            assert "&& pluginSettings.fontFamily !== undefined;" in script
            assert "const yahei = [...fontFamily.options].find((option) => option.value === 'Microsoft YaHei');" in script
            assert "if (yahei && (!hasStoredFont || !fontFamily.value)) {" in script
            if not has_stored or not current:
                current = "Microsoft YaHei"
        else:
            assert "if (fontFamily && !fontFamily.value) {" in script
            if not current:
                current = "Microsoft YaHei"
        return current

    assert daily_art_module.DEFAULT_FONT == "Microsoft YaHei"
    assert native_initial != "Microsoft YaHei"
    assert "fontFamily.value = 'Microsoft YaHei';" in html
    assert "fontFamily.value = 'Jost';" not in html
    assert submitted_font("Jost") == "Jost"
    assert submitted_font("LXGW WenKai") == "LXGW WenKai"
    assert submitted_font("") == "Microsoft YaHei"
    assert submitted_font() == "Microsoft YaHei"


def test_enabled_sources_skips_keyed_sources_without_keys(tmp_path):
    plugin = make_plugin(tmp_path)

    assert plugin._enabled_sources({"sourceMode": "all"}, FakeDeviceConfig()) == ["met", "artic"]


def test_open_source_mode_includes_art_institute_without_keys(tmp_path):
    plugin = make_plugin(tmp_path)

    assert plugin._enabled_sources({"sourceMode": "open"}, FakeDeviceConfig()) == ["met", "artic"]


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

    settings = bound_settings(cadence="daily")
    first = plugin.generate_image(settings, device)
    second = plugin.generate_image(settings, device)

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

    image = plugin.generate_image(
        bound_settings(cadence="daily", layoutMode="auto_gallery", galleryCount="3"),
        device,
    )
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

    image = plugin.generate_image(
        bound_settings(cadence="daily", layoutMode="auto_gallery", galleryCount="3"),
        device,
    )
    cache = plugin._read_daily_cache()

    assert image.size == (800, 480)
    assert calls["download"] == 1
    assert cache["layout"] == "single"
    assert cache["artworks"][0]["artwork_id"] == "met:wide"


def test_daily_art_manifest_declares_presentation_capability():
    manifest_path = Path(__file__).resolve().parents[1] / "src" / "plugins" / "daily_art" / "plugin-info.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert payload["capabilities"]["supports_presentation_refresh"] is True


@pytest.mark.parametrize("cadence", ["daily", "hourly"])
def test_bucketed_cadence_presentation_is_no_change_and_zero_provider(tmp_path, monkeypatch, cadence):
    plugin = make_plugin(tmp_path)
    settings = bound_settings(cadence=cadence)
    now = datetime(2026, 7, 12, 9, 15)
    candidate = art_candidate(1)
    calls = {"pool": 0, "download": 0}
    monkeypatch.setattr(plugin, "_now_for_device", lambda _device: now)
    monkeypatch.setattr(
        plugin,
        "_candidate_pool",
        lambda *_args: calls.__setitem__("pool", calls["pool"] + 1) or [candidate],
    )
    monkeypatch.setattr(
        plugin,
        "_download_image_preview",
        lambda *_args: calls.__setitem__("download", calls["download"] + 1)
        or Image.new("RGB", (240, 420), "red"),
    )
    monkeypatch.setattr(daily_art_module, "write_context", lambda *_args, **_kwargs: None)

    plugin.generate_image(settings, FakeDeviceConfig())
    first_calls = dict(calls)
    plugin.generate_image(settings, FakeDeviceConfig())
    assert calls == first_calls
    assert plugin.presentation_mode(settings) is PresentationMode.NO_CHANGE

    monkeypatch.setattr(plugin, "_candidate_pool", lambda *_args: pytest.fail("bucketed presentation used provider"))
    monkeypatch.setattr(plugin, "_get_json", lambda *_args, **_kwargs: pytest.fail("bucketed presentation used JSON"))
    monkeypatch.setattr(
        plugin,
        "_download_image_preview",
        lambda *_args, **_kwargs: pytest.fail("bucketed presentation downloaded media"),
    )
    monkeypatch.setattr(daily_art_module, "get_http_client", lambda: pytest.fail("bucketed presentation opened HTTP"))
    prepared = plugin.prepare_presentation(
        settings,
        FakeDeviceConfig(),
        request=request("a" * 32),
        resolved_theme_context={"mode": "day"},
    )

    assert prepared.changed is False
    assert prepared.image is None


@pytest.mark.parametrize(
    ("cadence", "first", "second"),
    [
        ("daily", datetime(2026, 7, 12, 9, 15), datetime(2026, 7, 13, 9, 15)),
        ("hourly", datetime(2026, 7, 12, 9, 15), datetime(2026, 7, 12, 10, 15)),
    ],
)
def test_bucketed_cadence_crosses_bucket_and_force_refresh_keeps_legacy_provider_behavior(
    tmp_path,
    monkeypatch,
    cadence,
    first,
    second,
):
    plugin = make_plugin(tmp_path)
    settings = bound_settings(cadence=cadence)
    now = {"value": first}
    calls = {"pool": 0, "download": 0, "context": 0}
    monkeypatch.setattr(plugin, "_now_for_device", lambda _device: now["value"])
    monkeypatch.setattr(
        plugin,
        "_candidate_pool",
        lambda *_args: calls.__setitem__("pool", calls["pool"] + 1) or [art_candidate(calls["pool"])],
    )
    monkeypatch.setattr(
        plugin,
        "_download_image_preview",
        lambda *_args: calls.__setitem__("download", calls["download"] + 1)
        or Image.new("RGB", (240, 420), "red"),
    )
    monkeypatch.setattr(
        daily_art_module,
        "write_context",
        lambda *_args, **_kwargs: calls.__setitem__("context", calls["context"] + 1),
    )

    plugin.generate_image(settings, FakeDeviceConfig())
    after_first = dict(calls)
    plugin.generate_image(settings, FakeDeviceConfig())
    assert calls["pool"] == after_first["pool"]
    assert calls["download"] == after_first["download"]
    assert calls["context"] == after_first["context"] + 1

    now["value"] = second
    plugin.generate_image(settings, FakeDeviceConfig())
    assert calls["pool"] == after_first["pool"] + 1
    assert calls["download"] == after_first["download"] + 1

    plugin.generate_image({**settings, "forceRefresh": "true"}, FakeDeviceConfig())
    assert calls["pool"] == after_first["pool"] + 2
    assert calls["download"] == after_first["download"] + 2
    assert plugin._context_ttl_seconds(settings) == (
        26 * 60 * 60 if cadence == "daily" else 2 * 60 * 60
    )


def test_every_refresh_source_fingerprint_is_stable_and_excludes_runtime_noise():
    from plugins.daily_art.presentation_bank import settings_fingerprint

    base = {
        "rotationCadence": "every_refresh",
        "sourceMode": "all",
        "sources": "met,artic,europeana",
        "queryTerms": "portrait, landscape",
        "layoutMode": "gallery",
        "galleryCount": 3,
        "fitMode": "contain",
        "showCaption": "true",
        "backgroundColor": "warm",
        "europeanaApiKey": "secret-one",
        "forceRefresh": "false",
        "themeContext": {"mode": "day"},
        "_inkypiPresentationRequestId": "a" * 32,
        "_inkypi_presentation_instance_identity": object(),
    }
    noisy = {
        **base,
        "europeanaApiKey": "secret-two",
        "forceRefresh": "true",
        "themeContext": {"mode": "night"},
        "_inkypiPresentationRequestId": "b" * 32,
        "_inkypi_presentation_instance_identity": object(),
    }

    first = settings_fingerprint(base, (800, 480), "2026-07-12", ["met", "artic"])
    second = settings_fingerprint(noisy, (800, 480), "2026-07-12", ["met", "artic"])

    assert first == second
    assert first != settings_fingerprint(base, (800, 480), "2026-07-13", ["met", "artic"])
    assert first != settings_fingerprint(base, (640, 400), "2026-07-12", ["met", "artic"])
    assert first != settings_fingerprint({**base, "layoutMode": "single"}, (800, 480), "2026-07-12", ["met", "artic"])
    assert first != settings_fingerprint({**base, "queryTerms": "sculpture"}, (800, 480), "2026-07-12", ["met", "artic"])
    assert first != settings_fingerprint(base, (800, 480), "2026-07-12", ["met"])

    omitted_defaults = {key: value for key, value in base.items() if key != "queryTerms"}
    explicit_defaults = {**omitted_defaults, "queryTerms": ", ".join(daily_art_module.DEFAULT_QUERY_TERMS)}
    assert settings_fingerprint(omitted_defaults, (800, 480), "2026-07-12", ["met", "artic"]) == settings_fingerprint(
        explicit_defaults,
        (800, 480),
        "2026-07-12",
        ["met", "artic"],
    )


@pytest.mark.parametrize(
    ("setting", "explicit"),
    [
        ("sourceMode", "all"),
        ("sourceLimit", 12),
        ("maxAttempts", 10),
        ("layoutMode", "auto_gallery"),
        ("galleryCount", 3),
        ("fitMode", "contain"),
        ("backgroundStyle", "blur"),
        ("backgroundColor", "warm"),
        ("showCaption", "false"),
        ("fontFamily", "Microsoft YaHei"),
        ("iiifWidth", 1200),
        ("maxImageBytes", 12_000_000),
        ("imageTimeoutSeconds", 14),
    ],
)
def test_fingerprint_omitted_pixel_defaults_equal_explicit_renderer_defaults(setting, explicit):
    from plugins.daily_art.presentation_bank import settings_fingerprint

    omitted = {"rotationCadence": "every_refresh"}
    explicit_settings = {**omitted, setting: explicit}

    assert settings_fingerprint(omitted, (800, 480), "2026-07-12", ["met", "artic"]) == settings_fingerprint(
        explicit_settings,
        (800, 480),
        "2026-07-12",
        ["met", "artic"],
    )


@pytest.mark.parametrize(
    ("setting", "changed"),
    [
        ("maxAttempts", 3),
        ("layoutMode", "single"),
        ("galleryCount", 4),
        ("fitMode", "cover"),
        ("backgroundStyle", "plain"),
        ("backgroundColor", "black"),
        ("showCaption", "true"),
        ("fontFamily", "Jost"),
        ("iiifWidth", 900),
        ("maxImageBytes", 6_000_000),
        ("imageTimeoutSeconds", 7),
    ],
)
def test_fingerprint_changes_when_selection_or_pixel_semantics_change(setting, changed):
    from plugins.daily_art.presentation_bank import settings_fingerprint

    base = {"rotationCadence": "every_refresh"}
    changed_settings = {**base, setting: changed}

    assert settings_fingerprint(base, (800, 480), "2026-07-12", ["met", "artic"]) != settings_fingerprint(
        changed_settings,
        (800, 480),
        "2026-07-12",
        ["met", "artic"],
    )


def test_equal_default_fingerprints_have_equal_selection_and_render_pixels(tmp_path):
    from plugins.daily_art.presentation_bank import settings_fingerprint

    plugin = make_plugin(tmp_path)
    omitted = {"rotationCadence": "every_refresh", "showCaption": "true"}
    explicit = {
        **omitted,
        "sourceMode": "all",
        "sourceLimit": 12,
        "maxAttempts": 10,
        "layoutMode": "auto_gallery",
        "galleryCount": 3,
        "fitMode": "contain",
        "backgroundStyle": "blur",
        "backgroundColor": "warm",
        "fontFamily": "Microsoft YaHei",
        "iiifWidth": 1200,
        "maxImageBytes": 12_000_000,
        "imageTimeoutSeconds": 14,
    }
    candidate = art_candidate(2)
    source = Image.new("RGB", (240, 420), (70, 100, 140))

    omitted_fingerprint = settings_fingerprint(omitted, (800, 480), "2026-07-12", ["met", "artic"])
    explicit_fingerprint = settings_fingerprint(explicit, (800, 480), "2026-07-12", ["met", "artic"])
    omitted_image = plugin._render_artwork(source, candidate, (800, 480), omitted)
    explicit_image = plugin._render_artwork(source, candidate, (800, 480), explicit)

    assert omitted_fingerprint == explicit_fingerprint
    assert plugin._layout_mode(omitted) == plugin._layout_mode(explicit) == "auto_gallery"
    assert plugin._gallery_count(omitted) == plugin._gallery_count(explicit) == 3
    assert omitted_image.tobytes() == explicit_image.tobytes()


def test_every_refresh_data_hydrates_16_without_seen_context_or_pending(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path)
    settings = bound_settings(maxAttempts=8)
    contexts = []
    monkeypatch.setattr(daily_art_module, "write_context", lambda *args, **kwargs: contexts.append((args, kwargs)))
    candidates, calls = hydrate_bank(plugin, monkeypatch, settings)
    plugin.generate_image(settings, FakeDeviceConfig())
    state = presentation_state(plugin)
    profile = profile_for(state)

    assert len(profile["records"]) == 16
    assert calls == {"pool": 2, "download": 16}
    assert profile["current_selection"] is not None
    assert profile["pending_selection"] is None
    profile_bucket = profile.get("date_buckets", {}).get("2026-07-12", {})
    assert profile_bucket.get("seen_artwork_ids", []) == []
    assert "updated_at" not in profile_bucket
    assert contexts == []
    assert set(selection_artwork_ids(state, profile["current_selection"])).issubset(
        {candidate.artwork_id for candidate in candidates}
    )


def test_every_refresh_refills_only_after_falling_below_six_and_bounds_attempts(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path)
    settings = bound_settings(maxAttempts=3)
    candidates, calls = hydrate_bank(plugin, monkeypatch, settings)
    assert calls["download"] == 3

    plugin.generate_image(settings, FakeDeviceConfig())
    assert calls["download"] == 6
    state = presentation_state(plugin)
    profile = profile_for(state)
    assert len(profile["records"]) == 6

    plugin.generate_image(settings, FakeDeviceConfig())
    assert calls["download"] == 9
    state = presentation_state(plugin)
    profile = profile_for(state)
    assert len(profile["records"]) == 9

    profile["refill_in_progress"] = False
    plugin._presentation_state_path().write_text(json.dumps(state), encoding="utf-8")
    before = dict(calls)
    plugin.generate_image(settings, FakeDeviceConfig())
    assert calls == before
    assert {record["artwork_id"] for record in profile["records"]}.issubset(
        {candidate.artwork_id for candidate in candidates}
    )


def test_every_refresh_data_preserves_current_selection(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path)
    settings = bound_settings()
    hydrate_bank(plugin, monkeypatch, settings)
    before = presentation_state(plugin)
    current = profile_for(before)["current_selection"]

    plugin.generate_image(settings, FakeDeviceConfig())
    after = presentation_state(plugin)

    assert profile_for(after)["current_selection"] == current
    assert profile_for(after)["pending_selection"] is None


def test_every_refresh_data_keeps_legacy_output_cleanup_without_touching_bank_media(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path)
    settings = bound_settings()
    stale_output = tmp_path / "2026-01-01.png"
    stale_output.write_bytes(b"old-output")
    old = (datetime.now(timezone.utc) - timedelta(days=11)).timestamp()
    os.utime(stale_output, (old, old))
    hydrate_bank(plugin, monkeypatch, settings)
    managed_before = cache_tree(plugin._presentation_media_dir())

    plugin.generate_image(settings, FakeDeviceConfig())

    assert not stale_output.exists()
    assert cache_tree(plugin._presentation_media_dir()) == managed_before


def test_every_refresh_warm_presentation_is_provider_free_and_pending_is_unseen(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path)
    settings = bound_settings()
    hydrate_bank(plugin, monkeypatch, settings)
    monkeypatch.setattr(plugin, "_candidate_pool", lambda *_args: pytest.fail("warm presentation used candidates"))
    monkeypatch.setattr(plugin, "_get_json", lambda *_args, **_kwargs: pytest.fail("warm presentation used JSON"))
    monkeypatch.setattr(
        plugin,
        "_download_image_preview",
        lambda *_args, **_kwargs: pytest.fail("warm presentation downloaded media"),
    )
    monkeypatch.setattr(daily_art_module, "get_http_client", lambda: pytest.fail("warm presentation opened HTTP"))

    prepared = plugin.prepare_presentation(
        settings,
        FakeDeviceConfig(),
        request=request("b" * 32),
        resolved_theme_context=theme_context("night"),
    )
    state = presentation_state(plugin)
    profile = profile_for(state)
    pending_ids = selection_artwork_ids(state, profile["pending_selection"])
    bucket = profile["date_buckets"]["2026-07-12"]

    assert prepared.changed is True
    assert prepared.image.size == (800, 480)
    assert profile["pending_selection"]["request_id"] == "b" * 32
    assert not set(pending_ids).intersection(bucket.get("seen_artwork_ids", []))


def test_matching_receipt_commits_exact_selection_once_and_writes_displayed_context(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path)
    settings = bound_settings()
    contexts = []
    monkeypatch.setattr(daily_art_module, "write_context", lambda *args, **kwargs: contexts.append((args, kwargs)))
    hydrate_bank(plugin, monkeypatch, settings)
    plugin.prepare_presentation(
        settings,
        FakeDeviceConfig(),
        request=request("c" * 32),
        resolved_theme_context=None,
    )
    pending_state = presentation_state(plugin)
    pending_ids = selection_artwork_ids(pending_state, profile_for(pending_state)["pending_selection"])
    contexts.clear()

    plugin.reconcile_presentation_receipt(settings, receipt("c" * 32))
    committed_bytes = plugin._presentation_state_path().read_bytes()
    plugin.reconcile_presentation_receipt(settings, receipt("c" * 32))
    state = presentation_state(plugin)
    profile = profile_for(state)

    assert plugin._presentation_state_path().read_bytes() == committed_bytes
    assert profile["pending_selection"] is None
    assert selection_artwork_ids(state, profile["current_selection"]) == pending_ids
    bucket = profile["date_buckets"]["2026-07-12"]
    assert bucket["seen_artwork_ids"][-len(pending_ids):] == pending_ids
    assert bucket["committed_at"] == "2026-07-12T10:01:00+00:00"
    assert len(contexts) == 1
    assert [item["artwork_id"] for item in contexts[0][0][1]["items"]] == pending_ids


def test_foreign_canceled_duplicate_and_late_receipts_are_byte_noops(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path)
    settings = bound_settings()
    monkeypatch.setattr(daily_art_module, "write_context", lambda *_args, **_kwargs: None)
    hydrate_bank(plugin, monkeypatch, settings)
    plugin.prepare_presentation(settings, FakeDeviceConfig(), request=request("d" * 32), resolved_theme_context=None)
    baseline = plugin._presentation_state_path().read_bytes()

    plugin.reconcile_presentation_receipt(settings, receipt("e" * 32))
    plugin.reconcile_presentation_receipt(
        settings,
        receipt("d" * 32, display="origin-display"),
    )
    assert plugin._presentation_state_path().read_bytes() == baseline

    plugin.reconcile_presentation_receipt(settings, receipt("d" * 32))
    committed = plugin._presentation_state_path().read_bytes()
    plugin.reconcile_presentation_receipt(settings, receipt("d" * 32, committed_at="2026-07-12T09:00:00+00:00"))
    assert plugin._presentation_state_path().read_bytes() == committed


def test_trusted_origin_commits_current_once_before_choosing_pending(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path)
    settings = bound_settings()
    monkeypatch.setattr(daily_art_module, "write_context", lambda *_args, **_kwargs: None)
    hydrate_bank(plugin, monkeypatch, settings)
    before = presentation_state(plugin)
    current_ids = selection_artwork_ids(before, profile_for(before)["current_selection"])

    plugin.prepare_presentation(
        settings,
        FakeDeviceConfig(),
        request=request("f" * 32, origin="origin-once"),
        resolved_theme_context=None,
    )
    first = presentation_state(plugin)
    plugin.prepare_presentation(
        settings,
        FakeDeviceConfig(),
        request=request("f" * 32, origin="origin-once"),
        resolved_theme_context=theme_context("night"),
    )
    second = presentation_state(plugin)

    assert first == second
    assert profile_for(first)["date_buckets"]["2026-07-12"]["seen_artwork_ids"][: len(current_ids)] == current_ids
    assert profile_for(first)["last_applied_origin_commit_id"] == "origin-once"


def test_pending_survives_restart_and_theme_redraw(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path)
    settings = bound_settings()
    monkeypatch.setattr(daily_art_module, "write_context", lambda *_args, **_kwargs: None)
    hydrate_bank(plugin, monkeypatch, settings)
    first = plugin.prepare_presentation(
        settings,
        FakeDeviceConfig(),
        request=request("1" * 32),
        resolved_theme_context=theme_context("day"),
    )
    first_state = presentation_state(plugin)
    pending = profile_for(first_state)["pending_selection"]

    restarted = make_plugin(tmp_path)
    monkeypatch.setattr(restarted, "_candidate_pool", lambda *_args: pytest.fail("restart called provider"))
    monkeypatch.setattr(restarted, "_download_image_preview", lambda *_args: pytest.fail("restart downloaded"))
    second = restarted.prepare_presentation(
        settings,
        FakeDeviceConfig(),
        request=request("1" * 32),
        resolved_theme_context=theme_context("night"),
    )

    assert profile_for(presentation_state(restarted))["pending_selection"] == pending
    assert first.image.info["inkypi_theme_mode"] == "day"
    assert second.image.info["inkypi_theme_mode"] == "night"


@pytest.mark.parametrize("damage", ["missing", "expired"])
def test_data_recovers_exact_protected_current_or_fails_without_state_change(tmp_path, monkeypatch, damage):
    plugin = make_plugin(tmp_path)
    settings = bound_settings()
    hydrate_bank(plugin, monkeypatch, settings)
    state = presentation_state(plugin)
    profile = profile_for(state)
    current = profile["current_selection"]
    record_key = current["record_keys"][0]
    record = next(item for item in profile["records"] if item["record_key"] == record_key)
    media = plugin._presentation_media_dir() / f"{record['media_key']}.png"
    if damage == "missing":
        media.unlink()
    else:
        record["downloaded_at"] = (datetime.now(timezone.utc) - timedelta(hours=49)).isoformat()
        plugin._presentation_state_path().write_text(json.dumps(state), encoding="utf-8")
    saved_url = record["image_url"]
    recovered_urls = []
    monkeypatch.setattr(plugin, "_candidate_pool", lambda *_args: pytest.fail("protected recovery used candidate pool"))
    monkeypatch.setattr(
        plugin,
        "_download_image_preview",
        lambda url, *_args: recovered_urls.append(url) or Image.new("RGB", (240, 420), "blue"),
    )

    plugin.generate_image(settings, FakeDeviceConfig())
    recovered = presentation_state(plugin)
    assert profile_for(recovered)["current_selection"] == current
    assert recovered_urls == [saved_url]

    media.unlink()
    baseline = plugin._presentation_state_path().read_bytes()
    monkeypatch.setattr(plugin, "_download_image_preview", lambda *_args: (_ for _ in ()).throw(RuntimeError("offline")))
    with pytest.raises(RuntimeError, match="protected|recover"):
        plugin.generate_image(settings, FakeDeviceConfig())
    assert plugin._presentation_state_path().read_bytes() == baseline


def test_identical_settings_instances_are_isolated_and_json_cannot_spoof_identity(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path)
    first_settings = bound_settings(instance_uuid="instance-one")
    second_settings = bound_settings(instance_uuid="instance-two")
    hydrate_bank(plugin, monkeypatch, first_settings)
    hydrate_bank(plugin, monkeypatch, second_settings)
    plugin.prepare_presentation(
        first_settings,
        FakeDeviceConfig(),
        request=request("2" * 32, origin="origin-one"),
        resolved_theme_context=None,
    )
    plugin.prepare_presentation(
        second_settings,
        FakeDeviceConfig(),
        request=request("3" * 32, origin="origin-two"),
        resolved_theme_context=None,
    )
    state = presentation_state(plugin)

    assert state["instance_profiles"]["instance-one"] != state["instance_profiles"]["instance-two"]
    assert profile_for(state, "instance-one")["pending_selection"]["request_id"] == "2" * 32
    assert profile_for(state, "instance-two")["pending_selection"]["request_id"] == "3" * 32

    spoofed = {
        "rotationCadence": "every_refresh",
        "_inkypi_presentation_instance_identity": {"instance_uuid": "instance-one"},
    }
    before = cache_tree(tmp_path)
    monkeypatch.setattr(plugin, "_candidate_pool", lambda *_args: [art_candidate(99)])
    monkeypatch.setattr(plugin, "_download_image_preview", lambda *_args: Image.new("RGB", (240, 420), "red"))
    plugin.generate_image(spoofed, FakeDeviceConfig())
    assert cache_tree(tmp_path) == before


def test_receipt_seen_history_and_candidate_filtering_are_isolated_per_instance(tmp_path, monkeypatch):
    from plugins.daily_art import presentation_bank

    plugin = make_plugin(tmp_path)
    settings_a = bound_settings(instance_uuid="history-a")
    settings_b = bound_settings(instance_uuid="history-b")
    monkeypatch.setattr(presentation_bank.random, "shuffle", lambda _values: None)
    monkeypatch.setattr(daily_art_module, "write_context", lambda *_args, **_kwargs: None)
    hydrate_bank(plugin, monkeypatch, settings_a)
    hydrate_bank(plugin, monkeypatch, settings_b)

    request_a = request("8" * 32, origin="origin-a")
    plugin.prepare_presentation(settings_a, FakeDeviceConfig(), request=request_a, resolved_theme_context=None)
    before_receipt = presentation_state(plugin)
    pending_a = profile_for(before_receipt, "history-a")["pending_selection"]
    committed_a_ids = selection_artwork_ids(before_receipt, pending_a, "history-a")
    plugin.reconcile_presentation_receipt(settings_a, receipt("8" * 32))

    request_b = request("9" * 32, origin="origin-b")
    plugin.prepare_presentation(settings_b, FakeDeviceConfig(), request=request_b, resolved_theme_context=None)
    after = presentation_state(plugin)
    profile_a = profile_for(after, "history-a")
    profile_b = profile_for(after, "history-b")
    pending_b_ids = selection_artwork_ids(after, profile_b["pending_selection"], "history-b")
    seen_a = profile_a["date_buckets"]["2026-07-12"]["seen_artwork_ids"]
    seen_b = profile_b["date_buckets"]["2026-07-12"]["seen_artwork_ids"]

    assert pending_b_ids == committed_a_ids
    assert set(committed_a_ids).issubset(seen_a)
    assert not set(committed_a_ids).intersection(seen_b)
    assert profile_a["date_buckets"] is not profile_b["date_buckets"]


def test_legacy_global_history_is_copied_deterministically_then_never_shared(tmp_path):
    from plugins.daily_art.presentation_bank import (
        DailyArtPresentationBank,
        instance_profile_fingerprint,
    )

    state_path = tmp_path / "presentation-state.json"
    legacy = {
        "2026-07-12": {
            "seen_artwork_ids": ["legacy:1"],
            "committed_at": "2026-07-12T01:00:00+00:00",
        }
    }
    state_path.write_text(
        json.dumps(
            {
                "presentation_schema_version": 1,
                "profiles": {},
                "instance_profiles": {},
                "date_buckets": legacy,
            }
        ),
        encoding="utf-8",
    )
    base = "b" * 64
    fingerprint_a = instance_profile_fingerprint(base, "legacy-a")
    fingerprint_b = instance_profile_fingerprint(base, "legacy-b")
    bank_a = DailyArtPresentationBank(
        state_path,
        tmp_path / "presentation-media",
        fingerprint=fingerprint_a,
        base_fingerprint=base,
        profile_settings_key="c" * 64,
        instance_uuid="legacy-a",
        date_key="2026-07-12",
    )
    document, profile_a = bank_a.load_for_data()
    bank_a.save(document)
    bank_b = DailyArtPresentationBank(
        state_path,
        tmp_path / "presentation-media",
        fingerprint=fingerprint_b,
        base_fingerprint=base,
        profile_settings_key="c" * 64,
        instance_uuid="legacy-b",
        date_key="2026-07-12",
    )
    document, profile_b = bank_b.load_for_data()
    bank_b.save(document)

    assert profile_a["date_buckets"] == legacy
    assert profile_b["date_buckets"] == legacy
    document["profiles"][fingerprint_a]["date_buckets"]["2026-07-12"]["seen_artwork_ids"].append("only-a")
    bank_b.save(document)
    reloaded, _ = bank_b.load_for_data()

    assert reloaded["profiles"][fingerprint_a]["date_buckets"]["2026-07-12"]["seen_artwork_ids"] == [
        "legacy:1",
        "only-a",
    ]
    assert reloaded["profiles"][fingerprint_b]["date_buckets"]["2026-07-12"]["seen_artwork_ids"] == ["legacy:1"]


@pytest.mark.parametrize("cadence", ["daily", "hourly", "every_refresh"])
def test_unbound_preview_leaves_entire_cache_tree_unchanged(tmp_path, monkeypatch, cadence):
    plugin = make_plugin(tmp_path)
    sentinel = tmp_path / "provider-cache.json"
    sentinel.write_bytes(b"provider-cache")
    before = cache_tree(tmp_path)
    monkeypatch.setattr(plugin, "_now_for_device", lambda _device: datetime(2026, 7, 12, 9, 15))
    monkeypatch.setattr(plugin, "_candidate_pool", lambda *_args: [art_candidate(1)])
    monkeypatch.setattr(plugin, "_download_image_preview", lambda *_args: Image.new("RGB", (240, 420), "red"))

    image = plugin.generate_image({"rotationCadence": cadence, "layoutMode": "single"}, FakeDeviceConfig())

    assert image.size == (800, 480)
    assert cache_tree(tmp_path) == before


@pytest.mark.parametrize(
    ("layout_mode", "gallery_count", "portrait", "expected_count", "expected_layout"),
    [
        ("single", 4, True, 1, "single"),
        ("gallery", 2, False, 2, "gallery"),
        ("auto_gallery", 3, True, 3, "gallery"),
        ("auto_gallery", 3, False, 1, "single"),
    ],
)
def test_every_refresh_preserves_legacy_selection_semantics(
    tmp_path,
    monkeypatch,
    layout_mode,
    gallery_count,
    portrait,
    expected_count,
    expected_layout,
):
    plugin = make_plugin(tmp_path)
    settings = bound_settings(layoutMode=layout_mode, galleryCount=gallery_count)
    hydrate_bank(plugin, monkeypatch, settings, portrait=portrait)
    state = presentation_state(plugin)
    profile = profile_for(state)

    assert len(profile["current_selection"]["record_keys"]) == expected_count
    assert profile["current_selection"]["layout"] == expected_layout


def test_force_refresh_does_not_change_bank_identity(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path)
    settings = bound_settings(forceRefresh="false")
    hydrate_bank(plugin, monkeypatch, settings)
    state = presentation_state(plugin)
    fingerprint = state["instance_profiles"]["daily-art-test-instance"]

    plugin.generate_image({**settings, "forceRefresh": "true"}, FakeDeviceConfig())
    after = presentation_state(plugin)

    assert after["instance_profiles"]["daily-art-test-instance"] == fingerprint
    assert set(after["profiles"]) == set(state["profiles"])


def test_presentation_bank_budget_and_state_limits_are_declared():
    from plugins.daily_art import presentation_bank

    assert presentation_bank.READY_TARGET == 16
    assert presentation_bank.REFILL_THRESHOLD == 6
    assert presentation_bank.MAX_PROFILES == 64
    assert presentation_bank.MAX_DATE_BUCKETS == 366
    assert presentation_bank.MAX_RECORDS_PER_PROFILE == 16
    assert presentation_bank.MAX_STATE_BYTES == 4 * 1024 * 1024
    assert presentation_bank.MEDIA_MAX_AGE_SECONDS == 48 * 60 * 60
    assert presentation_bank.MEDIA_MAX_FILES == 48
    assert presentation_bank.MEDIA_MAX_BYTES == 96 * 1024 * 1024
    assert presentation_bank.MEDIA_MAX_OBJECT_BYTES == 12 * 1024 * 1024
    assert presentation_bank.MEDIA_MAX_DIMENSION == 8192
    assert presentation_bank.MEDIA_MAX_PIXELS == 32_000_000


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "https://user:secret@example.org/art.jpg",
        "https://example.org:444/art.jpg",
        "http://127.0.0.1/art.jpg",
        "http://localhost/art.jpg",
        "javascript:alert(1)",
    ],
)
def test_presentation_bank_rejects_unsafe_media_urls(tmp_path, url):
    from plugins.daily_art.presentation_bank import DailyArtPresentationBank

    bank = DailyArtPresentationBank(
        tmp_path / "presentation-state.json",
        tmp_path / "presentation-media",
        fingerprint="a" * 64,
        base_fingerprint="b" * 64,
        profile_settings_key="c" * 64,
        instance_uuid="instance",
        date_key="2026-07-12",
    )
    with pytest.raises(RuntimeError, match="URL|authority|source"):
        bank.normalize_candidate(art_candidate(1).__dict__ | {"image_url": url})


def test_media_download_allows_public_first_party_redirect_and_validates_final_url(tmp_path, monkeypatch):
    from security.ssrf import SSRFPolicy

    plugin = make_plugin(tmp_path)
    start = "https://images.metmuseum.org/start.jpg"
    final = "https://cdn.metmuseum.org/final.jpg"
    policy = SSRFPolicy(
        resolver=resolver_for(
            {
                "images.metmuseum.org": "93.184.216.34",
                "cdn.metmuseum.org": "93.184.216.35",
            }
        )
    )
    session = FakeRedirectSession(
        [
            FakeHttpResponse(302, url=start, headers={"Location": final}),
            FakeHttpResponse(200, url=final, headers={"Content-Length": str(len(png_bytes()))}, payload=png_bytes()),
        ]
    )
    monkeypatch.setattr(daily_art_module, "get_ssrf_policy", lambda: policy, raising=False)
    monkeypatch.setattr(daily_art_module, "get_http_client", lambda: FakeRedirectClient(session))

    image = plugin._download_image_preview(
        start,
        (800, 480),
        {"_inkypiDailyArtSource": "met"},
    )

    assert image.size == (32, 48)
    assert [call[1] for call in session.calls] == [start, final]
    assert all(call[2]["allow_redirects"] is False for call in session.calls)
    assert all(response.closed for response in session.responses) if session.responses else True


def test_europeana_federated_media_allows_public_third_party_redirect(tmp_path, monkeypatch):
    from security.ssrf import SSRFPolicy

    plugin = make_plugin(tmp_path)
    start = "https://www.europeana.eu/start.jpg"
    final = "https://media.example.org/final.jpg"
    policy = SSRFPolicy(
        resolver=resolver_for(
            {
                "www.europeana.eu": "93.184.216.34",
                "media.example.org": "93.184.216.35",
            }
        )
    )
    response_payload = png_bytes("blue")
    session = FakeRedirectSession(
        [
            FakeHttpResponse(302, url=start, headers={"Location": final}),
            FakeHttpResponse(200, url=final, payload=response_payload),
        ]
    )
    monkeypatch.setattr(daily_art_module, "get_ssrf_policy", lambda: policy, raising=False)
    monkeypatch.setattr(daily_art_module, "get_http_client", lambda: FakeRedirectClient(session))

    image = plugin._download_image_preview(
        start,
        (800, 480),
        {"_inkypiDailyArtSource": "europeana"},
    )

    assert image.size == (32, 48)
    assert len(session.calls) == 2


@pytest.mark.parametrize("private_address", ["127.0.0.1", "169.254.169.254"])
def test_media_redirect_rejects_dns_resolution_to_private_or_metadata_before_second_request(
    tmp_path,
    monkeypatch,
    private_address,
):
    from security.ssrf import SSRFPolicy, UnsafeTarget

    plugin = make_plugin(tmp_path)
    start = "https://images.metmuseum.org/start.jpg"
    redirect = "https://redirect.metmuseum.org/private.jpg"
    policy = SSRFPolicy(
        resolver=resolver_for(
            {
                "images.metmuseum.org": "93.184.216.34",
                "redirect.metmuseum.org": private_address,
            }
        )
    )
    session = FakeRedirectSession(
        [FakeHttpResponse(302, url=start, headers={"Location": redirect})]
    )
    monkeypatch.setattr(daily_art_module, "get_ssrf_policy", lambda: policy, raising=False)
    monkeypatch.setattr(daily_art_module, "get_http_client", lambda: FakeRedirectClient(session))

    with pytest.raises(UnsafeTarget, match="metadata|non-public"):
        plugin._download_image_preview(start, (800, 480), {"_inkypiDailyArtSource": "met"})
    assert len(session.calls) == 1


@pytest.mark.parametrize(
    "redirect",
    [
        "http://cdn.metmuseum.org/insecure.jpg",
        "https://user:secret@cdn.metmuseum.org/credential.jpg",
        "https://cdn.metmuseum.org:444/custom-port.jpg",
        "file:///etc/passwd",
        "https://evil.example.org/foreign.jpg",
    ],
)
def test_media_redirect_rejects_scheme_userinfo_port_and_foreign_authority(
    tmp_path,
    monkeypatch,
    redirect,
):
    from security.ssrf import SSRFPolicy

    plugin = make_plugin(tmp_path)
    start = "https://images.metmuseum.org/start.jpg"
    policy = SSRFPolicy(
        resolver=resolver_for(
            {
                "images.metmuseum.org": "93.184.216.34",
                "cdn.metmuseum.org": "93.184.216.35",
                "evil.example.org": "93.184.216.36",
            }
        )
    )
    session = FakeRedirectSession(
        [FakeHttpResponse(302, url=start, headers={"Location": redirect})]
    )
    monkeypatch.setattr(daily_art_module, "get_ssrf_policy", lambda: policy, raising=False)
    monkeypatch.setattr(daily_art_module, "get_http_client", lambda: FakeRedirectClient(session))

    with pytest.raises((RuntimeError, ValueError), match="HTTP|HTTPS|userinfo|port|authority|allowed"):
        plugin._download_image_preview(start, (800, 480), {"_inkypiDailyArtSource": "met"})
    assert len(session.calls) == 1


def test_media_download_rejects_unexpected_private_final_url_before_reading_body(tmp_path, monkeypatch):
    from security.ssrf import SSRFPolicy, UnsafeTarget

    plugin = make_plugin(tmp_path)
    start = "https://images.metmuseum.org/start.jpg"
    final = "https://metadata.metmuseum.org/final.jpg"
    policy = SSRFPolicy(
        resolver=resolver_for(
            {
                "images.metmuseum.org": "93.184.216.34",
                "metadata.metmuseum.org": "169.254.169.254",
            }
        )
    )
    response = FakeHttpResponse(200, url=final, payload=png_bytes())
    session = FakeRedirectSession([response])
    monkeypatch.setattr(daily_art_module, "get_ssrf_policy", lambda: policy, raising=False)
    monkeypatch.setattr(daily_art_module, "get_http_client", lambda: FakeRedirectClient(session))

    with pytest.raises(UnsafeTarget, match="metadata"):
        plugin._download_image_preview(start, (800, 480), {"_inkypiDailyArtSource": "met"})
    assert response.closed is True


def test_media_download_never_inherits_browser_private_host_allowlist(tmp_path, monkeypatch):
    from security.ssrf import SSRFPolicy

    plugin = make_plugin(tmp_path)
    start = "https://images.metmuseum.org/private.jpg"
    policy = SSRFPolicy(
        resolver=resolver_for({"images.metmuseum.org": "127.0.0.1"}),
        allowed_private_hosts=("images.metmuseum.org",),
    )
    session = FakeRedirectSession(
        [FakeHttpResponse(200, url=start, payload=png_bytes())]
    )
    monkeypatch.setattr(daily_art_module, "get_ssrf_policy", lambda: policy, raising=False)
    monkeypatch.setattr(daily_art_module, "get_http_client", lambda: FakeRedirectClient(session))

    with pytest.raises(RuntimeError, match="public|private|non-public"):
        plugin._download_image_preview(start, (800, 480), {"_inkypiDailyArtSource": "met"})
    assert session.calls == []


def test_presentation_state_symlink_is_not_followed_or_replaced(tmp_path):
    from plugins.daily_art.presentation_bank import DailyArtPresentationBank

    outside = tmp_path / "outside.json"
    outside.write_text('{"sentinel": true}', encoding="utf-8")
    state_path = tmp_path / "presentation-state.json"
    try:
        state_path.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks unavailable")
    bank = DailyArtPresentationBank(
        state_path,
        tmp_path / "presentation-media",
        fingerprint="a" * 64,
        base_fingerprint="b" * 64,
        profile_settings_key="c" * 64,
        instance_uuid="instance",
        date_key="2026-07-12",
    )

    with pytest.raises(RuntimeError, match="state|regular|symlink"):
        bank.load_for_data()
    assert json.loads(outside.read_text(encoding="utf-8")) == {"sentinel": True}


def test_presentation_state_parent_symlink_is_rejected_when_target_does_not_exist(tmp_path):
    from plugins.daily_art.presentation_bank import DailyArtPresentationBank

    outside = tmp_path / "outside"
    outside.mkdir()
    linked_root = tmp_path / "linked-cache"
    try:
        linked_root.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks unavailable")
    bank = DailyArtPresentationBank(
        linked_root / "presentation-state.json",
        linked_root / "presentation-media",
        fingerprint="a" * 64,
        base_fingerprint="b" * 64,
        profile_settings_key="c" * 64,
        instance_uuid="instance",
        date_key="2026-07-12",
    )
    document, _profile = bank.load_for_data()

    with pytest.raises(RuntimeError, match="root|parent|directory|reparse|unsafe"):
        bank.save(document)
    assert not (outside / "presentation-state.json").exists()


def test_bounded_state_read_detects_target_swap_after_descriptor_open(tmp_path, monkeypatch):
    from plugins.daily_art import presentation_bank

    state_path = tmp_path / "presentation-state.json"
    state_path.write_text('{"sentinel": "original"}', encoding="utf-8")
    replacement = tmp_path / "replacement.json"
    replacement.write_text('{"sentinel": "replacement"}', encoding="utf-8")
    original_read = presentation_bank.os.read
    swapped = {"done": False}

    def swapping_read(fd, count):
        if not swapped["done"]:
            swapped["done"] = True
            state_path.replace(tmp_path / "original-opened.json")
            replacement.replace(state_path)
        return original_read(fd, count)

    monkeypatch.setattr(presentation_bank.os, "read", swapping_read)

    with pytest.raises(RuntimeError, match="changed|identity|state|unsafe"):
        presentation_bank.read_bounded_json_object(state_path)
    assert swapped["done"] is True


def test_fallback_state_save_detects_cache_root_identity_swap(tmp_path, monkeypatch):
    if os.name == "posix":
        pytest.skip("fallback-only identity check")
    from plugins.daily_art import presentation_bank

    cache_root = tmp_path / "cache-root"
    cache_root.mkdir()
    bank = presentation_bank.DailyArtPresentationBank(
        cache_root / "presentation-state.json",
        cache_root / "presentation-media",
        fingerprint="a" * 64,
        base_fingerprint="b" * 64,
        profile_settings_key="c" * 64,
        instance_uuid="instance",
        date_key="2026-07-12",
    )
    document, _profile = bank.load_for_data()
    real_atomic_write = presentation_bank.atomic_write_json

    def swapping_atomic(path, payload, *, mode):
        cache_root.replace(tmp_path / "old-cache-root")
        cache_root.mkdir()
        real_atomic_write(path, payload, mode=mode)

    monkeypatch.setattr(presentation_bank, "atomic_write_json", swapping_atomic)

    with pytest.raises(RuntimeError, match="root|identity|changed|unsafe"):
        bank.save(document)


def test_presentation_media_oversize_and_symlink_fail_closed(tmp_path, monkeypatch):
    from plugins.daily_art.presentation_bank import DailyArtPresentationBank, MEDIA_MAX_OBJECT_BYTES

    bank = DailyArtPresentationBank(
        tmp_path / "presentation-state.json",
        tmp_path / "presentation-media",
        fingerprint="a" * 64,
        base_fingerprint="b" * 64,
        profile_settings_key="c" * 64,
        instance_uuid="instance",
        date_key="2026-07-12",
    )
    document, profile = bank.load_for_data()
    record = bank.ingest(profile, art_candidate(1).__dict__, Image.new("RGB", (40, 60), "red"))
    bank.save(document)
    media_path = bank.media.path(record["media_key"], suffix=".png")
    media_path.write_bytes(b"x" * (MEDIA_MAX_OBJECT_BYTES + 1))
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


@pytest.mark.parametrize("recovery_succeeds", [True, False])
def test_data_recovers_exact_pending_or_fails_without_losing_receipt_metadata(
    tmp_path,
    monkeypatch,
    recovery_succeeds,
):
    plugin = make_plugin(tmp_path)
    settings = bound_settings()
    monkeypatch.setattr(daily_art_module, "write_context", lambda *_args, **_kwargs: None)
    hydrate_bank(plugin, monkeypatch, settings)
    plugin.prepare_presentation(
        settings,
        FakeDeviceConfig(),
        request=request("4" * 32),
        resolved_theme_context=None,
    )
    state = presentation_state(plugin)
    pending = profile_for(state)["pending_selection"]
    pending_ids = selection_artwork_ids(state, pending)
    record_key = pending["record_keys"][0]
    record = next(item for item in profile_for(state)["records"] if item["record_key"] == record_key)
    (plugin._presentation_media_dir() / f"{record['media_key']}.png").unlink()
    baseline = plugin._presentation_state_path().read_bytes()
    recovered = []
    monkeypatch.setattr(plugin, "_candidate_pool", lambda *_args: pytest.fail("pending recovery used candidate pool"))

    def recover(url, *_args):
        recovered.append(url)
        if not recovery_succeeds:
            raise RuntimeError("offline")
        return Image.new("RGB", (240, 420), "green")

    monkeypatch.setattr(plugin, "_download_image_preview", recover)
    if recovery_succeeds:
        plugin.generate_image(settings, FakeDeviceConfig())
        after = presentation_state(plugin)
        assert selection_artwork_ids(after, profile_for(after)["pending_selection"]) == pending_ids
        plugin.reconcile_presentation_receipt(settings, receipt("4" * 32))
        committed = presentation_state(plugin)
        assert selection_artwork_ids(committed, profile_for(committed)["current_selection"]) == pending_ids
    else:
        with pytest.raises(RuntimeError, match="protected|recovery"):
            plugin.generate_image(settings, FakeDeviceConfig())
        assert plugin._presentation_state_path().read_bytes() == baseline
    assert recovered == [record["image_url"]]


def test_missing_pending_media_fails_presentation_and_receipt_without_network_or_state_write(
    tmp_path,
    monkeypatch,
):
    plugin = make_plugin(tmp_path)
    settings = bound_settings()
    monkeypatch.setattr(daily_art_module, "write_context", lambda *_args, **_kwargs: None)
    hydrate_bank(plugin, monkeypatch, settings)
    req = request("5" * 32)
    plugin.prepare_presentation(settings, FakeDeviceConfig(), request=req, resolved_theme_context=None)
    state = presentation_state(plugin)
    pending = profile_for(state)["pending_selection"]
    record_key = pending["record_keys"][0]
    record = next(item for item in profile_for(state)["records"] if item["record_key"] == record_key)
    (plugin._presentation_media_dir() / f"{record['media_key']}.png").unlink()
    baseline = plugin._presentation_state_path().read_bytes()
    monkeypatch.setattr(plugin, "_candidate_pool", lambda *_args: pytest.fail("presentation used candidate provider"))
    monkeypatch.setattr(plugin, "_get_json", lambda *_args, **_kwargs: pytest.fail("presentation used JSON provider"))
    monkeypatch.setattr(plugin, "_download_image_preview", lambda *_args: pytest.fail("presentation downloaded media"))
    monkeypatch.setattr(daily_art_module, "get_http_client", lambda: pytest.fail("presentation opened HTTP"))

    with pytest.raises(RuntimeError, match="media|selection"):
        plugin.prepare_presentation(settings, FakeDeviceConfig(), request=req, resolved_theme_context=None)
    assert plugin._presentation_state_path().read_bytes() == baseline
    with pytest.raises(RuntimeError, match="media|selection"):
        plugin.reconcile_presentation_receipt(settings, receipt("5" * 32))
    assert plugin._presentation_state_path().read_bytes() == baseline


def test_old_pending_receipt_commits_after_date_profile_becomes_active(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path)
    settings = bound_settings()
    contexts = []
    monkeypatch.setattr(daily_art_module, "write_context", lambda *args, **kwargs: contexts.append((args, kwargs)))
    hydrate_bank(plugin, monkeypatch, settings)
    old_request = request("6" * 32)
    plugin.prepare_presentation(settings, FakeDeviceConfig(), request=old_request, resolved_theme_context=None)
    old_state = presentation_state(plugin)
    old_fingerprint = old_state["instance_profiles"]["daily-art-test-instance"]
    old_pending_ids = selection_artwork_ids(old_state, profile_for(old_state)["pending_selection"])

    monkeypatch.setattr(plugin, "_now_for_device", lambda _device: datetime(2026, 7, 13, 9, 30))
    monkeypatch.setattr(plugin, "_candidate_pool", lambda *_args: [art_candidate(index + 100) for index in range(24)])
    monkeypatch.setattr(plugin, "_download_image_preview", lambda *_args: Image.new("RGB", (240, 420), "blue"))
    plugin.generate_image(settings, FakeDeviceConfig())
    active_state = presentation_state(plugin)
    assert active_state["instance_profiles"]["daily-art-test-instance"] != old_fingerprint
    assert active_state["profiles"][old_fingerprint]["pending_selection"]["request_id"] == "6" * 32

    contexts.clear()
    plugin.reconcile_presentation_receipt(
        settings,
        receipt("6" * 32, committed_at="2026-07-12T10:02:00+00:00"),
    )
    committed = presentation_state(plugin)
    old_profile = committed["profiles"][old_fingerprint]
    records = {record["record_key"]: record for record in old_profile["records"]}
    assert [records[key]["artwork_id"] for key in old_profile["current_selection"]["record_keys"]] == old_pending_ids
    assert old_profile["pending_selection"] is None
    assert committed["instance_profiles"]["daily-art-test-instance"] != old_fingerprint
    assert len(contexts) == 1


def test_profile_capacity_prunes_oldest_inactive_but_protects_pending(tmp_path):
    from plugins.daily_art.presentation_bank import (
        MAX_PROFILES,
        DailyArtPresentationBank,
        instance_profile_fingerprint,
    )

    state_path = tmp_path / "presentation-state.json"
    media_dir = tmp_path / "presentation-media"
    document = {
        "presentation_schema_version": 1,
        "profiles": {},
        "instance_profiles": {},
        "date_buckets": {},
    }
    protected_fingerprint = "0" * 64
    for index in range(MAX_PROFILES):
        fingerprint = f"{index:064x}"
        profile = {
            "profile_fingerprint": fingerprint,
            "settings_fingerprint": "b" * 64,
            "settings_key": "c" * 64,
            "instance_uuid": f"old-{index}",
            "date_key": "2026-07-12",
            "records": [],
            "current_selection": None,
            "pending_selection": None,
            "last_applied_origin_commit_id": None,
            "last_applied_request_id": None,
            "refill_in_progress": False,
            "last_used_at": f"2026-01-{(index % 28) + 1:02d}T00:00:00+00:00",
        }
        if fingerprint == protected_fingerprint:
            profile["pending_selection"] = {
                "request_id": "7" * 32,
                "origin_display_commit_id": "origin",
                "requested_at": "2026-07-12T10:00:00+00:00",
                "record_keys": ["d" * 64],
                "date_key": "2026-07-12",
                "layout": "single",
                "reset_seen": False,
            }
            profile["records"] = [
                {
                    **art_candidate(1).__dict__,
                    "image_url": "https://images.example.org/portrait/1.jpg",
                    "record_key": "d" * 64,
                    "media_key": "e" * 64,
                    "width": 240,
                    "height": 420,
                    "downloaded_at": "2026-07-12T00:00:00+00:00",
                    "date_key": "2026-07-12",
                }
            ]
        document["profiles"][fingerprint] = profile
    state_path.write_text(json.dumps(document), encoding="utf-8")
    new_base = "f" * 64
    new_fingerprint = instance_profile_fingerprint(new_base, "new-instance")
    bank = DailyArtPresentationBank(
        state_path,
        media_dir,
        fingerprint=new_fingerprint,
        base_fingerprint=new_base,
        profile_settings_key="a" * 64,
        instance_uuid="new-instance",
        date_key="2026-07-12",
    )

    migrated, _profile = bank.load_for_data()

    assert len(migrated["profiles"]) == MAX_PROFILES
    assert new_fingerprint in migrated["profiles"]
    assert protected_fingerprint in migrated["profiles"]


def test_presentation_bank_rejects_oversized_dimensions_before_encoding(tmp_path):
    from plugins.daily_art.presentation_bank import DailyArtPresentationBank, MEDIA_MAX_DIMENSION

    bank = DailyArtPresentationBank(
        tmp_path / "presentation-state.json",
        tmp_path / "presentation-media",
        fingerprint="a" * 64,
        base_fingerprint="b" * 64,
        profile_settings_key="c" * 64,
        instance_uuid="instance",
        date_key="2026-07-12",
    )
    _document, profile = bank.load_for_data()

    with pytest.raises(RuntimeError, match="dimension|safety"):
        bank.ingest(
            profile,
            art_candidate(1).__dict__,
            Image.new("RGB", (MEDIA_MAX_DIMENSION + 1, 1), "red"),
        )


def test_presentation_state_rejects_oversized_input_before_json_decode(tmp_path):
    from plugins.daily_art.presentation_bank import (
        MAX_STATE_BYTES,
        DailyArtPresentationBank,
    )

    state_path = tmp_path / "presentation-state.json"
    state_path.write_bytes(b"{" + b" " * MAX_STATE_BYTES + b"}")
    bank = DailyArtPresentationBank(
        state_path,
        tmp_path / "presentation-media",
        fingerprint="a" * 64,
        base_fingerprint="b" * 64,
        profile_settings_key="c" * 64,
        instance_uuid="instance",
        date_key="2026-07-12",
    )

    with pytest.raises(RuntimeError, match="size"):
        bank.load_for_data()


def test_presentation_state_writer_uses_durable_atomic_mode(tmp_path, monkeypatch):
    from plugins.daily_art import presentation_bank

    calls = []
    real_atomic_write = presentation_bank.atomic_write_json

    def record_atomic_write(path, payload, *, mode):
        calls.append((Path(path), payload, mode))
        real_atomic_write(path, payload, mode=mode)

    monkeypatch.setattr(
        presentation_bank,
        "atomic_write_json",
        record_atomic_write,
    )
    bank = presentation_bank.DailyArtPresentationBank(
        tmp_path / "presentation-state.json",
        tmp_path / "presentation-media",
        fingerprint="a" * 64,
        base_fingerprint="b" * 64,
        profile_settings_key="c" * 64,
        instance_uuid="instance",
        date_key="2026-07-12",
    )
    document, _profile = bank.load_for_data()

    bank.save(document)

    if os.name == "posix":
        assert calls == []
        assert (tmp_path / "presentation-state.json").stat().st_mode & 0o777 == 0o600
    else:
        assert calls[0][0] == tmp_path / "presentation-state.json"
        assert calls[0][2] == 0o600
        assert calls[0][1]["presentation_schema_version"] == 1


def test_presentation_media_budget_cleanup_never_touches_provider_cache(tmp_path):
    from plugins.daily_art.presentation_bank import DailyArtPresentationBank, MEDIA_MAX_FILES

    provider_cache = tmp_path / "provider-cache.json"
    provider_cache.write_bytes(b"provider")
    bank = DailyArtPresentationBank(
        tmp_path / "presentation-state.json",
        tmp_path / "presentation-media",
        fingerprint="a" * 64,
        base_fingerprint="b" * 64,
        profile_settings_key="c" * 64,
        instance_uuid="instance",
        date_key="2026-07-12",
    )

    for index in range(MEDIA_MAX_FILES + 4):
        bank.media.put_bytes(f"{index:064x}", b"small", suffix=".png")

    managed = list((tmp_path / "presentation-media").glob("*.png"))
    assert len(managed) <= MEDIA_MAX_FILES
    assert provider_cache.read_bytes() == b"provider"


def test_legacy_date_buckets_are_deterministically_pruned_to_366(tmp_path):
    from plugins.daily_art.presentation_bank import (
        MAX_DATE_BUCKETS,
        DailyArtPresentationBank,
    )

    state_path = tmp_path / "presentation-state.json"
    buckets = {
        f"2025-{index:03d}": {
            "seen_artwork_ids": [f"art:{index}"],
            "committed_at": "2025-01-01T00:00:00+00:00",
        }
        for index in range(MAX_DATE_BUCKETS + 10)
    }
    state_path.write_text(
        json.dumps(
            {
                "presentation_schema_version": 1,
                "profiles": {},
                "instance_profiles": {},
                "date_buckets": buckets,
            }
        ),
        encoding="utf-8",
    )
    bank = DailyArtPresentationBank(
        state_path,
        tmp_path / "presentation-media",
        fingerprint="a" * 64,
        base_fingerprint="b" * 64,
        profile_settings_key="c" * 64,
        instance_uuid="instance",
        date_key="2026-07-12",
    )

    document, _profile = bank.load_for_data()
    bank.save(document)

    assert len(document["date_buckets"]) == MAX_DATE_BUCKETS
    assert set(document["date_buckets"]) == set(sorted(buckets)[-MAX_DATE_BUCKETS:])
