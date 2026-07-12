import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone

import pytest

from src.runtime import runtime_state
from src.runtime.runtime_state import (
    LastGoodCacheState,
    RefreshLane,
    RuntimeStateStore,
)


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


def presentation_request(request_id="a" * 32, **changes):
    values = {
        "request_id": request_id,
        "requested_at": "2026-07-09T10:00:00+00:00",
        "structural_generation": 4,
        "settings_revision": 9,
        "origin_theme_mode": "day",
        "origin_display_commit_id": "display-origin",
    }
    values.update(changes)
    return runtime_state.PresentationRequestState(**values)


def presentation_receipt(request_id="a" * 32, **changes):
    values = {
        "request_id": request_id,
        "committed_at": "2026-07-09T10:05:00+00:00",
        "display_commit_id": "display-committed",
        "structural_generation": 4,
        "settings_revision": 9,
        "theme_mode": "night",
    }
    values.update(changes)
    return runtime_state.PresentationCommitReceipt(**values)


def test_data_live_theme_success_clocks_are_independent(tmp_path):
    store = RuntimeStateStore(tmp_path / "runtime.json")

    store.record_success(
        "one",
        "2026-07-09T10:00:00+00:00",
        lane=RefreshLane.DATA,
    )
    store.record_success(
        "one",
        "2026-07-09T10:01:00+00:00",
        lane=RefreshLane.LIVE,
    )
    store.record_success(
        "one",
        "2026-07-09T10:02:00+00:00",
        lane=RefreshLane.THEME,
    )

    state = store.snapshot().instances["one"]
    assert state.data.last_success_at == "2026-07-09T10:00:00+00:00"
    assert state.live.last_success_at == "2026-07-09T10:01:00+00:00"
    assert state.theme.last_success_at == "2026-07-09T10:02:00+00:00"


def test_failure_cools_only_requested_instance_lane(tmp_path):
    store = RuntimeStateStore(tmp_path / "runtime.json")
    for lane in RefreshLane:
        store.record_success("one", "2026-07-09T10:00:00+00:00", lane=lane)

    store.record_failure(
        "one",
        "2026-07-09T10:03:00+00:00",
        "live provider offline",
        "2026-07-09T10:04:00+00:00",
        lane=RefreshLane.LIVE,
    )

    state = store.snapshot().instances["one"]
    assert state.data.next_retry_at is None
    assert state.theme.next_retry_at is None
    assert state.live.next_retry_at == "2026-07-09T10:04:00+00:00"
    assert state.live.last_error == "live provider offline"


def test_last_good_cache_requires_exact_revision(tmp_path):
    store = RuntimeStateStore(tmp_path / "runtime.json")
    cache = LastGoodCacheState(
        theme_mode="night",
        structural_generation=3,
        settings_revision=7,
        promoted_at="2026-07-09T10:00:00+00:00",
    )

    store.record_success(
        "one",
        "2026-07-09T10:00:00+00:00",
        last_good_cache=cache,
    )
    state = store.snapshot().instances["one"]

    assert state.last_good_cache == cache
    assert state.last_good_cache.structural_generation == 3
    assert state.last_good_cache.settings_revision == 7
    with pytest.raises((TypeError, ValueError)):
        LastGoodCacheState(
            theme_mode="night",
            structural_generation=3,
            settings_revision=None,
            promoted_at="2026-07-09T10:00:00+00:00",
        )
    with pytest.raises(ValueError):
        LastGoodCacheState(
            theme_mode=None,
            structural_generation=0,
            settings_revision=7,
            promoted_at="2026-07-09T10:00:00+00:00",
        )


