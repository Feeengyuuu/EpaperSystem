import pytest

from plugins.stocktracker.trend_chart import monotone_curve, plot_points, split_at_baseline, value_bounds


POINTS = [(0, 50), (10, 20), (20, 35), (30, 35), (40, 5), (50, 60)]


def test_monotone_curve_passes_through_every_point_and_keeps_endpoints():
    curve = monotone_curve(POINTS, subdivisions=8)

    assert len(curve) == (len(POINTS) - 1) * 8 + 1
    assert curve[0] == (0.0, 50.0)
    assert curve[-1] == (50.0, 60.0)
    for index, point in enumerate(POINTS):
        assert curve[index * 8] == pytest.approx(point)


def test_monotone_curve_never_leaves_each_segment_range():
    curve = monotone_curve(POINTS, subdivisions=8)

    for index in range(len(POINTS) - 1):
        low = min(POINTS[index][1], POINTS[index + 1][1])
        high = max(POINTS[index][1], POINTS[index + 1][1])
        for _x, y in curve[index * 8: index * 8 + 9]:
            assert low - 1e-9 <= y <= high + 1e-9


def test_monotone_curve_returns_short_input_unchanged():
    assert monotone_curve([(0, 1), (5, 2)]) == [(0.0, 1.0), (5.0, 2.0)]
    assert monotone_curve([]) == []


def test_split_at_baseline_cuts_exactly_on_the_baseline():
    runs = split_at_baseline([(0, 40), (10, 60), (20, 30), (30, 70)], 50)

    assert [above for _run, above in runs] == [True, False, True, False]
    for (first, _), (second, _) in zip(runs, runs[1:], strict=False):
        assert first[-1] == second[0]
        assert first[-1][1] == 50.0
    assert runs[0][0][0] == (0, 40)
    assert runs[0][0][-1] == pytest.approx((5.0, 50.0))
    assert runs[-1][0][-1] == (30, 70)


def test_split_at_baseline_drops_a_zero_length_run_at_the_start():
    assert split_at_baseline([(0, 50), (10, 70), (20, 80)], 50) == [([(0, 50), (10, 70), (20, 80)], False)]


def test_value_bounds_pads_the_range_and_handles_flat_series():
    low, high = value_bounds([100.0, 110.0])
    flat_low, flat_high = value_bounds([250000.0, 250000.0])
    single_low, single_high = value_bounds([5.0])

    assert (low, high) == (pytest.approx(98.6), pytest.approx(111.4))
    assert flat_low < 250000.0 < flat_high
    assert flat_high - flat_low == pytest.approx(250000 * 0.002 * 0.28)
    assert single_low < 5.0 < single_high


def test_plot_points_maps_values_into_the_box():
    assert plot_points((10, 20, 110, 120), [0.0, 50.0, 100.0], 0.0, 100.0) == [
        (10.0, 120.0),
        (60.0, 70.0),
        (110.0, 20.0),
    ]
