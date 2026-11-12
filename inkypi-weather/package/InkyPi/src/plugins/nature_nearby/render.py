"""800 x 480 Nature Nearby page: the newest find, this week's top species, life groups (with
eBird's notable birds under them when a key is set) and a filmstrip of what was just observed
around home."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageOps

from utils.doodle_backdrop import paint_doodles
from utils.flat_icons import draw_icon
from utils.local_wordmark import paste_local_wordmark
from utils.fonts import load_font
from utils.page_canvas import PageCanvas

from .sources import (
    ESTABLISHMENT, IUCN_CATEGORIES, RECENT_TILES, SPECIES_TILES, group_info, is_simplified, local_now, parse_time,
)


ASSETS = Path(__file__).resolve().parent / "assets"
# The sources' official logos (assets/logos, tools/build_nature_source_logos.py) where the page
# names them; on the night page their dark lettering takes the page's ink.
LOGO_MIN_CONTRAST = 2.0
# The newest find's name: large enough to lead the card (owner: 20% over the old 23 px).
HERO_NAME_SIZE = 28
# A field guide's faces (tools/build_nature_fonts.py): the cards' titles and the species' names
# in Noto Serif SC SemiBold, their scientific names in Lora Italic at LATIN_WEIGHT; the small
# figures and notes stay in the page's sans. A missing file falls back to the page's face.
FONT_DIR = Path(__file__).resolve().parents[2] / "static" / "fonts"
FACES = {"serif": FONT_DIR / "NotoSerifSC-SemiBold-Nature.ttf", "latin": FONT_DIR / "Lora-Italic-VF.ttf"}
LATIN_WEIGHT = 560
# The newest find's photo takes the height its text leaves in the card, never less than this.
HERO_PHOTO_MIN = 110
# The week's species and the filmstrip are rows of picture tiles (sources.SPECIES_TILES and
# RECENT_TILES of them, as wide as their card allows), their photos this tall.
SPECIES_PHOTO_HEIGHT, RECENT_PHOTO_HEIGHT = 102, 90
# The months it is seen around home: twelve bars this wide and tall, shown with at least
# SEASON_MIN sightings in all.
SEASON_BAR, SEASON_GAP, SEASON_HEIGHT, SEASON_MIN = 5, 2, 12, 12
# Pure white or black paper and neutral grey rules: an off-white or tinted ground, or a tinted
# grey, dithers into scattered yellow, blue or green dots on the panel. Accents are refined
# crimson, cobalt, emerald and gold rather than the panel's pure inks (owner's choice).
DAY = {
    "paper": "#ffffff", "panel": "#e8eadf", "ink": "#14201a", "muted": "#1f2b24", "rule": "#999999",
    "green": "#059669", "yellow": "#e8a90c", "blue": "#2554c7", "red": "#c81e32", "band": "#14201a",
    "badge_ink": "#ffffff",
}
NIGHT = {
    "paper": "#000000", "panel": "#18271f", "ink": "#eef3ec", "muted": "#c7d3cb", "rule": "#5a5a5a",
    "green": "#34d399", "yellow": "#fbbf24", "blue": "#7aa2ff", "red": "#ff6b78", "band": "#05090c",
    "badge_ink": "#000000",
}
WEEKDAYS = "一二三四五六日"
HERO_CARD = (12, 58, 330, 340)
SPECIES_CARD = (340, 58, 788, 256)
GROUP_CARD = (12, 346, 330, 468)
RECENT_CARD = (340, 262, 788, 468)
HEADER = 28
# California's water at the filmstrip's foot, in turn with the line the foot has (even hours the
# water, odd hours the line): from WATER_TOP down a key, a level gauge a reservoir, three a turn (filled to its
# share of capacity, a notch where the date's average stands, its name over its share of that
# average) and the Sierra's snow: its depth over its water against the median for the date.
WATER_TOP = 428
WATER_KEY_W, WATER_CELL_W, WATER_GAUGE = 60, 92, (11, 26)
# Three reservoirs a turn, so each has room for its name: the northern three and the southern
# three on alternate even hours.
WATER_PER_TURN = 3
# On the other hours the foot carries up to this many cells (threatened species, the top observer).
FOOT_CELLS = 3
# Quiet nature doodles in each card's colour under its content, by the card's icon: leaves,
# birds and butterflies, or the naturalist's binoculars, notebook and lens.
DOODLES = {icon: Path(__file__).resolve().parent / "assets" / "doodles" / f"{name}.png"
           for icon, name in (("leaf", "wildlife"), ("trophy", "fieldwork"), ("pulse", "wildlife"),
                              ("clock", "fieldwork"))}


@lru_cache(maxsize=32)
def _face(kind, size):
    try:
        font = load_font(FACES[kind], int(size))
        if kind == "latin":
            font.set_variation_by_axes([LATIN_WEIGHT])
        return font
    except (OSError, ValueError, KeyError):
        return None


def _covers(text):
    """Whether the serif face sets every character of ``text``: it keeps GB 2312's hanzi, ASCII,
    Latin-1 and the punctuation (tools/build_nature_fonts.py), so a rarer bird's name (鹀, 鸮)
    is not."""
    for char in str(text or ""):
        code = ord(char)
        if code <= 0xFF or 0x2000 <= code <= 0x206F or 0x3000 <= code <= 0x303F or 0xFF00 <= code <= 0xFFEF:
            continue
        if not 0x4E00 <= code <= 0x9FFF:
            return False
        try:
            char.encode("gb2312")
        except UnicodeEncodeError:
            return False
    return True


class _FaceCanvas(PageCanvas):
    """The page's canvas drawing in one of its field-guide faces: same picture, palette and
    clipping and wrapping, only the font differs. Text the serif face cannot set whole is drawn
    in the page's own face instead, rather than with a missing glyph's box."""

    def __init__(self, page, kind):
        self.palette, self.image, self.draw, self.family = page.palette, page.image, page.draw, page.family
        self.kind, self.page = kind, page
        self._fonts = {}

    def font(self, size, bold=True):
        return _face(self.kind, size) or super().font(size, bold)

    def text(self, xy, text, size=14, color="ink", **kwargs):
        if self.kind == "serif" and not _covers(text):
            return self.page.text(xy, text, size, color, **kwargs)
        return super().text(xy, text, size, color, **kwargs)

    def wrap(self, text, size, width, max_lines, bold=True):
        if self.kind == "serif" and not _covers(text):
            return self.page.wrap(text, size, width, max_lines, bold)
        return super().wrap(text, size, width, max_lines, bold)


def _rgb(value):
    if isinstance(value, str) and value.startswith("#") and len(value) == 7:
        return tuple(int(value[index:index + 2], 16) for index in (1, 3, 5))
    return value


def _palette(settings):
    theme = settings.get("_inkypi_theme") or {}
    palette = NIGHT if theme.get("mode", settings.get("themeMode")) == "night" else DAY
    return {key: _rgb(value) for key, value in palette.items()}


def ago(stamp, now):
    seconds = max(0, (now - stamp).total_seconds())
    if seconds < 3600:
        return f"{max(1, int(seconds // 60))} 分钟前"
    if seconds < 86400:
        return f"{int(seconds // 3600)} 小时前"
    return f"{int(seconds // 86400)} 天前"


def _display_name(item):
    return item.get("name") or item.get("scientific") or ""


def _luminance(rgb):
    channels = [value / 255 for value in rgb[:3]]
    channels = [value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4 for value in channels]
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


# The header's source logos (iNaturalist, eBird) this tall, this far apart.
HEADER_LOGO_HEIGHTS, HEADER_LOGO_GAP = (26, 22), 14


@lru_cache(maxsize=8)
def _logo(name, height, paper, ink):
    """A source's logo ``height`` px tall in its own colours, its parts that would vanish on
    ``paper`` in ``ink``; None without the file."""
    path = ASSETS / "logos" / f"{name}.png"
    if not path.is_file():
        return None
    with Image.open(path) as source:
        art = source.convert("RGBA")
    art = art.resize((max(1, round(art.width * height / art.height)), height), Image.Resampling.LANCZOS)
    ground = _luminance(paper)
    pixels = art.load()
    for y in range(art.height):
        for x in range(art.width):
            red, green, blue, alpha = pixels[x, y]
            lighter, darker = sorted((_luminance((red, green, blue)), ground), reverse=True)
            if alpha >= 8 and (lighter + 0.05) / (darker + 0.05) < LOGO_MIN_CONTRAST:
                pixels[x, y] = (*ink, alpha)
    return art


def _paste_logo(canvas, name, xy, height, anchor="lm"):
    """Paste a source logo at ``xy`` (its left middle, or right middle for ``rm``); returns its width."""
    logo = _logo(name, height, tuple(canvas.color("paper")), tuple(canvas.color("ink")))
    if logo is None:
        return 0
    x, y = xy
    left = x - logo.width if anchor == "rm" else x
    canvas.image.paste(logo, (round(left), round(y - logo.height / 2)), logo)
    return logo.width


def _card(canvas, box, bar, icon, title, detail="", logo=None):
    """A card in its own colour: a frame, a solid band along the top and the header rule in it,
    then the section icon, title and a right-aligned detail led by its source's logo."""
    left, top, right, bottom = box
    colour = canvas.color(bar)
    canvas.draw.rounded_rectangle(box, radius=7, outline=colour, width=2)
    canvas.draw.rounded_rectangle((left, top, right, top + 5), radius=7, fill=colour, corners=(True, True, False, False))
    if icon in DOODLES:
        doodles = (left + 2, top + HEADER + 2, right - 2, bottom - 2)
        paint_doodles(canvas.image, doodles, DOODLES[icon], colour, radius=5, offset=doodles[:2])
    draw_icon(canvas.draw, icon, (left + 10, top + 7), 18, colour, canvas.color("paper"))
    width = canvas.serif.text((left + 34, top + 16), title, 16, anchor="lm")
    if detail:
        detail_w = canvas.text((right - 8, top + 16), detail, 11, "muted", anchor="rm", bold=False,
                               width=right - left - width - 60)
        if logo:
            _paste_logo(canvas, logo, (right - 14 - detail_w, top + 16), 14, anchor="rm")
    canvas.rule((left + 8, top + HEADER, right - 8, top + HEADER), colour)


