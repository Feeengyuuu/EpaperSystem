"""ATP and WTA tennis for the lower panel: ESPN's keyless tennis scoreboards (about 260 KB and
500 KB, each the tour's tournaments of the week with their whole draws).

Only the singles main draw is kept: each match's round (in Chinese), state, start, ESPN's one-line
result and both players with their seed, country flag, headshot and set scores (tie-breaks
included). Each tour shows its biggest tournament of the week, live matches first, then the latest
results, then the next matches. Grand Slams and the year-end Finals rank as big events, the
1000-level events above the rest; an early round at a small event never counts as a live game.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

from runtime.refresh_contracts import TaskCancelled

from . import event_ranking, extra_feeds, tennis_events, tennis_photos

logger = logging.getLogger(__name__)
SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/tennis/{tour}/scoreboard"
HEADSHOT_URL = "https://a.espncdn.com/i/headshots/tennis/players/full/{id}.png"
TOURS = ("ATP", "WTA")
# v2: each tournament keeps ESPN's whole place line ("place"), which finds its own mark and venue.
VERSION = "tennis-scoreboard-v2"
MAX_BYTES = 1536 * 1024
TTL_SECONDS = 30 * 60
MATCHES_KEPT = 16
SHOWN = 3
LATE_ROUNDS = {"Quarterfinal", "Semifinal", "Final"}
RECENT_WINDOW = timedelta(hours=12)
ROUNDS_ZH = {"Round 1": "第一轮", "Round 2": "第二轮", "Round 3": "第三轮", "Round 4": "第四轮", "Round of 16": "16强",
             "Quarterfinal": "1/4决赛", "Semifinal": "半决赛", "Final": "决赛", "Round Robin": "小组赛",
             "Group Stage": "小组赛"}
SLAMS = (("australian open", "澳大利亚网球公开赛"), ("roland garros|french open", "法国网球公开赛"),
         ("wimbledon", "温布尔登网球锦标赛"), ("us open", "美国网球公开赛"))
FINALS = (("atp finals", "ATP 年终总决赛"), ("wta finals", "WTA 年终总决赛"))
THOUSANDS = (("indian wells|bnp paribas open", "印第安维尔斯大师赛"), ("miami", "迈阿密大师赛"),
             ("monte.?carlo", "蒙特卡洛大师赛"), ("madrid", "马德里大师赛"), ("internazionali|rome|italian open", "罗马大师赛"),
             ("canadian open|national bank|canada", "加拿大大师赛"), ("cincinnati", "辛辛那提大师赛"),
             ("shanghai", "上海大师赛"), ("paris", "巴黎大师赛"), ("china open|beijing", "中国网球公开赛"),
             ("wuhan", "武汉网球公开赛"), ("qatar|doha", "多哈公开赛"), ("dubai", "迪拜网球锦标赛"))


SLAM_KEYS = (("australian open", "australian_open"), ("roland garros|french open", "roland_garros"),
             ("wimbledon", "wimbledon"), ("us open", "us_open"))
# The 500-level events of each tour (the rest below the 1000s are 250s).
ATP_500 = ("rotterdam|abn amro|dallas|rio open|mexican open|acapulco|abierto mexicano|dubai|qatar|doha|barcelona|munich|"
           "bmw open|queen|halle|terra wortmann|hamburg|washington|citi open|china open|beijing|japan open|tokyo|basel|"
           "swiss indoors|vienna|erste bank")
WTA_1000 = ("qatar|doha|dubai|indian wells|bnp paribas|miami|madrid|internazionali|rome|italian open|canadian|"
            "national bank|cincinnati|china open|beijing|wuhan")
WTA_500 = ("brisbane|adelaide|abu dhabi|linz|charleston|credit one|stuttgart|porsche|berlin|queen|bad homburg|eastbourne|"
           "washington|citi open|monterrey|san diego|seoul|korea open|pan pacific|toray|tokyo|ningbo|strasbourg|"
           "guadalajara|zhengzhou")
# The bundled official marks: the Grand Slams' logos, the ATP Finals' logo and the tours' own tier
# stamps and tags.
MARKS = {"australian_open", "roland_garros", "wimbledon", "us_open"}
# The place beside a tournament's name by its official logo, matched in ESPN's venue line: the host
# city's own logo where ESPN names only the city (Paris), else the venue's (assets/logos).
PLACE_MARKS = (("indian wells", "tennis_place_indian_wells"), ("inalpi|turin|torino", "tennis_place_inalpi_arena"),
               ("paris", "tennis_place_paris"))


def place_mark(tournament):
    """The bundled logo naming the tournament's place (tennis_place_paris ...), or None."""
    text = str((tournament or {}).get("city") or "").lower()
    return next((key for pattern, key in PLACE_MARKS if re.search(pattern, text)), None)


