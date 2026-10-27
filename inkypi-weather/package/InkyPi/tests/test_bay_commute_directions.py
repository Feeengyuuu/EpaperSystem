import pytest
from PIL import ImageChops

from plugins.bay_commute.command_render import Page
from plugins.bay_commute.direction_plaques import cardinal_directions, draw_direction


@pytest.mark.parametrize("source,expected", [
    ("North", ("NORTH",)), (" eastbound ", ("EAST",)), ("WB", ("WEST",)),
    ("South / North", ("NORTH", "SOUTH")), ("East / West", ("EAST", "WEST")),
    ("东西双向", ("EAST", "WEST")),
])
def test_direction_plaques_retain_explicit_source_direction(source, expected):
    assert cardinal_directions(source) == expected


@pytest.mark.parametrize("source", [None, "", "N/A", "Both", "Northeast", "North / unknown", "East / North"])
def test_missing_or_ambiguous_bearing_never_implies_a_cardinal_direction(source):
    page = Page({"themeMode": "day"})
    draw_direction(page, source, right=482, top=136, interstate=True)
    assert [text for text, _ in page.text_bounds] == ["方向未报"]
    page.image.close()


@pytest.mark.parametrize("mode", ["day", "night"])
@pytest.mark.parametrize("route,source", [("I-880", "East / West"), ("SR-84", "North / South")])
def test_direction_plaques_stay_above_the_camera_without_covering_the_status(mode, route, source):
    page = Page({"themeMode": mode})
    before = page.image.copy()
    draw_direction(page, source, right=482, top=136, interstate=route.startswith("I-"))
    bounds = ImageChops.difference(page.image, before).getbbox()
    assert bounds is not None
    assert 370 <= bounds[0] < bounds[2] <= 482
    assert 136 <= bounds[1] < bounds[3] < 155  # Camera begins at y=155.
    assert len(page.text_bounds) == 2
    assert all(370 <= box[0] <= box[2] <= 482 and 136 <= box[1] <= box[3] < 155
               for _, box in page.text_bounds)
    before.close()
    page.image.close()
