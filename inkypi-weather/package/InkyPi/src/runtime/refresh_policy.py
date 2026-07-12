"""Pure due and admission policy for independent refresh work."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import Enum
import math
from typing import TYPE_CHECKING

from .runtime_state import InstanceRuntimeState, RefreshLane

if TYPE_CHECKING:
    from model import PluginInstanceSnapshot


class DueReason(str, Enum):
    BOOTSTRAP_MISSING = "bootstrap_missing"
    INTERVAL = "interval"
    SCHEDULED = "scheduled"
    LIVE = "live"
    THEME = "theme"


class ResourceTier(str, Enum):
    HEALTHY = "healthy"
    SOFT = "soft"
    HARD = "hard"


@dataclass(frozen=True)
class ResourceSample:
    available_mb: float | None
    swap_percent: float | None


@dataclass(frozen=True)
class ResourceThresholds:
    soft_min_available_mb: float = 150.0
    soft_max_swap_percent: float = 70.0
    hard_min_available_mb: float = 70.0
    hard_max_swap_percent: float = 75.0
    soft_spacing_seconds: float = 60.0


@dataclass(frozen=True)
class DueCandidate:
    instance: PluginInstanceSnapshot
    lane: RefreshLane
    due_since: datetime
    reason: DueReason
    last_attempt_at: datetime | None


@dataclass(frozen=True)
class DueEvaluation:
    candidate: DueCandidate | None
    invalid_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class AdmissionState:
    consecutive_data_admissions: int = 0
    last_soft_data_admitted_monotonic: float | None = None


@dataclass(frozen=True)
class AdmissionDecision:
    candidate: DueCandidate | None
    state: AdmissionState


def classify_resource_tier(
    sample: ResourceSample,
    thresholds: ResourceThresholds,
) -> ResourceTier:
    """Classify caller-sampled resource facts, with hard limits taking priority."""
    available = _finite_metric(sample.available_mb)
    swap = _finite_metric(sample.swap_percent)
    if (
        available is not None
        and available < thresholds.hard_min_available_mb
    ) or (
        swap is not None
        and swap >= thresholds.hard_max_swap_percent
    ):
        return ResourceTier.HARD
    if available is None or swap is None:
        return ResourceTier.SOFT
    if (
        available < thresholds.soft_min_available_mb
        or swap >= thresholds.soft_max_swap_percent
    ):
        return ResourceTier.SOFT
    return ResourceTier.HEALTHY


def choose_refresh_candidate(
    data_candidates,
    auxiliary_candidates,
    *,
    tier: ResourceTier,
    state: AdmissionState,
    now_monotonic: float,
    thresholds: ResourceThresholds,
) -> AdmissionDecision:
    """Admit at most one deterministically ordered refresh candidate."""
    data = sorted(data_candidates, key=_candidate_order)
    auxiliary = sorted(auxiliary_candidates, key=_candidate_order)

    if tier is ResourceTier.HARD:
        return AdmissionDecision(None, state)

    if tier is ResourceTier.SOFT:
        if not data or not _soft_spacing_elapsed(
            state,
            now_monotonic,
            thresholds,
        ):
            return AdmissionDecision(None, state)
        return AdmissionDecision(
            data[0],
            replace(
                state,
                consecutive_data_admissions=min(
                    3,
                    max(0, state.consecutive_data_admissions) + 1,
                ),
                last_soft_data_admitted_monotonic=now_monotonic,
            ),
        )

    if data and (
        not auxiliary or state.consecutive_data_admissions < 3
    ):
        return AdmissionDecision(
            data[0],
            replace(
                state,
                consecutive_data_admissions=min(
                    3,
                    max(0, state.consecutive_data_admissions) + 1,
                ),
            ),
        )
    if auxiliary:
        return AdmissionDecision(
            auxiliary[0],
            replace(state, consecutive_data_admissions=0),
        )
    return AdmissionDecision(None, state)


def evaluate_data_due(
    instance: PluginInstanceSnapshot,
    runtime_state: InstanceRuntimeState,
    has_displayable_cache: bool,
    now: datetime,
) -> DueEvaluation:
    """Evaluate ordinary data cadence from immutable caller-supplied facts."""
    invalid_fields: list[str] = []
    data_state = runtime_state.data
    last_success = _parse_lane_time(data_state.last_success_at, now)
    last_attempt = _parse_lane_time(data_state.last_attempt_at, now)
    next_retry = _parse_lane_time(data_state.next_retry_at, now)

    candidates: list[tuple[datetime, DueReason]] = []
    interval = _valid_interval(instance.refresh.get("interval"), invalid_fields)
    scheduled = _valid_schedule(instance.refresh.get("scheduled"), invalid_fields)

    if not has_displayable_cache:
        candidates.append((now, DueReason.BOOTSTRAP_MISSING))
    else:
        if interval is not None:
            interval_due = (
                now if last_success is None else last_success + timedelta(seconds=interval)
            )
            if interval_due <= now:
                candidates.append((interval_due, DueReason.INTERVAL))

        if scheduled is not None:
            scheduled_due = now.replace(
                hour=scheduled.hour,
                minute=scheduled.minute,
                second=0,
                microsecond=0,
            )
            if scheduled_due > now:
                scheduled_due -= timedelta(days=1)
            if last_success is None or last_success < scheduled_due <= now:
                candidates.append((scheduled_due, DueReason.SCHEDULED))

    if not candidates or (next_retry is not None and next_retry > now):
        return DueEvaluation(None, tuple(invalid_fields))

    due_since, reason = min(candidates, key=lambda candidate: candidate[0])
    return DueEvaluation(
        DueCandidate(
            instance=instance,
            lane=RefreshLane.DATA,
            due_since=due_since,
            reason=reason,
            last_attempt_at=last_attempt,
        ),
        tuple(invalid_fields),
    )


def _valid_interval(value, invalid_fields: list[str]) -> float | None:
    if value is None:
        return None
    try:
        interval = float(value)
    except (TypeError, ValueError, OverflowError):
        interval = math.nan
    if not math.isfinite(interval) or interval <= 0:
        invalid_fields.append("refresh.interval")
        return None
    return interval


def _valid_schedule(value, invalid_fields: list[str]):
    if value is None:
        return None
    if not isinstance(value, str):
        invalid_fields.append("refresh.scheduled")
        return None
    try:
        return datetime.strptime(value, "%H:%M").time()
    except ValueError:
        invalid_fields.append("refresh.scheduled")
        return None


def _parse_lane_time(value: str | None, reference: datetime) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None and reference.tzinfo is not None:
        return parsed.replace(tzinfo=reference.tzinfo)
    if parsed.tzinfo is not None and reference.tzinfo is not None:
        return parsed.astimezone(reference.tzinfo)
    if parsed.tzinfo is not None and reference.tzinfo is None:
        return parsed.replace(tzinfo=None)
    return parsed


def _finite_metric(value: float | None) -> float | None:
    if value is None:
        return None
    try:
        metric = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return metric if math.isfinite(metric) else None


def _candidate_order(candidate: DueCandidate):
    return (
        candidate.reason is not DueReason.BOOTSTRAP_MISSING,
        candidate.due_since,
        candidate.last_attempt_at is not None,
        candidate.last_attempt_at or candidate.due_since,
        candidate.instance.instance_uuid,
    )


def _soft_spacing_elapsed(
    state: AdmissionState,
    now_monotonic: float,
    thresholds: ResourceThresholds,
) -> bool:
    last_admitted = state.last_soft_data_admitted_monotonic
    return last_admitted is None or (
        now_monotonic - last_admitted >= thresholds.soft_spacing_seconds
    )
