import json
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
import threading
import time
import uuid

import pytest
from PIL import Image

from plugins.base_plugin.base_plugin import BasePlugin
from plugins.newspaper.newspaper import Newspaper
from plugins.plugin_settings import PluginSettingError
from src.model import Playlist, PlaylistManager, RefreshInfo
from src.plugins.plugin_manifest import PluginCapabilities, PluginManifest
from src.refresh_task import ManualRefresh, PlaylistRefresh, RefreshTask
from runtime.refresh_contracts import (
    CommandKind,
    CommandSource,
    JobStatus,
    LifecycleState,
    RefreshCommand,
    TaskCancelled,
    TaskContext,
)
from runtime.refresh_queue import QueueFullError, QueueStoppingError, RefreshQueue
from runtime.render_arbiter import RenderArbiter
from runtime.scheduler_state import LifecycleController, RetryRegistry, SchedulerState


TEST_STATE_ROOT = Path(__file__).resolve().parents[4] / ".tmp" / "refresh_task_tests"
PLUGIN_SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src" / "plugins"


def _settings_default_refresh_on_display_plugin_ids():
    plugin_ids = set()
    for settings_path in PLUGIN_SOURCE_ROOT.glob("*/settings.html"):
        text = settings_path.read_text(encoding="utf-8")
        if "refreshOnDisplay" not in text:
            continue
        if (
            'value="true"' in text
            or "value='true'" in text
            or 'refreshOnDisplay: "true"' in text
            or "refreshOnDisplay: 'true'" in text
            or ".checked = true" in text
            or "!== 'false'" in text
            or '!== "false"' in text
        ):
            plugin_ids.add(settings_path.parent.name)
    return plugin_ids


def _refresh_on_display_plugin_info_ids():
    plugin_ids = set()
    for info_path in PLUGIN_SOURCE_ROOT.glob("*/plugin-info.json"):
        data = json.loads(info_path.read_text(encoding="utf-8"))
        if data.get("refresh_on_display"):
            plugin_ids.add(info_path.parent.name)
    return plugin_ids


def test_refresh_on_display_settings_defaults_have_runtime_fallback():
    expected_plugin_ids = _settings_default_refresh_on_display_plugin_ids()

    assert expected_plugin_ids <= _refresh_on_display_plugin_info_ids()


def make_test_dir(name):
    path = TEST_STATE_ROOT / f"{name}-{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    return path


class FakeDeviceConfig:
    def __init__(self, plugin_image_dir):
        self.plugin_image_dir = str(plugin_image_dir)
        self.write_count = 0
        self.config = {}

    def get_plugin(self, plugin_id):
        return {"id": plugin_id}

    def get_config(self, key=None, default=None):
        if key is None:
            return self.config
        return self.config.get(key, default)

    def update_value(self, key, value, write=False):
        self.config[key] = value
        if write:
            self.write_config()

    def write_config(self):
        self.write_count += 1


class FakePlugin:
    REFRESH_ON_DISPLAY_IDS = {
        "backtothedate",
        "live_radar",
        "riot-page",
        "simple-calendar",
        "steam-art",
    }

    def __init__(self, calls, refresh_on_display=False, live_state=None):
        self.calls = calls
        self.refresh_on_display = refresh_on_display
        self.live_state = live_state

    def wants_refresh_on_display(self, settings):
        if callable(self.refresh_on_display):
            return bool(self.refresh_on_display(settings or {}))
        if self.refresh_on_display:
            return True
        settings = settings or {}
        if str(settings.get("mediaRotationMode") or "").lower() == "rotate":
            return True
        return settings.get("id") in self.REFRESH_ON_DISPLAY_IDS

    def get_live_refresh_state(self, settings, current_dt):
        if callable(self.live_state):
            return self.live_state(settings or {}, current_dt)
        return self.live_state

    def generate_image(self, settings, device_config):
        self.calls.append(settings["id"])
        return Image.new("RGB", (1, 1), "white")


class CapturePlugin:
    def __init__(self, calls):
        self.calls = calls
        self.config = {}

    def generate_image(self, settings, device_config):
        self.calls.append(dict(settings))
        return Image.new("RGB", (1, 1), "white")


class ThreadedDeviceConfig(FakeDeviceConfig):
    def __init__(self, plugin_image_dir, playlist):
        super().__init__(plugin_image_dir)
        self.playlist_manager = PlaylistManager([playlist])
        self.refresh_info = RefreshInfo(
            refresh_type="Playlist",
            plugin_id="old",
            playlist="DailyDoseOfDay",
            plugin_instance="Old",
            refresh_time="2000-01-01T00:00:00+00:00",
            image_hash="old",
        )

    def get_playlist_manager(self):
        return self.playlist_manager

    def get_refresh_info(self):
        return self.refresh_info


class BlockingDisplayManager:
    def __init__(self):
        self.first_display_started = threading.Event()
        self.release_first_display = threading.Event()
        self.display_count = 0

    def display_image(self, image, image_settings=None):
        self.display_count += 1
        if self.display_count == 1:
            self.first_display_started.set()
            self.release_first_display.wait(timeout=1)


def test_refresh_due_plugin_instances_updates_due_cache_only(monkeypatch):
    calls = []
    tmp_path = make_test_dir("due-cache")
    device_config = FakeDeviceConfig(tmp_path)
    task = RefreshTask(device_config, display_manager=None)
    playlist = Playlist(
        "DailyDoseOfDay",
        "00:00",
        "24:00",
        plugins=[
            {
                "plugin_id": "due",
                "name": "Due Plugin",
                "plugin_settings": {"id": "due"},
                "refresh": {"interval": 300},
                "latest_refresh_time": "2026-05-26T07:00:00+00:00",
            },
            {
                "plugin_id": "fresh",
                "name": "Fresh Plugin",
                "plugin_settings": {"id": "fresh"},
                "refresh": {"interval": 300},
                "latest_refresh_time": "2026-05-26T07:04:00+00:00",
            },
        ],
    )

    fresh_path = tmp_path / "fresh_Fresh_Plugin.png"
    Image.new("RGB", (1, 1), "black").save(fresh_path)

    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda config: FakePlugin(calls),
    )

    task._refresh_due_plugin_instances(
        playlist,
        datetime(2026, 5, 26, 7, 5, tzinfo=timezone.utc),
    )

    assert calls == ["due"]
    assert (tmp_path / "due_Due_Plugin.png").exists()
    assert playlist.find_plugin("due", "Due Plugin").latest_refresh_time == "2026-05-26T07:05:00+00:00"
    assert playlist.find_plugin("fresh", "Fresh Plugin").latest_refresh_time == "2026-05-26T07:04:00+00:00"
    assert device_config.write_count == 1


def test_refresh_due_plugin_instances_prefers_oldest_due_cache_when_limited(monkeypatch):
    calls = []
    tmp_path = make_test_dir("oldest-due-cache")
    device_config = FakeDeviceConfig(tmp_path)
    task = RefreshTask(device_config, display_manager=None)
    playlist = Playlist(
        "DailyDoseOfDay",
        "00:00",
        "24:00",
        plugins=[
            {
                "plugin_id": "live_radar",
                "name": "LiveRadar",
                "plugin_settings": {"id": "live_radar"},
                "refresh": {"interval": 60},
                "latest_refresh_time": "2026-05-26T07:04:00+00:00",
            },
            {
                "plugin_id": "steam_charts",
                "name": "Steam Charts",
                "plugin_settings": {"id": "steam_charts"},
                "refresh": {"interval": 21600},
                "latest_refresh_time": "2026-05-25T07:00:00+00:00",
            },
        ],
    )
    for plugin_instance in playlist.plugins:
        Image.new("RGB", (1, 1), "black").save(tmp_path / plugin_instance.get_image_path())

    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda config: FakePlugin(calls),
    )

    task._refresh_due_plugin_instances(
        playlist,
        datetime(2026, 5, 26, 7, 5, tzinfo=timezone.utc),
        max_updates=1,
    )

    assert calls == ["steam_charts"]
    assert playlist.find_plugin("steam_charts", "Steam Charts").latest_refresh_time == "2026-05-26T07:05:00+00:00"
    assert playlist.find_plugin("live_radar", "LiveRadar").latest_refresh_time == "2026-05-26T07:04:00+00:00"
    assert device_config.write_count == 1


def test_playlist_cache_refresh_due_detects_stale_long_interval_plugin(monkeypatch):
    tmp_path = make_test_dir("playlist-cache-due")
    device_config = FakeDeviceConfig(tmp_path)
    task = RefreshTask(device_config, display_manager=None)
    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda config: FakePlugin([], refresh_on_display=config["id"] == "live_radar"),
    )
    current_dt = datetime(2026, 5, 26, 7, 5, tzinfo=timezone.utc)
    playlist = Playlist(
        "DailyDoseOfDay",
        "00:00",
        "24:00",
        plugins=[
            {
                "plugin_id": "live_radar",
                "name": "LiveRadar",
                "plugin_settings": {"id": "live_radar"},
                "refresh": {"interval": 999999999},
                "latest_refresh_time": current_dt.isoformat(),
            },
            {
                "plugin_id": "steam_charts",
                "name": "Steam Charts",
                "plugin_settings": {"id": "steam_charts"},
                "refresh": {"interval": 21600},
                "latest_refresh_time": "2026-05-25T07:00:00+00:00",
            },
        ],
    )
    for plugin_instance in playlist.plugins:
        Image.new("RGB", (1, 1), "black").save(tmp_path / plugin_instance.get_image_path())

    live_radar = playlist.find_plugin("live_radar", "LiveRadar")
    steam_charts = playlist.find_plugin("steam_charts", "Steam Charts")

    assert task._plugin_instance_cache_refresh_due(live_radar, current_dt) is False
    assert task._plugin_instance_cache_refresh_due(
        live_radar,
        current_dt,
        displayed_plugin_instance=live_radar,
    ) is True
    assert task._playlist_has_cache_refresh_due(playlist, current_dt) is True

    steam_charts.latest_refresh_time = current_dt.isoformat()
    assert task._playlist_has_cache_refresh_due(playlist, current_dt) is False


def test_refresh_due_plugin_instances_refreshes_missing_image(monkeypatch):
    calls = []
    tmp_path = make_test_dir("missing-cache")
    device_config = FakeDeviceConfig(tmp_path)
    task = RefreshTask(device_config, display_manager=None)
    playlist = Playlist(
        "DailyDoseOfDay",
        "00:00",
        "24:00",
        plugins=[
            {
                "plugin_id": "missing",
                "name": "Missing Plugin",
                "plugin_settings": {"id": "missing"},
                "refresh": {"interval": 3600},
                "latest_refresh_time": "2026-05-26T07:04:00+00:00",
            },
        ],
    )

    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda config: FakePlugin(calls),
    )

    task._refresh_due_plugin_instances(
        playlist,
        datetime(2026, 5, 26, 7, 5, tzinfo=timezone.utc),
    )

    assert calls == ["missing"]
    assert (tmp_path / "missing_Missing_Plugin.png").exists()
    assert playlist.find_plugin("missing", "Missing Plugin").latest_refresh_time == "2026-05-26T07:05:00+00:00"
    assert device_config.write_count == 1


def test_refresh_due_plugin_instances_updates_live_hook_cache_early(monkeypatch):
    calls = []
    tmp_path = make_test_dir("live-hook-cache")
    device_config = FakeDeviceConfig(tmp_path)
    task = RefreshTask(device_config, display_manager=None)
    live_plugin = FakePlugin(calls, live_state={"active": True, "interval_seconds": 180})
    playlist = Playlist(
        "DailyDoseOfDay",
        "00:00",
        "24:00",
        plugins=[
            {
                "plugin_id": "live_radar",
                "name": "LiveRadar",
                "plugin_settings": {"id": "live_radar"},
                "refresh": {"interval": 60},
                "latest_refresh_time": "2026-05-26T07:00:00+00:00",
            },
            {
                "plugin_id": "live_plugin",
                "name": "LivePlugin",
                "plugin_settings": {"id": "live"},
                "refresh": {"interval": 3600},
                "latest_refresh_time": "2026-05-26T07:00:00+00:00",
            },
        ],
    )
    plugin_instance = playlist.find_plugin("live_plugin", "LivePlugin")
    other_plugin = playlist.find_plugin("live_radar", "LiveRadar")
    Image.new("RGB", (1, 1), "black").save(tmp_path / other_plugin.get_image_path())
    Image.new("RGB", (1, 1), "black").save(tmp_path / plugin_instance.get_image_path())

    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda config: live_plugin if config["id"] == "live_plugin" else FakePlugin(calls),
    )

    task._refresh_due_plugin_instances(
        playlist,
        datetime(2026, 5, 26, 7, 4, tzinfo=timezone.utc),
        only_plugin_id="live_plugin",
    )

    assert calls == ["live"]
    assert other_plugin.latest_refresh_time == "2026-05-26T07:00:00+00:00"
    assert plugin_instance.latest_refresh_time == "2026-05-26T07:04:00+00:00"
    assert device_config.write_count == 1


def test_refresh_due_plugin_instances_skips_sports_dashboard_live_background_by_default(monkeypatch):
    calls = []
    tmp_path = make_test_dir("sports-dashboard-live-background-skip")
    device_config = FakeDeviceConfig(tmp_path)
    task = RefreshTask(device_config, display_manager=None)
    live_plugin = FakePlugin(calls, live_state={"active": True, "interval_seconds": 60})
    playlist = Playlist(
        "DailyDoseOfDay",
        "00:00",
        "24:00",
        plugins=[
            {
                "plugin_id": "sports_dashboard",
                "name": "SportsDashboard",
                "plugin_settings": {"id": "sports_dashboard"},
                "refresh": {"interval": 60},
                "latest_refresh_time": "2026-05-26T07:00:00+00:00",
            },
        ],
    )
    monkeypatch.setattr("src.refresh_task.get_plugin_instance", lambda config: live_plugin)

    task._refresh_due_plugin_instances(
        playlist,
        datetime(2026, 5, 26, 7, 5, tzinfo=timezone.utc),
        only_plugin_id="sports_dashboard",
    )

    assert calls == []
    assert playlist.find_plugin("sports_dashboard", "SportsDashboard").latest_refresh_time == "2026-05-26T07:00:00+00:00"
    assert device_config.write_count == 0


def test_refresh_due_plugin_instances_skips_sports_dashboard_background_without_live_state(monkeypatch):
    calls = []
    tmp_path = make_test_dir("sports-dashboard-display-only-background")
    device_config = FakeDeviceConfig(tmp_path)
    task = RefreshTask(device_config, display_manager=None)
    plugin = FakePlugin(calls, live_state=None)
    playlist = Playlist(
        "DailyDoseOfDay",
        "00:00",
        "24:00",
        plugins=[
            {
                "plugin_id": "sports_dashboard",
                "name": "SportsDashboard",
                "plugin_settings": {"id": "sports_dashboard"},
                "refresh": {"interval": 60},
                "latest_refresh_time": "2026-05-26T07:00:00+00:00",
            },
        ],
    )
    monkeypatch.setattr("src.refresh_task.get_plugin_instance", lambda config: plugin)

    task._refresh_due_plugin_instances(
        playlist,
        datetime(2026, 5, 26, 7, 5, tzinfo=timezone.utc),
        only_plugin_id="sports_dashboard",
    )

    assert calls == []
    assert playlist.find_plugin("sports_dashboard", "SportsDashboard").latest_refresh_time == "2026-05-26T07:00:00+00:00"
    assert device_config.write_count == 0


def test_refresh_due_plugin_instances_allows_sports_dashboard_background_when_enabled(monkeypatch):
    calls = []
    tmp_path = make_test_dir("sports-dashboard-background-enabled")
    device_config = FakeDeviceConfig(tmp_path)
    task = RefreshTask(device_config, display_manager=None)
    live_plugin = FakePlugin(calls, live_state={"active": True, "interval_seconds": 60})
    playlist = Playlist(
        "DailyDoseOfDay",
        "00:00",
        "24:00",
        plugins=[
            {
                "plugin_id": "sports_dashboard",
                "name": "SportsDashboard",
                "plugin_settings": {"id": "sports_dashboard", "backgroundCacheRefreshEnabled": "true"},
                "refresh": {"interval": 60},
                "latest_refresh_time": "2026-05-26T07:00:00+00:00",
            },
        ],
    )
    monkeypatch.setattr("src.refresh_task.get_plugin_instance", lambda config: live_plugin)

    task._refresh_due_plugin_instances(
        playlist,
        datetime(2026, 5, 26, 7, 5, tzinfo=timezone.utc),
        only_plugin_id="sports_dashboard",
    )

    assert calls == ["sports_dashboard"]
    assert playlist.find_plugin("sports_dashboard", "SportsDashboard").latest_refresh_time == "2026-05-26T07:05:00+00:00"
    assert device_config.write_count == 1


def test_background_cache_refresh_does_not_target_only_display_only_live_plugin(monkeypatch):
    tmp_path = make_test_dir("background-cache-display-only-live")
    device_config = FakeDeviceConfig(tmp_path)
    task = RefreshTask(device_config, display_manager=None)
    playlist = Playlist(
        "DailyDoseOfDay",
        "00:00",
        "24:00",
        plugins=[
            {
                "plugin_id": "sports_dashboard",
                "name": "SportsDashboard",
                "plugin_settings": {"id": "sports_dashboard"},
                "refresh": {"interval": 3600},
                "latest_refresh_time": "2026-05-26T07:00:00+00:00",
            },
            {
                "plugin_id": "tech_pulse",
                "name": "TechPulse",
                "plugin_settings": {"id": "tech_pulse"},
                "refresh": {"interval": 60},
                "latest_refresh_time": "2026-05-26T07:00:00+00:00",
            },
            {
                "plugin_id": "simple-calendar",
                "name": "Calendar",
                "plugin_settings": {"id": "calendar"},
                "refresh": {"interval": 3600},
                "latest_refresh_time": "2026-05-26T07:05:00+00:00",
            },
        ],
    )
    for plugin_instance in playlist.plugins:
        Image.new("RGB", (1, 1), "black").save(tmp_path / plugin_instance.get_image_path())
    plugins = {
        "sports_dashboard": FakePlugin([], live_state={"active": True, "interval_seconds": 60}),
        "tech_pulse": FakePlugin([]),
        "simple-calendar": FakePlugin([]),
    }
    monkeypatch.setattr("src.refresh_task.get_plugin_instance", lambda config: plugins[config["id"]])
    captured = []

    def capture_start(*args, **kwargs):
        captured.append({"args": args, "kwargs": kwargs})

    monkeypatch.setattr(task, "_start_due_plugin_cache_refresh", capture_start)

    task._maybe_start_background_cache_refresh(
        playlist,
        playlist.find_plugin("simple-calendar", "Calendar"),
        datetime(2026, 5, 26, 7, 5, tzinfo=timezone.utc),
    )

    assert len(captured) == 1
    assert captured[0]["kwargs"]["only_plugin_id"] is None


