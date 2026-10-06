"""Pure geometry for the Stock Tracker portfolio trend chart: no drawing, no plugin state."""

from __future__ import annotations

from collections.abc import Sequence

Point = tuple[float, float]


def value_bounds(values: Sequence[float], padding_ratio: float = 0.14) -> tuple[float, float]:
    """Pad the observed range so the line never touches the plot edge."""

    low = min(values)
    high = max(values)
    span = high - low
    if span < 1e-9:
        span = max(abs(values[0]) * 0.002, 1.0)
    return low - span * padding_ratio, high + span * padding_ratio


def plot_points(
    box: tuple[float, float, float, float],
    values: Sequence[float],
    vmin: float,
    vmax: float,
) -> list[Point]:
    left, top, right, bottom = box
    last = max(len(values) - 1, 1)
    return [
        (left + (right - left) * index / last, bottom - (bottom - top) * (float(value) - vmin) / (vmax - vmin))
        for index, value in enumerate(values)
    ]


def monotone_curve(points: Sequence[Point], subdivisions: int = 8) -> list[Point]:
    """Fritsch-Carlson monotone cubic through every point; no segment leaves its endpoints' range."""

    if len(points) < 3:
        return [(float(x), float(y)) for x, y in points]
    xs = [float(x) for x, _y in points]
    ys = [float(y) for _x, y in points]
    widths = [xs[index + 1] - xs[index] for index in range(len(xs) - 1)]
    slopes = [(ys[index + 1] - ys[index]) / width if width else 0.0 for index, width in enumerate(widths)]
    tangents = [slopes[0]] + [0.0] * (len(xs) - 2) + [slopes[-1]]
    for index in range(1, len(xs) - 1):
        before, after = slopes[index - 1], slopes[index]
        if before * after > 0:
            # Weighted harmonic mean keeps both Hermite tangents within 3x the secant.
            w1 = 2 * widths[index] + widths[index - 1]
            w2 = widths[index] + 2 * widths[index - 1]
            tangents[index] = (w1 + w2) / (w1 / before + w2 / after)
    curve = [(xs[0], ys[0])]
    for index, width in enumerate(widths):
        for step in range(1, subdivisions + 1):
            t = step / subdivisions
            y = ((1 + 2 * t) * (1 - t) ** 2 * ys[index]
                 + t * (1 - t) ** 2 * width * tangents[index]
                 + t * t * (3 - 2 * t) * ys[index + 1]
                 + t * t * (t - 1) * width * tangents[index + 1])
            curve.append((xs[index] + width * t, y))
    curve[-1] = (xs[-1], ys[-1])
    return curve


def split_at_baseline(curve: Sequence[Point], base_y: float) -> list[tuple[list[Point], bool]]:
    """Cut the curve where it crosses base_y; above means screen y <= base_y (value >= start)."""

    if not curve:
        return []
    runs: list[tuple[list[Point], bool]] = []
    run = [curve[0]]
    above = curve[0][1] <= base_y
    for start, end in zip(curve, curve[1:], strict=False):
        end_above = end[1] <= base_y
        if end_above != above:
            ratio = (base_y - start[1]) / (end[1] - start[1])
            cross = (start[0] + (end[0] - start[0]) * ratio, float(base_y))
            run.append(cross)
            if any(point != run[0] for point in run[1:]):
                runs.append((run, above))
            run = [cross]
            above = end_above
        run.append(end)
    if any(point != run[0] for point in run[1:]):
        runs.append((run, above))
    return runs
