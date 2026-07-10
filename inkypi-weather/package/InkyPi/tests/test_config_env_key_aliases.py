import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import config as config_module
from config import Config, ConfigLoadError
from config_store import ConfigConflictError
from model import PlaylistManager, RefreshInfo
from runtime_paths import RuntimePaths

TEST_CACHE_ROOT = Path(__file__).resolve().parents[4] / ".tmp" / "config_env_key_aliases_tests"


def _device_config(monkeypatch, tmp_path, payload=None):
    config_path = tmp_path / "device.json"
    if payload is not None:
        config_path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(Config, "config_file", str(config_path))
    return Config(), config_path


def cache_dir_for(name):
    path = TEST_CACHE_ROOT / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def test_load_env_key_accepts_user_named_groq_aliases(monkeypatch):
    env_path = cache_dir_for("groq") / ".env"
    env_path.write_text(
        "\n".join([
            "Groq_V2=groq-v2-value",
            "GROQ_KEY=groq-key-value",
        ]),
        encoding="utf-8",
    )
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("Groq_V2", raising=False)
    monkeypatch.delenv("GROQ_KEY", raising=False)

    config = Config.__new__(Config)
    monkeypatch.setattr(config, "_env_file_candidates", lambda: [str(env_path)])

    assert config.load_env_key("GROQ_API_KEY") == "groq-v2-value"


def test_load_env_key_does_not_alias_unrelated_keys(monkeypatch):
    env_path = cache_dir_for("unrelated") / ".env"
    env_path.write_text("Groq_V2=groq-v2-value\n", encoding="utf-8")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("Groq_V2", raising=False)

    config = Config.__new__(Config)
    monkeypatch.setattr(config, "_env_file_candidates", lambda: [str(env_path)])

    assert config.load_env_key("OPENAI_API_KEY") == ""


def test_load_env_key_accepts_pixiv_phpsessid_cookie_aliases(monkeypatch):
    env_path = cache_dir_for("pixiv") / ".env"
    env_path.write_text("PIXIV_COOKIE=pixiv-cookie-value\n", encoding="utf-8")
    monkeypatch.delenv("PIXIV_PHPSESSID", raising=False)
    monkeypatch.delenv("PIXIV_COOKIE", raising=False)
    monkeypatch.delenv("PIXIV_SESSION", raising=False)

    config = Config.__new__(Config)
    monkeypatch.setattr(config, "_env_file_candidates", lambda: [str(env_path)])

    assert config.load_env_key("PIXIV_PHPSESSID") == "pixiv-cookie-value"


def test_load_env_key_accepts_telegram_account_aliases(monkeypatch):
    env_path = cache_dir_for("telegram") / ".env"
    env_path.write_text(
        "\n".join([
            "TG_API_ID=12345",
            "TG_API_HASH=hash-value",
            "TELEGRAM_ACCOUNT_SESSION=/tmp/telegram_account",
        ]),
        encoding="utf-8",
    )
    for key in (
        "TELEGRAM_API_ID",
        "TG_API_ID",
        "TELEGRAM_API_HASH",
        "TG_API_HASH",
        "TELEGRAM_SESSION_PATH",
        "TELEGRAM_ACCOUNT_SESSION",
    ):
        monkeypatch.delenv(key, raising=False)

    config = Config.__new__(Config)
    monkeypatch.setattr(config, "_env_file_candidates", lambda: [str(env_path)])

    assert config.load_env_key("TELEGRAM_API_ID") == "12345"
    assert config.load_env_key("TELEGRAM_API_HASH") == "hash-value"
    assert config.load_env_key("TELEGRAM_SESSION_PATH") == "/tmp/telegram_account"


@pytest.mark.parametrize(
    ("canonical", "alias"),
    [
        ("OPENAI_API_KEY", "OPEN_AI_SECRET"),
        ("TICKETMASTER_API_KEY", "TICKETMASTER_CONSUMER_KEY"),
        ("TELEGRAM_BOT_TOKEN", "TG_BOT_TOKEN"),
        ("BAMBU_ACCESS_CODE", "BAMBU_LAB_ACCESS_CODE"),
    ],
)
def test_load_env_key_resolves_every_declared_migration_alias(
    monkeypatch,
    canonical,
    alias,
):
    monkeypatch.delenv(canonical, raising=False)
    monkeypatch.setenv(alias, "legacy-value")
    config = Config.__new__(Config)
    monkeypatch.setattr(config, "_env_file_candidates", lambda: [])

    assert config.load_env_key(canonical) == "legacy-value"
    assert config.load_env_key(alias) == "legacy-value"


