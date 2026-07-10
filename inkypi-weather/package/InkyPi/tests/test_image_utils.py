import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from utils import image_utils  # noqa: E402


class RecordingRenderer:
    def __init__(self):
        self.calls = []

    def render_html(self, html, **kwargs):
        self.calls.append(("html", html, kwargs))
        return Image.new("RGB", (8, 8), "white")

    def render_url(self, url, **kwargs):
        self.calls.append(("url", url, kwargs))
        if kwargs.get("validator") is None:
            return None
        return Image.new("RGB", (8, 8), "white")


def test_take_screenshot_html_delegates_to_bounded_renderer(monkeypatch):
    renderer = RecordingRenderer()
    monkeypatch.setattr(image_utils, "get_browser_renderer", lambda: renderer)

    result = image_utils.take_screenshot_html(
        "<p>hello</p>",
        (800, 480),
        timeout_ms=1000,
        timezone_name="UTC",
    )

    assert result.size == (8, 8)
    kind, html, kwargs = renderer.calls[0]
    assert kind == "html"
    assert html == "<p>hello</p>"
    assert kwargs["viewport"] == (800, 480)
    assert kwargs["timezone_name"] == "UTC"


def test_take_screenshot_remote_url_fails_closed_without_validator(monkeypatch):
    renderer = RecordingRenderer()
    monkeypatch.setattr(image_utils, "get_browser_renderer", lambda: renderer)

    assert image_utils.take_screenshot(
        "https://example.test/page",
        (800, 480),
    ) is None

    assert renderer.calls[0][0] == "url"
    assert renderer.calls[0][2]["validator"] is None


def test_take_screenshot_local_file_uses_html_entrypoint(tmp_path, monkeypatch):
    renderer = RecordingRenderer()
    monkeypatch.setattr(image_utils, "get_browser_renderer", lambda: renderer)
    html_path = tmp_path / "page.html"
    html_path.write_text("<p>local</p>", encoding="utf-8")

    result = image_utils.take_screenshot(str(html_path), (800, 480))

    assert result.size == (8, 8)
    assert renderer.calls[0][0:2] == ("html", "<p>local</p>")
