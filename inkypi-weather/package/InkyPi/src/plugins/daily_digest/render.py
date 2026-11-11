"""800 x 480 万象速览 page.

Header: the art-type title, today's date with the colour key of the cards, and a line of classical
poetry (一言) with its work and poet. Body, one colour per kind of card, drawn as a solid band along
its top (titles and small text stay pure ink):

- 天象 (amber), left: the month's Webb or Hubble picture, feathered into the page, its title beneath;
  then the live sun (SDO's 193 Å picture), its corona fading into the page.
- 世界 (cobalt), middle: the newest headlines of DW 中文, 纽约时报中文网 and 少数派, each outlet in a
  block of its own on the page's ground, its brand colour (DW Blue, the Times' black, 少数派 red) a
  thin line round it, quiet doodles behind its headlines.
- 数字 (emerald), right, a 2 x 2 of tiles: the US public debt with its day's change and who holds
  it; California's regular gasoline with its weekly change and six weeks' line; the crypto Fear &
  Greed Index on a half dial with its last seven days. 游戏 (violet): yesterday's Wordle answer.
- 影音 (crimson), the foot: a NetEase chart's top three with their album covers (soaring, new,
  original and hot songs take turns by the hour), and the first three of Douban's films now showing
  and of its American series, as posters with their ratings.

Each card names its source by the official logo. A feed with nothing at hand leaves its card out
and the cards beside it take the room.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timezone
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps

from utils.doodle_backdrop import paint_doodles
from utils.flat_icons import draw_icon
from utils.local_wordmark import load_source_logo
from utils.page_canvas import PageCanvas
from utils.picture_grounds import light_on_dark
from utils.theme_utils import contrast_ratio


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
GRID_BOTTOM = 314
# The foot is tall enough for album covers and posters people can make out on the panel.
MEDIA = (10, 322, 790, 474)
# The 天象 and 数字 columns keep these widths; the headlines take the rest between them.
SKY = (10, BODY_TOP, 204, GRID_BOTTOM)
TILES = (518, BODY_TOP, 790, GRID_BOTTOM)
TILE_GAP = 8
BAND = 5
RADIUS = 6
# The month's picture on the 天象 card, and the sun beneath it.
SPACE_HEIGHT = 90
SPACE_FEATHER = 10
SUN_SIZE = 106
# SDO's disk reaches this share of its picture's half-width; past it the corona fades out, and past
# SUN_CROP nothing is kept (the caption SDO prints along the bottom-left corner starts at 0.93).
SUN_DISK = 0.78
SUN_CROP = 0.9
# By day the corona's colours are lifted this much (the disk keeps its own).
CORONA_LIFT = 1.9
# The foot's pictures: where they start below each panel's heading, the room between them, their
# corners and the rank discs on them.
ART_TOP = 24
ART_GAP = 10
ART_RADIUS = 5
RANK_SIZE = 17
# A poster's rating sits on the picture itself: white figures this size in a thin halo of this ink.
RATING_SIZE = 13
RATING_HALO = 2
RATING_HALO_INK = (18, 26, 32)
# A song's move on its cover, the same way: its arrow green for places gained, red for places lost.
MOVE_SIZE = 12
MOVE_INKS = {"up": (22, 163, 74), "down": (220, 38, 38)}
# A single's release day reads as a day within this many days, else as its year.
RECENT_DAYS = 60
NETEASE_ORDER = (19723756, 3779629, 2884035, 3778678)
# The charts in turn: NetEase's four, then Billboard's Hot 100.
MUSIC_ORDER = (*NETEASE_ORDER, "billboard")
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
    # The date at the right; the line of verse has the rest of the header (the cards wear their
    # kinds' colours on their own bars, with no key to read them by).
    date_w = canvas.text((790, 27), f"{now:%m.%d} 周{WEEKDAYS[now.weekday()]}  {now:%H:%M}", 17, anchor="rm")
    right = 790 - date_w - 16
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
        text_x, middle = left + size - 2, bottom - size / 2 + 2
        canvas.text((text_x, middle - 22), "太阳此刻", 13, anchor="lm")
        canvas.text((text_x, middle - 4), "SDO 193Å", 11, "muted", anchor="lm", bold=False)
        if taken:
            canvas.text((text_x, middle + 12), taken, 11, "muted", anchor="lm", bold=False)
        _paste_logo(canvas, night, "sdo", right - 6, bottom - 18, 24, right=True)
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
# Each outlet's block stands on the page's own ground, its brand colour only a thin line round it: DW
# Blue #002D5A (dw.com's own interface; its 2012 symbol is #012D5B), the Times' black (its nameplate),
# and 少数派 red #D71A1B (sspai.com's --sspai-red, the same in its light and dark themes). Its logo keeps
# its own colours and its headlines are the page's ink.
NEWS_BRANDS = {"dw": (0x00, 0x2D, 0x5A), "nyt": (0x00, 0x00, 0x00), "sspai": (0xD7, 0x1A, 0x1B)}
NEWS_EDGE = 1
# A brand colour this close to the page (DW's navy and the Times' black by night) is lightened for the
# line, its hue kept, the way its logo is.
NEWS_EDGE_CONTRAST = 3.0
# Behind each block's headlines, quiet line doodles of its own world (assets/doodles: DW's broadcasting,
# the Times' newsroom and type, 少数派's devices and apps), a shade off the page in its ink (day, night);
# the type clears them by a thin halo of the page.
NEWS_DOODLE_STRENGTH = (0.12, 0.17)
NEWS_GAP = 5


def _band(canvas, box, kind):
    """A kind's colour as a solid band along the top of a column drawn as several panels."""
    left, top, right, _bottom = box
    canvas.draw.rounded_rectangle((left, top, right, top + BAND), radius=RADIUS, fill=canvas.color(kind),
                                  corners=(True, True, False, False))


