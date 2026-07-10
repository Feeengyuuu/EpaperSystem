import json
import math
import os
import stat
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


def _config(label="base"):
    return {
        "name": label,
        "resolution": [800, 480],
        "nested": {"items": [1, {"enabled": True}]},
    }


def _instance(
    instance_uuid="11111111111111111111111111111111",
    *,
    refresh=None,
    structural_generation=1,
    settings_revision=1,
):
    return {
        "plugin_id": "clock",
        "name": "Clock",
        "plugin_settings": {},
        "refresh": {"interval": 60} if refresh is None else refresh,
        "latest_refresh_time": None,
        "instance_uuid": instance_uuid,
        "structural_generation": structural_generation,
        "settings_revision": settings_revision,
    }


def _config_with_instances(*instances, label="base"):
    value = _config(label)
    value["playlist_config"] = {
        "playlists": [
            {
                "name": "Default",
                "start_time": "00:00",
                "end_time": "24:00",
                "plugins": list(instances),
                "current_plugin_index": None,
            }
        ],
        "active_playlist": "Default",
    }
    return value


def _write(path: Path, payload, *, bom=False):
    encoded = json.dumps(payload, allow_nan=False).encode("utf-8")
    path.write_bytes((b"\xef\xbb\xbf" if bom else b"") + encoded)


def _versioned(payload, revision):
    return {**payload, "schema_version": 1, "config_revision": revision}


def _store(path):
    from src.config_store import ConfigStore

    return ConfigStore(path)


def test_commit_strictly_freezes_detached_json_and_persists_owned_metadata(tmp_path):
    path = tmp_path / "device.json"
    _write(path, _config("legacy"))
    store = _store(path)
    store.load()
    candidate = _config("next")
    candidate["tuple_value"] = ("a", {"count": 2})

    snapshot = store.commit(0, candidate)
    candidate["name"] = "mutated"
    candidate["nested"]["items"][1]["enabled"] = False
    candidate["tuple_value"][1]["count"] = 99

    assert snapshot.version == 1
    assert snapshot.data["name"] == "next"
    assert snapshot.data["nested"]["items"][1]["enabled"] is True
    assert snapshot.data["tuple_value"] == ("a", {"count": 2})
    assert store.current().snapshot is snapshot
    assert store.current().status is store.status()
    with pytest.raises(TypeError):
        snapshot.data["name"] = "forbidden"
    with pytest.raises(TypeError):
        snapshot.data["nested"]["items"][1]["enabled"] = False
    with pytest.raises(AttributeError):
        snapshot.data["nested"]["items"].append("forbidden")

    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted["schema_version"] == 1
    assert persisted["config_revision"] == 1
    assert persisted["tuple_value"] == ["a", {"count": 2}]


class _Custom:
    pass


class _IntSubclass(int):
    pass


@pytest.mark.parametrize(
    "bad_value",
    [
        b"bytes",
        {"set"},
        _Custom(),
        _IntSubclass(2),
        math.nan,
        math.inf,
    ],
)
def test_commit_rejects_non_json_values_without_touching_disk(tmp_path, bad_value):
    from src.config_store import ConfigValidationError

    path = tmp_path / "device.json"
    original = _config("legacy")
    _write(path, original)
    store = _store(path)
    store.load()
    candidate = _config("invalid")
    candidate["bad"] = bad_value

    with pytest.raises(ConfigValidationError):
        store.commit(0, candidate)

    assert json.loads(path.read_text(encoding="utf-8")) == original
    assert store.snapshot().version == 0


def test_commit_rejects_non_string_keys_and_cycles(tmp_path):
    from src.config_store import ConfigValidationError

    path = tmp_path / "device.json"
    _write(path, _config())
    store = _store(path)
    store.load()

    non_string_key = _config()
    non_string_key["bad"] = {1: "coerced"}
    with pytest.raises(ConfigValidationError, match="keys"):
        store.commit(0, non_string_key)

    cyclic = _config()
    cyclic["cycle"] = cyclic
    with pytest.raises(ConfigValidationError, match="cycle"):
        store.commit(0, cyclic)


@pytest.mark.parametrize("bad_string", ["\ud800", "\udfff"])
def test_commit_rejects_non_utf8_unicode_scalars_before_persistence(tmp_path, bad_string):
    from src.config_store import ConfigValidationError

    path = tmp_path / "device.json"
    _write(path, _config())
    store = _store(path)
    store.load()
    bad_value = _config()
    bad_value["bad"] = bad_string
    with pytest.raises(ConfigValidationError, match="UTF-8"):
        store.commit(0, bad_value)

    bad_key = _config()
    bad_key[bad_string] = "value"
    with pytest.raises(ConfigValidationError, match="UTF-8"):
        store.commit(0, bad_key)


def test_missing_resolution_fails_schema_validation_before_persistence(tmp_path, monkeypatch):
    from src import config_store

    path = tmp_path / "device.json"
    _write(path, _config())
    store = _store(path)
    store.load()
    monkeypatch.setattr(
        config_store,
        "atomic_write_json",
        lambda *_args, **_kwargs: pytest.fail("persistence must not be attempted"),
    )

    with pytest.raises(config_store.ConfigValidationError, match="resolution"):
        store.commit(0, {"name": "toy"})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("display_type", ""),
        ("display_type", None),
        ("orientation", "diagonal"),
        ("scheduler_sleep_time", True),
        ("scheduler_sleep_time", 0),
        ("plugin_cycle_interval_seconds", "60"),
        ("plugin_cycle_interval_seconds", -1),
        ("image_settings", []),
    ],
)
def test_v1_commit_rejects_invalid_optional_root_fields(tmp_path, field, value):
    from src.config_store import ConfigValidationError

    path = tmp_path / "device.json"
    _write(path, _config())
    store = _store(path)
    store.load()
    candidate = _config()
    candidate[field] = value

    with pytest.raises(ConfigValidationError):
        store.commit(0, candidate)


