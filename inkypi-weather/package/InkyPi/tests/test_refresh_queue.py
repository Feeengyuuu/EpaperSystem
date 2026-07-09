from __future__ import annotations

import threading
from dataclasses import FrozenInstanceError, replace

import pytest

from src.runtime.refresh_contracts import (
    CommandKind,
    CommandSource,
    JobStatus,
    RefreshCommand,
)
from src.runtime.refresh_queue import (
    DuplicateCommandConflictError,
    IdempotencyConflictError,
    InvalidRefreshCommandError,
    InvalidJobTransitionError,
    QueueFullError,
    QueueStoppingError,
    RefreshQueue,
)


class FakeTime:
    def __init__(self, monotonic: float = 10.0, wall: float = 100.0):
        self.monotonic_value = monotonic
        self.wall_value = wall

    def monotonic(self) -> float:
        return self.monotonic_value

    def wall_time(self) -> float:
        return self.wall_value

    def advance(self, seconds: float, *, wall_seconds: float | None = None) -> None:
        self.monotonic_value += seconds
        self.wall_value += seconds if wall_seconds is None else wall_seconds


def command(
    *,
    kind: CommandKind = CommandKind.DISPLAY,
    source: CommandSource = CommandSource.BACKGROUND,
    plugin_id: str = "plugin",
    instance_uuid: str | None = "instance",
    structural_generation: int | None = 1,
    settings_revision: int | None = 1,
    force: bool = False,
    priority: int = 0,
    idempotency_key: str | None = None,
    payload=None,
    now: float = 10.0,
    deadline: float = 1000.0,
) -> RefreshCommand:
    return RefreshCommand.create(
        kind=kind,
        source=source,
        plugin_id=plugin_id,
        instance_uuid=instance_uuid,
        structural_generation=structural_generation,
        settings_revision=settings_revision,
        force=force,
        priority=priority,
        idempotency_key=idempotency_key,
        payload={} if payload is None else payload,
        now_monotonic=now,
        deadline_monotonic=deadline,
    )


def make_queue(fake_time: FakeTime | None = None, **kwargs) -> RefreshQueue:
    fake_time = fake_time or FakeTime()
    return RefreshQueue(
        clock=fake_time.monotonic,
        wall_clock=fake_time.wall_time,
        **kwargs,
    )


def test_submit_normalizes_direct_payload_and_returns_detached_job_copies():
    fake_time = FakeTime()
    queue = make_queue(fake_time)
    mutable_payload = {"nested": [{"value": "original"}]}
    direct = RefreshCommand(
        id="direct-command",
        kind=CommandKind.DISPLAY,
        source=CommandSource.MANUAL,
        plugin_id="plugin",
        instance_uuid="direct",
        structural_generation=1,
        settings_revision=1,
        force=False,
        priority=1,
        enqueued_monotonic=10.0,
        deadline_monotonic=20.0,
        idempotency_key=None,
        payload=mutable_payload,
    )

    submitted = queue.submit(direct)
    submitted.status = JobStatus.FAILED
    mutable_payload["nested"][0]["value"] = "mutated"

    assert queue.get_job(direct.id).status is JobStatus.QUEUED
    entry = queue.take(timeout=0)
    assert entry is not None
    assert entry.command.payload["nested"][0]["value"] == "original"
    with pytest.raises(TypeError):
        entry.command.payload["nested"][0]["value"] = "again"
    with pytest.raises(FrozenInstanceError):
        entry.command = direct

    entry.job.status = JobStatus.FAILED
    assert queue.get_job(direct.id).status is JobStatus.RUNNING


def test_display_supersedes_cache_and_absorbs_newest_requirements():
    queue = make_queue()
    cache = command(
        kind=CommandKind.CACHE_REFRESH,
        source=CommandSource.SCHEDULER,
        instance_uuid="one",
        settings_revision=5,
        force=True,
        priority=4,
        idempotency_key="cache-key",
        payload={"owner": "cache"},
        deadline=90.0,
    )
    display = command(
        kind=CommandKind.DISPLAY,
        source=CommandSource.BACKGROUND,
        instance_uuid="one",
        settings_revision=3,
        priority=2,
        idempotency_key="display-key",
        payload={"owner": "display"},
        deadline=80.0,
    )

    cache_job = queue.submit(cache)
    display_job = queue.submit(display)

    superseded = queue.get_job(cache_job.id)
    assert superseded.status is JobStatus.SUPERSEDED
    assert superseded.superseded_by == display_job.id
    assert queue.snapshot().superseded_total == 1

    replay = queue.submit(cache)
    assert replay.id == display_job.id
    entry = queue.take(timeout=0)
    assert entry.command.id == display.id
    assert entry.command.kind is CommandKind.DISPLAY
    assert entry.command.settings_revision == 5
    assert entry.command.force is True
    assert entry.command.priority == 4
    assert entry.command.source is CommandSource.SCHEDULER
    assert entry.command.deadline_monotonic == 90.0
    assert entry.command.payload["owner"] == "cache"


