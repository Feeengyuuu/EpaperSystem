"""800 x 480 世界赔率榜 page.

Left: Polymarket's busiest event leads: its likeliest outcomes as bars (each with its portrait or
mark, its moves over the day and the week and its own volume) beside a week's chart of the two
likeliest, then how much has been traded, is held open and stands in the book, when it settles and
how many have commented. The busiest event of each other category sits beneath in a 2 x 2 grid
with its two likeliest outcomes. Right: Kalshi's US economy board, the Fed's next decision as a
split bar over its outcomes with a month of its likeliest's odds and the money on it, then the
year's recession odds and what the markets expect for CPI (with a month of its pivot threshold's
odds), GDP, jobs, gas, the S&P 500 and bitcoin, each with the little chart of the odds it is read from.
"""

from __future__ import annotations

import re
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps

from utils.doodle_backdrop import paint_doodles
from utils.local_wordmark import load_source_logo, paste_local_wordmark
from utils.page_canvas import PageCanvas
from utils.picture_grounds import cut_flat_ground

from . import sources


ASSETS = Path(__file__).resolve().parent / "assets"
DAY = {"paper": "#ffffff", "ink": "#121a20", "muted": "#3a454d", "rule": "#a6a6a6", "track": "#e3e6ea",
       "up": "#0a8f3c", "down": "#d42020", "lead": "#eef3ff"}
NIGHT = {"paper": "#000000", "ink": "#edf1ea", "muted": "#c6d0d5", "rule": "#5a5a5a", "track": "#2a2e33",
         "up": "#6fd38f", "down": "#ff6b6b", "lead": "#0b1430"}
POLYMARKET = (0x2E, 0x5C, 0xFF)
# The lead chart's second line, and its outcome's bar.
RUNNER_UP = (0xF0, 0x8C, 0x00)
KALSHI = (0x09, 0xC2, 0x85)
CATEGORY_COLORS = {"政治": (0x5B, 0x4B, 0xD6), "地缘": (0xD9, 0x48, 0x1E), "经济": (0x0E, 0x8F, 0x7E),
                   "科技": (0x23, 0x6B, 0xE0), "加密": (0xF0, 0x8C, 0x00)}
# The Fed's outcomes on the split bar: cuts in cool tones, holding in grey, hikes in warm ones.
FED_COLORS = {"降息>25": (0x0B, 0x6E, 0x8C), "降息25": (0x2B, 0xA8, 0xC4), "维持": (0x8C, 0x96, 0xA0),
              "加息25": (0xF0, 0x8C, 0x00), "加息>25": (0xD4, 0x20, 0x20)}
WEEKDAYS = "一二三四五六日"
BAR = 6
LEFT = (12, 58, 520, 470)
LEAD = (12, 58, 520, 266)
LEAD_EDGE = 2
BOARD = (528, 58, 788, 470)
LEAD_OUTCOMES = 4
LEAD_CHART_W = 160
# The lead's outcome names (with their portraits) take this much of a row before the bar.
LEAD_NAME_W = 146
# The board's rows after the Fed, in order.
BOARD_ROWS = ("KXRECSSNBER", "KXCPIYOY", "KXGDP", "KXPAYROLLS", "KXAAAGASM", "KXINXY", "KXBTCD")
LADDER_W, LADDER_H = 96, 12
# Each row's mark: the source its market settles on (assets/logos), in an ICON square before its label.
ROW_MARKS = {"KXFEDDECISION": "fed", "KXRECSSNBER": "nber", "KXCPIYOY": "bls", "KXGDP": "bea",
             "KXPAYROLLS": "bls", "KXAAAGASM": "aaa", "KXINXY": "spdji", "KXBTCD": "bitcoin"}
# The part of a wide logo that stands for it at icon size (left, top, right, bottom shares):
# BEA's chart mark, and the "S&P" of S&P Dow Jones Indices' wordmark.
MARK_CROPS = {"bea": (0.0, 0.0, 0.27, 0.72), "spdji": (0.0, 0.0, 0.35, 0.44)}
ICON = 22
# The left side's cards carry quiet doodles of their subject in their colour (assets/doodles): the
# lead the forecasting itself, each tile its category's.
CATEGORY_DOODLES = {"政治": "politics", "地缘": "geopolitics", "经济": "economy", "科技": "tech", "加密": "crypto"}
# Each category's icon (assets/icons, a generated black silhouette by the same names) in its chip and,
# for a market with no picture, in a tile of its colour; "none" marks a None outcome.
CHIP_ICON = 11
# The US states whose flags are bundled (assets/state_flags, by postal code) mark an outcome naming them.
US_STATES = frozenset(path.stem.upper() for path in (ASSETS / "state_flags").glob("*.png"))
# A central bank's decision as Polymarket names its outcomes (its own pictures for them are only the
# event's): holding the rate, or moving it some basis points ("25 bps decrease", "50+ bps increase").
RATE_HOLDS = frozenset(("no change", "hold", "pause", "unchanged", "no cut"))
RATE_MOVE = re.compile(r"(\d+)(\+)?\s*bps\s+(decrease|increase|cut|hike)", re.I)
DOODLE_STRENGTH = 0.24


def _rgb(value):
    if isinstance(value, str) and value.startswith("#") and len(value) == 7:
        return tuple(int(value[index:index + 2], 16) for index in (1, 3, 5))
    return value


