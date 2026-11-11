"""Money Radar's rotating feeds: small keyless extras that take turns in two places on the page.

The yield-curve card takes turns with CBOE's positioning (the VIX futures curve under the day's
put/call ratios) and China's markets (five indices and the yuan's central parity); the right half
of the holdings card takes turns with the Chinese market wire, this week's US releases,
r/wallstreetbets' most mentioned tickers and the coming IPO pricings. Both move on one place every
SLOT_SECONDS (three and five turns, so every pairing comes up and no hour of the day keeps the
same one); a panel without its data gives its turn to the next. The central parity also stands
on the dollar/yuan card.

Every feed is optional: it has its own cache and life, a refresh fetches at most one of them (the
ones on show first), and a failure only leaves the page as it was.
"""

from __future__ import annotations

import html
import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from plugins.base_plugin.source_book import CLOCK_SKEW_SECONDS, LIVE, Source
from utils.http_client import HttpStatusError, get_http_client

from .sources import REQUEST_TIMEOUT, USER_AGENT, _finite, parse_time


EASTERN = ZoneInfo("America/New_York")
SHANGHAI = ZoneInfo("Asia/Shanghai")
HOUR = 3600
# The two slots and what takes turns in them, one place on every SLOT_SECONDS.
SLOT_SECONDS = 3 * HOUR
SLOT_A = ("curve", "volatility", "china")
SLOT_B = ("wire", "calendar", "chatter", "ipo", "news")
ALWAYS_READY = frozenset({"curve", "news"})
# The feeds each panel reads (the first is the one it cannot do without); the parity is also on
# the dollar/yuan card whatever the slots show.
PANEL_FEEDS = {"volatility": ("vix_curve", "put_call"), "china": ("cn_indices", "parity"), "wire": ("wire",),
               "calendar": ("calendar",), "chatter": ("chatter",), "ipo": ("ipo",)}
ALWAYS_SHOWN = ("parity",)
FEEDS = ("calendar", "put_call", "vix_curve", "cn_indices", "parity", "ipo", "chatter", "wire")
# How long each reading is current, and how long a kept one may still be shown.
FRESH_SECONDS = {"calendar": 6 * HOUR, "put_call": 6 * HOUR, "vix_curve": 6 * HOUR, "cn_indices": HOUR,
                 "parity": 6 * HOUR, "ipo": 12 * HOUR, "chatter": 2 * HOUR, "wire": 45 * 60}
USABLE_SECONDS = {"calendar": 5 * 86400, "put_call": 6 * 86400, "vix_curve": 6 * 86400, "cn_indices": 4 * 86400,
                  "parity": 6 * 86400, "ipo": 5 * 86400, "chatter": 2 * 86400, "wire": 12 * HOUR}
# A feed that is not on show is refreshed no more often than this; one that failed waits this long.
HIDDEN_FRESH_SECONDS = 3 * HOUR
RETRY_SECONDS = 2 * HOUR
STATE_NAME = "extras_state"

CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
CALENDAR_IMPACTS = ("High", "Medium")
MAX_EVENTS = 24
# ForexFactory's titles in Chinese: whole titles, then the measures that carry a period suffix.
EVENT_NAMES = {
    "Unemployment Claims": "初请失业金", "Unemployment Rate": "失业率", "Non-Farm Employment Change": "非农就业",
    "ADP Non-Farm Employment Change": "ADP 就业", "JOLTS Job Openings": "JOLTS 职位空缺",
    "Federal Funds Rate": "联邦基金利率", "FOMC Statement": "FOMC 声明", "FOMC Press Conference": "FOMC 记者会",
    "FOMC Meeting Minutes": "FOMC 会议纪要", "FOMC Economic Projections": "FOMC 经济预测",
    "ISM Manufacturing PMI": "ISM 制造业 PMI", "ISM Services PMI": "ISM 服务业 PMI",
    "Flash Manufacturing PMI": "制造业 PMI 初值", "Flash Services PMI": "服务业 PMI 初值",
    "CB Consumer Confidence": "咨商会消费者信心", "Prelim UoM Consumer Sentiment": "密歇根消费者信心初值",
    "Revised UoM Consumer Sentiment": "密歇根消费者信心终值", "Prelim UoM Inflation Expectations": "密歇根通胀预期初值",
    "Empire State Manufacturing Index": "纽约联储制造业", "Philly Fed Manufacturing Index": "费城联储制造业",
    "Existing Home Sales": "成屋销售", "New Home Sales": "新屋销售", "Building Permits": "营建许可",
    "Housing Starts": "新屋开工", "Crude Oil Inventories": "原油库存", "Trade Balance": "贸易帐",
    "Federal Budget Balance": "联邦预算", "Treasury Currency Report": "财政部汇率报告",
    "President Trump Speaks": "总统讲话", "10-y Bond Auction": "10 年期国债拍卖", "30-y Bond Auction": "30 年期国债拍卖",
}
EVENT_MEASURES = {
    "CPI": "CPI", "Core CPI": "核心 CPI", "PPI": "PPI", "Core PPI": "核心 PPI", "Retail Sales": "零售销售",
    "Core Retail Sales": "核心零售销售", "Core PCE Price Index": "核心 PCE", "PCE Price Index": "PCE 物价",
    "Average Hourly Earnings": "平均时薪", "Advance GDP": "GDP 初值", "Prelim GDP": "GDP 修正值",
    "Final GDP": "GDP 终值", "Durable Goods Orders": "耐用品订单", "Core Durable Goods Orders": "核心耐用品订单",
    "Employment Cost Index": "就业成本指数", "Industrial Production": "工业产出", "Personal Spending": "个人支出",
    "Personal Income": "个人收入", "Pending Home Sales": "成屋签约销售", "Import Prices": "进口物价",
    "Factory Orders": "工厂订单", "Advance GDP Price Index": "GDP 物价指数初值",
}
EVENT_PERIODS = {"m/m": "月率", "y/y": "年率", "q/q": "季率"}
EVENT_SPEAKERS = (("Fed Chair ", "美联储主席"), ("FOMC Member ", "美联储"))

# CBOE's daily files carry their trading day in the address, and a day's are published after its
# close: the newest candidate is today from CBOE_PUBLISH_HOUR Eastern on, else the weekday before.
PUT_CALL_URL = "https://cdn.cboe.com/data/us/options/market_statistics/daily/{day}_daily_options"
VIX_URL = "https://www.cboe.com/us/futures/market_statistics/settlement/csv"
CBOE_PUBLISH_HOUR = 17
MISSED_DAY_SECONDS = 6 * HOUR
PUT_CALL_NAMES = {
    "TOTAL PUT/CALL RATIO": "total", "INDEX PUT/CALL RATIO": "index",
    "EXCHANGE TRADED PRODUCTS PUT/CALL RATIO": "etp", "EQUITY PUT/CALL RATIO": "equity",
    "CBOE VOLATILITY INDEX (VIX) PUT/CALL RATIO": "vix", "SPX + SPXW + SPXO PUT/CALL RATIO": "spx",
}
# The monthly VIX futures (VX/X6), not the weeklies (VX45/X6) or the minis (VXM/X6).
VIX_MONTHLY = re.compile(r"VX/[FGHJKMNQUVXZ]\d{1,2}")
VIX_MIN_POINTS, VIX_MAX_POINTS = 3, 10

# Tencent's quote line for the five indices, in the page's order and under its short names.
CN_INDICES = (("s_sh000001", "上证"), ("s_sz399001", "深成"), ("s_sz399006", "创业板"), ("s_hkHSI", "恒生"),
              ("s_hkHSTECH", "恒生科技"))
CN_INDEX_URL = "https://qt.gtimg.cn/q=" + ",".join(code for code, _name in CN_INDICES)
PARITY_URL = "https://www.chinamoney.com.cn/ags/ms/cm-u-bk-ccpr/CcprHisNew"
PARITY_DAYS = 30
IPO_URL = "https://api.nasdaq.com/api/ipo/calendar"
MAX_IPOS = 8
COMPANY_SUFFIXES = re.compile(
    r"[,\s]+(inc|incorporated|corp|corporation|co|ltd|limited|plc|pbc|llc|lp|ag|sa|nv|se)\.?$", re.IGNORECASE)