def tournament_category(tournament):
    """"australian_open" / "roland_garros" / "wimbledon" / "us_open" for a Grand Slam, "finals" for
    the year-end Finals, else the tour's tier: "1000", "500" or "250"."""
    tournament = tournament or {}
    name = str(tournament.get("name") or "").lower()
    slam = next((key for pattern, key in SLAM_KEYS if re.search(pattern, name)), None)
    if slam:
        return slam
    if _match_name(name, FINALS):
        return "finals"
    if tournament.get("tour") == "WTA":
        if re.search(WTA_1000, name) or "1000" in name:
            return "1000"
        return "500" if re.search(WTA_500, name) else "250"
    if _match_name(name, THOUSANDS) or re.search(r"masters|1000", name):
        return "1000"
    return "500" if re.search(ATP_500, name) else "250"


def tournament_mark(tournament):
    """The bundled official mark naming the tournament: a Grand Slam's logo, else the event's own logo
    (tennis_events), else the ATP Finals' logo or the tour's tier stamp or tag (tennis_atp_1000 /
    tennis_wta_250 ...); None for a WTA Finals without its own."""
    category = tournament_category(tournament)
    tour = "wta" if (tournament or {}).get("tour") == "WTA" else "atp"
    if category in MARKS:
        return f"tennis_{category}"
    event = tennis_events.event_for(tournament)
    if event and event["mark"]:
        return event["mark"]
    if category == "finals":
        return "tennis_atp_finals" if tour == "atp" else None
    return f"tennis_{tour}_{category}"


