from __future__ import annotations

import math
import threading
from dataclasses import FrozenInstanceError

import pytest

from src.runtime.refresh_contracts import (
    CommandKind,
    CommandSource,
    JobStatus,
    LifecycleState,
    RefreshCommand,
    TaskCancelled,
    TaskContext,
    TaskDeadlineExceeded,
)
from src.runtime.refresh_queue import QueueStoppingError, RefreshQueue
from src.runtime.render_arbiter import RenderArbiter, ReentrantPluginLeaseError
from src.runtime.scheduler_state import (
    InvalidLifecycleTransition,
    LifecycleController,
    RetryRegistry,
    SchedulerState,
)


class FakeClock:
    def __init__(self, monotonic: float = 10.0, wall: float = 100.0):
        self.monotonic_value = monotonic
        self.wall_value = wall
        self._lock = threading.Lock()

    def monotonic(self) -> float:
        with self._lock:
            return self.monotonic_value

    def wall_time(self) -> float:
        with self._lock:
            return self.wall_value

    def set(self, *, monotonic: float | None = None, wall: float | None = None):
        with self._lock:
            if monotonic is not None:
                self.monotonic_value = monotonic
            if wall is not None:
                self.wall_value = wall


def context(
    *,
    cancel_event: threading.Event | None = None,
    deadline: float = 100.0,
    clock=lambda: 0.0,
) -> TaskContext:
    return TaskContext(cancel_event or threading.Event(), deadline, clock)


def command(instance_uuid: str, *, priority: int = 0) -> RefreshCommand:
    return RefreshCommand.create(
        kind=CommandKind.DISPLAY,
        source=CommandSource.BACKGROUND,
        plugin_id="plugin",
        instance_uuid=instance_uuid,
        structural_generation=1,
        settings_revision=1,
        force=False,
        priority=priority,
        idempotency_key=None,
        payload={},
        now_monotonic=0.0,
        deadline_monotonic=100.0,
    )


class FatalProbe(BaseException):
    pass


@pytest.mark.parametrize("plugin_id", [None, 1, "", " \t "])
def test_render_arbiter_rejects_invalid_plugin_ids(plugin_id):
    arbiter = RenderArbiter()

    with pytest.raises((TypeError, ValueError)):
        with arbiter.lease(plugin_id, context()):
            pass


def test_render_arbiter_serializes_canonical_same_plugin_id_without_sleep():
    arbiter = RenderArbiter()
    holder_entered = threading.Event()
    release_holder = threading.Event()
    waiter_started = threading.Event()
    waiter_entered = threading.Event()
    failures = []

    def holder():
        try:
            with arbiter.lease(" plugin ", context()):
                holder_entered.set()
                assert release_holder.wait(1.0)
        except BaseException as error:  # pragma: no cover - asserted below
            failures.append(error)

    def waiter():
        try:
            waiter_started.set()
            with arbiter.lease("plugin", context()):
                waiter_entered.set()
        except BaseException as error:  # pragma: no cover - asserted below
            failures.append(error)

    holder_thread = threading.Thread(target=holder)
    waiter_thread = threading.Thread(target=waiter)
    holder_thread.start()
    assert holder_entered.wait(1.0)
    waiter_thread.start()
    assert waiter_started.wait(1.0)
    assert not waiter_entered.is_set()

    release_holder.set()
    assert waiter_entered.wait(1.0)
    holder_thread.join(timeout=1.0)
    waiter_thread.join(timeout=1.0)

    assert not holder_thread.is_alive()
    assert not waiter_thread.is_alive()
    assert not failures


def test_render_arbiter_allows_different_plugin_ids_to_overlap():
    arbiter = RenderArbiter()
    simultaneous = threading.Barrier(3)
    failures = []

    def render(plugin_id):
        try:
            with arbiter.lease(plugin_id, context()):
                simultaneous.wait(timeout=1.0)
        except BaseException as error:  # pragma: no cover - asserted below
            failures.append(error)

    threads = [
        threading.Thread(target=render, args=("one",)),
        threading.Thread(target=render, args=("two",)),
    ]
    for thread in threads:
        thread.start()
    simultaneous.wait(timeout=1.0)
    for thread in threads:
        thread.join(timeout=1.0)

    assert all(not thread.is_alive() for thread in threads)
    assert not failures


def test_render_arbiter_rejects_recursive_same_thread_entry_and_recovers():
    arbiter = RenderArbiter()

    with arbiter.lease("plugin", context()):
        with pytest.raises(ReentrantPluginLeaseError):
            with arbiter.lease(" plugin ", context()):
                pass

    with arbiter.lease("plugin", context()):
        pass


def test_render_arbiter_checks_cancellation_before_acquiring_free_lock():
    arbiter = RenderArbiter()
    cancel_event = threading.Event()
    cancel_event.set()

    with pytest.raises(TaskCancelled):
        with arbiter.lease("plugin", context(cancel_event=cancel_event)):
            pass

    with arbiter.lease("plugin", context()):
        pass


