"""Successful ordinary refreshes hand back to full scheduler admission."""

from datetime import datetime, timezone
from types import SimpleNamespace
from pathlib import Path
import os
import sys

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tests.test_refresh_task import (
    CapturePlugin, CommandKind, CommandSource, DiskPressureTier, IanResourceSample,
    JobStatus, ManualRefresh, PluginRefreshDeferred, RefreshCommand, RefreshIntent,
    ResourcePressureDeferred, ResourceSample, RetryRegistry, RuntimeClock,
    _make_runtime_task, _runtime_playlist, _runtime_plugin_data, _theme_manifest,
)


START = datetime(2026, 9, 3, 12, tzinfo=timezone.utc)


def _completion_case(tmp_path, monkeypatch, *, count=2, resources=None,
                     first_plugin="live_radar", **task_kwargs):
    entries = []
    for index, plugin_id in enumerate((first_plugin, "bambu_monitor")[:count]):
        entry = _runtime_plugin_data(plugin_id, plugin_id, latest_refresh_time=None, interval=300)
        entry["instance_uuid"] = f"{index + 1:032x}"
        entries.append(entry)
    playlist = _runtime_playlist(*entries)
    clock = RuntimeClock(wall=START.timestamp())
    now = lambda: datetime.fromtimestamp(clock.wall_time(), timezone.utc)
    task, config, _clock = _make_runtime_task(
        tmp_path, playlists=[playlist], clock=clock, **task_kwargs,
    )
    config.config.update({
        "active_theme": "day", "theme_mode": "day", "display_triggered_refresh_enabled": False,
    })
    config.refresh_info.refresh_time = START.isoformat()
    manifests = {entry["plugin_id"]: _theme_manifest(entry["plugin_id"], supported=False) for entry in entries}
    config.get_plugin = lambda key: {"id": key, "_manifest": manifests[key]}
    resource = [resources or ResourceSample(512, 0)]
    monkeypatch.setattr(task, "_get_current_datetime", now)
    monkeypatch.setattr(task, "_resource_sample", lambda: resource[0])
    monkeypatch.setattr(task, "_memory_watchdog_should_restart", lambda: False)
    monkeypatch.setattr(task, "_sample_disk_pressure", lambda: DiskPressureTier.HEALTHY)
    monkeypatch.setattr(task, "_run_cache_lifecycle_maintenance", lambda *_a, **_k: False)
    monkeypatch.setattr(task, "_run_memory_maintenance", lambda *_a, **_k: None)
    rendered = []
    after_render = []

    def render(command, resolved, context):
        rendered.append((command.plugin_id, clock.monotonic()))
        clock.advance(5)
        context.raise_if_cancelled()
        task._set_render_metadata(True, True, config.get_plugin(command.plugin_id))
        if after_render:
            after_render[0](command)
        return Image.new("RGB", (32, 16), "white")

    monkeypatch.setattr(task, "_render_playlist_command", render)
    return SimpleNamespace(task=task, config=config, clock=clock, playlist=playlist,
                           now=now, resources=resource, rendered=rendered, after_render=after_render)


@pytest.mark.parametrize("first_plugin", ["live_radar", "telegram_digest", "newspaper"])
def test_successful_ordinary_refresh_rechecks_due_work_without_idle_poll(
    tmp_path, monkeypatch, first_plugin,
):
    case = _completion_case(tmp_path, monkeypatch, first_plugin=first_plugin)

    first = case.task._run_one_iteration_for_test()
    after_first = case.clock.monotonic()
    second = case.task._run_one_iteration_for_test()

    assert first.command.plugin_id == first_plugin
    assert case.task.refresh_queue.get_job(first.job.id).status is JobStatus.SUCCEEDED
    assert second is not None, "due ordinary work should not wait for the idle 30-second scheduler poll"
    assert second.command.plugin_id == "bambu_monitor"
    assert second.command.intent is RefreshIntent.DATA_REFRESH
    assert case.rendered == [(first_plugin, 0.0), ("bambu_monitor", after_first)]


def test_successful_completion_with_no_due_work_returns_to_idle_poll(tmp_path, monkeypatch):
    case = _completion_case(tmp_path, monkeypatch, count=1)

    first = case.task._run_one_iteration_for_test()
    # The filesystem uses real wall time; the cache and promotion must agree
    # with the simulated clock before asserting that no bootstrap is due.
    for cache_file in Path(case.config.plugin_image_dir).rglob("*.png"):
        os.utime(cache_file, (case.clock.wall_time(), case.clock.wall_time()))
    empty = case.task._run_one_iteration_for_test()
    attempts = case.task.attempt_count
    repeated_empty = case.task._run_one_iteration_for_test()

    assert case.task.refresh_queue.get_job(first.job.id).status is JobStatus.SUCCEEDED
    assert empty is None and repeated_empty is None
    assert case.task.scheduler_snapshot().next_attempt_monotonic > case.clock.monotonic()
    assert case.task.attempt_count == attempts
    assert case.rendered == [("live_radar", 0.0)]


