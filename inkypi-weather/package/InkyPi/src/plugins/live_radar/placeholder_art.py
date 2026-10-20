"""Playful LiveRadar placeholder art.

Everything here is drawn without anti-aliasing in the theme's panel/ink colours
plus the six colours the Spectra 6 panel shows solidly, so the art stays crisp
after the display's dithering: TV colour bars for a missing snapshot, a
blue-screen TV waiting in an empty slot, and small offline doodles that stay
fixed per streamer.
"""

from __future__ import annotations

import hashlib
import math

from PIL import Image, ImageDraw

BLACK = (0, 0, 0)
WHITE = (255, 255, 255)
YELLOW = (255, 255, 0)
RED = (255, 0, 0)
BLUE = (0, 0, 255)
GREEN = (0, 255, 0)
COLOUR_BARS = (WHITE, YELLOW, GREEN, RED, BLUE, BLACK)
WAITING_CAPTION = "虚位以待"
OFFLINE_DOODLE_COUNT = 5


def draw_colour_bars(draw, box, *, large):
    """Fill ``box`` (inclusive) with TV colour bars over a black band.

    Returns the plate box reserved for the platform logo and the band box, so the
    caller can add the logo and, on large cards, a caption.
    """
    left, top, right, bottom = [int(value) for value in box]
    width, height = right - left + 1, bottom - top + 1
    band = max(4, round(height * 0.24))
    bars_bottom = bottom - band
    for index, colour in enumerate(COLOUR_BARS):
        bar_left = left + round(index * width / len(COLOUR_BARS))
        bar_right = left + round((index + 1) * width / len(COLOUR_BARS)) - 1
        draw.rectangle((bar_left, top, bar_right, bars_bottom), fill=colour)
    draw.rectangle((left, bars_bottom + 1, right, bottom), fill=BLACK)
    if not large and band >= 6:
        step = max(4, width // 12)
        for index, block_left in enumerate(range(left + step // 2, right - step, step * 2)):
            draw.rectangle(
                (block_left, bars_bottom + 3, block_left + step - 1, bottom - 2),
                fill=WHITE if index % 2 == 0 else BLUE,
            )
    plate = max(10, min(round((bars_bottom - top + 1) * 0.62), 34))
    plate_left = left + (width - plate) // 2
    plate_top = top + (bars_bottom - top + 1 - plate) // 2
    plate_box = (plate_left, plate_top, plate_left + plate, plate_top + plate)
    draw.rounded_rectangle(
        plate_box,
        radius=max(3, plate // 4),
        fill=WHITE,
        outline=BLACK,
        width=2 if plate >= 20 else 1,
    )
    return {"plate": plate_box, "band": (left, bars_bottom + 1, right, bottom)}


def draw_waiting_tv(draw, box, theme, font, *, fill=True):
    """An empty slot: a blue-screen TV with sparkles waiting for the next stream."""
    left, top, right, bottom = [int(value) for value in box]
    width, height = right - left + 1, bottom - top + 1
    if fill:
        draw.rectangle((left, top, right, bottom), fill=theme["panel"])
    caption_w = draw.textlength(WAITING_CAPTION, font=font)
    caption_h = font.size
    beside = width >= height * 2.6
    tv_h = min(round(height * (0.72 if beside else 0.62)), 40)
    tv_w = round(tv_h * 1.25)
    if beside:
        tv_x = left + round((width - tv_w - 8 - caption_w) / 2)
        tv_y = top + (height - tv_h) // 2
        caption_xy = (tv_x + tv_w + 8, top + (height - caption_h) / 2 - 2)
    else:
        tv_x = left + (width - tv_w) // 2
        tv_y = top + max(2, (height - tv_h - caption_h - 3) // 2)
        caption_xy = (left + (width - caption_w) / 2, tv_y + tv_h + 1)
    _draw_tv(draw, tv_x, tv_y, tv_w, tv_h, theme, screen=BLUE)
    spark = max(3, tv_h // 7)
    _draw_sparkle(draw, tv_x - spark - 3, tv_y + tv_h // 3, spark, YELLOW, theme["ink"])
    _draw_sparkle(draw, tv_x + tv_w + spark + (-1 if beside else 2), tv_y + spark + 1, max(2, spark - 1), RED, None)
    draw.ellipse((tv_x - 2, tv_y + tv_h - 4, tv_x + 1, tv_y + tv_h - 1), fill=GREEN)
    draw.text(caption_xy, WAITING_CAPTION, fill=theme["ink"], font=font)


def doodle_kind(card):
    """A stable doodle per streamer, so a card keeps its doodle across renders."""
    key = f"{card.get('platform')}|{card.get('id')}".encode("utf-8")
    return int.from_bytes(hashlib.sha256(key).digest()[:4], "big") % OFFLINE_DOODLE_COUNT


def render_offline_doodle(size, theme, kind):
    """Return a transparent RGBA doodle of ``size``: anything outside it is clipped."""
    width, height = max(1, int(size[0])), max(1, int(size[1]))
    layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    (_sleeping_tv, _moon_and_stars, _flatline, _coffee_break, _empty_radar)[kind % OFFLINE_DOODLE_COUNT](
        draw, width, height, theme,
    )
    return layer


def _sleeping_tv(draw, width, height, theme):
    tv_w = round(height * 1.2)
    zzz = max(4, height // 4)
    tv_x = (width - tv_w - 3 - 3 * zzz) // 2
    _draw_tv(draw, tv_x, 0, tv_w, height, theme, screen=BLACK, eyes_closed=True)
    for index, scale in enumerate((1.0, 0.8, 0.6)):
        side = max(3, round(zzz * scale))
        x = tv_x + tv_w + 3 + index * round(zzz * 0.9)
        y = height // 2 - index * round(zzz * 0.55)
        draw.line([(x, y), (x + side, y), (x, y + side), (x + side, y + side)], fill=BLUE, width=1)


def _moon_and_stars(draw, width, height, theme):
    ink, panel = theme["ink"], theme["panel"]
    radius = height // 2 - 1
    middle = height // 2
    moon_x = (width - 70) // 2 + radius + 12
    draw.ellipse((moon_x - radius, middle - radius, moon_x + radius, middle + radius), fill=YELLOW, outline=ink)
    bite = radius // 2 + 2
    draw.ellipse(
        (moon_x - radius + bite, middle - radius - 2, moon_x + radius + bite, middle + radius - 2), fill=panel,
    )
    draw.arc((moon_x - radius, middle - radius, moon_x + radius, middle + radius), 40, 320, fill=ink)
    for x, y, star in ((moon_x + radius + 8, 4, 3), (moon_x + radius + 20, middle + 2, 4), (moon_x - radius - 10, 5, 3)):
        _draw_sparkle(draw, x, y, star, YELLOW, ink)
    cloud = moon_x + radius + 30
    base = height - 3
    for dx, puff in ((0, 4), (6, 6), (13, 4)):
        draw.ellipse((cloud + dx - puff, base - 2 * puff, cloud + dx + puff, base), fill=panel, outline=ink)
    draw.rectangle((cloud - 3, base - 5, cloud + 16, base - 1), fill=panel)
    draw.line((cloud - 4, base, cloud + 17, base), fill=ink)


def _flatline(draw, width, height, theme):
    span = min(width - 8, 130)
    left = (width - span) // 2
    right = left + span - 16
    middle = height // 2
    centre = (left + right) // 2
    trace = GREEN if theme.get("mode") == "dark" else theme["ink"]
    draw.line(
        [
            (left, middle), (centre - 8, middle), (centre - 4, middle - height // 3),
            (centre, middle + height // 4), (centre + 4, middle), (right, middle),
        ],
        fill=trace,
        width=2,
    )
    heart_x, lobe = right + 9, max(3, height // 5)
    draw.ellipse((heart_x - 2 * lobe, middle - lobe - 1, heart_x, middle + 1), fill=RED)
    draw.ellipse((heart_x, middle - lobe - 1, heart_x + 2 * lobe, middle + 1), fill=RED)
    draw.polygon([(heart_x - 2 * lobe, middle - 1), (heart_x + 2 * lobe, middle - 1), (heart_x, middle + 2 * lobe)], fill=RED)


def _coffee_break(draw, width, height, theme):
    ink = theme["ink"]
    mug_w, mug_h = round(height * 0.8), round(height * 0.62)
    mug_x = (width - mug_w - 26) // 2
    mug_top = height - 1 - mug_h
    draw.rounded_rectangle((mug_x, mug_top, mug_x + mug_w, height - 1), radius=3, fill=RED, outline=ink)
    draw.arc((mug_x + mug_w - 4, mug_top + 3, mug_x + mug_w + 7, height - 5), 270, 90, fill=ink, width=2)
    for steam_x in (mug_x + mug_w // 3, mug_x + 2 * mug_w // 3):
        draw.line(
            [(steam_x, mug_top - 2), (steam_x - 2, mug_top - 5), (steam_x, mug_top - 8), (steam_x - 2, mug_top - 11)],
            fill=ink,
            width=1,
        )
    cookie = mug_x + mug_w + 16
    draw.ellipse((cookie - 5, height - 11, cookie + 5, height - 1), fill=YELLOW, outline=ink)
    for dx, dy in ((-2, -7), (2, -5), (-1, -4)):
        draw.point((cookie + dx, height - 1 + dy), fill=ink)


def _empty_radar(draw, width, height, theme):
    ink = theme["ink"]
    radius = height // 2 - 1
    centre_x, middle = width // 2 - 6, height // 2
    for ring in (radius, radius * 2 // 3, radius // 3):
        draw.ellipse((centre_x - ring, middle - ring, centre_x + ring, middle + ring), outline=ink)
    draw.pieslice((centre_x - radius, middle - radius, centre_x + radius, middle + radius), 300, 340, fill=GREEN)
    angle = math.radians(340)
    draw.line(
        (centre_x, middle, centre_x + round(radius * math.cos(angle)), middle + round(radius * math.sin(angle))),
        fill=ink,
    )
    _draw_sparkle(draw, centre_x + radius + 10, 5, 3, YELLOW, ink)


def _draw_sparkle(draw, cx, cy, radius, colour, outline):
    points = []
    for index in range(8):
        reach = radius if index % 2 == 0 else max(1, radius // 3)
        angle = -math.pi / 2 + index * math.pi / 4
        points.append((cx + math.cos(angle) * reach, cy + math.sin(angle) * reach))
    draw.polygon(points, fill=colour, outline=outline)


def _draw_tv(draw, x, y, width, height, theme, *, screen, eyes_closed=False):
    """A rounded TV mascot filling (x, y, width, height), antennas and feet included."""
    ink, panel = theme["ink"], theme["panel"]
    antenna = max(3, round(height * 0.22))
    feet = max(2, round(height * 0.1))
    stroke = 2 if height >= 22 else 1
    body = (x, y + antenna, x + width - 1, y + height - 1 - feet)
    centre = x + width // 2
    tip = max(2, round(height * 0.07))
    for side in (-1, 1):
        tip_x = centre + side * round(width * 0.22)
        draw.line((centre, body[1], tip_x, y + tip), fill=ink, width=1)
        draw.ellipse((tip_x - tip, y, tip_x + tip, y + 2 * tip), fill=YELLOW, outline=ink)
    radius = max(3, round(height * 0.16))
    draw.rounded_rectangle(body, radius=radius, fill=panel, outline=ink, width=stroke)
    inset = max(2, round(height * 0.11))
    screen_box = (body[0] + inset, body[1] + inset, body[2] - inset - max(2, round(width * 0.12)), body[3] - inset)
    draw.rounded_rectangle(screen_box, radius=max(2, radius - 2), fill=screen, outline=ink)
    knob_x = (screen_box[2] + body[2]) // 2
    knob = max(1, round(height * 0.045))
    for knob_y in (body[1] + (body[3] - body[1]) // 3, body[1] + 2 * (body[3] - body[1]) // 3):
        draw.ellipse((knob_x - knob, knob_y - knob, knob_x + knob, knob_y + knob), fill=ink)
    face = WHITE if screen in (BLUE, BLACK) else ink
    screen_w, screen_h = screen_box[2] - screen_box[0], screen_box[3] - screen_box[1]
    eye_y = screen_box[1] + round(screen_h * 0.4)
    eye = max(1, round(screen_h * 0.12))
    for eye_x in (screen_box[0] + round(screen_w * 0.32), screen_box[0] + round(screen_w * 0.68)):
        if eyes_closed:
            draw.line((eye_x - eye - 1, eye_y, eye_x + eye + 1, eye_y), fill=face, width=max(1, eye // 2 + 1))
        else:
            draw.ellipse((eye_x - eye, eye_y - eye, eye_x + eye, eye_y + eye), fill=face)
    mouth_y = screen_box[1] + round(screen_h * 0.66)
    mouth = max(3, round(screen_w * 0.22))
    mouth_x = screen_box[0] + screen_w // 2
    draw.arc((mouth_x - mouth, mouth_y - mouth // 2, mouth_x + mouth, mouth_y + mouth // 2), 20, 160, fill=face, width=1)
    for foot_x in (body[0] + round(width * 0.22), body[2] - round(width * 0.22)):
        draw.line((foot_x, body[3], foot_x, body[3] + feet), fill=ink, width=stroke)
