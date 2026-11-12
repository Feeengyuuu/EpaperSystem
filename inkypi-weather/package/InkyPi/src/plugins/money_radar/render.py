"""800 x 480 Money Radar page: market tiles, yield curve and macro, then holdings activity."""

from __future__ import annotations

import math
import re
from datetime import date
from pathlib import Path

from PIL import Image, ImageOps

from plugins.base_plugin.cover_gallery import SOURCE_LOGO_DIR
from utils.doodle_backdrop import paint_doodles
from utils.flat_icons import draw_icon
from utils.local_wordmark import load_source_logo, paste_header_logo_row, paste_local_wordmark, source_logo_width
from utils.page_canvas import PageCanvas
from utils.safe_image import ImageLimits, safe_open_base64_image, safe_open_image
from utils.theme_utils import relative_luminance

from . import extras
from .sources import (
    CURVE_POINTS, EARNINGS_HOURS, FORM_NAMES, ITEM_NAMES, days_until, fear_greed_band, local_now, parse_time,
    upcoming_earnings,
)
from .trend_lines import delta, direction as _direction, draw_segments, finite


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
# The header names the page's sources by their logos (assets/logos) in one row between the title
# and the date: official statistics first, then market data, as tall as the row fits. The last
# names the earnings calendar in use: Finnhub's with its key, else Nasdaq's.
SOURCE_LOGO_ROW = ("ecb", "fred", "bls", "sec", "yahoo_finance", "coingecko", "finnhub")
EARNINGS_SOURCE_LOGOS = {"nasdaq": "nasdaq"}
SOURCE_LOGO_HEIGHTS = range(26, 11, -1)
SOURCE_LOGO_GAP = 12
# The SEC's, Yahoo Finance's and Nasdaq's logos are the stock page's too: they live with the
# shared source logos (base_plugin/assets/source_logos), the rest in this page's assets/logos.
SHARED_SOURCE_LOGOS = frozenset({"sec", "yahoo_finance", "nasdaq"})
# Logos whose black lettering takes the night ink (True: every dark part, as Yahoo prints its
# wordmark white on dark grounds); the seals and emblems keep their printed colours.
SOURCE_LOGO_INKED = {"fred": False, "coingecko": False, "nasdaq": False, "yahoo_finance": True}
# A macro tile names its source (BLS, FRED) by the official logo this tall at its top right.
TILE_SOURCE_LOGOS, TILE_SOURCE_LOGO_HEIGHT = {"BLS": "bls", "FRED": "fred"}, 16
MACRO_ICON_SIZE = 18
# The dollar index and gold cards' figure size, beside their 52-week block.
RANGED_FIGURE_SIZE = 18
EARNINGS_ROWS = 2
# The earnings dates stand in two columns of EARNINGS_ROWS, the nearest first down the left one,
# each led by the generated calendar 科技与游戏 marks release dates with (assets/icon-calendar.png).
EARNINGS_COLUMNS, EARNINGS_COLUMN_GAP, CALENDAR_ICON_HEIGHT = 2, 10, 14
# Each earnings row leads with the company's mark: the same files the stock tracker's Robinhood
# holdings list shows (stocktracker/assets/logos/<symbol>.png), in a HOLDING_LOGO_SLOT-wide slot.
HOLDING_LOGOS = Path(__file__).resolve().parents[1] / "stocktracker" / "assets" / "logos"
# Share classes whose mark is the company's (Berkshire's A and B shares however they are spelt), and
# the marks printed wholly in a deep brand ink that takes the night ink whole (Berkshire's navy).
HOLDING_LOGO_ALIASES = {"BRK.A": "brk", "BRK.B": "brk"}
NIGHT_INKED_HOLDING_LOGOS = {"brk"}
HOLDING_LOGO_SIZE, HOLDING_LOGO_SLOT = 14, 20
# The holdings header lists each holding by its logo, this tall and at most this wide (a wordmark
# such as Nintendo's needs the width to read), this far apart.
HEADER_HOLDING_LOGO_SIZE, HEADER_HOLDING_LOGO_WIDTH, HEADER_HOLDING_GAP = 15, 40, 9
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
# Each card's kind as quiet engraving-style doodles in its colour under its figures: coins and
# exchange arrows, chained blocks, bank columns and bonds, factories and carts, gold bars and
# barrels, newspapers and calendars.
DOODLE_DIR = Path(__file__).resolve().parent / "assets" / "doodles"
DOODLE_KINDS = {
    "fx": "fx", "dxy": "fx", "bitcoin": "crypto", "ethereum": "crypto", "solana": "crypto", "crypto": "crypto",
    "yield": "rates", "fed_funds": "rates", "mortgage30": "rates", "cpi": "macro", "unemployment": "macro",
    "gold": "commodities", "holdings": "holdings",
}
DOODLE_STRENGTH = 0.18
# The market tiles' title row, clear of the colour bar along their top (60-66).
TITLE_Y = 83
TILES = ((12, 200), (206, 394), (400, 588), (594, 788))
# CoinGecko's trending coins on the coin tiles' bottom row: logo height and the gap after each.
TRENDING_ROW_Y = 187
TRENDING_LOGO = 13
TRENDING_GAP = 6
# The yield curve's card, and CNN's Fear & Greed gauge beside it.
CURVE_CARD = (12, 204, 262, 330)
FEAR_GREED_CARD = (268, 204, 392, 330)
# CNN's five bands from extreme fear to extreme greed, as the gauge's arc colours them.
FEAR_GREED_COLOURS = ((0xD4, 0x20, 0x20), (0xF0, 0x8C, 0x00), (0x8C, 0x96, 0xA0), (0x6C, 0xC0, 0x4A),
                      (0x00, 0xA8, 0x3A))