@pytest.mark.parametrize("bad_value", [True, "1.0", None])
def test_v1_commit_rejects_non_numeric_image_adjustments(tmp_path, bad_value):
    from src.config_store import ConfigValidationError

    path = tmp_path / "device.json"
    _write(path, _config())
    store = _store(path)
    store.load()
    candidate = _config()
    candidate["image_settings"] = {"contrast": bad_value, "future_setting": "allowed"}

    with pytest.raises(ConfigValidationError):
        store.commit(0, candidate)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value["playlist_config"]["playlists"][0].update(name=""),
        lambda value: value["playlist_config"]["playlists"].append(
            dict(value["playlist_config"]["playlists"][0])
        ),
        lambda value: value["playlist_config"]["playlists"][0].update(
            start_time="24:00"
        ),
        lambda value: value["playlist_config"]["playlists"][0].update(
            end_time="24:01"
        ),
        lambda value: value["playlist_config"].update(active_playlist="missing"),
        lambda value: value["playlist_config"]["playlists"][0].update(
            current_plugin_index=True
        ),
        lambda value: value["playlist_config"]["playlists"][0].update(
            plugin_rotation_queue={}
        ),
        lambda value: value["playlist_config"]["playlists"][0].update(
            plugin_rotation_pool="not-an-array"
        ),
        lambda value: value["playlist_config"]["playlists"][0].update(
            plugin_rotation_recent_history=None
        ),
    ],
    ids=[
        "empty-name",
        "duplicate-name",
        "invalid-start",
        "invalid-end",
        "unknown-active",
        "boolean-index",
        "queue-type",
        "pool-type",
        "history-type",
    ],
)
def test_v1_commit_rejects_invalid_playlist_runtime_shape(tmp_path, mutate):
    from src.config_store import ConfigValidationError

    path = tmp_path / "device.json"
    _write(path, _config())
    store = _store(path)
    store.load()
    candidate = _config_with_instances()
    mutate(candidate)

    with pytest.raises(ConfigValidationError):
        store.commit(0, candidate)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda item: item.update(instance_uuid="not-a-uuid"),
        lambda item: item.update(structural_generation=0),
        lambda item: item.update(settings_revision=True),
        lambda item: item.update(refresh={"interval": 0}),
        lambda item: item.update(refresh={"scheduled": "25:00"}),
    ],
)
def test_v1_commit_rejects_invalid_instance_identity_revision_and_refresh(tmp_path, mutate):
    from src.config_store import ConfigValidationError

    path = tmp_path / "device.json"
    _write(path, _config())
    store = _store(path)
    store.load()
    item = _instance()
    mutate(item)

    with pytest.raises(ConfigValidationError):
        store.commit(0, _config_with_instances(item))


def test_v1_commit_rejects_globally_duplicate_instance_uuids(tmp_path):
    from src.config_store import ConfigValidationError

    path = tmp_path / "device.json"
    _write(path, _config())
    store = _store(path)
    store.load()

    with pytest.raises(ConfigValidationError, match="unique"):
        store.commit(0, _config_with_instances(_instance(), _instance()))


@pytest.mark.parametrize(
    "mutate",
    [
        lambda item: item.update(plugin_id=""),
        lambda item: item.update(name=""),
        lambda item: item.update(plugin_settings=[]),
    ],
    ids=["empty-plugin-id", "empty-name", "settings-type"],
)
def test_v1_commit_rejects_invalid_plugin_runtime_shape(tmp_path, mutate):
    from src.config_store import ConfigValidationError

    path = tmp_path / "device.json"
    _write(path, _config())
    store = _store(path)
    store.load()
    item = _instance()
    mutate(item)

    with pytest.raises(ConfigValidationError):
        store.commit(0, _config_with_instances(item))


def test_v1_commit_rejects_globally_duplicate_legacy_plugin_identity(tmp_path):
    from src.config_store import ConfigValidationError

    path = tmp_path / "device.json"
    _write(path, _config())
    store = _store(path)
    store.load()
    first = _instance("11111111111111111111111111111111")
    second = _instance("22222222222222222222222222222222")

    with pytest.raises(ConfigValidationError, match="identity"):
        store.commit(0, _config_with_instances(first, second))


@pytest.mark.parametrize(
    "refresh",
    [
        {},
        {"extra": True},
        {"interval": 60, "extra": True},
        {"scheduled": "23:59", "extra": True},
        {"interval": 60, "scheduled": "23:59"},
    ],
)
def test_v1_commit_allows_empty_and_extended_refresh_objects(tmp_path, refresh):
    path = tmp_path / "device.json"
    _write(path, _config())
    store = _store(path)
    store.load()

    snapshot = store.commit(0, _config_with_instances(_instance(refresh=refresh)))

    persisted_refresh = snapshot.data["playlist_config"]["playlists"][0]["plugins"][0]["refresh"]
    assert dict(persisted_refresh) == refresh


