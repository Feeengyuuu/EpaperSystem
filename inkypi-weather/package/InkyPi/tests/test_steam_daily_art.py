from datetime import datetime
import json
import logging
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
from PIL import Image, ImageChops, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import plugins.steam_daily_art.steam_daily_art as steam_daily_art_module  # noqa: E402
from plugins.steam_daily_art.steam_daily_art import SteamDailyArt  # noqa: E402
from plugins.base_plugin.presentation import (  # noqa: E402
    PresentationMode,
    PresentationRequestContext,
    bind_presentation_instance_identity,
)
from plugins.base_plugin.render_provenance import (  # noqa: E402
    SourceProvenance,
    read_source_provenance,
)
from runtime.runtime_state import PresentationCommitReceipt  # noqa: E402


class FakeDeviceConfig:
    def __init__(self, resolution=(800, 480), timezone="America/Los_Angeles"):
        self.resolution = resolution
        self.timezone = timezone

    def get_resolution(self):
        return self.resolution

    def get_config(self, key=None, default=None):
        values = {
            "timezone": self.timezone,
            "orientation": "horizontal",
        }
        if key is None:
            return values
        return values.get(key, default)


# The real translation call, kept before the offline fixture replaces it for every test.
ORIGINAL_TRANSLATE_BLURBS = SteamDailyArt._translate_blurbs


@pytest.fixture(autouse=True)
def _offline_shelf_and_account(monkeypatch):
    """The shelf's store facts, the wishlist and the library stay offline in tests: without
    store facts no shelf is drawn, so the art tests below see the art alone."""

    def offline(self, url, label):
        raise RuntimeError(f"offline in tests: {label}")

    monkeypatch.setattr(SteamDailyArt, "_fetch_profile_json", offline)
    monkeypatch.setattr(SteamDailyArt, "_fetch_profile_text", offline)
    monkeypatch.setattr(SteamDailyArt, "_translate_blurbs", lambda self, texts: {})


def make_plugin(tmp_path, monkeypatch):
    plugin = SteamDailyArt({"id": "steam_daily_art"})
    monkeypatch.setattr(plugin, "_cache_dir", lambda *args, **kwargs: tmp_path)
    monkeypatch.setattr(plugin, "_write_daily_art_context", lambda *args, **kwargs: None)
    monkeypatch.setattr(plugin, "_now_for_device", lambda device_config: datetime(2026, 6, 29, 15, 5, 0))
    return plugin


