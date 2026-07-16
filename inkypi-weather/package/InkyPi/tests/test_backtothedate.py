import json
import os
import struct
import sys
import uuid
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, PngImagePlugin

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.backtothedate.backtothedate import BacktotheDate
from plugins.backtothedate import backtothedate as backtothedate_module
from plugins.backtothedate import presentation_bank as presentation_bank_module
from plugins.backtothedate.presentation_bank import (
    MEDIA_BUDGET,
    MEDIA_MAX_OBJECT_BYTES,
)
from plugins.base_plugin.presentation import (
    PresentationMode,
    PresentationRequestContext,
    bind_presentation_instance_identity,
)
from plugins.base_plugin.render_provenance import SourceProvenance, read_source_provenance
from runtime.runtime_state import PresentationCommitReceipt


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


def make_plugin(name, base=None):
    plugin = BacktotheDate({"id": "backtothedate"})
    base = Path(base) if base is not None else TEST_STATE_ROOT / f"{name}-{uuid.uuid4().hex}"
    base.mkdir(parents=True, exist_ok=True)

    def plugin_dir(path=None):
        return str(base / path) if path else str(base)

    plugin.get_plugin_dir = plugin_dir
    return plugin


def _poster(index):
    return {
        "page_url": f"https://chineseposters.net/posters/bank-{index}",
        "image_url": f"https://chineseposters.net/sites/default/files/images/bank-{index}.jpg",
        "title": f"Bank poster {index}",
    }


def _hydrate_bank(
    plugin,
    monkeypatch,
    *,
    fit_mode="contain",
    image_size=(200, 400),
    offset=0,
    instance_uuid=None,
):
    posters = [_poster(index + offset) for index in range(30)]
    candidates = iter(posters)
    sizes = image_size if isinstance(image_size, list) else [image_size] * 30
    loader = FakeImageLoader(
        [Image.new("RGB", size, (index, 40, 80)) for index, size in enumerate(sizes)]
    )
    plugin.image_loader = loader
    monkeypatch.setattr(plugin, "_select_random_poster", lambda _settings: next(candidates))
    settings = bind_presentation_instance_identity(
        {
            "fitMode": fit_mode,
            "sourceMode": "all_archive",
            "maxPage": 0,
        },
        instance_uuid or f"test-instance-{Path(plugin.get_plugin_dir()).name}",
    )
    image = plugin.generate_image(settings, DeviceConfig())
    return settings, posters, loader, image


def _request(request_id, *, origin="origin-display"):
    return PresentationRequestContext(
        request_id=request_id,
        requested_at="2026-07-12T10:00:00+00:00",
        origin_display_commit_id=origin,
        last_receipt=None,
    )


def _preview_settings(settings):
    return {**settings, "_inkypiStatelessPreview": True}


def _receipt(request_id, *, display="prepared-display", committed_at="2026-07-12T10:01:00+00:00"):
    return PresentationCommitReceipt(
        request_id=request_id,
        committed_at=committed_at,
        display_commit_id=display,
        structural_generation=1,
        settings_revision=1,
        theme_mode=None,
    )


def _state_json(plugin):
    return json.loads(plugin._state_path().read_text(encoding="utf-8"))


def _active_profile(state):
    return state["profiles"][state["active_fingerprint"]]


def _pending(profile, request_id):
    pending = profile["pending_selection"]
    assert pending["request_id"] == request_id
    return pending


def _selection_posters(profile, selection):
    records = {record["media_key"]: record for record in profile["records"]}
    return [records[key] for key in selection["media_keys"]]


def _minimal_png(width, height):
    def chunk(kind, payload):
        checksum = zlib.crc32(kind + payload) & 0xFFFFFFFF
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", checksum)

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(b""))
        + chunk(b"IEND", b"")
    )


def test_triptych_selection_falls_back_to_one_portrait_when_bank_is_partial():
    plugin = make_plugin("partial-triptych-bank")
    settings = bind_presentation_instance_identity(
        {"fitMode": "triptych", "sourceMode": "all_archive", "maxPage": 0},
        "partial-triptych-instance",
    )
    bank = plugin._presentation_bank(settings, DeviceConfig().get_resolution())
    ready = [
        {
            "media_key": f"portrait-{index}",
            "page_url": f"https://example.com/poster-{index}",
            "image_url": f"https://example.com/poster-{index}.jpg",
            "width": 200,
            "height": 400,
        }
        for index in range(2)
    ]

    selection = bank.choose_selection({}, ready, "triptych", set(), set())

    assert selection["media_keys"] in (["portrait-0"], ["portrait-1"])


def test_data_hydrates_bank_without_consuming_discard_or_last_displayed_state(monkeypatch):
    plugin = make_plugin("data-bank")
    legacy = {
        "discarded_page_urls": ["https://chineseposters.net/posters/already-seen"],
        "discarded_image_urls": [
            "https://chineseposters.net/sites/default/files/images/already-seen.jpg"
        ],
        "last_displayed_at": "2026-07-11T08:00:00+00:00",
    }
    plugin._write_state(legacy)
    before_bytes = plugin._state_path().read_bytes()

    settings, _posters, loader, image = _hydrate_bank(
        plugin,
        monkeypatch,
        fit_mode="triptych",
    )

    after_bytes = plugin._state_path().read_bytes()
    state = _state_json(plugin)
    assert before_bytes != after_bytes
    assert state["discarded_page_urls"] == legacy["discarded_page_urls"]
    assert state["discarded_image_urls"] == legacy["discarded_image_urls"]
    assert state["last_displayed_at"] == legacy["last_displayed_at"]
    profile = _active_profile(state)
    assert len(profile["records"]) == 24
    assert len(profile["current_selection"]["media_keys"]) == 3
    assert profile["pending_selection"] is None
    assert len(loader.calls) == 24
    assert image.size == DeviceConfig().get_resolution()
    assert plugin.presentation_mode(settings) is PresentationMode.PREPARED_BANK


@pytest.mark.parametrize("force_key", ["forceRefresh", "force_refresh"])
def test_force_refresh_attempts_provider_for_full_bank_without_consuming_selection(
    monkeypatch,
    force_key,
):
    plugin = make_plugin(f"forced-full-bank-{force_key}")
    settings, _posters, _loader, _image = _hydrate_bank(plugin, monkeypatch)
    before = _state_json(plugin)
    before_profile = _active_profile(before)
    current = dict(before_profile["current_selection"])
    calls = []
    forced_poster = _poster(99)
    monkeypatch.setattr(
        plugin,
        "_select_random_poster",
        lambda _settings: calls.append("provider") or forced_poster,
    )
    plugin.image_loader = FakeImageLoader(Image.new("RGB", (200, 400), "purple"))

    image = plugin.generate_image({**settings, force_key: "true"}, DeviceConfig())

    after = _state_json(plugin)
    profile = _active_profile(after)
    assert calls == ["provider"]
    assert profile["last_provider_status"] == "success"
    assert datetime.fromisoformat(profile["last_provider_attempt_at"]).tzinfo is not None
    assert profile["current_selection"] == current
    assert profile["pending_selection"] is None
    assert any(record["image_url"] == forced_poster["image_url"] for record in profile["records"])
    assert read_source_provenance(image) is SourceProvenance.FRESH_CACHE


