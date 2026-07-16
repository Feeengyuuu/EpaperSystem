from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import MappingProxyType
from zoneinfo import ZoneInfo

import pytz

import src.runtime.refresh_policy as refresh_policy
from src.model import PluginInstanceSnapshot
from src.runtime.refresh_policy import (
    AdmissionState,
    DueCandidate,
    DueReason,
    ResourceSample,
    ResourceThresholds,
    ResourceTier,
    choose_refresh_candidate,
    classify_resource_tier,
    evaluate_data_due,
)
from src.runtime.runtime_state import (
    InstanceRuntimeState,
    LastGoodCacheState,
    PresentationCommitReceipt,
    PresentationRequestState,
    RefreshLane,
    RefreshLaneState,
)


UTC = timezone.utc


def _instance(
    *,
    instance_uuid: str = "instance-a",
    refresh=None,
    settings=None,
) -> PluginInstanceSnapshot:
    return PluginInstanceSnapshot(
        instance_uuid=instance_uuid,
        plugin_id="test_plugin",
        name="Test Instance",
        settings=MappingProxyType({} if settings is None else dict(settings)),
        refresh=MappingProxyType({} if refresh is None else dict(refresh)),
        latest_refresh_time=None,
        structural_generation=3,
        settings_revision=7,
    )


def _lane(
    *,
    attempt: datetime | None = None,
    success: datetime | None = None,
    failure: datetime | None = None,
    retry: datetime | None = None,
) -> RefreshLaneState:
    return RefreshLaneState(
        last_attempt_at=attempt.isoformat() if attempt is not None else None,
        last_success_at=success.isoformat() if success is not None else None,
        last_failure_at=failure.isoformat() if failure is not None else None,
        next_retry_at=retry.isoformat() if retry is not None else None,
    )


def _candidate(
    instance_uuid: str,
    *,
    lane: RefreshLane = RefreshLane.DATA,
    reason: DueReason = DueReason.INTERVAL,
    due_since: datetime | None = None,
    last_attempt_at: datetime | None = None,
) -> DueCandidate:
    return DueCandidate(
        instance=_instance(instance_uuid=instance_uuid),
        lane=lane,
        due_since=(
            datetime(2026, 7, 11, 8, 0, tzinfo=UTC)
            if due_since is None
            else due_since
        ),
        reason=reason,
        last_attempt_at=last_attempt_at,
    )


def _presentation_request(
    *,
    request_id: str = "a" * 32,
    requested_at: datetime | None = None,
    structural_generation: int = 3,
    settings_revision: int = 7,
    prepared_at: datetime | None = None,
    prepared_theme_mode: str | None = None,
) -> PresentationRequestState:
    return PresentationRequestState(
        request_id=request_id,
        requested_at=(
            datetime(2026, 7, 11, 11, 0, tzinfo=UTC)
            if requested_at is None
            else requested_at
        ).isoformat(),
        structural_generation=structural_generation,
        settings_revision=settings_revision,
        origin_theme_mode="day",
        origin_display_commit_id="display-origin",
        prepared_at=(
            prepared_at.isoformat() if prepared_at is not None else None
        ),
        prepared_theme_mode=prepared_theme_mode,
    )


def _presentation_candidate(instance_uuid: str) -> DueCandidate:
    return _candidate(
        instance_uuid,
        lane=RefreshLane.PRESENTATION,
        reason=refresh_policy.DueReason.PRESENTATION,
    )


def test_interval_uses_data_success_not_live_or_theme_success():
    now = datetime(2026, 7, 11, 12, 0, tzinfo=UTC)
    data_attempt = datetime(2026, 7, 11, 11, 40, tzinfo=UTC)
    runtime_state = InstanceRuntimeState(
        data=_lane(
            attempt=data_attempt,
            success=datetime(2026, 7, 11, 8, 0, tzinfo=UTC),
        ),
        live=_lane(success=datetime(2026, 7, 11, 11, 59, tzinfo=UTC)),
        theme=_lane(success=datetime(2026, 7, 11, 11, 58, tzinfo=UTC)),
    )

    result = evaluate_data_due(
        _instance(refresh={"interval": 3600}),
        runtime_state,
        has_displayable_cache=True,
        now=now,
    )

    assert result.invalid_fields == ()
    assert result.candidate is not None
    assert result.candidate.lane is RefreshLane.DATA
    assert result.candidate.reason is DueReason.INTERVAL
    assert result.candidate.due_since == datetime(
        2026, 7, 11, 9, 0, tzinfo=UTC
    )
    assert result.candidate.last_attempt_at == data_attempt


def test_scheduled_uses_most_recent_occurrence_and_data_success():
    local_tz = timezone(timedelta(hours=5, minutes=30))
    now = datetime(2026, 7, 11, 12, 30, tzinfo=local_tz)
    runtime_state = InstanceRuntimeState(
        data=_lane(
            success=datetime(2026, 7, 10, 10, 0, tzinfo=local_tz),
        ),
        live=_lane(success=datetime(2026, 7, 11, 12, 0, tzinfo=local_tz)),
    )

    result = evaluate_data_due(
        _instance(refresh={"scheduled": "09:15"}),
        runtime_state,
        has_displayable_cache=True,
        now=now,
    )

    assert result.candidate is not None
    assert result.candidate.reason is DueReason.SCHEDULED
    assert result.candidate.due_since == datetime(
        2026, 7, 11, 9, 15, tzinfo=local_tz
    )