def test_render_arbiter_waiter_observes_cancellation_on_bounded_poll():
    arbiter = RenderArbiter()
    holder_entered = threading.Event()
    release_holder = threading.Event()
    waiter_started = threading.Event()
    waiter_done = threading.Event()
    cancel_event = threading.Event()
    failures = []

    def holder():
        with arbiter.lease("plugin", context()):
            holder_entered.set()
            assert release_holder.wait(1.0)

    def waiter():
        try:
            waiter_started.set()
            with arbiter.lease(
                "plugin",
                context(cancel_event=cancel_event, deadline=100.0),
            ):
                failures.append(AssertionError("canceled waiter acquired the lease"))
        except BaseException as error:
            failures.append(error)
        finally:
            waiter_done.set()

    holder_thread = threading.Thread(target=holder)
    waiter_thread = threading.Thread(target=waiter)
    holder_thread.start()
    assert holder_entered.wait(1.0)
    waiter_thread.start()
    assert waiter_started.wait(1.0)

    cancel_event.set()
    assert waiter_done.wait(1.0)
    release_holder.set()
    holder_thread.join(timeout=1.0)
    waiter_thread.join(timeout=1.0)

    assert len(failures) == 1
    assert type(failures[0]) is TaskCancelled


def test_render_arbiter_waiter_classifies_deadline_expiry():
    arbiter = RenderArbiter()
    fake_clock = FakeClock(monotonic=0.0)
    holder_entered = threading.Event()
    release_holder = threading.Event()
    waiter_started = threading.Event()
    waiter_done = threading.Event()
    failures = []

    def holder():
        with arbiter.lease("plugin", context()):
            holder_entered.set()
            assert release_holder.wait(1.0)

    def waiter():
        try:
            waiter_started.set()
            with arbiter.lease(
                "plugin",
                context(deadline=10.0, clock=fake_clock.monotonic),
            ):
                failures.append(AssertionError("expired waiter acquired the lease"))
        except BaseException as error:
            failures.append(error)
        finally:
            waiter_done.set()

    holder_thread = threading.Thread(target=holder)
    waiter_thread = threading.Thread(target=waiter)
    holder_thread.start()
    assert holder_entered.wait(1.0)
    waiter_thread.start()
    assert waiter_started.wait(1.0)

    fake_clock.set(monotonic=10.0)
    assert waiter_done.wait(1.0)
    release_holder.set()
    holder_thread.join(timeout=1.0)
    waiter_thread.join(timeout=1.0)

    assert len(failures) == 1
    assert isinstance(failures[0], TaskDeadlineExceeded)


def test_render_arbiter_releases_lock_when_deadline_arrives_at_acquisition():
    arbiter = RenderArbiter()
    values = iter((9.0, 10.0, 10.0))

    with pytest.raises(TaskDeadlineExceeded):
        with arbiter.lease(
            "plugin",
            context(deadline=10.0, clock=lambda: next(values)),
        ):
            pass

    with arbiter.lease("plugin", context()):
        pass


def test_render_arbiter_releases_lock_and_ownership_on_body_base_exception():
    arbiter = RenderArbiter()

    with pytest.raises(FatalProbe):
        with arbiter.lease("plugin", context()):
            raise FatalProbe()

    with arbiter.lease("plugin", context()):
        pass


class RecordingQueue:
    def __init__(self, affected=3):
        self.affected = affected
        self.accepting = True
        self.calls = 0
        self.events = []

    def begin_quiesce(self):
        self.calls += 1
        self.accepting = False
        self.events.append("queue")
        return self.affected


class BlockingQueue(RecordingQueue):
    def __init__(self, affected=3):
        super().__init__(affected)
        self.entered = threading.Event()
        self.release = threading.Event()

    def begin_quiesce(self):
        self.calls += 1
        self.accepting = False
        self.events.append("queue")
        self.entered.set()
        if not self.release.wait(1.0):
            raise AssertionError("test did not release blocking queue")
        return self.affected


class FailOnceQueue(RecordingQueue):
    def begin_quiesce(self):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("queue close failed")
        self.accepting = False
        self.events.append("queue")
        return self.affected


class RetryCountQueue(RecordingQueue):
    def __init__(self, results):
        super().__init__()
        self._results = iter(results)

    def begin_quiesce(self):
        self.calls += 1
        self.accepting = False
        self.events.append("queue")
        return next(self._results)


class FailOnceEvent(threading.Event):
    def __init__(self):
        super().__init__()
        self.calls = 0

    def set(self):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("stop event failed")
        super().set()


class ToggleFailClock:
    def __init__(self, value):
        self.value = value
        self.fail = False

    def __call__(self):
        if self.fail:
            raise RuntimeError("clock failed")
        return self.value


