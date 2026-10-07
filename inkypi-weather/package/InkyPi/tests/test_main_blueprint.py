import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from flask import Flask


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_DIR))

from blueprints.main import main_bp
from blueprints.apikeys import apikeys_bp
from blueprints.auth import auth_bp
from blueprints.playlist import playlist_bp
from blueprints.plugin import plugin_bp
from blueprints.settings import settings_bp
from runtime_paths import RuntimePaths


class StubPlaylistManager:
    def __init__(self, playlists=None, active=None):
        self.playlists = playlists or []
        self.active = active

    def to_dict(self):
        return {"playlists": self.playlists, "active_playlist": self.active}


class StubConfig:
    def __init__(self, *, plugins=None, playlists=None, active=None, refresh_info=None):
        self.plugins = plugins or []
        self.playlist_manager = StubPlaylistManager(playlists, active)
        self.refresh_info = refresh_info or {}

    def get_config(self, key=None, default=None):
        config = {"name": "Test InkyPi"}
        return config if key is None else config.get(key, default)

    def get_plugins(self):
        return self.plugins

    def get_playlist_manager(self):
        return self.playlist_manager

    def get_refresh_info(self):
        return SimpleNamespace(to_dict=lambda: dict(self.refresh_info))


def make_app(paths, *, commit=None, device_config=None):
    app = Flask(__name__, template_folder=str(SRC_DIR / "templates"))
    app.config["RUNTIME_PATHS"] = paths
    app.config["DEVICE_CONFIG"] = device_config or StubConfig()
    app.config["DISPLAY_MANAGER"] = SimpleNamespace(
        transaction=SimpleNamespace(current=lambda: commit)
    )
    app.register_blueprint(main_bp)
    app.register_blueprint(apikeys_bp)
    app.register_blueprint(playlist_bp)
    app.register_blueprint(plugin_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(auth_bp)
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


PLUGINS = [
    {"id": "clock", "display_name": "Clock"},
    {
        "id": "weather",
        "display_name": "Weather",
        "capabilities": {"refresh_data_before_display": True},
    },
]
PLAYLISTS = [
    {
        "name": "Default",
        "start_time": "00:00",
        "end_time": "24:00",
        "plugins": [
            {
                "plugin_id": "clock",
                "name": "Kitchen Clock",
                "plugin_settings": {"secret_token": "never-render-me"},
                "refresh": {"interval": 21600},
                "latest_refresh_time": "2026-10-06T12:00:00+00:00",
                "instance_uuid": "clock-uuid",
            },
            {
                "plugin_id": "weather",
                "name": "Home Weather",
                "plugin_settings": {"latitude": "12.3456"},
                "refresh": {"interval": 3600},
                "latest_refresh_time": None,
                "instance_uuid": "weather-uuid",
            },
        ],
    },
    {"name": "Night", "start_time": "22:00", "end_time": "06:00", "plugins": []},
]


def _rich_config():
    return StubConfig(
        plugins=PLUGINS,
        playlists=PLAYLISTS,
        active="Default",
        refresh_info={
            "refresh_time": "2026-10-06T12:05:00+00:00",
            "image_hash": "abc123",
            "refresh_type": "Playlist",
            "plugin_id": "clock",
            "playlist": "Default",
            "plugin_instance": "Kitchen Clock",
        },
    )


def _rich_app(tmp_path, monkeypatch):
    monkeypatch.setenv("INKYPI_DEV_ROOT", str(tmp_path / "src"))
    paths = RuntimePaths.from_environment(dev_mode=True)
    return make_app(paths, device_config=_rich_config())


def test_home_renders_large_preview_cards_without_plugin_settings(tmp_path, monkeypatch):
    html = _rich_app(tmp_path, monkeypatch).test_client().get("/").get_data(as_text=True)

    assert html.count("data-instance-card") == 2
    assert "/plugin_instance_image/Default/clock/Kitchen%20Clock" in html
    assert "Every 6 hours" in html
    assert "Updates before each display" in html
    assert 'class="preview-card is-current"' in html
    assert "never-render-me" not in html
    assert "12.3456" not in html
    assert 'data-playlist-tab="Night"' in html
    assert "<title>Now Playing \u00b7 Test InkyPi</title>" in html


def test_playlist_route_renders_the_same_page_and_selects_requested_tab(tmp_path, monkeypatch):
    html = (
        _rich_app(tmp_path, monkeypatch)
        .test_client()
        .get("/playlist?playlist=Night")
        .get_data(as_text=True)
    )

    assert '<img src="/api/current_image" alt="Current Image">' in html
    night_tab = html.split('data-playlist-tab="Night"')[0].rsplit("<button", 1)[1]
    assert 'aria-selected="true"' in night_tab
    default_tab = html.split('data-playlist-tab="Default"')[0].rsplit("<button", 1)[1]
    assert 'aria-selected="false"' in default_tab


def test_now_playing_api_reports_display_without_hash(tmp_path, monkeypatch):
    response = _rich_app(tmp_path, monkeypatch).test_client().get("/api/now-playing")

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.get_json() == {
        "plugin_id": "clock",
        "display_name": "Clock",
        "playlist": "Default",
        "plugin_instance": "Kitchen Clock",
        "refresh_time": "2026-10-06T12:05:00+00:00",
        "refresh_type": "Playlist",
    }


def test_plugins_page_lists_plugins_in_the_shared_shell(tmp_path, monkeypatch):
    html = _rich_app(tmp_path, monkeypatch).test_client().get("/plugins").get_data(as_text=True)

    assert 'data-plugin-id="clock"' in html
    assert 'data-plugin-id="weather"' in html
    assert 'class="nav-link is-active" href="/plugins" aria-current="page"' in html
    assert 'name="inkypi-csrf-token"' in html
    assert "inkypi-security.js" in html
