"""Printed-comic touches for LiveRadar's pages.

Kept quiet on purpose: a halftone band behind the LIVE NOW cards in their
platform colours, hard shadows under cards and walls, tabs for the section
titles, ticket-stub counters, one speech bubble and a highlighter stroke under
the footer. By day the page is cream paper and the lines are ink; by night the
same pieces sit on the dark page in its own ink, with shadows dimmed to glow.
"""

from __future__ import annotations

import math

from PIL import Image, ImageDraw

PAPER = (254, 252, 246)
INK = (0, 0, 0)
WHITE = (255, 255, 255)
SHADOW_OFFSET = 3
HALFTONE_PITCH = 7
HALFTONE_MAX_RADIUS = 2.1
# How far each halftone dot is pulled toward the page: the band stays a light tint.
HALFTONE_PAPER_SHARE = 0.5
HIGHLIGHTER = (255, 214, 0)
# By night shadows and the highlighter are these shares of the way back to the page.
NIGHT_SHADOW_PAGE_SHARE = 0.55
NIGHT_CARD_SHADOW_PAGE_SHARE = 0.35
NIGHT_HIGHLIGHTER_PAGE_SHARE = 0.55
SECTION_COUNT_FILLS = {
    "LIVE NOW": (255, 119, 0),
    "LIVE TOO": (255, 119, 0),
    "OFFLINE": (0, 174, 236),
    "REPLAY": (145, 70, 255),
}
BUBBLE_TEXT = "WATCH MORE TOGETHER!"


def mix(colour, other, share):
    """``colour`` moved ``share`` of the way toward ``other``."""
    return tuple(round(a + (b - a) * share) for a, b in zip(colour, other))


def roles(theme):
    """The page's comic colours: line and tab ink, what sits on a tab, shadow, bubble, marker."""
    if theme.get("mode") == "dark":
        page = theme["bg"]
        return {
            "ink": theme["ink"],
            "on_ink": page,
            "shadow": mix(theme["muted"], page, NIGHT_SHADOW_PAGE_SHARE),
            "bubble": theme["panel"],
            "marker": mix(HIGHLIGHTER, page, NIGHT_HIGHLIGHTER_PAGE_SHARE),
        }
    return {"ink": INK, "on_ink": WHITE, "shadow": INK, "bubble": WHITE, "marker": HIGHLIGHTER}


def card_shadow(theme, brand):
    """A LIVE NOW card's shadow: ink by day, its platform colour dimmed into the night page."""
    if theme.get("mode") == "dark":
        return mix(brand, theme["bg"], NIGHT_CARD_SHADOW_PAGE_SHARE)
    return INK


def colour_along(stops, x):
    """The colour at ``x`` between (x, colour) stops, flat beyond the first and last."""
    stops = sorted(stops)
    if x <= stops[0][0]:
        return stops[0][1]
    for (x0, c0), (x1, c1) in zip(stops, stops[1:]):
        if x <= x1:
            return mix(c0, c1, (x - x0) / max(1, x1 - x0))
    return stops[-1][1]


def halftone_band(image, box, stops, paper=PAPER):
    """Dots across ``box`` taking the stops' colours, largest along the band's middle."""
    left, top, right, bottom = [int(value) for value in box]
    if not stops or right - left < HALFTONE_PITCH or bottom - top < HALFTONE_PITCH:
        return
    draw = ImageDraw.Draw(image)
    height = bottom - top
    for row, y in enumerate(range(top + HALFTONE_PITCH // 2, bottom, HALFTONE_PITCH)):
        radius = HALFTONE_MAX_RADIUS * math.sin(math.pi * (y - top) / height)
        if radius < 0.6:
            continue
        shift = HALFTONE_PITCH // 2 if row % 2 else 0
        for x in range(left + shift, right, HALFTONE_PITCH):
            colour = mix(colour_along(stops, x), paper, HALFTONE_PAPER_SHARE)
            draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=colour)


def ink_shadow(image, box, radius, colour=INK, offset=SHADOW_OFFSET):
    """A hard offset shadow under the rounded ``box``, painted only where the box is not."""
    left, top, right, bottom = [int(value) for value in box]
    width, height = right - left + 1 + offset, bottom - top + 1 + offset
    if width <= offset or height <= offset:
        return
    mask = Image.new("L", (width, height), 0)
    shape = ImageDraw.Draw(mask)
    shape.rounded_rectangle((offset, offset, width - 1, height - 1), radius=radius, fill=255)
    shape.rounded_rectangle((0, 0, width - 1 - offset, height - 1 - offset), radius=radius, fill=0)
    image.paste(Image.new("RGB", mask.size, colour), (left, top), mask)


def ink_tab(draw, box, fill=INK, radius=5):
    draw.rounded_rectangle(box, radius=radius, fill=fill)


def count_badge(draw, centre, text, font, fill, outline=INK):
    """The section count in a coloured circle with a rim; the number stays black on the colour."""
    cx, cy = centre
    text_w = draw.textlength(text, font=font)
    radius = max(10, int(text_w / 2) + 5)
    draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=fill, outline=outline, width=2)
    bbox = draw.textbbox((0, 0), text, font=font)
    draw.text((cx - (bbox[0] + bbox[2]) / 2, cy - (bbox[1] + bbox[3]) / 2), text, fill=INK, font=font)
    return cx + radius


def speech_bubble(draw, right, top, text, font, tail_x, ink=INK, fill=WHITE, shadow=INK):
    """A small speech bubble ending at ``right``, its tail pointing up at ``tail_x``."""
    bbox = draw.textbbox((0, 0), text, font=font)
    text_w, text_h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    left = right - text_w - 16
    bottom = top + text_h + 9
    tail_x = min(max(tail_x, left + 12), right - 12)
    draw.rounded_rectangle((left + 2, top + 2, right + 2, bottom + 2), radius=8, fill=shadow)
    draw.rounded_rectangle((left, top, right, bottom), radius=8, fill=fill, outline=ink, width=2)
    draw.polygon(((tail_x - 6, top + 1), (tail_x + 4, top + 1), (tail_x + 3, top - 7)), fill=fill)
    draw.line(((tail_x - 6, top + 1), (tail_x + 3, top - 7), (tail_x + 4, top + 1)), fill=ink, width=2)
    draw.text((left + 8 - bbox[0], top + 4 - bbox[1]), text, fill=ink, font=font)
    return left, top, right, bottom


def highlighter(draw, left, right, baseline, colour=HIGHLIGHTER, height=6):
    """A highlighter stroke under the lower half of a line of text."""
    draw.polygon(((left - 3, baseline - height + 1), (right + 3, baseline - height),
                  (right + 2, baseline + 1), (left - 2, baseline + 2)), fill=colour)