def test_background_cache_refresh_skips_when_only_display_only_live_plugin_is_due(monkeypatch):
    tmp_path = make_test_dir("background-cache-only-display-only-live")
    device_config = FakeDeviceConfig(tmp_path)
    task = RefreshTask(device_config, display_manager=None)
    playlist = Playlist(
        "DailyDoseOfDay",
        "00:00",
        "24:00",
        plugins=[
            {
                "plugin_id": "sports_dashboard",
                "name": "SportsDashboard",
                "plugin_settings": {"id": "sports_dashboard"},
                "refresh": {"interval": 3600},
                "latest_refresh_time": "2026-05-26T07:00:00+00:00",
            },
            {
                "plugin_id": "simple-calendar",
                "name": "Calendar",
                "plugin_settings": {"id": "calendar"},
                "refresh": {"interval": 3600},
                "latest_refresh_time": "2026-05-26T07:05:00+00:00",
            },
        ],
    )
    for plugin_instance in playlist.plugins:
        Image.new("RGB", (1, 1), "black").save(tmp_path / plugin_instance.get_image_path())
    plugins = {
        "sports_dashboard": FakePlugin([], live_state={"active": True, "interval_seconds": 60}),
        "simple-calendar": FakePlugin([]),
    }
    monkeypatch.setattr("src.refresh_task.get_plugin_instance", lambda config: plugins[config["id"]])
    captured = []
    monkeypatch.setattr(task, "_start_due_plugin_cache_refresh", lambda *args, **kwargs: captured.append(kwargs))

    task._maybe_start_background_cache_refresh(
        playlist,
        playlist.find_plugin("simple-calendar", "Calendar"),
        datetime(2026, 5, 26, 7, 5, tzinfo=timezone.utc),
    )

    assert captured == []


def test_refresh_due_plugin_instances_stops_before_generation_under_resource_pressure(monkeypatch):
    calls = []
    tmp_path = make_test_dir("background-cache-pressure-before-generation")
    device_config = FakeDeviceConfig(tmp_path)
    task = RefreshTask(device_config, display_manager=None)
    plugin = FakePlugin(calls)
    playlist = Playlist(
        "DailyDoseOfDay",
        "00:00",
        "24:00",
        plugins=[
            {
                "plugin_id": "tech_pulse",
                "name": "TechPulse",
                "plugin_settings": {"id": "tech_pulse"},
                "refresh": {"interval": 60},
                "latest_refresh_time": "2026-05-26T07:00:00+00:00",
            },
        ],
    )
    Image.new("RGB", (1, 1), "black").save(tmp_path / "tech_pulse_TechPulse.png")
    monkeypatch.setattr("src.refresh_task.get_plugin_instance", lambda config: plugin)
    monkeypatch.setattr(task, "_cache_refresh_under_resource_pressure", lambda: True)

    task._refresh_due_plugin_instances(
        playlist,
        datetime(2026, 5, 26, 7, 5, tzinfo=timezone.utc),
    )

    assert calls == []
    assert playlist.find_plugin("tech_pulse", "TechPulse").latest_refresh_time == "2026-05-26T07:00:00+00:00"
    assert device_config.write_count == 0


def test_live_refresh_wait_seconds_uses_plugin_hook(monkeypatch):
    tmp_path = make_test_dir("live-hook-wait")
    live_plugin = FakePlugin([], live_state={"active": True, "interval_seconds": 180})
    playlist = Playlist(
        "DailyDoseOfDay",
        "00:00",
        "24:00",
        plugins=[
            {
                "plugin_id": "live_plugin",
                "name": "LivePlugin",
                "plugin_settings": {"id": "live"},
                "refresh": {"interval": 3600},
                "latest_refresh_time": "2026-05-26T07:00:00+00:00",
            },
        ],
    )
    device_config = ThreadedDeviceConfig(tmp_path, playlist)
    task = RefreshTask(device_config, display_manager=None)
    monkeypatch.setattr("src.refresh_task.get_plugin_instance", lambda config: live_plugin)

    wait_seconds = task._live_refresh_wait_seconds(
        datetime(2026, 5, 26, 7, 2, tzinfo=timezone.utc)
    )

    assert wait_seconds == 60


def test_live_refresh_wait_seconds_is_due_without_prior_refresh(monkeypatch):
    tmp_path = make_test_dir("live-hook-no-prior-refresh")
    live_plugin = FakePlugin([], live_state={"active": True, "interval_seconds": 180})
    playlist = Playlist(
        "DailyDoseOfDay",
        "00:00",
        "24:00",
        plugins=[
            {
                "plugin_id": "live_plugin",
                "name": "LivePlugin",
                "plugin_settings": {"id": "live"},
                "refresh": {"interval": 3600},
            },
        ],
    )
    device_config = ThreadedDeviceConfig(tmp_path, playlist)
    task = RefreshTask(device_config, display_manager=None)
    monkeypatch.setattr("src.refresh_task.get_plugin_instance", lambda config: live_plugin)

    wait_seconds = task._live_refresh_wait_seconds(
        datetime(2026, 5, 26, 7, 2, tzinfo=timezone.utc)
    )

    assert wait_seconds == 0


def test_live_refresh_is_not_due_without_active_hook(monkeypatch):
    tmp_path = make_test_dir("live-hook-inactive")
    live_plugin = FakePlugin([], live_state=None)
    playlist = Playlist(
        "DailyDoseOfDay",
        "00:00",
        "24:00",
        plugins=[
            {
                "plugin_id": "live_plugin",
                "name": "LivePlugin",
                "plugin_settings": {"id": "live"},
                "refresh": {"interval": 3600},
                "latest_refresh_time": "2026-05-26T07:00:00+00:00",
            },
        ],
    )
    device_config = ThreadedDeviceConfig(tmp_path, playlist)
    task = RefreshTask(device_config, display_manager=None)
    monkeypatch.setattr("src.refresh_task.get_plugin_instance", lambda config: live_plugin)
    plugin_instance = playlist.find_plugin("live_plugin", "LivePlugin")

    live_due = task._plugin_live_refresh_due(
        plugin_instance,
        datetime(2026, 5, 26, 7, 10, tzinfo=timezone.utc),
    )
    wait_seconds = task._live_refresh_wait_seconds(
        datetime(2026, 5, 26, 7, 10, tzinfo=timezone.utc)
    )

    assert live_due is False
    assert wait_seconds is None


def test_live_refresh_scan_skips_plugin_without_manifest_capability(monkeypatch):
    tmp_path = make_test_dir("manifest-live-scan-lazy")
    playlist = Playlist(
        "DailyDoseOfDay",
        "00:00",
        "24:00",
        plugins=[
            {
                "plugin_id": "ordinary_plugin",
                "name": "Ordinary Plugin",
                "plugin_settings": {"id": "ordinary"},
                "refresh": {"interval": 3600},
                "latest_refresh_time": "2026-05-26T07:00:00+00:00",
            },
        ],
    )
    device_config = ThreadedDeviceConfig(tmp_path, playlist)
    manifest = PluginManifest(
        schema_version=2,
        id="ordinary_plugin",
        class_name="OrdinaryPlugin",
        display_name="Ordinary Plugin",
        refresh_on_display=False,
        capabilities=PluginCapabilities(supports_live_refresh=False),
        raw={},
    )
    device_config.get_plugin = lambda plugin_id: {
        "id": plugin_id,
        "_manifest": manifest,
    }
    loaded = []
    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda config: loaded.append(config) or FakePlugin([], live_state=None),
    )
    task = RefreshTask(device_config, display_manager=None)

    wait_seconds = task._live_refresh_wait_seconds(
        datetime(2026, 5, 26, 7, 2, tzinfo=timezone.utc)
    )
    snapshot_due = task._snapshot_live_refresh_due(
        playlist.plugins[0].snapshot(),
        datetime(2026, 5, 26, 7, 2, tzinfo=timezone.utc),
    )

    assert wait_seconds is None
    assert snapshot_due is False
    assert loaded == []


def test_playlist_refresh_uses_cached_image_without_generating_for_scheduled_display():
    calls = []
    tmp_path = make_test_dir("scheduled-cache")
    device_config = FakeDeviceConfig(tmp_path)
    playlist = Playlist(
        "DailyDoseOfDay",
        "00:00",
        "24:00",
        plugins=[
            {
                "plugin_id": "slow",
                "name": "Slow Plugin",
                "plugin_settings": {"id": "slow"},
                "refresh": {"interval": 300},
                "latest_refresh_time": "2026-05-26T07:00:00+00:00",
            },
        ],
    )
    plugin_instance = playlist.find_plugin("slow", "Slow Plugin")
    Image.new("RGB", (2, 1), "black").save(tmp_path / "slow_Slow_Plugin.png")

    image = PlaylistRefresh(playlist, plugin_instance, display_cached_only=True).execute(
        FakePlugin(calls),
        device_config,
        datetime(2026, 5, 26, 7, 5, tzinfo=timezone.utc),
    )

    assert calls == []
    assert image.size == (2, 1)
    assert image.getpixel((0, 0)) == (0, 0, 0)
    assert plugin_instance.latest_refresh_time == "2026-05-26T07:00:00+00:00"


def test_playlist_refresh_instance_false_overrides_manifest_refresh_on_display():
    calls = []
    tmp_path = make_test_dir("instance-refresh-on-display-false")
    device_config = FakeDeviceConfig(tmp_path)
    device_config.config["display_refresh_resource_guard_enabled"] = False
    playlist = Playlist(
        "DailyDoseOfDay",
        "00:00",
        "24:00",
        plugins=[
            {
                "plugin_id": "base_plugin",
                "name": "ManifestDefaultTrue",
                "plugin_settings": {
                    "id": "base-instance",
                    "refreshOnDisplay": False,
                },
                "refresh": {"interval": 300},
                "latest_refresh_time": "2999-01-01T00:00:00+00:00",
            },
        ],
    )
    plugin_instance = playlist.plugins[0]
    Image.new("RGB", (2, 1), "black").save(
        tmp_path / plugin_instance.get_image_path()
    )
    plugin = BasePlugin({"id": "base_plugin", "refresh_on_display": True})
    plugin.generate_image = lambda *_args: calls.append("rendered") or Image.new(
        "RGB", (2, 1), "white"
    )

    image = PlaylistRefresh(
        playlist,
        plugin_instance,
        display_cached_only=True,
    ).execute(
        plugin,
        device_config,
        datetime(2026, 5, 26, 7, 5, tzinfo=timezone.utc),
    )

    assert calls == []
    assert image.getpixel((0, 0)) == (0, 0, 0)


def test_playlist_refresh_newspaper_refresh_on_display_false_overrides_rotation_default():
    calls = []
    tmp_path = make_test_dir("newspaper-instance-refresh-on-display-false")
    device_config = FakeDeviceConfig(tmp_path)
    device_config.config["display_refresh_resource_guard_enabled"] = False
    playlist = Playlist(
        "DailyDoseOfDay",
        "00:00",
        "24:00",
        plugins=[
            {
                "plugin_id": "newspaper",
                "name": "RotatingNewspaper",
                "plugin_settings": {
                    "id": "rotating-news",
                    "mediaRotationMode": "rotate",
                    "refreshOnDisplay": " false ",
                },
                "refresh": {"scheduled": "15:00"},
                "latest_refresh_time": "2999-01-01T00:00:00+00:00",
            },
        ],
    )
    plugin_instance = playlist.plugins[0]
    Image.new("RGB", (2, 1), "black").save(
        tmp_path / plugin_instance.get_image_path()
    )
    plugin = Newspaper({"id": "newspaper"})
    plugin.generate_image = lambda *_args: calls.append("rendered") or Image.new(
        "RGB", (2, 1), "white"
    )

    image = PlaylistRefresh(
        playlist,
        plugin_instance,
        display_cached_only=True,
    ).execute(
        plugin,
        device_config,
        datetime(2026, 5, 26, 16, 0, tzinfo=timezone.utc),
    )

    assert calls == []
    assert image.getpixel((0, 0)) == (0, 0, 0)


@pytest.mark.parametrize(
    ("settings", "expected"),
    [
        ({"mediaRotationMode": "rotate"}, True),
        ({"mediaRotationMode": "single"}, False),
        (
            {"mediaRotationMode": "rotate", "refreshOnDisplay": False},
            False,
        ),
        (
            {"mediaRotationMode": "single", "refreshOnDisplay": " true "},
            True,
        ),
    ],
)
def test_newspaper_refresh_on_display_uses_rotation_only_as_missing_value_default(
    settings,
    expected,
):
    plugin = Newspaper({"id": "newspaper"})

    assert plugin.wants_refresh_on_display(settings) is expected


def test_playlist_refresh_rerenders_live_refresh_due_on_scheduled_display():
    calls = []
    tmp_path = make_test_dir("scheduled-live-refresh")
    device_config = FakeDeviceConfig(tmp_path)
    playlist = Playlist(
        "DailyDoseOfDay",
        "00:00",
        "24:00",
        plugins=[
            {
                "plugin_id": "sports_dashboard",
                "name": "SportsDashboard",
                "plugin_settings": {"id": "sports_dashboard"},
                "refresh": {"interval": 900},
                "latest_refresh_time": "2026-05-26T07:00:00+00:00",
            },
        ],
    )
    plugin_instance = playlist.find_plugin("sports_dashboard", "SportsDashboard")
    Image.new("RGB", (2, 1), "black").save(tmp_path / "sports_dashboard_SportsDashboard.png")

    image = PlaylistRefresh(playlist, plugin_instance, display_cached_only=True).execute(
        FakePlugin(calls, live_state={"active": True, "interval_seconds": 900}),
        device_config,
        datetime(2026, 5, 26, 7, 15, tzinfo=timezone.utc),
    )

    assert calls == ["sports_dashboard"]
    assert image.size == (1, 1)
    assert image.getpixel((0, 0)) == (255, 255, 255)
    assert plugin_instance.latest_refresh_time == "2026-05-26T07:15:00+00:00"


def test_playlist_refresh_uses_cached_image_for_live_refresh_under_resource_pressure(monkeypatch):
    calls = []
    tmp_path = make_test_dir("scheduled-live-refresh-pressure")
    device_config = FakeDeviceConfig(tmp_path)
    device_config.config["display_refresh_min_available_mb"] = 150
    device_config.config["display_refresh_max_swap_percent"] = 30
    playlist = Playlist(
        "DailyDoseOfDay",
        "00:00",
        "24:00",
        plugins=[
            {
                "plugin_id": "sports_dashboard",
                "name": "SportsDashboard",
                "plugin_settings": {"id": "sports_dashboard"},
                "refresh": {"interval": 900},
                "latest_refresh_time": "2026-05-26T07:00:00+00:00",
            },
        ],
    )
    plugin_instance = playlist.find_plugin("sports_dashboard", "SportsDashboard")
    Image.new("RGB", (2, 1), "black").save(tmp_path / "sports_dashboard_SportsDashboard.png")
    memory = type("Memory", (), {"available": 134 * 1024 * 1024, "percent": 71.0})()
    swap = type("Swap", (), {"percent": 31.0})()
    monkeypatch.setattr("src.refresh_task.psutil.virtual_memory", lambda: memory)
    monkeypatch.setattr("src.refresh_task.psutil.swap_memory", lambda: swap)

    image = PlaylistRefresh(playlist, plugin_instance, display_cached_only=True).execute(
        FakePlugin(calls, live_state={"active": True, "interval_seconds": 900}),
        device_config,
        datetime(2026, 5, 26, 7, 15, tzinfo=timezone.utc),
    )

    assert calls == []
    assert image.size == (2, 1)
    assert image.getpixel((0, 0)) == (0, 0, 0)
    assert plugin_instance.latest_refresh_time == "2026-05-26T07:00:00+00:00"


def test_playlist_refresh_rerenders_sports_dashboard_when_display_interval_is_due():
    calls = []
    tmp_path = make_test_dir("scheduled-sports-dashboard-refresh")
    device_config = FakeDeviceConfig(tmp_path)
    playlist = Playlist(
        "DailyDoseOfDay",
        "00:00",
        "24:00",
        plugins=[
            {
                "plugin_id": "sports_dashboard",
                "name": "SportsDashboard",
                "plugin_settings": {"id": "sports_dashboard"},
                "refresh": {"interval": 900},
                "latest_refresh_time": "2026-05-26T07:00:00+00:00",
            },
        ],
    )
    plugin_instance = playlist.find_plugin("sports_dashboard", "SportsDashboard")
    Image.new("RGB", (2, 1), "black").save(tmp_path / "sports_dashboard_SportsDashboard.png")

    image = PlaylistRefresh(playlist, plugin_instance, display_cached_only=True).execute(
        FakePlugin(calls, live_state=None),
        device_config,
        datetime(2026, 5, 26, 7, 15, tzinfo=timezone.utc),
    )

    assert calls == ["sports_dashboard"]
    assert image.size == (1, 1)
    assert image.getpixel((0, 0)) == (255, 255, 255)
    assert plugin_instance.latest_refresh_time == "2026-05-26T07:15:00+00:00"


def test_playlist_refresh_rerenders_lol_info_on_scheduled_display():
    calls = []
    tmp_path = make_test_dir("scheduled-lol-info-refresh")
    device_config = FakeDeviceConfig(tmp_path)
    playlist = Playlist(
        "DailyDoseOfDay",
        "00:00",
        "24:00",
        plugins=[
            {
                "plugin_id": "lol_info",
                "name": "LoLInfo",
                "plugin_settings": {"id": "riot-page"},
                "refresh": {"interval": 7200},
                "latest_refresh_time": "2026-05-26T07:00:00+00:00",
            },
        ],
    )
    plugin_instance = playlist.find_plugin("lol_info", "LoLInfo")
    Image.new("RGB", (2, 1), "black").save(tmp_path / "lol_info_LoLInfo.png")

    image = PlaylistRefresh(playlist, plugin_instance, display_cached_only=True).execute(
        FakePlugin(calls),
        device_config,
        datetime(2026, 5, 26, 7, 5, tzinfo=timezone.utc),
    )

    assert calls == ["riot-page"]
    assert image.size == (1, 1)
    assert image.getpixel((0, 0)) == (255, 255, 255)
    assert plugin_instance.latest_refresh_time == "2026-05-26T07:05:00+00:00"


