#!/usr/bin/env python3

"""InkyPi application construction and command-line entry point."""

from __future__ import annotations

import argparse
import logging
import logging.config
import os
import time
import warnings
from collections.abc import Sequence

from flask import Flask
from jinja2 import ChoiceLoader, FileSystemLoader

from blueprints.apikeys import apikeys_bp
from blueprints.health import health_bp
from blueprints.main import main_bp
from blueprints.playlist import playlist_bp
from blueprints.plugin import plugin_bp
from blueprints.settings import settings_bp
from config import Config
from display.display_manager import DisplayManager
from health import HealthCollector, HealthPublisher, ReadinessEvaluator
from plugins.plugin_registry import load_plugins, register_plugin_blueprints
from refresh_task import RefreshTask
from runtime.long_task_executor import shutdown_long_task_executors
from runtime_paths import RuntimePaths
from security.request_limits import (
    WAITRESS_MAX_REQUEST_BODY_BYTES,
    configure_request_limits,
)
from utils.app_utils import generate_startup_image
from utils.browser_renderer import close_browser_renderer
from utils.http_client import close_http_session, sanitize_dead_local_proxy_environment
from utils.network_utils import disable_wifi_powersave, start_wifi_reconnect_watchdog
from utils.secret_key import load_or_create_secret_key


# pi-heif ships only in the Pi runtime requirements; HEIF support is optional elsewhere.
try:
    from pi_heif import register_heif_opener
except ImportError:
    register_heif_opener = None


logger = logging.getLogger(__name__)


def _mark_startup_degraded(app: Flask, stage: str, error: Exception) -> None:
    reasons = dict(app.config.get("STARTUP_DEGRADED_REASONS") or {})
    reasons[stage] = f"{type(error).__name__}: {error}"[:512]
    app.config["STARTUP_DEGRADED"] = True
    app.config["STARTUP_DEGRADED_REASONS"] = reasons


def display_startup_image_best_effort(app: Flask) -> bool:
    """Attempt the one-shot startup image without blocking the control plane."""

    device_config = app.config["DEVICE_CONFIG"]
    if device_config.get_config("startup") is not True:
        return True
    try:
        logger.info("Startup flag is set, displaying startup image")
        image = generate_startup_image(device_config.get_resolution())
        app.config["DISPLAY_MANAGER"].display_image(image)
        device_config.update_value("startup", False, write=True)
    except Exception as error:
        logger.exception("Startup image could not be displayed; continuing degraded")
        _mark_startup_degraded(app, "startup_image", error)
        return False
    return True


def _configure_process_logging() -> None:
    logging.config.fileConfig(os.path.join(os.path.dirname(__file__), "config", "logging.conf"))
    logging.getLogger("waitress.queue").setLevel(logging.ERROR)
    # Suppress the known noisy Inky hardware warning only when the process is
    # actually launched; importing the application remains side-effect free.
    warnings.filterwarnings("ignore", message=".*Busy Wait: Held high.*")


