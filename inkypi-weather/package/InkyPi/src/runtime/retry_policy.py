"""Retry timing policy, separate from mutable lane bookkeeping and admission."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import math

from .refresh_contracts import RefreshIntent


MAX_SOURCE_RETRY_SECONDS = 15 * 60


@dataclass(frozen=True)
class RetryPolicy:
    delays: tuple[float, ...] = (30.0, 60.0, 120.0, 300.0)

    def __post_init__(self):
        if (
            not isinstance(self.delays, tuple)
            or not self.delays
            or any(
                isinstance(v, bool)
                or not isinstance(v, (int, float))
                or not math.isfinite(v)
                or not 0 < v <= MAX_SOURCE_RETRY_SECONDS
                for v in self.delays
            )
            or tuple(sorted(self.delays)) != self.delays
        ):
            raise ValueError("retry delays must be ordered finite seconds in (0, 900]")

    @property
    def maximum_delay(self) -> float:
        return self.delays[-1]

    def delay_for_failure(self, failure_count: int) -> float:
        if type(failure_count) is not int or failure_count < 1:
            raise ValueError("failure_count must be a positive integer")
        return self.delays[min(failure_count - 1, len(self.delays) - 1)]


DEFAULT_RETRY_POLICY = RetryPolicy()
_SOURCE_DELAYS = (30.0, 60.0, 120.0, 300.0, 600.0, 900.0)
_SLOW_SOURCE_PLUGINS = frozenset({"apod", "ticketmaster_events"})


def source_retry_policy(command) -> RetryPolicy:
    """Bound sustained DATA failures by fifteen minutes and the saved cadence.

    Live scores, rotation, resource deferrals and unrelated plugins keep their
    existing recovery timing. The first four attempts stay responsive.
    """
    if command.intent is not RefreshIntent.DATA_REFRESH or command.plugin_id not in _SLOW_SOURCE_PLUGINS:
        return DEFAULT_RETRY_POLICY
    refresh = command.payload.get("refresh")
    value = refresh.get("interval") if isinstance(refresh, Mapping) else None
    maximum = float(MAX_SOURCE_RETRY_SECONDS)
    if not isinstance(value, bool):
        try:
            interval = float(value)
        except (TypeError, ValueError, OverflowError):
            interval = 0.0
        if math.isfinite(interval) and interval > 0:
            maximum = min(maximum, interval)
    return RetryPolicy(tuple(min(delay, maximum) for delay in _SOURCE_DELAYS))