FEAR_GREED_EDGES = (0, 25, 45, 55, 75, 100)


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


_PERIOD_START = object()


def sparkline(canvas, box, values, color=None, *, baseline=_PERIOD_START):
    values = [finite(value) for value in values or []]
    left, top, right, bottom = box
    valid = [value for value in values if value is not None]
    if len(valid) < 2:
        canvas.text(((left + right) / 2, (top + bottom) / 2), "暂无走势", 12, "muted", anchor="mm", bold=False)
        return
    baseline = values[0] if baseline is _PERIOD_START else finite(baseline)
    limits = valid + ([baseline] if baseline is not None else [])
    low, high = min(limits), max(limits)
    if low == high:
        low, high = low - 0.5, high + 0.5
    span = high - low
    points = [(left + (right - left) * index / (len(values) - 1),
               bottom - (bottom - top) * (value - low) / span) if value is not None else None
              for index, value in enumerate(values)]
    if baseline is not None:
        base_y = bottom - (bottom - top) * (baseline - low) / span
        for x in range(int(left), int(right), 6):
            canvas.draw.line((x, base_y, min(right, x + 2), base_y), fill=canvas.color("rule"), width=1)
    differences = [delta(value, baseline) for value in values]
    draw_segments(canvas, points, differences)
    if points[-1] is not None:
        end_x, end_y = points[-1]
        canvas.draw.ellipse((end_x - 3, end_y - 3, end_x + 3, end_y + 3),
                            fill=canvas.color(_direction(differences[-1])[1]))


def _number(canvas, xy, value, size, width, tone="ink", *, anchor="lm"):
    """Keep complete values and units; shrink type instead of ellipsizing numbers."""
    while size > 1 and canvas.width(value, size) > width:
        size -= 1
    return canvas.text(xy, value, size, tone, anchor=anchor)


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


def _card_doodles(canvas, box, key, accent, bar=ACCENT_BAR):
    """Lay ``key``'s kind of doodles in ``accent`` inside a card, under its colour bar."""
    left, top, right, bottom = box
    colour = canvas.color(accent) if isinstance(accent, str) else accent
    inside = (left + 1, top + bar + 1, right - 1, bottom - 1)
    paint_doodles(canvas.image, inside, DOODLE_DIR / f"{DOODLE_KINDS[key]}.png", colour, strength=DOODLE_STRENGTH,
                  radius=5, offset=inside[:2])


def _tile_frame(canvas, left, right, title, mark=None, mark_text=None, accent=None, doodles=None):
    canvas.draw.rounded_rectangle((left, 60, right, 196), radius=6, outline=canvas.color("rule"), width=1)
    _accent_bar(canvas, (left, 60, right, 196), accent)
    if doodles:
        _card_doodles(canvas, (left, 60, right, 196), doodles, accent or KIND_COLOURS["加密"])
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
    _tile_frame(canvas, left, right, "美元/人民币", accent=CARD_ACCENTS["fx"], doodles="fx")
    flag_x = right - 10 - (us.width if us else 0) - (cn.width if cn else 0) - 4
    _paste(canvas, us, (flag_x, TITLE_Y - 7))
    _paste(canvas, cn, (flag_x + (us.width if us else 0) + 4, TITLE_Y - 7))
    if len(series) < 2:
        canvas.text((left + 10, 120), "汇率暂不可用", 15, "muted", anchor="lm")
        return
    latest, first = finite(series[-1][1]), finite(series[0][1])
    if latest is None:
        canvas.text((left + 10, 120), "汇率暂不可用", 15, "muted", anchor="lm")
        return
    change = (latest / first - 1) * 100 if first else None
    _number(canvas, (left + 10, 114), f"¥{latest:.4f}", 28, right - left - 20, _direction(change)[1])
    _change_line(canvas, (left + 10, 138), change, f"{change:+.2f}%" if change is not None else "—",
                 f"{len(series)} 个交易日")
    # The central bank's parity for the day stands at the end of the market rate's line, as the
    # coins' 7-day change does on theirs; without it the line keeps the card's width.
    parity = extras.parity_reading(extras.feed_data(payload.get("extras"), "parity"))
    line_right = right - 12
    if parity is not None:
        figure = f"{parity[0]:.4f}"
        line_right = right - 19 - max(canvas.width("中间价", 10), canvas.width(figure, 12))
        canvas.text((right - 10, 156), "中间价", 10, "muted", anchor="rm", bold=False)
        canvas.text((right - 10, 171), figure, 12, anchor="rm")
    sparkline(canvas, (left + 12, 150, line_right, 176), [value for _day, value in series])
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
                accent=CARD_ACCENTS.get(coin["id"], "ink"), doodles="crypto")
    canvas.text((right - 10, TITLE_Y), coin["symbol"], 12, "muted", anchor="rm", bold=False)
    day, week = coin.get("change_24h"), coin.get("change_7d")
    _number(canvas, (left + 10, 114), price_text(coin["price"]), 28, right - left - 20, _direction(day)[1])
    _change_line(canvas, (left + 10, 138), day, f"{day:+.2f}%" if day is not None else "—", "24 小时")
    # The 7-day change stands at the end of its 7-day line, leaving the bottom row to the
    # trending coins.
    figure = f"{week:+.1f}%" if week is not None else "—"
    column = max(canvas.width("7 天", 10), canvas.width(figure, 12))
    sparkline(canvas, (left + 12, 150, right - 19 - column, 176), coin.get("sparkline"))
    canvas.text((right - 10, 156), "7 天", 10, "muted", anchor="rm", bold=False)
    canvas.text((right - 10, 171), figure, 12, _direction(week)[1] if week is not None else "muted", anchor="rm")
    return left + 10


