import json
from pathlib import Path
import sys
import uuid

from PIL import Image
import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from config import Config, ConfigLoadError  # noqa: E402
from runtime.cache_catalog import CacheCatalog, authoritative_cache_path  # noqa: E402
from runtime.runtime_state import InstanceRuntimeState  # noqa: E402


MIGRATION_ID = "runtime_balance_cadence_v1"
SOFT_THRESHOLD_MIGRATION_ID = "runtime_balance_soft_threshold_v1"
SOFT_THRESHOLD_KEY = "background_cache_refresh_min_available_mb"
TARGETS = (
    ("d45cd9dc716240bea25e3eb77aef406d", "weather", "AwesomeWeather", 1, 900),
    (
        "c77bdce845dc451aad3e5439a1fdc21b",
        "steam_profile_dashboard",
        "SteamDaily",
        1,
        900,
    ),
    (
        "8e80501ddeaf423f8813edfa24c2fcd2",
        "daily_word_poem",
        "DailyWord",
        2,
        3600,
    ),
    (
        "8e0046a5330149d9903ef4a456df827e",
        "gcd_comic_covers",
        "ComicCovers",
        1,
        3600,
    ),
    (
        "462bd9102b004ca1b71ed63c959909af",
        "daily_art",
        "DailyArt",
        2,
        3600,
    ),
)


def _instance(plugin_id, name, *, interval=300, revision=1, instance_uuid=None):
    return {
        "plugin_id": plugin_id,
        "name": name,
        "plugin_settings": {"opaque": {"keep": [1, 2, 3]}},
        "refresh": {"interval": interval, "opaque": "keep"},
        "latest_refresh_time": "2026-09-03T12:00:00-07:00",
        "instance_uuid": instance_uuid or uuid.uuid4().hex,
        "structural_generation": 1,
        "settings_revision": revision,
    }


def _document(*, migrations=None, soft_threshold=None):
    document = {
        "schema_version": 1,
        "config_revision": 40,
        "resolution": [800, 480],
        "opaque_top_level": {"keep": True},
        "runtime_migrations": {
            "daily_art_gallery_decor_v1": True,
            **dict(migrations or {}),
        },
        "playlist_config": {
            "active_playlist": "DailyDoseOfDay",
            "playlists": [
                {
                    "name": "DailyDoseOfDay",
                    "start_time": "00:00",
                    "end_time": "24:00",
                    "plugins": [
                        *[
                            _instance(
                                plugin_id,
                                name,
                                revision=revision,
                                instance_uuid=instance_uuid,
                            )
                            for instance_uuid, plugin_id, name, revision, _interval in TARGETS
                        ],
                        _instance("live_radar", "LiveRadar", interval=300, revision=2),
                        _instance("bambu_monitor", "Bambu", interval=300, revision=1),
                        _instance("weather", "User Weather", interval=300, revision=20),
                        _instance("unrelated", "KeepMe", interval=7200, revision=21),
                    ],
                }
            ],
        },
    }
    if soft_threshold is not None:
        document[SOFT_THRESHOLD_KEY] = soft_threshold
    return document


def _write_and_load(monkeypatch, tmp_path, document):
    config_path = tmp_path / "device.json"
    config_path.write_text(json.dumps(document), encoding="utf-8")
    monkeypatch.setattr(Config, "config_file", str(config_path))
    return Config(), config_path


def _instance_by_identity(document, plugin_id, name):
    matches = [
        instance
        for playlist in document["playlist_config"]["playlists"]
        for instance in playlist["plugins"]
        if instance["plugin_id"] == plugin_id and instance["name"] == name
    ]
    assert len(matches) == 1
    return matches[0]


