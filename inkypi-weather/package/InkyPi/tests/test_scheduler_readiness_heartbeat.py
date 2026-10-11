"""Readiness budgets the scheduler from its committed poll, not the rotation countdown."""

import os
from pathlib import Path
from types import SimpleNamespace

from health import HealthCollector, HealthPublisher, ReadinessEvaluator
from tests.test_refresh_completion_admission import JobStatus, RefreshIntent, _completion_case
from tests.test_refresh_task import RefreshLane, _write_runtime_cache


def _rotation_case(tmp_path, monkeypatch):
    """Probe at 291 s with fresh data; the cached rotation is due at 300 s."""
    case = _completion_case(tmp_path, monkeypatch, first_plugin="sports_dashboard")
    for plugin in case.playlist.plugins:
        instance = plugin.snapshot()
        cache_path = _write_runtime_cache(case.task, instance)
        os.utime(cache_path, (case.clock.wall_time(), case.clock.wall_time()))
        case.task.runtime_state.record_success(
            instance.instance_uuid, case.now().isoformat(), lane=RefreshLane.DATA,
        )
    collector = HealthCollector(
        HealthPublisher(release_id="release-test", clock=case.clock.monotonic, started_monotonic=-1000.0),
        refresh_task=case.task,
        device_config=case.config,
        runtime_paths=SimpleNamespace(data_dir=Path(tmp_path)),
        dev_mode=False,
        startup_state=lambda: {"degraded": False, "reasons": {}},
        clock=case.clock.monotonic,
    )

    def stalled():
        snapshot = collector.collect_once()
        scheduler = snapshot.components["scheduler"]
        assert "error_code" not in scheduler and scheduler["heartbeat_monotonic"] is not None
        verdict = ReadinessEvaluator().evaluate(snapshot, now_monotonic=case.clock.monotonic())
        return "scheduler_stalled" in verdict.error_codes

    case.stalled = stalled
    case.clock.advance(291)
    assert case.task._run_one_iteration_for_test() is None
    assert case.task.scheduler_snapshot().next_attempt_monotonic == 300.0
    return case


def test_scheduler_sleeping_until_an_imminent_rotation_is_not_stalled(tmp_path, monkeypatch):
    case = _rotation_case(tmp_path, monkeypatch)
    stalled_at = []
    for _second in range(9):
        case.clock.advance(1)
        if case.stalled():
            stalled_at.append(case.clock.monotonic())

    assert stalled_at == [], (
        "the scheduler committed to sleeping until the 300 s rotation; a shrinking "
        "rotation countdown must not turn that sleep into scheduler_stalled"
    )


def test_rotation_preflight_and_post_command_maintenance_are_not_stalled(tmp_path, monkeypatch):
    case = _rotation_case(tmp_path, monkeypatch)
    case.clock.advance(9)
    manager = case.config.get_playlist_manager()
    reserve = manager.reserve_next_active_instance
    original_display = case.task.display_manager.display_image
    verdicts = {}
    writes = []

    def slow_reservation(*args, **kwargs):
        selection = reserve(*args, **kwargs)
        case.clock.advance(3)  # rotation reservation and config write on the SD card
        verdicts["rotation preflight"] = case.stalled()
        return selection

    def display(image, image_settings=None):
        writes.append(case.clock.monotonic())
        case.clock.advance(60)  # full-refresh e-paper write
        return original_display(image, image_settings)

    def memory_maintenance(*_args, **_kwargs):
        case.clock.advance(2)  # gc.collect() and malloc_trim() after the command
        verdicts["post-command maintenance"] = case.stalled()

    monkeypatch.setattr(manager, "reserve_next_active_instance", slow_reservation)
    monkeypatch.setattr(case.task.display_manager, "display_image", display)
    monkeypatch.setattr(case.task, "_run_memory_maintenance", memory_maintenance)

    entry = case.task._run_one_iteration_for_test()

    assert entry is not None and entry.command.intent is RefreshIntent.DISPLAY_CACHE
    assert case.task.refresh_queue.get_job(entry.job.id).status is JobStatus.SUCCEEDED
    assert writes == [303.0]
    assert verdicts == {"rotation preflight": False, "post-command maintenance": False}
    assert case.stalled() is False


def test_scheduler_that_misses_two_idle_polls_is_still_stalled(tmp_path, monkeypatch):
    case = _rotation_case(tmp_path, monkeypatch)

    case.clock.advance(60)
    assert case.stalled() is False
    case.clock.advance(1)
    assert case.stalled() is True


def test_worker_stuck_after_withdrawing_command_deadline_is_still_stalled(tmp_path, monkeypatch):
    case = _rotation_case(tmp_path, monkeypatch)
    case.clock.advance(9)
    original_display = case.task.display_manager.display_image
    observed = []

    def display(image, image_settings=None):
        case.clock.advance(20)
        return original_display(image, image_settings)

    def stuck_maintenance(*_args, **_kwargs):
        observed.append(("finished", case.clock.monotonic()))
        observed.append(("heartbeat", case.task.scheduler_snapshot().heartbeat_monotonic))
        case.clock.advance(60)
        observed.append(("stalled", case.stalled()))
        case.clock.advance(1)
        observed.append(("stalled", case.stalled()))

    monkeypatch.setattr(case.task.display_manager, "display_image", display)
    monkeypatch.setattr(case.task, "_run_memory_maintenance", stuck_maintenance)

    entry = case.task._run_one_iteration_for_test()

    assert entry is not None and entry.command.intent is RefreshIntent.DISPLAY_CACHE
    assert observed == [
        ("finished", 320.0), ("heartbeat", 320.0), ("stalled", False), ("stalled", True),
    ]
