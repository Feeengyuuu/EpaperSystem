import pickle
import re
import shutil
import sys
from pathlib import Path

import pytest
from PIL import Image, ImageChops, ImageDraw, ImageFont

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_DIR))

from utils import fonts  # noqa: E402
from utils.app_utils import get_base_ui_font  # noqa: E402
from utils.fonts import GlyphSafeFont, SystemFont, load_font  # noqa: E402

FONT_DIR = SRC_DIR / "static" / "fonts"
CJK_FONT = FONT_DIR / "NotoSansSC-VF.ttf"
LATIN_FONT = FONT_DIR / "Jost.ttf"


def _mask(font, text, **kwargs):
    image = Image.new("L", (900, 120), 0)
    ImageDraw.Draw(image).text((20.0, 40), text, font=font, fill=255, **kwargs)
    return image


def test_every_font_is_opened_through_load_font():
    offenders = []
    for path in SRC_DIR.rglob("*.py"):
        if path == SRC_DIR / "utils" / "fonts.py":
            continue
        source = path.read_text(encoding="utf-8")
        if re.search(r"ImageFont\.(truetype|FreeTypeFont)\(", source):
            offenders.append(str(path.relative_to(SRC_DIR)))
    assert offenders == [], (
        "open fonts with utils.fonts.load_font so every render uses the BASIC "
        f"layout and YaHei gets SystemFont: {offenders}"
    )


def test_load_font_uses_basic_layout_and_wraps_yahei(tmp_path):
    plain = load_font(LATIN_FONT, 14)
    assert type(plain) is GlyphSafeFont
    assert plain.layout_engine == ImageFont.Layout.BASIC

    yahei_named = tmp_path / "msyhbd.ttf"
    shutil.copyfile(LATIN_FONT, yahei_named)
    system = load_font(yahei_named, 14)
    assert isinstance(system, SystemFont)
    assert system.layout_engine == ImageFont.Layout.BASIC


def test_base_ui_font_is_a_system_font_only_for_yahei(monkeypatch, tmp_path):
    fallback = get_base_ui_font(12, bold=True)
    assert type(fallback) is GlyphSafeFont
    assert fallback.layout_engine == ImageFont.Layout.BASIC

    (tmp_path / "fonts").mkdir()
    shutil.copyfile(LATIN_FONT, tmp_path / "fonts" / "msyhbd.ttf")
    monkeypatch.setenv("INKYPI_DATA_DIR", str(tmp_path))
    yahei = get_base_ui_font(12, bold=True)
    assert isinstance(yahei, SystemFont)
    assert Path(yahei.path).name == "msyhbd.ttf"


def test_wide_runs_keep_the_crisp_hinted_raster():
    text = "汕头重大火灾事故调查报告"
    for size in (10, 12, 16, 24):
        plain = ImageFont.FreeTypeFont(str(CJK_FONT), size, layout_engine=ImageFont.Layout.BASIC)
        system = SystemFont(str(CJK_FONT), size)
        assert system.getlength(text) == plain.getlength(text)
        assert ImageChops.difference(_mask(system, text), _mask(plain, text)).getbbox() is None


def test_narrow_runs_are_rasterised_at_supersampled_size():
    # Whole-pixel glyph placement is what made "Jason" render as "Ja son":
    # narrow runs must come from the supersampled raster, box-filtered back.
    text = "CREDIT | Jason Kurth, Ty Buckley"
    scale = fonts.SUPERSAMPLE
    for size in (10, 12, 13):
        system = SystemFont(str(CJK_FONT), size)
        hires = ImageFont.FreeTypeFont(str(CJK_FONT), size * scale, layout_engine=ImageFont.Layout.BASIC)
        assert system.getlength(text) == hires.getlength(text) / scale

        core, (x0, y0) = system.getmask2(text, "L", anchor="ls")
        hi_core, (hx, hy) = hires.getmask2(text, "L", anchor="ls")
        canvas = Image.new("L", (core.size[0] * scale, core.size[1] * scale), 0)
        canvas.paste(Image.Image()._new(hi_core), (hx - x0 * scale, hy - y0 * scale))
        expected = canvas.reduce(scale).point(fonts._COVERAGE_LUT)
        assert ImageChops.difference(Image.Image()._new(core), expected).getbbox() is None


def test_mixed_runs_snap_wide_glyphs_to_whole_pixels():
    font = SystemFont(str(CJK_FONT), 13)
    pieces, width = font._layout("特斯拉新Roadster发布，具备")
    wide_starts = [x for wide, _, x, _ in pieces if wide]
    assert wide_starts and all(float(x).is_integer() for x in wide_starts)
    assert width == pytest.approx(font.getlength("特斯拉新Roadster发布，具备"))


