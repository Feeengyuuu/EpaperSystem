from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import MappingProxyType

import pytest

from model import PluginInstanceSnapshot
from runtime.refresh_progress import RefreshProgressTracker
from runtime.runtime_state import (
    InstanceRuntimeState,
    PresentationCommitReceipt,
    PresentationRequestState,
    RefreshLaneState,
)


NOW = datetime(2026, 8, 30, 12, tzinfo=timezone.utc)


class Clock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value


def instance(key="instance-a", *, interval=300, revision=1):
    return PluginInstanceSnapshot(
        instance_uuid=key,
        plugin_id="test_plugin",
        name="Private instance",
        settings=MappingProxyType({"api_key": "private-key"}),
        refresh=MappingProxyType({} if interval is None else {"interval": interval}),
        latest_refresh_time=None,
        structural_generation=1,
        settings_revision=revision,
    )


def observe(tracker, instances, states=None, *, cache=(), presentation=(), now=NOW, cycle=300):
    return tracker.observe(
        instances=instances,
        runtime_instances={} if states is None else states,
        cache_instance_uuids=set(cache),
        presentation_instance_uuids=set(presentation),
        now=now,
        rotation_cycle_seconds=cycle,
    )


def test_tracker_starts_unobserved_and_empty_playlist_is_not_stalled():
    tracker = RefreshProgressTracker(clock=Clock())

    assert tracker.snapshot() == {
        "enabled": False,
        "observed": False,
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
    empty = observe(tracker, None)
    assert empty["enabled"] is False
    assert empty["observed"] is True
    assert empty["active_instances"] == 0
    empty["data_stalled_count"] = 99
    assert tracker.snapshot()["data_stalled_count"] == 0


def test_overdue_data_in_backoff_is_stalled_but_daily_data_not_due_is_healthy():
    tracker = RefreshProgressTracker(clock=Clock())
    frequent, daily = instance(), instance("daily", interval=86400)
    states = {
        frequent.instance_uuid: InstanceRuntimeState(data=RefreshLaneState(
            last_success_at=(NOW - timedelta(seconds=1500)).isoformat(),
            next_retry_at=(NOW + timedelta(seconds=300)).isoformat(),
        )),
        daily.instance_uuid: InstanceRuntimeState(data=RefreshLaneState(
            last_success_at=(NOW - timedelta(hours=23)).isoformat(),
        )),
    }

    result = observe(tracker, [frequent, daily], states, cache=states)

    assert result["enabled"] is True
    assert result["active_instances"] == 2
    assert result["data_overdue_count"] == 1
    assert result["data_backoff_count"] == 1
    assert result["data_stalled_count"] == 1
    assert result["oldest_data_overdue_seconds"] == 1200.0
    assert states[frequent.instance_uuid].data.next_retry_at == (
        NOW + timedelta(seconds=300)
    ).isoformat()


def test_never_successful_bootstrap_accumulates_monotonic_age_until_success():
    clock = Clock()
    tracker = RefreshProgressTracker(clock=clock)
    plugin = instance()

    initial = observe(tracker, [plugin])
    assert initial["never_succeeded_count"] == 1
    assert initial["oldest_data_overdue_seconds"] == 0.0
    clock.value = 899
    waiting = observe(tracker, [plugin], now=NOW + timedelta(seconds=899))
    assert waiting["data_stalled_count"] == 0
    clock.value = 900
    stalled = observe(tracker, [plugin], now=NOW + timedelta(seconds=900))
    assert stalled["data_stalled_count"] == 1
    assert stalled["oldest_data_overdue_seconds"] == 900.0

    success_at = NOW + timedelta(seconds=901)
    states = {plugin.instance_uuid: InstanceRuntimeState(data=RefreshLaneState(
        last_success_at=success_at.isoformat(),
    ))}
    recovered = observe(tracker, [plugin], states, cache=states, now=success_at)
    assert recovered["data_stalled_count"] == 0
    assert recovered["never_succeeded_count"] == 0
    assert recovered["oldest_data_overdue_seconds"] is None


def request(plugin, *, requested_at=NOW, revision=None, prepared=False):
    return PresentationRequestState(
        request_id="a" * 32,
        requested_at=requested_at.isoformat(),
        structural_generation=plugin.structural_generation,
        settings_revision=plugin.settings_revision if revision is None else revision,
        origin_theme_mode="night",
        origin_display_commit_id="origin-commit",
        prepared_at=requested_at.isoformat() if prepared else None,
        prepared_theme_mode="night" if prepared else None,
    )


def test_unadopted_prepared_presentation_stalls_without_counting_obsolete_requests():
    tracker = RefreshProgressTracker(clock=Clock())
    pending, stale_revision, committed = [instance(key, interval=None) for key in (
        "pending", "stale-revision", "committed",
    )]
    old = NOW - timedelta(seconds=1800)
    committed_request = request(committed, requested_at=old)
    states = {
        pending.instance_uuid: InstanceRuntimeState(
            presentation_request=request(pending, requested_at=old, prepared=True),
        ),
        stale_revision.instance_uuid: InstanceRuntimeState(
            presentation_request=request(stale_revision, requested_at=old, revision=9),
        ),
        committed.instance_uuid: InstanceRuntimeState(
            presentation_request=committed_request,
            presentation_receipt=PresentationCommitReceipt(
                request_id=committed_request.request_id,
                committed_at=NOW.isoformat(),
                display_commit_id="physical-commit",
                structural_generation=1,
                settings_revision=1,
                theme_mode="day",
            ),
        ),
    }

    result = observe(tracker, [pending, stale_revision, committed], states,
                     cache=states, presentation=states)

    assert result["presentation_stall_threshold_seconds"] == 1800.0
    assert result["presentation_pending_count"] == 1
    assert result["presentation_stalled_count"] == 1
    assert result["obsolete_presentation_count"] == 2
    assert result["oldest_presentation_pending_seconds"] == 1800.0
    assert result["data_stalled_count"] == 0
    assert "private-key" not in repr(result)
    assert "Private instance" not in repr(result)


@pytest.mark.parametrize("reset", ["revision", "generation", "inactive", "not-due"])
def test_bootstrap_observation_does_not_survive_identity_or_eligibility_changes(reset):
    clock = Clock()
    tracker = RefreshProgressTracker(clock=clock)
    plugin = instance()
    observe(tracker, [plugin])
    clock.value = 900
    assert observe(tracker, [plugin])["data_stalled_count"] == 1

    if reset == "revision":
        plugin = replace(plugin, settings_revision=2)
    elif reset == "generation":
        plugin = replace(plugin, structural_generation=2)
    elif reset == "inactive":
        observe(tracker, [])
    else:
        static = replace(plugin, refresh=MappingProxyType({}))
        assert observe(tracker, [static], cache=[plugin.instance_uuid])["data_overdue_count"] == 0

    result = observe(tracker, [plugin])

    assert result["data_stalled_count"] == 0
    assert result["oldest_data_overdue_seconds"] == 0.0


def test_data_success_and_retry_expiration_do_not_hide_unadopted_presentation():
    tracker = RefreshProgressTracker(clock=Clock())
    plugin = instance()
    old = NOW - timedelta(seconds=1800)
    states = {plugin.instance_uuid: InstanceRuntimeState(
        data=RefreshLaneState(
            last_success_at=(NOW - timedelta(seconds=1500)).isoformat(),
            next_retry_at=(NOW + timedelta(seconds=1)).isoformat(),
        ),
        presentation_request=request(plugin, requested_at=old, prepared=True),
    )}
    initial = observe(tracker, [plugin], states, cache=states, presentation=states)
    assert initial["data_stalled_count"] == 1
    assert initial["presentation_stalled_count"] == 1
    assert initial["data_backoff_count"] == 1
    expired = observe(tracker, [plugin], states, cache=states, presentation=states,
                      now=NOW + timedelta(seconds=2))
    assert expired["data_backoff_count"] == 0
    assert expired["data_stalled_count"] == 1

    states[plugin.instance_uuid] = replace(states[plugin.instance_uuid], data=RefreshLaneState(
        last_success_at=(NOW + timedelta(seconds=2)).isoformat(),
    ))
    recovered = observe(tracker, [plugin], states, cache=states, presentation=states,
                        now=NOW + timedelta(seconds=2))
    assert recovered["data_stalled_count"] == 0
    assert recovered["presentation_stalled_count"] == 1


@pytest.mark.parametrize("last_success", ["bad-date", 123, float("inf"), (NOW + timedelta(days=1)).isoformat()])
def test_untrusted_data_timestamps_cannot_immediately_report_a_stall(last_success):
    tracker = RefreshProgressTracker(clock=Clock())
    plugin = instance()
    states = {plugin.instance_uuid: InstanceRuntimeState(data=RefreshLaneState(
        last_success_at=last_success,
        last_attempt_at={"private-error": "invalid"},
    ))}

    result = observe(tracker, [plugin], states, cache=states)

    assert result["data_stalled_count"] == 0
    assert result["oldest_data_overdue_seconds"] in {None, 0.0}


def test_unrepresentable_interval_does_not_prevent_other_instances_being_observed():
    tracker = RefreshProgressTracker(clock=Clock())
    malformed = instance("too-large", interval=1e308)
    normal = instance()
    states = {plugin.instance_uuid: InstanceRuntimeState(data=RefreshLaneState(
        last_success_at=(NOW - timedelta(seconds=1500)).isoformat(),
    )) for plugin in (malformed, normal)}

    result = observe(tracker, [malformed, normal], states, cache=states)

    assert result["data_overdue_count"] == 1
    assert result["data_stalled_count"] == 1
    assert result["oldest_data_overdue_seconds"] == 1200.0


def test_future_and_disabled_presentation_requests_do_not_count_as_stalled():
    tracker = RefreshProgressTracker(clock=Clock())
    future, disabled = instance("future", interval=None), instance("disabled", interval=None)
    states = {
        future.instance_uuid: InstanceRuntimeState(presentation_request=request(
            future, requested_at=NOW + timedelta(days=1),
        )),
        disabled.instance_uuid: InstanceRuntimeState(presentation_request=request(
            disabled, requested_at=NOW - timedelta(days=1),
        )),
    }

    result = observe(tracker, [future, disabled], states, cache=states,
                     presentation=[future.instance_uuid])

    assert result["presentation_pending_count"] == 0
    assert result["presentation_stalled_count"] == 0
    assert result["obsolete_presentation_count"] == 1
    assert result["oldest_presentation_pending_seconds"] is None


def test_prepared_request_waits_two_complete_playlist_rounds_before_stall():
    tracker = RefreshProgressTracker(clock=Clock())
    plugins = [instance(f"plugin-{index}", interval=None) for index in range(27)]
    pending = plugins[0]
    states = {pending.instance_uuid: InstanceRuntimeState(
        presentation_request=request(pending, requested_at=NOW, prepared=True),
    )}
    keys = [plugin.instance_uuid for plugin in plugins]

    waiting = observe(tracker, plugins, states, cache=keys, presentation=keys,
                      now=NOW + timedelta(seconds=16199))
    stalled = observe(tracker, plugins, states, cache=keys, presentation=keys,
                      now=NOW + timedelta(seconds=16200))

    assert waiting["presentation_stall_threshold_seconds"] == 16200.0
    assert waiting["presentation_pending_count"] == 1
    assert waiting["presentation_stalled_count"] == 0
    assert stalled["presentation_stalled_count"] == 1


def test_new_success_resets_bootstrap_observation_even_if_cache_is_still_missing():
    clock = Clock()
    tracker = RefreshProgressTracker(clock=clock)
    plugin = instance()
    observe(tracker, [plugin])
    clock.value = 900
    assert observe(tracker, [plugin])["data_stalled_count"] == 1
    states = {plugin.instance_uuid: InstanceRuntimeState(data=RefreshLaneState(
        last_success_at=NOW.isoformat(),
    ))}

    result = observe(tracker, [plugin], states)

    assert result["data_overdue_count"] == 1
    assert result["data_stalled_count"] == 0
    assert result["never_succeeded_count"] == 0
    assert result["oldest_data_overdue_seconds"] == 0.0
