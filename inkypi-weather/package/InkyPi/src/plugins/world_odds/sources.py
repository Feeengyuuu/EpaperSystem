"""Bounded keyless prediction-market feeds for 世界赔率榜: Polymarket's busiest events by category
and Kalshi's US-economy markets.

Polymarket's Gamma API lists events by 24-hour volume within a tag; each event's markets are its
outcomes. Kalshi's public trade API lists a series' open events with their markets: the Fed's next
decision (one market per outcome), and thresholds ("CPI above 3.0%") whose middle the page reads as
the market's median expectation. Every fetcher returns a small normalised dict.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from utils.http_client import get_http_client


PACIFIC = ZoneInfo("America/Los_Angeles")
USER_AGENT = "InkyPi WorldOdds/1.0 (personal e-paper display)"
REQUEST_TIMEOUT = (3, 12)
POLYMARKET_EVENTS = "https://gamma-api.polymarket.com/events"
KALSHI_EVENTS = "https://api.elections.kalshi.com/trade-api/v2/events"
PRICE_HISTORY = "https://clob.polymarket.com/prices-history"
# A week of an outcome's price at one point every three hours.
HISTORY_MINUTES = 180
# Polymarket tags in the page's order, with their Chinese names.
CATEGORIES = {"politics": "政治", "geopolitics": "地缘", "economy": "经济", "tech": "科技", "crypto": "加密"}
EVENTS_PER_TAG = 4
MAX_OUTCOMES = 5
ON_THE_BOARD = re.compile(r"\bFed\b|\bFOMC\b|\bCPI\b|inflation|recession|rate (?:cut|hike)", re.I)
MONTHS = {name: number for number, name in enumerate(
    ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), start=1)}
QUARTERS = {"1": "一", "2": "二", "3": "三", "4": "四"}
IMAGE_HOSTS = ("polymarket-upload.s3.us-east-2.amazonaws.com",)
# Kalshi series on the page: (series, Chinese label, how a value reads).
KALSHI_SERIES = (
    ("KXFEDDECISION", "美联储议息", "decision"),
    ("KXRECSSNBER", "年内经济衰退", "yes"),
    ("KXCPIYOY", "CPI 同比", "percent"),
    ("KXGDP", "GDP 年化增速", "percent"),
    ("KXPAYROLLS", "非农新增就业", "jobs"),
    ("KXAAAGASM", "月底全美油价", "gas"),
    ("KXINXY", "标普 500 年末", "index"),
    ("KXBTCD", "比特币今日收盘", "btc"),
)
# A threshold or range row keeps at most this many points for its little distribution chart.
LADDER_POINTS = 40
# Kalshi's Fed outcomes, in Chinese, from the deepest cut to the steepest hike.
FED_OUTCOMES = {"Cut >25bps": "降息>25", "Cut 25bps": "降息25", "Fed maintains rate": "维持", "Hike 0bps": "维持",
                "Hike 25bps": "加息25", "Hike >25bps": "加息>25"}
FED_ORDER = ("降息>25", "降息25", "维持", "加息25", "加息>25")


def local_now(now=None):
    value = now or datetime.now(PACIFIC)
    return value.replace(tzinfo=PACIFIC) if value.tzinfo is None else value.astimezone(PACIFIC)


def _clean(text, limit):
    return " ".join(str(text or "").split())[:limit]


def _float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_image_url(value):
    url = str(value or "").strip()
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.hostname not in IMAGE_HOSTS:
        return None
    return url


def _get(url, context, *, max_bytes, params=None):
    context.raise_if_cancelled()
    response = get_http_client().request_json(
        "GET", url, context=context, timeout=REQUEST_TIMEOUT, max_bytes=max_bytes, params=params,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    context.raise_if_cancelled()
    return response.data


# --- Polymarket ------------------------------------------------------------------------------------

def _prices(market):
    """A Polymarket market's outcome prices (its outcomePrices is a JSON string)."""
    raw = market.get("outcomePrices")
    try:
        values = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return []
    return [_float(value) for value in values or []]


def _labels(market):
    raw = market.get("outcomes")
    try:
        values = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return []
    return [str(value) for value in values or []]


