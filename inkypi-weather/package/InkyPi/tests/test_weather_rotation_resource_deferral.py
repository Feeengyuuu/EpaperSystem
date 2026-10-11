"""A memory-deferred fresh Weather turn must not hold the whole rotation."""

from dataclasses import replace
from datetime import timedelta

import pytest
from PIL import Image

from plugins.base_plugin.render_provenance import SourceProvenance, attach_source_provenance
from runtime.refresh_contracts import JobStatus
from runtime.refresh_policy import ResourceSample
from tests.test_refresh_task import _runtime_plugin_data, _theme_manifest, _write_runtime_theme_cache
from tests.test_weather_display_schedule import weather_runtime

LOW = ResourceSample(available_mb=147, swap_percent=22)
HEALTHY = ResourceSample(available_mb=180, swap_percent=22)


class _WeatherProvider:
    config = {"id": "weather"}

    def render_themed_image(self, settings, _device, **_kwargs):
        assert settings["_inkypiFreshDisplay"] is True
        return attach_source_provenance(Image.new("RGB", (800, 480), "white"), SourceProvenance.LIVE)


def _rotation_runtime(tmp_path, monkeypatch, *, pending_members):
    task, device, weather, start = weather_runtime(tmp_path, monkeypatch, cached=True)
    manager = device.get_playlist_manager()
    active = manager.snapshot_active_playlist(start)
    names = ["cached_page"] + [f"pending_{index}" for index in range(pending_members)]
    for name in names:
        assert manager.add_plugin_to_playlist_snapshot(active.name, _runtime_plugin_data(name, name))
    members = {instance.plugin_id: instance for instance in manager.snapshot_active_playlist(start).plugins}
    weather_manifest = _theme_manifest("weather")
    weather_manifest = replace(
        weather_manifest, capabilities=replace(weather_manifest.capabilities, refresh_data_before_display=True),
    )
    device.get_plugin = lambda name: {
        "id": name, "_manifest": weather_manifest if name == "weather" else _theme_manifest(name),
    }
    # Only cached_page has an exact-theme cache; pending pages still await
    # their theme catch-up, like the night of 10-09.
    _write_runtime_theme_cache(task, members["cached_page"], "day")
    playlist = manager.playlists[0]
    playlist.plugin_rotation_pool = [instance.instance_uuid for instance in members.values()]
    playlist.plugin_rotation_queue = [weather.instance_uuid] + [
        members[name].instance_uuid for name in names[1:]
    ]
    playlist.plugin_rotation_recent_history = [members["cached_page"].instance_uuid]
    monkeypatch.setattr("refresh_task.get_plugin_instance", lambda _config: _WeatherProvider())
    return task, device, weather, members, start


def _drive(task, clock, monkeypatch, start, seconds, *, sample, step=10):
    """Run scheduler selections every ``step`` seconds and return their outcomes."""
    outcomes = []
    monkeypatch.setattr(task, "_resource_sample", lambda: sample)
    for _ in range(int(seconds // step)):
        clock.advance(step)
        now = start + timedelta(seconds=clock.wall_value - start.timestamp())
        monkeypatch.setattr(task, "_get_current_datetime", lambda now=now: now)
        command = task._select_cached_display_command(now)
        if command is None:
            continue
        task.refresh_queue.submit(command)
        task._execute_queue_entry(task.refresh_queue.take(timeout=0))
        entry = task.refresh_queue.get_entry(command.id)
        outcomes.append((now, command.plugin_id, entry.job.status, entry.job.error_code))
    return outcomes


def _displays(outcomes):
    return [(now, plugin_id) for now, plugin_id, status, _code in outcomes if status is JobStatus.SUCCEEDED]


@pytest.mark.parametrize("pending_members", [0, 3])
def test_low_memory_weather_turn_does_not_stop_the_rotation(tmp_path, monkeypatch, pending_members):
    # 10-09 20:06-20:32: Weather was the only remaining exact-theme member of
    # its round and was re-selected every scheduler poll for 26 minutes.
    task, device, weather, members, start = _rotation_runtime(
        tmp_path, monkeypatch, pending_members=pending_members,
    )
    clock = task._clock.__self__  # the RuntimeClock behind weather_runtime
    assert clock.wall_value == start.timestamp()

    low = _drive(task, clock, monkeypatch, start, 26 * 60, sample=LOW)

    weather_turns = [item for item in low if item[1] == "weather"]
    assert weather_turns, "Weather must still be tried while memory is low"
    assert all(code == "weather_browser_start_margin" for _now, _id, _status, code in weather_turns)
    displays = _displays(low)
    assert [plugin_id for _now, plugin_id in displays] and all(
        plugin_id == "cached_page" for _now, plugin_id in displays
    )
    # The rotation keeps writing the panel instead of polling Weather.
    write_times = [start] + [now for now, _plugin_id in displays]
    gaps = [(later - earlier).total_seconds() for earlier, later in zip(write_times, write_times[1:])]
    assert max(gaps) <= 2 * 300
    assert len(weather_turns) <= len(displays) + 1

    recovered = _drive(task, clock, monkeypatch, start, 15 * 60, sample=HEALTHY)
    assert "weather" in [plugin_id for _now, plugin_id in _displays(recovered)]
    assert task.runtime_state.snapshot().instances[weather.instance_uuid].data.last_success_at is not None


def test_deferred_weather_moves_behind_the_next_round_member(tmp_path, monkeypatch):
    task, device, weather, members, start = _rotation_runtime(tmp_path, monkeypatch, pending_members=0)
    playlist = device.get_playlist_manager().playlists[0]
    other = members["cached_page"].instance_uuid
    playlist.plugin_rotation_queue = [weather.instance_uuid, other]
    playlist.plugin_rotation_recent_history = []
    monkeypatch.setattr(task, "_resource_sample", lambda: LOW)

    selected = task._select_cached_display_command(start)
    assert selected.instance_uuid == weather.instance_uuid
    task.refresh_queue.submit(selected)
    task._execute_queue_entry(task.refresh_queue.take(timeout=0))

    assert task.refresh_queue.get_entry(selected.id).job.error_code == "weather_browser_start_margin"
    assert playlist.plugin_rotation_queue == [other, weather.instance_uuid]
    following = task._select_cached_display_command(start)
    assert following.instance_uuid == other
