"""Bounded feeds for the Tech & Play page: service status, Epic giveaways, upcoming games,
new arXiv papers and speedrun world records."""

from __future__ import annotations

import calendar
import json
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import feedparser

from runtime.refresh_contracts import TaskCancelled
from utils.http_client import HttpClientError, get_http_client


PACIFIC = ZoneInfo("America/Los_Angeles")
USER_AGENT = "InkyPi TechPlay/1.0 (personal e-paper display)"
REQUEST_TIMEOUT = (3, 9)
SERVICES = (
    ("claude", "Claude", "https://status.claude.com"),
    ("github", "GitHub", "https://www.githubstatus.com"),
    ("openai", "OpenAI", "https://status.openai.com"),
    ("cloudflare", "Cloudflare", "https://www.cloudflarestatus.com"),
    ("discord", "Discord", "https://discordstatus.com"),
)
EPIC_URL = "https://store-site-backend-static.ak.epicgames.com/freeGamesPromotions"
TWITCH_TOKEN_URL = "https://id.twitch.tv/oauth2/token"
IGDB_GAMES_URL = "https://api.igdb.com/v4/games"
STEAM_UPCOMING_URL = "https://store.steampowered.com/search/results/"
STEAM_APPDETAILS_URL = "https://store.steampowered.com/api/appdetails"
STEAM_SEARCH_URL = "https://store.steampowered.com/api/storesearch/"
STEAM_ITEMS_URL = "https://api.steampowered.com/IStoreBrowseService/GetItems/v1/"
# IGDB's id for Steam among a game's external games (its external_game_source, formerly category).
IGDB_STEAM_SOURCE = 1
IGDB_STEAM_FIELDS = "external_games.uid,external_games.category,external_games.external_game_source"
# A game's small Steam icon (the 32 px community icon of its store page).
STEAM_ICON_URL = "https://cdn.cloudflare.steamstatic.com/steamcommunity/public/images/apps/{appid}/{icon}.jpg"
ARXIV_URL = "https://export.arxiv.org/api/query"
SPEEDRUN_GAMES_URL = "https://www.speedrun.com/api/v1/games"
SPEEDRUN_RUNS_URL = "https://www.speedrun.com/api/v1/runs"
WIKIPEDIA_API_URL = "https://en.wikipedia.org/w/api.php"
DEFAULT_ARXIV = "cs.AI"
IMAGE_HOSTS = ("cdn1.epicgames.com", "cdn2.unrealengine.com", "images.igdb.com",
               "shared.akamai.steamstatic.com", "shared.fastly.steamstatic.com",
               "cdn.akamai.steamstatic.com", "cdn.cloudflare.steamstatic.com", "upload.wikimedia.org",
               "thumb.wikimedia.org", "flagcdn.com")
INDICATORS = {"none", "minor", "major", "critical", "maintenance"}
MAX_UPCOMING = 6
WEEK = timedelta(days=7)
FEED_PAGE = 200
MAX_FEED_PAGES = 5
MAX_GAMES_PER_DAY = 300
TOP_GAMES = 3


