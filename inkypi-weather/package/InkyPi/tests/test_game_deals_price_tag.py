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
    assert 6 <= left < right <= width - 6
    assert 5 <= top < bottom <= 26
    assert result.info["price_font_size"] > 0


def test_corrupt_art_keeps_a_dark_gold_edged_tag_and_complete_price(tmp_path, monkeypatch):
    bad_art = tmp_path / "broken.png"
    bad_art.write_bytes(b"not an image")
    monkeypatch.setattr(price_tag, "ASSET", bad_art)
    renderer = price_tag.PriceTags()
    result = renderer.render("199.99", (92, 31))
    # Downsampling blends the dark surface slightly with the nearby gold rim.
    assert max(result.getpixel((46, 2))[:3]) < 60
    assert result.getpixel((46, 2))[3] == 255
    assert result.info["price_text"] == "$199.99"


def test_bundled_art_has_no_transparent_padding_and_repeated_prices_are_stable():
    renderer = price_tag.PriceTags()
    first, second = renderer.render("1.99", (92, 31)), renderer.render("1.99", (92, 31))
    assert ImageChops.difference(first, second).getbbox() is None
    left, top, right, bottom = first.getchannel("A").getbbox()
    assert left == top == 0 and right == 92 and bottom == 31
