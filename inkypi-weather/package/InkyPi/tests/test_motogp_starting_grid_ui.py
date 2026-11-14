"""The official MotoGP race grid replaces only the header art, never a partial field."""

from copy import deepcopy

import pytest
from PIL import Image, ImageChops, ImageDraw

from plugins.sports_dashboard import motogp_bikes
from plugins.sports_dashboard.common import _ACTIVE_COLORS, DAY_COLORS, DEEP_NIGHT_COLORS
from plugins.sports_dashboard.sports_dashboard import SportsDashboard


BOX = (168, 2, 463, 48)


def _card(count=22):
    return {"sport": "MOTOGP", "status": "NEXT", "weekend": {"id": "current-event", "start": "2026-03-27"},
            "sessions": [{"id": "race-session", "type": "RAC"}],
            "grid": {"event_id": "current-event", "kind": "official", "target_type": "RAC",
                     "session_id": "race-session", "complete": True, "season": 2026,
                     "rows": [{"pos": index + 1, "number": index + 10, "constructor": "Ducati"}
                              for index in range(count)]}}


def _render(monkeypatch, card, colours=DAY_COLORS, box=BOX):
    plugin = SportsDashboard({"id": "sports_dashboard"})
    texts, bikes = [], []
    original = plugin._draw_text_in_box

    def text(draw, bounds, label, *args, **kwargs):
        texts.append((str(label), bounds))
        return original(draw, bounds, label, *args, **kwargs)

    monkeypatch.setattr(plugin, "_draw_text_in_box", text)

    def bike(row, season, size, panel):
        art = Image.new("RGBA", size, (120, 80, row["number"], 255))
        art.info["bike_number"] = row["number"]
        art.info["season"] = season
        return art

    paste = Image.Image.paste

    def record_paste(image, art, xy, *args, **kwargs):
        if isinstance(art, Image.Image) and "bike_number" in art.info:
            bikes.append((art.info["bike_number"], (xy[0], xy[1], xy[0] + art.width, xy[1] + art.height),
                          art.info["season"]))
        return paste(image, art, xy, *args, **kwargs)

    monkeypatch.setattr(motogp_bikes, "load_bike", bike)
    monkeypatch.setattr(Image.Image, "paste", record_paste)
    token = _ACTIVE_COLORS.set(colours)
    try:
        image = Image.new("RGB", (556, 268), colours["panel"])
        before = image.copy()
        shown = plugin._draw_motogp_starting_grid(image, ImageDraw.Draw(image), box, card)
    finally:
        _ACTIVE_COLORS.reset(token)
    return shown, texts, bikes, ImageChops.difference(before, image).getbbox()


@pytest.mark.parametrize("colours", [DAY_COLORS, DEEP_NIGHT_COLORS], ids=["day", "night"])
@pytest.mark.parametrize("count", [21, 22, 24, 27, 30])
def test_every_bike_fits_without_overlap_or_numeric_labels(monkeypatch, colours, count):
    card = _card(count)
    shown, texts, bikes, bounds = _render(monkeypatch, card, colours)
    assert shown and not texts
    assert {number for number, _box, _season in bikes} == {row['number'] for row in card["grid"]["rows"]}
    assert len(bikes) == count
    assert {season for _number, _box, season in bikes} == {2026}
    for _number, box, _season in bikes:
        assert BOX[0] <= box[0] < box[2] <= BOX[2]
        assert BOX[1] <= box[1] < box[3] <= BOX[3]
        assert box[3] - box[1] == 14
    boxes = [box for _number, box, _season in bikes]
    for index, one in enumerate(boxes):
        for other in boxes[index + 1:]:
            assert one[2] <= other[0] or other[2] <= one[0] or one[3] <= other[1] or other[3] <= one[1]
    assert bounds and BOX[0] <= bounds[0] < bounds[2] <= BOX[2] and BOX[1] <= bounds[1] < bounds[3] <= BOX[3]


