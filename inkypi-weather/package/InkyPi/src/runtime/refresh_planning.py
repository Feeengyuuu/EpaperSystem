"""Pure snapshot-to-command planning, independent of plugins and device I/O."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import TYPE_CHECKING, Mapping, Sequence

from runtime.refresh_contracts import CommandSource, RefreshIntent
from runtime.refresh_policy import (
    AdmissionState, DueCandidate, ResourceTier, evaluate_data_due, evaluate_presentation_due,
)
from runtime.runtime_state import InstanceRuntimeState, RefreshLane

if TYPE_CHECKING:
    from model import PluginInstanceSnapshot


@dataclass(frozen=True)
class RefreshPlan:
    """The semantic work to perform; execution and persistence belong to the caller."""

    instance: PluginInstanceSnapshot
    source: CommandSource
    intent: RefreshIntent
    priority: int
    presentation_request_id: str | None = None
    expected_displayed_instance_uuid: str | None = None
    background_live_refresh: bool = False
    theme_render_only: bool = False
    automatic_rotation: bool = False


@dataclass(frozen=True)
class PlannedAdmission:
    """A choice and the fairness state to publish only when accepting it."""

    plan: RefreshPlan
    state: AdmissionState


@dataclass(frozen=True)
class InstanceDueInput:
    """Resolved plugin capabilities and state for one due evaluation."""

    instance: PluginInstanceSnapshot
    state: InstanceRuntimeState
    has_cache: bool
    first_due_since: datetime | None = None
    presentation_enabled: bool = False
    provider_presentation: bool = False
    theme_mode: str | None = None


@dataclass(frozen=True)
class DueCandidates:
    """Candidates and observations; publishing scheduler state is a separate step."""

    data: tuple[DueCandidate, ...]
    presentations: tuple[DueCandidate, ...]
    wakeups: tuple[datetime, ...]
    invalid_fields: tuple[tuple[str, tuple[str, ...]], ...]


def collect_due_candidates(inputs: Sequence[InstanceDueInput], *, now: datetime) -> DueCandidates:
    """Evaluate each immutable input without importing or invoking any plugin."""
    data, presentations, wakeups, invalid = [], [], [], []
    for item in inputs:
        evaluation = evaluate_data_due(
            item.instance, item.state, item.has_cache, now,
            first_due_since=item.first_due_since,
        )
        if evaluation.next_due_at is not None:
            wakeups.append(evaluation.next_due_at)
        if evaluation.invalid_fields:
            invalid.append((item.instance.plugin_id, tuple(evaluation.invalid_fields)))
        presentation_due = False
        if item.presentation_enabled:
            presentation = evaluate_presentation_due(
                item.instance, item.state, item.has_cache, item.theme_mode, now,
            )
            if presentation.next_due_at is not None:
                wakeups.append(presentation.next_due_at)
            if presentation.candidate is not None:
                presentations.append(presentation.candidate)
                presentation_due = item.provider_presentation
        if evaluation.candidate is not None and not presentation_due:
            data.append(evaluation.candidate)
    return DueCandidates(tuple(data), tuple(presentations), tuple(wakeups), tuple(invalid))


def align_datetime_tz(value: datetime, reference: datetime) -> datetime:
    """Compare legacy naive timestamps using the reference's timezone semantics."""
    if value.tzinfo is None and reference.tzinfo is not None:
        localize = getattr(reference.tzinfo, "localize", None)
        return localize(value) if localize else value.replace(tzinfo=reference.tzinfo)
    if value.tzinfo is not None and reference.tzinfo is not None:
        return value.astimezone(reference.tzinfo)
    if value.tzinfo is not None and reference.tzinfo is None:
        return value.replace(tzinfo=None)
    return value


def plan_candidate(
    candidate: DueCandidate,
    runtime_instances: Mapping[str, InstanceRuntimeState],
) -> RefreshPlan | None:
    """Preserve the selected lane's display identity, priority, and refresh intent."""
    instance = candidate.instance
    if candidate.lane is RefreshLane.PRESENTATION:
        state = runtime_instances.get(instance.instance_uuid)
        request = state.presentation_request if state is not None else None
        if request is None:
            return None
        return RefreshPlan(
            instance, CommandSource.BACKGROUND, RefreshIntent.PRESENTATION_REFRESH, 20,
            presentation_request_id=request.request_id,
        )
    if candidate.lane is RefreshLane.THEME:
        return RefreshPlan(
            instance, CommandSource.SCHEDULER, RefreshIntent.THEME_REDRAW, 80,
            expected_displayed_instance_uuid=instance.instance_uuid,
            theme_render_only=True,
        )
    if candidate.lane is RefreshLane.LIVE:
        return RefreshPlan(
            instance, CommandSource.LIVE, RefreshIntent.LIVE_REFRESH, 70,
            expected_displayed_instance_uuid=(
                instance.instance_uuid if candidate.requires_displayed_instance else None
            ),
            background_live_refresh=not candidate.requires_displayed_instance,
        )
    return RefreshPlan(instance, CommandSource.BACKGROUND, RefreshIntent.DATA_REFRESH, 10)


def plan_reserved_presentation(
    *,
    data: Sequence[DueCandidate],
    presentations: Sequence[DueCandidate],
    runtime_instances: Mapping[str, InstanceRuntimeState],
    reserved_instance_uuids: frozenset[str],
    state: AdmissionState,
    tier: ResourceTier,
    blocked: frozenset[tuple[str, RefreshLane]],
) -> PlannedAdmission | None:
    """Give a reserved display one due data attempt, then its exact presentation."""
    data_by_id = {item.instance.instance_uuid: item for item in data}
    for presentation in presentations:
        instance = presentation.instance
        if instance.instance_uuid not in reserved_instance_uuids:
            continue
        runtime = runtime_instances.get(instance.instance_uuid)
        request = runtime.presentation_request if runtime is not None else None
        if request is None:
            continue
        pending = data_by_id.get(instance.instance_uuid)
        if (
            pending is not None
            and tier is not ResourceTier.HARD
            and (instance.plugin_id, RefreshLane.DATA) not in blocked
            and (
                pending.last_attempt_at is None
                or align_datetime_tz(pending.last_attempt_at, presentation.due_since)
                <= presentation.due_since
            )
        ):
            return PlannedAdmission(
                RefreshPlan(instance, CommandSource.BACKGROUND, RefreshIntent.DATA_REFRESH, 95,
                            automatic_rotation=True),
                replace(state, consecutive_data_admissions=0, consecutive_background_live_admissions=0),
            )
        if (instance.plugin_id, presentation.lane) in blocked:
            continue
        return PlannedAdmission(
            RefreshPlan(instance, CommandSource.BACKGROUND, RefreshIntent.PRESENTATION_REFRESH, 90,
                        presentation_request_id=request.request_id, automatic_rotation=True),
            replace(state, consecutive_data_admissions=0),
        )
    return None
