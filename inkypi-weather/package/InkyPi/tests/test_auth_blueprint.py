from pathlib import Path
import sys

from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from blueprints.auth import auth_bp
from security.credentials import CredentialStore
from security.request_guard import install_request_guards


class DeviceConfig:
    def get_config(self, key=None, default=None):
        values = {"name": "inkypi"}
        return values if key is None else values.get(key, default)


def _app(tmp_path):
    app = Flask(
        __name__,
        template_folder=str(Path(__file__).resolve().parents[1] / "src" / "templates"),
        static_folder=str(Path(__file__).resolve().parents[1] / "src" / "static"),
    )
    app.secret_key = "stable-test-secret"
    app.config.update(
        TESTING=True,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
    )
    store = CredentialStore(tmp_path)
    app.config["CREDENTIAL_STORE"] = store
    app.register_blueprint(auth_bp)
    install_request_guards(app, store, DeviceConfig())
    return app, store


def _csrf(client):
    with client.session_transaction() as session:
        return session["csrf_token"]


def test_setup_consumes_bootstrap_and_starts_bounded_admin_session(tmp_path):
    app, store = _app(tmp_path)
    token = store.create_bootstrap_token()
    client = app.test_client()

    page = client.get("/auth/setup")
    csrf = _csrf(client)
    response = client.post(
        "/auth/setup",
        json={"bootstrap_token": token, "password": "strong-password"},
        headers={"X-CSRF-Token": csrf, "Accept": "application/json"},
    )

    assert page.status_code == 200
    assert token not in page.get_data(as_text=True)
    assert response.status_code == 201
    assert response.get_json()["authenticated"] is True
    assert store.verify_admin_password("strong-password")
    with client.session_transaction() as session:
        assert session["admin_identity"] == "admin"
        assert session["csrf_token"] != csrf
        assert session.permanent


def test_login_rejects_wrong_password_and_rotates_session(tmp_path):
    app, store = _app(tmp_path)
    token = store.create_bootstrap_token()
    store.consume_bootstrap_token(token, "strong-password")
    client = app.test_client()
    client.get("/auth/login")
    csrf = _csrf(client)

    wrong = client.post(
        "/auth/login",
        json={"password": "wrong-password"},
        headers={"X-CSRF-Token": csrf, "Accept": "application/json"},
    )
    valid = client.post(
        "/auth/login",
        json={"password": "strong-password"},
        headers={"X-CSRF-Token": csrf, "Accept": "application/json"},
    )

    assert wrong.status_code == 401
    assert wrong.get_json()["error_code"] == "invalid_credentials"
    assert valid.status_code == 200
    with client.session_transaction() as session:
        assert session["admin_identity"] == "admin"
        assert session["csrf_token"] != csrf


def test_logout_and_password_rotation_require_authenticated_csrf(tmp_path):
    app, store = _app(tmp_path)
    token = store.create_bootstrap_token()
    store.consume_bootstrap_token(token, "first-strong-password")
    client = app.test_client()
    client.get("/auth/login")
    login_csrf = _csrf(client)
    client.post(
        "/auth/login",
        json={"password": "first-strong-password"},
        headers={"X-CSRF-Token": login_csrf},
    )
    csrf = _csrf(client)

    rotated = client.post(
        "/auth/password",
        json={
            "current_password": "first-strong-password",
            "new_password": "second-strong-password",
        },
        headers={"X-CSRF-Token": csrf},
    )
    csrf = _csrf(client)
    logged_out = client.post(
        "/auth/logout",
        headers={"X-CSRF-Token": csrf, "Accept": "application/json"},
    )

    assert rotated.status_code == 200
    assert store.verify_admin_password("second-strong-password")
    assert logged_out.status_code == 200
    with client.session_transaction() as session:
        assert "admin_identity" not in session


def test_plain_http_warning_and_tls_forwarded_cookie_security(tmp_path):
    app, store = _app(tmp_path)
    token = store.create_bootstrap_token()
    store.consume_bootstrap_token(token, "strong-password")
    client = app.test_client()

    plain = client.get("/auth/login")
    assert "Connection is not encrypted" in plain.get_data(as_text=True)
    csrf = _csrf(client)
    secure = client.post(
        "/auth/login",
        json={"password": "strong-password"},
        headers={
            "X-CSRF-Token": csrf,
            "X-Forwarded-Proto": "https",
        },
    )

    cookies = secure.headers.getlist("Set-Cookie")
    assert any("Secure" in value for value in cookies)
    assert all("HttpOnly" in value for value in cookies)
    assert all("SameSite=Lax" in value for value in cookies)


def test_setup_is_unavailable_after_admin_exists(tmp_path):
    app, store = _app(tmp_path)
    token = store.create_bootstrap_token()
    store.consume_bootstrap_token(token, "strong-password")

    response = app.test_client().get("/auth/setup")

    assert response.status_code in {302, 303}
    assert response.headers["Location"].endswith("/auth/login")


def test_corrupt_credential_store_keeps_authentication_fail_closed(tmp_path):
    app, store = _app(tmp_path)
    store.credentials_path.write_text("{broken", encoding="utf-8")

    response = app.test_client().get(
        "/auth/login",
        headers={"Accept": "application/json"},
    )

    assert response.status_code == 503
    assert response.get_json()["error_code"] == "credential_store_failed"


def test_root_recovery_token_can_reset_password_once(tmp_path):
    app, store = _app(tmp_path)
    bootstrap = store.create_bootstrap_token()
    store.consume_bootstrap_token(bootstrap, "first-strong-password")
    recovery = store.create_recovery_token()
    client = app.test_client()

    page = client.get("/auth/recover")
    csrf = _csrf(client)
    response = client.post(
        "/auth/recover",
        json={
            "recovery_token": recovery,
            "password": "recovered-strong-password",
        },
        headers={"X-CSRF-Token": csrf, "Accept": "application/json"},
    )

    assert page.status_code == 200
    assert recovery not in page.get_data(as_text=True)
    assert response.status_code == 200
    assert response.get_json()["authenticated"] is True
    assert store.verify_admin_password("recovered-strong-password")
    assert not store.recovery_plaintext_path.exists()
    with client.session_transaction() as recovered_session:
        assert recovered_session["admin_identity"] == "admin"

    client.get("/auth/recover")
    reused = client.post(
        "/auth/recover",
        json={
            "recovery_token": recovery,
            "password": "third-strong-password",
        },
        headers={"X-CSRF-Token": _csrf(client), "Accept": "application/json"},
    )
    assert reused.status_code == 401
    assert reused.get_json()["error_code"] == "invalid_recovery_token"
