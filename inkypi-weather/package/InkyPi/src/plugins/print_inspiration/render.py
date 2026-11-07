"""800 x 480 打印灵感 page.

Header: the page's title wordmark, MakerWorld's mark and name, and the date. Left: the most trending
model on MakerWorld leads with its picture and next pictures, its name (in Chinese with the original
under it), summary, category, print profile, maker and its likes, downloads, prints, rating and
comments; the next four stand in cards beneath, picture over a name that runs down the card, its print
profile and counts. Right: the most printed of the trending models and the all-time classics, a
picture beside each name, its counts and its maker.
The solid marks wear Bambu Lab's green; the cards carry a quiet doodle of a maker's workshop.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageOps

from utils.doodle_backdrop import paint_doodles
from utils.flat_icons import draw_icon
from utils.local_wordmark import paste_local_wordmark
from utils.page_canvas import PageCanvas


ASSETS = Path(__file__).resolve().parent / "assets"
TITLE = ASSETS / "title_wordmark.png"
DOODLES = ASSETS / "doodles" / "workshop.png"
DOODLE_STRENGTH = {"day": 0.18, "night": 0.28}
# Bambu Lab's green (MakerWorld's brand) for the solid marks: badges, chips, icons.
BRAND = (0x00, 0xAE, 0x42)
MARK = ASSETS / "makerworld.png"
DAY = {"paper": "#ffffff", "ink": "#121a20", "muted": "#3a454d", "rule": "#b4b4b4", "lead": "#eefaf2",
       "card": "#ffffff", "track": "#eef0f2"}
NIGHT = {"paper": "#000000", "ink": "#edf1ea", "muted": "#c6d0d5", "rule": "#5a5a5a", "lead": "#07190d",
         "card": "#0b0b0b", "track": "#24282d"}
WEEKDAYS = "一二三四五六日"
LOCAL_TZ = ZoneInfo("America/Los_Angeles")
HEADER = (8, 4, 792, 54)
LEAD = (10, 60, 522, 278)
GRID = (10, 286, 522, 472)
CARDS = 4
CARD_GAP = 8
RIGHT = (530, 60, 790, 472)
MAKES_ROWS = 4
CLASSIC_ROWS = 3
ROW_HEIGHT = 52
SECTION_HEIGHT = 22
# A right-column row's picture.
THUMB = (66, 48)
# The lead's picture block: its cover and, at the cover's right, a column of its next pictures.
LEAD_PICTURE = (304, 206)
SUMMARY_LINES = 3
GALLERY_WIDTH = 62
GALLERY_GAP = 4
CARD_PICTURE_RATIO = 0.75
CARD_TITLE_LINE = 15


def _rgb(value):
    if isinstance(value, str) and value.startswith("#") and len(value) == 7:
        return tuple(int(value[index:index + 2], 16) for index in (1, 3, 5))
    return value


def _palette(settings):
    theme = (settings or {}).get("_inkypi_theme") or {}
    palette = NIGHT if theme.get("mode", (settings or {}).get("themeMode")) == "night" else DAY
    return {key: _rgb(value) for key, value in palette.items()}


def _night(canvas):
    return sum(canvas.color("paper")) < 200


def count_text(value):
    """1520 -> "1.5k", 75 -> "75", 23400 -> "23k"."""
    value = int(value or 0)
    if value >= 10000:
        return f"{value // 1000}k"
    return f"{value / 1000:.1f}k" if value >= 1000 else str(value)


def shown_name(model, chinese):
    """The model's name and summary as the page prints them (Chinese when translated) and whether
    they were translated."""
    translated = (chinese or {}).get(model["id"]) or {}
    return (translated.get("title") or model["name"], translated.get("summary") or model["summary"],
            bool(translated.get("title")))


def duration_text(minutes):
    """63 -> "1小时3分", 45 -> "45分", 120 -> "2小时"; "" without a time."""
    minutes = int(minutes or 0)
    if minutes <= 0:
        return ""
    hours, rest = divmod(minutes, 60)
    if not hours:
        return f"{rest}分"
    return f"{hours}小时{rest}分" if rest else f"{hours}小时"


def colour_text(settings):
    """"2 色 AMS" for a profile printing several colours through the AMS, "需 AMS" or "3 色" otherwise."""
    colours, ams = int(settings.get("colors") or 0), bool(settings.get("ams"))
    if colours and ams:
        return f"{colours} 色 AMS"
    return "需 AMS" if ams else (f"{colours} 色" if colours else "")


def print_specs(model):
    """The model's print profile as short labels, most telling first: material, time, filament,
    layer height, colours."""
    settings = model.get("print") or {}
    return [text for text in (settings.get("material") or "", duration_text(settings.get("minutes")),
                              f"{settings['weight']} g" if settings.get("weight") else "",
                              f"层高 {settings['layer']}" if settings.get("layer") else "",
                              colour_text(settings)) if text]


def spec_cells(model):
    """The lead's spec sheet from its default print profile, as (glyph, text): print time, filament
    (material and weight), layer height and colours; only those the profile gives."""
    settings = model.get("print") or {}
    filament = " · ".join(text for text in (settings.get("material") or "",
                                            f"{settings['weight']} g" if settings.get("weight") else "") if text)
    cells = (("clock", duration_text(settings.get("minutes"))), ("spool", filament),
             ("layers", f"层高 {settings['layer']} mm" if settings.get("layer") else ""),
             ("colours", colour_text(settings)))
    return [(kind, text) for kind, text in cells if text]


def file_facts(model):
    """What comes with the model: its print profiles, remixes, licence and whether it prints in several colours."""
    return [text for text in (f"{model['files']} 个打印配置" if model.get("files") else "",
                              f"{model['remixes']} 改版" if model.get("remixes") else "",
                              model.get("license") or "", "多色打印" if model.get("multi_material") else "") if text]


def card_line(model):
    """A card's line of detail: its first three print settings, else its print profiles and licence."""
    specs = print_specs(model)
    if specs:
        return " · ".join(specs[:3])
    return " · ".join(text for text in (f"{model['files']} 个打印配置" if model.get("files") else "",
                                        model.get("license") or "") if text)


