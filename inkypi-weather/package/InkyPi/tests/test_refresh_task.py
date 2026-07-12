import ast
import json
import inspect
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
import threading
import time
import uuid

import pytest
from PIL import Image, ImageFont

from plugins.base_plugin.base_plugin import BasePlugin
from plugins.base_plugin.presentation import (
    PresentationMode,
    PresentationPreparation,
    PresentationRequestContext,
)
from plugins.base_plugin import presentation as presentation_contract
from plugins.newspaper.newspaper import Newspaper
from plugins import plugin_registry, plugin_settings
from plugins.plugin_settings import PluginSettingError
from src.model import Playlist, PlaylistManager, RefreshInfo
from src.plugins.plugin_manifest import PluginCapabilities, PluginManifest, PluginTheme
from src.refresh_task import ManualRefresh, PlaylistRefresh, RefreshTask
import src.refresh_task as refresh_task_module
from runtime.refresh_contracts import (
    CommandKind,
    CommandSource,
    JobStatus,
    LifecycleState,
    RefreshCommand,
    RefreshIntent,
    TaskCancelled,
    TaskContext,
)
from runtime.refresh_queue import QueueFullError, QueueStoppingError, RefreshQueue
from runtime.cache_catalog import authoritative_cache_path
from runtime.presentation_cache import (
    PreparedPresentationCandidate,
    PresentationCache,
    prepared_presentation_path,
)
from runtime.refresh_policy import ResourceSample
from runtime.render_arbiter import RenderArbiter
from runtime.runtime_state import (
    LastGoodCacheState,
    PresentationCommitReceipt,
    PresentationRequestState,
    RefreshLane,
)
from runtime.long_task_executor import (
    current_instance_identity,
    current_task_context,
)
from runtime.scheduler_state import LifecycleController, RetryRegistry, SchedulerState
from utils.image_utils import compute_image_hash


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


@pytest.mark.parametrize(
    ("settings", "plugin_config", "expected"),
    [
        ({}, {"id": "manifest-default", "refresh_on_display": True}, True),
        (
            {"refreshOnDisplay": True},
            {"id": "saved-true", "refresh_on_display": False},
            True,
        ),
        (
            {"refreshOnDisplay": False},
            {"id": "saved-false", "refresh_on_display": True},
            False,
        ),
        (
            {},
            {
                "id": "manifest-object",
                "_manifest": SimpleNamespace(refresh_on_display=True),
            },
            True,
        ),
        ({"mediaRotationMode": "rotate"}, {"id": "newspaper"}, True),
        ({}, {"id": "newspaper"}, True),
        ({"mediaRotationMode": "single"}, {"id": "newspaper"}, False),
        (
            {"mediaRotationMode": "rotate"},
            {"id": "newspaper", "refresh_on_display": False},
            False,
        ),
        (
            {"mediaRotationMode": "rotate", "refreshOnDisplay": False},
            {"id": "newspaper", "refresh_on_display": True},
            False,
        ),
    ],
)
def test_refresh_on_display_for_config_preserves_strict_precedence(
    settings,
    plugin_config,
    expected,
):
    assert (
        plugin_settings.resolve_refresh_on_display_for_config(
            settings,
            plugin_config,
        )
        is expected
    )


def test_refresh_on_display_for_config_rejects_invalid_saved_value():
    with pytest.raises(PluginSettingError, match="refreshOnDisplay"):
        plugin_settings.resolve_refresh_on_display_for_config(
            {"refreshOnDisplay": "sometimes"},
            {"id": "newspaper", "refresh_on_display": True},
        )


def test_refresh_on_display_for_config_restores_dynamic_thirteenth_newspaper():
    manifest_default_ids = {
        "backtothedate",
        "daily_art",
        "daily_wiki_page",
        "gcd_comic_covers",
        "live_radar",
        "magazine_covers",
        "pixiv_r18_ranking",
        "simple_calendar",
        "species_radar",
        "steam_daily_art",
        "tech_pulse",
    }
    no_display_trigger_ids = {
        "bambu_monitor",
        "box_office_top_movies",
        "china_box_office_top_movies",
        "daily_word_poem",
        "sports_dashboard",
        "steam_charts",
        "stocktracker",
        "weather",
    }
    rows = []
    for plugin_id in sorted(manifest_default_ids | no_display_trigger_ids):
        rows.append(
            (
                plugin_id,
                {},
                {
                    "id": plugin_id,
                    "refresh_on_display": plugin_id in manifest_default_ids,
                },
            )
        )
    rows.extend(
        [
            (
                "daily_ai_news",
                {"refreshOnDisplay": True},
                {"id": "daily_ai_news", "refresh_on_display": False},
            ),
            (
                "newspaper",
                {"mediaRotationMode": "rotate"},
                {"id": "newspaper"},
            ),
        ]
    )

    effective_ids = {
        plugin_id
        for plugin_id, settings, plugin_config in rows
        if plugin_settings.resolve_refresh_on_display_for_config(
            settings,
            plugin_config,
        )
    }

    assert effective_ids == manifest_default_ids | {
        "daily_ai_news",
        "newspaper",
    }
    assert len(effective_ids - {"newspaper"}) == 12
    assert len(effective_ids) == 13


def test_presentation_capability_lookup_is_metadata_only(monkeypatch):
    manifest = SimpleNamespace(
        capabilities=SimpleNamespace(supports_presentation_refresh=True),
    )
    monkeypatch.setattr(
        plugin_registry,
        "get_plugin_instance",
        lambda *_args, **_kwargs: pytest.fail("capability lookup instantiated plugin"),
    )
    monkeypatch.setattr(
        plugin_registry.importlib,
        "import_module",
        lambda *_args, **_kwargs: pytest.fail("capability lookup imported plugin"),
    )

    assert plugin_registry.plugin_supports_presentation_refresh(
        {"id": "prepared", "_manifest": manifest}
    ) is True
    assert plugin_registry.plugin_supports_presentation_refresh(
        {"id": "legacy-metadata-free"}
    ) is False


def test_refresh_task_routes_every_plugin_render_through_theme_wrapper():
    source = inspect.getsource(refresh_task_module)

    assert source.count("plugin.generate_image(") == 0
    # Seven original render sites plus the dedicated theme-only UI path.
    assert source.count("plugin.render_themed_image(") == 8


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


def _theme_manifest(
    plugin_id="themed_plugin",
    *,
    supported=True,
    presentation="ui",
):
    theme = None
    if supported:
        theme = PluginTheme(
            presentation=presentation,
            day=MappingProxyType(
                {"background": "#f7f1e3", "accent": "#9b3424"}
            ),
            night=MappingProxyType(
                {"background": "#101820", "accent": "#f2aa4c"}
            ),
        )
    return PluginManifest(
        schema_version=2,
        id=plugin_id,
        class_name="ThemedPlugin",
        display_name="Themed Plugin",
        refresh_on_display=False,
        capabilities=PluginCapabilities(supports_day_night_theme=supported),
        raw={},
        theme=theme,
    )


class DelegatingThemeWrapper:
    def render_themed_image(
        self,
        settings,
        device_config,
        **_kwargs,
    ):
        return self.generate_image(settings, device_config)


class FakePlugin(DelegatingThemeWrapper):
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


class CapturePlugin(DelegatingThemeWrapper):
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
    device_config.config.update({"theme_mode": "day", "active_theme": "day"})
    display_manager = BlockingDisplayManager()
    task = RefreshTask(device_config, display_manager=display_manager)
    _write_runtime_cache(task, plugin_instance)
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
        assert calls[-1] == {
            "id": "live_radar",
            "forceRefresh": True,
            "force_refresh": True,
            "_inkypiDisplayRender": True,
        }
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


class NonCacheablePlugin(DelegatingThemeWrapper):
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


def test_cache_only_playlist_worker_does_not_evaluate_refresh_on_display(monkeypatch):
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
    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda _config: pytest.fail("cache-only display instantiated a plugin"),
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

        assert result["status"] == "completed"
        assert task.display_manager.calls
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


class FailingPlugin(DelegatingThemeWrapper):
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
    device_config.config.update({"theme_mode": "day", "active_theme": "day"})
    display_manager = BlockingDisplayManager()
    task = RefreshTask(device_config, display_manager=display_manager)
    _write_runtime_cache(task, plugin_instance)
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


class BlockingRuntimePlugin(DelegatingThemeWrapper):
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


@pytest.mark.parametrize(
    ("plugin_mode", "device_mode", "expected_mode"),
    [("night", "day", "night"), ("auto", "day", "day")],
)
def test_playlist_command_pins_full_plugin_context_and_theme_cache_suffix(
    plugin_mode,
    device_mode,
    expected_mode,
):
    tmp_path = make_test_dir(f"runtime-theme-context-{plugin_mode}")
    plugin_data = _runtime_plugin_data("themed_plugin", "Themed Plugin")
    plugin_data["plugin_settings"]["themeMode"] = plugin_mode
    playlist = _runtime_playlist(plugin_data)
    task, device_config, _clock = _make_runtime_task(tmp_path, playlists=[playlist])
    device_config.config["theme_mode"] = device_mode
    device_config.get_plugin = lambda plugin_id: {
        "id": plugin_id,
        "_manifest": _theme_manifest(plugin_id),
    }
    instance = playlist.plugins[0].snapshot()

    command = task._playlist_command(
        playlist.name,
        instance,
        source=CommandSource.SCHEDULER,
        intent=RefreshIntent.DATA_REFRESH,
        current_dt=datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc),
    )

    context = command.payload["resolved_theme_context"]
    assert context["requested_mode"] == plugin_mode
    assert context["mode"] == expected_mode
    assert context["palette"]["background"] == (
        (16, 24, 32) if expected_mode == "night" else (247, 241, 227)
    )
    expected_name = task._cache_identity_filename(
        instance.instance_uuid,
        instance.structural_generation,
        instance.settings_revision,
        expected_mode,
    )
    assert Path(task._snapshot_cache_path(instance, expected_mode)).name == expected_name
    assert Path(task._staging_cache_path(instance, expected_mode)).name == expected_name


def test_theme_unaware_command_keeps_exact_legacy_unsuffixed_cache_identity():
    tmp_path = make_test_dir("runtime-theme-unaware-cache")
    playlist = _runtime_playlist(_runtime_plugin_data("plain_plugin", "Plain Plugin"))
    task, device_config, _clock = _make_runtime_task(tmp_path, playlists=[playlist])
    device_config.get_plugin = lambda plugin_id: {
        "id": plugin_id,
        "_manifest": _theme_manifest(plugin_id, supported=False),
    }
    instance = playlist.plugins[0].snapshot()

    command = task._playlist_command(
        playlist.name,
        instance,
        source=CommandSource.BACKGROUND,
        intent=RefreshIntent.DATA_REFRESH,
    )

    assert "resolved_theme_context" not in command.payload
    prefix = task._cache_identity_prefix(instance.instance_uuid)
    assert task._cache_identity_filename(
        instance.instance_uuid,
        instance.structural_generation,
        instance.settings_revision,
    ) == (
        f"{prefix}-{instance.structural_generation}-"
        f"{instance.settings_revision}.png"
    )


class RecordingThemeWrapperPlugin:
    def __init__(self, config):
        self.config = config
        self.contexts = []

    def render_themed_image(
        self,
        settings,
        device_config,
        *,
        theme_render_only=False,
        resolved_theme_context=None,
    ):
        self.contexts.append(resolved_theme_context)
        return Image.new("RGB", (2, 1), "white")


def test_pinned_mode_survives_environment_flip_through_render_stage_and_commit(
    monkeypatch,
):
    tmp_path = make_test_dir("runtime-theme-pinned-commit")
    plugin_data = _runtime_plugin_data("themed_plugin", "Themed Plugin")
    plugin_data["plugin_settings"]["themeMode"] = "auto"
    playlist = _runtime_playlist(plugin_data)
    task, device_config, _clock = _make_runtime_task(tmp_path, playlists=[playlist])
    manifest = _theme_manifest("themed_plugin")
    plugin_config = {
        "id": "themed_plugin",
        "_manifest": manifest,
    }
    device_config.get_plugin = lambda _plugin_id: plugin_config
    device_config.config["theme_mode"] = "night"
    plugin = RecordingThemeWrapperPlugin(plugin_config)
    monkeypatch.setattr("src.refresh_task.get_plugin_instance", lambda _config: plugin)
    instance = playlist.plugins[0].snapshot()
    command = task._playlist_command(
        playlist.name,
        instance,
        source=CommandSource.SCHEDULER,
        intent=RefreshIntent.DATA_REFRESH,
        force=True,
        display_cached_only=False,
        current_dt=datetime(2026, 7, 11, 22, 0, tzinfo=timezone.utc),
    )
    device_config.config["theme_mode"] = "day"
    monkeypatch.setattr(
        task,
        "_get_current_datetime",
        lambda: datetime(2026, 7, 12, 8, 0, tzinfo=timezone.utc),
    )

    result = task._execute_command(command)

    assert result is not None
    assert plugin.contexts[0]["mode"] == "night"
    night_path = Path(task._snapshot_cache_path(instance, "night"))
    assert night_path.exists()
    assert not Path(task._snapshot_cache_path(instance, "day")).exists()
    assert not Path(task._snapshot_cache_path(instance)).exists()
    assert not Path(task._staging_cache_path(instance, "night")).exists()


def _write_runtime_cache(task, instance, image=None):
    """Seed the UUID/revision cache used by the production command worker."""
    snapshot = instance.snapshot() if hasattr(instance, "snapshot") else instance
    cache_path = Path(task.cache_path_for_snapshot(snapshot))
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    (image or Image.new("RGB", (1, 1), "black")).save(cache_path)
    return cache_path


def _write_runtime_theme_cache(task, instance, mode, image=None):
    snapshot = instance.snapshot() if hasattr(instance, "snapshot") else instance
    cache_path = Path(task._snapshot_cache_path(snapshot, mode))
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    (image or Image.new("RGB", (32, 16), "black")).save(cache_path)
    return cache_path


class ThemeOnlyRecordingPlugin:
    def __init__(self, config, *, fail=False, color="white"):
        self.config = config
        self.fail = fail
        self.color = color
        self.calls = []

    def wants_refresh_on_display(self, _settings):
        return False

    def render_themed_image(
        self,
        settings,
        device_config,
        *,
        theme_render_only=False,
        resolved_theme_context=None,
    ):
        self.calls.append(
            {
                "settings": dict(settings),
                "theme_render_only": theme_render_only,
                "resolved_theme_context": dict(resolved_theme_context or {}),
            }
        )
        if self.fail:
            raise RuntimeError("theme presentation failed")
        image = Image.new("RGB", device_config.get_resolution(), self.color)
        if resolved_theme_context:
            image.info["inkypi_theme_mode"] = resolved_theme_context["mode"]
        return image


def _theme_transition_runtime(
    name,
    *,
    displayed_mode="auto",
    displayed_supported=True,
    displayed_uuid="current",
):
    tmp_path = make_test_dir(name)
    displayed = _runtime_plugin_data("displayed", "Displayed")
    displayed["plugin_settings"]["themeMode"] = displayed_mode
    fallback = _runtime_plugin_data("fallback", "Fallback")
    fallback["plugin_settings"]["themeMode"] = "auto"
    playlist = _runtime_playlist(displayed, fallback)
    task, device_config, _clock = _make_runtime_task(
        tmp_path,
        playlists=[playlist],
        cycle_seconds=300,
    )
    device_config.config.update({"theme_mode": "night", "active_theme": "day"})
    device_config.refresh_info = RefreshInfo(
        refresh_type="Playlist",
        playlist=playlist.name,
        plugin_id=playlist.plugins[0].plugin_id,
        plugin_instance=playlist.plugins[0].name,
        refresh_time="2026-07-11T21:59:00+00:00",
        image_hash="day-image",
    )
    runtime_displayed_uuid = (
        playlist.plugins[0].instance_uuid
        if displayed_uuid == "current"
        else displayed_uuid
    )
    task.runtime_state.set_display_state(
        "committed",
        instance_uuid=runtime_displayed_uuid,
        changed_at="2026-07-11T21:59:00+00:00",
    )
    configs = {
        "displayed": {
            "id": "displayed",
            "_manifest": _theme_manifest(
                "displayed",
                supported=displayed_supported,
            ),
        },
        "fallback": {
            "id": "fallback",
            "_manifest": _theme_manifest("fallback"),
        },
    }
    device_config.get_plugin = lambda plugin_id: configs[plugin_id]
    return task, device_config, playlist, configs


def test_theme_transition_selects_exact_displayed_auto_instance_without_fallback():
    task, _device_config, playlist, _configs = _theme_transition_runtime(
        "theme-transition-exact-auto"
    )
    manager = task.device_config.playlist_manager
    original_select = manager.select_theme_instance
    observed = {}

    def select_with_observation(*args, **kwargs):
        observed.update(kwargs)
        return original_select(*args, **kwargs)

    manager.select_theme_instance = select_with_observation

    command = task._select_scheduled_command(
        datetime(2026, 7, 11, 22, 0, tzinfo=timezone.utc)
    )

    assert command.instance_uuid == playlist.plugins[0].instance_uuid
    assert observed["allow_fallback"] is False
    assert observed["is_eligible"](playlist.plugins[0].snapshot()) is True
    assert observed["is_eligible"](playlist.plugins[1].snapshot()) is True
    assert command.force is False
    assert command.payload["theme_render_only"] is True
    assert command.payload["expected_displayed_instance_uuid"] == (
        playlist.plugins[0].instance_uuid
    )
    assert command.payload["resolved_theme_context"]["requested_mode"] == "auto"


@pytest.mark.parametrize(
    ("displayed_mode", "displayed_supported", "displayed_uuid"),
    [
        ("day", True, "current"),
        ("auto", False, "current"),
        ("auto", True, "missing-instance-uuid"),
    ],
)
def test_ineligible_or_missing_displayed_theme_target_persists_noop_without_rotation(
    displayed_mode,
    displayed_supported,
    displayed_uuid,
):
    task, device_config, playlist, _configs = _theme_transition_runtime(
        "theme-transition-noop",
        displayed_mode=displayed_mode,
        displayed_supported=displayed_supported,
        displayed_uuid=displayed_uuid,
    )
    before_rotation = (
        playlist.current_plugin_index,
        list(playlist.plugin_rotation_queue),
        list(playlist.plugin_rotation_pool),
        list(playlist.plugin_rotation_recent_history),
    )

    command = task._select_scheduled_command(
        datetime(2026, 7, 11, 22, 0, tzinfo=timezone.utc)
    )

    assert command is None
    assert device_config.config["active_theme"] == "night"
    assert device_config.config["active_theme_info"]["mode"] == "night"
    assert device_config.write_count == 1
    assert before_rotation == (
        playlist.current_plugin_index,
        list(playlist.plugin_rotation_queue),
        list(playlist.plugin_rotation_pool),
        list(playlist.plugin_rotation_recent_history),
    )


def test_immediate_ui_theme_redraw_is_pinned_force_free_and_preserves_data_cadence(
    monkeypatch,
):
    task, device_config, playlist, configs = _theme_transition_runtime(
        "theme-transition-ui-cadence"
    )
    current_dt = datetime(2026, 7, 11, 22, 0, tzinfo=timezone.utc)
    anchor = device_config.refresh_info.refresh_time
    instance = playlist.plugins[0].snapshot()
    _write_runtime_theme_cache(
        task,
        instance,
        "day",
        Image.new("RGB", (32, 16), "black"),
    )
    plugin = ThemeOnlyRecordingPlugin(configs["displayed"], color="white")
    monkeypatch.setattr("src.refresh_task.get_plugin_instance", lambda _config: plugin)
    monkeypatch.setattr(task, "_get_current_datetime", lambda: current_dt)

    command = task._select_scheduled_command(current_dt)
    result = task._execute_command(command)

    assert result is not None
    assert plugin.calls == [
        {
            "settings": {"id": "displayed", "themeMode": "auto", "_inkypiDisplayRender": True},
            "theme_render_only": True,
            "resolved_theme_context": dict(command.payload["resolved_theme_context"]),
        }
    ]
    assert command.force is False
    assert device_config.refresh_info.refresh_time == anchor
    state = task.runtime_state.snapshot().instances.get(instance.instance_uuid)
    assert state is None or state.last_success_at is None
    assert device_config.config["active_theme"] == "night"
    assert Path(task._snapshot_cache_path(instance, "night")).exists()
    assert not Path(task._staging_cache_path(instance, "night")).exists()


