"""Turn a rolling CS2 match feed into one coherent, named-team event card."""

from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
import re
from urllib.parse import urlparse


FOLLOWED_TEAMS = (
    "Natus Vincere",
    "NAVI",
    "Spirit",
    "Team Spirit",
    "MOUZ",
    "mousesports",
    "Vitality",
    "Team Vitality",
    "G2",
    "G2 Esports",
    "FaZe",
    "FaZe Clan",
    "Falcons",
    "Team Falcons",
    "FURIA",
    "Aurora",
    "Astralis",
    "Liquid",
    "Team Liquid",
    "HEROIC",
    "Virtus.pro",
    "BIG",
    "Complexity",
    "GamerLegion",
    "paiN",
    "MIBR",
    "The MongolZ",
    "MongolZ",
    "3DMAX",
    "Ninjas in Pyjamas",
    "NIP",
    "PARIVISION",
    "BetBoom",
    "BetBoom Team",
    "TYLOO",
    "Legacy",
    "fnatic",
    "9z",
    "Eternal Fire",
    "B8",
    "SAW",
    "ENCE",
)
PLACEHOLDERS = {"", "tbd", "tba", "unknown", "tobedetermined", "tobeannounced"}


def normalized_name(value):
    return "".join(c for c in str(value or "").casefold() if c.isalnum())


def followed_names(settings):
    configured = str((settings or {}).get("pandaScoreCs2FollowedTeams") or "").strip()
    names = re.split(r"[,;\n]+", configured)[:64] if configured else FOLLOWED_TEAMS
    return {normalized_name(name[:80]) for name in names if normalized_name(name)}


def match_teams(item):
    opponents = item.get("opponents") if isinstance(item, Mapping) else None
    if not isinstance(opponents, list):
        return []
    teams = [entry.get("opponent") for entry in opponents if isinstance(entry, Mapping)]
    teams = [team for team in teams if isinstance(team, Mapping)]
    if len(teams) != 2:
        return []
    names = [normalized_name(team.get("name")) for team in teams]
    if any(name in PLACEHOLDERS or name.startswith(("winnerof", "loserof")) for name in names):
        return []
    return teams


def has_followed_team(item, settings=None):
    names = followed_names(settings)
    return any(normalized_name(team.get("name")) in names for team in match_teams(item))


def utc_datetime(value):
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def safe_logo_url(value):
    text = str(value or "").strip()
    try:
        parsed = urlparse(text)
        valid = (
            parsed.scheme == "https"
            and parsed.hostname
            in {"cdn-api.pandascore.co", "cdn.pandascore.co", "cdn.pandascore.net", "img-cdn.hltv.org"}
            and not parsed.username
            and not parsed.password
            and parsed.port in {None, 443}
        )
    except ValueError:
        return ""
    return text if valid else ""


def _mapping(value):
    return value if isinstance(value, Mapping) else {}


def _integer(value):
    try:
        return int(value) if not isinstance(value, bool) else None
    except (TypeError, ValueError, OverflowError):
        return None


def event_identity(item, start):
    league, serie, tournament = (_mapping(item.get(k)) for k in ("league", "serie", "tournament"))
    name = str(serie.get("full_name") or "").strip()
    league_name = str(league.get("name") or "").strip()
    if name and league_name and normalized_name(league_name) not in normalized_name(name):
        name = league_name + " " + name
    if not name:
        name = " ".join(str(value).strip() for value in (league.get("name"), serie.get("name")) if value)
        year = str(serie.get("year") or start.year)
        if name and year not in name:
            name += " " + year
    if not name:
        return None
    series_id = str(serie.get("id") or item.get("serie_id") or "").strip()
    identity = (
        "series:" + series_id if series_id.isdigit() else "event:" + normalized_name(name) + ":" + str(start.year)
    )
    branding = next((entity for entity in (tournament, serie, league) if safe_logo_url(entity.get("image_url"))), {})
    return {
        "event_id": identity,
        "event_name": name[:140],
        "event_start": utc_datetime(serie.get("begin_at")),
        "event_end": utc_datetime(serie.get("end_at")),
        "event_logo_url": safe_logo_url(branding.get("image_url")),
        "event_logo_url_dark": safe_logo_url(branding.get("dark_mode_image_url")),
        "league_name": str(league.get("name") or "")[:100],
        "tier": str(tournament.get("tier") or "").casefold(),
    }


def event_caption(name):
    caption = str(name)
    for pattern, replacement in (
        (r"\bEurope(?:an)?\b", "EU"),
        (r"\bNorth America(?:n)?\b", "NA"),
        (r"\bSouth America(?:n)?\b", "SA"),
        (r"\bQualifier\s*#?\s*(\d+)\b", r"Q\1"),
    ):
        caption = re.sub(pattern, replacement, caption, flags=re.IGNORECASE)
    return caption


