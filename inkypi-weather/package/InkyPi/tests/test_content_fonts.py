"""Content typography stays readable, bounded, and disposable across page roles."""

import gc
from pathlib import Path
import shutil
import weakref

import pytest
from PIL import Image, ImageDraw

from utils import content_fonts, page_canvas
from utils.app_utils import get_base_ui_font
from utils.fonts import load_font
from utils.steam_font import FILES as STEAM_FILES


SRC = Path(__file__).resolve().parents[1] / "src"
SAMPLE = "电量 100% · Café 999 km，科技日报"
# Private brand bytes never enter fixtures; this bundled OFL face exercises the loader.
PRIVATE_STAND_IN = SRC / "plugins/sports_dashboard/assets/fonts/BarlowSemiCondensed-Bold.ttf"


@pytest.fixture(autouse=True)
def isolated_content_fonts(monkeypatch):
    monkeypatch.delenv("INKYPI_DATA_DIR", raising=False)
    content_fonts._PAIRS.clear()
    yield
    content_fonts._PAIRS.clear()


def _raster(font, text=SAMPLE):
    image = Image.new("L", (640, 100), 0)
    ImageDraw.Draw(image).text((5, 70), text, font=font, anchor="ls", fill=255)
    return image.tobytes()


@pytest.mark.parametrize("profile", ["news", "science", "instrument", "steam"])
def test_roles_keep_real_chinese_and_accented_latin_glyphs_on_one_baseline(profile):
    font = content_fonts.content_font(18, True, profile=profile)

    assert font.covers(SAMPLE)
    assert not font.primary.covers("电量")
    assert font.fallback.covers("电量")
    assert _raster(font, "电量") == _raster(font.fallback, "电量")
    assert _raster(font, "Café 999 km") == _raster(font.primary, "Café 999 km")
    assert font.getlength("电量") == font.fallback.getlength("电量")
    assert font.getlength("Café 999 km") == font.primary.getlength("Café 999 km")
    assert any(_raster(font))


@pytest.mark.parametrize("size,bold,cjk_weight,latin_name", [
    (13, True, 500, "Inter-SemiBold.ttf"),
    (14, True, 600, "Inter-Bold.ttf"),
    (18, False, 500, "Inter-SemiBold.ttf"),
])
def test_small_labels_and_headings_keep_their_requested_weight(size, bold, cjk_weight, latin_name):
    font = content_fonts.content_font(size, bold)
    expected_cjk = load_font(SRC / "static/fonts/NotoSansSC-VF.ttf", size)
    expected_cjk.set_variation_by_axes([cjk_weight])

    assert Path(font.primary.path).name == latin_name
    assert _raster(font, "科技日报") == _raster(expected_cjk, "科技日报")


@pytest.mark.parametrize("payload", [None, b"not a font"])
def test_missing_or_corrupt_cjk_uses_real_shared_ui_glyphs(tmp_path, monkeypatch, payload):
    unavailable = tmp_path / "unavailable-cjk.ttf"
    if payload is not None:
        unavailable.write_bytes(payload)
    monkeypatch.setattr(content_fonts, "CJK_PATH", unavailable)
    shared = get_base_ui_font(16, bold=True)
    monkeypatch.setattr(content_fonts, "get_base_ui_font", lambda size, bold=False: shared)

    font = content_fonts.content_font(16, True)

    assert font.fallback is shared
    assert font.covers(SAMPLE)
    assert _raster(font, "科技日报") == _raster(shared, "科技日报")


@pytest.mark.parametrize("order", [(False, True), (True, False)])
def test_small_fallback_weights_stay_independent_in_either_call_order(tmp_path, monkeypatch, order):
    monkeypatch.setattr(content_fonts, "CJK_PATH", tmp_path / "absent-cjk.ttf")
    shared = {bold: get_base_ui_font(13, bold=bold) for bold in (False, True)}
    monkeypatch.setattr(content_fonts, "get_base_ui_font", lambda size, bold=False: shared[bold])

    fonts = {bold: content_fonts.content_font(13, bold) for bold in order}

    assert fonts[False] is not fonts[True]
    for bold, font in fonts.items():
        assert content_fonts.content_font(13, bold) is font
        assert font.fallback is shared[bold]
        assert _raster(font, "科技日报") == _raster(shared[bold], "科技日报")


@pytest.mark.parametrize("payload", [None, b"not a font"])
def test_missing_or_corrupt_latin_keeps_the_complete_cjk_face(tmp_path, monkeypatch, payload):
    unavailable = tmp_path / "unavailable-latin.ttf"
    if payload is not None:
        unavailable.write_bytes(payload)
    monkeypatch.setattr(content_fonts, "LATIN_PATHS", {"news": (unavailable, unavailable)})

    font = content_fonts.content_font(16, True)

    assert Path(font.path) == content_fonts.CJK_PATH
    assert font.covers(SAMPLE)
    assert any(_raster(font))


