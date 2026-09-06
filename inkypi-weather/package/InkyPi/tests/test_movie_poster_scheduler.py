from datetime import datetime, timezone
from types import SimpleNamespace

from plugins.plugin_manifest import PluginManifest
from runtime.refresh_contracts import RefreshIntent
from tests.test_refresh_task import (
    _make_runtime_task, _runtime_playlist, _runtime_plugin_data, _write_runtime_cache,
    PLUGIN_SOURCE_ROOT,
)


def test_missing_movie_media_can_refresh_offscreen_without_forcing_display(tmp_path, monkeypatch):
    now = datetime(2026, 9, 5, 10, tzinfo=timezone.utc)
    playlist = _runtime_playlist(
        _runtime_plugin_data('box_office_top_movies', 'Movies', interval=21600, latest_refresh_time=now.isoformat()),
        _runtime_plugin_data('ordinary', 'Other', interval=21600, latest_refresh_time=now.isoformat()),
    )
    task, config, _ = _make_runtime_task(tmp_path, playlists=[playlist])
    movie, ordinary = playlist.plugins
    for instance in playlist.plugins:
        _write_runtime_cache(task, instance)
        task.runtime_state.record_success(instance.instance_uuid, now.isoformat())
    task.runtime_state.set_display_state('committed', instance_uuid=ordinary.instance_uuid, changed_at=now.isoformat())
    manifest = PluginManifest.from_path(PLUGIN_SOURCE_ROOT / 'box_office_top_movies/plugin-info.json')
    config.get_plugin = lambda pid: {'id': pid, '_manifest': manifest} if pid == movie.plugin_id else {'id': pid}
    plugin = SimpleNamespace(
        wants_background_live_refresh=lambda *_: True,
        get_live_refresh_state=lambda *_: {'active': True, 'interval_seconds': 300},
    )
    monkeypatch.setattr(task, '_get_plugin_for_snapshot', lambda *_, **__: plugin)
    command = task._select_independent_refresh_command(now)
    assert command is not None and command.instance_uuid == movie.instance_uuid
    assert command.intent is RefreshIntent.LIVE_REFRESH
    assert command.payload['background_live_refresh'] is True
    assert task._live_display_target_is_current(command)
    resolved = task._resolve_playlist_command(command)
    assert resolved is not None
    assert task._enqueue_live_display_followup(command, resolved, now, 'day') is None
