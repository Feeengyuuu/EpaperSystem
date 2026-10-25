import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.bay_traffic_cams import bay_traffic_cams as plugin_module  # noqa: E402
from plugins.bay_traffic_cams import sources  # noqa: E402
from plugins.bay_traffic_cams.render import age_label, render_page, route_colour  # noqa: E402
from runtime.refresh_contracts import TaskContext  # noqa: E402

NOW = datetime(2026, 10, 24, 10, 50, tzinfo=sources.PACIFIC)
BASE = "https://cwwp2.dot.ca.gov/data/d4/cctv/image"


def _row(name, route, direction, lat, lon, frequency="15", in_service="true"):
    camera_id = name.split(" ", 1)[0].lower()
    static = {"currentImageURL": f"{BASE}/{camera_id}/{camera_id}.jpg", "referenceImageUpdateFrequency": frequency,
              "referenceImage1UpdateAgoURL": f"{BASE}/{camera_id}/previous/{camera_id}-1.jpg"}
    for step in range(2, 13):
        static[f"referenceImage{step}UpdatesAgoURL"] = f"{BASE}/{camera_id}/previous/{camera_id}-{step}.jpg"
    return {"cctv": {"inService": in_service, "imageData": {"static": static},
                     "location": {"locationName": name, "route": route, "direction": direction,
                                  "latitude": str(lat), "longitude": str(lon), "nearbyPlace": "Fremont"}}}


def test_places_read_as_tidy_names_with_the_chinese_side():
    def place(name):
        return sources.place_label({"locationName": f"TVX -- I-880 : {name}"})

    assert place("MOWRY LOOP OR") == "Mowry Loop 入口"
    assert place("WASHINGTON BL") == "Washington Blvd"
    assert place("W84 at Newark Bl") == "Newark Blvd"
    assert place("AT JNO MILLBRAE AVE OC") == "Millbrae Ave 以北"
    assert place("Just West of Pacheco Boulevard") == "Pacheco Boulevard 以西"
    assert place("AT JCT-84") == "84 号交汇"
    assert place("San Pablo Dam Road Offramp - Looking West") == "San Pablo Dam Road 出口"


def test_cameras_near_home_come_nearest_first_with_three_hourly_stills():
    payload = {"data": [
        _row("TVB12 -- I-680 : WASHINGTON BL", "I-680", "South", 37.53, -121.93, frequency="60"),
        _row("TVB11 -- I-880 : MOWRY LOOP OR", "I-880", "South", 37.54, -121.99),
        _row("TVZ99 -- I-80 : Far Away", "I-80", "West", 38.4, -122.5),
        _row("TVB13 -- I-880 : Down", "I-880", "North", 37.55, -121.99, in_service="false"),
    ]}
    cameras = sources.normalize_cameras(payload, sources.FREMONT)["cameras"]

    assert [camera["id"] for camera in cameras] == ["TVB11", "TVB12"]
    # Every 15 minutes: updates 4, 8 and 12 back are one, two and three hours ago.
    assert [url.rsplit("-", 1)[1] for url in cameras[0]["history"]] == ["4.jpg", "8.jpg", "12.jpg"]
    assert [url.rsplit("-", 1)[1] for url in cameras[1]["history"]] == ["1.jpg", "2.jpg", "3.jpg"]
    assert sources.compass_label(cameras[1]["bearing"]) in {"东", "东南"}
    with pytest.raises(ValueError):
        sources.normalize_cameras({"data": [payload["data"][2]]}, sources.FREMONT)


def test_the_wall_covers_other_roads_before_a_second_view_of_one():
    cameras = [{"id": f"C{index}", "route": route, "direction": direction}
               for index, (route, direction) in enumerate(
                   [("I-880", "South"), ("I-880", "South"), ("I-880", "North"), ("SR-84", "West"), ("I-680", "South")])]

    assert [camera["id"] for camera in sources.choose(cameras, 4)] == ["C0", "C2", "C3", "C4"]
    assert [camera["id"] for camera in sources.choose(cameras, 4, "c3")][0] == "C3"


def test_unavailable_cards_and_black_frames_are_told_from_road_views():
    card = Image.new("RGB", (320, 260), (255, 255, 255))
    ImageDraw.Draw(card).text((60, 100), "Temporarily Unavailable", fill=(0, 0, 90))
    black = Image.new("RGB", (320, 260), (2, 2, 2))
    ImageDraw.Draw(black).rectangle((0, 240, 320, 260), fill=(140, 140, 140))
    road = _road()

    assert sources.is_placeholder(card) and sources.is_placeholder(black)
    assert not sources.is_placeholder(road)


