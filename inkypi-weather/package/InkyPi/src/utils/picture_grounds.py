"""Pictures set on a page without the ground they came on.

A seal, logo or avatar on a flat ground loses the colour at least ``share`` of its edge is within
``tolerance`` of, flooded in from the edge (the same colour inside the mark stays); a photo keeps
its frame. A diagram drawn on white (a paper's figure) is blended into the page instead of sitting
on a white mat: multiplied onto a light page, and on a dark one turned to light-on-dark first (its
lightness inverted, its hues kept) and lightened onto it.
"""

from __future__ import annotations

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from utils.theme_utils import contrast_ratio


GROUND_TOLERANCE, GROUND_SHARE = 36, 0.9
# A figure whose edge is at least this share near white (within WHITE_TOLERANCE) is a drawing on white.
WHITE_TOLERANCE, WHITE_SHARE = 24, 0.85


def _edge(size):
    width, height = size
    return [(x, y) for x in range(width) for y in (0, height - 1)] + \
        [(x, y) for y in range(1, height - 1) for x in (0, width - 1)]


def cut_flat_ground(picture, side, *, tolerance=GROUND_TOLERANCE, share=GROUND_SHARE):
    """``picture`` (at most ``side`` * 3 px) without the flat ground its edge shows, cropped to
    what stands on it (RGBA); None when its edge is not one colour, as a photo's is."""
    art = picture.convert("RGBA")
    art.thumbnail((side * 3, side * 3), Image.Resampling.LANCZOS)
    alpha = art.getchannel("A")
    width, height = art.size
    edge = _edge(art.size)
    if any(alpha.getpixel(corner) < 128 for corner in ((0, 0), (width - 1, 0), (0, height - 1),
                                                         (width - 1, height - 1))):
        # Already cut out (a round seal leaves its corners clear): only its margins go.
        box = alpha.point(lambda value: 255 if value > 16 else 0).getbbox()
        return art.crop(box) if box else None
    rgb = art.convert("RGB")
    colours = [rgb.getpixel(point) for point in edge]
    ground = tuple(sorted(channel)[len(channel) // 2] for channel in zip(*colours))
    if sum(max(abs(a - b) for a, b in zip(colour, ground)) <= tolerance
           for colour in colours) < share * len(colours):
        return None
    red, green, blue = ImageChops.difference(rgb, Image.new("RGB", rgb.size, ground)).split()
    distance = ImageChops.lighter(ImageChops.lighter(red, green), blue)
    near = distance.point(lambda value: 255 if value <= tolerance else 0)
    # Only the ground that reaches the edge goes: the same colour inside the mark stays.
    for point in edge:
        if near.getpixel(point) == 255:
            ImageDraw.floodfill(near, point, 128)
    flooded = near.point(lambda value: 255 if value == 128 else 0)
    # Where the mark's antialiased rim meets the ground, it fades by how far from the ground it is.
    rim = ImageChops.subtract(flooded.filter(ImageFilter.MaxFilter(3)), flooded)
    graded = distance.point(lambda value: min(255, value * 255 // (2 * tolerance)))
    kept = ImageChops.invert(flooded)
    kept.paste(graded, (0, 0), rim)
    art.putalpha(ImageChops.multiply(kept, alpha))
    box = art.getchannel("A").point(lambda value: 255 if value > 16 else 0).getbbox()
    return art.crop(box) if box else None


def readable_on(art, paper, ink, *, min_contrast=1.6):
    """``art`` (RGBA) with its parts too close to ``paper`` in ``ink`` (a white letter left on a
    white page once its coloured tile is gone); its other colours stay."""
    art = art.copy()
    pixels = art.load()
    paper, ink = tuple(paper[:3]), tuple(ink[:3])
    for y in range(art.height):
        for x in range(art.width):
            red, green, blue, alpha = pixels[x, y]
            if alpha >= 8 and contrast_ratio((red, green, blue), paper) < min_contrast:
                pixels[x, y] = (*ink, alpha)
    return art


def drawn_on_white(picture, *, tolerance=WHITE_TOLERANCE, share=WHITE_SHARE):
    """Whether ``picture``'s edge is (nearly all) white: a diagram, not a photo."""
    rgb = picture.convert("RGB")
    rgb.thumbnail((160, 160))
    edge = _edge(rgb.size)
    white = sum(min(rgb.getpixel(point)) >= 255 - tolerance for point in edge)
    return white >= share * len(edge)


def _light_on_dark(rgb):
    """The drawing with its lightness inverted and its hues kept: white ground to black, black
    lines to white, a blue box still blue."""
    hue, saturation, value = ImageChops.invert(rgb).convert("HSV").split()
    hue = hue.point(lambda channel: (channel + 128) % 256)
    return Image.merge("HSV", (hue, saturation, value)).convert("RGB")


def blend_drawing(image, drawing, xy, *, night):
    """Blend ``drawing`` (a diagram on white) into ``image`` at ``xy``: its white becomes the
    page wherever it falls, whatever the page's ground there."""
    x, y = (int(value) for value in xy)
    rgb = drawing.convert("RGB")
    region = image.crop((x, y, x + rgb.width, y + rgb.height)).convert("RGB")
    blended = ImageChops.lighter(region, _light_on_dark(rgb)) if night else ImageChops.multiply(region, rgb)
    image.paste(blended, (x, y))