def test_failed_immediate_theme_redraw_keeps_last_good_and_enters_cooldown(
    monkeypatch,
):
    task, device_config, playlist, configs = _theme_transition_runtime(
        "theme-transition-last-good"
    )
    current_dt = datetime(2026, 7, 11, 22, 0, tzinfo=timezone.utc)
    instance = playlist.plugins[0].snapshot()
    day_path = _write_runtime_theme_cache(
        task,
        instance,
        "day",
        Image.new("RGB", (32, 16), "black"),
    )
    original = day_path.read_bytes()
    plugin = ThemeOnlyRecordingPlugin(configs["displayed"], fail=True)
    monkeypatch.setattr("src.refresh_task.get_plugin_instance", lambda _config: plugin)
    monkeypatch.setattr(task, "_get_current_datetime", lambda: current_dt)
    command = task._select_scheduled_command(current_dt)

    submitted = task.refresh_queue.submit(command)
    task._process_queue_entry(task.refresh_queue.take(timeout=0))
    result = task.refresh_queue.get_entry(submitted.id).job

    assert command.force is False
    assert command.payload["theme_render_only"] is True
    assert result.status is JobStatus.FAILED
    assert day_path.read_bytes() == original
    assert not Path(task._snapshot_cache_path(instance, "night")).exists()
    assert task.display_manager.calls == []
    assert device_config.config["active_theme"] == "day"
    assert task._theme_refresh_retry_delayed(
        command.payload["theme_context"],
        current_dt + timedelta(seconds=1),
    )


def test_queued_theme_transition_is_stale_if_display_changes_before_render(
    monkeypatch,
):
    task, device_config, playlist, configs = _theme_transition_runtime(
        "theme-transition-stale-before-render"
    )
    current_dt = datetime(2026, 7, 11, 22, 0, tzinfo=timezone.utc)
    target = playlist.plugins[0].snapshot()
    other = playlist.plugins[1].snapshot()
    plugin = ThemeOnlyRecordingPlugin(configs["displayed"])
    monkeypatch.setattr("src.refresh_task.get_plugin_instance", lambda _config: plugin)
    monkeypatch.setattr(task, "_get_current_datetime", lambda: current_dt)
    command = task._select_scheduled_command(current_dt)
    task.runtime_state.set_display_state(
        "committed",
        instance_uuid=other.instance_uuid,
        changed_at=(current_dt + timedelta(seconds=1)).isoformat(),
    )

    submitted = task.refresh_queue.submit(command)
    task._process_queue_entry(task.refresh_queue.take(timeout=0))
    result = task.refresh_queue.get_entry(submitted.id).job

    assert command.payload["expected_displayed_instance_uuid"] == target.instance_uuid
    assert result.status is JobStatus.CANCELED
    assert result.error_code == "stale_selection"
    assert plugin.calls == []
    assert task.display_manager.calls == []
    assert not Path(task._snapshot_cache_path(target, "night")).exists()
    assert device_config.config["active_theme"] == "day"
    assert "active_theme_info" not in device_config.config
    assert "active_theme_refresh_failure" not in device_config.config
    assert device_config.write_count == 0


def test_theme_transition_is_stale_if_display_changes_during_render(
    monkeypatch,
):
    task, device_config, playlist, configs = _theme_transition_runtime(
        "theme-transition-stale-after-render"
    )
    current_dt = datetime(2026, 7, 11, 22, 0, tzinfo=timezone.utc)
    target = playlist.plugins[0].snapshot()
    other = playlist.plugins[1].snapshot()
    day_path = _write_runtime_theme_cache(
        task,
        target,
        "day",
        Image.new("RGB", (32, 16), "black"),
    )
    day_bytes = day_path.read_bytes()

    class DisplaySwitchingPlugin(ThemeOnlyRecordingPlugin):
        def render_themed_image(self, *args, **kwargs):
            image = super().render_themed_image(*args, **kwargs)
            task.runtime_state.set_display_state(
                "committed",
                instance_uuid=other.instance_uuid,
                changed_at=(current_dt + timedelta(seconds=1)).isoformat(),
            )
            return image

    plugin = DisplaySwitchingPlugin(configs["displayed"])
    monkeypatch.setattr("src.refresh_task.get_plugin_instance", lambda _config: plugin)
    monkeypatch.setattr(task, "_get_current_datetime", lambda: current_dt)
    command = task._select_scheduled_command(current_dt)

    submitted = task.refresh_queue.submit(command)
    task._process_queue_entry(task.refresh_queue.take(timeout=0))
    result = task.refresh_queue.get_entry(submitted.id).job

    assert result.status is JobStatus.CANCELED
    assert result.error_code == "stale_selection"
    assert len(plugin.calls) == 1
    assert day_path.read_bytes() == day_bytes
    assert not Path(task._snapshot_cache_path(target, "night")).exists()
    assert not Path(task._staging_cache_path(target, "night")).exists()
    assert task.display_manager.calls == []
    state = task.runtime_state.snapshot().instances[target.instance_uuid]
    assert state.last_success_at is None
    assert device_config.config["active_theme"] == "day"
    assert "active_theme_info" not in device_config.config
    assert "active_theme_refresh_failure" not in device_config.config
    assert device_config.write_count == 0


def test_theme_transition_without_runtime_uuid_does_not_use_refresh_info_fallback(
    monkeypatch,
):
    task, device_config, playlist, configs = _theme_transition_runtime(
        "theme-transition-refresh-info-compat"
    )
    current_dt = datetime(2026, 7, 11, 22, 0, tzinfo=timezone.utc)
    task.runtime_state.set_display_state(
        "committed",
        instance_uuid=None,
        changed_at=current_dt.isoformat(),
    )
    _prepare_independent_theme_candidate(task, playlist, current_dt)
    plugin = ThemeOnlyRecordingPlugin(configs["displayed"])
    monkeypatch.setattr("src.refresh_task.get_plugin_instance", lambda _config: plugin)
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
    )
    monkeypatch.setattr(task, "_get_current_datetime", lambda: current_dt)

    command = task._select_independent_refresh_command(current_dt)

    assert command is None
    assert plugin.calls == []
    assert device_config.config["active_theme"] == "night"


@pytest.mark.parametrize("presentation", ["ui", "media"])
def test_missing_theme_cache_under_pressure_keeps_last_good_without_render(
    monkeypatch,
    presentation,
):
    task, device_config, playlist, configs = _theme_transition_runtime(
        f"theme-transition-pressure-{presentation}"
    )
    current_dt = datetime(2026, 7, 11, 22, 0, tzinfo=timezone.utc)
    target = playlist.plugins[0].snapshot()
    configs["displayed"]["_manifest"] = _theme_manifest(
        "displayed",
        presentation=presentation,
    )
    dimensions = (40, 24) if presentation == "media" else (32, 16)
    device_config.get_resolution = lambda: dimensions
    day_path = _write_runtime_theme_cache(
        task,
        target,
        "day",
        Image.new("RGB", dimensions, "black"),
    )
    day_bytes = day_path.read_bytes()
    plugin = ThemeOnlyRecordingPlugin(configs["displayed"])
    chrome_calls = []
    original_chrome = refresh_task_module.apply_media_theme_chrome
    monkeypatch.setattr("src.refresh_task.get_plugin_instance", lambda _config: plugin)

    def record_chrome(*args, **kwargs):
        chrome_calls.append((args, kwargs))
        return original_chrome(*args, **kwargs)

    monkeypatch.setattr(
        "src.refresh_task.apply_media_theme_chrome",
        record_chrome,
    )
    monkeypatch.setattr(
        "src.refresh_task._display_refresh_under_resource_pressure",
        lambda _device_config, **_kwargs: True,
    )
    monkeypatch.setattr(task, "_get_current_datetime", lambda: current_dt)
    before_refresh = device_config.refresh_info.to_dict()
    command = task._select_scheduled_command(current_dt)

    result = task._execute_command(command)

    assert result is None
    assert plugin.calls == []
    assert chrome_calls == []
    assert day_path.read_bytes() == day_bytes
    assert not Path(task._snapshot_cache_path(target, "night")).exists()
    assert not Path(task._staging_cache_path(target, "night")).exists()
    assert task.display_manager.calls == []
    assert device_config.refresh_info.to_dict() == before_refresh
    state = task.runtime_state.snapshot().instances.get(target.instance_uuid)
    assert state is None or state.last_success_at is None
    assert device_config.config["active_theme"] == "day"
    assert "active_theme_info" not in device_config.config
    assert "active_theme_refresh_failure" not in device_config.config
    assert device_config.write_count == 0


def test_existing_target_theme_cache_is_safe_to_promote_under_pressure(monkeypatch):
    task, device_config, playlist, configs = _theme_transition_runtime(
        "theme-transition-pressure-cached"
    )
    current_dt = datetime(2026, 7, 11, 22, 0, tzinfo=timezone.utc)
    target = playlist.plugins[0].snapshot()
    _write_runtime_theme_cache(
        task,
        target,
        "night",
        Image.new("RGB", (32, 16), "white"),
    )
    plugin = ThemeOnlyRecordingPlugin(configs["displayed"], fail=True)
    monkeypatch.setattr("src.refresh_task.get_plugin_instance", lambda _config: plugin)
    monkeypatch.setattr(
        "src.refresh_task._display_refresh_under_resource_pressure",
        lambda _device_config, **_kwargs: True,
    )
    monkeypatch.setattr(task, "_get_current_datetime", lambda: current_dt)
    anchor = device_config.refresh_info.refresh_time
    command = task._select_scheduled_command(current_dt)

    result = task._execute_command(command)

    assert result is not None
    assert plugin.calls == []
    assert device_config.config["active_theme"] == "night"
    assert device_config.refresh_info.refresh_time == anchor
    state = task.runtime_state.snapshot().instances.get(target.instance_uuid)
    assert state is None or state.last_success_at is None


def test_manual_force_display_still_renders_under_display_pressure(monkeypatch):
    tmp_path = make_test_dir("manual-force-under-pressure")
    playlist = _runtime_playlist(
        _runtime_plugin_data("manual_force", "Manual Force")
    )
    task, _device_config, _clock = _make_runtime_task(tmp_path, playlists=[playlist])
    current_dt = datetime(2026, 7, 11, 22, 0, tzinfo=timezone.utc)
    calls = []
    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda _config: CapturePlugin(calls),
    )
    monkeypatch.setattr(
        "src.refresh_task._display_refresh_under_resource_pressure",
        lambda _device_config, **_kwargs: True,
    )
    monkeypatch.setattr(task, "_get_current_datetime", lambda: current_dt)
    instance = playlist.plugins[0].snapshot()
    command = task._playlist_command(
        playlist.name,
        instance,
        source=CommandSource.MANUAL,
        intent=RefreshIntent.MANUAL_RENDER,
        force=True,
        display_cached_only=True,
        require_active=True,
    )

    result = task._execute_command(command)

    assert result is not None
    assert calls == [
        {
            "id": "manual_force",
            "forceRefresh": True,
            "force_refresh": True,
            "_inkypiDisplayRender": True,
        }
    ]


@pytest.mark.parametrize("source_mode", ["day", None])
def test_media_theme_redraw_reuses_opposite_or_legacy_cache_without_provider(
    monkeypatch,
    source_mode,
):
    task, device_config, playlist, configs = _theme_transition_runtime(
        f"theme-transition-media-{source_mode or 'legacy'}"
    )
    current_dt = datetime(2026, 7, 11, 22, 0, tzinfo=timezone.utc)
    instance = playlist.plugins[0].snapshot()
    configs["displayed"]["_manifest"] = _theme_manifest(
        "displayed",
        presentation="media",
    )
    device_config.get_resolution = lambda: (40, 24)
    source = Image.new("RGB", (40, 24), (180, 20, 30))
    source_path = Path(task._snapshot_cache_path(instance, source_mode))
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source.save(source_path)
    plugin = ThemeOnlyRecordingPlugin(configs["displayed"], color="white")
    monkeypatch.setattr("src.refresh_task.get_plugin_instance", lambda _config: plugin)
    monkeypatch.setattr(task, "_get_current_datetime", lambda: current_dt)

    command = task._select_scheduled_command(current_dt)
    result = task._execute_command(command)

    assert plugin.calls == []
    assert result.getpixel((20, 12)) == (180, 20, 30)
    assert result.info["inkypi_theme_mode"] == "night"
    assert Path(task._snapshot_cache_path(instance, "night")).exists()
    assert not Path(task._staging_cache_path(instance, "night")).exists()


def test_opposite_theme_cache_is_not_background_missing_until_data_is_due(
    monkeypatch,
):
    tmp_path = make_test_dir("theme-background-lazy")
    plugin_data = _runtime_plugin_data(
        "themed_plugin",
        "Themed Plugin",
        latest_refresh_time="2999-01-01T00:00:00+00:00",
        interval=3600,
    )
    plugin_data["plugin_settings"]["themeMode"] = "auto"
    playlist = _runtime_playlist(plugin_data)
    task, device_config, _clock = _make_runtime_task(tmp_path, playlists=[playlist])
    device_config.config.update({"theme_mode": "night", "active_theme": "night"})
    plugin_config = {
        "id": "themed_plugin",
        "_manifest": _theme_manifest("themed_plugin"),
    }
    device_config.get_plugin = lambda _plugin_id: plugin_config
    instance = playlist.plugins[0]
    _write_runtime_theme_cache(task, instance, "day")
    current_dt = datetime(2026, 7, 11, 22, 0, tzinfo=timezone.utc)

    assert task._select_background_commands(current_dt) == ()

    task.runtime_state.record_success(
        instance.instance_uuid,
        (current_dt - timedelta(hours=2)).isoformat(),
    )
    commands = task._select_background_commands(current_dt)

    assert len(commands) == 1
    assert commands[0].kind is CommandKind.CACHE_REFRESH
    assert commands[0].payload.get("theme_render_only") is None
    plugin = ThemeOnlyRecordingPlugin(plugin_config, color="white")
    monkeypatch.setattr("src.refresh_task.get_plugin_instance", lambda _config: plugin)
    monkeypatch.setattr(task, "_cache_refresh_under_resource_pressure", lambda: False)
    monkeypatch.setattr(task, "_get_current_datetime", lambda: current_dt)

    task._execute_command(commands[0])

    assert plugin.calls[0]["theme_render_only"] is False
    state = task.runtime_state.snapshot().instances[instance.instance_uuid]
    assert state.last_success_at == current_dt.isoformat()
    assert Path(task._snapshot_cache_path(instance.snapshot(), "night")).exists()


def test_theme_retry_cooldown_does_not_block_independently_due_background_data(
    monkeypatch,
):
    tmp_path = make_test_dir("theme-cooldown-keeps-data-refresh")
    current_dt = datetime(2026, 7, 11, 22, 0, tzinfo=timezone.utc)
    due = _runtime_plugin_data(
        "ordinary_due",
        "Ordinary Due",
        latest_refresh_time=(current_dt - timedelta(hours=2)).isoformat(),
        interval=3600,
    )
    presentation_only = _runtime_plugin_data(
        "presentation_only",
        "Presentation Only",
        latest_refresh_time="2999-01-01T00:00:00+00:00",
        interval=3600,
    )
    presentation_only["plugin_settings"]["themeMode"] = "auto"
    playlist = _runtime_playlist(due, presentation_only)
    task, device_config, _clock = _make_runtime_task(tmp_path, playlists=[playlist])
    failure = {
        "mode": "night",
        "retry_after": (current_dt + timedelta(minutes=10)).isoformat(),
        "error": "theme render failed",
    }
    device_config.config.update(
        {
            "theme_mode": "night",
            "active_theme": "day",
            "active_theme_refresh_failure": failure,
        }
    )

    def plugin_config(plugin_id):
        return {
            "id": plugin_id,
            "_manifest": _theme_manifest(
                plugin_id,
                supported=plugin_id == "presentation_only",
            ),
        }

    device_config.get_plugin = plugin_config

    commands = task._select_background_commands(current_dt)

    assert [command.instance_uuid for command in commands] == [
        playlist.plugins[0].instance_uuid
    ]
    assert commands[0].plugin_id == "ordinary_due"
    assert commands[0].payload.get("theme_render_only") is None
    calls = []
    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda _config: CapturePlugin(calls),
    )
    monkeypatch.setattr(task, "_cache_refresh_under_resource_pressure", lambda: False)
    monkeypatch.setattr(task, "_get_current_datetime", lambda: current_dt)

    task._execute_command(commands[0])

    assert calls == [
        {
            "id": "ordinary_due",
        }
    ]
    state = task.runtime_state.snapshot().instances[playlist.plugins[0].instance_uuid]
    assert state.last_success_at == current_dt.isoformat()
    assert device_config.config["active_theme"] == "day"
    assert device_config.config["active_theme_refresh_failure"] == failure


def test_ordinary_next_display_uses_last_good_theme_cache_without_generation(
    monkeypatch,
):
    tmp_path = make_test_dir("theme-lazy-next-display")
    plugin_data = _runtime_plugin_data(
        "themed_plugin",
        "Themed Plugin",
        latest_refresh_time="2999-01-01T00:00:00+00:00",
        interval=3600,
    )
    plugin_data["plugin_settings"]["themeMode"] = "auto"
    playlist = _runtime_playlist(plugin_data)
    task, device_config, _clock = _make_runtime_task(
        tmp_path,
        playlists=[playlist],
        cycle_seconds=300,
    )
    current_dt = datetime(2026, 7, 11, 22, 0, tzinfo=timezone.utc)
    device_config.config.update({"theme_mode": "night", "active_theme": "night"})
    device_config.refresh_info = RefreshInfo(
        refresh_type="Playlist",
        playlist=playlist.name,
        plugin_id=playlist.plugins[0].plugin_id,
        plugin_instance=playlist.plugins[0].name,
        refresh_time="2026-07-11T21:50:00+00:00",
        image_hash="day-image",
    )
    plugin_config = {
        "id": "themed_plugin",
        "_manifest": _theme_manifest("themed_plugin"),
    }
    device_config.get_plugin = lambda _plugin_id: plugin_config
    instance = playlist.plugins[0].snapshot()
    _write_runtime_theme_cache(task, instance, "day")
    seeded_at = current_dt - timedelta(minutes=10)
    _seed_theme_last_good(task, instance, "day", seeded_at)
    plugin = ThemeOnlyRecordingPlugin(plugin_config, color="white")
    monkeypatch.setattr("src.refresh_task.get_plugin_instance", lambda _config: plugin)
    monkeypatch.setattr(task, "_get_current_datetime", lambda: current_dt)

    command = task._select_cached_display_command(current_dt)
    result = task._execute_command(command)

    assert result is not None
    assert command.payload.get("theme_render_only") is None
    assert command.payload["cache_theme_mode"] == "day"
    assert plugin.calls == []
    state = task.runtime_state.snapshot().instances.get(instance.instance_uuid)
    assert state.data.last_success_at == seeded_at.isoformat()
    assert device_config.refresh_info.refresh_time == current_dt.isoformat()
    assert not Path(task._snapshot_cache_path(instance, "night")).exists()
    assert not Path(task._staging_cache_path(instance, "night")).exists()


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
    device_config.config.update({"theme_mode": "day", "active_theme": "day"})
    target = device_config.playlist_manager.resolve_plugin_instance_snapshot(
        "Inactive",
        "inactive",
        "Inactive",
    ).instance
    _write_runtime_cache(task, target)
    _write_runtime_cache(task, active.plugins[0])
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
        assert calls == []
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


def _make_scheduler_fairness_task(name, *, refresh_time, clock=None):
    tmp_path = make_test_dir(name)
    playlist = _runtime_playlist(
        _runtime_plugin_data(
            "sports_dashboard",
            "SportsDashboard",
            latest_refresh_time="2026-05-26T07:00:00+00:00",
            interval=900,
        ),
        _runtime_plugin_data(
            "ordinary_plugin",
            "Ordinary Plugin",
            latest_refresh_time="2026-05-26T07:00:00+00:00",
            interval=3600,
        ),
    )
    sports, ordinary = playlist.plugins
    plugin_keys = [sports.instance_uuid, ordinary.instance_uuid]
    playlist.current_plugin_index = 0
    playlist.plugin_rotation_pool = list(plugin_keys)
    playlist.plugin_rotation_queue = [ordinary.instance_uuid, sports.instance_uuid]

    task, device_config, _clock = _make_runtime_task(
        tmp_path,
        playlists=[playlist],
        clock=clock,
        cycle_seconds=300,
    )
    device_config.config.update({"theme_mode": "day", "active_theme": "day"})
    device_config.refresh_info = RefreshInfo(
        refresh_type="Playlist",
        playlist=playlist.name,
        plugin_id=sports.plugin_id,
        plugin_instance=sports.name,
        refresh_time=refresh_time,
        image_hash="sports",
    )
    task.runtime_state.set_display_state(
        "committed",
        instance_uuid=sports.instance_uuid,
        changed_at=refresh_time,
    )
    return task, device_config, playlist, sports, ordinary