def test_force_refresh_provider_error_marks_warm_bank_stale_and_skips_cache(monkeypatch):
    plugin = make_plugin("forced-provider-error")
    monkeypatch.setattr(presentation_bank_module.random, "shuffle", lambda _items: None)
    settings, _posters, _loader, _image = _hydrate_bank(plugin, monkeypatch)
    monkeypatch.setattr(
        plugin,
        "_select_random_poster",
        lambda _settings: (_ for _ in ()).throw(RuntimeError("provider offline")),
    )

    image = plugin.generate_image(
        {**settings, "forceRefresh": "true"},
        DeviceConfig(),
    )

    profile = _active_profile(_state_json(plugin))
    assert profile["last_provider_status"] == "error"
    assert read_source_provenance(image) is SourceProvenance.STALE_CACHE
    assert image.info["inkypi_skip_cache"] is True


def test_warm_bank_presentation_has_zero_http_and_does_not_mark_seen_before_receipt(monkeypatch):
    plugin = make_plugin("warm-presentation")
    settings, _posters, _loader, _image = _hydrate_bank(plugin, monkeypatch)
    request = _request("a" * 32)
    before_state = _state_json(plugin)
    before_profile = _active_profile(before_state)
    current = _selection_posters(before_profile, before_profile["current_selection"])

    monkeypatch.setattr(
        plugin,
        "_fetch_text",
        lambda *_args, **_kwargs: pytest.fail("warm presentation must not fetch HTML"),
    )
    monkeypatch.setattr(
        plugin.image_loader,
        "from_url",
        lambda *_args, **_kwargs: pytest.fail("warm presentation must not fetch media"),
    )
    before_bytes = plugin._state_path().read_bytes()

    preparation = plugin.prepare_presentation(
        settings,
        DeviceConfig(),
        request=request,
        resolved_theme_context=None,
    )

    after_bytes = plugin._state_path().read_bytes()
    state = _state_json(plugin)
    profile = _active_profile(state)
    pending = _selection_posters(profile, _pending(profile, request.request_id))
    discarded_pages = set(state.get("discarded_page_urls", []))
    discarded_images = set(state.get("discarded_image_urls", []))
    assert before_bytes != after_bytes
    assert preparation.changed is True
    assert preparation.request_id == request.request_id
    assert preparation.image.size == DeviceConfig().get_resolution()
    assert all(poster["page_url"] not in discarded_pages for poster in pending)
    assert all(poster["image_url"] not in discarded_images for poster in pending)
    assert all(poster["page_url"] in discarded_pages for poster in current)
    assert profile["last_applied_origin_commit_id"] == request.origin_display_commit_id
    assert profile["last_applied_request_id"] is None


def test_matching_display_receipt_marks_exact_posters_once(monkeypatch):
    plugin = make_plugin("matching-receipt")
    settings, _posters, _loader, _image = _hydrate_bank(plugin, monkeypatch)
    request = _request("b" * 32)
    plugin.prepare_presentation(
        settings,
        DeviceConfig(),
        request=request,
        resolved_theme_context=None,
    )
    before = _state_json(plugin)
    before_profile = _active_profile(before)
    pending = _selection_posters(
        before_profile,
        _pending(before_profile, request.request_id),
    )
    receipt = _receipt(request.request_id)

    plugin.reconcile_presentation_receipt(settings, receipt)

    after = _state_json(plugin)
    after_profile = _active_profile(after)
    assert after["last_page_urls"] == [poster["page_url"] for poster in pending]
    assert after["last_image_urls"] == [poster["image_url"] for poster in pending]
    assert after["last_displayed_at"] == receipt.committed_at
    assert after_profile["last_applied_request_id"] == request.request_id
    assert after_profile["pending_selection"] is None
    committed_bytes = plugin._state_path().read_bytes()
    plugin.reconcile_presentation_receipt(settings, receipt)
    assert plugin._state_path().read_bytes() == committed_bytes


def test_canceled_or_foreign_receipt_never_marks_posters_seen(monkeypatch):
    plugin = make_plugin("foreign-receipt")
    settings, _posters, _loader, _image = _hydrate_bank(plugin, monkeypatch)
    foreign = _receipt("f" * 32, display="foreign-display")
    before_untrusted_origin = plugin._state_path().read_bytes()
    plugin.reconcile_presentation_receipt(settings, foreign)
    assert plugin._state_path().read_bytes() == before_untrusted_origin

    canceled = _request("c" * 32, origin="origin-canceled")
    plugin.prepare_presentation(
        settings,
        DeviceConfig(),
        request=canceled,
        resolved_theme_context=None,
    )
    before_foreign = plugin._state_path().read_bytes()

    plugin.reconcile_presentation_receipt(settings, foreign)

    assert plugin._state_path().read_bytes() == before_foreign

    replacement = _request("d" * 32, origin="origin-replacement")
    plugin.prepare_presentation(
        settings,
        DeviceConfig(),
        request=replacement,
        resolved_theme_context=None,
    )
    replacement_profile = _active_profile(_state_json(plugin))
    assert replacement_profile["pending_selection"]["request_id"] == replacement.request_id
    before_canceled_receipt = plugin._state_path().read_bytes()

    plugin.reconcile_presentation_receipt(
        settings,
        _receipt(canceled.request_id, display="late-canceled-display"),
    )

    assert plugin._state_path().read_bytes() == before_canceled_receipt


def test_triptych_receipt_commits_all_three_only_after_display(monkeypatch):
    plugin = make_plugin("triptych-receipt")
    settings, _posters, _loader, _image = _hydrate_bank(
        plugin,
        monkeypatch,
        fit_mode="triptych",
    )
    request = _request("e" * 32)

    preparation = plugin.prepare_presentation(
        settings,
        DeviceConfig(),
        request=request,
        resolved_theme_context=None,
    )

    prepared = _state_json(plugin)
    prepared_profile = _active_profile(prepared)
    pending = _selection_posters(
        prepared_profile,
        _pending(prepared_profile, request.request_id),
    )
    assert preparation.changed is True
    assert len(pending) == 3
    assert all(
        poster["page_url"] not in prepared.get("discarded_page_urls", [])
        for poster in pending
    )

    plugin.reconcile_presentation_receipt(settings, _receipt(request.request_id))

    committed = _state_json(plugin)
    assert committed["last_page_urls"] == [poster["page_url"] for poster in pending]
    assert committed["last_image_urls"] == [poster["image_url"] for poster in pending]


