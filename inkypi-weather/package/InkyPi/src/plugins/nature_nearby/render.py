"""800 x 480 Nature Nearby page: the newest find, this week's top species, life groups (with
eBird's notable birds under them when a key is set) and a filmstrip of what was just observed
around home."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageOps

from utils.flat_icons import draw_icon
from utils.local_wordmark import paste_header_art, paste_local_wordmark
from utils.page_canvas import PageCanvas

from .sources import group_info, local_now, parse_time


ASSETS = Path(__file__).resolve().parent / "assets"
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


def _card(canvas, box, bar, icon, title, detail=""):
    """A card in its own colour: a frame, a solid band along the top and the header rule in it,
    then the section icon, title and a right-aligned detail."""
    left, top, right, _bottom = box
    colour = canvas.color(bar)
    canvas.draw.rounded_rectangle(box, radius=7, outline=colour, width=2)
    canvas.draw.rounded_rectangle((left, top, right, top + 5), radius=7, fill=colour, corners=(True, True, False, False))
    draw_icon(canvas.draw, icon, (left + 10, top + 7), 18, colour, canvas.color("paper"))
    width = canvas.text((left + 34, top + 16), title, 16, anchor="lm")
    if detail:
        canvas.text((right - 8, top + 16), detail, 11, "muted", anchor="rm", bold=False,
                    width=right - left - width - 60)
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


def _hero(canvas, payload, images, now):
    items = payload["recent"]["data"].get("items", [])
    _card(canvas, HERO_CARD, "green", "leaf", "最新发现", "iNaturalist · 研究级")
    left, top, right = HERO_CARD[0] + 8, HERO_CARD[1] + HEADER + 6, HERO_CARD[2] - 8
    if not items:
        canvas.text((left, 150), "观察数据暂不可用", 15, "muted", anchor="lm")
        return
    item = items[0]
    _photo(canvas, images, item["photo"], (left, top, right, top + 158), item["group"])
    y = top + 176
    chip = _group_chip(canvas, right, y, item["group"], right_align=True)
    canvas.text((left, y), _display_name(item), 23, anchor="lm", width=right - left - chip - 8)
    canvas.text((left, y + 21), item["scientific"], 12, "muted", anchor="lm", bold=False, width=right - left)
    stamp = parse_time(item["time"])
    where = " · ".join(part for part in (item.get("place"), ago(stamp, now) if stamp else "",
                                         f"距家 {item['distance_km']:.0f} km" if item.get("distance_km") is not None
                                         else "") if part)
    canvas.text((left, y + 39), where, 11, anchor="lm", bold=False, width=right - left)
    canvas.text((left, y + 54), f"© {item['observer']}" if item.get("observer") else item.get("attribution", ""),
                10, "muted", anchor="lm", bold=False, width=right - left)


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
        for line_index, line in enumerate(canvas.wrap(_display_name(item), 13, text_width, 2)):
            canvas.text((text_left, y + 6 + line_index * 16), line, 13, anchor="la")
        _label, icon, tone = group_info(item["group"])
        draw_icon(canvas.draw, icon, (text_left, y + 46), 15, canvas.color(tone), canvas.color("paper"))
        canvas.text((text_left + 19, y + 54), f"{item['count']} 次", 18, anchor="lm")
        canvas.text((text_left, y + 72), item["scientific"], 9, "muted", anchor="lm", bold=False, width=text_width)


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
    draw_icon(canvas.draw, "bird", (left, y + 4), 14, canvas.color("blue"), canvas.color("paper"))
    canvas.text((left + 18, y + 11), "eBird 罕见鸟讯", 11, anchor="lm")
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
    subtitle_right = title_right + 14 + canvas.text((title_right + 14, 30), f"iNaturalist · 半径 {payload['radius_km']} km · 研究级观察",
                                                    14, "muted", anchor="lm", bold=False)
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
