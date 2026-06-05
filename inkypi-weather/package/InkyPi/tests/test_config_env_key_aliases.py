import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from config import Config

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
