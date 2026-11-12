"""Bay Traffic Cams' message board: what Caltrans' changeable message signs say when it matters
(a crash, blocked lanes, a closure, chain control), not their travel times or blank faces; one
message in the header, the road of a camera on screen first. An optional extra: without it the
page is the one it was."""

import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.base_plugin.render_provenance import SourceProvenance  # noqa: E402
from plugins.bay_traffic_cams import bay_traffic_cams as plugin_module  # noqa: E402
from plugins.bay_traffic_cams import render, sources  # noqa: E402
from plugins.bay_traffic_cams.render import render_page  # noqa: E402
from runtime.refresh_contracts import TaskContext  # noqa: E402

from tests.test_bay_traffic_cams import _jpeg, _Response, _road, _row  # noqa: E402

NOW = datetime(2026, 11, 11, 12, 50, tzinfo=sources.PACIFIC)
DAY = {"_inkypi_theme": {"mode": "day"}}


def _sign(index, name, place, lon, lat, direction, route, lines=("", "", ""), posted=("2026-11-11", "12:29:43"),
          display="1 Page (Normal)", in_service="true", second=("", "", "")):
    """One sign of Caltrans' cmsStatusD04.json, shaped as the feed gives it."""
    return {"cms": {
        "index": index, "recordTimestamp": {"recordDate": "2026-11-11", "recordTime": "12:49:34"},
        "location": {"district": "4", "locationName": name, "nearbyPlace": place, "longitude": lon, "latitude": lat,
                     "elevation": "0", "direction": direction, "county": "", "route": route, "routeSuffix": "",
                     "postmilePrefix": "", "postmile": "7.79", "alignment": "", "milepost": "449.07"},
        "inService": in_service,
        "message": {"messageTimestamp": {"messageDate": posted[0], "messageTime": posted[1]}, "display": display,
                    "displayTime": "Not Reported",
                    "phase1": {"phase1Font": "Not Reported", "phase1Line1": lines[0], "phase1Line2": lines[1],
                               "phase1Line3": lines[2]},
                    "phase2": {"phase2Font": "Not Reported", "phase2Line1": second[0], "phase2Line2": second[1],
                               "phase2Line3": second[2]}}}}


CLOSURE = ("W58O RICHMOND BR", "CLOSURE", "SAT 11PM-SUN 8AM")
NOT_REPORTED = ("Not Reported",) * 3
# Signs of District 4 as the feed listed them on 2026-11-11 at 12:49; CM003 carries the crash it
# showed at 09:45 that morning.
FEED = {"data": [
    _sign("1", "CM001-MRN 101 N/B S/OF SIR FRANCIS DRAKE", "Corte Madera", "-122.515881", "37.932114", "North", "US-101",
          posted=("2026-11-11", "12:41:18"), display="Blank"),
    _sign("3", "CM003-MRN 101 N/B N/OF PACHECO CRK OC", "Novato", "-122.530845", "38.051245", "North", "US-101",
          ("CRASH N-101", "ATHERTON AVE", "RIGHT LANE BLKD"), posted=("2026-11-11", "00:49:56")),
    _sign("6", "CM006-CC 24 W/B W/OF LAFAYETTE STA UC", "Lafayette", "-122.123319", "37.893832", "West", "SR-24", CLOSURE),
    _sign("8", "CM008-ALA 880 N/B N/OF WEST A STREET UC", "Hayward", "-122.115113", "37.675234", "North", "I-880",
          CLOSURE, posted=("2026-11-11", "12:29:51")),
    _sign("10", "CM010 -ALA 880 N/B N/OF INDUSTRIAL PKWY", "Hayward", "-122.076074", "37.620465", "North", "I-880",
          ("OAK ARPT  11 MIN", "OAKLAND   20 MIN", "CASTRO VY 10 MIN"), posted=("2026-11-11", "12:29:59")),
    _sign("21", "CM021-SCL 680 S/B S/OF SCOTT CREEK RD", "Milpitas", "-121.902118", "37.461466", "South", "I-680",
          ("SJ STATE  10 MIN", "CUPERTINO 19 MIN", "S101 / 85 17 MIN"), posted=("2026-11-11", "12:31:39")),
    _sign("38", "CM038-SCR 1 N/B N/OF 41ST AVE OC", "Santa Cruz", "-121.974062", "36.984233", "North", "SR-1",
          NOT_REPORTED, posted=("Not Reported", "Not Reported"), display="Not Reported", in_service="false",
          second=NOT_REPORTED),
    _sign("124", "CM124 WB80 AT OLIVER RD", "Fairfield", "-122.067043", "38.253295", "West", "I-80",
          ("W58O RICHMOND BR", "CLOSURE", "NOV 14 11 PM-8AM"), posted=("2026-11-11", "12:29:47")),
]}


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(sources, "fetch_signs", lambda home, _ctx: sources.normalize_signs(FEED, home))


