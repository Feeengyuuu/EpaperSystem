"""800 x 480 万象速览 page.

Header: the art-type title, today's date with the colour key of the cards, and a line of classical
poetry (一言) with its work and poet. Body, one colour per kind of card, drawn as a solid band along
its top (titles and small text stay pure ink):

- 天象 (amber), left: the month's Webb or Hubble picture, feathered into the page, its title beneath;
  then the live sun (SDO's 193 Å picture), its corona fading into the page.
- 世界 (cobalt), middle: the newest headlines of DW 中文, 纽约时报中文网 and 少数派.
- 数字 (emerald), right, a 2 x 2 of tiles: the US public debt with its day's change and who holds
  it; California's regular gasoline with its weekly change and six weeks' line; the crypto Fear &
  Greed Index on a half dial with its last seven days. 游戏 (violet): yesterday's Wordle answer.
- 影音 (crimson), the foot: a NetEase chart's top three (soaring, new, original and hot songs take
  turns by the hour), Douban's films now showing and its American series, with their ratings.

Each card names its source by the official logo. A feed with nothing at hand leaves its card out
and the cards beside it take the room.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps

from utils.flat_icons import draw_icon
from utils.local_wordmark import load_source_logo
from utils.page_canvas import PageCanvas
from utils.picture_grounds import light_on_dark


ASSETS = Path(__file__).resolve().parent / "assets"
LOGOS = ASSETS / "logos"
TITLE = ASSETS / "title_wordmark.png"
WEEKDAYS = "一二三四五六日"
DAY = {"paper": (255, 255, 255), "ink": (18, 26, 32), "muted": (58, 69, 77), "rule": (166, 166, 166),
       "track": (226, 229, 233), "up": (22, 150, 96), "down": (214, 62, 72), "flat": (120, 128, 136)}
NIGHT = {"paper": (0, 0, 0), "ink": (237, 241, 234), "muted": (198, 208, 213), "rule": (90, 90, 90),
         "track": (44, 48, 54), "up": (74, 222, 148), "down": (255, 112, 118), "flat": (150, 158, 166)}
# The kinds of card and their colours (day, night): refined hues, brighter by night.
KINDS = {
    "世界": ((37, 84, 199), (110, 150, 255)),
    "影音": ((200, 30, 50), (255, 96, 112)),
    "天象": ((217, 119, 6), (255, 178, 64)),
    "数字": ((5, 150, 105), (52, 211, 153)),
    "游戏": ((124, 58, 237), (172, 140, 255)),
}
KIND_ORDER = ("世界", "影音", "天象", "数字", "游戏")
# NYT Games' own tile colours (Wordle's green), as its icon prints them.
WORDLE_GREEN = (0x6A, 0xAA, 0x64)
# Fear & Greed bands: alternative.me's edges and names, a colour each (day, night).
FEAR_GREED_EDGES = (0, 25, 47, 55, 76, 100)
FEAR_GREED_COLOURS = (((200, 30, 50), (255, 96, 112)), ((234, 88, 12), (255, 150, 70)),
                      ((128, 138, 148), (160, 170, 180)), ((101, 163, 13), (163, 230, 53)),
                      ((5, 150, 105), (52, 211, 153)))
FEAR_GREED_NAMES = ("极度恐惧", "恐惧", "中性", "贪婪", "极度贪婪")
# A logo whose dark lettering would vanish on the night page: "ink" turns its near-black parts to the
# night ink (the default), "invert" lightens its dark colours keeping their hues (a navy wordmark).
NIGHT_LOGOS = {"dw": "invert"}

HEADER_RULE = 56
BODY_TOP = 64
GRID_BOTTOM = 354
MEDIA = (10, 362, 790, 472)
# The 天象 and 数字 columns keep these widths; the headlines take the rest between them.
SKY = (10, BODY_TOP, 204, GRID_BOTTOM)
TILES = (518, BODY_TOP, 790, GRID_BOTTOM)
TILE_GAP = 8
BAND = 5
RADIUS = 6
# The month's picture on the 天象 card, and the sun beneath it.
SPACE_HEIGHT = 112
SPACE_FEATHER = 10
SUN_SIZE = 128
# SDO's disk reaches this share of its picture's half-width; past it the corona fades out, and past
# SUN_CROP nothing is kept (the caption SDO prints along the bottom-left corner starts at 0.93).
SUN_DISK = 0.78
SUN_CROP = 0.9
# By day the corona's colours are lifted this much (the disk keeps its own).
CORONA_LIFT = 1.9
NEWS_SIZE = 13
NEWS_LINE = 18
NEWS_HEAD = 22
MEDIA_ROWS = 3


def _palette(settings):
    theme = (settings or {}).get("_inkypi_theme") or {}
    night = theme.get("mode", (settings or {}).get("themeMode")) == "night"
    palette = dict(NIGHT if night else DAY)
    for kind, (day, dark) in KINDS.items():
        palette[kind] = dark if night else day
    return palette, night


# --- shared pieces -------------------------------------------------------------------------------

def logo(name, height, night, ink):
    """A source's official logo ``height`` px tall in its own colours; by night its dark lettering
    turns light (NIGHT_LOGOS)."""
    art = load_source_logo(LOGOS / f"{name}.png", height, ink if night and NIGHT_LOGOS.get(name) != "invert" else None)
    if art is not None and night and NIGHT_LOGOS.get(name) == "invert":
        art = Image.merge("RGBA", (*light_on_dark(art.convert("RGB")).split(), art.getchannel("A")))
    return art


def _paste_logo(canvas, night, name, x, middle, height, *, right=False):
    art = logo(name, height, night, canvas.color("ink"))
    if art is None:
        return 0
    left = x - art.width if right else x
    canvas.image.paste(art, (round(left), round(middle - art.height / 2)), art)
    return art.width


def _card(canvas, box, kind):
    """A card's frame and its kind's colour as a solid band along its top."""
    left, top, right, bottom = box
    canvas.draw.rounded_rectangle(box, radius=RADIUS, outline=canvas.color("rule"), width=1)
    canvas.draw.rounded_rectangle((left, top, right, top + BAND), radius=RADIUS, fill=canvas.color(kind),
                                  corners=(True, True, False, False))


def _number(canvas, xy, text, size, width, color="ink", *, anchor="ls", min_size=12):
    """A figure whole, its type shrunk rather than ellipsized to fit ``width``."""
    while size > min_size and canvas.width(text, size) > width:
        size -= 1
    return canvas.text(xy, text, size, color, anchor=anchor)


def _change(canvas, x, middle, change, text, *, rising_is_good=True, size=12):
    """An arrow and ``text`` in the change's colour: green when it is good news, red when not."""
    if change is None or abs(change) < 1e-9:
        icon, tone = "flat", "flat"
    else:
        good = (change > 0) == rising_is_good
        icon, tone = ("up" if change > 0 else "down"), ("up" if good else "down")
    draw_icon(canvas.draw, icon, (x, middle - 5), 10, canvas.color(tone), canvas.color("paper"))
    return 13 + canvas.text((x + 13, middle), text, size, tone, anchor="lm")


