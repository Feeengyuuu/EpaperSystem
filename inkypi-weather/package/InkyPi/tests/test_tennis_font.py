"""The tours' own typefaces come only from the device's private fonts, missing glyphs from the panel."""

import shutil
import gc
import weakref
from pathlib import Path

from PIL import Image, ImageDraw

from plugins.sports_dashboard.common import _ACTIVE_COLORS, DAY_COLORS
from plugins.sports_dashboard.sports_dashboard import SportsDashboard
from utils.app_utils import get_base_ui_font
from utils.tennis_font import FILES, tour_font
from utils.fonts import load_font
from plugins.sports_dashboard.render_fonts import render_font_scope

# The tours' faces never ship in the repository: an open font stands in for the installed file.
STAND_IN = Path(__file__).resolve().parents[1] / "src/plugins/sports_dashboard/assets/fonts/BarlowSemiCondensed-Bold.ttf"


def _install(tmp_path, monkeypatch, *keys):
    fonts = tmp_path / "fonts"
    fonts.mkdir(exist_ok=True)
    for key in keys:
        shutil.copyfile(STAND_IN, fonts / FILES[key])
    monkeypatch.setenv("INKYPI_DATA_DIR", str(tmp_path))
    return fonts


def test_without_the_tour_fonts_the_panel_keeps_its_own(tmp_path, monkeypatch):
    monkeypatch.setenv("INKYPI_DATA_DIR", str(tmp_path))
    fallback = get_base_ui_font(11, bold=True)
    assert tour_font("ATP", 11, True, fallback) is fallback
    assert tour_font("", 11, True, fallback) is fallback


def test_an_installed_tour_font_sets_names_with_chinese_and_missing_accents_from_the_panel(tmp_path, monkeypatch):
    fonts = _install(tmp_path, monkeypatch, ("ATP", True))
    fallback = get_base_ui_font(11, bold=True)
    font = tour_font("ATP", 11, True, fallback)

    assert Path(font.path) == fonts / FILES[("ATP", True)]
    assert font.covers("Miomir Kecmanović 第二轮 11/10 05:30")
    assert font.getlength("Holger Rune") > 0 and font.getlength("第二轮") > 0
    # Each tour and weight is a file of its own: absent, it falls back.
    assert tour_font("WTA", 11, True, fallback) is fallback
    assert tour_font("ATP", 11, False, fallback) is fallback


def test_a_symlinked_or_empty_tour_font_is_ignored(tmp_path, monkeypatch):
    fonts = tmp_path / "fonts"
    fonts.mkdir()
    (fonts / FILES[("WTA", False)]).write_bytes(b"")
    monkeypatch.setenv("INKYPI_DATA_DIR", str(tmp_path))
    fallback = get_base_ui_font(9)
    assert tour_font("WTA", 9, False, fallback) is fallback


def test_the_tennis_panel_sets_each_column_in_its_tours_type(tmp_path, monkeypatch):
    _install(tmp_path, monkeypatch, ("WTA", True))
    plugin = SportsDashboard({"id": "sports_dashboard"})
    monkeypatch.setattr(plugin, "_load_icon_flag", lambda url, size: Image.new("RGBA", size, (0, 80, 200, 255)))
    used = []
    original = plugin._tennis_font
    monkeypatch.setattr(plugin, "_tennis_font", lambda tour, size, bold=False: used.append(
        (tour, getattr(original(tour, size, bold), "path", ""))) or original(tour, size, bold))
    match = {"state": "post", "round_zh": "小组赛", "date": "2026-11-09T20:00Z", "detail": "",
             "players": [{"name": "Coco Gauff", "seed": 4, "winner": True, "sets": [[6, None]]},
                         {"name": "Jessica Pegula", "seed": 5, "sets": [[4, None]]}]}
    token = _ACTIVE_COLORS.set(DAY_COLORS)
    try:
        image = Image.new("RGB", (270, 64), DAY_COLORS["paper"])
        plugin._draw_tennis_match(image, ImageDraw.Draw(image), (2, 2, 266, 60), match, None, DAY_COLORS["blue"],
                                  tour="WTA")
    finally:
        _ACTIVE_COLORS.reset(token)

    assert used and {tour for tour, _path in used} == {"WTA"}
    assert any(Path(path).name == FILES[("WTA", True)] for _tour, path in used)


def test_tour_size_keeps_visible_capitals_and_height_does_not_jump_backwards(tmp_path, monkeypatch):
    fonts = _install(tmp_path, monkeypatch, ("WTA", True))
    heights = []
    for size in range(7, 18):
        fallback = get_base_ui_font(size, bold=True)
        original = load_font(fonts / FILES[("WTA", True)], size)
        fitted = tour_font("WTA", size, True, fallback)
        original_box, box = original.getbbox("8H"), fitted.getbbox("8H")
        assert box[3] - box[1] >= original_box[3] - original_box[1]
        heights.append(box[3] - box[1])
        assert fitted.size == size
    assert heights == sorted(heights)


def test_corrupt_tour_font_falls_back_and_fonts_are_not_strongly_cached(tmp_path, monkeypatch):
    fonts = _install(tmp_path, monkeypatch, ("ATP", True))
    with render_font_scope():
        fallback = get_base_ui_font(11, bold=True)
        font = tour_font("ATP", 11, True, fallback)
        refs = [weakref.ref(font), weakref.ref(font.primary), weakref.ref(fallback)]
        del font, fallback
        gc.collect()
        assert all(ref() is None for ref in refs)
    (fonts / FILES[("WTA", True)]).write_bytes(b"not a font")
    fallback = get_base_ui_font(11, bold=True)
    assert tour_font("WTA", 11, True, fallback) is fallback


def test_upcoming_clock_keeps_its_tour_face_and_all_minutes(tmp_path, monkeypatch):
    _install(tmp_path, monkeypatch, ("ATP", False))
    plugin = SportsDashboard({"id": "sports_dashboard"})
    recorded = []
    original = plugin._draw_text_in_box

    def capture(draw, box, text, font, *args, **kwargs):
        recorded.append((text, Path(font.path).name))
        return original(draw, box, text, font, *args, **kwargs)

    monkeypatch.setattr(plugin, "_draw_text_in_box", capture)
    match = {"state": "pre", "date": "2026-11-09T05:30Z", "players": [], "round_zh": "半决赛"}
    image = Image.new("RGB", (270, 64), DAY_COLORS["paper"])
    plugin._draw_tennis_match(image, ImageDraw.Draw(image), (2, 2, 266, 60), match, None, DAY_COLORS["blue"], tour="ATP")
    assert ("11/09 05:30", FILES[("ATP", False)]) in recorded