def test_lifecycle_full_path_is_idempotent_and_preserves_metadata():
    fake_clock = FakeClock(monotonic=1.0, wall=100.0)
    stop_event = threading.Event()
    queue = RecordingQueue(affected=4)
    lifecycle = LifecycleController(
        stop_event,
        queue,
        clock=fake_clock.monotonic,
        wall_clock=fake_clock.wall_time,
    )

    starting = lifecycle.snapshot()
    assert starting.state is LifecycleState.STARTING
    assert starting.changed_at_monotonic == 1.0
    assert starting.changed_at_wall == 100.0
    assert starting.reason is None
    assert starting.queue_cancel_affected == 0

    fake_clock.set(monotonic=2.0, wall=101.0)
    lifecycle.mark_running()
    running = lifecycle.snapshot()
    fake_clock.set(monotonic=3.0, wall=102.0)
    lifecycle.mark_running()
    assert lifecycle.snapshot() == running

    assert lifecycle.begin_quiesce("shutdown") == 4
    quiescing = lifecycle.snapshot()
    fake_clock.set(monotonic=4.0, wall=103.0)
    assert lifecycle.begin_quiesce("ignored") == 4
    assert lifecycle.snapshot() == quiescing
    assert stop_event.is_set()

    lifecycle.begin_draining()
    draining = lifecycle.snapshot()
    fake_clock.set(monotonic=5.0, wall=104.0)
    lifecycle.begin_draining()
    assert lifecycle.snapshot() == draining
    assert draining.reason == "shutdown"
    assert draining.queue_cancel_affected == 4

    lifecycle.mark_stopped()
    stopped = lifecycle.snapshot()
    fake_clock.set(monotonic=6.0, wall=105.0)
    lifecycle.mark_stopped()
    assert lifecycle.snapshot() == stopped
    assert stopped.state is LifecycleState.STOPPED
    assert stopped.reason == "shutdown"


def test_lifecycle_supports_stop_before_startup_completes():
    lifecycle = LifecycleController(threading.Event(), RecordingQueue(affected=2))

    assert lifecycle.begin_quiesce("startup-stop") == 2
    lifecycle.begin_draining()
    lifecycle.mark_stopped()

    assert lifecycle.snapshot().state is LifecycleState.STOPPED
    assert lifecycle.snapshot().reason == "startup-stop"


def test_lifecycle_rejects_skipped_and_terminal_rewrite_transitions():
    lifecycle = LifecycleController(threading.Event(), RecordingQueue())

    with pytest.raises(InvalidLifecycleTransition):
        lifecycle.begin_draining()
    with pytest.raises(InvalidLifecycleTransition):
        lifecycle.mark_stopped()
    with pytest.raises(InvalidLifecycleTransition):
        lifecycle.mark_forced_exit()

    lifecycle.mark_running()
    with pytest.raises(InvalidLifecycleTransition):
        lifecycle.begin_draining()
    lifecycle.begin_quiesce()
    lifecycle.begin_draining()
    lifecycle.mark_stopped()

    with pytest.raises(InvalidLifecycleTransition):
        lifecycle.begin_quiesce()
    with pytest.raises(InvalidLifecycleTransition):
        lifecycle.mark_forced_exit("too-late")


def test_lifecycle_forced_exit_is_idempotent_and_replaces_only_explicit_reason():
    fake_clock = FakeClock(monotonic=1.0, wall=100.0)
    lifecycle = LifecycleController(
        threading.Event(),
        RecordingQueue(),
        clock=fake_clock.monotonic,
        wall_clock=fake_clock.wall_time,
    )
    lifecycle.mark_running()
    lifecycle.begin_quiesce("shutdown")
    lifecycle.begin_draining()
    fake_clock.set(monotonic=5.0, wall=105.0)
    lifecycle.mark_forced_exit("deadline")
    forced = lifecycle.snapshot()

    fake_clock.set(monotonic=6.0, wall=106.0)
    lifecycle.mark_forced_exit("ignored")

    assert lifecycle.snapshot() == forced
    assert forced.reason == "deadline"

    inherited = LifecycleController(threading.Event(), RecordingQueue())
    inherited.mark_running()
    inherited.begin_quiesce("shutdown")
    inherited.begin_draining()
    inherited.mark_forced_exit()
    assert inherited.snapshot().reason == "shutdown"


def test_lifecycle_concurrent_quiesce_has_one_owner_and_blocks_other_transition():
    queue = BlockingQueue(affected=7)
    lifecycle = LifecycleController(threading.Event(), queue)
    results = []
    failures = []
    waiter_started = threading.Event()

    def quiesce(waiter=False):
        try:
            if waiter:
                waiter_started.set()
            results.append(lifecycle.begin_quiesce("first" if not waiter else "second"))
        except BaseException as error:  # pragma: no cover - asserted below
            failures.append(error)

    owner = threading.Thread(target=quiesce)
    waiter = threading.Thread(target=quiesce, kwargs={"waiter": True})
    owner.start()
    assert queue.entered.wait(1.0)

    with pytest.raises(InvalidLifecycleTransition):
        lifecycle.mark_running()
    for invalid_transition in (
        lifecycle.begin_draining,
        lifecycle.mark_stopped,
        lifecycle.mark_forced_exit,
    ):
        with pytest.raises(InvalidLifecycleTransition):
            invalid_transition()

    waiter.start()
    assert waiter_started.wait(1.0)
    queue.release.set()
    owner.join(timeout=1.0)
    waiter.join(timeout=1.0)

    assert not owner.is_alive()
    assert not waiter.is_alive()
    assert not failures
    assert queue.calls == 1
    assert sorted(results) == [7, 7]
    assert lifecycle.snapshot().state is LifecycleState.QUIESCING
    assert lifecycle.snapshot().reason == "first"


