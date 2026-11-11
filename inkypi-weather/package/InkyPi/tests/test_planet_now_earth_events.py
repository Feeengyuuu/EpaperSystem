"""环球此刻's world view: tropical cyclones, strong earthquakes and NASA's Earth image of the day.

The fixtures are cut from the providers' answers of 2026-11-11 (China's National Meteorological
Centre, the US National Hurricane Center, USGS, the wolfx mirror of the China Earthquake Networks
list and science.nasa.gov's Earth Observatory posts).
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
from runtime.refresh_contracts import TaskContext  # noqa: E402
from tests.test_planet_now import _payload  # noqa: E402

TYPHOON_LIST = (
    'typhoon_jsons_list_default(({"typhoonList":[[3382203,"CEMPAKA","查帕卡","2632","2632",null,'
    '"以其芬芳的花闻名的植物","start"],[3368875,"IN-FA","烟花","2631","2631",null,"烟花","stop"],'
    '[3346033,"KOGUMA","小熊\\n","2629","2629",null,"小熊星座","stop"],'
    '[3332799,"nameless","热带低压","20260027","20260027",20260027,null,"stop"]]}))')
TYPHOON_VIEW = (
    'typhoon_jsons_view_3382203({"typhoon":[3382203,"CEMPAKA","查帕卡",2632,2632,null,"以其芬芳的花闻名的植物","start",['
    '[3382336,"202611070600",1794031200000,"TS",143.2,19.7,995,20,"WNW",27,[["30KTS",200,150,150,250,3382336]],'
    '{"BABJ":[[12,"202611070600",140.4,20.9,980,30,"BABJ","STS"]]},["202611071400","2026年11月07日14时00分",null,null]],'
    '[3385029,"202611110600",1794376800000,"TY",149.9,28.7,975,33,"ENE",45,[["30KTS",200,250,250,200,3385029],'
    '["50KTS",70,80,80,70,3385029]],{"BABJ":[[12,"202611110600",155.1,30.4,982,28,"BABJ","STS"]]},'
    '["202611111400","2026年11月11日14时00分",null,null]],'
    '[3385053,"202611110900",1794387600000,"TY",151.4,29.2,975,33,"ENE",66,[["30KTS",300,280,220,300,3385053],'
    '["50KTS",80,60,120,100,3385053]],{"BABJ":[[12,"202611110900",158.9,32,982,28,"BABJ","STS"]]},'
    '["202611111700","2026年11月11日17时00分",null,null]]],null]})')
NHC = {"activeStorms": [{
    "id": "cp042026", "binNumber": "CP4", "name": "Pena", "classification": "TS", "intensity": "40",
    "pressure": "1002", "latitude": "9.9N", "longitude": "169.4W", "latitudeNumeric": 9.9,
    "longitudeNumeric": -169.4, "movementDir": 265, "movementSpeed": 16, "lastUpdate": "2026-11-11T21:00:00.000Z",
    "publicAdvisory": {"advNum": "021", "issuance": "2026-11-11T21:00:00.000Z",
                       "url": "https://www.nhc.noaa.gov/text/HFOTCPCP4.shtml"}}]}
USGS = {"type": "FeatureCollection", "metadata": {"title": "USGS Significant Earthquakes, Past Week", "count": 1},
        "features": [{"type": "Feature", "properties": {
            "mag": 6.4, "place": "30 km WSW of Lata, Solomon Islands", "time": 1794195299466, "alert": "green",
            "tsunami": 0, "sig": 631, "type": "earthquake", "title": "M 6.4 - 30 km WSW of Lata, Solomon Islands"},
            "geometry": {"type": "Point", "coordinates": [165.5616, -10.8669, 17]}, "id": "us7000tx16"}]}
CENC = {
    "No1": {"type": "reviewed", "EventID": "CD.20261111221508.675", "time": "2026-11-11 22:15:08",
            "location": "新疆巴音郭楞州和静县", "magnitude": "3.0", "depth": "17", "latitude": "42.66",
            "longitude": "83.10", "intensity": "4"},
    "No2": {"type": "reviewed", "EventID": "AU.20261111061508.5", "time": "2026-11-11 06:15:08",
            "location": "江苏扬州市江都区", "magnitude": "3.9", "depth": "20", "latitude": "32.62",
            "longitude": "119.60", "intensity": "5"},
    "No3": {"type": "reviewed", "EventID": "CC.20261109131303.5", "time": "2026-11-09 12:52:49",
            "location": "日本本州东部附近海域", "magnitude": "5.7", "depth": "10", "latitude": "36.25",
            "longitude": "141.85", "intensity": "8"},
    "No4": {"type": "reviewed", "EventID": "CC.20261109120320.6", "time": "2026-11-09 11:34:59",
            "location": "所罗门群岛", "magnitude": "6.3", "depth": "10", "latitude": "-10.90",
            "longitude": "165.60", "intensity": "8"},
    "No5": {"type": "reviewed", "EventID": "CC.20261104053418.0", "time": "2026-11-04 05:34:18",
            "location": "新疆克孜勒苏州乌恰县", "magnitude": "5.0", "depth": "10", "latitude": "39.80",
            "longitude": "74.30", "intensity": "7"},
    "md5": "0e2f1b6c",
}
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


def _world():
    """The new feeds' data as the page keeps it, from the fixtures."""
    storms = sources.parse_typhoon_list(TYPHOON_LIST)
    return {
        "typhoons": {"data": {"storms": [{**storms[0], **sources.parse_typhoon_track(TYPHOON_VIEW)}]}},
        "hurricanes": {"data": sources.parse_hurricanes(NHC)},
        "quakes": {"data": sources.parse_world_quakes(USGS)},
        "china_quakes": {"data": sources.parse_china_quakes(CENC)},
        "earth_photo": {"data": sources.parse_earth_photo(EARTH_OBSERVATORY)},
    }


