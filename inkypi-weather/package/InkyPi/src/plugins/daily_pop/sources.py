"""Bounded keyless feeds for Daily Pop: Bilibili trending, today's anime, Apple charts,
Chinese Wikipedia's most read, the daily chess puzzle and NASA EPIC's Earth.

Every fetcher returns a small normalised dict; the plugin caches each one in a SourceBook
and the page draws whatever is cached when a provider is down.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from utils.http_client import get_http_client


PACIFIC = ZoneInfo("America/Los_Angeles")
USER_AGENT = "InkyPi DailyPop/1.0 (personal e-paper display)"
REQUEST_TIMEOUT = (3, 9)
BILIBILI_URL = "https://api.bilibili.com/x/web-interface/popular"
BANGUMI_URL = "https://api.bgm.tv/calendar"
APPLE_SONGS_URL = "https://rss.marketingtools.apple.com/api/v2/us/music/most-played/10/songs.json"
WIKI_FEED_URL = "https://api.wikimedia.org/feed/v1/wikipedia/zh/featured/{date:%Y/%m/%d}"
CHESS_URL = "https://api.chess.com/pub/puzzle"
EPIC_URL = "https://epic.gsfc.nasa.gov/api/natural"
EPIC_IMAGE_URL = "https://epic.gsfc.nasa.gov/archive/natural/{date:%Y/%m/%d}/jpg/{name}.jpg"
IMAGE_HOSTS = ("i0.hdslb.com", "i1.hdslb.com", "i2.hdslb.com", "lain.bgm.tv", "is1-ssl.mzstatic.com",
               "is2-ssl.mzstatic.com", "is3-ssl.mzstatic.com", "is4-ssl.mzstatic.com", "is5-ssl.mzstatic.com",
               "upload.wikimedia.org", "www.chess.com", "epic.gsfc.nasa.gov")
MAX_VIDEOS = 6
MAX_SHOWS = 8
MAX_SONGS = 5
MAX_ARTICLES = 6
# Wikipedia's most-read list always has its main page and search pages on top.
WIKI_SKIP = re.compile(r"^(?:Wikipedia|Special|Help|File|Portal|Template|Category|Talk|User):|^首页$", re.I)
WEEKDAYS = "一二三四五六日"


def local_now(now=None):
    value = now or datetime.now(PACIFIC)
    return value.replace(tzinfo=PACIFIC) if value.tzinfo is None else value.astimezone(PACIFIC)


def _clean(text, limit):
    return " ".join(str(text or "").split())[:limit]


def https(url):
    """Upgrade a provider's http image link to https (Bilibili and Bangumi serve both)."""
    text = str(url or "").strip()
    return "https://" + text[len("http://"):] if text.startswith("http://") else text


def safe_image_url(value):
    url = https(value)
    parts = urlsplit(url)
    return url if parts.scheme == "https" and parts.hostname in IMAGE_HOSTS else None


def _get(url, context, *, max_bytes, params=None):
    context.raise_if_cancelled()
    response = get_http_client().request_json("GET", url, context=context, timeout=REQUEST_TIMEOUT,
                                              max_bytes=max_bytes, params=params,
                                              headers={"User-Agent": USER_AGENT})
    context.raise_if_cancelled()
    return response.data


def _count(value):
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


# --- Bilibili trending ---------------------------------------------------------------------

def normalize_bilibili(payload):
    rows = (((payload or {}).get("data") or {}).get("list")) or []
    videos = []
    for row in rows:
        if not isinstance(row, dict) or not row.get("title"):
            continue
        stat = row.get("stat") or {}
        videos.append({
            "title": _clean(row.get("title"), 80), "owner": _clean((row.get("owner") or {}).get("name"), 30),
            "section": _clean(row.get("tname"), 12), "views": _count(stat.get("view")),
            "likes": _count(stat.get("like")), "cover": https(row.get("pic")),
            "reason": _clean((row.get("rcmd_reason") or {}).get("content"), 12),
        })
        if len(videos) >= MAX_VIDEOS:
            break
    if not videos:
        raise ValueError("Bilibili returned no trending videos")
    return {"videos": videos}


def fetch_bilibili(context):
    return normalize_bilibili(_get(BILIBILI_URL, context, max_bytes=512 * 1024, params={"ps": 10, "pn": 1}))


def valid_bilibili(data):
    return bool((data or {}).get("videos"))


# --- Bangumi: today's anime ------------------------------------------------------------------

def normalize_bangumi(payload, weekday):
    """Today's shows (Bangumi weekday ids run Monday=1..Sunday=7), best rated first."""
    days = payload if isinstance(payload, list) else []
    today = next((day for day in days if ((day or {}).get("weekday") or {}).get("id") == weekday), None)
    shows = []
    for item in (today or {}).get("items") or []:
        if not isinstance(item, dict):
            continue
        rating = (item.get("rating") or {})
        images = item.get("images") or {}
        shows.append({
            "name": _clean(item.get("name_cn") or item.get("name"), 40),
            "score": float(rating.get("score") or 0), "votes": _count(rating.get("total")),
            "watching": _count((item.get("collection") or {}).get("doing")),
            "cover": https(images.get("common") or images.get("medium") or images.get("large")),
        })
    shows.sort(key=lambda show: (show["watching"], show["score"]), reverse=True)
    if not shows:
        raise ValueError("Bangumi returned no shows for today")
    return {"weekday": weekday, "count": len(shows), "shows": shows[:MAX_SHOWS]}