def test_lifecycle_orders_queue_then_event_then_quiescing_publication():
    queue = RecordingQueue(affected=1)
    observations = []
    lifecycle_holder = {}

    class ObservingEvent(threading.Event):
        def set(self):
            observations.append(
                (
                    "event",
                    queue.accepting,
                    lifecycle_holder["lifecycle"].state,
                )
            )
            super().set()

    stop_event = ObservingEvent()
    lifecycle = LifecycleController(stop_event, queue)
    lifecycle_holder["lifecycle"] = lifecycle
    lifecycle.mark_running()

    lifecycle.begin_quiesce("ordered")

    assert queue.events == ["queue"]
    assert observations == [("event", False, LifecycleState.RUNNING)]
    assert lifecycle.state is LifecycleState.QUIESCING


def test_lifecycle_recovers_after_queue_closure_failure_without_publishing():
    queue = FailOnceQueue(affected=4)
    stop_event = threading.Event()
    lifecycle = LifecycleController(stop_event, queue)
    lifecycle.mark_running()
    before = lifecycle.snapshot()

    with pytest.raises(RuntimeError, match="queue close failed"):
        lifecycle.begin_quiesce("failed")

    assert lifecycle.snapshot() == before
    assert not stop_event.is_set()
    assert lifecycle.begin_quiesce("retry") == 4
    assert lifecycle.snapshot().state is LifecycleState.QUIESCING
    assert lifecycle.snapshot().reason == "retry"
    assert queue.calls == 2


def test_lifecycle_invalid_queue_count_releases_owner_and_allows_retry():
    queue = RetryCountQueue((None, 0))
    stop_event = threading.Event()
    lifecycle = LifecycleController(stop_event, queue)
    lifecycle.mark_running()
    before = lifecycle.snapshot()

    with pytest.raises(ValueError, match="affected count"):
        lifecycle.begin_quiesce("invalid-count")

    assert lifecycle.snapshot() == before
    assert not stop_event.is_set()
    assert lifecycle.begin_quiesce("retry") == 0
    assert lifecycle.state is LifecycleState.QUIESCING
    assert lifecycle.snapshot().reason == "retry"
    assert queue.calls == 2


def test_lifecycle_event_failure_preserves_affected_count_for_idempotent_retry():
    queue = RetryCountQueue((3, 9))
    stop_event = FailOnceEvent()
    lifecycle = LifecycleController(stop_event, queue)
    lifecycle.mark_running()
    before = lifecycle.snapshot()

    with pytest.raises(RuntimeError, match="stop event failed"):
        lifecycle.begin_quiesce("first")

    assert lifecycle.snapshot() == before
    assert not stop_event.is_set()
    retry_done = threading.Event()
    retry_results = []
    retry_failures = []

    def retry():
        try:
            retry_results.append(lifecycle.begin_quiesce("retry"))
        except BaseException as error:  # pragma: no cover - asserted below
            retry_failures.append(error)
        finally:
            retry_done.set()

    retry_thread = threading.Thread(target=retry, daemon=True)
    retry_thread.start()
    assert retry_done.wait(1.0)
    retry_thread.join(timeout=1.0)

    assert not retry_thread.is_alive()
    assert not retry_failures
    assert retry_results == [3]
    assert lifecycle.snapshot().queue_cancel_affected == 3
    assert lifecycle.snapshot().reason == "first"
    assert stop_event.is_set()
    assert stop_event.calls == 2
    assert queue.calls == 1


def test_lifecycle_zero_count_and_none_reason_survive_event_failure():
    queue = RetryCountQueue((0, 9))
    stop_event = FailOnceEvent()
    lifecycle = LifecycleController(stop_event, queue)
    lifecycle.mark_running()

    with pytest.raises(RuntimeError, match="stop event failed"):
        lifecycle.begin_quiesce(None)

    assert lifecycle.begin_quiesce("retry") == 0
    assert lifecycle.snapshot().queue_cancel_affected == 0
    assert lifecycle.snapshot().reason is None
    assert queue.calls == 1


def test_lifecycle_event_failure_wakes_waiter_that_retries_without_deadlock():
    class BlockingRetryCountQueue(RetryCountQueue):
        def __init__(self):
            super().__init__((4, 9))
            self.entered = threading.Event()
            self.release = threading.Event()

        def begin_quiesce(self):
            result = super().begin_quiesce()
            if self.calls == 1:
                self.entered.set()
                if not self.release.wait(1.0):
                    raise AssertionError("test did not release queue")
            return result

    queue = BlockingRetryCountQueue()
    stop_event = FailOnceEvent()
    lifecycle = LifecycleController(stop_event, queue)
    lifecycle.mark_running()
    waiter_started = threading.Event()
    results = []
    failures = []

    def owner():
        try:
            lifecycle.begin_quiesce("owner")
        except BaseException as error:
            failures.append(error)

    def waiter():
        waiter_started.set()
        try:
            results.append(lifecycle.begin_quiesce("waiter"))
        except BaseException as error:  # pragma: no cover - asserted below
            failures.append(error)

    owner_thread = threading.Thread(target=owner, daemon=True)
    waiter_thread = threading.Thread(target=waiter, daemon=True)
    owner_thread.start()
    assert queue.entered.wait(1.0)
    waiter_thread.start()
    assert waiter_started.wait(1.0)
    queue.release.set()
    owner_thread.join(timeout=1.0)
    waiter_thread.join(timeout=1.0)

    assert not owner_thread.is_alive()
    assert not waiter_thread.is_alive()
    assert results == [4]
    assert len(failures) == 1
    assert str(failures[0]) == "stop event failed"
    assert queue.calls == 1
    assert lifecycle.snapshot().state is LifecycleState.QUIESCING
    assert lifecycle.snapshot().queue_cancel_affected == 4
    assert lifecycle.snapshot().reason == "owner"