def test_interval_and_scheduled_are_or_and_return_earliest_due_since():
    now = datetime(2026, 7, 11, 12, 0, tzinfo=UTC)
    both_due = evaluate_data_due(
        _instance(refresh={"interval": 3600, "scheduled": "10:30"}),
        InstanceRuntimeState(
            data=_lane(success=datetime(2026, 7, 11, 8, 0, tzinfo=UTC))
        ),
        has_displayable_cache=True,
        now=now,
    )

    assert both_due.candidate is not None
    assert both_due.candidate.reason is DueReason.INTERVAL
    assert both_due.candidate.due_since == datetime(
        2026, 7, 11, 9, 0, tzinfo=UTC
    )

    scheduled_only = evaluate_data_due(
        _instance(refresh={"interval": 7200, "scheduled": "11:45"}),
        InstanceRuntimeState(
            data=_lane(success=datetime(2026, 7, 11, 11, 30, tzinfo=UTC))
        ),
        has_displayable_cache=True,
        now=now,
    )

    assert scheduled_only.candidate is not None
    assert scheduled_only.candidate.reason is DueReason.SCHEDULED
    assert scheduled_only.candidate.due_since == datetime(
        2026, 7, 11, 11, 45, tzinfo=UTC
    )


def test_missing_all_exact_caches_bootstraps_unconfigured_instance_once():
    now = datetime(2026, 7, 11, 12, 0, tzinfo=UTC)
    instance = _instance(refresh={})
    runtime_state = InstanceRuntimeState()

    missing = evaluate_data_due(
        instance,
        runtime_state,
        has_displayable_cache=False,
        now=now,
    )
    available = evaluate_data_due(
        instance,
        runtime_state,
        has_displayable_cache=True,
        now=now,
    )

    assert missing.candidate is not None
    assert missing.candidate.reason is DueReason.BOOTSTRAP_MISSING
    assert missing.candidate.due_since == now
    assert available.candidate is None


def test_opposite_theme_last_good_does_not_create_false_data_due():
    now = datetime(2026, 7, 11, 12, 0, tzinfo=UTC)
    runtime_state = InstanceRuntimeState(
        last_good_cache=LastGoodCacheState(
            theme_mode="day",
            structural_generation=3,
            settings_revision=7,
            promoted_at=datetime(2026, 7, 11, 8, 0, tzinfo=UTC).isoformat(),
        )
    )

    result = evaluate_data_due(
        _instance(settings={"resolvedThemeMode": "night"}),
        runtime_state,
        has_displayable_cache=True,
        now=now,
    )

    assert result.candidate is None
    assert result.invalid_fields == ()


def test_data_retry_does_not_read_live_or_theme_retry_deadlines():
    now = datetime(2026, 7, 11, 12, 0, tzinfo=UTC)
    future_retry = datetime(2026, 7, 11, 13, 0, tzinfo=UTC)
    instance = _instance(refresh={"interval": 3600})
    due_data = _lane(success=datetime(2026, 7, 11, 8, 0, tzinfo=UTC))

    auxiliary_retry_only = evaluate_data_due(
        instance,
        InstanceRuntimeState(
            data=due_data,
            live=_lane(retry=future_retry),
            theme=_lane(retry=future_retry),
        ),
        has_displayable_cache=True,
        now=now,
    )
    data_retry = evaluate_data_due(
        instance,
        InstanceRuntimeState(
            data=_lane(
                success=datetime(2026, 7, 11, 8, 0, tzinfo=UTC),
                retry=future_retry,
            )
        ),
        has_displayable_cache=True,
        now=now,
    )

    assert auxiliary_retry_only.candidate is not None
    assert auxiliary_retry_only.candidate.reason is DueReason.INTERVAL
    assert data_retry.candidate is None


def test_spring_forward_interval_uses_elapsed_time_not_wall_clock():
    los_angeles = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 3, 8, 3, 15, tzinfo=los_angeles)
    last_success = datetime(2026, 3, 8, 1, 45, tzinfo=los_angeles)

    result = evaluate_data_due(
        _instance(refresh={"interval": 3600}),
        InstanceRuntimeState(data=_lane(success=last_success)),
        has_displayable_cache=True,
        now=now,
    )

    assert result.candidate is None


def test_fall_back_interval_uses_elapsed_time_and_preserves_due_fold():
    los_angeles = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 11, 1, 1, 30, tzinfo=los_angeles, fold=1)
    last_success = datetime(
        2026,
        11,
        1,
        1,
        15,
        tzinfo=los_angeles,
        fold=0,
    )

    result = evaluate_data_due(
        _instance(refresh={"interval": 3600}),
        InstanceRuntimeState(data=_lane(success=last_success)),
        has_displayable_cache=True,
        now=now,
    )

    assert result.candidate is not None
    assert result.candidate.reason is DueReason.INTERVAL
    assert result.candidate.due_since.fold == 1
    assert result.candidate.due_since.astimezone(UTC) == datetime(
        2026,
        11,
        1,
        9,
        15,
        tzinfo=UTC,
    )


