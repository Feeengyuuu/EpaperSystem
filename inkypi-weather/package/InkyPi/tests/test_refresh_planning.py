"""Scheduling choices at the snapshot-to-command public boundary."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import MappingProxyType

import pytest

from model import PluginInstanceSnapshot
from runtime.refresh_contracts import CommandSource, RefreshIntent
from runtime.refresh_policy import AdmissionState, DueCandidate, DueReason, ResourceTier
from runtime.refresh_planning import plan_candidate
from runtime.runtime_state import InstanceRuntimeState, PresentationRequestState, RefreshLane, RefreshLaneState


NOW = datetime(2026, 9, 4, 12, tzinfo=timezone.utc)


def candidate(lane=RefreshLane.DATA, *, identity="page-a", attempted=None):
    instance = PluginInstanceSnapshot(
        instance_uuid=identity, plugin_id="clock", name=identity,
        settings=MappingProxyType({}), refresh=MappingProxyType({"interval": 300}),
        latest_refresh_time=None, structural_generation=1, settings_revision=1,
    )
    return DueCandidate(instance, lane, NOW - timedelta(minutes=10), DueReason.INTERVAL, attempted)


@pytest.mark.parametrize(
    "lane,source,intent,priority",
    [
        (RefreshLane.DATA, CommandSource.BACKGROUND, RefreshIntent.DATA_REFRESH, 10),
        (RefreshLane.LIVE, CommandSource.LIVE, RefreshIntent.LIVE_REFRESH, 70),
        (RefreshLane.THEME, CommandSource.SCHEDULER, RefreshIntent.THEME_REDRAW, 80),
    ],
)
def test_selected_lane_produces_the_existing_command_semantics(lane, source, intent, priority):
    selected = candidate(lane)
    plan = plan_candidate(selected, {})
    assert (plan.source, plan.intent, plan.priority) == (source, intent, priority)
    assert plan.instance is selected.instance
    assert plan.background_live_refresh is (lane is RefreshLane.LIVE)
    assert plan.theme_render_only is (lane is RefreshLane.THEME)


def test_current_page_live_work_remains_bound_to_its_displayed_instance():
    plan = plan_candidate(replace(candidate(RefreshLane.LIVE), requires_displayed_instance=True), {})
    assert plan.expected_displayed_instance_uuid == "page-a"
    assert not plan.background_live_refresh


def presentation_state():
    return InstanceRuntimeState(presentation_request=PresentationRequestState(
        request_id="a" * 32, requested_at=NOW.isoformat(),
        structural_generation=1, settings_revision=1,
        origin_theme_mode="day", origin_display_commit_id="display-origin",
    ))


def test_presentation_plan_requires_and_preserves_the_pending_request():
    selected = candidate(RefreshLane.PRESENTATION)
    assert plan_candidate(selected, {}) is None
    plan = plan_candidate(selected, {"page-a": presentation_state()})
    assert plan.intent is RefreshIntent.PRESENTATION_REFRESH
    assert plan.presentation_request_id == "a" * 32
    assert plan.priority == 20


def test_reserved_display_gets_one_data_attempt_then_its_presentation():
    from runtime.refresh_planning import plan_reserved_presentation

    presentation = replace(candidate(RefreshLane.PRESENTATION), due_since=NOW)
    state = AdmissionState(consecutive_data_admissions=3, consecutive_background_live_admissions=2)
    inputs = dict(
        presentations=[presentation], runtime_instances={"page-a": presentation_state()},
        reserved_instance_uuids=frozenset({"page-a"}), state=state,
        tier=ResourceTier.HEALTHY, blocked=frozenset(),
    )
    first = plan_reserved_presentation(data=[candidate()], **inputs)
    assert first.plan.intent is RefreshIntent.DATA_REFRESH
    assert first.plan.priority == 95
    assert first.plan.automatic_rotation
    assert first.state.consecutive_data_admissions == 0
    assert first.state.consecutive_background_live_admissions == 0
    assert state.consecutive_data_admissions == 3

    second = plan_reserved_presentation(data=[candidate(attempted=NOW + timedelta(seconds=1))], **inputs)
    assert second.plan.intent is RefreshIntent.PRESENTATION_REFRESH
    assert second.plan.presentation_request_id == "a" * 32
    assert second.plan.priority == 90


def test_reserved_work_obeys_lane_specific_resource_restrictions():
    from runtime.refresh_planning import plan_reserved_presentation

    inputs = dict(
        data=[candidate()], presentations=[candidate(RefreshLane.PRESENTATION)],
        runtime_instances={"page-a": presentation_state()},
        reserved_instance_uuids=frozenset({"page-a"}), state=AdmissionState(),
        tier=ResourceTier.HARD,
    )
    # Existing HARD behavior still permits a provider-free presentation.
    result = plan_reserved_presentation(blocked=frozenset(), **inputs)
    assert result.plan.intent is RefreshIntent.PRESENTATION_REFRESH
    result = plan_reserved_presentation(blocked=frozenset({("clock", RefreshLane.PRESENTATION)}), **inputs)
    assert result is None


def test_due_collection_avoids_a_duplicate_provider_fetch_for_pending_presentation():
    from runtime.refresh_planning import InstanceDueInput, collect_due_candidates

    item = InstanceDueInput(
        instance=candidate().instance, state=presentation_state(), has_cache=True,
        presentation_enabled=True, provider_presentation=True, theme_mode="day",
    )
    result = collect_due_candidates([item], now=NOW)
    assert not result.data
    assert len(result.presentations) == 1
    separate = collect_due_candidates([replace(item, provider_presentation=False)], now=NOW)
    assert len(separate.data) == 1
    assert len(separate.presentations) == 1


def test_due_collection_reports_the_next_wakeup_without_starting_any_work():
    from runtime.refresh_planning import InstanceDueInput, collect_due_candidates

    item = InstanceDueInput(
        instance=candidate().instance,
        state=InstanceRuntimeState(data=RefreshLaneState(last_success_at=(NOW - timedelta(seconds=100)).isoformat())),
        has_cache=True,
    )
    result = collect_due_candidates([item], now=NOW)
    assert not result.data
    assert result.wakeups == (NOW + timedelta(seconds=200),)


@pytest.mark.parametrize("has_cache", [False, True])
def test_display_driven_data_has_no_periodic_or_bootstrap_fetch(has_cache):
    from runtime.refresh_planning import InstanceDueInput, collect_due_candidates

    item = InstanceDueInput(
        instance=candidate().instance, state=InstanceRuntimeState(),
        has_cache=has_cache, refresh_data_before_display=True,
    )
    result = collect_due_candidates([item], now=NOW)
    assert result.data == ()
    assert result.wakeups == ()
    ordinary = collect_due_candidates([replace(item, refresh_data_before_display=False)], now=NOW)
    assert len(ordinary.data) == 1


@pytest.mark.parametrize("blocked_lane,intent", [
    (RefreshLane.DATA, RefreshIntent.PRESENTATION_REFRESH),
    (RefreshLane.PRESENTATION, RefreshIntent.DATA_REFRESH),
])
def test_reserved_lanes_are_independent_and_preserve_soft_spacing(blocked_lane, intent):
    from runtime.refresh_planning import plan_reserved_presentation

    state = AdmissionState(3, 101.0, 102.0, 2)
    result = plan_reserved_presentation(
        data=[candidate(attempted=NOW.replace(tzinfo=None))],
        presentations=[replace(candidate(RefreshLane.PRESENTATION), due_since=NOW)],
        runtime_instances={"page-a": presentation_state()},
        reserved_instance_uuids=frozenset({"page-a"}), state=state,
        tier=ResourceTier.HEALTHY, blocked=frozenset({("clock", blocked_lane)}),
    )
    assert result.plan.intent is intent
    assert result.state.last_soft_data_admitted_monotonic == 101.0
    assert result.state.last_soft_renderer_admitted_monotonic == 102.0
    assert result.state.consecutive_background_live_admissions == (2 if blocked_lane is RefreshLane.DATA else 0)


def test_reserved_work_never_uses_another_instance_of_the_same_plugin():
    from runtime.refresh_planning import plan_reserved_presentation

    other = replace(presentation_state(), presentation_request=replace(
        presentation_state().presentation_request, request_id="b" * 32,
    ))
    result = plan_reserved_presentation(
        data=[candidate(identity="page-b")],
        presentations=[candidate(RefreshLane.PRESENTATION, identity="page-b"), candidate(RefreshLane.PRESENTATION)],
        runtime_instances={"page-a": presentation_state(), "page-b": other},
        reserved_instance_uuids=frozenset({"page-a"}), state=AdmissionState(),
        tier=ResourceTier.HEALTHY, blocked=frozenset(),
    )
    assert result.plan.instance.instance_uuid == "page-a"
    assert result.plan.intent is RefreshIntent.PRESENTATION_REFRESH
    assert result.plan.presentation_request_id == "a" * 32


@pytest.mark.parametrize("reason", ["retry", "prepared", "cache_missing"])
def test_provider_presentation_without_a_runnable_candidate_does_not_suppress_data(reason):
    from runtime.refresh_planning import InstanceDueInput, collect_due_candidates

    state = presentation_state()
    if reason == "retry":
        state = replace(state, presentation=RefreshLaneState(next_retry_at=(NOW + timedelta(minutes=5)).isoformat()))
    if reason == "prepared":
        state = replace(state, presentation_request=replace(
            state.presentation_request, prepared_at=NOW.isoformat(), prepared_theme_mode="day",
        ))
    item = InstanceDueInput(
        candidate().instance, state, reason != "cache_missing",
        presentation_enabled=True, provider_presentation=True, theme_mode="day",
    )
    result = collect_due_candidates([item], now=NOW)
    assert not result.presentations
    assert len(result.data) == 1


def _sports_input(*, data_ago, live_ago, renews=True):
    from runtime.refresh_planning import InstanceDueInput

    instance = PluginInstanceSnapshot(
        instance_uuid="sports", plugin_id="sports_dashboard", name="Sports",
        settings=MappingProxyType({}), refresh=MappingProxyType({"interval": 900}),
        latest_refresh_time=None, structural_generation=1, settings_revision=1,
    )
    state = InstanceRuntimeState(
        data=RefreshLaneState(last_success_at=(NOW - data_ago).isoformat()),
        live=RefreshLaneState(last_success_at=(NOW - live_ago).isoformat()) if live_ago is not None
        else RefreshLaneState(),
    )
    return InstanceDueInput(instance=instance, state=state, has_cache=True, live_refresh_renews_data=renews)


def test_a_live_refresh_that_renders_the_page_renews_its_data_cadence():
    from runtime.refresh_planning import LIVE_REFRESH_RENEWS_DATA, collect_due_candidates

    assert "sports_dashboard" in LIVE_REFRESH_RENEWS_DATA
    assert "box_office_top_movies" not in LIVE_REFRESH_RENEWS_DATA
    # Data last fetched 20 minutes ago, but a live refresh did the same work a minute ago.
    renewed = collect_due_candidates([_sports_input(data_ago=timedelta(minutes=20), live_ago=timedelta(minutes=1))],
                                     now=NOW)
    assert renewed.data == () and NOW + timedelta(minutes=14) in renewed.wakeups
    # Without the capability, the data lane only knows its own success and is due again.
    alone = collect_due_candidates([_sports_input(data_ago=timedelta(minutes=20), live_ago=timedelta(minutes=1),
                                                  renews=False)], now=NOW)
    assert len(alone.data) == 1


def test_an_older_or_missing_live_success_leaves_the_data_cadence_alone():
    from runtime.refresh_planning import collect_due_candidates

    older = collect_due_candidates([_sports_input(data_ago=timedelta(minutes=20), live_ago=timedelta(minutes=30))],
                                   now=NOW)
    missing = collect_due_candidates([_sports_input(data_ago=timedelta(minutes=20), live_ago=None)], now=NOW)
    assert len(older.data) == 1 and len(missing.data) == 1
