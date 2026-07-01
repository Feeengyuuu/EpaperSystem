import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PIL import ImageFont  # noqa: E402
from utils.app_utils import DEFAULT_FONT_FAMILY, FONTS, get_available_font_names, get_font, get_font_path  # noqa: E402

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


def test_yahei_font_path_aliases_are_registered():
    assert FONTS["microsoft-yahei"] == "msyh.ttf"
    assert FONTS["microsoft-yahei-bold"] == "msyhbd.ttf"
    assert Path(get_font_path("microsoft-yahei")).name == "msyh.ttf"
    assert Path(get_font_path("microsoft-yahei-bold")).name == "msyhbd.ttf"


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