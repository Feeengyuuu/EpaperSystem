"""Nearby camera evidence must agree with the road and remain optional/offline-safe."""

import hashlib
import io
import json
from datetime import timedelta
from types import SimpleNamespace

from PIL import Image
import pytest

from plugins.bay_commute import nearby_cameras as subject
from plugins.bay_commute.bay_commute import BayCommute
from plugins.bay_commute.command_render import render_page
from runtime.refresh_contracts import TaskCancelled, TaskDeadlineExceeded
from tests.test_bay_commute import NOW, context, road_data, tide_data


URL = "https://cwwp2.dot.ca.gov/data/d4/cctv/image/tvc53.jpg"
CAMERA = {"id": "TVC53", "route": "880", "direction": "North",
          "latitude": 37.3342, "longitude": -121.9366, "image": URL}


def closure(**kwargs):
    return {**road_data()["items"][0], "begin_lat": CAMERA["latitude"], "begin_lon": CAMERA["longitude"], **kwargs}


def payload(rows=None):
    return {"roads": {"state": "live", "fetched_at": NOW.isoformat(),
                      "data": {"items": rows if rows is not None else [closure()], "source_updated_at": NOW.isoformat()}},
            "tides": {"state": "live", "fetched_at": NOW.isoformat(), "data": tide_data()},
            "buoy": {"state": "unavailable", "data": {}}, "routes": ["I-880"], "station": "9414523"}


