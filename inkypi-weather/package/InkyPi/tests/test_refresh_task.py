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
    def __init__(self, calls):
        self.calls = calls

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


def test_refresh_due_plugin_instances_updates_sports_dashboard_live_cache_early(monkeypatch):
    calls = []
    tmp_path = make_test_dir("sports-live-cache")
    state_path = tmp_path / "lpl_live_state.json"
    state_path.write_text(
        json.dumps(
            {
                "version": "sports-dashboard-lpl-live-v1",
                "has_live": True,
                "live_until": "2026-05-26T08:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    device_config = FakeDeviceConfig(tmp_path)
    task = RefreshTask(device_config, display_manager=None)
    monkeypatch.setattr(task, "_sports_dashboard_worldcup_live_state_path", lambda: str(tmp_path / "missing_worldcup.json"))
    monkeypatch.setattr(task, "_sports_dashboard_lpl_live_state_path", lambda: str(state_path))
    monkeypatch.setattr(task, "_sports_dashboard_nba_live_state_path", lambda: str(tmp_path / "missing_nba.json"))
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
                "plugin_id": "sports_dashboard",
                "name": "SportsDashboard",
                "plugin_settings": {"id": "sports", "lplLiveRefreshIntervalSeconds": "180"},
                "refresh": {"interval": 3600},
                "latest_refresh_time": "2026-05-26T07:00:00+00:00",
            },
        ],
    )
    plugin_instance = playlist.find_plugin("sports_dashboard", "SportsDashboard")
    other_plugin = playlist.find_plugin("live_radar", "LiveRadar")
    Image.new("RGB", (1, 1), "black").save(tmp_path / other_plugin.get_image_path())
    Image.new("RGB", (1, 1), "black").save(tmp_path / plugin_instance.get_image_path())

    monkeypatch.setattr(
        "src.refresh_task.get_plugin_instance",
        lambda config: FakePlugin(calls),
    )

    task._refresh_due_plugin_instances(
        playlist,
        datetime(2026, 5, 26, 7, 4, tzinfo=timezone.utc),
        only_plugin_id="sports_dashboard",
    )

    assert calls == ["sports"]
    assert other_plugin.latest_refresh_time == "2026-05-26T07:00:00+00:00"
    assert plugin_instance.latest_refresh_time == "2026-05-26T07:04:00+00:00"
    assert device_config.write_count == 1


