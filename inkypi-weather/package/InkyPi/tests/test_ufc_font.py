"""Private UFC typography keeps Chinese readable and stays local to its fight card."""

import shutil
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from plugins.sports_dashboard.common import _ACTIVE_COLORS, DAY_COLORS
from plugins.sports_dashboard.sports_dashboard import SportsDashboard
from utils.app_utils import get_base_ui_font
from utils.ufc_font import ufc_font


# Brand font bytes never enter the repository; an OFL face exercises the same loader.
STAND_IN = Path(__file__).resolve().parents[1] / "src/plugins/sports_dashboard/assets/fonts/BarlowSemiCondensed-Bold.ttf"
BOLD_NAME = "ufc-sans-condensed-bold.woff2"


def _install(tmp_path, monkeypatch):
    fonts = tmp_path / "fonts"
    fonts.mkdir()
    target = fonts / BOLD_NAME
    shutil.copyfile(STAND_IN, target)
    monkeypatch.setenv("INKYPI_DATA_DIR", str(tmp_path))
    return target


@pytest.mark.parametrize("payload", [None, b"", b"invalid font bytes"])
def test_missing_empty_or_corrupt_ufc_font_preserves_the_readable_fallback(tmp_path, monkeypatch, payload):
    monkeypatch.setenv("INKYPI_DATA_DIR", str(tmp_path))
    if payload is not None:
        fonts = tmp_path / "fonts"
        fonts.mkdir()
        (fonts / BOLD_NAME).write_bytes(payload)
    fallback = get_base_ui_font(17, bold=True)

    assert ufc_font(17, True, fallback) is fallback


def test_ufc_letters_and_digits_use_the_private_face_while_chinese_remains_readable(tmp_path, monkeypatch):
    target = _install(tmp_path, monkeypatch)
    fallback = get_base_ui_font(17, bold=True)
    font = ufc_font(17, True, fallback)

    assert Path(font.path) == target
    assert font.covers("Uroš Medić 重量级 R5 4:59 14-2-0")
    assert font.getlength("Jon Jones 14-2-0") == font.primary.getlength("Jon Jones 14-2-0")
    assert font.getlength("重量级") == fallback.getlength("重量级")
    mask = font.getmask("Jon Jones 重量级 14-2-0")
    assert mask.size[0] > 0 and mask.size[1] > 0 and any(bytes(mask))
    # The other weight has its own file and must not silently borrow the bold face.
    assert ufc_font(17, False, fallback) is fallback


def test_the_ufc_faceoff_uses_its_font_without_changing_other_sports(tmp_path, monkeypatch):
    target = _install(tmp_path, monkeypatch)
    plugin = SportsDashboard({"id": "sports_dashboard"})
    original_font = plugin._font(17, True)
    original_tennis_font = plugin._tennis_font("ATP", 17, True)
    monkeypatch.setattr(plugin, "_load_icon_flag", lambda url, size: None)
    drawn = {}
    original_draw = plugin._draw_text_in_box
    original_centered = plugin._draw_centered_in_box

    def capture(draw, box, text, font, *args, **kwargs):
        drawn[str(text)] = font
        return original_draw(draw, box, text, font, *args, **kwargs)

    def capture_centered(draw, box, text, font, *args, **kwargs):
        drawn[str(text)] = font
        return original_centered(draw, box, text, font, *args, **kwargs)

    monkeypatch.setattr(plugin, "_draw_text_in_box", capture)
    monkeypatch.setattr(plugin, "_draw_centered_in_box", capture_centered)
    fight = {"state": "pre", "weight_zh": "重量级", "rounds": 5,
             "fighters": [{"name": "Jon Jones", "record": "28-1-0", "country": "USA"},
                          {"name": "Tom Aspinall", "record": "15-3-0", "country": "England"}]}
    token = _ACTIVE_COLORS.set(DAY_COLORS)
    try:
        image = Image.new("RGB", (556, 100), DAY_COLORS["paper"])
        plugin._draw_ufc_face_off(image, ImageDraw.Draw(image), (12, 3, 543, 95), fight, DAY_COLORS["red"])
    finally:
        _ACTIVE_COLORS.reset(token)

    assert {"Jon Jones", "28-1-0", "15-3-0", "VS", "重量级"} <= drawn.keys()
    assert all(Path(drawn[text].path) == target for text in ("Jon Jones", "28-1-0", "15-3-0", "VS"))
    assert drawn["重量级"].covers("重量级")
    assert plugin._font(17, True).path == original_font.path
    assert plugin._tennis_font("ATP", 17, True).path == original_tennis_font.path
