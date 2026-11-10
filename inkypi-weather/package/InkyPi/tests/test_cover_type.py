"""The cover pages' own typefaces (plugins/base_plugin/cover_type.py)."""

import hashlib
import json
import re
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from plugins.base_plugin import cover_gallery, cover_type
from plugins.base_plugin.cover_type import CHIP, STRONG, TEXT, TITLE, CoverType, Face, Role
from utils.app_utils import get_base_ui_font


def _face_for(font, char):
    """The file of the face a composite font draws ``char`` with."""
    while hasattr(font, "primary"):
        if font.primary.covers(char):
            return Path(font.primary.path).name
        font = font.fallback
    return Path(font.path).name


def _ui(size, bold):
    return Path(get_base_ui_font(size, bold=bold).path).name


STYLE_FACES = [
    ("gallery", TITLE, "Spectral-BoldItalic.ttf"), ("gallery", TEXT, "Spectral-SemiBold.ttf"),
    ("gallery", STRONG, "Spectral-Bold.ttf"), ("gallery", CHIP, "Spectral-Bold.ttf"),
    ("comic", TITLE, "Bangers-Regular.ttf"), ("comic", TEXT, "ComicNeue-Bold.ttf"),
    ("comic", STRONG, "Bangers-Regular.ttf"), ("comic", CHIP, "Bangers-Regular.ttf"),
    ("newsstand", TITLE, "BodoniModa-Variable.ttf"), ("newsstand", TEXT, "Jost.ttf"),
    ("newsstand", STRONG, "Jost.ttf"), ("newsstand", CHIP, "Jost.ttf"),
]


@pytest.mark.parametrize("style, role, face", STYLE_FACES)
def test_each_page_sets_its_latin_and_figures_in_its_own_typeface(style, role, face):
    font = CoverType(style).font(role, 12)

    assert {_face_for(font, char) for char in "Aa7#"} == {face}


def test_chinese_comes_from_the_styles_own_face_then_from_the_ui_font():
    museum, comic, magazine = CoverType("gallery"), CoverType("comic"), CoverType("newsstand")

    # A museum label's Chinese is Noto Serif SC, a comic's ZCOOL KuaiLe, a magazine title's Noto Serif SC Black.
    assert _face_for(museum.font(TEXT, 11), "馆") == "NotoSerifSC-SemiBold-Nature.ttf"
    assert _face_for(museum.font(TITLE, 14), "馆") == "NotoSerifSC-SemiBold-Nature.ttf"
    assert _face_for(comic.font(TEXT, 11), "期") == "ZCOOLKuaiLe-Regular.ttf"
    assert _face_for(comic.font(CHIP, 10), "期") == "ZCOOLKuaiLe-Regular.ttf"
    assert _face_for(magazine.font(TITLE, 14), "周") == "NotoSerifSC-Black-Headline.ttf"
    # Small white type on a chip, and a magazine's facts beside Jost, keep the UI font's Chinese.
    assert _face_for(museum.font(CHIP, 10), "馆") == _ui(10, True)
    assert _face_for(magazine.font(TEXT, 11), "周") == _ui(11, False)
    # A character the style's Chinese face lacks (outside GB 2312) falls through to the UI font.
    assert _face_for(museum.font(TEXT, 11), "驛") == _ui(11, False)
    assert _face_for(comic.font(TITLE, 14), "驛") == _ui(14, True)


def test_a_face_is_sized_against_the_ui_font_and_stands_on_its_baseline():
    title = CoverType("gallery").font(TITLE, 14)
    ui = get_base_ui_font(14, bold=True)

    # Spectral's bold italic is drawn larger than the UI font it stands in for, so it reads as its 14 px did.
    scale = cover_type.STYLES["gallery"][TITLE].latin.scale
    assert scale > 1 and title.size == 14 and title.primary.size == round(14 * scale)
    assert title.getmetrics() == ui.getmetrics()
    # Mixed copy measures as the sum of its runs, each in its own face.
    spectral, serif = title.primary, title.fallback.primary
    assert title.getlength("Still Life 静物") == pytest.approx(spectral.getlength("Still Life ") + serif.getlength("静物"))