def test_only_the_signs_that_say_something_that_matters_are_kept():
    data = sources.normalize_signs(FEED, sources.FREMONT)

    # The crash first, then the closure notice's signs nearest home first; no blank face, travel
    # times or sign out of service.
    assert [(sign["id"], sign["kind"]) for sign in data["signs"]] == [
        ("3", "crash"), ("8", "planned"), ("6", "planned"), ("124", "planned")]
    crash = data["signs"][0]
    assert crash["pages"] == [["CRASH N-101", "ATHERTON AVE", "RIGHT LANE BLKD"]]
    assert (crash["route"], crash["direction"], crash["place"]) == ("US-101", "North", "Novato")
    assert crash["posted"] == "2026-11-11T00:49:56-08:00" and 70 < crash["distance_km"] < 80
    assert sources.valid_signs(data) and len(str(data)) < 2000
    assert not sources.valid_signs({"signs": [{"route": "I-80"}]}) and not sources.valid_signs({})
    for broken in ({}, {"data": "none"}, []):
        with pytest.raises(ValueError):
            sources.normalize_signs(broken, sources.FREMONT)
    # A feed where every face is blank is an answer too: nothing to show.
    assert sources.normalize_signs({"data": FEED["data"][:1]}, sources.FREMONT) == {"signs": []}


@pytest.mark.parametrize("lines, kind, label", [
    (["CRASH N-101", "ATHERTON AVE", "RIGHT LANE BLKD"], "crash", "事故"),
    (["ACCIDENT AHEAD", "2 LEFT LANES", "BLOCKED"], "crash", "事故"),
    (["AMBER ALERT", "BLK HONDA CIVIC", "CA LIC 8ABC123"], "alert", "警报"),
    (["LEFT LANE", "BLOCKED", "AT ASHBY AVE"], "blocked", "车道受阻"),
    (["HWY 37 CLOSED", "AT LAKEVILLE HWY", "USE HWY 116"], "blocked", "封闭"),
    (["W58O RICHMOND BR", "CLOSURE", "SAT 11PM-SUN 8AM"], "planned", "封闭预告"),
    (["I-80 CLOSED", "NOV 14 11 PM-8AM"], "planned", "封闭预告"),
    (["ROADWORK", "TONIGHT 9PM-5AM", "EXPECT DELAYS"], "planned", "施工预告"),
    (["CHAINS REQUIRED", "I-80 AT KINGVALE"], "weather", "防滑链"),
    (["DENSE FOG", "REDUCE SPEED"], "weather", "浓雾"),
    (["HIGH WIND", "ADVISORY", "BAY BRIDGE"], "weather", "大风"),
    (["FLOODING", "RIGHT LANE"], "weather", "积水"),
    (["ROAD WORK AHEAD", "EXPECT DELAYS"], "work", "施工"),
    (["HEAVY TRAFFIC", "TO BAY BRIDGE"], "work", "延误"),
])
def test_a_message_is_named_by_what_it_reports(lines, kind, label):
    assert sources.sign_kind([lines]) == kind and sources.sign_label(kind, [lines]) == label


@pytest.mark.parametrize("lines", [
    ["OAK ARPT  11 MIN", "OAKLAND   20 MIN", "CASTRO VY 10 MIN"],          # travel times
    ["SJ STATE  10 MIN", "CUPERTINO 19 MIN", "S101 / 85 17 MIN"],
    ["CLICK IT", "OR TICKET"], ["DRIVE SOBER", "OR GET PULLED OVER"],       # campaign slogans
    ["MARTINEZ", "SUNOL", "NOVATO"],                                       # towns are not days or months
])
def test_travel_times_and_slogans_are_not_messages_that_matter(lines):
    assert sources.sign_kind([lines]) is None


