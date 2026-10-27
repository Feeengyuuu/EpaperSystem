"""Real-photo selection, metadata consistency and exact-frame web cache boundaries."""

from datetime import datetime, timedelta
from io import BytesIO
import base64
from types import SimpleNamespace

from PIL import Image
import pytest

from plugins.planet_now import mars_motion, sources
from plugins.planet_now.planet_now import PlanetNow


def _frame(direction, camera="MCZ_RIGHT", product="EBY", sol=2020):
    name = f"{camera}-{direction}-{product}"
    return {"sol": sol, "sample_type": "Full", "camera": {"instrument": camera},
            "imageid": f"ZR0_2020_0846179643_598{product}_N0920000ZCAM10053_1100LMJ",
            "extended": {"mastAz": str(direction), "mastEl": "-1.8", "dimension": "(1648,1200)"},
            "date_taken_utc": f"2026-10-26T05:{33 + direction // 15:02}:00",
            "image_files": {"medium": f"https://mars.nasa.gov/{name}-m.jpg",
                            "large": f"https://mars.nasa.gov/{name}-l.jpg"}}


def test_album_avoids_stereo_and_processing_duplicates_binds_every_timestamp():
    raw = [_frame(direction, camera, product) for direction in (0, 30, 60, 90)
           for camera in ("MCZ_RIGHT", "MCZ_LEFT") for product in ("EBY", "ECM")]
    album = sources.mars_album({"images": raw})
    assert len(album) == 3
    assert len({row["taken_utc"] for row in album}) == 3
    assert all(row["sol"] == 2020 and "EBY" in row["image"] for row in album)
    assert len({row["image"].split("-")[-3] for row in album}) == 3


@pytest.mark.parametrize("legacy", [False, True])
def test_refresh_rotates_only_available_images_and_supports_old_single_photo_cache(monkeypatch, tmp_path, legacy):
    from plugins.planet_now import planet_now

    photos = [{"sol": n, "camera": "桅杆相机", "taken_utc": f"2026-10-{20+n}T05:00:00", "image": f"https://mars.nasa.gov/{n}.jpg"}
              for n in range(3)]
    mars = dict(photos[0]) if legacy else {**photos[0], "photos": photos}
    book = SimpleNamespace(load=lambda source, *_a, **_kw: {"data": mars if source.name == "mars" else {}})
    plugin = PlanetNow({"id": "planet_now"})
    monkeypatch.setattr(plugin, "_book", lambda: book)
    monkeypatch.setattr(plugin, "cache_dir", lambda **_kw: tmp_path)
    available = {photos[0]["image"]: Image.new("RGB", (100, 100)), photos[2]["image"]: Image.new("RGB", (100, 100))}
    seen = []
    def thumbnails(urls, *_a, **kwargs):
        seen.append((urls, kwargs["cached_only"]))
        return {url: image for url, image in available.items() if url in urls}
    monkeypatch.setattr(planet_now, "load_thumbnails", thumbnails)
    monkeypatch.setattr(planet_now, "combined_provenance", lambda _: {})
    now = datetime(2026, 10, 27, 10, 0, tzinfo=sources.PACIFIC)
    a, _ = plugin._refresh({}, now, allow_network=False, context=None)
    b, _ = plugin._refresh({}, now + timedelta(minutes=13), allow_network=False, context=None)
    assert all(cached_only for _, cached_only in seen)
    if legacy:
        assert a["mars"]["data"]["image"] == b["mars"]["data"]["image"] == photos[0]["image"]
    else:
        assert a["mars"]["data"]["image"] != b["mars"]["data"]["image"]
        for response in (a, b):
            selected = response["mars"]["data"]
            expected = next(p for p in photos if p["image"] == selected["image"])
            assert selected["taken_utc"] == expected["taken_utc"]
            assert len(selected["photos"]) == 2


