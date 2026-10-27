"""Actual matchup ink stays on the team axis with private and fallback faces."""

from datetime import datetime, timezone

from PIL import Image, ImageDraw
import pytest

from plugins.sports_dashboard.esports_font_runs import EsportsFont
from plugins.sports_dashboard.esports_fonts import type_scope
from plugins.sports_dashboard.score_fonts import FONT_DIR
from plugins.sports_dashboard.sports_dashboard import SportsDashboard
from tests.test_sports_dashboard import _plugin
from utils.app_utils import get_base_ui_font
from utils.fonts import load_font


def face(size, private):
    fallback = get_base_ui_font(size, True)
    if private:
        # Open font fixture exercises the same composite adapter as private faces.
        return EsportsFont(load_font(FONT_DIR / "BarlowCondensed-Bold.ttf", size), fallback)
    return fallback


@pytest.mark.parametrize("private", [False, True])
@pytest.mark.parametrize("align", ["left", "center", "right"])
@pytest.mark.parametrize("value", ["0:1", "128-127", "VS", "T1 对阵 BLG"])
def test_match_text_actual_ink_is_centered_without_double_bearing(private, align, value):
    image = Image.new("L", (240, 100))
    box = (20, 30, 220, 70)
    SportsDashboard._draw_esports_match_text(ImageDraw.Draw(image), box, value, face(20, private), 255, align)
    ink = image.getbbox()
    assert ink is not None
    assert box[0] <= ink[0] <= ink[2] <= box[2]
    assert box[1] <= ink[1] <= ink[3] <= box[3]
    assert abs((ink[1] + ink[3]) / 2 - 50) <= 1.5
    if align == "center":
        assert abs((ink[0] + ink[2]) / 2 - 120) <= 2
    elif align == "left":
        assert ink[0] - box[0] <= 3
    else:
        assert box[2] - ink[2] <= 3


def test_mixed_team_name_retains_one_shared_baseline():
    font = face(20, True)
    value = "T1 中国"
    result = Image.new("L", (180, 70))
    SportsDashboard._draw_esports_match_text(ImageDraw.Draw(result), (10, 10, 170, 60), value, font, 255)
    # Independently draw each language run on the same baseline, then compare ink.
    expected = Image.new("L", (180, 70))
    draw = ImageDraw.Draw(expected)
    x = 10
    for run_font, run in font._runs(value):
        draw.text((x, 40), run, font=run_font, fill=255, anchor="ls")
        x += run_font.getlength(run)
    assert result.crop(result.getbbox()).tobytes() == expected.crop(expected.getbbox()).tobytes()


def capture_matchups(monkeypatch, private):
    plugin = _plugin()
    monkeypatch.setattr(SportsDashboard, "_font", staticmethod(lambda size, bold=False: face(size, private)))
    labels, logos = [], []
    original = plugin._draw_esports_match_text

    def text(draw, box, value, font, color, align="center"):
        mask = Image.new("L", (800, 480))
        original(ImageDraw.Draw(mask), box, value, font, 255, align)
        ink = mask.getbbox()
        assert ink is not None
        assert box[0] - 1 <= ink[0] <= ink[2] <= box[2] + 1
        assert box[1] - 1 <= ink[1] <= ink[3] <= box[3] + 1
        labels.append((value, box, ink, align))
        return original(draw, box, value, font, color, align)

    monkeypatch.setattr(plugin, "_draw_esports_match_text", text)
    monkeypatch.setattr(plugin, "_draw_valve_team_icon", lambda image, draw, event, side, x, y, size: logos.append((x, y, size)))
    monkeypatch.setattr(plugin, "_draw_team_logo", lambda image, draw, url, x, y, size, name: logos.append((x, y, size)))
    return plugin, labels, logos


@pytest.mark.parametrize("private", [False, True])
@pytest.mark.parametrize("series", ["CS", "TI"])
@pytest.mark.parametrize("state,score", [("unstarted", "VS"), ("inProgress", "0:1"), ("completed", "0:2")])
def test_valve_scores_share_team_axis_and_names_stay_in_mirrored_columns(monkeypatch, private, series, state, score):
    plugin, labels, logos = capture_matchups(monkeypatch, private)
    monkeypatch.setattr(plugin, "_draw_valve_focus_event_logo", lambda *args: False)
    monkeypatch.setattr(plugin, "_draw_valve_ti_title_wordmark", lambda *args: False)
    now = datetime(2026, 10, 27, 12, tzinfo=timezone.utc)
    event = {"start": now, "series": series, "state": state, "team_a": "Long International Alpha 中文队",
             "team_b": "Long International Omega 中文队", "wins_a": None if state == "unstarted" else 0,
             "wins_b": None if state == "unstarted" else 2 if state == "completed" else 1,
             "stage": "Group B", "best_of": 3, "score_kind": "MAPS"}
    image = Image.new("RGB", (800, 480))
    draw = ImageDraw.Draw(image)
    with type_scope("dota" if series == "TI" else "cs2"):
        plugin._draw_valve_esports_focus_card(image, draw, 556, 244, 78,
                                            {"series": series, "event_name": "Test Event"}, event, now, (200, 20, 20))
        plugin._draw_valve_esports_recent_row(image, draw, 556, 244, 310, event, (200, 20, 20))
    scores = [row for row in labels if row[0] == score]
    assert len(scores) == 2
    for item, (left, right) in zip(scores, (logos[:2], logos[2:])):
        axis = left[1] + left[2] / 2
        assert axis == right[1] + right[2] / 2
        assert abs((item[2][1] + item[2][3]) / 2 - axis) <= 1.5
    row_names = [row for row in labels if row[1][1] > 310 and row[0] != score]
    assert [row[3] for row in row_names] == ["left", "right"]
    assert row_names[0][2][2] < scores[1][1][0]
    assert row_names[1][2][0] > scores[1][1][2]


