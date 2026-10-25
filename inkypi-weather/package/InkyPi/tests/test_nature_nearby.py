import sys
import time
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.base_plugin.render_provenance import SourceProvenance, read_source_provenance  # noqa: E402
from plugins.nature_nearby import sources  # noqa: E402
from plugins.nature_nearby.nature_nearby import NatureNearby  # noqa: E402
from plugins.nature_nearby.render import render_page  # noqa: E402
from runtime.refresh_contracts import TaskContext  # noqa: E402
from utils.remote_thumbnails import load_thumbnails  # noqa: E402

NOW = datetime(2026, 10, 21, 14, 0, tzinfo=sources.PACIFIC)
PHOTO = "https://inaturalist-open-data.s3.amazonaws.com/photos/753861248/square.jpg"


class Device:
    def get_resolution(self):
        return (800, 480)

    def get_config(self, key=None, default=None):
        values = {"orientation": "horizontal", "timezone": "America/Los_Angeles"}
        return values if key is None else values.get(key, default)

    def load_env_key(self, _key):
        return None


def test_places_photos_and_hosts_normalize():
    assert sources.short_place("Grand Blvd, San Jose, CA, US") == "San Jose"
    assert sources.short_place("Santa Clara, CA 95050, USA") == "Santa Clara"
    assert sources.photo_url(PHOTO) == PHOTO.replace("/square.", "/medium.")
    assert sources.photo_url("https://evil.example/photos/1/square.jpg") is None
    assert sources.group_info("Aves") == ("鸟类", "bird", "blue")
    assert sources.group_info("Chromista")[0] == "其他"


def test_recent_observations_keep_photo_name_distance_and_observer():
    data = sources.normalize_recent({"results": [
        {"id": 1, "time_observed_at": "2026-10-21T13:20:43-07:00", "place_guess": "Grand Blvd, San Jose, CA, US",
         "location": "37.4326,-121.9624", "user": {"login": "abbyvb56"},
         "photos": [{"url": PHOTO, "attribution": "(c) abbyvb56"}],
         "taxon": {"name": "Ardea alba", "preferred_common_name": "大白鹭", "iconic_taxon_name": "Aves"}},
        {"id": 2, "observed_on": "2026-10-21", "photos": [], "taxon": {"name": "No photo"}},
    ]}, sources.DEFAULT_HOME)

    assert len(data["items"]) == 1
    item = data["items"][0]
    assert (item["name"], item["place"], item["observer"]) == ("大白鹭", "San Jose", "abbyvb56")
    assert 10 < item["distance_km"] < 16 and item["photo"].endswith("/medium.jpg")


def test_species_and_group_counts_normalize():
    species = sources.normalize_species({"total_results": 533, "results": [
        {"count": 140, "taxon": {"name": "Danaus plexippus", "preferred_common_name": "君主斑蝶",
                                 "iconic_taxon_name": "Insecta", "default_photo": {"medium_url": PHOTO}}}]})
    groups = sources.normalize_groups({"results": [
        {"count": 146, "taxon": {"name": "Aves"}}, {"count": 553, "taxon": {"name": "Plantae"}},
        {"count": 0, "taxon": {"name": "Protozoa"}}]})

    assert species["total"] == 533 and species["items"][0]["count"] == 140
    assert [group["group"] for group in groups["groups"]] == ["Plantae", "Aves"]


def test_notable_birds_keep_each_species_latest_report():
    rows = [
        {"speciesCode": "baleag", "comName": "白头海雕", "sciName": "Haliaeetus leucocephalus",
         "locName": "Coyote Hills RP--Main Marsh", "obsDt": "2026-10-20 08:10", "howMany": 1,
         "lat": 37.55, "lng": -122.09, "obsValid": True},
        {"speciesCode": "baleag", "comName": "白头海雕", "sciName": "Haliaeetus leucocephalus",
         "locName": "Lake Elizabeth", "obsDt": "2026-10-21 07:45", "howMany": 2, "lat": 37.55, "lng": -121.96},
        {"speciesCode": "tufduc", "comName": "凤头潜鸭", "sciName": "Aythya fuligula", "locName": "37.5, -122.0",
         "obsDt": "2026-10-19", "lat": 37.5, "lng": -122.0},
        {"speciesCode": "bad"},
    ]
    data = sources.normalize_notable(rows, sources.DEFAULT_HOME)

    assert data["species"] == 2
    eagle, duck = data["items"]
    assert (eagle["name"], eagle["place"], eagle["count"], eagle["confirmed"]) == ("白头海雕", "Lake Elizabeth", 2, False)
    assert eagle["distance_km"] < 5
    assert (duck["name"], duck["place"]) == ("凤头潜鸭", "")
    assert sources.ebird_place("Coyote Hills RP--Main Marsh") == "Coyote Hills RP"
    with pytest.raises(ValueError):
        sources.normalize_notable({"errors": []}, sources.DEFAULT_HOME)


