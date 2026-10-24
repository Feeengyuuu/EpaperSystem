"""bo3.gg's public stats API for the maps a CS2 player plays best.

HLTV's per-map stats sit behind its challenge pages, so the map pool comes from bo3.gg:
one lookup resolves the player's bo3 slug (kept in the tracker state), one call reads
their last six months per map. Parsers are plain functions over the decoded JSON.
"""

from __future__ import annotations

from datetime import date, timedelta
import json
import unicodedata
import urllib.parse

BO3_API = "https://api.bo3.gg/api/v1"
USER_AGENT = "EpaperSystem/ProTracker"
MAX_BYTES = 512 * 1024
WINDOW_DAYS = 182
# A map counts as a strength only once played this often in the window.
MIN_MAPS = 4
# Official Chinese names (Perfect World), then the English one.
MAP_NAMES = {
    "de_ancient": ("远古遗迹", "Ancient"), "de_anubis": ("阿努比斯", "Anubis"), "de_cache": ("死城之谜", "Cache"),
    "de_dust2": ("炙热沙城 II", "Dust2"), "de_inferno": ("炼狱小镇", "Inferno"), "de_mirage": ("荒漠迷城", "Mirage"),
    "de_nuke": ("核子危机", "Nuke"), "de_overpass": ("死亡游乐园", "Overpass"), "de_train": ("列车停放站", "Train"),
    "de_vertigo": ("殒命大厦", "Vertigo"),
}


def _plain(text):
    folded = unicodedata.normalize("NFKD", str(text or ""))
    return "".join(char for char in folded if not unicodedata.combining(char)).casefold().strip()


def _get_json(session, path, params=None, timeout=(6, 20)):
    url = f"{BO3_API}{path}" + (f"?{urllib.parse.urlencode(params)}" if params else "")
    response = session.get(url, timeout=timeout, headers={"User-Agent": USER_AGENT}, stream=True)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    body = bytearray()
    for chunk in response.iter_content(64 * 1024):
        body.extend(chunk)
        if len(body) > MAX_BYTES:
            raise ValueError(f"bo3.gg answer too large: {path}")
    return json.loads(body.decode("utf-8", errors="replace"))


def pick_player(results, nick, real_name=""):
    """The bo3 player for an HLTV nickname: an exact nickname, the real surname breaking ties."""
    matches = [row for row in results or [] if str(row.get("nickname") or "").casefold() == str(nick).casefold()]
    surname = _plain(str(real_name or "").split(" ")[-1]) if real_name else ""
    for row in matches:
        if surname and _plain(row.get("last_name")) == surname:
            return row
    return matches[0] if len(matches) == 1 or not surname else None


def resolve_slug(session, nick, real_name=""):
    """The bo3.gg slug of an HLTV player (None when bo3 has no such player)."""
    for name in dict.fromkeys((nick, str(nick).lower())):
        payload = _get_json(session, "/players", {"filter[nickname][eq]": name, "page[limit]": 10})
        found = pick_player((payload or {}).get("results"), nick, real_name)
        if found and found.get("slug"):
            return found["slug"]
    return None


def parse_map_stats(payload):
    """[{map, name, english, maps, rating, kpr, adr}] for the known competitive maps, best first.

    ``rating`` is bo3's 0-10 player score; maps played fewer than MIN_MAPS times sink below
    the rest whatever their score, so one lucky map does not head the list.
    """
    rows = []
    for row in payload if isinstance(payload, list) else []:
        key = str(row.get("map_name") or "")
        if key not in MAP_NAMES:
            continue
        try:
            rows.append({"map": key, "name": MAP_NAMES[key][0], "english": MAP_NAMES[key][1],
                         "maps": int(row.get("maps_count") or 0), "rating": float(row.get("avg_player_rating") or 0),
                         "kpr": float(row.get("avg_kills") or 0), "adr": float(row.get("avg_damage") or 0)})
        except (TypeError, ValueError):
            continue
    rows.sort(key=lambda item: (item["maps"] >= MIN_MAPS, item["rating"]), reverse=True)
    return rows


def map_stats(session, slug, today=None):
    """The player's last six months per map, best first."""
    today = today or date.today()
    payload = _get_json(session, f"/players/{urllib.parse.quote(slug)}/map_stats",
                        {"filter[begin_at_to]": today.isoformat(),
                         "filter[begin_at_from]": (today - timedelta(days=WINDOW_DAYS)).isoformat()})
    return parse_map_stats(payload)