def test_cache_reuses_display_and_only_escalates_revision_force_and_deadline():
    queue = make_queue()
    display = command(
        kind=CommandKind.DISPLAY,
        source=CommandSource.MANUAL,
        instance_uuid="one",
        settings_revision=1,
        force=False,
        priority=5,
        payload={"owner": "display"},
        deadline=50.0,
    )
    cache = command(
        kind=CommandKind.CACHE_REFRESH,
        source=CommandSource.BACKGROUND,
        instance_uuid="one",
        settings_revision=2,
        force=True,
        priority=99,
        payload={"owner": "cache"},
        deadline=70.0,
    )

    display_job = queue.submit(display)
    reused = queue.submit(cache)

    assert reused.id == display_job.id
    assert queue.get_job(cache.id) is None
    entry = queue.take(timeout=0)
    assert entry.command.id == display.id
    assert entry.command.kind is CommandKind.DISPLAY
    assert entry.command.settings_revision == 2
    assert entry.command.force is True
    assert entry.command.deadline_monotonic == 70.0
    assert entry.command.payload["owner"] == "cache"
    assert entry.command.priority == 5
    assert entry.command.source is CommandSource.MANUAL


@pytest.mark.parametrize(
    ("display_revision", "payload_owner"),
    [(1, "cache"), (2, "display"), (3, "display")],
)
def test_display_always_supersedes_cache_across_revision_orderings(
    display_revision: int, payload_owner: str
):
    queue = make_queue()
    cache = command(
        kind=CommandKind.CACHE_REFRESH,
        instance_uuid="display-matrix",
        settings_revision=2,
        payload={"owner": "cache"},
    )
    display = command(
        kind=CommandKind.DISPLAY,
        instance_uuid="display-matrix",
        settings_revision=display_revision,
        payload={"owner": "display"},
    )

    cache_job = queue.submit(cache)
    display_job = queue.submit(display)

    assert display_job.id == display.id
    assert queue.get_job(cache_job.id).status is JobStatus.SUPERSEDED
    selected = queue.take(timeout=0).command
    assert selected.kind is CommandKind.DISPLAY
    assert selected.settings_revision == max(2, display_revision)
    assert selected.payload["owner"] == payload_owner


@pytest.mark.parametrize(
    ("cache_revision", "payload_owner"),
    [(1, "display"), (2, "display"), (3, "cache")],
)
def test_cache_always_reuses_display_across_revision_orderings(
    cache_revision: int, payload_owner: str
):
    queue = make_queue()
    display = command(
        kind=CommandKind.DISPLAY,
        instance_uuid="cache-matrix",
        settings_revision=2,
        payload={"owner": "display"},
    )
    cache = command(
        kind=CommandKind.CACHE_REFRESH,
        instance_uuid="cache-matrix",
        settings_revision=cache_revision,
        payload={"owner": "cache"},
    )

    display_job = queue.submit(display)
    actual = queue.submit(cache)

    assert actual.id == display_job.id
    assert queue.get_job(cache.id) is None
    selected = queue.take(timeout=0).command
    assert selected.kind is CommandKind.DISPLAY
    assert selected.settings_revision == max(2, cache_revision)
    assert selected.payload["owner"] == payload_owner


@pytest.mark.parametrize(
    ("incoming_revision", "supersedes"),
    [(1, False), (2, False), (3, True)],
)
def test_same_kind_revision_matrix_preserves_newest_payload(
    incoming_revision: int, supersedes: bool
):
    queue = make_queue()
    existing = command(
        instance_uuid="revisioned",
        settings_revision=2,
        source=CommandSource.BACKGROUND,
        priority=1,
        force=False,
        payload={"owner": "existing"},
        deadline=50.0,
    )
    incoming = command(
        instance_uuid="revisioned",
        settings_revision=incoming_revision,
        source=CommandSource.SCHEDULER,
        priority=5,
        force=True,
        payload={"owner": "incoming"},
        deadline=80.0,
    )

    existing_job = queue.submit(existing)
    actual_job = queue.submit(incoming)

    assert actual_job.id == (incoming.id if supersedes else existing.id)
    if supersedes:
        assert queue.get_job(existing_job.id).status is JobStatus.SUPERSEDED
        assert queue.get_job(existing_job.id).superseded_by == actual_job.id
    else:
        assert queue.get_job(existing_job.id).status is JobStatus.QUEUED

    entry = queue.take(timeout=0)
    assert entry.command.settings_revision == max(2, incoming_revision)
    assert entry.command.force is True
    assert entry.command.priority == 5
    assert entry.command.source is CommandSource.SCHEDULER
    assert entry.command.deadline_monotonic == 80.0
    expected_payload_owner = "incoming" if supersedes else "existing"
    assert entry.command.payload["owner"] == expected_payload_owner