def _labels(monkeypatch):
    drawn = []
    original = render.SpaceCanvas.text
    monkeypatch.setattr(render.SpaceCanvas, "text", lambda self, xy, text, *args, **kwargs: (
        drawn.append(str(text)), original(self, xy, text, *args, **kwargs))[1])
    return drawn


# --- the feeds' parsers ---------------------------------------------------------------------------

def test_the_typhoon_list_reads_the_active_storms_through_its_doubled_parentheses():
    storms = sources.parse_typhoon_list(TYPHOON_LIST)

    # Only the storm still going; its number is the year and the season's count.
    assert storms == [{"id": 3382203, "latin": "CEMPAKA", "name": "查帕卡", "number": "2632"}]
    quiet = TYPHOON_LIST.replace('"start"', '"stop"')
    assert sources.parse_typhoon_list(quiet) == []
    for broken in ("", "<html>busy</html>", "callback(([1,2]))", 'x(({"typhoonList":"none"}))'):
        with pytest.raises(ValueError):
            sources.parse_typhoon_list(broken)


def test_a_typhoons_track_gives_its_newest_fix_and_the_way_it_came():
    track = sources.parse_typhoon_track(TYPHOON_VIEW)

    assert track["level"] == "台风" and (track["lat"], track["lon"]) == (29.2, 151.4)
    assert (track["pressure"], track["wind"]) == (975, 33.0)
    assert track["time"] == "2026-11-11T09:00:00+00:00" and track["area"] == "西北太平洋"
    # The path so far, oldest first, as [lat, lon].
    assert track["track"] == [[19.7, 143.2], [28.7, 149.9], [29.2, 151.4]]
    assert sources.parse_typhoon_track('typhoon_jsons_view_1({"typhoon":[1,"A","甲",1,1,null,"","start",[],null]})') == {}
    # Where a storm is, in the seas the Centre names.
    assert sources.storm_area(18.0, 114.0) == "南海" and sources.storm_area(28.0, 124.0) == "东海"
    assert sources.storm_area(15.0, 130.0) == "西北太平洋"


def test_typhoons_cost_one_request_when_none_is_active_and_one_more_per_storm(monkeypatch):
    asked = []

    def text(_method, url, _context, **kwargs):
        asked.append((url, kwargs.get("params", {}).get("callback")))
        return TYPHOON_VIEW if "view_" in url else TYPHOON_LIST

    monkeypatch.setattr(sources, "_text", text)
    data = sources.fetch_typhoons(NOW, None)

    assert asked == [("http://typhoon.nmc.cn/weatherservice/typhoon/jsons/list_default", "typhoon_jsons_list_default"),
                     ("http://typhoon.nmc.cn/weatherservice/typhoon/jsons/view_3382203", "typhoon_jsons_view_3382203")]
    assert [storm["name"] for storm in data["storms"]] == ["查帕卡"] and data["storms"][0]["wind"] == 33.0
    assert sources.valid_storms(data)

    asked.clear()
    monkeypatch.setattr(sources, "_text", lambda _m, url, _c, **_kw: asked.append(url) or TYPHOON_LIST.replace(
        '"start"', '"stop"'))
    # A quiet ocean is a valid answer, not a failure.
    assert sources.fetch_typhoons(NOW, None) == {"storms": []} and len(asked) == 1
    assert sources.valid_storms({"storms": []}) and not sources.valid_storms({}) and not sources.valid_storms(None)


