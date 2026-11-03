"""万象速览's sources: twelve small keyless feeds, each fetched and cached on its own.

- 一言 (v1.hitokoto.cn, category i): a line of classical poetry, its work and poet.
- NetEase Cloud Music's chart summary (music.163.com/api/toplist/detail): the soaring, new, original
  and hot charts with their top three songs. The full playlists run to 600 KB; the summary is 94 KB.
- Douban's mobile collections (m.douban.com/rexxar): films now showing in Chinese cinemas and the
  American series people are watching, with their Douban ratings. Douban answers only with its own
  mobile page as the Referer.
- Headlines from DW 中文 (rss.dw.com), 纽约时报中文网 (cn.nytimes.com) and 少数派 (sspai.com).
- EIA's weekly California retail prices (the dnav table page, 19 KB): regular gasoline's last six
  weeks. EIA's keyless download of the series is an Excel file; this page is the same series as HTML.
- ESA/Webb's and ESA/Hubble's pictures of the month (their listing pages, each picture's 700 px copy).
- Fiscal Data's Debt to the Penny: the total public debt outstanding on its last two record days.
- Alternative.me's crypto Fear & Greed Index over the last seven days.
- NYT Games' Wordle answer for yesterday, never today's.

Every fetcher returns a small normalised dict and raises on anything it cannot read, so a failing
provider only loses its own card. The live sun (SDO) is a picture, fetched by the plugin.
"""

from __future__ import annotations

import calendar
import html
import re
import unicodedata
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import feedparser

from plugins.daily_ai_news.news_text import TRADITIONAL_PHRASE_REPLACEMENTS, TRADITIONAL_TO_SIMPLIFIED
from utils.http_client import get_http_client


PACIFIC = ZoneInfo("America/Los_Angeles")
USER_AGENT = "InkyPi DailyDigest/1.0 (personal e-paper display)"
REQUEST_TIMEOUT = (3, 10)
HITOKOTO = "https://v1.hitokoto.cn/"
# A line longer than this does not fit the header beside the title.
POEM_MAX_LENGTH = 24
NETEASE_TOPLISTS = "https://music.163.com/api/toplist/detail"
# The charts the page reads, by their ids: soaring, new, original, hot.
NETEASE_CHARTS = {19723756: "飙升榜", 3779629: "新歌榜", 2884035: "原创榜", 3778678: "热歌榜"}
DOUBAN = "https://m.douban.com/rexxar/api/v2/subject_collection/{collection}/items"
DOUBAN_REFERER = "https://m.douban.com/movie/"
DOUBAN_COLLECTIONS = {"showing": "movie_showing", "us_tv": "tv_american"}
NEWS_FEEDS = {"dw": "https://rss.dw.com/rdf/rss-chi-all", "nyt": "https://cn.nytimes.com/rss/",
              "sspai": "https://sspai.com/feed"}
EIA_CALIFORNIA = "https://www.eia.gov/dnav/pet/pet_pri_gnd_dcus_sca_w.htm"
# California regular gasoline, dollars per gallon including taxes, weekly.
EIA_SERIES = "EMM_EPMR_PTE_SCA_DPG"
# The Picture of the Month pages (10-17 KB). Their RSS feeds went stale on 2026-11-02 (Webb's
# listed nothing, Hubble's served its 2025 Picture of the Week), the pages did not.
SPACE_PAGES = {"webb": "https://esawebb.org/images/potm/", "hubble": "https://esahubble.org/images/potm/"}
SPACE_IMAGE_HOSTS = ("cdn.esawebb.org", "cdn.esahubble.org")
# Each picture's 700 px copy (80-140 KB), a fifth of its 1280 px "screen" copy.
SPACE_IMAGE_SIZE = "thumb700x"
DEBT = ("https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v2/accounting/od/debt_to_penny"
        "?sort=-record_date&page%5Bsize%5D=2")
FEAR_GREED = "https://api.alternative.me/fng/?limit=7"
FEAR_GREED_NAMES = {"extreme fear": "极度恐惧", "fear": "恐惧", "neutral": "中性", "greed": "贪婪",
                    "extreme greed": "极度贪婪"}
WORDLE = "https://www.nytimes.com/svc/wordle/v2/{day}.json"
SUN = "https://sdo.gsfc.nasa.gov/assets/img/latest/latest_512_0193.jpg"
SUN_HOST = "sdo.gsfc.nasa.gov"
JSON_MAX_BYTES = 256 * 1024
FEED_MAX_BYTES = 512 * 1024
NEWS_ITEMS = 6
MEDIA_ITEMS = 6
SPACE_ITEMS = 3
TRACKS = 3
GAS_PRICE_RANGE = (1.0, 15.0)


