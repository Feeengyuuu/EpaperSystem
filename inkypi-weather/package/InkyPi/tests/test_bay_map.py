import hashlib
import math

import pytest

from plugins.bay_commute.map_view import MAP_PATH, project_coordinate, read_manifest, render_map


def _wgs84(x, y):
    return math.degrees(2 * math.atan(math.exp(y / 6378137)) - math.pi / 2), math.degrees(x / 6378137)


def test_asset_hash_dimensions_and_actual_projected_extent_are_consistent():
    manifest = read_manifest()
    assert hashlib.sha256(MAP_PATH.read_bytes()).hexdigest() == manifest["asset_sha256"]
    extent = manifest["mercator_bounds"]
    assert (extent["xmax"] - extent["xmin"]) / (extent["ymax"] - extent["ymin"]) == pytest.approx(184 / 282)
    assert manifest["source"].startswith("USGS")
    assert "Public domain" in manifest["license"]


def test_actual_extent_centre_projects_to_centre_without_using_requested_bbox():
    extent = read_manifest()["mercator_bounds"]
    lat, lon = _wgs84((extent["xmin"] + extent["xmax"]) / 2, (extent["ymin"] + extent["ymax"]) / 2)
    assert project_coordinate(lat, lon) == pytest.approx((92, 141), abs=0.001)
    assert project_coordinate(lat, lon, size=(300, 282)) == pytest.approx((150, 141), abs=0.001)


def test_known_cities_land_in_correct_order_and_inside_real_map():
    san_ramon = project_coordinate(37.7805, -121.978)
    fremont = project_coordinate(37.54827, -121.98857)
    newark = project_coordinate(37.52966, -122.04024)
    san_jose = project_coordinate(37.33939, -121.89496)
    assert 0 < san_ramon[1] < fremont[1] < san_jose[1] < 282
    assert newark[0] < fremont[0] < san_jose[0]


@pytest.mark.parametrize("lat,lon", [(None,None), (0,0), (90,0), (float("nan"),-122),
                                    (37.7,float("inf")), (37.78,-123), (38.1,-122), (True,-122)])
def test_missing_invalid_and_outside_locations_never_get_a_fake_marker(lat, lon):
    assert project_coordinate(lat, lon) is None


def test_marker_numbers_follow_card_order_and_cached_map_needs_no_network(monkeypatch):
    import socket
    monkeypatch.setattr(socket, "socket", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network forbidden")))
    rows = [{"id":"road1","begin_lat":37.54827,"begin_lon":-121.98857},
            {"id":"road2","begin_lat":None,"begin_lon":None},
            {"id":"road3","begin_lat":37.7805,"begin_lon":-121.978}]
    before = MAP_PATH.stat().st_mtime_ns
    day, night = render_map(rows), render_map(rows, night=True)
    assert day.size == night.size == (184,282)
    assert day.mode == night.mode == "RGB"
    assert day.info["map_markers"] == night.info["map_markers"]
    assert [row["number"] for row in day.info["map_markers"]] == [1,2,3]
    assert [row["visible"] for row in day.info["map_markers"]] == [True,False,True]
    assert MAP_PATH.stat().st_mtime_ns == before
    assert "USGS" in day.info["map_attribution"]


def test_actual_closure_markers_do_not_obscure_san_ramon_or_fremont_labels():
    rows = [{"id":"680", "begin_lat":37.759613, "begin_lon":-121.966449},
            {"id":"84", "begin_lat":37.589684, "begin_lon":-121.957626},
            {"id":"880", "begin_lat":37.334205, "begin_lon":-121.936595}]
    image = render_map(rows)
    assert image.info["markers"] == image.info["map_markers"]
    for item in image.info["map_labels"]:
        if item["label"] not in {"San Ramon", "Fremont"}:
            continue
        left, top, right, bottom = item["box"]
        for marker in image.info["markers"]:
            x, y = marker["pixel"]
            assert not (left < x + 9 and right > x - 9 and top < y + 9 and bottom > y - 9)
