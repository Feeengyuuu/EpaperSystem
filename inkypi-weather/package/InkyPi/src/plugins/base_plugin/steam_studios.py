"""A Steam game's studio logo: the avatar of the creator clan its store entry names as developer.

Steam's store (IStoreBrowseService/GetItems, basic info) gives each developer's creator clan; the
clan's public member-list page gives its avatar, which studios set to their logo.

A studio Steam names no creator clan for (Infinity Ward on Call of Duty: Modern Warfare 4) has its
official logo looked up on Wikidata instead: the game studio of that name, its logo claim (the
small logo or icon first, then the logo image) and that file on Wikimedia Commons.
"""

from __future__ import annotations

import hashlib
import re
from urllib.parse import quote

STORE_ITEMS_URL = "https://api.steampowered.com/IStoreBrowseService/GetItems/v1/"
CLAN_XML_URL = "https://steamcommunity.com/gid/{gid}/memberslistxml/?xml=1"
CLAN_STEAMID_BASE = 103582791429521408
AVATAR_RE = re.compile(
    r"<avatarFull>\s*(?:<!\[CDATA\[)?\s*(https://avatars\.(?:[a-z]+\.)?steamstatic\.com/[0-9a-f]{40}_full\.jpg)"
)
WIKIDATA_API = "https://www.wikidata.org/w/api.php"
COMMONS_FILES = "https://upload.wikimedia.org/wikipedia/commons/"
# Wikimedia asks automated clients to name themselves with a way to reach their maintainer.
WIKIMEDIA_AGENT = "InkyPi/1.0 (https://github.com/Feeengyuuu/EpaperSystem)"
# A studio's logo claims, the square ones first: small logo or icon, icon, logo image.
LOGO_CLAIMS = ("P8972", "P2910", "P154")
# Commons renders thumbnails at its standard widths only (a 240 px one is refused).
LOGO_WIDTH = 250
_CJK = re.compile(r"[぀-ヿ㐀-鿿가-힯]")


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


# A company's legal form, left off the end of its name when names are matched ("Valve
# Corporation" is Steam's "Valve", "miHoYo Co., Ltd." its "miHoYo").
LEGAL_FORMS = {"corporation", "corp", "incorporated", "inc", "limited", "ltd", "co", "company", "llc", "gmbh", "sa",
               "ab", "plc", "kk", "srl", "bv", "oy", "pty", "株式会社", "有限公司"}


def name_key(name):
    """A studio's name for matching: case, marks (®, ™, punctuation, spaces) and a trailing legal
    form aside."""
    words = re.findall(r"\w+", str(name or "").casefold())
    while len(words) > 1 and words[-1] in LEGAL_FORMS:
        words.pop()
    return "".join(words)


def studio_search(name):
    """Wikidata's entity search for a studio by its name (in Chinese for a Chinese, Japanese or
    Korean name), its descriptions in English."""
    return {"action": "wbsearchentities", "search": str(name or "").strip()[:80],
            "language": "zh" if _CJK.search(str(name or "")) else "en", "uselang": "en",
            "type": "item", "limit": 7, "format": "json"}


def studio_entity(found, name):
    """The id of the game studio a search ``found`` for ``name``: a result whose label (or the
    alias it matched) is that name and whose description says it makes games; "" for none."""
    wanted = name_key(name)
    for hit in (found or {}).get("search") or []:
        if not isinstance(hit, dict) or not re.fullmatch(r"Q\d+", str(hit.get("id") or "")):
            continue
        description = str(hit.get("description") or "").casefold()
        names = {name_key(hit.get("label")), name_key((hit.get("match") or {}).get("text"))
                 if isinstance(hit.get("match"), dict) else ""}
        if wanted and wanted in names and ("game" in description or "游戏" in description):
            return hit["id"]
    return ""


def claims_query(qid, prop):
    """Wikidata's claims of one property of an entity: a few KB, where the whole entity of a
    large company runs to 300 KB."""
    return {"action": "wbgetclaims", "entity": qid, "property": prop, "format": "json"}


def logo_file(answer):
    """The Commons file a claims answer names as a logo, by LOGO_CLAIMS in order (a preferred
    claim first, deprecated ones never); "" for none."""
    claims = (answer or {}).get("claims") if isinstance((answer or {}).get("claims"), dict) else {}
    for prop in LOGO_CLAIMS:
        found = [claim for claim in claims.get(prop) or [] if isinstance(claim, dict) and claim.get("rank") != "deprecated"]
        for claim in sorted(found, key=lambda claim: claim.get("rank") != "preferred"):
            value = ((claim.get("mainsnak") or {}).get("datavalue") or {}).get("value")
            if isinstance(value, str) and value.lower().endswith((".svg", ".png", ".jpg", ".jpeg")):
                return value
    return ""


def commons_urls(file_name, width=LOGO_WIDTH):
    """Where Commons serves a file: its ``width`` px thumbnail (an SVG's drawn as PNG), then, for
    a picture, the file itself (one narrower than ``width`` has no larger thumbnail)."""
    name = str(file_name or "").strip().replace(" ", "_")
    if not name:
        return []
    digest = hashlib.md5(name.encode("utf-8"), usedforsecurity=False).hexdigest()
    folder, quoted = f"{digest[0]}/{digest[:2]}/", quote(name, safe="")
    suffix = ".png" if name.lower().endswith(".svg") else ""
    thumb = f"{COMMONS_FILES}thumb/{folder}{quoted}/{int(width)}px-{quoted}{suffix}"
    return [thumb] if suffix else [thumb, f"{COMMONS_FILES}{folder}{quoted}"]


def official_mark(logo, height=48, widest=8):
    """An official logo as a mark (RGBA, cropped to what is drawn, ``height`` px tall at most and
    at most ``widest`` times as wide): a logo published on a flat ground has it cut away; None for
    one that is a picture (no transparency and no flat ground) or nearly empty."""
    from PIL import Image

    art = logo.convert("RGBA")
    if art.getchannel("A").getextrema()[0] == 255:
        art = cut_ground(art)
        if art is None:
            return None
    box = art.getchannel("A").point(lambda value: 255 if value > 16 else 0).getbbox()
    if box is None or (box[2] - box[0]) * (box[3] - box[1]) < 16:
        return None
    art = art.crop(box)
    scale = min(1.0, height / art.height, height * widest / art.width)
    size = (max(1, round(art.width * scale)), max(1, round(art.height * scale)))
    return art if size == art.size else art.resize(size, Image.Resampling.LANCZOS)


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
