"""What Money Radar's rotating feeds draw (extras.py says whose turn it is).

In the yield-curve card's place: the VIX futures curve under the day's put/call ratios, or China's
five indices over the yuan's central parity. In the holdings card's right half: a titled list of
the market wire, this week's US releases, r/wallstreetbets' most mentioned tickers or the coming
IPO pricings. Every figure is ink; colour is on the bars, dots and lines.
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
# The lists: a title line, then LIST_ROWS rows LIST_PITCH apart (the holdings news' own pitch);
# (title, source note, colour of the title's bar).
LIST_PITCH, LIST_ROWS = 19, 4
LISTS = {
    "wire": ("财经快讯", "华尔街见闻 7×24", "accent"),
    "calendar": ("本周美国数据", "ForexFactory · 太平洋时间", render.KIND_COLOURS["宏观"]),
    "chatter": ("散户热议", "r/wallstreetbets · 24 小时提及", render.KIND_COLOURS["加密"]),
    "ipo": ("新股定价", "Nasdaq IPO 日历", render.KIND_COLOURS["商品"]),
}
IMPACT_COLOURS = {"High": render.KIND_COLOURS["宏观"], "Medium": render.KIND_COLOURS["商品"]}
CHATTER_COLUMNS, CHATTER_BAR = 2, 38


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
    lane = (left + 138, left + 182)
    middle = (lane[0] + lane[1]) / 2
    for index, row in enumerate(extras.feed_data(loaded, "cn_indices")["indices"][:5]):
        y = top + 38 + index * 15
        canvas.text((left + 10, y), row["name"], 12, anchor="lm")
        canvas.text((left + 130, y), f"{row['last']:,.2f}", 12, anchor="rm")
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


CARDS = {"volatility": _volatility, "china": _china}


def draw_card(canvas, panel, loaded, now):
    CARDS[panel](canvas, loaded, now)


def _rules(canvas, left, right, top, count):
    for index in range(count - 1):
        y = top + index * LIST_PITCH + 9
        canvas.draw.line((left, y, right, y), fill=canvas.color("rule"), width=1)


def _wire(canvas, loaded, now, left, right, top):
    """The newest items, each led by its time; a dot marks the ones the wire rates important."""
    items = extras.recent_wire(extras.feed_data(loaded, "wire"), now, LIST_ROWS)
    for index, item in enumerate(items):
        y = top + index * LIST_PITCH
        stamp = extras.parse_time(item["time"]).astimezone(now.tzinfo)
        if item.get("important"):
            canvas.draw.ellipse((left, y - 3, left + 6, y + 3), fill=_colour(canvas, LISTS["wire"][2]))
        news_rows.text(canvas, (left + 10, y), f"{stamp:%H:%M}", "time", 11)
        news_rows.text(canvas, (left + 46, y), item["text"], "title", 13, width=right - left - 46)
    _rules(canvas, left, right, top, len(items))


def _when(stamp, now):
    days = (stamp.date() - now.date()).days
    day = "今天" if days == 0 else "明天" if days == 1 else f"周{render.WEEKDAYS[stamp.weekday()]}"
    return f"{day} {stamp:%H:%M}"


def _calendar(canvas, loaded, now, left, right, top):
    """The coming releases: a dot for the impact (red high, gold medium), the day and Pacific time,
    the release's name, then what is expected and what it was before."""
    events = extras.upcoming_events(extras.feed_data(loaded, "calendar"), now, LIST_ROWS)
    for index, event in enumerate(events):
        y = top + index * LIST_PITCH
        stamp = extras.parse_time(event["time"]).astimezone(now.tzinfo)
        canvas.draw.ellipse((left, y - 3, left + 6, y + 3), fill=IMPACT_COLOURS.get(event["impact"], canvas.color("rule")))
        news_rows.text(canvas, (left + 10, y), _when(stamp, now), "time", 11)
        figures = " · ".join(f"{label} {event[key]}" for label, key in (("预", "forecast"), ("前", "previous"))
                             if event.get(key))
        room = right
        if figures:
            room -= news_rows.text(canvas, (right, y), figures, "time", 11, "ink", "right") + 8
        news_rows.text(canvas, (left + 74, y), extras.event_name(event["title"]), "title", 13,
                       width=max(8, room - left - 74))
    _rules(canvas, left, right, top, len(events))


