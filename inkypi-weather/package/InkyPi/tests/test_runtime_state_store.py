import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone

import pytest

from src.runtime.runtime_state import RuntimeStateStore


class FakeClock:
    def __init__(self, monotonic=0.0, wall=1_752_067_200.0):
        self.monotonic = monotonic
        self.wall = wall

    def monotonic_time(self):
        return self.monotonic

    def wall_time(self):
        return self.wall

    def advance(self, seconds):
        self.monotonic += seconds
        self.wall += seconds


class ManualTimer:
    def __init__(self, delay, callback):
        self.delay = delay
        self.callback = callback
        self.daemon = False
        self.started = False
        self.cancelled = False

    def start(self):
        self.started = True

    def cancel(self):
        self.cancelled = True

    def fire(self):
        if not self.cancelled:
            self.callback()


def test_failure_does_not_advance_success_time(tmp_path):
    store = RuntimeStateStore(tmp_path / "runtime.json")
    store.record_success("one", "2026-07-09T10:00:00+00:00")
    store.record_failure(
        "one",
        "2026-07-09T10:01:00+00:00",
        "offline",
        "2026-07-09T10:01:30+00:00",
    )

    state = store.snapshot().instances["one"]

    assert state.last_success_at == "2026-07-09T10:00:00+00:00"
    assert state.last_failure_at == "2026-07-09T10:01:00+00:00"
    assert state.last_error == "offline"
    assert state.next_retry_at == "2026-07-09T10:01:30+00:00"


def test_attempt_is_separate_and_snapshot_is_immutable(tmp_path):
    store = RuntimeStateStore(tmp_path / "runtime.json")
    store.record_success("one", "2026-07-09T10:00:00+00:00")
    before = store.snapshot()

    store.record_attempt("one", "2026-07-09T10:01:00+00:00")
    after = store.snapshot()

    assert before.instances["one"].last_attempt_at is None
    assert before.instances["one"].last_success_at == "2026-07-09T10:00:00+00:00"
    assert after.instances["one"].last_attempt_at == "2026-07-09T10:01:00+00:00"
    assert after.instances["one"].last_success_at == "2026-07-09T10:00:00+00:00"
    with pytest.raises(TypeError):
        after.instances["two"] = after.instances["one"]
    with pytest.raises(FrozenInstanceError):
        after.instances["one"].last_success_at = "mutated"


def test_persistence_is_debounced_to_five_seconds_and_flush_is_synchronous(
    tmp_path,
    monkeypatch,
):
    clock = FakeClock()
    writes = []

    def record_write(path, payload, *, mode=0o600):
        writes.append((path, payload, mode))

    monkeypatch.setattr("src.runtime.runtime_state.atomic_write_json", record_write)
    store = RuntimeStateStore(
        tmp_path / "runtime.json",
        clock=clock.monotonic_time,
        wall_clock=clock.wall_time,
    )

    store.record_attempt("one", "2026-07-09T10:00:00+00:00")
    clock.advance(1)
    store.record_failure("one", "2026-07-09T10:00:01+00:00", "offline", None)
    clock.advance(3.999)
    store.record_attempt("one", "2026-07-09T10:00:04.999000+00:00")

    assert len(writes) == 1

    clock.advance(0.001)
    store.record_attempt("one", "2026-07-09T10:00:05+00:00")

    assert len(writes) == 2

    clock.advance(1)
    store.record_success("one", "2026-07-09T10:00:06+00:00")
    assert len(writes) == 2

    assert store.flush() is True
    assert len(writes) == 3
    assert store.flush() is False
    assert len(writes) == 3
    assert writes[-1][2] == 0o600


def test_last_dirty_update_is_flushed_when_debounce_timer_expires(tmp_path):
    clock = FakeClock()
    timers = []

    def timer_factory(delay, callback):
        timer = ManualTimer(delay, callback)
        timers.append(timer)
        return timer

    path = tmp_path / "runtime.json"
    store = RuntimeStateStore(
        path,
        clock=clock.monotonic_time,
        wall_clock=clock.wall_time,
        timer_factory=timer_factory,
    )
    store.record_success("one", "2026-07-09T10:00:00+00:00")
    clock.advance(1)
    store.record_failure(
        "one",
        "2026-07-09T10:00:01+00:00",
        "offline",
        "2026-07-09T10:00:30+00:00",
    )

    assert json.loads(path.read_text(encoding="utf-8"))["instances"]["one"][
        "last_failure_at"
    ] is None
    assert len(timers) == 1
    assert timers[0].delay == pytest.approx(4.0)
    assert timers[0].started is True
    assert timers[0].daemon is True

    clock.advance(4)
    timers[0].fire()

    assert json.loads(path.read_text(encoding="utf-8"))["instances"]["one"][
        "last_failure_at"
    ] == "2026-07-09T10:00:01+00:00"