def build_application(
    *,
    dev_mode: bool,
    runtime_paths: RuntimePaths | None = None,
) -> Flask:
    """Construct the Flask application without starting background services."""

    sanitize_dead_local_proxy_environment()
    paths = runtime_paths or RuntimePaths.from_environment(dev_mode=dev_mode)

    app = Flask(__name__)
    template_dirs = [
        os.path.join(os.path.dirname(__file__), "templates"),
        os.path.join(os.path.dirname(__file__), "plugins"),
    ]
    app.jinja_loader = ChoiceLoader([FileSystemLoader(directory) for directory in template_dirs])

    # Preserve the established construction order: configuration, display,
    # refresh service, then plugin registration.
    device_config = Config(runtime_paths=paths)
    display_manager = DisplayManager(device_config)
    refresh_task = RefreshTask(device_config, display_manager)
    runtime_plugins = getattr(
        device_config,
        "get_runtime_plugins",
        device_config.get_plugins,
    )()
    load_plugins(runtime_plugins)

    app.config["RUNTIME_PATHS"] = paths
    app.config["DEV_MODE"] = dev_mode
    app.config["DEVICE_CONFIG"] = device_config
    app.config["DISPLAY_MANAGER"] = display_manager
    app.config["REFRESH_TASK"] = refresh_task
    app.config["STARTUP_DEGRADED"] = False
    app.config["STARTUP_DEGRADED_REASONS"] = {}
    display_init_error = getattr(display_manager, "initialization_error", None)
    if display_init_error is not None:
        _mark_startup_degraded(app, "display_init", display_init_error)
    health_publisher = HealthPublisher(release_id=paths.release_id)
    health_collector = HealthCollector(
        health_publisher,
        refresh_task=refresh_task,
        device_config=device_config,
        runtime_paths=paths,
        dev_mode=dev_mode,
        startup_state=lambda: {
            "degraded": app.config.get("STARTUP_DEGRADED", False),
            "reasons": app.config.get("STARTUP_DEGRADED_REASONS", {}),
        },
    )
    app.config["HEALTH_PUBLISHER"] = health_publisher
    app.config["HEALTH_COLLECTOR"] = health_collector
    app.config["READINESS_EVALUATOR"] = ReadinessEvaluator()
    configure_request_limits(app)
    app.secret_key = load_or_create_secret_key(paths.flask_secret_file)

    app.register_blueprint(main_bp)
    app.register_blueprint(health_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(plugin_bp)
    app.register_blueprint(playlist_bp)
    app.register_blueprint(apikeys_bp)
    register_plugin_blueprints(app)

    if register_heif_opener:
        register_heif_opener()
    else:
        logger.warning("pi-heif is not installed; HEIF/HEIC images will not be supported")

    return app


def _web_server_threads(device_config: Config) -> int:
    raw_threads = device_config.get_config("web_server_threads", default=4)
    try:
        threads = int(raw_threads)
    except (TypeError, ValueError):
        threads = 4
    return max(1, min(16, threads))


def _log_development_url(port: int) -> None:
    import socket

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            local_ip = sock.getsockname()[0]
        logger.info("Serving on http://%s:%s", local_ip, port)
    except OSError:
        pass


def run(app: Flask, *, dev_mode: bool, port: int) -> int:
    """Run background work and HTTP serving while preserving degraded access."""

    device_config = app.config["DEVICE_CONFIG"]
    refresh_task = app.config["REFRESH_TASK"]
    health_collector = app.config.get("HEALTH_COLLECTOR")

    try:
        if health_collector is not None:
            try:
                health_collector.start()
            except Exception as error:
                logger.exception("Health collector failed to start")
                _mark_startup_degraded(app, "health_collector", error)
        try:
            refresh_task.start()
        except Exception as error:
            logger.exception("Refresh task failed to start; continuing degraded")
            _mark_startup_degraded(app, "refresh_task", error)

        display_startup_image_best_effort(app)

        if dev_mode:
            _log_development_url(port)

        from waitress import serve

        serve(
            app,
            host="0.0.0.0",
            port=port,
            threads=_web_server_threads(device_config),
            max_request_body_size=WAITRESS_MAX_REQUEST_BODY_BYTES,
        )
        return 0
    finally:
        try:
            try:
                refresh_task.stop()
            finally:
                if health_collector is not None:
                    health_collector.stop()
        finally:
            try:
                shutdown_long_task_executors(
                    deadline_monotonic=time.monotonic() + 5.0,
                )
            finally:
                try:
                    close_browser_renderer()
                finally:
                    close_http_session()


def main(argv: Sequence[str] | None = None) -> int:
    """Run the InkyPi service and return its process exit status."""

    parser = argparse.ArgumentParser(description="InkyPi Display Server")
    parser.add_argument("-d", "--dev", action="store_true", help="Run in development mode")
    args = parser.parse_args(argv)
    dev_mode = bool(args.dev)
    port = 8080 if dev_mode else 80

    _configure_process_logging()
    if dev_mode:
        logger.info("Starting InkyPi in DEVELOPMENT mode on port %s", port)
    else:
        logger.info("Starting InkyPi in PRODUCTION mode on port %s", port)
        disable_wifi_powersave()
        start_wifi_reconnect_watchdog()

    app = build_application(dev_mode=dev_mode)
    return run(app, dev_mode=dev_mode, port=port)


if __name__ == "__main__":
    raise SystemExit(main())