def test_motion_package_is_bound_to_base_pixels_and_read_has_no_writes(tmp_path):
    base = Image.new("RGB", (800, 480), (32, 44, 55))
    photo = Image.new("RGB", (371, 270), (150, 90, 40))
    row = {"sol": 2020, "camera": "桅杆相机", "taken_utc": "2026-10-26T05:34:00Z", "image": "https://mars.nasa.gov/a.jpg"}
    directory = tmp_path / "motion"
    mars_motion.publish_motion(directory, base, {"mars": {"data": row}}, {},
                               datetime(2026, 10, 27, tzinfo=sources.PACIFIC), {row["image"]: photo})
    saved = tmp_path / "base.png"
    base.save(saved)
    before = {p.name: p.stat().st_mtime_ns for p in directory.iterdir()}
    result = mars_motion.read_motion(saved, directory=directory)
    after = {p.name: p.stat().st_mtime_ns for p in directory.iterdir()}
    assert before == after
    assert result["slides"][0]["taken_utc"] == row["taken_utc"]
    assert result["box"] == [320, 304, 432, 468]
    decoded = Image.open(BytesIO(base64.b64decode(result["base"].split(",", 1)[1])))
    assert decoded.tobytes() == base.tobytes()
    cache_path = next(directory.glob("*.json"))
    original = cache_path.read_bytes()
    for corrupt in (b"[]", b"null", b"invalid json"):
        cache_path.write_bytes(corrupt)
        assert mars_motion.read_motion(saved, directory=directory) is None
    cache_path.write_bytes(original)
    base.putpixel((0, 0), (0, 0, 0))
    base.save(saved)
    assert mars_motion.read_motion(saved, directory=directory) is None
    assert mars_motion.read_motion(saved, directory=tmp_path / "missing") is None
    assert not (tmp_path / "missing").exists()


def test_current_motion_uses_authoritative_frame_and_static_route_unchanged(tmp_path, monkeypatch):
    from flask import Flask
    from blueprints.main import main_bp

    path = tmp_path / "committed.png"
    path.write_bytes(b"committed")
    commit = SimpleNamespace(image_path=path, commit_id="id", committed_at="2026-10-27T17:00:00Z")
    app = Flask(__name__)
    app.config["DISPLAY_MANAGER"] = SimpleNamespace(transaction=SimpleNamespace(current=lambda: commit))
    app.register_blueprint(main_bp)
    calls = []
    monkeypatch.setattr(mars_motion, "read_motion", lambda p: calls.append(p) or {"schema": 1})
    response = app.test_client().get("/api/current_image?motion=mars")
    assert response.get_json() == {"motion": {"schema": 1}}
    assert response.headers["Cache-Control"] == "no-store"
    assert calls == [path]
    assert app.test_client().get("/api/current_image").data == b"committed"


def test_old_photo_cache_upgrades_on_network_refresh_without_changing_cache_only(monkeypatch, tmp_path):
    from plugins.planet_now import planet_now

    old = {"image": "https://mars.nasa.gov/old.jpg", "sol": 2019}
    new = {"image": "https://mars.nasa.gov/new.jpg", "sol": 2020}
    forced = []
    def load(source, *_args, **kwargs):
        if source.name != "mars":
            return {"data": {}}
        forced.append(kwargs["force"])
        return {"data": {**new, "photos": [new]} if kwargs["force"] else old}
    plugin = PlanetNow({"id": "planet_now"})
    monkeypatch.setattr(plugin, "_book", lambda: SimpleNamespace(load=load))
    monkeypatch.setattr(plugin, "cache_dir", lambda **_kw: tmp_path)
    monkeypatch.setattr(planet_now, "load_thumbnails", lambda *_a, **_kw: {})
    monkeypatch.setattr(planet_now, "combined_provenance", lambda _: {})
    now = datetime(2026, 10, 27, 10, 0, tzinfo=sources.PACIFIC)
    payload, _ = plugin._refresh({}, now, allow_network=True, context=None)
    assert forced == [False, True]
    assert payload["mars"]["data"]["image"] == new["image"]
    forced.clear()
    payload, _ = plugin._refresh({}, now, allow_network=False, context=None)
    assert forced == [False]
    assert payload["mars"]["data"]["image"] == old["image"]