def test_the_hurricane_centres_storms_read_class_wind_pressure_and_basin():
    storms = sources.parse_hurricanes(NHC)["storms"]

    assert len(storms) == 1
    pena = storms[0]
    assert (pena["name"], pena["level"], pena["area"]) == ("Pena", "热带风暴", "中太平洋")
    # 40 kt is 20.6 m/s: the page gives every storm in the same unit.
    assert pena["wind"] == pytest.approx(20.6, abs=0.05) and pena["pressure"] == 1002
    assert (pena["lat"], pena["lon"], pena["time"]) == (9.9, -169.4, "2026-11-11T21:00:00+00:00")
    major = sources.parse_hurricanes({"activeStorms": [{**NHC["activeStorms"][0], "id": "al142026",
                                                        "classification": "HU", "intensity": "115"}]})["storms"][0]
    assert (major["level"], major["area"]) == ("飓风 4 级", "大西洋")
    assert sources.parse_hurricanes({"activeStorms": []}) == {"storms": []}
    assert sources.parse_hurricanes({"activeStorms": [{"name": "Nowhere"}]}) == {"storms": []}
    with pytest.raises(ValueError):
        sources.parse_hurricanes({"error": "busy"})


def test_strong_quakes_come_from_usgs_and_chinas_network_with_chinese_places():
    world = sources.parse_world_quakes(USGS)["quakes"]

    assert world == [{"mag": 6.4, "place": "所罗门群岛", "time": "2026-11-09T03:34:59+00:00", "lat": -10.867,
                      "lon": 165.562, "depth": 17, "domestic": False}]
    assert sources.quake_place("12 km SSE of Petrolia, CA") == "美国加州"
    assert sources.quake_place("off the east coast of Honshu, Japan") == "日本"
    assert sources.quake_place("Mid-Indian Ridge") == "Mid-Indian Ridge"
    assert sources.parse_world_quakes({"features": []}) == {"quakes": []}

    china = sources.parse_china_quakes(CENC)["quakes"]
    # Beijing time becomes UTC; quakes abroad are told from China's own by the place's province.
    assert china[0] == {"mag": 3.0, "place": "新疆巴音郭楞州和静县", "time": "2026-11-11T14:15:08+00:00",
                        "lat": 42.66, "lon": 83.1, "depth": 17, "domestic": True}
    assert [(row["place"], row["domestic"]) for row in china[2:4]] == [("日本本州东部附近海域", False),
                                                                       ("所罗门群岛", False)]
    assert len(china) == 5 and sources.valid_quakes({"quakes": china}) and not sources.valid_quakes({})
    with pytest.raises(ValueError):
        sources.parse_china_quakes({"md5": "only"})


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


# --- what the page makes of them -------------------------------------------------------------------

def test_the_earth_rows_lead_with_storms_then_the_newest_quakes_under_their_chinese_names():
    events = render.earth_events(_world(), NOW)

    assert [(row["kind"], row["title"]) for row in events["rows"]] == [
        ("storm", "查帕卡 · 台风"), ("storm", "Pena · 热带风暴"),
        ("quake", "M3.9 江苏扬州市江都区"), ("quake", "M6.4 所罗门群岛")]
    assert (events["rows"][0]["detail"], events["rows"][0]["aside"]) == ("975 hPa · 33 m/s", "西北太平洋")
    assert (events["rows"][1]["detail"], events["rows"][1]["aside"]) == ("1002 hPa · 21 m/s", "中太平洋")
    # A typhoon's mark is amber, a tropical storm's blue, an M6 quake's red, a small one's grey.
    assert [row["colour"] for row in events["rows"]] == ["gold", "earth", "muted", "trends"]
    # USGS's quake and the Chinese network's report of it are one row: USGS's size, the Chinese name,
    # the page's own clock.
    assert events["rows"][3]["detail"] == "11/08 19:34 · 深 17 km"
    assert events["storms"] == 2
    # China's strongest of the last two days stands in when nothing there reached M4.5 this week
    # (Japan's M5.7 is neither China's nor a world-scale quake, so it has no row).
    assert events["rows"][2]["detail"] == "11/10 14:15 · 深 20 km"
    # Three days earlier the week held an M5.0 in Xinjiang, which needs no stand-in beside it; what
    # had not happened yet is not shown.
    earlier = render.earth_events(_world(), NOW - timedelta(days=3))
    assert [row["title"] for row in earlier["rows"]] == ["M5.0 新疆克孜勒苏州乌恰县"] and earlier["storms"] == 0


