import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import config as config_module
from config import Config
from model import PlaylistManager, RefreshInfo
from runtime_paths import RuntimePaths

TEST_CACHE_ROOT = Path(__file__).resolve().parents[4] / ".tmp" / "config_env_key_aliases_tests"

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


def test_write_config_persists_device_json(tmp_path):
    config = Config.__new__(Config)
    config.config_file = str(tmp_path / "device.json")
    config.config = {"resolution": [800, 480]}
    config.playlist_manager = PlaylistManager()
    config.refresh_info = RefreshInfo(
        refresh_type="Playlist",
        plugin_id="clock",
        refresh_time="2026-06-04T12:00:00+00:00",
        image_hash="abc",
    )

    config.write_config()

    saved = json.loads((tmp_path / "device.json").read_text(encoding="utf-8"))
    assert saved["resolution"] == [800, 480]
    assert saved["playlist_config"] == {"playlists": [], "active_playlist": None}
    assert saved["refresh_info"]["plugin_id"] == "clock"


def test_read_config_returns_empty_dict_for_invalid_json(tmp_path):
    config = Config.__new__(Config)
    config.config_file = str(tmp_path / "device.json")
    (tmp_path / "device.json").write_text("{bad json", encoding="utf-8")

    assert config.read_config() == {}


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
