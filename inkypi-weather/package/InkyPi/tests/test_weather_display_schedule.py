"""Weather fetch/display ordering through the real coordinator and cache catalog."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from PIL import Image
from model import RefreshInfo

from plugins.base_plugin.render_provenance import SourceProvenance, attach_source_provenance
from runtime.refresh_contracts import CommandKind, RefreshIntent, JobStatus, TaskDeadlineExceeded
from runtime.refresh_policy import ResourceSample
from tests.test_refresh_task import (
    _make_runtime_task, _runtime_playlist, _runtime_plugin_data, _theme_manifest,
    _write_runtime_theme_cache,
)


def weather_runtime(tmp_path, monkeypatch, *, cached=False):
    playlist = _runtime_playlist(_runtime_plugin_data("weather", latest_refresh_time=None))
    task, device, clock = _make_runtime_task(tmp_path, playlists=[playlist])
    manifest = _theme_manifest("weather")
    manifest = replace(manifest, capabilities=replace(manifest.capabilities, refresh_data_before_display=True))
    device.get_plugin = lambda name: {"id": name, "_manifest": manifest}
    device.config.update(theme_mode="day", active_theme="day")
    now = datetime(2026, 9, 8, 12, tzinfo=timezone.utc)
    clock.wall_value = now.timestamp()
    device.refresh_info = RefreshInfo(refresh_time=(now - timedelta(minutes=10)).isoformat(), image_hash="old")
    monkeypatch.setattr(task, "_get_current_datetime", lambda: now)
    monkeypatch.setattr(task, "_has_theme_changed", lambda *_args: False)
    monkeypatch.setattr(task, "_resource_sample", lambda: ResourceSample(available_mb=512, swap_percent=0))
    instance = playlist.plugins[0].snapshot()
    if cached:
        _write_runtime_theme_cache(task, instance, "day")
    return task, device, instance, now


@pytest.mark.parametrize("cached", [False, True])
def test_weather_waits_for_its_turn_even_if_interval_expired_or_cache_missing(tmp_path, monkeypatch, cached):
    task, device, instance, now = weather_runtime(tmp_path, monkeypatch, cached=cached)
    assert task._select_independent_refresh_command(now) is None
    command = task._select_cached_display_command(now)
    assert command is not None
    assert command.instance_uuid == instance.instance_uuid
    assert command.kind is CommandKind.DISPLAY
    assert command.intent is RefreshIntent.DATA_REFRESH
    assert command.force


@pytest.mark.parametrize("provenance", list(SourceProvenance))
def test_only_a_live_weather_result_can_replace_the_display(tmp_path, monkeypatch, provenance):
    task, device, instance, now = weather_runtime(tmp_path, monkeypatch, cached=True)
    events = []

    class WeatherProvider:
        config = {"id": "weather"}

        def render_themed_image(self, settings, _device, **kwargs):
            assert settings["forceRefresh"] is True
            assert settings["_inkypiFreshDisplay"] is True
            events.append("fetch")
            return attach_source_provenance(Image.new("RGB", (800, 480), "white"), provenance)

    monkeypatch.setattr("refresh_task.get_plugin_instance", lambda _config: WeatherProvider())
    original_display = task.display_manager.display_image

    def display(*args, **kwargs):
        events.append("display")
        return original_display(*args, **kwargs)

    monkeypatch.setattr(task.display_manager, "display_image", display)
    command = task._select_cached_display_command(now)
    if provenance is SourceProvenance.LIVE:
        task._execute_command(command)
        assert events == ["fetch", "display"]
        assert task.runtime_state.snapshot().displayed_instance_uuid == instance.instance_uuid
    else:
        previous = Path(task._snapshot_cache_path(instance, "day")).read_bytes()
        with pytest.raises(RuntimeError, match="fresh provider"):
            task._execute_command(command)
        assert events == ["fetch"]
        assert Path(task._snapshot_cache_path(instance, "day")).read_bytes() == previous


def test_failed_weather_is_not_retried_on_every_rotation_tick(tmp_path, monkeypatch):
    task, device, instance, now = weather_runtime(tmp_path, monkeypatch, cached=True)
    task.runtime_state.record_failure(
        instance.instance_uuid, now.isoformat(),
        "provider unavailable", next_retry_at=(now + timedelta(minutes=5)).isoformat(),
    )
    assert task._select_cached_display_command(now) is None


def test_manual_weather_display_also_fetches_before_writing(tmp_path, monkeypatch):
    task, _, instance, _ = weather_runtime(tmp_path, monkeypatch)
    task.running = True
    job = task.submit_playlist_display(instance.instance_uuid)
    command = task.refresh_queue.get_entry(job["id"]).command
    assert command.kind is CommandKind.DISPLAY
    assert command.intent is RefreshIntent.DATA_REFRESH
    assert command.payload["fresh_display"] is True


@pytest.mark.parametrize("global_display_refresh", [False, True])
def test_low_memory_defers_weather_without_starting_a_provider(tmp_path, monkeypatch, global_display_refresh):
    task, device, instance, now = weather_runtime(tmp_path, monkeypatch, cached=True)
    device.config["display_triggered_refresh_enabled"] = global_display_refresh
    command = task._select_cached_display_command(now)
    monkeypatch.setattr(task, "_resource_sample", lambda: ResourceSample(available_mb=120, swap_percent=0))
    monkeypatch.setattr("refresh_task.get_plugin_instance", lambda _: pytest.fail("provider started under low memory"))
    task.refresh_queue.submit(command)
    task._execute_queue_entry(task.refresh_queue.take(timeout=0))
    entry = task.refresh_queue.get_entry(command.id)
    assert entry.job.status is JobStatus.CANCELED
    assert entry.job.error_code == "weather_browser_start_margin"
    assert task._select_cached_display_command(now) is None
    assert not task.display_manager.calls


def test_expired_weather_turn_releases_its_reservation_and_backs_off(tmp_path, monkeypatch):
    task, device, instance, now = weather_runtime(tmp_path, monkeypatch)
    command = task._select_cached_display_command(now)
    assert task._record_pending_rotation_deadline_failure(command, TaskDeadlineExceeded("expired"), now)
    manager = device.get_playlist_manager()
    assert not manager.validate_rotation_reservation(instance.instance_uuid, expected_playlist_name=command.payload["playlist_name"])
    assert task._select_cached_display_command(now) is None
