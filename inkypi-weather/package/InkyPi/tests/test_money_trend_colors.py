"""Colour denotes numeric comparison, never curve slope or economic benefit."""

from copy import deepcopy

import pytest

from plugins.money_radar import render
from plugins.money_radar.trend_lines import delta, direction, segments
from utils.page_canvas import PageCanvas


class RecordedCanvas(PageCanvas):
    def __init__(self, mode="day"):
        super().__init__(render._palette({"themeMode": mode}))
        self.texts = []

    def text(self, xy, text, size=14, color="ink", **kwargs):
        self.texts.append((str(text), color, size, xy))
        return super().text(xy, text, size, color, **kwargs)


@pytest.mark.parametrize("value,tone", [(1, "up"), (-1, "down"), (0, "muted"),
                                         (None, "muted"), (float("nan"), "muted")])
def test_only_finite_numeric_difference_has_direction(value, tone):
    assert direction(value)[1] == tone
    assert delta(4.2, None) is None


def test_baseline_crossing_splits_line_without_changing_points():
    pieces = list(segments([(0, 10), (10, 0)], [-2, 2]))
    assert pieces == [((0, 10), (5, 5), "down"), ((5, 5), (10, 0), "up")]
    assert list(segments([(0, 0), (1, 1)], [0, 0]))[0][-1] == "muted"
    assert list(segments([(0, 0), None, (2, 2)], [1, None, 2])) == []
    assert list(segments([(0, 0), (1, 1)], [None, 2]))[0][-1] == "muted"


@pytest.mark.parametrize("mode", ["day", "night"])
def test_sparkline_uses_period_start_and_neutral_when_baseline_missing(mode):
    canvas = RecordedCanvas(mode)
    render.sparkline(canvas, (10, 10, 110, 50), [10, 8, 12])
    # Falling below the first point is red; crossing it turns the same curve green.
    assert canvas.image.getpixel((35, 40)) == canvas.color("down")
    assert canvas.image.getpixel((110, 10)) == canvas.color("up")
    blank = RecordedCanvas(mode)
    render.sparkline(blank, (10, 10, 110, 50), [10, 8, 12], baseline=None)
    assert blank.image.getpixel((110, 10)) == blank.color("muted")


@pytest.mark.parametrize("mode", ["day", "night"])
def test_yield_curve_uses_each_tenor_month_comparison_not_upward_slope(monkeypatch, mode):
    rows = {symbol: {"last": 1 + i, "month_ago": 1.2 + i}
            for i, (symbol, _label) in enumerate(render.CURVE_POINTS)}
    payload = {"markets": {"data": {"quotes": rows}}}
    before = deepcopy(payload)
    comparisons = []
    original = render.draw_segments

    def record(canvas, points, differences, **kwargs):
        comparisons.append(differences)
        original(canvas, points, differences, **kwargs)

    monkeypatch.setattr(render, "draw_segments", record)
    canvas = RecordedCanvas(mode)
    render._curve(canvas, payload)
    assert comparisons[0] == pytest.approx([-0.2] * 4)
    assert all(tone == "down" for value, tone, _size, _xy in canvas.texts if value in {"1.00", "2.00", "3.00", "4.00"})
    assert {"今天", "一个月前"} <= {value for value, *_ in canvas.texts}
    assert any("非利好判断" in value for value, *_ in canvas.texts)
    assert payload == before


def test_missing_yield_comparison_is_neutral_and_not_synthesized(monkeypatch):
    rows = {symbol: {"last": 1 + i} for i, (symbol, _label) in enumerate(render.CURVE_POINTS)}
    canvas = RecordedCanvas()
    render._curve(canvas, {"markets": {"data": {"quotes": rows}}})
    assert all(tone == "muted" for value, tone, *_ in canvas.texts if value in {"1.00", "2.00", "3.00", "4.00"})


def test_macro_values_follow_numeric_direction_with_explicit_units_and_missing_base():
    payload = {"macro": {"data": {"source": "BLS",
        "cpi": {"value": 3.7, "previous": 3.4, "period": "2026-09"},
        "unemployment": {"value": 4.2, "previous": None, "period": "2026-09"}}},
        "markets": {"data": {"quotes": {"DX-Y.NYB": {"last": 102.3, "month_ago": 101.25},
                                        "GC=F": {"last": 4072, "month_ago": 4168}}}}}
    canvas = RecordedCanvas()
    render._macro_tiles(canvas, payload)
    drawn = {text: tone for text, tone, *_ in canvas.texts}
    assert drawn["3.7%"] == drawn["+0.30pp"] == "up"
    assert drawn["4.2%"] == drawn["—"] == "muted"
    assert drawn["102.3"] == drawn["+1.05点"] == "up"
    assert drawn["$4,072"] == drawn["-$96"] == "down"
    assert canvas.image.getpixel((450, 206)) == render.CARD_ACCENTS["cpi"]
    assert canvas.image.getpixel((650, 270)) == render.CARD_ACCENTS["gold"]


def test_yield_sparkline_exposes_three_month_window_and_month_baseline(monkeypatch):
    captured = []
    monkeypatch.setattr(render, "sparkline", lambda *args, **kw: captured.append((args, kw)))
    canvas = RecordedCanvas()
    render._yield_tile(canvas, {"markets": {"data": {"quotes": {
        "^TNX": {"last": 5.0, "month_ago": 5.25, "series": [4.8, 5.3, 5.0]}}}}}, (594, 788))
    assert captured[0][1]["baseline"] == 5.25
    drawn = {value: color for value, color, *_ in canvas.texts}
    assert drawn["5.00%"] == "down"
    assert "3个月 · 虚线=1月前" in drawn
    assert "-25 基点" in drawn


def test_large_numeric_text_keeps_all_digits_and_units():
    canvas = RecordedCanvas()
    value = "+12345.67pp"
    render._number(canvas, (0, 20), value, 20, 54)
    text, _tone, size, _xy = canvas.texts[-1]
    assert text == value and canvas.width(value, size) <= 54
