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


def test_all_take_screenshot_callers_supply_ssrf_validator():
    source_root = Path(__file__).resolve().parents[1] / "src" / "plugins"
    callers = (
        source_root / "screenshot" / "screenshot.py",
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


def test_newspaper_browser_capture_keeps_network_closed_security_chain():
    source_path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "plugins"
        / "newspaper"
        / "newspaper.py"
    )
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    newspaper = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "Newspaper"
    )

    def method(name):
        return next(
            node
            for node in newspaper.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == name
        )

    def names(node):
        return {child.id for child in ast.walk(node) if isinstance(child, ast.Name)}

    def attributes(node):
        return {
            child.attr for child in ast.walk(node) if isinstance(child, ast.Attribute)
        }

    html_limit = next(
        node.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "MAX_HTML_BYTES"
            for target in node.targets
        )
    )
    assert ast.unparse(html_limit).replace(" ", "") == "2*1024*1024"

    capture = method("_fetch_url_screenshot")
    capture_names = names(capture)
    capture_attributes = attributes(capture)
    assert {
        "MAX_BROWSER_SECONDS",
        "MAX_HTML_BYTES",
        "TaskContext",
        "_remaining_timeout",
        "get_browser_renderer",
    } <= capture_names
    assert {
        "_allowed_hosts_for_url",
        "_assert_browser_html_network_closed",
        "_download_provider_bytes",
        "_sanitize_browser_html",
        "never_cancelled",
        "render_html",
    } <= capture_attributes
    assert any(
        keyword.arg == "max_bytes"
        and isinstance(keyword.value, ast.Name)
        and keyword.value.id == "MAX_HTML_BYTES"
        for call in ast.walk(capture)
        if isinstance(call, ast.Call)
        for keyword in call.keywords
    )
    assert any(
        keyword.arg == "deadline_monotonic"
        and isinstance(keyword.value, ast.Name)
        and keyword.value.id == "deadline"
        for call in ast.walk(capture)
        if isinstance(call, ast.Call)
        for keyword in call.keywords
    )

    download = method("_download_provider_bytes")
    assert {"MAX_REDIRECTS", "_PinnedResponse", "get_ssrf_policy"} <= names(
        download
    )
    assert {
        "_validate_approved_target",
        "iter_content",
        "open",
        "resolve_and_validate",
    } <= attributes(download)
    target_checks = [
        call
        for call in ast.walk(download)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr == "_validate_approved_target"
    ]
    assert len(target_checks) >= 2
    assert all(
        len(call.args) >= 2
        and isinstance(call.args[1], ast.Name)
        and call.args[1].id == "allowed_hosts"
        for call in target_checks
    )

    target_validation = method("_validate_approved_target")
    assert "ipaddress" in names(target_validation)
    assert {"is_global", "ipv4_mapped"} <= attributes(target_validation)

    pinned_response = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "_PinnedResponse"
    )
    pinned_open = next(
        node
        for node in pinned_response.body
        if isinstance(node, ast.FunctionDef) and node.name == "open"
    )
    assert {"create_connection", "wrap_socket"} <= attributes(pinned_open)
    assert {"addresses", "authority", "hostname"} <= attributes(pinned_open)

    sanitizer = method("_sanitize_browser_html")
    audit = method("_assert_browser_html_network_closed")
    assert "_NetworkClosedHTMLSanitizer" in names(sanitizer)
    assert "_NetworkClosedHTMLAudit" in names(audit)
    sanitizer_text = "".join(
        child.value
        for child in ast.walk(sanitizer)
        if isinstance(child, ast.Constant) and isinstance(child.value, str)
    )
    for directive in (
        "Content-Security-Policy",
        "default-src 'none'",
        "navigate-to 'none'",
        "form-action 'none'",
        "base-uri 'none'",
        "object-src 'none'",
    ):
        assert directive in sanitizer_text
