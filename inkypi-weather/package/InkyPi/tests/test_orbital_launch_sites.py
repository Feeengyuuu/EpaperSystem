"""Location identities must remain accurate and fit beside the place and flag."""

import hashlib
import json
from datetime import datetime, timezone

import pytest
from PIL import Image, ImageChops, ImageDraw

from plugins.orbital_signal import launch_sites
from plugins.orbital_signal.orbital_signal import DAY_PALETTE, NIGHT_PALETTE, OrbitalSignal


KNOWN_SITES = [
    ("Vandenberg SFB, CA, USA", "US", "vandenberg"),
    ("Cape Canaveral SFS, FL, USA", "US", "cape_canaveral"),
    ("Kennedy Space Center, FL, USA", "US", "nasa"),
    ("Wallops Island, Virginia, USA", "US", "nasa"),
    ("Mahia Peninsula, New Zealand", "NZ", "rocketlab"),
    ("Guiana Space Centre, French Guiana", "GF", "csg"),
    ("Kourou, France", "FR", "csg"),
    ("Satish Dhawan Space Centre, India", "IN", "isro"),
    ("Tanegashima Space Center, Japan", "JP", "jaxa"),
    ("Uchinoura Space Center, Japan", "JP", "jaxa"),
    ("Baikonur Cosmodrome, Kazakhstan", "KZ", "tsenki"),
    ("Vostochny Cosmodrome, Russia", "RU", "tsenki"),
]


@pytest.mark.parametrize("location,country,key", KNOWN_SITES)
def test_verified_location_matches_and_operator_never_comes_from_rocket_provider(location, country, key):
    launch = {"location": location, "country_code": country, "provider": "Unrelated Rocket Company"}
    assert launch_sites.site_mark_key(launch) == key


@pytest.mark.parametrize("launch", [
    {}, {"location": "Wenchang Space Launch Site, China", "country_code": "CN", "provider": "CASC"},
    {"location": "Plesetsk Cosmodrome, Russia", "country_code": "RU", "provider": "Roscosmos"},
    {"location": "Vandenberg", "country_code": "NZ"},
    {"location": "Unannounced pad", "provider": "NASA", "provider_id": 44},
    {"location": "../../nasa", "country_code": "US"},
])
def test_unknown_or_inconsistent_location_has_no_invented_identity(launch):
    assert launch_sites.site_mark(launch, DAY_PALETTE) is None


def test_names_accept_missing_country_accents_and_irregular_spacing():
    assert launch_sites.site_mark_key({"location": "  MĀHIA   Peninsula "}) == "rocketlab"
    assert launch_sites.site_mark_key({"location": "Rocket Lab Launch Complex 1", "country_code": "nz"}) == "rocketlab"


def test_every_offline_mark_has_verified_provenance_and_matches_recorded_hash():
    manifest = json.loads((launch_sites.ASSET_DIR / "source.json").read_text(encoding="utf-8"))
    assert {record[0] for record in launch_sites.SITE_IDENTITIES} == set(manifest["marks"])
    for record in manifest["marks"].values():
        path = launch_sites.ASSET_DIR / record["file"]
        assert record["official_pages"] and record["role"] in {"site_identity", "site_operator"}
        if "sha256" in record:
            assert hashlib.sha256(path.read_bytes()).hexdigest() == record["sha256"]
        else:
            assert (launch_sites.ASSET_DIR / record["source_manifest"]).is_file()
        with Image.open(path) as image:
            assert image.mode == "RGBA" and image.getbbox()


@pytest.mark.parametrize("palette", [DAY_PALETTE, NIGHT_PALETTE], ids=["day", "night"])
def test_marks_remain_bounded_visible_and_do_not_mutate_the_cached_original(palette):
    for location, country, _key in KNOWN_SITES:
        launch = {"location": location, "country_code": country}
        mark = launch_sites.site_mark(launch, palette)
        assert 0 < mark.width <= 48 and 0 < mark.height <= 24
        canvas = Image.new("RGB", mark.size, palette["paper"])
        canvas.paste(mark, (0, 0), mark)
        assert ImageChops.difference(canvas, Image.new("RGB", mark.size, palette["paper"])).getbbox()
        first = mark.tobytes()
        mark.paste((0, 0, 0, 0), (0, 0, mark.width, mark.height))
        assert launch_sites.site_mark(launch, palette).tobytes() == first


class LayoutDraw:
    def __init__(self, image):
        self.draw = ImageDraw.Draw(image)
        self.texts = []

    def text(self, xy, text, **kwargs):
        self.texts.append((text, self.draw.textbbox(xy, text, font=kwargs.get("font"))))
        return self.draw.text(xy, text, **kwargs)

    def __getattr__(self, name):
        return getattr(self.draw, name)


@pytest.mark.parametrize("palette", [DAY_PALETTE, NIGHT_PALETTE], ids=["day", "night"])
def test_site_marks_never_cover_name_flag_time_or_facts(monkeypatch, palette):
    plugin = OrbitalSignal({"id": "orbital_signal"})
    now = datetime(2026, 11, 15, tzinfo=timezone.utc)
    for location, country, _key in KNOWN_SITES:
        canvas = Image.new("RGB", (800, 480), palette["paper"])
        draw = LayoutDraw(canvas)
        launch = {"location": location, "country_code": country}
        # Capture the actual rectangles pasted by Pillow, including the flag.
        boxes = []
        real_paste = canvas.paste

        def paste(art, box=None, mask=None):
            boxes.append((box[0], box[1], box[0] + art.width, box[1] + art.height))
            return real_paste(art, box, mask)

        monkeypatch.setattr(canvas, "paste", paste)
        plugin._draw_when_and_where(canvas, draw, launch, now, now, 142, 448, 236, palette, None)
        label, bounds = draw.texts[-1]
        assert label and bounds[0] >= 142 and bounds[2] <= 448
        mark_box, flag_box = boxes[-2:]
        # Some font glyphs overhang their origin by a pixel; still keep clear air.
        assert mark_box[2] + 4 <= bounds[0]
        assert bounds[2] < flag_box[0] and flag_box[2] <= 448
        assert 252 <= mark_box[1] < mark_box[3] <= 280  # Below time, above facts rule at 284.


@pytest.mark.parametrize("broken", [False, True], ids=["missing", "corrupt"])
def test_missing_or_corrupt_local_art_preserves_pin_name_and_flag(monkeypatch, tmp_path, broken):
    if broken:
        (tmp_path / "vandenberg.png").write_bytes(b"not an image")
    launch_sites._source_mark.cache_clear()
    monkeypatch.setattr(launch_sites, "ASSET_DIR", tmp_path)
    plugin = OrbitalSignal({"id": "orbital_signal"})
    canvas = Image.new("RGB", (800, 480), "white")
    draw = LayoutDraw(canvas)
    icons = []
    monkeypatch.setattr(plugin, "_paste_icon", lambda _image, key, *args: icons.append(key))
    now = datetime(2026, 11, 15, tzinfo=timezone.utc)
    try:
        plugin._draw_when_and_where(canvas, draw, {"location": "Vandenberg SFB", "country_code": "US"},
                                    now, now, 142, 448, 236, DAY_PALETTE, None)
        assert "pin" in icons and draw.texts[-1][0] == "VANDENBERG SFB"
        assert len(canvas.crop((draw.texts[-1][1][2] + 1, 256, 448, 274)).getcolors(4096)) > 12
    finally:
        launch_sites._source_mark.cache_clear()