def test_retry_deadline_uses_absolute_time_across_fall_fold():
    los_angeles = ZoneInfo("America/Los_Angeles")
    instance = _instance(refresh={"interval": 60})
    last_success = datetime(2026, 11, 1, 0, 0, tzinfo=los_angeles)

    expired_retry = evaluate_data_due(
        instance,
        InstanceRuntimeState(
            data=_lane(
                success=last_success,
                retry=datetime(
                    2026,
                    11,
                    1,
                    1,
                    45,
                    tzinfo=los_angeles,
                    fold=0,
                ),
            )
        ),
        has_displayable_cache=True,
        now=datetime(
            2026,
            11,
            1,
            1,
            30,
            tzinfo=los_angeles,
            fold=1,
        ),
    )
    future_retry = evaluate_data_due(
        instance,
        InstanceRuntimeState(
            data=_lane(
                success=last_success,
                retry=datetime(
                    2026,
                    11,
                    1,
                    1,
                    15,
                    tzinfo=los_angeles,
                    fold=1,
                ),
            )
        ),
        has_displayable_cache=True,
        now=datetime(
            2026,
            11,
            1,
            1,
            45,
            tzinfo=los_angeles,
            fold=0,
        ),
    )

    assert expired_retry.candidate is not None
    assert future_retry.candidate is None


def test_fall_back_schedule_uses_second_ambiguous_occurrence():
    los_angeles = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 11, 1, 2, 0, tzinfo=los_angeles)
    last_success = datetime(
        2026,
        11,
        1,
        1,
        45,
        tzinfo=los_angeles,
        fold=0,
    )

    result = evaluate_data_due(
        _instance(refresh={"scheduled": "01:30"}),
        InstanceRuntimeState(data=_lane(success=last_success)),
        has_displayable_cache=True,
        now=now,
    )

    assert result.candidate is not None
    assert result.candidate.reason is DueReason.SCHEDULED
    assert result.candidate.due_since.fold == 1
    assert result.candidate.due_since.astimezone(UTC) == datetime(
        2026,
        11,
        1,
        9,
        30,
        tzinfo=UTC,
    )


def test_fall_back_schedule_uses_first_occurrence_before_second_exists():
    los_angeles = ZoneInfo("America/Los_Angeles")
    now = datetime(
        2026,
        11,
        1,
        1,
        45,
        tzinfo=los_angeles,
        fold=0,
    )

    result = evaluate_data_due(
        _instance(refresh={"scheduled": "01:30"}),
        InstanceRuntimeState(
            data=_lane(
                success=datetime(2026, 10, 31, 2, 0, tzinfo=los_angeles)
            )
        ),
        has_displayable_cache=True,
        now=now,
    )

    assert result.candidate is not None
    assert result.candidate.reason is DueReason.SCHEDULED
    assert result.candidate.due_since.fold == 0
    assert result.candidate.due_since.astimezone(UTC) == datetime(
        2026,
        11,
        1,
        8,
        30,
        tzinfo=UTC,
    )


def test_nonexistent_schedule_returns_previous_valid_occurrence():
    los_angeles = ZoneInfo("America/Los_Angeles")
    now = datetime(2026, 3, 8, 4, 0, tzinfo=los_angeles)

    result = evaluate_data_due(
        _instance(refresh={"scheduled": "02:30"}),
        InstanceRuntimeState(),
        has_displayable_cache=True,
        now=now,
    )

    assert result.candidate is not None
    assert result.candidate.reason is DueReason.SCHEDULED
    assert result.candidate.due_since == datetime(
        2026,
        3,
        7,
        2,
        30,
        tzinfo=los_angeles,
    )


def test_pytz_nonexistent_schedule_returns_previous_valid_occurrence():
    los_angeles = pytz.timezone("America/Los_Angeles")
    now = los_angeles.localize(datetime(2026, 3, 8, 4, 0), is_dst=True)
    expected = los_angeles.localize(
        datetime(2026, 3, 7, 2, 30),
        is_dst=False,
    )

    result = evaluate_data_due(
        _instance(refresh={"scheduled": "02:30"}),
        InstanceRuntimeState(),
        has_displayable_cache=True,
        now=now,
    )

    assert result.candidate is not None
    assert result.candidate.reason is DueReason.SCHEDULED
    assert result.candidate.due_since.astimezone(UTC) == expected.astimezone(UTC)


def test_naive_interval_remains_naive_without_system_timezone_conversion():
    now = datetime(2026, 7, 11, 12, 0)

    result = evaluate_data_due(
        _instance(refresh={"interval": 3600}),
        InstanceRuntimeState(
            data=_lane(success=datetime(2026, 7, 11, 10, 0))
        ),
        has_displayable_cache=True,
        now=now,
    )

    assert result.candidate is not None
    assert result.candidate.due_since == datetime(2026, 7, 11, 11, 0)
    assert result.candidate.due_since.tzinfo is None