def test_sports_dashboard_live_refresh_wait_seconds_uses_live_state(monkeypatch):
    tmp_path = make_test_dir("sports-live-wait")
    state_path = tmp_path / "lpl_live_state.json"
    state_path.write_text(
        json.dumps(
            {
                "version": "sports-dashboard-lpl-live-v1",
                "has_live": True,
                "live_until": "2026-05-26T08:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    playlist = Playlist(
        "DailyDoseOfDay",
        "00:00",
        "24:00",
        plugins=[
            {
                "plugin_id": "sports_dashboard",
                "name": "SportsDashboard",
                "plugin_settings": {"id": "sports", "lplLiveRefreshIntervalSeconds": "180"},
                "refresh": {"interval": 3600},
                "latest_refresh_time": "2026-05-26T07:00:00+00:00",
            },
        ],
    )
    device_config = ThreadedDeviceConfig(tmp_path, playlist)
    task = RefreshTask(device_config, display_manager=None)
    monkeypatch.setattr(task, "_sports_dashboard_worldcup_live_state_path", lambda: str(tmp_path / "missing_worldcup.json"))
    monkeypatch.setattr(task, "_sports_dashboard_lpl_live_state_path", lambda: str(state_path))
    monkeypatch.setattr(task, "_sports_dashboard_nba_live_state_path", lambda: str(tmp_path / "missing_nba.json"))

    wait_seconds = task._sports_dashboard_live_refresh_wait_seconds(
        datetime(2026, 5, 26, 7, 2, tzinfo=timezone.utc)
    )

    assert wait_seconds == 60


def test_sports_dashboard_live_refresh_wait_seconds_uses_nba_live_state(monkeypatch):
    tmp_path = make_test_dir("sports-nba-live-wait")
    state_path = tmp_path / "nba_live_state.json"
    state_path.write_text(
        json.dumps(
            {
                "version": "sports-dashboard-nba-live-v1",
                "has_live": True,
                "live_until": "2026-05-26T08:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    playlist = Playlist(
        "DailyDoseOfDay",
        "00:00",
        "24:00",
        plugins=[
            {
                "plugin_id": "sports_dashboard",
                "name": "SportsDashboard",
                "plugin_settings": {"id": "sports", "nbaLiveRefreshIntervalSeconds": "120"},
                "refresh": {"interval": 3600},
                "latest_refresh_time": "2026-05-26T07:00:00+00:00",
            },
        ],
    )
    device_config = ThreadedDeviceConfig(tmp_path, playlist)
    task = RefreshTask(device_config, display_manager=None)
    monkeypatch.setattr(task, "_sports_dashboard_worldcup_live_state_path", lambda: str(tmp_path / "missing_worldcup.json"))
    monkeypatch.setattr(task, "_sports_dashboard_lpl_live_state_path", lambda: str(tmp_path / "missing_lpl.json"))
    monkeypatch.setattr(task, "_sports_dashboard_nba_live_state_path", lambda: str(state_path))

    wait_seconds = task._sports_dashboard_live_refresh_wait_seconds(
        datetime(2026, 5, 26, 7, 1, tzinfo=timezone.utc)
    )

    assert wait_seconds == 60


def test_sports_dashboard_live_refresh_wait_seconds_uses_worldcup_live_state(monkeypatch):
    tmp_path = make_test_dir("sports-worldcup-live-wait")
    state_path = tmp_path / "worldcup_live_state.json"
    state_path.write_text(
        json.dumps(
            {
                "version": "sports-dashboard-worldcup-live-v1",
                "has_live": True,
                "live_until": "2026-05-26T08:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    playlist = Playlist(
        "DailyDoseOfDay",
        "00:00",
        "24:00",
        plugins=[
            {
                "plugin_id": "sports_dashboard",
                "name": "SportsDashboard",
                "plugin_settings": {"id": "sports", "worldCupLiveRefreshIntervalSeconds": "120"},
                "refresh": {"interval": 3600},
                "latest_refresh_time": "2026-05-26T07:00:00+00:00",
            },
        ],
    )
    device_config = ThreadedDeviceConfig(tmp_path, playlist)
    task = RefreshTask(device_config, display_manager=None)
    monkeypatch.setattr(task, "_sports_dashboard_worldcup_live_state_path", lambda: str(state_path))
    monkeypatch.setattr(task, "_sports_dashboard_lpl_live_state_path", lambda: str(tmp_path / "missing_lpl.json"))
    monkeypatch.setattr(task, "_sports_dashboard_nba_live_state_path", lambda: str(tmp_path / "missing_nba.json"))

    wait_seconds = task._sports_dashboard_live_refresh_wait_seconds(
        datetime(2026, 5, 26, 7, 1, tzinfo=timezone.utc)
    )

    assert wait_seconds == 60


def test_sports_dashboard_live_refresh_wait_seconds_defaults_to_one_minute(monkeypatch):
    tmp_path = make_test_dir("sports-live-wait-default")
    state_path = tmp_path / "lpl_live_state.json"
    state_path.write_text(
        json.dumps(
            {
                "version": "sports-dashboard-lpl-live-v1",
                "has_live": True,
                "live_until": "2026-05-26T08:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    playlist = Playlist(
        "DailyDoseOfDay",
        "00:00",
        "24:00",
        plugins=[
            {
                "plugin_id": "sports_dashboard",
                "name": "SportsDashboard",
                "plugin_settings": {"id": "sports"},
                "refresh": {"interval": 3600},
                "latest_refresh_time": "2026-05-26T07:00:00+00:00",
            },
        ],
    )
    device_config = ThreadedDeviceConfig(tmp_path, playlist)
    task = RefreshTask(device_config, display_manager=None)
    monkeypatch.setattr(task, "_sports_dashboard_worldcup_live_state_path", lambda: str(tmp_path / "missing_worldcup.json"))
    monkeypatch.setattr(task, "_sports_dashboard_lpl_live_state_path", lambda: str(state_path))
    monkeypatch.setattr(task, "_sports_dashboard_nba_live_state_path", lambda: str(tmp_path / "missing_nba.json"))

    wait_seconds = task._sports_dashboard_live_refresh_wait_seconds(
        datetime(2026, 5, 26, 7, 0, 30, tzinfo=timezone.utc)
    )

    assert wait_seconds == 30


def test_sports_dashboard_live_refresh_is_not_due_without_live_state(monkeypatch):
    tmp_path = make_test_dir("sports-live-no-state")
    playlist = Playlist(
        "DailyDoseOfDay",
        "00:00",
        "24:00",
        plugins=[
            {
                "plugin_id": "sports_dashboard",
                "name": "SportsDashboard",
                "plugin_settings": {"id": "sports"},
                "refresh": {"interval": 3600},
                "latest_refresh_time": "2026-05-26T07:00:00+00:00",
            },
        ],
    )
    device_config = ThreadedDeviceConfig(tmp_path, playlist)
    task = RefreshTask(device_config, display_manager=None)
    monkeypatch.setattr(task, "_sports_dashboard_worldcup_live_state_path", lambda: str(tmp_path / "missing_worldcup.json"))
    monkeypatch.setattr(task, "_sports_dashboard_lpl_live_state_path", lambda: str(tmp_path / "missing.json"))
    monkeypatch.setattr(task, "_sports_dashboard_nba_live_state_path", lambda: str(tmp_path / "missing_nba.json"))
    plugin_instance = playlist.find_plugin("sports_dashboard", "SportsDashboard")

    live_due = task._sports_dashboard_live_refresh_due(
        plugin_instance,
        datetime(2026, 5, 26, 7, 10, tzinfo=timezone.utc),
    )
    wait_seconds = task._sports_dashboard_live_refresh_wait_seconds(
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


def test_playlist_refresh_uses_placeholder_when_scheduled_cache_is_missing():
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

    assert calls == []
    assert image.size == (200, 120)
    assert plugin_instance.latest_refresh_time == "2026-05-26T07:00:00+00:00"


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

    assert calls == [{"id": "worldcup", "forceRefresh": True, "force_refresh": True}]
    assert plugin_instance.settings == {"id": "worldcup", "forceRefresh": "false"}


def test_manual_refresh_marks_plugin_settings():
    calls = []

    ManualRefresh("sports_dashboard", {"id": "worldcup"}).execute(
        CapturePlugin(calls),
        device_config=None,
        current_dt=datetime(2026, 5, 26, 7, 5, tzinfo=timezone.utc),
    )

    assert calls == [{"id": "worldcup", "forceRefresh": True, "force_refresh": True}]


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
        assert all(call == {"id": "live_radar", "forceRefresh": True, "force_refresh": True} for call in calls)
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
        lambda config: FakePlugin(calls),
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