def test_missing_revision_is_older_than_concrete_and_cannot_replace_payload():
    queue = make_queue()
    missing = command(
        instance_uuid="missing",
        settings_revision=None,
        payload={"owner": "missing"},
    )
    concrete = command(
        instance_uuid="missing",
        settings_revision=4,
        payload={"owner": "concrete"},
    )
    late_missing = command(
        instance_uuid="missing",
        settings_revision=None,
        force=True,
        payload={"owner": "late-missing"},
    )

    missing_job = queue.submit(missing)
    concrete_job = queue.submit(concrete)
    assert concrete_job.id == concrete.id
    assert queue.get_job(missing_job.id).status is JobStatus.SUPERSEDED

    reused = queue.submit(late_missing)
    assert reused.id == concrete_job.id
    entry = queue.take(timeout=0)
    assert entry.command.settings_revision == 4
    assert entry.command.force is True
    assert entry.command.payload["owner"] == "concrete"


def test_equal_priority_source_urgency_never_downgrades_reused_job():
    queue = make_queue()
    first = command(
        instance_uuid="urgency",
        source=CommandSource.BACKGROUND,
        priority=7,
    )
    live = command(
        instance_uuid="urgency",
        source=CommandSource.LIVE,
        priority=7,
    )
    scheduler = command(
        instance_uuid="urgency",
        source=CommandSource.SCHEDULER,
        priority=7,
    )
    manual = command(
        instance_uuid="urgency",
        source=CommandSource.MANUAL,
        priority=7,
    )
    background_again = command(
        instance_uuid="urgency",
        source=CommandSource.BACKGROUND,
        priority=7,
    )

    job = queue.submit(first)
    for incoming in (live, scheduler, manual, background_again):
        assert queue.submit(incoming).id == job.id

    assert queue.take(timeout=0).command.source is CommandSource.MANUAL


def test_higher_numeric_priority_owns_source_before_source_tiebreak():
    queue = make_queue()
    manual = command(
        instance_uuid="priority-source",
        source=CommandSource.MANUAL,
        priority=1,
    )
    background = command(
        instance_uuid="priority-source",
        source=CommandSource.BACKGROUND,
        priority=2,
    )

    job = queue.submit(manual)
    assert queue.submit(background).id == job.id
    selected = queue.take(timeout=0).command
    assert selected.priority == 2
    assert selected.source is CommandSource.BACKGROUND


def test_different_generations_and_missing_or_empty_uuids_never_coalesce():
    queue = make_queue(capacity=8, manual_reserved=0)
    commands = [
        command(instance_uuid="same", structural_generation=1),
        command(instance_uuid="same", structural_generation=2),
        command(instance_uuid=None, structural_generation=1),
        command(instance_uuid=None, structural_generation=1),
        command(instance_uuid="", structural_generation=1),
        command(instance_uuid="", structural_generation=1),
    ]

    jobs = [queue.submit(item) for item in commands]

    assert len({job.id for job in jobs}) == len(commands)
    assert queue.snapshot().depth == len(commands)


def test_reserved_slots_accept_only_manual_display_commands():
    queue = make_queue(capacity=4, manual_reserved=1)
    for index in range(3):
        queue.submit(
            command(
                source=CommandSource.BACKGROUND,
                instance_uuid=f"background-{index}",
            )
        )

    with pytest.raises(QueueFullError) as manual_cache_error:
        queue.submit(
            command(
                kind=CommandKind.CACHE_REFRESH,
                source=CommandSource.MANUAL,
                instance_uuid="manual-cache",
            )
        )
    assert manual_cache_error.value.error_code == "refresh_queue_full"

    with pytest.raises(QueueFullError):
        queue.submit(
            command(
                kind=CommandKind.DISPLAY,
                source=CommandSource.SCHEDULER,
                instance_uuid="scheduler-display",
            )
        )

    queue.submit(
        command(
            kind=CommandKind.DISPLAY,
            source=CommandSource.MANUAL,
            instance_uuid="manual-display",
        )
    )
    assert queue.snapshot().depth == 4


def test_constructor_clamps_capacity_and_reserved_range():
    capped = make_queue(capacity=1000, manual_reserved=1000)
    assert capped.capacity == 128
    assert capped.manual_reserved == 128
    with pytest.raises(QueueFullError):
        capped.submit(
            command(
                kind=CommandKind.CACHE_REFRESH,
                source=CommandSource.MANUAL,
                instance_uuid="manual-cache",
            )
        )
    capped.submit(
        command(
            kind=CommandKind.DISPLAY,
            source=CommandSource.MANUAL,
            instance_uuid="manual-display",
        )
    )

    minimum = make_queue(capacity=0, manual_reserved=-10)
    assert minimum.capacity == 1
    assert minimum.manual_reserved == 0

    assert make_queue(alias_limit=10000).alias_limit == 4096
    assert make_queue(alias_limit=0).alias_limit == 1


