"""Known guard and catch-up expiry times bound the next idle scheduler wait."""

from datetime import timedelta
import os

import pytest

from tests.test_refresh_completion_admission import START, _completion_case
from tests.test_refresh_task import (
    JobStatus, RefreshIntent, RefreshLane, ResourceSample,
    _seed_theme_last_good, _theme_manifest, _write_runtime_cache, _write_runtime_theme_cache,
)


@pytest.mark.parametrize(
    ("plugin_id", "window_attribute", "initial_resources", "later_resources"),
    [
        ("ticketmaster_events", "_ticketmaster_liveness_window", ResourceSample(110, 50), ResourceSample(110, 50)),
        ("sports_dashboard", "_sports_liveness_window", ResourceSample(113, 50), ResourceSample(113, 50)),
        ("weather", "_weather_liveness_window", ResourceSample(142, 50), ResourceSample(130, 50)),
    ],
    ids=["ticketmaster", "sports", "weather"],
)
def test_quiet_window_expiry_releases_due_ordinary_work_without_an_extra_idle_poll(
    tmp_path, monkeypatch, plugin_id, window_attribute, initial_resources, later_resources,
):
    case = _completion_case(tmp_path, monkeypatch, first_plugin=plugin_id, resources=initial_resources)
    for index, plugin in enumerate(case.playlist.plugins):
        instance = plugin.snapshot()
        cache = _write_runtime_cache(case.task, instance)
        os.utime(cache, (case.clock.wall_time(), case.clock.wall_time()))
        case.task.runtime_state.record_success(
            instance.instance_uuid,
            (START - timedelta(hours=20) if index == 0 else START).isoformat(),
            lane=RefreshLane.DATA,
        )

    # Create the real default quiet window. Do not shorten its duration or
    # alter the resource thresholds that decide whether a renderer may start.
    assert case.task._run_one_iteration_for_test() is None
    window = getattr(case.task, window_attribute)
    assert window is not None
    expiry = window.deadline_monotonic
    case.clock.advance(expiry - case.clock.monotonic() - 5)
    case.resources[0] = later_resources

    assert case.task._run_one_iteration_for_test() is None
    assert case.rendered == []
    ordinary = case.playlist.plugins[1].snapshot()
    case.task.runtime_state.record_success(
        ordinary.instance_uuid,
        (START - timedelta(hours=1)).isoformat(),
        lane=RefreshLane.DATA,
    )
    deadline = case.task.scheduler_snapshot().next_attempt_monotonic
    case.clock.advance(deadline - case.clock.monotonic())
    entry = case.task._run_one_iteration_for_test()

    assert entry is not None
    assert entry.command.plugin_id == "bambu_monitor"
    assert entry.command.intent is RefreshIntent.DATA_REFRESH
    assert case.task.refresh_queue.get_job(entry.job.id).status is JobStatus.SUCCEEDED
    assert case.rendered == [("bambu_monitor", expiry)]


def test_theme_catchup_uses_its_own_future_retry_deadline(tmp_path, monkeypatch):
    case = _completion_case(tmp_path, monkeypatch, count=1)
    instance = case.playlist.plugins[0].snapshot()
    case.config.config.update({"theme_mode": "night", "active_theme": "night"})
    manifest = _theme_manifest(instance.plugin_id, supported=True)
    case.config.get_plugin = lambda key: {"id": key, "_manifest": manifest}
    cache = _write_runtime_theme_cache(case.task, instance, "day")
    os.utime(cache, (case.clock.wall_time(), case.clock.wall_time()))
    _seed_theme_last_good(case.task, instance, "day", START)
    case.task.runtime_state.record_theme_catchup_failure(
        instance.instance_uuid, "night", (START - timedelta(seconds=10)).isoformat(),
        "previous theme catch-up failure", (START + timedelta(seconds=5)).isoformat(),
    )

    assert case.task._run_one_iteration_for_test() is None
    deadline = case.task.scheduler_snapshot().next_attempt_monotonic
    case.clock.advance(deadline - case.clock.monotonic())
    entry = case.task._run_one_iteration_for_test()

    assert entry is not None
    assert entry.command.intent is RefreshIntent.THEME_CATCHUP
    assert case.task.refresh_queue.get_job(entry.job.id).status is JobStatus.SUCCEEDED
    assert case.rendered == [("live_radar", 5.0)]