def test_restart_reuses_pending_selection_without_choosing_another(monkeypatch):
    plugin = make_plugin("restart-pending")
    settings, _posters, _loader, _image = _hydrate_bank(plugin, monkeypatch)
    request = _request("1" * 32)
    first = plugin.prepare_presentation(
        settings,
        DeviceConfig(),
        request=request,
        resolved_theme_context=None,
    )
    pending_before = _pending(
        _active_profile(_state_json(plugin)),
        request.request_id,
    )
    state_bytes_before = plugin._state_path().read_bytes()

    restarted = make_plugin("restart-pending", base=plugin.get_plugin_dir())
    restarted.image_loader = FakeImageLoader(Image.new("RGB", (1, 1), "red"))
    monkeypatch.setattr(
        restarted,
        "_fetch_text",
        lambda *_args, **_kwargs: pytest.fail("restart preparation must not fetch HTML"),
    )
    monkeypatch.setattr(
        restarted.image_loader,
        "from_url",
        lambda *_args, **_kwargs: pytest.fail("restart preparation must not fetch media"),
    )

    second = restarted.prepare_presentation(
        settings,
        DeviceConfig(),
        request=request,
        resolved_theme_context=None,
    )

    assert _pending(
        _active_profile(_state_json(restarted)),
        request.request_id,
    ) == pending_before
    assert restarted._state_path().read_bytes() == state_bytes_before
    assert first.image.tobytes() == second.image.tobytes()


def test_triptych_landscape_receipt_commits_only_single_rendered_poster(monkeypatch):
    plugin = make_plugin("triptych-landscape-receipt")
    settings, _posters, _loader, _image = _hydrate_bank(
        plugin,
        monkeypatch,
        fit_mode="triptych",
        image_size=(500, 260),
    )
    request = _request("2" * 32)

    plugin.prepare_presentation(
        settings,
        DeviceConfig(),
        request=request,
        resolved_theme_context=None,
    )
    prepared = _state_json(plugin)
    profile = _active_profile(prepared)
    pending = _selection_posters(profile, _pending(profile, request.request_id))
    assert len(pending) == 1

    plugin.reconcile_presentation_receipt(settings, _receipt(request.request_id))

    committed = _state_json(plugin)
    assert committed["last_page_urls"] == [pending[0]["page_url"]]
    assert committed["last_image_urls"] == [pending[0]["image_url"]]


def test_missing_pending_media_fails_closed_without_provider_calls(monkeypatch):
    plugin = make_plugin("missing-pending-media")
    settings, _posters, _loader, _image = _hydrate_bank(plugin, monkeypatch)
    request = _request("3" * 32)
    plugin.prepare_presentation(
        settings,
        DeviceConfig(),
        request=request,
        resolved_theme_context=None,
    )
    state = _state_json(plugin)
    profile = _active_profile(state)
    media_key = _pending(profile, request.request_id)["media_keys"][0]
    media_files = list(Path(plugin._presentation_media_dir()).glob(f"{media_key}.*"))
    assert len(media_files) == 1
    media_files[0].unlink()

    monkeypatch.setattr(
        plugin,
        "_fetch_text",
        lambda *_args, **_kwargs: pytest.fail("cold presentation must not fetch HTML"),
    )
    monkeypatch.setattr(
        "plugins.backtothedate.backtothedate.get_http_session",
        lambda: pytest.fail("cold presentation must not open an HTTP session"),
    )
    monkeypatch.setattr(
        plugin.image_loader,
        "from_url",
        lambda *_args, **_kwargs: pytest.fail("cold presentation must not fetch media"),
    )

    with pytest.raises(RuntimeError, match="media"):
        plugin.prepare_presentation(
            settings,
            DeviceConfig(),
            request=request,
            resolved_theme_context=None,
        )


def test_data_rehydrates_exact_missing_current_without_rotating_selection(monkeypatch):
    plugin = make_plugin("data-rehydrate-current")
    settings, _posters, _loader, _image = _hydrate_bank(plugin, monkeypatch)
    before = _state_json(plugin)
    profile = _active_profile(before)
    current_before = dict(profile["current_selection"])
    current_posters = _selection_posters(profile, current_before)
    bank = plugin._presentation_bank(settings, DeviceConfig().get_resolution())
    for poster in current_posters:
        bank.media.path(poster["media_key"], suffix=".png").unlink()
    loader = FakeImageLoader(Image.new("RGB", (200, 400), "blue"))
    plugin.image_loader = loader
    monkeypatch.setattr(
        plugin,
        "_select_random_poster",
        lambda _settings: pytest.fail("missing current must not select another poster"),
    )

    plugin.generate_image(settings, DeviceConfig())

    after = _state_json(plugin)
    after_profile = after["profiles"][before["active_fingerprint"]]
    assert after_profile["current_selection"] == current_before
    assert after_profile["pending_selection"] is None
    assert [call["url"] for call in loader.calls] == [
        poster["image_url"] for poster in current_posters
    ]
    assert after.get("discarded_page_urls", []) == before.get("discarded_page_urls", [])
    assert after.get("last_displayed_at") == before.get("last_displayed_at")


def test_data_pending_media_failure_preserves_receipt_metadata(monkeypatch):
    plugin = make_plugin("data-preserve-pending")
    settings, _posters, _loader, _image = _hydrate_bank(plugin, monkeypatch)
    request = _request("31" * 16)
    plugin.prepare_presentation(
        settings,
        DeviceConfig(),
        request=request,
        resolved_theme_context=None,
    )
    before = _state_json(plugin)
    profile = _active_profile(before)
    pending_before = dict(_pending(profile, request.request_id))
    pending_posters = _selection_posters(profile, pending_before)
    bank = plugin._presentation_bank(settings, DeviceConfig().get_resolution())
    for poster in pending_posters:
        bank.media.path(poster["media_key"], suffix=".png").unlink()
    provider_calls = []

    def missing_media(url, *_args, **_kwargs):
        provider_calls.append(url)
        return None

    monkeypatch.setattr(plugin.image_loader, "from_url", missing_media)
    state_bytes_before = plugin._state_path().read_bytes()

    with pytest.raises(RuntimeError, match="current|pending|protected|media"):
        plugin.generate_image(settings, DeviceConfig())

    assert provider_calls == [poster["image_url"] for poster in pending_posters]
    assert plugin._state_path().read_bytes() == state_bytes_before

    plugin.reconcile_presentation_receipt(settings, _receipt(request.request_id))

    committed = _state_json(plugin)
    committed_profile = committed["profiles"][before["active_fingerprint"]]
    assert committed_profile["pending_selection"] is None
    assert committed["last_page_urls"] == [
        poster["page_url"] for poster in pending_posters
    ]


