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