CHATTER_URL = "https://apewisdom.io/api/v1.0/filter/wallstreetbets/page/1"
MAX_TICKERS = 12
CHATTER_MIN = 4
WIRE_URL = "https://api-one.wallstcn.com/apiv1/content/lives"
MAX_WIRE = 10
WIRE_TEXT = 120
WIRE_MIN = 2


def _get(url, context, *, max_bytes, params=None, headers=None, encoding=None):
    """One bounded GET: the reply's JSON, or its text when ``encoding`` is given."""
    context.raise_if_cancelled()
    client = get_http_client()
    options = {"context": context, "timeout": REQUEST_TIMEOUT, "max_bytes": max_bytes, "params": params,
               "headers": {"User-Agent": USER_AGENT, **(headers or {})}}
    if encoding:
        response = client.request_text("GET", url, encoding=encoding, errors="replace", **options)
    else:
        response = client.request_json("GET", url, **options)
    context.raise_if_cancelled()
    return response.data


def _whole(value):
    number = _finite(value)
    return int(number) if number is not None and number >= 0 and number == int(number) else None


# --- This week's US releases (ForexFactory's weekly file) ------------------------------------

def normalize_calendar(payload):
    """The week's dollar releases of high and medium impact, in time order:
    {events: [{title, impact, time (UTC), forecast, previous}]}. The file has no actual figure."""
    if not isinstance(payload, list):
        raise ValueError("ForexFactory returned no calendar")
    events = []
    for row in payload:
        if not isinstance(row, dict) or row.get("country") != "USD" or row.get("impact") not in CALENDAR_IMPACTS:
            continue
        stamp = parse_time(row.get("date"))
        title = " ".join(str(row.get("title") or "").split())[:60]
        if stamp is None or not title:
            continue
        events.append({"title": title, "impact": row["impact"], "time": stamp.astimezone(timezone.utc).isoformat(),
                       "forecast": str(row.get("forecast") or "").strip()[:12],
                       "previous": str(row.get("previous") or "").strip()[:12]})
    events.sort(key=lambda event: event["time"])
    return {"events": events[:MAX_EVENTS]}


def fetch_calendar(context):
    return normalize_calendar(_get(CALENDAR_URL, context, max_bytes=128 * 1024, headers={"Accept": "application/json"}))


def valid_calendar(data):
    events = (data or {}).get("events")
    return isinstance(events, list) and all(
        isinstance(event, dict) and event.get("title") and parse_time(event.get("time")) for event in events)


def upcoming_events(data, now, count):
    """Up to ``count`` releases still to come (one that is half an hour old still counts), the
    high-impact ones before any other, in time order."""
    events = (data or {}).get("events")
    coming = []
    for event in events if isinstance(events, list) else []:
        stamp = parse_time(event.get("time")) if isinstance(event, dict) else None
        if stamp is not None and event.get("title") and (stamp - now).total_seconds() >= -1800:
            coming.append(event)
    chosen = sorted(coming, key=lambda event: event.get("impact") != "High")[:count]
    return sorted(chosen, key=lambda event: event["time"])


def event_name(title):
    """A release's Chinese name where the page knows one, else its own title."""
    title = " ".join(str(title or "").split())
    if title in EVENT_NAMES:
        return EVENT_NAMES[title]
    measure, _space, period = title.rpartition(" ")
    if measure in EVENT_MEASURES and period in EVENT_PERIODS:
        return f"{EVENT_MEASURES[measure]} {EVENT_PERIODS[period]}"
    if title in EVENT_MEASURES:
        return EVENT_MEASURES[title]
    for lead, office in EVENT_SPEAKERS:
        if title.startswith(lead) and title.endswith(" Speaks"):
            return f"{office} {title[len(lead):-len(' Speaks')]} 讲话"
    return title


# --- CBOE: put/call ratios and the VIX futures curve (dated daily files) ----------------------

def cboe_days(now, count=4):
    """The trading days whose CBOE files may exist, newest first (holidays are found out by asking)."""
    eastern = now.astimezone(EASTERN)
    day = eastern.date() if eastern.hour >= CBOE_PUBLISH_HOUR else eastern.date() - timedelta(days=1)
    days = []
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day.isoformat())
        day -= timedelta(days=1)
    return days