def _coin_logo(value):
    try:
        return safe_open_base64_image(value or "", limits=_LIMITS).convert("RGBA")
    except (OSError, ValueError, TypeError):
        return None


def _trending_row(canvas, x, right, coins, label):
    """As many of ``coins`` as fit between ``x`` and ``right`` on a coin tile's bottom row, each
    its official logo and symbol, after a muted label; how many it drew."""
    y = TRENDING_ROW_Y
    if label:
        x += canvas.text((x, y), label, 10, "muted", anchor="lm", bold=False) + 4
    drawn = 0
    for coin in coins:
        logo = _coin_logo(coin.get("logo"))
        if logo is not None:
            logo = ImageOps.contain(logo, (TRENDING_LOGO, TRENDING_LOGO), Image.Resampling.LANCZOS)
        width = (logo.width + 2 if logo is not None else 0) + canvas.width(coin["symbol"], 10)
        if x + width > right:
            break
        if logo is not None:
            canvas.image.paste(logo, (int(x), int(y - logo.height / 2)), logo)
            x += logo.width + 2
        x += canvas.text((x, y), coin["symbol"], 10, anchor="lm") + TRENDING_GAP
        drawn += 1
    return drawn


def _crypto_tiles(canvas, payload):
    """The two coin tiles; CoinGecko's trending coins run along their bottom rows, from the
    first tile into the second, after each tile's 7-day change."""
    coins = ((payload.get("crypto") or {}).get("data") or {}).get("coins", [])
    trending = [coin for coin in ((payload.get("trending") or {}).get("data") or {}).get("coins") or []
                if isinstance(coin, dict) and coin.get("symbol")]
    for coin, box in zip(coins[:2], TILES[1:3]):
        end = _coin_tile(canvas, coin, box)
        if trending:
            first = box == TILES[1]
            trending = trending[_trending_row(canvas, end, box[1] - 8, trending, "热搜" if first else ""):]
    for box in TILES[1 + len(coins[:2]):3]:
        _tile_frame(canvas, box[0], box[1], "加密货币", mark_text="C", doodles="crypto")
        canvas.text(((box[0] + box[1]) / 2, 130), "暂不可用", 15, "muted", anchor="mm")


def _yield_tile(canvas, payload, box):
    left, right = box
    quotes = payload["markets"]["data"].get("quotes") or {}
    ten = quotes.get("^TNX")
    _tile_frame(canvas, left, right, "10 年期美债", mark_text="%", accent=CARD_ACCENTS["yield"], doodles="yield")
    if not ten or finite(ten.get("last")) is None:
        canvas.text((left + 10, 120), "利率暂不可用", 15, "muted", anchor="lm")
        return
    change = delta(ten.get("last"), ten.get("month_ago"))
    figure_right = left + 10 + _number(canvas, (left + 10, 114), f"{ten['last']:.2f}%", 28, right - left - 20,
                                       _direction(change)[1])
    # Beside the figure: where it sits in its 52-week range.
    _range_block(canvas, (max(figure_right + 12, right - 92), right - 10), 106, ten, "{:.2f}", CARD_ACCENTS["yield"])
    change_bp = change * 100 if change is not None else None
    _change_line(canvas, (left + 10, 138), change_bp,
                 f"{change_bp:+.0f} 基点" if change_bp is not None else "—", "比1月前")
    canvas.text((left + 12, 151), "3个月 · 虚线=1月前", 8, "muted", anchor="lm", bold=False)
    sparkline(canvas, (left + 12, 158, right - 12, 176), ten.get("series"), baseline=ten.get("month_ago"))
    rest = [f"{label} {quotes[symbol]['last']:.2f}" for symbol, label in CURVE_POINTS
            if symbol != "^TNX" and symbol in quotes and finite(quotes[symbol].get("last")) is not None]
    canvas.text((left + 10, 187), " · ".join(rest), 11, "muted", anchor="lm", bold=False, width=right - left - 20)


