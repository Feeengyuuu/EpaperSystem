"""800 x 480 Money Radar page: market tiles, yield curve and macro, then holdings activity."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from PIL import Image, ImageOps

from utils.flat_icons import draw_icon
from utils.local_wordmark import paste_header_art, paste_local_wordmark
from utils.page_canvas import PageCanvas
from utils.safe_image import ImageLimits, safe_open_image

from .sources import (
    CURVE_POINTS, EARNINGS_HOURS, FORM_NAMES, ITEM_NAMES, days_until, local_now, parse_time, upcoming_earnings,
)


ASSETS = Path(__file__).resolve().parent / "assets"
_LIMITS = ImageLimits(max_bytes=256 * 1024, max_width=512, max_height=512, max_pixels=512 * 512)
# Pure white or black paper and neutral grey rules: an off-white or navy ground, or a tinted
# grey, dithers into scattered yellow, blue or green dots on the panel.
DAY = {
    "paper": "#ffffff", "panel": "#f0eee6", "ink": "#121a20", "muted": "#222c34", "rule": "#a6a6a6",
    "up": "#00a83a", "down": "#d42020", "accent": "#1f5fbf", "past": "#8a949b", "gold": "#ffde00",
    "badge_ink": "#ffffff",
}
NIGHT = {
    "paper": "#000000", "panel": "#16232d", "ink": "#edf1ea", "muted": "#c6d0d5", "rule": "#5a5a5a",
    "up": "#6fd38f", "down": "#ff6b6b", "accent": "#7fb8ff", "past": "#7d8b94", "gold": "#ffd84a",
    "badge_ink": "#0e171f",
}
COIN_NAMES = {"bitcoin": "比特币", "ethereum": "以太坊", "solana": "Solana"}
# What a dollar buys elsewhere, along the foot of the dollar/yuan card: (currency, flag, sign),
# the flags FX_FLAG_HEIGHT tall.
FX_OTHERS = (("EUR", "flag-eu", "€"), ("JPY", "flag-jp", "¥"), ("HKD", "flag-hk", "HK$"))
FX_FLAG_HEIGHT = 10
MACRO_ICON_SIZE = 18
EARNINGS_ROWS = 2
# Ticker chips in each brand's own published colour, as is (no snapping to the panel's
# inks): (fill, text, outline). Brands whose colours are close keep apart by the shape of
# their mark: Nintendo is a red outlined pill, SpaceX black on white, Apple plain black.
# Strings are theme roles, which swap at night so monochrome marks stay visible.
NVIDIA_GREEN = (0x76, 0xB9, 0x00)        # nvidia.com accent
TESLA_RED = (0xE8, 0x21, 0x27)           # Tesla logo red
NINTENDO_RED = (0xE6, 0x00, 0x12)        # Nintendo red
VANGUARD_RED = (0x96, 0x15, 0x1D)        # Vanguard maroon, shared by its funds
STATE_STREET_BLUE = (0x00, 0x1A, 0xFF)   # State Street logo (SPDR ETFs)
MICROSOFT_BLUE = (0x00, 0x78, 0xD4)
GOOGLE_BLUE = (0x42, 0x85, 0xF4)
AMAZON_ORANGE = (0xFF, 0x99, 0x00)
NETFLIX_RED = (0xE5, 0x09, 0x14)
CHIP_LIGHT, CHIP_DARK = (255, 255, 255), (0, 0, 0)
BRAND_CHIPS = {
    "AAPL": ("ink", "paper", None),
    "AMD": ("ink", "paper", None),
    "SPCX": ("paper", "ink", "ink"),
    "NVDA": (NVIDIA_GREEN, CHIP_DARK, None),
    "TSLA": (TESLA_RED, CHIP_LIGHT, None),
    "NTDOY": ("paper", NINTENDO_RED, NINTENDO_RED),
    "MSFT": (MICROSOFT_BLUE, CHIP_LIGHT, None),
    "GOOGL": (GOOGLE_BLUE, CHIP_LIGHT, None),
    "GOOG": (GOOGLE_BLUE, CHIP_LIGHT, None),
    "AMZN": (AMAZON_ORANGE, CHIP_DARK, None),
    "NFLX": (NETFLIX_RED, CHIP_LIGHT, None),
    "SPY": (STATE_STREET_BLUE, CHIP_LIGHT, None),
    "VTI": (VANGUARD_RED, CHIP_LIGHT, None),
    "VXUS": (VANGUARD_RED, CHIP_LIGHT, None),
    "VOO": (VANGUARD_RED, CHIP_LIGHT, None),
    "VT": (VANGUARD_RED, CHIP_LIGHT, None),
    "BND": (VANGUARD_RED, CHIP_LIGHT, None),
}
DEFAULT_CHIP = ("ink", "paper", None)
WEEKDAYS = "一二三四五六日"
# Each card's colour, drawn as a solid bar across its top so the kinds of figure read apart
# at a glance (a tinted fill would dither into dots; titles stay in ink). One colour per
# kind, keyed in the header's subtitle; coins keep their brand colours.
KIND_COLOURS = {
    "汇率": (0x0C, 0x85, 0x99),
    "加密": (0xF7, 0x93, 0x1A),
    "利率": (0x1F, 0x5F, 0xBF),
    "宏观": (0xD9, 0x48, 0x2B),
    "商品": (0xD4, 0xA0, 0x00),
    "持仓": "ink",
}
CARD_ACCENTS = {
    "fx": KIND_COLOURS["汇率"], "dxy": KIND_COLOURS["汇率"],
    "bitcoin": KIND_COLOURS["加密"], "ethereum": (0x62, 0x7E, 0xEA), "solana": (0x99, 0x45, 0xFF),
    "yield": KIND_COLOURS["利率"], "fed_funds": KIND_COLOURS["利率"], "mortgage30": KIND_COLOURS["利率"],
    "cpi": KIND_COLOURS["宏观"], "unemployment": KIND_COLOURS["宏观"],
    "gold": KIND_COLOURS["商品"],
    "holdings": KIND_COLOURS["持仓"],
}
ACCENT_BAR = 6
# The market tiles' title row, clear of the colour bar along their top (60-66).
TITLE_Y = 83
TILES = ((12, 200), (206, 394), (400, 588), (594, 788))


def _rgb(value):
    if isinstance(value, str) and value.startswith("#") and len(value) == 7:
        return tuple(int(value[index:index + 2], 16) for index in (1, 3, 5))
    return value


def _palette(settings):
    theme = settings.get("_inkypi_theme") or {}
    palette = NIGHT if theme.get("mode", settings.get("themeMode")) == "night" else DAY
    return {key: _rgb(value) for key, value in palette.items()}


def _asset(name, height):
    path = ASSETS / f"{name}.png"
    if not path.is_file():
        return None
    with safe_open_image(path, limits=_LIMITS) as source:
        image = source.convert("RGBA")
    return ImageOps.contain(image, (height * 3, height), Image.Resampling.LANCZOS)


def _paste(canvas, image, xy):
    if image is not None:
        canvas.image.paste(image, (round(xy[0]), round(xy[1])), image)
        return image.width
    return 0


def ago(stamp, now):
    seconds = max(0, (now - stamp).total_seconds())
    if seconds < 3600:
        return f"{max(1, int(seconds // 60))} 分钟前"
    if seconds < 86400:
        return f"{int(seconds // 3600)} 小时前"
    return f"{int(seconds // 86400)} 天前"


def price_text(value):
    if value >= 1000:
        return f"${value:,.0f}"
    if value >= 1:
        return f"${value:,.2f}"
    return f"${value:.4f}"


def sparkline(canvas, box, values, color):
    values = [value for value in values or [] if value is not None]
    left, top, right, bottom = box
    if len(values) < 2:
        canvas.text(((left + right) / 2, (top + bottom) / 2), "暂无走势", 12, "muted", anchor="mm", bold=False)
        return
    low, high = min(values), max(values)
    span = (high - low) or 1
    points = [(left + (right - left) * index / (len(values) - 1),
               bottom - (bottom - top) * (value - low) / span) for index, value in enumerate(values)]
    base_y = points[0][1]
    for x in range(int(left), int(right), 6):
        canvas.draw.line((x, base_y, x + 2, base_y), fill=canvas.color("rule"), width=1)
    canvas.draw.line(points, fill=color, width=2, joint="curve")
    end_x, end_y = points[-1]
    canvas.draw.ellipse((end_x - 3, end_y - 3, end_x + 3, end_y + 3), fill=color)


def _direction(change):
    if change is None or abs(change) < 1e-9:
        return "flat", "muted"
    return ("up", "up") if change > 0 else ("down", "down")


def _change_line(canvas, xy, change, figure, note=""):
    """A change in its direction's colour (green up, red down, grey flat), then a grey note.

    Every changing figure on the page uses this colour, so direction reads at a glance;
    the note says over what period.
    """
    icon, tone = _direction(change)
    draw_icon(canvas.draw, icon, (xy[0], xy[1] - 6), 11, canvas.color(tone), canvas.color("paper"))
    width = canvas.text((xy[0] + 15, xy[1]), figure, 13, tone, anchor="lm")
    if note:
        canvas.text((xy[0] + 15 + width + 5, xy[1]), note, 13, "muted", anchor="lm", bold=False)


def _accent_bar(canvas, box, accent, height=ACCENT_BAR):
    """The card's colour as a solid bar along its rounded top edge."""
    if accent is None:
        return
    left, top, right, _bottom = box
    colour = canvas.color(accent) if isinstance(accent, str) else accent
    canvas.draw.rounded_rectangle((left, top, right, top + height), radius=6, fill=colour, corners=(True, True, False, False))


