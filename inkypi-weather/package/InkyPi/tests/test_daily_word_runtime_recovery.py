"""DailyWord's real render/cache/display path under an upstream outage."""

from datetime import datetime, timezone
from pathlib import Path
import sys

from PIL import Image
import pytest
from requests.exceptions import ReadTimeout

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.daily_word_poem import daily_word_poem as word_module
from plugins.daily_word_poem.daily_word_poem import DailyWordPoem
from runtime.refresh_contracts import CommandKind, CommandSource, RefreshIntent
from runtime.runtime_state import RefreshLane
from tests.test_refresh_task import (
    _make_runtime_task,
    _runtime_playlist,
    _runtime_plugin_data,
    _write_runtime_cache,
    refresh_task_module,
)


@pytest.mark.parametrize("failed_provider", ["dictionary", "wikiquote", "both"])
def test_dailyword_outage_advances_daily_display_without_claiming_provider_recovery(
    tmp_path, monkeypatch, failed_provider,
):
    old_success = "2026-08-26T06:45:46+00:00"
    current = [datetime(2026, 8, 30, 12, tzinfo=timezone.utc)]
    playlist = _runtime_playlist(_runtime_plugin_data(
        "daily_word_poem", "DailyWord", latest_refresh_time=old_success, interval=300,
    ))
    task, device, _clock = _make_runtime_task(tmp_path, playlists=[playlist])
    device.config.update({"theme_mode": "day", "active_theme": "day"})
    monkeypatch.setattr(device, "get_resolution", lambda: (800, 480))
    plugin = DailyWordPoem({"id": "daily_word_poem"})
    monkeypatch.setattr(plugin, "_cache_dir", lambda: tmp_path / "provider")
    monkeypatch.setattr(plugin, "_localized_now", lambda _device: current[0])
    monkeypatch.setattr(task, "_get_current_datetime", lambda: current[0])
    monkeypatch.setattr(refresh_task_module, "get_plugin_instance", lambda _config: plugin)

    recovery = [False]

    class ProviderResponse:
        status_code = 200
        text = "<p>A newly fetched sentence.</p><p> ~ [[Fixture Author]] ~ </p>"

        def raise_for_status(self):
            pass

        def json(self):
            return [{"word": "tranquil", "meanings": [{
                "partOfSpeech": "adjective",
                "definitions": [{"definition": "A recovered live definition."}],
            }]}]

    class OfflineSession:
        def get(self, url, **_kwargs):
            provider = "dictionary" if "dictionaryapi.dev" in url else "wikiquote"
            if not recovery[0] and failed_provider in (provider, "both"):
                raise ReadTimeout("fixture upstream unavailable")
            return ProviderResponse()

    monkeypatch.setattr(word_module, "get_http_session", OfflineSession)
    instance = playlist.plugins[0].snapshot()
    task.runtime_state.record_success(instance.instance_uuid, old_success, lane=RefreshLane.DATA)
    cache_path = Path(_write_runtime_cache(task, instance, Image.new("RGB", (800, 480), "blue")))
    with Image.open(cache_path) as cached:
        previous_pixels = cached.tobytes()

    for day in (30, 31):
        current[0] = datetime(2026, 8, day, 12, tzinfo=timezone.utc)
        refresh = task._playlist_command(
            playlist.name, instance, source=CommandSource.BACKGROUND,
            intent=RefreshIntent.DATA_REFRESH, display_cached_only=False,
            kind=CommandKind.CACHE_REFRESH, current_dt=current[0],
        )
        task._execute_command(refresh)
        with Image.open(cache_path) as cached:
            assert cached.size == (800, 480)
            current_pixels = cached.tobytes()
        assert current_pixels != previous_pixels, "outage must not freeze the old daily page"

        state = task.runtime_state.snapshot().instances[instance.instance_uuid]
        assert state.data.last_success_at == old_success
        assert state.data.last_failure_at == current[0].isoformat()
        assert state.data.next_retry_at is not None
        display = task._playlist_command(
            playlist.name, instance, source=CommandSource.SCHEDULER,
            intent=RefreshIntent.DISPLAY_CACHE, display_cached_only=True,
            kind=CommandKind.DISPLAY, current_dt=current[0],
        )
        task._execute_command(display)
        assert task.display_manager.calls[-1][0].tobytes() == current_pixels
        previous_pixels = current_pixels

    recovery[0] = True
    current[0] = datetime(2026, 8, 31, 12, 5, tzinfo=timezone.utc)
    refresh = task._playlist_command(
        playlist.name, instance, source=CommandSource.BACKGROUND,
        intent=RefreshIntent.DATA_REFRESH, display_cached_only=False,
        kind=CommandKind.CACHE_REFRESH, current_dt=current[0],
    )
    task._execute_command(refresh)
    state = task.runtime_state.snapshot().instances[instance.instance_uuid]
    assert state.data.last_success_at == current[0].isoformat()
    assert state.data.next_retry_at is None
    assert (tmp_path / "provider" / "daily.json").is_file()
    with Image.open(cache_path) as cached:
        recovered_pixels = cached.tobytes()
    assert recovered_pixels != previous_pixels
