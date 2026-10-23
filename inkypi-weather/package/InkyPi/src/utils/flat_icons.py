"""Flat procedural icons for e-paper pages.

Every icon is solid polygons and lines in one colour (plus an optional knockout colour),
so it stays crisp after six-colour dithering. ``draw_icon(draw, kind, (x, y), size, color, knockout)``
fills the square at (x, y) with side ``size``.
"""

from __future__ import annotations

import math


def _p(x, y, size, points):
    return [(x + px * size, y + py * size) for px, py in points]


def _w(size, ratio=0.09):
    return max(1, round(size * ratio))


def _check(draw, x, y, s, color, knockout):
    draw.ellipse((x, y, x + s, y + s), fill=color)
    draw.line(_p(x, y, s, [(0.26, 0.52), (0.43, 0.69), (0.75, 0.33)]), fill=knockout, width=_w(s, 0.12), joint="curve")


def _alert(draw, x, y, s, color, knockout):
    draw.polygon(_p(x, y, s, [(0.5, 0.04), (0.97, 0.92), (0.03, 0.92)]), fill=color)
    draw.rectangle(_p(x, y, s, [(0.45, 0.34), (0.55, 0.64)]), fill=knockout)
    draw.rectangle(_p(x, y, s, [(0.45, 0.72), (0.55, 0.82)]), fill=knockout)


def _info(draw, x, y, s, color, knockout):
    draw.ellipse((x, y, x + s, y + s), fill=color)
    draw.rectangle(_p(x, y, s, [(0.44, 0.42), (0.56, 0.78)]), fill=knockout)
    draw.rectangle(_p(x, y, s, [(0.44, 0.2), (0.56, 0.32)]), fill=knockout)


def _flame(draw, x, y, s, color, knockout):
    outer = [(0.5, 0.02), (0.68, 0.24), (0.8, 0.18), (0.88, 0.48), (0.84, 0.74), (0.66, 0.94),
             (0.34, 0.94), (0.16, 0.76), (0.12, 0.5), (0.24, 0.28), (0.32, 0.42), (0.38, 0.2)]
    draw.polygon(_p(x, y, s, outer), fill=color)
    inner = [(0.5, 0.46), (0.62, 0.62), (0.64, 0.8), (0.5, 0.9), (0.36, 0.8), (0.38, 0.62)]
    draw.polygon(_p(x, y, s, inner), fill=knockout)


def _wind(draw, x, y, s, color, knockout):
    width = _w(s, 0.1)
    for top, length in ((0.28, 0.66), (0.5, 0.86), (0.72, 0.52)):
        draw.line(_p(x, y, s, [(0.06, top), (length, top)]), fill=color, width=width)
        cx, cy, r = x + length * s, y + (top - 0.1) * s, 0.1 * s
        draw.arc((cx - r, cy - r, cx + r, cy + r), 270, 90 + 360, fill=color, width=width)


def _heat(draw, x, y, s, color, knockout):
    draw.rounded_rectangle(_p(x, y, s, [(0.4, 0.04), (0.6, 0.64)]), radius=max(1, s * 0.1), fill=color)
    draw.ellipse(_p(x, y, s, [(0.28, 0.58), (0.72, 0.98)]), fill=color)
    draw.rectangle(_p(x, y, s, [(0.47, 0.22), (0.53, 0.7)]), fill=knockout)
    for top in (0.18, 0.32, 0.46):
        draw.line(_p(x, y, s, [(0.66, top), (0.82, top)]), fill=color, width=_w(s, 0.07))


def _flood(draw, x, y, s, color, knockout):
    width = _w(s, 0.1)
    for top in (0.3, 0.55, 0.8):
        points = [(x + s * (0.04 + 0.92 * i / 16), y + s * (top + 0.07 * math.sin(i / 16 * 4 * math.pi)))
                  for i in range(17)]
        draw.line(points, fill=color, width=width, joint="curve")


def _snow(draw, x, y, s, color, knockout):
    cx, cy, r = x + s / 2, y + s / 2, s * 0.46
    width = _w(s, 0.09)
    for angle in range(0, 180, 60):
        rad = math.radians(angle)
        dx, dy = math.cos(rad) * r, math.sin(rad) * r
        draw.line((cx - dx, cy - dy, cx + dx, cy + dy), fill=color, width=width)
        for sign in (1, -1):
            bx, by = cx + sign * dx * 0.6, cy + sign * dy * 0.6
            for turn in (0.6, -0.6):
                ex = bx + sign * (math.cos(rad + turn) * r * 0.32)
                ey = by + sign * (math.sin(rad + turn) * r * 0.32)
                draw.line((bx, by, ex, ey), fill=color, width=width)