def brand_edge(colour, paper):
    """An outlet's brand colour as the line round its block: itself where it shows on ``paper``, else
    its lightness inverted with its hue kept (navy to a pale blue, black to white)."""
    colour = tuple(colour[:3])
    if contrast_ratio(colour, paper) >= NEWS_EDGE_CONTRAST:
        return colour
    return light_on_dark(Image.new("RGB", (1, 1), colour)).getpixel((0, 0))


def _brand_panel(canvas, night, box, colour):
    """An outlet's block: the page's ground with its brand colour as a thin line round it."""
    canvas.draw.rounded_rectangle(box, radius=RADIUS, outline=brand_edge(colour, canvas.color("paper")),
                                  width=NEWS_EDGE)


def _news(canvas, night, box, payload):
    left, top, right, bottom = box
    feeds = [(name, label, ((payload.get("news") or {}).get(name) or {}).get("data") or {})
             for name, label in NEWS_SOURCES]
    feeds = [(name, label, data["items"]) for name, label, data in feeds if data.get("items")]
    if not feeds:
        return False
    _band(canvas, box, "世界")
    text_x, text_right = left + 20, right - 10
    lines = int((bottom - 6 - (top + BAND + 6) - len(feeds) * NEWS_HEAD - (len(feeds) - 1) * NEWS_GAP) // NEWS_LINE)
    shares = [lines // len(feeds) + (1 if index < lines % len(feeds) else 0) for index in range(len(feeds))]
    # Three blocks, a small gap apart, sharing the column's height; each pads its foot with what is left.
    panel_top = top + BAND + 3
    spare = bottom - panel_top - (len(feeds) - 1) * NEWS_GAP - sum(NEWS_HEAD + share * NEWS_LINE for share in shares)
    y = panel_top
    for (name, label, items), share in zip(feeds, shares):
        height = NEWS_HEAD + share * NEWS_LINE + spare // len(feeds)
        _brand_panel(canvas, night, (left, y, right, y + height), NEWS_BRANDS[name])
        paint_doodles(canvas.image, (left + NEWS_EDGE, y + NEWS_EDGE, right - NEWS_EDGE, y + height - NEWS_EDGE),
                      ASSETS / "doodles" / f"{name}.png", canvas.color("ink"), strength=NEWS_DOODLE_STRENGTH[night],
                      radius=RADIUS - NEWS_EDGE, offset=(left * 3 + y, y * 2))
        middle = y + NEWS_HEAD / 2 + 1
        x = left + 10
        x += _paste_logo(canvas, night, name, x, middle, NEWS_LOGO_HEIGHTS.get(name, 16)) + 6
        canvas.text((x, middle), label, 12, anchor="lm", stroke="paper")
        if items[0].get("time"):
            canvas.text((text_right, middle), ago(items[0]["time"], payload.get("_now")), 10, "muted", anchor="rm",
                        bold=False, stroke="paper")
        line_y = y + NEWS_HEAD
        left_lines = share
        for index, item in enumerate(items):
            if left_lines <= 0:
                break
            # The newest headline may take two lines when the share allows; the rest take one.
            allowed = 2 if index == 0 and left_lines >= 3 else 1
            wrapped = canvas.wrap(item["title"], NEWS_SIZE, text_right - text_x, allowed)
            canvas.draw.rectangle((left + 10, line_y + 6, left + 14, line_y + 10), fill=canvas.color("ink"))
            for line in wrapped:
                canvas.text((text_x, line_y), line, NEWS_SIZE, anchor="la", bold=index == 0, stroke="paper")
                line_y += NEWS_LINE
            left_lines -= len(wrapped)
        y += height + NEWS_GAP
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
    _figure(canvas, left + 8, top + 56, f"{total / 1e12:.2f}", "万亿", right - left - 56)
    previous = debt.get("previous_total")
    if previous:
        change = total - previous
        sign = "+" if change >= 0 else "−"
        _change(canvas, left + 8, top + 74, change, f"{sign}{money_text(abs(change))[1:]}", rising_is_good=False)
        canvas.text((right - 8, top + 74), "日变化", 10, "muted", anchor="rm", bold=False)
    public, intragov = debt.get("public"), debt.get("intragov")
    if public and intragov:
        # Who holds it: the public's share and the government's own accounts', as one split bar.
        bar_top, bar_left, bar_right = top + 87, left + 8, right - 8
        split = bar_left + (bar_right - bar_left) * public / (public + intragov)
        canvas.draw.rounded_rectangle((bar_left, bar_top, bar_right, bar_top + 7), radius=3, fill=canvas.color("track"))
        canvas.draw.rounded_rectangle((bar_left, bar_top, split, bar_top + 7), radius=3, fill=canvas.color("数字"))
        canvas.text((bar_left, bar_top + 19), f"公众 {public / 1e12:.1f}", 10, anchor="lm", bold=False)
        canvas.text((bar_right, bar_top + 19), f"政府间 {intragov / 1e12:.1f}", 10, "muted", anchor="rm", bold=False)


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
    _figure(canvas, left + 8, top + 56, f"{price:.3f}", "/加仑", right - left - 64)
    change = price - weeks[-2]["price"]
    tone = "flat" if abs(change) < 1e-9 else ("up" if change < 0 else "down")
    _change(canvas, left + 8, top + 74, change, f"{abs(change):.3f}", rising_is_good=False)
    canvas.text((right - 8, top + 74), "较上周", 10, "muted", anchor="rm", bold=False)
    # The weeks EIA's page lists (six), the latest dot in its change's colour.
    sparkline(canvas, (left + 12, top + 88, right - 14, bottom - 10), [week["price"] for week in weeks], tone)


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
    centre, radius = ((left + right) / 2, top + 76), 40
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
    middle = centre[1] + 13
    x += canvas.text((x, middle), value, 20, anchor="lm") + 5
    canvas.draw.rounded_rectangle((x, middle - 8, x + pill_w, middle + 8), radius=8, fill=colours[band])
    canvas.text((x + pill_w / 2, middle), name, 11, (255, 255, 255) if not night else (0, 0, 0), anchor="mm")
    # The last seven days, oldest at the left, each dot in its band's colour.
    history = list(reversed(days[:7]))
    dots_y = bottom - 10
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
    y = top + 40
    font = canvas.font(round(side * 0.62), True)
    for letter in letters:
        canvas.draw.rectangle((x, y, x + side, y + side), fill=WORDLE_GREEN)
        canvas.draw.text((x + side / 2, y + side / 2 + 1), letter, font=font, fill=(255, 255, 255), anchor="mm")
        x += side + gap
    number = f"第 {wordle['number']} 题 · " if wordle.get("number") else ""
    canvas.text(((left + right) / 2, y + side + 15), f"{number}{short_date(wordle.get('day'))}", 11, anchor="mm",
                bold=False)
    canvas.text(((left + right) / 2, bottom - 12), "今天的题不剧透", 10, "muted", anchor="mm", bold=False)


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

def music_charts(music):
    """The charts at hand (each one's data), in the page's order."""
    music = music or {}
    return [((music.get(chart_id) or {}).get("data") or {}) for chart_id in MUSIC_ORDER
            if ((music.get(chart_id) or {}).get("data") or {}).get("tracks")]


def music_chart(music, now):
    """The NetEase chart the page shows this hour: the soaring, new, original and hot charts take
    turns hour by hour, so each is up a few times a day; one that failed gives its hours to the rest."""
    charts = music_charts(music)
    return charts[now.hour % len(charts)] if charts else None


def _artwork(canvas, picture, box):
    """A cover or poster filling ``box`` with softly rounded corners and no frame; without its picture
    the slot keeps a quiet tile, so the title below still reads as one of the row."""
    left, top, right, bottom = (round(value) for value in box)
    size = (right - left, bottom - top)
    if picture is None:
        canvas.draw.rounded_rectangle((left, top, right - 1, bottom - 1), radius=ART_RADIUS, fill=canvas.color("track"))
        return
    tile = ImageOps.fit(picture.convert("RGB"), size, Image.Resampling.LANCZOS)
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius=ART_RADIUS, fill=255)
    canvas.image.paste(tile, (left, top), mask)


def _rank(canvas, x, y, rank):
    """The item's place: a solid 影音 disc with its number, over the picture's top-left corner."""
    canvas.draw.ellipse((x, y, x + RANK_SIZE, y + RANK_SIZE), fill=canvas.color("影音"), outline=canvas.color("paper"),
                        width=1)
    canvas.draw.text((x + RANK_SIZE / 2, y + RANK_SIZE / 2 + 1), str(rank), font=canvas.font(11, True),
                     fill=(255, 255, 255), anchor="mm")


def _rating_mark(canvas, right, bottom, rating):
    """A rating straight on the poster's foot, no plate: the amber star and the score in white, each
    edged in a thin dark halo so they read on a bright poster as on a dark one."""
    text = f"{rating:.1f}"
    font = canvas.font(RATING_SIZE, True)
    width = canvas.draw.textlength(text, font=font)
    middle = bottom - RATING_SIZE / 2 - 2
    canvas.draw.text((right - 2, middle), text, font=font, fill=(255, 255, 255), anchor="rm",
                     stroke_width=RATING_HALO, stroke_fill=RATING_HALO_INK)
    star = right - 2 - width - 15
    halo = RATING_HALO
    draw_icon(canvas.draw, "star", (star - halo, middle - 7 - halo), 13 + 2 * halo, RATING_HALO_INK, RATING_HALO_INK)
    draw_icon(canvas.draw, "star", (star, middle - 7), 13, canvas.color("天象"), canvas.color("天象"))


def _panel_head(canvas, night, box, mark, height, title, note=""):
    left, top, right, _bottom = box
    middle = top + 11
    x = left + _paste_logo(canvas, night, mark, left, middle, height) + 6
    note_w = canvas.text((right, middle), note, 10, "muted", anchor="rm", bold=False) + 6 if note else 0
    canvas.text((x, middle), title, 13, anchor="lm", width=right - x - note_w)


def _slots(box):
    left, _top, right, _bottom = box
    width = (right - left) / MEDIA_ROWS
    return [(left + index * width, left + (index + 1) * width) for index in range(MEDIA_ROWS)]


def song_move(track, rank):
    """A song's move since the chart's last edition: ("up", "446%") for how far its plays rose on the
    soaring chart, ("up", "3") / ("down", "2") for places gained or lost, ("new", "NEW") for a song
    new to the chart; None when it held its place or the chart keeps no record of the last edition."""
    if "last_rank" not in track:
        return None
    if track.get("reentry"):
        return "new", "RE"
    if track.get("soaring"):
        return "up", f"{track['soaring']}%"
    last = track.get("last_rank")
    if last is None:
        return "new", "NEW"
    if last == rank:
        return None
    return ("up" if last > rank else "down"), str(abs(last - rank))


def song_note(track, now, rank=None):
    """The line under a song's artist: its album when that is not the song itself (a single), else
    when it came out (the day within RECENT_DAYS, else the year), then its length: '《未完成》· 5:20'.
    The parts are joined by " · "; the length is the part a narrow slot drops (_music_panel).
    A Billboard song instead says how long it has been on the chart and, below its peak, the peak."""
    weeks = track.get("weeks")
    if isinstance(weeks, int):
        note = "首周上榜" if weeks == 1 else f"上榜 {weeks} 周"
        peak = track.get("peak")
        return f"{note} · 最高第 {peak}" if isinstance(peak, int) and rank and peak < rank else note
    parts = []
    album, title = str(track.get("album") or "").strip(), str(track.get("title") or "").strip()
    published = str(track.get("published") or "")
    if album and album.casefold() != title.casefold():
        parts.append(f"《{album}》")
    elif len(published) >= 10:
        try:
            day = datetime.fromisoformat(published[:10]).date()
        except ValueError:
            day = None
        if day is not None:
            recent = 0 <= (now.date() - day).days <= RECENT_DAYS if now else False
            parts.append(f"{day:%m.%d} 发行" if recent else f"{day.year} 发行")
    seconds = track.get("seconds")
    if isinstance(seconds, int) and seconds > 0:
        parts.append(f"{seconds // 60}:{seconds % 60:02d}")
    return " · ".join(parts)


def _move_mark(canvas, right, bottom, move):
    """A song's move on its cover's foot, as a poster's rating sits on its poster: the arrow in the
    move's colour and the figure in white, each edged in the thin dark halo; a newcomer gets a small
    solid 影音 tag."""
    kind, text = move
    middle = bottom - MOVE_SIZE / 2 - 2
    font = canvas.font(MOVE_SIZE, True)
    width = canvas.draw.textlength(text, font=font)
    if kind == "new":
        tag = (right - width - 10, middle - 7, right, middle + 7)
        canvas.draw.rounded_rectangle(tag, radius=4, fill=canvas.color("影音"), outline=(255, 255, 255), width=1)
        canvas.draw.text(((tag[0] + tag[2]) / 2, middle + 0.5), text, font=canvas.font(MOVE_SIZE - 2, True),
                         fill=(255, 255, 255), anchor="mm")
        return
    canvas.draw.text((right - 2, middle), text, font=font, fill=(255, 255, 255), anchor="rm",
                     stroke_width=RATING_HALO, stroke_fill=RATING_HALO_INK)
    arrow = right - 2 - width - 13
    halo = RATING_HALO
    draw_icon(canvas.draw, kind, (arrow - halo, middle - 6 - halo), 11 + 2 * halo, RATING_HALO_INK, RATING_HALO_INK)
    draw_icon(canvas.draw, kind, (arrow, middle - 6), 11, MOVE_INKS[kind], MOVE_INKS[kind])


def _music_panel(canvas, night, box, chart, images, zone, now=None):
    left, top, right, bottom = box
    updated = ""
    billboard = chart.get("id") == "billboard"
    if chart.get("updated"):
        try:
            updated = (f"{date.fromisoformat(chart['updated'][:10]):%m.%d} 榜单" if billboard else
                       f"{datetime.fromisoformat(chart['updated']).astimezone(zone):%m.%d} 更新")
        except ValueError:
            updated = ""
    if billboard:
        _panel_head(canvas, night, box, "billboard", 14, chart["name"], updated)
    else:
        _panel_head(canvas, night, box, "netease_music", 18, f"网易云{chart['name']}", updated)
    art_top = top + ART_TOP
    side = min(bottom - art_top - 32, round((right - left) / MEDIA_ROWS) - ART_GAP)
    for index, ((slot_left, slot_right), track) in enumerate(zip(_slots(box), chart["tracks"][:MEDIA_ROWS])):
        centre = (slot_left + slot_right) / 2
        cover = (centre - side / 2, art_top, centre + side / 2, art_top + side)
        _artwork(canvas, images.get(track.get("cover") or ""), cover)
        _rank(canvas, cover[0] - 3, cover[1] - 3, index + 1)
        move = song_move(track, index + 1)
        if move:
            _move_mark(canvas, cover[2] - 2, cover[3] - 2, move)
        width = slot_right - slot_left - 2
        # Title and artist a size up from the posters' captions, a long title stepping down before it
        # is cut short; the note's line meets the captions' line.
        size = 13
        while size > 11 and canvas.width(track["title"], size) > width:
            size -= 1
        canvas.text((centre, art_top + side + 11), track["title"], size, anchor="mm", width=width)
        canvas.text((centre, art_top + side + 26), track["artist"], 11, "muted", anchor="mm", width=width,
                    bold=False)
        note = song_note(track, now, index + 1)
        if " · " in note and canvas.width(note, 10, bold=False) > width:
            note = note.rsplit(" · ", 1)[0]
        if note:
            canvas.text((centre, bottom - 7), note, 10, "muted", anchor="mm", width=width, bold=False)


def _douban_panel(canvas, night, box, items, title, images):
    left, top, right, bottom = box
    _panel_head(canvas, night, box, "douban", 15, title)
    art_top = top + ART_TOP
    height = bottom - art_top - 17
    width = min(round(height * 2 / 3), round((right - left) / MEDIA_ROWS) - ART_GAP)
    for index, ((slot_left, slot_right), item) in enumerate(zip(_slots(box), items[:MEDIA_ROWS])):
        centre = (slot_left + slot_right) / 2
        poster = (centre - width / 2, art_top, centre + width / 2, art_top + height)
        _artwork(canvas, images.get(item.get("poster") or ""), poster)
        _rank(canvas, poster[0] - 3, poster[1] - 3, index + 1)
        if item.get("rating"):
            _rating_mark(canvas, poster[2] - 2, poster[3] - 2, item["rating"])
        else:
            canvas.text((centre, poster[3] - 9), "暂无评分", 10, "muted", anchor="mm", bold=False)
        canvas.text((centre, bottom - 7), item["title"], 12, anchor="mm", width=slot_right - slot_left - 4)


def _media(canvas, night, box, payload, images):
    left, top, right, bottom = box
    panels = []
    chart = music_chart(payload.get("music"), payload["_now"])
    if chart:
        panels.append(lambda inner: _music_panel(canvas, night, inner, chart, images, payload.get("_zone"),
                                                 payload.get("_now")))
    for key, title in (("showing", "正在热映"), ("us_tv", "热门美剧")):
        items = ((payload.get(key) or {}).get("data") or {}).get("items")
        if items:
            panels.append(lambda inner, items=items, title=title: _douban_panel(canvas, night, inner, items, title,
                                                                                images))
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
    has_media = bool(music_charts(payload.get("music"))) or any(
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
        _media(canvas, night, MEDIA, payload, images)
    if not (sky_ready or has_news or has_tiles or has_media):
        canvas.text((400, 260), "暂时取不到今日速览", 18, "muted", anchor="mm")
    result = canvas.image
    if tuple(dimensions) != (800, 480):
        result = result.resize(tuple(dimensions), Image.Resampling.LANCZOS)
    return result