def _dated(now, known, missed, read):
    """The newest CBOE day's reading with one request: the kept one (``known``) when nothing newer
    can exist yet; a day whose file was missing within MISSED_DAY_SECONDS is passed over for the
    day before, and a new miss is written into ``missed``."""
    have = str((known or {}).get("date") or "")
    for day in cboe_days(now):
        if have and day <= have:
            return known
        stamp = parse_time(missed.get(day))
        if stamp is not None and 0 <= (now - stamp).total_seconds() < MISSED_DAY_SECONDS:
            continue
        try:
            return read(day)
        except HttpStatusError as error:
            if error.status in (403, 404):
                missed[day] = now.isoformat()
            raise
        except ValueError:
            missed[day] = now.isoformat()  # the day's file is not complete yet
            raise
    raise ValueError("CBOE has published none of the recent days")


def normalize_put_call(payload, day):
    """{date, ratios: {total, index, etp, equity, vix, spx}} from a day's options statistics."""
    rows = payload.get("ratios") if isinstance(payload, dict) else None
    ratios = {}
    for row in rows if isinstance(rows, list) else []:
        key = PUT_CALL_NAMES.get(str(row.get("name") or "").strip().upper()) if isinstance(row, dict) else None
        value = _finite(row.get("value")) if key else None
        if value is not None and 0 < value < 20:
            ratios[key] = round(value, 2)
    if "total" not in ratios:
        raise ValueError("CBOE returned no total put/call ratio")
    return {"date": day, "ratios": ratios}


def fetch_put_call(now, context, known=None, missed=None):
    return _dated(now, known, {} if missed is None else missed, lambda day: normalize_put_call(
        _get(PUT_CALL_URL.format(day=day), context, max_bytes=128 * 1024, headers={"Accept": "application/json"}),
        day))


def valid_put_call(data):
    ratios = (data or {}).get("ratios")
    return isinstance(ratios, dict) and _finite(ratios.get("total")) is not None and bool((data or {}).get("date"))


def normalize_vix_curve(text, day):
    """{date, points: [{expiry, month, price}]}: the monthly VIX futures' settlements, nearest first."""
    points = []
    for line in str(text or "").splitlines()[1:400]:
        parts = [part.strip() for part in line.split(",")]
        if len(parts) != 4 or parts[0] != "VX" or not VIX_MONTHLY.fullmatch(parts[1]):
            continue
        price = _finite(parts[3])
        try:
            expiry = date.fromisoformat(parts[2])
        except ValueError:
            continue
        if price is not None and price > 0:
            points.append({"expiry": expiry.isoformat(), "month": expiry.month, "price": round(price, 4)})
    points.sort(key=lambda point: point["expiry"])
    if len(points) < VIX_MIN_POINTS:
        raise ValueError("CBOE returned too few VIX futures settlements")
    return {"date": day, "points": points[:VIX_MAX_POINTS]}


def fetch_vix_curve(now, context, known=None, missed=None):
    return _dated(now, known, {} if missed is None else missed, lambda day: normalize_vix_curve(
        _get(VIX_URL, context, max_bytes=64 * 1024, params={"dt": day}, encoding="utf-8"), day))


def valid_vix_curve(data):
    points = (data or {}).get("points")
    return isinstance(points, list) and len(points) >= VIX_MIN_POINTS and all(
        isinstance(point, dict) and _finite(point.get("price")) is not None and point.get("month") for point in points)


# --- China: five indices (Tencent's quote line) and the yuan's central parity (CFETS) ----------

def normalize_cn_indices(text):
    """{indices: [{code, name, last, change, pct}]} in the page's order; the reply's own (GBK)
    names are not read."""
    rows = dict(re.findall(r'v_(s_[A-Za-z0-9]+)="([^"]*)"', str(text or "")))
    indices = []
    for code, name in CN_INDICES:
        parts = rows.get(code, "").split("~")
        if len(parts) < 6:
            continue
        last, change, pct = (_finite(parts[index]) for index in (3, 4, 5))
        if last is None or last <= 0 or pct is None:
            continue
        indices.append({"code": code[2:], "name": name, "last": round(last, 2),
                        "change": round(change, 2) if change is not None else None, "pct": round(pct, 2)})
    if len(indices) < 3:
        raise ValueError("Tencent returned too few index quotes")
    return {"indices": indices}