def test_legacy_load_allows_old_refresh_shapes_and_missing_instance_revisions(tmp_path):
    path = tmp_path / "device.json"
    legacy_instance = _instance(refresh={"interval": 0, "legacy_extra": True})
    legacy_instance.pop("instance_uuid")
    legacy_instance.pop("structural_generation")
    legacy_instance.pop("settings_revision")
    _write(path, _config_with_instances(legacy_instance))

    state = _store(path).load()

    assert state.status.valid is True
    assert state.snapshot.version == 0
    assert state.status.source == "primary"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.update(display_type=""),
        lambda value: value["playlist_config"]["playlists"][0].update(
            start_time="not-a-time"
        ),
        lambda value: value["playlist_config"]["playlists"][0]["plugins"][0].update(
            plugin_settings=[]
        ),
        lambda value: value["playlist_config"]["playlists"][0]["plugins"][0].update(
            refresh={"scheduled": "25:00"}
        ),
    ],
    ids=["display-type", "playlist-time", "settings-type", "scheduled-time"],
)
def test_legacy_load_still_rejects_runtime_incompatible_shapes(tmp_path, mutate):
    path = tmp_path / "device.json"
    legacy_instance = _instance(refresh={"interval": 0, "legacy_extra": True})
    legacy_instance.pop("instance_uuid")
    legacy_instance.pop("structural_generation")
    legacy_instance.pop("settings_revision")
    payload = _config_with_instances(legacy_instance)
    mutate(payload)
    _write(path, payload)

    state = _store(path).load()

    assert state.snapshot is None
    assert state.status.valid is False
    assert state.status.writable is False
    assert state.status.degraded_reason == "schema"


@pytest.mark.parametrize("mutation", ["rename", "delete"])
def test_legacy_load_allows_stale_active_playlist_from_current_producer(
    tmp_path,
    mutation,
):
    from model import Playlist, PlaylistManager

    manager = PlaylistManager(
        [Playlist("Default", "00:00", "24:00")],
        active_playlist="Default",
    )
    if mutation == "rename":
        manager.update_playlist("Default", "Renamed", "00:00", "24:00")
    else:
        manager.delete_playlist("Default")
    payload = _config(f"legacy-{mutation}")
    payload["playlist_config"] = manager.to_dict()
    path = tmp_path / "device.json"
    _write(path, payload)

    state = _store(path).load()

    assert state.snapshot.version == 0
    assert state.snapshot.data["playlist_config"]["active_playlist"] == "Default"
    assert state.status.valid is True
    assert state.status.writable is True


def test_legacy_revision_zero_advances_to_one_and_survives_restart(tmp_path):
    path = tmp_path / "device.json"
    _write(path, _config("legacy"))
    first = _store(path)

    assert first.load().snapshot.version == 0
    assert first.commit(0, _config("v1")).version == 1

    restarted = _store(path)
    assert restarted.load().snapshot.version == 1
    assert restarted.current().snapshot.data["name"] == "v1"


def test_versioned_revision_zero_is_valid_and_advances_to_one(tmp_path):
    path = tmp_path / "device.json"
    _write(path, _versioned(_config("v1-zero"), 0))
    store = _store(path)

    state = store.load()

    assert state.snapshot.version == 0
    assert state.snapshot.data["name"] == "v1-zero"
    assert state.status.writable is True
    assert store.commit(0, _config("next")).version == 1


def test_primary_is_authoritative_but_next_revision_uses_highest_valid_floor(tmp_path):
    path = tmp_path / "device.json"
    _write(path, _versioned(_config("primary"), 2))
    _write(tmp_path / "device.lkg.1.json", _versioned(_config("newer-backup"), 9))
    store = _store(path)

    state = store.load()
    committed = store.commit(2, _config("next"))

    assert state.snapshot.data["name"] == "primary"
    assert committed.version == 10


def test_revision_floor_never_decreases_after_observed_files_disappear(tmp_path):
    path = tmp_path / "device.json"
    _write(path, _versioned(_config("observed"), 9))
    store = _store(path)
    assert store.load().snapshot.version == 9
    path.unlink()

    missing = store.load()

    assert missing.snapshot is None
    assert missing.status.writable is True
    committed = store.commit(0, _config("replacement"))
    assert committed.version == 10


def test_same_store_two_writer_cas_allows_exactly_one_commit(tmp_path):
    from src.config_store import ConfigConflictError

    path = tmp_path / "device.json"
    _write(path, _config())
    store = _store(path)
    store.load()
    barrier = threading.Barrier(2)

    def commit(label):
        barrier.wait(timeout=5)
        try:
            return store.commit(0, _config(label))
        except ConfigConflictError as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(commit, ("a", "b")))

    snapshots = [item for item in results if not isinstance(item, ConfigConflictError)]
    conflicts = [item for item in results if isinstance(item, ConfigConflictError)]
    assert len(snapshots) == 1
    assert len(conflicts) == 1
    assert conflicts[0].expected_version == 0
    assert conflicts[0].actual_version == 1


def test_every_pre_replace_main_failure_preserves_primary_published_state_and_lkgs(tmp_path, monkeypatch):
    from src import config_store
    from src.utils.atomic_file import AtomicWriteError

    path = tmp_path / "device.json"
    lkg1 = tmp_path / "device.lkg.1.json"
    lkg2 = tmp_path / "device.lkg.2.json"
    _write(path, _versioned(_config("old"), 3))
    _write(lkg1, _versioned(_config("backup-1"), 2))
    _write(lkg2, _versioned(_config("backup-2"), 1))
    before = {item: item.read_bytes() for item in (path, lkg1, lkg2)}
    store = _store(path)
    old_state = store.load()

    def fail_main(target, _payload, *, mode):
        assert mode == 0o600
        assert Path(target) == path
        raise AtomicWriteError(path, "replace")

    monkeypatch.setattr(config_store, "atomic_write_json", fail_main)

    with pytest.raises(config_store.ConfigPersistenceError) as caught:
        store.commit(3, _config("new"))

    assert caught.value.stage == "replace"
    assert store.snapshot() is old_state.snapshot
    assert store.status() is old_state.status
    assert {item: item.read_bytes() for item in before} == before


