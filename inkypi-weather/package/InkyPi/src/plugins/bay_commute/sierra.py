"""The drive to Tahoe and Yosemite: Caltrans' road conditions for I-80, US-50 and SR-120, its
chain controls in force and Palisades Tahoe's lifts. Three small readings, each kept on its own
rest, of which the page reads at most one per refresh."""

from __future__ import annotations

import html
import re
from datetime import date, datetime

from utils.http_client import get_http_client

from .sources import PACIFIC, REQUEST_TIMEOUT, USER_AGENT, local_now, parse_time


ROUTES = ("I-80", "US-50", "SR-120")
# One page for the three highways (about 4 KB): each highway's notes by area.
ROADS_URL = "https://roads.dot.ca.gov/roadscell.php"
ROADS_BYTES = 128 * 1024
# QuickMap's chain controls in force, statewide: a placemark each, none when there is none (1 KB).
CHAINS_URL = "https://quickmap.dot.ca.gov/data/cc.kml"
CHAINS_BYTES = 256 * 1024
MAX_PLACEMARKS = 400
# Palisades Tahoe's own status feed (Alterra's mtnpowder; 12 KB on the wire, 180 KB of JSON, of
# which a dozen numbers are kept).
RESORT_URL = "https://mtnpowder.com/feed"
RESORT_ID = 61
RESORT_BYTES = 768 * 1024
# A route's state, mildest first: no restriction, shut for the winter (Tioga, every year), then
# what stops a car today: snow tires or chains (R1), chains unless four-wheel drive with snow tires
# (R2), a chain control the text does not grade, chains on everything (R3), closed.
RANK = {"clear": 0, "seasonal": 1, "r1": 2, "r2": 3, "chains": 4, "r3": 5, "closed": 6}
ALERT_LEVELS = frozenset({"r1", "r2", "chains", "r3", "closed"})
ROUTE_HEADINGS = {"I 80": "I-80", "US 50": "US-50", "SR 120": "SR-120"}
# Only a highway's mountain stretch counts: the areas Caltrans heads with the Sierra or Tahoe, and
# in them the counties the drive from the Bay crosses (SR-120 east of US 395 is Mono County alone).
SIERRA_AREA = re.compile(r"SIERRA|TAHOE", re.IGNORECASE)
SIERRA_COUNTIES = {"I-80": {"placer", "nevada", "sierra"}, "US-50": {"el dorado"},
                   "SR-120": {"tuolumne", "mariposa"}}
REPORTED = re.compile(r"as of\s+\w+,\s+([A-Za-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?,\s+(\d{4})\s+at\s+"
                      r"(\d{1,2}):(\d{2})\s*([AP]M)", re.IGNORECASE)
MONTHS = ("january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
          "november", "december")
# The highway itself shut ("Is closed from ..."), not a connector or ramp named as the subject.
CLOSED = re.compile(r"^(?:(?:the\s+)?(?:north|south|east|west)bound(?:\s+(?:lanes|traffic))?\s+)?(?:is|are)\s+closed\b",
                    re.IGNORECASE)
NOT_FOR_CARS = re.compile(r"\bclosed to (?:all )?(?:trucks|big rigs|tractor|semi|oversize|trailers|vehicles (?:over|with|towing))",
                          re.IGNORECASE)
SEASONAL = re.compile(r"\bfor the (?:winter|season)\b", re.IGNORECASE)
CHAINS = re.compile(r"\bchains\b.*\brequired\b", re.IGNORECASE)
TRUCK_CHAINS = re.compile(r"\bchains (?:are )?required on (?:all )?(?:commercial|trucks|tractor|single|vehicles (?:over|towing))",
                          re.IGNORECASE)
STRETCH = re.compile(r"\bfrom\s+(.+?)\s+to\s+(.+?)(?=\s+-\s|\s*\(|\s*$)", re.IGNORECASE)
OPENING = re.compile(r"opening day\b[^.]{0,80}?\b(" + "|".join(MONTHS) + r")\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(\d{4}))?",
                     re.IGNORECASE)
# Seconds each reading rests before it is due again: while chains are on or a pass is shut, in
# the snow season, out of it. Their order is their turn when two are as far past their rest.
REST = {"sierra_roads": (30 * 60, 2 * 3600, 6 * 3600),
        "sierra_chains": (30 * 60, 3600, 3 * 3600),
        "sierra_resort": (3 * 3600, 3 * 3600, 12 * 3600)}
