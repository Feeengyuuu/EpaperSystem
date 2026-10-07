from __future__ import annotations

from pathlib import Path
import json
import sys
from types import ModuleType

import pytest
from waitress.proxy_headers import proxy_headers_middleware
from werkzeug.test import Client
from werkzeug.wrappers import Response


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import web_portal.runtime as runtime
import web_portal.worker as worker_runtime
from web_portal.runtime import RuntimeConfigurationError, build_runtime_app
from web_portal.health import WorkerStateStore, load_worker_state
from web_portal.worker import (
    WorkerConfigurationError,
    build_worker,
    heartbeat_is_fresh,
    run_worker,
)


class RuntimePublications:
    def list_publications(self):
        return {"playlists": [], "publications": []}

    def get_publication(self, _slug):
        return None

    def get_asset(self, _asset_id):
        return None


def _install_factory(monkeypatch, name="portal_test_factory"):
    module = ModuleType(name)
    source = RuntimePublications()
    module.create_publications = lambda: source
    monkeypatch.setitem(sys.modules, name, module)
    return f"{name}:create_publications", source


def test_runtime_builds_app_from_factory_and_docker_secret_file(tmp_path, monkeypatch):
    factory_path, source = _install_factory(monkeypatch)
    secret_path = tmp_path / "portal_secret"
    secret_path.write_text("s" * 48 + "\n", encoding="utf-8")

    app = build_runtime_app(
        {
            "WEB_PORTAL_PUBLICATIONS_FACTORY": factory_path,
            "WEB_PORTAL_CREDENTIAL_ROOT": str(tmp_path / "credentials"),
            "WEB_PORTAL_SECRET_KEY_FILE": str(secret_path),
            "WEB_PORTAL_TIMEZONE": "America/Los_Angeles",
            "WEB_PORTAL_WORKER_STATE_FILE": str(tmp_path / "worker-state.json"),
            "WEB_PORTAL_WORKER_STATE_MAX_AGE_SECONDS": "420",
            "WEB_PORTAL_PUBLICATION_HEALTH_CACHE_SECONDS": "12",
        }
    )

    assert app.extensions["web_portal_publications"] is source
    assert app.secret_key == "s" * 48
    assert app.config["PORTAL_TIMEZONE"] == "America/Los_Angeles"
    assert app.config["CREDENTIAL_STORE"].root == tmp_path / "credentials"
    assert app.config["WORKER_STATE_PATH"] == str(tmp_path / "worker-state.json")
    assert app.config["WORKER_STATE_MAX_AGE_SECONDS"] == 420
    assert app.config["PUBLICATION_HEALTH_CACHE_SECONDS"] == 12


@pytest.mark.parametrize(
    "environment",
    [
        {"WEB_PORTAL_PUBLICATIONS_FACTORY": "missing.module:create"},
        {
            "WEB_PORTAL_PUBLICATIONS_FACTORY": "invalid-spec",
            "WEB_PORTAL_SECRET_KEY": "s" * 48,
        },
        {
            "WEB_PORTAL_PUBLICATIONS_FACTORY": "unused:create",
            "WEB_PORTAL_SECRET_KEY": "short",
        },
    ],
)
def test_runtime_fails_fast_for_missing_or_invalid_contract(environment):
    with pytest.raises(RuntimeConfigurationError):
        build_runtime_app(environment)


def test_runtime_rejects_a_session_key_that_is_not_private(tmp_path, monkeypatch):
    secret_path = tmp_path / "portal_secret"
    secret_path.write_text("s" * 48, encoding="utf-8")
    monkeypatch.setattr(runtime, "_secret_file_is_private", lambda _path: False, raising=False)

    with pytest.raises(RuntimeConfigurationError, match="private permissions"):
        runtime._load_secret({"WEB_PORTAL_SECRET_KEY_FILE": str(secret_path)})


def test_runtime_rejects_an_oversized_session_key_before_reading_it_unbounded(
    tmp_path,
    monkeypatch,
):
    secret_path = tmp_path / "portal_secret"
    secret_path.write_bytes(b"s" * 4097)
    monkeypatch.setattr(runtime, "_secret_file_is_private", lambda _path: True)

    with pytest.raises(RuntimeConfigurationError, match="too large"):
        runtime._load_secret({"WEB_PORTAL_SECRET_KEY_FILE": str(secret_path)})


def test_worker_state_is_loaded_from_one_bounded_file_descriptor(tmp_path, monkeypatch):
    state_path = tmp_path / "worker-state.json"
    WorkerStateStore(state_path).record_completed(
        {"attempted": 1, "published": 1},
        100.0,
    )

    def reject_path_read(*_args, **_kwargs):
        raise AssertionError("Path.read_text would reopen the checked path")

    monkeypatch.setattr(Path, "read_text", reject_path_read)

    state = load_worker_state(state_path)

    assert state is not None
    assert state.cycle_status == "ok"