@pytest.mark.parametrize("live_due", [False, True])
def test_playlist_cycle_wins_before_live_or_sports_priority(monkeypatch, live_due):
    task, _device_config, _playlist, sports, ordinary = (
        _make_scheduler_fairness_task(
            "scheduler-cycle-priority",
            refresh_time="2026-05-26T07:00:00+00:00",
        )
    )
    monkeypatch.setattr(
        task,
        "_snapshot_live_refresh_due",
        lambda instance, _current_dt: (
            live_due and instance.instance_uuid == sports.instance_uuid
        ),
    )

    command = task._select_scheduled_command(
        datetime(2026, 5, 26, 7, 20, tzinfo=timezone.utc)
    )

    assert command is not None
    assert command.instance_uuid == ordinary.instance_uuid
    assert command.source is CommandSource.SCHEDULER
    assert command.priority == 50


@pytest.mark.parametrize("under_pressure", [False, True])
def test_live_refresh_cycles_do_not_move_playlist_rotation_anchor(
    monkeypatch,
    under_pressure,
):
    anchor = "2026-05-26T07:00:00+00:00"
    task, device_config, playlist, sports, ordinary = (
        _make_scheduler_fairness_task(
            "scheduler-live-anchor",
            refresh_time=anchor,
        )
    )
    monkeypatch.setattr(
        task,
        "_snapshot_live_refresh_due",
        lambda instance, _current_dt: instance.instance_uuid == sports.instance_uuid,
    )
    monkeypatch.setattr(
        "src.refresh_task._display_refresh_under_resource_pressure",
        lambda _device_config, **_kwargs: under_pressure,
    )

    for minute in (1, 2):
        current = datetime(2026, 5, 26, 7, minute, tzinfo=timezone.utc)
        command = task._select_scheduled_command(current)
        if under_pressure:
            assert command is None
            continue
        assert command is not None
        assert command.instance_uuid == sports.instance_uuid
        assert command.source is CommandSource.LIVE
        resolved = task._resolve_playlist_command(command)
        task._set_render_metadata(True, True, {})
        task._commit_command_result(
            command,
            resolved,
            Image.new("RGB", (2, 1), (minute, minute, minute)),
            current,
        )
        assert device_config.refresh_info.refresh_time == anchor

    rotation = task._select_scheduled_command(
        datetime(2026, 5, 26, 7, 5, tzinfo=timezone.utc)
    )

    assert rotation is not None
    assert rotation.instance_uuid == ordinary.instance_uuid
    assert rotation.source is CommandSource.SCHEDULER


def test_live_refresh_does_not_preempt_a_different_displayed_instance(monkeypatch):
    task, device_config, playlist, sports, ordinary = (
        _make_scheduler_fairness_task(
            "scheduler-live-non-current",
            refresh_time="2026-05-26T07:19:00+00:00",
        )
    )
    device_config.refresh_info = RefreshInfo(
        refresh_type="Playlist",
        playlist=playlist.name,
        plugin_id=ordinary.plugin_id,
        plugin_instance=ordinary.name,
        refresh_time="2026-05-26T07:19:00+00:00",
        image_hash="ordinary",
    )
    task.runtime_state.set_display_state(
        "committed",
        instance_uuid=ordinary.instance_uuid,
        changed_at="2026-05-26T07:19:00+00:00",
    )
    monkeypatch.setattr(
        task,
        "_snapshot_live_refresh_due",
        lambda instance, _current_dt: instance.instance_uuid == sports.instance_uuid,
    )
    monkeypatch.setattr(
        "src.refresh_task._display_refresh_under_resource_pressure",
        lambda _device_config, **_kwargs: False,
    )

    command = task._select_scheduled_command(
        datetime(2026, 5, 26, 7, 20, tzinfo=timezone.utc)
    )

    assert command is None


def test_stale_display_uuid_never_falls_back_to_same_name_live_instance(monkeypatch):
    task, _device_config, _playlist, sports, _ordinary = (
        _make_scheduler_fairness_task(
            "scheduler-live-stale-uuid",
            refresh_time="2026-05-26T07:19:00+00:00",
        )
    )
    task.runtime_state.set_display_state(
        "committed",
        instance_uuid="stale-replaced-instance-uuid",
        changed_at="2026-05-26T07:19:30+00:00",
    )
    monkeypatch.setattr(
        task,
        "_snapshot_live_refresh_due",
        lambda instance, _current_dt: instance.instance_uuid == sports.instance_uuid,
    )
    monkeypatch.setattr(
        "src.refresh_task._display_refresh_under_resource_pressure",
        lambda _device_config, **_kwargs: False,
    )

    command = task._select_scheduled_command(
        datetime(2026, 5, 26, 7, 20, tzinfo=timezone.utc)
    )

    assert command is None


def test_live_command_is_stale_if_display_changes_before_execution(monkeypatch):
    task, device_config, playlist, sports, ordinary = (
        _make_scheduler_fairness_task(
            "scheduler-live-stale-before-execute",
            refresh_time="2026-05-26T07:19:00+00:00",
        )
    )
    monkeypatch.setattr(
        task,
        "_snapshot_live_refresh_due",
        lambda instance, _current_dt: instance.instance_uuid == sports.instance_uuid,
    )
    monkeypatch.setattr(
        "src.refresh_task._display_refresh_under_resource_pressure",
        lambda _device_config, **_kwargs: False,
    )
    command = task._select_scheduled_command(
        datetime(2026, 5, 26, 7, 20, tzinfo=timezone.utc)
    )
    assert command is not None
    assert command.source is CommandSource.LIVE

    device_config.refresh_info = RefreshInfo(
        refresh_type="Playlist",
        playlist=playlist.name,
        plugin_id=ordinary.plugin_id,
        plugin_instance=ordinary.name,
        refresh_time="2026-05-26T07:19:30+00:00",
        image_hash="ordinary",
    )
    task.runtime_state.set_display_state(
        "committed",
        instance_uuid=ordinary.instance_uuid,
        changed_at="2026-05-26T07:19:30+00:00",
    )

    assert task._resolve_playlist_command(command) is None


def test_live_command_revalidates_current_display_before_commit(monkeypatch):
    task, device_config, playlist, sports, ordinary = (
        _make_scheduler_fairness_task(
            "scheduler-live-stale-before-commit",
            refresh_time="2026-05-26T07:19:00+00:00",
        )
    )
    monkeypatch.setattr(
        task,
        "_snapshot_live_refresh_due",
        lambda instance, _current_dt: instance.instance_uuid == sports.instance_uuid,
    )
    monkeypatch.setattr(
        "src.refresh_task._display_refresh_under_resource_pressure",
        lambda _device_config, **_kwargs: False,
    )
    command = task._select_scheduled_command(
        datetime(2026, 5, 26, 7, 20, tzinfo=timezone.utc)
    )
    resolved = task._resolve_playlist_command(command)
    assert resolved is not None

    changed_at = "2026-05-26T07:19:30+00:00"
    device_config.refresh_info = RefreshInfo(
        refresh_type="Playlist",
        playlist=playlist.name,
        plugin_id=ordinary.plugin_id,
        plugin_instance=ordinary.name,
        refresh_time=changed_at,
        image_hash="ordinary",
    )
    task.runtime_state.set_display_state(
        "committed",
        instance_uuid=ordinary.instance_uuid,
        changed_at=changed_at,
    )
    task._set_render_metadata(False, False, {})

    with pytest.raises(TaskCancelled, match="live display target changed"):
        task._commit_command_result(
            command,
            resolved,
            Image.new("RGB", (2, 1), "white"),
            datetime(2026, 5, 26, 7, 20, tzinfo=timezone.utc),
        )

    assert device_config.refresh_info.plugin_instance == ordinary.name
    assert task.display_manager.calls == []


def test_sports_interval_does_not_bypass_playlist_cycle(monkeypatch):
    task, _device_config, _playlist, sports, _ordinary = (
        _make_scheduler_fairness_task(
            "scheduler-sports-interval",
            refresh_time="2026-05-26T07:19:00+00:00",
        )
    )
    monkeypatch.setattr(task, "_snapshot_live_refresh_due", lambda *_args: False)

    current = datetime(2026, 5, 26, 7, 20, tzinfo=timezone.utc)
    command = task._select_scheduled_command(current)
    background = task._select_background_commands(current)

    assert command is None
    assert all(item.instance_uuid != sports.instance_uuid for item in background)


def test_live_due_background_policy_remains_reachable(monkeypatch):
    task, _device_config, _playlist, sports, ordinary = (
        _make_scheduler_fairness_task(
            "scheduler-live-background",
            refresh_time="2026-05-26T07:19:00+00:00",
        )
    )
    monkeypatch.setattr(
        task,
        "_snapshot_live_refresh_due",
        lambda instance, _current_dt, plugin=None: (
            instance.instance_uuid == ordinary.instance_uuid
        ),
    )
    monkeypatch.setattr(
        task,
        "_snapshot_background_cache_disabled",
        lambda instance: instance.instance_uuid == sports.instance_uuid,
    )

    current = datetime(2026, 5, 26, 7, 20, tzinfo=timezone.utc)
    display = task._select_scheduled_command(current)
    background = task._select_background_commands(current)

    assert display is None
    ordinary_work = [
        item for item in background if item.instance_uuid == ordinary.instance_uuid
    ]
    assert len(ordinary_work) == 1
    assert ordinary_work[0].kind is CommandKind.CACHE_REFRESH
    assert ordinary_work[0].source is CommandSource.BACKGROUND


def test_live_failures_never_delay_the_playlist_rotation_deadline(monkeypatch):
    task, device_config, _playlist, sports, current_dt, _anchor = (
        _sports_live_runtime(
            "scheduler-live-failure-fairness",
            background_value=True,
        )
    )
    task.retry_registry = RetryRegistry(jitter=lambda delay: delay)
    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda _config: FakePlugin(
            [],
            live_state={"active": True, "interval_seconds": 60},
        ),
    )
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
    )
    command = task._select_independent_refresh_command(current_dt)
    assert command.intent is RefreshIntent.LIVE_REFRESH
    monkeypatch.setattr(
        task,
        "_execute_command",
        lambda _command: (_ for _ in ()).throw(RuntimeError("live render failed")),
    )
    submitted = task.refresh_queue.submit(command)
    task._process_queue_entry(task.refresh_queue.take(timeout=0))

    assert task.refresh_queue.get_entry(submitted.id).job.status is JobStatus.FAILED
    assert [entry.key for entry in task.retry_registry.snapshot()] == [
        f"{sports.instance_uuid}:live"
    ]
    device_config.refresh_info.refresh_time = (
        current_dt - timedelta(minutes=6)
    ).isoformat()
    display = task._select_cached_display_command(current_dt)
    assert display is not None
    assert display.intent is RefreshIntent.DISPLAY_CACHE


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


def test_cache_pending_placeholder_uses_shared_base_ui_fonts(monkeypatch):
    tmp_path = make_test_dir("cache-pending-fonts")
    device_config = FakeDeviceConfig(tmp_path)
    playlist = _runtime_playlist(_runtime_plugin_data("plugin", "Plugin"))
    instance = playlist.plugins[0]
    calls = []
    font_path = Path(__file__).resolve().parents[1] / "src/static/fonts/NotoSansSC-VF.ttf"
    monkeypatch.setattr(
        refresh_task_module,
        "get_base_ui_font",
        lambda size, bold=False: calls.append((size, bold))
        or ImageFont.truetype(font_path, size),
        raising=False,
    )

    image = PlaylistRefresh(playlist, instance)._placeholder_image(device_config)

    assert image.size == (800, 480)
    assert calls == [(40, True), (17, False)]


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
        while not calls and time.monotonic() < deadline:
            time.sleep(0.01)
        assert calls
        task.signal_config_change()
        deadline = time.monotonic() + 1.0
        while not task.display_manager.calls and time.monotonic() < deadline:
            time.sleep(0.01)
        assert task.display_manager.calls
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


def _submit_blocked_playlist_render(task, manager):
    instance = manager.snapshot_instance(manager.first_instance_uuid())
    command = task._playlist_command(
        "DailyDoseOfDay",
        instance,
        source=CommandSource.MANUAL,
        intent=RefreshIntent.MANUAL_RENDER,
        force=True,
        display_cached_only=False,
        priority=100,
    )
    job = task.refresh_queue.submit(command)
    return task._job_payload(task.refresh_queue.get_entry(job.id))


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
        job = _submit_blocked_playlist_render(task, manager)
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
        job = _submit_blocked_playlist_render(task, manager)
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
            intent=RefreshIntent.THEME_REDRAW,
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
    plugin_data = _runtime_plugin_data(
        "themed_plugin",
        "Themed Plugin",
        latest_refresh_time="2999-01-01T00:00:00+00:00",
    )
    plugin_data["plugin_settings"]["themeMode"] = "auto"
    playlist = _runtime_playlist(plugin_data)
    clock = RuntimeClock()
    task, device_config, _clock = _make_runtime_task(tmp_path, playlists=[playlist], clock=clock)
    device_config.config["active_theme"] = "day"
    device_config.get_plugin = lambda plugin_id: {
        "id": plugin_id,
        "_manifest": _theme_manifest(plugin_id),
    }
    displayed = playlist.plugins[0]
    device_config.refresh_info = RefreshInfo(
        refresh_type="Playlist",
        playlist=playlist.name,
        plugin_id=displayed.plugin_id,
        plugin_instance=displayed.name,
        refresh_time="2026-05-26T22:07:00+00:00",
        image_hash="day-image",
    )
    task.runtime_state.set_display_state(
        "committed",
        instance_uuid=displayed.instance_uuid,
        changed_at="2026-05-26T22:07:00+00:00",
    )
    current_dt = [datetime(2026, 5, 26, 22, 8, tzinfo=timezone.utc)]
    _write_runtime_theme_cache(task, displayed, "day")
    _seed_theme_last_good(
        task,
        displayed.snapshot(),
        "day",
        current_dt[0] - timedelta(minutes=10),
    )
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
    followup = task.refresh_queue.take(timeout=0)
    assert followup.command.intent is RefreshIntent.DISPLAY_CACHE
    task._process_queue_entry(followup)
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

        def render_themed_image(self, settings, config, **_kwargs):
            return self.generate_image(settings, config)

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


def test_cache_only_display_validates_each_visible_side_effect(monkeypatch):
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
        for side_effect in ("display", "config"):
            index = events.index(side_effect)
            assert events[index - 1] == "validate"
        assert "cache" not in events
        assert "timestamp" not in events
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
        intent=RefreshIntent.THEME_REDRAW,
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
        job = _submit_blocked_playlist_render(task, manager)
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
        intent=RefreshIntent.MANUAL_RENDER,
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


def test_active_operation_snapshot_publishes_command_deadline_then_clears(monkeypatch):
    tmp_path = make_test_dir("runtime-active-operation-snapshot")
    clock = RuntimeClock()
    task, _device_config, _clock = _make_runtime_task(
        tmp_path,
        playlists=[],
        clock=clock,
    )
    command = RefreshCommand.create(
        kind=CommandKind.DISPLAY,
        source=CommandSource.MANUAL,
        plugin_id="active_plugin",
        payload={"refresh_type": "Manual Update", "settings": {}},
        now_monotonic=clock.monotonic(),
        deadline_monotonic=clock.monotonic() + 90,
    )
    submitted = task.refresh_queue.submit(command)
    entry = task.refresh_queue.take(timeout=0)
    observed = []

    def capture_active(_command):
        observed.append(task.active_operation_snapshot())

    monkeypatch.setattr(task, "_execute_command", capture_active)

    task._process_queue_entry(entry)

    assert len(observed) == 1
    assert observed[0].command_id == submitted.id
    assert observed[0].plugin_id == "active_plugin"
    assert observed[0].deadline_monotonic == command.deadline_monotonic
    assert task.active_operation_snapshot() is None


def test_process_queue_entry_binds_context_and_immutable_instance_identity(monkeypatch):
    tmp_path = make_test_dir("runtime-long-task-binding")
    playlist = _runtime_playlist(_runtime_plugin_data())
    task, device_config, _clock = _make_runtime_task(tmp_path, playlists=[playlist])
    instance = device_config.playlist_manager.snapshot_instance(
        device_config.playlist_manager.first_instance_uuid()
    )
    command = task._playlist_command(
        playlist.name,
        instance,
        source=CommandSource.MANUAL,
        intent=RefreshIntent.MANUAL_RENDER,
    )
    submitted = task.refresh_queue.submit(command)
    entry = task.refresh_queue.take(timeout=0)
    observed = []

    def capture_runtime(_command):
        observed.append((current_task_context(), current_instance_identity()))

    monkeypatch.setattr(task, "_execute_command", capture_runtime)

    task._process_queue_entry(entry)

    assert task.refresh_queue.get_entry(submitted.id).job.status is JobStatus.SUCCEEDED
    context, identity = observed[0]
    assert context.cancel_event is entry.cancel_event
    assert context.deadline_monotonic == command.deadline_monotonic
    assert identity.instance_uuid == instance.instance_uuid
    assert identity.structural_generation == instance.structural_generation
    assert identity.settings_revision == instance.settings_revision
    assert current_task_context() is None
    assert current_instance_identity() is None


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
        intent=RefreshIntent.DATA_REFRESH,
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


def test_instance_success_does_not_clear_prior_global_selection_retry(monkeypatch):
    tmp_path = make_test_dir("runtime-global-retry-success")
    playlist = _runtime_playlist(_runtime_plugin_data())
    task, device_config, _clock = _make_runtime_task(tmp_path, playlists=[playlist])
    monkeypatch.setattr(
        task,
        "_select_cached_display_command",
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
        intent=RefreshIntent.DATA_REFRESH,
        kind=CommandKind.CACHE_REFRESH,
    )
    submitted = task.refresh_queue.submit(command)
    entry = task.refresh_queue.take(timeout=0)
    monkeypatch.setattr(task, "_execute_command", lambda _command: None)
    task._process_queue_entry(entry)

    assert task.refresh_queue.get_entry(submitted.id).job.status is JobStatus.SUCCEEDED
    assert [entry.key for entry in task.retry_registry.snapshot()] == [
        RetryRegistry.GLOBAL_KEY
    ]


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


def test_legacy_background_candidates_are_clamped_to_one_cache_command(monkeypatch):
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

    assert len(commands) == 1
    assert all(command.kind is CommandKind.CACHE_REFRESH for command in commands)
    assert commands[0].instance_uuid == playlist.plugins[0].instance_uuid


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
    filenames = tuple(
        task._cache_identity_filename("target", 1, 2, mode)
        for mode in (None, "day", "night")
    )
    expected_paths = []
    for directory in (staging, cache):
        for filename in filenames:
            path = directory / filename
            path.write_bytes(b"owned")
            expected_paths.append(str(path))
    (staging / task._cache_identity_filename("other", 1, 2)).write_bytes(b"other")

    context = task.make_cleanup_context(timeout_seconds=12)
    paths = task.managed_cache_paths(
        "target",
        plugin_id="weather",
        instance_name="Main View",
    )

    assert context.cancel_event is task.stop_event
    assert context.deadline_monotonic == 12.0
    assert paths == tuple(sorted(expected_paths))
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


def test_runtime_worker_records_data_failure_without_advancing_seeded_model_success(
    monkeypatch,
):
    class ExplodingPlugin:
        config = {}

        def render_themed_image(self, settings, device_config, **_kwargs):
            return self.generate_image(settings, device_config)

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
        command = task._playlist_command(
            playlist.name,
            device_config.playlist_manager.snapshot_instance(instance.instance_uuid),
            source=CommandSource.BACKGROUND,
            intent=RefreshIntent.DATA_REFRESH,
            force=False,
            display_cached_only=False,
            kind=CommandKind.CACHE_REFRESH,
        )
        submitted = task.refresh_queue.submit(command)
        job = task._job_payload(task.refresh_queue.get_entry(submitted.id))
        result = task.wait_for_job(job["id"], timeout=1.0)

        state = task.runtime_state.snapshot().instances[instance.instance_uuid]
        assert result["status"] == "failed"
        assert state.last_attempt_at is not None
        assert state.last_failure_at is not None
        assert state.data.last_success_at == legacy_success
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
        command = task._playlist_command(
            playlist.name,
            device_config.playlist_manager.snapshot_instance(instance.instance_uuid),
            source=CommandSource.BACKGROUND,
            intent=RefreshIntent.DATA_REFRESH,
            force=False,
            display_cached_only=False,
            kind=CommandKind.CACHE_REFRESH,
        )
        submitted = task.refresh_queue.submit(command)
        job = task._job_payload(task.refresh_queue.get_entry(submitted.id))
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


