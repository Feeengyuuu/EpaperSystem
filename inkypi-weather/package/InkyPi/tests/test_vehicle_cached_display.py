"""Exercise Vehicle's time-sensitive local view through the display worker."""

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.base_plugin.render_provenance import SourceProvenance, read_source_provenance
from plugins.plugin_manifest import PluginManifest
from plugins.vehicle_status import vehicle_status as vehicle_module
from runtime.refresh_contracts import CommandSource, JobStatus, RefreshIntent
from runtime.refresh_policy import ResourceSample
from runtime.runtime_state import RefreshLane
from refresh_task import RefreshTask
import refresh_task as refresh_task_module
from tests.test_refresh_task import (
    PresentationTransactionDisplayManager,
    RuntimeClock,
    RuntimeDeviceConfig,
    _queue_and_process,
    _runtime_playlist,
    _runtime_plugin_data,
    _write_runtime_cache,
    _write_runtime_theme_cache,
)
from tests.test_vehicle_status import _plugin, _summary


NOW = datetime(2026, 9, 3, 3, 0, tzinfo=timezone.utc)
OLD_PIXEL = (255, 0, 255)


class VehicleDisplayRecorder(PresentationTransactionDisplayManager):
    def display_image(self, image, *args, **kwargs):
        provenance = read_source_provenance(image)
        result = super().display_image(image, *args, **kwargs)
        self.calls[-1]["source_provenance"] = provenance
        return result


def _forbidden(*_args, **_kwargs):
    pytest.fail("cached display must not use a provider, credentials, or source writes")


def _vehicle_display(tmp_path, monkeypatch, *, source_age=72_000, redraw=True, theme="day", automatic=False):
    data = _runtime_plugin_data("vehicle_status", "Vehicle", latest_refresh_time=None)
    data["plugin_settings"].update({"language": "en", "cacheSeconds": 900, "themeMode": "auto" if automatic else theme})
    playlist = _runtime_playlist(data)
    device = RuntimeDeviceConfig(tmp_path / "display", [playlist])
    device.config.update({"theme_mode": theme, "active_theme": theme})
    device.get_resolution = lambda: (800, 480)
    device.load_env_key = _forbidden
    manifest = PluginManifest.from_path(
        Path(vehicle_module.__file__).with_name("plugin-info.json")
    )
    if not redraw:
        manifest = replace(
            manifest,
            capabilities=replace(manifest.capabilities, supports_cached_display_redraw=False),
        )
    plugin_config = {"id": "vehicle_status", "_manifest": manifest}
    device.get_plugin = lambda _plugin_id: plugin_config
    clock = RuntimeClock(wall=NOW.timestamp())
    display = VehicleDisplayRecorder()
    task = RefreshTask(device, display, clock=clock.monotonic, wall_clock=clock.wall_time)
    monkeypatch.setattr(task, "_get_current_datetime", lambda: datetime.fromtimestamp(clock.wall_time(), timezone.utc))
    monkeypatch.setattr(task, "_resource_sample", lambda: ResourceSample(available_mb=512, swap_percent=0))
    monkeypatch.setattr(vehicle_module.time, "time", clock.wall_time)
    instance = playlist.plugins[0].snapshot()
    formal_path = _write_runtime_theme_cache(
        task, instance, "day", Image.new("RGB", (800, 480), OLD_PIXEL)
    )
    plugin = _plugin(tmp_path / "source")
    plugin.config = plugin_config
    payload = _summary(freshness="stale_cache", connectivity="offline")
    payload["snapshot"]["captured_at"] = (NOW - timedelta(seconds=source_age)).isoformat()
    payload["snapshot"]["age_seconds"] = source_age - 300
    plugin._write_cache({"fetched_at": NOW.timestamp() - 300, "summary": payload})
    source_path = plugin._cache_file(create=False)
    task.runtime_state.record_attempt(instance.instance_uuid, (NOW - timedelta(minutes=2)).isoformat(), lane=RefreshLane.DATA)
    task.runtime_state.record_failure(
        instance.instance_uuid,
        (NOW - timedelta(minutes=1)).isoformat(),
        "DATA source is display-safe but unhealthy: stale_cache",
        lane=RefreshLane.DATA,
    )
    before = task.runtime_state.snapshot().instances[instance.instance_uuid]
    monkeypatch.setattr(refresh_task_module, "get_plugin_instance", lambda _config: plugin)
    monkeypatch.setattr(vehicle_module, "get_http_client", _forbidden)
    monkeypatch.setattr(plugin, "_write_cache_unlocked", _forbidden)
    monkeypatch.setattr(plugin, "_write_location_presentation", _forbidden)
    monkeypatch.setattr(refresh_task_module, "_plugin_live_refresh_state", _forbidden)
    return SimpleNamespace(
        task=task, device=device, playlist=playlist, instance=instance, clock=clock,
        plugin=plugin, display=display, before=before, source_path=source_path,
        source_bytes=source_path.read_bytes(), formal_path=formal_path,
        formal_bytes=formal_path.read_bytes(), theme=theme,
    )


