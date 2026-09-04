import importlib
import subprocess
import sys
import threading
import warnings
from pathlib import Path
from types import SimpleNamespace

from flask import Flask
import pytest


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_DIR))

from runtime_paths import RuntimePaths


def test_process_setup_preserves_inky_busy_wait_warning_filter(monkeypatch):
    import inkypi

    monkeypatch.setattr(
        inkypi.logging.config,
        "fileConfig",
        lambda _path, **_kwargs: None,
    )

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        inkypi._configure_process_logging()
        warnings.warn("Busy Wait: Held high", UserWarning, stacklevel=1)

    assert caught == []


def test_process_logging_preserves_preimported_operational_loggers(monkeypatch):
    import inkypi

    calls = []
    monkeypatch.setattr(
        inkypi.logging.config,
        "fileConfig",
        lambda path, **kwargs: calls.append((path, kwargs)),
    )

    inkypi._configure_process_logging()

    assert len(calls) == 1
    assert calls[0][1]["disable_existing_loggers"] is False


def test_import_does_not_parse_cli_or_construct_or_start_services(monkeypatch):
    import config
    import refresh_task
    import utils.network_utils as network_utils
    import waitress

    def forbidden(*_args, **_kwargs):
        raise AssertionError("service side effect ran while importing inkypi")

    monkeypatch.setattr(sys, "argv", ["inkypi.py", "--not-an-inkypi-option"])
    monkeypatch.setattr(config.Config, "__init__", forbidden)
    monkeypatch.setattr(refresh_task.RefreshTask, "start", forbidden)
    monkeypatch.setattr(network_utils, "disable_wifi_powersave", forbidden)
    monkeypatch.setattr(network_utils, "start_wifi_reconnect_watchdog", forbidden)
    monkeypatch.setattr(waitress, "serve", forbidden)
    sys.modules.pop("inkypi", None)

    try:
        module = importlib.import_module("inkypi")

        assert callable(module.build_application)
        assert callable(module.main)
        assert not hasattr(module, "app")
    finally:
        # Do not retain from-import aliases bound to monkeypatched callables.
        sys.modules.pop("inkypi", None)


def test_factory_uses_one_injected_paths_identity_and_canonical_secret(tmp_path, monkeypatch):
    import inkypi

    monkeypatch.setenv("INKYPI_DEV_ROOT", str(tmp_path / "src"))
    monkeypatch.setenv(
        "INKYPI_FLASK_SECRET_FILE",
        str(tmp_path / "runtime" / "flask.secret"),
    )
    paths = RuntimePaths.from_environment(dev_mode=True)
    events = []

    class FakeConfig:
        def __init__(self, *, runtime_paths):
            events.append(("config", runtime_paths))
            self.runtime_paths = runtime_paths

        def get_plugins(self):
            return []

    class FakeDisplayManager:
        def __init__(self, device_config):
            events.append(("display", device_config))

    class FakeRefreshTask:
        def __init__(self, device_config, display_manager, *, plugin_registry):
            self.plugin_registry = plugin_registry
            events.append(("refresh", device_config, display_manager))

    monkeypatch.setattr(inkypi, "Config", FakeConfig)
    monkeypatch.setattr(inkypi, "DisplayManager", FakeDisplayManager)
    monkeypatch.setattr(inkypi, "RefreshTask", FakeRefreshTask)
    monkeypatch.setattr(
        inkypi.RuntimePaths,
        "from_environment",
        staticmethod(lambda **_kwargs: (_ for _ in ()).throw(AssertionError("paths rebuilt"))),
    )
    monkeypatch.setattr(
        inkypi,
        "load_or_create_secret_key",
        lambda path: events.append(("secret", Path(path))) or "canonical-secret",
    )
    monkeypatch.setattr(
        inkypi,
        "load_plugins",
        lambda plugins, **kwargs: events.append(("load_plugins", plugins)),
    )
    monkeypatch.setattr(
        inkypi,
        "register_plugin_blueprints",
        lambda app: events.append(("plugin_blueprints", app)),
    )

    app = inkypi.build_application(dev_mode=True, runtime_paths=paths)

    assert app.config["REFRESH_TASK"].plugin_registry is app.extensions["plugin_registry"]
    assert app.config["RUNTIME_PATHS"] is paths
    assert app.config["DEVICE_CONFIG"].runtime_paths is paths
    assert app.config["DEV_MODE"] is True
    assert app.secret_key == "canonical-secret"
    assert [event[0] for event in events[:3]] == ["config", "display", "refresh"]
    assert ("secret", paths.flask_secret_file) in events