def test_runtime_uses_production_reader_factory_by_default(tmp_path, monkeypatch):
    source = RuntimePublications()
    requested = []

    def load(spec):
        requested.append(spec)
        return source

    monkeypatch.setattr(runtime, "load_injected_component", load)

    app = build_runtime_app(
        {
            "WEB_PORTAL_SECRET_KEY": "s" * 48,
            "WEB_PORTAL_CREDENTIAL_ROOT": str(tmp_path / "credentials"),
        }
    )

    assert requested == ["web_portal.factories:create_reader"]
    assert app.extensions["web_portal_publications"] is source


def test_waitress_proxy_contract_preserves_one_caddy_hop():
    options = runtime._waitress_proxy_options({"WEB_PORTAL_TRUST_PROXY_HEADERS": "true"})
    observed = {}

    def probe(environ, start_response):
        observed.update(
            remote_addr=environ["REMOTE_ADDR"],
            scheme=environ["wsgi.url_scheme"],
            host=environ["HTTP_HOST"],
        )
        start_response("200 OK", [("Content-Type", "text/plain")])
        return [b"ok"]

    application = proxy_headers_middleware(
        probe,
        trusted_proxy=options["trusted_proxy"],
        trusted_proxy_count=options["trusted_proxy_count"],
        trusted_proxy_headers=options["trusted_proxy_headers"],
        clear_untrusted=options["clear_untrusted_proxy_headers"],
    )
    response = Client(application, Response).get(
        "/",
        environ_base={"REMOTE_ADDR": "172.20.0.3"},
        headers={
            "Host": "web:8080",
            "X-Forwarded-For": "198.51.100.8",
            "X-Forwarded-Host": "portal.example.test",
            "X-Forwarded-Proto": "https",
        },
    )

    assert response.status_code == 200
    assert observed == {
        "remote_addr": "198.51.100.8",
        "scheme": "https",
        "host": "portal.example.test",
    }


def test_runtime_main_passes_proxy_contract_to_waitress(tmp_path, monkeypatch):
    source = RuntimePublications()
    calls = []
    fake_waitress = ModuleType("waitress")
    fake_waitress.serve = lambda app, **kwargs: calls.append((app, kwargs))
    monkeypatch.setitem(sys.modules, "waitress", fake_waitress)
    monkeypatch.setattr(runtime, "load_injected_component", lambda _spec: source)

    runtime.main(
        {
            "WEB_PORTAL_SECRET_KEY": "s" * 48,
            "WEB_PORTAL_CREDENTIAL_ROOT": str(tmp_path / "credentials"),
            "WEB_PORTAL_TRUST_PROXY_HEADERS": "true",
        }
    )

    assert len(calls) == 1
    _, options = calls[0]
    assert options["trusted_proxy"] == "*"
    assert options["trusted_proxy_count"] == 1
    assert options["trusted_proxy_headers"] == {
        "x-forwarded-for",
        "x-forwarded-host",
        "x-forwarded-proto",
    }
    assert options["max_request_body_size"] == 16 * 1024
    assert options["max_request_header_size"] == 32 * 1024


def test_worker_runs_injected_run_once_boundary():
    class Runner:
        def __init__(self):
            self.calls = 0

        def run_once(self):
            self.calls += 1
            return {"published": 2}

    runner = Runner()

    result = run_worker(runner, once=True, interval_seconds=60)

    assert result == {"published": 2}
    assert runner.calls == 1


def test_worker_records_a_fresh_heartbeat_only_after_a_successful_cycle(tmp_path):
    heartbeat = tmp_path / "health" / "worker-heartbeat.json"

    class Runner:
        def run_once(self):
            assert not heartbeat.exists()
            return {"published": 1}

    result = run_worker(
        Runner(),
        once=True,
        interval_seconds=60,
        heartbeat_path=heartbeat,
        clock=lambda: 1_700_000_000.0,
    )

    assert result == {"published": 1}
    assert json.loads(heartbeat.read_text(encoding="utf-8")) == {
        "attempted": 1,
        "consecutive_failures": 0,
        "cycle_completed_at": 1_700_000_000.0,
        "cycle_status": "ok",
        "last_successful_publication_at": 1_700_000_000.0,
        "published": 1,
        "skipped": 0,
        "unavailable": 0,
        "version": 2,
    }
    assert heartbeat_is_fresh(
        heartbeat,
        max_age_seconds=60,
        now=1_700_000_030.0,
    )