def _tile_frame(canvas, left, right, title, mark=None, mark_text=None, accent=None):
    canvas.draw.rounded_rectangle((left, 60, right, 196), radius=6, outline=canvas.color("rule"), width=1)
    _accent_bar(canvas, (left, 60, right, 196), accent)
    x = left + 10
    if mark is not None:
        x += _paste(canvas, mark, (x, TITLE_Y - 11)) + 6
    elif mark_text:
        canvas.draw.rounded_rectangle((x, TITLE_Y - 11, x + 22, TITLE_Y + 11), radius=5, fill=canvas.color("ink"))
        canvas.text((x + 11, TITLE_Y), mark_text, 13, "paper", anchor="mm")
        x += 28
    canvas.text((x, TITLE_Y), title, 14, anchor="lm", width=right - x - 8)


def _fx_tile(canvas, payload, box):
    left, right = box
    data = payload["fx"]["data"]
    series = (data.get("series") or {}).get("CNY") or []
    us, cn = _asset("flag-us", 14), _asset("flag-cn", 14)
    _tile_frame(canvas, left, right, "美元/人民币", accent=CARD_ACCENTS["fx"])
    flag_x = right - 10 - (us.width if us else 0) - (cn.width if cn else 0) - 4
    _paste(canvas, us, (flag_x, TITLE_Y - 7))
    _paste(canvas, cn, (flag_x + (us.width if us else 0) + 4, TITLE_Y - 7))
    if len(series) < 2:
        canvas.text((left + 10, 120), "汇率暂不可用", 15, "muted", anchor="lm")
        return
    latest, first = series[-1][1], series[0][1]
    canvas.text((left + 10, 114), f"¥{latest:.4f}", 28, anchor="lm")
    change = (latest / first - 1) * 100
    _change_line(canvas, (left + 10, 138), change, f"{change:+.2f}%", f"{len(series)} 个交易日")
    sparkline(canvas, (left + 12, 150, right - 12, 176), [value for _day, value in series], CARD_ACCENTS["fx"])
    # A dollar in the other currencies: each one's flag, its sign and the rate, spread across the
    # foot; when they run long the yen loses its decimal first, then the type steps down.
    rates = []
    for code, flag, sign in FX_OTHERS:
        rows = (data.get("series") or {}).get(code) or []
        if rows:
            rates.append((_asset(flag, FX_FLAG_HEIGHT), code, sign, rows[-1][1]))
    if not rates:
        return
    room = right - left - 20

    def layout(size, whole_yen):
        texts = [(f"{sign}{rate:.0f}" if code == "JPY" and whole_yen else
                  f"{sign}{rate:.1f}" if code == "JPY" else f"{sign}{rate:.2f}") if mark is not None else
                 f"{code} {rate:.2f}" for mark, code, sign, rate in rates]
        widths = [(mark.width + 3 if mark is not None else 0) + canvas.width(text, size)
                  for (mark, _code, _sign, _rate), text in zip(rates, texts)]
        return texts, widths

    for size, whole_yen in ((12, False), (11, False), (12, True), (11, True), (10, True)):
        texts, widths = layout(size, whole_yen)
        if sum(widths) + 4 * (len(rates) - 1) <= room:
            break
    gap = (room - sum(widths)) / (len(rates) - 1) if len(rates) > 1 else 0
    x = left + 10
    for (mark, _code, _sign, _rate), text, width in zip(rates, texts, widths):
        start_x = x
        if mark is not None:
            # A hairline round each flag, so Japan's white field still reads as a flag on the card.
            top = round(187 - mark.height / 2)
            _paste(canvas, mark, (x, top))
            canvas.draw.rectangle((round(x) - 1, top - 1, round(x) + mark.width, top + mark.height), outline=canvas.color("muted"))
            x += mark.width + 3
        canvas.text((x, 187), text, size, anchor="lm", width=right - 10 - x)
        x = start_x + width + gap