def test_ages_mark_a_still_that_stopped_updating():
    assert age_label({"taken": (NOW - timedelta(minutes=3)).isoformat()}, NOW) == ("3分钟前", False)
    assert age_label({"taken": (NOW - timedelta(hours=2)).isoformat()}, NOW) == ("2小时前", True)
    assert age_label(None, NOW) == ("", False)
    assert route_colour("I-880") != route_colour("SR-84")


def _road():
    """A road view: sky over grey asphalt with white lane lines and the caption bar."""
    image = Image.new("RGB", (320, 260), (150, 190, 230))
    draw = ImageDraw.Draw(image)
    draw.polygon(((0, 240), (130, 90), (190, 90), (320, 240)), fill=(95, 95, 98))
    draw.rectangle((0, 140, 60, 240), fill=(70, 110, 60))
    for x in (150, 170):
        draw.line((x, 95, x + (x - 160) * 6, 240), fill=(245, 245, 245), width=3)
    draw.rectangle((0, 240, 320, 260), fill=(85, 85, 85))
    return image


class _Response:
    def __init__(self, data):
        self.data, self.headers = data, {"Last-Modified": "Sat, 24 Oct 2026 17:48:00 GMT"}


def _jpeg(colour=None):
    from io import BytesIO

    image = Image.new("RGB", (320, 260), colour) if colour else _road()
    buffer = BytesIO()
    image.save(buffer, "JPEG")
    return buffer.getvalue()


def test_a_camera_that_is_down_gives_way_to_the_next_nearest(monkeypatch, tmp_path):
    rows = [_row(f"TVA{index} -- {route} : Place {index}", route, "South", 37.548 + index / 1000, -121.988)
            for index, route in enumerate(["I-880", "I-680", "SR-84", "SR-238", "SR-262", "US-101", "SR-92"])]
    cameras = sources.normalize_cameras({"data": rows}, sources.FREMONT)
    monkeypatch.setenv("BAY_TRAFFIC_CAMS_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(sources, "fetch_cameras", lambda home, context: cameras)

    class Http:
        def request_bytes(self, method, url, **kwargs):
            return _Response(_jpeg((255, 255, 255)) if "tva1" in url else _jpeg())

    monkeypatch.setattr(plugin_module, "get_http_client", lambda: Http())
    plugin = plugin_module.BayTrafficCams({"id": "bay_traffic_cams"})
    context = TaskContext.never_cancelled(deadline_monotonic=time.monotonic() + 60)
    payload = plugin._payload({}, NOW, allow_network=True, context=context)
    try:
        assert [camera["id"] for camera in payload["cameras"]] == ["TVA0", "TVA2", "TVA3", "TVA4", "TVA5"]
        assert len(payload["cameras"][0]["history"]) == 3
        assert all(url in payload["frames"] for url in payload["cameras"][0]["history"])
        image = render_page((800, 480), payload, {"_inkypi_theme": {"mode": "day"}}, NOW)
        assert image.size == (800, 480)
    finally:
        for frame in payload["frames"].values():
            frame["image"].close()


@pytest.mark.parametrize("mode", ["day", "night"])
def test_page_renders_with_no_cameras(mode):
    image = render_page((800, 480), {"cameras": [], "frames": {}}, {"_inkypi_theme": {"mode": mode}}, NOW)

    assert image.size == (800, 480)


def test_control_room_style_puts_each_still_on_a_crt_monitor_with_recorder_text():
    from plugins.bay_traffic_cams import crt, render

    def camera(index):
        return {"id": f"TVA{index}", "route": "I-880", "direction": "South", "place": f"Place {index}",
                "image": f"https://cwwp2.dot.ca.gov/{index}.jpg", "history": [], "distance_km": 2.0 + index,
                "bearing": 200}

    cameras = [camera(index) for index in range(5)]
    frames = {item["image"]: {"image": _road(), "taken": (NOW - timedelta(minutes=2)).isoformat()} for item in cameras}
    payload = {"cameras": cameras, "frames": frames}

    image = render_page((800, 480), payload, {"_inkypi_theme": {"mode": "day"}, "frameStyle": "crt"}, NOW)
    plain = render_page((800, 480), payload, {"_inkypi_theme": {"mode": "day"}}, NOW)

    console = render._rgb(crt.PALETTE["paper"])
    assert image.getpixel((790, 475)) == console and plain.getpixel((790, 475)) == (255, 255, 255)
    x0, y0, x1, _y1 = render.MAIN
    assert image.getpixel((x0 + 4, y0 + 60)) == crt.BEZEL
    # A red REC sits at the top right of the main screen.
    corner = image.crop((x1 - 80, y0 + 9, x1 - 9, y0 + 34))
    pixels = [corner.getpixel((x, y)) for x in range(corner.width) for y in range(corner.height)]
    assert any(pixel[0] > 200 and pixel[1] < 90 and pixel[2] < 90 for pixel in pixels)
