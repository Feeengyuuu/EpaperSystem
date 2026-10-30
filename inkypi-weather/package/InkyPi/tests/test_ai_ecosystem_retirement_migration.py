import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import aggregate_pages_migration  # noqa: E402
import ai_ecosystem_retirement_migration as migration  # noqa: E402
from config import Config  # noqa: E402

OWNER_UUID = "6ce0a56e6ddd4feaa1006be9394daf10"


def _instance(plugin_id, name, uuid, interval=1800):
    return {"plugin_id": plugin_id, "name": name, "plugin_settings": {}, "refresh": {"interval": interval},
            "instance_uuid": uuid, "structural_generation": 1, "settings_revision": 1}


def _payload(*extra, live_radar_uuid=OWNER_UUID):
    return {
        "schema_version": 1,
        "config_revision": 1,
        "resolution": [800, 480],
        "runtime_migrations": {"existing_migration_v1": True, "aggregate_pages_playlist_v1": True,
                               "pop_traffic_pages_playlist_v1": True, "planet_now_page_playlist_v1": True, "sky_radar_page_playlist_v1": True,
                               "hot_odds_pages_playlist_v1": True, "ai_papers_page_playlist_v1": True},
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


def test_startup_retires_the_ai_ecosystem_page_once_tech_pulse_carries_it(monkeypatch, tmp_path):
    tech = _instance("tech_pulse", "TechPulse", "c" * 32)
    config, path = _start(monkeypatch, tmp_path, _payload(tech, _instance("ai_ecosystem_pulse", "AI", "d" * 32)))

    saved = _saved(path)
    plugins = saved["playlist_config"]["playlists"][0]["plugins"]
    assert [item["plugin_id"] for item in plugins] == ["live_radar", "tech_pulse"]
    # Everything else is saved exactly as it was.
    assert plugins[1] == {**tech, "latest_refresh_time": None}
    assert saved["runtime_migrations"] == {"existing_migration_v1": True, "aggregate_pages_playlist_v1": True,
                                           "pop_traffic_pages_playlist_v1": True, "planet_now_page_playlist_v1": True, "sky_radar_page_playlist_v1": True,
                                           "hot_odds_pages_playlist_v1": True, "ai_papers_page_playlist_v1": True,
                                           migration.MIGRATION_ID: True}
    assert config.get_playlist_manager().find_plugin("ai_ecosystem_pulse", "AI") is None

    revision = saved["config_revision"]
    Config()
    assert _saved(path)["config_revision"] == revision


def test_the_page_stays_without_tech_pulse_or_on_another_device(monkeypatch, tmp_path):
    ai = _instance("ai_ecosystem_pulse", "AI", "d" * 32)
    for payload in (_payload(ai), _payload(_instance("tech_pulse", "TechPulse", "c" * 32), ai,
                                           live_radar_uuid="b" * 32)):
        _config, path = _start(monkeypatch, tmp_path, payload)
        saved = _saved(path)
        assert "ai_ecosystem_pulse" in [item["plugin_id"] for item in saved["playlist_config"]["playlists"][0]["plugins"]]
        assert migration.MIGRATION_ID not in saved["runtime_migrations"]
