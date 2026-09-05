"""Replay Weather pressure deferrals while other ordinary work stays due."""

from datetime import datetime, timedelta, timezone

import pytest

from tests.test_refresh_task import (
    RefreshLane, ResourceSample, _weather_margin_runtime,
)
from runtime.refresh_queue import QueueFullError
from runtime.refresh_contracts import RefreshIntent


@pytest.mark.parametrize("available_mb", [118, 135, 149])
def test_weather_pressure_recovery_survives_runnable_ordinary_work(monkeypatch, available_mb):
    now = datetime(2026, 9, 4, 21, 15, tzinfo=timezone.utc)
    task, clock, weather, ordinary = _weather_margin_runtime(
        "weather-pressure-recovery-backlog", now, ordinary_due=True,
    )
    sample = [ResourceSample(available_mb=available_mb, swap_percent=18)]
    monkeypatch.setattr(task, "_resource_sample", lambda: sample[0])
    monkeypatch.setattr(task, "_run_memory_maintenance", lambda *_a, **_k: None)

    first = task._select_independent_refresh_command(now)
    assert first is not None and first.instance_uuid == ordinary.instance_uuid

    # An ordinary task can leave more backlog due; Weather's own admission
    # retry must not discard the newly recovered normal browser margin.
    clock.advance(61)
    sample[0] = ResourceSample(available_mb=160, swap_percent=18)
    recovered = task._select_independent_refresh_command(now + timedelta(seconds=61))

    assert recovered is not None and recovered.instance_uuid == weather.instance_uuid
    assert recovered.payload.get("weather_liveness_concession") is not True
    task.runtime_state.record_success(
        weather.instance_uuid, (now + timedelta(seconds=61)).isoformat(), lane=RefreshLane.DATA,
    )
    clock.advance(61)
    following = task._select_independent_refresh_command(now + timedelta(seconds=122))
    assert following is not None and following.instance_uuid == ordinary.instance_uuid


@pytest.mark.parametrize("changed", ["failure", "deferral", "success"])
def test_weather_recovery_cannot_override_new_runtime_outcome(monkeypatch, changed):
    now = datetime(2026, 9, 4, 21, 15, tzinfo=timezone.utc)
    task, clock, weather, ordinary = _weather_margin_runtime(
        "weather-recovery-new-outcome", now, ordinary_due=True,
    )
    sample = [ResourceSample(135, 18)]
    monkeypatch.setattr(task, "_resource_sample", lambda: sample[0])
    monkeypatch.setattr(task, "_run_memory_maintenance", lambda *_a, **_k: None)
    task._select_independent_refresh_command(now)
    at = (now + timedelta(seconds=1)).isoformat()
    retry = (now + timedelta(minutes=15)).isoformat()
    if changed == "failure":
        task.runtime_state.record_failure(weather.instance_uuid, at, "provider failed", next_retry_at=retry)
    elif changed == "deferral":
        task.runtime_state.record_deferral(weather.instance_uuid, at, retry)
    else:
        task.runtime_state.record_success(weather.instance_uuid, at)
    expected = task.runtime_state.snapshot().instances[weather.instance_uuid].data
    clock.advance(61)
    sample[0] = ResourceSample(160, 18)
    command = task._select_independent_refresh_command(now + timedelta(seconds=61))
    assert command is not None and command.instance_uuid == ordinary.instance_uuid
    assert task.runtime_state.snapshot().instances[weather.instance_uuid].data == expected