def test_startup_migrates_only_exact_legacy_300_second_cadences(monkeypatch, tmp_path):
    original = _document()
    config, config_path = _write_and_load(monkeypatch, tmp_path, original)

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    backup = json.loads(
        config_path.with_name("device.lkg.2.json").read_text(encoding="utf-8")
    )

    assert saved["config_revision"] == 41
    assert backup == original
    assert saved["opaque_top_level"] == original["opaque_top_level"]
    assert saved["runtime_migrations"][MIGRATION_ID] is True
    for _uuid, plugin_id, name, before_revision, target_interval in TARGETS:
        before = _instance_by_identity(original, plugin_id, name)
        after = _instance_by_identity(saved, plugin_id, name)
        assert after["refresh"] == {"interval": target_interval, "opaque": "keep"}
        assert before["settings_revision"] == before_revision
        expected_revision = before_revision
        assert after["settings_revision"] == expected_revision
        assert after["plugin_settings"] == before["plugin_settings"]
        runtime = config.get_playlist_manager().find_plugin(plugin_id, name)
        assert runtime.refresh == after["refresh"]
        assert runtime.settings_revision == expected_revision

    for plugin_id, name in (("live_radar", "LiveRadar"), ("bambu_monitor", "Bambu")):
        assert _instance_by_identity(saved, plugin_id, name) == _instance_by_identity(
            original,
            plugin_id,
            name,
        )

    custom = _instance_by_identity(saved, "weather", "User Weather")
    assert custom["refresh"] == {"interval": 300, "opaque": "keep"}
    assert custom["settings_revision"] == 20
    unrelated = _instance_by_identity(saved, "unrelated", "KeepMe")
    assert unrelated == _instance_by_identity(original, "unrelated", "KeepMe")


def test_direct_soft_release_from_pre_cadence_config_stages_across_two_starts(
    monkeypatch,
    tmp_path,
):
    original = _document(soft_threshold=200)

    first_config, config_path = _write_and_load(monkeypatch, tmp_path, original)
    first_saved = json.loads(config_path.read_text(encoding="utf-8"))

    assert first_saved["config_revision"] == 41
    assert first_saved["runtime_migrations"][MIGRATION_ID] is True
    assert SOFT_THRESHOLD_MIGRATION_ID not in first_saved["runtime_migrations"]
    assert first_saved[SOFT_THRESHOLD_KEY] == 200
    assert first_config.get_config(SOFT_THRESHOLD_KEY) == 200
    for _uuid, plugin_id, name, _revision, target_interval in TARGETS:
        assert _instance_by_identity(first_saved, plugin_id, name)["refresh"][
            "interval"
        ] == target_interval

    second_config = Config()
    second_saved = json.loads(config_path.read_text(encoding="utf-8"))

    assert second_saved["config_revision"] == 42
    assert second_saved["runtime_migrations"][MIGRATION_ID] is True
    assert second_saved["runtime_migrations"][SOFT_THRESHOLD_MIGRATION_ID] is True
    assert second_saved[SOFT_THRESHOLD_KEY] == 175
    assert second_config.get_config(SOFT_THRESHOLD_KEY) == 175


def test_soft_release_after_persisted_cadence_stage_applies_on_first_start(
    monkeypatch,
    tmp_path,
):
    staged = _document(
        migrations={MIGRATION_ID: True},
        soft_threshold=200,
    )
    for _uuid, plugin_id, name, _revision, target_interval in TARGETS:
        _instance_by_identity(staged, plugin_id, name)["refresh"][
            "interval"
        ] = target_interval

    config, config_path = _write_and_load(monkeypatch, tmp_path, staged)
    saved = json.loads(config_path.read_text(encoding="utf-8"))

    assert saved["config_revision"] == 41
    assert saved["runtime_migrations"][MIGRATION_ID] is True
    assert saved["runtime_migrations"][SOFT_THRESHOLD_MIGRATION_ID] is True
    assert saved[SOFT_THRESHOLD_KEY] == 175
    assert config.get_config(SOFT_THRESHOLD_KEY) == 175


