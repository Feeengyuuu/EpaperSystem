from io import BytesIO
from types import SimpleNamespace

from PIL import Image

from plugins.game_deals.media import load_covers
from plugins.game_deals.source import normalize_deals


def row():
    return {"title": "Ori and the Will of the Wisps", "dealID": "abc%2Bdef%3D",
            "gameID": "209143", "storeID": "1", "salePrice": "2.99",
            "normalPrice": "29.99", "thumb": "https://shared.fastly.steamstatic.com/example.jpg"}


class ImagesHTTP:
    def __init__(self):
        self.calls = []

    def request_bytes(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        stream = BytesIO()
        Image.new("RGB", (462, 174), "blue").save(stream, format="PNG")
        return SimpleNamespace(data=stream.getvalue())


def test_media_downsizes_persists_and_cached_display_never_fetches_or_writes(tmp_path):
    http = ImagesHTTP()
    deals = normalize_deals([row()])
    covers = load_covers(deals, tmp_path / "covers", http=http)
    image = covers["209143"]
    assert image.width <= 320 and image.height <= 180
    image.close()
    paths = list((tmp_path / "covers").glob("*.png"))
    assert len(paths) == 1
    old = (paths[0].read_bytes(), paths[0].stat().st_mtime_ns)
    covers = load_covers(deals, tmp_path / "covers", http=http, cached_only=True)
    covers["209143"].close()
    assert len(http.calls) == 1
    assert (paths[0].read_bytes(), paths[0].stat().st_mtime_ns) == old
    assert http.calls[0][1]["allow_redirects"] is False
    assert http.calls[0][1]["max_bytes"] == 512 * 1024


def test_cache_only_missing_cover_does_not_create_directory(tmp_path):
    http = ImagesHTTP()
    target = tmp_path / "missing"
    assert load_covers(normalize_deals([row()]), target, http=http, cached_only=True) == {}
    assert http.calls == []
    assert not target.exists()


def test_untrusted_image_host_is_never_contacted(tmp_path):
    http = ImagesHTTP()
    deal = normalize_deals([row()])[0]
    deal["thumbnail_url"] = "https://127.0.0.1/private"
    assert load_covers([deal], tmp_path, http=http) == {}
    assert http.calls == []