def test_factory_constructs_runtime_paths_exactly_once_when_not_injected(tmp_path, monkeypatch):
    import inkypi

    monkeypatch.setenv("INKYPI_DEV_ROOT", str(tmp_path / "src"))
    paths = RuntimePaths.from_environment(dev_mode=True)
    calls = []

    class FakeConfig:
        def __init__(self, *, runtime_paths):
            self.runtime_paths = runtime_paths

        def get_plugins(self):
            return []

    class FakeDisplayManager:
        def __init__(self, _device_config):
            pass

    class FakeRefreshTask:
        def __init__(self, _device_config, _display_manager, *, plugin_registry):
            self.plugin_registry = plugin_registry
            pass

    def build_paths(*, dev_mode):
        calls.append(dev_mode)
        return paths

    monkeypatch.setattr(inkypi.RuntimePaths, "from_environment", staticmethod(build_paths))
    monkeypatch.setattr(inkypi, "Config", FakeConfig)
    monkeypatch.setattr(inkypi, "DisplayManager", FakeDisplayManager)
    monkeypatch.setattr(inkypi, "RefreshTask", FakeRefreshTask)
    monkeypatch.setattr(inkypi, "load_plugins", lambda _plugins, **kwargs: None)
    monkeypatch.setattr(inkypi, "register_plugin_blueprints", lambda _app: None)
    monkeypatch.setattr(inkypi, "load_or_create_secret_key", lambda _path: "secret")

    app = inkypi.build_application(dev_mode=True)

    assert calls == [True]
    assert app.config["REFRESH_TASK"].plugin_registry is app.extensions["plugin_registry"]
    assert app.config["RUNTIME_PATHS"] is paths
    assert app.config["DEVICE_CONFIG"].runtime_paths is paths


def test_factory_marks_hardware_initialization_failure_degraded(tmp_path, monkeypatch):
    import inkypi

    monkeypatch.setenv("INKYPI_DEV_ROOT", str(tmp_path / "src"))
    paths = RuntimePaths.from_environment(dev_mode=True)

    class FakeConfig:
        def __init__(self, *, runtime_paths):
            self.runtime_paths = runtime_paths

        def get_plugins(self):
            return []

    class DegradedDisplayManager:
        def __init__(self, _device_config):
            self.initialization_error = OSError("panel unavailable")

    class FakeRefreshTask:
        def __init__(self, _device_config, _display_manager, *, plugin_registry):
            self.plugin_registry = plugin_registry
            pass

    monkeypatch.setattr(inkypi, "Config", FakeConfig)
    monkeypatch.setattr(inkypi, "DisplayManager", DegradedDisplayManager)
    monkeypatch.setattr(inkypi, "RefreshTask", FakeRefreshTask)
    monkeypatch.setattr(inkypi, "load_plugins", lambda _plugins, **kwargs: None)
    monkeypatch.setattr(inkypi, "register_plugin_blueprints", lambda _app: None)
    monkeypatch.setattr(inkypi, "load_or_create_secret_key", lambda _path: "secret")

    app = inkypi.build_application(dev_mode=True, runtime_paths=paths)

    assert app.config["STARTUP_DEGRADED"] is True
    assert "display_init" in app.config["STARTUP_DEGRADED_REASONS"]


def test_main_stops_refresh_task_when_start_raises(monkeypatch):
    import inkypi

    events = []

    class FakeConfig:
        def get_config(self, _key, default=None):
            return default

    class FailingRefreshTask:
        def start(self):
            events.append("start")
            raise RuntimeError("partial start failed")

        def stop(self):
            events.append("stop")

    app = Flask(__name__)
    app.config.update(
        DEVICE_CONFIG=FakeConfig(),
        DISPLAY_MANAGER=object(),
        REFRESH_TASK=FailingRefreshTask(),
    )
    monkeypatch.setattr(inkypi, "_configure_process_logging", lambda: None)
    monkeypatch.setattr(inkypi, "build_application", lambda **_kwargs: app)

    served = []
    monkeypatch.setattr(
        "waitress.serve",
        lambda *_args, **_kwargs: served.append(True),
    )

    assert inkypi.main(["--dev"]) == 0

    assert events == ["start", "stop"]
    assert served == [True]
    assert app.config["STARTUP_DEGRADED"] is True
    assert "refresh_task" in app.config["STARTUP_DEGRADED_REASONS"]


