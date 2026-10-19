"""Read official Apple keynote schedules from the event page and Apple Newsroom."""
from datetime import date, datetime, timedelta, timezone
import re
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup
import feedparser
import icalendar

from plugins.simple_calendar.game_event_sources import (
    TIME_RE, TIMEZONES, Announcement, SourceBatch, _date_from_text, _split_sentences, _time_from_text,
)

HOME = "https://www.apple.com/apple-events/"
NEWSROOM_FEED = "https://www.apple.com/newsroom/rss-feed.rss"
FAMILY = "apple_event"
EXCLUDED = re.compile(r"replay|recap|\bASL\b|session|workshop|state of the union|conference", re.I)
KEYNOTE = re.compile(r"\bApple\s+(?:Special\s+)?Event\b|\b(?:WWDC\d{0,4}\s+|Apple\s+)?Keynote\b", re.I)
# Newsroom announces WWDC weeks ahead; the event page only changes on the day.
NOTICE = re.compile(r"\bWWDC\d{0,4}\b|Worldwide Developers Conference|\bApple\s+(?:Special\s+)?Event\b|\bKeynote\b", re.I)
NEWSROOM_EXCLUDED = re.compile(r"replay|recap|\bASL\b|sessions?\b|workshop|state of the union", re.I)
WWDC = re.compile(r"\bWWDC\d{0,4}\b|Worldwide Developers Conference", re.I)
NEWSROOM_ARTICLE = re.compile(r"/newsroom/(20\d{2})/(\d{2})/[a-z0-9][a-z0-9-]{0,200}/?", re.I)
MAX_NEWSROOM_ARTICLES = 6


def valid_source_url(url):
    """Keep all discovery and redirect requests on Apple's public web origin."""
    try:
        parsed = urlsplit(url)
        return (parsed.scheme == "https" and parsed.hostname in {"www.apple.com", "apple.com"}
                and not parsed.username and not parsed.password and parsed.port in {None, 443})
    except ValueError:
        return False


def _newsroom_article(url):
    """Only US Newsroom stories qualify; regional copies repeat the same notice."""
    return valid_source_url(url) and bool(NEWSROOM_ARTICLE.fullmatch(urlsplit(url).path))


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


def _plain(text):
    """Collapse Apple's non-breaking spaces so phrases match like ordinary prose."""
    return " ".join(str(text).split())


def _newsroom_published(soup, url):
    """The story's own date; a story without one counts from the end of its URL month."""
    year, month = (int(part) for part in NEWSROOM_ARTICLE.search(urlsplit(url).path).groups())
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        if match := re.search(r'"datePublished"\s*:\s*"(20\d{2}-\d{2}-\d{2})', script.string or ""):
            return date.fromisoformat(match[1])
    eyebrow = soup.select_one(".category-eyebrow__date")
    published = _date_from_text(_plain(eyebrow.get_text(" ")), year) if eyebrow else None
    return published or date(year + month // 12, month % 12 + 1, 1) - timedelta(days=1)


def _newsroom_keynote(html, url):
    """Read an upcoming keynote date, and its time when Apple states one, from a story."""
    soup = BeautifulSoup(html, "html.parser")
    heading = soup.find("h1")
    title = _plain(heading.get_text(" ")) if heading else ""
    if not NOTICE.search(title) or NEWSROOM_EXCLUDED.search(title):
        return None
    published = _newsroom_published(soup, url)
    article = soup.find("article") or soup
    for tag in article.select("script, style, nav, footer, aside, figure, [class*=docsanddownloads]"):
        tag.decompose()
    blocks = list(dict.fromkeys(_plain(node.get_text(" ")) for node in article.select("p, li, .pagebody-copy")))

    def upcoming(text):
        # Stories dated on or after the keynote report on it; they never schedule it.
        day = _date_from_text(text, published.year)
        return day if day and day > published else None

    event_date = starts_at = zone = None
    # A schedule block such as "June 8, 10 a.m. PDT ... The Keynote will stream"
    # names the keynote alone; the State of the Union block names it only in passing.
    for block in blocks:
        if KEYNOTE.search(block) and not re.search(r"state of the union", block, re.I) and (day := upcoming(block)):
            if instant := _time_from_text(block, day):
                event_date, starts_at, zone = day, instant, TIMEZONES[TIME_RE.search(block)[4].upper()]
                break
    sentences = (sentence for block in blocks for sentence in _split_sentences(block))
    while event_date is None and (sentence := next(sentences, None)) is not None:
        if KEYNOTE.search(sentence):
            event_date = upcoming(sentence)
    if event_date is None:
        return None
    is_wwdc = bool(WWDC.search(title + " " + " ".join(blocks)))
    return Announcement(
        FAMILY, "WWDC 主题演讲" if is_wwdc else "苹果发布会", event_date, starts_at, "apple", url,
        event_timezone=zone or "America/Los_Angeles",
    )


def _newsroom_links(text):
    feed = feedparser.parse(text)
    if not feed.get("version"):
        raise ValueError("unrecognized Apple Newsroom feed")
    links = []
    for entry in feed.entries[:40]:
        link = str(entry.get("link", ""))
        title = _plain(entry.get("title", ""))
        if NOTICE.search(title) and _newsroom_article(link) and link not in links:
            links.append(link)
    return links


def fetch_source(source_id, fetch_text, known_urls, now):
    """Rediscover the event-page ICS and Newsroom notices each check; dates are never inferred."""
    del now
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
    # Retained stories are rechecked after they scroll out of the 20-item feed.
    articles = [url for url in dict.fromkeys(known_urls) if _newsroom_article(url)]
    try:
        articles.extend(link for link in _newsroom_links(fetch_text(NEWSROOM_FEED)) if link not in articles)
    except Exception as exc:
        errors.append(f"apple.com/newsroom: {type(exc).__name__}")
    for url in articles[:MAX_NEWSROOM_ARTICLES]:
        try:
            notice = _newsroom_keynote(fetch_text(url), url)
        except Exception as exc:
            errors.append(f"apple.com/newsroom: {type(exc).__name__}")
            continue
        if notice:
            events.append(notice)
    return SourceBatch(events, errors)