def test_lifecycle_mark_running_clock_failure_leaves_snapshot_unchanged():
    monotonic = ToggleFailClock(1.0)
    lifecycle = LifecycleController(
        threading.Event(),
        RecordingQueue(),
        clock=monotonic,
        wall_clock=lambda: 100.0,
    )
    before = lifecycle.snapshot()
    monotonic.fail = True

    with pytest.raises(RuntimeError, match="clock failed"):
        lifecycle.mark_running()

    assert lifecycle.snapshot() == before
    monotonic.fail = False
    monotonic.value = 2.0
    lifecycle.mark_running()
    assert lifecycle.state is LifecycleState.RUNNING


def test_lifecycle_quiesce_clock_failure_releases_owner_and_retries_queue():
    monotonic = ToggleFailClock(1.0)
    queue = RetryCountQueue((5, 9))
    stop_event = threading.Event()
    lifecycle = LifecycleController(
        stop_event,
        queue,
        clock=monotonic,
        wall_clock=lambda: 100.0,
    )
    before = lifecycle.snapshot()
    monotonic.fail = True

    with pytest.raises(RuntimeError, match="clock failed"):
        lifecycle.begin_quiesce("first")

    assert lifecycle.snapshot() == before
    assert not stop_event.is_set()
    monotonic.fail = False
    monotonic.value = 2.0
    assert lifecycle.begin_quiesce("retry") == 5
    assert lifecycle.snapshot().queue_cancel_affected == 5
    assert lifecycle.snapshot().reason == "first"
    assert queue.calls == 1


def test_lifecycle_zero_count_and_none_reason_survive_clock_failure():
    monotonic = ToggleFailClock(1.0)
    queue = RetryCountQueue((0, 9))
    lifecycle = LifecycleController(
        threading.Event(),
        queue,
        clock=monotonic,
        wall_clock=lambda: 100.0,
    )
    monotonic.fail = True

    with pytest.raises(RuntimeError, match="clock failed"):
        lifecycle.begin_quiesce(None)

    monotonic.fail = False
    assert lifecycle.begin_quiesce("retry") == 0
    assert lifecycle.snapshot().queue_cancel_affected == 0
    assert lifecycle.snapshot().reason is None
    assert queue.calls == 1


def test_lifecycle_forced_exit_clock_failure_does_not_publish_reason_or_state():
    monotonic = ToggleFailClock(1.0)
    lifecycle = LifecycleController(
        threading.Event(),
        RecordingQueue(),
        clock=monotonic,
        wall_clock=lambda: 100.0,
    )
    lifecycle.mark_running()
    lifecycle.begin_quiesce("shutdown")
    lifecycle.begin_draining()
    before = lifecycle.snapshot()
    monotonic.fail = True

    with pytest.raises(RuntimeError, match="clock failed"):
        lifecycle.mark_forced_exit("deadline")

    assert lifecycle.snapshot() == before
    monotonic.fail = False
    monotonic.value = 2.0
    lifecycle.mark_forced_exit("deadline")
    assert lifecycle.state is LifecycleState.FORCED_EXIT
    assert lifecycle.snapshot().reason == "deadline"


def test_lifecycle_queue_failure_wakes_waiter_for_a_new_single_owner_attempt():
    class BlockingFailOnceQueue(RecordingQueue):
        def __init__(self):
            super().__init__(affected=5)
            self.entered = threading.Event()
            self.release = threading.Event()

        def begin_quiesce(self):
            self.calls += 1
            if self.calls == 1:
                self.entered.set()
                if not self.release.wait(1.0):
                    raise AssertionError("test did not release failed queue close")
                raise RuntimeError("first close failed")
            self.accepting = False
            return self.affected

    queue = BlockingFailOnceQueue()
    lifecycle = LifecycleController(threading.Event(), queue)
    lifecycle.mark_running()
    results = []
    failures = []

    def quiesce():
        try:
            results.append(lifecycle.begin_quiesce("shutdown"))
        except BaseException as error:
            failures.append(error)

    threads = [threading.Thread(target=quiesce) for _ in range(2)]
    threads[0].start()
    assert queue.entered.wait(1.0)
    threads[1].start()
    queue.release.set()
    for thread in threads:
        thread.join(timeout=1.0)

    assert all(not thread.is_alive() for thread in threads)
    assert queue.calls == 2
    assert results == [5]
    assert len(failures) == 1
    assert str(failures[0]) == "first close failed"
    assert lifecycle.state is LifecycleState.QUIESCING