def _event(item, tz, now, settings):
    if not isinstance(item, Mapping) or not has_followed_team(item, settings):
        return None
    match_id = str(item.get("id") or "").strip()
    start = utc_datetime(item.get("begin_at") or item.get("scheduled_at") or item.get("original_scheduled_at"))
    state = {"running": "inProgress", "not_started": "unstarted", "finished": "completed"}.get(str(item.get("status")))
    if not match_id or not start or not state:
        return None
    if state == "unstarted" and not now - timedelta(hours=12) <= start <= now + timedelta(days=14):
        return None
    if state == "inProgress" and not now - timedelta(hours=48) <= start <= now + timedelta(minutes=30):
        return None
    if state == "completed" and not now - timedelta(days=2) <= start <= now:
        return None
    end = utc_datetime(item.get("end_at"))
    if end and (end < start or (state == "completed" and end > now + timedelta(minutes=5))):
        return None
    identity = event_identity(item, start)
    if not identity:
        return None
    teams = match_teams(item)
    best_of = _integer(item.get("number_of_games"))
    best_of = best_of if best_of and 1 <= best_of <= 7 else None
    max_wins = best_of // 2 + 1 if best_of else 9
    scores = {}
    results = item.get("results")
    for result in results if isinstance(results, list) else []:
        if not isinstance(result, Mapping):
            continue
        score, team_id = _integer(result.get("score")), _integer(result.get("team_id"))
        if score is not None and team_id is not None and 0 <= score <= max_wins:
            scores[team_id] = score
    event = {
        **identity,
        "series": "CS",
        "match_id": match_id,
        "start": start.astimezone(tz),
        "state": state,
        "stage": str(_mapping(item.get("tournament")).get("name") or "Main Event")[:80],
        "tournament_id": str(_mapping(item.get("tournament")).get("id") or ""),
        "best_of": best_of,
        "maps": [],
        "source": "PandaScore",
        "score_kind": "MAPS",
        "feed": str(item.get("_cs2_feed") or ""),
        "feed_fresh": item.get("_cs2_feed_fresh", True),
        "feed_failure": str(item.get("_cs2_feed_failure") or ""),
    }
    for side, team in zip(("a", "b"), teams):
        team_id = _integer(team.get("id"))
        event.update(
            {
                f"team_{side}": str(team.get("name"))[:80],
                f"team_{side}_id": team_id,
                f"team_{side}_tag": str(team.get("acronym") or "")[:16],
                f"team_{side}_logo": safe_logo_url(team.get("image_url")),
                f"wins_{side}": scores.get(team_id) if state != "unstarted" else None,
            }
        )
    return event


def parse_cs2_card(payload, tz, now, settings=None, *, game_logo_path=""):
    """Select a focus event and retain followed teams' upcoming fixtures across events."""
    if not isinstance(payload, list):
        return None
    now = utc_datetime(now) or datetime.now(timezone.utc)
    events_by_id = {}
    for item in payload:
        event = _event(item, tz, now, settings or {})
        if event and event["match_id"] not in events_by_id:
            events_by_id[event["match_id"]] = event
    events = list(events_by_id.values())
    if not events:
        return None
    # A failed live feed must not hide a usable upcoming fixture from another feed.
    viable = [event for event in events if event["feed_fresh"]] or events
    phase = {"inProgress": 0, "unstarted": 1, "completed": 2}
    main = min(
        viable,
        key=lambda event: (
            phase[event["state"]],
            -event["start"].timestamp() if event["state"] == "completed" else event["start"].timestamp(),
            event["match_id"],
        ),
    )
    same_event = [
        event for event in events if event["event_id"] == main["event_id"] and event["feed_fresh"] == main["feed_fresh"]
    ]
    live = sorted([event for event in same_event if event["state"] == "inProgress"], key=lambda event: event["start"])
    upcoming = sorted(
        [event for event in events if event["state"] == "unstarted"],
        key=lambda event: (not event["feed_fresh"], event["start"], event["match_id"]),
    )
    recent = sorted(
        [event for event in same_event if event["state"] == "completed"], key=lambda event: event["start"], reverse=True
    )
    latest = max(event["start"] for event in same_event)
    end = main["event_end"] or latest + timedelta(hours=12)
    return {
        "series": "CS",
        "sport": "CS2",
        "event_name": main["event_name"],
        "event_id": main["event_id"],
        "status": {"inProgress": "LIVE", "unstarted": "NEXT", "completed": "RECENT"}[main["state"]],
        "window_active": main["state"] != "completed",
        "main": main,
        "live": live,
        "upcoming": upcoming,
        "recent": recent,
        "events": sorted(same_event, key=lambda event: event["start"]),
        "start": min(event["start"] for event in same_event),
        "end": end,
        "event_end": end,
        "latest": latest,
        "logo_path": game_logo_path,
        "event_logo_url": main["event_logo_url"],
        "event_logo_url_dark": main["event_logo_url_dark"],
        "event_logo_caption": event_caption(main["event_name"]),
        "source": "PandaScore",
        "source_state": "PANDASCORE DATA",
        "order": 0,
        "auto_follow": True,
    }