def test_invalid_interval_and_schedule_return_diagnostics_without_tight_loop():
    result = evaluate_data_due(
        _instance(
            refresh={
                "interval": "whenever",
                "scheduled": "25:99",
            }
        ),
        InstanceRuntimeState(),
        has_displayable_cache=True,
        now=datetime(2026, 7, 11, 12, 0, tzinfo=UTC),
    )

    assert result.candidate is None
    assert result.invalid_fields == (
        "refresh.interval",
        "refresh.scheduled",
    )


def test_boolean_interval_is_invalid_without_tight_loop():
    result = evaluate_data_due(
        _instance(refresh={"interval": True}),
        InstanceRuntimeState(),
        has_displayable_cache=True,
        now=datetime(2026, 7, 11, 12, 0, tzinfo=UTC),
    )

    assert result.candidate is None
    assert result.invalid_fields == ("refresh.interval",)


def test_presentation_due_requires_cache_request_revision_and_retry_deadline():
    now = datetime(2026, 7, 11, 12, 0, tzinfo=UTC)
    requested_at = datetime(2026, 7, 11, 11, 0, tzinfo=UTC)
    last_attempt = datetime(2026, 7, 11, 11, 30, tzinfo=UTC)
    instance = _instance()
    request = _presentation_request(requested_at=requested_at)
    due = refresh_policy.evaluate_presentation_due(
        instance,
        InstanceRuntimeState(
            presentation=_lane(attempt=last_attempt, retry=now),
            presentation_request=request,
        ),
        has_displayable_cache=True,
        resolved_theme_mode="day",
        now=now,
    )

    assert due.invalid_fields == ()
    assert due.candidate is not None
    assert due.candidate.lane is RefreshLane.PRESENTATION
    assert due.candidate.reason is refresh_policy.DueReason.PRESENTATION
    assert due.candidate.due_since == requested_at
    assert due.candidate.last_attempt_at == last_attempt

    matching_receipt = PresentationCommitReceipt(
        request_id=request.request_id,
        committed_at=datetime(
            2026, 7, 11, 11, 45, tzinfo=UTC
        ).isoformat(),
        display_commit_id="display-committed",
        structural_generation=3,
        settings_revision=7,
        theme_mode="day",
    )
    not_due = (
        refresh_policy.evaluate_presentation_due(
            instance,
            InstanceRuntimeState(presentation_request=request),
            has_displayable_cache=False,
            resolved_theme_mode="day",
            now=now,
        ),
        refresh_policy.evaluate_presentation_due(
            instance,
            InstanceRuntimeState(),
            has_displayable_cache=True,
            resolved_theme_mode="day",
            now=now,
        ),
        refresh_policy.evaluate_presentation_due(
            instance,
            InstanceRuntimeState(
                presentation_request=_presentation_request(
                    structural_generation=4
                )
            ),
            has_displayable_cache=True,
            resolved_theme_mode="day",
            now=now,
        ),
        refresh_policy.evaluate_presentation_due(
            instance,
            InstanceRuntimeState(
                presentation_request=_presentation_request(
                    settings_revision=8
                )
            ),
            has_displayable_cache=True,
            resolved_theme_mode="day",
            now=now,
        ),
        refresh_policy.evaluate_presentation_due(
            instance,
            InstanceRuntimeState(
                presentation=_lane(retry=now + timedelta(seconds=1)),
                presentation_request=request,
            ),
            has_displayable_cache=True,
            resolved_theme_mode="day",
            now=now,
        ),
        refresh_policy.evaluate_presentation_due(
            instance,
            InstanceRuntimeState(
                presentation_request=request,
                presentation_receipt=matching_receipt,
            ),
            has_displayable_cache=True,
            resolved_theme_mode="day",
            now=now,
        ),
    )

    assert all(result.candidate is None for result in not_due)


def test_presentation_due_ignores_attempt_from_before_current_request():
    now = datetime(2026, 7, 11, 12, 0, tzinfo=UTC)
    requested_at = datetime(2026, 7, 11, 11, 30, tzinfo=UTC)
    old_request_attempt = datetime(2026, 7, 11, 10, 0, tzinfo=UTC)

    due = refresh_policy.evaluate_presentation_due(
        _instance(),
        InstanceRuntimeState(
            presentation=_lane(
                attempt=old_request_attempt,
                retry=now + timedelta(minutes=5),
            ),
            presentation_request=_presentation_request(
                requested_at=requested_at
            ),
        ),
        has_displayable_cache=True,
        resolved_theme_mode="day",
        now=now,
    )

    assert due.candidate is not None
    assert due.candidate.due_since == requested_at
    assert due.candidate.last_attempt_at is None


