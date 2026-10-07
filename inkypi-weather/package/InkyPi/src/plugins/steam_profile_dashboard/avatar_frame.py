"""Gilded frame around the console rail avatar.

Drawn procedurally at 4x and downsampled once per process. The avatar window
stays fully transparent so the photo is never covered. Strokes are at least
2 px at final size, the gold is yellow-dominant and the gems are pure red so
the frame survives Spectra 6 dithering on the epd7in3e panel.
"""

from functools import lru_cache

from PIL import Image, ImageDraw


SCALE = 4
FRAME_SIZE = (180, 178)
AVATAR_WINDOW = (16, 16, 162, 162)
BAND_OUTER = (5, 5, 173, 173)
BAND_WIDTH = 10
CORNERS = ((10, 10), (168, 10), (10, 168), (168, 168))
STUDS = ((10, 89), (168, 89), (89, 168))
CROWN_CENTER_X = 89

GOLD_HIGHLIGHT = (255, 240, 170)
GOLD = (236, 190, 70)
GOLD_MID = (214, 160, 46)
GOLD_DEEP = (150, 100, 22)
BRONZE = (70, 42, 8)
RUBY = (214, 24, 36)
RUBY_HIGHLIGHT = (255, 130, 130)

# Colour across the band from its outer edge to its inner edge.
BAND_PROFILE = (
    (0.0, GOLD_DEEP),
    (0.2, GOLD_HIGHLIGHT),
    (0.5, GOLD),
    (0.78, GOLD_MID),
    (1.0, GOLD_DEEP),
)
# Light falls from the top-left, so the bottom and right sides sit in shade.
SIDE_SHADE = {"top": 1.0, "left": 0.94, "right": 0.8, "bottom": 0.72}


def _px(value):
    return round(value * SCALE)


def _mix(stops, position):
    for (start, low), (end, high) in zip(stops, stops[1:]):
        if position <= end:
            weight = 0.0 if end == start else (position - start) / (end - start)
            return tuple(round(a + (b - a) * weight) for a, b in zip(low, high))
    return stops[-1][1]


def _shade(color, factor):
    return tuple(round(channel * factor) for channel in color)


def _draw_band(draw):
    x0, y0, x1, y1 = (_px(value) for value in BAND_OUTER)
    width = _px(BAND_WIDTH)
    for step in range(width):
        color = _mix(BAND_PROFILE, step / (width - 1))
        # Insetting both ends of every line by its depth mitres the corners.
        draw.line((x0 + step, y0 + step, x1 - step, y0 + step), fill=_shade(color, SIDE_SHADE["top"]))
        draw.line((x0 + step, y1 - step, x1 - step, y1 - step), fill=_shade(color, SIDE_SHADE["bottom"]))
        draw.line((x0 + step, y0 + step + 1, x0 + step, y1 - step - 1), fill=_shade(color, SIDE_SHADE["left"]))
        draw.line((x1 - step, y0 + step + 1, x1 - step, y1 - step - 1), fill=_shade(color, SIDE_SHADE["right"]))
    draw.rectangle((x0, y0, x1, y1), outline=BRONZE, width=_px(1.5))
    draw.rectangle(tuple(_px(value) for value in (14.5, 14.5, 163.5, 163.5)), outline=BRONZE, width=_px(1.5))


def _diamond(draw, center_x, center_y, radius, fill):
    draw.polygon(
        (
            (center_x, center_y - radius),
            (center_x + radius, center_y),
            (center_x, center_y + radius),
            (center_x - radius, center_y),
        ),
        fill=fill,
    )


def _gem(draw, center_x, center_y, radius):
    ring = radius + _px(1.2)
    draw.ellipse((center_x - ring, center_y - ring, center_x + ring, center_y + ring), fill=BRONZE)
    draw.ellipse((center_x - radius, center_y - radius, center_x + radius, center_y + radius), fill=RUBY)
    glint = radius * 0.38
    glint_x, glint_y = center_x - radius * 0.42, center_y - radius * 0.42
    draw.ellipse((glint_x - glint, glint_y - glint, glint_x + glint, glint_y + glint), fill=RUBY_HIGHLIGHT)


def _draw_corners(draw):
    for corner_x, corner_y in CORNERS:
        center_x, center_y = _px(corner_x), _px(corner_y)
        _diamond(draw, center_x, center_y, _px(9), BRONZE)
        _diamond(draw, center_x, center_y, _px(7.6), GOLD)
        _diamond(draw, center_x, center_y, _px(5.2), GOLD_HIGHLIGHT)
        _gem(draw, center_x, center_y, _px(3.2))


def _draw_studs(draw):
    for stud_x, stud_y in STUDS:
        center_x, center_y = _px(stud_x), _px(stud_y)
        for radius, fill in ((_px(3), BRONZE), (_px(2.1), GOLD_HIGHLIGHT)):
            draw.ellipse((center_x - radius, center_y - radius, center_x + radius, center_y + radius), fill=fill)


def _draw_crown(draw):
    cx = CROWN_CENTER_X
    outline = (
        (cx - 15, 14.5), (cx - 14, 4.5), (cx - 10.5, 10), (cx - 7, 6.5), (cx - 3.5, 9.5),
        (cx, 1.5), (cx + 3.5, 9.5), (cx + 7, 6.5), (cx + 10.5, 10), (cx + 14, 4.5), (cx + 15, 14.5),
    )
    draw.polygon([(_px(x), _px(y)) for x, y in outline], fill=GOLD, outline=BRONZE, width=_px(1.2))
    draw.rectangle(tuple(_px(value) for value in (cx - 15.5, 11, cx + 15.5, 15)),
                   fill=GOLD_DEEP, outline=BRONZE, width=_px(1))
    for tip_x, tip_y in ((cx - 14, 4.5), (cx - 7, 6.5), (cx, 1.5), (cx + 7, 6.5), (cx + 14, 4.5)):
        center_x, center_y = _px(tip_x), _px(tip_y)
        for radius, fill in ((_px(1.9), BRONZE), (_px(1.2), GOLD_HIGHLIGHT)):
            draw.ellipse((center_x - radius, center_y - radius, center_x + radius, center_y + radius), fill=fill)
    for dot_x in (cx - 9, cx + 9):
        center_x, center_y = _px(dot_x), _px(13)
        radius = _px(1.1)
        draw.ellipse((center_x - radius, center_y - radius, center_x + radius, center_y + radius), fill=GOLD_HIGHLIGHT)
    _gem(draw, _px(cx), _px(12.6), _px(1.8))


@lru_cache(maxsize=1)
def gilded_avatar_frame():
    """Return the shared RGBA overlay; callers must paste it, never mutate it."""
    width, height = FRAME_SIZE
    canvas = Image.new("RGBA", (width * SCALE, height * SCALE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    _draw_band(draw)
    _draw_corners(draw)
    _draw_studs(draw)
    _draw_crown(draw)
    frame = canvas.resize(FRAME_SIZE, Image.Resampling.LANCZOS)
    canvas.close()
    # LANCZOS rings faintly past hard edges; drop the imperceptible alpha.
    frame.putalpha(frame.getchannel("A").point(lambda value: 0 if value < 8 else value))
    # Resampling can spill a faint fringe inward; the photo window stays clear.
    frame.paste((0, 0, 0, 0), AVATAR_WINDOW)
    return frame