def test_random_display_never_instantiates_plugin_or_calls_renderer(monkeypatch):
    tmp_path = make_test_dir("cache-only-random-display")
    playlist = _runtime_playlist(
        _runtime_plugin_data("one", "One"),
        _runtime_plugin_data("two", "Two"),
    )
    task, device_config, _clock = _make_runtime_task(
        tmp_path,
        playlists=[playlist],
        cycle_seconds=60,
    )
    device_config.config.update({"theme_mode": "day", "active_theme": "day"})
    device_config.refresh_info = RefreshInfo(
        refresh_type="Playlist",
        playlist=playlist.name,
        plugin_id="old",
        plugin_instance="Old",
        refresh_time="2026-07-11T11:00:00+00:00",
        image_hash="old",
    )
    for instance in playlist.plugins:
        _write_runtime_cache(task, instance)
    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda _config: (_ for _ in ()).throw(
            AssertionError("cache display must not instantiate a plugin")
        ),
    )

    command = task._select_cached_display_command(
        datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)
    )
    result = task._execute_command(command)

    assert result is not None
    assert command.intent is RefreshIntent.DISPLAY_CACHE
    assert len(task.display_manager.calls) == 1


def test_catalog_display_never_reopens_path_after_bound_validation(monkeypatch):
    tmp_path = make_test_dir("cache-only-bound-descriptor")
    playlist = _runtime_playlist(_runtime_plugin_data("one", "One"))
    task, device_config, _clock = _make_runtime_task(
        tmp_path,
        playlists=[playlist],
        cycle_seconds=60,
    )
    device_config.config.update({"theme_mode": "day", "active_theme": "day"})
    device_config.refresh_info.refresh_time = "2026-07-11T11:00:00+00:00"
    cache_path = _write_runtime_cache(
        task,
        playlist.plugins[0],
        Image.new("RGB", (2, 1), "red"),
    )
    replacement = tmp_path / "replacement.png"
    Image.new("RGB", (2, 1), "blue").save(replacement)
    command = task._select_cached_display_command(
        datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)
    )
    original_bytes = cache_path.read_bytes()
    replacement_bytes = replacement.read_bytes()
    real_path_loader = refresh_task_module._load_image_copy
    reopen_count = 0

    def swap_for_reopen_then_restore(path):
        nonlocal reopen_count
        reopen_count += 1
        Path(path).write_bytes(replacement_bytes)
        try:
            return real_path_loader(path)
        finally:
            Path(path).write_bytes(original_bytes)

    monkeypatch.setattr(
        refresh_task_module,
        "_load_image_copy",
        swap_for_reopen_then_restore,
    )

    image = task._load_catalog_display_image(command, resolved=None)

    try:
        assert image.getpixel((0, 0)) == (255, 0, 0)
        assert reopen_count == 0
    finally:
        image.close()


def test_random_selection_passes_only_catalog_eligible_uuids_to_model():
    tmp_path = make_test_dir("cache-only-random-eligibility")
    playlist = _runtime_playlist(
        _runtime_plugin_data("one", "One"),
        _runtime_plugin_data("two", "Two"),
        _runtime_plugin_data("three", "Three"),
    )
    task, device_config, _clock = _make_runtime_task(
        tmp_path,
        playlists=[playlist],
        cycle_seconds=60,
    )
    device_config.config.update({"theme_mode": "day", "active_theme": "day"})
    device_config.refresh_info.refresh_time = "2026-07-11T11:00:00+00:00"
    expected = {
        playlist.plugins[0].instance_uuid,
        playlist.plugins[2].instance_uuid,
    }
    _write_runtime_cache(task, playlist.plugins[0])
    corrupt = Path(task.cache_path_for_snapshot(playlist.plugins[1].snapshot()))
    corrupt.parent.mkdir(parents=True, exist_ok=True)
    corrupt.write_bytes(b"not-a-png")
    _write_runtime_cache(task, playlist.plugins[2])
    manager = device_config.playlist_manager
    original_select = manager.select_next_active_instance
    observed = []

    def select_with_observation(*args, **kwargs):
        observed.append(kwargs["eligible_instance_uuids"])
        return original_select(*args, **kwargs)

    manager.select_next_active_instance = select_with_observation

    command = task._select_cached_display_command(
        datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)
    )

    assert command is not None
    assert observed == [frozenset(expected)]
    assert command.instance_uuid in expected


@pytest.mark.parametrize("cache_state", ["missing", "corrupt"])
def test_missing_or_corrupt_cache_skips_candidate_without_placeholder_or_provider_call(
    monkeypatch,
    cache_state,
):
    tmp_path = make_test_dir(f"cache-only-{cache_state}")
    playlist = _runtime_playlist(_runtime_plugin_data("only", "Only"))
    task, device_config, _clock = _make_runtime_task(
        tmp_path,
        playlists=[playlist],
        cycle_seconds=60,
    )
    device_config.config.update({"theme_mode": "day", "active_theme": "day"})
    device_config.refresh_info.refresh_time = "2026-07-11T11:00:00+00:00"
    if cache_state == "corrupt":
        corrupt = Path(task.cache_path_for_snapshot(playlist.plugins[0].snapshot()))
        corrupt.parent.mkdir(parents=True, exist_ok=True)
        corrupt.write_bytes(b"not-a-png")
    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda _config: (_ for _ in ()).throw(
            AssertionError("cache miss must not instantiate a plugin")
        ),
    )

    command = task._select_cached_display_command(
        datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)
    )

    assert command is None
    assert task.display_manager.calls == []
    assert device_config.write_count == 0


def test_no_displayable_candidates_keep_current_display_and_rotation_anchor():
    tmp_path = make_test_dir("cache-only-empty-eligibility")
    playlist = _runtime_playlist(
        _runtime_plugin_data("one", "One"),
        _runtime_plugin_data("two", "Two"),
    )
    task, device_config, _clock = _make_runtime_task(
        tmp_path,
        playlists=[playlist],
        cycle_seconds=60,
    )
    device_config.config.update({"theme_mode": "day", "active_theme": "day"})
    anchor = "2026-07-11T11:00:00+00:00"
    device_config.refresh_info = RefreshInfo(
        refresh_type="Playlist",
        playlist=playlist.name,
        plugin_id=playlist.plugins[0].plugin_id,
        plugin_instance=playlist.plugins[0].name,
        refresh_time=anchor,
        image_hash="current",
    )
    playlist.current_plugin_index = 0
    playlist.plugin_rotation_queue = [playlist.plugins[1].instance_uuid]
    playlist.plugin_rotation_pool = [
        instance.instance_uuid for instance in playlist.plugins
    ]
    playlist.plugin_rotation_recent_history = [playlist.plugins[0].instance_uuid]
    before_rotation = (
        playlist.current_plugin_index,
        list(playlist.plugin_rotation_queue),
        list(playlist.plugin_rotation_pool),
        list(playlist.plugin_rotation_recent_history),
    )

    command = task._select_cached_display_command(
        datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)
    )

    assert command is None
    assert device_config.refresh_info.refresh_time == anchor
    assert before_rotation == (
        playlist.current_plugin_index,
        list(playlist.plugin_rotation_queue),
        list(playlist.plugin_rotation_pool),
        list(playlist.plugin_rotation_recent_history),
    )
    assert task.display_manager.calls == []


def test_cache_disappearing_after_selection_cancels_without_refresh_failure():
    tmp_path = make_test_dir("cache-only-toctou-miss")
    playlist = _runtime_playlist(_runtime_plugin_data("only", "Only"))
    task, device_config, _clock = _make_runtime_task(
        tmp_path,
        playlists=[playlist],
        cycle_seconds=60,
    )
    device_config.config.update({"theme_mode": "day", "active_theme": "day"})
    device_config.refresh_info.refresh_time = "2026-07-11T11:00:00+00:00"
    cache_path = _write_runtime_cache(task, playlist.plugins[0])
    command = task._select_cached_display_command(
        datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)
    )
    cache_path.unlink()

    submitted = task.refresh_queue.submit(command)
    task._process_queue_entry(task.refresh_queue.take(timeout=0))
    result = task.refresh_queue.get_entry(submitted.id).job

    assert result.status is JobStatus.CANCELED
    assert result.error_code == "cache_unavailable"
    assert task.runtime_state.snapshot().instances == {}
    scheduler = task.scheduler_snapshot()
    assert scheduler.last_failure_wall is None
    assert scheduler.last_error is None
    assert scheduler.retry_entries == ()
    assert task.display_manager.calls == []
    assert device_config.write_count == 0


def test_production_playlist_commands_always_have_explicit_intent():
    tree = ast.parse(inspect.getsource(refresh_task_module))
    missing = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = None
        if isinstance(node.func, ast.Attribute):
            name = node.func.attr
        if name not in {"_playlist_command", "create"}:
            continue
        if name == "create" and not (
            isinstance(node.func.value, ast.Name)
            and node.func.value.id == "RefreshCommand"
        ):
            continue
        if not any(keyword.arg == "intent" for keyword in node.keywords):
            missing.append((name, node.lineno))

    assert missing == []


def test_scheduler_enqueues_at_most_one_refresh_candidate_per_probe(monkeypatch):
    tmp_path = make_test_dir("independent-single-admission")
    playlist = _runtime_playlist(
        _runtime_plugin_data("one", "One", latest_refresh_time=None),
        _runtime_plugin_data("two", "Two", latest_refresh_time=None),
        _runtime_plugin_data("three", "Three", latest_refresh_time=None),
    )
    task, _device_config, _clock = _make_runtime_task(
        tmp_path,
        playlists=[playlist],
    )
    current_dt = datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(task, "_get_current_datetime", lambda: current_dt)
    monkeypatch.setattr(task, "_select_cached_display_command", lambda _now: None)
    monkeypatch.setattr(task, "_memory_watchdog_should_restart", lambda: False)
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
        raising=False,
    )

    task._schedule_if_due()
    entries = []
    while (entry := task.refresh_queue.take(timeout=0)) is not None:
        entries.append(entry)

    assert len(entries) == 1
    assert entries[0].command.kind is CommandKind.CACHE_REFRESH
    assert entries[0].command.intent is RefreshIntent.DATA_REFRESH


def test_display_and_due_refresh_for_same_instance_are_distinct_serial_commands(
    monkeypatch,
):
    tmp_path = make_test_dir("independent-display-and-refresh")
    current_dt = datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)
    playlist = _runtime_playlist(
        _runtime_plugin_data(
            "one",
            "One",
            latest_refresh_time=(current_dt - timedelta(hours=2)).isoformat(),
            interval=60,
        )
    )
    task, device_config, _clock = _make_runtime_task(
        tmp_path,
        playlists=[playlist],
        cycle_seconds=60,
    )
    device_config.config.update({"theme_mode": "day", "active_theme": "day"})
    device_config.refresh_info.refresh_time = (
        current_dt - timedelta(minutes=2)
    ).isoformat()
    _write_runtime_cache(task, playlist.plugins[0])
    task.runtime_state.record_success(
        playlist.plugins[0].instance_uuid,
        (current_dt - timedelta(hours=2)).isoformat(),
        lane=RefreshLane.DATA,
    )
    monkeypatch.setattr(task, "_get_current_datetime", lambda: current_dt)
    monkeypatch.setattr(task, "_memory_watchdog_should_restart", lambda: False)
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
        raising=False,
    )

    task._schedule_if_due()
    commands = []
    while (entry := task.refresh_queue.take(timeout=0)) is not None:
        commands.append(entry.command)

    assert [command.intent for command in commands] == [
        RefreshIntent.DISPLAY_CACHE,
        RefreshIntent.DATA_REFRESH,
    ]
    assert {command.instance_uuid for command in commands} == {
        playlist.plugins[0].instance_uuid
    }


def test_soft_pressure_makes_spaced_fair_progress_across_ordinary_instances(
    monkeypatch,
):
    tmp_path = make_test_dir("independent-soft-fairness")
    clock = RuntimeClock()
    current_dt = datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)
    playlist = _runtime_playlist(
        _runtime_plugin_data("one", "One", latest_refresh_time=None),
        _runtime_plugin_data("two", "Two", latest_refresh_time=None),
    )
    task, _device_config, _clock = _make_runtime_task(
        tmp_path,
        playlists=[playlist],
        clock=clock,
    )
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=100, swap_percent=0),
        raising=False,
    )

    first = task._select_independent_refresh_command(current_dt)
    immediate = task._select_independent_refresh_command(current_dt)
    task._record_runtime_attempt(first)
    clock.advance(60)
    second = task._select_independent_refresh_command(
        current_dt + timedelta(seconds=60)
    )

    assert first.intent is RefreshIntent.DATA_REFRESH
    assert immediate is None
    assert second.intent is RefreshIntent.DATA_REFRESH
    assert second.instance_uuid != first.instance_uuid


def test_hard_pressure_still_rotates_valid_caches_without_generation(monkeypatch):
    tmp_path = make_test_dir("independent-hard-display")
    current_dt = datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)
    playlist = _runtime_playlist(
        _runtime_plugin_data("one", "One", latest_refresh_time=None)
    )
    task, device_config, _clock = _make_runtime_task(
        tmp_path,
        playlists=[playlist],
        cycle_seconds=60,
    )
    device_config.config.update({"theme_mode": "day", "active_theme": "day"})
    device_config.refresh_info.refresh_time = (
        current_dt - timedelta(minutes=2)
    ).isoformat()
    _write_runtime_cache(task, playlist.plugins[0])
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=60, swap_percent=80),
        raising=False,
    )
    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda _config: (_ for _ in ()).throw(
            AssertionError("hard-tier cache display must not render")
        ),
    )

    display = task._select_cached_display_command(current_dt)
    refresh = task._select_independent_refresh_command(current_dt)
    result = task._execute_command(display)

    assert display.intent is RefreshIntent.DISPLAY_CACHE
    assert refresh is None
    assert result is not None
    assert len(task.display_manager.calls) == 1


def test_watchdog_restart_still_displays_valid_cache_and_blocks_generation(
    monkeypatch,
):
    tmp_path = make_test_dir("watchdog-hard-cache-display")
    current_dt = datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)
    playlist = _runtime_playlist(
        _runtime_plugin_data("one", "One", latest_refresh_time=None)
    )
    task, device_config, _clock = _make_runtime_task(
        tmp_path,
        playlists=[playlist],
        cycle_seconds=60,
    )
    device_config.config.update({"theme_mode": "day", "active_theme": "day"})
    device_config.refresh_info.refresh_time = (
        current_dt - timedelta(minutes=2)
    ).isoformat()
    _write_runtime_cache(task, playlist.plugins[0])

    def request_restart():
        task._restart_request = {"reason": "memory_pressure"}
        return True

    monkeypatch.setattr(task, "_get_current_datetime", lambda: current_dt)
    monkeypatch.setattr(task, "_memory_watchdog_should_restart", request_restart)
    monkeypatch.setattr(
        task,
        "_select_independent_refresh_command",
        lambda _now: pytest.fail("hard-tier watchdog admitted renderer generation"),
    )
    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda _config: pytest.fail("hard-tier cache display instantiated a plugin"),
    )

    processed = task._run_one_iteration_for_test()

    assert processed is not None
    assert processed.command.intent is RefreshIntent.DISPLAY_CACHE
    assert task.restart_request["reason"] == "memory_pressure"
    assert len(task.display_manager.calls) == 1


def test_data_failure_a_does_not_delay_due_instance_b_or_global_poll(monkeypatch):
    tmp_path = make_test_dir("independent-failure-isolation")
    clock = RuntimeClock()
    current_dt = datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)
    playlist = _runtime_playlist(
        _runtime_plugin_data("one", "One", latest_refresh_time=None),
        _runtime_plugin_data("two", "Two", latest_refresh_time=None),
    )
    task, _device_config, _clock = _make_runtime_task(
        tmp_path,
        playlists=[playlist],
        clock=clock,
    )
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
        raising=False,
    )
    first = task._select_independent_refresh_command(current_dt)
    submitted = task.refresh_queue.submit(first)
    monkeypatch.setattr(
        task,
        "_execute_command",
        lambda _command: (_ for _ in ()).throw(RuntimeError("instance offline")),
    )

    task._process_queue_entry(task.refresh_queue.take(timeout=0))
    clock.advance(task._scheduler_poll_seconds())
    second = task._select_independent_refresh_command(
        current_dt + timedelta(seconds=task._scheduler_poll_seconds())
    )

    assert task.refresh_queue.get_entry(submitted.id).job.status is JobStatus.FAILED
    assert second is not None
    assert second.instance_uuid != first.instance_uuid
    assert task.scheduler_snapshot().next_attempt_monotonic == (
        clock.monotonic()
    )


def test_live_and_theme_failure_do_not_cool_data_lane(monkeypatch):
    tmp_path = make_test_dir("independent-lane-failure-isolation")
    current_dt = datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)
    playlist = _runtime_playlist(
        _runtime_plugin_data("one", "One", latest_refresh_time=None)
    )
    task, _device_config, _clock = _make_runtime_task(
        tmp_path,
        playlists=[playlist],
    )
    instance = playlist.plugins[0].snapshot()
    live = task._playlist_command(
        playlist.name,
        instance,
        source=CommandSource.LIVE,
        intent=RefreshIntent.LIVE_REFRESH,
        kind=CommandKind.CACHE_REFRESH,
    )
    theme = task._playlist_command(
        playlist.name,
        instance,
        source=CommandSource.SCHEDULER,
        intent=RefreshIntent.THEME_REDRAW,
        kind=CommandKind.CACHE_REFRESH,
        theme_render_only=True,
    )
    task._record_intent_failure(live, RuntimeError("live failed"), current_dt)
    task._record_intent_failure(theme, RuntimeError("theme failed"), current_dt)
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
        raising=False,
    )

    selected = task._select_independent_refresh_command(current_dt)
    state = task.runtime_state.snapshot().instances[instance.instance_uuid]

    assert selected.intent is RefreshIntent.DATA_REFRESH
    assert state.data.next_retry_at is None
    assert state.live.next_retry_at is not None
    assert state.theme.next_retry_at is not None


def test_background_max_per_pass_above_one_is_compatibly_clamped_without_config_write():
    tmp_path = make_test_dir("independent-max-per-pass-clamp")
    device_config = FakeDeviceConfig(tmp_path)
    device_config.config["background_cache_refresh_max_per_pass"] = 9
    task = RefreshTask(device_config, display_manager=None)

    assert task._background_cache_refresh_max_per_pass() == 1
    assert device_config.config["background_cache_refresh_max_per_pass"] == 9
    assert device_config.write_count == 0


def _sports_live_runtime(name, *, background_value="missing"):
    tmp_path = make_test_dir(name)
    current_dt = datetime(2026, 7, 11, 12, 0, tzinfo=timezone.utc)
    plugin_data = _runtime_plugin_data(
        "sports_dashboard",
        "Sports",
        latest_refresh_time=current_dt.isoformat(),
        interval=3600,
    )
    plugin_data["plugin_settings"].update(
        {"worldCupLiveRefreshEnabled": "true"}
    )
    if background_value != "missing":
        plugin_data["plugin_settings"]["backgroundCacheRefreshEnabled"] = (
            background_value
        )
    playlist = _runtime_playlist(plugin_data)
    task, device_config, _clock = _make_runtime_task(
        tmp_path,
        playlists=[playlist],
        cycle_seconds=300,
    )
    manifest = PluginManifest(
        schema_version=2,
        id="sports_dashboard",
        class_name="SportsDashboard",
        display_name="Sports Dashboard",
        refresh_on_display=False,
        capabilities=PluginCapabilities(supports_live_refresh=True),
        raw={},
    )
    device_config.get_plugin = lambda plugin_id: {
        "id": plugin_id,
        "_manifest": manifest,
    }
    device_config.config.update({"theme_mode": "day", "active_theme": "day"})
    instance = playlist.plugins[0]
    _write_runtime_cache(task, instance)
    task.runtime_state.record_success(
        instance.instance_uuid,
        current_dt.isoformat(),
        lane=RefreshLane.DATA,
    )
    task.runtime_state.record_success(
        instance.instance_uuid,
        (current_dt - timedelta(minutes=2)).isoformat(),
        lane=RefreshLane.LIVE,
    )
    task.runtime_state.set_display_state(
        "committed",
        instance_uuid=instance.instance_uuid,
        changed_at=(current_dt - timedelta(minutes=1)).isoformat(),
    )
    anchor = (current_dt - timedelta(minutes=1)).isoformat()
    device_config.refresh_info = RefreshInfo(
        refresh_type="Playlist",
        playlist=playlist.name,
        plugin_id=instance.plugin_id,
        plugin_instance=instance.name,
        refresh_time=anchor,
        image_hash="old",
    )
    return task, device_config, playlist, instance, current_dt, anchor


