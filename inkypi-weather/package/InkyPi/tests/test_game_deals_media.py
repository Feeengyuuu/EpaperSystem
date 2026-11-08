from io import BytesIO
from types import SimpleNamespace

from PIL import Image

from plugins.game_deals.media import STEAM_HEADER, load_covers, load_hero_cover
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


def test_every_one_of_ten_offers_gets_its_cover(tmp_path):
    http = ImagesHTTP()
    rows = []
    for number in range(10):
        value = row()
        value.update(gameID=str(number), title=f"Game {number}",
                     thumb=f"https://shared.fastly.steamstatic.com/{number}.jpg")
        rows.append(value)
    covers = load_covers(normalize_deals(rows), tmp_path / "covers", http=http)
    assert sorted(covers, key=int) == [str(number) for number in range(10)]
    for image in covers.values():
        image.close()


def test_cache_only_missing_cover_does_not_create_directory(tmp_path):
    http = ImagesHTTP()
    target = tmp_path / "missing"
    assert load_covers(normalize_deals([row()]), target, http=http, cached_only=True) == {}
    assert http.calls == []
    assert not target.exists()


def test_hero_takes_its_steam_store_header_once_and_cache_only_never_fetches(tmp_path):
    http = ImagesHTTP()
    deal = dict(normalize_deals([row()])[0], steam_app_id="49520")
    cover = load_hero_cover(deal, tmp_path / "covers", http=http)
    assert cover is not None and cover.width <= 460 and cover.height <= 215
    cover.close()
    assert http.calls[0][0] == ("GET", STEAM_HEADER.format(app_id="49520"))
    again = load_hero_cover(deal, tmp_path / "covers", http=http, cached_only=True)
    again.close()
    assert len(http.calls) == 1
    # Without a Steam app id there is no header to ask for.
    assert load_hero_cover(dict(deal, steam_app_id=""), tmp_path / "covers", http=http) is None
    assert len(http.calls) == 1


def test_untrusted_image_host_is_never_contacted(tmp_path):
    http = ImagesHTTP()
    deal = normalize_deals([row()])[0]
    deal["thumbnail_url"] = "https://127.0.0.1/private"
    assert load_covers([deal], tmp_path, http=http) == {}
    assert http.calls == []
