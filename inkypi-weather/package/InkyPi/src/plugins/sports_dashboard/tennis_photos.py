"""The tours' own player photos, for tennis players ESPN has no headshot of.

ATP: its site search answers a player's official headshot URL (a transparent face cut-out). WTA: its
player search gives the player's id, whose page carries the official torso cut-out
(<Name>-Torso_<id>.png), else its plain headshot photo (a JPG, shown round). A player the tour's
site does not answer for (ATP's site refuses the frame's requests with 403 though a desktop's pass)
takes their Wikidata photo instead: the tennis player's image (P18), a Commons photo the panel
crops to the head and shows round. Each answer, a photo or none, is kept in the dashboard's cache
(a found photo for 60 days, a miss for 7), so a player is looked up about once; a cache-only redraw
only reads what is kept.
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from runtime.refresh_contracts import TaskCancelled

from . import extra_feeds

logger = logging.getLogger(__name__)
ATP_SEARCH = "https://www.atptour.com/en/-/www/site-search/{query}/"
WTA_SEARCH = "https://api.wtatennis.com/tennis/players/"
WTA_PAGE = "https://www.wtatennis.com/players/{id}/{slug}"
WTA_SIZE = "?width=480&height=450"
WTA_HEADSHOT = "https://wtafiles.blob.core.windows.net/images/headshots/{id}.jpg"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                         "Chrome/130.0 Safari/537.36"}
WIKIDATA_API = "https://www.wikidata.org/w/api.php"
COMMONS_THUMB = "https://upload.wikimedia.org/wikipedia/commons/thumb/{a}/{ab}/{name}/{width}px-{name}"
# Wikimedia asks automated clients to name themselves.
WIKI_HEADERS = {"User-Agent": "InkyPi/1.0 (https://github.com/Feeengyuuu/EpaperSystem)"}
WIKI_PHOTO_WIDTH = 250
CACHE_FILE = "extra_tennis_photos.json"
# 2: the Wikidata fallback; the tours' old misses are looked up again.
VERSION = 2
FOUND_TTL, MISSED_TTL = timedelta(days=60), timedelta(days=7)
LOOKUPS_PER_REFRESH = 6
MAX_BYTES = 768 * 1024


def name_key(name):
    """"Iga Świątek" -> "iga swiatek": letters and spaces only, accents dropped."""
    text = unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode("ascii").lower()
    return " ".join(re.sub(r"[^a-z ]+", " ", text).split())


def _atp(client, name, deadline):
    last = name.split(" ")[-1] if " " not in name.strip() else " ".join(name.split()[1:])
    response = client.request_json("GET", ATP_SEARCH.format(query=quote(last)), headers=HEADERS,
                                   timeout=extra_feeds.remaining(deadline), max_bytes=MAX_BYTES)
    wanted = name_key(name)
    for player in (response.data or {}).get("Players") or []:
        if isinstance(player, dict) and name_key(f"{player.get('FirstName')} {player.get('LastName')}") == wanted:
            return str(player.get("PlayerHeadshotUrl") or "")
    return ""


def _wta(client, name, deadline):
    # The search matches any part of a name: the full name finds the player alone.
    response = client.request_json("GET", WTA_SEARCH, params={"name": name, "pageSize": 20}, headers=HEADERS,
                                   timeout=extra_feeds.remaining(deadline), max_bytes=MAX_BYTES)
    wanted = name_key(name)
    player = next((item for item in (response.data or {}).get("content") or []
                   if isinstance(item, dict) and name_key(item.get("fullName")) == wanted), None)
    if not player or not player.get("id"):
        return ""
    page = client.request_text("GET", WTA_PAGE.format(id=player["id"], slug=wanted.replace(" ", "-")), headers=HEADERS,
                               timeout=extra_feeds.remaining(deadline), max_bytes=2 * 1024 * 1024)
    text = str(page.data or "")
    found = re.search(rf"https://photoresources\.wtatennis\.com/photo-resources/[^\"'\s?]+_{player['id']}\.png", text)
    if found:
        return found.group(0) + WTA_SIZE
    # A player without a torso cut-out still has the WTA's plain headshot photo.
    headshot = WTA_HEADSHOT.format(id=player["id"])
    return headshot if headshot in text else ""


def commons_thumb(file_name, width=WIKI_PHOTO_WIDTH):
    """A Commons photo's thumbnail URL ``width`` px wide; "" for a file that is not a JPEG or PNG."""
    name = str(file_name or "").strip().replace(" ", "_")
    if not name.lower().endswith((".jpg", ".jpeg", ".png")):
        return ""
    digest = hashlib.md5(name.encode("utf-8")).hexdigest()  # noqa: S324 - Commons' own path scheme
    quoted = quote(name, safe="")
    return COMMONS_THUMB.format(a=digest[0], ab=digest[:2], name=quoted, width=int(width))


