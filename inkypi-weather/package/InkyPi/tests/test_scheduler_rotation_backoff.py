"""A blocked cached display must not reserve an unusable rotation window."""

from datetime import timedelta
import os

from tests.test_refresh_completion_admission import (
    JobStatus, RefreshIntent, START, _completion_case,
)
from tests.test_refresh_task import _write_runtime_cache


def test_all_display_caches_in_backoff_allow_due_heavy_data(tmp_path, monkeypatch):
    case = _completion_case(tmp_path, monkeypatch, count=1, first_plugin="newspaper")
    instance = case.playlist.plugins[0].snapshot()
    cache = _write_runtime_cache(case.task, instance)
    os.utime(cache, (case.clock.wall_time(), case.clock.wall_time()))
    case.config.refresh_info.refresh_time = (START - timedelta(seconds=300)).isoformat()
    display_retry_key = case.task._rotation_display_retry_key(instance.instance_uuid)
    retry_delay = case.task.retry_registry.mark_failure(display_retry_key, case.clock.monotonic())

    # A real, decodable cache exists, but its physical display retry is not due.
    # There is no available write for the ordinary DATA job to delay.
    assert case.task._get_rotation_wait_seconds() == 0
    assert case.task.retry_registry.next_delay(display_retry_key, case.clock.monotonic()) > 0

    entry = case.task._run_one_iteration_for_test()

    assert entry is not None, "display backoff must not reserve a window that blocks due heavy DATA"
    assert entry.command.plugin_id == "newspaper"
    assert entry.command.intent is RefreshIntent.DATA_REFRESH
    assert case.task.refresh_queue.get_job(entry.job.id).status is JobStatus.SUCCEEDED
    assert case.rendered == [("newspaper", 0.0)]
    assert case.task.retry_registry.next_delay(display_retry_key, case.clock.monotonic()) == retry_delay - 5
