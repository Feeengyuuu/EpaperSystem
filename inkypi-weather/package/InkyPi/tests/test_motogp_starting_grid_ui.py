"""The official MotoGP race grid replaces only the header art, never a partial field."""

from copy import deepcopy

import pytest
from PIL import Image, ImageChops, ImageDraw

from plugins.sports_dashboard.common import _ACTIVE_COLORS, DAY_COLORS, DEEP_NIGHT_COLORS
from plugins.sports_dashboard.sports_dashboard import SportsDashboard


BOX = (168, 2, 463, 48)


def _card(count=22):
    return {"sport": "MOTOGP", "status": "NEXT", "weekend": {"id": "current-event"},
            "sessions": [{"id": "race-session", "type": "RAC"}],
            "grid": {"event_id": "current-event", "kind": "official", "target_type": "RAC",
                     "session_id": "race-session", "complete": True,
                     "rows": [{"pos": index + 1, "number": index + 10, "constructor": "Ducati"}
                              for index in range(count)]}}


def _render(monkeypatch, card, colours=DAY_COLORS, box=BOX):
    plugin = SportsDashboard({"id": "sports_dashboard"})
    texts = []
    original = plugin._draw_text_in_box

    def text(draw, bounds, label, *args, **kwargs):
        texts.append((str(label), bounds))
        return original(draw, bounds, label, *args, **kwargs)

    monkeypatch.setattr(plugin, "_draw_text_in_box", text)
    token = _ACTIVE_COLORS.set(colours)
    try:
        image = Image.new("RGB", (556, 268), colours["panel"])
        before = image.copy()
        shown = plugin._draw_motogp_starting_grid(image, ImageDraw.Draw(image), box, card)
    finally:
        _ACTIVE_COLORS.reset(token)
    return shown, texts, ImageChops.difference(before, image).getbbox()


@pytest.mark.parametrize("colours", [DAY_COLORS, DEEP_NIGHT_COLORS], ids=["day", "night"])
@pytest.mark.parametrize("count", [21, 22, 24, 27, 30])
def test_every_grid_place_and_bike_number_fit_without_overlap(monkeypatch, colours, count):
    card = _card(count)
    shown, texts, bounds = _render(monkeypatch, card, colours)
    assert shown
    positions = [(text, box) for text, box in texts if text.isdigit()]
    bikes = [(text, box) for text, box in texts if text.startswith("#")]
    assert {text for text, _ in positions} == {str(row["pos"]) for row in card["grid"]["rows"]}
    assert {text for text, _ in bikes} == {f"#{row['number']}" for row in card["grid"]["rows"]}
    assert len(positions) == len(bikes) == count
    assert "正赛发车位 / 车号 #" in {text for text, _ in texts}
    assert "杆位 →" in {text for text, _ in texts}
    for _text, box in positions + bikes:
        assert BOX[0] <= box[0] < box[2] <= BOX[2]
        assert BOX[1] + 10 <= box[1] < box[3] <= BOX[3]
    # Every number lives in a separate text box, including the two labels within one slot.
    boxes = [box for _text, box in positions + bikes]
    for index, one in enumerate(boxes):
        for other in boxes[index + 1:]:
            assert one[2] <= other[0] or other[2] <= one[0] or one[3] <= other[1] or other[3] <= one[1]
    assert bounds and BOX[0] <= bounds[0] < bounds[2] <= BOX[2] and BOX[1] <= bounds[1] < bounds[3] <= BOX[3]


def test_official_empty_places_stay_empty_and_input_order_does_not_reorder_them(monkeypatch):
    card = _card(24)
    card["grid"]["rows"] = [row for row in reversed(card["grid"]["rows"]) if row["pos"] not in {2, 11}]
    shown, texts, _bounds = _render(monkeypatch, card)
    assert shown
    labels = dict(texts)
    assert "2" not in labels and "11" not in labels
    assert "24" in labels and "#33" in labels
    assert labels["1"][0] > labels["4"][0] > labels["7"][0]
    assert labels["1"][1] == labels["4"][1] == labels["7"][1]


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
    shown, texts, bounds = _render(monkeypatch, card)
    assert not shown and not texts and bounds is None


def test_a_field_beyond_readable_space_is_not_silently_cut_to_a_top_n(monkeypatch):
    shown, texts, bounds = _render(monkeypatch, _card(40))
    assert not shown and not texts and bounds is None


def test_a_provider_identified_pit_lane_start_is_named_separately(monkeypatch):
    card = _card(22)
    card["grid"]["rows"].append({"pos": None, "number": 99, "pit_lane": True})
    shown, texts, _bounds = _render(monkeypatch, card)
    assert shown
    assert "维修区 #99" in dict(texts)
    assert "23" not in dict(texts)


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
