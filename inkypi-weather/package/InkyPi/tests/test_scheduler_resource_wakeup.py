"""Known resource spacing deadlines do not acquire another idle poll."""

from datetime import timedelta
import os

from tests.test_refresh_completion_admission import START, _completion_case
from tests.test_refresh_task import JobStatus, ResourceSample, RefreshIntent, RefreshLane, _write_runtime_cache


def test_soft_spacing_wakes_at_admission_deadline_after_longer_execution(tmp_path, monkeypatch):
    case = _completion_case(tmp_path, monkeypatch, resources=ResourceSample(135, 20))
    case.after_render.append(lambda command: case.clock.advance(32)
                             if command.plugin_id == "live_radar" else None)

    first = case.task._run_one_iteration_for_test()
    assert case.task.refresh_queue.get_job(first.job.id).status is JobStatus.SUCCEEDED
    assert case.clock.monotonic() == 37
    assert case.task._run_one_iteration_for_test() is None
    deadline = case.task.scheduler_snapshot().next_attempt_monotonic
    case.clock.advance(deadline - case.clock.monotonic())
    second = case.task._run_one_iteration_for_test()

    assert second.command.plugin_id == "bambu_monitor"
    assert case.rendered == [("live_radar", 0.0), ("bambu_monitor", 60.0)]


def test_rotation_deadline_is_preserved_by_general_wakeup_planning(tmp_path, monkeypatch):
    case = _completion_case(tmp_path, monkeypatch)
    for plugin in case.playlist.plugins:
        instance = plugin.snapshot()
        cache = _write_runtime_cache(case.task, instance)
        os.utime(cache, (case.clock.wall_time(), case.clock.wall_time()))
        case.task.runtime_state.record_success(instance.instance_uuid, START.isoformat(), lane=RefreshLane.DATA)
    case.config.refresh_info.refresh_time = (START - timedelta(seconds=297)).isoformat()

    assert case.task._run_one_iteration_for_test() is None
    deadline = case.task.scheduler_snapshot().next_attempt_monotonic
    case.clock.advance(deadline - case.clock.monotonic())
    entry = case.task._run_one_iteration_for_test()

    assert entry.command.intent is RefreshIntent.DISPLAY_CACHE
    assert case.task.refresh_queue.get_job(entry.job.id).status is JobStatus.SUCCEEDED
    assert deadline == 3.0


def test_terminal_rechecks_soft_resources_when_spacing_already_allows_work(tmp_path, monkeypatch):
    case = _completion_case(tmp_path, monkeypatch)
    case.after_render.append(lambda _command: case.resources.__setitem__(0, ResourceSample(135, 20)))

    first = case.task._run_one_iteration_for_test()
    assert case.task.refresh_queue.get_job(first.job.id).status is JobStatus.SUCCEEDED
    second = case.task._run_one_iteration_for_test()

    assert second is not None
    assert second.command.plugin_id == "bambu_monitor"
    assert case.rendered == [("live_radar", 0.0), ("bambu_monitor", 5.0)]