def local_now(now=None, zone=PACIFIC):
    value = now or datetime.now(zone)
    return value.replace(tzinfo=zone) if value.tzinfo is None else value.astimezone(zone)


def device_zone(name):
    """The device's configured time zone, Pacific when it names none the system knows."""
    try:
        return ZoneInfo(str(name)) if name else PACIFIC
    except (ZoneInfoNotFoundError, ValueError):
        return PACIFIC


def simplified(text):
    """``text`` in Simplified Chinese, one line, without invisible format marks (a stray U+200E)."""
    value = html.unescape(str(text or ""))
    value = "".join(char for char in value if unicodedata.category(char) != "Cf")
    value = " ".join(value.split())
    for traditional, replacement in TRADITIONAL_PHRASE_REPLACEMENTS:
        value = value.replace(traditional, replacement)
    return value.translate(TRADITIONAL_TO_SIMPLIFIED)


def _clean(text, limit):
    return simplified(text)[:limit].strip()


def _number(value):
    try:
        number = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def _get(url, context, *, kind="json", max_bytes=JSON_MAX_BYTES, params=None, headers=None):
    context.raise_if_cancelled()
    request = {"context": context, "timeout": REQUEST_TIMEOUT, "max_bytes": max_bytes, "params": params,
               "headers": {"User-Agent": USER_AGENT, **(headers or {})}}
    client = get_http_client()
    if kind == "json":
        data = client.request_json("GET", url, **request).data
    elif kind == "text":
        data = client.request_text("GET", url, **request).data
    else:
        data = client.request_bytes("GET", url, **request).data
    context.raise_if_cancelled()
    return data


# --- 一言 ----------------------------------------------------------------------------------------

_CJK = "[" + chr(0x3000) + "-" + chr(0x9FFF) + chr(0xFF00) + "-" + chr(0xFFEF) + "]"
_CJK_GAP = re.compile(rf"(?<={_CJK})\s+(?={_CJK})")


def normalize_poem(payload):
    payload = payload if isinstance(payload, dict) else {}
    # A line broken across two rows reads as one: no space between Chinese characters.
    text = _CJK_GAP.sub("", _clean(payload.get("hitokoto"), 80))
    if not text:
        raise ValueError("Hitokoto returned no line")
    author = _clean(payload.get("from_who"), 30)
    return {"text": text, "work": _clean(payload.get("from"), 40),
            "author": "" if author in ("佚名", "未知") else author, "id": str(payload.get("uuid") or "")}


def fetch_poem(context):
    return normalize_poem(_get(HITOKOTO, context, params={"c": "i", "max_length": POEM_MAX_LENGTH, "encode": "json"}))


def valid_poem(data):
    return isinstance(data, dict) and bool(data.get("text"))


# --- NetEase Cloud Music -------------------------------------------------------------------------

def normalize_music(payload):
    charts = []
    lists = {item.get("id"): item for item in (payload or {}).get("list") or [] if isinstance(item, dict)}
    for chart_id, name in NETEASE_CHARTS.items():
        item = lists.get(chart_id)
        if not item:
            continue
        tracks = [{"title": _clean(track.get("first"), 60), "artist": _clean(track.get("second"), 60)}
                  for track in item.get("tracks") or [] if isinstance(track, dict) and track.get("first")]
        if tracks:
            charts.append({"id": chart_id, "name": name, "update": _clean(item.get("updateFrequency"), 12),
                           "tracks": tracks[:TRACKS]})
    if not charts:
        raise ValueError("NetEase returned no charts")
    return {"charts": charts}


def fetch_music(context):
    return normalize_music(_get(NETEASE_TOPLISTS, context, headers={"Referer": "https://music.163.com/"}))


def valid_music(data):
    return isinstance(data, dict) and bool(data.get("charts"))


# --- Douban ----------------------------------------------------------------------------------------

def normalize_douban(payload):
    items = []
    for raw in (payload or {}).get("subject_collection_items") or []:
        if not isinstance(raw, dict) or not raw.get("title"):
            continue
        rating = _number((raw.get("rating") or {}).get("value"))
        # "2026 / 美国 / 剧情 惊悚 / director / cast": the year, countries and genres.
        parts = [part.strip() for part in simplified(raw.get("card_subtitle")).split("/")]
        items.append({"title": _clean(raw.get("title"), 40), "rating": rating if rating and rating > 0 else None,
                      "year": _clean(raw.get("year"), 4), "region": parts[1].split(" ")[0] if len(parts) > 1 else "",
                      "genres": parts[2].split(" ")[:2] if len(parts) > 2 else []})
    if not items:
        raise ValueError("Douban returned no titles")
    return {"items": items[:MEDIA_ITEMS]}


