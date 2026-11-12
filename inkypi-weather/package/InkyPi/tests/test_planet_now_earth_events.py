"""NASA Earth photos share the Mars slot; the cancelled Earth-events column stays retired.

Earth Observatory fixture captured from science.nasa.gov on 2026-11-11.
"""

import json
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from PIL import Image
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.planet_now import planet_now, render, sources  # noqa: E402
from plugins.planet_now.planet_now import PlanetNow  # noqa: E402
from runtime.refresh_contracts import TaskCancelled, TaskContext, TaskDeadlineExceeded  # noqa: E402
from tests.test_http_client import FakeResponse, FakeSession  # noqa: E402
from tests.test_planet_now import _payload  # noqa: E402
from utils.http_client import HttpClient  # noqa: E402
from utils.remote_thumbnails import _key  # noqa: E402

EO_IMAGE = ("https://assets.science.nasa.gov/content/dam/science/esd/eo/images/iotd/2026/"
            "water-surges-into-navajo-reservoir/navajoreservoir_oli2_20261107_th.jpg/jcr:content/renditions/")
EARTH_OBSERVATORY = [{
    "date": "2026-11-11T00:01:00",
    "link": "https://science.nasa.gov/earth/earth-observatory/water-surges-into-navajo-reservoir/",
    "title": {"rendered": "Water Surges Into Navajo Reservoir"},
    "excerpt": {"rendered": "<p>Rounds of heavy rain in the U.S. Southwest flooded rivers and rapidly raised "
                            "New Mexico&#8217;s second-largest reservoir.</p>\n", "protected": False},
    "_embedded": {"wp:featuredmedia": [{
        "source_url": "https://assets.science.nasa.gov/dynamicimage/assets/science/esd/eo/images/iotd/2026/"
                      "water-surges-into-navajo-reservoir/navajoreservoir_oli2_20261107_th.jpg",
        "media_details": {"filesize": 556643, "width": 720, "height": 480, "sizes": {
            "thumbnail": {"width": 319, "height": 319, "source_url": EO_IMAGE + "cq5dam.thumbnail.319.319.png"},
            "large": {"width": 1280, "height": 1280, "source_url": EO_IMAGE + "cq5dam.web.1280.1280.jpeg"},
            "thumbnail-sm": {"width": 140, "height": 100, "source_url": EO_IMAGE + "cq5dam.thumbnail.140.100.png"},
            "full": {"width": 720, "height": 480, "source_url": "http://insecure.example/full.jpg"}}}}]},
}]
NOW = datetime(2026, 11, 11, 12, 55, tzinfo=sources.PACIFIC)



def _context(seconds=30):
    return TaskContext(threading.Event(), time.monotonic() + seconds, time.monotonic)


def _labels(monkeypatch):
    drawn = []
    original = render.SpaceCanvas.text
    monkeypatch.setattr(render.SpaceCanvas, "text", lambda self, xy, text, *args, **kwargs: (
        drawn.append(str(text)), original(self, xy, text, *args, **kwargs))[1])
    return drawn


def test_the_earth_image_of_the_day_takes_a_size_near_the_slot_not_the_original():
    photo = sources.parse_earth_photo(EARTH_OBSERVATORY)

    assert photo["title"] == "Water Surges Into Navajo Reservoir" and photo["date"] == "2026-11-11"
    # 319 px wide is 213 tall for this 3:2 picture: enough for the slot; 140 x 100 is not, and the
    # 1280 px and original files are never asked for.
    assert photo["image"] == EO_IMAGE + "cq5dam.thumbnail.319.319.png"
    assert sources.safe_image_url(photo["image"]) == photo["image"]
    assert sources.valid_earth_photo(photo) and not sources.valid_earth_photo({"title": "x"})
    assert sources.parse_earth_photo([]) == {} and sources.parse_earth_photo({"code": "rest_no_route"}) == {}
    # A post whose only picture is on a host the page does not trust gives no picture.
    stranger = json.loads(json.dumps(EARTH_OBSERVATORY))
    media = stranger[0]["_embedded"]["wp:featuredmedia"][0]
    media["media_details"]["sizes"] = {"full": media["media_details"]["sizes"]["full"]}
    media["source_url"] = "https://example.com/a.jpg"
    assert sources.parse_earth_photo(stranger) == {}


