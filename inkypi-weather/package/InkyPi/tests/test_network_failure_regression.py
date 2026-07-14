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


def test_apod_http_500_fails_fast_without_logging_api_key(monkeypatch, caplog):
    calls = []
    api_key = "nasa-super-secret"

    class Session:
        def get(self, url, params=None, timeout=None):
            calls.append({"url": url, "params": params, "timeout": timeout})
            return FakeResponse(
                status_code=500,
                text=f"server echoed api_key={api_key}",
            )

    monkeypatch.setattr(apod_module, "get_http_session", lambda: Session())

    with pytest.raises(RuntimeError, match="Failed to retrieve NASA APOD"):
        Apod({"id": "apod"}).generate_image(
            {},
            FakeDeviceConfig({"NASA_SECRET": api_key}),
        )

    assert calls == [{
        "url": "https://api.nasa.gov/planetary/apod",
        "params": {"api_key": api_key},
        "timeout": 10,
    }]
    assert api_key not in caplog.text


def test_apod_random_mode_retries_a_different_date_until_image(monkeypatch):
    api_calls = []
    loaded_image = object()

    class Session:
        responses = [
            FakeResponse(
                json_data={
                    "date": "2024-05-07",
                    "media_type": "video",
                    "title": "A video APOD",
                }
            ),
            FakeResponse(
                json_data={
                    "date": "2024-05-08",
                    "hdurl": "https://images.example/apod.jpg",
                    "media_type": "image",
                    "title": "An image APOD",
                }
            ),
        ]

        def get(self, url, params=None, timeout=None):
            api_calls.append({
                "url": url,
                "params": dict(params or {}),
                "timeout": timeout,
            })
            return self.responses.pop(0)

    class ImageLoader:
        def __init__(self):
            self.calls = []

        def from_url(self, url, dimensions, timeout_ms=None):
            self.calls.append((url, dimensions, timeout_ms))
            return loaded_image

    monkeypatch.setattr(apod_module, "get_http_session", lambda: Session())
    monkeypatch.setattr(apod_module, "randint", lambda _start, _end: 0)

    plugin = Apod({"id": "apod"})
    plugin.image_loader = ImageLoader()
    monkeypatch.setattr(plugin, "_overlay_nasa_logo", lambda image: image)
    monkeypatch.setattr(plugin, "_write_apod_context", lambda *_args: None)

    result = plugin.generate_image(
        {"randomizeApod": "true"},
        FakeDeviceConfig({"NASA_SECRET": "nasa-key"}),
    )

    assert result is loaded_image
    assert len(api_calls) == 2
    assert api_calls[0]["params"]["date"] != api_calls[1]["params"]["date"]
    assert all(call["params"]["api_key"] == "nasa-key" for call in api_calls)
    assert plugin.image_loader.calls == [
        ("https://images.example/apod.jpg", (800, 480), 40000),
    ]


def test_apod_random_mode_retries_when_first_image_cannot_be_loaded(monkeypatch):
    api_calls = []
    loaded_image = object()

    class Session:
        responses = [
            FakeResponse(
                json_data={
                    "date": "2024-05-07",
                    "hdurl": "https://images.example/oversized.jpg",
                    "media_type": "image",
                    "title": "An oversized image APOD",
                }
            ),
            FakeResponse(
                json_data={
                    "date": "2024-05-08",
                    "hdurl": "https://images.example/usable.jpg",
                    "media_type": "image",
                    "title": "A usable image APOD",
                }
            ),
        ]

        def get(self, url, params=None, timeout=None):
            api_calls.append(
                {
                    "url": url,
                    "params": dict(params or {}),
                    "timeout": timeout,
                }
            )
            return self.responses.pop(0)

    class ImageLoader:
        def __init__(self):
            self.calls = []

        def from_url(self, url, dimensions, timeout_ms=None):
            self.calls.append((url, dimensions, timeout_ms))
            if url.endswith("oversized.jpg"):
                return None
            return loaded_image

    monkeypatch.setattr(apod_module, "get_http_session", lambda: Session())
    monkeypatch.setattr(apod_module, "randint", lambda _start, _end: 0)

    plugin = Apod({"id": "apod"})
    plugin.image_loader = ImageLoader()
    monkeypatch.setattr(plugin, "_overlay_nasa_logo", lambda image: image)
    monkeypatch.setattr(plugin, "_write_apod_context", lambda *_args: None)

    result = plugin.generate_image(
        {"randomizeApod": "true"},
        FakeDeviceConfig({"NASA_SECRET": "nasa-key"}),
    )

    assert result is loaded_image
    assert len(api_calls) == 2
    assert api_calls[0]["params"]["date"] != api_calls[1]["params"]["date"]
    assert plugin.image_loader.calls == [
        ("https://images.example/oversized.jpg", (800, 480), 40000),
        ("https://images.example/usable.jpg", (800, 480), 40000),
    ]


def test_apod_random_mode_stops_after_five_unique_non_image_dates(monkeypatch):
    api_calls = []

    class Session:
        def get(self, url, params=None, timeout=None):
            api_calls.append({
                "url": url,
                "params": dict(params or {}),
                "timeout": timeout,
            })
            return FakeResponse(
                json_data={
                    "date": params["date"],
                    "media_type": "video",
                    "title": "A video APOD",
                }
            )

    monkeypatch.setattr(apod_module, "get_http_session", lambda: Session())
    monkeypatch.setattr(apod_module, "randint", lambda _start, _end: 0)

    with pytest.raises(
        RuntimeError,
        match="No usable APOD image found after 5 random dates",
    ):
        Apod({"id": "apod"}).generate_image(
            {"randomizeApod": "true"},
            FakeDeviceConfig({"NASA_SECRET": "nasa-key"}),
        )

    assert len(api_calls) == 5
    assert len({call["params"]["date"] for call in api_calls}) == 5


@pytest.mark.parametrize(
    ("settings", "expected_params"),
    [
        ({}, {"api_key": "nasa-key"}),
        (
            {"customDate": "2024-05-07"},
            {"api_key": "nasa-key", "date": "2024-05-07"},
        ),
    ],
)
def test_apod_non_random_mode_fails_once_for_non_image_media(
    monkeypatch,
    settings,
    expected_params,
):
    api_calls = []

    class Session:
        def get(self, url, params=None, timeout=None):
            api_calls.append({
                "url": url,
                "params": dict(params or {}),
                "timeout": timeout,
            })
            return FakeResponse(
                json_data={
                    "date": "2024-05-07",
                    "media_type": "video",
                    "title": "A video APOD",
                }
            )

    monkeypatch.setattr(apod_module, "get_http_session", lambda: Session())

    with pytest.raises(RuntimeError, match="APOD is not an image"):
        Apod({"id": "apod"}).generate_image(
            settings,
            FakeDeviceConfig({"NASA_SECRET": "nasa-key"}),
        )

    assert len(api_calls) == 1
    assert api_calls[0]["params"] == expected_params


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