def test_uncertain_main_write_keeps_old_snapshot_fences_then_reconciles(tmp_path, monkeypatch):
    from src import config_store
    from src.utils.atomic_file import AtomicCommitUncertainError

    path = tmp_path / "device.json"
    _write(path, _versioned(_config("old"), 4))
    store = _store(path)
    old = store.load().snapshot
    real_write = config_store.atomic_write_json
    calls = 0

    def uncertain_once(target, payload, *, mode):
        nonlocal calls
        calls += 1
        real_write(target, payload, mode=mode)
        if Path(target) == path and calls == 1:
            raise AtomicCommitUncertainError(path)

    monkeypatch.setattr(config_store, "atomic_write_json", uncertain_once)

    with pytest.raises(config_store.ConfigCommitUncertainError):
        store.commit(4, _config("disk-new"))

    assert json.loads(path.read_text(encoding="utf-8"))["name"] == "disk-new"
    assert store.snapshot() is old
    assert store.status().writable is False
    assert store.status().degraded_reason == "persistence_uncertain"
    with pytest.raises(config_store.ConfigStoreFencedError):
        store.commit(4, _config("blocked"))

    reconciled = store.load()
    assert reconciled.snapshot.version == 5
    assert reconciled.snapshot.data["name"] == "disk-new"
    assert reconciled.status.writable is True
    assert store.commit(5, _config("after-reconcile")).version == 6


def test_uncertain_reconcile_requires_successful_directory_fsync(tmp_path, monkeypatch):
    from src import config_store
    from src.utils.atomic_file import AtomicCommitUncertainError

    path = tmp_path / "device.json"
    _write(path, _versioned(_config("old"), 1))
    store = _store(path)
    old = store.load().snapshot
    real_write = config_store.atomic_write_json

    def uncertain(target, payload, *, mode):
        real_write(target, payload, mode=mode)
        raise AtomicCommitUncertainError(path)

    monkeypatch.setattr(config_store, "atomic_write_json", uncertain)
    with pytest.raises(config_store.ConfigCommitUncertainError):
        store.commit(1, _config("new"))
    monkeypatch.setattr(
        config_store,
        "fsync_directory",
        lambda _path: (_ for _ in ()).throw(OSError("still uncertain")),
    )

    state = store.load()
    assert state.snapshot is old
    assert state.status.writable is False
    with pytest.raises(config_store.ConfigStoreFencedError):
        store.commit(1, _config("blocked"))


def test_uncertain_reconcile_reads_primary_before_retrying_directory_fsync(
    tmp_path,
    monkeypatch,
):
    from src import config_store
    from src.utils.atomic_file import AtomicCommitUncertainError

    path = tmp_path / "device.json"
    _write(path, _versioned(_config("old"), 1))
    store = _store(path)
    store.load()
    real_write = config_store.atomic_write_json

    def uncertain(target, payload, *, mode):
        real_write(target, payload, mode=mode)
        raise AtomicCommitUncertainError(path)

    monkeypatch.setattr(config_store, "atomic_write_json", uncertain)
    with pytest.raises(config_store.ConfigCommitUncertainError):
        store.commit(1, _config("new"))

    events = []
    real_read = store._read_path
    real_fsync = config_store.fsync_directory

    def recording_read(target, source):
        events.append(f"read:{source}")
        return real_read(target, source)

    def recording_fsync(target):
        events.append("fsync")
        return real_fsync(target)

    monkeypatch.setattr(store, "_read_path", recording_read)
    monkeypatch.setattr(config_store, "fsync_directory", recording_fsync)

    reconciled = store.load()

    assert events[:2] == ["read:primary", "fsync"]
    assert reconciled.snapshot.version == 2
    assert reconciled.snapshot.data["name"] == "new"


def test_reads_do_not_wait_for_writer_lock_or_persistence(tmp_path, monkeypatch):
    from src import config_store

    path = tmp_path / "device.json"
    _write(path, _versioned(_config("old"), 1))
    store = _store(path)
    old = store.load().snapshot
    entered = threading.Event()
    release = threading.Event()
    real_write = config_store.atomic_write_json

    def blocked_write(target, payload, *, mode):
        if Path(target) == path:
            entered.set()
            assert release.wait(timeout=5)
        return real_write(target, payload, mode=mode)

    monkeypatch.setattr(config_store, "atomic_write_json", blocked_write)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(store.commit, 1, _config("new"))
        assert entered.wait(timeout=5)
        assert store.snapshot() is old
        assert store.current().snapshot.data["name"] == "old"
        assert store.status().version == 1
        release.set()
        assert future.result(timeout=5).version == 2


def test_candidate_deep_validation_and_copy_finish_before_writer_lock(tmp_path, monkeypatch):
    from src import config_store

    path = tmp_path / "device.json"
    _write(path, _versioned(_config("old"), 1))
    store = _store(path)
    store.load()
    real_freeze = config_store._freeze_json
    lock_observations = []

    def observing_freeze(value, active=None):
        lock_observations.append(store._writer_lock.locked())
        return real_freeze(value, active)

    monkeypatch.setattr(config_store, "_freeze_json", observing_freeze)

    store.commit(1, _config("new"))

    assert lock_observations
    assert not any(lock_observations)


