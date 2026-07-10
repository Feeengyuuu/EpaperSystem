import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from flask import Flask


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_DIR))

from blueprints.main import main_bp
from blueprints.apikeys import apikeys_bp
from blueprints.playlist import playlist_bp
from blueprints.settings import settings_bp
from runtime_paths import RuntimePaths


class StubConfig:
    def get_config(self):
        return {"name": "Test InkyPi"}

    def get_plugins(self):
        return []


def make_app(paths, *, commit=None):
    app = Flask(__name__, template_folder=str(SRC_DIR / "templates"))
    app.config["RUNTIME_PATHS"] = paths
    app.config["DEVICE_CONFIG"] = StubConfig()
    app.config["DISPLAY_MANAGER"] = SimpleNamespace(
        transaction=SimpleNamespace(current=lambda: commit)
    )
    app.register_blueprint(main_bp)
    app.register_blueprint(apikeys_bp)
    app.register_blueprint(playlist_bp)
    app.register_blueprint(settings_bp)
    return app


def test_current_image_uses_manifest_path_and_standard_conditional_get(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("INKYPI_DEV_ROOT", str(tmp_path / "src"))
    monkeypatch.setenv(
        "INKYPI_CURRENT_IMAGE_FILE",
        str(tmp_path / "runtime" / "current.png"),
    )
    paths = RuntimePaths.from_environment(dev_mode=True)
    paths.current_image_file.parent.mkdir(parents=True)
    paths.current_image_file.write_bytes(b"stale-compatibility-image")
    committed_path = tmp_path / "runtime" / "objects" / f'{"a" * 32}.png'
    committed_path.parent.mkdir(parents=True)
    committed_path.write_bytes(b"manifest-committed-image")
    commit = SimpleNamespace(
        commit_id="a" * 32,
        image_path=committed_path,
        committed_at=datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc).isoformat(),
    )
    app = make_app(paths, commit=commit)

    client = app.test_client()
    response = client.get("/api/current_image")

    assert response.status_code == 200
    assert response.data == b"manifest-committed-image"
    assert response.mimetype == "image/png"
    assert response.headers["ETag"] == f'"{commit.commit_id}"'
    assert response.headers["Cache-Control"] == "no-cache"

    conditional = client.get(
        "/api/current_image",
        headers={"If-None-Match": response.headers["ETag"]},
    )

    assert conditional.status_code == 304
    assert conditional.data == b""


def test_current_image_returns_404_without_committed_manifest(tmp_path, monkeypatch):
    monkeypatch.setenv("INKYPI_DEV_ROOT", str(tmp_path / "src"))
    paths = RuntimePaths.from_environment(dev_mode=True)
    paths.current_image_file.parent.mkdir(parents=True)
    paths.current_image_file.write_bytes(b"uncommitted-compatibility-image")

    response = make_app(paths).test_client().get("/api/current_image")

    assert response.status_code == 404
    assert response.get_json() == {"error": "Image not found"}


def test_main_template_uses_current_image_route_not_source_static_file(tmp_path, monkeypatch):
    monkeypatch.setenv("INKYPI_DEV_ROOT", str(tmp_path / "src"))
    paths = RuntimePaths.from_environment(dev_mode=True)
    app = make_app(paths)

    response = app.test_client().get("/")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert '<img src="/api/current_image" alt="Current Image">' in html
    assert "/static/images/current_image.png" not in html
