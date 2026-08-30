"""Progress is observed through the real scheduler and public health snapshot."""

import json
from datetime import timedelta
from types import SimpleNamespace

from runtime.runtime_state import RefreshLane
import src.refresh_task as refresh_task_module
from tests.test_machine_progress_soak import START, _make_machine


def test_scheduler_reports_stale_data_even_while_the_worker_can_run(
    tmp_path, monkeypatch
):
    task, config, _clock, instances, _providers, _display = _make_machine(
        tmp_path, monkeypatch
    )
    config.refresh_info.refresh_time = START.isoformat()
    for instance in instances:
        task.runtime_state.record_success(
            instance.instance_uuid,
            (START - timedelta(days=1)).isoformat(),
            lane=RefreshLane.DATA,
        )

    assert task.refresh_health_snapshot()["progress"]["observed"] is False
    task._run_one_iteration_for_test()
    progress = task.refresh_health_snapshot()["progress"]

    assert progress["enabled"] is True
    assert progress["observed"] is True
    assert progress["active_instances"] == 3
    assert progress["data_stalled_count"] == 3
    assert progress["oldest_data_overdue_seconds"] == 82800
    assert all(instance.instance_uuid not in json.dumps(progress) for instance in instances)


def test_storage_pressure_cannot_hide_progress_stalls(tmp_path, monkeypatch):
    monkeypatch.setattr(
        refresh_task_module.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(total=10_000_000_000, used=9_900_000_000, free=100_000_000),
    )
    task, config, _clock, instances, _providers, _display = _make_machine(
        tmp_path, monkeypatch
    )
    config.refresh_info.refresh_time = START.isoformat()
    for instance in instances:
        task.runtime_state.record_success(
            instance.instance_uuid,
            (START - timedelta(days=1)).isoformat(),
            lane=RefreshLane.DATA,
        )

    task._run_one_iteration_for_test()

    progress = task.refresh_health_snapshot()["progress"]
    assert progress["observed"] is True
    assert progress["data_stalled_count"] == 3
