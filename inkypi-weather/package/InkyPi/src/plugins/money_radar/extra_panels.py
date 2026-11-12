"""What Money Radar's rotating feeds draw (extras.py says whose turn it is), in the yield-curve
card's place: the VIX futures curve under the day's put/call ratios, or China's five indices over
the yuan's central parity, each index led by its exchange's mark. And in the holdings card's right
half, in turn with the holdings news: r/wallstreetbets' most mentioned tickers, each led by its
company's logo. Every figure is ink; colour is on the bars, dots, lines and logos.
"""

from utils.flat_icons import draw_icon

from . import extras, news_rows, render


# The cards' colours, from the page's own: the periwinkle of its Ethereum card for volatility, its
# macro red for China's markets.
VOLATILITY_COLOUR = render.CARD_ACCENTS["ethereum"]
CHINA_COLOUR = render.KIND_COLOURS["宏观"]
PUT_CALL_LABELS = (("total", "总"), ("equity", "股票"), ("index", "指数"))
# An index's change as a bar from the middle of its lane, full at this many percent.
CHINA_BAR_FULL = 3.0
# Each index by the mark of the exchange (or index company) that publishes it (assets/logos), in
# a slot this wide before its name.
INDEX_MARKS = {"上证": "logos/index-sse", "深成": "logos/index-szse", "创业板": "logos/index-szse",
               "恒生": "logos/index-hsi", "恒生科技": "logos/index-hsi"}
INDEX_MARK_SIZE, INDEX_MARK_SLOT = 13, 17
# The chatter list: a title line, then LIST_ROWS rows a column at LIST_PITCH, in CHATTER_COLUMNS
# columns; each ticker's logo in a slot this wide, its mentions as a bar at most CHATTER_BAR long.
LIST_ROWS, LIST_PITCH = 4, 19
CHATTER_TITLE = ("散户热议", "r/wallstreetbets · 24 小时提及", render.KIND_COLOURS["加密"])
CHATTER_COLUMNS, CHATTER_BAR = 2, 24
CHATTER_LOGO_SIZE, CHATTER_LOGO_SLOT = 14, 20
# A logo part this close to the paper in lightness would vanish on it: it is drawn in the ink.
LOGO_MIN_CONTRAST = 1.6
# A logo this opaque over its whole box is a tile with its own ground, and is left as it is.
LOGO_TILE_COVER = 0.9


def _colour(canvas, value):
    return canvas.color(value) if isinstance(value, str) else value


def _card(canvas, colour, doodles):
    """The rotating card's frame in the yield-curve card's place, under its colour bar."""
    box = render.CURVE_CARD
    canvas.draw.rounded_rectangle(box, radius=6, outline=canvas.color("rule"), width=1)
    render._accent_bar(canvas, box, colour)
    render._card_doodles(canvas, box, doodles, colour)
    return box


def _volatility(canvas, loaded, _now):
    """The VIX futures' settlements from the nearest month out, as a curve: far above near is the
    calm shape (升水), near above far the stressed one (贴水). Over it, the day's put/call ratios."""
    left, top, right, bottom = _card(canvas, VOLATILITY_COLOUR, "yield")
    curve = extras.feed_data(loaded, "vix_curve")
    points = curve["points"]
    title_right = left + 10 + canvas.text((left + 10, top + 18), "VIX 期货曲线", 15, anchor="lm")
    spread = points[-1]["price"] - points[0]["price"]
    shape = "升水" if spread > 0 else "贴水" if spread < 0 else "持平"
    day = str(curve.get("date") or "")[5:].replace("-", "/")
    render._number(canvas, (right - 10, top + 18), f"远月{shape} {spread:+.2f} · {day} 结算", 11,
                   right - 16 - title_right, "muted", anchor="rm")
    ratios = extras.feed_data(loaded, "put_call").get("ratios") or {}
    if render.finite(ratios.get("total")) is not None:
        x = left + 10 + canvas.text((left + 10, top + 36), "Put/Call", 10, "muted", anchor="lm", bold=False) + 8
        for key, label in PUT_CALL_LABELS:
            if render.finite(ratios.get(key)) is None:
                continue
            x += canvas.text((x, top + 36), label, 10, "muted", anchor="lm", bold=False) + 3
            x += canvas.text((x, top + 36), f"{ratios[key]:.2f}", 11, anchor="lm") + 9
    prices = [point["price"] for point in points]
    low, high = min(prices) - 0.2, max(prices) + 0.2
    plot = (left + 40, top + 54, right - 16, bottom - 24)

    def y_of(value):
        return plot[3] - (plot[3] - plot[1]) * (value - low) / (high - low)

    for step in range(3):
        value = low + (high - low) * step / 2
        canvas.rule((plot[0], y_of(value), plot[2], y_of(value)))
        canvas.text((plot[0] - 6, y_of(value)), f"{value:.1f}", 11, "muted", anchor="rm", bold=False)
    xs = [plot[0] + (plot[2] - plot[0]) * index / (len(points) - 1) for index in range(len(points))]
    line = [(x, y_of(price)) for x, price in zip(xs, prices)]
    colour = VOLATILITY_COLOUR
    canvas.draw.line(line, fill=colour, width=3, joint="curve")
    # Every other month is named along the foot, and the far end always.
    named = set(range(0, len(points), 2))
    if len(points) - 1 not in named:
        named.discard(len(points) - 2)
        named.add(len(points) - 1)
    for index, ((x, y), point) in enumerate(zip(line, points)):
        canvas.draw.ellipse((x - 3, y - 3, x + 3, y + 3), fill=colour)
        if index in (0, len(points) - 1):
            # The ends carry their settlements, written inwards (21.325 rounds up, as it is quoted).
            canvas.text((x - 3 if index == 0 else x + 4, y - 7), f"{point['price'] + 1e-9:.2f}", 11,
                        anchor="lb" if index == 0 else "rb", stroke="paper")
        if index in named:
            canvas.text((x, bottom - 11), f"{point['month']}月", 11, anchor="mm", bold=False)


