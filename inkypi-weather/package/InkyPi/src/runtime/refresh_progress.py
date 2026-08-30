"""Bounded, identity-free progress diagnostics from immutable scheduler inputs."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import math
import time

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
            if retry is not None and _elapsed(now, retry) < 0:
                result["data_backoff_count"] += 1
            interval = _positive_seconds(instance.refresh.get("interval")) or 0.0
            if age >= max(900.0, 2 * interval):
                result["data_stalled_count"] += 1
        result["oldest_data_overdue_seconds"] = max(data_ages, default=None)
        result["oldest_presentation_pending_seconds"] = max(presentation_ages, default=None)
        self._first_due = first_due
        self._snapshot = result
        return self.snapshot()