# How long a reading that could not be read again is still shown.
KEEP = {"sierra_roads": 8 * 3600, "sierra_chains": 6 * 3600, "sierra_resort": 48 * 3600}
SNOW_MONTHS = frozenset({11, 12, 1, 2, 3, 4})


def in_season(now):
    return local_now(now).month in SNOW_MONTHS


def _place(value):
    """A stretch's end as a name: without its county, its aside between slashes or its distance
    ("5 mi west of the Jct of US 395 /Tioga Pass/" reads "Jct of US 395")."""
    value = re.sub(r"\([^)]*\)|/[^/]*/", " ", value)
    value = re.sub(r"^\s*\d+(?:\.\d+)?\s*mi(?:les?)?\s+(?:north|south|east|west)\s+of\s+", "", value, flags=re.IGNORECASE)
    return " ".join(re.sub(r"^\s*the\s+", "", value, flags=re.IGNORECASE).split())[:60]


def _stretch(note):
    for match in STRETCH.finditer(note):
        # "from 2300 hrs each night to 0500 hrs" is when, not where.
        if not re.match(r"\d{3,4}\s*hrs\b", match.group(1), re.IGNORECASE):
            return _place(match.group(1)), _place(match.group(2))
    return "", ""


def _note_level(route, note):
    """What one of Caltrans' notes means for a car on ``route``'s mountain stretch; None for a note
    that restricts nothing there (an advisory, a truck rule, a ramp, another county)."""
    counties = {name.strip().lower() for group in re.findall(r"\(([^()]{2,60}?)\s+co\)", note, re.IGNORECASE)
                for name in group.split(",")}
    if counties and not counties & SIERRA_COUNTIES[route]:
        return None
    if CLOSED.match(note) and not NOT_FOR_CARS.search(note):
        return "seasonal" if SEASONAL.search(note) else "closed"
    if CHAINS.search(note) and not TRUCK_CHAINS.search(note):
        if re.search(r"\bchains or snow tires\b", note, re.IGNORECASE):
            return "r1"
        if re.search(r"\bexcept\s+(?:for\s+)?(?:4|four)[- ]?wheel", note, re.IGNORECASE):
            return "r2"
        if re.search(r"\bon all vehicles\b", note, re.IGNORECASE) and not re.search(r"\bexcept\b", note, re.IGNORECASE):
            return "r3"
        return "chains"
    return None


def _route_state(route, body):
    state = {"route": route, "level": "clear", "from": "", "to": ""}
    mountain = False
    for paragraph in re.findall(r"<p\b[^>]*>(.*?)</p>", body, re.DOTALL | re.IGNORECASE):
        heading = re.search(r"<strong>\s*\[([^\]]*)\]\s*</strong>", paragraph, re.IGNORECASE)
        if heading:
            mountain = bool(SIERRA_AREA.search(heading.group(1)))
        if not mountain:
            continue
        note = re.sub(r"<strong>.*?</strong>", " ", paragraph, flags=re.DOTALL | re.IGNORECASE)
        note = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", note)).split())
        level = _note_level(route, note)
        if level and RANK[level] > RANK[state["level"]]:
            start, end = _stretch(note)
            state = {"route": route, "level": level, "from": start, "to": end}
    return state


def parse_road_conditions(page):
    """Caltrans' road-condition page for the three highways as {reported_at, routes: [{route,
    level, from, to}]}: for each highway the worst that holds on its mountain stretch (see RANK)
    and where."""
    page = str(page or "")
    sections = re.split(r"<h3>\s*([^<]{1,24}?)\s*</h3>", page, flags=re.IGNORECASE)
    found = {}
    for heading, body in zip(sections[1::2], sections[2::2]):
        route = ROUTE_HEADINGS.get(" ".join(heading.upper().split()))
        if route:
            found[route] = _route_state(route, body)
    if not found:
        raise ValueError("Caltrans returned no road conditions")
    stamp = REPORTED.search(page)
    reported = None
    if stamp and stamp.group(1).lower() in MONTHS:
        hour = int(stamp.group(4)) % 12 + (12 if stamp.group(6).upper() == "PM" else 0)
        try:
            reported = datetime(int(stamp.group(3)), MONTHS.index(stamp.group(1).lower()) + 1, int(stamp.group(2)),
                                hour, int(stamp.group(5)), tzinfo=PACIFIC).isoformat()
        except ValueError:
            reported = None
    return {"reported_at": reported, "routes": [found[route] for route in ROUTES if route in found]}


