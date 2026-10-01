from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from PIL import Image

from plugins.plugin_manifest import PluginManifest
from runtime.refresh_contracts import CommandKind, CommandSource, JobStatus, RefreshIntent
from tests.test_refresh_task import (
    CapturePlugin,
    PLUGIN_SOURCE_ROOT,
    ResourceSample,
    RuntimeClock,
    _make_runtime_task,
    _runtime_playlist,
    _runtime_plugin_data,
    _write_runtime_cache,
    _write_runtime_theme_cache,
)


MOVIE_PLUGINS = ("box_office_top_movies", "china_box_office_top_movies")


def _movie_runtime(tmp_path, monkeypatch, plugin_id, *, missing_media=True):
    now = datetime(2026, 9, 30, 10, tzinfo=timezone.utc)
    last_data = (now - timedelta(minutes=10)).isoformat()
    playlist = _runtime_playlist(
        _runtime_plugin_data(plugin_id, "Movies", interval=21600, latest_refresh_time=last_data),
        _runtime_plugin_data("ordinary", "Other", interval=21600, latest_refresh_time=last_data),
    )
    task, config, _ = _make_runtime_task(
        tmp_path, playlists=[playlist], clock=RuntimeClock(wall=now.timestamp()),
    )
    movie, ordinary = playlist.plugins
    for instance in playlist.plugins:
        instance.settings["themeMode"] = "day"
        _write_runtime_cache(task, instance)
        _write_runtime_theme_cache(task, instance, "day")
        task.runtime_state.record_success(instance.instance_uuid, last_data)
    task.runtime_state.set_display_state(
        "committed", instance_uuid=ordinary.instance_uuid, changed_at=last_data,
    )
    manifest = PluginManifest.from_path(PLUGIN_SOURCE_ROOT / plugin_id / "plugin-info.json")
    config.get_plugin = lambda pid: {"id": pid, "_manifest": manifest} if pid == plugin_id else {"id": pid}
    calls = []
    plugin = CapturePlugin(calls)
    plugin.wants_background_live_refresh = lambda *_: missing_media
    plugin.get_live_refresh_state = lambda *_: {"active": missing_media, "interval_seconds": 300}
    monkeypatch.setattr(task, "_get_plugin_for_snapshot", lambda *_, **__: plugin)
    monkeypatch.setattr(task, "_get_plugin_instance", lambda *_: plugin)
    monkeypatch.setattr(task, "_get_current_datetime", lambda: now)
    monkeypatch.setattr(task, "_resource_sample", lambda: ResourceSample(512, 0))
    return task, movie, now, calls


@pytest.mark.parametrize("plugin_id", MOVIE_PLUGINS)
def test_missing_movie_media_can_refresh_offscreen_without_forcing_display(tmp_path, monkeypatch, plugin_id):
    task, movie, now, _ = _movie_runtime(tmp_path, monkeypatch, plugin_id)

    command = task._select_independent_refresh_command(now)

    assert command is not None and command.instance_uuid == movie.instance_uuid
    assert command.intent is RefreshIntent.LIVE_REFRESH
    assert command.kind is CommandKind.CACHE_REFRESH
    assert command.force is False
    assert command.payload["background_live_refresh"] is True
    assert task._live_display_target_is_current(command)
    resolved = task._resolve_playlist_command(command)
    assert resolved is not None
    assert task._enqueue_live_display_followup(command, resolved, now, "day") is None


@pytest.mark.parametrize("plugin_id", MOVIE_PLUGINS)
def test_movie_media_live_updates_canonical_without_data_freshness_or_display(tmp_path, monkeypatch, plugin_id):
    task, movie, now, calls = _movie_runtime(tmp_path, monkeypatch, plugin_id)
    before = task.runtime_state.snapshot()
    latest_refresh = movie.latest_refresh_time
    command = task._select_independent_refresh_command(now)
    assert command is not None

    submitted = task.refresh_queue.submit(command)
    task._process_queue_entry(task.refresh_queue.take(timeout=0))

    assert task.refresh_queue.get_entry(submitted.id).job.status is JobStatus.SUCCEEDED
    assert len(calls) == 1 and calls[0]["_movie_media_only"] is True
    after = task.runtime_state.snapshot()
    assert after.instances[movie.instance_uuid].data == before.instances[movie.instance_uuid].data
    assert movie.latest_refresh_time == latest_refresh
    assert after.instances[movie.instance_uuid].live.last_success_at == now.isoformat()
    assert after.displayed_instance_uuid == before.displayed_instance_uuid
    assert after.display_state == before.display_state
    assert after.display_commit_id == before.display_commit_id
    canonical = Path(task.cache_path_for_snapshot(movie.snapshot()))
    with Image.open(canonical) as image:
        assert image.getpixel((0, 0)) == (255, 255, 255)
    assert task.display_manager.calls == []
    assert task.refresh_queue.take(timeout=0) is None
    assert task._select_independent_refresh_command(now + timedelta(seconds=299)) is None


@pytest.mark.parametrize("plugin_id", MOVIE_PLUGINS)
@pytest.mark.parametrize("missing_media,available_mb", [(False, 512), (True, 60)])
def test_movie_media_live_obeys_missing_media_hook_and_resource_admission(
    tmp_path, monkeypatch, plugin_id, missing_media, available_mb,
):
    task, _, now, _ = _movie_runtime(tmp_path, monkeypatch, plugin_id, missing_media=missing_media)
    monkeypatch.setattr(task, "_resource_sample", lambda: ResourceSample(available_mb, 0))

    assert task._select_independent_refresh_command(now) is None


@pytest.mark.parametrize("plugin_id", MOVIE_PLUGINS)
def test_normal_movie_data_refresh_does_not_set_media_only(tmp_path, monkeypatch, plugin_id):
    task, movie, now, calls = _movie_runtime(tmp_path, monkeypatch, plugin_id)
    command = task._playlist_command(
        "DailyDoseOfDay", movie.snapshot(), source=CommandSource.BACKGROUND,
        intent=RefreshIntent.DATA_REFRESH, force=False, display_cached_only=False,
        kind=CommandKind.CACHE_REFRESH, current_dt=now,
    )

    task._execute_command(command)

    assert len(calls) == 1 and calls[0]["_movie_media_only"] is False
    assert task.display_manager.calls == []