def test_unwrapped_pre_replace_oserror_is_exposed_as_persistence_failure(tmp_path, monkeypatch):
    from src import config_store

    path = tmp_path / "device.json"
    _write(path, _versioned(_config("old"), 1))
    store = _store(path)
    old_state = store.load()
    monkeypatch.setattr(
        config_store,
        "atomic_write_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("parent vanished")),
    )

    with pytest.raises(config_store.ConfigPersistenceError) as caught:
        store.commit(1, _config("new"))

    assert caught.value.stage == "prepare"
    assert store.snapshot() is old_state.snapshot
    assert store.status() is old_state.status


def test_lkg_failure_after_main_commit_still_publishes_writable_degraded_state(tmp_path, monkeypatch):
    from src import config_store
    from src.utils.atomic_file import AtomicWriteError

    path = tmp_path / "device.json"
    _write(path, _versioned(_config("old"), 1))
    store = _store(path)
    store.load()
    real_write = config_store.atomic_write_json

    def fail_backups(target, payload, *, mode):
        if Path(target) != path:
            raise AtomicWriteError(Path(target), "replace")
        return real_write(target, payload, mode=mode)

    monkeypatch.setattr(config_store, "atomic_write_json", fail_backups)
    snapshot = store.commit(1, _config("new"))

    assert snapshot.version == 2
    assert store.current().snapshot.data["name"] == "new"
    assert store.status().valid is True
    assert store.status().writable is True
    assert store.status().degraded_reason == "lkg_update_failed"


def test_lkg_rotation_writes_old_primary_to_lkg2_then_new_primary_to_lkg1(tmp_path):
    path = tmp_path / "device.json"
    _write(path, _versioned(_config("old-primary"), 4))
    _write(tmp_path / "device.lkg.1.json", _versioned(_config("older"), 3))
    store = _store(path)
    store.load()

    store.commit(4, _config("new-primary"))

    lkg2 = json.loads((tmp_path / "device.lkg.2.json").read_text(encoding="utf-8"))
    lkg1 = json.loads((tmp_path / "device.lkg.1.json").read_text(encoding="utf-8"))
    assert (lkg2["config_revision"], lkg2["name"]) == (4, "old-primary")
    assert (lkg1["config_revision"], lkg1["name"]) == (5, "new-primary")


def test_commit_persistence_order_is_primary_then_lkg2_then_lkg1(tmp_path, monkeypatch):
    from src import config_store

    path = tmp_path / "device.json"
    _write(path, _versioned(_config("old-primary"), 4))
    _write(tmp_path / "device.lkg.1.json", _versioned(_config("older"), 3))
    store = _store(path)
    store.load()
    real_write = config_store.atomic_write_json
    write_order = []

    def recording_write(target, payload, *, mode):
        write_order.append(Path(target))
        return real_write(target, payload, mode=mode)

    monkeypatch.setattr(config_store, "atomic_write_json", recording_write)

    store.commit(4, _config("new-primary"))

    assert write_order == [path, store.lkg_paths[1], store.lkg_paths[0]]


def test_lkg1_failure_keeps_lkg2_old_history_and_does_not_fail_commit(tmp_path, monkeypatch):
    from src import config_store
    from src.utils.atomic_file import AtomicWriteError

    path = tmp_path / "device.json"
    _write(path, _versioned(_config("old-primary"), 4))
    store = _store(path)
    store.load()
    real_write = config_store.atomic_write_json

    def fail_lkg1(target, payload, *, mode):
        if Path(target) == store.lkg_paths[0]:
            raise AtomicWriteError(Path(target), "replace")
        return real_write(target, payload, mode=mode)

    monkeypatch.setattr(config_store, "atomic_write_json", fail_lkg1)

    snapshot = store.commit(4, _config("new-primary"))

    assert snapshot.version == 5
    assert store.status().degraded_reason == "lkg_update_failed"
    lkg2 = json.loads(store.lkg_paths[1].read_text(encoding="utf-8"))
    assert (lkg2["config_revision"], lkg2["name"]) == (4, "old-primary")


def test_lkg2_failure_preserves_the_only_valid_old_lkg1(tmp_path, monkeypatch):
    from src import config_store
    from src.utils.atomic_file import AtomicWriteError

    path = tmp_path / "device.json"
    lkg1 = tmp_path / "device.lkg.1.json"
    lkg2 = tmp_path / "device.lkg.2.json"
    _write(path, _versioned(_config("old-primary"), 4))
    _write(lkg1, _versioned(_config("only-backup"), 3))
    old_backup = lkg1.read_bytes()
    store = _store(path)
    store.load()
    real_write = config_store.atomic_write_json

    def fail_lkg2(target, payload, *, mode):
        if Path(target) == lkg2:
            raise AtomicWriteError(lkg2, "replace")
        return real_write(target, payload, mode=mode)

    monkeypatch.setattr(config_store, "atomic_write_json", fail_lkg2)
    store.commit(4, _config("new-primary"))

    assert lkg1.read_bytes() == old_backup
    assert store.status().degraded_reason == "lkg_update_failed"


def test_corrupt_primary_recovers_highest_revision_lkg_and_quarantines_original(tmp_path):
    path = tmp_path / "device_dev.json"
    path.write_text("{bad json", encoding="utf-8")
    _write(tmp_path / "device_dev.lkg.1.json", _versioned(_config("lkg1"), 4))
    _write(tmp_path / "device_dev.lkg.2.json", _versioned(_config("lkg2"), 7))
    store = _store(path)

    state = store.load()

    assert state.snapshot.version == 7
    assert state.snapshot.data["name"] == "lkg2"
    assert state.status.source == "lkg2"
    assert state.status.degraded_reason == "primary_recovered"
    assert json.loads(path.read_text(encoding="utf-8"))["name"] == "lkg2"
    quarantines = list(tmp_path.glob("device_dev.corrupt.*.json"))
    assert len(quarantines) == 1
    assert quarantines[0].read_text(encoding="utf-8") == "{bad json"