def valid_roads(data):
    rows = data.get("routes") if isinstance(data, dict) else None
    return (isinstance(rows, list) and 1 <= len(rows) <= len(ROUTES)
            and (data.get("reported_at") is None or parse_time(data.get("reported_at")) is not None)
            and all(isinstance(row, dict) and row.get("route") in ROUTES and row.get("level") in RANK
                    and isinstance(row.get("from"), str) and isinstance(row.get("to"), str) for row in rows))


def fetch_road_conditions(context):
    context.raise_if_cancelled()
    response = get_http_client().request_text(
        "GET", ROADS_URL, context=context, timeout=REQUEST_TIMEOUT, max_bytes=ROADS_BYTES, errors="replace",
        params={"roadnumber": ",".join(route.split("-")[1] for route in ROUTES)}, headers={"User-Agent": USER_AGENT})
    context.raise_if_cancelled()
    return parse_road_conditions(response.data)


def parse_chain_controls(text):
    """QuickMap's chain-control file as {count, closures, routes}: how many controls are in force
    statewide, how many of them close the road, and for the page's routes the level a placemark
    names ("R-2") or "closed"."""
    text = str(text or "")
    if "<kml" not in text[:4096].lower():
        raise ValueError("Caltrans returned no chain control file")
    marks = re.findall(r"<Placemark\b.*?</Placemark>", text, re.DOTALL | re.IGNORECASE)[:MAX_PLACEMARKS]
    closures, routes = 0, {}
    for mark in marks:
        closed = bool(re.search(r"<styleUrl>\s*#?full-closure\s*</styleUrl>", mark, re.IGNORECASE))
        closures += closed
        words = " ".join(html.unescape(re.sub(r"<!\[CDATA\[|\]\]>|<[^>]+>", " ", mark)).split())
        graded = re.search(r"\bR-?\s?([123])\b", words)
        level = "closed" if closed else f"r{graded.group(1)}" if graded else "chains"
        for route in ROUTES:
            prefix, number = route.split("-")
            if (re.search(rf"\b{prefix}[- ]?{number}\b", words, re.IGNORECASE)
                    and RANK[level] > RANK.get(routes.get(route), 0)):
                routes[route] = level
    return {"count": len(marks), "closures": closures, "routes": routes}


def valid_chains(data):
    return (isinstance(data, dict) and type(data.get("count")) is int and 0 <= data["count"] <= MAX_PLACEMARKS
            and type(data.get("closures")) is int and isinstance(data.get("routes"), dict)
            and all(route in ROUTES and level in RANK for route, level in data["routes"].items()))


def fetch_chain_controls(context):
    context.raise_if_cancelled()
    response = get_http_client().request_text(
        "GET", CHAINS_URL, context=context, timeout=REQUEST_TIMEOUT, max_bytes=CHAINS_BYTES, errors="replace",
        headers={"User-Agent": USER_AGENT})
    context.raise_if_cancelled()
    return parse_chain_controls(response.data)


def _whole(value):
    """A count or a temperature the feed gives as 7, "7" or "--": an int, else None."""
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return int(round(number)) if -200 < number < 100000 else None


def _opening_day(texts, now):
    """The opening day a resort announces ("Opening Day is scheduled for November 25, 2026") as an
    ISO date; without its year, the next such day."""
    today = local_now(now).date()
    for text in texts:
        match = OPENING.search(re.sub(r"<[^>]+>", " ", html.unescape(str(text or "")))[:2000])
        if not match:
            continue
        month, day = MONTHS.index(match.group(1).lower()) + 1, int(match.group(2))
        try:
            if match.group(3):
                return date(int(match.group(3)), month, day).isoformat()
            found = date(today.year, month, day)
            return (found if found >= today else date(today.year + 1, month, day)).isoformat()
        except ValueError:
            continue
    return None


