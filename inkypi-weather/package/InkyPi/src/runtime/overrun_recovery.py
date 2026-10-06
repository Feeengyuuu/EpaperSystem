"""Bound a refresh worker that keeps running long after its command deadline.

Cancellation is cooperative: plugin code that swallows ``TaskCancelled`` or
blocks outside deadline-aware I/O can hold the single refresh worker far past
its deadline, freezing rotation while health only reports ``scheduler_stalled``.
Python cannot stop that thread, so a sustained overrun requests the same
supervised process replacement already used for leaked isolated children.
The persisted recovery ledger enforces a minimum interval, so a deterministic
overrun cannot turn into a restart loop.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import math
import time
from typing import Any, Callable, Iterable, Mapping

logger = logging.getLogger(__name__)

OVERRUN_REASON = "refresh_worker_overrun"
DEFAULT_GRACE_SECONDS = 240.0
DEFAULT_MIN_INTERVAL_SECONDS = 3600.0


def _bounded_float(device_config: Any, key: str, default: float, low: float, high: float) -> float:
    getter = getattr(device_config, "get_config", None)
    if not callable(getter):
        return default
    try:
        value = float(getter(key, default=default))
    except (TypeError, ValueError, OverflowError):
        return default
    if not math.isfinite(value):
        return default
    return max(low, min(high, value))


@dataclass(frozen=True)
class OverrunPolicy:
    enabled: bool = True
    grace_seconds: float = DEFAULT_GRACE_SECONDS
    min_interval_seconds: float = DEFAULT_MIN_INTERVAL_SECONDS

    @classmethod
    def from_config(cls, device_config: Any) -> "OverrunPolicy":
        getter = getattr(device_config, "get_config", None)
        enabled = True
        if callable(getter):
            enabled = getter("refresh_overrun_recovery_enabled", default=True) is not False
        return cls(
            enabled=enabled,
            grace_seconds=_bounded_float(
                device_config, "refresh_overrun_grace_seconds",
                DEFAULT_GRACE_SECONDS, 60.0, 3600.0,
            ),
            min_interval_seconds=_bounded_float(
                device_config, "refresh_overrun_min_interval_seconds",
                DEFAULT_MIN_INTERVAL_SECONDS, 300.0, 86_400.0,
            ),
        )


def _event_epoch(event: Mapping[str, Any]) -> float | None:
    try:
        stamp = datetime.fromisoformat(str(event.get("at")).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.timestamp()


class WorkerOverrunGuard:
    """Decide, from the restart monitor thread, when an overrun needs recovery."""

    def __init__(
        self,
        policy: OverrunPolicy,
        *,
        history: Callable[[], Iterable[Mapping[str, Any]]] = lambda: (),
        wall_clock: Callable[[], float] = time.time,
    ):
        self.policy = policy
        self._history = history
        self._wall_clock = wall_clock
        self._suppressed_command_id = None

    def _recently_recovered(self) -> bool:
        now = self._wall_clock()
        for event in self._history():
            if not isinstance(event, Mapping) or event.get("reason") != OVERRUN_REASON:
                continue
            at = _event_epoch(event)
            if at is not None and 0 <= now - at < self.policy.min_interval_seconds:
                return True
        return False

    def evaluate(self, active: Any, overrun_seconds: float | None) -> dict | None:
        if not self.policy.enabled or active is None or overrun_seconds is None:
            return None
        if overrun_seconds < self.policy.grace_seconds:
            return None
        if active.command_id == self._suppressed_command_id:
            return None
        if self._recently_recovered():
            self._suppressed_command_id = active.command_id
            logger.error(
                "Refresh worker overran its deadline, but a recent overrun "
                "recovery suppresses another restart. | plugin_id: %s | "
                "overrun_seconds: %.1f",
                active.plugin_id,
                overrun_seconds,
            )
            return None
        return {
            "reason": OVERRUN_REASON,
            "plugin_id": active.plugin_id,
            "intent": active.intent,
            "overrun_seconds": round(float(overrun_seconds), 1),
        }


def guard_for_refresh_task(refresh_task: Any) -> WorkerOverrunGuard | None:
    """Build a guard only for tasks that publish overrun state."""

    required = ("active_operation_snapshot", "active_operation_overrun_seconds", "stage_restart_request")
    if not all(callable(getattr(refresh_task, name, None)) for name in required):
        return None
    from runtime.runtime_status import recovery_events

    device_config = getattr(refresh_task, "device_config", None)
    data_dir = getattr(getattr(device_config, "runtime_paths", None), "data_dir", None)
    return WorkerOverrunGuard(
        OverrunPolicy.from_config(device_config),
        history=lambda: recovery_events(data_dir),
    )


def stage_worker_overrun(guard: WorkerOverrunGuard | None, refresh_task: Any) -> dict | None:
    """Stage supervised replacement for a sustained overrun, if one exists."""

    if guard is None:
        return None
    request = guard.evaluate(
        refresh_task.active_operation_snapshot(),
        refresh_task.active_operation_overrun_seconds(),
    )
    if request is None:
        return None
    logger.error(
        "Refresh worker overran its deadline; requesting supervised process "
        "replacement. | plugin_id: %s | intent: %s | overrun_seconds: %.1f",
        request["plugin_id"],
        request["intent"],
        request["overrun_seconds"],
    )
    return refresh_task.stage_restart_request(request)