def _photo(canvas, images, url, box, group):
    left, top, right, bottom = box
    image = images.get(url)
    if image is not None:
        canvas.image.paste(ImageOps.fit(image, (right - left, bottom - top), Image.Resampling.LANCZOS,
                                        centering=(0.5, 0.45)), (left, top))
    else:
        _label, icon, _tone = group_info(group)
        canvas.draw.rectangle(box, fill=canvas.color("panel"))
        side = min(right - left, bottom - top) // 2
        draw_icon(canvas.draw, icon, ((left + right - side) / 2, (top + bottom - side) / 2), side,
                  canvas.color("ink"), canvas.color("panel"))
    canvas.draw.rectangle(box, outline=canvas.color("ink"), width=1)


def _group_chip(canvas, x, y, group, *, right_align=False):
    label, icon, tone = group_info(group)
    width = canvas.width(label, 11) + 26
    left = x - width if right_align else x
    fill = canvas.color(tone if tone != "ink" else "panel")
    ink = (20, 20, 20) if tone in ("yellow", "ink") else "badge_ink"
    canvas.draw.rounded_rectangle((left, y - 9, left + width, y + 9), radius=9, fill=fill)
    draw_icon(canvas.draw, icon, (left + 5, y - 6), 12, canvas.color(ink), fill)
    canvas.text((left + 20, y), label, 11, ink, anchor="lm")
    return width