def test_playlist_refresh_rerenders_simple_calendar_on_scheduled_display():
    calls = []
    tmp_path = make_test_dir("scheduled-simple-calendar-refresh")
    device_config = FakeDeviceConfig(tmp_path)
    playlist = Playlist(
        "DailyDoseOfDay",
        "00:00",
        "24:00",
        plugins=[
            {
                "plugin_id": "simple_calendar",
                "name": "Date",
                "plugin_settings": {"id": "simple-calendar"},
                "refresh": {"scheduled": "00:00"},
                "latest_refresh_time": "2026-06-29T00:01:00+00:00",
            },
        ],
    )
    plugin_instance = playlist.find_plugin("simple_calendar", "Date")
    Image.new("RGB", (2, 1), "black").save(tmp_path / "simple_calendar_Date.png")

    image = PlaylistRefresh(playlist, plugin_instance, display_cached_only=True).execute(
        FakePlugin(calls),
        device_config,
        datetime(2026, 6, 29, 7, 5, tzinfo=timezone.utc),
    )

    assert calls == ["simple-calendar"]
    assert image.size == (1, 1)
    assert image.getpixel((0, 0)) == (255, 255, 255)
    assert plugin_instance.latest_refresh_time == "2026-06-29T07:05:00+00:00"



def test_playlist_refresh_rerenders_steam_daily_art_on_scheduled_display():
    calls = []
    tmp_path = make_test_dir("scheduled-steam-daily-art-refresh")
    device_config = FakeDeviceConfig(tmp_path)
    playlist = Playlist(
        "DailyDoseOfDay",
        "00:00",
        "24:00",
        plugins=[
            {
                "plugin_id": "steam_daily_art",
                "name": "SteamDailyArt",
                "plugin_settings": {"id": "steam-art"},
                "refresh": {"scheduled": "00:00"},
                "latest_refresh_time": "2026-06-29T00:01:00+00:00",
            },
        ],
    )
    plugin_instance = playlist.find_plugin("steam_daily_art", "SteamDailyArt")
    Image.new("RGB", (2, 1), "black").save(tmp_path / "steam_daily_art_SteamDailyArt.png")

    image = PlaylistRefresh(playlist, plugin_instance, display_cached_only=True).execute(
        FakePlugin(calls),
        device_config,
        datetime(2026, 6, 29, 7, 5, tzinfo=timezone.utc),
    )

    assert calls == ["steam-art"]
    assert image.size == (1, 1)
    assert image.getpixel((0, 0)) == (255, 255, 255)
    assert plugin_instance.latest_refresh_time == "2026-06-29T07:05:00+00:00"



def test_playlist_refresh_generates_when_scheduled_cache_is_missing():
    calls = []
    tmp_path = make_test_dir("scheduled-placeholder")
    device_config = FakeDeviceConfig(tmp_path)
    device_config.config["resolution"] = [200, 120]
    playlist = Playlist(
        "DailyDoseOfDay",
        "00:00",
        "24:00",
        plugins=[
            {
                "plugin_id": "missing",
                "name": "Missing Plugin",
                "plugin_settings": {"id": "missing"},
                "refresh": {"interval": 300},
                "latest_refresh_time": "2026-05-26T07:00:00+00:00",
            },
        ],
    )
    plugin_instance = playlist.find_plugin("missing", "Missing Plugin")

    image = PlaylistRefresh(playlist, plugin_instance, display_cached_only=True).execute(
        FakePlugin(calls),
        device_config,
        datetime(2026, 5, 26, 7, 5, tzinfo=timezone.utc),
    )

    assert calls == ["missing"]
    assert image.size == (1, 1)
    assert image.getpixel((0, 0)) == (255, 255, 255)
    assert plugin_instance.latest_refresh_time == "2026-05-26T07:05:00+00:00"
    assert (tmp_path / "missing_Missing_Plugin.png").exists()


def test_playlist_refresh_regenerates_when_scheduled_cache_is_corrupt():
    calls = []
    tmp_path = make_test_dir("scheduled-corrupt-cache")
    device_config = FakeDeviceConfig(tmp_path)
    playlist = Playlist(
        "DailyDoseOfDay",
        "00:00",
        "24:00",
        plugins=[
            {
                "plugin_id": "daily_art",
                "name": "DailyArt",
                "plugin_settings": {"id": "daily-art"},
                "refresh": {"interval": 300},
                "latest_refresh_time": "2026-05-26T07:00:00+00:00",
            },
        ],
    )
    plugin_instance = playlist.find_plugin("daily_art", "DailyArt")
    cache_path = tmp_path / "daily_art_DailyArt.png"
    cache_path.write_bytes(b"\x89PNG\r\n\x1a\n")

    image = PlaylistRefresh(playlist, plugin_instance, display_cached_only=True).execute(
        FakePlugin(calls),
        device_config,
        datetime(2026, 5, 26, 7, 5, tzinfo=timezone.utc),
    )

    assert calls == ["daily-art"]
    assert image.size == (1, 1)
    assert image.getpixel((0, 0)) == (255, 255, 255)
    assert plugin_instance.latest_refresh_time == "2026-05-26T07:05:00+00:00"
    with Image.open(cache_path) as saved:
        assert saved.size == (1, 1)


def test_playlist_force_refresh_marks_plugin_settings():
    calls = []
    tmp_path = make_test_dir("playlist-force-settings")
    device_config = FakeDeviceConfig(tmp_path)
    playlist = Playlist(
        "DailyDoseOfDay",
        "00:00",
        "24:00",
        plugins=[
            {
                "plugin_id": "sports_dashboard",
                "name": "WorldCup",
                "plugin_settings": {"id": "worldcup", "forceRefresh": "false"},
                "refresh": {"interval": 3600},
                "latest_refresh_time": "2026-05-26T07:04:00+00:00",
            },
        ],
    )
    plugin_instance = playlist.find_plugin("sports_dashboard", "WorldCup")
    Image.new("RGB", (1, 1), "black").save(tmp_path / "sports_dashboard_WorldCup.png")

    PlaylistRefresh(playlist, plugin_instance, force=True).execute(
        CapturePlugin(calls),
        device_config,
        datetime(2026, 5, 26, 7, 5, tzinfo=timezone.utc),
    )

    assert calls == [{"id": "worldcup", "forceRefresh": True, "force_refresh": True, "_inkypiDisplayRender": True}]
    assert plugin_instance.settings == {"id": "worldcup", "forceRefresh": "false"}


def test_manual_refresh_marks_plugin_settings():
    calls = []

    ManualRefresh("sports_dashboard", {"id": "worldcup"}).execute(
        CapturePlugin(calls),
        device_config=None,
        current_dt=datetime(2026, 5, 26, 7, 5, tzinfo=timezone.utc),
    )

    assert calls == [{"id": "worldcup", "forceRefresh": True, "force_refresh": True, "_inkypiDisplayRender": True}]


def test_manual_update_times_out_instead_of_waiting_forever():
    tmp_path = make_test_dir("manual-timeout")
    device_config = FakeDeviceConfig(tmp_path)
    device_config.config["manual_update_timeout_seconds"] = 0.01
    task = RefreshTask(device_config, display_manager=None)
    task.running = True
    action = ManualRefresh("sports_dashboard", {"id": "worldcup"})

    try:
        task.manual_update(action)
    except TimeoutError as exc:
        assert "Manual update timed out" in str(exc)
    else:
        raise AssertionError("manual_update should time out without a running worker thread")

    assert task.manual_update_request == ()


def test_manual_update_runs_after_in_flight_playlist_refresh(monkeypatch):
    calls = []
    tmp_path = make_test_dir("manual-after-inflight")
    playlist = Playlist(
        "DailyDoseOfDay",
        "00:00",
        "24:00",
        plugins=[
            {
                "plugin_id": "live_radar",
                "name": "LiveRadar",
                "plugin_settings": {"id": "live_radar"},
                "refresh": {"interval": 999999999},
                "latest_refresh_time": "2999-01-01T00:00:00+00:00",
            },
        ],
    )
    plugin_instance = playlist.find_plugin("live_radar", "LiveRadar")
    Image.new("RGB", (1, 1), "black").save(tmp_path / plugin_instance.get_image_path())

    device_config = ThreadedDeviceConfig(tmp_path, playlist)
    device_config.config["manual_update_timeout_seconds"] = 1
    display_manager = BlockingDisplayManager()
    task = RefreshTask(device_config, display_manager=display_manager)
    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda config: CapturePlugin(calls),
    )

    task.start()
    try:
        assert display_manager.first_display_started.wait(timeout=1)

        errors = []
        manual_thread = threading.Thread(
            target=lambda: _run_manual_update(task, playlist, plugin_instance, errors),
            daemon=True,
        )
        manual_thread.start()
        time.sleep(0.05)
        display_manager.release_first_display.set()
        manual_thread.join(timeout=1)

        assert not manual_thread.is_alive()
        assert errors == []
        assert calls
        assert all(call == {"id": "live_radar", "forceRefresh": True, "force_refresh": True, "_inkypiDisplayRender": True} for call in calls)
    finally:
        display_manager.release_first_display.set()
        task.stop()


def _run_manual_update(task, playlist, plugin_instance, errors):
    try:
        task.manual_update(PlaylistRefresh(playlist, plugin_instance, force=True))
    except Exception as exc:
        errors.append(exc)


def test_refresh_due_plugin_instances_skips_displayed_plugin(monkeypatch):
    calls = []
    tmp_path = make_test_dir("skip-displayed")
    device_config = FakeDeviceConfig(tmp_path)
    task = RefreshTask(device_config, display_manager=None)
    playlist = Playlist(
        "DailyDoseOfDay",
        "00:00",
        "24:00",
        plugins=[
            {
                "plugin_id": "displayed",
                "name": "Displayed Plugin",
                "plugin_settings": {"id": "displayed"},
                "refresh": {"interval": 300},
                "latest_refresh_time": "2026-05-26T07:00:00+00:00",
            },
            {
                "plugin_id": "other",
                "name": "Other Plugin",
                "plugin_settings": {"id": "other"},
                "refresh": {"interval": 300},
                "latest_refresh_time": "2026-05-26T07:00:00+00:00",
            },
        ],
    )

    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda config: FakePlugin(calls),
    )

    displayed = playlist.find_plugin("displayed", "Displayed Plugin")
    task._refresh_due_plugin_instances(
        playlist,
        datetime(2026, 5, 26, 7, 5, tzinfo=timezone.utc),
        skip_plugin_instance=displayed,
    )

    assert calls == ["other"]
    assert not (tmp_path / "displayed_Displayed_Plugin.png").exists()
    assert (tmp_path / "other_Other_Plugin.png").exists()
    assert displayed.latest_refresh_time == "2026-05-26T07:00:00+00:00"
    assert playlist.find_plugin("other", "Other Plugin").latest_refresh_time == "2026-05-26T07:05:00+00:00"
    assert device_config.write_count == 1


def test_refresh_due_plugin_instances_refreshes_displayed_refresh_on_display_only(monkeypatch):
    calls = []
    tmp_path = make_test_dir("displayed-on-display-cache")
    device_config = FakeDeviceConfig(tmp_path)
    current_dt = datetime(2026, 5, 26, 16, 0, tzinfo=timezone.utc)
    playlist = Playlist(
        "DailyDoseOfDay",
        "00:00",
        "24:00",
        plugins=[
            {
                "plugin_id": "newspaper",
                "name": "Displayed News",
                "plugin_settings": {"id": "displayed", "mediaRotationMode": "rotate"},
                "refresh": {"scheduled": "15:00"},
                "latest_refresh_time": "2026-05-26T15:01:00+00:00",
            },
            {
                "plugin_id": "newspaper",
                "name": "Other News",
                "plugin_settings": {"id": "other", "mediaRotationMode": "rotate"},
                "refresh": {"scheduled": "15:00"},
                "latest_refresh_time": "2026-05-26T15:01:00+00:00",
            },
        ],
    )
    Image.new("RGB", (1, 1), "black").save(tmp_path / "newspaper_Displayed_News.png")
    Image.new("RGB", (1, 1), "black").save(tmp_path / "newspaper_Other_News.png")
    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda config: FakePlugin(calls),
    )

    displayed = playlist.find_plugin("newspaper", "Displayed News")
    task = RefreshTask(device_config, display_manager=None)
    task._refresh_due_plugin_instances(
        playlist,
        current_dt,
        displayed_plugin_instance=displayed,
    )

    assert calls == ["displayed"]
    assert displayed.latest_refresh_time == "2026-05-26T16:00:00+00:00"
    assert playlist.find_plugin("newspaper", "Other News").latest_refresh_time == "2026-05-26T15:01:00+00:00"
    assert device_config.write_count == 1


def test_refresh_due_plugin_instances_refreshes_displayed_lol_info_by_default(monkeypatch):
    calls = []
    tmp_path = make_test_dir("displayed-lol-info-refresh")
    device_config = FakeDeviceConfig(tmp_path)
    current_dt = datetime(2026, 6, 4, 16, 0, tzinfo=timezone.utc)
    playlist = Playlist(
        "DailyDoseOfDay",
        "00:00",
        "24:00",
        plugins=[
            {
                "plugin_id": "lol_info",
                "name": "LoL Daily",
                "plugin_settings": {"id": "displayed"},
                "refresh": {"scheduled": "15:00"},
                "latest_refresh_time": "2026-06-04T15:01:00+00:00",
            },
            {
                "plugin_id": "lol_info",
                "name": "LoL Other",
                "plugin_settings": {"id": "other"},
                "refresh": {"scheduled": "15:00"},
                "latest_refresh_time": "2026-06-04T15:01:00+00:00",
            },
        ],
    )
    Image.new("RGB", (1, 1), "black").save(tmp_path / "lol_info_LoL_Daily.png")
    Image.new("RGB", (1, 1), "black").save(tmp_path / "lol_info_LoL_Other.png")
    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda config: FakePlugin(calls, refresh_on_display=True),
    )

    displayed = playlist.find_plugin("lol_info", "LoL Daily")
    task = RefreshTask(device_config, display_manager=None)
    task._refresh_due_plugin_instances(
        playlist,
        current_dt,
        displayed_plugin_instance=displayed,
    )

    assert calls == ["displayed"]
    assert displayed.latest_refresh_time == "2026-06-04T16:00:00+00:00"
    assert playlist.find_plugin("lol_info", "LoL Other").latest_refresh_time == "2026-06-04T15:01:00+00:00"
    assert device_config.write_count == 1


def test_refresh_due_plugin_instances_force_refreshes_fresh_cache(monkeypatch):
    calls = []
    tmp_path = make_test_dir("force-cache")
    device_config = FakeDeviceConfig(tmp_path)
    task = RefreshTask(device_config, display_manager=None)
    playlist = Playlist(
        "DailyDoseOfDay",
        "00:00",
        "24:00",
        plugins=[
            {
                "plugin_id": "fresh",
                "name": "Fresh Plugin",
                "plugin_settings": {"id": "fresh"},
                "refresh": {"interval": 3600},
                "latest_refresh_time": "2026-05-26T07:04:00+00:00",
            },
        ],
    )

    Image.new("RGB", (1, 1), "black").save(tmp_path / "fresh_Fresh_Plugin.png")
    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda config: FakePlugin(calls),
    )

    task._refresh_due_plugin_instances(
        playlist,
        datetime(2026, 5, 26, 7, 5, tzinfo=timezone.utc),
        force=True,
    )

    assert calls == ["fresh"]
    assert playlist.find_plugin("fresh", "Fresh Plugin").latest_refresh_time == "2026-05-26T07:05:00+00:00"
    assert device_config.write_count == 1


def test_theme_refresh_prefers_currently_displayed_playlist_plugin():
    tmp_path = make_test_dir("theme-current-plugin")
    device_config = FakeDeviceConfig(tmp_path)
    task = RefreshTask(device_config, display_manager=None)
    playlist = Playlist(
        "DailyDoseOfDay",
        "00:00",
        "24:00",
        plugins=[
            {
                "plugin_id": "one",
                "name": "One",
                "plugin_settings": {"id": "one"},
                "refresh": {"interval": 3600},
            },
            {
                "plugin_id": "two",
                "name": "Two",
                "plugin_settings": {"id": "two"},
                "refresh": {"interval": 3600},
            },
        ],
    )
    manager = PlaylistManager([playlist])
    latest = RefreshInfo(
        refresh_type="Playlist",
        plugin_id="one",
        playlist="DailyDoseOfDay",
        plugin_instance="One",
        refresh_time="2026-05-26T07:00:00+00:00",
        image_hash="old",
    )

    _playlist, plugin = task._determine_theme_refresh_plugin(
        manager,
        latest,
        datetime(2026, 5, 26, 7, 5, tzinfo=timezone.utc),
    )

    assert plugin.name == "One"


def test_theme_state_persists_after_forced_theme_refresh():
    tmp_path = make_test_dir("theme-persist")
    device_config = FakeDeviceConfig(tmp_path)
    device_config.config["active_theme"] = "night"
    task = RefreshTask(device_config, display_manager=None)
    current_dt = datetime(2026, 5, 26, 7, 5, tzinfo=timezone.utc)
    theme_context = {
        "mode": "day",
        "source": "weather",
        "reason": "sunrise/sunset",
        "date": "2026-05-26",
        "sunrise": "2026-05-26T05:50:00-07:00",
        "sunset": "2026-05-26T20:15:00-07:00",
    }

    assert task._has_theme_changed(theme_context)
    task._persist_active_theme(theme_context, current_dt)

    assert device_config.config["active_theme"] == "day"
    assert device_config.config["active_theme_info"]["source"] == "weather"


def test_theme_refresh_failure_suppresses_immediate_same_mode_retry():
    tmp_path = make_test_dir("theme-failure-cooldown")
    device_config = FakeDeviceConfig(tmp_path)
    device_config.config["active_theme"] = "day"
    device_config.config["theme_refresh_retry_cooldown_seconds"] = 120
    task = RefreshTask(device_config, display_manager=None)
    current_dt = datetime(2026, 5, 26, 22, 8, tzinfo=timezone.utc)
    theme_context = {
        "mode": "night",
        "source": "weather",
        "reason": "sunrise/sunset",
        "date": "2026-05-26",
    }

    assert task._has_theme_changed(theme_context, current_dt)
    task._mark_theme_refresh_failed(theme_context, current_dt, RuntimeError("screenshot timeout"))

    assert device_config.config["active_theme"] == "day"
    assert device_config.config["active_theme_refresh_failure"]["mode"] == "night"
    assert not task._has_theme_changed(theme_context, current_dt + timedelta(seconds=30))


def test_theme_refresh_failure_allows_retry_after_cooldown():
    tmp_path = make_test_dir("theme-failure-retry")
    device_config = FakeDeviceConfig(tmp_path)
    device_config.config["active_theme"] = "day"
    device_config.config["theme_refresh_retry_cooldown_seconds"] = 120
    task = RefreshTask(device_config, display_manager=None)
    current_dt = datetime(2026, 5, 26, 22, 8, tzinfo=timezone.utc)
    theme_context = {
        "mode": "night",
        "source": "weather",
        "reason": "sunrise/sunset",
        "date": "2026-05-26",
    }

    task._mark_theme_refresh_failed(theme_context, current_dt, RuntimeError("screenshot timeout"))

    assert task._has_theme_changed(theme_context, current_dt + timedelta(seconds=121))