def _camera(index, route="I-880", lat=37.6, lon=-122.07, **extra):
    return {"id": f"TVA{index}", "route": route, "direction": "North", "place": f"Place {index}", "town": "Hayward",
            "image": f"https://cwwp2.dot.ca.gov/{index}.jpg", "history": [], "distance_km": 2.0 + index, "bearing": 200,
            "latitude": lat, "longitude": lon, **extra}


def test_the_board_takes_the_most_urgent_message_then_the_road_of_a_camera_on_screen():
    data = sources.normalize_signs(FEED, sources.FREMONT)
    far = [_camera(0, "SR-92", 37.57, -122.27), _camera(1, "SR-84", 37.53, -122.06)]

    crash = sources.pick_message(data, far)
    assert (crash["kind"], crash["label"], crash["lines"]) == (
        "crash", "事故", ["CRASH N-101", "ATHERTON AVE", "RIGHT LANE BLKD"])
    assert (crash["route"], crash["direction"], crash["place"], crash["posted"]) == (
        "US-101", "North", "Novato", "2026-11-11T00:49:56-08:00")
    # One crash sign, no camera on its road, and the closure notice still waiting behind it.
    assert (crash["signs"], crash["camera"], crash["more"]) == (1, None, 1)

    notices = {"signs": data["signs"][1:]}
    # The same notice on three signs is one message, told where it is nearest home...
    nearest = sources.pick_message(notices, far)
    assert (nearest["label"], nearest["place"], nearest["signs"], nearest["camera"], nearest["more"]) == (
        "封闭预告", "Hayward", 3, None, 0)
    # ...unless a camera on screen looks at the road of one of its signs: then that sign, and
    # which camera (the second on the wall here).
    tied = sources.pick_message(notices, [far[0], _camera(1, "SR-24", 37.89, -122.10)])
    assert (tied["route"], tied["place"], tied["camera"]) == ("SR-24", "Lafayette", 1)
    # A camera on the same road but a county away is not the sign's road any more.
    assert sources.pick_message(notices, [_camera(0, "SR-24", 37.80, -121.60)])["camera"] is None
    # Cameras without a position (an older cache) simply tie nothing.
    assert sources.pick_message(notices, [{"route": "SR-24"}])["place"] == "Hayward"
    assert sources.pick_message({"signs": []}, far) is None and sources.pick_message(None, far) is None
    # Of a sign that alternates two pages the board takes the one that says what is the matter.
    two = sources.normalize_signs({"data": [_sign(
        "70", "CM070-ALA80 W/B W/OF RTE 13 (ASHBY AVE)", "Emeryville", "-122.299285", "37.849538", "West", "I-80",
        ("BAY BRIDGE", "WESTBOUND", ""), second=("CRASH AHEAD", "2 LEFT LANES", "BLOCKED"))]}, sources.FREMONT)
    assert two["signs"][0]["pages"] == [["BAY BRIDGE", "WESTBOUND"], ["CRASH AHEAD", "2 LEFT LANES", "BLOCKED"]]
    assert sources.pick_message(two, far)["lines"] == ["CRASH AHEAD", "2 LEFT LANES", "BLOCKED"]


def test_the_feed_is_capped_and_asked_once_with_the_plugin_s_client(monkeypatch):
    calls = []

    class Client:
        def request_json(self, method, url, **kwargs):
            calls.append((method, url, kwargs))
            return type("Response", (), {"data": FEED})()

    monkeypatch.undo()
    monkeypatch.setattr(sources, "get_http_client", lambda: Client())
    context = TaskContext.never_cancelled(deadline_monotonic=time.monotonic() + 30)

    data = sources.fetch_signs(sources.FREMONT, context)

    assert len(calls) == 1 and calls[0][:2] == ("GET", sources.SIGNS_URL)
    assert 256 * 1024 <= calls[0][2]["max_bytes"] <= 512 * 1024
    assert [sign["id"] for sign in data["signs"]] == ["3", "8", "6", "124"]


