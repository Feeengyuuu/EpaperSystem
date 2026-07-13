import os
import time
from pathlib import Path

import pytest
from PIL import Image

from src.display import display_transaction as transaction_module
from src.display.display_transaction import (
    DisplayCommitUnknownError,
    DisplayTransaction,
)
from src.runtime.cache_lifecycle import (
    CleanupBudget,
    LifecycleAggregate,
    LifecycleAllowance,
)
from src.runtime.refresh_contracts import TaskContext
from src.runtime.runtime_state import RuntimeStateStore


def _image(color):
    return Image.new("RGB", (8, 6), color)


def _context():
    return TaskContext.never_cancelled(deadline_monotonic=time.monotonic() + 5)


def _maintenance_allowance(
    *,
    now_monotonic=None,
    scanned=128,
    deleted=32,
    deleted_bytes=1024 * 1024,
):
    now_monotonic = time.monotonic() if now_monotonic is None else now_monotonic
    clock = lambda: now_monotonic
    return LifecycleAllowance(
        CleanupBudget(
            max_scanned_entries=scanned,
            max_deleted_entries=deleted,
            max_deleted_bytes=deleted_bytes,
            max_duration_seconds=5,
        ).start(now_monotonic),
        LifecycleAggregate(),
        clock=clock,
    )


def _aged_file(path, *, now_epoch, age_seconds, payload=b"residue"):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    modified = now_epoch - age_seconds
    os.utime(path, (modified, modified))
    return path


def _orphan_object(transaction, commit_id, *, modified):
    path = transaction.objects_dir / f"{commit_id}.png"
    _image("blue").save(path)
    os.utime(path, (modified, modified))
    return path


class FakeManager:
    def __init__(self):
        self.calls = []
        self.error = None

    def prepare_image(self, image, *, image_settings=()):
        return image.copy()

    def hardware_fingerprint(self, image_settings=()):
        return f"fake:{tuple(image_settings)}"

    def write_hardware_path(self, image_path, *, image_settings=(), task_context):
        task_context.raise_if_cancelled()
        self.calls.append((Path(image_path), tuple(image_settings)))
        if self.error is not None:
            raise self.error


@pytest.fixture
def display_transaction(tmp_path):
    display_dir = tmp_path / "display"
    display_dir.mkdir()
    runtime_state = RuntimeStateStore(tmp_path / "runtime.json")
    manager = FakeManager()
    transaction = DisplayTransaction(
        manager,
        display_dir=display_dir,
        compatibility_image_path=display_dir / "current_image.png",
        runtime_state_store=runtime_state,
    )
    return transaction, manager, runtime_state


def test_hardware_failure_keeps_previous_manifest(display_transaction):
    transaction, manager, _runtime_state = display_transaction
    first = transaction.commit(
        transaction.prepare(_image("red"), logical_target={"id": "one"}),
        task_context=_context(),
    )
    manager.error = RuntimeError("busy")

    with pytest.raises(RuntimeError, match="busy"):
        transaction.commit(
            transaction.prepare(_image("blue"), logical_target={"id": "two"}),
            task_context=_context(),
        )

    assert transaction.current().commit_id == first.commit_id


def test_same_pixels_new_logical_target_creates_metadata_only_commit(
    display_transaction,
):
    transaction, manager, _runtime_state = display_transaction
    first = transaction.commit(
        transaction.prepare(_image("red"), logical_target={"id": "one"}),
        task_context=_context(),
    )
    second = transaction.commit(
        transaction.prepare(_image("red"), logical_target={"id": "two"}),
        task_context=_context(),
    )

    assert len(manager.calls) == 1
    assert second.commit_id != first.commit_id
    assert dict(second.logical_target) == {"id": "two"}
    assert second.hardware_written is False


