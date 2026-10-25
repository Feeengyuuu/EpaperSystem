import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import aggregate_pages_migration  # noqa: E402
import box_office_two_markets_migration as migration  # noqa: E402
from config import Config  # noqa: E402

OWNER_UUID = "6ce0a56e6ddd4feaa1006be9394daf10"
MARKERS = {"aggregate_pages_playlist_v1": True, "ai_ecosystem_pulse_retired_v1": True,
           "pop_traffic_pages_playlist_v1": True, "planet_now_page_playlist_v1": True}


def _instance(plugin_id, name, uuid, settings=None):
    return {"plugin_id": plugin_id, "name": name, "plugin_settings": settings or {}, "refresh": {"interval": 21600},
            "instance_uuid": uuid, "structural_generation": 1, "settings_revision": 1}


def _china(**settings):
    return _instance("box_office_top_movies", "BoxOfficeTopMovies", "c" * 32,
                     {"sourceMode": "official_china", "cacheHours": "3", **settings})


def _america():
    return _instance("china_box_office_top_movies", "China Movie Hot", "d" * 32,
                     {"sourceMode": "tmdb_cn_now_playing", "cacheHours": "6"})


def _payload(*extra, live_radar_uuid=OWNER_UUID):
    return {
        "schema_version": 1,
        "config_revision": 1,
        "resolution": [800, 480],
        "runtime_migrations": dict(MARKERS),
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


def test_startup_retires_the_north_america_page_once_the_china_chart_shows_both(monkeypatch, tmp_path):
    china = _china()
    config, path = _start(monkeypatch, tmp_path, _payload(china, _america()))

    saved = _saved(path)
    plugins = saved["playlist_config"]["playlists"][0]["plugins"]
    assert [item["plugin_id"] for item in plugins] == ["live_radar", "box_office_top_movies"]
    # The China chart keeps its settings: showing both markets is its default.
    assert plugins[1] == {**china, "latest_refresh_time": None}
    assert saved["runtime_migrations"] == {**MARKERS, migration.MIGRATION_ID: True}
    assert config.get_playlist_manager().find_plugin("china_box_office_top_movies", "China Movie Hot") is None

    revision = saved["config_revision"]
    Config()
    assert _saved(path)["config_revision"] == revision


def test_the_page_stays_without_a_two_market_china_chart_or_on_another_device(monkeypatch, tmp_path):
    for payload in (_payload(_america()),
                    _payload(_china(northAmericaColumn="false"), _america()),
                    _payload(_china(sourceMode="the_numbers"), _america()),
                    _payload(_china(), _america(), live_radar_uuid="b" * 32)):
        _config, path = _start(monkeypatch, tmp_path, payload)
        saved = _saved(path)
        plugins = [item["plugin_id"] for item in saved["playlist_config"]["playlists"][0]["plugins"]]
        assert "china_box_office_top_movies" in plugins
        assert migration.MIGRATION_ID not in saved["runtime_migrations"]