def test_playlist_refresh_refreshes_rotating_newspaper_on_display():
    calls = []
    tmp_path = make_test_dir("newspaper-on-display")
    device_config = FakeDeviceConfig(tmp_path)
    playlist = Playlist(
        "DailyDoseOfDay",
        "00:00",
        "24:00",
        plugins=[
            {
                "plugin_id": "newspaper",
                "name": "ChinaDaily",
                "plugin_settings": {
                    "id": "rotating-news",
                    "mediaRotationMode": "rotate",
                },
                "refresh": {"scheduled": "15:00"},
                "latest_refresh_time": "2026-05-26T15:01:00+00:00",
            },
        ],
    )
    plugin_instance = playlist.find_plugin("newspaper", "ChinaDaily")
    old_image = tmp_path / "newspaper_ChinaDaily.png"
    Image.new("RGB", (1, 1), "black").save(old_image)

    action = PlaylistRefresh(playlist, plugin_instance)
    action.execute(
        FakePlugin(calls),
        device_config,
        datetime(2026, 5, 26, 16, 0, tzinfo=timezone.utc),
    )

    assert calls == ["rotating-news"]
    assert plugin_instance.latest_refresh_time == "2026-05-26T16:00:00+00:00"


def test_playlist_refresh_refreshes_backtothedate_on_display():
    calls = []
    tmp_path = make_test_dir("backtothedate-on-display")
    device_config = FakeDeviceConfig(tmp_path)
    playlist = Playlist(
        "DailyDoseOfDay",
        "00:00",
        "24:00",
        plugins=[
            {
                "plugin_id": "backtothedate",
                "name": "BacktotheDate",
                "plugin_settings": {"id": "backtothedate"},
                "refresh": {"scheduled": "15:00"},
                "latest_refresh_time": "2026-05-26T15:01:00+00:00",
            },
        ],
    )
    plugin_instance = playlist.find_plugin("backtothedate", "BacktotheDate")
    old_image = tmp_path / "backtothedate_BacktotheDate.png"
    Image.new("RGB", (1, 1), "black").save(old_image)

    action = PlaylistRefresh(playlist, plugin_instance)
    action.execute(
        FakePlugin(calls),
        device_config,
        datetime(2026, 5, 26, 16, 0, tzinfo=timezone.utc),
    )

    assert calls == ["backtothedate"]
    assert plugin_instance.latest_refresh_time == "2026-05-26T16:00:00+00:00"


def test_playlist_refresh_creates_plugin_image_directory_before_save():
    calls = []
    tmp_path = make_test_dir("create-image-dir")
    plugin_image_dir = tmp_path / "missing" / "plugins"
    device_config = FakeDeviceConfig(plugin_image_dir)
    playlist = Playlist(
        "DailyDoseOfDay",
        "00:00",
        "24:00",
        plugins=[
            {
                "plugin_id": "clock",
                "name": "Clock",
                "plugin_settings": {"id": "clock"},
                "refresh": {"interval": 300},
            },
        ],
    )
    plugin_instance = playlist.find_plugin("clock", "Clock")

    PlaylistRefresh(playlist, plugin_instance).execute(
        FakePlugin(calls),
        device_config,
        datetime(2026, 5, 26, 16, 0, tzinfo=timezone.utc),
    )

    assert calls == ["clock"]
    assert (plugin_image_dir / "clock_Clock.png").exists()


def test_get_current_datetime_falls_back_to_utc_for_invalid_timezone():
    tmp_path = make_test_dir("invalid-timezone")
    device_config = FakeDeviceConfig(tmp_path)
    device_config.config["timezone"] = "Not/AZone"
    task = RefreshTask(device_config, display_manager=None)

    current_dt = task._get_current_datetime()

    assert current_dt.tzinfo is not None
    assert current_dt.tzinfo.zone == "UTC"


class NonCacheablePlugin:
    def __init__(self, calls):
        self.calls = calls

    def generate_image(self, settings, device_config):
        self.calls.append(settings["id"])
        image = Image.new("RGB", (1, 1), "red")
        image.info["inkypi_skip_cache"] = True
        return image


def test_refresh_due_plugin_instances_preserves_cache_for_non_cacheable_image(monkeypatch):
    calls = []
    tmp_path = make_test_dir("non-cacheable-cache")
    device_config = FakeDeviceConfig(tmp_path)
    task = RefreshTask(device_config, display_manager=None)
    playlist = Playlist(
        "DailyDoseOfDay",
        "00:00",
        "24:00",
        plugins=[
            {
                "plugin_id": "bambu_monitor",
                "name": "Bambu",
                "plugin_settings": {"id": "bambu"},
                "refresh": {"interval": 300},
                "latest_refresh_time": "2026-05-26T07:00:00+00:00",
            },
        ],
    )
    cache_path = tmp_path / "bambu_monitor_Bambu.png"
    Image.new("RGB", (2, 1), "black").save(cache_path)

    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda config: NonCacheablePlugin(calls),
    )

    task._refresh_due_plugin_instances(
        playlist,
        datetime(2026, 5, 26, 7, 5, tzinfo=timezone.utc),
    )

    assert calls == ["bambu"]
    assert playlist.find_plugin("bambu_monitor", "Bambu").latest_refresh_time == "2026-05-26T07:00:00+00:00"
    assert device_config.write_count == 0
    with Image.open(cache_path) as saved:
        assert saved.size == (2, 1)
        assert saved.getpixel((0, 0)) == (0, 0, 0)


def test_playlist_refresh_uses_previous_cache_for_non_cacheable_display_image():
    calls = []
    tmp_path = make_test_dir("non-cacheable-display")
    device_config = FakeDeviceConfig(tmp_path)
    playlist = Playlist(
        "DailyDoseOfDay",
        "00:00",
        "24:00",
        plugins=[
            {
                "plugin_id": "bambu_monitor",
                "name": "Bambu",
                "plugin_settings": {"id": "bambu"},
                "refresh": {"interval": 300},
                "latest_refresh_time": "2026-05-26T07:00:00+00:00",
            },
        ],
    )
    plugin_instance = playlist.find_plugin("bambu_monitor", "Bambu")
    cache_path = tmp_path / "bambu_monitor_Bambu.png"
    Image.new("RGB", (2, 1), "black").save(cache_path)

    image = PlaylistRefresh(playlist, plugin_instance, force=True).execute(
        NonCacheablePlugin(calls),
        device_config,
        datetime(2026, 5, 26, 7, 5, tzinfo=timezone.utc),
    )

    assert calls == ["bambu"]
    assert image.size == (2, 1)
    assert image.getpixel((0, 0)) == (0, 0, 0)
    assert plugin_instance.latest_refresh_time == "2026-05-26T07:00:00+00:00"
    with Image.open(cache_path) as saved:
        assert saved.size == (2, 1)
        assert saved.getpixel((0, 0)) == (0, 0, 0)


def test_playlist_worker_uses_previous_cache_for_non_cacheable_display(monkeypatch):
    tmp_path = make_test_dir("non-cacheable-worker-display")
    playlist = _runtime_playlist(
        _runtime_plugin_data(
            "bambu_monitor",
            "Bambu",
            latest_refresh_time="2999-01-01T00:00:00+00:00",
        )
    )
    task, device_config, _clock = _make_runtime_task(tmp_path, playlists=[playlist])
    device_config.config.update({"theme_mode": "day", "active_theme": "day"})
    cache_path = _write_runtime_cache(
        task,
        playlist.plugins[0],
        Image.new("RGB", (2, 1), "black"),
    )
    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda config: NonCacheablePlugin([]),
    )
    task.start()
    try:
        assert task.wait_until_waiting(timeout=1.0)
        job = task.submit_playlist_display(device_config.playlist_manager.first_instance_uuid())
        result = task.wait_for_job(job["id"], timeout=1.0)

        assert result["status"] == "completed"
        displayed = task.display_manager.calls[0][0]
        assert displayed.size == (2, 1)
        assert displayed.getpixel((0, 0)) == (0, 0, 0)
    finally:
        task.stop(join_timeout=1.0)
    with Image.open(cache_path) as saved:
        assert saved.size == (2, 1)
        assert saved.getpixel((0, 0)) == (0, 0, 0)


def test_playlist_worker_rejects_invalid_explicit_refresh_on_display(monkeypatch):
    tmp_path = make_test_dir("invalid-refresh-on-display-worker")
    plugin_data = _runtime_plugin_data(
        "base_plugin",
        "Invalid Explicit Boolean",
    )
    plugin_data["plugin_settings"]["refreshOnDisplay"] = "sometimes"
    playlist = _runtime_playlist(plugin_data)
    task, device_config, _clock = _make_runtime_task(tmp_path, playlists=[playlist])
    _write_runtime_cache(
        task,
        playlist.plugins[0],
        Image.new("RGB", (2, 1), "black"),
    )
    plugin = BasePlugin({"id": "base_plugin", "refresh_on_display": False})
    plugin.generate_image = lambda *_args: Image.new("RGB", (2, 1), "white")
    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda _config: plugin,
    )

    task.start()
    try:
        assert task.wait_until_waiting(timeout=1.0)
        job = task.submit_playlist_display(
            device_config.playlist_manager.first_instance_uuid(),
            force=False,
            display_cached_only=True,
        )
        result = task.wait_for_job(job["id"], timeout=1.0)

        assert result["status"] == "failed"
        assert "refreshOnDisplay must be true or false" in result["error"]
        assert task.display_manager.calls == []
    finally:
        task.stop(join_timeout=1.0)


def test_scheduler_selection_rejects_invalid_explicit_refresh_on_display():
    tmp_path = make_test_dir("invalid-refresh-on-display-selection")
    plugin_data = _runtime_plugin_data("base_plugin", "Invalid Selection Boolean")
    plugin_data["plugin_settings"]["refreshOnDisplay"] = "sometimes"
    playlist = _runtime_playlist(plugin_data)
    task, _device_config, _clock = _make_runtime_task(
        tmp_path,
        playlists=[playlist],
    )
    plugin = BasePlugin({"id": "base_plugin", "refresh_on_display": False})

    with pytest.raises(PluginSettingError, match="refreshOnDisplay"):
        task._plugin_wants_refresh_on_display(playlist.plugins[0], plugin=plugin)


class FailingPlugin:
    def generate_image(self, settings, device_config):
        raise RuntimeError("boom")


def test_refresh_due_plugin_instances_limits_background_pass(monkeypatch):
    calls = []
    tmp_path = make_test_dir("due-cache-limit")
    device_config = FakeDeviceConfig(tmp_path)
    task = RefreshTask(device_config, display_manager=None)
    playlist = Playlist(
        "DailyDoseOfDay",
        "00:00",
        "24:00",
        plugins=[
            {
                "plugin_id": "one",
                "name": "One",
                "plugin_settings": {"id": "one"},
                "refresh": {"interval": 300},
                "latest_refresh_time": "2026-05-26T07:00:00+00:00",
            },
            {
                "plugin_id": "two",
                "name": "Two",
                "plugin_settings": {"id": "two"},
                "refresh": {"interval": 300},
                "latest_refresh_time": "2026-05-26T07:00:00+00:00",
            },
        ],
    )

    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda config: FakePlugin(calls),
    )

    task._refresh_due_plugin_instances(
        playlist,
        datetime(2026, 5, 26, 7, 5, tzinfo=timezone.utc),
        max_updates=1,
    )

    assert calls == ["one"]
    assert (tmp_path / "one_One.png").exists()
    assert not (tmp_path / "two_Two.png").exists()
    assert playlist.find_plugin("one", "One").latest_refresh_time == "2026-05-26T07:05:00+00:00"
    assert playlist.find_plugin("two", "Two").latest_refresh_time == "2026-05-26T07:00:00+00:00"
    assert device_config.write_count == 1


def test_failed_due_cache_refresh_records_failure_without_advancing_success(monkeypatch):
    tmp_path = make_test_dir("due-cache-failure-cooldown")
    device_config = FakeDeviceConfig(tmp_path)
    task = RefreshTask(
        device_config,
        display_manager=None,
        retry_registry=RetryRegistry(jitter=lambda delay: delay),
    )
    playlist = Playlist(
        "DailyDoseOfDay",
        "00:00",
        "24:00",
        plugins=[
            {
                "plugin_id": "bad",
                "name": "Bad Plugin",
                "plugin_settings": {"id": "bad"},
                "refresh": {"interval": 300},
                "latest_refresh_time": "2026-05-26T07:00:00+00:00",
            },
        ],
    )

    attempts = []

    def failing_plugin(_config):
        attempts.append("attempt")
        return FailingPlugin()

    monkeypatch.setattr("src.refresh_task.get_plugin_instance", failing_plugin)

    task._refresh_due_plugin_instances(
        playlist,
        datetime(2026, 5, 26, 7, 5, tzinfo=timezone.utc),
        max_updates=1,
    )

    instance = playlist.find_plugin("bad", "Bad Plugin")
    state = task.runtime_state.snapshot().instances[instance.instance_uuid]
    assert instance.latest_refresh_time == "2026-05-26T07:00:00+00:00"
    assert state.last_success_at is None
    assert state.last_failure_at == "2026-05-26T07:05:00+00:00"
    assert state.next_retry_at == "2026-05-26T07:05:30+00:00"
    assert not (tmp_path / "bad_Bad_Plugin.png").exists()
    assert device_config.write_count == 0
    attempts_before_cooldown_probe = list(attempts)

    task._refresh_due_plugin_instances(
        playlist,
        datetime(2026, 5, 26, 7, 5, 10, tzinfo=timezone.utc),
        max_updates=1,
    )
    assert attempts == attempts_before_cooldown_probe

    attempts_before_retry = len(attempts)
    task._refresh_due_plugin_instances(
        playlist,
        datetime(2026, 5, 26, 7, 5, 31, tzinfo=timezone.utc),
        max_updates=1,
    )
    assert len(attempts) > attempts_before_retry


def test_memory_maintenance_collects_and_trims_when_forced(monkeypatch):
    tmp_path = make_test_dir("memory-maintenance")
    device_config = FakeDeviceConfig(tmp_path)
    task = RefreshTask(device_config, display_manager=None)
    collected = []

    monkeypatch.setattr("src.refresh_task.gc.collect", lambda: collected.append("gc") or 7)
    monkeypatch.setattr(task, "_malloc_trim", lambda: True)
    monkeypatch.setattr(
        task,
        "_read_memory_stats",
        lambda: {"available_mb": 64.0, "memory_percent": 91.0, "swap_percent": 99.0},
    )

    result = task._run_memory_maintenance("test", force=True)

    assert collected == ["gc"]
    assert result["collected_objects"] == 7
    assert result["malloc_trim"] is True
    assert result["after"]["swap_percent"] == 99.0


def test_memory_watchdog_requests_restart_on_hard_swap_pressure(monkeypatch):
    tmp_path = make_test_dir("memory-watchdog-swap")
    device_config = FakeDeviceConfig(tmp_path)
    device_config.config["memory_watchdog_min_available_mb"] = 70
    device_config.config["memory_watchdog_max_swap_percent"] = 98
    device_config.config["memory_watchdog_restart_min_interval_seconds"] = 1800
    task = RefreshTask(device_config, display_manager=None)
    captured = []

    memory = type("Memory", (), {"available": 200 * 1024 * 1024, "percent": 82.0})()
    swap = type("Swap", (), {"percent": 99.0})()
    monkeypatch.setattr("src.refresh_task.psutil.virtual_memory", lambda: memory)
    monkeypatch.setattr("src.refresh_task.psutil.swap_memory", lambda: swap)
    monkeypatch.setattr("src.refresh_task.time.monotonic", lambda: 1000.0)
    monkeypatch.setattr("src.refresh_task.time.time", lambda: 2000.0)
    monkeypatch.setattr(
        task,
        "_restart_process_for_memory_pressure",
        lambda stats, min_available_mb, max_swap_percent: captured.append(
            (stats, min_available_mb, max_swap_percent)
        ),
    )

    assert task._memory_watchdog_should_restart() is True

    assert captured[0][0]["available_mb"] == 200.0
    assert captured[0][0]["swap_percent"] == 99.0
    assert captured[0][1:] == (70.0, 98.0)
    assert float((tmp_path / ".memory_watchdog_last_restart").read_text(encoding="utf-8")) == 2000.0


def test_memory_restart_request_never_exits_from_refresh_worker(monkeypatch):
    tmp_path = make_test_dir("memory-restart-request")
    task = RefreshTask(FakeDeviceConfig(tmp_path), display_manager=None)
    exits = []
    monkeypatch.setattr("src.refresh_task.os._exit", lambda code: exits.append(code))
    stats = {"available_mb": 40.0, "swap_percent": 90.0}

    task._restart_process_for_memory_pressure(stats, 70.0, 75.0)

    assert exits == []
    assert task.restart_request == {
        "reason": "memory_pressure",
        "available_mb": 40.0,
        "min_available_mb": 70.0,
        "swap_percent": 90.0,
        "max_swap_percent": 75.0,
    }


def test_memory_watchdog_default_restarts_before_kernel_oom_swap_pressure(monkeypatch):
    tmp_path = make_test_dir("memory-watchdog-default-swap")
    device_config = FakeDeviceConfig(tmp_path)
    device_config.config["memory_watchdog_restart_min_interval_seconds"] = 1800
    task = RefreshTask(device_config, display_manager=None)
    captured = []

    memory = type("Memory", (), {"available": 200 * 1024 * 1024, "percent": 82.0})()
    swap = type("Swap", (), {"percent": 80.0})()
    monkeypatch.setattr("src.refresh_task.psutil.virtual_memory", lambda: memory)
    monkeypatch.setattr("src.refresh_task.psutil.swap_memory", lambda: swap)
    monkeypatch.setattr("src.refresh_task.time.monotonic", lambda: 1000.0)
    monkeypatch.setattr("src.refresh_task.time.time", lambda: 2000.0)
    monkeypatch.setattr(
        task,
        "_restart_process_for_memory_pressure",
        lambda stats, min_available_mb, max_swap_percent: captured.append(
            (stats, min_available_mb, max_swap_percent)
        ),
    )

    assert task._memory_watchdog_should_restart() is True

    assert captured[0][0]["available_mb"] == 200.0
    assert captured[0][0]["swap_percent"] == 80.0
    assert captured[0][1:] == (70.0, 75.0)


def test_memory_watchdog_respects_persisted_restart_cooldown(monkeypatch):
    tmp_path = make_test_dir("memory-watchdog-cooldown")
    (tmp_path / ".memory_watchdog_last_restart").write_text("1990.0", encoding="utf-8")
    device_config = FakeDeviceConfig(tmp_path)
    device_config.config["memory_watchdog_min_available_mb"] = 70
    device_config.config["memory_watchdog_max_swap_percent"] = 98
    device_config.config["memory_watchdog_restart_min_interval_seconds"] = 60
    task = RefreshTask(device_config, display_manager=None)
    captured = []

    memory = type("Memory", (), {"available": 50 * 1024 * 1024, "percent": 95.0})()
    swap = type("Swap", (), {"percent": 99.0})()
    monkeypatch.setattr("src.refresh_task.psutil.virtual_memory", lambda: memory)
    monkeypatch.setattr("src.refresh_task.psutil.swap_memory", lambda: swap)
    monkeypatch.setattr("src.refresh_task.time.monotonic", lambda: 1000.0)
    monkeypatch.setattr("src.refresh_task.time.time", lambda: 2000.0)
    monkeypatch.setattr(task, "_restart_process_for_memory_pressure", lambda *args: captured.append(args))

    assert task._memory_watchdog_should_restart() is False
    assert captured == []