def test_canonical_secret_name_wins_when_legacy_alias_is_also_set(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "canonical-value")
    monkeypatch.setenv("OPEN_AI_SECRET", "legacy-value")
    config = Config.__new__(Config)
    monkeypatch.setattr(config, "_env_file_candidates", lambda: [])

    assert config.load_env_key("OPEN_AI_SECRET") == "canonical-value"


def test_load_env_key_picks_up_modified_env_file(monkeypatch):
    env_path = cache_dir_for("mtime_change") / ".env"
    env_path.write_text("INKYPI_TEST_MTIME_KEY=old-value\n", encoding="utf-8")
    monkeypatch.delenv("INKYPI_TEST_MTIME_KEY", raising=False)

    config = Config.__new__(Config)
    monkeypatch.setattr(config, "_env_file_candidates", lambda: [str(env_path)])

    assert config.load_env_key("INKYPI_TEST_MTIME_KEY") == "old-value"

    env_path.write_text("INKYPI_TEST_MTIME_KEY=new-value\n", encoding="utf-8")
    stat = env_path.stat()
    os.utime(env_path, (stat.st_atime, stat.st_mtime + 2))

    assert config.load_env_key("INKYPI_TEST_MTIME_KEY") == "new-value"


def test_load_env_key_does_not_reparse_unchanged_env_files(monkeypatch):
    env_path = cache_dir_for("no_reparse") / ".env"
    env_path.write_text("INKYPI_TEST_CACHED_KEY=cached-value\n", encoding="utf-8")
    monkeypatch.delenv("INKYPI_TEST_CACHED_KEY", raising=False)

    config = Config.__new__(Config)
    monkeypatch.setattr(config, "_env_file_candidates", lambda: [str(env_path)])

    calls = []
    real_load_dotenv = config_module.load_dotenv

    def counting_load_dotenv(*args, **kwargs):
        calls.append((args, kwargs))
        return real_load_dotenv(*args, **kwargs)

    monkeypatch.setattr(config_module, "load_dotenv", counting_load_dotenv)

    assert config.load_env_key("INKYPI_TEST_CACHED_KEY") == "cached-value"
    first_call_count = len(calls)
    assert first_call_count > 0

    assert config.load_env_key("INKYPI_TEST_CACHED_KEY") == "cached-value"
    assert len(calls) == first_call_count


def test_write_config_persists_device_json_through_store(monkeypatch, tmp_path):
    config, config_path = _device_config(
        monkeypatch,
        tmp_path,
        {"resolution": [800, 480]},
    )
    config.playlist_manager = PlaylistManager()
    config.refresh_info = RefreshInfo(
        refresh_type="Playlist",
        plugin_id="clock",
        refresh_time="2026-06-04T12:00:00+00:00",
        image_hash="abc",
    )

    config.write_config()

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["resolution"] == [800, 480]
    assert saved["playlist_config"] == {"playlists": [], "active_playlist": None}
    assert saved["refresh_info"]["plugin_id"] == "clock"
    assert saved["schema_version"] == 1
    assert saved["config_revision"] == 1


def test_read_config_reports_invalid_json_without_overwriting_it(tmp_path):
    config = Config.__new__(Config)
    config.config_file = str(tmp_path / "device.json")
    original = b"{bad json"
    (tmp_path / "device.json").write_bytes(original)

    with pytest.raises(ConfigLoadError, match="invalid"):
        config.read_config()

    assert (tmp_path / "device.json").read_bytes() == original


def test_constructor_reports_invalid_config_instead_of_booting_with_empty_state(
    monkeypatch,
    tmp_path,
):
    config_path = tmp_path / "device.json"
    original = b"{bad json"
    config_path.write_bytes(original)
    monkeypatch.setattr(Config, "config_file", str(config_path))

    with pytest.raises(ConfigLoadError, match="invalid"):
        Config()

    assert config_path.read_bytes() == original