def test_missing_optional_logo_is_negatively_cached(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    calls = []
    plugin._logo_candidate_urls = lambda _item: ["https://cdn.example.test/missing-logo.png"]

    def missing(url):
        calls.append(url)
        raise RuntimeError("Steam media request failed with status 404")

    plugin._download_logo = missing

    assert plugin._download_first_available_logo({"id": 7}) == (None, None)
    assert plugin._download_first_available_logo({"id": 7}) == (None, None)

    assert calls == ["https://cdn.example.test/missing-logo.png"]


STEAM_LOGO_URL = "https://cdn.cloudflare.steamstatic.com/steam/apps/3883570/logo.png"


def _plugin_with_logo_media(tmp_path, monkeypatch, media):
    """Exercise the real logo cache path with only the HTTP fetch replaced."""

    plugin = make_plugin(tmp_path, monkeypatch)
    calls = []
    plugin._logo_candidate_urls = lambda _item: [STEAM_LOGO_URL]

    def download(url, *, max_bytes, timeout):
        calls.append(url)
        return media()

    monkeypatch.setattr(plugin, "_download_media_bytes", download)
    return plugin, calls


def _steam_records(caplog):
    return [
        record
        for record in caplog.records
        if record.name == steam_daily_art_module.logger.name
    ]


def _raise_steam_status(status):
    def media():
        raise RuntimeError(f"Steam media request failed with status {status}")

    return media


def test_upstream_missing_logo_is_info_and_negatively_cached_through_resource_cache(
    tmp_path,
    monkeypatch,
    caplog,
):
    plugin, calls = _plugin_with_logo_media(
        tmp_path,
        monkeypatch,
        _raise_steam_status(404),
    )
    caplog.set_level(logging.INFO, logger=steam_daily_art_module.logger.name)

    assert plugin._download_first_available_logo({"id": 3883570}) == (None, None)
    assert plugin._download_first_available_logo({"id": 3883570}) == (None, None)

    records = _steam_records(caplog)
    assert calls == [STEAM_LOGO_URL]
    assert plugin._optional_media_negative_hit(STEAM_LOGO_URL)
    assert [record for record in records if record.levelno >= logging.WARNING] == []
    assert [
        record.getMessage()
        for record in records
        if record.levelno == logging.INFO and STEAM_LOGO_URL in record.getMessage()
    ] == [f"Optional Steam logo candidate is unavailable: {STEAM_LOGO_URL}"]


def test_logo_retry_backoff_after_restart_is_info_without_refetch(
    tmp_path,
    monkeypatch,
    caplog,
):
    first, first_calls = _plugin_with_logo_media(
        tmp_path,
        monkeypatch,
        _raise_steam_status(404),
    )
    assert first._download_first_available_logo({"id": 3883570}) == (None, None)
    # A new process keeps the durable retry marker but not the memory cache.
    restarted, restarted_calls = _plugin_with_logo_media(
        tmp_path,
        monkeypatch,
        _raise_steam_status(404),
    )
    caplog.clear()
    caplog.set_level(logging.INFO, logger=steam_daily_art_module.logger.name)

    assert restarted._download_first_available_logo({"id": 3883570}) == (None, None)

    records = _steam_records(caplog)
    assert first_calls == [STEAM_LOGO_URL]
    assert restarted_calls == []
    assert [record for record in records if record.levelno >= logging.WARNING] == []
    assert [
        record.getMessage()
        for record in records
        if record.levelno == logging.INFO and STEAM_LOGO_URL in record.getMessage()
    ] == [f"Optional Steam logo candidate is in retry backoff: {STEAM_LOGO_URL}"]


@pytest.mark.parametrize(
    ("media", "expected_detail"),
    [
        (_raise_steam_status(503), "status 503"),
        (lambda: b"not an image", "UnidentifiedImageError"),
    ],
)
def test_real_logo_download_failure_stays_warning_and_is_not_negatively_cached(
    tmp_path,
    monkeypatch,
    caplog,
    media,
    expected_detail,
):
    plugin, calls = _plugin_with_logo_media(tmp_path, monkeypatch, media)
    caplog.set_level(logging.INFO, logger=steam_daily_art_module.logger.name)

    assert plugin._download_first_available_logo({"id": 3883570}) == (None, None)

    warnings = [
        record
        for record in _steam_records(caplog)
        if record.levelno >= logging.WARNING
    ]
    assert calls == [STEAM_LOGO_URL]
    assert not plugin._optional_media_negative_hit(STEAM_LOGO_URL)
    assert len(warnings) == 1
    assert warnings[0].levelno == logging.WARNING
    assert STEAM_LOGO_URL in warnings[0].getMessage()
    assert expected_detail in warnings[0].getMessage()


MISSING_HERO_URL = "https://cdn.cloudflare.steamstatic.com/steam/apps/7/library_hero.jpg"
CAPSULE_URL = "https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/7/capsule_616x353.jpg"


def _plugin_with_hero_failure(tmp_path, monkeypatch, error):
    """Item 7 has no hero (the error) and a lit capsule; the store lookup is empty."""

    plugin = make_plugin(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(plugin, "_fetch_store_assets", lambda _appid, _settings: {})

    def download(url):
        calls.append(url)
        if url == MISSING_HERO_URL:
            raise RuntimeError(error)
        if url == CAPSULE_URL:
            return Image.new("RGB", (16, 9), "blue")
        raise RuntimeError("Steam media request failed with status 404")

    plugin._download_image = download
    item = {"id": 7, "name": "Game 7", "large_capsule_image": CAPSULE_URL}
    return plugin, calls, item


def _art_urls(calls):
    return [url for url in calls if url in {MISSING_HERO_URL, CAPSULE_URL}]


def test_missing_primary_art_candidate_is_negatively_cached_without_blocking_fallback(
    tmp_path,
    monkeypatch,
):
    plugin, calls, item = _plugin_with_hero_failure(
        tmp_path, monkeypatch, "Steam media request failed with status 404",
    )

    first_url, _logo, first_image = plugin._render_item(item, base_settings(), (16, 9))
    second_url, _logo, second_image = plugin._render_item(item, base_settings(), (16, 9))

    assert first_url == second_url == CAPSULE_URL
    assert first_image.size == second_image.size == (16, 9)
    assert _art_urls(calls) == [MISSING_HERO_URL, CAPSULE_URL, CAPSULE_URL]


def test_missing_primary_art_candidate_retries_after_negative_cache_ttl(
    tmp_path,
    monkeypatch,
):
    plugin, calls, item = _plugin_with_hero_failure(
        tmp_path, monkeypatch, "Steam media request failed with status 404",
    )
    now = [1000.0]
    plugin._optional_media_clock = lambda: now[0]

    plugin._render_item(item, base_settings(), (16, 9))
    now[0] += steam_daily_art_module.OPTIONAL_MEDIA_NEGATIVE_TTL_SECONDS + 1
    plugin._render_item(item, base_settings(), (16, 9))

    assert _art_urls(calls) == [
        MISSING_HERO_URL,
        CAPSULE_URL,
        MISSING_HERO_URL,
        CAPSULE_URL,
    ]


def test_non_404_primary_art_failure_is_not_negatively_cached(
    tmp_path,
    monkeypatch,
):
    plugin, calls, item = _plugin_with_hero_failure(
        tmp_path, monkeypatch, "Steam media request failed with status 500",
    )

    plugin._render_item(item, base_settings(), (16, 9))
    plugin._render_item(item, base_settings(), (16, 9))

    assert _art_urls(calls) == [
        MISSING_HERO_URL,
        CAPSULE_URL,
        MISSING_HERO_URL,
        CAPSULE_URL,
    ]


def base_settings():
    return {
        "sourceCategory": "fresh_frontpage",
        "selectionMode": "daily_rotation",
        "rotationCadence": "hourly",
        "imageMode": "library_hero",
        "logoOverlay": "hide",
        "logoPosition": "empty_space",
        "logoSize": "normal",
        "countryCode": "US",
        "language": "english",
        "showCaption": "false",
    }


def every_refresh_settings(instance_uuid="steam-art-instance", **overrides):
    settings = {
        **base_settings(),
        "selectionMode": "every_refresh",
    }
    settings.update(overrides)
    return bind_presentation_instance_identity(settings, instance_uuid)


def presentation_request(request_id, *, origin="origin-display"):
    return PresentationRequestContext(
        request_id=request_id,
        requested_at="2026-07-12T10:00:00+00:00",
        origin_display_commit_id=origin,
        last_receipt=None,
    )


def presentation_receipt(
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


def write_matching_cache(plugin, settings, tmp_path):
    image_path = tmp_path / "cached.png"
    Image.new("RGB", (800, 480), "black").save(image_path)
    rotation_key = "2026-06-29-15"
    plugin._write_cache({
        "cache_key": plugin._cache_key(settings, (800, 480), rotation_key),
        "rotation_key": rotation_key,
        "name": "Cached Game",
        "appid": 1,
        "image_path": str(image_path),
    })


def test_generate_image_uses_matching_cache_without_force(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    settings = base_settings()
    write_matching_cache(plugin, settings, tmp_path)

    monkeypatch.setattr(
        plugin,
        "_select_item",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("selection should not run")),
    )

    image = plugin.generate_image(settings, FakeDeviceConfig())

    assert image.getpixel((0, 0)) == (0, 0, 0)


def test_force_refresh_bypasses_matching_cache(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    settings = base_settings()
    write_matching_cache(plugin, settings, tmp_path)
    calls = []

    def select_item(received_settings, rotation_key, exclude=frozenset()):
        calls.append((received_settings.get("forceRefresh"), rotation_key))
        return {"id": 2, "name": "Fresh Game"}

    monkeypatch.setattr(plugin, "_select_item", select_item)
    monkeypatch.setattr(
        plugin,
        "_render_item",
        lambda item, received_settings, dimensions: (
            "https://example.test/fresh.jpg",
            None,
            Image.new("RGB", (800, 480), "white"),
        ),
    )

    image = plugin.generate_image({**settings, "forceRefresh": True}, FakeDeviceConfig())
    cache_entry = plugin._read_cache()

    assert calls == [(True, "2026-06-29-15")]
    assert image.getpixel((0, 0)) == (255, 255, 255)
    assert cache_entry["name"] == "Fresh Game"


def test_settings_template_persists_refresh_on_display_default():
    settings_path = Path(__file__).resolve().parents[1] / "src" / "plugins" / "steam_daily_art" / "settings.html"
    html = settings_path.read_text(encoding="utf-8")

    assert 'name="refreshOnDisplay"' in html
    assert 'value="true"' in html


def test_bucketed_modes_are_no_change_and_only_every_refresh_is_prepared_bank(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)

    for selection_mode in (None, "current", "daily_rotation", "first", "random"):
        settings = base_settings()
        if selection_mode is None:
            settings.pop("selectionMode", None)
        else:
            settings["selectionMode"] = selection_mode
        assert plugin.presentation_mode(settings) is PresentationMode.NO_CHANGE

    assert plugin.presentation_mode(every_refresh_settings()) is PresentationMode.PREPARED_BANK


def test_legacy_layout_every_refresh_cadence_uses_prepared_bank(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    legacy = {
        **base_settings(),
        "selectionMode": "daily_rotation",
        "rotationCadence": "every_refresh",
    }

    assert plugin.presentation_mode(legacy) is PresentationMode.PREPARED_BANK


def test_omitted_and_explicit_current_share_stable_presentation_fingerprint(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    omitted = bind_presentation_instance_identity(
        {key: value for key, value in base_settings().items() if key != "selectionMode"},
        "steam-art-instance",
    )
    explicit = bind_presentation_instance_identity(
        {**base_settings(), "selectionMode": "current", "forceRefresh": True, "apiKey": "secret"},
        "steam-art-instance",
    )

    first = plugin._presentation_profile_fingerprint(omitted, (800, 480), "2026-07-12-10")
    second = plugin._presentation_profile_fingerprint(explicit, (800, 480), "2026-07-12-10")

    assert first == second


def test_omitted_and_explicit_current_share_bucket_cache_and_pixels(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    omitted = {key: value for key, value in base_settings().items() if key != "selectionMode"}
    explicit = {**base_settings(), "selectionMode": "current"}
    write_matching_cache(plugin, omitted, tmp_path)
    monkeypatch.setattr(
        plugin,
        "_select_item",
        lambda *_args, **_kwargs: pytest.fail("explicit current missed omitted cache"),
    )

    omitted_image = plugin.generate_image(omitted, FakeDeviceConfig())
    explicit_image = plugin.generate_image(explicit, FakeDeviceConfig())

    assert omitted_image.tobytes() == explicit_image.tobytes()


def test_manifest_declares_presentation_without_changing_saved_data_cadence():
    manifest_path = Path(__file__).resolve().parents[1] / "src" / "plugins" / "steam_daily_art" / "plugin-info.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert payload["capabilities"]["supports_presentation_refresh"] is True
    assert payload["refresh_on_display"] is True


def test_bucketed_prepare_is_provider_free_no_change(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    monkeypatch.setattr(plugin, "_fetch_featured_categories", lambda *_args: pytest.fail("bucketed prepare fetched Steam"))

    prepared = plugin.prepare_presentation(
        {**base_settings(), "selectionMode": "current"},
        FakeDeviceConfig(),
        request=presentation_request("a" * 32),
        resolved_theme_context={"mode": "day"},
    )

    assert prepared.changed is False
    assert prepared.image is None


def steam_featured_payload(count=10):
    return {
        "specials": {
            "id": "specials",
            "name": "Specials",
            "items": [
                {
                    "id": index,
                    "name": f"Steam Game {index}",
                    "large_capsule_image": (
                        f"https://cdn.cloudflare.steamstatic.com/steam/apps/{index}/capsule_616x353.jpg"
                    ),
                }
                for index in range(1, count + 1)
            ],
        }
    }


def read_presentation_state(plugin):
    return json.loads(plugin._presentation_state_path().read_text(encoding="utf-8"))


def hydrate_every_refresh_bank(plugin, monkeypatch, settings, count=10):
    calls = {"featured": 0, "image": 0}

    def featured(_settings):
        calls["featured"] += 1
        return steam_featured_payload(count)

    def image(item, _settings, _dimensions):
        calls["image"] += 1
        color = (int(item["id"]) * 17 % 255, 80, 120)
        return item["large_capsule_image"], None, Image.new("RGB", (800, 480), color)

    monkeypatch.setattr(plugin, "_fetch_featured_categories", featured)
    monkeypatch.setattr(plugin, "_render_item", image)
    rendered = plugin.generate_image(settings, FakeDeviceConfig())
    return rendered, calls


def active_profile(state, instance_uuid="steam-art-instance"):
    fingerprint = state["instance_profiles"][instance_uuid]
    return state["profiles"][fingerprint]


def test_every_refresh_data_hydrates_bank_without_consuming_selection(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    settings = every_refresh_settings()

    rendered, calls = hydrate_every_refresh_bank(plugin, monkeypatch, settings)
    state = read_presentation_state(plugin)
    profile = active_profile(state)

    assert rendered.size == (800, 480)
    assert calls["featured"] == 1
    assert 6 <= len(profile["records"]) <= 16
    assert profile["current_selection"] is None
    assert profile["pending_selection"] is None
    assert profile["date_buckets"] == {}


def test_every_refresh_prepare_is_provider_free_and_receipt_advances_exactly_once(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    settings = every_refresh_settings()
    hydrate_every_refresh_bank(plugin, monkeypatch, settings)
    before_prepare = read_presentation_state(plugin)
    monkeypatch.setattr(plugin, "_fetch_featured_categories", lambda *_args: pytest.fail("prepare fetched Steam"))
    monkeypatch.setattr(plugin, "_render_item", lambda *_args: pytest.fail("prepare downloaded"))

    prepared = plugin.prepare_presentation(
        settings,
        FakeDeviceConfig(),
        request=presentation_request("b" * 32),
        resolved_theme_context={"mode": "night", "palette": {"background": (0, 0, 0), "accent": (255, 255, 255)}},
    )
    pending_state = read_presentation_state(plugin)
    pending = active_profile(pending_state)["pending_selection"]

    assert prepared.changed is True
    assert prepared.image.size == (800, 480)
    assert active_profile(before_prepare)["date_buckets"] == {}
    assert pending["request_id"] == "b" * 32

    plugin.reconcile_presentation_receipt(settings, presentation_receipt("b" * 32))
    committed_bytes = plugin._presentation_state_path().read_bytes()
    committed = active_profile(read_presentation_state(plugin))
    plugin.reconcile_presentation_receipt(settings, presentation_receipt("b" * 32))

    assert plugin._presentation_state_path().read_bytes() == committed_bytes
    assert committed["pending_selection"] is None
    assert committed["current_selection"]["record_keys"] == pending["record_keys"]
    assert committed["date_buckets"]


def test_every_refresh_wrong_instance_receipt_cannot_commit_pending(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    settings = every_refresh_settings("trusted-instance")
    hydrate_every_refresh_bank(plugin, monkeypatch, settings)
    plugin.prepare_presentation(
        settings,
        FakeDeviceConfig(),
        request=presentation_request("c" * 32),
        resolved_theme_context=None,
    )
    before = plugin._presentation_state_path().read_bytes()

    plugin.reconcile_presentation_receipt(
        every_refresh_settings("wrong-instance"),
        presentation_receipt("c" * 32),
    )

    assert plugin._presentation_state_path().read_bytes() == before


def test_bucketed_live_fresh_and_cross_key_stale_provenance(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    settings = {**base_settings(), "selectionMode": "current"}
    item = {"id": 7, "name": "Live Game"}
    context_times = []
    monkeypatch.setattr(
        plugin,
        "_write_daily_art_context",
        lambda _entry, _settings, generated_at: context_times.append(generated_at),
    )
    monkeypatch.setattr(plugin, "_select_item", lambda *_args, **_kwargs: item)
    monkeypatch.setattr(
        plugin,
        "_render_item",
        lambda *_args: (
            "https://cdn.cloudflare.steamstatic.com/steam/apps/7/header.jpg",
            None,
            Image.new("RGB", (800, 480), "red"),
        ),
    )

    live = plugin.generate_image(settings, FakeDeviceConfig())
    fresh = plugin.generate_image(settings, FakeDeviceConfig())
    monkeypatch.setattr(plugin, "_now_for_device", lambda _device: datetime(2026, 6, 29, 16, 5, 0))
    monkeypatch.setattr(
        plugin,
        "_select_item",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("offline")),
    )
    stale = plugin.generate_image(settings, FakeDeviceConfig())

    assert read_source_provenance(live) is SourceProvenance.LIVE
    assert read_source_provenance(fresh) is SourceProvenance.FRESH_CACHE
    assert read_source_provenance(stale) is SourceProvenance.STALE_CACHE
    assert stale.info["inkypi_skip_cache"] is True
    assert context_times[-1] == datetime(2026, 6, 29, 15, 5, 0)


def test_stale_legacy_cache_without_generation_time_does_not_publish_context(
    tmp_path,
    monkeypatch,
):
    plugin = make_plugin(tmp_path, monkeypatch)
    settings = {**base_settings(), "selectionMode": "current"}
    write_matching_cache(plugin, settings, tmp_path)
    context_times = []
    monkeypatch.setattr(
        plugin,
        "_write_daily_art_context",
        lambda _entry, _settings, generated_at: context_times.append(generated_at),
    )
    monkeypatch.setattr(
        plugin,
        "_select_item",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("offline")),
    )

    image = plugin.generate_image(
        {**settings, "forceRefresh": True},
        FakeDeviceConfig(),
    )

    assert read_source_provenance(image) is SourceProvenance.STALE_CACHE
    assert context_times == []


def test_metadata_only_failure_card_is_explicit_local_fallback(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    settings = {**base_settings(), "selectionMode": "current"}
    monkeypatch.setattr(plugin, "_select_item", lambda *_args, **_kwargs: {"id": 9, "name": "Offline Game"})
    monkeypatch.setattr(
        plugin,
        "_render_item",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("offline")),
    )

    image = plugin.generate_image(settings, FakeDeviceConfig())

    assert image.size == (800, 480)
    assert read_source_provenance(image) is SourceProvenance.LOCAL_FALLBACK
    assert image.info["inkypi_skip_cache"] is True


def test_steam_target_validation_rejects_wrong_authority_and_non_https(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    valid = SimpleNamespace(scheme="https", port=443, hostname="cdn.cloudflare.steamstatic.com", normalized_url="https://cdn.cloudflare.steamstatic.com/a.jpg", addresses=("93.184.216.34",))
    wrong = SimpleNamespace(scheme="https", port=443, hostname="evil.example", normalized_url="https://evil.example/a.jpg", addresses=("93.184.216.34",))
    plain = SimpleNamespace(scheme="http", port=80, hostname="cdn.cloudflare.steamstatic.com", normalized_url="http://cdn.cloudflare.steamstatic.com/a.jpg", addresses=("93.184.216.34",))

    assert plugin._validate_steam_target(valid, kind="media") == valid.normalized_url
    with pytest.raises(RuntimeError, match="Steam|authority|HTTPS"):
        plugin._validate_steam_target(wrong, kind="media")
    with pytest.raises(RuntimeError, match="Steam|authority|HTTPS"):
        plugin._validate_steam_target(plain, kind="media")


@pytest.mark.parametrize(
    "addresses",
    [
        (),
        ("93.184.216.34", "127.0.0.1"),
        ("10.0.0.1",),
        ("169.254.169.254",),
        ("fe80::1",),
        ("::ffff:127.0.0.1",),
    ],
)
def test_steam_target_validation_rechecks_every_approved_address(tmp_path, monkeypatch, addresses):
    plugin = make_plugin(tmp_path, monkeypatch)
    target = SimpleNamespace(
        scheme="https",
        port=443,
        hostname="cdn.cloudflare.steamstatic.com",
        normalized_url="https://cdn.cloudflare.steamstatic.com/a.jpg",
        addresses=addresses,
    )

    with pytest.raises(RuntimeError, match="Steam|address|public"):
        plugin._validate_steam_target(target, kind="media")


def test_every_refresh_fingerprint_preserves_legacy_selection_semantics(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    fingerprints = {
        mode: plugin._presentation_profile_fingerprint(
            every_refresh_settings(selectionMode=mode, rotationCadence="every_refresh"),
            (800, 480),
            "2026-07-12-10",
        )
        for mode in ("first", "random", "daily_rotation")
    }

    assert len(set(fingerprints.values())) == 3


@pytest.mark.parametrize(
    ("selection_mode", "expected_artwork_id"),
    [
        ("first", "app:1"),
        ("daily_rotation", "app:1"),
        ("random", "app:8"),
    ],
)
def test_every_refresh_bank_preserves_first_ranked_and_random_modes(
    tmp_path,
    monkeypatch,
    selection_mode,
    expected_artwork_id,
):
    plugin = make_plugin(tmp_path, monkeypatch)
    settings = every_refresh_settings(
        selectionMode=selection_mode,
        rotationCadence="every_refresh",
    )
    hydrate_every_refresh_bank(plugin, monkeypatch, settings)
    monkeypatch.setattr(
        "plugins.daily_art.presentation_bank.random.shuffle",
        lambda values: values.reverse(),
    )

    plugin.prepare_presentation(
        settings,
        FakeDeviceConfig(),
        request=presentation_request("e" * 32),
        resolved_theme_context=None,
    )
    profile = active_profile(read_presentation_state(plugin))
    pending_key = profile["pending_selection"]["record_keys"][0]
    record = next(item for item in profile["records"] if item["record_key"] == pending_key)

    assert record["artwork_id"] == expected_artwork_id


def test_every_refresh_daily_rotation_advances_in_rank_order_after_receipt(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    settings = every_refresh_settings(
        selectionMode="daily_rotation",
        rotationCadence="every_refresh",
    )
    hydrate_every_refresh_bank(plugin, monkeypatch, settings)
    monkeypatch.setattr(
        "plugins.daily_art.presentation_bank.random.shuffle",
        lambda values: values.reverse(),
    )

    plugin.prepare_presentation(
        settings,
        FakeDeviceConfig(),
        request=presentation_request("1" * 32),
        resolved_theme_context=None,
    )
    plugin.reconcile_presentation_receipt(
        settings,
        presentation_receipt("1" * 32, display="display-1"),
    )
    plugin.prepare_presentation(
        settings,
        FakeDeviceConfig(),
        request=presentation_request("2" * 32, origin="display-1"),
        resolved_theme_context=None,
    )
    profile = active_profile(read_presentation_state(plugin))
    pending_key = profile["pending_selection"]["record_keys"][0]
    record = next(item for item in profile["records"] if item["record_key"] == pending_key)

    assert record["artwork_id"] == "app:2"


@pytest.mark.parametrize(
    ("selection_mode", "expected"),
    [
        ("first", ["app:1", "app:1"]),
        ("random", ["app:8", "app:7"]),
    ],
)
def test_every_refresh_first_repeats_and_random_remains_no_repeat(
    tmp_path,
    monkeypatch,
    selection_mode,
    expected,
):
    plugin = make_plugin(tmp_path, monkeypatch)
    settings = every_refresh_settings(
        selectionMode=selection_mode,
        rotationCadence="every_refresh",
    )
    hydrate_every_refresh_bank(plugin, monkeypatch, settings)
    monkeypatch.setattr(
        "plugins.daily_art.presentation_bank.random.shuffle",
        lambda values: values.reverse(),
    )
    selected = []

    for index, origin in (("a", "origin-display"), ("b", "display-a")):
        request_id = index * 32
        plugin.prepare_presentation(
            settings,
            FakeDeviceConfig(),
            request=presentation_request(request_id, origin=origin),
            resolved_theme_context=None,
        )
        profile = active_profile(read_presentation_state(plugin))
        pending_key = profile["pending_selection"]["record_keys"][0]
        selected.append(
            next(
                record["artwork_id"]
                for record in profile["records"]
                if record["record_key"] == pending_key
            )
        )
        plugin.reconcile_presentation_receipt(
            settings,
            presentation_receipt(request_id, display=f"display-{index}"),
        )

    assert selected == expected


def _tree_snapshot(root):
    root = Path(root)
    if not root.exists():
        return {}
    return {
        path.relative_to(root).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in root.rglob("*")
        if path.is_file()
    }


def test_theme_only_cold_every_refresh_fails_closed_without_io_or_directories(tmp_path, monkeypatch):
    cache_root = tmp_path / "cold-steam-cache"
    plugin = make_plugin(cache_root, monkeypatch)
    settings = every_refresh_settings(_theme_render_only=True)
    calls = {"provider": 0, "image": 0, "context": 0}
    monkeypatch.setattr(plugin, "_fetch_featured_categories", lambda *_args: calls.__setitem__("provider", calls["provider"] + 1))
    monkeypatch.setattr(plugin, "_render_item", lambda *_args: calls.__setitem__("image", calls["image"] + 1))
    monkeypatch.setattr(plugin, "_write_daily_art_context", lambda *_args, **_kwargs: calls.__setitem__("context", calls["context"] + 1))

    with pytest.raises(RuntimeError, match="cold|theme|bank"):
        plugin.generate_image(settings, FakeDeviceConfig())

    assert calls == {"provider": 0, "image": 0, "context": 0}
    assert not cache_root.exists()


def test_theme_only_warm_every_refresh_is_byte_and_timestamp_read_only(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    settings = every_refresh_settings(selectionMode="daily_rotation", rotationCadence="every_refresh")
    hydrate_every_refresh_bank(plugin, monkeypatch, settings)
    plugin.prepare_presentation(
        settings,
        FakeDeviceConfig(),
        request=presentation_request("3" * 32),
        resolved_theme_context=None,
    )
    plugin.reconcile_presentation_receipt(settings, presentation_receipt("3" * 32))
    before = _tree_snapshot(tmp_path)
    monkeypatch.setattr(plugin, "_fetch_featured_categories", lambda *_args: pytest.fail("theme-only fetched provider"))
    monkeypatch.setattr(plugin, "_render_item", lambda *_args: pytest.fail("theme-only downloaded media"))
    monkeypatch.setattr(plugin, "_write_daily_art_context", lambda *_args, **_kwargs: pytest.fail("theme-only wrote context"))

    image = plugin.generate_image({**settings, "_theme_render_only": True}, FakeDeviceConfig())

    assert image.size == (800, 480)
    assert _tree_snapshot(tmp_path) == before


def test_every_refresh_receipt_writes_actual_committed_context_only_once(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    settings = every_refresh_settings(selectionMode="daily_rotation", rotationCadence="every_refresh")
    hydrate_every_refresh_bank(plugin, monkeypatch, settings)
    written = []
    monkeypatch.setattr(plugin, "_write_daily_art_context", lambda entry, received, generated: written.append((entry, received, generated)))

    plugin.prepare_presentation(
        settings,
        FakeDeviceConfig(),
        request=presentation_request("4" * 32),
        resolved_theme_context=None,
    )
    before_failed_receipt = plugin._presentation_state_path().read_bytes()
    plugin.reconcile_presentation_receipt(
        settings,
        presentation_receipt("4" * 32, display="origin-display"),
    )
    assert written == []
    assert plugin._presentation_state_path().read_bytes() == before_failed_receipt
    plugin.reconcile_presentation_receipt(settings, presentation_receipt("4" * 32, display="display-4"))
    plugin.reconcile_presentation_receipt(settings, presentation_receipt("4" * 32, display="display-4"))
    plugin.reconcile_presentation_receipt(
        every_refresh_settings("foreign-instance", selectionMode="daily_rotation", rotationCadence="every_refresh"),
        presentation_receipt("4" * 32, display="foreign"),
    )

    assert len(written) == 1
    entry, received, generated = written[0]
    assert entry["name"] == "Steam Game 1"
    assert entry["appid"] == 1
    assert entry["rotation_key"] == "2026-06-29-15"
    assert entry["image_url"].endswith("/1/capsule_616x353.jpg")
    assert received is settings
    assert generated == "2026-07-12T10:01:00+00:00"


def test_every_refresh_context_advances_with_each_committed_record(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    settings = every_refresh_settings(selectionMode="daily_rotation", rotationCadence="every_refresh")
    hydrate_every_refresh_bank(plugin, monkeypatch, settings)
    written = []
    monkeypatch.setattr(plugin, "_write_daily_art_context", lambda entry, *_args: written.append(entry["name"]))

    for index, origin in ((5, "origin-display"), (6, "display-5")):
        request_id = str(index) * 32
        plugin.prepare_presentation(
            settings,
            FakeDeviceConfig(),
            request=presentation_request(request_id, origin=origin),
            resolved_theme_context=None,
        )
        plugin.reconcile_presentation_receipt(
            settings,
            presentation_receipt(request_id, display=f"display-{index}"),
        )

    assert written == ["Steam Game 1", "Steam Game 2"]


def test_steam_bank_limits_and_cleanup_protect_pending_media(tmp_path, monkeypatch):
    from plugins.steam_daily_art import presentation_bank

    assert presentation_bank.READY_TARGET == 16
    assert presentation_bank.REFILL_THRESHOLD == 6
    assert presentation_bank.MEDIA_MAX_AGE_SECONDS == 48 * 60 * 60
    assert presentation_bank.MEDIA_MAX_FILES == 48
    assert presentation_bank.MEDIA_MAX_BYTES == 96 * 1024 * 1024
    assert presentation_bank.MEDIA_MAX_OBJECT_BYTES == 12 * 1024 * 1024

    plugin = make_plugin(tmp_path, monkeypatch)
    settings = every_refresh_settings()
    hydrate_every_refresh_bank(plugin, monkeypatch, settings)
    plugin.prepare_presentation(
        settings,
        FakeDeviceConfig(),
        request=presentation_request("d" * 32),
        resolved_theme_context=None,
    )
    bank = plugin._presentation_bank(
        settings,
        (800, 480),
        plugin._bank_rotation_key(FakeDeviceConfig(), settings),
    )
    document, profile = bank.load_warm()
    pending_key = profile["pending_selection"]["record_keys"][0]
    pending_record = next(record for record in profile["records"] if record["record_key"] == pending_key)
    pending_path = bank.media.path(pending_record["media_key"], suffix=".png")
    orphan_key = "f" * 64
    bank.media.put_bytes(orphan_key, b"orphan", suffix=".png")

    bank.cleanup(document, profile)

    assert pending_path.is_file()
    assert not bank.media.path(orphan_key, suffix=".png").exists()


def test_steam_cleanup_retains_only_current_and_pending_not_all_history(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    settings = every_refresh_settings(selectionMode="daily_rotation", rotationCadence="every_refresh")
    hydrate_every_refresh_bank(plugin, monkeypatch, settings)
    plugin.prepare_presentation(
        settings,
        FakeDeviceConfig(),
        request=presentation_request("7" * 32),
        resolved_theme_context=None,
    )
    plugin.reconcile_presentation_receipt(
        settings,
        presentation_receipt("7" * 32, display="display-7"),
    )
    plugin.prepare_presentation(
        settings,
        FakeDeviceConfig(),
        request=presentation_request("8" * 32, origin="display-7"),
        resolved_theme_context=None,
    )
    bank = plugin._presentation_bank(
        settings,
        (800, 480),
        plugin._bank_rotation_key(FakeDeviceConfig(), settings),
    )
    document, profile = bank.load_warm()
    protected_keys = {
        *profile["current_selection"]["record_keys"],
        *profile["pending_selection"]["record_keys"],
    }
    for record in profile["records"]:
        record["downloaded_at"] = "2020-01-01T00:00:00+00:00"
    historical_media = {
        record["media_key"]
        for record in profile["records"]
        if record["record_key"] not in protected_keys
    }

    bank.cleanup(document, profile)

    assert {record["record_key"] for record in profile["records"]} == protected_keys
    assert all(
        not bank.media.path(media_key, suffix=".png").exists()
        for media_key in historical_media
    )


def test_steam_cleanup_protects_latest_receipt_but_reclaims_older_inactive_current(
    tmp_path,
    monkeypatch,
):
    plugin = make_plugin(tmp_path, monkeypatch)
    settings = every_refresh_settings(selectionMode="daily_rotation", rotationCadence="every_refresh")
    hydrate_every_refresh_bank(plugin, monkeypatch, settings)
    plugin.prepare_presentation(
        settings,
        FakeDeviceConfig(),
        request=presentation_request("9" * 32),
        resolved_theme_context=None,
    )
    plugin.reconcile_presentation_receipt(
        settings,
        presentation_receipt(
            "9" * 32,
            display="display-9",
            committed_at="2026-07-12T10:01:00+00:00",
        ),
    )
    old_state = read_presentation_state(plugin)
    old_fingerprint = old_state["instance_profiles"]["steam-art-instance"]

    monkeypatch.setattr(
        plugin,
        "_now_for_device",
        lambda _device: datetime(2026, 6, 29, 16, 5, 0),
    )
    changed_settings = {**settings, "imageMode": "header"}
    hydrate_every_refresh_bank(plugin, monkeypatch, changed_settings)
    plugin.prepare_presentation(
        changed_settings,
        FakeDeviceConfig(),
        request=presentation_request("e1" * 16, origin="display-9"),
        resolved_theme_context=None,
    )
    plugin.reconcile_presentation_receipt(
        changed_settings,
        presentation_receipt(
            "e1" * 16,
            display="display-latest",
            committed_at="2026-07-12T11:01:00+00:00",
        ),
    )
    changed_state = read_presentation_state(plugin)
    latest_fingerprint = changed_state["instance_profiles"]["steam-art-instance"]
    latest_current_key = changed_state["profiles"][latest_fingerprint]["current_selection"]["record_keys"][0]

    third_settings = {**settings, "showCaption": "true"}
    newest_bank = plugin._presentation_bank(
        third_settings,
        (800, 480),
        plugin._bank_rotation_key(FakeDeviceConfig(), third_settings),
    )
    document, profile = newest_bank.load_for_data()
    for fingerprint in (old_fingerprint, latest_fingerprint):
        for record in document["profiles"][fingerprint]["records"]:
            record["downloaded_at"] = "2020-01-01T00:00:00+00:00"

    newest_bank.cleanup(document, profile)

    assert document["profiles"][old_fingerprint]["records"] == []
    assert document["profiles"][old_fingerprint]["current_selection"] is None
    assert [
        record["record_key"]
        for record in document["profiles"][latest_fingerprint]["records"]
    ] == [latest_current_key]
    assert document["profiles"][latest_fingerprint]["current_selection"]["record_keys"] == [
        latest_current_key
    ]


def test_every_refresh_profile_identity_is_stable_across_hours(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    settings = every_refresh_settings(selectionMode="daily_rotation", rotationCadence="every_refresh")
    first = plugin._presentation_profile_fingerprint(settings, (800, 480), "2026-07-12-10")
    second = plugin._presentation_profile_fingerprint(settings, (800, 480), "2026-07-12-11")

    assert first == second


@pytest.mark.parametrize("selection_mode", ["daily_rotation", "random"])
def test_every_refresh_selection_exhausts_one_pool_without_repeat_across_hours(
    tmp_path,
    monkeypatch,
    selection_mode,
):
    plugin = make_plugin(tmp_path, monkeypatch)
    current_hour = {"value": 15}
    monkeypatch.setattr(
        plugin,
        "_now_for_device",
        lambda _device: datetime(2026, 6, 29, current_hour["value"], 5, 0),
    )
    settings = every_refresh_settings(
        selectionMode=selection_mode,
        rotationCadence="every_refresh",
    )
    hydrate_every_refresh_bank(plugin, monkeypatch, settings)
    monkeypatch.setattr(
        "plugins.daily_art.presentation_bank.random.shuffle",
        lambda values: values.reverse(),
    )
    selected = []
    origin = "origin-display"

    for index in range(8):
        current_hour["value"] = (15 + index) % 24
        request_id = f"{index + 1:032x}"
        plugin.prepare_presentation(
            settings,
            FakeDeviceConfig(),
            request=presentation_request(request_id, origin=origin),
            resolved_theme_context=None,
        )
        profile = active_profile(read_presentation_state(plugin))
        pending_key = profile["pending_selection"]["record_keys"][0]
        selected.append(
            next(
                record["artwork_id"]
                for record in profile["records"]
                if record["record_key"] == pending_key
            )
        )
        origin = f"cross-hour-display-{index}"
        plugin.reconcile_presentation_receipt(
            settings,
            presentation_receipt(request_id, display=origin),
        )

    state = read_presentation_state(plugin)
    assert len(set(selected)) == 8
    assert len(state["profiles"]) == 1


def test_theme_only_reuses_actual_committed_current_across_hour_boundary(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    current_hour = {"value": 15}
    monkeypatch.setattr(
        plugin,
        "_now_for_device",
        lambda _device: datetime(2026, 6, 29, current_hour["value"], 5, 0),
    )
    settings = every_refresh_settings(selectionMode="daily_rotation", rotationCadence="every_refresh")
    hydrate_every_refresh_bank(plugin, monkeypatch, settings)
    prepared = plugin.prepare_presentation(
        settings,
        FakeDeviceConfig(),
        request=presentation_request("a1" * 16),
        resolved_theme_context=None,
    )
    plugin.reconcile_presentation_receipt(
        settings,
        presentation_receipt("a1" * 16, display="cross-hour-current"),
    )
    before = _tree_snapshot(tmp_path)
    current_hour["value"] = 16
    monkeypatch.setattr(plugin, "_fetch_featured_categories", lambda *_args: pytest.fail("theme fetched provider"))

    themed = plugin.generate_image(
        {**settings, "_theme_render_only": True},
        FakeDeviceConfig(),
    )

    assert themed.tobytes() == prepared.image.tobytes()
    assert _tree_snapshot(tmp_path) == before
    assert len(read_presentation_state(plugin)["profiles"]) == 1


def test_cross_hour_cleanup_keeps_active_committed_current_but_reclaims_expired_history(
    tmp_path,
    monkeypatch,
):
    plugin = make_plugin(tmp_path, monkeypatch)
    current_hour = {"value": 15}
    monkeypatch.setattr(
        plugin,
        "_now_for_device",
        lambda _device: datetime(2026, 6, 29, current_hour["value"], 5, 0),
    )
    settings = every_refresh_settings(selectionMode="daily_rotation", rotationCadence="every_refresh")
    hydrate_every_refresh_bank(plugin, monkeypatch, settings)
    plugin.prepare_presentation(
        settings,
        FakeDeviceConfig(),
        request=presentation_request("b1" * 16),
        resolved_theme_context=None,
    )
    plugin.reconcile_presentation_receipt(
        settings,
        presentation_receipt("b1" * 16, display="cleanup-current"),
    )
    current_hour["value"] = 16
    bank = plugin._presentation_bank(
        settings,
        (800, 480),
        plugin._bank_rotation_key(FakeDeviceConfig(), settings),
    )
    document, profile = bank.load_for_data()
    current_key = profile["current_selection"]["record_keys"][0]
    for record in profile["records"]:
        record["downloaded_at"] = "2020-01-01T00:00:00+00:00"

    bank.cleanup(document, profile)

    assert [record["record_key"] for record in profile["records"]] == [current_key]
    assert len(document["profiles"]) == 1


def _steam_featured_payload_range(start, count):
    payload = steam_featured_payload(0)
    payload["specials"]["items"] = [
        {
            "id": index,
            "name": f"Steam Game {index}",
            "large_capsule_image": (
                f"https://cdn.cloudflare.steamstatic.com/steam/apps/{index}/capsule_616x353.jpg"
            ),
        }
        for index in range(start, start + count)
    ]
    return payload


def test_every_refresh_force_refresh_hydrates_source_without_advancing_committed_state(
    tmp_path,
    monkeypatch,
):
    plugin = make_plugin(tmp_path, monkeypatch)
    settings = every_refresh_settings(selectionMode="daily_rotation", rotationCadence="every_refresh")
    hydrate_every_refresh_bank(plugin, monkeypatch, settings, count=20)
    plugin.generate_image(settings, FakeDeviceConfig())
    plugin.prepare_presentation(
        settings,
        FakeDeviceConfig(),
        request=presentation_request("c1" * 16),
        resolved_theme_context=None,
    )
    plugin.reconcile_presentation_receipt(
        settings,
        presentation_receipt("c1" * 16, display="force-current"),
    )
    before = active_profile(read_presentation_state(plugin))
    calls = {"provider": 0, "image": 0}

    def provider(_settings):
        calls["provider"] += 1
        return _steam_featured_payload_range(20, 8)

    def image(item, _settings, _dimensions):
        calls["image"] += 1
        return item["large_capsule_image"], None, Image.new("RGB", (800, 480), (20, int(item["id"]), 90))

    monkeypatch.setattr(plugin, "_fetch_featured_categories", provider)
    monkeypatch.setattr(plugin, "_render_item", image)

    plugin.generate_image({**settings, "forceRefresh": True}, FakeDeviceConfig())
    after = active_profile(read_presentation_state(plugin))

    assert calls["provider"] == 1
    assert calls["image"] > 0
    assert after["profile_fingerprint"] == before["profile_fingerprint"]
    assert after["current_selection"] == before["current_selection"]
    assert after["pending_selection"] == before["pending_selection"]
    assert after["date_buckets"] == before["date_buckets"]
    assert any(record["artwork_id"] == "app:20" for record in after["records"])


def test_every_refresh_force_provider_failure_is_state_and_media_fail_closed(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    settings = every_refresh_settings(selectionMode="daily_rotation", rotationCadence="every_refresh")
    hydrate_every_refresh_bank(plugin, monkeypatch, settings, count=20)
    plugin.generate_image(settings, FakeDeviceConfig())
    before_state = plugin._presentation_state_path().read_bytes()
    before_media = {
        path.name: path.read_bytes()
        for path in plugin._presentation_media_dir().glob("*.png")
    }
    monkeypatch.setattr(
        plugin,
        "_fetch_featured_categories",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("provider unavailable")),
    )

    with pytest.raises(RuntimeError, match="provider unavailable"):
        plugin.generate_image({**settings, "forceRefresh": True}, FakeDeviceConfig())

    assert plugin._presentation_state_path().read_bytes() == before_state
    assert {
        path.name: path.read_bytes()
        for path in plugin._presentation_media_dir().glob("*.png")
    } == before_media


def test_every_refresh_full_warm_bank_renders_without_provider_or_unbound_state(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    settings = every_refresh_settings(selectionMode="daily_rotation", rotationCadence="every_refresh")
    hydrate_every_refresh_bank(plugin, monkeypatch, settings, count=20)
    plugin.generate_image(settings, FakeDeviceConfig())
    monkeypatch.setattr(plugin, "_fetch_featured_categories", lambda *_args: pytest.fail("full bank fetched"))

    image = plugin.generate_image(settings, FakeDeviceConfig())

    assert image.size == (800, 480)
    assert read_source_provenance(image) is SourceProvenance.FRESH_CACHE


# --- Art selection: a game's logo, scene and fallbacks stay with that game ---

STORE_ROOT = steam_daily_art_module.STEAM_STORE_ASSET_ROOT
NEAR_BLACK = (3, 3, 4)  # Mumble Pines' real library hero averages 2.7/255
LIT = (150, 170, 160)


def show_logo_settings(**overrides):
    return {**base_settings(), "logoOverlay": "show", **overrides}


def _art_plugin(tmp_path, monkeypatch, media, *, assets=None):
    """A plugin whose downloads come from media: url -> image (else a 404)."""

    plugin = make_plugin(tmp_path, monkeypatch)
    calls = {"media": [], "assets": []}

    def download(url):
        calls["media"].append(url)
        value = media.get(url)
        if isinstance(value, Exception):
            raise value
        if value is None:
            raise RuntimeError("Steam media request failed with status 404")
        return value.copy()

    def store_assets(appid, _settings):
        calls["assets"].append(appid)
        return dict(assets or {})

    monkeypatch.setattr(plugin, "_download_image", download)
    monkeypatch.setattr(plugin, "_fetch_store_assets", store_assets)
    return plugin, calls


def _with_logo(monkeypatch, plugin, logo):
    found = ("https://cdn.cloudflare.steamstatic.com/steam/apps/logo.png", logo) if logo else (None, None)
    monkeypatch.setattr(plugin, "_download_first_available_logo", lambda _item: found)


def _red_logo(size=(300, 300)):
    logo = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(logo).rectangle((20, 20, size[0] - 20, size[1] - 20), fill=(255, 0, 0, 255))
    return logo


def _colors(image):
    return {color for _count, color in image.getcolors(image.width * image.height)}


def _lit(plugin, image):
    return plugin._is_lit(plugin._scene_light(image, image.size))


def test_dark_library_hero_falls_back_to_the_same_games_brightest_screenshot(tmp_path, monkeypatch):
    appid = 3567020
    legacy_hero = f"https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/library_hero.jpg"
    root = f"{STORE_ROOT}steam/apps/{appid}"
    hashed_hero = f"{root}/e9f5/library_hero.jpg"
    shots = [(f"{root}/{name}/ss_{name}.jpg", f"{root}/{name}/ss_{name}.600x338.jpg") for name in "abc"]
    media = {
        legacy_hero: Image.new("RGB", (1920, 620), NEAR_BLACK),
        hashed_hero: Image.new("RGB", (1920, 620), NEAR_BLACK),
        shots[0][1]: Image.new("RGB", (600, 338), (12, 14, 16)),
        shots[1][1]: Image.new("RGB", (600, 338), (90, 110, 100)),
        shots[2][1]: Image.new("RGB", (600, 338), LIT),
        shots[1][0]: Image.new("RGB", (1920, 1080), (90, 110, 100)),
        shots[2][0]: Image.new("RGB", (1920, 1080), LIT),
    }
    plugin, calls = _art_plugin(
        tmp_path,
        monkeypatch,
        media,
        assets={"library_hero": hashed_hero, "screenshots": shots},
    )
    _with_logo(monkeypatch, plugin, _red_logo())
    item = {"id": appid, "name": "Mumble Pines: The Arrival"}

    image_url, logo_url, image = plugin._render_item(item, show_logo_settings(), (800, 480))

    assert image_url == shots[2][0]
    assert logo_url is not None
    assert calls["assets"] == [str(appid)]
    assert shots[0][0] not in calls["media"] and shots[1][0] not in calls["media"]
    assert _lit(plugin, image)
    assert (255, 0, 0) in _colors(image)


def test_scene_without_its_logo_yields_to_the_titled_store_capsule(tmp_path, monkeypatch):
    hero = "https://cdn.cloudflare.steamstatic.com/steam/apps/5/library_hero.jpg"
    capsule = f"{STORE_ROOT}steam/apps/5/c/capsule_616x353.jpg"
    plugin, calls = _art_plugin(
        tmp_path,
        monkeypatch,
        {hero: Image.new("RGB", (1920, 620), LIT), capsule: Image.new("RGB", (616, 353), (200, 40, 40))},
    )
    _with_logo(monkeypatch, plugin, None)
    item = {"id": 5, "name": "No Logo Game", "large_capsule_image": capsule}

    image_url, logo_url, _image = plugin._render_item(item, show_logo_settings(), (800, 480))

    assert (image_url, logo_url) == (capsule, None)
    assert hero not in calls["media"]


def test_titled_art_never_gets_a_second_title_from_the_logo(tmp_path, monkeypatch):
    capsule = f"{STORE_ROOT}steam/apps/5/c/capsule_616x353.jpg"
    plugin, _calls = _art_plugin(
        tmp_path,
        monkeypatch,
        {capsule: Image.new("RGB", (616, 353), (200, 40, 40))},
    )
    _with_logo(monkeypatch, plugin, _red_logo())
    item = {"id": 5, "name": "Capsule Game", "large_capsule_image": capsule}

    image_url, logo_url, image = plugin._render_item(
        item,
        show_logo_settings(imageMode="large_capsule"),
        (800, 480),
    )

    assert (image_url, logo_url) == (capsule, None)
    assert _colors(image) == {(200, 40, 40)}


def test_bundle_never_borrows_art_or_logo_from_the_app_sharing_its_number(tmp_path, monkeypatch):
    bundle_capsule = f"{STORE_ROOT}steam/bundles/32470/x/capsule_616x353.jpg"
    unrelated = "https://cdn.cloudflare.steamstatic.com/steam/apps/32470/"
    plugin, calls = _art_plugin(
        tmp_path,
        monkeypatch,
        {
            unrelated + "library_hero.jpg": Image.new("RGB", (1920, 620), LIT),
            unrelated + "capsule_616x353.jpg": Image.new("RGB", (616, 353), LIT),
            bundle_capsule: Image.new("RGB", (616, 353), (0, 0, 255)),
        },
    )
    bundle = {
        "id": 32470,
        "type": 2,
        "name": "Cyberpunk 2077: Ultimate Edition",
        "large_capsule_image": bundle_capsule,
    }

    image_url, logo_url, _image = plugin._render_item(bundle, show_logo_settings(), (800, 480))

    assert (image_url, logo_url) == (bundle_capsule, None)
    assert calls["assets"] == []
    assert not [url for url in calls["media"] if "/apps/32470/" in url]
    assert plugin._logo_candidate_urls(bundle) == [None, None, None]
    assert plugin._item_identity(bundle) == "bundle:32470"


def _featured(*items):
    return {"specials": {"id": "specials", "name": "Specials", "items": list(items)}}


def _game(appid, name):
    return {"id": appid, "name": name, "large_capsule_image": f"{STORE_ROOT}steam/apps/{appid}/c/capsule.jpg"}


def test_rotation_moves_past_a_game_whose_art_is_all_dark(tmp_path, monkeypatch):
    dark, lit = _game(1, "Dark Game"), _game(2, "Lit Game")
    plugin, _calls = _art_plugin(
        tmp_path,
        monkeypatch,
        {
            dark["large_capsule_image"]: Image.new("RGB", (616, 353), NEAR_BLACK),
            lit["large_capsule_image"]: Image.new("RGB", (616, 353), LIT),
        },
    )
    monkeypatch.setattr(plugin, "_fetch_featured_categories", lambda _settings: _featured(dark, lit))

    image = plugin.generate_image({**base_settings(), "selectionMode": "current"}, FakeDeviceConfig())

    pool = next(iter(plugin._read_selection_state().values()))
    assert read_source_provenance(image) is SourceProvenance.LIVE
    assert plugin._read_cache()["name"] == "Lit Game"
    assert pool["used"] == ["app:1", "app:2"]
    assert _lit(plugin, image)


def test_when_every_game_is_dark_the_fallback_is_a_light_title_card(tmp_path, monkeypatch):
    games = [_game(index, f"Dark Game {index}") for index in range(1, 5)]
    plugin, _calls = _art_plugin(
        tmp_path,
        monkeypatch,
        {game["large_capsule_image"]: Image.new("RGB", (616, 353), NEAR_BLACK) for game in games},
    )
    monkeypatch.setattr(plugin, "_fetch_featured_categories", lambda _settings: _featured(*games))

    image = plugin.generate_image({**base_settings(), "selectionMode": "current"}, FakeDeviceConfig())

    pool = next(iter(plugin._read_selection_state().values()))
    assert read_source_provenance(image) is SourceProvenance.LOCAL_FALLBACK
    assert image.info["inkypi_skip_cache"] is True
    assert len(pool["used"]) == steam_daily_art_module.MAX_ART_GAMES
    assert image.getpixel((image.width // 2, image.height - 8)) == (255, 255, 255)
    assert _lit(plugin, image)


def test_a_download_outage_does_not_burn_through_the_rotation(tmp_path, monkeypatch):
    games = [_game(index, f"Game {index}") for index in range(1, 4)]
    outage = RuntimeError("Steam media request failed with status 503")
    plugin, _calls = _art_plugin(
        tmp_path,
        monkeypatch,
        {
            url: outage
            for game in games
            for url in (
                game["large_capsule_image"],
                f"https://cdn.cloudflare.steamstatic.com/steam/apps/{game['id']}/library_hero.jpg",
                f"https://cdn.cloudflare.steamstatic.com/steam/apps/{game['id']}/capsule_616x353.jpg",
            )
        },
    )
    fetches = []
    monkeypatch.setattr(
        plugin,
        "_fetch_featured_categories",
        lambda _settings: fetches.append(1) or _featured(*games),
    )

    image = plugin.generate_image({**base_settings(), "selectionMode": "current"}, FakeDeviceConfig())

    assert read_source_provenance(image) is SourceProvenance.LOCAL_FALLBACK
    assert len(fetches) == 1


def test_store_assets_keep_only_this_apps_paths_and_all_ages_screenshots():
    payload = {"response": {"store_items": [
        {
            "appid": 999,
            "success": 1,
            "assets": {"asset_url_format": "steam/apps/999/${FILENAME}", "library_hero": "x/library_hero.jpg"},
        },
        {
            "appid": 3567020,
            "success": 1,
            "assets": {
                "asset_url_format": "steam/apps/3567020/${FILENAME}?t=1",
                "library_hero": "e9/library_hero.jpg",
                "main_capsule_2x": "61/capsule_616x353_2x.jpg",
                "header": "../../999/header.jpg",
            },
            "screenshots": {
                "all_ages_screenshots": [
                    {"filename": "steam/apps/3567020/5f/ss_5f.jpg?t=1"},
                    {"filename": "steam/apps/999/ss_other.jpg"},
                    "not-a-screenshot",
                ],
                "mature_content_screenshots": [{"filename": "steam/apps/3567020/m/ss_m.jpg"}],
            },
        },
    ]}}
    app = f"{STORE_ROOT}steam/apps/3567020"

    assert SteamDailyArt._parse_store_assets(payload, "3567020") == {
        "library_hero": f"{app}/e9/library_hero.jpg?t=1",
        "main_capsule_2x": f"{app}/61/capsule_616x353_2x.jpg?t=1",
        "screenshots": [(f"{app}/5f/ss_5f.jpg?t=1", f"{app}/5f/ss_5f.600x338.jpg?t=1")],
    }
    foreign_format = {"response": {"store_items": [{
        "appid": 7,
        "success": 1,
        "assets": {"asset_url_format": "steam/apps/8/${FILENAME}", "library_hero": "h/library_hero.jpg"},
    }]}}
    assert SteamDailyArt._parse_store_assets(foreign_format, "7") == {"screenshots": []}
    assert SteamDailyArt._parse_store_assets({"response": {"store_items": [{"appid": 7, "success": 2}]}}, "7") == {}
    assert SteamDailyArt._parse_store_assets({"response": "bad"}, "7") == {}


def test_spotlight_promotes_the_app_its_link_names(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    sale_art = f"{STORE_ROOT}steam/spotlights/y/vertical_capsule_english.jpg"
    data = {"0": {"id": "cat_spotlight", "name": "Spotlights", "items": [
        {
            "name": "MIDWEEK DEAL",
            "header_image": f"{STORE_ROOT}steam/spotlights/x/vertical_capsule_english.png",
            "url": "https://store.steampowered.com/app/463530",
        },
        {"name": "FRANCHISE SALE", "header_image": sale_art, "url": "https://store.steampowered.com/sale/Fallout"},
    ]}}

    deal, sale = plugin._collect_items(data, "spotlights")

    assert plugin._item_identity(deal) == "app:463530"
    assert plugin._app_id(deal) == "463530"
    assert plugin._item_identity(sale) == f"image:{sale_art}"
    assert plugin._app_id(sale) is None


def test_logo_matching_its_backdrop_gets_a_pure_ink_outline(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    scene = Image.new("RGB", (800, 480), (250, 230, 0))
    settings = show_logo_settings(logoPosition="center")
    yellow_logo = Image.new("RGBA", (400, 120), (0, 0, 0, 0))
    ImageDraw.Draw(yellow_logo).rectangle((20, 20, 380, 100), fill=(255, 235, 20, 255))
    blue_logo = Image.new("RGBA", (400, 120), (0, 0, 0, 0))
    ImageDraw.Draw(blue_logo).rectangle((20, 20, 380, 100), fill=(20, 20, 120, 255))

    outlined = plugin._overlay_logo(scene, yellow_logo, settings)
    shadowed = plugin._overlay_logo(scene, blue_logo, settings)

    assert (0, 0, 0) in _colors(outlined)
    assert (0, 0, 0) not in _colors(shadowed)


def test_square_logo_is_sized_by_area_not_squeezed_to_a_wordmark_height(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    scene = Image.new("RGB", (800, 480), LIT)

    composed = plugin._overlay_logo(scene, _red_logo((360, 360)), show_logo_settings(logoPosition="center"))

    red, green, _blue = composed.split()
    logo_pixels = ImageChops.multiply(
        red.point(lambda value: 255 if value > 200 else 0),
        green.point(lambda value: 255 if value < 60 else 0),
    )
    left, top, right, bottom = logo_pixels.getbbox()
    assert bottom - top > 120
    assert right - left > 120


@pytest.mark.parametrize(("art", "mat"), [((200, 30, 30), (0, 0, 0)), ((240, 240, 200), (255, 255, 255))])
def test_portrait_art_sits_on_a_solid_ink_mat(tmp_path, monkeypatch, art, mat):
    plugin = make_plugin(tmp_path, monkeypatch)

    canvas = plugin._contain_on_canvas(Image.new("RGB", (300, 500), art), (800, 480))

    assert canvas.getpixel((5, 5)) == mat
    assert canvas.getpixel((400, 240)) == art


def test_store_lookup_host_is_a_json_authority(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    api = SimpleNamespace(
        scheme="https",
        port=443,
        hostname="api.steampowered.com",
        normalized_url="https://api.steampowered.com/IStoreBrowseService/GetItems/v1/",
        addresses=("93.184.216.34",),
    )

    assert plugin._validate_steam_target(api, kind="json") == api.normalized_url
    with pytest.raises(RuntimeError, match="authority"):
        plugin._validate_steam_target(api, kind="media")


# Info shelf and the owner's Steam account ------------------------------------------

from plugins.steam_daily_art import shelf as info_shelf  # noqa: E402

FIELD = (0, 255, 39)


def _field_art(field_top=312, size=(800, 480)):
    """Sky with a castle-like block, then a flat field with a few sprites on it."""
    art = Image.new("RGB", size, (150, 205, 225))
    draw = ImageDraw.Draw(art)
    draw.rectangle((60, 80, 160, field_top), fill=(120, 90, 70))
    draw.rectangle((0, field_top, size[0], size[1]), fill=FIELD)
    for x in (140, 380, 650):
        draw.rectangle((x, field_top - 14, x + 18, field_top + 2), fill=(200, 40, 40))
    return art


def test_flat_band_is_found_at_the_foot_of_flat_art_only():
    band = info_shelf.find_flat_band(_field_art())

    assert band is not None
    top, colour = band
    assert 310 <= top <= 316
    assert colour == FIELD
    gradient = Image.linear_gradient("L").resize((800, 480)).convert("RGB")
    assert info_shelf.find_flat_band(gradient) is None


def test_pixel_grid_finds_upscaled_pixel_art_but_not_smooth_or_noisy_art():
    import random

    rng = random.Random(7)
    # Pixel art: flat sprites on a flat ground, drawn at 90x60 and enlarged four times.
    native = Image.new("RGB", (90, 60), (90, 160, 210))
    sprites = ImageDraw.Draw(native)
    for _ in range(60):
        x, y = rng.randrange(88), rng.randrange(58)
        colour = (rng.randrange(256), rng.randrange(256), rng.randrange(256))
        sprites.rectangle((x, y, x + rng.randrange(1, 5), y + rng.randrange(1, 4)), fill=colour)
    pixel = native.resize((360, 240), Image.Resampling.NEAREST).crop((1, 2, 359, 240))

    # Exact grids are recovered from lossless art; JPEG pixel art is left to is_hard_edged.
    grid = info_shelf.pixel_grid(pixel)

    assert grid == (4, 3, 2)
    assert info_shelf.to_native(pixel, grid).size == (88, 59)
    smooth = Image.linear_gradient("L").resize((360, 240)).convert("RGB")
    assert info_shelf.pixel_grid(smooth) is None
    noise = Image.new("RGB", (360, 240))
    noise.putdata([(rng.randrange(256), rng.randrange(256), rng.randrange(256)) for _ in range(360 * 240)])
    assert info_shelf.pixel_grid(noise) is None


def _pixel_scene(factor):
    """A small pixel-art scene (sky, ground, a castle, sprites) enlarged without smoothing."""
    native = Image.new("RGB", (100, 60), (120, 190, 225))
    draw = ImageDraw.Draw(native)
    draw.rectangle((0, 42, 99, 59), fill=FIELD)
    draw.rectangle((8, 14, 22, 42), fill=(150, 120, 90))
    for y in range(16, 40, 5):
        draw.rectangle((11, y, 13, y + 2), fill=(40, 30, 30))
        draw.rectangle((17, y, 19, y + 2), fill=(40, 30, 30))
    for x in range(30, 96, 7):
        draw.rectangle((x, 38, x + 2, 41), fill=(200, 40, 40))
        draw.rectangle((x, 36, x + 1, 37), fill=(250, 220, 170))
    size = (round(100 * factor), round(60 * factor))
    return native.resize(size, Image.Resampling.NEAREST)


def test_hard_edged_flat_art_is_told_from_gradients_and_painted_art():
    assert info_shelf.is_hard_edged(_pixel_scene(2.5))
    assert not info_shelf.is_hard_edged(Image.linear_gradient("L").resize((800, 480)).convert("RGB"))
    painted = Image.radial_gradient("L").resize((800, 480)).convert("RGB")
    ImageDraw.Draw(painted).ellipse((200, 100, 600, 400), outline=(250, 250, 250), width=3)
    assert not info_shelf.is_hard_edged(painted.resize((200, 120)).resize((800, 480), Image.Resampling.BICUBIC))


def test_hard_edged_art_is_enlarged_without_smoothing(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    art = _pixel_scene(2.5)

    assert info_shelf.pixel_grid(art) is None
    fitted = plugin._fit_artwork(art, (800, 480), steam_daily_art_module.ART_TITLED)

    # Nearest-neighbour keeps only the art's own colours; smoothing would add blends at edges.
    assert set(fitted.get_flattened_data()) <= set(art.get_flattened_data())


SHELF_INFO = {
    "reasons": [("wishlist", "愿望单特惠"), ("spotlight", "首页推荐")],
    "price": {"discount": 30, "final": "$4.89", "original": "$6.99"},
    "line": "俄罗斯方块加即时战略",
    "tags": ["策略", "休闲"],
    "credit": "Loop Forge / 今日发售",
}


def test_shelf_sits_on_the_flat_block_and_leaves_the_art_above_it():
    art = _field_art()
    band = info_shelf.find_flat_band(art)
    start = info_shelf.shelf_top(art.height, band)

    shelved = info_shelf.draw_shelf(art, SHELF_INFO, band)

    assert ImageChops.difference(art.crop((0, 0, 800, start)), shelved.crop((0, 0, 800, start))).getbbox() is None
    below = set(shelved.crop((0, start + 3, 800, 480)).get_flattened_data())
    # The deep ground covers the field at 80%: a fifth of the picture shows through.
    base = info_shelf.palette(FIELD)["base"]
    alpha = info_shelf.SHELF_OPACITY / 255
    ground = tuple(round(b * alpha + f * (1 - alpha)) for b, f in zip(base, FIELD))
    assert any(max(abs(a - b) for a, b in zip(pixel, ground)) <= 2 for pixel in below)
    assert (255, 255, 255) in below
    assert info_shelf.DEAL_GREEN in below
    assert FIELD not in below
    assert set(info_shelf.tag_colours(SHELF_INFO["tags"])) <= below


def test_each_tag_on_a_shelf_gets_its_own_colour_and_keeps_it():
    tags = ["多人", "合作", "在线合作", "本地合作", "休闲", "策略"]

    colours = info_shelf.tag_colours(tags)

    assert len(set(colours)) == len(tags)
    assert set(colours) <= set(info_shelf.TAG_COLOURS)
    assert info_shelf.tag_colours(["合作"])[0] == info_shelf.tag_colours(["合作", "多人"])[0]


def test_shelf_without_a_flat_block_fades_in_at_the_foot():
    art = Image.linear_gradient("L").resize((800, 480)).convert("RGB")

    shelved = info_shelf.draw_shelf(art, SHELF_INFO)

    start = info_shelf.shelf_top(480)
    assert ImageChops.difference(art.crop((0, 0, 800, start)), shelved.crop((0, 0, 800, start))).getbbox() is None
    assert shelved.getpixel((400, 470)) != art.getpixel((400, 470))


def test_card_keeps_titled_art_whole_above_the_shelf():
    art = Image.new("RGB", (800, 480), (200, 30, 30))
    ImageDraw.Draw(art).rectangle((300, 440, 500, 479), fill=(255, 255, 255))  # a title at the foot

    carded, band = info_shelf.card(art, 112)

    art_height = 480 - 112
    assert band[0] + info_shelf.GROUND_KEEP == art_height
    assert (255, 255, 255) in set(carded.crop((0, art_height - 40, 800, art_height)).get_flattened_data())
    assert carded.getpixel((5, 200)) == band[1]
    assert info_shelf.shelf_top(480, band) == art_height


def _personal_plugin(tmp_path, monkeypatch, wishlist, owned, deals):
    plugin = make_plugin(tmp_path, monkeypatch)
    monkeypatch.setattr(plugin, "_wishlist", lambda _settings: wishlist)
    monkeypatch.setattr(plugin, "_owned_appids", lambda _settings: owned)
    monkeypatch.setattr(plugin, "_wishlist_deals", lambda _settings, _wishlist: [dict(deal) for deal in deals])
    return plugin


def test_personalize_gives_wishlist_games_every_other_turn_and_owned_games_last(tmp_path, monkeypatch):
    deals = [{"id": 20, "type": 0, "name": "Deal", "discount_percent": 50, "_wishlist": True}]
    plugin = _personal_plugin(tmp_path, monkeypatch, {"10": 0, "20": 1}, {"30"}, deals)
    items = [{"id": 10, "type": 0, "discount_percent": 10}, {"id": 30, "type": 0}, {"id": 40, "type": 0},
             {"id": 50, "type": 0}, {"id": 60, "type": 2}]

    ordered = plugin._personalize(items, {})

    assert [item["id"] for item in ordered] == [20, 40, 10, 50, 60, 30]
    assert ordered[2]["_wishlist"] is True and ordered[-1]["_owned"] is True
    assert plugin._personalize(items, {"personalize": "off"}) == items


def test_wishlist_deal_already_on_the_front_page_is_not_added_twice(tmp_path, monkeypatch):
    deals = [{"id": 10, "type": 0, "name": "Deal", "discount_percent": 40, "_wishlist": True}]
    plugin = _personal_plugin(tmp_path, monkeypatch, {"10": 0}, set(), deals)

    ordered = plugin._personalize([{"id": 10, "type": 0}, {"id": 40, "type": 0}], {})

    assert [item["id"] for item in ordered] == [10, 40]


def _store_entry(appid, description, **extra):
    return {
        "appid": appid, "success": 1, "name": f"Game {appid}", "is_free": extra.get("free", False),
        "basic_info": {"short_description": description,
                       "developers": [{"name": "Loop Forge", "creator_clan_account_id": 46173202}],
                       "publishers": [{"name": "Loop Forge", "creator_clan_account_id": 46173202},
                                      *([{"name": extra["publisher"]}] if extra.get("publisher") else [])]},
        "tags": [{"tagid": 9}, {"tagid": 4182}],
        "reviews": {"summary_filtered": {"review_count": extra.get("reviews", 0), "percent_positive": 93,
                                         "review_score_label": "特别好评"}},
        "release": {"steam_release_date": 1792963736},
        "platforms": {"windows": True, "steam_deck_compat_category": extra.get("deck", 0)},
        "best_purchase_option": {"discount_pct": extra.get("discount", 30), "formatted_final_price": "$4.89",
                                 "formatted_original_price": "$6.99",
                                 "hide_discount_pct_for_compliance": extra.get("hide", False)},
    }


def test_store_details_are_fetched_in_one_batch_parsed_and_cached(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    calls = []

    def fetch(url, label):
        calls.append(url)
        return {"response": {"store_items": [_store_entry(1, "一款游戏。还有更多", reviews=1200, deck=3,
                                                          publisher="Daedalic Entertainment"),
                                             _store_entry(2, "An English blurb.", hide=True)]}}

    monkeypatch.setattr(plugin, "_fetch_profile_json", fetch)

    details = plugin._store_details(["1", "2", "1"], {})
    again = plugin._store_details(["2", "1"], {})

    assert len(calls) == 1 and "schinese" in calls[0]
    assert set(details) == {"1", "2"} and again == details
    assert details["1"]["review_label"] == "特别好评" and details["1"]["discount"] == 30
    assert details["2"]["review_label"] == "" and details["2"]["discount"] == 0
    assert details["1"]["tags"] == ["9", "4182"] and details["1"]["developer"] == "Loop Forge"
    assert details["1"]["developer_clan"] == "46173202"
    assert details["1"]["publisher"] == "Daedalic Entertainment" and details["2"]["publisher"] == ""
    assert details["1"]["deck"] == 3


def test_lines_translate_only_english_blurbs_and_remember_them(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    asked = []

    def translate(texts):
        asked.append(dict(texts))
        return {"2": "俄罗斯方块加即时战略"}

    monkeypatch.setattr(plugin, "_translate_blurbs", translate)
    details = {"1": {"description": "一款关于火车的合作游戏。和朋友一起修铁路"},
               "2": {"description": "Tetromancer is a Tetris battler. Clear lines to conjure an army."}}

    lines = plugin._blurbs(details)
    again = plugin._blurbs(details)

    assert lines == {"1": "一款关于火车的合作游戏", "2": "俄罗斯方块加即时战略"} == again
    assert asked == [{"2": details["2"]["description"]}]
    # A blurb Steam rewrites is translated again.
    plugin._blurbs({"2": {"description": "A new English blurb."}})
    assert len(asked) == 2


def test_shelf_info_gives_the_reasons_price_tags_and_credit(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    from datetime import timedelta, timezone

    now = datetime(2026, 10, 26, 3, 0, tzinfo=timezone.utc)
    facts = {"name": "Tetromancer", "description": "一款方块战争游戏", "developer": "Loop Forge", "tags": ["9", "4182"],
             "review_label": "特别好评", "review_percent": 93, "review_count": 1200,
             "release": int((now - timedelta(hours=3)).timestamp()), "coming_soon": False, "free": False,
             "discount": 30, "final": "$4.89", "original": "$6.99"}
    monkeypatch.setattr(plugin, "_store_details", lambda appids, _settings: {"4898560": facts})
    monkeypatch.setattr(plugin, "_tag_names", lambda: {"9": "策略", "4182": "单人"})
    monkeypatch.setattr(plugin, "_shelf_now", lambda: now)
    item = {"id": 4898560, "type": 0, "_wishlist": True, "_category_tags": ["new_releases", "spotlights", "top_sellers"]}

    info = plugin._shelf_info(item, {})

    assert info["reasons"] == [("wishlist", "愿望单特惠"), ("new", "今日发售"), ("top_seller", "热销")]
    assert info["price"] == {"discount": 30, "final": "$4.89", "original": "$6.99"}
    assert info["line"] == "一款方块战争游戏" and info["tags"] == ["策略", "单人"]
    assert info["credit"] == [("Loop Forge", 0), ("今日发售", 2), ("特别好评 93%", 1)]
    assert info["studio_logo"] is None
    assert plugin._shelf_info(item, {"infoShelf": "off"}) is None
    verified = {**facts, "deck": 3}
    monkeypatch.setattr(plugin, "_store_details", lambda appids, _settings: {"4898560": verified})
    assert plugin._shelf_info(item, {})["reasons"][-1] == ("steam_icon", "Steam Deck 已验证")
    assert plugin._shelf_info({"id": 5, "type": 2}, {}) is None


def test_shelf_is_left_out_without_store_facts(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)

    assert plugin._shelf_info({"id": 4898560, "type": 0}, {}) is None


def test_owned_games_lookup_never_logs_the_steam_key(tmp_path, monkeypatch, caplog):
    plugin = make_plugin(tmp_path, monkeypatch)
    plugin._active_device_config = SimpleNamespace(load_env_key=lambda name: "SECRETKEY123" if name == "STEAM_API_KEY" else "")

    def failing(url, label):
        raise RuntimeError(f"request failed for {url}")

    monkeypatch.setattr(plugin, "_fetch_profile_json", failing)
    with caplog.at_level(logging.WARNING):
        assert plugin._owned_appids({"steamId": "76561198000000000"}) == set()

    assert "SECRETKEY123" not in caplog.text
    assert "owned lookup failed" in caplog.text


def test_render_item_puts_the_shelf_on_the_arts_flat_block(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    art = _field_art()
    monkeypatch.setattr(plugin, "_download_first_available_logo", lambda _item: (None, None))
    monkeypatch.setattr(plugin, "_choose_artwork", lambda *_args: ("https://example.test/a.jpg", art, steam_daily_art_module.ART_TITLED))
    monkeypatch.setattr(plugin, "_shelf_info", lambda _item, _settings: SHELF_INFO)

    _url, _logo, image = plugin._render_item({"id": 1, "type": 0}, {}, (800, 480))

    start = info_shelf.shelf_top(480, info_shelf.find_flat_band(art))
    assert ImageChops.difference(art.crop((0, 0, 800, start)), image.crop((0, 0, 800, start))).getbbox() is None
    assert FIELD not in set(image.crop((0, start + 3, 800, 480)).get_flattened_data())


def test_shelf_text_uses_the_devices_yahei_from_its_data_folder(tmp_path, monkeypatch):
    # On the device YaHei lives only in $INKYPI_DATA_DIR/fonts; a Latin fallback drew boxes.
    fonts = tmp_path / "fonts"
    fonts.mkdir()
    bundled = Path(steam_daily_art_module.__file__).resolve().parents[2] / "static" / "fonts" / "NotoSansSC-VF.ttf"
    (fonts / "msyhbd.ttf").write_bytes(bundled.read_bytes())
    monkeypatch.setenv("INKYPI_DATA_DIR", str(tmp_path))
    info_shelf._font.cache_clear()
    try:
        assert Path(info_shelf._font(14, True).path).resolve() == (fonts / "msyhbd.ttf").resolve()
    finally:
        info_shelf._font.cache_clear()


def test_the_price_paid_is_the_largest_text_on_the_shelf(monkeypatch):
    sizes = []
    real = info_shelf._font

    def record(size, bold):
        sizes.append(size)
        return real(size, bold)

    monkeypatch.setattr(info_shelf, "_font", record)
    info_shelf.draw_shelf(_field_art(), SHELF_INFO, info_shelf.find_flat_band(_field_art()))

    assert max(sizes) == info_shelf.PRICE_SIZE
    assert info_shelf.PRICE_SIZE >= round(21 * 1.3)
    assert info_shelf.CUT_SIZE in sizes and info_shelf.ORIGINAL_SIZE in sizes


def test_a_flat_block_too_short_for_every_row_is_left_to_the_fading_band():
    short = _field_art(field_top=380)
    band = info_shelf.find_flat_band(short)

    assert band is not None
    assert info_shelf.band_fits(480, band, SHELF_INFO) is None
    tall = _field_art()
    assert info_shelf.band_fits(480, info_shelf.find_flat_band(tall), SHELF_INFO) is not None
    assert info_shelf.band_fits(480, None, SHELF_INFO) is None


def test_the_translation_asks_for_strict_json_lines_like_the_news_brief(tmp_path, monkeypatch):
    import openai

    plugin = make_plugin(tmp_path, monkeypatch)
    plugin._active_device_config = SimpleNamespace(load_env_key=lambda name: "sk-test" if name == "OPEN_AI_SECRET" else "")
    asked = {}

    class FakeResponses:
        def create(self, **kwargs):
            asked.update(kwargs)
            return SimpleNamespace(output_text=json.dumps(
                {"lines": [{"appid": "2", "line": "潜行之后是硬核战术枪战。"}, {"appid": "9", "line": "不在请求里"}]},
                ensure_ascii=False,
            ))

    class FakeOpenAI:
        def __init__(self, **kwargs):
            asked["client"] = kwargs
            self.responses = FakeResponses()

    monkeypatch.setattr(openai, "OpenAI", FakeOpenAI)

    lines = ORIGINAL_TRANSLATE_BLURBS(plugin, {"2": "Teeth-clenching stealth, followed by tactical gunplay."})

    assert lines == {"2": "潜行之后是硬核战术枪战"}
    assert asked["text"]["format"]["type"] == "json_schema" and asked["text"]["format"]["strict"] is True
    assert asked["reasoning"] == {"effort": "low"}
    assert asked["client"]["max_retries"] == 0
    assert json.loads(asked["input"]) == [{"appid": "2", "text": "Teeth-clenching stealth, followed by tactical gunplay."}]


def test_a_refused_translation_says_why_without_the_key(tmp_path, monkeypatch, caplog):
    import openai

    plugin = make_plugin(tmp_path, monkeypatch)
    plugin._active_device_config = SimpleNamespace(load_env_key=lambda name: "sk-secret-123" if name == "OPEN_AI_SECRET" else "")

    class Refusing:
        def __init__(self, **kwargs):
            self.responses = SimpleNamespace(create=self.create)

        @staticmethod
        def create(**kwargs):
            raise ValueError("Unsupported parameter: 'reasoning.effort'")

    monkeypatch.setattr(openai, "OpenAI", Refusing)
    with caplog.at_level(logging.WARNING):
        assert ORIGINAL_TRANSLATE_BLURBS(plugin, {"2": "An English blurb."}) == {}

    assert "Unsupported parameter" in caplog.text and "sk-secret-123" not in caplog.text


STUDIO_PAGE = (
    "<?xml version=\"1.0\"?><memberList><groupID64>103582791475694610</groupID64><groupDetails>"
    "<groupName><![CDATA[Loop Forge Studios]]></groupName>"
    "<avatarFull><![CDATA[https://avatars.fastly.steamstatic.com/ea188325c1208d56fa1d44bfe7fd934d81b1ec7a_full.jpg]]></avatarFull>"
    "</groupDetails></memberList>"
)


def test_studio_logo_is_the_clans_avatar_fetched_once_and_kept(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    pages, images = [], []

    def page(url, label):
        pages.append(url)
        return STUDIO_PAGE if "103582791475694610" in url else "<memberList><groupDetails></groupDetails></memberList>"

    def download(url):
        images.append(url)
        return Image.new("RGB", (184, 184), (200, 30, 30))

    monkeypatch.setattr(plugin, "_fetch_profile_text", page)
    monkeypatch.setattr(plugin, "_download_image", download)

    logo = plugin._studio_logo("46173202")
    again = plugin._studio_logo("46173202")
    none = plugin._studio_logo("2750327")
    still_none = plugin._studio_logo("2750327")

    assert logo.size == (48, 48) and logo.getpixel((24, 24)) == (200, 30, 30)
    assert again is not None
    assert none is None and still_none is None
    assert pages == [
        "https://steamcommunity.com/gid/103582791475694610/memberslistxml/?xml=1",
        "https://steamcommunity.com/gid/103582791432271735/memberslistxml/?xml=1",
    ]
    assert images == ["https://avatars.fastly.steamstatic.com/ea188325c1208d56fa1d44bfe7fd934d81b1ec7a_full.jpg"]
    assert plugin._studio_logo("") is None and plugin._studio_logo("0") is None


def test_credit_gives_way_part_by_part_before_it_is_cut():
    draw = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    font = info_shelf._font(13, False)
    parts = [("Indoor Astronaut", 0), ("Daedalic 发行", 3), ("2020 年发售", 2), ("特别好评 93%", 1)]
    full = " / ".join(text for text, _give in parts)

    assert info_shelf._fit_credit(draw, parts, font, 2000) == full
    narrower = draw.textlength("Indoor Astronaut / 2020 年发售 / 特别好评 93%", font=font) + 2
    assert info_shelf._fit_credit(draw, parts, font, narrower) == "Indoor Astronaut / 2020 年发售 / 特别好评 93%"
    assert info_shelf._fit_credit(draw, parts, font, 60).endswith("...")
    assert info_shelf._fit_credit(draw, "Loop Forge / 今日发售", font, 2000) == "Loop Forge / 今日发售"


def test_studio_logo_stands_before_the_developers_name():
    info = {**SHELF_INFO, "credit": [("Loop Forge", 0), ("今日发售", 2)], "studio_logo": Image.new("RGB", (48, 48), (200, 30, 30))}
    art = _field_art()

    shelved = info_shelf.draw_shelf(art, info, info_shelf.find_flat_band(art))

    foot = shelved.crop((400, 440, 800, 480))
    reds = [x for x in range(foot.width) for y in range(foot.height) if foot.getpixel((x, y)) == (200, 30, 30)]
    assert reds