def short_date(value):
    """"2026-10-29" -> "10.29"."""
    text = str(value or "")
    return f"{text[5:7]}.{text[8:10]}" if len(text) >= 10 else ""


# --- header ----------------------------------------------------------------------------------------

def poem_credit(poem):
    work = f"《{poem['work']}》" if poem.get("work") else ""
    who = poem.get("author") or ""
    return f"—— {who}{work}" if who or work else ""


def _header(canvas, night, payload, now):
    title = load_source_logo(TITLE, 42, canvas.color("ink") if night else None)
    if title is not None:
        canvas.image.paste(title, (10, 7), title)
        x = 10 + title.width + 18
    else:
        x = 14 + canvas.text((14, 28), "万象速览", 30, anchor="lm") + 18
    # The date and the colour key of the cards, at the right.
    date_w = canvas.text((790, 19), f"{now:%m.%d} 周{WEEKDAYS[now.weekday()]}  {now:%H:%M}", 17, anchor="rm")
    key_x = 790
    for kind in reversed(KIND_ORDER):
        key_x -= canvas.width(kind, 10, False)
        canvas.text((key_x, 43), kind, 10, "muted", anchor="lm", bold=False)
        key_x -= 12
        canvas.draw.rounded_rectangle((key_x, 38, key_x + 9, 47), radius=2, fill=canvas.color(kind))
        key_x -= 8
    right = min(790 - date_w, key_x) - 16
    poem = (payload.get("poem") or {}).get("data") or {}
    if poem.get("text"):
        x += _paste_logo(canvas, night, "hitokoto", x, 21, 20) + 8
        canvas.text((x, 21), poem["text"], 17, anchor="lm", width=right - x)
        credit = poem_credit(poem)
        if credit:
            canvas.text((x, 44), credit, 11, "muted", anchor="lm", width=right - x, bold=False)
    canvas.rule((10, HEADER_RULE, 790, HEADER_RULE), "ink", 2)