def test_old_fixes_and_old_quakes_leave_the_rows():
    world = _world()
    week_later = NOW + timedelta(days=8)

    # A cached storm whose last fix is days old is no longer "now"; week-old quakes are no news.
    assert render.earth_events(world, week_later) == {"rows": [], "storms": 0}
    assert render.earth_events({}, NOW) == {"rows": [], "storms": 0}
    world["typhoons"] = {"data": {"storms": []}}
    world["hurricanes"] = {"data": {}}
    quiet = render.earth_events(world, NOW)
    assert quiet["storms"] == 0 and all(row["kind"] == "quake" for row in quiet["rows"]) and quiet["rows"]


# --- the page ------------------------------------------------------------------------------------

def _turn(monkeypatch, shown):
    monkeypatch.setattr(render, "earth_turn", lambda now: shown)


@pytest.mark.parametrize("mode", ["day", "night"])
def test_the_crew_column_takes_turns_with_the_earth_rows(monkeypatch, mode):
    theme = {"_inkypi_theme": {"mode": mode}}
    payload = {**_payload(), **_world()}
    drawn = _labels(monkeypatch)

    _turn(monkeypatch, True)
    page = render.render_page((800, 480), payload, theme, NOW, {})
    assert "地球动态" in drawn and "查帕卡 · 台风" in drawn and "M6.4 所罗门群岛" in drawn
    assert "975 hPa · 33 m/s" in drawn and "西北太平洋" in drawn and "在轨" not in drawn
    palette = render._palette(theme)
    left, top, right, bottom = render.CARDS["space"]

    def near(box, colour, tolerance=24):
        return sum(all(abs(a - b) <= tolerance for a, b in zip(pixel, colour))
                   for pixel in page.crop(box).get_flattened_data())

    # Every line is plain ink; the colour is in the marks that lead the rows (amber for the
    # typhoon, blue for the tropical storm, red for the M6.4).
    column = (right - 176, top + render.HEADER + 6, right - 8, bottom - 6)
    marks = (column[0], column[1] + 20, column[0] + render.EARTH_MARK + 1, column[3])
    words = (marks[2] + 3, column[1], column[2], column[3])
    assert all(near(marks, palette[name]) >= 8 for name in ("gold", "earth", "trends"))
    assert near(words, palette["ink"], 0) > 300
    assert not any(near(words, palette[name]) for name in ("gold", "earth", "trends"))
    # The storms and quakes are also marked where they are on the world map.
    map_box = (left + 130, top + render.HEADER + 6, right - 186, top + render.HEADER + 86)
    world_map = page.crop(map_box)
    assert near(map_box, palette["trends"]) >= 4

    drawn.clear()
    _turn(monkeypatch, False)
    crew_page = render.render_page((800, 480), payload, theme, NOW, {})
    assert "在轨" in drawn and "地球动态" not in drawn
    assert crew_page.crop(map_box).tobytes() != world_map.tobytes()
    # On the crew's turn the page is drawn exactly as without the new feeds.
    assert crew_page.tobytes() == render.render_page((800, 480), _payload(), theme, NOW, {}).tobytes()


def test_without_rows_the_crew_keeps_its_column_on_every_turn(monkeypatch):
    _turn(monkeypatch, True)
    drawn = _labels(monkeypatch)
    empty = {name: {"data": {}} for name in ("typhoons", "hurricanes", "quakes", "china_quakes", "earth_photo")}

    page = render.render_page((800, 480), {**_payload(), **empty}, {}, NOW, {})

    assert "在轨" in drawn and "地球动态" not in drawn
    assert page.tobytes() == render.render_page((800, 480), _payload(), {}, NOW, {}).tobytes()


def test_the_rows_turn_comes_every_other_slot():
    slot = render.EARTH_TURN_MINUTES * 60
    start = datetime.fromtimestamp(slot * 1000, tz=sources.PACIFIC)
    turns = [render.earth_turn(start + timedelta(seconds=slot * index)) for index in range(6)]
    assert turns == [False, True, False, True, False, True]
    assert render.earth_turn(start + timedelta(seconds=slot + 5)) is True


