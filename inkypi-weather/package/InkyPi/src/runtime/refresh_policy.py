"""Pure due and admission policy for independent refresh work."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from enum import Enum
import math
from typing import TYPE_CHECKING

from .runtime_state import InstanceRuntimeState, RefreshLane

if TYPE_CHECKING:
    from model import PluginInstanceSnapshot


_SCHEDULE_LOOKBACK_DAYS = 8


class DueReason(str, Enum):
    BOOTSTRAP_MISSING = "bootstrap_missing"
    INTERVAL = "interval"
    SCHEDULED = "scheduled"
    PRESENTATION = "presentation"
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
    last_soft_renderer_admitted_monotonic: float | None = None


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
    data_by_instance_uuid = {
        candidate.instance.instance_uuid: candidate for candidate in data
    }
    urgent_presentation = []
    presentation_required_data = []
    required_data_uuids = set()
    filtered_auxiliary = []
    for candidate in auxiliary:
        data_candidate = data_by_instance_uuid.get(
            candidate.instance.instance_uuid
        )
        if (
            candidate.lane is RefreshLane.PRESENTATION
            and data_candidate is not None
        ):
            data_attempt = data_candidate.last_attempt_at
            if data_attempt is None or (
                _instant_key(data_attempt)
                <= _instant_key(candidate.due_since)
            ):
                # One data attempt gets priority for an exact instance so the
                # presentation can use the freshest available bank/cache.
                instance_uuid = data_candidate.instance.instance_uuid
                if instance_uuid not in required_data_uuids:
                    presentation_required_data.append(data_candidate)
                    required_data_uuids.add(instance_uuid)
                continue
            # A short data interval must not suppress the same pending
            # presentation forever after that attempt has already happened.
            urgent_presentation.append(candidate)
        filtered_auxiliary.append(candidate)
    auxiliary = filtered_auxiliary

    if tier is ResourceTier.HARD:
        return AdmissionDecision(None, state)

    if tier is ResourceTier.SOFT:
        # Soft pressure is enforced by admission SPACING, not by lane type:
        # every lane renders through the same plugin machinery, and deferring
        # THEME indefinitely freezes rotation because it gates on the theme
        # transition completing.  Keep the healthy-tier lane fairness here.
        if (not data and not auxiliary) or not _soft_spacing_elapsed(
            state,
            now_monotonic,
            thresholds,
        ):
            return AdmissionDecision(None, state)
        if urgent_presentation:
            return AdmissionDecision(
                urgent_presentation[0],
                replace(
                    state,
                    consecutive_data_admissions=0,
                    last_soft_renderer_admitted_monotonic=now_monotonic,
                ),
            )
        if presentation_required_data:
            return AdmissionDecision(
                presentation_required_data[0],
                replace(
                    state,
                    consecutive_data_admissions=min(
                        3,
                        max(0, state.consecutive_data_admissions) + 1,
                    ),
                    last_soft_data_admitted_monotonic=now_monotonic,
                    last_soft_renderer_admitted_monotonic=now_monotonic,
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
                    last_soft_data_admitted_monotonic=now_monotonic,
                    last_soft_renderer_admitted_monotonic=now_monotonic,
                ),
            )
        return AdmissionDecision(
            auxiliary[0],
            replace(
                state,
                consecutive_data_admissions=0,
                last_soft_renderer_admitted_monotonic=now_monotonic,
            ),
        )

    if urgent_presentation:
        return AdmissionDecision(
            urgent_presentation[0],
            replace(state, consecutive_data_admissions=0),
        )
    if presentation_required_data:
        return AdmissionDecision(
            presentation_required_data[0],
            replace(
                state,
                consecutive_data_admissions=min(
                    3,
                    max(0, state.consecutive_data_admissions) + 1,
                ),
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
                now
                if last_success is None
                else _add_elapsed_seconds(last_success, interval, now)
            )
            if _instant_key(interval_due) <= _instant_key(now):
                candidates.append((interval_due, DueReason.INTERVAL))

        if scheduled is not None:
            scheduled_due = _most_recent_schedule_occurrence(now, scheduled)
            if scheduled_due is not None and (
                last_success is None
                or _instant_key(last_success) < _instant_key(scheduled_due)
            ):
                candidates.append((scheduled_due, DueReason.SCHEDULED))

    if not candidates or (
        next_retry is not None
        and _instant_key(next_retry) > _instant_key(now)
    ):
        return DueEvaluation(None, tuple(invalid_fields))

    due_since, reason = min(
        candidates,
        key=lambda candidate: _instant_key(candidate[0]),
    )
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


def evaluate_presentation_due(
    instance: PluginInstanceSnapshot,
    runtime_state: InstanceRuntimeState,
    has_displayable_cache: bool,
    resolved_theme_mode: str | None,
    now: datetime,
) -> DueEvaluation:
    """Evaluate provider-free presentation work from immutable state."""
    request = runtime_state.presentation_request
    lane = runtime_state.presentation
    if not has_displayable_cache or request is None:
        return DueEvaluation(None)
    if (
        request.structural_generation != instance.structural_generation
        or request.settings_revision != instance.settings_revision
    ):
        return DueEvaluation(None)
    receipt = runtime_state.presentation_receipt
    if receipt is not None and receipt.request_id == request.request_id:
        return DueEvaluation(None)
    if (
        request.prepared_at is not None
        and request.prepared_theme_mode == resolved_theme_mode
    ):
        return DueEvaluation(None)

    next_retry = _parse_lane_time(lane.next_retry_at, now)
    if (
        next_retry is not None
        and _instant_key(next_retry) > _instant_key(now)
    ):
        return DueEvaluation(None)

    due_since = _parse_lane_time(request.requested_at, now)
    if due_since is None:
        return DueEvaluation(None)
    return DueEvaluation(
        DueCandidate(
            instance=instance,
            lane=RefreshLane.PRESENTATION,
            due_since=due_since,
            reason=DueReason.PRESENTATION,
            last_attempt_at=_parse_lane_time(lane.last_attempt_at, now),
        )
    )


def _valid_interval(value, invalid_fields: list[str]) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        invalid_fields.append("refresh.interval")
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
    if not _is_aware(parsed) and _is_aware(reference):
        return _localize_wall_time(parsed.replace(tzinfo=None), reference)
    if _is_aware(parsed) and _is_aware(reference):
        return parsed.astimezone(reference.tzinfo)
    if _is_aware(parsed) and not _is_aware(reference):
        return parsed.replace(tzinfo=None)
    return parsed


def _add_elapsed_seconds(
    value: datetime,
    seconds: float,
    reference: datetime,
) -> datetime:
    delta = timedelta(seconds=seconds)
    if not _is_aware(value):
        return value + delta
    target_timezone = reference.tzinfo if _is_aware(reference) else value.tzinfo
    return (value.astimezone(timezone.utc) + delta).astimezone(target_timezone)


def _most_recent_schedule_occurrence(now: datetime, scheduled):
    now_key = _instant_key(now)
    timezone_info = now.tzinfo if _is_aware(now) else None
    for days_back in range(_SCHEDULE_LOOKBACK_DAYS):
        local_date = now.date() - timedelta(days=days_back)
        wall_time = datetime.combine(local_date, scheduled)
        occurrences = (
            (wall_time,)
            if timezone_info is None
            else _valid_local_occurrences(wall_time, timezone_info)
        )
        eligible = [
            occurrence
            for occurrence in occurrences
            if _instant_key(occurrence) <= now_key
        ]
        if eligible:
            return max(eligible, key=_instant_key)
    return None


def _valid_local_occurrences(wall_time: datetime, timezone_info):
    localize = getattr(timezone_info, "localize", None)
    if callable(localize):
        candidates = (
            localize(wall_time, is_dst=True),
            localize(wall_time, is_dst=False),
        )
    else:
        candidates = (
            wall_time.replace(tzinfo=timezone_info, fold=0),
            wall_time.replace(tzinfo=timezone_info, fold=1),
        )

    valid: dict[datetime, datetime] = {}
    for candidate in candidates:
        normalized = candidate.astimezone(timezone.utc).astimezone(timezone_info)
        if normalized.replace(tzinfo=None) != wall_time:
            continue
        valid[_instant_key(normalized)] = normalized
    return tuple(valid[key] for key in sorted(valid))


def _localize_wall_time(
    wall_time: datetime,
    reference: datetime,
) -> datetime | None:
    timezone_info = reference.tzinfo
    occurrences = _valid_local_occurrences(wall_time, timezone_info)
    if not occurrences:
        return None

    localize = getattr(timezone_info, "localize", None)
    if callable(localize):
        preferred_key = _instant_key(localize(wall_time))
        for occurrence in occurrences:
            if _instant_key(occurrence) == preferred_key:
                return occurrence
    return occurrences[0]


def _is_aware(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


def _instant_key(value: datetime) -> datetime:
    if _is_aware(value):
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value.replace(tzinfo=None)


def _finite_metric(value: float | None) -> float | None:
    if value is None:
        return None
    try:
        metric = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return metric if math.isfinite(metric) else None


def _candidate_order(candidate: DueCandidate):
    due_since = _instant_key(candidate.due_since)
    last_attempt = (
        due_since
        if candidate.last_attempt_at is None
        else _instant_key(candidate.last_attempt_at)
    )
    return (
        candidate.reason is not DueReason.BOOTSTRAP_MISSING,
        due_since,
        candidate.last_attempt_at is not None,
        last_attempt,
        candidate.instance.instance_uuid,
    )


def _soft_spacing_elapsed(
    state: AdmissionState,
    now_monotonic: float,
    thresholds: ResourceThresholds,
) -> bool:
    admitted_at = (
        state.last_soft_data_admitted_monotonic,
        state.last_soft_renderer_admitted_monotonic,
    )
    last_admitted = max(
        (value for value in admitted_at if value is not None),
        default=None,
    )
    return last_admitted is None or (
        now_monotonic - last_admitted >= thresholds.soft_spacing_seconds
    )