def test_official_empty_places_stay_empty_and_input_order_does_not_reorder_them(monkeypatch):
    card = _card(24)
    card["grid"]["rows"] = [row for row in reversed(card["grid"]["rows"]) if row["pos"] not in {2, 11}]
    shown, texts, bikes, _bounds = _render(monkeypatch, card)
    assert shown
    labels = {number: box for number, box, _season in bikes}
    assert not texts and 11 not in labels and 20 not in labels
    assert 33 in labels
    assert labels[10][0] > labels[13][0] > labels[16][0]
    assert labels[10][1] == labels[13][1] == labels[16][1]


@pytest.mark.parametrize("change", [
    lambda card: card.update(grid={}),
    lambda card: card.update(grid="invalid"),
    lambda card: card["grid"].update(complete=False),
    lambda card: card["grid"].update(complete="yes"),
    lambda card: card["grid"].update(kind="qualifying"),
    lambda card: card["grid"].update(target_type="SPR"),
    lambda card: card["grid"].update(event_id="previous-event"),
    lambda card: card["grid"].update(session_id="previous-race"),
    lambda card: card["grid"].update(session_id=None),
    lambda card: card.update(sessions=[{"id": "race-session", "type": "SPR"}]),
    lambda card: card["grid"].update(rows=[]),
    lambda card: card["grid"]["rows"][0].update(pos=0),
    lambda card: card["grid"]["rows"][0].update(pos=True),
    lambda card: card["grid"]["rows"][0].update(pos=2),
    lambda card: card["grid"]["rows"][0].update(number=11),
    lambda card: card["grid"]["rows"][0].update(number=None),
])
def test_unknown_partial_wrong_event_or_wrong_session_leaves_the_art_untouched(monkeypatch, change):
    card = _card()
    change(card)
    shown, texts, bikes, bounds = _render(monkeypatch, card)
    assert not shown and not texts and not bikes and bounds is None


def test_a_field_beyond_readable_space_is_not_silently_cut_to_a_top_n(monkeypatch):
    shown, texts, bikes, bounds = _render(monkeypatch, _card(40))
    assert not shown and not texts and not bikes and bounds is None


def test_an_unsupported_pit_lane_start_is_not_put_in_an_ordinary_grid_box(monkeypatch):
    card = _card(22)
    card["grid"]["rows"].append({"pos": None, "number": 99, "pit_lane": True})
    shown, texts, bikes, bounds = _render(monkeypatch, card)
    assert not shown and not texts and not bikes and bounds is None


@pytest.mark.parametrize("season,start,expected", [
    (2027, "2027-03-27", 2027), (2026, "2027-03-27", None), (True, "2026-03-27", None),
    (None, "2026-03-27", 2026), (None, "", None), (None, "invalid", None),
])
def test_livery_year_comes_from_the_event_not_the_current_clock(monkeypatch, season, start, expected):
    card = _card()
    card["weekend"]["start"] = start
    if season is None:
        del card["grid"]["season"]
    else:
        card["grid"]["season"] = season
    shown, texts, bikes, _bounds = _render(monkeypatch, card)
    assert shown and not texts and len(bikes) == 22
    assert {year for _number, _box, year in bikes} == {expected}


def test_only_confirmed_grid_replaces_the_existing_header_decoration(monkeypatch):
    plugin = SportsDashboard({"id": "sports_dashboard"})
    fallback = []
    cutout = plugin._draw_standalone_sport_header_cutout
    monkeypatch.setattr(plugin, "_draw_standalone_sport_header_cutout",
                        lambda *args, **kwargs: fallback.append(args[1]) or cutout(*args, **kwargs))
    for valid in (False, True):
        card = deepcopy(_card())
        if not valid:
            card["grid"] = {}
        image = Image.new("RGB", (556, 268), DAY_COLORS["panel"])
        token = _ACTIVE_COLORS.set(DAY_COLORS)
        try:
            plugin._draw_standalone_sport_header(image, ImageDraw.Draw(image), 0, 0, 555, "MOTOGP", card, "MOTOGP LIVE")
        finally:
            _ACTIVE_COLORS.reset(token)
    assert fallback == ["MOTOGP"]
