"""Bounded, deterministic adapters for announced gaming broadcasts."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from html import escape
import json
import re
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
import icalendar
import feedparser

SERIES = {
    "state_of_play": "State of Play",
    "nintendo_direct": "Nintendo Direct",
    "summer_game_fest": "Summer Game Fest Live",
    "the_game_awards": "The Game Awards",
    "xbox_showcase": "Xbox Showcase",
    "gamescom_onl": "gamescom Opening Night Live",
}
DEFAULT_TIMEZONES = {key: "America/Los_Angeles" for key in SERIES}
DEFAULT_TIMEZONES["gamescom_onl"] = "Europe/Berlin"
FAMILY_PATTERNS = {
    "state_of_play": r"state of play",
    "nintendo_direct": r"nintendo direct",
    "summer_game_fest": r"summer game fest",
    "the_game_awards": r"the game awards|\btga\b",
    "xbox_showcase": r"xbox.*showcase|developer.?direct",
    "gamescom_onl": r"opening night live|gamescom.*\bonl\b",
}
MONTHS = "January February March April May June July August September October November December".split()
DATE_RE = re.compile(
    r"\b(" + "|".join(m + r"|" + m[:3] + r"\.?" for m in MONTHS)
    + r")\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(20\d{2}))?\b", re.I
)
DAY_FIRST_RE = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(" + "|".join(m + r"|" + m[:3] + r"\.?" for m in MONTHS) + r")(?:,?\s+(20\d{2}))?\b", re.I)
TIME_RE = re.compile(
    r"\b(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?|a|p)?\s*"
    r"(PST|PDT|PT|EST|EDT|ET|CEST|CET|BST|GMT|UTC|JST|Pacific|Eastern|UK time)\b", re.I
)
TIMEZONES = {
    "PT": "America/Los_Angeles", "PDT": "America/Los_Angeles", "PST": "America/Los_Angeles",
    "ET": "America/New_York", "EDT": "America/New_York", "EST": "America/New_York",
    "CET": "Europe/Berlin", "CEST": "Europe/Berlin", "BST": "Europe/London",
    "GMT": "UTC", "UTC": "UTC", "JST": "Asia/Tokyo",
    "PACIFIC": "America/Los_Angeles", "EASTERN": "America/New_York", "UK TIME": "Europe/London",
}
OFFICIAL_HOSTS = {
    "state_of_play": {"blog.playstation.com", "www.playstation.com"},
    "nintendo_direct": {"www.nintendo.com", "www.nintendo.co.jp"},
    "summer_game_fest": {"www.summergamefest.com"},
    "the_game_awards": {"thegameawards.com", "www.thegameawards.com"},
    "xbox_showcase": {"news.xbox.com", "www.xbox.com"},
    "gamescom_onl": {"www.gamescom.global", "gamescom.global"},
}
SOURCE_FAMILIES = {
    "playstation": ("state_of_play",), "nintendo": ("nintendo_direct",),
    "sgf": ("summer_game_fest", "the_game_awards", "xbox_showcase", "state_of_play", "nintendo_direct", "gamescom_onl"),
    "tga": ("the_game_awards",), "xbox": ("xbox_showcase",),
    "gamescom": ("gamescom_onl",), "eurogamer": tuple(SERIES), "ign": tuple(SERIES),
}
SOURCE_URLS = {
    "playstation": ("https://blog.playstation.com/tag/state-of-play/feed/",),
    "nintendo": ("https://www.nintendo.com/us/nintendo-direct/", "https://www.nintendo.com/us/nintendo-direct/archive/"),
    "sgf": ("https://www.addevent.com/feed/eeidoioaw.ics", "https://www.summergamefest.com/"),
    "tga": ("https://thegameawards.com/news", "https://thegameawards.com/faq"),
    "xbox": ("https://news.xbox.com/en-us/feed/",),
    "gamescom": ("https://www.gamescom.global/en/program",),
    "eurogamer": ("https://www.eurogamer.net/feed/news",),
    "ign": ("https://www.ign.com/rss/articles/feed?tags=games",),
}
ALLOWED_HOSTS = frozenset(host for hosts in OFFICIAL_HOSTS.values() for host in hosts) | {
    "www.addevent.com", "www.eurogamer.net", "www.ign.com",
}
RUMOR_RE = re.compile(r"\b(rumou?rs?|leaks?|reportedly|expected|might|could|insider|prediction)\b", re.I)
RECAP_RE = re.compile(r"recap|highlights|everything (?:announced|revealed)|all (?:the )?announcements|watch (?:the )?replay|round.?up|announced at|revealed at|viewership record", re.I)
BROADCAST_CUE = re.compile(
    r"\btune in\b|\bwatch\b.{0,70}(?:broadcast|state of play|direct|showcase|show|live)|"
    r"(?:state of play(?: japan)?|nintendo direct|summer game fest|game awards|\btga\b|opening night live|showcase|developer.?direct|broadcast)"
    r"[^.!?]{0,90}\b(?:returns?|airs?|airing|streams? live|starts?|starting|begins?|take place|be held|scheduled|rescheduled|moved|confirmed|cancelled|canceled|postponed)\b|"
    r"(?:confirms?|confirmed|announced)[^.!?]{0,65}(?:state of play|nintendo direct|showcase|game awards|opening night live)", re.I
)


@dataclass(frozen=True)
class Announcement:
    """One attributed statement; not yet admitted or merged into the calendar."""

    family: str
    title: str
    event_date: date
    starts_at: datetime | None
    source_id: str
    source_url: str
    authority: int = 3
    official_url: str = ""
    external_id: str = ""
    edition: str = "main"
    status: str = "confirmed"
    published_at: datetime | None = None
    event_timezone: str = ""
    previous_date: date | None = None
    revision: int = 0


def _edition(title):
    title = title.lower()
    if "partner showcase" in title:
        return "partner"
    if re.search(r"developer.?direct", title):
        return "developer_direct"
    if "state of play japan" in title and "&" not in title and " and " not in title:
        return "japan"
    return "main"


def _display_title(family, edition):
    suffix = {"main": "", "partner": " Partner Showcase", "developer_direct": " / Developer Direct", "japan": " Japan"}
    return SERIES[family] + suffix[edition]


def _broadcast_status(text, family):
    pattern = r"(?:" + FAMILY_PATTERNS[family] + r"|broadcast|showcase|ceremony)([^.!?]{0,120})\b(cancell?ed|postponed)\b"
    for match in re.finditer(pattern, text, re.I):
        if re.search(r"\b(?:not|never|no longer|isn['’]t|hasn['’]t|wasn['’]t|game)\b", match[1], re.I):
            continue
        return "postponed" if match[2].lower() == "postponed" else "cancelled"
    return "confirmed"


def _date_from_text(text, year):
    match = DATE_RE.search(text)
    alternate = DAY_FIRST_RE.search(text)
    if alternate and (not match or alternate.start() < match.start()):
        month_name, day, explicit_year = alternate[2], alternate[1], alternate[3]
    elif match:
        month_name, day, explicit_year = match[1], match[2], match[3]
    else:
        return None
    month = next(i for i, name in enumerate(MONTHS, 1) if name[:3].lower() == month_name[:3].lower())
    try:
        return date(int(explicit_year or year), month, int(day))
    except ValueError:
        return None


def _time_from_text(text, event_date):
    match = TIME_RE.search(text)
    if not match:
        return None
    hour, minute = int(match[1]), int(match[2] or 0)
    meridiem = (match[3] or "").replace(".", "").lower()
    if meridiem:
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if meridiem.startswith("p") else 0)
    try:
        local = datetime.combine(event_date, datetime.min.time()).replace(
            hour=hour, minute=minute, tzinfo=ZoneInfo(TIMEZONES[match[4].upper()])
        )
        return local.astimezone(timezone.utc)
    except ValueError:
        return None


def parse_announcement(html: str, *, url: str, source_id: str, now: datetime) -> list[Announcement]:
    """Extract the announced broadcast date, never an article's publication time."""
    soup = BeautifulSoup(html, "html.parser")
    heading = soup.find("h1") or soup.find("title")
    title = heading.get_text(" ", strip=True) if heading else ""
    family = next((key for key, pattern in FAMILY_PATTERNS.items() if re.search(pattern, title, re.I)), None)
    if not family:
        return []
    if RUMOR_RE.search(title) or RECAP_RE.search(title):
        return []
    body = next((node for selector in (".article_body_content", "#content_body", ".entry-content", "article.text-editor", "article", "main")
                 if (node := soup.select_one(selector)) is not None), soup)
    for tag in body.select("script, style, nav, footer, aside"):
        tag.decompose()
    text = body.get_text(" ", strip=True)
    if not BROADCAST_CUE.search(title + ". " + text):
        return []
    publication = soup.find("meta", attrs={"property": "article:published_time"})
    year_match = re.search(r"20\d{2}", publication.get("content", "") if publication else url)
    year = int(year_match[0]) if year_match else now.year
    blocks = [node.get_text(" ", strip=True) for node in body.select("p, li")]
    if not blocks:
        blocks = [text]
    blocks = [sentence for block in blocks for sentence in re.split(r"(?<=[.!?])\s+(?=[A-Z])", block)]
    evidence = [block for block in blocks if BROADCAST_CUE.search(block) and not RUMOR_RE.search(block)]
    if not evidence and not BROADCAST_CUE.search(title):
        return []
    evidence_text = " ".join(evidence)
    rescheduled = re.search(r"(?:rescheduled|moved|postponed)\s+(?:to|until)\s+(.{0,100})", evidence_text, re.I)
    from_to = re.search(r"(?:rescheduled|moved|postponed)\s+from\s+(.{0,80}?)\s+(?:to|until)\s+(.{0,100})", evidence_text, re.I)
    previous = re.search(r"(?:originally scheduled (?:for|on)|moved from|rescheduled from)\s+(.{0,60})", evidence_text, re.I)
    new_schedule = from_to[2] if from_to else rescheduled[1] if rescheduled else ""
    previous_text = from_to[1] if from_to else previous[1] if previous else ""
    previous_date = _date_from_text(previous_text, year) if new_schedule else None
    new_date = _date_from_text(new_schedule, year)
    event_date = new_date or _date_from_text(title, year) or _date_from_text(evidence_text, year)
    if event_date is None:
        return []
    own_host = urlsplit(url).hostname in OFFICIAL_HOSTS[family]
    if not own_host and source_id in {"ign", "eurogamer"}:
        statement = title + ". " + text
        if not re.search(r"\b(?:confirms?|confirmed|announces?|announced)\b", statement, re.I):
            return []
        if re.search(r"\b(?:not|hasn['’]t|haven['’]t)\s+(?:(?:yet|been|officially)\s+)*(?:confirmed|announced)\b", statement, re.I):
            return []
    official_url = next((urljoin(url, a["href"]) for a in body.select("a[href]")
                         if urlsplit(urljoin(url, a["href"])).hostname in OFFICIAL_HOSTS[family]
                         and re.search(FAMILY_PATTERNS[family], urlsplit(urljoin(url, a["href"])).path.replace("-", " ").replace("_", " "), re.I)), "")
    status_text = title + ". " + evidence_text
    status = _broadcast_status(status_text, family)
    if status == "postponed" and new_date:
        status = "confirmed"
    edition = _edition(title)
    display_title = _display_title(family, edition)
    starts_at = _time_from_text(new_schedule or evidence_text, event_date)
    time_match = TIME_RE.search(new_schedule or evidence_text)
    event_timezone = TIMEZONES[time_match[4].upper()] if time_match else DEFAULT_TIMEZONES[family]
    if edition == "japan" and re.search(r"immediately following|after (?:the )?(?:first|main)", text, re.I):
        starts_at = None
    modified = soup.find("meta", attrs={"property": "article:modified_time"}) or publication
    published_at = None
    if modified:
        raw_date = str(modified.get("content", ""))
        try:
            published_at = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
        except ValueError:
            try:
                published_at = parsedate_to_datetime(raw_date)
            except (TypeError, ValueError):
                pass
        if published_at is not None:
            published_at = published_at.replace(tzinfo=timezone.utc) if published_at.tzinfo is None else published_at.astimezone(timezone.utc)
    return [Announcement(family, display_title, event_date, starts_at, source_id, url,
                         authority=3 if own_host else 1, official_url=official_url,
                         edition=edition, status=status, published_at=published_at,
                         event_timezone=event_timezone, previous_date=previous_date)]


