"""Spawned long-task children (Sports, Weather) log like the service process."""

import io
import logging
import logging.config
import multiprocessing
from pathlib import Path
import re
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from runtime import long_task_executor as long_task_module  # noqa: E402
from runtime.long_task_executor import InstanceIdentity, LongTaskExecutor  # noqa: E402
from runtime.refresh_contracts import TaskContext  # noqa: E402


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
SERVICE_FORMAT = "%(asctime)s - %(levelname)s - %(name)s - %(message)s"
SERVICE_LINE = re.compile(
    r"^\d{2}:\d{2}:\d{2} - (?P<level>[A-Z]+) - (?P<name>[\w.]+) - (?P<message>.*)$"
)


def _log_through_child_handlers_task(_payload, _cancel_event):
    """Emit Sports-style records through whatever the child configured."""

    root = logging.getLogger()
    buffer = io.StringIO()
    stream_is_stdout = []
    for handler in root.handlers:
        if isinstance(handler, logging.StreamHandler):
            stream_is_stdout.append(handler.stream is sys.stdout)
            handler.setStream(buffer)
    logging.getLogger("plugins.sports_dashboard.esports").warning(
        "LPL schedule fetch failed: probe"
    )
    logging.getLogger("plugins.sports_dashboard.club_football").info(
        "Club API-Football odds fallback failed: probe"
    )
    logging.getLogger("plugins.sports_dashboard.club_football").debug(
        "debug detail stays hidden"
    )
    return {
        "lines": buffer.getvalue().splitlines(),
        "stream_is_stdout": stream_is_stdout,
    }


def test_spawned_child_logs_warning_and_info_with_the_service_format():
    executor = LongTaskExecutor(
        {"probe": _log_through_child_handlers_task},
        max_queue=0,
    )
    try:
        handle = executor.submit(
            "probe",
            {},
            context=TaskContext.never_cancelled(
                deadline_monotonic=time.monotonic() + 60,
            ),
            instance_identity=InstanceIdentity("child-logging-probe", 1, 1),
        )
        result = handle.result(timeout=60)
    finally:
        executor.shutdown(deadline_monotonic=time.monotonic() + 10)

    assert result.status == "succeeded", result
    lines = result.value["lines"]
    parsed = [SERVICE_LINE.match(line) for line in lines]
    assert all(parsed), lines
    assert [(m["level"], m["name"], m["message"]) for m in parsed] == [
        (
            "WARNING",
            "plugins.sports_dashboard.esports",
            "LPL schedule fetch failed: probe",
        ),
        (
            "INFO",
            "plugins.sports_dashboard.club_football",
            "Club API-Football odds fallback failed: probe",
        ),
    ]
    assert result.value["stream_is_stdout"] == [True]


def test_child_logging_setup_leaves_the_parent_process_untouched(monkeypatch):
    calls = []
    root = logging.getLogger()
    handlers = list(root.handlers)
    monkeypatch.setattr(multiprocessing, "parent_process", lambda: None)
    monkeypatch.setattr(
        logging.config,
        "fileConfig",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    long_task_module._configure_child_logging()

    assert calls == []
    assert root.handlers == handlers


def test_child_logging_keeps_handlers_inherited_by_a_forked_child(monkeypatch):
    calls = []
    root = logging.getLogger()
    inherited = logging.NullHandler()
    monkeypatch.setattr(multiprocessing, "parent_process", lambda: object())
    monkeypatch.setattr(root, "handlers", [inherited])
    monkeypatch.setattr(
        logging.config,
        "fileConfig",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    long_task_module._configure_child_logging()

    assert calls == []
    assert root.handlers == [inherited]


def test_fresh_child_loads_the_service_logging_conf(monkeypatch):
    calls = []
    root = logging.getLogger()
    monkeypatch.setattr(multiprocessing, "parent_process", lambda: object())
    monkeypatch.setattr(root, "handlers", [])
    monkeypatch.setattr(
        logging.config,
        "fileConfig",
        lambda path, **kwargs: calls.append((Path(path).resolve(), kwargs)),
    )

    long_task_module._configure_child_logging()

    service_conf = (SRC_ROOT / "config" / "logging.conf").resolve()
    assert calls == [(service_conf, {"disable_existing_loggers": False})]
    assert service_conf.is_file()


def test_fresh_child_falls_back_to_the_service_format_without_the_conf(
    monkeypatch,
):
    root = logging.getLogger()
    previous_handlers = root.handlers
    previous_level = root.level
    monkeypatch.setattr(multiprocessing, "parent_process", lambda: object())

    def unreadable(*_args, **_kwargs):
        raise KeyError("formatters")

    monkeypatch.setattr(logging.config, "fileConfig", unreadable)
    root.handlers = []
    try:
        long_task_module._configure_child_logging()
        configured = list(root.handlers)
        configured_level = root.level
    finally:
        root.handlers = previous_handlers
        root.setLevel(previous_level)

    assert len(configured) == 1
    handler = configured[0]
    assert type(handler) is logging.StreamHandler
    assert handler.stream is sys.stdout
    assert handler.formatter._fmt == SERVICE_FORMAT
    assert handler.formatter.datefmt == "%H:%M:%S"
    assert configured_level == logging.INFO
