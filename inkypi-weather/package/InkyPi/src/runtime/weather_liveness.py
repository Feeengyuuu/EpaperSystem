"""Bounded quiet window for a due Weather browser start.

Weather needs a Chromium start margin. When ordinary work keeps memory below it,
one overdue Weather instance may reserve a bounded quiet window; if the normal
margin never appears, the window ends with a single concession attempt or a
cooldown. Unlike StarvationLiveness, the window also ends when the provider fails,
the target's identity changes, or a refresh completes, and its decision returns
(target, hold_independent, concession) for the coordinator's selection.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
import hashlib
import logging
from typing import Any, Callable, Mapping

logger = logging.getLogger(__name__)

WINDOW_KEY = "weather_liveness_window_seconds"
WINDOW_MAX_SECONDS = 90
COOLDOWN_KEY = "weather_liveness_cooldown_seconds"
COOLDOWN_MAX_SECONDS = 60 * 60


@dataclass(frozen=True)
class WeatherWindow:
    instance_uuid: str
    due_since: datetime
    started_monotonic: float
    deadline_monotonic: float
    candidate: Any
    last_failure_at: str | None = None


def _uuid_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


class WeatherQuietWindow:
    """Own the Weather quiet window and its cooldown across scheduler turns."""

    def __init__(
        self,
        *,
        clock: Callable[[], float],
        seconds: Callable[[str, float, float], float],
        window_default: float,
        cooldown_default: float,
        on_finish: Callable[[WeatherWindow, float, bool], None],
    ):
        self._clock = clock
        self._seconds = seconds
        self._window_default = window_default
        self._cooldown_default = cooldown_default
        self._on_finish = on_finish
        self.window: WeatherWindow | None = None
        self.cooldown_until_monotonic = 0.0

    def finish(self, *, reason, resource_sample, yield_to_ordinary=True) -> None:
        window = self.window
        if window is None:
            return
        now = self._clock()
        cooldown_seconds = self._seconds(COOLDOWN_KEY, self._cooldown_default, COOLDOWN_MAX_SECONDS)
        self.window = None
        self.cooldown_until_monotonic = now + cooldown_seconds
        self._on_finish(window, now, yield_to_ordinary)
        handoff = (
            "ordinary background data gets the next bounded admission turn"
            if yield_to_ordinary
            else "runnable auxiliary background work may proceed"
        )
        logger.warning(
            "Weather quiet window ended; %s. | reason: %s | instance_uuid_hash: %s | "
            "window_seconds: %.1f | cooldown_seconds: %.1f | available_mb: %s | "
            "swap_percent: %s",
            handoff,
            reason,
            _uuid_hash(window.instance_uuid),
            max(0.0, window.deadline_monotonic - window.started_monotonic),
            cooldown_seconds,
            getattr(resource_sample, "available_mb", None),
            getattr(resource_sample, "swap_percent", None),
        )

    def decide(
        self,
        *,
        active_weather: Mapping[str, Any],
        candidates_by_uuid: Mapping[str, Any],
        runtime_data: Callable[[str], Any],
        current_dt: datetime,
        resource_sample: Any,
        start_margin: Callable[[Any], tuple[bool, Any, Any]],
        concession_margin: Callable[[Any], tuple[bool, Any]],
        parse_time: Callable[[Any], datetime | None],
        align: Callable[[datetime, datetime], datetime],
        before_window: Callable[[], Any],
    ):
        """Return (target, hold_independent, concession) for this scheduler turn."""

        now = self._clock()
        normal_margin, required_mb, max_swap = start_margin(resource_sample)
        concession_ok, _ = concession_margin(resource_sample)
        if self.window is not None:
            return self._continue(
                now, active_weather, candidates_by_uuid, runtime_data, current_dt,
                resource_sample, normal_margin, concession_ok, parse_time, align,
            )

        weather_candidates = sorted(
            candidates_by_uuid.values(),
            key=lambda candidate: (align(candidate.due_since, current_dt), candidate.instance.instance_uuid),
        )
        target = weather_candidates[0] if weather_candidates else None
        if target is None:
            return None, False, False
        if normal_margin:
            # With no quiet window, Weather participates in the ordinary DATA
            # ordering. The liveness path must not grant an unnecessary
            # priority boost merely because its start margin is healthy.
            return None, False, False
        if now < self.cooldown_until_monotonic:
            return None, False, False
        # A quiet window is useful only when a bounded start could eventually
        # be safe. Unknown metrics or less than 140 MiB never hold other work.
        if not concession_ok:
            return None, False, False
        window_seconds = self._seconds(WINDOW_KEY, self._window_default, WINDOW_MAX_SECONDS)
        if window_seconds <= 0:
            return None, False, False
        # A detached-image cleanup can restore the browser margin immediately.
        # Use the post-maintenance sample instead of spending a quiet window
        # waiting on memory that the scheduler has already reclaimed.
        resource_sample = before_window()
        if start_margin(resource_sample)[0]:
            return target, False, False
        if not concession_margin(resource_sample)[0]:
            return None, False, False
        due_since = align(target.due_since, current_dt)
        self.window = WeatherWindow(
            instance_uuid=target.instance.instance_uuid,
            due_since=due_since,
            started_monotonic=now,
            deadline_monotonic=now + window_seconds,
            candidate=target,
            last_failure_at=runtime_data(target.instance.instance_uuid).last_failure_at,
        )
        logger.warning(
            "Reserving bounded quiet window for due Weather data. | "
            "instance_uuid_hash: %s | overdue_seconds: %.1f | window_seconds: %.1f | "
            "available_mb: %s | swap_percent: %s | required_available_mb: %s | "
            "max_swap_percent: %s",
            _uuid_hash(target.instance.instance_uuid),
            max(0.0, (current_dt - due_since).total_seconds()),
            window_seconds,
            resource_sample.available_mb,
            resource_sample.swap_percent,
            required_mb,
            max_swap,
        )
        return None, True, False

    def _continue(
        self, now, active_weather, candidates_by_uuid, runtime_data, current_dt,
        resource_sample, normal_margin, concession_ok, parse_time, align,
    ):
        window = self.window
        active_instance = active_weather.get(window.instance_uuid)
        original_instance = window.candidate.instance
        identity_current = bool(
            active_instance is not None
            and active_instance.structural_generation == original_instance.structural_generation
            and active_instance.settings_revision == original_instance.settings_revision
        )
        runtime = runtime_data(window.instance_uuid)
        if runtime.last_failure_at != window.last_failure_at:
            self.finish(reason="provider_failed", resource_sample=resource_sample)
            return None, False, False
        last_success = parse_time(runtime.last_success_at)
        if last_success is not None:
            last_success = align(last_success, current_dt)
        if not identity_current or (last_success is not None and last_success >= window.due_since):
            self.finish(
                reason=("target_changed" if not identity_current else "completed"),
                resource_sample=resource_sample,
            )
            return None, False, False

        target = candidates_by_uuid.get(window.instance_uuid)
        next_retry = parse_time(runtime.next_retry_at)
        retry_pending = False
        if next_retry is not None:
            retry_pending = current_dt < align(next_retry, current_dt)
        if target is None and not retry_pending:
            self.finish(reason="no_longer_due", resource_sample=resource_sample)
            return None, False, False

        if now >= window.deadline_monotonic:
            if not concession_ok:
                self.finish(reason="margin_unavailable", resource_sample=resource_sample)
                return None, False, False
            # The retry gate can hide a candidate created by this window's
            # own pressure deferral. Rebuild it with the currently active,
            # identity-checked snapshot before issuing the single concession.
            return target or replace(window.candidate, instance=active_instance), False, True
        if normal_margin:
            if target is None and retry_pending:
                target = replace(window.candidate, instance=active_instance)
            return target, False, False
        return None, True, False