@pytest.mark.parametrize(
    ("mode", "background", "accent"),
    [
        ("day", (245, 238, 220), (160, 82, 45)),
        ("night", (9, 10, 11), (210, 120, 45)),
    ],
)
def test_prepared_presentation_applies_resolved_media_theme_chrome(
    monkeypatch,
    mode,
    background,
    accent,
):
    plugin = make_plugin("prepared-theme")
    settings, _posters, _loader, _image = _hydrate_bank(plugin, monkeypatch)
    theme = {
        "mode": mode,
        "requested_mode": "auto",
        "palette": {
            "background": background,
            "accent": accent,
        },
    }

    preparation = plugin.prepare_presentation(
        settings,
        DeviceConfig(),
        request=_request("4" * 32),
        resolved_theme_context=theme,
    )

    assert preparation.image.getpixel((6, 6)) == theme["palette"]["accent"]
    assert preparation.image.getpixel((0, 0)) == theme["palette"]["background"]
    assert preparation.image.info["inkypi_theme_mode"] == mode


def test_same_settings_instances_keep_current_origin_and_pending_isolated(monkeypatch):
    plugin = make_plugin("same-settings-instance-isolation")
    settings_a, _posters, _loader, _image = _hydrate_bank(
        plugin,
        monkeypatch,
        offset=0,
        instance_uuid="playlist-instance-a",
    )
    state_a = _state_json(plugin)
    fingerprint_a = state_a["active_fingerprint"]
    profile_a = state_a["profiles"][fingerprint_a]
    actual_a_origin = _selection_posters(profile_a, profile_a["current_selection"])

    settings_b, _posters, loader_b, _image = _hydrate_bank(
        plugin,
        monkeypatch,
        offset=100,
        instance_uuid="playlist-instance-b",
    )
    state_b = _state_json(plugin)
    fingerprint_b = state_b["active_fingerprint"]
    assert fingerprint_b != fingerprint_a
    assert loader_b.calls == []
    request_b = _request("6" * 32, origin="origin-b")
    plugin.prepare_presentation(
        settings_b,
        DeviceConfig(),
        request=request_b,
        resolved_theme_context=None,
    )
    staged_b = _state_json(plugin)
    profile_b = staged_b["profiles"][fingerprint_b]
    pending_b = _selection_posters(profile_b, _pending(profile_b, request_b.request_id))
    plugin.reconcile_presentation_receipt(
        settings_b,
        _receipt(
            request_b.request_id,
            display="prepared-display-b",
            committed_at="2026-07-12T10:02:00+00:00",
        ),
    )

    request_a = _request("5" * 32, origin="origin-a")
    plugin.prepare_presentation(
        settings_a,
        DeviceConfig(),
        request=request_a,
        resolved_theme_context=None,
    )
    isolated = _state_json(plugin)
    profile_a = isolated["profiles"][fingerprint_a]
    profile_b = isolated["profiles"][fingerprint_b]
    assert all(
        poster["page_url"] in isolated["discarded_page_urls"]
        for poster in actual_a_origin
    )
    assert profile_a["last_applied_origin_commit_id"] == request_a.origin_display_commit_id
    assert profile_b["last_applied_request_id"] == request_b.request_id
    assert _pending(profile_a, request_a.request_id)["request_id"] == request_a.request_id
    assert profile_b["pending_selection"] is None
    assert isolated["last_page_urls"] == [poster["page_url"] for poster in pending_b]

    plugin.generate_image(settings_a, DeviceConfig())
    assert _state_json(plugin)["active_fingerprint"] == fingerprint_a
    plugin.generate_image(settings_b, DeviceConfig())
    assert _state_json(plugin)["active_fingerprint"] == fingerprint_b


def test_playlist_bank_paths_reject_missing_or_json_spoofed_identity():
    plugin = make_plugin("missing-trusted-identity")
    spoofed = {
        "_inkypi_presentation_instance_identity": {
            "instance_uuid": "json-controlled-instance"
        }
    }

    with pytest.raises(RuntimeError, match="trusted instance identity"):
        plugin.generate_image(spoofed, DeviceConfig())
    with pytest.raises(RuntimeError, match="trusted instance identity"):
        plugin.prepare_presentation(
            spoofed,
            DeviceConfig(),
            request=_request("51" * 16),
            resolved_theme_context=None,
        )
    with pytest.raises(RuntimeError, match="trusted instance identity"):
        plugin.reconcile_presentation_receipt(
            spoofed,
            _receipt("51" * 16),
        )
    assert not plugin._state_path().exists()


def test_oversize_managed_media_is_rejected_before_namespace_read(monkeypatch):
    plugin = make_plugin("oversize-managed-media")
    settings, _posters, _loader, _image = _hydrate_bank(plugin, monkeypatch)
    request = _request("7" * 32)
    plugin.prepare_presentation(
        settings,
        DeviceConfig(),
        request=request,
        resolved_theme_context=None,
    )
    state = _state_json(plugin)
    profile = _active_profile(state)
    media_key = _pending(profile, request.request_id)["media_keys"][0]
    bank = plugin._presentation_bank(settings, DeviceConfig().get_resolution())
    target = bank.media.path(media_key, suffix=".png")
    target.write_bytes(b"x" * (MEDIA_MAX_OBJECT_BYTES + 1))
    original_get_bytes = bank.media.get_bytes

    def reject_oversize_read(key, **kwargs):
        if key == media_key:
            pytest.fail("oversize media must be rejected before read")
        return original_get_bytes(key, **kwargs)

    monkeypatch.setattr(
        bank.media,
        "get_bytes",
        reject_oversize_read,
    )

    with pytest.raises(RuntimeError, match="budget|large|regular"):
        plugin.prepare_presentation(
            settings,
            DeviceConfig(),
            request=request,
            resolved_theme_context=None,
        )


def test_symlink_managed_media_is_rejected_before_namespace_read(monkeypatch):
    plugin = make_plugin("symlink-managed-media")
    settings, _posters, _loader, _image = _hydrate_bank(plugin, monkeypatch)
    request = _request("8" * 32)
    plugin.prepare_presentation(
        settings,
        DeviceConfig(),
        request=request,
        resolved_theme_context=None,
    )
    state = _state_json(plugin)
    profile = _active_profile(state)
    media_key = _pending(profile, request.request_id)["media_keys"][0]
    bank = plugin._presentation_bank(settings, DeviceConfig().get_resolution())
    target = bank.media.path(media_key, suffix=".png")
    replacement = target.parent / "symlink-source.png"
    replacement.write_bytes(target.read_bytes())
    target.unlink()
    try:
        os.symlink(replacement, target)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")
    original_get_bytes = bank.media.get_bytes

    def reject_symlink_read(key, **kwargs):
        if key == media_key:
            pytest.fail("symlink media must be rejected before read")
        return original_get_bytes(key, **kwargs)

    monkeypatch.setattr(
        bank.media,
        "get_bytes",
        reject_symlink_read,
    )

    with pytest.raises(RuntimeError, match="regular|missing"):
        plugin.prepare_presentation(
            settings,
            DeviceConfig(),
            request=request,
            resolved_theme_context=None,
        )