def test_no_content_font_files_preserves_shared_fallback_pixels(tmp_path, monkeypatch):
    unavailable = tmp_path / "absent.ttf"
    monkeypatch.setattr(content_fonts, "CJK_PATH", unavailable)
    monkeypatch.setattr(content_fonts, "LATIN_PATHS", {"news": (unavailable, unavailable)})
    shared = get_base_ui_font(18, bold=True)
    monkeypatch.setattr(content_fonts, "get_base_ui_font", lambda size, bold=False: shared)

    font = content_fonts.content_font(18, True)

    assert font is shared
    assert _raster(font) == _raster(shared)


@pytest.mark.parametrize("profile", ["news", "science", "instrument", "steam"])
def test_font_pairs_and_native_faces_are_not_retained_after_the_page_releases_them(profile):
    canvas = content_fonts.ContentCanvas({"paper": "white"}, profile=profile)
    font = canvas.font(18, True)
    assert content_fonts.content_font(18, True, profile=profile) is font
    references = [weakref.ref(face) for face in (font, font.primary, font.fallback)]

    del font
    gc.collect()
    assert all(reference() is not None for reference in references)
    del canvas
    gc.collect()

    assert all(reference() is None for reference in references)


def test_custom_font_family_keeps_the_existing_page_canvas_resolver(monkeypatch):
    sentinel = object()
    calls = []
    monkeypatch.setattr(
        page_canvas, "get_font",
        lambda family, size, weight: calls.append((family, size, weight)) or sentinel,
    )
    monkeypatch.setattr(content_fonts, "content_font", lambda *args, **kwargs: pytest.fail("custom font replaced"))
    canvas = content_fonts.ContentCanvas({"paper": "white"}, family="Reader's Custom Font", profile="science")

    assert canvas.font(15, True) is sentinel
    assert canvas.font(15, True) is sentinel
    assert calls == [("Reader's Custom Font", 15, "bold")]


@pytest.mark.parametrize("size", [22, 38, 44, 54, 64])
@pytest.mark.parametrize("bold", [False, True])
def test_instrument_capitals_and_digits_preserve_the_reference_visible_height(size, bold):
    font = content_fonts.content_font(size, bold, profile="instrument")
    reference = get_base_ui_font(size, bold=bold)
    box, original = font.getbbox("8H"), reference.getbbox("8H")

    assert Path(font.primary.path).name == "SpaceGrotesk-VF.ttf"
    # Integer-size hinting can move an edge by a pixel after em-size normalisation.
    assert abs((box[3] - box[1]) - (original[3] - original[1])) <= 2


@pytest.mark.parametrize("text,size", [("100%", 44), ("100%", 64), ("999", 38), ("999", 54)])
def test_three_digit_instrument_readings_keep_the_existing_column_width_budget(text, size):
    font = content_fonts.content_font(size, True, profile="instrument")
    reference = get_base_ui_font(size, bold=True)

    assert 0 < font.getlength(text) <= reference.getlength(text) * 1.2
    assert any(bytes(font.getmask(text)))


@pytest.mark.parametrize("bold", [False, True])
def test_valid_installed_steam_face_precedes_public_latin_but_keeps_chinese(tmp_path, monkeypatch, bold):
    private = tmp_path / "fonts" / STEAM_FILES[bold]
    private.parent.mkdir()
    shutil.copyfile(PRIVATE_STAND_IN, private)
    monkeypatch.setenv("INKYPI_DATA_DIR", str(tmp_path))

    font = content_fonts.content_font(18, bold, profile="steam")

    assert Path(font.primary.path) == private
    assert Path(font.fallback.path) == content_fonts.CJK_PATH
    assert font.covers(SAMPLE)
    assert _raster(font, "科技日报") == _raster(font.fallback, "科技日报")


@pytest.mark.parametrize("payload", [None, b"", b"corrupt private font"])
def test_missing_empty_or_corrupt_private_steam_face_uses_public_pair(tmp_path, monkeypatch, payload):
    monkeypatch.setenv("INKYPI_DATA_DIR", str(tmp_path))
    if payload is not None:
        private = tmp_path / "fonts" / STEAM_FILES[True]
        private.parent.mkdir()
        private.write_bytes(payload)

    font = content_fonts.content_font(18, True, profile="steam")

    assert Path(font.primary.path).name == "Inter-Bold.ttf"
    assert Path(font.fallback.path) == content_fonts.CJK_PATH
    assert font.covers(SAMPLE)
