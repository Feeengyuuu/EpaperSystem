from src.utils import image_loader


class FakeResponse:
    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size=8192):
        yield b"123"
        yield b"456"


class FakeSession:
    def get(self, url, timeout=None, stream=False, headers=None):
        assert url == "https://example.test/large.jpg"
        assert stream is True
        return FakeResponse()


def test_from_url_rejects_oversized_download(monkeypatch):
    loader = image_loader.AdaptiveImageLoader.__new__(image_loader.AdaptiveImageLoader)
    loader.is_low_resource = False
    monkeypatch.setattr(image_loader, "get_http_session", lambda: FakeSession())

    image = loader.from_url(
        "https://example.test/large.jpg",
        (800, 480),
        max_bytes=4,
    )

    assert image is None
