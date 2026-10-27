"""Small, deterministic traffic UI pictograms. These are not regulatory signs.

Each icon uses the same 24-unit grid and 2.3-unit strokes. Native preview output
is supersampled so 16-20px icons retain crisp, consistent edges.
"""
from PIL import Image, ImageDraw

PALETTES = {
    'day': {'ink': '#193b50', 'accent': '#d35d20', 'blue': '#087e98', 'paper': '#ffffff'},
    'night': {'ink': '#dcebf3', 'accent': '#ffad70', 'blue': '#80cddc', 'paper': '#080e14'},
}
NAMES = ('barricade', 'exit_closed', 'entrance_closed', 'lane_merge', 'cone',
         'alternating_traffic', 'scheduled', 'active')
ALIASES = {'ramp_closed': 'exit_closed', 'full_closure': 'barricade',
           'partial_lane': 'cone', 'clock': 'scheduled', 'construction': 'active'}


def shapes(name):
    """Return the shared primitives used by both SVG and Pillow renderers."""
    name = ALIASES.get(name, name)
    if name not in NAMES:
        raise ValueError(f'Unknown traffic icon: {name}')
    lines = []
    polys = []
    circles = []
    rects = []
    if name == 'barricade':
        lines += [([(6, 13), (6, 21)], 'ink'), ([(18, 13), (18, 21)], 'ink')]
        rects += [((3, 6, 21, 14), 'accent', 'ink')]
        polys += [([(5, 6), (9, 6), (5, 14), (3, 14), (3, 10)], 'paper'),
                  ([(14, 6), (18, 6), (14, 14), (10, 14)], 'paper')]
    elif name in {'exit_closed', 'entrance_closed'}:
        lines += [([(5, 3), (5, 21)], 'ink'), ([(11, 21), (11, 14), (20, 5)], 'ink'),
                  ([(8, 4), (8, 7)], 'ink'), ([(8, 11), (8, 13)], 'ink'),
                  ([(8, 17), (8, 20)], 'ink'), ([(15, 5), (21, 11)], 'accent')]
        if name == 'exit_closed':
            lines += [([(15, 5), (20, 5), (20, 10)], 'ink')]
        else:
            lines += [([(11, 9), (11, 14), (16, 14)], 'ink')]
    elif name == 'lane_merge':
        lines += [([(8, 21), (8, 3)], 'ink'), ([(4, 7), (8, 3), (12, 7)], 'ink'),
                  ([(19, 21), (19, 16), (8, 7)], 'accent')]
    elif name in {'cone', 'active'}:
        polys += [([(11, 3), (13, 3), (19, 20), (5, 20)], 'accent'),
                  ([(9, 9), (15, 9), (16, 12), (8, 12)], 'paper'),
                  ([(7, 16), (17, 16), (18, 18), (6, 18)], 'paper')]
        lines += [([(3, 21), (21, 21)], 'ink')]
    elif name == 'alternating_traffic':
        lines += [([(3, 7), (21, 7)], 'ink'), ([(17, 3), (21, 7), (17, 11)], 'ink'),
                  ([(21, 17), (3, 17)], 'blue'), ([(7, 13), (3, 17), (7, 21)], 'blue')]
    elif name == 'scheduled':
        circles += [((3, 3, 21, 21), None, 'ink')]
        lines += [([(12, 7), (12, 12), (16, 14)], 'ink')]
    return {'lines': lines, 'polys': polys, 'circles': circles, 'rects': rects}


def get_palette(palette):
    if isinstance(palette, str):
        return PALETTES[palette]
    return {**PALETTES['day'], **palette}


def draw_icon(name, size=18, palette='day'):
    """Return an RGBA PIL image; `palette` is day/night or a color dictionary."""
    if not 12 <= size <= 256:
        raise ValueError('Icon size must be between 12 and 256 pixels')
    colors = get_palette(palette)
    scale = size * 4 / 24
    im = Image.new('RGBA', (size * 4, size * 4))
    d = ImageDraw.Draw(im)
    width = max(1, round(2.3 * scale))
    def box(coords):
        return tuple(round(v * scale) for v in coords)
    def points(coords):
        return [(round(x * scale), round(y * scale)) for x, y in coords]
    primitives = shapes(name)
    for bounds, fill, stroke in primitives['rects']:
        d.rectangle(box(bounds), fill=colors.get(fill), outline=colors.get(stroke), width=width)
    for coords, fill in primitives['polys']:
        d.polygon(points(coords), fill=colors[fill])
    for bounds, fill, stroke in primitives['circles']:
        d.ellipse(box(bounds), fill=colors.get(fill), outline=colors.get(stroke), width=width)
    for coords, stroke in primitives['lines']:
        d.line(points(coords), fill=colors[stroke], width=width, joint='curve')
    return im.resize((size, size), Image.Resampling.LANCZOS)