def test_constructor_exposes_recovered_lkg_snapshot_instead_of_empty_state(
    monkeypatch,
    tmp_path,
):
    config_path = tmp_path / "device.json"
    config_path.write_text("{bad json", encoding="utf-8")
    lkg_path = tmp_path / "device.lkg.1.json"
    lkg_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "config_revision": 7,
                "resolution": [800, 480],
                "name": "Recovered",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(Config, "config_file", str(config_path))

    config = Config()

    assert config.get_config("name") == "Recovered"
    assert config.get_config("config_revision") == 7
    assert json.loads(config_path.read_text(encoding="utf-8"))["name"] == "Recovered"
    assert list(tmp_path.glob("device.corrupt.*.json"))


def test_missing_config_stays_missing_until_a_valid_transaction_bootstraps_it(
    monkeypatch,
    tmp_path,
):
    config, config_path = _device_config(monkeypatch, tmp_path)

    assert config.get_config() == {}
    assert not config_path.exists()

    config.update_value("resolution", [800, 480])

    assert config_path.exists()
    assert config.get_config("resolution") == [800, 480]
    assert config.get_config("config_revision") == 1


def test_partial_legacy_config_can_boot_then_persist_detected_resolution(
    monkeypatch,
    tmp_path,
):
    config, config_path = _device_config(
        monkeypatch,
        tmp_path,
        {"name": "Legacy", "display_type": "inky"},
    )

    assert config.get_config("resolution") is None

    config.update_value("resolution", [800, 480], write=True)

    persisted = json.loads(config_path.read_text(encoding="utf-8"))
    assert persisted["resolution"] == [800, 480]
    assert persisted["name"] == "Legacy"
    assert persisted["display_type"] == "inky"


def test_get_config_reads_latest_store_snapshot_and_returns_detached_legacy_values(
    monkeypatch,
    tmp_path,
):
    config, _ = _device_config(
        monkeypatch,
        tmp_path,
        {"resolution": [800, 480], "nested": {"items": [1]}},
    )
    before = config._config_store.snapshot()
    config._config_store.commit(
        before.version,
        {"resolution": [800, 480], "nested": {"items": [1, 2]}},
    )

    first = config.get_config()
    first["nested"]["items"].append(99)

    assert config.get_config("nested") == {"items": [1, 2]}
    assert config.config["nested"] == {"items": [1, 2]}


def test_legacy_config_assignment_replaces_state_through_transactional_facade(
    monkeypatch,
    tmp_path,
):
    config, config_path = _device_config(
        monkeypatch,
        tmp_path,
        {"resolution": [800, 480], "remove_me": True},
    )
    config.playlist_manager = PlaylistManager()

    config.config = {"resolution": [1024, 600], "name": "Replacement"}

    assert config.get_config("resolution") == [1024, 600]
    assert config.get_config("name") == "Replacement"
    assert config.get_config("remove_me") is None
    persisted = json.loads(config_path.read_text(encoding="utf-8"))
    assert persisted["resolution"] == [1024, 600]
    assert "remove_me" not in persisted


def test_update_value_retries_cas_without_losing_a_concurrent_key(
    monkeypatch,
    tmp_path,
):
    config, _ = _device_config(
        monkeypatch,
        tmp_path,
        {"resolution": [800, 480], "startup": True},
    )
    real_commit = config._config_store.commit
    calls = 0

    def commit_after_concurrent_writer(expected_version, candidate):
        nonlocal calls
        calls += 1
        if calls == 1:
            concurrent = config.get_config()
            concurrent["concurrent_key"] = "preserved"
            current = config._config_store.snapshot()
            real_commit(current.version, concurrent)
        return real_commit(expected_version, candidate)

    monkeypatch.setattr(config._config_store, "commit", commit_after_concurrent_writer)

    config.update_value("startup", False)

    assert calls == 2
    assert config.get_config("startup") is False
    assert config.get_config("concurrent_key") == "preserved"


