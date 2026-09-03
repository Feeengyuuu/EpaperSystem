import copy
import json
from pathlib import Path
import sys
import uuid


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from config import Config  # noqa: E402


MIGRATION_ID = "runtime_balance_soft_threshold_v1"
CADENCE_MIGRATION_ID = "runtime_balance_cadence_v1"
SOFT_THRESHOLD_KEY = "background_cache_refresh_min_available_mb"


def _document(*, threshold=200, migrations=None):
    instance = {
        "plugin_id": "weather",
        "name": "Not Production Weather",
        "plugin_settings": {"opaque": {"keep": True}},
        "refresh": {"interval": 300, "opaque": "keep"},
        "latest_refresh_time": "2026-09-03T12:00:00-07:00",
        "instance_uuid": uuid.uuid4().hex,
        "structural_generation": 4,
        "settings_revision": 9,
    }
    document = {
        "schema_version": 1,
        "config_revision": 70,
        "resolution": [800, 480],
        "opaque_top_level": {"keep": [1, 2, 3]},
        "runtime_migrations": dict(migrations or {}),
        "refresh_info": {
            "refresh_time": None,
            "image_hash": None,
            "refresh_type": None,
            "plugin_id": None,
        },
        "playlist_config": {
            "active_playlist": "DailyDoseOfDay",
            "playlists": [
                {
                    "name": "DailyDoseOfDay",
                    "start_time": "00:00",
                    "end_time": "24:00",
                    "plugins": [instance],
                    "current_plugin_index": None,
                    "plugin_rotation_queue": [],
                    "plugin_rotation_pool": [],
                    "plugin_rotation_recent_history": [],
                    "plugin_rotation_starved_since": None,
                }
            ],
        },
    }
    if threshold is not None:
        document[SOFT_THRESHOLD_KEY] = threshold
    return document


def _write_and_load(monkeypatch, tmp_path, document):
    config_path = tmp_path / "device.json"
    config_path.write_text(json.dumps(document), encoding="utf-8")
    monkeypatch.setattr(Config, "config_file", str(config_path))
    return Config(), config_path


def test_startup_changes_only_exact_200_mib_soft_threshold(monkeypatch, tmp_path):
    original = _document(
        migrations={
            "existing_migration_v1": True,
            CADENCE_MIGRATION_ID: True,
        }
    )
    config, config_path = _write_and_load(monkeypatch, tmp_path, original)

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    expected = copy.deepcopy(original)
    expected[SOFT_THRESHOLD_KEY] = 175
    expected["runtime_migrations"][MIGRATION_ID] = True
    expected["config_revision"] = 71
    assert saved == expected
    assert config.get_config(SOFT_THRESHOLD_KEY) == 175

    backup = json.loads(
        config_path.with_name("device.lkg.2.json").read_text(encoding="utf-8")
    )
    assert backup == original


def test_soft_threshold_marker_preserves_later_operator_choice(monkeypatch, tmp_path):
    document = _document(
        threshold=200,
        migrations={CADENCE_MIGRATION_ID: True, MIGRATION_ID: True},
    )
    config, config_path = _write_and_load(monkeypatch, tmp_path, document)
    original = config_path.read_bytes()

    restarted = Config()

    assert config_path.read_bytes() == original
    assert config.get_config(SOFT_THRESHOLD_KEY) == 200
    assert restarted.get_config(SOFT_THRESHOLD_KEY) == 200


def test_nonlegacy_soft_threshold_values_do_not_write(monkeypatch, tmp_path):
    for threshold in (None, 150, 175, 200.0, "200", True):
        directory = tmp_path / str(threshold).replace(".", "_")
        directory.mkdir()
        document = _document(
            threshold=threshold,
            migrations={CADENCE_MIGRATION_ID: True},
        )
        config_path = directory / "device.json"
        config_path.write_text(json.dumps(document), encoding="utf-8")
        original = config_path.read_bytes()
        monkeypatch.setattr(Config, "config_file", str(config_path))

        config = Config()

        assert config_path.read_bytes() == original
        assert MIGRATION_ID not in config.get_config("runtime_migrations", default={})


def test_exact_200_without_cadence_phase_marker_does_not_write(monkeypatch, tmp_path):
    document = _document(
        threshold=200,
        migrations={"existing_migration_v1": True},
    )
    config, config_path = _write_and_load(monkeypatch, tmp_path, document)
    original = config_path.read_bytes()

    restarted = Config()

    assert config_path.read_bytes() == original
    assert config.get_config(SOFT_THRESHOLD_KEY) == 200
    assert restarted.get_config(SOFT_THRESHOLD_KEY) == 200
    assert config.get_config("runtime_migrations") == {"existing_migration_v1": True}