def test_a_variable_font_is_set_to_the_styles_weight():
    magazine = CoverType("newsstand")
    regular, bold = magazine.font(TEXT, 12).primary, magazine.font(STRONG, 12).primary

    assert Path(regular.path).name == Path(bold.path).name == "Jost.ttf"
    assert bold.getlength("Magazine") > regular.getlength("Magazine")


def test_an_unknown_style_or_an_unreadable_font_is_set_in_the_ui_font(monkeypatch, tmp_path):
    plain = CoverType("elsewhere")
    assert Path(plain.font(TITLE, 14).path).name == _ui(14, True) and not hasattr(plain.font(TITLE, 14), "primary")
    assert Path(plain.font(TEXT, 11).path).name == _ui(11, False)

    monkeypatch.setitem(cover_type.STYLES, "broken", {TEXT: Role(Face(tmp_path / "missing.ttf", 1.1)),
                                                      TITLE: Role(Face(tmp_path / "missing.ttf"), bold=True)})
    broken = CoverType("broken")
    assert not hasattr(broken.font(TEXT, 11), "primary") and Path(broken.font(TEXT, 11).path).name == _ui(11, False)
    assert Path(broken.font(TITLE, 14).path).name == _ui(14, True)


def test_every_cover_font_is_in_the_repository_with_its_source_and_licence():
    record = json.loads((cover_type.COVER_FONTS / "cover_fonts.source.json").read_text(encoding="utf-8"))["fonts"]
    faces = {face.path for roles in cover_type.STYLES.values() for role in roles.values()
             for face in (role.latin, role.chinese) if face is not None}

    assert all(path.is_file() for path in faces)
    bundled = {path.name for path in faces if path.parent == cover_type.COVER_FONTS}
    assert bundled == set(record) == {path.name for path in cover_type.COVER_FONTS.glob("*.ttf")}
    for name, entry in record.items():
        data = (cover_type.COVER_FONTS / name).read_bytes()
        assert hashlib.sha256(data).hexdigest() == entry["sha256"] and len(data) == entry["bytes"], name
        assert entry["source"].startswith("https://github.com/google/fonts/raw/main/ofl/"), name
        licence = re.search(r"\(([\w-]+\.txt)\)", entry["licence"]).group(1)
        assert "SIL Open Font License" in (cover_type.COVER_FONTS / licence).read_text(encoding="utf-8"), name


@pytest.mark.parametrize("style, title, chip, fact", [
    ("gallery", "Spectral-BoldItalic.ttf", "Spectral-Bold.ttf", "Spectral-SemiBold.ttf"),
    ("comic", "Bangers-Regular.ttf", "Bangers-Regular.ttf", "ComicNeue-Bold.ttf"),
    ("newsstand", "BodoniModa-Variable.ttf", "Jost.ttf", "Jost.ttf"),
])
def test_a_page_draws_its_header_labels_and_timeline_in_its_styles_type(monkeypatch, style, title, chip, fact):
    drawn = {}
    original = ImageDraw.ImageDraw.text

    def capture(self, xy, text, *args, **kwargs):
        drawn.setdefault(str(text), kwargs.get("font"))
        return original(self, xy, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", capture)
    labels = [cover_gallery.Label(title="Still Life", chip="1667", lines=("Ottomar Elliger", "Oil on panel"), year=1667),
              cover_gallery.Label(title="Reine de Joie", chip="1892", lines=("佚名", "Poster"), year=1892)]
    images = [Image.new("RGB", (200, 300), (120, 90, 60))] * 2

    page = cover_gallery.compose(images, labels, (800, 480), title="DAILY ART", subtitle="今日馆藏",
                                 date_text="11月9日 周一", style=style, timeline=True)

    assert page.size == (800, 480)
    assert _face_for(drawn["Still Life"], "S") == _face_for(drawn["DAILY ART"], "D") == title
    assert _face_for(drawn["1667"], "1") == chip
    assert _face_for(drawn["Ottomar Elliger"], "O") == _face_for(drawn["今日馆藏"], "A") == fact
    # The timeline's century ticks are running text; the date is strong type.
    assert _face_for(drawn["1700"], "1") == fact
    assert _face_for(drawn["11月9日 周一"], "1") == Path(CoverType(style).font(STRONG, 12).primary.path).name
