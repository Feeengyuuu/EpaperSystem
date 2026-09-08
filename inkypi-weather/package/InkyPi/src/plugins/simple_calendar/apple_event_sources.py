"""Read official Apple keynote calendars discovered from the current event page."""
from datetime import date, datetime, timezone
import re
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup
import icalendar

from plugins.simple_calendar.game_event_sources import Announcement, SourceBatch

HOME = "https://www.apple.com/apple-events/"
FAMILY = "apple_event"
EXCLUDED = re.compile(r"replay|recap|\bASL\b|session|workshop|state of the union|conference", re.I)
KEYNOTE = re.compile(r"\bApple\s+(?:Special\s+)?Event\b|\b(?:WWDC\d{0,4}\s+|Apple\s+)?Keynote\b", re.I)


def valid_source_url(url):
    """Keep all discovery and redirect requests on Apple's public web origin."""
    try:
        parsed = urlsplit(url)
        return (parsed.scheme == "https" and parsed.hostname in {"www.apple.com", "apple.com"}
                and not parsed.username and not parsed.password and parsed.port in {None, 443})
    except ValueError:
        return False


def _calendar_events(text, url):
    calendar = icalendar.Calendar.from_ical(text)
    items = calendar.walk("VEVENT")
    if len(items) > 16:
        raise ValueError("Apple calendar exceeds event limit")
    events = []
    for item in items:
        title = str(item.get("SUMMARY", ""))
        if not KEYNOTE.search(title) or EXCLUDED.search(title):
            continue
        uid = str(item.get("UID", "")).strip()
        start = item.decoded("DTSTART") if "DTSTART" in item else None
        if not uid or len(uid) > 256 or not isinstance(start, date):
            raise ValueError("Apple keynote lacks identity or date")
        if isinstance(start, datetime) and start.tzinfo is None:
            raise ValueError("Apple keynote has an ambiguous time")
        status = str(item.get("STATUS", "CONFIRMED")).upper()
        if status not in {"CONFIRMED", "CANCELLED"}:
            continue
        modified = item.decoded("LAST-MODIFIED") if "LAST-MODIFIED" in item else None
        published = modified.astimezone(timezone.utc) if isinstance(modified, datetime) and modified.tzinfo else None
        is_wwdc = bool(re.search(r"\bWWDC\d{0,4}\b", title + " " + str(item.get("DESCRIPTION", "")), re.I))
        events.append(Announcement(
            FAMILY, "WWDC 主题演讲" if is_wwdc else "苹果发布会",
            start.date() if isinstance(start, datetime) else start,
            start.astimezone(timezone.utc) if isinstance(start, datetime) else None,
            "apple", HOME, official_url=url, external_id=uid,
            status="cancelled" if status == "CANCELLED" else "confirmed",
            published_at=published,
            event_timezone="America/Los_Angeles" if not isinstance(start, datetime) else (getattr(start.tzinfo, "key", None) or "UTC"),
            revision=max(0, int(item.get("SEQUENCE", 0))),
        ))
    return events


def fetch_source(source_id, fetch_text, known_urls, now):
    """Rediscover the current ICS URL each check; archive dates are never inferred."""
    del known_urls, now
    if source_id != "apple":
        raise ValueError("Unknown Apple source")
    events, errors = [], []
    try:
        page = BeautifulSoup(fetch_text(HOME), "html.parser")
        main = page.find("main")
        if main is None or not re.search(r"Apple\s+Event|WWDC", main.get_text(" ", strip=True), re.I):
            raise ValueError("Apple event page unavailable")
        links = list(dict.fromkeys(urljoin(HOME, a["href"]) for a in main.select("a[href]")
                                  if urlsplit(a["href"]).path.lower().endswith(".ics")))
        if len(links) > 4:
            raise ValueError("Apple calendar link limit exceeded")
        for url in links:
            if not valid_source_url(url):
                raise ValueError("Untrusted Apple calendar link")
            events.extend(_calendar_events(fetch_text(url), url))
    except Exception as exc:
        errors.append(f"apple.com: {type(exc).__name__}")
    return SourceBatch(events, errors)