def follower_text(value):
    value = int(value or 0)
    return f"{value / 10000:.1f}万" if value >= 10000 else count_text(value)


def maker_line(model):
    """The maker's standing on MakerWorld: "Lv30 · 2.0万关注"."""
    return " · ".join(text for text in (f"Lv{model['maker_level']}" if model.get("maker_level") else "",
                                        f"{follower_text(model['maker_followers'])}关注"
                                        if model.get("maker_followers") else "") if text)


def week_totals(payload):
    """The header's sum of the leading list: "当前热门 7 款 · 合计 6.6k 赞 · 133 次打印 · 21k 下载"
    (MakerWorld's hot ranking, not a weekly one)."""
    week = [model for model in payload.get("week") or [] if model]
    if not week:
        return ""
    likes, makes, downloads = (sum(int(model.get(key) or 0) for model in week)
                               for key in ("likes", "makes", "downloads"))
    return (f"当前热门 {len(week)} 款 · 合计 {count_text(likes)} 赞 · {count_text(makes)} 次打印 · "
            f"{count_text(downloads)} 下载")


def draw_mark(canvas, x, y, size, color=None):
    """MakerWorld's cube-stack mark, ``size`` px tall, its top left at (x, y), in ``color`` (the page's
    ink by default: the mark is one colour, dark on a light page and light on a dark one)."""
    try:
        with Image.open(MARK) as source:
            alpha = source.convert("RGBA").getchannel("A")
    except (OSError, ValueError):
        return
    alpha = alpha.resize((max(1, round(alpha.width * size / alpha.height)), int(size)), Image.Resampling.LANCZOS)
    solid = Image.new("RGBA", alpha.size, (*(color or canvas.color("ink")), 255))
    solid.putalpha(alpha)
    canvas.image.paste(solid, (round(x), round(y)), solid)