@pytest.mark.parametrize(
    ("terminal_error", "status", "error_code"),
    [
        (RuntimeError("provider failed"), JobStatus.FAILED, "refresh_failed"),
        (
            PluginRefreshDeferred(reason="provider_not_ready", phase="fetch", minimum_seconds=1800),
            JobStatus.CANCELED, "plugin_refresh_deferred",
        ),
        (
            ResourcePressureDeferred(reason="image_resource_pressure", phase="render",
                                     available_mb=80, swap_percent=70),
            JobStatus.CANCELED, "resource_pressure_deferred",
        ),
    ],
)
def test_failure_and_deferral_keep_own_retry_without_delaying_other_work(
    tmp_path, monkeypatch, terminal_error, status, error_code,
):
    case = _completion_case(tmp_path, monkeypatch)

    def fail(command):
        if command.plugin_id == "live_radar":
            raise terminal_error

    case.after_render.append(fail)
    first = case.task._run_one_iteration_for_test()
    own_retry = case.task.runtime_state.snapshot().instances[first.command.instance_uuid].data.next_retry_at
    second = case.task._run_one_iteration_for_test()
    finished = case.task.refresh_queue.get_job(first.job.id)

    assert finished.status is status
    assert finished.error_code == error_code
    assert second.command.plugin_id == "bambu_monitor"
    assert case.task.refresh_queue.get_job(second.job.id).status is JobStatus.SUCCEEDED
    assert own_retry is not None
    assert case.task.runtime_state.snapshot().instances[first.command.instance_uuid].data.next_retry_at == own_retry
    assert case.rendered == [("live_radar", 0.0), ("bambu_monitor", 5.0)]


@pytest.mark.parametrize("gate", ["hard_memory", "hard_disk", "restart"])
def test_next_admission_rechecks_hard_gates_after_success(tmp_path, monkeypatch, gate):
    case = _completion_case(tmp_path, monkeypatch)
    first = case.task._run_one_iteration_for_test()
    if gate == "hard_memory":
        case.resources[0] = ResourceSample(60, 0)
    elif gate == "hard_disk":
        monkeypatch.setattr(case.task, "_sample_disk_pressure", lambda: DiskPressureTier.HARD)
    else:
        monkeypatch.setattr(case.task, "_memory_watchdog_should_restart", lambda: True)

    blocked = case.task._run_one_iteration_for_test()
    attempts = case.task.attempt_count
    still_blocked = case.task._run_one_iteration_for_test()

    assert case.task.refresh_queue.get_job(first.job.id).status is JobStatus.SUCCEEDED
    assert blocked is None and still_blocked is None
    assert case.task.attempt_count == attempts
    assert case.rendered == [("live_radar", 0.0)]


def test_next_admission_preserves_soft_spacing(tmp_path, monkeypatch):
    case = _completion_case(tmp_path, monkeypatch, resources=ResourceSample(135, 20))

    first = case.task._run_one_iteration_for_test()
    blocked = case.task._run_one_iteration_for_test()
    admitted = None
    for _ in range(3):
        next_attempt = case.task.scheduler_snapshot().next_attempt_monotonic
        case.clock.advance(next_attempt - case.clock.monotonic())
        admitted = case.task._run_one_iteration_for_test()
        if admitted is not None:
            break

    assert case.task.refresh_queue.get_job(first.job.id).status is JobStatus.SUCCEEDED
    assert blocked is None
    assert admitted is not None and admitted.command.plugin_id == "bambu_monitor"
    assert case.rendered == [("live_radar", 0.0), ("bambu_monitor", 60.0)]


def test_manual_command_still_takes_priority_after_background_success(tmp_path, monkeypatch):
    case = _completion_case(tmp_path, monkeypatch)
    manual_calls = []
    monkeypatch.setattr("refresh_task.get_plugin_instance", lambda _config: CapturePlugin(manual_calls))
    case.task._run_one_iteration_for_test()
    manual = case.task._command_from_refresh_action(
        ManualRefresh("live_radar", {"id": "urgent-manual"}),
    )
    case.task.refresh_queue.submit(manual)

    next_entry = case.task._run_one_iteration_for_test()

    assert next_entry.command.id == manual.id
    assert case.task.refresh_queue.get_job(next_entry.job.id).status is JobStatus.SUCCEEDED
    assert [call["id"] for call in manual_calls] == ["urgent-manual"]
    assert case.rendered == [("live_radar", 0.0)]


