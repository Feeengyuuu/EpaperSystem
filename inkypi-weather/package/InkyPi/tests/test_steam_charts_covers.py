"""Cover recovery at the metadata-to-render boundary, including cached misses."""
import sys
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import plugins.steam_charts.steam_charts as module
from plugins.steam_charts.steam_charts import SteamCharts
from runtime.refresh_contracts import TaskCancelled
from PIL import Image


@pytest.fixture(autouse=True)
def clear_metadata_caches():
    def clear():
        for name in ("_fetch_store_appdetails", "_fetch_store_appdetails_cached", "_fetch_store_page_images"):
            method = getattr(SteamCharts, name, None)
            if hasattr(method, "cache_clear"):
                method.cache_clear()
    clear()
    yield
    clear()


def test_live_missing_cover_recovers_from_identity_checked_store_page(monkeypatch):
    """The live API returned a different AppID; the legacy capsule returns 404."""
    url = "https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/1867240/hash/header.jpg"
    requests = []

    class Client:
        def request_json(self, *args, **kwargs):
            return SimpleNamespace(data={"4840670": {"success": True, "data": {"name": "Wrong game"}}})

        def request_text(self, method, source, **kwargs):
            assert "/app/1867240/" in source
            return SimpleNamespace(data=f'<img class="game_header_image_full" src="{url}">')

    monkeypatch.setattr(module, "get_http_client", Client)
    plugin = SteamCharts({"id": "steam_charts"})

    def image(source):
        requests.append(source)
        return "data:image/png;base64,valid-cover" if source == url else ""

    monkeypatch.setattr(plugin, "_image_url_to_data_uri", image)
    games = [{"app_id": 1867240, "name": "WARDOGS"}]
    plugin._apply_store_metadata(games)
    assert games[0]["image"].startswith("data:image/"), requests
    assert games[0]["name"] == "WARDOGS"
    assert requests == [url]


@pytest.mark.parametrize("failure", ["empty", "exception"])
def test_cover_tries_next_candidate_after_download_or_negative_cache_miss(monkeypatch, failure):
    plugin = SteamCharts({"id": "steam_charts"})
    monkeypatch.setattr(plugin, "_fetch_store_appdetails", lambda app, language: {
        "name": "Bongo Cat", "capsule_image": "https://example.test/broken.jpg",
        "header_image": "https://example.test/header.jpg",
    })
    calls = []

    def image(url):
        calls.append(url)
        if url.endswith("broken.jpg"):
            if failure == "exception":
                raise RuntimeError("HTTP 404")
            return ""
        return "data:image/png;base64,valid-cover"

    monkeypatch.setattr(plugin, "_image_url_to_data_uri", image)
    games = [{"app_id": 3419430, "name": "Bongo Cat"}]
    plugin._apply_store_metadata(games)
    assert games[0]["image"].startswith("data:image/"), calls
    assert calls == ["https://example.test/broken.jpg", "https://example.test/header.jpg"]


