"""LiveRadar's stage lights: the masthead's free span hung with one projector lamp per LIVE NOW
card, each throwing a beam in its card's platform colour onto the card like a film onto a screen.

Pseudo-3D and kept quiet: shaded cans on a slim truss, each lens foreshortened toward its card
and aimed down the middle of its beam; the beam's edges run from the lens exactly to the card's
top corners, a little brighter than the light inside, and the light fades with distance, with a
faint pool where it meets the card. Light only falls on the bare page, never over the
masthead's lettering, counters or bubble. It changes with the refresh slot: the streaks and dust
in each beam shift, and the most-watched of the three cards gets the brightest lamp.
"""

from __future__ import annotations

import math
import random

from PIL import Image, ImageChops, ImageDraw, ImageFilter

SLOT_SECONDS = 600
SUPERSAMPLE = 4
# The truss hangs this far below the page top; lamps pivot this far under it.
TRUSS_Y = 10
PIVOT_DROP = 13
LAMP_SPACING = 74
CAN_LENGTH = 22.0
CAN_BACK_RADIUS = 5.5
CAN_FRONT_RADIUS = 7.5
LENS_SHARE = 0.78
# Beam opacity at the lens by page mode, and the share of it left where the beam lands.
BEAM_ALPHA = {"light": 0.34, "dark": 0.46}
BEAM_FAR_SHARE = 0.28
BEAM_SOFTNESS = 0.9
# The beam's two edges and its streaks, as shares of the lens opacity (before the falloff).
EDGE_SHARE = 1.5
STREAK_SHARE = 0.7
STREAKS_PER_BEAM = 3
# "soft" light, or "halftone": the beam printed as dots on the page's comic grid.
BEAM_STYLE = "soft"
HALFTONE_PITCH = 5
HALFTONE_MAX_RADIUS = 1.35
HALFTONE_OPACITY = 0.6
# The light: the platform colour this share of the way to white.
LIGHT_WHITE_SHARE = {"light": 0.12, "dark": 0.42}
# Lamp strength by the cards' viewer rank (most watched first).
RANK_STRENGTH = (1.0, 0.8, 0.66)
DUST_PER_BEAM = 10
# The pool of light just inside a card's top edge.
POOL_DEPTH = 18
POOL_ALPHA = {"light": 0.18, "dark": 0.24}
METAL = {"light": ((34, 36, 42), (128, 134, 146)), "dark": ((74, 78, 88), (176, 182, 194))}
TRUSS = {"light": (92, 96, 106), "dark": (120, 126, 138)}


