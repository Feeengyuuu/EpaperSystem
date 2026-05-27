from datetime import datetime, timezone
from pathlib import Path
import uuid

from PIL import Image

from src.model import Playlist
from src.refresh_task import PlaylistRefresh, RefreshTask


TEST_STATE_ROOT = Path(__file__).resolve().parents[4] / ".tmp" / "refresh_task_tests"


def make_test_dir(name):
    path = TEST_STATE_ROOT / f"{name}-{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    return path


class FakeDeviceConfig:
    def __init__(self, plugin_image_dir):
        self.plugin_image_dir = str(plugin_image_dir)
        self.write_count = 0

    def get_plugin(self, plugin_id):
        return {"id": plugin_id}

    def write_config(self):
        self.write_count += 1


class FakePlugin:
    def __init__(self, calls):
        self.calls = calls

    def generate_image(self, settings, device_config):
        self.calls.append(settings["id"])
        return Image.new("RGB", (1, 1), "white")


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