@pytest.mark.parametrize("first", ["wrong_id", "unsuccessful", "transport", "wrong_data_id"])
def test_store_metadata_failure_is_retried_on_next_refresh(monkeypatch, first):
    calls = []

    class Client:
        def request_json(self, *args, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                if first == "transport":
                    raise RuntimeError("connection reset")
                if first == "wrong_id":
                    return SimpleNamespace(data={"4840670": {"success": True, "data": {"name": "Wrong"}}})
                if first == "wrong_data_id":
                    return SimpleNamespace(data={"1867240": {"success": True, "data": {"steam_appid": 4840670, "name": "Wrong"}}})
                return SimpleNamespace(data={"1867240": {"success": False}})
            return SimpleNamespace(data={"1867240": {"success": True, "data": {"steam_appid": 1867240, "name": "WARDOGS"}}})

    monkeypatch.setattr(module, "get_http_client", Client)
    assert SteamCharts._fetch_store_appdetails(1867240, "english") == {}
    assert SteamCharts._fetch_store_appdetails(1867240, "english")["name"] == "WARDOGS"
    assert SteamCharts._fetch_store_appdetails(1867240, "english")["name"] == "WARDOGS"
    assert len(calls) == 2


def test_successful_metadata_expires_and_refreshes_asset_urls(monkeypatch):
    now = [100.0]
    calls = []
    monkeypatch.setattr(module.time, "monotonic", lambda: now[0])

    class Client:
        def request_json(self, *args, **kwargs):
            calls.append(1)
            return SimpleNamespace(data={"1867240": {"success": True, "data": {"name": "WARDOGS", "header_image": str(len(calls))}}})

    monkeypatch.setattr(module, "get_http_client", Client)
    assert SteamCharts._fetch_store_appdetails(1867240, "english")["header_image"] == "1"
    now[0] += 24 * 3600
    assert SteamCharts._fetch_store_appdetails(1867240, "english")["header_image"] == "2"


@pytest.mark.parametrize("image_url", [
    "https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/730/header.jpg",
    "https://untrusted.example/steam/apps/1867240/header.jpg",
])
def test_store_page_never_uses_another_games_or_untrusted_image(monkeypatch, image_url):
    class Client:
        def request_text(self, *args, **kwargs):
            return SimpleNamespace(data=f'<meta property="og:image" content="{image_url}">')

    monkeypatch.setattr(module, "get_http_client", Client)
    with pytest.raises(ValueError, match="cover"):
        SteamCharts._fetch_store_page_images("1867240", 0)


def test_store_page_supports_og_image_and_decodes_query_entities(monkeypatch):
    class Client:
        def request_text(self, *args, **kwargs):
            return SimpleNamespace(data='<meta property="og:image" content="https://shared.fastly.steamstatic.com/store_item_assets/steam/apps/3419430/hash/capsule.jpg?t=1&amp;lang=en">')

    monkeypatch.setattr(module, "get_http_client", Client)
    urls = SteamCharts._fetch_store_page_images("3419430", 0)
    assert len(urls) == 1
    assert parse_qs(urlsplit(urls[0]).query) == {"t": ["1"], "lang": ["en"]}


def test_cover_cancellation_is_not_swallowed(monkeypatch):
    plugin = SteamCharts({"id": "steam_charts"})
    monkeypatch.setattr(plugin, "_fetch_store_appdetails", lambda *args: {"capsule_image": "https://example.test/cover.jpg"})

    def image(url):
        raise TaskCancelled("cancelled")

    monkeypatch.setattr(plugin, "_image_url_to_data_uri", image)
    with pytest.raises(TaskCancelled):
        plugin._apply_store_metadata([{"app_id": 1867240, "name": "WARDOGS"}])


def test_disk_negative_cache_does_not_hide_cached_fallback_on_next_refresh(monkeypatch, tmp_path):
    plugin = SteamCharts({"id": "steam_charts"})
    monkeypatch.setattr(plugin, "cache_dir", lambda **kwargs: tmp_path)
    monkeypatch.setattr(plugin, "_fetch_store_appdetails", lambda *args: {
        "name": "Bongo Cat", "capsule_image": "https://example.test/broken.jpg",
        "header_image": "https://example.test/header.jpg",
    })
    output = BytesIO()
    Image.new("RGB", (64, 32), "red").save(output, format="PNG")
    calls = []

    class Client:
        def request_bytes(self, method, url, **kwargs):
            calls.append(url)
            if url.endswith("broken.jpg"):
                raise RuntimeError("HTTP 404")
            return SimpleNamespace(data=output.getvalue())

    monkeypatch.setattr(module, "get_http_client", Client)
    for _ in range(2):
        games = [{"app_id": 3419430, "name": "Bongo Cat"}]
        plugin._apply_store_metadata(games)
        assert games[0]["image"].startswith("data:image/png;base64,")
    assert calls == ["https://example.test/broken.jpg", "https://example.test/header.jpg"]


def test_all_cover_candidates_unavailable_leaves_no_browser_network_url(monkeypatch):
    plugin = SteamCharts({"id": "steam_charts"})
    monkeypatch.setattr(plugin, "_fetch_store_appdetails", lambda *args: {})
    monkeypatch.setattr(plugin, "_fetch_store_page_images", lambda *args: ())
    monkeypatch.setattr(plugin, "_image_url_to_data_uri", lambda *args: "")
    games = [{"app_id": 1867240, "name": "WARDOGS", "image": "https://example.test/remote.jpg"}]
    plugin._apply_store_metadata(games)
    assert games[0]["image"] == ""


def test_disabled_covers_do_not_fetch_store_page(monkeypatch):
    plugin = SteamCharts({"id": "steam_charts"})
    monkeypatch.setattr(plugin, "_fetch_store_appdetails", lambda *args: {})

    def unexpected(*args):
        raise AssertionError("disabled covers must not fetch images")

    monkeypatch.setattr(plugin, "_fetch_store_page_images", unexpected)
    monkeypatch.setattr(plugin, "_image_url_to_data_uri", unexpected)
    plugin._apply_store_metadata([{"app_id": 1867240, "name": "WARDOGS"}], include_images=False)
