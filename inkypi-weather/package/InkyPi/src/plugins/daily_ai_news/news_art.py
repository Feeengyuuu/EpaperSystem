"""Procedural spot art for the 整点新闻 page.

Flat shapes without anti-aliasing in the six colours the Spectra 6 panel shows
solidly (plus the theme's ink and panel), so the art stays crisp after dithering:
a topic medallion beside the lead story and a small globe for spare column space.
"""

from __future__ import annotations

import math

BLACK = (0, 0, 0)
WHITE = (255, 255, 255)
YELLOW = (255, 255, 0)
RED = (255, 0, 0)
BLUE = (0, 0, 255)
GREEN = (0, 255, 0)

# Keyword groups, checked in order against the lead's tag and title.
TOPIC_KEYWORDS = (
    ("health", ("公共卫生", "卫生", "疫情", "病毒", "埃博拉", "医疗", "医院", "健康", "疾病", "疫苗", "流感")),
    ("conflict", ("冲突", "军事", "战争", "袭击", "空袭", "导弹", "武装", "战机", "军队", "停火", "以军", "胡塞")),
    ("aviation", ("航空", "飞机", "客机", "机场", "波音", "空客", "航班")),
    ("tech", ("科技", "人工智能", "芯片", "航天", "卫星", "火箭", "互联网", "数字", "半导体", "AI")),
    ("climate", ("气候", "环境", "灾害", "地震", "台风", "洪水", "暴雨", "天气", "火灾", "干旱", "高温")),
    ("justice", ("司法", "法院", "法律", "判决", "审判", "执行", "警方", "逮捕", "检方", "起诉")),
    ("sports", ("体育", "比赛", "奥运", "足球", "篮球", "冠军", "联赛")),
    ("economy", ("经济", "金融", "财经", "市场", "股市", "贸易", "产业", "投资", "基建", "铁路", "人民币",
                 "央行", "关税", "就业", "消费", "制造")),
    ("politics", ("外交", "选举", "公投", "政治", "两岸", "会谈", "峰会", "政府", "议会", "国台办", "总统", "首相")),
)
TOPIC_COLOURS = {
    "health": RED,
    "conflict": BLACK,
    "aviation": BLUE,
    "tech": BLUE,
    "climate": GREEN,
    "justice": BLACK,
    "sports": GREEN,
    "economy": GREEN,
    "politics": RED,
    "news": BLACK,
}


def topic_kind(*texts):
    """The illustration topic for a story, from its tag first and then its title."""
    for text in texts:
        value = str(text or "")
        for kind, words in TOPIC_KEYWORDS:
            if any(word in value for word in words):
                return kind
    return "news"