def _range_position(quote):
    """(low, high, share of the way from low to high) of a quote's 52-week range, or None."""
    low, high, last = (finite((quote or {}).get(key)) for key in ("low_52w", "high_52w", "last"))
    if low is None or high is None or last is None or not low < high:
        return None
    return low, high, min(1.0, max(0.0, (last - low) / (high - low)))


def _range_track(canvas, x0, x1, y, share, accent):
    """A slim grey 52-week track with the current value as a dot in the card's colour."""
    colour = canvas.color(accent) if isinstance(accent, str) else accent
    canvas.draw.rounded_rectangle((x0, y - 1.5, x1, y + 1.5), radius=1, fill=canvas.color("rule"))
    for x in (x0, x1):
        canvas.draw.line((x, y - 3, x, y + 3), fill=canvas.color("rule"), width=1)
    x = x0 + (x1 - x0) * share
    canvas.draw.ellipse((x - 4, y - 4, x + 4, y + 4), fill=colour, outline=canvas.color("paper"), width=1)


def _range_block(canvas, span, top, quote, fmt, accent):
    """The 52-week range stacked in a block: the low and high in ink with a small 52周 between them,
    over the track. The 10-year, dollar index and gold cards all show it this way."""
    position = _range_position(quote)
    x0, x1 = span
    if position is None or x1 - x0 < 60:
        return
    low, high, share = position
    canvas.text((x0, top), fmt.format(low), 10, anchor="lm", bold=False)
    canvas.text((x1, top), fmt.format(high), 10, anchor="rm", bold=False)
    canvas.text(((x0 + x1) / 2, top), "52周", 9, "muted", anchor="mm", bold=False)
    _range_track(canvas, x0 + 2, x1 - 2, top + 12, share, accent)