def test_prepared_request_is_not_renderer_due_until_displayed():
    now = datetime(2026, 7, 11, 12, 0, tzinfo=UTC)
    requested_at = datetime(2026, 7, 11, 11, 0, tzinfo=UTC)
    prepared_at = datetime(2026, 7, 11, 11, 30, tzinfo=UTC)
    runtime_state = InstanceRuntimeState(
        presentation=_lane(attempt=prepared_at),
        presentation_request=_presentation_request(
            requested_at=requested_at,
            prepared_at=prepared_at,
            prepared_theme_mode="day",
        ),
    )

    matching_theme = refresh_policy.evaluate_presentation_due(
        _instance(),
        runtime_state,
        has_displayable_cache=True,
        resolved_theme_mode="day",
        now=now,
    )
    changed_theme = refresh_policy.evaluate_presentation_due(
        _instance(),
        runtime_state,
        has_displayable_cache=True,
        resolved_theme_mode="night",
        now=now,
    )

    assert matching_theme.candidate is None
    assert changed_theme.candidate is not None
    assert changed_theme.candidate.lane is RefreshLane.PRESENTATION
    assert changed_theme.candidate.reason is refresh_policy.DueReason.PRESENTATION
    assert changed_theme.candidate.due_since == requested_at
    assert changed_theme.candidate.last_attempt_at == prepared_at


def test_data_has_same_instance_priority_over_pending_presentation():
    data = _candidate("shared-instance")
    presentation = _presentation_candidate("shared-instance")
    state = AdmissionState(consecutive_data_admissions=3)

    decision = choose_refresh_candidate(
        [data],
        [presentation],
        tier=ResourceTier.HEALTHY,
        state=state,
        now_monotonic=1000.0,
        thresholds=ResourceThresholds(),
    )

    assert decision.candidate == data
    assert decision.candidate.lane is RefreshLane.DATA
    assert decision.state.consecutive_data_admissions == 3


def test_same_instance_presentation_runs_after_newer_data_attempt_under_soft_pressure():
    requested_at = datetime(2026, 7, 11, 8, 0, tzinfo=UTC)
    first_data = _candidate(
        "shared-instance",
        due_since=requested_at,
    )
    presentation = _candidate(
        "shared-instance",
        lane=RefreshLane.PRESENTATION,
        reason=DueReason.PRESENTATION,
        due_since=requested_at,
    )
    thresholds = ResourceThresholds(soft_spacing_seconds=60.0)

    refreshed_data = _candidate(
        "shared-instance",
        due_since=requested_at,
        last_attempt_at=requested_at + timedelta(seconds=1),
    )
    for tier in (ResourceTier.SOFT, ResourceTier.HEALTHY):
        first = choose_refresh_candidate(
            [first_data],
            [presentation],
            tier=tier,
            state=AdmissionState(),
            now_monotonic=100.0,
            thresholds=thresholds,
        )
        assert first.candidate == first_data

        second = choose_refresh_candidate(
            [refreshed_data],
            [presentation],
            tier=tier,
            state=first.state,
            now_monotonic=160.0,
            thresholds=thresholds,
        )

        assert second.candidate == presentation
        assert second.state.consecutive_data_admissions == 0
        if tier is ResourceTier.SOFT:
            assert second.state.last_soft_renderer_admitted_monotonic == 160.0


def test_pending_presentation_prioritizes_its_required_data_before_unrelated_backlog():
    requested_at = datetime(2026, 7, 11, 9, 0, tzinfo=UTC)
    unrelated = _candidate(
        "older-unrelated",
        due_since=requested_at - timedelta(hours=4),
    )
    matching = _candidate(
        "shared-instance",
        due_since=requested_at - timedelta(hours=1),
        last_attempt_at=requested_at - timedelta(seconds=1),
    )
    presentation = _candidate(
        "shared-instance",
        lane=RefreshLane.PRESENTATION,
        reason=DueReason.PRESENTATION,
        due_since=requested_at,
    )

    for tier in (ResourceTier.SOFT, ResourceTier.HEALTHY):
        decision = choose_refresh_candidate(
            [unrelated, matching],
            [presentation],
            tier=tier,
            state=AdmissionState(),
            now_monotonic=100.0,
            thresholds=ResourceThresholds(soft_spacing_seconds=60.0),
        )

        assert decision.candidate == matching


def test_soft_admits_spaced_three_data_then_one_auxiliary_turn():
    data = [_candidate("soft-data")]
    auxiliary = [
        _presentation_candidate("soft-presentation"),
        _candidate(
            "soft-theme",
            lane=RefreshLane.THEME,
            reason=DueReason.THEME,
        ),
    ]
    thresholds = ResourceThresholds(soft_spacing_seconds=10.0)
    state = AdmissionState()
    selected_lanes = []

    for now_monotonic in range(100, 180, 10):
        decision = choose_refresh_candidate(
            data,
            auxiliary,
            tier=ResourceTier.SOFT,
            state=state,
            now_monotonic=float(now_monotonic),
            thresholds=thresholds,
        )
        assert decision.candidate is not None
        selected_lanes.append(decision.candidate.lane)
        state = decision.state

        too_soon = choose_refresh_candidate(
            data,
            auxiliary,
            tier=ResourceTier.SOFT,
            state=state,
            now_monotonic=now_monotonic + 9.99,
            thresholds=thresholds,
        )
        assert too_soon.candidate is None
        assert too_soon.state == state

    assert selected_lanes == [
        RefreshLane.DATA,
        RefreshLane.DATA,
        RefreshLane.DATA,
        RefreshLane.PRESENTATION,
        RefreshLane.DATA,
        RefreshLane.DATA,
        RefreshLane.DATA,
        RefreshLane.PRESENTATION,
    ]
    assert state.last_soft_renderer_admitted_monotonic == 170.0