def test_schema_v1_migrates_cache_success_without_claiming_theme_as_data_success(
    tmp_path,
):
    path = tmp_path / "runtime.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "updated_at": "2026-07-09T10:06:00+00:00",
                "display": {
                    "state": "committed",
                    "commit_id": "display-1",
                    "instance_uuid": "one",
                },
                "instances": {
                    "one": {
                        "last_attempt_at": "2026-07-09T10:00:00+00:00",
                        "last_success_at": "2026-07-09T10:01:00+00:00",
                        "last_failure_at": "2026-07-09T10:02:00+00:00",
                        "last_error": "temporary",
                        "next_retry_at": "2026-07-09T10:03:00+00:00",
                        "tombstoned_at": None,
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    snapshot = RuntimeStateStore(path).snapshot()
    state = snapshot.instances["one"]

    assert snapshot.schema_version == 3
    assert state.data.last_attempt_at == "2026-07-09T10:00:00+00:00"
    assert state.data.last_failure_at == "2026-07-09T10:02:00+00:00"
    assert state.data.last_success_at is None
    assert state.live.last_success_at is None
    assert state.theme.last_success_at is None
    assert state.last_good_cache is None
    assert state.legacy_cache_success_at == "2026-07-09T10:01:00+00:00"
    assert state.last_success_at == "2026-07-09T10:01:00+00:00"
    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == 3


def test_schema_v2_roundtrip_preserves_lane_and_last_good_state(tmp_path):
    path = tmp_path / "runtime.json"
    store = RuntimeStateStore(path)
    cache = LastGoodCacheState(
        theme_mode="day",
        structural_generation=4,
        settings_revision=9,
        promoted_at="2026-07-09T10:04:00+00:00",
    )
    store.record_attempt(
        "one",
        "2026-07-09T10:00:00+00:00",
        lane=RefreshLane.THEME,
    )
    store.record_success(
        "one",
        "2026-07-09T10:04:00+00:00",
        lane=RefreshLane.THEME,
        last_good_cache=cache,
    )
    store.record_failure(
        "one",
        "2026-07-09T10:05:00+00:00",
        "data failed",
        "2026-07-09T10:06:00+00:00",
        lane=RefreshLane.DATA,
    )
    store.flush()

    state = RuntimeStateStore(path).snapshot().instances["one"]

    assert state.theme.last_attempt_at == "2026-07-09T10:00:00+00:00"
    assert state.theme.last_success_at == "2026-07-09T10:04:00+00:00"
    assert state.data.last_failure_at == "2026-07-09T10:05:00+00:00"
    assert state.last_good_cache == cache


def test_schema_v2_migrates_with_empty_presentation_state(tmp_path):
    path = tmp_path / "runtime.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "updated_at": "2026-07-09T10:06:00+00:00",
                "display": {
                    "state": "committed",
                    "commit_id": "display-1",
                    "instance_uuid": "one",
                },
                "instances": {
                    "one": {
                        "lanes": {
                            "data": {
                                "last_attempt_at": "2026-07-09T10:00:00+00:00",
                                "last_success_at": "2026-07-09T10:01:00+00:00",
                                "last_failure_at": None,
                                "last_error": None,
                                "next_retry_at": None,
                            },
                            "live": {},
                            "theme": {},
                        },
                        "last_good_cache": None,
                        "legacy_cache_success_at": None,
                        "tombstoned_at": None,
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    snapshot = RuntimeStateStore(path).snapshot()
    state = snapshot.instances["one"]

    assert snapshot.schema_version == 3
    assert state.data.last_success_at == "2026-07-09T10:01:00+00:00"
    assert state.presentation == runtime_state.RefreshLaneState()
    assert state.presentation_request is None
    assert state.presentation_receipt is None
    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == 3


def test_schema_v3_roundtrip_preserves_pending_prepared_request(tmp_path):
    path = tmp_path / "runtime.json"
    store = RuntimeStateStore(path)
    request = presentation_request()

    assert store.request_presentation("one", request) is True
    store.record_attempt(
        "one",
        "2026-07-09T10:01:00+00:00",
        lane=runtime_state.RefreshLane.PRESENTATION,
    )
    store.record_failure(
        "one",
        "2026-07-09T10:02:00+00:00",
        "bank temporarily unavailable",
        "2026-07-09T10:03:00+00:00",
        lane=runtime_state.RefreshLane.PRESENTATION,
    )
    assert (
        store.mark_presentation_prepared(
            "one",
            request.request_id,
            "2026-07-09T10:04:00+00:00",
            "night",
        )
        is True
    )
    prepared = store.snapshot().instances["one"].presentation_request
    store.flush()

    loaded = RuntimeStateStore(path).snapshot().instances["one"]

    assert loaded.presentation_request == prepared
    assert loaded.presentation_request.prepared_at == "2026-07-09T10:04:00+00:00"
    assert loaded.presentation_request.prepared_theme_mode == "night"
    assert loaded.presentation.last_attempt_at == "2026-07-09T10:01:00+00:00"
    assert loaded.presentation.last_failure_at == "2026-07-09T10:02:00+00:00"
    assert loaded.presentation.next_retry_at == "2026-07-09T10:03:00+00:00"
    assert loaded.presentation_receipt is None
    assert loaded.latest_activity_at() == "2026-07-09T10:04:00+00:00"
    with pytest.raises(FrozenInstanceError):
        loaded.presentation_request.prepared_at = "mutated"

    for invalid_request_id in ("A" * 32, "g" * 32, "a" * 31, "a" * 33):
        with pytest.raises(ValueError):
            presentation_request(invalid_request_id)
    for field in ("structural_generation", "settings_revision"):
        for invalid_revision in (True, 0, -1, 1.5):
            with pytest.raises((TypeError, ValueError)):
                presentation_request(**{field: invalid_revision})
    for invalid_theme in ("dusk", "DAY"):
        with pytest.raises(ValueError):
            presentation_request(origin_theme_mode=invalid_theme)
        with pytest.raises(ValueError):
            presentation_request(
                prepared_at="2026-07-09T10:04:00+00:00",
                prepared_theme_mode=invalid_theme,
            )
    for invalid_timestamp in ("", "not-an-iso-timestamp"):
        with pytest.raises(ValueError):
            presentation_request(requested_at=invalid_timestamp)
        with pytest.raises(ValueError):
            presentation_request(prepared_at=invalid_timestamp)


def test_presentation_commit_atomically_records_receipt_lane_success_and_last_good(
    tmp_path,
):
    path = tmp_path / "runtime.json"
    store = RuntimeStateStore(path)
    request = presentation_request()
    receipt = presentation_receipt()
    last_good = LastGoodCacheState(
        theme_mode="night",
        structural_generation=4,
        settings_revision=9,
        promoted_at="2026-07-09T10:05:00+00:00",
    )
    assert store.request_presentation("one", request) is True
    store.record_failure(
        "one",
        "2026-07-09T10:02:00+00:00",
        "transient",
        "2026-07-09T10:03:00+00:00",
        lane=runtime_state.RefreshLane.PRESENTATION,
    )
    assert (
        store.mark_presentation_prepared(
            "one",
            request.request_id,
            "2026-07-09T10:04:00+00:00",
            "night",
        )
        is True
    )
    before = store.snapshot()

    assert (
        store.commit_presentation(
            "one",
            receipt,
            last_good_cache=last_good,
        )
        is True
    )
    after = store.snapshot()

    assert before.instances["one"].presentation_request is not None
    assert before.instances["one"].presentation_receipt is None
    assert after is not before
    assert after.instances["one"].presentation_request is None
    assert after.instances["one"].presentation_receipt == receipt
    assert after.instances["one"].presentation.last_success_at == "2026-07-09T10:05:00+00:00"
    assert after.instances["one"].presentation.next_retry_at is None
    assert after.instances["one"].last_good_cache == last_good
    assert after.instances["one"].latest_activity_at() == receipt.committed_at
    store.flush()
    assert RuntimeStateStore(path).snapshot().instances["one"] == after.instances["one"]
    with pytest.raises(FrozenInstanceError):
        after.instances["one"].presentation_receipt.committed_at = "mutated"

    for invalid_request_id in ("A" * 32, "g" * 32, "a" * 31, "a" * 33):
        with pytest.raises(ValueError):
            presentation_receipt(invalid_request_id)
    for field in ("structural_generation", "settings_revision"):
        for invalid_revision in (True, 0, -1, 1.5):
            with pytest.raises((TypeError, ValueError)):
                presentation_receipt(**{field: invalid_revision})
    with pytest.raises(ValueError):
        presentation_receipt(theme_mode="dusk")
    for invalid_timestamp in ("", "not-an-iso-timestamp"):
        with pytest.raises(ValueError):
            presentation_receipt(committed_at=invalid_timestamp)


def test_stale_request_id_cannot_prepare_or_commit_newer_request(tmp_path):
    store = RuntimeStateStore(tmp_path / "runtime.json")
    old_request = presentation_request("a" * 32, settings_revision=8)
    current_request = presentation_request("b" * 32, settings_revision=9)

    assert store.request_presentation("one", old_request) is True
    assert store.request_presentation("one", current_request) is True
    before_stale_prepare = store.snapshot()
    assert (
        store.mark_presentation_prepared(
            "one",
            old_request.request_id,
            "2026-07-09T10:04:00+00:00",
            "night",
        )
        is False
    )
    assert store.snapshot() is before_stale_prepare

    assert (
        store.mark_presentation_prepared(
            "one",
            current_request.request_id,
            "2026-07-09T10:04:00+00:00",
            "night",
        )
        is True
    )
    before_stale_commit = store.snapshot()
    assert (
        store.commit_presentation(
            "one",
            presentation_receipt("a" * 32, settings_revision=8),
            last_good_cache=LastGoodCacheState(
                theme_mode="night",
                structural_generation=4,
                settings_revision=8,
                promoted_at="2026-07-09T10:05:00+00:00",
            ),
        )
        is False
    )
    assert store.snapshot() is before_stale_commit
    assert store.snapshot().instances["one"].presentation_request.request_id == current_request.request_id

    before_current_clear = store.snapshot()
    assert (
        store.clear_stale_presentation(
            "one",
            structural_generation=4,
            settings_revision=9,
        )
        is False
    )
    assert store.snapshot() is before_current_clear
    assert (
        store.clear_stale_presentation(
            "one",
            structural_generation=5,
            settings_revision=9,
        )
        is True
    )
    assert store.snapshot().instances["one"].presentation_request is None
    for invalid_revision in (True, 0, -1, 1.5):
        with pytest.raises((TypeError, ValueError)):
            store.clear_stale_presentation(
                "one",
                structural_generation=invalid_revision,
                settings_revision=9,
            )


def test_unresolved_request_is_coalesced_without_resetting_retry(tmp_path):
    store = RuntimeStateStore(tmp_path / "runtime.json")
    original = presentation_request("a" * 32)
    duplicate = presentation_request(
        "b" * 32,
        requested_at="2026-07-09T10:06:00+00:00",
        origin_theme_mode="night",
        origin_display_commit_id="display-later",
    )

    assert store.request_presentation("one", original) is True
    store.record_attempt(
        "one",
        "2026-07-09T10:01:00+00:00",
        lane=runtime_state.RefreshLane.PRESENTATION,
    )
    store.record_failure(
        "one",
        "2026-07-09T10:02:00+00:00",
        "transient",
        "2026-07-09T10:03:00+00:00",
        lane=runtime_state.RefreshLane.PRESENTATION,
    )
    before_coalesce = store.snapshot()

    assert store.request_presentation("one", duplicate) is False
    assert store.snapshot() is before_coalesce
    state = store.snapshot().instances["one"]
    assert state.presentation_request == original
    assert state.presentation.last_attempt_at == "2026-07-09T10:01:00+00:00"
    assert state.presentation.last_failure_at == "2026-07-09T10:02:00+00:00"
    assert state.presentation.next_retry_at == "2026-07-09T10:03:00+00:00"

    before_wrong_satisfaction = store.snapshot()
    assert (
        store.satisfy_presentation_no_change(
            "one",
            duplicate.request_id,
            "2026-07-09T10:07:00+00:00",
        )
        is False
    )
    assert store.snapshot() is before_wrong_satisfaction
    with pytest.raises(ValueError):
        store.mark_presentation_prepared(
            "one",
            original.request_id,
            "2026-07-09T10:04:00+00:00",
            "dusk",
        )
    with pytest.raises(ValueError):
        store.satisfy_presentation_no_change(
            "one",
            original.request_id,
            "not-an-iso-timestamp",
        )

    assert (
        store.satisfy_presentation_no_change(
            "one",
            original.request_id,
            "2026-07-09T10:07:00+00:00",
        )
        is True
    )
    satisfied = store.snapshot().instances["one"]
    assert satisfied.presentation_request is None
    assert satisfied.presentation_receipt is None
    assert satisfied.presentation.last_success_at == "2026-07-09T10:07:00+00:00"
    assert satisfied.presentation.next_retry_at is None
    assert satisfied.presentation.last_failure_at == "2026-07-09T10:02:00+00:00"


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

    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted["instances"]["one"]["lanes"]["data"][
        "last_failure_at"
    ] is None
    assert len(timers) == 1
    assert timers[0].delay == pytest.approx(4.0)
    assert timers[0].started is True
    assert timers[0].daemon is True

    clock.advance(4)
    timers[0].fire()

    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted["instances"]["one"]["lanes"]["data"][
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
    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == 3


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