@pytest.mark.parametrize("gate", ["memory", "swap", "unknown", "retry", "rotation", "soft_spacing"])
def test_weather_recovery_preserves_normal_admission_guards(monkeypatch, gate):
    now = datetime(2026, 9, 4, 21, 15, tzinfo=timezone.utc)
    task, clock, weather, _ordinary = _weather_margin_runtime(
        "weather-recovery-guard", now, ordinary_due=True,
    )
    sample = [ResourceSample(135, 18)]
    monkeypatch.setattr(task, "_resource_sample", lambda: sample[0])
    monkeypatch.setattr(task, "_run_memory_maintenance", lambda *_a, **_k: None)
    task._select_independent_refresh_command(now)
    elapsed = 61
    sample[0] = ResourceSample(160, 18)
    if gate == "memory":
        sample[0] = ResourceSample(118, 18)
    elif gate == "swap":
        sample[0] = ResourceSample(160, 70)
    elif gate == "unknown":
        sample[0] = ResourceSample(None, None)
    elif gate == "retry":
        elapsed = 1
    elif gate == "rotation":
        task._rotation_has_ready_candidates = True
        monkeypatch.setattr(task, "_get_rotation_wait_seconds", lambda: 20)
    else:
        from dataclasses import replace
        task.device_config.config["background_cache_refresh_min_available_mb"] = 175
        task._admission_state = replace(task._admission_state, last_soft_data_admitted_monotonic=60)
    clock.advance(elapsed)
    command = task._select_independent_refresh_command(now + timedelta(seconds=elapsed))
    assert command is None or command.instance_uuid != weather.instance_uuid


@pytest.mark.parametrize("submission", ["rejected", "presentation"])
def test_weather_recovery_is_consumed_by_data_attempt_not_submission(monkeypatch, submission):
    from dataclasses import replace
    now = datetime(2026, 9, 4, 21, 15, tzinfo=timezone.utc)
    task, clock, weather, ordinary = _weather_margin_runtime(
        "weather-recovery-submission", now, ordinary_due=True,
    )
    sample = [ResourceSample(135, 18)]
    monkeypatch.setattr(task, "_resource_sample", lambda: sample[0])
    monkeypatch.setattr(task, "_run_memory_maintenance", lambda *_a, **_k: None)
    task._select_independent_refresh_command(now)
    clock.advance(61)
    sample[0] = ResourceSample(160, 18)
    command = task._select_independent_refresh_command(now + timedelta(seconds=61))
    if submission == "rejected":
        def reject(_command):
            raise QueueFullError("queue full")
        with monkeypatch.context() as patch:
            patch.setattr(task.refresh_queue, "submit", reject)
            with pytest.raises(QueueFullError):
                task._submit_independent_refresh_command(command)
    else:
        task._submit_independent_refresh_command(replace(command, intent=RefreshIntent.THEME_REDRAW))
    clock.advance(61)
    retry = task._select_independent_refresh_command(now + timedelta(seconds=122))
    assert retry is not None and retry.instance_uuid == weather.instance_uuid
    task.runtime_state.record_attempt(weather.instance_uuid, (now + timedelta(seconds=123)).isoformat())
    clock.advance(61)
    following = task._select_independent_refresh_command(now + timedelta(seconds=183))
    assert following is not None and following.instance_uuid == ordinary.instance_uuid


@pytest.mark.parametrize("change", ["settings_revision", "structural_generation", "removed"])
def test_weather_recovery_receipt_does_not_survive_instance_change(monkeypatch, change):
    from dataclasses import replace
    now = datetime(2026, 9, 4, 21, 15, tzinfo=timezone.utc)
    task, clock, weather, ordinary = _weather_margin_runtime(
        "weather-recovery-instance-change", now, ordinary_due=True,
    )
    sample = [ResourceSample(135, 18)]
    monkeypatch.setattr(task, "_resource_sample", lambda: sample[0])
    monkeypatch.setattr(task, "_run_memory_maintenance", lambda *_a, **_k: None)
    task._select_independent_refresh_command(now)
    active = [ordinary] if change == "removed" else [replace(weather, **{change: 2}), ordinary]
    task._weather_pressure_recovery.select(active, [], task.runtime_state.snapshot().instances)
    clock.advance(61)
    sample[0] = ResourceSample(160, 18)
    # Even restoring the old revision cannot revive a discarded receipt.
    command = task._select_independent_refresh_command(now + timedelta(seconds=61))
    assert command is not None and command.instance_uuid == ordinary.instance_uuid
