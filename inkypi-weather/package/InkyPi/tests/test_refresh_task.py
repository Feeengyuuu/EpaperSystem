import json
from datetime import datetime, timezone
from pathlib import Path
import threading
import time
import uuid

from PIL import Image

from src.model import Playlist, PlaylistManager, RefreshInfo
from src.refresh_task import ManualRefresh, PlaylistRefresh, RefreshTask


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


def test_failed_due_cache_refresh_marks_attempt_to_avoid_immediate_retry(monkeypatch):
    tmp_path = make_test_dir("due-cache-failure-cooldown")
    device_config = FakeDeviceConfig(tmp_path)
    task = RefreshTask(device_config, display_manager=None)
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

    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda config: FailingPlugin(),
    )

    task._refresh_due_plugin_instances(
        playlist,
        datetime(2026, 5, 26, 7, 5, tzinfo=timezone.utc),
        max_updates=1,
    )

    assert playlist.find_plugin("bad", "Bad Plugin").latest_refresh_time == "2026-05-26T07:05:00+00:00"
    assert not (tmp_path / "bad_Bad_Plugin.png").exists()
    assert device_config.write_count == 1


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