def test_cache_refresh_resource_pressure_ignores_swap_when_memory_is_available(monkeypatch):
    tmp_path = make_test_dir("cache-pressure")
    device_config = FakeDeviceConfig(tmp_path)
    device_config.config["background_cache_refresh_min_available_mb"] = 80
    device_config.config["background_cache_refresh_max_swap_percent"] = 70
    task = RefreshTask(device_config, display_manager=None)

    memory = type("Memory", (), {"available": 200 * 1024 * 1024})()
    swap = type("Swap", (), {"percent": 91.0})()
    monkeypatch.setattr("src.refresh_task.psutil.virtual_memory", lambda: memory)
    monkeypatch.setattr("src.refresh_task.psutil.swap_memory", lambda: swap)

    assert task._cache_refresh_under_resource_pressure() is False


def test_cache_refresh_resource_pressure_respects_low_available_memory(monkeypatch):
    tmp_path = make_test_dir("cache-pressure-low-memory")
    device_config = FakeDeviceConfig(tmp_path)
    device_config.config["background_cache_refresh_min_available_mb"] = 80
    device_config.config["background_cache_refresh_max_swap_percent"] = 70
    task = RefreshTask(device_config, display_manager=None)

    memory = type("Memory", (), {"available": 60 * 1024 * 1024})()
    swap = type("Swap", (), {"percent": 10.0})()
    monkeypatch.setattr("src.refresh_task.psutil.virtual_memory", lambda: memory)
    monkeypatch.setattr("src.refresh_task.psutil.swap_memory", lambda: swap)

    assert task._cache_refresh_under_resource_pressure() is True


def test_cache_refresh_default_pressure_gate_blocks_low_zero2w_headroom(monkeypatch):
    tmp_path = make_test_dir("cache-pressure-zero2w-default")
    device_config = FakeDeviceConfig(tmp_path)
    task = RefreshTask(device_config, display_manager=None)

    memory = type("Memory", (), {"available": 134 * 1024 * 1024})()
    swap = type("Swap", (), {"percent": 29.0})()
    monkeypatch.setattr("src.refresh_task.psutil.virtual_memory", lambda: memory)
    monkeypatch.setattr("src.refresh_task.psutil.swap_memory", lambda: swap)

    assert task._cache_refresh_under_resource_pressure() is True


def test_live_cache_refresh_can_ignore_swap_pressure(monkeypatch):
    tmp_path = make_test_dir("cache-pressure-live")
    device_config = FakeDeviceConfig(tmp_path)
    device_config.config["background_cache_refresh_min_available_mb"] = 80
    device_config.config["background_cache_refresh_max_swap_percent"] = 70
    task = RefreshTask(device_config, display_manager=None)

    memory = type("Memory", (), {"available": 200 * 1024 * 1024})()
    swap = type("Swap", (), {"percent": 82.0})()
    monkeypatch.setattr("src.refresh_task.psutil.virtual_memory", lambda: memory)
    monkeypatch.setattr("src.refresh_task.psutil.swap_memory", lambda: swap)

    assert task._cache_refresh_under_resource_pressure(allow_high_swap=True) is False


def test_live_cache_refresh_still_respects_low_memory(monkeypatch):
    tmp_path = make_test_dir("cache-pressure-live-low-memory")
    device_config = FakeDeviceConfig(tmp_path)
    device_config.config["background_cache_refresh_min_available_mb"] = 80
    device_config.config["background_cache_refresh_max_swap_percent"] = 70
    task = RefreshTask(device_config, display_manager=None)

    memory = type("Memory", (), {"available": 60 * 1024 * 1024})()
    swap = type("Swap", (), {"percent": 82.0})()
    monkeypatch.setattr("src.refresh_task.psutil.virtual_memory", lambda: memory)
    monkeypatch.setattr("src.refresh_task.psutil.swap_memory", lambda: swap)

    assert task._cache_refresh_under_resource_pressure(allow_high_swap=True) is True

def test_targeted_cache_refresh_passes_swap_pressure_override(monkeypatch):
    tmp_path = make_test_dir("cache-pressure-live-path")
    device_config = FakeDeviceConfig(tmp_path)
    task = RefreshTask(device_config, display_manager=None)
    task.running = True
    captured = []

    def fake_pressure(**kwargs):
        captured.append(kwargs)
        return True

    monkeypatch.setattr(task, "_cache_refresh_under_resource_pressure", fake_pressure)

    task._start_due_plugin_cache_refresh(
        playlist=None,
        current_dt=datetime(2026, 5, 26, 7, 5, tzinfo=timezone.utc),
        only_plugin_id="live_plugin",
    )
    task._start_due_plugin_cache_refresh(
        playlist=None,
        current_dt=datetime(2026, 5, 26, 7, 5, tzinfo=timezone.utc),
    )

    assert captured == [{"allow_high_swap": True}, {"allow_high_swap": False}]


def test_cache_refresh_in_progress_reflects_background_lock():
    tmp_path = make_test_dir("cache-refresh-in-progress")
    task = RefreshTask(FakeDeviceConfig(tmp_path), display_manager=None)

    assert task.cache_refresh_in_progress() is False
    assert task.cache_refresh_lock.acquire(blocking=False) is True
    try:
        assert task.cache_refresh_in_progress() is True
    finally:
        task.cache_refresh_lock.release()


def test_manual_update_in_progress_reflects_manual_refresh_lock():
    tmp_path = make_test_dir("manual-refresh-in-progress")
    task = RefreshTask(FakeDeviceConfig(tmp_path), display_manager=None)

    assert task.manual_update_in_progress() is False
    assert task.manual_refresh_lock.acquire(blocking=False) is True
    try:
        assert task.manual_update_in_progress() is True
    finally:
        task.manual_refresh_lock.release()


def test_background_cache_refresh_skips_while_manual_update_running(monkeypatch):
    tmp_path = make_test_dir("manual-refresh-skips-background-cache")
    task = RefreshTask(FakeDeviceConfig(tmp_path), display_manager=None)
    task.running = True
    pressure_checks = []
    monkeypatch.setattr(task, "_cache_refresh_under_resource_pressure", lambda **kwargs: pressure_checks.append(kwargs) or False)

    assert task.manual_refresh_lock.acquire(blocking=False) is True
    try:
        task._start_due_plugin_cache_refresh(
            playlist=None,
            current_dt=datetime(2026, 5, 26, 7, 5, tzinfo=timezone.utc),
        )
    finally:
        task.manual_refresh_lock.release()

    assert pressure_checks == []
    assert task.cache_refresh_in_progress() is False


def test_refresh_due_plugin_instances_stops_when_manual_update_starts(monkeypatch):
    calls = []
    tmp_path = make_test_dir("manual-refresh-stops-cache-pass")
    device_config = FakeDeviceConfig(tmp_path)
    task = RefreshTask(device_config, display_manager=None)
    playlist = Playlist(
        "DailyDoseOfDay",
        "00:00",
        "24:00",
        plugins=[
            {
                "plugin_id": "a_plugin",
                "name": "A Plugin",
                "plugin_settings": {"id": "a_plugin"},
                "refresh": {"interval": 300},
                "latest_refresh_time": "2026-05-26T07:00:00+00:00",
            },
            {
                "plugin_id": "b_plugin",
                "name": "B Plugin",
                "plugin_settings": {"id": "b_plugin"},
                "refresh": {"interval": 300},
                "latest_refresh_time": "2026-05-26T07:00:00+00:00",
            },
        ],
    )
    manual_states = iter([False, False, True])
    monkeypatch.setattr(task, "manual_update_in_progress", lambda: next(manual_states, True))
    monkeypatch.setattr("src.refresh_task.get_plugin_instance", lambda config: FakePlugin(calls))

    task._refresh_due_plugin_instances(
        playlist,
        datetime(2026, 5, 26, 7, 6, tzinfo=timezone.utc),
    )

    assert calls == ["a_plugin"]
    assert playlist.find_plugin("a_plugin", "A Plugin").latest_refresh_time == "2026-05-26T07:06:00+00:00"
    assert playlist.find_plugin("b_plugin", "B Plugin").latest_refresh_time == "2026-05-26T07:00:00+00:00"


def test_submit_manual_update_returns_job_without_waiting_for_inflight_refresh(monkeypatch):
    calls = []
    tmp_path = make_test_dir("async-manual-after-inflight")
    playlist = Playlist(
        "DailyDoseOfDay",
        "00:00",
        "24:00",
        plugins=[
            {
                "plugin_id": "live_radar",
                "name": "LiveRadar",
                "plugin_settings": {"id": "live_radar"},
                "refresh": {"interval": 999999999},
                "latest_refresh_time": "2999-01-01T00:00:00+00:00",
            },
        ],
    )
    plugin_instance = playlist.find_plugin("live_radar", "LiveRadar")
    Image.new("RGB", (1, 1), "black").save(tmp_path / plugin_instance.get_image_path())

    device_config = ThreadedDeviceConfig(tmp_path, playlist)
    display_manager = BlockingDisplayManager()
    task = RefreshTask(device_config, display_manager=display_manager)
    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda config: CapturePlugin(calls),
    )

    task.start()
    try:
        assert display_manager.first_display_started.wait(timeout=1)

        started = time.monotonic()
        job = task.submit_manual_update(PlaylistRefresh(playlist, plugin_instance, force=True))
        elapsed = time.monotonic() - started

        assert elapsed < 0.2
        assert job["status"] == "queued"
        assert job["plugin_id"] == "live_radar"
        assert task.get_manual_update_job(job["id"])["status"] == "queued"

        display_manager.release_first_display.set()
        for _ in range(30):
            latest_job = task.get_manual_update_job(job["id"])
            if latest_job and latest_job["status"] == "completed":
                break
            time.sleep(0.05)

        latest_job = task.get_manual_update_job(job["id"])
        assert latest_job["status"] == "completed"
        assert {"id": "live_radar", "forceRefresh": True, "force_refresh": True, "_inkypiDisplayRender": True} in calls
    finally:
        display_manager.release_first_display.set()
        task.stop()


class RuntimeClock:
    def __init__(self, monotonic=0.0, wall=1000.0):
        self.monotonic_value = float(monotonic)
        self.wall_value = float(wall)

    def monotonic(self):
        return self.monotonic_value

    def wall_time(self):
        return self.wall_value

    def advance(self, seconds):
        self.monotonic_value += seconds
        self.wall_value += seconds


class RuntimeDeviceConfig(FakeDeviceConfig):
    def __init__(self, plugin_image_dir, playlists=(), refresh_info=None):
        super().__init__(plugin_image_dir)
        self.playlist_manager = PlaylistManager(list(playlists))
        self.refresh_info = refresh_info or RefreshInfo(
            refresh_time="2999-01-01T00:00:00+00:00",
            image_hash="current",
        )

    def get_playlist_manager(self):
        return self.playlist_manager

    def get_refresh_info(self):
        return self.refresh_info

    def get_resolution(self):
        return (32, 16)


class RecordingDisplayManager:
    def __init__(self):
        self.calls = []

    def display_image(self, image, image_settings=None):
        self.calls.append((image.copy(), list(image_settings or [])))


class TransactionRecordingDisplayManager:
    def __init__(self):
        self.calls = []
        self.bound_runtime_state = None
        self.recovery_context = None

    def bind_runtime_state(self, runtime_state):
        self.bound_runtime_state = runtime_state
        return object()

    def recover_display(self, *, task_context):
        self.recovery_context = task_context
        return None

    def display_image(
        self,
        image,
        image_settings=(),
        *,
        task_context=None,
        logical_target=None,
        instance_revision=None,
    ):
        self.calls.append(
            {
                "image": image.copy(),
                "image_settings": tuple(image_settings),
                "task_context": task_context,
                "logical_target": dict(logical_target or {}),
                "instance_revision": instance_revision,
            }
        )


class BlockingRuntimePlugin:
    def __init__(self, render_started, allow_render, calls=None, fail_first=False):
        self.render_started = render_started
        self.allow_render = allow_render
        self.calls = [] if calls is None else calls
        self.fail_first = fail_first
        self.config = {}

    def generate_image(self, settings, device_config):
        self.calls.append(dict(settings))
        self.render_started.set()
        assert self.allow_render.wait(1.0)
        if self.fail_first and len(self.calls) == 1:
            raise RuntimeError("theme render failed")
        return Image.new("RGB", (2, 1), "white")


class FalseyRefreshQueue(RefreshQueue):
    def __bool__(self):
        return False


def _runtime_playlist(*plugins, name="DailyDoseOfDay", start="00:00", end="24:00"):
    return Playlist(name, start, end, plugins=list(plugins))


def _runtime_plugin_data(
    plugin_id="runtime_plugin",
    name="Runtime Plugin",
    *,
    latest_refresh_time="2999-01-01T00:00:00+00:00",
    interval=3600,
):
    data = {
        "plugin_id": plugin_id,
        "name": name,
        "plugin_settings": {"id": plugin_id},
        "refresh": {"interval": interval},
    }
    if latest_refresh_time is not None:
        data["latest_refresh_time"] = latest_refresh_time
    return data


def _make_runtime_task(tmp_path, *, playlists=(), clock=None, cycle_seconds=300):
    clock = clock or RuntimeClock()
    device_config = RuntimeDeviceConfig(tmp_path, playlists)
    device_config.config["plugin_cycle_interval_seconds"] = cycle_seconds
    task = RefreshTask(
        device_config,
        RecordingDisplayManager(),
        clock=clock.monotonic,
        wall_clock=clock.wall_time,
    )
    return task, device_config, clock


def _write_runtime_cache(task, instance, image=None):
    """Seed the UUID/revision cache used by the production command worker."""
    snapshot = instance.snapshot() if hasattr(instance, "snapshot") else instance
    cache_path = Path(task.cache_path_for_snapshot(snapshot))
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    (image or Image.new("RGB", (1, 1), "black")).save(cache_path)
    return cache_path


def test_refresh_task_binds_shared_runtime_state_and_recovers_display_on_start():
    tmp_path = make_test_dir("runtime-display-recovery")
    manager = TransactionRecordingDisplayManager()
    task = RefreshTask(RuntimeDeviceConfig(tmp_path), manager)

    assert manager.bound_runtime_state is task.runtime_state

    task.start()
    try:
        assert task.wait_until_waiting(timeout=1.0)
        assert isinstance(manager.recovery_context, TaskContext)
    finally:
        task.stop(join_timeout=1.0)


def test_playlist_display_commit_passes_target_revision_and_task_context(monkeypatch):
    tmp_path = make_test_dir("runtime-display-transaction-metadata")
    playlist = _runtime_playlist(
        _runtime_plugin_data("transactional", "Transactional Plugin")
    )
    manager = TransactionRecordingDisplayManager()
    device_config = RuntimeDeviceConfig(tmp_path, [playlist])
    task = RefreshTask(device_config, manager)
    instance = playlist.plugins[0]
    _write_runtime_cache(task, instance, Image.new("RGB", (2, 1), "white"))
    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda _config: FakePlugin([]),
    )

    task.start()
    try:
        assert task.wait_until_waiting(timeout=1.0)
        job = task.submit_playlist_display(
            instance.instance_uuid,
            force=False,
            display_cached_only=True,
            expected_playlist_name=playlist.name,
            expected_generation=instance.structural_generation,
            expected_settings_revision=instance.settings_revision,
        )
        result = task.wait_for_job(job["id"], timeout=1.0)

        assert result["status"] == "completed"
        assert len(manager.calls) == 1
        call = manager.calls[0]
        assert isinstance(call["task_context"], TaskContext)
        assert call["logical_target"] == {
            "kind": "playlist",
            "playlist": playlist.name,
            "plugin_id": instance.plugin_id,
            "plugin_instance": instance.name,
            "instance_uuid": instance.instance_uuid,
        }
        assert call["instance_revision"] == (
            instance.structural_generation,
            instance.settings_revision,
        )
    finally:
        task.stop(join_timeout=1.0)


def _wait_for_legacy_job(task, job_id, timeout=1.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = task.get_manual_update_job(job_id)
        if job and job["status"] not in {"queued", "running"}:
            return job
        time.sleep(0.01)
    return task.get_manual_update_job(job_id)


def test_runtime_render_skips_live_hook_without_manifest_capability(monkeypatch):
    tmp_path = make_test_dir("runtime-manifest-live-render-gate")
    playlist = _runtime_playlist(
        _runtime_plugin_data("ordinary_plugin", "Ordinary Plugin")
    )
    task, device_config, _clock = _make_runtime_task(
        tmp_path,
        playlists=[playlist],
    )
    instance = playlist.plugins[0]
    _write_runtime_cache(task, instance)
    manifest = PluginManifest(
        schema_version=2,
        id="ordinary_plugin",
        class_name="OrdinaryPlugin",
        display_name="Ordinary Plugin",
        refresh_on_display=False,
        capabilities=PluginCapabilities(supports_live_refresh=False),
        raw={},
    )
    device_config.get_plugin = lambda plugin_id: {
        "id": plugin_id,
        "_manifest": manifest,
    }
    hook_calls = []
    plugin = FakePlugin(
        [],
        live_state=lambda *_args: hook_calls.append("called")
        or {"active": True, "interval_seconds": 1},
    )
    monkeypatch.setattr("src.refresh_task.get_plugin_instance", lambda _config: plugin)

    task.start()
    try:
        assert task.wait_until_waiting(timeout=1.0)
        job = task.submit_playlist_display(
            instance.instance_uuid,
            force=False,
            display_cached_only=True,
            expected_playlist_name=playlist.name,
            expected_generation=instance.structural_generation,
            expected_settings_revision=instance.settings_revision,
        )
        result = task.wait_for_job(job["id"], timeout=1.0)

        assert result["status"] == "completed"
        assert hook_calls == []
    finally:
        task.stop(join_timeout=1.0)


def test_manual_playlist_display_can_target_inactive_playlist_with_exact_cas(
    monkeypatch,
):
    tmp_path = make_test_dir("manual-inactive-playlist-display")
    active = _runtime_playlist(
        _runtime_plugin_data("active", "Active"),
        name="Active",
    )
    inactive = _runtime_playlist(
        _runtime_plugin_data("inactive", "Inactive"),
        name="Inactive",
    )
    task, device_config, _clock = _make_runtime_task(
        tmp_path,
        playlists=[active, inactive],
    )
    target = device_config.playlist_manager.resolve_plugin_instance_snapshot(
        "Inactive",
        "inactive",
        "Inactive",
    ).instance
    calls = []
    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda _config: CapturePlugin(calls),
    )

    task.start()
    try:
        assert task.wait_until_waiting(timeout=1.0)
        job = task.submit_playlist_display(
            target.instance_uuid,
            force=True,
            display_cached_only=False,
            expected_playlist_name="Inactive",
            expected_generation=target.structural_generation,
            expected_settings_revision=target.settings_revision,
            require_active=False,
        )
        result = task.wait_for_job(job["id"], timeout=1.0)

        assert result["status"] == "completed"
        assert calls[-1] == {
            "id": "inactive",
            "forceRefresh": True,
            "force_refresh": True,
            "_inkypiDisplayRender": True,
        }
        assert device_config.refresh_info.playlist == "Inactive"
    finally:
        task.stop(join_timeout=1.0)


