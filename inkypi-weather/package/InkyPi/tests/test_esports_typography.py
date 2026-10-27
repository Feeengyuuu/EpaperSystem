"""Game/event routing, private-font fallback and small bilingual score bounds."""

import gc
from pathlib import Path
import shutil
import weakref

from PIL import Image, ImageDraw
import pytest

from plugins.sports_dashboard import esports_fonts as fonts
from plugins.sports_dashboard.esports_font_runs import EsportsFont
from plugins.sports_dashboard.render_fonts import render_font_scope
from plugins.sports_dashboard.score_fonts import FONT_DIR
from plugins.sports_dashboard.sports_dashboard import SportsDashboard
from utils.app_utils import get_base_ui_font
from utils.fonts import load_font


@pytest.fixture
def private(monkeypatch, tmp_path):
    monkeypatch.setenv("INKYPI_DATA_DIR", str(tmp_path))
    root = tmp_path / "fonts"
    root.mkdir()
    # Redistributable fixture only: never put proprietary font bytes in tests.
    for key in fonts.PRIVATE_KEYS:
        shutil.copyfile(FONT_DIR / "BarlowCondensed-Bold.ttf", root / f"sports-{key}-score.ttf")
    return root


@pytest.mark.parametrize("name,family", [
    ("Tesla Cup 2026", "tesla"), ("PGL Masters Bucharest", "pgl"),
    ("BLAST Open", "blast"), ("ESL Pro League", "esl"), ("IEM Krakow", "iem"),
    ("European Pro League", "montserrat"), ("ESEA Advanced", "cs2"),
    ("StarSeries", "roboto"), ("CS Asia Championships", "industry"),
    ("Esports Nations Cup", "gear"), ("CCT Europe", "cct"),
    ("Dust2.dk POWER Ligaen", "dust2"), ("unknown cup", "cs2"),
])
def test_event_identity_routes_known_organizers_without_guessing(name, family):
    assert fonts.event_family({"event_name": name}) == family
    assert fonts.event_family({"main": {"event_name": name}}) == family
    assert fonts.event_family({"main": {"team_a": name}}) == "cs2"


def test_scope_role_and_bad_font_fallback_do_not_leak(private):
    (private / "sports-esports-dota-score.ttf").write_bytes(b"invalid")
    assert fonts.active_font(12, True) is None
    with fonts.type_scope("cs2", "pgl"):
        body = SportsDashboard._font(12, True)
        assert Path(body.path).name == "sports-esports-cs2-score.ttf"

        @fonts.esports_role("event")
        def event():
            return SportsDashboard._font(12, True)

        assert Path(event().path).name == "sports-esports-pgl-event-score.ttf"
        with fonts.type_scope("riot"):
            assert Path(SportsDashboard._font(12, True).path).name == "sports-esports-riot-score.ttf"
        assert Path(SportsDashboard._font(12, True).path) == Path(body.path)
        with fonts.type_scope("dota"):
            assert Path(SportsDashboard._font(12, True).path) == Path(get_base_ui_font(12, True).path)
    assert fonts.active_font(12, True) is None


def test_missing_and_conflicting_private_files_keep_ui(monkeypatch, tmp_path, private):
    shutil.copyfile(private / "sports-esports-cs2-score.ttf", private / "sports-esports-cs2-score.otf")
    with fonts.type_scope("cs2"):
        assert Path(SportsDashboard._font(12, True).path) == Path(get_base_ui_font(12, True).path)
    monkeypatch.setenv("INKYPI_DATA_DIR", str(tmp_path / "absent"))
    with fonts.type_scope("dota"):
        assert Path(SportsDashboard._font(12, True).path) == Path(get_base_ui_font(12, True).path)


def test_original_unsupported_glyphs_use_ui_per_character():
    primary = load_font(FONT_DIR / "BarlowCondensed-Bold.ttf", 18)
    fallback = get_base_ui_font(18, True)
    mixed = EsportsFont(primary, fallback)
    value = "T1 对阵 BLG · 2-1"
    runs = mixed._runs(value)
    assert any(font is fallback and "对阵" in text for font, text in runs)
    assert any(font is primary and "T1" in text for font, text in runs)
    assert mixed.covers(value)
    image = Image.new("RGB", (240, 50))
    draw = ImageDraw.Draw(image)
    SportsDashboard._draw_text_in_box(draw, (0, 0, 240, 50), value, mixed, "white")
    assert image.getbbox() is not None
    # Changing the Latin face cannot silently remove the CJK text.
    assert "对阵" in mixed.safe_text(value)


@pytest.mark.parametrize("game", ["cs2", "riot", "dota"])
@pytest.mark.parametrize("text,width", [("128-127", 32), ("00:59", 30), ("2-1", 15), ("100", 18)])
def test_full_numeric_information_fits_without_ellipsis(private, game, text, width):
    draw = ImageDraw.Draw(Image.new("RGB", (200, 50)))
    with fonts.type_scope(game):
        result, font = SportsDashboard._fit_text(draw, text, width, 24, True, min_size=12)
        assert result == text
        box = draw.textbbox((0, 0), result, font=font)
        assert box[2] - box[0] <= width


def test_fonts_follow_weak_render_ownership(private):
    with render_font_scope(), fonts.type_scope("riot"):
        font = SportsDashboard._font(13, True)
        assert SportsDashboard._font(13, True) is font
        ref = weakref.ref(font)
        del font
        gc.collect()
        assert ref() is None


def test_private_installer_allows_exact_esports_names_only():
    import importlib.util
    path = Path(__file__).resolve().parents[1] / "install/lib/private_score_fonts.py"
    spec = importlib.util.spec_from_file_location("esports_font_policy_test", path)
    policy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(policy)
    for key in fonts.PRIVATE_KEYS:
        assert key in policy.LEAGUES
        assert f"sports-{key}-score.woff2" in policy.FONT_NAMES
    assert "sports-esports-unknown-score.ttf" not in policy.FONT_NAMES