def parse_resort(payload, now):
    """The resort's feed as {name, open, lifts_open, lifts_total, trails_open, trails_total,
    base_temp_c, base_cm, new_cm (the base area's last 24 hours), opening, updated}."""
    report = payload.get("SnowReport") if isinstance(payload, dict) else None
    status = str(payload.get("OperatingStatus") or "").strip().lower() if isinstance(payload, dict) else ""
    if not isinstance(report, dict) or not status:
        raise ValueError("The resort returned no status")
    conditions = payload.get("CurrentConditions")
    base = (conditions.get("Base") if isinstance(conditions, dict) else None) or {}
    area = report.get("BaseArea") if isinstance(report.get("BaseArea"), dict) else {}
    celsius = _whole(base.get("TemperatureC"))
    if celsius is None and _whole(base.get("TemperatureF")) is not None:
        celsius = int(round((_whole(base.get("TemperatureF")) - 32) * 5 / 9))
    try:
        updated = datetime.strptime(str(payload.get("LastUpdate")), "%Y-%m-%dT%H:%M:%S%z").isoformat()
    except ValueError:
        updated = None
    return {"name": " ".join(str(payload.get("Name") or "Palisades Tahoe").split())[:40],
            "open": status.startswith("open"),
            "lifts_open": _whole(report.get("TotalOpenLifts")), "lifts_total": _whole(report.get("TotalLifts")),
            "trails_open": _whole(report.get("TotalOpenTrails")), "trails_total": _whole(report.get("TotalTrails")),
            "base_temp_c": celsius, "base_cm": _whole(area.get("BaseCm")), "new_cm": _whole(area.get("Last24HoursCm")),
            "opening": _opening_day((report.get("Alert"), report.get("News"), report.get("Report")), now),
            "updated": updated}


def valid_resort(data):
    if not isinstance(data, dict) or type(data.get("open")) is not bool or not data.get("name"):
        return False
    numbers = [data.get(key) for key in ("lifts_open", "lifts_total", "trails_open", "trails_total", "base_temp_c",
                                         "base_cm", "new_cm")]
    opening = data.get("opening")
    return (all(value is None or type(value) is int for value in numbers)
            and (opening is None or _day(opening) is not None))


def fetch_resort(now, context):
    context.raise_if_cancelled()
    response = get_http_client().request_json(
        "GET", RESORT_URL, context=context, timeout=REQUEST_TIMEOUT, max_bytes=RESORT_BYTES,
        params={"resortId": RESORT_ID}, headers={"User-Agent": USER_AGENT})
    context.raise_if_cancelled()
    return parse_resort(response.data, now)


def _day(value):
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def watching(roads, chains):
    """Whether the kept readings (their data) say chains are on somewhere or a pass is shut: the
    roads and the controls then come round sooner."""
    rows = (roads or {}).get("routes") or []
    return (any(isinstance(row, dict) and row.get("level") in ALERT_LEVELS for row in rows)
            or bool((chains or {}).get("count")))


def due_source(ages, now, watch):
    """The one reading the page may read at this refresh, or None while all three rest: a reading
    never taken first, else the one furthest past its rest (see REST). ``ages`` gives each
    reading's age in seconds, None for one never taken."""
    column = 0 if watch else 1 if in_season(now) else 2
    turn, furthest = None, 1.0
    for name, rests in REST.items():
        age = ages.get(name)
        if age is None:
            return name
        past = max(0.0, age) / rests[column]
        if past >= 1.0 and (turn is None or past > furthest):
            turn, furthest = name, past
    return turn


def _kept(source, name, now):
    """A reading's data while it is young enough to show, else None."""
    data = (source or {}).get("data")
    fetched = parse_time((source or {}).get("fetched_at"))
    if (source or {}).get("state") in (None, "unavailable") or not data or fetched is None:
        return None
    return data if -300 <= (local_now(now) - fetched).total_seconds() <= KEEP[name] else None


def summary(roads, chains, resort, now):
    """What the page shows of the Sierra from its three readings (each {state, data, fetched_at}):
    {routes, alert, reported_at, resort}. ``alert`` when chains are required or a pass is closed
    on one of the routes. None when there is nothing worth a line: no reading, or nothing
    restricted out of the snow season with the lifts shut and no opening day announced."""
    conditions = _kept(roads, "sierra_roads", now)
    raised = (_kept(chains, "sierra_chains", now) or {}).get("routes") or {}
    routes = []
    for row in (conditions or {}).get("routes") or []:
        level = raised.get(row["route"])
        # A control in force that the road text has not caught up with.
        routes.append({"route": row["route"], "level": level, "from": "", "to": ""}
                      if level in RANK and RANK[level] > RANK[row["level"]] else dict(row))
    lifts = _kept(resort, "sierra_resort", now)
    if lifts:
        opening = _day(lifts.get("opening"))
        lifts = {**lifts, "opening": opening.isoformat() if opening and opening >= local_now(now).date() else None}
    alert = any(row["level"] in ALERT_LEVELS for row in routes)
    news = any(row["level"] != "clear" for row in routes) or bool(lifts and (lifts["open"] or lifts["opening"]))
    if not (routes or lifts) or not (news or in_season(now)):
        return None
    return {"routes": routes, "alert": alert, "reported_at": (conditions or {}).get("reported_at"),
            "resort": lifts or None}
