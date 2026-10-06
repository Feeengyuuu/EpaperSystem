"""A timed-out poster provider must give the queue back to cached display."""

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest
from PIL import Image

from plugins.backtothedate.backtothedate import BacktotheDate
from runtime.long_task_executor import current_task_context
from runtime.refresh_contracts import CommandKind, CommandSource, JobStatus, RefreshIntent
from tests.test_refresh_task import (
    _make_runtime_task,
    _runtime_playlist,
    _runtime_plugin_data,
    _theme_manifest,
    _write_runtime_cache,
)


@pytest.mark.parametrize("budget", ["shared_deadline", "local_refill_budget"])
def test_poster_deadline_releases_queue_for_next_cached_display(tmp_path, monkeypatch, budget):
    local_budget = budget == "local_refill_budget"
    poster_data = _runtime_plugin_data("backtothedate", "Poster", latest_refresh_time=None)
    poster_data["plugin_settings"].update(
        {"sourceMode": "mao_era", "maxPage": 0, "attempts": 1}
    )
    playlist = _runtime_playlist(
        poster_data, _runtime_plugin_data("next_page", "Next page")
    )
    task, config, clock = _make_runtime_task(tmp_path, playlists=[playlist])
    poster, next_page = (item.snapshot() for item in playlist.plugins)
    manifest = _theme_manifest("backtothedate", supported=False)
    manifest = replace(
        manifest,
        capabilities=replace(manifest.capabilities, supports_presentation_refresh=True),
    )
    config.get_plugin = lambda key: (
        {"id": key, "_manifest": manifest} if key == "backtothedate" else {"id": key}
    )
    plugin = BacktotheDate(config.get_plugin("backtothedate"))
    plugin_root = tmp_path / "poster-state"
    monkeypatch.setattr(
        plugin, "get_plugin_dir", lambda path=None: str(plugin_root / path if path else plugin_root)
    )
    monkeypatch.setenv("INKYPI_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("INKYPI_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(task, "_get_plugin_instance", lambda _config: plugin)
    old_poster_cache = _write_runtime_cache(task, poster)
    old_poster_bytes = old_poster_cache.read_bytes()
    _write_runtime_cache(task, next_page, Image.new("RGB", (32, 16), "green"))
    fetches = []

    def expired_provider(url, params=None):
        context = current_task_context()
        assert context is not None
        fetches.append(url)
        clock.advance(15 if local_budget else 5)
        context.raise_if_cancelled()
        return "<html>No matching posters in this source.</html>"

    monkeypatch.setattr(plugin, "_fetch_text", expired_provider)
    data_command = task._playlist_command(
        playlist.name,
        poster,
        source=CommandSource.BACKGROUND,
        intent=RefreshIntent.DATA_REFRESH,
        kind=CommandKind.CACHE_REFRESH,
        force=False,
        display_cached_only=False,
        deadline_monotonic=clock.monotonic() + (180 if local_budget else 5),
    )
    data_job = task.refresh_queue.submit(data_command)
    data_entry = task.refresh_queue.take(timeout=0)
    assert data_entry is not None and data_entry.job.id == data_job.id
    # Display becomes pending while the real provider already owns the queue.
    display_command = task._playlist_command(
        playlist.name,
        next_page,
        source=CommandSource.SCHEDULER,
        intent=RefreshIntent.DISPLAY_CACHE,
        deadline_monotonic=clock.monotonic() + (90 if local_budget else 30),
    )
    display_job = task.refresh_queue.submit(display_command)

    task._process_queue_entry(data_entry)

    finished_data = task.refresh_queue.get_job(data_job.id)
    if local_budget:
        assert finished_data.status is JobStatus.CANCELED
        assert finished_data.error_code == "plugin_refresh_deferred"
        lane = task.runtime_state.snapshot().instances[poster.instance_uuid].data
        assert lane.last_failure_at is None
        assert lane.last_success_at is None
        retry_at = datetime.fromisoformat(lane.next_retry_at)
        assert retry_at.timestamp() - clock.wall_time() == 300
        current_dt = datetime.fromtimestamp(clock.wall_time(), timezone.utc)
        assert not any(
            command.instance_uuid == poster.instance_uuid
            for command in task._select_background_commands(current_dt)
        ), "budget-limited refill must not immediately restart"
    else:
        assert finished_data.status is JobStatus.ABANDONED
        assert finished_data.error_code == "deadline_expired"
    assert old_poster_cache.read_bytes() == old_poster_bytes
    assert not Path(task._staging_cache_path(poster)).exists()
    assert task.active_operation_snapshot() is None
    assert current_task_context() is None
    next_entry = task.refresh_queue.take(timeout=0)
    assert next_entry is not None, "poster retries exhausted the next display's deadline"
    assert next_entry.job.id == display_job.id
    task._process_queue_entry(next_entry)

    assert task.refresh_queue.get_job(display_job.id).status is JobStatus.SUCCEEDED
    assert len(task.display_manager.calls) == 1
    assert task.display_manager.calls[0][0].getpixel((0, 0)) == (0, 128, 0)
    assert len(fetches) == (4 if local_budget else 1)
    assert clock.monotonic() == (60 if local_budget else 5)
