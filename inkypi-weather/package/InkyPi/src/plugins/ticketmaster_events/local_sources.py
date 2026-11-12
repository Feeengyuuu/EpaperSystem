"""Local Events' keyless sources beside Ticketmaster.

DoTheBay's list of today (the Bay Area's curated things to do, with their votes) and Luma's San
Francisco discover feed (tech meetups, with their guest counts). Each answer is cut down to small
rows kept in the page's own state file; a refresh asks a feed again FRESH_HOURS after its last
answer and a feed that fails keeps that answer for USABLE_HOURS. The page then lists a few of
today's most popular rows that Ticketmaster does not already carry, beside Ticketmaster's own.
Parsers and the choice are plain functions over the fetched data, testable offline.
"""

from __future__ import annotations

import logging
import math
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from runtime.refresh_contracts import TaskCancelled, TaskDeadlineExceeded
from utils.http_client import get_http_client

logger = logging.getLogger(__name__)

DOTHEBAY, LUMA = "dothebay", "luma"
SOURCES = (DOTHEBAY, LUMA)
DOTHEBAY_URL = "https://dothebay.com/events/today.json"
DOTHEBAY_SITE = "https://dothebay.com"
# DoTheBay answers a plain product token; a User-Agent that carries an address gets a 403.
DOTHEBAY_HEADERS = {"User-Agent": "InkyPi-LocalEvents/1.0", "Accept": "application/json"}
LUMA_URL = "https://api.lu.ma/discover/get-paginated-events"
LUMA_PARAMS = {"discover_place_api_id": "discplace-BDj7GNbGlsF7Cka", "pagination_limit": "20"}
LUMA_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; InkyPi TicketmasterEvents/1.0; "
                              "+https://github.com/fatihak/InkyPi/)", "Accept": "application/json"}
LUMA_SITE = "https://lu.ma"
# Luma's covers are asked through its image service at a size near the row's picture.
LUMA_IMAGE_HOST = "images.lumacdn.com"
LUMA_IMAGE_SIZE = "cdn-cgi/image/fit=cover,quality=75,width=400,height=225"
# Both answers are about 150 KB of JSON (40 KB on the wire).
MAX_BYTES, REQUEST_SECONDS = 512 * 1024, 10
FRESH_HOURS, USABLE_HOURS = 3, 12
# Rows of a feed kept in the page's state, and the most the list takes from each by default.
KEPT = 12
QUOTAS = {DOTHEBAY: 2, LUMA: 1}
EARTH_RADIUS_MILES = 3958.7613
# DoTheBay's categories as the page's kinds (Ticketmaster's segment and genre names).
DOTHEBAY_KINDS = {
    "music": ("Music", ""), "comedy": ("Arts & Theatre", "Comedy"), "film": ("Film", ""),
    "the arts": ("Arts & Theatre", "Cultural"), "theater": ("Arts & Theatre", "Theatre"),
    "theatre": ("Arts & Theatre", "Theatre"), "dance": ("Arts & Theatre", "Dance"),
    "variety": ("Arts & Theatre", "Variety"), "food & drink": ("Miscellaneous", "Food & Drink"),
    "holiday event": ("Miscellaneous", "Holiday"), "family": ("Miscellaneous", "Family"),
    "festival": ("Miscellaneous", "Fairs & Festivals"), "sports": ("Sports", ""),
}
LUMA_KIND = ("Miscellaneous", "Tech Meetup")
AGE_PATTERN = re.compile(r"\b(21|18|16)\s*\+")
ALL_AGES_PATTERN = re.compile(r"\ball\s+ages\b", re.IGNORECASE)
PRICE_PATTERN = re.compile(r"\$\s*(\d+(?:\.\d+)?)")
# Shows sold through Ticketmaster are Ticketmaster's own rows on this page.
TICKETMASTER_SELLERS = ("ticketmaster.", "livenation.")


def _text(value):
    return " ".join(str(value or "").split())


def _number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _price(low, high=None):
    """The page's stored price text: 'From $0' (free), 'From $34', '$38-42'."""
    if low is None:
        return ""
    if high is not None and round(high) != round(low):
        return f"${low:.0f}-{high:.0f}"
    return f"From ${low:.0f}"