def _curve(canvas, payload):
    left, top, right, bottom = CURVE_CARD
    canvas.draw.rounded_rectangle((left, top, right, bottom), radius=6, outline=canvas.color("rule"), width=1)
    _accent_bar(canvas, (left, top, right, bottom), CARD_ACCENTS["yield"])
    _card_doodles(canvas, (left, top, right, bottom), "yield", CARD_ACCENTS["yield"])
    icon = _asset("macro-curve", MACRO_ICON_SIZE)
    title_x = left + 10 + (_paste(canvas, icon, (left + 9, top + 18 - icon.height // 2)) + 6 if icon else 0)
    canvas.text((title_x, top + 18), "美债收益率曲线", 15, anchor="lm")
    quotes = payload["markets"]["data"].get("quotes") or {}
    points = [(label, quotes.get(symbol) or {}) for symbol, label in CURVE_POINTS]
    # The legend under the title: today's line and last month's, then how the colours read.
    legend_x, legend_y = left + 10, top + 36
    for label, color, dashed in (("今天", "muted", False), ("一个月前", "past", True)):
        if dashed:
            for x in range(int(legend_x), int(legend_x) + 16, 5):
                canvas.draw.line((x, legend_y, x + 2, legend_y), fill=canvas.color(color), width=2)
        else:
            canvas.draw.line((legend_x, legend_y, legend_x + 16, legend_y), fill=canvas.color(color), width=2)
        legend_x += 20 + canvas.text((legend_x + 20, legend_y), label, 10, "muted", anchor="lm", bold=False) + 10
    canvas.text((right - 10, legend_y), "绿升红降 · 非利好判断", 9, "muted", anchor="rm", bold=False)
    if sum(finite(quote.get("last")) is not None for _label, quote in points) < 2:
        canvas.text(((left + right) / 2, (top + bottom) / 2 + 8), "收益率暂不可用", 14, "muted", anchor="mm")
        return
    values = [finite(quote.get(key)) for _label, quote in points for key in ("last", "month_ago")]
    values = [value for value in values if value is not None]
    low, high = min(values) - 0.1, max(values) + 0.1
    plot = (left + 44, top + 57, right - 16, bottom - 22)

    def y_of(value):
        return plot[3] - (plot[3] - plot[1]) * (value - low) / (high - low)

    for step in range(3):
        value = low + (high - low) * step / 2
        y = y_of(value)
        canvas.rule((plot[0], y, plot[2], y))
        canvas.text((plot[0] - 6, y), f"{value:.1f}%", 11, "muted", anchor="rm", bold=False)
    xs = [plot[0] + (plot[2] - plot[0]) * index / (len(points) - 1) for index in range(len(points))]
    past = [(x, y_of(quote["month_ago"])) if finite(quote.get("month_ago")) is not None else None
            for x, (_label, quote) in zip(xs, points)]
    for start, end in zip(past, past[1:]):
        if start is None or end is None:
            continue
        (x1, y1), (x2, y2) = start, end
        steps = max(1, int(abs(x2 - x1) // 6))
        for index in range(0, steps, 2):
            a, b = index / steps, min(1, (index + 1) / steps)
            canvas.draw.line((x1 + (x2 - x1) * a, y1 + (y2 - y1) * a, x1 + (x2 - x1) * b, y1 + (y2 - y1) * b),
                             fill=canvas.color("past"), width=2)
    now_points = [(x, y_of(quote["last"])) if finite(quote.get("last")) is not None else None
                  for x, (_label, quote) in zip(xs, points)]
    changes = [delta(quote.get("last"), quote.get("month_ago")) for _label, quote in points]
    draw_segments(canvas, now_points, changes, width=3)
    for x, point, (label, quote), change in zip(xs, now_points, points, changes):
        if point is not None:
            _x, y = point
            tone = _direction(change)[1]
            canvas.draw.ellipse((x - 4, y - 4, x + 4, y + 4), fill=canvas.color(tone))
            canvas.text((x, y - 8), f"{quote['last']:.2f}", 11, tone, anchor="mb", stroke="paper")
        canvas.text((x, bottom - 10), label, 12, anchor="mm", bold=False)


def _gauge_point(centre, radius, score):
    angle = math.pi * (1 - score / 100)
    return centre[0] + radius * math.cos(angle), centre[1] - radius * math.sin(angle)


def _fear_greed(canvas, payload):
    """CNN's Fear & Greed Index as a half-dial over its five bands, the needle at today's score,
    with the band named beneath and where it stood a week and a month ago."""
    left, top, right, bottom = FEAR_GREED_CARD
    reading = (payload.get("fear_greed") or {}).get("data") or {}
    score = reading.get("score")
    band, name = fear_greed_band(score) if score is not None else (2, "")
    accent = FEAR_GREED_COLOURS[band]
    canvas.draw.rounded_rectangle((left, top, right, bottom), radius=6, outline=canvas.color("rule"), width=1)
    _accent_bar(canvas, (left, top, right, bottom), accent)
    canvas.text((left + 9, top + 18), "恐惧贪婪", 14, anchor="lm")
    logo = load_source_logo(ASSETS / "logos" / "cnn.png", 11)
    if logo is not None:
        canvas.image.paste(logo, (right - 8 - logo.width, top + 18 - logo.height // 2), logo)
    if score is None:
        canvas.text(((left + right) / 2, (top + bottom) / 2 + 8), "暂不可用", 13, "muted", anchor="mm")
        return
    centre, radius = ((left + right) / 2, top + 76), 42
    box = (centre[0] - radius, centre[1] - radius, centre[0] + radius, centre[1] + radius)
    for colour, start, end in zip(FEAR_GREED_COLOURS, FEAR_GREED_EDGES, FEAR_GREED_EDGES[1:]):
        # PIL's angles run clockwise from 3 o'clock: the dial's left end is 180 degrees.
        canvas.draw.arc(box, 180 + 180 * start / 100 + 1, 180 + 180 * end / 100 - 1, fill=colour, width=9)
    tip = _gauge_point(centre, radius - 12, score)
    canvas.draw.line((centre, tip), fill=canvas.color("ink"), width=3)
    canvas.draw.ellipse((centre[0] - 5, centre[1] - 5, centre[0] + 5, centre[1] + 5), fill=canvas.color("ink"))
    # Under the hub: the score and its band, side by side.
    value = f"{score:.0f}"
    pill_w = canvas.width(name, 11) + 14
    row_w = canvas.width(value, 20) + 6 + pill_w
    x = centre[0] - row_w / 2
    middle = centre[1] + 20
    x += canvas.text((x, middle), value, 20, anchor="lm") + 6
    canvas.draw.rounded_rectangle((x, middle - 8, x + pill_w, middle + 8), radius=8, fill=accent)
    canvas.text((x + pill_w / 2, middle), name, 11, "badge_ink", anchor="mm")
    past = [f"{label} {value:.0f}" for label, value in (("周前", reading.get("week")), ("月前", reading.get("month")))
            if value is not None]
    if past:
        canvas.text((centre[0], bottom - 9), " · ".join(past), 10, "muted", anchor="mm", bold=False,
                    width=right - left - 10)


def _macro_tiles(canvas, payload):
    macro = payload["macro"]["data"]
    quotes = payload["markets"]["data"].get("quotes") or {}
    tiles = []
    for key, label, unit in (("cpi", "CPI 同比", "%"), ("unemployment", "失业率", "%")):
        item = macro.get(key)
        if item and finite(item.get("value")) is not None:
            tiles.append((key, label, f"{item['value']:.1f}{unit}", item.get("previous"), item["value"],
                          (f"{str(item.get('period'))[-2:].lstrip('0')} 月", macro.get("source", "")), None))
    if macro.get("fed_funds") or macro.get("mortgage30"):
        for key, label in (("fed_funds", "联邦基金利率"), ("mortgage30", "30 年房贷利率")):
            item = macro.get(key)
            if item and finite(item.get("value")) is not None:
                tiles.append((key, label, f"{item['value']:.2f}%", item.get("previous"), item["value"], ("", "FRED"),
                              None))
    else:
        # Each with its 52-week range in the same units (gold's without the dollar sign, to fit).
        for key, symbol, label, fmt, range_fmt in (("dxy", "DX-Y.NYB", "美元指数", "{:.1f}", "{:.1f}"),
                                                    ("gold", "GC=F", "黄金", "${:,.0f}", "{:,.0f}")):
            quote = quotes.get(symbol)
            if quote and finite(quote.get("last")) is not None:
                tiles.append((key, label, fmt.format(quote["last"]), quote.get("month_ago"), quote["last"], ("比1月前", ""),
                              (quote, range_fmt) if _range_position(quote) else None))
    for index, (key, label, value, previous, current, note, ranged) in enumerate(tiles[:4]):
        column, row = index % 2, index // 2
        left = 400 + column * 197
        top = 204 + row * 64
        canvas.draw.rounded_rectangle((left, top, left + 191, top + 58), radius=6, outline=canvas.color("rule"))
        _accent_bar(canvas, (left, top, left + 191, top + 58), CARD_ACCENTS.get(key), 5)
        _card_doodles(canvas, (left, top, left + 191, top + 58), key, CARD_ACCENTS.get(key, "ink"), 5)
        title_y = top + 15 if ranged else top + 18
        # Each indicator carries its own picture (cart, briefcase, dollar coin, gold bars, bank, house).
        icon = _asset(f"macro-{key}", MACRO_ICON_SIZE)
        label_x = left + 10 + (_paste(canvas, icon, (left + 9, title_y - icon.height // 2)) + 5 if icon else 0)
        canvas.text((label_x, title_y), label, 13, anchor="lm", width=left + 118 - label_x)
        _tile_note(canvas, left + 181, title_y, *note)
        change = delta(current, previous)
        icon, tone = _direction(change)
        if change is None:
            figure = "—"
        elif key in {"cpi", "unemployment", "fed_funds", "mortgage30"}:
            figure = f"{change:+.2f}pp"
        elif key == "gold":
            figure = f"{'+' if change >= 0 else '-'}${abs(change):,.0f}"
        else:
            figure = f"{change:+.2f}点"
        if ranged:
            # The figure (smaller than the taller cards' 22 px, so title, figure and change sit evenly
            # in the short card) over its change on the left; the 52-week block on the right, its
            # numbers on the figure's line and its track on the change's, as on the 10-year card.
            figure_right = left + 10 + _number(canvas, (left + 10, top + 32), value, RANGED_FIGURE_SIZE, 84, tone)
            draw_icon(canvas.draw, icon, (left + 10, top + 43), 9, canvas.color(tone), canvas.color("paper"))
            _number(canvas, (left + 22, top + 47), figure, 12, 72, tone)
            _range_block(canvas, (max(figure_right + 8, left + 96), left + 181), top + 32, ranged[0], ranged[1],
                         CARD_ACCENTS.get(key, "ink"))
            continue
        _number(canvas, (left + 10, top + 41), value, 22, 102, tone)
        draw_icon(canvas.draw, icon, (left + 115, top + 35), 10, canvas.color(tone), canvas.color("paper"))
        _number(canvas, (left + 129, top + 40), figure, 13, 54, tone)
        if key in {"cpi", "unemployment", "fed_funds", "mortgage30"}:
            canvas.text((left + 154, top + 52), "较前期" if key in {"cpi", "unemployment"} else "较历史基准",
                        7, "muted", anchor="mm", bold=False)
    if not tiles:
        canvas.text((594, 268), "宏观数据暂不可用", 14, "muted", anchor="mm")


def _tile_note(canvas, right, middle, text, source):
    """A tile's note right-aligned at ``right``: its words, then its source's official logo (the
    source's name in text where there is no logo)."""
    key = TILE_SOURCE_LOGOS.get(str(source or "").upper())
    night = relative_luminance(canvas.color("paper")) < 0.3
    logo = load_source_logo(ASSETS / "logos" / f"{key}.png", TILE_SOURCE_LOGO_HEIGHT,
                            canvas.color("ink") if night else None, dark_parts=night) if key else None
    if logo is not None:
        _paste(canvas, logo, (right - logo.width, middle - logo.height // 2))
        right -= logo.width + 4
    elif source:
        text = " · ".join(part for part in (text, source) if part)
    if text:
        canvas.text((right, middle), text, 11, "muted", anchor="rm", bold=False, width=80)


def chip_style(canvas, symbol):
    """(fill, text, outline) colours of a ticker's chip in this theme."""
    fill, text, outline = BRAND_CHIPS.get(str(symbol or "").upper(), DEFAULT_CHIP)
    return canvas.color(fill), canvas.color(text), canvas.color(outline) if outline else None


def _draw_chip(canvas, box, symbol, radius):
    fill, text, outline = chip_style(canvas, symbol)
    canvas.draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=1 if outline else 0)
    centre = ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
    canvas.text(centre, symbol or "—", 10, text, anchor="mm", width=box[2] - box[0] - 4)


def _news(canvas, items, now, left, right, top, rows):
    from .news_rows import news
    return news(canvas, items, now, left, right, top, rows)


def _rotating_card(canvas, panel, payload, now):
    from .extra_panels import draw_card
    draw_card(canvas, panel, payload.get("extras"), now)


def _weekday_date(day):
    value = date.fromisoformat(day)
    return f"{value:%m/%d} 周{WEEKDAYS[value.weekday()]}"


def _earnings(canvas, payload, now, left, right, top, rows):
    """Up to EARNINGS_COLUMNS * ``rows`` coming earnings in columns side by side; returns the rows used."""
    data = payload["earnings"]["data"]
    items = upcoming_earnings(data.get("items"), now)[:rows * EARNINGS_COLUMNS]
    if not items:
        canvas.text((left, top), f"{_earnings_window(data)}持仓无财报", 13, "muted", anchor="lm")
        return 1
    width = (right - left - EARNINGS_COLUMN_GAP * (EARNINGS_COLUMNS - 1)) / EARNINGS_COLUMNS
    calendar = _calendar_icon()
    lead = (calendar.width if calendar is not None else 14) + 4
    cells = []
    for item in items:
        remaining = days_until(item["date"], now)
        cells.append((item, _weekday_date(item["date"]), "今天" if remaining == 0 else f"{remaining}天后",
                      EARNINGS_HOURS.get(item.get("hour"), "")))

    def room(day, when, symbol, hour):
        tag = canvas.width(hour, 11, bold=False) + 3 if hour else 0
        return (lead + canvas.width(day, 11) + 4 + HOLDING_LOGO_SLOT + 1 + canvas.width(symbol, 12) + tag + 4
                + canvas.width(when, 11, bold=False))

    # The dates keep their weekday only when every cell has room for it, so the columns read alike.
    weekday = all(room(day, when, item["symbol"], hour) <= width for item, day, when, hour in cells)
    for index, (item, day, when, hour) in enumerate(cells):
        column, row = divmod(index, rows)
        x0, y = left + column * (width + EARNINGS_COLUMN_GAP), top + row * 19
        x1 = x0 + width
        if column:
            canvas.rule((x0 - EARNINGS_COLUMN_GAP / 2, y - 8, x0 - EARNINGS_COLUMN_GAP / 2, y + 8))
        if calendar is not None:
            canvas.image.paste(calendar, (round(x0), round(y - calendar.height / 2)), calendar)
        else:
            draw_icon(canvas.draw, "calendar", (x0, y - 7), 13, canvas.color("ink"), canvas.color("paper"))
        x = x0 + lead
        end = x1 - canvas.text((x1, y), when, 11, "muted", anchor="rm", bold=False) - 4
        x += canvas.text((x, y), day if weekday else day.split()[0], 11, anchor="lm") + 4
        logo = _holding_logo(canvas, item["symbol"], HOLDING_LOGO_SIZE)
        if logo is not None:
            canvas.image.paste(logo, (round(x + (HOLDING_LOGO_SLOT - logo.width) / 2), round(y - logo.height / 2)), logo)
            x += HOLDING_LOGO_SLOT + 1
        x += canvas.text((x, y), item["symbol"], 12, anchor="lm", width=end - x) + 3
        if hour and x + canvas.width(hour, 11, bold=False) <= end:
            canvas.text((x, y), hour, 11, "muted", anchor="lm", bold=False)
    return min(len(items), rows)


def _earnings_window(data):
    """How far ahead the earnings list looks: Nasdaq's coming trading days, or Finnhub's 45 days."""
    days = data.get("days")
    return f"近 {days} 个交易日" if data.get("source") == "nasdaq" and isinstance(days, int) else "45 天内"


def _earnings_rows(payload, now):
    """Rows the earnings take in the holdings card: as many as their columns need, at most
    EARNINGS_ROWS, and one for the 'none due' line."""
    count = len(upcoming_earnings((payload["earnings"]["data"] or {}).get("items"), now))
    return max(1, min(EARNINGS_ROWS, math.ceil(count / EARNINGS_COLUMNS)))


def _calendar_icon():
    """The generated calendar icon CALENDAR_ICON_HEIGHT tall, or None without the file."""
    path = ASSETS / "icon-calendar.png"
    if not path.is_file():
        return None
    with safe_open_image(path, limits=_LIMITS) as source:
        image = source.convert("RGBA")
    return image.resize((max(1, round(image.width * CALENDAR_ICON_HEIGHT / image.height)), CALENDAR_ICON_HEIGHT),
                        Image.Resampling.LANCZOS)


def holding_logo_name(symbol):
    """The stock tracker's logo file stem for a ticker: BRK.B, BRK-B and BRK/B all name Berkshire."""
    symbol = re.sub(r"[-/ ]", ".", str(symbol or "").strip().upper())
    return HOLDING_LOGO_ALIASES.get(symbol, symbol.lower())


def _holding_logo(canvas, symbol, size, width=HOLDING_LOGO_SLOT):
    """The company's mark as the Robinhood holdings page shows it, at most ``size`` tall and
    ``width`` wide, its black parts in the night ink; None without one."""
    name = holding_logo_name(symbol)
    path = HOLDING_LOGOS / f"{name}.png"
    ink = canvas.color("ink") if canvas.color("paper") == _rgb(NIGHT["paper"]) else None
    dark_parts = ink is not None and name in NIGHT_INKED_HOLDING_LOGOS
    logo = load_source_logo(path, size, ink, dark_parts=dark_parts)
    if logo is not None and logo.width > width:
        logo = load_source_logo(path, max(1, round(size * width / logo.width)), ink, dark_parts=dark_parts)
    return logo


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
    _card_doodles(canvas, (left, top, right, bottom), "holdings", CARD_ACCENTS["holdings"], 4)
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
    calendar = "Nasdaq" if (payload["earnings"]["data"] or {}).get("source") == "nasdaq" else "Finnhub"
    detail = " · ".join([origin, *hint]) if hint else f"{origin} · Yahoo · {calendar} · SEC"
    detail_width = canvas.text((right - 8, top + 15), detail, 11, "muted", anchor="rm", bold=False)
    for symbol in payload["symbols"]:
        # Each holding by its company's logo alone; a ticker chip only where there is no logo.
        logo = _holding_logo(canvas, symbol, HEADER_HOLDING_LOGO_SIZE, HEADER_HOLDING_LOGO_WIDTH)
        width = logo.width if logo is not None else canvas.width(symbol, 10) + 10
        if x + width > right - 16 - detail_width:
            break
        if logo is not None:
            canvas.image.paste(logo, (round(x), round(top + 15 - logo.height / 2)), logo)
        else:
            _draw_chip(canvas, (x, top + 8, x + width, top + 22), symbol, 7)
        x += width + HEADER_HOLDING_GAP
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
        # Two earnings rows head the left half (one line when none is due); the news reads on below
        # them and down the right half.
        earnings_rows = _earnings_rows(payload, now)
        _earnings(canvas, payload, now, left + 10, middle - 10, body, earnings_rows)
        split = body + earnings_rows * 19 - 10
        canvas.rule((left + 8, split, middle - 8, split))
        canvas.rule((middle, body - 8, middle, bottom - 8))
        below = rows - earnings_rows
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


def _source_logos(canvas, left, right, night, earnings_source=None):
    """The sources' logos in one centred row between ``left`` and ``right``, as tall as the row
    fits; nothing when even the smallest do not. Returns the row's (left, right), or None."""
    ink = canvas.color("ink") if night else None
    calendar = EARNINGS_SOURCE_LOGOS.get(earnings_source, "finnhub")
    paths = {name: (SOURCE_LOGO_DIR if name in SHARED_SOURCE_LOGOS else ASSETS / "logos") / f"{name}.png"
             for name in (calendar if name == "finnhub" else name for name in SOURCE_LOGO_ROW)}
    # The row's width at each height follows from the logos' proportions, so only the height
    # that fits is drawn.
    for height in SOURCE_LOGO_HEIGHTS:
        widths = [width for width in (source_logo_width(path, height) for path in paths.values()) if width is not None]
        if widths and sum(widths) + SOURCE_LOGO_GAP * (len(widths) - 1) <= right - left:
            break
    else:
        return None
    row = [load_source_logo(path, height, ink if name in SOURCE_LOGO_INKED else None,
                            dark_parts=SOURCE_LOGO_INKED.get(name, False)) for name, path in paths.items()]
    return paste_header_logo_row(canvas.image, row, left, right, 28, SOURCE_LOGO_GAP, snap=False)


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
    date_left = 788 - canvas.text((788, 27), f"{now:%m.%d} 周{WEEKDAYS[now.weekday()]} {now:%H:%M}", 16, anchor="rm")
    # Between the title and the date: where the page's figures come from, by the sources' own logos.
    canvas.image.info["source_logos"] = _source_logos(
        canvas, title_right + 16, date_left - 16, night, ((payload.get("earnings") or {}).get("data") or {}).get("source"))
    canvas.rule((12, 52, 788, 52), "ink", 2)
    _fx_tile(canvas, payload, TILES[0])
    _crypto_tiles(canvas, payload)
    _yield_tile(canvas, payload, TILES[3])
    # The yield curve's card takes turns with the VIX futures curve and China's markets (the
    # 10-year card above keeps the curve's other points whichever is up).
    card = extras.shown_panel(payload.get("extras"), now)
    if card == "curve":
        _curve(canvas, payload)
    else:
        _rotating_card(canvas, card, payload, now)
    _fear_greed(canvas, payload)
    _macro_tiles(canvas, payload)
    _holdings(canvas, payload, now)
    result = canvas.image
    if tuple(dimensions) != (800, 480):
        result = result.resize(tuple(dimensions), Image.Resampling.LANCZOS)
    return result