def test_update_value_reports_conflict_instead_of_overwriting_concurrent_model_state(
    monkeypatch,
    tmp_path,
):
    initial_playlist = {
        "playlists": [
            {
                "name": "Default",
                "start_time": "00:00",
                "end_time": "24:00",
                "plugins": [],
            }
        ],
        "active_playlist": "Default",
    }
    config, _ = _device_config(
        monkeypatch,
        tmp_path,
        {"resolution": [800, 480], "playlist_config": initial_playlist},
    )
    real_commit = config._config_store.commit
    calls = 0
    concurrent_playlist = {
        "playlists": [
            {
                "name": "Concurrent",
                "start_time": "00:00",
                "end_time": "24:00",
                "plugins": [],
            }
        ],
        "active_playlist": "Concurrent",
    }

    def commit_after_model_writer(expected_version, candidate):
        nonlocal calls
        calls += 1
        if calls == 1:
            concurrent = config.get_config()
            concurrent["playlist_config"] = concurrent_playlist
            current = config._config_store.snapshot()
            real_commit(current.version, concurrent)
        return real_commit(expected_version, candidate)

    monkeypatch.setattr(config._config_store, "commit", commit_after_model_writer)

    with pytest.raises(ConfigConflictError):
        config.update_value("startup", False)

    assert calls == 1
    assert config.get_config("playlist_config") == concurrent_playlist
    assert config.get_config("startup") is None


def test_update_value_migrates_legacy_playlist_instances_in_its_complete_candidate(
    monkeypatch,
    tmp_path,
):
    legacy = {
        "resolution": [800, 480],
        "startup": True,
        "playlist_config": {
            "playlists": [
                {
                    "name": "Default",
                    "start_time": "00:00",
                    "end_time": "24:00",
                    "plugins": [
                        {
                            "plugin_id": "clock",
                            "name": "Clock",
                            "plugin_settings": {},
                            "refresh": {"interval": 60},
                        }
                    ],
                }
            ],
            "active_playlist": "Default",
        },
    }
    config, _ = _device_config(monkeypatch, tmp_path, legacy)

    config.update_value("startup", False)

    instance = config.get_config("playlist_config")["playlists"][0]["plugins"][0]
    assert config.get_config("startup") is False
    assert instance["instance_uuid"]
    assert instance["structural_generation"] == 1
    assert instance["settings_revision"] == 1


def test_update_value_migrates_duplicate_legacy_identities_without_dropping_instances(
    monkeypatch,
    tmp_path,
):
    duplicate = {
        "plugin_id": "clock",
        "name": "Clock",
        "plugin_settings": {},
        "refresh": {"interval": 60},
    }
    legacy = {
        "resolution": [800, 480],
        "playlist_config": {
            "playlists": [
                {
                    "name": playlist_name,
                    "start_time": "00:00",
                    "end_time": "24:00",
                    "plugins": [dict(duplicate)],
                }
                for playlist_name in ("A", "B")
            ],
            "active_playlist": "A",
        },
    }
    config, _ = _device_config(monkeypatch, tmp_path, legacy)

    config.update_value("startup", False)

    playlists = config.get_config("playlist_config")["playlists"]
    instances = [playlist["plugins"][0] for playlist in playlists]
    assert len(instances) == 2
    assert {instance["plugin_id"] for instance in instances} == {"clock"}
    assert len({instance["name"] for instance in instances}) == 2
    assert len({instance["instance_uuid"] for instance in instances}) == 2
    assert config.get_playlist_manager().find_plugin("clock", instances[1]["name"])


def test_update_config_commits_complete_candidate_with_model_state(
    monkeypatch,
    tmp_path,
):
    config, _ = _device_config(
        monkeypatch,
        tmp_path,
        {"resolution": [800, 480], "timezone": "UTC"},
    )
    config.playlist_manager = PlaylistManager()
    config.refresh_info = RefreshInfo(plugin_id="clock")

    config.update_config({"timezone": "America/Los_Angeles"})

    assert config.get_config("timezone") == "America/Los_Angeles"
    assert config.get_config("playlist_config") == {
        "playlists": [],
        "active_playlist": None,
    }
    assert config.get_config("refresh_info")["plugin_id"] == "clock"


