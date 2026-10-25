"""bo3.gg's public stats API for a CS2 player's maps, career and latest win.

HLTV's per-map stats and match pages sit behind its challenge pages, so these come from
bo3.gg: one lookup resolves the player's bo3 slug (kept in the tracker state), one call
reads their last six months per map, one their career totals, and their team's latest won
match is read with its maps and the player's line on each. Parsers are plain functions
over the decoded JSON.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
import json
import unicodedata
import urllib.parse

BO3_API = "https://api.bo3.gg/api/v1"
USER_AGENT = "EpaperSystem/ProTracker"
MAX_BYTES = 512 * 1024
WINDOW_DAYS = 182
# A map counts as a strength only once played this often in the window.
MIN_MAPS = 4
# The CS2 page's dynamic slot shows the team's newest win among its last RECENT_MATCHES
# results when it is at most WIN_DAYS old; a bo5 lists all five maps.
RECENT_MATCHES = 5
WIN_DAYS = 30
MAX_GAMES = 5
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


def player_record(session, slug):
    """The player's bo3 id and current team id (None when bo3 has no such player)."""
    payload = _get_json(session, f"/players/{urllib.parse.quote(slug)}")
    if not isinstance(payload, dict) or not payload.get("id"):
        return None
    return {"id": payload["id"], "team_id": payload.get("team_id")}


def parse_career(payload):
    """{maps, win_rate, kd, adr, titles} over the player's whole bo3 record (None without a map)."""
    try:
        maps = int(payload.get("games_count") or 0)
        if maps <= 0:
            return None
        deaths, rounds = int(payload.get("deaths_sum") or 0), int(payload.get("rounds_count") or 0)
        return {"maps": maps, "win_rate": int(payload.get("games_won_count") or 0) / maps,
                "kd": int(payload.get("kills_sum") or 0) / deaths if deaths else None,
                "adr": float(payload.get("damage_sum") or 0) / rounds if rounds else None,
                "titles": int(payload.get("events_won_count") or 0)}
    except (AttributeError, TypeError, ValueError):
        return None


def career(session, slug):
    return parse_career(_get_json(session, f"/players/{urllib.parse.quote(slug)}/general_stats"))


def _timestamp(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def pick_win(matches, team_id, now_ts):
    """The newest of ``matches`` (newest first) the team won, when it is at most WIN_DAYS old."""
    for match in matches or []:
        if team_id and match.get("status") == "finished" and match.get("winner_team_id") == team_id:
            start = _timestamp(match.get("start_date"))
            return match if start and now_ts - start <= WIN_DAYS * 86400 else None
    return None


def player_line(rows, player_id):
    """The player's kills, deaths and damage per round on one map (None when they are not listed)."""
    for row in rows if isinstance(rows, list) else []:
        if (row.get("steam_profile") or {}).get("player_id") == player_id:
            try:
                return {"kills": int(row.get("kills") or 0), "deaths": int(row.get("death") or 0),
                        "adr": float(row.get("adr") or 0)}
            except (TypeError, ValueError):
                return None
    return None


def _map_label(key):
    english = key.removeprefix("de_").title()
    return MAP_NAMES.get(key, (english, english))


def parse_win(match, team_id, lines=None):
    """The won match as the page draws it: {slug, start, opponent, opponent_logo, event, score,
    maps: [{map, name, english, ours, theirs, won, kills?, deaths?, adr?}], kills?, deaths?, adr?}.

    ``lines`` holds the player's line per game id; the totals add up those maps, the damage
    per round weighted by each map's rounds."""
    lines = lines or {}
    ours_first = match.get("team1_id") == team_id
    opponent = match.get("team2" if ours_first else "team1") or {}
    maps, kills, deaths, damage, rounds = [], 0, 0, 0.0, 0
    games = [game for game in match.get("games") or [] if game.get("status") == "finished"]
    for game in sorted(games, key=lambda item: item.get("number") or 0)[:MAX_GAMES]:
        key = str(game.get("map_name") or "")
        won = ((game.get("winner_team_clan") or {}).get("team") or {}).get("id") == team_id
        high, low = int(game.get("winner_clan_score") or 0), int(game.get("loser_clan_score") or 0)
        name, english = _map_label(key)
        row = {"map": key, "name": name, "english": english, "ours": high if won else low,
               "theirs": low if won else high, "won": won}
        line = lines.get(game.get("id"))
        if line:
            row.update(line)
            played = int(game.get("rounds_count") or high + low)
            kills, deaths = kills + line["kills"], deaths + line["deaths"]
            damage, rounds = damage + line["adr"] * played, rounds + played
        maps.append(row)
    ours = match.get("team1_score" if ours_first else "team2_score")
    theirs = match.get("team2_score" if ours_first else "team1_score")
    win = {"slug": match.get("slug") or "", "start": _timestamp(match.get("start_date")),
           "opponent": opponent.get("name") or "", "opponent_logo": opponent.get("image_url") or "",
           "event": (match.get("tournament") or {}).get("name") or "", "score": f"{ours} : {theirs}", "maps": maps}
    if rounds:
        win.update(kills=kills, deaths=deaths, adr=damage / rounds)
    return win


def latest_win(session, record, now_ts, known=None):
    """The player's team's newest win of the last WIN_DAYS, with its maps and the player's line
    on each (None when there is none). ``known`` is the last answer, kept as it is while that
    match is still the newest win, so a finished match is read only once."""
    team_id = (record or {}).get("team_id")
    if not team_id:
        return None
    listing = _get_json(session, "/matches", {"sort": "-start_date", "page[limit]": RECENT_MATCHES,
                                              "filter[matches.status][in]": "finished",
                                              "filter[matches.team_ids][overlap]": team_id, "with": "teams"})
    found = pick_win((listing or {}).get("results"), team_id, now_ts)
    if not found:
        return None
    if known and known.get("slug") == found.get("slug"):
        return known
    match = _get_json(session, f"/matches/{urllib.parse.quote(str(found.get('slug')))}",
                      {"scope": "show-match", "with": "games,teams,tournament_deep"}) or {}
    lines = {}
    for game in (match.get("games") or [])[:MAX_GAMES]:
        if game.get("status") == "finished" and game.get("id"):
            lines[game["id"]] = player_line(_get_json(session, f"/games/{game['id']}/players_stats"), record.get("id"))
    return parse_win(match, team_id, lines) if match.get("games") else None
