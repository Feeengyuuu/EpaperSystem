"""Injected publication worker for ``python -m web_portal.worker``.

The web package does not import a scheduler implementation.  Deployment
provides a no-argument factory whose result exposes ``run_once()`` (preferred)
or a no-argument ``publish_due()`` adapter.  A missing boundary fails before
the process enters its loop.
"""

from __future__ import annotations

import json
import logging
import math
import os
from pathlib import Path
import signal
import threading
import time

from .health import WorkerStateStore, worker_state_is_fresh
from .runtime import RuntimeConfigurationError, _enabled, _integer, load_injected_component


DEFAULT_WORKER_FACTORY = "web_portal.factories:create_worker"
logger = logging.getLogger(__name__)


class WorkerConfigurationError(RuntimeError):
    """Raised when the injected publication worker does not meet its contract."""


def heartbeat_is_fresh(path, *, max_age_seconds, now=None):
    """Return whether a v1/v2 cycle heartbeat is valid and recent."""

    current_time = time.time() if now is None else now
    if worker_state_is_fresh(path, max_age_seconds=max_age_seconds, now=current_time):
        return True
    try:
        maximum_age = float(max_age_seconds)
        current_time = float(current_time)
        target = Path(path)
        if maximum_age <= 0 or target.is_symlink() or target.stat().st_size > 4096:
            return False
        payload = json.loads(target.read_text(encoding="utf-8"))
        completed_at = float(payload["completed_at"])
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
        return False
    age = current_time - completed_at
    return (
        payload.get("version") == 1
        and math.isfinite(maximum_age)
        and math.isfinite(current_time)
        and math.isfinite(completed_at)
        and 0 <= age <= maximum_age
    )


def _operation(runner):
    operation = getattr(runner, "run_once", None)
    if not callable(operation):
        operation = getattr(runner, "publish_due", None)
    if not callable(operation):
        raise WorkerConfigurationError("worker factory result must provide run_once() or publish_due()")
    return operation


def run_worker(
    runner,
    *,
    once=False,
    interval_seconds=30,
    stop_event=None,
    heartbeat_path=None,
    clock=time.time,
):
    """Run one publication cycle or a stoppable fixed-delay loop."""

    operation = _operation(runner)
    try:
        interval = float(interval_seconds)
    except (TypeError, ValueError) as error:
        raise WorkerConfigurationError("worker interval must be numeric") from error
    if interval < 1:
        raise WorkerConfigurationError("worker interval must be at least one second")
    state_store = WorkerStateStore(heartbeat_path) if heartbeat_path else None

    def execute_cycle():
        try:
            cycle_result = operation()
        except Exception:
            if state_store is not None:
                try:
                    state_store.record_failed(clock())
                except Exception:
                    logger.error("Publication worker state persistence failed")
            logger.error("Publication worker cycle failed")
            raise
        if state_store is not None:
            try:
                state_store.record_completed(cycle_result, clock())
            except Exception:
                logger.error("Publication worker completion state persistence failed")
                raise
        return cycle_result

    if once:
        return execute_cycle()
    stop_event = stop_event or threading.Event()
    result = None
    while not stop_event.is_set():
        try:
            result = execute_cycle()
        except Exception:
            pass
        if stop_event.wait(interval):
            break
    return result


def build_worker(environment=None):
    environment = os.environ if environment is None else environment
    factory_spec = environment.get("WEB_PORTAL_WORKER_FACTORY", DEFAULT_WORKER_FACTORY)
    try:
        runner = load_injected_component(factory_spec)
    except RuntimeConfigurationError as error:
        raise WorkerConfigurationError(str(error)) from error
    _operation(runner)
    return runner


def main(environment=None):
    environment = os.environ if environment is None else environment
    runner = build_worker(environment)
    interval = _integer(
        environment,
        "WEB_PORTAL_WORKER_INTERVAL_SECONDS",
        30,
        minimum=1,
        maximum=86400,
    )
    once = _enabled(environment.get("WEB_PORTAL_WORKER_ONCE"), default=False)
    stop_event = threading.Event()

    def request_stop(_signum, _frame):
        stop_event.set()

    if not once:
        signal.signal(signal.SIGTERM, request_stop)
        signal.signal(signal.SIGINT, request_stop)
    return run_worker(
        runner,
        once=once,
        interval_seconds=interval,
        stop_event=stop_event,
        heartbeat_path=environment.get("WEB_PORTAL_WORKER_HEARTBEAT_FILE"),
    )


if __name__ == "__main__":
    main()


__all__ = [
    "WorkerConfigurationError",
    "build_worker",
    "heartbeat_is_fresh",
    "main",
    "run_worker",
]
