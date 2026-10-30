import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from utils.picture_grounds import blend_drawing, cut_flat_ground, drawn_on_white, readable_on, trim_white  # noqa: E402


def _diagram():
    drawing = Image.new("RGB", (60, 40), (255, 255, 255))
    draw = ImageDraw.Draw(drawing)
    draw.line((5, 20, 55, 20), fill=(0, 0, 0), width=3)
    draw.rectangle((20, 5, 30, 12), fill=(30, 90, 220))
    return drawing


def test_a_diagram_on_white_is_told_from_a_photo():
    assert drawn_on_white(_diagram())
    assert not drawn_on_white(Image.effect_noise((60, 40), 80).convert("RGB"))


def test_a_figure_loses_its_empty_white_margins():
    figure = Image.new("RGB", (200, 120), (255, 255, 255))
    ImageDraw.Draw(figure).rectangle((50, 30, 149, 89), fill=(30, 90, 220))

    assert trim_white(figure).size == (104, 64)  # its drawing and 2 px around it
    assert trim_white(Image.new("RGB", (20, 10), (255, 255, 255))).size == (20, 10)


def test_a_diagram_takes_the_ground_it_lands_on_by_day_and_turns_light_at_night():
    page = Image.new("RGB", (80, 60), (255, 248, 220))
    blend_drawing(page, _diagram(), (10, 10), night=False)
    assert page.getpixel((12, 12)) == (255, 248, 220)  # its white is the card's tint
    assert page.getpixel((35, 30)) == (0, 0, 0)  # its line stays dark

    night = Image.new("RGB", (80, 60), (0, 0, 0))
    blend_drawing(night, _diagram(), (10, 10), night=True)
    assert night.getpixel((12, 12)) == (0, 0, 0)  # its white is the dark page
    assert min(night.getpixel((35, 30))) > 200  # its black line is light
    red, green, blue = night.getpixel((35, 18))
    assert blue > red  # the blue box stays blue


def test_a_logo_leaves_its_flat_tile_and_its_white_letters_take_ink():
    tile = Image.new("RGB", (48, 48), (20, 80, 200))
    ImageDraw.Draw(tile).rectangle((14, 14, 34, 34), fill=(255, 255, 255))

    mark = cut_flat_ground(tile, 16)
    readable = readable_on(mark, (255, 255, 255), (18, 26, 32))

    assert mark.getchannel("A").getbbox() == (0, 0, mark.width, mark.height)
    assert mark.getpixel((mark.width // 2, mark.height // 2))[:3] == (255, 255, 255)
    assert readable.getpixel((readable.width // 2, readable.height // 2))[:3] == (18, 26, 32)
    assert cut_flat_ground(Image.effect_noise((48, 48), 80).convert("RGB"), 16) is None
