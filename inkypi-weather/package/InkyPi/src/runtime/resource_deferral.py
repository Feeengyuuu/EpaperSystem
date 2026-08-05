"""Typed cancellation for retryable work deferred by resource pressure."""

from __future__ import annotations

from .refresh_contracts import TaskCancelled


class ResourcePressureDeferred(TaskCancelled):
    """Signal that work should be retried after transient pressure clears."""

    def __init__(
        self,
        *,
        reason,
        phase,
        available_mb=None,
        swap_percent=None,
    ):
        self.reason = str(reason)
        self.phase = str(phase)
        self.available_mb = available_mb
        self.swap_percent = swap_percent
        super().__init__(
            f"{self.reason} during {self.phase} "
            f"(available_mb={self.available_mb}, swap_percent={self.swap_percent})"
        )
