import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import starter_rotation_migration  # noqa: E402
from config import Config  # noqa: E402


def _start(monkeypatch, tmp_path, payload):
    path = tmp_path / "device.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(Config, "config_file", str(path))
    return Config(), path


def _saved(path):
    return json.loads(path.read_text(encoding="utf-8"))


FRESH = {"schema_version": 1, "config_revision": 1, "resolution": [800, 480], "display_type": "epd7in3e",
         "runtime_migrations": {"starter_rotation_v1": False}}


def test_a_fresh_install_starts_with_the_starter_rotation_once(monkeypatch, tmp_path):
    _config, path = _start(monkeypatch, tmp_path, FRESH)

    saved = _saved(path)
    playlists = saved["playlist_config"]["playlists"]
    assert [playlist["name"] for playlist in playlists] == ["Default"]
    pages = [(item["plugin_id"], item["refresh"]["interval"]) for item in playlists[0]["plugins"]]
    assert pages == [(plugin_id, interval) for plugin_id, _name, interval in starter_rotation_migration.PAGES]
    assert all(item["instance_uuid"] for item in playlists[0]["plugins"])
    assert saved["runtime_migrations"]["starter_rotation_v1"] is True
    revision = saved["config_revision"]
    Config()
    assert _saved(path)["config_revision"] == revision


def test_a_config_that_never_asked_for_it_is_left_alone(monkeypatch, tmp_path):
    plain = {key: value for key, value in FRESH.items() if key != "runtime_migrations"}
    _config, path = _start(monkeypatch, tmp_path, plain)
    saved = _saved(path)
    assert not (saved.get("playlist_config") or {}).get("playlists")
    assert "starter_rotation_v1" not in saved.get("runtime_migrations", {})


def test_the_installers_starting_config_asks_for_the_starter_rotation():
    base = Path(__file__).resolve().parents[1] / "install" / "config_base" / "device.json"
    assert json.loads(base.read_text(encoding="utf-8"))["runtime_migrations"] == {"starter_rotation_v1": False}


def test_a_device_with_any_page_or_the_opt_out_marker_is_left_alone(monkeypatch, tmp_path):
    existing = dict(FRESH, playlist_config={"playlists": [{"name": "Mine", "start_time": "00:00", "end_time": "24:00",
                                                           "plugins": [{"plugin_id": "clock", "name": "Clock",
                                                                        "plugin_settings": {}, "refresh": {"interval": 300},
                                                                        "instance_uuid": "c" * 32,
                                                                        "structural_generation": 1,
                                                                        "settings_revision": 1}]}],
                                            "active_playlist": "Mine"})
    _config, path = _start(monkeypatch, tmp_path, existing)
    saved = _saved(path)
    assert [item["plugin_id"] for item in saved["playlist_config"]["playlists"][0]["plugins"]] == ["clock"]
    # The request is answered (nothing to add), so it is not looked at again.
    assert saved["runtime_migrations"]["starter_rotation_v1"] is True

    opted_out = dict(FRESH, runtime_migrations={"starter_rotation_v1": True})
    _config, path = _start(monkeypatch, tmp_path, opted_out)
    assert not (_saved(path).get("playlist_config") or {}).get("playlists")


def test_every_starter_page_is_a_packaged_plugin():
    plugins = Path(__file__).resolve().parents[1] / "src" / "plugins"
    for plugin_id, _name, _interval in starter_rotation_migration.PAGES:
        assert (plugins / plugin_id / "plugin-info.json").is_file(), plugin_id