def normalize_polymarket_event(event, category):
    """{"title", "category", "volume_24h", "volume", "ends", "image", "outcomes": [{name, chance, change}]}.

    A multi-market event's outcomes are its markets (each one's Yes price); a single market's are
    its own outcomes (Yes / No, or the two sides of a match). The likeliest come first."""
    markets = [market for market in event.get("markets") or [] if isinstance(market, dict)
               and not market.get("closed") and market.get("active", True)]
    outcomes = []
    if len(markets) > 1:
        for market in markets:
            prices = _prices(market)
            name = _clean(market.get("groupItemTitle") or market.get("question"), 40)
            if prices and name:
                # A candidate's own picture (a portrait, a party's mark) when the event gives one.
                image = safe_image_url(market.get("image") or market.get("icon"))
                outcomes.append({"name": name, "chance": prices[0], "change": _float(market.get("oneDayPriceChange")),
                                 "image": image if image != safe_image_url(event.get("image")) else None,
                                 "token": _token(market, 0), "volume_24h": _float(market.get("volume24hr"))})
    elif markets:
        market = markets[0]
        change = _float(market.get("oneDayPriceChange"))
        for index, (name, chance) in enumerate(zip(_labels(market), _prices(market))):
            outcomes.append({"name": _clean(name, 40), "chance": chance, "change": change if index == 0 else -change,
                             "image": None, "token": _token(market, index),
                             "volume_24h": _float(market.get("volume24hr"))})
    outcomes = [outcome for outcome in outcomes if 0 <= outcome["chance"] <= 1]
    outcomes.sort(key=lambda outcome: outcome["chance"], reverse=True)
    title = _clean(event.get("title"), 90)
    if not title or not outcomes:
        return None
    return {"id": str(event.get("id") or event.get("slug") or title), "title": title, "category": category,
            "volume_24h": _float(event.get("volume24hr")), "volume": _float(event.get("volume")),
            "comments": int(_float(event.get("commentCount"))),
            "ends": str(event.get("endDate") or "")[:10], "image": safe_image_url(event.get("image") or event.get("icon")),
            "outcomes": outcomes[:MAX_OUTCOMES]}


def _token(market, index):
    """The CLOB token of a market's ``index``-th outcome (its price history's key), or None."""
    raw = market.get("clobTokenIds")
    try:
        tokens = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return None
    if not isinstance(tokens, list) or len(tokens) <= index:
        return None
    token = str(tokens[index] or "")
    return token if token.isdigit() else None


def fetch_history(token, context):
    """An outcome's price over the past week: [[unix seconds, chance], ...], oldest first."""
    payload = _get(PRICE_HISTORY, context, max_bytes=128 * 1024,
                   params={"market": token, "interval": "1w", "fidelity": HISTORY_MINUTES})
    points = [[int(_float(point.get("t"))), _float(point.get("p"))] for point in (payload or {}).get("history") or []
              if isinstance(point, dict) and 0 <= _float(point.get("p"), -1) <= 1]
    if len(points) < 2:
        raise ValueError("Polymarket returned no price history")
    return {"points": sorted(points)}


def valid_history(data):
    return len((data or {}).get("points") or []) >= 2


def fetch_polymarket(context):
    """The busiest open events in each category, by 24-hour volume."""
    events = []
    for tag, category in CATEGORIES.items():
        payload = _get(POLYMARKET_EVENTS, context, max_bytes=1536 * 1024,
                       params={"tag_slug": tag, "order": "volume24hr", "ascending": "false", "closed": "false",
                               "limit": EVENTS_PER_TAG})
        for raw in payload if isinstance(payload, list) else []:
            event = normalize_polymarket_event(raw, category) if isinstance(raw, dict) else None
            if event is not None:
                events.append(event)
    if not events:
        raise ValueError("Polymarket returned no open events")
    return {"events": events}


def valid_polymarket(data):
    return bool((data or {}).get("events"))


def lead_and_categories(events):
    """The busiest event of all, then each other category's busiest (no event twice). The tiles
    leave out what Kalshi's board already prices (the Fed, CPI, recession)."""
    events = sorted(events or [], key=lambda event: event["volume_24h"], reverse=True)
    if not events:
        return None, []
    lead, seen, tiles = events[0], {events[0]["id"]}, []
    for category in CATEGORIES.values():
        pick = next((event for event in events if event["category"] == category and event["id"] not in seen
                     and not ON_THE_BOARD.search(event["title"])), None)
        if pick is not None:
            seen.add(pick["id"])
            tiles.append(pick)
    return lead, tiles


# --- Kalshi ----------------------------------------------------------------------------------------

def _kalshi_price(market):
    """A market's chance: its last trade, else the middle of its bid and ask."""
    last = _float(market.get("last_price_dollars"), -1)
    if last >= 0:
        return last
    bid, ask = _float(market.get("yes_bid_dollars"), -1), _float(market.get("yes_ask_dollars"), -1)
    return (bid + ask) / 2 if bid >= 0 and ask >= 0 else None


