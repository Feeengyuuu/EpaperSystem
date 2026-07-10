import subprocess
import threading
import time

from PIL import Image

from src.runtime.refresh_contracts import TaskContext
from src.utils.browser_renderer import BrowserRenderer


def _context(seconds=2):
    return TaskContext.never_cancelled(
        deadline_monotonic=time.monotonic() + seconds,
    )


class TimeoutProcess:
    returncode = None
    pid = 1234

    def __init__(self):
        self.terminated = False
        self.killed = False
        self.wait_calls = 0

    def wait(self, timeout=None):
        self.wait_calls += 1
        if self.wait_calls <= 2:
            raise subprocess.TimeoutExpired("chromium", timeout)
        self.returncode = -9
        return self.returncode

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True

    def poll(self):
        return self.returncode


def test_timeout_terminates_kills_waits_and_removes_all_temp_paths(tmp_path):
    process = TimeoutProcess()
    renderer = BrowserRenderer(
        binary="chromium",
        temp_root=tmp_path,
        popen=lambda *_args, **_kwargs: process,
    )

    result = renderer.render_html(
        "<p>x</p>",
        viewport=(800, 480),
        context=_context(),
        timeout_seconds=0.01,
    )

    assert result is None
    assert process.terminated
    assert process.killed
    assert process.wait_calls == 3
    assert renderer.active_processes == ()
    assert list(tmp_path.iterdir()) == []


def test_each_render_uses_clean_profile_without_disabling_sandbox(tmp_path):
    commands = []

    class SuccessProcess:
        returncode = 0
        pid = 2222

        def __init__(self, command):
            commands.append(command)
            output = next(
                item.split("=", 1)[1]
                for item in command
                if item.startswith("--screenshot=")
            )
            Image.new("RGB", (8, 8), "white").save(output)

        def wait(self, timeout=None):
            return 0

        def poll(self):
            return self.returncode

        def terminate(self):
            self.returncode = -15

        def kill(self):
            self.returncode = -9

    renderer = BrowserRenderer(
        binary="chromium",
        temp_root=tmp_path,
        popen=lambda command, **_kwargs: SuccessProcess(command),
    )

    first = renderer.render_html("<p>one</p>", viewport=(800, 480), context=_context())
    second = renderer.render_html("<p>two</p>", viewport=(800, 480), context=_context())

    assert first.size == (8, 8)
    assert second.size == (8, 8)
    profiles = [
        next(arg for arg in command if arg.startswith("--user-data-dir="))
        for command in commands
    ]
    assert profiles[0] != profiles[1]
    assert all("--no-sandbox" not in command for command in commands)
    assert all("--disk-cache-size=1" in command for command in commands)
    assert all(
        any(argument.startswith("--proxy-server=http://127.0.0.1:") for argument in command)
        for command in commands
    )
    assert all("--proxy-bypass-list=<-loopback>" in command for command in commands)
    assert list(tmp_path.iterdir()) == []


def test_two_renderer_instances_never_overlap(tmp_path):
    state_lock = threading.Lock()
    active = 0
    maximum = 0
    next_pid = 3000

    class SlowProcess:
        returncode = 0

        def __init__(self, command):
            nonlocal active, maximum, next_pid
            with state_lock:
                next_pid += 1
                self.pid = next_pid
                active += 1
                maximum = max(maximum, active)
            output = next(
                item.split("=", 1)[1]
                for item in command
                if item.startswith("--screenshot=")
            )
            Image.new("RGB", (4, 4), "white").save(output)

        def wait(self, timeout=None):
            nonlocal active
            time.sleep(0.05)
            with state_lock:
                active -= 1
            return 0

        def poll(self):
            return self.returncode

        def terminate(self):
            pass

        def kill(self):
            pass

    popen = lambda command, **_kwargs: SlowProcess(command)
    renderers = [
        BrowserRenderer(binary="chromium", temp_root=tmp_path, popen=popen),
        BrowserRenderer(binary="chromium", temp_root=tmp_path, popen=popen),
    ]
    results = []

    threads = [
        threading.Thread(
            target=lambda renderer=renderer: results.append(
                renderer.render_html("<p>x</p>", viewport=(80, 48), context=_context())
            )
        )
        for renderer in renderers
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)

    assert len(results) == 2
    assert maximum == 1


def test_remote_url_requires_validator_and_negative_cache_is_bounded(tmp_path):
    calls = []
    renderer = BrowserRenderer(
        binary="chromium",
        temp_root=tmp_path,
        popen=lambda *_args, **_kwargs: calls.append(True),
    )

    assert renderer.render_url(
        "https://example.test/page?secret=value",
        viewport=(800, 480),
        context=_context(),
        validator=None,
    ) is None
    assert renderer.render_url(
        "https://example.test/page?secret=value",
        viewport=(800, 480),
        context=_context(),
        validator=lambda _url: False,
    ) is None

    assert calls == []
    assert renderer.negative_cache_size <= 1


def test_repeated_failures_leave_no_processes_or_temp_growth(tmp_path):
    next_pid = 5000

    class FailedProcess:
        returncode = 1

        def __init__(self):
            nonlocal next_pid
            next_pid += 1
            self.pid = next_pid

        def wait(self, timeout=None):
            return 1

        def poll(self):
            return self.returncode

        def terminate(self):
            pass

        def kill(self):
            pass

    renderer = BrowserRenderer(
        binary="chromium",
        temp_root=tmp_path,
        popen=lambda *_args, **_kwargs: FailedProcess(),
    )

    for index in range(100):
        assert renderer.render_html(
            f"<p>{index}</p>",
            viewport=(80, 48),
            context=_context(),
        ) is None

    assert renderer.active_processes == ()
    assert renderer.negative_cache_size == 100
    assert list(tmp_path.iterdir()) == []