def test_displayed_live_wins_auxiliary_turn_over_older_presentation():
    older_presentation = _candidate(
        "older-presentation",
        lane=RefreshLane.PRESENTATION,
        reason=DueReason.PRESENTATION,
        due_since=datetime(2026, 7, 15, 19, 0, tzinfo=UTC),
    )
    displayed_live = _candidate(
        "displayed-live",
        lane=RefreshLane.LIVE,
        reason=DueReason.LIVE,
        due_since=datetime(2026, 7, 15, 20, 0, tzinfo=UTC),
    )

    for tier in (ResourceTier.SOFT, ResourceTier.HEALTHY):
        decision = choose_refresh_candidate(
            [_candidate("ordinary-data")],
            [older_presentation, displayed_live],
            tier=tier,
            state=AdmissionState(),
            now_monotonic=1000.0,
            thresholds=ResourceThresholds(soft_spacing_seconds=60.0),
        )

        assert decision.candidate == displayed_live


def test_hard_admits_no_presentation():
    state = AdmissionState(
        consecutive_data_admissions=3,
        last_soft_data_admitted_monotonic=100.0,
    )

    decision = choose_refresh_candidate(
        [],
        [_presentation_candidate("hard-presentation")],
        tier=ResourceTier.HARD,
        state=state,
        now_monotonic=1000.0,
        thresholds=ResourceThresholds(),
    )

    assert decision.candidate is None
    assert decision.state == state


def test_hard_threshold_wins_over_soft_threshold():
    thresholds = ResourceThresholds()

    assert classify_resource_tier(
        ResourceSample(available_mb=149.0, swap_percent=75.0),
        thresholds,
    ) is ResourceTier.HARD
    assert classify_resource_tier(
        ResourceSample(available_mb=69.99, swap_percent=None),
        thresholds,
    ) is ResourceTier.HARD


def test_resource_threshold_boundaries_cover_hard_soft_and_healthy():
    thresholds = ResourceThresholds()

    assert classify_resource_tier(
        ResourceSample(available_mb=150.0, swap_percent=69.99),
        thresholds,
    ) is ResourceTier.HEALTHY
    assert classify_resource_tier(
        ResourceSample(available_mb=149.99, swap_percent=69.99),
        thresholds,
    ) is ResourceTier.SOFT
    assert classify_resource_tier(
        ResourceSample(available_mb=150.0, swap_percent=70.0),
        thresholds,
    ) is ResourceTier.SOFT
    assert classify_resource_tier(
        ResourceSample(available_mb=69.99, swap_percent=0.0),
        thresholds,
    ) is ResourceTier.HARD
    assert classify_resource_tier(
        ResourceSample(available_mb=150.0, swap_percent=75.0),
        thresholds,
    ) is ResourceTier.HARD


def test_missing_metric_degrades_to_soft():
    thresholds = ResourceThresholds()

    for sample in (
        ResourceSample(available_mb=None, swap_percent=0.0),
        ResourceSample(available_mb=1000.0, swap_percent=None),
        ResourceSample(available_mb=None, swap_percent=None),
        ResourceSample(available_mb=float("nan"), swap_percent=0.0),
    ):
        assert classify_resource_tier(sample, thresholds) is ResourceTier.SOFT

    assert classify_resource_tier(
        ResourceSample(available_mb=None, swap_percent=80.0),
        thresholds,
    ) is ResourceTier.HARD


def test_healthy_admits_exactly_one_and_gives_data_three_of_four_non_live_slots():
    data = [_candidate("data")]
    auxiliary = [
        _candidate(
            "theme",
            lane=RefreshLane.THEME,
            reason=DueReason.THEME,
        )
    ]
    state = AdmissionState()
    selected_lanes = []

    for now_monotonic in range(8):
        decision = choose_refresh_candidate(
            data,
            auxiliary,
            tier=ResourceTier.HEALTHY,
            state=state,
            now_monotonic=float(now_monotonic),
            thresholds=ResourceThresholds(),
        )
        assert decision.candidate is not None
        selected_lanes.append(decision.candidate.lane)
        state = decision.state

    assert selected_lanes == [
        RefreshLane.DATA,
        RefreshLane.DATA,
        RefreshLane.DATA,
        RefreshLane.THEME,
        RefreshLane.DATA,
        RefreshLane.DATA,
        RefreshLane.DATA,
        RefreshLane.THEME,
    ]


