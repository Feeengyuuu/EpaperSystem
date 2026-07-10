"""Public liveness and readiness routes backed by immutable snapshots."""

from flask import Blueprint, current_app, jsonify, request

from health import health_jsonable


health_bp = Blueprint("health", __name__)


def _public_body(snapshot, now, status):
    return {
        "status": status,
        "release_id": snapshot.release_id,
        "boot_id": snapshot.boot_id,
        "uptime_seconds": round(snapshot.uptime_seconds(now), 3),
    }


def _detail_allowed():
    authorizer = current_app.config.get("HEALTH_DETAIL_AUTHORIZER")
    if not callable(authorizer):
        return False
    try:
        return bool(authorizer(request))
    except Exception:
        return False


def _response(body, status_code):
    response = jsonify(body)
    response.status_code = status_code
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@health_bp.get("/healthz")
def healthz():
    publisher = current_app.config["HEALTH_PUBLISHER"]
    snapshot = publisher.snapshot()
    now = publisher.now_monotonic()
    body = _public_body(snapshot, now, "alive")
    if _detail_allowed():
        result = current_app.config["READINESS_EVALUATOR"].evaluate(
            snapshot,
            now_monotonic=now,
        )
        body["components"] = health_jsonable(snapshot.components)
        body["readiness_status"] = result.status
        body["error_codes"] = list(result.error_codes)
    return _response(body, 200)


@health_bp.get("/readyz")
def readyz():
    publisher = current_app.config["HEALTH_PUBLISHER"]
    snapshot = publisher.snapshot()
    now = publisher.now_monotonic()
    result = current_app.config["READINESS_EVALUATOR"].evaluate(
        snapshot,
        now_monotonic=now,
    )
    body = _public_body(snapshot, now, result.status)
    if _detail_allowed():
        body["components"] = health_jsonable(snapshot.components)
        body["error_codes"] = list(result.error_codes)
    status_code = 200 if result.status in {"ready", "degraded"} else 503
    return _response(body, status_code)