def _haze(draw, x, y, s, color, knockout):
    width = _w(s, 0.1)
    for top, left, right in ((0.24, 0.18, 0.92), (0.44, 0.06, 0.8), (0.64, 0.22, 0.96), (0.84, 0.1, 0.7)):
        draw.line(_p(x, y, s, [(left, top), (right, top)]), fill=color, width=width)


def _storm(draw, x, y, s, color, knockout):
    draw.ellipse(_p(x, y, s, [(0.1, 0.18), (0.56, 0.58)]), fill=color)
    draw.ellipse(_p(x, y, s, [(0.38, 0.06), (0.9, 0.56)]), fill=color)
    draw.rectangle(_p(x, y, s, [(0.3, 0.36), (0.74, 0.58)]), fill=color)
    draw.polygon(_p(x, y, s, [(0.5, 0.46), (0.3, 0.78), (0.48, 0.78), (0.4, 1.0), (0.7, 0.66),
                              (0.52, 0.66), (0.62, 0.46)]), fill=knockout)
    draw.polygon(_p(x, y, s, [(0.52, 0.52), (0.38, 0.74), (0.52, 0.74), (0.46, 0.9), (0.64, 0.68),
                              (0.5, 0.68), (0.58, 0.52)]), fill=color)


def _quake(draw, x, y, s, color, knockout):
    points = [(0.0, 0.55), (0.18, 0.55), (0.26, 0.38), (0.34, 0.8), (0.44, 0.08), (0.54, 0.92),
              (0.64, 0.24), (0.72, 0.66), (0.8, 0.55), (1.0, 0.55)]
    draw.line(_p(x, y, s, points), fill=color, width=_w(s, 0.1), joint="curve")


def _volcano(draw, x, y, s, color, knockout):
    draw.polygon(_p(x, y, s, [(0.02, 0.96), (0.36, 0.44), (0.64, 0.44), (0.98, 0.96)]), fill=color)
    for points in ([(0.42, 0.36), (0.34, 0.06)], [(0.5, 0.34), (0.5, 0.02)], [(0.58, 0.36), (0.66, 0.06)]):
        draw.line(_p(x, y, s, points), fill=color, width=_w(s, 0.08))


def _landslide(draw, x, y, s, color, knockout):
    draw.polygon(_p(x, y, s, [(0.02, 0.96), (0.02, 0.2), (0.98, 0.96)]), fill=color)
    for cx, cy in ((0.6, 0.42), (0.78, 0.6)):
        draw.ellipse(_p(x, y, s, [(cx - 0.08, cy - 0.08), (cx + 0.08, cy + 0.08)]), fill=color)


def _home(draw, x, y, s, color, knockout):
    draw.polygon(_p(x, y, s, [(0.5, 0.04), (0.98, 0.48), (0.84, 0.48), (0.84, 0.96), (0.16, 0.96),
                              (0.16, 0.48), (0.02, 0.48)]), fill=color)
    draw.rectangle(_p(x, y, s, [(0.42, 0.62), (0.58, 0.96)]), fill=knockout)


def _clock(draw, x, y, s, color, knockout):
    draw.ellipse((x, y, x + s, y + s), fill=color)
    draw.line(_p(x, y, s, [(0.5, 0.2), (0.5, 0.52), (0.72, 0.64)]), fill=knockout, width=_w(s, 0.1))


def _calendar(draw, x, y, s, color, knockout):
    draw.rounded_rectangle(_p(x, y, s, [(0.06, 0.14), (0.94, 0.96)]), radius=max(1, s * 0.08), fill=color)
    draw.rectangle(_p(x, y, s, [(0.16, 0.38), (0.84, 0.86)]), fill=knockout)
    for left in (0.26, 0.66):
        draw.rectangle(_p(x, y, s, [(left, 0.02), (left + 0.08, 0.24)]), fill=color)


def _arrow(direction):
    def draw_arrow(draw, x, y, s, color, knockout):
        if direction == "up":
            points = [(0.5, 0.08), (0.94, 0.86), (0.06, 0.86)]
        elif direction == "down":
            points = [(0.06, 0.14), (0.94, 0.14), (0.5, 0.92)]
        else:
            points = [(0.1, 0.4), (0.9, 0.4), (0.9, 0.6), (0.1, 0.6)]
        draw.polygon(_p(x, y, s, points), fill=color)
    return draw_arrow


