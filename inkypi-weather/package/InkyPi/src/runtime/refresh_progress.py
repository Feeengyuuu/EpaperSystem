"""Bounded, identity-free progress diagnostics from immutable scheduler inputs."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import math
import time

from .refresh_planning import LIVE_REFRESH_RENEWS_DATA, with_live_data_freshness
from .refresh_policy import DueReason, evaluate_data_due
from .runtime_state import InstanceRuntimeState


def _positive_seconds(value):
    if isinstance(value, bool):
        return None
    try:
        seconds = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return seconds if math.isfinite(seconds) and seconds > 0 else None


def _timestamp(value, reference):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=reference.tzinfo)
    elif reference.tzinfo is None:
        parsed = parsed.replace(tzinfo=None)
    return parsed


def _elapsed(now, then):
    if now.tzinfo is not None:
        now, then = now.astimezone(timezone.utc), then.astimezone(timezone.utc)
    return (now - then).total_seconds()


class RefreshProgressTracker:
    """Observe expected refresh progress without changing scheduler policy."""

    def __init__(self, *, clock=time.monotonic):
        self._clock = clock
        self._first_due = {}
        self._snapshot = self._empty(observed=False)

    @staticmethod
    def _empty(*, observed):
        return {
            "enabled": False,
            "observed": observed,
            "active_instances": 0,
            "data_overdue_count": 0,
            "data_stalled_count": 0,
            "data_backoff_count": 0,
            "never_succeeded_count": 0,
            "presentation_pending_count": 0,
            "presentation_stalled_count": 0,
            "obsolete_presentation_count": 0,
            "oldest_data_overdue_seconds": None,
            "oldest_presentation_pending_seconds": None,
            "data_stall_grace_floor_seconds": 900.0,
            "presentation_stall_threshold_seconds": 900.0,
        }

    def snapshot(self):
        return dict(self._snapshot)

    def observe(
        self,
        *,
        instances,
        runtime_instances,
        cache_instance_uuids,
        presentation_instance_uuids,
        now,
        rotation_cycle_seconds,
        display_owned_instance_uuids=(),
    ):
        active = tuple(instances or ())
        result = self._empty(observed=True)
        result["enabled"] = bool(active)
        result["active_instances"] = len(active)
        cycle = _positive_seconds(rotation_cycle_seconds) or 300.0
        presentation_grace = max(900.0, 2 * len(active) * cycle)
        result["presentation_stall_threshold_seconds"] = presentation_grace
        data_ages = []
        presentation_ages = []
        monotonic_now = float(self._clock())
        first_due = {}
        # Due refreshes are served in the order they fell due, so with more due than the device
        # can run every instance waits about as long. One that waits while the lane keeps
        # succeeding elsewhere is late, not stalled, until every instance could have had a turn
        # (one refresh per rotation). The fixed floor alone called a five-minute page stalled
        # several times a day: 41 degraded spells in the 48 hours to 2026-11-26.
        newest_success = None
        for instance in active:
            lane = runtime_instances.get(instance.instance_uuid, InstanceRuntimeState()).data
            succeeded = _timestamp(lane.last_success_at, now)
            if succeeded is not None and _elapsed(now, succeeded) >= 0 and (
                    newest_success is None or _elapsed(succeeded, newest_success) > 0):
                newest_success = succeeded
        lane_moving = newest_success is not None and _elapsed(now, newest_success) < 900.0
        queue_grace = max(900.0, len(active) * cycle)
        for instance in active:
            state = runtime_instances.get(instance.instance_uuid, InstanceRuntimeState())
            pending = state.presentation_request
            if pending is not None:
                receipt = state.presentation_receipt
                if (
                    instance.instance_uuid not in presentation_instance_uuids
                    or pending.structural_generation != instance.structural_generation
                    or pending.settings_revision != instance.settings_revision
                    or (receipt is not None and receipt.request_id == pending.request_id)
                ):
                    result["obsolete_presentation_count"] += 1
                else:
                    requested = _timestamp(pending.requested_at, now)
                    pending_age = None if requested is None else _elapsed(now, requested)
                    if pending_age is not None and pending_age >= 0:
                        result["presentation_pending_count"] += 1
                        presentation_ages.append(pending_age)
                        if pending_age >= presentation_grace:
                            result["presentation_stalled_count"] += 1
            # A display-owned provider has no background deadline. Its saved
            # interval must not create a stall that the scheduler cannot service.
            # Presentation/display failures remain observable independently.
            if instance.instance_uuid in display_owned_instance_uuids:
                continue
            if instance.plugin_id in LIVE_REFRESH_RENEWS_DATA:
                state = with_live_data_freshness(state, now)
            diagnostic = replace(state, data=replace(
                state.data,
                last_success_at=(state.data.last_success_at
                                 if isinstance(state.data.last_success_at, str) else None),
                last_attempt_at=(state.data.last_attempt_at
                                 if isinstance(state.data.last_attempt_at, str) else None),
                next_retry_at=None,
            ))
            try:
                evaluation = evaluate_data_due(
                    instance,
                    diagnostic,
                    instance.instance_uuid in cache_instance_uuids,
                    now,
                )
            except (ValueError, OverflowError):
                # An unrepresentable cadence must not erase other diagnostics.
                continue
            if evaluation.candidate is None:
                continue
            age = _elapsed(now, evaluation.candidate.due_since)
            if age < 0:
                continue
            success = _timestamp(state.data.last_success_at, now)
            if success is not None and _elapsed(now, success) < 0:
                success = None
            if success is None:
                result["never_succeeded_count"] += 1
            bootstrapping = success is None or evaluation.candidate.reason is DueReason.BOOTSTRAP_MISSING
            failure = _timestamp(state.data.last_failure_at, now)
            failed_since_due = (
                failure is not None
                and _elapsed(now, failure) <= _elapsed(now, evaluation.candidate.due_since)
            )
            if success is None or evaluation.candidate.reason is DueReason.BOOTSTRAP_MISSING:
                key = (
                    instance.instance_uuid,
                    instance.structural_generation,
                    instance.settings_revision,
                    None if success is None else success.isoformat(),
                )
                first_due[key] = self._first_due.get(key, monotonic_now)
                age = max(0.0, monotonic_now - first_due[key])
            result["data_overdue_count"] += 1
            data_ages.append(age)
            retry = _timestamp(state.data.next_retry_at, now)
            backing_off = retry is not None and _elapsed(now, retry) < 0
            if backing_off:
                result["data_backoff_count"] += 1
            interval = _positive_seconds(instance.refresh.get("interval")) or 0.0
            waiting_its_turn = (
                lane_moving and not bootstrapping and not backing_off and not failed_since_due
                and age < queue_grace
            )
            if age >= max(900.0, 2 * interval) and not waiting_its_turn:
                result["data_stalled_count"] += 1
        result["oldest_data_overdue_seconds"] = max(data_ages, default=None)
        result["oldest_presentation_pending_seconds"] = max(presentation_ages, default=None)
        self._first_due = first_due
        self._snapshot = result
        return self.snapshot()
