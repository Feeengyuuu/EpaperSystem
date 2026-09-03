"""A terminal IAN job releases the scheduler without losing instance backoff."""

from datetime import datetime
import os
from pathlib import Path

import pytest

from tests.test_refresh_completion_admission import (
    IanResourceSample, JobStatus, PluginRefreshDeferred, ResourceSample,
    _completion_case, _runtime_plugin_data,
)


def _sync_generated_cache_times(case):
    # The fixture's wall clock is virtual while image writes use filesystem time.
    for cache_file in Path(case.config.plugin_image_dir).rglob("*.png"):
        os.utime(cache_file, (case.clock.wall_time(), case.clock.wall_time()))


def test_ian_success_hands_worker_to_other_due_data_without_idle_poll(tmp_path, monkeypatch):
    case = _completion_case(
        tmp_path, monkeypatch, first_plugin="sports_dashboard",
        ian_resource_sampler=lambda: IanResourceSample(available_mb=512, swap_percent=0),
    )

    first = case.task._run_one_iteration_for_test()
    after_first = case.clock.monotonic()
    _sync_generated_cache_times(case)
    second = case.task._run_one_iteration_for_test()

    assert first.command.plugin_id == "sports_dashboard"
    assert case.task.refresh_queue.get_job(first.job.id).status is JobStatus.SUCCEEDED
    assert case.task.refresh_health_snapshot()["ian_last_queue_status"] == "succeeded"
    assert second is not None, "terminal IAN work must release already-due unrelated data immediately"
    assert second.command.plugin_id == "bambu_monitor"
    assert case.rendered == [("sports_dashboard", 0.0), ("bambu_monitor", after_first)]
    _sync_generated_cache_times(case)
    assert case.task._run_one_iteration_for_test() is None


@pytest.mark.parametrize(
    ("terminal_error", "status", "error_code"),
    [
        (RuntimeError("sports provider unavailable"), JobStatus.FAILED, "refresh_failed"),
        (
            PluginRefreshDeferred(reason="provider_cooldown", phase="fetch", minimum_seconds=1800),
            JobStatus.CANCELED,
            "plugin_refresh_deferred",
        ),
    ],
)
def test_ian_failure_or_deferral_releases_other_data_and_keeps_own_retry(
    tmp_path, monkeypatch, terminal_error, status, error_code,
):
    case = _completion_case(
        tmp_path, monkeypatch, first_plugin="sports_dashboard",
        ian_resource_sampler=lambda: IanResourceSample(available_mb=512, swap_percent=0),
    )

    def fail_only_sports(command):
        if command.plugin_id == "sports_dashboard":
            raise terminal_error

    case.after_render.append(fail_only_sports)
    first = case.task._run_one_iteration_for_test()
    terminal = case.task.refresh_queue.get_job(first.job.id)
    sports_state = case.task.runtime_state.snapshot().instances[first.command.instance_uuid].data
    second = case.task._run_one_iteration_for_test()

    assert terminal.status is status
    assert terminal.error_code == error_code
    assert case.task.refresh_health_snapshot()["ian_last_queue_status"] == status.value
    assert sports_state.last_success_at is None
    assert datetime.fromisoformat(sports_state.next_retry_at) > case.now()
    assert second is not None, "an IAN failure must not pause an unrelated healthy instance"
    assert second.command.plugin_id == "bambu_monitor"
    assert case.task.refresh_queue.get_job(second.job.id).status is JobStatus.SUCCEEDED
    _sync_generated_cache_times(case)

    assert case.task._run_one_iteration_for_test() is None
    assert case.task.runtime_state.snapshot().instances[first.command.instance_uuid].data == sports_state
    assert case.rendered == [("sports_dashboard", 0.0), ("bambu_monitor", 5.0)]


def test_canceling_retained_ian_job_releases_other_data_without_erasing_resource_retry(
    tmp_path, monkeypatch,
):
    ian_resources = [IanResourceSample(available_mb=100, swap_percent=0)]
    case = _completion_case(
        tmp_path, monkeypatch, first_plugin="sports_dashboard",
        ian_resource_sampler=lambda: ian_resources[0],
    )
    retained = case.task._run_one_iteration_for_test()
    sports_uuid = retained.command.instance_uuid
    deferred_state = case.task.runtime_state.snapshot().instances[sports_uuid].data
    assert case.task.refresh_queue.get_job(retained.job.id).status is JobStatus.RUNNING
    assert datetime.fromisoformat(deferred_state.next_retry_at) > case.now()
    assert case.task.refresh_queue.cancel_instance(sports_uuid) == 1
    ian_resources[0] = IanResourceSample(available_mb=512, swap_percent=0)
    case.clock.advance(1)

    canceled = case.task._run_one_iteration_for_test()
    second = case.task._run_one_iteration_for_test()

    assert canceled.command.id == retained.command.id
    assert case.task.refresh_queue.get_job(retained.job.id).status is JobStatus.CANCELED
    assert case.task.refresh_health_snapshot()["ian_retained"] == 0
    assert second is not None, "canceling retained IAN work must release other due data"
    assert second.command.plugin_id == "bambu_monitor"
    assert case.task.refresh_queue.get_job(second.job.id).status is JobStatus.SUCCEEDED
    _sync_generated_cache_times(case)

    assert case.task._run_one_iteration_for_test() is None
    assert case.task.runtime_state.snapshot().instances[sports_uuid].data == deferred_state
    assert case.rendered == [("bambu_monitor", 1.0)]


