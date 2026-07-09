from __future__ import annotations

from dataclasses import dataclass, replace
import math
import random
import threading
import time

from .refresh_contracts import LifecycleState


class InvalidLifecycleTransition(RuntimeError):
    """Raised when a lifecycle transition skips or rewrites a state."""


@dataclass(frozen=True)
class LifecycleSnapshot:
    state: LifecycleState
    changed_at_monotonic: float
    changed_at_wall: float
    reason: str | None
    queue_cancel_affected: int


class LifecycleController:
    """Coordinate queue closure and publish a monotonic shutdown lifecycle."""

    def __init__(
        self,
        stop_event,
        refresh_queue,
        *,
        clock=time.monotonic,
        wall_clock=time.time,
    ):
        self._stop_event = stop_event
        self._refresh_queue = refresh_queue
        self._clock = clock
        self._wall_clock = wall_clock
        self._condition = threading.Condition()
        self._state = LifecycleState.STARTING
        self._changed_at_monotonic = self._clock()
        self._changed_at_wall = self._wall_clock()
        self._reason: str | None = None
        self._queue_cancel_affected = 0
        self._quiesce_in_progress = False

    @property
    def state(self) -> LifecycleState:
        with self._condition:
            return self._state

    def snapshot(self) -> LifecycleSnapshot:
        with self._condition:
            return self._snapshot_locked()

    def mark_running(self) -> None:
        with self._condition:
            self._reject_during_quiesce_locked()
            if self._state is LifecycleState.RUNNING:
                return
            self._require_state_locked(LifecycleState.STARTING, "mark running")
            self._publish_locked(LifecycleState.RUNNING)

    def begin_quiesce(self, reason: str | None = None) -> int:
        while True:
            with self._condition:
                if self._state is LifecycleState.QUIESCING:
                    return self._queue_cancel_affected
                if self._state not in {
                    LifecycleState.STARTING,
                    LifecycleState.RUNNING,
                }:
                    raise InvalidLifecycleTransition(f"cannot begin quiesce from {self._state.value}")
                if not self._quiesce_in_progress:
                    self._quiesce_in_progress = True
                    break
                self._condition.wait()

        try:
            affected = self._refresh_queue.begin_quiesce()
        except BaseException:
            with self._condition:
                self._quiesce_in_progress = False
                self._condition.notify_all()
            raise

        self._stop_event.set()
        with self._condition:
            self._queue_cancel_affected = int(affected)
            self._reason = reason
            self._publish_locked(LifecycleState.QUIESCING)
            self._quiesce_in_progress = False
            self._condition.notify_all()
            return self._queue_cancel_affected

    def begin_draining(self) -> None:
        with self._condition:
            self._reject_during_quiesce_locked()
            if self._state is LifecycleState.DRAINING:
                return
            self._require_state_locked(LifecycleState.QUIESCING, "begin draining")
            self._publish_locked(LifecycleState.DRAINING)

    def mark_stopped(self) -> None:
        with self._condition:
            self._reject_during_quiesce_locked()
            if self._state is LifecycleState.STOPPED:
                return
            self._require_state_locked(LifecycleState.DRAINING, "mark stopped")
            self._publish_locked(LifecycleState.STOPPED)

    def mark_forced_exit(self, reason: str | None = None) -> None:
        with self._condition:
            self._reject_during_quiesce_locked()
            if self._state is LifecycleState.FORCED_EXIT:
                return
            self._require_state_locked(LifecycleState.DRAINING, "mark forced exit")
            if reason is not None:
                self._reason = reason
            self._publish_locked(LifecycleState.FORCED_EXIT)

    def _snapshot_locked(self) -> LifecycleSnapshot:
        return LifecycleSnapshot(
            state=self._state,
            changed_at_monotonic=self._changed_at_monotonic,
            changed_at_wall=self._changed_at_wall,
            reason=self._reason,
            queue_cancel_affected=self._queue_cancel_affected,
        )

    def _publish_locked(self, state: LifecycleState) -> None:
        self._state = state
        self._changed_at_monotonic = self._clock()
        self._changed_at_wall = self._wall_clock()

    def _reject_during_quiesce_locked(self) -> None:
        if self._quiesce_in_progress:
            raise InvalidLifecycleTransition("quiesce transition is in progress")

    def _require_state_locked(self, expected: LifecycleState, action: str) -> None:
        if self._state is not expected:
            raise InvalidLifecycleTransition(f"cannot {action} from {self._state.value}")


@dataclass(frozen=True)
class RetryEntrySnapshot:
    key: str
    failure_count: int
    deadline_monotonic: float
    manual_bypass_available: bool


@dataclass(frozen=True)
class _RetryState:
    failure_count: int
    deadline_monotonic: float
    manual_bypass_available: bool