def test_manifest_failure_after_hardware_marks_display_unknown(
    display_transaction,
    monkeypatch,
):
    transaction, manager, runtime_state = display_transaction
    prepared = transaction.prepare(_image("red"), logical_target={"id": "one"})
    monkeypatch.setattr(
        transaction_module,
        "atomic_write_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )

    with pytest.raises(DisplayCommitUnknownError) as raised:
        transaction.commit(prepared, task_context=_context())

    assert raised.value.commit_id == prepared.commit_id
    assert len(manager.calls) == 1
    snapshot = runtime_state.snapshot()
    assert snapshot.display_state == "display_unknown"
    assert snapshot.display_commit_id == prepared.commit_id


def test_recover_resubmits_last_manifest_after_newer_orphan(display_transaction):
    transaction, manager, runtime_state = display_transaction
    first = transaction.commit(
        transaction.prepare(
            _image("red"),
            logical_target={"instance_uuid": "one"},
        ),
        task_context=_context(),
    )
    manager.error = RuntimeError("busy")
    with pytest.raises(RuntimeError):
        transaction.commit(
            transaction.prepare(_image("blue"), logical_target={"id": "two"}),
            task_context=_context(),
        )
    manager.error = None

    recovered = transaction.recover(task_context=_context())

    assert recovered.commit_id == first.commit_id
    assert manager.calls[-1][0] == first.image_path
    snapshot = runtime_state.snapshot()
    assert snapshot.display_state == "committed"
    assert snapshot.display_commit_id == first.commit_id
    assert snapshot.displayed_instance_uuid == "one"


def test_recover_without_valid_manifest_stays_not_ready(display_transaction):
    transaction, manager, runtime_state = display_transaction

    assert transaction.recover(task_context=_context()) is None
    assert manager.calls == []
    assert runtime_state.snapshot().display_state == "not_ready"


def test_display_maintenance_keeps_manifest_current_and_latest_eight_objects(
    display_transaction,
):
    transaction, _manager, _runtime_state = display_transaction
    current = transaction.commit(
        transaction.prepare(_image("red"), logical_target={"id": "current"}),
        task_context=_context(),
    )
    now = time.time()
    os.utime(current.image_path, (now - 100, now - 100))
    orphans = [
        _orphan_object(transaction, f"{index + 100:032x}", modified=now + index)
        for index in range(10)
    ]

    transaction.maintenance(
        now_epoch=now,
        stale_seconds=2 * 60 * 60,
        allowance=_maintenance_allowance(),
        dry_run=False,
    )

    remaining = set(transaction.objects_dir.glob("*.png"))
    assert current.image_path in remaining
    assert remaining == {current.image_path, *orphans[-8:]}


def test_display_object_prune_shares_budget_and_dry_run_plan(
    display_transaction,
):
    transaction, _manager, _runtime_state = display_transaction
    current = transaction.commit(
        transaction.prepare(_image("red"), logical_target={"id": "current"}),
        task_context=_context(),
    )
    now = time.time()
    os.utime(current.image_path, (now - 100, now - 100))
    orphans = [
        _orphan_object(transaction, f"{index + 300:032x}", modified=now + index)
        for index in range(10)
    ]
    all_objects = {current.image_path, *orphans}

    scan_limited = transaction.maintenance(
        now_epoch=now,
        stale_seconds=2 * 60 * 60,
        allowance=_maintenance_allowance(scanned=1),
        dry_run=False,
    )

    assert set(transaction.objects_dir.glob("*.png")) == all_objects
    assert scan_limited.candidate_entries == 0
    assert scan_limited.deleted_entries == 0
    assert scan_limited.backlog_entries == 1

    dry_allowance = _maintenance_allowance(deleted=1)
    dry = transaction.maintenance(
        now_epoch=now,
        stale_seconds=2 * 60 * 60,
        allowance=dry_allowance,
        dry_run=True,
    )

    assert set(transaction.objects_dir.glob("*.png")) == all_objects
    assert dry is dry_allowance.aggregate
    assert dry.candidate_entries == 1
    assert dry.deleted_entries == 0
    assert dry.backlog_entries == 1

    real = transaction.maintenance(
        now_epoch=now,
        stale_seconds=2 * 60 * 60,
        allowance=_maintenance_allowance(deleted=1),
        dry_run=False,
    )

    assert real.candidate_entries == dry.candidate_entries
    assert real.deleted_entries == 1
    assert real.deleted_bytes > 0
    assert len(set(transaction.objects_dir.glob("*.png"))) == len(all_objects) - 1


def test_display_maintenance_removes_only_old_reserved_atomic_temps_under_lock(
    display_transaction,
    monkeypatch,
):
    transaction, _manager, _runtime_state = display_transaction
    current = transaction.commit(
        transaction.prepare(_image("red"), logical_target={"id": "current"}),
        task_context=_context(),
    )
    now = time.time()
    stale = 2 * 60 * 60
    old_manifest_temp = _aged_file(
        transaction.display_dir / ".display_manifest.json.deadbeef.tmp",
        now_epoch=now,
        age_seconds=stale + 1,
    )
    old_object_temp = _aged_file(
        transaction.objects_dir / f".{('a' * 32)}.png.deadbeef.tmp",
        now_epoch=now,
        age_seconds=stale + 1,
    )
    old_compatibility_temp = _aged_file(
        transaction.display_dir / ".current_image.png.deadbeef.tmp",
        now_epoch=now,
        age_seconds=stale + 1,
    )
    recent_reserved = _aged_file(
        transaction.display_dir / ".display_manifest.json.recent.tmp",
        now_epoch=now,
        age_seconds=stale,
    )
    unknown_temp = _aged_file(
        transaction.display_dir / ".notes.deadbeef.tmp",
        now_epoch=now,
        age_seconds=stale + 1,
    )
    compatibility_before = transaction.compatibility_image_path.read_bytes()
    lock_checks = []
    original_current = transaction.current
    original_prune = transaction._prune_objects

    def current_under_lock():
        lock_checks.append(transaction._lock._is_owned())
        return original_current()

    def prune_under_lock(*, current_path, **kwargs):
        lock_checks.append(transaction._lock._is_owned())
        return original_prune(current_path=current_path, **kwargs)

    monkeypatch.setattr(transaction, "current", current_under_lock)
    monkeypatch.setattr(transaction, "_prune_objects", prune_under_lock)

    maintenance_allowance = _maintenance_allowance()
    aggregate = transaction.maintenance(
        now_epoch=now,
        stale_seconds=stale,
        allowance=maintenance_allowance,
        dry_run=False,
    )

    assert lock_checks == [True, True]
    assert not old_manifest_temp.exists()
    assert not old_object_temp.exists()
    assert not old_compatibility_temp.exists()
    assert recent_reserved.is_file()
    assert unknown_temp.is_file()
    assert current.image_path.is_file()
    assert transaction.compatibility_image_path.read_bytes() == compatibility_before
    assert aggregate is maintenance_allowance.aggregate
    assert aggregate.deleted_entries == 3


def test_display_maintenance_dry_run_and_budget_plan_only_reserved_temps(
    display_transaction,
):
    transaction, _manager, _runtime_state = display_transaction
    now = time.time()
    stale = 2 * 60 * 60
    temps = {
        _aged_file(
            transaction.display_dir / f".display_manifest.json.plan-{index}.tmp",
            now_epoch=now,
            age_seconds=stale + 1,
            payload=b"four",
        )
        for index in range(2)
    }
    dry_allowance = _maintenance_allowance(
        scanned=16,
        deleted=1,
        deleted_bytes=4,
    )

    dry = transaction.maintenance(
        now_epoch=now,
        stale_seconds=stale,
        allowance=dry_allowance,
        dry_run=True,
    )

    assert dry is dry_allowance.aggregate
    assert dry.candidate_entries == 1
    assert dry.deleted_entries == 0
    assert dry.backlog_entries == 1
    assert all(path.is_file() for path in temps)

    real = transaction.maintenance(
        now_epoch=now,
        stale_seconds=stale,
        budget=CleanupBudget(
            max_scanned_entries=16,
            max_deleted_entries=1,
            max_deleted_bytes=4,
            max_duration_seconds=5,
        ),
        dry_run=False,
    )

    assert real.candidate_entries == dry.candidate_entries
    assert real.deleted_entries == 1
    assert real.deleted_bytes == 4
    assert real.backlog_entries == 1
    assert sum(path.is_file() for path in temps) == 1


def test_display_maintenance_recovery_after_partial_temp_cleanup_is_idempotent(
    display_transaction,
    monkeypatch,
):
    transaction, _manager, _runtime_state = display_transaction
    transaction.commit(
        transaction.prepare(_image("red"), logical_target={"id": "current"}),
        task_context=_context(),
    )
    now = time.time()
    stale = 2 * 60 * 60
    temps = [
        _aged_file(
            transaction.display_dir / f".display_manifest.json.partial-{index}.tmp",
            now_epoch=now,
            age_seconds=stale + 1,
        )
        for index in range(2)
    ]
    original_unlink = Path.unlink
    attempted = []

    def interrupt_second(path, *args, **kwargs):
        if path in temps:
            attempted.append(path)
            if len(attempted) == 2:
                raise OSError("simulated cleanup interruption")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", interrupt_second)
    partial = transaction.maintenance(
        now_epoch=now,
        stale_seconds=stale,
        allowance=_maintenance_allowance(),
        dry_run=False,
    )

    assert sum(path.exists() for path in temps) == 1
    assert partial.deleted_entries == 1
    assert partial.error_count == 1

    monkeypatch.setattr(Path, "unlink", original_unlink)
    recovered = transaction.maintenance(
        now_epoch=now,
        stale_seconds=stale,
        allowance=_maintenance_allowance(),
        dry_run=False,
    )
    repeated = transaction.maintenance(
        now_epoch=now,
        stale_seconds=stale,
        allowance=_maintenance_allowance(),
        dry_run=False,
    )

    assert not any(path.exists() for path in temps)
    assert recovered.deleted_entries == 1
    assert repeated.deleted_entries == 0
    assert repeated.error_count == 0


def test_display_maintenance_never_touches_compatibility_current_image(
    display_transaction,
):
    transaction, _manager, _runtime_state = display_transaction
    now = time.time()
    stale = 2 * 60 * 60
    transaction.manifest_path.write_text("{}", encoding="utf-8")
    objects = [
        _orphan_object(transaction, f"{index + 200:032x}", modified=now - 10_000 - index)
        for index in range(12)
    ]
    compatibility_payload = b"compatibility-current-image"
    transaction.compatibility_image_path.write_bytes(compatibility_payload)
    reserved = _aged_file(
        transaction.display_dir / ".display_manifest.json.invalid-current.tmp",
        now_epoch=now,
        age_seconds=stale + 1,
    )

    aggregate = transaction.maintenance(
        now_epoch=now,
        stale_seconds=stale,
        allowance=_maintenance_allowance(),
        dry_run=False,
    )

    assert all(path.is_file() for path in objects)
    assert transaction.compatibility_image_path.read_bytes() == compatibility_payload
    assert not reserved.exists()
    assert aggregate.deleted_entries == 1
