"""lolesports.com's public feeds for a LoL pro's team: its schedule, series and each game's line.

The schedule of the big leagues comes in one call; a series' games come from its event
details; each game's final numbers come from the live stats feed, which answers any start
time after a game ended with that game's last frames (and a live game's newest frames).
Parsers are plain functions over the decoded JSON.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json

API = "https://esports-api.lolesports.com/persisted/gw"
FEED = "https://feed.lolesports.com/livestats/v1"
USER_AGENT = "EpaperSystem/ProTracker"
MAX_BYTES = 2 * 1024 * 1024
# Worlds, MSI, First Stand, LCK, LPL and LEC: the leagues the tracked pros play in.
LEAGUE_IDS = ("98767975604431411", "98767991325878492", "113464388705111224", "98767991310872058",
              "98767991314006698", "98767991302996019")
LEAGUE_NAMES = {"worlds": "全球总决赛", "msi": "季中冠军赛", "first_stand": "First Stand", "lck": "LCK",
                "lpl": "LPL", "lec": "LEC"}
STAGE_NAMES = {"Swiss": "瑞士轮", "Play-Ins": "入围赛", "Quarterfinals": "八强赛", "Semifinals": "半决赛",
               "Finals": "决赛", "Playoffs": "季后赛", "Groups": "小组赛", "Regional Qualifier": "资格赛"}
# The live stats feed's dragon names, drawn as dots in each kind's colour.
DRAGONS = ("infernal", "mountain", "ocean", "cloud", "hextech", "chemtech", "elder")


def _get(session, url, params=None, api_key="", timeout=(6, 25)):
    """GET a JSON answer (None for an empty 204, which the feed sends before a game starts)."""
    headers = {"User-Agent": USER_AGENT}
    if api_key:
        headers["x-api-key"] = api_key
    response = session.get(url, params=params or {}, headers=headers, timeout=timeout, stream=True)
    if response.status_code == 204:
        return None
    response.raise_for_status()
    body = bytearray()
    for chunk in response.iter_content(64 * 1024):
        body.extend(chunk)
        if len(body) > MAX_BYTES:
            raise ValueError(f"lolesports answer too large: {url}")
    return json.loads(body.decode("utf-8", errors="replace")) if body else None


def _timestamp(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _team(row):
    result = row.get("result") or {}
    return {"code": row.get("code") or "", "name": row.get("name") or "", "image": row.get("image") or "",
            "wins": result.get("gameWins"), "outcome": result.get("outcome") or "",
            "record": [(row.get("record") or {}).get(key) for key in ("wins", "losses")] if row.get("record") else None}


def parse_schedule(payload):
    """[{id, start, state, league, stage, best_of, teams}] of the schedule's matches, oldest first."""
    events = ((payload or {}).get("data") or {}).get("schedule", {}).get("events") or []
    rows = []
    for event in events:
        match = event.get("match") or {}
        if event.get("type") != "match" or not match.get("id"):
            continue
        rows.append({"id": match["id"], "start": _timestamp(event.get("startTime")), "state": event.get("state") or "",
                     "league": (event.get("league") or {}).get("slug") or "", "stage": event.get("blockName") or "",
                     "best_of": (match.get("strategy") or {}).get("count") or 1,
                     "teams": [_team(row) for row in match.get("teams") or []]})
    return sorted(rows, key=lambda row: row["start"] or 0)


def schedule(session, api_key):
    return parse_schedule(_get(session, f"{API}/getSchedule", {"hl": "en-US", "leagueId": ",".join(LEAGUE_IDS)},
                               api_key))


def _sides(event, code):
    teams = event.get("teams") or []
    ours = next((team for team in teams if team["code"] == code), None)
    theirs = next((team for team in teams if team is not ours), None)
    return ours, theirs


def team_series(events, code, now_ts, recent=3):
    """The team's next (or running) series and its last ``recent`` results, newest first."""
    upcoming, results = None, []
    for event in events:
        ours, theirs = _sides(event, code)
        if not ours or not theirs or theirs["code"] in ("", "TBD"):
            continue
        if event["state"] == "completed":
            results.append({**event, "ours": ours, "theirs": theirs, "won": ours.get("outcome") == "win"
                            or (ours.get("wins") or 0) > (theirs.get("wins") or 0)})
        elif upcoming is None and (event["state"] == "inProgress" or (event["start"] or 0) >= now_ts - 6 * 3600):
            upcoming = {**event, "ours": ours, "theirs": theirs}
    return upcoming, results[::-1][:recent]


def parse_event(payload):
    """A series' teams by id ({id: {code, name, image}}) and its games [{id, number, state}] in order."""
    match = (((payload or {}).get("data") or {}).get("event") or {}).get("match") or {}
    teams = {row.get("id"): {"code": row.get("code") or "", "name": row.get("name") or "", "image": row.get("image") or ""}
             for row in match.get("teams") or [] if row.get("id")}
    games = [{"id": game.get("id"), "number": game.get("number") or 0, "state": game.get("state") or ""}
             for game in match.get("games") or [] if game.get("id")]
    return {"teams": teams, "games": sorted(games, key=lambda game: game["number"])}