def test_startup_image_failure_is_degraded_and_still_serves(monkeypatch):
    import inkypi

    events = []

    class FakeConfig:
        def get_config(self, key, default=None):
            if key == "startup":
                return True
            return default

        def get_resolution(self):
            return (800, 480)

        def update_value(self, *_args, **_kwargs):
            events.append("startup-cleared")

    class RefreshTask:
        def start(self):
            events.append("start")

        def stop(self):
            events.append("stop")

    app = Flask(__name__)
    app.config.update(
        DEVICE_CONFIG=FakeConfig(),
        DISPLAY_MANAGER=object(),
        REFRESH_TASK=RefreshTask(),
    )
    monkeypatch.setattr(inkypi, "_configure_process_logging", lambda: None)
    monkeypatch.setattr(inkypi, "build_application", lambda **_kwargs: app)
    monkeypatch.setattr(inkypi, "_log_development_url", lambda _port: None)
    monkeypatch.setattr(
        inkypi,
        "generate_startup_image",
        lambda _resolution: (_ for _ in ()).throw(OSError("network offline")),
    )
    monkeypatch.setattr(
        "waitress.serve",
        lambda *_args, **_kwargs: events.append("serve"),
    )

    assert inkypi.main(["--dev"]) == 0

    assert events == ["start", "serve", "stop"]
    assert app.config["STARTUP_DEGRADED"] is True
    assert "startup_image" in app.config["STARTUP_DEGRADED_REASONS"]


def test_get_ip_address_returns_default_when_offline(monkeypatch):
    from utils import app_utils

    monkeypatch.setattr(
        app_utils.socket,
        "socket",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("offline")),
    )

    assert app_utils.get_ip_address(default="Unknown") == "Unknown"


@pytest.mark.parametrize(
    ("argv", "expected_port", "expect_wifi"),
    [
        ([], 80, True),
        (["--dev"], 8080, False),
        (["-d"], 8080, False),
    ],
)
def test_main_modes_start_serve_and_stop_in_order(
    monkeypatch,
    argv,
    expected_port,
    expect_wifi,
):
    import inkypi
    import waitress

    events = []

    class FakeConfig:
        def get_config(self, key, default=None):
            return False if key == "startup" else default

    class RefreshTask:
        def start(self):
            events.append("start")

        def stop(self):
            events.append("stop")

    app = Flask(__name__)
    app.config.update(
        DEVICE_CONFIG=FakeConfig(),
        DISPLAY_MANAGER=object(),
        REFRESH_TASK=RefreshTask(),
    )
    monkeypatch.setattr(inkypi, "_configure_process_logging", lambda: events.append("logging"))
    monkeypatch.setattr(inkypi, "disable_wifi_powersave", lambda: events.append("wifi_disable"))
    monkeypatch.setattr(
        inkypi,
        "start_wifi_reconnect_watchdog",
        lambda: events.append("wifi_watchdog"),
    )
    monkeypatch.setattr(
        inkypi,
        "build_application",
        lambda **_kwargs: events.append("build") or app,
    )
    monkeypatch.setattr(inkypi, "_log_development_url", lambda _port: None)
    monkeypatch.setattr(
        waitress,
        "serve",
        lambda _app, **kwargs: events.append(
            (
                "serve",
                kwargs["port"],
                kwargs["max_request_body_size"],
            )
        ),
    )

    assert inkypi.main(argv) == 0
    assert events[-3:] == [
        "start",
        ("serve", expected_port, 8 * 1024 * 1024 + 64 * 1024),
        "stop",
    ]
    assert ("wifi_disable" in events) is expect_wifi
    assert ("wifi_watchdog" in events) is expect_wifi


@pytest.mark.parametrize("serve_error", [RuntimeError("serve failed"), KeyboardInterrupt()])
def test_main_stops_and_preserves_serve_failures(monkeypatch, serve_error):
    import inkypi
    import waitress

    events = []

    class FakeConfig:
        def get_config(self, key, default=None):
            return False if key == "startup" else default

    class RefreshTask:
        def start(self):
            events.append("start")

        def stop(self):
            events.append("stop")

    app = Flask(__name__)
    app.config.update(
        DEVICE_CONFIG=FakeConfig(),
        DISPLAY_MANAGER=object(),
        REFRESH_TASK=RefreshTask(),
    )
    monkeypatch.setattr(inkypi, "_configure_process_logging", lambda: None)
    monkeypatch.setattr(inkypi, "build_application", lambda **_kwargs: app)
    monkeypatch.setattr(inkypi, "_log_development_url", lambda _port: None)

    def fail_serve(*_args, **_kwargs):
        raise serve_error

    monkeypatch.setattr(waitress, "serve", fail_serve)

    with pytest.raises(type(serve_error)) as caught:
        inkypi.main(["--dev"])

    assert caught.value is serve_error
    assert events == ["start", "stop"]