def test_lifecycle_terminal_race_has_one_winner_and_cannot_be_rewritten():
    lifecycle = LifecycleController(threading.Event(), RecordingQueue())
    lifecycle.mark_running()
    lifecycle.begin_quiesce("shutdown")
    lifecycle.begin_draining()
    barrier = threading.Barrier(3)
    winners = []
    failures = []

    def transition(name, action):
        try:
            barrier.wait(timeout=1.0)
            action()
            winners.append(name)
        except InvalidLifecycleTransition as error:
            failures.append(error)

    threads = [
        threading.Thread(target=transition, args=("stopped", lifecycle.mark_stopped)),
        threading.Thread(
            target=transition,
            args=("forced", lambda: lifecycle.mark_forced_exit("deadline")),
        ),
    ]
    for thread in threads:
        thread.start()
    barrier.wait(timeout=1.0)
    for thread in threads:
        thread.join(timeout=1.0)

    assert all(not thread.is_alive() for thread in threads)
    assert len(winners) == 1
    assert len(failures) == 1
    expected_state = {
        "stopped": LifecycleState.STOPPED,
        "forced": LifecycleState.FORCED_EXIT,
    }[winners[0]]
    assert lifecycle.state is expected_state
    if expected_state is LifecycleState.FORCED_EXIT:
        assert lifecycle.snapshot().reason == "deadline"
    else:
        assert lifecycle.snapshot().reason == "shutdown"


def test_lifecycle_snapshot_is_frozen_and_detached():
    lifecycle = LifecycleController(threading.Event(), RecordingQueue())
    snapshot = lifecycle.snapshot()

    with pytest.raises(FrozenInstanceError):
        snapshot.reason = "mutated"

    lifecycle.mark_running()
    assert snapshot.state is LifecycleState.STARTING


def test_retry_registry_uses_bounded_sequence_and_success_resets_streak():
    retry = RetryRegistry(jitter=lambda delay: delay)

    assert [retry.mark_failure("one", now) for now in (0, 30, 90, 210, 510)] == [
        30,
        60,
        120,
        300,
        300,
    ]
    retry.mark_success("one")

    assert retry.mark_failure("one", 1000) == 30


def test_retry_registry_strips_keys_and_validates_key_and_monotonic_inputs():
    retry = RetryRegistry(jitter=lambda delay: delay)
    retry.mark_failure(" key ", 0.0)

    assert retry.snapshot()[0].key == "key"
    assert retry.next_delay("key", 0.0) == 30.0

    for invalid_key in (None, 1, "", " \t "):
        with pytest.raises((TypeError, ValueError)):
            retry.mark_failure(invalid_key, 0.0)
    for invalid_now in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ValueError):
            retry.mark_failure("finite", invalid_now)
        with pytest.raises(ValueError):
            retry.next_delay("finite", invalid_now)


def test_retry_registry_accepts_jitter_bounds_and_hard_cap():
    low = RetryRegistry(jitter=lambda delay: delay * 0.9)
    high = RetryRegistry(jitter=lambda delay: min(delay * 1.1, 300.0))

    assert [low.mark_failure("low", 0.0) for _ in range(4)] == [27, 54, 108, 270]
    assert [high.mark_failure("high", 0.0) for _ in range(4)] == [33, 66, 132, 300]


def test_retry_registry_default_jitter_respects_bounds_and_cap(monkeypatch):
    monkeypatch.setattr(
        "src.runtime.scheduler_state.random.uniform",
        lambda _low, _high: 1.1,
    )
    retry = RetryRegistry()

    assert [retry.mark_failure("one", 0.0) for _ in range(4)] == [
        33,
        66,
        132,
        300,
    ]


def test_retry_registry_honors_a_falsey_injected_jitter_callable():
    class FalseyJitter:
        def __bool__(self):
            return False

        def __call__(self, delay):
            return delay * 0.9

    retry = RetryRegistry(jitter=FalseyJitter())

    assert retry.mark_failure("one", 0.0) == 27.0


@pytest.mark.parametrize("invalid", [math.nan, math.inf, -1.0, 26.9, 33.1])
def test_retry_registry_invalid_first_tier_jitter_publishes_nothing(invalid):
    retry = RetryRegistry(jitter=lambda _delay: invalid)

    with pytest.raises(ValueError):
        retry.mark_failure("one", 0.0)

    assert retry.snapshot() == ()
    assert retry.consume_manual_bypass("one") is False


def test_retry_registry_rejects_over_cap_jitter_without_partial_fourth_failure():
    values = iter((30.0, 60.0, 120.0, 300.1))
    retry = RetryRegistry(jitter=lambda _delay: next(values))
    for _ in range(3):
        retry.mark_failure("one", 0.0)

    with pytest.raises(ValueError):
        retry.mark_failure("one", 0.0)

    snapshot = retry.snapshot()[0]
    assert snapshot.failure_count == 3
    assert snapshot.deadline_monotonic == 120.0


def test_retry_registry_jitter_exception_publishes_nothing():
    def broken_jitter(_delay):
        raise RuntimeError("jitter failed")

    retry = RetryRegistry(jitter=broken_jitter)

    with pytest.raises(RuntimeError, match="jitter failed"):
        retry.mark_failure("one", 0.0)

    assert retry.snapshot() == ()


