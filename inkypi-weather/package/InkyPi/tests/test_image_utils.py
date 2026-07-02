import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from utils import image_utils  # noqa: E402


class FakeScreenshotProcess:
    returncode = 0
    pid = 12345

    def __init__(self, command, **kwargs):
        self.command = command
        self.kwargs = kwargs
        for arg in command:
            if arg.startswith("--screenshot="):
                image_path = arg.split("=", 1)[1]
                Image.new("RGB", (8, 8), "white").save(image_path)
                break

    def communicate(self, timeout=None):
        self.timeout = timeout
        return b"", b""

    def kill(self):
        pass

    def wait(self, timeout=None):
        return self.returncode


def test_take_screenshot_reuses_configured_profile_and_limits_disk_cache(tmp_path, monkeypatch):
    profile_dir = tmp_path / "browser-profile"
    commands = []

    def fake_popen(command, **kwargs):
        commands.append(command)
        return FakeScreenshotProcess(command, **kwargs)

    monkeypatch.setenv("INKYPI_BROWSER_PROFILE_DIR", str(profile_dir))
    monkeypatch.setattr(image_utils, "_find_chromium_binary", lambda: "fake-chromium")
    monkeypatch.setattr(image_utils.subprocess, "Popen", fake_popen)

    first = image_utils.take_screenshot("file:///tmp/first.html", (800, 480))
    second = image_utils.take_screenshot("file:///tmp/second.html", (800, 480))

    assert first.size == (8, 8)
    assert second.size == (8, 8)
    assert profile_dir.is_dir()
    profile_args = [arg for command in commands for arg in command if arg.startswith("--user-data-dir=")]
    assert profile_args == [f"--user-data-dir={profile_dir}", f"--user-data-dir={profile_dir}"]
    assert all("--disk-cache-size=1" in command for command in commands)
    assert all("--media-cache-size=1" in command for command in commands)