def _date(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _match_name(name, table):
    text = str(name or "").lower()
    return next((zh for pattern, zh in table if re.search(pattern, text)), "")


def tournament_tier(tournament):
    name = (tournament or {}).get("name")
    if (tournament or {}).get("major") or _match_name(name, SLAMS) or _match_name(name, FINALS):
        return "A"
    if _match_name(name, THOUSANDS) or re.search(r"masters|1000", str(name or "").lower()):
        return "B"
    return "C"


def tournament_zh(name):
    return _match_name(name, SLAMS) or _match_name(name, FINALS) or _match_name(name, THOUSANDS)


def _player(raw):
    athlete = raw.get("athlete") if isinstance(raw.get("athlete"), dict) else {}
    flag = athlete.get("flag") if isinstance(athlete.get("flag"), dict) else {}
    seed = (raw.get("curatedRank") or {}).get("current") if isinstance(raw.get("curatedRank"), dict) else None
    sets = []
    for line in raw.get("linescores") or []:
        if isinstance(line, dict) and isinstance(line.get("value"), (int, float)):
            tiebreak = line.get("tiebreak")
            sets.append([int(line["value"]), int(tiebreak) if isinstance(tiebreak, (int, float)) else None])
    player_id = str(raw.get("id") or "")
    return {"id": player_id, "name": str(athlete.get("displayName") or "TBD")[:36],
            "short": str(athlete.get("shortName") or athlete.get("displayName") or "TBD")[:24],
            "country": str(flag.get("alt") or "")[:30], "flag": str(flag.get("href") or ""),
            "headshot": HEADSHOT_URL.format(id=player_id) if player_id.isdigit() else "",
            "seed": seed if isinstance(seed, int) and 0 < seed < 99 else None, "winner": bool(raw.get("winner")),
            "sets": sets, "order": raw.get("order") or 0}


def _match(raw):
    status = (raw.get("status") or {}).get("type") or {}
    round_name = str((raw.get("round") or {}).get("displayName") or "")
    note = next((item.get("text") for item in raw.get("notes") or [] if isinstance(item, dict) and item.get("text")), "")
    players = sorted((_player(item) for item in raw.get("competitors") or [] if isinstance(item, dict)),
                     key=lambda player: player["order"])
    return {"round": round_name, "round_zh": ROUNDS_ZH.get(round_name, round_name), "date": str(raw.get("date") or ""),
            "state": str(status.get("state") or "pre"), "detail": str(status.get("detail") or "")[:40],
            "note": str(note or "")[:120],
            "players": [{key: value for key, value in player.items() if key != "order"} for player in players[:2]]}


def parse_scoreboard(payload, tour):
    """{"tour", "tournaments": [...]}: each tournament's singles main draw, latest matches kept."""
    tournaments = []
    for raw in (payload or {}).get("events") or [] if isinstance(payload, dict) else []:
        if not isinstance(raw, dict):
            continue
        matches = []
        for group in raw.get("groupings") or []:
            name = str(((group or {}).get("grouping") or {}).get("displayName") or "")
            if "singles" not in name.lower():
                continue
            for item in group.get("competitions") or []:
                if isinstance(item, dict) and not str((item.get("round") or {}).get("displayName") or "").startswith("Qualifying"):
                    match = _match(item)
                    if len(match["players"]) == 2:
                        matches.append(match)
        if not matches:
            continue
        matches.sort(key=lambda match: _date(match["date"]) or datetime.min.replace(tzinfo=timezone.utc))
        venue = raw.get("venue") if isinstance(raw.get("venue"), dict) else {}
        tournament = {"id": str(raw.get("id") or ""), "tour": tour, "name": str(raw.get("name") or "")[:60],
                      "city": str(venue.get("displayName") or "").split(",")[0].strip()[:30],
                      "place": str(venue.get("displayName") or "").strip()[:80],
                      "start": str(raw.get("date") or ""), "end": str(raw.get("endDate") or ""),
                      "major": bool(raw.get("major")), "matches": matches[-MATCHES_KEPT:]}
        tournament["name_zh"] = tournament_zh(tournament["name"])
        tournament["tier"] = tournament_tier(tournament)
        tournaments.append(tournament)
    return {"tour": tour, "tournaments": tournaments}


def _rank(tournament):
    live = any(match["state"] == "in" for match in tournament["matches"])
    return (live, {"A": 3, "B": 2, "C": 1}.get(tournament.get("tier"), 0))


def _shown(tournament, now):
    matches = tournament["matches"]
    live = [match for match in matches if match["state"] == "in"]
    done = sorted((match for match in matches if match["state"] == "post"),
                  key=lambda match: _date(match["date"]) or now, reverse=True)
    ahead = [match for match in matches if match["state"] == "pre"]
    shown = live[:SHOWN]
    if len(shown) < SHOWN and done:
        shown.append(done[0])
    shown += ahead[:SHOWN - len(shown)]
    shown += done[1:1 + SHOWN - len(shown)]
    return shown[:SHOWN]


def build_card(tours, now):
    """{"sport": "TENNIS", "status", "tours": {tour: {"tournament", "shown"}}} or None."""
    now = now.astimezone(timezone.utc)
    picked = {}
    for tour in TOURS:
        tournaments = ((tours or {}).get(tour) or {}).get("tournaments") or []
        if tournaments:
            tournament = max(tournaments, key=_rank)
            picked[tour] = {"tournament": tournament, "shown": _shown(tournament, now)}
    if not picked:
        return None
    shown = [match for entry in picked.values() for match in entry["shown"]]
    if any(match["state"] == "in" for match in shown):
        status = "LIVE"
    elif any(match["state"] == "post" and (_date(match["date"]) or now) >= now - RECENT_WINDOW for match in shown):
        status = "RECENT"
    else:
        status = "NEXT"
    return {"sport": "TENNIS", "status": status, "tours": picked}


def _tour_score(tournament, now):
    tier = tournament.get("tier") or "C"
    matches = tournament["matches"]
    live = [match for match in matches if match["state"] == "in"]
    if any(match["round"] in LATE_ROUNDS for match in live) or (live and tier in ("A", "B")):
        return event_ranking.score("live", None, now, tier)
    scores = []
    ahead = [_date(match["date"]) for match in matches if match["state"] == "pre" and _date(match["date"])]
    if ahead:
        scores.append(event_ranking.score("scheduled", min(ahead), now, tier))
    done = [_date(match["date"]) for match in matches if match["state"] in ("post", "in") and _date(match["date"])]
    if done:
        latest = max(done)
        scores.append(event_ranking.score("final", latest, now, tier, end=latest + timedelta(hours=2)))
    return max(scores, default=0.0)


def candidate(card, now):
    if not card:
        return None
    score = max(_tour_score(entry["tournament"], now) for entry in card["tours"].values())
    return {"kind": "extra", "key": "TENNIS", "card": card, "score": score, "order": 8}


def load_card(plugin, settings, now):
    now = now.astimezone(timezone.utc)

    def fetch(client, deadline):
        data = {}
        for tour in TOURS:
            try:
                response = client.request_json("GET", SCOREBOARD_URL.format(tour=tour.lower()),
                                               timeout=extra_feeds.remaining(deadline), max_bytes=MAX_BYTES)
                data[tour] = parse_scoreboard(response.data, tour)
            except TaskCancelled:
                raise
            except Exception as exc:  # noqa: BLE001 - one tour alone still fills the panel.
                logger.warning("%s scoreboard unavailable: %s", tour, type(exc).__name__)
        if not data:
            raise ValueError("no tennis scoreboard answered")
        return data

    data, state = extra_feeds.load(plugin, settings, "tennis", VERSION, fetch, TTL_SECONDS, now, label="ESPN TENNIS")
    card = build_card(data, now)
    if card:
        attach_photos(plugin, settings, card, now)
    return card, state


def attach_photos(plugin, settings, card, now):
    """Give each shown player the tour's own photo (``photo``), for when ESPN has no headshot of them:
    every match shows its players' faces."""
    shown = [(tour, player) for tour, entry in card["tours"].items() for match in entry["shown"]
             for player in match["players"]]
    photos = tennis_photos.official_photos(plugin, settings, [dict(player, tour=tour) for tour, player in shown], now)
    for tour, player in shown:
        player["photo"] = photos.get(f"{tour}:{tennis_photos.name_key(player.get('name'))}", "")