def test_stop_during_completion_does_not_start_another_due_renderer(tmp_path, monkeypatch):
    case = _completion_case(tmp_path, monkeypatch)
    case.after_render.append(lambda _command: case.task.stop_event.set())

    case.task._run_one_iteration_for_test()
    blocked = case.task._run_one_iteration_for_test()

    assert blocked is None
    assert case.rendered == [("live_radar", 0.0)]


def test_success_does_not_erase_an_active_global_scheduler_retry(tmp_path, monkeypatch):
    case = _completion_case(tmp_path, monkeypatch)
    retry_at = []

    def scheduler_failed_while_renderer_finished(_command):
        now = case.clock.monotonic()
        delay = case.task.retry_registry.mark_failure(RetryRegistry.GLOBAL_KEY, now)
        retry_at.append(now + delay)
        case.task.scheduler_state.set_next_attempt(retry_at[0])

    case.after_render.append(scheduler_failed_while_renderer_finished)
    first = case.task._run_one_iteration_for_test()
    blocked = case.task._run_one_iteration_for_test()

    assert case.task.refresh_queue.get_job(first.job.id).status is JobStatus.SUCCEEDED
    assert blocked is None
    assert case.task.scheduler_snapshot().next_attempt_monotonic == retry_at[0]
    assert case.task.retry_registry.next_delay(RetryRegistry.GLOBAL_KEY, case.clock.monotonic()) > 0
    assert case.rendered == [("live_radar", 0.0)]


def test_success_rechecks_ordinary_work_during_ian_retry_wait(tmp_path, monkeypatch):
    case = _completion_case(
        tmp_path, monkeypatch,
        ian_resource_sampler=lambda: IanResourceSample(available_mb=100, swap_percent=0),
    )
    case.config.config["ian_admission_retry_seconds"] = 30
    sports = RefreshCommand.create(
        kind=CommandKind.CACHE_REFRESH, source=CommandSource.BACKGROUND,
        plugin_id="sports_dashboard", instance_uuid="33333333333333333333333333333333",
        structural_generation=1, settings_revision=1,
        payload={"playlist_name": case.playlist.name}, now_monotonic=0,
        deadline_monotonic=60, priority=10, intent=RefreshIntent.DATA_REFRESH,
    )
    sports_job = case.task.refresh_queue.submit(sports)
    retained = case.task._run_one_iteration_for_test()
    ordinary = case.task._playlist_command(
        case.playlist.name, case.playlist.plugins[0].snapshot(),
        source=CommandSource.BACKGROUND, intent=RefreshIntent.DATA_REFRESH,
        kind=CommandKind.CACHE_REFRESH, display_cached_only=False, current_dt=case.now(),
    )
    case.task.refresh_queue.submit(ordinary)
    case.task.scheduler_state.set_next_attempt(30)

    first = case.task._run_one_iteration_for_test()
    following = case.task._run_one_iteration_for_test()

    assert retained.command.id == sports.id
    assert case.task.refresh_queue.get_job(sports_job.id).status is JobStatus.RUNNING
    assert first.command.id == ordinary.id
    assert case.task.refresh_queue.get_job(first.job.id).status is JobStatus.SUCCEEDED
    assert following.command.plugin_id == "bambu_monitor"
    assert case.task.refresh_queue.get_job(sports_job.id).status is JobStatus.RUNNING
    assert case.rendered == [("live_radar", 0.0), ("bambu_monitor", 5.0)]


def test_queued_ian_preempts_completion_then_yields_ordinary_work_during_retry(tmp_path, monkeypatch):
    case = _completion_case(
        tmp_path, monkeypatch,
        ian_resource_sampler=lambda: IanResourceSample(available_mb=100, swap_percent=0),
    )
    first = case.task._run_one_iteration_for_test()
    sports = RefreshCommand.create(
        kind=CommandKind.CACHE_REFRESH, source=CommandSource.BACKGROUND,
        plugin_id="sports_dashboard", instance_uuid="33333333333333333333333333333333",
        structural_generation=1, settings_revision=1,
        payload={"playlist_name": case.playlist.name}, now_monotonic=case.clock.monotonic(),
        deadline_monotonic=60, priority=10, intent=RefreshIntent.DATA_REFRESH,
    )
    sports_job = case.task.refresh_queue.submit(sports)

    retained = case.task._run_one_iteration_for_test()
    following = case.task._run_one_iteration_for_test()

    assert case.task.refresh_queue.get_job(first.job.id).status is JobStatus.SUCCEEDED
    assert retained.command.id == sports.id
    assert case.task.refresh_queue.get_job(sports_job.id).status is JobStatus.RUNNING
    assert following.command.plugin_id == "bambu_monitor"
    assert case.clock.monotonic() == 10
    assert case.rendered == [("live_radar", 0.0), ("bambu_monitor", 5.0)]
