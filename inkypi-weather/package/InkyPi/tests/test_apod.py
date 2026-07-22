import hashlib
import json
import random
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.apod import apod as apod_module  # noqa: E402
from plugins.apod.apod import Apod  # noqa: E402
from runtime.long_task_executor import InstanceIdentity  # noqa: E402


@pytest.fixture
def apod_storage(monkeypatch, tmp_path):
    monkeypatch.setenv("INKYPI_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("INKYPI_DATA_DIR", str(tmp_path / "data"))
    return Apod({"id": "apod"})


def _identity(uuid, generation, revision=1):
    return InstanceIdentity(uuid, generation, revision)


def test_instance_namespace_uses_uuid_and_generation_not_settings_revision(
    monkeypatch, apod_storage
):
    monkeypatch.setattr(
        apod_module,
        "current_instance_identity",
        lambda: _identity("4f83c7ef-0e5a-4df8-bbe1-62d14f9ef531", 7, 1),
    )
    first = apod_module._instance_paths(apod_storage)

    monkeypatch.setattr(
        apod_module,
        "current_instance_identity",
        lambda: _identity("4f83c7ef-0e5a-4df8-bbe1-62d14f9ef531", 7, 99),
    )
    revised = apod_module._instance_paths(apod_storage)

    expected = hashlib.sha256(
        b"4f83c7ef-0e5a-4df8-bbe1-62d14f9ef531:7"
    ).hexdigest()
    assert first.identity_key == expected
    assert first == revised
    assert first.cache.is_dir()
    assert first.data.is_dir()
    assert first.media.is_dir()


def test_instance_namespace_separates_uuid_and_generation(monkeypatch, apod_storage):
    monkeypatch.setattr(
        apod_module,
        "current_instance_identity",
        lambda: _identity("4f83c7ef-0e5a-4df8-bbe1-62d14f9ef531", 7),
    )
    first = apod_module._instance_paths(apod_storage)

    monkeypatch.setattr(
        apod_module,
        "current_instance_identity",
        lambda: _identity("a2e3b8e3-6eb0-4d31-a7bc-0d0b9beb4737", 7),
    )
    other_uuid = apod_module._instance_paths(apod_storage)
    monkeypatch.setattr(
        apod_module,
        "current_instance_identity",
        lambda: _identity("4f83c7ef-0e5a-4df8-bbe1-62d14f9ef531", 8),
    )
    other_generation = apod_module._instance_paths(apod_storage)

    assert first.cache != other_uuid.cache != other_generation.cache
    assert first.data != other_uuid.data != other_generation.data


@pytest.mark.parametrize("identity", [None, _identity(None, 7), _identity("uuid", None)])
def test_instance_namespace_fails_closed_without_runtime_identity(
    monkeypatch, apod_storage, identity
):
    monkeypatch.setattr(apod_module, "current_instance_identity", lambda: identity)

    with pytest.raises(RuntimeError, match="instance identity"):
        apod_module._instance_paths(apod_storage)


def test_instance_namespace_allows_explicit_preview_namespace_without_identity(
    monkeypatch, apod_storage
):
    monkeypatch.setattr(apod_module, "current_instance_identity", lambda: None)

    paths = apod_module._instance_paths(apod_storage, preview_namespace="test-preview")

    assert paths.identity_key.startswith("preview-")
    assert paths.cache.is_dir()
    assert paths.data.is_dir()
    assert paths.media.is_dir()


@pytest.fixture
def selection_paths(apod_storage):
    return apod_module._instance_paths(apod_storage, preview_namespace="selection")


def test_selection_today_persists_the_device_day(selection_paths):
    selection = apod_module._resolve_selection(
        settings={},
        device_day=date(2026, 7, 22),
        paths=selection_paths,
        rng=random.Random(11),
    )

    assert selection.mode == "today"
    assert selection.device_day == "2026-07-22"
    assert selection.requested_date == "2026-07-22"
    assert selection.resolved_record_date == "2026-07-22"
    assert selection.provisional is False


def test_selection_custom_date_persists_exact_requested_date(selection_paths):
    selection = apod_module._resolve_selection(
        settings={"customDate": "2024-05-07"},
        device_day=date(2026, 7, 22),
        paths=selection_paths,
        rng=random.Random(11),
    )

    assert selection.mode == "custom"
    assert selection.requested_date == "2024-05-07"
    assert selection.resolved_record_date == "2024-05-07"


def test_selection_compatibility_random_mode_is_stable_for_the_day(selection_paths):
    first = apod_module._resolve_selection(
        settings={"randomizeApod": "true"},
        device_day=date(2026, 7, 22),
        paths=selection_paths,
        rng=random.Random(17),
    )
    second = apod_module._resolve_selection(
        settings={"randomizeApod": "true"},
        device_day=date(2026, 7, 22),
        paths=selection_paths,
        rng=object(),
    )

    assert first.mode == "random"
    assert date(2015, 1, 1) <= date.fromisoformat(first.requested_date) <= date(2026, 7, 22)
    assert first == second
    assert first.provisional is True


def test_selection_manual_force_repeat_reuses_existing_selection(selection_paths):
    first = apod_module._resolve_selection(
        settings={"randomizeApod": "true"},
        device_day=date(2026, 7, 22),
        paths=selection_paths,
        rng=random.Random(9),
    )
    forced = apod_module._resolve_selection(
        settings={"randomizeApod": "true", "forceRefresh": "true"},
        device_day=date(2026, 7, 22),
        paths=selection_paths,
        rng=object(),
    )

    assert forced == first


def test_selection_random_mode_rerolls_on_next_device_day(selection_paths):
    first = apod_module._resolve_selection(
        settings={"randomizeApod": "true"},
        device_day=date(2026, 7, 22),
        paths=selection_paths,
        rng=random.Random(3),
    )
    next_day = apod_module._resolve_selection(
        settings={"randomizeApod": "true"},
        device_day=date(2026, 7, 23),
        paths=selection_paths,
        rng=random.Random(4),
    )

    assert next_day.device_day == "2026-07-23"
    assert next_day.fingerprint != first.fingerprint


def test_selection_fingerprint_invalidates_mode_requested_and_custom_date():
    device_day = date(2026, 7, 22)
    today = apod_module._selection_fingerprint(
        mode="today", device_day=device_day, requested_date="2026-07-22", custom_date=""
    )
    random_mode = apod_module._selection_fingerprint(
        mode="random", device_day=device_day, requested_date="2026-07-22", custom_date=""
    )
    requested = apod_module._selection_fingerprint(
        mode="today", device_day=device_day, requested_date="2026-07-21", custom_date=""
    )
    custom = apod_module._selection_fingerprint(
        mode="custom", device_day=device_day, requested_date="2024-05-07", custom_date="2024-05-07"
    )

    assert len({today, random_mode, requested, custom}) == 4


def test_selection_json_is_atomic_and_contains_selection_contract(selection_paths):
    selection = apod_module._resolve_selection(
        settings={"randomizeApod": "true"},
        device_day=date(2026, 7, 22),
        paths=selection_paths,
        rng=random.Random(7),
    )

    persisted = json.loads((selection_paths.data / "selection.json").read_text("utf-8"))
    assert persisted == {
        "device_day": "2026-07-22",
        "mode": "random",
        "requested_date": selection.requested_date,
        "selected_apod_date": selection.resolved_record_date,
        "selection_fingerprint": selection.fingerprint,
        "provisional": True,
        "record_cache_key": selection.record_cache_key,
    }
    assert not list(selection_paths.data.glob("selection.json.*.tmp"))


def test_selection_settings_revision_changes_do_not_change_namespace_or_reroll(
    monkeypatch, apod_storage
):
    monkeypatch.setattr(
        apod_module,
        "current_instance_identity",
        lambda: _identity("4f83c7ef-0e5a-4df8-bbe1-62d14f9ef531", 7, 1),
    )
    first_paths = apod_module._instance_paths(apod_storage)
    first = apod_module._resolve_selection(
        settings={"randomizeApod": "true"},
        device_day=date(2026, 7, 22),
        paths=first_paths,
        rng=random.Random(2),
    )
    monkeypatch.setattr(
        apod_module,
        "current_instance_identity",
        lambda: _identity("4f83c7ef-0e5a-4df8-bbe1-62d14f9ef531", 7, 2),
    )
    revised_paths = apod_module._instance_paths(apod_storage)
    revised = apod_module._resolve_selection(
        settings={"randomizeApod": "true"},
        device_day=date(2026, 7, 22),
        paths=revised_paths,
        rng=object(),
    )

    assert revised_paths == first_paths
    assert revised == first