def fetch_cn_indices(context):
    return normalize_cn_indices(_get(CN_INDEX_URL, context, max_bytes=16 * 1024, encoding="gbk"))


def valid_cn_indices(data):
    indices = (data or {}).get("indices")
    return isinstance(indices, list) and len(indices) >= 3 and all(
        isinstance(row, dict) and row.get("name") and _finite(row.get("last")) is not None
        and _finite(row.get("pct")) is not None for row in indices)


def normalize_parity(payload):
    """{date, series: [[day, rate]]}: the dollar/yuan central parity of the past weeks, oldest first."""
    records = payload.get("records") if isinstance(payload, dict) else None
    series = []
    for row in records if isinstance(records, list) else []:
        values = row.get("values") if isinstance(row, dict) else None
        value = _finite(values[0]) if isinstance(values, list) and values else None
        day = str(row.get("date") or "") if isinstance(row, dict) else ""
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", day) and value is not None and 1 < value < 20:
            series.append([day, round(value, 4)])
    series.sort()
    if len(series) < 2:
        raise ValueError("CFETS returned too few central parity rates")
    return {"date": series[-1][0], "series": series[-PARITY_DAYS:]}


def fetch_parity(now, context):
    # The parity is fixed at 9:15 in Beijing, which is the Pacific evening of the day before.
    end = now.astimezone(SHANGHAI).date()
    return normalize_parity(_get(PARITY_URL, context, max_bytes=64 * 1024, headers={"Accept": "application/json"}, params={
        "startDate": (end - timedelta(days=45)).isoformat(), "endDate": end.isoformat(), "currency": "USD/CNY",
        "pageNum": 1, "pageSize": PARITY_DAYS}))


def valid_parity(data):
    series = (data or {}).get("series")
    return isinstance(series, list) and len(series) >= 2 and all(
        isinstance(row, list) and len(row) == 2 and _finite(row[1]) is not None for row in series)


def parity_reading(data):
    """(today's parity, its change on the day before in pips, the series' rates), or None."""
    if not valid_parity(data):
        return None
    rates = [float(rate) for _day, rate in data["series"]]
    return rates[-1], (rates[-1] - rates[-2]) * 10000, rates


# --- IPO pricings (Nasdaq's calendar, a month per request) -----------------------------------

def company_name(value):
    """A company's name without its legal suffix; a name shouted in capitals in title case."""
    name = " ".join(html.unescape(str(value or "")).split())
    for _ in range(2):  # "Co., Ltd." carries two
        name = COMPANY_SUFFIXES.sub("", name).rstrip(" ,")
    return (name.title() if name.isupper() else name)[:40]


def price_range(value):
    """"70.00-78.00" -> "$70–78"; "10.00" -> "$10"; "" when it is not a price."""
    numbers = [_finite(part) for part in str(value or "").replace("$", "").split("-")]
    if not numbers or len(numbers) > 2 or any(number is None or number <= 0 for number in numbers):
        return ""
    return "$" + "–".join(f"{number:.2f}".rstrip("0").rstrip(".") for number in numbers)


def money_short(value):
    """An offer amount in billions or millions: "$74.0B", "$25M"."""
    value = _finite(value)
    if value is None or value <= 0:
        return ""
    if value >= 1e9:
        return f"${value / 1e9:.1f}B"
    return f"${value / 1e6:.0f}M" if value >= 1e6 else f"${value / 1e3:.0f}K"