def test_runtime_incompatible_primary_yields_to_healthy_lkg(tmp_path):
    path = tmp_path / "device.json"
    invalid_primary = _config("invalid-primary")
    invalid_primary["orientation"] = "diagonal"
    _write(path, _versioned(invalid_primary, 5))
    _write(tmp_path / "device.lkg.1.json", _versioned(_config("healthy-lkg"), 4))

    state = _store(path).load()

    assert state.snapshot.version == 4
    assert state.snapshot.data["name"] == "healthy-lkg"
    assert state.status.source == "lkg1"
    assert state.status.degraded_reason == "primary_recovered"
    restored = json.loads(path.read_text(encoding="utf-8"))
    assert restored["name"] == "healthy-lkg"


def test_primary_read_failure_never_quarantines_or_restores_stale_lkg(
    tmp_path,
    monkeypatch,
):
    from src import config_store

    path = tmp_path / "device.json"
    lkg1 = tmp_path / "device.lkg.1.json"
    _write(path, _versioned(_config("current"), 9))
    _write(lkg1, _versioned(_config("stale"), 2))
    store = _store(path)
    old_state = store.load()
    primary_before = path.read_bytes()
    lkg_before = lkg1.read_bytes()
    real_open = Path.open

    def fail_primary_read(candidate, *args, **kwargs):
        if candidate == path and args and "r" in args[0]:
            raise PermissionError("primary is temporarily locked")
        return real_open(candidate, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_primary_read)

    failed = store.load()

    assert failed.snapshot is old_state.snapshot
    assert failed.status.valid is True
    assert failed.status.writable is False
    assert failed.status.degraded_reason == "read_failed"
    assert path.read_bytes() == primary_before
    assert lkg1.read_bytes() == lkg_before
    assert list(tmp_path.glob("device.corrupt.*.json")) == []
    with pytest.raises(config_store.ConfigStoreFencedError):
        store.commit(9, _config("blocked"))

    monkeypatch.setattr(Path, "open", real_open)
    retried = store.load()
    assert retried.snapshot.version == 9
    assert retried.snapshot.data["name"] == "current"
    assert retried.status.writable is True


def test_lkg_read_failure_is_degraded_without_overwriting_history(
    tmp_path,
    monkeypatch,
):
    path = tmp_path / "device.json"
    lkg1 = tmp_path / "device.lkg.1.json"
    _write(path, _versioned(_config("current"), 9))
    _write(lkg1, _versioned(_config("history"), 2))
    before = lkg1.read_bytes()
    real_open = Path.open

    def fail_lkg_read(candidate, *args, **kwargs):
        if candidate == lkg1 and args and "r" in args[0]:
            raise PermissionError("history is temporarily locked")
        return real_open(candidate, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_lkg_read)

    state = _store(path).load()

    assert state.snapshot.version == 9
    assert state.snapshot.data["name"] == "current"
    assert state.status.writable is False
    assert state.status.degraded_reason == "lkg_read_failed"
    assert lkg1.read_bytes() == before


def test_lkg_read_failure_fences_commit_until_all_history_is_observable(
    tmp_path,
    monkeypatch,
):
    from src import config_store

    path = tmp_path / "device.json"
    lkg1 = tmp_path / "device.lkg.1.json"
    lkg2 = tmp_path / "device.lkg.2.json"
    _write(path, _versioned(_config("primary"), 2))
    _write(lkg1, _versioned(_config("newer-history"), 9))
    before = {candidate: candidate.read_bytes() for candidate in (path, lkg1)}
    real_open = Path.open

    def fail_lkg_read(candidate, *args, **kwargs):
        if candidate == lkg1 and args and "r" in args[0]:
            raise PermissionError("history is temporarily locked")
        return real_open(candidate, *args, **kwargs)

    store = _store(path)
    healthy = store.load()
    assert healthy.status.writable is True

    monkeypatch.setattr(Path, "open", fail_lkg_read)

    with pytest.raises(config_store.ConfigStoreFencedError):
        store.commit(2, _config("must-not-overwrite-history"))
    assert store.status().writable is False
    assert store.status().degraded_reason == "lkg_read_failed"
    assert path.read_bytes() == before[path]
    assert lkg1.read_bytes() == before[lkg1]
    assert not lkg2.exists()

    monkeypatch.setattr(Path, "open", real_open)
    reconciled = store.load()

    assert reconciled.snapshot.version == 2
    assert reconciled.status.writable is True
    assert store.commit(2, _config("safe-after-reconcile")).version == 10


def test_commit_includes_newly_observed_lkg_revision_in_monotonic_floor(tmp_path):
    path = tmp_path / "device.json"
    lkg1 = tmp_path / "device.lkg.1.json"
    _write(path, _versioned(_config("primary"), 2))
    store = _store(path)
    assert store.load().snapshot.version == 2

    _write(lkg1, _versioned(_config("externally-observed-history"), 9))

    committed = store.commit(2, _config("next"))

    assert committed.version == 10


def test_equal_revision_recovery_prefers_lkg1(tmp_path):
    path = tmp_path / "device.json"
    path.write_text("[]", encoding="utf-8")
    _write(tmp_path / "device.lkg.1.json", _versioned(_config("first"), 5))
    _write(tmp_path / "device.lkg.2.json", _versioned(_config("second"), 5))

    state = _store(path).load()

    assert state.snapshot.data["name"] == "first"
    assert state.status.source == "lkg1"