def _objects(value):
    """Walk provider JSON with explicit depth/object bounds."""
    pending = [(value, 0)]
    visited = 0
    while pending and visited < 40000:
        node, depth = pending.pop()
        visited += 1
        if depth > 40:
            continue
        if isinstance(node, dict):
            yield node
            pending.extend((v, depth + 1) for v in reversed(list(node.values())) if isinstance(v, (dict, list)))
        elif isinstance(node, list):
            pending.extend((v, depth + 1) for v in reversed(node) if isinstance(v, (dict, list)))


def _embedded_objects(html):
    """Read Next data and joined RSC frames; frame boundaries may cross scripts."""
    match = re.search(r'<script\b[^>]*\bid=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', html, re.S)
    if match:
        try:
            yield from _objects(json.loads(match[1]))
        except (ValueError, TypeError):
            pass
    chunks = []
    for match in re.finditer(r"self\.__next_f\.push\(", html):
        try:
            value, _ = json.JSONDecoder().raw_decode(html[match.end():])
        except ValueError:
            continue
        if isinstance(value, list) and len(value) == 2 and value[0] == 1 and isinstance(value[1], str):
            chunks.append(value[1])
    stream = "".join(chunks).encode("utf-8")
    cursor = 0
    while cursor < len(stream):
        # Resource-hint frames such as :HL[...] have an empty identifier.
        header = re.match(rb"[0-9a-f]*:", stream[cursor:cursor + 32])
        if not header:
            break
        cursor += header.end()
        text_frame = re.match(rb"T([0-9a-f]+),", stream[cursor:cursor + 32])
        if text_frame:
            # React Flight text lengths count UTF-8 bytes, including embedded LF.
            cursor += text_frame.end() + int(text_frame[1], 16)
            continue
        end = stream.find(b"\n", cursor)
        if end < 0:
            end = len(stream)
        payload = stream[cursor:end]
        cursor = end + 1
        if payload[:1] in (b"[", b"{"):
            try:
                yield from _objects(json.loads(payload))
            except (ValueError, TypeError):
                continue