def test_compressed_oversize_dimensions_are_rejected_before_pixel_decode(monkeypatch):
    plugin = make_plugin("compressed-oversize-media")
    settings, _posters, _loader, _image = _hydrate_bank(plugin, monkeypatch)
    request = _request("81" * 16)
    plugin.prepare_presentation(
        settings,
        DeviceConfig(),
        request=request,
        resolved_theme_context=None,
    )
    state = _state_json(plugin)
    profile = _active_profile(state)
    media_key = _pending(profile, request.request_id)["media_keys"][0]
    bank = plugin._presentation_bank(settings, DeviceConfig().get_resolution())
    target = bank.media.path(media_key, suffix=".png")
    target.write_bytes(_minimal_png(6000, 6000))
    original_load = PngImagePlugin.PngImageFile.load

    def reject_oversize_decode(image, *args, **kwargs):
        if image.size == (6000, 6000):
            pytest.fail("oversize compressed pixels must be rejected before load")
        return original_load(image, *args, **kwargs)

    monkeypatch.setattr(PngImagePlugin.PngImageFile, "load", reject_oversize_decode)

    with pytest.raises(RuntimeError, match="dimensions"):
        plugin.prepare_presentation(
            settings,
            DeviceConfig(),
            request=request,
            resolved_theme_context=None,
        )


@pytest.mark.parametrize(
    ("leading_sizes", "expected_indexes"),
    [
        ([(200, 400), (500, 260), (210, 400), (220, 400)], [1]),
        ([(200, 400), (210, 400), (220, 400), (500, 260)], [0, 1, 2]),
    ],
)
def test_mixed_triptych_selection_preserves_legacy_scan_order(
    monkeypatch,
    leading_sizes,
    expected_indexes,
):
    plugin = make_plugin("mixed-triptych")
    sizes = leading_sizes + [(230, 400)] * 26
    settings, _posters, _loader, _image = _hydrate_bank(
        plugin,
        monkeypatch,
        fit_mode="triptych",
        image_size=sizes,
    )
    state = _state_json(plugin)
    profile = _active_profile(state)
    profile["current_selection"] = {
        "media_keys": [profile["records"][-1]["media_key"]],
        "request_id": None,
    }
    plugin._write_state(state)
    monkeypatch.setattr(
        "plugins.backtothedate.presentation_bank.random.shuffle",
        lambda _items: None,
    )
    request = _request("9" * 32)

    plugin.prepare_presentation(
        settings,
        DeviceConfig(),
        request=request,
        resolved_theme_context=None,
    )

    prepared = _state_json(plugin)
    prepared_profile = _active_profile(prepared)
    pending = _pending(prepared_profile, request.request_id)
    expected = [profile["records"][index]["media_key"] for index in expected_indexes]
    assert pending["media_keys"] == expected


@pytest.mark.parametrize("setting_name", ["previewImageUrl", "posterImageUrl"])
def test_legacy_forced_image_only_setting_remains_supported(monkeypatch, setting_name):
    plugin = make_plugin(f"legacy-forced-{setting_name}")
    source_url = "https://chineseposters.net/sites/default/files/images/legacy-preview.jpg"
    plugin.image_loader = FakeImageLoader(Image.new("RGB", (500, 260), (20, 120, 220)))

    image = plugin.generate_image(
        _preview_settings({"fitMode": "landscape", setting_name: source_url}),
        DeviceConfig(),
    )

    assert image.size == DeviceConfig().get_resolution()
    assert plugin.image_loader.calls[0]["url"] == source_url
    assert not plugin._state_path().exists()


@pytest.mark.parametrize("invalid_max_page", [None, "", "not-a-number", 10001])
def test_stateless_preview_normalizes_invalid_max_page_without_state_write(
    monkeypatch,
    invalid_max_page,
):
    plugin = make_plugin("stateless-invalid-max-page")
    plugin.image_loader = FakeImageLoader(Image.new("RGB", (200, 400), "red"))

    def fake_fetch(url, params=None):
        if url.endswith("/posters/posters"):
            return '<a href="?page=2">3</a><a href="/posters/preview">Preview</a>'
        return '<h1>Preview</h1><img src="/sites/default/files/images/preview.jpg">'

    monkeypatch.setattr(plugin, "_fetch_text", fake_fetch)
    monkeypatch.setattr(
        "plugins.backtothedate.backtothedate.random.randint",
        lambda _low, _high: 0,
    )
    monkeypatch.setattr(
        "plugins.backtothedate.backtothedate.random.shuffle",
        lambda _items: None,
    )

    image = plugin.generate_image(
        _preview_settings(
            {
                "fitMode": "contain",
                "sourceMode": "all_archive",
                "maxPage": invalid_max_page,
            }
        ),
        DeviceConfig(),
    )

    assert image.size == DeviceConfig().get_resolution()
    assert not plugin._state_path().exists()


def test_profile_bank_evicts_oldest_inactive_profile_at_capacity():
    plugin = make_plugin("profile-cap-inactive")
    first_fingerprint = None

    for index in range(65):
        settings = bind_presentation_instance_identity(
            {
                "fitMode": "contain",
                "sourceMode": "all_archive",
                "maxPage": 0,
                "backgroundColor": f"color-{index}",
            },
            "one-changing-instance",
        )
        bank = plugin._presentation_bank(settings, DeviceConfig().get_resolution())
        document, _profile = bank.load_for_data()
        if first_fingerprint is None:
            first_fingerprint = bank.fingerprint
        bank.save(document)

    state = plugin._read_state()
    assert len(state["profiles"]) == 64
    assert first_fingerprint not in state["profiles"]
    assert state["instance_profiles"]["one-changing-instance"] == bank.fingerprint


def test_profile_bank_fails_closed_when_capacity_is_all_active():
    plugin = make_plugin("profile-cap-active")

    for index in range(64):
        settings = bind_presentation_instance_identity(
            {
                "fitMode": "contain",
                "sourceMode": "all_archive",
                "maxPage": index,
            },
            f"active-instance-{index}",
        )
        bank = plugin._presentation_bank(settings, DeviceConfig().get_resolution())
        document, _profile = bank.load_for_data()
        bank.save(document)

    overflow_settings = bind_presentation_instance_identity(
        {
            "fitMode": "contain",
            "sourceMode": "all_archive",
            "maxPage": 65,
        },
        "active-instance-overflow",
    )
    overflow_bank = plugin._presentation_bank(
        overflow_settings,
        DeviceConfig().get_resolution(),
    )
    with pytest.raises(RuntimeError, match="profile capacity"):
        overflow_bank.load_for_data()

    state = plugin._read_state()
    assert len(state["profiles"]) == 64
    assert "active-instance-overflow" not in state["instance_profiles"]