def fetch_bangumi(now, context):
    return normalize_bangumi(_get(BANGUMI_URL, context, max_bytes=1024 * 1024), now.isoweekday())


def valid_bangumi(data):
    return bool((data or {}).get("shows"))


# --- Apple Music most played ---------------------------------------------------------------

def normalize_songs(payload):
    results = (((payload or {}).get("feed") or {}).get("results")) or []
    songs = [{"name": _clean(row.get("name"), 50), "artist": _clean(row.get("artistName"), 40),
              "artwork": str(row.get("artworkUrl100") or "").replace("100x100bb", "200x200bb")}
             for row in results if isinstance(row, dict) and row.get("name")]
    if not songs:
        raise ValueError("Apple returned no songs")
    return {"songs": songs[:MAX_SONGS]}


def fetch_songs(context):
    return normalize_songs(_get(APPLE_SONGS_URL, context, max_bytes=256 * 1024))


def valid_songs(data):
    return bool((data or {}).get("songs"))


# --- Chinese Wikipedia: most read ----------------------------------------------------------

def normalize_most_read(payload):
    articles = []
    for row in ((payload or {}).get("mostread") or {}).get("articles") or []:
        title = _clean(row.get("normalizedtitle") or row.get("title"), 30)
        if not title or WIKI_SKIP.search(title):
            continue
        articles.append({"title": title, "views": _count(row.get("views")),
                         "description": _clean(row.get("description"), 30)})
        if len(articles) >= MAX_ARTICLES:
            break
    if not articles:
        raise ValueError("Wikipedia returned no most-read articles")
    return {"articles": articles}


def fetch_most_read(now, context):
    # The most-read list covers a finished UTC day: yesterday's.
    day = now.astimezone(timezone.utc) - timedelta(days=1)
    return normalize_most_read(_get(WIKI_FEED_URL.format(date=day), context, max_bytes=1024 * 1024))


def valid_most_read(data):
    return bool((data or {}).get("articles"))


# --- Chess.com daily puzzle ----------------------------------------------------------------

def normalize_puzzle(payload):
    fen = str((payload or {}).get("fen") or "")
    if not re.fullmatch(r"[pnbrqkPNBRQK1-8/]+ [wb] [-KQkq]+ [-a-h1-8]+ \d+ \d+", fen):
        raise ValueError("Chess.com returned no puzzle position")
    moves = re.sub(r"\[[^\]]*\]", "", str(payload.get("pgn") or ""))
    moves = " ".join(re.sub(r"(?:1-0|0-1|1/2-1/2|\*)\s*$", "", moves.strip()).split())
    plies = [token for token in moves.split() if not re.fullmatch(r"\d+\.+", token)]
    side_moves = (len(plies) + 1) // 2
    return {"title": _clean(payload.get("title"), 40), "fen": fen, "to_move": fen.split()[1],
            "image": str(payload.get("image") or ""), "solution": moves[:80],
            "mate_in": side_moves if plies and plies[-1].endswith("#") else None}


def fetch_puzzle(context):
    return normalize_puzzle(_get(CHESS_URL, context, max_bytes=64 * 1024))


def valid_puzzle(data):
    return bool((data or {}).get("fen"))


# --- NASA EPIC: the whole Earth ------------------------------------------------------------

def normalize_epic(payload):
    """The day's natural-colour frames (UTC), oldest first, each with where its centre looks."""
    frames = []
    for row in payload if isinstance(payload, list) else []:
        if not isinstance(row, dict):
            continue
        name = str(row.get("image") or "")
        try:
            taken = datetime.strptime(str(row.get("date")), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if not re.fullmatch(r"epic_1b_\d{14}", name):
            continue
        centre = row.get("centroid_coordinates") or {}
        frames.append({"taken": taken.isoformat(), "image": EPIC_IMAGE_URL.format(date=taken, name=name),
                       "lat": float(centre.get("lat") or 0), "lon": float(centre.get("lon") or 0)})
    if not frames:
        raise ValueError("EPIC returned no frames")
    frames.sort(key=lambda frame: frame["taken"])
    return {"frames": frames}


def fetch_epic(context):
    return normalize_epic(_get(EPIC_URL, context, max_bytes=256 * 1024))


def valid_epic(data):
    return bool((data or {}).get("frames"))


def epic_frame_index(epic, now):
    """The frame whose UTC time of day is nearest now's, so the globe turns through the day."""
    frames = (epic or {}).get("frames") or []
    if not frames:
        return None
    minute = now.astimezone(timezone.utc).hour * 60 + now.astimezone(timezone.utc).minute

    def distance(frame):
        taken = datetime.fromisoformat(frame["taken"])
        delta = abs(taken.hour * 60 + taken.minute - minute)
        return min(delta, 1440 - delta)

    return min(range(len(frames)), key=lambda index: distance(frames[index]))


def epic_frame_for(epic, now):
    index = epic_frame_index(epic, now)
    return None if index is None else epic["frames"][index]["image"]


def facing(lat, lon):
    """Which part of the globe the frame looks at, in Chinese."""
    if -170 <= lon <= -100 or lon >= 150:
        place = "太平洋"
    elif -100 < lon <= -30:
        place = "美洲"
    elif -30 < lon <= 60:
        place = "非洲与欧洲" if lat >= -10 else "大西洋"
    elif 60 < lon <= 100:
        place = "印度洋"
    else:
        place = "亚洲与澳洲"
    return place + "上空"