class RetryRegistry:
    """Track bounded per-instance retry streaks under one short lock."""

    GLOBAL_KEY = "__scheduler__"
    DELAYS = (30.0, 60.0, 120.0, 300.0)
    MAX_DELAY = 300.0

    def __init__(self, jitter=None):
        self._jitter = self._default_jitter if jitter is None else jitter
        self._lock = threading.Lock()
        self._entries: dict[str, _RetryState] = {}

    def mark_failure(self, key, now_monotonic) -> float:
        canonical_key = self._canonical_key(key)
        now = self._finite_monotonic(now_monotonic)
        with self._lock:
            previous = self._entries.get(canonical_key)
            failure_count = 1 if previous is None else previous.failure_count + 1
            base_delay = self.DELAYS[min(failure_count - 1, len(self.DELAYS) - 1)]
            delay = self._validated_jitter(base_delay)
            deadline = now + delay
            if not math.isfinite(deadline):
                raise ValueError("retry deadline must be finite")
            manual_bypass_available = True if previous is None else previous.manual_bypass_available
            self._entries[canonical_key] = _RetryState(
                failure_count=failure_count,
                deadline_monotonic=deadline,
                manual_bypass_available=manual_bypass_available,
            )
            return delay

    def next_delay(self, key, now_monotonic) -> float:
        canonical_key = self._canonical_key(key)
        now = self._finite_monotonic(now_monotonic)
        with self._lock:
            entry = self._entries.get(canonical_key)
            if entry is None:
                return 0.0
            remaining = entry.deadline_monotonic - now
            if remaining <= 0:
                return 0.0
            return min(self.MAX_DELAY, remaining)

    def mark_success(self, key) -> None:
        canonical_key = self._canonical_key(key)
        with self._lock:
            self._entries.pop(canonical_key, None)

    def discard(self, key) -> None:
        canonical_key = self._canonical_key(key)
        with self._lock:
            self._entries.pop(canonical_key, None)

    def consume_manual_bypass(self, key) -> bool:
        canonical_key = self._canonical_key(key)
        with self._lock:
            entry = self._entries.get(canonical_key)
            if entry is None or not entry.manual_bypass_available:
                return False
            self._entries[canonical_key] = replace(
                entry,
                manual_bypass_available=False,
            )
            return True

    def snapshot(self) -> tuple[RetryEntrySnapshot, ...]:
        with self._lock:
            return tuple(
                RetryEntrySnapshot(
                    key=key,
                    failure_count=entry.failure_count,
                    deadline_monotonic=entry.deadline_monotonic,
                    manual_bypass_available=entry.manual_bypass_available,
                )
                for key, entry in sorted(self._entries.items())
            )

    def _validated_jitter(self, base_delay: float) -> float:
        candidate = self._jitter(base_delay)
        try:
            delay = float(candidate)
        except (TypeError, ValueError, OverflowError) as error:
            raise ValueError("retry jitter must be a finite number") from error
        lower = base_delay * 0.9
        upper = min(base_delay * 1.1, self.MAX_DELAY)
        if not math.isfinite(delay) or delay < lower or delay > upper:
            raise ValueError(f"retry jitter must be between {lower} and {upper} seconds")
        return delay

    @classmethod
    def _default_jitter(cls, base_delay: float) -> float:
        return min(cls.MAX_DELAY, base_delay * random.uniform(0.9, 1.1))

    @staticmethod
    def _canonical_key(key) -> str:
        if not isinstance(key, str):
            raise TypeError("retry key must be a string")
        canonical = key.strip()
        if not canonical:
            raise ValueError("retry key must not be empty")
        return canonical

    @staticmethod
    def _finite_monotonic(value) -> float:
        try:
            result = float(value)
        except (TypeError, ValueError, OverflowError) as error:
            raise ValueError("monotonic time must be finite") from error
        if not math.isfinite(result):
            raise ValueError("monotonic time must be finite")
        return result


@dataclass(frozen=True)
class SchedulerSnapshot:
    last_attempt_monotonic: float | None
    last_attempt_wall: float | None
    last_success_wall: float | None
    last_failure_wall: float | None
    last_error: str | None
    next_attempt_monotonic: float | None
    retry_entries: tuple[RetryEntrySnapshot, ...]


class SchedulerState:
    """Publish immutable scheduler diagnostics without nested component locks."""

    def __init__(
        self,
        retry_registry,
        *,
        clock=time.monotonic,
        wall_clock=time.time,
    ):
        self._retry_registry = retry_registry
        self._clock = clock
        self._wall_clock = wall_clock
        self._lock = threading.Lock()
        self._last_attempt_monotonic: float | None = None
        self._last_attempt_wall: float | None = None
        self._last_success_wall: float | None = None
        self._last_failure_wall: float | None = None
        self._last_error: str | None = None
        self._next_attempt_monotonic: float | None = None

    def record_attempt(self) -> None:
        monotonic = self._clock()
        wall = self._wall_clock()
        with self._lock:
            self._last_attempt_monotonic = monotonic
            self._last_attempt_wall = wall

    def record_success(self) -> None:
        wall = self._wall_clock()
        with self._lock:
            self._last_success_wall = wall

    def record_failure(self, error) -> None:
        error_text = str(error)
        wall = self._wall_clock()
        with self._lock:
            self._last_failure_wall = wall
            self._last_error = error_text

    def set_next_attempt(self, value_or_none) -> None:
        value = self._optional_finite_monotonic(value_or_none)
        with self._lock:
            self._next_attempt_monotonic = value

    def snapshot(self) -> SchedulerSnapshot:
        with self._lock:
            fields = (
                self._last_attempt_monotonic,
                self._last_attempt_wall,
                self._last_success_wall,
                self._last_failure_wall,
                self._last_error,
                self._next_attempt_monotonic,
            )
        retry_entries = self._retry_registry.snapshot()
        return SchedulerSnapshot(*fields, retry_entries=retry_entries)

    @staticmethod
    def _optional_finite_monotonic(value):
        if value is None:
            return None
        try:
            result = float(value)
        except (TypeError, ValueError, OverflowError) as error:
            raise ValueError("next attempt monotonic time must be finite") from error
        if not math.isfinite(result):
            raise ValueError("next attempt monotonic time must be finite")
        return result
