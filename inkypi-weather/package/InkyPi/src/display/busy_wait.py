"""Cancelable, bounded polling for e-paper BUSY pins."""

from __future__ import annotations

import math
import time

from runtime.refresh_contracts import TaskContext


DEFAULT_BUSY_TIMEOUT_SECONDS = 90.0
DEFAULT_POLL_INTERVAL_SECONDS = 0.01


class DisplayBusyTimeout(TimeoutError):
    """A display controller did not release its BUSY signal in time."""

    def __init__(self, stage: str, timeout_seconds: float):
        self.stage = stage
        self.timeout_seconds = float(timeout_seconds)
        super().__init__(
            f"{stage} BUSY signal did not clear within "
            f"{self.timeout_seconds:g} seconds; check display power, cabling, and HAT seating"
        )


def _positive_finite(value, name):
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f"{name} must be a positive finite number") from None
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"{name} must be a positive finite number")
    return number


def wait_while_busy(
    read_busy,
    *,
    task_context: TaskContext,
    stage: str,
    timeout_seconds=DEFAULT_BUSY_TIMEOUT_SECONDS,
    poll_interval_seconds=DEFAULT_POLL_INTERVAL_SECONDS,
    clock=time.monotonic,
    sleeper=time.sleep,
):
    """Poll an active-low BUSY signal without spinning or exceeding a deadline."""

    if not callable(read_busy):
        raise TypeError("read_busy must be callable")
    if task_context is None or not callable(
        getattr(task_context, "raise_if_cancelled", None)
    ):
        raise TypeError("task_context must provide raise_if_cancelled()")
    if not isinstance(stage, str) or not stage.strip():
        raise ValueError("stage must be a non-empty string")
    timeout = _positive_finite(timeout_seconds, "timeout_seconds")
    poll_interval = _positive_finite(
        poll_interval_seconds,
        "poll_interval_seconds",
    )
    deadline = min(float(task_context.deadline_monotonic), float(clock()) + timeout)

    while read_busy() == 0:
        task_context.raise_if_cancelled()
        now = float(clock())
        if now >= deadline:
            raise DisplayBusyTimeout(stage.strip(), timeout)
        sleeper(min(poll_interval, deadline - now))
