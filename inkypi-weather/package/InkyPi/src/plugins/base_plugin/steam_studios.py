"""A Steam game's studio logo: the avatar of the creator clan its store entry names as developer.

Steam's store (IStoreBrowseService/GetItems, basic info) gives each developer's creator clan; the
clan's public member-list page gives its avatar, which studios set to their logo.
"""

from __future__ import annotations

import re

STORE_ITEMS_URL = "https://api.steampowered.com/IStoreBrowseService/GetItems/v1/"
CLAN_XML_URL = "https://steamcommunity.com/gid/{gid}/memberslistxml/?xml=1"
CLAN_STEAMID_BASE = 103582791429521408
AVATAR_RE = re.compile(
    r"<avatarFull>\s*(?:<!\[CDATA\[)?\s*(https://avatars\.(?:[a-z]+\.)?steamstatic\.com/[0-9a-f]{40}_full\.jpg)"
)


def clan_page_url(clan):
    """The public page of a creator clan (by its account id) that carries its avatar."""
    return CLAN_XML_URL.format(gid=CLAN_STEAMID_BASE + int(clan))


def avatar_url(page):
    """The clan's full-size avatar named on its page, or ""."""
    match = AVATAR_RE.search(page or "")
    return match.group(1) if match else ""


def cut_ground(logo, tolerance=30, least=0.04):
    """The logo off the flat ground its avatar square is published on (RGBA, cropped to what is
    left): when the four corners share one colour, that colour is cut away from the edges in.
    None when the avatar has no flat ground (a photo) or too little is left (under ``least``)."""
    from PIL import Image, ImageChops, ImageDraw, ImageFilter

    art = logo.convert("RGB")
    width, height = art.size
    points = ((0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1))
    corners = [art.getpixel(point) for point in points]
    if max(max(channel) - min(channel) for channel in zip(*corners)) > tolerance:
        return None
    ground = tuple(sum(channel) // 4 for channel in zip(*corners))
    near = Image.new("L", art.size, 0)
    near.putdata([255 if max(abs(a - b) for a, b in zip(pixel, ground)) <= tolerance else 0
                  for pixel in art.get_flattened_data()])
    for corner in points:
        if near.getpixel(corner) == 255:
            ImageDraw.floodfill(near, corner, 128)
    removed = near.point(lambda value: 255 if value == 128 else 0)
    # The ground's blended rim (a JPEG's, an antialiased edge's) goes with it: pixels within two of
    # the cut ground and still well nearer its colour than the mark's.
    loose = Image.new("L", art.size, 0)
    loose.putdata([255 if max(abs(a - b) for a, b in zip(pixel, ground)) <= tolerance * 3 else 0
                   for pixel in art.get_flattened_data()])
    removed = ImageChops.lighter(removed, ImageChops.multiply(removed.filter(ImageFilter.MaxFilter(5)), loose))
    alpha = ImageChops.invert(removed)
    if not alpha.getbbox() or alpha.histogram()[255] < width * height * least:
        return None
    mark = art.convert("RGBA")
    mark.putalpha(alpha.filter(ImageFilter.GaussianBlur(1)))
    return mark.crop(alpha.getbbox())


def peel_plate(mark, tolerance=30, ring_share=0.85, least=0.04, solid=0.55):
    """A cut-out logo (RGBA) without the plate its letters sit on (Valve's white box round its red
    letters): when at least ``ring_share`` of its outline is one colour, and that colour fills at
    least ``solid`` of a frame spanning the whole logo, it is cut away from the outline in, as long
    as at least ``least`` of the logo is left (a mark all of one colour stays as it is). Cropped to
    what is left."""
    rgba = mark.convert("RGBA")
    width, height = rgba.size
    pixels = rgba.load()

    # The opaque core decides (a cut-out's soft rim blends the plate with the ground it came off).
    def visible(x, y):
        return 0 <= x < width and 0 <= y < height and pixels[x, y][3] > 200

    steps = ((1, 0), (-1, 0), (0, 1), (0, -1))
    shown = [(x, y) for y in range(height) for x in range(width) if visible(x, y)]
    ring = [(x, y) for x, y in shown if any(not visible(x + dx, y + dy) for dx, dy in steps)]
    if not ring:
        return rgba
    counts = {}
    for x, y in ring:
        key = tuple(channel // 32 for channel in pixels[x, y][:3])
        counts[key] = counts.get(key, 0) + 1
    common = max(counts, key=counts.get)
    colour = tuple(sorted(pixels[x, y][index] for x, y in ring
                          if tuple(channel // 32 for channel in pixels[x, y][:3]) == common)[counts[common] // 2]
                   for index in range(3))

    def near(point):
        return max(abs(a - b) for a, b in zip(pixels[point][:3], colour)) <= tolerance

    edge = [point for point in ring if near(point)]
    if len(edge) < len(ring) * ring_share:
        return rgba
    plate, stack = set(edge), list(edge)
    while stack:
        x, y = stack.pop()
        for dx, dy in steps:
            point = (x + dx, y + dy)
            if point not in plate and visible(*point) and near(point):
                plate.add(point)
                stack.append(point)
    # A plate is a solid frame round the whole logo (Valve's box), not its own letters drawn in
    # that colour (Eternity's white ET, PUBG's white letters on their black ground).
    xs, ys = [x for x, _y in plate], [y for _x, y in plate]
    spans = (max(xs) - min(xs) + 1) * (max(ys) - min(ys) + 1)
    whole = (min(x for x, _y in shown), min(y for _x, y in shown), max(x for x, _y in shown), max(y for _x, y in shown))
    if len(plate) < spans * solid or any(abs(a - b) > 2 for a, b in zip((min(xs), min(ys), max(xs), max(ys)), whole)):
        return rgba
    # The plate's blend into its letters (their antialiased rims) goes with it: two pixels in, while
    # still well nearer the plate's colour than the letters'.
    for _step in range(2):
        rim = {(x + dx, y + dy) for x, y in plate for dx, dy in steps}
        plate |= {point for point in rim - plate if visible(*point)
                  and max(abs(a - b) for a, b in zip(pixels[point][:3], colour)) <= tolerance * 3}
    if len(shown) - len(plate) < len(shown) * least:
        return rgba
    for x, y in plate:
        pixels[x, y] = (*pixels[x, y][:3], 0)
    # The plate's soft rim (its blurred edge) goes with it.
    for y in range(height):
        for x in range(width):
            if pixels[x, y][3] <= 200:
                pixels[x, y] = (*pixels[x, y][:3], 0)
    box = rgba.getchannel("A").point(lambda value: 255 if value > 16 else 0).getbbox()
    return rgba.crop(box) if box else rgba


def legible(mark, least=25, lowest=7, specks=0.2, speck=3):
    """Whether a cut-out logo still reads at the size it is shown: at least ``least`` solid pixels,
    ``lowest`` pixels tall, and no more than ``specks`` of it in loose bits of ``speck`` pixels or
    fewer (what fine lettering crumbles into)."""
    from PIL import ImageDraw

    solid = mark.getchannel("A").point(lambda value: 255 if value > 128 else 0)
    count = solid.histogram()[255]
    if count < least or mark.height < lowest:
        return False
    pixels, loose, label = solid.load(), 0, 0
    for y in range(solid.height):
        for x in range(solid.width):
            if pixels[x, y] == 255 and label < 250:
                label += 1
                ImageDraw.floodfill(solid, (x, y), label)
    sizes = solid.histogram()
    loose = sum(sizes[value] for value in range(1, label + 1) if sizes[value] <= speck)
    return loose <= count * specks


def developer_clans(store_items):
    """Each app's first developer's creator clan (account id, as text) from GetItems' items; apps
    whose developer has none are left out."""
    clans = {}
    for item in store_items or ():
        if not isinstance(item, dict) or not str(item.get("appid") or "").isdecimal():
            continue
        basic = item.get("basic_info") if isinstance(item.get("basic_info"), dict) else {}
        developers = [entry for entry in basic.get("developers") or [] if isinstance(entry, dict)]
        clan = str((developers[0].get("creator_clan_account_id") if developers else "") or "")
        if clan.isdecimal() and int(clan):
            clans[str(item["appid"])] = clan
    return clans
