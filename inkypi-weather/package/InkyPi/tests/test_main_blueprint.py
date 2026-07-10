import sys
from pathlib import Path

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


def make_app(paths):
    app = Flask(__name__, template_folder=str(SRC_DIR / "templates"))
    app.config["RUNTIME_PATHS"] = paths
    app.config["DEVICE_CONFIG"] = StubConfig()
    app.register_blueprint(main_bp)
    app.register_blueprint(apikeys_bp)
    app.register_blueprint(playlist_bp)
    app.register_blueprint(settings_bp)
    return app


def test_current_image_route_serves_canonical_runtime_file(tmp_path, monkeypatch):
    monkeypatch.setenv("INKYPI_DEV_ROOT", str(tmp_path / "src"))
    monkeypatch.setenv(
        "INKYPI_CURRENT_IMAGE_FILE",
        str(tmp_path / "runtime" / "current.png"),
    )
    paths = RuntimePaths.from_environment(dev_mode=True)
    paths.current_image_file.parent.mkdir(parents=True)
    paths.current_image_file.write_bytes(b"canonical-current-image")
    app = make_app(paths)

    response = app.test_client().get("/api/current_image")

    assert response.status_code == 200
    assert response.data == b"canonical-current-image"
    assert response.mimetype == "image/png"


def test_main_template_uses_current_image_route_not_source_static_file(tmp_path, monkeypatch):
    monkeypatch.setenv("INKYPI_DEV_ROOT", str(tmp_path / "src"))
    paths = RuntimePaths.from_environment(dev_mode=True)
    app = make_app(paths)

    response = app.test_client().get("/")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert '<img src="/api/current_image" alt="Current Image">' in html
    assert "/static/images/current_image.png" not in html