def _rich_text(value):
    if isinstance(value, str):
        return value
    if isinstance(value, dict) and isinstance(value.get("text"), str):
        return value["text"]
    return " ".join(str(obj["value"]) for obj in _objects(value) if obj.get("nodeType") == "text" and isinstance(obj.get("value"), str))


def _parse_nintendo(html, url, now):
    events, links = [], []
    for item in _embedded_objects(html):
        if item.get("__typename") != "NintendoDirect" or not item.get("startDate"):
            continue
        try:
            event_date = date.fromisoformat(str(item["startDate"])[:10])
        except ValueError:
            continue
        name = str(item.get("name") or "Nintendo Direct")
        description = _rich_text(item.get("description", {}))
        event_date = _date_from_text(description, event_date.year) or event_date
        event_url = urljoin("https://www.nintendo.com/us/nintendo-direct/", str(item.get("slug", "")) + "/")
        if event_date >= now.date():
            links.append(event_url)
        # Nintendo's startDate Z has historically encoded local wall time.
        # Only explicit description time + timezone may produce an instant.
        starts_at = _time_from_text(description, event_date)
        time_match = TIME_RE.search(description)
        zone = TIMEZONES[time_match[4].upper()] if time_match else DEFAULT_TIMEZONES["nintendo_direct"]
        events.append(Announcement("nintendo_direct", name, event_date, starts_at, "nintendo", event_url,
                                   external_id=str(item.get("id", "")),
                                   edition=_edition(name), event_timezone=zone,
                                   status=_broadcast_status(description, "nintendo_direct")))
    return events, links