def _coin_tile(canvas, coin, box):
    left, right = box
    name = COIN_NAMES.get(coin["id"], coin.get("name") or coin["symbol"])
    _tile_frame(canvas, left, right, name, _asset(f"coin-{coin['id']}", 22), coin["symbol"][:2],
                accent=CARD_ACCENTS.get(coin["id"], "ink"))
    canvas.text((right - 10, TITLE_Y), coin["symbol"], 12, "muted", anchor="rm", bold=False)
    canvas.text((left + 10, 114), price_text(coin["price"]), 28, anchor="lm", width=right - left - 20)
    day, week = coin.get("change_24h"), coin.get("change_7d")
    _change_line(canvas, (left + 10, 138), day, f"{day:+.2f}%" if day is not None else "—", "24 小时")
    color = canvas.color("up" if (week or 0) >= 0 else "down")
    sparkline(canvas, (left + 12, 150, right - 12, 176), coin.get("sparkline"), color)
    if week is None:
        canvas.text((left + 10, 187), "7 天走势", 11, "muted", anchor="lm", bold=False)
    else:
        label = canvas.text((left + 10, 187), "7 天", 11, "muted", anchor="lm", bold=False)
        canvas.text((left + 10 + label + 4, 187), f"{week:+.1f}%", 11, _direction(week)[1], anchor="lm")


