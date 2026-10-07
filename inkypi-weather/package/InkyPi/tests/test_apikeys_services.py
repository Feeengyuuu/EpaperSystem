import sys
from pathlib import Path
from types import SimpleNamespace

from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import blueprints.apikeys as apikeys_module
from blueprints.apikeys import apikeys_bp, build_key_services, key_service_status
from runtime_paths import RuntimePaths


REGISTRY = [
    {
        "key": "OPENAI_API_KEY",
        "service": "OpenAI",
        "features": ["AI Image", "AI Text"],
        "aliases": ["OPEN_AI_SECRET"],
        "signup_url": "https://example.test/openai",
        "notes": "",
        "value_type": "secret",
    },
    {
        "key": "NASA_SECRET",
        "service": "NASA",
        "features": ["NASA Picture"],
        "aliases": [],
        "signup_url": "",
        "notes": "",
        "value_type": "secret",
    },
    {
        "key": "GOOGLE_CREDENTIALS_FILE",
        "service": "Google",
        "features": ["Calendar"],
        "aliases": [],
        "signup_url": "",
        "notes": "Path to a service account file.",
        "value_type": "path",
    },
]


def _keys(group):
    return [service["key"] for service in group]


def test_build_key_services_groups_by_state_and_use():
    services = build_key_services(
        [("OPEN_AI_SECRET", "sk-live"), ("NASA_SECRET", ""), ("CUSTOM_TOKEN", "x")],
        REGISTRY,
        used_features={"NASA Picture"},
    )

    assert _keys(services["configured"]) == ["OPENAI_API_KEY"]
    assert _keys(services["needed"]) == ["NASA_SECRET"]
    assert _keys(services["available"]) == ["GOOGLE_CREDENTIALS_FILE"]
    assert services["other"] == [{"key": "CUSTOM_TOKEN", "configured": True}]
    assert services["env_keys"] == ["OPEN_AI_SECRET", "NASA_SECRET", "CUSTOM_TOKEN"]


def test_build_key_services_reports_the_variable_in_use():
    services = build_key_services([("OPEN_AI_SECRET", "sk-live")], REGISTRY)
    openai = services["configured"][0]

    assert openai["env_key"] == "OPEN_AI_SECRET"
    assert openai["configured"] is True
    assert openai["in_use"] is False


def test_build_key_services_keeps_listed_empty_alias_as_target():
    services = build_key_services([("OPEN_AI_SECRET", "")], REGISTRY)
    openai = next(item for item in services["available"] if item["key"] == "OPENAI_API_KEY")

    assert openai["configured"] is False
    assert openai["env_key"] == "OPEN_AI_SECRET"


def test_build_key_services_never_returns_values():
    services = build_key_services(
        [("OPENAI_API_KEY", "sk-very-secret"), ("CUSTOM_TOKEN", "hidden-value")],
        REGISTRY,
    )

    assert "sk-very-secret" not in repr(services)
    assert "hidden-value" not in repr(services)


def test_key_service_status_matches_alias_and_unknown_names():
    entries = [("OPENAI_API_KEY", "sk")]

    assert key_service_status("OPEN_AI_SECRET", entries, REGISTRY)["configured"] is True
    assert key_service_status("NASA_SECRET", entries, REGISTRY)["configured"] is False
    unknown = key_service_status("SOMETHING_ELSE", [("SOMETHING_ELSE", "v")], REGISTRY)
    assert unknown["configured"] is True
    assert unknown["env_key"] == "SOMETHING_ELSE"


def test_api_keys_page_renders_groups_without_values(tmp_path, monkeypatch):
    monkeypatch.setenv("INKYPI_DEV_ROOT", str(tmp_path / "src"))
    env_file = tmp_path / "runtime" / "inkypi.env"
    env_file.parent.mkdir(parents=True)
    env_file.write_text("OPENAI_API_KEY=sk-never-render\nCUSTOM_TOKEN=also-hidden\n", encoding="utf-8")
    monkeypatch.setenv("INKYPI_ENV_FILE", str(env_file))
    monkeypatch.setattr(apikeys_module, "get_api_key_registry", lambda: REGISTRY)
    paths = RuntimePaths.from_environment(dev_mode=True)

    playlists = {
        "playlists": [{"name": "Default", "plugins": [{"plugin_id": "apod", "name": "Sky"}]}],
        "active_playlist": "Default",
    }
    device_config = SimpleNamespace(
        get_plugins=lambda: [{"id": "apod", "display_name": "NASA Picture"}],
        get_playlist_manager=lambda: SimpleNamespace(to_dict=lambda: playlists),
        get_config=lambda key=None, default=None: "Test" if key == "name" else {},
    )
    source_root = Path(apikeys_module.__file__).resolve().parents[1]
    app = Flask(__name__, template_folder=str(source_root / "templates"))
    app.config["RUNTIME_PATHS"] = paths
    app.config["DEVICE_CONFIG"] = device_config
    app.register_blueprint(apikeys_bp)

    html = app.test_client().get("/api-keys").get_data(as_text=True)

    assert "sk-never-render" not in html
    assert "also-hidden" not in html
    assert 'id="key-NASA_SECRET"' in html
    assert html.index("Used by your playlists") < html.index('id="key-NASA_SECRET"')
    assert 'data-env-key="CUSTOM_TOKEN"' in html
    assert '"OPENAI_API_KEY", "CUSTOM_TOKEN"' in html
    assert 'class="nav-link is-active" href="/api-keys" aria-current="page"' in html