def _reach_text(value):
    """How often the species has been observed worldwide: "全球 4.8 万次观察"."""
    return f"全球 {value / 10000:.1f} 万次观察" if value >= 10000 else f"全球 {value:,} 次观察"


def _iucn_chip(canvas, right, y, code):
    """The species' IUCN Red List category as a chip in the Red List's own colour, ending at
    ``right``; returns its width."""
    name, colour = IUCN_CATEGORIES[code]
    fill = _rgb(colour)
    text = f"{code} {name}"
    width = canvas.width(text, 10) + 12
    ink = (0, 0, 0) if _luminance(fill) > 0.35 else (255, 255, 255)
    canvas.draw.rounded_rectangle((right - width, y - 8, right, y + 8), radius=8, fill=fill)
    canvas.text((right - width / 2, y), text, 10, ink, anchor="mm")
    return width


def blurb_text(payload):
    """The featured species' Chinese Wikipedia lead in Simplified Chinese: Wikipedia's own zh-cn
    text, else iNaturalist's copy when that is already Simplified; "" otherwise."""
    blurb = ((payload.get("blurb") or {}).get("data") or {}).get("text") or ""
    if blurb:
        return blurb
    summary = (((payload.get("taxon") or {}).get("data")) or {}).get("wiki_summary") or ""
    return summary if is_simplified(summary) else ""


def season_months(payload):
    """The featured species' twelve monthly counts around home, or None with too few sightings."""
    months = ((payload.get("season") or {}).get("data") or {}).get("months")
    return months if isinstance(months, list) and len(months) == 12 and sum(months) >= SEASON_MIN else None


def _season(canvas, months, right, middle, now):
    """The months it is seen around home as twelve small bars ending at ``right``, this month in
    the card's green, with the peak month named before them; returns the width used."""
    peak = max(months)
    width = 12 * SEASON_BAR + 11 * SEASON_GAP
    left = right - width
    for index, count in enumerate(months):
        x = left + index * (SEASON_BAR + SEASON_GAP)
        height = max(1, round(SEASON_HEIGHT * count / peak)) if peak else 1
        colour = canvas.color("green") if index == now.month - 1 else canvas.color("muted")
        canvas.draw.rectangle((x, middle + SEASON_HEIGHT / 2 - height, x + SEASON_BAR - 1,
                               middle + SEASON_HEIGHT / 2), fill=colour)
    label = f"高峰 {months.index(peak) + 1} 月"
    label_w = canvas.text((left - 5, middle), label, 10, "muted", anchor="rm", bold=False)
    return width + 5 + label_w