def _wikidata(client, name, deadline):
    """The tennis player's Wikidata photo (P18) as a Commons thumbnail URL, else ""."""
    found = client.request_json("GET", WIKIDATA_API, params={"action": "wbsearchentities", "search": name, "language": "en",
                                                             "type": "item", "limit": 5, "format": "json"},
                                headers=WIKI_HEADERS, timeout=extra_feeds.remaining(deadline), max_bytes=MAX_BYTES)
    wanted = name_key(name)
    hit = next((item for item in (found.data or {}).get("search") or [] if isinstance(item, dict)
                and "tennis" in str(item.get("description") or "").lower()
                and name_key(item.get("label")) == wanted), None)
    if not hit or not re.fullmatch(r"Q\d+", str(hit.get("id") or "")):
        return ""
    entity = client.request_json("GET", WIKIDATA_API, params={"action": "wbgetentities", "ids": hit["id"], "props": "claims",
                                                              "format": "json"},
                                 headers=WIKI_HEADERS, timeout=extra_feeds.remaining(deadline), max_bytes=MAX_BYTES)
    claims = (((entity.data or {}).get("entities") or {}).get(hit["id"]) or {}).get("claims") or {}
    for claim in claims.get("P18") or []:
        value = ((claim.get("mainsnak") or {}).get("datavalue") or {}).get("value") if isinstance(claim, dict) else None
        url = commons_thumb(value)
        if url:
            return url
    return ""


def official_photos(plugin, settings, players, now):
    """{"<TOUR>:<name key>": photo URL or ""} for ``players`` ({"name", "tour"}): the kept answers, with
    up to LOOKUPS_PER_REFRESH new lookups when the network is allowed."""
    now = now.astimezone(timezone.utc)
    path = plugin._sports_dashboard_cache_dir() / CACHE_FILE
    cache = plugin._read_json_file(path)
    kept = cache.get("photos") if isinstance(cache, dict) and cache.get("version") == VERSION else None
    kept = kept if isinstance(kept, dict) else {}

    def current(entry):
        try:
            at = datetime.fromisoformat(entry["at"])
        except (KeyError, TypeError, ValueError):
            return False
        return now - at <= (FOUND_TTL if entry.get("url") else MISSED_TTL)

    keys = {f"{player.get('tour')}:{name_key(player.get('name'))}": player for player in players
            if player.get("tour") in ("ATP", "WTA") and name_key(player.get("name")) and name_key(player.get("name")) != "tbd"}
    wanted = [key for key in keys if not (isinstance(kept.get(key), dict) and current(kept[key]))]
    if wanted and not plugin._provider_cache_only(settings):
        changed = False
        try:
            with extra_feeds.create_single_attempt_http_client() as client:
                deadline = time.monotonic() + extra_feeds.DEADLINE_SECONDS
                for key in wanted[:LOOKUPS_PER_REFRESH]:
                    tour, name = keys[key]["tour"], keys[key]["name"]
                    url = ""
                    try:
                        url = (_atp if tour == "ATP" else _wta)(client, name, deadline)
                    except TaskCancelled:
                        raise
                    except Exception as exc:  # noqa: BLE001 - Wikidata answers in its place.
                        logger.info("No %s photo for a player: %s", tour, type(exc).__name__)
                        if isinstance(exc, TimeoutError):
                            break
                    if not url:
                        try:
                            url = _wikidata(client, name, deadline)
                        except TaskCancelled:
                            raise
                        except Exception as exc:  # noqa: BLE001 - this player is looked up again next time.
                            logger.info("No Wikidata photo for a player: %s", type(exc).__name__)
                            if isinstance(exc, TimeoutError):
                                break
                            continue
                    kept[key] = {"url": url, "at": now.isoformat()}
                    changed = True
        except TaskCancelled:
            raise
        except Exception as exc:  # noqa: BLE001 - the panel keeps ESPN's photos alone.
            logger.info("Tour photo lookups unavailable: %s", type(exc).__name__)
        if changed:
            try:
                plugin._write_json_file(path, {"version": VERSION, "photos": kept})
            except OSError as exc:
                logger.warning("Failed to keep the tennis photos: %s", exc)
    return {key: (kept.get(key) or {}).get("url", "") for key in keys if isinstance(kept.get(key), dict)}