def fetch_douban(collection, context):
    url = DOUBAN.format(collection=DOUBAN_COLLECTIONS[collection])
    return normalize_douban(_get(url, context, params={"start": 0, "count": 10},
                                 headers={"Referer": DOUBAN_REFERER}))


def valid_items(data):
    return isinstance(data, dict) and bool(data.get("items"))


# --- Headlines -------------------------------------------------------------------------------------

def _entry_time(entry):
    stamp = entry.get("published_parsed") or entry.get("updated_parsed")
    return datetime.fromtimestamp(calendar.timegm(stamp), timezone.utc).isoformat() if stamp else ""


def normalize_news(content):
    feed = feedparser.parse(content)
    items, seen = [], set()
    for entry in feed.entries:
        title = _clean(entry.get("title"), 120)
        if not title or title in seen:
            continue
        seen.add(title)
        items.append({"title": title, "time": _entry_time(entry)})
    if not items:
        raise ValueError("feed returned no headlines")
    items.sort(key=lambda item: item["time"], reverse=True)
    return {"items": items[:NEWS_ITEMS]}


def fetch_news(name, context):
    return normalize_news(_get(NEWS_FEEDS[name], context, kind="bytes", max_bytes=FEED_MAX_BYTES))


# --- EIA California gasoline -------------------------------------------------------------------

_WEEK = re.compile(r'<th[^>]*class="Series5"[^>]*>\s*(\d{2})/(\d{2})/(\d{2})\s*</th>', re.I)
_CELL = re.compile(r'<td[^>]*class="(?:DataB|Current2|Data)"[^>]*>\s*([^<]*?)\s*</td>', re.I)


def normalize_gas(page):
    """California regular gasoline's weeks on EIA's table page, oldest first: [{"week", "price"}]."""
    weeks = [date(2000 + int(year), int(month), int(day)).isoformat() for month, day, year in _WEEK.findall(page or "")]
    row = next((chunk for chunk in re.split(r'<tr[^>]*class="DataRow"[^>]*>', page or "", flags=re.I)
                if re.search(rf"s={EIA_SERIES}(?:&|&amp;)", chunk)), None)
    if row is None or not weeks:
        raise ValueError("EIA page has no California regular gasoline row")
    # The row's figures run up to its history link (its stub holds a nested table of its own).
    prices = [_number(cell) for cell in _CELL.findall(re.split(r'class="DataHist"', row, flags=re.I)[0])]
    series = [{"week": week, "price": round(price, 3)} for week, price in zip(weeks, prices)
              if price is not None and GAS_PRICE_RANGE[0] < price < GAS_PRICE_RANGE[1]]
    if len(series) < 2:
        raise ValueError("EIA page has too few California prices")
    return {"weeks": series}


def fetch_gas(context):
    return normalize_gas(_get(EIA_CALIFORNIA, context, kind="text", max_bytes=JSON_MAX_BYTES))


def valid_gas(data):
    return isinstance(data, dict) and len(data.get("weeks") or []) >= 2


# --- ESA/Webb and ESA/Hubble ---------------------------------------------------------------------

def safe_space_image(value):
    url = str(value or "").strip()
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.hostname not in SPACE_IMAGE_HOSTS:
        return None
    return url


_SPACE_ENTRY = re.compile(r'<a href="/images/(potm\d{4}[a-z])/">', re.I)
_SPACE_TITLE = re.compile(r'class="news-title[^"]*"[^>]*>\s*([^<]+?)\s*</div>', re.I)
_SPACE_DATE = re.compile(r"<strong>\s*(\d{1,2} [A-Za-z]+ \d{4})\s*</strong>")


def normalize_space(page, name):
    """The newest pictures on ESA/Webb's or ESA/Hubble's Picture of the Month page, newest first:
    each one's title, publication date and its 700 px copy on the site's CDN."""
    host = SPACE_IMAGE_HOSTS[0] if name == "webb" else SPACE_IMAGE_HOSTS[1]
    items, seen = [], set()
    parts = _SPACE_ENTRY.split(page or "")
    for code, block in zip(parts[1::2], parts[2::2]):
        title = _SPACE_TITLE.search(block)
        if code in seen or not title:
            continue
        seen.add(code)
        stamp = _SPACE_DATE.search(block)
        try:
            published = datetime.strptime(stamp.group(1), "%d %B %Y").date().isoformat() if stamp else ""
        except ValueError:
            published = ""
        items.append({"title": _clean(title.group(1), 120), "code": code, "time": published,
                      "image": f"https://{host}/archives/images/{SPACE_IMAGE_SIZE}/{code}.jpg"})
        if len(items) == SPACE_ITEMS:
            break
    if not items:
        raise ValueError("page lists no pictures")
    return {"items": items}