def _age(text):
    match = AGE_PATTERN.search(text)
    return f"{match.group(1)}+" if match else "全龄" if ALL_AGES_PATTERN.search(text) else ""


def _row(source, identity, title, start, venue, city, point, kind, popularity, price, image, url, **more):
    lat, lon = point
    return {"source": source, "id": _text(identity), "title": title[:160], "start": start, "venue": venue[:80],
            "city": city[:40], "lat": lat, "lon": lon, "segment": kind[0], "genre": kind[1],
            "popularity": int(popularity or 0), "price": price, "image": image, "url": url, **more}


def _point(lat, lon):
    lat, lon = _number(lat), _number(lon)
    return (lat, lon) if lat is not None and lon is not None and -90 <= lat <= 90 and -180 <= lon <= 180 else (None, None)


def parse_dothebay(payload):
    """Today's shows from DoTheBay's list, in its order (by popularity): the page's rows with the
    start as a local time on the list's day. The time of day is ``tz_adjusted_begin_date``'s
    (``begin_time`` carries a wrong offset); a weekly night keeps tonight's date, and a run that
    began on an earlier day (a festival, an exhibition) has no start of its own today and is left
    out. ValueError for an answer without the list."""
    events = payload.get("events") if isinstance(payload, dict) else None
    if not isinstance(events, list):
        raise ValueError("DoTheBay listed no events")
    today = _text(payload.get("date"))[:10]
    rows = []
    for event in events:
        if not isinstance(event, dict) or event.get("past") is True or event.get("is_ongoing") is True:
            continue
        title = _text(event.get("title"))
        venue = event.get("venue") if isinstance(event.get("venue"), dict) else {}
        clock = re.search(r"T(\d\d:\d\d:\d\d)", _text(event.get("tz_adjusted_begin_date")))
        day = _text(event.get("begin_date"))[:10] or _text(event.get("begin_time"))[:10]
        if not title or not _text(venue.get("title")) or not clock or not today or day != today:
            continue
        info = _text(event.get("ticket_info"))
        prices = [float(figure) for figure in PRICE_PATTERN.findall(info)]
        free = event.get("is_free") is True
        covers = (event.get("imagery") or {}).get("aws") if isinstance(event.get("imagery"), dict) else {}
        covers = covers if isinstance(covers, dict) else {}
        image = next((_text(covers.get(name)) for name in ("cover_image_h_250_w_680", "cover_image_h_300_w_864",
                                                             "poster_w_400") if covers.get(name)), "")
        seller = _text(event.get("buy_url")).lower()
        rows.append(_row(
            DOTHEBAY, event.get("id"), title, f"{today}T{clock.group(1)}", _text(venue.get("title")),
            _text(venue.get("city")), _point(venue.get("latitude"), venue.get("longitude")),
            DOTHEBAY_KINDS.get(_text(event.get("category")).lower(), ("Miscellaneous", "")),
            _number(event.get("votes")) or 0,
            _price(0.0) if free else _price(min(prices), max(prices)) if prices else "",
            image if urlsplit(image).scheme == "https" else "",
            DOTHEBAY_SITE + _text(event.get("permalink")) if _text(event.get("permalink")).startswith("/") else "",
            age=_age(info), sold_out=event.get("sold_out") is True,
            ticketmaster=any(name in seller for name in TICKETMASTER_SELLERS)))
    return rows


def _luma_cover(url):
    """A cover's address through Luma's image service at LUMA_IMAGE_SIZE; '' for any other host."""
    parts = urlsplit(_text(url))
    if parts.scheme != "https" or parts.hostname != LUMA_IMAGE_HOST or parts.path.startswith("/cdn-cgi/"):
        return ""
    return f"https://{LUMA_IMAGE_HOST}/{LUMA_IMAGE_SIZE}{parts.path}"