def _parse_gamescom(html, url, now):
    events, links = [], []
    for item in _embedded_objects(html):
        title = str(item.get("title", ""))
        if not item.get("id") or not item.get("slug") or not item.get("startsAt"):
            continue
        if not re.search(FAMILY_PATTERNS["gamescom_onl"], title, re.I) or re.search(r"pre.?show|post.?show|\bASL\b|sign language", title, re.I):
            continue
        try:
            instant = datetime.fromisoformat(str(item["startsAt"]).replace("Z", "+00:00"))
        except ValueError:
            continue
        if instant.tzinfo is None:
            continue
        event_url = urljoin("https://www.gamescom.global/en/event/", str(item["slug"]))
        if instant >= now:
            links.append(event_url)
        status = str(item.get("status", "")).lower()
        if status == "canceled":
            status = "cancelled"
        if status not in {"cancelled", "postponed"}:
            status = _broadcast_status(title + ". " + _rich_text(item.get("description", "")), "gamescom_onl")
        events.append(Announcement("gamescom_onl", SERIES["gamescom_onl"], instant.date(), instant.astimezone(timezone.utc),
                                   "gamescom", event_url, external_id=str(item["id"]), event_timezone="Europe/Berlin", status=status))
    return events, links


def _parse_ics(text, url):
    events = {}
    for item in icalendar.Calendar.from_ical(text).walk("VEVENT")[:512]:
        title = str(item.get("summary", ""))
        if re.search(r"nominations|pre.?show|post.?show|play days|\bASL\b|co.?stream|replay|countdown", title, re.I):
            continue
        family = next((key for key, pattern in FAMILY_PATTERNS.items() if re.search(pattern, title, re.I)), None)
        if not family or "DTSTART" not in item:
            continue
        start = item.decoded("dtstart")
        if isinstance(start, datetime) and start.tzinfo is None:
            continue
        instant = start.astimezone(timezone.utc) if isinstance(start, datetime) else None
        event_date = start.date() if isinstance(start, datetime) else start
        modified = item.get("last-modified") or item.get("dtstamp")
        published = modified.dt if modified else None
        if not isinstance(published, datetime) or published.tzinfo is None:
            published = None
        uid = str(item.get("uid", ""))
        if not uid:
            continue
        edition = _edition(title)
        candidate = Announcement(family, _display_title(family, edition), event_date, instant, "sgf", url,
                                 authority=2, external_id=uid,
                                 edition=edition,
                                 status="cancelled" if str(item.get("status", "")).upper() == "CANCELLED" else "confirmed",
                                 published_at=published.astimezone(timezone.utc) if published else None,
                                 event_timezone=DEFAULT_TIMEZONES[family] if instant is None else (getattr(start.tzinfo, "key", None) or "UTC"),
                                 revision=max(0, int(item.get("sequence", 0))))
        old = events.get(uid)
        rank = lambda event: (event.revision, event.published_at or datetime.min.replace(tzinfo=timezone.utc), event.status == "cancelled")
        if old is None or rank(candidate) >= rank(old):
            events[uid] = candidate
    return list(events.values()), []


