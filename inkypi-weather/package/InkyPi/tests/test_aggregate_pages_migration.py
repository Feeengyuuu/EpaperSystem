import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import aggregate_pages_migration as migration  # noqa: E402
from config import Config  # noqa: E402

OWNER_UUID = "6ce0a56e6ddd4feaa1006be9394daf10"


def _instance(plugin_id, name, uuid, interval=300):
    return {"plugin_id": plugin_id, "name": name, "plugin_settings": {}, "refresh": {"interval": interval},
            "instance_uuid": uuid, "structural_generation": 1, "settings_revision": 1}


def _payload(*extra, playlist="DailyDoseOfDay", live_radar_uuid=OWNER_UUID):
    return {
        "schema_version": 1,
        "config_revision": 1,
        "resolution": [800, 480],
        "runtime_migrations": {"existing_migration_v1": True},
        "playlist_config": {
            "playlists": [{"name": playlist, "start_time": "00:00", "end_time": "24:00",
                           "plugins": [_instance("live_radar", "LiveRadar", live_radar_uuid), *extra]}],
            "active_playlist": playlist,
        },
    }


def _start(monkeypatch, tmp_path, payload):
    monkeypatch.setattr(migration, "OWNER_LIVE_RADAR_UUID_SHA256_PREFIX",
                        hashlib.sha256(OWNER_UUID.encode()).hexdigest()[:16])
    path = tmp_path / "device.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(Config, "config_file", str(path))
    return Config(), path


def _saved(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_startup_adds_the_four_pages_to_the_owner_rotation_once(monkeypatch, tmp_path):
    config, path = _start(monkeypatch, tmp_path, _payload())

    saved = _saved(path)
    plugins = saved["playlist_config"]["playlists"][0]["plugins"]
    assert [(item["plugin_id"], item["name"]) for item in plugins] == [
        ("live_radar", "LiveRadar"), ("bay_alerts", "BayAlerts"), ("money_radar", "MoneyRadar"),
        ("tech_play", "TechPlay"), ("nature_nearby", "BayDruid")]
    assert plugins[0] == {**_instance("live_radar", "LiveRadar", OWNER_UUID), "latest_refresh_time": None}
    assert all(item["refresh"] == {"interval": 1800} and item["plugin_settings"] == {} for item in plugins[1:])
    assert len({item["instance_uuid"] for item in plugins}) == 5
    assert saved["runtime_migrations"] == {"existing_migration_v1": True, "aggregate_pages_playlist_v1": True}
    assert config.get_playlist_manager().find_plugin("tech_play", "TechPlay") is not None

    revision = saved["config_revision"]
    Config()
    assert _saved(path)["config_revision"] == revision


def test_a_page_already_in_the_rotation_is_not_added_again(monkeypatch, tmp_path):
    _config, path = _start(monkeypatch, tmp_path, _payload(_instance("tech_play", "Games", "a" * 32, 3600)))

    plugins = _saved(path)["playlist_config"]["playlists"][0]["plugins"]
    assert [item["plugin_id"] for item in plugins].count("tech_play") == 1
    assert [item["name"] for item in plugins] == ["LiveRadar", "Games", "BayAlerts", "MoneyRadar", "BayDruid"]


def test_other_devices_and_playlists_are_left_alone(monkeypatch, tmp_path):
    for payload in (_payload(live_radar_uuid="b" * 32), _payload(playlist="Evening")):
        _config, path = _start(monkeypatch, tmp_path, payload)
        saved = _saved(path)
        assert len(saved["playlist_config"]["playlists"][0]["plugins"]) == 1
        assert "aggregate_pages_playlist_v1" not in saved["runtime_migrations"]