def test_discarded_history_keeps_only_newest_4096_unique_urls():
    plugin = make_plugin("history-cap")
    page_urls = [f"https://chineseposters.net/posters/history-{index}" for index in range(4096)]
    image_urls = [
        f"https://chineseposters.net/sites/default/files/images/history-{index}.jpg"
        for index in range(4096)
    ]
    plugin._write_state(
        {
            "discarded_page_urls": page_urls,
            "discarded_image_urls": image_urls,
        }
    )

    plugin._remember_success(_poster(9000))

    state = plugin._read_state()
    assert len(state["discarded_page_urls"]) == 4096
    assert len(state["discarded_image_urls"]) == 4096
    assert page_urls[0] not in state["discarded_page_urls"]
    assert image_urls[0] not in state["discarded_image_urls"]
    assert _poster(9000)["page_url"] in state["discarded_page_urls"]
    assert _poster(9000)["image_url"] in state["discarded_image_urls"]


@pytest.mark.parametrize("source", ["durable", "legacy"])
def test_state_reader_rejects_oversized_state_before_json_decode(source):
    plugin = make_plugin(f"state-read-cap-{source}")
    path = plugin._state_path() if source == "durable" else plugin._legacy_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'{"padding":"' + (b"x" * (4 * 1024 * 1024)) + b'"}')

    with pytest.raises(RuntimeError, match="size limit"):
        plugin._read_state()


def test_plugin_state_writer_rejects_oversized_payload_before_atomic_write(monkeypatch):
    plugin = make_plugin("plugin-state-write-cap")
    monkeypatch.setattr(
        backtothedate_module,
        "atomic_write_json",
        lambda *_args, **_kwargs: pytest.fail("atomic writer must not receive oversized state"),
    )

    with pytest.raises(RuntimeError, match="size limit"):
        plugin._write_state({"padding": "x" * (4 * 1024 * 1024)})


def test_bank_state_writer_rejects_oversized_payload_before_atomic_write(monkeypatch):
    plugin = make_plugin("bank-state-write-cap")
    settings = bind_presentation_instance_identity(
        {"fitMode": "contain", "sourceMode": "all_archive", "maxPage": 0},
        "bank-state-write-cap-instance",
    )
    bank = plugin._presentation_bank(settings, DeviceConfig().get_resolution())
    monkeypatch.setattr(
        presentation_bank_module,
        "atomic_write_json",
        lambda *_args, **_kwargs: pytest.fail("atomic writer must not receive oversized state"),
    )

    with pytest.raises(RuntimeError, match="size limit"):
        bank.save({"padding": "x" * (4 * 1024 * 1024)})


def test_prepare_reads_discard_history_once(monkeypatch):
    plugin = make_plugin("discard-history-single-read")
    settings, _posters, _loader, _image = _hydrate_bank(plugin, monkeypatch)
    original = presentation_bank_module.PosterPresentationBank._read_document
    calls = []

    def counted_read(bank):
        calls.append(bank.state_path)
        return original(bank)

    monkeypatch.setattr(
        presentation_bank_module.PosterPresentationBank,
        "_read_document",
        counted_read,
    )

    plugin.prepare_presentation(
        settings,
        DeviceConfig(),
        request=_request("f" * 32, origin="single-history-read-origin"),
        resolved_theme_context=None,
    )

    assert calls == [plugin._state_path()]


def test_forced_provider_http_urls_are_canonicalized_before_image_load():
    plugin = make_plugin("forced-provider-https")
    loader = FakeImageLoader(Image.new("RGB", (200, 400), "red"))
    plugin.image_loader = loader

    plugin.generate_image(
        _preview_settings(
            {
                "fitMode": "contain",
                "posterImageUrl": (
                    "http://chineseposters.net/sites/default/files/images/provider-http.jpg"
                ),
                "posterPageUrl": "http://chineseposters.net/posters/provider-http",
            }
        ),
        DeviceConfig(),
    )

    assert loader.calls[0]["url"] == (
        "https://chineseposters.net/sites/default/files/images/provider-http.jpg"
    )


@pytest.mark.parametrize(
    "unsafe_url",
    [
        "ftp://chineseposters.net/sites/default/files/images/unsafe.jpg",
        "https://user@chineseposters.net/sites/default/files/images/unsafe.jpg",
        "https://chineseposters.net:444/sites/default/files/images/unsafe.jpg",
        "https://chineseposters.net/foo/sites/default/files/images/unsafe.jpg",
    ],
)
def test_forced_provider_rejects_non_http_credentials_and_ports(unsafe_url):
    plugin = make_plugin("forced-provider-reject")

    with pytest.raises(RuntimeError, match="provider URL"):
        plugin._forced_poster_from_settings({"posterImageUrl": unsafe_url})


def test_provider_html_links_canonicalize_http_and_ignore_unsafe_authorities():
    plugin = make_plugin("provider-link-canonicalization")
    links = plugin._extract_poster_links(
        """
        <a href="http://chineseposters.net/posters/http-ok">HTTP</a>
        <a href="ftp://chineseposters.net/posters/ftp-bad">FTP</a>
        <a href="https://user@chineseposters.net/posters/credential-bad">Credentials</a>
        <a href="https://chineseposters.net:444/posters/port-bad">Port</a>
        """
    )

    assert links == [
        {
            "url": "https://chineseposters.net/posters/http-ok",
            "title": "HTTP",
        }
    ]


def test_provider_detail_canonicalizes_http_page_and_image_urls():
    plugin = make_plugin("provider-detail-canonicalization")
    poster = plugin._extract_poster_data(
        """
        <h1>HTTP provider poster</h1>
        <img src="http://chineseposters.net/sites/default/files/images/http-ok.jpg">
        """,
        "http://chineseposters.net/posters/http-ok",
    )

    assert poster == {
        "page_url": "https://chineseposters.net/posters/http-ok",
        "image_url": (
            "https://chineseposters.net/sites/default/files/images/http-ok.jpg"
        ),
        "title": "HTTP provider poster",
    }


def test_provider_detail_rejects_prefixed_uncontrolled_image_path():
    plugin = make_plugin("provider-detail-controlled-root")
    poster = plugin._extract_poster_data(
        """
        <h1>Unsafe provider path</h1>
        <img src="https://chineseposters.net/foo/sites/default/files/images/unsafe.jpg">
        """,
        "https://chineseposters.net/posters/unsafe-provider-path",
    )

    assert poster["image_url"] is None