def test_full_queue_absorbs_merge_and_idempotent_replay_and_running_frees_slot():
    fake_time = FakeTime()
    queue = make_queue(fake_time, capacity=2, manual_reserved=0)
    first = command(instance_uuid="first", settings_revision=2)
    second = command(
        instance_uuid="second",
        settings_revision=1,
        idempotency_key="second-key",
        payload={"value": 2},
    )
    first_job = queue.submit(first)
    second_job = queue.submit(second)
    assert queue.snapshot().depth == 2

    merged = queue.submit(
        command(
            instance_uuid="first",
            settings_revision=1,
            force=True,
            priority=10,
        )
    )
    assert merged.id == first_job.id

    replay = queue.submit(
        command(
            instance_uuid="second",
            settings_revision=1,
            idempotency_key="second-key",
            payload={"value": 2},
            now=900.0,
            deadline=901.0,
        )
    )
    assert replay.id == second_job.id

    running = queue.take(timeout=0)
    assert running.command.id == first_job.id
    assert running.command.force is True
    queue.submit(command(instance_uuid="third"))
    assert queue.snapshot().depth == 2

    queue.finish(running.job.id, JobStatus.SUCCEEDED)
    assert queue.snapshot().depth == 2


def test_idempotency_key_follows_supersession_running_terminal_and_quiesce():
    queue = make_queue()
    original = command(
        instance_uuid="idempotent",
        settings_revision=1,
        idempotency_key="original-key",
        payload={"revision": 1},
    )
    replacement = command(
        instance_uuid="idempotent",
        settings_revision=2,
        idempotency_key="replacement-key",
        payload={"revision": 2},
    )

    original_job = queue.submit(original)
    replacement_job = queue.submit(replacement)
    assert queue.get_job(original_job.id).superseded_by == replacement_job.id

    replay_queued = queue.submit(original)
    assert replay_queued.id == replacement_job.id
    assert replay_queued.status is JobStatus.QUEUED

    entry = queue.take(timeout=0)
    replay_running = queue.submit(original)
    assert replay_running.id == entry.job.id
    assert replay_running.status is JobStatus.RUNNING

    queue.finish(entry.job.id, JobStatus.SUCCEEDED)
    replay_terminal = queue.submit(original)
    assert replay_terminal.id == entry.job.id
    assert replay_terminal.status is JobStatus.SUCCEEDED

    queue.begin_quiesce()
    replay_stopping = queue.submit(original)
    assert replay_stopping.id == entry.job.id
    assert replay_stopping.status is JobStatus.SUCCEEDED


def test_command_id_is_implicit_idempotency_and_conflict_cannot_overwrite_job():
    queue = make_queue()
    original = command(instance_uuid="command-id", payload={"value": "original"})
    submitted = queue.submit(original)
    equivalent = replace(
        original,
        enqueued_monotonic=500.0,
        deadline_monotonic=600.0,
    )

    assert queue.submit(equivalent).id == submitted.id
    assert queue.take(timeout=0).job.id == submitted.id
    assert queue.submit(equivalent).status is JobStatus.RUNNING

    conflicting = replace(original, plugin_id="different-plugin")
    with pytest.raises(DuplicateCommandConflictError) as conflict:
        queue.submit(conflicting)

    assert conflict.value.error_code == "duplicate_command_conflict"
    assert conflict.value.job.id != original.id
    assert conflict.value.job.status is JobStatus.REJECTED
    assert queue.get_job(conflict.value.job.id).status is JobStatus.REJECTED
    assert queue.get_job(original.id).status is JobStatus.RUNNING

    with pytest.raises(DuplicateCommandConflictError):
        queue.submit(replace(original, idempotency_key="changed-key"))
    assert queue.finish(original.id, JobStatus.SUCCEEDED).status is JobStatus.SUCCEEDED


def test_absorbed_command_id_replays_to_actual_job():
    queue = make_queue()
    original = command(instance_uuid="absorbed-id", settings_revision=2)
    absorbed = command(
        instance_uuid="absorbed-id",
        settings_revision=1,
        force=True,
    )
    actual = queue.submit(original)
    assert queue.submit(absorbed).id == actual.id
    assert queue.take(timeout=0).job.id == actual.id

    replay = queue.submit(absorbed)
    assert replay.id == actual.id
    assert replay.status is JobStatus.RUNNING


def test_idempotency_replay_registers_replay_command_id_before_later_conflict():
    queue = make_queue()
    original = command(
        instance_uuid="keyed-command-id",
        idempotency_key="keyed-command-id-key",
        payload={"value": "same"},
    )
    actual = queue.submit(original)
    replay_command = command(
        instance_uuid="keyed-command-id",
        idempotency_key="keyed-command-id-key",
        payload={"value": "same"},
        now=500.0,
        deadline=600.0,
    )
    assert queue.submit(replay_command).id == actual.id

    with pytest.raises(DuplicateCommandConflictError):
        queue.submit(replace(replay_command, plugin_id="conflicting-plugin"))

    assert queue.get_job(actual.id).status is JobStatus.QUEUED


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("enqueued_monotonic", float("nan")),
        ("deadline_monotonic", float("nan")),
        ("deadline_monotonic", float("inf")),
        ("priority", float("nan")),
    ],
)
def test_non_finite_command_values_are_retained_stable_rejections(
    field_name: str, invalid_value: float
):
    queue = make_queue()
    invalid = replace(
        command(instance_uuid=f"invalid-{field_name}"),
        **{field_name: invalid_value},
    )

    with pytest.raises(InvalidRefreshCommandError) as rejection:
        queue.submit(invalid)

    assert rejection.value.error_code == "invalid_refresh_command"
    assert rejection.value.job.status is JobStatus.REJECTED
    assert rejection.value.job.error_code == "invalid_refresh_command"
    assert queue.get_job(rejection.value.job.id).status is JobStatus.REJECTED
    assert queue.snapshot().depth == 0


