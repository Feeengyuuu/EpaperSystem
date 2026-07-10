from pathlib import Path
import sys

from flask import Flask, jsonify, request, session

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from security.credentials import CredentialStore
from security.rate_limit import BoundedRateLimiter
from security.request_guard import install_request_guards


class DeviceConfig:
    def get_config(self, key=None, default=None):
        values = {
            "name": "living-room-frame",
            "allowed_hosts": ["frame.internal", "192.0.2.10"],
        }
        return values if key is None else values.get(key, default)


def _secured_app(tmp_path):
    app = Flask(__name__)
    app.secret_key = "test-secret"
    store = CredentialStore(tmp_path)
    bootstrap = store.create_bootstrap_token()
    store.consume_bootstrap_token(bootstrap, "strong-password")
    app.config["CREDENTIAL_STORE"] = store

    @app.get("/read")
    def read():
        return jsonify({"ok": True})

    @app.post("/mutate")
    def mutate():
        return jsonify({"ok": True})

    install_request_guards(app, store, DeviceConfig())
    return app


def _authenticate(client, token="c" * 43, *, base_url=None):
    kwargs = {"base_url": base_url} if base_url else {}
    with client.session_transaction(**kwargs) as session:
        session["admin_identity"] = "admin"
        session["csrf_token"] = token
        session.permanent = True
    return token


def test_get_is_public_but_anonymous_mutation_is_rejected(tmp_path):
    client = _secured_app(tmp_path).test_client()

    assert client.get("/read").status_code == 200
    response = client.post("/mutate", json={})

    assert response.status_code == 401
    assert response.get_json()["error_code"] == "authentication_required"


def test_authenticated_mutation_requires_constant_time_csrf(tmp_path):
    client = _secured_app(tmp_path).test_client()
    token = _authenticate(client)

    missing = client.post("/mutate", json={})
    wrong = client.post(
        "/mutate",
        json={},
        headers={"X-CSRF-Token": "wrong"},
    )
    valid = client.post(
        "/mutate",
        json={},
        headers={"X-CSRF-Token": token},
    )

    assert missing.status_code == 403
    assert wrong.status_code == 403
    assert missing.get_json()["error_code"] == "csrf_failed"
    assert valid.status_code == 200


def test_host_and_same_origin_are_validated_before_mutation(tmp_path):
    client = _secured_app(tmp_path).test_client()
    token = _authenticate(client)

    evil_host = client.post(
        "/mutate",
        json={},
        headers={"X-CSRF-Token": token, "Host": "evil.test"},
    )
    evil_origin = client.post(
        "/mutate",
        json={},
        headers={
            "X-CSRF-Token": token,
            "Origin": "http://evil.test",
        },
    )
    valid_origin = client.post(
        "/mutate",
        json={},
        headers={
            "X-CSRF-Token": token,
            "Origin": "http://localhost",
        },
    )

    assert evil_host.status_code in {400, 403}
    assert evil_host.get_json()["error_code"] == "host_not_allowed"
    assert evil_origin.status_code == 403
    assert evil_origin.get_json()["error_code"] == "csrf_failed"
    assert valid_origin.status_code == 200


def test_configured_device_names_and_ips_are_allowed_hosts(tmp_path):
    app = _secured_app(tmp_path)

    for host in (
        "living-room-frame",
        "living-room-frame.local",
        "frame.internal",
        "192.0.2.10",
    ):
        client = app.test_client()
        base_url = f"http://{host}"
        token = _authenticate(client, base_url=base_url)
        response = client.post(
            "/mutate",
            json={},
            headers={"Host": host, "X-CSRF-Token": token},
            base_url=base_url,
        )
        assert response.status_code == 200, host


def test_form_csrf_token_is_accepted(tmp_path):
    client = _secured_app(tmp_path).test_client()
    token = _authenticate(client)

    response = client.post("/mutate", data={"_csrf_token": token})

    assert response.status_code == 200


def test_supported_tls_proxy_header_participates_in_same_origin_check(tmp_path):
    client = _secured_app(tmp_path).test_client()
    token = _authenticate(client)

    response = client.post(
        "/mutate",
        json={},
        headers={
            "X-CSRF-Token": token,
            "X-Forwarded-Proto": "https",
            "Origin": "https://localhost",
        },
    )

    assert response.status_code == 200


def test_health_detail_authorizer_uses_the_real_admin_session(tmp_path):
    app = _secured_app(tmp_path)
    authorizer = app.config["HEALTH_DETAIL_AUTHORIZER"]

    with app.test_request_context("/healthz"):
        assert authorizer(request) is False
        session["admin_identity"] = "admin"
        assert authorizer(request) is True


def test_application_guard_returns_stable_rate_limit_code(tmp_path):
    client = _secured_app(tmp_path).test_client()
    token = _authenticate(client)

    for _index in range(120):
        assert client.post(
            "/mutate",
            json={},
            headers={"X-CSRF-Token": token},
        ).status_code == 200
    rejected = client.post(
        "/mutate",
        json={},
        headers={"X-CSRF-Token": token},
    )

    assert rejected.status_code == 429
    assert rejected.get_json()["error_code"] == "rate_limited"
    assert int(rejected.headers["Retry-After"]) >= 1


def test_rate_limiter_is_bounded_and_returns_retry_delay():
    clock = [10.0]
    limiter = BoundedRateLimiter(
        default_limit=2,
        default_window_seconds=10,
        max_keys=3,
        clock=lambda: clock[0],
    )

    assert limiter.check("client", "save").allowed
    assert limiter.check("client", "save").allowed
    rejected = limiter.check("client", "save")
    assert not rejected.allowed
    assert 0 < rejected.retry_after_seconds <= 10

    for index in range(10):
        limiter.check(f"client-{index}", "save")
    assert limiter.size <= 3

    clock[0] += 11
    assert limiter.check("client", "save").allowed
