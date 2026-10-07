import sys
from pathlib import Path

from PIL import ImageFont

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_DIR))

from utils.glyph_text import font_has_glyph, glyph_safe_text


class _Mask:
    def __init__(self, char):
        self.size = (8, 8)
        self.char = char

    def __bytes__(self):
        return self.char.encode("utf-8")


class StubFont:
    """Draws only the characters it was given; everything else is .notdef."""

    def __init__(self, supported):
        self.supported = set(supported)
        self.path = f"stub-{id(self)}"

    def getmask(self, char):
        return _Mask(char if char in self.supported else "notdef")


def test_keeps_text_the_font_can_draw():
    font = StubFont("小猪佩奇完美假期·")

    assert glyph_safe_text("小猪佩奇·完美假期", font) == "小猪佩奇·完美假期"


def test_replaces_missing_middle_dot_with_a_drawable_dot():
    font = StubFont("小猪佩奇完美假期・")

    assert glyph_safe_text("小猪佩奇·完美假期", font) == "小猪佩奇・完美假期"


def test_falls_back_to_space_when_no_equivalent_exists():
    font = StubFont("AB")

    assert glyph_safe_text("A·B", font) == "A B"


def test_dashes_and_quotes_fall_back_to_ascii():
    font = StubFont("ab-'\"")

    assert glyph_safe_text("a—b ‘a’ “b”", font) == "a-b 'a' \"b\""


def test_plain_text_skips_glyph_checks():
    class Exploding:
        def getmask(self, char):
            raise AssertionError("should not render")

    assert glyph_safe_text("Resident Evil：爆发夜", Exploding()) == "Resident Evil：爆发夜"


def test_real_font_without_dot_operator_gets_middle_dot():
    font = ImageFont.truetype(str(SRC_DIR / "static" / "fonts" / "NotoSansSC-VF.ttf"), 24)

    assert font_has_glyph(font, "·")
    assert not font_has_glyph(font, "⋅")
    assert glyph_safe_text("A⋅B", font) == "A·B"