@pytest.mark.parametrize(
    "changes",
    [
        {"kind": "display"},
        {"source": "manual"},
        {"payload": ["not", "a", "mapping"]},
        {"payload": None},
    ],
)
def test_structurally_invalid_commands_are_retained_stable_rejections(changes):
    queue = make_queue()
    invalid = replace(command(instance_uuid="invalid-structure"), **changes)

    with pytest.raises(InvalidRefreshCommandError) as rejection:
        queue.submit(invalid)

    assert rejection.value.error_code == "invalid_refresh_command"
    assert rejection.value.job.status is JobStatus.REJECTED
    assert queue.get_job(rejection.value.job.id).status is JobStatus.REJECTED
    assert queue.snapshot().depth == 0


def test_idempotency_conflict_is_global_retained_and_precedes_stopping_check():
    queue = make_queue()
    accepted = command(
        plugin_id="one",
        instance_uuid="one",
        idempotency_key="global-key",
        payload={"operation": "original"},
    )
    accepted_job = queue.submit(accepted)

    conflicting = command(
        plugin_id="two",
        instance_uuid="two",
        idempotency_key="global-key",
        payload={"operation": "different"},
    )
    with pytest.raises(IdempotencyConflictError) as conflict_error:
        queue.submit(conflicting)
    assert conflict_error.value.error_code == "idempotency_conflict"
    rejected = queue.get_job(conflict_error.value.job.id)
    assert rejected.status is JobStatus.REJECTED
    assert rejected.error_code == "idempotency_conflict"
    assert rejected.completed_at == 100.0

    assert queue.begin_quiesce() == 1
    assert queue.get_job(accepted_job.id).status is JobStatus.CANCELED

    with pytest.raises(IdempotencyConflictError):
        queue.submit(
            command(
                plugin_id="three",
                instance_uuid="three",
                idempotency_key="global-key",
                payload={"operation": "another-conflict"},
            )
        )

    with pytest.raises(QueueStoppingError) as stopping_error:
        queue.submit(command(instance_uuid="new", idempotency_key="new-key"))
    assert stopping_error.value.error_code == "refresh_service_stopping"
    assert queue.get_job(stopping_error.value.job.id).status is JobStatus.REJECTED
    assert queue.snapshot().rejected_total == 3


def test_three_high_one_lower_fairness_and_fifo_within_priority_bands():
    queue = make_queue(capacity=8, manual_reserved=0)
    high = [command(instance_uuid=f"high-{index}", priority=10) for index in range(5)]
    middle = [
        command(instance_uuid=f"middle-{index}", priority=5) for index in range(2)
    ]
    low = command(instance_uuid="low", priority=1)
    for item in (*high, *middle, low):
        queue.submit(item)

    selected = [queue.take(timeout=0).command.id for _ in range(8)]

    assert selected == [
        high[0].id,
        high[1].id,
        high[2].id,
        middle[0].id,
        high[3].id,
        high[4].id,
        middle[1].id,
        low.id,
    ]


def test_reusing_job_preserves_its_fifo_age():
    queue = make_queue(capacity=4, manual_reserved=0)
    first = command(instance_uuid="first-fifo", priority=5)
    second = command(instance_uuid="second-fifo", priority=5)
    first_job = queue.submit(first)
    queue.submit(second)

    reused = queue.submit(
        command(
            instance_uuid="first-fifo",
            priority=5,
            force=True,
        )
    )

    assert reused.id == first_job.id
    assert queue.take(timeout=0).job.id == first_job.id


def test_cancel_instance_cancels_queued_and_requests_running_once():
    fake_time = FakeTime()
    queue = make_queue(fake_time, capacity=4, manual_reserved=0)
    running_command = command(instance_uuid="target", priority=10)
    queued_command = command(
        instance_uuid="target",
        structural_generation=2,
        priority=1,
    )
    other_command = command(instance_uuid="other", priority=5)
    running_job = queue.submit(running_command)
    queued_job = queue.submit(queued_command)
    other_job = queue.submit(other_command)
    assert queue.take(timeout=0).job.id == running_job.id

    fake_time.advance(5.0)
    assert queue.cancel_instance("target") == 2
    assert queue.cancel_instance("target") == 0

    running = queue.get_job(running_job.id)
    assert running.status is JobStatus.RUNNING
    assert running.cancel_requested_at == 105.0
    queued = queue.get_job(queued_job.id)
    assert queued.status is JobStatus.CANCELED
    assert queued.completed_at == 105.0
    assert queue.get_job(other_job.id).status is JobStatus.QUEUED
    assert queue.snapshot().depth == 1

    assert queue.begin_quiesce() == 1
    assert queue.get_job(other_job.id).status is JobStatus.CANCELED
    assert queue.begin_quiesce() == 0