def test_ian_deferral_allows_due_ordinary_work_without_early_ian_retry(tmp_path, monkeypatch):
    sampled_at = []

    def sample_ian_resources():
        sampled_at.append(case.clock.monotonic())
        return IanResourceSample(available_mb=100, swap_percent=0)

    case = _completion_case(
        tmp_path, monkeypatch, first_plugin="sports_dashboard",
        ian_resource_sampler=sample_ian_resources,
    )

    retained = case.task._run_one_iteration_for_test()
    deferred_state = case.task.runtime_state.snapshot().instances[retained.command.instance_uuid].data
    ordinary = case.task._run_one_iteration_for_test()

    assert retained.command.plugin_id == "sports_dashboard"
    assert case.task.refresh_queue.get_job(retained.job.id).status is JobStatus.RUNNING
    assert ordinary is not None, "IAN deferral must not idle the healthy ordinary worker"
    assert ordinary.command.plugin_id == "bambu_monitor"
    assert case.task.refresh_queue.get_job(ordinary.job.id).status is JobStatus.SUCCEEDED
    assert case.task.refresh_health_snapshot()["ian_retained"] == 1
    assert case.task.runtime_state.snapshot().instances[retained.command.instance_uuid].data == deferred_state
    assert sampled_at == [0.0], "ordinary work must not trigger an early IAN resource retry"
    assert case.rendered == [("bambu_monitor", 0.0)]


def test_ready_ian_resume_is_not_starved_by_continuously_due_ordinary_work(tmp_path, monkeypatch):
    sampled_at = []

    def sample_ian_resources():
        sampled_at.append(case.clock.monotonic())
        return IanResourceSample(available_mb=100 if len(sampled_at) == 1 else 512, swap_percent=0)

    case = _completion_case(
        tmp_path, monkeypatch, first_plugin="sports_dashboard",
        ian_resource_sampler=sample_ian_resources,
    )
    for index in (3, 4):
        entry = _runtime_plugin_data(
            "bambu_monitor", f"Pending Bambu {index}", latest_refresh_time=None, interval=300,
        )
        entry["instance_uuid"] = f"{index:032x}"
        case.playlist.add_plugin(entry)

    retained = case.task._run_one_iteration_for_test()
    ordinary = case.task._run_one_iteration_for_test()
    assert ordinary is not None and ordinary.command.plugin_id == "bambu_monitor"
    assert sampled_at == [0.0]
    assert case.clock.monotonic() == 5
    _sync_generated_cache_times(case)

    resumed = case.task._run_one_iteration_for_test()
    assert resumed is not None and resumed.command.id == retained.command.id
    assert case.task.refresh_queue.get_job(retained.job.id).status is JobStatus.SUCCEEDED
    assert sampled_at == [0.0, 5.0]
    _sync_generated_cache_times(case)

    following_ordinary = case.task._run_one_iteration_for_test()
    assert following_ordinary is not None
    assert following_ordinary.command.instance_uuid == f"{3:032x}"
    assert case.rendered == [
        ("bambu_monitor", 0.0), ("sports_dashboard", 5.0), ("bambu_monitor", 10.0),
    ]


def test_cleanup_recovery_to_healthy_releases_other_due_data(tmp_path, monkeypatch):
    case = _completion_case(
        tmp_path, monkeypatch, first_plugin="sports_dashboard",
        ian_resource_sampler=lambda: IanResourceSample(available_mb=512, swap_percent=0),
    )
    cleanup_events = []

    def render_leaves_soft_pressure(command):
        if command.plugin_id == "sports_dashboard":
            case.resources[0] = ResourceSample(135, 20)

    def memory_cleanup(_reason, *, command=None, **_kwargs):
        if command is not None and command.plugin_id == "sports_dashboard":
            cleanup_events.append((case.clock.monotonic(), case.resources[0].available_mb))
            case.clock.advance(3)
            case.resources[0] = ResourceSample(512, 0)

    case.after_render.append(render_leaves_soft_pressure)
    monkeypatch.setattr(case.task, "_run_memory_maintenance", memory_cleanup)

    first = case.task._run_one_iteration_for_test()
    _sync_generated_cache_times(case)
    second = case.task._run_one_iteration_for_test()

    assert case.task.refresh_queue.get_job(first.job.id).status is JobStatus.SUCCEEDED
    assert cleanup_events == [(5.0, 135)]
    assert second is not None, "successful cleanup must be observed before deciding to idle"
    assert second.command.plugin_id == "bambu_monitor"
    assert case.rendered == [("sports_dashboard", 0.0), ("bambu_monitor", 8.0)]