def _display_command(case):
    return case.task._playlist_command(
        case.playlist.name, case.instance,
        source=CommandSource.SCHEDULER, intent=RefreshIntent.DISPLAY_CACHE,
        force=False, display_cached_only=True, cache_theme_mode=case.theme,
        current_dt=datetime.fromtimestamp(case.clock.wall_time(), timezone.utc),
    )


def _assert_data_and_caches_unchanged(case):
    after = case.task.runtime_state.snapshot().instances[case.instance.instance_uuid]
    assert after.data == case.before.data
    assert after.data.last_success_at is None
    assert after.last_good_cache == case.before.last_good_cache
    assert after.presentation_request == case.before.presentation_request
    assert after.presentation_receipt == case.before.presentation_receipt
    if case.source_bytes is None:
        assert not case.source_path.exists()
    else:
        assert case.source_path.read_bytes() == case.source_bytes
    if case.formal_bytes is None:
        assert not case.formal_path.exists()
    else:
        assert case.formal_path.read_bytes() == case.formal_bytes


def test_vehicle_display_uses_current_source_cache_and_advances_offline_age(tmp_path, monkeypatch):
    case = _vehicle_display(tmp_path, monkeypatch)
    seen = []
    render = case.plugin._render_summary

    def capture(summary, *args, **kwargs):
        seen.append(deepcopy(summary))
        return render(summary, *args, **kwargs)

    monkeypatch.setattr(case.plugin, "_render_summary", capture)
    for elapsed in (0, 3600):
        case.clock.advance(elapsed)
        result = _queue_and_process(case.task, _display_command(case))
        assert result.job.status is JobStatus.SUCCEEDED
        assert seen[-1]["snapshot"]["vehicle_connectivity"] == "offline"
        assert seen[-1]["snapshot"]["age_seconds"] == 72_000 + elapsed
        assert seen[-1]["snapshot"]["freshness"] == "stale_cache"
        image = case.display.calls[-1]["image"]
        assert image.getpixel((0, 0)) != OLD_PIXEL
        assert case.display.calls[-1]["source_provenance"] is SourceProvenance.STALE_CACHE
        _assert_data_and_caches_unchanged(case)
    assert len(case.display.calls) == 2


def test_vehicle_display_hides_expired_values_instead_of_old_formal_image(tmp_path, monkeypatch):
    case = _vehicle_display(tmp_path, monkeypatch, source_age=86_401)
    messages = []
    render_message = case.plugin._render_message

    def capture_message(dimensions, theme, title, message, language):
        messages.append((title, message))
        return render_message(dimensions, theme, title, message, language)

    monkeypatch.setattr(case.plugin, "_render_summary", _forbidden)
    monkeypatch.setattr(case.plugin, "_render_message", capture_message)
    result = _queue_and_process(case.task, _display_command(case))

    assert result.job.status is JobStatus.SUCCEEDED
    assert messages and messages[0][0] == vehicle_module._t("en", "status_old_title")
    assert len(case.display.calls) == 1
    image = case.display.calls[0]["image"]
    assert image.getpixel((0, 0)) != OLD_PIXEL
    assert case.display.calls[0]["source_provenance"] is SourceProvenance.LOCAL_FALLBACK
    _assert_data_and_caches_unchanged(case)


def test_cached_display_redraw_failure_does_not_write_old_vehicle_image(tmp_path, monkeypatch):
    case = _vehicle_display(tmp_path, monkeypatch)

    def broken(*_args, **_kwargs):
        raise RuntimeError("local summary renderer failed")

    monkeypatch.setattr(case.plugin, "_theme_only_image", broken)
    result = _queue_and_process(case.task, _display_command(case))

    assert result.job.status is not JobStatus.SUCCEEDED
    assert case.display.calls == []
    _assert_data_and_caches_unchanged(case)