def _assert_sports_normal_selected(monkeypatch, background_value):
    task, _device_config, _playlist, instance, current_dt, _anchor = (
        _sports_live_runtime(
            f"sports-normal-{background_value}",
            background_value=background_value,
        )
    )
    task.runtime_state.record_success(
        instance.instance_uuid,
        (current_dt - timedelta(hours=2)).isoformat(),
        lane=RefreshLane.DATA,
    )
    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda _config: FakePlugin([], live_state=None),
    )
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
    )

    command = task._select_independent_refresh_command(current_dt)

    assert command is not None
    assert command.instance_uuid == instance.instance_uuid
    assert command.intent is RefreshIntent.DATA_REFRESH


def test_sports_normal_interval_is_selected_when_background_flag_is_missing(
    monkeypatch,
):
    _assert_sports_normal_selected(monkeypatch, "missing")


def test_sports_normal_interval_is_selected_when_background_flag_is_false(
    monkeypatch,
):
    _assert_sports_normal_selected(monkeypatch, False)


@pytest.mark.parametrize(
    ("enabled", "hook_active", "displayed", "sample", "expected_live"),
    [
        (True, True, True, ResourceSample(512, 0), True),
        (True, False, True, ResourceSample(512, 0), False),
        (True, True, False, ResourceSample(512, 0), False),
        (True, True, True, ResourceSample(100, 0), False),
        (False, True, True, ResourceSample(512, 0), False),
    ],
)
def test_sports_live_requires_enabled_hook_displayed_uuid_and_healthy_tier(
    monkeypatch,
    enabled,
    hook_active,
    displayed,
    sample,
    expected_live,
):
    task, _device_config, _playlist, instance, current_dt, _anchor = (
        _sports_live_runtime(
            "sports-live-gates",
            background_value=enabled,
        )
    )
    if not displayed:
        task.runtime_state.set_display_state(
            "committed",
            instance_uuid="different-instance",
            changed_at=current_dt.isoformat(),
        )
    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda _config: FakePlugin(
            [],
            live_state=(
                {"active": True, "interval_seconds": 60}
                if hook_active
                else None
            ),
        ),
    )
    monkeypatch.setattr(task, "_resource_sample", lambda: sample)

    command = task._select_independent_refresh_command(current_dt)

    assert (command is not None) is expected_live
    if expected_live:
        assert command.intent is RefreshIntent.LIVE_REFRESH


def test_explicit_false_legacy_background_flag_is_live_master_off_only(
    monkeypatch,
):
    task, _device_config, _playlist, instance, current_dt, _anchor = (
        _sports_live_runtime(
            "sports-live-master-off-data-on",
            background_value=False,
        )
    )
    task.runtime_state.record_success(
        instance.instance_uuid,
        (current_dt - timedelta(hours=2)).isoformat(),
        lane=RefreshLane.DATA,
    )
    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda _config: FakePlugin(
            [],
            live_state={"active": True, "interval_seconds": 60},
        ),
    )
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
    )

    command = task._select_independent_refresh_command(current_dt)

    assert command.intent is RefreshIntent.DATA_REFRESH


def test_sports_live_success_queues_cache_only_followup_without_moving_anchor(
    monkeypatch,
):
    task, device_config, _playlist, instance, current_dt, anchor = (
        _sports_live_runtime(
            "sports-live-followup",
            background_value=True,
        )
    )
    calls = []
    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda _config: FakePlugin(
            calls,
            live_state={"active": True, "interval_seconds": 60},
        ),
    )
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
    )
    monkeypatch.setattr(task, "_get_current_datetime", lambda: current_dt)
    command = task._select_independent_refresh_command(current_dt)

    submitted = task.refresh_queue.submit(command)
    task._process_queue_entry(task.refresh_queue.take(timeout=0))
    followup = task.refresh_queue.take(timeout=0)

    assert task.refresh_queue.get_entry(submitted.id).job.status is JobStatus.SUCCEEDED
    assert followup is not None
    assert followup.command.intent is RefreshIntent.DISPLAY_CACHE
    assert followup.command.instance_uuid == instance.instance_uuid
    task._process_queue_entry(followup)
    assert calls == ["sports_dashboard"]
    assert device_config.refresh_info.refresh_time == anchor


def test_live_exact_followup_does_not_merge_with_pending_manual_display(
    monkeypatch,
):
    task, _device_config, playlist, instance, current_dt, _anchor = (
        _sports_live_runtime(
            "sports-live-exact-followup-scope",
            background_value=True,
        )
    )
    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda _config: FakePlugin(
            [],
            live_state={"active": True, "interval_seconds": 60},
        ),
    )
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
    )
    monkeypatch.setattr(task, "_get_current_datetime", lambda: current_dt)
    live = task._select_independent_refresh_command(current_dt)
    task.refresh_queue.submit(live)
    running_live = task.refresh_queue.take(timeout=0)
    manual = task._playlist_command(
        playlist.name,
        instance.snapshot(),
        source=CommandSource.MANUAL,
        intent=RefreshIntent.DISPLAY_CACHE,
        display_cached_only=True,
        priority=100,
        current_dt=current_dt,
        cache_theme_mode=None,
        require_active=False,
    )
    task.refresh_queue.submit(manual)

    task._process_queue_entry(running_live)
    entries = [
        task.refresh_queue.take(timeout=0),
        task.refresh_queue.take(timeout=0),
    ]
    assert all(entry is not None for entry in entries)
    pending = [entry.command for entry in entries]
    exact = next(command for command in pending if command.source is CommandSource.LIVE)
    retained_manual = next(
        command for command in pending if command.source is CommandSource.MANUAL
    )

    assert retained_manual.payload["require_active"] is False
    assert exact.payload["expected_displayed_instance_uuid"] == instance.instance_uuid
    assert exact.coalescing_scope is not None
    assert exact.coalescing_scope != retained_manual.coalescing_scope


def test_sports_live_success_does_not_advance_normal_data_cadence(monkeypatch):
    task, _device_config, _playlist, instance, current_dt, _anchor = (
        _sports_live_runtime(
            "sports-live-lane-clock",
            background_value=True,
        )
    )
    data_success = task.runtime_state.snapshot().instances[
        instance.instance_uuid
    ].data.last_success_at
    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda _config: FakePlugin(
            [],
            live_state={"active": True, "interval_seconds": 60},
        ),
    )
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
    )
    monkeypatch.setattr(task, "_get_current_datetime", lambda: current_dt)
    command = task._select_independent_refresh_command(current_dt)

    submitted = task.refresh_queue.submit(command)
    task._process_queue_entry(task.refresh_queue.take(timeout=0))
    state = task.runtime_state.snapshot().instances[instance.instance_uuid]

    assert task.refresh_queue.get_entry(submitted.id).job.status is JobStatus.SUCCEEDED
    assert state.data.last_success_at == data_success
    assert state.live.last_success_at == current_dt.isoformat()


def _seed_theme_last_good(task, instance, mode, succeeded_at):
    task.runtime_state.record_success(
        instance.instance_uuid,
        succeeded_at.isoformat(),
        lane=RefreshLane.DATA,
        last_good_cache=LastGoodCacheState(
            theme_mode=mode,
            structural_generation=instance.structural_generation,
            settings_revision=instance.settings_revision,
            promoted_at=succeeded_at.isoformat(),
        ),
    )


def _prepare_independent_theme_candidate(task, playlist, current_dt):
    snapshots = [instance.snapshot() for instance in playlist.plugins]
    for instance in snapshots:
        _write_runtime_theme_cache(task, instance, "day")
        _seed_theme_last_good(
            task,
            instance,
            "day",
            current_dt - timedelta(minutes=10),
        )
    return snapshots[0]


def test_theme_redraw_is_cache_refresh_intent_not_display_intent(monkeypatch):
    task, _device_config, playlist, _configs = _theme_transition_runtime(
        "independent-theme-intent"
    )
    current_dt = datetime(2026, 7, 11, 22, 0, tzinfo=timezone.utc)
    instance = _prepare_independent_theme_candidate(task, playlist, current_dt)
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
    )

    command = task._select_independent_refresh_command(current_dt)

    assert command.kind is CommandKind.CACHE_REFRESH
    assert command.intent is RefreshIntent.THEME_REDRAW
    assert command.source is CommandSource.SCHEDULER
    assert command.instance_uuid == instance.instance_uuid
    assert command.force is False


def test_theme_redraw_sets_theme_render_only_and_preserves_data_live_clocks(
    monkeypatch,
):
    task, _device_config, playlist, configs = _theme_transition_runtime(
        "independent-theme-lane-clocks"
    )
    current_dt = datetime(2026, 7, 11, 22, 0, tzinfo=timezone.utc)
    instance = _prepare_independent_theme_candidate(task, playlist, current_dt)
    live_success = current_dt - timedelta(minutes=9)
    task.runtime_state.record_success(
        instance.instance_uuid,
        live_success.isoformat(),
        lane=RefreshLane.LIVE,
    )
    before = task.runtime_state.snapshot().instances[instance.instance_uuid]
    plugin = ThemeOnlyRecordingPlugin(configs["displayed"], color="white")
    monkeypatch.setattr("src.refresh_task.get_plugin_instance", lambda _config: plugin)
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
    )
    monkeypatch.setattr(task, "_get_current_datetime", lambda: current_dt)
    command = task._select_independent_refresh_command(current_dt)

    submitted = task.refresh_queue.submit(command)
    task._process_queue_entry(task.refresh_queue.take(timeout=0))
    after = task.runtime_state.snapshot().instances[instance.instance_uuid]

    assert task.refresh_queue.get_entry(submitted.id).job.status is JobStatus.SUCCEEDED
    assert command.payload["theme_render_only"] is True
    assert before.data.last_success_at == after.data.last_success_at
    assert before.live.last_success_at == after.live.last_success_at
    assert after.theme.last_success_at == current_dt.isoformat()
    assert after.last_good_cache.theme_mode == "night"
    followup = task.refresh_queue.take(timeout=0)
    assert followup.command.intent is RefreshIntent.DISPLAY_CACHE


def test_theme_exact_followup_does_not_merge_with_pending_manual_display(
    monkeypatch,
):
    task, _device_config, playlist, configs = _theme_transition_runtime(
        "independent-theme-exact-followup-scope"
    )
    current_dt = datetime(2026, 7, 11, 22, 0, tzinfo=timezone.utc)
    instance = _prepare_independent_theme_candidate(task, playlist, current_dt)
    plugin = ThemeOnlyRecordingPlugin(configs["displayed"], color="white")
    monkeypatch.setattr("src.refresh_task.get_plugin_instance", lambda _config: plugin)
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
    )
    monkeypatch.setattr(task, "_get_current_datetime", lambda: current_dt)
    theme = task._select_independent_refresh_command(current_dt)
    task.refresh_queue.submit(theme)
    running_theme = task.refresh_queue.take(timeout=0)
    manual = task._playlist_command(
        playlist.name,
        instance,
        source=CommandSource.MANUAL,
        intent=RefreshIntent.DISPLAY_CACHE,
        display_cached_only=True,
        priority=100,
        current_dt=current_dt,
        cache_theme_mode="day",
        require_active=False,
    )
    task.refresh_queue.submit(manual)

    task._process_queue_entry(running_theme)
    entries = [
        task.refresh_queue.take(timeout=0),
        task.refresh_queue.take(timeout=0),
    ]
    assert all(entry is not None for entry in entries)
    pending = [entry.command for entry in entries]
    exact = next(
        command for command in pending if command.source is CommandSource.SCHEDULER
    )
    retained_manual = next(
        command for command in pending if command.source is CommandSource.MANUAL
    )

    assert retained_manual.payload["require_active"] is False
    assert retained_manual.payload["cache_theme_mode"] == "day"
    assert exact.payload["cache_theme_mode"] == "night"
    assert exact.payload["resolved_theme_context"]["mode"] == "night"
    assert exact.payload["expected_displayed_instance_uuid"] == instance.instance_uuid
    assert exact.payload["preserve_rotation_anchor"] is True
    assert exact.coalescing_scope is not None
    assert exact.coalescing_scope != retained_manual.coalescing_scope


def test_theme_redraw_preserves_rotation_anchor_and_exact_displayed_no_fallback(
    monkeypatch,
):
    current_dt = datetime(2026, 7, 11, 22, 0, tzinfo=timezone.utc)
    task, device_config, playlist, configs = _theme_transition_runtime(
        "independent-theme-exact-display"
    )
    instance = _prepare_independent_theme_candidate(task, playlist, current_dt)
    anchor = device_config.refresh_info.refresh_time
    plugin = ThemeOnlyRecordingPlugin(configs["displayed"], color="white")
    monkeypatch.setattr("src.refresh_task.get_plugin_instance", lambda _config: plugin)
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
    )
    monkeypatch.setattr(task, "_get_current_datetime", lambda: current_dt)
    command = task._select_independent_refresh_command(current_dt)

    assert command.payload["expected_displayed_instance_uuid"] == instance.instance_uuid
    task.refresh_queue.submit(command)
    task._process_queue_entry(task.refresh_queue.take(timeout=0))
    task._process_queue_entry(task.refresh_queue.take(timeout=0))
    assert device_config.refresh_info.refresh_time == anchor

    missing, missing_config, missing_playlist, _missing_configs = (
        _theme_transition_runtime(
            "independent-theme-no-refresh-info-fallback",
            displayed_uuid=None,
        )
    )
    _prepare_independent_theme_candidate(missing, missing_playlist, current_dt)
    monkeypatch.setattr(
        missing,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
    )

    assert missing._select_independent_refresh_command(current_dt) is None
    assert missing_config.config["active_theme"] == "night"
    assert missing_config.write_count == 1


@pytest.mark.parametrize("source_mode", ["day", None])
def test_media_theme_redraw_reuses_opposite_or_legacy_uuid_cache_with_zero_provider_calls(
    monkeypatch,
    source_mode,
):
    task, device_config, playlist, configs = _theme_transition_runtime(
        f"independent-theme-media-{source_mode or 'legacy'}"
    )
    current_dt = datetime(2026, 7, 11, 22, 0, tzinfo=timezone.utc)
    instance = playlist.plugins[0].snapshot()
    configs["displayed"]["_manifest"] = _theme_manifest(
        "displayed",
        presentation="media",
    )
    device_config.get_resolution = lambda: (40, 24)
    source = Image.new("RGB", (40, 24), (180, 20, 30))
    source_path = Path(task._snapshot_cache_path(instance, source_mode))
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source.save(source_path)
    _seed_theme_last_good(
        task,
        instance,
        source_mode,
        current_dt - timedelta(minutes=10),
    )
    fallback = playlist.plugins[1].snapshot()
    _write_runtime_theme_cache(task, fallback, "day")
    _seed_theme_last_good(
        task,
        fallback,
        "day",
        current_dt - timedelta(minutes=10),
    )
    provider_calls = []
    plugin = ThemeOnlyRecordingPlugin(configs["displayed"], fail=True)
    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda _config: provider_calls.append("provider") or plugin,
    )
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
    )
    monkeypatch.setattr(task, "_get_current_datetime", lambda: current_dt)
    command = task._select_independent_refresh_command(current_dt)

    task.refresh_queue.submit(command)
    task._process_queue_entry(task.refresh_queue.take(timeout=0))
    task._process_queue_entry(task.refresh_queue.take(timeout=0))

    assert plugin.calls == []
    assert provider_calls == ["provider"]
    assert Path(task._snapshot_cache_path(instance, "night")).exists()
    assert device_config.refresh_info.refresh_time == "2026-07-11T21:59:00+00:00"


def test_nonvisible_opposite_cache_is_last_good_not_lazy_display_generation(
    monkeypatch,
):
    tmp_path = make_test_dir("nonvisible-opposite-last-good")
    plugin_data = _runtime_plugin_data("themed_plugin", "Themed Plugin")
    plugin_data["plugin_settings"]["themeMode"] = "auto"
    playlist = _runtime_playlist(plugin_data)
    task, device_config, _clock = _make_runtime_task(
        tmp_path,
        playlists=[playlist],
        cycle_seconds=300,
    )
    current_dt = datetime(2026, 7, 11, 22, 0, tzinfo=timezone.utc)
    device_config.config.update({"theme_mode": "night", "active_theme": "night"})
    device_config.refresh_info = RefreshInfo(
        refresh_type="Playlist",
        playlist=playlist.name,
        plugin_id="previous_plugin",
        plugin_instance="Previous Plugin",
        refresh_time=(current_dt - timedelta(minutes=10)).isoformat(),
        image_hash="previous",
    )
    device_config.get_plugin = lambda plugin_id: {
        "id": plugin_id,
        "_manifest": _theme_manifest(plugin_id),
    }
    task.runtime_state.set_display_state(
        "committed",
        instance_uuid="another-visible-instance",
        changed_at=current_dt.isoformat(),
    )
    instance = playlist.plugins[0].snapshot()
    _write_runtime_theme_cache(task, instance, "day")
    _seed_theme_last_good(
        task,
        instance,
        "day",
        current_dt - timedelta(minutes=10),
    )
    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda _config: pytest.fail("DISPLAY_CACHE instantiated a plugin"),
    )
    monkeypatch.setattr(task, "_get_current_datetime", lambda: current_dt)

    command = task._select_cached_display_command(current_dt)
    result = task._execute_command(command)

    assert command.intent is RefreshIntent.DISPLAY_CACHE
    assert command.payload["cache_theme_mode"] == "day"
    assert result is not None
    assert not Path(task._snapshot_cache_path(instance, "night")).exists()


def test_theme_failure_cools_theme_lane_only_and_keeps_last_good(monkeypatch):
    task, _device_config, playlist, configs = _theme_transition_runtime(
        "independent-theme-failure-lane"
    )
    current_dt = datetime(2026, 7, 11, 22, 0, tzinfo=timezone.utc)
    instance = _prepare_independent_theme_candidate(task, playlist, current_dt)
    live_success = current_dt - timedelta(minutes=9)
    task.runtime_state.record_success(
        instance.instance_uuid,
        live_success.isoformat(),
        lane=RefreshLane.LIVE,
    )
    before = task.runtime_state.snapshot().instances[instance.instance_uuid]
    plugin = ThemeOnlyRecordingPlugin(configs["displayed"], fail=True)
    monkeypatch.setattr("src.refresh_task.get_plugin_instance", lambda _config: plugin)
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
    )
    monkeypatch.setattr(task, "_get_current_datetime", lambda: current_dt)
    command = task._select_independent_refresh_command(current_dt)

    submitted = task.refresh_queue.submit(command)
    task._process_queue_entry(task.refresh_queue.take(timeout=0))
    failed = task.runtime_state.snapshot().instances[instance.instance_uuid]

    assert task.refresh_queue.get_entry(submitted.id).job.status is JobStatus.FAILED
    assert failed.data.last_success_at == before.data.last_success_at
    assert failed.live.last_success_at == before.live.last_success_at
    assert failed.theme.next_retry_at is not None
    assert failed.last_good_cache == before.last_good_cache

    task.runtime_state.record_success(
        instance.instance_uuid,
        (current_dt - timedelta(hours=2)).isoformat(),
        lane=RefreshLane.DATA,
    )
    next_command = task._select_independent_refresh_command(
        current_dt + timedelta(seconds=1)
    )
    assert next_command.intent is RefreshIntent.DATA_REFRESH