def normalize_ipo(payload):
    """{upcoming: [{symbol, name, date, price, amount}]}: the expected pricings, nearest first."""
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        raise ValueError("Nasdaq returned no IPO calendar")
    upcoming = data.get("upcoming") if isinstance(data.get("upcoming"), dict) else {}
    table = upcoming.get("upcomingTable") if isinstance(upcoming.get("upcomingTable"), dict) else {}
    rows = []
    for row in table.get("rows") or []:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("proposedTickerSymbol") or "").strip().upper()
        try:
            day = datetime.strptime(str(row.get("expectedPriceDate") or ""), "%m/%d/%Y").date()
        except ValueError:
            continue
        if re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,7}", symbol):
            rows.append({"symbol": symbol, "name": company_name(row.get("companyName")), "date": day.isoformat(),
                         "price": price_range(row.get("proposedSharePrice")),
                         "amount": _finite(str(row.get("dollarValueOfSharesOffered") or "").replace("$", "")
                                           .replace(",", ""))})
    rows.sort(key=lambda row: row["date"])
    return {"upcoming": rows[:MAX_IPOS]}


def fetch_ipo(now, context):
    return normalize_ipo(_get(IPO_URL, context, max_bytes=256 * 1024, params={"date": f"{now:%Y-%m}"},
                              headers={"Accept": "application/json"}))


def valid_ipo(data):
    rows = (data or {}).get("upcoming")
    return isinstance(rows, list) and all(isinstance(row, dict) and row.get("symbol") and row.get("date")
                                          for row in rows)


def upcoming_ipos(data, now, count):
    """Up to ``count`` pricings expected from today on, nearest first; short of room the largest
    offers stay."""
    rows = (data or {}).get("upcoming")
    today = now.date().isoformat()
    coming = [row for row in rows if isinstance(row, dict) and str(row.get("date") or "") >= today] \
        if isinstance(rows, list) else []
    largest = sorted(coming, key=lambda row: -(_finite(row.get("amount")) or 0))[:count]
    return sorted(largest, key=lambda row: row["date"])


# --- r/wallstreetbets' most mentioned tickers (ApeWisdom) --------------------------------------

def normalize_chatter(payload):
    """{tickers: [{ticker, mentions, rank, rank_before, mentions_before}]} in rank order."""
    rows = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise ValueError("ApeWisdom returned no ranking")
    tickers = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        ticker = str(row.get("ticker") or "").strip().upper()
        mentions, rank = _whole(row.get("mentions")), _whole(row.get("rank"))
        if not re.fullmatch(r"[A-Z][A-Z0-9.]{0,5}", ticker) or mentions is None or not rank:
            continue
        tickers.append({"ticker": ticker, "mentions": mentions, "rank": rank,
                        "rank_before": _whole(row.get("rank_24h_ago")) or None,
                        "mentions_before": _whole(row.get("mentions_24h_ago"))})
        if len(tickers) >= MAX_TICKERS:
            break
    if not tickers:
        raise ValueError("ApeWisdom returned no tickers")
    return {"tickers": tickers}


def fetch_chatter(context):
    return normalize_chatter(_get(CHATTER_URL, context, max_bytes=128 * 1024, headers={"Accept": "application/json"}))


def valid_chatter(data):
    tickers = (data or {}).get("tickers")
    return isinstance(tickers, list) and bool(tickers) and all(
        isinstance(row, dict) and row.get("ticker") and _whole(row.get("rank")) for row in tickers)


def rank_move(row):
    """Places climbed since a day ago (negative: fallen); None for a ticker that was not ranked."""
    before = _whole(row.get("rank_before"))
    return None if not before else before - row["rank"]


# --- The Chinese market wire (华尔街见闻 7x24) ---------------------------------------------------

def _wire_text(item):
    """An item's headline, or without one the first sentence of its text."""
    title = " ".join(str(item.get("title") or "").split())
    if title:
        return title[:WIRE_TEXT]
    paragraph = next((part.strip() for part in str(item.get("content_text") or "").splitlines() if part.strip()), "")
    sentence, stop, _rest = paragraph.partition("。")
    return (sentence + stop if len(sentence) >= 6 else paragraph)[:WIRE_TEXT]