def _plugin(monkeypatch, tmp_path):
    rows = [_row(f"TVA{index} -- {route} : Place {index}", route, "North", 37.60 + index / 1000, -122.07)
            for index, route in enumerate(["I-880", "I-680", "SR-84", "SR-238", "SR-262", "US-101", "SR-92"])]
    cameras = sources.normalize_cameras({"data": rows}, sources.FREMONT)
    monkeypatch.setenv("BAY_TRAFFIC_CAMS_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(sources, "fetch_cameras", lambda home, context: cameras)

    class Http:
        def request_bytes(self, method, url, **kwargs):
            return _Response(_jpeg())

    monkeypatch.setattr(plugin_module, "get_http_client", lambda: Http())
    return plugin_module.BayTrafficCams({"id": "bay_traffic_cams"})


def _close(payload):
    for frame in payload["frames"].values():
        frame["image"].close()


def test_the_signs_are_a_cached_extra_that_never_decides_the_page_s_health(monkeypatch, tmp_path):
    plugin = _plugin(monkeypatch, tmp_path)
    context = TaskContext.never_cancelled(deadline_monotonic=time.monotonic() + 60)
    settings = {"mainCamera": "TVA0"}

    first = plugin._payload(settings, NOW, allow_network=True, context=context)
    _close(first)
    assert first["sign"]["label"] == "事故" and first["provenance"] is SourceProvenance.LIVE

    def down(*_args):
        raise OSError("offline")

    monkeypatch.setattr(sources, "fetch_signs", down)
    # Within its few fresh minutes the feed is not asked again; after them a failed refresh keeps
    # the last copy for half an hour, then the board goes dark. The page's health never notices.
    soon = plugin._payload(settings, NOW + timedelta(minutes=3), allow_network=True, context=context)
    later = plugin._payload(settings, NOW + timedelta(minutes=20), allow_network=True, context=context)
    gone = plugin._payload(settings, NOW + timedelta(minutes=50), allow_network=True, context=context)
    for payload in (soon, later, gone):
        _close(payload)
    assert soon["sign"]["label"] == later["sign"]["label"] == "事故" and gone["sign"] is None
    assert {payload["provenance"] for payload in (soon, later, gone)} == {SourceProvenance.LIVE}
    # A redraw from cache never fetches.
    monkeypatch.setattr(sources, "fetch_signs", lambda *_args: pytest.fail("a cache-only redraw fetched the signs"))
    redraw = plugin._payload(settings, NOW + timedelta(minutes=20), allow_network=False, context=context)
    _close(redraw)
    assert redraw["sign"]["label"] == "事故"
    # The lead camera here is on I-880 by Hayward: with only the notice, its I-880 sign is the one named.
    monkeypatch.setattr(sources, "fetch_signs", lambda home, _ctx: {"signs": sources.normalize_signs(FEED, home)["signs"][1:]})
    notice = plugin._payload({**settings, "forceRefresh": True}, NOW + timedelta(hours=2), allow_network=True, context=context)
    _close(notice)
    assert (notice["sign"]["route"], notice["sign"]["place"], notice["sign"]["camera"]) == ("I-880", "Hayward", 0)


def _wall(sign=None):
    cameras = [_camera(index) for index in range(5)]
    frames = {item["image"]: {"image": _road(), "taken": (NOW - timedelta(minutes=2)).isoformat()} for item in cameras}
    return {"cameras": cameras, "frames": frames, **({"sign": sign} if sign is not None else {})}


def _record(monkeypatch):
    drawn = []
    original = render.PageCanvas.text
    monkeypatch.setattr(render.PageCanvas, "text", lambda self, xy, text, *args, **kwargs: drawn.append(str(text)) or original(
        self, xy, text, *args, **kwargs))
    osd = render.crt.osd_text
    monkeypatch.setattr(render.crt, "osd_text", lambda draw, xy, text, *args, **kwargs: drawn.append(str(text)) or osd(
        draw, xy, text, *args, **kwargs))
    # Sign legends (the board's lines, the guide signs, the direction plaques) are laid out here.
    legend = render.fit_sign
    monkeypatch.setattr(render, "fit_sign", lambda canvas, text, *args, **kwargs: drawn.append(str(text)) or legend(
        canvas, text, *args, **kwargs))
    return drawn


@pytest.mark.parametrize("style", ["crt", "modern"])
@pytest.mark.parametrize("mode", ["day", "night"])
def test_the_header_carries_the_board_and_the_wall_below_it_stays(monkeypatch, mode, style):
    settings = {"_inkypi_theme": {"mode": mode}, "frameStyle": style}
    message = sources.pick_message(sources.normalize_signs(FEED, sources.FREMONT), [_camera(0), _camera(1, "US-101", 38.0, -122.53)])
    plain = render_page((800, 480), _wall(), settings, NOW)
    drawn = _record(monkeypatch)

    image = render_page((800, 480), _wall(message), settings, NOW)

    # Everything under the header's rule is the page as it was.
    assert image.crop((0, 52, 800, 480)).tobytes() == plain.crop((0, 52, 800, 480)).tobytes()
    assert image.crop((0, 0, 800, 52)).tobytes() != plain.crop((0, 0, 800, 52)).tobytes()
    left, top, right, bottom = image.info["sign_board"]
    assert 200 < left < right < 700 and 0 <= top < bottom <= 52
    # The sign's own three lines, what it reports in Chinese, when it was posted and the camera
    # on its road; the header's note gives its place to them.
    assert {"CRASH N-101", "ATHERTON AVE", "RIGHT LANE BLKD", "事故"} <= set(drawn)
    assert any("00:49" in text for text in drawn) and any("CAM-02" in text for text in drawn)
    assert not any("实时监控" in text for text in drawn)
    # The board is a dark face whatever the page's paper, an amber beacon in each upper corner,
    # and its lettering is the signs' LED amber.
    assert max(image.getpixel((left + 3, (top + bottom) // 2))) < 40
    assert image.getpixel((left + 8, top + 8)) == image.getpixel((right - 8, top + 8)) == render.BOARD_BEACON
    face = image.crop((left + 14, top + 2, right - 14, bottom - 2))
    assert render.BOARD_INK in set(face.get_flattened_data())
    assert render.BOARD_INK == (255, 214, 10)


def test_a_narrow_header_keeps_the_camera_before_the_hour_and_the_count():
    notes = render.board_notes({"posted": "2026-11-11T00:49:56-08:00", "camera": 1, "more": 2})

    assert notes[0] == "00:49 发布 · CAM-02 同路 · 另 2 条" and notes[-1] == "00:49"
    assert notes.index("CAM-02 同路") < notes.index("00:49 发布")
    # The main view and the patrol strip's cameras carry no CAM number on the page.
    assert render.board_notes({"camera": 0})[0] == "主画面同路"
    assert render.board_notes({"camera": 6}, wall=5)[0] == "巡视镜头同路"
    assert render.board_notes({"posted": "2026-11-11T12:29:51-08:00", "camera": None, "more": 0}) == ["12:29 发布", "12:29"]
    assert render.board_notes({"posted": None, "camera": None, "more": 0}) == []


def test_without_a_message_the_header_is_the_one_it_was(monkeypatch):
    drawn = _record(monkeypatch)

    image = render_page((800, 480), _wall(), DAY, NOW)

    assert image.info.get("sign_board") is None
    assert any("实时监控" in text for text in drawn)
    assert image.tobytes() == render_page((800, 480), {**_wall(), "sign": None, "signs_state": "unavailable"}, DAY, NOW).tobytes()
    # A message from a cache with a shape the board does not know is left out, not drawn wrong.
    odd = render_page((800, 480), _wall({"lines": "CRASH", "label": 3}), DAY, NOW)
    assert odd.info.get("sign_board") is None and odd.tobytes() == image.tobytes()


def test_a_notice_on_several_signs_says_how_many(monkeypatch):
    notices = {"signs": sources.normalize_signs(FEED, sources.FREMONT)["signs"][1:]}
    drawn = _record(monkeypatch)

    render_page((800, 480), _wall(sources.pick_message(notices, [])), DAY, NOW)

    assert {"W58O RICHMOND BR", "CLOSURE", "SAT 11PM-SUN 8AM", "封闭预告"} <= set(drawn)
    assert any("3 处" in text for text in drawn)