def test_startup_seeds_data_clock_from_valid_model_latest_refresh_only(monkeypatch):
    tmp_path = make_test_dir("startup-data-clock-seed")
    valid_time = "2026-07-11T20:00:00+00:00"
    playlist = _runtime_playlist(
        _runtime_plugin_data(
            "valid_latest",
            "Valid Latest",
            latest_refresh_time=valid_time,
        ),
        _runtime_plugin_data(
            "invalid_latest",
            "Invalid Latest",
            latest_refresh_time="not-a-timestamp",
        ),
        _runtime_plugin_data(
            "missing_latest",
            "Missing Latest",
            latest_refresh_time=None,
        ),
    )
    task, _device_config, _clock = _make_runtime_task(tmp_path, playlists=[playlist])
    monkeypatch.setattr(task.runtime_state, "flush", lambda: True)

    task._prune_runtime_state()
    states = task.runtime_state.snapshot().instances

    assert states[playlist.plugins[0].instance_uuid].data.last_success_at == valid_time
    assert states.get(playlist.plugins[1].instance_uuid) is None
    assert states.get(playlist.plugins[2].instance_uuid) is None


def test_startup_discovers_only_valid_exact_revision_last_good_cache(monkeypatch):
    tmp_path = make_test_dir("startup-last-good-discovery")
    playlist = _runtime_playlist(
        _runtime_plugin_data("valid_cache", "Valid Cache"),
        _runtime_plugin_data("invalid_cache", "Invalid Cache"),
    )
    task, _device_config, _clock = _make_runtime_task(tmp_path, playlists=[playlist])
    valid = playlist.plugins[0].snapshot()
    invalid = playlist.plugins[1].snapshot()
    valid_path = Path(task._snapshot_cache_path(valid, "day"))
    valid_path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (2, 1), "white").save(valid_path)
    corrupt_current = Path(task._snapshot_cache_path(invalid, "night"))
    corrupt_current.write_bytes(b"not-an-image")
    stale_path = Path(
        authoritative_cache_path(
            task.cache_catalog.cache_root,
            invalid.instance_uuid,
            invalid.structural_generation,
            invalid.settings_revision + 1,
            "day",
        )
    )
    Image.new("RGB", (2, 1), "black").save(stale_path)
    monkeypatch.setattr(task.runtime_state, "flush", lambda: True)

    task._prune_runtime_state()
    states = task.runtime_state.snapshot().instances

    assert states[valid.instance_uuid].last_good_cache.theme_mode == "day"
    assert states[valid.instance_uuid].last_good_cache.structural_generation == 1
    assert states[valid.instance_uuid].last_good_cache.settings_revision == 1
    assert states[invalid.instance_uuid].last_good_cache is None


def test_startup_migration_does_not_write_playlist_or_user_settings(monkeypatch):
    tmp_path = make_test_dir("startup-migration-read-only-config")
    plugin_data = _runtime_plugin_data(
        "migration_target",
        "Migration Target",
        latest_refresh_time="2026-07-11T20:00:00+00:00",
    )
    plugin_data["plugin_settings"].update({"city": "Seattle", "units": "metric"})
    playlist = _runtime_playlist(plugin_data)
    task, device_config, _clock = _make_runtime_task(tmp_path, playlists=[playlist])
    instance = playlist.plugins[0].snapshot()
    _write_runtime_cache(task, instance)
    before_manager = device_config.get_playlist_manager().to_dict()
    before_config = dict(device_config.config)
    monkeypatch.setattr(task.runtime_state, "flush", lambda: True)

    task._prune_runtime_state()

    assert device_config.write_count == 0
    assert device_config.get_playlist_manager().to_dict() == before_manager
    assert device_config.config == before_config


PRESENTATION_NOW = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)


def test_presentation_instance_identity_rejects_missing_and_json_spoofed_values():
    reserved_key = presentation_contract._PRESENTATION_INSTANCE_IDENTITY_KEY
    spoofed = json.loads(
        json.dumps(
            {
                reserved_key: {
                    "instance_uuid": "json-controlled-instance",
                }
            }
        )
    )

    assert presentation_contract.get_presentation_instance_uuid({}) is None
    assert presentation_contract.get_presentation_instance_uuid(spoofed) is None


def test_presentation_instance_identity_binding_overwrites_spoof_without_mutation():
    reserved_key = presentation_contract._PRESENTATION_INSTANCE_IDENTITY_KEY
    instance_uuid = "trusted-playlist-instance"
    original = {
        "city": "Fremont",
        reserved_key: "json-spoof",
    }
    before = dict(original)

    bound = presentation_contract.bind_presentation_instance_identity(
        original,
        instance_uuid,
    )

    assert bound is not original
    assert original == before
    assert bound["city"] == "Fremont"
    assert bound[reserved_key] != "json-spoof"
    assert presentation_contract.get_presentation_instance_uuid(bound) == instance_uuid
    assert instance_uuid not in repr(bound[reserved_key])


@pytest.mark.parametrize("instance_uuid", [None, "", "   ", " padded-instance "])
def test_presentation_instance_identity_binding_rejects_invalid_uuid(instance_uuid):
    with pytest.raises((TypeError, ValueError), match="instance_uuid"):
        presentation_contract.bind_presentation_instance_identity({}, instance_uuid)


def _presentation_manifest(plugin_id="presentation_plugin"):
    return PluginManifest(
        schema_version=2,
        id=plugin_id,
        class_name="PresentationPlugin",
        display_name="Presentation Plugin",
        refresh_on_display=True,
        capabilities=PluginCapabilities(supports_presentation_refresh=True),
        raw={},
    )


class PresentationTransactionDisplayManager:
    def __init__(self, *, after_display=None):
        self.calls = []
        self.bound_runtime_state = None
        self.after_display = after_display

    def bind_runtime_state(self, runtime_state):
        self.bound_runtime_state = runtime_state
        return object()

    def display_image(
        self,
        image,
        image_settings=(),
        *,
        task_context=None,
        logical_target=None,
        instance_revision=None,
    ):
        commit_id = uuid.uuid4().hex
        committed_at = PRESENTATION_NOW.isoformat()
        call = {
            "commit_id": commit_id,
            "committed_at": committed_at,
            "image": image.copy(),
            "image_settings": tuple(image_settings),
            "task_context": task_context,
            "logical_target": dict(logical_target or {}),
            "instance_revision": instance_revision,
        }
        self.calls.append(call)
        if self.bound_runtime_state is not None:
            self.bound_runtime_state.set_display_state(
                "committed",
                commit_id,
                instance_uuid=call["logical_target"].get("instance_uuid"),
                changed_at=committed_at,
            )
        if self.after_display is not None:
            self.after_display(self, call)
        return SimpleNamespace(commit_id=commit_id, committed_at=committed_at)


class PresentationBankPlugin(DelegatingThemeWrapper):
    def __init__(self, *, changed=True, prepared_color="white", data_color="gray"):
        self.changed = changed
        self.prepared_color = prepared_color
        self.data_color = data_color
        self.events = []
        self.contexts = []
        self.identity_events = []
        self.config = {}

    def presentation_mode(self, settings):
        self.identity_events.append(
            ("mode", presentation_contract.get_presentation_instance_uuid(settings))
        )
        self.events.append(("mode", dict(settings or {})))
        return PresentationMode.PREPARED_BANK

    def reconcile_presentation_receipt(self, settings, receipt):
        self.identity_events.append(
            ("reconcile", presentation_contract.get_presentation_instance_uuid(settings))
        )
        self.events.append(("reconcile", receipt))

    def prepare_presentation(
        self,
        settings,
        device_config,
        *,
        request,
        resolved_theme_context,
    ):
        assert isinstance(request, PresentationRequestContext)
        self.identity_events.append(
            ("prepare", presentation_contract.get_presentation_instance_uuid(settings))
        )
        self.events.append(("prepare", request.request_id))
        self.contexts.append(request)
        image = Image.new("RGB", (32, 16), self.prepared_color) if self.changed else None
        return PresentationPreparation(
            request_id=request.request_id,
            image=image,
            changed=self.changed,
        )

    def generate_image(self, settings, device_config):
        self.identity_events.append(
            ("generate", presentation_contract.get_presentation_instance_uuid(settings))
        )
        self.events.append(("generate", dict(settings or {})))
        return Image.new("RGB", (32, 16), self.data_color)


class BaseCopyIdentityPlugin(BasePlugin):
    def __init__(self):
        self.config = {}
        self.events = []
        self.identity_events = []

    def resolve_theme(self, settings, device_config, now=None):
        return {"mode": "day"}

    def presentation_mode(self, settings):
        self.identity_events.append(
            ("mode", presentation_contract.get_presentation_instance_uuid(settings))
        )
        self.events.append(("mode", dict(settings or {})))
        return PresentationMode.PREPARED_BANK

    def reconcile_presentation_receipt(self, settings, receipt):
        self.identity_events.append(
            ("reconcile", presentation_contract.get_presentation_instance_uuid(settings))
        )
        self.events.append(("reconcile", receipt))

    def generate_image(self, settings, device_config):
        self.identity_events.append(
            ("generate", presentation_contract.get_presentation_instance_uuid(settings))
        )
        self.events.append(("generate", dict(settings or {})))
        return Image.new("RGB", (32, 16), "gray")


class NoChangePresentationPlugin(PresentationBankPlugin):
    def presentation_mode(self, settings):
        self.events.append(("mode", dict(settings or {})))
        return PresentationMode.NO_CHANGE

    def prepare_presentation(self, *args, **kwargs):
        pytest.fail("NO_CHANGE must not call the preparation hook")


class LegacyPresentationPlugin(PresentationBankPlugin):
    def presentation_mode(self, settings):
        self.events.append(("mode", dict(settings or {})))
        return PresentationMode.LEGACY_ASYNC

    def prepare_presentation(self, *args, **kwargs):
        pytest.fail("LEGACY_ASYNC must remain disabled")


def _make_presentation_task(
    name,
    *,
    plugin_count=1,
    latest_refresh_time="2999-01-01T00:00:00+00:00",
    interval=3600,
    clock=None,
    display_manager=None,
):
    tmp_path = make_test_dir(name)
    plugins = [
        _runtime_plugin_data(
            f"presentation_plugin_{index}",
            f"Presentation Plugin {index}",
            latest_refresh_time=latest_refresh_time,
            interval=interval,
        )
        for index in range(plugin_count)
    ]
    for plugin in plugins:
        plugin["plugin_settings"]["refreshOnDisplay"] = True
    playlist = _runtime_playlist(*plugins, name="Presentation Playlist")
    clock = clock or RuntimeClock()
    device_config = RuntimeDeviceConfig(tmp_path, [playlist])
    device_config.config.update(
        {
            "active_theme": "day",
            "theme_mode": "day",
            "plugin_cycle_interval_seconds": 60,
        }
    )
    manifests = {plugin["plugin_id"]: _presentation_manifest(plugin["plugin_id"]) for plugin in plugins}
    device_config.get_plugin = lambda plugin_id: {
        "id": plugin_id,
        "refresh_on_display": True,
        "_manifest": manifests[plugin_id],
    }
    display_manager = display_manager or PresentationTransactionDisplayManager()
    task = RefreshTask(
        device_config,
        display_manager,
        clock=clock.monotonic,
        wall_clock=clock.wall_time,
    )
    return task, device_config, clock, playlist, display_manager


def _install_display_provider_plugin_sentinels(monkeypatch):
    def plugin_sentinel(*_args, **_kwargs):
        pytest.fail("DISPLAY_CACHE instantiated a plugin")

    def provider_sentinel(*_args, **_kwargs):
        pytest.fail("DISPLAY_CACHE reached a provider/live hook")

    monkeypatch.setattr(refresh_task_module, "get_plugin_instance", plugin_sentinel)
    monkeypatch.setattr(
        refresh_task_module,
        "_plugin_live_refresh_state",
        provider_sentinel,
    )


def _seed_presentation_request(
    task,
    instance,
    *,
    request_id=None,
    requested_at=PRESENTATION_NOW,
    origin_commit_id="origin-display-commit",
    origin_theme_mode=None,
):
    request = PresentationRequestState(
        request_id=request_id or uuid.uuid4().hex,
        requested_at=requested_at.isoformat(),
        structural_generation=instance.structural_generation,
        settings_revision=instance.settings_revision,
        origin_theme_mode=origin_theme_mode,
        origin_display_commit_id=origin_commit_id,
    )
    assert task.runtime_state.request_presentation(instance.instance_uuid, request)
    return request


def _prepared_presentation_candidate(task, instance, request, theme_mode=None):
    root = Path(task.device_config.plugin_image_dir) / ".refresh-presentation"
    return PreparedPresentationCandidate(
        instance_uuid=instance.instance_uuid,
        structural_generation=instance.structural_generation,
        settings_revision=instance.settings_revision,
        theme_mode=theme_mode,
        request_id=request.request_id,
        cache_path=prepared_presentation_path(
            root,
            instance.instance_uuid,
            instance.structural_generation,
            instance.settings_revision,
            theme_mode,
            request.request_id,
        ),
    )


def _seed_prepared_presentation(
    task,
    instance,
    request,
    *,
    image=None,
    theme_mode=None,
):
    candidate = _prepared_presentation_candidate(
        task,
        instance,
        request,
        theme_mode,
    )
    PresentationCache(Path(task.device_config.plugin_image_dir) / ".refresh-presentation").save(
        candidate,
        image or Image.new("RGB", (32, 16), "white"),
    )
    assert task.runtime_state.mark_presentation_prepared(
        instance.instance_uuid,
        request.request_id,
        (PRESENTATION_NOW + timedelta(seconds=1)).isoformat(),
        theme_mode,
    )
    return candidate


def _non_presentation_lane_bytes(state):
    return json.dumps(
        {lane: getattr(state, lane).__dict__ for lane in ("data", "live", "theme")},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _seed_independent_lane_clocks(task, instance):
    for lane, offset in (
        (RefreshLane.DATA, 10),
        (RefreshLane.LIVE, 20),
        (RefreshLane.THEME, 30),
    ):
        task.runtime_state.record_success(
            instance.instance_uuid,
            (PRESENTATION_NOW - timedelta(minutes=offset)).isoformat(),
            lane=lane,
        )


def _queue_and_process(task, command):
    submitted = task.refresh_queue.submit(command)
    entry = task.refresh_queue.take(timeout=0)
    assert entry is not None
    assert entry.job.id == submitted.id
    task._process_queue_entry(entry)
    return task.refresh_queue.get_entry(submitted.id)


def _normal_cache_display_command(task, playlist, instance, *, source=CommandSource.SCHEDULER):
    return task._playlist_command(
        playlist.name,
        instance,
        source=source,
        intent=RefreshIntent.DISPLAY_CACHE,
        force=False,
        display_cached_only=True,
        priority=50 if source is CommandSource.SCHEDULER else 100,
        current_dt=PRESENTATION_NOW,
        cache_theme_mode=None,
    )


def _presentation_followup_command(task, playlist, instance, request):
    return task._playlist_command(
        playlist.name,
        instance,
        source=CommandSource.BACKGROUND,
        intent=RefreshIntent.DISPLAY_CACHE,
        force=False,
        display_cached_only=True,
        priority=65,
        kind=CommandKind.DISPLAY,
        current_dt=PRESENTATION_NOW,
        cache_theme_mode=None,
        expected_displayed_instance_uuid=instance.instance_uuid,
        preserve_rotation_anchor=True,
        coalescing_scope=f"presentation-followup:{request.request_id}",
        allow_prepared_presentation=True,
        presentation_request_id=request.request_id,
    )


def test_normal_cache_commit_records_one_coalesced_presentation_request(monkeypatch):
    task, device_config, clock, playlist, _display = _make_presentation_task("presentation-normal-display-request")
    instance = playlist.plugins[0].snapshot()
    _write_runtime_cache(task, instance, Image.new("RGB", (32, 16), "black"))
    now = [PRESENTATION_NOW]
    device_config.refresh_info.refresh_time = (now[0] - timedelta(minutes=2)).isoformat()
    monkeypatch.setattr(task, "_get_current_datetime", lambda: now[0])
    monkeypatch.setattr(task, "_memory_watchdog_should_restart", lambda: False)
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
    )
    _install_display_provider_plugin_sentinels(monkeypatch)

    task._schedule_if_due()
    first = task.refresh_queue.take(timeout=0)
    assert first is not None
    assert first.command.intent is RefreshIntent.DISPLAY_CACHE
    assert first.command.allow_prepared_presentation is True
    task._process_queue_entry(first)
    original = task.runtime_state.snapshot().instances[instance.instance_uuid].presentation_request

    assert original is not None
    device_config.refresh_info.refresh_time = (now[0] - timedelta(minutes=2)).isoformat()
    clock.advance(61)
    now[0] += timedelta(seconds=61)
    task._schedule_if_due()
    second = task.refresh_queue.take(timeout=0)
    assert second is not None
    assert second.command.intent is RefreshIntent.DISPLAY_CACHE
    task._process_queue_entry(second)

    assert task.runtime_state.snapshot().instances[instance.instance_uuid].presentation_request == original


def test_manual_cache_display_records_request_but_live_theme_followups_do_not(
    monkeypatch,
):
    _install_display_provider_plugin_sentinels(monkeypatch)
    results = {}
    for label, source, scope, expected_request in (
        ("manual", CommandSource.MANUAL, None, True),
        ("live", CommandSource.LIVE, "live-followup:source-command", False),
        (
            "theme",
            CommandSource.SCHEDULER,
            "theme-followup:source-command",
            False,
        ),
    ):
        task, _config, _clock, playlist, _display = _make_presentation_task(f"presentation-{label}-display-rule")
        instance = playlist.plugins[0].snapshot()
        _write_runtime_cache(task, instance, Image.new("RGB", (32, 16), "black"))
        task.runtime_state.set_display_state(
            "committed",
            f"{label}-origin",
            instance_uuid=instance.instance_uuid,
            changed_at=PRESENTATION_NOW.isoformat(),
        )
        command = task._playlist_command(
            playlist.name,
            instance,
            source=source,
            intent=RefreshIntent.DISPLAY_CACHE,
            force=False,
            display_cached_only=True,
            priority=100 if label == "manual" else 75,
            current_dt=PRESENTATION_NOW,
            cache_theme_mode=None,
            expected_displayed_instance_uuid=(None if label == "manual" else instance.instance_uuid),
            preserve_rotation_anchor=label == "theme",
            coalescing_scope=scope,
        )

        assert command.allow_prepared_presentation is expected_request
        _queue_and_process(task, command)
        state = task.runtime_state.snapshot().instances.get(instance.instance_uuid)
        results[label] = None if state is None else state.presentation_request

    assert results["manual"] is not None
    assert results["live"] is None
    assert results["theme"] is None


def test_display_cache_never_instantiates_plugin_with_pending_presentation(
    monkeypatch,
):
    task, _config, _clock, playlist, display = _make_presentation_task("presentation-pending-display-cache")
    instance = playlist.plugins[0].snapshot()
    _write_runtime_cache(task, instance, Image.new("RGB", (32, 16), "black"))
    request = _seed_presentation_request(task, instance)
    _install_display_provider_plugin_sentinels(monkeypatch)

    result = _queue_and_process(
        task,
        _normal_cache_display_command(task, playlist, instance),
    )

    assert result.job.status is JobStatus.SUCCEEDED
    assert len(display.calls) == 1
    assert display.calls[0]["image"].getpixel((0, 0)) == (0, 0, 0)
    assert task.runtime_state.snapshot().instances[instance.instance_uuid].presentation_request == request


