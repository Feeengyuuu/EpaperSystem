import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.base_plugin.presentation import (  # noqa: E402
    PresentationMode,
    PresentationRequestContext,
    bind_presentation_instance_identity,
)
from plugins.gcd_comic_covers.gcd_comic_covers import (  # noqa: E402
    GcdComicCovers,
    GcdCoverImageUnavailable,
    _canonical_provider_url,
    _GcdMonthlyParser,
)
from plugins.gcd_comic_covers.presentation_bank import (  # noqa: E402
    MAX_STATE_BYTES,
    MEDIA_MAX_DIMENSION,
    MEDIA_MAX_FILES,
    MEDIA_MAX_OBJECT_BYTES,
    MEDIA_MAX_PIXELS,
    READY_TARGET,
)
from runtime.runtime_state import PresentationCommitReceipt  # noqa: E402

MEDIA_MAX_BYTES = 128 * 1024 * 1024


class DeviceConfig:
    def get_resolution(self):
        return (800, 480)

    def get_config(self, key, default=None):
        if key == "timezone":
            return "America/Los_Angeles"
        if key == "orientation":
            return "horizontal"
        return default


class StaticLoader:
    def from_url(self, url, dimensions, timeout_ms=40000, resize=True, headers=None, focus_crop=False):
        return Image.new("RGB", (320, 480), "white")


class MissingImageLoader:
    def from_url(self, url, dimensions, timeout_ms=40000, resize=True, headers=None, focus_crop=False):
        return None


def make_plugin(tmp_path, monkeypatch):
    plugin = GcdComicCovers({"id": "gcd_comic_covers"})
    plugin.image_loader = StaticLoader()
    monkeypatch.setattr(plugin, "_current_date", lambda _device: date(2026, 7, 12))

    def download_cover_image(cover_url, candidate, detail):
        image = plugin.image_loader.from_url(cover_url, (800, 480), resize=False)
        if not image:
            raise GcdCoverImageUnavailable("cover image could not be loaded", candidate, detail, cover_url)
        return image
    monkeypatch.setattr(plugin, "_download_cover_image", download_cover_image)
    monkeypatch.setenv("INKYPI_GCD_COMIC_COVERS_CACHE", str(tmp_path))
    return plugin


def _bound_settings(*, fit_mode="contain", instance_uuid="gcd-test-instance"):
    return bind_presentation_instance_identity(
        {
            "fitMode": fit_mode,
            "sourceMode": "mixed",
            "countryCodes": "us",
            "maxCoverAttempts": 8,
        },
        instance_uuid,
    )


def _request(request_id, *, origin="origin-display"):
    return PresentationRequestContext(
        request_id=request_id,
        requested_at="2026-07-12T10:00:00+00:00",
        origin_display_commit_id=origin,
        last_receipt=None,
    )


def _receipt(request_id, *, display="prepared-display", committed_at="2026-07-12T10:01:00+00:00"):
    return PresentationCommitReceipt(
        request_id=request_id,
        committed_at=committed_at,
        display_commit_id=display,
        structural_generation=1,
        settings_revision=1,
        theme_mode=None,
    )


def _bank_candidate(index, *, metadata_only=False):
    return {
        "source": "comicvine" if index % 2 == 0 else "gcd",
        "issue_id": f"issue:{index}",
        "match_quality": "comicvine_recent" if index % 2 == 0 else "exact_day",
        "cover_url": f"https://comicvine.gamespot.com/a/uploads/scale_large/1/{index}.jpg",
        "page_url": f"https://www.comics.org/issue/{1000 + index}/",
        "series_name": f"Series {index}",
        "issue_number": str(index),
        "publisher": "Example Comics",
        "on_sale_date": "2026-07-12",
        "date_label": "2026-07-12",
        "cover_credits": "Example Artist",
        "metadata_only": metadata_only,
    }


def _hydrate_presentation_bank(plugin, monkeypatch, settings, *, metadata_only=False):
    candidates = [_bank_candidate(index, metadata_only=metadata_only) for index in range(24)]
    monkeypatch.setattr(plugin, "_candidate_pool", lambda _settings, _today: list(candidates))

    def load_cover(candidate, _dimensions, _settings):
        if candidate.get("metadata_only"):
            raise GcdCoverImageUnavailable(
                "source media unavailable",
                candidate,
                candidate,
                candidate["cover_url"],
            )
        return {
            **candidate,
            "image": Image.new("RGB", (220, 360), (80, 110, 140)),
        }

    monkeypatch.setattr(plugin, "_load_cover", load_cover)
    image = plugin.generate_image(settings, DeviceConfig())
    return candidates, image


def _fill_presentation_bank(plugin, monkeypatch, settings, *, metadata_only=False):
    result = None
    for _index in range(3):
        result = _hydrate_presentation_bank(
            plugin,
            monkeypatch,
            settings,
            metadata_only=metadata_only,
        )
    return result


def _state_json(plugin):
    return json.loads(plugin._state_path().read_text(encoding="utf-8"))


def _profile_for(state, instance_uuid="gcd-test-instance"):
    fingerprint = state["instance_profiles"][instance_uuid]
    return state["profiles"][fingerprint]


def _pending_issue_ids(state, request_id, instance_uuid="gcd-test-instance"):
    profile = _profile_for(state, instance_uuid)
    pending = profile["pending_selection"]
    assert pending["request_id"] == request_id
    records = {record["record_key"]: record for record in profile["records"]}
    return [records[key]["issue_id"] for key in pending["record_keys"]]


def _selection_issue_ids(state, selection, instance_uuid="gcd-test-instance"):
    profile = _profile_for(state, instance_uuid)
    records = {record["record_key"]: record for record in profile["records"]}
    return [records[key]["issue_id"] for key in selection["record_keys"]]


def _cache_tree(root):
    root = Path(root)
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.is_symlink()
    }


