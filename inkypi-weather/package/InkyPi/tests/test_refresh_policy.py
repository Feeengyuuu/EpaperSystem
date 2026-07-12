from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import MappingProxyType

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


def test_healthy_admits_exactly_one_and_gives_data_three_of_four_slots():
    data = [_candidate("data")]
    auxiliary = [
        _candidate(
            "live",
            lane=RefreshLane.LIVE,
            reason=DueReason.LIVE,
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
        RefreshLane.LIVE,
        RefreshLane.DATA,
        RefreshLane.DATA,
        RefreshLane.DATA,
        RefreshLane.LIVE,
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


def test_soft_never_admits_live_or_theme():
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
    state = AdmissionState()

    decision = choose_refresh_candidate(
        [],
        auxiliary,
        tier=ResourceTier.SOFT,
        state=state,
        now_monotonic=1000.0,
        thresholds=ResourceThresholds(),
    )

    assert decision.candidate is None
    assert decision.state == state


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