def _comment(draw, x, y, s, color, knockout):
    draw.rounded_rectangle(_p(x, y, s, [(0.04, 0.1), (0.96, 0.72)]), radius=max(1, s * 0.16), fill=color)
    draw.polygon(_p(x, y, s, [(0.22, 0.6), (0.48, 0.66), (0.2, 0.94)]), fill=color)
    for left in (0.24, 0.44, 0.64):
        draw.rectangle(_p(x, y, s, [(left, 0.36), (left + 0.12, 0.48)]), fill=knockout)


def _heart(draw, x, y, s, color, knockout):
    draw.ellipse(_p(x, y, s, [(0.04, 0.1), (0.54, 0.6)]), fill=color)
    draw.ellipse(_p(x, y, s, [(0.46, 0.1), (0.96, 0.6)]), fill=color)
    draw.polygon(_p(x, y, s, [(0.07, 0.46), (0.93, 0.46), (0.5, 0.94)]), fill=color)


def _star(draw, x, y, s, color, knockout):
    points = []
    for index in range(10):
        radius = 0.48 if index % 2 == 0 else 0.2
        angle = math.radians(-90 + index * 36)
        points.append((0.5 + radius * math.cos(angle), 0.54 + radius * math.sin(angle)))
    draw.polygon(_p(x, y, s, points), fill=color)


def _download(draw, x, y, s, color, knockout):
    draw.rectangle(_p(x, y, s, [(0.4, 0.04), (0.6, 0.42)]), fill=color)
    draw.polygon(_p(x, y, s, [(0.18, 0.38), (0.82, 0.38), (0.5, 0.72)]), fill=color)
    draw.rectangle(_p(x, y, s, [(0.06, 0.82), (0.94, 0.96)]), fill=color)


def _paper(draw, x, y, s, color, knockout):
    draw.polygon(_p(x, y, s, [(0.14, 0.02), (0.64, 0.02), (0.88, 0.26), (0.88, 0.98), (0.14, 0.98)]), fill=color)
    draw.polygon(_p(x, y, s, [(0.64, 0.02), (0.64, 0.26), (0.88, 0.26)]), fill=knockout)
    for top in (0.44, 0.6, 0.76):
        draw.line(_p(x, y, s, [(0.26, top), (0.76, top)]), fill=knockout, width=_w(s, 0.07))


def _gift(draw, x, y, s, color, knockout):
    draw.rectangle(_p(x, y, s, [(0.06, 0.3), (0.94, 0.5)]), fill=color)
    draw.rectangle(_p(x, y, s, [(0.12, 0.54), (0.88, 0.98)]), fill=color)
    draw.rectangle(_p(x, y, s, [(0.45, 0.3), (0.55, 0.98)]), fill=knockout)
    draw.ellipse(_p(x, y, s, [(0.22, 0.04), (0.5, 0.3)]), outline=color, width=_w(s, 0.08))
    draw.ellipse(_p(x, y, s, [(0.5, 0.04), (0.78, 0.3)]), outline=color, width=_w(s, 0.08))


def _trophy(draw, x, y, s, color, knockout):
    draw.polygon(_p(x, y, s, [(0.2, 0.04), (0.8, 0.04), (0.74, 0.44), (0.58, 0.58), (0.42, 0.58), (0.26, 0.44)]),
                 fill=color)
    draw.arc(_p(x, y, s, [(0.02, 0.08), (0.34, 0.42)]), 90, 270, fill=color, width=_w(s, 0.07))
    draw.arc(_p(x, y, s, [(0.66, 0.08), (0.98, 0.42)]), 270, 90, fill=color, width=_w(s, 0.07))
    draw.rectangle(_p(x, y, s, [(0.44, 0.58), (0.56, 0.8)]), fill=color)
    draw.rectangle(_p(x, y, s, [(0.24, 0.8), (0.76, 0.96)]), fill=color)


def _rocket(draw, x, y, s, color, knockout):
    draw.polygon(_p(x, y, s, [(0.5, 0.02), (0.68, 0.24), (0.68, 0.7), (0.32, 0.7), (0.32, 0.24)]), fill=color)
    draw.polygon(_p(x, y, s, [(0.32, 0.48), (0.12, 0.78), (0.32, 0.72)]), fill=color)
    draw.polygon(_p(x, y, s, [(0.68, 0.48), (0.88, 0.78), (0.68, 0.72)]), fill=color)
    draw.ellipse(_p(x, y, s, [(0.42, 0.28), (0.58, 0.44)]), fill=knockout)
    draw.polygon(_p(x, y, s, [(0.38, 0.74), (0.62, 0.74), (0.5, 0.98)]), fill=color)


