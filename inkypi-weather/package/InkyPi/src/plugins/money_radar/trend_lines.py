"""Numeric direction and baseline-relative line segments, independent of slope."""

import math


def finite(value):
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        return None
    return float(value) if math.isfinite(value) else None


def delta(current, previous):
    current, previous = finite(current), finite(previous)
    return current - previous if current is not None and previous is not None else None


def direction(change):
    change = finite(change)
    if change is None or abs(change) < 1e-9:
        return "flat", "muted"
    return ("up", "up") if change > 0 else ("down", "down")


def segments(points, differences):
    """Split only at a comparison-baseline crossing; never infer from line slope.

    Missing point coordinates leave a gap. Missing comparison data preserves a
    neutral segment, instead of inventing a previous value or joining across it.
    """
    for start, end, before, after in zip(points, points[1:], differences, differences[1:]):
        if start is None or end is None:
            continue
        before, after = finite(before), finite(after)
        if before is None or after is None:
            yield start, end, "muted"
        elif before * after < 0:
            fraction = abs(before) / (abs(before) + abs(after))
            cross = (start[0] + (end[0] - start[0]) * fraction,
                     start[1] + (end[1] - start[1]) * fraction)
            yield start, cross, direction(before)[1]
            yield cross, end, direction(after)[1]
        else:
            yield start, end, direction(before + after)[1]


def draw_segments(canvas, points, differences, width=2):
    for start, end, tone in segments(points, differences):
        canvas.draw.line((*start, *end), fill=canvas.color(tone), width=width)