@pytest.mark.parametrize("mode", ["day", "night"])
def test_the_earth_image_takes_the_photo_slot_with_its_title(monkeypatch, mode):
    world = {"earth_photo": {"data": sources.parse_earth_photo(EARTH_OBSERVATORY)}}
    photo = world["earth_photo"]["data"]
    picture = Image.new("RGB", (319, 213), (40, 120, 90))
    payload = {**_payload(), **world, "photo_slot": "earth"}
    drawn = _labels(monkeypatch)

    page = render.render_page((800, 480), payload, {"_inkypi_theme": {"mode": mode}}, NOW, {photo["image"]: picture})

    left, top, _right, _bottom = render.CARDS["space"]
    assert page.getpixel((left + 64, top + 110)) == (40, 120, 90)
    assert "在轨" in drawn and "地球动态" not in drawn
    assert "NASA 地球观测站" in drawn and "每日一图 · 11/11" in drawn
    assert "Water Surges Into" in " ".join(drawn) and "火星日 2019" not in drawn

    # Without its picture the slot stays Mars's.
    drawn.clear()
    mars = Image.new("RGB", (400, 300), (190, 120, 80))
    page = render.render_page((800, 480), payload, {"_inkypi_theme": {"mode": mode}}, NOW,
                              {"https://mars.nasa.gov/a.jpg": mars})
    assert page.getpixel((left + 64, top + 110)) == (190, 120, 80) and "火星日 2019" in drawn


def _plugin(monkeypatch, tmp_path, fetchers):
    """The plugin on a real SourceBook under ``tmp_path``; every old feed is offline; the photo
    answers through ``fetchers``."""
    monkeypatch.setenv("INKYPI_CACHE_DIR", str(tmp_path / "cache"))

    def offline(*_args, **_kwargs):
        raise OSError("offline in tests")

    for name in ("_text", "_json", "_bytes"):
        monkeypatch.setattr(sources, name, offline)
    for name, fetch in fetchers.items():
        monkeypatch.setattr(sources, name, fetch)
    monkeypatch.setattr(planet_now, "load_thumbnails", lambda *_a, **_kw: {})
    return PlanetNow({"id": "planet_now"})


def test_a_failed_refresh_keeps_the_last_good_copy_and_never_fails_the_page(monkeypatch, tmp_path):
    answers = {"earth_photo": sources.parse_earth_photo(EARTH_OBSERVATORY)}
    calls = []

    def earth_photo(_context):
        calls.append("earth_photo")
        if answers["earth_photo"] is None:
            raise OSError("provider down")
        return answers["earth_photo"]

    plugin = _plugin(monkeypatch, tmp_path, {"fetch_earth_photo": earth_photo})
    first, _ = plugin._refresh({}, NOW, allow_network=True, context=_context())
    assert first["earth_photo"]["state"] == "live"
    assert first["earth_photo"]["data"]["title"] == "Water Surges Into Navajo Reservoir"

    # Within six hours the cached copy answers and the provider is not asked again.
    calls.clear()
    again, _ = plugin._refresh({}, NOW + timedelta(minutes=30), allow_network=True, context=_context())
    assert again["earth_photo"]["state"] == "fresh_cache" and calls == []

    # Later the provider fails: the last good copy stays for four days, then it is dropped.
    answers["earth_photo"] = None
    stale, _ = plugin._refresh({}, NOW + timedelta(hours=7), allow_network=True, context=_context())
    assert stale["earth_photo"]["state"] == "stale_cache" and stale["earth_photo"]["data"] == first["earth_photo"]["data"]
    gone, _ = plugin._refresh({}, NOW + timedelta(days=5), allow_network=True, context=_context())
    assert gone["earth_photo"]["state"] == "unavailable" and gone["earth_photo"]["data"] == {}
    # None of this is the page's health: only the core feeds decide it, and the page still draws.
    assert first["provenance"] == gone["provenance"]
    assert render.render_page((800, 480), gone, {}, NOW, {}).size == (800, 480)


def test_a_theme_redraw_reads_the_caches_and_never_asks_a_provider(monkeypatch, tmp_path):
    calls = []
    fetchers = {"fetch_earth_photo": lambda *_args: calls.append("earth_photo") or {}}
    plugin = _plugin(monkeypatch, tmp_path, fetchers)
    thumbnails = []
    monkeypatch.setattr(planet_now, "load_thumbnails", lambda urls, *_a, **kw: thumbnails.append(kw["cached_only"]) or {})

    payload, _ = plugin._refresh({}, NOW, allow_network=False, context=_context())

    assert calls == [] and thumbnails == [True]
    assert payload["earth_photo"]["state"] == "unavailable"


