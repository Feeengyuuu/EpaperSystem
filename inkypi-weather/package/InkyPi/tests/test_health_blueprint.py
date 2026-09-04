import time

from flask import Flask
import pytest

from blueprints.health import health_bp
from health import HealthPublisher, ReadinessEvaluator


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


@pytest.mark.parametrize(
    ("data_stalled_count", "presentation_stalled_count", "expected_codes"),
    [
        (2, 0, ["data_progress_stalled"]),
        (0, 1, ["presentation_progress_stalled"]),
        (2, 1, ["data_progress_stalled", "presentation_progress_stalled"]),
    ],
)
def test_readyz_exposes_sustained_progress_stalls_without_requesting_restart(
    data_stalled_count,
    presentation_stalled_count,
    expected_codes,
):
    app, publisher = _app_with_health()
    publisher.publish_component(
        "scheduler",
        {
            "heartbeat_monotonic": time.monotonic(),
            "tick_seconds": 30,
            "active_deadline_monotonic": None,
            "progress": {
                "enabled": True,
                "observed": True,
                "active_instances": 27,
                "data_stalled_count": data_stalled_count,
                "presentation_stalled_count": presentation_stalled_count,
                "oldest_data_overdue_seconds": 86400.0,
                "oldest_presentation_pending_seconds": 172800.0,
            },
        },
    )
    public = app.test_client().get("/readyz")
    app.config["HEALTH_DETAIL_AUTHORIZER"] = lambda _request: True
    client = app.test_client()

    ready = client.get("/readyz")
    health = client.get("/healthz")

    assert ready.status_code == 200
    assert public.status_code == 200
    assert public.get_json()["status"] == "degraded"
    assert set(public.get_json()) == {
        "status", "release_id", "boot_id", "uptime_seconds",
    }
    assert ready.get_json()["status"] == "degraded"
    assert ready.get_json()["error_codes"] == expected_codes
    assert health.status_code == 200
    assert health.get_json()["status"] == "alive"
    assert health.get_json()["readiness_status"] == "degraded"


@pytest.mark.parametrize(
    "progress",
    [
        None,
        {},
        [],
        {"enabled": False, "observed": True, "data_stalled_count": 2},
        {"enabled": True, "observed": False, "data_stalled_count": 2},
        {"enabled": "true", "observed": True, "data_stalled_count": 2},
        {"enabled": True, "observed": 1, "data_stalled_count": 2},
        {"enabled": True, "observed": True, "data_stalled_count": "private-text"},
        {"enabled": True, "observed": True, "data_stalled_count": True},
        {"enabled": True, "observed": True, "data_stalled_count": float("nan")},
        {
            "enabled": True,
            "observed": True,
            "active_instances": 27,
            "data_stalled_count": 0,
            "presentation_stalled_count": 0,
            "oldest_data_overdue_seconds": 86400.0,
            "oldest_presentation_pending_seconds": 16200.0,
            "presentation_pending_count": 4,
            "obsolete_presentation_count": 3,
        },
    ],
    ids=[
        "missing", "empty", "malformed", "disabled", "not-observed",
        "invalid-enabled", "invalid-observed", "invalid-count-text",
        "invalid-count-bool", "invalid-count-nan", "daily-and-next-rotation-wait",
    ],
)
def test_readyz_does_not_infer_stalls_from_age_or_unconfirmed_progress(progress):
    app, publisher = _app_with_health()
    publisher.publish_component(
        "scheduler",
        {
            "heartbeat_monotonic": time.monotonic(),
            "tick_seconds": 30,
            "active_deadline_monotonic": None,
            "oldest_data_overdue_seconds": 172800.0,
            "progress": progress,
        },
    )
    app.config["HEALTH_DETAIL_AUTHORIZER"] = lambda _request: True

    response = app.test_client().get("/readyz")

    assert response.status_code == 200
    assert response.get_json()["status"] == "ready"
    assert response.get_json()["error_codes"] == []


def test_readyz_keeps_fatal_display_failure_above_progress_degradation():
    app, publisher = _app_with_health()
    publisher.publish_components(
        {
            "display": {"state": "display_unknown", "commit_id": "pending"},
            "scheduler": {
                "heartbeat_monotonic": time.monotonic(),
                "tick_seconds": 30,
                "active_deadline_monotonic": None,
                "progress": {
                    "enabled": True,
                    "observed": True,
                    "data_stalled_count": 2,
                    "presentation_stalled_count": 1,
                },
            },
        },
    )
    app.config["HEALTH_DETAIL_AUTHORIZER"] = lambda _request: True

    response = app.test_client().get("/readyz")

    assert response.status_code == 503
    assert response.get_json()["status"] == "not_ready"
    assert response.get_json()["error_codes"] == [
        "display_unknown", "data_progress_stalled", "presentation_progress_stalled",
    ]