def parse_time(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def local_now(now=None):
    value = now or datetime.now(PACIFIC)
    return value.replace(tzinfo=PACIFIC) if value.tzinfo is None else value.astimezone(PACIFIC)


def safe_image_url(value):
    url = str(value or "").strip()
    parts = urlsplit(url)
    return url if parts.scheme == "https" and parts.hostname in IMAGE_HOSTS else None


def arxiv_setting(value):
    text = str(value or "").strip()
    return text if re.fullmatch(r"[a-z\-]{2,12}\.[A-Za-z\-]{2,8}", text) else DEFAULT_ARXIV


def _clean(text, limit):
    return " ".join(str(text or "").split())[:limit]


def _get_json(method, url, context, *, max_bytes=512 * 1024, params=None, headers=None, data=None,
              timeout=REQUEST_TIMEOUT):
    context.raise_if_cancelled()
    kwargs = {"params": params, "headers": {"User-Agent": USER_AGENT, **(headers or {})}}
    if data is not None:
        kwargs["data"] = data
    response = get_http_client().request_json(method, url, context=context, timeout=timeout,
                                              max_bytes=max_bytes, **kwargs)
    context.raise_if_cancelled()
    return response.data


# --- Service status (Atlassian Statuspage v2) ------------------------------------------------------

def normalize_status(service_id, name, status_payload, incidents_payload):
    status = (status_payload or {}).get("status") or {}
    indicator = str(status.get("indicator") or "").lower()
    if indicator not in INDICATORS:
        raise ValueError(f"{name} status has an unknown indicator")
    incidents = [incident for incident in (incidents_payload or {}).get("incidents") or []
                 if isinstance(incident, dict)]
    incident = incidents[0] if incidents else None
    return {
        "id": service_id, "name": name, "indicator": indicator,
        "description": _clean(status.get("description"), 60),
        "incident": _clean(incident.get("name"), 90) if incident else None,
        "impact": str(incident.get("impact") or "")[:12] if incident else None,
        "since": incident.get("started_at") or incident.get("created_at") if incident else None,
        "open_incidents": len(incidents),
    }


def fetch_status(context):
    services = []
    for service_id, name, base in SERVICES:
        try:
            status = _get_json("GET", f"{base}/api/v2/status.json", context, max_bytes=64 * 1024)
            try:
                incidents = _get_json("GET", f"{base}/api/v2/incidents/unresolved.json", context,
                                      max_bytes=256 * 1024)
            except HttpClientError:
                incidents = {}  # some pages only mirror status.json; the indicator still stands
            services.append(normalize_status(service_id, name, status, incidents))
        except (HttpClientError, ValueError, KeyError, TypeError, AttributeError) as exc:
            # One unreachable status page marks only that service unknown.
            services.append({"id": service_id, "name": name, "indicator": "unknown",
                             "description": type(exc).__name__, "incident": None, "open_incidents": 0})
    if all(service["indicator"] == "unknown" for service in services):
        raise ValueError("no status page answered")
    return {"services": services}


def valid_status(data):
    services = (data or {}).get("services")
    return isinstance(services, list) and bool(services) and all(
        isinstance(service, dict) and service.get("name") for service in services)


# --- Epic Games Store giveaways ----------------------------------------------------------------------

def _epic_image(element):
    images = {item.get("type"): item.get("url") for item in element.get("keyImages") or [] if isinstance(item, dict)}
    for kind in ("OfferImageWide", "DieselStoreFrontWide", "Thumbnail", "OfferImageTall"):
        url = safe_image_url(images.get(kind))
        if url:
            return url + ("&" if "?" in url else "?") + "h=270&w=480&resize=1"
    return None


def _free_window(groups, now):
    for group in groups or []:
        for offer in (group or {}).get("promotionalOffers") or []:
            if ((offer.get("discountSetting") or {}).get("discountPercentage")) != 0:
                continue
            start, end = parse_time(offer.get("startDate")), parse_time(offer.get("endDate"))
            if start and end and end > now:
                return start, end
    return None


def normalize_epic(payload, now):
    elements = ((((payload or {}).get("data") or {}).get("Catalog") or {}).get("searchStore") or {}).get("elements")
    if not isinstance(elements, list):
        raise ValueError("Epic returned no catalog elements")
    current, upcoming = [], []
    for element in elements:
        if not isinstance(element, dict):
            continue
        promotions = element.get("promotions") or {}
        live = _free_window(promotions.get("promotionalOffers"), now)
        later = _free_window(promotions.get("upcomingPromotionalOffers"), now)
        window, bucket = (live, current) if live and live[0] <= now else (later, upcoming)
        if not window:
            continue
        bucket.append({"title": _clean(element.get("title"), 80), "start": window[0].isoformat(),
                       "end": window[1].isoformat(), "image": _epic_image(element)})
    current.sort(key=lambda item: item["end"])
    upcoming.sort(key=lambda item: item["start"])
    return {"current": current[:4], "upcoming": upcoming[:4]}


def fetch_epic(now, context):
    payload = _get_json("GET", EPIC_URL, context, max_bytes=1024 * 1024,
                        params={"locale": "zh-CN", "country": "US", "allowCountries": "US"})
    return normalize_epic(payload, now)


def valid_epic(data):
    return isinstance(data, dict) and isinstance(data.get("current"), list) and isinstance(data.get("upcoming"), list)


# --- Upcoming games: IGDB with Twitch credentials, Steam's popular upcoming otherwise --------------

def fetch_igdb(client_id, client_secret, now, context):
    token = _get_json("POST", TWITCH_TOKEN_URL, context, max_bytes=16 * 1024, params={
        "client_id": client_id, "client_secret": client_secret, "grant_type": "client_credentials"})
    access = str((token or {}).get("access_token") or "")
    if not access:
        raise ValueError("Twitch returned no access token")
    start = int(now.timestamp())
    fields = "name,first_release_date,hypes,cover.image_id,screenshots.image_id,platforms.abbreviation"
    where = (f" where first_release_date >= {start} & first_release_date < {start + 60 * 86400} & hypes > 0;"
             " sort hypes desc; limit 12;")
    headers = {"Client-ID": client_id, "Authorization": f"Bearer {access}", "Accept": "application/json"}
    try:
        # The games' Steam links name their Steam app (for its icon); IGDB refuses a query with a field
        # it does not know, so the plain query stands in.
        rows = _get_json("POST", IGDB_GAMES_URL, context, max_bytes=256 * 1024, headers=headers,
                         data=f"fields {fields},{IGDB_STEAM_FIELDS};{where}".encode("utf-8"))
    except HttpClientError:
        rows = _get_json("POST", IGDB_GAMES_URL, context, max_bytes=256 * 1024, headers=headers,
                         data=f"fields {fields};{where}".encode("utf-8"))
    if not isinstance(rows, list):
        raise ValueError("IGDB returned no game list")
    items = []
    for row in rows:
        stamp = row.get("first_release_date") if isinstance(row, dict) else None
        if not isinstance(stamp, int) or not row.get("name"):
            continue
        shots = [shot.get("image_id") for shot in row.get("screenshots") or [] if isinstance(shot, dict)]
        cover = (row.get("cover") or {}).get("image_id")
        image = (f"https://images.igdb.com/igdb/image/upload/t_screenshot_med/{shots[0]}.jpg" if shots
                 else f"https://images.igdb.com/igdb/image/upload/t_cover_big/{cover}.jpg" if cover else None)
        platforms = [str(item.get("abbreviation")) for item in row.get("platforms") or []
                     if isinstance(item, dict) and item.get("abbreviation")]
        items.append({"name": _clean(row["name"], 60), "appid": igdb_steam_appid(row),
                      "date": datetime.fromtimestamp(stamp, timezone.utc).isoformat(),
                      "date_text": None, "hype": int(row.get("hypes") or 0),
                      "platforms": platforms[:4], "image": safe_image_url(image)})
    items = sorted(items[:MAX_UPCOMING], key=lambda item: item["date"])
    return {"source": "IGDB", "items": items}


def igdb_steam_appid(row):
    """The Steam app id IGDB links a game to (its Steam external game), or None."""
    for external in (row or {}).get("external_games") or []:
        if not isinstance(external, dict):
            continue
        source = external.get("external_game_source", external.get("category"))
        uid = str(external.get("uid") or "")
        if source == IGDB_STEAM_SOURCE and uid.isdigit():
            return int(uid)
    return None


def fetch_steam_upcoming(context):
    search = _get_json("GET", STEAM_UPCOMING_URL, context, max_bytes=256 * 1024, params={
        "filter": "popularcomingsoon", "json": 1, "cc": "us", "l": "schinese", "count": 12})
    items = []
    for row in (search or {}).get("items") or []:
        match = re.search(r"/apps/(\d+)/", str((row or {}).get("logo") or ""))
        if not match:
            continue
        appid = match.group(1)
        details = _get_json("GET", STEAM_APPDETAILS_URL, context, max_bytes=256 * 1024, params={
            "appids": appid, "cc": "us", "l": "schinese", "filters": "basic,release_date"})
        data = ((details or {}).get(appid) or {}).get("data") or {}
        release = data.get("release_date") or {}
        if data.get("type") not in (None, "game") or not release.get("coming_soon", True):
            continue
        items.append({"name": _clean(data.get("name") or row.get("name"), 60), "appid": int(appid),
                      "date": steam_release_date(release.get("date")),
                      "date_text": _clean(release.get("date"), 24), "hype": None, "platforms": ["PC"],
                      "image": safe_image_url(data.get("header_image") or data.get("capsule_image"))})
        if len(items) >= 4:
            break
    if not items:
        raise ValueError("Steam returned no upcoming games")
    return {"source": "Steam", "items": items}


def valid_upcoming(data):
    items = (data or {}).get("items")
    return isinstance(items, list) and all(isinstance(item, dict) and item.get("name") for item in items)


# --- arXiv -------------------------------------------------------------------------------------------

def normalize_arxiv(text):
    feed = feedparser.parse(text)
    papers = []
    for entry in feed.entries:
        title = _clean(entry.get("title"), 200)
        stamp = entry.get("published_parsed")
        if not title or not stamp:
            continue
        authors = [_clean(author.get("name"), 40) for author in entry.get("authors") or [] if author.get("name")]
        identifier = str(entry.get("id") or "").rsplit("/", 1)[-1]
        papers.append({
            "id": re.sub(r"v\d+$", "", identifier)[:24], "title": title,
            "authors": authors[:1], "author_count": len(authors),
            "time": datetime.fromtimestamp(calendar.timegm(stamp), timezone.utc).isoformat(),
            "category": _clean((entry.get("arxiv_primary_category") or {}).get("term"), 16),
        })
    if not papers:
        raise ValueError("arXiv returned no papers")
    return {"papers": papers[:8]}


def fetch_arxiv(category, context):
    context.raise_if_cancelled()
    response = get_http_client().request_text(
        "GET", ARXIV_URL, context=context, timeout=REQUEST_TIMEOUT, max_bytes=512 * 1024,
        params={"search_query": f"cat:{category}", "sortBy": "submittedDate", "sortOrder": "descending",
                "max_results": 8},
        headers={"User-Agent": USER_AGENT})
    context.raise_if_cancelled()
    return normalize_arxiv(response.data)


def valid_arxiv(data):
    papers = (data or {}).get("papers")
    return isinstance(papers, list) and all(isinstance(paper, dict) and paper.get("title") for paper in papers)


# --- Speedrun.com weekly hot games (rolling seven-day counts of newly verified runs) ------------

def _player_name(player):
    names = (player or {}).get("names") or {}
    return _clean(names.get("international") or (player or {}).get("name"), 30)


def flag_url(country):
    return f"https://flagcdn.com/w40/{country}.png" if re.fullmatch(r"[a-z]{2}", str(country or "")) else None


def run_entry(run):
    """A verified run with embedded category and players -> display row; placeholders give None."""
    seconds = ((run or {}).get("times") or {}).get("primary_t")
    verified = parse_time(((run or {}).get("status") or {}).get("verify-date"))
    # Individual-level runs can genuinely take a fraction of a second; only the 0.001 s
    # placeholders parked in retired categories are dropped.
    if not isinstance(seconds, (int, float)) or seconds < 0.01 or verified is None:
        return None
    players = (((run or {}).get("players") or {}).get("data")) or []
    runner = _player_name(players[0]) if players else ""
    if not runner or runner.lower() in {"not applicable", "n/a"}:
        return None
    country = str((((players[0]).get("location") or {}).get("country") or {}).get("code") or "")
    category = _clean((((run.get("category") or {}).get("data")) or {}).get("name"), 40)
    level = run.get("level")
    level_name = _clean(((level.get("data") or {}) if isinstance(level, dict) else {}).get("name"), 30)
    return {"category": f"{level_name} · {category}" if level_name else category,
            "seconds": float(seconds), "verified": verified.isoformat(), "runner": runner,
            "country": country[:2].lower() if re.fullmatch(r"[a-z]{2}(/.*)?", country) else ""}


def is_real_run(run):
    """Moderators bulk-verify 0.001 s 'N/A' guest placeholders; they are not speedrunning activity."""
    seconds = ((run or {}).get("times") or {}).get("primary_t")
    if isinstance(seconds, (int, float)) and seconds < 0.01:
        return False
    players = (run or {}).get("players") or []
    players = players.get("data") or [] if isinstance(players, dict) else players
    return not any(isinstance(player, dict) and str(player.get("name") or "").strip().lower()
                   in {"n/a", "not applicable"} for player in players)


def count_new_runs(previous, pages, now):
    """Fold feed pages (newest first) into rolling per-day, per-game counts.

    Returns (days, newest, since). Counting stops at the newest run already counted or at
    the seven-day cutoff; if the pages never reach the previous run, the gap restarts coverage.
    """
    days = {day: dict(counts) for day, counts in ((previous or {}).get("days") or {}).items()}
    last_seen = parse_time((previous or {}).get("last_verified"))
    since = parse_time((previous or {}).get("since"))
    cutoff = now.astimezone(timezone.utc) - WEEK
    newest, oldest, reached = last_seen, None, False
    for runs in pages:
        for run in runs:
            stamp = parse_time(((run or {}).get("status") or {}).get("verify-date"))
            game = (run or {}).get("game")
            if stamp is None or not isinstance(game, str):
                continue
            if (last_seen and stamp <= last_seen) or stamp < cutoff:
                reached = True
                break
            newest = stamp if newest is None or stamp > newest else newest
            oldest = stamp if oldest is None or stamp < oldest else oldest
            if not is_real_run(run):
                continue
            bucket = days.setdefault(stamp.date().isoformat(), {})
            bucket[game] = bucket.get(game, 0) + 1
        if reached:
            break
    if since is None or (last_seen and not reached and oldest):
        since = oldest or now.astimezone(timezone.utc)
    since = max(since, cutoff)
    days = {day: dict(sorted(counts.items(), key=lambda item: -item[1])[:MAX_GAMES_PER_DAY])
            for day, counts in days.items() if day >= cutoff.date().isoformat()}
    return days, newest, since


def weekly_totals(days):
    totals = {}
    for counts in days.values():
        for game, count in counts.items():
            totals[game] = totals.get(game, 0) + count
    return totals


def _game_info(game_id, context):
    payload = _get_json("GET", f"{SPEEDRUN_GAMES_URL}/{game_id}", context, max_bytes=256 * 1024)
    game = (payload or {}).get("data") or {}
    info = {"id": game_id, "name": _clean(((game.get("names") or {}).get("international")), 50),
            "released": game.get("released") if isinstance(game.get("released"), int) else None,
            "cover": None}
    # speedrun.com keeps its own artwork behind a bot challenge. Try the Wikipedia infobox
    # box art, then the Steam store capsule, with and without a trailing "(… Version)".
    plain = re.sub(r"\s*\([^)]*\)\s*$", "", info["name"]).strip()
    for name in dict.fromkeys(filter(None, (info["name"], plain))):
        for lookup in (wikipedia_cover, steam_cover):
            try:
                info["cover"] = lookup(name, context)
            except (HttpClientError, ValueError, KeyError, TypeError):
                info["cover"] = None
            if info["cover"]:
                return info
    return info


def steam_appid_from_url(url):
    """The app id in a Steam store image URL (".../apps/<id>/header.jpg"), or None."""
    parts = urlsplit(str(url or ""))
    if not (parts.hostname or "").endswith("steamstatic.com"):
        return None
    match = re.search(r"/apps/(\d{1,9})/", parts.path)
    return int(match.group(1)) if match else None


def steam_appid(name, context):
    """The Steam app a game's name is (its exact name, or a name it starts), or None; a name with
    a subtitle ("Home - A Unique Horror Adventure") also tries its head, matched exactly."""
    attempts = [(name, False)]
    head = re.split(r"\s+[-–:]\s+|:\s+", str(name or ""), maxsplit=1)[0].strip()
    if head and head != name:
        attempts.append((head, True))
    for term, exact in attempts:
        wanted = _name_words(term)
        if not wanted:
            continue
        payload = _get_json("GET", STEAM_SEARCH_URL, context, max_bytes=256 * 1024,
                            params={"term": term, "cc": "us", "l": "english"})
        for item in (payload or {}).get("items") or []:
            found = _name_words((item or {}).get("name"))
            # Whole words only: "Grand Theft Auto VI" is not "Grand Theft Auto: Vice City".
            if found and str(item.get("type") or "app") == "app" and (
                    found == wanted or (not exact and found[:len(wanted)] == wanted)):
                return int(item["id"])
    return None


def _name_words(name):
    return re.findall(r"[^\W_]+", str(name or "").lower())


def fetch_steam_icons(games, context):
    """{"icons": {game name: its small Steam icon URL}} for the (name, appid or None) games; a
    game Steam does not sell is left out."""
    appids = {}
    for name, appid in games:
        try:
            appid = int(appid) if appid else steam_appid(name, context)
        except TaskCancelled:
            raise
        except Exception:  # noqa: BLE001 - a game that cannot be found just has no icon
            appid = None
        if appid:
            appids[name] = appid
    if not appids:
        return {"icons": {}}
    request = {"ids": [{"appid": appid} for appid in sorted(set(appids.values()))],
               "context": {"language": "english", "country_code": "US"}, "data_request": {"include_assets": True}}
    payload = _get_json("GET", STEAM_ITEMS_URL, context, max_bytes=512 * 1024,
                        params={"input_json": json.dumps(request)})
    hashes = {}
    for item in ((payload or {}).get("response") or {}).get("store_items") or []:
        icon = str(((item or {}).get("assets") or {}).get("community_icon") or "")
        if re.fullmatch(r"[0-9a-f]{40}", icon) and item.get("appid"):
            hashes[int(item["appid"])] = icon
    return {"icons": {name: STEAM_ICON_URL.format(appid=appid, icon=hashes[appid])
                      for name, appid in appids.items() if appid in hashes}}


def valid_steam_icons(data):
    return isinstance(data, dict) and isinstance(data.get("icons"), dict)


def steam_cover(name, context):
    payload = _get_json("GET", STEAM_SEARCH_URL, context, max_bytes=256 * 1024,
                        params={"term": name, "cc": "us", "l": "english"})
    wanted = re.sub(r"[^a-z0-9]+", "", name.lower())
    for item in (payload or {}).get("items") or []:
        found = re.sub(r"[^a-z0-9]+", "", str((item or {}).get("name") or "").lower())
        if found and (found == wanted or found.startswith(wanted)):
            return safe_image_url(item.get("tiny_image"))
    return None


def fetch_speedrun_week(previous, now, context):
    previous = previous if valid_speedrun_week(previous) else {}
    pages = []
    last_seen = parse_time(previous.get("last_verified"))
    cutoff = now.astimezone(timezone.utc) - WEEK
    for page in range(MAX_FEED_PAGES):
        payload = _get_json("GET", SPEEDRUN_RUNS_URL, context, max_bytes=2 * 1024 * 1024, params={
            "status": "verified", "orderby": "verify-date", "direction": "desc",
            "max": FEED_PAGE, "offset": page * FEED_PAGE})
        runs = (payload or {}).get("data")
        if not isinstance(runs, list):
            raise ValueError("speedrun.com returned no run feed")
        pages.append(runs)
        stamps = [parse_time(((run or {}).get("status") or {}).get("verify-date")) for run in runs]
        stamps = [stamp for stamp in stamps if stamp]
        if len(runs) < FEED_PAGE or not stamps or min(stamps) < cutoff or (last_seen and min(stamps) <= last_seen):
            break
    days, newest, since = count_new_runs(previous, pages, now)
    totals = weekly_totals(days)
    known = previous.get("games") or {}
    top = []
    for game_id, count in sorted(totals.items(), key=lambda item: -item[1])[:TOP_GAMES]:
        info = known.get(game_id) or _game_info(game_id, context)
        latest = _get_json("GET", SPEEDRUN_RUNS_URL, context, max_bytes=1024 * 1024, params={
            "game": game_id, "status": "verified", "orderby": "verify-date", "direction": "desc",
            "max": 5, "embed": "category,level,players"})
        runs = [entry for entry in (run_entry(run) for run in (latest or {}).get("data") or []) if entry]
        top.append({**info, "week_runs": count, "runs": runs[:3]})
    games = {game_id: info for game_id, info in known.items() if game_id in totals}
    games.update({item["id"]: {key: item[key] for key in ("id", "name", "released", "cover")} for item in top})
    return {"days": days, "last_verified": newest.isoformat() if newest else None,
            "since": since.isoformat(), "games": dict(list(games.items())[:12]), "top": top,
            "total": sum(totals.values())}


def valid_speedrun_week(data):
    return (isinstance(data, dict) and isinstance(data.get("days"), dict) and isinstance(data.get("top"), list)
            and all(isinstance(counts, dict) for counts in data["days"].values()))


def wikipedia_cover(name, context):
    """Infobox image of '<name> (video game)' or '<name>', skipping disambiguation pages."""
    if not name:
        return None
    titles = [f"{name} (video game)", name]
    payload = _get_json("GET", WIKIPEDIA_API_URL, context, max_bytes=128 * 1024, params={
        "action": "query", "format": "json", "formatversion": 2, "redirects": 1,
        "titles": "|".join(titles), "prop": "pageimages|pageprops", "piprop": "thumbnail",
        # Box art is usually a non-free (fair-use) file, which PageImages skips by default.
        "pithumbsize": 160, "pilicense": "any", "ppprop": "disambiguation"})
    pages = ((payload or {}).get("query") or {}).get("pages") or []
    redirects = {item.get("from"): item.get("to") for item in ((payload or {}).get("query") or {}).get("redirects") or []}
    by_title = {page.get("title"): page for page in pages if isinstance(page, dict)}
    for title in titles:
        page = by_title.get(redirects.get(title, title))
        if not page or page.get("missing") or "disambiguation" in (page.get("pageprops") or {}):
            continue
        url = safe_image_url((page.get("thumbnail") or {}).get("source"))
        if url:
            return url
    return None


def steam_release_date(text):
    """'2026 年 10 月 22 日' -> noon Pacific that day; quarters and 'coming soon' stay text."""
    match = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", str(text or ""))
    if not match:
        return None
    try:
        return datetime(int(match.group(1)), int(match.group(2)), int(match.group(3)), 12,
                        tzinfo=PACIFIC).isoformat()
    except ValueError:
        return None


def run_time(seconds):
    """1:36:07, 4:05.33, 58.40s or 0.23s; whole seconds drop their '.00'."""
    hours, rest = divmod(float(seconds), 3600)
    minutes, secs = divmod(rest, 60)
    if not hours and not minutes:
        text = f"{secs:.2f}"
        return (text[:-3] if text.endswith(".00") else text) + "s"
    text = f"{secs:05.2f}"
    if text.endswith(".00"):
        text = text[:-3]
    if hours:
        return f"{int(hours)}:{int(minutes):02d}:{text}"
    return f"{int(minutes)}:{text}"


def epic_end_label(stamp, now):
    local = stamp.astimezone(PACIFIC)
    return f"至 {local:%m/%d} {local:%H:%M}" + ("（今天）" if local.date() == now.date() else "")