def test_write_config_captures_playlist_once_before_retrying_store_cas(
    monkeypatch,
    tmp_path,
):
    config, _ = _device_config(
        monkeypatch,
        tmp_path,
        {"resolution": [800, 480]},
    )

    class OneShotPlaylistManager:
        calls = 0

        def to_dict(self):
            self.calls += 1
            if self.calls > 1:
                raise AssertionError("playlist snapshot was captured more than once")
            return {"playlists": [], "active_playlist": None}

    manager = OneShotPlaylistManager()
    config.playlist_manager = manager
    real_commit = config._config_store.commit
    commit_calls = 0

    def commit_after_concurrent_writer(expected_version, candidate):
        nonlocal commit_calls
        commit_calls += 1
        assert manager.calls == 1
        if commit_calls == 1:
            concurrent = config.get_config()
            concurrent["concurrent_key"] = "preserved"
            current = config._config_store.snapshot()
            real_commit(current.version, concurrent)
        return real_commit(expected_version, candidate)

    monkeypatch.setattr(config._config_store, "commit", commit_after_concurrent_writer)

    config.write_config()

    assert manager.calls == 1
    assert commit_calls == 2
    assert config.get_config("concurrent_key") == "preserved"
    assert config.get_config("playlist_config") == {
        "playlists": [],
        "active_playlist": None,
    }


def test_repeated_config_conflicts_are_reported_after_a_bounded_retry(
    monkeypatch,
    tmp_path,
):
    config, _ = _device_config(
        monkeypatch,
        tmp_path,
        {"resolution": [800, 480]},
    )
    calls = 0

    def always_conflict(expected_version, candidate):
        nonlocal calls
        calls += 1
        raise ConfigConflictError(expected_version, expected_version + 1)

    monkeypatch.setattr(config._config_store, "commit", always_conflict)

    with pytest.raises(ConfigConflictError):
        config.update_value("startup", False)

    assert calls == config.CONFIG_COMMIT_ATTEMPTS


def test_injected_runtime_paths_bind_one_identity_without_changing_legacy_aliases(tmp_path, monkeypatch):
    monkeypatch.setenv("INKYPI_DEV_ROOT", str(tmp_path))
    paths = RuntimePaths.from_environment(dev_mode=True)
    legacy_aliases = (
        Config.config_file,
        Config.current_image_file,
        Config.plugin_image_dir,
    )

    config = Config(runtime_paths=paths)

    assert config.runtime_paths is paths
    assert Path(config.config_file) == paths.config_file
    assert Path(config.current_image_file) == paths.current_image_file
    assert Path(config.plugin_image_dir) == paths.plugin_image_dir
    assert (
        Config.config_file,
        Config.current_image_file,
        Config.plugin_image_dir,
    ) == legacy_aliases


def test_injected_config_loads_only_its_canonical_env_file(tmp_path, monkeypatch):
    dev_root = tmp_path / "checkout" / "src"
    canonical_env = tmp_path / "runtime" / "inkypi.env"
    canonical_env.parent.mkdir(parents=True)
    canonical_env.write_text("INKYPI_INJECTED_ENV_TEST=canonical\n", encoding="utf-8")
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    (cwd / ".env").write_text("INKYPI_INJECTED_ENV_TEST=wrong-cwd\n", encoding="utf-8")
    monkeypatch.chdir(cwd)
    monkeypatch.setenv("INKYPI_DEV_ROOT", str(dev_root))
    monkeypatch.setenv("INKYPI_ENV_FILE", str(canonical_env))
    monkeypatch.delenv("INKYPI_INJECTED_ENV_TEST", raising=False)
    paths = RuntimePaths.from_environment(dev_mode=True)
    config = Config(runtime_paths=paths)
    calls = []
    real_load_dotenv = config_module.load_dotenv

    def recording_load_dotenv(*args, **kwargs):
        calls.append((args, kwargs))
        return real_load_dotenv(*args, **kwargs)

    monkeypatch.setattr(config_module, "load_dotenv", recording_load_dotenv)

    assert config.load_env_key("INKYPI_INJECTED_ENV_TEST") == "canonical"
    assert calls == [((str(canonical_env),), {"override": True})]