def test_quiet_window_expiring_during_probe_rechecks_as_soon_as_probe_finishes(tmp_path, monkeypatch):
    case = _completion_case(tmp_path, monkeypatch, first_plugin="weather", resources=ResourceSample(142, 50))
    for index, plugin in enumerate(case.playlist.plugins):
        instance = plugin.snapshot()
        cache = _write_runtime_cache(case.task, instance)
        os.utime(cache, (case.clock.wall_time(), case.clock.wall_time()))
        case.task.runtime_state.record_success(
            instance.instance_uuid,
            (START - timedelta(hours=20) if index == 0 else START).isoformat(),
            lane=RefreshLane.DATA,
        )
    assert case.task._run_one_iteration_for_test() is None
    expiry = case.task._weather_liveness_window.deadline_monotonic
    case.clock.advance(expiry - case.clock.monotonic() - 5)
    case.resources[0] = ResourceSample(130, 50)
    original_deferral = case.task._record_lane_resource_pressure_deferral

    def slow_persistence(*args, **kwargs):
        result = original_deferral(*args, **kwargs)
        case.clock.advance(6)
        return result

    monkeypatch.setattr(case.task, "_record_lane_resource_pressure_deferral", slow_persistence)
    assert case.task._run_one_iteration_for_test() is None
    probe_finished = case.clock.monotonic()
    assert probe_finished == expiry + 1
    ordinary = case.playlist.plugins[1].snapshot()
    case.task.runtime_state.record_success(
        ordinary.instance_uuid,
        (START - timedelta(hours=1)).isoformat(),
        lane=RefreshLane.DATA,
    )
    deadline = case.task.scheduler_snapshot().next_attempt_monotonic
    case.clock.advance(max(0, deadline - case.clock.monotonic()))
    entry = case.task._run_one_iteration_for_test()

    assert entry is not None and entry.command.plugin_id == "bambu_monitor"
    assert case.task.refresh_queue.get_job(entry.job.id).status is JobStatus.SUCCEEDED
    assert case.rendered == [("bambu_monitor", probe_finished)]
    assert case.task._weather_liveness_window is None
    # Generated files use the host clock. Keep the newly promoted cache's
    # mtime coherent with the virtual success time, so the next turn is truly
    # idle rather than a legitimate missing-cache bootstrap for Bambu.
    os.utime(
        case.task._snapshot_cache_path(entry.command, None),
        (case.clock.wall_time(), case.clock.wall_time()),
    )
    assert case.task._run_one_iteration_for_test() is None
    attempts = case.task.attempt_count
    assert case.task._run_one_iteration_for_test() is None
    assert case.task.attempt_count == attempts


def test_ordinary_handoff_expiry_releases_specialized_work_at_its_deadline(tmp_path, monkeypatch):
    case = _completion_case(tmp_path, monkeypatch, count=1, first_plugin="ticketmaster_events")
    instance = case.playlist.plugins[0].snapshot()
    cache = _write_runtime_cache(case.task, instance)
    os.utime(cache, (case.clock.wall_time(), case.clock.wall_time()))
    case.task.runtime_state.record_success(
        instance.instance_uuid, (START - timedelta(hours=20)).isoformat(), lane=RefreshLane.DATA,
    )
    # The normal Weather handoff gives ordinary DATA one bounded opportunity.
    # This playlist only has specialized work, so its existing timer must end.
    case.task._request_burst_liveness_ordinary_yield()
    assert case.task._run_one_iteration_for_test() is None
    expiry = case.task._burst_liveness_yield_deadline_monotonic
    case.clock.advance(expiry - case.clock.monotonic() - 5)
    monkeypatch.setattr(case.task, "running", True)
    case.task.signal_config_change()

    assert case.task._run_one_iteration_for_test() is None
    assert case.rendered == []
    deadline = case.task.scheduler_snapshot().next_attempt_monotonic
    case.clock.advance(deadline - case.clock.monotonic())
    entry = case.task._run_one_iteration_for_test()

    assert entry is not None and entry.command.plugin_id == "ticketmaster_events"
    assert entry.command.intent is RefreshIntent.DATA_REFRESH
    assert case.task.refresh_queue.get_job(entry.job.id).status is JobStatus.SUCCEEDED
    assert case.rendered == [("ticketmaster_events", expiry)]
    assert case.task._burst_liveness_yield_ordinary_pending is False
    os.utime(
        case.task._snapshot_cache_path(entry.command, None),
        (case.clock.wall_time(), case.clock.wall_time()),
    )
    assert case.task._run_one_iteration_for_test() is None
    attempts = case.task.attempt_count
    assert case.task._run_one_iteration_for_test() is None
    assert case.task.attempt_count == attempts