def _pulse(draw, x, y, s, color, knockout):
    draw.ellipse((x, y, x + s, y + s), fill=color)
    draw.line(_p(x, y, s, [(0.12, 0.54), (0.32, 0.54), (0.42, 0.3), (0.56, 0.76), (0.66, 0.54), (0.88, 0.54)]),
              fill=knockout, width=_w(s, 0.09))


def _cross(draw, x, y, s, color, knockout):
    draw.ellipse((x, y, x + s, y + s), fill=color)
    width = _w(s, 0.13)
    draw.line(_p(x, y, s, [(0.3, 0.3), (0.7, 0.7)]), fill=knockout, width=width)
    draw.line(_p(x, y, s, [(0.7, 0.3), (0.3, 0.7)]), fill=knockout, width=width)


def _wrench(draw, x, y, s, color, knockout):
    draw.ellipse((x, y, x + s, y + s), fill=color)
    draw.rectangle(_p(x, y, s, [(0.3, 0.44), (0.7, 0.56)]), fill=knockout)


def _leaf(draw, x, y, s, color, knockout):
    draw.polygon(_p(x, y, s, [(0.12, 0.88), (0.18, 0.44), (0.44, 0.16), (0.9, 0.08), (0.84, 0.52),
                              (0.58, 0.82)]), fill=color)
    draw.line(_p(x, y, s, [(0.06, 0.96), (0.3, 0.7), (0.74, 0.26)]), fill=knockout, width=_w(s, 0.07))


def _bug(draw, x, y, s, color, knockout):
    width = _w(s, 0.07)
    for side in (1, -1):
        for top in (0.42, 0.58, 0.74):
            draw.line(_p(x, y, s, [(0.5, top), (0.5 + side * 0.44, top + 0.08)]), fill=color, width=width)
        draw.line(_p(x, y, s, [(0.5 + side * 0.08, 0.22), (0.5 + side * 0.22, 0.04)]), fill=color, width=width)
    draw.ellipse(_p(x, y, s, [(0.38, 0.14), (0.62, 0.34)]), fill=color)
    draw.ellipse(_p(x, y, s, [(0.28, 0.3), (0.72, 0.94)]), fill=color)
    draw.line(_p(x, y, s, [(0.5, 0.34), (0.5, 0.92)]), fill=knockout, width=_w(s, 0.05))


def _bird(draw, x, y, s, color, knockout):
    draw.ellipse(_p(x, y, s, [(0.14, 0.36), (0.74, 0.8)]), fill=color)
    draw.ellipse(_p(x, y, s, [(0.56, 0.18), (0.86, 0.48)]), fill=color)
    draw.polygon(_p(x, y, s, [(0.84, 0.3), (0.99, 0.36), (0.84, 0.4)]), fill=color)
    draw.polygon(_p(x, y, s, [(0.18, 0.5), (0.0, 0.34), (0.04, 0.62)]), fill=color)
    draw.ellipse(_p(x, y, s, [(0.7, 0.27), (0.76, 0.33)]), fill=knockout)
    draw.line(_p(x, y, s, [(0.42, 0.8), (0.38, 0.96)]), fill=color, width=_w(s, 0.06))
    draw.line(_p(x, y, s, [(0.54, 0.8), (0.56, 0.96)]), fill=color, width=_w(s, 0.06))


def _mushroom(draw, x, y, s, color, knockout):
    draw.pieslice(_p(x, y, s, [(0.04, 0.1), (0.96, 0.9)]), 180, 360, fill=color)
    draw.rounded_rectangle(_p(x, y, s, [(0.36, 0.5), (0.64, 0.96)]), radius=max(1, s * 0.08), fill=color)
    for cx, cy in ((0.3, 0.32), (0.56, 0.22), (0.72, 0.38)):
        draw.ellipse(_p(x, y, s, [(cx - 0.06, cy - 0.06), (cx + 0.06, cy + 0.06)]), fill=knockout)


def _spider(draw, x, y, s, color, knockout):
    width = _w(s, 0.06)
    for side in (1, -1):
        for start, bend, end in (((0.5, 0.5), (0.78, 0.22), (0.94, 0.3)), ((0.5, 0.54), (0.84, 0.44), (0.98, 0.56)),
                                 ((0.5, 0.58), (0.82, 0.68), (0.94, 0.86)), ((0.5, 0.62), (0.7, 0.84), (0.74, 0.98))):
            points = [(0.5 + side * (px - 0.5), py) for px, py in (start, bend, end)]
            draw.line(_p(x, y, s, points), fill=color, width=width)
    draw.ellipse(_p(x, y, s, [(0.38, 0.3), (0.62, 0.54)]), fill=color)
    draw.ellipse(_p(x, y, s, [(0.32, 0.48), (0.68, 0.88)]), fill=color)