@pytest.mark.parametrize(
    ("primary_text", "reason"),
    [
        ("{broken", "syntax"),
        ("[]", "root_type"),
        (json.dumps({"schema_version": 2, "config_revision": 1, "resolution": [800, 480]}), "schema"),
    ],
)
def test_all_corrupt_distinguishes_primary_failure_and_never_returns_empty_success(
    tmp_path,
    primary_text,
    reason,
):
    path = tmp_path / "device.json"
    path.write_text(primary_text, encoding="utf-8")
    (tmp_path / "device.lkg.1.json").write_text("{bad", encoding="utf-8")
    (tmp_path / "device.lkg.2.json").write_text("[]", encoding="utf-8")

    state = _store(path).load()

    assert state.snapshot is None
    assert state.status.valid is False
    assert state.status.writable is False
    assert state.status.source == "invalid"
    assert state.status.degraded_reason == reason


def test_fully_missing_is_invalid_missing_but_cleanly_writable(tmp_path):
    state = _store(tmp_path / "device.json").load()

    assert state.snapshot is None
    assert state.status.valid is False
    assert state.status.writable is True
    assert state.status.source == "missing"
    assert state.status.version == 0
    assert state.status.degraded_reason == "missing"


def test_clean_first_boot_commit_creates_lkg1_without_fake_lkg2_history(tmp_path):
    path = tmp_path / "device.json"
    store = _store(path)
    store.load()

    snapshot = store.commit(0, _config("first"))

    assert snapshot.version == 1
    assert store.lkg_paths[0].exists()
    assert not store.lkg_paths[1].exists()
    assert json.loads(store.lkg_paths[0].read_text(encoding="utf-8"))["name"] == "first"


def test_commit_before_explicit_load_is_fenced_and_cannot_overwrite_primary(tmp_path):
    from src.config_store import ConfigStoreFencedError

    path = tmp_path / "device.json"
    _write(path, _versioned(_config("existing"), 8))
    before = path.read_bytes()
    store = _store(path)

    with pytest.raises(ConfigStoreFencedError):
        store.commit(0, _config("must-not-overwrite"))

    assert path.read_bytes() == before
    assert store.status().source == "unloaded"
    assert store.status().writable is False


def test_utf8_bom_primary_loads_normally(tmp_path):
    path = tmp_path / "device.json"
    _write(path, _versioned(_config("bom"), 3), bom=True)

    state = _store(path).load()

    assert state.snapshot.version == 3
    assert state.snapshot.data["name"] == "bom"
    assert state.status.degraded_reason is None


def test_quarantine_rename_failure_does_not_overwrite_bad_primary(tmp_path, monkeypatch):
    path = tmp_path / "device.json"
    bad = b"{do not overwrite"
    path.write_bytes(bad)
    _write(tmp_path / "device.lkg.1.json", _versioned(_config("backup"), 2))
    store = _store(path)
    monkeypatch.setattr(
        store,
        "_quarantine_replace",
        lambda *_args: (_ for _ in ()).throw(PermissionError("locked")),
    )

    state = store.load()

    assert path.read_bytes() == bad
    assert state.snapshot.data["name"] == "backup"
    assert state.status.degraded_reason == "quarantine_failed"
    assert state.status.writable is False
    with pytest.raises(Exception) as caught:
        store.commit(2, _config("must-not-overwrite"))
    from src.config_store import ConfigStoreFencedError

    assert isinstance(caught.value, ConfigStoreFencedError)


@pytest.mark.skipif(os.name != "nt", reason="Windows handle sharing semantics only")
def test_windows_held_primary_handle_prevents_unsafe_quarantine(tmp_path):
    path = tmp_path / "device.json"
    bad = b"{held and corrupt"
    path.write_bytes(bad)
    _write(tmp_path / "device.lkg.1.json", _versioned(_config("backup"), 2))
    store = _store(path)

    with path.open("rb") as held_primary:
        state = store.load()
        assert held_primary.read() == bad

    assert path.read_bytes() == bad
    assert state.snapshot.data["name"] == "backup"
    assert state.status.degraded_reason == "quarantine_failed"
    assert state.status.writable is False


def test_quarantine_directory_fsync_failure_fences_without_restore(tmp_path, monkeypatch):
    from src import config_store

    path = tmp_path / "device.json"
    path.write_text("{bad", encoding="utf-8")
    _write(tmp_path / "device.lkg.1.json", _versioned(_config("backup"), 3))
    store = _store(path)
    monkeypatch.setattr(
        config_store,
        "fsync_directory",
        lambda _path: (_ for _ in ()).throw(OSError("directory fsync failed")),
    )

    state = store.load()

    assert state.snapshot.data["name"] == "backup"
    assert state.status.degraded_reason == "persistence_uncertain"
    assert state.status.writable is False
    assert not path.exists()


def test_restore_pre_replace_failure_loads_lkg_in_memory_and_fences(tmp_path, monkeypatch):
    from src import config_store
    from src.utils.atomic_file import AtomicWriteError

    path = tmp_path / "device.json"
    _write(tmp_path / "device.lkg.1.json", _versioned(_config("backup"), 2))
    store = _store(path)

    def fail_restore(target, _payload, *, mode):
        assert Path(target) == path
        assert mode == 0o600
        raise AtomicWriteError(path, "replace")

    monkeypatch.setattr(config_store, "atomic_write_json", fail_restore)
    state = store.load()

    assert state.snapshot.data["name"] == "backup"
    assert state.status.degraded_reason == "restore_failed"
    assert state.status.writable is False
    assert not path.exists()
    with pytest.raises(config_store.ConfigStoreFencedError):
        store.commit(2, _config("blocked"))