def _hero(canvas, payload, images, now):
    """The newest find: its photo as tall as the card leaves it, then the name, its scientific
    name, its lineage and Red List category, two lines of the Wikipedia lead when there is one,
    where and when, and the photographer beside the months it is seen around home (or how often
    it has been observed worldwide)."""
    items = payload["recent"]["data"].get("items", [])
    taxon = ((payload.get("taxon") or {}).get("data")) or {}
    _card(canvas, HERO_CARD, "green", "leaf", "最新发现", "研究级", logo="inaturalist")
    left, top, right = HERO_CARD[0] + 8, HERO_CARD[1] + HEADER + 6, HERO_CARD[2] - 8
    if not items:
        canvas.text((left, 150), "观察数据暂不可用", 15, "muted", anchor="lm")
        return
    item = items[0]
    blurb, months = blurb_text(payload), season_months(payload)
    lines = canvas.wrap(blurb, 11, right - left, 2, bold=False) if blurb else []
    lineage = " · ".join(part for part in (taxon.get("order"), taxon.get("family"),
                                           ESTABLISHMENT.get(taxon.get("establishment"), "")) if part)
    # From the card's foot up: the photographer's line, where and when, the lead, the lineage,
    # the scientific name and the name; the photo has the rest.
    foot = HERO_CARD[3] - 11
    where_y = foot - 14
    blurb_y = where_y - 14 * len(lines)
    lineage_y = blurb_y - (15 if lineage or taxon.get("iucn") else 0)
    science_y = lineage_y - 16
    name_y = science_y - 21
    photo_bottom = max(top + HERO_PHOTO_MIN, name_y - 19)
    _photo(canvas, images, item["photo"], (left, top, right, photo_bottom), item["group"])
    chip = _group_chip(canvas, right, name_y, item["group"], right_align=True)
    canvas.serif.text((left, name_y), _display_name(item), HERO_NAME_SIZE, anchor="lm", width=right - left - chip - 8)
    canvas.latin.text((left, science_y), item["scientific"], 13, "muted", anchor="lm", width=right - left)
    chip_w = _iucn_chip(canvas, right, lineage_y, taxon["iucn"]) + 6 if taxon.get("iucn") else 0
    if lineage:
        canvas.text((left, lineage_y), lineage, 11, anchor="lm", width=right - left - chip_w)
    for index, line in enumerate(lines):
        canvas.text((left, blurb_y + 14 * index), line, 11, anchor="lm", bold=False)
    stamp = parse_time(item["time"])
    where = " · ".join(part for part in (item.get("place"), ago(stamp, now) if stamp else "",
                                         f"距家 {item['distance_km']:.0f} km" if item.get("distance_km") is not None
                                         else "") if part)
    canvas.text((left, where_y), where, 11, anchor="lm", bold=False, width=right - left)
    if months:
        used = _season(canvas, months, right, foot, now) + 8
    else:
        reach = _reach_text(taxon["observations"]) if taxon.get("observations") else ""
        used = canvas.text((right, foot), reach, 10, "muted", anchor="rm", bold=False) + 8 if reach else 0
    canvas.text((left, foot), f"© {item['observer']}" if item.get("observer") else item.get("attribution", ""),
                10, "muted", anchor="lm", bold=False, width=right - left - used)


