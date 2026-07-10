import sys
from pathlib import Path

from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import blueprints.apikeys as apikeys_module
from blueprints.apikeys import apikeys_bp, parse_env_file, write_env_file
from runtime_paths import RuntimePaths


def test_write_env_file_uses_atomic_file_and_escapes_values(tmp_path):
    env_path = tmp_path / ".env"

    assert write_env_file(
        str(env_path),
        [
            ("PLAIN", "abc123"),
            ("WITH_SPACE", "hello world"),
            ("WITH_QUOTE", 'hello "world"'),
            ("EMPTY", ""),
        ],
    )

    assert not list(tmp_path.glob(".env.*.tmp"))
    values = dict(parse_env_file(str(env_path)))
    assert values["PLAIN"] == "abc123"
    assert values["WITH_SPACE"] == "hello world"
    assert values["WITH_QUOTE"] == 'hello "world"'
    assert values["EMPTY"] == ""


def test_save_api_keys_targets_injected_env_file(tmp_path, monkeypatch):
    monkeypatch.setenv("INKYPI_DEV_ROOT", str(tmp_path / "src"))
    monkeypatch.setenv("INKYPI_ENV_FILE", str(tmp_path / "runtime" / "inkypi.env"))
    paths = RuntimePaths.from_environment(dev_mode=True)
    app = Flask(__name__)
    app.config["RUNTIME_PATHS"] = paths
    app.register_blueprint(apikeys_bp)
    writes = []

    monkeypatch.setattr(apikeys_module, "parse_env_file", lambda _path: [])

    def capture_write(path, entries):
        writes.append((Path(path), entries))
        return True

    monkeypatch.setattr(apikeys_module, "write_env_file", capture_write)

    response = app.test_client().post(
        "/api-keys/save",
        json={"entries": [{"key": "OPENAI_API_KEY", "value": "secret"}]},
    )

    assert response.status_code == 200
    assert writes == [(paths.env_file, [("OPENAI_API_KEY", "secret")])]