def _chatter(canvas, loaded, _now, left, right, top):
    """The most mentioned tickers in two columns: rank, ticker, a bar and the count of the day's
    mentions, then the places climbed or lost since yesterday (新 for a newcomer)."""
    tickers = extras.feed_data(loaded, "chatter")["tickers"][:LIST_ROWS * CHATTER_COLUMNS]
    gap = 12
    width = (right - left - gap * (CHATTER_COLUMNS - 1)) / CHATTER_COLUMNS
    most = max(row["mentions"] for row in tickers) or 1
    colour = _colour(canvas, LISTS["chatter"][2])
    for index, row in enumerate(tickers):
        column, line = divmod(index, LIST_ROWS)
        x0 = left + column * (width + gap)
        x1, y = x0 + width, top + line * LIST_PITCH
        if column and not line:
            canvas.rule((x0 - gap / 2, y - 8, x0 - gap / 2, top + (LIST_ROWS - 1) * LIST_PITCH + 8))
        news_rows.text(canvas, (x0 + 12, y), str(row["rank"]), "time", 11, "ink", "right")
        news_rows.text(canvas, (x0 + 18, y), row["ticker"], "ticker", 12, width=46)
        bar = max(2, CHATTER_BAR * row["mentions"] / most)
        canvas.draw.rectangle((x0 + 66, y - 3, x0 + 66 + bar, y + 3), fill=colour)
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
    for column in range(CHATTER_COLUMNS):
        x0 = left + column * (width + gap)
        _rules(canvas, x0, x0 + width, top, min(LIST_ROWS, len(tickers) - column * LIST_ROWS))


def _ipo(canvas, loaded, now, left, right, top):
    """The coming pricings, nearest first: date, ticker, company, the price range and the raise."""
    rows = extras.upcoming_ipos(extras.feed_data(loaded, "ipo"), now, LIST_ROWS)
    for index, row in enumerate(rows):
        y = top + index * LIST_PITCH
        news_rows.text(canvas, (left, y), row["date"][5:].replace("-", "/"), "time", 11)
        news_rows.text(canvas, (left + 36, y), row["symbol"], "ticker", 12, width=46)
        room = right
        amount = extras.money_short(row.get("amount"))
        if amount:
            news_rows.text(canvas, (right, y), amount, "time", 11, "ink", "right")
        room -= 52
        if row.get("price"):
            room -= news_rows.text(canvas, (room, y), row["price"], "title", 12, "ink", "right") + 8
        news_rows.text(canvas, (left + 86, y), row["name"], "title", 13, width=max(8, room - left - 86))
    _rules(canvas, left, right, top, len(rows))


ROWS = {"wire": _wire, "calendar": _calendar, "chatter": _chatter, "ipo": _ipo}


def draw_list(canvas, panel, loaded, now, left, right, top):
    """``panel``'s list from ``top`` (its title line's middle) in the holdings card's right half:
    a bar in its colour, its name and where it comes from, then its rows."""
    title, note, colour = LISTS[panel]
    canvas.draw.rectangle((left, top - 6, left + 3, top + 6), fill=_colour(canvas, colour))
    news_rows.text(canvas, (left + 9, top), title, "title", 13)
    news_rows.text(canvas, (right, top), note, "time", 10, "muted", "right")
    canvas.draw.line((left, top + 9, right, top + 9), fill=canvas.color("rule"), width=1)
    ROWS[panel](canvas, loaded, now, left, right, top + LIST_PITCH)
