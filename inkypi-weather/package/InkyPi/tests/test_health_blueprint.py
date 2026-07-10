import time

from flask import Flask

from src.blueprints.health import health_bp
from src.health import HealthPublisher, ReadinessEvaluator


def _app_with_health():
    app = Flask(__name__)
    publisher = HealthPublisher(
        release_id="release-123",
        boot_id="boot-123",
        started_monotonic=time.monotonic() - 300,
    )
    publisher.publish_components(
        {
            "runtime": {"dev_mode": False},
            "lifecycle": {"state": "running"},
            "config": {
                "valid": True,
                "writable": True,
                "source": "primary",
                "version": 3,
            },
            "display": {"state": "committed", "commit_id": "a" * 32},
            "queue": {
                "depth": 0,
                "capacity": 32,
                "accepting": True,
                "full_since_monotonic": None,
            },
            "scheduler": {
                "heartbeat_monotonic": time.monotonic(),
                "tick_seconds": 30,
                "active_deadline_monotonic": None,
            },
            "startup": {"degraded": False, "reason_codes": ()},
            "disk": {
                "free_bytes": 1024 * 1024 * 1024,
                "soft_min_bytes": 256 * 1024 * 1024,
                "hard_min_bytes": 64 * 1024 * 1024,
            },
        }
    )
    app.config["HEALTH_PUBLISHER"] = publisher
    app.config["READINESS_EVALUATOR"] = ReadinessEvaluator()
    app.register_blueprint(health_bp)
    return app, publisher


def test_public_health_and_ready_bodies_are_minimal():
    app, _publisher = _app_with_health()
    client = app.test_client()

    health = client.get("/healthz")
    ready = client.get("/readyz")

    assert health.status_code == 200
    assert ready.status_code == 200
    assert set(health.get_json()) == {"status", "release_id", "boot_id", "uptime_seconds"}
    assert set(ready.get_json()) == {"status", "release_id", "boot_id", "uptime_seconds"}
    assert health.get_json()["status"] == "alive"
    assert ready.get_json()["status"] == "ready"
    assert health.headers["Cache-Control"] == "no-store"


def test_readyz_does_not_acquire_publisher_or_core_component_locks():
    app, publisher = _app_with_health()

    class ExplodingLock:
        def __enter__(self):
            raise AssertionError("endpoint acquired a mutable component lock")

        def __exit__(self, *_args):
            return False

    publisher._lock = ExplodingLock()
    started = time.monotonic()
    response = app.test_client().get("/readyz")
    elapsed = time.monotonic() - started

    assert elapsed < 0.2
    assert response.status_code == 200


def test_readyz_returns_503_for_unknown_display_but_healthz_stays_live():
    app, publisher = _app_with_health()
    publisher.publish_component(
        "display",
        {"state": "display_unknown", "commit_id": "pending"},
    )
    client = app.test_client()

    assert client.get("/healthz").status_code == 200
    response = client.get("/readyz")
    assert response.status_code == 503
    assert response.get_json()["status"] == "not_ready"


def test_authenticated_detail_hook_can_add_sanitized_components():
    app, _publisher = _app_with_health()
    app.config["HEALTH_DETAIL_AUTHORIZER"] = lambda request: (
        request.headers.get("X-Test-Admin") == "yes"
    )

    public = app.test_client().get("/readyz").get_json()
    detailed = app.test_client().get(
        "/readyz",
        headers={"X-Test-Admin": "yes"},
    ).get_json()

    assert "components" not in public
    assert "components" in detailed
    assert detailed["components"]["config"]["version"] == 3
    assert detailed["error_codes"] == []
