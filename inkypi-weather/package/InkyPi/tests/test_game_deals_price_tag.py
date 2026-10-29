"""Private currency type is optional; full prices remain readable within their slot."""

from pathlib import Path
import shutil

from PIL import ImageChops
import pytest

from plugins.game_deals import price_tag


@pytest.fixture(autouse=True)
def isolated_private_font_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("INKYPI_DATA_DIR", str(tmp_path))


def test_owner_font_is_loaded_only_from_the_fixed_private_filename(tmp_path):
    fonts = tmp_path / "fonts"
    fonts.mkdir()
    target = fonts / price_tag.PRIVATE_FONT_NAME
    # A redistributable face exercises private-file discovery; no owner font is
    # copied into public fixtures or required in a clean checkout.
    shutil.copyfile(price_tag.FALLBACK_FONT, target)
    renderer = price_tag.PriceTags()
    assert Path(renderer._face.path) == target
    assert renderer._face.covers("0123456789$.,")
    tag = renderer.render("1,999.99", (92, 31))
    assert tag.info["price_text"] == "$1,999.99"


@pytest.mark.parametrize("file_state", ["absent", "invalid", "different_filename"])
def test_missing_or_unreadable_private_font_uses_the_public_fallback(tmp_path, file_state):
    fonts = tmp_path / "fonts"
    fonts.mkdir()
    if file_state == "invalid":
        (fonts / price_tag.PRIVATE_FONT_NAME).write_bytes(b"not a font")
    elif file_state == "different_filename":
        shutil.copyfile(price_tag.FALLBACK_FONT, fonts / "Banknote Regular.ttf")
    renderer = price_tag.PriceTags()
    assert Path(renderer._face.path) == price_tag.FALLBACK_FONT
    assert renderer.render("0.89", (92, 31)).size == (92, 31)


def test_incomplete_currency_font_falls_back_instead_of_omitting_symbols(tmp_path, monkeypatch):
    fonts = tmp_path / "fonts"
    fonts.mkdir()
    target = fonts / price_tag.PRIVATE_FONT_NAME
    target.write_bytes(b"a fake digits-only face")
    original_load = price_tag.load_font

    class DigitsOnly:
        def covers(self, text):
            return text.isdecimal()

    monkeypatch.setattr(price_tag, "load_font", lambda path, size: DigitsOnly() if path == target else original_load(path, size))
    renderer = price_tag.PriceTags()
    assert Path(renderer._face.path) == price_tag.FALLBACK_FONT
    assert renderer._face.covers(price_tag.REQUIRED_GLYPHS)


@pytest.mark.parametrize("amount", ["0.00", "0.89", "1.24", "4.99", "199.99", "1,999.99", "100000.00"])
@pytest.mark.parametrize("width", [76, 92])
def test_long_prices_keep_every_character_and_fit_without_touching_the_rim(amount, width):
    result = price_tag.PriceTags().render(amount, (width, 31))
    assert result.size == (width, 31)
    assert result.info["price_text"] == "$" + amount
    left, top, right, bottom = result.info["price_ink_bounds"]
    assert 1 <= left < right <= width - 1
    assert 2 <= top < bottom <= 29
    assert result.info["price_font_size"] > 0


@pytest.mark.parametrize("night", [False, True])
def test_the_price_sits_on_the_page_without_a_tag_in_greenback_green_and_gold(night):
    renderer = price_tag.PriceTags(night)
    first, second = renderer.render("1.99", (92, 31)), renderer.render("1.99", (92, 31))
    assert ImageChops.difference(first, second).getbbox() is None
    # No backing: the corners and the room round the type stay transparent.
    assert all(first.getpixel(xy)[3] == 0 for xy in ((0, 0), (91, 0), (0, 30), (91, 30)))
    left, _top, right, _bottom = first.getchannel("A").getbbox()
    assert left <= 4 and right < 92
    ink = [first.getpixel((x, y)) for x in range(92) for y in range(31) if first.getpixel((x, y))[3] == 255]
    # Orange digits (red high, green middling, blue low) and the amber dollar sign.
    assert sum(g > r + 30 and g > b + 20 for r, g, b, _a in ink) > 40
    # The dollar sign at the left is gold: red over green over blue, warm and light.
    left = int(first.info["price_ink_bounds"][0])
    dollar = [first.getpixel((x, y)) for x in range(left, left + 9) for y in range(31) if first.getpixel((x, y))[3] == 255]
    assert sum(r > g > b and r - b > 80 and g > 100 for r, g, b, _a in dollar) > 8
    assert result_is_left_aligned(renderer)


def result_is_left_aligned(renderer):
    # A price narrower than its slot starts at the slot's left, beside the title above it.
    narrow = renderer.render("0.99", (140, 31))
    left, _top, right, _bottom = narrow.info["price_ink_bounds"]
    return left <= 4 and right < 120
