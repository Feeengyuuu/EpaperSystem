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


def _offers(*names):
    rows = []
    for number, name in enumerate(names):
        value = row()
        value.update(gameID=str(1000 + number), title=name, steamAppID=str(2000 + number),
                     thumb=f"https://shared.fastly.steamstatic.com/{name}.jpg")
        rows.append(value)
    return normalize_deals(rows)


def test_a_cover_still_on_the_page_outlives_newer_ones_in_the_bounded_cache(tmp_path, monkeypatch):
    """On the device the long-running offers lost their covers: read straight from disk, they were
    never seen in use, stayed the cache's oldest files and were the first ones dropped, so the next
    cache-only redraw drew STEAM placeholders for them."""
    import itertools

    from plugins.game_deals import media
    from utils.cache_manager import CacheBudget, cache_namespace_for_directory

    budget = CacheBudget(max_age_seconds=86400, max_files=2, max_bytes=1024 * 1024)
    monkeypatch.setattr(media, "BUDGET", budget)
    directory = tmp_path / "covers"
    clock = itertools.count(1_000_000)
    cache_namespace_for_directory(directory, budget).manager._clock = lambda: float(next(clock))
    http = ImagesHTTP()
    (lead, old), (_lead, new) = _offers("lead", "old"), _offers("lead", "new")
    old = dict(old, game_id="1001")
    new = dict(new, game_id="1002")

    for image in load_covers([lead, old], directory, http=http).values():
        image.close()
    # The next refresh lists the lead again beside a new offer: the offer that left makes room.
    for image in load_covers([lead, new], directory, http=http).values():
        image.close()
    assert [call[0][1].rsplit("/", 1)[1] for call in http.calls] == ["lead.jpg", "old.jpg", "new.jpg"]
    covers = load_covers([lead, new], directory, http=http, cached_only=True)
    assert set(covers) == {lead["game_id"], new["game_id"]}
    for image in covers.values():
        image.close()


def test_a_refresh_marks_kept_covers_as_used_and_a_redraw_marks_nothing(tmp_path, monkeypatch):
    """The device's disk is mounted noatime, so reading a cover never shows as a use: a refresh has
    to tell the cache about every kept cover it draws, the listed offers' store headers included."""
    from utils.cache_manager import CacheNamespace

    directory = tmp_path / "covers"
    http = ImagesHTTP()
    deals = _offers("lead", "second")
    for image in load_covers(deals, directory, http=http).values():
        image.close()
    load_hero_cover(deals[0], directory, http=http).close()
    names = {path.name for path in directory.glob("*.png")}
    assert len(names) == 3 and len(http.calls) == 3
    marked = []
    original = CacheNamespace.get_path

    def get_path(namespace, key, **kwargs):
        marked.append(key)
        return original(namespace, key, **kwargs)

    monkeypatch.setattr(CacheNamespace, "get_path", get_path)
    for image in load_covers(deals, directory, http=http, cached_only=True).values():
        image.close()
    load_hero_cover(deals[0], directory, http=http, cached_only=True).close()
    assert marked == []

    for image in load_covers(deals, directory, http=http).values():
        image.close()
    # The two covers, and the header of an offer still listed, before anything new is stored.
    assert set(marked) == names and len(marked) == 3
    marked.clear()
    load_hero_cover(deals[0], directory, http=http).close()
    assert len(marked) == 1 and marked[0].startswith("hero-v1-")
    assert len(http.calls) == 3


def test_a_cover_that_cannot_be_fetched_is_logged_and_the_offer_stays(tmp_path, caplog):
    class Failing:
        def request_bytes(self, *args, **kwargs):
            raise OSError("name resolution failed")

    with caplog.at_level("INFO", logger="plugins.game_deals.media"):
        assert load_covers(_offers("lead"), tmp_path / "covers", http=Failing()) == {}
    assert "lead.jpg" in caplog.text and "OSError" in caplog.text
