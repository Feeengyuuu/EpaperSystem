"""Idle workers wake for known due work without shortening every idle poll."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import os
from zoneinfo import ZoneInfo

import pytest

from tests.test_refresh_completion_admission import START, _completion_case
from tests.test_refresh_policy import _instance, _lane, _presentation_request
from tests.test_refresh_task import (
    DiskPressureTier, JobStatus, NoChangePresentationPlugin, PRESENTATION_NOW,
    PresentationTransactionDisplayManager, RefreshIntent, RefreshLane,
    ResourceSample, RuntimeClock, _install_display_provider_plugin_sentinels,
    _make_presentation_task, _presentation_followup_command, _queue_and_process,
    _seed_prepared_presentation, _seed_presentation_request, _seed_theme_last_good,
    _theme_manifest, _write_runtime_cache, _write_runtime_theme_cache,
)
from runtime.refresh_policy import evaluate_data_due, evaluate_presentation_due
from runtime.runtime_state import InstanceRuntimeState


def test_idle_worker_starts_data_at_its_known_due_time(tmp_path, monkeypatch):
    case = _completion_case(tmp_path, monkeypatch, count=1)
    instance = case.playlist.plugins[0].snapshot()
    cache = _write_runtime_cache(case.task, instance)
    os.utime(cache, (case.clock.wall_time(), case.clock.wall_time()))
    # A 300s instance last succeeded 295s ago. There is no queued job,
    # resource pressure, live work, or panel rotation due in this window.
    case.task.runtime_state.record_success(
        instance.instance_uuid,
        (START - timedelta(seconds=295)).isoformat(),
        lane=RefreshLane.DATA,
    )

    assert case.task._run_one_iteration_for_test() is None
    assert case.rendered == []

    # Follow the deadline that the real scheduler publishes to its idle
    # queue wait; do not inject a 5s polling loop into the test.
    next_attempt = case.task.scheduler_snapshot().next_attempt_monotonic
    case.clock.advance(next_attempt - case.clock.monotonic())
    entry = case.task._run_one_iteration_for_test()

    assert entry is not None
    assert entry.command.intent is RefreshIntent.DATA_REFRESH
    assert case.task.refresh_queue.get_job(entry.job.id).status is JobStatus.SUCCEEDED
    assert case.rendered == [("live_radar", 5.0)]


@pytest.mark.parametrize(
    ("now", "last_success", "scheduled", "expected_utc"),
    [
        (
            datetime(2026, 11, 1, 1, 29, 55, tzinfo=ZoneInfo("America/Los_Angeles"), fold=1),
            datetime(2026, 11, 1, 1, 30, tzinfo=ZoneInfo("America/Los_Angeles"), fold=0),
            "01:30",
            datetime(2026, 11, 1, 9, 30, tzinfo=timezone.utc),
        ),
        (
            datetime(2026, 3, 8, 1, 59, 55, tzinfo=ZoneInfo("America/Los_Angeles")),
            datetime(2026, 3, 7, 2, 30, tzinfo=ZoneInfo("America/Los_Angeles")),
            "02:30",
            datetime(2026, 3, 9, 9, 30, tzinfo=timezone.utc),
        ),
    ],
    ids=["second-fall-occurrence", "skip-nonexistent-spring-occurrence"],
)
def test_future_schedule_deadline_uses_a_real_local_occurrence(now, last_success, scheduled, expected_utc):
    result = evaluate_data_due(
        _instance(refresh={"scheduled": scheduled}),
        InstanceRuntimeState(data=_lane(success=last_success)),
        has_displayable_cache=True,
        now=now,
    )

    assert result.candidate is None
    assert result.next_due_at.astimezone(timezone.utc) == expected_utc


@pytest.mark.parametrize(("retry_seconds", "expected_seconds"), [(2, 5), (12, 12)])
def test_future_data_deadline_waits_for_both_cadence_and_retry(retry_seconds, expected_seconds):
    result = evaluate_data_due(
        _instance(refresh={"interval": 300}),
        InstanceRuntimeState(data=_lane(
            success=START - timedelta(seconds=295),
            retry=START + timedelta(seconds=retry_seconds),
        )),
        has_displayable_cache=True,
        now=START,
    )

    assert result.candidate is None
    assert result.next_due_at == START + timedelta(seconds=expected_seconds)


@pytest.mark.parametrize("prepared", [False, True])
def test_presentation_retry_deadline_only_wakes_an_unprepared_request(prepared):
    result = evaluate_presentation_due(
        _instance(),
        InstanceRuntimeState(
            presentation=_lane(
                attempt=START - timedelta(seconds=10),
                retry=START + timedelta(seconds=5),
            ),
            presentation_request=_presentation_request(
                requested_at=START - timedelta(seconds=20),
                prepared_at=START - timedelta(seconds=2) if prepared else None,
                prepared_theme_mode="day" if prepared else None,
            ),
        ),
        has_displayable_cache=True,
        resolved_theme_mode="day",
        now=START,
    )

    assert result.candidate is None
    assert result.next_due_at == (None if prepared else START + timedelta(seconds=5))


def _live_deadline_case(tmp_path, monkeypatch, *, scan_seconds=0):
    case = _completion_case(tmp_path, monkeypatch, count=1, first_plugin="sports_dashboard")
    instance = case.playlist.plugins[0].snapshot()
    cache = _write_runtime_cache(case.task, instance)
    os.utime(cache, (case.clock.wall_time(), case.clock.wall_time()))
    case.task.runtime_state.record_success(instance.instance_uuid, START.isoformat(), lane=RefreshLane.DATA)
    case.task.runtime_state.record_success(
        instance.instance_uuid, (START - timedelta(seconds=295)).isoformat(), lane=RefreshLane.LIVE,
    )
    case.task.runtime_state.record_failure(
        instance.instance_uuid, (START - timedelta(seconds=10)).isoformat(), "previous live failure",
        (START + timedelta(seconds=12)).isoformat(), lane=RefreshLane.LIVE,
    )
    manifest = case.config.get_plugin(instance.plugin_id)["_manifest"]
    manifest = replace(manifest, capabilities=replace(manifest.capabilities, supports_live_refresh=True))
    case.config.get_plugin = lambda key: {"id": key, "_manifest": manifest}
    # Keep Sports displayed so this fixture isolates retry/deadline publication
    # at its 300s hook cadence. The 900s offscreen floor has separate coverage.
    case.task.runtime_state.set_display_state(
        "committed",
        "sports-live-deadline",
        instance_uuid=instance.instance_uuid,
        changed_at=START.isoformat(),
    )

    class SportsStream(NoChangePresentationPlugin):
        def get_live_refresh_state(self, settings, current_dt):
            case.clock.advance(scan_seconds)
            return {"active": True, "interval_seconds": 300}

        def wants_background_live_refresh(self, settings, current_dt):
            return True

    plugin = SportsStream()
    monkeypatch.setattr("src.refresh_task.get_plugin_instance", lambda _item: plugin)
    return case


def test_idle_worker_starts_background_live_at_cadence_and_retry_deadline(tmp_path, monkeypatch):
    case = _live_deadline_case(tmp_path, monkeypatch)

    assert case.task._run_one_iteration_for_test() is None
    deadline = case.task.scheduler_snapshot().next_attempt_monotonic
    case.clock.advance(deadline - case.clock.monotonic())
    entry = case.task._run_one_iteration_for_test()

    assert entry is not None
    assert entry.command.intent is RefreshIntent.LIVE_REFRESH
    assert case.task.refresh_queue.get_job(entry.job.id).status is JobStatus.SUCCEEDED
    assert case.rendered == [("sports_dashboard", 12.0)]


def test_live_deadline_does_not_add_elapsed_scan_time_again(tmp_path, monkeypatch):
    case = _live_deadline_case(tmp_path, monkeypatch, scan_seconds=3)

    assert case.task._run_one_iteration_for_test() is None

    assert case.clock.monotonic() == 3.0
    # The deadline was 12s from the shared wall/monotonic snapshot. A scan
    # that used 3s leaves 9s to wait; it must not move that deadline to 15s.
    assert case.task.scheduler_snapshot().next_attempt_monotonic == 12.0


def test_prepared_display_failure_retries_at_remaining_five_second_deadline(monkeypatch):
    clock = RuntimeClock(wall=PRESENTATION_NOW.timestamp())
    display_starts = []

    def fail_first_display(_display, _call):
        display_starts.append(clock.monotonic())
        if len(display_starts) == 1:
            raise RuntimeError("panel write failed")

    display = PresentationTransactionDisplayManager(after_display=fail_first_display)
    task, config, clock, playlist, _display = _make_presentation_task(
        "prepared-display-future-deadline", clock=clock, display_manager=display, provider_free=True,
    )
    now = lambda: datetime.fromtimestamp(clock.wall_time(), timezone.utc)
    config.refresh_info.refresh_time = PRESENTATION_NOW.isoformat()
    instance = playlist.plugins[0].snapshot()
    cache = _write_runtime_cache(task, instance)
    os.utime(cache, (clock.wall_time(), clock.wall_time()))
    task.runtime_state.record_success(instance.instance_uuid, PRESENTATION_NOW.isoformat(), lane=RefreshLane.DATA)
    request = _seed_presentation_request(task, instance)
    _seed_prepared_presentation(task, instance, request)
    task.runtime_state.set_display_state(
        "committed", request.origin_display_commit_id,
        instance_uuid=instance.instance_uuid, changed_at=request.requested_at,
    )
    monkeypatch.setattr(task, "_get_current_datetime", now)
    monkeypatch.setattr(task, "_memory_watchdog_should_restart", lambda: False)
    monkeypatch.setattr(task, "_resource_sample", lambda: ResourceSample(512, 0))
    monkeypatch.setattr(task, "_sample_disk_pressure", lambda: DiskPressureTier.HEALTHY)
    monkeypatch.setattr(task, "_run_cache_lifecycle_maintenance", lambda *_a, **_k: False)
    monkeypatch.setattr(task, "_run_memory_maintenance", lambda *_a, **_k: None)
    _install_display_provider_plugin_sentinels(monkeypatch)

    failed = _queue_and_process(task, _presentation_followup_command(task, playlist, instance, request))
    state = task.runtime_state.snapshot().instances[instance.instance_uuid]
    assert failed.job.status is JobStatus.FAILED
    assert state.presentation_receipt is None
    assert state.presentation_request.prepared_at is not None
    retry_at = datetime.fromisoformat(state.presentation.next_retry_at)
    clock.advance(retry_at.timestamp() - clock.wall_time() - 5)

    # Configuration wakes a running worker immediately. Its fresh selection
    # must preserve the five seconds remaining on this exact prepared request.
    monkeypatch.setattr(task, "running", True)
    task.signal_config_change()
    idle_at = clock.monotonic()
    assert task._run_one_iteration_for_test() is None
    deadline = task.scheduler_snapshot().next_attempt_monotonic
    clock.advance(deadline - clock.monotonic())
    retried = task._run_one_iteration_for_test()

    assert retried is not None
    assert retried.command.intent is RefreshIntent.DISPLAY_CACHE
    assert retried.command.payload["presentation_request_id"] == request.request_id
    assert task.refresh_queue.get_job(retried.job.id).status is JobStatus.SUCCEEDED
    assert display_starts[1] == idle_at + 5
    assert task.runtime_state.snapshot().instances[instance.instance_uuid].presentation_receipt.request_id == request.request_id


def test_idle_worker_retries_displayed_theme_at_its_future_deadline(tmp_path, monkeypatch):
    case = _completion_case(tmp_path, monkeypatch, count=1)
    instance = case.playlist.plugins[0].snapshot()
    case.config.config["theme_mode"] = "night"
    manifest = _theme_manifest(instance.plugin_id, supported=True)
    case.config.get_plugin = lambda key: {"id": key, "_manifest": manifest}
    cache = _write_runtime_theme_cache(case.task, instance, "day")
    os.utime(cache, (case.clock.wall_time(), case.clock.wall_time()))
    _seed_theme_last_good(case.task, instance, "day", START)
    case.task.runtime_state.set_display_state(
        "committed", "previous-day-display", instance_uuid=instance.instance_uuid,
        changed_at=START.isoformat(),
    )
    case.task.runtime_state.record_failure(
        instance.instance_uuid, (START - timedelta(seconds=10)).isoformat(), "previous theme failure",
        (START + timedelta(seconds=5)).isoformat(), lane=RefreshLane.THEME,
    )

    assert case.task._run_one_iteration_for_test() is None
    deadline = case.task.scheduler_snapshot().next_attempt_monotonic
    case.clock.advance(deadline - case.clock.monotonic())
    entry = case.task._run_one_iteration_for_test()

    assert entry is not None
    assert entry.command.intent is RefreshIntent.THEME_REDRAW
    assert case.task.refresh_queue.get_job(entry.job.id).status is JobStatus.SUCCEEDED
    assert case.rendered == [("live_radar", 5.0)]


def test_config_change_replaces_the_previous_future_wake_with_current_revision_work(tmp_path, monkeypatch):
    case = _completion_case(tmp_path, monkeypatch, count=1)
    instance = case.playlist.plugins[0].snapshot()
    cache = _write_runtime_cache(case.task, instance)
    os.utime(cache, (case.clock.wall_time(), case.clock.wall_time()))
    case.task.runtime_state.record_success(
        instance.instance_uuid, (START - timedelta(seconds=295)).isoformat(), lane=RefreshLane.DATA,
    )
    assert case.task._run_one_iteration_for_test() is None
    assert case.task.scheduler_snapshot().next_attempt_monotonic == 5.0

    updated = case.config.get_playlist_manager().update_plugin_instance(
        instance.instance_uuid, refresh={"interval": 600},
        expected_generation=instance.structural_generation,
        expected_settings_revision=instance.settings_revision,
    )
    monkeypatch.setattr(case.task, "running", True)
    case.task.signal_config_change()
    entry = case.task._run_one_iteration_for_test()

    # The prior revision's cache is no longer valid after this config edit.
    # Refresh the new revision immediately rather than wait on the old timer.
    assert entry is not None
    assert entry.command.settings_revision == updated.settings_revision
    assert case.task.refresh_queue.get_job(entry.job.id).status is JobStatus.SUCCEEDED
    assert case.rendered == [("live_radar", 0.0)]
