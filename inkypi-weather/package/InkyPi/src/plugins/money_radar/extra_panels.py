"""What Money Radar's rotating feeds draw (extras.py says whose turn it is), in the yield-curve
card's place: the VIX futures curve under the day's put/call ratios, or China's five indices over
the yuan's central parity. Every figure is ink; colour is on the bars, dots and lines.
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