def normalize_wire(payload):
    """{items: [{time (UTC), text, important}]}, newest first."""
    data = payload.get("data") if isinstance(payload, dict) else None
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise ValueError("华尔街见闻 returned no items")
    wire = []
    for item in items:
        if not isinstance(item, dict):
            continue
        stamp, text = _finite(item.get("display_time")), _wire_text(item)
        if stamp is None or stamp <= 0 or not text:
            continue
        wire.append({"time": datetime.fromtimestamp(stamp, timezone.utc).isoformat(), "text": text,
                     "important": (_finite(item.get("score")) or 0) >= 2})
    wire.sort(key=lambda item: item["time"], reverse=True)
    if not wire:
        raise ValueError("华尔街见闻 returned no readable items")
    return {"items": wire[:MAX_WIRE]}


def fetch_wire(context):
    return normalize_wire(_get(WIRE_URL, context, max_bytes=256 * 1024, headers={"Accept": "application/json"},
                               params={"channel": "global-channel", "limit": 20}))


def valid_wire(data):
    items = (data or {}).get("items")
    return isinstance(items, list) and bool(items) and all(
        isinstance(item, dict) and item.get("text") and parse_time(item.get("time")) for item in items)


def recent_wire(data, now, count):
    """Up to ``count`` of the newest items no older than the wire's usable age."""
    items = (data or {}).get("items")
    recent = []
    for item in items if isinstance(items, list) else []:
        stamp = parse_time(item.get("time")) if isinstance(item, dict) else None
        if stamp is not None and item.get("text") and (now - stamp).total_seconds() <= USABLE_SECONDS["wire"]:
            recent.append(item)
    return recent[:count]


# --- Whose turn it is --------------------------------------------------------------------------