def _picture(canvas, picture, box, radius=6):
    """``picture`` cropped to fill ``box`` with rounded corners (a quiet tile when it is missing)."""
    x1, y1, x2, y2 = (int(value) for value in box)
    size = (max(1, x2 - x1), max(1, y2 - y1))
    if picture is None:
        canvas.draw.rounded_rectangle(box, radius=radius, fill=canvas.color("track"))
        draw_mark(canvas, (x1 + x2) / 2 - min(size) * 0.18, (y1 + y2) / 2 - min(size) * 0.2, min(size) * 0.4,
                  canvas.color("rule"))
        return
    fitted = ImageOps.fit(picture.convert("RGB"), size, Image.Resampling.LANCZOS)
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius=radius, fill=255)
    canvas.image.paste(fitted, (x1, y1), mask)


def _badge(canvas, x, y, rank, size=20):
    canvas.draw.ellipse((x, y, x + size, y + size), fill=BRAND)
    canvas.text((x + size / 2, y + size / 2), str(rank), max(9, size - 8), (255, 255, 255), anchor="mm")


def _nozzle(draw, x, y, size, color):
    """A printer's hot end extruding: the block, its tapered nozzle and a drop of filament (a "make")."""
    s = size
    draw.rectangle((x + s * 0.22, y, x + s * 0.78, y + s * 0.38), fill=color)
    draw.polygon([(x + s * 0.3, y + s * 0.38), (x + s * 0.7, y + s * 0.38), (x + s * 0.56, y + s * 0.66),
                  (x + s * 0.44, y + s * 0.66)], fill=color)
    draw.ellipse((x + s * 0.38, y + s * 0.74, x + s * 0.62, y + s * 0.98), fill=color)


def _spool(draw, x, y, s, color, hole):
    """A filament spool seen end on: the reel, its wound filament ring and the hub."""
    draw.ellipse((x, y, x + s, y + s), fill=color)
    draw.ellipse((x + s * 0.2, y + s * 0.2, x + s * 0.8, y + s * 0.8), outline=hole, width=max(1, round(s * 0.08)))
    draw.ellipse((x + s * 0.38, y + s * 0.38, x + s * 0.62, y + s * 0.62), fill=hole)


def _layers(draw, x, y, s, color):
    """Three printed layers stacked, each a little narrower (a layer height)."""
    bar = max(2, round(s * 0.2))
    for index in range(3):
        inset = s * 0.12 * (2 - index)
        top = y + s * 0.08 + index * (bar + s * 0.12)
        draw.rounded_rectangle((x + inset, top, x + s - inset, top + bar), radius=1, fill=color)


def _colours(draw, x, y, s, color):
    """Four filament dots in a square (several colours through the AMS)."""
    dot = s * 0.42
    for column in (0, 1):
        for row in (0, 1):
            left, top = x + column * (s - dot), y + row * (s - dot)
            if (column, row) == (1, 1):
                draw.ellipse((left, top, left + dot, top + dot), outline=color, width=max(1, round(s * 0.1)))
            else:
                draw.ellipse((left, top, left + dot, top + dot), fill=color)


def _icon(canvas, kind, x, y, size):
    if kind == "nozzle":
        _nozzle(canvas.draw, x, y, size, BRAND)
    elif kind == "spool":
        _spool(canvas.draw, x, y, size, BRAND, canvas.color("lead"))
    elif kind == "layers":
        _layers(canvas.draw, x, y, size, BRAND)
    elif kind == "colours":
        _colours(canvas.draw, x, y, size, BRAND)
    else:
        draw_icon(canvas.draw, kind, (x, y), size, BRAND, canvas.color("paper"))


def _stat(canvas, x, middle, kind, value, size=12, icon=12):
    """An icon in the brand green and its count after it; returns the width it took."""
    _icon(canvas, kind, x, middle - icon / 2, icon)
    return icon + 3 + canvas.text((x + icon + 3, middle), value, size, anchor="lm")


def _chip(canvas, x, middle, text, size=10, fill=BRAND, color=(255, 255, 255)):
    width = canvas.width(text, size) + 12
    canvas.draw.rounded_rectangle((x, middle - 8, x + width, middle + 8), radius=8, fill=fill)
    canvas.text((x + width / 2, middle), text, size, color, anchor="mm")
    return width