def test_gcd_data_hydration_does_not_consume_seen_issue_ids(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    settings = _bound_settings()
    before = {
        "version": "gcd-comic-covers-state-v1",
        "date_buckets": {
            "07-12": {
                "seen_issue_ids": ["already-seen"],
                "last_issue_id": "already-seen",
                "last_displayed_at": "2026-07-11T10:00:00+00:00",
            }
        },
    }
    plugin._write_state(before)
    monkeypatch.setattr(
        plugin,
        "_mark_seen",
        lambda *_args, **_kwargs: pytest.fail("DATA hydration cannot consume display history"),
    )

    _candidates, image = _hydrate_presentation_bank(plugin, monkeypatch, settings)

    state = _state_json(plugin)
    assert state["date_buckets"] == before["date_buckets"]
    assert len(_profile_for(state)["records"]) == 8
    assert _profile_for(state)["pending_selection"] is None
    assert image.size == DeviceConfig().get_resolution()
    assert plugin.presentation_mode(settings) is PresentationMode.PREPARED_BANK


def test_gcd_warm_presentation_never_calls_gcd_comicvine_or_cover_http(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    settings = _bound_settings()
    _hydrate_presentation_bank(plugin, monkeypatch, settings)
    request = _request("a" * 32)
    for name in ("_candidate_pool", "_fetch_json", "_comic_vine_get", "_download_cover_image"):
        monkeypatch.setattr(
            plugin,
            name,
            lambda *_args, _name=name, **_kwargs: pytest.fail(
                f"warm presentation called provider method {_name}"
            ),
        )

    preparation = plugin.prepare_presentation(
        settings,
        DeviceConfig(),
        request=request,
        resolved_theme_context=None,
    )

    assert preparation.request_id == request.request_id
    assert preparation.changed is True
    assert preparation.image.size == DeviceConfig().get_resolution()
    assert _pending_issue_ids(_state_json(plugin), request.request_id)


def test_gcd_prepare_then_canceled_followup_leaves_seen_state_unchanged(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    settings = _bound_settings()
    _hydrate_presentation_bank(plugin, monkeypatch, settings)
    canceled = _request("b" * 32, origin="origin-canceled")
    plugin.prepare_presentation(
        settings,
        DeviceConfig(),
        request=canceled,
        resolved_theme_context=None,
    )
    replacement = _request("c" * 32, origin="origin-replacement")
    plugin.prepare_presentation(
        settings,
        DeviceConfig(),
        request=replacement,
        resolved_theme_context=None,
    )
    before = plugin._state_path().read_bytes()

    plugin.reconcile_presentation_receipt(
        settings,
        _receipt(canceled.request_id, display="late-canceled-display"),
    )

    assert plugin._state_path().read_bytes() == before


@pytest.mark.parametrize(("fit_mode", "expected_count"), [("contain", 1), ("triptych", 3)])
def test_gcd_matching_receipt_commits_single_or_triptych_exactly_once(
    tmp_path,
    monkeypatch,
    fit_mode,
    expected_count,
):
    plugin = make_plugin(tmp_path, monkeypatch)
    settings = _bound_settings(fit_mode=fit_mode)
    _hydrate_presentation_bank(plugin, monkeypatch, settings)
    request = _request("d" * 32)
    plugin.prepare_presentation(
        settings,
        DeviceConfig(),
        request=request,
        resolved_theme_context=None,
    )
    pending_ids = _pending_issue_ids(_state_json(plugin), request.request_id)
    assert len(pending_ids) == expected_count
    receipt = _receipt(request.request_id)

    plugin.reconcile_presentation_receipt(settings, receipt)

    committed = _state_json(plugin)
    bucket = committed["date_buckets"]["07-12"]
    assert bucket["seen_issue_ids"][-expected_count:] == pending_ids
    assert bucket["last_issue_id"] == pending_ids[-1]
    assert bucket["last_displayed_at"] == receipt.committed_at
    assert _profile_for(committed)["pending_selection"] is None
    committed_bytes = plugin._state_path().read_bytes()
    plugin.reconcile_presentation_receipt(settings, receipt)
    assert plugin._state_path().read_bytes() == committed_bytes


def test_gcd_metadata_fallback_is_not_seen_until_its_display_receipt(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    settings = _bound_settings()
    _hydrate_presentation_bank(plugin, monkeypatch, settings, metadata_only=True)
    hydrated = _state_json(plugin)
    assert hydrated.get("date_buckets", {}).get("07-12", {}).get("seen_issue_ids", []) == []
    request = _request("e" * 32)

    preparation = plugin.prepare_presentation(
        settings,
        DeviceConfig(),
        request=request,
        resolved_theme_context=None,
    )
    pending_ids = _pending_issue_ids(_state_json(plugin), request.request_id)
    assert preparation.image.size == DeviceConfig().get_resolution()
    seen_after_origin = set(
        _state_json(plugin).get("date_buckets", {}).get("07-12", {}).get("seen_issue_ids", [])
    )
    assert seen_after_origin.isdisjoint(pending_ids)

    plugin.reconcile_presentation_receipt(settings, _receipt(request.request_id))

    bucket = _state_json(plugin)["date_buckets"]["07-12"]
    assert bucket["seen_issue_ids"][-len(pending_ids):] == pending_ids


def test_gcd_bank_cleanup_is_bounded_and_does_not_follow_symlinks(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    settings = _bound_settings()
    _hydrate_presentation_bank(plugin, monkeypatch, settings)
    bank = plugin._presentation_bank(settings, DeviceConfig().get_resolution())
    media_root = bank.media.root
    outside = tmp_path / "outside-cover.png"
    outside.write_bytes(b"do-not-delete")
    link = media_root / ("f" * 64 + ".png")
    try:
        os.symlink(outside, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable")
    for index in range(MEDIA_MAX_FILES + 12):
        bank.media.put_bytes(f"{index:064x}", b"x", suffix=".png")

    status = bank.media.maintenance()

    assert status.files <= MEDIA_MAX_FILES
    assert status.bytes <= MEDIA_MAX_BYTES
    assert outside.read_bytes() == b"do-not-delete"


def test_gcd_candidate_order_is_pure_even_when_committed_pool_is_exhausted(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    today = date(2026, 7, 12)
    state = {
        "version": "gcd-comic-covers-state-v1",
        "date_buckets": {
            "07-12": {
                "seen_issue_ids": ["issue:1", "issue:2"],
                "last_issue_id": "issue:2",
            }
        },
    }
    before = json.dumps(state, sort_keys=True)
    candidates = [_bank_candidate(1), _bank_candidate(2)]

    ordered = plugin._candidate_order(candidates, state, today)

    assert {candidate["issue_id"] for candidate in ordered} == {"issue:1", "issue:2"}
    assert json.dumps(state, sort_keys=True) == before


def test_gcd_trusted_origin_commits_current_once_before_pending(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    settings = _bound_settings()
    _hydrate_presentation_bank(plugin, monkeypatch, settings)
    hydrated = _state_json(plugin)
    current_ids = _selection_issue_ids(
        hydrated,
        _profile_for(hydrated)["current_selection"],
    )
    request = _request("1" * 32)

    plugin.prepare_presentation(
        settings,
        DeviceConfig(),
        request=request,
        resolved_theme_context=None,
    )

    prepared = _state_json(plugin)
    pending_ids = _pending_issue_ids(prepared, request.request_id)
    seen = prepared["date_buckets"]["07-12"]["seen_issue_ids"]
    assert seen[-len(current_ids):] == current_ids
    assert set(pending_ids).isdisjoint(seen)
    first_bytes = plugin._state_path().read_bytes()
    plugin.prepare_presentation(
        settings,
        DeviceConfig(),
        request=request,
        resolved_theme_context=None,
    )
    assert plugin._state_path().read_bytes() == first_bytes


def test_gcd_restart_reuses_exact_pending_selection(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    settings = _bound_settings()
    _hydrate_presentation_bank(plugin, monkeypatch, settings)
    request = _request("2" * 32)
    plugin.prepare_presentation(
        settings,
        DeviceConfig(),
        request=request,
        resolved_theme_context=None,
    )
    first = _pending_issue_ids(_state_json(plugin), request.request_id)
    restarted = make_plugin(tmp_path, monkeypatch)
    monkeypatch.setattr(
        restarted,
        "_candidate_pool",
        lambda *_args, **_kwargs: pytest.fail("warm restart cannot query providers"),
    )

    restarted.prepare_presentation(
        settings,
        DeviceConfig(),
        request=request,
        resolved_theme_context=None,
    )

    assert _pending_issue_ids(_state_json(restarted), request.request_id) == first


def test_gcd_missing_prepared_media_fails_closed_and_keeps_pending_metadata(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    settings = _bound_settings()
    _hydrate_presentation_bank(plugin, monkeypatch, settings)
    request = _request("3" * 32)
    plugin.prepare_presentation(
        settings,
        DeviceConfig(),
        request=request,
        resolved_theme_context=None,
    )
    state = _state_json(plugin)
    profile = _profile_for(state)
    pending = profile["pending_selection"]
    records = {record["record_key"]: record for record in profile["records"]}
    media_record = next(records[key] for key in pending["record_keys"] if records[key]["media_key"])
    bank = plugin._presentation_bank(settings, DeviceConfig().get_resolution(), date(2026, 7, 12))
    bank.media.path(media_record["media_key"], suffix=".png").unlink()
    before = plugin._state_path().read_bytes()
    for action in (
        lambda: plugin.prepare_presentation(
            settings,
            DeviceConfig(),
            request=request,
            resolved_theme_context=None,
        ),
        lambda: plugin.reconcile_presentation_receipt(settings, _receipt(request.request_id)),
    ):
        with pytest.raises(RuntimeError, match="media"):
            action()
        assert plugin._state_path().read_bytes() == before
    assert _profile_for(_state_json(plugin))["pending_selection"] == pending


def test_gcd_identical_settings_instances_keep_independent_pending_receipts(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    settings_a = _bound_settings(instance_uuid="gcd-instance-a")
    settings_b = _bound_settings(instance_uuid="gcd-instance-b")
    _hydrate_presentation_bank(plugin, monkeypatch, settings_a)
    _hydrate_presentation_bank(plugin, monkeypatch, settings_b)
    request_a = _request("4" * 32, origin="origin-a")
    request_b = _request("5" * 32, origin="origin-b")
    plugin.prepare_presentation(
        settings_a,
        DeviceConfig(),
        request=request_a,
        resolved_theme_context=None,
    )
    plugin.prepare_presentation(
        settings_b,
        DeviceConfig(),
        request=request_b,
        resolved_theme_context=None,
    )

    plugin.reconcile_presentation_receipt(settings_a, _receipt(request_a.request_id))

    state = _state_json(plugin)
    profile_a = _profile_for(state, "gcd-instance-a")
    profile_b = _profile_for(state, "gcd-instance-b")
    assert profile_a["last_applied_request_id"] == request_a.request_id
    assert profile_a["pending_selection"] is None
    assert profile_b["pending_selection"]["request_id"] == request_b.request_id
    assert profile_b["last_applied_request_id"] is None


@pytest.mark.parametrize(
    ("mode", "background", "accent"),
    [
        ("day", (242, 238, 230), (48, 109, 132)),
        ("night", (13, 20, 23), (108, 184, 208)),
    ],
)
def test_gcd_prepared_media_uses_resolved_day_night_chrome_without_http(
    tmp_path,
    monkeypatch,
    mode,
    background,
    accent,
):
    plugin = make_plugin(tmp_path, monkeypatch)
    settings = _bound_settings(instance_uuid=f"gcd-theme-{mode}")
    _hydrate_presentation_bank(plugin, monkeypatch, settings)
    monkeypatch.setattr(
        plugin,
        "_candidate_pool",
        lambda *_args, **_kwargs: pytest.fail("theme presentation cannot fetch candidates"),
    )
    monkeypatch.setattr(
        plugin,
        "_download_cover_image",
        lambda *_args, **_kwargs: pytest.fail("theme presentation cannot fetch media"),
    )
    request = _request(("6" if mode == "day" else "7") * 32)

    preparation = plugin.prepare_presentation(
        settings,
        DeviceConfig(),
        request=request,
        resolved_theme_context={
            "mode": mode,
            "palette": {"background": background, "accent": accent},
        },
    )

    assert preparation.image.getpixel((6, 6)) == accent
    assert preparation.image.info["inkypi_theme_mode"] == mode


def test_gcd_refill_progresses_to_18_with_at_most_configured_attempts_per_data(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    settings = _bound_settings()
    candidates = [_bank_candidate(index) for index in range(30)]
    monkeypatch.setattr(plugin, "_candidate_pool", lambda _settings, _today: list(candidates))
    calls_by_pass = []
    current_calls = []

    def load_cover(candidate, _dimensions, _settings):
        current_calls.append(candidate["issue_id"])
        return {
            **candidate,
            "image": Image.new("RGB", (220, 360), "navy"),
        }

    monkeypatch.setattr(plugin, "_load_cover", load_cover)
    for _index in range(3):
        current_calls.clear()
        plugin.generate_image(settings, DeviceConfig())
        calls_by_pass.append(len(current_calls))

    state = _state_json(plugin)
    assert calls_by_pass == [8, 8, 2]
    assert len(_profile_for(state)["records"]) == READY_TARGET
    assert _profile_for(state)["refill_in_progress"] is False


def test_gcd_state_and_image_metadata_limits_fail_before_replacing_committed_state(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    settings = _bound_settings()
    _hydrate_presentation_bank(plugin, monkeypatch, settings)
    bank = plugin._presentation_bank(settings, DeviceConfig().get_resolution(), date(2026, 7, 12))
    document, profile = bank.load_for_data()
    before = plugin._state_path().read_bytes()
    document["oversized_metadata"] = "x" * MAX_STATE_BYTES
    with pytest.raises(RuntimeError, match="size limit"):
        bank.save(document)
    assert plugin._state_path().read_bytes() == before

    candidate = _bank_candidate(99)
    with pytest.raises(RuntimeError, match="dimensions"):
        bank.ingest(
            profile,
            candidate,
            Image.new("RGB", (MEDIA_MAX_DIMENSION + 1, 1), "white"),
        )
    pixel_width = 8000
    pixel_height = MEDIA_MAX_PIXELS // pixel_width + 1
    with pytest.raises(RuntimeError, match="dimensions"):
        bank.ingest(
            profile,
            candidate,
            Image.new("1", (pixel_width, pixel_height), 1),
        )


def test_gcd_oversized_prepared_media_is_rejected_before_read_and_keeps_pending(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    settings = _bound_settings()
    _hydrate_presentation_bank(plugin, monkeypatch, settings)
    request = _request("8" * 32)
    plugin.prepare_presentation(
        settings,
        DeviceConfig(),
        request=request,
        resolved_theme_context=None,
    )
    state = _state_json(plugin)
    profile = _profile_for(state)
    pending = dict(profile["pending_selection"])
    records = {record["record_key"]: record for record in profile["records"]}
    record = next(records[key] for key in pending["record_keys"] if records[key]["media_key"])
    bank = plugin._presentation_bank(settings, DeviceConfig().get_resolution(), date(2026, 7, 12))
    target = bank.media.path(record["media_key"], suffix=".png")
    with target.open("wb") as handle:
        handle.truncate(MEDIA_MAX_OBJECT_BYTES + 1)
    before = plugin._state_path().read_bytes()

    with pytest.raises(RuntimeError, match="object budget"):
        plugin.prepare_presentation(
            settings,
            DeviceConfig(),
            request=request,
            resolved_theme_context=None,
        )

    assert plugin._state_path().read_bytes() == before
    assert _profile_for(_state_json(plugin))["pending_selection"] == pending


def test_gcd_corrupt_state_fails_closed_without_replacement(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    settings = _bound_settings()
    _hydrate_presentation_bank(plugin, monkeypatch, settings)
    plugin._state_path().write_bytes(b"{not-json")
    before = plugin._state_path().read_bytes()

    with pytest.raises(RuntimeError, match="read safely"):
        plugin.prepare_presentation(
            settings,
            DeviceConfig(),
            request=_request("9" * 32),
            resolved_theme_context=None,
        )

    assert plugin._state_path().read_bytes() == before


def test_gcd_state_symlink_is_not_followed_or_replaced(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    settings = _bound_settings()
    _hydrate_presentation_bank(plugin, monkeypatch, settings)
    state_path = plugin._state_path()
    outside = tmp_path / "outside-state.json"
    outside.write_bytes(state_path.read_bytes())
    state_path.unlink()
    try:
        os.symlink(outside, state_path)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable")
    before = outside.read_bytes()

    with pytest.raises(RuntimeError, match="regular file"):
        plugin.prepare_presentation(
            settings,
            DeviceConfig(),
            request=_request("a1" * 16),
            resolved_theme_context=None,
        )

    assert outside.read_bytes() == before
    assert state_path.is_symlink()


@pytest.mark.parametrize("damage", ["missing", "expired"])
def test_gcd_data_exactly_recovers_protected_current_without_rotating(
    tmp_path,
    monkeypatch,
    damage,
):
    plugin = make_plugin(tmp_path, monkeypatch)
    settings = _bound_settings()
    _fill_presentation_bank(plugin, monkeypatch, settings)
    before = _state_json(plugin)
    profile = _profile_for(before)
    current = dict(profile["current_selection"])
    records = {record["record_key"]: record for record in profile["records"]}
    record = records[current["record_keys"][0]]
    bank = plugin._presentation_bank(settings, DeviceConfig().get_resolution(), date(2026, 7, 12))
    if damage == "missing":
        bank.media.path(record["media_key"], suffix=".png").unlink()
    else:
        document, writable_profile = bank.load_for_data()
        writable_record = next(
            item for item in writable_profile["records"] if item["record_key"] == record["record_key"]
        )
        writable_record["downloaded_at"] = (
            datetime.now(timezone.utc) - timedelta(days=31)
        ).isoformat()
        bank.save(document)
    calls = []

    def exact_download(url, candidate, detail):
        calls.append((url, candidate["record_key"], detail["record_key"]))
        return Image.new("RGB", (220, 360), "purple")

    monkeypatch.setattr(plugin, "_download_cover_image", exact_download)
    monkeypatch.setattr(
        plugin,
        "_candidate_pool",
        lambda *_args, **_kwargs: pytest.fail("full bank recovery cannot fetch candidates"),
    )

    image = plugin.generate_image(settings, DeviceConfig())

    after = _state_json(plugin)
    assert image.size == DeviceConfig().get_resolution()
    assert _profile_for(after)["current_selection"] == current
    assert calls == [(record["cover_url"], record["record_key"], record["record_key"])]


@pytest.mark.parametrize("damage", ["missing", "expired"])
def test_gcd_failed_exact_current_recovery_keeps_state_bytes_unchanged(
    tmp_path,
    monkeypatch,
    damage,
):
    plugin = make_plugin(tmp_path, monkeypatch)
    settings = _bound_settings()
    _fill_presentation_bank(plugin, monkeypatch, settings)
    state = _state_json(plugin)
    profile = _profile_for(state)
    current = profile["current_selection"]
    records = {record["record_key"]: record for record in profile["records"]}
    record = records[current["record_keys"][0]]
    bank = plugin._presentation_bank(settings, DeviceConfig().get_resolution(), date(2026, 7, 12))
    if damage == "missing":
        bank.media.path(record["media_key"], suffix=".png").unlink()
    else:
        document, writable_profile = bank.load_for_data()
        next(
            item for item in writable_profile["records"] if item["record_key"] == record["record_key"]
        )["downloaded_at"] = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()
        bank.save(document)
    before = plugin._state_path().read_bytes()
    monkeypatch.setattr(
        plugin,
        "_download_cover_image",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("exact recovery failed")),
    )

    with pytest.raises(RuntimeError, match="exact recovery|protected"):
        plugin.generate_image(settings, DeviceConfig())

    assert plugin._state_path().read_bytes() == before
    assert _profile_for(_state_json(plugin))["current_selection"] == current


@pytest.mark.parametrize("damage", ["missing", "expired"])
@pytest.mark.parametrize("recovery_succeeds", [True, False])
def test_gcd_data_recovers_exact_pending_or_fails_without_losing_receipt(
    tmp_path,
    monkeypatch,
    recovery_succeeds,
    damage,
):
    plugin = make_plugin(tmp_path, monkeypatch)
    settings = _bound_settings()
    _fill_presentation_bank(plugin, monkeypatch, settings)
    request = _request("b1" * 16)
    plugin.prepare_presentation(
        settings,
        DeviceConfig(),
        request=request,
        resolved_theme_context=None,
    )
    state = _state_json(plugin)
    profile = _profile_for(state)
    pending = dict(profile["pending_selection"])
    records = {record["record_key"]: record for record in profile["records"]}
    record = records[pending["record_keys"][0]]
    bank = plugin._presentation_bank(settings, DeviceConfig().get_resolution(), date(2026, 7, 12))
    if damage == "missing":
        bank.media.path(record["media_key"], suffix=".png").unlink()
    else:
        document, writable_profile = bank.load_for_data()
        next(
            item for item in writable_profile["records"] if item["record_key"] == record["record_key"]
        )["downloaded_at"] = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()
        bank.save(document)
    before = plugin._state_path().read_bytes()
    calls = []

    def recover(url, candidate, detail):
        calls.append(url)
        if not recovery_succeeds:
            raise RuntimeError("pending exact recovery failed")
        return Image.new("RGB", (220, 360), "teal")

    monkeypatch.setattr(plugin, "_download_cover_image", recover)
    if not recovery_succeeds:
        with pytest.raises(RuntimeError, match="pending exact recovery|protected"):
            plugin.generate_image(settings, DeviceConfig())
        assert plugin._state_path().read_bytes() == before
        assert _profile_for(_state_json(plugin))["pending_selection"] == pending
        return

    plugin.generate_image(settings, DeviceConfig())
    recovered = _state_json(plugin)
    assert _profile_for(recovered)["pending_selection"] == pending
    assert calls == [record["cover_url"]]

    plugin.reconcile_presentation_receipt(settings, _receipt(request.request_id))

    committed = _state_json(plugin)
    assert _profile_for(committed)["pending_selection"] is None
    assert _profile_for(committed)["last_applied_request_id"] == request.request_id


@pytest.mark.parametrize("metadata_only", [False, True])
def test_gcd_current_survives_date_rollover_without_unrelated_media_recovery(
    tmp_path,
    monkeypatch,
    metadata_only,
):
    plugin = make_plugin(tmp_path, monkeypatch)
    settings = _bound_settings()
    _fill_presentation_bank(plugin, monkeypatch, settings, metadata_only=metadata_only)
    before = _state_json(plugin)
    current = dict(_profile_for(before)["current_selection"])
    monkeypatch.setattr(plugin, "_current_date", lambda _device: date(2026, 7, 13))
    monkeypatch.setattr(
        plugin,
        "_download_cover_image",
        lambda *_args, **_kwargs: pytest.fail("fresh protected current needs no media recovery"),
    )
    candidates = [
        {**_bank_candidate(index), "on_sale_date": "2026-07-13", "target_date": "2026-07-13"}
        for index in range(30, 54)
    ]
    monkeypatch.setattr(plugin, "_candidate_pool", lambda _settings, _today: candidates)
    monkeypatch.setattr(
        plugin,
        "_load_cover",
        lambda candidate, _dimensions, _settings: (_ for _ in ()).throw(
            GcdCoverImageUnavailable(
                "new-day metadata",
                candidate,
                candidate,
                candidate["cover_url"],
            )
        ),
    )

    image = plugin.generate_image(settings, DeviceConfig())

    after = _state_json(plugin)
    assert image.size == DeviceConfig().get_resolution()
    assert _profile_for(after)["current_selection"] == current
    assert any(
        record["display_date_key"] == "07-13"
        for record in _profile_for(after)["records"]
    )


def _priority_candidate(index, quality):
    source = "comicvine" if quality == "comicvine_recent" else "gcd"
    candidate = _bank_candidate(index)
    candidate.update(
        {
            "source": source,
            "issue_id": f"{quality}:{index}",
            "match_quality": quality,
            "cover_url": (
                f"https://comicvine.gamespot.com/a/uploads/scale_large/1/{index}.jpg"
                if source == "comicvine"
                else f"https://files1.comics.org/img/gcd/covers_by_id/1/{index}.jpg"
            ),
        }
    )
    return candidate


def _priority_bank(tmp_path, monkeypatch, records):
    plugin = make_plugin(tmp_path, monkeypatch)
    settings = _bound_settings()
    bank = plugin._presentation_bank(settings, DeviceConfig().get_resolution(), date(2026, 7, 12))
    document, profile = bank.load_for_data()
    for candidate, size in records:
        bank.ingest(profile, candidate, Image.new("RGB", size, "orange"))
    bank.save(document)
    return bank, document, profile


def test_gcd_single_selection_never_leaves_available_comic_vine_tier(tmp_path, monkeypatch):
    records = [
        (_priority_candidate(index, "comicvine_recent"), (220, 360))
        for index in range(12)
    ] + [(_priority_candidate(90, "exact_day"), (220, 360))]
    bank, document, profile = _priority_bank(tmp_path, monkeypatch, records)

    for _index in range(30):
        selection = bank.choose_selection(
            document,
            profile,
            bank.ready_records(profile, prune=False),
            "contain",
        )
        selected = bank.selection_records(profile, selection, load_media=False)[0][0]
        assert selected["match_quality"] == "comicvine_recent"


def test_gcd_single_selection_prefers_exact_day_over_month_fallback(tmp_path, monkeypatch):
    records = [
        (_priority_candidate(1, "month_fallback"), (220, 360)),
        (_priority_candidate(2, "exact_day"), (220, 360)),
    ]
    bank, document, profile = _priority_bank(tmp_path, monkeypatch, records)

    selection = bank.choose_selection(
        document,
        profile,
        bank.ready_records(profile, prune=False),
        "contain",
    )

    selected = bank.selection_records(profile, selection, load_media=False)[0][0]
    assert selected["match_quality"] == "exact_day"


def test_gcd_single_selection_uses_real_cover_before_higher_tier_metadata_fallback(
    tmp_path,
    monkeypatch,
):
    plugin = make_plugin(tmp_path, monkeypatch)
    settings = _bound_settings()
    bank = plugin._presentation_bank(settings, DeviceConfig().get_resolution(), date(2026, 7, 12))
    document, profile = bank.load_for_data()
    bank.ingest(
        profile,
        _priority_candidate(1, "comicvine_recent"),
        render_kind="metadata",
    )
    bank.ingest(
        profile,
        _priority_candidate(2, "exact_day"),
        Image.new("RGB", (220, 360), "orange"),
    )

    selection = bank.choose_selection(
        document,
        profile,
        bank.ready_records(profile, prune=False),
        "contain",
    )

    selected = bank.selection_records(profile, selection, load_media=False)[0][0]
    assert selected["render_kind"] == "media"
    assert selected["match_quality"] == "exact_day"


def test_gcd_triptych_fills_priority_tiers_before_month_fallback(tmp_path, monkeypatch):
    records = [
        (_priority_candidate(1, "comicvine_recent"), (220, 360)),
        (_priority_candidate(2, "comicvine_recent"), (220, 360)),
        (_priority_candidate(3, "exact_day"), (220, 360)),
        (_priority_candidate(4, "month_fallback"), (220, 360)),
    ]
    bank, document, profile = _priority_bank(tmp_path, monkeypatch, records)

    selection = bank.choose_selection(
        document,
        profile,
        bank.ready_records(profile, prune=False),
        "triptych",
    )

    selected = bank.selection_records(profile, selection, load_media=False)
    qualities = [record["match_quality"] for record, _image in selected]
    assert qualities.count("comicvine_recent") == 2
    assert qualities.count("exact_day") == 1
    assert "month_fallback" not in qualities


def test_gcd_stateless_preview_leaves_entire_provider_cache_tree_unchanged(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    monkeypatch.setattr(plugin, "_current_date", lambda _device: date(2026, 7, 12))
    comic_vine = _priority_candidate(1, "comicvine_recent")
    gcd = _priority_candidate(2, "exact_day")
    monkeypatch.setattr(plugin, "_comic_vine_api_key", lambda _settings: "test-key")
    monkeypatch.setattr(
        plugin,
        "_fetch_comic_vine_recent_candidates",
        lambda _key, _today, _limit: [comic_vine],
    )
    monkeypatch.setattr(
        plugin,
        "_fetch_month_candidates",
        lambda _year, _month, _day=None: [gcd],
    )
    before = _cache_tree(tmp_path)

    image = plugin.generate_image(
        {
            "sourceMode": "mixed",
            "startYear": 2026,
            "endYear": 2026,
            "maxYearsPerRefresh": 1,
            "fitMode": "contain",
        },
        DeviceConfig(),
    )

    assert image.size == DeviceConfig().get_resolution()
    assert _cache_tree(tmp_path) == before


def test_gcd_legacy_date_buckets_are_deterministically_pruned_before_first_save(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    settings = _bound_settings()
    epoch = datetime(2020, 1, 1, tzinfo=timezone.utc)
    buckets = {
        f"legacy-{index:03d}": {
            "seen_issue_ids": [f"issue:{index}"],
            "last_issue_id": f"issue:{index}",
            "last_displayed_at": (epoch + timedelta(days=index)).isoformat(),
        }
        for index in range(370)
    }
    plugin._write_state(
        {
            "version": "gcd-comic-covers-state-v1",
            "date_buckets": buckets,
        }
    )
    bank = plugin._presentation_bank(settings, DeviceConfig().get_resolution(), date(2026, 7, 12))

    document, _profile = bank.load_for_data()
    bank.save(document)

    retained = _state_json(plugin)["date_buckets"]
    assert len(retained) == 366
    assert set(retained) == {f"legacy-{index:03d}" for index in range(4, 370)}


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example/api/issue/1/",
        "https://www.comics.org.evil.example/api/issue/1/",
        "https://user:pass@www.comics.org/api/issue/1/",
        "https://www.comics.org:8443/api/issue/1/",
        "ftp://www.comics.org/api/issue/1/",
    ],
)
def test_gcd_provider_authority_rejects_foreign_credentials_ports_and_schemes(url):
    with pytest.raises(RuntimeError, match="authority|approved"):
        _canonical_provider_url(url, "gcd")


def test_gcd_provider_authority_canonicalizes_approved_http_to_https():
    assert _canonical_provider_url(
        "http://www.comics.org/api/issue/1/?page=2#fragment",
        "gcd",
    ) == "https://www.comics.org/api/issue/1/?page=2"


def test_candidate_order_prefers_exact_day_before_month_fallback(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    today = date(2026, 5, 30)
    candidates = [
        {"issue_id": "1", "match_quality": "month_fallback"},
        {"issue_id": "2", "match_quality": "exact_day"},
        {"issue_id": "3", "match_quality": "month_fallback"},
    ]

    ordered = plugin._candidate_order(candidates, {"version": "gcd-comic-covers-state-v1", "date_buckets": {}}, today)

    assert ordered[0]["issue_id"] == "2"


def test_candidate_order_keeps_month_fallback_after_exact_day(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    today = date(2026, 5, 30)
    candidates = [
        {"issue_id": "1", "match_quality": "month_fallback"},
        {"issue_id": "2", "match_quality": "exact_day"},
        {"issue_id": "3", "match_quality": "month_fallback"},
    ]

    ordered = plugin._candidate_order(candidates, {"version": "gcd-comic-covers-state-v1", "date_buckets": {}}, today)

    assert ordered[0]["issue_id"] == "2"
    assert {item["issue_id"] for item in ordered[1:]} == {"1", "3"}


def test_candidate_order_uses_month_when_exact_day_is_in_waste_pit(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    today = date(2026, 5, 30)
    state = {
        "version": "gcd-comic-covers-state-v1",
        "date_buckets": {"05-30": {"seen_issue_ids": ["2"]}},
    }
    candidates = [
        {"issue_id": "1", "match_quality": "month_fallback"},
        {"issue_id": "2", "match_quality": "exact_day"},
    ]

    ordered = plugin._candidate_order(candidates, state, today)

    assert ordered[0]["issue_id"] == "1"


def test_waste_pit_uses_issue_id_so_variants_can_each_display(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    today = date(2026, 5, 30)
    state = {"version": "gcd-comic-covers-state-v1", "date_buckets": {}}
    variant_a = {"issue_id": "10", "match_quality": "exact_day"}
    variant_b = {"issue_id": "11", "match_quality": "exact_day"}

    plugin._mark_seen(state, today, {"issue_id": "10"})
    ordered = plugin._candidate_order([variant_a, variant_b], state, today)

    assert [item["issue_id"] for item in ordered] == ["11"]


def test_candidate_order_tries_candidates_with_cover_urls_first(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    today = date(2026, 5, 30)
    candidates = [
        {"issue_id": "1", "match_quality": "exact_day"},
        {"issue_id": "2", "match_quality": "exact_day", "cover_url": "https://example.com/cover.png"},
    ]

    ordered = plugin._candidate_order(candidates, {"version": "gcd-comic-covers-state-v1", "date_buckets": {}}, today)

    assert ordered[0]["issue_id"] == "2"


def test_candidate_order_prefers_comic_vine_recent_before_gcd_exact(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    today = date(2026, 6, 3)
    candidates = [
        {"source": "gcd", "issue_id": "1", "match_quality": "exact_day", "cover_url": "https://example.com/gcd.jpg"},
        {"source": "comicvine", "issue_id": "comicvine:2", "match_quality": "comicvine_recent", "cover_url": "https://example.com/cv.jpg"},
    ]

    ordered = plugin._candidate_order(candidates, {"version": "gcd-comic-covers-state-v1", "date_buckets": {}}, today)

    assert ordered[0]["issue_id"] == "comicvine:2"


def test_candidate_pool_defaults_to_comic_vine_with_gcd_fallback(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    today = date(2026, 6, 4)
    monkeypatch.setattr(
        plugin,
        "_gcd_candidate_pool",
        lambda _settings, _today: [{
            "source": "gcd",
            "issue_id": "gcd:1",
            "match_quality": "exact_day",
            "cover_url": "https://example.com/gcd.jpg",
        }],
    )
    monkeypatch.setattr(
        plugin,
        "_comic_vine_candidate_pool",
        lambda _settings, _today: [{
            "source": "comicvine",
            "issue_id": "comicvine:2",
            "match_quality": "comicvine_recent",
            "cover_url": "https://example.com/cv.jpg",
        }],
    )

    candidates = plugin._candidate_pool({}, today)

    assert [candidate["issue_id"] for candidate in candidates] == ["comicvine:2", "gcd:1"]


def test_candidate_order_recycles_comic_vine_before_gcd_when_priority_seen(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    today = date(2026, 6, 4)
    state = {
        "version": "gcd-comic-covers-state-v1",
        "date_buckets": {
            "06-04": {
                "seen_issue_ids": ["comicvine:1", "comicvine:2"],
                "last_issue_id": "comicvine:2",
            },
        },
    }
    candidates = [
        {"source": "comicvine", "issue_id": "comicvine:1", "match_quality": "comicvine_recent", "cover_url": "https://example.com/cv1.jpg"},
        {"source": "comicvine", "issue_id": "comicvine:2", "match_quality": "comicvine_recent", "cover_url": "https://example.com/cv2.jpg"},
        {"source": "gcd", "issue_id": "gcd:1", "match_quality": "exact_day", "cover_url": "https://example.com/gcd.jpg"},
    ]

    ordered = plugin._candidate_order(candidates, state, today)

    assert ordered[0]["issue_id"] == "comicvine:1"


def test_waste_pit_resets_after_pool_is_exhausted(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    today = date(2026, 5, 30)
    state = {
        "version": "gcd-comic-covers-state-v1",
        "date_buckets": {"05-30": {"seen_issue_ids": ["10", "11"], "last_issue_id": "11"}},
    }
    candidates = [
        {"issue_id": "10", "match_quality": "exact_day"},
        {"issue_id": "11", "match_quality": "exact_day"},
    ]

    ordered = plugin._candidate_order(candidates, state, today)

    assert {item["issue_id"] for item in ordered} == {"10", "11"}
    assert state["date_buckets"]["05-30"]["seen_issue_ids"] == ["10", "11"]


def test_filter_candidates_accepts_exact_day_and_month_only(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    today = date(2026, 5, 30)
    candidates = [
        {"issue_id": "1", "country": "United States", "on_sale_date": "1942-05-30"},
        {"issue_id": "2", "country": "us", "on_sale_date": "1942-05"},
        {"issue_id": "3", "country": "us", "on_sale_date": "1942-06-30"},
        {"issue_id": "4", "country": "us", "on_sale_date": "2026-05-31"},
        {"issue_id": "5", "country": "us", "on_sale_date": "2026-05-30"},
    ]

    filtered = plugin._filter_candidates(candidates, {"countryCodes": "us"}, today)

    assert [(item["issue_id"], item["match_quality"]) for item in filtered] == [
        ("1", "exact_day"),
        ("2", "month_fallback"),
        ("5", "exact_day"),
    ]


def test_default_year_range_runs_to_current_year(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)

    years = plugin._target_years({}, date(2026, 5, 30))

    assert years[0] == 1938
    assert years[-1] == 2026


def test_candidate_pool_fetches_current_year_first_and_pauses_backfill(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    today = date(2026, 5, 30)
    fetched_years = []

    monkeypatch.setattr(plugin, "_target_years", lambda settings, current_date: [2024, 2025, 2026])
    monkeypatch.setattr(plugin, "_read_month_cache", lambda year, month, day=None: None)
    monkeypatch.setattr(plugin, "_write_month_cache", lambda year, month, candidates, day=None: None)

    def fake_fetch(year, month, day=None):
        fetched_years.append(year)
        return [
            {"issue_id": f"{year}-{index}", "country": "us", "on_sale_date": f"{year:04d}-05-30"}
            for index in range(130)
        ]

    monkeypatch.setattr(plugin, "_fetch_month_candidates", fake_fetch)

    candidates = plugin._candidate_pool({"maxYearsPerRefresh": "10"}, today)

    assert fetched_years == [2026]
    assert len(candidates) == 130


def test_monthly_html_parser_extracts_issue_id_date_and_cover():
    parser = _GcdMonthlyParser("https://www.comics.org/on_sale_monthly/1942/month/5/")
    parser.feed(
        """
        <table>
          <tr>
            <td><img src="/covers/preview/abc.jpg" alt="preview"></td>
            <td><img src="/flags/us.png" alt="United States"></td>
            <td><a href="/issue/12345/">Captain Example #7</a></td>
            <td>1942-05-30</td>
          </tr>
        </table>
        """
    )
    plugin = GcdComicCovers({"id": "gcd_comic_covers"})

    candidates = []
    for row in parser.rows:
        candidates.append({
            "issue_id": row["issue_id"],
            "country": row["country"],
            "on_sale_date": plugin._date_from_text(" ".join(row["text"]), 1942, 5),
            "cover_url": row["cover_url"],
        })

    assert candidates == [{
        "issue_id": "12345",
        "country": "us",
        "on_sale_date": "1942-05-30",
        "cover_url": "https://www.comics.org/covers/preview/abc.jpg",
    }]


def test_day_candidate_fetch_uses_weekly_api(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    target = date(2026, 5, 29)
    iso_year, iso_week, _weekday = target.isocalendar()
    calls = []

    def fake_fetch_json(url):
        calls.append(url)
        if len(calls) == 1:
            return {
                "results": [{
                    "api_url": "https://www.comics.org/api/issue/256114/",
                    "series_name": "Captain Example",
                    "descriptor": "7",
                    "publication_date": "May 2026",
                }],
                "next": "https://www.comics.org/api/issue/on_sale_weekly/2026/week/22?page=2",
            }
        return {
            "results": [{
                "api_url": "https://www.comics.org/api/issue/256115/",
                "series_name": "Second Example",
                "descriptor": "8",
            }],
            "next": None,
        }

    monkeypatch.setattr(plugin, "_fetch_json", fake_fetch_json)

    candidates = plugin._fetch_month_candidates(target.year, target.month, target.day)

    assert f"/api/issue/on_sale_weekly/{iso_year}/week/{iso_week}" in calls[0]
    assert [candidate["issue_id"] for candidate in candidates] == ["256114", "256115"]
    assert {candidate["target_date"] for candidate in candidates} == {"2026-05-29"}


def test_cover_url_normalization_removes_duplicate_path_slashes(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)

    url = plugin._normalize_cover_url("https://files1.comics.org//img/gcd/covers_by_id/48/w400/48980.jpg")

    assert url == "https://files1.comics.org/img/gcd/covers_by_id/48/w400/48980.jpg"


def test_generate_image_uses_metadata_cover_when_source_image_is_blocked(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    plugin.image_loader = MissingImageLoader()
    monkeypatch.setattr(plugin, "_current_date", lambda _device_config: date(2026, 5, 30))
    monkeypatch.setattr(
        plugin,
        "_candidate_pool",
        lambda _settings, _today: [{
            "issue_id": "28815",
            "country": "us",
            "on_sale_date": "1975-05-30",
            "match_quality": "exact_day",
        }],
    )
    monkeypatch.setattr(
        plugin,
        "_issue_detail",
        lambda _candidate: {
            "issue_id": "28815",
            "series_name": "Tales of Evil",
            "issue_number": "3",
            "publisher": "Atlas Comics",
            "on_sale_date": "1975-05-30",
            "cover_url": "https://files1.comics.org//img/gcd/covers_by_id/48/w400/48980.jpg",
            "cover_credits": "Pencils: Rich Buckler; Inks: Rich Buckler",
        },
    )
    monkeypatch.setattr("plugins.gcd_comic_covers.gcd_comic_covers.write_context", lambda *args, **kwargs: None)

    image = plugin.generate_image({"maxCoverAttempts": "1"}, DeviceConfig())

    assert image.size == (800, 480)
    assert image.getbbox() is not None


def test_generate_image_limits_blocked_cover_attempts(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    plugin.image_loader = MissingImageLoader()
    attempted = []
    monkeypatch.setattr(plugin, "_current_date", lambda _device_config: date(2026, 5, 30))
    monkeypatch.setattr(
        plugin,
        "_candidate_pool",
        lambda _settings, _today: [
            {"issue_id": str(index), "country": "us", "on_sale_date": "1975-05-30", "match_quality": "exact_day"}
            for index in range(6)
        ],
    )

    def fake_issue_detail(candidate):
        attempted.append(candidate["issue_id"])
        return {
            "issue_id": candidate["issue_id"],
            "series_name": "Blocked Example",
            "issue_number": candidate["issue_id"],
            "country": "us",
            "on_sale_date": "1975-05-30",
            "cover_url": f"https://files1.comics.org/img/gcd/covers_by_id/0/w400/{candidate['issue_id']}.jpg",
        }

    monkeypatch.setattr(plugin, "_issue_detail", fake_issue_detail)
    monkeypatch.setattr("plugins.gcd_comic_covers.gcd_comic_covers.write_context", lambda *args, **kwargs: None)

    plugin.generate_image({"maxCoverAttempts": "2"}, DeviceConfig())

    assert len(attempted) == 2
    assert set(attempted).issubset({str(index) for index in range(6)})


def test_generate_image_uses_candidate_metadata_when_issue_detail_is_rate_limited(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    monkeypatch.setattr(plugin, "_current_date", lambda _device_config: date(2026, 5, 30))
    monkeypatch.setattr(
        plugin,
        "_candidate_pool",
        lambda _settings, _today: [{
            "issue_id": "rate-limited",
            "series_name": "Candidate Only",
            "issue_number": "12",
            "publisher": "Example Publisher",
            "on_sale_date": "1975-05-30",
            "match_quality": "exact_day",
        }],
    )
    monkeypatch.setattr(plugin, "_load_cover", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("429 Too Many Requests")))
    monkeypatch.setattr("plugins.gcd_comic_covers.gcd_comic_covers.write_context", lambda *args, **kwargs: None)

    image = plugin.generate_image({"maxCoverAttempts": "1"}, DeviceConfig())

    assert image.size == (800, 480)
    assert image.getbbox() is not None


def test_generate_image_defaults_to_plain_triptych_without_mutating_seen_state(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    monkeypatch.setattr(plugin, "_current_date", lambda _device_config: date(2026, 5, 30))
    monkeypatch.setattr(
        plugin,
        "_candidate_pool",
        lambda _settings, _today: [
            {"issue_id": "1", "country": "us", "on_sale_date": "1975-05-30", "match_quality": "exact_day"},
            {"issue_id": "2", "country": "us", "on_sale_date": "1975-05-30", "match_quality": "exact_day"},
            {"issue_id": "3", "country": "us", "on_sale_date": "1975-05-30", "match_quality": "exact_day"},
        ],
    )
    monkeypatch.setattr(plugin, "_candidate_order", lambda candidates, _state, _today: candidates)
    colors = {
        "1": (220, 0, 0),
        "2": (0, 160, 0),
        "3": (0, 0, 220),
    }

    def fake_load_cover(candidate, _dimensions, _settings):
        issue_id = candidate["issue_id"]
        return {
            **candidate,
            "series_name": f"Series {issue_id}",
            "issue_number": issue_id,
            "cover_url": f"https://example.com/{issue_id}.jpg",
            "date_label": "1975-05-30",
            "image": Image.new("RGB", (200, 400), colors[issue_id]),
        }

    monkeypatch.setattr(plugin, "_load_cover", fake_load_cover)
    monkeypatch.setattr("plugins.gcd_comic_covers.gcd_comic_covers.write_context", lambda *args, **kwargs: None)

    image = plugin.generate_image({}, DeviceConfig())
    state = plugin._read_state()

    assert image.size == (800, 480)
    assert image.getpixel((133, 240)) == colors["1"]
    assert image.getpixel((399, 240)) == colors["2"]
    assert image.getpixel((666, 240)) == colors["3"]
    assert state.get("date_buckets", {}).get("05-30", {}).get("seen_issue_ids", []) == []


def test_triptych_generation_prefers_portrait_covers_over_wide_strips(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    today = date(2026, 5, 30)
    candidates = [
        {"issue_id": "wide"},
        {"issue_id": "red"},
        {"issue_id": "green"},
        {"issue_id": "blue"},
    ]
    colors = {
        "wide": (230, 180, 0),
        "red": (220, 0, 0),
        "green": (0, 160, 0),
        "blue": (0, 0, 220),
    }

    def fake_load_cover(candidate, _dimensions, _settings):
        issue_id = candidate["issue_id"]
        size = (500, 160) if issue_id == "wide" else (200, 400)
        return {
            **candidate,
            "series_name": issue_id,
            "issue_number": "1",
            "date_label": "1975-05-30",
            "cover_url": f"https://example.com/{issue_id}.jpg",
            "image": Image.new("RGB", size, colors[issue_id]),
        }

    monkeypatch.setattr(plugin, "_load_cover", fake_load_cover)
    monkeypatch.setattr("plugins.gcd_comic_covers.gcd_comic_covers.write_context", lambda *args, **kwargs: None)

    image = plugin._generate_triptych_image(candidates, {}, today, (800, 480), {"backgroundColor": "white"}, 4)
    state = plugin._read_state()

    assert image.getpixel((133, 240)) == colors["red"]
    assert image.getpixel((399, 240)) == colors["green"]
    assert image.getpixel((666, 240)) == colors["blue"]
    assert state["date_buckets"]["05-30"]["seen_issue_ids"] == ["red", "green", "blue"]


def test_triptych_mode_renders_available_cover_without_info_label(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    cover = {
        "series_name": "Label Should Not Render",
        "issue_number": "9",
        "date_label": "1975-05-30",
        "image": Image.new("RGB", (200, 400), (220, 0, 0)),
    }

    image = plugin._compose_triptych_display_image([cover], (800, 480), {"backgroundColor": "white"})

    assert image.size == (800, 480)
    assert image.getpixel((399, 240)) == (220, 0, 0)
    assert image.getpixel((20, 460)) != (255, 255, 255)


def test_triptych_mode_expands_two_covers_to_fill_screen_width(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    covers = [
        {"image": Image.new("RGB", (200, 400), (220, 0, 0))},
        {"image": Image.new("RGB", (200, 400), (0, 0, 220))},
    ]

    image = plugin._compose_triptych_display_image(covers, (800, 480), {"backgroundColor": "white"})

    assert image.size == (800, 480)
    assert image.getpixel((100, 240)) == (220, 0, 0)
    assert image.getpixel((700, 240)) == (0, 0, 220)


def test_date_cache_path_is_day_scoped(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)

    assert plugin._month_cache_path(2026, 5, 29).as_posix().endswith("/dates/2026-05-29.json")


def test_validate_detail_date_accepts_month_fallback_and_rejects_other_month(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    candidate = {"target_date": "2026-05-29"}

    plugin._validate_detail_date({"on_sale_date": "2026-05-29"}, candidate)
    plugin._validate_detail_date({"on_sale_date": "2026-05-01"}, candidate)

    with pytest.raises(RuntimeError):
        plugin._validate_detail_date({"on_sale_date": "2026-06-01"}, candidate)


def test_validate_detail_date_skips_comic_vine_recent_candidates(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    candidate = {
        "source": "comicvine",
        "match_quality": "comicvine_recent",
        "target_date": "2026-06-03",
    }

    plugin._validate_detail_date({"source": "comicvine", "on_sale_date": "2023-07-21"}, candidate)


def test_default_fit_mode_rotates_portrait_cover_counterclockwise(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    portrait = Image.new("RGB", (320, 480), "blue")
    for y in range(480):
        for x in range(160, 320):
            portrait.putpixel((x, y), (255, 0, 0))

    image = plugin._fit_cover(
        portrait,
        (800, 480),
        {"backgroundStyle": "plain", "backgroundColor": "white", "showInfoLabel": "false"},
        {},
    )

    assert image.getpixel((0, 0)) == (255, 0, 0)
    assert image.getpixel((799, 0)) == (255, 0, 0)
    assert image.getpixel((0, 479)) == (0, 0, 255)


def test_comic_vine_recent_candidates_normalize_issue_and_image(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    today = date(2026, 6, 3)

    def fake_comic_vine_get(path, api_key, params):
        assert path == "issues/"
        assert api_key == "secret"
        assert params["sort"] == "date_added:desc"
        return {
            "status_code": 1,
            "results": [{
                "id": 123,
                "api_detail_url": "https://comicvine.gamespot.com/api/issue/4000-123/",
                "site_detail_url": "https://comicvine.gamespot.com/example/",
                "name": "The Test Issue",
                "issue_number": "7",
                "cover_date": "2026-06-01",
                "store_date": "2026-06-03",
                "date_added": "2026-06-03 12:30:00",
                "volume": {"name": "Test Volume"},
                "image": {"super_url": "https://comicvine.gamespot.com/a/uploads/scale_large/1/123.jpg"},
            }],
        }

    monkeypatch.setattr(plugin, "_comic_vine_get", fake_comic_vine_get)

    candidates = plugin._fetch_comic_vine_recent_candidates("secret", today, 10)

    assert candidates == [{
        "source": "comicvine",
        "source_label": "Comic Vine",
        "issue_id": "comicvine:123",
        "comic_vine_id": "123",
        "series_name": "Test Volume",
        "issue_number": "7",
        "title": "The Test Issue",
        "publisher": "",
        "country": "",
        "language": "",
        "on_sale_date": "2026-06-03",
        "store_date": "2026-06-03",
        "cover_date": "2026-06-01",
        "date_added": "2026-06-03 12:30:00",
        "cover_url": "https://comicvine.gamespot.com/a/uploads/scale_large/1/123.jpg",
        "page_url": "https://comicvine.gamespot.com/example/",
        "api_url": "https://comicvine.gamespot.com/api/issue/4000-123/",
        "target_date": "2026-06-03",
        "year": 2026,
        "match_quality": "comicvine_recent",
    }]


def test_mixed_source_mode_prepends_comic_vine_candidates(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)
    today = date(2026, 6, 3)
    monkeypatch.setattr(plugin, "_gcd_candidate_pool", lambda _settings, _today: [{"source": "gcd", "issue_id": "1"}])
    monkeypatch.setattr(plugin, "_comic_vine_candidate_pool", lambda _settings, _today: [{"source": "comicvine", "issue_id": "comicvine:2"}])

    candidates = plugin._candidate_pool({"sourceMode": "mixed"}, today)

    assert [candidate["issue_id"] for candidate in candidates] == ["comicvine:2", "1"]


def test_source_mode_accepts_settings_html_comicvine_value(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)

    assert plugin._source_mode({"sourceMode": "comicvine"}) == "comicvine"


def test_comic_vine_issue_cache_path_is_windows_safe(tmp_path, monkeypatch):
    plugin = make_plugin(tmp_path, monkeypatch)

    path = plugin._issue_cache_path("comicvine:123")

    assert "comicvine_123" in path.name
    assert ":" not in path.name
