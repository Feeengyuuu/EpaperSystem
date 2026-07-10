from io import BytesIO
import tempfile

import pytest
from PIL import Image

from src.utils import image_loader


def _png_bytes(size=(4, 3)):
    output = BytesIO()
    Image.new("RGB", size, "red").save(output, format="PNG")
    return output.getvalue()


class FakeResponse:
    def __init__(self, payload=(b"123", b"456")):
        self.payload = payload
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.closed = True

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size=8192):
        yield from self.payload


class FakeSession:
    def __init__(self, response=None):
        self.response = response or FakeResponse()

    def get(self, url, timeout=None, stream=False, headers=None):
        assert url.startswith("https://example.test/")
        assert stream is True
        return self.response


@pytest.mark.parametrize("configured_limit", [0, -1])
def test_non_positive_download_limit_falls_back_to_safe_default(configured_limit):
    loader = image_loader.AdaptiveImageLoader.__new__(image_loader.AdaptiveImageLoader)

    assert loader._max_download_bytes(configured_limit) == image_loader.DEFAULT_MAX_IMAGE_DOWNLOAD_BYTES


def test_from_url_rejects_oversized_download(monkeypatch):
    loader = image_loader.AdaptiveImageLoader.__new__(image_loader.AdaptiveImageLoader)
    loader.is_low_resource = False
    response = FakeResponse()
    monkeypatch.setattr(image_loader, "get_http_session", lambda: FakeSession(response))

    image = loader.from_url(
        "https://example.test/large.jpg",
        (800, 480),
        max_bytes=4,
    )

    assert image is None
    assert response.closed is True


@pytest.mark.parametrize("low_resource", [False, True])
def test_from_url_uses_one_spooled_file_and_closes_response(monkeypatch, low_resource):
    loader = image_loader.AdaptiveImageLoader.__new__(image_loader.AdaptiveImageLoader)
    loader.is_low_resource = low_resource
    payload = _png_bytes()
    response = FakeResponse((payload[:10], payload[10:]))
    monkeypatch.setattr(image_loader, "get_http_session", lambda: FakeSession(response))
    real_spooled_file = tempfile.SpooledTemporaryFile
    spool_calls = []

    def recording_spooled_file(*args, **kwargs):
        spool_calls.append((args, kwargs))
        return real_spooled_file(*args, **kwargs)

    decoded = []

    def fake_safe_open(source, **_kwargs):
        decoded.append(source.read())
        return Image.new("RGB", (4, 3), "red")

    monkeypatch.setattr(image_loader.tempfile, "SpooledTemporaryFile", recording_spooled_file)
    monkeypatch.setattr(image_loader, "safe_open_image", fake_safe_open, raising=False)

    result = loader.from_url(
        "https://example.test/image.png",
        (800, 480),
        resize=False,
    )

    assert result.size == (4, 3)
    assert decoded == [payload]
    assert len(spool_calls) == 1
    assert response.closed is True


@pytest.mark.parametrize("low_resource", [False, True])
def test_from_file_delegates_decode_to_safe_open(monkeypatch, tmp_path, low_resource):
    loader = image_loader.AdaptiveImageLoader.__new__(image_loader.AdaptiveImageLoader)
    loader.is_low_resource = low_resource
    path = tmp_path / "source.png"
    path.write_bytes(_png_bytes())
    decoded = []

    def fake_safe_open(source, **_kwargs):
        decoded.append(source)
        return Image.new("RGB", (4, 3), "red")

    monkeypatch.setattr(image_loader, "safe_open_image", fake_safe_open, raising=False)

    result = loader.from_file(path, (800, 480), resize=False)

    assert result.size == (4, 3)
    assert decoded == [path]


def test_low_resource_file_decode_requests_jpeg_draft(monkeypatch, tmp_path):
    loader = image_loader.AdaptiveImageLoader.__new__(image_loader.AdaptiveImageLoader)
    loader.is_low_resource = True
    path = tmp_path / "source.jpg"
    Image.new("RGB", (64, 48), "red").save(path, format="JPEG")
    decode_kwargs = []

    def fake_safe_open(_source, **kwargs):
        decode_kwargs.append(kwargs)
        return Image.new("RGB", (64, 48), "red")

    monkeypatch.setattr(image_loader, "safe_open_image", fake_safe_open)

    result = loader.from_file(path, (20, 10), resize=True)

    assert result.size == (20, 10)
    assert decode_kwargs == [{"limits": image_loader.ImageLimits(), "draft_size": (40, 20)}]


def test_from_bytesio_delegates_decode_to_safe_open(monkeypatch):
    loader = image_loader.AdaptiveImageLoader.__new__(image_loader.AdaptiveImageLoader)
    loader.is_low_resource = False
    source = BytesIO(_png_bytes())
    decoded = []

    def fake_safe_open(value, **_kwargs):
        decoded.append(value)
        return Image.new("RGB", (4, 3), "red")

    monkeypatch.setattr(image_loader, "safe_open_image", fake_safe_open, raising=False)

    result = loader.from_bytesio(source, (800, 480), resize=False)

    assert result.size == (4, 3)
    assert decoded == [source]