def _palette(settings):
    theme = (settings or {}).get("_inkypi_theme") or {}
    palette = NIGHT if theme.get("mode", (settings or {}).get("themeMode")) == "night" else DAY
    return {key: _rgb(value) for key, value in palette.items()}


def money_text(value):
    """5148981 -> "$5.1M", 177642 -> "$178K"."""
    value = float(value or 0)
    if value >= 1e9:
        return f"${value / 1e9:.1f}B"
    if value >= 1e6:
        return f"${value / 1e6:.1f}M"
    if value >= 1e3:
        return f"${value / 1e3:.0f}K"
    return f"${value:.0f}"


def chance_text(chance):
    """0.455 -> "46%", 0.004 -> "<1%", 0.996 -> ">99%"."""
    percent = float(chance or 0) * 100
    if percent < 1:
        return "<1%"
    if percent > 99:
        return ">99%"
    return f"{percent:.0f}%"


def date_text(iso):
    """"2026-11-03" -> "11/3"."""
    try:
        day = datetime.strptime(str(iso)[:10], "%Y-%m-%d")
    except ValueError:
        return ""
    return f"{day.month}/{day.day}"


def board_value(row):
    """A Kalshi row's value as the board prints it, and its small note."""
    kind, when = row.get("kind"), sources.chinese_when(row.get("when"))
    if kind == "yes":
        return chance_text(row.get("chance")), f"发生概率 · {when}"
    if kind == "index":
        low, high = row.get("low"), row.get("high")
        if low is None:
            return "—", ""
        return f"{low:,.0f}–{round(high or low):,.0f}", f"{chance_text(row.get('chance'))} 最可能 · {when}"
    median = row.get("median")
    if median is None:
        return "—", ""
    if kind == "percent":
        return f"≈{median:.1f}%", f"中位预期 · {when}"
    if kind == "jobs":
        return f"≈{median / 1000:+.0f}K", f"中位预期 · {when}"
    if kind == "gas":
        return f"≈${median:.2f}", f"每加仑 · {when}"
    if kind == "btc":
        return f"≈${median:,.0f}", f"中位预期 · {when}"
    return f"{median:g}", when


def _night(canvas):
    return sum(canvas.color("paper")) < 200


@lru_cache(maxsize=8)
def _logo_art(name, height, night):
    return load_source_logo(ASSETS / "logos" / f"{name}.png", height, (237, 241, 234) if night else None)


@lru_cache(maxsize=16)
def _mark_art(name, side, night):
    """A row's settle-source mark fitted inside a ``side`` square (its wide logos cut to their mark)."""
    art = load_source_logo(ASSETS / "logos" / f"{name}.png", 64, (237, 241, 234) if night else None)
    if art is None:
        return None
    crop = MARK_CROPS.get(name)
    if crop:
        art = art.crop((round(art.width * crop[0]), round(art.height * crop[1]),
                        round(art.width * crop[2]), round(art.height * crop[3])))
        box = art.getchannel("A").point(lambda value: 255 if value > 16 else 0).getbbox()
        art = art.crop(box) if box else art
    scale = side / max(art.width, art.height)
    return art.resize((max(1, round(art.width * scale)), max(1, round(art.height * scale))), Image.Resampling.LANCZOS)


def _paste_mark(canvas, series, x, middle):
    """The row's mark centred in an ICON square from ``x``; returns the room it takes."""
    art = _mark_art(ROW_MARKS.get(series, ""), ICON, _night(canvas)) if series in ROW_MARKS else None
    if art is None:
        return 0
    canvas.image.paste(art, (round(x + (ICON - art.width) / 2), round(middle - art.height / 2)), art)
    return ICON + 6


def _paste_logo(canvas, name, x, middle, height):
    art = _logo_art(name, height, _night(canvas))
    if art is None:
        return canvas.text((x, middle), name.title(), 13, anchor="lm")
    canvas.image.paste(art, (round(x), round(middle - art.height / 2)), art)
    return art.width


def _picture(canvas, picture, box, radius=6):
    """``picture`` fitted to ``box`` with rounded corners (a round one when ``radius`` is half its
    side); a quiet tile without one. Returns whether a picture was drawn."""
    x0, y0, x1, y1 = [int(value) for value in box]
    size = (max(1, x1 - x0), max(1, y1 - y0))
    mask = Image.new("L", (size[0] * 4, size[1] * 4), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size[0] * 4 - 1, size[1] * 4 - 1), radius=radius * 4, fill=255)
    mask = mask.resize(size, Image.Resampling.LANCZOS)
    if picture is None:
        canvas.image.paste(canvas.color("track"), (x0, y0, x1, y1), mask)
        return False
    # A transparent picture stands on the paper, not on whatever colour its palette hides there.
    ground = Image.new("RGBA", picture.size, canvas.color("paper"))
    ground.alpha_composite(picture.convert("RGBA"))
    canvas.image.paste(ImageOps.fit(ground.convert("RGB"), size, method=Image.Resampling.LANCZOS,
                                    centering=(0.5, 0.3)), (x0, y0), mask)
    return True


def cutout(picture, side):
    """``picture`` (at most ``side`` * 3 px) without the flat ground its edge shows, cropped to
    what stands on it (RGBA); None when its edge is not one colour, as a photo's is."""
    return cut_flat_ground(picture, side)