def _payload():
    recent = [{"id": index, "name": name, "scientific": "Sci name", "group": group, "time": NOW.isoformat(),
               "place": "Fremont", "distance_km": 3.0, "observer": "someone",
               "photo": PHOTO.replace("/square.", "/medium.")}
              for index, (name, group) in enumerate([("大白鹭", "Aves"), ("郊狼", "Mammalia"), ("君主斑蝶", "Insecta")])]
    return {
        "recent": {"state": "live", "data": {"items": recent}},
        "species": {"state": "live", "data": {"total": 533, "items": [
            {"count": 140, "name": "君主斑蝶", "scientific": "Danaus plexippus", "group": "Insecta", "photo": None}]}},
        "groups": {"state": "live", "data": {"groups": [{"group": "Plantae", "count": 553},
                                                         {"group": "Insecta", "count": 388}]}},
        "stats": {"state": "live", "data": {"observations": 6229, "observers": 813, "top_observer": "sms273",
                                            "top_observations": 278, "top_species": 116}},
        "home": list(sources.DEFAULT_HOME), "radius_km": 40,
    }


@pytest.mark.parametrize("mode", ["day", "night"])
def test_page_renders_without_photos(mode):
    image = render_page((800, 480), _payload(), {"_inkypi_theme": {"mode": mode}}, NOW, {})

    assert image.size == (800, 480)


@pytest.mark.parametrize("mode", ["day", "night"])
def test_page_shows_notable_birds_under_the_groups(mode):
    payload = _payload()
    payload["birds"] = {"state": "live", "data": {"species": 9, "items": [
        {"name": "白头海雕", "scientific": "Haliaeetus leucocephalus", "place": "Coyote Hills RP",
         "time": NOW.isoformat(), "count": 1, "distance_km": 9.0, "confirmed": True}]}}

    with_birds = render_page((800, 480), payload, {"_inkypi_theme": {"mode": mode}}, NOW, {})
    without = render_page((800, 480), _payload(), {"_inkypi_theme": {"mode": mode}}, NOW, {})

    assert with_birds.size == (800, 480)
    assert with_birds.crop((12, 400, 300, 468)).tobytes() != without.crop((12, 400, 300, 468)).tobytes()
    assert with_birds.crop((310, 58, 788, 300)).tobytes() == without.crop((310, 58, 788, 300)).tobytes()


def test_birds_load_only_with_an_ebird_key(monkeypatch, tmp_path):
    monkeypatch.setenv("INKYPI_CACHE_DIR", str(tmp_path / "cache"))
    payload = _payload()
    for name in ("recent", "species", "groups", "stats"):
        monkeypatch.setattr(sources, f"fetch_{name}", lambda *_args, data=payload[name]["data"]: data)
    keys = []
    monkeypatch.setattr(sources, "fetch_notable",
                        lambda _home, _radius, key, _ctx: keys.append(key) or {"species": 0, "items": []})

    class KeyedDevice(Device):
        def load_env_key(self, key):
            return " ebird-test-key " if key == "EBIRD_API_KEY" else None

    plugin = NatureNearby({"id": "nature_nearby"})
    plugin.generate_image({"_inkypi_theme": {"mode": "day"}}, Device())
    assert keys == []
    plugin.generate_image({"_inkypi_theme": {"mode": "day"}, "forceRefresh": True}, KeyedDevice())
    assert keys == ["ebird-test-key"]


def test_cache_only_redraw_reuses_sources(monkeypatch, tmp_path):
    monkeypatch.setenv("INKYPI_CACHE_DIR", str(tmp_path / "cache"))
    payload = _payload()
    monkeypatch.setattr(sources, "fetch_recent", lambda *_args: payload["recent"]["data"])
    monkeypatch.setattr(sources, "fetch_species", lambda *_args: payload["species"]["data"])
    monkeypatch.setattr(sources, "fetch_groups", lambda *_args: payload["groups"]["data"])
    monkeypatch.setattr(sources, "fetch_stats", lambda *_args: payload["stats"]["data"])
    plugin = NatureNearby({"id": "nature_nearby"})

    live = plugin.generate_image({"_inkypi_theme": {"mode": "day"}}, Device())
    for name in ("fetch_recent", "fetch_species", "fetch_groups", "fetch_stats"):
        monkeypatch.setattr(sources, name, lambda *_args: pytest.fail("cache-only redraw fetched"))
    cached = plugin.generate_image({"_inkypi_theme": {"mode": "day"}, "_theme_render_only": True}, Device())

    assert read_source_provenance(live) is SourceProvenance.LIVE
    assert read_source_provenance(cached) is SourceProvenance.FRESH_CACHE


def test_thumbnails_respect_allowlist_cache_only_and_time_budget(tmp_path):
    from io import BytesIO

    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (64, 48), (0, 128, 0)).save(buffer, format="PNG")
    calls = []

    class Http:
        def request_bytes(self, _method, url, **_kwargs):
            calls.append(url)
            return type("Response", (), {"data": buffer.getvalue()})()

    context = TaskContext.never_cancelled(deadline_monotonic=time.monotonic() + 30)
    kwargs = {"http": Http(), "context": context, "safe_url": sources.safe_photo_url, "user_agent": "test"}
    first = load_thumbnails([PHOTO, "https://evil.example/x.jpg"], tmp_path, cached_only=False, **kwargs)
    cached = load_thumbnails([PHOTO], tmp_path, cached_only=True, **kwargs)
    nothing = load_thumbnails(["https://static.inaturalist.org/photos/2/small.jpg"], tmp_path, cached_only=True,
                              **kwargs)

    assert list(first) == [PHOTO] and calls == [PHOTO]
    assert cached[PHOTO].size == (64, 48)
    assert nothing == {}
