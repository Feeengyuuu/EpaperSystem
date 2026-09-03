"""An unavailable retry store must not turn failures into immediate retry loops."""

from datetime import timedelta
import os

import pytest

from tests.test_refresh_completion_admission import (
    JobStatus, PluginRefreshDeferred, ResourcePressureDeferred, ResourceSample, RetryRegistry,
    _completion_case,
)
from tests.test_refresh_task import _write_runtime_cache


@pytest.mark.parametrize(
    ("kind", "expected_status", "expected_code"),
    [
        ("failure", JobStatus.FAILED, "refresh_failed"),
        ("resource_deferral", JobStatus.CANCELED, "resource_pressure_deferred"),
        ("provider_deferral", JobStatus.CANCELED, "plugin_refresh_deferred"),
    ],
)
def test_retry_bookkeeping_failure_keeps_a_bounded_wait_before_reexecution(
    tmp_path, monkeypatch, kind, expected_status, expected_code,
):
    case = _completion_case(tmp_path, monkeypatch, count=1)
    calls = []

    def render(command, _resolved, _context):
        calls.append((command.plugin_id, case.clock.monotonic()))
        if kind == "resource_deferral":
            raise ResourcePressureDeferred(
                reason="image_resource_pressure", phase="render",
                available_mb=80, swap_percent=70,
            )
        if kind == "provider_deferral":
            raise PluginRefreshDeferred(
                reason="provider_not_ready", phase="fetch", minimum_seconds=1800,
            )
        raise RuntimeError("provider failed")

    def unavailable_retry_store(*_args, **_kwargs):
        raise RuntimeError("retry bookkeeping unavailable")

    monkeypatch.setattr(case.task, "_render_playlist_command", render)
    if kind == "failure":
        monkeypatch.setattr(case.task, "_record_command_failure", unavailable_retry_store)
    else:
        # Both typed deferrals persist their lane deadline through this store.
        monkeypatch.setattr(case.task.runtime_state, "record_deferral", unavailable_retry_store)

    first = case.task._run_one_iteration_for_test()
    terminal = case.task.refresh_queue.get_job(first.job.id)
    assert terminal.status is expected_status
    assert terminal.error_code == expected_code

    second = case.task._run_one_iteration_for_test()
    assert second is None, "a failed retry write must not immediately reexecute the same provider"
    attempts = case.task.attempt_count
    assert case.task._run_one_iteration_for_test() is None
    assert case.task.attempt_count == attempts
    assert calls == [("live_radar", 0.0)]
    assert case.task.retry_registry.next_delay(RetryRegistry.GLOBAL_KEY, case.clock.monotonic()) > 0
    assert case.task.scheduler_snapshot().next_attempt_monotonic > case.clock.monotonic()


def test_retry_write_failure_during_selection_stops_new_renderer_submission(tmp_path, monkeypatch):
    case = _completion_case(
        tmp_path, monkeypatch, first_plugin="ticketmaster_events",
        resources=ResourceSample(100, 20),
    )

    def unavailable_retry_store(*_args, **_kwargs):
        raise RuntimeError("retry bookkeeping unavailable")

    # Ticketmaster cannot start at 100 MiB. Persisting that deferral fails
    # while the normal selector also has a due, otherwise-admissible Bambu.
    monkeypatch.setattr(case.task.runtime_state, "record_deferral", unavailable_retry_store)

    entry = case.task._run_one_iteration_for_test()

    assert entry is None, "GLOBAL retry established during selection must gate this same submission"
    assert case.rendered == []
    assert case.task.retry_registry.next_delay(RetryRegistry.GLOBAL_KEY, case.clock.monotonic()) > 0
    assert case.task.scheduler_snapshot().next_attempt_monotonic > case.clock.monotonic()


def test_rotation_deadline_retry_write_failure_keeps_a_bounded_scheduler_wait(tmp_path, monkeypatch):
    case = _completion_case(tmp_path, monkeypatch, count=1, first_plugin="newspaper")
    instance = case.playlist.plugins[0].snapshot()
    cache = _write_runtime_cache(case.task, instance)
    os.utime(cache, (case.clock.wall_time(), case.clock.wall_time()))
    case.config.config.update({
        "display_triggered_refresh_enabled": True,
        "manual_update_timeout_seconds": 0.1,
    })
    case.config.refresh_info.refresh_time = (case.now() - timedelta(seconds=300)).isoformat()
    calls = []

    def exceed_deadline(command, _resolved, context):
        calls.append((command.plugin_id, case.clock.monotonic()))
        case.clock.advance(1)
        context.raise_if_cancelled()

    def unavailable_retry_store(*_args, **_kwargs):
        raise RuntimeError("retry bookkeeping unavailable")

    monkeypatch.setattr(case.task, "_render_playlist_command", exceed_deadline)
    monkeypatch.setattr(case.task.runtime_state, "record_failure", unavailable_retry_store)

    first = case.task._run_one_iteration_for_test()
    assert first.command.payload.get("automatic_rotation") is True
    terminal = case.task.refresh_queue.get_job(first.job.id)
    assert terminal.status is JobStatus.ABANDONED
    assert terminal.error_code == "deadline_expired"
    assert case.task.runtime_state.snapshot().instances[instance.instance_uuid].data.next_retry_at is None

    second = case.task._run_one_iteration_for_test()

    assert second is None, "failed rotation deadline bookkeeping must not immediately reenter scheduling"
    attempts = case.task.attempt_count
    assert case.task._run_one_iteration_for_test() is None
    assert case.task.attempt_count == attempts
    assert calls == [("newspaper", 0.0)]
    assert case.task.retry_registry.next_delay(RetryRegistry.GLOBAL_KEY, case.clock.monotonic()) > 0
    assert case.task.scheduler_snapshot().next_attempt_monotonic > case.clock.monotonic()