def test_soft_admits_one_data_only_after_spacing():
    candidate = _candidate("soft-data")
    thresholds = ResourceThresholds(soft_spacing_seconds=60.0)
    waiting_state = AdmissionState(last_soft_data_admitted_monotonic=100.0)

    first = choose_refresh_candidate(
        [candidate],
        [],
        tier=ResourceTier.SOFT,
        state=AdmissionState(),
        now_monotonic=100.0,
        thresholds=thresholds,
    )
    too_soon = choose_refresh_candidate(
        [candidate],
        [],
        tier=ResourceTier.SOFT,
        state=waiting_state,
        now_monotonic=159.99,
        thresholds=thresholds,
    )
    admitted = choose_refresh_candidate(
        [candidate],
        [],
        tier=ResourceTier.SOFT,
        state=waiting_state,
        now_monotonic=160.0,
        thresholds=thresholds,
    )

    assert first.candidate == candidate
    assert first.state.last_soft_data_admitted_monotonic == 100.0
    assert too_soon.candidate is None
    assert too_soon.state == waiting_state
    assert admitted.candidate == candidate
    assert admitted.state.last_soft_data_admitted_monotonic == 160.0


def test_soft_theme_gets_a_turn_after_three_data_admissions():
    """A pending theme transition must not starve under sustained soft tier.

    Rotation is gated on the theme transition completing, so indefinitely
    deferring THEME work freezes the display while data retries churn.
    """
    data = [_candidate("soft-data")]
    auxiliary = [
        _candidate(
            "soft-theme",
            lane=RefreshLane.THEME,
            reason=DueReason.THEME,
        ),
    ]
    thresholds = ResourceThresholds(soft_spacing_seconds=10.0)
    state = AdmissionState()
    selected_lanes = []

    for now_monotonic in range(100, 180, 10):
        decision = choose_refresh_candidate(
            data,
            auxiliary,
            tier=ResourceTier.SOFT,
            state=state,
            now_monotonic=float(now_monotonic),
            thresholds=thresholds,
        )
        assert decision.candidate is not None
        selected_lanes.append(decision.candidate.lane)
        state = decision.state

    assert selected_lanes == [
        RefreshLane.DATA,
        RefreshLane.DATA,
        RefreshLane.DATA,
        RefreshLane.THEME,
        RefreshLane.DATA,
        RefreshLane.DATA,
        RefreshLane.DATA,
        RefreshLane.THEME,
    ]


def test_soft_admits_live_and_theme_when_nothing_else_is_due():
    auxiliary = [
        _candidate(
            "live",
            lane=RefreshLane.LIVE,
            reason=DueReason.LIVE,
        ),
        _candidate(
            "theme",
            lane=RefreshLane.THEME,
            reason=DueReason.THEME,
        ),
    ]

    decision = choose_refresh_candidate(
        [],
        auxiliary,
        tier=ResourceTier.SOFT,
        state=AdmissionState(),
        now_monotonic=1000.0,
        thresholds=ResourceThresholds(),
    )

    assert decision.candidate is not None
    assert decision.candidate.lane in {RefreshLane.LIVE, RefreshLane.THEME}
    assert decision.state.consecutive_data_admissions == 0


def test_hard_admits_nothing():
    state = AdmissionState(
        consecutive_data_admissions=2,
        last_soft_data_admitted_monotonic=10.0,
    )

    decision = choose_refresh_candidate(
        [_candidate("data")],
        [
            _candidate(
                "theme",
                lane=RefreshLane.THEME,
                reason=DueReason.THEME,
            )
        ],
        tier=ResourceTier.HARD,
        state=state,
        now_monotonic=1000.0,
        thresholds=ResourceThresholds(),
    )

    assert decision.candidate is None
    assert decision.state == state


def test_failed_oldest_candidate_yields_when_its_data_retry_is_filtered():
    now = datetime(2026, 7, 11, 12, 0, tzinfo=UTC)
    oldest = evaluate_data_due(
        _instance(instance_uuid="oldest", refresh={"interval": 3600}),
        InstanceRuntimeState(
            data=_lane(
                success=datetime(2026, 7, 11, 6, 0, tzinfo=UTC),
                retry=datetime(2026, 7, 11, 13, 0, tzinfo=UTC),
            )
        ),
        has_displayable_cache=True,
        now=now,
    )
    next_due = evaluate_data_due(
        _instance(instance_uuid="next-due", refresh={"interval": 3600}),
        InstanceRuntimeState(
            data=_lane(success=datetime(2026, 7, 11, 9, 0, tzinfo=UTC))
        ),
        has_displayable_cache=True,
        now=now,
    )

    decision = choose_refresh_candidate(
        [
            result.candidate
            for result in (oldest, next_due)
            if result.candidate is not None
        ],
        [],
        tier=ResourceTier.HEALTHY,
        state=AdmissionState(),
        now_monotonic=1000.0,
        thresholds=ResourceThresholds(),
    )

    assert oldest.candidate is None
    assert decision.candidate is not None
    assert decision.candidate.instance.instance_uuid == "next-due"