def test_uncertain_restore_loads_lkg_in_memory_and_fences(tmp_path, monkeypatch):
    from src import config_store
    from src.utils.atomic_file import AtomicCommitUncertainError

    path = tmp_path / "device.json"
    _write(tmp_path / "device.lkg.1.json", _versioned(_config("backup"), 6))
    store = _store(path)
    real_write = config_store.atomic_write_json

    def uncertain_restore(target, payload, *, mode):
        real_write(target, payload, mode=mode)
        raise AtomicCommitUncertainError(path)

    monkeypatch.setattr(config_store, "atomic_write_json", uncertain_restore)
    state = store.load()

    assert state.snapshot.version == 6
    assert state.status.degraded_reason == "persistence_uncertain"
    assert state.status.writable is False
    assert json.loads(path.read_text(encoding="utf-8"))["name"] == "backup"


def test_valid_primary_with_corrupt_lkg_is_started_and_repaired_in_isolation(tmp_path):
    path = tmp_path / "device.json"
    lkg1 = tmp_path / "device.lkg.1.json"
    _write(path, _versioned(_config("primary"), 3))
    lkg1.write_text("{bad", encoding="utf-8")

    state = _store(path).load()

    assert state.snapshot.data["name"] == "primary"
    assert state.status.valid is True
    assert state.status.degraded_reason is None
    repaired = json.loads(lkg1.read_text(encoding="utf-8"))
    assert repaired["name"] == "primary"
    assert repaired["config_revision"] == 3


def test_valid_primary_survives_failed_corrupt_lkg_repair_as_writable_degraded(tmp_path, monkeypatch):
    from src import config_store
    from src.utils.atomic_file import AtomicWriteError

    path = tmp_path / "device.json"
    lkg1 = tmp_path / "device.lkg.1.json"
    _write(path, _versioned(_config("primary"), 3))
    lkg1.write_text("{bad", encoding="utf-8")
    monkeypatch.setattr(
        config_store,
        "atomic_write_json",
        lambda target, *_args, **_kwargs: (_ for _ in ()).throw(
            AtomicWriteError(Path(target), "replace")
        ),
    )

    state = _store(path).load()

    assert state.snapshot.data["name"] == "primary"
    assert state.status.writable is True
    assert state.status.degraded_reason == "lkg_update_failed"


def test_unexpected_lkg_exception_never_reports_the_durable_main_commit_as_failed(tmp_path, monkeypatch):
    from src import config_store

    path = tmp_path / "device.json"
    _write(path, _versioned(_config("old"), 1))
    store = _store(path)
    store.load()
    real_write = config_store.atomic_write_json

    def fail_lkg(target, payload, *, mode):
        if Path(target) != path:
            raise RuntimeError("unexpected backup implementation failure")
        return real_write(target, payload, mode=mode)

    monkeypatch.setattr(config_store, "atomic_write_json", fail_lkg)

    committed = store.commit(1, _config("durable"))

    assert committed.version == 2
    assert store.snapshot() is committed
    assert store.status().writable is True
    assert store.status().degraded_reason == "lkg_update_failed"
    assert json.loads(path.read_text(encoding="utf-8"))["name"] == "durable"


def test_actual_device_dev_name_derives_two_lkg_paths_and_quarantine_is_not_history(tmp_path):
    path = tmp_path / "device_dev.json"
    path.write_text("{bad", encoding="utf-8")
    _write(tmp_path / "device_dev.lkg.1.json", _versioned(_config("backup"), 1))
    store = _store(path)
    store.load()
    store.commit(1, _config("next"))

    assert store.lkg_paths == (
        tmp_path / "device_dev.lkg.1.json",
        tmp_path / "device_dev.lkg.2.json",
    )
    assert len(list(tmp_path.glob("device_dev.lkg.*.json"))) == 2
    assert len(list(tmp_path.glob("device_dev.corrupt.*.json"))) == 1


def test_runtime_paths_object_is_the_config_identity_source(tmp_path):
    class Paths:
        config_file = tmp_path / "device_dev.json"

    _write(Paths.config_file, _config("runtime-paths"))

    store = _store(Paths())
    state = store.load()

    assert store.config_path == Paths.config_file
    assert state.snapshot.data["name"] == "runtime-paths"


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits are not meaningful on Windows")
def test_primary_and_lkg_files_use_owner_only_mode_on_posix(tmp_path):
    path = tmp_path / "device.json"
    _write(path, _versioned(_config("old"), 1))
    store = _store(path)
    store.load()

    store.commit(1, _config("new"))

    for target in (path, *store.lkg_paths):
        assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_every_atomic_config_write_requests_owner_only_mode(tmp_path, monkeypatch):
    from src import config_store

    path = tmp_path / "device.json"
    _write(path, _versioned(_config("old"), 1))
    store = _store(path)
    store.load()
    real_write = config_store.atomic_write_json
    observed_modes = []

    def recording_write(target, payload, *, mode):
        observed_modes.append((Path(target), mode))
        return real_write(target, payload, mode=mode)

    monkeypatch.setattr(config_store, "atomic_write_json", recording_write)
    store.commit(1, _config("new"))

    assert [target for target, _mode in observed_modes] == [
        path,
        store.lkg_paths[1],
        store.lkg_paths[0],
    ]
    assert {mode for _target, mode in observed_modes} == {0o600}
