"""An expected non-cacheable theme catch-up is a warning, not an error."""

import logging
from pathlib import Path

import refresh_task as refresh_task_module
from runtime.refresh_contracts import JobStatus
from runtime.refresh_policy import ResourceSample
from tests.test_refresh_task import (
    ThemeOnlyRecordingPlugin,
    _one_pending_theme_catchup,
    _prepare_theme_catchup_runtime,
)


def _run_theme_catchup(monkeypatch, name, plugin_factory):
    task, _device_config, playlist, configs, current_dt = _prepare_theme_catchup_runtime(name)
    monkeypatch.setattr(task, "_resource_sample", lambda: ResourceSample(available_mb=512, swap_percent=0))
    monkeypatch.setattr(task, "_cache_refresh_under_resource_pressure", lambda: False)
    monkeypatch.setattr(task, "_get_current_datetime", lambda: current_dt)
    plugin = plugin_factory(configs["fallback"])
    monkeypatch.setattr("refresh_task.get_plugin_instance", lambda _config: plugin)
    command, target = _one_pending_theme_catchup(task, playlist, current_dt)
    submitted = task.refresh_queue.submit(command)
    task._process_queue_entry(task.refresh_queue.take(timeout=0))
    state = task.runtime_state.snapshot().instances[target.instance_uuid]
    return task, submitted, target, state, current_dt


def _failure_records(caplog):
    return [
        record for record in caplog.records
        if record.name == "refresh_task" and "Refresh command failed" in record.getMessage()
    ]


def test_noncacheable_theme_catchup_logs_warning_without_traceback(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="refresh_task")

    class NoncacheableThemePlugin(ThemeOnlyRecordingPlugin):
        def render_themed_image(self, *args, **kwargs):
            image = super().render_themed_image(*args, **kwargs)
            image.info[refresh_task_module.SKIP_CACHE_IMAGE_INFO_KEY] = True
            return image

    task, submitted, target, state, current_dt = _run_theme_catchup(
        monkeypatch, "theme-catchup-expected-warning", NoncacheableThemePlugin,
    )

    entry = task.refresh_queue.get_entry(submitted.id)
    assert entry.job.status is JobStatus.FAILED
    assert entry.job.error_code == "refresh_failed"
    assert "theme catch-up did not produce an exact cacheable image" in entry.job.error
    [record] = _failure_records(caplog)
    assert record.levelno == logging.WARNING
    assert record.exc_info is None
    assert "THEME_CATCHUP" in record.getMessage()
    assert "plugin_id: fallback" in record.getMessage()
    assert "theme catch-up did not produce an exact cacheable image" in record.getMessage()
    assert not [record for record in caplog.records if record.levelno >= logging.ERROR]
    # Catch-up bookkeeping and its cooldown are unchanged.
    assert state.theme_catchup.target_mode == "night"
    assert state.theme_catchup.last_failure_at == current_dt.isoformat()
    assert state.theme_catchup.next_retry_at is not None
    assert not Path(task._snapshot_cache_path(target, "night")).exists()


def test_unexpected_theme_catchup_failure_keeps_error_traceback(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="refresh_task")

    task, submitted, _target, state, current_dt = _run_theme_catchup(
        monkeypatch, "theme-catchup-unexpected-error",
        lambda config: ThemeOnlyRecordingPlugin(config, fail=True),
    )

    assert task.refresh_queue.get_entry(submitted.id).job.status is JobStatus.FAILED
    [record] = _failure_records(caplog)
    assert record.levelno == logging.ERROR
    assert record.exc_info is not None
    assert state.theme_catchup.last_failure_at == current_dt.isoformat()
