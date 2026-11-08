"""Steam's Motiva Sans comes only from the device's private fonts, with Chinese from the UI font."""

import shutil
from pathlib import Path

from utils.app_utils import get_base_ui_font
from utils.steam_font import FILES, steam_font

# Motiva Sans never ships in the repository: an open font stands in for the installed file.
STAND_IN = Path(__file__).resolve().parents[1] / "src/plugins/sports_dashboard/assets/fonts/BarlowSemiCondensed-Bold.ttf"


def test_without_the_private_font_the_page_keeps_its_ui_font(tmp_path, monkeypatch):
    monkeypatch.setenv("INKYPI_DATA_DIR", str(tmp_path))
    font = steam_font(18, bold=True)
    assert font.path == get_base_ui_font(18, bold=True).path


def test_installed_motiva_sets_latin_and_figures_with_chinese_from_the_ui_font(tmp_path, monkeypatch):
    fonts = tmp_path / "fonts"
    fonts.mkdir()
    shutil.copyfile(STAND_IN, fonts / FILES[True])
    monkeypatch.setenv("INKYPI_DATA_DIR", str(tmp_path))

    font = steam_font(18, bold=True)
    assert Path(font.path) == fonts / FILES[True]
    assert font.covers("Borderlands 2 $1.99 特别好评")
    assert font.getlength("$1.99") > 0 and font.getlength("特别好评") > 0
    # The regular weight is a file of its own: absent, it falls back.
    assert steam_font(18).path == get_base_ui_font(18).path


def test_a_symlinked_or_empty_private_font_is_ignored(tmp_path, monkeypatch):
    fonts = tmp_path / "fonts"
    fonts.mkdir()
    (fonts / FILES[False]).write_bytes(b"")
    monkeypatch.setenv("INKYPI_DATA_DIR", str(tmp_path))
    assert steam_font(14).path == get_base_ui_font(14).path