@pytest.mark.parametrize("anchor", ["la", "lt", "lm", "ls", "lb", "ld", "ma", "mm", "ms", "ra", "rm", "rs", "rd"])
@pytest.mark.parametrize("stroke", [0, 1])
def test_textbbox_contains_the_drawn_ink(anchor, stroke):
    font = SystemFont(str(CJK_FONT), 12)
    text = "汕头 12·9 Jason Kurth"
    image = Image.new("L", (360, 80), 0)
    draw = ImageDraw.Draw(image)
    draw.text((180.25, 40), text, font=font, fill=255, anchor=anchor, stroke_width=stroke, stroke_fill=255)
    ink = image.getbbox()
    box = draw.textbbox((180.25, 40), text, font=font, anchor=anchor, stroke_width=stroke)
    assert ink is not None
    assert box[0] - 1 <= ink[0] and box[1] - 1 <= ink[1]
    assert ink[2] <= box[2] + 1 and ink[3] <= box[3] + 1


def test_large_sizes_and_mono_mode_use_the_plain_renderer():
    text = "Jason Kurth"
    big = SystemFont(str(CJK_FONT), fonts.MAX_EVEN_SPACING_SIZE + 2)
    plain_big = ImageFont.FreeTypeFont(str(CJK_FONT), fonts.MAX_EVEN_SPACING_SIZE + 2, layout_engine=ImageFont.Layout.BASIC)
    assert big.getlength(text) == plain_big.getlength(text)
    small = SystemFont(str(CJK_FONT), 12)
    plain_small = ImageFont.FreeTypeFont(str(CJK_FONT), 12, layout_engine=ImageFont.Layout.BASIC)
    assert small.getlength(text, mode="1") == plain_small.getlength(text, mode="1")


def test_real_font_presence_tracks_the_notdef_glyph():
    facts = SystemFont(str(CJK_FONT), 12)._facts
    assert facts.present("A") and facts.present("汕") and facts.present(" ")
    assert not facts.present("\uffff")
    assert not facts.present("\U0001f525")


class _Facts:
    def __init__(self, missing):
        self.missing = set(missing)

    def present(self, char):
        return char not in self.missing

    def wide(self, char):
        return ord(char) >= 0x2E80


def test_safe_text_swaps_missing_glyphs_for_same_font_equivalents():
    font = SystemFont(str(CJK_FONT), 12)
    font._facts = _Facts({"·", "\u200b", "\u00a0", "\U0001f525", "✓", "▸", "▶", "►", "ț", "ก"})

    assert font.safe_text("汕头“12·9”") == "汕头“12‧9”"
    assert font.safe_text("零宽\u200b空格\u00a0x") == "零宽空格 x"
    assert font.safe_text("热门\U0001f525✓完成 ▸下一项") == "热门√完成 ›下一项"
    assert font.safe_text("Constanța") == "Constanta"
    # Scripts the font lacks stay as-is: there is deliberately no font fallback.
    assert font.safe_text("กข") == "กข"
    assert font.safe_text("plain ASCII 2026-10-16") == "plain ASCII 2026-10-16"
    # Plugins compare rasters against these to detect missing glyphs.
    font._facts = _Facts({"\ufffd", "\uffff"})
    assert font.safe_text("\ufffd\uffff") == "\ufffd\uffff"


def test_measurement_and_drawing_use_the_same_safe_text():
    font = SystemFont(str(CJK_FONT), 14)
    font._facts = _Facts({"·"})
    assert font.getlength("12·9") == font.getlength("12‧9")
    assert font.getbbox("12·9") == font.getbbox("12‧9")
    assert ImageChops.difference(_mask(font, "12·9"), _mask(font, "12‧9")).getbbox() is None


def test_variants_and_pickling_keep_system_font_and_weight():
    font = SystemFont(str(CJK_FONT), 12)
    font.set_variation_by_axes([700])
    variant = font.font_variant(size=20)
    assert isinstance(variant, SystemFont) and variant.size == 20
    assert variant._variation == font._variation

    restored = pickle.loads(pickle.dumps(font))
    assert isinstance(restored, SystemFont)
    assert restored._variation == font._variation
    assert restored.getlength("Jason Kurth") == pytest.approx(font.getlength("Jason Kurth"))


def test_other_fonts_only_lose_missing_glyphs_and_keep_their_spacing():
    plain = ImageFont.FreeTypeFont(str(LATIN_FONT), 15, layout_engine=ImageFont.Layout.BASIC)
    safe = load_font(LATIN_FONT, 15)
    text = "All executive power - from the reign of ancient kings"
    assert safe.getlength(text) == plain.getlength(text)
    assert safe.getbbox(text) == plain.getbbox(text)
    assert ImageChops.difference(_mask(safe, text), _mask(plain, text)).getbbox() is None

    safe._facts = _Facts({"\u200b", "\U0001f525"})
    assert safe.safe_text("hot\u200b take \U0001f525") == "hot take "
    assert safe.getlength("hot\u200b take \U0001f525") == plain.getlength("hot take ")