def _write_legacy_profile_overflow(plugin, bank, *, all_active):
    document, current = bank.load_for_data()
    current["last_used_at"] = "2026-07-12T10:00:00+00:00"
    for index in range(64):
        fingerprint = f"overflow-profile-{index:02d}"
        instance_uuid = f"overflow-instance-{index:02d}"
        document["profiles"][fingerprint] = {
            **current,
            "profile_fingerprint": fingerprint,
            "instance_uuid": instance_uuid,
            "records": [],
            "current_selection": None,
            "pending_selection": None,
            "last_used_at": f"2020-01-01T00:00:{index:02d}+00:00",
        }
        if all_active:
            document["instance_profiles"][instance_uuid] = fingerprint
    path = plugin._state_path()
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def test_bank_writer_rejects_legacy_profile_overflow_before_atomic_write(monkeypatch):
    plugin = make_plugin("bank-profile-overflow-save")
    settings = bind_presentation_instance_identity(
        {"fitMode": "contain", "sourceMode": "all_archive", "maxPage": 0},
        "bank-profile-overflow-save-instance",
    )
    bank = plugin._presentation_bank(settings, DeviceConfig().get_resolution())
    path = _write_legacy_profile_overflow(plugin, bank, all_active=False)
    document = json.loads(path.read_text(encoding="utf-8"))
    monkeypatch.setattr(
        presentation_bank_module,
        "atomic_write_json",
        lambda *_args, **_kwargs: pytest.fail("overflow state must not reach writer"),
    )

    with pytest.raises(RuntimeError, match="profile capacity"):
        bank.save(document)


def test_prepare_prunes_inactive_legacy_profile_overflow_before_any_save():
    plugin = make_plugin("prepare-profile-overflow-prune")
    settings = bind_presentation_instance_identity(
        {"fitMode": "contain", "sourceMode": "all_archive", "maxPage": 0},
        "prepare-profile-overflow-prune-instance",
    )
    bank = plugin._presentation_bank(settings, DeviceConfig().get_resolution())
    path = _write_legacy_profile_overflow(plugin, bank, all_active=False)

    with pytest.raises(RuntimeError, match="no decoded media"):
        plugin.prepare_presentation(
            settings,
            DeviceConfig(),
            request=_request("e" * 32, origin="overflow-prune-origin"),
            resolved_theme_context=None,
        )

    state = json.loads(path.read_text(encoding="utf-8"))
    assert len(state["profiles"]) == 64
    assert len(state["instance_profiles"]) <= 64
    assert all(
        fingerprint in state["profiles"]
        for fingerprint in state["instance_profiles"].values()
    )


def test_warm_profile_overflow_fails_closed_when_all_profiles_are_active():
    plugin = make_plugin("warm-profile-overflow-active")
    settings = bind_presentation_instance_identity(
        {"fitMode": "contain", "sourceMode": "all_archive", "maxPage": 0},
        "warm-profile-overflow-active-instance",
    )
    bank = plugin._presentation_bank(settings, DeviceConfig().get_resolution())
    path = _write_legacy_profile_overflow(plugin, bank, all_active=True)
    before = path.read_bytes()

    with pytest.raises(RuntimeError, match="profile capacity"):
        bank.load_warm()

    assert path.read_bytes() == before


def test_reconcile_fails_closed_on_legacy_profile_overflow():
    plugin = make_plugin("reconcile-profile-overflow")
    settings = bind_presentation_instance_identity(
        {"fitMode": "contain", "sourceMode": "all_archive", "maxPage": 0},
        "reconcile-profile-overflow-instance",
    )
    bank = plugin._presentation_bank(settings, DeviceConfig().get_resolution())
    path = _write_legacy_profile_overflow(plugin, bank, all_active=False)
    before = path.read_bytes()

    with pytest.raises(RuntimeError, match="profile capacity"):
        plugin.reconcile_presentation_receipt(settings, _receipt("d" * 32))

    assert path.read_bytes() == before


def test_out_of_order_receipts_keep_last_content_and_timestamp_consistent(monkeypatch):
    plugin = make_plugin("out-of-order-receipts")
    settings_a, _posters, _loader, _image = _hydrate_bank(
        plugin,
        monkeypatch,
        fit_mode="contain",
        offset=0,
        instance_uuid="out-of-order-instance-a",
    )
    request_a = _request("a1" * 16, origin="origin-profile-a")
    plugin.prepare_presentation(
        settings_a,
        DeviceConfig(),
        request=request_a,
        resolved_theme_context=None,
    )
    staged_a = _state_json(plugin)
    fingerprint_a = staged_a["active_fingerprint"]
    pending_a = _selection_posters(
        staged_a["profiles"][fingerprint_a],
        _pending(staged_a["profiles"][fingerprint_a], request_a.request_id),
    )

    settings_b, _posters, _loader, _image = _hydrate_bank(
        plugin,
        monkeypatch,
        fit_mode="landscape",
        offset=100,
        instance_uuid="out-of-order-instance-b",
    )
    request_b = _request("b2" * 16, origin="origin-profile-b")
    plugin.prepare_presentation(
        settings_b,
        DeviceConfig(),
        request=request_b,
        resolved_theme_context=None,
    )
    staged_b = _state_json(plugin)
    fingerprint_b = staged_b["active_fingerprint"]
    pending_b = _selection_posters(
        staged_b["profiles"][fingerprint_b],
        _pending(staged_b["profiles"][fingerprint_b], request_b.request_id),
    )

    plugin.reconcile_presentation_receipt(
        settings_b,
        _receipt(
            request_b.request_id,
            display="display-newer",
            committed_at="2026-07-12T10:02:00+00:00",
        ),
    )
    plugin.reconcile_presentation_receipt(
        settings_a,
        _receipt(
            request_a.request_id,
            display="display-older",
            committed_at="2026-07-12T10:01:00+00:00",
        ),
    )

    state = _state_json(plugin)
    assert state["last_displayed_at"] == "2026-07-12T10:02:00+00:00"
    assert state["last_page_urls"] == [poster["page_url"] for poster in pending_b]
    assert all(
        poster["page_url"] in state["discarded_page_urls"]
        for poster in pending_a + pending_b
    )


def test_bad_state_json_fails_closed_without_provider_or_history_overwrite(monkeypatch):
    plugin = make_plugin("bad-state-json")
    plugin._state_path().write_text('{"discarded_page_urls": [', encoding="utf-8")
    before = plugin._state_path().read_bytes()
    plugin.image_loader = FakeImageLoader(Image.new("RGB", (200, 400), "red"))
    monkeypatch.setattr(
        plugin.image_loader,
        "from_url",
        lambda *_args, **_kwargs: pytest.fail("bad state must fail before provider media"),
    )

    with pytest.raises(RuntimeError, match="state"):
        plugin.generate_image(
            bind_presentation_instance_identity({}, "bad-state-instance"),
            DeviceConfig(),
        )

    assert plugin._state_path().read_bytes() == before