def stored_frame(root, fetched=NOW):
    root.mkdir(parents=True)
    # A textured grayscale road-like frame avoids the provider's unavailable-frame detector.
    image = Image.new("RGB", (320, 200))
    image.putdata([((40 if (x // 40 + y // 40) % 2 else 190),) * 3 for y in range(200) for x in range(320)])
    raw = io.BytesIO()
    image.save(raw, format="JPEG")
    name = hashlib.sha256(URL.encode()).hexdigest()[:24]
    (root / f"{name}.jpg").write_bytes(raw.getvalue())
    (root / "frames.json").write_text(json.dumps({name: {"fetched": fetched.isoformat()}}))
    return raw.getvalue()


def test_matching_requires_real_coordinates_road_direction_and_distance():
    wrong = [{**CAMERA, "route": "680"}, {**CAMERA, "direction": "South"},
             {**CAMERA, "latitude": 37.39}, {**CAMERA, "latitude": None},
             {**CAMERA, "image": "https://example.com/untrusted.jpg"}]
    assert [item["id"] for item in subject.matching_cameras(closure(), [*wrong, CAMERA])] == ["TVC53"]
    assert subject.matching_cameras(closure(begin_lat=None), [CAMERA]) == []
    assert subject.matching_cameras(closure(direction=""), [CAMERA]) == []
    assert len(subject.matching_cameras(closure(direction="North / South"), [CAMERA])) == 1


def test_cached_redraw_reuses_shared_camera_without_network_or_writes(tmp_path, monkeypatch):
    shared = tmp_path / "traffic"
    (shared / "sources").mkdir(parents=True)
    (shared / "sources/cameras-home.json").write_text(json.dumps({"fetched_at": NOW.isoformat(),
                                                                "data": {"cameras": [CAMERA]}}))
    stored_frame(shared / "frames")
    monkeypatch.setenv("BAY_TRAFFIC_CAMS_CACHE_DIR", str(shared))
    own = tmp_path / "commute"
    monkeypatch.setenv("BAY_COMMUTE_CACHE_DIR", str(own))
    monkeypatch.setattr(subject, "get_http_client", lambda: (_ for _ in ()).throw(AssertionError("network forbidden")))
    before = {str(p): p.stat().st_mtime_ns for p in shared.rglob("*") if p.is_file()}
    results = subject.load_nearby(BayCommute({"id": "bay_commute"}), payload(), NOW,
                                  context=context(), allow_network=False)
    try:
        assert results[closure()["id"]]["camera_id"] == "TVC53"
        assert results[closure()["id"]]["distance_km"] == 0
        assert not results[closure()["id"]]["stale"]
        assert not own.exists()
        assert before == {str(p): p.stat().st_mtime_ns for p in shared.rglob("*") if p.is_file()}
    finally:
        for frame in results.values():
            if frame.get("image") is not None:
                frame["image"].close()


def test_stills_expire_without_inventing_freshness(tmp_path, monkeypatch):
    stored_frame(tmp_path / "frames", NOW - timedelta(hours=7))
    name = hashlib.sha256(URL.encode()).hexdigest()[:24]
    assert subject._read_frame(tmp_path / "frames", name, NOW) is None
    meta = tmp_path / "frames/frames.json"
    meta.write_text(json.dumps({name: {"fetched": (NOW - timedelta(hours=2)).isoformat()}}))
    result = subject._read_frame(tmp_path / "frames", name, NOW)
    assert result["stale"] and result["fetched"] == (NOW - timedelta(hours=2)).isoformat()
    result["image"].close()


def test_directory_refresh_and_usable_still_are_cached(tmp_path, monkeypatch):
    monkeypatch.setenv("BAY_COMMUTE_CACHE_DIR", str(tmp_path / "commute"))
    monkeypatch.setenv("BAY_TRAFFIC_CAMS_CACHE_DIR", str(tmp_path / "traffic"))
    raw = stored_frame(tmp_path / "sample")
    calls = []
    raw_camera = {"cctv": {"inService": "true", "location": {**CAMERA, "locationName": "TVC53 road"},
                            "imageData": {"static": {"currentImageURL": URL}}}}
    def directory(*args, **kwargs):
        calls.append("directory")
        return SimpleNamespace(data={"data": [raw_camera]})
    def frame(*args, **kwargs):
        calls.append("frame")
        return SimpleNamespace(data=raw)
    monkeypatch.setattr(subject, "get_http_client", lambda: SimpleNamespace(request_json=directory, request_bytes=frame))
    plugin = BayCommute({"id": "bay_commute"})
    for minute in (0, 5):
        result = subject.load_nearby(plugin, payload(), NOW + timedelta(minutes=minute),
                                    context=context(), allow_network=True)
        assert result[closure()["id"]]["status"] == "available"
        result[closure()["id"]]["image"].close()
    assert calls == ["directory", "frame"]


def test_unavailable_nearest_camera_falls_back_then_reuses_that_fresh_still(tmp_path, monkeypatch):
    monkeypatch.setenv("BAY_COMMUTE_CACHE_DIR", str(tmp_path / "commute"))
    monkeypatch.setenv("BAY_TRAFFIC_CAMS_CACHE_DIR", str(tmp_path / "traffic"))
    raw = stored_frame(tmp_path / "sample")
    unavailable = io.BytesIO()
    Image.new("RGB", (320, 200), "white").save(unavailable, "JPEG")
    farther = {**CAMERA, "id": "TVC54", "latitude": CAMERA["latitude"] + .002,
               "image": URL.replace("tvc53", "tvc54")}
    monkeypatch.setattr(subject, "_catalog", lambda *a, **k: [CAMERA, farther])
    calls = []
    def frame(method, url, **kwargs):
        calls.append(url)
        return SimpleNamespace(data=unavailable.getvalue() if url == URL else raw)
    monkeypatch.setattr(subject, "get_http_client", lambda: SimpleNamespace(request_bytes=frame))
    plugin = BayCommute({"id": "bay_commute"})
    for minute in (0, 5):
        result = subject.load_nearby(plugin, payload(), NOW + timedelta(minutes=minute),
                                    context=context(), allow_network=True)
        assert result[closure()["id"]]["camera_id"] == "TVC54"
        result[closure()["id"]]["image"].close()
    assert calls == [URL, farther["image"]]


def test_optional_deadline_falls_back_but_owner_cancellation_propagates(tmp_path, monkeypatch):
    monkeypatch.setenv("BAY_COMMUTE_CACHE_DIR", str(tmp_path / "commute"))
    monkeypatch.setenv("BAY_TRAFFIC_CAMS_CACHE_DIR", str(tmp_path / "traffic"))
    plugin = BayCommute({"id": "bay_commute"})
    monkeypatch.setattr(subject, "_catalog", lambda *a, **k: (_ for _ in ()).throw(TaskDeadlineExceeded("optional")))
    results = subject.load_nearby(plugin, payload(), NOW, context=context(), allow_network=True)
    assert results[closure()["id"]]["status"] == "no_match"
    monkeypatch.setattr(subject, "_catalog", lambda *a, **k: (_ for _ in ()).throw(TaskCancelled("owner")))
    with pytest.raises(TaskCancelled):
        subject.load_nearby(plugin, payload(), NOW, context=context(), allow_network=True)


@pytest.mark.parametrize("mode", ["day", "night"])
def test_real_render_keeps_arbitrary_places_timestamps_and_empty_state_readable(mode):
    rows = [closure(id="long", place="A very long unfamiliar municipality with a distant interchange", route="I-80"),
            closure(id="chinese", place="旧金山湾区中文地点", kind="Full", facility="Off Ramp", indefinite=True)]
    page = render_page((800, 480), payload(rows), {"themeMode": mode}, NOW)
    bounds = page.info["bay_text_bounds"]
    assert any(text == "出口匝道全封闭" for text, _ in bounds)
    assert any(text == "时间未定" for text, _ in bounds)
    assert sum(text == "示意" for text, _ in bounds) == 2
    assert all(0 <= box[0] <= box[2] <= 800 and 0 <= box[1] <= box[3] <= 480 for _, box in bounds)
    assert page.size == (800, 480)
    page.close()