def _emblem(canvas, picture, box, radius=6):
    """The lead's picture: a seal or logo cut from its ground and contained in ``box``, else the
    picture framed as ``_picture`` frames it."""
    x0, y0, x1, y1 = [int(value) for value in box]
    art = cutout(picture, max(x1 - x0, y1 - y0)) if picture is not None else None
    if art is None:
        return _picture(canvas, picture, box, radius)
    art = ImageOps.contain(art, (x1 - x0, y1 - y0), Image.Resampling.LANCZOS)
    canvas.image.paste(art, (x0 + (x1 - x0 - art.width) // 2, y0 + (y1 - y0 - art.height) // 2), art)
    return True


@lru_cache(maxsize=32)
def _icon(name, size, colour):
    """assets/icons/<name>.png (a black silhouette) ``size`` px at most, in ``colour``; None without it."""
    path = ASSETS / "icons" / f"{name}.png"
    if not path.is_file():
        return None
    with Image.open(path) as source:
        alpha = source.convert("RGBA").getchannel("A")
    alpha = ImageOps.contain(alpha, (size, size), Image.Resampling.LANCZOS)
    icon = Image.new("RGBA", alpha.size, (*colour[:3], 255))
    icon.putalpha(alpha)
    return icon


def _chip(canvas, x, middle, text, fill, size=10, icon=None):
    """A label on a rounded tag of ``fill``, led by the category's icon when ``icon`` names one."""
    mark = _icon(icon, CHIP_ICON, (255, 255, 255)) if icon else None
    lead = mark.width + 3 if mark is not None else 0
    width = canvas.width(text, size) + 10 + lead
    canvas.draw.rounded_rectangle((x, middle - 8, x + width, middle + 8), radius=4, fill=fill)
    if mark is not None:
        canvas.image.paste(mark, (round(x + 5), round(middle - mark.height / 2)), mark)
    canvas.text((x + lead + (width - lead) / 2, middle), text, size, (255, 255, 255), anchor="mm")
    return width


def _change(canvas, x, middle, change, *, anchor="rm", size=10, prefix=""):
    """A move in percentage points (the day's, or with ``prefix`` "周" the week's): a small
    triangle and the points, after the prefix, ending (or starting) at x. Returns its width."""
    points = round(float(change or 0) * 100)
    lead = canvas.width(prefix, size, bold=False) + 2 if prefix else 0
    if points == 0:
        label = f"{prefix}持平" if prefix else "持平"
        return canvas.text((x, middle), label, size, "muted", anchor=anchor, bold=False)
    tone = "up" if points > 0 else "down"
    label = f"{abs(points)}"
    width = lead + canvas.width(label, size) + 9
    left = x - width if anchor == "rm" else x
    if prefix:
        canvas.text((left, middle), prefix, size, "muted", anchor="lm", bold=False)
        left += lead
    triangle = ((left, middle + 3), (left + 7, middle + 3), (left + 3.5, middle - 4)) if points > 0 else \
        ((left, middle - 3), (left + 7, middle - 3), (left + 3.5, middle + 4))
    canvas.draw.polygon(triangle, fill=canvas.color(tone))
    canvas.text((left + 9, middle), label, size, tone, anchor="lm")
    return width


def _spark(canvas, box, points, color):
    """A month of daily odds drawn small: the line over a quiet fill, today's value as a dot. The
    scale fits the month's range (at least ten points wide, so a quiet month stays flat)."""
    left, top, right, bottom = box
    values = [chance for _when, chance in points]
    if len(values) < 2 or right - left < 12:
        return
    low, high = min(values), max(values)
    if high - low < 0.1:
        middle = (high + low) / 2
        low, high = max(0.0, middle - 0.05), min(1.0, middle + 0.05)
        if high - low < 0.1:
            low, high = (0.0, 0.1) if low <= 0 else (0.9, 1.0)
    start, end = points[0][0], points[-1][0]
    xy = [(left + (right - left - 3) * (when - start) / max(1, end - start),
           bottom - (bottom - top) * (chance - low) / (high - low)) for when, chance in points]
    canvas.draw.polygon([(xy[0][0], bottom), *xy, (xy[-1][0], bottom)], fill=canvas.color("track"))
    canvas.draw.line(xy, fill=color, width=2, joint="curve")
    canvas.draw.ellipse((xy[-1][0] - 2.5, xy[-1][1] - 2.5, xy[-1][0] + 2.5, xy[-1][1] + 2.5), fill=color)


def _fitted(canvas, facts, size, width, *, keep=1, bold=False):
    """The facts joined by " · ", dropping from the end (never the first ``keep``) until they fit."""
    facts = [fact for fact in facts if fact]
    while len(facts) > keep and canvas.width(" · ".join(facts), size, bold) > width:
        facts.pop()
    return " · ".join(facts)


def _bar(canvas, box, share, fill):
    x0, y0, x1, y1 = box
    radius = (y1 - y0) / 2
    canvas.draw.rounded_rectangle(box, radius=radius, fill=canvas.color("track"))
    if share > 0:
        end = x0 + max(y1 - y0, (x1 - x0) * min(1.0, share))
        canvas.draw.rounded_rectangle((x0, y0, end, y1), radius=radius, fill=fill)


# --- Polymarket ------------------------------------------------------------------------------------

def _week_chart(canvas, box, series):
    """A week of the likeliest outcomes' odds: one line each over a light 0–100% grid, today's
    value at each line's end."""
    left, top, right, bottom = box
    canvas.text((left, top + 5), "7 天走势", 10, "muted", anchor="lm")
    plot = (left + 2, top + 14, right - 30, bottom - 12)
    for level in (0.0, 0.5, 1.0):
        y = plot[3] - (plot[3] - plot[1]) * level
        canvas.draw.line((plot[0], y, plot[2], y), fill=canvas.color("track"), width=1)
        canvas.text((right, y), f"{level * 100:.0f}%", 9, "muted", anchor="rm", bold=False)
    start = min((points[0][0] for points, _color in series), default=0)
    end = max((points[-1][0] for points, _color in series), default=1)
    for points, color in series:
        xy = [(plot[0] + (plot[2] - plot[0]) * (t - start) / max(1, end - start),
               plot[3] - (plot[3] - plot[1]) * p) for t, p in points]
        canvas.draw.line(xy, fill=color, width=2, joint="curve")
        canvas.draw.ellipse((xy[-1][0] - 3, xy[-1][1] - 3, xy[-1][0] + 3, xy[-1][1] + 3), fill=color)
    canvas.text((plot[0], bottom - 4), "7 天前", 9, "muted", anchor="lm", bold=False)
    canvas.text((plot[2], bottom - 4), "现在", 9, "muted", anchor="rm", bold=False)


def _lead(canvas, payload, images):
    left, top, right, bottom = LEAD
    # Polymarket's card: framed in its blue under a band of it, as the board is in Kalshi's green.
    canvas.draw.rounded_rectangle(LEAD, radius=6, fill=canvas.color("lead"), outline=POLYMARKET, width=LEAD_EDGE)
    canvas.draw.rounded_rectangle((left, top, right, top + BAR), radius=6, fill=POLYMARKET,
                                  corners=(True, True, False, False))
    paint_doodles(canvas.image, (left + LEAD_EDGE, top + BAR, right - LEAD_EDGE, bottom - LEAD_EDGE),
                  ASSETS / "doodles" / "lead.png", POLYMARKET, strength=DOODLE_STRENGTH, radius=4, offset=(left, top))
    # The contents sit under the band.
    top += BAR - 2
    event = payload.get("lead")
    if not event:
        canvas.text(((left + right) / 2, (top + bottom) / 2), "Polymarket 数据暂不可用", 15, "muted", anchor="mm")
        return
    outcomes = event["outcomes"][:LEAD_OUTCOMES]
    # The event's own picture, else its likeliest outcome's (a 2560 px event banner is too large to fetch).
    picture = images.get(sources.safe_image_url(event.get("image"))) or next(
        (images.get(sources.safe_image_url(outcome.get("image"))) for outcome in outcomes
         if images.get(sources.safe_image_url(outcome.get("image"))) is not None), None)
    picture_box = (left + 10, top + 10, left + 70, top + 70)
    _emblem(canvas, picture, picture_box, radius=8)
    x = picture_box[2] + 10
    color = CATEGORY_COLORS.get(event["category"], POLYMARKET)
    _chip(canvas, x, top + 18, f"今日最热 · {event['category']}", color, icon=CATEGORY_DOODLES.get(event["category"]))
    canvas.text((right - 10, top + 18), f"24h 成交 {money_text(event['volume_24h'])}", 11, "ink", anchor="rm")
    for size in (17, 15, 14):
        lines = canvas.wrap(event["title"], size, right - 12 - x, 2)
        if not lines[-1].endswith("…"):
            break
    y = top + 30
    for line in lines:
        canvas.text((x, y), line, size)
        y += size + 3
    # The money behind it (traded, held open, standing in the order book), then when it settles;
    # the comment count gives way first when the line is full.
    facts = [f"累计成交 {money_text(event['volume'])}",
             f"持仓 {money_text(event['open_interest'])}" if event.get("open_interest") else "",
             f"流动性 {money_text(event['liquidity'])}" if event.get("liquidity") else "",
             f"截止 {date_text(event['ends'])}" if event.get("ends") else "",
             f"{event['comments']:,} 条评论" if event.get("comments") else ""]
    canvas.text((x, max(y + 2, top + 66)), _fitted(canvas, facts, 10, right - 12 - x), 10, "muted", bold=False,
                width=right - 12 - x)
    # The outcomes on the left, a week of the two likeliest's odds on the right.
    rows_top, rows_bottom = top + 84, bottom - 8
    chart_left = right - 10 - LEAD_CHART_W
    histories = payload.get("histories") or {}
    series = [(histories[outcome["token"]], POLYMARKET if index == 0 else RUNNER_UP)
              for index, outcome in enumerate(outcomes[:2]) if outcome.get("token") in histories]
    rows_right = chart_left - 12 if series else right - 10
    if series:
        _week_chart(canvas, (chart_left, rows_top, right - 10, rows_bottom), series)
    row_h = (rows_bottom - rows_top) / max(1, len(outcomes))
    weekly = any(outcome.get("week") is not None for outcome in outcomes)
    # Each outcome's portrait, else its mark (the flags of the states it names, a ✓ / ✗, a None
    # sign...), centred in a column as wide as the widest, the names lined up after it.
    side = int(min(row_h - 6, 22))
    rooms = [_mark_room(*_mark_source(event, outcome, images), side) for outcome in outcomes]
    column = max(rooms, default=0)
    x = left + 12 + column
    name_w = LEAD_NAME_W - column
    for index, outcome in enumerate(outcomes):
        middle = rows_top + row_h * (index + 0.5)
        tint = POLYMARKET if index == 0 else (RUNNER_UP if index == 1 and series else canvas.color("muted"))
        _outcome_mark(canvas, event, outcome, images, left + 12 + (column - rooms[index]) // 2, middle, side, tint)
        canvas.text((x, middle - 5), outcome["name"], 12, anchor="lm", width=name_w)
        if outcome.get("volume_24h"):
            canvas.text((x, middle + 8), f"24h {money_text(outcome['volume_24h'])}", 9, "muted", anchor="lm",
                        bold=False, width=name_w)
        track = (left + 18 + LEAD_NAME_W, middle - (4 if index == 0 else 3), rows_right - 84,
                 middle + (4 if index == 0 else 3))
        _bar(canvas, track, outcome["chance"], tint)
        canvas.text((rows_right - 40, middle), chance_text(outcome["chance"]), 16 if index == 0 else 13,
                    POLYMARKET if index == 0 else "ink", anchor="rm")
        # The day's move beside the name, the week's under it beside the 24h volume (a market
        # younger than a week has none).
        week = outcome.get("week")
        _change(canvas, rows_right, middle - (5 if weekly else 0), outcome["change"])
        if week is not None:
            _change(canvas, rows_right, middle + 8, week, size=9, prefix="周")


MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December")


def _outcome_kind(name):
    """How an outcome with no picture of its own is marked: "yes" / "no", "none" for a None outcome,
    ("rate", move, steps) for a central bank's decision ("No change" holds, "25 bps decrease" is one
    step down, "50+ bps increase" two up), ("states", codes) for one naming US states ("IA, KS"),
    ("date", day) for a "October 28" deadline, "level" for a price level ("↑ 82,500"), else None."""
    text = str(name or "").strip()
    if text.lower() in ("yes", "no"):
        return text.lower()
    if text.lower() in ("none", "neither", "no one", "nobody"):
        return "none"
    if text.lower() in RATE_HOLDS:
        return ("rate", "hold", 0)
    rate = RATE_MOVE.fullmatch(text)
    if rate:
        steps = 2 if rate.group(2) or int(rate.group(1)) > 25 else 1
        return ("rate", "cut" if rate.group(3).lower() in ("decrease", "cut") else "hike", steps)
    states = [part.strip().upper() for part in text.replace("&", ",").replace("/", ",").split(",")]
    if states and all(part in US_STATES for part in states):
        return ("states", tuple(states))
    words = text.split()
    if len(words) == 2 and words[0] in MONTHS and words[1].isdigit():
        return ("date", int(words[1]))
    if text[:1] in ("↑", "↓"):
        return "level"
    return None


def _mark_source(event, outcome, images):
    """What marks an outcome: its kind (see _outcome_kind) and its own picture, or for a price
    level the event's (the coin); the picture is None without one."""
    kind = _outcome_kind(outcome["name"])
    picture = images.get(sources.safe_image_url(outcome.get("image")))
    if picture is None and kind == "level":
        picture = images.get(sources.safe_image_url(event.get("image")))
    return kind, picture


def _mark_room(kind, picture, side):
    """The room an outcome's mark ``side`` px tall takes before its name, 0 without one."""
    if picture is None and isinstance(kind, tuple) and kind[0] == "states":
        flags = _flag_images(kind[1], side)
        return (flags[0].width // 2 + 2 if len(flags) > 1 else 0) + flags[-1].width + 6
    return side + 5 if picture is not None or kind in ("yes", "no", "none") or isinstance(kind, tuple) else 0


def _outcome_mark(canvas, event, outcome, images, x, middle, side, color):
    """The outcome's mark before its name: its own picture (a party's or company's logo), a ✓ / ✗
    for Yes / No, a ⌀ for None, the flags of the states it names, a small calendar page for a date,
    the event's own picture (the coin) for a price level. Returns the room it took, 0 without one."""
    top = int(middle - side / 2)
    kind, picture = _mark_source(event, outcome, images)
    room = _mark_room(kind, picture, side)
    if picture is not None:
        _picture(canvas, picture, (x, top, x + side, top + side), radius=side // 2)
    elif kind in ("yes", "no"):
        canvas.draw.ellipse((x, top, x + side, top + side), fill=canvas.color("up" if kind == "yes" else "down"))
        ink, inset, width = canvas.color("paper"), side * 0.28, max(2, side // 7)
        if kind == "yes":
            canvas.draw.line(((x + inset, top + side * 0.52), (x + side * 0.43, top + side - inset),
                              (x + side - inset, top + inset * 1.1)), fill=ink, width=width, joint="curve")
        else:
            canvas.draw.line((x + inset, top + inset, x + side - inset, top + side - inset), fill=ink, width=width)
            canvas.draw.line((x + inset, top + side - inset, x + side - inset, top + inset), fill=ink, width=width)
    elif kind == "none":
        icon = _icon("none", side, canvas.color("muted"))
        if icon is not None:
            canvas.image.paste(icon, (x + (side - icon.width) // 2, top + (side - icon.height) // 2), icon)
    elif isinstance(kind, tuple) and kind[0] == "states":
        _state_flags(canvas, kind[1], x, middle, side)
    elif isinstance(kind, tuple) and kind[0] == "rate":
        _rate_mark(canvas, kind[1], kind[2], x, top, side)
    elif isinstance(kind, tuple):
        band = max(4, round(side * 0.3))
        canvas.draw.rounded_rectangle((x, top, x + side, top + side), radius=3, fill=(255, 255, 255),
                                      outline=color, width=1)
        canvas.draw.rounded_rectangle((x, top, x + side, top + band), radius=3, fill=color)
        canvas.draw.rectangle((x, top + band - 2, x + side, top + band), fill=color)
        canvas.text((x + side / 2 + 0.5, top + band + (side - band) / 2), str(kind[1]), max(7, side - band - 3),
                    (0x12, 0x1A, 0x20), anchor="mm")
    return room


def _rate_mark(canvas, move, steps, x, top, side):
    """A rate decision's mark, in the Fed bar's colours on the Kalshi board: a disc, cool for a cut,
    grey for a hold, warm for a hike, with a white chevron per step (two for 50 bps or more) pointing
    the way the rate goes, or an equals sign for a hold."""
    key = {"hold": "维持", "cut": "降息25" if steps < 2 else "降息>25", "hike": "加息25" if steps < 2 else "加息>25"}[move]
    canvas.draw.ellipse((x, top, x + side, top + side), fill=FED_COLORS[key])
    ink, width = (255, 255, 255), max(2, side // 8)
    centre_x, centre_y = x + side / 2, top + side / 2
    if move == "hold":
        for offset in (-side * 0.12, side * 0.12):
            canvas.draw.line((x + side * 0.28, centre_y + offset, x + side * 0.72, centre_y + offset), fill=ink,
                             width=width)
        return
    # The chevron's tip points down for a cut, up for a hike; one sits in the middle, two a gap apart.
    tip = 1 if move == "cut" else -1
    half, rise, gap = side * 0.24, side * 0.12, side * 0.22
    for index in range(steps):
        y = centre_y + (index - (steps - 1) / 2) * gap
        canvas.draw.line(((centre_x - half, y - rise * tip), (centre_x, y + rise * tip), (centre_x + half, y - rise * tip)),
                         fill=ink, width=width, joint="curve")


@lru_cache(maxsize=32)
def _flag_images(states, side):
    """The flags of the US states an outcome names (assets/state_flags), the first two: each ``side``
    px tall at most, a little shorter when two share the room."""
    flags = []
    for code in states[:2]:
        with Image.open(ASSETS / "state_flags" / f"{code.lower()}.png") as source:
            flags.append(ImageOps.contain(source.convert("RGB"), (side * 2, side - (4 if len(states) > 1 else 0)),
                                          Image.Resampling.LANCZOS))
    return tuple(flags)


def _state_flags(canvas, states, x, middle, side):
    """The states' flags (_flag_images) edged with a hairline, a second one set over the first's
    right half, a little lower."""
    flags = _flag_images(states, side)
    for index, flag in enumerate(flags):
        left, top = x + index * (flags[0].width // 2 + 2), round(middle - side / 2) + index * 4
        canvas.image.paste(flag, (left, top))
        canvas.draw.rectangle((left, top, left + flag.width - 1, top + flag.height - 1), outline=canvas.color("rule"))


def _tile(canvas, box, event, images):
    left, top, right, bottom = box
    color = CATEGORY_COLORS.get(event["category"], POLYMARKET)
    canvas.draw.rounded_rectangle(box, radius=6, outline=canvas.color("rule"), width=1)
    doodles = CATEGORY_DOODLES.get(event["category"])
    if doodles:
        paint_doodles(canvas.image, (left + 5, top + 1, right - 1, bottom - 1), ASSETS / "doodles" / f"{doodles}.png",
                      color, strength=DOODLE_STRENGTH, radius=5, offset=(left, top))
    canvas.draw.rounded_rectangle((left, top, left + 4, bottom), radius=2, fill=color)
    picture_box = (left + 10, top + 8, left + 44, top + 42)
    picture = images.get(sources.safe_image_url(event.get("image")))
    if picture is not None:
        _picture(canvas, picture, picture_box, radius=5)
    else:
        # No picture: the category's icon in its colour, standing on the page without a ground (else
        # its first character on a quiet badge of that colour).
        middle = ((picture_box[0] + picture_box[2]) / 2, (picture_box[1] + picture_box[3]) / 2)
        icon = _icon(CATEGORY_DOODLES.get(event["category"], ""), picture_box[2] - picture_box[0] - 2, color)
        if icon is not None:
            canvas.image.paste(icon, (round(middle[0] - icon.width / 2), round(middle[1] - icon.height / 2)), icon)
        else:
            canvas.draw.rounded_rectangle(picture_box, radius=5, fill=color)
            canvas.text(middle, event["category"][0], 18, (255, 255, 255), anchor="mm")
    x = picture_box[2] + 8
    used = _chip(canvas, x, top + 16, event["category"], color, size=9, icon=CATEGORY_DOODLES.get(event["category"]))
    room = right - 8 - x - used - 6
    meta = _fitted(canvas, [f"24h {money_text(event['volume_24h'])}",
                            f"持仓 {money_text(event['open_interest'])}" if event.get("open_interest") else "",
                            f"截止 {date_text(event['ends'])}" if event.get("ends") else ""], 9, room)
    canvas.text((x + used + 6, top + 16), meta, 9, "muted", anchor="lm", bold=False, width=room)
    for number, line in enumerate(canvas.wrap(event["title"], 12, right - 8 - x, 2)):
        canvas.text((x, top + 26 + number * 15), line, 12)
    outcomes = event["outcomes"]
    lead = outcomes[0]
    middle = bottom - 27
    mark = _outcome_mark(canvas, event, lead, images, left + 10, middle, 17, color)
    canvas.text((left + 10 + mark, middle), lead["name"], 12, anchor="lm", width=right - left - 104 - mark)
    canvas.text((right - 40, middle), chance_text(lead["chance"]), 17, color, anchor="rm")
    _change(canvas, right - 8, middle, lead["change"])
    if len(outcomes) > 1:
        second = outcomes[1]
        middle = bottom - 10
        # The second outcome's mark sits under the first's, its name lined up with the first name.
        mark = _outcome_mark(canvas, event, second, images, left + 11, middle, 14, color)
        canvas.text((left + 10 + (mark + 3 if mark else 0), middle), second["name"], 10, "muted", anchor="lm", bold=False,
                    width=right - left - 104 - mark)
        canvas.text((right - 40, middle), chance_text(second["chance"]), 11, "muted", anchor="rm")
        _change(canvas, right - 8, middle, second["change"], size=9)


def _tiles(canvas, payload, images):
    tiles = (payload.get("tiles") or [])[:4]
    left, top, right, bottom = LEFT[0], LEAD[3] + 8, LEFT[2], LEFT[3]
    width, height = (right - left - 8) / 2, (bottom - top - 8) / 2
    for index, event in enumerate(tiles):
        x0 = left + (index % 2) * (width + 8)
        y0 = top + (index // 2) * (height + 8)
        _tile(canvas, (round(x0), round(y0), round(x0 + width), round(y0 + height)), event, images)


# --- Kalshi ----------------------------------------------------------------------------------------

def _fed(canvas, row, top, left, right, spark=None):
    """The Fed's next decision: a split bar over its outcomes, the two likeliest named under it,
    then the likeliest's month of odds (``spark``) and the money traded and held on the meeting."""
    when = date_text(row.get("date")) or sources.chinese_when(row.get("when"))
    taken = _paste_mark(canvas, "KXFEDDECISION", left, top + 8)
    canvas.text((left + taken, top + 8), "美联储议息", 13, anchor="lm")
    canvas.text((right, top + 8), f"下次会议 {when}", 10, "muted", anchor="rm", bold=False)
    outcomes = [outcome for outcome in row.get("outcomes") or [] if outcome["chance"] > 0]
    total = sum(outcome["chance"] for outcome in outcomes) or 1
    x, bar_top, bar_bottom = left, top + 20, top + 34
    mask = Image.new("L", (right - left, bar_bottom - bar_top), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, right - left - 1, bar_bottom - bar_top - 1), radius=6, fill=255)
    strip = Image.new("RGB", mask.size, canvas.color("track"))
    draw = ImageDraw.Draw(strip)
    for outcome in outcomes:
        width = (right - left) * outcome["chance"] / total
        draw.rectangle((x - left, 0, x - left + width, mask.size[1]), fill=FED_COLORS[outcome["name"]])
        x += width
    canvas.image.paste(strip, (left, bar_top), mask)
    ranked = sorted(outcomes, key=lambda outcome: outcome["chance"], reverse=True)[:2]
    x = left
    for outcome in ranked:
        canvas.draw.rounded_rectangle((x, top + 41, x + 9, top + 50), radius=2, fill=FED_COLORS[outcome["name"]])
        x += 13
        x += canvas.text((x, top + 46), f"{outcome['name']} {chance_text(outcome['chance'])}", 12, anchor="lm") + 4
        x += _change(canvas, x, top + 46, outcome["change"], anchor="lm") + 10
    money = _fitted(canvas, [f"24h {money_text(row['volume_24h'])}" if row.get("volume_24h") else "",
                             f"持仓 {money_text(row['open_interest'])}" if row.get("open_interest") else ""], 10,
                    (right - left) // 2, keep=0)
    points = (spark or {}).get("points") or []
    if not money and len(points) < 2:
        return top + 58
    money_w = canvas.text((right, top + 65), money, 10, "muted", anchor="rm", bold=False) if money else 0
    if len(points) >= 2:
        label_w = canvas.text((left, top + 65), "30天", 9, "muted", anchor="lm", bold=False) + 4
        color = FED_COLORS.get(spark.get("name"), KALSHI)
        _spark(canvas, (left + label_w, top + 58, right - money_w - 10, top + 71), points, color)
    return top + 78


def _ladder(canvas, box, row):
    """The odds a row is read from, drawn small: for thresholds the chance of "above x" falling
    across the strikes (the median marked), for ranges each range's chance as a bar (the likeliest
    filled), for a yes/no market its share of a bar."""
    left, top, right, bottom = box
    kind = row.get("kind")
    if kind == "yes":
        _bar(canvas, (left, top + 3, right, bottom - 3), row.get("chance") or 0, KALSHI)
        return
    if row.get("ranges"):
        ranges = [entry for entry in row["ranges"] if entry[1] is not None]
        most = max((entry[1] for entry in ranges), default=1) or 1
        step = (right - left) / max(1, len(ranges))
        for index, (low, chance) in enumerate(ranges):
            height = max(1, (bottom - top) * chance / most)
            x0 = left + index * step
            best = low == row.get("low")
            canvas.draw.rectangle((x0, bottom - height, x0 + max(1, step - 1), bottom),
                                  fill=KALSHI if best else canvas.color("rule"))
        return
    ladder = row.get("ladder") or []
    if len(ladder) < 2:
        return
    low, high = ladder[0][0], ladder[-1][0]
    span = max(1e-9, high - low)
    points = [(left + (right - left) * (strike - low) / span, bottom - (bottom - top) * chance)
              for strike, chance in ladder]
    canvas.draw.polygon([(left, bottom), *points, (right, bottom)], fill=canvas.color("track"))
    canvas.draw.line(points, fill=KALSHI, width=1)
    median = row.get("median")
    if median is not None and low <= median <= high:
        x = left + (right - left) * (median - low) / span
        canvas.draw.line((x, top, x, bottom), fill=canvas.color("ink"), width=1)


def _pivot_spark(canvas, span, spark):
    """A threshold row's month of odds in the room between its label and value (``span`` is left,
    middle, right): the chance of "above <strike>" the market is least sure of, named before it."""
    left, middle, right = span
    strike = spark.get("strike")
    label = f">{strike:g}%" if isinstance(strike, (int, float)) else ""
    label_w = canvas.width(label, 9, bold=False) + 3 if label else 0
    if right - left - label_w < 30:
        return
    if label:
        canvas.text((left, middle), label, 9, "muted", anchor="lm", bold=False)
    _spark(canvas, (left + label_w, middle - 6, min(right, left + label_w + 64), middle + 6), spark["points"], KALSHI)


def _board(canvas, payload):
    left, top, right, bottom = BOARD
    board = payload.get("board") or {}
    canvas.draw.rounded_rectangle(BOARD, radius=6, outline=canvas.color("rule"), width=1)
    canvas.draw.rounded_rectangle((left, top, right, top + BAR), radius=6, fill=KALSHI, corners=(True, True, False, False))
    paint_doodles(canvas.image, (left + 2, top + BAR + 24, right - 2, bottom - 2), ASSETS / "doodles" / "money.png",
                  KALSHI, radius=4, offset=(left, top))
    middle = top + BAR + 13
    _paste_logo(canvas, "kalshi", left + 10, middle, 15)
    canvas.text((right - 10, middle), "美国经济晴雨表", 12, anchor="rm")
    if not board:
        canvas.text(((left + right) / 2, (top + bottom) / 2), "Kalshi 数据暂不可用", 13, "muted", anchor="mm")
        return
    inner_left, inner_right = left + 12, right - 12
    y = top + BAR + 28
    sparks = payload.get("sparks") or {}
    if "KXFEDDECISION" in board:
        y = _fed(canvas, board["KXFEDDECISION"], y, inner_left, inner_right, sparks.get("KXFEDDECISION"))
    rows = [(series, board[series]) for series in BOARD_ROWS if series in board]
    row_h = (bottom - 4 - y) / max(1, len(rows))
    for index, (series, row) in enumerate(rows):
        y0 = y + index * row_h
        canvas.rule((inner_left, y0, inner_right, y0))
        value, note = board_value(row)
        top_line, low_line = y0 + row_h * 0.34, y0 + row_h * 0.73
        taken = _paste_mark(canvas, series, inner_left, y0 + row_h / 2)
        label_w = canvas.text((inner_left + taken, top_line), row["label"], 13, anchor="lm")
        tone = KALSHI if row.get("kind") != "yes" else ((0xD4, 0x20, 0x20) if row.get("chance", 0) >= 0.3 else KALSHI)
        value_w = canvas.text((inner_right, top_line), value, 16, tone, anchor="rm")
        if row.get("kind") == "yes" and row.get("change"):
            _change(canvas, inner_right - value_w - 6, top_line, row["change"])
        elif series in sparks:
            _pivot_spark(canvas, (inner_left + taken + label_w + 8, top_line, inner_right - value_w - 8), sparks[series])
        canvas.text((inner_left + taken, low_line), note, 10, "muted", anchor="lm", bold=False,
                    width=inner_right - inner_left - taken - LADDER_W - 6)
        _ladder(canvas, (inner_right - LADDER_W, low_line - LADDER_H / 2, inner_right, low_line + LADDER_H / 2), row)


def render_page(dimensions, payload, settings, now, images):
    canvas = PageCanvas(_palette(settings), family=(settings or {}).get("fontFamily") or "Microsoft YaHei")
    placed = paste_local_wordmark(canvas.image, ASSETS / "title_wordmark.png", (10, 4, 236, 50), align="left")
    x = (placed[2] if placed else 14 + canvas.text((14, 26), "世界赔率榜", 28, anchor="lm")) + 18
    x += _paste_logo(canvas, "polymarket", x, 27, 17) + 9
    canvas.rule((x, 18, x, 36))
    _paste_logo(canvas, "kalshi", x + 9, 27, 16)
    canvas.text((788, 19), f"{now:%m.%d} 周{WEEKDAYS[now.weekday()]} {now:%H:%M}", 16, anchor="rm")
    canvas.text((788, 39), "概率 = 预测市场的成交价", 10, "muted", anchor="rm", bold=False)
    canvas.rule((12, 52, 788, 52), "ink", 2)
    _lead(canvas, payload, images)
    _tiles(canvas, payload, images)
    _board(canvas, payload)
    result = canvas.image
    if tuple(dimensions) != (800, 480):
        result = result.resize(tuple(dimensions), Image.Resampling.LANCZOS)
    return result