def _date_text(value):
    """"10月25日" for a model's ISO publication time, else ""."""
    try:
        published = datetime.fromisoformat(str(value)).astimezone(LOCAL_TZ)
    except (TypeError, ValueError):
        return ""
    return f"{published.month}月{published.day}日"


def _pill(canvas, x, middle, text, size=10):
    """An outlined spec label in the brand green; returns its width."""
    width = canvas.width(text, size) + 10
    canvas.draw.rounded_rectangle((x, middle - 8, x + width, middle + 8), radius=8, outline=BRAND, width=1)
    canvas.text((x + width / 2, middle), text, size, anchor="mm")
    return width


def _header(canvas, now, totals=""):
    x1, y1, x2, y2 = HEADER
    placed = paste_local_wordmark(canvas.image, TITLE, (x1 + 2, y1, x1 + 210, y2), align="left")
    left = (placed[2] if placed else x1 + canvas.text((x1, (y1 + y2) / 2), "打印灵感", 26, anchor="lm")) + 14
    draw_mark(canvas, left, y1 + 9, 20)
    canvas.text((left + 24, y1 + 19), "MakerWorld", 16, anchor="lm")
    canvas.text((left, y1 + 40), totals or "MakerWorld 3D 打印模型热门 · 社区点赞与打印数", 10, "muted", anchor="lm",
                bold=False, width=x2 - 120 - left)
    canvas.text((x2, y1 + 19), f"{now:%m.%d} 周{WEEKDAYS[now.weekday()]}", 18, anchor="rm")
    canvas.text((x2, y1 + 40), f"更新 {now:%H:%M}", 10, "muted", anchor="rm", bold=False)
    canvas.rule((x1, y2 + 2, x2, y2 + 2))


def _lead_pictures(canvas, model, images, box):
    """The cover with, when its next pictures are at hand, a column of them at its right."""
    x1, y1, x2, y2 = box
    gallery = [images[url] for url in model.get("gallery") or [] if images.get(url) is not None]
    if not gallery:
        _picture(canvas, images.get(model["image"]), box)
        return
    split = x2 - GALLERY_WIDTH
    _picture(canvas, images.get(model["image"]), (x1, y1, split - GALLERY_GAP, y2))
    height = (y2 - y1 - GALLERY_GAP * 2) / 3
    for index, picture in enumerate(gallery[:3]):
        top = y1 + index * (height + GALLERY_GAP)
        _picture(canvas, picture, (split, round(top), x2, round(top + height)), radius=5)


def _fitted_line(canvas, parts, size, width):
    """``parts`` joined with " · ", as many from the first as fit ``width`` whole."""
    shown = []
    for part in parts:
        if canvas.width(" · ".join([*shown, part]), size, bold=False) > width:
            break
        shown.append(part)
    return " · ".join(shown)


