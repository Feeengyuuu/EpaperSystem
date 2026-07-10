import ast
from pathlib import Path
import time

from PIL import Image

from src.runtime.refresh_contracts import TaskContext
from src.security.ssrf import ApprovedTarget, UnsafeTarget
from src.utils.browser_renderer import BrowserRenderer


def _context():
    return TaskContext.never_cancelled(deadline_monotonic=time.monotonic() + 2)


class Policy:
    def __init__(self, error=None):
        self.error = error
        self.urls = []

    def resolve_and_validate(self, url):
        self.urls.append(url)
        if self.error:
            raise self.error
        return ApprovedTarget(
            normalized_url="https://safe.example/page",
            scheme="https",
            hostname="safe.example",
            port=443,
            addresses=("93.184.216.34",),
        )


class Proxy:
    def __init__(self, available=True):
        self.available = available
        self.proxy_url = "http://127.0.0.1:43123" if available else None
        self.closed = False
        self.starts = 0

    def start(self):
        self.starts += 1
        return self.available

    def close(self):
        self.closed = True


def test_remote_render_is_revalidated_and_chromium_has_no_proxy_bypass(tmp_path):
    commands = []
    policy = Policy()
    proxy = Proxy()

    class Process:
        returncode = 0
        pid = 7123

        def __init__(self, command):
            commands.append(command)
            output = next(
                value.split("=", 1)[1]
                for value in command
                if value.startswith("--screenshot=")
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
        popen=lambda command, **_kwargs: Process(command),
        ssrf_policy=policy,
        egress_proxy=proxy,
    )

    image = renderer.render_url(
        "https://safe.example/page#fragment",
        viewport=(800, 480),
        context=_context(),
        validator=lambda url: url,
    )

    assert image.size == (8, 8)
    assert policy.urls == ["https://safe.example/page#fragment"]
    assert proxy.starts == 1
    command = commands[0]
    assert "--proxy-server=http://127.0.0.1:43123" in command
    assert "--proxy-bypass-list=<-loopback>" in command
    assert "--disable-quic" in command
    assert command[-1] == "https://safe.example/page"


def test_unsafe_target_or_unavailable_proxy_never_launches_browser(tmp_path):
    launches = []
    rejected = BrowserRenderer(
        binary="chromium",
        temp_root=tmp_path,
        popen=lambda *_args, **_kwargs: launches.append(True),
        ssrf_policy=Policy(UnsafeTarget("private address")),
        egress_proxy=Proxy(),
    )
    unavailable = BrowserRenderer(
        binary="chromium",
        temp_root=tmp_path,
        popen=lambda *_args, **_kwargs: launches.append(True),
        ssrf_policy=Policy(),
        egress_proxy=Proxy(available=False),
    )

    assert rejected.render_url(
        "http://127.0.0.1/",
        viewport=(800, 480),
        context=_context(),
        validator=lambda url: url,
    ) is None
    assert unavailable.render_url(
        "https://safe.example/",
        viewport=(800, 480),
        context=_context(),
        validator=lambda url: url,
    ) is None
    assert unavailable.render_html(
        "<p>local content with possible remote subresources</p>",
        viewport=(800, 480),
        context=_context(),
    ) is None
    assert launches == []


def test_renderer_close_stops_egress_proxy(tmp_path):
    proxy = Proxy()
    renderer = BrowserRenderer(
        binary="chromium",
        temp_root=tmp_path,
        ssrf_policy=Policy(),
        egress_proxy=proxy,
    )

    renderer.close()

    assert proxy.closed


def test_all_remote_screenshot_callers_supply_ssrf_validator():
    source_root = Path(__file__).resolve().parents[1] / "src" / "plugins"
    callers = (
        source_root / "screenshot" / "screenshot.py",
        source_root / "newspaper" / "newspaper.py",
        source_root / "sports_dashboard" / "worldcup.py",
        source_root / "tech_pulse" / "tech_pulse.py",
    )

    for path in callers:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "take_screenshot"
        ]
        assert calls, path
        assert all(
            any(keyword.arg == "validator" for keyword in call.keywords)
            for call in calls
        ), path