def _species(canvas, payload, images):
    data = payload["species"]["data"]
    items = data.get("items", [])
    _card(canvas, SPECIES_CARD, "yellow", "trophy", "本周热门物种",
          f"过去 7 天 · 共 {data.get('total', 0):,} 种" if items else "")
    left, right = SPECIES_CARD[0] + 8, SPECIES_CARD[2] - 8
    if not items:
        canvas.text((left, 180), "物种统计暂不可用", 15, "muted", anchor="lm")
        return
    # A row of picture tiles: the photo, its place in the week on it, then the name, the
    # scientific name and the week's count under it.
    gap = 8
    width = (right - left - gap * (SPECIES_TILES - 1)) // SPECIES_TILES
    top = SPECIES_CARD[1] + HEADER + 6
    bottom = top + SPECIES_PHOTO_HEIGHT
    for index, item in enumerate(items[:SPECIES_TILES]):
        x = left + index * (width + gap)
        _photo(canvas, images, item.get("photo"), (x, top, x + width, bottom), item["group"])
        badge_fill = canvas.color("yellow" if index == 0 else "ink")
        canvas.draw.ellipse((x - 4, top - 4, x + 16, top + 16), fill=badge_fill, outline=canvas.color("paper"))
        canvas.text((x + 6, top + 6), str(index + 1), 12, (20, 20, 20) if index == 0 else "paper", anchor="mm")
        canvas.serif.text((x, bottom + 12), _display_name(item), 14, anchor="lm", width=width)
        canvas.latin.text((x, bottom + 28), item["scientific"], 11, "muted", anchor="lm", width=width)
        _label, icon, tone = group_info(item["group"])
        draw_icon(canvas.draw, icon, (x, bottom + 39), 15, canvas.color(tone), canvas.color("paper"))
        canvas.text((x + 19, bottom + 47), f"{item['count']} 次", 16, anchor="lm")


