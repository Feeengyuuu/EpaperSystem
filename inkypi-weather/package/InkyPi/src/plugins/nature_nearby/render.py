"""800 x 480 Nature Nearby page: the newest find, this week's top species, life groups (with
eBird's notable birds under them when a key is set) and a filmstrip of what was just observed
around home."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageOps

from utils.flat_icons import draw_icon
from utils.local_wordmark import paste_header_art, paste_local_wordmark
from utils.page_canvas import PageCanvas

from .sources import ESTABLISHMENT, IUCN_CATEGORIES, group_info, local_now, parse_time


ASSETS = Path(__file__).resolve().parent / "assets"
# The sources' official logos (assets/logos, tools/build_nature_source_logos.py) where the page
# names them; on the night page their dark lettering takes the page's ink.
LOGO_MIN_CONTRAST = 2.0
# The newest find's name: large enough to lead the card (owner: 20% over the old 23 px).
HERO_NAME_SIZE = 28
HERO_PHOTO_HEIGHT = 132
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
HERO_CARD = (12, 58, 300, 330)
SPECIES_CARD = (310, 58, 788, 300)
GROUP_CARD = (12, 336, 300, 468)
RECENT_CARD = (310, 306, 788, 468)
HEADER = 28


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
    left, top, right, _bottom = box
    colour = canvas.color(bar)
    canvas.draw.rounded_rectangle(box, radius=7, outline=colour, width=2)
    canvas.draw.rounded_rectangle((left, top, right, top + 5), radius=7, fill=colour, corners=(True, True, False, False))
    draw_icon(canvas.draw, icon, (left + 10, top + 7), 18, colour, canvas.color("paper"))
    width = canvas.text((left + 34, top + 16), title, 16, anchor="lm")
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


def _hero(canvas, payload, images, now):
    items = payload["recent"]["data"].get("items", [])
    taxon = ((payload.get("taxon") or {}).get("data")) or {}
    _card(canvas, HERO_CARD, "green", "leaf", "最新发现", "研究级", logo="inaturalist")
    left, top, right = HERO_CARD[0] + 8, HERO_CARD[1] + HEADER + 6, HERO_CARD[2] - 8
    if not items:
        canvas.text((left, 150), "观察数据暂不可用", 15, "muted", anchor="lm")
        return
    item = items[0]
    _photo(canvas, images, item["photo"], (left, top, right, top + HERO_PHOTO_HEIGHT), item["group"])
    y = top + HERO_PHOTO_HEIGHT + 18
    chip = _group_chip(canvas, right, y, item["group"], right_align=True)
    canvas.text((left, y), _display_name(item), HERO_NAME_SIZE, anchor="lm", width=right - left - chip - 8)
    canvas.text((left, y + 23), item["scientific"], 12, "muted", anchor="lm", bold=False, width=right - left)
    # The species in more depth: its order and family, its Red List category and standing here.
    lineage = " · ".join(part for part in (taxon.get("order"), taxon.get("family"),
                                           ESTABLISHMENT.get(taxon.get("establishment"), "")) if part)
    chip_w = _iucn_chip(canvas, right, y + 40, taxon["iucn"]) + 6 if taxon.get("iucn") else 0
    if lineage:
        canvas.text((left, y + 40), lineage, 11, anchor="lm", width=right - left - chip_w)
    stamp = parse_time(item["time"])
    where = " · ".join(part for part in (item.get("place"), ago(stamp, now) if stamp else "",
                                         f"距家 {item['distance_km']:.0f} km" if item.get("distance_km") is not None
                                         else "") if part)
    canvas.text((left, y + 57), where, 11, anchor="lm", bold=False, width=right - left)
    reach = _reach_text(taxon["observations"]) if taxon.get("observations") else ""
    reach_w = canvas.text((right, y + 72), reach, 10, "muted", anchor="rm", bold=False) + 8 if reach else 0
    canvas.text((left, y + 72), f"© {item['observer']}" if item.get("observer") else item.get("attribution", ""),
                10, "muted", anchor="lm", bold=False, width=right - left - reach_w)


def _species(canvas, payload, images):
    data = payload["species"]["data"]
    items = data.get("items", [])
    _card(canvas, SPECIES_CARD, "yellow", "trophy", "本周热门物种",
          f"过去 7 天 · 共 {data.get('total', 0):,} 种" if items else "")
    left, right = SPECIES_CARD[0] + 8, SPECIES_CARD[2] - 8
    if not items:
        canvas.text((left, 180), "物种统计暂不可用", 15, "muted", anchor="lm")
        return
    columns, gap = 3, 8
    width = (right - left - gap * (columns - 1)) // columns
    top = SPECIES_CARD[1] + HEADER + 6
    height = (SPECIES_CARD[3] - 8 - top - gap) // 2
    for index, item in enumerate(items[:6]):
        x = left + (index % columns) * (width + gap)
        y = top + (index // columns) * (height + gap)
        side = 70
        _photo(canvas, images, item.get("photo"), (x, y + 6, x + side, y + 6 + side), item["group"])
        badge_fill = canvas.color("yellow" if index == 0 else "ink")
        canvas.draw.ellipse((x - 4, y + 2, x + 14, y + 20), fill=badge_fill, outline=canvas.color("paper"))
        canvas.text((x + 5, y + 11), str(index + 1), 11, (20, 20, 20) if index == 0 else "paper", anchor="mm")
        text_left = x + side + 7
        text_width = x + width - text_left
        # The Chinese name, the scientific name under it (on two lines when the name takes one),
        # and the week's count at the foot, level with the photo's bottom.
        names = canvas.wrap(_display_name(item), 13, text_width, 2)
        for line_index, line in enumerate(names):
            canvas.text((text_left, y + 6 + line_index * 16), line, 13, anchor="la")
        science_top = y + 8 + len(names) * 16
        for line_index, line in enumerate(canvas.wrap(item["scientific"], 10, text_width, 3 - len(names), bold=False)):
            canvas.text((text_left, science_top + line_index * 12), line, 10, "muted", anchor="la", bold=False)
        _label, icon, tone = group_info(item["group"])
        draw_icon(canvas.draw, icon, (text_left, y + 59), 15, canvas.color(tone), canvas.color("paper"))
        canvas.text((text_left + 19, y + 67), f"{item['count']} 次", 18, anchor="lm")


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
    bar_top, x = GROUP_CARD[1] + HEADER + 10, float(left)
    for group in groups:
        _label, _icon, tone = group_info(group["group"])
        width = (right - left) * group["count"] / total
        fill = canvas.color(tone) if tone != "ink" else canvas.color("panel")
        canvas.draw.rectangle((x, bar_top, x + width, bar_top + 16), fill=fill)
        x += width
    canvas.draw.rectangle((left, bar_top, right, bar_top + 16), outline=canvas.color("ink"), width=1)
    column_width = (right - left) / 2
    for index, group in enumerate(groups[:6]):
        label, icon, tone = group_info(group["group"])
        cx = left + (index % 2) * column_width
        cy = bar_top + 35 + (index // 2) * 22
        draw_icon(canvas.draw, icon, (cx, cy - 8), 16, canvas.color(tone if tone != "ink" else "ink"),
                  canvas.color("paper"))
        canvas.text((cx + 22, cy), label, 13, anchor="lm")
        canvas.text((cx + column_width - 10, cy), f"{group['count']} 种", 13, anchor="rm")


def _groups_with_birds(canvas, groups, total, birds, now):
    """The life groups as a thin bar and their top three, then eBird's notable birds this week:
    each the latest report of its species, with where and when."""
    left, right = GROUP_CARD[0] + 10, GROUP_CARD[2] - 10
    bar_top, x = GROUP_CARD[1] + HEADER + 7, float(left)
    for group in groups:
        _label, _icon, tone = group_info(group["group"])
        width = (right - left) * group["count"] / total
        canvas.draw.rectangle((x, bar_top, x + width, bar_top + 9),
                              fill=canvas.color(tone) if tone != "ink" else canvas.color("panel"))
        x += width
    canvas.draw.rectangle((left, bar_top, right, bar_top + 9), outline=canvas.color("ink"), width=1)
    column = (right - left) / 3
    for index, group in enumerate(groups[:3]):
        label, icon, tone = group_info(group["group"])
        cx, cy = left + index * column, bar_top + 22
        draw_icon(canvas.draw, icon, (cx, cy - 7), 14, canvas.color(tone), canvas.color("paper"))
        canvas.text((cx + 17, cy), f"{label} {group['count']}", 11, anchor="lm", width=column - 19)
    y = bar_top + 34
    canvas.rule((left, y, right, y))
    logo_w = _paste_logo(canvas, "ebird", (left, y + 11), 13)
    if not logo_w:
        draw_icon(canvas.draw, "bird", (left, y + 4), 14, canvas.color("blue"), canvas.color("paper"))
    canvas.text((left + (logo_w + 6 if logo_w else 18), y + 11), "罕见鸟讯" if logo_w else "eBird 罕见鸟讯", 11,
                anchor="lm")
    canvas.text((right, y + 11), f"7 天 · {birds.get('species', 0)} 种", 10, "muted", anchor="rm", bold=False)
    for index, bird in enumerate(birds["items"][:2]):
        row = y + 28 + index * 17
        stamp = parse_time(bird.get("time"))
        meta = " · ".join(part for part in (bird.get("place"), ago(stamp, now).replace(" ", "") if stamp else "")
                          if part)
        meta_w = min(150, canvas.width(meta, 10, False)) if meta else 0
        if meta:
            canvas.text((right, row), meta, 10, "muted", anchor="rm", bold=False, width=150)
        canvas.text((left, row), bird.get("name") or bird.get("scientific") or "", 12, anchor="lm",
                    width=right - left - meta_w - 6)


def _recent(canvas, payload, images, now):
    items = payload["recent"]["data"].get("items", [])[1:6]
    stats = payload["stats"]["data"]
    _card(canvas, RECENT_CARD, "red", "clock", "刚刚观察到",
          f"本周 {stats.get('observations', 0):,} 条观察 · {stats.get('observers', 0):,} 位观察者" if stats else "")
    left, right = RECENT_CARD[0] + 8, RECENT_CARD[2] - 8
    if not items:
        canvas.text((left, 400), "暂无新的观察", 14, "muted", anchor="lm")
        return
    gap, count = 8, 5
    width = (right - left - gap * (count - 1)) // count
    top = RECENT_CARD[1] + HEADER + 6
    for index, item in enumerate(items[:count]):
        x = left + index * (width + gap)
        _photo(canvas, images, item["photo"], (x, top, x + width, top + 62), item["group"])
        _label, icon, tone = group_info(item["group"])
        canvas.draw.ellipse((x + 3, top + 3, x + 21, top + 21), fill=canvas.color("paper"))
        draw_icon(canvas.draw, icon, (x + 5, top + 5), 14, canvas.color(tone), canvas.color("paper"))
        canvas.text((x, top + 74), _display_name(item), 12, anchor="lm", width=width)
        stamp = parse_time(item["time"])
        meta = " · ".join(part for part in (ago(stamp, now).replace(" ", "") if stamp else "", item.get("place"))
                          if part)
        canvas.text((x, top + 89), meta, 10, "muted", anchor="lm", bold=False, width=width)
    if stats.get("top_observer"):
        canvas.text((left, RECENT_CARD[3] - 11),
                    f"本周最活跃：@{stats['top_observer']} · {stats.get('top_observations', 0)} 条观察 · "
                    f"{stats.get('top_species', 0)} 种", 11, "muted", anchor="lm", bold=False, width=right - left)


def render_page(dimensions, payload, settings, now, images):
    now = local_now(now)
    canvas = PageCanvas(_palette(settings), family=settings.get("fontFamily") or "Microsoft YaHei")
    pasted = paste_local_wordmark(canvas.image, ASSETS / "title_wordmark.png", (12, 4, 232, 50), align="left")
    title_right = pasted[2] if pasted else 16 + canvas.text((16, 27), "湾区德鲁伊", 30, anchor="lm")
    # The page's source by its own logo, then the area it covers.
    logo_w = _paste_logo(canvas, "inaturalist", (title_right + 14, 30), 20)
    subtitle_left = title_right + 14 + (logo_w + 10 if logo_w else 0)
    subtitle = f"半径 {payload['radius_km']} km · 研究级观察" if logo_w else \
        f"iNaturalist · 半径 {payload['radius_km']} km · 研究级观察"
    subtitle_right = subtitle_left + canvas.text((subtitle_left, 30), subtitle, 14, "muted", anchor="lm", bold=False)
    date_left = 788 - canvas.text((788, 27), f"{now:%m.%d} 周{WEEKDAYS[now.weekday()]} {now:%H:%M}", 16, anchor="rm")
    paste_header_art(canvas.image, ASSETS / "header_art.png", subtitle_right, date_left)
    canvas.rule((12, 52, 788, 52), "ink", 2)
    _hero(canvas, payload, images, now)
    _species(canvas, payload, images)
    _groups(canvas, payload, now)
    _recent(canvas, payload, images, now)
    result = canvas.image
    if tuple(dimensions) != (800, 480):
        result = result.resize(tuple(dimensions), Image.Resampling.LANCZOS)
    return result