def _parse_tga(html, url, now):
    links, faq = [], {}
    for item in _embedded_objects(html):
        if isinstance(item.get("posts"), list):
            for post in item["posts"]:
                if not isinstance(post, dict):
                    continue
                headline = str(post.get("title", ""))
                searchable = headline + " " + str(post.get("slug", "")).replace("-", " ")
                if post.get("slug") and re.search(FAMILY_PATTERNS["the_game_awards"], headline, re.I) and not RECAP_RE.search(searchable):
                    links.append(urljoin("https://thegameawards.com/news/", post["slug"]))
        if item.get("category") == "The Event" and item.get("title") in {"When is The Game Awards?", "How can I watch the show?"}:
            faq[item["title"]] = str(item.get("content", ""))
    if faq:
        when = BeautifulSoup(faq.get("When is The Game Awards?", ""), "html.parser").get_text(" ", strip=True)
        how = BeautifulSoup(faq.get("How can I watch the show?", ""), "html.parser").get_text(" ", strip=True)
        event_date = _date_from_text(when, now.year)
        if event_date:
            instant = _time_from_text(when, event_date) or _time_from_text(how, event_date)
            return [Announcement("the_game_awards", SERIES["the_game_awards"], event_date, instant, "tga", url,
                                 event_timezone="America/Los_Angeles",
                                 status=_broadcast_status(when, "the_game_awards"))], links
    return parse_announcement(html, url=url, source_id="tga", now=now), links