def _yield_tile(canvas, payload, box):
    left, right = box
    quotes = payload["markets"]["data"].get("quotes") or {}
    ten = quotes.get("^TNX")
    _tile_frame(canvas, left, right, "10 年期美债", mark_text="%", accent=CARD_ACCENTS["yield"])
    if not ten:
        canvas.text((left + 10, 120), "利率暂不可用", 15, "muted", anchor="lm")
        return
    canvas.text((left + 10, 114), f"{ten['last']:.2f}%", 28, anchor="lm")
    change_bp = (ten["last"] - ten["month_ago"]) * 100
    _change_line(canvas, (left + 10, 138), change_bp, f"{change_bp:+.0f} 基点", "30 天")
    sparkline(canvas, (left + 12, 150, right - 12, 176), ten.get("series"), canvas.color("accent"))
    rest = [f"{label} {quotes[symbol]['last']:.2f}" for symbol, label in CURVE_POINTS
            if symbol != "^TNX" and symbol in quotes]
    canvas.text((left + 10, 187), " · ".join(rest), 11, "muted", anchor="lm", bold=False, width=right - left - 20)


def _curve(canvas, payload):
    left, top, right, bottom = 12, 204, 392, 330
    canvas.draw.rounded_rectangle((left, top, right, bottom), radius=6, outline=canvas.color("rule"), width=1)
    _accent_bar(canvas, (left, top, right, bottom), CARD_ACCENTS["yield"])
    icon = _asset("macro-curve", MACRO_ICON_SIZE)
    title_x = left + 10 + (_paste(canvas, icon, (left + 9, top + 18 - icon.height // 2)) + 6 if icon else 0)
    canvas.text((title_x, top + 18), "美债收益率曲线", 15, anchor="lm")
    quotes = payload["markets"]["data"].get("quotes") or {}
    points = [(label, quotes[symbol]) for symbol, label in CURVE_POINTS if symbol in quotes]
    legend_x = right - 10
    for label, color, dashed in (("一个月前", "past", True), ("今天", "accent", False)):
        width = canvas.text((legend_x, top + 18), label, 11, "muted", anchor="rm", bold=False)
        line_left = legend_x - width - 22
        if dashed:
            for x in range(int(line_left), int(line_left) + 16, 5):
                canvas.draw.line((x, top + 18, x + 2, top + 18), fill=canvas.color(color), width=2)
        else:
            canvas.draw.line((line_left, top + 18, line_left + 16, top + 18), fill=canvas.color(color), width=2)
        legend_x = line_left - 10
    if len(points) < 2:
        canvas.text(((left + right) / 2, (top + bottom) / 2 + 8), "收益率暂不可用", 14, "muted", anchor="mm")
        return
    values = [quote["last"] for _label, quote in points] + [quote["month_ago"] for _label, quote in points]
    low, high = min(values) - 0.1, max(values) + 0.1
    plot = (left + 44, top + 44, right - 16, bottom - 22)

    def y_of(value):
        return plot[3] - (plot[3] - plot[1]) * (value - low) / (high - low)

    for step in range(3):
        value = low + (high - low) * step / 2
        y = y_of(value)
        canvas.rule((plot[0], y, plot[2], y))
        canvas.text((plot[0] - 6, y), f"{value:.1f}%", 11, "muted", anchor="rm", bold=False)
    xs = [plot[0] + (plot[2] - plot[0]) * index / (len(points) - 1) for index in range(len(points))]
    past = [(x, y_of(quote["month_ago"])) for x, (_label, quote) in zip(xs, points)]
    for (x1, y1), (x2, y2) in zip(past, past[1:]):
        steps = max(1, int(abs(x2 - x1) // 6))
        for index in range(0, steps, 2):
            a, b = index / steps, min(1, (index + 1) / steps)
            canvas.draw.line((x1 + (x2 - x1) * a, y1 + (y2 - y1) * a, x1 + (x2 - x1) * b, y1 + (y2 - y1) * b),
                             fill=canvas.color("past"), width=2)
    now_points = [(x, y_of(quote["last"])) for x, (_label, quote) in zip(xs, points)]
    canvas.draw.line(now_points, fill=canvas.color("accent"), width=3, joint="curve")
    for (x, y), (label, quote) in zip(now_points, points):
        canvas.draw.ellipse((x - 4, y - 4, x + 4, y + 4), fill=canvas.color("accent"))
        # Each yield is coloured by its move since a month ago, like every other change.
        tone = _direction(quote["last"] - quote["month_ago"])[1]
        canvas.text((x, y - 8), f"{quote['last']:.2f}", 11, "ink" if tone == "muted" else tone, anchor="mb",
                    stroke="paper")
        canvas.text((x, bottom - 10), label, 12, anchor="mm", bold=False)


def _macro_tiles(canvas, payload):
    macro = payload["macro"]["data"]
    quotes = payload["markets"]["data"].get("quotes") or {}
    tiles = []
    for key, label, unit in (("cpi", "CPI 同比", "%"), ("unemployment", "失业率", "%")):
        item = macro.get(key)
        if item and item.get("value") is not None:
            tiles.append((key, label, f"{item['value']:.1f}{unit}", item.get("previous"), item["value"],
                          f"{str(item.get('period'))[-2:].lstrip('0')} 月 · {macro.get('source', '')}"))
    if macro.get("fed_funds") or macro.get("mortgage30"):
        for key, label in (("fed_funds", "联邦基金利率"), ("mortgage30", "30 年房贷利率")):
            item = macro.get(key)
            if item:
                tiles.append((key, label, f"{item['value']:.2f}%", item.get("previous"), item["value"], "FRED"))
    else:
        for key, symbol, label, fmt in (("dxy", "DX-Y.NYB", "美元指数", "{:.1f}"), ("gold", "GC=F", "黄金", "${:,.0f}")):
            quote = quotes.get(symbol)
            if quote:
                tiles.append((key, label, fmt.format(quote["last"]), quote["month_ago"], quote["last"], "30 天"))
    for index, (key, label, value, previous, current, note) in enumerate(tiles[:4]):
        column, row = index % 2, index // 2
        left = 400 + column * 197
        top = 204 + row * 64
        canvas.draw.rounded_rectangle((left, top, left + 191, top + 58), radius=6, outline=canvas.color("rule"))
        _accent_bar(canvas, (left, top, left + 191, top + 58), CARD_ACCENTS.get(key), 5)
        # Each indicator carries its own picture (cart, briefcase, dollar coin, gold bars, bank, house).
        icon = _asset(f"macro-{key}", MACRO_ICON_SIZE)
        label_x = left + 10 + (_paste(canvas, icon, (left + 9, top + 18 - icon.height // 2)) + 5 if icon else 0)
        canvas.text((label_x, top + 18), label, 13, anchor="lm", width=left + 118 - label_x)
        canvas.text((left + 181, top + 18), note, 11, "muted", anchor="rm", bold=False, width=80)
        canvas.text((left + 10, top + 41), value, 22, anchor="lm")
        if previous is not None:
            change = current - previous
            icon, tone = _direction(change)
            draw_icon(canvas.draw, icon, (left + 120, top + 35), 11, canvas.color(tone), canvas.color("paper"))
            canvas.text((left + 135, top + 41), f"{change:+.2f}" if abs(change) < 10 else f"{change:+,.0f}",
                        13, tone, anchor="lm")
    if not tiles:
        canvas.text((594, 268), "宏观数据暂不可用", 14, "muted", anchor="mm")


def chip_style(canvas, symbol):
    """(fill, text, outline) colours of a ticker's chip in this theme."""
    fill, text, outline = BRAND_CHIPS.get(str(symbol or "").upper(), DEFAULT_CHIP)
    return canvas.color(fill), canvas.color(text), canvas.color(outline) if outline else None


def _draw_chip(canvas, box, symbol, radius):
    fill, text, outline = chip_style(canvas, symbol)
    canvas.draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=1 if outline else 0)
    centre = ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
    canvas.text(centre, symbol or "—", 10, text, anchor="mm", width=box[2] - box[0] - 4)


def _ticker_chip(canvas, x, y, symbol, width=42):
    _draw_chip(canvas, (x, y - 8, x + width, y + 8), symbol, 3)
    return width


def _news(canvas, items, now, left, right, top, rows):
    if not items:
        canvas.text((left, top + 4), "暂无持仓相关新闻", 13, "muted", anchor="lm")
        return
    for index, item in enumerate(items[:rows]):
        y = top + index * 19
        stamp = parse_time(item.get("time"))
        meta = ago(stamp, now).replace(" ", "") if stamp else ""
        meta_width = canvas.text((right, y), meta, 11, "muted", anchor="rm", bold=False)
        chip = _ticker_chip(canvas, left, y, item.get("symbol"))
        canvas.text((left + chip + 6, y), item["title"], 12, anchor="lm", bold=False,
                    width=right - left - chip - 14 - meta_width)
        if index < min(rows, len(items)) - 1:
            for x in range(int(left), int(right), 5):
                canvas.draw.point((x, y + 9), fill=canvas.color("rule"))


def _weekday_date(day):
    value = date.fromisoformat(day)
    return f"{value:%m/%d} 周{WEEKDAYS[value.weekday()]}"


def _earnings(canvas, payload, now, left, right, top, rows):
    items = upcoming_earnings(payload["earnings"]["data"].get("items"), now)[:rows]
    if not items:
        canvas.text((left, top), "45 天内持仓无财报", 13, "muted", anchor="lm")
        return 1
    for index, item in enumerate(items):
        y = top + index * 19
        draw_icon(canvas.draw, "calendar", (left, y - 7), 13, canvas.color("ink"), canvas.color("paper"))
        canvas.text((left + 19, y), _weekday_date(item["date"]), 12, anchor="lm")
        hour = EARNINGS_HOURS.get(item.get("hour"), "")
        canvas.text((left + 104, y), f"{item['symbol']} {hour}", 13, anchor="lm")
        remaining = days_until(item["date"], now)
        canvas.text((right, y), "今天" if remaining == 0 else f"{remaining} 天后", 12, "muted",
                    anchor="rm", bold=False)
    return len(items)


def _filings(canvas, payload, left, right, top, rows):
    items = payload["filings"]["data"].get("items", [])[:rows]
    insider = payload["filings"]["data"].get("insider") or {}
    if not items:
        canvas.text((left, top), "60 天内持仓无新公告", 13, "muted", anchor="lm")
        return
    for index, item in enumerate(items):
        y = top + index * 19
        canvas.text((left, y), item["date"][5:].replace("-", "/"), 12, "muted", anchor="lm", bold=False)
        canvas.text((left + 42, y), item["symbol"], 13, anchor="lm")
        badge = item["form"]
        badge_left = left + 92
        width = canvas.width(badge, 11) + 10
        canvas.draw.rounded_rectangle((badge_left, y - 8, badge_left + width, y + 8), radius=3,
                                      fill=canvas.color("ink"))
        canvas.text((badge_left + width / 2, y), badge, 11, "paper", anchor="mm")
        meaning = "、".join(ITEM_NAMES[code] for code in item.get("items", []) if code in ITEM_NAMES) \
            or FORM_NAMES.get(item["form"], item.get("description", ""))
        canvas.text((badge_left + width + 6, y), meaning, 12, anchor="lm", bold=False,
                    width=right - badge_left - width - 6)
    if insider:
        text = "近 30 天内部人交易：" + "、".join(f"{symbol} {count}" for symbol, count in insider.items())
        canvas.text((left, top + len(items) * 19), text, 11, "muted", anchor="lm", bold=False, width=right - left)


def _holdings(canvas, payload, now):
    left, top, right, bottom = 12, 336, 788, 468
    canvas.draw.rounded_rectangle((left, top, right, bottom), radius=6, outline=canvas.color("rule"), width=1)
    _accent_bar(canvas, (left, top, right, bottom), CARD_ACCENTS["holdings"], 4)
    canvas.draw.rectangle((left + 8, top + 8, left + 12, top + 22), fill=canvas.color("accent"))
    draw_icon(canvas.draw, "paper", (left + 18, top + 7), 16, canvas.color("ink"), canvas.color("paper"))
    x = left + 40 + canvas.text((left + 40, top + 15), "持仓动态", 15, anchor="lm") + 10
    earnings_on = payload["earnings"]["state"] != "disabled"
    filings_on = payload["filings"]["state"] != "disabled"
    hint = []
    if not earnings_on:
        hint.append("财报需 FINNHUB_API_KEY")
    if not filings_on:
        hint.append("公告需 SEC 联系邮箱")
    origin = "跟随股票页" if payload["symbols_from_portfolio"] else "自选"
    detail = " · ".join([origin, *hint]) if hint else f"{origin} · Yahoo · Finnhub · SEC"
    detail_width = canvas.text((right - 8, top + 15), detail, 11, "muted", anchor="rm", bold=False)
    for symbol in payload["symbols"]:
        width = canvas.width(symbol, 10) + 10
        if x + width > right - 16 - detail_width:
            break
        # The holdings row doubles as the colour key for the chips below.
        _draw_chip(canvas, (x, top + 8, x + width, top + 22), symbol, 7)
        x += width + 4
    canvas.rule((left + 8, top + 28, right - 8, top + 28))
    body, rows = top + 42, 5
    news = payload["news"]["data"].get("items", [])
    middle = (left + right) // 2
    if not earnings_on and not filings_on:
        _news(canvas, news, now, left + 10, middle - 10, body, rows)
        _news(canvas, news[rows:], now, middle + 10, right - 10, body, rows)
        canvas.rule((middle, body - 8, middle, bottom - 8))
        return
    if not filings_on:
        # Two earnings rows head the left half; the news reads on below them and down the right half.
        _earnings(canvas, payload, now, left + 10, middle - 10, body, EARNINGS_ROWS)
        split = body + EARNINGS_ROWS * 19 - 10
        canvas.rule((left + 8, split, middle - 8, split))
        canvas.rule((middle, body - 8, middle, bottom - 8))
        below = rows - EARNINGS_ROWS
        _news(canvas, news, now, left + 10, middle - 10, split + 10, below)
        if len(news) > below:
            _news(canvas, news[below:], now, middle + 10, right - 10, body, rows)
        return
    left_rows = rows
    y = body
    if earnings_on:
        used = _earnings(canvas, payload, now, left + 10, middle - 10, y, EARNINGS_ROWS)
        y += max(used, 1) * 19 + 2
        left_rows -= max(used, 1)
    if filings_on:
        _filings(canvas, payload, left + 10, middle - 10, y, max(left_rows - 1, 2))
    canvas.rule((middle, body - 8, middle, bottom - 8))
    _news(canvas, news, now, middle + 10, right - 10, body, rows)


def _kind_key(canvas, x, y):
    """The subtitle as the colour key: each kind of figure after a square in its card colour."""
    for kind, colour in KIND_COLOURS.items():
        fill = canvas.color(colour) if isinstance(colour, str) else colour
        canvas.draw.rounded_rectangle((x, y - 5, x + 10, y + 5), radius=2, fill=fill)
        x += 14 + canvas.text((x + 14, y), kind, 14, anchor="lm", bold=False) + 10
    return x - 10


def render_page(dimensions, payload, settings, now):
    now = local_now(now)
    canvas = PageCanvas(_palette(settings), family=settings.get("fontFamily") or "Microsoft YaHei")
    # The night artwork lightens the navy outline and radar ring so they survive a dark ground.
    night = canvas.color("paper") == _rgb(NIGHT["paper"])
    wordmark = ASSETS / ("title_wordmark_night.png" if night else "title_wordmark.png")
    pasted = paste_local_wordmark(canvas.image, wordmark, (12, 2, 206, 51), align="left")
    if pasted:
        title_right = pasted[2]
    else:
        title_right = 16 + canvas.text((16, 27), "资金雷达", 30, anchor="lm")
    subtitle_right = _kind_key(canvas, title_right + 14, 30)
    date_left = 788 - canvas.text((788, 27), f"{now:%m.%d} 周{WEEKDAYS[now.weekday()]} {now:%H:%M}", 16, anchor="rm")
    paste_header_art(canvas.image, ASSETS / "header_art.png", subtitle_right, date_left)
    canvas.rule((12, 52, 788, 52), "ink", 2)
    _fx_tile(canvas, payload, TILES[0])
    coins = payload["crypto"]["data"].get("coins", [])
    for coin, box in zip(coins[:2], TILES[1:3]):
        _coin_tile(canvas, coin, box)
    for box in TILES[1 + len(coins[:2]):3]:
        _tile_frame(canvas, box[0], box[1], "加密货币", mark_text="C")
        canvas.text(((box[0] + box[1]) / 2, 130), "暂不可用", 15, "muted", anchor="mm")
    _yield_tile(canvas, payload, TILES[3])
    _curve(canvas, payload)
    _macro_tiles(canvas, payload)
    _holdings(canvas, payload, now)
    result = canvas.image
    if tuple(dimensions) != (800, 480):
        result = result.resize(tuple(dimensions), Image.Resampling.LANCZOS)
    return result