def test_retry_registry_next_delay_handles_unknown_due_and_clock_regression():
    retry = RetryRegistry(jitter=lambda delay: delay)

    assert retry.next_delay("unknown", 0.0) == 0.0
    retry.mark_failure("one", 100.0)
    assert retry.next_delay("one", 100.0) == 30.0
    assert retry.next_delay("one", 130.0) == 0.0
    assert retry.next_delay("one", -1000.0) == 300.0


def test_retry_registry_tracks_keys_independently_and_sorts_snapshots():
    retry = RetryRegistry(jitter=lambda delay: delay)
    retry.mark_failure("zeta", 0.0)
    retry.mark_failure("alpha", 10.0)
    retry.mark_failure("zeta", 30.0)

    snapshot = retry.snapshot()

    assert [entry.key for entry in snapshot] == ["alpha", "zeta"]
    assert [entry.failure_count for entry in snapshot] == [1, 2]
    assert [entry.deadline_monotonic for entry in snapshot] == [40.0, 90.0]


def test_retry_registry_manual_bypass_is_once_per_failure_streak():
    retry = RetryRegistry(jitter=lambda delay: delay)
    retry.mark_failure("one", 0.0)

    assert retry.consume_manual_bypass("one") is True
    assert retry.consume_manual_bypass("one") is False
    retry.mark_failure("one", 30.0)
    assert retry.consume_manual_bypass("one") is False

    retry.mark_success("one")
    retry.mark_failure("one", 100.0)
    assert retry.consume_manual_bypass("one") is True


def test_retry_registry_concurrent_failures_publish_one_complete_streak():
    retry = RetryRegistry(jitter=lambda delay: delay)
    barrier = threading.Barrier(6)
    results = []
    failures = []

    def fail():
        try:
            barrier.wait(timeout=1.0)
            results.append(retry.mark_failure("one", 0.0))
        except BaseException as error:  # pragma: no cover - asserted below
            failures.append(error)

    threads = [threading.Thread(target=fail) for _ in range(5)]
    for thread in threads:
        thread.start()
    barrier.wait(timeout=1.0)
    for thread in threads:
        thread.join(timeout=1.0)

    assert all(not thread.is_alive() for thread in threads)
    assert not failures
    assert sorted(results) == [30, 60, 120, 300, 300]
    snapshot = retry.snapshot()[0]
    assert snapshot.failure_count == 5
    assert snapshot.deadline_monotonic == 300.0
    assert snapshot.manual_bypass_available is True


def test_retry_registry_failure_then_waiting_success_leaves_state_cleared():
    jitter_entered = threading.Event()
    release_jitter = threading.Event()
    success_started = threading.Event()
    failures = []

    def blocking_jitter(delay):
        jitter_entered.set()
        if not release_jitter.wait(1.0):
            raise AssertionError("test did not release jitter")
        return delay

    retry = RetryRegistry(jitter=blocking_jitter)

    def fail():
        try:
            retry.mark_failure("one", 0.0)
        except BaseException as error:  # pragma: no cover - asserted below
            failures.append(error)

    def succeed():
        success_started.set()
        retry.mark_success("one")

    failure_thread = threading.Thread(target=fail)
    success_thread = threading.Thread(target=succeed)
    failure_thread.start()
    assert jitter_entered.wait(1.0)
    success_thread.start()
    assert success_started.wait(1.0)
    release_jitter.set()
    failure_thread.join(timeout=1.0)
    success_thread.join(timeout=1.0)

    assert not failure_thread.is_alive()
    assert not success_thread.is_alive()
    assert not failures
    assert retry.snapshot() == ()


def test_retry_registry_success_then_failure_leaves_complete_failure_entry():
    retry = RetryRegistry(jitter=lambda delay: delay)
    retry.mark_failure("one", 0.0)
    retry.mark_success("one")

    retry.mark_failure("one", 100.0)

    assert retry.snapshot()[0].failure_count == 1
    assert retry.snapshot()[0].deadline_monotonic == 130.0


def test_retry_registry_discard_removes_all_state_and_snapshot_is_frozen():
    retry = RetryRegistry(jitter=lambda delay: delay)
    retry.mark_failure("one", 0.0)
    snapshot = retry.snapshot()

    with pytest.raises(FrozenInstanceError):
        snapshot[0].failure_count = 99

    retry.discard("one")
    assert retry.snapshot() == ()
    assert snapshot[0].failure_count == 1


def test_scheduler_state_records_diagnostics_and_retains_failure_after_success():
    fake_clock = FakeClock(monotonic=10.0, wall=100.0)
    retry = RetryRegistry(jitter=lambda delay: delay)
    retry.mark_failure("instance", 10.0)
    scheduler = SchedulerState(
        retry,
        clock=fake_clock.monotonic,
        wall_clock=fake_clock.wall_time,
    )
    scheduler.record_attempt()
    error = RuntimeError("render failed")
    scheduler.record_failure(error)
    scheduler.set_next_attempt(40.0)
    failed = scheduler.snapshot()

    assert failed.last_attempt_monotonic == 10.0
    assert failed.last_attempt_wall == 100.0
    assert failed.last_failure_wall == 100.0
    assert failed.last_error == "render failed"
    assert failed.next_attempt_monotonic == 40.0
    assert failed.retry_entries == retry.snapshot()

    error.args = ("mutated",)
    fake_clock.set(monotonic=20.0, wall=110.0)
    scheduler.record_success()
    scheduler.set_next_attempt(None)
    succeeded = scheduler.snapshot()

    assert succeeded.last_success_wall == 110.0
    assert succeeded.last_failure_wall == 100.0
    assert succeeded.last_error == "render failed"
    assert succeeded.next_attempt_monotonic is None