def parse_luma(payload):
    """The in-person events of Luma's San Francisco feed, in its order (by start): the page's rows
    with the start in UTC. A hidden address leaves the organiser to name the place. ValueError
    for an answer without the list."""
    entries = payload.get("entries") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        raise ValueError("Luma listed no events")
    rows = []
    for entry in entries:
        event = entry.get("event") if isinstance(entry, dict) else None
        if not isinstance(event, dict) or event.get("location_type") != "offline":
            continue
        title = _text(event.get("name"))
        try:
            start = datetime.fromisoformat(_text(event.get("start_at") or entry.get("start_at")).replace("Z", "+00:00"))
        except ValueError:
            continue
        if not title or start.tzinfo is None:
            continue
        place = event.get("geo_address_info") if isinstance(event.get("geo_address_info"), dict) else {}
        point = event.get("coordinate") if isinstance(event.get("coordinate"), dict) else {}
        calendar = _text((entry.get("calendar") or {}).get("name")) if isinstance(entry.get("calendar"), dict) else ""
        hosts = entry.get("hosts") if isinstance(entry.get("hosts"), list) else []
        host = calendar if calendar and calendar.lower() != "personal" else _text((hosts[0] or {}).get("name")) if (
            hosts and isinstance(hosts[0], dict)) else ""
        ticket = entry.get("ticket_info") if isinstance(entry.get("ticket_info"), dict) else {}
        cents = _number((ticket.get("price") or {}).get("cents")) if isinstance(ticket.get("price"), dict) else None
        slug = _text(event.get("url"))
        rows.append(_row(
            LUMA, entry.get("api_id") or event.get("api_id"), title,
            start.astimezone(timezone.utc).replace(microsecond=0).isoformat(),
            _text(place.get("address")) or host, _text(place.get("city")),
            _point(point.get("latitude"), point.get("longitude")), LUMA_KIND, _number(entry.get("guest_count")) or 0,
            _price(0.0) if ticket.get("is_free") is True else _price(cents / 100) if cents is not None else "",
            _luma_cover(event.get("cover_url")), f"{LUMA_SITE}/{slug}" if re.fullmatch(r"[\w-]+", slug) else "",
            age="", sold_out=ticket.get("is_sold_out") is True, approval=ticket.get("require_approval") is True))
    return rows


def _fetch_dothebay():
    return get_http_client().request_json("GET", DOTHEBAY_URL, timeout=REQUEST_SECONDS, headers=DOTHEBAY_HEADERS,
                                          max_bytes=MAX_BYTES).data


def _fetch_luma():
    return get_http_client().request_json("GET", LUMA_URL, params=LUMA_PARAMS, timeout=REQUEST_SECONDS,
                                          headers=LUMA_HEADERS, max_bytes=MAX_BYTES).data


PARSERS = {DOTHEBAY: parse_dothebay, LUMA: parse_luma}
EMPTY = {"fetched_at": "", "events": []}


def _age_hours(entry, now):
    try:
        fetched = datetime.fromisoformat(str(entry.get("fetched_at")))
    except ValueError:
        return None
    if fetched.tzinfo is None:
        return None
    return (now - fetched).total_seconds() / 3600


def load_local_sources(previous, now, fetchers=None):
    """{source: {"fetched_at", "events"}} for the page's state: each feed's rows (KEPT at most),
    asked again once its kept answer is FRESH_HOURS old. A feed that fails keeps its last answer
    until that is USABLE_HOURS old, then has none; that is never an error for the page."""
    fetchers = fetchers or {DOTHEBAY: _fetch_dothebay, LUMA: _fetch_luma}
    state = {}
    for source in SOURCES:
        kept = previous.get(source) if isinstance(previous, dict) else None
        kept = kept if isinstance(kept, dict) and isinstance(kept.get("events"), list) else None
        age = _age_hours(kept, now) if kept else None
        if age is not None and -0.1 <= age < FRESH_HOURS:
            state[source] = kept
            continue
        try:
            rows = PARSERS[source](fetchers[source]())[:KEPT]
            state[source] = {"fetched_at": now.astimezone(timezone.utc).isoformat(), "events": rows}
        except (TaskCancelled, TaskDeadlineExceeded):
            raise
        except Exception as exc:
            logger.warning("Local events source %s unavailable. | error_type: %s", source, type(exc).__name__)
            state[source] = kept if age is not None and -0.1 <= age <= USABLE_HOURS else dict(EMPTY)
    return state