def parse_source_page(source_id: str, text: str, url: str, now: datetime):
    """Return announcement candidates and canonical detail links from a source page."""
    if text.lstrip().startswith("BEGIN:VCALENDAR"):
        return _parse_ics(text, url)
    if source_id == "nintendo":
        return _parse_nintendo(text, url, now)
    if source_id == "gamescom":
        return _parse_gamescom(text, url, now)
    if source_id == "tga":
        return _parse_tga(text, url, now)
    return parse_announcement(text, url=url, source_id=source_id, now=now), []


def valid_source_url(url):
    """Only curated public HTTPS origins may be visited, including redirects."""
    try:
        parsed = urlsplit(url)
        return parsed.scheme == "https" and parsed.hostname in ALLOWED_HOSTS and not parsed.username and not parsed.password and parsed.port in (None, 443)
    except ValueError:
        return False


@dataclass
class SourceBatch:
    announcements: list[Announcement]
    errors: list[str]


def fetch_source(source_id, fetch_text, known_urls, now):
    """Discover announcements and recheck retained detail URLs, with bounded IO."""
    pending = list(SOURCE_URLS[source_id]) + sorted(set(known_urls))
    seen, events, errors = set(), {}, []

    def remember(item):
        key = (item.source_url, item.external_id, item.family)
        previous = events.get(key)
        if previous and not item.published_at:
            item = replace(item, published_at=previous.published_at)
        events[key] = item

    while pending and len(seen) < 16:
        url = pending.pop(0)
        if url in seen or not valid_source_url(url):
            continue
        seen.add(url)
        try:
            text = fetch_text(url)
            if not isinstance(text, str) or not text.strip():
                raise ValueError("empty response")
            if re.search(r"<(?:rss|feed)\b", text[:3000], re.I):
                feed = feedparser.parse(text)
                if not feed.get("version"):
                    raise ValueError("unrecognized feed")
                for entry in feed.entries[:100]:
                    link = str(entry.get("link", ""))
                    if not valid_source_url(link):
                        continue
                    content = " ".join(part.get("value", "") for part in entry.get("content", [])) or entry.get("summary", "")
                    article = '<meta property="article:published_time" content="' + escape(entry.get("published", "")) + '"><h1>' + escape(entry.get("title", "")) + '</h1><article>' + content + '</article>'
                    parsed = parse_announcement(article, url=link, source_id=source_id, now=now)
                    for item in parsed:
                        remember(item)
                    if any(item.event_date >= now.date() for item in parsed):
                        pending.append(link)
                    elif not parsed and any(re.search(pattern, entry.get("title", ""), re.I) for pattern in FAMILY_PATTERNS.values()) and not RUMOR_RE.search(entry.get("title", "")) and not RECAP_RE.search(entry.get("title", "")):
                        pending.append(link)
                continue
            parsed, links = parse_source_page(source_id, text, url, now)
            if not parsed and not links and source_id in {"nintendo", "gamescom", "tga"}:
                raise ValueError("source schedule structure unavailable")
            for item in parsed:
                remember(item)
            pending.extend(link for link in links if link not in seen)
            if not parsed and not links and not re.search(r"<html\b|<!doctype html|BEGIN:VCALENDAR", text, re.I):
                raise ValueError("unrecognized page")
        except Exception as exc:
            errors.append(f"{urlsplit(url).hostname}: {type(exc).__name__}")
    if any(url not in seen and valid_source_url(url) for url in pending):
        errors.append("source request limit reached")
    return SourceBatch(list(events.values()), errors)
