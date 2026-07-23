import json
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path
import sys

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import config as config_module  # noqa: E402
from config import Config  # noqa: E402
from config_store import (  # noqa: E402
    ConfigCommitUncertainError,
    ConfigConflictError,
    ConfigStore,
)
from model import PlaylistManager  # noqa: E402
from nasapics_migration import (  # noqa: E402
    ExpectedNasapicsIdentity,
    NasapicsMigrationError,
    migrate_nasapics_instance,
)
from runtime_paths import RuntimePaths  # noqa: E402


TARGET_UUID = "11111111-1111-4111-8111-111111111111"
OTHER_APOD_UUID = "22222222-2222-4222-8222-222222222222"
OTHER_PLUGIN_UUID = "33333333-3333-4333-8333-333333333333"
SECOND_PLAYLIST_UUID = "44444444-4444-4444-8444-444444444444"
MIGRATION_ID = "nasapics_space_weather_v1"
MIGRATION_CLI = PROJECT_ROOT / "install" / "migrate_nasapics_instance.py"


def _target_instance(*, migrated=False):
    settings = {
        "randomizeApod": False if migrated else True,
        "customDate": "" if migrated else "2026-07-20",
        "refreshOnDisplay": False if migrated else True,
        "unknownNested": {
            "keep": ["alpha", {"secret": "do-not-log"}],
            "url": "https://example.invalid/private?token=do-not-log",
        },
        "themeMode": "day",
    }
    return {
        "plugin_id": "apod",
        "name": "NASAPics",
        "plugin_settings": settings,
        "refresh": {"interval": 1800} if migrated else {"interval": 300, "at": "04:00"},
        "latest_refresh_time": "2026-07-22T01:02:03+00:00",
        "instance_uuid": TARGET_UUID,
        "structural_generation": 7,
        "settings_revision": 11,
    }


def _device_document(*, target_count=1, migrated=False):
    plugins = [
        {
            "plugin_id": "apod",
            "name": "Other APOD",
            "plugin_settings": {"keep": "other-apod"},
            "refresh": {"interval": 7200},
            "latest_refresh_time": None,
            "instance_uuid": OTHER_APOD_UUID,
            "structural_generation": 2,
            "settings_revision": 3,
        }
    ]
    if target_count:
        plugins.append(_target_instance(migrated=migrated))
    if target_count > 1:
        duplicate = _target_instance(migrated=migrated)
        duplicate["instance_uuid"] = "55555555-5555-4555-8555-555555555555"
        plugins.append(duplicate)
    plugins.append(
        {
            "plugin_id": "weather",
            "name": "Weather",
            "plugin_settings": {"location": "Fremont"},
            "refresh": {"interval": 900},
            "latest_refresh_time": "2026-07-22T00:00:00+00:00",
            "instance_uuid": OTHER_PLUGIN_UUID,
            "structural_generation": 5,
            "settings_revision": 6,
        }
    )
    return {
        "schema_version": 1,
        "config_revision": 20,
        "resolution": [800, 480],
        "plugin_order": ["apod", "weather"],
        "opaque_top_level": {"keep": [1, 2, 3]},
        "runtime_migrations": {
            "existing_migration": {
                "opaque": "keep",
            }
        },
        "playlist_config": {
            "active_playlist": "DailyDoseOfDay",
            "playlists": [
                {
                    "name": "DailyDoseOfDay",
                    "start_time": "00:00",
                    "end_time": "24:00",
                    "plugins": plugins,
                    "current_plugin_index": 1,
                    "plugin_rotation_queue": [TARGET_UUID],
                    "plugin_rotation_pool": [OTHER_APOD_UUID, TARGET_UUID],
                    "plugin_rotation_recent_history": [OTHER_APOD_UUID],
                    "plugin_rotation_starved_since": None,
                },
                {
                    "name": "Night",
                    "start_time": "22:00",
                    "end_time": "24:00",
                    "plugins": [
                        {
                            "plugin_id": "clock",
                            "name": "Night Clock",
                            "plugin_settings": {"keep": True},
                            "refresh": {"interval": 60},
                            "latest_refresh_time": None,
                            "instance_uuid": SECOND_PLAYLIST_UUID,
                            "structural_generation": 1,
                            "settings_revision": 1,
                        }
                    ],
                    "current_plugin_index": 0,
                    "plugin_rotation_queue": [SECOND_PLAYLIST_UUID],
                    "plugin_rotation_pool": [SECOND_PLAYLIST_UUID],
                    "plugin_rotation_recent_history": [],
                    "plugin_rotation_starved_since": "2026-07-22T02:00:00+00:00",
                },
            ],
        },
        "refresh_info": {
            "refresh_time": "2026-07-22T00:30:00+00:00",
            "image_hash": "keep-image-hash",
            "refresh_type": "Playlist",
            "plugin_id": "weather",
            "playlist": "DailyDoseOfDay",
            "plugin_instance": "Weather",
            "opaque_refresh_field": {"keep": True},
        },
    }