def _miles(home, lat, lon):
    lat1, lon1, lat2, lon2 = (math.radians(value) for value in (*home, lat, lon))
    half = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS_MILES * math.asin(math.sqrt(half))


def _words(title):
    return re.sub(r"[^\w]+", " ", str(title or "").casefold()).split()


def same_show(first, second):
    """Whether two listings name one show: the same words, or one title leading the other
    ('Soft Cell' and 'Soft Cell - The Final Tour')."""
    first, second = _words(first), _words(second)
    short, long = sorted((first, second), key=len)
    return bool(short) and long[:len(short)] == short and sum(len(word) for word in short) >= 4


def _local_start(row, now):
    try:
        start = datetime.fromisoformat(str(row.get("start")))
    except ValueError:
        return None
    return start.replace(tzinfo=now.tzinfo) if start.tzinfo is None else start.astimezone(now.tzinfo)


def pick_local_events(state, listed, now, home, radius_miles, limit):
    """At most ``limit`` rows for the list, in start order, as the page's event records: what has
    not started, today's before tomorrow's and the most popular first, QUOTAS per feed (a feed
    with fewer leaves its places to the other). Left out: shows sold through Ticketmaster or
    named like one of ``listed`` on the same day (those are Ticketmaster's rows), anything past
    tomorrow, and anything farther than ``radius_miles`` from ``home`` when both are known."""
    today, tomorrow = now.date(), now.date() + timedelta(days=1)
    pools = {}
    for source in SOURCES:
        entry = state.get(source) if isinstance(state, dict) else None
        rows = entry.get("events") if isinstance(entry, dict) and isinstance(entry.get("events"), list) else []
        pool = []
        for row in rows:
            start = _local_start(row, now) if isinstance(row, dict) and row.get("title") else None
            if start is None or start <= now or start.date() > tomorrow or row.get("ticketmaster"):
                continue
            day = start.date().isoformat()
            if any(event.local_date == day and same_show(event.title, row["title"]) for event in listed):
                continue
            distance = None
            if home and isinstance(row.get("lat"), (int, float)) and isinstance(row.get("lon"), (int, float)):
                distance = _miles(home, row["lat"], row["lon"])
                if distance > radius_miles:
                    continue
            if any(same_show(other["title"], row["title"]) and other_start.date() == start.date()
                   for _key, other_start, other, _distance in pool):
                continue
            pool.append(((start.date() != today, -int(row.get("popularity") or 0)), start, row, distance))
        pools[source] = sorted(pool, key=lambda item: item[0])
    chosen = []
    for source in SOURCES:
        chosen += pools[source][:QUOTAS[source]]
        pools[source] = pools[source][QUOTAS[source]:]
    for source in SOURCES:
        room = limit - len(chosen)
        if room > 0:
            chosen += pools[source][:room]
    events = []
    for _key, start, row, distance in sorted(chosen[:max(0, limit)], key=lambda item: item[1]):
        source = row.get("source")
        extra = {"source": source, "subgenre": "", "age": _text(row.get("age")), "sold_out": bool(row.get("sold_out")),
                 "has_art": bool(row.get("image")), "popularity": int(row.get("popularity") or 0)}
        if source == LUMA:
            extra.update({"approval": bool(row.get("approval")), "guests": int(row.get("popularity") or 0)})
        events.append({
            "rank": 0, "title": _text(row["title"]), "event_id": f"{source}-{row.get('id')}",
            "local_date": start.date().isoformat(), "local_time": f"{start:%H:%M}:00",
            "venue_name": _text(row.get("venue")), "city": _text(row.get("city")), "state_code": "CA",
            "segment": _text(row.get("segment")), "genre": _text(row.get("genre")), "status": "",
            "price": _text(row.get("price")), "distance": f"{distance:.1f} mi" if distance is not None else "",
            "url": _text(row.get("url")), "image_url": _text(row.get("image")), "poster_url": _text(row.get("image")),
            "poster_path": "", "extra": extra})
    return events