def _china(canvas, loaded, _now):
    """Shanghai, Shenzhen, ChiNext, the Hang Seng and its tech index: each one's last and its
    change as a bar and a figure; along the foot the yuan's central parity, its change on the day
    before and its recent run."""
    left, top, right, bottom = _card(canvas, CHINA_COLOUR, "fx")
    canvas.text((left + 10, top + 18), "中国市场", 15, anchor="lm")
    x = right - 10
    for name in ("flag-hk", "flag-cn"):
        flag = render._asset(name, 12)
        if flag is not None:
            x -= flag.width
            render._paste(canvas, flag, (x, top + 12))
            x -= 4
    lane = (left + 142, left + 180)
    middle = (lane[0] + lane[1]) / 2
    for index, row in enumerate(extras.feed_data(loaded, "cn_indices")["indices"][:5]):
        y = top + 38 + index * 15
        mark = render._asset(INDEX_MARKS[row["name"]], INDEX_MARK_SIZE) if row["name"] in INDEX_MARKS else None
        if mark is not None:
            render._paste(canvas, mark, (round(left + 9 + (INDEX_MARK_SLOT - 3 - mark.width) / 2), y - mark.height // 2))
        canvas.text((left + 9 + INDEX_MARK_SLOT, y), row["name"], 12, anchor="lm")
        canvas.text((left + 136, y), f"{row['last']:,.2f}", 12, anchor="rm")
        pct = row["pct"]
        reach = (lane[1] - middle) * min(abs(pct), CHINA_BAR_FULL) / CHINA_BAR_FULL
        canvas.draw.line((middle, y - 6, middle, y + 6), fill=canvas.color("rule"), width=1)
        if reach >= 1:
            tone = "up" if pct > 0 else "down"
            canvas.draw.rectangle((min(middle, middle + reach if pct > 0 else middle - reach), y - 4,
                                   max(middle, middle + reach if pct > 0 else middle - reach), y + 4),
                                  fill=canvas.color(tone))
        canvas.text((right - 10, y), f"{pct:+.2f}%", 11, anchor="rm")
    reading = extras.parity_reading(extras.feed_data(loaded, "parity"))
    if reading is None:
        return
    value, pips, rates = reading
    y = bottom - 12
    canvas.rule((left + 8, y - 10, right - 8, y - 10))
    x = left + 10 + canvas.text((left + 10, y), "中间价", 10, "muted", anchor="lm", bold=False) + 4
    x += canvas.text((x, y), f"{value:.4f}", 12, anchor="lm") + 6
    icon, tone = render._direction(pips)
    draw_icon(canvas.draw, icon, (x, y - 5), 9, canvas.color(tone), canvas.color("paper"))
    x += 12 + canvas.text((x + 12, y), f"{abs(pips):.0f}点", 10, anchor="lm", bold=False) + 8
    if right - 12 - x >= 30:
        render.sparkline(canvas, (x, y - 7, right - 14, y + 6), rates)


def _visible(logo, paper, ink):
    """``logo`` with the parts that would vanish on ``paper`` (a white mark by day, a black one by
    night) in ``ink``."""
    if sum(logo.getchannel("A").point(lambda value: 1 if value >= 128 else 0).getdata())             >= LOGO_TILE_COVER * logo.width * logo.height:
        return logo
    ground = render.relative_luminance(paper)
    pixels = logo.load()
    for y in range(logo.height):
        for x in range(logo.width):
            red, green, blue, alpha = pixels[x, y]
            if alpha < 8:
                continue
            lighter, darker = sorted((render.relative_luminance((red, green, blue)), ground), reverse=True)
            if (lighter + 0.05) / (darker + 0.05) < LOGO_MIN_CONTRAST:
                pixels[x, y] = (*ink[:3], alpha)
    return logo


def ticker_logo(canvas, row):
    """The ticker's logo CHATTER_LOGO_SIZE tall: the holdings' own mark where the page has one,
    else the one kept with the reading; None without either."""
    logo = render._holding_logo(canvas, row["ticker"], CHATTER_LOGO_SIZE, CHATTER_LOGO_SLOT - 2)
    if logo is not None:
        return logo
    kept = render._coin_logo(row.get("logo"))
    if kept is None:
        return None
    kept.thumbnail((CHATTER_LOGO_SLOT - 2, CHATTER_LOGO_SIZE), render.Image.Resampling.LANCZOS)
    return _visible(kept, tuple(canvas.color("paper")), tuple(canvas.color("ink")))


def _chatter(canvas, loaded, left, right, top):
    """The most mentioned tickers in two columns: rank, the company's logo, the ticker, a bar and
    the count of the day's mentions, then the places climbed or lost since yesterday (新 for a
    newcomer)."""
    tickers = extras.feed_data(loaded, "chatter")["tickers"][:LIST_ROWS * CHATTER_COLUMNS]
    gap = 12
    width = (right - left - gap * (CHATTER_COLUMNS - 1)) / CHATTER_COLUMNS
    most = max(row["mentions"] for row in tickers) or 1
    colour = _colour(canvas, CHATTER_TITLE[2])
    for index, row in enumerate(tickers):
        column, line = divmod(index, LIST_ROWS)
        x0 = left + column * (width + gap)
        x1, y = x0 + width, top + line * LIST_PITCH
        if column and not line:
            canvas.rule((x0 - gap / 2, y - 8, x0 - gap / 2, top + (LIST_ROWS - 1) * LIST_PITCH + 8))
        if line:
            canvas.draw.line((x0, y - 10, x1, y - 10), fill=canvas.color("rule"), width=1)
        news_rows.text(canvas, (x0 + 10, y), str(row["rank"]), "time", 11, "ink", "right")
        logo = ticker_logo(canvas, row)
        if logo is not None:
            canvas.image.paste(logo, (round(x0 + 15 + (CHATTER_LOGO_SLOT - logo.width) / 2), round(y - logo.height / 2)),
                               logo)
        news_rows.text(canvas, (x0 + 18 + CHATTER_LOGO_SLOT, y), row["ticker"], "ticker", 12, width=44)
        bar = max(2, CHATTER_BAR * row["mentions"] / most)
        canvas.draw.rectangle((x0 + 86, y - 3, x0 + 86 + bar, y + 3), fill=colour)
        move = extras.rank_move(row)
        if move is None:
            news_rows.text(canvas, (x1, y), "新", "time", 11, "ink", "right")
        elif move:
            reach = news_rows.text(canvas, (x1, y), str(abs(move)), "time", 11, "ink", "right")
            tone = "up" if move > 0 else "down"
            draw_icon(canvas.draw, tone, (x1 - reach - 11, y - 5), 9, canvas.color(tone), canvas.color("paper"))
        else:
            canvas.draw.line((x1 - 9, y, x1 - 1, y), fill=canvas.color("muted"), width=2)
        news_rows.text(canvas, (x1 - 30, y), f"{row['mentions']:,}", "time", 11, "ink", "right")


def draw_list(canvas, loaded, left, right, top):
    """The chatter list from ``top`` (its title line's middle) in the holdings card's right half:
    a bar in its colour, its name and where it comes from, then its rows."""
    title, note, colour = CHATTER_TITLE
    canvas.draw.rectangle((left, top - 6, left + 3, top + 6), fill=_colour(canvas, colour))
    news_rows.text(canvas, (left + 9, top), title, "title", 13)
    news_rows.text(canvas, (right, top), note, "time", 10, "muted", "right")
    canvas.draw.line((left, top + 9, right, top + 9), fill=canvas.color("rule"), width=1)
    _chatter(canvas, loaded, left, right, top + LIST_PITCH)


CARDS = {"volatility": _volatility, "china": _china}


def draw_card(canvas, panel, loaded, now):
    CARDS[panel](canvas, loaded, now)