@pytest.mark.parametrize("private", [False, True])
def test_lol_compact_names_and_scores_fit_between_mirrored_logos(monkeypatch, private):
    plugin, labels, logos = capture_matchups(monkeypatch, private)
    now = datetime(2026, 10, 27, 12, tzinfo=timezone.utc)
    event = {"start": now, "state": "completed", "wins_a": 0, "wins_b": 2,
             "team_a": "International 中文 Alpha Team", "team_b": "International 中文 Omega Team"}
    image = Image.new("RGB", (800, 480))
    draw = ImageDraw.Draw(image)
    with type_scope("riot"):
        plugin._draw_lpl_next_row(image, draw, 556, 244, 275, event, now, league_key="WORLDS")
        plugin._draw_lpl_recent_result_row(image, draw, 556, 244, 402, event, league_key="WORLDS")
    for y, value, pair in ((275, "VS", logos[:2]), (402, "0-2", logos[2:])):
        score = next(row for row in labels if row[0] == value)
        axis = pair[0][1] + pair[0][2] / 2
        assert axis == pair[1][1] + pair[1][2] / 2
        assert abs((score[2][1] + score[2][3]) / 2 - axis) <= 1.5
        names = [row for row in labels if row[1][1] >= y + 13 and row[1][3] <= y + 35 and row[0] != value]
        assert [row[3] for row in names] == ["left", "right"]
        assert names[0][2][2] < score[1][0]
        assert names[1][2][0] > score[1][2]


@pytest.mark.parametrize("private", [False, True])
@pytest.mark.parametrize("league_key", ["LPL", "LCK", "MSI", "WORLDS"])
@pytest.mark.parametrize("teams,odds", [(("CFO", "KC"), ("4.88", "1.26")),
                                      (("International 中文 Alpha", "International 中文 Omega"), ("123.45", "9.87"))])
def test_lol_upcoming_odds_have_actual_ink_gap_without_moving_matchup(monkeypatch, private, league_key, teams, odds):
    plugin, labels, logos = capture_matchups(monkeypatch, private)
    now = datetime(2026, 10, 27, 12, tzinfo=timezone.utc)
    y = 275
    event = {"start": now, "state": "unstarted", "team_a": teams[0], "team_b": teams[1],
             "odds": {"team_a": odds[0], "team_b": odds[1]}}
    image = Image.new("RGB", (800, 480))
    with type_scope("riot"):
        plugin._draw_lpl_next_row(image, ImageDraw.Draw(image), 556, 244, y, event, now, league_key=league_key)
    prices = [row for row in labels if row[0] in odds]
    names = [row for row in labels if row[1][1] == y + 18 and row[0] != "VS"]
    assert [row[0] for row in prices] == list(odds)  # Full decimal values survive fitting.
    assert [row[3] for row in prices] == ["left", "right"]
    assert [row[1] for row in names] == [(612, y + 18, 662, y + 34), (694, y + 18, 744, y + 34)]
    assert next(row for row in labels if row[0] == "VS")[1] == (665, y + 18, 691, y + 34)
    assert logos == [(580, y + 13, 26), (750, y + 13, 26)]
    for name, price in zip(names, prices):
        assert price[2][1] - name[2][3] >= 2
        assert y + 44 - price[2][3] >= 2
        assert price[1][0] <= price[2][0] <= price[2][2] <= price[1][2]


@pytest.mark.parametrize("odds", [{}, {"team_a": "4.88"}, {"team_b": "1.26"}])
def test_lol_upcoming_incomplete_odds_do_not_invent_a_price(monkeypatch, odds):
    plugin, labels, _ = capture_matchups(monkeypatch, True)
    now = datetime(2026, 10, 27, 12, tzinfo=timezone.utc)
    event = {"start": now, "state": "unstarted", "team_a": "CFO", "team_b": "KC", "odds": odds}
    image = Image.new("RGB", (800, 480))
    with type_scope("riot"):
        plugin._draw_lpl_next_row(image, ImageDraw.Draw(image), 556, 244, 275, event, now, league_key="WORLDS")
    assert {row[0] for row in labels}.isdisjoint({"4.88", "1.26"})