# --- 天象: the month's picture and the sun ----------------------------------------------------

def feathered(picture, size, feather):
    """``picture`` cut to ``size`` with every edge fading out over ``feather`` px: no hard frame."""
    tile = ImageOps.fit(picture.convert("RGB"), size, Image.Resampling.LANCZOS)
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((feather // 2, feather // 2, size[0] - 1 - feather // 2,
                                            size[1] - 1 - feather // 2), radius=feather, fill=255)
    tile.putalpha(mask.filter(ImageFilter.GaussianBlur(feather / 2.5)))
    return tile


def sun_disc(picture, size, *, night=False):
    """SDO's sun ``size`` px across on a clear ground: the disk whole (its dark coronal holes too),
    the corona fading out with its own light, and nothing past SUN_CROP (the corner caption). On
    the white day page the corona is lifted to a warm glow: its dim brown would read as a smudge."""
    art = ImageOps.fit(picture.convert("RGB"), (size, size), Image.Resampling.LANCZOS)
    centre, half = (size - 1) / 2, size / 2
    radial = Image.new("L", (size, size), 0)
    pixels = radial.load()
    for y in range(size):
        for x in range(size):
            distance = math.hypot(x - centre, y - centre) / half
            if distance <= SUN_DISK:
                pixels[x, y] = 255
            elif distance < SUN_CROP:
                pixels[x, y] = round(255 * (SUN_CROP - distance) / (SUN_CROP - SUN_DISK))
    glow = art.convert("L").point(lambda value: min(255, value * 3))
    disk = radial.point(lambda value: 255 if value == 255 else 0)
    alpha = ImageChops.lighter(ImageChops.multiply(glow, radial), disk)
    if not night:
        art = Image.composite(art, art.point(lambda value: min(255, round(value * CORONA_LIFT))),
                              disk.filter(ImageFilter.GaussianBlur(size / 80)))
    art.putalpha(alpha)
    return art


def _sky(canvas, night, box, payload, images):
    left, top, right, bottom = box
    _card(canvas, box, "天象")
    space = payload.get("space") or {}
    picture = images.get("space") if space else None
    sun = images.get("sun")
    y = top + BAND + 1
    if picture is not None:
        # Alone on the card the picture takes the room the sun would.
        height = SPACE_HEIGHT if sun is not None else bottom - y - 60
        tile = feathered(picture, (right - left - 2, height), SPACE_FEATHER)
        canvas.image.paste(tile, (left + 1, y), tile)
        y += height + 2
        name = space.get("name")
        x = left + 8
        x += _paste_logo(canvas, night, "esa_webb" if name == "webb" else "esa_hubble", x, y + 10, 20) + 6
        x += canvas.text((x, y + 10), f"{'韦布' if name == 'webb' else '哈勃'}本月图", 13, anchor="lm") + 6
        published = short_date(space.get("time"))
        if published:
            canvas.text((x, y + 10), published, 11, "muted", anchor="lm", bold=False)
        y += 22
        for line in canvas.wrap(space.get("title"), 11, right - left - 16, 2, bold=False):
            canvas.text((left + 8, y), line, 11, anchor="la", bold=False)
            y += 15
    if sun is None:
        return
    slot = payload.get("sun_slot")
    taken = (datetime.fromtimestamp(slot, timezone.utc).astimezone(payload.get("_zone")).strftime("%H:%M")
             if slot else "")
    if picture is not None:
        # The sun at the card's foot, its faint outer corona just inside the frame, the words beside it.
        size = SUN_SIZE
        disc = sun_disc(sun, size, night=night)
        canvas.image.paste(disc, (left, bottom - size + 2), disc)
        text_x = left + size - 2
        canvas.text((text_x, bottom - 92), "太阳此刻", 13, anchor="lm")
        canvas.text((text_x, bottom - 74), "SDO 193Å", 11, "muted", anchor="lm", bold=False)
        if taken:
            canvas.text((text_x, bottom - 58), taken, 11, "muted", anchor="lm", bold=False)
        _paste_logo(canvas, night, "sdo", right - 6, bottom - 20, 26, right=True)
        return
    size = min(right - left - 8, bottom - top - BAND - 56)
    disc = sun_disc(sun, size, night=night)
    canvas.image.paste(disc, (round((left + right - size) / 2), top + BAND + 6), disc)
    middle = top + BAND + 6 + size + 18
    label = f"太阳此刻 · SDO 193Å{' · ' + taken if taken else ''}"
    canvas.text(((left + right) / 2, middle), label, 12, anchor="mm", width=right - left - 12)
    _paste_logo(canvas, night, "sdo", (left + right) / 2 - 12, middle + 22, 24)


# --- 世界: headlines ----------------------------------------------------------------------------

NEWS_SOURCES = (("dw", "德国之声"), ("nyt", "中文网"), ("sspai", "少数派"))
# Each source's logo height in its section's heading: the NYT's wide masthead a little lower.
NEWS_LOGO_HEIGHTS = {"dw": 15, "nyt": 15, "sspai": 16}


def _news(canvas, night, box, payload):
    left, top, right, bottom = box
    feeds = [(name, label, ((payload.get("news") or {}).get(name) or {}).get("data") or {})
             for name, label in NEWS_SOURCES]
    feeds = [(name, label, data["items"]) for name, label, data in feeds if data.get("items")]
    if not feeds:
        return False
    _card(canvas, box, "世界")
    text_x, text_right = left + 20, right - 10
    inner_top = top + BAND + 6
    gap = 6
    lines = int((bottom - 6 - inner_top - len(feeds) * NEWS_HEAD - (len(feeds) - 1) * gap) // NEWS_LINE)
    shares = [lines // len(feeds) + (1 if index < lines % len(feeds) else 0) for index in range(len(feeds))]
    y = inner_top
    for (name, label, items), share in zip(feeds, shares):
        middle = y + NEWS_HEAD / 2
        x = left + 10
        x += _paste_logo(canvas, night, name, x, middle, NEWS_LOGO_HEIGHTS.get(name, 16)) + 6
        canvas.text((x, middle), label, 12, anchor="lm")
        if items[0].get("time"):
            canvas.text((text_right, middle), ago(items[0]["time"], payload.get("_now")), 10, "muted", anchor="rm",
                        bold=False)
        y += NEWS_HEAD
        left_lines = share
        for index, item in enumerate(items):
            if left_lines <= 0:
                break
            # The newest headline may take two lines when the share allows; the rest take one.
            allowed = 2 if index == 0 and left_lines >= 3 else 1
            wrapped = canvas.wrap(item["title"], NEWS_SIZE, text_right - text_x, allowed)
            canvas.draw.rectangle((left + 10, y + 6, left + 14, y + 10), fill=canvas.color("世界"))
            for line in wrapped:
                canvas.text((text_x, y), line, NEWS_SIZE, anchor="la", bold=index == 0)
                y += NEWS_LINE
            left_lines -= len(wrapped)
        y += left_lines * NEWS_LINE + gap
    return True


def ago(stamp, now):
    """How long ago an ISO time was, briefly: 刚刚 / 25 分钟前 / 3 小时前 / 2 天前."""
    try:
        then = datetime.fromisoformat(str(stamp))
    except ValueError:
        return ""
    now = now or datetime.now(timezone.utc)
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    minutes = max(0, int((now - then).total_seconds() // 60))
    if minutes < 2:
        return "刚刚"
    if minutes < 60:
        return f"{minutes} 分钟前"
    if minutes < 48 * 60:
        return f"{minutes // 60} 小时前"
    return f"{minutes // 1440} 天前"


# --- 数字 and 游戏: tiles ------------------------------------------------------------------------

def money_text(amount):
    """Dollars as the page prints them: 40593637262883.48 -> "$40.59 万亿"; 42582410521 -> "$425.8 亿"."""
    amount = float(amount)
    if abs(amount) >= 1e12:
        return f"${amount / 1e12:.2f} 万亿"
    if abs(amount) >= 1e8:
        return f"${amount / 1e8:.1f} 亿"
    return f"${amount / 1e4:.0f} 万"


def _tile_head(canvas, night, box, kind, mark, title, height=18, note=""):
    """A tile's frame, its source's logo and title, and a small note (the reading's date) at the right."""
    left, top, right, _bottom = box
    _card(canvas, box, kind)
    middle = top + BAND + 14
    note_w = canvas.text((right - 8, middle), note, 10, "muted", anchor="rm", bold=False) + 6 if note else 0
    x = left + 8 + _paste_logo(canvas, night, mark, left + 8, middle, height) + 5
    canvas.text((x, middle), title, 12, anchor="lm", width=right - 8 - note_w - x)
    return middle


def _figure(canvas, x, baseline, figure, unit, width, *, prefix="$"):
    """A big figure: its currency sign and unit small beside it."""
    x += canvas.text((x, baseline), prefix, 16, anchor="ls") if prefix else 0
    x += _number(canvas, (x, baseline), figure, 26, width) + 3
    canvas.text((x, baseline), unit, 11, anchor="ls", bold=False)


def _debt_tile(canvas, night, box, debt):
    left, top, right, bottom = box
    _tile_head(canvas, night, box, "数字", "treasury", "美国国债", note=short_date(debt.get("date")))
    total = debt["total"]
    _figure(canvas, left + 8, top + 62, f"{total / 1e12:.2f}", "万亿", right - left - 56)
    previous = debt.get("previous_total")
    if previous:
        change = total - previous
        sign = "+" if change >= 0 else "−"
        _change(canvas, left + 8, top + 82, change, f"{sign}{money_text(abs(change))[1:]}", rising_is_good=False)
        canvas.text((right - 8, top + 82), "日变化", 10, "muted", anchor="rm", bold=False)
    public, intragov = debt.get("public"), debt.get("intragov")
    if public and intragov:
        # Who holds it: the public's share and the government's own accounts', as one split bar.
        bar_top, bar_left, bar_right = top + 100, left + 8, right - 8
        split = bar_left + (bar_right - bar_left) * public / (public + intragov)
        canvas.draw.rounded_rectangle((bar_left, bar_top, bar_right, bar_top + 7), radius=3, fill=canvas.color("track"))
        canvas.draw.rounded_rectangle((bar_left, bar_top, split, bar_top + 7), radius=3, fill=canvas.color("数字"))
        canvas.text((bar_left, bar_top + 20), f"公众 {public / 1e12:.1f}", 10, anchor="lm", bold=False)
        canvas.text((bar_right, bar_top + 20), f"政府间 {intragov / 1e12:.1f}", 10, "muted", anchor="rm", bold=False)


def sparkline(canvas, box, values, tone):
    left, top, right, bottom = box
    low, high = min(values), max(values)
    if high - low < 1e-9:
        low, high = low - 0.5, high + 0.5
    points = [(left + (right - left) * index / (len(values) - 1), bottom - (bottom - top) * (value - low) / (high - low))
              for index, value in enumerate(values)]
    canvas.draw.line(points, fill=canvas.color("ink"), width=2, joint="curve")
    for x, y in points[:-1]:
        canvas.draw.ellipse((x - 2, y - 2, x + 2, y + 2), fill=canvas.color("ink"))
    x, y = points[-1]
    canvas.draw.ellipse((x - 4, y - 4, x + 4, y + 4), fill=canvas.color(tone))


def _gas_tile(canvas, night, box, gas):
    left, top, right, bottom = box
    weeks = gas["weeks"]
    _tile_head(canvas, night, box, "数字", "eia", "加州汽油", note=short_date(weeks[-1]["week"]))
    price = weeks[-1]["price"]
    _figure(canvas, left + 8, top + 62, f"{price:.3f}", "/加仑", right - left - 64)
    change = price - weeks[-2]["price"]
    tone = "flat" if abs(change) < 1e-9 else ("up" if change < 0 else "down")
    _change(canvas, left + 8, top + 82, change, f"{abs(change):.3f}", rising_is_good=False)
    canvas.text((right - 8, top + 82), "较上周", 10, "muted", anchor="rm", bold=False)
    # The weeks EIA's page lists (six), the latest dot in its change's colour.
    sparkline(canvas, (left + 12, top + 98, right - 14, bottom - 12), [week["price"] for week in weeks], tone)


def fear_greed_band(value):
    for index, upper in enumerate(FEAR_GREED_EDGES[1:]):
        if value < upper or index == len(FEAR_GREED_EDGES) - 2:
            return index
    return len(FEAR_GREED_NAMES) - 1


def _gauge_point(centre, radius, value):
    angle = math.pi * (1 - value / 100)
    return centre[0] + radius * math.cos(angle), centre[1] - radius * math.sin(angle)


def _fear_greed_tile(canvas, night, box, fear_greed):
    left, top, right, bottom = box
    days = fear_greed["days"]
    today = days[0]
    _tile_head(canvas, night, box, "数字", "alternative_me", "加密恐惧贪婪", 16)
    colours = [colour[1] if night else colour[0] for colour in FEAR_GREED_COLOURS]
    band = fear_greed_band(today["value"])
    centre, radius = ((left + right) / 2, top + 84), 44
    arc = (centre[0] - radius, centre[1] - radius, centre[0] + radius, centre[1] + radius)
    for colour, start, end in zip(colours, FEAR_GREED_EDGES, FEAR_GREED_EDGES[1:]):
        canvas.draw.arc(arc, 180 + 180 * start / 100 + 1, 180 + 180 * end / 100 - 1, fill=colour, width=9)
    tip = _gauge_point(centre, radius - 13, today["value"])
    canvas.draw.line((centre, tip), fill=canvas.color("ink"), width=3)
    canvas.draw.ellipse((centre[0] - 4, centre[1] - 4, centre[0] + 4, centre[1] + 4), fill=canvas.color("ink"))
    value = str(today["value"])
    name = today.get("name") or FEAR_GREED_NAMES[band]
    pill_w = canvas.width(name, 11) + 12
    row_w = canvas.width(value, 20) + 5 + pill_w
    x = centre[0] - row_w / 2
    middle = centre[1] + 15
    x += canvas.text((x, middle), value, 20, anchor="lm") + 5
    canvas.draw.rounded_rectangle((x, middle - 8, x + pill_w, middle + 8), radius=8, fill=colours[band])
    canvas.text((x + pill_w / 2, middle), name, 11, (255, 255, 255) if not night else (0, 0, 0), anchor="mm")
    # The last seven days, oldest at the left, each dot in its band's colour.
    history = list(reversed(days[:7]))
    dots_y = bottom - 12
    span = right - left - 54
    canvas.text((left + 8, dots_y), "7日", 10, "muted", anchor="lm", bold=False)
    for index, day in enumerate(history):
        x = left + 34 + (span * index / max(1, len(history) - 1))
        size = 5 if index == len(history) - 1 else 3.5
        canvas.draw.ellipse((x - size, dots_y - size, x + size, dots_y + size),
                            fill=colours[fear_greed_band(day["value"])])


def _wordle_tile(canvas, night, box, wordle):
    left, top, right, bottom = box
    _tile_head(canvas, night, box, "游戏", "wordle", "Wordle 昨日")
    letters = wordle["solution"]
    gap = 3
    side = min(24, (right - left - 12 - gap * 4) // 5)
    x = left + (right - left - (side * 5 + gap * 4)) / 2
    y = top + 48
    font = canvas.font(round(side * 0.62), True)
    for letter in letters:
        canvas.draw.rectangle((x, y, x + side, y + side), fill=WORDLE_GREEN)
        canvas.draw.text((x + side / 2, y + side / 2 + 1), letter, font=font, fill=(255, 255, 255), anchor="mm")
        x += side + gap
    number = f"第 {wordle['number']} 题 · " if wordle.get("number") else ""
    canvas.text(((left + right) / 2, y + side + 16), f"{number}{short_date(wordle.get('day'))}", 11, anchor="mm",
                bold=False)
    canvas.text(((left + right) / 2, bottom - 14), "今天的题不剧透", 10, "muted", anchor="mm", bold=False)


TILE_DRAWERS = (("debt", _debt_tile), ("gas", _gas_tile), ("fear_greed", _fear_greed_tile), ("wordle", _wordle_tile))


def _tiles(canvas, night, box, payload):
    left, top, right, bottom = box
    present = [(key, draw) for key, draw in TILE_DRAWERS if _tile_ready(key, (payload.get(key) or {}).get("data"))]
    width = (right - left - TILE_GAP) / 2
    height = (bottom - top - TILE_GAP) / 2
    for index, (key, draw) in enumerate(present[:4]):
        column, row = index % 2, index // 2
        x0, y0 = left + column * (width + TILE_GAP), top + row * (height + TILE_GAP)
        draw(canvas, night, (round(x0), round(y0), round(x0 + width), round(y0 + height)), payload[key]["data"])
    return len(present)


def _tile_ready(key, data):
    data = data or {}
    if key == "debt":
        return bool(data.get("total"))
    if key == "gas":
        return len(data.get("weeks") or []) >= 2
    if key == "fear_greed":
        return bool(data.get("days"))
    return bool(data.get("solution"))


# --- 影音: songs, films, series -------------------------------------------------------------------

def _rating(canvas, right, middle, rating):
    if not rating:
        canvas.text((right, middle), "暂无评分", 10, "muted", anchor="rm", bold=False)
        return
    width = canvas.text((right, middle), f"{rating:.1f}", 13, anchor="rm")
    draw_icon(canvas.draw, "star", (right - width - 14, middle - 6), 12, canvas.color("天象"), canvas.color("paper"))


def music_chart(music, now):
    """The NetEase chart the page shows this hour: the soaring, new, original and hot charts take
    turns hour by hour, so each is up a few times a day."""
    charts = music["charts"]
    return charts[now.hour % len(charts)]


def _music_panel(canvas, night, box, music, now):
    left, top, right, _bottom = box
    chart = music_chart(music, now)
    middle = top + 14
    x = left + _paste_logo(canvas, night, "netease_music", left, middle, 18) + 6
    x += canvas.text((x, middle), f"网易云{chart['name']}", 13, anchor="lm") + 6
    if chart.get("update"):
        canvas.text((right, middle), chart["update"], 10, "muted", anchor="rm", bold=False)
    for index, track in enumerate(chart["tracks"][:MEDIA_ROWS]):
        row = top + 38 + index * 23
        canvas.text((left + 6, row), str(index + 1), 14, "影音", anchor="mm")
        title_w = canvas.text((left + 18, row), track["title"], 12, anchor="lm", width=(right - left - 18) * 0.62)
        canvas.text((left + 18 + title_w + 6, row), track["artist"], 11, "muted", anchor="lm", bold=False,
                    width=right - (left + 18 + title_w + 6))


def _douban_panel(canvas, night, box, items, title):
    left, top, right, _bottom = box
    middle = top + 14
    x = left + _paste_logo(canvas, night, "douban", left, middle, 15) + 6
    canvas.text((x, middle), title, 13, anchor="lm")
    for index, item in enumerate(items[:MEDIA_ROWS]):
        row = top + 38 + index * 23
        canvas.text((left + 6, row), str(index + 1), 14, "影音", anchor="mm")
        rating_w = 50
        name_w = canvas.text((left + 18, row), item["title"], 12, anchor="lm", width=right - rating_w - left - 18)
        detail = " ".join(part for part in [item.get("region"), *(item.get("genres") or [])[:1]] if part)
        room = right - rating_w - (left + 18 + name_w + 6)
        if detail and room > 24:
            canvas.text((left + 18 + name_w + 6, row), detail, 10, "muted", anchor="lm", bold=False, width=room)
        _rating(canvas, right, row, item.get("rating"))


def _media(canvas, night, box, payload):
    left, top, right, bottom = box
    panels = []
    music = (payload.get("music") or {}).get("data") or {}
    if music.get("charts"):
        panels.append(lambda inner: _music_panel(canvas, night, inner, music, payload["_now"]))
    for key, title in (("showing", "正在热映"), ("us_tv", "热门美剧")):
        items = ((payload.get(key) or {}).get("data") or {}).get("items")
        if items:
            panels.append(lambda inner, items=items, title=title: _douban_panel(canvas, night, inner, items, title))
    if not panels:
        return False
    _card(canvas, box, "影音")
    width = (right - left) / len(panels)
    for index, panel in enumerate(panels):
        x0 = left + index * width
        if index:
            canvas.rule((round(x0), top + BAND + 8, round(x0), bottom - 8), "track")
        panel((round(x0 + 10), top + BAND + 3, round(x0 + width - 10), bottom - 4))
    return True


# --- page --------------------------------------------------------------------------------------------

def render_page(dimensions, payload, settings, now, images):
    palette, night = _palette(settings)
    canvas = PageCanvas(palette, family=(settings or {}).get("fontFamily") or "Microsoft YaHei")
    payload = {**payload, "_now": now, "_zone": now.tzinfo}
    _header(canvas, night, payload, now)
    sky_ready = images.get("sun") is not None or (images.get("space") is not None and payload.get("space"))
    has_news = any(((payload.get("news") or {}).get(name) or {}).get("data", {}).get("items")
                   for name, _label in NEWS_SOURCES)
    has_tiles = any(_tile_ready(key, (payload.get(key) or {}).get("data")) for key, _draw in TILE_DRAWERS)
    has_media = bool(((payload.get("music") or {}).get("data") or {}).get("charts")) or any(
        ((payload.get(key) or {}).get("data") or {}).get("items") for key in ("showing", "us_tv"))
    grid_bottom = GRID_BOTTOM if has_media else MEDIA[3]
    # The columns present share the width: a missing one gives its room to the middle.
    columns = []
    if sky_ready:
        columns.append(("sky", SKY[2] - SKY[0]))
    if has_news:
        columns.append(("news", None))
    if has_tiles:
        columns.append(("tiles", TILES[2] - TILES[0]))
    fixed = sum(width for _name, width in columns if width)
    flexible = 780 - fixed - TILE_GAP * (len(columns) - 1)
    x = 10
    for name, width in columns:
        width = width or flexible
        if not has_news and name == "tiles":
            width += flexible
        box = (round(x), BODY_TOP, round(x + width), grid_bottom)
        if name == "sky":
            _sky(canvas, night, box, payload, images)
        elif name == "news":
            _news(canvas, night, box, payload)
        else:
            _tiles(canvas, night, box, payload)
        x += width + TILE_GAP
    if has_media:
        _media(canvas, night, MEDIA, payload)
    if not (sky_ready or has_news or has_tiles or has_media):
        canvas.text((400, 260), "暂时取不到今日速览", 18, "muted", anchor="mm")
    result = canvas.image
    if tuple(dimensions) != (800, 480):
        result = result.resize(tuple(dimensions), Image.Resampling.LANCZOS)
    return result