def median_of_thresholds(markets):
    """The value a set of "above X" markets puts at even odds: between the last strike the market
    gives 50% or more and the next, in proportion. None without a crossing."""
    points = sorted((_float(market.get("floor_strike")), _kalshi_price(market)) for market in markets
                    if market.get("strike_type") == "greater" and _kalshi_price(market) is not None)
    for (low, p_low), (high, p_high) in zip(points, points[1:]):
        if p_low >= 0.5 > p_high:
            return low + (high - low) * (p_low - 0.5) / max(1e-9, p_low - p_high)
    if points and points[0][1] < 0.5:
        return points[0][0]
    return points[-1][0] if points else None


def normalize_kalshi_event(event, kind):
    """One Kalshi event read for the page: its title, settle date and value (by ``kind``)."""
    markets = [market for market in event.get("markets") or [] if isinstance(market, dict)]
    base = {"title": _clean(event.get("title"), 60), "when": _clean(event.get("sub_title"), 40),
            "date": str(event.get("strike_date") or "")[:10]}
    if kind == "decision":
        outcomes = {}
        for market in markets:
            label = FED_OUTCOMES.get(str(market.get("yes_sub_title") or ""))
            price = _kalshi_price(market)
            if label and price is not None:
                previous = _float(market.get("previous_price_dollars"), price)
                outcomes[label] = {"chance": price, "change": price - previous}
        if not outcomes:
            return None
        return {**base, "outcomes": [{"name": name, **outcomes[name]} for name in FED_ORDER if name in outcomes]}
    if kind == "yes":
        price = _kalshi_price(markets[0]) if markets else None
        if price is None:
            return None
        return {**base, "chance": price, "change": price - _float(markets[0].get("previous_price_dollars"), price)}
    if any(market.get("strike_type") == "between" for market in markets):
        # Mutually exclusive ranges: the likeliest one.
        ranged = [market for market in markets if market.get("strike_type") == "between"
                  and _kalshi_price(market) is not None]
        if not ranged:
            return None
        best = max(ranged, key=_kalshi_price)
        ranges = sorted([_float(market.get("floor_strike")), _kalshi_price(market)] for market in ranged)
        return {**base, "low": _float(best.get("floor_strike")), "high": _float(best.get("cap_strike")),
                "chance": _kalshi_price(best), "ranges": ranges[:LADDER_POINTS]}
    median = median_of_thresholds(markets)
    ladder = sorted([_float(market.get("floor_strike")), _kalshi_price(market)] for market in markets
                    if market.get("strike_type") == "greater" and _kalshi_price(market) is not None)
    if len(ladder) > LADDER_POINTS:
        step = len(ladder) / LADDER_POINTS
        ladder = [ladder[int(index * step)] for index in range(LADDER_POINTS)]
    return None if median is None else {**base, "median": median, "ladder": ladder}


def fetch_kalshi(context):
    """Each series' next open event, read as the page shows it."""
    board = {}
    for series, label, kind in KALSHI_SERIES:
        payload = _get(KALSHI_EVENTS, context, max_bytes=512 * 1024,
                       params={"series_ticker": series, "status": "open", "with_nested_markets": "true", "limit": 1})
        events = (payload or {}).get("events") if isinstance(payload, dict) else None
        reading = normalize_kalshi_event(events[0], kind) if events and isinstance(events[0], dict) else None
        if reading is not None:
            board[series] = {**reading, "label": label, "kind": kind}
    if not board:
        raise ValueError("Kalshi returned no open events")
    return {"board": board}


def valid_kalshi(data):
    return bool((data or {}).get("board"))


def utc_now():
    return datetime.now(timezone.utc)


def chinese_when(text):
    """Kalshi's settle phrase in Chinese: "In Nov 2026" -> "2026年11月", "In Q3 2026" -> "2026年第三季度",
    "On Oct 31, 2026" -> "10月31日", "… at 4pm EST" -> "…收盘", "In 2026" -> "2026年"."""
    text = str(text or "").strip()
    match = re.fullmatch(r"In Q([1-4]) (\d{4})", text)
    if match:
        return f"{match.group(2)}年第{QUARTERS[match.group(1)]}季度"
    match = re.fullmatch(r"In ([A-Z][a-z]{2}) (\d{4})", text)
    if match and match.group(1) in MONTHS:
        return f"{match.group(2)}年{MONTHS[match.group(1)]}月"
    match = re.fullmatch(r"In (\d{4})", text)
    if match:
        return f"{match.group(1)}年"
    match = re.match(r"On ([A-Z][a-z]{2}) (\d{1,2}), \d{4}( at .*)?$", text)
    if match and match.group(1) in MONTHS:
        return f"{MONTHS[match.group(1)]}月{int(match.group(2))}日" + ("收盘" if match.group(3) else "")
    return text