def test_data_due_wins_same_instance_and_cannot_record_presentation_success(
    monkeypatch,
):
    task, device_config, _clock, playlist, _display = _make_presentation_task(
        "presentation-data-wins",
        latest_refresh_time=None,
    )
    instance = playlist.plugins[0].snapshot()
    _write_runtime_cache(task, instance, Image.new("RGB", (32, 16), "black"))
    prior_request = _seed_presentation_request(
        task,
        instance,
        requested_at=PRESENTATION_NOW - timedelta(minutes=40),
        origin_commit_id="prior-origin-display",
    )
    prior_prepared_at = (PRESENTATION_NOW - timedelta(minutes=30)).isoformat()
    assert task.runtime_state.mark_presentation_prepared(
        instance.instance_uuid,
        prior_request.request_id,
        prior_prepared_at,
        None,
    )
    prior_receipt = PresentationCommitReceipt(
        request_id=prior_request.request_id,
        committed_at=(PRESENTATION_NOW - timedelta(minutes=20)).isoformat(),
        display_commit_id="prior-prepared-display",
        structural_generation=instance.structural_generation,
        settings_revision=instance.settings_revision,
        theme_mode=None,
    )
    assert task.runtime_state.commit_presentation(
        instance.instance_uuid,
        prior_receipt,
        last_good_cache=LastGoodCacheState(
            theme_mode=None,
            structural_generation=instance.structural_generation,
            settings_revision=instance.settings_revision,
            promoted_at=prior_receipt.committed_at,
        ),
    )
    request = _seed_presentation_request(task, instance)
    plugin = BaseCopyIdentityPlugin()
    monkeypatch.setattr(
        refresh_task_module,
        "get_plugin_instance",
        lambda _config: plugin,
    )
    monkeypatch.setattr(task, "_get_current_datetime", lambda: PRESENTATION_NOW)
    monkeypatch.setattr(task, "_select_cached_display_command", lambda _now: None)
    monkeypatch.setattr(task, "_memory_watchdog_should_restart", lambda: False)
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
    )

    task._schedule_if_due()
    entry = task.refresh_queue.take(timeout=0)
    assert entry is not None
    assert entry.command.intent is RefreshIntent.DATA_REFRESH
    payload_before = json.dumps(
        refresh_task_module.thaw_payload(entry.command.payload),
        sort_keys=True,
    )
    playlist_before = json.dumps(
        device_config.get_playlist_manager().to_dict(),
        sort_keys=True,
    )
    task._process_queue_entry(entry)
    state = task.runtime_state.snapshot().instances[instance.instance_uuid]

    assert state.data.last_success_at is not None
    assert state.presentation.last_success_at == prior_receipt.committed_at
    assert state.presentation_request == request
    assert [event[0] for event in plugin.events] == [
        "mode",
        "reconcile",
        "generate",
    ]
    assert plugin.events[1][1] == prior_receipt
    assert plugin.identity_events == [
        ("mode", instance.instance_uuid),
        ("reconcile", instance.instance_uuid),
        ("generate", instance.instance_uuid),
    ]
    assert json.dumps(
        refresh_task_module.thaw_payload(entry.command.payload),
        sort_keys=True,
    ) == payload_before
    assert json.dumps(
        device_config.get_playlist_manager().to_dict(),
        sort_keys=True,
    ) == playlist_before


def test_non_presentation_capable_data_render_receives_no_trusted_identity(
    monkeypatch,
):
    task, device_config, _clock, playlist, _display = _make_presentation_task(
        "non-presentation-data-identity",
        latest_refresh_time=None,
    )
    instance = playlist.plugins[0].snapshot()
    manifest = PluginManifest(
        schema_version=2,
        id=instance.plugin_id,
        class_name="OrdinaryPlugin",
        display_name="Ordinary Plugin",
        refresh_on_display=False,
        capabilities=PluginCapabilities(supports_presentation_refresh=False),
        raw={},
    )
    device_config.get_plugin = lambda plugin_id: {
        "id": plugin_id,
        "_manifest": manifest,
    }
    plugin = BaseCopyIdentityPlugin()
    monkeypatch.setattr(
        refresh_task_module,
        "get_plugin_instance",
        lambda _config: plugin,
    )
    monkeypatch.setattr(task, "_get_current_datetime", lambda: PRESENTATION_NOW)
    monkeypatch.setattr(task, "_select_cached_display_command", lambda _now: None)
    monkeypatch.setattr(task, "_memory_watchdog_should_restart", lambda: False)
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
    )

    task._schedule_if_due()
    entry = task.refresh_queue.take(timeout=0)
    assert entry is not None
    assert entry.command.intent is RefreshIntent.DATA_REFRESH
    task._process_queue_entry(entry)

    assert plugin.identity_events == [("generate", None)]


@pytest.mark.parametrize(
    ("intent", "source", "kind", "force", "theme_render_only"),
    [
        (
            RefreshIntent.LIVE_REFRESH,
            CommandSource.LIVE,
            CommandKind.CACHE_REFRESH,
            False,
            False,
        ),
        (
            RefreshIntent.THEME_REDRAW,
            CommandSource.SCHEDULER,
            CommandKind.CACHE_REFRESH,
            False,
            True,
        ),
        (
            RefreshIntent.MANUAL_RENDER,
            CommandSource.MANUAL,
            CommandKind.DISPLAY,
            True,
            False,
        ),
    ],
)
def test_presentation_capable_playlist_renderer_binds_identity_before_generate(
    monkeypatch,
    intent,
    source,
    kind,
    force,
    theme_render_only,
):
    task, device_config, _clock, playlist, _display = _make_presentation_task(
        f"presentation-{intent.value}-identity"
    )
    instance = playlist.plugins[0].snapshot()
    plugin = BaseCopyIdentityPlugin()
    monkeypatch.setattr(
        refresh_task_module,
        "get_plugin_instance",
        lambda _config: plugin,
    )
    monkeypatch.setattr(task, "_get_current_datetime", lambda: PRESENTATION_NOW)
    task.runtime_state.set_display_state(
        "committed",
        "theme-redraw-origin",
        instance_uuid=instance.instance_uuid,
        changed_at=PRESENTATION_NOW.isoformat(),
    )
    command = task._playlist_command(
        playlist.name,
        instance,
        source=source,
        intent=intent,
        force=force,
        display_cached_only=not force,
        priority=85,
        kind=kind,
        theme_render_only=theme_render_only,
        current_dt=PRESENTATION_NOW,
    )
    payload_before = json.dumps(
        refresh_task_module.thaw_payload(command.payload),
        sort_keys=True,
    )
    playlist_before = json.dumps(
        device_config.get_playlist_manager().to_dict(),
        sort_keys=True,
    )
    config_before = json.dumps(device_config.config, sort_keys=True)

    result = _queue_and_process(task, command)

    assert result.job.status is JobStatus.SUCCEEDED
    assert plugin.identity_events == [("generate", instance.instance_uuid)]
    assert json.dumps(
        refresh_task_module.thaw_payload(command.payload),
        sort_keys=True,
    ) == payload_before
    assert json.dumps(
        device_config.get_playlist_manager().to_dict(),
        sort_keys=True,
    ) == playlist_before
    assert json.dumps(device_config.config, sort_keys=True) == config_before


def test_presentation_prepare_does_not_promote_last_good_or_change_lane_success(
    monkeypatch,
):
    task, _config, _clock, playlist, display = _make_presentation_task("presentation-prepare-only")
    instance = playlist.plugins[0].snapshot()
    _write_runtime_cache(task, instance, Image.new("RGB", (32, 16), "black"))
    _seed_independent_lane_clocks(task, instance)
    baseline_last_good = LastGoodCacheState(
        theme_mode=None,
        structural_generation=instance.structural_generation,
        settings_revision=instance.settings_revision,
        promoted_at=(PRESENTATION_NOW - timedelta(minutes=10)).isoformat(),
    )
    task.runtime_state.record_success(
        instance.instance_uuid,
        baseline_last_good.promoted_at,
        lane=RefreshLane.DATA,
        last_good_cache=baseline_last_good,
    )
    request = _seed_presentation_request(task, instance)
    task.runtime_state.set_display_state(
        "committed",
        request.origin_display_commit_id,
        instance_uuid=instance.instance_uuid,
        changed_at=request.requested_at,
    )
    before = task.runtime_state.snapshot().instances[instance.instance_uuid]
    before_lanes = _non_presentation_lane_bytes(before)
    plugin = PresentationBankPlugin(prepared_color="white")
    monkeypatch.setattr(
        refresh_task_module,
        "get_plugin_instance",
        lambda _config: plugin,
    )
    monkeypatch.setattr(task, "_get_current_datetime", lambda: PRESENTATION_NOW)
    monkeypatch.setattr(task, "_select_cached_display_command", lambda _now: None)
    monkeypatch.setattr(task, "_memory_watchdog_should_restart", lambda: False)
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
    )

    task._schedule_if_due()
    entry = task.refresh_queue.take(timeout=0)
    assert entry is not None
    assert entry.command.intent is RefreshIntent.PRESENTATION_REFRESH
    task._process_queue_entry(entry)
    state = task.runtime_state.snapshot().instances[instance.instance_uuid]
    candidate = _prepared_presentation_candidate(task, instance, request)

    assert state.presentation_request.prepared_at is not None
    assert state.presentation.last_success_at is None
    assert state.last_good_cache == baseline_last_good
    assert _non_presentation_lane_bytes(state) == before_lanes
    assert PresentationCache(Path(task.device_config.plugin_image_dir) / ".refresh-presentation").validate(candidate)
    assert display.calls == []
    origin_receipt = PresentationCommitReceipt(
        request_id=request.request_id,
        committed_at=request.requested_at,
        display_commit_id=request.origin_display_commit_id,
        structural_generation=instance.structural_generation,
        settings_revision=instance.settings_revision,
        theme_mode=request.origin_theme_mode,
    )
    assert [event[0] for event in plugin.events[:3]] == [
        "mode",
        "reconcile",
        "prepare",
    ]
    assert plugin.events[1:] == [
        ("reconcile", origin_receipt),
        ("prepare", request.request_id),
    ]
    assert plugin.contexts == [
        PresentationRequestContext(
            request_id=request.request_id,
            requested_at=request.requested_at,
            origin_display_commit_id=request.origin_display_commit_id,
            last_receipt=None,
        )
    ]
    followup = task.refresh_queue.take(timeout=0)
    assert followup is not None
    assert followup.command.coalescing_scope == (f"presentation-followup:{request.request_id}")


def test_presentation_prepare_reconciles_origin_then_prior_receipt_before_selection(
    monkeypatch,
):
    task, device_config, _clock, playlist, _display = _make_presentation_task(
        "presentation-origin-before-prior-receipt"
    )
    instance = playlist.plugins[0].snapshot()
    _write_runtime_cache(task, instance, Image.new("RGB", (32, 16), "black"))
    _seed_independent_lane_clocks(task, instance)
    prior_request = _seed_presentation_request(
        task,
        instance,
        request_id="a" * 32,
        requested_at=PRESENTATION_NOW - timedelta(minutes=20),
        origin_commit_id="prior-origin-display",
        origin_theme_mode="night",
    )
    assert task.runtime_state.mark_presentation_prepared(
        instance.instance_uuid,
        prior_request.request_id,
        (PRESENTATION_NOW - timedelta(minutes=15)).isoformat(),
        "night",
    )
    prior_receipt = PresentationCommitReceipt(
        request_id=prior_request.request_id,
        committed_at=(PRESENTATION_NOW - timedelta(minutes=10)).isoformat(),
        display_commit_id="prior-prepared-display",
        structural_generation=instance.structural_generation,
        settings_revision=instance.settings_revision,
        theme_mode="night",
    )
    assert task.runtime_state.commit_presentation(
        instance.instance_uuid,
        prior_receipt,
        last_good_cache=LastGoodCacheState(
            theme_mode="night",
            structural_generation=instance.structural_generation,
            settings_revision=instance.settings_revision,
            promoted_at=prior_receipt.committed_at,
        ),
    )
    request = _seed_presentation_request(
        task,
        instance,
        request_id="b" * 32,
        origin_commit_id="current-origin-display",
        origin_theme_mode="day",
    )
    task.runtime_state.set_display_state(
        "committed",
        request.origin_display_commit_id,
        instance_uuid=instance.instance_uuid,
        changed_at=request.requested_at,
    )
    plugin = PresentationBankPlugin()
    monkeypatch.setattr(
        refresh_task_module,
        "get_plugin_instance",
        lambda _config: plugin,
    )
    monkeypatch.setattr(task, "_get_current_datetime", lambda: PRESENTATION_NOW)
    monkeypatch.setattr(task, "_select_cached_display_command", lambda _now: None)
    monkeypatch.setattr(task, "_memory_watchdog_should_restart", lambda: False)
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
    )

    task._schedule_if_due()
    entry = task.refresh_queue.take(timeout=0)
    assert entry is not None
    payload_before = json.dumps(
        refresh_task_module.thaw_payload(entry.command.payload),
        sort_keys=True,
    )
    playlist_before = json.dumps(
        device_config.get_playlist_manager().to_dict(),
        sort_keys=True,
    )
    task._process_queue_entry(entry)

    origin_receipt = PresentationCommitReceipt(
        request_id=request.request_id,
        committed_at=request.requested_at,
        display_commit_id=request.origin_display_commit_id,
        structural_generation=instance.structural_generation,
        settings_revision=instance.settings_revision,
        theme_mode=request.origin_theme_mode,
    )
    assert [event[0] for event in plugin.events[:4]] == [
        "mode",
        "reconcile",
        "reconcile",
        "prepare",
    ]
    assert plugin.events[1:4] == [
        ("reconcile", origin_receipt),
        ("reconcile", prior_receipt),
        ("prepare", request.request_id),
    ]
    assert plugin.identity_events[:4] == [
        ("mode", instance.instance_uuid),
        ("reconcile", instance.instance_uuid),
        ("reconcile", instance.instance_uuid),
        ("prepare", instance.instance_uuid),
    ]
    assert json.dumps(
        refresh_task_module.thaw_payload(entry.command.payload),
        sort_keys=True,
    ) == payload_before
    assert json.dumps(
        device_config.get_playlist_manager().to_dict(),
        sort_keys=True,
    ) == playlist_before


def test_prepared_followup_commit_records_receipt_success_and_preserves_anchor(
    monkeypatch,
):
    task, device_config, _clock, playlist, display = _make_presentation_task("presentation-followup-commit")
    instance = playlist.plugins[0].snapshot()
    canonical = _write_runtime_cache(
        task,
        instance,
        Image.new("RGB", (32, 16), "black"),
    )
    _seed_independent_lane_clocks(task, instance)
    request = _seed_presentation_request(task, instance)
    candidate = _seed_prepared_presentation(
        task,
        instance,
        request,
        image=Image.new("RGB", (32, 16), "white"),
    )
    task.runtime_state.set_display_state(
        "committed",
        request.origin_display_commit_id,
        instance_uuid=instance.instance_uuid,
        changed_at=request.requested_at,
    )
    anchor = (PRESENTATION_NOW - timedelta(minutes=5)).isoformat()
    device_config.refresh_info = RefreshInfo(
        refresh_type="Playlist",
        playlist=playlist.name,
        plugin_id=instance.plugin_id,
        plugin_instance=instance.name,
        refresh_time=anchor,
        image_hash=compute_image_hash(Image.new("RGB", (32, 16), "black")),
    )
    before_lanes = _non_presentation_lane_bytes(task.runtime_state.snapshot().instances[instance.instance_uuid])
    _install_display_provider_plugin_sentinels(monkeypatch)

    result = _queue_and_process(
        task,
        _presentation_followup_command(task, playlist, instance, request),
    )
    state = task.runtime_state.snapshot().instances[instance.instance_uuid]

    assert result.job.status is JobStatus.SUCCEEDED
    assert len(display.calls) == 1
    assert state.presentation_request is None
    assert state.presentation_receipt == PresentationCommitReceipt(
        request_id=request.request_id,
        committed_at=display.calls[0]["committed_at"],
        display_commit_id=display.calls[0]["commit_id"],
        structural_generation=instance.structural_generation,
        settings_revision=instance.settings_revision,
        theme_mode=None,
    )
    assert state.presentation.last_success_at == display.calls[0]["committed_at"]
    assert state.last_good_cache.promoted_at == display.calls[0]["committed_at"]
    assert _non_presentation_lane_bytes(state) == before_lanes
    assert device_config.refresh_info.refresh_time == anchor
    assert Image.open(canonical).getpixel((0, 0)) == (255, 255, 255)
    assert not Path(candidate.cache_path).exists()


def test_changed_target_keeps_prepared_item_for_next_normal_selection(monkeypatch):
    other_uuid = str(uuid.uuid4())

    def change_target(manager, call):
        manager.bound_runtime_state.set_display_state(
            "committed",
            "new-target-commit",
            instance_uuid=other_uuid,
            changed_at=(PRESENTATION_NOW + timedelta(seconds=2)).isoformat(),
        )

    display = PresentationTransactionDisplayManager(after_display=change_target)
    task, device_config, _clock, playlist, _display = _make_presentation_task(
        "presentation-target-changed",
        display_manager=display,
    )
    instance = playlist.plugins[0].snapshot()
    canonical = _write_runtime_cache(
        task,
        instance,
        Image.new("RGB", (32, 16), "black"),
    )
    _seed_independent_lane_clocks(task, instance)
    request = _seed_presentation_request(task, instance)
    candidate = _seed_prepared_presentation(
        task,
        instance,
        request,
        image=Image.new("RGB", (32, 16), "white"),
    )
    task.runtime_state.set_display_state(
        "committed",
        request.origin_display_commit_id,
        instance_uuid=instance.instance_uuid,
        changed_at=request.requested_at,
    )
    original_refresh = device_config.refresh_info
    before = task.runtime_state.snapshot().instances[instance.instance_uuid]
    before_lanes = _non_presentation_lane_bytes(before)
    _install_display_provider_plugin_sentinels(monkeypatch)

    result = _queue_and_process(
        task,
        _presentation_followup_command(task, playlist, instance, request),
    )
    state = task.runtime_state.snapshot().instances[instance.instance_uuid]

    assert result.job.status is JobStatus.CANCELED
    assert len(display.calls) == 1
    assert state.presentation_request == before.presentation_request
    assert state.presentation_receipt is None
    assert state.presentation.last_success_at is None
    assert _non_presentation_lane_bytes(state) == before_lanes
    assert Path(candidate.cache_path).exists()
    assert Image.open(canonical).getpixel((0, 0)) == (0, 0, 0)
    assert device_config.refresh_info is original_refresh


def test_normal_display_consuming_prepared_item_does_not_request_a_second_item(
    monkeypatch,
):
    task, _config, _clock, playlist, display = _make_presentation_task("presentation-normal-consume")
    instance = playlist.plugins[0].snapshot()
    _write_runtime_cache(task, instance, Image.new("RGB", (32, 16), "black"))
    request = _seed_presentation_request(task, instance)
    candidate = _seed_prepared_presentation(
        task,
        instance,
        request,
        image=Image.new("RGB", (32, 16), "white"),
    )
    _install_display_provider_plugin_sentinels(monkeypatch)

    command = _normal_cache_display_command(task, playlist, instance)
    assert command.allow_prepared_presentation is True
    result = _queue_and_process(task, command)
    state = task.runtime_state.snapshot().instances[instance.instance_uuid]

    assert result.job.status is JobStatus.SUCCEEDED
    assert len(display.calls) == 1
    assert state.presentation_request is None
    assert state.presentation_receipt.request_id == request.request_id
    assert state.presentation.last_success_at == display.calls[0]["committed_at"]
    assert not Path(candidate.cache_path).exists()


def test_same_pixel_prepared_item_gets_a_new_display_commit_receipt(monkeypatch):
    task, _config, _clock, playlist, display = _make_presentation_task("presentation-same-pixel-commit")
    instance = playlist.plugins[0].snapshot()
    pixels = Image.new("RGB", (32, 16), "black")
    _write_runtime_cache(task, instance, pixels)
    request = _seed_presentation_request(
        task,
        instance,
        origin_commit_id="same-pixel-origin-commit",
    )
    _seed_prepared_presentation(task, instance, request, image=pixels)
    task.runtime_state.set_display_state(
        "committed",
        request.origin_display_commit_id,
        instance_uuid=instance.instance_uuid,
        changed_at=request.requested_at,
    )
    _install_display_provider_plugin_sentinels(monkeypatch)

    _queue_and_process(
        task,
        _normal_cache_display_command(task, playlist, instance),
    )
    receipt = task.runtime_state.snapshot().instances[instance.instance_uuid].presentation_receipt

    assert len(display.calls) == 1
    assert receipt.display_commit_id == display.calls[0]["commit_id"]
    assert receipt.display_commit_id != request.origin_display_commit_id