def test_playlist_refresh_rerenders_non_sports_live_due_before_ordinary_interval():
    calls = []
    tmp_path = make_test_dir("scheduled-non-sports-live-refresh")
    device_config = FakeDeviceConfig(tmp_path)
    device_config.config["display_refresh_resource_guard_enabled"] = False
    playlist = _runtime_playlist(
        _runtime_plugin_data(
            "live_plugin",
            "Live Plugin",
            latest_refresh_time="2026-05-26T07:00:00+00:00",
            interval=3600,
        )
    )
    plugin_instance = playlist.plugins[0]
    Image.new("RGB", (2, 1), "black").save(tmp_path / plugin_instance.get_image_path())

    image = PlaylistRefresh(playlist, plugin_instance, display_cached_only=True).execute(
        FakePlugin(calls, live_state={"active": True, "interval_seconds": 60}),
        device_config,
        datetime(2026, 5, 26, 7, 2, tzinfo=timezone.utc),
    )

    assert calls == ["live_plugin"]
    assert image.getpixel((0, 0)) == (255, 255, 255)


def test_background_cache_rechecks_pressure_before_second_candidate(monkeypatch):
    calls = []
    tmp_path = make_test_dir("background-pressure-recheck")
    device_config = FakeDeviceConfig(tmp_path)
    task = RefreshTask(device_config, display_manager=None)
    playlist = _runtime_playlist(
        _runtime_plugin_data("a_plugin", "A Plugin", latest_refresh_time="2026-05-26T07:00:00+00:00", interval=60),
        _runtime_plugin_data("b_plugin", "B Plugin", latest_refresh_time="2026-05-26T07:00:00+00:00", interval=60),
    )
    for instance in playlist.plugins:
        Image.new("RGB", (1, 1), "black").save(tmp_path / instance.get_image_path())
    pressure = iter([False, True])
    monkeypatch.setattr(task, "_cache_refresh_under_resource_pressure", lambda: next(pressure))
    monkeypatch.setattr("src.refresh_task.get_plugin_instance", lambda config: FakePlugin(calls))

    task._refresh_due_plugin_instances(
        playlist,
        datetime(2026, 5, 26, 7, 2, tzinfo=timezone.utc),
    )

    assert calls == ["a_plugin"]
    assert playlist.plugins[0].latest_refresh_time == "2026-05-26T07:02:00+00:00"
    assert playlist.plugins[1].latest_refresh_time == "2026-05-26T07:00:00+00:00"


def test_default_display_pressure_trips_below_150_mib_with_safe_swap(monkeypatch):
    calls = []
    tmp_path = make_test_dir("display-pressure-default-memory")
    device_config = FakeDeviceConfig(tmp_path)
    playlist = _runtime_playlist(
        _runtime_plugin_data("live_plugin", "Live Plugin", latest_refresh_time="2026-05-26T07:00:00+00:00")
    )
    instance = playlist.plugins[0]
    Image.new("RGB", (2, 1), "black").save(tmp_path / instance.get_image_path())
    memory = type("Memory", (), {"available": 149 * 1024 * 1024})()
    swap = type("Swap", (), {"percent": 0.0})()
    monkeypatch.setattr("src.refresh_task.psutil.virtual_memory", lambda: memory)
    monkeypatch.setattr("src.refresh_task.psutil.swap_memory", lambda: swap)

    image = PlaylistRefresh(playlist, instance, display_cached_only=True).execute(
        FakePlugin(calls, live_state={"active": True, "interval_seconds": 60}),
        device_config,
        datetime(2026, 5, 26, 7, 2, tzinfo=timezone.utc),
    )

    assert calls == []
    assert image.getpixel((0, 0)) == (0, 0, 0)


def test_default_display_pressure_trips_at_30_percent_swap_with_safe_memory(monkeypatch):
    calls = []
    tmp_path = make_test_dir("display-pressure-default-swap")
    device_config = FakeDeviceConfig(tmp_path)
    playlist = _runtime_playlist(
        _runtime_plugin_data("live_plugin", "Live Plugin", latest_refresh_time="2026-05-26T07:00:00+00:00")
    )
    instance = playlist.plugins[0]
    Image.new("RGB", (2, 1), "black").save(tmp_path / instance.get_image_path())
    memory = type("Memory", (), {"available": 512 * 1024 * 1024})()
    swap = type("Swap", (), {"percent": 30.0})()
    monkeypatch.setattr("src.refresh_task.psutil.virtual_memory", lambda: memory)
    monkeypatch.setattr("src.refresh_task.psutil.swap_memory", lambda: swap)

    image = PlaylistRefresh(playlist, instance, display_cached_only=True).execute(
        FakePlugin(calls, live_state={"active": True, "interval_seconds": 60}),
        device_config,
        datetime(2026, 5, 26, 7, 2, tzinfo=timezone.utc),
    )

    assert calls == []
    assert image.getpixel((0, 0)) == (0, 0, 0)


def test_theme_refresh_failure_default_retry_cooldown_is_600_seconds():
    tmp_path = make_test_dir("theme-default-cooldown")
    device_config = FakeDeviceConfig(tmp_path)
    device_config.config["active_theme"] = "day"
    task = RefreshTask(device_config, display_manager=None)
    failed_at = datetime(2026, 5, 26, 22, 8, tzinfo=timezone.utc)
    theme = {"mode": "night", "source": "weather", "reason": "sunset"}

    task._mark_theme_refresh_failed(theme, failed_at, RuntimeError("render failed"))

    failure = device_config.config["active_theme_refresh_failure"]
    retry_after = datetime.fromisoformat(failure["retry_after"])
    assert (retry_after - failed_at).total_seconds() == 600
    assert not task._has_theme_changed(theme, failed_at + timedelta(seconds=599))
    assert task._has_theme_changed(theme, failed_at + timedelta(seconds=600))


@pytest.mark.parametrize("cache_state", ["missing", "corrupt"])
def test_display_pressure_missing_or_corrupt_cache_uses_placeholder_without_render(monkeypatch, cache_state):
    calls = []
    tmp_path = make_test_dir(f"display-pressure-{cache_state}-cache")
    device_config = FakeDeviceConfig(tmp_path)
    playlist = _runtime_playlist(
        _runtime_plugin_data("live_plugin", "Live Plugin", latest_refresh_time="2026-05-26T07:00:00+00:00")
    )
    instance = playlist.plugins[0]
    cache_path = tmp_path / instance.get_image_path()
    if cache_state == "corrupt":
        cache_path.write_bytes(b"not an image")
    memory = type("Memory", (), {"available": 149 * 1024 * 1024})()
    swap = type("Swap", (), {"percent": 0.0})()
    monkeypatch.setattr("src.refresh_task.psutil.virtual_memory", lambda: memory)
    monkeypatch.setattr("src.refresh_task.psutil.swap_memory", lambda: swap)

    image = PlaylistRefresh(playlist, instance, display_cached_only=True).execute(
        FakePlugin(calls, live_state={"active": True, "interval_seconds": 60}),
        device_config,
        datetime(2026, 5, 26, 7, 2, tzinfo=timezone.utc),
    )

    assert calls == []
    assert image.size == (800, 480)


def test_overdue_empty_playlist_advances_monotonic_attempt_deadline():
    tmp_path = make_test_dir("runtime-empty-deadline")
    task, _device_config, _clock = _make_runtime_task(tmp_path, playlists=[])

    task._run_one_iteration_for_test()
    first = task.scheduler_snapshot().next_attempt_monotonic
    task._run_one_iteration_for_test()

    assert first >= 30.0
    assert task.attempt_count == 1


def test_memory_watchdog_error_advances_deadline_without_killing_scheduler(monkeypatch):
    tmp_path = make_test_dir("runtime-watchdog-deadline")
    task, _device_config, _clock = _make_runtime_task(tmp_path, playlists=[])
    monkeypatch.setattr(
        task,
        "_memory_watchdog_should_restart",
        lambda: (_ for _ in ()).throw(RuntimeError("watchdog")),
    )

    task._run_one_iteration_for_test()

    assert task.scheduler_snapshot().next_attempt_monotonic >= 30.0
    assert task.attempt_count == 1


def test_start_registers_one_non_daemon_worker():
    tmp_path = make_test_dir("runtime-single-worker")
    task, _device_config, _clock = _make_runtime_task(tmp_path, playlists=[], cycle_seconds=300)

    task.start()
    try:
        assert task.wait_until_waiting(timeout=1.0)
        first_thread = task.thread
        task.start()

        assert task.thread is first_thread
        assert task.thread.daemon is False
    finally:
        task.stop(join_timeout=1.0)


def test_stop_wakes_waiting_refresh_thread_without_cycle_delay():
    tmp_path = make_test_dir("runtime-stop-wake")
    task, _device_config, _clock = _make_runtime_task(tmp_path, playlists=[], cycle_seconds=300)
    task.start()
    assert task.wait_until_waiting(timeout=1.0)

    assert task.stop(join_timeout=1.0) is True
    assert not task.thread.is_alive()
    assert task.lifecycle.state is LifecycleState.STOPPED


def test_stop_serializes_with_the_start_critical_section():
    tmp_path = make_test_dir("runtime-start-stop-serialization")
    task, _device_config, _clock = _make_runtime_task(tmp_path, playlists=[])
    stop_attempted = threading.Event()
    results = []

    def stop_task():
        stop_attempted.set()
        results.append(task.stop(join_timeout=1.0))

    task._start_lock.acquire()
    stop_thread = threading.Thread(target=stop_task)
    try:
        stop_thread.start()
        assert stop_attempted.wait(1.0)
        assert not task.stop_event.wait(0.1)
    finally:
        task._start_lock.release()
        stop_thread.join(timeout=1.0)

    assert not stop_thread.is_alive()
    assert results == [True]
    assert task.lifecycle.state is LifecycleState.STOPPED


def test_worker_exit_clears_running_state_when_queue_closes():
    tmp_path = make_test_dir("runtime-worker-running-state")
    task, _device_config, _clock = _make_runtime_task(tmp_path, playlists=[], cycle_seconds=300)
    task.start()
    try:
        assert task.wait_until_waiting(timeout=1.0)
        task.refresh_queue.begin_quiesce()
        task.thread.join(timeout=1.0)

        assert not task.thread.is_alive()
        assert task.running is False
    finally:
        task.stop(join_timeout=1.0)


def test_constructor_adopts_falsey_injected_collaborators_by_identity():
    tmp_path = make_test_dir("runtime-injected-collaborators")
    device_config = RuntimeDeviceConfig(tmp_path)
    clock = RuntimeClock()
    queue = FalseyRefreshQueue(clock=clock.monotonic, wall_clock=clock.wall_time)
    stop_event = threading.Event()
    lifecycle = LifecycleController(
        stop_event,
        queue,
        clock=clock.monotonic,
        wall_clock=clock.wall_time,
    )
    arbiter = RenderArbiter()
    retries = RetryRegistry(jitter=lambda value: value)
    scheduler = SchedulerState(retries, clock=clock.monotonic, wall_clock=clock.wall_time)

    task = RefreshTask(
        device_config,
        RecordingDisplayManager(),
        clock=clock.monotonic,
        wall_clock=clock.wall_time,
        stop_event=stop_event,
        refresh_queue=queue,
        render_arbiter=arbiter,
        lifecycle=lifecycle,
        retry_registry=retries,
        scheduler_state=scheduler,
    )

    assert task.stop_event is stop_event
    assert task.refresh_queue is queue
    assert task.render_arbiter is arbiter
    assert task.lifecycle is lifecycle
    assert task.retry_registry is retries
    assert task.scheduler_state is scheduler


def test_constructor_rejects_lifecycle_with_different_queue_or_event():
    tmp_path = make_test_dir("runtime-inconsistent-collaborators")
    device_config = RuntimeDeviceConfig(tmp_path)
    lifecycle_queue = RefreshQueue()
    lifecycle_event = threading.Event()
    lifecycle = LifecycleController(lifecycle_event, lifecycle_queue)

    with pytest.raises(ValueError, match="lifecycle"):
        RefreshTask(
            device_config,
            RecordingDisplayManager(),
            stop_event=threading.Event(),
            refresh_queue=RefreshQueue(),
            lifecycle=lifecycle,
        )


def test_direct_queue_submission_wakes_idle_worker(monkeypatch):
    tmp_path = make_test_dir("runtime-direct-queue-wake")
    task, _device_config, clock = _make_runtime_task(tmp_path, playlists=[], cycle_seconds=300)
    calls = []
    monkeypatch.setattr("src.refresh_task.get_plugin_instance", lambda config: CapturePlugin(calls))
    task.start()
    try:
        assert task.wait_until_waiting(timeout=1.0)
        command = RefreshCommand.create(
            kind=CommandKind.DISPLAY,
            source=CommandSource.MANUAL,
            plugin_id="direct_plugin",
            payload={"refresh_type": "Manual Update", "settings": {"id": "direct"}},
            now_monotonic=clock.monotonic(),
            deadline_monotonic=clock.monotonic() + 60,
            force=True,
            priority=100,
        )

        job = task.refresh_queue.submit(command)
        result = task.wait_for_job(job.id, timeout=1.0)

        assert result["status"] == "completed"
        assert calls == [{"id": "direct", "forceRefresh": True, "force_refresh": True, "_inkypiDisplayRender": True}]
    finally:
        task.stop(join_timeout=1.0)


def test_manual_worker_preserves_plugin_image_settings(monkeypatch):
    tmp_path = make_test_dir("runtime-manual-image-settings")
    task, _device_config, _clock = _make_runtime_task(tmp_path, playlists=[])
    plugin = CapturePlugin([])
    plugin.config = {"image_settings": ["rotate-180"]}
    monkeypatch.setattr("src.refresh_task.get_plugin_instance", lambda config: plugin)
    task.start()
    try:
        job = task.submit_manual_update(ManualRefresh("manual", {"id": "manual"}))
        result = task.wait_for_job(job["id"], timeout=1.0)

        assert result["status"] == "completed"
        assert task.display_manager.calls[0][1] == ["rotate-180"]
    finally:
        task.stop(join_timeout=1.0)


def test_manual_wait_reports_pruned_terminal_result_without_timing_out(monkeypatch):
    tmp_path = make_test_dir("runtime-manual-pruned-result")
    task, device_config, _clock = _make_runtime_task(tmp_path, playlists=[])
    task.refresh_queue.terminal_limit = 0
    device_config.config["manual_update_timeout_seconds"] = 0.1
    monkeypatch.setattr("src.refresh_task.get_plugin_instance", lambda config: CapturePlugin([]))
    task.start()
    try:
        with pytest.raises(RuntimeError, match="no longer available"):
            task.manual_update(ManualRefresh("manual", {"id": "manual"}))
    finally:
        task.stop(join_timeout=1.0)


def test_signal_config_change_wakes_and_reprobes_scheduled_selection(monkeypatch):
    tmp_path = make_test_dir("runtime-config-wake")
    empty_playlist = _runtime_playlist()
    task, device_config, _clock = _make_runtime_task(
        tmp_path,
        playlists=[empty_playlist],
        cycle_seconds=300,
    )
    device_config.refresh_info = RefreshInfo(refresh_time="2000-01-01T00:00:00+00:00", image_hash="old")
    calls = []
    monkeypatch.setattr("src.refresh_task.get_plugin_instance", lambda config: CapturePlugin(calls))
    task.start()
    try:
        assert task.wait_until_waiting(timeout=1.0)
        assert device_config.playlist_manager.add_plugin_to_playlist(
            "DailyDoseOfDay",
            _runtime_plugin_data("new_plugin", "New Plugin", latest_refresh_time=None),
        )

        task.signal_config_change()

        deadline = time.monotonic() + 1.0
        while not task.display_manager.calls and time.monotonic() < deadline:
            time.sleep(0.01)
        assert task.display_manager.calls
        assert calls
    finally:
        task.stop(join_timeout=1.0)


def _make_blocked_playlist_task(monkeypatch, name):
    tmp_path = make_test_dir(name)
    playlist = _runtime_playlist(_runtime_plugin_data(latest_refresh_time="2999-01-01T00:00:00+00:00"))
    task, device_config, _clock = _make_runtime_task(tmp_path, playlists=[playlist], cycle_seconds=300)
    device_config.config["theme_mode"] = "day"
    device_config.config["active_theme"] = "day"
    _write_runtime_cache(task, playlist.plugins[0])
    render_started = threading.Event()
    allow_render = threading.Event()
    plugin = BlockingRuntimePlugin(render_started, allow_render)
    monkeypatch.setattr("src.refresh_task.get_plugin_instance", lambda config: plugin)
    task.start()
    assert task.wait_until_waiting(timeout=1.0)
    return task, device_config.playlist_manager, render_started, allow_render, plugin, tmp_path


def test_deleted_instance_result_is_discarded_after_render(monkeypatch):
    task, manager, render_started, allow_render, _plugin, tmp_path = _make_blocked_playlist_task(
        monkeypatch,
        "runtime-stale-delete",
    )
    try:
        instance_uuid = manager.first_instance_uuid()
        cache_path = Path(task.cache_path_for_snapshot(
            manager.snapshot_instance(instance_uuid)
        ))
        original_cache = cache_path.read_bytes()
        job = task.submit_playlist_display(instance_uuid)
        assert render_started.wait(1.0)

        manager.delete_plugin_instance(instance_uuid)
        allow_render.set()
        result = task.wait_for_job(job["id"], timeout=1.0)

        assert result["status"] == "canceled"
        assert result["error_code"] == "stale_selection"
        assert not task.display_manager.calls
        assert task.device_config.write_count == 0
        assert cache_path.read_bytes() == original_cache
    finally:
        allow_render.set()
        task.stop(join_timeout=1.0)