def test_begin_quiesce_wakes_blocked_take_without_sleep():
    queue = make_queue()
    ready = threading.Event()
    done = threading.Event()
    results = []

    def wait_for_entry() -> None:
        ready.set()
        results.append(queue.take())
        done.set()

    waiter = threading.Thread(target=wait_for_entry)
    waiter.start()
    assert ready.wait(1.0)

    assert queue.begin_quiesce() == 0
    assert done.wait(1.0)
    waiter.join(timeout=1.0)
    assert not waiter.is_alive()
    assert results == [None]
    assert queue.take(timeout=0) is None


def test_timed_take_rechecks_predicate_after_timeout_reacquires_lock():
    timeout_reached = threading.Event()
    producer_done = threading.Event()
    failures = []
    produced = []

    class TimeoutBoundaryCondition(threading.Condition):
        def wait(self, timeout=None):
            saved_state = self._release_save()
            try:
                timeout_reached.set()
                if not producer_done.wait(1.0):
                    raise AssertionError("producer did not enqueue at timeout boundary")
                return False
            finally:
                self._acquire_restore(saved_state)

    queue = make_queue()
    queue._condition = TimeoutBoundaryCondition()

    def produce_at_timeout() -> None:
        try:
            if not timeout_reached.wait(1.0):
                raise AssertionError("take did not enter its timed wait")
            produced.append(queue.submit(command(instance_uuid="timeout-boundary")))
        except BaseException as exc:  # pragma: no cover - asserted below
            failures.append(exc)
        finally:
            producer_done.set()

    producer = threading.Thread(target=produce_at_timeout)
    producer.start()
    entry = queue.take(timeout=1.0)
    producer.join(timeout=1.0)

    assert not failures
    assert not producer.is_alive()
    assert len(produced) == 1
    assert entry is not None
    assert entry.job.id == produced[0].id


def test_begin_quiesce_requests_running_cancel_and_success_finishes_canceled():
    queue = make_queue()
    submitted = queue.submit(command(instance_uuid="running-at-quiesce"))
    assert queue.take(timeout=0).job.id == submitted.id

    assert queue.begin_quiesce() == 1
    requested = queue.get_job(submitted.id)
    assert requested.status is JobStatus.RUNNING
    assert requested.cancel_requested_at == 100.0

    finished = queue.finish(submitted.id, JobStatus.SUCCEEDED)
    assert finished.status is JobStatus.CANCELED
    finished.status = JobStatus.SUCCEEDED
    assert queue.get_job(submitted.id).status is JobStatus.CANCELED


def test_expired_pending_job_is_canceled_but_running_job_can_finish_after_deadline():
    fake_time = FakeTime(monotonic=10.0, wall=100.0)
    queue = make_queue(fake_time, capacity=3, manual_reserved=0)
    expired = command(instance_uuid="expired", priority=100, deadline=10.0)
    valid = command(instance_uuid="valid", priority=1, deadline=20.0)
    expired_job = queue.submit(expired)
    valid_job = queue.submit(valid)

    entry = queue.take(timeout=0)
    assert entry.job.id == valid_job.id
    expired_record = queue.get_job(expired_job.id)
    assert expired_record.status is JobStatus.CANCELED
    assert expired_record.error_code == "deadline_expired"
    assert expired_record.completed_at == 100.0

    fake_time.advance(20.0)
    finished = queue.finish(entry.job.id, JobStatus.SUCCEEDED)
    assert finished.status is JobStatus.SUCCEEDED
    assert finished.completed_at == 120.0


def test_cancel_request_wins_success_finish_race_and_terminal_finish_is_idempotent():
    fake_time = FakeTime()
    queue = make_queue(fake_time)
    submitted = queue.submit(command(instance_uuid="race"))
    entry = queue.take(timeout=0)
    assert entry.job.id == submitted.id
    assert queue.cancel_instance("race") == 1

    fake_time.advance(1.0)
    finished = queue.finish(entry.job.id, JobStatus.SUCCEEDED)
    assert finished.status is JobStatus.CANCELED
    repeated = queue.finish(entry.job.id, JobStatus.SUCCEEDED)
    assert repeated.status is JobStatus.CANCELED

    with pytest.raises(InvalidJobTransitionError) as transition_error:
        queue.finish(
            entry.job.id,
            JobStatus.FAILED,
            error_code="render_failed",
            error="different terminal result",
        )
    assert transition_error.value.error_code == "invalid_job_transition"
    assert queue.get_job(entry.job.id).status is JobStatus.CANCELED