def test_the_earth_image_joins_the_photo_rotation_only_when_its_picture_is_at_hand(monkeypatch, tmp_path):
    photos = [{"sol": n, "camera": "桅杆相机", "taken_utc": f"2026-10-{20 + n}T05:00:00",
               "image": f"https://mars.nasa.gov/{n}.jpg"} for n in range(2)]
    earth = sources.parse_earth_photo(EARTH_OBSERVATORY)
    feeds = {"mars": {**photos[0], "photos": photos}, "earth_photo": earth}
    book = SimpleNamespace(load=lambda source, *_a, **_kw: {"data": feeds.get(source.name, {})})
    plugin = PlanetNow({"id": "planet_now"})
    monkeypatch.setattr(plugin, "_book", lambda: book)
    monkeypatch.setattr(plugin, "cache_dir", lambda **_kw: tmp_path)
    monkeypatch.setattr(planet_now, "combined_provenance", lambda _: {})
    at_hand = {photo["image"]: Image.new("RGB", (10, 10)) for photo in photos}
    at_hand[earth["image"]] = Image.new("RGB", (10, 10))
    asked = []
    monkeypatch.setattr(planet_now, "load_thumbnails", lambda urls, *_a, **_kw: asked.append(list(urls)) or {
        url: image for url, image in at_hand.items() if url in urls})

    slots = [plugin._refresh({}, NOW + timedelta(minutes=13 * index), allow_network=False, context=None)[0]
             for index in range(3)]

    # Three pictures share the slot: two from Mars and the Earth's, one turn each.
    assert earth["image"] in {url for batch in asked for url in batch}
    assert sorted(slot["photo_slot"] for slot in slots) == ["earth", "mars", "mars"]
    assert len({slot["mars"]["data"]["image"] for slot in slots if slot["photo_slot"] == "mars"}) == 2
    assert all(len(slot["mars"]["data"]["photos"]) == 2 for slot in slots)

    del at_hand[earth["image"]]
    slots = [plugin._refresh({}, NOW + timedelta(minutes=13 * index), allow_network=False, context=None)[0]
             for index in range(3)]
    assert {slot["photo_slot"] for slot in slots} == {"mars"}


def test_the_web_motion_is_only_published_for_a_mars_photo(monkeypatch, tmp_path):
    from plugins.planet_now import mars_motion

    published = []
    monkeypatch.setattr(mars_motion, "publish_motion", lambda *args: published.append(args[2].get("photo_slot")))
    plugin = PlanetNow({"id": "planet_now"})
    monkeypatch.setattr(plugin, "cache_dir", lambda **_kw: tmp_path)
    monkeypatch.setattr(plugin, "get_dimensions", lambda _device: (800, 480))
    monkeypatch.setattr(planet_now, "attach_source_provenance", lambda image, *_a, **_kw: image)
    for slot in ("mars", "earth"):
        monkeypatch.setattr(plugin, "_refresh", lambda *_a, slot=slot, **_kw: (
            {**_payload(), "photo_slot": slot, "provenance": None}, {}))
        plugin.generate_image({"_inkypi_theme": {"mode": "day"}}, None)

    assert published == ["mars"]


@pytest.mark.parametrize("mode", ["day", "night"])
def test_retired_earth_events_cannot_replace_the_crew_or_mark_the_map(monkeypatch, mode):
    # An existing experimental cache must not bring the cancelled column back.
    legacy = {"typhoons": {"data": {"storms": [{
        "name": "查帕卡", "level": "台风", "time": NOW.isoformat(), "lat": 29.2, "lon": 151.4,
        "wind": 33.0, "pressure": 975, "area": "西北太平洋", "track": [[28.7, 149.9], [29.2, 151.4]],
    }]}}, "quakes": {"data": {"quakes": [{
        "mag": 6.4, "place": "所罗门群岛", "time": NOW.isoformat(), "lat": -10.867, "lon": 165.562,
        "depth": 17, "domestic": False,
    }]}}}
    monkeypatch.setattr(render, "earth_turn", lambda _now: True, raising=False)
    drawn = _labels(monkeypatch)
    theme = {"_inkypi_theme": {"mode": mode}}

    actual = render.render_page((800, 480), {**_payload(), **legacy}, theme, NOW, {})

    assert "在轨" in drawn and "地球动态" not in drawn
    assert actual.tobytes() == render.render_page((800, 480), _payload(), theme, NOW, {}).tobytes()