def slot_index(now):
    return int(now.timestamp() // SLOT_SECONDS)


def slot_panels(now):
    """The panels this slot schedules: (yield-curve card, holdings right half)."""
    index = slot_index(now)
    return SLOT_A[index % len(SLOT_A)], SLOT_B[index % len(SLOT_B)]


def feed_data(loaded, name):
    result = loaded.get(name) if isinstance(loaded, dict) else None
    data = result.get("data") if isinstance(result, dict) else None
    return data if isinstance(data, dict) else {}


def panel_ready(panel, loaded, now):
    """Whether ``panel`` has what it needs to be drawn now."""
    if panel in ALWAYS_READY:
        return True
    data = feed_data(loaded, PANEL_FEEDS[panel][0])
    if panel == "volatility":
        return valid_vix_curve(data)
    if panel == "china":
        return valid_cn_indices(data)
    if panel == "wire":
        return len(recent_wire(data, now, WIRE_MIN)) >= WIRE_MIN
    if panel == "calendar":
        return bool(upcoming_events(data, now, 1))
    if panel == "chatter":
        return valid_chatter(data) and len(data["tickers"]) >= CHATTER_MIN
    return bool(upcoming_ipos(data, now, 1))


def shown_panels(loaded, now):
    """The panels to draw: each slot's scheduled one, else the next in its turn that is ready (the
    yield curve and the holdings news always are)."""
    index = slot_index(now)

    def pick(cycle):
        for step in range(len(cycle)):
            panel = cycle[(index + step) % len(cycle)]
            if panel_ready(panel, loaded, now):
                return panel
        return cycle[0]

    return pick(SLOT_A), pick(SLOT_B)


# --- Loading: one request a refresh ---------------------------------------------------------

def _state_source():
    return Source(STATE_NAME, {"kind": "attempts"}, lambda _context: {}, fresh_seconds=0,
                  usable_seconds=30 * 86400, validate=lambda data: isinstance(data, dict))


def _read_state(book):
    """{failed: {feed: time}, missed: {feed: {day: time}}}: when a feed last failed and which dated
    files were missing, kept beside the caches so a failing feed waits its turn."""
    kept = book.read(_state_source())
    data = kept.get("data") if isinstance(kept, dict) else None
    failed = data.get("failed") if isinstance(data, dict) else None
    missed = data.get("missed") if isinstance(data, dict) else None
    return {"failed": dict(failed) if isinstance(failed, dict) else {},
            "missed": {name: dict(days) for name, days in (missed if isinstance(missed, dict) else {}).items()
                       if isinstance(days, dict)}}


def _write_state(book, state, now):
    cutoff = now - timedelta(days=2)
    for days in state["missed"].values():
        for day in [day for day, stamp in days.items() if (parse_time(stamp) or cutoff) <= cutoff]:
            del days[day]
    source = _state_source()
    try:
        book.write(source, {"schema": book.schema, "identity": dict(source.identity),
                            "fetched_at": now.astimezone(timezone.utc).isoformat(), "data": state})
    except (OSError, ValueError):
        pass  # without the note a failing feed is only asked again sooner


def _sources(book, now, state):
    """Each feed's Source; the dated CBOE feeds get their kept reading and their missing days."""
    def source(name, fetch, validate):
        return Source(name, {"feed": name}, fetch, fresh_seconds=FRESH_SECONDS[name],
                      usable_seconds=USABLE_SECONDS[name], validate=validate, timeout_seconds=10)

    def dated(name, validate):
        spec = source(name, None, validate)
        kept = book.read(spec)
        known = kept.get("data") if isinstance(kept, dict) else None
        missed = state["missed"].setdefault(name, {})
        fetch = globals()[f"fetch_{name}"]
        return source(name, lambda context: fetch(now, context, known, missed), validate)

    return {
        "calendar": source("calendar", lambda context: fetch_calendar(context), valid_calendar),
        "put_call": dated("put_call", valid_put_call),
        "vix_curve": dated("vix_curve", valid_vix_curve),
        "cn_indices": source("cn_indices", lambda context: fetch_cn_indices(context), valid_cn_indices),
        "parity": source("parity", lambda context: fetch_parity(now, context), valid_parity),
        "ipo": Source("ipo", {"feed": "ipo", "month": f"{now:%Y-%m}"}, lambda context: fetch_ipo(now, context),
                      fresh_seconds=FRESH_SECONDS["ipo"], usable_seconds=USABLE_SECONDS["ipo"], validate=valid_ipo,
                      timeout_seconds=10),
        "chatter": source("chatter", lambda context: fetch_chatter(context), valid_chatter),
        "wire": source("wire", lambda context: fetch_wire(context), valid_wire),
    }


def shown_feeds(now):
    """The feeds whose panels this slot schedules, and the ones always on the page."""
    return {feed for panel in slot_panels(now) for feed in PANEL_FEEDS.get(panel, ())} | set(ALWAYS_SHOWN)


def _due(book, sources, state, now, force):
    """The one feed this refresh may fetch: a stale one that is on show before any hidden one, the
    most overdue first; None when all are current or waiting after a failure."""
    shown = shown_feeds(now)
    best = None
    for name, source in sources.items():
        failed = parse_time(state["failed"].get(name))
        if failed is not None and 0 <= (now - failed).total_seconds() < RETRY_SECONDS:
            continue
        kept = book.read(source)
        fetched = parse_time(kept.get("fetched_at")) if isinstance(kept, dict) else None
        age = (now - fetched).total_seconds() if fetched is not None else None
        life = source.fresh_seconds if name in shown else max(source.fresh_seconds, HIDDEN_FRESH_SECONDS)
        if not force and age is not None and -CLOCK_SKEW_SECONDS <= age < life:
            continue
        rank = (name in shown, float("inf") if age is None or age < 0 else age / life)
        if best is None or rank > best[0]:
            best = (rank, name)
    return best[1] if best else None


def load(book, now, *, allow_network, context, force=False):
    """Every feed's result ({data, state, error, fetched_at}) from its cache; with the network, at
    most one of them is fetched first."""
    state = _read_state(book)
    sources = _sources(book, now, state)
    due = _due(book, sources, state, now, force) if allow_network else None
    missed_before = {name: dict(days) for name, days in state["missed"].items()}
    loaded = {name: book.load(source, now, allow_network=name == due, context=context, force=name == due)
              for name, source in sources.items()}
    if due is not None:
        failed_before = dict(state["failed"])
        if loaded[due]["state"] == LIVE:
            state["failed"].pop(due, None)
        elif loaded[due]["error"] != "deadline_budget":
            # A refresh that ran out of time before the feed's turn says nothing about the feed.
            state["failed"][due] = now.isoformat()
        if state["failed"] != failed_before or state["missed"] != missed_before:
            _write_state(book, state, now)
    return loaded
