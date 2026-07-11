import os
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PIL import ImageFont  # noqa: E402
from utils import app_utils  # noqa: E402
from utils.app_utils import (  # noqa: E402
    DEFAULT_FONT_FAMILY,
    FONTS,
    get_available_font_names,
    get_font,
    get_font_path,
    get_fonts,
)

# The YaHei binaries are proprietary and not committed; loading tests only run
# where the fonts have been installed locally (see static/fonts/).
_FONTS_DIR = Path(__file__).resolve().parents[1] / "src" / "static" / "fonts"
requires_yahei_files = pytest.mark.skipif(
    not (_FONTS_DIR / "msyh.ttf").is_file() or not (_FONTS_DIR / "msyhbd.ttf").is_file(),
    reason="Microsoft YaHei font files are not installed",
)


def test_default_font_family_is_microsoft_yahei():
    assert DEFAULT_FONT_FAMILY == "Microsoft YaHei"
    assert "Microsoft YaHei" in get_available_font_names()
    assert "\u5fae\u8f6f\u96c5\u9ed1" in get_available_font_names()


def test_durable_yahei_regular_and_bold_take_priority(tmp_path, monkeypatch):
    source = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "static"
        / "fonts"
        / "NotoSansSC-VF.ttf"
    )
    fonts = tmp_path / "fonts"
    fonts.mkdir()
    shutil.copyfile(source, fonts / "msyh.ttf")
    shutil.copyfile(source, fonts / "msyhbd.ttf")
    monkeypatch.setenv("INKYPI_DATA_DIR", os.fspath(tmp_path))

    regular = app_utils.get_base_ui_font(18)
    bold = app_utils.get_base_ui_font(18, bold=True)

    assert Path(regular.path) == fonts / "msyh.ttf"
    assert Path(bold.path) == fonts / "msyhbd.ttf"
    assert Path(get_font_path("microsoft-yahei")) == fonts / "msyh.ttf"
    assert Path(get_font_path("microsoft-yahei-bold")) == fonts / "msyhbd.ttf"


def test_durable_yahei_ttc_regular_and_bold_are_supported(tmp_path, monkeypatch):
    source = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "static"
        / "fonts"
        / "NotoSansSC-VF.ttf"
    )
    fonts = tmp_path / "fonts"
    fonts.mkdir()
    shutil.copyfile(source, fonts / "msyh.ttc")
    shutil.copyfile(source, fonts / "msyhbd.ttc")
    monkeypatch.setenv("INKYPI_DATA_DIR", os.fspath(tmp_path))

    assert Path(app_utils.get_base_ui_font(18).path) == fonts / "msyh.ttc"
    assert Path(app_utils.get_base_ui_font(18, bold=True).path) == fonts / "msyhbd.ttc"


def test_corrupt_bold_falls_back_independently_of_regular(tmp_path, monkeypatch):
    source = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "static"
        / "fonts"
        / "NotoSansSC-VF.ttf"
    )
    fonts = tmp_path / "fonts"
    fonts.mkdir()
    regular_path = fonts / "msyh.ttf"
    shutil.copyfile(source, regular_path)
    (fonts / "msyhbd.ttf").write_bytes(b"not-a-font")
    monkeypatch.setenv("INKYPI_DATA_DIR", os.fspath(tmp_path))

    regular = app_utils.get_base_ui_font(18)
    bold = app_utils.get_base_ui_font(18, bold=True)

    assert Path(regular.path) == regular_path
    assert Path(bold.path).name == "NotoSansSC-VF.ttf"


@pytest.mark.parametrize("durable_font_state", ["missing", "corrupt"])
@pytest.mark.parametrize("bold", [False, True])
def test_missing_or_corrupt_durable_yahei_falls_back_to_tracked_font(
    tmp_path, monkeypatch, durable_font_state, bold
):
    fonts = tmp_path / "fonts"
    fonts.mkdir()
    if durable_font_state == "corrupt":
        filename = "msyhbd.ttf" if bold else "msyh.ttf"
        (fonts / filename).write_bytes(b"not-a-font")
    monkeypatch.setenv("INKYPI_DATA_DIR", os.fspath(tmp_path))

    font = app_utils.get_base_ui_font(18, bold=bold)

    assert isinstance(font, ImageFont.FreeTypeFont)
    assert Path(font.path).name == "NotoSansSC-VF.ttf"


def test_base_font_resolver_and_css_uri_use_durable_yahei(tmp_path, monkeypatch):
    source = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "static"
        / "fonts"
        / "NotoSansSC-VF.ttf"
    )
    fonts = tmp_path / "fonts"
    fonts.mkdir()
    durable_path = fonts / "msyh.ttf"
    durable_bold_path = fonts / "msyhbd.ttf"
    shutil.copyfile(source, durable_path)
    shutil.copyfile(source, durable_bold_path)
    monkeypatch.setenv("INKYPI_DATA_DIR", os.fspath(tmp_path))

    assert Path(app_utils.base_ui_font_candidates()[0]) == durable_path
    assert Path(app_utils.resolve_base_ui_font_path()) == durable_path
    assert app_utils.font_file_uri(os.fspath(durable_path)) == durable_path.resolve().as_uri()

    yahei_regular = next(
        font
        for font in get_fonts()
        if font["font_family"] == "Microsoft YaHei"
        and font["font_weight"] == "normal"
    )
    yahei_bold = next(
        font
        for font in get_fonts()
        if font["font_family"] == "Microsoft YaHei"
        and font["font_weight"] == "bold"
    )
    assert yahei_regular["url"] == durable_path.resolve().as_uri()
    assert yahei_bold["url"] == durable_bold_path.resolve().as_uri()


@requires_yahei_files
def test_microsoft_yahei_font_family_loads_static_files():
    regular = get_font("Microsoft YaHei", 18)
    bold = get_font("Microsoft YaHei", 18, "bold")
    alias = get_font("\u5fae\u8f6f\u96c5\u9ed1", 18)

    assert isinstance(regular, ImageFont.FreeTypeFont)
    assert isinstance(bold, ImageFont.FreeTypeFont)
    assert isinstance(alias, ImageFont.FreeTypeFont)
    assert Path(regular.path).name == "msyh.ttf"
    assert Path(bold.path).name == "msyhbd.ttf"
    assert Path(alias.path).name == "msyh.ttf"
    assert "Microsoft YaHei" in regular.getname()[0]
    assert "Microsoft YaHei" in bold.getname()[0]


def test_yahei_font_path_aliases_are_registered(monkeypatch):
    monkeypatch.delenv("INKYPI_DATA_DIR", raising=False)

    assert FONTS["microsoft-yahei"] == "msyh.ttf"
    assert FONTS["microsoft-yahei-bold"] == "msyhbd.ttf"
    assert Path(get_font_path("microsoft-yahei")).name == "NotoSansSC-VF.ttf"
    assert Path(get_font_path("microsoft-yahei-bold")).name == "NotoSansSC-VF.ttf"


def test_base_plugin_css_defaults_to_microsoft_yahei():
    css = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "plugins"
        / "base_plugin"
        / "render"
        / "plugin.css"
    ).read_text(encoding="utf-8")

    assert 'font-family: "Microsoft YaHei"' in css
    assert "Arial, sans-serif" in css