def fetch_space(name, context):
    return normalize_space(_get(SPACE_PAGES[name], context, kind="text", max_bytes=FEED_MAX_BYTES), name)


def space_pick(webb, hubble, today):
    """Which picture of the month leads today: Webb's and Hubble's take turns by the day, and one
    whose feed failed gives way to the other. ("webb" | "hubble", the picture) or (None, None)."""
    order = ["webb", "hubble"] if today.toordinal() % 2 == 0 else ["hubble", "webb"]
    found = {"webb": ((webb or {}).get("items") or [None])[0], "hubble": ((hubble or {}).get("items") or [None])[0]}
    for name in order:
        if found[name]:
            return name, found[name]
    return None, None


# --- Debt to the Penny ---------------------------------------------------------------------------

def normalize_debt(payload):
    rows = [row for row in (payload or {}).get("data") or [] if isinstance(row, dict)]
    readings = []
    for row in rows:
        total = _number(row.get("tot_pub_debt_out_amt"))
        if total and row.get("record_date"):
            readings.append({"date": str(row["record_date"])[:10], "total": total,
                             "public": _number(row.get("debt_held_public_amt")),
                             "intragov": _number(row.get("intragov_hold_amt"))})
    if not readings:
        raise ValueError("Fiscal Data returned no debt total")
    readings.sort(key=lambda reading: reading["date"], reverse=True)
    latest = readings[0]
    previous = readings[1] if len(readings) > 1 else None
    return {**latest, "previous_total": previous["total"] if previous else None,
            "previous_date": previous["date"] if previous else None}


def fetch_debt(context):
    return normalize_debt(_get(DEBT, context))


def valid_debt(data):
    return isinstance(data, dict) and bool(data.get("total"))


# --- Fear & Greed -------------------------------------------------------------------------------

def normalize_fear_greed(payload):
    days = []
    for row in (payload or {}).get("data") or []:
        value = _number((row or {}).get("value"))
        stamp = _number((row or {}).get("timestamp"))
        if value is None or not 0 <= value <= 100 or stamp is None:
            continue
        label = str(row.get("value_classification") or "").strip().lower()
        days.append({"value": int(round(value)), "name": FEAR_GREED_NAMES.get(label, ""),
                     "day": datetime.fromtimestamp(stamp, timezone.utc).date().isoformat()})
    if not days:
        raise ValueError("Alternative.me returned no index")
    days.sort(key=lambda day: day["day"], reverse=True)
    return {"days": days[:7]}


def fetch_fear_greed(context):
    return normalize_fear_greed(_get(FEAR_GREED, context))


def valid_fear_greed(data):
    return isinstance(data, dict) and bool(data.get("days"))


# --- Wordle -----------------------------------------------------------------------------------------

def wordle_day(now):
    """Yesterday in the device's time zone: the page never shows today's answer."""
    return (now.date() - timedelta(days=1)).isoformat()


def normalize_wordle(payload, day):
    payload = payload if isinstance(payload, dict) else {}
    solution = str(payload.get("solution") or "").strip().upper()
    if not re.fullmatch(r"[A-Z]{5}", solution) or str(payload.get("print_date") or "") != day:
        raise ValueError("Wordle returned no answer for the day")
    number = payload.get("days_since_launch")
    return {"day": day, "solution": solution, "number": int(number) if isinstance(number, int) else None}


def fetch_wordle(day, context):
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(day)):
        raise ValueError("not a date")
    return normalize_wordle(_get(WORDLE.format(day=day), context), day)


def valid_wordle(data):
    return isinstance(data, dict) and bool(re.fullmatch(r"[A-Z]{5}", str(data.get("solution") or "")))


# --- The sun ----------------------------------------------------------------------------------------

def safe_sun_image(value):
    url = str(value or "").strip()
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.hostname != SUN_HOST:
        return None
    return url


def sun_url(stamp):
    """SDO's latest 193 Å picture, its URL marked with ``stamp`` (a refresh slot), so each slot
    caches its own copy: SDO always serves the latest picture under one name."""
    return f"{SUN}?slot={int(stamp)}"