def test_hard_pressure_defers_vehicle_redraw_without_plugin_or_write(tmp_path, monkeypatch):
    case = _vehicle_display(tmp_path, monkeypatch)
    monkeypatch.setattr(case.task, "_resource_sample", lambda: ResourceSample(available_mb=60, swap_percent=80))
    monkeypatch.setattr(refresh_task_module, "get_plugin_instance", _forbidden)

    result = _queue_and_process(case.task, _display_command(case))

    assert result.job.status is JobStatus.CANCELED
    assert result.job.error_code == "cache_unavailable"
    assert case.display.calls == []
    _assert_data_and_caches_unchanged(case)


def test_display_cache_without_redraw_capability_never_instantiates_plugin(tmp_path, monkeypatch):
    case = _vehicle_display(tmp_path, monkeypatch, redraw=False)
    monkeypatch.setattr(refresh_task_module, "get_plugin_instance", _forbidden)

    result = _queue_and_process(case.task, _display_command(case))

    assert result.job.status is JobStatus.SUCCEEDED
    assert len(case.display.calls) == 1
    assert case.display.calls[0]["image"].getpixel((0, 0)) == OLD_PIXEL
    _assert_data_and_caches_unchanged(case)


@pytest.mark.parametrize("theme,missing_source", [("day", False), ("night", False), ("night", True)])
@pytest.mark.parametrize("automatic", [False, True])
def test_vehicle_rotation_without_formal_png_uses_local_view_or_warning(
    tmp_path, monkeypatch, theme, missing_source, automatic,
):
    case = _vehicle_display(tmp_path, monkeypatch, theme=theme, automatic=automatic)
    case.formal_path.unlink()
    case.formal_bytes = None
    if missing_source:
        case.source_path.unlink()
        case.source_bytes = None
        monkeypatch.setattr(case.plugin, "_render_summary", _forbidden)
    case.device.refresh_info.refresh_time = (NOW - timedelta(minutes=10)).isoformat()
    case.device.config["display_triggered_refresh_enabled"] = False
    current = case.device.get_playlist_manager().snapshot_active_playlist(NOW)
    assert case.task._active_cache_candidates(current, None, exact_theme_only=True) == {}

    candidates = case.task._rotation_display_candidates(current, None, exact_theme_only=True)
    assert case.instance.instance_uuid in candidates
    assert candidates[case.instance.instance_uuid].theme_mode == theme
    command = case.task._select_cached_display_command(NOW)
    assert command is not None
    assert command.intent is RefreshIntent.DISPLAY_CACHE
    assert command.instance_uuid == case.instance.instance_uuid
    result = _queue_and_process(case.task, command)

    assert result.job.status is JobStatus.SUCCEEDED
    assert len(case.display.calls) == 1
    assert case.display.calls[0]["image"].getpixel((0, 0)) != OLD_PIXEL
    assert case.display.calls[0]["source_provenance"] is (
        SourceProvenance.LOCAL_FALLBACK if missing_source else SourceProvenance.STALE_CACHE
    )
    _assert_data_and_caches_unchanged(case)


def test_vehicle_display_failure_does_not_modify_data_failure_or_promote_cache(tmp_path, monkeypatch):
    case = _vehicle_display(tmp_path, monkeypatch)

    def display_failure(*_args, **_kwargs):
        raise RuntimeError("display hardware failed")

    monkeypatch.setattr(case.display, "display_image", display_failure)
    result = _queue_and_process(case.task, _display_command(case))

    assert result.job.status is not JobStatus.SUCCEEDED
    assert case.display.calls == []
    _assert_data_and_caches_unchanged(case)


