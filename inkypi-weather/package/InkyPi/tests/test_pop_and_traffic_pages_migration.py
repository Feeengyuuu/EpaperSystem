import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import aggregate_pages_migration  # noqa: E402
from config import Config  # noqa: E402

OWNER_UUID = "6ce0a56e6ddd4feaa1006be9394daf10"


def _instance(plugin_id, name, uuid, interval=300):
    return {"plugin_id": plugin_id, "name": name, "plugin_settings": {}, "refresh": {"interval": interval},
            "instance_uuid": uuid, "structural_generation": 1, "settings_revision": 1}


def _payload(*extra, live_radar_uuid=OWNER_UUID):
    return {
        "schema_version": 1,
        "config_revision": 1,
        "resolution": [800, 480],
        "runtime_migrations": {"aggregate_pages_playlist_v1": True, "ai_ecosystem_retired_v1": True,
                               "box_office_two_markets_v1": True, "planet_now_page_playlist_v1": True, "sky_radar_page_playlist_v1": True,
                               "hot_odds_pages_playlist_v1": True},
        "playlist_config": {
            "playlists": [{"name": "DailyDoseOfDay", "start_time": "00:00", "end_time": "24:00",
                           "plugins": [_instance("live_radar", "LiveRadar", live_radar_uuid), *extra]}],
            "active_playlist": "DailyDoseOfDay",
        },
    }


def _start(monkeypatch, tmp_path, payload):
    monkeypatch.setattr(aggregate_pages_migration, "OWNER_LIVE_RADAR_UUID_SHA256_PREFIX",
                        hashlib.sha256(OWNER_UUID.encode()).hexdigest()[:16])
    path = tmp_path / "device.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(Config, "config_file", str(path))
    return Config(), path


def _saved(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_startup_adds_daily_pop_and_the_traffic_wall_once(monkeypatch, tmp_path):
    _config, path = _start(monkeypatch, tmp_path, _payload())

    saved = _saved(path)
    added = {item["plugin_id"]: item for item in saved["playlist_config"]["playlists"][0]["plugins"][1:]}
    assert {plugin_id: (item["name"], item["refresh"]) for plugin_id, item in added.items()} == {
        "daily_pop": ("DailyPop", {"interval": 1800}), "bay_traffic_cams": ("BayTrafficCams", {"interval": 900})}
    assert all(item["plugin_settings"] == {} for item in added.values())
    assert saved["runtime_migrations"]["pop_traffic_pages_playlist_v1"] is True

    revision = saved["config_revision"]
    Config()
    assert _saved(path)["config_revision"] == revision


def test_an_existing_page_is_kept_and_other_devices_are_left_alone(monkeypatch, tmp_path):
    _config, path = _start(monkeypatch, tmp_path, _payload(_instance("daily_pop", "Pop", "a" * 32, 3600)))
    names = [item["name"] for item in _saved(path)["playlist_config"]["playlists"][0]["plugins"]]
    assert names == ["LiveRadar", "Pop", "BayTrafficCams"]

    _config, path = _start(monkeypatch, tmp_path, _payload(live_radar_uuid="b" * 32))
    saved = _saved(path)
    assert len(saved["playlist_config"]["playlists"][0]["plugins"]) == 1
    assert "pop_traffic_pages_playlist_v1" not in saved["runtime_migrations"]
