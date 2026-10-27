"""The Bay map's elevation contours: an offline 3DEP layer at the map's size, drawn over the relief."""

import hashlib
import json

import numpy
from PIL import Image

from plugins.bay_alerts import map_view
from plugins.bay_alerts.render import DAY, NIGHT

ASSETS = map_view.ASSETS


def test_the_contour_layer_matches_its_record_and_the_map():
    record = json.loads((ASSETS / f"{map_view.CONTOURS}.json").read_text(encoding="utf-8"))
    path = ASSETS / f"{map_view.CONTOURS}.png"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == record["sha256"]
    with Image.open(path) as image, Image.open(ASSETS / f"{map_view.BAY_MAP}.png") as layers:
        lines = numpy.asarray(image.convert("L"))
        water = numpy.asarray(layers.convert("RGB").split()[0]) > 127
        assert image.size == tuple(record["image_size"]) == layers.size
    assert set(numpy.unique(lines)) == {0, record["values"]["minor"], record["values"]["index"]}
    assert not lines[water].any()
    # Contours mark the hills, not the whole land: a small share of the map.
    assert 0.02 < (lines > 0).mean() < 0.35


def test_contours_draw_in_the_palette_and_skip_a_missing_layer(monkeypatch):
    for palette in (DAY, NIGHT):
        colours = {key: map_view.ImageColor.getrgb(value) for key, value in palette.items() if isinstance(value, str)}
        image = Image.new("RGB", (440, 408), colours["paper"])
        map_view._draw_contours(image, colours, image.size)
        pixels = set(image.get_flattened_data())
        assert colours["contour"] in pixels and colours["contour_index"] in pixels
    monkeypatch.setattr(map_view, "CONTOURS", "missing-layer")
    blank = Image.new("RGB", (440, 408), (255, 255, 255))
    map_view._draw_contours(blank, {"contour": (1, 2, 3), "contour_index": (4, 5, 6)}, blank.size)
    assert blank.getbbox() is not None and set(blank.get_flattened_data()) == {(255, 255, 255)}