@pytest.mark.parametrize("mode", ["day", "night"])
def test_the_earth_image_takes_the_photo_slot_with_its_title(monkeypatch, mode):
    _turn(monkeypatch, False)
    world = _world()
    photo = world["earth_photo"]["data"]
    picture = Image.new("RGB", (319, 213), (40, 120, 90))
    payload = {**_payload(), **world, "photo_slot": "earth"}
    drawn = _labels(monkeypatch)

    page = render.render_page((800, 480), payload, {"_inkypi_theme": {"mode": mode}}, NOW, {photo["image"]: picture})

    left, top, _right, _bottom = render.CARDS["space"]
    assert page.getpixel((left + 64, top + 110)) == (40, 120, 90)
    assert "NASA 地球观测站" in drawn and "每日一图 · 11/11" in drawn
    assert "Water Surges Into" in " ".join(drawn) and "火星日 2019" not in drawn

    # Without its picture the slot stays Mars's.
    drawn.clear()
    mars = Image.new("RGB", (400, 300), (190, 120, 80))
    page = render.render_page((800, 480), payload, {"_inkypi_theme": {"mode": mode}}, NOW,
                              {"https://mars.nasa.gov/a.jpg": mars})
    assert page.getpixel((left + 64, top + 110)) == (190, 120, 80) and "火星日 2019" in drawn


# --- the refresh ---------------------------------------------------------------------------------

def test_the_new_feeds_are_optional_with_their_own_lifetimes():
    text = Path(planet_now.__file__).read_text(encoding="utf-8")

    for line in ('"typhoons": load("typhoons", lambda ctx: sources.fetch_typhoons(now, ctx), HOUR, 12 * HOUR,',
                 '"hurricanes": load("hurricanes", sources.fetch_hurricanes, HOUR, 12 * HOUR,',
                 '"quakes": load("quakes", sources.fetch_world_quakes, HOUR, 2 * DAY,',
                 '"china_quakes": load("china_quakes", sources.fetch_china_quakes, HOUR, 2 * DAY,',
                 '"earth_photo": load("earth_photo", sources.fetch_earth_photo, 6 * HOUR, 4 * DAY,'):
        assert line in text, line
    assert not set(planet_now.EARTH_FEEDS) & set(planet_now.CORE_FEEDS)
    assert planet_now.EARTH_FEEDS == ("typhoons", "hurricanes", "quakes", "china_quakes", "earth_photo")


def _plugin(monkeypatch, tmp_path, fetchers):
    """The plugin on a real SourceBook under ``tmp_path``; every old feed is offline, the new ones
    answer through ``fetchers``."""
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
    answers = {"quakes": sources.parse_world_quakes(USGS)}
    calls = []

    def world_quakes(_context):
        calls.append("quakes")
        if answers["quakes"] is None:
            raise OSError("provider down")
        return answers["quakes"]

    plugin = _plugin(monkeypatch, tmp_path, {"fetch_world_quakes": world_quakes})
    first, _ = plugin._refresh({}, NOW, allow_network=True, context=_context())
    assert first["quakes"]["state"] == "live" and first["typhoons"]["state"] == "unavailable"
    assert first["quakes"]["data"]["quakes"][0]["place"] == "所罗门群岛"

    # Within the hour the cached copy answers and the provider is not asked again.
    calls.clear()
    again, _ = plugin._refresh({}, NOW + timedelta(minutes=30), allow_network=True, context=_context())
    assert again["quakes"]["state"] == "fresh_cache" and calls == []

    # Later the provider fails: the last good copy stays for two days, then it is dropped.
    answers["quakes"] = None
    stale, _ = plugin._refresh({}, NOW + timedelta(hours=3), allow_network=True, context=_context())
    assert stale["quakes"]["state"] == "stale_cache" and stale["quakes"]["data"] == first["quakes"]["data"]
    gone, _ = plugin._refresh({}, NOW + timedelta(days=3), allow_network=True, context=_context())
    assert gone["quakes"]["state"] == "unavailable" and gone["quakes"]["data"] == {}
    # None of this is the page's health: only the core feeds decide it, and the page still draws.
    assert first["provenance"] == gone["provenance"]
    assert render.render_page((800, 480), gone, {}, NOW, {}).size == (800, 480)


def test_a_theme_redraw_reads_the_caches_and_never_asks_a_provider(monkeypatch, tmp_path):
    calls = []
    fetchers = {name: (lambda *_args, name=name: calls.append(name) or {})
                for name in ("fetch_typhoons", "fetch_hurricanes", "fetch_world_quakes", "fetch_china_quakes",
                             "fetch_earth_photo")}
    plugin = _plugin(monkeypatch, tmp_path, fetchers)
    thumbnails = []
    monkeypatch.setattr(planet_now, "load_thumbnails", lambda urls, *_a, **kw: thumbnails.append(kw["cached_only"]) or {})

    payload, _ = plugin._refresh({}, NOW, allow_network=False, context=_context())

    assert calls == [] and thumbnails == [True]
    assert all(payload[name]["state"] == "unavailable" for name in planet_now.EARTH_FEEDS)


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
    assert earth["image"] in asked[0]
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