def test_hard_pressure_releases_vehicle_reservation_and_rotates_other_cache(tmp_path, monkeypatch):
    case = _vehicle_display(tmp_path, monkeypatch)
    case.playlist.add_plugin(_runtime_plugin_data("plain_image", "Plain Image"))
    other = case.playlist.plugins[-1].snapshot()
    vehicle_config = case.device.get_plugin("vehicle_status")
    case.device.get_plugin = lambda plugin_id: (
        vehicle_config if plugin_id == "vehicle_status" else {"id": plugin_id}
    )
    _write_runtime_cache(case.task, other, Image.new("RGB", (800, 480), "blue"))
    members = [case.instance.instance_uuid, other.instance_uuid]
    case.playlist.plugin_rotation_pool = members[:]
    case.playlist.plugin_rotation_queue = members[:]
    case.playlist.plugin_rotation_recent_history = []
    case.playlist._plugin_rotation_reserved_key = case.instance.instance_uuid
    case.device.refresh_info.refresh_time = (NOW - timedelta(minutes=10)).isoformat()
    case.device.config["display_triggered_refresh_enabled"] = False
    monkeypatch.setattr(case.task, "_resource_sample", lambda: ResourceSample(available_mb=60, swap_percent=80))
    monkeypatch.setattr(refresh_task_module, "get_plugin_instance", _forbidden)
    command = case.task._playlist_command(
        case.playlist.name, case.instance,
        source=CommandSource.SCHEDULER, intent=RefreshIntent.DISPLAY_CACHE,
        force=False, display_cached_only=True, cache_theme_mode="day",
        current_dt=NOW, automatic_rotation=True, force_hardware_write=True,
    )

    blocked = _queue_and_process(case.task, command)

    assert blocked.job.status is JobStatus.CANCELED
    assert case.display.calls == []
    assert not case.playlist.is_rotation_reservation_current(case.instance.instance_uuid)
    retry = case.task._rotation_display_retry_key(case.instance.instance_uuid)
    assert case.task.retry_registry.next_delay(retry, case.clock.monotonic()) > 0
    next_command = case.task._select_cached_display_command(NOW)
    assert next_command is not None
    assert next_command.instance_uuid == other.instance_uuid
    assert next_command.intent is RefreshIntent.DISPLAY_CACHE
    displayed = _queue_and_process(case.task, next_command)
    assert displayed.job.status is JobStatus.SUCCEEDED
    assert case.display.calls[-1]["image"].getpixel((0, 0)) == (0, 0, 255)
    _assert_data_and_caches_unchanged(case)


@pytest.mark.parametrize("interruption", ["settings_revision", "cancellation"])
def test_vehicle_redraw_interrupted_during_render_never_commits(tmp_path, monkeypatch, interruption):
    case = _vehicle_display(tmp_path, monkeypatch)
    render = case.plugin._theme_only_image
    rendered = []

    def interrupt_after_render(*args, **kwargs):
        image = render(*args, **kwargs)
        rendered.append(True)
        if interruption == "settings_revision":
            settings = dict(case.instance.settings)
            settings["language"] = "zh"
            updated = case.device.get_playlist_manager().update_plugin_instance(
                case.instance.instance_uuid,
                settings=settings,
                expected_generation=case.instance.structural_generation,
                expected_settings_revision=case.instance.settings_revision,
            )
            assert updated.settings_revision == case.instance.settings_revision + 1
        else:
            assert case.task.refresh_queue.cancel_instance(case.instance.instance_uuid) == 1
        return image

    monkeypatch.setattr(case.plugin, "_theme_only_image", interrupt_after_render)
    result = _queue_and_process(case.task, _display_command(case))

    assert rendered == [True]
    assert result.job.status is JobStatus.CANCELED
    assert result.job.error_code == (
        "stale_selection" if interruption == "settings_revision" else "task_canceled"
    )
    assert case.display.calls == []
    _assert_data_and_caches_unchanged(case)


def test_vehicle_redraw_theme_change_during_render_never_commits(tmp_path, monkeypatch):
    case = _vehicle_display(tmp_path, monkeypatch, theme="auto")
    case.theme = "day"
    case.device.config.update({"theme_mode": "day", "active_theme": "day"})
    render = case.plugin._theme_only_image
    rendered = []

    def change_theme_after_render(*args, **kwargs):
        image = render(*args, **kwargs)
        rendered.append(True)
        case.device.config["theme_mode"] = "night"
        return image

    monkeypatch.setattr(case.plugin, "_theme_only_image", change_theme_after_render)
    result = _queue_and_process(case.task, _display_command(case))

    assert rendered == [True]
    assert result.job.status is JobStatus.CANCELED
    assert result.job.error_code == "stale_selection"
    assert case.display.calls == []
    _assert_data_and_caches_unchanged(case)
