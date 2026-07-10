from pathlib import Path
import sys

from flask import Blueprint, Flask, jsonify

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from blueprints.apikeys import apikeys_bp
from blueprints.auth import auth_bp
from blueprints.main import main_bp
from blueprints.playlist import playlist_bp
from blueprints.plugin import plugin_bp
from blueprints.settings import settings_bp
from security.credentials import CredentialStore
from security.request_guard import PUBLIC_MUTATION_ENDPOINTS, install_request_guards


SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
TEMPLATE_ROOT = Path(__file__).resolve().parents[1] / "src" / "templates"


class DeviceConfig:
    def get_config(self, key=None, default=None):
        values = {"name": "inkypi"}
        return values if key is None else values.get(key, default)


def _application(tmp_path):
    app = Flask(__name__)
    app.secret_key = "security-enumeration-secret"
    store = CredentialStore(tmp_path)
    bootstrap = store.create_bootstrap_token()
    store.consume_bootstrap_token(bootstrap, "strong-password")
    app.config["CREDENTIAL_STORE"] = store
    for blueprint in (
        main_bp,
        settings_bp,
        plugin_bp,
        playlist_bp,
        apikeys_bp,
        auth_bp,
    ):
        app.register_blueprint(blueprint)

    dynamic = Blueprint("dynamic_security_test", __name__)

    @dynamic.post("/dynamic-plugin/action")
    def action():
        return jsonify({"ok": True})

    app.register_blueprint(dynamic)
    install_request_guards(app, store, DeviceConfig())
    return app


def _mutating_rules(app):
    for rule in app.url_map.iter_rules():
        methods = sorted(set(rule.methods or ()) - SAFE_METHODS)
        if not methods or rule.endpoint in PUBLIC_MUTATION_ENDPOINTS:
            continue
        yield rule, methods


def _rule_path(app, rule):
    values = {}
    for argument in rule.arguments:
        converter = rule._converters[argument]
        values[argument] = (
            1 if converter.__class__.__name__ == "IntegerConverter" else "sample"
        )
    return app.url_map.bind("localhost").build(rule.endpoint, values)


def test_every_base_and_dynamic_mutating_route_rejects_anonymous(tmp_path):
    app = _application(tmp_path)
    client = app.test_client()
    checked = []

    for rule, methods in _mutating_rules(app):
        path = _rule_path(app, rule)
        for method in methods:
            response = client.open(path, method=method, json={})
            checked.append((rule.endpoint, method))
            assert response.status_code == 401, (rule.rule, method)
            assert response.get_json()["error_code"] == "authentication_required"

    assert ("settings.shutdown", "POST") in checked
    assert ("apikeys.save_apikeys", "POST") in checked
    assert ("dynamic_security_test.action", "POST") in checked


def test_every_authenticated_mutating_route_rejects_missing_csrf(tmp_path):
    app = _application(tmp_path)
    client = app.test_client()
    with client.session_transaction() as session:
        session["admin_identity"] = "admin"
        session["csrf_token"] = "csrf-token"
        session.permanent = True

    for rule, methods in _mutating_rules(app):
        path = _rule_path(app, rule)
        for method in methods:
            response = client.open(path, method=method, json={})
            assert response.status_code == 403, (rule.rule, method)
            assert response.get_json()["error_code"] == "csrf_failed"


def test_high_risk_shutdown_rejects_disallowed_host_even_with_auth_and_csrf(tmp_path):
    app = _application(tmp_path)
    client = app.test_client()
    with client.session_transaction() as session:
        session["admin_identity"] = "admin"
        session["csrf_token"] = "csrf-token"

    response = client.post(
        "/shutdown",
        json={},
        headers={"X-CSRF-Token": "csrf-token", "Host": "evil.test"},
    )

    assert response.status_code in {400, 403}
    assert response.get_json()["error_code"] == "host_not_allowed"


def test_all_administration_pages_load_the_shared_csrf_fetch_wrapper():
    for name in ("inky.html", "playlist.html", "plugin.html", "settings.html", "apikeys.html"):
        source = (TEMPLATE_ROOT / name).read_text(encoding="utf-8")
        assert 'name="inkypi-csrf-token"' in source, name
        assert "inkypi-security.js" in source, name

    script = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "static"
        / "inkypi-security.js"
    ).read_text(encoding="utf-8")
    assert "window.fetch" in script
    assert "X-CSRF-Token" in script
    assert "target.origin === window.location.origin" in script