def test_cadence_marker_makes_restart_zero_write_and_preserves_later_choices(
    monkeypatch,
    tmp_path,
):
    document = _document(migrations={MIGRATION_ID: True})
    weather = _instance_by_identity(document, "weather", "AwesomeWeather")
    weather["refresh"]["interval"] = 600
    weather["settings_revision"] = 99
    config, config_path = _write_and_load(monkeypatch, tmp_path, document)
    first = config_path.read_bytes()

    restarted = Config()

    assert config_path.read_bytes() == first
    persisted = restarted.get_playlist_manager().find_plugin(
        "weather",
        "AwesomeWeather",
    )
    assert persisted.refresh["interval"] == 600
    assert persisted.settings_revision == 99


def test_startup_cadence_migration_keeps_existing_display_caches_eligible(
    monkeypatch,
    tmp_path,
):
    original = _document()
    cache_root = tmp_path / ".refresh-cache"
    expected_paths = {}
    for instance_uuid, _plugin_id, _name, revision, _interval in TARGETS:
        path = Path(
            authoritative_cache_path(
                cache_root,
                instance_uuid,
                1,
                revision,
                None,
            )
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (8, 8), "white").save(path, format="PNG")
        expected_paths[instance_uuid] = path

    config, _config_path = _write_and_load(monkeypatch, tmp_path, original)
    catalog = CacheCatalog(cache_root)

    for instance_uuid, _plugin_id, _name, revision, target_interval in TARGETS:
        migrated = config.get_playlist_manager().snapshot_instance(instance_uuid)
        assert migrated.refresh["interval"] == target_interval
        assert migrated.structural_generation == 1
        assert migrated.settings_revision == revision
        assert Path(
            authoritative_cache_path(
                cache_root,
                migrated.instance_uuid,
                migrated.structural_generation,
                migrated.settings_revision,
                None,
            )
        ) == expected_paths[instance_uuid]
        candidate = catalog.resolve(migrated, None, InstanceRuntimeState())
        assert candidate is not None
        assert Path(candidate.cache_path) == expected_paths[instance_uuid]
        image = catalog.load_image(candidate)
        assert image is not None
        image.close()


def test_cadence_migration_rejects_target_drift_without_writing(monkeypatch, tmp_path):
    document = _document()
    weather = _instance_by_identity(document, "weather", "AwesomeWeather")
    weather["refresh"]["interval"] = 600
    weather["settings_revision"] = 17
    config_path = tmp_path / "device.json"
    original = json.dumps(document).encode("utf-8")
    config_path.write_bytes(original)
    monkeypatch.setattr(Config, "config_file", str(config_path))

    with pytest.raises(ConfigLoadError, match="target drifted"):
        Config()

    assert config_path.read_bytes() == original


def test_partial_production_target_set_aborts_before_write(monkeypatch, tmp_path):
    document = _document()
    plugins = document["playlist_config"]["playlists"][0]["plugins"]
    plugins[:] = [
        instance
        for instance in plugins
        if instance["plugin_id"] != "daily_art"
    ]
    config_path = tmp_path / "device.json"
    original = json.dumps(document).encode("utf-8")
    config_path.write_bytes(original)
    monkeypatch.setattr(Config, "config_file", str(config_path))

    with pytest.raises(ConfigLoadError, match="target set is incomplete"):
        Config()

    assert config_path.read_bytes() == original


def test_non_target_device_does_not_gain_a_marker_or_write(monkeypatch, tmp_path):
    document = _document()
    for instance in document["playlist_config"]["playlists"][0]["plugins"]:
        instance["instance_uuid"] = uuid.uuid4().hex
    config_path = tmp_path / "device.json"
    original = json.dumps(document).encode("utf-8")
    config_path.write_bytes(original)
    monkeypatch.setattr(Config, "config_file", str(config_path))

    config = Config()

    assert config_path.read_bytes() == original
    assert MIGRATION_ID not in config.get_config("runtime_migrations", default={})