def test_store_round_trips_display_and_instance_state(tmp_path):
    path = tmp_path / "runtime.json"
    store = RuntimeStateStore(path)
    store.record_attempt("one", "2026-07-09T10:00:00+00:00")
    store.record_success("one", "2026-07-09T10:00:01+00:00")
    store.set_display_state("committed", "commit-1", instance_uuid="one")
    store.flush()

    loaded = RuntimeStateStore(path).snapshot()

    assert loaded.instances["one"].last_attempt_at == "2026-07-09T10:00:00+00:00"
    assert loaded.instances["one"].last_success_at == "2026-07-09T10:00:01+00:00"
    assert loaded.display_state == "committed"
    assert loaded.display_commit_id == "commit-1"
    assert loaded.displayed_instance_uuid == "one"
    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == 1


def test_prune_keeps_current_instances_and_only_64_recent_tombstones(tmp_path):
    clock = FakeClock()
    store = RuntimeStateStore(
        tmp_path / "runtime.json",
        clock=clock.monotonic_time,
        wall_clock=clock.wall_time,
    )
    start = datetime(2026, 7, 9, 10, 0, tzinfo=timezone.utc)
    store.record_success("current", start.isoformat())
    for index in range(70):
        store.record_success(
            f"old-{index:02d}",
            (start + timedelta(seconds=index + 1)).isoformat(),
        )

    store.prune({"current"}, tombstoned_at="2026-07-09T11:00:00+00:00")
    snapshot = store.snapshot()

    assert "current" in snapshot.instances
    assert snapshot.instances["current"].tombstoned_at is None
    tombstones = {
        instance_uuid: state
        for instance_uuid, state in snapshot.instances.items()
        if state.tombstoned_at is not None
    }
    assert len(tombstones) == 64
    assert set(tombstones) == {f"old-{index:02d}" for index in range(6, 70)}


def test_prune_revives_a_current_uuid_and_persists_the_tombstone_cap(tmp_path):
    path = tmp_path / "runtime.json"
    store = RuntimeStateStore(path)
    store.record_success("one", "2026-07-09T10:00:00+00:00")
    store.prune(set(), tombstoned_at="2026-07-09T10:01:00+00:00")
    assert store.snapshot().instances["one"].tombstoned_at is not None

    store.prune({"one"}, tombstoned_at="2026-07-09T10:02:00+00:00")
    store.flush()

    assert store.snapshot().instances["one"].tombstoned_at is None
    assert RuntimeStateStore(path).snapshot().instances["one"].tombstoned_at is None


def test_concurrent_updates_publish_complete_immutable_snapshot(tmp_path):
    path = tmp_path / "runtime.json"
    clock = FakeClock()
    store = RuntimeStateStore(
        path,
        clock=clock.monotonic_time,
        wall_clock=clock.wall_time,
    )

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [
            executor.submit(
                store.record_attempt,
                f"instance-{index:02d}",
                f"2026-07-09T10:00:{index:02d}+00:00",
            )
            for index in range(32)
        ]
        for future in futures:
            future.result(timeout=2.0)

    assert set(store.snapshot().instances) == {
        f"instance-{index:02d}" for index in range(32)
    }
    store.flush()
    assert set(RuntimeStateStore(path).snapshot().instances) == {
        f"instance-{index:02d}" for index in range(32)
    }


@pytest.mark.parametrize("invalid_uuid", [None, 1, "", " \t "])
def test_instance_identity_is_a_non_empty_string(tmp_path, invalid_uuid):
    store = RuntimeStateStore(tmp_path / "runtime.json")

    with pytest.raises((TypeError, ValueError)):
        store.record_attempt(invalid_uuid, "2026-07-09T10:00:00+00:00")
