import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from config import Config
from model import PlaylistManager, RefreshInfo

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


def test_load_env_key_accepts_pixiv_refresh_token_aliases(monkeypatch):
    env_path = cache_dir_for("pixiv") / ".env"
    env_path.write_text("PIXIV_TOKEN=pixiv-refresh-value\n", encoding="utf-8")
    monkeypatch.delenv("PIXIV_REFRESH_TOKEN", raising=False)
    monkeypatch.delenv("PIXIV_TOKEN", raising=False)
    monkeypatch.delenv("PIXIV_REFRESH", raising=False)

    config = Config.__new__(Config)
    monkeypatch.setattr(config, "_env_file_candidates", lambda: [str(env_path)])

    assert config.load_env_key("PIXIV_REFRESH_TOKEN") == "pixiv-refresh-value"


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