def test_scheduler_snapshot_is_frozen_and_detached():
    scheduler = SchedulerState(RetryRegistry(jitter=lambda delay: delay))
    snapshot = scheduler.snapshot()

    with pytest.raises(FrozenInstanceError):
        snapshot.last_error = "mutated"

    scheduler.record_attempt()
    assert snapshot.last_attempt_monotonic is None


def test_scheduler_snapshot_does_not_hold_state_lock_while_retry_snapshot_blocks():
    class BlockingRetrySnapshot:
        def __init__(self):
            self.entered = threading.Event()
            self.release = threading.Event()

        def snapshot(self):
            self.entered.set()
            if not self.release.wait(1.0):
                raise AssertionError("test did not release retry snapshot")
            return ()

    retry = BlockingRetrySnapshot()
    scheduler = SchedulerState(retry)
    snapshot_done = threading.Event()
    attempt_done = threading.Event()
    failures = []

    def take_snapshot():
        try:
            scheduler.snapshot()
        except BaseException as error:  # pragma: no cover - asserted below
            failures.append(error)
        finally:
            snapshot_done.set()

    def record_attempt():
        try:
            scheduler.record_attempt()
        except BaseException as error:  # pragma: no cover - asserted below
            failures.append(error)
        finally:
            attempt_done.set()

    snapshot_thread = threading.Thread(target=take_snapshot)
    attempt_thread = threading.Thread(target=record_attempt)
    snapshot_thread.start()
    assert retry.entered.wait(1.0)
    attempt_thread.start()

    assert attempt_done.wait(1.0)
    retry.release.set()
    assert snapshot_done.wait(1.0)
    snapshot_thread.join(timeout=1.0)
    attempt_thread.join(timeout=1.0)

    assert not snapshot_thread.is_alive()
    assert not attempt_thread.is_alive()
    assert not failures


def test_lifecycle_with_real_queue_cancels_queued_and_running_jobs():
    fake_clock = FakeClock(monotonic=0.0, wall=100.0)
    queue = RefreshQueue(
        capacity=4,
        manual_reserved=0,
        clock=fake_clock.monotonic,
        wall_clock=fake_clock.wall_time,
    )
    running = queue.submit(command("running", priority=10))
    queued = queue.submit(command("queued", priority=1))
    assert queue.take(timeout=0).job.id == running.id
    lifecycle = LifecycleController(
        threading.Event(),
        queue,
        clock=fake_clock.monotonic,
        wall_clock=fake_clock.wall_time,
    )
    lifecycle.mark_running()

    assert lifecycle.begin_quiesce("shutdown") == 2
    snapshot = lifecycle.snapshot()

    assert snapshot.queue_cancel_affected == 2
    assert queue.get_job(queued.id).status is JobStatus.CANCELED
    assert queue.get_job(running.id).cancel_requested_at == 100.0
    assert queue.finish(running.id, JobStatus.SUCCEEDED).status is JobStatus.CANCELED
    assert queue.snapshot().accepting is False
    with pytest.raises(QueueStoppingError):
        queue.submit(command("new"))


def test_lifecycle_shared_stop_event_cancels_render_arbiter_waiter():
    arbiter = RenderArbiter()
    queue = RefreshQueue(clock=lambda: 0.0, wall_clock=lambda: 100.0)
    stop_event = threading.Event()
    lifecycle = LifecycleController(
        stop_event,
        queue,
        clock=lambda: 0.0,
        wall_clock=lambda: 100.0,
    )
    lifecycle.mark_running()
    holder_entered = threading.Event()
    release_holder = threading.Event()
    waiter_started = threading.Event()
    waiter_done = threading.Event()
    failures = []

    def holder():
        with arbiter.lease("plugin", context()):
            holder_entered.set()
            assert release_holder.wait(1.0)

    def waiter():
        try:
            waiter_started.set()
            with arbiter.lease(
                "plugin",
                context(cancel_event=stop_event, deadline=100.0),
            ):
                failures.append(AssertionError("stopping waiter acquired lease"))
        except BaseException as error:
            failures.append(error)
        finally:
            waiter_done.set()

    holder_thread = threading.Thread(target=holder)
    waiter_thread = threading.Thread(target=waiter)
    holder_thread.start()
    assert holder_entered.wait(1.0)
    waiter_thread.start()
    assert waiter_started.wait(1.0)

    lifecycle.begin_quiesce("shutdown")
    assert waiter_done.wait(1.0)
    release_holder.set()
    holder_thread.join(timeout=1.0)
    waiter_thread.join(timeout=1.0)

    assert len(failures) == 1
    assert type(failures[0]) is TaskCancelled
    assert queue.snapshot().accepting is False