def event(session, api_key, match_id):
    return parse_event(_get(session, f"{API}/getEventDetails", {"hl": "en-US", "id": match_id}, api_key))


def _is_player(row, player):
    """The participant whose broadcast name is the pro's ("T1 Faker" for Faker)."""
    name = str(row.get("name") or "").casefold()
    player = str(player or "").casefold()
    return bool(player) and (name == player or name.endswith(" " + player))


def parse_game(payload):
    """One game's newest frame: {state, at, sides: {"blue"|"red": {team_id, kills, gold, towers,
    inhibitors, barons, dragons, players: [{name, champion, role, participant, kills, deaths,
    assists, cs, gold, level}]}}} (None before the feed has a frame)."""
    meta = (payload or {}).get("gameMetadata") or {}
    frames = (payload or {}).get("frames") or []
    if not frames:
        return None
    frame = frames[-1]
    sides = {}
    for side in ("blue", "red"):
        team_meta, stats = meta.get(f"{side}TeamMetadata") or {}, frame.get(f"{side}Team") or {}
        lines = {row.get("participantId"): row for row in stats.get("participants") or []}
        players = []
        for row in team_meta.get("participantMetadata") or []:
            line = lines.get(row.get("participantId")) or {}
            players.append({"name": row.get("summonerName") or "", "champion": row.get("championId") or "",
                            "role": row.get("role") or "", "participant": row.get("participantId"),
                            "kills": int(line.get("kills") or 0), "deaths": int(line.get("deaths") or 0),
                            "assists": int(line.get("assists") or 0), "cs": int(line.get("creepScore") or 0),
                            "gold": int(line.get("totalGold") or 0), "level": int(line.get("level") or 0)})
        sides[side] = {"team_id": team_meta.get("esportsTeamId"), "kills": int(stats.get("totalKills") or 0),
                       "gold": int(stats.get("totalGold") or 0), "towers": int(stats.get("towers") or 0),
                       "inhibitors": int(stats.get("inhibitors") or 0), "barons": int(stats.get("barons") or 0),
                       "dragons": [str(name) for name in stats.get("dragons") or []], "players": players}
    return {"state": frame.get("gameState") or "", "at": _timestamp(frame.get("rfc460Timestamp")), "sides": sides}


def parse_shares(payload):
    """Each participant's share of the fights in a game's newest frame:
    {participant id (str): {kp, damage_share, wards, items}}."""
    frames = (payload or {}).get("frames") or []
    shares = {}
    for line in (frames[-1].get("participants") if frames else []) or []:
        shares[str(line.get("participantId"))] = {
            "kp": float(line.get("killParticipation") or 0), "damage_share": float(line.get("championDamageShare") or 0),
            "wards": int(line.get("wardsPlaced") or 0), "items": [int(item) for item in line.get("items") or []]}
    return shares


def feed_stamp(now_ts):
    """The feed's start time for a game's newest frames: a minute back, on a ten-second step."""
    stamp = int(now_ts - 60) // 10 * 10
    return datetime.fromtimestamp(stamp, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def game(session, game_id, now_ts, details=False):
    """A game's newest frame (its final one once it is over), with everyone's fight shares when
    ``details``; None while the feed has nothing for it."""
    parsed = parse_game(_get(session, f"{FEED}/window/{game_id}", {"startingTime": feed_stamp(now_ts)}))
    if parsed and details:
        parsed["shares"] = parse_shares(_get(session, f"{FEED}/details/{game_id}",
                                             {"startingTime": feed_stamp(now_ts)}))
    return parsed


def player_line(parsed, player):
    """The pro's line in a parsed game, with their team's numbers as ``ours`` and the other side's as
    ``theirs`` (None when the pro sat the game out)."""
    for side, other in (("blue", "red"), ("red", "blue")):
        ours = ((parsed or {}).get("sides") or {}).get(side) or {}
        found = next((row for row in ours.get("players") or [] if _is_player(row, player)), None)
        if found:
            theirs = parsed["sides"].get(other) or {}
            team = {key: value for key, value in ours.items() if key != "players"}
            return {**found, **((parsed.get("shares") or {}).get(str(found.get("participant"))) or {}),
                    "side": side, "state": parsed.get("state"), "at": parsed.get("at"), "ours": team,
                    "theirs": {key: value for key, value in theirs.items() if key != "players"}}
    return None


def series_winners(lines, ours_won_series, our_wins, finished=True):
    """Who won each finished game of a series, as {index in ``lines``: True for the pro's team}.

    The feed's frames carry no winner, but a finished series' last game always goes to the
    series' winner, and the score says how many of the other games each side took; those go
    to the side that destroyed more towers in them."""
    played = [index for index, line in enumerate(lines) if line and line.get("state") == "finished"]
    result = {}
    if not played:
        return result
    ours_left = int(our_wins or 0)
    if finished:
        result[played[-1]] = bool(ours_won_series)
        ours_left -= 1 if ours_won_series else 0
        played = played[:-1]
    rest = sorted(played, key=lambda index: lines[index]["ours"]["towers"] - lines[index]["theirs"]["towers"],
                  reverse=True)
    for rank, index in enumerate(rest):
        result[index] = rank < ours_left
    return result