def test_finish_rejects_non_running_unknown_and_conflicting_terminal_transitions():
    queue = make_queue()
    queued = queue.submit(command(instance_uuid="queued"))
    with pytest.raises(InvalidJobTransitionError):
        queue.finish(queued.id, JobStatus.SUCCEEDED)
    with pytest.raises(InvalidJobTransitionError):
        queue.finish("missing-job", JobStatus.SUCCEEDED)

    entry = queue.take(timeout=0)
    failed = queue.finish(
        entry.job.id,
        JobStatus.FAILED,
        error_code="render_failed",
        error="render failed",
    )
    assert failed.status is JobStatus.FAILED
    assert failed.error_code == "render_failed"
    assert failed.error == "render failed"
    assert queue.finish(
        entry.job.id,
        JobStatus.FAILED,
        error_code="render_failed",
        error="render failed",
    ).status is JobStatus.FAILED
    with pytest.raises(InvalidJobTransitionError):
        queue.finish(
            entry.job.id,
            JobStatus.FAILED,
            error_code="different_code",
            error="render failed",
        )


def test_queue_full_rejection_is_retained_and_exception_job_is_detached():
    queue = make_queue(capacity=1, manual_reserved=0)
    queue.submit(command(instance_uuid="occupies-slot"))

    with pytest.raises(QueueFullError) as error:
        queue.submit(command(instance_uuid="rejected"))

    rejected_id = error.value.job.id
    assert error.value.error_code == "refresh_queue_full"
    assert error.value.job.status is JobStatus.REJECTED
    assert error.value.job.completed_at == 100.0
    error.value.job.status = JobStatus.SUCCEEDED
    retained = queue.get_job(rejected_id)
    assert retained.status is JobStatus.REJECTED
    assert retained.error_code == "refresh_queue_full"
    assert queue.snapshot().rejected_total == 1
    assert queue.snapshot().depth == 1


def test_keyed_rejection_replays_until_pruning_then_releases_key():
    fake_time = FakeTime(monotonic=0.0, wall=100.0)
    queue = make_queue(
        fake_time,
        capacity=1,
        manual_reserved=0,
        terminal_limit=10,
        terminal_ttl_seconds=1.0,
    )
    occupant = queue.submit(command(instance_uuid="occupant"))
    rejected_command = command(
        instance_uuid="keyed-rejection",
        idempotency_key="rejected-key",
        payload={"operation": "same"},
    )
    with pytest.raises(QueueFullError) as rejection:
        queue.submit(rejected_command)

    replay = queue.submit(
        command(
            instance_uuid="keyed-rejection",
            idempotency_key="rejected-key",
            payload={"operation": "same"},
            now=500.0,
            deadline=600.0,
        )
    )
    assert replay.id == rejection.value.job.id
    assert replay.status is JobStatus.REJECTED
    assert queue.snapshot().rejected_total == 1

    assert queue.take(timeout=0).job.id == occupant.id
    queue.finish(occupant.id, JobStatus.SUCCEEDED)
    fake_time.advance(2.0)
    queue.snapshot()
    assert queue.get_job(rejection.value.job.id) is None

    admitted = queue.submit(
        command(
            plugin_id="different-plugin",
            instance_uuid="different-instance",
            idempotency_key="rejected-key",
            payload={"operation": "different"},
        )
    )
    assert admitted.status is JobStatus.QUEUED


def test_count_pruning_includes_superseded_and_rejected_history():
    queue = make_queue(
        capacity=1,
        manual_reserved=0,
        terminal_limit=1,
        terminal_ttl_seconds=1000.0,
    )
    original = queue.submit(
        command(instance_uuid="pruned-supersession", settings_revision=1)
    )
    replacement = queue.submit(
        command(instance_uuid="pruned-supersession", settings_revision=2)
    )
    assert queue.get_job(original.id).status is JobStatus.SUPERSEDED

    with pytest.raises(QueueFullError) as rejection:
        queue.submit(command(instance_uuid="pruned-rejection"))

    assert queue.get_job(original.id) is None
    assert queue.get_job(rejection.value.job.id).status is JobStatus.REJECTED
    assert queue.get_job(replacement.id).status is JobStatus.QUEUED