def _groups(canvas, payload, now):
    groups = payload["groups"]["data"].get("groups", [])
    birds = ((payload.get("birds") or {}).get("data") or {}).get("items") or []
    total = sum(group["count"] for group in groups)
    _card(canvas, GROUP_CARD, "blue", "pulse", "生物类群", f"本周物种数 · {total:,}" if total else "")
    left, right = GROUP_CARD[0] + 10, GROUP_CARD[2] - 10
    if not groups:
        canvas.text((left, 410), "类群统计暂不可用", 14, "muted", anchor="lm")
        return
    if birds:
        _groups_with_birds(canvas, groups, total, payload["birds"]["data"], now)
        return
    threatened = threatened_data(payload)
    if threatened:
        _groups_with_threatened(canvas, groups, total, threatened, pictured(payload))
        return
    bar_top, x = GROUP_CARD[1] + HEADER + 7, float(left)
    for group in groups:
        _label, _icon, tone = group_info(group["group"])
        width = (right - left) * group["count"] / total
        fill = canvas.color(tone) if tone != "ink" else canvas.color("panel")
        canvas.draw.rectangle((x, bar_top, x + width, bar_top + 12), fill=fill)
        x += width
    canvas.draw.rectangle((left, bar_top, right, bar_top + 12), outline=canvas.color("ink"), width=1)
    column_width = (right - left) / 3
    for index, group in enumerate(groups[:6]):
        label, icon, tone = group_info(group["group"])
        cx = left + (index % 3) * column_width
        cy = bar_top + 28 + (index // 3) * 20
        draw_icon(canvas.draw, icon, (cx, cy - 8), 16, canvas.color(tone if tone != "ink" else "ink"),
                  canvas.color("paper"))
        canvas.text((cx + 20, cy), f"{label} {group['count']}", 12, anchor="lm", width=column_width - 22)


def pictured(payload):
    """The scientific names of the species the page shows as pictures: the newest find, the
    week's species and the filmstrip."""
    recent = payload["recent"]["data"].get("items", [])[:1 + RECENT_TILES]
    species = payload["species"]["data"].get("items", [])[:SPECIES_TILES]
    return {item.get("scientific") for item in recent + species if item.get("scientific")}


def threatened_data(payload):
    """This week's species around home at vulnerable or worse ({total, partial, items}, perhaps
    none of them), or None when the reading is unavailable."""
    source = payload.get("threatened") or {}
    data = source.get("data") or {}
    if source.get("state") in (None, "unavailable", "disabled") or not isinstance(data.get("total"), int):
        return None
    return data


def threatened_count(threatened):
    """"35 种", or "200+ 种" when there were more threatened species than one page held."""
    return f"{threatened['total']}{'+' if threatened.get('partial') else ''} 种"


def _thin_groups(canvas, groups, total):
    """The life groups as a thin bar and their top three in a row under it; returns the row's
    bottom."""
    left, right = GROUP_CARD[0] + 10, GROUP_CARD[2] - 10
    bar_top, x = GROUP_CARD[1] + HEADER + 6, float(left)
    for group in groups:
        _label, _icon, tone = group_info(group["group"])
        width = (right - left) * group["count"] / total
        canvas.draw.rectangle((x, bar_top, x + width, bar_top + 8),
                              fill=canvas.color(tone) if tone != "ink" else canvas.color("panel"))
        x += width
    canvas.draw.rectangle((left, bar_top, right, bar_top + 8), outline=canvas.color("ink"), width=1)
    column = (right - left) / 3
    for index, group in enumerate(groups[:3]):
        label, icon, tone = group_info(group["group"])
        cx, cy = left + index * column, bar_top + 20
        draw_icon(canvas.draw, icon, (cx, cy - 7), 14, canvas.color(tone), canvas.color("paper"))
        canvas.text((cx + 17, cy), f"{label} {group['count']}", 11, anchor="lm", width=column - 19)
    return bar_top + 32


def _unshown(items, shown):
    """``items`` without the species the page already pictures (``shown`` scientific names), as
    long as that leaves any: a species is not listed twice on one page."""
    fresh = [item for item in items if item.get("scientific") not in shown]
    return fresh or items


def _groups_with_threatened(canvas, groups, total, threatened, shown=()):
    """The life groups as a thin bar and their top three, then this week's species at vulnerable
    or worse: how many, and the most observed with their Red List category (as their local status
    stands for one) and how often they were seen; one quiet line in a week without any."""
    left, right = GROUP_CARD[0] + 10, GROUP_CARD[2] - 10
    y = _thin_groups(canvas, groups, total)
    canvas.rule((left, y, right, y))
    draw_icon(canvas.draw, "alert", (left, y + 4), 14, canvas.color("red"), canvas.color("paper"))
    canvas.text((left + 18, y + 11), "受威胁物种", 11, anchor="lm")
    if not threatened["items"]:
        canvas.text((left, y + 29), "本周无易危及以上物种", 11, "muted", anchor="lm", bold=False)
        return
    canvas.text((right, y + 11), f"本周 {threatened_count(threatened)} · 易危及以上", 10, "muted", anchor="rm",
                bold=False)
    for index, item in enumerate(_unshown(threatened["items"], shown)[:2]):
        row = y + 29 + index * 17
        count_w = canvas.text((right, row), f"{item['count']} 次", 11, anchor="rm")
        chip_w = _iucn_chip(canvas, right - count_w - 6, row, item["iucn"]) + 6 if item.get("iucn") in IUCN_CATEGORIES \
            else 0
        canvas.serif.text((left, row), _display_name(item), 12, anchor="lm", width=right - left - count_w - chip_w - 12)


def _groups_with_birds(canvas, groups, total, birds, now):
    """The life groups as a thin bar and their top three, then eBird's notable birds this week:
    each the latest report of its species, with where and when."""
    left, right = GROUP_CARD[0] + 10, GROUP_CARD[2] - 10
    y = _thin_groups(canvas, groups, total)
    canvas.rule((left, y, right, y))
    logo_w = _paste_logo(canvas, "ebird", (left, y + 11), 13)
    if not logo_w:
        draw_icon(canvas.draw, "bird", (left, y + 4), 14, canvas.color("blue"), canvas.color("paper"))
    canvas.text((left + (logo_w + 6 if logo_w else 18), y + 11), "罕见鸟讯" if logo_w else "eBird 罕见鸟讯", 11,
                anchor="lm")
    canvas.text((right, y + 11), f"7 天 · {birds.get('species', 0)} 种", 10, "muted", anchor="rm", bold=False)
    for index, bird in enumerate(birds["items"][:2]):
        row = y + 29 + index * 17
        stamp = parse_time(bird.get("time"))
        meta = " · ".join(part for part in (bird.get("place"), ago(stamp, now).replace(" ", "") if stamp else "")
                          if part)
        meta_w = min(150, canvas.width(meta, 10, False)) if meta else 0
        if meta:
            canvas.text((right, row), meta, 10, "muted", anchor="rm", bold=False, width=150)
        canvas.serif.text((left, row), bird.get("name") or bird.get("scientific") or "", 12, anchor="lm",
                          width=right - left - meta_w - 6)


def water_turn(now):
    """Whether this hour is the water's at the filmstrip's foot (the even hours)."""
    return now.hour % 2 == 0


def _shown(payload, name):
    source = payload.get(name) or {}
    return source.get("data") if source.get("state") not in (None, "unavailable", "disabled") else None


def _gauge(canvas, x, top, level, mark=None):
    """A level gauge at ``x``: a tank filled from its foot to ``level`` (0 to 1) in the page's
    blue, with a notch at ``mark`` (the level the date's average would stand at)."""
    width, height = WATER_GAUGE
    canvas.draw.rectangle((x, top, x + width, top + height), outline=canvas.color("ink"), width=1)
    filled = round((height - 2) * max(0.0, min(1.0, level)))
    if filled:
        canvas.draw.rectangle((x + 1, top + height - filled, x + width - 1, top + height - 1), fill=canvas.color("blue"))
    if mark is not None and 0 < mark <= 1:
        y = top + height - 1 - round((height - 2) * mark)
        canvas.draw.line((x - 2, y, x + width + 2, y), fill=canvas.color("ink"), width=1)


def _water(canvas, payload, now):
    """California's water along the filmstrip's foot; False (nothing drawn) without the reservoirs."""
    water, snow = _shown(payload, "water"), _shown(payload, "snow")
    items = (water or {}).get("items") or []
    if not items:
        return False
    left, top = RECENT_CARD[0] + 8, WATER_TOP + 8
    upper, lower = top + 4, top + 19
    canvas.rule((left, WATER_TOP, RECENT_CARD[2] - 8, WATER_TOP))
    canvas.text((left, upper), "加州水库", 11, anchor="lm")
    canvas.text((left, lower), "占常年", 11, anchor="lm", bold=False)
    x = left + WATER_KEY_W
    turns = max(1, -(-len(items) // WATER_PER_TURN))
    first = (now.hour // 2 % turns) * WATER_PER_TURN
    for item in items[first:first + WATER_PER_TURN]:
        # The date's average as a level: where the water would stand at 100% of it.
        mark = item["pct_cap"] / item["pct_avg"] if item["pct_avg"] else None
        _gauge(canvas, x, top, item["pct_cap"] / 100, mark)
        canvas.text((x + WATER_GAUGE[0] + 7, upper), item["name"], 11, anchor="lm", bold=False,
                    width=WATER_CELL_W - WATER_GAUGE[0] - 10)
        canvas.text((x + WATER_GAUGE[0] + 7, lower), f"{item['pct_avg']}%", 13, anchor="lm")
        x += WATER_CELL_W
    if snow and (snow.get("pct_median") is not None or snow.get("swe_in")):
        # The Sierra's snow water: a peak, white from its top as far as the snow stands to the median.
        share = min(1.0, (snow.get("pct_median") or 0) / 100)
        peak = [(x, top + WATER_GAUGE[1]), (x + 7, top), (x + 14, top + WATER_GAUGE[1])]
        canvas.draw.polygon(peak, fill=canvas.color("blue"))
        cap = round(WATER_GAUGE[1] * 0.55 * share)
        if cap >= 3:
            half = cap * 7 / WATER_GAUGE[1]
            canvas.draw.polygon([(x + 7, top + 1), (x + 7 - half, top + cap), (x + 7 + half, top + cap)],
                                fill=canvas.color("paper"))
        room = RECENT_CARD[2] - 8 - x - 21
        if snow.get("depth_in") is not None:
            canvas.text((x + 21, upper), f"雪深 {snow['depth_in'] * 2.54:.0f} cm", 11, anchor="lm", bold=False,
                        width=room)
        canvas.text((x + 21, lower), f"雪水 {snow['pct_median']}%" if snow.get("pct_median") is not None
                    else f"雪水 {snow['swe_in'] * 25.4:.0f} mm", 13, anchor="lm", width=room)
    return True


def _foot(canvas, key, note, cells):
    """The filmstrip's foot on the hours the water does not have it, in the water's two lines: a
    key (``key`` over ``note``), then ``cells`` of (Red List code or None, name, figure) sharing
    the rest of the width, each name over its figure after its category's chip."""
    left, right, top = RECENT_CARD[0] + 8, RECENT_CARD[2] - 8, WATER_TOP + 8
    upper, lower = top + 4, top + 19
    canvas.rule((left, WATER_TOP, right, WATER_TOP))
    canvas.text((left, upper), key, 11, anchor="lm")
    canvas.text((left, lower), note, 11, anchor="lm", bold=False)
    x = left + WATER_KEY_W + 6
    width = (right - x) / max(1, len(cells))
    for code, name, figure in cells:
        text_left = x
        if code in IUCN_CATEGORIES:
            fill = _rgb(IUCN_CATEGORIES[code][1])
            canvas.draw.rounded_rectangle((x, top + 3, x + 24, top + 21), radius=5, fill=fill)
            canvas.text((x + 12, top + 12), code, 10, (0, 0, 0) if _luminance(fill) > 0.35 else (255, 255, 255),
                        anchor="mm")
            text_left = x + 30
        canvas.serif.text((text_left, upper), name, 12, anchor="lm", width=x + width - 8 - text_left)
        canvas.text((text_left, lower), figure, 13, anchor="lm", width=x + width - 8 - text_left)
        x += width


def _recent(canvas, payload, images, now):
    items = payload["recent"]["data"].get("items", [])[1:1 + RECENT_TILES]
    stats = payload["stats"]["data"]
    _card(canvas, RECENT_CARD, "red", "clock", "刚刚观察到",
          f"本周 {stats.get('observations', 0):,} 条观察 · {stats.get('observers', 0):,} 位观察者" if stats else "")
    left, right = RECENT_CARD[0] + 8, RECENT_CARD[2] - 8
    if not items:
        canvas.text((left, 400), "暂无新的观察", 14, "muted", anchor="lm")
        return
    gap = 8
    width = (right - left - gap * (RECENT_TILES - 1)) // RECENT_TILES
    top = RECENT_CARD[1] + HEADER + 6
    for index, item in enumerate(items):
        x = left + index * (width + gap)
        _photo(canvas, images, item["photo"], (x, top, x + width, top + RECENT_PHOTO_HEIGHT), item["group"])
        _label, icon, tone = group_info(item["group"])
        canvas.draw.ellipse((x + 3, top + 3, x + 21, top + 21), fill=canvas.color("paper"))
        draw_icon(canvas.draw, icon, (x + 5, top + 5), 14, canvas.color(tone), canvas.color("paper"))
        canvas.serif.text((x, top + RECENT_PHOTO_HEIGHT + 12), _display_name(item), 13, anchor="lm", width=width)
        stamp = parse_time(item["time"])
        meta = " · ".join(part for part in (ago(stamp, now).replace(" ", "") if stamp else "", item.get("place"))
                          if part)
        canvas.text((x, top + RECENT_PHOTO_HEIGHT + 27), meta, 10, "muted", anchor="lm", bold=False, width=width)
    if water_turn(now) and _water(canvas, payload, now):
        return
    threatened = threatened_data(payload)
    observer = []
    if stats.get("top_observer"):
        observer = [(None, f"@{stats['top_observer']}", f"{stats.get('top_observations', 0)} 条观察")]
    if threatened and threatened["items"] and ((payload.get("birds") or {}).get("data") or {}).get("items"):
        # With eBird's birds in the groups card, the week's vulnerable-or-worse species take the
        # foot: each its Red List category, its name and how often it was seen; the week's most
        # active observer has the room a short list leaves.
        cells = [(item.get("iucn"), _display_name(item), f"{item['count']} 次")
                 for item in _unshown(threatened["items"], pictured(payload))[:FOOT_CELLS]]
        cells += observer if len(cells) < FOOT_CELLS else []
        _foot(canvas, "易危及以上", f"本周 {threatened_count(threatened)}", cells)
    elif observer:
        cells = observer + [(None, "记录物种", f"{stats.get('top_species', 0)} 种")]
        if stats.get("observations") and stats.get("top_observations"):
            cells.append((None, "占本周观察", f"{100 * stats['top_observations'] / stats['observations']:.0f}%"))
        _foot(canvas, "本周最活跃", "观察者", cells)


def render_page(dimensions, payload, settings, now, images):
    now = local_now(now)
    canvas = PageCanvas(_palette(settings), family=settings.get("fontFamily") or "Microsoft YaHei")
    canvas.serif, canvas.latin = _FaceCanvas(canvas, "serif"), _FaceCanvas(canvas, "latin")
    pasted = paste_local_wordmark(canvas.image, ASSETS / "title_wordmark.png", (12, 4, 232, 50), align="left")
    title_right = pasted[2] if pasted else 16 + canvas.text((16, 27), "湾区德鲁伊", 30, anchor="lm")
    # The page's sources by their own logos (iNaturalist's observations, eBird's rarities), then
    # the area it covers.
    logo_w = _paste_logo(canvas, "inaturalist", (title_right + 16, 29), HEADER_LOGO_HEIGHTS[0])
    subtitle_left = title_right + 16 + (logo_w + HEADER_LOGO_GAP if logo_w else 0)
    bird_w = _paste_logo(canvas, "ebird", (subtitle_left, 29), HEADER_LOGO_HEIGHTS[1])
    subtitle_left += bird_w + HEADER_LOGO_GAP if bird_w else 0
    subtitle = f"半径 {payload['radius_km']} km · 研究级观察" if logo_w else \
        f"iNaturalist · 半径 {payload['radius_km']} km · 研究级观察"
    date_left = 788 - canvas.text((788, 27), f"{now:%m.%d} 周{WEEKDAYS[now.weekday()]} {now:%H:%M}", 16, anchor="rm")
    canvas.text((subtitle_left, 30), subtitle, 14, "ink", anchor="lm", bold=False, width=date_left - 12 - subtitle_left)
    canvas.rule((12, 52, 788, 52), "ink", 2)
    _hero(canvas, payload, images, now)
    _species(canvas, payload, images)
    _groups(canvas, payload, now)
    _recent(canvas, payload, images, now)
    result = canvas.image
    if tuple(dimensions) != (800, 480):
        result = result.resize(tuple(dimensions), Image.Resampling.LANCZOS)
    return result