def test_corrupt_prepared_png_cools_only_presentation_and_keeps_authoritative_cache(
    monkeypatch,
):
    task, _config, _clock, playlist, display = _make_presentation_task("presentation-corrupt-prepared")
    instance = playlist.plugins[0].snapshot()
    canonical = _write_runtime_cache(
        task,
        instance,
        Image.new("RGB", (32, 16), "black"),
    )
    authoritative_bytes = canonical.read_bytes()
    _seed_independent_lane_clocks(task, instance)
    baseline_last_good = LastGoodCacheState(
        theme_mode=None,
        structural_generation=instance.structural_generation,
        settings_revision=instance.settings_revision,
        promoted_at=(PRESENTATION_NOW - timedelta(minutes=10)).isoformat(),
    )
    task.runtime_state.record_success(
        instance.instance_uuid,
        baseline_last_good.promoted_at,
        lane=RefreshLane.DATA,
        last_good_cache=baseline_last_good,
    )
    request = _seed_presentation_request(task, instance)
    candidate = _seed_prepared_presentation(task, instance, request)
    Path(candidate.cache_path).write_bytes(b"not-a-png")
    before = task.runtime_state.snapshot().instances[instance.instance_uuid]
    before_lanes = _non_presentation_lane_bytes(before)
    _install_display_provider_plugin_sentinels(monkeypatch)

    result = _queue_and_process(
        task,
        _normal_cache_display_command(task, playlist, instance),
    )
    state = task.runtime_state.snapshot().instances[instance.instance_uuid]

    assert result.job.status is JobStatus.CANCELED
    assert display.calls == []
    assert canonical.read_bytes() == authoritative_bytes
    assert state.last_good_cache == baseline_last_good
    assert state.presentation_request.request_id == request.request_id
    assert state.presentation_request.prepared_at is None
    assert state.presentation_request.prepared_theme_mode is None
    assert state.presentation.last_failure_at is not None
    assert state.presentation.next_retry_at is not None
    assert state.presentation.last_success_at is None
    assert _non_presentation_lane_bytes(state) == before_lanes


@pytest.mark.parametrize("restart_state", ["requested", "prepared"])
def test_restart_replays_requested_or_prepared_presentation_without_duplicate_selection(
    monkeypatch,
    restart_state,
):
    task, device_config, clock, playlist, _display = _make_presentation_task(f"presentation-restart-{restart_state}")
    instance = playlist.plugins[0].snapshot()
    _write_runtime_cache(task, instance, Image.new("RGB", (32, 16), "black"))
    task.runtime_state.record_success(
        instance.instance_uuid,
        PRESENTATION_NOW.isoformat(),
        lane=RefreshLane.DATA,
    )
    request = _seed_presentation_request(task, instance)
    task.runtime_state.set_display_state(
        "committed",
        request.origin_display_commit_id,
        instance_uuid=instance.instance_uuid,
        changed_at=request.requested_at,
    )
    if restart_state == "prepared":
        _seed_prepared_presentation(task, instance, request)
    assert task.runtime_state.flush()

    plugin = PresentationBankPlugin()
    first_restart = RefreshTask(
        device_config,
        PresentationTransactionDisplayManager(),
        clock=clock.monotonic,
        wall_clock=clock.wall_time,
    )
    if restart_state == "requested":
        monkeypatch.setattr(
            refresh_task_module,
            "get_plugin_instance",
            lambda _config: plugin,
        )
        monkeypatch.setattr(
            first_restart,
            "_get_current_datetime",
            lambda: PRESENTATION_NOW,
        )
        monkeypatch.setattr(
            first_restart,
            "_select_cached_display_command",
            lambda _now: None,
        )
        monkeypatch.setattr(
            first_restart,
            "_memory_watchdog_should_restart",
            lambda: False,
        )
        monkeypatch.setattr(
            first_restart,
            "_resource_sample",
            lambda: ResourceSample(available_mb=512, swap_percent=0),
        )
        first_restart._schedule_if_due()
        prepared_entry = first_restart.refresh_queue.take(timeout=0)
        assert prepared_entry is not None
        assert prepared_entry.command.intent is RefreshIntent.PRESENTATION_REFRESH
        first_restart._process_queue_entry(prepared_entry)
        assert first_restart.runtime_state.flush()

    second_display = PresentationTransactionDisplayManager()
    second_restart = RefreshTask(
        device_config,
        second_display,
        clock=clock.monotonic,
        wall_clock=clock.wall_time,
    )
    _install_display_provider_plugin_sentinels(monkeypatch)
    _queue_and_process(
        second_restart,
        _normal_cache_display_command(
            second_restart,
            playlist,
            instance,
        ),
    )
    state = second_restart.runtime_state.snapshot().instances[instance.instance_uuid]

    assert state.presentation_request is None
    assert state.presentation_receipt.request_id == request.request_id
    assert len(second_display.calls) == 1
    assert [event[0] for event in plugin.events].count("prepare") == (1 if restart_state == "requested" else 0)


def test_hard_pressure_rotates_cache_without_presentation_renderer(monkeypatch):
    task, device_config, _clock, playlist, display = _make_presentation_task("presentation-hard-pressure")
    instance = playlist.plugins[0].snapshot()
    _write_runtime_cache(task, instance, Image.new("RGB", (32, 16), "black"))
    request = _seed_presentation_request(task, instance)
    device_config.refresh_info.refresh_time = (PRESENTATION_NOW - timedelta(minutes=2)).isoformat()
    monkeypatch.setattr(task, "_get_current_datetime", lambda: PRESENTATION_NOW)
    monkeypatch.setattr(task, "_memory_watchdog_should_restart", lambda: False)
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=60, swap_percent=80),
    )
    _install_display_provider_plugin_sentinels(monkeypatch)

    task._schedule_if_due()
    entry = task.refresh_queue.take(timeout=0)
    assert entry is not None
    assert entry.command.intent is RefreshIntent.DISPLAY_CACHE
    task._process_queue_entry(entry)

    assert len(display.calls) == 1
    assert task.refresh_queue.take(timeout=0) is None
    assert task.runtime_state.snapshot().instances[instance.instance_uuid].presentation_request == request


def test_soft_pressure_makes_bounded_data_and_presentation_progress(monkeypatch):
    clock = RuntimeClock()
    task, _config, _unused, playlist, display = _make_presentation_task(
        "presentation-soft-fairness",
        plugin_count=4,
        latest_refresh_time=None,
        clock=clock,
    )
    instances = [plugin.snapshot() for plugin in playlist.plugins]
    presentation_instance = instances[-1]
    _write_runtime_cache(
        task,
        presentation_instance,
        Image.new("RGB", (32, 16), "black"),
    )
    task.runtime_state.record_success(
        presentation_instance.instance_uuid,
        PRESENTATION_NOW.isoformat(),
        lane=RefreshLane.DATA,
    )
    request = _seed_presentation_request(task, presentation_instance)
    task.runtime_state.set_display_state(
        "committed",
        request.origin_display_commit_id,
        instance_uuid=presentation_instance.instance_uuid,
        changed_at=request.requested_at,
    )
    before_lanes = _non_presentation_lane_bytes(
        task.runtime_state.snapshot().instances[presentation_instance.instance_uuid]
    )
    plugins = {instance.plugin_id: PresentationBankPlugin() for instance in instances}
    monkeypatch.setattr(
        refresh_task_module,
        "get_plugin_instance",
        lambda config: plugins[config["id"]],
    )
    current_dt = [PRESENTATION_NOW]
    monkeypatch.setattr(task, "_get_current_datetime", lambda: current_dt[0])
    monkeypatch.setattr(task, "_select_cached_display_command", lambda _now: None)
    monkeypatch.setattr(task, "_memory_watchdog_should_restart", lambda: False)
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=100, swap_percent=0),
    )

    intents = []
    for _ in range(4):
        task._schedule_if_due()
        entry = task.refresh_queue.take(timeout=0)
        assert entry is not None
        intents.append(entry.command.intent)
        task._process_queue_entry(entry)
        clock.advance(60)
        current_dt[0] += timedelta(seconds=60)

    state = task.runtime_state.snapshot().instances[presentation_instance.instance_uuid]
    assert intents == [
        RefreshIntent.DATA_REFRESH,
        RefreshIntent.DATA_REFRESH,
        RefreshIntent.DATA_REFRESH,
        RefreshIntent.PRESENTATION_REFRESH,
    ]
    assert state.presentation_request.prepared_at is not None
    assert state.presentation.last_success_at is None
    assert _non_presentation_lane_bytes(state) == before_lanes
    assert display.calls == []


def test_presentation_no_change_succeeds_at_committed_origin_without_display(
    monkeypatch,
):
    task, _config, _clock, playlist, display = _make_presentation_task("presentation-no-change-origin")
    instance = playlist.plugins[0].snapshot()
    _write_runtime_cache(task, instance, Image.new("RGB", (32, 16), "black"))
    _seed_independent_lane_clocks(task, instance)
    prior_request = _seed_presentation_request(
        task,
        instance,
        request_id="c" * 32,
        requested_at=PRESENTATION_NOW - timedelta(minutes=20),
        origin_commit_id="no-change-prior-origin",
    )
    assert task.runtime_state.mark_presentation_prepared(
        instance.instance_uuid,
        prior_request.request_id,
        (PRESENTATION_NOW - timedelta(minutes=15)).isoformat(),
        None,
    )
    prior_receipt = PresentationCommitReceipt(
        request_id=prior_request.request_id,
        committed_at=(PRESENTATION_NOW - timedelta(minutes=10)).isoformat(),
        display_commit_id="no-change-prior-prepared",
        structural_generation=instance.structural_generation,
        settings_revision=instance.settings_revision,
        theme_mode=None,
    )
    assert task.runtime_state.commit_presentation(
        instance.instance_uuid,
        prior_receipt,
        last_good_cache=LastGoodCacheState(
            theme_mode=None,
            structural_generation=instance.structural_generation,
            settings_revision=instance.settings_revision,
            promoted_at=prior_receipt.committed_at,
        ),
    )
    request = _seed_presentation_request(
        task,
        instance,
        request_id="d" * 32,
        origin_commit_id="no-change-current-origin",
    )
    task.runtime_state.set_display_state(
        "committed",
        request.origin_display_commit_id,
        instance_uuid=instance.instance_uuid,
        changed_at=request.requested_at,
    )
    before_lanes = _non_presentation_lane_bytes(task.runtime_state.snapshot().instances[instance.instance_uuid])
    plugin = NoChangePresentationPlugin()
    monkeypatch.setattr(
        refresh_task_module,
        "get_plugin_instance",
        lambda _config: plugin,
    )
    monkeypatch.setattr(task, "_get_current_datetime", lambda: PRESENTATION_NOW)
    monkeypatch.setattr(task, "_select_cached_display_command", lambda _now: None)
    monkeypatch.setattr(task, "_memory_watchdog_should_restart", lambda: False)
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
    )

    task._schedule_if_due()
    entry = task.refresh_queue.take(timeout=0)
    assert entry is not None
    task._process_queue_entry(entry)
    state = task.runtime_state.snapshot().instances[instance.instance_uuid]
    origin_receipt = PresentationCommitReceipt(
        request_id=request.request_id,
        committed_at=request.requested_at,
        display_commit_id=request.origin_display_commit_id,
        structural_generation=instance.structural_generation,
        settings_revision=instance.settings_revision,
        theme_mode=request.origin_theme_mode,
    )

    assert state.presentation_request is None
    assert state.presentation.last_success_at == request.requested_at
    assert state.presentation_receipt == prior_receipt
    assert _non_presentation_lane_bytes(state) == before_lanes
    assert [event[0] for event in plugin.events] == [
        "mode",
        "reconcile",
        "reconcile",
    ]
    assert plugin.events[1:] == [
        ("reconcile", origin_receipt),
        ("reconcile", prior_receipt),
    ]
    assert display.calls == []


def test_invalid_refresh_on_display_is_safe_false_after_scheduler_display_probe(
    monkeypatch,
):
    task, device_config, _clock, playlist, display = _make_presentation_task("presentation-invalid-trigger")
    instance = playlist.plugins[0].snapshot()
    playlist.plugins[0].settings["refreshOnDisplay"] = "sometimes"
    instance = playlist.plugins[0].snapshot()
    _write_runtime_cache(task, instance, Image.new("RGB", (32, 16), "black"))
    device_config.refresh_info.refresh_time = (PRESENTATION_NOW - timedelta(minutes=2)).isoformat()
    monkeypatch.setattr(task, "_get_current_datetime", lambda: PRESENTATION_NOW)
    monkeypatch.setattr(task, "_memory_watchdog_should_restart", lambda: False)
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
    )
    _install_display_provider_plugin_sentinels(monkeypatch)

    task._schedule_if_due()
    entry = task.refresh_queue.take(timeout=0)
    assert entry is not None
    task._process_queue_entry(entry)
    state = task.runtime_state.snapshot().instances.get(instance.instance_uuid)

    assert len(display.calls) == 1
    assert state is None or state.presentation_request is None
    assert task.scheduler_snapshot().last_error is None


def test_legacy_async_presentation_mode_fails_closed_without_renderer(monkeypatch):
    task, _config, _clock, playlist, display = _make_presentation_task("presentation-legacy-disabled")
    instance = playlist.plugins[0].snapshot()
    _write_runtime_cache(task, instance, Image.new("RGB", (32, 16), "black"))
    task.runtime_state.record_success(
        instance.instance_uuid,
        PRESENTATION_NOW.isoformat(),
        lane=RefreshLane.DATA,
    )
    request = _seed_presentation_request(task, instance)
    plugin = LegacyPresentationPlugin()
    monkeypatch.setattr(
        refresh_task_module,
        "get_plugin_instance",
        lambda _config: plugin,
    )
    monkeypatch.setattr(task, "_get_current_datetime", lambda: PRESENTATION_NOW)
    monkeypatch.setattr(task, "_select_cached_display_command", lambda _now: None)
    monkeypatch.setattr(task, "_memory_watchdog_should_restart", lambda: False)
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
    )

    task._schedule_if_due()
    entry = task.refresh_queue.take(timeout=0)
    assert entry is not None
    task._process_queue_entry(entry)
    state = task.runtime_state.snapshot().instances[instance.instance_uuid]

    assert task.refresh_queue.get_entry(entry.job.id).job.status is JobStatus.FAILED
    assert state.presentation_request == request
    assert state.presentation.last_failure_at is not None
    assert state.presentation.next_retry_at is not None
    assert state.presentation.last_success_at is None
    assert [event[0] for event in plugin.events] == ["mode"]
    assert display.calls == []


def test_presentation_commit_cas_false_retains_prepared_candidate(monkeypatch):
    task, _config, _clock, playlist, display = _make_presentation_task("presentation-commit-cas-false")
    instance = playlist.plugins[0].snapshot()
    _write_runtime_cache(task, instance, Image.new("RGB", (32, 16), "black"))
    request = _seed_presentation_request(task, instance)
    candidate = _seed_prepared_presentation(
        task,
        instance,
        request,
        image=Image.new("RGB", (32, 16), "white"),
    )
    _install_display_provider_plugin_sentinels(monkeypatch)
    monkeypatch.setattr(task.runtime_state, "commit_presentation", lambda *a, **k: False)

    result = _queue_and_process(
        task,
        _normal_cache_display_command(task, playlist, instance),
    )
    state = task.runtime_state.snapshot().instances[instance.instance_uuid]

    assert result.job.status is JobStatus.CANCELED
    assert len(display.calls) == 1
    assert state.presentation_request.request_id == request.request_id
    assert state.presentation_receipt is None
    assert state.presentation.last_success_at is None
    assert Path(candidate.cache_path).exists()


@pytest.mark.parametrize("failure_point", ["display", "commit"])
def test_prepared_display_exception_cools_only_presentation_and_schedules_exact_retry(
    monkeypatch,
    failure_point,
):
    def fail_after_display(_manager, _call):
        if failure_point == "display":
            raise RuntimeError("prepared display failed")

    display = PresentationTransactionDisplayManager(after_display=fail_after_display)
    task, _config, clock, playlist, _display = _make_presentation_task(
        f"presentation-{failure_point}-exception",
        display_manager=display,
    )
    instance = playlist.plugins[0].snapshot()
    _write_runtime_cache(task, instance, Image.new("RGB", (32, 16), "black"))
    _seed_independent_lane_clocks(task, instance)
    request = _seed_presentation_request(task, instance)
    candidate = _seed_prepared_presentation(task, instance, request)
    task.runtime_state.set_display_state(
        "committed",
        request.origin_display_commit_id,
        instance_uuid=instance.instance_uuid,
        changed_at=request.requested_at,
    )
    before_lanes = _non_presentation_lane_bytes(
        task.runtime_state.snapshot().instances[instance.instance_uuid]
    )
    if failure_point == "commit":
        def fail_commit(*_args, **_kwargs):
            raise RuntimeError("presentation commit failed")

        monkeypatch.setattr(task.runtime_state, "commit_presentation", fail_commit)
    now = [PRESENTATION_NOW]
    monkeypatch.setattr(task, "_get_current_datetime", lambda: now[0])
    monkeypatch.setattr(task, "_memory_watchdog_should_restart", lambda: False)
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
    )
    _install_display_provider_plugin_sentinels(monkeypatch)

    result = _queue_and_process(
        task,
        _presentation_followup_command(task, playlist, instance, request),
    )
    failed_state = task.runtime_state.snapshot().instances[instance.instance_uuid]

    assert result.job.status is JobStatus.FAILED
    assert _non_presentation_lane_bytes(failed_state) == before_lanes
    assert failed_state.presentation.last_failure_at is not None
    assert failed_state.presentation.next_retry_at is not None
    assert failed_state.presentation_request.request_id == request.request_id
    assert failed_state.presentation_request.prepared_at is not None
    assert Path(candidate.cache_path).exists()
    assert task.refresh_queue.take(timeout=0) is None

    clock.advance(3601)
    now[0] += timedelta(seconds=3601)
    task._schedule_if_due()
    retry = task.refresh_queue.take(timeout=0)

    assert retry is not None
    assert retry.command.intent is RefreshIntent.DISPLAY_CACHE
    assert retry.command.payload["presentation_request_id"] == request.request_id
    assert retry.command.coalescing_scope == f"presentation-followup:{request.request_id}"
    assert retry.command.allow_prepared_presentation is True


def test_presentation_commit_published_then_raised_finishes_as_committed(
    monkeypatch,
):
    task, _config, _clock, playlist, display = _make_presentation_task(
        "presentation-commit-published-then-raised"
    )
    instance = playlist.plugins[0].snapshot()
    _write_runtime_cache(task, instance, Image.new("RGB", (32, 16), "black"))
    request = _seed_presentation_request(task, instance)
    candidate = _seed_prepared_presentation(task, instance, request)
    task.runtime_state.set_display_state(
        "committed",
        request.origin_display_commit_id,
        instance_uuid=instance.instance_uuid,
        changed_at=request.requested_at,
    )
    original_commit = task.runtime_state.commit_presentation

    def commit_then_raise(*args, **kwargs):
        assert original_commit(*args, **kwargs) is True
        raise RuntimeError("runtime persistence failed after publication")

    monkeypatch.setattr(
        task.runtime_state,
        "commit_presentation",
        commit_then_raise,
    )
    _install_display_provider_plugin_sentinels(monkeypatch)

    result = _queue_and_process(
        task,
        _presentation_followup_command(task, playlist, instance, request),
    )
    state = task.runtime_state.snapshot().instances[instance.instance_uuid]

    assert result.job.status is JobStatus.SUCCEEDED
    assert len(display.calls) == 1
    assert state.presentation_request is None
    assert state.presentation_receipt.request_id == request.request_id
    assert state.presentation.last_failure_at is None
    assert not Path(candidate.cache_path).exists()


def test_exact_presentation_followup_with_revoked_capability_never_falls_back(
    monkeypatch,
):
    task, device_config, _clock, playlist, display = _make_presentation_task(
        "presentation-capability-revoked"
    )
    instance = playlist.plugins[0].snapshot()
    canonical = _write_runtime_cache(
        task,
        instance,
        Image.new("RGB", (32, 16), "black"),
    )
    authoritative_bytes = canonical.read_bytes()
    request = _seed_presentation_request(task, instance)
    candidate = _seed_prepared_presentation(task, instance, request)
    task.runtime_state.set_display_state(
        "committed",
        request.origin_display_commit_id,
        instance_uuid=instance.instance_uuid,
        changed_at=request.requested_at,
    )
    revoked = PluginManifest(
        schema_version=2,
        id=instance.plugin_id,
        class_name="PresentationPlugin",
        display_name="Presentation Plugin",
        refresh_on_display=True,
        capabilities=PluginCapabilities(supports_presentation_refresh=False),
        raw={},
    )
    device_config.get_plugin = lambda plugin_id: {
        "id": plugin_id,
        "refresh_on_display": True,
        "_manifest": revoked,
    }
    _install_display_provider_plugin_sentinels(monkeypatch)

    result = _queue_and_process(
        task,
        _presentation_followup_command(task, playlist, instance, request),
    )
    state = task.runtime_state.snapshot().instances[instance.instance_uuid]

    assert result.job.status is JobStatus.CANCELED
    assert display.calls == []
    assert state.presentation_request.request_id == request.request_id
    assert state.presentation_receipt is None
    assert Path(candidate.cache_path).exists()
    assert canonical.read_bytes() == authoritative_bytes