def slot_index(when):
    """The refresh slot (10 minutes) ``when`` falls in."""
    return int(when.timestamp() // SLOT_SECONDS)


def untouched(image, bare):
    """An L mask, 255 where ``image`` still matches ``bare`` in every channel; None without a bare page."""
    if bare is None:
        return None
    red, green, blue = ImageChops.difference(image.convert("RGB"), bare.convert("RGB")).split()
    changed = ImageChops.lighter(ImageChops.lighter(red, green), blue)
    return changed.point(lambda value: 255 if value == 0 else 0)


def _mix(colour, other, share):
    return tuple(round(a + (b - a) * share) for a, b in zip(colour, other))


def _mode(theme):
    return "dark" if theme.get("mode") == "dark" else "light"


def _hull(points):
    """The points (a convex quadrilateral's corners) in order round their centre."""
    cx = sum(x for x, _y in points) / len(points)
    cy = sum(y for _x, y in points) / len(points)
    return sorted(points, key=lambda point: math.atan2(point[1] - cy, point[0] - cx))


def _ellipse(centre, axis_a, radius_a, radius_b, steps=28):
    """A polygon for the ellipse at ``centre`` with ``radius_a`` along unit ``axis_a``."""
    ax, ay = axis_a
    bx, by = -ay, ax
    return [(centre[0] + ax * radius_a * math.cos(t) + bx * radius_b * math.sin(t),
             centre[1] + ay * radius_a * math.cos(t) + by * radius_b * math.sin(t))
            for t in (2 * math.pi * step / steps for step in range(steps))]


def _unit(dx, dy):
    length = math.hypot(dx, dy) or 1.0
    return dx / length, dy / length


def plan(span, targets, theme, slot):
    """One lamp per target, spread across the masthead's free ``span`` (left, right): each lamp's
    pivot, its aim (halfway between the rays to its card's top corners), lens, landing (the
    card's top edge, corner to corner) and strength. ``targets`` are (card box, platform colour,
    heat); [] when the span is too narrow for them."""
    left, right = span
    count = len(targets)
    if not count or right - left < 40 * count:
        return []
    spacing = min(LAMP_SPACING, (right - left) / (count + 0.4))
    first = (left + right) / 2 - spacing * (count - 1) / 2
    order = sorted(range(count), key=lambda index: -int(targets[index][2] or 0))
    strengths = {index: RANK_STRENGTH[min(rank, len(RANK_STRENGTH) - 1)] for rank, index in enumerate(order)}
    mode = _mode(theme)
    lamps = []
    for index, (box, colour, _heat) in enumerate(targets):
        x0, y0, x1, _y1 = box
        pivot = (first + index * spacing, TRUSS_Y + PIVOT_DROP)
        to_left, to_right = _unit(x0 - pivot[0], y0 - pivot[1]), _unit(x1 - pivot[0], y0 - pivot[1])
        aim = _unit(to_left[0] + to_right[0], to_left[1] + to_right[1])
        lens = (pivot[0] + aim[0] * CAN_LENGTH * 0.55, pivot[1] + aim[1] * CAN_LENGTH * 0.55)
        lamps.append({
            "box": box, "pivot": pivot, "aim": aim, "lens": lens, "strength": strengths[index],
            "landing": (x0, x1, y0),
            "light": _mix(colour, (255, 255, 255), LIGHT_WHITE_SHARE[mode]),
            "seed": f"{slot}:{index}",
        })
    return lamps


def _edges(lamp):
    """The beam's two edges: each from the lens rim to the card corner on its side."""
    lens, aim = lamp["lens"], lamp["aim"]
    normal = (-aim[1], aim[0])
    radius = CAN_FRONT_RADIUS * LENS_SHARE
    rim_a = (lens[0] + normal[0] * radius, lens[1] + normal[1] * radius)
    rim_b = (lens[0] - normal[0] * radius, lens[1] - normal[1] * radius)
    left, right, y = lamp["landing"]
    corner_l, corner_r = (left, y), (right, y)
    if math.dist(rim_a, corner_l) + math.dist(rim_b, corner_r) <= math.dist(rim_a, corner_r) + math.dist(rim_b, corner_l):
        return (rim_a, corner_l), (rim_b, corner_r)
    return (rim_b, corner_l), (rim_a, corner_r)


def _beam_mask(size, lamp, mode):
    """The beam's opacity: its cone from lens to landing, brightest at the lens and fading with
    distance, with crisp brighter edges and a few streaks of light placed by the slot."""
    lens = lamp["lens"]
    edge_l, edge_r = _edges(lamp)
    cone = _hull([edge_l[0], edge_r[0], edge_l[1], edge_r[1]])
    shape = Image.new("L", size, 0)
    ImageDraw.Draw(shape).polygon(cone, fill=255)
    reach = max(math.hypot(px - lens[0], py - lens[1]) for px, py in cone) * 1.05
    far = round(255 * BEAM_FAR_SHARE)
    falloff = Image.new("L", size, far)
    ramp = Image.radial_gradient("L").resize((round(reach * 2), round(reach * 2)), Image.Resampling.BILINEAR)
    # Image.radial_gradient runs 0 at its centre to 255 at its inscribed circle.
    ramp = ramp.point(lambda value: round(255 - min(255, value) * (255 - far) / 255))
    falloff.paste(ramp, (round(lens[0] - reach), round(lens[1] - reach)))
    peak = BEAM_ALPHA[mode] * lamp["strength"]
    body = ImageChops.multiply(shape, falloff).point(lambda value: round(value * peak))
    body = body.filter(ImageFilter.GaussianBlur(BEAM_SOFTNESS))
    rays = Image.new("L", size, 0)
    draw = ImageDraw.Draw(rays)
    for start, end in (edge_l, edge_r):
        draw.line((*start, *end), fill=round(255 * min(1.0, peak * EDGE_SHARE)), width=1)
    rng = random.Random(f"streaks:{lamp['seed']}")
    left, right, y = lamp["landing"]
    for _ in range(STREAKS_PER_BEAM):
        draw.line((*lens, rng.uniform(left + 6, right - 6), y), fill=round(255 * min(1.0, peak * STREAK_SHARE)),
                  width=1)
    rays = ImageChops.multiply(rays, falloff)
    return ImageChops.lighter(body, rays)


def _dust(mask, lamp, mode):
    """A few motes floating in the beam, placed by the slot."""
    rng = random.Random(lamp["seed"])
    draw = ImageDraw.Draw(mask)
    lens = lamp["lens"]
    left, right, y = lamp["landing"]
    base = round(255 * min(1.0, BEAM_ALPHA[mode] * 2.2) * lamp["strength"])
    for _ in range(DUST_PER_BEAM):
        t = rng.uniform(0.18, 0.9)
        s = rng.uniform(-0.75, 0.75)
        cx = lens[0] + ((left + right) / 2 - lens[0]) * t
        cy = lens[1] + (y - lens[1]) * t
        x, y_mote = round(cx + s * (right - left) / 2 * t), round(cy)
        size = 1 if rng.random() < 0.7 else 2
        draw.rectangle((x, y_mote, x + size - 1, y_mote + size - 1), fill=round(base * rng.uniform(0.55, 1.0)))


def _halftone(mask, peak):
    """The beam's opacity as print dots: on a staggered grid, each dot as large as the light there."""
    box = mask.getbbox()
    if box is None:
        return mask
    s = SUPERSAMPLE
    x0, y0, x1, y1 = box
    dots = Image.new("L", ((x1 - x0) * s, (y1 - y0) * s), 0)
    draw = ImageDraw.Draw(dots)
    full = max(1, round(255 * peak))
    for row, y in enumerate(range(y0, y1, HALFTONE_PITCH)):
        for x in range(x0 + (HALFTONE_PITCH // 2 if row % 2 else 0), x1, HALFTONE_PITCH):
            radius = HALFTONE_MAX_RADIUS * math.sqrt(min(1.0, mask.getpixel((x, y)) / full))
            if radius >= 0.35:
                cx, cy = (x - x0 + 0.5) * s, (y - y0 + 0.5) * s
                draw.ellipse((cx - radius * s, cy - radius * s, cx + radius * s, cy + radius * s), fill=255)
    printed = Image.new("L", mask.size, 0)
    printed.paste(dots.resize((x1 - x0, y1 - y0), Image.Resampling.LANCZOS)
                  .point(lambda value: round(value * HALFTONE_OPACITY)), (x0, y0))
    return printed


def _truss(draw, lamps, mode):
    s = SUPERSAMPLE
    colour = TRUSS[mode]
    x0 = min(lamp["pivot"][0] for lamp in lamps) - 16
    x1 = max(lamp["pivot"][0] for lamp in lamps) + 16
    top, bottom = TRUSS_Y - 2.5, TRUSS_Y + 2.5
    for cable_x in (x0 + 6, x1 - 6):
        draw.line((cable_x * s, 0, cable_x * s, top * s), fill=colour, width=s)
    draw.line((x0 * s, top * s, x1 * s, top * s), fill=colour, width=round(1.3 * s))
    draw.line((x0 * s, bottom * s, x1 * s, bottom * s), fill=colour, width=round(1.3 * s))
    x, up = x0, True
    while x + 5 <= x1:
        draw.line((x * s, (top if up else bottom) * s, (x + 5) * s, (bottom if up else top) * s), fill=colour,
                  width=s)
        x, up = x + 5, not up


def _can(draw, lamp, mode):
    """A spotlight can on its yoke, shaded across its width, its lens facing the card."""
    s = SUPERSAMPLE
    dark, light = METAL[mode]
    pivot, aim = lamp["pivot"], lamp["aim"]
    normal = (-aim[1], aim[0])
    back = (pivot[0] - aim[0] * CAN_LENGTH * 0.45, pivot[1] - aim[1] * CAN_LENGTH * 0.45)
    front = lamp["lens"]
    # The yoke: a rod up to the truss and a hinge at the pivot.
    draw.line((pivot[0] * s, (TRUSS_Y + 2.5) * s, pivot[0] * s, pivot[1] * s), fill=dark, width=round(1.6 * s))
    # The back cap, then the body in strips from shadow to highlight and back.
    draw.polygon([(x * s, y * s) for x, y in _ellipse(back, normal, CAN_BACK_RADIUS, CAN_BACK_RADIUS * 0.4)],
                 fill=dark)
    steps = 14
    for step in range(steps):
        offset = -1 + 2 * (step + 0.5) / steps
        glint = max(0.0, 1 - abs(offset + 0.35) * 1.8)
        colour = _mix(dark, light, glint * 0.9)
        a = (back[0] + normal[0] * offset * CAN_BACK_RADIUS, back[1] + normal[1] * offset * CAN_BACK_RADIUS)
        b = (front[0] + normal[0] * offset * CAN_FRONT_RADIUS, front[1] + normal[1] * offset * CAN_FRONT_RADIUS)
        draw.line((a[0] * s, a[1] * s, b[0] * s, b[1] * s), fill=colour,
                  width=round(2 * CAN_FRONT_RADIUS / steps * s) + 2)
    hinge = 1.9
    draw.ellipse(((pivot[0] - hinge) * s, (pivot[1] - hinge) * s, (pivot[0] + hinge) * s, (pivot[1] + hinge) * s),
                 fill=light, outline=dark, width=s)
    # The lens: a dark rim, the glass in the beam's light, a hot centre and a glint.
    draw.polygon([(x * s, y * s) for x, y in _ellipse(front, normal, CAN_FRONT_RADIUS, CAN_FRONT_RADIUS * 0.42)],
                 fill=dark)
    glass = _ellipse(front, normal, CAN_FRONT_RADIUS * LENS_SHARE, CAN_FRONT_RADIUS * 0.42 * LENS_SHARE)
    draw.polygon([(x * s, y * s) for x, y in glass], fill=_mix(lamp["light"], (255, 255, 255), 0.45))
    core = _ellipse(front, normal, CAN_FRONT_RADIUS * 0.36, CAN_FRONT_RADIUS * 0.16)
    draw.polygon([(x * s, y * s) for x, y in core], fill=(255, 255, 255))


def hang(image, lamps, theme, untouched=None):
    """Light the page: each lamp's beam (only where ``untouched``, an L mask of the bare page,
    allows), then the truss and the lamps over it."""
    if not lamps:
        return
    mode = _mode(theme)
    for lamp in lamps:
        mask = _beam_mask(image.size, lamp, mode)
        if BEAM_STYLE == "halftone":
            mask = _halftone(mask, BEAM_ALPHA[mode] * lamp["strength"])
        else:
            _dust(mask, lamp, mode)
        if untouched is not None:
            mask = ImageChops.multiply(mask, untouched)
        image.paste(Image.new("RGB", image.size, lamp["light"]), mask=mask)
    # The rig, drawn large and scaled down so the cans read as rounded metal.
    left = int(min(lamp["pivot"][0] for lamp in lamps) - 22)
    right = int(max(lamp["pivot"][0] for lamp in lamps) + 22)
    bottom = int(max(lamp["lens"][1] for lamp in lamps) + CAN_FRONT_RADIUS + 3)
    width, height = right - left, bottom
    layer = Image.new("RGBA", (width * SUPERSAMPLE, height * SUPERSAMPLE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    shifted = [{**lamp, "pivot": (lamp["pivot"][0] - left, lamp["pivot"][1]),
                "lens": (lamp["lens"][0] - left, lamp["lens"][1])} for lamp in lamps]
    _truss(draw, shifted, mode)
    for lamp in shifted:
        _can(draw, lamp, mode)
    layer = layer.resize((width, height), Image.Resampling.LANCZOS)
    image.paste(layer.convert("RGB"), (left, 0), layer.getchannel("A"))


def pool(image, lamp, theme):
    """The faint light where a beam lands, just inside its card's top edge."""
    mode = _mode(theme)
    left, right, y = lamp["landing"]
    x0, y0, x1, _y1 = (int(value) for value in lamp["box"])
    width, depth = x1 - x0 - 4, POOL_DEPTH
    if width <= 0:
        return
    peak = POOL_ALPHA[mode] * lamp["strength"]
    fade = Image.linear_gradient("L").resize((width, depth)).point(lambda value: round((255 - value) * peak))
    across = Image.new("L", (width, depth), 0)
    ImageDraw.Draw(across).ellipse((left - x0 - 2, -depth, right - x0 - 2, depth), fill=255)
    mask = ImageChops.multiply(fade, across.filter(ImageFilter.GaussianBlur(6)))
    image.paste(Image.new("RGB", (width, depth), lamp["light"]), (x0 + 2, y0 + 2), mask)