def test_refresh_no_longer_loads_retired_earth_event_sources(monkeypatch, tmp_path):
    loaded = []

    def load(source, *_args, **_kwargs):
        loaded.append(source)
        return {"data": {}}

    plugin = PlanetNow({"id": "planet_now"})
    monkeypatch.setattr(plugin, "_book", lambda: SimpleNamespace(load=load))
    monkeypatch.setattr(plugin, "cache_dir", lambda **_kwargs: tmp_path)
    monkeypatch.setattr(planet_now, "combined_provenance", lambda _: {})
    monkeypatch.setattr(planet_now, "load_thumbnails", lambda *_args, **_kwargs: {})

    payload, _ = plugin._refresh({}, NOW, allow_network=True, context=_context())

    retired = {"typhoons", "hurricanes", "quakes", "china_quakes"}
    assert not retired.intersection(source.name for source in loaded)
    assert not retired.intersection(payload)
    assert {"earth_photo", "mars", "iss", "people", *planet_now.CORE_FEEDS} <= set(payload)
    photo = next(source for source in loaded if source.name == "earth_photo")
    assert photo.fresh_seconds == 6 * 3600 and photo.usable_seconds == 4 * 86400
    assert "earth_photo" not in planet_now.CORE_FEEDS


def _photo_refresh(monkeypatch, tmp_path, *, seconds=20, during_request=None, cached_earth=False):
    """Real thumbnail/cache and HTTP paths; only the public source payload and HTTP session are fake."""
    clock = [100.0]
    parent = TaskContext(threading.Event(), clock[0] + seconds, lambda: clock[0])
    earth = sources.parse_earth_photo(EARTH_OBSERVATORY)
    feeds = {"mars": _payload()["mars"]}
    mars = feeds["mars"]["data"]
    feeds["mars"] = {"data": {**mars, "photos": [mars]}}
    feeds["earth_photo"] = {"data": earth}
    for url in [mars["image"], *([earth["image"]] if cached_earth else [])]:
        with Image.new("RGB", (64, 64), (190, 120, 80)) as picture:
            picture.save(tmp_path / _key(url))
    response = FakeResponse(503, headers={"Retry-After": "30"})
    session = FakeSession([response])
    original = session.request

    def request(*args, **kwargs):
        if during_request:
            during_request(parent, clock)
        return original(*args, **kwargs)

    session.request = request
    client = HttpClient(session=session)
    monkeypatch.setattr(planet_now, "get_http_client", lambda: client)
    monkeypatch.setattr(planet_now, "combined_provenance", lambda _: {})
    plugin = PlanetNow({"id": "planet_now"})
    monkeypatch.setattr(plugin, "_book", lambda: SimpleNamespace(load=lambda source, *_a, **_kw: (
        feeds.get(source.name, {"data": {}}))))
    monkeypatch.setattr(plugin, "cache_dir", lambda **_kw: tmp_path)
    return plugin, parent, clock, session, response, mars["image"], earth["image"]


@pytest.mark.parametrize("advance", [0, 8])
def test_earth_thumbnail_retry_deadline_keeps_mars_and_the_page(monkeypatch, tmp_path, advance):
    def spend_slice(_parent, clock):
        clock[0] += advance

    plugin, parent, _clock, session, response, mars, earth = _photo_refresh(
        monkeypatch, tmp_path, during_request=spend_slice)

    payload, images = plugin._refresh({}, NOW, allow_network=True, context=parent)

    assert [call[1] for call in session.calls] == [earth]
    assert response.closed and parent.remaining_seconds() == 20 - advance
    assert earth not in images and payload["photo_slot"] == "mars"
    assert images[mars].getpixel((0, 0)) == (190, 120, 80)
    assert render.render_page((800, 480), payload, {}, NOW, images).size == (800, 480)
    for image in images.values():
        image.close()


@pytest.mark.parametrize("cached", [False, True])
def test_earth_thumbnail_leaves_a_render_reserve_and_uses_available_cache(monkeypatch, tmp_path, cached):
    plugin, parent, _clock, session, _response, mars, earth = _photo_refresh(
        monkeypatch, tmp_path, seconds=10, cached_earth=cached)

    _payload, images = plugin._refresh({}, NOW, allow_network=True, context=parent)

    assert session.calls == [] and parent.remaining_seconds() == 10
    assert (earth in images) is cached
    assert images[mars].getpixel((0, 0)) == (190, 120, 80)
    for image in images.values():
        image.close()


@pytest.mark.parametrize("stop", ["cancel", "deadline"])
def test_earth_thumbnail_propagates_actual_parent_stop(monkeypatch, tmp_path, stop):
    def stop_parent(parent, clock):
        if stop == "cancel":
            parent.cancel_event.set()
        else:
            clock[0] += 21

    plugin, parent, _clock, session, response, _mars, _earth = _photo_refresh(
        monkeypatch, tmp_path, during_request=stop_parent)

    with pytest.raises(TaskCancelled if stop == "cancel" else TaskDeadlineExceeded):
        plugin._refresh({}, NOW, allow_network=True, context=parent)

    assert len(session.calls) == 1 and response.closed