def _paw(draw, x, y, s, color, knockout):
    draw.ellipse(_p(x, y, s, [(0.26, 0.48), (0.74, 0.92)]), fill=color)
    for cx, cy in ((0.18, 0.38), (0.38, 0.18), (0.62, 0.18), (0.82, 0.38)):
        draw.ellipse(_p(x, y, s, [(cx - 0.1, cy - 0.12), (cx + 0.1, cy + 0.12)]), fill=color)


def _lizard(draw, x, y, s, color, knockout):
    width = _w(s, 0.14)
    draw.line(_p(x, y, s, [(0.82, 0.12), (0.62, 0.3), (0.44, 0.52), (0.4, 0.72), (0.18, 0.94)]), fill=color,
              width=width, joint="curve")
    draw.ellipse(_p(x, y, s, [(0.72, 0.02), (0.96, 0.22)]), fill=color)
    feet = _w(s, 0.07)
    for start, end in (((0.62, 0.3), (0.86, 0.38)), ((0.62, 0.3), (0.5, 0.1)), ((0.44, 0.56), (0.66, 0.66)),
                       ((0.44, 0.56), (0.2, 0.46))):
        draw.line(_p(x, y, s, [start, end]), fill=color, width=feet)


def _fish(draw, x, y, s, color, knockout):
    draw.ellipse(_p(x, y, s, [(0.08, 0.26), (0.74, 0.74)]), fill=color)
    draw.polygon(_p(x, y, s, [(0.66, 0.5), (0.98, 0.24), (0.98, 0.76)]), fill=color)
    draw.ellipse(_p(x, y, s, [(0.2, 0.42), (0.28, 0.5)]), fill=knockout)


def _frog(draw, x, y, s, color, knockout):
    draw.ellipse(_p(x, y, s, [(0.12, 0.36), (0.88, 0.9)]), fill=color)
    for cx in (0.3, 0.7):
        draw.ellipse(_p(x, y, s, [(cx - 0.14, 0.14), (cx + 0.14, 0.42)]), fill=color)
        draw.ellipse(_p(x, y, s, [(cx - 0.06, 0.22), (cx + 0.06, 0.34)]), fill=knockout)
    draw.arc(_p(x, y, s, [(0.3, 0.46), (0.7, 0.72)]), 20, 160, fill=knockout, width=_w(s, 0.06))


def _snail(draw, x, y, s, color, knockout):
    draw.rounded_rectangle(_p(x, y, s, [(0.04, 0.72), (0.96, 0.9)]), radius=max(1, s * 0.08), fill=color)
    draw.ellipse(_p(x, y, s, [(0.22, 0.2), (0.82, 0.8)]), fill=color)
    draw.arc(_p(x, y, s, [(0.36, 0.34), (0.68, 0.66)]), 0, 300, fill=knockout, width=_w(s, 0.07))
    draw.line(_p(x, y, s, [(0.12, 0.74), (0.06, 0.5)]), fill=color, width=_w(s, 0.06))


ICONS = {
    "ok": _check, "alert": _alert, "info": _info, "fire": _flame, "wind": _wind, "heat": _heat,
    "flood": _flood, "snow": _snow, "haze": _haze, "storm": _storm, "quake": _quake,
    "volcano": _volcano, "landslide": _landslide, "home": _home, "clock": _clock,
    "calendar": _calendar, "up": _arrow("up"), "down": _arrow("down"), "flat": _arrow("flat"),
    "paper": _paper, "gift": _gift, "trophy": _trophy, "rocket": _rocket, "pulse": _pulse,
    "outage": _cross, "maintenance": _wrench, "leaf": _leaf, "bug": _bug, "bird": _bird,
    "mushroom": _mushroom, "spider": _spider, "paw": _paw, "lizard": _lizard, "fish": _fish, "frog": _frog,
    "snail": _snail, "comment": _comment, "heart": _heart, "star": _star, "download": _download,
}


def draw_icon(draw, kind, xy, size, color, knockout=(255, 255, 255)):
    """Draw icon ``kind`` (unknown kinds fall back to 'alert') in the square at ``xy``."""
    x, y = xy
    ICONS.get(kind, _alert)(draw, float(x), float(y), float(size), color, knockout)