def _spec_sheet(canvas, cells, left, width, first_middle):
    """The print profile as a two-column sheet of glyph and value, row by row."""
    column = width / 2
    for index, (kind, text) in enumerate(cells[:4]):
        x = left + (index % 2) * column
        middle = first_middle + (index // 2) * 19
        _icon(canvas, kind, x, middle - 6, 12)
        canvas.text((x + 16, middle), canvas.fit(text, 11, column - 20), 11, anchor="lm")


def _lead_stats(canvas, model, left, right, middle):
    """Likes, prints, downloads, rating and comments, as many as the row holds, most telling first."""
    stats = [("heart", count_text(model["likes"])), ("nozzle", count_text(model["makes"])),
             ("download", count_text(model["downloads"]))]
    if model.get("ratings"):
        stats.append(("star", f"{model['rating']:.1f}"))
    stats.append(("comment", count_text(model["comments"])))
    x = left
    for kind, value in stats:
        if x + 15 + canvas.width(value, 11) > right:
            break
        x += _stat(canvas, x, middle, kind, value, size=11) + 9


def _lead(canvas, model, images, chinese, doodle_key):
    x1, y1, x2, y2 = LEAD
    canvas.draw.rounded_rectangle(LEAD, radius=8, fill=canvas.color("lead"), outline=BRAND, width=2)
    text_left = x1 + 6 + LEAD_PICTURE[0] + 12
    right = x2 - 10
    width = right - text_left
    paint_doodles(canvas.image, (text_left - 4, y1 + 3, x2 - 3, y2 - 3), DOODLES, BRAND,
                  strength=DOODLE_STRENGTH[doodle_key], grounds=[canvas.color("lead")])
    _lead_pictures(canvas, model, images, (x1 + 6, y1 + 6, x1 + 6 + LEAD_PICTURE[0], y2 - 6))
    _badge(canvas, x1 + 12, y1 + 12, 1, 24)
    name, summary, translated = shown_name(model, chinese)
    # The top row: the list's own tag and the model's category.
    chips_x = text_left + _chip(canvas, text_left, y1 + 18, "编辑精选" if model.get("staff_pick") else "当前最热") + 6
    if model.get("category"):
        _chip(canvas, chips_x, y1 + 18, canvas.fit(model["category"], 10, right - chips_x - 12),
              fill=canvas.color("ink"), color=canvas.color("paper"))
    # The rows from the card's foot up: counts, maker, facts, then the print profile.
    stats_middle, maker_middle, facts_middle = y2 - 14, y2 - 39, y2 - 62
    cells = spec_cells(model)
    rows = (len(cells) + 1) // 2
    sheet_middle = facts_middle - 20 - 19 * (rows - 1) if cells else None
    tags = [] if cells else [f"#{tag}" for tag in model.get("tags") or []]
    content_bottom = (sheet_middle - 10) if cells else (facts_middle - 30 if tags else facts_middle - 10)
    # The name comes first, up to three lines; the summary takes the room left.
    y = y1 + 32
    for line in canvas.wrap(name, 16, width, 3):
        canvas.text((text_left, y), line, 16)
        y += 20
    if translated:
        canvas.text((text_left, y), model["name"], 10, "muted", width=width, bold=False)
        y += 14
    y += 2
    lines = max(0, min(SUMMARY_LINES, int((content_bottom - y) // 14)))
    for line in canvas.wrap(summary, 11, width, lines, bold=False) if lines and summary else []:
        canvas.text((text_left, y), line, 11, "muted", bold=False)
        y += 14
    if cells:
        _spec_sheet(canvas, cells, text_left, width, sheet_middle)
    x = text_left
    for text in tags:
        if x + canvas.width(text, 10) + 10 > right:
            continue
        x += _pill(canvas, x, facts_middle - 20, text) + 4
    # When it came out and what comes with it (its colours already stand in the sheet).
    published = _date_text(model.get("published"))
    facts = [text for text in (f"{published}发布" if published else "",
                               f"{model['files']} 个配置" if model.get("files") else "",
                               model.get("license") or "",
                               "多色打印" if model.get("multi_material") and not any(
                                   kind == "colours" for kind, _text in cells) else "") if text]
    if facts:
        canvas.text((text_left, facts_middle), " · ".join(facts), 10, "muted", width=width, bold=False, anchor="lm")
    # The maker: avatar, name, and their standing under it.
    avatar = images.get(model.get("avatar"))
    maker_x = text_left
    if avatar is not None:
        _picture(canvas, avatar, (maker_x, maker_middle - 12, maker_x + 24, maker_middle + 12), radius=12)
        maker_x += 30
    standing = maker_line(model)
    name_middle = maker_middle - 6 if standing else maker_middle
    canvas.text((maker_x, name_middle), model.get("maker") or "MakerWorld", 12, anchor="lm", width=right - maker_x)
    if standing:
        canvas.text((maker_x, maker_middle + 7), standing, 9, "muted", anchor="lm", bold=False, width=right - maker_x)
    canvas.rule((text_left, stats_middle - 12, right, stats_middle - 12))
    _lead_stats(canvas, model, text_left, right, stats_middle)


def _maker(canvas, x, middle, model, images, right, avatar_size=14, size=10):
    """The maker's round avatar (when it loaded) and name from ``x`` on, centred on ``middle``."""
    avatar = images.get(model.get("avatar"))
    if avatar is not None and x + avatar_size + 20 < right:
        top = round(middle - avatar_size / 2)
        _picture(canvas, avatar, (x, top, x + avatar_size, top + avatar_size), radius=avatar_size // 2)
        x += avatar_size + 4
    if model.get("maker") and x + 16 < right:
        canvas.text((x, middle), model["maker"], size, "muted", anchor="lm", bold=False, width=right - x)


def _card_tags(canvas, x, middle, model, right):
    """A card's category (else its first tag) as an outlined label, and its rating after it."""
    rating = f"{model['rating']:.1f}" if model.get("ratings") else ""
    rating_width = (16 + canvas.width(rating, 10)) if rating else 0
    tags = [tag for tag in model.get("tags") or [] if tag]
    label = model.get("category") or (f"#{tags[0]}" if tags else "")
    room = right - x - rating_width - 10
    if label and room > 24:
        x += _pill(canvas, x, middle, canvas.fit(label, 9, room), size=9) + 6
    if rating and x + rating_width <= right:
        _stat(canvas, x, middle, "star", rating, size=10, icon=10)


def _card(canvas, box, model, rank, images, chinese):
    x1, y1, x2, y2 = box
    canvas.draw.rounded_rectangle(box, radius=7, fill=canvas.color("card"), outline=canvas.color("rule"), width=1)
    picture_bottom = y1 + 4 + int((x2 - x1 - 8) * CARD_PICTURE_RATIO)
    _picture(canvas, images.get(model["image"]), (x1 + 4, y1 + 4, x2 - 4, picture_bottom), radius=5)
    _badge(canvas, x1 + 8, y1 + 8, rank, 18)
    name, _summary, _translated = shown_name(model, chinese)
    # How it prints (else what comes with it) and its counts sit at the foot; the name runs down
    # into the room above them, and its maker (avatar and name) takes the line a short name leaves.
    # A name that needs the whole room keeps it, and the maker's avatar sits on the picture instead.
    width = x2 - x1 - 10
    detail = _fitted_line(canvas, print_specs(model)[:3], 10, width) or card_line(model)
    stats_middle = y2 - 12
    detail_middle = y2 - 30
    y = picture_bottom + 5
    bottom = (detail_middle - 8) if detail else (stats_middle - 9)
    room = max(1, int((bottom - y) // CARD_TITLE_LINE))
    lines = canvas.wrap(name, 12, width, room)
    has_maker = bool(model.get("maker") or images.get(model.get("avatar")))
    maker_line = has_maker and len(lines) < room
    for line in lines:
        canvas.text((x1 + 5, y), line, 12)
        y += CARD_TITLE_LINE
    # A line still to spare (a short name) carries its category, else a tag, and its rating.
    if room - len(lines) - (1 if maker_line else 0) >= 1:
        _card_tags(canvas, x1 + 5, bottom - 7 - (18 if maker_line else 0), model, x2 - 4)
    if maker_line:
        _maker(canvas, x1 + 5, bottom - 7, model, images, x2 - 4)
    elif images.get(model.get("avatar")) is not None:
        size = 22
        left, top = x2 - 8 - size, picture_bottom - 4 - size
        canvas.draw.ellipse((left - 2, top - 2, left + size + 2, top + size + 2), fill=canvas.color("card"))
        _picture(canvas, images[model["avatar"]], (left, top, left + size, top + size), radius=size // 2)
    if detail:
        canvas.text((x1 + 5, detail_middle), detail, 10, "muted", anchor="lm", width=width, bold=False)
    # Likes, prints and downloads: all three share the row, closer set when the counts run long.
    stats = (("heart", count_text(model["likes"])), ("nozzle", count_text(model["makes"])),
             ("download", count_text(model["downloads"])))
    natural = sum(12 + canvas.width(value, 10) for _kind, value in stats)
    gap = max(2, min(7, (width - natural) // 2))
    x = x1 + 5
    for kind, value in stats:
        x += _stat(canvas, x, stats_middle, kind, value, size=10, icon=9) + gap


def _section(canvas, y, title, icon):
    x1, _y1, x2, _y2 = RIGHT
    _icon(canvas, icon, x1 + 2, y + 2, 15)
    canvas.text((x1 + 22, y + 9), title, 14, anchor="lm")
    canvas.rule((x1 + 22 + canvas.width(title, 14) + 8, y + 10, x2, y + 10))
    return y + SECTION_HEIGHT


def _row(canvas, y, model, images, chinese, stat):
    """A picture with its name, counts and maker beside it, the text block centred on the picture."""
    x1, _y1, x2, _y2 = RIGHT
    top_picture = y + (ROW_HEIGHT - THUMB[1]) // 2
    _picture(canvas, images.get(model.get("image_small")) or images.get(model["image"]),
             (x1, top_picture, x1 + THUMB[0], top_picture + THUMB[1]), radius=5)
    name, _summary, _translated = shown_name(model, chinese)
    left = x1 + THUMB[0] + 8
    lines = canvas.wrap(name, 12, x2 - left, 2)
    maker_line_below = len(lines) < 2 and bool(model.get("maker"))
    block = len(lines) * 15 + 15 + (15 if maker_line_below else 0)
    top = top_picture + (THUMB[1] - block) / 2
    for index, line in enumerate(lines):
        canvas.text((left, top + index * 15), line, 12)
    middle = top + len(lines) * 15 + 8
    x = left
    kind, value = stat
    x += _stat(canvas, x, middle, kind, value, size=10, icon=10) + 8
    x += _stat(canvas, x, middle, "heart", count_text(model["likes"]), size=10, icon=10) + 8
    # Who made it, avatar and name: on a line of its own under a one-line name, else after the
    # counts where it fits.
    if maker_line_below:
        _maker(canvas, left, middle + 14, model, images, x2, avatar_size=13, size=9)
    elif model.get("maker") and x + 40 < x2:
        _maker(canvas, x, middle, model, images, x2, avatar_size=12, size=9)


def render_page(size, payload, settings, now, images):
    palette = _palette(settings)
    canvas = PageCanvas(palette, family=str((settings or {}).get("fontFamily") or "Microsoft YaHei"), size=(800, 480))
    doodle_key = "night" if _night(canvas) else "day"
    chinese = payload.get("chinese") or {}
    _header(canvas, now, week_totals(payload))
    week = list(payload.get("week") or [])
    if week:
        _lead(canvas, week[0], images, chinese, doodle_key)
        x1, y1, x2, y2 = GRID
        width = (x2 - x1 - CARD_GAP * (CARDS - 1)) / CARDS
        for index, model in enumerate(week[1:CARDS + 1]):
            left = x1 + index * (width + CARD_GAP)
            _card(canvas, (round(left), y1, round(left + width), y2), model, index + 2, images, chinese)
    else:
        canvas.text(((LEAD[0] + LEAD[2]) / 2, 260), "MakerWorld 暂不可用", 16, "muted", anchor="mm")
    y = RIGHT[1]
    y = _section(canvas, y, "热门里打印最多", "nozzle")
    for model in list(payload.get("makes") or [])[:MAKES_ROWS]:
        _row(canvas, y, model, images, chinese, ("nozzle", f"{count_text(model['makes'])} 次打印"))
        y += ROW_HEIGHT
    y += 4
    y = _section(canvas, y, "历史名作", "trophy")
    for model in list(payload.get("classics") or [])[:CLASSIC_ROWS]:
        if y + ROW_HEIGHT > RIGHT[3] + 2:
            break
        _row(canvas, y, model, images, chinese, ("download", f"{count_text(model['downloads'])} 下载"))
        y += ROW_HEIGHT
    image = canvas.image
    if tuple(size) != image.size:
        image = image.resize(tuple(size), Image.Resampling.LANCZOS)
    return image