def test_equal_due_candidates_order_by_last_attempt_then_uuid():
    due_since = datetime(2026, 7, 11, 8, 0, tzinfo=UTC)
    older_attempt = datetime(2026, 7, 11, 9, 0, tzinfo=UTC)
    newer_attempt = datetime(2026, 7, 11, 10, 0, tzinfo=UTC)
    state = AdmissionState()
    thresholds = ResourceThresholds()

    never_attempted = choose_refresh_candidate(
        [
            _candidate(
                "attempted",
                due_since=due_since,
                last_attempt_at=older_attempt,
            ),
            _candidate("never", due_since=due_since, last_attempt_at=None),
        ],
        [],
        tier=ResourceTier.HEALTHY,
        state=state,
        now_monotonic=1000.0,
        thresholds=thresholds,
    )
    oldest_attempt = choose_refresh_candidate(
        [
            _candidate(
                "newer-attempt",
                due_since=due_since,
                last_attempt_at=newer_attempt,
            ),
            _candidate(
                "older-attempt",
                due_since=due_since,
                last_attempt_at=older_attempt,
            ),
        ],
        [],
        tier=ResourceTier.HEALTHY,
        state=state,
        now_monotonic=1000.0,
        thresholds=thresholds,
    )
    uuid_tiebreak = choose_refresh_candidate(
        [
            _candidate(
                "uuid-b",
                due_since=due_since,
                last_attempt_at=older_attempt,
            ),
            _candidate(
                "uuid-a",
                due_since=due_since,
                last_attempt_at=older_attempt,
            ),
        ],
        [],
        tier=ResourceTier.HEALTHY,
        state=state,
        now_monotonic=1000.0,
        thresholds=thresholds,
    )

    assert never_attempted.candidate.instance.instance_uuid == "never"
    assert oldest_attempt.candidate.instance.instance_uuid == "older-attempt"
    assert uuid_tiebreak.candidate.instance.instance_uuid == "uuid-a"


def test_presentation_candidate_never_attempted_beats_older_failed_request():
    """A retrying old request must not starve a newer display request."""
    failed_old_request = _candidate(
        "failed-old",
        lane=RefreshLane.PRESENTATION,
        reason=DueReason.PRESENTATION,
        due_since=datetime(2026, 7, 11, 8, 0, tzinfo=UTC),
        last_attempt_at=datetime(2026, 7, 11, 11, 55, tzinfo=UTC),
    )
    waiting_new_request = _candidate(
        "waiting-new",
        lane=RefreshLane.PRESENTATION,
        reason=DueReason.PRESENTATION,
        due_since=datetime(2026, 7, 11, 11, 50, tzinfo=UTC),
        last_attempt_at=None,
    )

    decision = choose_refresh_candidate(
        [],
        [failed_old_request, waiting_new_request],
        tier=ResourceTier.HEALTHY,
        state=AdmissionState(),
        now_monotonic=1000.0,
        thresholds=ResourceThresholds(),
    )

    assert decision.candidate == waiting_new_request


def test_data_candidates_order_bootstrap_before_due_since():
    old_interval = _candidate(
        "old-interval",
        reason=DueReason.INTERVAL,
        due_since=datetime(2026, 7, 1, 8, 0, tzinfo=UTC),
    )
    newer_interval = _candidate(
        "newer-interval",
        reason=DueReason.INTERVAL,
        due_since=datetime(2026, 7, 10, 8, 0, tzinfo=UTC),
    )
    bootstrap = _candidate(
        "bootstrap",
        reason=DueReason.BOOTSTRAP_MISSING,
        due_since=datetime(2026, 7, 11, 8, 0, tzinfo=UTC),
    )
    common = {
        "tier": ResourceTier.HEALTHY,
        "state": AdmissionState(),
        "now_monotonic": 1000.0,
        "thresholds": ResourceThresholds(),
    }

    with_bootstrap = choose_refresh_candidate(
        [old_interval, bootstrap, newer_interval],
        [],
        **common,
    )
    cadence_only = choose_refresh_candidate(
        [newer_interval, old_interval],
        [],
        **common,
    )

    assert with_bootstrap.candidate == bootstrap
    assert cadence_only.candidate == old_interval


def test_candidate_order_uses_absolute_instants_across_fall_fold():
    los_angeles = ZoneInfo("America/Los_Angeles")
    first_instant = _candidate(
        "first-instant",
        due_since=datetime(
            2026,
            11,
            1,
            1,
            45,
            tzinfo=los_angeles,
            fold=0,
        ),
    )
    second_instant = _candidate(
        "second-instant",
        due_since=datetime(
            2026,
            11,
            1,
            1,
            15,
            tzinfo=los_angeles,
            fold=1,
        ),
    )
    shared_due = datetime(2026, 11, 1, 0, 0, tzinfo=los_angeles)
    first_attempt = _candidate(
        "first-attempt",
        due_since=shared_due,
        last_attempt_at=datetime(
            2026,
            11,
            1,
            1,
            45,
            tzinfo=los_angeles,
            fold=0,
        ),
    )
    second_attempt = _candidate(
        "second-attempt",
        due_since=shared_due,
        last_attempt_at=datetime(
            2026,
            11,
            1,
            1,
            15,
            tzinfo=los_angeles,
            fold=1,
        ),
    )
    common = {
        "tier": ResourceTier.HEALTHY,
        "state": AdmissionState(),
        "now_monotonic": 1000.0,
        "thresholds": ResourceThresholds(),
    }

    due_order = choose_refresh_candidate(
        [second_instant, first_instant],
        [],
        **common,
    )
    attempt_order = choose_refresh_candidate(
        [second_attempt, first_attempt],
        [],
        **common,
    )

    assert due_order.candidate == first_instant
    assert attempt_order.candidate == first_attempt
