import sys
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.ai_image import ai_image as ai_image_module  # noqa: E402
from plugins.ai_image.ai_image import AIImage  # noqa: E402
from plugins.apod import apod as apod_module  # noqa: E402
from plugins.apod.apod import Apod  # noqa: E402
from plugins.image_album import image_album as image_album_module  # noqa: E402
from plugins.image_album.image_album import IMMICH_REQUEST_TIMEOUT_SECONDS, ImageAlbum, ImmichProvider  # noqa: E402
from plugins.unsplash import unsplash as unsplash_module  # noqa: E402
from plugins.unsplash.unsplash import Unsplash  # noqa: E402


class FakeDeviceConfig:
    def __init__(self, env=None):
        self.env = env or {}

    def load_env_key(self, key):
        return self.env.get(key, "")

    def get_config(self, key=None, default=None):
        values = {
            "orientation": "horizontal",
            "resolution": "800x480",
            "width": 800,
            "height": 480,
        }
        if key is None:
            return values
        return values.get(key, default)

    def get_resolution(self):
        return (800, 480)


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, content=b"", text=""):
        self.status_code = status_code
        self._json_data = json_data
        self.content = content
        self.text = text

    def json(self):
        return self._json_data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"HTTP {self.status_code}")



def test_ai_image_download_uses_shared_session_and_http_errors(monkeypatch):
    calls = []

    class Session:
        def get(self, url, timeout=None, stream=False):
            calls.append({"url": url, "timeout": timeout, "stream": stream})
            return FakeResponse(status_code=500, text="server error")

    class Images:
        def generate(self, **_kwargs):
            item = type("ImageItem", (), {"url": "https://example.test/generated.png"})()
            return type("ImageResponse", (), {"data": [item]})()

    monkeypatch.setattr(ai_image_module, "get_http_session", lambda: Session())

    plugin = AIImage({"id": "ai_image"})

    with pytest.raises(requests.exceptions.HTTPError):
        plugin.fetch_image(type("Client", (), {"images": Images()})(), "prompt")

    assert calls == [
        {
            "url": "https://example.test/generated.png",
            "timeout": None,
            "stream": True,
        }
    ]


def test_apod_http_500_fails_fast_with_timeout(monkeypatch):
    calls = []

    class Session:
        def get(self, url, params=None, timeout=None):
            calls.append({"url": url, "params": params, "timeout": timeout})
            return FakeResponse(status_code=500, text="server error")

    monkeypatch.setattr(apod_module, "get_http_session", lambda: Session())

    with pytest.raises(RuntimeError, match="Failed to retrieve NASA APOD"):
        Apod({"id": "apod"}).generate_image({}, FakeDeviceConfig({"NASA_SECRET": "nasa-key"}))

    assert calls == [{
        "url": "https://api.nasa.gov/planetary/apod",
        "params": {"api_key": "nasa-key"},
        "timeout": 10,
    }]


def test_unsplash_missing_api_key_fails_before_network(monkeypatch):
    monkeypatch.setattr(
        unsplash_module,
        "get_http_session",
        lambda: pytest.fail("Unsplash should not create an HTTP session without an API key"),
    )

    with pytest.raises(RuntimeError, match="Unsplash Access Key"):
        Unsplash({"id": "unsplash"}).generate_image({}, FakeDeviceConfig())


def test_unsplash_request_timeout_is_wrapped(monkeypatch):
    calls = []

    class Session:
        def get(self, url, params=None, timeout=None):
            calls.append({"url": url, "params": dict(params or {}), "timeout": timeout})
            raise requests.exceptions.Timeout("slow response")

    monkeypatch.setattr(unsplash_module, "get_http_session", lambda: Session())

    with pytest.raises(RuntimeError, match="Failed to fetch image from Unsplash API"):
        Unsplash({"id": "unsplash"}).generate_image(
            {"search_query": "mountains"},
            FakeDeviceConfig({"UNSPLASH_ACCESS_KEY": "unsplash-key"}),
        )

    assert calls[0]["url"] == "https://api.unsplash.com/search/photos"
    assert calls[0]["params"]["client_id"] == "unsplash-key"
    assert calls[0]["params"]["query"] == "mountains"
    assert calls[0]["timeout"] == 15


def test_immich_metadata_requests_use_timeout(monkeypatch):
    calls = []

    class Session:
        def get(self, url, headers=None, timeout=None):
            calls.append(("get", url, headers, timeout))
            return FakeResponse(json_data=[{"albumName": "Frame", "id": "album-1"}])

        def post(self, url, json=None, headers=None, timeout=None):
            calls.append(("post", url, json, headers, timeout))
            items = [{"id": "asset-1"}] if json["page"] == 1 else []
            return FakeResponse(json_data={"assets": {"items": items}})

    monkeypatch.setattr(image_album_module, "get_http_session", lambda: Session())

    provider = ImmichProvider("https://immich.example", "immich-key", object())

    assert provider.get_album_id("Frame") == "album-1"
    assert provider.get_assets("album-1") == [{"id": "asset-1"}]
    assert calls[0] == (
        "get",
        "https://immich.example/api/albums",
        {"x-api-key": "immich-key"},
        IMMICH_REQUEST_TIMEOUT_SECONDS,
    )
    assert calls[1][0] == "post"
    assert calls[1][1] == "https://immich.example/api/search/metadata"
    assert calls[1][4] == IMMICH_REQUEST_TIMEOUT_SECONDS


def test_image_album_missing_api_key_fails_before_provider():
    settings = {"albumProvider": "Immich", "url": "https://immich.example", "album": "Frame"}

    with pytest.raises(RuntimeError, match="Immich API Key not configured"):
        ImageAlbum({"id": "image_album"}).generate_image(settings, FakeDeviceConfig())