def test_run_reaps_long_tasks_before_closing_browser_and_http(monkeypatch):
    import inkypi
    import waitress

    events = []

    class FakeConfig:
        def get_config(self, key, default=None):
            return False if key == "startup" else default

    class RefreshTask:
        def start(self):
            events.append("start")

        def stop(self):
            events.append("stop")

    app = Flask(__name__)
    app.config.update(
        DEVICE_CONFIG=FakeConfig(),
        DISPLAY_MANAGER=object(),
        REFRESH_TASK=RefreshTask(),
    )
    monkeypatch.setattr(waitress, "serve", lambda *_args, **_kwargs: events.append("serve"))
    monkeypatch.setattr(
        inkypi,
        "shutdown_long_task_executors",
        lambda **_kwargs: events.append("long_tasks"),
        raising=False,
    )
    monkeypatch.setattr(
        inkypi,
        "close_browser_renderer",
        lambda: events.append("browser"),
    )
    monkeypatch.setattr(
        inkypi,
        "close_http_session",
        lambda: events.append("http"),
    )

    assert inkypi.run(app, dev_mode=False, port=80) == 0

    assert events == [
        "start",
        "serve",
        "stop",
        "long_tasks",
        "browser",
        "http",
    ]


def test_run_consumes_staged_memory_restart_request(monkeypatch):
    import inkypi
    import waitress

    events = []
    interrupted = threading.Event()

    class FakeConfig:
        def get_config(self, key, default=None):
            return False if key == "startup" else default

    class RefreshTask:
        restart_request = None

        def start(self):
            events.append("start")

        def stop(self, join_timeout=None):
            events.append(("stop", join_timeout))

    refresh_task = RefreshTask()
    app = Flask(__name__)
    app.config.update(
        DEVICE_CONFIG=FakeConfig(),
        DISPLAY_MANAGER=object(),
        REFRESH_TASK=refresh_task,
    )

    def request_memory_restart(_app, **_kwargs):
        refresh_task.restart_request = {
            "reason": "memory_pressure",
            "available_mb": 40.0,
            "swap_percent": 90.0,
        }
        assert interrupted.wait(1.0)
        events.append("serve-returned")

    monkeypatch.setattr(waitress, "serve", request_memory_restart)
    monkeypatch.setattr(
        inkypi,
        "_interrupt_waitress_for_restart",
        lambda: events.append("interrupt") or interrupted.set(),
        raising=False,
    )
    monkeypatch.setattr(
        inkypi,
        "RESTART_REQUEST_POLL_SECONDS",
        0.01,
        raising=False,
    )
    monkeypatch.setattr(
        inkypi,
        "shutdown_long_task_executors",
        lambda **_kwargs: events.append("long-tasks"),
    )
    monkeypatch.setattr(
        inkypi,
        "close_browser_renderer",
        lambda: events.append("browser"),
    )
    monkeypatch.setattr(
        inkypi,
        "close_http_session",
        lambda: events.append("http"),
    )

    assert inkypi.run(app, dev_mode=False, port=80) == 75
    assert events == [
        "start",
        "interrupt",
        "serve-returned",
        ("stop", inkypi.MEMORY_PRESSURE_WORKER_STOP_TIMEOUT_SECONDS),
        "long-tasks",
        "browser",
        "http",
    ]


@pytest.mark.parametrize(
    ("platform_name", "expected_events"),
    [
        ("nt", ["interrupt-main"]),
        ("posix", [("kill", 1234, 2)]),
    ],
)
def test_memory_restart_interrupts_waitress_with_platform_safe_signal(
    monkeypatch,
    platform_name,
    expected_events,
):
    import inkypi

    events = []
    monkeypatch.setattr(
        inkypi,
        "PLATFORM_OS_NAME",
        platform_name,
        raising=False,
    )
    monkeypatch.setattr(inkypi.os, "getpid", lambda: 1234)
    monkeypatch.setattr(
        inkypi.os,
        "kill",
        lambda pid, signal_number: events.append(("kill", pid, signal_number)),
    )
    monkeypatch.setattr(
        inkypi,
        "_thread",
        SimpleNamespace(interrupt_main=lambda: events.append("interrupt-main")),
        raising=False,
    )

    inkypi._interrupt_waitress_for_restart()

    assert events == expected_events