def draw_topic_art(draw, box, kind, ink, panel, rule):
    """A topic medallion with a ring, an accent bubble and a dot grid inside ``box``."""
    left, top, right, bottom = [int(value) for value in box]
    height = bottom - top
    radius = max(12, min(height // 2 - 6, (right - left) // 4))
    cx, cy = right - radius - 10, (top + bottom) // 2
    for row in range(3):
        for column in range(6):
            x = left + 6 + column * 9
            y = cy - 9 + row * 9
            if x < cx - radius - 14:
                draw.ellipse((x - 1, y - 1, x + 1, y + 1), fill=rule)
    ring = radius + 7
    draw.arc((cx - ring, cy - ring, cx + ring, cy + ring), 150, 330, fill=ink, width=2)
    colour = TOPIC_COLOURS.get(kind, BLACK)
    draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=colour, outline=ink, width=2)
    glyph = WHITE if colour in (BLACK, RED, BLUE) else BLACK
    GLYPHS.get(kind, _newspaper)(draw, cx, cy, radius, glyph, colour)
    bubble = max(5, radius // 3)
    bx, by = cx - radius + 2, cy - radius + 4
    draw.ellipse((bx - bubble, by - bubble, bx + bubble, by + bubble), fill=YELLOW, outline=ink, width=1)
    _sparkle(draw, cx + radius + 4, cy + radius - 2, max(3, radius // 6), ink)


def draw_globe(draw, cx, cy, radius, ink, panel, pins=(RED, BLUE, YELLOW)):
    """A small wireframe globe with location pins and an orbiting satellite."""
    orbit = (cx - radius - 12, cy - radius // 3 - 2, cx + radius + 12, cy + radius // 3 + 2)
    draw.arc(orbit, 180, 360, fill=ink, width=1)
    draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=panel, outline=ink, width=2)
    for ratio in (0.38, 0.72):
        half = round(radius * ratio)
        draw.ellipse((cx - half, cy - radius, cx + half, cy + radius), outline=ink, width=1)
    for ratio in (-0.5, 0.0, 0.5):
        y = cy + round(radius * ratio)
        half = round(math.sqrt(max(0.0, radius * radius - (y - cy) ** 2)))
        draw.line((cx - half, y, cx + half, y), fill=ink, width=1)
    for (dx, dy), colour in zip(((-0.42, -0.35), (0.34, 0.1), (-0.1, 0.48)), pins):
        px, py = cx + round(radius * dx), cy + round(radius * dy)
        draw.ellipse((px - 4, py - 4, px + 4, py + 4), fill=colour, outline=ink, width=1)
    draw.arc(orbit, 0, 180, fill=ink, width=1)
    sx, sy = orbit[2] - 2, cy
    draw.rectangle((sx - 3, sy - 2, sx + 3, sy + 2), fill=YELLOW, outline=ink)


def _sparkle(draw, cx, cy, radius, ink):
    points = []
    for index in range(8):
        reach = radius if index % 2 == 0 else max(1, radius // 3)
        angle = -math.pi / 2 + index * math.pi / 4
        points.append((cx + math.cos(angle) * reach, cy + math.sin(angle) * reach))
    draw.polygon(points, fill=YELLOW, outline=ink)


def _cross(draw, cx, cy, r, glyph, _fill):
    arm, half = round(r * 0.55), max(3, round(r * 0.18))
    draw.rectangle((cx - half, cy - arm, cx + half, cy + arm), fill=glyph)
    draw.rectangle((cx - arm, cy - half, cx + arm, cy + half), fill=glyph)


def _shield(draw, cx, cy, r, glyph, fill):
    w, h = round(r * 0.55), round(r * 0.62)
    points = [(cx - w, cy - h), (cx + w, cy - h), (cx + w, cy), (cx, cy + h), (cx - w, cy)]
    draw.polygon(points, fill=glyph)
    bar = max(2, round(r * 0.1))
    draw.rectangle((cx - bar, cy - round(h * 0.6), cx + bar, cy + round(h * 0.1)), fill=fill)
    draw.rectangle((cx - bar, cy + round(h * 0.25), cx + bar, cy + round(h * 0.25) + 2 * bar), fill=fill)


def _plane(draw, cx, cy, r, glyph, _fill):
    s = r * 0.62
    body = [(cx - s, cy + s * 0.15), (cx + s, cy - s * 0.15), (cx + s, cy + s * 0.05), (cx - s, cy + s * 0.3)]
    draw.polygon(body, fill=glyph)
    draw.polygon([(cx - s * 0.1, cy + s * 0.05), (cx + s * 0.25, cy - s * 0.75), (cx + s * 0.42, cy - s * 0.75),
                  (cx + s * 0.25, cy + 0.0)], fill=glyph)
    draw.polygon([(cx - s * 0.1, cy + s * 0.2), (cx + s * 0.15, cy + s * 0.8), (cx + s * 0.32, cy + s * 0.8),
                  (cx + s * 0.22, cy + s * 0.12)], fill=glyph)
    draw.polygon([(cx - s, cy + s * 0.15), (cx - s * 0.85, cy - s * 0.35), (cx - s * 0.7, cy - s * 0.35),
                  (cx - s * 0.68, cy + s * 0.1)], fill=glyph)


def _chip(draw, cx, cy, r, glyph, fill):
    half = round(r * 0.36)
    draw.rectangle((cx - half, cy - half, cx + half, cy + half), fill=glyph)
    inner = max(2, half // 2)
    draw.rectangle((cx - inner, cy - inner, cx + inner, cy + inner), fill=fill)
    pin = round(r * 0.58)
    for offset in (-half + 2, 0, half - 2):
        draw.line((cx + offset, cy - pin, cx + offset, cy - half), fill=glyph, width=2)
        draw.line((cx + offset, cy + half, cx + offset, cy + pin), fill=glyph, width=2)
        draw.line((cx - pin, cy + offset, cx - half, cy + offset), fill=glyph, width=2)
        draw.line((cx + half, cy + offset, cx + pin, cy + offset), fill=glyph, width=2)


def _bars(draw, cx, cy, r, glyph, _fill):
    base = cy + round(r * 0.5)
    width = max(3, round(r * 0.2))
    for index, height in enumerate((0.35, 0.6, 0.9)):
        x = cx - round(r * 0.5) + index * (width + max(2, round(r * 0.1)))
        draw.rectangle((x, base - round(r * height), x + width, base), fill=glyph)
    tip = (cx + round(r * 0.55), cy - round(r * 0.55))
    draw.line((cx - round(r * 0.55), cy + round(r * 0.05), tip[0], tip[1]), fill=glyph, width=2)
    draw.polygon([tip, (tip[0] - 8, tip[1] + 1), (tip[0] - 1, tip[1] + 8)], fill=glyph)


def _columns(draw, cx, cy, r, glyph, _fill):
    w = round(r * 0.6)
    roof = cy - round(r * 0.3)
    draw.polygon([(cx - w - 2, roof), (cx + w + 2, roof), (cx, cy - round(r * 0.68))], fill=glyph)
    base = cy + round(r * 0.48)
    draw.rectangle((cx - w - 2, base, cx + w + 2, base + max(2, round(r * 0.12))), fill=glyph)
    column = max(2, round(r * 0.12))
    for offset in (-0.45, -0.15, 0.15, 0.45):
        x = cx + round(w * offset * 2)
        draw.rectangle((x - column // 2 - 1, roof + 3, x + column // 2 + 1, base - 2), fill=glyph)


def _storm(draw, cx, cy, r, glyph, fill):
    puff = round(r * 0.3)
    for dx, dy, scale in ((-0.3, -0.05, 1.0), (0.05, -0.25, 1.25), (0.38, -0.02, 0.95)):
        pr = round(puff * scale)
        px, py = cx + round(r * dx), cy + round(r * dy)
        draw.ellipse((px - pr, py - pr, px + pr, py + pr), fill=glyph)
    draw.rectangle((cx - round(r * 0.55), cy - 2, cx + round(r * 0.6), cy + round(r * 0.18)), fill=glyph)
    bolt = [(cx + 2, cy + 2), (cx - round(r * 0.2), cy + round(r * 0.45)), (cx - 1, cy + round(r * 0.42)),
            (cx - round(r * 0.08), cy + round(r * 0.78)), (cx + round(r * 0.25), cy + round(r * 0.3)),
            (cx + 2, cy + round(r * 0.33))]
    draw.polygon(bolt, fill=YELLOW if fill != YELLOW else BLACK)


def _scales(draw, cx, cy, r, glyph, _fill):
    top = cy - round(r * 0.55)
    draw.line((cx, top, cx, cy + round(r * 0.5)), fill=glyph, width=3)
    draw.line((cx - round(r * 0.6), top + 4, cx + round(r * 0.6), top + 4), fill=glyph, width=2)
    for side in (-1, 1):
        px = cx + side * round(r * 0.5)
        draw.line((px, top + 4, px - 6, cy + 4), fill=glyph, width=1)
        draw.line((px, top + 4, px + 6, cy + 4), fill=glyph, width=1)
        draw.chord((px - 8, cy - 4, px + 8, cy + 10), 0, 180, fill=glyph)
    draw.rectangle((cx - round(r * 0.3), cy + round(r * 0.5), cx + round(r * 0.3), cy + round(r * 0.6)), fill=glyph)


def _ball(draw, cx, cy, r, glyph, fill):
    ball = round(r * 0.6)
    draw.ellipse((cx - ball, cy - ball, cx + ball, cy + ball), fill=glyph)
    patch = round(ball * 0.38)
    draw.regular_polygon((cx, cy, patch), 5, fill=fill)
    for index in range(5):
        angle = -math.pi / 2 + index * 2 * math.pi / 5
        x, y = cx + math.cos(angle) * patch, cy + math.sin(angle) * patch
        draw.line((x, y, cx + math.cos(angle) * ball, cy + math.sin(angle) * ball), fill=fill, width=1)


def _newspaper(draw, cx, cy, r, glyph, fill):
    w, h = round(r * 0.6), round(r * 0.5)
    draw.rectangle((cx - w, cy - h, cx + w, cy + h), fill=glyph)
    draw.rectangle((cx - w + 4, cy - h + 4, cx - 2, cy), fill=fill)
    for index in range(3):
        y = cy - h + 5 + index * 5
        draw.line((cx + 2, y, cx + w - 4, y), fill=fill, width=2)
    for index in range(2):
        y = cy + 4 + index * 5
        draw.line((cx - w + 4, y, cx + w - 4, y), fill=fill, width=2)


GLYPHS = {
    "health": _cross,
    "conflict": _shield,
    "aviation": _plane,
    "tech": _chip,
    "economy": _bars,
    "politics": _columns,
    "climate": _storm,
    "justice": _scales,
    "sports": _ball,
    "news": _newspaper,
}