def test_alias_budget_bounds_coalescing_and_target_pruning_releases_aliases():
    fake_time = FakeTime(monotonic=0.0, wall=100.0)
    queue = make_queue(
        fake_time,
        capacity=4,
        manual_reserved=0,
        alias_limit=5,
        terminal_limit=10,
        terminal_ttl_seconds=1.0,
    )
    original = command(instance_uuid="alias-target", settings_revision=2)
    actual = queue.submit(original)
    aliases = [
        command(
            instance_uuid="alias-target",
            settings_revision=1,
            idempotency_key=f"alias-key-{index}",
        )
        for index in range(2)
    ]
    for alias in aliases:
        assert queue.submit(alias).id == actual.id

    with pytest.raises(QueueFullError) as overflow:
        queue.submit(
            command(
                instance_uuid="alias-target",
                settings_revision=1,
                idempotency_key="overflow-key",
            )
        )
    assert overflow.value.error_code == "refresh_queue_full"
    assert queue.get_job(overflow.value.job.id).status is JobStatus.REJECTED
    assert queue.get_job(actual.id).status is JobStatus.QUEUED

    assert queue.submit(aliases[0]).id == actual.id
    fresh_exact_key_replay = command(
        instance_uuid="alias-target",
        settings_revision=1,
        idempotency_key="alias-key-0",
    )
    assert queue.submit(fresh_exact_key_replay).id == actual.id

    entry = queue.take(timeout=0)
    queue.finish(entry.job.id, JobStatus.SUCCEEDED)
    fake_time.advance(2.0)
    queue.snapshot()
    assert queue.get_job(actual.id) is None

    admitted_after_prune = queue.submit(command(instance_uuid="after-prune"))
    assert admitted_after_prune.status is JobStatus.QUEUED


def test_terminal_count_prunes_oldest_records_but_never_active_jobs():
    fake_time = FakeTime(monotonic=0.0, wall=100.0)
    queue = make_queue(
        fake_time,
        capacity=8,
        manual_reserved=0,
        terminal_limit=2,
        terminal_ttl_seconds=1000.0,
    )
    first = queue.submit(command(instance_uuid="terminal-1"))
    assert queue.cancel_instance("terminal-1") == 1
    fake_time.advance(1.0)
    second = queue.submit(command(instance_uuid="terminal-2"))
    assert queue.cancel_instance("terminal-2") == 1

    running = queue.submit(command(instance_uuid="active-running", priority=100))
    queued = queue.submit(command(instance_uuid="active-queued", priority=1))
    assert queue.take(timeout=0).job.id == running.id

    fake_time.advance(1.0)
    third = queue.submit(command(instance_uuid="terminal-3", priority=2))
    assert queue.cancel_instance("terminal-3") == 1

    assert queue.get_job(first.id) is None
    assert queue.get_job(second.id).status is JobStatus.CANCELED
    assert queue.get_job(third.id).status is JobStatus.CANCELED
    assert queue.get_job(running.id).status is JobStatus.RUNNING
    assert queue.get_job(queued.id).status is JobStatus.QUEUED


def test_terminal_ttl_uses_monotonic_time_and_releases_idempotency_key():
    fake_time = FakeTime(monotonic=0.0, wall=100.0)
    queue = make_queue(
        fake_time,
        capacity=4,
        manual_reserved=0,
        terminal_limit=10,
        terminal_ttl_seconds=5.0,
    )
    active = queue.submit(command(instance_uuid="active"))
    terminal_command = command(
        instance_uuid="terminal",
        idempotency_key="released-key",
        payload={"operation": "old"},
    )
    terminal = queue.submit(terminal_command)
    assert queue.cancel_instance("terminal") == 1

    fake_time.advance(6.0, wall_seconds=-50.0)
    queue.snapshot()

    assert queue.get_job(terminal.id) is None
    assert queue.get_job(active.id).status is JobStatus.QUEUED
    admitted = queue.submit(
        command(
            plugin_id="different-plugin",
            instance_uuid="different-instance",
            idempotency_key="released-key",
            payload={"operation": "new"},
        )
    )
    assert admitted.status is JobStatus.QUEUED


def test_concurrent_submit_take_cancel_and_get_are_atomic_without_sleeps():
    queue = make_queue(capacity=8, manual_reserved=0)
    running_command = command(instance_uuid="run", priority=100)
    canceled_command = command(instance_uuid="cancel", priority=50)
    running = queue.submit(running_command)
    canceled = queue.submit(canceled_command)

    barrier = threading.Barrier(5)
    failures = []
    taken = []
    observed = []
    produced = []

    def guard(action) -> None:
        try:
            barrier.wait()
            action()
        except BaseException as exc:  # pragma: no cover - asserted below
            failures.append(exc)

    threads = [
        threading.Thread(
            target=guard,
            args=(lambda: produced.append(queue.submit(command(instance_uuid="new"))),),
        ),
        threading.Thread(
            target=guard,
            args=(lambda: taken.append(queue.take(timeout=0)),),
        ),
        threading.Thread(
            target=guard,
            args=(lambda: queue.cancel_instance("cancel"),),
        ),
        threading.Thread(
            target=guard,
            args=(lambda: observed.append(queue.get_job(running.id)),),
        ),
    ]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=1.0)

    assert not failures
    assert all(not thread.is_alive() for thread in threads)
    assert len(produced) == 1
    assert len(taken) == 1
    assert taken[0].job.id == running.id
    assert observed[0].status in {JobStatus.QUEUED, JobStatus.RUNNING}
    assert queue.get_job(running.id).status is JobStatus.RUNNING
    assert queue.get_job(canceled.id).status is JobStatus.CANCELED
    assert queue.get_job(produced[0].id).status is JobStatus.QUEUED