def test_memory_restart_force_exits_after_cleanup_when_worker_will_not_stop():
    child_program = f"""
import sys
import threading
import time

sys.path.insert(0, {str(SRC_DIR)!r})

from flask import Flask
import inkypi
import waitress


class FakeConfig:
    def get_config(self, key, default=None):
        return False if key == "startup" else default


class StuckRefreshTask:
    restart_request = {{
        "reason": "memory_pressure",
        "available_mb": 40.0,
        "swap_percent": 90.0,
    }}

    def __init__(self):
        self.never_stop = threading.Event()

    def start(self):
        threading.Thread(
            target=self.never_stop.wait,
            name="stuck-refresh-worker",
            daemon=False,
        ).start()

    def stop(self, join_timeout=None):
        print(f"cleanup:refresh:{{join_timeout}}", flush=True)
        return False


def serve_until_interrupted(*_args, **_kwargs):
    try:
        while True:
            time.sleep(0.05)
    except KeyboardInterrupt:
        return


refresh_task = StuckRefreshTask()
app = Flask(__name__)
app.config.update(
    DEVICE_CONFIG=FakeConfig(),
    DISPLAY_MANAGER=object(),
    REFRESH_TASK=refresh_task,
)
waitress.serve = serve_until_interrupted
inkypi.shutdown_long_task_executors = (
    lambda **_kwargs: print("cleanup:long-tasks", flush=True)
)
inkypi.close_browser_renderer = lambda: print("cleanup:browser", flush=True)
inkypi.close_http_session = lambda: print("cleanup:http", flush=True)

raise SystemExit(inkypi.run(app, dev_mode=False, port=80))
"""

    try:
        result = subprocess.run(
            [sys.executable, "-c", child_program],
            cwd=str(Path(__file__).resolve().parents[1]),
            capture_output=True,
            text=True,
            timeout=4.0,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        pytest.fail(
            "memory restart stayed blocked behind a non-daemon refresh worker "
            f"after {error.timeout} seconds"
        )

    assert result.returncode == 75, result.stderr
    cleanup_events = [
        line
        for line in result.stdout.splitlines()
        if line.startswith("cleanup:")
    ]
    assert cleanup_events == [
        "cleanup:refresh:5.0",
        "cleanup:long-tasks",
        "cleanup:browser",
        "cleanup:http",
    ]


def test_memory_restart_force_exits_after_cleanup_when_stop_raises(monkeypatch):
    import inkypi
    import waitress

    events = []

    class ForcedExit(BaseException):
        def __init__(self, code):
            self.code = code

    class FakeConfig:
        def get_config(self, key, default=None):
            return False if key == "startup" else default

    class BrokenStopRefreshTask:
        restart_request = {
            "reason": "memory_pressure",
            "available_mb": 40.0,
            "swap_percent": 90.0,
        }

        def start(self):
            events.append("start")

        def stop(self, join_timeout=None):
            events.append(("stop", join_timeout))
            raise RuntimeError("stop failed")

    app = Flask(__name__)
    app.config.update(
        DEVICE_CONFIG=FakeConfig(),
        DISPLAY_MANAGER=object(),
        REFRESH_TASK=BrokenStopRefreshTask(),
    )
    monkeypatch.setattr(waitress, "serve", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        inkypi,
        "shutdown_long_task_executors",
        lambda **_kwargs: events.append("long-tasks"),
    )
    monkeypatch.setattr(
        inkypi,
        "close_browser_renderer",
        lambda: events.append("browser"),
    )
    monkeypatch.setattr(
        inkypi,
        "close_http_session",
        lambda: events.append("http"),
    )
    monkeypatch.setattr(
        inkypi.os,
        "_exit",
        lambda code: (_ for _ in ()).throw(ForcedExit(code)),
    )

    with pytest.raises(ForcedExit) as error:
        inkypi.run(app, dev_mode=False, port=80)

    assert error.value.code == 75
    assert events == [
        "start",
        ("stop", inkypi.MEMORY_PRESSURE_WORKER_STOP_TIMEOUT_SECONDS),
        "long-tasks",
        "browser",
        "http",
    ]