def test_settings_revision_changed_during_render_discards_all_side_effects(monkeypatch):
    task, manager, render_started, allow_render, _plugin, tmp_path = _make_blocked_playlist_task(
        monkeypatch,
        "runtime-stale-settings",
    )
    try:
        instance_uuid = manager.first_instance_uuid()
        before = manager.snapshot_instance(instance_uuid)
        cache_path = Path(task.cache_path_for_snapshot(before))
        original_cache = cache_path.read_bytes()
        job = task.submit_playlist_display(instance_uuid)
        assert render_started.wait(1.0)

        manager.update_plugin_instance(
            instance_uuid,
            settings={"id": "changed"},
            expected_generation=before.structural_generation,
            expected_settings_revision=before.settings_revision,
        )
        allow_render.set()
        result = task.wait_for_job(job["id"], timeout=1.0)

        assert result["status"] == "canceled"
        assert not task.display_manager.calls
        assert task.device_config.write_count == 0
        assert cache_path.read_bytes() == original_cache
        assert manager.snapshot_instance(instance_uuid).latest_refresh_time == before.latest_refresh_time
    finally:
        allow_render.set()
        task.stop(join_timeout=1.0)


def test_render_failure_after_instance_deletion_is_stale_without_theme_write(monkeypatch):
    task, manager, render_started, allow_render, plugin, _tmp_path = _make_blocked_playlist_task(
        monkeypatch,
        "runtime-stale-failure",
    )
    plugin.fail_first = True
    try:
        instance_uuid = manager.first_instance_uuid()
        instance = manager.snapshot_instance(instance_uuid)
        command = task._playlist_command(
            "DailyDoseOfDay",
            instance,
            source=CommandSource.SCHEDULER,
            force=True,
            display_cached_only=False,
            theme_context={"mode": "night", "source": "weather", "reason": "sunset"},
        )
        submitted = task.refresh_queue.submit(command)
        assert render_started.wait(1.0)

        assert manager.delete_plugin_instance(instance_uuid)
        allow_render.set()
        result = task.wait_for_job(submitted.id, timeout=1.0)

        assert result["status"] == "canceled"
        assert result["error_code"] == "stale_selection"
        assert "active_theme_refresh_failure" not in task.device_config.config
        assert task.device_config.write_count == 0
    finally:
        allow_render.set()
        task.stop(join_timeout=1.0)


def test_theme_render_exception_in_run_records_cooldown_then_success_clears(monkeypatch):
    tmp_path = make_test_dir("runtime-theme-run-cooldown")
    playlist = _runtime_playlist(_runtime_plugin_data(latest_refresh_time="2999-01-01T00:00:00+00:00"))
    clock = RuntimeClock()
    task, device_config, _clock = _make_runtime_task(tmp_path, playlists=[playlist], clock=clock)
    device_config.config["active_theme"] = "day"
    current_dt = [datetime(2026, 5, 26, 22, 8, tzinfo=timezone.utc)]
    monkeypatch.setattr(task, "_get_current_datetime", lambda: current_dt[0])
    monkeypatch.setattr(
        "src.refresh_task.get_theme_context",
        lambda config, now: {"mode": "night", "source": "weather", "reason": "sunset"},
    )
    render_started = threading.Event()
    allow_render = threading.Event()
    allow_render.set()
    plugin = BlockingRuntimePlugin(render_started, allow_render, fail_first=True)
    monkeypatch.setattr("src.refresh_task.get_plugin_instance", lambda config: plugin)

    task._run_one_iteration_for_test()
    failure = device_config.config["active_theme_refresh_failure"]
    assert datetime.fromisoformat(failure["retry_after"]) - current_dt[0] == timedelta(seconds=600)

    clock.advance(31)
    current_dt[0] += timedelta(seconds=31)
    task._run_one_iteration_for_test()
    assert len(plugin.calls) == 1

    clock.advance(570)
    current_dt[0] += timedelta(seconds=570)
    task._run_one_iteration_for_test()

    assert len(plugin.calls) == 2
    assert device_config.config["active_theme"] == "night"
    assert device_config.config["active_theme_refresh_failure"] is None


def test_shared_plugin_singleton_never_executes_concurrently(monkeypatch):
    tmp_path = make_test_dir("runtime-singleton")
    device_config = RuntimeDeviceConfig(tmp_path)
    device_config.config["plugin_cycle_interval_seconds"] = 300
    task = RefreshTask(device_config, RecordingDisplayManager())
    entered = threading.Event()
    release = threading.Event()
    guard = threading.Lock()
    active = 0
    maximum = 0

    class SingletonPlugin:
        config = {}

        def generate_image(self, settings, config):
            nonlocal active, maximum
            with guard:
                active += 1
                maximum = max(maximum, active)
            entered.set()
            if settings["id"] == "first":
                assert release.wait(1.0)
            with guard:
                active -= 1
            return Image.new("RGB", (1, 1), "white")

    plugin = SingletonPlugin()
    monkeypatch.setattr("src.refresh_task.get_plugin_instance", lambda config: plugin)
    task.start()
    try:
        first = task.submit_manual_update(ManualRefresh("singleton", {"id": "first"}))
        second = task.submit_manual_update(ManualRefresh("singleton", {"id": "second"}))
        assert entered.wait(1.0)
        assert maximum == 1
        release.set()

        assert _wait_for_legacy_job(task, first["id"])["status"] == "completed"
        assert _wait_for_legacy_job(task, second["id"])["status"] == "completed"
        assert maximum == 1
    finally:
        release.set()
        task.stop()


def test_bounded_stop_marks_forced_exit_when_render_does_not_cooperate(monkeypatch):
    tmp_path = make_test_dir("runtime-bounded-stop")
    task, _device_config, _clock = _make_runtime_task(tmp_path, playlists=[])
    render_started = threading.Event()
    allow_render = threading.Event()
    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda config: BlockingRuntimePlugin(render_started, allow_render),
    )
    task.start()
    try:
        task.submit_manual_update(ManualRefresh("blocked", {"id": "blocked"}))
        assert render_started.wait(1.0)

        assert task.stop(join_timeout=0.01) is False
        assert task.lifecycle.state is LifecycleState.FORCED_EXIT
    finally:
        allow_render.set()
        task.thread.join(timeout=1.0)


def test_each_visible_playlist_side_effect_has_fresh_validation(monkeypatch):
    tmp_path = make_test_dir("runtime-validation-before-side-effects")
    playlist = _runtime_playlist(_runtime_plugin_data(latest_refresh_time="2999-01-01T00:00:00+00:00"))
    task, device_config, _clock = _make_runtime_task(tmp_path, playlists=[playlist])
    device_config.config.update({"theme_mode": "day", "active_theme": "day"})
    cache_path = _write_runtime_cache(task, playlist.plugins[0])
    events = []
    manager = device_config.playlist_manager
    original_validate = manager.validate_selection
    original_record = task.runtime_state.record_success
    original_replace = __import__("os").replace
    inner_arbiter = task.render_arbiter
    lease_depth = 0

    class ObservingArbiter:
        @contextmanager
        def lease(self, plugin_id, context):
            nonlocal lease_depth
            with inner_arbiter.lease(plugin_id, context):
                lease_depth += 1
                try:
                    yield
                finally:
                    lease_depth -= 1

    task.render_arbiter = ObservingArbiter()

    def validate(*args, **kwargs):
        events.append("validate")
        return original_validate(*args, **kwargs)

    def record(*args, **kwargs):
        events.append("timestamp")
        return original_record(*args, **kwargs)

    def replace(source, destination):
        if Path(destination) == cache_path:
            assert lease_depth == 1
            events.append("cache")
        return original_replace(source, destination)

    manager.validate_selection = validate
    task.runtime_state.record_success = record
    task.display_manager.display_image = lambda image, image_settings=None: events.append("display")
    device_config.write_config = lambda: events.append("config")
    monkeypatch.setattr("src.refresh_task.os.replace", replace)
    monkeypatch.setattr("src.refresh_task.get_plugin_instance", lambda config: CapturePlugin([]))

    task.start()
    try:
        assert task.wait_until_waiting(timeout=1.0)
        job = task.submit_playlist_display(manager.first_instance_uuid())
        result = task.wait_for_job(job["id"], timeout=1.0)

        assert result["status"] == "completed"
        for side_effect in ("cache", "display", "timestamp", "config"):
            index = events.index(side_effect)
            assert events[index - 1] == "validate"
    finally:
        task.stop(join_timeout=1.0)


def test_final_playlist_validation_failure_does_not_mutate_shared_config(monkeypatch):
    tmp_path = make_test_dir("runtime-final-config-validation")
    playlist = _runtime_playlist(
        _runtime_plugin_data(latest_refresh_time="2999-01-01T00:00:00+00:00")
    )
    task, device_config, _clock = _make_runtime_task(tmp_path, playlists=[playlist])
    device_config.config.update({"theme_mode": "day", "active_theme": "day"})
    _write_runtime_cache(task, playlist.plugins[0])
    monkeypatch.setattr("src.refresh_task.get_plugin_instance", lambda config: CapturePlugin([]))
    original_require = task._require_fresh_selection
    checks = []

    def fail_final_validation(command, context):
        checks.append(command.id)
        if len(checks) == 4:
            raise TaskCancelled("selection changed at final config check")
        return original_require(command, context)

    monkeypatch.setattr(task, "_require_fresh_selection", fail_final_validation)
    before_refresh = device_config.refresh_info.to_dict()
    instance = device_config.playlist_manager.snapshot_instance(
        device_config.playlist_manager.first_instance_uuid()
    )
    command = task._playlist_command(
        playlist.name,
        instance,
        source=CommandSource.SCHEDULER,
        force=False,
        display_cached_only=True,
        theme_context={"mode": "night", "source": "weather", "reason": "sunset"},
    )
    task.start()
    try:
        submitted = task.refresh_queue.submit(command)
        result = task.wait_for_job(submitted.id, timeout=1.0)

        assert result["status"] == "canceled"
        assert len(checks) == 4
        assert device_config.refresh_info.to_dict() == before_refresh
        assert "displayed_instance_uuid" not in device_config.config
        assert device_config.config["active_theme"] == "day"
        assert "active_theme_info" not in device_config.config
        assert device_config.write_count == 0
    finally:
        task.stop(join_timeout=1.0)


def test_final_manual_context_failure_does_not_mutate_shared_refresh_info(monkeypatch):
    tmp_path = make_test_dir("runtime-final-manual-context")
    task, device_config, clock = _make_runtime_task(tmp_path, playlists=[])
    command = RefreshCommand.create(
        kind=CommandKind.DISPLAY,
        source=CommandSource.MANUAL,
        plugin_id="manual",
        payload={"refresh_type": "Manual Update", "settings": {}},
        now_monotonic=clock.monotonic(),
        deadline_monotonic=clock.monotonic() + 60,
    )

    class CancelOnFourthCheck:
        def __init__(self):
            self.checks = 0

        def raise_if_cancelled(self):
            self.checks += 1
            if self.checks == 4:
                raise TaskCancelled("cancel at final config check")

    context = CancelOnFourthCheck()
    monkeypatch.setattr(task, "_current_task_context", lambda _command: context)
    before_refresh = device_config.refresh_info.to_dict()

    with pytest.raises(TaskCancelled, match="final config check"):
        task._commit_command_result(
            command,
            None,
            Image.new("RGB", (1, 1), "white"),
            datetime(2026, 5, 26, 7, 0, tzinfo=timezone.utc),
        )

    assert context.checks == 4
    assert device_config.refresh_info.to_dict() == before_refresh
    assert device_config.write_count == 0


def test_running_playlist_cancel_finishes_canceled_not_succeeded(monkeypatch):
    task, manager, render_started, allow_render, _plugin, _tmp_path = _make_blocked_playlist_task(
        monkeypatch,
        "runtime-running-cancel",
    )
    try:
        instance_uuid = manager.first_instance_uuid()
        job = task.submit_playlist_display(instance_uuid)
        assert render_started.wait(1.0)

        assert task.refresh_queue.cancel_instance(instance_uuid) == 1
        allow_render.set()
        result = task.wait_for_job(job["id"], timeout=1.0)

        assert result["status"] == "canceled"
        assert task.refresh_queue.get_entry(job["id"]).job.status is JobStatus.CANCELED
        assert not task.display_manager.calls
    finally:
        allow_render.set()
        task.stop(join_timeout=1.0)


def test_cancel_requested_after_execute_cannot_kill_worker_or_finish_succeeded(monkeypatch):
    tmp_path = make_test_dir("runtime-cancel-before-finish")
    playlist = _runtime_playlist(_runtime_plugin_data())
    task, device_config, _clock = _make_runtime_task(tmp_path, playlists=[playlist])
    instance = device_config.playlist_manager.snapshot_instance(
        device_config.playlist_manager.first_instance_uuid()
    )
    command = task._playlist_command(
        playlist.name,
        instance,
        source=CommandSource.MANUAL,
    )
    submitted = task.refresh_queue.submit(command)
    entry = task.refresh_queue.take(timeout=0)

    def execute_then_cancel(_command):
        assert task.refresh_queue.cancel_instance(instance.instance_uuid) == 1

    monkeypatch.setattr(task, "_execute_command", execute_then_cancel)

    task._process_queue_entry(entry)

    result = task.get_manual_update_job(submitted.id)
    assert result["status"] == "canceled"
    assert task.refresh_queue.get_entry(submitted.id).job.status is JobStatus.CANCELED


def test_running_command_deadline_finishes_abandoned(monkeypatch):
    tmp_path = make_test_dir("runtime-running-deadline")
    clock = RuntimeClock()
    task, _device_config, _clock = _make_runtime_task(tmp_path, playlists=[], clock=clock)
    render_started = threading.Event()
    allow_render = threading.Event()
    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda config: BlockingRuntimePlugin(render_started, allow_render),
    )
    task.start()
    try:
        command = RefreshCommand.create(
            kind=CommandKind.DISPLAY,
            source=CommandSource.MANUAL,
            plugin_id="deadline_plugin",
            payload={"refresh_type": "Manual Update", "settings": {"id": "deadline"}},
            now_monotonic=clock.monotonic(),
            deadline_monotonic=clock.monotonic() + 5,
            force=True,
            priority=100,
        )
        job = task.refresh_queue.submit(command)
        assert render_started.wait(1.0)

        clock.advance(5)
        allow_render.set()
        result = task.wait_for_job(job.id, timeout=1.0)

        assert result["status"] == "timed_out"
        assert task.refresh_queue.get_entry(job.id).job.status is JobStatus.ABANDONED
        assert not task.display_manager.calls
    finally:
        allow_render.set()
        task.stop(join_timeout=1.0)


def test_deadline_crossed_after_execute_is_abandoned_before_success(monkeypatch):
    tmp_path = make_test_dir("runtime-deadline-before-finish")
    clock = RuntimeClock()
    task, _device_config, _clock = _make_runtime_task(tmp_path, playlists=[], clock=clock)
    command = RefreshCommand.create(
        kind=CommandKind.DISPLAY,
        source=CommandSource.MANUAL,
        plugin_id="deadline_plugin",
        payload={"refresh_type": "Manual Update", "settings": {}},
        now_monotonic=clock.monotonic(),
        deadline_monotonic=clock.monotonic() + 5,
    )
    submitted = task.refresh_queue.submit(command)
    entry = task.refresh_queue.take(timeout=0)
    monkeypatch.setattr(task, "_execute_command", lambda _command: clock.advance(5))

    task._process_queue_entry(entry)

    result = task.get_manual_update_job(submitted.id)
    assert result["status"] == "timed_out"
    assert result["error_code"] == "deadline_expired"
    assert task.refresh_queue.get_entry(submitted.id).job.status is JobStatus.ABANDONED


def test_failure_bookkeeping_error_cannot_leave_queue_job_running(monkeypatch):
    tmp_path = make_test_dir("runtime-failure-bookkeeping")
    task, _device_config, clock = _make_runtime_task(tmp_path, playlists=[], clock=RuntimeClock())
    command = RefreshCommand.create(
        kind=CommandKind.DISPLAY,
        source=CommandSource.MANUAL,
        plugin_id="failing",
        payload={"refresh_type": "Manual Update", "settings": {}},
        now_monotonic=clock.monotonic(),
        deadline_monotonic=clock.monotonic() + 60,
    )
    submitted = task.refresh_queue.submit(command)
    entry = task.refresh_queue.take(timeout=0)
    monkeypatch.setattr(task, "_execute_command", lambda _command: (_ for _ in ()).throw(RuntimeError("render")))
    monkeypatch.setattr(task, "_record_command_failure", lambda *_args: (_ for _ in ()).throw(RuntimeError("bookkeeping")))

    task._process_queue_entry(entry)

    finished = task.refresh_queue.get_entry(submitted.id).job
    assert finished.status is JobStatus.FAILED
    assert finished.error == "render"


def test_cancel_arriving_during_failure_bookkeeping_finishes_canceled(monkeypatch):
    tmp_path = make_test_dir("runtime-failure-bookkeeping-cancel")
    playlist = _runtime_playlist(_runtime_plugin_data())
    task, device_config, _clock = _make_runtime_task(tmp_path, playlists=[playlist])
    instance = device_config.playlist_manager.snapshot_instance(
        device_config.playlist_manager.first_instance_uuid()
    )
    command = task._playlist_command(
        playlist.name,
        instance,
        source=CommandSource.SCHEDULER,
    )
    submitted = task.refresh_queue.submit(command)
    entry = task.refresh_queue.take(timeout=0)
    monkeypatch.setattr(
        task,
        "_execute_command",
        lambda _command: (_ for _ in ()).throw(RuntimeError("render")),
    )

    def cancel_during_bookkeeping(*_args):
        assert task.refresh_queue.cancel_instance(instance.instance_uuid) == 1

    monkeypatch.setattr(task, "_record_command_failure", cancel_during_bookkeeping)

    task._process_queue_entry(entry)

    finished = task.refresh_queue.get_entry(submitted.id).job
    assert finished.status is JobStatus.CANCELED
    assert finished.error_code == "task_canceled"


def test_deadline_arriving_during_failure_bookkeeping_finishes_abandoned(monkeypatch):
    tmp_path = make_test_dir("runtime-failure-bookkeeping-deadline")
    clock = RuntimeClock()
    task, _device_config, _clock = _make_runtime_task(tmp_path, playlists=[], clock=clock)
    command = RefreshCommand.create(
        kind=CommandKind.DISPLAY,
        source=CommandSource.MANUAL,
        plugin_id="deadline",
        payload={"refresh_type": "Manual Update", "settings": {}},
        now_monotonic=clock.monotonic(),
        deadline_monotonic=clock.monotonic() + 5,
    )
    submitted = task.refresh_queue.submit(command)
    entry = task.refresh_queue.take(timeout=0)
    monkeypatch.setattr(
        task,
        "_execute_command",
        lambda _command: (_ for _ in ()).throw(RuntimeError("render")),
    )
    monkeypatch.setattr(task, "_record_command_failure", lambda *_args: clock.advance(5))

    task._process_queue_entry(entry)

    finished = task.refresh_queue.get_entry(submitted.id).job
    assert finished.status is JobStatus.ABANDONED
    assert finished.error_code == "deadline_expired"