def test_both_state_writers_use_durable_atomic_json(monkeypatch):
    plugin = make_plugin("durable-state-writers")
    writes = []

    def record_write(path, payload, *, mode):
        writes.append((Path(path), payload, mode))

    monkeypatch.setattr(
        backtothedate_module,
        "atomic_write_json",
        record_write,
        raising=False,
    )
    plugin._write_state({"discarded_page_urls": ["one"]})
    settings = bind_presentation_instance_identity(
        {"fitMode": "contain"},
        "durable-writer-instance",
    )
    bank = plugin._presentation_bank(settings, DeviceConfig().get_resolution())
    monkeypatch.setattr(
        presentation_bank_module,
        "atomic_write_json",
        record_write,
        raising=False,
    )
    bank.save({"schema_version": 1, "profiles": {}})

    assert writes == [
        (
            plugin._state_path(),
            {"discarded_page_urls": ["one"]},
            0o600,
        ),
        (
            plugin._state_path(),
            {"schema_version": 1, "profiles": {}},
            0o600,
        ),
    ]


def test_state_writer_never_follows_precreated_fixed_temp_path():
    plugin = make_plugin("state-temp-symlink")
    target = plugin._state_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    fixed_temp = target.with_suffix(target.suffix + ".tmp")
    sentinel = target.parent / "sentinel.txt"
    sentinel.write_text("do-not-touch", encoding="utf-8")
    try:
        os.symlink(sentinel, fixed_temp)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")

    plugin._write_state({"safe": True})

    assert json.loads(target.read_text(encoding="utf-8")) == {"safe": True}
    assert sentinel.read_text(encoding="utf-8") == "do-not-touch"
    assert fixed_temp.is_symlink()


def test_legacy_cache_state_migrates_to_durable_data_without_deleting_source(monkeypatch):
    plugin = make_plugin("legacy-state-migration")
    root = Path(plugin.get_plugin_dir())
    durable = root / "durable"
    cache = root / "cache"
    monkeypatch.setattr(
        plugin,
        "data_dir",
        lambda *args, **kwargs: durable,
    )

    def cache_dir(*_args, leaf=None, **_kwargs):
        path = cache / leaf if leaf else cache
        path.mkdir(parents=True, exist_ok=True)
        return path

    monkeypatch.setattr(plugin, "cache_dir", cache_dir)
    legacy_path = plugin._legacy_state_path()
    legacy_path.parent.mkdir(parents=True, exist_ok=True)
    legacy = {
        "discarded_page_urls": ["https://chineseposters.net/posters/legacy"],
        "discarded_image_urls": [
            "https://chineseposters.net/sites/default/files/images/legacy.jpg"
        ],
        "last_displayed_at": "2026-07-10T08:00:00+00:00",
    }
    legacy_path.write_text(json.dumps(legacy), encoding="utf-8")
    legacy_bytes = legacy_path.read_bytes()

    _hydrate_bank(plugin, monkeypatch)

    migrated = _state_json(plugin)
    assert plugin._state_path().parent == durable
    assert migrated["discarded_page_urls"] == legacy["discarded_page_urls"]
    assert migrated["discarded_image_urls"] == legacy["discarded_image_urls"]
    assert migrated["last_displayed_at"] == legacy["last_displayed_at"]
    assert legacy_path.read_bytes() == legacy_bytes


def test_expired_media_is_pruned_by_download_age_not_recent_access(monkeypatch):
    plugin = make_plugin("expired-media")
    settings, _posters, _loader, _image = _hydrate_bank(plugin, monkeypatch)
    state = _state_json(plugin)
    profile = _active_profile(state)
    expired = profile["records"][0]
    expired["downloaded_at"] = (
        datetime.now(timezone.utc) - timedelta(hours=49)
    ).isoformat()
    plugin._write_state(state)
    bank = plugin._presentation_bank(settings, DeviceConfig().get_resolution())
    target = bank.media.path(expired["media_key"], suffix=".png")
    os.utime(target, None)
    monkeypatch.setattr(
        plugin.image_loader,
        "from_url",
        lambda *_args, **_kwargs: pytest.fail("23 warm records must not trigger refill"),
    )

    plugin.generate_image(settings, DeviceConfig())

    refreshed = _active_profile(_state_json(plugin))
    assert expired["media_key"] not in {
        record["media_key"] for record in refreshed["records"]
    }


def test_media_namespace_uses_prescribed_48h_64_file_96mib_budget():
    assert MEDIA_BUDGET.max_age_seconds == 48 * 60 * 60
    assert MEDIA_BUDGET.max_files == 64
    assert MEDIA_BUDGET.max_bytes == 96 * 1024 * 1024


def test_extract_poster_links_deduplicates_image_and_text_links():
    plugin = make_plugin("links")
    html = """
    <a href="/posters/d12-729"><img src="/thumb.jpg" alt=""></a>
    <a href="/posters/d12-729">An order given to the Shanghai Municipal Police</a>
    <a href="/posters/posters">Posters</a>
    <a href="https://example.com/posters/foreign">Foreign poster</a>
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
    <img src="https://example.com/sites/default/files/images/foreign.jpg">
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

    image = plugin.generate_image(
        _preview_settings({"fitMode": "rotate_portrait"}),
        DeviceConfig(),
    )

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

    image = plugin.generate_image(
        _preview_settings({"fitMode": "landscape"}),
        DeviceConfig(),
    )

    assert image.size == (800, 480)
    assert image.mode == "RGB"
    assert len(loader.calls) == 1
    assert rendered_sizes == [(500, 260)]


def test_generate_image_triptych_loads_three_posters_without_marking_displayed(monkeypatch):
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

    image = plugin.generate_image(
        _preview_settings({"fitMode": "triptych", "attempts": 3}),
        DeviceConfig(),
    )
    assert image.size == (800, 480)
    assert image.mode == "RGB"
    assert len(loader.calls) == 3
    assert not plugin._state_path().exists()


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

    image = plugin.generate_image(
        _preview_settings({"fitMode": "triptych", "attempts": 3}),
        DeviceConfig(),
    )
    assert image.size == (800, 480)
    assert image.getpixel((0, 0)) == (12, 34, 56)
    assert rendered_sizes == [(500, 260)]
    assert len(loader.calls) == 1
    assert not plugin._state_path().exists()


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
        _preview_settings({
            "fitMode": "triptych",
            "posterImageUrl": "https://chineseposters.net/sites/default/files/images/landscape.jpg",
            "posterPageUrl": "https://chineseposters.net/posters/landscape",
            "posterTitle": "Landscape",
        }),
        DeviceConfig(),
    )
    assert image.size == (800, 480)
    assert image.getpixel((0, 0)) == (12, 34, 56)
    assert rendered_sizes == [(500, 260)]
    assert len(loader.calls) == 1
    assert not plugin._state_path().exists()


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

    image = plugin.generate_image(
        _preview_settings({"fitMode": "triptych", "attempts": 1}),
        DeviceConfig(),
    )
    assert image.size == (800, 480)
    assert image.getpixel((0, 0)) == (12, 34, 56)
    assert rendered_sizes == [(500, 260)]
    assert len(loader.calls) == 3
    assert not plugin._state_path().exists()


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

    image = plugin.generate_image(
        _preview_settings({"fitMode": "contain"}),
        DeviceConfig(),
    )

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