def _runtime_paths(tmp_path, *, release_id="candidate"):
    config_file = tmp_path / "config" / "device.json"
    config_file.parent.mkdir(parents=True)
    return RuntimePaths(
        release_id=release_id,
        config_file=config_file,
        data_dir=tmp_path / "data",
        cache_dir=tmp_path / "cache",
        env_file=tmp_path / "inkypi.env",
        display_dir=tmp_path / "display",
        current_image_file=tmp_path / "display" / "current.png",
        plugin_image_dir=tmp_path / "plugins",
        flask_secret_file=tmp_path / "config" / "flask_secret",
    )


def _loaded_config(tmp_path, document=None):
    paths = _runtime_paths(tmp_path)
    payload = _device_document() if document is None else document
    paths.config_file.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return Config(runtime_paths=paths), paths


def _write_device_config(tmp_path, document=None, *, release_id="candidate"):
    paths = _runtime_paths(tmp_path, release_id=release_id)
    payload = _device_document() if document is None else document
    paths.config_file.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return paths


def _write_expectation(
    tmp_path,
    *,
    release_id="candidate",
    target_overrides=None,
    document_overrides=None,
):
    target = {
        "playlist_name": "DailyDoseOfDay",
        "plugin_id": "apod",
        "instance_name": "NASAPics",
        "instance_uuid": TARGET_UUID,
        "structural_generation": 7,
        "settings_revision": 11,
    }
    target.update(target_overrides or {})
    document = {
        "schema_version": 1,
        "migration": MIGRATION_ID,
        "release_id": release_id,
        "target": target,
    }
    document.update(document_overrides or {})
    expectation = tmp_path / ".nasapics-space-weather-v1.expectation.json"
    expectation.write_text(
        json.dumps(document, ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return expectation


def _marker(*, release_id="candidate"):
    return {
        "release_id": release_id,
        "instance_uuid": TARGET_UUID,
        "structural_generation": 7,
        "before_settings_revision": 11,
        "after_settings_revision": 12,
    }


def _load_migration_cli():
    loader = SourceFileLoader("migrate_nasapics_instance_cli", str(MIGRATION_CLI))
    spec = spec_from_loader(loader.name, loader)
    module = module_from_spec(spec)
    loader.exec_module(module)
    return module


def _expected(**overrides):
    values = {
        "instance_uuid": TARGET_UUID,
        "structural_generation": 7,
        "settings_revision": 11,
    }
    values.update(overrides)
    return ExpectedNasapicsIdentity(**values)


def _ordered_instance_uuids(playlist_config):
    return [
        [
            instance["instance_uuid"]
            for instance in playlist["plugins"]
        ]
        for playlist in playlist_config["playlists"]
    ]


def _target_from_playlist(playlist_config):
    matches = [
        instance
        for playlist in playlist_config["playlists"]
        if playlist["name"] == "DailyDoseOfDay"
        for instance in playlist["plugins"]
        if instance["plugin_id"] == "apod" and instance["name"] == "NASAPics"
    ]
    assert len(matches) == 1
    return matches[0]


def test_exact_migration_preserves_unknowns_other_instances_and_order(tmp_path):
    original = _device_document()
    config, paths = _loaded_config(tmp_path, original)
    before_playlist = config.get_config("playlist_config")
    before_order = _ordered_instance_uuids(before_playlist)
    before_target = _target_from_playlist(before_playlist)

    result = migrate_nasapics_instance(config, expected=_expected())

    persisted = json.loads(paths.config_file.read_text(encoding="utf-8"))
    after_playlist = persisted["playlist_config"]
    after_target = _target_from_playlist(after_playlist)
    expected_settings = dict(before_target["plugin_settings"])
    expected_settings.update(
        {
            "randomizeApod": False,
            "customDate": "",
            "refreshOnDisplay": False,
        }
    )
    assert after_target["plugin_settings"] == expected_settings
    assert after_target["refresh"] == {"interval": 1800}
    assert after_target["settings_revision"] == before_target["settings_revision"] + 1
    assert {
        key: after_target[key]
        for key in (
            "plugin_id",
            "name",
            "latest_refresh_time",
            "instance_uuid",
            "structural_generation",
        )
    } == {
        key: before_target[key]
        for key in (
            "plugin_id",
            "name",
            "latest_refresh_time",
            "instance_uuid",
            "structural_generation",
        )
    }
    assert _ordered_instance_uuids(after_playlist) == before_order
    assert after_playlist["playlists"][0]["plugins"][0] == before_playlist["playlists"][0]["plugins"][0]
    assert after_playlist["playlists"][0]["plugins"][-1] == before_playlist["playlists"][0]["plugins"][-1]
    assert after_playlist["playlists"][1] == before_playlist["playlists"][1]
    assert persisted["opaque_top_level"] == original["opaque_top_level"]
    assert persisted["plugin_order"] == original["plugin_order"]
    assert persisted["refresh_info"] == original["refresh_info"]
    assert result.playlist_name == "DailyDoseOfDay"
    assert result.before.instance_uuid == TARGET_UUID
    assert result.before.settings_revision == 11
    assert result.after.settings_revision == 12
    assert config.playlist_manager.snapshot_instance(TARGET_UUID) == result.after


def test_migration_rejects_zero_detached_targets(tmp_path):
    config, paths = _loaded_config(
        tmp_path,
        _device_document(target_count=0),
    )
    original = paths.config_file.read_bytes()

    with pytest.raises(NasapicsMigrationError, match="exactly one"):
        migrate_nasapics_instance(config, expected=_expected())

    assert paths.config_file.read_bytes() == original


def test_migration_counts_detached_targets_before_first_match_resolution():
    manager = PlaylistManager.from_dict(
        _device_document(target_count=2)["playlist_config"]
    )

    class DetachedConfig:
        playlist_manager = manager

        @staticmethod
        def capture_detached_playlist_transaction():
            return (
                1,
                manager.to_dict(),
                PlaylistManager.from_dict(manager.to_dict()),
            )

        @staticmethod
        def commit_detached_playlist_transaction(**_kwargs):
            raise AssertionError("ambiguous target must fail before persistence")

    with pytest.raises(NasapicsMigrationError, match="exactly one"):
        migrate_nasapics_instance(DetachedConfig(), expected=_expected())


@pytest.mark.parametrize(
    ("override", "message"),
    (
        ({"instance_uuid": "99999999-9999-4999-8999-999999999999"}, "UUID"),
        ({"structural_generation": 8}, "generation"),
        ({"settings_revision": 12}, "revision"),
    ),
)
def test_migration_rejects_each_identity_drift(tmp_path, override, message):
    config, paths = _loaded_config(tmp_path)
    original = paths.config_file.read_bytes()

    with pytest.raises(NasapicsMigrationError, match=message):
        migrate_nasapics_instance(config, expected=_expected(**override))

    assert paths.config_file.read_bytes() == original


def test_migration_rejects_already_migrated_target_without_marker(tmp_path):
    config, paths = _loaded_config(tmp_path, _device_document(migrated=True))
    original = paths.config_file.read_bytes()

    with pytest.raises(NasapicsMigrationError, match="already migrated"):
        migrate_nasapics_instance(config, expected=_expected())

    assert paths.config_file.read_bytes() == original


def test_migration_fails_closed_when_config_version_changes_before_cas(
    tmp_path,
    monkeypatch,
):
    config, paths = _loaded_config(tmp_path)
    real_transaction = config.commit_detached_playlist_transaction
    real_store_commit = config._config_store.commit
    calls = []

    def concurrent_then_migrate(**kwargs):
        calls.append("migration")
        state = config._config_store.current()
        concurrent = config.get_config()
        concurrent["concurrent_top_level"] = "preserve"
        real_store_commit(state.snapshot.version, concurrent)
        return real_transaction(**kwargs)

    monkeypatch.setattr(
        config,
        "commit_detached_playlist_transaction",
        concurrent_then_migrate,
    )

    with pytest.raises(ConfigConflictError):
        migrate_nasapics_instance(config, expected=_expected())

    persisted = json.loads(paths.config_file.read_text(encoding="utf-8"))
    assert calls == ["migration"]
    assert persisted["concurrent_top_level"] == "preserve"
    assert _target_from_playlist(persisted["playlist_config"])["settings_revision"] == 11
    assert config.get_config("concurrent_top_level") == "preserve"
    assert config.playlist_manager.snapshot_instance(TARGET_UUID).settings_revision == 11


def test_service_start_commits_migrated_model_and_marker_in_one_write(
    tmp_path,
    monkeypatch,
):
    paths = _write_device_config(tmp_path)
    expectation = _write_expectation(tmp_path)
    monkeypatch.setattr(
        config_module,
        "_nasapics_expectation_path",
        lambda: expectation,
        raising=False,
    )
    real_commit = ConfigStore.commit
    commits = []

    def recording_commit(store, expected_version, candidate):
        commits.append(json.loads(json.dumps(candidate)))
        return real_commit(store, expected_version, candidate)

    monkeypatch.setattr(ConfigStore, "commit", recording_commit)

    config = Config(runtime_paths=paths)

    assert len(commits) == 1
    committed = commits[0]
    assert committed["runtime_migrations"] == {
        "existing_migration": {"opaque": "keep"},
        MIGRATION_ID: _marker(),
    }
    committed_target = _target_from_playlist(committed["playlist_config"])
    assert committed_target["plugin_settings"]["randomizeApod"] is False
    assert committed_target["plugin_settings"]["customDate"] == ""
    assert committed_target["plugin_settings"]["refreshOnDisplay"] is False
    assert committed_target["refresh"] == {"interval": 1800}
    assert committed_target["settings_revision"] == 12
    assert config.get_config("runtime_migrations")[MIGRATION_ID] == _marker()
    assert expectation.is_file()


def test_valid_marker_makes_restarts_and_later_user_changes_zero_write(
    tmp_path,
    monkeypatch,
):
    document = _device_document(migrated=True)
    document["playlist_config"]["playlists"][0]["plugins"][1][
        "settings_revision"
    ] = 13
    document["playlist_config"]["playlists"][0]["plugins"][1][
        "plugin_settings"
    ]["customDate"] = "2026-07-21"
    document["runtime_migrations"][MIGRATION_ID] = _marker()
    paths = _write_device_config(tmp_path, document)
    expectation = _write_expectation(tmp_path)
    monkeypatch.setattr(
        config_module,
        "_nasapics_expectation_path",
        lambda: expectation,
        raising=False,
    )
    commits = []
    monkeypatch.setattr(
        ConfigStore,
        "commit",
        lambda *_args, **_kwargs: commits.append("unexpected"),
    )

    first_restart = Config(runtime_paths=paths)
    second_restart = Config(runtime_paths=paths)

    assert commits == []
    assert (
        first_restart.playlist_manager.snapshot_instance(TARGET_UUID).settings[
            "customDate"
        ]
        == "2026-07-21"
    )
    assert (
        second_restart.playlist_manager.snapshot_instance(TARGET_UUID).settings_revision
        == 13
    )


@pytest.mark.parametrize(
    "target_overrides",
    (
        {"instance_uuid": "99999999-9999-4999-8999-999999999999"},
        {"structural_generation": 8},
        {"settings_revision": 12},
    ),
)
def test_service_start_fails_closed_on_captured_identity_drift(
    tmp_path,
    monkeypatch,
    target_overrides,
):
    paths = _write_device_config(tmp_path)
    original = paths.config_file.read_bytes()
    expectation = _write_expectation(
        tmp_path,
        target_overrides=target_overrides,
    )
    monkeypatch.setattr(
        config_module,
        "_nasapics_expectation_path",
        lambda: expectation,
        raising=False,
    )

    with pytest.raises(NasapicsMigrationError, match="drift"):
        Config(runtime_paths=paths)

    assert paths.config_file.read_bytes() == original


@pytest.mark.parametrize(
    ("expectation_kwargs", "message"),
    (
        ({"release_id": "different"}, "release"),
        (
            {"document_overrides": {"schema_version": 2}},
            "expectation",
        ),
        (
            {"document_overrides": {"unexpected": True}},
            "expectation",
        ),
    ),
)
def test_service_start_rejects_malformed_or_release_mismatched_expectation(
    tmp_path,
    monkeypatch,
    expectation_kwargs,
    message,
):
    paths = _write_device_config(tmp_path)
    original = paths.config_file.read_bytes()
    expectation = _write_expectation(tmp_path, **expectation_kwargs)
    monkeypatch.setattr(
        config_module,
        "_nasapics_expectation_path",
        lambda: expectation,
        raising=False,
    )

    with pytest.raises(NasapicsMigrationError, match=message):
        Config(runtime_paths=paths)

    assert paths.config_file.read_bytes() == original


def test_service_start_rejects_already_migrated_target_without_marker(
    tmp_path,
    monkeypatch,
):
    paths = _write_device_config(tmp_path, _device_document(migrated=True))
    original = paths.config_file.read_bytes()
    expectation = _write_expectation(tmp_path)
    monkeypatch.setattr(
        config_module,
        "_nasapics_expectation_path",
        lambda: expectation,
        raising=False,
    )

    with pytest.raises(NasapicsMigrationError, match="already migrated"):
        Config(runtime_paths=paths)

    assert paths.config_file.read_bytes() == original


def test_service_start_rejects_invalid_marker_instead_of_inferring_success(
    tmp_path,
    monkeypatch,
):
    document = _device_document(migrated=True)
    document["runtime_migrations"][MIGRATION_ID] = {
        **_marker(),
        "after_settings_revision": 99,
    }
    paths = _write_device_config(tmp_path, document)
    original = paths.config_file.read_bytes()
    expectation = _write_expectation(tmp_path)
    monkeypatch.setattr(
        config_module,
        "_nasapics_expectation_path",
        lambda: expectation,
        raising=False,
    )

    with pytest.raises(NasapicsMigrationError, match="marker"):
        Config(runtime_paths=paths)

    assert paths.config_file.read_bytes() == original


def test_service_start_rejects_boolean_marker_revisions(tmp_path, monkeypatch):
    document = _device_document(migrated=True)
    document["runtime_migrations"][MIGRATION_ID] = {
        "release_id": "candidate",
        "instance_uuid": TARGET_UUID,
        "structural_generation": True,
        "before_settings_revision": True,
        "after_settings_revision": 2,
    }
    paths = _write_device_config(tmp_path, document)
    expectation = _write_expectation(
        tmp_path,
        target_overrides={
            "structural_generation": 1,
            "settings_revision": 1,
        },
    )
    monkeypatch.setattr(
        config_module,
        "_nasapics_expectation_path",
        lambda: expectation,
        raising=False,
    )

    with pytest.raises(NasapicsMigrationError, match="marker"):
        Config(runtime_paths=paths)


def test_service_start_conflict_reloads_authoritative_model_and_does_not_retry(
    tmp_path,
    monkeypatch,
):
    paths = _write_device_config(tmp_path)
    expectation = _write_expectation(tmp_path)
    monkeypatch.setattr(
        config_module,
        "_nasapics_expectation_path",
        lambda: expectation,
        raising=False,
    )
    real_load = ConfigStore.load
    load_calls = []
    commit_calls = []

    def recording_load(store):
        load_calls.append("load")
        return real_load(store)

    def conflicting_commit(_store, expected_version, _candidate):
        commit_calls.append(expected_version)
        raise ConfigConflictError(expected_version, expected_version + 1)

    monkeypatch.setattr(ConfigStore, "load", recording_load)
    monkeypatch.setattr(ConfigStore, "commit", conflicting_commit)

    with pytest.raises(ConfigConflictError):
        Config(runtime_paths=paths)

    assert commit_calls == [20]
    assert load_calls == ["load", "load"]


def test_service_start_commit_uncertain_fails_without_reload_or_retry(
    tmp_path,
    monkeypatch,
):
    paths = _write_device_config(tmp_path)
    expectation = _write_expectation(tmp_path)
    monkeypatch.setattr(
        config_module,
        "_nasapics_expectation_path",
        lambda: expectation,
        raising=False,
    )
    real_load = ConfigStore.load
    load_calls = []
    commit_calls = []

    def recording_load(store):
        load_calls.append("load")
        return real_load(store)

    def uncertain_commit(store, _expected_version, _candidate):
        commit_calls.append("commit")
        raise ConfigCommitUncertainError(store.config_path)

    monkeypatch.setattr(ConfigStore, "load", recording_load)
    monkeypatch.setattr(ConfigStore, "commit", uncertain_commit)

    with pytest.raises(ConfigCommitUncertainError):
        Config(runtime_paths=paths)

    assert commit_calls == ["commit"]
    assert load_calls == ["load"]


def test_debug_cli_requires_all_three_expected_identity_fields(tmp_path):
    cli = _load_migration_cli()
    paths = _write_device_config(tmp_path)
    base = ["--config", str(paths.config_file)]
    required = {
        "--expected-uuid": TARGET_UUID,
        "--expected-generation": "7",
        "--expected-settings-revision": "11",
    }

    for omitted in required:
        argv = list(base)
        for option, value in required.items():
            if option != omitted:
                argv.extend((option, value))
        with pytest.raises(SystemExit) as error:
            cli.main(argv)
        assert error.value.code == 2


def test_debug_cli_uses_explicit_runtime_paths_and_prints_sanitized_json(
    tmp_path,
    capsys,
):
    cli = _load_migration_cli()
    paths = _write_device_config(tmp_path)

    exit_code = cli.main(
        [
            "--config",
            str(paths.config_file),
            "--expected-uuid",
            TARGET_UUID,
            "--expected-generation",
            "7",
            "--expected-settings-revision",
            "11",
        ]
    )

    assert exit_code == 0
    output = capsys.readouterr()
    body = json.loads(output.out)
    assert body["ok"] is True
    assert body["playlist_name"] == "DailyDoseOfDay"
    assert body["before"]["instance_uuid"] == TARGET_UUID
    assert body["before"]["settings_revision"] == 11
    assert body["after"]["settings_revision"] == 12
    assert body["after"]["approved_settings"] == {
        "randomizeApod": False,
        "customDate": "",
        "refreshOnDisplay": False,
    }
    assert body["after"]["refresh"] == {"interval": 1800}
    combined = output.out + output.err
    assert "do-not-log" not in combined
    assert "example.invalid" not in combined
    assert "token=" not in combined
    persisted = json.loads(paths.config_file.read_text(encoding="utf-8"))
    assert _target_from_playlist(persisted["playlist_config"])["refresh"] == {
        "interval": 1800
    }


def test_debug_cli_mismatch_fails_with_only_sanitized_error_type(
    tmp_path,
    capsys,
):
    cli = _load_migration_cli()
    paths = _write_device_config(tmp_path)

    exit_code = cli.main(
        [
            "--config",
            str(paths.config_file),
            "--expected-uuid",
            "99999999-9999-4999-8999-999999999999",
            "--expected-generation",
            "7",
            "--expected-settings-revision",
            "11",
        ]
    )

    assert exit_code == 1
    output = capsys.readouterr()
    error = json.loads(output.err)
    assert error == {
        "ok": False,
        "error": "NasapicsMigrationError",
    }
    combined = output.out + output.err
    assert "do-not-log" not in combined
    assert "example.invalid" not in combined
    assert "token=" not in combined