def test_manual_failure_then_success_clears_global_retry_streak(monkeypatch):
    tmp_path = make_test_dir("runtime-manual-retry-success")
    clock = RuntimeClock()
    task, _device_config, _clock = _make_runtime_task(tmp_path, playlists=[], clock=clock)

    def manual_command():
        return RefreshCommand.create(
            kind=CommandKind.DISPLAY,
            source=CommandSource.MANUAL,
            plugin_id="manual",
            payload={"refresh_type": "Manual Update", "settings": {}},
            now_monotonic=clock.monotonic(),
            deadline_monotonic=clock.monotonic() + 60,
        )

    first = task.refresh_queue.submit(manual_command())
    first_entry = task.refresh_queue.take(timeout=0)
    monkeypatch.setattr(
        task,
        "_execute_command",
        lambda _command: (_ for _ in ()).throw(RuntimeError("render")),
    )
    task._process_queue_entry(first_entry)
    assert task.refresh_queue.get_entry(first.id).job.status is JobStatus.FAILED
    assert [entry.key for entry in task.retry_registry.snapshot()] == [RetryRegistry.GLOBAL_KEY]

    second = task.refresh_queue.submit(manual_command())
    second_entry = task.refresh_queue.take(timeout=0)
    monkeypatch.setattr(task, "_execute_command", lambda _command: None)
    task._process_queue_entry(second_entry)

    assert task.refresh_queue.get_entry(second.id).job.status is JobStatus.SUCCEEDED
    assert task.retry_registry.snapshot() == ()


def test_instance_success_clears_prior_global_selection_retry(monkeypatch):
    tmp_path = make_test_dir("runtime-global-retry-success")
    playlist = _runtime_playlist(_runtime_plugin_data())
    task, device_config, _clock = _make_runtime_task(tmp_path, playlists=[playlist])
    monkeypatch.setattr(
        task,
        "_select_scheduled_command",
        lambda _current_dt: (_ for _ in ()).throw(RuntimeError("selection")),
    )

    task._run_one_iteration_for_test()
    assert [entry.key for entry in task.retry_registry.snapshot()] == [RetryRegistry.GLOBAL_KEY]

    instance = device_config.playlist_manager.snapshot_instance(
        device_config.playlist_manager.first_instance_uuid()
    )
    command = task._playlist_command(
        playlist.name,
        instance,
        source=CommandSource.BACKGROUND,
        kind=CommandKind.CACHE_REFRESH,
    )
    submitted = task.refresh_queue.submit(command)
    entry = task.refresh_queue.take(timeout=0)
    monkeypatch.setattr(task, "_execute_command", lambda _command: None)
    task._process_queue_entry(entry)

    assert task.refresh_queue.get_entry(submitted.id).job.status is JobStatus.SUCCEEDED
    assert task.retry_registry.snapshot() == ()


def test_success_bookkeeping_error_cannot_kill_worker_after_terminalization(monkeypatch):
    tmp_path = make_test_dir("runtime-success-bookkeeping")
    task, _device_config, clock = _make_runtime_task(tmp_path, playlists=[], clock=RuntimeClock())
    command = RefreshCommand.create(
        kind=CommandKind.CACHE_REFRESH,
        source=CommandSource.BACKGROUND,
        plugin_id="success",
        payload={"refresh_type": "Playlist", "settings": {}},
        now_monotonic=clock.monotonic(),
        deadline_monotonic=clock.monotonic() + 60,
    )
    submitted = task.refresh_queue.submit(command)
    entry = task.refresh_queue.take(timeout=0)
    monkeypatch.setattr(task, "_execute_command", lambda _command: None)
    monkeypatch.setattr(
        task.scheduler_state,
        "record_success",
        lambda: (_ for _ in ()).throw(RuntimeError("bookkeeping")),
    )

    task._process_queue_entry(entry)

    assert task.refresh_queue.get_entry(submitted.id).job.status is JobStatus.SUCCEEDED


def test_memory_maintenance_error_cannot_kill_worker_after_terminalization(monkeypatch):
    tmp_path = make_test_dir("runtime-maintenance-bookkeeping")
    task, _device_config, clock = _make_runtime_task(tmp_path, playlists=[], clock=RuntimeClock())
    command = RefreshCommand.create(
        kind=CommandKind.DISPLAY,
        source=CommandSource.MANUAL,
        plugin_id="success",
        payload={"refresh_type": "Manual Update", "settings": {}},
        now_monotonic=clock.monotonic(),
        deadline_monotonic=clock.monotonic() + 60,
    )
    submitted = task.refresh_queue.submit(command)
    entry = task.refresh_queue.take(timeout=0)
    monkeypatch.setattr(task, "_execute_command", lambda _command: None)
    monkeypatch.setattr(
        task,
        "_run_memory_maintenance",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("maintenance")),
    )

    task._process_queue_entry(entry)

    assert task.refresh_queue.get_entry(submitted.id).job.status is JobStatus.SUCCEEDED


@pytest.mark.parametrize(
    ("render_error", "expected_status"),
    [
        (None, JobStatus.SUCCEEDED),
        (RuntimeError("render failed"), JobStatus.FAILED),
    ],
)
def test_manual_preview_upload_is_removed_at_job_terminal(
    monkeypatch,
    render_error,
    expected_status,
):
    tmp_path = make_test_dir(f"manual-transient-{expected_status.value}")
    task, _device_config, _clock = _make_runtime_task(tmp_path, playlists=[])
    task.running = True
    upload = tmp_path / "preview.png"
    upload.write_bytes(b"preview")

    job = task.submit_manual_update(
        ManualRefresh("weather", {"imageFile": str(upload)}),
        transient_paths=(str(upload),),
    )
    entry = task.refresh_queue.take(timeout=0)

    def execute(_command):
        assert upload.read_bytes() == b"preview"
        if render_error is not None:
            raise render_error

    monkeypatch.setattr(task, "_execute_command", execute)
    task._process_queue_entry(entry)

    assert not upload.exists()
    assert task.refresh_queue.get_entry(job["id"]).job.status is expected_status


def test_blocking_manual_completion_map_holds_only_events_and_is_removed(monkeypatch):
    tmp_path = make_test_dir("runtime-completion-event-map")
    task, _device_config, _clock = _make_runtime_task(tmp_path, playlists=[])
    render_started = threading.Event()
    allow_render = threading.Event()
    errors = []
    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda config: BlockingRuntimePlugin(render_started, allow_render),
    )
    task.start()
    caller = threading.Thread(
        target=lambda: _capture_manual_error(
            task,
            ManualRefresh("blocking_manual", {"id": "blocking"}),
            errors,
        )
    )
    caller.start()
    try:
        assert render_started.wait(1.0)
        assert task._completion_events
        assert all(type(event) is threading.Event for event in task._completion_events.values())
        allow_render.set()
        caller.join(timeout=1.0)

        assert not caller.is_alive()
        assert errors == []
        assert task._completion_events == {}
    finally:
        allow_render.set()
        caller.join(timeout=1.0)
        task.stop(join_timeout=1.0)


def _capture_manual_error(task, action, errors):
    try:
        task.manual_update(action)
    except Exception as error:
        errors.append(error)


def test_background_candidates_are_individual_cache_commands(monkeypatch):
    tmp_path = make_test_dir("runtime-background-command-per-candidate")
    playlist = _runtime_playlist(
        _runtime_plugin_data("one", "One", latest_refresh_time=None),
        _runtime_plugin_data("two", "Two", latest_refresh_time=None),
    )
    task, device_config, _clock = _make_runtime_task(tmp_path, playlists=[playlist])
    device_config.config.update({"theme_mode": "day", "active_theme": "day"})
    monkeypatch.setattr("src.refresh_task.get_plugin_instance", lambda config: FakePlugin([]))

    commands = task._select_background_commands(
        datetime(2026, 5, 26, 7, 2, tzinfo=timezone.utc)
    )

    assert len(commands) == 2
    assert all(command.kind is CommandKind.CACHE_REFRESH for command in commands)
    assert {command.instance_uuid for command in commands} == {
        instance.instance_uuid for instance in playlist.plugins
    }


def test_legacy_background_trigger_executes_on_single_command_worker(monkeypatch):
    tmp_path = make_test_dir("runtime-background-single-worker")
    playlist = _runtime_playlist(_runtime_plugin_data())
    task, device_config, _clock = _make_runtime_task(tmp_path, playlists=[playlist])
    device_config.config.update({"theme_mode": "day", "active_theme": "day"})
    _write_runtime_cache(task, playlist.plugins[0])
    called = threading.Event()
    thread_ids = []

    class ThreadRecordingPlugin(FakePlugin):
        def generate_image(self, settings, config):
            thread_ids.append(threading.get_ident())
            called.set()
            return super().generate_image(settings, config)

    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda config: ThreadRecordingPlugin([]),
    )
    monkeypatch.setattr(task, "_cache_refresh_under_resource_pressure", lambda **kwargs: False)
    task.start()
    try:
        assert task.wait_until_waiting(timeout=1.0)
        task._start_due_plugin_cache_refresh(
            playlist,
            datetime(2026, 5, 26, 7, 2, tzinfo=timezone.utc),
            force=True,
        )

        assert called.wait(1.0)
        assert thread_ids == [task.thread.ident]
    finally:
        task.stop(join_timeout=1.0)


def test_cleanup_context_and_managed_cache_paths_are_bounded_public_contracts():
    tmp_path = make_test_dir("runtime-cleanup-contracts")
    clock = RuntimeClock()
    task, _device_config, _clock = _make_runtime_task(tmp_path, playlists=[], clock=clock)
    staging = tmp_path / ".refresh-staging"
    staging.mkdir()
    cache = tmp_path / ".refresh-cache"
    cache.mkdir()
    filename = task._cache_identity_filename("target", 1, 2)
    expected_stage = staging / filename
    expected_stage.write_bytes(b"stage")
    expected_cache = cache / filename
    expected_cache.write_bytes(b"cache")
    (staging / task._cache_identity_filename("other", 1, 2)).write_bytes(b"other")

    context = task.make_cleanup_context(timeout_seconds=12)
    paths = task.managed_cache_paths(
        "target",
        plugin_id="weather",
        instance_name="Main View",
    )

    assert context.cancel_event is task.stop_event
    assert context.deadline_monotonic == 12.0
    assert paths == tuple(sorted((
        str(expected_stage),
        str(expected_cache),
    )))
    task.stop(join_timeout=0)
    with pytest.raises(TaskCancelled):
        context.raise_if_cancelled()


def test_authoritative_cache_identity_changes_for_same_name_replacement():
    tmp_path = make_test_dir("runtime-versioned-cache-identity")
    first = _runtime_playlist(_runtime_plugin_data("weather", "Main"))
    second = _runtime_playlist(_runtime_plugin_data("weather", "Main"))
    task, _device_config, _clock = _make_runtime_task(tmp_path, playlists=[])

    first_path = Path(task.cache_path_for_snapshot(first.plugins[0].snapshot()))
    second_path = Path(task.cache_path_for_snapshot(second.plugins[0].snapshot()))

    assert first.plugins[0].instance_uuid != second.plugins[0].instance_uuid
    assert first_path != second_path
    assert first_path.parent.name == ".refresh-cache"
    assert second_path.parent.name == ".refresh-cache"
    assert "weather_Main.png" not in {first_path.name, second_path.name}


def test_manual_submission_propagates_queue_full_and_stopping_errors():
    tmp_path = make_test_dir("runtime-queue-errors")
    queue = RefreshQueue(capacity=1, manual_reserved=0)
    task = RefreshTask(
        RuntimeDeviceConfig(tmp_path),
        RecordingDisplayManager(),
        refresh_queue=queue,
    )
    task.running = True
    task.submit_manual_update(ManualRefresh("one", {"id": "one"}))

    with pytest.raises(QueueFullError):
        task.submit_manual_update(ManualRefresh("two", {"id": "two"}))

    task.stop(join_timeout=0)
    with pytest.raises(QueueStoppingError):
        task.submit_manual_update(ManualRefresh("three", {"id": "three"}))


def test_playlist_uuid_submission_propagates_queue_stopping_error():
    tmp_path = make_test_dir("runtime-playlist-stopping-error")
    playlist = _runtime_playlist(_runtime_plugin_data())
    task, device_config, _clock = _make_runtime_task(tmp_path, playlists=[playlist])
    device_config.config.update({"theme_mode": "day", "active_theme": "day"})
    instance_uuid = device_config.playlist_manager.first_instance_uuid()
    task.stop(join_timeout=0)

    with pytest.raises(QueueStoppingError):
        task.submit_playlist_display(instance_uuid)


def test_runtime_success_state_takes_precedence_over_legacy_refresh_time():
    tmp_path = make_test_dir("runtime-success-precedes-legacy")
    playlist = _runtime_playlist(
        _runtime_plugin_data(latest_refresh_time="2026-05-26T07:00:00+00:00")
    )
    task, _device_config, _clock = _make_runtime_task(tmp_path, playlists=[playlist])
    instance = playlist.plugins[0].snapshot()

    task.runtime_state.record_success(
        instance.instance_uuid,
        "2026-05-26T07:05:00+00:00",
    )
    task.runtime_state.record_failure(
        instance.instance_uuid,
        "2026-05-26T07:06:00+00:00",
        "offline",
        "2026-05-26T07:06:30+00:00",
    )

    assert task._snapshot_latest_refresh_dt(instance) == datetime(
        2026,
        5,
        26,
        7,
        5,
        tzinfo=timezone.utc,
    )


def test_config_change_prunes_deleted_runtime_instance_to_a_tombstone():
    tmp_path = make_test_dir("runtime-config-change-tombstone")
    playlist = _runtime_playlist(
        _runtime_plugin_data("one", "One"),
        _runtime_plugin_data("two", "Two"),
    )
    task, device_config, _clock = _make_runtime_task(tmp_path, playlists=[playlist])
    manager = device_config.playlist_manager
    removed_uuid = playlist.plugins[0].instance_uuid
    current_uuid = playlist.plugins[1].instance_uuid
    task.runtime_state.record_success(
        removed_uuid,
        "2026-05-26T07:05:00+00:00",
    )
    task.runtime_state.record_success(
        current_uuid,
        "2026-05-26T07:05:00+00:00",
    )

    assert manager.delete_plugin_instance(removed_uuid)
    task.signal_config_change()

    snapshot = task.runtime_state.snapshot()
    assert snapshot.instances[removed_uuid].tombstoned_at is not None
    assert snapshot.instances[current_uuid].tombstoned_at is None


def test_background_selection_waits_for_runtime_retry_deadline():
    tmp_path = make_test_dir("runtime-background-retry-deadline")
    playlist = _runtime_playlist(_runtime_plugin_data(latest_refresh_time=None))
    task, device_config, _clock = _make_runtime_task(tmp_path, playlists=[playlist])
    device_config.config.update({"theme_mode": "day", "active_theme": "day"})
    instance = playlist.plugins[0]
    task.runtime_state.record_failure(
        instance.instance_uuid,
        "2026-05-26T07:05:00+00:00",
        "offline",
        "2026-05-26T07:05:30+00:00",
    )

    delayed = task._select_background_commands(
        datetime(2026, 5, 26, 7, 5, 10, tzinfo=timezone.utc)
    )
    due = task._select_background_commands(
        datetime(2026, 5, 26, 7, 5, 31, tzinfo=timezone.utc)
    )

    assert delayed == ()
    assert len(due) == 1
    assert due[0].instance_uuid == instance.instance_uuid


def test_runtime_worker_records_attempt_failure_without_advancing_legacy_success(
    monkeypatch,
):
    class ExplodingPlugin:
        config = {}

        def generate_image(self, settings, device_config):
            raise RuntimeError("offline")

    tmp_path = make_test_dir("runtime-attempt-failure-state")
    legacy_success = "2026-05-26T07:00:00+00:00"
    playlist = _runtime_playlist(
        _runtime_plugin_data(latest_refresh_time=legacy_success)
    )
    task, device_config, _clock = _make_runtime_task(tmp_path, playlists=[playlist])
    device_config.config.update({"theme_mode": "day", "active_theme": "day"})
    instance = playlist.plugins[0]
    _write_runtime_cache(task, instance)
    monkeypatch.setattr("src.refresh_task.get_plugin_instance", lambda _config: ExplodingPlugin())
    task.start()
    try:
        assert task.wait_until_waiting(timeout=1.0)
        job = task.submit_playlist_display(instance.instance_uuid, force=True)
        result = task.wait_for_job(job["id"], timeout=1.0)

        state = task.runtime_state.snapshot().instances[instance.instance_uuid]
        assert result["status"] == "failed"
        assert state.last_attempt_at is not None
        assert state.last_failure_at is not None
        assert state.last_success_at is None
        assert device_config.playlist_manager.snapshot_instance(
            instance.instance_uuid
        ).latest_refresh_time == legacy_success
    finally:
        task.stop(join_timeout=1.0)


def test_generated_cache_success_uses_runtime_state_not_user_config(monkeypatch):
    tmp_path = make_test_dir("runtime-cache-success-state")
    legacy_success = "2026-05-26T07:00:00+00:00"
    current_dt = datetime(2026, 5, 26, 7, 5, tzinfo=timezone.utc)
    playlist = _runtime_playlist(
        _runtime_plugin_data(latest_refresh_time=legacy_success)
    )
    task, device_config, _clock = _make_runtime_task(tmp_path, playlists=[playlist])
    device_config.config.update({"theme_mode": "day", "active_theme": "day"})
    instance = playlist.plugins[0]
    _write_runtime_cache(task, instance)
    monkeypatch.setattr(task, "_get_current_datetime", lambda: current_dt)
    monkeypatch.setattr("src.refresh_task.get_plugin_instance", lambda _config: CapturePlugin([]))
    task.start()
    try:
        assert task.wait_until_waiting(timeout=1.0)
        job = task.submit_playlist_display(instance.instance_uuid, force=True)
        result = task.wait_for_job(job["id"], timeout=1.0)

        state = task.runtime_state.snapshot().instances[instance.instance_uuid]
        assert result["status"] == "completed"
        assert state.last_success_at == current_dt.isoformat()
        assert device_config.playlist_manager.snapshot_instance(
            instance.instance_uuid
        ).latest_refresh_time == legacy_success
    finally:
        task.stop(join_timeout=1.0)


def test_stop_flushes_runtime_state_synchronously_after_entering_drain():
    states = []
    holder = {}

    class RecordingRuntimeState:
        def flush(self):
            states.append(holder["task"].lifecycle.state)
            return True

    tmp_path = make_test_dir("runtime-state-drain-flush")
    task = RefreshTask(
        RuntimeDeviceConfig(tmp_path),
        RecordingDisplayManager(),
        runtime_state_store=RecordingRuntimeState(),
    )
    holder["task"] = task

    assert task.stop(join_timeout=0) is True
    assert states == [LifecycleState.DRAINING]