def test_failed_worker_cycle_records_safe_liveness_state_without_leaking_exception(tmp_path, caplog):
    heartbeat = tmp_path / "worker-heartbeat.json"
    original = (
        '{"attempted":1,"consecutive_failures":0,"cycle_completed_at":100.0,'
        '"cycle_status":"ok","last_successful_publication_at":100.0,"published":1,'
        '"skipped":0,"unavailable":0,"version":2}\n'
    )
    heartbeat.write_text(original, encoding="utf-8")
    secret_canary = "signed-provider-url-secret-canary"

    class FailedRunner:
        def run_once(self):
            raise RuntimeError(secret_canary)

    with pytest.raises(RuntimeError, match=secret_canary):
        run_worker(
            FailedRunner(),
            once=True,
            interval_seconds=60,
            heartbeat_path=heartbeat,
            clock=lambda: 200.0,
        )

    state = load_worker_state(heartbeat)
    assert state.cycle_status == "failed"
    assert state.cycle_completed_at == 200.0
    assert state.last_successful_publication_at == 100.0
    assert state.consecutive_failures == 1
    assert secret_canary not in heartbeat.read_text(encoding="utf-8")
    assert secret_canary not in caplog.text
    assert heartbeat_is_fresh(
        heartbeat,
        max_age_seconds=60,
        now=200.0,
    )
    assert not heartbeat_is_fresh(
        tmp_path / "missing.json",
        max_age_seconds=60,
        now=200.0,
    )


def test_successful_cycle_logs_safe_event_when_completion_state_cannot_persist(
    tmp_path,
    caplog,
    monkeypatch,
):
    secret_canary = "worker-state-filesystem-secret-canary"

    class FailingStateStore:
        def __init__(self, _path):
            pass

        def record_completed(self, _report, _completed_at):
            raise OSError(secret_canary)

        def record_failed(self, _completed_at):
            raise OSError(secret_canary)

    class Runner:
        def run_once(self):
            return {"published": 1}

    monkeypatch.setattr(worker_runtime, "WorkerStateStore", FailingStateStore)

    with pytest.raises(OSError, match=secret_canary):
        run_worker(
            Runner(),
            once=True,
            heartbeat_path=tmp_path / "worker-state.json",
            clock=lambda: 100.0,
        )

    assert "Publication worker completion state persistence failed" in caplog.text
    assert secret_canary not in caplog.text


def test_all_unavailable_cycle_is_live_but_degraded_and_does_not_invent_success(tmp_path):
    heartbeat = tmp_path / "worker-heartbeat.json"

    class UnavailableRunner:
        def run_once(self):
            return {"attempted": 2, "published": 0, "skipped": 0, "unavailable": 2}

    run_worker(
        UnavailableRunner(),
        once=True,
        interval_seconds=60,
        heartbeat_path=heartbeat,
        clock=lambda: 300.0,
    )

    state = load_worker_state(heartbeat)
    assert state.cycle_status == "degraded"
    assert state.last_successful_publication_at is None
    assert heartbeat_is_fresh(heartbeat, max_age_seconds=60, now=330.0)


def test_loop_survives_one_provider_exception_and_recovers_on_the_next_cycle(tmp_path, caplog):
    heartbeat = tmp_path / "worker-heartbeat.json"
    secret_canary = "private-token-in-exception"

    class Runner:
        calls = 0

        def run_once(self):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError(secret_canary)
            return {"attempted": 1, "published": 1}

    class StopAfterTwoWaits:
        waits = 0

        def is_set(self):
            return False

        def wait(self, _interval):
            self.waits += 1
            return self.waits >= 2

    times = iter((400.0, 401.0))
    runner = Runner()
    result = run_worker(
        runner,
        interval_seconds=60,
        stop_event=StopAfterTwoWaits(),
        heartbeat_path=heartbeat,
        clock=lambda: next(times),
    )

    assert result == {"attempted": 1, "published": 1}
    assert runner.calls == 2
    state = load_worker_state(heartbeat)
    assert state.cycle_status == "ok"
    assert state.consecutive_failures == 0
    assert state.last_successful_publication_at == 401.0
    assert secret_canary not in caplog.text


def test_worker_accepts_publish_due_and_rejects_mutating_unknown_objects():
    class Publisher:
        def publish_due(self):
            return "complete"

    assert run_worker(Publisher(), once=True, interval_seconds=60) == "complete"
    with pytest.raises(WorkerConfigurationError, match="run_once"):
        run_worker(object(), once=True, interval_seconds=60)


def test_worker_uses_production_worker_factory_by_default(monkeypatch):
    class Runner:
        def run_once(self):
            return None

    requested = []

    def load(spec):
        requested.append(spec)
        return Runner()

    monkeypatch.setattr(worker_runtime, "load_injected_component", load)

    runner = build_worker({})

    assert isinstance(runner, Runner)
    assert requested == ["web_portal.factories:create_worker"]
